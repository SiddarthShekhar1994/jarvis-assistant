"""Overdue to-dos (the walkthrough of 2026-11-28, fix 3).

17 cards waited on Approvals, most of them to-dos due days before, and each still read "Due Sun Nov 22" as if it were
to come. Now a card due before today reads "Overdue, was due Sun Nov 22" (spoken: "overdue, it was due Sunday November
22"), and Approvals' bar offers "Mark N overdue to-dos done..." while two or more to-do hand-offs are overdue: one more
confirm lists them, and each goes through its own Done (which only records - nothing is sent, added or changed). A
to-do with a block to add, a card already decided and a to-do still to come never go. Qt offscreen; every name and id
is invented.
"""

from __future__ import annotations

import logging
import unittest
from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import actions, approvals_view, batch, hud, ui
from tests.ui_fakes import AppHarness, fonts, settle

_app: QApplication | None = None
TODAY = date(2026, 11, 28)
# Due well before the harness's own day and the real one, and well after both.
OLD_A = "Todo: title=Submit the lab form | due=2026-09-20T23:30 | block= | acct= | link="
OLD_B = "Todo: title=Cancel the trial plan | due=2026-09-25 | block= | acct= | link="
OLD_BLOCK = "Todo: title=Read chapter 2 | due=2026-09-22 | block=2027-01-08 19:00-20:00 | acct= | link="
LATER = "Todo: title=Plan the spring term | due=2027-01-10 | block= | acct= | link="


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_overdue_todos", "-platform", "offscreen"])
    fonts()


def parse(line: str) -> actions.ProposedAction:
    return actions.parse_action_line(line)


class WordsTests(unittest.TestCase):
    def test_a_card_due_before_today_says_overdue(self) -> None:
        todo = parse("Todo: title=Form | due=2026-11-22T23:30 | block= | acct= | link=")
        detail = actions.card_view(todo, TODAY).detail
        self.assertTrue(detail.startswith("Overdue, was due Sun Nov 22"), detail)
        self.assertIn("11:30 PM", detail)

    def test_today_and_later_read_as_before(self) -> None:
        today = parse("Todo: title=Form | due=2026-11-28 | block= | acct= | link=")
        later = parse("Todo: title=Form | due=2026-12-02 | block= | acct= | link=")
        self.assertTrue(actions.card_view(today, TODAY).detail.startswith("Due today"))
        self.assertTrue(actions.card_view(later, TODAY).detail.startswith("Due Wed Dec 2"))

    def test_a_reply_due_before_today_says_overdue(self) -> None:
        # A Reply / Email card's detail is these words (actions.due_words).
        self.assertEqual(actions._due_words(date(2026, 11, 25), TODAY), "overdue, was due Wed Nov 25")

    def test_a_year_shows_when_it_differs(self) -> None:
        self.assertEqual(actions._due_words(date(2025, 12, 30), TODAY), "overdue, was due Tue Dec 30, 2025")

    def test_the_spoken_words_say_overdue(self) -> None:
        self.assertEqual(actions._spoken_due(datetime(2026, 11, 22, 23, 30), TODAY)[:44],
                         "overdue, it was due Sunday November 22 at 11")
        self.assertEqual(actions._spoken_due(date(2026, 11, 28), TODAY), "due today")


class ListTests(unittest.TestCase):
    def cards(self) -> list[actions.ProposedAction]:
        return [parse(line) for line in (OLD_A, OLD_B, OLD_BLOCK, LATER)]

    def test_only_overdue_hand_offs_go(self) -> None:
        listed = batch.overdue_todos(self.cards(), decided=(), jobs=(), today=TODAY)
        self.assertEqual([a.title for a in listed], ["Submit the lab form", "Cancel the trial plan"])

    def test_decided_running_and_undated_cards_stay(self) -> None:
        cards = self.cards() + [parse("Todo: title=No date | due= | block= | acct= | link=")]
        first, second = cards[0], cards[1]
        self.assertEqual(batch.overdue_todos(cards, decided={first.id}, jobs={second.id}, today=TODAY), [])

    def test_other_kinds_never_go(self) -> None:
        reply = parse("Reply: acct= | to=ana@example.edu | cc= | subject=Re: Forms | due=2026-09-25 | link= | "
                      "msgid= | thread= | body=Hi Ana")
        event = parse("Calendar: Old talk | 2026-09-20 10:00-11:00 | | Hall |")
        self.assertEqual(batch.overdue_todos([reply, event], decided=(), jobs=(), today=TODAY), [])

    def test_rows_say_what_each_card_says(self) -> None:
        listed = batch.overdue_todos(self.cards(), decided=(), jobs=(), today=TODAY)
        rows = batch.done_rows(listed, TODAY)
        self.assertEqual([row.kind for row in rows], ["To-do", "To-do"])
        self.assertEqual(rows[0].title, "Submit the lab form")
        self.assertTrue(rows[0].detail.startswith("Overdue, was due Sun Sep 20"))
        self.assertNotEqual(batch.digest(rows), batch.digest(rows[:1]))


class BarTests(unittest.TestCase):
    def test_the_button_shows_with_a_count_and_hides_at_zero(self) -> None:
        bar = approvals_view.BatchBar()
        self.addCleanup(bar.deleteLater)
        bar.set_batch(0, approvals_view.BATCH_HIDDEN)
        bar.set_overdue(3)
        self.assertTrue(bar.isVisibleTo(bar.parentWidget()) if bar.parentWidget() else not bar.isHidden())
        self.assertFalse(bar.done_button.isHidden())
        self.assertTrue(bar.button.isHidden())               # no Add all: only the to-dos' button
        self.assertEqual(bar.done_button.text(), "Mark 3 overdue to-dos done...")
        bar.set_overdue(0)
        self.assertTrue(bar.isHidden())

    def test_it_hides_while_an_add_all_runs(self) -> None:
        bar = approvals_view.BatchBar()
        self.addCleanup(bar.deleteLater)
        bar.set_overdue(2)
        bar.set_batch(3, approvals_view.BATCH_RUNNING, "Adding 1 of 3...")
        self.assertTrue(bar.done_button.isHidden())
        bar.set_batch(3, approvals_view.BATCH_IDLE)
        self.assertFalse(bar.done_button.isHidden())
        self.assertFalse(bar.button.isHidden())

    def test_its_click_is_a_request_only(self) -> None:
        bar = approvals_view.BatchBar()
        self.addCleanup(bar.deleteLater)
        bar.set_overdue(2)
        heard: list[bool] = []
        bar.doneRequested.connect(lambda: heard.append(True))
        bar.done_button.click()
        self.assertEqual(heard, [True])


class AppTests(unittest.TestCase):
    lines = (OLD_A, OLD_B, OLD_BLOCK, LATER)

    def setUp(self) -> None:
        self.app = AppHarness(self, ask=False, lines=self.lines)
        self.app.reading(autoplay=False)
        self.c, self.reading = self.app.c, self.app.c.window.reading
        self.reading.set_tab(ui.SCREEN_APPROVALS)
        self.stage = self.reading.approvals_stage
        self.c._refresh_actions_ui()
        settle()

    def status(self, title: str) -> str:
        action = next(a for a in self.c._actions if a.title == title)
        return (self.app.store.get(action.id) or {}).get("status", "")

    def open_dialog(self) -> hud.BatchConfirmDialog:
        bar = self.stage.batch_bar
        self.assertEqual(bar.overdue(), 2)
        bar.done_button.click()
        settle()
        dialog = self.c._batch_dialog
        self.assertIsNotNone(dialog)
        return dialog

    def test_the_bar_offers_the_two_overdue_hand_offs(self) -> None:
        self.assertTrue(self.stage.batch_bar.isVisibleTo(self.stage))
        self.assertEqual(self.stage.batch_bar.done_button.text(), "Mark 2 overdue to-dos done...")

    def test_cancel_marks_nothing(self) -> None:
        dialog = self.open_dialog()
        self.assertEqual(dialog.add_button.text(), "Mark 2 done")
        self.assertIn("nothing is sent, added or changed", dialog.footer_label.text())
        self.assertEqual(dialog.title_label.text(), "Mark these 2 overdue to-dos done?")
        dialog.cancel_button.click()
        settle()
        self.assertEqual(self.status("Submit the lab form"), "")
        self.assertEqual(self.status("Cancel the trial plan"), "")

    def test_enter_marks_nothing(self) -> None:
        dialog = self.open_dialog()
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        settle()
        self.assertEqual(self.status("Submit the lab form"), "")

    def test_confirm_marks_each_done_and_leaves_the_rest(self) -> None:
        dialog = self.open_dialog()
        with self.assertLogs("briefing_reader.ui", level="INFO") as logs:
            dialog.add_button.click()
            settle()
        self.assertEqual(self.status("Submit the lab form"), "done")
        self.assertEqual(self.status("Cancel the trial plan"), "done")
        self.assertEqual(self.status("Read chapter 2"), "")         # a block to add: its own click
        self.assertEqual(self.status("Plan the spring term"), "")   # still to come
        self.assertIn("Overdue to-dos: marked 2 done", " ".join(logs.output))
        self.assertNotIn("lab form", " ".join(logs.output))
        self.assertEqual(self.stage.batch_bar.overdue(), 0)

    def test_a_list_that_changed_marks_nothing(self) -> None:
        dialog = self.open_dialog()
        first = next(a for a in self.c._actions if a.title == "Submit the lab form")
        self.app.store.set(first.id, "denied", kind=first.kind)
        dialog.add_button.click()
        settle()
        self.assertEqual(self.status("Cancel the trial plan"), "")

    def test_the_button_hides_under_two(self) -> None:
        first = next(a for a in self.c._actions if a.title == "Submit the lab form")
        self.app.store.set(first.id, "done", kind=first.kind)
        self.c._refresh_actions_ui()
        settle()
        self.assertEqual(self.stage.batch_bar.overdue(), 0)


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()
