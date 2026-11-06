"""An RSVP's note goes to the organizer with the answer, so the owner must have seen all of it first.

The card shows three lines of a note. A note that does not fit opens Edit on the click, as a long
message does: nothing is sent, and the answer goes only after Edit has shown exactly this note to its
end. A note that fits, no note at all, and a Move's or Cancel's note (never sent) stay one click.
AppHarness: fake calendars that only record, Qt offscreen; every name and id is invented.
"""

from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from briefing_reader import hud, ui
from tests.ui_fakes import MOVE_LINE, AppHarness, fonts, settle, wait_for

_app: QApplication | None = None
HEAD = ("RSVP: acct=work | event=jts0001aa | cal=primary | answer=no | notify=all | title=Jarvis test sync | "
        "at=2026-10-05 14:00-15:00 | due= | link= | body=")
HIDDEN = "PRIVATE-TAIL-WORDS"
LONG_NOTE = "See you there. " + "Some harmless words to fill the card. " * 22 + HIDDEN
SHORT_NOTE = "Running late"
MOVE_LONG = MOVE_LINE + LONG_NOTE


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_rsvp_note_review", "-platform", "offscreen"])
    fonts()


class RsvpNoteReviewTests(unittest.TestCase):
    def make(self, *lines: str) -> None:
        self.app = AppHarness(self, ask=False, lines=lines)
        for calendar in self.app.calendars.values():
            calendar.stamps = True
        self.app.reading(autoplay=False)
        self.c, self.reading = self.app.c, self.app.c.window.reading
        self.reading.set_tab(ui.SCREEN_APPROVALS)
        self.work = self.app.calendars["work"]
        settle()
        self.action = self.c._actions[0]
        self.assertTrue(wait_for(lambda: self.action.id in self.c._checks, 5), "Google's view of the event")
        self.card = self.reading.action_card(self.action.id)

    def click(self) -> None:
        self.card.approve_button.click()
        settle()

    def responded(self) -> bool:
        return any(call[0] == "respond" for call in self.work.calls)

    def read_to_the_end_and_close(self) -> None:
        dialog = self.c._dialog
        self.assertIsNotNone(dialog)
        bar = dialog.note_edit.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)   # the note is longer than its box
        bar.setValue(bar.maximum())
        settle()
        self.assertTrue(dialog.whole_seen())
        dialog.reject()
        settle()

    def test_a_note_the_card_cuts_opens_edit_and_sends_nothing(self) -> None:
        self.make(HEAD + LONG_NOTE)
        self.assertTrue(self.card.body_cut())
        self.click()
        self.assertFalse(self.responded())
        self.assertNotIn(self.action.id, self.c._jobs)
        dialog = self.c._dialog
        self.assertIsNotNone(dialog)
        self.assertIn("goes to the organizer", dialog.banner_label.text())
        self.assertIn(HIDDEN, dialog.note_edit.toPlainText())   # the part the card hid
        self.assertEqual(self.card.note(), ui.RSVP_REVIEW_NOTE.format(button="Decline"))

    def test_closing_edit_without_reading_to_the_end_still_sends_nothing(self) -> None:
        self.make(HEAD + LONG_NOTE)
        self.click()
        dialog = self.c._dialog
        self.assertFalse(dialog.whole_seen())
        dialog.reject()
        settle()
        self.assertIsNone(self.c._dialog)
        self.click()
        self.assertFalse(self.responded())
        self.assertIsNotNone(self.c._dialog)   # it asks again

    def test_the_answer_goes_after_edit_showed_the_whole_note(self) -> None:
        self.make(HEAD + LONG_NOTE)
        self.click()
        self.read_to_the_end_and_close()
        self.assertFalse(self.responded())     # reading is not answering: the owner clicks again
        self.click()
        self.assertTrue(wait_for(lambda: self.responded(), 10))
        self.assertIsNone(self.c._dialog)

    def test_a_note_typed_after_reading_and_cancelled_is_not_counted_as_read(self) -> None:
        self.make(HEAD + LONG_NOTE)
        self.click()
        dialog = self.c._dialog
        dialog.note_edit.setPlainText(LONG_NOTE + " and more")
        settle()
        bar = dialog.note_edit.verticalScrollBar()
        bar.setValue(bar.maximum())
        settle()
        dialog.reject()                        # what the card says was never the text on screen
        settle()
        self.click()
        self.assertFalse(self.responded())
        self.assertIsNotNone(self.c._dialog)

    def test_a_note_changed_after_it_was_read_needs_reading_again(self) -> None:
        self.make(HEAD + LONG_NOTE)
        self.c._reviewed[self.action.id] = ui._review_digest("", LONG_NOTE)
        self.assertFalse(self.c._note_unread(self.action))
        self.c._reviewed[self.action.id] = ui._review_digest("", LONG_NOTE + " changed")
        self.assertTrue(self.c._note_unread(self.action))

    def test_a_note_that_fits_is_one_click(self) -> None:
        self.make(HEAD + SHORT_NOTE)
        self.assertFalse(self.card.body_cut())
        self.click()
        self.assertIsNone(self.c._dialog)
        self.assertTrue(wait_for(lambda: self.responded(), 10))

    def test_no_note_is_one_click(self) -> None:
        self.make(HEAD)
        self.click()
        self.assertIsNone(self.c._dialog)
        self.assertTrue(wait_for(lambda: self.responded(), 10))

    def test_a_card_that_is_not_on_screen_counts_as_cut(self) -> None:
        self.make(HEAD + SHORT_NOTE)
        original = self.reading.action_card
        self.addCleanup(setattr, self.reading, "action_card", original)
        self.reading.action_card = lambda _action_id: None
        self.assertTrue(self.c._note_unread(self.action))

    def test_a_move_s_note_is_never_sent_so_it_needs_no_reading(self) -> None:
        self.make(MOVE_LONG)
        self.assertEqual(self.action.kind, "move")
        self.assertFalse(self.c._note_unread(self.action))


class RsvpDialogSeenTests(unittest.TestCase):
    def dialog(self, note: str) -> hud.EditDialog:
        dialog = hud.EditDialog("id1", hud.EDIT_RSVP, "Invitation", "Sync", note=note)
        self.addCleanup(dialog.deleteLater)
        dialog.open()
        settle()
        return dialog

    def test_a_note_that_fits_its_box_counts_as_seen(self) -> None:
        dialog = self.dialog(SHORT_NOTE)
        self.assertTrue(dialog.whole_seen())
        dialog.close()

    def test_a_long_note_counts_only_at_its_end(self) -> None:
        dialog = self.dialog(LONG_NOTE)
        bar = dialog.note_edit.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertFalse(dialog.whole_seen())
        bar.setValue(bar.maximum() // 2)
        settle()
        self.assertFalse(dialog.whole_seen())
        bar.setValue(bar.maximum())
        settle()
        self.assertTrue(dialog.whole_seen())
        dialog.close()

    def test_a_move_has_nothing_it_sends_to_read(self) -> None:
        dialog = hud.EditDialog("id2", hud.EDIT_MOVE, "Move", "Sync", note=LONG_NOTE)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.whole_seen())


if __name__ == "__main__":
    unittest.main()
