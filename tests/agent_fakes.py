"""Fakes for the agent tests: a scripted Claude Code that really speaks to the real endpoint over HTTP, on loopback.

FakeEngine is a process (cli.Process) that reads the --mcp-config file it was given as the real CLI does, connects
(initialize, tools/list), and then does what its script says: wait for a stdin message, call a tool - which POSTs
tools/call to the endpoint with the token from that file - and end its turn. The events it writes have the shapes of a
real stream (tests/fixtures/agent/stream_sample.jsonl, recorded from Claude Code 2.1.294 with made-up data and scrubbed:
no path, no id). Nothing here starts claude.exe. Every value is invented.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import queue
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from briefing_reader import snapshots
from briefing_reader.agent import web
from briefing_reader.agent.runner import AgentEngine, Timing
from briefing_reader.ask import cli
from briefing_reader.ask.planner import ClaudeEngine
from briefing_reader.config import AgentConfig
from tests.ask_fakes import AUTH_OK, FakeProcess, FakeRunner, fake_exe

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agent"
SESSION = "00000000-0000-4000-8000-00000000a5c2"
UUID = "66666666-6666-4666-8666-666666666666"
PREFIX = cli.AGENT_TOOL_PREFIX
FAST = Timing(poll_s=0.01, connect_s=2.0, init_s=10.0, question_s=30.0, keep_going_s=30.0, wrap_up_s=5.0, after_finish_s=2.0,
              replay_s=0.5, tool_s=5.0, resume_margin_s=0.0)


class Killed(Exception):
    """The process was killed: the script's thread ends here."""


def init_event(tools: Sequence[str], **changes: Any) -> dict[str, Any]:
    """An init event as Claude Code 2.1.294 writes one in a task's run (the agents and the built-in plugins it lists are
    its own; ``tools`` is what counts)."""
    event = {"type": "system", "subtype": "init", "cwd": "C:/Users/example/AppData/Local/briefing-reader/agent",
             "session_id": SESSION, "tools": [PREFIX + tool for tool in tools],
             "mcp_servers": [{"name": "jarvis", "status": "connected", "source": "dynamic"}],
             "model": "claude-opus-fake", "permissionMode": "dontAsk", "slash_commands": [], "apiKeySource": "none",
             "claude_code_version": "2.1.294", "output_style": "default",
             "agents": ["claude", "Explore", "general-purpose", "Plan", "statusline-setup", "web-fetch"], "skills": [],
             "plugins": [{"name": "cc-plugin-agents-md", "path": "builtin", "source": "cc-plugin-agents-md@builtin"}],
             "uuid": UUID}
    event.update(changes)
    return event


def result_event(text: str = "", **changes: Any) -> dict[str, Any]:
    event = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 900, "duration_api_ms": 800,
             "num_turns": 2, "result": text, "stop_reason": "end_turn", "session_id": SESSION, "total_cost_usd": 0.01,
             "usage": {"input_tokens": 120, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 300,
                       "output_tokens": 40, "service_tier": "standard"}, "modelUsage": {}, "permission_denials": [],
             "terminal_reason": "completed", "api_error_status": None, "queued_turn_count": 0, "uuid": UUID}
    event.update(changes)
    return event


def rate_limit_event(status: str = "rejected", resets_at: float | None = None, **info: Any) -> dict[str, Any]:
    details: dict[str, Any] = {"status": status, "rateLimitType": "five_hour", "overageStatus": "rejected",
                               "isUsingOverage": False}
    if resets_at is not None:
        details["resetsAt"] = int(resets_at)
    details.update(info)
    return {"type": "rate_limit_event", "rate_limit_info": details, "session_id": SESSION, "uuid": UUID}


class FakeEngine(FakeProcess):
    """A scripted Claude Code; ``script(engine)`` runs on its own thread once the process has "started". See the module
    docs. ``tools``: the names its init events show (None: what tools/list said). ``init_changes``: what each init event
    changes, by number from 0 (a list of dicts). ``failure``: what went wrong in the script, for the test to see."""

    def __init__(self, script: Callable[[FakeEngine], Any], *, tools: Sequence[str] | None = None,
                 init_changes: Sequence[Mapping[str, Any]] = (), stderr: str = "") -> None:
        super().__init__([], stderr=stderr)
        self.script = script
        self.tools = list(tools) if tools is not None else None
        self.init_changes = list(init_changes)
        self.argv: list[str] = []
        self.env: dict[str, str] = {}
        self.cwd: Path | None = None
        self.url = ""
        self.token = ""
        self.messages: list[str] = []         # every message as it was delivered to the "model"
        self.results: list[dict[str, Any]] = []   # every tools/call answer
        self.failure: BaseException | None = None
        self.turn_number = 0
        self.tool_number = 0
        self.inits = 0
        self.finished = threading.Event()     # the script's thread ended
        self._out: queue.Queue[str | None] = queue.Queue()
        self._in: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self.listed: list[dict[str, Any]] = []
        self.exit_code = 0

    # ---- the process ------------------------------------------------------------------------------

    def attach(self, argv: Sequence[str], stdin_text: str, env: Mapping[str, str], cwd: Path) -> None:
        """What the runner's start() hands over."""
        self.argv, self.env, self.cwd = list(argv), dict(env), Path(cwd)
        config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="ascii"))
        server = config["mcpServers"]["jarvis"]
        self.url = server["url"]
        self.token = server["headers"]["Authorization"].removeprefix("Bearer ")
        if stdin_text:
            self._in.put(stdin_text)
        self._thread = threading.Thread(target=self._main, name="fake-engine", daemon=True)
        self._thread.start()

    def _main(self) -> None:
        try:
            self.connect()
            self.script(self)
            while not self.killed and not self.stdin_closed:   # idle, as a real one is: it ends with its stdin
                time.sleep(0.005)
        except Killed:
            pass
        except BaseException as exc:  # noqa: BLE001 - the test looks at it
            self.failure = exc
        finally:
            self.finished.set()
            self._out.put(None)

    def send(self, text: str) -> bool:
        if not self.alive():
            return False
        self.sent.append(text)
        if self.on_send is not None:
            self.on_send(text)
        self._in.put(text)
        return True

    def close_stdin(self) -> None:
        self.stdin_closed = True
        self._in.put(None)

    def read_line(self, timeout: float) -> str | None:
        try:
            item = self._out.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return ""
        if item is None:
            self._out.put(None)
        return item

    def kill(self) -> None:
        self.killed = True
        self._in.put(None)

    def wait(self, timeout: float) -> int | None:
        return -9 if self.killed else self.exit_code

    # ---- what a script does -------------------------------------------------------------------------

    def _check(self) -> None:
        if self.killed:
            raise Killed()

    def emit(self, event: Mapping[str, Any] | str) -> None:
        self._check()
        self._out.put(event if isinstance(event, str) else json.dumps(event))

    def post(self, method: str, params: Mapping[str, Any] | None = None, *, rid: int | None = 1,
             headers: Mapping[str, str] | None = None) -> Any:
        """One JSON-RPC request over HTTP with this run's token: the answer's JSON (None for an empty body)."""
        parts = urlsplit(self.url)
        body = {"jsonrpc": "2.0", "method": method, "params": dict(params or {})}
        if rid is not None:
            body["id"] = rid
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
        try:
            connection.request("POST", parts.path, json.dumps(body),
                               {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                                "Accept": "application/json, text/event-stream", **(headers or {})})
            answer = connection.getresponse()
            raw = answer.read()
        except OSError as exc:
            if self.killed:
                raise Killed() from exc
            raise
        except http.client.HTTPException as exc:
            if self.killed:
                raise Killed() from exc
            raise
        finally:
            connection.close()
        self._check()
        return json.loads(raw) if raw else None

    def connect(self) -> None:
        self.post("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "fake-engine", "version": "0"}})
        self.post("notifications/initialized", rid=None)
        self.listed = self.post("tools/list")["result"]["tools"]

    def _init(self) -> None:
        names = self.tools if self.tools is not None else [tool["name"] for tool in self.listed]
        changes = self.init_changes[self.inits] if self.inits < len(self.init_changes) else {}
        self.inits += 1
        self.emit({**init_event(names), **changes})

    def wait_message(self, timeout: float = 15.0) -> str:
        """The next stdin message: this session's init, the replay, and its text - what the real CLI does per
        message."""
        deadline = time.monotonic() + timeout
        while True:
            self._check()
            try:
                raw = self._in.get(timeout=0.02)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise TimeoutError("no message came") from None
                continue
            if raw is None:
                self._check()
                if self.stdin_closed:
                    raise Killed()
                continue
            text = json.loads(raw)["message"]["content"]
            self._init()
            self.emit({"type": "system", "subtype": "status", "status": "requesting", "session_id": SESSION, "uuid": UUID})
            self.emit({"type": "user", "message": {"role": "user", "content": text}, "session_id": SESSION,
                       "parent_tool_use_id": None, "uuid": UUID, "isReplay": True})
            self.messages.append(text)
            return text

    def settle(self) -> None:
        """What was written has been read: as with the real CLI, a tool call reaches Jarvis after the event that names
        it."""
        while not self._out.empty():
            self._check()
            time.sleep(0.002)
        time.sleep(0.01)

    def has_message(self) -> bool:
        return not self._in.empty()

    def new_turn(self) -> str:
        self.turn_number += 1
        return f"msg_fake{self.turn_number:04d}"

    def assistant(self, blocks: Sequence[Mapping[str, Any]], message_id: str = "", parent: str | None = None) -> None:
        self.emit({"type": "assistant", "message": {"model": "claude-opus-fake", "id": message_id or self.new_turn(),
                                                    "type": "message", "role": "assistant", "content": list(blocks),
                                                    "stop_reason": None, "usage": {"input_tokens": 2, "output_tokens": 8}},
                   "parent_tool_use_id": parent, "session_id": SESSION, "uuid": UUID})

    def chunk_start(self, block: Mapping[str, Any], *, parent: str | None = None) -> None:
        self.emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                                     "content_block": dict(block)},
                   "session_id": SESSION, "parent_tool_use_id": parent, "uuid": UUID})

    def call(self, name: str, /, *, message_id: str = "", **arguments: Any) -> dict[str, Any]:
        """A tool call as the model makes it: the chunks, the assistant event, the real POST, the tool result event. The
        answer's result ({"content": [...], "isError": true when it failed}), or {"error": ...} from the endpoint."""
        self.tool_number += 1
        tool_id = f"toolu_fake{self.tool_number:04d}"
        block = {"type": "tool_use", "id": tool_id, "name": PREFIX + name, "input": arguments,
                 "caller": {"type": "direct"}}
        self.chunk_start({**block, "input": {}})
        self.assistant([block], message_id)
        self.settle()
        answer = self.post("tools/call", {"name": name, "arguments": arguments}, rid=self.tool_number + 100)
        self.results.append(answer)
        result = answer.get("result", {"error": answer.get("error")}) if isinstance(answer, dict) else {}
        self.emit({"type": "user", "message": {"role": "user", "content": [
            {"tool_use_id": tool_id, "type": "tool_result", "content": result.get("content", ""),
             "is_error": bool(result.get("isError"))}]}, "parent_tool_use_id": None, "session_id": SESSION, "uuid": UUID,
            "tool_use_result": result.get("content", "")})
        return result

    def end_turn(self, text: str = "All right.", **changes: Any) -> None:
        self.assistant([{"type": "text", "text": text}])
        self.emit(result_event(text, **changes))

    @staticmethod
    def text_of(result: Mapping[str, Any]) -> str:
        return "\n".join(block.get("text", "") for block in result.get("content", ()) if isinstance(block, Mapping))


class FakeControl:
    """A control of a FakePage, as agent.drive's controls script would list it. ``hid``: the owner can't see it (it is
    counted, never listed). ``field``: its name in its form (what a press would send). ``goes_to``: the address a click
    on it (a free button) or an approved press of it loads. A submit's ``formaction`` / ``formtarget`` win over its
    form's, as in a real press. ``press``: what an approved press of it meets ("invalid": the form's checks stop it,
    no submit event; "prevent": a script of the page takes the submit over; both stay on the page)."""

    def __init__(self, role: str, name: str = "", *, kind: str = "", value: str = "", options: Sequence[str] = (),
                 checked: bool = False, form: int = 0, href: str = "", blank: bool = False, hints: str = "",
                 auto: str = "", required: bool = False, sr: bool = False, hid: bool = False, field: str = "",
                 goes_to: str = "", formaction: str = "", formtarget: str = "", press: str = "") -> None:
        self.role, self.name, self.kind, self.value, self.options = role, name, kind, value, list(options)
        self.checked, self.form, self.href, self.blank, self.hints = checked, form, href, blank, hints
        self.auto, self.required, self.sr, self.hid, self.field, self.goes_to = auto, required, sr, hid, field, goes_to
        self.formaction, self.formtarget, self.press = formaction, formtarget, press

    def how(self) -> str:
        """What a press of it does (agent.drive's howOf)."""
        if self.role == "submit":
            return "submit" if self.form else "click"
        if self.role in ("textbox", "textarea"):
            return "enter" if self.form else "key"
        return "click"

    def sent_value(self) -> str:
        if self.role in ("checkbox", "radio"):
            return (self.value or "on") if self.checked else ""
        return self.value


class FakePage:
    """A page FakeBrowser "opens": what its scripts would read. ``fails``: the load's reason (snapshots.R_*). For the
    drive: ``controls`` (FakeControl), ``forms`` ((method, action) or (method, action, target) by number from 1),
    ``extra`` (form, name, value, hidden): fields the page set itself, and ``on_type(page, control)``: a page script
    that runs after each typing. ``mine``: the elements Jarvis set, by their keys (agent.drive's MINE store: a load
    empties it, a new list never does)."""

    def __init__(self, title: str = "A page", text: str = "Its words.", links: Sequence[tuple[str, str]] = (),
                 results: Sequence[tuple[str, str, str]] = (), final: str = "", fails: str = "",
                 still_loading: bool = False, reads: Sequence[str] = (), controls: Sequence[FakeControl] = (),
                 forms: Sequence[tuple[str, str]] = (), extra: Sequence[list[Any]] = (),
                 on_type: Callable[[FakePage, FakeControl], None] | None = None) -> None:
        self.title, self.text, self.links, self.results = title, text, list(links), list(results)
        self.final, self.fails, self.still_loading = final, fails, still_loading
        self.reads = list(reads)              # later reads of it see these texts in turn (a page that grows)
        self.controls, self.forms, self.extra = list(controls), list(forms), [list(item) for item in extra]
        self.on_type = on_type
        self.gen = ""                         # the controls script's last gen: its refs (a load empties it)
        self.mine: dict[int, FakeControl] = {}


class FakeBrowser:
    """agent.web's Browser seam with no web engine: pages by address, scripts answered as the real ones would be.
    Records every call. ``before(name)``: called first in each call (a test holds it there). Nothing here reaches
    anything."""

    PICTURE = b"\xff\xd8fake-picture"

    def __init__(self, pages: Mapping[str, FakePage] | None = None, *, ready: bool = True,
                 before: Callable[[str], None] | None = None) -> None:
        self.pages = dict(pages or {})
        self.ready = ready
        self.before = before
        self.loads: list[str] = []
        self.files: list[str] = []
        self.scrolls: list[float] = []
        self.pictures: list[tuple[int, int]] = []
        self.current: FakePage | None = None
        self.gone = False                     # script() and picture() see no page from now on
        self.stopped = self.ended = self.closed = self.shut = self.kept_clear = 0
        self.lingers: list[bool] = []
        self.holds: list[bool] = []
        self.owner_hidden = False
        self.away = False
        self.rest = False                     # a file card waits: the window is off the screen
        self.rect: Any = None                 # window_rect(): where the window is (None: not showing)
        self.other_ways: list[tuple[str, bool]] = []
        self.shown: list[str] = []            # what the owner's Show asked for
        self.bigs: list[bool] = []            # end_run's "opens big" (a task that filled in pages, done)

    # ---- the Browser seam -----------------------------------------------------------------------------

    def scheme_ready(self) -> bool:
        return self.ready

    def load(self, url: str, *, timeout_s: float, cancelled: Callable[[], bool]) -> snapshots.ShotResult:
        self.loads.append(url)
        if self.before is not None:
            self.before("load")
        page = self.pages.get(url)
        if page is None:
            self.current = None
            return snapshots.ShotResult(False, snapshots.R_LOAD_FAILED)
        if page.fails:
            self.current = None
            return snapshots.ShotResult(False, page.fails)
        self.current = page
        return snapshots.ShotResult(True, final_url=page.final or url, still_loading=page.still_loading, took_ms=5)

    def load_file(self, name: str, folder: Any, *, timeout_s: float, cancelled: Callable[[], bool]) -> snapshots.ShotResult:
        self.files.append(name)
        if self.before is not None:
            self.before("load_file")
        body = folder.body(name).decode("utf-8", "replace")
        self.current = FakePage(title=name, text=body)
        return snapshots.ShotResult(True, final_url=name, took_ms=5)

    def script(self, source: str, *, timeout_s: float, cancelled: Callable[[], bool]) -> Any:
        if self.before is not None:
            self.before("script")
        page = self.current
        if page is None or self.gone:
            return web.NO_PAGE
        if "scrollTo" in source:
            self.scrolls.append(float(source.split("* ")[1].split(")")[0]))
            return json.dumps({"top": 100, "height": 1000})
        address = page.final or (self.loads[-1] if self.loads else "")
        if "marks" in source:
            return json.dumps({"title": page.title, "url": address, "text": page.text,
                               "results": [list(row) for row in page.results]})
        if page.reads:
            page.text = page.reads.pop(0)
        return json.dumps({"title": page.title, "url": address, "length": len(page.text),
                           "text": page.text[:web.TEXT_KEEP], "links": [list(row) for row in page.links]})

    def picture(self, max_width: int, max_bytes: int, *, timeout_s: float,
                cancelled: Callable[[], bool]) -> snapshots.ShotResult:
        self.pictures.append((max_width, max_bytes))
        if self.current is None or self.gone:
            return snapshots.ShotResult(False, snapshots.R_LOAD_FAILED)
        return snapshots.ShotResult(True, image=self.PICTURE, width=min(max_width, 960), height=600,
                                    final_url=self.current.final or (self.loads[-1] if self.loads else ""))

    # ---- what the controller calls ----------------------------------------------------------------------

    def stop(self) -> None:
        self.stopped += 1

    def end_run(self, *, linger: bool = True, hold: bool = False, big: bool = False) -> None:
        self.ended += 1
        self.lingers.append(linger)
        self.holds.append(hold)
        self.bigs.append(big)

    def set_hidden(self, *, owner: bool | None = None, away: bool | None = None) -> None:
        if owner is not None:
            self.owner_hidden = bool(owner)
        if away is not None:
            self.away = bool(away)

    def set_resting(self, resting: bool) -> None:
        self.rest = bool(resting)

    def resting(self) -> bool:
        return self.rest and self.rect is not None

    def owner_hid(self) -> bool:
        return self.owner_hidden

    def window_rect(self) -> Any:
        return None if self.rest else self.rect

    def keep_clear(self) -> None:
        self.kept_clear += 1

    def other_way(self, host: str, reading: bool) -> None:
        self.other_ways.append((host, reading))

    def show_for_owner(self, name: str, folder: Any, done: Any = None) -> None:
        self.shown.append(name)

    def close_now(self) -> None:
        self.closed += 1

    def shutdown(self) -> None:
        self.shut += 1

    def deleteLater(self) -> None:  # noqa: N802 - Qt's name
        pass


class FakeDriveBrowser(FakeBrowser):
    """FakeBrowser with agent.drive's seam: tabs, the seal, the drive scripts answered as the real ones would be (each
    read by its op mark and its ARGS line), and an approved press. Records every call: ``seals``, ``typed_sealed`` (was
    the window sealed when each value went in), ``presses`` (tab, ref, the values its form held then), ``unapproved``
    (a press run any other way: must stay empty), ``closed_tabs`` (tab, sealed while it went, unseal)."""

    _DRIVE_RE = re.compile(r"/\*drive:(\w+)\*/\nvar ARGS = (.*);\n")

    def __init__(self, pages: Mapping[str, FakePage] | None = None, *, seal_ok: bool = True, **options: Any) -> None:
        super().__init__(pages, **options)
        self.tabs: dict[int, FakePage | None] = {1: None}
        self.active = 1
        self.sealed = False
        self.seal_ok = seal_ok
        self.seals: list[bool] = []
        self.typed_sealed: list[bool] = []
        self.acts: list[tuple[int, str]] = []
        self.clicks: list[tuple[int, int]] = []
        self.presses: list[tuple[int, int, dict[str, str]]] = []
        self.unapproved: list[tuple[int, str]] = []
        self.closed_tabs: list[tuple[int, bool, bool]] = []
        self.new_tabs: list[int] = []
        self.press_hosts: list[tuple[str, ...]] = []   # the hosts each approved press could reach
        self.sealed_after: list[bool] = []             # sealed again after each approved press (no new page came)
        self.close_ok = True                           # close_tab's answer (False: the page did not go)
        self.before_press: Callable[[FakePage], None] | None = None   # a page script that runs just before a press

    # ---- tabs -------------------------------------------------------------------------------------------

    def load(self, url: str, *, timeout_s: float, cancelled: Callable[[], bool]) -> snapshots.ShotResult:
        result = super().load(url, timeout_s=timeout_s, cancelled=cancelled)
        if self.current is not None:
            self.current.gen, self.current.mine = "", {}
        self.tabs[self.active] = self.current
        return result

    def new_tab(self, tab: int, *, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        self.new_tabs.append(tab)
        self.tabs[tab] = None
        self.active = tab
        self.current = None
        return True

    def switch_tab(self, tab: int, *, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        if tab not in self.tabs:
            return False
        self.active = tab
        self.current = self.tabs[tab]
        return True

    def close_tab(self, tab: int, *, unseal: bool, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        self.closed_tabs.append((tab, self.sealed, unseal))
        if not self.close_ok:
            return False
        self.tabs.pop(tab, None)
        if self.active == tab:
            self.current = None
        if unseal:
            self.sealed = False
        return True

    def seal(self, cut: bool, *, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        self.seals.append(cut)
        if self.seal_ok:
            self.sealed = True
        return self.seal_ok

    # ---- the drive scripts ----------------------------------------------------------------------------------

    @staticmethod
    def seen(page: FakePage) -> list[FakeControl]:
        return [control for control in page.controls if not control.hid]

    def _address(self, page: FakePage) -> str:
        return page.final or next((url for url, known in self.pages.items() if known is page), "")

    def _page_json(self, page: FakePage) -> str:
        return json.dumps({"title": page.title, "url": self._address(page), "length": len(page.text), "text": page.text,
                           "links": [list(row) for row in page.links]})

    def act(self, tab: int, source: str, *, settle_s: float, timeout_s: float, cancelled: Callable[[], bool]) -> Any:
        from briefing_reader.agent import drive

        if tab not in self.tabs:
            return drive.ActResult(ok=False)
        page = self.tabs[tab]
        found = self._DRIVE_RE.search(source)
        op = found.group(1) if found else "read"
        self.acts.append((tab, op))
        if page is None:
            return drive.ActResult(web.NO_PAGE)
        if found is None:
            return drive.ActResult(self._page_json(page))
        args = json.loads(found.group(2))
        if op == "controls":
            page.gen = args["gen"]
            seen = self.seen(page)
            rows = [{"role": c.role, "kind": c.kind, "name": c.name, "sr": int(c.sr), "hints": c.hints, "auto": c.auto,
                     "required": c.required, "form": c.form, "value": c.value, "options": c.options,
                     "checked": c.checked, "href": c.href, "blank": c.blank} for c in seen[:args["max"]]]
            return drive.ActResult(json.dumps({"gen": args["gen"], "url": self._address(page), "title": page.title,
                                               "controls": rows, "forms": [list(form[:2]) for form in page.forms],
                                               "left": len(page.controls) - len(seen), "more": len(seen) > args["max"]}))
        seen = self.seen(page)
        n = args.get("n") or 0
        if args.get("gen") != page.gen or (n and not 1 <= n <= len(seen)):
            return drive.ActResult(json.dumps({"stale": 1}))
        control = seen[n - 1] if n else None
        if op == "type":
            if any(known is control for known in page.mine.values()):
                return drive.ActResult(json.dumps({"again": 1}))
            page.mine[args["key"]] = control
            self.typed_sealed.append(self.sealed)
            control.value = args["text"]
            if page.on_type is not None:
                page.on_type(page, control)
            return drive.ActResult(json.dumps({"ok": 1, "ms": args["ms"]}))
        if op == "choose":
            key = next((k for k, known in page.mine.items() if known is control), args["key"])
            if control.role == "select":
                match = next((o for o in control.options if o.casefold() == str(args["option"]).strip().casefold()), None)
                if match is None:
                    return drive.ActResult(json.dumps({"missing": 1}))
                control.value = match
            else:
                control.checked = bool(args["checked"])
            page.mine[key] = control
            return drive.ActResult(json.dumps({"ok": 1, "value": control.sent_value(), "key": key}))
        if op == "click":
            self.clicks.append((tab, n))
            if control.goes_to:
                return self._go(tab, control.goes_to, {"ok": 1})
            return drive.ActResult(json.dumps({"ok": 1}))
        if op == "scroll":
            return drive.ActResult(json.dumps({"top": 120, "height": 2400}))
        if op == "form":
            return drive.ActResult(json.dumps(self._form(page, control, args["mine"])))
        self.unapproved.append((tab, op))
        return drive.ActResult(json.dumps({"ok": 1}))

    @staticmethod
    def whole(value: str) -> dict[str, Any]:
        """A value as agent.drive's form script gives it: its first 2,001 characters, its length and a hash."""
        return {"value": value[:2001], "len": len(value), "hash": hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]}

    def _form(self, page: FakePage, control: FakeControl, mine: list[int]) -> dict[str, Any]:
        values = {str(k): self.whole(page.mine[k].sent_value()) for k in mine if k in page.mine}
        missing = [k for k in mine if k not in page.mine or page.mine[k] not in page.controls]
        form = control.form
        fields = []
        if form:
            for c in page.controls:
                if c.form != form or not c.field or c.role in ("submit", "button", "link"):
                    continue
                if c.role in ("checkbox", "radio") and not c.checked:
                    continue
                key = next((k for k, known in page.mine.items() if known is c), 0)
                fields.append({**self.whole(c.sent_value()), "k": key, "name": c.field, "hidden": c.hid})
            fields += [{**self.whole(value), "k": 0, "name": name, "hidden": hidden}
                       for number, name, value, hidden in page.extra if number == form]
        method, action, target = (list(page.forms[form - 1]) + [""])[:3] if form and form <= len(page.forms) \
            else ("", "", "")
        how = control.how()
        if how == "submit":
            action = control.formaction or action
            target = control.formtarget or target
        return {"words": control.name, "form": bool(form), "how": how, "method": method, "action": action,
                "target": target, "fields": fields, "mine": values, "missing": missing, "url": self._address(page),
                "title": page.title}

    def _go(self, tab: int, address: str, answer: dict[str, Any]) -> Any:
        from briefing_reader.agent import drive

        page = self.pages.get(address)
        self.loads.append(address)
        if page is not None:
            page.gen, page.mine = "", {}
        self.tabs[tab] = page
        if tab == self.active:
            self.current = page
        return drive.ActResult(json.dumps(answer), navigated=True, final_url=address)

    def press_approved(self, tab: int, source: str, hosts: Sequence[str], *, settle_s: float, timeout_s: float,
                       cancelled: Callable[[], bool]) -> Any:
        """The seal lifts for ``hosts`` only (recorded); a new page lifts it whole, none puts it back."""
        from briefing_reader.agent import drive

        self.press_hosts.append(tuple(hosts))
        was = self.sealed
        result = self._press(tab, source)
        self.sealed = was and not result.navigated
        self.sealed_after.append(self.sealed)
        return result

    def _press(self, tab: int, source: str) -> Any:
        from briefing_reader.agent import drive

        page = self.tabs.get(tab)
        found = self._DRIVE_RE.search(source)
        if page is None or found is None or found.group(1) != "press":
            return drive.ActResult(ok=False)
        args = json.loads(found.group(2))
        if self.before_press is not None:
            self.before_press(page)
        seen = self.seen(page)
        if args["gen"] != page.gen or not 1 <= args["n"] <= len(seen):
            return drive.ActResult(json.dumps({"stale": 1}))
        control = seen[args["n"] - 1]
        if json.dumps(self._form(page, control, args["mine"])) != args["expect"]:
            return drive.ActResult(json.dumps({"changed": 1}))
        how = control.how()
        if control.press == "invalid":
            return drive.ActResult(json.dumps({"ok": 1, "how": how, "fired": False, "prevented": False}))
        held = {c.field or c.name: c.sent_value() for c in page.controls
                if control.form and c.form == control.form and c.role not in ("submit", "button")}
        held.update({name: value for number, name, value, _hidden in page.extra if number == control.form})
        self.presses.append((tab, args["n"], held))
        answer = {"ok": 1, "how": how, "fired": how in ("submit", "enter"), "prevented": control.press == "prevent"}
        if control.goes_to and control.press != "prevent":
            return self._go(tab, control.goes_to, answer)
        return drive.ActResult(json.dumps(answer))


class FakeEngineRunner(FakeRunner):
    """FakeRunner with its queue holding FakeEngines too (its start() hands them what they were started with): Claude
    Code's quick commands answer like 2.1.293's."""


def base_engine(root: Path, runner: Any, *, environ: Mapping[str, str] | None = None) -> ClaudeEngine:
    """The planner's engine over ``runner``, in a data folder under ``root``: never the real claude.exe."""
    exe = fake_exe(root)
    ask = SimpleNamespace(model="sonnet", max_turns=4, timeout_seconds=30, hardened_flags=True)
    env = {"PATH": "C:/Windows", "ANTHROPIC_API_KEY": "sk-ant-dummy-not-for-the-child", "NOTION_TOKEN": "secret-not-for-it",
           "LOCALAPPDATA": str(root / "local")} if environ is None else dict(environ)
    return ClaudeEngine(ask, root / "data", runner=runner, environ=env, locate=lambda _env: exe)


def agent_engine(root: Path, runner: Any, **agent: Any) -> AgentEngine:
    return AgentEngine(replace(AgentConfig(enabled=True), **agent), base_engine(root, runner))


__all__ = ["AUTH_OK", "FAST", "FIXTURES", "FakeBrowser", "FakeControl", "FakeDriveBrowser", "FakeEngine",
           "FakeEngineRunner", "FakePage", "Killed",
           "agent_engine", "base_engine", "init_event", "rate_limit_event", "result_event"]
