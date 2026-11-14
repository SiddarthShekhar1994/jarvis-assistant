"""The new look, part 3: the board's host, without the web engine (tests/test_board_lockdown.py runs the real engine in
probe processes). Offscreen; nothing here imports QtWebEngine (the shared test process must never start it).

Covered: the messages the app sends (types, caps, hostile shapes: wrong types, huge strings, deep nesting, NaN and
Infinity, control and bidi characters kept as text, a 2 MB picture refused) and how they reach the page; the requests
the page may make (the list, the frame ids, sizes, rates; the bridge refuses a deleteLater); the scheme's allowlist and
content types, qwebchannel.js from Qt's own resource, tokens.css written from holo; the page's policy and a static check
of its script; the fallback tile and every way BoardView falls back to it by itself; the window's keys with the focus in
the page and Space (ui._keeps_space); and the snapshot_engine helpers the board starts Chromium through. Part 4 changed
two of these on purpose: the kinds on the list (tests/test_board_frames.py checks each), and a board that fell back still
takes messages and shows them as read-only tiles (tests/test_board_glance.py).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import shiboken6
from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QFile, QIODevice, QObject, Qt, QUrl
from PySide6.QtGui import QColor, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit, QWidget

from briefing_reader import __main__ as entry
from briefing_reader import holo, hud, snapshot_engine, snapshots, ui
from briefing_reader.board import protocol, scheme, view
from tests.ui_fakes import fonts, settle, tiny_jpeg, wait_for

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS = PROJECT_ROOT / "briefing_reader" / "board" / "assets"
CSP = ("default-src 'none'; script-src jarvis-board:; style-src jarvis-board:; font-src jarvis-board:; img-src data:; "
       "connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'")
_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_board", "-platform", "offscreen"])
    fonts()


def frame(frame_id: str = "f1", **fields: object) -> dict:
    message = {"type": "frame", "id": frame_id, "kind": "text", "title": "Budget lines", "body": {"text": "Words."}}
    message.update(fields)
    return message


def data_url(payload: bytes) -> str:
    import base64

    return protocol.PICTURE_PREFIX + base64.b64encode(payload).decode("ascii")


def deep(levels: int) -> list:
    value: list = []
    for _ in range(levels):
        value = [value]
    return value


# --------------------------------------------------------------------------
# In: the app's messages
# --------------------------------------------------------------------------

class MessageTests(unittest.TestCase):
    def refused(self, message: object, known: set[str] | None = None) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_message(message, known if known is not None else {"f1"})
        return caught.exception.reason

    def test_a_frame_is_rebuilt_whole_with_every_field(self) -> None:
        checked = protocol.check_message(frame(meta="USD", waits="Reply waits on Approvals"))
        self.assertEqual((checked.type, checked.frame_id), ("frame", "f1"))
        self.assertEqual(json.loads(checked.text), {
            "type": "frame", "id": "f1", "kind": "text", "title": "Budget lines", "meta": "USD",
            "body": {"text": "Words."}, "status": "ok", "status_text": "", "status_meta": "", "waits":
            "Reply waits on Approvals", "changed": False, "pinned": False, "closed": False})
        self.assertNotIn("Words", repr(checked))                       # a repr never carries the text

    def test_markup_control_and_bidi_characters_are_kept_exactly_as_text(self) -> None:
        hostile = "<img src=http://example.com/x.png>\x00\x07\x1b[31m\u202eevil\u2066\u2028\ud800 &amp; \"q\" \\"
        checked = protocol.check_message(frame(title=hostile[:60], body={"text": hostile}))
        self.assertTrue(checked.text.isascii())                        # every other character escaped
        self.assertNotIn("\n", checked.text)
        self.assertEqual(json.loads(checked.text)["body"]["text"], hostile)
        self.assertEqual(json.loads(checked.text)["title"], hostile[:60])

    def test_wrong_types_are_refused(self) -> None:
        self.assertEqual(self.refused(["frame"]), protocol.R_SHAPE)
        self.assertEqual(self.refused("frame"), protocol.R_SHAPE)
        self.assertEqual(self.refused(None), protocol.R_SHAPE)

        class Sneaky(dict):
            pass

        self.assertEqual(self.refused(Sneaky(frame())), protocol.R_SHAPE)
        self.assertEqual(self.refused(frame(title=7)), protocol.R_SHAPE)
        self.assertEqual(self.refused(frame(title=["Budget"])), protocol.R_SHAPE)
        self.assertEqual(self.refused(frame(title={"text": "x"})), protocol.R_SHAPE)
        self.assertEqual(self.refused(frame(body=["Words."])), protocol.R_SHAPE)
        self.assertEqual(self.refused(frame(body={"text": 3})), protocol.R_SHAPE)
        self.assertEqual(self.refused(frame(changed="yes")), protocol.R_SHAPE)
        self.assertEqual(self.refused(frame(changed=1)), protocol.R_SHAPE)        # a bool, not a number
        self.assertEqual(self.refused(frame(status="lost")), protocol.R_FIELD)
        self.assertEqual(self.refused(frame(kind="chart")), protocol.R_KIND)       # part 6's kinds are not here yet
        self.assertEqual(self.refused(frame(kind="Calendar")), protocol.R_KIND)
        self.assertEqual(self.refused(frame(kind=["text"])), protocol.R_KIND)
        self.assertEqual(self.refused({"type": "script"}), protocol.R_TYPE)
        self.assertEqual(self.refused({"type": None}), protocol.R_TYPE)
        self.assertEqual(self.refused(frame(onclick="x")), protocol.R_FIELD)       # no field it does not know
        self.assertEqual(self.refused(frame(body={"text": "x", "html": "<b>"})), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "frame", "id": "f1", "kind": "text"}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "clear", 1: "x"}), protocol.R_FIELD)

    def test_huge_strings_are_refused_at_their_caps(self) -> None:
        self.assertEqual(protocol.check_message(frame(title="x" * protocol.TITLE_CAP)).type, "frame")
        self.assertEqual(self.refused(frame(title="x" * (protocol.TITLE_CAP + 1))), protocol.R_TOO_LONG)
        self.assertEqual(self.refused(frame(meta="x" * (protocol.META_CAP + 1))), protocol.R_TOO_LONG)
        self.assertEqual(self.refused(frame(body={"text": "x" * (protocol.TEXT_CAP + 1)})), protocol.R_TOO_LONG)
        self.assertEqual(self.refused(frame(waits="x" * (protocol.WAITS_CAP + 1))), protocol.R_TOO_LONG)
        self.assertEqual(self.refused(frame(status="failed", status_text="x" * (protocol.WORDS_CAP + 1))),
                         protocol.R_TOO_LONG)
        self.assertEqual(self.refused(frame(title="x" * 5_000_000)), protocol.R_TOO_LONG)
        self.assertEqual((protocol.TITLE_CAP, protocol.TEXT_CAP), (60, 400))            # DESIGN-SPEC 3.5

    def test_deep_nesting_is_refused_without_recursing(self) -> None:
        self.assertEqual(self.refused(frame(title=deep(100_000))), protocol.R_SHAPE)
        self.assertEqual(self.refused(frame(body={"text": deep(100_000)})), protocol.R_SHAPE)
        self.assertEqual(self.refused(frame(body=deep(50_000))), protocol.R_SHAPE)
        self.assertEqual(self.refused({"type": "clear", "x": deep(100_000)}), protocol.R_FIELD)

    def test_nan_and_infinity_are_refused(self) -> None:
        for number in (float("nan"), float("inf"), float("-inf")):
            self.assertEqual(self.refused(frame(changed=number)), protocol.R_SHAPE)
            self.assertEqual(self.refused(frame(title=number)), protocol.R_SHAPE)
            self.assertEqual(self.refused({"type": "motion", "level": number}), protocol.R_FIELD)

    def test_ids_are_short_plain_words_and_lit_remove_picture_name_a_frame_the_app_sent(self) -> None:
        for bad in ("", "a b", "../x", "x" * (protocol.ID_CAP + 1), "f1\n", "<f1>", 7, None):
            self.assertEqual(self.refused(frame(bad)), protocol.R_ID, repr(bad))
        self.assertEqual(protocol.check_message({"type": "lit", "id": ""}).text, '{"type":"lit","id":""}')
        self.assertEqual(protocol.check_message({"type": "lit", "id": "f1"}, {"f1"}).frame_id, "f1")
        self.assertEqual(self.refused({"type": "lit", "id": "f2"}), protocol.R_ID)
        self.assertEqual(self.refused({"type": "remove", "id": "f2"}), protocol.R_ID)
        self.assertEqual(self.refused({"type": "remove", "id": ""}), protocol.R_ID)
        self.assertEqual(self.refused({"type": "picture", "id": "f2", "src": data_url(tiny_jpeg())}), protocol.R_ID)

    def test_a_board_holds_at_most_max_frames(self) -> None:
        full = {f"f{index}" for index in range(protocol.MAX_FRAMES)}
        self.assertEqual(protocol.MAX_FRAMES, 24)
        self.assertEqual(self.refused(frame("new"), full), protocol.R_FULL)
        self.assertEqual(protocol.check_message(frame("f3"), full).frame_id, "f3")    # an update still goes

    def test_every_message_is_at_most_32_kb_of_json(self) -> None:
        self.assertEqual(protocol.MESSAGE_CAP, 32 * 1024)
        with mock.patch.object(protocol, "MESSAGE_CAP", 400):
            self.assertEqual(self.refused(frame(body={"text": "x" * 300})), protocol.R_SIZE)
            self.assertEqual(protocol.check_message(frame(body={"text": "x" * 20})).type, "frame")
            # a picture's one image does not count against the 32 KB; its other fields do
            self.assertEqual(protocol.check_message({"type": "picture", "id": "f1", "src": data_url(tiny_jpeg())},
                                                    {"f1"}).type, "picture")

    def test_a_picture_is_one_jpeg_data_url_and_a_2_mb_one_is_refused(self) -> None:
        jpeg = tiny_jpeg()
        checked = protocol.check_message({"type": "picture", "id": "f1", "src": data_url(jpeg)}, {"f1"})
        self.assertEqual(json.loads(checked.text)["src"], data_url(jpeg))
        self.assertEqual(self.refused({"type": "picture", "id": "f1", "src": data_url(jpeg + b"\0" * (2 << 20))}),
                         protocol.R_PICTURE)
        self.assertEqual(self.refused({"type": "picture", "id": "f1", "src": data_url(b"\x89PNG\r\n" + b"\0" * 64)}),
                         protocol.R_PICTURE)                                       # not a JPEG
        self.assertEqual(self.refused({"type": "picture", "id": "f1", "src": "data:image/png;base64,AAAA"}),
                         protocol.R_PICTURE)
        self.assertEqual(self.refused({"type": "picture", "id": "f1", "src": "http://example.com/x.jpg"}),
                         protocol.R_PICTURE)
        self.assertEqual(self.refused({"type": "picture", "id": "f1", "src": protocol.PICTURE_PREFIX + "%%%%"}),
                         protocol.R_PICTURE)
        self.assertEqual(self.refused({"type": "picture", "id": "f1", "src": protocol.PICTURE_PREFIX}),
                         protocol.R_PICTURE)
        self.assertEqual(self.refused({"type": "picture", "id": "f1", "src": data_url(jpeg), "alt": "x"}),
                         protocol.R_FIELD)
        biggest = b"\xff\xd8\xff" + b"\0" * (protocol.PICTURE_BYTES - 3)
        self.assertEqual(protocol.check_message({"type": "picture", "id": "f1", "src": data_url(biggest)},
                                                {"f1"}).type, "picture")              # a 300 KB JPEG fits in 410 KB
        self.assertEqual(self.refused({"type": "picture", "id": "f1", "src": data_url(biggest + b"\0")}),
                         protocol.R_PICTURE)

    def test_motion_and_clear(self) -> None:
        for level in ("lively", "calm", "off"):
            self.assertEqual(json.loads(protocol.check_message({"type": "motion", "level": level}).text)["level"],
                             level)
        self.assertEqual(self.refused({"type": "motion", "level": "warp"}), protocol.R_FIELD)
        self.assertEqual(protocol.check_message({"type": "clear"}).text, '{"type":"clear"}')
        self.assertEqual(self.refused({"type": "clear", "all": True}), protocol.R_FIELD)


# --------------------------------------------------------------------------
# Out: the page's requests, and the bridge
# --------------------------------------------------------------------------

class RequestTests(unittest.TestCase):
    known = frozenset({"f1", "f2"})

    def refused(self, kind: object, payload: object) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_request(kind, payload, self.known)
        return caught.exception.reason

    def test_the_list_of_requests(self) -> None:
        # Part 5b added clear .. focus_out on purpose (tests/test_board_ways.py checks each of them and their hostile
        # shapes); part 5c added landed and big on purpose (tests/test_board_lit.py checks them).
        self.assertEqual(protocol.REQUEST_KINDS, ("ready", "layout", "focus", "open", "close", "pin", "bring_back",
                                                  "retry", "go_approvals", "clear", "restore", "show_new", "reading",
                                                  "focus_out", "landed", "big"))
        for kind in ("focus", "open", "close", "bring_back", "retry", "go_approvals"):
            self.assertEqual(protocol.check_request(kind, '{"id":"f1"}', self.known), protocol.Request(kind, "f1"))
        self.assertEqual(protocol.check_request("pin", '{"id":"f2","on":true}', self.known),
                         protocol.Request("pin", "f2", on=True))
        self.assertEqual(protocol.check_request("ready", "{}", self.known), protocol.Request("ready"))
        self.assertEqual(protocol.check_request("ready", '{"v":1}', self.known), protocol.Request("ready"))
        layout = protocol.check_request("layout", '{"frames":[{"id":"f1","top":12,"height":180},'
                                                  '{"id":"f2","top":-40,"height":0}],"scrolling":true}', self.known)
        self.assertEqual(layout, protocol.Request("layout", places=(("f1", 12, 180), ("f2", -40, 0)), scrolling=True))

    def test_anything_else_is_refused(self) -> None:
        self.assertEqual(self.refused("evil", "{}"), protocol.R_KIND)
        self.assertEqual(self.refused("OPEN", '{"id":"f1"}'), protocol.R_KIND)
        self.assertEqual(self.refused(None, "{}"), protocol.R_KIND)
        self.assertEqual(protocol.kind_word("evil"), "unknown")
        self.assertEqual(protocol.kind_word("x" * 10_000), "unknown")
        self.assertEqual(protocol.kind_word("open"), "open")
        self.assertEqual(self.refused("open", '{"id":"f9"}'), protocol.R_ID)        # not a frame the app sent
        self.assertEqual(self.refused("open", '{"id":"../f1"}'), protocol.R_ID)
        self.assertEqual(self.refused("open", '{"id":1}'), protocol.R_ID)
        self.assertEqual(self.refused("open", "{}"), protocol.R_FIELD)
        self.assertEqual(self.refused("open", '{"id":"f1","then":"x"}'), protocol.R_FIELD)
        self.assertEqual(self.refused("open", '["f1"]'), protocol.R_SHAPE)
        self.assertEqual(self.refused("open", {"id": "f1"}), protocol.R_SHAPE)       # a payload is JSON text
        self.assertEqual(self.refused("open", "not json"), protocol.R_JSON)
        self.assertEqual(self.refused("open", '{"id": NaN}'), protocol.R_JSON)
        self.assertEqual(self.refused("layout", '{"frames":[],"scrolling":Infinity}'), protocol.R_JSON)
        self.assertEqual(self.refused("open", '{"id":"f1"}' + " " * protocol.PAYLOAD_CAP), protocol.R_SIZE)
        self.assertIn(self.refused("open", "[" * 2000 + "]" * 2000), (protocol.R_JSON, protocol.R_SHAPE))
        self.assertEqual(self.refused("pin", '{"id":"f1"}'), protocol.R_FIELD)
        self.assertEqual(self.refused("pin", '{"id":"f1","on":1}'), protocol.R_SHAPE)
        self.assertEqual(self.refused("ready", '{"v":2}'), protocol.R_FIELD)

    def test_a_layout_names_known_frames_once_with_whole_pixels(self) -> None:
        one = '{"id":"f1","top":1,"height":2}'
        self.assertEqual(self.refused("layout", '{"frames":[' + ",".join([one] * 25) + "]}"), protocol.R_SHAPE)
        self.assertEqual(self.refused("layout", '{"frames":[' + one + "," + one + "]}"), protocol.R_ID)
        self.assertEqual(self.refused("layout", '{"frames":[{"id":"f1","top":1.5,"height":2}]}'), protocol.R_SHAPE)
        self.assertEqual(self.refused("layout", '{"frames":[{"id":"f1","top":1e12,"height":2}]}'), protocol.R_SHAPE)
        self.assertEqual(self.refused("layout", '{"frames":[{"id":"f1","top":1,"height":-2}]}'), protocol.R_SHAPE)
        self.assertEqual(self.refused("layout", '{"frames":[{"id":"f1","top":true,"height":2}]}'), protocol.R_SHAPE)
        self.assertEqual(self.refused("layout", '{"frames":[{"id":"f9","top":1,"height":2}]}'), protocol.R_ID)
        self.assertEqual(self.refused("layout", '{"frames":[],"scrolling":"yes"}'), protocol.R_SHAPE)


class BridgeTests(unittest.TestCase):
    def make(self, known: set[str] | None = None) -> tuple[view.BoardBridge, list, list]:
        delivered: list[protocol.Request] = []
        clock = [100.0]
        bridge = view.BoardBridge(known=lambda: known if known is not None else {"f1"}, deliver=delivered.append,
                                  clock=lambda: clock[0])
        self.addCleanup(lambda: shiboken6.isValid(bridge) and shiboken6.delete(bridge))
        return bridge, delivered, clock

    def test_requests_on_the_list_about_known_frames_are_delivered(self) -> None:
        bridge, delivered, _clock = self.make()
        bridge.post("open", '{"id":"f1"}')
        bridge.post("pin", '{"id":"f1","on":false}')
        self.assertEqual(delivered, [protocol.Request("open", "f1"), protocol.Request("pin", "f1", on=False)])
        self.assertEqual(bridge.counts(), {"accepted:open": 1, "accepted:pin": 1})

    def test_anything_else_is_dropped_counted_and_logged_by_its_kind_only(self) -> None:
        bridge, delivered, _clock = self.make()
        with self.assertLogs("briefing_reader.board.view", "INFO") as logs:
            bridge.post("SECRET-KIND-WORDS", '{"id":"f1"}')
            bridge.post("open", '{"id":"SECRETID"}')
            bridge.post("open", '{"id":"f1","words":"SECRET-PAYLOAD"}')
        self.assertEqual(delivered, [])
        counts = bridge.counts()
        self.assertEqual((counts["dropped:unknown"], counts["dropped:open"]), (1, 2))
        self.assertEqual((counts["why:kind"], counts["why:id"], counts["why:field"]), (1, 1, 1))
        text = "\n".join(logs.output)
        self.assertIn("unknown", text)
        self.assertNotIn("SECRET", text)                               # never the page's words

    def test_a_flood_is_cut_at_the_rate(self) -> None:
        bridge, delivered, clock = self.make()
        for _ in range(view.POSTS_PER_S + 5):
            bridge.post("focus", '{"id":"f1"}')
        self.assertEqual(len(delivered), view.POSTS_PER_S)
        self.assertEqual(bridge.counts()["why:rate"], 5)
        clock[0] += 1.0
        bridge.post("focus", '{"id":"f1"}')
        self.assertEqual(len(delivered), view.POSTS_PER_S + 1)

    def test_layouts_have_their_own_rate(self) -> None:
        bridge, delivered, _clock = self.make()
        for _ in range(view.LAYOUTS_PER_S + 3):
            bridge.post("layout", '{"frames":[]}')
        self.assertEqual(len(delivered), view.LAYOUTS_PER_S)
        bridge.post("open", '{"id":"f1"}')                              # other kinds still pass
        self.assertEqual(delivered[-1], protocol.Request("open", "f1"))

    def test_the_channel_sees_one_slot_and_nothing_else_of_jarvis(self) -> None:
        bridge, _delivered, _clock = self.make()
        meta = bridge.metaObject()
        own = [bytes(meta.method(index).name().data()).decode() for index in range(meta.methodOffset(),
                                                                                       meta.methodCount())]
        self.assertEqual(own, ["post"])
        self.assertEqual(meta.propertyCount() - meta.propertyOffset(), 0)

    def test_a_delete_later_the_page_asks_for_is_refused(self) -> None:
        page = QObject()                                                # the bridge's parent, as the board's page is
        delivered: list[protocol.Request] = []
        bridge = view.BoardBridge(page, known=lambda: {"f1"}, deliver=delivered.append)
        bridge.deleteLater()                                            # what bridge.deleteLater() on the page does
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(shiboken6.isValid(bridge))
        self.assertEqual(bridge.counts()["refused:delete"], 1)
        bridge.post("open", '{"id":"f1"}')
        self.assertEqual(delivered, [protocol.Request("open", "f1")])
        bridge.release()
        shiboken6.delete(page)                                          # the board's teardown: it goes with its page
        self.assertFalse(shiboken6.isValid(bridge))


# --------------------------------------------------------------------------
# The scheme and what it serves
# --------------------------------------------------------------------------

class FakeJob(QObject):
    """A QWebEngineUrlRequestJob's surface (no engine): the address, the method, fail() and reply()."""

    Error = SimpleNamespace(UrlNotFound="UrlNotFound", RequestDenied="RequestDenied")

    def __init__(self, url: str, method: bytes = b"GET") -> None:
        super().__init__()
        self._url = QUrl(url)
        self._method = method
        self.failed: object = None
        self.replied: tuple[bytes, bytes] | None = None

    def requestUrl(self) -> QUrl:  # noqa: N802 - Qt's name
        return self._url

    def requestMethod(self) -> QByteArray:  # noqa: N802 - Qt's name
        return QByteArray(self._method)

    def fail(self, error: object) -> None:
        self.failed = error

    def reply(self, kind: bytes, device: QIODevice) -> None:
        self.replied = (bytes(kind), bytes(device.readAll().data()))


class SchemeTests(unittest.TestCase):
    def serve(self, url: str, method: bytes = b"GET") -> FakeJob:
        job = FakeJob(url, method)
        self.addCleanup(job.deleteLater)
        counts: Counter[str] = Counter()
        scheme.serve(job, counts)
        self.assertEqual(sum(counts.values()), 1)
        return job

    def test_the_allowlist_is_fixed(self) -> None:
        self.assertEqual(set(scheme.FILES), {
            "board.html", "board.css", "board.js", "tokens.css", "qwebchannel.js", "fonts/chakra-petch-500.ttf",
            "fonts/chakra-petch-600.ttf", "fonts/sora.ttf", "fonts/jetbrains-mono.ttf"})
        self.assertEqual(set(scheme.FONTS.values()), set(hud.FONT_FILES))   # the three typefaces, never drifting
        self.assertEqual(scheme.BOARD_URL, "jarvis-board://board/board.html")

    def test_each_file_is_served_with_its_content_type(self) -> None:
        kinds = {"board.html": b"text/html;charset=utf-8", "board.css": b"text/css;charset=utf-8",
                 "board.js": b"text/javascript;charset=utf-8", "tokens.css": b"text/css;charset=utf-8",
                 "qwebchannel.js": b"text/javascript;charset=utf-8"}
        for name in scheme.FILES:
            job = self.serve(f"jarvis-board://board/{name}")
            self.assertIsNone(job.failed, name)
            kind, data = job.replied
            self.assertEqual(kind, kinds.get(name, b"font/ttf"), name)
            self.assertTrue(data, name)
            if name in ("board.html", "board.css", "board.js"):
                self.assertEqual(data, (ASSETS / name).read_bytes())
            elif name in scheme.FONTS:
                self.assertEqual(data, (PROJECT_ROOT / "fonts" / scheme.FONTS[name]).read_bytes())

    def test_every_other_address_fails_with_url_not_found(self) -> None:
        others = ("jarvis-board://board/", "jarvis-board://board/config.toml", "jarvis-board://board/../config.toml",
                  "jarvis-board://board/fonts/../board.html", "jarvis-board://board/./board.js",
                  "jarvis-board://board/board.html/", "jarvis-board://board/BOARD.HTML",
                  "jarvis-board://board/fonts/Sora[wght].ttf", "jarvis-board://board/fonts/", "jarvis-board://board/.env",
                  "jarvis-board://board/assets/board.js", "jarvis-board://board/board.js?v=2",
                  "jarvis-board://board/board.js#x", "jarvis-board://evil/board.html", "jarvis-board://board:81/board.js",
                  "jarvis-board://me@board/board.js", "jarvis-task://board/board.html", "http://board/board.html",
                  "file:///C:/board.html", "jarvis-board://board/qrc:/qtwebchannel/qwebchannel.js",
                  "jarvis-board://board/%2E%2E/config.toml")
        for url in others:
            job = self.serve(url)
            self.assertEqual(job.failed, "UrlNotFound", url)
            self.assertIsNone(job.replied, url)
            self.assertEqual(scheme.board_file(url), "", url)
        posted = self.serve("jarvis-board://board/board.html", b"POST")
        self.assertEqual(posted.failed, "UrlNotFound")

    def test_a_request_that_goes_wrong_is_refused_and_never_raises(self) -> None:
        class Broken(FakeJob):
            def requestUrl(self) -> QUrl:  # noqa: N802
                raise RuntimeError("gone")

        job = Broken("jarvis-board://board/board.html")
        self.addCleanup(job.deleteLater)
        counts: Counter[str] = Counter()
        scheme.serve(job, counts)
        self.assertEqual((job.failed, counts["refused"]), ("UrlNotFound", 1))
        self.assertIsNone(scheme.body("nothing.js"))
        self.assertIsNone(scheme.body(None))                             # type: ignore[arg-type]

    def test_qwebchannel_js_comes_from_qt_s_own_resource_never_a_copy(self) -> None:
        import PySide6.QtWebChannel  # noqa: F401 - its resource (QtCore only; nothing of the web engine)

        resource = QFile(scheme.QWEBCHANNEL_RESOURCE)
        self.assertTrue(resource.open(QIODevice.OpenModeFlag.ReadOnly))
        expected = bytes(resource.readAll().data())
        resource.close()
        self.assertEqual(scheme.body("qwebchannel.js"), ("text/javascript;charset=utf-8", expected))
        self.assertIn(b"QWebChannel", expected)
        copies = [path for path in PROJECT_ROOT.rglob("qwebchannel*.js") if ".git" not in path.parts]
        self.assertEqual(copies, [])

    def test_tokens_css_is_written_from_holo_at_each_request(self) -> None:
        css = scheme.tokens_css()
        for name in ("HOLO", "HOLO_HI", "TILE", "TILE_FOCUS", "TILE_AMBER", "TILE_AMBER_LIT", "TILE_ALERT", "INK_1",
                     "INK_4", "AMBER", "AMBER_HI", "ALERT", "FOCUS", "RAISED"):
            self.assertIn(f"--{name.lower().replace('_', '-')}:{QColor(getattr(holo, name)).name()};", css)
        self.assertIn("--frame-edge:rgba(60, 211, 238, 0.14)", css)
        self.assertIn("--line-strong:rgba(60, 211, 238, 0.33)", css)
        self.assertIn("--cue-amber:rgba(255, 200, 113, 0.75)", css)
        self.assertIn("--radius-amber:12px", css)
        self.assertIn("--ease-out:cubic-bezier(0.16, 1, 0.3, 1)", css)
        self.assertIn('.t-frame-title{font:500 15px/20px "Chakra Petch",sans-serif;letter-spacing:0.01em}', css)
        self.assertIn('.t-badge{font:500 10px/12px "JetBrains Mono",monospace;letter-spacing:0.16em;'
                      'text-transform:uppercase;font-variant-numeric:tabular-nums}', css)
        self.assertIn('.g-pin{d:path("M9.5 4h5 M10.5 4v4.5l-3 3.5h9l-3-3.5V4 M12 12v8")}', css)
        self.assertIn('.g-diamond{d:path("M12 4l8 8-8 8-8-8z")}', css)
        for glyph in holo.ICONS:
            self.assertIn(f".g-{glyph.replace('_', '-')}{{d:path(", css)
        for name in scheme.FONTS:
            self.assertIn(f'src:url("jarvis-board://board/{name}") format("truetype")', css)
        self.assertEqual(sorted(set(re.findall(r'url\("([^"]+)"\)', css))),
                         sorted(f"jarvis-board://board/{name}" for name in scheme.FONTS))
        with mock.patch.object(holo, "HOLO", "#123456"):                  # never a copy that drifts
            self.assertIn("--holo:#123456;", scheme.tokens_css())
        self.assertEqual(scheme.body("tokens.css")[1], scheme.tokens_css().encode("utf-8"))

    def test_registering_needs_no_application_yet_and_imports_nothing_here(self) -> None:
        before = "PySide6.QtWebEngineCore" in sys.modules
        self.assertFalse(scheme.register_scheme())                        # a QApplication exists in this process
        self.assertFalse(scheme.scheme_ready())
        self.assertEqual("PySide6.QtWebEngineCore" in sys.modules, before)

    def test_the_scheme_before_the_application_with_the_flags_of_jarvis_task(self) -> None:
        code = ("from PySide6.QtWidgets import QApplication\n"
                "from briefing_reader.board import scheme\n"
                "print(scheme.scheme_ready(), scheme.register_scheme(), scheme.register_scheme(), scheme.scheme_ready())\n"
                "app = QApplication(['x', '-platform', 'offscreen'])\n"
                "print(scheme.scheme_ready(), scheme.register_scheme())\n"
                "from PySide6.QtWebEngineCore import QWebEngineUrlScheme as S\n"
                "found = S.schemeByName(b'jarvis-board')\n"
                "print(int(found.flags().value) == int((S.Flag.SecureScheme | S.Flag.LocalScheme"
                " | S.Flag.LocalAccessAllowed).value), found.syntax().name)\n")
        done = subprocess.run([sys.executable, "-B", "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True,
                              timeout=120, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(done.stdout.split(), ["False", "True", "True", "True", "True", "True", "True", "Host"])

    def test_importing_the_board_imports_nothing_of_the_web_engine(self) -> None:
        code = ("import sys; import briefing_reader.board, briefing_reader.board.scheme, briefing_reader.board.view,"
                " briefing_reader.board.protocol; from briefing_reader.board import scheme; scheme.tokens_css();"
                " print(sorted(m for m in sys.modules if m.startswith(('PySide6.QtWebEngine', 'PySide6.QtQuick',"
                " 'PySide6.QtWebChannel'))))")
        done = subprocess.run([sys.executable, "-B", "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True,
                              timeout=120, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(done.stdout.strip(), "[]")

    def test_main_registers_it_before_the_application_on_every_start(self) -> None:
        code = ("from briefing_reader import __main__ as entry\n"
                "from briefing_reader.board import scheme\n"
                "entry._prepare_board()\n"
                "print(scheme.scheme_ready())\n")
        done = subprocess.run([sys.executable, "-B", "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True,
                              timeout=120, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(done.stdout.split(), ["True"])
        with mock.patch.object(scheme, "register_scheme", side_effect=RuntimeError("no")):
            entry._prepare_board()                                        # never stops the app


# --------------------------------------------------------------------------
# The page's own files
# --------------------------------------------------------------------------

class PageTests(unittest.TestCase):
    html = (ASSETS / "board.html").read_text(encoding="utf-8")
    js = (ASSETS / "board.js").read_text(encoding="utf-8")
    css = (ASSETS / "board.css").read_text(encoding="utf-8")

    def test_board_html_carries_the_exact_policy_before_anything_it_loads(self) -> None:
        found = re.findall(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', self.html)
        self.assertEqual(found, [CSP])
        policy_at = self.html.index("Content-Security-Policy")
        self.assertLess(policy_at, self.html.index("<link"))
        self.assertLess(policy_at, self.html.index("<script"))

    def test_board_html_has_no_inline_script_style_or_handler_and_loads_only_its_own_files(self) -> None:
        self.assertNotRegex(self.html, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(self.html, r"<style")
        self.assertNotRegex(self.html, r"\sstyle=")
        self.assertNotRegex(self.html, r"\son\w+\s*=")
        addresses = re.findall(r'(?:src|href)="([^"]+)"', self.html)
        self.assertEqual(sorted(addresses), sorted(f"jarvis-board://board/{name}" for name in
                                                   ("tokens.css", "board.css", "qwebchannel.js", "board.js")))
        self.assertEqual([scheme.board_file(address) != "" for address in addresses], [True] * 4)

    def test_board_js_never_reads_markup_or_opens_anything(self) -> None:
        forbidden = {
            "innerHTML": r"\binnerHTML\b", "insertAdjacentHTML": r"\binsertAdjacentHTML\b",
            "outerHTML": r"\bouterHTML\b", "document.write": r"document\s*\.\s*write", "eval": r"\beval\s*\(",
            "new Function": r"\bnew\s+Function\b", "Function(": r"(?<![\w.])Function\s*\(",
            "a string timer": r"set(?:Timeout|Interval)\s*\(\s*['\"`]", "an event attribute": r"setAttribute\s*\(\s*['\"]on",
            "srcdoc": r"srcdoc", "createContextualFragment": r"createContextualFragment", "DOMParser": r"DOMParser",
            "setHTML": r"setHTML", "location": r"\blocation\b", "window.open": r"window\s*\.\s*open\b",
            "fetch": r"\bfetch\s*\(", "XMLHttpRequest": r"XMLHttpRequest", "WebSocket": r"WebSocket",
            "sendBeacon": r"sendBeacon", "import()": r"\bimport\s*\(", "href": r"\bhref\b", "download": r"download",
            "a title attribute (tooltip)": r"""\.title\s*=|setAttribute\s*\(\s*['"]title""",
        }
        for name, pattern in forbidden.items():
            self.assertNotRegex(self.js, pattern, name)
        self.assertIn("textContent", self.js)
        self.assertIn("createElement", self.js)

    def test_board_css_loads_nothing(self) -> None:
        self.assertNotIn("url(", self.css)
        self.assertNotIn("@import", self.css)
        self.assertNotIn("expression(", self.css)

    def test_the_page_checks_with_the_same_caps_and_lists_as_python(self) -> None:
        def number(name: str) -> int:
            return int(re.search(rf"\b{name}\s*=\s*(\d+)", self.js).group(1))

        self.assertEqual(number("MESSAGE_CAP"), protocol.MESSAGE_CAP)
        self.assertEqual(number("PICTURE_CAP"), protocol.PICTURE_CAP)
        self.assertEqual(number("MAX_FRAMES"), protocol.MAX_FRAMES)
        self.assertIn(f"var PICTURE_PREFIX = '{protocol.PICTURE_PREFIX}';", self.js)
        self.assertIn("var ID = /^[A-Za-z0-9_-]{1,%d}$/;" % protocol.ID_CAP, self.js)
        caps = dict(re.findall(r"(\w+): (\d+)", re.search(r"var CAPS = \{([^}]*)\}", self.js).group(1)))
        self.assertEqual({key: int(value) for key, value in caps.items()},
                         {"title": protocol.TITLE_CAP, "meta": protocol.META_CAP, "text": protocol.TEXT_CAP,
                          "status_text": protocol.WORDS_CAP, "status_meta": protocol.META_CAP,
                          "waits": protocol.WAITS_CAP, "rows": protocol.ROWS_CAP, "spans": protocol.SPANS_CAP,
                          "ticks": protocol.TICKS_CAP, "tick": protocol.TICK_CAP, "pill": protocol.PILL_CAP,
                          # part 5a: a group's topic and time
                          "topic": protocol.TOPIC_CAP, "time": protocol.TIME_CAP})
        self.assertEqual(number("MAX_GROUPS"), protocol.MAX_GROUPS)
        desk = re.search(r"var DESK_STATES = \[([^\]]*)\]", self.js).group(1)
        self.assertEqual(re.findall(r"'(\w+)'", desk), list(protocol.DESK_STATES))
        self.assertEqual(number("DAY_MINUTES"), protocol.DAY_MINUTES)
        for name, values in (("CALENDAR_STATES", protocol.CALENDAR_STATES), ("SPAN_KINDS", protocol.SPAN_KINDS),
                             ("TODO_STATES", protocol.TODO_STATES)):
            found = re.search(rf"var {name} = \[([^\]]*)\]", self.js).group(1)
            self.assertEqual(re.findall(r"'(\w*)'", found), list(values), name)
        self.assertEqual(set(re.findall(r"HANDLERS\.(\w+) = function", self.js)), set(protocol.MESSAGE_TYPES))
        self.assertEqual(set(re.findall(r"KINDS\.(\w+) = function", self.js)), set(protocol.KINDS))
        self.assertEqual(set(re.findall(r"post\('(\w+)'", self.js)), set(protocol.REQUEST_KINDS))
        statuses = re.search(r"var STATUSES = \[([^\]]*)\]", self.js).group(1)
        self.assertEqual(re.findall(r"'(\w+)'", statuses), list(protocol.STATUSES))
        motions = re.search(r"var MOTIONS = \{([^}]*)\}", self.js).group(1)
        self.assertEqual(re.findall(r"(\w+):", motions), list(protocol.MOTION_LEVELS))
        fields = re.search(r"var FRAME_FIELDS = \[([^\]]*)\]", self.js).group(1)
        self.assertEqual(re.findall(r"'(\w+)'", fields), list(protocol._FRAME_FIELDS))  # noqa: SLF001

    def test_the_page_s_motion_follows_the_design(self) -> None:
        self.assertIn(":root.motion-calm { --k: 1.6; }", self.css)         # calm x1.6
        self.assertIn(":root.motion-off { --k: 0; }", self.css)            # off: each end state at once
        for timing in ("calc(260ms * var(--k))", "calc(var(--at) + 260ms * var(--k))", "calc(200ms * var(--k))",
                       "calc(900ms * var(--k))", "calc(var(--at) + 300ms * var(--k))",
                       "calc(var(--at) + 600ms * var(--k))", "calc(var(--i, 0) * 80ms * var(--k))"):
            self.assertIn(timing, self.css)
        self.assertIn("MAX_ANIMATED = 6", self.js)
        self.assertIn("STAGGER_MS = 80, DRAW_MS = 800, LAP_MS = 900, FADE_MS = 160, HEIGHT_MS = 200", self.js)
        self.assertIn("CLOSED_MS = 8000", self.js)

    def test_assets_are_lf_only(self) -> None:
        for path in ASSETS.iterdir():
            self.assertNotIn(b"\r", path.read_bytes(), path.name)


# --------------------------------------------------------------------------
# The view without the engine: delivery, the fallback, the keys
# --------------------------------------------------------------------------

class FakePage:
    def __init__(self) -> None:
        self.sources: list[tuple[str, int]] = []

    def runJavaScript(self, source: str, world: int = 0) -> None:  # noqa: N802 - Qt's name
        self.sources.append((source, world))


class ViewTests(unittest.TestCase):
    def make(self, **options: object) -> tuple[QWidget, view.BoardView]:
        host = QWidget()
        host.resize(503, 400)
        options.setdefault("scheme_ready", lambda: True)
        board = view.BoardView(host, **options)
        board.setGeometry(0, 0, 503, 400)
        self.addCleanup(host.deleteLater)
        self.addCleanup(board.shutdown)
        return host, board

    def fall_back(self, **options: object) -> tuple[view.BoardView, list[str]]:
        host, board = self.make(start_delay_ms=0, **options)
        reasons: list[str] = []
        board.unavailable.connect(reasons.append)
        host.show()
        wait_for(lambda: reasons, 3)
        return board, reasons

    def test_no_web_engine_falls_back_to_the_native_tile_by_itself(self) -> None:
        before = "PySide6.QtWebEngineCore" in sys.modules
        board, reasons = self.fall_back(engine=lambda: snapshots.R_NO_ENGINE)
        self.assertEqual(reasons, ["no_engine"])
        self.assertEqual(board.unavailable_reason(), "no_engine")
        self.assertIsNone(board.web_view())
        self.assertEqual(board.fallback().words(), (view.CANT_START, ""))
        self.assertTrue(board.fallback().isVisibleTo(board))
        # Fallen back, a message is still checked, and its frame shows as a read-only tile (part 4).
        self.assertTrue(board.apply(frame()))
        self.assertEqual(board.fallback().words(), (view.CANT_START, "READ-ONLY TILES BELOW"))
        self.assertEqual([tile["id"] for tile in board.tiles().frames()], ["f1"])
        self.assertFalse(board.apply({"type": "script"}))
        self.assertFalse(board.set_motion("calm"))
        self.assertEqual((board.counts()["tiles:frame"], board.counts()["refused:type"]), (1, 1))
        board.shutdown()
        self.assertFalse(board.apply(frame()))
        self.assertEqual(board.counts()["dropped:unavailable"], 1)
        self.assertEqual("PySide6.QtWebEngineCore" in sys.modules, before)

    def test_every_reason_the_engine_gives_falls_back(self) -> None:
        for reason, word in ((snapshots.R_SANDBOX_OFF, "sandbox"), (snapshots.R_UNSAFE_FLAGS, "unsafe_flags"),
                             (snapshots.R_ENGINE_FAILED, "engine_failed"), (snapshots.R_NO_ENGINE, "no_engine")):
            board, reasons = self.fall_back(engine=lambda reason=reason: reason)
            self.assertEqual(reasons, [word])

    def test_no_scheme_falls_back_without_asking_the_engine(self) -> None:
        asked: list[int] = []
        board, reasons = self.fall_back(scheme_ready=lambda: False, engine=lambda: asked.append(1) or "")
        self.assertEqual((reasons, asked), (["no_scheme"], []))

    def test_a_page_that_can_t_be_made_falls_back(self) -> None:
        with mock.patch.object(view.BoardView, "_build", side_effect=RuntimeError("SECRET")), \
                self.assertLogs("briefing_reader.board.view", "INFO") as logs:
            board, reasons = self.fall_back(engine=lambda: "")
        self.assertEqual(reasons, ["engine_failed"])
        self.assertIn("RuntimeError", "\n".join(logs.output))
        self.assertNotIn("SECRET", "\n".join(logs.output))              # the kind of error, never its words

    def test_the_engine_starts_after_a_delay_never_on_the_first_paint(self) -> None:
        asked: list[int] = []
        host, board = self.make(start_delay_ms=150, engine=lambda: asked.append(1) or snapshots.R_NO_ENGINE)
        host.show()
        settle()
        self.assertEqual(asked, [])                                      # the window paints first
        board.start()
        wait_for(lambda: asked, 3)
        settle()
        board.start()
        self.assertEqual(asked, [1])

    def test_messages_wait_for_the_page_and_known_ids_follow_them(self) -> None:
        host, board = self.make(start_delay_ms=10 ** 6)
        for index in range(protocol.MAX_FRAMES):
            self.assertTrue(board.apply(frame(f"f{index}")))
        self.assertFalse(board.apply(frame("one-more")))
        self.assertEqual(board.counts()["refused:full"], 1)
        for _ in range(view.QUEUE_CAP + 6 - protocol.MAX_FRAMES):
            board.apply({"type": "lit", "id": "f1"})
        self.assertEqual(board.counts()["dropped:queue"], 6)
        self.assertEqual(len(board.known_ids()), protocol.MAX_FRAMES)
        board.apply({"type": "remove", "id": "f1"})
        self.assertNotIn("f1", board.known_ids())
        self.assertFalse(board.apply({"type": "lit", "id": "f1"}))       # gone: no longer a frame the app sent
        board.apply({"type": "clear"})
        self.assertEqual(board.known_ids(), frozenset())
        self.assertFalse(board.apply({"type": "script", "text": "alert(1)"}))
        self.assertEqual(board.counts()["refused:type"], 1)

    def test_a_ready_page_gets_board_apply_with_one_json_string_in_its_main_world(self) -> None:
        host, board = self.make(start_delay_ms=10 ** 6)
        page = FakePage()
        board._page, board._phase = page, "ready"                        # noqa: SLF001 - the delivery, no engine
        hostile = "</script><script>alert(1)</script>\u2028\u2029\"'\\\x00"
        self.assertTrue(board.apply(frame(title=hostile[:60], body={"text": hostile})))
        self.assertTrue(board.set_motion("Calm"))
        (source, world), (motion, _world) = page.sources
        self.assertEqual(world, 0)                                        # QWebEngineScript.MainWorld
        self.assertTrue(source.startswith("board.apply(") and source.endswith(");"))
        argument = json.loads(source[len("board.apply("):-2])            # exactly one JSON string literal
        self.assertEqual(json.loads(argument)["body"]["text"], hostile)
        self.assertTrue(source.isascii())
        self.assertNotIn("\n", source)
        self.assertEqual(json.loads(json.loads(motion[len("board.apply("):-2])), {"type": "motion", "level": "calm"})
        board._page = None                                               # noqa: SLF001

    def test_set_motion_reads_as_holo_does_and_follows_reduced_motion(self) -> None:
        host, board = self.make(start_delay_ms=10 ** 6)
        self.addCleanup(holo.set_reduced_motion, None)
        holo.set_reduced_motion(False)
        board.set_motion("Calm")
        self.assertEqual(board.motion(), "calm")
        board.set_motion("warp")
        self.assertEqual(board.motion(), "lively")
        holo.set_reduced_motion(True)
        self.assertEqual(board.motion(), "off")
        holo.set_reduced_motion(False)
        self.assertEqual(board.motion(), holo.motion_level())

    def test_each_request_the_page_makes_is_its_own_typed_signal(self) -> None:
        host, board = self.make(start_delay_ms=10 ** 6)
        seen: list[tuple] = []
        for name in ("frameFocused", "openRequested", "closeRequested", "bringBackRequested", "retryRequested",
                     "approvalsRequested"):
            getattr(board, name).connect(lambda frame_id, n=name: seen.append((n, frame_id)))
        board.pinRequested.connect(lambda frame_id, on: seen.append(("pinRequested", frame_id, on)))
        board.layoutChanged.connect(lambda layout: seen.append(("layoutChanged", layout)))
        board.ready.connect(lambda: seen.append(("ready",)))
        for kind in ("focus", "open", "close", "bring_back", "retry", "go_approvals"):
            board._on_request(protocol.Request(kind, "f1"))              # noqa: SLF001 - as the bridge delivers
        board._on_request(protocol.Request("pin", "f1", on=True))        # noqa: SLF001
        board._on_request(protocol.Request("layout", places=(("f1", 3, 4),), scrolling=True))  # noqa: SLF001
        board._on_request(protocol.Request("ready"))                     # noqa: SLF001 - not while it loads: dropped
        self.assertEqual(seen, [
            ("frameFocused", "f1"), ("openRequested", "f1"), ("closeRequested", "f1"), ("bringBackRequested", "f1"),
            ("retryRequested", "f1"), ("approvalsRequested", "f1"), ("pinRequested", "f1", True),
            ("layoutChanged", view.BoardLayout((("f1", 3, 4),), True))])
        self.assertEqual(board.counts()["dropped:ready_again"], 1)

    def test_shutdown_is_final(self) -> None:
        host, board = self.make(start_delay_ms=10 ** 6)
        opened: list[str] = []
        board.openRequested.connect(opened.append)
        board.shutdown()
        board.shutdown()
        self.assertFalse(board.apply(frame()))
        board.start()
        settle()
        self.assertIsNone(board.fallback())
        board._on_request(protocol.Request("open", "f1"))               # noqa: SLF001 - a page on its way out
        self.assertEqual(opened, [])
        self.assertEqual(board.counts()["dropped:closed"], 1)


class FallbackTileTests(unittest.TestCase):
    def test_its_words_and_the_line_under_them(self) -> None:
        tile = view.BoardFallback()
        self.addCleanup(tile.deleteLater)
        self.assertEqual(view.CANT_START, "The board can't start on this PC. Answers still show here as text.")
        self.assertEqual(tile.words(), (view.CANT_START, ""))
        self.assertEqual(tile.accessibleName(), view.CANT_START)
        self.assertEqual(tile.focusPolicy(), Qt.FocusPolicy.NoFocus)
        tile.set_tiles_below(True)
        self.assertEqual(tile.words(), (view.CANT_START, "READ-ONLY TILES BELOW"))

    def test_it_paints_the_raised_ground_and_wraps_its_words(self) -> None:
        tile = view.BoardFallback()
        self.addCleanup(tile.deleteLater)
        tile.resize(448, tile.heightForWidth(448) if tile.hasHeightForWidth() else 64)
        tile.setFixedWidth(260)
        tile.adjustSize()
        image = tile.grab().toImage()
        self.assertEqual(image.pixelColor(30, image.height() - 4).name(), QColor(holo.RAISED).name())
        words = tile.findChild(QWidget, "boardCantStart")
        self.assertTrue(words.wordWrap())
        self.assertGreater(words.height(), 24)                           # two lines at 260 px: never cut


class KeyTests(unittest.TestCase):
    def test_the_window_s_keys(self) -> None:
        names = [QKeySequence(Qt.KeyboardModifier.ControlModifier | key).toString() for key in view.WINDOW_KEYS]
        self.assertEqual(names, ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6", "Ctrl+7", "Ctrl+8",
                                 "Ctrl+9", "Ctrl+,", "Ctrl+K", "Ctrl+Space", "Ctrl+M", "Ctrl+B"])

    def test_only_ctrl_and_one_of_them(self) -> None:
        ctrl, shift = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier
        keypad, alt = Qt.KeyboardModifier.KeypadModifier, Qt.KeyboardModifier.AltModifier
        self.assertTrue(view.is_window_key(Qt.Key.Key_1, ctrl))
        self.assertTrue(view.is_window_key(int(Qt.Key.Key_8.value), ctrl))
        self.assertTrue(view.is_window_key(Qt.Key.Key_3, ctrl | keypad))
        self.assertTrue(view.is_window_key(Qt.Key.Key_Space, ctrl))
        self.assertFalse(view.is_window_key(Qt.Key.Key_1, ctrl | shift))
        self.assertFalse(view.is_window_key(Qt.Key.Key_1, ctrl | alt))
        self.assertFalse(view.is_window_key(Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier))
        self.assertFalse(view.is_window_key(Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        self.assertFalse(view.is_window_key(Qt.Key.Key_P, ctrl))
        self.assertFalse(view.is_window_key(Qt.Key.Key_C, ctrl))           # copy stays the page's
        self.assertFalse(view.is_window_key("x", ctrl))

    def test_the_filter_refuses_shortcut_override_for_them_only(self) -> None:
        checks: list[int] = []
        owner = QObject()
        self.addCleanup(owner.deleteLater)
        keys = view.WindowKeysFilter(owner, lambda: checks.append(1))
        target = QObject(owner)
        override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
        override.accept()
        self.assertTrue(keys.eventFilter(target, override))
        self.assertFalse(override.isAccepted())                           # on to the window: its shortcut fires
        self.assertEqual(keys.refused, 1)
        copy = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        copy.accept()
        self.assertFalse(keys.eventFilter(target, copy))
        self.assertTrue(copy.isAccepted())                                # left alone
        keys.view = target                                                # the view sees it too: counted once
        self.assertTrue(keys.eventFilter(target, override))
        self.assertEqual(keys.refused, 1)
        from PySide6.QtCore import QChildEvent
        from PySide6.QtGui import QFocusEvent

        keys.eventFilter(target, QFocusEvent(QEvent.Type.FocusIn))
        keys.eventFilter(target, QChildEvent(QEvent.Type.ChildPolished, target))
        self.assertEqual(checks, [1, 1])
        keys.eventFilter(target, QChildEvent(QEvent.Type.ChildAdded, target))
        settle()
        self.assertEqual(checks, [1, 1, 1])                               # after the view set its new proxy

    def test_with_qt_s_own_dispatch_a_greedy_focus_widget_keeps_them_only_without_the_filter(self) -> None:
        class Greedy(QWidget):
            """Takes every key, as Chromium may in the page."""

            def event(self, event: QEvent) -> bool:  # noqa: N802
                if event.type() == QEvent.Type.ShortcutOverride:
                    event.accept()
                    return True
                return super().event(event)

        window = QWidget()
        self.addCleanup(window.deleteLater)
        fired: list[str] = []
        for name in ("Ctrl+K", "Ctrl+1"):
            shortcut = QShortcut(QKeySequence(name), window)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda name=name: fired.append(name))
        greedy = Greedy(window)
        greedy.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        window.show()
        window.activateWindow()
        self.assertTrue(wait_for(lambda: QApplication.activeWindow() is window, 3))
        greedy.setFocus()
        self.assertTrue(wait_for(lambda: QApplication.focusWidget() is greedy, 3))
        QTest.keyClick(greedy, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(fired, [])                                        # the control: the page keeps it
        greedy.installEventFilter(view.WindowKeysFilter(window, lambda: None))
        QTest.keyClick(greedy, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(greedy, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(fired, ["Ctrl+K", "Ctrl+1"])


class KeepsSpaceTests(unittest.TestCase):
    def test_the_board_keeps_space_as_a_text_field_does(self) -> None:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        board = view.BoardView(host, start_delay_ms=10 ** 6, scheme_ready=lambda: True)
        self.addCleanup(board.shutdown)
        inner = QWidget(QWidget(board))                                   # as deep as the view's focus proxy
        outside = QWidget(host)
        self.assertTrue(view.keeps_space(inner))
        self.assertTrue(view.keeps_space(board))
        self.assertFalse(view.keeps_space(outside))
        self.assertFalse(view.keeps_space(None))
        self.assertTrue(ui._keeps_space(inner))                           # noqa: SLF001 - the one-line change
        self.assertFalse(ui._keeps_space(outside))                        # noqa: SLF001
        self.assertTrue(ui._keeps_space(QLineEdit()))                     # noqa: SLF001 - as before
        self.assertTrue(ui._keeps_space(QPushButton()))                   # noqa: SLF001
        self.assertTrue(ui._keeps_space(QPlainTextEdit()))                # noqa: SLF001
        read_only = QTextEdit()
        read_only.setReadOnly(True)
        self.assertFalse(ui._keeps_space(read_only))                      # noqa: SLF001 - the transcript still plays
        self.assertFalse(ui._keeps_space(None))                           # noqa: SLF001


# --------------------------------------------------------------------------
# The snapshot_engine helpers the board starts Chromium through
# --------------------------------------------------------------------------

class EngineHelperTests(unittest.TestCase):
    def test_ensure_engine_judges_the_inherited_settings_before_anything_starts(self) -> None:
        with mock.patch.object(snapshot_engine, "_start_engine", return_value="") as start, \
                mock.patch.object(snapshots, "sandbox_problem", return_value=snapshots.R_SANDBOX_OFF):
            self.assertEqual(snapshot_engine.ensure_engine(), snapshots.R_SANDBOX_OFF)
        start.assert_not_called()
        with mock.patch.object(snapshot_engine, "_start_engine", return_value="") as start, \
                mock.patch.object(snapshots, "sandbox_problem", return_value=""), \
                mock.patch.object(snapshots, "engine_flags_problem", return_value=snapshots.R_UNSAFE_FLAGS):
            self.assertEqual(snapshot_engine.ensure_engine(), snapshots.R_UNSAFE_FLAGS)
        start.assert_not_called()
        with mock.patch.object(snapshot_engine, "_start_engine", return_value="") as start, \
                mock.patch.object(snapshots, "sandbox_problem", return_value=""), \
                mock.patch.object(snapshots, "engine_flags_problem", return_value=""):
            self.assertEqual(snapshot_engine.ensure_engine(), "")
        start.assert_called_once_with()
        with mock.patch.object(snapshot_engine, "QCoreApplication") as core, \
                mock.patch.object(snapshot_engine, "_start_engine") as start:
            core.instance.return_value = None
            self.assertEqual(snapshot_engine.ensure_engine(), snapshots.R_ENGINE_FAILED)
        start.assert_not_called()
        with mock.patch.object(snapshot_engine, "_judged_environ", side_effect=RuntimeError("boom")):
            self.assertEqual(snapshot_engine.ensure_engine(), snapshots.R_ENGINE_FAILED)

    def test_private_profile_is_off_the_record_with_nothing_kept_and_starts_chromium_once(self) -> None:
        calls: list[tuple] = []

        class Profile:
            HttpCacheType = SimpleNamespace(NoCache="no-cache")
            PersistentCookiesPolicy = SimpleNamespace(NoPersistentCookies="no-cookies")
            PersistentPermissionsPolicy = SimpleNamespace(AskEveryTime="ask")
            record = True

            def __init__(self, parent: object = None) -> None:
                calls.append(("made", parent))

            def isOffTheRecord(self) -> bool:  # noqa: N802
                return self.record

            def __getattr__(self, name: str):
                return lambda *args: calls.append((name, *args))

        classes = SimpleNamespace(QWebEngineProfile=Profile)
        with mock.patch.object(snapshot_engine, "_classes", return_value=classes), \
                mock.patch.object(snapshot_engine._ENGINE, "started", False):  # noqa: SLF001
            profile = snapshot_engine.private_profile("parent")
            self.assertIsInstance(profile, Profile)
            self.assertTrue(snapshot_engine._ENGINE.started)                    # noqa: SLF001
            self.assertEqual(calls, [("made", "parent"), ("setHttpCacheType", "no-cache"),
                                     ("setPersistentCookiesPolicy", "no-cookies"),
                                     ("setPersistentPermissionsPolicy", "ask"), ("setSpellCheckEnabled", False),
                                     ("setPushServiceEnabled", False)])
            calls.clear()
            Profile.record = False
            self.assertIsNone(snapshot_engine.private_profile())
            self.assertEqual(calls, [("made", None), ("deleteLater",)])

    def test_apply_web_settings_sets_the_two_lists_and_local_storage(self) -> None:
        names = snapshot_engine._SETTINGS_OFF + snapshot_engine._SETTINGS_ON   # noqa: SLF001
        classes = SimpleNamespace(QWebEngineSettings=SimpleNamespace(
            WebAttribute=SimpleNamespace(**{name: name for name in names}),
            UnknownUrlSchemePolicy=SimpleNamespace(DisallowUnknownUrlSchemes="refuse-unknown"),
            ImageAnimationPolicy=SimpleNamespace(AnimateOnce="once")))
        for local_storage in (False, True):
            values: dict[str, object] = {}
            settings = SimpleNamespace(setAttribute=values.__setitem__,
                                       setUnknownUrlSchemePolicy=lambda policy: values.__setitem__("schemes", policy),
                                       setImageAnimationPolicy=lambda policy: values.__setitem__("animation", policy))
            with mock.patch.object(snapshot_engine, "_classes", return_value=classes):
                snapshot_engine.apply_web_settings(settings, local_storage=local_storage)
            for name in snapshot_engine._SETTINGS_OFF:                          # noqa: SLF001
                self.assertIs(values[name], False, name)
            for name in snapshot_engine._SETTINGS_ON:                           # noqa: SLF001
                self.assertIs(values[name], local_storage if name == "LocalStorageEnabled" else True, name)
            self.assertEqual((values["schemes"], values["animation"]), ("refuse-unknown", "once"))
        self.assertIn("JavascriptEnabled", snapshot_engine._SETTINGS_ON)       # noqa: SLF001
        self.assertIn("JavascriptCanAccessClipboard", snapshot_engine._SETTINGS_OFF)   # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
