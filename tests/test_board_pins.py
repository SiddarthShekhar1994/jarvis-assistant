"""The board's pins across a restart, and a closed frame's Bring back held while the owner is on its row (the
canvas-pins brief). Offscreen; the web engine never starts here: the board is tests.board_fakes' stand-in (a real
BoardView whose page is a plain widget: every message checked and kept); tests/test_board_lockdown.py's "expiry" case
runs the page's own hold in the real engine.

Covered: board.pins - which pinned frames may be kept (Jarvis's own answers and what Jarvis keeps on this PC already;
never mail, the calendar, a page picture, a web search's results, the follow-ups or the canvas's "Now"), the file
(atomic, LF, versioned, bounded, written only when the pins changed, gone with the last pin; a damaged, too large or
hostile one read as no pins and logged by its reason word only, then replaced; a newer one never written over; never
outside the temporary folder while the tests run); the feed's ``pinned`` and pinned_view; the desk saving after a pin,
an unpin, a close and a Bring back, and putting the kept pins back pinned, in their order, in one group "Pinned" when
the board is made (at most MAX_PINS), with no landing sound; the hold request (its fields and hostile shapes), the
feed's hold (a held closed frame never leaves; let go, it leaves CLOSE_S later; a page that came up again lets every
hold go) and the desk's timer following it; the page's script (its hold on the row's pointer and focus, its CLOSED_MS
started again, close posted before its row takes the focus). No words in the logs or in the file of a frame that is not
kept.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PySide6.QtWidgets import QApplication

from briefing_reader import holo, ui
from briefing_reader.board import ask_frames, pins, protocol
from briefing_reader.board import frames as board_frames
from briefing_reader.board.feed import CLOSE_S, BoardFeed
from tests import board_fakes
from tests.ui_fakes import AppHarness, fonts, settle

_app: QApplication | None = None
ASSETS = Path(__file__).resolve().parent.parent / "briefing_reader" / "board" / "assets"
TABLE = {"kind": "table", "title": "Budget lines", "unit": "USD", "changed": 1, "change": "+500 Software",
         "columns": ["Line", "Friday", "Today"], "rows": [["Travel", 1200, 1200], ["Software", 2900, 3400]]}
SECRET = "Secret mail words"


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_board_pins", "-platform", "offscreen"])
    fonts()


def text_frame(frame_id: str, title: str = "Budget lines", **extra: Any) -> dict[str, Any]:
    message = {"type": "frame", "id": frame_id, "kind": "text", "title": title, "meta": "",
               "body": {"text": "Words."}, "waits": ""}
    message.update(extra)
    return message


def research(title: str) -> Any:
    return lambda fid: board_frames.from_research(
        SimpleNamespace(answer=f"Words of {title}.", sources=(("A source", "https://example.edu/a"),)),
        frame_id=fid, title=title)


def table(title: str = "Budget lines") -> tuple[Any, str]:
    frame = ask_frames.check_frame(dict(TABLE, title=title))
    assert isinstance(frame, ask_frames.AskFrame)
    return (lambda fid: ask_frames.build(frame, frame_id=fid)), frame.key


def mail(fid: str) -> board_frames.Built:
    return board_frames.Built(({"type": "frame", "id": fid, "kind": "mail_thread", "title": "Dana Reyes", "meta": "",
                                "body": {"sender": "Dana Reyes", "time": "8:12 AM", "text": SECRET}, "waits": ""},))


def pinned_frame(kind: str, sources: tuple[str, ...], key: str = "", group: str = "g1") -> Any:
    frame = text_frame("f1", kind=kind)
    if kind == "mail_thread":
        frame["body"] = {"sender": "Dana", "time": "", "text": SECRET}
    elif kind == "todos":
        frame["body"] = {"rows": [{"text": "Essay", "meta": "", "state": "open"}]}
    elif kind == "calendar":
        frame["body"] = {"rows": []}
    return SimpleNamespace(id="f1", group=group, sources=sources, key=key, frame=frame, approvals="")


# --------------------------------------------------------------------------
# board.pins: what may be kept, and the file
# --------------------------------------------------------------------------

class KeepRuleTests(unittest.TestCase):
    def test_jarvis_s_own_answers_and_what_jarvis_keeps_here_are_kept(self) -> None:
        for kind, sources, key in (("text", ("research",), ""), ("table", ("ask:0",), "table:budget lines"),
                                   ("chart", ("ask:1",), "chart:q4"), ("number", ("ask:1:number",), "number:x"),
                                   ("timeline", ("ask:2",), ""), ("maths", ("instant",), "maths:sum"),
                                   ("capture", ("capture",), ""), ("todos", ("todos",), "todos"),
                                   ("text", ("doc",), "doc:abc123"), ("table", ("sheet",), "sheet:abc123"),
                                   ("text", ("research#3",), "")):     # a pin that came back keeps its class
            with self.subTest(kind=kind, sources=sources):
                self.assertTrue(pins.keepable(kind, sources, key))

    def test_mail_the_calendar_pages_searches_follow_ups_are_never_kept(self) -> None:
        for kind, sources, key in (("mail_thread", ("step:3",), "mail:work:t1"),          # a mail Jarvis read
                                   ("mail_thread", ("card:abc",), "card:abc"),            # a reply that waits
                                   ("mail_thread", ("step:3", "card:abc"), "mail:work:t1"),   # the two as one
                                   ("calendar", ("calendar:today",), "day:2026-11-27"),
                                   ("calendar", ("step:2",), ""), ("picture", ("step:5",), "page:https://x"),
                                   ("text", ("step:6",), "search:words"), ("mail_list", ("followups",), "followups"),
                                   ("text", ("doc",), ""), ("text", ("doc",), "page:x"),   # a document needs its id
                                   ("mail_thread", ("research",), ""), ("text", ("ask:0",), ""),
                                   ("approvals", ("approvals",), ""), ("agents", ("agents",), ""),
                                   ("text", (), "")):
            with self.subTest(kind=kind, sources=sources):
                self.assertFalse(pins.keepable(kind, sources, key))

    def test_kept_leaves_out_the_now_group_and_keeps_the_order_and_six_at_most(self) -> None:
        items = [pinned_frame("todos", ("todos",), "todos", group="g9"),              # the canvas's "Now"
                 pinned_frame("mail_thread", ("step:3",), "mail:work:t1"),
                 pinned_frame("text", ("research",))]
        items += [pinned_frame("text", (f"ask:{index}",)) for index in range(3)]     # text is no typed kind
        items += [SimpleNamespace(id=f"f{index}", group="g1", sources=("research",), key="",
                                  frame=text_frame("f1", title=f"Answer {index}"), approvals="") for index in range(8)]
        found = pins.kept(items, quiet_groups={"g9"})
        self.assertEqual(len(found), protocol.MAX_PINS)
        self.assertEqual([pin.frame["title"] for pin in found],
                         ["Budget lines"] + [f"Answer {index}" for index in range(5)])
        self.assertTrue(all(pin.source == "research" for pin in found))

    def test_a_pin_is_checked_as_the_page_would_take_it(self) -> None:
        self.assertIsNone(pins.make_pin("research", "", "", {"kind": "text", "title": "x" * 500, "body": {}}))
        self.assertIsNone(pins.make_pin("research", "", "", {"kind": "text", "title": "T", "body": {"evil": 1}}))
        self.assertIsNone(pins.make_pin("research", "", "javascript:alert(1)", text_frame("f1")))
        self.assertIsNone(pins.make_pin("Research!", "", "", text_frame("f1")))
        self.assertIsNone(pins.make_pin("research", "a\nb", "", text_frame("f1")))
        self.assertIsNone(pins.make_pin("research", 5, "", text_frame("f1")))
        pin = pins.make_pin("research", "", "a1b2", dict(text_frame("f9"), onclick="x"))
        self.assertNotIn("onclick", pin.frame)                                   # only a frame's own fields are kept
        pin = pins.make_pin("research#2", "", "a1b2", dict(text_frame("f9"), pinned=True, closed=True, changed=True))
        self.assertEqual(pin.source, "research")
        self.assertEqual(sorted(pin.frame), ["body", "kind", "meta", "status", "status_meta", "status_text", "title",
                                             "waits"])                          # never the app's flags or an id
        self.assertEqual(pin.message("f4")["id"], "f4")
        self.assertEqual(repr(pin), "Pin(text)")


class PinStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / pins.PINS_FILE

    def two(self) -> list[pins.Pin]:
        return [pins.make_pin("research", "", "", text_frame("f1", "First")),
                pins.make_pin("ask:0", "table:budget lines", "c0ffee", ask_frames.build(
                    ask_frames.check_frame(TABLE), frame_id="f2").messages[0])]

    def test_saved_and_read_back_in_order(self) -> None:
        store = pins.PinStore(self.root)
        self.assertEqual(store.load(), [])
        self.assertFalse(self.path.exists())
        self.assertTrue(store.save(self.two()))
        raw = self.path.read_bytes()
        self.assertEqual(raw.count(b"\r"), 0)
        payload = json.loads(raw)
        self.assertEqual(payload["version"], pins.VERSION)
        self.assertEqual([item["source"] for item in payload["pins"]], ["research", "ask:0"])
        back = pins.PinStore(self.root).load()
        self.assertEqual([(pin.source, pin.key, pin.approvals, pin.kind) for pin in back],
                         [("research", "", "", "text"), ("ask:0", "table:budget lines", "c0ffee", "table")])
        self.assertEqual(back[1].frame, self.two()[1].frame)
        self.assertEqual(list(self.root.glob("*.tmp")), [])                       # no temporary file left

    def test_written_only_when_the_pins_changed_and_gone_with_the_last(self) -> None:
        store = pins.PinStore(self.root)
        store.load()
        store.save(self.two())
        os.utime(self.path, (1_000_000, 1_000_000))
        self.assertTrue(store.save(self.two()))
        self.assertEqual(self.path.stat().st_mtime, 1_000_000)                   # the same pins: not written
        self.assertTrue(store.save(self.two()[:1]))
        self.assertNotEqual(self.path.stat().st_mtime, 1_000_000)
        self.assertTrue(store.save([]))
        self.assertFalse(self.path.exists())                                     # nothing pinned: no file

    def test_a_damaged_file_is_no_pins_logged_by_its_word_and_replaced(self) -> None:
        for raw, word in ((b"{not json " + SECRET.encode(), "damaged"), (b'{"version": 1, "pins": {}}', "damaged"),
                          (b'{"version": true, "pins": []}', "damaged"), (b"[1, 2]", "damaged"),
                          (b"\xff\xfe", "damaged"), (b" " * (pins.MAX_FILE_BYTES + 1), "too_large")):
            with self.subTest(word=word, raw=raw[:20]):
                self.path.write_bytes(raw)
                store = pins.PinStore(self.root)
                with self.assertLogs("briefing_reader.board.pins", logging.WARNING) as logs:
                    self.assertEqual(store.load(), [])
                self.assertEqual(store.problem, word)
                self.assertEqual(logs.output, [f"WARNING:briefing_reader.board.pins:Board: board_pins.json is {word};"
                                               " no pins came back"])
                self.assertTrue(store.save(self.two()))                         # the next save replaces it
                self.assertEqual(len(pins.PinStore(self.root).load()), 2)

    def test_hostile_pins_in_the_file_are_left_out(self) -> None:
        good = self.two()[0].data()
        records = [good, "x", {"source": "step:3", "key": "mail:work:t1", "approvals": "",
                               "frame": {"kind": "mail_thread", "title": "Dana", "body": {"sender": "Dana", "time": "",
                                                                                          "text": SECRET}}},
                   dict(good, frame=dict(good["frame"], body={"text": "<b>" * 300})),
                   dict(good, frame=dict(good["frame"], body={"text": "<b>" * 100})),
                   dict(good, frame=dict(good["frame"], kind="script")), dict(good, approvals=7), good]
        self.path.write_text(json.dumps({"version": 1, "pins": records}), encoding="utf-8")
        with self.assertLogs("briefing_reader.board.pins", logging.WARNING) as logs:
            back = pins.PinStore(self.root).load()
        self.assertEqual(len(back), 3)                                           # "<b>" is only text: it checks
        self.assertEqual(back[1].frame["body"]["text"], "<b>" * 100)
        self.assertEqual(logs.output, ["WARNING:briefing_reader.board.pins:Board: 5 kept pin(s) did not check and "
                                       "are left out"])

    def test_at_most_six_come_back(self) -> None:
        records = [dict(self.two()[0].data(), frame=text_frame("f1", f"Answer {index}")) for index in range(9)]
        for record in records:
            record["frame"].pop("type")
            record["frame"].pop("id")
        self.path.write_text(json.dumps({"version": 1, "pins": records}), encoding="utf-8")
        with self.assertLogs("briefing_reader.board.pins", logging.WARNING):
            back = pins.PinStore(self.root).load()
        self.assertEqual([pin.frame["title"] for pin in back], [f"Answer {index}" for index in range(6)])
        store = pins.PinStore(self.root)
        self.assertTrue(store.save(back + back))
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))["pins"]), 6)

    def test_a_newer_file_is_never_written_over(self) -> None:
        self.path.write_text(json.dumps({"version": pins.VERSION + 1, "pins": [], "more": 1}), encoding="utf-8")
        store = pins.PinStore(self.root)
        with self.assertLogs("briefing_reader.board.pins", logging.WARNING):
            self.assertEqual(store.load(), [])
        self.assertEqual(store.problem, "newer")
        self.assertFalse(store.save(self.two()))
        self.assertFalse(store.save([]))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["more"], 1)

    @unittest.skipUnless(os.environ.get("JARVIS_DESKTOP_TEST_GUARD") == "1", "the test guard is off")
    def test_never_outside_the_temporary_folder_while_the_tests_run(self) -> None:
        outside = Path(__file__).resolve().parent / "no_such_folder_for_pins"
        store = pins.PinStore(outside)
        self.assertEqual(store.load(), [])
        self.assertFalse(store.save(self.two()))
        self.assertFalse(outside.exists())


# --------------------------------------------------------------------------
# board.feed: pinned frames put up, and the closed row's hold
# --------------------------------------------------------------------------

class FeedTests(unittest.TestCase):
    def test_put_pinned_and_pinned_view_in_board_order(self) -> None:
        feed = BoardFeed()
        ids = []
        for index in range(8):
            frame_id = feed.frame_id(1, f"s{index}")
            feed.put(1, f"s{index}", [text_frame(frame_id, f"Answer {index}")], when="9:41 AM", pinned=True,
                     rank=float(index))
            ids.append(frame_id)
        self.assertEqual(feed.pinned(), protocol.MAX_PINS)                       # never a 7th
        self.assertEqual([feed.flags(frame_id)["pinned"] for frame_id in ids], [True] * 6 + [False] * 2)
        self.assertTrue(feed.frame_message(ids[0])["pinned"])
        feed.close(ids[1], 0.0)
        view = feed.pinned_view()
        self.assertEqual([item.id for item in view], [ids[0]] + ids[2:6])         # a closed one is not kept
        self.assertEqual(view[0].sources, ("s0",))
        self.assertEqual(view[0].frame["title"], "Answer 0")
        self.assertNotIn("pinned", view[0].frame)
        self.assertEqual(repr(view[0]), f"PinnedFrame({ids[0]}, text)")

    def closed(self) -> tuple[BoardFeed, str]:
        feed = BoardFeed()
        frame_id = feed.frame_id(1, "a")
        feed.put(1, "a", [text_frame(frame_id)], when="9:41 AM")
        feed.close(frame_id, 100.0)
        return feed, frame_id

    def test_a_held_closed_frame_never_leaves_and_let_go_it_leaves_close_s_later(self) -> None:
        feed, frame_id = self.closed()
        self.assertEqual(feed.next_due(), 100.0 + CLOSE_S)
        self.assertEqual(feed.hold(frame_id, True, 103.0), [])
        self.assertIsNone(feed.next_due())
        self.assertEqual(feed.due(10_000.0), [])                                 # an hour on the row: still there
        self.assertTrue(feed.flags(frame_id)["closed"])
        feed.hold(frame_id, True, 10_001.0)                                      # (again: nothing changes)
        self.assertEqual(feed.counts()["held"], 1)
        feed.hold(frame_id, False, 10_002.0)
        self.assertEqual(feed.next_due(), 10_002.0 + CLOSE_S)                    # its 8 s start again
        self.assertEqual(feed.due(10_002.0 + CLOSE_S - 0.1), [])
        sent = feed.due(10_002.0 + CLOSE_S)
        self.assertEqual(sent[0], {"type": "remove_group", "id": "g1"})
        self.assertEqual(feed.frame_ids(), [])

    def test_hold_on_a_frame_not_closed_or_let_go_without_a_hold_changes_nothing(self) -> None:
        feed = BoardFeed()
        frame_id = feed.frame_id(1, "a")
        feed.put(1, "a", [text_frame(frame_id)], when="9:41 AM")
        feed.hold(frame_id, True, 1.0)
        feed.close(frame_id, 2.0)
        self.assertEqual(feed.next_due(), 2.0 + CLOSE_S)                         # the close's own 8 s
        feed.hold(frame_id, False, 5.0)
        self.assertEqual(feed.next_due(), 2.0 + CLOSE_S)
        self.assertEqual(feed.hold("f99", True, 5.0), [])

    def test_bring_back_ends_a_hold_and_a_new_close_starts_its_own_8_s(self) -> None:
        feed, frame_id = self.closed()
        feed.hold(frame_id, True, 101.0)
        feed.bring_back(frame_id)
        feed.hold(frame_id, False, 102.0)                                        # (late: nothing)
        self.assertIsNone(feed.next_due())
        feed.close(frame_id, 200.0)
        self.assertEqual(feed.next_due(), 200.0 + CLOSE_S)

    def test_release_holds_lets_every_hold_go(self) -> None:
        feed, frame_id = self.closed()
        feed.hold(frame_id, True, 101.0)
        feed.release_holds(150.0)
        self.assertEqual(feed.next_due(), 150.0 + CLOSE_S)


class HoldRequestTests(unittest.TestCase):
    known = ("f1", "f2")

    def refused(self, payload: str) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_request("hold", payload, self.known)
        return caught.exception.reason

    def test_the_hold_request_and_its_hostile_shapes(self) -> None:
        self.assertEqual(protocol.check_request("hold", '{"id":"f2","on":true}', self.known),
                         protocol.Request("hold", "f2", on=True))
        self.assertEqual(protocol.check_request("hold", '{"id":"f1","on":false}', self.known),
                         protocol.Request("hold", "f1", on=False))
        self.assertEqual(self.refused('{"id":"f1"}'), protocol.R_FIELD)
        self.assertEqual(self.refused('{"id":"f1","on":1}'), protocol.R_SHAPE)
        self.assertEqual(self.refused('{"id":"f9","on":true}'), protocol.R_ID)
        self.assertEqual(self.refused('{"id":"f1","on":true,"ms":99999}'), protocol.R_FIELD)
        self.assertEqual(self.refused('[]'), protocol.R_SHAPE)


class PageScriptTests(unittest.TestCase):
    """The page's own hold, read from its script (the real engine plays it in test_board_lockdown's expiry case)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (ASSETS / "board.js").read_text(encoding="utf-8")

    def body(self, name: str) -> str:
        found = re.search(r"\n  function %s\(.*?\n  }\n" % name, self.js, re.S)
        self.assertIsNotNone(found, name)
        return found.group(0)

    def test_the_row_s_pointer_and_focus_hold_it_and_its_time_starts_again(self) -> None:
        self.assertIn("CLOSED_MS = 8000", self.js)
        for event in ("pointerenter", "pointerleave", "focusin", "focusout"):
            self.assertIn(f"closedRow.addEventListener('{event}'", self.js)
        check = self.body("checkHold")
        self.assertIn("post('hold', {id: rec.id, on: held});", check)
        self.assertIn("clearTimeout(rec.closedTimer);", check)                  # held: its expiry waits
        self.assertIn("armClosed(rec);", check)                                  # let go: CLOSED_MS again
        self.assertIn("document.hasFocus()", self.body("onClosedRow"))           # a window without the focus holds not
        self.assertIn("watchHolds();", self.js)
        close = self.body("close")
        self.assertLess(close.index("post('close'"), close.index("setClosed(rec, true, true)"))


# --------------------------------------------------------------------------
# The desk: pins saved and put back; the hold
# --------------------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class DeskCase(unittest.TestCase):
    def make(self, prepare: Any = None) -> AppHarness:
        self.boards: list = []
        harness = AppHarness(self, board_factory=board_fakes.stand_in_factory(self.boards), prepare=prepare)
        harness.reading(autoplay=False)
        self.c = harness.c
        self.reading = self.c.window.reading
        self.reading.set_tab(ui.SCREEN_HOME)
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        self.desk = self.c.look.desk
        self.clock = Clock()
        self.desk._clock = self.clock                                     # noqa: SLF001 - the test's own clock
        self.path = Path(self.c.config.data_dir) / pins.PINS_FILE
        settle(4)
        return harness

    def answer(self, key: int, entries: list[tuple[Any, ...]]) -> list[str]:
        desk = self.desk
        self.c._you(f"Question {key}?")
        desk.on_answer_started(key, f"Question {key}?")
        self.c._jarvis(f"Answer {key}, sir.")
        answer = desk._answers[key]
        answer.done = True
        answer.say = f"Answer {key}, sir."
        desk._put(answer, entries)
        desk._sync_counts()
        settle(3)
        return [desk.feed.frame_id(key, entry[0]) for entry in entries]

    def board(self) -> board_fakes.StandInBoard:
        return self.boards[0]

    def kept(self) -> list[dict[str, Any]]:
        return json.loads(self.path.read_text(encoding="utf-8"))["pins"] if self.path.exists() else []


class DeskPinsTests(DeskCase):
    def first_run(self) -> tuple[list[str], str]:
        """Pins of every sort: a research answer, a table, a mail Jarvis read (never kept), a second research answer."""
        self.make()
        build, key = table()
        ids = self.answer(1, [("research", research("Flights to Denver"), "", ""), ("ask:0", build, "", "", key),
                              ("step:3", mail, "mail:work:t1", "", "mail:work:t1")])
        ids += self.answer(2, [("research", research("Hotels"), "", "")])
        return ids, self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    def test_a_pin_unpin_close_and_bring_back_are_saved_and_mail_is_never_kept(self) -> None:
        with self.assertLogs("briefing_reader", logging.INFO) as logs:
            ids, before = self.first_run()
            self.assertEqual(before, "")                                         # nothing pinned: no file
            board = self.board()
            for frame_id in (ids[3], ids[0], ids[1], ids[2]):
                self.assertTrue(board.request("pin", id=frame_id, on=True))
            self.assertEqual(self.desk.feed.pinned(), 4)
            kept = self.kept()
            self.assertEqual([item["frame"]["title"] for item in kept], ["Hotels", "Flights to Denver",
                                                                        "Budget lines"])     # board order, no mail
            self.assertEqual(kept[2]["key"], "table:budget lines")
            text = self.path.read_text(encoding="utf-8")
            self.assertNotIn(SECRET, text)
            self.assertNotIn("Dana", text)
            board.request("pin", id=ids[3], on=False)
            self.assertEqual([item["frame"]["title"] for item in self.kept()], ["Flights to Denver", "Budget lines"])
            board.request("close", id=ids[0])
            self.assertEqual([item["frame"]["title"] for item in self.kept()], ["Budget lines"])
            board.request("bring_back", id=ids[0])
            self.assertEqual([item["frame"]["title"] for item in self.kept()], ["Flights to Denver", "Budget lines"])
            board.request("pin", id=ids[0], on=False)
            board.request("pin", id=ids[1], on=False)
            self.assertFalse(self.path.exists())                                 # nothing kept: the file goes
        words = "\n".join(logs.output)
        for word in ("Flights", "Denver", "Hotels", "Budget", "Dana", SECRET, "table:", str(self.path)):
            self.assertNotIn(word, words)
        self.assertIn("Board: 3 pin(s) kept", words)

    def restart(self, text: str) -> None:
        """The next start: board_pins.json as the last run left it, then the board made (the first Ask)."""
        def prepare(config: Any) -> None:
            Path(config.data_dir).mkdir(parents=True, exist_ok=True)
            (Path(config.data_dir) / pins.PINS_FILE).write_text(text, encoding="utf-8")

        self.make(prepare=prepare)
        self.assertIsNone(self.desk.board)                                       # never at the app's start
        self.desk.on_ask_started()
        settle(3)

    def test_the_kept_pins_come_back_pinned_in_their_order_after_a_restart(self) -> None:
        ids, _before = self.first_run()
        board = self.board()
        for frame_id in (ids[1], ids[3], ids[2], ids[0]):
            board.request("pin", id=frame_id, on=True)
        text = self.path.read_text(encoding="utf-8")
        self.restart(text)
        feed = self.desk.feed
        group = feed.groups()[0]
        when, topic, older = feed.group_header(group)
        self.assertEqual((topic, older), ("Pinned", False))
        self.assertRegex(when, r"^\d{1,2}:\d{2} [AP]M$")                     # when it went up, as any group
        order = feed.group_order(group)
        self.assertEqual([feed.frame_message(frame_id)["title"] for frame_id in order],
                         ["Hotels", "Flights to Denver", "Budget lines"])
        self.assertTrue(all(feed.flags(frame_id)["pinned"] for frame_id in order))
        self.assertEqual(feed.pinned(), 3)
        sent = self.boards[0].frames_sent()
        self.assertEqual([sent[frame_id]["pinned"] for frame_id in order], [True] * 3)
        self.assertEqual(self.boards[0].of_type("bar")[-1]["pinned"], 3)
        self.assertEqual(feed.key_of(order[2]), "table:budget lines")            # a later answer builds it in place
        self.assertTrue(self.reading.desk_shown())
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)            # the same pins: not written again
        # They are pins like any other: a later Clear keeps them, an unpin takes one out of the file.
        self.boards[0].request("clear")
        self.assertEqual(feed.pinned(), 3)
        self.boards[0].request("pin", id=order[0], on=False)
        self.assertEqual([item["frame"]["title"] for item in self.kept()], ["Flights to Denver", "Budget lines"])

    def test_a_later_answer_with_the_same_table_builds_the_kept_pin_again_in_its_place(self) -> None:
        ids, _before = self.first_run()
        self.board().request("pin", id=ids[1], on=True)
        self.restart(self.path.read_text(encoding="utf-8"))
        kept_id = self.desk.feed.frame_ids()[0]
        build, key = table()
        self.answer(5, [("ask:0", build, "", "", key)])
        self.assertEqual(self.desk.feed.frame_ids().count(kept_id), 1)
        self.assertEqual(self.desk.feed.pinned(), 1)

    def test_no_landing_sound_for_the_pins_that_came_back(self) -> None:
        ids, _before = self.first_run()
        self.board().request("pin", id=ids[0], on=True)
        self.restart(self.path.read_text(encoding="utf-8"))
        sounds: list[str] = []
        self.desk.sound = lambda name, *args: sounds.append(name)               # (a stand-in for the look's sounds)
        frame_id = self.desk.feed.frame_ids()[0]
        self.boards[0].request("landed", id=frame_id, step="outline")
        self.boards[0].request("landed", id=frame_id, step="done")
        self.assertEqual(sounds, [])

    def test_a_damaged_file_brings_no_pins_and_the_next_pin_replaces_it(self) -> None:
        with self.assertLogs("briefing_reader.board.pins", logging.WARNING) as logs:
            self.restart("{damaged " + SECRET)
        self.assertEqual(self.desk.feed.frame_ids(), [])
        self.assertEqual(logs.output, ["WARNING:briefing_reader.board.pins:Board: board_pins.json is damaged; no pins "
                                       "came back"])
        ids = self.answer(1, [("research", research("Flights"), "", "")])
        self.boards[0].request("pin", id=ids[0], on=True)
        self.assertEqual([item["frame"]["title"] for item in self.kept()], ["Flights"])

    def test_more_than_six_in_the_file_bring_back_six(self) -> None:
        records = [{"source": "research", "key": "", "approvals": "",
                    "frame": {"kind": "text", "title": f"Answer {index}", "meta": "", "body": {"text": "Words."}}}
                   for index in range(9)]
        with self.assertLogs("briefing_reader.board.pins", logging.WARNING):
            self.restart(json.dumps({"version": 1, "pins": records}))
        self.assertEqual(self.desk.feed.pinned(), protocol.MAX_PINS)
        self.assertEqual([self.desk.feed.frame_message(frame_id)["title"] for frame_id in self.desk.feed.frame_ids()],
                         [f"Answer {index}" for index in range(6)])


class DeskHoldTests(DeskCase):
    def test_a_held_row_stays_past_8_s_and_leaves_8_s_after_it_is_let_go(self) -> None:
        self.make()
        ids = self.answer(1, [("research", research("Flights"), "", ""), ("research2", research("Hotels"), "", "")])
        board = self.board()
        board.request("close", id=ids[0])
        self.assertTrue(self.desk._due_timer.isActive())                         # noqa: SLF001
        self.clock.now += 2
        self.assertTrue(board.request("hold", id=ids[0], on=True))              # the pointer on its row
        self.assertFalse(self.desk._due_timer.isActive())                        # noqa: SLF001 - nothing to wait for
        self.clock.now += 60
        self.desk._on_due()                                                      # noqa: SLF001 - as a late timer would
        self.assertIn(ids[0], self.desk.feed.frame_ids())
        board.request("hold", id=ids[0], on=False)                               # it left the row
        self.assertTrue(self.desk._due_timer.isActive())                         # noqa: SLF001
        self.assertGreater(self.desk._due_timer.remainingTime(), 7000)           # noqa: SLF001 - its 8 s again
        self.clock.now += CLOSE_S
        self.desk._on_due()                                                      # noqa: SLF001
        settle()
        self.assertNotIn(ids[0], self.desk.feed.frame_ids())
        self.assertEqual(self.desk.counts["request:holdRequested"], 2)

    def test_a_page_that_came_up_again_or_a_desk_that_folded_lets_the_hold_go(self) -> None:
        self.make()
        ids = self.answer(1, [("research", research("Flights"), "", ""), ("research2", research("Hotels"), "", "")])
        board = self.board()
        board.request("close", id=ids[0])
        board.request("hold", id=ids[0], on=True)
        self.desk._on_ready()                                                    # noqa: SLF001 - the first ready
        self.desk._on_ready()                                                    # noqa: SLF001 - the page came back
        self.assertTrue(self.desk._due_timer.isActive())                         # noqa: SLF001
        self.assertEqual(self.desk.feed.next_due(), self.clock.now + CLOSE_S)
        board.request("hold", id=ids[0], on=True)
        self.desk._on_desk_shown(False)                                          # noqa: SLF001 - out of sight
        self.assertEqual(self.desk.feed.next_due(), self.clock.now + CLOSE_S)


if __name__ == "__main__":
    unittest.main()
