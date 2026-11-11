"""Jarvis, a true agent, part A1: the local MCP endpoint (SPEC R11), over real HTTP on loopback. It refuses a wrong
token, a non-loopback Host, an unknown tool and an oversized body; handlers run off the HTTP thread and a tool that
passes the timeout returns an error result; connection resets are quiet; it dies with the run. Nothing leaves this
PC."""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import logging
import socket
import struct
import threading
import time
import unittest
from collections.abc import Mapping
from typing import Any

from briefing_reader.agent import endpoint as ep
from briefing_reader.agent.endpoint import Endpoint
from briefing_reader.agent.tools import ToolResult


class EndpointCase(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, Any, str]] = []
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.refused: list[str] = []
        self.given_up: list[Any] = []
        self.make()

    def make(self, tool_timeout_s: float = 5.0) -> None:
        self.ep = Endpoint(self.listing, self.invoke, lambda name: name in ("echo", "slow", "broken", "picture"),
                           tool_timeout_s=tool_timeout_s, on_refused=self.refused.append,
                           on_given_up=self.given_up.append)
        self.ep.start()
        self.addCleanup(self.ep.close)

    @staticmethod
    def listing() -> list[dict[str, Any]]:
        return [{"name": "echo", "description": "Echo.", "inputSchema": {"type": "object"}}]

    def invoke(self, name: str, args: Any, call: ep.Call) -> ToolResult:
        self.calls.append((name, args, threading.current_thread().name))
        self.last_call = call
        if name == "slow":
            self.release.wait(10)
            return ToolResult("late")
        if name == "broken":
            raise RuntimeError("private words")
        if name == "picture":
            return ToolResult("A picture.", pictures=(("QUJD", "image/png"),))
        if isinstance(args, Mapping) and args.get("fail"):
            return ToolResult("It failed.", is_error=True, code="refused")
        return ToolResult(f"got {args}")

    def raw(self, method: str, path: str = "/mcp", body: bytes | None = b"{}", headers: Mapping[str, str] | None = None,
            *, token: str | None = "") -> tuple[int, bytes, dict[str, str]]:
        sent = {"Content-Type": "application/json"}
        given = self.ep.token if token == "" else token
        if given is not None:
            sent["Authorization"] = f"Bearer {given}"
        sent.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.ep.port, timeout=10)
        try:
            connection.request(method, path, body, sent)
            answer = connection.getresponse()
            return answer.status, answer.read(), dict(answer.getheaders())
        finally:
            connection.close()

    def rpc(self, method: str, params: Mapping[str, Any] | None = None, rid: Any = 1, **kwargs: Any) -> dict[str, Any]:
        message = {"jsonrpc": "2.0", "method": method, "params": dict(params or {})}
        if rid is not None:
            message["id"] = rid
        status, body, _headers = self.raw("POST", body=json.dumps(message).encode(), **kwargs)
        self.assertEqual(status, 200)
        return json.loads(body)


class ProtocolTests(EndpointCase):
    def test_it_listens_on_loopback_on_a_port_the_system_picks(self) -> None:
        self.assertEqual(self.ep._server.server_address[0], "127.0.0.1")
        self.assertGreater(self.ep.port, 1024)
        self.assertEqual(self.ep.url, f"http://127.0.0.1:{self.ep.port}/mcp")

    def test_a_token_of_its_own_and_never_in_its_repr(self) -> None:
        other = Endpoint(self.listing, self.invoke, lambda _name: True)
        self.assertNotEqual(other.token, self.ep.token)
        self.assertGreaterEqual(len(self.ep.token), 40)
        self.assertNotIn(self.ep.token, repr(self.ep))

    def test_initialize(self) -> None:
        answer = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        self.assertEqual(answer["id"], 1)
        self.assertEqual(answer["result"], {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                            "serverInfo": {"name": "jarvis", "version": ep.SERVER_VERSION}})
        odd = self.rpc("initialize", {"protocolVersion": 3})
        self.assertEqual(odd["result"]["protocolVersion"], ep.DEFAULT_PROTOCOL)

    def test_tools_list_ping_and_an_unknown_method(self) -> None:
        self.assertEqual(self.rpc("tools/list")["result"], {"tools": self.listing()})
        self.assertEqual(self.rpc("ping")["result"], {})
        self.assertEqual(self.rpc("resources/list")["error"]["code"], ep.METHOD_NOT_FOUND)

    def test_a_notification_gets_202(self) -> None:
        status, body, _headers = self.raw("POST", body=b'{"jsonrpc":"2.0","method":"notifications/initialized"}')
        self.assertEqual((status, body), (202, b""))

    def test_a_tool_call(self) -> None:
        answer = self.rpc("tools/call", {"name": "echo", "arguments": {"a": 1}}, rid="abc")
        self.assertEqual(answer["id"], "abc")
        self.assertEqual(answer["result"], {"content": [{"type": "text", "text": "got {'a': 1}"}]})
        failed = self.rpc("tools/call", {"name": "echo", "arguments": {"fail": True}})
        self.assertEqual(failed["result"], {"content": [{"type": "text", "text": "It failed."}], "isError": True})
        picture = self.rpc("tools/call", {"name": "picture"})
        self.assertEqual(picture["result"]["content"][1], {"type": "image", "data": "QUJD", "mimeType": "image/png"})

    def test_a_handler_runs_off_the_http_thread(self) -> None:
        self.rpc("tools/call", {"name": "echo", "arguments": {}})
        self.assertEqual(self.calls[0][2], "agent-tool")

    def test_get_is_405_and_delete_ends_a_session(self) -> None:
        status, _body, headers = self.raw("GET", body=None)
        self.assertEqual((status, headers.get("Allow")), (405, "POST"))
        self.assertEqual(self.raw("DELETE", body=None)[0], 200)
        self.assertEqual(self.raw("DELETE", body=None, token="wrong")[0], 401)
        self.assertEqual(self.raw("PUT")[0], 501)

    def test_keep_alive_on_one_connection_as_a_real_client_does(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.ep.port, timeout=10)
        self.addCleanup(connection.close)
        headers = {"Authorization": f"Bearer {self.ep.token}"}
        for number in range(3):
            connection.request("POST", "/mcp", json.dumps({"jsonrpc": "2.0", "id": number, "method": "ping"}), headers)
            answer = connection.getresponse()
            self.assertEqual(json.loads(answer.read())["id"], number)

    def test_tools_side_by_side(self) -> None:
        answers: list[dict] = []
        slow = threading.Thread(target=lambda: answers.append(self.rpc("tools/call", {"name": "slow"})))
        slow.start()
        self.assertTrue(_until(lambda: any(call[0] == "slow" for call in self.calls)))
        self.assertIn("got", self.rpc("tools/call", {"name": "echo"})["result"]["content"][0]["text"])
        self.release.set()
        slow.join(5)
        self.assertEqual(answers[0]["result"]["content"][0]["text"], "late")


class RefusalTests(EndpointCase):
    def assert_refused(self, status: int, reason: str, method: str = "POST", **kwargs: Any) -> None:
        got, _body, headers = self.raw(method, **kwargs)
        self.assertEqual(got, status)
        self.assertIn(reason, self.refused)
        self.assertEqual(headers.get("Connection"), "close")
        self.assertEqual(self.calls, [])

    def tool_body(self) -> bytes:
        return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "echo"}}).encode()

    def test_a_wrong_token(self) -> None:
        self.assert_refused(401, ep.R_TOKEN, body=self.tool_body(), token="wrong")

    def test_no_token(self) -> None:
        self.assert_refused(401, ep.R_TOKEN, body=self.tool_body(), token=None)

    def test_a_token_of_another_kind(self) -> None:
        self.assert_refused(401, ep.R_TOKEN, body=self.tool_body(), token=None,
                            headers={"Authorization": f"Basic {self.ep.token}"})

    def test_a_non_loopback_host(self) -> None:
        for host in ("evil.example", "127.0.0.1.evil.example", "evil.example:80", f"example.com:{self.ep.port}",
                     f"127.0.0.1:{self.ep.port + 1}", "192.168.1.5", "[::1]", "0.0.0.0"):
            with self.subTest(host=host):
                self.refused.clear()
                self.assert_refused(403, ep.R_HOST, body=self.tool_body(), headers={"Host": host})

    def test_the_host_s_own_names(self) -> None:
        for host in (f"127.0.0.1:{self.ep.port}", f"LOCALHOST:{self.ep.port}", "127.0.0.1", "localhost"):
            with self.subTest(host=host):
                self.assertEqual(self.raw("POST", body=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                                          headers={"Host": host})[0], 200)

    def test_another_site_s_page(self) -> None:
        self.assert_refused(403, ep.R_ORIGIN, body=self.tool_body(), headers={"Origin": "https://evil.example"})
        self.assert_refused(403, ep.R_ORIGIN, body=self.tool_body(), headers={"Origin": "null"})
        self.assertEqual(self.raw("POST", body=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                                  headers={"Origin": "http://localhost:1234"})[0], 200)

    def test_another_path(self) -> None:
        self.assert_refused(404, ep.R_PATH, path="/", body=self.tool_body())
        self.assert_refused(404, ep.R_PATH, path="/mcp/x", body=self.tool_body())

    def test_an_oversized_body_is_never_read(self) -> None:
        connection = socket.create_connection(("127.0.0.1", self.ep.port), timeout=10)
        self.addCleanup(connection.close)
        connection.sendall(f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{self.ep.port}\r\nAuthorization: Bearer {self.ep.token}"
                           f"\r\nContent-Length: {ep.MAX_BODY_BYTES + 1}\r\n\r\n".encode())
        answer = connection.recv(4096)                   # no body was sent: it answers at once
        self.assertTrue(answer.startswith(b"HTTP/1.1 413"), answer[:40])
        self.assertIn(ep.R_TOO_BIG, self.refused)

    def test_the_biggest_allowed_body_is_read(self) -> None:
        text = "x" * (5 * 1024 * 1024)
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "echo", "arguments": {"text": text}}}).encode()
        self.assertLess(len(body), ep.MAX_BODY_BYTES)
        self.assertEqual(self.raw("POST", body=body)[0], 200)

    def test_a_body_with_no_length(self) -> None:
        connection = socket.create_connection(("127.0.0.1", self.ep.port), timeout=10)
        self.addCleanup(connection.close)
        connection.sendall(f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{self.ep.port}\r\nAuthorization: Bearer {self.ep.token}"
                           "\r\nTransfer-Encoding: chunked\r\n\r\n".encode())
        self.assertTrue(connection.recv(4096).startswith(b"HTTP/1.1 411"))
        bare = socket.create_connection(("127.0.0.1", self.ep.port), timeout=10)
        self.addCleanup(bare.close)
        bare.sendall(f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{self.ep.port}\r\nAuthorization: Bearer {self.ep.token}"
                     "\r\n\r\n".encode())
        self.assertTrue(bare.recv(4096).startswith(b"HTTP/1.1 411"))
        self.assert_refused(411, ep.R_LENGTH, body=None, headers={"Content-Length": "-1"})
        self.assert_refused(411, ep.R_LENGTH, body=None, headers={"Content-Length": "12x"})

    def test_an_unknown_tool(self) -> None:
        answer = self.rpc("tools/call", {"name": "Bash", "arguments": {"command": "dir"}})
        self.assertEqual(answer["error"]["code"], ep.INVALID_PARAMS)
        self.assertIn(ep.R_UNKNOWN_TOOL, self.refused)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc("tools/call", {"name": 3})["error"]["code"], ep.INVALID_PARAMS)
        self.assertEqual(self.rpc("tools/call")["error"]["code"], ep.INVALID_PARAMS)

    def test_what_is_not_a_request(self) -> None:
        status, body, _headers = self.raw("POST", body=b"not json")
        self.assertEqual((status, json.loads(body)["error"]["code"]), (400, ep.PARSE_ERROR))
        status, body, _headers = self.raw("POST", body=b"[1, 2]")
        self.assertEqual((status, json.loads(body)["error"]["code"]), (400, ep.INVALID_REQUEST))
        status, body, _headers = self.raw("POST", body=b'{"id": 1}')
        self.assertEqual((status, json.loads(body)["error"]["code"]), (400, ep.INVALID_REQUEST))

    def test_the_log_counts_reasons_and_never_a_token(self) -> None:
        self.raw("POST", token="wrong-token-value")
        with self.assertLogs("briefing_reader.agent.endpoint", logging.INFO) as logs:
            self.ep.close()
        text = "\n".join(logs.output)
        self.assertIn("token 1", text)
        self.assertNotIn(self.ep.token, text)
        self.assertNotIn("wrong-token-value", text)


class TimeoutTests(EndpointCase):
    def setUp(self) -> None:
        super().setUp()
        self.ep.close()
        self.make(tool_timeout_s=0.3)

    def test_a_tool_past_the_timeout_returns_an_error_and_its_late_result_is_dropped(self) -> None:
        began = time.monotonic()
        answer = self.rpc("tools/call", {"name": "slow"})
        took = time.monotonic() - began
        self.assertLess(took, 3.0)                       # never a hung call
        self.assertEqual(answer["result"], {"content": [{"type": "text", "text": ep.TOO_LONG_TEXT}], "isError": True})
        self.assertTrue(self.last_call.abandoned.is_set())
        self.assertEqual(self.given_up, [self.last_call])
        self.release.set()                               # it comes back now: nobody hears it
        self.assertEqual(self.rpc("ping")["result"], {})

    def test_the_spec_s_timeout(self) -> None:
        self.assertEqual(ep.TOOL_TIMEOUT_S, 45.0)

    def test_a_tool_that_raises_is_an_error_result(self) -> None:
        answer = self.rpc("tools/call", {"name": "broken"})
        self.assertTrue(answer["result"]["isError"])
        self.assertIn("RuntimeError", answer["result"]["content"][0]["text"])
        self.assertNotIn("private words", json.dumps(answer))

    def test_too_many_side_by_side(self) -> None:
        self.ep._running = ep.MAX_SIDE_BY_SIDE
        answer = self.rpc("tools/call", {"name": "echo"})
        self.assertTrue(answer["result"]["isError"])
        self.ep._running = 0


class ClosingTests(EndpointCase):
    def test_it_dies_with_the_run(self) -> None:
        port = self.ep.port
        self.assertEqual(self.rpc("ping")["result"], {})
        self.ep.close()
        self.assertTrue(self.ep.closed)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1)
        self.ep.close()                                  # safe twice

    def test_a_kept_connection_gets_nothing_more(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.ep.port, timeout=5)
        self.addCleanup(connection.close)
        headers = {"Authorization": f"Bearer {self.ep.token}"}
        connection.request("POST", "/mcp", b'{"jsonrpc":"2.0","id":1,"method":"ping"}', headers)
        connection.getresponse().read()
        self.ep.close()
        with self.assertRaises((OSError, http.client.HTTPException)):
            connection.request("POST", "/mcp", b'{"jsonrpc":"2.0","id":2,"method":"ping"}', headers)
            connection.getresponse().read()

    def test_a_call_on_its_way_gets_no_answer_and_ends(self) -> None:
        outcome: list[Any] = []

        def go() -> None:
            try:
                outcome.append(self.rpc("tools/call", {"name": "slow"}))
            except (OSError, http.client.HTTPException) as exc:
                outcome.append(exc)

        thread = threading.Thread(target=go)
        thread.start()
        self.assertTrue(_until(lambda: self.calls))
        began = time.monotonic()
        self.ep.close()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - began, 3.0)
        self.assertIsInstance(outcome[0], (OSError, http.client.HTTPException))   # no answer: the client just lost it
        self.assertTrue(_until(self.last_call.abandoned.is_set))

    def test_connection_resets_are_quiet(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            for body in (b"POST /mcp HTTP/1.1\r\nContent-Le", b""):
                connection = socket.create_connection(("127.0.0.1", self.ep.port), timeout=5)
                connection.sendall(body)
                # A reset, as when the process is killed: SO_LINGER 0 sends RST instead of FIN.
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                connection.close()
            self.assertEqual(self.rpc("ping")["result"], {})
            time.sleep(0.1)
        self.assertEqual(stderr.getvalue(), "")


def _until(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


if __name__ == "__main__":
    unittest.main()
