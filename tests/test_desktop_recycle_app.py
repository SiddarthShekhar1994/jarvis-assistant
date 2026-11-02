"""Jarvis on your desktop, P2.6 in the app: the tidy card's Recycle Bin variant, its run and its Undo, and the Undo of
Jarvis's own copies and screenshots through the bin (SPEC 2.1, 4.5, 6.2, 6.3, 10.3 and 16 P2.6; hard rules 1, 4, 5,
12, 13 and 14).

"delete the old copies ... from Downloads" typed on the HUD goes to the planner (tests.ask_fakes: no real run) and comes
back as ONE amber card - TO THE RECYCLE BIN - beside the File Explorer window showing Downloads; nothing is binned
until the owner's click on "Move 3 files to the Recycle Bin"; then exactly the ticked rows go to the bin on the
desktop thread (the Recycle Bin's Windows side is tests.desktop_fakes.FakeRecycleBin over the fake backend's pretend
disk - the real one is never made), the stamp says so, and Undo takes them back out of the bin - plainly saying what it
can't ("Can't be put back - the Recycle Bin was emptied.", "Something new is in its old place - left where it is.").
A row Windows didn't put in the bin stops the batch. "tidy my Downloads" never bins anything, whatever the planner
says. A copy card's Undo bins Jarvis's own copies; a screenshot chip's Undo bins the screenshot. Every name is
invented.
"""

from __future__ import annotations

import threading
import unittest
from typing import Any

from PySide6.QtWidgets import QApplication

from briefing_reader import hud_overlay
from briefing_reader.desktop.context import FileItem
from tests.ask_fakes import plan_stream
from tests.desktop_fakes import DOCS, DOWNLOADS, FakeRecycleBin, desktop_context
from tests.test_desktop_focus_app import FocusApp
from tests.test_desktop_tidy_app import EXPLORER, EXPLORER_EXE, EXPLORER_RECT, TidyFlowCase
from tests.ui_fakes import fonts, settle, wait_for

DOT = chr(0xB7)
NAMES = ("budget_oct (old).xlsx", "IMG_2041 (copy).jpg", "setup_zoom_v6.exe")
WORDS = "delete the old copies and the zoom installer from Downloads"
BIN_PLAN = {"say": "I'd bin three files.",
            "steps": [{"skill": "recycle_files", "why": "x", "files": ["f1", "f2", "f3"]}]}
LOGGED = ("budget_oct", "IMG_2041", "setup_zoom", "Downloads", "Owner", "C:\\")

_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_desktop_recycle_app", "-platform", "offscreen"])
    fonts()


class BinFlowCase(TidyFlowCase):
    """TidyFlowCase with three old files in the pretend Downloads and the Recycle Bin's Windows side a FakeRecycleBin
    over the pretend disk (``bin``)."""

    def make_bin(self, plan: dict | None = None, words: str = WORDS, names: tuple[str, ...] = NAMES) -> None:
        self.make_ask(plan_stream(plan or BIN_PLAN), desktop_window_api=self.winapi)
        self.bin = FakeRecycleBin(self.fake)
        self.desktop.recycle_factory = lambda: self.bin
        files = []
        for name in names:
            path = DOWNLOADS + "\\" + name
            self.fake.make_file(path, b"x" * 100)
            files.append(FileItem(path, name, DOWNLOADS, size=100))
        context = desktop_context(files=files, backend=self.fake)
        self.desktop.context_source = lambda _words: context
        self.desktop.journal_folder = lambda: self.journal_dir
        self.desktop.stop_keys = self.keys
        self.c._click_clock = lambda: self.clock[0]
        self.winapi.add(EXPLORER, pid=61, exe=EXPLORER_EXE, rect=EXPLORER_RECT, folder=DOWNLOADS)
        self.enter()
        self.type_on_hud(words)
        self.finish()
        settle()

    def path(self, number: int) -> str:
        return DOWNLOADS + "\\" + NAMES[number - 1]


class BinCardTests(BinFlowCase):
    def test_the_card_waits_amber_in_the_bins_words_and_nothing_is_binned_without_the_click(self) -> None:
        self.make_bin()
        card = self.card()
        desk = card.desk()
        self.assertEqual(desk.kicker, f"TO THE RECYCLE BIN {DOT} NEEDS YOUR OK")
        self.assertEqual((desk.title, desk.tone), ("Move these files to the Recycle Bin", hud_overlay.TONE_AMBER))
        self.assertEqual([(row.name, row.to, row.struck) for row in desk.rows],
                         [(name, "Recycle Bin", False) for name in NAMES])
        self.assertEqual([button.text for button in desk.buttons], ["Not now", "Move 3 files to the Recycle Bin"])
        self.assertEqual(desk.note, "You can get them back from the Recycle Bin, or with Undo.")
        self.assertEqual(card.cut_texts(), [])
        self.assertEqual(self.bin.asked_to_bin(), [])                     # nothing before his click
        self.assertTrue(all(self.there(self.path(n)) for n in (1, 2, 3)))
        self.assertIn(hud_overlay.needs_ok_text(self.reading.pending_count() + 1), self.hud().chip_texts())
        self.assertEqual(self.hud().caption_text(),
                         "Moving 3 files to the Recycle Bin needs your OK first - nothing has changed yet, sir.")

    def test_the_click_bins_the_ticked_rows_on_the_desktop_thread_and_undo_takes_them_out(self) -> None:
        self.make_bin()
        self.tick(2)                                                     # untick the photo
        self.assertEqual(self.card().desk().buttons[1].text, "Move 2 files to the Recycle Bin")
        self.later()
        with self.assertLogs("briefing_reader", "INFO") as logs:
            self.press("go")
        self.assertEqual(self.bin.recycled, [self.path(1), self.path(3)])
        self.assertTrue(self.there(self.path(2)))
        self.assertEqual({thread for (method, _a), thread in zip(self.fake.calls, self.fake.threads)
                          if method in ("drive_type", "file_id")}, {"desktop"})
        desk = self.card().desk()
        self.assertTrue(desk.kicker.startswith(f"BINNED 2 FILES {DOT} "))
        self.assertEqual((desk.tone, desk.link, desk.link_note),
                         (hud_overlay.TONE_DONE, ("undo", "Undo"), "Puts every file back where it was."))
        self.assertEqual(self.hud().caption_text(), "Moved 2 files to the Recycle Bin, sir. Undo puts them back.")
        self.later()
        self.press("undo")
        self.assertTrue(all(self.there(self.path(n)) for n in (1, 2, 3)))
        desk = self.card().desk()
        self.assertTrue(desk.kicker.startswith("PUT BACK 2 OF 2"))
        self.assertEqual(self.hud().caption_text(), "Put back, sir. Everything is where it was.")
        for name in LOGGED:
            self.assertNotIn(name, "\n".join(logs.output))

    def test_undo_says_what_the_bin_lost_and_what_is_in_the_old_place(self) -> None:
        self.make_bin()
        self.later()
        self.press("go")
        self.bin.clear_bin(self.path(1))                                 # the owner emptied that one from the bin
        self.fake.make_file(self.path(2), b"new")                        # and saved something new in another's place
        self.later()
        self.press("undo")
        desk = self.card().desk()
        self.assertEqual(desk.kicker, f"PUT BACK 1 OF 3 {DOT} 1 BIN EMPTIED {DOT} 1 SOMETHING NEW IN ITS PLACE")
        self.assertEqual(desk.tone, hud_overlay.TONE_CORAL)
        self.assertEqual([row.words for row in desk.rows],
                         ["Can't be put back - the Recycle Bin was emptied.",
                          "Something new is in its old place - left where it is.", "Put back."])
        self.assertEqual(self.fake.identity(self.path(2)).size, 3)        # never over the new one
        self.assertTrue(self.there(self.path(3)))

    def test_a_row_windows_didnt_bin_stops_the_batch(self) -> None:
        self.make_bin()
        seen = [0]
        original = self.bin.recycle_item

        def second_nuked(path: str) -> Any:
            seen[0] += 1
            self.bin.nuke = seen[0] == 2
            return original(path)

        self.bin.recycle_item = second_nuked                             # type: ignore[method-assign]
        self.later()
        self.press("go")
        self.assertEqual(self.bin.asked_to_bin(), [self.path(1), self.path(2)])
        self.assertTrue(self.there(self.path(3)))                       # never started
        desk = self.card().desk()
        self.assertTrue(desk.kicker.startswith(f"BINNED 1 OF 3 FILES {DOT} "))
        self.assertEqual([row.words for row in desk.rows[1:]],
                         ["Windows didn't put it in the Recycle Bin - I stopped there.",
                          "Not done - I stopped after the Recycle Bin didn't take one."])
        self.assertEqual(self.hud().caption_text(),
                         "Moved 1 of 3 to the Recycle Bin, sir - see the card for the rest.")

    def test_tidy_words_never_bin_anything_whatever_the_planner_says(self) -> None:
        self.make_bin(words="tidy my Downloads")
        self.assertIsNone(self.entry())                                  # refused: no card at all
        self.assertEqual(self.bin.calls, [])
        self.assertTrue(all(self.there(self.path(n)) for n in (1, 2, 3)))

    def test_a_copy_cards_undo_bins_jarviss_own_copies(self) -> None:
        plan = {"say": "", "steps": [{"skill": "copy_files", "why": "x", "files": ["f1", "f2"], "to": "d1"}]}
        self.make_bin(plan, words="copy the old copies from Downloads into Documents")
        self.later()
        self.press("go")
        desk = self.card().desk()
        self.assertEqual((desk.link, desk.link_note), (("undo", "Undo"), "Puts every file back where it was. Copies "
                                                                         "and zips Jarvis made go to the Recycle Bin."))
        copies = [DOCS + "\\" + NAMES[0], DOCS + "\\" + NAMES[1]]
        self.assertTrue(all(self.there(path) for path in copies))
        self.later()
        self.press("undo")
        self.assertEqual(self.bin.recycled, copies[::-1])                 # newest first
        self.assertFalse(any(self.there(path) for path in copies))
        self.assertTrue(all(self.there(self.path(n)) for n in (1, 2)))    # the originals untouched
        self.assertTrue(self.card().desk().kicker.startswith("PUT BACK 2 OF 2"))


class ScreenshotChipBinTests(FocusApp):
    def test_the_screenshot_chips_undo_puts_it_in_the_recycle_bin(self) -> None:
        self.make_pc()
        made: list[FakeRecycleBin] = []

        def factory() -> FakeRecycleBin:
            made.append(FakeRecycleBin(self.fake))
            return made[-1]

        self.desktop.recycle_factory = factory
        self.enter()
        self.say("screenshot")
        self.assertEqual(self.row(), [f"Screenshot {DOT} saved", "Undo all"])
        self.assertEqual(self.undo("shots"), "Back as it was, sir: screenshot in the Recycle Bin.")
        self.assertEqual(len(made), 1)
        self.assertEqual(len(made[0].recycled), 1)
        self.assertTrue(made[0].recycled[0].casefold().endswith(".png"))
        self.assertTrue(wait_for(lambda: "Screenshot" not in " ".join(self.row())), "the chip goes once binned")
        self.assertEqual(threading.current_thread().name, "MainThread")


if __name__ == "__main__":
    unittest.main()
