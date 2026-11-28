"""Standing runs in LIVE, and the ACTIVITY rows' columns.

A standing run happens in the background check, outside Jarvis's process, so LIVE cannot watch it as it goes: each run
file read for the first time since Jarvis started goes to LIVE once, as a finished task at the run's own time and
length, with its steps from the file (what started it, what it read, the run, what it made). On the Agents screen the
ACTIVITY rows' times, badges and Undo buttons each stand in one column. Qt runs offscreen; nothing reaches Google or
claude.exe, and no process is started.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from briefing_reader import live, standing_ui, ui
from tests.standing_fakes import (AGENT_ID, AGENT_NAME, REPORT, RULE_NAME, card_receipt, tell_receipt, todo_record,
                                  write_run)
from tests.test_standing_app import _Base
from tests.ui_fakes import NOW, fonts, settle

_app: QApplication | None = None

AFTER = NOW + timedelta(minutes=2)          # a run that starts after Jarvis did (the harness's clock is NOW)


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_standing_live", "-platform", "offscreen"])
    fonts()


class StandingRunsInLiveTests(_Base):

    def standing_tasks(self) -> list[live.TaskView]:
        return [task for task in self.c.live.snapshot() if task.kind == live.TASK_STANDING]

    def test_a_rules_run_goes_to_live_once_with_its_steps(self) -> None:
        todo = todo_record()
        write_run(self.app.config, started=AFTER, run_cards=[todo], receipts=[card_receipt(todo)])
        self.s.reload()
        self.s.reload()                                                     # read again: still told once
        task, = self.standing_tasks()
        self.assertEqual(task.title, f"Rule: {RULE_NAME}")
        self.assertEqual(task.tag, "AGENTS")
        self.assertEqual(task.status, live.STATUS_OK)
        self.assertEqual([step.kind for step in task.steps], [live.STANDING_START, live.STANDING_READ,
                                                             live.STANDING_RUN, live.STANDING_MADE])
        start = {field.label: field.value for field in task.steps[0].fields}
        self.assertEqual((start["Started by"], start["Checked by"]), ("New mail", "The background check (Windows)"))
        self.assertEqual({field.label: field.value for field in task.steps[1].fields}["Mail"], "1 new mail")
        made = task.steps[3]
        self.assertEqual([(item.text, item.status_text) for item in made.items], [("To-do: Pay $40.00", "WAITING")])
        self.assertIn("1 thing to show you", task.summary)

    def test_its_time_and_length_are_the_runs_not_the_moment_it_was_told(self) -> None:
        write_run(self.app.config, started=AFTER, receipts=[tell_receipt()])
        self.s.reload()
        task, = self.standing_tasks()
        self.assertEqual(task.at, AFTER)
        self.assertAlmostEqual(task.elapsed(0.0), 40.0, delta=1.0)          # write_run's runs take 40 s
        run_step = task.steps[2]
        self.assertAlmostEqual(run_step.elapsed(0.0), 40.0, delta=1.0)
        self.assertTrue(all(step.at == AFTER for step in task.steps))

    def test_a_run_from_before_this_start_is_not_told(self) -> None:
        write_run(self.app.config, started=NOW - timedelta(minutes=30), receipts=[tell_receipt()])
        self.s.reload()
        self.assertEqual(self.standing_tasks(), [])
        self.assertEqual(len(self.c.window.reading.agents_stage.activity_rows()), 1)   # the screen still shows it

    def test_a_failed_run_says_why_and_made_nothing(self) -> None:
        write_run(self.app.config, started=AFTER, status="failed", needs_owner=False, problem="timeout")
        self.s.reload()
        task, = self.standing_tasks()
        self.assertEqual(task.status, live.STATUS_FAILED)
        self.assertEqual([step.kind for step in task.steps], [live.STANDING_START, live.STANDING_READ,
                                                             live.STANDING_RUN])
        self.assertEqual({field.label: field.value for field in task.steps[2].fields}["Why"], "timeout")
        self.assertEqual(task.steps[2].status, live.STATUS_FAILED)

    def test_a_run_that_could_not_run_has_no_run_step(self) -> None:
        write_run(self.app.config, started=AFTER, status="signed_out", trigger="manual", origin="now",
                  needs_owner=False, counts={"mails": 0})
        self.s.reload()
        task, = self.standing_tasks()
        self.assertEqual(task.status, live.STATUS_WARN)
        self.assertEqual([step.kind for step in task.steps], [live.STANDING_START, live.STANDING_READ])
        start = {field.label: field.value for field in task.steps[0].fields}
        self.assertEqual((start["Started by"], start["Checked by"]), ("Run now", "Your Run now"))
        self.assertEqual(task.steps[1].summary, standing_ui.RUN_LINES["signed_out"])
        self.assertEqual(task.steps[1].status_text, "NOT READ")

    def test_an_agents_report_shows_its_summary(self) -> None:
        write_run(self.app.config, started=AFTER, job_kind="agent", job_id=AGENT_ID, job_name=AGENT_NAME,
                  report=REPORT, needs_owner=False)
        self.s.reload()
        task, = self.standing_tasks()
        self.assertEqual(task.title, f"Agent: {AGENT_NAME}")
        made = {field.label: field.value for field in task.steps[3].fields}
        self.assertEqual(made["Report"], "Two mails from Pat")

    def test_a_standing_run_never_opens_live_by_itself(self) -> None:
        self.assertNotIn(live.TASK_STANDING, live.ATTENTION_KINDS)
        self.assertIn(live.TASK_STANDING, live.TASK_KINDS)
        for kind in live.STANDING_STEP_KINDS:
            self.assertIn(kind, live.STEP_KINDS)
        write_run(self.app.config, started=AFTER, receipts=[tell_receipt()])
        self.s.reload()
        settle()
        self.assertNotEqual(self.c.window.reading.current_tab(), ui.SCREEN_LIVE)
        self.assertEqual(self.c.live.running(), 0)


class ActivityColumnsTests(_Base):

    def test_badges_and_undo_stand_in_one_column(self) -> None:
        todo = todo_record()
        write_run(self.app.config, started=NOW - timedelta(days=1, minutes=30), receipts=[tell_receipt()])
        write_run(self.app.config, status="capped", needs_owner=False, problem="runs",
                  started=NOW - timedelta(hours=2))
        write_run(self.app.config, run_cards=[todo], receipts=[card_receipt(todo)])
        self.s.reload()
        reading = self.c.window.reading
        reading.set_tab(ui.SCREEN_AGENTS)
        settle()
        stage = reading.agents_stage
        rows = stage.activity_rows()
        self.assertEqual([row.info().state_text for row in rows], ["Waiting", "CAPPED", "Told"])
        self.assertEqual([row.info().can_undo for row in rows], [True, False, False])
        lefts = {row.badge.mapTo(stage, QPoint(0, 0)).x() for row in rows}
        self.assertEqual(len(lefts), 1)                                      # one column of badges
        jobs = {row.job_label.mapTo(stage, QPoint(0, 0)).x() for row in rows}
        self.assertEqual(len(jobs), 1)                                       # the names start together too
        slots = {row.undo_slot.width() for row in rows}
        self.assertEqual(len(slots), 1)
        self.assertGreaterEqual(slots.pop(), rows[0].undo_button.sizeHint().width())
        self.assertTrue(rows[1].undo_button.isHidden())                     # its place kept, empty
        self.assertFalse(rows[1].undo_slot.isHidden())

    def test_with_no_undo_on_any_row_no_space_is_kept(self) -> None:
        write_run(self.app.config, receipts=[tell_receipt()])
        self.s.reload()
        reading = self.c.window.reading
        reading.set_tab(ui.SCREEN_AGENTS)
        settle()
        row, = reading.agents_stage.activity_rows()
        self.assertTrue(row.undo_slot.isHidden())


class LiveBackdateTests(unittest.TestCase):

    def test_finish_takes_the_start_and_length_given(self) -> None:
        clock = [100.0]
        stream = live.LiveStream(clock=lambda: clock[0])
        task = stream.task(live.TASK_STANDING, "Rule: x")
        step = task.step(live.STANDING_RUN, "The run")
        step.done(live.STATUS_OK, took_s=12.5)
        at = datetime(2026, 10, 4, 9, 0, tzinfo=NOW.tzinfo)
        task.finish(live.STATUS_OK, at=at, took_s=40)
        view, = stream.snapshot()
        self.assertEqual(view.at, at)
        self.assertAlmostEqual(view.elapsed(clock[0]), 40.0)
        self.assertAlmostEqual(view.steps[0].elapsed(clock[0]), 12.5)
        self.assertEqual(view.steps[0].at, at)

    def test_a_bad_length_is_ignored(self) -> None:
        stream = live.LiveStream(clock=lambda: 50.0)
        task = stream.task(live.TASK_STANDING, "Rule: x")
        for bad in (float("nan"), "12", True, None):
            task.step(live.STANDING_RUN, "The run", key=str(bad)).done(live.STATUS_OK, took_s=bad)
        task.finish(live.STATUS_OK, took_s=-5)
        view, = stream.snapshot()
        self.assertEqual(view.elapsed(50.0), 0.0)
        self.assertTrue(all(step.elapsed(50.0) == 0.0 for step in view.steps))


if __name__ == "__main__":
    unittest.main()
