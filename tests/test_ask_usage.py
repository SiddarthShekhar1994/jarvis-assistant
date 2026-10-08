"""Tests for briefing_reader.ask.usage: the hourly and daily caps on a fake clock."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from briefing_reader.ask.usage import UsageLog
from tests.ask_fakes import NOW

LOGGER = "briefing_reader.ask.usage"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class UsageTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "data" / "ask_usage.json"
        self.clock = Clock()

    def log(self, **kwargs: int) -> UsageLog:
        return UsageLog(self.path, clock=self.clock, **kwargs)

    def spend(self, usage: UsageLog, count: int, step: timedelta = timedelta(minutes=1)) -> None:
        for _ in range(count):
            run = usage.start()
            usage.finish(run, outcome="ok", duration_ms=8000, turns=2, input_tokens=5000, output_tokens=400)
            self.clock.now += step

    def test_the_21st_run_in_an_hour_is_refused(self) -> None:
        usage = self.log(max_per_hour=20, max_per_day=60)
        start = self.clock.now
        self.spend(usage, 20)
        check = usage.check()
        self.assertFalse(check.allowed)
        self.assertEqual((check.left_hour, check.left_day), (0, 40))
        self.assertEqual(check.retry_at, start + timedelta(hours=1))
        self.assertEqual(check.message, "Ask limit reached; try again after 10:30 AM "
                                        "(20 planner runs an hour: [ask] max_per_hour)")
        self.clock.now = start + timedelta(hours=1, seconds=1)
        self.assertTrue(usage.check().allowed)

    def test_the_61st_run_in_a_day_is_refused(self) -> None:
        usage = self.log(max_per_hour=20, max_per_day=60)
        self.spend(usage, 60, step=timedelta(minutes=10))
        check = usage.check()
        self.assertFalse(check.allowed)
        self.assertIn("Ask's daily limit reached; try again after 9:30 AM ", check.message)
        self.assertIn("(60 planner runs a day: [ask] max_per_day)", check.message)
        self.assertEqual(check.left_day, 0)

    def test_two_runs_needed_for_mail(self) -> None:
        usage = self.log(max_per_hour=3, max_per_day=60)
        self.spend(usage, 2)
        self.assertTrue(usage.check(runs=1).allowed)
        self.assertFalse(usage.check(runs=2).allowed)

    def test_saved_counts_only_and_reloaded(self) -> None:
        usage = self.log()
        self.spend(usage, 3)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["runs"]), 3)
        self.assertEqual(set(data["runs"][0]), {"id", "at", "outcome", "duration_ms", "turns", "input_tokens",
                                                "output_tokens", "cache_read_tokens", "cache_write_tokens"})
        self.assertRegex(data["runs"][0]["id"], r"^[0-9a-f]{16}$")   # random: never the request
        self.assertNotIn("hold_until", data)
        self.assertEqual(self.path.read_bytes().count(b"\r"), 0)
        self.assertEqual(self.log().counts(), (3, 3))

    def test_a_started_run_counts_even_if_it_never_finishes(self) -> None:
        usage = self.log(max_per_hour=1)
        usage.start()
        self.assertFalse(self.log(max_per_hour=1).check().allowed)

    def test_old_entries_are_pruned(self) -> None:
        usage = self.log()
        self.spend(usage, 2)
        self.clock.now += timedelta(days=3)
        usage.start()
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))["runs"]), 1)

    def test_unreadable_file_counts_as_empty(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertLogs(LOGGER, level="WARNING"):
            self.assertTrue(self.log().check().allowed)
        self.path.write_text(json.dumps({"runs": [{"at": "yesterday"}, {"at": NOW.isoformat(), "turns": -3},
                                                  {"at": "2026-10-07T09:00:00"}, "junk"]}), encoding="utf-8")
        self.assertEqual(self.log().counts(), (1, 1))

    def test_failed_write_keeps_counting(self) -> None:
        usage = self.log(max_per_hour=2)
        with mock.patch("briefing_reader.ask.usage.tempfile.mkstemp", side_effect=OSError(13, "denied")):
            with self.assertLogs(LOGGER, level="WARNING"):
                self.spend(usage, 2)
        self.assertFalse(usage.check().allowed)

    def test_runs_from_another_process_count_and_survive(self) -> None:
        """--ask-text from the command line while the app is open: each is a UsageLog of its own."""
        app = self.log(max_per_hour=20)
        self.assertTrue(app.check().allowed)
        for _ in range(20):
            other = self.log(max_per_hour=20)
            other.finish(other.start(), outcome="ok", duration_ms=1000)
        check = app.check()
        self.assertFalse(check.allowed)
        self.assertEqual(check.left_hour, 0)
        self.assertEqual(self.log().counts(), (20, 20))
        self.clock.now += timedelta(hours=1, seconds=1)
        app.finish(app.start(), outcome="ok")
        self.assertEqual(self.log().counts(), (1, 21))   # the app's write kept the other runs
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len({run["id"] for run in data["runs"]}), 21)
        self.assertEqual([run["outcome"] for run in data["runs"]], ["ok"] * 21)

    def test_two_logs_interleaved_never_drop_a_run(self) -> None:
        first, second = self.log(), self.log()
        a = first.start()
        b = second.start()
        first.finish(a, outcome="ok", turns=2)
        second.finish(b, outcome="limit", turns=1)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(sorted((run["outcome"], run["turns"]) for run in data["runs"]), [("limit", 1), ("ok", 2)])

    def test_the_lock_file_is_held_across_processes(self) -> None:
        from briefing_reader.ask.usage import _FileLock

        lock = self.path.with_name("ask_usage.json.lock")
        with _FileLock(lock) as held:
            self.assertTrue(held.locked)
            with _FileLock(lock, timeout=0.1) as other:
                self.assertFalse(other.locked)   # the second waits, then goes on without it
        with _FileLock(lock, timeout=0.1) as again:
            self.assertTrue(again.locked)

    def test_hold_pauses_every_process_until_it_ends(self) -> None:
        usage = self.log()
        until = self.clock.now + timedelta(hours=3)
        self.assertEqual(usage.hold(until), until)
        for log in (usage, self.log()):
            check = log.check()
            self.assertFalse(check.allowed)
            self.assertTrue(check.held)
            self.assertEqual(check.retry_at, until)
            self.assertEqual(check.message, "Ask is paused until 12:30 PM: turn usage credits off at "
                                            "claude.ai/settings/usage (Claude Code said a request would use them)")
        self.assertEqual(self.log().hold_until(), until)
        self.assertIn("hold_until", json.loads(self.path.read_text(encoding="utf-8")))
        self.clock.now = until + timedelta(seconds=1)
        self.assertTrue(usage.check().allowed)
        self.assertIsNone(self.log().hold_until())

    def test_hold_is_capped_and_named_by_weekday_when_far(self) -> None:
        usage = self.log()
        until = usage.hold(self.clock.now + timedelta(days=30))
        self.assertEqual(until, self.clock.now + timedelta(days=8))
        self.assertIn("paused until Thu 9:30 AM", usage.check().message)

    def test_hold_survives_a_failed_write(self) -> None:
        usage = self.log()
        with mock.patch("briefing_reader.ask.usage.tempfile.mkstemp", side_effect=OSError(13, "denied")):
            with self.assertLogs(LOGGER, level="WARNING"):
                usage.hold(self.clock.now + timedelta(hours=1))
        self.assertFalse(usage.check().allowed)


if __name__ == "__main__":
    unittest.main()
