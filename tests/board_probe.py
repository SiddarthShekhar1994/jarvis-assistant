"""One case of the board's host (briefing_reader.board.view.BoardView) with the real web engine, run in a process of its
own by tests/test_board_lockdown.py, so QtWebEngine never starts inside the shared unittest process.

    python -B tests/board_probe.py <case>      prints one JSON line (the case's findings)

Cases:
    lockdown   the real board page, offscreen: frames whose strings are markup render as text and load nothing; then
               the worst case, script running in the page itself (injected in its main world), tries every way out -
               fetch and XHR to the web and to local-network addresses, a beacon, a web socket, pictures, a style sheet
               and a font from elsewhere, a file: and a jarvis-task: address, a board address outside the allowlist,
               window.open, navigating away (three ways), a form, an inline frame, a <base>, an <object>, an <audio>,
               workers from blob: and data:, notifications, the location, the clipboard - and the page's own channel
               is asked for a bad kind, an unknown frame, an oversized payload, a deleteLater and a flood
    control    the same tries on the same page with its content security policy taken out (the probe serves board.html
               without the <meta>): the engine's own rules and the request interceptor still let nothing out
    media      the microphone and camera (getUserMedia): refused. A case of its own because Chromium starts its video
               capture service (a utility process) to list the devices BEFORE it asks for the permission, and keeps
               it until the engine ends: what is left after the board closes is that service only, never a renderer
    keys       the window's keys and Space with the focus in the page, in a window that keeps Space as Jarvis's does
               (ui._keeps_space); then the renderer is killed, the board comes back on its own, and the keys again
    fallback   inherited unsafe flags (Chromium never starts), a page that is not ready in time, and a renderer that
               keeps stopping: each time the native tile, and no renderer left
    shell      the page shell itself: the draw-in's timings lively, calm (x1.6, no rise) and off (the end state at
               once), the changed light, the stagger of an arrival (six at most), the waits / failed / empty / focused /
               closed states and their controls asking the app, and titles that wrap with no text cut when narrow
    kinds      part 4's kinds, built by board.frames from LIVE's pictures, research, to-dos and follow-ups (hostile words
               among them): each renders with every word of its data as text and no element of its own making; the
               mail's text is whole in the page and clamped to 3 lines by style only; the waits chip (a click, then
               Enter on it) asks for go_approvals and nothing else; the picture shows only its data: JPEG, at 80 % until
               lit or focused, and a real click on it focuses its frame and nothing else (no navigation, no request);
               re-sending a frame updates it in place with no draw-in (the changed lap only when changed) and keeps its
               picture; bodies Python would refuse, put into board.apply directly, are refused by the page too; at
               300 px no word is cut

    desk       part 5a: the desk in Jarvis's own window (ui.BriefingWindow): the board in the desk's board column, two
               answers' groups through board.feed (the mail and its waiting reply as one frame, first), the bar and
               the desk's enter; what the page shows, a grab of the window (the frames are in it), the first frame's
               time to the page's layout, reveal, a group leaving whole, Space in the page, the renderer's memory
               (desk_x15: the same at QT_SCALE_FACTOR 1.5)
    ways       part 5b: the board's ways with the real page AND the app's own controller (desk_ui.DeskController in
               a whole offscreen app, tests.ui_fakes.AppHarness; its 8 s made short): the index strip (board order,
               the rules, the in-view brace, the waiting pip, a real click scrolling to its frame), "N more below" at
               its 16 px edge, the fades, satellites (48 px, their facts, a click and Enter expanding), pin (real P;
               a 7th says why for 4 s), close (real Delete; the row, Bring back, then gone), All | Pinned, Clear and
               its Bring back (then gone, and the desk folding when nothing is left), parking (the focus in the page:
               the pill, the dashed strip entry, the scroll holding; Show; quiet), the room a group makes (220 ms),
               the keyboard (real F6, Shift+F6, the arrows, Home / End, Esc), pairs at 1232 x 640 (Left / Right), and
               the new messages refused by the page when hostile
    lit        part 5c with the app's own controller: Jarvis's sentence lights the frame it names (his voice's
               timeline handed to the app as the voice gives it) - its brackets in its own colour (AMBER_HI on amber,
               never cyan), its ground, the stub from the view's left edge to it (the dot 6 px from it, where the
               app's native part ends), hidden while the board scrolls and back after, gone with the speech; an
               arriving frame traced in LINE_FOCUS while one is lit; the changed lap (a frame built again from its
               source by a later answer: the light, then CHANGED; the same words again: nothing sent); three across
               at 1500 x 960 (Left / Right in the row) and pairs at 1480 x 960; Clear with nothing pinned (the desk
               stays for the Bring back row, then folds); the new messages refused by the page when hostile
    big        part 5c's open big in Jarvis's own window with the app's controller: a real double click opens it (the
               geometry set once, the backdrop the only new picture, the frame's scale from its rectangle to the
               whole view, the native dim), a resize to 900 x 600 keeps it spanning both columns, real Esc reverses
               it (the scale-down, then the geometry set back once, then the dim lifts); big_x15 at 1.5x

Qt runs offscreen and Chromium's proxy is a dead address (snapshots.OFFLINE_FLAGS, as the board's engine always runs in a
test): nothing can reach the network whatever the page does. No window is shown on the real screen.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

HOSTILE = {
    "title": "<img src=http://example.com/x.png>",
    "meta": "<script>window.pwned=1</script>",
    "text": ("<a href=\"javascript:window.pwned=2\">link</a> <img src=x onerror=\"window.pwned=3\"> "
             "<iframe src=\"http://example.com/\"></iframe> <style>*{display:none}</style> <b>bold</b> "
             "javascript:window.pwned=4 &lt;b&gt; &amp;"),
    "waits": "javascript:window.pwned=5",
    "status_text": "<img src=http://example.com/fail.png onerror=window.pwned=6>",
}
CONTROL_TEXT = "\u202eevil\u2066 \x00\x07 tab\tend \u200b"

# Script in the page's own main world: every way out it could try. Each result goes to window.__probe.results.
ATTEMPTS = r"""
(function () {
  var probe = window.__probe = {violations: [], results: {}};
  document.addEventListener('securitypolicyviolation', function (e) { probe.violations.push(e.violatedDirective); });
  function note(name, value) { probe.results[name] = value; }
  function settle(name, make) {
    try {
      make().then(function (v) { note(name, 'resolved ' + String(v)); },
                  function (e) { note(name, 'rejected ' + (e && e.name)); });
    } catch (e) { note(name, 'threw ' + e.name); }
  }
  settle('fetch_http', function () { return fetch('http://example.com/x'); });
  settle('fetch_https', function () { return fetch('https://example.com/x'); });
  settle('fetch_lan', function () { return fetch('http://192.168.1.1/x'); });
  settle('fetch_loopback', function () { return fetch('http://127.0.0.1:9/x'); });
  function xhr(name, url) {
    try {
      var x = new XMLHttpRequest();
      x.open('GET', url);
      x.onload = function () { note(name, 'loaded'); };
      x.onerror = function () { note(name, 'error'); };
      x.send();
    } catch (e) { note(name, 'threw ' + e.name); }
  }
  xhr('xhr_http', 'http://example.com/x');
  xhr('xhr_lan', 'http://10.0.0.1/x');
  try { note('beacon', String(navigator.sendBeacon('http://example.com/beacon', 'x'))); }
  catch (e) { note('beacon', 'threw ' + e.name); }
  try {
    var ws = new WebSocket('ws://example.com/');
    ws.onopen = function () { note('websocket', 'open'); };
    ws.onerror = function () { note('websocket', 'error'); };
  } catch (e) { note('websocket', 'threw ' + e.name); }
  function picture(name, url) {
    var i = new Image();
    i.onload = function () { note(name, 'loaded'); };
    i.onerror = function () { note(name, 'error'); };
    i.src = url;
  }
  picture('image_http', 'http://example.com/x.png');
  picture('image_https', 'https://example.com/x.png');
  picture('image_lan', 'http://192.168.1.1/x.png');
  picture('image_file', 'file:///C:/Windows/win.ini');
  picture('image_task', 'jarvis-task://task/x.png');
  function sheet(name, url) {
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.onload = function () { note(name, 'loaded'); };
    link.onerror = function () { note(name, 'error'); };
    link.href = url;
    document.head.appendChild(link);
  }
  sheet('css_remote', 'http://example.com/x.css');
  sheet('css_outside_allowlist', 'jarvis-board://board/../config.toml');
  settle('font_remote', function () { return new FontFace('ProbeFont', 'url(http://example.com/x.woff2)').load(); });
  try { note('window_open', String(window.open('http://example.com/'))); } catch (e) { note('window_open', 'threw ' + e.name); }
  try {
    var form = document.createElement('form');
    form.action = 'http://example.com/submit';
    form.method = 'GET';
    document.body.appendChild(form);
    form.submit();
    note('form', 'submitted');
  } catch (e) { note('form', 'threw ' + e.name); }
  var frame = document.createElement('iframe');
  frame.src = 'http://example.com/';
  document.body.appendChild(frame);
  var base = document.createElement('base');                 // base-uri
  base.href = 'http://example.com/';
  document.head.appendChild(base);
  note('base', document.baseURI);
  var object = document.createElement('object');             // object-src
  object.data = 'http://example.com/x.swf';
  document.body.appendChild(object);
  var audio = document.createElement('audio');               // media-src: default-src 'none'
  audio.onerror = function () { note('audio', 'error'); };
  audio.oncanplay = function () { note('audio', 'loaded'); };
  audio.src = 'http://example.com/x.mp3';
  document.body.appendChild(audio);
  try {
    var blobWorker = new Worker(URL.createObjectURL(new Blob(['postMessage(1)'], {type: 'text/javascript'})));
    blobWorker.onmessage = function () { note('worker_blob', 'ran'); };
    blobWorker.onerror = function () { note('worker_blob', 'error'); };
  } catch (e) { note('worker_blob', 'threw ' + e.name); }
  try {
    var dataWorker = new Worker('data:text/javascript,postMessage(1)');
    dataWorker.onmessage = function () { note('worker_data', 'ran'); };
    dataWorker.onerror = function () { note('worker_data', 'error'); };
  } catch (e) { note('worker_data', 'threw ' + e.name); }
  settle('notifications', function () { return Notification.requestPermission(); });
  try {
    navigator.geolocation.getCurrentPosition(function () { note('geolocation', 'position'); },
                                             function (e) { note('geolocation', 'error ' + e.code); });
  } catch (e) { note('geolocation', 'threw ' + e.name); }
  settle('clipboard', function () { return navigator.clipboard.readText(); });
  setTimeout(function () { try { window.location.href = 'http://example.com/'; } catch (e) { note('navigate_href', 'threw ' + e.name); } }, 200);
  setTimeout(function () { try { window.location.reload(); } catch (e) { note('navigate_reload', 'threw ' + e.name); } }, 400);
  setTimeout(function () {
    var a = document.createElement('a');
    a.href = 'https://example.com/';
    document.body.appendChild(a);
    a.click();
  }, 600);
  return 'started';
})()
"""

# A second client on the page's channel (the page's own script would be the worst case): what the bridge does with
# requests that are not on the list.
BRIDGE_TRIES = r"""
(function () {
  new QWebChannel(qt.webChannelTransport, function (channel) {
    var b = channel.objects.bridge;
    window.__bridgeKeys = Object.keys(b).filter(function (k) { return typeof b[k] === 'function'; }).sort().join('|');
    b.post('evil', '{}');
    b.post('open', '{"id":"nope"}');
    b.post('open', 'x'.repeat(5000));
    b.post('layout', JSON.stringify({frames: [{id: 'f1', top: 1e12, height: 1}]}));
    b.post('pin', '{"id":"f1"}');
    b.post('open', '{"id":"f1"}');
    b.deleteLater();
    setTimeout(function () {
      b.post('close', '{"id":"f2"}');
      for (var i = 0; i < 120; i += 1) { b.post('focus', '{"id":"f1"}'); }
      window.__bridgeDone = true;
    }, 300);
  });
  return 'started';
})()
"""


def process_kinds(pids: list[int]) -> list[str]:
    """What each child process is, from its command line: its --type= and --utility-sub-type= tokens only (Windows;
    [] elsewhere). Nothing else of a command line is kept."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtQueryInformationProcess.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
                                                ctypes.POINTER(wintypes.ULONG))
    kinds: list[str] = []
    for pid in pids:
        handle = kernel.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            kinds.append("unknown")
            continue
        try:
            size = wintypes.ULONG(0)
            ntdll.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(size))   # ProcessCommandLineInformation
            buffer = ctypes.create_string_buffer(max(size.value, ctypes.sizeof(UnicodeString)))
            if ntdll.NtQueryInformationProcess(handle, 60, buffer, len(buffer), ctypes.byref(size)) != 0:
                kinds.append("unknown")
                continue
            text = UnicodeString.from_buffer(buffer)
            line = ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Buffer else ""
            kinds.append(" ".join(token for token in line.split()
                                  if token.startswith(("--type=", "--utility-sub-type="))) or "unknown")
        finally:
            kernel.CloseHandle(handle)
    return sorted(kinds)


def tiny_jpeg_url(width: int = 96, height: int = 54) -> str:
    """A real JPEG the probe makes (as the app re-encodes a page picture), as a data: URL."""
    import base64

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#1e6091"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", 80)
    buffer.close()
    return "data:image/jpeg;base64," + base64.b64encode(bytes(data.data())).decode("ascii")


PROBE_MAIL = ("Here are the Q4 numbers - line 14 changed since Friday, the rest is the same. <b>Bold</b> stays words, "
              "as does javascript:window.pwned=7 and a link <a href=\"https://example.com/\">here</a>. " * 3)


def kind_frames(jpeg_url: str) -> list[tuple[str, Any]]:
    """Part 4's frames as the builders make them (board.frames), from invented data with hostile words among it."""
    from datetime import date, datetime, timedelta, timezone

    from briefing_reader import pictures
    from briefing_reader.actions import parse_action_line
    from briefing_reader.agenda import Deadline
    from briefing_reader.ask.mail import MailMessage, MailPerson, MailThread
    from briefing_reader.ask.research import ResearchAnswer
    from briefing_reader.board import frames
    from briefing_reader.followups import OWED, WAITING, FollowupItem
    from briefing_reader.gcal import EventBrief
    from briefing_reader.gmail import MailView

    zone = timezone(timedelta(hours=-7))
    now = datetime(2026, 11, 17, 9, 41, tzinfo=zone)

    def at(hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 11, 17, hour, minute, tzinfo=zone)

    thread = MailThread("work", "t1", "Re: Budget numbers <img src=x>", (MailMessage(
        "g1", sender=MailPerson("dana@example.edu", "Dana Whitfield"), sent_at=at(8, 12),
        subject="Re: Budget numbers <img src=x>", text=PROBE_MAIL),))
    reply = MailView("me@example.edu", "dana@example.edu", "", "Re: Budget numbers", "<abc@example.com>", "", "t1",
                     "Hi Dana, thanks - looks right. <script>window.pwned=8</script>")
    day = {"work": [EventBrief("e0", "primary", "Breakfast", start=at(8), end=at(8, 30)),
                    EventBrief("e1", "primary", "AI-Trader check-in", start=at(10), end=at(10, 30)),
                    EventBrief("e2", "primary", "Study group <i>2F</i>", start=at(13), end=at(14)),
                    EventBrief("e3", "primary", "Project Development check-in", start=at(16), end=at(16, 45))]}
    import base64

    page = pictures.page_ready("https://city-hack.example.org/schedule", width=320, height=180,
                               image=base64.b64decode(jpeg_url.split(",", 1)[1]))
    search = pictures.search_picture("city hack schedule", [("City Hack 2026", "https://city-hack.example.org/"),
                                                            ("<b>Evil</b>", "javascript:window.pwned=9")])
    todo = parse_action_line("Todo: title=Send budget comments to Dana | due=2026-11-19 | acct=work | link=")
    done = parse_action_line("Todo: title=Print boarding pass | due=2026-11-18 | acct=work | link=")
    return [
        ("mail", frames.from_picture(pictures.email_picture(thread, tz=zone), frame_id="m1", now=now)),
        ("reply", frames.from_picture(pictures.outgoing_picture(reply, account="work"), frame_id="m2", now=now)),
        ("day", frames.from_picture(pictures.plan_picture(day, (), day=date(2026, 11, 17), now=now, hour24=True),
                                    frame_id="c1", now=now, title="Today")),
        ("week", frames.from_picture(pictures.week_picture(day, at(0), at(0) + timedelta(days=7), now=now),
                                     frame_id="w1", now=now)),
        ("page", frames.from_picture(page, frame_id="p1", now=now)),
        ("search", frames.from_picture(search, frame_id="s1", now=now)),
        ("research", frames.from_research(ResearchAnswer(
            "The schedule moved to Friday. <img src=x onerror=window.pwned=10>",
            (("City Hack schedule", "https://city-hack.example.org/s"), ("Evil", "javascript:window.pwned=11"))),
            frame_id="r1")),
        ("todos", frames.from_todos([todo, done], [Deadline("LING essay draft", date(2026, 11, 19))], frame_id="t1",
                                    now=now, statuses={done.id: "done"})),
        ("followups", frames.from_followups([
            FollowupItem("r1", "k", WAITING, "work", thread_id="t", last_gmail_id="g", who="Prof. Ana Ruiz",
                         subject="Lab section moved <u>Thursday</u>", since=now - timedelta(hours=30)),
            FollowupItem("r2", "k", OWED, "work", thread_id="t", last_gmail_id="g", who="City Hack",
                         subject="Registration closes Friday", since=now - timedelta(days=3))], frame_id="f1",
            now=now)),
    ]


def shown_words(frame: dict[str, Any]) -> list[str]:
    """Every word a frame's data says the page shows (a "+N MORE" row's meta only; a to-do's state word for its
    meta)."""
    found = [frame["title"], frame["meta"], frame["waits"], frame["status_text"], frame["status_meta"]]
    body = frame["body"]
    for key, value in body.items():
        if isinstance(value, str):
            found.append(value)
    for row in body.get("rows", []):
        found += [value for value in row.values() if isinstance(value, str) and value not in ("", "open", "waits",
                                                                                              "done", *_STATES)]
    for tick in body.get("ticks", []):
        found.append(tick["text"])
    return [words for words in found if words]


_STATES = ("past", "now", "next", "free", "new", "struck", "today")


def main(case: str) -> dict[str, Any]:
    from briefing_reader import snapshot_engine, snapshots

    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(snapshots.OFFLINE_FLAGS)
    snapshot_engine.prepare_application()
    from briefing_reader.board import scheme

    registered = scheme.register_scheme()
    from PySide6.QtCore import QEvent, QEventLoop, Qt, QTimer, qInstallMessageHandler
    from PySide6.QtGui import QColor, QKeySequence, QShortcut
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication([sys.argv[0], "-platform", "offscreen"])
    app.setQuitOnLastWindowClosed(False)
    messages: list[str] = []
    qInstallMessageHandler(lambda _mode, _context, text: messages.append(str(text)))

    import tests  # noqa: F401 - the desktop test guard
    from briefing_reader import holo, hud, ui
    from briefing_reader.board import protocol, view
    from tests.snapshot_probe import children

    hud.load_fonts(PROJECT_ROOT / "fonts")

    def wait_until(predicate: Any, timeout_s: float) -> bool:
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

    def main_world(board: Any, source: str) -> Any:
        box: list[Any] = []
        board.web_view().page().runJavaScript(source, 0, box.append)
        wait_until(lambda: box, 10)
        return box[0] if box else None

    class Host(QWidget):
        """A window that keeps Space as Jarvis's main window does (ui.BriefingWindow.event)."""

        def event(self, event: QEvent) -> bool:  # noqa: N802 - Qt override
            if (event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Space
                    and event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier)
                    and ui._keeps_space(self.focusWidget())):  # noqa: SLF001 - the rule under test
                event.accept()
                return True
            return super().event(event)

    seen: list[list[Any]] = []

    def make(**options: Any) -> tuple[Any, Any]:
        host = Host()
        host.resize(503, 700)
        host.setAutoFillBackground(True)
        palette = host.palette()
        palette.setColor(host.backgroundRole(), QColor(holo.VOID))
        host.setPalette(palette)
        options.setdefault("start_delay_ms", 0)
        board = view.BoardView(host, **options)
        board.setGeometry(0, 0, 503, 700)
        board.ready.connect(lambda: seen.append(["ready"]))
        board.unavailable.connect(lambda why: seen.append(["unavailable", why]))
        for name in ("frameFocused", "openRequested", "closeRequested", "bringBackRequested", "retryRequested",
                     "approvalsRequested"):
            getattr(board, name).connect(lambda frame_id, n=name: seen.append([n, frame_id]))
        board.pinRequested.connect(lambda frame_id, on: seen.append(["pinRequested", frame_id, on]))
        board.layoutChanged.connect(lambda _layout: seen.append(["layoutChanged"]))
        return host, board

    def readies() -> int:
        return sum(1 for item in seen if item == ["ready"])

    def settle_end(report: dict[str, Any], *boards: Any) -> None:
        for board in boards:
            board.shutdown()
        pump(0.3)
        wait_until(lambda: not children(), 15)
        report["children_end"] = len(children())
        report["profile_warning"] = any("still not deleted" in text for text in messages)

    report: dict[str, Any] = {"case": case, "platform": app.platformName(), "registered": registered}

    if case == "control":
        # The page as it would be without its own policy: board.html served without the <meta>.
        real_body = scheme.body

        def no_policy(name: str) -> Any:
            item = real_body(name)
            if name == scheme.PAGE and item is not None:
                kind, data = item
                start = data.index(b'<meta http-equiv="Content-Security-Policy"')
                data = data[:start] + data[data.index(b">", start) + 1:]
                return kind, data
            return item

        scheme.body = no_policy

    if case in ("lockdown", "control"):
        host, board = make()
        host.show()
        began = time.monotonic()
        wait_until(lambda: board.is_ready() or board.unavailable_reason(), 40)
        report["ready_s"] = round(time.monotonic() - began, 2)
        report["ready"] = board.is_ready()
        report["policy_in_page"] = main_world(
            board, "String(!!document.querySelector('meta[http-equiv=\"Content-Security-Policy\"]'))")
        jpeg = tiny_jpeg_url()
        applied = [
            board.apply({"type": "frame", "id": "f1", "kind": "text", "title": HOSTILE["title"], "meta": HOSTILE["meta"],
                         "body": {"text": HOSTILE["text"]}, "waits": HOSTILE["waits"]}),
            board.apply({"type": "frame", "id": "f2", "kind": "text", "title": CONTROL_TEXT,
                         "body": {"text": "line one\nline two \u2028 \u200b"}}),
            board.apply({"type": "frame", "id": "f3", "kind": "text", "title": "Failed", "status": "failed",
                         "status_text": HOSTILE["status_text"]}),
            board.apply({"type": "picture", "id": "f1", "src": jpeg}),
        ]
        report["applied"] = applied
        report["refused_2mb_picture"] = not board.apply(
            {"type": "picture", "id": "f1", "src": "data:image/jpeg;base64," + "A" * (2 * 1024 * 1024)})
        pump(1.5)
        report["rendered"] = json.loads(main_world(board, """JSON.stringify((function () {
            var root = document.getElementById('board');
            var pictures = root.querySelectorAll('img');
            return {
              frames: root.querySelectorAll('.frame').length,
              title1: document.getElementById('title-f1').textContent,
              meta1: root.querySelector('.frame .f-meta').textContent,
              text1: root.querySelector('.frame .f-text').textContent,
              waits1: root.querySelector('.frame .f-wait .f-words').textContent,
              title2: document.getElementById('title-f2').textContent,
              fail3: root.querySelector('.frame.failed .f-words').textContent,
              markup: root.querySelectorAll('a, iframe, script, style, b:not(.f-closed b), object, embed, form').length,
              scripts: document.scripts.length,
              pictures: pictures.length,
              picture_is_data: pictures.length === 1 && pictures[0].src.indexOf('data:image/jpeg;base64,') === 0,
              picture_loaded: pictures.length === 1 && pictures[0].complete && pictures[0].naturalWidth === 96,
              pwned: typeof window.pwned
            };
          })())"""))
        report["expected"] = {"title1": HOSTILE["title"], "meta1": HOSTILE["meta"], "text1": HOSTILE["text"],
                              "waits1": HOSTILE["waits"], "title2": CONTROL_TEXT, "fail3": HOSTILE["status_text"]}
        before = board.counts()
        report["allowed_before_tries"] = before["allowed"]
        report["blocked_before_tries"] = before["blocked"]
        report["attempts_started"] = main_world(board, ATTEMPTS)
        pump(3.5)
        found = main_world(board, "JSON.stringify(window.__probe || null)")
        report["attempts"] = json.loads(found) if isinstance(found, str) else None
        report["url_in_page"] = main_world(board, "document.URL")
        report["url"] = board.web_view().page().url().toString()
        report["bridge_started"] = main_world(board, BRIDGE_TRIES)
        wait_until(lambda: main_world(board, "String(!!window.__bridgeDone)") == "true", 10)
        pump(0.5)
        report["bridge_methods"] = main_world(board, "window.__bridgeKeys")
        report["frames_after"] = main_world(board, "document.querySelectorAll('#board .frame').length")
        report["counts"] = board.counts()
        report["seen"] = [item for item in seen if item[0] != "layoutChanged"]
        report["layouts"] = sum(1 for item in seen if item[0] == "layoutChanged")
        report["engine_flags"] = snapshots.flag_tokens(os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""))
        report["children_while_open"] = len(children())
        settle_end(report, board)

    elif case == "shell":
        host, board = make()
        host.show()
        wait_until(board.is_ready, 40)
        report["ready"] = board.is_ready()
        style = ("(function (id, sel, prop) { var f = document.getElementById('title-' + id).closest('.frame');"
                 " var el = sel ? f.querySelector(sel) : f; return getComputedStyle(el)[prop]; })")

        def styled(frame_id: str, selector: str, prop: str) -> Any:
            return main_world(board, f"{style}({json.dumps(frame_id)}, {json.dumps(selector)}, {json.dumps(prop)})")

        def classes(frame_id: str) -> list[str]:
            found = main_world(board, f"Array.from(document.getElementById('title-' + {json.dumps(frame_id)})"
                                      ".closest('.frame').classList).join(' ')")
            return str(found or "").split()

        def text_frame(frame_id: str, **fields: Any) -> dict:
            message = {"type": "frame", "id": frame_id, "kind": "text", "title": f"Frame {frame_id}", "meta": "Now",
                       "body": {"text": "A few words of text."}}
            message.update(fields)
            return message

        board.set_motion("lively")
        board.apply(text_frame("m1"))
        report["lively"] = {"classes": classes("m1"), "trace": styled("m1", ".fx-outline rect", "animationDuration"),
                            "ticks_at": styled("m1", ".fx-tick", "animationDelay"),
                            "content": styled("m1", ".f-head", "animationName")}
        pump(1.2)
        report["lively_after"] = {"classes": classes("m1"), "opacity": styled("m1", ".f-head", "opacity")}
        board.set_motion("calm")
        board.apply(text_frame("m2"))
        report["calm"] = {"classes": classes("m2"), "trace": styled("m2", ".fx-outline rect", "animationDuration"),
                          "ticks_at": styled("m2", ".fx-tick", "animationDelay"),
                          "content": styled("m2", ".f-head", "animationName")}
        pump(1.8)
        board.set_motion("off")
        board.apply(text_frame("m3"))
        board.apply({"type": "lit", "id": "m3"})
        report["off"] = {"classes": classes("m3"), "opacity": styled("m3", ".f-head", "opacity"),
                         "brackets": styled("m3", ".fx-bracket", "animationDuration"),
                         "bracket_opacity": styled("m3", ".fx-bracket", "opacity")}
        board.apply({"type": "lit", "id": ""})
        # The changed light: one lap (lively), then CHANGED; calm: the badge only.
        board.set_motion("lively")
        board.apply(text_frame("m1", body={"text": "New words."}, changed=True))
        report["changed_lively"] = {"during": classes("m1"),
                                    "lap": styled("m1", ".fx-light rect", "animationDuration")}
        pump(1.2)
        report["changed_lively"]["after"] = classes("m1")
        report["changed_lively"]["tag"] = main_world(board, "document.getElementById('title-m1').closest('.frame')"
                                                            ".querySelector('.f-tag').textContent")
        board.set_motion("calm")
        board.apply(text_frame("m2", body={"text": "Other words."}, changed=True))
        report["changed_calm"] = classes("m2")
        # At most six of one arrival draw in, 80 ms apart; the rest are there at once.
        board.set_motion("lively")
        for index in range(7):
            board.apply(text_frame(f"s{index}"))
        report["stagger"] = [[classes(f"s{index}").count("arriving"),
                              main_world(board, f"document.getElementById('title-s{index}').closest('.frame')"
                                                ".style.getPropertyValue('--i')")] for index in range(7)]
        pump(1.5)
        # The states, as the owner meets them: waits, failed (Try again), empty, then closing and Bring back.
        board.apply(text_frame("w1", waits="Reply waits on Approvals"))
        board.apply(text_frame("x1", status="failed", status_text="Google Calendar didn't answer."))
        board.apply(text_frame("e1", status="empty", status_text="Nothing unread, sir.", status_meta="Checked 9:44 AM"))
        pump(1.2)
        report["states"] = {
            "waits": [styled("w1", "", "borderTopLeftRadius"), styled("w1", ".f-wait", "display")],
            "failed": [styled("x1", "", "backgroundColor"), main_world(
                board, "document.getElementById('title-x1').closest('.frame').querySelector('.f-act').textContent")],
            "empty": main_world(board, "document.getElementById('title-e1').closest('.frame').querySelector('.f-checked')"
                                       ".textContent"),
        }
        main_world(board, "document.getElementById('title-w1').closest('.frame').querySelector('.f-wait').click(); 1")
        main_world(board, "document.getElementById('title-x1').closest('.frame').querySelector('.f-act').click(); 1")
        main_world(board, "document.getElementById('title-m1').closest('.frame').focus(); 1")
        pump(0.3)
        report["focused"] = {"keys": styled("m1", ".f-keys", "display"), "meta": styled("m1", ".f-meta", "display"),
                             "controls": styled("m1", ".f-ctl", "display"),
                             "footer": main_world(board, "document.getElementById('title-m1').closest('.frame')"
                                                         ".querySelector('.f-keys').innerText")}
        main_world(board, "document.getElementById('title-m1').closest('.frame').querySelectorAll('.f-icon')[2].click(); 1")
        pump(0.6)
        report["closed"] = {"classes": classes("m1"), "row": main_world(
            board, "document.getElementById('title-m1').closest('.frame').querySelector('.f-closed').innerText")}
        main_world(board, "document.getElementById('title-m1').closest('.frame').querySelector('.f-closed .f-act')"
                          ".click(); 1")
        pump(0.6)
        report["brought_back"] = classes("m1")
        # Titles wrap and are never cut, also in a narrow board.
        board.apply(text_frame("t1", title="A title of sixty characters that has to wrap onto two lines",
                               meta="Tue 17 Nov · 3 meetings"))
        board.setGeometry(0, 0, 300, 700)
        pump(1.2)
        report["wrap"] = json.loads(main_world(board, """JSON.stringify((function () {
            var title = document.getElementById('title-t1');
            var frame = title.closest('.frame');
            var words = Array.from(document.querySelectorAll('#board .f-title, #board .f-meta, #board .f-text,'
                                                             + ' #board .f-words, #board .f-keys span'));
            var root = document.getElementById('board');
            return {lines: Math.round(title.getBoundingClientRect().height / 20),
                    text_cut: words.filter(function (el) { return el.scrollWidth > el.clientWidth + 1; }).length,
                    words: words.length, page_cut: root.scrollWidth > root.clientWidth,
                    numerals: getComputedStyle(frame).fontVariantNumeric};
          })())"""))
        report["seen"] = [item for item in seen if item[0] not in ("layoutChanged", "frameFocused")]
        settle_end(report, board)

    elif case == "kinds":
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest

        host, board = make()
        host.show()
        host.activateWindow()
        wait_until(lambda: QApplication.activeWindow() is host, 5)
        wait_until(board.is_ready, 40)
        report["ready"] = board.is_ready()
        board.set_motion("lively")
        jpeg = tiny_jpeg_url(320, 180)
        built = kind_frames(jpeg)
        report["applied"] = [board.apply(message) for _name, one in built for message in one.messages]
        report["refused_pictures"] = sum(one.refused for _name, one in built)
        sent = {one.messages[0]["id"]: json.loads(protocol.check_message(one.messages[0], set()).text)
                for _name, one in built}
        report["expected"] = {frame_id: shown_words(frame) for frame_id, frame in sent.items()}
        report["kinds"] = {frame_id: frame["kind"] for frame_id, frame in sent.items()}
        pump(1.5)
        before = board.counts()
        report["allowed_before"] = before["allowed"]
        report["rendered"] = json.loads(main_world(board, """JSON.stringify((function () {
            var made = 'a, script, iframe, style, object, embed, form, link, base, u, img:not(.f-pic), '
              + 'b:not(.mt-sender):not(.f-closed b), i:not([class]):not(.fx-skeleton i)';   // none is the page's
            var found = {};
            document.querySelectorAll('#board .frame').forEach(function (f) {
              var id = f.querySelector('.f-title').id.slice(6);
              found[id] = {text: f.textContent, classes: f.className,
                           glyph: f.querySelector('.f-glyph path').getAttribute('class'),
                           markup: f.querySelectorAll(made).length,
                           images: f.querySelectorAll('img').length};
            });
            var mail = document.getElementById('title-m1').closest('.frame').querySelector('.mt-text');
            var line = parseFloat(getComputedStyle(mail).lineHeight);
            var picture = document.querySelector('#board .f-pic');
            return {frames: found, scripts: document.scripts.length, pwned: typeof window.pwned,
                    mail: {text: mail.textContent, lines: Math.round(mail.getBoundingClientRect().height / line),
                           clamped: mail.scrollHeight > mail.clientHeight + 1},
                    picture: {src: picture.getAttribute('src').slice(0, 23), loaded: picture.complete && picture.naturalWidth === 320,
                              inSlot: !!picture.closest('.pic-slot'), filter: getComputedStyle(picture).filter,
                              draggable: picture.draggable},
                    horizon: (function () {
                      var rows = Array.from(document.getElementById('title-c1').closest('.frame').querySelectorAll('.cal-rows > *'));
                      var at = rows.findIndex(function (el) { return el.classList.contains('cal-now'); });
                      return {at: at, before: rows[at - 1] ? rows[at - 1].className : '', after: rows[at + 1] ? rows[at + 1].className : ''};
                    })()};
          })())"""))
        report["mail_text"] = sent["m1"]["body"]["text"]
        # The waits chip: a click, then Enter on it (a real key), ask for go_approvals and nothing else.
        chip = "document.getElementById('title-m2').closest('.frame').querySelector('.f-wait')"
        mark = len(seen)
        main_world(board, f"{chip}.click(); 1")
        pump(0.4)
        report["chip_click"] = [item for item in seen[mark:] if item[0] != "layoutChanged"]
        board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
        wait_until(lambda: view.keeps_space(QApplication.focusWidget()), 5)
        main_world(board, f"{chip}.focus(); 1")
        pump(0.4)
        mark = len(seen)
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Return)
        pump(0.5)
        report["chip_enter"] = [item for item in seen[mark:] if item[0] != "layoutChanged"]
        # The picture: a real click on it focuses its frame, and nothing else happens.
        main_world(board, "document.querySelector('#board .f-pic').scrollIntoView({block: 'center'}); 1")
        pump(0.6)
        box = json.loads(main_world(board, "JSON.stringify((function () { var r = document.querySelector("
                                           "'#board .f-pic').getBoundingClientRect(); return [r.left, r.top, r.width,"
                                           " r.height]; })())"))
        mark = len(seen)
        url_before = board.web_view().page().url().toString()
        QTest.mouseClick(board.web_view().focusProxy(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(int(box[0] + box[2] / 2), int(box[1] + box[3] / 2)))
        pump(0.6)
        report["picture_click"] = {
            "seen": [item for item in seen[mark:] if item[0] != "layoutChanged"],
            "focused": main_world(board, "(document.activeElement.querySelector('.f-pic') ? 'picture frame' : "
                                         "document.activeElement.className)"),
            "filter_focused": main_world(board, "getComputedStyle(document.querySelector('#board .f-pic')).filter"),
            "drag_refused": main_world(board, "(function () { var e = new DragEvent('dragstart', {bubbles: true, "
                                              "cancelable: true}); document.querySelector('#board .f-pic')"
                                              ".dispatchEvent(e); return e.defaultPrevented; })()"),
            "url_same": board.web_view().page().url().toString() == url_before,
        }
        main_world(board, "document.activeElement.blur(); 1")
        board.apply({"type": "lit", "id": "p1"})
        pump(0.3)
        report["picture_lit_filter"] = main_world(board, "getComputedStyle(document.querySelector('#board .f-pic'))"
                                                         ".filter")
        board.apply({"type": "lit", "id": ""})
        # An update in place: the same element, no draw-in; the lap only when changed; the picture stays.
        main_world(board, "document.getElementById('title-c1').closest('.frame').__probe = 7; "
                          "document.getElementById('title-p1').closest('.frame').__probe = 8; 1")
        update = json.loads(json.dumps(sent["c1"]))
        update["body"]["rows"][0]["title"] = "Breakfast moved"
        board.apply(update)
        board.apply(dict(sent["p1"], meta="9:42 AM"))
        pump(0.2)
        report["update"] = json.loads(main_world(board, """JSON.stringify((function () {
            var c = document.getElementById('title-c1').closest('.frame');
            var p = document.getElementById('title-p1').closest('.frame');
            return {same: c.__probe === 7 && p.__probe === 8, classes: c.className, text: c.textContent.indexOf('Breakfast moved') >= 0,
                    pictures: p.querySelectorAll('.pic-slot img').length, meta: p.querySelector('.f-meta').textContent};
          })())"""))
        board.apply(dict(update, changed=True))
        pump(0.2)
        report["update_changed"] = main_world(board, "document.getElementById('title-c1').closest('.frame').className")
        # Bodies Python refuses are refused by the page too (put into board.apply directly).
        base = {"type": "frame", "id": "bad", "title": "x", "meta": "", "status": "ok", "status_text": "",
                "status_meta": "", "waits": "", "changed": False, "pinned": False, "closed": False}
        bad = [
            dict(base, kind="calendar", body={"first": 0, "last": 600, "now_min": -1, "ticks": [], "now": "",
                                              "spans": [{"start": 700, "end": 800, "kind": "busy"}], "rows": []}),
            dict(base, kind="calendar", body={"first": 0, "last": 600, "now_min": -1, "ticks": [], "spans": [],
                                              "now": "", "rows": [{"time": "", "title": "", "meta": "",
                                                                   "state": "soon"}]}),
            dict(base, kind="todos", body={"rows": [{"text": "x", "meta": "", "state": "open"}] * 9}),
            dict(base, kind="mail_list", body={"rows": [{"sender": "x", "time": "", "subject": "", "meta": "",
                                                         "unread": "yes"}]}),
            dict(base, kind="text", body={"text": "", "rows": [{"lead": "", "text": "x", "meta": "", "html": "<b>"}]}),
            dict(base, kind="picture", body={"caption": "x" * 61}),
            dict(base, kind="mail_thread", body={"sender": "", "time": "", "text": "", "href": "x"}),
            dict(base, kind="todos", body={"rows": [{"text": "x", "meta": ""}]}),
        ]
        report["page_refuses"] = [main_world(board, f"board.apply({json.dumps(json.dumps(item))})") for item in bad]
        report["bad_frames"] = main_world(board, "document.querySelectorAll('#title-bad').length")
        # At 300 px no word is cut.
        board.setGeometry(0, 0, 300, 700)
        pump(1.0)
        report["narrow"] = json.loads(main_world(board, """JSON.stringify((function () {
            var words = Array.from(document.querySelectorAll('#board .frame span, #board .frame p, #board .frame b, #board .frame h2'));
            var root = document.getElementById('board');
            return {cut: words.filter(function (el) { return el.scrollWidth > el.clientWidth + 1; }).map(function (el) { return el.className; }),
                    words: words.length, page_cut: root.scrollWidth > root.clientWidth};
          })())"""))
        after = board.counts()
        report["allowed_after"] = after["allowed"]
        report["blocked_after"] = after["blocked"]
        report["navigation_refused"] = after.get("refused:navigation", 0)
        report["seen"] = [item for item in seen if item[0] not in ("layoutChanged",)]
        settle_end(report, board)

    elif case == "media":
        host, board = make()
        host.show()
        wait_until(board.is_ready, 40)
        report["ready"] = board.is_ready()
        main_world(board, "window.__media = ''; navigator.mediaDevices.getUserMedia({audio: true, video: true})"
                          ".then(function () { window.__media = 'stream'; },"
                          " function (e) { window.__media = 'rejected ' + e.name; }); 'started'")
        wait_until(lambda: main_world(board, "window.__media"), 15)
        report["media"] = main_world(board, "window.__media")
        report["kinds_while_open"] = process_kinds(children())
        report["counts"] = board.counts()
        board.shutdown()
        pump(0.3)
        wait_until(lambda: not any("renderer" in kind for kind in process_kinds(children())), 15)
        report["kinds_after"] = process_kinds(children())
        report["profile_warning"] = any("still not deleted" in text for text in messages)

    elif case == "keys":
        from PySide6.QtTest import QTest

        host, board = make()
        fired: dict[str, int] = {}
        for key in view.WINDOW_KEYS:
            sequence = QKeySequence(Qt.KeyboardModifier.ControlModifier | key)
            shortcut = QShortcut(sequence, host)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda name=sequence.toString(): fired.__setitem__(name, fired.get(name, 0) + 1))
        play = QShortcut(QKeySequence(Qt.Key.Key_Space), host)
        play.setContext(Qt.ShortcutContext.WindowShortcut)
        plays: list[int] = []
        play.activated.connect(lambda: plays.append(1))
        host.show()
        host.activateWindow()
        wait_until(lambda: QApplication.activeWindow() is host, 5)
        wait_until(board.is_ready, 40)
        report["ready"] = board.is_ready()

        def send_keys(label: str) -> None:
            board.apply({"type": "frame", "id": "f1", "kind": "text", "title": "Keys", "body": {"text": "Focus here."}})
            pump(0.4)
            board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
            wait_until(lambda: view.keeps_space(QApplication.focusWidget()), 5)
            main_world(board, "window.__keys = []; document.addEventListener('keydown', function (e) {"
                              " window.__keys.push((e.ctrlKey ? 'ctrl+' : '') + e.key); }, true);"
                              " document.querySelector('.frame').focus(); 'ok'")
            pump(0.2)
            target = QApplication.focusWidget()
            report[f"{label}_focus_in_board"] = view.keeps_space(target)
            report[f"{label}_focus_is_proxy"] = target is board.web_view().focusProxy()
            report[f"{label}_proxy"] = id(board.web_view().focusProxy())
            for key in view.WINDOW_KEYS:
                QTest.keyClick(target, key, Qt.KeyboardModifier.ControlModifier)
                pump(0.05)
            QTest.keyClick(target, Qt.Key.Key_Space)
            pump(0.1)
            QTest.keyClick(target, Qt.Key.Key_P)
            pump(0.1)
            QTest.keyClick(target, Qt.Key.Key_Return)
            pump(0.4)
            report[f"{label}_fired"] = dict(fired)
            report[f"{label}_plays"] = len(plays)
            report[f"{label}_page_keys"] = json.loads(main_world(board, "JSON.stringify(window.__keys)") or "[]")

        send_keys("before")
        # The control: the same Space, without the board in ui._keeps_space, plays the briefing.
        real = ui.board_view.keeps_space
        ui.board_view.keeps_space = lambda _widget: False
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
        pump(0.2)
        report["control_plays"] = len(plays)
        ui.board_view.keeps_space = real
        # The renderer stops: the board loads its page again by itself (a new focus proxy), the app sends its frames.
        page = board.web_view().page()
        report["readies_before_kill"] = readies()
        os.kill(page.renderProcessPid(), signal.SIGTERM)
        wait_until(lambda: readies() >= 2, 40)
        report["readies_after_kill"] = readies()
        report["ready_after_kill"] = board.is_ready()
        plays.clear()
        fired.clear()
        send_keys("after")
        report["counts"] = board.counts()
        report["seen"] = [item for item in seen if item[0] not in ("layoutChanged",)]
        settle_end(report, board)

    elif case == "fallback":
        # 1. Inherited settings Jarvis refuses: the board falls back before Chromium could start.
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(snapshots.OFFLINE_FLAGS) + " --remote-debugging-port=9222"
        host1, refused = make()
        host1.show()
        wait_until(lambda: refused.unavailable_reason(), 10)
        report["unsafe"] = {"reason": refused.unavailable_reason(), "started": snapshot_engine._ENGINE.started,
                            "children": len(children()), "words": list(refused.fallback().words()),
                            "web_view": refused.web_view() is not None, "apply": refused.apply(
                                {"type": "frame", "id": "x", "kind": "text", "title": "x"})}
        # Fallen back, frames still come: read-only tiles under the tile (glance.GlanceTiles), never the web engine.
        report["unsafe"]["words_after"] = list(refused.fallback().words())
        report["unsafe"]["tiles"] = [frame["id"] for frame in refused.tiles().frames()]
        report["unsafe"]["started_after"] = snapshot_engine._ENGINE.started
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(snapshots.OFFLINE_FLAGS)
        # 2. A page that does not say it is ready in time.
        host2, late = make(ready_timeout_ms=1)
        host2.show()
        wait_until(lambda: late.unavailable_reason(), 20)
        wait_until(lambda: not children(), 15)
        report["late"] = {"reason": late.unavailable_reason(), "started": snapshot_engine._ENGINE.started,
                          "web_view": late.web_view() is not None, "fallback": late.fallback() is not None,
                          "children_after": len(children())}
        # 3. A renderer that keeps stopping: loaded again MAX_RESTARTS times, then the tile.
        host3, crashing = make()
        host3.show()
        wait_until(crashing.is_ready, 40)
        stops = 0
        while crashing.web_view() is not None and stops <= view.MAX_RESTARTS:
            readies_now = readies()
            os.kill(crashing.web_view().page().renderProcessPid(), signal.SIGTERM)
            stops += 1
            wait_until(lambda: readies() > readies_now or crashing.unavailable_reason(), 40)
        wait_until(lambda: not children(), 15)
        report["crash"] = {"stops": stops, "reason": crashing.unavailable_reason(),
                           "renderer_stopped": crashing.counts().get("renderer_stopped", 0),
                           "children_after": len(children())}
        report["seen"] = seen
        settle_end(report, refused, late, crashing)
    elif case in ("desk", "desk_x15"):
        report.update(desk_case(wait_until, pump, main_world, settle_end))
    elif case == "ways":
        report.update(ways_case(wait_until, pump, main_world, settle_end))
    elif case == "lit":
        report.update(lit_case(wait_until, pump, main_world, settle_end))
    elif case in ("big", "big_x15"):
        report.update(big_case(wait_until, pump, main_world, settle_end))
    else:
        report["error"] = f"unknown case {case!r}"
    report["webengine_flags_offline"] = all(flag in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
                                            for flag in snapshots.OFFLINE_FLAGS)
    report["protocol_caps"] = [protocol.MESSAGE_CAP, protocol.PICTURE_CAP]
    qInstallMessageHandler(None)
    return report


def memory_mb(pids: list[int]) -> dict[str, float]:
    """The private bytes and the working set of ``pids`` together, in MB (Windows; zeros elsewhere)."""
    if sys.platform != "win32" or not pids:
        return {"private": 0.0, "working_set": 0.0}
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
    private = working = 0
    for pid in pids:
        handle = kernel.OpenProcess(0x1000 | 0x0010, False, pid)   # query limited information, VM read
        if not handle:
            continue
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                private += counters.PrivateUsage
                working += counters.WorkingSetSize
        finally:
            kernel.CloseHandle(handle)
    return {"private": round(private / 2 ** 20, 1), "working_set": round(working / 2 ** 20, 1)}


_DESK_PAGE = r"""JSON.stringify((function () {
    var root = document.getElementById('board'), bar = document.getElementById('bar');
    var groups = Array.prototype.map.call(root.querySelectorAll('.group'), function (g) {
      return {time: g.querySelector('.g-time').textContent, topic: g.querySelector('.g-topic').textContent,
              older: g.classList.contains('older'),
              frames: Array.prototype.map.call(g.querySelectorAll('.frame'), function (f) {
                return f.querySelector('.f-title').textContent; }),
              waits: Array.prototype.map.call(g.querySelectorAll('.frame'), function (f) {
                return f.classList.contains('waits'); })};
    });
    var first = root.querySelector('.frame').getBoundingClientRect();
    var chip = root.querySelector('.frame.waits .f-wait');
    return {bar: bar ? bar.innerText.replace(/\s+/g, ' ').trim() : '', bar_display: bar ? getComputedStyle(bar).display : '',
            groups: groups, frame_left: first.left, frame_top: first.top, frame_width: first.width,
            board_top: root.getBoundingClientRect().top, desk_in: document.documentElement.classList.contains('desk-in'),
            chip: chip ? chip.innerText.replace(/\s+/g, ' ').trim() : '', scroll_height: root.scrollHeight,
            client_height: root.clientHeight};
})())"""


def desk_case(wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    """Part 5a's desk with the real engine, in Jarvis's own window (ui.BriefingWindow, frameless and translucent):
    the board made in the desk's board column, two answers' groups put up through board.feed (the mail thread and its
    waiting reply as ONE frame, first), the bar, the desk's enter; then what the page shows (the bar's words, the
    groups' headers and order, the frames' left edge), a grab of the window (the board's frames are in it), the first
    frame's time to the page's layout, reveal and a group leaving whole, Space with the focus in the page, and the
    renderer's memory."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest

    from briefing_reader import holo, ui
    from briefing_reader.board import feed as feed_mod
    from briefing_reader.board import view
    from tests.snapshot_probe import children

    report: dict[str, Any] = {}
    window = ui.BriefingWindow(10, 30)
    window.show_reading_view()
    window.resize(1120, 700)
    window.show()
    reading = window.reading
    reading.set_tab(ui.SCREEN_HOME)
    pump(0.3)
    own_before = memory_mb([os.getpid()])          # Jarvis's own process before the board's engine started
    board = view.BoardView(reading, start_delay_ms=0)
    layouts: list[tuple[float, list[str]]] = []
    board.layoutChanged.connect(lambda layout: layouts.append((time.monotonic(), [place[0] for place in
                                                                                 layout.places])))
    reading.set_board_available(True)
    reading.set_board_view(board)
    board.start()
    began = time.monotonic()
    wait_until(board.is_ready, 40)
    report["ready"] = board.is_ready()
    report["ready_s"] = round(time.monotonic() - began, 2)
    board.set_motion("off")
    built = dict(kind_frames(tiny_jpeg_url(320, 180)))
    feed = feed_mod.BoardFeed()

    def put(answer: int, name: str, when: str, merge: str = "", approvals: str = "", rank: float = 0.0) -> list:
        frame_id = feed.frame_id(answer, name, merge)
        messages = [dict(message, id=frame_id) for message in built[name].messages]
        return feed.put(answer, name, messages, when=when, merge=merge, approvals=approvals, rank=rank)

    for message in put(1, "todos", "9:20 AM"):
        board.apply(message)
    newest: list = []
    newest += put(2, "day", "9:41 AM", rank=0.1)
    newest += put(2, "mail", "9:41 AM", merge="mail:work:t1", rank=0.5)
    newest += put(2, "reply", "9:41 AM", merge="mail:work:t1", approvals="card-1", rank=0.5)
    newest += put(2, "research", "9:41 AM", rank=0.9)
    newest += put(2, "page", "9:41 AM", rank=0.95)
    sent_at = time.monotonic()
    first_id = next(message["id"] for message in newest if message.get("type") == "frame")
    report["applied"] = [board.apply(message) for message in newest]
    wait_until(lambda: any(at >= sent_at and first_id in ids for at, ids in layouts), 10)
    seen_at = next((at for at, ids in layouts if at >= sent_at and first_id in ids), None)
    report["first_frame_layout_ms"] = round((seen_at - sent_at) * 1000) if seen_at else -1
    reading.set_board_count(feed.frames())
    for message in feed.bar_again() + [{"type": "desk", "state": "enter"}]:
        board.apply(message)
    pump(1.5)
    report["desk_shown"] = reading.desk_shown()
    report["board_in_desk"] = board.parentWidget() is reading.desk_page()
    report["view_rect"] = list(board.geometry().getRect())
    report["page"] = json.loads(main_world(board, _DESK_PAGE))
    # A grab of the window (as the screen wipe, the card flight and the switch to the HUD take it) shows the board.
    image = window.grab().toImage()
    ratio = image.devicePixelRatio()
    origin = board.mapTo(window, QPoint(0, 0))
    page = report["page"]
    x = origin.x() + page["frame_left"] + 24
    y = origin.y() + page["frame_top"] + 6
    pixel = QColor(image.pixel(round(x * ratio), round(y * ratio)))
    report["grab"] = {"ratio": ratio, "size": [image.width(), image.height()], "at": [round(x), round(y)],
                      "color": pixel.name(), "tile_amber": QColor(holo.TILE_AMBER).name()}
    # Reveal: the board scrolls to the older group; then that group leaves whole.
    older_group = feed.group_for(1)
    board.apply({"type": "reveal", "group": older_group})
    pump(0.6)
    report["scroll_after_reveal"] = main_world(board, "document.getElementById('board').scrollTop")
    removed = feed._drop_group(feed._groups[older_group])     # noqa: SLF001 - the probe makes the room by hand
    for message in removed:
        board.apply(message)
    pump(0.5)
    report["groups_after_remove"] = main_world(board, "document.querySelectorAll('#board .group').length")
    report["known_after_remove"] = len(board.known_ids())
    # The page checks the new messages again: hostile ones put straight into board.apply change nothing.
    known = sorted(board.known_ids())[0]
    hostile = [
        {"type": "group", "id": "g9", "time": "9:41 AM", "topic": "y" * 41, "frames": [], "older": False},
        {"type": "group", "id": "<b>", "time": "9:41 AM", "topic": "y", "frames": [], "older": False},
        {"type": "group", "id": "g9", "time": "9:41 AM", "topic": "y", "frames": ["nope"], "older": False},
        {"type": "group", "id": "g9", "time": "9:41 AM", "topic": "y", "frames": [], "older": "yes"},
        {"type": "bar", "frames": 3, "waits": 4, "pinned": 0, "compact": False},
        {"type": "bar", "frames": 30, "waits": 0, "pinned": 0, "compact": False},
        {"type": "desk", "state": "open"},
        {"type": "reveal", "group": "g77"},
        {"type": "frame", "id": known, "kind": "text", "title": "x", "meta": "", "body": {"text": "x"},
         "status": "ok", "status_text": "", "status_meta": "", "waits": "", "changed": False, "pinned": False,
         "closed": False, "group": "g77"},
    ]
    report["page_refused"] = json.loads(main_world(board, "JSON.stringify(%s.map(function (m) { "
                                                          "return board.apply(JSON.stringify(m)); }))"
                                                          % json.dumps(hostile)))
    report["groups_after_hostile"] = main_world(board, "document.querySelectorAll('#board .group').length")
    # Space with the focus in the page never plays the briefing (ui._keeps_space covers the board's view).
    plays: list[int] = []
    reading.playPause.connect(lambda: plays.append(1))
    window.activateWindow()
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    pump(0.3)
    focus = window.focusWidget()
    report["space_focus_in_board"] = focus is not None and view.keeps_space(focus)
    if focus is not None:
        QTest.keyClick(focus, Qt.Key.Key_Space)
    pump(0.3)
    report["space_plays"] = len(plays)
    pids = children()
    report["renderers"] = len(pids)
    report["memory_mb"] = memory_mb(pids)
    own_after = memory_mb([os.getpid()])
    report["own_process_mb"] = {"private_before": own_before["private"], "private_after": own_after["private"]}
    report["counts"] = {key: value for key, value in board.counts().items() if isinstance(value, int)}
    window.hide()
    settle_end(report, board)
    return report


_WAYS_PAGE = r"""JSON.stringify((function () {
  function words(node) { return node ? node.innerText.replace(/\s+/g, ' ').trim() : ''; }
  function frameOf(node) { return node && node.closest ? node.closest('.frame') : null; }
  var root = document.getElementById('board'), box = root.getBoundingClientRect();
  var list = document.querySelector('#strip .s-list'), brace = list.querySelector('.ix-brace');
  var strip = Array.prototype.filter.call(list.children, function (n) { return n !== brace; }).map(function (n) {
    var pip = n.querySelector('.ix-pip');
    return {cls: n.className, label: n.getAttribute('aria-label') || '', top: n.offsetTop, height: n.offsetHeight,
            pip: pip ? getComputedStyle(pip).display : '', tab: n.tabIndex, border: getComputedStyle(n).borderTopStyle};
  });
  var frames = Array.prototype.map.call(root.querySelectorAll('.frame'), function (f) {
    var r = f.getBoundingClientRect();
    return {id: f.querySelector('.f-title').id.slice(6), cls: f.className, top: r.top, bottom: r.bottom,
            height: r.height, left: r.left, width: r.width, shown: getComputedStyle(f).display !== 'none',
            fact: f.querySelector('.s-words').textContent,
            note: words([f.querySelector('.f-note'), f.querySelector('.s-note')].filter(function (n) {
              return n.offsetParent !== null; })[0]),
            closed: words(f.querySelector('.f-closed')), offsetTop: f.offsetTop};
  });
  var active = document.activeElement, row = document.querySelector('.cleared-row');
  var style = getComputedStyle(root);
  return {strip: strip, brace: brace.classList.contains('shown') ? {top: brace.offsetTop, height: brace.offsetHeight} : null,
          frames: frames, more: document.getElementById('more').classList.contains('shown') ? words(document.getElementById('more')) : '',
          box: {top: box.top, bottom: box.bottom, height: box.height}, scrollTop: root.scrollTop,
          scrollHeight: root.scrollHeight, clientHeight: root.clientHeight, pairs: root.classList.contains('pairs'),
          mask: style.webkitMaskImage || style.maskImage,
          pill: getComputedStyle(document.getElementById('pill')).display, pillText: words(document.getElementById('pill')),
          cleared: row.classList.contains('shown') ? words(row) : '',
          active: active && active !== document.body ? active.className : '',
          activeId: frameOf(active) ? frameOf(active).querySelector('.f-title').id.slice(6) : '',
          activeIx: active && active.classList && active.classList.contains('ix') ? active.getAttribute('aria-label') : '',
          groups: Array.prototype.map.call(root.querySelectorAll('.group'), function (g) {
            return {cls: g.className, display: getComputedStyle(g).display, time: g.querySelector('.g-time').textContent}; }),
          bar: words(document.getElementById('bar')), clearDisabled: document.querySelector('#bar .b-clear').disabled,
          pinnedDisabled: document.querySelector('#bar .b-seg button + button').disabled,
          counts: board.counts()};
})())"""


def ways_case(wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    """Part 5b's board ways with the real page and the app's own controller (see the module doc)."""
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from briefing_reader import desk_ui, holo, ui
    from briefing_reader.board import view
    from tests.ui_fakes import B_TODO, AppHarness

    class Shim:
        """What AppHarness asks of a test: addCleanup."""

        def __init__(self) -> None:
            self.cleanups: list = []

        def addCleanup(self, fn: Any, *args: Any) -> None:  # noqa: N802 - unittest's name
            self.cleanups.append((fn, args))

        def close(self) -> None:
            for fn, args in reversed(self.cleanups):
                try:
                    fn(*args)
                except Exception:  # noqa: BLE001
                    pass

    report: dict[str, Any] = {}
    desk_ui.DeskController.CLOSE_S = 1.5          # the 8 s made short here (the unit tests hold the 8 s)
    desk_ui.DeskController.CLEAR_S = 1.5
    desk_ui.DeskController.QUIET_S = 1.0
    holo.set_motion_level("off")
    boards: list = []

    def factory(parent: Any) -> Any:
        made = view.BoardView(parent, start_delay_ms=0)
        boards.append(made)
        return made

    shim = Shim()
    harness = AppHarness(shim, lines=(B_TODO,), board_factory=factory)
    harness.reading(autoplay=False)
    c = harness.c
    window, reading = c.window, c.window.reading
    window.resize(1120, 700)
    reading.set_tab(ui.SCREEN_HOME)
    window.show()
    window.activateWindow()
    pump(0.3)
    desk = c.look.desk
    built = dict(kind_frames(tiny_jpeg_url(320, 180)))

    def answer(key: int, names: list[tuple[str, str]], settle: float = 0.4) -> list[str]:
        """An answer shown on Home (the controller's own steps): (source, kind_frames name) each; its frame ids."""
        c._you(f"Question {key}?")
        desk.on_answer_started(key, f"Question {key}?")
        c._jarvis(f"Answer {key}, sir.")
        answered = desk._answers[key]
        answered.done = True
        answered.say = f"Answer {key}, sir."
        desk.ensure_board()
        park = desk._reading_now()
        if not park and desk.feed.parked_groups():
            desk._land()
        messages: list = []
        ids: list[str] = []
        for rank, (source, name) in enumerate(names):
            merge = "mail:work:t1" if name in ("mail", "reply") else ""
            frame_id = desk.feed.frame_id(key, source, merge)
            ids.append(frame_id)
            messages += desk.feed.put(key, source, [dict(message, id=frame_id) for message in built[name].messages],
                                      when=f"9:{key:02d} AM", merge=merge, approvals="card-1" if name == "reply" else "",
                                      rank=float(rank), park=park)
        desk._send(messages)
        desk.feed.set_turn(key, desk._answer_entry())
        desk._sync_counts()
        if desk.feed.parked_groups():
            desk._schedule_landing()
        pump(settle)
        return list(dict.fromkeys(ids))

    oldest = answer(5, [("research", "research"), ("page", "page")])
    middle = answer(20, [("todos", "todos"), ("followups", "followups"), ("search", "search")])
    newest = answer(41, [("mail", "mail"), ("reply", "reply"), ("day", "day"), ("week", "week")])
    board = boards[0]
    wait_until(board.is_ready, 40)
    pump(1.5)
    report["ready"] = board.is_ready()
    report["ids"] = {"oldest": oldest, "middle": middle, "newest": newest}
    report["feed_order"] = desk.feed.frame_ids()
    report["facts"] = {frame_id: desk.feed.frame_message(frame_id)["fact"] for frame_id in desk.feed.frame_ids()}

    def page() -> dict:
        return json.loads(main_world(board, _WAYS_PAGE))

    def proxy() -> Any:
        return board.web_view().focusProxy()

    def rect(script: str) -> dict:
        return json.loads(main_world(board, "JSON.stringify((function () { var r = (%s).getBoundingClientRect(); "
                                            "return {left: r.left, top: r.top, width: r.width, height: r.height}; "
                                            "})())" % script))

    def click(box: dict) -> None:
        QTest.mouseClick(proxy(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(int(box["left"] + box["width"] / 2), int(box["top"] + box["height"] / 2)))
        pump(0.35)

    def key(code: Any, modifiers: Any = Qt.KeyboardModifier.NoModifier) -> None:
        target = QApplication.focusWidget() or window
        QTest.keyClick(target, code, modifiers)
        pump(0.3)

    def frame_js(frame_id: str) -> str:
        return f"document.getElementById('title-{frame_id}').closest('.frame')"

    def scroll(top: int) -> None:
        main_world(board, f"document.getElementById('board').scrollTop = {int(top)}; 1")
        pump(0.25)

    def pointer_leaves() -> None:
        """The pointer leaves the board (real clicks put it over the page; offscreen it never moves away itself)."""
        QApplication.sendEvent(proxy(), QEvent(QEvent.Type.Leave))
        pump(0.3)

    def nobody_reads() -> None:
        """No sign of reading left at all (the quiet 8 s made zero)."""
        pointer_leaves()
        desk._watch.reset()

    # ---- the strip, "N more below", the fades ------------------------------------------------------------------
    report["first"] = page()
    strip = [item for item in report["first"]["strip"] if "ix-rule" not in item["cls"]]
    # A real click on a strip button far down scrolls the board to its frame.
    target_index = len(strip) - 2
    click(rect(f"document.querySelectorAll('#strip .ix')[{target_index}]"))
    pump(0.3)
    report["after_strip_click"] = page()
    report["strip_click_index"] = target_index
    # "N more below" at its 16 px edge: a frame whose bottom is 17 px above the end is fully in view (in the 44 px
    # fade); 15 px above it, it is not.
    scroll(0)
    first = page()
    edge_frame = next(item for item in first["frames"] if item["shown"] and item["bottom"] > first["box"]["bottom"])
    base = edge_frame["offsetTop"] + edge_frame["height"] - first["box"]["height"]
    scroll(base + 17)
    report["edge_17"] = page()
    scroll(base + 15)
    report["edge_15"] = page()
    report["edge_frame"] = edge_frame["id"]
    scroll(100000)
    report["at_end"] = page()
    scroll(0)
    report["at_top"] = page()

    # ---- satellites -------------------------------------------------------------------------------------------------
    report["sats"] = [item for item in page()["frames"] if " sat" in f" {item['cls']}"]
    sat_click = middle[1]                                         # the follow-ups: a satellite
    main_world(board, f"{frame_js(sat_click)}.scrollIntoView({{block: 'center'}}); 1")
    pump(0.3)
    click(rect(f"{frame_js(sat_click)}.querySelector('.f-sat')"))
    report["sat_clicked"] = next(item for item in page()["frames"] if item["id"] == sat_click)
    sat_enter = oldest[0]
    main_world(board, f"{frame_js(sat_enter)}.querySelector('.f-sat').focus(); 1")
    pump(0.2)
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    wait_until(lambda: view.keeps_space(QApplication.focusWidget()), 5)
    main_world(board, f"{frame_js(sat_enter)}.querySelector('.f-sat').focus(); 1")
    pump(0.2)
    key(Qt.Key.Key_Return)
    report["sat_entered"] = next(item for item in page()["frames"] if item["id"] == sat_enter)

    # ---- parking: the focus in the page (its own report), a new answer parks; Show; then quiet ----------------------
    scroll(160)
    main_world(board, f"{frame_js(newest[1])}.focus({{preventScroll: true}}); 1")
    pump(0.4)
    report["reading_focus"] = desk._watch.focus                  # the page said: the focus is in it
    held = page()["scrollTop"]
    parked_ids = answer(43, [("search", "search")])
    report["parked"] = page()
    report["parked_held"] = held
    report["parked_groups"] = desk.feed.parked_groups()
    click(rect("document.querySelector('#pill .pill-btn')"))
    report["shown_new"] = page()
    report["parked_after_show"] = desk.feed.parked_groups()
    main_world(board, f"{frame_js(newest[0])}.focus({{preventScroll: true}}); 1")
    pump(0.3)
    answer(44, [("research", "research")])
    report["parked_again"] = desk.feed.parked_groups()
    pointer_leaves()                                             # the pointer goes, then the focus: quiet from now
    report["reading_pointer_left"] = desk._watch.pointer
    reading.focus_ask_bar()
    pump(0.3)
    report["reading_after_leaving"] = [desk._watch.focus, desk._watch.pointer]
    report["landed_before_quiet"] = desk.feed.parked_groups()
    pump(1.4)
    report["landed_after_quiet"] = desk.feed.parked_groups()
    # The room a new answer makes (lively motion: 220 ms; read while it runs).
    nobody_reads()
    board.set_motion("lively")
    pump(0.2)
    answer(45, [("week", "week")], settle=0.12)
    report["room"] = json.loads(main_world(board, "JSON.stringify(Array.prototype.concat.apply([], Array.prototype.map."
                                                  "call(document.querySelectorAll('#board .group'), function (g) { return "
                                                  "g.getAnimations().map(function (a) { return a.effect.getTiming()"
                                                  ".duration; }); })))"))
    pump(1.2)
    board.set_motion("off")
    pump(0.2)
    report["after_landings"] = page()

    # ---- pin (real P), a 7th says why for 4 s ----------------------------------------------------------------------
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    wait_until(lambda: view.keeps_space(QApplication.focusWidget()), 5)
    order = [item["id"] for item in page()["frames"] if item["shown"] and "closed" not in item["cls"]]
    pinned: list[str] = []
    for frame_id in order[:7]:                                  # six pins, and a 7th tried on a folded frame
        main_world(board, f"(function () {{ var f = {frame_js(frame_id)}; (f.classList.contains('sat') ? "
                          "f.querySelector('.f-sat') : f).focus(); })(); 1")
        pump(0.15)
        key(Qt.Key.Key_P)
        pinned.append(frame_id)
    main_world(board, f"{frame_js(order[7])}.querySelector('.f-sat').click(); 1")   # a 7th tried on a full one
    pump(0.3)
    main_world(board, f"{frame_js(order[7])}.focus(); 1")
    pump(0.15)
    key(Qt.Key.Key_P)
    report["pins"] = page()
    report["pins_feed"] = desk.feed.pinned()
    report["seventh"] = pinned[6]
    report["seventh_full"] = order[7]
    main_world(board, "document.activeElement.blur(); 1")
    pump(4.3)
    report["note_after_4s"] = next(item for item in page()["frames"] if item["id"] == pinned[6])
    for frame_id in pinned[:5]:                                  # the sixth stays pinned (an older answer's)
        main_world(board, f"{frame_js(frame_id)}.focus(); 1")
        pump(0.1)
        key(Qt.Key.Key_P)
    report["pinned_left"] = [frame_id for frame_id in desk.feed.frame_ids() if desk.feed.flags(frame_id)["pinned"]]

    # ---- close (real Delete), the row, then gone; Bring back -------------------------------------------------------
    nobody_reads()
    closable = next(item["id"] for item in page()["frames"]
                    if item["shown"] and "pinned" not in item["cls"] and " sat" not in f" {item['cls']}")
    main_world(board, f"{frame_js(closable)}.focus(); 1")
    pump(0.15)
    key(Qt.Key.Key_Delete)
    report["closing"] = next(item for item in page()["frames"] if item["id"] == closable)
    report["closing_flags"] = desk.feed.flags(closable)
    report["closing_active"] = page()["active"]
    pump(1.9)
    report["closed_gone"] = [item["id"] for item in page()["frames"]].count(closable)
    report["closed_gone_feed"] = desk.feed.flags(closable)
    keep = next(item["id"] for item in page()["frames"] if item["shown"] and "pinned" not in item["cls"]
                and " sat" not in f" {item['cls']}")
    main_world(board, f"{frame_js(keep)}.focus(); 1")
    pump(0.15)
    key(Qt.Key.Key_Delete)
    click(rect(f"{frame_js(keep)}.querySelector('.f-closed .f-act')"))
    pump(1.9)
    report["brought_back"] = next(item for item in page()["frames"] if item["id"] == keep)
    report["brought_back_flags"] = desk.feed.flags(keep)

    # ---- All | Pinned ----------------------------------------------------------------------------------------------------
    click(rect("document.querySelector('#bar .b-seg button + button')"))
    report["filter_pinned"] = page()
    click(rect("document.querySelector('#bar .b-seg button')"))
    report["filter_all"] = page()

    # ---- the keyboard: real F6, Shift+F6, the arrows, Home / End, Esc ---------------------------------------------
    nobody_reads()
    scroll(0)
    reading.focus_ask_bar()
    pump(0.2)
    keys: dict[str, Any] = {"start_ask": reading.command_bar.isAncestorOf(QApplication.focusWidget())}
    key(Qt.Key.Key_F6)
    keys["conversation"] = QApplication.focusWidget() is reading.desk_page().turns
    key(Qt.Key.Key_F6)
    keys["board_qt"] = view.keeps_space(QApplication.focusWidget())
    keys["board"] = page()["activeId"]
    key(Qt.Key.Key_Down)
    keys["down"] = page()["activeId"]
    key(Qt.Key.Key_Up)
    keys["up"] = page()["activeId"]
    key(Qt.Key.Key_End)
    keys["end"] = page()["activeId"]
    key(Qt.Key.Key_Home)
    keys["home"] = page()["activeId"]
    key(Qt.Key.Key_F6)
    keys["strip"] = page()["activeIx"]
    key(Qt.Key.Key_Down)
    keys["strip_down"] = page()["activeIx"]
    key(Qt.Key.Key_F6)
    keys["back_to_ask"] = reading.command_bar.isAncestorOf(QApplication.focusWidget())
    key(Qt.Key.Key_F6, Qt.KeyboardModifier.ShiftModifier)
    keys["shift_strip"] = page()["activeIx"]
    key(Qt.Key.Key_F6, Qt.KeyboardModifier.ShiftModifier)
    keys["shift_board"] = page()["activeId"]
    key(Qt.Key.Key_F6, Qt.KeyboardModifier.ShiftModifier)
    keys["shift_conversation"] = QApplication.focusWidget() is reading.desk_page().turns
    key(Qt.Key.Key_F6)
    key(Qt.Key.Key_Escape)
    keys["escape"] = reading.command_bar.isAncestorOf(QApplication.focusWidget())
    keys["order"] = [item["id"] for item in page()["frames"] if item["shown"] and "closed" not in item["cls"]]
    keys["cycle_keys_sent_on"] = board.counts().get("cycle_keys_sent_on", 0)
    report["keys"] = keys

    # ---- the new messages, hostile, straight into the page: refused, nothing changes -------------------------------
    known = sorted(board.known_ids())[0]
    hostile = [
        {"type": "cleared", "frames": ["nope"]},
        {"type": "cleared", "frames": [known, known]},
        {"type": "cleared", "frames": known},
        {"type": "cleared"},
        {"type": "focus_part", "part": "address bar"},
        {"type": "focus_part", "part": "board", "then": "x"},
        {"type": "group", "id": "g1", "time": "9:41 AM", "topic": "x", "frames": [], "older": False, "parked": "yes"},
        {"type": "frame", "id": known, "kind": "text", "title": "x", "meta": "", "body": {"text": "x"}, "status": "ok",
         "status_text": "", "status_meta": "", "waits": "", "changed": False, "pinned": False, "closed": False,
         "fact": "x" * 41},
        {"type": "frame", "id": known, "kind": "text", "title": "x", "meta": "", "body": {"text": "x"}, "status": "ok",
         "status_text": "", "status_meta": "", "waits": "", "changed": False, "pinned": False, "closed": False,
         "fact": 3},
    ]
    before_hostile = page()
    report["page_refused"] = json.loads(main_world(board, "JSON.stringify(%s.map(function (m) { "
                                                          "return board.apply(JSON.stringify(m)); }))"
                                                          % json.dumps(hostile)))
    after_hostile = page()
    report["hostile_changed_nothing"] = (
        [item["cls"] for item in before_hostile["frames"]] == [item["cls"] for item in after_hostile["frames"]]
        and before_hostile["cleared"] == after_hostile["cleared"])

    # ---- Clear and its Bring back, then gone ----------------------------------------------------------------------
    click(rect("document.querySelector('#bar .b-clear')"))
    report["cleared"] = page()
    report["cleared_count"] = reading.board_count()
    report["cleared_desk"] = reading.desk_shown()
    click(rect("document.querySelector('.cleared-row .f-act')"))
    report["restored"] = page()
    report["restored_count"] = reading.board_count()
    click(rect("document.querySelector('#bar .b-clear')"))
    pump(1.9)
    report["clear_gone"] = page()
    report["clear_gone_feed"] = desk.feed.frame_ids()

    # ---- pairs at 1232 x 640: Left / Right within a pair ---------------------------------------------------------
    window.resize(1232, 640)
    pump(0.6)
    nobody_reads()
    pair = answer(50, [("todos-a", "todos"), ("todos-b", "todos")])
    pump(0.5)
    report["pairs_page"] = page()
    report["pair_ids"] = pair
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    wait_until(lambda: view.keeps_space(QApplication.focusWidget()), 5)
    main_world(board, f"{frame_js(pair[0])}.focus(); 1")
    pump(0.2)
    key(Qt.Key.Key_Right)
    report["pair_right"] = page()["activeId"]
    key(Qt.Key.Key_Left)
    report["pair_left"] = page()["activeId"]
    key(Qt.Key.Key_Down)
    report["pair_down"] = page()["activeId"]

    # ---- nothing pinned, Clear: the desk folds back to the stage ----------------------------------------------------
    for frame_id in [frame_id for frame_id in desk.feed.frame_ids() if desk.feed.flags(frame_id)["pinned"]]:
        main_world(board, f"{frame_js(frame_id)}.focus(); 1")
        pump(0.1)
        key(Qt.Key.Key_P)
    click(rect("document.querySelector('#bar .b-clear')"))
    pump(0.4)
    # Part 5c: the desk stays for the Bring back row (the header's count says what is left: 0), then folds.
    report["last_clear_held"] = [reading.desk_shown(), reading.board_held(), reading.board_count()]
    pump(1.9)
    report["last_clear_desk"] = reading.desk_shown()
    report["last_clear_stage"] = reading.stage.isVisible()
    report["last_clear_count"] = reading.board_count()
    report["desk_counts"] = {key_: value for key_, value in desk.counts.items() if isinstance(value, int)}
    report["counts"] = {key_: value for key_, value in board.counts().items() if isinstance(value, int)}
    window.hide()
    settle_end(report, board)
    shim.close()
    return report


class _Shim:
    """What AppHarness asks of a test: addCleanup."""

    def __init__(self) -> None:
        self.cleanups: list = []

    def addCleanup(self, fn: Any, *args: Any) -> None:  # noqa: N802 - unittest's name
        self.cleanups.append((fn, args))

    def close(self) -> None:
        for fn, args in reversed(self.cleanups):
            try:
                fn(*args)
            except Exception:  # noqa: BLE001
                pass


SAY = "Three meetings today, sir. Dana sent the budget numbers, and a reply waits for your OK."

_LIT_PAGE = r"""JSON.stringify((function () {
  function box(n) { if (!n) { return null; } var r = n.getBoundingClientRect(); return {left: r.left, top: r.top, width: r.width, height: r.height}; }
  var root = document.getElementById('board'), lit = root.querySelector('.frame.lit'), stub = document.querySelector('.cue-stub');
  var frames = Array.prototype.map.call(root.querySelectorAll('.frame'), function (f) {
    var r = f.getBoundingClientRect(), tag = f.querySelector('.f-tag');
    return {id: f.querySelector('.f-title').id.slice(6), cls: f.className, top: r.top, left: r.left, width: r.width,
            height: r.height, ground: getComputedStyle(f).backgroundColor,
            outline: getComputedStyle(f.querySelector('.fx-outline rect')).stroke,
            tag: tag ? getComputedStyle(tag).display : ''};
  });
  return {lit: lit ? lit.querySelector('.f-title').id.slice(6) : '', has_lit: root.classList.contains('has-lit'),
          brackets: lit ? Array.prototype.map.call(lit.querySelectorAll('.fx-bracket'), function (b) {
            var s = getComputedStyle(b); return {color: s.borderTopColor === 'rgba(0, 0, 0, 0)' ? s.borderBottomColor : s.borderTopColor,
              opacity: s.opacity, box: box(b)}; }) : [],
          litBox: box(lit), stub: stub ? {cls: stub.className, line: box(stub.querySelector('.cs-line')),
            dot: box(stub.querySelector('.cs-dot')), color: getComputedStyle(stub.querySelector('.cs-line')).backgroundColor,
            visibility: getComputedStyle(stub).visibility} : null,
          stubs: document.querySelectorAll('.cue-stub').length, frames: frames, board: root.className,
          active: document.activeElement && document.activeElement.querySelector && document.activeElement.querySelector('.f-title')
            ? document.activeElement.querySelector('.f-title').id.slice(6) : '',
          counts: board.counts()};
})())"""


def _app_case(shim: Any, wait_until: Any, pump: Any, size: tuple[int, int]) -> tuple[Any, Any, list, Any]:
    """The whole app offscreen with the real board (tests.ui_fakes.AppHarness: nothing real reached), on Home."""
    from briefing_reader import ui
    from briefing_reader.board import view
    from tests.ui_fakes import B_TODO, AppHarness

    boards: list = []

    def factory(parent: Any) -> Any:
        made = view.BoardView(parent, start_delay_ms=0)
        boards.append(made)
        return made

    harness = AppHarness(shim, lines=(B_TODO,), board_factory=factory)
    harness.reading(autoplay=False)
    c = harness.c
    c.window.resize(*size)
    c.window.reading.set_tab(ui.SCREEN_HOME)
    c.window.show()
    c.window.activateWindow()
    pump(0.3)
    return harness, c, boards, c.look.desk


def _put_answer(c: Any, desk: Any, built: dict, key: int, names: list[tuple[str, str]], *, say: str = "",
                keys: dict | None = None, when: str = "") -> list[str]:
    """An answer shown on Home (the controller's own steps): (source, kind_frames name) each, with source keys."""
    c._you(f"Question {key}?")
    desk.on_answer_started(key, f"Question {key}?")
    c._jarvis(say or f"Answer {key}, sir.")
    answered = desk._answers[key]
    answered.done = True
    answered.say = say or f"Answer {key}, sir."
    desk.ensure_board()
    park = desk._reading_now()
    if not park and desk.feed.parked_groups():
        desk._land()
    messages: list = []
    ids: list[str] = []
    for rank, (source, name) in enumerate(names):
        merge = "mail:work:t1" if name in ("mail", "reply", "mail2") else ""
        source_key = (keys or {}).get(source, merge)
        frame_id = desk.feed.frame_id(key, source, merge, source_key)
        ids.append(frame_id)
        messages += desk.feed.put(key, source, [dict(message, id=frame_id) for message in built[name].messages],
                                  when=when or f"9:{key % 60:02d} AM", merge=merge,
                                  approvals="card-1" if name == "reply" else "", rank=float(rank), park=park,
                                  key=source_key)
    desk._send(messages)
    desk.feed.set_turn(key, desk._answer_entry())
    desk._sync_counts()
    return list(dict.fromkeys(ids))


def _speak(c: Any, text: str, sentence: int) -> int:
    """Jarvis says ``text`` and is in its ``sentence``-th sentence: his voice's timeline as the voice gives it."""
    from briefing_reader import voice_fx
    from briefing_reader.board import lit as lit_mod

    spans = lit_mod.sentence_spans(text)
    words, at_ms, position = [], 0, -1
    for index, (start, end) in enumerate(spans):
        for word, _a, _b in lit_mod.tokens(text[start:end]):
            words.append((at_ms, at_ms + 280, word))
            if index == sentence and position < 0:
                position = at_ms + 140
            at_ms += 300
    timeline = voice_fx.VoiceTimeline(words=tuple(words), duration_ms=at_ms)
    c._voice_timeline = lambda: timeline
    c._voice_position = lambda: position
    c._on_voice_speaking(text)
    return position


def lit_case(wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    """Part 5c's lit frame, cue stub, changed lap, three across and Clear with nothing pinned (see the module doc)."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from briefing_reader import desk_ui, holo
    from briefing_reader.board import view

    report: dict[str, Any] = {}
    desk_ui.DeskController.CLEAR_S = 1.5          # the 8 s made short here (the unit tests hold the 8 s)
    holo.set_motion_level("off")
    shim = _Shim()
    harness, c, boards, desk = _app_case(shim, wait_until, pump, (1120, 700))
    window, reading = c.window, c.window.reading
    jpeg = tiny_jpeg_url(320, 180)
    built = dict(kind_frames(jpeg))
    ids = _put_answer(c, desk, built, 41, [("mail", "mail"), ("reply", "reply"), ("day", "day"), ("todos", "todos")],
                      say=SAY, keys={"day": "day:2026-11-17", "todos": "todos"})
    board = boards[0]
    wait_until(board.is_ready, 40)
    pump(1.2)
    report["ready"] = board.is_ready()
    report["ids"] = ids

    def page() -> dict:
        return json.loads(main_world(board, _LIT_PAGE))

    def frame_js(frame_id: str) -> str:
        return f"document.getElementById('title-{frame_id}').closest('.frame')"

    def native() -> dict:
        path = desk.cue.path()
        line = reading.desk_page().cue_line
        top = board.mapTo(reading.desk_page(), QPoint(0, 0)).y()
        return {"lit": desk.cue.lit(), "glow": reading.glow_share(),
                "rings": [ring.glow_share() for ring in (reading.hero, reading.dock.mini_orb(), reading.briefing_orb,
                                                         reading.plan_stage.orb, reading.desk_page().head.orb)],
                "path": None if path is None else {"start": [path.start.x(), path.start.y()], "seam_x": path.seam_x,
                                                   "end_y": path.end_y, "amber": path.amber},
                "shown": line.shown(), "line_right": line.geometry().right(), "view_left": board.geometry().left(),
                "seam": reading.desk_page().geometry_now().seam.x(), "board_top": top}

    # ---- the second sentence names the mail (Dana, numbers): it is lit, amber ----------------------------------------
    _speak(c, SAY, 1)
    pump(0.6)
    report["amber_page"] = page()
    report["amber_native"] = native()
    # ---- the board scrolls: the stub hides (and the native part), then both are back -------------------------------
    main_world(board, "document.getElementById('board').scrollTop = 60; 1")
    pump(0.06)
    report["scrolling_page"] = page()
    report["scrolling_native"] = native()
    pump(0.6)
    report["after_scroll_page"] = page()
    report["after_scroll_native"] = native()
    main_world(board, "document.getElementById('board').scrollTop = 0; 1")
    pump(0.6)
    # ---- the first sentence names today's calendar: it is lit, in cyan -----------------------------------------------
    _speak(c, SAY + " ", 0)               # a new utterance (the same words): the first sentence
    pump(0.6)
    report["cyan_page"] = page()
    report["cyan_native"] = native()
    # ---- part 5c fixes: the frame above the lit one closes and comes back (lively: its 200 ms height animation), then
    # a picture loads after the layout went out (off) - the app's layout follows where the frames end up each time ----
    layouts: list = []
    board.layoutChanged.connect(layouts.append)

    def last_layout() -> dict:
        return {frame_id: [top, height] for frame_id, top, height in layouts[-1].places} if layouts else {}

    holo.set_motion_level("lively")
    board.set_motion("lively")
    pump(0.2)
    main_world(board, f"{frame_js(ids[0])}.querySelector('.f-ctl button:last-child').click(); 1")
    pump(0.7)
    report["closed_above"] = {"page": page(), "native": native(), "layout": last_layout()}
    main_world(board, f"{frame_js(ids[0])}.querySelector('.f-closed .f-act').click(); 1")
    pump(0.7)
    report["back_above"] = {"page": page(), "native": native(), "layout": last_layout()}
    holo.set_motion_level("off")
    board.set_motion("off")
    pump(0.2)
    pictured = _put_answer(c, desk, built, 47, [("pic-page", "page"), ("pic-search", "search")])
    pump(1.0)
    main_world(board, "window.__loaded = 0; new MutationObserver(function (records) { records.forEach(function (r) { "
                      "r.addedNodes.forEach(function (n) { if (n.tagName === 'IMG') { n.addEventListener('load', "
                      "function () { window.__loaded += 1; }); } }); }); }).observe(document.getElementById('board'), "
                      "{childList: true, subtree: true}); 1")
    board.apply({"type": "picture", "id": pictured[0], "src": tiny_jpeg_url(320, 400)})   # a taller picture
    wait_until(lambda: main_world(board, "window.__loaded") >= 1, 5)
    loaded = main_world(board, "window.__loaded")
    pump(0.6)
    # Its size as a load that comes after the layout went out gives it (here at once: its height changed, then its
    # load event): the frames below move, and only the picture's own load can say so.
    main_world(board, f"(function () {{ var picture = {frame_js(pictured[0])}.querySelector('.f-pic'); "
                      "picture.style.height = '160px'; picture.dispatchEvent(new Event('load')); return 1; })()")
    pump(0.5)
    report["picture_load"] = {"page": page(), "layout": last_layout(), "loaded": loaded}
    # ---- an answer arrives while a frame is lit: it traces in LINE_FOCUS (lively motion) ---------------------------
    holo.set_motion_level("lively")
    board.set_motion("lively")
    pump(0.2)
    arriving = _put_answer(c, desk, built, 42, [("research", "research")])
    pump(0.15)
    report["arriving_page"] = page()
    report["arriving_id"] = arriving[0]
    report["line_focus"] = holo.LINE_FOCUS.name() if hasattr(holo.LINE_FOCUS, "name") else str(holo.LINE_FOCUS)
    pump(1.2)
    # ---- the speech ends: nothing lit, the glow back, no stub ---------------------------------------------------------
    c._on_voice_speaking("")
    pump(0.6)
    report["ended_page"] = page()
    report["ended_native"] = native()
    # ---- changed: a later answer builds the mail again from its thread with other words (lively: the lap) --------
    nobody = desk._watch
    nobody.reset()
    sent_before = board.counts().get("sent:frame", 0)
    changed_mail = dict(built["mail"].messages[0])
    changed_mail["body"] = dict(changed_mail["body"], text="Line 14 is now 3,400 (it was 2,900 on Friday).")
    from briefing_reader.board.frames import Built

    built["mail2"] = Built((changed_mail,), 0)
    _put_answer(c, desk, built, 43, [("mail-again", "mail2")], keys={"mail-again": "mail:work:t1"})
    pump(0.3)
    report["lap_page"] = page()
    pump(1.2)
    report["after_lap_page"] = page()
    report["changed_frames"] = board.counts().get("sent:frame", 0) - sent_before
    sent_again = board.counts().get("sent:frame", 0)
    _put_answer(c, desk, built, 44, [("mail-third", "mail2")], keys={"mail-third": "mail:work:t1"})
    pump(0.3)
    report["same_again_sent"] = board.counts().get("sent:frame", 0) - sent_again
    report["feed_counts"] = desk.feed.counts()
    holo.set_motion_level("off")
    board.set_motion("off")
    pump(0.2)
    # ---- three across at 1500 x 960, pairs at 1480 x 960 ------------------------------------------------------------
    window.resize(1500, 960)
    pump(0.6)
    three = _put_answer(c, desk, built, 45, [("todo-a", "todos"), ("todo-b", "todos"), ("todo-c", "todos")],
                        keys={"todo-a": "", "todo-b": "", "todo-c": ""})
    pump(0.8)
    report["three_ids"] = three
    report["three_page"] = page()
    report["three_view"] = list(board.geometry().getRect())
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    wait_until(lambda: view.keeps_space(QApplication.focusWidget()), 5)
    main_world(board, f"{frame_js(three[0])}.focus(); 1")
    pump(0.2)
    keys: dict[str, str] = {}
    for name, code in (("right", Qt.Key.Key_Right), ("right2", Qt.Key.Key_Right), ("left", Qt.Key.Key_Left),
                       ("down", Qt.Key.Key_Down)):
        QTest.keyClick(QApplication.focusWidget() or window, code)
        pump(0.25)
        keys[name] = page()["active"]
    report["three_keys"] = keys
    window.resize(1480, 960)
    pump(0.8)
    report["pairs_page"] = page()
    report["pairs_view"] = list(board.geometry().getRect())
    window.resize(1120, 700)
    pump(0.6)
    # ---- the new messages, hostile, straight into the page while it holds frames (part 5c fixes: a frame it has, so a
    # check taken out lets a message through - the control opens and closes big for it): refused, and why -----------
    from briefing_reader.board import protocol

    known = desk.feed.frame_ids()[0]
    box = {"x": 357, "y": 12, "w": 503, "h": 626}
    prefix = protocol.PICTURE_PREFIX
    hostile = [
        {"type": "lit", "id": known, "cue": "yes"},
        {"type": "lit", "id": known, "cue": True, "x": 1},
        {"type": "backdrop", "src": "data:image/png;base64,iVBORw0KGgo="},
        {"type": "backdrop", "src": "https://example.com/x.jpg"},
        {"type": "backdrop", "src": jpeg, "id": known},
        {"type": "backdrop", "src": prefix + "!!!!"},
        {"type": "backdrop", "src": prefix},
        {"type": "backdrop", "src": prefix + "A" * (protocol.PICTURE_CAP - len(prefix) + 4)},
        {"type": "big", "state": "open", "id": known, **box, "x": -1},
        {"type": "big", "state": "open", "id": known, **box, "x": "1"},
        {"type": "big", "state": "open", "id": known, **box, "x": 1.5},
        {"type": "big", "state": "open", "id": known, **box, "y": -1},
        {"type": "big", "state": "open", "id": known, **box, "w": 0},
        {"type": "big", "state": "open", "id": known, **box, "w": protocol.BIG_LIMIT + 1},
        {"type": "big", "state": "open", "id": "<b>", **box},
        {"type": "big", "state": "close", "id": "", "x": 0},
        {"type": "big", "state": "half", "id": ""},                  # (an id: only the state check refuses it)
        # Part 5c fixes, round 2: an item only each of the page's other checks refuses (lockdown 0) - a big open wrong
        # only in h, one key too many, a well-formed id the page has no frame for; a close with a bad id; a lit with one.
        {"type": "big", "state": "open", "id": known, **box, "h": 0},
        {"type": "big", "state": "open", "id": known, **box, "z": 1},
        {"type": "big", "state": "open", "id": "f999", **box},
        {"type": "big", "state": "close", "id": 5},
        {"type": "big", "state": "close", "id": "<b>"},
        {"type": "lit", "id": "<b>"},
        {"type": "lit", "id": 5},
    ]
    report["hostile_unknown_absent"] = ("f999" not in desk.feed.frame_ids()
                                        and main_world(board, "!document.getElementById('title-f999')"))
    report["hostile_known"] = main_world(board, f"!!document.getElementById('title-{known}')")
    before = json.loads(main_world(board, "JSON.stringify(board.counts())"))
    report["page_refused"] = json.loads(main_world(board, "JSON.stringify(%s.map(function (m) { "
                                                          "return board.apply(JSON.stringify(m)); }))"
                                                          % json.dumps(hostile)))
    report["big_after_hostile"] = main_world(board, "board.counts().big")
    after = json.loads(main_world(board, "JSON.stringify(board.counts())"))
    report["hostile_drops"] = {why: after.get(why, 0) - before.get(why, 0) for why in ("shape", "picture", "id", "size")}
    report["control"] = json.loads(main_world(board, "JSON.stringify([%s, %s].map(function (m) { "
                                                     "return board.apply(JSON.stringify(m)); }))"
                                                     % (json.dumps({"type": "big", "state": "open", "id": known, **box}),
                                                        json.dumps({"type": "big", "state": "close", "id": ""}))))
    pump(0.3)
    report["big_after_control"] = main_world(board, "board.counts().big")
    # ---- Clear with nothing pinned: the desk stays for its Bring back row, then folds --------------------------------
    for frame_id in desk.feed.frame_ids():
        if desk.feed.flags(frame_id).get("pinned"):
            desk.feed.set_pinned(frame_id, False)
    desk._watch.reset()
    desk._on_clear()
    pump(0.4)
    report["cleared_desk"] = reading.desk_shown()
    report["cleared_held"] = reading.board_held()
    report["cleared_count"] = reading.board_count()
    report["cleared_row"] = main_world(board, "(function () { var r = document.querySelector('.cleared-row'); "
                                              "return r.classList.contains('shown') ? r.innerText.replace(/\\s+/g, ' ')"
                                              ".trim() : ''; })()")
    report["cleared_bar"] = main_world(board, "document.getElementById('bar').innerText.replace(/\\s+/g, ' ').trim()")
    report["cleared_ring_head"] = reading.desk_page().head.isVisible()
    pump(1.9)
    report["after_clear_desk"] = reading.desk_shown()
    report["after_clear_stage"] = reading.stage.isVisible()
    report["counts"] = {key_: value for key_, value in board.counts().items() if isinstance(value, int)}
    report["desk_counts"] = {key_: value for key_, value in desk.counts.items() if isinstance(value, int)}
    report["cue_counts"] = dict(desk.cue.counts)
    window.hide()
    settle_end(report, board)
    shim.close()
    return report


def _double_click(target: Any, point: Any) -> None:
    """A real double click at ``point``: two presses and releases 40 ms apart with their own timestamps, as a mouse
    gives them (Chromium makes its dblclick from the pair; QTest's double-click event reaches it as one press, and its
    separate clicks are kept a double-click interval apart on purpose)."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    local = QPointF(point)
    global_point = QPointF(target.mapToGlobal(point))
    base = int(time.monotonic() * 1000)
    for index, (kind, buttons) in enumerate(((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                                             (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
                                             (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                                             (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton))):
        event = QMouseEvent(kind, local, global_point, Qt.MouseButton.LeftButton, buttons,
                            Qt.KeyboardModifier.NoModifier)
        event.setTimestamp(base + index * 40)
        QApplication.sendEvent(target, event)


_BIG_PAGE = r"""JSON.stringify((function () {
  function box(n) { if (!n) { return null; } var r = n.getBoundingClientRect(); return {left: r.left, top: r.top, width: r.width, height: r.height}; }
  var card = document.querySelector('#big .big-card'), back = document.getElementById('backdrop');
  var images = Array.prototype.map.call(document.images, function (i) {
    return {id: i.id, src: i.src.slice(0, 23), inBoard: !!i.closest('#board'), inCard: !!i.closest('#big')}; });
  return {card: box(card), cardMeta: card ? card.querySelector('.f-meta').textContent : '',
          cardFoot: card ? card.querySelector('.f-back').innerText : '', cardFocus: !!card && document.activeElement === card,
          transform: card ? getComputedStyle(card).transform : '',
          backdrop: back ? {display: getComputedStyle(back).display, box: box(back), src: back.src.slice(0, 23),
                            opacity: getComputedStyle(back).opacity, filter: getComputedStyle(back).filter} : null,
          board: box(document.getElementById('board')), boardOpacity: getComputedStyle(document.getElementById('board')).opacity,
          body: document.body.className, inner: [window.innerWidth, window.innerHeight], images: images,
          frame: box(document.querySelector('#board .frame')), counts: board.counts(),
          rootClass: document.getElementById('board').className,
          inert: ['board', 'bar', 'strip', 'pill'].map(function (id) { var n = document.getElementById(id); return !!n && n.inert; }),
          cardPicture: card && card.querySelector('.f-pic') ? card.querySelector('.f-pic').src.length : 0,
          active: (function (a) { return !a ? 'none' : a.closest && a.closest('#big') ? 'card'
            : a.closest && a.closest('.frame') ? 'frame:' + a.closest('.frame').querySelector('.f-title').id.slice(6)
            : a.tagName.toLowerCase(); })(document.activeElement),
          shapes: Array.prototype.map.call(document.querySelectorAll('#board .frame'), function (f) {
            return f.className + ' h=' + Math.round(f.getBoundingClientRect().height) + ' a=' + f.getAnimations().length
              + ' ' + Array.prototype.map.call(f.children, function (k) { return Math.round(k.getBoundingClientRect().height); }).join(','); })};
})())"""


# Part 5c fixes: hooks that press a key at the exact moment open big's steps cross (the page's own handlers take the key
# as a real one): "esc-at-open" (as the open's "laid" is made ready, the view wide), "enter-at-close" (as the close's
# "laid" is made ready, the view narrow again: Enter on the frame), "esc-at-grow" (the big frame just added, its
# backdrop not yet decoded) and, round 2, "enter-at-shut" (Enter after "shut", before the page lays out narrow).
_STEP_HOOKS = r"""(function (frameId) {
  var raf = window.requestAnimationFrame.bind(window);
  window.__hook = '';
  window.requestAnimationFrame = function (fn) {
    var wide = document.body.classList.contains('wide');
    if (window.__hook === 'esc-at-open' && wide) {
      window.__hook = '';
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    } else if (window.__hook === 'enter-at-close' && !wide) {
      window.__hook = '';
      document.getElementById('title-' + frameId).closest('.frame').dispatchEvent(
        new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    }
    return raf(fn);
  };
  new MutationObserver(function () {
    if (window.__hook === 'esc-at-grow' && document.getElementById('big')) {
      window.__hook = '';
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    }
  }).observe(document.body, {childList: true});
  // Part 5c fixes, round 2: "enter-at-shut" - Enter on the frame after the page posted "shut", as the app's narrow view
  // reaches it: in the page's own handling of that resize, just before its board is its own size again (the page drops
  // its "wide" class first).
  var toggle = DOMTokenList.prototype.toggle;
  DOMTokenList.prototype.toggle = function (name, force) {
    if (window.__hook === 'enter-at-shut' && name === 'wide' && force === false && this === document.body.classList
        && this.contains('wide')) {
      window.__hook = '';
      document.getElementById('title-' + frameId).closest('.frame').dispatchEvent(
        new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    }
    return toggle.apply(this, arguments);
  };
  return 1;
})(%s)"""

# Part 5c fixes, round 2: how the big frame is when it is added (its layer's opacity and pointer events, the board's dim,
# whether it has the focus): held until its growth starts, never the big frame at full size first.
_HELD_HOOK = r"""(function () {
  window.__held = [];
  new MutationObserver(function (records) {
    records.forEach(function (record) {
      Array.prototype.forEach.call(record.addedNodes, function (node) {
        if (node.id === 'big') {
          var style = getComputedStyle(node), card = node.querySelector('.big-card');
          window.__held.push([style.opacity, style.pointerEvents, document.body.classList.contains('dim'),
                              !!card && document.activeElement === card]);
        }
      });
    });
  }).observe(document.body, {childList: true});
  return 1;
})()"""

# Part 5c fixes, round 2: a test-only layout mark in the view's bottom-right corner, magenta while the page is laid out
# wide (open big) and cyan at its own size; a MutationObserver repaints it before the next frame, so every frame Qt can
# show carries the layout it was made for. (CSSOM: the page's policy allows it; no style sheet, no markup.)
_LAYOUT_MARK = r"""(function () {
  var mark = document.createElement('div');
  mark.id = 'probe-layout-mark';
  mark.style.position = 'fixed'; mark.style.right = '0px'; mark.style.bottom = '0px';
  mark.style.width = '8px'; mark.style.height = '8px'; mark.style.zIndex = '2147483647';
  mark.style.pointerEvents = 'none';
  function paint() { mark.style.background = document.body.classList.contains('wide') ? '#ff00ff' : '#00ffff'; }
  new MutationObserver(paint).observe(document.body, {attributes: true, attributeFilter: ['class']});
  paint();
  document.documentElement.appendChild(mark);
  return 1;
})()"""


def big_case(wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    """Part 5c's open big in Jarvis's own window with the app's controller (see the module doc)."""
    from PySide6.QtCore import QEvent, QObject, QPoint, Qt
    from PySide6.QtTest import QTest

    from briefing_reader import desk_big, holo

    report: dict[str, Any] = {}
    holo.set_motion_level("off")
    desk_big.VEIL_MS = 5000          # part 5c fixes: the still goes with the page's "laid", never its time limit, here
    shim = _Shim()
    harness, c, boards, desk = _app_case(shim, wait_until, pump, (1120, 700))
    window, reading = c.window, c.window.reading
    built = dict(kind_frames(tiny_jpeg_url(320, 180)))
    ids = _put_answer(c, desk, built, 51, [("page", "page"), ("day", "day"), ("mail", "mail")])
    board = boards[0]
    wait_until(board.is_ready, 40)
    pump(1.2)
    report["ready"] = board.is_ready()
    page_id = ids[0]
    report["page_id"] = page_id

    class Moves(QObject):
        """Every geometry change of the board's view (it is set once each way, never frame by frame)."""

        def __init__(self) -> None:
            super().__init__()
            self.events: list[list[Any]] = []

        def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 - Qt override
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
                self.events.append([event.type().name, list(board.geometry().getRect()),
                                    round(time.monotonic(), 3)])
            return False

    moves = Moves()
    board.installEventFilter(moves)
    order: list[list[Any]] = []
    board.bigStep.connect(lambda step: order.append(["page", step, round(time.monotonic(), 3)]))
    opening: list[list[Any]] = []    # each step from the double click on, the still after the app took it
    board.bigStep.connect(lambda step: opening.append([step, round(time.monotonic(), 3),
                                                       reading.desk_page().veil.shown()]))

    def page() -> dict:
        return json.loads(main_world(board, _BIG_PAGE))

    def frame_box(frame_id: str) -> dict:
        return json.loads(main_world(board, "JSON.stringify((function () { var r = document.getElementById('title-%s')"
                                            ".closest('.frame').getBoundingClientRect(); return {left: r.left, top: r.top,"
                                            " width: r.width, height: r.height}; })())" % frame_id))

    report["blocked_before"] = dict(board.counts().get("blocked", {}))
    report["view_before"] = list(board.geometry().getRect())
    main_world(board, _HELD_HOOK)                            # part 5c fixes, round 2: the big frame held as it is added
    geometry = reading.desk_page().geometry_now()
    report["big_rect"] = list(geometry.big.getRect())
    report["view_rect"] = list(geometry.view.getRect())
    # ---- a real double click on the page picture's frame opens it big (lively: the scale is sampled) ----------------
    holo.set_motion_level("lively")
    board.set_motion("lively")
    pump(0.2)
    start = frame_box(page_id)
    report["frame_start"] = start
    proxy = board.web_view().focusProxy()
    point = QPoint(int(start["left"] + start["width"] / 2), int(start["top"] + 30))
    opening.clear()
    _double_click(proxy, point)
    opened_at = time.monotonic()
    wait_until(lambda: desk.big.state() == "open", 5)
    report["veil_shown"] = reading.desk_page().veil.shown()
    wait_until(lambda: page()["card"] is not None, 5)
    pump(0.06)
    report["mid_open"] = page()
    pump(0.7)
    report["open"] = page()
    report["open_view"] = list(board.geometry().getRect())
    report["open_dim"] = round(reading.chrome_dim(), 3)
    report["veil_after"] = reading.desk_page().veil.shown()
    report["moves_open"] = list(moves.events)
    report["open_s"] = round(time.monotonic() - opened_at, 2)
    report["big_counts"] = dict(desk.big.counts)
    report["opened_at"] = round(opened_at, 3)
    report["open_steps"] = list(opening)                    # part 5c fixes: the page's own "laid" at the open
    report["held_at_add"] = json.loads(main_world(board, "JSON.stringify(window.__held[0] || null)"))
    # ---- part 5c fixes: the big frame's picture follows its frame's; Tab stays in the big frame, the board inert ----
    other = tiny_jpeg_url(240, 160)                          # (a size of its own: its data: URL is its own length)
    report["card_picture_before"] = page()["cardPicture"]
    board.apply({"type": "picture", "id": page_id, "src": other})
    pump(0.3)
    report["card_picture"] = page()["cardPicture"]
    report["card_picture_want"] = len(other)
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    pump(0.2)
    trail = []
    for key, modifiers in ((Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier),
                           (Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier),
                           (Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)):
        QTest.keyClick(board.web_view().focusProxy(), key, modifiers)
        pump(0.15)
        trail.append(page()["active"])
    report["tab_trail"] = trail
    report["inert_open"] = page()["inert"]
    report["big_after_tabs"] = desk.big.state()
    # Part 5c fixes, round 2: the keyboard comes into the view by Tab while big (QtWebEngine then moves the page's
    # focus to its first stop): it lands on the big frame, and Esc later gives it back to the frame (never <body>).
    reading.focus_ask_bar()
    pump(0.2)
    board.web_view().setFocus(Qt.FocusReason.TabFocusReason)
    pump(0.3)
    report["tab_in_active"] = page()["active"]
    # ---- a resize while big (1232 x 640, the Dock still labelled): it keeps spanning both columns, set once ----------
    moves.events.clear()
    window.resize(1232, 640)
    pump(0.8)
    report["resized_rect"] = list(reading.desk_page().geometry_now().big.getRect())
    report["resized_view"] = list(board.geometry().getRect())
    report["resized"] = page()
    report["moves_resized"] = list(moves.events)
    report["resized_state"] = desk.big.state()
    # ---- real Esc: the scale-down first, then the geometry set back once, then the dim lifts ------------------------
    moves.events.clear()
    order.clear()
    dims: list[list[Any]] = []
    board.bigStep.connect(lambda step: dims.append([step, round(reading.chrome_dim(), 3),
                                                     list(board.geometry().getRect())]))
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    pump(0.2)
    QTest.keyClick(board.web_view().focusProxy(), Qt.Key.Key_Escape)
    esc_at = time.monotonic()
    pump(0.08)
    report["mid_close"] = page()
    report["mid_close_dim"] = round(reading.chrome_dim(), 3)
    report["mid_close_view"] = list(board.geometry().getRect())
    wait_until(lambda: not desk.big.state(), 5)
    shut_at = time.monotonic()
    pump(0.6)
    report["closed"] = page()
    report["closed_view"] = list(board.geometry().getRect())
    report["closed_rect"] = list(reading.desk_page().geometry_now().view.getRect())
    report["closed_dim"] = round(reading.chrome_dim(), 3)
    report["moves_close"] = list(moves.events)
    report["order"] = list(order)
    report["dims_at_steps"] = dims
    report["layer_after_close"] = main_world(board, "!!document.getElementById('big')")
    report["hit_after_close"] = main_world(board, "(function () { var r = document.getElementById('title-%s')"
                                                  ".closest('.frame').getBoundingClientRect(); var n = document."
                                                  "elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
                                                  " var f = n && n.closest ? n.closest('.frame') : null; return f ? "
                                                  "f.querySelector('.f-title').id.slice(6) : ''; })()" % page_id)
    report["esc_at"] = round(esc_at, 3)
    report["shut_s"] = round(shut_at - esc_at, 3)
    report["blocked_after"] = dict(board.counts().get("blocked", {}))
    report["allowed"] = dict(board.counts().get("allowed", {}))
    # ---- 900 x 600 (the Dock folded, the compact column): open big spans both columns there too -------------------
    holo.set_motion_level("off")
    board.set_motion("off")
    window.resize(900, 600)
    pump(0.8)
    window.activateWindow()
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    pump(0.3)
    moves.events.clear()
    main_world(board, "window.__ev = []; ['mousedown', 'dblclick'].forEach(function (n) { document.addEventListener(n, "
                      "function (e) { window.__ev.push(n + ':' + e.detail); }, true); }); 1")
    small = frame_box(page_id)
    _double_click(board.web_view().focusProxy(), QPoint(int(small["left"] + small["width"] / 2), int(small["top"] + 30)))
    wait_until(lambda: desk.big.state() == "open", 5)
    report["small_events"] = main_world(board, "JSON.stringify(window.__ev)")
    report["small_box"] = small
    report["requests"] = {key_: value for key_, value in board.counts().items()
                          if key_.startswith(("accepted:", "dropped:", "why:"))}
    report["small_big_counts"] = dict(desk.big.counts)
    report["small_desk_shown"] = [reading.desk_shown(), reading.current_tab(), reading.isVisible(),
                                  desk.on_screen(), board.is_ready(), reading.desk_page().board() is board]
    pump(0.6)
    report["small_rect"] = list(reading.desk_page().geometry_now().big.getRect())
    report["small_view"] = list(board.geometry().getRect())
    report["small"] = page()
    report["moves_small"] = list(moves.events)
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    pump(0.2)
    QTest.keyClick(board.web_view().focusProxy(), Qt.Key.Key_Escape)
    wait_until(lambda: not desk.big.state(), 5)
    pump(0.5)
    report["small_closed_view"] = list(board.geometry().getRect())
    report["small_closed"] = page()
    # ---- Esc again with nothing big: the ask bar ---------------------------------------------------------------------
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    pump(0.2)
    QTest.keyClick(board.web_view().focusProxy(), Qt.Key.Key_Escape)
    pump(0.3)
    from PySide6.QtWidgets import QApplication

    report["esc_again_ask"] = reading.command_bar.isAncestorOf(QApplication.focusWidget())
    # ---- part 5c fixes: a "laid" only for the page's step still going on (open big's steps against each other) -------
    steps: list[str] = []
    board.bigStep.connect(steps.append)
    board.openRequested.connect(lambda _frame_id: steps.append("open"))
    main_world(board, _STEP_HOOKS % json.dumps(page_id))
    # (1) motion off: Esc as the open's "laid" is made ready (two frames before it would be sent) - it never comes
    steps.clear()
    main_world(board, "window.__hook = 'esc-at-open'; 1")
    desk._on_open(page_id)                                   # as the page's request
    wait_until(lambda: not desk.big.state() and steps.count("laid") >= 1, 5)
    pump(0.5)
    report["esc_at_open_steps"] = list(steps)
    report["esc_at_open_veil"] = reading.desk_page().veil.shown()
    # (2) motion off: Enter on a frame as the close's "laid" is made ready - only the new open's "laid" comes
    desk._on_open(page_id)
    wait_until(lambda: page()["card"] is not None, 5)
    pump(0.4)
    steps.clear()
    main_world(board, "window.__hook = 'enter-at-close'; 1")
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    pump(0.2)
    QTest.keyClick(board.web_view().focusProxy(), Qt.Key.Key_Escape)
    wait_until(lambda: "open" in steps and desk.big.state() == "open" and page()["card"] is not None, 5)
    pump(0.6)
    report["enter_at_close_steps"] = list(steps)
    report["enter_at_close_veil"] = reading.desk_page().veil.shown()
    report["enter_at_close_state"] = desk.big.state()
    QTest.keyClick(board.web_view().focusProxy(), Qt.Key.Key_Escape)
    wait_until(lambda: not desk.big.state(), 5)
    pump(0.5)
    # (3) lively: Esc while the backdrop decodes (before the frame starts growing) - it never grows while it closes
    holo.set_motion_level("lively")
    board.set_motion("lively")
    pump(0.2)
    main_world(board, f"document.getElementById('title-{page_id}').closest('.frame').focus(); 1")
    pump(0.1)
    steps.clear()
    main_world(board, "window.__hook = 'esc-at-grow'; 1")
    desk._on_open(page_id)
    wait_until(lambda: not desk.big.state() and steps.count("laid") >= 1, 5)
    pump(0.5)
    report["esc_at_grow_steps"] = list(steps)
    report["esc_at_grow_page"] = page()
    # ---- part 5c fixes, round 2: the still lifts only once Qt shows the view laid out at its new size. The page's
    # layout mark (magenta wide, cyan at its own size) is read from the window the moment the still is cleared ---------
    from PySide6.QtCore import QRect, QSize

    main_world(board, _LAYOUT_MARK)
    pump(0.2)
    veil = reading.desk_page().veil
    marks: list[list[str]] = []

    def clear_and_look() -> None:
        shown = veil.shown()
        type(veil).clear(veil)
        if shown:
            corner = reading.desk_page().mapTo(window, board.geometry().bottomRight() - QPoint(4, 4))
            marks.append([desk.big.state() or "closed",
                          window.grab(QRect(corner, QSize(1, 1))).toImage().pixelColor(0, 0).name()])

    veil.clear = clear_and_look
    for motion in ("lively", "off"):
        holo.set_motion_level(motion)
        board.set_motion(motion)
        pump(0.3)
        marks.clear()
        desk._on_open(page_id)
        wait_until(lambda: desk.big.state() == "open" and not veil.shown(), 5)
        pump(0.6)
        desk.big.close()
        wait_until(lambda: not desk.big.state() and not veil.shown(), 5)
        pump(0.4)
        report[f"lift_marks_{motion}"] = list(marks)
    del veil.clear
    # A "laid" that comes before Qt shows the wide layout (as one meant for another step would): the view's own check
    # keeps the still up.
    desk._on_open(page_id)
    desk.big.on_step("laid")
    report["stray_laid_veil"] = veil.shown()
    wait_until(lambda: desk.big.state() == "open" and not veil.shown(), 5)
    pump(0.6)
    desk.big.close()
    wait_until(lambda: not desk.big.state() and not veil.shown(), 5)
    pump(0.4)
    # (4) motion off: Enter on the frame after the page's "shut", just before it handles the narrow resize - the close's
    # "laid" is never sent for the open asked after it (only that open's own "laid" comes)
    holo.set_motion_level("off")
    board.set_motion("off")
    pump(0.2)
    desk._on_open(page_id)
    wait_until(lambda: page()["card"] is not None and not veil.shown(), 5)
    pump(0.4)
    ignored = desk.big.counts.get("laid:ignored", 0)
    steps.clear()
    main_world(board, "window.__hook = 'enter-at-shut'; 1")
    board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
    pump(0.2)
    QTest.keyClick(board.web_view().focusProxy(), Qt.Key.Key_Escape)
    wait_until(lambda: "open" in steps and desk.big.state() == "open" and page()["card"] is not None, 5)
    pump(0.8)
    report["enter_at_shut_steps"] = list(steps)
    report["enter_at_shut_veil"] = veil.shown()
    report["enter_at_shut_state"] = desk.big.state()
    report["enter_at_shut_ignored"] = desk.big.counts.get("laid:ignored", 0) - ignored
    QTest.keyClick(board.web_view().focusProxy(), Qt.Key.Key_Escape)
    wait_until(lambda: not desk.big.state() and not veil.shown(), 5)
    pump(0.5)
    # (5) lively: Esc as the open's "laid" is made ready (its growth just started, the still still up): the open's
    # still goes once Qt shows the wide layout, during the scale-down (so it is seen), then the close's
    holo.set_motion_level("lively")
    board.set_motion("lively")
    pump(0.2)
    veil.clear = clear_and_look
    marks.clear()
    steps.clear()
    main_world(board, "window.__hook = 'esc-at-open'; 1")
    desk._on_open(page_id)
    wait_until(lambda: not desk.big.state() and steps.count("laid") >= 1 and not veil.shown(), 5)
    pump(0.5)
    del veil.clear
    report["back_steps"] = list(steps)
    report["back_marks"] = list(marks)
    # ---- part 5c fixes, round 2: the big frame's group leaves for room (a full board, a new answer) while it is big:
    # the focus goes to the board, never to <body> --------------------------------------------------------------------
    holo.set_motion_level("lively")
    board.set_motion("lively")
    desk._watch.reset()
    other = _put_answer(c, desk, built, 52, [("todos-b", "todos")], keys={"todos-b": ""})
    pump(1.0)
    desk._on_open(page_id)
    wait_until(lambda: desk.big.state() == "open" and not veil.shown(), 5)
    pump(0.6)
    report["room_before_active"] = page()["active"]
    desk.feed.max_groups = 2                                 # the board is full: the oldest group leaves for the next
    desk._watch.reset()
    newest = _put_answer(c, desk, built, 53, [("todos-c", "todos")], keys={"todos-c": ""})
    wait_until(lambda: not desk.big.state() and not veil.shown(), 5)
    pump(0.6)
    report["room_active"] = page()["active"]
    report["room_ids"] = [other[0], newest[0]]
    report["room_left"] = page_id not in desk.feed.frame_ids()
    # ... and with nothing else on the board just then (both groups leave for the next answer, the big frame's last):
    # the next frame shown takes the keyboard
    desk._on_open(newest[0])
    wait_until(lambda: desk.big.state() == "open" and not veil.shown(), 5)
    pump(0.6)
    report["room_empty_before_active"] = page()["active"]
    desk.feed.max_groups = 1
    desk._watch.reset()
    last = _put_answer(c, desk, built, 54, [("todos-d", "todos")], keys={"todos-d": ""})
    wait_until(lambda: not desk.big.state() and not veil.shown(), 5)
    pump(0.6)
    report["room_empty_active"] = page()["active"]
    report["room_empty_ids"] = [newest[0], last[0]]
    report["room_empty_board"] = desk.feed.frame_ids()
    report["desk_counts"] = {key_: value for key_, value in desk.counts.items() if isinstance(value, int)}
    report["big_counts_end"] = dict(desk.big.counts)
    board.removeEventFilter(moves)
    window.hide()
    settle_end(report, board)
    shim.close()
    return report


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1] if len(sys.argv) > 1 else "")), flush=True)
