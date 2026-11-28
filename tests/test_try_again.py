"""A failed frame's Try again (the Try again brief): the budget's one owner-clicked retry per failed page picture, the
snapshotter taking the same page again, the board's retry request (its strict checks and its rate), the frame that
offers it, and the app's wiring from the page's click to the frame updated in place.

Offscreen; no web engine, no page, no network: the snapshotter shoots with tests.test_page_snapshotter's FakeCamera and
the board is tests.board_fakes' stand-in (tests/test_board_lockdown.py's "shell" case presses the real page's button).
Every address is invented.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from types import SimpleNamespace

import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import live, pictures, snapshot_engine, snapshots, ui
from briefing_reader.board import feed as feed_mod
from briefing_reader.board import frames as board_frames
from briefing_reader.board import protocol, view
from tests import board_fakes
from tests.test_page_snapshotter import FakeCamera, SnapshotterCase
from tests.ui_fakes import AppHarness, fonts, settle, wait_for

_app: QApplication | None = None
PAGE = "https://www.example.org/report"
OTHER = "https://news.example.net/story"


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_try_again", "-platform", "offscreen"])
    fonts()


def failed_frame(frame_id: str = "f1", **fields: object) -> dict:
    message = {"type": "frame", "id": frame_id, "kind": "picture", "title": "Page: example.org",
               "body": {"caption": "example.org"}, "status": "failed",
               "status_text": "Picture unavailable: the page did not load within 15 s", "retry": True}
    message.update(fields)
    return message


# --------------------------------------------------------------------------
# The budget's rule
# --------------------------------------------------------------------------

class BudgetTests(unittest.TestCase):
    def test_one_retry_per_failed_page_and_no_more(self) -> None:
        budget = snapshots.RunBudget()
        self.assertEqual(budget.admit(7, PAGE), "")
        self.assertEqual(budget.retry(7, PAGE), "")                       # the owner's one Try again
        self.assertEqual(budget.retry(7, PAGE), snapshots.R_RETRIED)      # never a second
        self.assertEqual(budget.retry(7, PAGE + "#part"), snapshots.R_RETRIED)   # the same page (fragment aside)
        self.assertEqual(snapshots.MAX_RETRIES, 1)

    def test_every_other_limit_holds(self) -> None:
        budget = snapshots.RunBudget()
        self.assertEqual(budget.admit(7, PAGE, 2), "")
        self.assertEqual(budget.retry(7, PAGE), "")
        self.assertEqual(budget.admit(7, PAGE, 2), snapshots.R_SAME_PAGE)   # a research's own second ask: still no
        self.assertEqual(budget.taken(7), 1)                                # a retry takes no place under the cap
        self.assertEqual(budget.admit(7, OTHER, 2), "")
        self.assertEqual(budget.admit(7, "https://third.example.com/", 2), snapshots.over_cap_reason(2))
        self.assertEqual(budget.retry(7, "https://never.example.com/"), snapshots.R_NO_RETRY)   # never admitted
        budget.cancel(7)
        self.assertEqual(budget.retry(7, OTHER), snapshots.R_CANCELLED)

    def test_a_run_let_go_is_held_again_ended(self) -> None:
        budget = snapshots.RunBudget()
        budget.admit(9, PAGE)
        budget.end(9)
        budget.forget(9)                                                    # its view closed
        self.assertEqual(budget.retry(9, PAGE), "")
        self.assertTrue(budget.ended(9))                                    # it asks no more pages of its own
        self.assertEqual(budget.retry(9, PAGE), snapshots.R_RETRIED)

    def test_only_passing_failures_are_retryable(self) -> None:
        for reason in (snapshots.R_TIMEOUT, snapshots.R_LOAD_FAILED, snapshots.R_BLANK, snapshots.R_CRASHED):
            self.assertTrue(snapshots.retryable(reason), reason)
        for reason in (snapshots.R_SITE_REFUSED, snapshots.R_SAME_PAGE, snapshots.R_OVER_CAP, snapshots.R_SEARCH_PAGE,
                       snapshots.R_NOT_PUBLIC_HTTPS, snapshots.R_TEXT_OFF, snapshots.R_CANCELLED, snapshots.R_TOO_LARGE,
                       snapshots.R_SHUTDOWN, snapshots.R_SANDBOX_OFF, snapshots.R_UNSAFE_FLAGS, snapshots.R_TEST_RUN,
                       snapshots.R_NO_ENGINE, snapshots.R_ENGINE_FAILED, snapshots.R_RETRIED, "", None, 5):
            self.assertFalse(snapshots.retryable(reason), reason)
        self.assertEqual(snapshots.reason_kind(snapshots.R_RETRIED), "retried")
        self.assertEqual(snapshots.reason_kind(snapshots.R_NO_RETRY), "no_retry")


# --------------------------------------------------------------------------
# The snapshotter: the same page again on the owner's click
# --------------------------------------------------------------------------

class SnapshotterRetryTests(SnapshotterCase):
    def failed(self, snapper: snapshot_engine.PageSnapshotter, reason: str = snapshots.R_TIMEOUT) -> live.LiveStep:
        step = self.step()
        snapper.request(step, PAGE, task_id=self.task.id)
        self.assertTrue(self.shots(1))
        self.camera.complete(ok=False, reason=reason)
        settle()
        return step

    def test_a_timeout_offers_try_again_and_the_retry_takes_the_same_page(self) -> None:
        snapper = self.make()
        step = self.failed(snapper)
        picture = self.picture(step)
        self.assertEqual(picture.state, live.PICTURE_UNAVAILABLE)
        self.assertTrue(picture.page.retry)
        self.assertIn(snapshots.R_TIMEOUT, picture.note)
        self.assertEqual(snapper.retry(step.id), "")
        self.assertEqual(self.state(step), live.PICTURE_PENDING)            # "Opening..." at once
        self.assertTrue(self.shots(2))
        self.assertEqual(self.camera.shots[1][0], PAGE)                     # the same page, nothing else
        self.camera.complete(ok=True)
        settle()
        self.assertEqual(self.state(step), live.PICTURE_READY)
        self.assertEqual(snapper.retry_counts(), {"offered": 1, "taken": 1, "refused": 0})

    def test_fails_twice_the_reason_shows_and_there_is_no_third_try(self) -> None:
        snapper = self.make()
        step = self.failed(snapper)
        self.assertEqual(snapper.retry(step.id), "")
        self.assertTrue(self.shots(2))
        self.camera.complete(ok=False, reason=snapshots.R_LOAD_FAILED)
        settle()
        picture = self.picture(step)
        self.assertEqual(picture.state, live.PICTURE_UNAVAILABLE)
        self.assertIn(snapshots.R_LOAD_FAILED, picture.note)
        self.assertFalse(picture.page.retry)                               # no other Try again
        self.assertEqual(snapper.retry(step.id), snapshots.R_NO_RETRY)
        settle(3)
        self.assertEqual(len(self.camera.shots), 2)                          # never a third shot

    def test_a_failure_a_retry_cannot_change_offers_none(self) -> None:
        snapper = self.make()
        step = self.failed(snapper, snapshots.R_SITE_REFUSED)
        self.assertFalse(self.picture(step).page.retry)
        self.assertIn(snapshots.R_SITE_REFUSED, self.picture(step).note)  # its reason words kept
        self.assertEqual(snapper.retry(step.id), snapshots.R_NO_RETRY)
        refused = self.step()
        snapper.request(refused, "https://www.google.com/search?q=x", task_id=self.task.id)   # a rule's refusal
        self.assertFalse(self.picture(refused).page.retry)
        self.assertEqual(snapper.retry(refused.id), snapshots.R_NO_RETRY)
        settle(3)
        self.assertEqual(len(self.camera.shots), 1)

    def test_hostile_step_ids_are_refused(self) -> None:
        snapper = self.make()
        step = self.failed(snapper)
        for bad in (None, "1", str(step.id), 1.0 * step.id, -1, 0, step.id + 1000, True):
            self.assertEqual(snapper.retry(bad), snapshots.R_NO_RETRY, repr(bad))
        self.assertEqual(snapper.retry(step.id), "")                        # the real one still works, once

    def test_a_cancel_takes_the_offer_away(self) -> None:
        snapper = self.make()
        step = self.failed(snapper)
        snapper.cancel_task(self.task.id)                                   # the owner cancelled the research
        settle()
        self.assertEqual(snapper.retry(step.id), snapshots.R_NO_RETRY)

    def test_live_s_clear_takes_the_offer_away(self) -> None:
        snapper = self.make()
        step = self.failed(snapper)
        self.task.finish(live.STATUS_OK)
        self.stream.clear()                                                 # LIVE's Clear: the step went
        snapper.drop_cleared()
        self.assertIsNone(step.view())
        self.assertEqual(snapper.retry(step.id), snapshots.R_NO_RETRY)

    def test_shutdown_takes_the_offer_away(self) -> None:
        snapper = self.make()
        step = self.failed(snapper)
        snapper.shutdown()                                                  # Jarvis closes
        self.assertEqual(snapper.retry(step.id), snapshots.R_NO_RETRY)
        self.assertEqual(snapper.retry_counts()["taken"], 0)

    def test_text_off_after_the_failure_refuses_the_retry_with_its_reason(self) -> None:
        snapper = self.make()
        step = self.failed(snapper)
        snapper._keep_text = False                                          # noqa: SLF001 - [live] text turned off
        self.assertEqual(snapper.retry(step.id), snapshots.R_TEXT_OFF)
        self.assertIn(snapshots.R_TEXT_OFF, self.picture(step).note)
        self.assertFalse(self.picture(step).page.retry)
        settle(3)
        self.assertEqual(len(self.camera.shots), 1)

    def test_the_cap_still_holds_with_a_retry(self) -> None:
        snapper = self.make(max_per_run=1)
        step = self.failed(snapper)
        self.assertEqual(snapper.retry(step.id), "")
        late = self.step()
        snapper.request(late, OTHER, task_id=self.task.id)
        self.assertIn(snapshots.over_cap_reason(1), self.picture(late).note)


# --------------------------------------------------------------------------
# The frame and the board's request
# --------------------------------------------------------------------------

class FrameTests(unittest.TestCase):
    def test_a_failed_page_picture_offers_try_again_only_when_its_picture_says_so(self) -> None:
        now = datetime(2026, 11, 28, 9, 41)
        offered = board_frames.from_picture(pictures.page_unavailable(PAGE, snapshots.R_TIMEOUT, retry=True),
                                            frame_id="f1", now=now)
        frame = offered.messages[0]
        self.assertEqual((frame["status"], frame.get("retry")), ("failed", True))
        self.assertIn(snapshots.R_TIMEOUT, frame["status_text"])
        self.assertTrue(json.loads(protocol.check_message(frame).text)["retry"])
        plain = board_frames.from_picture(pictures.page_unavailable(PAGE, snapshots.R_SITE_REFUSED),
                                          frame_id="f2", now=now).messages[0]
        self.assertNotIn("retry", plain)
        self.assertIn(snapshots.R_SITE_REFUSED, plain["status_text"])
        pending = board_frames.from_picture(pictures.page_pending(PAGE), frame_id="f3", now=now).messages[0]
        self.assertNotIn("retry", pending)

    def test_the_frame_s_retry_flag_is_checked(self) -> None:
        self.assertTrue(json.loads(protocol.check_message(failed_frame()).text)["retry"])
        self.assertFalse(json.loads(protocol.check_message(failed_frame(retry=False, status="ok")).text)["retry"])
        for bad, reason in (({"status": "ok"}, protocol.R_FIELD), ({"status": "empty"}, protocol.R_FIELD),
                            ({"retry": "yes"}, protocol.R_SHAPE), ({"retry": 1}, protocol.R_SHAPE),
                            ({"retry": None}, protocol.R_SHAPE)):
            with self.assertRaises(protocol.Refused) as caught:
                protocol.check_message(failed_frame(**bad))
            self.assertEqual(caught.exception.reason, reason, repr(bad))


class RequestTests(unittest.TestCase):
    known = frozenset({"f1", "f2"})
    retryable = frozenset({"f1"})

    def refused(self, payload: object, retryable: frozenset = retryable) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_request("retry", payload, self.known, retryable)
        return caught.exception.reason

    def test_a_retry_names_a_frame_that_offers_try_again(self) -> None:
        self.assertEqual(protocol.check_request("retry", '{"id":"f1"}', self.known, self.retryable),
                         protocol.Request("retry", "f1"))
        self.assertEqual(protocol.kind_word("retry"), "retry")

    def test_hostile_shapes_are_refused(self) -> None:
        self.assertEqual(self.refused('{"id":"f2"}'), protocol.R_ID)                  # known, offers no Try again
        self.assertEqual(self.refused('{"id":"f1"}', frozenset()), protocol.R_ID)      # the default: nothing offers
        with self.assertRaises(protocol.Refused):
            protocol.check_request("retry", '{"id":"f1"}', self.known)                # no retryable given
        self.assertEqual(self.refused('{"id":"f9"}', frozenset({"f9"})), protocol.R_ID)   # not a frame the app sent
        self.assertEqual(self.refused('{"id":"../f1"}'), protocol.R_ID)
        self.assertEqual(self.refused('{"id":1}'), protocol.R_ID)
        self.assertEqual(self.refused('{"id":["f1"]}'), protocol.R_ID)
        self.assertEqual(self.refused("{}"), protocol.R_FIELD)
        self.assertEqual(self.refused('{"id":"f1","url":"https://evil.example.com/"}'), protocol.R_FIELD)
        self.assertEqual(self.refused('{"id":"f1","step":3}'), protocol.R_FIELD)
        self.assertEqual(self.refused('["f1"]'), protocol.R_SHAPE)
        self.assertEqual(self.refused({"id": "f1"}), protocol.R_SHAPE)
        self.assertEqual(self.refused("not json"), protocol.R_JSON)
        self.assertEqual(self.refused('{"id": NaN}'), protocol.R_JSON)
        self.assertEqual(self.refused('{"id":"f1"}' + " " * protocol.PAYLOAD_CAP), protocol.R_SIZE)
        self.assertIn(self.refused("[" * 2000 + "]" * 2000), (protocol.R_JSON, protocol.R_SHAPE))
        with self.assertRaises(protocol.Refused):
            protocol.check_request("RETRY", '{"id":"f1"}', self.known, self.retryable)


class BridgeTests(unittest.TestCase):
    def make(self, retryable: set[str]) -> tuple[view.BoardBridge, list, list]:
        delivered: list[protocol.Request] = []
        clock = [100.0]
        bridge = view.BoardBridge(known=lambda: {"f1", "f2"}, deliver=delivered.append, clock=lambda: clock[0],
                                  retryable=lambda: retryable)
        self.addCleanup(lambda: shiboken6.isValid(bridge) and shiboken6.delete(bridge))
        return bridge, delivered, clock

    def test_only_an_offered_frame_s_retry_passes_and_at_its_own_rate(self) -> None:
        bridge, delivered, clock = self.make({"f1"})
        bridge.post("retry", '{"id":"f2"}')
        self.assertEqual(delivered, [])
        self.assertEqual(bridge.counts()["why:id"], 1)
        clock[0] += 1.0                                                      # (that post took its place in the rate)
        for _ in range(view.RETRIES_PER_S + 3):
            bridge.post("retry", '{"id":"f1"}')
        self.assertEqual(delivered, [protocol.Request("retry", "f1")] * view.RETRIES_PER_S)
        self.assertEqual(bridge.counts()["why:rate"], 3)
        bridge.post("focus", '{"id":"f1"}')                                 # other kinds still pass
        self.assertEqual(delivered[-1], protocol.Request("focus", "f1"))
        clock[0] += 1.0
        bridge.post("retry", '{"id":"f1"}')
        self.assertEqual(len(delivered), view.RETRIES_PER_S + 2)


class ViewTests(unittest.TestCase):
    def test_one_retry_per_offer_and_the_offer_follows_the_frame(self) -> None:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        board = view.BoardView(host, start_delay_ms=10 ** 6, scheme_ready=lambda: True)
        self.addCleanup(board.shutdown)
        asked: list[str] = []
        board.retryRequested.connect(asked.append)
        self.assertTrue(board.apply(failed_frame("f1")))
        self.assertTrue(board.apply(failed_frame("f2", retry=False)))
        self.assertEqual(board.retryable_ids(), frozenset({"f1"}))
        board._on_request(protocol.Request("retry", "f1"))                  # noqa: SLF001 - as the bridge delivers
        board._on_request(protocol.Request("retry", "f1"))                  # noqa: SLF001 - a second press
        self.assertEqual(asked, ["f1"])
        self.assertEqual(board.counts()["dropped:retry_again"], 1)
        self.assertEqual(board.retryable_ids(), frozenset())
        board.apply(failed_frame("f1"))                                      # offered again (a new failure)
        self.assertEqual(board.retryable_ids(), frozenset({"f1"}))
        board.apply(failed_frame("f1", retry=False))                         # sent again without it
        self.assertEqual(board.retryable_ids(), frozenset())
        board.apply(failed_frame("f1"))
        board.apply({"type": "remove", "id": "f1"})
        self.assertEqual(board.retryable_ids(), frozenset())


class FeedTests(unittest.TestCase):
    def test_the_source_to_retry_and_dropping_the_offer(self) -> None:
        feed = feed_mod.BoardFeed()
        feed.put(1, "step:5", [failed_frame("f1")], when="9:41 AM")
        feed.put(1, "step:6", [failed_frame("f2", retry=False)], when="9:41 AM")
        self.assertEqual(feed.retry_source("f1"), "step:5")
        self.assertEqual(feed.retry_source("f2"), "")
        self.assertEqual(feed.retry_source("nope"), "")
        sent = feed.drop_retry("f1")
        self.assertEqual(len(sent), 1)
        self.assertNotIn("retry", sent[0])
        self.assertEqual(sent[0]["status_text"], failed_frame()["status_text"])   # its reason words kept
        self.assertEqual(feed.retry_source("f1"), "")
        self.assertEqual(feed.drop_retry("f1"), [])                          # nothing changed: nothing sent


# --------------------------------------------------------------------------
# The app's wiring: the page's click -> the same page again -> the frame in place
# --------------------------------------------------------------------------

class DeskRetryTests(unittest.TestCase):
    def make(self) -> None:
        self.boards: list = []
        self.cameras: list[FakeCamera] = []

        def camera(parent: QObject) -> FakeCamera:
            made = FakeCamera(parent)
            self.cameras.append(made)
            return made

        def snapper(controller: QObject) -> snapshot_engine.PageSnapshotter:
            self.snapper = snapshot_engine.PageSnapshotter(controller, camera_factory=camera)
            return self.snapper

        harness = AppHarness(self, board_factory=board_fakes.stand_in_factory(self.boards), snapshot_factory=snapper)
        harness.reading(autoplay=False)
        self.c = harness.c
        self.c.window.resize(1120, 700)
        self.c.window.reading.set_tab(ui.SCREEN_HOME)
        settle(6)

    def shots(self, count: int) -> bool:
        return wait_for(lambda: bool(self.cameras) and len(self.cameras[0].shots) >= count, 5)

    def failed_frame_on_the_board(self) -> tuple[board_fakes.StandInBoard, str]:
        desk = self.c.look.desk
        task = self.c.live.task(live.TASK_ASK, "Ask")
        self.task_id = task.id
        self.step = task.step(live.WEB_FETCH, "Read a page")
        self.snapper.request(self.step, PAGE, task_id=task.id)
        self.assertTrue(self.shots(1))
        self.cameras[0].complete(ok=False, reason=snapshots.R_TIMEOUT)
        settle()
        desk.on_answer_started(task.id, "web: the report")
        self.c._live_feed.drain()
        desk.on_ask_outcome(SimpleNamespace(ok=True, say="", cards=(), research=None, calendar_read=None, mode=""),
                            task.id)
        settle(4)
        board = self.boards[0]
        frame = next(frame for frame in board.frames_sent().values() if frame["kind"] == "picture")
        self.assertEqual((frame["status"], frame.get("retry")), ("failed", True))
        self.assertEqual(board.retryable_ids(), frozenset({frame["id"]}))
        return board, frame["id"]

    def frame(self, board: board_fakes.StandInBoard, frame_id: str) -> dict:
        self.c._live_feed.drain()
        settle()
        return board.frames_sent()[frame_id]

    def test_fails_then_succeeds_the_frame_updates_in_place(self) -> None:
        self.make()
        board, frame_id = self.failed_frame_on_the_board()
        self.assertTrue(board.request("retry", id=frame_id))                 # the page's post, through the checks
        self.assertTrue(self.shots(2))
        self.assertEqual(self.cameras[0].shots[1][0], PAGE)                  # the same page again
        opening = self.frame(board, frame_id)
        self.assertEqual(opening["status"], "empty")                         # "Opening..." in its place
        self.assertNotIn("retry", opening)
        self.cameras[0].complete(ok=True)
        settle()
        done = self.frame(board, frame_id)
        self.assertEqual(done["status"], "ok")
        self.assertEqual(len([frame for frame in board.frames_sent().values() if frame["kind"] == "picture"]), 1)
        self.assertTrue(any(message["id"] == frame_id for message in board.of_type("picture")))
        self.assertEqual(self.c.look.desk.counts["retry:taken"], 1)

    def test_fails_twice_the_reason_shows_and_no_third_try(self) -> None:
        self.make()
        board, frame_id = self.failed_frame_on_the_board()
        self.assertTrue(board.request("retry", id=frame_id))
        self.assertTrue(self.shots(2))
        self.cameras[0].complete(ok=False, reason=snapshots.R_LOAD_FAILED)
        settle()
        again = self.frame(board, frame_id)
        self.assertEqual(again["status"], "failed")
        self.assertIn(snapshots.R_LOAD_FAILED, again["status_text"])
        self.assertNotIn("retry", again)                                     # no button
        self.assertFalse(board.request("retry", id=frame_id))                # the checks refuse a third
        self.c.look.desk._on_retry(frame_id)                                 # noqa: SLF001 - even straight in
        settle(3)
        self.assertEqual(len(self.cameras[0].shots), 2)
        self.assertEqual(self.snapper.retry_counts()["taken"], 1)

    def test_a_retry_that_can_t_be_taken_keeps_the_words_and_drops_the_button(self) -> None:
        self.make()
        board, frame_id = self.failed_frame_on_the_board()
        words = board.frames_sent()[frame_id]["status_text"]
        self.snapper.cancel_task(self.task_id)                               # the research was cancelled meanwhile
        settle()
        self.assertTrue(board.request("retry", id=frame_id))                 # the button was still on the page
        settle()
        after = board.frames_sent()[frame_id]
        self.assertEqual(after["status_text"], words)
        self.assertNotIn("retry", after)
        self.assertEqual(board.retryable_ids(), frozenset())
        self.assertEqual(self.c.look.desk.counts["retry:refused"], 1)
        self.assertEqual(len(self.cameras[0].shots), 1)


if __name__ == "__main__":
    unittest.main()
