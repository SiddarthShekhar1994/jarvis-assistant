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


def tiny_jpeg_url() -> str:
    """A real JPEG the probe makes (as the app re-encodes a page picture), as a data: URL."""
    import base64

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(96, 54, QImage.Format.Format_RGB32)
    image.fill(QColor("#1e6091"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", 80)
    buffer.close()
    return "data:image/jpeg;base64," + base64.b64encode(bytes(data.data())).decode("ascii")


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
    else:
        report["error"] = f"unknown case {case!r}"
    report["webengine_flags_offline"] = all(flag in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
                                            for flag in snapshots.OFFLINE_FLAGS)
    report["protocol_caps"] = [protocol.MESSAGE_CAP, protocol.PICTURE_CAP]
    qInstallMessageHandler(None)
    return report


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1] if len(sys.argv) > 1 else "")), flush=True)
