"""Tests for standing.schedule: a timed job is a baseline when first seen, due at its slot (also a little late),
missed when older than the catch-up window, kept to its weekdays, keyed by local date and wall-clock time so a
daylight-saving night neither skips nor repeats a run; an interval job runs once after a long sleep; the next run
to show; quiet hours across midnight. Times are naive local wall-clock moments here."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from briefing_reader.standing import schedule
from briefing_reader.standing.model import TRIGGER_EVERY, TRIGGER_MAIL, TRIGGER_TIME, Trigger

WEEKDAYS_8 = Trigger(TRIGGER_TIME, times=("08:00",), weekdays=(0, 1, 2, 3, 4))
DAILY = Trigger(TRIGGER_TIME, times=("08:00", "17:30"))
EVERY_HOUR = Trigger(TRIGGER_EVERY, every_minutes=60)
WED = datetime(2026, 11, 11)          # a Wednesday


class TimedTests(unittest.TestCase):

    def test_first_sight_is_a_baseline(self) -> None:
        due = schedule.due_time(DAILY, WED.replace(hour=9), "", 3)
        self.assertEqual((due.state, due.key), (schedule.BASELINE, "2026-11-11 08:00"))
        self.assertFalse(due.runs)

    def test_due_at_its_slot_and_a_little_late(self) -> None:
        last = "2026-11-10 17:30"
        self.assertEqual(schedule.due_time(DAILY, WED.replace(hour=7, minute=59), last, 3).state, schedule.NOT_DUE)
        on_time = schedule.due_time(DAILY, WED.replace(hour=8, minute=4), last, 0)
        self.assertEqual((on_time.state, on_time.key), (schedule.DUE, "2026-11-11 08:00"))
        self.assertEqual(schedule.due_time(DAILY, WED.replace(hour=8, minute=4), "2026-11-11 08:00", 3).state,
                         schedule.NOT_DUE)

    def test_after_a_sleep_within_the_window_it_runs_once_older_is_missed(self) -> None:
        last = "2026-11-10 17:30"
        self.assertEqual(schedule.due_time(DAILY, WED.replace(hour=10, minute=50), last, 3).state, schedule.DUE)
        missed = schedule.due_time(DAILY, WED.replace(hour=11, minute=30), last, 3)
        self.assertEqual((missed.state, missed.key), (schedule.MISSED, "2026-11-11 08:00"))
        # zero catch-up hours still allow a few minutes (the check comes every 5 minutes)
        self.assertEqual(schedule.due_time(DAILY, WED.replace(hour=8, minute=9), last, 0).state, schedule.DUE)
        self.assertEqual(schedule.due_time(DAILY, WED.replace(hour=8, minute=11), last, 0).state, schedule.MISSED)

    def test_weekdays_only(self) -> None:
        saturday = datetime(2026, 11, 14, 9, 0)
        found = schedule.latest_slot(WEEKDAYS_8, saturday)
        self.assertEqual(found[0], "2026-11-13 08:00")             # Friday's slot
        self.assertEqual(schedule.due_time(WEEKDAYS_8, saturday, "2026-11-13 08:00", 3).state, schedule.NOT_DUE)
        self.assertEqual(schedule.next_time(WEEKDAYS_8, saturday).replace(tzinfo=None), datetime(2026, 11, 16, 8, 0))

    def test_a_spring_forward_night_still_runs_its_slot_once(self) -> None:
        early = Trigger(TRIGGER_TIME, times=("02:30",))
        # 2026-03-08 (US): the clocks jump from 01:59 to 03:00; the first check after it runs 02:30's slot
        now = datetime(2026, 3, 8, 3, 5)
        due = schedule.due_time(early, now, "2026-03-07 02:30", 3)
        self.assertEqual((due.state, due.key), (schedule.DUE, "2026-03-08 02:30"))
        self.assertEqual(schedule.due_time(early, now + timedelta(minutes=5), due.key, 3).state, schedule.NOT_DUE)

    def test_a_fall_back_night_does_not_repeat_its_slot(self) -> None:
        night = Trigger(TRIGGER_TIME, times=("01:30",))
        # 2026-11-01 (US): 01:30 happens twice; one key, one run
        first = schedule.due_time(night, datetime(2026, 11, 1, 1, 35), "2026-10-31 01:30", 3)
        self.assertEqual(first.state, schedule.DUE)
        again = schedule.due_time(night, datetime(2026, 11, 1, 1, 35, fold=1), first.key, 3)
        self.assertEqual(again.state, schedule.NOT_DUE)

    def test_a_trigger_without_times_is_never_due(self) -> None:
        self.assertEqual(schedule.due_time(Trigger(TRIGGER_MAIL), WED, "", 3).state, schedule.NOT_DUE)
        self.assertIsNone(schedule.next_time(Trigger(TRIGGER_MAIL), WED))


class IntervalTests(unittest.TestCase):

    def test_first_sight_is_a_baseline_then_every_hour(self) -> None:
        self.assertEqual(schedule.due_every(EVERY_HOUR, WED, None).state, schedule.BASELINE)
        self.assertEqual(schedule.due_every(EVERY_HOUR, WED.replace(hour=1), WED.replace(minute=1)).state,
                         schedule.NOT_DUE)
        self.assertEqual(schedule.due_every(EVERY_HOUR, WED.replace(hour=1, minute=1), WED.replace(minute=1)).state,
                         schedule.DUE)

    def test_a_long_sleep_is_one_run(self) -> None:
        due = schedule.due_every(EVERY_HOUR, WED + timedelta(hours=9), WED)
        self.assertEqual(due.state, schedule.DUE)
        self.assertEqual(schedule.next_every(EVERY_HOUR, WED + timedelta(hours=9), WED + timedelta(hours=9)),
                         WED + timedelta(hours=10))


class QuietTests(unittest.TestCase):

    def test_quiet_hours_across_midnight(self) -> None:
        self.assertTrue(schedule.in_quiet(WED.replace(hour=23), "22:00", "08:00"))
        self.assertTrue(schedule.in_quiet(WED.replace(hour=7, minute=59), "22:00", "08:00"))
        self.assertFalse(schedule.in_quiet(WED.replace(hour=8), "22:00", "08:00"))
        self.assertTrue(schedule.in_quiet(WED.replace(hour=13), "12:00", "14:00"))
        self.assertFalse(schedule.in_quiet(WED.replace(hour=13), "12:00", "12:00"))
        self.assertFalse(schedule.in_quiet(WED.replace(hour=13), "bad", "14:00"))


if __name__ == "__main__":
    unittest.main()
