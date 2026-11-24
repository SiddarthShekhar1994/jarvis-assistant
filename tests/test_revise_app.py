"""Rewrite in the app (mail extras, part 2): Edit on an Email, the owner's words, Rewrite, Jarvis's text offered in the same
dialog (never kept before Save, never sent before Send); the run sees only the draft and the words; the screen stays; a
question or a failure is said in Jarvis's own words; an answer for a closed or changed dialog is dropped; closing the
dialog cancels the run; a saved rewrite is read whole before Send. AppHarness: a fake Claude Code that only records, Qt
offscreen; every name and address is invented."""

from __future__ import annotations

import logging
import threading
import unittest

from PySide6.QtWidgets import QApplication

from briefing_reader import hud, ui
from tests.ask_fakes import GatedProcess, plan_stream
from tests.ui_fakes import AppHarness, fonts, settle, wait_for

_app: QApplication | None = None
EMAIL = ("Email: acct=work | to=ana@example.edu | cc= | subject=Budget numbers | due= | link= | "
         "body=Hi Ana,\\nThe numbers are in.\\nThanks")
REWRITE = "Hi Ana,\nGood news: the numbers are in, and I'll send the rest Friday.\nThanks so much"
ANSWER = {"say": "Warmer, with Friday.", "body": REWRITE, "question": ""}


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_revise_app", "-platform", "offscreen"])
    fonts()


class ReviseAppTestCase(unittest.TestCase):
    runs: tuple = ()

    def setUp(self) -> None:
        self.app = AppHarness(self, ask=True, runs=self.make_runs(), lines=(EMAIL,))
        self.app.reading(autoplay=False)
        self.c, self.reading = self.app.c, self.app.c.window.reading
        self.reading.set_tab(ui.SCREEN_APPROVALS)
        settle()
        self.email = next(action for action in self.c._actions if action.kind == "email")

    def make_runs(self) -> list:
        return [plan_stream(ANSWER)]

    def open_edit(self) -> hud.EditDialog:
        self.c.edit_action(self.email.id)
        settle()
        dialog = self.c._dialog
        self.assertIsNotNone(dialog)
        self.assertIsNotNone(dialog.revise_edit)
        return dialog

    def rewrite(self, dialog: hud.EditDialog, words: str = "warmer, and say I'll send the rest Friday") -> None:
        dialog.revise_edit.setText(words)
        dialog.revise_button.click()
        settle()

    def wait_answer(self, dialog: hud.EditDialog) -> None:
        self.assertTrue(wait_for(lambda: not dialog.is_revising() and self.c.ask is not None and not self.c.ask.busy,
                                 10))
        settle(4)


class RewriteTests(ReviseAppTestCase):
    def test_rewrite_offers_the_text_in_the_dialog_and_nothing_is_kept_before_save(self) -> None:
        dialog = self.open_edit()
        tab = self.reading.current_tab()
        said = list(self.app.voice.texts())
        with self.assertLogs("briefing_reader", logging.INFO) as logs:
            self.rewrite(dialog)
            self.assertTrue(dialog.is_revising())
            self.assertEqual(self.reading.current_tab(), tab)       # the screen stays (LIVE never comes up)
            self.wait_answer(dialog)
        self.assertEqual(dialog.values()["body"], REWRITE)
        self.assertEqual(dialog.values()["to"], ["ana@example.edu"])
        self.assertEqual(dialog.values()["subject"], "Budget numbers")
        self.assertEqual(dialog.revise_note_text(), hud.REVISE_OFFERED_NOTE)
        self.assertEqual(self.c._current(self.email.id).body, self.email.body)   # nothing kept yet
        self.assertEqual(self.reading.current_tab(), tab)
        self.assertEqual(list(self.app.voice.texts()), said)       # nothing said
        started = self.app.factory.runner.started[0]
        self.assertIn("<draft subject=\"Budget numbers\">", started.stdin)
        self.assertIn("The owner's words: warmer, and say I'll send the rest Friday", started.stdin)
        self.assertNotIn("<mail>", started.stdin)
        self.assertEqual(self.app.senders["work"].sent if hasattr(self.app.senders["work"], "sent") else [], [])
        text = "\n".join(logs.output)
        for secret in ("Ana", "numbers", "Friday", "warmer", "example.edu"):
            self.assertNotIn(secret, text)
        self.assertNotIn("Warmer, with Friday", self.reading.command_bar.status_text()
                         if hasattr(self.reading.command_bar, "status_text") else "")

    def test_save_keeps_the_rewrite_and_send_reads_it_whole_first(self) -> None:
        dialog = self.open_edit()
        self.rewrite(dialog)
        self.wait_answer(dialog)
        dialog.save_button.click()
        settle()
        self.assertIsNone(self.c._dialog)
        self.assertEqual(self.c._current(self.email.id).body, REWRITE)
        self.assertIn(self.email.id, self.c._rewritten)
        self.c._reviewed.pop(self.email.id, None)          # saved before it was on screen whole
        self.c.approve_action(self.email.id)
        settle()
        self.assertIsNotNone(self.c._dialog)                # Send opens Edit to read it first
        self.assertIsNone(self.c._send_hold)
        self.assertEqual(self.c._dialog.banner_label.text(), ui.REVIEW_BANNER)

    def test_put_back_and_save_keeps_the_old_message(self) -> None:
        dialog = self.open_edit()
        self.rewrite(dialog)
        self.wait_answer(dialog)
        dialog.put_back_button.click()
        dialog.save_button.click()
        settle()
        self.assertEqual(self.c._current(self.email.id).body, self.email.body)
        self.assertNotIn(self.email.id, self.c._rewritten)


class QuestionTests(ReviseAppTestCase):
    def make_runs(self) -> list:
        return [plan_stream({"say": "Hmm.", "body": "", "question": "Which Friday do you mean?"})]

    def test_a_question_is_said_in_jarvis_own_words(self) -> None:
        dialog = self.open_edit()
        self.rewrite(dialog)
        self.wait_answer(dialog)
        self.assertEqual(dialog.values()["body"], "Hi Ana,\nThe numbers are in.\nThanks")
        self.assertEqual(dialog.revise_note_text(), ui.REVISE_QUESTION_NOTE)
        self.assertNotIn("Which Friday", dialog.revise_note_text())


class HeldTests(ReviseAppTestCase):
    def make_runs(self) -> list:
        return [plan_stream({"say": "Done.", "body": REWRITE + "\nmallory@example.net", "question": ""})]

    def test_a_rewrite_with_a_new_address_is_never_offered(self) -> None:
        dialog = self.open_edit()
        self.rewrite(dialog)
        self.wait_answer(dialog)
        self.assertNotIn("mallory", dialog.values()["body"])
        self.assertEqual(dialog.revise_note_text(), ui.REVISE_HELD_NOTES["new_address"])


class GatedTests(ReviseAppTestCase):
    def make_runs(self) -> list:
        self.gate = threading.Event()
        return [GatedProcess(plan_stream(ANSWER), self.gate, after=1)]

    def test_closing_the_dialog_cancels_the_run_and_drops_its_answer(self) -> None:
        dialog = self.open_edit()
        self.rewrite(dialog)
        self.assertTrue(self.c.ask.busy)
        dialog.reject()
        settle()
        self.assertIsNone(self.c._revise_run)
        self.gate.set()
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle(4)
        self.assertEqual(self.c._current(self.email.id).body, self.email.body)
        self.assertIsNone(self.c._dialog)

    def test_an_answer_for_a_changed_message_is_dropped(self) -> None:
        dialog = self.open_edit()
        self.rewrite(dialog)
        dialog.subject_edit.setText("Budget numbers, final")    # the subject is not read only: the digest moves
        self.gate.set()
        self.wait_answer(dialog)
        self.assertEqual(dialog.values()["body"], "Hi Ana,\nThe numbers are in.\nThanks")
        self.assertEqual(dialog.revise_note_text(), ui.REVISE_CHANGED_NOTE)

    def test_stop_cancels_the_run(self) -> None:
        dialog = self.open_edit()
        self.rewrite(dialog)
        dialog.revise_button.click()     # Stop
        settle()
        self.assertFalse(dialog.is_revising())
        self.assertEqual(dialog.revise_note_text(), ui.REVISE_STOPPED_NOTE)
        self.gate.set()
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle(4)
        self.assertEqual(dialog.values()["body"], "Hi Ana,\nThe numbers are in.\nThanks")

    def test_a_second_rewrite_waits_while_one_runs(self) -> None:
        dialog = self.open_edit()
        self.rewrite(dialog)
        dialog.set_revising(False)       # (as if the row were free again)
        self.rewrite(dialog, "shorter")
        self.assertEqual(dialog.revise_note_text(), ui.REVISE_BUSY_NOTE)
        self.gate.set()
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))

    # ---- fix round 1 (low 6 and low 8) -----------------------------------------------------------------------------

    def test_esc_in_the_ask_line_frees_the_row_and_adds_no_entry(self) -> None:
        dialog = self.open_edit()
        said: list = []
        jarvis = self.c._jarvis
        self.c._jarvis = lambda *args, **kw: said.append(args) or jarvis(*args, **kw)
        self.rewrite(dialog)
        self.c.ask.cancel()                                     # Esc in the ask line
        self.gate.set()
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle(4)
        self.assertFalse(dialog.is_revising())
        self.assertEqual(dialog.revise_note_text(), ui.REVISE_STOPPED_NOTE)
        self.assertEqual(said, [])                              # no "cancelled" entry for a Rewrite
        self.assertFalse(self.c._revise_asking)

    def test_stop_adds_no_conversation_entry(self) -> None:
        dialog = self.open_edit()
        said: list = []
        jarvis = self.c._jarvis
        self.c._jarvis = lambda *args, **kw: said.append(args) or jarvis(*args, **kw)
        self.rewrite(dialog)
        dialog.revise_button.click()                            # Stop
        self.gate.set()
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))
        settle(4)
        self.assertEqual(said, [])


class ReviseOutcomeTests(ReviseAppTestCase):
    """Fix round 1 (low 6): an outcome that names no dialog (busy, an unexpected error) frees the row of the dialog
    that asked, in Jarvis's words; (low 8) LIVE never says notes were given to a Rewrite."""

    def test_an_outcome_without_its_digest_frees_the_row(self) -> None:
        from briefing_reader.ask.modes import MODE_REVISE
        from briefing_reader.ask.planner import AskOutcome

        dialog = self.open_edit()
        self.rewrite(dialog)
        self.assertTrue(self.c.ask.busy)
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, 10))   # (the real answer; then a stray one)
        settle(4)
        dialog.set_revising(True)
        self.c._revise_run = (self.email.id, "digest")
        self.c._on_revise_outcome(AskOutcome(False, "busy", "Jarvis is busy", mode=MODE_REVISE, target=self.email.id))
        self.assertFalse(dialog.is_revising())
        self.assertIn("couldn't rewrite it this time (Jarvis is busy)", dialog.revise_note_text())

    def test_live_says_no_notes_were_given(self) -> None:
        from briefing_reader import live

        dialog = self.open_edit()
        self.rewrite(dialog)
        self.wait_answer(dialog)
        task = next(task for task in self.c.live.snapshot() if task.kind == live.TASK_REVISE)
        labels = [field.label for field in task.steps[0].fields]
        self.assertNotIn("Memory given", labels)


class AskOffTests(unittest.TestCase):
    def test_no_revise_row_while_ask_is_off(self) -> None:
        app = AppHarness(self, ask=False, lines=(EMAIL,))
        app.reading(autoplay=False)
        email = next(action for action in app.c._actions if action.kind == "email")
        app.c.edit_action(email.id)
        settle()
        self.assertIsNone(app.c._dialog.revise_edit)
        app.c._dialog.reject()
        settle()


if __name__ == "__main__":
    unittest.main()
