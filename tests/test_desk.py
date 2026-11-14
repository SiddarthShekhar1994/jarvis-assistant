"""The new look, part 5a: Home as a desk (desk_view, desk_ui, the ReadingView's desk, the header's Board button, the HUD's
chip). Offscreen; the web engine never starts here: the board is tests.board_fakes' stand-in (a real BoardView whose page
is a plain widget: every message checked and kept, its frames painted as boxes) or the real fallback (BoardView with an
engine that says no). tests/test_board_lockdown.py's desk cases run the same desk with the real engine.

Covered: the desk's geometry at 1120 x 700, 1232 x 640 and 900 x 600 (and the drawer under a 360 px frames area); the
board made only with [look] board on and a factory here, started on the first Ask (or the first group), never at start;
the desk forming with the first group (the ring's flight, the bar, desk: enter) and folding with Ctrl+B and the
button; folded Home's "9 on the board"; empty Home unchanged with its hint line (counted once a session, gone after
three); the turns (labels, the current turn's marks, bubbles, tokens, the last exchange and Earlier when compact);
each feed (LIVE pictures, research, cards, to-dos, follow-ups) in its group's order; go_approvals; the HUD's chip; the
fallback desk; Space in the board; a grab of the window showing the board at 1x and 1.5x; no words in the logs.
"""

from __future__ import annotations

import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import desk_ui, desk_view, holo, hud, hud_overlay, live, pictures, prefs, ui
from briefing_reader import orb as orb_mod
from briefing_reader.actions import parse_action_line
from briefing_reader.ask.mail import MailMessage, MailPerson, MailThread
from briefing_reader.ask.research_validate import ResearchReport, WebSource
from briefing_reader.board import frames as board_frames
from briefing_reader.board import view
from briefing_reader.gcal import EventBrief
from tests import board_fakes
from tests.ui_fakes import COMMAND, AppHarness, fonts, plan, settle, wait_for

_app: QApplication | None = None
PDT = timezone(timedelta(hours=-7))
THREAD = "18c0ffee00000001"
REPLY_LINE = (f"Reply: acct=work | thread={THREAD} | msgid=<abc@mail.example.edu> | gmid= | to=dana@example.edu | cc= | "
              "subject=Re: Budget numbers | replied=no | due= | link= | body=Thanks Dana, the numbers look right.")
TODO_LINE = "Todo: title=Send budget comments to Dana | due=2026-10-06 | block= | acct=work | link="
SAID = "Three meetings today, sir - the first at ten. Dana sent the budget numbers at 8:12, and a reply waits for your OK."


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_desk", "-platform", "offscreen"])
    fonts()


def thread() -> MailThread:
    return MailThread("work", THREAD, "Re: Budget numbers", (MailMessage(
        "g1", sender=MailPerson("dana@example.edu", "Dana Whitfield"), sent_at=datetime(2026, 10, 4, 8, 12, tzinfo=PDT),
        subject="Re: Budget numbers", text="Here are the Q4 numbers - line 14 changed since Friday."),))


# --------------------------------------------------------------------------
# The geometry (DESIGN-SPEC 2.1 - 2.3)
# --------------------------------------------------------------------------

class GeometryTests(unittest.TestCase):
    def test_1120x700(self) -> None:
        g = desk_view.desk_geometry(908, 636, folded_dock=False)          # _main 908, the body 636
        self.assertFalse(g.compact)
        self.assertEqual(g.column, QRect(24, 12, 336, 600))               # gutter 24 | conversation 336 | ...
        self.assertEqual((g.head.height(), g.ring), (136, 120))
        self.assertEqual(g.seam, QRect(380, 16, 1, 596))                  # ... gap 20 | seam 1 | ...
        self.assertEqual(g.view, QRect(381, 12, 503, 624))                # ... the view 503 | gutter 24
        self.assertEqual((g.frames_x, g.frames_width), (396, 448))        # x 602 and 448 wide in the window
        self.assertEqual(206 + g.view.x(), 587)
        self.assertEqual(206 + g.frames_x, 602)
        self.assertEqual(g.ask, QRect(24, 560, 336, 52))                  # 52 px, the column's full width
        self.assertEqual(g.chips, QRect(24, 518, 336, 30))                # 30 px with 12 above and below
        self.assertEqual((g.turns.top(), g.turns.bottom() + 1), (148, 506))
        self.assertEqual(g.column.bottom() + 1, 636 - 24)                 # the bottom gutter
        self.assertFalse(g.drawer)

    def test_900x600(self) -> None:
        g = desk_view.desk_geometry(824, 536, folded_dock=True)
        self.assertTrue(g.compact)
        self.assertEqual(g.column, QRect(20, 10, 280, 506))
        self.assertEqual((g.head.height(), g.ring), (104, 92))
        self.assertEqual(g.seam.x(), 316)
        self.assertEqual(g.view, QRect(317, 12, 483, 524))
        self.assertEqual(g.frames_width, 428)
        self.assertEqual(g.ask.width(), 280)

    def test_1232x640(self) -> None:
        g = desk_view.desk_geometry(1020, 576, folded_dock=False)
        self.assertEqual(g.column.width(), 377)
        self.assertEqual(g.view.width() - desk_view.PAD, 559)             # the board
        self.assertEqual(g.frames_width, 519)
        self.assertEqual((g.ring, g.head.height()), (92, 108))            # a body under 600 tall: the 92 px ring

    def test_the_width_rules_and_the_drawer(self) -> None:
        self.assertEqual(desk_view.desk_geometry(700, 636, folded_dock=False).column.width(), 300)    # at least 300
        self.assertEqual(desk_view.desk_geometry(1300, 636, folded_dock=False).column.width(), 400)   # at most 400
        self.assertEqual(desk_view.desk_geometry(1000, 636, folded_dock=True).column.width(), 280)
        narrow = desk_view.desk_geometry(700, 500, folded_dock=True)
        self.assertLess(narrow.frames_width, 360)
        self.assertTrue(narrow.drawer)                                    # the safety net: the board as a drawer


# --------------------------------------------------------------------------
# The app's desk (the stand-in board)
# --------------------------------------------------------------------------

class DeskApp(unittest.TestCase):
    def make(self, *, runs=(), size=(1120, 700), factory="stand_in", extra: str = "", **kwargs) -> AppHarness:
        self.boards: list = []
        if factory == "stand_in":
            board_factory = board_fakes.stand_in_factory(self.boards)
        elif factory == "fallback":
            board_factory = board_fakes.fallback_factory(self.boards)
        else:
            board_factory = None
        harness = AppHarness(self, runs=runs, config_extra=extra, board_factory=board_factory, **kwargs)
        harness.reading(autoplay=False)
        self.c, self.window = harness.c, harness.c.window
        self.reading = self.window.reading
        self.window.resize(*size)
        self.reading.set_tab(ui.SCREEN_HOME)
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        settle(6)
        return harness

    def desk(self) -> desk_ui.DeskController:
        return self.c.look.desk

    def ask(self, harness: AppHarness, text: str = COMMAND) -> None:
        harness.c.ask.start()
        self.assertTrue(wait_for(lambda: harness.c.ask.ready is not None))
        harness.c.window.reading.command_bar.set_text(text)
        harness.c.window.reading.command_bar.input.returnPressed.emit()
        settle()
        self.assertTrue(wait_for(lambda: not harness.c.ask.busy, 10))
        settle(4)

    def answer(self, text: str, outcome, *, task=None) -> int:
        """An answer shown on Home: the owner's words, the controller told, Jarvis's turn, the outcome."""
        desk = self.desk()
        task_id = task.id if task is not None else 0
        self.c._you(text)
        desk.on_answer_started(task_id, text)
        if task is not None:
            self.c._live_feed.drain()
        self.c._jarvis(getattr(outcome, "say", "") or "Done.")
        desk.on_ask_outcome(outcome, task_id or desk._current)
        settle(4)
        return desk._current

    @staticmethod
    def outcome(**fields) -> SimpleNamespace:
        values = dict(ok=True, say="", cards=(), research=None, calendar_read=None, mode="")
        values.update(fields)
        return SimpleNamespace(**values)

    def board(self) -> board_fakes.StandInBoard:
        return self.boards[0]

    def in_window(self, widget) -> QRect:
        return QRect(widget.mapTo(self.window, QPoint(0, 0)), widget.size())


class AvailabilityTests(DeskApp):
    def test_board_off_keeps_home_exactly_as_it_was(self) -> None:
        self.make(extra="\n[look]\nboard = false\n")
        self.assertIsNone(self.c.look.desk)
        self.assertFalse(self.reading.board_available())
        self.assertFalse(self.reading.header.board_visible())
        self.assertFalse(self.reading.board_hint_shown())
        self.assertIsNone(self.reading.desk_page())
        self.c._look_call("on_ask_started")
        self.assertEqual(self.boards, [])                                 # never made

    def test_no_factory_on_a_test_platform_no_board(self) -> None:
        self.make(factory=None)
        self.assertIsNone(self.c.look.desk)                               # the engine never starts in a test
        self.assertFalse(self.reading.header.board_visible())
        self.assertFalse(desk_ui.can_have_board(None))
        self.assertTrue(desk_ui.can_have_board(lambda parent: None))

    def test_never_at_start_made_and_started_on_the_first_ask(self) -> None:
        harness = self.make(runs=(plan(),))
        settle(10)
        self.assertEqual(self.boards, [])                                 # not at the app's start, not on its paint
        self.assertTrue(self.reading.header.board_visible())
        self.assertEqual(self.reading.header.board_button.count(), 0)     # dimmed: empty
        self.ask(harness)
        self.assertEqual(len(self.boards), 1)
        self.assertEqual(self.board().begins, 1)                          # started once
        self.assertTrue(self.board().is_ready())
        self.c._look_call("on_ask_started")
        self.assertEqual(len(self.boards), 1)                             # one board for the window's life

    def test_the_first_group_starts_it_too(self) -> None:
        self.make()
        desk = self.desk()
        report = ResearchReport("The final round moved to Friday [1].",
                                sources=(WebSource(1, "City Hack schedule", "https://city-hack.example.org/s",
                                                   "city-hack.example.org"),))
        desk.on_ask_outcome(self.outcome(research=report), 0)             # a group with no Ask before it
        settle(4)
        self.assertEqual(len(self.boards), 1)
        self.assertEqual(self.reading.board_count(), 1)


class DeskFormsTests(DeskApp):
    def put_demo(self) -> None:
        report = ResearchReport("The final round moved to Friday [1].",
                                sources=(WebSource(1, "City Hack schedule", "https://city-hack.example.org/s",
                                                   "city-hack.example.org"),))
        self.c._you("What's new with City Hack?")
        self.desk().on_answer_started(0, "What's new with City Hack?")
        self.c._jarvis("The final round moved to Friday, sir.")
        self.desk().on_ask_outcome(self.outcome(research=report, say="The final round moved to Friday, sir."))
        settle(6)

    def test_the_desk_forms_with_the_first_group(self) -> None:
        self.make()
        stage_before = self.in_window(self.reading.stage)
        self.assertTrue(self.reading.stage.isVisible())
        self.put_demo()
        reading = self.reading
        self.assertTrue(reading.desk_shown())
        self.assertFalse(reading.stage.isVisible())
        desk = reading.desk_page()
        board = self.board()
        self.assertIs(board.parentWidget(), desk)
        geometry = desk.geometry_now()
        self.assertEqual(board.geometry(), geometry.view)                 # set once, at its place
        self.assertEqual(self.in_window(board).x(), self.in_window(desk).x() + 381)
        self.assertIs(reading.command_bar.parentWidget(), desk.ask_slot)   # the ask bar in the column ...
        bar = QRect(self.in_window(reading.command_bar))
        column = QRect(self.in_window(desk).topLeft() + geometry.column.topLeft(), geometry.column.size())
        self.assertEqual((bar.left() + 4, bar.right() - 4), (column.left(), column.right()))   # ... its full width
        self.assertTrue(desk.isAncestorOf(reading.approvals_chip))
        types = [message["type"] for message in board.sent]
        self.assertEqual(types[0], "motion")
        self.assertIn("bar", types)
        self.assertEqual(types[-1], "desk")
        self.assertEqual(board.sent[-1], {"type": "desk", "state": "enter"})
        self.assertEqual(reading.header.board_button.count(), 1)          # the cyan badge
        self.assertTrue(stage_before.isValid())

    def test_the_ring_flies_to_the_column_head(self) -> None:
        self.make()
        holo.set_motion_level(holo.MOTION_LIVELY)
        self.put_demo()
        flight = self.reading.ring_flight()
        self.assertIsNotNone(flight)
        self.assertEqual(flight.length_ms(), desk_view.FLIGHT_MS)          # 420 ms, ease-out-expo
        orb = self.reading.desk_page().head.orb
        self.assertTrue(wait_for(lambda: not flight.running(), 2))
        self.assertTrue(orb.isVisible())                                  # landed: the column's ring shows
        self.assertEqual(orb.orb_size(), 120)

    def test_with_motion_off_the_desk_is_there_at_once(self) -> None:
        self.make()
        self.put_demo()
        flight = self.reading.ring_flight()
        self.assertTrue(flight is None or not flight.running())
        self.assertTrue(self.reading.desk_page().head.orb.isVisible())

    def test_ctrl_b_and_the_button_fold_and_unfold_it(self) -> None:
        self.make()
        self.put_demo()
        reading = self.reading
        reading.header.board_button.click()
        settle()
        self.assertFalse(reading.desk_shown())                            # Home's stage again ...
        self.assertTrue(reading.stage.isVisible())
        self.assertEqual(reading.board_chip_words(), "1 on the board")    # ... with the chip that brings it back
        self.assertEqual(self.board().sent[-1], {"type": "desk", "state": "leave"})
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        reading.setFocus()
        settle()
        QTest.keyClick(QApplication.focusWidget() or self.window, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
        settle()
        self.assertTrue(reading.desk_shown())                             # Ctrl+B unfolds it
        self.assertEqual(self.board().sent[-1], {"type": "desk", "state": "enter"})
        QTest.keyClick(QApplication.focusWidget() or self.window, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
        settle()
        self.assertFalse(reading.desk_shown())                            # ... and folds it again
        reading.board_chip.click()
        settle()
        self.assertTrue(reading.desk_shown())                             # the chip brings it back
        reading.set_tab(ui.SCREEN_APPROVALS)
        reading.toggle_board()
        settle()
        self.assertEqual(reading.current_tab(), ui.SCREEN_HOME)            # from another screen: Home with the board
        self.assertTrue(reading.desk_shown())

    def test_the_window_s_overlays_keep_working_with_the_desk(self) -> None:
        self.make()
        self.put_demo()
        reading = self.reading
        reading.open_find()
        settle()
        self.assertTrue(reading.find_overlay.isVisible())                 # Find over the desk
        reading.find_overlay.close_list()
        reading.open_day_view()
        settle()
        self.assertTrue(reading.day_drawer.is_open())                    # the Day drawer over the board
        reading.day_drawer.close_drawer()
        holo.set_motion_level(holo.MOTION_LIVELY)
        reading.set_tab(ui.SCREEN_APPROVALS)
        reading.set_tab(ui.SCREEN_HOME)
        self.assertTrue(reading.transition.is_running())                 # the wipe plays onto the desk ...
        QApplication.processEvents()
        self.assertTrue(reading.transition.has_snapshot())               # ... its snapshot taken of it
        board = QRectF(QRect(self.board().mapTo(reading.home_screen, QPoint(0, 0)), self.board().size()))
        self.assertIn(board, reading.transition.panel_rects())           # the board one of its panels
        self.assertTrue(reading.desk_shown())

    def test_the_board_s_requests_are_wired_and_counted(self) -> None:
        self.make()
        self.put_demo()
        board, desk = self.board(), self.desk()
        frame_id = board.board_order()[0]
        board.frameFocused.emit(frame_id)
        board.openRequested.emit(frame_id)
        board.closeRequested.emit(frame_id)
        board.pinRequested.emit(frame_id, True)
        board.bringBackRequested.emit(frame_id)
        board.retryRequested.emit(frame_id)
        for word in ("frameFocused", "openRequested", "closeRequested", "pinRequested", "bringBackRequested",
                     "retryRequested"):
            self.assertEqual(desk.counts[f"request:{word}"], 1, word)    # part 5b gives them their work
        self.assertTrue(self.reading.desk_shown())                        # and nothing else changed

    def test_the_page_follows_the_motion_level(self) -> None:
        self.make()
        self.put_demo()
        board = self.board()
        self.assertEqual(board.sent[0]["type"], "motion")
        holo.set_reduced_motion(True)
        self.addCleanup(holo.set_reduced_motion, None)
        self.assertEqual(board.of_type("motion")[-1], {"type": "motion", "level": "off"})   # Windows: no animations
        holo.set_reduced_motion(None)
        holo.set_motion_level(holo.MOTION_CALM)
        self.reading.toggle_board()
        self.reading.toggle_board()                                       # the desk forms again: as it is now
        self.assertEqual(board.of_type("motion")[-1], {"type": "motion", "level": "calm"})

    def test_a_moment_takes_the_stage_and_the_desk_comes_back(self) -> None:
        self.make()
        self.put_demo()
        self.reading._set_moment(ui.MOMENT_PLAN)                          # noqa: SLF001 - the plan shows on Home
        self.assertFalse(self.reading.desk_shown())
        self.reading._set_moment("")                                      # noqa: SLF001
        self.assertTrue(self.reading.desk_shown())


class DeskGeometryAppTests(DeskApp):
    """The real widgets at each size: the column, the seam, the board's view, the chips and the ask bar where the
    geometry puts them, and no turn's words clipped (measured)."""

    def check_size(self, width: int, height: int) -> None:
        self.make(size=(width, height))
        c = self.c
        c._you("What's on my list this week?")
        c._jarvis("Four things, sir. The LING essay is the big one: the full draft is due Wednesday, the essay "
                  "Thursday night.")
        DeskFormsTests.put_demo(self)
        reading = self.reading
        desk = reading.desk_page()
        self.assertTrue(reading.desk_shown())
        area = reading._home_area()                                    # noqa: SLF001
        expected = desk_view.desk_geometry(area.width(), area.height(), folded_dock=width < ui.DOCK_LABELS_FROM)
        self.assertEqual(desk.geometry_now(), expected)
        self.assertEqual(self.board().geometry(), expected.view)        # the frames' x: view + 15
        self.assertEqual(desk.head.geometry(), expected.head)
        self.assertEqual(desk.turns.geometry(), expected.turns)
        bar = QRect(reading.command_bar.mapTo(desk, QPoint(0, 0)), reading.command_bar.size())
        self.assertEqual((bar.left() + 4, bar.right() - 4), (expected.ask.left(), expected.ask.right()))
        self.assertEqual(bar.bottom() - 4, expected.ask.bottom())
        for chip in (reading.approvals_chip, reading.briefing_chip, reading.answer_chip):
            if chip.isVisible():
                drawn = QRect(chip.mapTo(desk, QPoint(0, 0)), chip.size()).adjusted(4, 4, -4, -4)
                self.assertTrue(expected.column.contains(drawn), (chip.text(), drawn, expected.column))
        ring = desk.head.ring_rect()
        if expected.compact:
            self.assertEqual(ring.left(), 0)                            # the 92 px ring at the left
        else:
            self.assertAlmostEqual(QRectF(ring).center().x(), desk.head.width() / 2, delta=1)   # centred
        self.assertEqual(ring.width(), expected.ring)
        for turn in desk.turns.views():                                 # never clipped: measured
            text = turn.text_rect()
            self.assertLessEqual(text.bottom(), turn.height() + 0.5, turn.label())
            self.assertLessEqual(turn.block().natural, text.width() + 0.5, turn.label())
            self.assertLessEqual(turn.width(), expected.turns.width())

    def test_1120x700(self) -> None:
        self.check_size(1120, 700)

    def test_1232x640(self) -> None:
        self.check_size(1232, 640)
        self.assertEqual(self.reading.desk_page().geometry_now().ring, 92)

    def test_900x600(self) -> None:
        self.check_size(900, 600)
        self.assertTrue(self.reading.desk_page().geometry_now().compact)
        self.assertEqual(self.board().of_type("bar")[-1]["compact"], True)   # the bar drops its frames count

    def test_under_a_360_px_frames_area_the_board_is_a_drawer(self) -> None:
        from PySide6.QtCore import QSize

        self.make()
        with mock.patch.object(self.reading, "_home_area", return_value=QSize(700, 500)):
            self.assertTrue(self.reading.board_in_drawer())
            DeskFormsTests.put_demo(self)
            self.assertFalse(self.reading.desk_shown())                 # Home stays the stage ...
            self.assertEqual(self.reading.board_chip_words(), "1 on the board")
            self.reading.toggle_board()
            settle()
            drawer = self.reading.board_drawer
            self.assertTrue(drawer.is_open())                           # ... and the board opens as the drawer
            self.assertIs(self.board().parentWidget(), drawer)
            self.reading.toggle_board()
            settle()
            self.assertTrue(wait_for(lambda: not drawer.is_open(), 2))


class HomeTests(DeskApp):
    def home_places(self) -> dict:
        reading = self.reading
        return {"hero": reading.hero.orb_size(), "hero_at": self.in_window(reading.hero),
                "captions": self.in_window(reading.captions).bottom(), "bar": self.in_window(reading.command_bar),
                "chips": self.in_window(reading._chip_row), "greeting": reading.captions.resting()}  # noqa: SLF001

    def test_empty_home_is_today_s_with_the_hint_line(self) -> None:
        self.make(factory=None)
        before = self.home_places()
        self.make()
        after = self.home_places()
        self.assertEqual(after["hero"], 320)                              # today's hero size at 1120 x 700
        self.assertEqual(after["hero"], before["hero"])
        self.assertEqual(after["bar"], before["bar"])                     # the ask bar where it is today
        self.assertEqual(after["chips"], before["chips"])                 # the chips too
        self.assertEqual(after["greeting"], before["greeting"])
        reading = self.reading
        self.assertTrue(reading.board_hint_shown())
        hint = self.in_window(reading.board_hint)
        self.assertGreater(hint.top(), after["captions"])                 # under the greeting ...
        self.assertLess(hint.bottom(), after["chips"].top())              # ... over the chips
        self.assertGreaterEqual(hint.width(), reading.board_hint.natural_width())   # never cut
        self.assertEqual(desk_view.HINT_TEXT,
                         "When I find something to show you, it appears beside our conversation.")

    def test_the_hint_counts_once_a_session_and_stops_after_three(self) -> None:
        self.make(prepare=lambda config: [prefs.AssistantPrefs(config.data_dir / prefs.PREFS_FILE).note_board_hint()
                                          for _ in range(2)])
        self.assertTrue(self.reading.board_hint_shown())
        DeskFormsTests.put_demo(self)
        store = prefs.AssistantPrefs(self.c.config.data_dir / prefs.PREFS_FILE)
        self.assertEqual(store.look.board_hints, 3)                       # the desk formed: seen a third time
        self.reading.toggle_board()
        self.reading.toggle_board()                                       # formed again: not counted again
        self.assertEqual(prefs.AssistantPrefs(self.c.config.data_dir / prefs.PREFS_FILE).look.board_hints, 3)
        self.make(prepare=lambda config: [prefs.AssistantPrefs(config.data_dir / prefs.PREFS_FILE).note_board_hint()
                                          for _ in range(3)])
        self.assertFalse(self.reading.board_hint_shown())                  # after three: never again

    def test_the_amber_chip_leads_with_the_diamond(self) -> None:
        chip = holo.ActionChip("1 needs your OK", holo.CHIP_AMBER)
        plain = holo.ActionChip("1 needs your OK", holo.CHIP_CYAN)
        self.assertEqual(chip._lead(), "diamond")                         # noqa: SLF001
        self.assertEqual(chip.sizeHint().width(), plain.sizeHint().width() + holo.ActionChip.LEAD)
        image = QPixmap(chip.sizeHint())
        image.fill(QColor(holo.VOID))
        chip.resize(chip.sizeHint())
        chip.render(image)
        amber = QColor(holo.AMBER)
        found = image.toImage()
        hits = sum(1 for x in range(12, 26) for y in range(14, 26)
                   if abs(QColor(found.pixel(x, y)).red() - amber.red()) < 40
                   and abs(QColor(found.pixel(x, y)).green() - amber.green()) < 40)
        self.assertGreater(hits, 6)                                       # a drawn diamond, amber


class TurnTests(DeskApp):
    def test_the_turns_are_the_transcript_s_entries(self) -> None:
        self.make()
        c = self.c
        c._you("What's on my list this week?")
        c._jarvis("Four things, sir.")
        c._you("What does my day look like, and did Dana send the budget numbers?")
        report = ResearchReport("Ten things [1].", sources=(WebSource(1, "A", "https://a.example.org/", "a.example.org"),))
        self.desk().on_answer_started(0, "x")
        c._jarvis(SAID)
        self.desk().on_ask_outcome(self.outcome(research=report, say=SAID))
        settle(6)
        views = self.reading.desk_page().turns.views()
        self.assertEqual([view.label().split(" ")[0] for view in views], ["YOU", "JARVIS", "YOU", "JARVIS"])
        stamp = views[1].turn.stamp
        self.assertEqual(views[1].label(), f"JARVIS · {stamp}".upper())
        current = views[-1]
        self.assertTrue(current.current())
        self.assertFalse(views[1].current())
        self.assertEqual(current.block().font.pixelSize(), 16)            # Sora 16/25 ...
        self.assertEqual(current.block().line, 25)
        self.assertEqual(views[1].block().font.pixelSize(), 13)           # ... the others body_sm
        marks = {SAID[start:start + length]: color for start, length, color in current.block().marks}
        self.assertEqual(marks["ten"], holo.HOLO_HI)                      # times in HOLO_HI
        self.assertEqual(marks["8:12"], holo.HOLO_HI)
        self.assertEqual(marks["a reply waits for your OK"], holo.AMBER_HI)   # the waiting phrase in AMBER_HI
        self.assertEqual(current.frames(), 1)
        self.assertEqual(current.token.text(), "1 frame on the board")
        you = views[2]
        self.assertTrue(you.you())
        width = you.width()
        bubble = you._bubble_rect(width)                                  # noqa: SLF001
        self.assertAlmostEqual(bubble.right(), width, delta=0.5)         # right-aligned ...
        self.assertLessEqual(bubble.width(), 0.9 * width + 0.5)           # ... at most 90 % of the column
        for view in views:                                                # never clipped: every line has its room
            text = view.text_rect()
            self.assertLessEqual(text.bottom(), view.height() + 0.5)
            self.assertLessEqual(view.block().natural, text.width() + 0.5)

    def test_a_token_scrolls_the_board_to_its_group(self) -> None:
        self.make()
        DeskFormsTests.put_demo(self)
        current = self.reading.desk_page().turns.views()[-1]
        group = self.desk().feed.turn_frames(current.turn.entry_id)[0]
        self.assertTrue(group)
        current.token.click()
        settle()
        self.assertEqual(self.board().sent[-1], {"type": "reveal", "group": group})

    def test_compact_shows_the_last_exchange_and_earlier_opens_the_transcript(self) -> None:
        self.make(size=(900, 600))
        c = self.c
        c._you("one")
        c._jarvis("Two.")
        DeskFormsTests.put_demo(self)
        desk = self.reading.desk_page()
        self.assertTrue(desk.geometry_now().compact)
        views = desk.turns.views()
        self.assertEqual([view.you() for view in views], [True, False])   # the last exchange only
        self.assertEqual(desk.head.earlier.text(), "Earlier · 2 turns")
        self.assertTrue(desk.head.earlier.isVisible())
        self.assertEqual(desk.head.orb.orb_size(), 92)
        desk.head.earlier.click()
        settle()
        self.assertTrue(self.reading.transcript_drawer.is_open())
        shown = [chip for chip in (self.reading.approvals_chip, self.reading.briefing_chip, self.reading.answer_chip)
                 if chip.isVisible()]
        self.assertLessEqual(len(shown), 1)                               # one chip, the amber one first

    def test_what_jarvis_says_now_is_the_current_turn_until_his_turn_comes(self) -> None:
        self.make()
        DeskFormsTests.put_demo(self)
        turns = self.reading.desk_page().turns
        turns.set_live("Checking your calendar now")
        self.assertEqual(turns.views()[-1].turn.entry_id, -1)
        self.c._jarvis("Checking your calendar now, sir.")
        self.assertEqual(turns.live_text(), "")                           # his turn came: it stands for it
        self.assertNotEqual(turns.views()[-1].turn.entry_id, -1)


class FeedTests(DeskApp):
    def live_task(self):
        task = self.c.live.task(live.TASK_ASK, "Ask")
        return task

    def test_pictures_research_cards_and_todos_in_their_group_s_order(self) -> None:
        self.make()
        task = self.live_task()
        mail = task.step(live.MAIL_THREAD, "Read email thread", key=f"work:{THREAD}")
        mail.picture(pictures.email_picture(thread()))
        search = task.step(live.WEB_SEARCH, "Searched the web")
        search.picture(pictures.search_picture("city hack schedule", [("City Hack 2026", "https://city-hack.example.org/")]))
        reply = parse_action_line(REPLY_LINE)
        todo = parse_action_line(TODO_LINE)
        report = ResearchReport("The final round moved to Friday [1].",
                                sources=(WebSource(1, "City Hack schedule", "https://city-hack.example.org/s",
                                                   "city-hack.example.org"),))
        self.answer("Did Dana send the numbers? Add a to-do.", self.outcome(
            say="Dana sent the budget numbers, sir; the final round moved.", cards=(reply, todo), research=report),
            task=task)
        board = self.board()
        frames = board.frames_sent()
        kinds = {frame["kind"]: frame for frame in frames.values()}
        self.assertEqual(set(kinds), {"mail_thread", "text", "todos"})
        mail_frames = [frame for frame in frames.values() if frame["kind"] == "mail_thread"]
        self.assertEqual(len(mail_frames), 1)                             # the mail and its reply: ONE frame
        self.assertEqual(mail_frames[0]["body"]["sender"], "Dana Whitfield")
        self.assertEqual(mail_frames[0]["waits"], "Reply waits on Approvals")
        order = board.board_order()
        self.assertEqual(frames[order[0]]["kind"], "mail_thread")         # what waits first
        groups = board.groups_sent()
        self.assertEqual(len(groups), 1)                                  # one answer, one group
        research = next(frame for frame in frames.values() if frame["title"] == board_frames.RESEARCH_TITLE)
        self.assertEqual(research["body"]["rows"][0]["lead"], "1")       # its [1] numbered
        todos = kinds["todos"]
        self.assertIn("waits", [row["state"] for row in todos["body"]["rows"]])
        # go_approvals on the merged frame opens Approvals at the reply's card (Ctrl+3's path).
        frame_id = mail_frames[0]["id"]
        self.assertEqual(self.desk().feed.approvals_for(frame_id), reply.id)
        with mock.patch.object(self.reading, "open_card", wraps=self.reading.open_card) as opened:
            board.approvalsRequested.emit(frame_id)
            settle()
        opened.assert_called_once_with(reply.id)
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_APPROVALS)

    def test_a_page_picture_that_comes_ready_late_joins_its_group(self) -> None:
        self.make()
        task = self.live_task()
        step = task.step(live.WEB_FETCH, "Read a page")
        step.picture(pictures.page_pending("https://city-hack.example.org/schedule"))
        report = ResearchReport("Friday [1].", sources=(WebSource(1, "S", "https://city-hack.example.org/s",
                                                                  "city-hack.example.org"),))
        self.answer("web: city hack", self.outcome(research=report, say="Friday."), task=task)
        first = self.board().frames_sent()
        page = [frame for frame in first.values() if frame["kind"] == "picture"]
        self.assertEqual(page[0]["status"], "empty")                      # on its way
        from tests.ui_fakes import tiny_jpeg

        step.picture(pictures.page_ready("https://city-hack.example.org/schedule", image=tiny_jpeg(96, 54), width=96,
                                         height=54))
        self.c._live_feed.drain()
        settle()
        after = self.board().frames_sent()
        self.assertEqual(after[page[0]["id"]]["status"], "ok")           # the same frame, now filled
        self.assertEqual(len(self.board().groups_sent()), 1)
        self.assertTrue(self.board().of_type("picture"))

    def test_the_calendar_read_only_when_the_answer_is_about_it(self) -> None:
        self.make()
        for words, shown in (("Remind me about the essay", False), ("What's on my calendar next week?", True)):
            with self.subTest(words=words):
                task = self.live_task()
                read = task.step(live.CALENDAR_READ, "Calendar")
                start = datetime(2026, 10, 4, tzinfo=PDT)
                events = {"work": [EventBrief("e1", "primary", "Study group", start=start + timedelta(days=1, hours=13),
                                              end=start + timedelta(days=1, hours=14))]}
                read.picture(pictures.week_picture(events, start, start + timedelta(days=7), now=start))
                before = len(self.board().frames_sent()) if self.boards else 0
                self.answer(words, self.outcome(say="Done.", research=ResearchReport(
                    "x [1].", sources=(WebSource(1, "S", "https://a.example.org", "a.example.org"),))), task=task)
                calendars = [frame for frame in self.board().frames_sent().values() if frame["kind"] == "calendar"]
                self.assertEqual(bool(calendars), shown)
                self.assertGreater(len(self.board().frames_sent()), before)

    def test_today_s_day_when_the_answer_speaks_of_today(self) -> None:
        self.make()
        now = self.c._now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        events = (("work", (EventBrief("e1", "primary", "AI-Trader check-in", start=now + timedelta(minutes=19),
                                       end=now + timedelta(minutes=49)),)),)
        read = pictures.CalendarRead(start, start + timedelta(days=7), events, now)
        self.answer("What does my day look like?", self.outcome(say="One meeting today, sir.", calendar_read=read))
        (frame,) = self.board().frames_sent().values()
        self.assertEqual((frame["kind"], frame["title"]), ("calendar", "Today"))
        self.assertTrue(frame["body"]["now"])                            # the now-line's pill
        self.assertEqual(frame["body"]["rows"][0]["state"], "next")

    def test_follow_ups_spoken_of(self) -> None:
        self.make(productivity=True)
        from briefing_reader.followups import WAITING, FollowupItem

        item = FollowupItem("r1", "k", WAITING, "work", thread_id="t", last_gmail_id="g", who="Prof. Ana Ruiz",
                            subject="Lab section moved to Thursday", since=datetime(2026, 10, 3, 8, tzinfo=PDT))
        with mock.patch.object(self.c.productivity, "followups", return_value=SimpleNamespace(rows=(item,))):
            self.answer("Who am I waiting on?", self.outcome(say="You're waiting on Ana."))
        kinds = [frame["kind"] for frame in self.board().frames_sent().values()]
        self.assertEqual(kinds, ["mail_list"])

    def test_limits_the_oldest_group_leaves_whole(self) -> None:
        self.make()
        for index in range(9):
            report = ResearchReport(f"Answer {index} [1].", sources=(WebSource(1, "S", "https://a.example.org",
                                                                               "a.example.org"),))
            self.answer(f"web: question {index}", self.outcome(research=report, say=f"Answer {index}."))
        self.assertEqual(len(self.board().groups_sent()), 8)
        self.assertEqual(self.board().of_type("remove_group")[0]["id"], "g1")
        self.assertEqual(self.reading.board_count(), 8)

    def test_the_real_ask_puts_its_cards_up(self) -> None:
        harness = self.make(runs=(plan(),))
        self.ask(harness)
        frames = self.board().frames_sent()
        kinds = sorted(frame["kind"] for frame in frames.values())
        self.assertIn("mail_thread", kinds)                               # the Email card, waiting
        self.assertIn("calendar", kinds)                                  # the Move card's change on its day
        waits = [frame["waits"] for frame in frames.values() if frame["waits"]]
        self.assertEqual(waits, ["Email waits on Approvals"])
        self.assertTrue(self.reading.desk_shown())
        self.assertEqual(self.reading.board_count(), len(frames))

    def test_the_logs_hold_no_words(self) -> None:
        self.make()
        secret = "Zanzibar-quartz-7781"
        with self.assertLogs("briefing_reader", level=logging.DEBUG) as logs:
            report = ResearchReport(f"{secret} [1].", sources=(WebSource(1, secret, "https://a.example.org",
                                                                         "a.example.org"),))
            self.answer(f"web: {secret}", self.outcome(research=report, say=secret))
            logging.getLogger("briefing_reader").debug("marker")
        self.assertFalse(any(secret in line for line in logs.output))


class FallbackTests(DeskApp):
    def test_the_desk_still_forms_with_read_only_tiles(self) -> None:
        self.make(factory="fallback")
        DeskFormsTests.put_demo(self)
        board = self.boards[0]
        self.assertTrue(self.reading.desk_shown())
        self.assertEqual(board.unavailable_reason(), "no_engine")
        bar = board.bar_widget()
        self.assertTrue(bar.isVisible())
        self.assertEqual(bar.words(), ["Board", "READ-ONLY", "1 FRAME"])  # the bar says READ-ONLY
        self.assertTrue(board.fallback().isVisible())
        tiles = board.tiles()
        self.assertEqual([frame["title"] for frame in tiles.frames()], [board_frames.RESEARCH_TITLE])
        self.assertEqual(list(tiles.headers()), [tiles.frames()[0]["id"]])   # the group's header over its tile
        x = board.fallback().mapTo(board, QPoint(0, 0)).x()
        self.assertEqual(x, view.PAD)                                     # the frames' x
        self.assertEqual(board.width() - (board.fallback().mapTo(board, QPoint(0, 0)).x() + board.fallback().width()),
                         view.STRIP)                                      # no strip: its room kept (448 wide)


class KeysAndGrabTests(DeskApp):
    def test_space_in_the_board_never_plays_the_briefing(self) -> None:
        self.make()
        DeskFormsTests.put_demo(self)
        plays: list[int] = []
        self.reading.playPause.connect(lambda: plays.append(1))
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        page = self.board().page_widget
        page.setFocus(Qt.FocusReason.OtherFocusReason)
        settle()
        self.assertTrue(view.keeps_space(QApplication.focusWidget()))
        QTest.keyClick(page, Qt.Key.Key_Space)
        settle()
        self.assertEqual(plays, [])
        self.reading.setFocus(Qt.FocusReason.OtherFocusReason)
        settle()
        QTest.keyClick(self.reading, Qt.Key.Key_Space)
        settle()
        self.assertEqual(plays, [1])                                      # elsewhere Space still plays

    def test_a_grab_of_the_window_shows_the_board_at_1x_and_1_5x(self) -> None:
        self.make()
        DeskFormsTests.put_demo(self)
        board = self.board()
        page = board.page_widget
        box = page.boxes()[0][1]
        at = page.mapTo(self.window, QPoint(round(box.left() + 30), round(box.top() + 50)))
        tile = QColor(holo.TILE)
        for ratio in (1.0, 1.5):
            with self.subTest(ratio=ratio):
                pixmap = QPixmap(round(self.window.width() * ratio), round(self.window.height() * ratio))
                pixmap.setDevicePixelRatio(ratio)
                pixmap.fill(Qt.GlobalColor.transparent)
                self.window.render(pixmap)                                # as the wipe, the flight and the HUD grab it
                color = QColor(pixmap.toImage().pixel(round(at.x() * ratio), round(at.y() * ratio)))
                self.assertLess(abs(color.red() - tile.red()) + abs(color.green() - tile.green())
                                + abs(color.blue() - tile.blue()), 12, color.name())
        image = self.window.grab().toImage()
        color = QColor(image.pixel(round(at.x() * image.devicePixelRatio()), round(at.y() * image.devicePixelRatio())))
        self.assertEqual(color.name(), tile.name())


class HudChipTests(DeskApp):
    def test_the_hud_gets_9_on_the_board_which_brings_jarvis_back_with_it(self) -> None:
        from tests.test_desktop_hud import DESKTOP_ON, FakeNative

        native = FakeNative()
        self.make(extra=DESKTOP_ON, desktop_factory=lambda config: hud_overlay.create(config, native=native))
        DeskFormsTests.put_demo(self)
        self.reading.toggle_board()                                       # folded before leaving
        desktop = self.c.desktop
        self.assertTrue(desktop.enter_hud())
        settle()
        hud = desktop.overlay.hud
        self.assertIn("1 on the board", hud.chip_texts())
        chip = hud.chips[hud_overlay.CHIP_BOARD]
        QTest.mouseClick(chip, Qt.MouseButton.LeftButton)
        settle(6)
        self.assertFalse(desktop.is_hud())
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_HOME)
        self.assertTrue(self.reading.desk_shown())                        # Jarvis back with the board showing


if __name__ == "__main__":
    unittest.main()
