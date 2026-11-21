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
               it until the engine ends: what is left after the board closes is that service only, never a renderer.
               Round 5: the devices are Chromium's own stand-ins (FAKE_MEDIA_DEVICES), so the permission is asked
               whatever this PC has plugged in, and its real microphone and camera are never even listed
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
               it (the scale-down, then the geometry set back once, then the dim lifts); big_x15 at 1.5x. Part 5c
               fixes, round 3: the quick routes (a step asked while the view still holds the other step's frame) read
               as each still lifts and for 150 ms after; the dims' light at each render against the big frame's own
               progress; what the still's check leaves running after each way it ends; the keyboard when the big
               frame's group leaves for room while it fills the view. Round 4: a group leaving for room after the
               owner's real click on a group's head left the focus on <body> (the board scrolled into an older
               group; the answer parked): the focus, the scroll and the page's reading report as they were, and the
               parked answer landing once quiet; lively and off
    expiry     part 5c fixes, round 5: a closed frame's own end with the app's controller, the page's expiry first (its
               8 s made short once) and the app's removal later (CLOSE_S made longer): real Delete on a group's last
               frame (remove_group) and on one that is not (remove), the row cut by the view's edge; the keyboard stays
               in the closed frame at the expiry (no reading or focus report, no scroll; Enter, P and Delete there do
               nothing), then a staying frame has it, never <body>; lively and off
    reduced    part 5c fixes, round 5: the page's own reduced motion (Chromium told reduced motion, as this PC's "Show
               animations" off would tell it): lively plays as off (K and --k 0)
    typed      part 6, Ask's typed frames: a model's answer (invented) with markup, scripts, javascript: and http:
               addresses and bidi characters in every string, through board.ask_frames and board.protocol into the
               real page - every element in a frame's body one the renderers make, no attribute there from data, no
               request, no script run; its words whole as text; the page's own refusals of hostile bodies put into
               board.apply directly (Infinity, depth 7, 65 nodes, an unknown op ...); maths' elements made from the
               tree (a fraction, a power, a root) and the page's reading of plain expressions; at 448, 300 and 270 px
               no word cut (the timeline in a row from 300 px, one node a row under it; maths' step names over their
               lines); opened big, a table's rows and a timeline's six nodes using the room. Part 6 fixes: a 12-row
               table with its change words and source and an 8-step maths frame opened big at the card sizes of a
               1120x700 and a 900x600 window - fitted (their sizes stepped down, never below the board's own) with
               nothing hidden, or scrolling with its cue (the cut edge's fade, a thin bar) and the real keys (Down,
               Page Down, Space, Shift+Space, Page Up, Up, End, Home); maths frames on the desk's board (a mean of
               five as a fraction paired with a chart at 253 px, 300, 270 and 448 px; seven-digit sums beside long
               step names, a long radicand, an indexed root): every element inside its frame's content box, and a
               line made too narrow for its fraction reading it inline (a/b), laid out again once wide again
    canvas     the canvas (PLAN-canvas part 1), the real page as Home at 1280 x 800: no canvas layer until the first
               orb message; the rings (seen at an angle, each ring at its own depth), the badge (flat, facing, the
               name in it) and the corner (a third of its size, bottom right); the moves between them lively (the
               turn's three keyframes: it overshoots; the trip's trail), calm (x1.6, two keyframes, no trail) and off
               (a cut, the turns still); each state's own sign (the lean and the level, the counter turn, the amber
               arc, the slash, the steel); the corners' words set as text - hostile ones among them - with nothing
               of them in any attribute and no element made of them; a real press of every button that shows posting
               its own word, forged words on a second channel dropped; hostile orb and canvas messages put into
               board.apply directly refused by the page; the board's frame in the right-hand part with the orb in
               its corner, Clear view asking for the board's own clear, and the board away when the desk leaves
    canvas_layout  the canvas, part 2 (frames across the screen): 1, 3, 6, 9, 12 and 24 frames at 900x600, 1280x800,
               1920x1080 and 2560x1440 - the same page resized, packing again - with nothing overlapping, nothing
               outside the frames' area, the orb's corner and the caption's strip free, every frame placed or folded
               to a tile and nothing scrolling; then each kind with long words (a 60-character title, 40-character
               cells, 8 bars, 12 rows, markup among them) in the mock's style: no word cut sideways, a body its place
               cuts saying so, and nothing of the data in any attribute
    canvas_ways  the canvas, part 2, at 1280x800: the task card (working beside the orb, a step on, Stop asking the
               app, done: it becomes its group's head), the arrival lively (one after another, the heading decoding,
               numbers counting up), calm (slower, no decode, no count-up) and off (at once), hover (the ticks and the
               controls), the arrows by where the frames are (real keys), open big growing from a grid cell, the
               Pinned filter, a close leaving a gap with Tidy packing again (the frames gliding, lively: a little
               past), Clear view's lines into the orb before the orb leaves its corner, Bring back, and hostile task
               and number messages refused
    canvas_fixes  the canvas, part 3's real-screen fixes at the owner's 1389x868 (the top row also at 900x600, 1280x800
               and 1920x1080): the top right row on ONE line with every button (a two-digit Approvals count, Tidy,
               Clear view, Pinned), the least used under More, its list over the frames and its items pressing their
               own buttons, the frames' area never moving as a button comes or goes; the wake badge's longest words
               whole; one, two and three frames using the screen (wider, then centred, the orb's corner free); the
               task card at the mock's size with two frames up, the frames never moving as it comes and goes
    canvas_resting  the canvas, part 3B's two resting frames ("waits for your OK" and Agents, as board.resting builds
               them) at 1389x868, 1920x1080 and 1280x800 beside the day and the to-dos: long words, many rows and
               markup shown whole as text, nothing of them in an attribute, nothing overlapping; the waiting frame
               amber with no chip; each frame's one button inside it, a real press posting its corner's own word
               (approvals, agents), hidden while that corner button is; open big keeping it the only control; the
               same frames with little in them; the resting group "Now" put under an answer ("below": the answer's
               frames keep their places); the corner's "Now" shown only while the app says so, a press posting "now";
               a day with nothing booked ("empty") only as tall as its words;
               hostile bodies put into board.apply directly refused by the page

Qt runs offscreen and Chromium's proxy is a dead address (snapshots.OFFLINE_FLAGS, as the board's engine always runs in a
test): nothing can reach the network whatever the page does. No window is shown on the real screen. Chromium's
prefers-reduced-motion follows Windows' "Show animations" setting, and the board page then plays every level as off: so
the probe's engine is told no reduced motion (NO_REDUCED_MOTION; the "reduced" case REDUCED_MOTION), and the page plays
the probe's own level (lively, calm, off) whatever this PC's setting. It goes in among Jarvis's own flags in this
process only (snapshots.CHROMIUM_FLAGS, as tests/snapshot_probe.py does for its control): the inherited value the app
judges (snapshots.engine_flags_problem) stays the dead proxy alone, and what the shipped app accepts is unchanged.
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
# Part 5c fixes, round 5: what the probe's engine is told of reduced motion (see the module doc).
NO_REDUCED_MOTION = "--force-prefers-no-reduced-motion"
REDUCED_MOTION = "--force-prefers-reduced-motion"
# ... and, in the media case, Chromium's own stand-in camera and microphone instead of this PC's (the same way in):
# Chromium asks for the permission only once it found a device of each kind asked for, so with this PC's microphone
# unplugged it refused with NotFoundError before asking anything, and the board's refusal never came to be shown.
FAKE_MEDIA_DEVICES = "--use-fake-device-for-media-stream"
# The page's own word on its motion: the media query board.js reads, board.css's --k, and one frame's draw-in.
MOTION_LOOK = r"""JSON.stringify((function (id) {
  var frame = document.getElementById('title-' + id).closest('.frame');
  return {reduce: matchMedia('(prefers-reduced-motion: reduce)').matches,
          k: getComputedStyle(document.documentElement).getPropertyValue('--k').trim(),
          level: document.documentElement.className.split(' ').filter(function (c) {
            return c.indexOf('motion-') === 0; }),
          classes: Array.from(frame.classList),
          trace: getComputedStyle(frame.querySelector('.fx-outline rect')).animationDuration};
})(%s))"""

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

    # Round 5: the page plays the probe's own motion level whatever this PC's "Show animations" says (see the module
    # doc) - read by chromium_flags at the engine's start, among Jarvis's own flags; the inherited value stays as it is.
    motion_flag = REDUCED_MOTION if case == "reduced" else NO_REDUCED_MOTION
    snapshots.CHROMIUM_FLAGS = (*snapshots.CHROMIUM_FLAGS, motion_flag)
    if case == "media":
        snapshots.CHROMIUM_FLAGS = (*snapshots.CHROMIUM_FLAGS, FAKE_MEDIA_DEVICES)
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
        report["lively_look"] = json.loads(main_world(board, MOTION_LOOK % json.dumps("m1")))   # round 5
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
        # The switches the engine started with, so the test can see the stand-in devices' switch in them.
        report["engine_flags"] = snapshots.flag_tokens(os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""))
        board.shutdown()
        pump(0.3)
        # A process that is on its way out can no longer say what it is ("unknown": its command line cannot be read
        # while it exits; the canvas's heavier page takes longer to go): wait for it to be gone too, within the same
        # bound. One that stays past it still fails the test.
        wait_until(lambda: not any("renderer" in kind or kind == "unknown" for kind in process_kinds(children())), 15)
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
    elif case == "expiry":
        report.update(expiry_case(wait_until, pump, main_world, settle_end))
    elif case == "typed":
        report.update(typed_case(make, wait_until, pump, main_world, settle_end))
    elif case == "canvas":
        report.update(canvas_case(make, wait_until, pump, main_world, settle_end))
    elif case == "canvas_layout":
        report.update(canvas_layout_case(make, wait_until, pump, main_world, settle_end))
    elif case == "canvas_ways":
        report.update(canvas_ways_case(make, wait_until, pump, main_world, settle_end))
    elif case == "canvas_fixes":
        report.update(canvas_fixes_case(make, wait_until, pump, main_world, settle_end))
    elif case == "canvas_resting":
        report.update(canvas_resting_case(make, wait_until, pump, main_world, settle_end))
    elif case == "reduced":
        # The page's own reduced motion, Chromium told reduced motion (REDUCED_MOTION): lively plays as off.
        host, board = make()
        host.show()
        wait_until(board.is_ready, 40)
        report["ready"] = board.is_ready()
        board.set_motion("lively")
        board.apply({"type": "frame", "id": "m1", "kind": "text", "title": "Frame m1", "meta": "Now",
                     "body": {"text": "A few words of text."}})
        report["lively"] = json.loads(main_world(board, MOTION_LOOK % json.dumps("m1")))
        settle_end(report, board)
    else:
        report["error"] = f"unknown case {case!r}"
    report["webengine_flags_offline"] = all(flag in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
                                            for flag in snapshots.OFFLINE_FLAGS)
    report["motion_flag"] = motion_flag
    report["motion_flag_in_engine"] = motion_flag in snapshots.flag_tokens(os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS",
                                                                                          ""))
    report["inherited_flags_judged"] = snapshots.engine_flags_problem(snapshot_engine._judged_environ())  # noqa: SLF001
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
    # Part 5c fixes, round 3: the desk's entrance runs lively (with motion off there is none: the desk at once) and is
    # dropped once it is over - what starts and ends on the board and the bar is kept.
    board.set_motion("lively")
    main_world(board, r"""(function () {
      window.__entrance = [];
      ['animationstart', 'animationend'].forEach(function (kind) {
        document.addEventListener(kind, function (event) {
          if (event.animationName === 'desk-in') { window.__entrance.push(kind.slice(9) + ':' + event.target.id); }
        }, true);
      });
      return 1; })()""")
    for message in feed.bar_again() + [{"type": "desk", "state": "enter"}]:
        board.apply(message)
    pump(1.5)
    board.set_motion("off")
    report["entrance"] = json.loads(main_world(board, "JSON.stringify(window.__entrance)"))
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

# Part 5c fixes, round 3 (stale-big 0): a second test-only mark beside it whose colour changes with each change of the
# page's layout (wide <-> its own size), so one wide entry is told from the next: a still lifted on the other step's
# frame, or a later frame of the other layout, shows another entry than the page's last.
_ENTRY_COLOURS = ["#203080", "#208030", "#802030", "#808020", "#208080", "#802080", "#c0c0c0", "#604020"]
_ENTRY_MARK = r"""(function () {
  if (document.getElementById('probe-entry-mark')) { return 2; }
  var mark = document.createElement('div');
  mark.id = 'probe-entry-mark';
  mark.style.position = 'fixed'; mark.style.right = '8px'; mark.style.bottom = '0px';
  mark.style.width = '8px'; mark.style.height = '8px'; mark.style.zIndex = '2147483647';
  mark.style.pointerEvents = 'none';
  var palette = PALETTE, wide = document.body.classList.contains('wide'), entries = 0;
  function paint() {
    var now = document.body.classList.contains('wide');
    if (now !== wide) { wide = now; entries += 1; }
    mark.style.background = palette[entries % palette.length];
  }
  new MutationObserver(paint).observe(document.body, {attributes: true, attributeFilter: ['class']});
  paint();
  document.documentElement.appendChild(mark);
  window.__entries = function () { return entries; };
  return 1;
})()""".replace("PALETTE", json.dumps(_ENTRY_COLOURS))

# Part 5c fixes, round 3 (item 7): test-only CSSOM rules - the big card's border magenta (its rectangle is found in the
# view's own pixels; nothing else changes), and the card hidden (the page under it at full light, then dimmed).
_MAGENTA_CARD = r"""(function () {
  var sheet = document.styleSheets[0];
  sheet.insertRule('.frame.big-card { border-color: #ff00ff !important; }', sheet.cssRules.length);
  return 1;
})()"""
_HIDE_CARD = r"""(function () {
  var sheet = document.styleSheets[0];
  window.__hideCard = sheet.insertRule('#big .big-card { visibility: hidden !important; }', sheet.cssRules.length);
  document.body.classList.remove('dim');
  return 1;
})()"""
_SHOW_CARD = "document.styleSheets[0].deleteRule(window.__hideCard); 1"
# What is left of an entrance on the elements open big dims (part 5c fixes, round 3: an animation left on them, even one
# that is over, keeps their dim from easing with the growth).
_ENTRANCE_LEFT = r"""JSON.stringify((function () {
  function left(id) { var n = document.getElementById(id); return n ? n.getAnimations().length : -1; }
  var pill = document.getElementById('pill');
  return {board: left('board'), bar: left('bar'), pill: left('pill'), desk_in: document.documentElement.classList.contains('desk-in'),
          pill_shown: !!pill && pill.classList.contains('shown'), pill_arriving: !!pill && pill.classList.contains('arriving')};
})())"""
_GROUND_LIGHT = 0.2126 * 5 + 0.7152 * 8 + 0.0722 * 12      # holo.VOID: what the window shows under the translucent view


def _view_pixels(view: Any, area: Any = None) -> Any:
    """The board view's own pixels, as desk_big.view_shows reads them (QWidget.render with its children into a clear
    image, never a window grab): the whole view, or ``area`` (logical px) drawn from the image's origin."""
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QImage, QRegion
    from PySide6.QtWidgets import QWidget

    area = area or QRect(0, 0, view.width(), view.height())
    ratio = view.devicePixelRatioF() or 1.0
    image = QImage(max(1, round(area.width() * ratio)), max(1, round(area.height() * ratio)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    view.render(image, QPoint(), QRegion(area), QWidget.RenderFlag.DrawChildren)
    return image


def _view_marks(view: Any) -> list[str]:
    """[the layout mark, the entry mark] in the view's own pixels (its bottom-right corner)."""
    from PySide6.QtCore import QRect

    image = _view_pixels(view, QRect(view.width() - 16, view.height() - 8, 16, 8))
    ratio = image.devicePixelRatio() or 1.0
    return [image.pixelColor(int(12 * ratio), int(4 * ratio)).name(), image.pixelColor(int(4 * ratio), int(4 * ratio)).name()]


class _Light:
    """A kept image of the view's own pixels: a patch's mean light as the window shows it (over its ground), and the
    big card's rectangle (the bounding box of its magenta border), in logical px."""

    def __init__(self, image: Any) -> None:
        self.ratio = image.devicePixelRatio() or 1.0
        self.w, self.h = image.width(), image.height()
        self.stride = image.bytesPerLine()
        self.data = bytes(image.constBits())

    def _px(self, x: int, y: int) -> tuple[int, int, int, int]:
        at = y * self.stride + x * 4                         # ARGB32 premultiplied, little endian: B G R A
        return self.data[at + 2], self.data[at + 1], self.data[at], self.data[at + 3]

    def light(self, rect: tuple[float, float, float, float]) -> float:
        x0, y0, x1, y1 = (round(value * self.ratio) for value in rect)
        total = count = 0
        for y in range(max(0, y0), min(self.h, y1), 2):
            for x in range(max(0, x0), min(self.w, x1), 2):
                r, g, b, a = self._px(x, y)
                total += 0.2126 * r + 0.7152 * g + 0.0722 * b + (255 - a) / 255 * _GROUND_LIGHT
                count += 1
        return total / count if count else 0.0

    def _magenta(self, x: int, y: int) -> bool:
        r, g, b, _a = self._px(x, y)
        return r >= 24 and b >= 24 and g * 2 < r and g * 2 < b and abs(r - b) < 32

    def card(self, center: tuple[float, float]) -> list[int] | None:
        """The card's rectangle from lines through ``center`` (inside the card all through its growth: it grows from
        its frame's rectangle, whose centre this is, to the whole view)."""
        cx, cy = round(center[0] * self.ratio), round(center[1] * self.ratio)
        xs = [x for y in (cy - 6, cy, cy + 6) if 0 <= y < self.h for x in range(self.w) if self._magenta(x, y)]
        ys = [y for x in (cx - 6, cx, cx + 6) if 0 <= x < self.w for y in range(self.h) if self._magenta(x, y)]
        if len(xs) < 2 or len(ys) < 2:
            return None
        return [round(min(xs) / self.ratio), round(min(ys) / self.ratio), round((max(xs) + 1) / self.ratio),
                round((max(ys) + 1) / self.ratio)]


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
    # Part 5c fixes, round 3 (stale-big 2): what the still's check leaves running - its beat, its time limit, its link
    # to the view's renders (the view's Qt Quick window's afterRendering, counted against the window's own receivers).
    from PySide6.QtCore import SIGNAL
    from PySide6.QtQuickWidgets import QQuickWidget

    view_proxy = board.web_view().focusProxy()
    quick = view_proxy.quickWindow() if isinstance(view_proxy, QQuickWidget) else None
    report["quick_window"] = quick is not None
    hooked_before = quick.receivers(SIGNAL("afterRendering()")) if quick is not None else 0

    def stops() -> list[Any]:
        big = desk.big
        hooked = quick.receivers(SIGNAL("afterRendering()")) - hooked_before if quick is not None else -1
        return [big._edge_timer.isActive(), big._veil_timer.isActive(), big._watched is None, hooked]  # noqa: SLF001

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
    report["stops_open_lift"] = stops()                     # part 5c fixes, round 3: nothing left after the lift
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
    report["stops_close_lift"] = stops()                    # part 5c fixes, round 3: nothing left after the close
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
    # Part 5c fixes, round 3: a quick route (a step asked while the view may still hold the other step's frame) runs
    # with the still's real cap - its in-between frame may never show, and then the cap lifts it (veil_late, by design)
    # - and its late lifts are counted apart from the other steps'.
    quick_lates: dict[str, int] = {}

    class QuickRoute:
        def __enter__(self) -> None:
            self.before = {key_: value for key_, value in desk.big.counts.items() if key_.startswith("veil_late")}
            desk_big.VEIL_MS = 400

        def __exit__(self, *_exc: Any) -> None:
            desk_big.VEIL_MS = 5000
            for key_, value in desk.big.counts.items():
                if key_.startswith("veil_late") and value != self.before.get(key_, 0):
                    quick_lates[key_] = quick_lates.get(key_, 0) + value - self.before.get(key_, 0)

    # (1) motion off: Esc as the open's "laid" is made ready (two frames before it would be sent) - it never comes
    steps.clear()
    with QuickRoute():
        main_world(board, "window.__hook = 'esc-at-open'; 1")
        desk._on_open(page_id)                               # as the page's request
        wait_until(lambda: not desk.big.state() and steps.count("laid") >= 1, 5)
        pump(0.5)
    report["esc_at_open_steps"] = list(steps)
    report["esc_at_open_veil"] = reading.desk_page().veil.shown()
    # (2) motion off: Enter on a frame as the close's "laid" is made ready - only the new open's "laid" comes
    desk._on_open(page_id)
    wait_until(lambda: page()["card"] is not None, 5)
    pump(0.4)
    steps.clear()
    with QuickRoute():
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
    with QuickRoute():
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
    with QuickRoute():
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
    # ---- part 5c fixes, round 3 (stale-big 0): a step asked while the view still holds the other step's frame - Enter
    # after the close's "shut", Enter as the close's "laid" is made ready, Esc as the big frame is added; lively and off.
    # The still lifts on this step's own layout and the page's last entry (both marks read from the window as the still
    # is cleared), and no frame of the other layout follows it (the view's own marks at each of its renders, 150 ms).
    # Quick routes: the still's real cap lifts a step whose in-between frame never shows (counted veil_late) ---------
    main_world(board, _ENTRY_MARK)
    pump(0.2)
    lifts: list[list[Any]] = []
    sampled: list[list[Any]] = []
    sampling = [False]

    def lift_and_look() -> None:
        shown = veil.shown()
        type(veil).clear(veil)
        if shown:
            corner = reading.desk_page().mapTo(window, board.geometry().bottomRight() - QPoint(4, 4))
            lifts.append([desk.big.state() or "closed",
                          window.grab(QRect(corner, QSize(1, 1))).toImage().pixelColor(0, 0).name(),
                          window.grab(QRect(corner - QPoint(8, 0), QSize(1, 1))).toImage().pixelColor(0, 0).name(),
                          time.monotonic()])

    def sample() -> None:                                    # each render of the view: its marks as Qt has them
        if sampling[0]:
            sampled.append([time.monotonic()] + _view_marks(board.web_view()))

    if quick is not None:
        quick.afterRendering.connect(sample, Qt.ConnectionType.QueuedConnection)
    routes: dict[str, Any] = {}
    routing = QuickRoute()
    routing.__enter__()
    for route in ("enter-at-shut", "enter-at-close", "esc-at-grow"):
        for motion in ("lively", "off"):
            holo.set_motion_level(motion)
            board.set_motion(motion)
            pump(0.2)
            if route == "esc-at-grow":                       # from the board, the frame focused
                main_world(board, f"document.getElementById('title-{page_id}').closest('.frame').focus(); 1")
                pump(0.1)
            else:                                            # from the frame open big
                desk._on_open(page_id)
                wait_until(lambda: desk.big.state() == "open" and not veil.shown() and page()["card"] is not None, 5)
                pump(0.5)
            counts_before = dict(desk.big.counts)
            lifts.clear()
            sampled.clear()
            steps.clear()
            veil.clear = lift_and_look
            sampling[0] = True
            main_world(board, "window.__hook = '%s'; 1" % route)
            if route == "esc-at-grow":
                desk._on_open(page_id)                       # (its Esc comes as the big frame is added)
                wait_until(lambda: "shut" in steps and not desk.big.state() and not veil.shown(), 5)
            else:
                board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
                pump(0.1)
                QTest.keyClick(board.web_view().focusProxy(), Qt.Key.Key_Escape)
                wait_until(lambda: "open" in steps and desk.big.state() == "open" and not veil.shown(), 5)
            pump(0.4)
            sampling[0] = False
            del veil.clear
            entries = main_world(board, "window.__entries()")
            last = lifts[-1][3] if lifts else time.monotonic()
            routes[f"{route}:{motion}"] = {
                "steps": list(steps), "state": desk.big.state() or "closed",
                "lifts": [lift[:3] for lift in lifts],
                "after": [marks_[1:] for marks_ in sampled if last < marks_[0] <= last + 0.15],
                "entry": _ENTRY_COLOURS[int(entries or 0) % len(_ENTRY_COLOURS)],
                "counts": {key_: value - counts_before.get(key_, 0) for key_, value in desk.big.counts.items()
                           if value != counts_before.get(key_, 0) and key_.startswith(("veil_late", "armed", "shown"))}}
            if desk.big.state():                             # back to the board
                board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
                pump(0.1)
                QTest.keyClick(board.web_view().focusProxy(), Qt.Key.Key_Escape)
            wait_until(lambda: not desk.big.state() and not veil.shown(), 5)
            pump(0.5)
    routing.__exit__(None, None, None)
    if quick is not None:
        quick.afterRendering.disconnect(sample)
    report["routes"] = routes
    # ---- part 5c fixes, round 3 (item 7): the dims move with the frame at open big and back. Nothing of the desk's or
    # the pill's entrance is left on what open big dims (an animation left there, even one over, kept their dim from
    # easing with the growth); then, lively, the view's own pixels at each of its renders: the light of the backdrop,
    # the board under the big frame's first rectangle and the bar, as a share of the way from full light to the dim's
    # end (both read with the card hidden, test-only), against the big frame's own progress (its border magenta,
    # test-only, found in the same pixels) ---------------------------------------------------------------------------
    window.resize(1120, 700)
    pump(0.8)
    holo.set_motion_level("lively")
    board.set_motion("lively")
    pump(0.2)
    board.apply({"type": "desk", "state": "enter"})
    pump(0.7)
    report["entrance_after_desk_in"] = json.loads(main_world(board, _ENTRANCE_LEFT))
    board.apply({"type": "group", "id": "g99", "time": "9:59 AM", "topic": "Parked", "frames": [], "older": False,
                 "parked": True})
    pump(0.5)
    report["entrance_after_pill"] = json.loads(main_world(board, _ENTRANCE_LEFT))
    board.apply({"type": "remove_group", "id": "g99"})
    pump(0.4)

    def measure_dims(frame_id: str) -> None:               # (run at the end: a small frame on top, pinned ones under it)
        view = board.web_view()
        main_world(board, _MAGENTA_CARD)
        holo.set_motion_level("off")
        board.set_motion("off")
        pump(0.3)
        geometry = reading.desk_page().geometry_now()
        bx, by = geometry.view.x() - geometry.big.x(), geometry.view.y() - geometry.big.y()
        wide_w, wide_h, own_w = geometry.big.width(), geometry.big.height(), geometry.view.width()
        box = frame_box(frame_id)                            # the big frame's first rectangle, in the wide view
        first = (bx + box["left"], by + box["top"], bx + box["left"] + box["width"], by + box["top"] + box["height"])
        center = ((first[0] + first[2]) / 2, (first[1] + first[3]) / 2)
        # Patches clear of the big frame (and its shadow) until it is 60 % grown: the backdrop left of it, the board
        # under it, and the bar over it (that one only while the frame is still small).
        patches = {"backdrop": (8, 8, max(12, int(first[0] * 0.4 - 24)), wide_h - 8),
                   "board": (bx + 15, min(wide_h - 24, int(first[3] + 0.6 * (wide_h - first[3]) + 24)),
                             bx + own_w - 44, wide_h - 8),
                   "bar": (bx + 15, by + 4, bx + int(own_w * 0.6), by + 28)}
        desk._on_open(frame_id)
        wait_until(lambda: desk.big.state() == "open" and not veil.shown() and page()["card"] is not None, 5)
        pump(0.4)
        main_world(board, _HIDE_CARD)
        pump(0.4)
        full = _Light(_view_pixels(view))
        main_world(board, "document.body.classList.add('dim'); 1")
        pump(0.4)
        dimmed = _Light(_view_pixels(view))
        main_world(board, _SHOW_CARD)
        pump(0.2)
        desk.big.close()
        wait_until(lambda: not desk.big.state() and not veil.shown(), 5)
        pump(0.4)
        refs = {name: [round(full.light(rect), 2), round(dimmed.light(rect), 2)] for name, rect in patches.items()}
        kept: list[Any] = []
        keeping = [False]

        def keep() -> None:
            if keeping[0]:
                kept.append(_view_pixels(view))

        if quick is not None:
            quick.afterRendering.connect(keep, Qt.ConnectionType.QueuedConnection)
        samples: list[list[Any]] = []
        renders_seen: list[list[Any]] = []
        from_w = first[2] - first[0]
        for motion in ("lively", "calm"):                    # (calm: the same frames, more of them in the motion)
            holo.set_motion_level(motion)
            board.set_motion(motion)
            pump(0.3)
            for step in ("open", "close"):
                kept.clear()
                keeping[0] = True
                if step == "open":
                    desk._on_open(frame_id)
                    wait_until(lambda: desk.big.state() == "open" and not veil.shown(), 5)
                else:
                    desk.big.close()
                    wait_until(lambda: not desk.big.state() and not veil.shown(), 5)
                pump(0.6)
                keeping[0] = False
                for render, image in enumerate(kept):
                    light = _Light(image)
                    card = light.card(center) if abs(light.w / light.ratio - wide_w) <= 2 else None
                    renders_seen.append([motion, step, render, round(light.w / light.ratio), card])
                    if not card or card[2] - card[0] < from_w - 4:
                        continue                             # (no big frame: not there yet, faded, or the view narrow)
                    growth = ((card[2] - card[0]) - from_w) / (wide_w - from_w)
                    for name, (x0, y0, x1, y1) in patches.items():
                        if not (card[2] + 24 <= x0 or card[0] - 24 >= x1 or card[3] + 24 <= y0 or card[1] - 24 >= y1):
                            continue                         # (under the big frame or its shadow)
                        lit_full, lit_dim = refs[name]
                        samples.append([motion, step, render, name,
                                        round((lit_full - light.light((x0, y0, x1, y1))) / (lit_full - lit_dim), 3),
                                        round(growth, 3)])
                kept.clear()
        if quick is not None:
            quick.afterRendering.disconnect(keep)
        report["dims_patches"] = refs
        report["dims_samples"] = samples
        report["dims_renders"] = renders_seen
        report["dims_first"] = [round(value) for value in first]

    # ---- part 5c fixes, round 3 (stale-big 2): end_now while a still is up leaves nothing of it running -------------
    desk._on_open(page_id)
    report["stops_before_end_now"] = [veil.shown(), desk.big.still_judged()] + stops()
    desk.big.end_now()
    report["stops_end_now"] = stops()
    pump(0.6)
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
    # ---- part 5c fixes, round 3 (stale-big 1): the big frame's group fills the board's view (its frames whole, the big
    # frame the first of its three) and leaves for room while it is big - the oldest group the board may drop, since
    # every older one holds a pinned frame (P on each, the page's own key): the keyboard goes to another group's frame,
    # never to a sibling leaving with it (and from there to <body>); lively and off -----------------------------------
    in_view = r"""JSON.stringify((function () {                 // the frames in view, as the page's focusBoard tells it
      var box = document.getElementById('board').getBoundingClientRect();
      return Array.prototype.filter.call(document.querySelectorAll('#board .frame'), function (f) {
        var r = f.getBoundingClientRect(); return r.height > 0 && r.bottom > box.top + 6 && r.top < box.bottom - 40;
      }).map(function (f) { return f.querySelector('.f-title').id.slice(6); }); })())"""
    press_p = ("(function (n) { n.dispatchEvent(new KeyboardEvent('keydown', {key: 'p', bubbles: true})); return 1; })"
               "(document.getElementById('title-%s').closest('.frame'))")
    main_world(board, r"""(function () {                     // every place the keyboard goes (a frame by its id)
      window.__trail = [];
      document.addEventListener('focusin', function (event) {
        var n = event.target, f = n.closest ? n.closest('.frame') : null;
        window.__trail.push(n.closest && n.closest('#big') ? 'card' : f && f.querySelector('.f-title')
          ? f.querySelector('.f-title').id.slice(6) : n.tagName.toLowerCase());
      }, true);
      return 1; })()""")
    for index, motion in enumerate(("lively", "off")):
        holo.set_motion_level(motion)
        board.set_motion(motion)
        pump(0.2)
        desk.feed.max_groups = 8
        for frame_id in desk.feed.frame_ids():               # every older group stays: a pinned frame each
            if not desk.feed.flags(frame_id).get("pinned"):
                main_world(board, press_p % frame_id)
                pump(0.3)
        pinned = [frame_id for frame_id in desk.feed.frame_ids() if desk.feed.flags(frame_id).get("pinned")]
        desk._watch.reset()
        three = _put_answer(c, desk, built, 61 + 2 * index, [("page", "page"), ("day", "day"), ("mail", "mail")])
        pump(1.0)
        first_in_view = json.loads(main_world(board, in_view))
        desk._on_open(three[0])
        wait_until(lambda: desk.big.state() == "open" and not veil.shown(), 5)
        pump(0.6)
        before = page()["active"]
        desk.feed.max_groups = len(desk.feed.groups())       # full: the next answer makes room (the big frame's group)
        desk._watch.reset()
        main_world(board, "window.__trail = []; 1")
        newest_ = _put_answer(c, desk, built, 62 + 2 * index, [(f"todos-s{index}", "todos")],
                              keys={f"todos-s{index}": ""})
        wait_until(lambda: not desk.big.state() and not veil.shown(), 5)
        pump(0.6)
        report[f"room_view_{motion}"] = {
            "group": three, "pinned": pinned, "first_in_view": first_in_view, "before": before,
            "active": page()["active"], "left": [frame_id not in desk.feed.frame_ids() for frame_id in three],
            "board": desk.feed.frame_ids(), "newest": newest_[0],
            "trail": json.loads(main_world(board, "JSON.stringify(window.__trail)"))}   # (never a leaving sibling)
    # ---- part 5c fixes, round 4 (stale-big 0): a group leaves for room while the page's focus is on <body> because the
    # owner clicked beside the frames (a real click on a group's head): it stays there - no frame takes it, nothing
    # scrolls, the page says nothing of reading focus - so the answer that made the room, parked while the owner reads,
    # still lands once it is quiet. The board is scrolled into an older group, the frame the board would pick (its
    # focusBoard) cut at its top; lively and off ---------------------------------------------------------------------
    room_look = r"""JSON.stringify((function (refId, victim) {
      var root = document.getElementById('board'), box = root.getBoundingClientRect();
      var ref = document.getElementById('title-' + refId), place = ref && ref.closest('.frame').getBoundingClientRect();
      var pick = Array.prototype.filter.call(root.querySelectorAll('.frame'), function (f) {   // focusBoard's own pick
        var r = f.getBoundingClientRect();
        return r.height > 0 && r.bottom > box.top + 6 && r.top < box.bottom - 40; })[0];
      var head = Array.prototype.filter.call(root.querySelectorAll('.g-head'), function (h) {  // a staying group's head
        var r = h.getBoundingClientRect();
        return h.id !== 'group-' + victim && r.height > 0 && r.top >= box.top + 4 && r.bottom <= box.bottom - 44; })[0];
      var r = head && head.getBoundingClientRect();
      return {scrollTop: root.scrollTop, refTop: place ? place.top - box.top : null,
              refHeight: place ? place.height : 0, pick: pick ? pick.querySelector('.f-title').id.slice(6) : '',
              victimShown: !!document.getElementById('group-' + victim),
              head: r ? {left: r.left, top: r.top, width: r.width, height: r.height} : null};
    })(%s, %s))"""
    cut_at_top = ("(function (id) { var root = document.getElementById('board'), box = root.getBoundingClientRect();"
                  " var r = document.getElementById('title-' + id).closest('.frame').getBoundingClientRect();"
                  " root.scrollTop += r.top - box.top + 20; return root.scrollTop; })(%s)")
    reading_posts: list[list[bool]] = []
    board.readingChanged.connect(lambda pointer, focus: reading_posts.append([pointer, focus]))
    report["room_body"] = {}
    for index, motion in enumerate(("lively", "off")):
        holo.set_motion_level(motion)
        board.set_motion(motion)
        pump(0.2)
        desk.feed.max_groups = 8
        for step in range(3):                                # three answers of three frames each: the board scrolls
            desk._watch.reset()
            _put_answer(c, desk, built, 70 + 10 * index + step, [("page", "page"), ("day", "day"), ("mail", "mail")],
                        keys={"mail": ""})
            pump(0.8)
        groups_now = desk.feed.groups()                      # (newest first)
        desk.feed.max_groups = len(groups_now)               # full: the next answer makes room
        victim = next(group_id for group_id in reversed(groups_now)   # the oldest group with nothing pinned
                      if not any(desk.feed.flags(frame_id).get("pinned")
                                 for frame_id in desk.feed.group_order(group_id)))
        ref = desk.feed.group_order(groups_now[1])[0]        # the next group's first frame (a satellite) ...
        main_world(board, cut_at_top % json.dumps(ref))      # ... 20 px of it above the board's top
        pump(0.4)
        look = json.loads(main_world(board, room_look % (json.dumps(ref), json.dumps(victim))))
        board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
        pump(0.2)
        head = look["head"]
        if head:                                             # the owner's click beside the frames: on a group's head
            QTest.mouseClick(board.web_view().focusProxy(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                             QPoint(int(head["left"] + head["width"] / 2), int(head["top"] + head["height"] / 2)))
        pump(0.4)
        before = json.loads(main_world(board, room_look % (json.dumps(ref), json.dumps(victim))))
        body_before = [page()["active"], main_world(board, "document.hasFocus()")]
        posts_from = len(reading_posts)
        _put_answer(c, desk, built, 79 + 10 * index, [(f"todos-r{index}", "todos")], keys={f"todos-r{index}": ""})
        parked = desk.feed.parked_groups()
        pump(0.8)
        after = json.loads(main_world(board, room_look % (json.dumps(ref), json.dumps(victim))))
        active = page()["active"]
        posts = reading_posts[posts_from:]
        # Quiet from here: the pointer goes (offscreen it never moves away itself), and the quiet time (made short) is
        # up.
        QApplication.sendEvent(board.web_view().focusProxy(), QEvent(QEvent.Type.Leave))
        pump(0.3)
        quiet_s, desk._watch.quiet_s = desk._watch.quiet_s, 0.2
        pump(0.4)
        desk._schedule_landing()
        pump(0.4)
        landed = [group_id for group_id in parked
                  if not desk.feed.is_parked(group_id) and group_id in desk.feed.groups()]
        desk._watch.quiet_s = quiet_s
        report["room_body"][motion] = {
            "head": bool(head), "before": body_before, "pick_before": before["pick"], "ref": ref,
            "ref_top_before": before["refTop"], "ref_height": before["refHeight"], "scroll_before": before["scrollTop"],
            "victim": victim, "victim_left": victim not in desk.feed.groups(), "victim_shown": after["victimShown"],
            "parked": parked, "active": active, "scroll": after["scrollTop"], "ref_top": after["refTop"],
            "posts": posts, "landed": landed}
        pump(0.4)
    # The board as it was before the round-4 case (the newest group's lone to-do list, the pinned ones under, the
    # board at its top): every other group leaves for this answer's room.
    holo.set_motion_level("off")
    board.set_motion("off")
    pump(0.2)
    desk.feed.max_groups = 3
    desk._watch.reset()
    _put_answer(c, desk, built, 65, [("todos-t", "todos")], keys={"todos-t": ""})
    pump(0.8)
    report["room_body_after"] = [desk.feed.frame_ids(), [desk.feed.flags(frame_id).get("pinned")
                                                         for frame_id in desk.feed.frame_ids()]]
    measure_dims(desk.feed.frame_ids()[0])                  # (item 7: the newest group's to-do list, pinned ones under)
    report["desk_counts"] = {key_: value for key_, value in desk.counts.items() if isinstance(value, int)}
    report["big_counts_end"] = dict(desk.big.counts)
    report["veil_late_quick"] = quick_lates
    report["veil_late_outside_routes"] = {key_: value - quick_lates.get(key_, 0) for key_, value in
                                          desk.big.counts.items() if key_.startswith("veil_late")}
    # ---- part 5c fixes, round 3 (stale-big 2): shutdown while a still is up leaves nothing of it running ------------
    desk._on_open(desk.feed.frame_ids()[0])
    report["stops_before_shutdown"] = [veil.shown(), desk.big.still_judged()] + stops()
    desk.big.shutdown()
    report["stops_shutdown"] = stops()
    desk.big.end_now()
    pump(0.4)
    board.removeEventFilter(moves)
    window.hide()
    settle_end(report, board)
    shim.close()
    return report


# Part 5c fixes, round 5 (focus 0): the page's CLOSED_MS (8000 ms: a closed frame's row offers Bring back this long)
# made short once when asked (window.__expiryMs), so its own expiry comes well before the app's removal - the order the
# owner meets whenever the app's timer is a few ms later than the page's; every other timer keeps its time. Where the
# keyboard goes (a frame by its id: the frame itself, its Bring back, or inside it) and, after the expiry's style
# update, where it is and the board's scroll.
_EXPIRY_HOOKS = r"""(function () {
  var later = window.setTimeout;
  window.__expiryMs = 0;
  window.setTimeout = function (fn, ms) {
    var rest = Array.prototype.slice.call(arguments, 2);
    if (window.__expiryMs && ms === 8000) {
      ms = window.__expiryMs;
      window.__expiryMs = 0;
    }
    return later.apply(window, [fn, ms].concat(rest));
  };
  function name(node) {
    if (!node) { return 'none'; }
    if (node === document.body) { return 'body'; }
    var frame = node.closest ? node.closest('.frame') : null, title = frame && frame.querySelector('.f-title');
    if (!title) { return node.tagName ? node.tagName.toLowerCase() : 'none'; }
    var id = title.id.slice(6);
    return node === frame ? 'frame:' + id : node.closest('.f-closed') ? 'bring_back:' + id : 'in:' + id;
  }
  window.__name = name;
  var seen = window.__seen = {trail: [], expired: '', atExpiry: '', scrollAtExpiry: -1};
  document.addEventListener('focusin', function (e) { seen.trail.push('in ' + name(e.target)); }, true);
  document.addEventListener('focusout', function (e) {
    seen.trail.push('out ' + name(e.target) + ' > ' + name(e.relatedTarget));
  }, true);
  new MutationObserver(function (records) {
    records.forEach(function (record) {
      var frame = record.target;
      if (frame.classList.contains('frame') && frame.classList.contains('expired')
          && (' ' + (record.oldValue || '') + ' ').indexOf(' expired ') < 0) {
        seen.expired = frame.querySelector('.f-title').id.slice(6);
        later(function () {                     // after the style update that drops a hidden button's focus
          void document.body.offsetHeight;
          later(function () {
            seen.atExpiry = name(document.activeElement);
            seen.scrollAtExpiry = document.getElementById('board').scrollTop;
          }, 60);
        }, 120);
      }
    });
  }).observe(document.getElementById('board'), {subtree: true, attributes: true, attributeFilter: ['class'],
                                                 attributeOldValue: true});
  return 1;
})()"""
# The closed row cut 10 px by the view's bottom edge (else by its top edge): a focus that scrolls would show (Chromium
# scrolls a row cut so to its closest edge on focus(), never with preventScroll). Its side and how much is cut.
_CUT_ROW = r"""JSON.stringify((function (id) {
  var root = document.getElementById('board'), box = root.getBoundingClientRect();
  var frame = document.getElementById('title-' + id).closest('.frame'), r = frame.getBoundingClientRect();
  var most = root.scrollHeight - root.clientHeight, side = '';
  var down = root.scrollTop + (r.bottom - box.bottom) - 10, up = root.scrollTop + (r.top - box.top) + 10;
  if (down >= 0 && down <= most) { root.scrollTop = down; side = 'bottom'; }
  else if (up >= 0 && up <= most) { root.scrollTop = up; side = 'top'; }
  var now = frame.getBoundingClientRect();
  return [side, Math.round(side === 'top' ? box.top - now.top : now.bottom - box.bottom)];
})(%s))"""


def expiry_case(wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    """Part 5c fixes, round 5 (focus 0): a closed frame's own end, the page's expiry before the app's removal (see the
    module doc)."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from briefing_reader import desk_ui, holo
    from briefing_reader.board import view

    report: dict[str, Any] = {}
    desk_ui.DeskController.CLOSE_S = 4.5          # the app's removal 4.5 s after the Delete; the page's expiry at 1.5 s
    holo.set_motion_level("off")
    shim = _Shim()
    harness, c, boards, desk = _app_case(shim, wait_until, pump, (1120, 700))
    window = c.window
    built = dict(kind_frames(tiny_jpeg_url(320, 180)))
    asked: list[list[str]] = []
    reading_posts: list[list[bool]] = []
    focus_posts: list[str] = []

    def answer(key: int, names: list[tuple[str, str]]) -> list[str]:
        desk._watch.reset()                      # (nobody reads: never parked)
        ids = _put_answer(c, desk, built, key, names, keys={source: "" for source, _name in names})
        pump(0.8)
        return ids

    def frame_js(frame_id: str) -> str:
        return f"document.getElementById('title-{frame_id}').closest('.frame')"

    def active() -> str:
        return str(main_world(board, "window.__name(document.activeElement)"))

    def close_one(frame_id: str, route: str) -> dict[str, Any]:
        """Real Delete on ``frame_id`` (its own key target: a satellite's row, else the frame), then its row cut by the
        view's edge; the page's expiry; Enter, P and Delete on it; the app's removal."""
        board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
        wait_until(lambda: view.keeps_space(QApplication.focusWidget()), 5)
        main_world(board, f"(function () {{ var f = {frame_js(frame_id)}; (f.classList.contains('sat') ? "
                          "f.querySelector('.f-sat') : f).focus(); })(); 1")
        pump(0.2)
        main_world(board, "window.__seen.trail = []; window.__seen.expired = ''; window.__seen.atExpiry = ''; "
                          "window.__expiryMs = 1500; 1")
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Delete)
        pump(0.3)
        row: dict[str, Any] = {"route": route, "frame": frame_id, "group": desk.feed.group_of(frame_id),
                               "after_delete": active(), "closed": bool(desk.feed.flags(frame_id).get("closed"))}
        reading_from, focus_from = len(reading_posts), len(focus_posts)
        row["cut"] = json.loads(main_world(board, _CUT_ROW % json.dumps(frame_id)))
        pump(0.1)
        row["scroll_before"] = main_world(board, "document.getElementById('board').scrollTop")
        row["marked_before_expiry"] = main_world(board, "window.__seen.expired") == ""
        wait_until(lambda: main_world(board, "window.__seen.atExpiry") != "", 6)
        seen = json.loads(main_world(board, "JSON.stringify(window.__seen)"))
        row.update(expired=seen["expired"], at_expiry=seen["atExpiry"], scroll_at_expiry=seen["scrollAtExpiry"],
                   expired_first=frame_id in desk.feed.frame_ids() and bool(desk.feed.flags(frame_id).get("closed")),
                   reading_posts=reading_posts[reading_from:], focus_posts=focus_posts[focus_from:])
        asked_from = len(asked)                  # the board's own keys there: nothing
        for code in (Qt.Key.Key_Return, Qt.Key.Key_P, Qt.Key.Key_Delete):
            QTest.keyClick(QApplication.focusWidget(), code)
            pump(0.1)
        row["keys_asked"] = asked[asked_from:]
        row["after_keys"] = active()
        row["keys_in_window"] = frame_id in desk.feed.frame_ids() and bool(desk.feed.flags(frame_id).get("closed"))
        wait_until(lambda: frame_id not in desk.feed.frame_ids(), 8)
        pump(0.6)
        row["left"] = frame_id not in desk.feed.frame_ids()
        row["group_left"] = row["group"] not in desk.feed.groups()
        row["after"] = active()
        row["staying"] = row["after"].startswith("frame:") and row["after"][6:] in desk.feed.frame_ids()
        row["has_focus"] = main_world(board, "document.hasFocus()")
        row["trail"] = json.loads(main_world(board, "JSON.stringify(window.__seen.trail)"))
        return row

    board = None
    report["expiry"] = {}
    for index, motion in enumerate(("lively", "off")):
        holo.set_motion_level(motion)
        key = 80 + 10 * index
        older = answer(key, [(f"research-{index}", "research"), (f"search-{index}", "search"),
                             (f"followups-{index}", "followups")])
        lone = answer(key + 1, [(f"todos-{index}", "todos")])
        newest = answer(key + 2, [(f"page-{index}", "page"), (f"day-{index}", "day"), (f"mail-{index}", "mail")])
        if board is None:
            board = boards[0]
            wait_until(board.is_ready, 40)
            pump(1.0)
            report["ready"] = board.is_ready()
            main_world(board, _EXPIRY_HOOKS)
            for name in ("openRequested", "closeRequested", "bringBackRequested"):
                getattr(board, name).connect(lambda frame_id, n=name: asked.append([n, frame_id]))
            board.pinRequested.connect(lambda frame_id, on: asked.append(["pinRequested", frame_id, on]))
            board.readingChanged.connect(lambda pointer, focus: reading_posts.append([pointer, focus]))
            board.frameFocused.connect(focus_posts.append)
        board.set_motion(motion)
        pump(0.3)
        report["expiry"][motion] = {
            "older": older, "board": desk.feed.frame_ids(),
            "group_last": close_one(lone[0], "group_last"),       # the group's last frame: remove_group
            "not_last": close_one(newest[-1], "not_last"),        # one of three: remove
        }
    report["desk_counts"] = {key_: value for key_, value in desk.counts.items() if isinstance(value, int)}
    window.hide()
    settle_end(report, board)
    shim.close()
    return report


# --------------------------------------------------------------------------
# Part 6: Ask's typed frames in the real page
# --------------------------------------------------------------------------

# Words a model might write, in every string of its frames (each within its field's cap).
TYPED_WORDS = {"script": "<script>window.pwned=2</script>", "img": "<img src=x onerror=window.pwned=3>",
               "js": "javascript:window.pwned=4", "http": "http://example.com/x.png", "bidi": "‮Events⁦"}
# The plain expressions whose reading the page and board.ask_frames.maths_text must agree on (no power, fraction or
# root: those the page lays out, so their words are not one line).
PLAIN_EXPRESSIONS = [
    ["=", ["+", ["*", 2, "x"], ["*", -8, "x"], 6], 0], ["*", 2, ["+", "x", 1]], ["-", "a", ["+", "b", "c"]],
    ["-", ["+", "a", "b"]], ["*", "x", "y"], ["*", "rate", "time"], ["*", 2, -3], ["/", ["*", 6, "x"], ["*", 2, "x"]],
    ["=", "x", ["or", 1, 2]], ["+", "a", ["-", ["-", "b"]]], ["*", ["()", ["-", "x", 1]], ["()", ["-", "x", 3]]],
    ["or", ["=", "x", 1], ["=", "x", 3]], ["+", -2, "a", ["-", ["*", 4, "x"]]], ["*", 2, ["*", 3, "x"]],
    ["-", ["-", "x"]], ["=", 1234567.5, ["*", 3, 411522.5]],
]
_TYPED_PAGE = r"""JSON.stringify((function (markers) {
  var tags = ['ARTICLE', 'DIV', 'SPAN', 'P', 'H2', 'B', 'I', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'BUTTON',
              'svg', 'path', 'rect'];
  var names = ['class', 'scope', 'aria-hidden', 'style', 'viewBox', 'focusable'];
  var styleOk = /^(\s*--(x0|xw|n|today):\s*[0-9a-z.%() +\-\/]*;)*\s*$/;
  var found = {};
  document.querySelectorAll('#board .frame').forEach(function (f) {
    var id = f.querySelector('.f-title').id.slice(6);
    var badTags = [], badAttributes = [], dataAttributes = [];
    f.querySelectorAll('*').forEach(function (el) {
      if (tags.indexOf(el.tagName) < 0) { badTags.push(el.tagName); }
    });
    f.querySelectorAll('.f-body, .f-body *').forEach(function (el) {
      Array.prototype.forEach.call(el.attributes, function (a) {
        if (names.indexOf(a.name) < 0 || (a.name === 'style' && (!styleOk.test(a.value) || /url|\/\//i.test(a.value)))) {
          badAttributes.push(a.name + '=' + a.value.slice(0, 40));
        }
        markers.forEach(function (m) { if (a.value.indexOf(m) >= 0) { dataAttributes.push(a.name); } });
      });
    });
    found[id] = {kind: f.className, text: f.querySelector('.f-body').textContent, title: f.querySelector('.f-title').textContent,
                 badTags: badTags, badAttributes: badAttributes, dataAttributes: dataAttributes,
                 lines: Array.prototype.map.call(f.querySelectorAll('.mx-e'), function (el) { return el.textContent; })};
  });
  var parts = function (sel) {
    return Array.prototype.map.call(document.querySelectorAll(sel), function (el) { return el.textContent; });
  };
  return {frames: found, scripts: document.scripts.length, pwned: typeof window.pwned,
          links: document.querySelectorAll('#board a, #board img, #board iframe, #board script, #board style').length,
          fracTop: parts('#board .mx-frac > .mx-top'), fracBottom: parts('#board .mx-frac > .mx-bot'),
          sup: parts('#board .mx-sup'), rad: parts('#board .mx-rad'), idx: parts('#board .mx-idx'),
          answers: parts('#board .mx-ans'), hot: parts('#board tr.hot td'), zeros: parts('#board td.z').length,
          ghost: document.querySelectorAll('#board .ch-was').length, hotBars: document.querySelectorAll('#board .ch-fl.hot').length,
          delta: parts('#board .delta .delta-lead'), states: parts('#board .tl-state'), today: parts('#board .tl-today')};
})(__MARKERS__))"""
_TYPED_LAYOUT = r"""JSON.stringify((function () {
  var words = Array.from(document.querySelectorAll('#board .frame span, #board .frame p, #board .frame b, ' +
                                                   '#board .frame h2, #board .frame th, #board .frame td'));
  var root = document.getElementById('board');
  var tl = document.querySelector('#board .tline'), mx = document.querySelector('#board .mx');
  var today = document.querySelector('#board .tl-today');
  return {frameWidth: Math.round(document.querySelector('#board .frame').getBoundingClientRect().width),
          cut: words.filter(function (el) { return el.scrollWidth > el.clientWidth + 1; }).map(function (el) { return el.className || el.tagName; }),
          words: words.length, pageCut: root.scrollWidth > root.clientWidth,
          timelineColumns: getComputedStyle(tl).gridTemplateColumns,
          nodeTops: Array.prototype.map.call(tl.querySelectorAll('.tl-n'), function (n) {
            return Math.round(n.getBoundingClientRect().top); }),
          todayPosition: getComputedStyle(today).position,
          mathsColumns: getComputedStyle(mx).gridTemplateColumns};
})())"""
_TYPED_BIG = r"""JSON.stringify((function () {
  var card = document.querySelector('.big-card');
  if (!card) { return null; }
  var cell = card.querySelector('td'), tl = card.querySelector('.tline');
  return {rows: card.querySelectorAll('tbody tr').length, cellFont: cell ? getComputedStyle(cell).fontSize : '',
          timelineColumns: tl ? getComputedStyle(tl).gridTemplateColumns : '',
          nodeTops: tl ? Array.prototype.map.call(tl.querySelectorAll('.tl-n'), function (n) {
            return Math.round(n.getBoundingClientRect().top); }) : [],
          box: (function (r) { return [Math.round(r.width), Math.round(r.height)]; })(card.getBoundingClientRect()),
          state: board.counts().big};
})())"""


def typed_answer() -> dict[str, Any]:
    """A model's answer (invented): a table, a chart and a timeline with hostile words in every string."""
    words = TYPED_WORDS
    return {"topic": "<b>Dana</b>'s numbers", "frames": [
        {"kind": "table", "title": words["script"], "unit": "<i>USD</i>",
         "columns": ["Line", "<u>Fri</u>", "Today", "Change"],
         "rows": [[words["js"], 1200, 1200, 0], ["Software", 2900, 3400, "+500"], [words["bidi"], 2100, 2100, 0],
                  [words["http"], 800, 800, 0]],
         "changed": 1, "change": "+500 <b>Software</b> · was 2.9k", "source": "<a href=\"x\">From Dana's mail</a>"},
        {"kind": "chart", "title": "Q4 " + words["img"], "unit": "USD",
         "bars": [{"label": "Travel", "value": 1200}, {"label": words["http"], "value": 3400},
                  {"label": "<b>Events</b>", "value": 2100}, {"label": "Hardware", "value": -800}],
         "changed": 1, "was": 2900, "change": "+500 " + words["js"]},
        {"kind": "timeline", "title": "LING " + words["img"],
         "nodes": [{"when": "Mon", "words": words["js"], "state": "done"},
                   {"when": "<i>Wed</i>", "words": "Full draft", "state": "next"},
                   {"when": "Thu 11:59 PM", "words": words["bidi"], "state": "due"}], "today": 1},
        {"kind": "maths", "title": "the fourth: left out"}]}


def typed_case(make: Any, wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    from briefing_reader.board import ask_frames, protocol

    report: dict[str, Any] = {}
    host, board = make()
    host.resize(478, 900)
    board.setGeometry(0, 0, 478, 900)                      # a 448 px frame (the page's own 15 px pads)
    host.show()
    wait_until(board.is_ready, 40)
    report["ready"] = board.is_ready()
    board.set_motion("off")
    found = ask_frames.check_answer(typed_answer())
    report["dropped"] = dict(found.dropped)
    maths = ask_frames.check_frame({"kind": "maths", "title": "Solve 2x² − 8x + 6 = 0 " + TYPED_WORDS["script"],
                                    "steps": [{"name": "Divide by 2", "expr": ["=", ["+", ["pow", "x", 2],
                                                                                         ["*", -4, "x"], 3], 0]},
                                              {"name": TYPED_WORDS["img"][:24], "expr": ["root", ["frac", 1, 3], 3]}],
                                    "answer": {"name": "So", "expr": ["or", ["=", "x", 1], ["=", "x", 3]]}})
    plain = [ask_frames.check_frame({"kind": "maths", "title": f"Plain {index}",
                                     "steps": [{"expr": expression} for expression in PLAIN_EXPRESSIONS[at:at + 8]],
                                     "answer": {"expr": 0}}) for index, at in enumerate((0, 8))]
    six = ask_frames.check_frame({"kind": "timeline", "title": "Six steps",
                                  "nodes": [{"when": f"Day {n}", "words": f"Step {n}", "state": "done" if n < 2 else
                                             "next" if n == 2 else ""} for n in range(6)], "today": 2})
    typed = [*found.frames, maths, *plain, six]
    report["typed_kinds"] = [frame.kind if not isinstance(frame, str) else frame for frame in typed]
    messages = [ask_frames.build(frame, frame_id=f"t{index}").messages[0] for index, frame in enumerate(typed)]
    before = board.counts()
    report["applied"] = [board.apply(message) for message in messages]
    pump(1.2)
    after = board.counts()
    report["requests"] = {"allowed_before": before["allowed"], "allowed_after": after["allowed"],
                          "blocked_before": before["blocked"], "blocked_after": after["blocked"]}
    markers = ["pwned", "javascript", "http", "<", "example.com", "onerror", "Dana", "Software", "Events"]
    report["page"] = json.loads(main_world(board, _TYPED_PAGE.replace("__MARKERS__", json.dumps(markers))))
    report["sent"] = {message["id"]: {"kind": message["kind"], "title": message["title"],
                                      "words": [word for word in shown_typed(message["body"])]}
                      for message in messages}
    report["plain_expected"] = [ask_frames.maths_text(step["expr"]) for frame in plain for step in frame.body["steps"]]
    # The page's own refusals: bodies put into board.apply directly (never past Python's checks in the app).
    base = {"type": "frame", "title": "x", "meta": "", "status": "ok", "status_text": "", "status_meta": "",
            "waits": "", "changed": False, "pinned": False, "closed": False}
    good = {message["kind"]: message["body"] for message in messages}
    deep_tree: Any = ["name", "x"]
    for _ in range(6):
        deep_tree = ["-", deep_tree]
    nine = ["+", *([["num", "1"]] * 8)]
    bad_texts = [
        json.dumps(dict(base, id="bad1", kind="table", body=dict(good["table"], rows=[["a", "b"]]))),
        json.dumps(dict(base, id="bad2", kind="table", body=dict(good["table"], html="<b>x</b>"))),
        json.dumps(dict(base, id="bad3", kind="table", body=dict(good["table"], changed=True))),
        json.dumps(dict(base, id="bad4", kind="chart", body=dict(good["chart"], changed=-1))),   # a ghost, no bar
        json.dumps(dict(base, id="bad5", kind="chart", body=dict(good["chart"], bars=[
            {"label": "a", "value": 1, "text": "<b>1</b>"}], changed=-1, was=None))),
        json.dumps(dict(base, id="bad6", kind="chart", body=dict(good["chart"], bars=[
            {"label": "a", "value": 7, "text": "1"}], changed=-1, was=None))).replace(": 7,", ": 1e309,"),
        json.dumps(dict(base, id="bad7", kind="chart", body=dict(good["chart"], bars=[
            {"label": "a", "value": "1", "text": "1"}], changed=-1, was=None))),
        json.dumps(dict(base, id="bad8", kind="timeline", body=dict(good["timeline"], today=9))),
        json.dumps(dict(base, id="bad9", kind="timeline", body=dict(good["timeline"], nodes=[
            {"when": "", "words": "x", "state": "late"}], today=-1))),
        json.dumps(dict(base, id="bad10", kind="maths", body=dict(good["maths"], answer={"name": "", "expr": deep_tree}))),
        json.dumps(dict(base, id="bad11", kind="maths", body=dict(good["maths"], steps=[{"name": "", "expr": nine}] * 7,
                                                                  answer={"name": "", "expr": ["-", ["num", "1"]]}))),
        json.dumps(dict(base, id="bad12", kind="maths", body=dict(good["maths"], answer={
            "name": "", "expr": ["script", ["num", "1"]]}))),
        json.dumps(dict(base, id="bad13", kind="maths", body=dict(good["maths"], answer={
            "name": "", "expr": ["name", "<img>"]}))),
        json.dumps(dict(base, id="bad14", kind="maths", body=dict(good["maths"], answer={
            "name": "", "expr": ["num", "1e5"]}))),
        json.dumps(dict(base, id="bad15", kind="maths", body=dict(good["maths"], answer={
            "name": "", "expr": ["__proto__", ["num", "1"], ["num", "2"]]}))),
    ]
    report["page_refuses"] = [main_world(board, f"board.apply({json.dumps(text)})") for text in bad_texts]
    report["bad_frames"] = main_world(board, "document.querySelectorAll('[id^=\"title-bad\"]').length")
    report["python_refuses"] = []
    for text in bad_texts:
        try:
            protocol.check_message(json.loads(text))
            report["python_refuses"].append(False)
        except protocol.Refused:
            report["python_refuses"].append(True)
    # The widths: a 448, a 300 and a 270 px frame.
    report["layout"] = {}
    for frame_width in (448, 300, 270):
        board.setGeometry(0, 0, frame_width + 30, 900)
        pump(0.8)
        report["layout"][str(frame_width)] = json.loads(main_world(board, _TYPED_LAYOUT))
    # Open big: the view wide (as the app sets it), the board in its own box at its right.
    board.setGeometry(0, 0, 1000, 700)
    pump(0.5)
    report["big"] = {}
    for name, frame_id in (("table", "t0"), ("timeline", f"t{len(typed) - 1}")):
        board.apply({"type": "big", "state": "open", "id": frame_id, "x": 522, "y": 0, "w": 478, "h": 700})
        wait_until(lambda: main_world(board, "board.counts().big") == "open", 5)
        pump(0.6)
        report["big"][name] = json.loads(main_world(board, _TYPED_BIG) or "null")
        board.apply({"type": "big", "state": "close", "id": ""})
        pump(0.6)
    report["fit"] = typed_fit(board, wait_until, pump, main_world)
    report["inside"] = typed_inside(board, pump, main_world)
    after_all = board.counts()
    report["requests"]["allowed_end"] = after_all["allowed"]
    report["requests"]["blocked_end"] = after_all["blocked"]
    settle_end(report, board)
    return report


# Part 6 fixes (board 0): a typed frame opened big - its card, its body (scroll height, height, scroll top), the fit
# level its sizes stepped down to, the parts that should show (rows, the delta line, the source, maths' lines and its
# boxed answer) and those outside the body's visible box, its cue (the cut edges' fades, the thin bar), the last row's
# and the source's place.
_FIT_BIG = r"""JSON.stringify((function () {
  var card = document.querySelector('.big-card');
  if (!card) { return null; }
  var body = card.querySelector(':scope > .f-body'), view = body.getBoundingClientRect();
  var inView = function (el) {
    var r = el.getBoundingClientRect();
    return r.top >= view.top - 0.5 && r.bottom <= view.bottom + 0.5;
  };
  var parts = Array.prototype.slice.call(card.querySelectorAll('tbody tr, .delta, .tb-src, .mx-e, .mx-ans'));
  var rows = card.querySelectorAll('tbody tr'), source = card.querySelector('.tb-src');
  var answer = card.querySelector('.mx-ans'), style = getComputedStyle(body);
  return {card: [Math.round(card.getBoundingClientRect().width), Math.round(card.getBoundingClientRect().height)],
          body: [body.scrollHeight, body.clientHeight, Math.round(body.scrollTop)],
          fit: (card.className.match(/fit-[0-9]/) || ['fit-0'])[0], parts: parts.length,
          hidden: parts.filter(function (el) { return !inView(el); }).map(function (el) {
            return el.className || el.tagName; }),
          cut: [body.classList.contains('cut-start'), body.classList.contains('cut-end')],
          masked: style.maskImage !== 'none' || style.webkitMaskImage !== 'none',
          bar: body.offsetWidth - body.clientWidth,
          lastRow: rows.length ? inView(rows[rows.length - 1]) : null, source: source ? inView(source) : null,
          answer: answer ? inView(answer) : null, cell: rows.length ? getComputedStyle(rows[0].cells[0]).fontSize : '',
          line: card.querySelector('.mx-e') ? getComputedStyle(card.querySelector('.mx-e')).fontSize : '',
          focus: document.activeElement === card};
})())"""
# Part 6 fixes (board 1): each frame shown, every element of its head and body against its content box (px outside
# it, left / right), how many fractions and roots are laid out (stacked) in it, and its maths lines' words.
_TYPED_INSIDE = r"""JSON.stringify((function () {
  var out = [];
  document.querySelectorAll('#board .frame').forEach(function (f) {
    var r = f.getBoundingClientRect(), cs = getComputedStyle(f);
    if (!r.width || !r.height) { return; }
    var left = r.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
    var right = r.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
    var over = [];
    f.querySelectorAll('.f-head *, .f-body *').forEach(function (el) {
      var b = el.getBoundingClientRect();
      if (el.closest('.f-ctl') || (!b.width && !b.height)) { return; }
      if (b.left < left - 0.5 || b.right > right + 0.5) {
        over.push((typeof el.className === 'string' ? el.className : el.tagName) + ' ' + Math.round(b.left - left)
                  + ' ' + Math.round(b.right - right));
      }
    });
    out.push({title: f.querySelector('.f-title').textContent, width: Math.round(r.width), over: over,
              stacked: f.querySelectorAll('.mx-frac, .mx-root').length,
              lines: Array.prototype.map.call(f.querySelectorAll('.mx-e'), function (el) { return el.textContent; })});
  });
  var board = document.getElementById('board');
  return {frames: out, pageCut: board.scrollWidth > board.clientWidth, pairs: board.classList.contains('pairs')};
})())"""
_NARROW_LINE = r"""(function (width) {
  var lines = document.querySelectorAll('#board .mx-e');
  var line = window.__narrowLine || Array.prototype.filter.call(lines, function (el) {
    return el.querySelector('.mx-frac') && el.textContent.indexOf('4,567,890.5') >= 0;
  })[0];
  window.__narrowLine = line;
  line.style.width = width;
  window.dispatchEvent(new Event('resize'));
  return 1;
})(__WIDTH__)"""
_NARROW_SHOWN = r"""JSON.stringify((function () {
  var line = window.__narrowLine, edge = line.getBoundingClientRect();
  return {width: line.clientWidth, stacked: line.querySelectorAll('.mx-frac, .mx-root').length,
          inline: Array.prototype.some.call(line.querySelectorAll('.mx-op'), function (op) {
            return op.textContent === '/'; }),
          text: line.textContent,
          over: Array.prototype.filter.call(line.querySelectorAll('*'), function (el) {
            var box = el.getBoundingClientRect();
            return box.width && (box.left < edge.left - 0.5 || box.right > edge.right + 0.5);
          }).length};
})())"""
# Part 7 fixes: the divisor frame's line (frame "dv0"), given a width through the CSSOM ("" its own), read by
# _NARROW_SHOWN.
_DIVISOR_LINE = r"""(function (width) {
  var frame = document.getElementById('title-dv0').closest('.frame');
  var line = frame.querySelector('.mx-e');
  window.__narrowLine = line;
  line.style.width = width;
  window.dispatchEvent(new Event('resize'));
  return 1;
})(__WIDTH__)"""
BIG_LINES = ("Travel", "Software", "Events", "Hardware", "Office", "Training", "Legal", "Marketing", "Cloud", "Support",
             "Research", "Facilities")


def typed_fit(board: Any, wait_until: Any, pump: Any, main_world: Any) -> dict[str, Any]:
    """Part 6 fixes (board 0): a 12-row table with its change words and source, and an 8-step maths frame (invented),
    each opened big at the card sizes of a 1120x700 and a 900x600 window (860 x 550, 780 x 454): where its body fits
    nothing is hidden; where it cannot, its cue shows and the real keys scroll it (Down, Page Down, Space, Shift+Space,
    Page Up, Up, End, Home)."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from briefing_reader.board import ask_frames

    table = ask_frames.check_frame({
        "kind": "table", "title": "Budget lines", "unit": "USD", "columns": ["Line", "Friday", "Today", "Change"],
        "rows": [[line, 1200 + 100 * n, 1200 + 100 * n + (100 if n == 11 else 0), "+100" if n == 11 else 0]
                 for n, line in enumerate(BIG_LINES)],
        "changed": 11, "change": "+100 Facilities · the other eleven lines unchanged",
        "source": "From Dana's mail, 8:12 AM"})
    maths = ask_frames.check_frame({
        "kind": "maths", "title": "Average spend per line",
        "steps": [{"name": "Add the lines", "expr": ["=", "s", ["+", 1200, 3400, 2100, 800]]},
                  {"name": "Their sum", "expr": ["=", "s", 7500]}, {"name": "Count them", "expr": ["=", "n", 4]},
                  {"name": "Divide", "expr": ["=", "m", ["frac", "s", "n"]]},
                  {"name": "Put them in", "expr": ["=", "m", ["frac", 7500, 4]]},
                  {"name": "Work it out", "expr": ["=", "m", 1875]},
                  {"name": "Per month", "expr": ["=", "p", ["frac", 1875, 12]]},
                  {"name": "Round it", "expr": ["=", "p", 156.25]}],
        "answer": {"name": "So", "expr": ["=", "p", 156.25]}})
    for index, frame in enumerate((table, maths)):
        board.apply(ask_frames.build(frame, frame_id=f"fit{index}").messages[0])
    pump(0.6)
    keys = (("Down", Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier),
            ("PageDown", Qt.Key.Key_PageDown, Qt.KeyboardModifier.NoModifier),
            ("Space", Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier),
            ("ShiftSpace", Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier),
            ("PageUp", Qt.Key.Key_PageUp, Qt.KeyboardModifier.NoModifier),
            ("Up", Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier),
            ("End", Qt.Key.Key_End, Qt.KeyboardModifier.NoModifier),
            ("Home", Qt.Key.Key_Home, Qt.KeyboardModifier.NoModifier))
    found: dict[str, Any] = {}
    for width, height in ((860, 550), (780, 454)):
        board.setGeometry(0, 0, width, height)
        pump(0.5)
        for name, frame_id in (("table", "fit0"), ("maths", "fit1")):
            board.apply({"type": "big", "state": "open", "id": frame_id, "x": width - 503, "y": 0, "w": 503,
                         "h": height})
            wait_until(lambda: main_world(board, "board.counts().big") == "open", 5)
            pump(0.6)
            case: dict[str, Any] = {"top": json.loads(main_world(board, _FIT_BIG) or "null"), "keys": {}}
            board.web_view().setFocus(Qt.FocusReason.OtherFocusReason)
            pump(0.2)
            for word, key, modifiers in keys:
                QTest.keyClick(board.web_view().focusProxy(), key, modifiers)
                pump(0.2)
                shown = json.loads(main_world(board, _FIT_BIG) or "null")
                case["keys"][word] = shown["body"][2] if shown else None
                if word == "End":
                    case["end"] = shown
            case["state"] = main_world(board, "board.counts().big")
            found[f"{name}_{width}x{height}"] = case
            board.apply({"type": "big", "state": "close", "id": ""})
            pump(0.6)
    return found


def typed_inside(board: Any, pump: Any, main_world: Any) -> dict[str, Any]:
    """Part 6 fixes (board 1): one answer's maths frames (invented) - the mean of five budget lines as a fraction,
    paired with a chart; a four-term sum of seven-digit figures in a fraction and alone beside long step names; a long
    radicand and an indexed root - on the desk's board (its bar: the frames area is the view less 55 px) at a pair of
    253 px frames (1232x640), one of 300, 270 and 448 px: every element of every frame against its content box."""
    from briefing_reader.board import ask_frames
    from briefing_reader.board import feed as feed_mod

    raw = [("mean", {"kind": "maths", "title": "Mean of the five lines",
                     "steps": [{"name": "Add, then divide",
                                "expr": ["=", "mean", ["frac", ["+", 1200, 3400, 2100, 800, 1500], 5]]}],
                     "answer": {"name": "So", "expr": ["=", "mean", 1800]}}),
           ("chart", {"kind": "chart", "title": "The five lines", "unit": "USD",
                      "bars": [{"label": label, "value": value} for label, value in
                               (("Travel", 1200), ("Software", 3400), ("Events", 2100), ("Hardware", 800),
                                ("Office", 1500))]}),
           ("long", {"kind": "maths", "title": "Long working",
                     "steps": [{"name": "Multiply both sides by x",
                                "expr": ["=", "mean", ["frac", ["+", 1234567.5, 2345678.5, 3456789.5, 4567890.5], 4]]},
                               {"name": "Add every figure",
                                "expr": ["=", "total", ["+", 1234567, 2345678, 3456789, 4567890]]},
                               {"name": "Take the square root now",
                                "expr": ["root", ["+", ["pow", "alphabetical", 2], ["pow", "betamaxwells", 2]]]},
                               {"name": "Cube root",
                                "expr": ["=", "r", ["root", ["+", 1234567, 2345678, 3456789], 3]]}],
                     "answer": {"name": "So", "expr": ["=", "x", 1234567.5]}})]
    feed = feed_mod.BoardFeed()
    messages: list[dict[str, Any]] = []
    report: dict[str, Any] = {}
    for source, frame_raw in raw:
        frame = ask_frames.check_frame(frame_raw)
        frame_id = feed.frame_id(1, source, "", frame.key)
        messages += feed.put(1, source, ask_frames.build(frame, frame_id=frame_id).messages, when="9:41 AM",
                             topic="The budget lines", key=frame.key)
        if source == "long":                                     # its first line as Python reads it (inline: a/b)
            report["fallback_expected"] = ask_frames.maths_text(frame.body["steps"][0]["expr"])
    board.apply({"type": "clear"})
    report["applied"] = [board.apply(message) for message in messages]
    for name, width in (("pair", 518), ("300", 300), ("270", 270), ("448", 448)):
        board.setGeometry(0, 0, width + 55, 900)                 # the bar's strip and pads: 28 + 15 + 12 px
        pump(0.8)
        report[name] = json.loads(main_world(board, _TYPED_INSIDE))
    # The fallback itself (a safety net: the shrink and wrap keep every line above): a line made narrower than its
    # fraction can be (by the probe, through the CSSOM) reads it inline once measured, laid out again once wide again.
    report["fallback"] = []
    for width in ("14px", ""):
        main_world(board, _NARROW_LINE.replace("__WIDTH__", json.dumps(width)))
        pump(0.5)
        report["fallback"].append(json.loads(main_world(board, _NARROW_SHOWN)))
    # Part 7 fixes (part 6 fixes' carried-over low): a fraction that divides - laid out (no brackets), then in a line
    # too narrow for it (inline: "6 ÷ (1/2)", as Python reads it), then laid out again.
    divisor = ask_frames.check_frame({"kind": "maths", "title": "Divide by a half",
                                      "steps": [{"name": "", "expr": ["/", 6, ["frac", 1, 2]]}],
                                      "answer": {"name": "So", "expr": 12}})
    board.apply({"type": "clear"})
    report["divisor_applied"] = board.apply(ask_frames.build(divisor, frame_id="dv0").messages[0])
    board.setGeometry(0, 0, 448 + 30, 900)
    pump(0.8)
    report["divisor_expected"] = ask_frames.maths_text(divisor.body["steps"][0]["expr"])
    report["divisor"] = []
    for width in ("", "2px", ""):                              # (2 px: narrower than even its "1" over "2")
        main_world(board, _DIVISOR_LINE.replace("__WIDTH__", json.dumps(width)))
        pump(0.5)
        report["divisor"].append(json.loads(main_world(board, _NARROW_SHOWN)))
    return report


# ---- the canvas (PLAN-canvas part 1): the orb's forms and states in the real page, and nothing from data in an
# attribute ---------------------------------------------------------------------------------------------------------

CANVAS_HOSTILE = {
    "clock": "<img src=http://example.com/c.png>", "word": "<script>window.pwned=31</script>",
    "hold": "\"><b onclick=pwn()>", "rec": "javascript:pwned=32 <iframe src=x>",
    "wake": "' onmouseover='pwned=33",
}
CANVAS_BUTTONS = ["history", "briefing", "approvals", "followups", "meetings", "agents", "live", "memory", "settings",
                  "desktop", "minimize", "maximize", "close", "mute", "day"]

# What the page shows of the canvas: the orb as board.js draws it now, in numbers (board.orb(): its form, how far it
# is turned to face and its rings are shut, its tilt, the state's lean, its levels, where it is and how big, its trip,
# each ring's rate) and in pixels (its own canvas: how much of it is lit, amber, cyan); the corners' words; every
# attribute in the canvas layer; its buttons.
CANVAS_LOOK = r"""JSON.stringify((function () {
  var canvas = document.getElementById('canvas'), look = board.orb();
  if (!canvas || !look) { return {made: false, html: document.documentElement.className.split(' ').sort()}; }
  function box(n) { var r = n.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; }
  function text(sel) { var n = canvas.querySelector(sel); return n && !n.hidden ? n.textContent : ''; }
  var orb = document.getElementById('orb'), pic = orb.querySelector('canvas');
  var copy = document.createElement('canvas');   // read from a copy made for reading (the orb's own canvas is not)
  copy.width = pic.width;
  copy.height = pic.height;
  var reader = copy.getContext('2d', {willReadFrequently: true});
  reader.drawImage(pic, 0, 0);
  var data = reader.getImageData(0, 0, pic.width, pic.height).data;
  var lit = 0, amber = 0, cyan = 0;
  for (var i = 0; i < data.length; i += 16) {
    var r = data[i], g = data[i + 1], b = data[i + 2];
    if (data[i + 3] > 48) {
      lit += 1;
      if (r > 170 && b < 140 && r > b * 1.5) { amber += 1; } else if (b > r * 1.3 && g > r * 1.2) { cyan += 1; }
    }
  }
  var attrs = [];
  Array.prototype.forEach.call(canvas.querySelectorAll('*'), function (n) {
    for (var j = 0; j < n.attributes.length; j += 1) { attrs.push(n.attributes[j].name + '=' + n.attributes[j].value); }
  });
  return {
    made: true, orb: look, cls: orb.className.split(' ').filter(Boolean).sort(),
    html: document.documentElement.className.split(' ').sort(),
    pixels: {lit: lit, amber: amber, cyan: cyan}, pic: [pic.width, pic.height], win: [innerWidth, innerHeight],
    elements: orb.querySelectorAll('*').length,
    texts: {state: text('.c-state'), clock: text('span.c-clock') || text('button.c-clock'), hold: text('.c-hold'),
            rec: text('.c-rec'), wake: canvas.querySelectorAll('.c-state')[1] && !canvas.querySelectorAll('.c-state')[1].hidden
              ? canvas.querySelectorAll('.c-state')[1].textContent : ''},
    foreign: canvas.querySelectorAll('img, script, iframe, a, style, link, object, embed, form, input, b').length,
    attrs: attrs,
    buttons: Array.prototype.filter.call(canvas.querySelectorAll('button'), function (b) { return !b.hidden; })
      .map(function (b) { return b.textContent || b.getAttribute('aria-label'); }),
    bars: canvas.querySelectorAll('.c-meter i.on').length,
    level: (function () { var n = canvas.querySelector('.c-bl > span.c-mono'); return n && !n.hidden ? n.textContent : ''; })(),
    board: box(document.body), frame: (function () { var f = document.querySelector('#board .frame'); return f ? box(f) : null; })(),
    counts: board.counts(), pwned: typeof window.pwned
  };
})())"""

# A move, sampled in the page every frame: how far the orb is turned to face (flat) and its rings are shut, where it
# is and its trail - so what a move does on its way (past its end and back, or not) can be read afterwards.
CANVAS_WATCH = r"""(function () {
  window.__move = [];
  var began = performance.now();
  (function sample() {
    var look = board.orb();
    window.__move.push([Math.round(performance.now() - began), look.flat, look.shut, look.centre[0], look.centre[1],
                        look.size, look.ghosts, look.travelling ? 1 : 0]);
    if (performance.now() - began < %d) { requestAnimationFrame(sample); } else { window.__moved = true; }
  })();
})()"""

# The page's own channel asked for words the list does not have (a second channel, as script in the page could make).
CANVAS_FORGED = r"""(function () {
  new QWebChannel(qt.webChannelTransport, function (channel) {
    var b = channel.objects.bridge;
    b.post('go', JSON.stringify({to: 'format_disk'}));
    b.post('go', JSON.stringify({to: 'briefing', then: 'approve_all'}));
    b.post('go', 'x');
    b.post('go', JSON.stringify({to: 'settings'}));
    window.__forged = true;
  });
})()"""


def canvas_case(make: Any, wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    """The canvas in the real page: its three forms and the moves between them at each motion level, the states, the
    corners' words as text (hostile ones among them) with nothing of them in an attribute, the buttons' words through
    the bridge (forged ones refused), hostile orb and canvas messages refused by the page itself, and the board's
    frames in the right-hand part with the orb in its corner."""
    report: dict[str, Any] = {}
    host, board = make(canvas=True)
    host.resize(1280, 800)
    board.setGeometry(0, 0, 1280, 800)
    went: list[str] = []
    board.goRequested.connect(went.append)
    cleared: list[int] = []
    board.clearRequested.connect(lambda: cleared.append(1))
    host.show()
    wait_until(board.is_ready, 40)
    report["ready"] = board.is_ready()
    board.set_motion("lively")

    def look() -> dict[str, Any]:
        return json.loads(main_world(board, CANVAS_LOOK))

    def watch(ms: int) -> list[list[float]]:
        """The move just asked for, sampled in the page each frame for ``ms``."""
        main_world(board, "window.__moved = false; 0")
        main_world(board, CANVAS_WATCH.replace("%d", str(ms)))
        wait_until(lambda: main_world(board, "String(window.__moved === true)") == "true", ms / 1000 + 5)
        return json.loads(main_world(board, "JSON.stringify(window.__move)"))

    def orb(state: str = "idle", form: str = "rings", **more: Any) -> bool:
        message = {"type": "orb", "state": state, "form": form, "muted": False, "offline": False, "dim": False,
                   "level": 0, "voice": 0}
        message.update(more)
        return board.apply(message)

    def corners(**more: Any) -> bool:
        message = {"type": "canvas", "clock": "Tue 17 Nov · 9:41 AM", "word": "Standby", "tone": "holo",
                   "approvals": 0, "buttons": CANVAS_BUTTONS, "briefing_new": False, "hold": "", "rec": "", "wake": "",
                   "meter": True}
        message.update(more)
        return board.apply(message)

    report["before"] = look()                              # a board that was sent no orb has no canvas at all
    report["sent"] = [corners(), orb()]
    pump(1.4)                                              # the orb's own entrance is over
    report["rings"] = look()

    # the states, in the rings
    orb("listening", level=62)
    pump(0.9)
    report["listening"] = look()
    orb("thinking")
    pump(0.9)
    report["thinking"] = look()
    orb("needs")
    pump(0.5)
    report["needs"] = look()
    orb("waiting")
    pump(1.2)
    report["waiting"] = look()
    orb("idle", muted=True)
    pump(0.5)
    report["muted"] = look()
    orb("idle", offline=True)
    pump(0.3)
    report["offline"] = look()
    orb("idle")
    pump(0.7)

    # the rings -> the badge (lively: the turn goes a little past and back), then -> the corner (the curve, the trail)
    orb("speaking", "badge", voice=80)
    report["turn"] = watch(1500)
    report["badge"] = look()
    orb("idle", "corner")
    report["trip"] = watch(1300)
    report["corner"] = look()
    orb("idle", "rings")
    report["back"] = watch(1700)
    report["rings_again"] = look()

    # calm: the same moves, x1.6, no overshoot, no trail
    board.set_motion("calm")
    orb("speaking", "badge", voice=40)
    report["calm_turn"] = watch(2400)
    orb("idle", "corner")
    report["calm_trip"] = watch(1900)
    report["calm_corner"] = look()

    # off: cuts
    board.set_motion("off")
    orb("idle", "rings")
    pump(0.05)
    report["off_cut"] = look()
    pump(0.4)
    orb("speaking", "badge", voice=40)
    pump(0.3)
    report["off_badge"] = look()
    pump(0.4)
    report["off_still"] = json.loads(main_world(board, "JSON.stringify(board.orb().frames)"))
    board.set_motion("lively")
    orb("idle", "rings")
    pump(1.2)

    # the corners' words: hostile ones are text, and nothing of them is in any attribute
    report["hostile_sent"] = corners(approvals=3, tone="amber", briefing_new=True, **CANVAS_HOSTILE)
    pump(0.3)
    report["hostile"] = look()

    # the buttons: a real press of each one that shows posts its own word; forged words are dropped
    corners(hold="Sent", rec="Rec 0:42")
    pump(0.2)
    report["pressed"] = json.loads(main_world(board, r"""JSON.stringify((function () {
      var pressed = [];
      Array.prototype.forEach.call(document.querySelectorAll('#canvas button'), function (b) {
        if (!b.hidden) { pressed.push(b.textContent || b.getAttribute('aria-label')); b.click(); }
      });
      return pressed;
    })())"""))
    pump(0.4)
    report["went"] = list(went)
    del went[:]
    report["console_before_forged"] = {key: value for key, value in board.counts().items()
                                       if key.startswith("console:")}
    main_world(board, CANVAS_FORGED)
    wait_until(lambda: main_world(board, "String(window.__forged === true)") == "true", 5)
    pump(0.3)
    report["forged_went"] = list(went)
    report["requests"] = {key: value for key, value in board.counts().items()
                          if key.startswith(("accepted:go", "dropped:go", "why:"))}

    # hostile messages put into the page directly (past Python's check): the page refuses each and changes nothing
    tries = {
        "state": {"type": "orb", "state": "evil", "form": "rings", "muted": False, "offline": False, "dim": False, "away": False,
                  "level": 0, "voice": 0},
        "form": {"type": "orb", "state": "idle", "form": "cube", "muted": False, "offline": False, "dim": False, "away": False,
                 "level": 0, "voice": 0},
        "level": {"type": "orb", "state": "idle", "form": "rings", "muted": False, "offline": False, "dim": False, "away": False,
                  "level": 101, "voice": 0},
        "fraction": {"type": "orb", "state": "idle", "form": "rings", "muted": False, "offline": False, "dim": False, "away": False,
                     "level": 0, "voice": 0.5},
        "extra": {"type": "orb", "state": "idle", "form": "rings", "muted": False, "offline": False, "dim": False, "away": False,
                  "level": 0, "voice": 0, "html": "<b>x</b>"},
        "button": {"type": "canvas", "clock": "", "word": "", "tone": "holo", "approvals": 0,
                   "buttons": ["history", "wipe"], "briefing_new": False, "hold": "", "rec": "", "wake": "",
                   "meter": False},
        "twice": {"type": "canvas", "clock": "", "word": "", "tone": "holo", "approvals": 0,
                  "buttons": ["history", "history"], "briefing_new": False, "hold": "", "rec": "", "wake": "",
                  "meter": False},
        "long": {"type": "canvas", "clock": "", "word": "x" * 41, "tone": "holo", "approvals": 0, "buttons": [],
                 "briefing_new": False, "hold": "", "rec": "", "wake": "", "meter": False},
        "tone": {"type": "canvas", "clock": "", "word": "", "tone": "url(x)", "approvals": 0, "buttons": [],
                 "briefing_new": False, "hold": "", "rec": "", "wake": "", "meter": False},
        "label": {"type": "canvas", "clock": "", "word": "", "tone": "holo", "approvals": 0, "buttons": [],
                  "briefing_new": False, "hold": "", "rec": "", "wake": "", "meter": False, "label": "Approve"},
    }
    report["page_refused"] = {name: main_world(board, f"String(board.apply({json.dumps(json.dumps(message))}))")
                              for name, message in tries.items()}
    report["after_refused"] = look()

    # the board's frames in the right-hand part, the orb in its corner; folded: the board away, the orb back
    corners()
    board.apply({"type": "frame", "id": "c1", "kind": "text", "title": "Dana's budget", "meta": "Now",
                 "body": {"text": "Made-up words for the canvas probe."}})
    board.apply({"type": "group", "id": "g1", "time": "9:41 AM", "topic": "Dana's budget", "frames": ["c1"],
                 "older": False})
    board.apply({"type": "bar", "frames": 1, "waits": 0, "pinned": 0, "compact": False})
    board.apply({"type": "desk", "state": "enter"})
    orb("idle", "corner")
    pump(1.8)
    report["with_frames"] = look()
    # open big on the canvas: no box to keep, no backdrop - the frame grows over the canvas, the orb and the corners
    # dim with the board, and it goes back
    seen_big: list[str] = []
    board.bigStep.connect(seen_big.append)
    board.apply({"type": "big", "state": "open", "id": "c1", "x": 0, "y": 0, "w": 1280, "h": 800})
    pump(0.9)
    report["big_open"] = json.loads(main_world(board, r"""JSON.stringify((function () {
      var card = document.querySelector('#big .big-card'), r = card ? card.getBoundingClientRect() : null;
      return {state: board.counts().big, card: r ? [r.left, r.top, r.width, r.height] : null,
              wide: document.body.classList.contains('wide'), dim: document.body.classList.contains('dim'),
              canvas: getComputedStyle(document.getElementById('canvas')).opacity,
              title: card ? card.querySelector('.f-title').textContent : '',
              body: (function () { var b = document.body.getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; })()};
    })())"""))
    board.apply({"type": "big", "state": "close", "id": ""})
    pump(0.9)
    report["big_shut"] = json.loads(main_world(board, r"""JSON.stringify({state: board.counts().big,
      layer: !!document.querySelector('#big .big-card'), dim: document.body.classList.contains('dim'),
      canvas: getComputedStyle(document.getElementById('canvas')).opacity})"""))
    report["big_steps"] = list(seen_big)
    report["clear_pressed"] = main_world(board, r"""(function () {
      var hit = '';
      Array.prototype.forEach.call(document.querySelectorAll('#canvas button'), function (b) {
        if (!b.hidden && b.textContent === 'Clear view') { hit = b.textContent; b.click(); }
      });
      return hit;
    })()""")
    pump(0.3)
    report["cleared"] = len(cleared)
    board.apply({"type": "desk", "state": "leave"})
    orb("idle", "rings")
    pump(1.4)
    report["folded"] = look()
    counts = board.counts()
    report["blocked"] = counts.get("blocked", {})
    report["allowed"] = counts.get("allowed", {})
    report["console"] = {key: value for key, value in counts.items() if key.startswith("console:")}
    report["messages_refused"] = {key: value for key, value in counts.items() if key.startswith("refused:")}
    settle_end(report, board)
    return report


# ---- the canvas, part 2 (PLAN-canvas part 2): frames across the screen - the layout at every size, each kind in the
# new style with long words, the arrival at each motion level, the task card, Tidy, Clear view, open big, the keys --------

CANVAS2_MARKS = ("<img", "<script", "javascript:", "onerror", "onmouseover", "pwned", "example.com", "http:")
CANVAS2_SIZES = ((900, 600), (1280, 800), (1920, 1080), (2560, 1440))
CANVAS2_COUNTS = (1, 3, 6, 9, 12, 24)
LONG_TITLE = "A sixty character title that goes on and on to its very cap!"          # 60 characters
LONG_CELL = "A forty character cell, cut at its cap!!"                                # 40 characters

# What the canvas shows, checked in the page: the layout in numbers (board.layout()) and, of everything it places - the
# frames and tiles, the groups' heads, the task card -: any two that overlap, any that leaves the frames' area, any on
# the orb's corner or under the area's end (the caption's strip), any frame neither placed nor folded, and every
# attribute in the board (for the data's marks).
CANVAS2_LOOK = r"""JSON.stringify((function (marks) {
  var look = board.layout();
  if (!look) { return null; }
  var boxes = [];
  look.items.forEach(function (item) {
    if (item.shown && !item.over) { boxes.push({name: item.id, box: item.box, kind: item.kind}); }
  });
  look.heads.forEach(function (head) { boxes.push({name: 'head:' + head.group, box: head.box, kind: 'head'}); });
  if (look.card) { boxes.push({name: 'card', box: look.card.box, kind: 'card'}); }
  function hits(a, b) {
    return a[0] < b[0] + b[2] - 0.5 && b[0] < a[0] + a[2] - 0.5 && a[1] < b[1] + b[3] - 0.5 && b[1] < a[1] + a[3] - 0.5;
  }
  var overlaps = [], outside = [], onOrb = [], empty = [];
  var area = look.area, orb = [look.orb[0], look.orb[1], look.win[0] - look.orb[0], look.win[1] - look.orb[1]];
  boxes.forEach(function (one, index) {
    for (var other = index + 1; other < boxes.length; other += 1) {
      if (hits(one.box, boxes[other].box)) { overlaps.push([one.name, boxes[other].name]); }
    }
    var b = one.box;
    if (b[0] < area[0] - 1 || b[1] < area[1] - 1 || b[0] + b[2] > area[0] + area[2] + 1
        || b[1] + b[3] > area[1] + area[3] + 1) { outside.push(one.name); }
    if (hits(b, orb)) { onOrb.push(one.name); }
    if (b[2] < 40 || b[3] < 20) { empty.push(one.name); }
  });
  var attrs = [], hit = [];
  Array.prototype.forEach.call(document.querySelectorAll('#board *, #big *'), function (n) {
    for (var j = 0; j < n.attributes.length; j += 1) {
      var text = n.attributes[j].name + '=' + n.attributes[j].value;
      marks.forEach(function (mark) { if (text.indexOf(mark) >= 0) { hit.push(text.slice(0, 80)); } });
      if (n.attributes[j].name !== 'class' && n.attributes[j].name !== 'style') { attrs.push(n.attributes[j].name); }
    }
  });
  var names = {};
  attrs.forEach(function (name) { names[name] = (names[name] || 0) + 1; });
  return {
    win: look.win, area: area, grid: look.grid, orb: look.orb, untidy: look.untidy, over: look.over, packs: look.packs,
    card: look.card, heads: look.heads, items: look.items, filter: look.filter, lines: look.lines,
    full: look.items.filter(function (i) { return i.shown && i.kind === 'full'; }).length,
    tiles: look.items.filter(function (i) { return i.shown && i.kind === 'tile'; }).length,
    lost: look.items.filter(function (i) { return !i.shown || i.over; }).map(function (i) { return i.id; }),
    overlaps: overlaps, outside: outside, on_orb: onOrb, empty: empty,
    strip: look.win[1] - (area[1] + area[3]),
    scroll: [getComputedStyle(document.getElementById('board')).overflowY, document.getElementById('board').scrollTop,
             document.documentElement.scrollHeight - innerHeight, document.documentElement.scrollTop],
    attr_names: names, attr_hits: hit, pwned: typeof window.pwned,
    foreign: document.querySelectorAll('#board script, #board iframe, #board a, #board style, #board link, '
      + '#board object, #board embed, #board form, #board input').length,
    bar: getComputedStyle(document.getElementById('bar') || document.body).display,
    strip_shown: getComputedStyle(document.getElementById('strip')).display,
    buttons: Array.prototype.filter.call(document.querySelectorAll('#canvas button'), function (b) { return !b.hidden; })
      .map(function (b) { return b.textContent || b.getAttribute('aria-label'); }),
    counts: board.counts()
  };
})(%s))"""

# Each frame as it is drawn: its own style (the mock's outline, corners, glows, caps heading, mono labels), whether
# any word of it is cut sideways, whether its body is whole or says it is cut, the sizes of its text.
CANVAS2_STYLE = r"""JSON.stringify((function () {
  var found = [];
  Array.prototype.forEach.call(document.querySelectorAll('#board .frame'), function (el) {
    var style = getComputedStyle(el), body = el.querySelector(':scope > .f-body'), title = el.querySelector('.f-title');
    var box = el.getBoundingClientRect(), head = title.getBoundingClientRect();
    var glow = getComputedStyle(el, '::before').boxShadow, sizes = [];
    Array.prototype.forEach.call(el.querySelectorAll('.f-body *'), function (n) {
      if (n.children.length === 0 && n.textContent.trim() && n.getClientRects().length) {
        sizes.push(parseFloat(getComputedStyle(n).fontSize));
      }
    });
    var wide = [];
    Array.prototype.forEach.call(el.querySelectorAll('.f-head *, .f-body *'), function (n) {
      var r = n.getBoundingClientRect();
      if (r.width > 0 && (r.left < box.left - 0.5 || r.right > box.right + 0.5)) { wide.push(n.className || n.tagName); }
    });
    found.push({
      id: title.id.slice(6), cls: el.className.split(' ').filter(Boolean).sort(), radius: style.borderTopLeftRadius,
      border: style.borderTopColor, ground: style.backgroundColor, glow: glow, position: style.position,
      title: {font: getComputedStyle(title).fontFamily, size: getComputedStyle(title).fontSize,
              caps: getComputedStyle(title).textTransform, weight: getComputedStyle(title).fontWeight,
              color: getComputedStyle(title).color, inside: head.top >= box.top - 0.5 && head.bottom <= box.bottom + 0.5
                && head.left >= box.left - 0.5 && head.right <= box.right + 0.5, text: title.textContent},
      meta: (function () { var m = el.querySelector('.f-meta'), s = getComputedStyle(m);
                           return {font: s.fontFamily, caps: s.textTransform, size: s.fontSize}; })(),
      body: body ? {fits: body.scrollHeight <= body.clientHeight + 1, cut: el.classList.contains('cut'),
                    more: getComputedStyle(el.querySelector('.f-cut') || el).display,
                    sideways: body.scrollWidth > body.clientWidth + 1} : null,
      smallest: sizes.length ? Math.min.apply(null, sizes) : 0, wide: wide.slice(0, 4),
      glyph: getComputedStyle(el.querySelector('.f-glyph')).display
    });
  });
  return found;
})())"""

# A stretch of the page's own time, sampled every frame: what boots, decodes and counts, where a frame is (a glide),
# how many of Clear view's lines fly, the orb's form.
CANVAS2_WATCH = r"""(function (ms, id) {
  window.__c2 = []; window.__c2done = false;
  var began = performance.now();
  (function sample(now) {
    var el = id ? document.getElementById('title-' + id).closest('.frame') : null;
    var value = document.querySelector('#board .ch-v'), figure = document.querySelector('#board .nb-fig');
    var boots = Array.prototype.map.call(document.querySelectorAll('#board .frame.boot'), function (n) {
      return [n.querySelector('.f-title').id.slice(6), parseFloat(n.style.getPropertyValue('--d')) || 0]; });
    var r = el ? el.getBoundingClientRect() : null;
    window.__c2.push({t: Math.round(now - began), boots: boots, decoding: document.querySelectorAll('.d-fx').length,
                      value: value ? value.textContent : '', figure: figure ? figure.textContent : '',
                      box: r ? [r.left, r.top, r.width, r.height] : null,
                      lines: document.querySelectorAll('#board .fx-line').length,
                      folding: document.querySelectorAll('#board .frame.folding').length,
                      form: board.orb().form, big: (function () { var c = document.querySelector('#big .big-card');
                        if (!c) { return null; } var b = c.getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; })()});
    if (now - began < ms) { requestAnimationFrame(sample); } else { window.__c2done = true; }
  })(performance.now());
})(%d, %s)"""


def canvas2_frames(count: int, hostile: bool = False) -> list[dict[str, Any]]:
    """``count`` made-up frames of every kind in turn (short, ordinary words; ``hostile``: markup among them)."""
    from briefing_reader.board import ask_frames

    evil = "<img src=x onerror=window.pwned=41>" if hostile else "Dana Whitfield"
    chart = ask_frames.check_frame({
        "kind": "chart", "title": "Q4 budget by line", "unit": "USD", "changed": 1, "was": 2900,
        "change": "+500 Software · was 2.9k on Friday",
        "bars": [{"label": "Travel", "value": 1200}, {"label": "Software", "value": 3400},
                 {"label": "Events", "value": 2100}, {"label": "Hardware", "value": 800}]})
    table = ask_frames.check_frame({
        "kind": "table", "title": "Budget lines", "unit": "USD", "changed": 1, "change": "+500 Software",
        "source": "Dana's mail", "columns": ["Line", "Friday", "Today"],
        "rows": [["Travel", 1200, 1200], ["Software", 2900, 3400], ["Events", 2100, 2100]]})
    timeline = ask_frames.check_frame({
        "kind": "timeline", "title": "City Hack dates", "today": 1,
        "nodes": [{"when": "Mon 9 Nov", "words": "Registration", "state": "done"},
                  {"when": "Thu 19 Nov", "words": "Idea due", "state": "next"},
                  {"when": "Sat 21 Nov", "words": "Hack day", "state": "due"}]})
    maths = ask_frames.check_frame({
        "kind": "maths", "title": "Average per line", "steps": [{"name": "Sum", "expr": ["+", 1200, 3400, 2100]}],
        "answer": {"name": "Mean", "expr": ["=", "m", ["frac", 6700, 3]]}})
    kinds: list[dict[str, Any]] = [
        {"kind": "mail_thread", "title": "Re: Budget numbers", "meta": "Work",
         "body": {"sender": evil, "time": "8:12 AM", "text": "Here are the Q4 numbers. Line 14 changed since Friday."},
         "waits": "A reply waits for your OK"},
        {"kind": "calendar", "title": "Today", "meta": "3 meetings",
         "body": {"first": 0, "last": 0, "now_min": -1, "ticks": [], "spans": [], "now": "",
                  "rows": [{"time": "10:00", "title": "AI-Trader check-in", "meta": "In 19 min", "state": "next"},
                           {"time": "13:00", "title": "Study group", "meta": "", "state": ""},
                           {"time": "16:00", "title": "Project check-in", "meta": "", "state": ""}]}},
        dict(ask_frames.build(chart, frame_id="x").messages[0]),
        dict(ask_frames.build(ask_frames.number_frame(chart), frame_id="x").messages[0]),
        {"kind": "todos", "title": "To-dos", "meta": "3",
         "body": {"rows": [{"text": "Send budget comments to Dana", "meta": "Waits", "state": "waits"},
                           {"text": "LING essay draft", "meta": "Thu", "state": "open"},
                           {"text": "Print boarding pass", "meta": "", "state": "done"}]}},
        dict(ask_frames.build(table, frame_id="x").messages[0]),
        dict(ask_frames.build(timeline, frame_id="x").messages[0]),
        dict(ask_frames.build(maths, frame_id="x").messages[0]),
        {"kind": "text", "title": "City Hack: what changed", "meta": "2 sources",
         "body": {"text": "The schedule moved to Saturday.",
                  "rows": [{"lead": "1", "text": "City Hack schedule", "meta": "city-hack.example.org"}]}},
        {"kind": "mail_list", "title": "Follow-ups", "meta": "2",
         "body": {"rows": [{"sender": "Prof. Ana Ruiz", "time": "30 h", "subject": "Lab section moved",
                            "meta": "Waiting", "unread": True}]}},
        {"kind": "picture", "title": "City Hack schedule", "meta": "Page", "body": {"caption": "city-hack.example.org"}},
        {"kind": "mail_thread", "title": "Lab section moved", "meta": "", "status": "failed",
         "status_text": "The mail could not be read.", "body": {"sender": "", "time": "", "text": ""}},
    ]
    return [dict(kinds[index % len(kinds)], type="frame", id=f"f{index + 1}") for index in range(count)]


def canvas2_long() -> list[dict[str, Any]]:
    """Each kind with long words: a 60-character title, 40-character cells, 8 bars, 12 rows, markup among them."""
    from briefing_reader.board import ask_frames

    mark = "<img src=http://example.com/x.png onerror=window.pwned=42>"
    cell = LONG_CELL
    table = ask_frames.check_frame({
        "kind": "table", "title": LONG_TITLE, "unit": "USD", "changed": 2, "change": "+500 " + "x" * 60,
        "source": "S" * 60, "columns": ["Line " + "c" * 19, "Friday", "Today", "Change"],
        "rows": [[cell, 1200 + row, 3400, "<b>" + "z" * 30] for row in range(12)]})
    chart = ask_frames.check_frame({
        "kind": "chart", "title": LONG_TITLE, "unit": "USD", "changed": 7, "was": 2900, "change": "+500 " + "y" * 70,
        "bars": [{"label": "A label of 24 characters"[:24], "value": 1000 + 300 * bar} for bar in range(8)]})
    timeline = ask_frames.check_frame({
        "kind": "timeline", "title": LONG_TITLE, "today": 3,
        "nodes": [{"when": "Thu 19 November 2026 9am"[:24], "words": cell, "state": ("done", "next", "due", "")[n % 4]}
                  for n in range(8)]})
    maths = ask_frames.check_frame({
        "kind": "maths", "title": LONG_TITLE,
        "steps": [{"name": "A step name of 24 chars!"[:24], "expr": ["+", 1234567, 2345678, 3456789, step]}
                  for step in range(8)],
        "answer": {"name": "Answer", "expr": ["=", "x", ["frac", 7037034, 3]]}})
    rows8 = [{"time": "10:00 - 11:30 AM", "title": cell, "meta": "In 19 min", "state": ""} for _ in range(8)]
    frames = [
        {"kind": "mail_thread", "title": LONG_TITLE, "meta": "M" * 40,
         "body": {"sender": mark, "time": "8:12 AM", "text": "Long words. " * 30}, "waits": "W" * 60},
        {"kind": "calendar", "title": LONG_TITLE, "meta": "8 meetings",
         "body": {"first": 0, "last": 0, "now_min": -1, "ticks": [], "spans": [], "now": "", "rows": rows8}},
        {"kind": "mail_list", "title": LONG_TITLE, "meta": "8",
         "body": {"rows": [{"sender": cell, "time": "30 h", "subject": LONG_TITLE, "meta": "Waiting", "unread": True}
                           for _ in range(8)]}},
        {"kind": "todos", "title": LONG_TITLE, "meta": "8",
         "body": {"rows": [{"text": LONG_TITLE, "meta": "This week", "state": ("waits", "open", "done")[n % 3]}
                           for n in range(8)]}},
        {"kind": "text", "title": LONG_TITLE, "meta": "8 sources",
         "body": {"text": "Research words. " * 24,
                  "rows": [{"lead": str(n + 1), "text": LONG_TITLE, "meta": cell} for n in range(8)]}},
        {"kind": "picture", "title": LONG_TITLE, "meta": "Page", "body": {"caption": LONG_TITLE}},
        dict(ask_frames.build(table, frame_id="x").messages[0]),
        dict(ask_frames.build(chart, frame_id="x").messages[0]),
        dict(ask_frames.build(ask_frames.number_frame(chart), frame_id="x").messages[0]),
        dict(ask_frames.build(timeline, frame_id="x").messages[0]),
        dict(ask_frames.build(maths, frame_id="x").messages[0]),
        {"kind": "mail_thread", "title": LONG_TITLE, "meta": "", "status": "failed", "status_text": "F" * 160,
         "body": {"sender": "", "time": "", "text": ""}},
        {"kind": "picture", "title": LONG_TITLE, "meta": "", "status": "empty", "status_text": "Getting the picture",
         "status_meta": "Started 9:41 AM", "body": {"caption": ""}},
    ]
    return [dict(frame, type="frame", id=f"k{index + 1}") for index, frame in enumerate(frames)]


def _canvas2_start(make: Any, wait_until: Any, pump: Any, size: tuple[int, int]) -> tuple[Any, Any]:
    host, board = make(canvas=True)
    host.resize(*size)
    board.setGeometry(0, 0, *size)
    host.show()
    wait_until(board.is_ready, 40)
    board.apply({"type": "canvas", "clock": "Tue 17 Nov · 9:41 AM", "word": "Standby", "tone": "holo",
                 "approvals": 0, "buttons": CANVAS_BUTTONS, "briefing_new": False, "hold": "", "rec": "", "wake": "",
                 "meter": True})
    board.apply({"type": "orb", "state": "idle", "form": "corner", "muted": False, "offline": False, "dim": False,
                 "level": 0, "voice": 0})
    return host, board


def _canvas2_put(board: Any, frames: list[dict[str, Any]], group: str = "g1", pinned: int = 0) -> None:
    """One answer's group and its frames, as the feed sends them (the group first, then its frames, then its order)."""
    board.apply({"type": "group", "id": group, "time": "9:41 AM", "topic": "Dana's budget", "frames": [],
                 "older": False})
    for frame in frames:
        board.apply(dict(frame, group=group))
    board.apply({"type": "group", "id": group, "time": "9:41 AM", "topic": "Dana's budget",
                 "frames": [frame["id"] for frame in frames], "older": False})
    board.apply({"type": "bar", "frames": len(frames), "waits": 0, "pinned": pinned, "compact": False})
    board.apply({"type": "desk", "state": "enter"})


def canvas_layout_case(make: Any, wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    """The canvas's layout in the real page: 1, 3, 6, 9, 12 and 24 frames at 900x600, 1280x800, 1920x1080 and
    2560x1440 (the same page, resized: it packs again), then each kind with long words at 1280x800 and 1920x1080."""
    report: dict[str, Any] = {"sizes": {}}
    host, board = _canvas2_start(make, wait_until, pump, CANVAS2_SIZES[0])
    report["ready"] = board.is_ready()
    board.set_motion("off")                               # (the layout is the same at every level; off: no waiting)
    marks = json.dumps(list(CANVAS2_MARKS))

    def look() -> dict[str, Any]:
        return json.loads(main_world(board, CANVAS2_LOOK % marks))

    for count in CANVAS2_COUNTS:
        board.apply({"type": "clear"})
        _canvas2_put(board, canvas2_frames(count, hostile=True))
        for size in CANVAS2_SIZES:
            host.resize(*size)
            board.setGeometry(0, 0, *size)
            pump(0.22)
            report["sizes"][f"{count}@{size[0]}x{size[1]}"] = look()
    # each kind with long words
    report["long"] = {}
    for size in ((1280, 800), (1920, 1080)):
        board.apply({"type": "clear"})
        host.resize(*size)
        board.setGeometry(0, 0, *size)
        frames = canvas2_long()
        _canvas2_put(board, frames)                       # all at once: most fold (what a tile shows of long words)
        board.apply({"type": "picture", "id": "k6", "src": tiny_jpeg_url(320, 200)})
        pump(0.4)
        found: dict[str, Any] = {"look": look(), "style": json.loads(main_world(board, CANVAS2_STYLE)), "full": [],
                                 "looks": []}
        for start in range(0, len(frames), 3):            # three at a time: each kind as a whole frame
            board.apply({"type": "clear"})
            _canvas2_put(board, frames[start:start + 3])
            if start <= 5 < start + 3:
                board.apply({"type": "picture", "id": "k6", "src": tiny_jpeg_url(320, 200)})
            pump(0.35)
            found["full"] += json.loads(main_world(board, CANVAS2_STYLE))
            found["looks"].append(look())
        report["long"][f"{size[0]}x{size[1]}"] = found
    report["long_titles"] = [LONG_TITLE, len(LONG_TITLE), len(LONG_CELL)]
    counts = board.counts()
    report["blocked"] = counts.get("blocked", {})
    report["allowed"] = counts.get("allowed", {})
    report["messages_refused"] = {key: value for key, value in counts.items() if key.startswith("refused:")}
    report["console"] = {key: value for key, value in counts.items() if key.startswith("console:")}
    settle_end(report, board)
    return report


def canvas_ways_case(make: Any, wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    """The canvas's moves and ways in the real page at 1280x800: the task card's states, the arrival at each motion
    level, hover, the keyboard's moves by where the frames are, open big from a grid cell, the Pinned filter, a close
    leaving a gap and Tidy, Clear view's lines into the orb and Bring back, and the page's own refusals."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    report: dict[str, Any] = {}
    host, board = _canvas2_start(make, wait_until, pump, (1280, 800))
    report["ready"] = board.is_ready()
    went: list[str] = []
    board.goRequested.connect(went.append)
    asked: list[list[Any]] = []
    for name in ("openRequested", "closeRequested"):
        getattr(board, name).connect(lambda frame_id, n=name: asked.append([n, frame_id]))
    board.pinRequested.connect(lambda frame_id, on: asked.append(["pinRequested", frame_id, on]))
    landed: list[list[str]] = []
    board.frameLanded.connect(lambda frame_id, step: landed.append([frame_id, step]))
    marks = json.dumps(list(CANVAS2_MARKS))

    def look() -> dict[str, Any]:
        return json.loads(main_world(board, CANVAS2_LOOK % marks))

    def watch(ms: int, frame_id: str = "") -> list[dict[str, Any]]:
        main_world(board, CANVAS2_WATCH % (ms, json.dumps(frame_id) if frame_id else "''"))
        wait_until(lambda: main_world(board, "String(window.__c2done === true)") == "true", ms / 1000 + 6)
        return json.loads(main_world(board, "JSON.stringify(window.__c2)"))

    def orb(form: str) -> None:
        board.apply({"type": "orb", "state": "idle", "form": form, "muted": False, "offline": False, "dim": False,
                     "level": 0, "voice": 0})

    def task(state: str, **more: Any) -> bool:
        message = {"type": "task", "state": state, "title": "Working on it", "words": "Reading your calendar",
                   "seconds": 3, "steps": 0, "sources": 0, "frames": 0, "group": "", "stop": True}
        message.update(more)
        return board.apply(message)

    def proxy() -> Any:
        return board.web_view().focusProxy()

    def press(label: str) -> str:
        return main_world(board, r"""(function (label) {
          var hit = '';
          Array.prototype.forEach.call(document.querySelectorAll('#canvas button, #board .task button'), function (b) {
            if (!b.hidden && !hit && b.textContent.indexOf(label) === 0) { hit = b.textContent; b.click(); }
          });
          return hit;
        })(%s)""" % json.dumps(label))

    def active() -> str:
        return main_world(board, "(function () { var a = document.activeElement, f = a && a.closest ? "
                                 "a.closest('.frame') : null; return f ? f.querySelector('.f-title').id.slice(6) : "
                                 "(a ? a.tagName : ''); })()")

    def key(code: Any) -> str:
        QTest.keyClick(QApplication.focusWidget() or host, code)
        pump(0.25)
        return active()

    # ---- the task card: working (alone on the canvas, beside the orb), a step on, then done with its group ----------
    board.set_motion("lively")
    orb("rings")
    pump(1.0)
    report["task_sent"] = [task("working"), task("working", words="<script>window.pwned=43</script>", steps=2,
                                                 sources=1, seconds=7)]
    pump(0.7)
    report["task_working"] = look()
    report["stop_pressed"] = press("Stop")
    pump(0.3)
    frames = canvas2_frames(6)
    orb("corner")
    _canvas2_put(board, frames)
    report["task_done_sent"] = task("done", title="", words="", seconds=9, steps=3, sources=2, frames=6, group="g1",
                                    stop=False)
    # ---- the arrival, lively: one after another, the heading decodes, numbers count up ----------------------------
    report["lively"] = watch(2600, "f3")
    report["lively_after"] = look()
    report["lively_style"] = json.loads(main_world(board, CANVAS2_STYLE))
    report["lively_anim"] = main_world(board, "(function () { return 1; })()")
    report["landed_lively"] = list(landed)
    # ---- hover: the frame brightens, its corner ticks and its controls show -----------------------------------------
    first = next(item for item in report["lively_after"]["items"] if item["id"] == "f3")
    QTest.mouseMove(proxy(), QPoint(first["box"][0] + first["box"][2] // 2, first["box"][1] + first["box"][3] // 2))
    pump(0.5)
    report["hover"] = json.loads(main_world(board, r"""JSON.stringify((function () {
      var el = document.getElementById('title-f3').closest('.frame');
      return {hovered: el.matches(':hover'), tick: getComputedStyle(el.querySelector('.fx-tick')).opacity,
              controls: getComputedStyle(el.querySelector('.f-ctl')).opacity,
              glow: getComputedStyle(el, '::after').opacity, border: getComputedStyle(el).borderTopColor,
              other: getComputedStyle(document.getElementById('title-f2').closest('.frame').querySelector('.f-ctl')).opacity,
              box: (function () { var r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()};
    })())"""))
    QTest.mouseMove(proxy(), QPoint(3, 3))
    pump(0.3)
    # ---- the keyboard: the arrows go to the nearest frame that way; Enter, P and Delete ask the app -----------------
    board.focus_part("board")
    pump(0.3)
    moves: list[list[str]] = [["start", active()]]
    for name, code in (("Right", Qt.Key.Key_Right), ("Down", Qt.Key.Key_Down), ("Left", Qt.Key.Key_Left),
                       ("Up", Qt.Key.Key_Up), ("End", Qt.Key.Key_End), ("Home", Qt.Key.Key_Home)):
        moves.append([name, key(code)])
    report["moves"] = moves
    report["move_boxes"] = {item["id"]: item["box"] for item in look()["items"]}
    key(Qt.Key.Key_P)
    key(Qt.Key.Key_Return)
    report["asked"] = list(asked)
    # ---- open big from a grid cell: it grows from its place, and goes back ------------------------------------------
    home = moves[-1][1]
    main_world(board, "document.body.focus(); 0")
    board.apply({"type": "big", "state": "open", "id": home, "x": 0, "y": 0, "w": 1280, "h": 800})
    report["big"] = watch(700, home)
    board.apply({"type": "big", "state": "close", "id": ""})
    pump(0.7)
    report["big_shut"] = main_world(board, "String(board.counts().big) + '/' + String(!!document.querySelector('#big .big-card'))")
    # ---- Pinned: a filter in the corner, only while something is pinned ---------------------------------------------
    report["before_pin"] = look()["buttons"]
    board.apply(dict(frames[2], group="g1", pinned=True))
    board.apply({"type": "bar", "frames": 6, "waits": 0, "pinned": 1, "compact": False})
    pump(0.4)
    report["pinned_buttons"] = look()["buttons"]
    report["pinned_pressed"] = press("Pinned")
    pump(0.9)
    report["pinned_only"] = look()
    press("Pinned")
    pump(0.9)
    report["pinned_all"] = look()
    board.apply(dict(frames[2], group="g1", pinned=False))
    board.apply({"type": "bar", "frames": 6, "waits": 0, "pinned": 0, "compact": False})
    pump(0.4)
    # ---- a close leaves a gap (nothing else moves); Tidy shows and packs again, the frames gliding ------------------
    before = look()
    report["before_close"] = before
    board.apply(dict(frames[1], group="g1", closed=True))
    pump(0.5)
    report["closed"] = look()
    board.apply({"type": "remove", "id": "f2"})
    board.apply({"type": "group", "id": "g1", "time": "9:41 AM", "topic": "Dana's budget",
                 "frames": [frame["id"] for frame in frames if frame["id"] != "f2"], "older": False})
    board.apply({"type": "bar", "frames": 5, "waits": 0, "pinned": 0, "compact": False})
    pump(0.7)
    report["gap"] = look()
    mover = next((item["id"] for item in report["gap"]["items"] if item["id"] not in ("f1", "f2")), "f3")
    report["tidy_mover"] = mover
    main_world(board, CANVAS2_WATCH % (1500, json.dumps("f6")))
    report["tidy_pressed"] = press("Tidy")
    wait_until(lambda: main_world(board, "String(window.__c2done === true)") == "true", 8)
    report["tidy"] = json.loads(main_world(board, "JSON.stringify(window.__c2)"))
    report["tidied"] = look()
    report["went"] = list(went)
    # ---- Clear view: each frame folds to a line the orb draws in; then the orb leaves its corner; Bring back ---------
    left = [item["id"] for item in report["tidied"]["items"]]
    main_world(board, CANVAS2_WATCH % (1500, "''"))
    board.apply({"type": "cleared", "frames": left})
    board.apply({"type": "bar", "frames": 0, "waits": 0, "pinned": 0, "compact": False})
    orb("rings")                                              # (the app: nothing shows any more)
    wait_until(lambda: main_world(board, "String(window.__c2done === true)") == "true", 8)
    report["clear"] = json.loads(main_world(board, "JSON.stringify(window.__c2)"))
    pump(0.9)
    report["cleared"] = look()
    report["cleared_row"] = main_world(board, "(function () { var r = document.querySelector('.cleared-row'); return "
                                              "r.classList.contains('shown') ? r.textContent : ''; })()")
    report["cleared_orb"] = json.loads(main_world(board, "JSON.stringify(board.orb())"))
    main_world(board, CANVAS2_WATCH % (2400, json.dumps("f1")))
    board.apply({"type": "cleared", "frames": []})
    board.apply({"type": "bar", "frames": 5, "waits": 0, "pinned": 0, "compact": False})
    orb("corner")
    wait_until(lambda: main_world(board, "String(window.__c2done === true)") == "true", 9)
    report["back"] = json.loads(main_world(board, "JSON.stringify(window.__c2)"))
    report["brought_back"] = look()
    # ---- calm: the same arrival, slower, no decode, no count-up; off: there at once ---------------------------------
    for level in ("calm", "off"):
        board.apply({"type": "clear"})
        board.set_motion(level)
        pump(0.3)
        del landed[:]
        _canvas2_put(board, canvas2_frames(6))
        report[level] = watch(3600 if level == "calm" else 500, "f3")
        report[f"{level}_after"] = look()
        report[f"landed_{level}"] = list(landed)
    board.set_motion("lively")
    # ---- a card whose request ends with nothing for the canvas goes; hostile cards are refused by the page ----------
    task("working")
    pump(0.5)
    report["card_again"] = look()["card"]
    task("gone", title="", words="", stop=False)
    pump(0.6)
    report["card_gone"] = look()["card"]
    tries = {
        "state": {"type": "task", "state": "evil", "title": "", "words": "", "seconds": 0, "steps": 0, "sources": 0,
                  "frames": 0, "group": "", "stop": False},
        "long": {"type": "task", "state": "working", "title": "x" * 41, "words": "", "seconds": 0, "steps": 0,
                 "sources": 0, "frames": 0, "group": "", "stop": False},
        "fraction": {"type": "task", "state": "working", "title": "", "words": "", "seconds": 0.5, "steps": 0,
                     "sources": 0, "frames": 0, "group": "", "stop": False},
        "group": {"type": "task", "state": "done", "title": "", "words": "", "seconds": 0, "steps": 0, "sources": 0,
                  "frames": 0, "group": "nope", "stop": False},
        "extra": {"type": "task", "state": "working", "title": "", "words": "", "seconds": 0, "steps": 0,
                  "sources": 0, "frames": 0, "group": "", "stop": False, "html": "<b>x</b>"},
        "figure": {"type": "frame", "id": "n9", "kind": "number", "title": "T", "meta": "", "status": "ok",
                   "status_text": "", "status_meta": "", "waits": "", "changed": False, "pinned": False,
                   "closed": False, "body": {"figure": "<b>500</b>", "text": "", "source": ""}},
        "number_key": {"type": "frame", "id": "n9", "kind": "number", "title": "T", "meta": "", "status": "ok",
                       "status_text": "", "status_meta": "", "waits": "", "changed": False, "pinned": False,
                       "closed": False, "body": {"figure": "+500", "text": "", "source": "", "href": "x"}},
    }
    report["page_refused"] = {name: main_world(board, f"String(board.apply({json.dumps(json.dumps(message))}))")
                              for name, message in tries.items()}
    report["python_refused"] = {name: board.apply(message) for name, message in tries.items()}
    report["forged"] = main_world(board, CANVAS_FORGED.replace("format_disk", "tidy_all").replace("'settings'", "'tidy'"))
    pump(0.4)
    report["went_end"] = list(went)
    report["end"] = look()
    counts = board.counts()
    report["blocked"] = counts.get("blocked", {})
    report["allowed"] = counts.get("allowed", {})
    report["console"] = {key: value for key, value in counts.items() if key.startswith("console:")}
    settle_end(report, board)
    return report


CANVAS3_SIZE = (1389, 868)                 # the owner's screen (logical px)
CANVAS3_ROW_SIZES = ((900, 600), (1280, 800), (1920, 1080))
# The top right row as drawn: each button that shows (its words, its box), how far apart their middles are (one line:
# none), the row's and the top left corner's boxes, More's list, the frames' area's top and the wake badge.
CANVAS3_ROW = r"""JSON.stringify((function () {
  function box(el) { var r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top),
                                                                 Math.round(r.width), Math.round(r.height)]; }
  var shown = [];
  Array.prototype.forEach.call(document.querySelectorAll('#canvas .c-tr button'), function (b) {
    if (b.getClientRects().length) { shown.push({text: b.textContent || b.getAttribute('aria-label'), box: box(b)}); }
  });
  var tops = shown.map(function (b) { return b.box[1] + b.box[3] / 2; });
  var menu = document.getElementById('c-menu');
  var items = menu ? Array.prototype.filter.call(menu.children, function (b) { return !b.hidden; })
    .map(function (b) { return {text: b.textContent, box: box(b)}; }) : [];
  var tl = document.querySelector('#canvas .c-tl'), wake = document.querySelectorAll('#canvas .c-bl .c-state')[0];
  return {
    shown: shown.map(function (b) { return b.text; }), boxes: shown.map(function (b) { return b.box; }),
    spread: tops.length ? Math.max.apply(null, tops) - Math.min.apply(null, tops) : 0,
    row: box(document.querySelector('#canvas .c-tr')), tl: box(tl), win: [innerWidth, innerHeight],
    area_top: Math.round(document.body.getBoundingClientRect().top),
    menu: menu && !menu.hidden ? box(menu) : null, items: items,
    menu_z: menu ? getComputedStyle(menu).zIndex : '', menu_in_canvas: !!(menu && menu.closest('#canvas')),
    wake: wake && !wake.hidden ? {text: wake.textContent, box: box(wake), cut: wake.scrollWidth > wake.clientWidth + 1}
      : null,
    bl: box(document.querySelector('#canvas .c-bl')),
    layout: board.layout()
  };
})())"""
CANVAS3_PRESS_MORE = r"""(function () {
  var hit = '';
  Array.prototype.forEach.call(document.querySelectorAll('#canvas .c-tr button'), function (b) {
    if (!hit && b.getClientRects().length && b.textContent === 'More') { hit = b.textContent; b.click(); }
  });
  return hit;
})()"""
CANVAS3_PRESS_ITEM = r"""(function (label) {
  var menu = document.getElementById('c-menu'), hit = '';
  Array.prototype.forEach.call(menu.children, function (b) {
    if (!hit && !b.hidden && b.textContent === label) { hit = b.textContent; b.click(); }
  });
  return hit + '|' + String(menu.hidden);
})(%s)"""


def canvas_fixes_case(make: Any, wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    """The canvas, part 3's six real-screen fixes in the real page (see the module doc's "canvas_fixes")."""
    from briefing_reader import wake_ui

    report: dict[str, Any] = {}
    host, board = _canvas2_start(make, wait_until, pump, CANVAS3_SIZE)
    report["ready"] = board.is_ready()
    board.set_motion("off")
    went: list[str] = []
    board.goRequested.connect(went.append)
    marks = json.dumps(list(CANVAS2_MARKS))
    longest = max(wake_ui.SHORT_WORDS, key=len)
    corners = {"type": "canvas", "clock": "Tue 17 Nov · 9:41 AM", "word": "Standby", "tone": "amber",
               "approvals": 12, "buttons": CANVAS_BUTTONS, "briefing_new": True, "hold": "", "rec": "",
               "wake": longest, "meter": True}

    def row() -> dict[str, Any]:
        return json.loads(main_world(board, CANVAS3_ROW))

    def look() -> dict[str, Any]:
        return json.loads(main_world(board, CANVAS2_LOOK % marks))

    def resize(size: tuple[int, int]) -> None:
        host.resize(*size)
        board.setGeometry(0, 0, *size)
        pump(0.3)

    # ---- 1. the top row: every button, Approvals 12, then Pinned, then Tidy; the frames' area stays where it is ---
    report["corners_sent"] = board.apply(corners)
    report["wake_words"] = [longest, len(longest)]
    pump(0.3)
    report["row_empty"] = row()
    frames = canvas2_frames(6)
    _canvas2_put(board, frames)
    pump(0.4)
    report["row_frames"] = row()                         # Clear view came
    board.apply(dict(frames[2], group="g1", pinned=True))
    board.apply({"type": "bar", "frames": 6, "waits": 0, "pinned": 1, "compact": False})
    pump(0.4)
    report["row_pinned"] = row()                         # ... and Pinned
    board.apply(dict(frames[1], group="g1", closed=True))
    board.apply({"type": "remove", "id": "f2"})
    board.apply({"type": "group", "id": "g1", "time": "9:41 AM", "topic": "Dana's budget",
                 "frames": [frame["id"] for frame in frames if frame["id"] != "f2"], "older": False})
    board.apply({"type": "bar", "frames": 5, "waits": 0, "pinned": 1, "compact": False})
    pump(0.5)
    report["row_tidy"] = row()                           # ... and Tidy: every button at once
    report["more_pressed"] = main_world(board, CANVAS3_PRESS_MORE)
    pump(0.3)
    report["row_open"] = row()                           # More's list, over the frames
    report["item_pressed"] = main_world(board, CANVAS3_PRESS_ITEM % json.dumps("Memory"))
    pump(0.3)
    report["row_after_item"] = row()
    report["went"] = list(went)
    for size in CANVAS3_ROW_SIZES:
        resize(size)
        report[f"row_{size[0]}x{size[1]}"] = row()
    resize(CANVAS3_SIZE)
    # ---- 5. one, two and three frames use the screen ----------------------------------------------------------------
    report["few"] = {}
    for count in (1, 2, 3):
        board.apply({"type": "clear"})
        _canvas2_put(board, canvas2_frames(count))
        pump(0.4)
        report["few"][str(count)] = look()
    board.apply({"type": "clear"})
    _canvas2_put(board, canvas2_frames(2))
    for size in ((1920, 1080), (1280, 800)):
        resize(size)
        report["few"][f"2@{size[0]}x{size[1]}"] = look()
    resize(CANVAS3_SIZE)
    # ---- 6. the task card with two frames up: the mock's size, a place of its own; the frames never move -----------
    board.apply({"type": "clear"})
    _canvas2_put(board, canvas2_frames(2))
    pump(0.4)
    report["card_before"] = look()
    card = {"type": "task", "state": "working", "title": "Working on it", "words": "Reading your calendar",
            "seconds": 3, "steps": 0, "sources": 0, "frames": 0, "group": "", "stop": True}
    report["card_sent"] = board.apply(card)
    pump(0.5)
    report["card_up"] = look()
    report["card_row"] = row()
    board.apply(dict(card, words="Searching your mail", steps=2, sources=1, seconds=9))
    pump(0.4)
    report["card_step"] = look()
    board.apply({"type": "task", "state": "gone", "title": "", "words": "", "seconds": 0, "steps": 0, "sources": 0,
                 "frames": 0, "group": "", "stop": False})
    pump(0.5)
    report["card_gone"] = look()
    # alone on the canvas: beside the orb, at the same size
    board.apply({"type": "clear"})
    board.apply({"type": "bar", "frames": 0, "waits": 0, "pinned": 0, "compact": False})
    board.apply({"type": "desk", "state": "leave"})
    pump(0.3)
    board.apply(card)
    pump(0.5)
    report["card_alone"] = look()
    counts = board.counts()
    report["blocked"] = counts.get("blocked", {})
    report["allowed"] = counts.get("allowed", {})
    report["messages_refused"] = {key: value for key, value in counts.items() if key.startswith("refused:")}
    report["console"] = {key: value for key, value in counts.items() if key.startswith("console:")}
    settle_end(report, board)
    return report


# The canvas, part 3B: the resting frames as drawn - each one's classes, its chip, its rows' words, its foot (the
# amber words, the button's words, whether it shows and whether it is inside the frame), whether it says CUT.
CANVAS3B_RESTING = r"""JSON.stringify((function () {
  function box(el) { var r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top),
                                                                 Math.round(r.width), Math.round(r.height)]; }
  var found = {};
  Array.prototype.forEach.call(document.querySelectorAll('#board .frame'), function (el) {
    var foot = el.querySelector('.rs-foot');
    if (!foot) { return; }
    var id = el.querySelector('.f-title').id.slice(6), go = foot.querySelector('.rs-go'), b = box(el), g = box(go);
    var rows = el.querySelector('.rs-rows');
    found[id] = {
      cls: el.className.split(' ').filter(Boolean).sort(), box: b, title: el.querySelector('.f-title').textContent,
      chip: getComputedStyle(el.querySelector(':scope > .f-wait')).display,
      ap: Array.prototype.map.call(el.querySelectorAll('.ap-row'), function (r) {
        return [r.querySelector('.ap-kind').textContent, r.querySelector('.ap-title').textContent]; }),
      ag: Array.prototype.map.call(el.querySelectorAll('.ag-row'), function (r) {
        var wait = r.querySelector('.ag-wait');
        return [r.querySelector('.ag-name').textContent, r.querySelector('.ag-state').textContent,
                r.querySelector('.ag-when').textContent, wait ? wait.textContent : '',
                r.className.split(' ').filter(Boolean).sort().join(' ')]; }),
      more: Array.prototype.map.call(el.querySelectorAll('.rs-rows > .x-more'), function (r) { return r.textContent; }),
      waits: foot.querySelector('.rs-waits').textContent, waits_color: getComputedStyle(foot.querySelector('.rs-waits')).color,
      go: {text: go.textContent, hidden: go.hidden, shown: go.getClientRects().length > 0, box: g,
           inside: go.hidden || (g[0] >= b[0] && g[1] >= b[1] && g[0] + g[2] <= b[0] + b[2] + 0.5
                                 && g[1] + g[3] <= b[1] + b[3] + 0.5),
           cls: go.className.split(' ').filter(Boolean).sort(), border: getComputedStyle(go).borderTopColor},
      buttons: Array.prototype.filter.call(el.querySelectorAll('.f-body button'), function (n) {
        return n.getClientRects().length > 0; }).map(function (n) { return n.textContent; }),
      cut: el.classList.contains('cut'), rows_cut: !!rows && rows.scrollHeight > rows.clientHeight + 1,
      border: getComputedStyle(el).borderTopColor, ground: getComputedStyle(el).backgroundColor
    };
  });
  return found;
})())"""
CANVAS3B_PRESS = r"""(function (word) {
  var hit = '';
  Array.prototype.forEach.call(document.querySelectorAll('#board .rs-go'), function (b) {
    if (!hit && b.getClientRects().length && b.textContent === word) { hit = b.textContent; b.click(); }
  });
  return hit;
})(%s)"""


def canvas3b_resting(hostile: bool = True) -> list[dict[str, Any]]:
    """The two resting frames as board.resting builds them: long words, many rows, markup among them (``hostile``)."""
    from briefing_reader.board import resting

    mark = "<img src=x onerror=window.pwned=51>"
    cards = [(mark if hostile else "REPLY · WORK", LONG_TITLE), ("TO-DO · PERSONAL", "Pay the Example Billing invoice"),
             ("EVENT · SCHOOL · " + "K" * 30, "<script>window.pwned=52</script>" + "t" * 40),
             ("EMAIL · WORK", "Budget comments to Dana"), ("REPLY · WORK", "Not shown: past the first four")]
    lines = [resting.AgentLine(LONG_TITLE, "on", last="Yesterday 12:41 PM", came=3, next="Tomorrow 7:30 AM", waits=2),
             resting.AgentLine(mark + " javascript:pwned=53", "paused", last="Mon 9:41 AM", came=0, next="", waits=0),
             resting.AgentLine("Invoices to the sheet", "off", last="", came=-1, next="", waits=0)]
    lines += [resting.AgentLine(f"Agent number {n} with a longer name", "on", last="8:00 AM", came=n % 3,
                                next="5:00 PM", waits=n % 2) for n in range(4, 11)]
    approvals = resting.from_approvals(12, cards, frame_id="r1").messages[0]
    agents = resting.from_agents(lines, frame_id="r2").messages[0]
    return [dict(approvals), dict(agents)]


def canvas3b_short() -> list[dict[str, Any]]:
    """The two resting frames with little in them: one card waits; one agent on, nothing of it waits."""
    from briefing_reader.board import resting

    approvals = resting.from_approvals(1, [("REPLY · WORK", "Re: Budget numbers")], frame_id="r1").messages[0]
    agents = resting.from_agents([resting.AgentLine("Morning mail sweep", "on", last="8:00 AM", came=0,
                                                    next="Tomorrow 8:00 AM")], frame_id="r2").messages[0]
    return [dict(approvals), dict(agents)]


def canvas_resting_case(make: Any, wait_until: Any, pump: Any, main_world: Any, settle_end: Any) -> dict[str, Any]:
    """The canvas, part 3B: the two resting frames in the real page at 1389x868 (see the module doc's
    "canvas_resting")."""
    report: dict[str, Any] = {}
    host, board = _canvas2_start(make, wait_until, pump, CANVAS3_SIZE)
    report["ready"] = board.is_ready()
    board.set_motion("off")
    went: list[str] = []
    board.goRequested.connect(went.append)
    marks = json.dumps(list(CANVAS2_MARKS))

    def look() -> dict[str, Any]:
        return json.loads(main_world(board, CANVAS2_LOOK % marks))

    def resting() -> dict[str, Any]:
        return json.loads(main_world(board, CANVAS3B_RESTING))

    def corners(buttons: list[str]) -> bool:
        return board.apply({"type": "canvas", "clock": "Tue 17 Nov · 9:41 AM", "word": "Standby", "tone": "amber",
                            "approvals": 12, "buttons": buttons, "briefing_new": False, "hold": "", "rec": "",
                            "wake": "", "meter": True})

    def resize(size: tuple[int, int]) -> None:
        host.resize(*size)
        board.setGeometry(0, 0, *size)
        pump(0.3)

    # ---- long words, many rows, markup: beside the day and the to-dos --------------------------------------------
    built = canvas3b_resting()
    report["built"] = built
    report["corners_sent"] = corners(CANVAS_BUTTONS)
    others = [frame for frame in canvas2_frames(5) if frame["kind"] in ("calendar", "todos")]
    _canvas2_put(board, built + others)
    pump(0.6)
    report["long"] = resting()
    report["look"] = look()
    report["style"] = [one for one in json.loads(main_world(board, CANVAS2_STYLE)) if one["id"] in ("r1", "r2")]
    for size in ((1920, 1080), (1280, 800)):
        resize(size)
        report[f"long_{size[0]}x{size[1]}"] = resting()
        report[f"look_{size[0]}x{size[1]}"] = look()
    resize(CANVAS3_SIZE)
    # ---- each button presses its own corner word; without it on the corners it does not show ----------------------
    report["pressed"] = [main_world(board, CANVAS3B_PRESS % json.dumps(word))
                         for word in ("Open Approvals", "Open Agents")]
    pump(0.2)
    report["went"] = list(went)
    report["corners_without"] = corners([word for word in CANVAS_BUTTONS if word not in ("approvals", "agents")])
    pump(0.2)
    report["without"] = resting()
    report["pressed_without"] = [main_world(board, CANVAS3B_PRESS % json.dumps(word))
                                 for word in ("Open Approvals", "Open Agents")]
    corners(CANVAS_BUTTONS)
    pump(0.2)
    report["with_again"] = resting()
    # ---- open big: the same frame larger, its button still its only control ---------------------------------------
    board.apply({"type": "big", "state": "open", "id": "r2", "x": 0, "y": 0, "w": CANVAS3_SIZE[0], "h": CANVAS3_SIZE[1]})
    pump(0.8)
    report["big"] = json.loads(main_world(board, r"""JSON.stringify((function () {
      var card = document.querySelector('#big .big-card');
      if (!card) { return null; }
      return {cls: card.className.split(' ').filter(Boolean).sort(), rows: card.querySelectorAll('.ag-row').length,
              buttons: Array.prototype.filter.call(card.querySelectorAll('.f-body button'), function (n) {
                return n.getClientRects().length > 0; }).map(function (n) { return n.textContent; })};
    })())"""))
    board.apply({"type": "big", "state": "close", "id": ""})
    pump(0.6)
    # ---- little in them ----------------------------------------------------------------------------------------------
    board.apply({"type": "clear"})
    short = canvas3b_short()
    report["short_built"] = short
    _canvas2_put(board, short)
    pump(0.5)
    report["short"] = resting()
    report["short_look"] = look()
    # ---- a day with nothing booked: as tall as its words, beside frames of a row's height -----------------------------
    board.apply({"type": "clear"})
    nothing = {"type": "frame", "id": "d1", "kind": "calendar", "title": "Today", "meta": "Tue Nov 17",
               "body": {"rows": []}, "status": "empty", "status_text": "Nothing booked today", "status_meta": "",
               "waits": "", "changed": False, "pinned": False, "closed": False}
    _canvas2_put(board, [nothing] + canvas3b_short())
    pump(0.5)
    report["nothing_booked"] = look()
    report["nothing_booked_words"] = main_world(board, "document.getElementById('title-d1').closest('.frame')"
                                                       ".querySelector('.f-empty').textContent")
    # ---- the resting group "Now" put under an answer ("below"): the answer's frames keep their places -------------
    board.apply({"type": "clear"})
    answer = canvas2_frames(3)
    _canvas2_put(board, answer, group="g5")
    pump(0.5)
    report["below_before"] = look()
    rest = [dict(frame, id=f"n{index + 1}") for index, frame in enumerate(canvas3b_short())]
    board.apply({"type": "group", "id": "g6", "time": "9:41 AM", "topic": "Now", "frames": [], "older": True,
                 "below": True})
    for frame in rest:
        board.apply(dict(frame, group="g6"))
    board.apply({"type": "group", "id": "g6", "time": "9:41 AM", "topic": "Now", "frames": ["n1", "n2"],
                 "older": True, "below": True})
    pump(0.6)
    report["below_after"] = look()
    report["below_order"] = json.loads(main_world(board, "JSON.stringify(Array.prototype.map.call("
                                                  "document.querySelectorAll('#board .frame .f-title'), "
                                                  "function (n) { return n.id.slice(6); }))"))
    # ---- the corner's "Now": shown only when the app says so, its press posts its own word --------------------------
    corners(CANVAS_BUTTONS + ["now"])
    pump(0.3)
    report["row_now"] = json.loads(main_world(board, CANVAS3_ROW))["shown"]
    report["now_pressed"] = main_world(board, r"""(function () {
      var hit = '';
      Array.prototype.forEach.call(document.querySelectorAll('#canvas .c-tr button'), function (b) {
        if (!hit && b.getClientRects().length && b.textContent === 'Now') { hit = b.textContent; b.click(); }
      });
      return hit;
    })()""")
    pump(0.2)
    report["went_now"] = list(went)
    corners(CANVAS_BUTTONS)
    pump(0.3)
    report["row_without_now"] = json.loads(main_world(board, CANVAS3_ROW))["shown"]
    # ---- hostile bodies straight into the page: refused, nothing changes --------------------------------------------
    report["frames_before_hostile"] = main_world(board, "document.querySelectorAll('#board .frame').length")
    base = {"type": "frame", "id": "r9", "title": "T", "meta": "", "status": "ok", "status_text": "",
            "status_meta": "", "waits": "", "changed": False, "pinned": False, "closed": False}
    row = {"name": "A", "state": "on", "last": "", "came": 0, "next": "", "waits": 0}
    tries = {
        "count_0": {"kind": "approvals", "body": {"count": 0, "rows": []}},
        "count_under_rows": {"kind": "approvals", "body": {"count": 1, "rows": [{"kind": "K", "title": "T"}] * 2}},
        "nine_cards": {"kind": "approvals", "body": {"count": 9, "rows": [{"kind": "K", "title": "T"}] * 9}},
        "card_key": {"kind": "approvals", "body": {"count": 1, "rows": [{"kind": "K", "title": "T", "href": "x"}]}},
        "kind_long": {"kind": "approvals", "body": {"count": 1, "rows": [{"kind": "K" * 41, "title": "T"}]}},
        "count_text": {"kind": "approvals", "body": {"count": "3", "rows": []}},
        "no_agents": {"kind": "agents", "body": {"rows": [], "more": 0, "waits": 0}},
        "state": {"kind": "agents", "body": {"rows": [dict(row, state="evil")], "more": 0, "waits": 0}},
        "came": {"kind": "agents", "body": {"rows": [dict(row, came=-2)], "more": 0, "waits": 0}},
        "came_half": {"kind": "agents", "body": {"rows": [dict(row, came=1.5)], "more": 0, "waits": 0}},
        "waits_sum": {"kind": "agents", "body": {"rows": [dict(row, waits=3)], "more": 0, "waits": 2}},
        "row_key": {"kind": "agents", "body": {"rows": [dict(row, onclick="x")], "more": 0, "waits": 0}},
        "missing": {"kind": "agents", "body": {"rows": [row], "waits": 0}},
        "when_long": {"kind": "agents", "body": {"rows": [dict(row, last="x" * 25)], "more": 0, "waits": 0}},
    }
    hostile = [dict(base, **one) for one in tries.values()]
    report["page_refused"] = dict(zip(tries, json.loads(main_world(
        board, "JSON.stringify(%s.map(function (m) { return board.apply(JSON.stringify(m)); }))"
        % json.dumps(hostile)))))
    pump(0.2)
    report["frames_after_hostile"] = main_world(board, "document.querySelectorAll('#board .frame').length")
    counts = board.counts()
    report["blocked"] = counts.get("blocked", {})
    report["allowed"] = counts.get("allowed", {})
    report["messages_refused"] = {key: value for key, value in counts.items() if key.startswith("refused:")}
    report["console"] = {key: value for key, value in counts.items() if key.startswith("console:")}
    settle_end(report, board)
    return report


def shown_typed(body: Any) -> list[str]:
    """The words of a typed frame's body the page shows in it as they are (a maths tree's words apart; a unit is the
    frame's meta, in its header)."""
    found: list[str] = []
    if isinstance(body, dict):
        for key, value in body.items():
            if key not in ("expr", "state", "text", "value", "was", "changed", "today", "unit"):
                found += shown_typed(value)
    elif isinstance(body, list):
        for value in body:
            found += shown_typed(value)
    elif isinstance(body, str) and body:
        found.append(body)
    return found


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1] if len(sys.argv) > 1 else "")), flush=True)
