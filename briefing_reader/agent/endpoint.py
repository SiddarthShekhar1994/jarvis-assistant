"""The local MCP endpoint a run's Claude Code reaches Jarvis's tools through (SPEC R11): hand-written over http.server
and JSON-RPC, no new dependency.

    Endpoint(listing, invoke, known, tool_timeout_s=45.0, on_refused=None, on_given_up=None)
        start()          listens on 127.0.0.1, on a port the system picks, with a token from ``secrets`` that this run
                         alone holds
        url, token       what the run's MCP config file names (agent.runner writes it; the token never goes into a
                         command line or a log)
        close()          stops listening, drops every connection and answers nothing more: it dies with the run

    POST /mcp            initialize, tools/list, tools/call, ping; a notification gets 202; anything else -32601
    GET                  405: no event stream here. DELETE: 200 (a client ending its session)

Refused, each with a fixed reason code and never read further: a Host header that is not this endpoint's own loopback
address (and an Origin that is not loopback: another site's page in a browser on this PC), a wrong or missing token, a
path other than /mcp, a body with no length or over MAX_BODY_BYTES, an unknown tool. A connection that resets - the
process was killed mid-call, as Stop does - is quiet.

A tool's handler runs on a thread of its own (``invoke(name, args, call)``), never on the HTTP thread: past
``tool_timeout_s`` the model gets an error result and the handler is given up on (``call.abandoned`` is set; whatever it
still comes back with is dropped). So no call hangs, whatever a tool does. Logs: reason codes and counts only, never a
token, an argument or a result.
"""

from __future__ import annotations

import json
import logging
import secrets
import socket
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .tools import ToolResult, error

logger = logging.getLogger(__name__)

SERVER_NAME = "jarvis"
SERVER_VERSION = "1"
MCP_PATH = "/mcp"
DEFAULT_PROTOCOL = "2025-03-26"
TOOL_TIMEOUT_S = 45.0
# The biggest call there is: a 5 MB file's text in JSON. Over this the body is not read at all.
MAX_BODY_BYTES = 12 * 1024 * 1024
MAX_SIDE_BY_SIDE = 16              # tool calls running at once in a run
IDLE_CONNECTION_S = 120.0          # a keep-alive connection nothing comes on, or a body that never arrives, ends
POLL_S = 0.05
TOO_LONG_CODE = "too_long"
TOO_LONG_TEXT = ("That step took too long and was stopped. Nothing more will come from it. Try another way, or say so "
                 "in your report.")
TOO_MANY_TEXT = "Too many steps are running at once: wait for one to come back, then call again."
# Why a request was refused (the log's and on_refused's: fixed words).
R_HOST, R_ORIGIN, R_TOKEN, R_PATH, R_LENGTH, R_TOO_BIG, R_JSON, R_UNKNOWN_TOOL = (
    "host", "origin", "token", "path", "length", "too_big", "json", "unknown_tool")
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})


class Call:
    """One tool call on its way: ``abandoned`` is set when the HTTP side stopped waiting for it. ``number`` and
    ``name``: whatever ``invoke`` wants to know it by when it is given up on."""

    def __init__(self) -> None:
        self.abandoned = threading.Event()
        self.number = 0
        self.name = ""


Invoke = Callable[[str, Any, Call], ToolResult]


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:          # Windows: no other program can bind this port over us
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass                               # a client gone mid-call (Stop kills it) is not an error of Jarvis's


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = IDLE_CONNECTION_S
    endpoint: Endpoint                     # set on the per-endpoint subclass

    def setup(self) -> None:
        super().setup()
        self.endpoint._track(self.connection, True)

    def finish(self) -> None:
        self.endpoint._track(self.connection, False)
        try:
            super().finish()
        except OSError:
            pass

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server's name
        pass                               # never a request line: quiet

    def version_string(self) -> str:
        return SERVER_NAME

    def _reply(self, code: int, body: bytes = b"", kind: str = "application/json",
               extra: Sequence[tuple[str, str]] = ()) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            for name, value in extra:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            self.close_connection = True   # the client is gone: quiet

    def _rpc(self, rid: Any, *, result: Any = None, error_code: int = 0, message: str = "", status: int = 200) -> None:
        answer: dict[str, Any] = {"jsonrpc": "2.0", "id": rid}
        if error_code:
            answer["error"] = {"code": error_code, "message": message}
        else:
            answer["result"] = result
        self._reply(status, json.dumps(answer, ensure_ascii=True).encode("ascii"))

    def _refuse(self, code: int, reason: str) -> None:
        self.close_connection = True       # the body, if any, is never read
        self.endpoint._refused(reason)
        self._reply(code, b"", extra=(("Connection", "close"),))

    def _allowed(self, *, token: bool = True) -> bool:
        """The checks of every request, before any of its body: this endpoint's own Host, no other site's Origin, the
        token."""
        endpoint = self.endpoint
        if endpoint.closed:
            self.close_connection = True
            self._reply(503, b"", extra=(("Connection", "close"),))
            return False
        if not endpoint._host_ok(self.headers.get("Host")):
            self._refuse(403, R_HOST)
            return False
        origin = self.headers.get("Origin")
        if origin is not None and not _loopback_origin(origin):
            self._refuse(403, R_ORIGIN)
            return False
        if token and not endpoint._token_ok(self.headers.get("Authorization")):
            self._refuse(401, R_TOKEN)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        if self._allowed(token=False):
            self.close_connection = True
            self._reply(405, b"", extra=(("Allow", "POST"), ("Connection", "close")))

    def do_DELETE(self) -> None:  # noqa: N802 - http.server's name
        if self._allowed():
            self.close_connection = True
            self._reply(200, b"", extra=(("Connection", "close"),))

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        if not self._allowed():
            return
        if urlsplit(self.path).path != MCP_PATH:
            self._refuse(404, R_PATH)
            return
        size = self._body_size()
        if size is None:
            return
        try:
            raw = self.rfile.read(size)
        except OSError:
            self.close_connection = True
            return
        if len(raw) != size:
            self.close_connection = True
            return
        try:
            message = json.loads(raw)
        except ValueError:
            self.endpoint._refused(R_JSON)
            self._rpc(None, error_code=PARSE_ERROR, message="Not JSON", status=400)
            return
        finally:
            del raw
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            self._rpc(message.get("id") if isinstance(message, dict) else None, error_code=INVALID_REQUEST,
                      message="Not a request", status=400)
            return
        self._request(message)

    def _body_size(self) -> int | None:
        """The body's length; None when it was refused."""
        if self.headers.get("Transfer-Encoding"):
            self._refuse(411, R_LENGTH)
            return None
        try:
            size = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self._refuse(411, R_LENGTH)
            return None
        if size < 0:
            self._refuse(411, R_LENGTH)
            return None
        if size > MAX_BODY_BYTES:
            self._refuse(413, R_TOO_BIG)
            return None
        return size

    def _request(self, message: Mapping[str, Any]) -> None:
        method, rid = message["method"], message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if rid is None:                    # a notification ("notifications/initialized" ...)
            self._reply(202)
            return
        if method == "initialize":
            wanted = params.get("protocolVersion")
            version = wanted if isinstance(wanted, str) and 0 < len(wanted) <= 20 else DEFAULT_PROTOCOL
            self._rpc(rid, result={"protocolVersion": version, "capabilities": {"tools": {}},
                                   "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}})
        elif method == "ping":
            self._rpc(rid, result={})
        elif method == "tools/list":
            self._rpc(rid, result={"tools": self.endpoint.listing()})
            self.endpoint.listed.set()
        elif method == "tools/call":
            self._tool_call(rid, params)
        else:
            self._rpc(rid, error_code=METHOD_NOT_FOUND, message="Unknown method")

    def _tool_call(self, rid: Any, params: Mapping[str, Any]) -> None:
        name = params.get("name")
        if not self.endpoint.known(name):
            self.endpoint._refused(R_UNKNOWN_TOOL)
            self._rpc(rid, error_code=INVALID_PARAMS, message="Unknown tool")
            return
        result = self.endpoint._run_tool(name, params.get("arguments"))
        if result is None:                 # the endpoint closed meanwhile: nothing goes back
            self.close_connection = True
            return
        answer: dict[str, Any] = {"content": result.content()}
        if result.is_error:
            answer["isError"] = True
        self._rpc(rid, result=answer)


def _loopback_origin(origin: str) -> bool:
    try:
        return urlsplit(origin).hostname in _LOOPBACK_HOSTS
    except ValueError:
        return False


class Endpoint:
    """See the module docs. Thread-safe."""

    def __init__(self, listing: Callable[[], list[dict[str, Any]]], invoke: Invoke, known: Callable[[Any], bool], *,
                 tool_timeout_s: float = TOOL_TIMEOUT_S, on_refused: Callable[[str], None] | None = None,
                 on_given_up: Callable[[Call], None] | None = None) -> None:
        self.listing = listing
        self.known = known
        self._invoke = invoke
        self._tool_timeout_s = float(tool_timeout_s)
        self._on_refused = on_refused
        self._on_given_up = on_given_up
        self.token = secrets.token_urlsafe(32)
        self._expected = f"Bearer {self.token}".encode("ascii")
        self.closed = False
        self.listed = threading.Event()    # a client has asked for tools/list: it is connected
        self.refusals: dict[str, int] = {}
        self._lock = threading.Lock()
        self._connections: set[Any] = set()
        self._running = 0
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def __repr__(self) -> str:             # never the token
        return f"Endpoint(port {self.port}, closed {self.closed})"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}{MCP_PATH}"

    def start(self) -> None:
        handler = type("Handler", (_Handler,), {"endpoint": self})
        server = _Server(("127.0.0.1", 0), handler)
        self._server = server
        self.port = int(server.server_address[1])
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": POLL_S},
                                        name="agent-endpoint", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """No more calls, no more answers: every connection goes. Safe to call more than once and from any thread but
        the HTTP ones."""
        with self._lock:
            if self.closed:
                return
            self.closed = True
            connections, self._connections = list(self._connections), set()
        server = self._server
        if server is not None:
            server.shutdown()
            server.server_close()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        if self.refusals:
            logger.info("Agent endpoint: refused %s",
                        ", ".join(f"{reason} {count}" for reason, count in sorted(self.refusals.items())))

    # ---- the handler's side ------------------------------------------------------------------------

    def _track(self, connection: Any, opened: bool) -> None:
        with self._lock:
            if opened and not self.closed:
                self._connections.add(connection)
            else:
                self._connections.discard(connection)

    def _refused(self, reason: str) -> None:
        with self._lock:
            self.refusals[reason] = self.refusals.get(reason, 0) + 1
        if self._on_refused is not None:
            try:
                self._on_refused(reason)
            except Exception:  # noqa: BLE001 - only a report
                pass

    def _host_ok(self, host: str | None) -> bool:
        """This endpoint's own address exactly: a loopback name and, when one is given, its own port."""
        if not host:
            return False
        name, colon, port = host.strip().rpartition(":")
        if not colon:
            name, port = host.strip(), ""
        return name.casefold() in _LOOPBACK_HOSTS and (not port or port == str(self.port))

    def _token_ok(self, given: str | None) -> bool:
        return secrets.compare_digest((given or "").encode("utf-8", "replace"), self._expected)

    def _run_tool(self, name: str, args: Any) -> ToolResult | None:
        """The tool on a thread of its own; the HTTP thread waits at most the timeout. None when the endpoint closed."""
        with self._lock:
            if self._running >= MAX_SIDE_BY_SIDE:
                return error(TOO_MANY_TEXT, "too_many")
            self._running += 1
        call = Call()
        done: Future[ToolResult] = Future()

        def work() -> None:
            try:
                done.set_result(self._invoke(name, args, call))
            except BaseException as exc:  # noqa: BLE001 - a broken tool is an error result, never a hung call
                done.set_result(error(f"That tool failed ({type(exc).__name__}).", "raised"))
            finally:
                with self._lock:
                    self._running -= 1

        threading.Thread(target=work, name="agent-tool", daemon=True).start()
        waited = 0.0
        while True:
            try:
                return done.result(timeout=min(POLL_S * 2, max(0.0, self._tool_timeout_s - waited)))
            except FutureTimeout:
                waited += POLL_S * 2
            if self.closed:
                call.abandoned.set()
                return None
            if waited >= self._tool_timeout_s:
                call.abandoned.set()
                logger.info("Agent endpoint: a tool passed %.0f s and was given up on", self._tool_timeout_s)
                if self._on_given_up is not None:
                    try:
                        self._on_given_up(call)
                    except Exception:  # noqa: BLE001 - only a report
                        pass
                return error(TOO_LONG_TEXT, TOO_LONG_CODE)
