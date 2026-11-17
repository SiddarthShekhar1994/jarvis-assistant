"""The new look, part 5b: the board's ways - the index strip, "N more below", pairs, parking, satellites, pin / close /
Clear / Bring back and the keyboard (DESIGN-SPEC 3.1, 3.3, 3.5, 3.6). Offscreen; the web engine never starts here: the
board is tests.board_fakes' stand-in (a real BoardView whose page is a plain widget: every message checked and kept) or
the real fallback. tests/test_board_lockdown.py's "ways" cases run the same page ways in the real engine.

Covered, the app's side (the app is the source of truth): the new messages and requests with their hostile shapes
(a frame's fact, a group's parked, cleared, focus_part; clear, restore, show_new, reading, focus_out) and the page's
lists matching Python's; a satellite's facts made from each kind's data; the feed's pins (6 at most: a 7th refused and
the frame's flags sent again), close and Bring back (the frame leaves 8 s later), Clear and Bring back (out of sight,
then gone; groups and tokens with them; no late frame for an answer the owner cleared away), parking and landing (the
others older only then), the reading rule and its 8 s; the controller with the stand-in board (each request does its
work; the desk folds when Clear leaves nothing; a parked answer lands after 8 quiet seconds, on Show, on its token, when
the desk folds; tokens follow); F6's cycle and Esc; the window's key filter lets F6 through from the page; the read-only
tiles leave a parked answer and what Clear took away out; no words in the logs.
"""

from __future__ import annotations

import json
import logging
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import holo, ui
from briefing_reader.board import facts, protocol, view
from briefing_reader.board import frames as board_frames
from briefing_reader.board.feed import CLEAR_S, CLOSE_S, QUIET_S, BoardFeed, ReadingWatch
from tests import board_fakes
from tests.ui_fakes import AppHarness, fonts, settle, wait_for

_app: QApplication | None = None
ASSETS = Path(__file__).resolve().parent.parent / "briefing_reader" / "board" / "assets"


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_board_ways", "-platform", "offscreen"])
    fonts()


def frame(frame_id: str, title: str = "Budget lines", *, kind: str = "text", waits: str = "", **body: Any) -> dict:
    return {"type": "frame", "id": frame_id, "kind": kind, "title": title, "meta": "",
            "body": body or {"text": "Words."}, "waits": waits}


class Page:
    """What BoardView keeps to check the next message (the frames and groups the app sent), and what it sent: every
    message the feed makes must pass, in the order it is sent."""

    def __init__(self) -> None:
        self.known: set[str] = set()
        self.groups: list[str] = []
        self.group_of: dict[str, str] = {}
        self.sent: list[dict] = []

    def apply(self, messages: list[dict]) -> list[dict]:
        for message in messages:
            checked = protocol.check_message(message, self.known, self.groups)
            if checked.type == "frame":
                self.known.add(checked.frame_id)
                if checked.group_id:
                    self.group_of[checked.frame_id] = checked.group_id
            elif checked.type == "group" and checked.group_id not in self.groups:
                self.groups.append(checked.group_id)
            elif checked.type == "remove":
                self.known.discard(checked.frame_id)
                self.group_of.pop(checked.frame_id, None)
            elif checked.type == "remove_group":
                self.groups.remove(checked.group_id)
                for member in [key for key, owner in self.group_of.items() if owner == checked.group_id]:
                    self.known.discard(member)
                    del self.group_of[member]
            self.sent.append(json.loads(checked.text))
        return messages


def put(feed: BoardFeed, page: Page, answer: Any, source: str, title: str = "", *, park: bool = False,
        waits: str = "", kind: str = "text", **body: Any) -> list[dict]:
    frame_id = feed.frame_id(answer, source)
    return page.apply(feed.put(answer, source, [frame(frame_id, title or source, kind=kind, waits=waits, **body)],
                               when="9:41 AM", park=park))


# --------------------------------------------------------------------------
# The new messages and requests (board.protocol) and the page's own lists
# --------------------------------------------------------------------------

class ProtocolTests(unittest.TestCase):
    def refused(self, message: Any, known: set[str] | None = None, groups: set[str] | None = None) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_message(message, known if known is not None else {"f1", "f2"},
                                   groups if groups is not None else {"g1"})
        return caught.exception.reason

    def refused_request(self, kind: Any, payload: Any) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_request(kind, payload, {"f1"})
        return caught.exception.reason

    def test_a_frame_s_fact_only_when_given_and_capped(self) -> None:
        checked = json.loads(protocol.check_message({**frame("f1"), "fact": "3 events · next 10:00 AM"}).text)
        self.assertEqual(checked["fact"], "3 events · next 10:00 AM")
        self.assertNotIn("fact", json.loads(protocol.check_message(frame("f1")).text))     # as before when not given
        self.assertEqual(protocol.FACT_CAP, 40)
        protocol.check_message({**frame("f1"), "fact": "x" * 40})
        self.assertEqual(self.refused({**frame("f1"), "fact": "x" * 41}), protocol.R_TOO_LONG)
        self.assertEqual(self.refused({**frame("f1"), "fact": "\U0001F600" * 21}), protocol.R_TOO_LONG)   # 42 units
        self.assertEqual(self.refused({**frame("f1"), "fact": 3}), protocol.R_SHAPE)
        self.assertEqual(self.refused({**frame("f1"), "fact": ["x"]}), protocol.R_SHAPE)
        hostile = "<img src=x onerror=1>‮\x00"
        self.assertEqual(json.loads(protocol.check_message({**frame("f1"), "fact": hostile}).text)["fact"], hostile)

    def test_a_group_s_parked_only_when_given(self) -> None:
        good = {"type": "group", "id": "g1", "time": "9:41 AM", "topic": "Your day", "frames": ["f1"]}
        self.assertTrue(json.loads(protocol.check_message({**good, "parked": True}, {"f1"}, {"g1"}).text)["parked"])
        self.assertNotIn("parked", json.loads(protocol.check_message(good, {"f1"}, {"g1"}).text))
        self.assertEqual(self.refused({**good, "parked": "yes"}), protocol.R_SHAPE)
        self.assertEqual(self.refused({**good, "parked": 1}), protocol.R_SHAPE)

    def test_cleared_names_frames_the_app_sent_each_once(self) -> None:
        checked = protocol.check_message({"type": "cleared", "frames": ["f1", "f2"]}, {"f1", "f2"})
        self.assertEqual(json.loads(checked.text), {"type": "cleared", "frames": ["f1", "f2"]})
        self.assertEqual(json.loads(protocol.check_message({"type": "cleared", "frames": []}).text)["frames"], [])
        self.assertEqual(self.refused({"type": "cleared", "frames": ["f9"]}), protocol.R_ID)
        self.assertEqual(self.refused({"type": "cleared", "frames": ["f1", "f1"]}), protocol.R_ID)
        self.assertEqual(self.refused({"type": "cleared", "frames": ["<f1>"]}), protocol.R_ID)
        self.assertEqual(self.refused({"type": "cleared", "frames": "f1"}), protocol.R_SHAPE)
        self.assertEqual(self.refused({"type": "cleared", "frames": [f"f{i}" for i in range(25)]},
                                      {f"f{i}" for i in range(25)}), protocol.R_TOO_LONG)
        self.assertEqual(self.refused({"type": "cleared"}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "cleared", "frames": [], "count": 8}), protocol.R_FIELD)

    def test_focus_part_takes_the_four_parts_only(self) -> None:
        for part in ("board", "strip", "next", "prev"):
            self.assertEqual(json.loads(protocol.check_message({"type": "focus_part", "part": part}).text)["part"], part)
        # The canvas (part 1) added "controls" on purpose: the page's corner buttons are a part F6 reaches
        # (tests/test_canvas.py; tests/board_probe.py canvas).
        self.assertEqual(protocol.FOCUS_PARTS, ("board", "strip", "next", "prev", "controls"))
        self.assertEqual(self.refused({"type": "focus_part", "part": "address bar"}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "focus_part", "part": 1}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "focus_part"}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "focus_part", "part": "board", "id": "f1"}), protocol.R_FIELD)

    def test_the_new_requests_and_their_hostile_shapes(self) -> None:
        for kind in ("clear", "restore", "show_new"):
            self.assertEqual(protocol.check_request(kind, "{}", {"f1"}), protocol.Request(kind))
            self.assertEqual(self.refused_request(kind, '{"id":"f1"}'), protocol.R_FIELD)     # no field at all
            self.assertEqual(self.refused_request(kind, "[]"), protocol.R_SHAPE)
            self.assertEqual(self.refused_request(kind, "x"), protocol.R_JSON)
        self.assertEqual(protocol.check_request("reading", '{"pointer":true,"focus":false}', {"f1"}),
                         protocol.Request("reading", pointer=True, focused=False))
        self.assertEqual(self.refused_request("reading", '{"pointer":true}'), protocol.R_FIELD)
        self.assertEqual(self.refused_request("reading", '{"pointer":1,"focus":false}'), protocol.R_SHAPE)
        self.assertEqual(self.refused_request("reading", '{"pointer":true,"focus":false,"x":1}'), protocol.R_FIELD)
        self.assertEqual(self.refused_request("reading", '{"pointer":NaN,"focus":false}'), protocol.R_JSON)
        for to in ("ask", "conversation"):
            self.assertEqual(protocol.check_request("focus_out", json.dumps({"to": to}), {"f1"}),
                             protocol.Request("focus_out", to=to))
        self.assertEqual(self.refused_request("focus_out", '{"to":"https://example.com"}'), protocol.R_FIELD)
        self.assertEqual(self.refused_request("focus_out", "{}"), protocol.R_FIELD)
        self.assertEqual(self.refused_request("focus_out", '{"to":"ask","then":"x"}'), protocol.R_FIELD)
        self.assertEqual(self.refused_request("clear", "{}" + " " * protocol.PAYLOAD_CAP), protocol.R_SIZE)

    def test_the_page_checks_with_the_same_lists(self) -> None:
        js = (ASSETS / "board.js").read_text(encoding="utf-8")
        parts = re.search(r"var FOCUS_PARTS = \[([^\]]*)\]", js).group(1)
        self.assertEqual(tuple(re.findall(r"'(\w+)'", parts)), protocol.FOCUS_PARTS)
        self.assertEqual(int(re.search(r"\bMAX_PINS = (\d+)", js).group(1)), protocol.MAX_PINS)
        self.assertEqual(protocol.MAX_PINS, 6)
        self.assertIn("words(message.fact, CAPS.meta)", js)              # the page checks the fact as a meta
        self.assertEqual(protocol.FACT_CAP, protocol.META_CAP)
        self.assertIn("HANDLERS.cleared = function", js)
        self.assertIn("HANDLERS.focus_part = function", js)
        self.assertIn("'parked'", re.search(r"HANDLERS\.group = function[^\n]*\n[^\n]*", js).group(0))
        for name in ("CLOSED_MS = 8000", "NOTE_MS = 4000", "ROOM_MS = 220", "PAIRS_FROM = 500", "IN_VIEW_TOP = 6",
                     "IN_VIEW_END = 40", "EDGE = 16"):
            self.assertIn(name, js)
        css = (ASSETS / "board.css").read_text(encoding="utf-8")
        self.assertIn("transparent 0, #000 28px, #000 calc(100% - 44px)", css)     # the top fade once scrolled
        self.assertIn("#000 calc(100% - 44px), transparent 100%", css)            # the end fade
        self.assertIn("padding: 6px 12px 40px var(--pad)", css)                   # the 40 px end pad


# --------------------------------------------------------------------------
# A satellite's fact (board.facts)
# --------------------------------------------------------------------------

class FactTests(unittest.TestCase):
    def test_each_kind_says_its_own_fact(self) -> None:
        day = {"first": 480, "last": 1080, "now_min": 581, "ticks": [], "spans": [], "now": "9:41", "rows": [
            {"time": "8:00-8:30 AM", "title": "Breakfast", "meta": "", "state": "past"},
            {"time": "10:00-10:30 AM", "title": "AI-Trader check-in", "meta": "in 19 min", "state": "next"},
            {"time": "10:30 AM-1:00 PM", "title": "Free", "meta": "2 h 30", "state": "free"},
            {"time": "1:00-2:00 PM", "title": "Study group", "meta": "", "state": ""}]}
        cases = [
            ({"kind": "calendar", "meta": "Tue Nov 17 · 3 events", "body": day}, "3 events · next 10:00 AM"),
            ({"kind": "calendar", "meta": "12 events", "body": {"first": 0, "last": 0, "now": "", "rows": []}},
             "12 events"),
            ({"kind": "mail_thread", "waits": "Reply waits on Approvals", "body": {}}, "Reply waits"),
            ({"kind": "mail_thread", "body": {"sender": "Dana Whitfield", "time": "8:12 AM", "text": "x"}},
             "Dana Whitfield · 8:12 AM"),
            ({"kind": "mail_list", "body": {"rows": [
                {"sender": "Northwind", "time": "7:02 AM", "subject": "x", "meta": "", "unread": True},
                {"sender": "City Hack", "time": "Yesterday", "subject": "y", "meta": "", "unread": True},
                {"sender": "Ana", "time": "Yesterday", "subject": "z", "meta": "", "unread": False}]}},
             "2 unread · newest 7:02 AM"),
            ({"kind": "mail_list", "meta": "1 waiting · 1 owed", "body": {"rows": [
                {"sender": "Ana", "time": "1d", "subject": "x", "meta": "WAITING", "unread": False}]}},
             "1 waiting · 1 owed"),
            ({"kind": "todos", "body": {"rows": [{"text": "a", "meta": "", "state": "open"},
                                                 {"text": "b", "meta": "", "state": "waits"},
                                                 {"text": "c", "meta": "", "state": "done"},
                                                 {"text": "", "meta": "+3 MORE", "state": "open"}]}},
             "6 items · 1 waits"),
            ({"kind": "picture", "meta": "9:05 AM", "body": {"caption": "city-hack.example.org"}},
             "Page picture · 9:05 AM"),
            ({"kind": "text", "meta": "2 sources", "body": {"text": "x"}}, "2 sources"),
            ({"kind": "calendar", "status": "failed", "status_text": "Google Calendar didn't answer.", "body": {}},
             "Couldn't load"),
            ({"kind": "mail_list", "status": "empty", "status_text": "Nothing unread, sir.", "body": {}},
             "Nothing unread, sir."),
            ({"kind": "text", "meta": "2 sources", "changed": True, "body": {"text": "x"}}, "Changed · 2 sources"),
        ]
        for message, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(facts.fact_of(message), expected)

    def test_within_its_cap_and_never_raising(self) -> None:
        long = facts.fact_of({"kind": "mail_thread", "body": {"sender": "A very long sender name of a mailing list",
                                                               "time": "Yesterday at 11:59 PM"}})
        self.assertLessEqual(protocol.units(long), protocol.FACT_CAP)
        self.assertTrue(long.endswith("…"))
        for odd in (None, 3, "x", {"kind": "todos", "body": "rows"}, {"kind": "calendar", "body": {"rows": "x"}},
                    {"kind": "mail_list", "body": {"rows": [None, 3]}}, {"kind": "nope"}, {}):
            self.assertIsInstance(facts.fact_of(odd), str)
        self.assertEqual(facts.start_of("11:30 AM-1:00 PM"), "11:30 AM")
        self.assertEqual(facts.start_of("14:00-15:00"), "14:00")
        self.assertEqual(facts.start_of("ALL DAY"), "ALL DAY")

    def test_every_frame_the_builders_make_gets_a_fact_that_passes(self) -> None:
        feed, page = BoardFeed(), Page()
        built = board_frames.from_research(SimpleNamespace(answer="The final round moved to Friday.",
                                                           sources=(("City Hack schedule", "https://city.example.org/s"),)),
                                           frame_id=feed.frame_id(1, "research"))
        page.apply(feed.put(1, "research", built.messages, when="9:41 AM"))
        sent = [message for message in page.sent if message["type"] == "frame"]
        self.assertEqual(sent[0]["fact"], "1 source")


# --------------------------------------------------------------------------
# The feed: the app is the source of truth
# --------------------------------------------------------------------------

class PinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.feed, self.page = BoardFeed(), Page()
        for index in range(8):
            put(self.feed, self.page, 1, f"s{index}")
        self.ids = self.feed.frame_ids()

    def test_six_pins_and_a_seventh_is_refused_with_its_flags_sent_again(self) -> None:
        for frame_id in self.ids[:6]:
            sent = self.page.apply(self.feed.pin(frame_id, True))
            self.assertEqual([message["type"] for message in sent], ["bar"])     # the page has it: only the counts
        self.assertEqual(self.feed.pinned(), protocol.MAX_PINS)
        sent = self.page.apply(self.feed.pin(self.ids[6], True))             # the page tried the 7th anyway
        self.assertEqual([message["type"] for message in sent], ["frame"])
        self.assertEqual((sent[0]["id"], sent[0]["pinned"]), (self.ids[6], False))   # the app's own flag again
        self.assertEqual(self.feed.counts()["pins_refused"], 1)
        self.assertFalse(self.feed.set_pinned(self.ids[7], True))
        self.page.apply(self.feed.pin(self.ids[0], False))                   # one off: room for another
        self.assertTrue(self.feed.set_pinned(self.ids[7], True))

    def test_an_accepted_change_is_never_sent_back_and_an_update_keeps_the_pin(self) -> None:
        self.page.apply(self.feed.pin(self.ids[0], True))
        self.assertEqual(put(self.feed, self.page, 1, "s0"), [])            # the same frame again: nothing
        sent = put(self.feed, self.page, 1, "s0", text="New words.")
        self.assertTrue(sent[0]["pinned"])                                  # an update carries the app's flags

    def test_pins_keep_a_group_through_the_room_made_for_others(self) -> None:
        feed, page = BoardFeed(), Page()
        for answer in range(8):
            put(feed, page, answer, "a", f"Answer {answer}")
        page.apply(feed.pin(feed.group_order(feed.group_for(0))[0], True))
        sent = put(feed, page, 8, "a")
        self.assertEqual(sent[0], {"type": "remove_group", "id": feed.group_for(1) or "g2"})
        self.assertTrue(feed.group_for(0))                                   # the pinned answer stays


class CloseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.feed, self.page = BoardFeed(), Page()
        put(self.feed, self.page, 1, "a")
        put(self.feed, self.page, 1, "b")
        put(self.feed, self.page, 2, "c")
        self.a, self.b = self.feed.group_order(self.feed.group_for(1))
        self.c = self.feed.frame_ids()[0]

    def test_a_closed_frame_leaves_8_s_later(self) -> None:
        self.assertEqual(self.page.apply(self.feed.close(self.a, 100.0)), [])   # the page shows the row: nothing
        self.assertEqual(self.feed.flags(self.a), {"pinned": False, "closed": True, "cleared": False})
        self.assertEqual(self.feed.next_due(), 100.0 + CLOSE_S)
        self.assertEqual(self.feed.frames(), 3)                              # still on the board, as its row
        self.assertEqual(self.page.apply(self.feed.due(107.9)), [])
        sent = self.page.apply(self.feed.due(108.0))
        self.assertEqual(sent[0], {"type": "remove", "id": self.a})
        self.assertEqual(sent[-1]["type"], "bar")
        self.assertEqual((self.feed.frames(), self.feed.next_due()), (2, None))
        self.assertEqual(self.feed.flags(self.a), {})

    def test_bring_back_keeps_it(self) -> None:
        self.page.apply(self.feed.close(self.a, 100.0))
        self.assertEqual(self.page.apply(self.feed.bring_back(self.a)), [])
        self.assertIsNone(self.feed.next_due())
        self.assertEqual(self.page.apply(self.feed.due(200.0)), [])
        self.assertIn(self.a, self.feed.frame_ids())

    def test_the_last_frame_of_a_group_takes_its_group_and_no_late_frame_comes_back(self) -> None:
        self.page.apply(self.feed.close(self.c, 0.0))
        sent = self.page.apply(self.feed.due(CLOSE_S))
        self.assertEqual(sent[0], {"type": "remove_group", "id": "g2"})
        self.assertFalse(self.feed.group_for(2))
        self.assertEqual([message for message in sent if message["type"] == "group"][0]["older"], False)   # g1 newest
        self.assertEqual(put(self.feed, self.page, 2, "late"), [])          # its answer takes no late frame
        self.assertEqual(self.feed.counts()["refused"], 1)

    def test_a_closed_frame_s_late_update_stays_closed(self) -> None:
        self.page.apply(self.feed.close(self.a, 0.0))
        sent = put(self.feed, self.page, 1, "a", text="Late words.")
        self.assertTrue(sent[0]["closed"])


class ClearTests(unittest.TestCase):
    def setUp(self) -> None:
        self.feed, self.page = BoardFeed(), Page()
        for answer, sources in ((1, ("a", "b")), (2, ("c",)), (3, ("d", "e"))):
            for source in sources:
                put(self.feed, self.page, answer, source)
        self.feed.set_turn(1, 11)
        self.feed.set_turn(2, 12)
        self.feed.set_turn(3, 13)
        self.pinned = self.feed.group_order(self.feed.group_for(1))[0]
        self.page.apply(self.feed.pin(self.pinned, True))

    def test_clear_takes_every_unpinned_frame_out_of_sight_and_bring_back_restores_it(self) -> None:
        sent = self.page.apply(self.feed.clear_unpinned(50.0))
        cleared = sent[0]
        self.assertEqual(cleared["type"], "cleared")
        self.assertEqual(len(cleared["frames"]), 4)
        self.assertNotIn(self.pinned, cleared["frames"])
        self.assertEqual(sent[-1], {"type": "bar", "frames": 1, "waits": 0, "pinned": 1, "compact": False})
        self.assertEqual(self.feed.turn_frames(11)[1], 1)                    # a token with a pinned frame: the new count
        self.assertEqual(self.feed.turn_frames(13)[1], 0)                    # ... one with none goes
        sent = self.page.apply(self.feed.restore())
        self.assertEqual(sent[0], {"type": "cleared", "frames": []})
        self.assertEqual(self.feed.frames(), 5)
        self.assertEqual(self.feed.turn_frames(13)[1], 2)

    def test_after_8_s_they_leave_with_their_groups(self) -> None:
        self.page.apply(self.feed.clear_unpinned(50.0))
        self.assertEqual(self.feed.next_due(), 50.0 + CLEAR_S)
        sent = self.page.apply(self.feed.due(50.0 + CLEAR_S))
        kinds = [message["type"] for message in sent]
        self.assertEqual(kinds.count("remove_group"), 2)                    # the answers with nothing pinned
        self.assertIn("remove", kinds)                                      # ... the pinned one's other frame
        self.assertLess(kinds.index("remove_group"), kinds.index("cleared"))   # gone before the row goes
        self.assertEqual(sent[kinds.index("cleared")]["frames"], [])
        self.assertEqual(self.feed.frame_ids(), [self.pinned])
        self.assertEqual(self.feed.groups(), [self.feed.group_for(1)])
        self.assertFalse(self.feed.group_header(self.feed.group_for(1))[2])   # the one left is the newest
        self.assertEqual(self.feed.restore(), [])                           # nothing to bring back any more
        self.assertEqual(put(self.feed, self.page, 3, "late"), [])          # no late frame for an answer cleared away

    def test_nothing_pinned_leaves_nothing(self) -> None:
        self.page.apply(self.feed.pin(self.pinned, False))
        self.page.apply(self.feed.clear_unpinned(0.0))
        self.assertEqual(self.feed.frames(), 0)                              # the desk folds (set_board_count 0)
        self.assertEqual(self.feed.bar()["frames"], 0)

    def test_a_second_clear_lets_the_first_one_s_frames_go(self) -> None:
        self.page.apply(self.feed.clear_unpinned(0.0))
        put(self.feed, self.page, 4, "f")
        sent = self.page.apply(self.feed.clear_unpinned(1.0))
        cleared = [message for message in sent if message["type"] == "cleared"]
        self.assertEqual(cleared[0]["frames"], [])                          # the first Clear's row goes ...
        self.assertEqual(cleared[-1]["frames"], [self.feed.frame_id(4, "f")])   # ... the new one's takes its place

    def test_room_for_a_new_answer_takes_the_cleared_frames_first(self) -> None:
        feed, page = BoardFeed(max_frames=3), Page()
        put(feed, page, 1, "a")
        put(feed, page, 2, "b")
        page.apply(feed.pin(feed.frame_ids()[0], True))
        page.apply(feed.clear_unpinned(0.0))
        put(feed, page, 3, "c")
        sent = put(feed, page, 3, "d")
        self.assertIn({"type": "cleared", "frames": []}, sent)
        self.assertEqual(feed.frames(), 3)

    def test_the_parked_answer_stays_parked(self) -> None:
        put(self.feed, self.page, 4, "p", park=True)
        sent = self.page.apply(self.feed.clear_unpinned(0.0))
        self.assertNotIn(self.feed.frame_id(4, "p"), sent[0]["frames"])
        self.assertEqual(self.feed.parked_groups(), [self.feed.group_for(4)])


class ParkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.feed, self.page = BoardFeed(), Page()
        put(self.feed, self.page, 1, "a")
        put(self.feed, self.page, 2, "b")

    def test_a_new_answer_parks_and_the_others_stay_as_they_are_until_it_lands(self) -> None:
        newest = self.feed.group_for(2)
        sent = put(self.feed, self.page, 3, "c", park=True)
        group = next(message for message in sent if message["type"] == "group")
        self.assertTrue(group["parked"])
        self.assertFalse([message for message in sent if message["type"] == "group" and message["id"] == newest])
        self.assertFalse(self.feed.group_header(newest)[2])                   # not older yet: nothing folds
        self.assertEqual(self.feed.frames(), 3)
        put(self.feed, self.page, 3, "d", park=True)                         # the same answer: into the parked group
        self.assertEqual(self.feed.parked_groups(), [self.feed.group_for(3)])
        sent = self.page.apply(self.feed.land())
        landed = [message for message in sent if message["id"] == self.feed.group_for(3)]
        self.assertEqual(len(landed), 1)
        self.assertNotIn("parked", landed[0])                               # landed: no "parked"
        self.assertTrue(self.feed.group_header(newest)[2])                   # only now older: it folds
        self.assertEqual(self.feed.parked_groups(), [])
        self.assertEqual(self.feed.land(), [])

    def test_two_parked_answers_land_together_the_newest_on_top(self) -> None:
        put(self.feed, self.page, 3, "c", park=True)
        put(self.feed, self.page, 4, "d", park=True)
        self.page.apply(self.feed.land())
        self.assertEqual(self.feed.groups()[:2], [self.feed.group_for(4), self.feed.group_for(3)])
        self.assertTrue(self.feed.group_header(self.feed.group_for(3))[2])
        self.assertFalse(self.feed.group_header(self.feed.group_for(4))[2])

    def test_resend_puts_parked_and_cleared_back_as_they_were(self) -> None:
        put(self.feed, self.page, 3, "c", park=True)
        self.page.apply(self.feed.clear_unpinned(0.0))
        page = Page()
        sent = page.apply(self.feed.resend())
        parked = [message for message in sent if message["type"] == "group" and message.get("parked")]
        self.assertEqual({message["id"] for message in parked}, {self.feed.group_for(3)})
        self.assertEqual(next(message for message in sent if message["type"] == "cleared")["frames"],
                         self.feed.cleared())

    def test_a_parked_frame_can_t_be_closed(self) -> None:
        frame_id = self.feed.frame_id(3, "c")
        put(self.feed, self.page, 3, "c", park=True)
        sent = self.page.apply(self.feed.close(frame_id, 0.0))
        self.assertEqual((sent[0]["id"], sent[0]["closed"]), (frame_id, False))   # its flags again: not closed


class ReadingWatchTests(unittest.TestCase):
    def test_the_reading_rule_and_its_8_s(self) -> None:
        watch = ReadingWatch()
        self.assertEqual(watch.quiet_s, QUIET_S)
        self.assertEqual(QUIET_S, 8.0)
        self.assertFalse(watch.reading(0.0))
        watch.note(True, False, 10.0)                                        # the pointer over the board
        self.assertTrue(watch.reading(100.0))
        self.assertIsNone(watch.quiet_at(100.0))
        watch.note(False, False, 100.0)                                      # it left: quiet counts from now
        self.assertTrue(watch.reading(107.9))
        self.assertFalse(watch.reading(108.0))
        self.assertEqual(watch.quiet_at(101.0), 108.0)
        watch.note(False, True, 110.0)                                       # the focus in it
        self.assertTrue(watch.reading(500.0))
        watch.note(False, False, 500.0)
        watch.note(False, False, 503.0)                                      # a wheel or a key: a sign of reading
        self.assertTrue(watch.reading(510.9))
        self.assertFalse(watch.reading(511.0))
        watch.reset()
        self.assertFalse(watch.reading(0.0))


# --------------------------------------------------------------------------
# The view: the new requests, F6 through the key filter, the read-only tiles
# --------------------------------------------------------------------------

class ViewTests(unittest.TestCase):
    def test_each_new_request_is_its_own_typed_signal(self) -> None:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        board = board_fakes.StandInBoard(host)
        self.addCleanup(board.shutdown)
        board.start()
        settle()
        seen: list[tuple] = []
        board.clearRequested.connect(lambda: seen.append(("clear",)))
        board.restoreRequested.connect(lambda: seen.append(("restore",)))
        board.showNewRequested.connect(lambda: seen.append(("show_new",)))
        board.readingChanged.connect(lambda pointer, focus: seen.append(("reading", pointer, focus)))
        board.focusOutRequested.connect(lambda to: seen.append(("focus_out", to)))
        self.assertTrue(board.request("clear"))
        self.assertTrue(board.request("restore"))
        self.assertTrue(board.request("show_new"))
        self.assertTrue(board.request("reading", pointer=True, focus=False))
        self.assertTrue(board.request("focus_out", to="conversation"))
        self.assertFalse(board.request("focus_out", to="settings"))
        self.assertFalse(board.request("clear", everything=True))
        self.assertEqual(seen, [("clear",), ("restore",), ("show_new",), ("reading", True, False),
                                ("focus_out", "conversation")])

    def test_focus_part_needs_a_ready_page_and_takes_the_window_s_focus(self) -> None:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        late = board_fakes.StandInBoard(host, ready=False)
        self.addCleanup(late.shutdown)
        late.start()
        settle()
        self.assertFalse(late.focus_part("board"))                           # not ready: the caller goes on
        board = board_fakes.StandInBoard(host)
        self.addCleanup(board.shutdown)
        host.show()
        host.activateWindow()
        board.start()
        settle()
        self.assertFalse(board.focus_part("address bar"))
        self.assertTrue(board.focus_part("strip"))
        self.assertEqual(board.sent[-1], {"type": "focus_part", "part": "strip"})
        self.assertTrue(wait_for(lambda: QApplication.focusWidget() is board.page_widget, 2))
        fallen = board_fakes.fallback_factory([])(host)
        self.addCleanup(fallen.shutdown)
        fallen.start()
        settle()
        self.assertFalse(fallen.focus_part("board"))                         # read-only tiles take no focus

    def test_f6_goes_on_to_the_window_from_the_page(self) -> None:
        shift = Qt.KeyboardModifier.ShiftModifier
        self.assertTrue(view.is_cycle_key(Qt.Key.Key_F6, Qt.KeyboardModifier.NoModifier))
        self.assertTrue(view.is_cycle_key(Qt.Key.Key_F6, shift))
        self.assertFalse(view.is_cycle_key(Qt.Key.Key_F6, Qt.KeyboardModifier.ControlModifier))
        self.assertFalse(view.is_cycle_key(Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier))
        self.assertFalse(view.is_cycle_key("F6", Qt.KeyboardModifier.NoModifier))
        owner = QObject()
        self.addCleanup(owner.deleteLater)
        keys = view.WindowKeysFilter(owner, lambda: None)
        override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_F6, Qt.KeyboardModifier.NoModifier)
        override.accept()
        self.assertTrue(keys.eventFilter(QObject(owner), override))
        self.assertFalse(override.isAccepted())
        self.assertEqual((keys.cycled, keys.refused), (1, 0))

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
        for name in ("F6", "Shift+F6"):
            shortcut = QShortcut(QKeySequence(name), window)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda name=name: fired.append(name))
        greedy = Greedy(window)
        greedy.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        window.show()
        window.activateWindow()
        self.assertTrue(wait_for(lambda: QApplication.activeWindow() is window, 3))
        greedy.setFocus()
        QTest.keyClick(greedy, Qt.Key.Key_F6)
        self.assertEqual(fired, [])                                          # the control: the page keeps it
        greedy.installEventFilter(view.WindowKeysFilter(window, lambda: None))
        QTest.keyClick(greedy, Qt.Key.Key_F6)
        QTest.keyClick(greedy, Qt.Key.Key_F6, shift)
        self.assertEqual(fired, ["F6", "Shift+F6"])

    def test_read_only_tiles_leave_a_parked_answer_and_what_clear_took_away_out(self) -> None:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        board = board_fakes.fallback_factory([])(host)
        self.addCleanup(board.shutdown)
        board.start()
        settle()
        feed, page = BoardFeed(), Page()
        sent = put(feed, page, 1, "a", "Kept")
        sent += put(feed, page, 1, "b", "Cleared")
        sent += feed.pin(feed.frame_id(1, "a"), True)
        sent += put(feed, page, 2, "c", "Parked", park=True)
        sent += feed.clear_unpinned(0.0)
        for message in sent:
            self.assertTrue(board.apply(message), message["type"])
        self.assertEqual([tile["title"] for tile in board.tiles().frames()], ["Kept"])
        for message in feed.land():
            board.apply(message)
        self.assertEqual([tile["title"] for tile in board.tiles().frames()], ["Parked", "Kept"])


# --------------------------------------------------------------------------
# The controller with the stand-in board
# --------------------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class WaysApp(unittest.TestCase):
    def make(self, *, factory: str = "stand_in") -> AppHarness:
        self.boards: list = []
        board_factory = (board_fakes.stand_in_factory(self.boards) if factory == "stand_in"
                         else board_fakes.fallback_factory(self.boards))
        harness = AppHarness(self, board_factory=board_factory)
        harness.reading(autoplay=False)
        self.c = harness.c
        self.window = self.c.window
        self.reading = self.window.reading
        self.reading.set_tab(ui.SCREEN_HOME)
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        self.desk = self.c.look.desk
        self.clock = Clock()
        self.desk._clock = self.clock                                     # noqa: SLF001 - the test's own clock
        settle(4)
        return harness

    def answer(self, key: int, *titles: str) -> list[str]:
        """An answer shown on Home with one text frame per title; its frame ids in the order given."""
        desk = self.desk
        self.c._you(f"Question {key}?")
        desk.on_answer_started(key, f"Question {key}?")
        self.c._jarvis(f"Answer {key}, sir.")
        answer = desk._answers[key]
        answer.done = True
        answer.say = f"Answer {key}, sir."
        entries = [(f"s{index}", lambda fid, title=title: board_frames.from_research(
            SimpleNamespace(answer=f"Words of {title}.", sources=()), frame_id=fid, title=title), "", "")
            for index, title in enumerate(titles)]
        desk._put(answer, entries)
        desk.feed.set_turn(key, desk._answer_entry())
        desk._sync_counts()
        settle(3)
        return [desk.feed.frame_id(key, f"s{index}") for index in range(len(titles))]

    def board(self) -> board_fakes.StandInBoard:
        return self.boards[0]

    def tokens(self) -> dict[int, int]:
        """Each turn's frames token as the desk shows it (entry id -> its count; 0: no token)."""
        return {view_.turn.entry_id: view_.frames() for view_ in self.reading.desk_page().turns.views()
                if not view_.you()}


class OwnerWaysTests(WaysApp):
    def test_pins_through_the_page_and_a_seventh_refused(self) -> None:
        self.make()
        ids = self.answer(1, *[f"Frame {index}" for index in range(8)])
        board = self.board()
        for frame_id in ids[:6]:
            self.assertTrue(board.request("pin", id=frame_id, on=True))
        self.assertEqual(self.desk.feed.pinned(), 6)
        self.assertEqual(board.of_type("bar")[-1]["pinned"], 6)
        before = len(board.sent)
        board.request("pin", id=ids[6], on=True)
        again = [message for message in board.sent[before:] if message["type"] == "frame"]
        self.assertEqual((again[0]["id"], again[0]["pinned"]), (ids[6], False))
        self.assertEqual(self.desk.counts["request:pinRequested"], 7)

    def test_close_leaves_after_8_s_and_its_token_follows(self) -> None:
        self.make()
        ids = self.answer(1, "Budget lines", "Today")
        board = self.board()
        board.request("close", id=ids[0])
        self.assertTrue(self.desk.feed.flags(ids[0])["closed"])
        self.assertTrue(self.desk._due_timer.isActive())                     # noqa: SLF001 - armed for 8 s
        self.assertGreater(self.desk._due_timer.remainingTime(), 7000)        # noqa: SLF001
        self.assertEqual(max(self.tokens().values()), 2)                      # its row still offers Bring back
        self.clock.now += CLOSE_S
        self.desk._on_due()                                                   # noqa: SLF001 - as its timer does
        settle()
        self.assertEqual(board.sent[-2:][0], {"type": "remove", "id": ids[0]})
        self.assertEqual(self.reading.board_count(), 1)
        self.assertEqual(max(self.tokens().values()), 1)                      # the token's new count
        board.request("close", id=ids[1])
        self.clock.now += CLOSE_S
        self.desk._on_due()                                                   # noqa: SLF001
        settle()
        self.assertFalse(self.reading.desk_shown())                           # nothing left: Home's stage again
        self.assertEqual(self.reading.board_count(), 0)

    def test_bring_back_in_time_keeps_it(self) -> None:
        self.make()
        ids = self.answer(1, "Budget lines")
        self.board().request("close", id=ids[0])
        self.clock.now += 3
        self.board().request("bring_back", id=ids[0])
        self.assertFalse(self.desk._due_timer.isActive())                    # noqa: SLF001
        self.assertFalse(self.desk.feed.flags(ids[0])["closed"])
        self.assertTrue(self.reading.desk_shown())

    def test_clear_with_a_pin_keeps_the_desk_and_bring_back_restores(self) -> None:
        self.make()
        first = self.answer(1, "This week's list", "Follow-ups")
        self.answer(2, "Today", "Budget lines")
        board = self.board()
        board.request("pin", id=first[0], on=True)
        board.request("clear")
        cleared = board.of_type("cleared")[-1]["frames"]
        self.assertEqual(len(cleared), 3)
        self.assertNotIn(first[0], cleared)
        self.assertEqual(self.reading.board_count(), 1)                       # the pinned frame
        self.assertTrue(self.reading.desk_shown())
        self.assertEqual(sorted(self.tokens().values()), [0, 1])              # a token with nothing left goes
        board.request("restore")
        self.assertEqual(board.of_type("cleared")[-1]["frames"], [])
        self.assertEqual(self.reading.board_count(), 4)
        self.assertEqual(sorted(self.tokens().values()), [2, 2])

    def test_clear_with_nothing_pinned_keeps_the_desk_for_its_bring_back_then_folds_after_8_s(self) -> None:
        # Part 5c changed this on purpose: the desk used to leave at once, so "Bring back" could never be clicked.
        self.make()
        self.answer(1, "Today", "Budget lines")
        self.assertTrue(self.reading.desk_shown())
        self.board().request("clear")
        settle()
        self.assertTrue(self.reading.desk_shown())                            # the bar, the row, the ring at the head
        self.assertTrue(self.reading.board_held())
        self.assertFalse(self.reading.stage.isVisible())
        self.assertEqual(self.reading.board_count(), 0)                       # the header's count: what is left
        self.assertEqual(self.reading.header.board_button.count(), 0)
        self.assertEqual(self.tokens(), {view_.turn.entry_id: 0 for view_ in self.reading.desk_page().turns.views()
                                         if not view_.you()})                 # its token went with them
        self.assertEqual(self.board().cleared_ids(), self.desk.feed.cleared())
        self.clock.now += CLEAR_S
        self.desk._on_due()                                                   # noqa: SLF001
        settle()
        self.assertFalse(self.reading.desk_shown())                           # nothing left: Home returns to the stage
        self.assertTrue(self.reading.stage.isVisible())
        self.assertEqual(self.reading.board_chip_words(), "")
        self.assertEqual(self.board().of_type("remove_group")[-1]["id"], "g1")
        self.assertEqual(self.board().cleared_ids(), [])

    def test_clear_with_nothing_pinned_then_bring_back_restores_everything(self) -> None:
        self.make()
        self.answer(1, "Today", "Budget lines")
        self.board().request("clear")
        settle()
        self.board().request("restore")
        settle()
        self.assertTrue(self.reading.desk_shown())
        self.assertFalse(self.reading.board_held())
        self.assertEqual(self.reading.board_count(), 2)
        self.assertEqual(self.board().cleared_ids(), [])
        self.assertEqual(sorted(self.tokens().values()), [2])
        self.clock.now += CLEAR_S
        self.desk._on_due()                                                   # noqa: SLF001 - nothing leaves now
        settle()
        self.assertEqual(self.reading.board_count(), 2)
        self.assertTrue(self.reading.desk_shown())

    def test_bring_back_after_a_clear_never_folds_the_desk_in_between(self) -> None:
        # Part 5c fixes: the hold was let go before the count came back, so the desk folded (desk leave, the ring's
        # flight, the page's fade) and formed again.
        self.make()
        self.answer(1, "Today", "Budget lines")
        shown: list[bool] = []
        self.reading.deskShown.connect(shown.append)
        before = len(self.board().sent)
        self.board().request("clear")
        settle()
        self.board().request("restore")
        settle()
        self.assertEqual(shown, [])                                          # never folded, never formed again
        self.assertNotIn({"type": "desk", "state": "leave"}, self.board().sent[before:])
        self.assertNotIn({"type": "desk", "state": "enter"}, self.board().sent[before:])
        self.assertTrue(self.reading.desk_shown())
        self.assertEqual(self.reading.board_count(), 2)

    def test_a_new_answer_while_the_cleared_row_is_offered_never_folds_the_desk(self) -> None:
        # Part 5c fixes: the same when a new answer made room (8 groups, all cleared): the Clear's frames leave first.
        self.make()
        for key in range(1, 9):
            self.answer(key, f"Frame {key}")
        self.board().request("clear")
        settle()
        self.assertTrue(self.reading.board_held())
        shown: list[bool] = []
        self.reading.deskShown.connect(shown.append)
        before = len(self.board().sent)
        self.answer(9, "The newest")
        self.assertEqual(shown, [])
        self.assertNotIn({"type": "desk", "state": "leave"}, self.board().sent[before:])
        self.assertTrue(self.reading.desk_shown())
        self.assertFalse(self.reading.board_held())
        self.assertEqual(self.reading.board_count(), 1)
        self.assertEqual(self.board().cleared_ids(), [])                     # the Clear's frames left for room

    def test_the_requests_never_reach_further_than_the_board(self) -> None:
        self.make()
        ids = self.answer(1, "Budget lines")
        tab = self.reading.current_tab()
        actions = [(action.id, action.kind) for action in self.c._actions]  # noqa: SLF001
        decided = self.c._store.path.read_bytes() if self.c._store.path.exists() else b""  # noqa: SLF001
        for kind, payload in (("pin", {"id": ids[0], "on": True}), ("close", {"id": ids[0]}),
                              ("bring_back", {"id": ids[0]}), ("clear", {}), ("restore", {}), ("show_new", {}),
                              ("reading", {"pointer": True, "focus": False}), ("focus_out", {"to": "ask"})):
            self.assertTrue(self.board().request(kind, **payload), kind)
        self.assertEqual(self.reading.current_tab(), tab)                     # nothing opened ...
        self.assertEqual([(action.id, action.kind) for action in self.c._actions], actions)   # noqa: SLF001
        self.assertEqual(self.c._store.path.read_bytes() if self.c._store.path.exists() else b"",   # noqa: SLF001
                         decided)                                             # ... nothing approved, sent or undone


class ParkingAppTests(WaysApp):
    def test_an_answer_that_comes_while_the_owner_reads_parks_then_lands_after_8_quiet_seconds(self) -> None:
        self.make()
        self.answer(1, "Today")
        board = self.board()
        board.request("reading", pointer=True, focus=False)                  # the pointer over the board
        self.answer(2, "Study group")
        newest = self.desk.feed.group_for(2)
        self.assertEqual(board.parked_groups(), [newest])
        self.assertFalse(self.desk.feed.group_header(self.desk.feed.group_for(1))[2])   # nothing folded yet
        self.assertFalse(self.desk._land_timer.isActive())                   # noqa: SLF001 - the pointer is there
        self.clock.now += 60
        board.request("reading", pointer=False, focus=False)                 # it leaves: quiet counts from now
        self.assertTrue(self.desk._land_timer.isActive())                    # noqa: SLF001
        self.assertGreater(self.desk._land_timer.remainingTime(), 7000)       # noqa: SLF001
        self.clock.now += QUIET_S - 0.5
        self.desk._schedule_landing()                                         # noqa: SLF001 - as its timer does
        self.assertEqual(board.parked_groups(), [newest])
        self.clock.now += 0.5
        self.desk._schedule_landing()                                         # noqa: SLF001
        self.assertEqual(board.parked_groups(), [])                           # landed
        self.assertTrue(self.desk.feed.group_header(self.desk.feed.group_for(1))[2])   # only now older

    def test_show_lands_it_at_once_and_a_token_too(self) -> None:
        self.make()
        self.answer(1, "Today")
        board = self.board()
        board.request("reading", pointer=False, focus=True)
        self.answer(2, "Study group")
        self.assertTrue(board.parked_groups())
        board.request("show_new")
        self.assertEqual(board.parked_groups(), [])
        self.answer(3, "Room")
        self.assertTrue(board.parked_groups())                                # the focus is still in the board
        token = self.reading.desk_page().turns.views()[-1]
        self.assertEqual(token.frames(), 1)                                   # a parked answer's token counts it
        token.token.click()
        settle()
        self.assertEqual(board.parked_groups(), [])
        self.assertEqual(board.sent[-1], {"type": "reveal", "group": self.desk.feed.group_for(3)})

    def test_not_reading_it_lands_at_once_and_folding_the_desk_lands_a_parked_one(self) -> None:
        self.make()
        self.answer(1, "Today")
        self.answer(2, "Budget lines")
        self.assertEqual(self.board().parked_groups(), [])                    # nobody at the board
        self.board().request("reading", pointer=True, focus=False)
        self.answer(3, "Room")
        self.assertTrue(self.board().parked_groups())
        self.reading.toggle_board()                                           # Ctrl+B: out of sight
        settle()
        self.assertEqual(self.board().parked_groups(), [])
        self.assertFalse(self.desk._reading_now())                           # noqa: SLF001

    def test_a_scroll_or_a_key_counts_8_s(self) -> None:
        self.make()
        self.answer(1, "Today")
        self.board().request("reading", pointer=False, focus=False)          # a wheel in the page, then nothing
        self.clock.now += 7
        self.answer(2, "Room")
        self.assertTrue(self.board().parked_groups())
        self.clock.now += 1
        self.desk._schedule_landing()                                         # noqa: SLF001
        self.assertEqual(self.board().parked_groups(), [])


class KeyboardTests(WaysApp):
    def focus(self) -> QWidget | None:
        settle(2)
        return QApplication.focusWidget()

    def test_f6_cycles_the_ask_bar_the_conversation_the_board_and_the_strip(self) -> None:
        self.make()
        self.answer(1, "Today", "Budget lines")
        reading = self.reading
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        reading.focus_ask_bar()
        self.assertTrue(reading.command_bar.isAncestorOf(self.focus()))
        QTest.keyClick(self.focus(), Qt.Key.Key_F6)                          # the ask bar -> the conversation
        self.assertIs(self.focus(), reading.desk_page().turns)
        QTest.keyClick(self.focus(), Qt.Key.Key_F6)                          # -> the board's frames
        board = self.board()
        self.assertEqual(board.sent[-1], {"type": "focus_part", "part": "board"})
        self.assertIs(self.focus(), board.page_widget)
        QTest.keyClick(self.focus(), Qt.Key.Key_F6)                          # -> on: the page goes to its strip
        self.assertEqual(board.sent[-1], {"type": "focus_part", "part": "next"})
        board.request("focus_out", to="ask")                                 # past the strip the page hands it back
        self.assertTrue(reading.command_bar.isAncestorOf(self.focus()))
        QTest.keyClick(self.focus(), Qt.Key.Key_F6, Qt.KeyboardModifier.ShiftModifier)   # back: the strip
        self.assertEqual(board.sent[-1], {"type": "focus_part", "part": "strip"})
        QTest.keyClick(self.focus(), Qt.Key.Key_F6, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(board.sent[-1], {"type": "focus_part", "part": "prev"})
        board.request("focus_out", to="conversation")
        self.assertIs(self.focus(), reading.desk_page().turns)
        QTest.keyClick(self.focus(), Qt.Key.Key_Escape)                      # Esc in the conversation: the ask bar
        self.assertTrue(reading.command_bar.isAncestorOf(self.focus()))
        self.assertEqual(self.desk.counts["focus:board"], 1)

    def test_esc_in_the_board_returns_to_the_ask_bar(self) -> None:
        self.make()
        self.answer(1, "Today")
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        self.board().page_widget.setFocus()
        self.board().request("focus_out", to="ask")
        self.assertTrue(self.reading.command_bar.isAncestorOf(self.focus()))

    def test_off_the_desk_f6_does_nothing_and_a_board_that_can_t_start_is_skipped(self) -> None:
        self.make(factory="fallback")
        self.answer(1, "Today")
        reading = self.reading
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        reading.focus_conversation()
        self.assertIs(self.focus(), reading.desk_page().turns)
        reading.cycle_focus(True)                                             # no page to take it: the ask bar
        self.assertTrue(reading.command_bar.isAncestorOf(self.focus()))
        reading.set_tab(ui.SCREEN_APPROVALS)
        before = QApplication.focusWidget()
        reading.cycle_focus(True)
        self.assertIs(QApplication.focusWidget(), before)

    def test_the_conversation_shows_its_focus_ring(self) -> None:
        self.make()
        self.answer(1, "Today")
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        desk = self.reading.desk_page()
        self.reading.focus_conversation()
        settle(2)
        image = desk.grab().toImage()
        ring = desk.turns.geometry().adjusted(-3, -3, 3, 3)
        ratio = image.devicePixelRatio()
        x, y = round((ring.left() + 40) * ratio), round(ring.top() * ratio)
        focus = holo.FOCUS if isinstance(holo.FOCUS, str) else holo.FOCUS.name()
        found = image.pixelColor(x, y).name()
        self.assertEqual(found, focus.lower()[:7])


class LogTests(WaysApp):
    def test_the_logs_hold_no_words(self) -> None:
        self.make()
        secret = "Zanzibar-quartz-7781"
        with self.assertLogs("briefing_reader", level=logging.DEBUG) as logs:
            ids = self.answer(1, secret, f"{secret} two")
            board = self.board()
            board.request("pin", id=ids[0], on=True)
            board.request("close", id=ids[1])
            board.request("clear")
            board.request("restore")
            board.request("reading", pointer=True, focus=False)
            self.answer(2, secret)
            board.request("show_new")
            logging.getLogger("briefing_reader").debug("marker")
        self.assertFalse(any(secret in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
