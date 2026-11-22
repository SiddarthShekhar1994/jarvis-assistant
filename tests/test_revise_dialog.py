"""The Edit dialog's revise row (mail extras, part 2): only on a Reply / Email with revise on; Rewrite asks with the
owner's words and the message as it is; while it runs the words and the message are read only and Rewrite reads Stop;
a rewrite replaces only the message (recipients and subject never change), must be read again and can be put back;
Enter in the words never saves. Qt offscreen; every name and address is invented."""

from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import hud
from tests.ui_fakes import fonts, settle

_app: QApplication | None = None
BODY = "Hi Ana,\nThe numbers are in.\nThanks"
REWRITE = "Hi Ana,\nGood news: the numbers are in, and I'll send the rest Friday.\nThanks so much"


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_revise_dialog", "-platform", "offscreen"])
    fonts()


def dialog(kind: str = "email", revise: bool = True) -> hud.EditDialog:
    chips = (hud.RecipientChip("ana@example.edu", hud.RECIPIENT_KNOWN),) if kind in ("email", "reply") else ()
    if kind in ("email", "reply"):
        made = hud.EditDialog("a1", kind, f"{kind} · work", "Budget numbers", sender="me@example.edu", to=chips,
                              body=BODY, revise=revise)
    else:
        made = hud.EditDialog("a1", kind, kind, "Jarvis test sync", date_text="2026-10-09", start_text="2:00 PM",
                              end_text="3:00 PM", revise=revise)
    made.open()
    settle()
    return made


class ReviseRowTests(unittest.TestCase):
    def tearDown(self) -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, hud.EditDialog):
                widget.close()
                widget.deleteLater()
        settle()

    def test_the_row_is_there_only_for_mail_with_revise_on(self) -> None:
        self.assertIsNotNone(dialog("email").revise_edit)
        self.assertIsNotNone(dialog("reply").revise_edit)
        self.assertIsNone(dialog("email", revise=False).revise_edit)
        self.assertIsNone(dialog("move").revise_edit)

    def test_rewrite_waits_for_words_and_asks_with_the_message(self) -> None:
        made = dialog()
        asked: list[tuple[str, str, str]] = []
        made.reviseRequested.connect(lambda *args: asked.append(args))
        self.assertFalse(made.revise_button.isEnabled())
        self.assertEqual(made.revise_edit.placeholderText(), hud.REVISE_HINT)
        self.assertEqual(made.revise_edit.maxLength(), 300)
        made.revise_edit.setText("  warmer,   and say I'll send it Friday ")
        self.assertTrue(made.revise_button.isEnabled())
        made.revise_button.click()
        self.assertEqual(asked, [("a1", "warmer, and say I'll send it Friday", BODY)])

    def test_enter_in_the_words_asks_and_never_saves(self) -> None:
        made = dialog()
        asked, saved = [], []
        made.reviseRequested.connect(lambda *args: asked.append(args))
        made.saved.connect(lambda *args: saved.append(args))
        made.revise_edit.setText("shorter")
        made.revise_edit.setFocus()
        QTest.keyClick(made.revise_edit, Qt.Key.Key_Return)
        settle()
        self.assertEqual(len(asked), 1)
        self.assertEqual(saved, [])
        self.assertTrue(made.isVisible())

    def test_while_it_runs_it_is_read_only_and_stop_stops(self) -> None:
        made = dialog()
        stopped: list[str] = []
        made.reviseStopped.connect(stopped.append)
        made.revise_edit.setText("shorter")
        made.set_revising(True)
        self.assertTrue(made.is_revising())
        self.assertTrue(made.revise_edit.isReadOnly())
        self.assertTrue(made.body_edit.isReadOnly())
        self.assertEqual(made.revise_button.text(), hud.REVISE_STOP_TEXT)
        self.assertEqual(made.revise_note_text(), hud.REVISE_RUNNING_NOTE)
        made.revise_button.click()
        self.assertEqual(stopped, ["a1"])
        made.set_revising(False)
        self.assertFalse(made.body_edit.isReadOnly())
        self.assertEqual(made.revise_button.text(), hud.REVISE_TEXT)

    def test_a_rewrite_replaces_only_the_message_must_be_read_and_can_be_put_back(self) -> None:
        made = dialog()
        before = made.values()
        made.revise_edit.setText("warmer")
        made.set_revising(True)
        long = "\n".join(f"Line {n} of a long rewrite." for n in range(120))
        made.offer_rewrite(long)
        settle(6)
        self.assertFalse(made.whole_seen())        # read again: a rewrite that does not fit is unread until scrolled
        made.offer_rewrite(REWRITE)
        self.assertFalse(made.is_revising())
        after = made.values()
        self.assertEqual(after["body"], REWRITE)
        self.assertEqual((after["to"], after["cc"], after["subject"]), (before["to"], before["cc"], before["subject"]))
        self.assertTrue(made.rewrite_kept())
        self.assertTrue(made.put_back_button.isVisibleTo(made))
        self.assertEqual(made.revise_note_text(), hud.REVISE_OFFERED_NOTE)
        settle(6)
        self.assertTrue(made.whole_seen())         # it fits: on screen whole once laid out
        made.put_back_button.click()
        self.assertEqual(made.values()["body"], BODY)
        self.assertFalse(made.rewrite_kept())
        self.assertFalse(made.put_back_button.isVisibleTo(made))
        self.assertEqual(made.revise_note_text(), hud.REVISE_PUT_BACK_NOTE)

    def test_a_second_rewrite_puts_back_the_first_message(self) -> None:
        made = dialog()
        made.offer_rewrite(REWRITE)
        made.offer_rewrite("Hi Ana, shorter.")
        made.put_back_button.click()
        self.assertEqual(made.values()["body"], BODY)

    def test_notes_are_plain_text(self) -> None:
        made = dialog()
        made.show_revise_note("<b>bold</b>", warn=True)
        self.assertEqual(made.revise_note.textFormat(), Qt.TextFormat.PlainText)


if __name__ == "__main__":
    unittest.main()
