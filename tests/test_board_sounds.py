"""The new look, part 5c: the board's sounds (DESIGN-SPEC 7) and the changed frame (3.4 "Changed figure").

The sounds never reach an audio device: the look controller's board gets a recording player (and in the app the test
guard keeps it silent; the desk's calls to the look controller are recorded instead).

Covered: each hook of look_ui.LookController (a frame lands - FRAME by its place, the first four; a group settles -
SETTLED; a changed light - CHANGED; open big and back - OPEN_BIG / CLOSE_BIG) and every silence rule for each (while
Jarvis speaks: dropped, never queued; muted, quiet hours, focus mode, a meeting recording, locked, Windows busy or full
screen, sounds off in config.toml or on this PC, the test guard, the window hidden, motion off for FRAME and CHANGED, the
gate and the repeat); the desk calling them from the page's landed requests (by the frame's place in its group, SETTLED
once after a group's last frame, never again for a page that restarted, nothing off screen), the changed light and open
big; the feed's source keys (a later answer's frame built from the same thread, day, page or list is the same frame,
sent again with "changed" when what it says differs - never for the clock's own words - and nothing at all when it says
the same; quiet within one answer; never through a closed or cleared frame; a waiting reply's newer card). Part 5c
fixes: a page read again keeps its picture until the new one is ready (never "failed" over it; a newer snapshot shown
quietly), a frame the owner has not seen is brought up to date and never "changed", counts are a frame's own data, and a
mail read again the next day says the same.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtWidgets import QApplication

from briefing_reader import config as config_mod, holo, hud, look_ui, prefs, sounds, ui
from briefing_reader.board import protocol
from briefing_reader.board.feed import BoardFeed, substance
from briefing_reader.board.frames import Built
from tests import board_fakes
from tests.test_look_ui import FakeHost, RecordingPlayer
from tests.ui_fakes import AppHarness, fonts, settle

_app: QApplication | None = None
BOARD_HOOKS = (("on_frame_landed", (0,), "frame0"), ("on_group_settled", (), sounds.SETTLED),
               ("on_frame_changed", (), sounds.CHANGED), ("on_big", (True,), sounds.OPEN_BIG),
               ("on_big", (False,), sounds.CLOSE_BIG))


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_board_sounds", "-platform", "offscreen"])
    hud.load_fonts(config_mod.PROJECT_ROOT / "fonts")
    fonts()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --------------------------------------------------------------------------
# The look controller's hooks and every silence rule
# --------------------------------------------------------------------------

class HookTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def make(self, *, test_guard: bool = False, **kw) -> tuple[look_ui.LookController, FakeHost, RecordingPlayer, Clock]:
        host = FakeHost(self.root, **kw)
        player = RecordingPlayer()
        controller = look_ui.LookController(host, player_factory=lambda _paths, _owner: player, test_guard=test_guard,
                                            start_sounds=False)
        clock = Clock()
        controller.sounds._clock = clock                       # noqa: SLF001 - the gate's own time, stepped by hand
        controller.sounds.use_files({})
        controller.sounds._visible = lambda: True              # noqa: SLF001 - no window in these tests
        controller.sounds._motion_off = lambda: False          # noqa: SLF001 - motion as it is set
        return controller, host, player, clock

    def test_each_board_hook_plays_its_sound(self) -> None:
        controller, _host, player, clock = self.make()
        for name, args, expected in BOARD_HOOKS:
            with self.subTest(hook=name, args=args):
                clock.now += 3.0                               # past the gate and the repeat
                self.assertEqual(getattr(controller, name)(*args), "")
                self.assertEqual(player.played[-1], expected)

    def test_a_frame_lands_by_its_place_and_only_the_first_four_sound(self) -> None:
        controller, _host, player, clock = self.make()
        for place in range(4):
            clock.now += 3.0
            controller.on_frame_landed(place)
        self.assertEqual(player.played, ["frame0", "frame1", "frame2", "frame3"])
        clock.now += 3.0
        self.assertEqual(controller.on_frame_landed(4), sounds.R_UNKNOWN)
        self.assertEqual(controller.on_frame_landed(-1), sounds.R_UNKNOWN)
        self.assertEqual(len(player.played), 4)

    def test_never_while_jarvis_speaks_dropped_never_queued(self) -> None:
        controller, host, player, clock = self.make()
        host._utterance = "Three meetings today, sir."
        controller.on_speaking()
        for name, args, _expected in BOARD_HOOKS:
            clock.now += 3.0
            self.assertEqual(getattr(controller, name)(*args), sounds.R_SPEAKING, name)
        host._utterance = ""
        controller.on_speaking()
        clock.now += 0.1
        self.assertEqual(controller.on_group_settled(), sounds.R_SPEAKING)   # nor within 250 ms after
        clock.now += 0.3
        self.assertEqual(player.played, [])                                 # nothing kept for later
        self.assertEqual(controller.on_group_settled(), "")

    def test_every_quiet_rule_holds_every_board_sound(self) -> None:
        cases = [("voice", "muted"), ("quiet", "quiet hours"), ("focus", "focus mode"), ("meeting", "a meeting records"),
                 ("locked", "locked"), ("busy", "Windows says busy")]
        for attribute, reason in cases:
            with self.subTest(reason=reason):
                controller, host, player, clock = self.make()
                patch = None
                if attribute == "voice":
                    host.voice.muted = True
                elif attribute == "quiet":
                    host.productivity = object()
                    host.quiet_until = "8:00 AM"
                elif attribute == "busy":                     # busy, presentation mode or a full-screen program
                    patch = mock.patch("briefing_reader.standing.schedule.windows_busy", return_value=True)
                    patch.start()
                    self.addCleanup(patch.stop)
                else:
                    setattr(host, attribute, True)
                for name, args, _expected in BOARD_HOOKS:
                    clock.now += 3.0
                    self.assertEqual(getattr(controller, name)(*args), reason, name)
                self.assertEqual(player.played, [])
                if patch is not None:
                    patch.stop()

    def test_sounds_off_the_test_guard_hidden_and_motion_off(self) -> None:
        controller, _host, player, clock = self.make(sounds_value="off")       # config.toml's [look] sounds = "off"
        for name, args, _expected in BOARD_HOOKS:
            clock.now += 3.0
            self.assertEqual(getattr(controller, name)(*args), sounds.R_OFF)
        controller, _host, player, clock = self.make()
        controller.set_sounds_on(False)                                         # Settings > Look's Off on this PC
        clock.now += 3.0
        self.assertEqual(controller.on_big(True), sounds.R_OFF)
        controller.set_sounds_on(True)                                          # (on again for the cases below)
        controller, _host, player, clock = self.make(test_guard=True)          # the tests: never a sound
        self.assertEqual(controller.on_frame_landed(0), sounds.R_TESTS)
        controller, _host, player, clock = self.make()
        controller.sounds._visible = lambda: False                              # noqa: SLF001 - the window hidden
        for name, args, _expected in BOARD_HOOKS:
            clock.now += 3.0
            self.assertEqual(getattr(controller, name)(*args), sounds.R_HIDDEN, name)
        controller, _host, player, clock = self.make()
        controller.sounds._motion_off = lambda: True                            # noqa: SLF001 - motion off
        clock.now += 3.0
        self.assertEqual(controller.on_frame_landed(0), sounds.R_MOTION)
        clock.now += 3.0
        self.assertEqual(controller.on_frame_changed(), sounds.R_MOTION)
        clock.now += 3.0
        self.assertEqual(controller.on_group_settled(), "")                     # settled and big still sound
        clock.now += 3.0
        self.assertEqual(controller.on_big(False), "")
        self.assertEqual(player.played, [sounds.SETTLED, sounds.CLOSE_BIG])

    def test_the_gate_and_the_repeat_hold(self) -> None:
        controller, _host, player, clock = self.make()
        self.assertEqual(controller.on_frame_landed(0), "")
        clock.now += 0.08
        self.assertEqual(controller.on_frame_landed(1), sounds.R_GATE)        # 80 ms: under the 120 ms gate
        clock.now += 0.1
        self.assertEqual(controller.on_frame_landed(2), sounds.R_REPEAT)      # one frame sound every 2 s
        self.assertEqual(controller.on_group_settled(), "")                   # another event passes
        self.assertEqual(player.played, ["frame0", sounds.SETTLED])

    def test_the_app_under_the_test_guard_never_plays_them(self) -> None:
        harness = AppHarness(self)
        look = harness.c.look
        for name, args, _expected in BOARD_HOOKS:
            self.assertEqual(getattr(look, name)(*args), sounds.R_TESTS)


# --------------------------------------------------------------------------
# The desk calling them (the stand-in board's requests, as the page makes them)
# --------------------------------------------------------------------------

def frame_data(title: str, text: str = "Words.") -> dict:
    return {"kind": "text", "title": title, "meta": "", "body": {"text": text}}


class DeskSoundTests(unittest.TestCase):
    def make(self) -> AppHarness:
        self.boards: list = []
        harness = AppHarness(self, board_factory=board_fakes.stand_in_factory(self.boards))
        harness.reading(autoplay=False)
        self.c = harness.c
        self.reading = self.c.window.reading
        self.reading.set_tab(ui.SCREEN_HOME)
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        self.desk = self.c.look.desk
        self.calls: list[tuple] = []
        look = self.c.look
        for name in ("on_frame_landed", "on_group_settled", "on_frame_changed", "on_big"):
            setattr(look, name, lambda *args, name=name: self.calls.append((name, *args)))
        settle(6)
        return harness

    def answer(self, key: int, entries: list[tuple[str, dict, str]]) -> list[str]:
        desk = self.desk
        self.c._you(f"Question {key}?")
        desk.on_answer_started(key, f"Question {key}?")
        self.c._jarvis(f"Answer {key}, sir.")
        answer = desk._answers[key]
        answer.done = True
        answer.say = f"Answer {key}, sir."
        desk._put(answer, [(source, lambda fid, data=data: Built(({**data, "type": "frame", "id": fid},), 0), "", "",
                            key_) for source, data, key_ in entries])
        desk.feed.set_turn(key, desk._answer_entry())
        desk._sync_counts()
        settle(3)
        return [desk.feed.frame_id(key, source) for source, _data, _key in entries]

    def board(self) -> board_fakes.StandInBoard:
        return self.boards[0]

    def land(self, frame_ids: list[str], step: str) -> None:
        for frame_id in frame_ids:
            self.board().request("landed", id=frame_id, step=step)
        settle()

    def test_frames_land_by_their_place_and_the_group_settles_once(self) -> None:
        self.make()
        ids = self.answer(1, [(f"s{index}", frame_data(f"Frame {index}"), "") for index in range(5)])
        order = self.desk.feed.group_order(self.desk.feed.group_of(ids[0]))
        self.land(order, "outline")
        self.assertEqual(self.calls, [("on_frame_landed", place) for place in range(4)])   # the first four
        self.calls.clear()
        self.land(order[:-1], "done")
        self.assertEqual(self.calls, [])                                    # not until its last frame
        self.land(order[-1:], "done")
        self.assertEqual(self.calls, [("on_group_settled",)])
        self.calls.clear()
        self.land(order, "outline")                                          # a page that came back: drawn again ...
        self.land(order, "done")
        self.assertEqual(self.calls, [])                                    # ... never heard again
        alone = self.answer(2, [("one", frame_data("Alone"), "")])
        self.land(alone, "outline")
        self.land(alone, "done")
        self.assertEqual(self.calls, [("on_frame_landed", 0)])              # a group of one never settles

    def test_nothing_off_screen(self) -> None:
        self.make()
        ids = self.answer(1, [("a", frame_data("A"), ""), ("b", frame_data("B"), "")])
        self.reading.set_tab(ui.SCREEN_APPROVALS)
        settle()
        self.land(ids, "outline")
        self.land(ids, "done")
        self.assertEqual(self.calls, [])

    def test_the_changed_light_sounds_only_when_it_runs(self) -> None:
        self.make()
        first = self.answer(1, [("budget", frame_data("Budget lines", "Software 2,900."), "sheet:q4")])
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.answer(2, [("budget-again", frame_data("Budget lines", "Software 3,400."), "sheet:q4")])
        self.assertEqual(self.calls, [("on_frame_changed",)])
        resent = [message for message in self.board().of_type("frame") if message["id"] == first[0]][-1]
        self.assertTrue(resent["changed"])
        self.calls.clear()
        holo.set_motion_level(holo.MOTION_CALM)                              # calm: the badge only, no light
        self.answer(3, [("budget-third", frame_data("Budget lines", "Software 3,600."), "sheet:q4")])
        self.assertEqual(self.calls, [])
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.answer(4, [("budget-fourth", frame_data("Budget lines", "Software 3,600."), "sheet:q4")])
        self.assertEqual(self.calls, [])                                    # the same words: nothing at all

    def test_open_big_and_back(self) -> None:
        self.make()
        ids = self.answer(1, [("a", frame_data("A"), "")])
        self.board().request("open", id=ids[0])
        settle()
        self.board().request("big", step="back")
        settle()
        self.assertEqual(self.calls, [("on_big", True), ("on_big", False)])


# --------------------------------------------------------------------------
# The feed's source keys and "changed" (board.feed)
# --------------------------------------------------------------------------

class Page:
    """What BoardView keeps to check the next message, and what it sent."""

    def __init__(self) -> None:
        self.known: set[str] = set()
        self.groups: list[str] = []
        self.sent: list[dict] = []

    def apply(self, messages: list[dict]) -> list[dict]:
        for message in messages:
            checked = protocol.check_message(message, self.known, self.groups)
            if checked.type == "frame":
                self.known.add(checked.frame_id)
            elif checked.type == "group" and checked.group_id not in self.groups:
                self.groups.append(checked.group_id)
            self.sent.append(json.loads(checked.text))
        return messages


def put(feed: BoardFeed, page: Page, answer: int, source: str, data: dict, *, key: str = "", waits: str = "",
        approvals: str = "", merge: str = "") -> list[dict]:
    frame_id = feed.frame_id(answer, source, merge, key)
    message = {"type": "frame", "id": frame_id, **data}
    if waits:
        message["waits"] = waits
    return page.apply(feed.put(answer, source, [message], when="9:41 AM", key=key, approvals=approvals, merge=merge))


DAY_920 = {"kind": "calendar", "title": "Today", "meta": "Tue 17 Nov · 3 meetings", "body": {
    "first": 480, "last": 1080, "now_min": 560, "ticks": [], "spans": [], "now": "9:20", "rows": [
        {"time": "10:00-10:30", "title": "AI-Trader check-in", "meta": "in 40 min", "state": "next"},
        {"time": "10:30-1:00", "title": "Free", "meta": "2 h 30", "state": "free"},
        {"time": "1:00-2:00", "title": "Study group", "meta": "", "state": ""}]}}
DAY_941 = json.loads(json.dumps(DAY_920))
DAY_941["body"].update(now_min=581, now="9:41")
DAY_941["body"]["rows"][0]["meta"] = "in 19 min"
DAY_MOVED = json.loads(json.dumps(DAY_941))
DAY_MOVED["body"]["rows"][2]["time"] = "2:00-3:00"


class ChangedFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.feed, self.page = BoardFeed(), Page()

    def test_a_later_answer_built_from_the_same_source_changes_it_in_place(self) -> None:
        put(self.feed, self.page, 1, "calendar:today", DAY_941, key="day:2026-11-17")
        frame_id = self.feed.frame_ids()[0]
        sent = put(self.feed, self.page, 2, "calendar:today", DAY_MOVED, key="day:2026-11-17")
        self.assertEqual([message["type"] for message in sent], ["frame"])     # the same frame, sent again ...
        self.assertEqual((sent[0]["id"], sent[0]["changed"]), (frame_id, True))
        self.assertTrue(sent[0]["fact"].startswith("Changed"))
        self.assertEqual(self.feed.groups(), ["g1"])                          # ... in its own place, no new group
        self.assertEqual(self.feed.take_changed(), [frame_id])
        self.assertEqual(self.feed.take_changed(), [])                         # listed once
        self.assertEqual(self.feed.frames(), 1)

    def test_the_same_words_send_nothing_and_the_clock_s_own_words_are_the_same(self) -> None:
        put(self.feed, self.page, 1, "calendar:today", DAY_920, key="day:2026-11-17")
        self.assertEqual(put(self.feed, self.page, 2, "calendar:today", DAY_920, key="day:2026-11-17"), [])
        self.assertEqual(put(self.feed, self.page, 3, "calendar:today", DAY_941, key="day:2026-11-17"), [])
        self.assertEqual(substance(DAY_920), substance(DAY_941))              # the now line, "in 19 min": the clock's
        self.assertNotEqual(substance(DAY_941), substance(DAY_MOVED))
        self.assertEqual(self.feed.take_changed(), [])
        self.assertEqual(self.feed.counts()["changed"], 0)

    def test_within_one_answer_an_update_is_quiet(self) -> None:
        put(self.feed, self.page, 1, "step:5", {"kind": "text", "title": "Page", "body": {"text": "Opening it..."}},
            key="page:https://example.org")
        sent = put(self.feed, self.page, 1, "step:5", {"kind": "text", "title": "Page", "body": {"text": "Opened."}},
                   key="page:https://example.org")
        self.assertEqual(len(sent), 1)
        self.assertFalse(sent[0]["changed"])
        self.assertEqual(self.feed.take_changed(), [])

    def test_a_frame_not_ready_before_is_filled_not_changed(self) -> None:
        put(self.feed, self.page, 1, "step:1", {"kind": "picture", "title": "Page", "status": "empty",
                                                "status_text": "On its way.", "body": {"caption": "example.org"}},
            key="page:https://example.org")
        sent = put(self.feed, self.page, 2, "step:9", {"kind": "picture", "title": "Page",
                                                       "body": {"caption": "example.org"}}, key="page:https://example.org")
        self.assertEqual(len(sent), 1)
        self.assertFalse(sent[0]["changed"])

    def test_a_closed_or_cleared_frame_never_lends_its_id(self) -> None:
        put(self.feed, self.page, 1, "todos", {"kind": "todos", "title": "To-dos", "body": {"rows": []}}, key="todos")
        first = self.feed.frame_ids()[0]
        self.feed.close(first, 0.0)
        put(self.feed, self.page, 2, "todos", {"kind": "todos", "title": "To-dos", "body": {"rows": [
            {"text": "Book the dentist", "meta": "", "state": "open"}]}}, key="todos")
        self.assertEqual(len(self.feed.frame_ids()), 2)                        # a new frame: the closed one leaves
        second = [frame_id for frame_id in self.feed.frame_ids() if frame_id != first][0]
        self.feed.clear_unpinned(0.0)
        put(self.feed, self.page, 3, "todos", {"kind": "todos", "title": "To-dos", "body": {"rows": []}}, key="todos")
        self.assertNotIn(self.feed.frame_ids()[0], (first, second))

    def test_a_waiting_reply_s_newer_card_is_what_its_chip_opens(self) -> None:
        mail = {"kind": "mail_thread", "title": "Re: Budget numbers", "body": {"sender": "Dana Whitfield",
                                                                              "time": "8:12 AM", "text": "Numbers."}}
        reply = {"kind": "mail_thread", "title": "Re: Budget numbers", "body": {"sender": "To dana@example.edu",
                                                                               "time": "", "text": "Thanks."}}
        put(self.feed, self.page, 1, "step:mail", mail, key="mail:work:t1", merge="mail:work:t1")
        put(self.feed, self.page, 1, "card:c1", reply, key="mail:work:t1", merge="mail:work:t1",
            waits="Reply waits on Approvals", approvals="c1")
        frame_id = self.feed.frame_ids()[0]
        self.assertEqual(self.feed.approvals_for(frame_id), "c1")
        sent = put(self.feed, self.page, 2, "card:c2", reply, key="mail:work:t1", merge="mail:work:t1",
                   waits="Reply waits on Approvals", approvals="c2")
        self.assertEqual(self.feed.approvals_for(frame_id), "c2")               # the newer card
        self.assertEqual(sent, [])                                             # the page shows the same: nothing
        self.assertEqual(self.feed.take_changed(), [])

    # ---- part 5c fixes ------------------------------------------------------------------------------------------------

    def put_page(self, answer: int, source: str, status: str = "ok", src: str = "") -> list[dict]:
        """A page picture's frame of ``answer`` (its key: the page's address), with its picture when ``src``."""
        frame_id = self.feed.frame_id(answer, source, "", PAGE_KEY)
        data = {"kind": "picture", "title": "City Hack schedule", "meta": f"9:0{answer} AM",
                "body": {"caption": "city-hack.example.org"}, "status": status,
                "status_text": "" if status == "ok" else "Opening the page..."}
        messages = [{"type": "frame", "id": frame_id, **data}]
        if src:
            messages.append({"type": "picture", "id": frame_id, "src": src})
        return self.page.apply(self.feed.put(answer, source, messages, when="9:41 AM", key=PAGE_KEY))

    def test_a_page_read_again_keeps_its_picture_until_the_new_one_is_ready(self) -> None:
        # Part 5c fixes: a later answer's re-read of the page hid the good picture behind "Opening the page..." (or
        # "failed"), and a new snapshot ready at once was dropped.
        first, second = jpeg_src(40), jpeg_src(56)
        self.put_page(1, "step:1", src=first)
        frame_id = self.feed.frame_ids()[0]
        self.assertEqual(self.put_page(2, "step:9", status="empty"), [])      # on its way: the good one stays
        self.assertEqual(self.put_page(2, "step:9", status="failed"), [])     # failed: it stays (never "failed")
        self.assertEqual(self.feed.frame_message(frame_id)["status"], "ok")
        sent = self.put_page(2, "step:9", src=second)                          # ready: shown, quietly
        self.assertEqual([message["type"] for message in sent], ["frame", "picture"])
        self.assertEqual((sent[0]["id"], sent[0]["status"], sent[0]["changed"]), (frame_id, "ok", False))
        self.assertEqual(sent[1]["src"], second)
        self.assertEqual(self.feed.take_changed(), [])
        self.assertEqual(self.put_page(3, "step:12", src=second), [])          # the same picture again: nothing

    def test_a_frame_the_owner_has_not_seen_is_brought_up_to_date_never_changed(self) -> None:
        # Part 5c fixes: a parked frame, never shown yet, came up CHANGED (the light and the sound on a hidden frame).
        todos = {"kind": "todos", "title": "To-dos", "body": {"rows": [{"text": "Book the dentist", "meta": "",
                                                                        "state": "open"}]}}
        more = json.loads(json.dumps(todos))
        more["body"]["rows"].append({"text": "Buy milk", "meta": "", "state": "waits"})
        for answer, data in ((1, todos), (2, more)):
            frame_id = self.feed.frame_id(answer, "todos", "", "todos")
            sent = self.page.apply(self.feed.put(answer, "todos", [{"type": "frame", "id": frame_id, **data}],
                                                 when="9:41 AM", key="todos", park=True))
        frame_id = self.feed.frame_ids()[0]
        self.assertTrue(self.feed.is_parked(self.feed.group_of(frame_id)))
        resent = [message for message in sent if message["type"] == "frame"]
        self.assertEqual([(message["id"], message["changed"]) for message in resent], [(frame_id, False)])
        self.assertEqual(len(resent[0]["body"]["rows"]), 2)                   # brought up to date ...
        self.assertEqual(self.feed.take_changed(), [])                        # ... never "changed"
        # A frame the owner closed: a later answer's late update through its own id changes nothing either.
        self.feed.land()
        put(self.feed, self.page, 3, "todos", todos, key="todos")              # seen, built again: changed
        self.assertEqual(self.feed.take_changed(), [frame_id])
        self.feed.close(frame_id, 0.0)
        put(self.feed, self.page, 3, "todos", more, key="todos")
        self.assertEqual(self.feed.take_changed(), [])

    def test_a_count_is_the_frame_s_own_data(self) -> None:
        # Part 5c fixes: a week day's "3 events" or a "+2 MORE" row that changed was never sent.
        week = {"kind": "calendar", "title": "This week", "meta": "7 events", "body": {
            "first": 0, "last": 0, "now_min": -1, "ticks": [], "spans": [], "now": "", "rows": [
                {"time": "Mon 16", "title": "Standup · Design review", "meta": "3 events", "state": "today"}]}}
        busier = json.loads(json.dumps(week))
        busier["body"]["rows"][0]["meta"] = "4 events"
        self.assertNotEqual(substance(week), substance(busier))
        put(self.feed, self.page, 1, "calendar:week", week, key="week:2026-11-16")
        sent = put(self.feed, self.page, 2, "calendar:week", busier, key="week:2026-11-16")
        self.assertEqual([(message["type"], message["changed"]) for message in sent], [("frame", True)])
        for kind, more_row in (("calendar", {"time": "", "title": "", "meta": "+2 MORE", "state": ""}),
                               ("todos", {"text": "", "meta": "+2 MORE", "state": "open"})):
            with self.subTest(kind=kind):
                frame = {"kind": kind, "title": "T", "body": {"rows": [more_row]}}
                other = json.loads(json.dumps(frame))
                other["body"]["rows"][0]["meta"] = "+3 MORE"
                self.assertNotEqual(substance(frame), substance(other))
        due = {"kind": "todos", "title": "To-dos", "body": {"rows": [{"text": "Essay", "meta": "DUE THU",
                                                                      "state": "open"}]}}
        later = json.loads(json.dumps(due))
        later["body"]["rows"][0]["meta"] = "DUE TOMORROW"
        self.assertEqual(substance(due), substance(later))                     # due words are the clock's
        self.assertEqual(substance(DAY_920), substance(DAY_941))               # so are "now" and "in 19 min"

    def test_a_mail_read_again_the_next_day_says_the_same(self) -> None:
        # Part 5c fixes: the day shown before a mail's time once it is not today's made an unchanged thread CHANGED.
        today = {"kind": "mail_thread", "title": "Re: Budget numbers", "meta": "Work · 2:41 PM",
                 "body": {"sender": "Dana Whitfield", "time": "2:41 PM", "text": "Here are the Q4 numbers."}}
        tomorrow = json.loads(json.dumps(today))
        tomorrow["meta"] = "Work · Fri Nov 13 2026, 2:41 PM"
        tomorrow["body"]["time"] = "Fri Nov 13 2026, 2:41 PM"
        self.assertEqual(substance(today), substance(tomorrow))
        put(self.feed, self.page, 1, "step:mail", today, key="mail:work:t1")
        self.assertEqual(put(self.feed, self.page, 2, "step:mail", tomorrow, key="mail:work:t1"), [])
        replied = json.loads(json.dumps(tomorrow))
        replied["body"].update(time="Sat Nov 14 2026, 9:05 AM", text="Line 14 is now 3,400.")
        sent = put(self.feed, self.page, 3, "step:mail", replied, key="mail:work:t1")
        self.assertEqual([(message["type"], message["changed"]) for message in sent], [("frame", True)])


PAGE_KEY = "page:https://city-hack.example.org/schedule"


def jpeg_src(width: int) -> str:
    import base64

    from tests.ui_fakes import tiny_jpeg

    return protocol.PICTURE_PREFIX + base64.b64encode(tiny_jpeg(width, 30)).decode("ascii")


if __name__ == "__main__":
    unittest.main()
