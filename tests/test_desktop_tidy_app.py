"""Jarvis on your desktop, P2.3 in the app: the NEEDS YOUR OK card of file steps, its run and its Undo (SPEC 5.4, 6.3,
6.4, 8.1, 8.3, 8.4, 9, 10.1-10.3 and 16 P2.3; hard rules 1, 4, 7, 12, 13, 14 and 17).

A tidy request typed on the HUD goes to the planner (tests.ask_fakes: no real run) and comes back as ONE amber card
beside the File Explorer window showing Downloads (a pretend window: tests.desktop_fakes.FakeWinApi) with a cyan
bracket, its tag and a tether; it counts in "N need your OK". Nothing moves until the owner's click on "Move 5 files";
then exactly the ticked rows move on the desktop thread (the fake backend over a temporary folder - nothing real is
touched), "MOVING 2 OF 5" as each ends, and the stamp; Stop on the card, Esc (tests.desktop_fakes.FakeStopKeys: no real
hot key, held only while the batch acts) or a Turn off stop it after the row on its way; its Undo puts the files back,
newest first, and says plainly what it left ("Moved since - left where it is."). An expired plan, a click too soon
after another decision or while the cards are locked runs nothing. The card never takes the focus, leaving the HUD
leaves no window or hook (Approvals reminds instead), and no name reaches a log. Every name is invented.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import desktop_ui, dock, hud_overlay, ui
from briefing_reader.desktop import cardplan as cp
from briefing_reader.desktop import journal as jn
from briefing_reader.desktop import tidy as dt
from briefing_reader.desktop import winwatch as ww
from briefing_reader.desktop.context import FileItem
from tests.ask_fakes import plan_stream
from tests.desktop_fakes import DOCS, DOWNLOADS, LING, UNI, FakeStopKeys, FakeWinApi, desktop_context
from tests.test_desktop_requests import DesktopRequestApp
from tests.ui_fakes import fonts, settle, wait_for

DOT = "\u00b7"
CRUMB = "\u203a"
NAMES = ("LING_reading_wk7.pdf", "ECON_problem_set_4.pdf", "IMG_2041.jpg", "Syllabus_HIST210.docx",
         "budget_oct.xlsx", "setup_zoom_v6.exe")
WORDS = "tidy my Downloads into LING, Uni and Documents"
TIDY_PLAN = {"say": "I'd move five files.",
             "steps": [{"skill": "move_files", "why": "x", "files": ["f1"], "to": "d3"},
                       {"skill": "move_files", "why": "x", "files": ["f2", "f4"], "to": "d2"},
                       {"skill": "move_files", "why": "x", "files": ["f3", "f5", "f6"], "to": "d1"}]}
# The card's rows, in order: (name, where it goes) - the installer (row 6) unticked.
ROWS = [("LING_reading_wk7.pdf", LING), ("ECON_problem_set_4.pdf", UNI), ("Syllabus_HIST210.docx", UNI),
        ("IMG_2041.jpg", DOCS), ("budget_oct.xlsx", DOCS), ("setup_zoom_v6.exe", DOCS)]
EXPLORER = 0x3A0
EXPLORER_RECT = (16, 150, 280, 380)          # the pretend File Explorer window on Downloads (one 1x screen)
EXPLORER_EXE = "C:\\Windows\\" + "explorer.exe"
LOGGED_NAMES = ("LING_reading", "ECON", "IMG_2041", "Syllabus", "budget_oct", "setup_zoom", "Downloads", "Uni",
                "Owner", "C:\\")

_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_desktop_tidy_app", "-platform", "offscreen"])
    fonts()


class TidyFlowCase(DesktopRequestApp):
    """DesktopRequestApp with the plan's six files in the pretend Downloads, the journal in a temporary folder, the
    fake Esc seam and the pretend File Explorer window on Downloads; the click guard's clock a fake one."""

    def setUp(self) -> None:
        super().setUp()
        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry()
        self.winapi = FakeWinApi(monitors=[ww.Monitor(screen.name(), ww.Rect(geometry.x(), geometry.y(),
                                                                             geometry.width(), geometry.height()))])
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.journal_dir = os.path.join(tmp.name, "journal")
        self.keys = FakeStopKeys()
        self.clock = [1000.0]

    def make_tidy(self, *, explorer: bool = True, consent: bool | int = True) -> None:
        self.make_ask(plan_stream(TIDY_PLAN), desktop_window_api=self.winapi, desktop_consent=consent)
        files = []
        for name in NAMES:
            path = DOWNLOADS + "\\" + name
            self.fake.make_file(path, b"x" * 100)
            files.append(FileItem(path, name, DOWNLOADS, size=100))
        context = desktop_context(files=files, backend=self.fake)
        self.desktop.context_source = lambda _words: context
        self.desktop.journal_folder = lambda: self.journal_dir
        self.desktop.stop_keys = self.keys
        self.c._click_clock = lambda: self.clock[0]
        if explorer:
            self.winapi.add(EXPLORER, pid=61, exe=EXPLORER_EXE, rect=EXPLORER_RECT, folder=DOWNLOADS)
        self.enter()
        self.type_on_hud(WORDS)
        self.finish()
        settle()

    def later(self, seconds: float = 2.0) -> None:
        self.clock[0] += seconds

    def entry(self):  # type: ignore[no-untyped-def]
        return self.desktop.results.tidy()

    def card(self) -> hud_overlay.DesktopCard:
        entry = self.entry()
        self.assertIsNotNone(entry, "the tidy card shows")
        return entry.window

    def press(self, key: str, *, wait: bool = True) -> None:
        QTest.mouseClick(self.card().buttons()[key], Qt.MouseButton.LeftButton)
        settle()
        if wait:
            self.assertTrue(wait_for(lambda: not self.desktop.busy()), "the card's job ended")
            settle()

    def tick(self, number: int) -> None:
        QTest.mouseClick(self.card().ticks()[number], Qt.MouseButton.LeftButton)
        settle()

    def where(self, number: int) -> tuple[str, str]:
        """Row ``number``'s file: (where it was, where the plan puts it) - pretend paths."""
        name, folder = ROWS[number - 1]
        return DOWNLOADS + "\\" + name, folder + "\\" + name

    def there(self, path: str) -> bool:
        return os.path.lexists(self.fake.real(path))

    def moves(self) -> list[str]:
        return [argument for method, argument in self.fake.calls if method == "move_item"]

    def hold_at_move(self, number: int) -> tuple[threading.Event, threading.Event]:
        """The desktop thread holds at its ``number``th move (before it is made) until the test lets it go."""
        holding, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        seen = [0]

        def slow(method: str, _argument: str) -> None:
            if method == "move_item":
                seen[0] += 1
                if seen[0] == number:
                    holding.set()
                    release.wait(10)
        self.fake.on_call = slow
        return holding, release

    def kicker(self) -> str:
        return self.card().desk().kicker

    def rows(self) -> list[hud_overlay.DeskRow]:
        return list(self.card().desk().rows)

    def tasks(self) -> list:
        return [task for task in self.desktop_tasks() if "NEEDS YOUR OK" in task.title or task.title.startswith("Undo")]


class WaitingCardTests(TidyFlowCase):
    def test_the_card_waits_beside_downloads_amber_and_nothing_moves_without_the_click(self) -> None:
        self.make_tidy()
        card = self.card()
        content = card.desk()
        self.assertEqual(content.kicker, f"MOVE FILES {DOT} NEEDS YOUR OK")
        self.assertEqual(content.title, "Tidy Downloads into your folders")
        self.assertEqual(content.tone, hud_overlay.TONE_AMBER)                  # a decision waits
        self.assertEqual([(row.name, row.to) for row in content.rows],
                         [(name, f"Documents {CRUMB} Uni {CRUMB} LING" if folder == LING else
                           f"Documents {CRUMB} Uni" if folder == UNI else "Documents") for name, folder in ROWS])
        self.assertEqual([(row.mark, row.ticked, row.struck) for row in content.rows],
                         [(hud_overlay.MARK_TICK, True, False)] * 5 + [(hud_overlay.MARK_TICK, False, True)])
        self.assertEqual(content.rows[0].flags, "UPLOADS TO ONEDRIVE")             # D3: flagged, in words
        self.assertEqual(content.rows[5].flags, f"UPLOADS TO ONEDRIVE {DOT} INSTALLER - STAYS UNLESS YOU TICK IT")
        self.assertEqual(content.note, "Nothing is deleted. Installers stay where they are. 5 go to OneDrive and "
                                       "will upload.")
        self.assertEqual([(button.key, button.text, button.enabled) for button in content.buttons],
                         [("dismiss", "Not now", True), ("go", "Move 5 files", True)])
        self.assertEqual(self.hud().caption_text(),                               # p2r-4: as the card counts them
                         "Moving 5 files needs your OK first - nothing has changed yet, sir.")
        self.assertEqual(card.cut_texts(), [])
        self.assertEqual(self.moves(), [])                                         # nothing until the click
        self.assertEqual(self.keys.arms, 0)                                        # Esc never held while it waits
        # Beside the File Explorer window showing Downloads: the cyan bracket, its tag and a tether.
        explorer = QRect(*EXPLORER_RECT)
        entry = self.entry()
        self.assertTrue(entry.found() and entry.following())
        layer = self.desktop.overlay.layer_for(QGuiApplication.primaryScreen())
        self.assertEqual(layer.brackets(), (hud_overlay.Bracket(explorer, hud_overlay.TONE_CYAN, "DOWNLOADS"),))
        self.assertEqual(len(layer.tethers()), 1)
        self.assertFalse(card.geometry().intersects(explorer))
        self.assertFalse(card.geometry().intersects(self.hud().occupied_rect()))
        self.assertEqual({hook[2] for hook in self.winapi.hooks.values()}, {61})   # Explorer's process only
        # It counts in "N need your OK" while it waits.
        pending = self.reading.pending_count()
        self.assertIn(hud_overlay.needs_ok_text(pending + 1), self.hud().chip_texts())
        self.assertEqual(self.desktop.waiting_count(), 1)
        # A tool window on top that never takes the focus; its ticks and buttons NoFocus.
        flags = card.windowFlags()
        for flag in (Qt.WindowType.Tool, Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowStaysOnTopHint,
                     Qt.WindowType.WindowDoesNotAcceptFocus):
            self.assertTrue(flags & flag)
        controls = [*card.buttons().values(), *card.ticks().values()]
        self.assertTrue(controls and all(control.focusPolicy() == Qt.FocusPolicy.NoFocus for control in controls))
        self.assertIn(int(card.winId()), self.native.excluded)
        self.native.keys.clear()
        focus = QApplication.focusWidget()
        self.tick(3)
        self.tick(3)
        self.assertEqual(self.native.keys, [])                                    # a tick activates nothing ...
        self.assertIs(QApplication.focusWidget(), focus)                          # ... and moves no focus

    def test_not_now_takes_the_card_away_and_carries_nothing_out(self) -> None:
        self.make_tidy()
        self.press("dismiss", wait=False)
        self.assertIsNone(self.entry())
        self.assertIsNone(self.desktop.tidy())
        self.assertEqual(self.desktop.overlay.cards(), [])
        self.assertEqual(self.moves(), [])
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertEqual(self.winapi.live_hooks(), 0)

    def test_untick_all_says_nothing_ticked_and_the_button_does_nothing(self) -> None:
        self.make_tidy()
        for number in range(1, 6):
            self.tick(number)
        go = self.card().buttons()["go"]
        self.assertEqual((go.text(), go.isEnabled()), ("Nothing ticked", False))
        QTest.mouseClick(go, Qt.MouseButton.LeftButton)
        settle()
        self.assertEqual(self.desktop.tidy().stage, dt.WAITING)
        self.assertEqual(self.moves(), [])
        self.tick(6)                                                               # the installer, ticked by hand
        self.assertEqual(self.card().buttons()["go"].text(), "Move 1 file")
        self.assertNotIn("Installers stay", self.card().desk().note)

    def test_a_card_left_waiting_past_its_plans_expiry_stops_waiting(self) -> None:
        """p2r-15: a single shot at the plan's end takes the card out of waiting - the expired stamp's own words, no
        commit button, not amber - and the chip and Approvals' reminder drop it; nothing moves."""
        with mock.patch.object(cp, "CARD_TTL", timedelta(seconds=3)):
            self.make_tidy()
            self.assertEqual(self.desktop.waiting_count(), 1)
            self.assertFalse(self.c._desktop_waiting.isHidden())
            self.assertIn("3 need your OK", self.hud().chip_texts())                # with the 2 approvals
            self.assertTrue(wait_for(lambda: not self.desktop.tidy().waiting(), timeout=8.0), "it stopped waiting")
        settle()
        content = self.card().desk()
        self.assertTrue(content.kicker.startswith(f"NOTHING MOVED {DOT} "), content.kicker)
        self.assertEqual(content.tone, hud_overlay.TONE_CORAL)
        self.assertEqual(content.buttons, ())
        self.assertEqual(content.note, "This card is more than 15 minutes old, so nothing was moved. Ask again for "
                                       "a fresh one.")
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertTrue(self.c._desktop_waiting.isHidden())
        self.assertIn("2 need your OK", self.hud().chip_texts())                    # the approvals only
        self.assertEqual(self.moves(), [])
        self.assertFalse(self.desktop._expiry_timer.isActive())                # nothing more to wait for

    def test_leaving_the_hud_keeps_the_waiting_card_as_data_and_approvals_reminds(self) -> None:
        self.make_tidy()
        self.back()
        self.assertEqual(self.desktop.overlay.cards(), [])                       # no card window ...
        self.assertEqual(self.winapi.live_hooks(), 0)                            # ... and no hook in the window
        self.assertTrue(self.desktop.tidy().waiting())
        # p2r-5: in the window the card counts as the HUD counted it - Home's chip and the Dock's Approvals badge.
        total = self.reading.pending_count() + 1
        self.assertEqual(self.reading.dock.button(dock.APPROVALS).count(), total)
        self.reading.set_new_briefing(False)                                      # Home's chip slot is free
        settle()
        self.assertTrue(self.reading.approvals_chip.isVisibleTo(self.reading))
        self.assertEqual(self.reading.approvals_chip.text(), ui.needs_ok_chip(total))
        self.reading.set_tab(ui.SCREEN_APPROVALS)
        settle()
        row = self.c._desktop_waiting
        self.assertTrue(row.isVisible())
        self.assertEqual(row.label.text(), "Waiting on your desktop")
        QTest.mouseClick(row.show_me, Qt.MouseButton.LeftButton)                 # "Show me": onto the HUD
        settle()
        self.assert_on_hud()
        self.assertEqual(self.kicker(), f"MOVE FILES {DOT} NEEDS YOUR OK")
        self.assertEqual(self.moves(), [])


class RunTests(TidyFlowCase):
    def test_approve_moves_every_ticked_row_on_the_desktop_thread_then_the_stamp(self) -> None:
        self.make_tidy()
        self.native.keys.clear()
        focus = QApplication.focusWidget()
        with self.assertLogs("briefing_reader", level="INFO") as logs:
            self.press("go")
        for number in range(1, 6):
            old, new = self.where(number)
            self.assertFalse(self.there(old))
            self.assertTrue(self.there(new))
        self.assertTrue(self.there(self.where(6)[0]))                              # the installer stayed
        self.assertEqual(len(self.moves()), 5)
        self.assert_on_the_desktop_thread()                                        # never the calendar's worker
        self.assertEqual((self.keys.arms, self.keys.disarms, self.keys.armed()), (1, 1, False))
        self.assertEqual(set(self.keys.threads), {"desktop"})
        content = self.card().desk()
        self.assertTrue(content.kicker.startswith(f"MOVED 5 FILES {DOT} "), content.kicker)
        self.assertEqual(content.tone, hud_overlay.TONE_DONE)
        self.assertEqual([row.mark for row in content.rows], [hud_overlay.MARK_DONE] * 5 + [hud_overlay.MARK_IDLE])
        self.assertEqual(content.link, ("undo", "Undo"))
        self.assertEqual(content.link_note, "Puts every file back where it was.")
        self.assertEqual(content.buttons, ())
        self.assertEqual(self.card().cut_texts(), [])
        self.assertEqual(self.hud().caption_text(), "Moved 5 files, sir.")
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertEqual(self.native.keys, [])                                     # the click activated nothing
        self.assertIs(QApplication.focusWidget(), focus)
        journal = jn.Journal(self.journal_dir, self.desktop.tidy().card.run_id)
        self.assertEqual(sorted(item.state for item in journal.items().values()), [jn.DONE] * 5)
        task = self.tasks()[-1]
        self.assertEqual(task.title, "Move 5 files - your click on a NEEDS YOUR OK card")
        fields = {field.label: field.value for field in task.steps[0].fields}
        self.assertTrue(fields["Approved"].startswith("Approved by your click "))
        self.assertEqual([item.text for item in task.steps[1].items],
                         [f"Row {number} - move - done" for number in range(1, 6)])
        text = "\n".join(logs.output)
        self.assertRegex(text, r"Desktop \w+: done 5/5, skipped 0, failed 0, \d+\.\d s")
        for name in LOGGED_NAMES:
            self.assertNotIn(name, text)

    def test_partial_untick_moves_only_the_rows_left_ticked(self) -> None:
        self.make_tidy()
        self.tick(2)
        self.tick(4)
        self.assertEqual(self.card().buttons()["go"].text(), "Move 3 files")
        self.assertEqual([row.struck for row in self.rows()], [False, True, False, True, False, True])
        self.press("go")
        self.assertEqual([self.there(self.where(number)[1]) for number in range(1, 7)],
                         [True, False, True, False, True, False])
        self.assertEqual([self.there(self.where(number)[0]) for number in (2, 4, 6)], [True, True, True])
        self.assertTrue(self.kicker().startswith(f"MOVED 3 FILES {DOT} "))
        self.assertEqual([row.mark for row in self.rows()],
                         [hud_overlay.MARK_DONE, hud_overlay.MARK_IDLE, hud_overlay.MARK_DONE, hud_overlay.MARK_IDLE,
                          hud_overlay.MARK_DONE, hud_overlay.MARK_IDLE])

    def test_the_cards_stop_mid_batch_stops_after_the_row_on_its_way(self) -> None:
        self.make_tidy()
        holding, release = self.hold_at_move(2)
        self.press("go", wait=False)
        self.assertTrue(wait_for(holding.is_set))
        self.assertTrue(wait_for(lambda: self.kicker() == f"MOVING 2 OF 5{dt.ELLIPSIS}"))
        self.assertEqual(self.card().desk().tone, hud_overlay.TONE_CYAN)          # not amber: no decision waits
        self.assertTrue(self.keys.armed())                                         # Esc held while it acts
        self.assertEqual(self.rows()[0].mark, hud_overlay.MARK_DONE)
        self.press("stop", wait=False)
        self.assertEqual(self.kicker(), f"STOPPING AFTER THIS ONE{dt.ELLIPSIS}")
        release.set()
        self.assertTrue(wait_for(lambda: not self.desktop.busy()))
        settle()
        self.assertEqual(len(self.moves()), 2)                                     # the one on its way finished
        self.assertTrue(self.kicker().startswith(f"STOPPED BY YOU {DOT} MOVED 2 OF 5 FILES {DOT} "), self.kicker())
        self.assertEqual([row.words for row in self.rows()[2:5]], ["Not done - you stopped it."] * 3)
        self.assertEqual(self.hud().caption_text(), "Stopped, sir - moved 2 of 5.")
        self.assertFalse(self.keys.armed())
        task = self.tasks()[-1]
        stop = next(step for step in task.steps if step.title == "Stopped")
        self.assertEqual({field.label: field.value for field in stop.fields}["Why"], "Stop on the card")

    def test_a_fast_batchs_rows_refresh_the_card_at_most_once_a_tick(self) -> None:
        """p2r-13: rows that end in a burst refresh the card once (their last state shows), not once a row - and a row
        that ends after the wait shows at once."""
        self.make_tidy()
        holding, release = self.hold_at_move(1)
        self.press("go", wait=False)
        self.assertTrue(wait_for(holding.is_set))
        settle()
        seq = self.desktop._tidy_job[0]
        results = self.desktop.results
        with (mock.patch.object(desktop_ui, "ROW_REFRESH_S", 0.5),
              mock.patch.object(results, "refresh_tidy", wraps=results.refresh_tidy) as refresh):
            self.desktop._row_shown = time.monotonic()
            for number in (1, 2, 3):
                self.desktop._on_tidy_row(seq, dt.RowEnd(number, jn.DONE), number, 5)
            self.assertEqual(refresh.call_count, 0)                               # the burst: not yet
            self.assertTrue(wait_for(lambda: refresh.call_count == 1))
            self.assertEqual(self.kicker(), f"MOVING 4 OF 5{dt.ELLIPSIS}")         # the last state shows
            QTest.qWait(550)
            self.desktop._on_tidy_row(seq, dt.RowEnd(4, jn.DONE), 4, 5)
            self.assertEqual(refresh.call_count, 2)                               # after the wait: at once
        release.set()
        self.assertTrue(wait_for(lambda: not self.desktop.busy()))
        settle()
        self.assertTrue(self.kicker().startswith(f"MOVED 5 FILES {DOT} "), self.kicker())

    def test_esc_mid_batch_stops_it_and_is_held_only_while_it_acts(self) -> None:
        self.make_tidy()
        self.assertFalse(self.keys.press())                                        # at idle Esc is not Jarvis's
        holding, release = self.hold_at_move(3)
        self.press("go", wait=False)
        self.assertTrue(wait_for(holding.is_set))
        self.assertTrue(self.keys.press())                                         # the owner's Esc, anywhere
        self.assertTrue(wait_for(lambda: self.kicker() == f"STOPPING AFTER THIS ONE{dt.ELLIPSIS}"))
        release.set()
        self.assertTrue(wait_for(lambda: not self.desktop.busy()))
        settle()
        self.assertEqual(len(self.moves()), 3)
        self.assertTrue(self.kicker().startswith(f"STOPPED BY YOU {DOT} MOVED 3 OF 5 FILES"))
        self.assertEqual((self.keys.arms, self.keys.disarms, self.keys.armed()), (1, 1, False))
        self.assertFalse(self.keys.press())                                        # let go after the batch
        task = self.tasks()[-1]
        stop = next(step for step in task.steps if step.title == "Stopped")
        fields = {field.label: field.value for field in stop.fields}
        self.assertEqual(fields["Why"], "Esc")
        self.assertRegex(fields["Took"], r"^\d+ ms to stop$")

    def test_turn_off_mid_run_stops_at_the_next_row_and_the_card_goes(self) -> None:
        self.make_tidy()
        holding, release = self.hold_at_move(2)
        self.press("go", wait=False)
        self.assertTrue(wait_for(holding.is_set))
        self.assertTrue(self.desktop.turn_off())
        release.set()
        self.assertTrue(wait_for(lambda: not self.desktop.busy()))
        settle()
        self.assertEqual(len(self.moves()), 2)                                     # nothing after the Turn off
        self.assertTrue(self.there(self.where(3)[0]))
        self.assertIsNone(self.desktop.tidy())
        self.assertIsNone(self.entry())
        self.assertFalse(self.keys.armed())
        # p2r-2: what was done is still told - the caption, LIVE's task - with no card and no Undo while turned off.
        self.assertEqual(self.hud().caption_text(),
                         "Stopped - you turned Jarvis on your desktop off. 2 of 5 files were moved.")
        task = self.tasks()[-1]
        self.assertEqual(task.title, "Move 5 files - your click on a NEEDS YOUR OK card")
        self.assertTrue({field.label: field.value for field in task.steps[0].fields}["Approved"].startswith(
            "Approved by your click "))
        self.assertEqual([item.text for item in task.steps[1].items][:2],
                         ["Row 1 - move - done", "Row 2 - move - done"])
        stop = next(step for step in task.steps if step.title == "Stopped")
        self.assertEqual({field.label: field.value for field in stop.fields}["Why"], "Turned off")
        self.assertTrue(self.desktop.turn_on())                                    # on again: "undo" brings it
        self.type_on_hud("undo")
        self.answered()
        settle()
        content = self.card().desk()
        self.assertEqual([row.name for row in content.rows], [ROWS[0][0], ROWS[1][0]])
        self.assertEqual(content.link, ("undo", "Undo"))

    def test_an_expired_plan_is_refused_in_plain_words_and_nothing_moves(self) -> None:
        self.make_tidy()
        self.desktop._service._now = lambda: datetime.now(timezone.utc) + timedelta(minutes=16)
        self.press("go")
        self.assertEqual(self.moves(), [])
        self.assertEqual(self.keys.arms, 0)                                        # nothing acted: Esc never held
        content = self.card().desk()
        self.assertTrue(content.kicker.startswith(f"NOTHING MOVED {DOT} "))
        self.assertEqual(content.tone, hud_overlay.TONE_CORAL)                     # a refusal: coral
        self.assertEqual(content.note, "This card is more than 15 minutes old, so nothing was moved. Ask again for "
                                       "a fresh one.")
        self.assertEqual(self.hud().caption_text(),
                         "Nothing was moved, sir - the card was more than 15 minutes old.")

    def test_a_click_too_soon_or_while_locked_runs_nothing(self) -> None:
        self.make_tidy()
        self.c._decision_clicked_at = self.clock[0] - 0.5                          # another decision 0.5 s ago
        self.press("go", wait=False)
        self.assertEqual(self.desktop.tidy().stage, dt.WAITING)
        self.assertEqual(self.moves(), [])
        self.later()
        self.c._connect_alias = "personal"                                         # a sign-in locks the cards
        self.press("go", wait=False)
        self.assertEqual(self.desktop.tidy().stage, dt.WAITING)
        self.c._connect_alias = None
        self.later()
        self.press("go")
        self.assertEqual(len(self.moves()), 5)


class UndoTests(TidyFlowCase):
    def test_undo_after_a_change_puts_back_the_rest_with_the_partial_stamp(self) -> None:
        self.make_tidy()
        self.press("go")
        self.later()
        moved_away = DOCS + "\\elsewhere.pdf"                                      # row 1 moved since
        os.rename(self.fake.real(self.where(1)[1]), self.fake.real(moved_away))
        self.fake.make_file(self.where(2)[0], b"new")                              # something new in row 2's place
        with self.assertLogs("briefing_reader", level="INFO") as logs:
            self.press("undo")
        for number in (3, 4, 5):
            old, new = self.where(number)
            self.assertTrue(self.there(old))
            self.assertFalse(self.there(new))
        self.assertTrue(self.there(moved_away) and self.there(self.where(2)[1]))   # left as they are
        content = self.card().desk()
        self.assertTrue(content.kicker.startswith(
            f"PUT BACK 3 OF 5 {DOT} 1 MOVED SINCE {DOT} 1 SOMETHING NEW IN ITS PLACE"), content.kicker)
        self.assertEqual(content.tone, hud_overlay.TONE_CORAL)
        self.assertEqual([row.words for row in content.rows[:5]],
                         ["Moved since - left where it is.", "Something new is in its old place - left where it is.",
                          "Put back.", "Put back.", "Put back."])
        self.assertEqual(content.note, "2 were left as they are - see why above.")
        self.assertEqual(self.card().cut_texts(), [])
        self.assertIsNone(content.link)
        self.assertEqual(self.hud().caption_text(), "Put back 3 of 5, sir - see the card for the rest.")
        self.assertEqual((self.keys.arms, self.keys.disarms), (2, 2))               # Esc held for the Undo too
        undo = self.tasks()[-1]
        self.assertEqual(undo.title, "Undo - your click on the card's stamp")
        self.assertEqual(sorted(item.text for item in undo.steps[1].items),
                         ["Row 1 - move - left: moved_since", "Row 2 - move - left: occupied",
                          "Row 3 - move - put back", "Row 4 - move - put back", "Row 5 - move - put back"])
        text = "\n".join(logs.output)
        self.assertRegex(text, r"Desktop \w+: put back 3/5, left 2, \d+\.\d s")
        for name in LOGGED_NAMES:
            self.assertNotIn(name, text)

    def test_undo_puts_everything_back_newest_first(self) -> None:
        self.make_tidy()
        self.press("go")
        self.later()
        self.fake.calls.clear()
        self.press("undo")
        self.assertEqual([self.there(self.where(number)[0]) for number in range(1, 7)], [True] * 6)
        backs = [argument for method, argument in self.fake.calls if method == "move_item"]
        self.assertEqual(backs, [self.where(number)[1] for number in (5, 4, 3, 2, 1)])    # reverse order
        content = self.card().desk()
        self.assertTrue(content.kicker.startswith(f"PUT BACK 5 OF 5 {DOT} "), content.kicker)
        self.assertEqual(content.tone, hud_overlay.TONE_DONE)
        self.assertEqual(content.note, "Everything is back as it was.")
        self.assertEqual(self.hud().caption_text(), "Put back, sir. Everything is where it was.")

    def test_undo_is_offered_for_undo_days_only(self) -> None:
        self.make_tidy()
        self.press("go")
        self.later()
        self.desktop.tidy().at = datetime.now() - timedelta(days=8)              # [desktop] undo_days: 7
        self.fake.calls.clear()
        self.press("undo")
        self.assertEqual(self.moves(), [])
        self.assertIsNone(self.card().desk().link)
        self.assertEqual(self.hud().caption_text(), "That was more than 7 days ago, sir - Undo isn't offered any more.")

    def test_undo_is_the_owners_click_only_and_passes_the_guards(self) -> None:
        self.make_tidy()
        self.press("go")
        self.press("undo", wait=False)                                             # within 1 s of the approval
        self.assertEqual(self.desktop.tidy().stage, dt.DONE)
        self.assertTrue(all(self.there(self.where(number)[1]) for number in range(1, 6)))
        self.later()
        self.press("undo")
        self.assertEqual(self.desktop.tidy().stage, dt.UNDONE)


class UpdatedCardTidyTests(TidyFlowCase):
    """p2r-6: under the OK of Phase 1's card (version 1) a planner's file steps are refused in the run itself - no
    NEEDS YOUR OK card, nothing moves - in plain words, and the updated card is offered; a card's click likewise."""

    def test_phase_1_ok_refuses_the_planners_file_steps_and_offers_the_updated_card(self) -> None:
        self.make_tidy(consent=1)
        self.assertEqual(len(self.runs()), 1)                                     # planning is Phase 1's
        self.assertIsNone(self.entry())                                           # no amber card at all
        self.assertIsNone(self.desktop.tidy())
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertEqual(self.moves(), [])
        self.assertEqual(self.hud().caption_text(),
                         "That needs your OK on the updated card first - Bring Jarvis back to read it, sir.")
        self.assertEqual(self.c._desktop_consent_later, desktop_ui.ENTRY_UPDATE)  # shown at the way back
        self.back()
        settle()
        self.assertTrue(self.c._desktop_consent_card.is_open())

    def test_a_waiting_cards_click_under_phase_1s_ok_moves_nothing(self) -> None:
        self.make_tidy()
        self.assertTrue(self.c._prefs_store().set_desktop_turned_on(True, version=1))  # noqa: SLF001 - as stored
        self.press("go")
        self.assertEqual(self.moves(), [])
        self.assertEqual(self.desktop.tidy().stage, dt.WAITING)                  # still waits for a click
        self.assertEqual(self.hud().caption_text(), desktop_ui.UPDATE_NEEDED_HUD_TEXT.format(sir="sir"))
        self.assertEqual(self.c._desktop_consent_later, desktop_ui.ENTRY_UPDATE)


class KeyboardPathTests(TidyFlowCase):
    """p2r-10 (SPEC 8.1's keyboard path): while the HUD's bar has the keyboard, Tab moves into the newest card - its
    window drops WindowDoesNotAcceptFocus for that time and its ticks and buttons take Tab; Space or Enter clicks the
    focused one; Esc or Shift+Tab gives the keyboard back to the bar. Never as a card shows: only after his Tab."""

    def accepts_focus(self, card: hud_overlay.DesktopCard) -> bool:
        return not card.windowHandle().flags() & Qt.WindowType.WindowDoesNotAcceptFocus

    def bar_keyboard(self) -> None:
        QTest.mouseClick(self.hud().bar.input, Qt.MouseButton.LeftButton)       # a click in the bar: it has it
        settle()
        self.assertTrue(self.hud().holds_keyboard())

    def tab(self) -> None:
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        settle()

    def test_tab_into_the_card_space_and_enter_click_and_esc_gives_it_back(self) -> None:
        self.make_tidy()
        card = self.card()
        self.assertFalse(self.accepts_focus(card) or card.has_keyboard())          # never on show
        self.bar_keyboard()
        self.tab()
        ticks, buttons = card.ticks(), card.buttons()
        self.assertTrue(card.has_keyboard() and self.accepts_focus(card))
        self.assertIs(QApplication.focusWidget(), ticks[1])                       # its first control
        self.assertTrue(self.hud().is_lit())                                      # the bar keeps the hold
        self.assertTrue(all(control.focusPolicy() == Qt.FocusPolicy.TabFocus for control in card.keyboard_controls()))
        QTest.keyClick(ticks[1], Qt.Key.Key_Space)                                # Space unticks row 1 ...
        settle()
        self.assertEqual(card.buttons()["go"].text(), "Move 4 files")
        QTest.keyClick(ticks[1], Qt.Key.Key_Return)                               # ... Enter ticks it again
        settle()
        self.assertEqual(card.buttons()["go"].text(), "Move 5 files")
        for _ in range(10):                                                       # Tab goes on to "Move 5 files"
            if QApplication.focusWidget() is buttons["go"]:
                break
            self.tab()
        self.assertIs(QApplication.focusWidget(), buttons["go"])
        self.assertEqual(self.moves(), [])
        QTest.keyClick(buttons["go"], Qt.Key.Key_Return)                          # Enter: the owner's click
        self.assertTrue(wait_for(lambda: len(self.moves()) == 5 and not self.desktop.busy()), "it ran")
        settle()
        self.assertTrue(self.kicker().startswith(f"MOVED 5 FILES {DOT} "), self.kicker())
        self.assertTrue(card.has_keyboard())
        self.assertIs(QApplication.focusWidget(), card.buttons()["undo"])         # the stamp's Undo now
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)             # Esc: back to the bar
        settle()
        self.assertFalse(card.has_keyboard() or self.accepts_focus(card))
        self.assertTrue(all(control.focusPolicy() == Qt.FocusPolicy.NoFocus for control in card.ticks().values()))
        self.assertEqual(card.buttons()["undo"].focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertTrue(self.hud().holds_keyboard())
        self.assertIs(QApplication.focusWidget(), self.hud().bar.input)

    def test_shift_tab_gives_it_back_and_going_elsewhere_lets_it_go(self) -> None:
        self.make_tidy()
        card = self.card()
        self.bar_keyboard()
        self.tab()
        self.assertTrue(card.has_keyboard())
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
        settle()
        self.assertFalse(card.has_keyboard())
        self.assertTrue(self.hud().holds_keyboard())
        self.tab()                                                                # in again ...
        self.assertTrue(card.has_keyboard())
        other = QWidget()                                                         # ... then the owner goes elsewhere
        self.addCleanup(other.deleteLater)
        other.show()
        other.activateWindow()
        settle()
        self.assertTrue(wait_for(lambda: not card.has_keyboard()), "the card let it go")
        self.assertFalse(self.hud().is_lit())                                     # and the bar too: nothing activated
        self.assertFalse(self.accepts_focus(card))
        other.hide()

    def test_tab_with_no_card_keeps_the_keyboard_in_the_bar(self) -> None:
        self.make_ask()
        self.enter()
        self.bar_keyboard()
        self.tab()
        self.assertTrue(self.hud().holds_keyboard())
        self.assertIs(QApplication.focusWidget(), self.hud().bar.input)


class UndoRouteTests(TidyFlowCase):
    """p2r-3 (D9's 7-day Undo): the stamp says Undo stays for undo_days and how to bring it back; "undo" (typed)
    shows the newest change within undo_days that Undo may still put back - after a new request took its card away,
    after a restart - rebuilt from its journal as its stamped card; only the owner's click on its Undo puts anything
    back, through the card's own Undo; past undo_days or with nothing left to put back it says so."""

    RECALL = "Here's your last change, sir - Undo on its card puts it back."
    NOTHING = "Nothing to undo from the last 7 days, sir."

    def moved_five(self) -> None:
        self.make_tidy()
        self.press("go")
        self.assertTrue(self.kicker().startswith(f"MOVED 5 FILES {DOT} "))

    def ask(self, words: str) -> str:
        self.type_on_hud(words)
        self.answered()
        settle()
        return self.hud().caption_text()

    def assert_moved(self, there: bool) -> None:
        for number in range(1, 6):
            old, new = self.where(number)
            self.assertEqual((self.there(new), self.there(old)), (there, not there), number)

    def test_the_stamp_says_how_long_undo_stays(self) -> None:
        self.moved_five()
        content = self.card().desk()
        self.assertEqual(content.note, 'Undo stays for 7 days - type "undo" to bring this back.')
        self.assertEqual(content.link, ("undo", "Undo"))
        self.assertEqual(self.card().cut_texts(), [])

    def test_after_a_new_request_undo_brings_the_card_back_and_only_its_click_puts_back(self) -> None:
        self.moved_five()
        self.ask("open Downloads")                                                 # a new request: the card goes
        self.assertIsNone(self.entry())
        self.assertEqual(self.ask("undo"), self.RECALL)
        content = self.card().desk()
        self.assertTrue(content.kicker.startswith(f"MOVED 5 FILES {DOT} "), content.kicker)
        self.assertEqual([row.name for row in content.rows], [name for name, _folder in ROWS[:5]])
        self.assertEqual(content.link, ("undo", "Undo"))
        self.assertEqual(content.tone, hud_overlay.TONE_DONE)
        self.assertEqual(self.card().cut_texts(), [])
        self.assert_moved(True)                                                    # the words put nothing back
        self.assertEqual(self.desktop.waiting_count(), 0)                          # no decision waits: not amber
        self.later()
        self.press("undo")                                                         # his click: the card's Undo
        self.assert_moved(False)
        self.assertTrue(self.kicker().startswith(f"PUT BACK 5 OF 5 {DOT} "), self.kicker())
        self.assertEqual(self.ask("undo"), self.NOTHING)                           # all of it back now
        self.assertIsNone(self.entry())

    def test_after_a_restart(self) -> None:
        self.moved_five()
        fake = self.fake
        self.harness.close()
        self.make(runs=(), desktop_backend=lambda: fake, desktop_window_api=self.winapi)    # Jarvis again
        self.fake = fake
        self.c.ask.start()
        self.assertTrue(wait_for(lambda: self.c.ask.ready is not None))
        self.desktop.journal_folder = lambda: self.journal_dir
        self.desktop.stop_keys = self.keys
        self.c._click_clock = lambda: self.clock[0]
        self.enter()
        self.assertEqual(self.ask("undo that"), self.RECALL)
        self.assertEqual(len(self.card().desk().rows), 5)
        self.press("undo")
        self.assert_moved(False)

    def test_past_undo_days_nothing_and_a_days_old_change_shows_its_date(self) -> None:
        self.make_ask()
        self.desktop.journal_folder = lambda: self.journal_dir
        self.desktop.stop_keys = self.keys
        self.enter()
        now = time.time()

        def ran(run_id: str, days: float, name: str) -> None:
            moved = LING + "\\" + name
            self.fake.make_file(moved, b"x" * 10)
            record = jn.Record(1, jn.MOVE, jn.PLANNED, src=DOWNLOADS + "\\" + name, dst=moved)
            journal = jn.Journal(self.journal_dir, run_id, clock=lambda: now - days * 86_400)
            done = dataclasses.replace(record, state=jn.DONE).with_identity(self.fake.identity(moved))
            journal.extend([record, done])

        ran("old8", 8, "Old notes.pdf")                                            # past undo_days
        self.assertEqual(self.ask("undo"), self.NOTHING)
        self.assertIsNone(self.entry())
        ran("three", 3, "Three days.pdf")
        self.assertEqual(self.ask("undo"), self.RECALL)
        stamp = datetime.fromtimestamp(now - 3 * 86_400)
        self.assertIn(f"{stamp.day} {stamp.strftime('%b').upper()}, ", self.kicker().upper())   # its date too
        self.assertEqual([row.name for row in self.card().desk().rows], ["Three days.pdf"])


class HousekeepingTests(TidyFlowCase):
    """p2r-1: the journal's housekeeping on the desktop thread (desktop.service.housekeep) - at start-up once turned
    on, at the owner's Turn on and at the first desktop request of a new day; never before the Turn on. Recovery only
    corrects records (SPEC 6.1: never redo)."""

    FILE_OPS = ("move_item", "copy_file", "make_folder", "remove_folder", "write_zip")

    def seed(self) -> None:
        """An old run (31 days) and a move a crash left "doing" - it had moved (its file id is at its destination)."""
        now = time.time()
        jn.Journal(self.journal_dir, "old1", clock=lambda: now - 31 * 86_400).append(jn.Record(1, jn.MOVE, jn.DONE))
        source, there = DOWNLOADS + "\\" + NAMES[0], LING + "\\" + NAMES[0]
        self.fake.make_file(there, b"x" * 100)
        jn.Journal(self.journal_dir, "crash1").append(
            jn.Record(1, jn.MOVE, jn.DOING, src=source, dst=there).with_identity(self.fake.identity(there)))

    def healed(self) -> bool:
        item = jn.Journal(self.journal_dir, "crash1").items().get(1)
        return (item is not None and item.state == jn.DONE
                and not os.path.exists(os.path.join(self.journal_dir, "old1.jsonl")))

    def assert_looked_only(self) -> None:
        self.assertEqual(jn.Journal(self.journal_dir, "crash1").items()[1].reason_code, jn.R_RECOVERED)
        self.assertEqual([method for method, _argument in self.fake.calls if method in self.FILE_OPS], [])
        self.assertEqual(set(self.fake.threads), {"desktop"})

    def test_at_start_up_a_doing_record_is_reconciled_and_a_31_day_journal_purged(self) -> None:
        with mock.patch.object(jn, "default_folder", return_value=self.journal_dir):
            with self.assertLogs("briefing_reader", level="INFO") as logs:
                self.make_ask(prepare=lambda _config: self.seed())
                self.assertTrue(wait_for(self.healed), "housekept at start-up")
        self.assert_looked_only()
        self.assertTrue(os.path.exists(os.path.join(self.journal_dir, "crash1.jsonl")))
        text = "\n".join(line for line in logs.output if ":Desktop" in line)
        self.assertIn("the journal's housekeeping goes to the desktop thread (start)", text)
        self.assertIn("recovered 1 item(s) in 2 run(s) (done=1)", text)
        self.assertIn("purged 1 run(s) older than 30 days", text)
        for name in LOGGED_NAMES:
            self.assertNotIn(name, text)

    def test_at_the_turn_on_and_never_before(self) -> None:
        self.make_ask(desktop_consent=False, prepare=lambda _config: self.seed())
        self.desktop.journal_folder = lambda: self.journal_dir
        settle()
        self.assertIsNone(self.desktop._worker)                              # not turned on: no desktop thread
        self.assertFalse(self.desktop.request("open my LING essay"))
        self.assertIsNone(self.desktop._worker)
        self.assertTrue(os.path.exists(os.path.join(self.journal_dir, "old1.jsonl")))
        self.assertEqual(jn.Journal(self.journal_dir, "crash1").items()[1].state, jn.DOING)
        self.assertTrue(self.desktop.turn_on())
        self.assertTrue(wait_for(self.healed), "housekept at the Turn on")
        self.assert_looked_only()

    def test_again_at_the_first_request_of_a_new_day_only(self) -> None:
        self.make_ask()
        self.desktop.journal_folder = lambda: self.journal_dir
        self.desktop._worker.submit(lambda: None).result(timeout=5)         # noqa: SLF001 - the start-up's ended
        self.seed()
        self.assertTrue(self.desktop.request("open my LING essay", planner=False))
        self.answered()
        self.assertFalse(self.healed())                                      # the same day: not again
        self.desktop._housekept = date.today() - timedelta(days=1)          # midnight has passed since
        self.assertTrue(self.desktop.request("open my LING essay", planner=False))
        self.answered()
        self.assertTrue(wait_for(self.healed), "housekept at the day's first request")
        self.assert_looked_only()
        self.assertEqual(self.desktop._housekept, date.today())


if __name__ == "__main__":
    unittest.main()
