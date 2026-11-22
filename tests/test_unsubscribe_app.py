"""An UNSUBSCRIBE card in the app (mail extras, part 4): Unsubscribe shows UNSUBSCRIBED with Undo at once and holds the
one POST (the same hidden hold as a Send); Undo during it sends nothing; the hold's end makes exactly one (fake) POST;
Jarvis closing during the hold sends it; a link that fails the check again starts nothing; Add all never includes it.
AppHarness: fake calendars and a fake POST that only record, Qt offscreen; every name, host and id is invented."""

from __future__ import annotations

import time
import unittest

from PySide6.QtWidgets import QApplication

from briefing_reader import actions, approvals_view, executor, hud, ui
from briefing_reader.actions import SOURCE_ASK, STATUS_HELD, STATUS_SENT, make_unsubscribe
from tests.ui_fakes import B_CALENDAR, CAL_LINE, AppHarness, fonts, settle, wait_for

_app: QApplication | None = None
URL = "https://lists.example.com/u/abc123?token=xyz"


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_unsubscribe_app", "-platform", "offscreen"])
    fonts()


def card(url: str = URL) -> actions.ProposedAction:
    return make_unsubscribe("work", thread="thr0000888", gmid="msg0000888", url=url, sender="deals@example.com",
                            list_id="deals.acme.example.com", name="Acme Deals", source=SOURCE_ASK)


class UnsubscribeAppTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = AppHarness(self, ask=False, lines=(B_CALENDAR, CAL_LINE))
        self.app.reading(autoplay=False)
        self.c, self.reading = self.app.c, self.app.c.window.reading
        self.reading.set_tab(ui.SCREEN_APPROVALS)
        self.posts: list[str] = []
        backend = next(item for item in self.c._executor._backends if isinstance(item, executor.UnsubscribeBackend))
        backend._post = self.post
        backend._resolve = lambda _host: ["93.184.216.34"]
        self.unsub = card()
        self.c._set_source(SOURCE_ASK, [self.unsub])
        self.c._accounts_state["personal"] = (True, "", "")
        self.c._refresh_actions_ui()
        settle()

    def post(self, url: str, timeout: float, cap: int) -> int:
        self.posts.append(url)
        return 200

    def approve(self) -> hud.ActionCard:
        found = self.reading.action_card(self.unsub.id)
        self.assertIsNotNone(found)
        self.assertEqual(found.approve_text(), "Unsubscribe")
        found.approve_button.click()
        settle()
        return found

    def end_hold(self) -> None:
        self.assertIsNotNone(self.c._send_hold)
        self.c._send_hold.deadline = time.monotonic() - 0.001
        self.c._on_send_hold_tick()

    def wait_done(self) -> None:
        self.assertTrue(wait_for(lambda: self.unsub.id not in self.c._jobs and self.unsub.id not in self.c._running,
                                 10))
        settle()


class HoldTests(UnsubscribeAppTestCase):
    def test_unsubscribe_shows_unsubscribed_with_undo_and_holds_the_post(self) -> None:
        shown = self.approve()
        self.assertIsNotNone(self.c._send_hold)
        self.assertEqual(self.app.store.get(self.unsub.id)["status"], STATUS_HELD)
        self.assertEqual(shown.status(), hud.CARD_SENT)
        self.assertIn("UNSUBSCRIBED", shown.result_text())
        self.assertEqual(shown.undo_text(), "Undo")
        self.assertEqual(self.posts, [])                         # nothing before the hold ends
        self.end_hold()
        self.wait_done()
        self.assertEqual(self.posts, [URL])                      # exactly one POST
        self.assertEqual(self.app.store.get(self.unsub.id)["status"], STATUS_SENT)
        self.assertIn("UNSUBSCRIBED", self.reading.action_card(self.unsub.id).result_text())

    def test_undo_during_the_hold_sends_nothing(self) -> None:
        self.approve()
        self.c._send_hold.started -= 5                           # past the double-click guard
        self.reading.action_card(self.unsub.id).undo_button.click()
        settle()
        self.assertIsNone(self.c._send_hold)
        self.assertIsNone(self.app.store.get(self.unsub.id))
        time.sleep(0.2)
        settle()
        self.assertEqual(self.posts, [])
        self.assertEqual(self.reading.action_card(self.unsub.id).status(), hud.CARD_PENDING)

    def test_closing_during_the_hold_sends_it(self) -> None:
        self.approve()
        flushed = self.c._flush_send_hold()
        self.assertIsNotNone(flushed)
        self.assertTrue(flushed.wait(10))
        self.assertEqual(self.posts, [URL])

    def test_a_link_that_fails_the_check_again_starts_nothing(self) -> None:
        bad = card("https://127.0.0.1/u")
        self.c._set_source(SOURCE_ASK, [bad])
        settle()
        self.c.approve_action(bad.id)
        settle()
        self.assertIsNone(self.c._send_hold)
        self.assertEqual(self.posts, [])

    def test_add_all_never_includes_it(self) -> None:
        listed = self.c._batch_candidates()
        self.assertNotIn(self.unsub.id, {action.id for action in listed})
        self.assertEqual(len(listed), 2)                          # the two calendar cards
        self.assertEqual(self.reading.approvals_stage.batch_bar.state(), approvals_view.BATCH_IDLE)
        self.assertEqual(self.reading.approvals_stage.batch_bar.count(), 2)

    def test_the_activity_rows_say_unsubscribe(self) -> None:
        """Fix round 1 (low 5): never "Cancel ..." for an Unsubscribe."""
        self.post = lambda url, timeout, cap: 404       # the list's server refuses it
        backend = next(item for item in self.c._executor._backends if isinstance(item, executor.UnsubscribeBackend))
        backend._post = self.post
        self.approve()
        rows = [" ".join(entry) for entry in self.reading.activity.entries()]
        self.assertIn("Sending: Unsubscribe from Acme Deals", rows[0])
        self.end_hold()
        self.wait_done()
        rows = " | ".join(" ".join(entry) for entry in self.reading.activity.entries())
        self.assertIn("Couldn't unsubscribe from Acme Deals", rows)
        self.assertNotIn("Cancel", rows)


if __name__ == "__main__":
    unittest.main()
