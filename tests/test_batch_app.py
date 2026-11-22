"""Add all in the app (mail extras, part 1): the bar on Approvals, the one more confirm, one card at a time.

The bar shows only while at least two own-calendar cards could go together; Cancel adds nothing; "Add N" adds them in
card order through each card's own path, every card locked meanwhile, and stops on a failure, on Stop or on a list that
changed; each added card keeps its own Undo; the Approvals count falls; the logs hold counts and reason words only.
AppHarness: fake calendars that only record, Qt offscreen; every name and id is invented."""

from __future__ import annotations

import logging
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import approvals_view, batch, hud, ui
from briefing_reader.gcal import CalendarError
from tests.ui_fakes import B_CALENDAR, CAL_LINE, MOVE_LINE, AppHarness, fonts, settle, wait_for

_app: QApplication | None = None
TODO_BLOCK = "Todo: title=Read chapter 4 | due=2026-10-09 | block=2026-10-07 19:00-21:00 | acct= | link="
CAL_THIRD = "Calendar: Lab safety talk | 2026-10-08 10:00-11:00 | | Science hall |"
EMAIL_OK = ("Email: acct=work | to=ana@example.edu | cc= | subject=Budget numbers | due= | link= | "
            "body=Hi Ana,\\nThe numbers are in.\\nThanks")


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_batch_app", "-platform", "offscreen"])
    fonts()


class BatchAppTestCase(unittest.TestCase):
    lines: tuple[str, ...] = (B_CALENDAR, MOVE_LINE, TODO_BLOCK, EMAIL_OK, CAL_LINE, CAL_THIRD)

    def setUp(self) -> None:
        self.app = AppHarness(self, ask=False, lines=self.lines)
        for calendar in self.app.calendars.values():
            calendar.stamps = True
        self.app.reading(autoplay=False)
        self.c, self.reading = self.app.c, self.app.c.window.reading
        self.reading.set_tab(ui.SCREEN_APPROVALS)
        self.personal = self.app.calendars["personal"]
        self.stage = self.reading.approvals_stage
        self.c._accounts_state["personal"] = (True, "", "")   # the worker's sign-in check, as it reports it
        self.c._refresh_actions_ui()
        settle()

    def own_adds(self):
        return [a for a in self.c._actions if a.kind in ("calendar", "todo") and a.actionable]

    def open_dialog(self) -> hud.BatchConfirmDialog:
        self.assertEqual(self.stage.batch_bar.state(), approvals_view.BATCH_IDLE)
        self.stage.batch_bar.button.click()
        settle()
        dialog = self.c._batch_dialog
        self.assertIsNotNone(dialog)
        return dialog

    def finish(self, timeout: float = 10) -> None:
        self.assertTrue(wait_for(lambda: self.c._batch is None, timeout), "the batch ends")
        settle()

    def creates(self) -> list[str]:
        return [call[1] for call in self.personal.calls if call[0] == "create"]


class BarTests(BatchAppTestCase):
    def test_the_bar_offers_the_own_calendar_adds_only(self) -> None:
        bar = self.stage.batch_bar
        self.assertTrue(bar.isVisibleTo(self.stage))
        self.assertEqual(bar.count(), 4)                       # three events and a block; never the Move or Email
        self.assertEqual(bar.button.text(), "Add all 4 to calendar...")

    def test_the_bar_is_hidden_under_two(self) -> None:
        adds = self.own_adds()
        for action in adds[:3]:
            self.app.store.set(action.id, "denied", kind=action.kind)
        self.c._refresh_actions_ui()
        settle()
        self.assertEqual(self.stage.batch_bar.state(), approvals_view.BATCH_HIDDEN)
        self.assertFalse(self.stage.batch_bar.isVisibleTo(self.stage))

    def test_the_bar_is_hidden_while_the_personal_account_is_signed_out(self) -> None:
        self.c._accounts_state["personal"] = (False, "", "")
        self.c._refresh_actions_ui()
        settle()
        self.assertEqual(self.stage.batch_bar.state(), approvals_view.BATCH_HIDDEN)

    def test_cancel_adds_nothing(self) -> None:
        dialog = self.open_dialog()
        self.assertEqual(len(dialog.rows), 4)
        self.assertEqual(dialog.add_button.text(), "Add 4")
        self.assertIn("Nobody is invited or told.", dialog.footer_label.text())
        self.assertTrue(dialog.excluded_label.isVisibleTo(dialog))        # the Move and the Email
        self.assertIn("2 other cards wait for their own click.", dialog.excluded_label.text())
        dialog.cancel_button.click()
        settle()
        self.assertIsNone(self.c._batch_dialog)
        self.assertIsNone(self.c._batch)
        self.assertEqual(self.creates(), [])

    def test_enter_and_esc_add_nothing(self) -> None:
        dialog = self.open_dialog()
        self.assertFalse(dialog.add_button.hasFocus())
        self.assertFalse(dialog.cancel_button.hasFocus())
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        settle()
        self.assertEqual(self.creates(), [])
        self.assertTrue(dialog.isVisible())
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        settle()
        self.assertIsNone(self.c._batch_dialog)
        self.assertEqual(self.creates(), [])


class RunTests(BatchAppTestCase):
    def test_confirm_adds_them_in_card_order_with_every_card_locked(self) -> None:
        adds = self.own_adds()
        pending = self.reading.approvals_queue.count()
        locked: list[bool] = []
        original = self.c._carry_out

        def carry(action):
            original(action)
            others = [card for card in self.reading.approvals.cards() if card.action_id != action.id]
            locked.append(all(card.is_locked() for card in others if card.actionable))
            self.assertEqual(self.stage.batch_bar.state(), approvals_view.BATCH_RUNNING)

        self.c._carry_out = carry
        dialog = self.open_dialog()
        with self.assertLogs("briefing_reader", logging.INFO) as logs:
            dialog.add_button.click()
            self.finish()
        self.assertEqual(self.creates(), [a.title if a.kind == "calendar" else a.title for a in adds])
        self.assertEqual(locked, [True] * 4)
        for action in adds:
            card = self.reading.action_card(action.id)
            self.assertEqual(card.status(), hud.CARD_ADDED)
            self.assertEqual(card.undo_text(), "Undo")          # each keeps its own Undo
            self.assertIn(action.id, self.c._undos)
        self.assertLess(self.reading.approvals_queue.count(), pending)   # the count the canvas shows falls by 4
        self.assertEqual(self.reading.approvals_queue.count(), pending - 4)
        self.assertEqual(self.stage.batch_bar.state(), approvals_view.BATCH_HIDDEN)
        self.assertIn("Added 4 of 4", " ".join(self.reading.activity.entries()[0]))   # the newest row
        text = "\n".join(logs.output)
        self.assertIn("Add all ended: 4 of 4 added; all done", text)
        for action in adds:
            self.assertNotIn(action.title, text)                 # no title in any log line

    def test_undo_on_one_added_card_deletes_only_it(self) -> None:
        adds = self.own_adds()
        self.open_dialog().add_button.click()
        self.finish()
        first = adds[0]
        offer = self.c._undos[first.id]
        offer.shown -= 5
        self.reading.action_card(first.id).undo_button.click()
        self.assertTrue(wait_for(lambda: first.id not in self.c._jobs, 10))
        settle()
        undone = [call for call in self.personal.calls if call[0] == "undo_create"]
        self.assertEqual(len(undone), 1)
        self.assertEqual(self.reading.action_card(first.id).status(), hud.CARD_PENDING)
        for action in adds[1:]:
            self.assertEqual(self.reading.action_card(action.id).status(), hud.CARD_ADDED)

    def test_it_stops_on_the_first_failure(self) -> None:
        adds = self.own_adds()
        inner = self.personal.create_event

        def create_event(action, interactive: bool = True, **kwargs):
            if action.title == adds[1].title:
                self.personal._note("create", action.title)
                raise CalendarError("Google said no")
            return inner(action, interactive=interactive, **kwargs)

        self.personal.create_event = create_event
        self.open_dialog().add_button.click()
        self.finish()
        self.assertEqual(self.creates(), [adds[0].title, adds[1].title])   # the third and fourth never went
        self.assertEqual(self.reading.action_card(adds[0].id).status(), hud.CARD_ADDED)
        self.assertEqual(self.reading.action_card(adds[1].id).status(), hud.CARD_FAILED)
        self.assertEqual(self.reading.action_card(adds[2].id).status(), hud.CARD_PENDING)
        self.assertFalse(any(card.is_locked() for card in self.reading.approvals.cards()))

    def test_stop_ends_it_after_the_card_on_its_way(self) -> None:
        adds = self.own_adds()
        original = self.c._carry_out

        def carry(action):
            original(action)
            if action.id == adds[1].id:
                self.stage.batch_bar.button.click()           # Stop, while the second one is on its way
                self.assertEqual(self.stage.batch_bar.button.text(), approvals_view.BATCH_STOPPING_TEXT)

        self.c._carry_out = carry
        with self.assertLogs("briefing_reader", logging.INFO) as logs:
            self.open_dialog().add_button.click()
            self.finish()
        self.assertEqual(self.creates(), [adds[0].title, adds[1].title])
        self.assertIn("Add all ended: 2 of 4 added; stopped", "\n".join(logs.output))

    def test_a_list_changed_before_the_confirm_adds_nothing(self) -> None:
        adds = self.own_adds()
        dialog = self.open_dialog()
        self.app.store.set(adds[2].id, "denied", kind=adds[2].kind)   # decided meanwhile (another way)
        dialog.add_button.click()
        settle()
        self.assertIsNone(self.c._batch)
        self.assertEqual(self.creates(), [])

    def test_a_card_decided_during_the_batch_ends_it_there(self) -> None:
        adds = self.own_adds()
        original = self.c._carry_out

        def carry(action):
            original(action)
            if action.id == adds[0].id:
                self.app.store.set(adds[1].id, "denied", kind=adds[1].kind)

        self.c._carry_out = carry
        self.open_dialog().add_button.click()
        self.finish()
        self.assertEqual(self.creates(), [adds[0].title])

    def test_a_locked_card_click_does_nothing_while_it_runs(self) -> None:
        adds = self.own_adds()
        move = next(a for a in self.c._actions if a.kind == "move")
        original = self.c._carry_out
        tried: list[bool] = []

        def carry(action):
            original(action)
            if action.id == adds[0].id:
                tried.append(self.c._locked_out(move.id))

        self.c._carry_out = carry
        self.open_dialog().add_button.click()
        self.finish()
        self.assertEqual(tried, [True])
        self.assertNotIn(move.id, self.c._jobs)

    def test_quit_ends_it(self) -> None:
        adds = self.own_adds()
        self.c._batch = ui._Batch(ids=[a.id for a in adds], index=1, added=1)
        with self.assertLogs("briefing_reader.batch", logging.INFO) as logs:
            self.c.state = ui.STATE_QUITTING
            self.c._batch_step()
        self.assertIsNone(self.c._batch)
        self.assertEqual(logs.records[-1].getMessage(), "Add all ended: 1 of 4 added; quit")

    def test_the_step_gap_is_short(self) -> None:
        self.assertEqual(self.c._batch_timer.interval(), batch.STEP_GAP_MS)
        self.assertTrue(self.c._batch_timer.isSingleShot())


class FixRoundTests(BatchAppTestCase):
    """Fix round 1: each step checks the card's whole row (low 9), something that starts before the confirm says so
    (medium 4), and a standing Reply's check keeps the bar away (medium 4)."""

    def test_a_card_whose_place_changed_under_the_same_id_ends_it(self) -> None:
        import dataclasses

        from briefing_reader.actions import SOURCE_BRIEFING

        adds = self.own_adds()
        target = next(a for a in adds[1:] if a.kind == "calendar")   # a Calendar card's id leaves its place out
        original = self.c._carry_out

        def carry(action):
            original(action)
            if action.id == adds[0].id:
                briefed = [dataclasses.replace(a, where="Room 9") if a.id == target.id else a
                           for a in self.c._source_lists[SOURCE_BRIEFING]]
                self.assertEqual(next(a for a in briefed if a.id == target.id).id, target.id)   # the same id
                self.c._source_lists[SOURCE_BRIEFING] = briefed
                self.c._set_actions(self.c._merged_actions())

        self.c._carry_out = carry
        with self.assertLogs("briefing_reader.batch", logging.INFO) as logs:
            self.open_dialog().add_button.click()
            self.finish()
        self.assertNotIn(target.title, self.creates())
        self.assertEqual(self.creates(), [a.title for a in adds[:adds.index(target)]])
        self.assertIn("changed", logs.records[-1].getMessage())

    def test_something_started_before_the_confirm_says_the_list_changed(self) -> None:
        dialog = self.open_dialog()
        self.c._jobs["elsewhere"] = "working"
        dialog.add_button.click()
        settle()
        self.c._jobs.pop("elsewhere", None)
        self.assertIsNone(self.c._batch)
        self.assertEqual(self.creates(), [])
        self.assertIn(ui.BATCH_CHANGED_TEXT, " ".join(self.reading.activity.entries()[0]))

    def test_a_standing_reply_check_keeps_add_all_waiting(self) -> None:
        from types import SimpleNamespace

        saved = getattr(self.c, "standing", None)
        self.c.standing = SimpleNamespace(_reply_checking={"r1": (1, 0.0)})
        try:
            self.assertTrue(self.c._batch_blocked())
            self.c._refresh_actions_ui()
            settle()
            self.assertEqual(self.stage.batch_bar.state(), approvals_view.BATCH_HIDDEN)
        finally:
            self.c.standing = saved


if __name__ == "__main__":
    unittest.main()
