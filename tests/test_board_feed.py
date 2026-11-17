"""The new look, part 5a: what the board holds (board.feed.BoardFeed) and the messages that put it on the page
(board.protocol's group, remove_group, bar, desk and reveal, and a frame's group). Qt-free: no window, no engine.

Covered: each new message type and field with its checker (typed, capped, the ids the app sent, hostile shapes); one
answer = one group with its time and topic (else the first frame's title, at most 40 characters), the newest group on
top and the one before it older; in a group what waits first, then the order Jarvis speaks of them; a mail thread and the
reply that waits on it are ONE frame (either order), whose go_approvals opens the reply's card; the limits (24 frames,
8 groups: the oldest group with no pinned frame leaves whole; refused when none can); the bar's counts; every message
the feed makes passes the app's check in the order it is sent; resend; spoken_rank.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from briefing_reader.board import feed as feed_mod
from briefing_reader.board import protocol
from briefing_reader.board.feed import BoardFeed, spoken_rank


def frame(frame_id: str, title: str = "Budget lines", *, kind: str = "text", waits: str = "", **body: Any) -> dict:
    return {"type": "frame", "id": frame_id, "kind": kind, "title": title, "meta": "",
            "body": body or {"text": "Words."}, "waits": waits}


class Page:
    """What BoardView keeps to check the next message (the frames and groups the app sent), and what it sent."""

    def __init__(self) -> None:
        self.known: set[str] = set()
        self.groups: list[str] = []
        self.group_of: dict[str, str] = {}
        self.sent: list[dict] = []

    def apply(self, messages: list[dict]) -> None:
        for message in messages:
            checked = protocol.check_message(message, self.known, self.groups)
            if checked.type == "frame":
                self.known.add(checked.frame_id)
                if checked.group_id:
                    self.group_of[checked.frame_id] = checked.group_id
            elif checked.type == "group" and checked.group_id not in self.groups:
                self.groups.append(checked.group_id)
            elif checked.type == "remove_group":
                self.groups.remove(checked.group_id)
                for member in [key for key, owner in self.group_of.items() if owner == checked.group_id]:
                    self.known.discard(member)
                    del self.group_of[member]
            self.sent.append(json.loads(checked.text))

    def order(self) -> dict[str, list[str]]:
        """Each group's frames as the page would place them (a new frame at its group's end, then each order)."""
        placed: dict[str, list[str]] = {}
        for message in self.sent:
            if message["type"] == "group":
                existing = placed.setdefault(message["id"], [])
                ordered = [item for item in message["frames"] if item in existing or item in self.known]
                placed[message["id"]] = ordered + [item for item in existing if item not in ordered]
            elif message["type"] == "frame" and message.get("group"):
                members = placed.setdefault(message["group"], [])
                if message["id"] not in members:
                    members.append(message["id"])
            elif message["type"] == "remove_group":
                placed.pop(message["id"], None)
        return placed


def put(feed: BoardFeed, page: Page, answer: Any, source: str, title: str = "", *, merge: str = "", waits: str = "",
        approvals: str = "", rank: float = 0.0, when: str = "9:41 AM", topic: str = "", kind: str = "text",
        **body: Any) -> list[dict]:
    frame_id = feed.frame_id(answer, source, merge)
    messages = feed.put(answer, source, [frame(frame_id, title or source, kind=kind, waits=waits, **body)], when=when,
                        topic=topic, merge=merge, approvals=approvals, rank=rank)
    page.apply(messages)
    return messages


# --------------------------------------------------------------------------
# The new messages and fields
# --------------------------------------------------------------------------

class MessageTests(unittest.TestCase):
    def refused(self, message: Any, known: set[str] | None = None, groups: set[str] | None = None) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_message(message, known if known is not None else {"f1"},
                                   groups if groups is not None else {"g1"})
        return caught.exception.reason

    def test_a_group_is_rebuilt_whole(self) -> None:
        checked = protocol.check_message({"type": "group", "id": "g2", "time": "9:41 AM", "topic": "Your day",
                                          "frames": ["f1"]}, {"f1"}, {"g1"})
        self.assertEqual((checked.type, checked.group_id), ("group", "g2"))
        self.assertEqual(json.loads(checked.text), {"type": "group", "id": "g2", "time": "9:41 AM", "topic": "Your day",
                                                    "frames": ["f1"], "older": False})
        self.assertNotIn("Your day", repr(checked))

    def test_a_group_s_hostile_shapes_are_refused(self) -> None:
        good = {"type": "group", "id": "g1", "time": "9:41 AM", "topic": "Your day", "frames": ["f1"]}
        self.assertEqual(self.refused({**good, "frames": ["f9"]}), protocol.R_ID)          # a frame not sent
        self.assertEqual(self.refused({**good, "frames": ["f1", "f1"]}), protocol.R_ID)    # twice
        self.assertEqual(self.refused({**good, "frames": "f1"}), protocol.R_SHAPE)
        self.assertEqual(self.refused({**good, "topic": "x" * 41}), protocol.R_TOO_LONG)   # 40 characters at most
        self.assertEqual(self.refused({**good, "time": "9" * 17}), protocol.R_TOO_LONG)
        self.assertEqual(self.refused({**good, "older": 1}), protocol.R_SHAPE)
        self.assertEqual(self.refused({**good, "id": "<g>"}), protocol.R_ID)
        self.assertEqual(self.refused({**good, "html": "<b>"}), protocol.R_FIELD)
        self.assertEqual(self.refused({key: value for key, value in good.items() if key != "topic"}), protocol.R_FIELD)
        many = {f"g{index}" for index in range(protocol.MAX_GROUPS)}
        self.assertEqual(self.refused({**good, "id": "g99"}, groups=many), protocol.R_FULL)   # 8 groups at most
        protocol.check_message({**good, "id": "g0"}, {"f1"}, many)                          # a known one updates
        hostile = "<img src=x onerror=1>‮\x00"
        checked = protocol.check_message({**good, "topic": hostile}, {"f1"}, {"g1"})
        self.assertEqual(json.loads(checked.text)["topic"], hostile)                       # kept as text, exactly

    def test_a_frame_s_group_must_be_one_the_app_sent(self) -> None:
        checked = protocol.check_message({**frame("f2"), "group": "g1"}, {"f1"}, {"g1"})
        self.assertEqual((checked.frame_id, checked.group_id), ("f2", "g1"))
        self.assertEqual(json.loads(checked.text)["group"], "g1")
        self.assertEqual(self.refused({**frame("f2"), "group": "g9"}), protocol.R_ID)
        self.assertEqual(self.refused({**frame("f2"), "group": ""}), protocol.R_ID)
        self.assertEqual(self.refused({**frame("f2"), "group": 1}), protocol.R_ID)
        self.assertNotIn("group", json.loads(protocol.check_message(frame("f2"), {"f1"}).text))   # none: as before

    def test_remove_group_reveal_bar_and_desk(self) -> None:
        self.assertEqual(protocol.check_message({"type": "remove_group", "id": "g1"}, (), {"g1"}).group_id, "g1")
        self.assertEqual(self.refused({"type": "remove_group", "id": "g2"}), protocol.R_ID)
        self.assertEqual(protocol.check_message({"type": "reveal", "group": "g1"}, (), {"g1"}).group_id, "g1")
        self.assertEqual(self.refused({"type": "reveal", "group": "g2"}), protocol.R_ID)
        self.assertEqual(self.refused({"type": "reveal", "id": "g1"}), protocol.R_FIELD)
        bar = protocol.check_message({"type": "bar", "frames": 9, "waits": 1, "pinned": 1})
        self.assertEqual(json.loads(bar.text), {"type": "bar", "frames": 9, "waits": 1, "pinned": 1, "compact": False})
        self.assertEqual(self.refused({"type": "bar", "frames": 25}), protocol.R_SHAPE)       # 24 at most
        self.assertEqual(self.refused({"type": "bar", "frames": 2, "waits": 3}), protocol.R_SHAPE)
        self.assertEqual(self.refused({"type": "bar", "frames": True}), protocol.R_SHAPE)     # a bool is no count
        self.assertEqual(self.refused({"type": "bar", "frames": 1.0}), protocol.R_SHAPE)
        self.assertEqual(self.refused({"type": "bar", "frames": 1, "compact": "yes"}), protocol.R_SHAPE)
        for state in protocol.DESK_STATES:
            self.assertEqual(json.loads(protocol.check_message({"type": "desk", "state": state}).text)["state"], state)
        self.assertEqual(self.refused({"type": "desk", "state": "open"}), protocol.R_FIELD)
        self.assertEqual(self.refused({"type": "desk"}), protocol.R_FIELD)


# --------------------------------------------------------------------------
# The groups
# --------------------------------------------------------------------------

class FeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.feed = BoardFeed()
        self.page = Page()

    def test_one_answer_is_one_group_with_its_time_and_topic(self) -> None:
        sent = put(self.feed, self.page, 1, "day", "Today")
        self.assertEqual([message["type"] for message in sent], ["group", "frame", "bar"])   # the page appends it
        self.assertEqual(sent[0], {"type": "group", "id": "g1", "time": "9:41 AM", "topic": "Today", "frames": [],
                                   "older": False})
        self.assertEqual(sent[1]["group"], "g1")
        put(self.feed, self.page, 1, "mail", "Re: Budget numbers")
        self.assertEqual(self.feed.groups(), ["g1"])                     # the same answer: the same group
        self.assertEqual(self.feed.frames(), 2)
        put(self.feed, self.page, 2, "research", "A topic that is far longer than forty characters in all", when="10:02 AM")
        self.assertEqual(self.feed.groups(), ["g2", "g1"])               # the newest on top
        self.assertEqual(self.feed.group_header("g2"), ("10:02 AM", "A topic that is far longer than forty…", False))
        self.assertTrue(self.feed.group_header("g1")[2])                 # the one before it is older (INK_4)
        put(self.feed, self.page, 3, "x", "Frame title", topic="Your day and Dana's numbers")
        self.assertEqual(self.feed.group_header("g3")[1], "Your day and Dana's numbers")   # Ask's topic wins

    def test_what_waits_comes_first_then_the_order_jarvis_speaks(self) -> None:
        put(self.feed, self.page, 1, "research", rank=0.9)
        put(self.feed, self.page, 1, "day", rank=0.1)
        put(self.feed, self.page, 1, "todos", rank=1.0)
        put(self.feed, self.page, 1, "mail", waits="Reply waits on Approvals", rank=0.5)
        order = [self.feed.frame_message(frame_id)["title"] for frame_id in self.feed.group_order("g1")]
        self.assertEqual(order, ["mail", "day", "research", "todos"])
        self.assertEqual(self.page.order()["g1"], self.feed.group_order("g1"))     # the page has that order too

    def test_a_mail_and_its_waiting_reply_are_one_frame_in_either_order(self) -> None:
        for first, second in (("read", "reply"), ("reply", "read")):
            with self.subTest(first=first):
                feed, page = BoardFeed(), Page()
                parts = {"read": dict(title="Re: Budget numbers", kind="mail_thread",
                                      sender="Dana Whitfield", time="8:12 AM", text="Here are the Q4 numbers."),
                         "reply": dict(title="Re: Budget numbers", kind="mail_thread", sender="To Dana", time="",
                                       text="Thanks Dana.", waits="Reply waits on Approvals", approvals="card-7")}
                for name in (first, second):
                    options = dict(parts[name])
                    put(feed, page, 1, name, merge="mail:work:t1", **options)
                self.assertEqual(feed.frames(), 1)
                (frame_id,) = feed.frame_ids()
                shown = feed.frame_message(frame_id)
                self.assertEqual(shown["body"]["sender"], "Dana Whitfield")         # the mail as read ...
                self.assertEqual(shown["waits"], "Reply waits on Approvals")      # ... with the reply's chip
                self.assertEqual(feed.approvals_for(frame_id), "card-7")           # whose go_approvals opens its card
                self.assertEqual(feed.waits(), 1)
                self.assertEqual(feed.counts()["merged"], 1)

    def test_the_limits_make_room_by_whole_groups_the_oldest_first(self) -> None:
        for answer in range(8):                                         # 8 groups of 3: 24 frames
            for index in range(3):
                put(self.feed, self.page, answer, f"s{index}")
        self.assertEqual((self.feed.frames(), len(self.feed.groups())), (24, 8))
        sent = put(self.feed, self.page, 8, "new")
        self.assertEqual(sent[0], {"type": "remove_group", "id": "g1"})  # the oldest leaves whole, first
        self.assertEqual((self.feed.frames(), len(self.feed.groups())), (22, 8))
        self.assertNotIn("g1", self.feed.groups())
        self.assertEqual(self.feed.counts()["left"], 1)
        # A group with a pinned frame never leaves for room: the next oldest does.
        self.feed.set_pinned(self.feed.group_order("g2")[0], True)
        sent = put(self.feed, self.page, 9, "newer")
        self.assertEqual(sent[0], {"type": "remove_group", "id": "g3"})
        self.assertIn("g2", self.feed.groups())

    def test_a_ninth_group_takes_the_oldest_ones_place(self) -> None:
        for answer in range(8):
            put(self.feed, self.page, answer, "one")
        sent = put(self.feed, self.page, 8, "one")
        self.assertEqual(sent[0], {"type": "remove_group", "id": "g1"})
        self.assertEqual(len(self.feed.groups()), protocol.MAX_GROUPS)

    def test_when_nothing_can_leave_the_new_frame_is_refused(self) -> None:
        feed, page = BoardFeed(max_frames=2), Page()
        put(feed, page, 1, "a")
        feed.set_pinned(feed.frame_ids()[0], True)
        put(feed, page, 2, "b")
        feed.set_pinned(feed.group_order(feed.group_for(2))[0], True)
        self.assertEqual(put(feed, page, 3, "c"), [])
        self.assertEqual((feed.frames(), feed.counts()["refused"]), (2, 1))

    def test_the_bar_counts_and_the_compact_column(self) -> None:
        put(self.feed, self.page, 1, "a", waits="Reply waits on Approvals")
        put(self.feed, self.page, 1, "b")
        self.assertEqual(self.feed.bar(), {"type": "bar", "frames": 2, "waits": 1, "pinned": 0, "compact": False})
        self.assertEqual(self.feed.set_compact(True)[0]["compact"], True)
        self.assertEqual(self.feed.set_compact(True), [])               # unchanged: not sent again
        self.assertEqual(self.feed.bar_again()[0]["frames"], 2)

    def test_an_update_in_place_keeps_its_id_and_sends_nothing_unchanged(self) -> None:
        put(self.feed, self.page, 1, "step:5", "Page", text="Opening it...")
        before = self.feed.frame_ids()
        self.assertEqual(put(self.feed, self.page, 1, "step:5", "Page", text="Opening it..."), [])
        sent = put(self.feed, self.page, 1, "step:5", "Page", text="Opened.")
        self.assertEqual(self.feed.frame_ids(), before)
        self.assertEqual([message["type"] for message in sent], ["frame"])

    def test_turns_and_their_frames(self) -> None:
        put(self.feed, self.page, 1, "a")
        put(self.feed, self.page, 1, "b")
        self.feed.set_turn(1, 42)
        self.assertEqual(self.feed.turn_frames(42), ("g1", 2))
        self.assertEqual(self.feed.turn_frames(41), ("", 0))

    def test_every_message_passes_the_app_s_check_and_resend_rebuilds_the_page(self) -> None:
        for answer in range(10):
            for index in range(4):
                put(self.feed, self.page, answer, f"s{index}", waits="W" if index == 3 else "")
        page = Page()
        page.apply(self.feed.resend())                                  # a page with nothing: the board again
        self.assertEqual(set(page.known), set(self.feed.frame_ids()))
        self.assertEqual(page.order(), {group: self.feed.group_order(group) for group in self.feed.groups()})

    def test_its_mark_changes_exactly_when_what_the_board_shows_does(self) -> None:
        # Part 7 fixes (review 7, window 3): the second screen notes a board change, and rebuilds its wall, only then.
        feed = self.feed
        marks = [feed.mark()]

        def changed(why: str) -> None:
            marks.append(feed.mark())
            self.assertNotEqual(marks[-1], marks[-2], why)

        def same(why: str) -> None:
            self.assertEqual(feed.mark(), marks[-1], why)

        put(feed, self.page, 1, "a", "A", text="One.")
        changed("a frame put up")
        same("asked again")
        put(feed, self.page, 1, "a", "A", text="One.")
        same("the same words again")
        feed.set_compact(True)
        feed.set_compact(False)
        same("the bar alone (the desk's resize sends)")
        put(feed, self.page, 1, "a", "A", text="Two.")
        changed("its words changed")
        frame_id = feed.frame_ids()[0]
        feed.pin(frame_id, True)
        changed("pinned")
        feed.close(frame_id, 0.0)
        changed("closed")
        feed.bring_back(frame_id)
        changed("brought back")
        put(feed, self.page, 2, "b", "B")
        changed("another answer's group, on top")
        feed.clear_unpinned(0.0)
        changed("a Clear")
        feed.restore()
        changed("its Bring back")
        frame_id = feed.frame_id(3, "c")
        feed.put(3, "c", [frame(frame_id, "C")], when="9:41 AM", park=True)
        changed("a parked answer")
        feed.land()
        changed("it landed")
        feed.clear()
        changed("the board cleared")

    def test_spoken_rank(self) -> None:
        say = "Three meetings today, sir - the first at ten. Dana sent the budget numbers at 8:12."
        self.assertLess(spoken_rank(say, ["meeting"]), spoken_rank(say, ["Dana"]))
        self.assertEqual(spoken_rank(say, ["weather"]), 1.0)
        self.assertEqual(spoken_rank("", ["meeting"]), 1.0)
        self.assertEqual(spoken_rank(say, ["at"]), 1.0)                 # under 3 letters: not a word to go by

    def test_nothing_here_is_qt(self) -> None:
        import sys

        source = feed_mod.__file__
        with open(source, encoding="utf-8") as handle:
            self.assertNotIn("PySide6", handle.read())
        self.assertIn("briefing_reader.board.feed", sys.modules)


if __name__ == "__main__":
    unittest.main()
