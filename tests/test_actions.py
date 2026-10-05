"""Tests for briefing_reader.actions: proposal lines, section extraction and the decision store.

Everything is offline and pure: no Notion, no Google. Dates are fixed (today
is taken as 2026-10-04, a Sunday) so wording that depends on the year does
not depend on the machine clock. ActionStore tests use temporary folders.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from briefing_reader import actions
from briefing_reader.actions import (
    CALENDAR,
    UNKNOWN,
    ActionStore,
    ProposedAction,
    extract_actions,
    parse_action_line,
)
from briefing_reader.models import (
    BULLETED,
    DIVIDER,
    HEADING,
    NUMBERED,
    PARAGRAPH,
    TO_DO,
    FlatLine,
)
from briefing_reader.notion_client import FixtureSession, NotionClient, fetch_briefing

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FAKE_PAGE = Path(__file__).resolve().parent / "fixtures" / "fake_page.json"
ACTIONS_LOGGER = "briefing_reader.actions"

TODAY = date(2026, 10, 4)
PDT = timezone(timedelta(hours=-7), "PDT")
NOW = datetime(2026, 10, 4, 13, 52, tzinfo=PDT)

# The examples from HUD_SPEC.md section 1.
CHESS = ("Calendar: Chess Club Weekly Meeting | 2026-10-09 15:00-16:00 | weekly until 2026-12-11 | "
       "https://meet.google.com/aaa-bbbb-ccc | Carol's team sync")
FILM = ("Calendar: Film Club October General Meeting | 2026-10-06 17:00-18:00 | | "
       "Central Library, Media Lab |")
ESSAY = "Calendar: HIST Essay #1 draft due | 2026-10-05 |  |  | first draft + peer review"
SPRING = "Calendar: Spring break | 2027-03-22 to 2027-03-26"


def dt(day: int, hour: int, minute: int = 0, month: int = 10, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute)


def cal(when: str, repeat: str = "", title: str = "Sync") -> ProposedAction:
    return parse_action_line(f"Calendar: {title} | {when} | {repeat}")


def heading(text: str, level: int = 2) -> FlatLine:
    return FlatLine(HEADING, text, level=level)


def bullet(text: str, depth: int = 0) -> FlatLine:
    return FlatLine(BULLETED, text, depth)


def para(text: str, depth: int = 0) -> FlatLine:
    return FlatLine(PARAGRAPH, text, depth)


# --------------------------------------------------------------------------
# The spec's examples
# --------------------------------------------------------------------------

class SpecExampleTests(unittest.TestCase):

    def test_weekly_meeting_fields(self) -> None:
        action = parse_action_line(CHESS)
        self.assertTrue(action.actionable)
        self.assertEqual(action.kind, CALENDAR)
        self.assertEqual(action.error, "")
        self.assertEqual(action.title, "Chess Club Weekly Meeting")
        self.assertEqual(action.start, dt(9, 15))
        self.assertEqual(action.end, dt(9, 16))
        self.assertIsNone(action.all_day_start)
        self.assertFalse(action.all_day)
        self.assertEqual(action.repeat, "weekly until 2026-12-11")
        self.assertEqual(action.rrule, "RRULE:FREQ=WEEKLY;UNTIL=20261211T235959Z")
        self.assertEqual(action.where, "https://meet.google.com/aaa-bbbb-ccc")
        self.assertEqual(action.notes, "Carol's team sync")
        self.assertEqual(action.raw, CHESS)

    def test_weekly_meeting_wording(self) -> None:
        action = parse_action_line(CHESS)
        self.assertEqual(action.describe(TODAY), "Fri Oct 9 \u00b7 3:00-4:00 PM \u00b7 weekly until Dec 11")
        self.assertEqual(action.spoken(TODAY),
                         "Chess Club Weekly Meeting, Friday October 9, 3 to 4 PM, weekly until December 11")

    def test_meeting_with_empty_repeat_and_trailing_pipe(self) -> None:
        action = parse_action_line(FILM)
        self.assertTrue(action.actionable)
        self.assertEqual(action.title, "Film Club October General Meeting")
        self.assertEqual((action.start, action.end), (dt(6, 17), dt(6, 18)))
        self.assertEqual((action.repeat, action.rrule), ("", ""))
        self.assertEqual(action.where, "Central Library, Media Lab")
        self.assertEqual(action.notes, "")
        self.assertEqual(action.describe(TODAY), "Tue Oct 6 \u00b7 5:00-6:00 PM")
        self.assertEqual(action.spoken(TODAY), "Film Club October General Meeting, Tuesday October 6, 5 to 6 PM")

    def test_all_day_deadline(self) -> None:
        action = parse_action_line(ESSAY)
        self.assertTrue(action.actionable)
        self.assertEqual(action.title, "HIST Essay #1 draft due")
        self.assertIsNone(action.start)
        self.assertIsNone(action.end)
        self.assertEqual((action.all_day_start, action.all_day_end), (date(2026, 10, 5), date(2026, 10, 5)))
        self.assertTrue(action.all_day)
        self.assertEqual(action.where, "")
        self.assertEqual(action.notes, "first draft + peer review")
        self.assertEqual(action.describe(TODAY), "Mon Oct 5 \u00b7 all day")
        self.assertEqual(action.spoken(TODAY), "HIST Essay number 1 draft due, Monday October 5, all day")

    def test_multi_day_all_day(self) -> None:
        action = parse_action_line(SPRING)
        self.assertTrue(action.actionable)
        self.assertEqual((action.all_day_start, action.all_day_end), (date(2027, 3, 22), date(2027, 3, 26)))
        self.assertEqual(action.describe(TODAY), "Mon Mar 22 - Fri Mar 26, 2027 \u00b7 all day")
        self.assertEqual(action.spoken(TODAY),
                         "Spring break, Monday March 22 to Friday March 26, 2027, all day")
        self.assertEqual(action.describe(date(2027, 1, 1)), "Mon Mar 22 - Fri Mar 26 \u00b7 all day")


# --------------------------------------------------------------------------
# <when>
# --------------------------------------------------------------------------

class WhenTimedTests(unittest.TestCase):

    def assertTimed(self, when: str, start: datetime, end: datetime) -> None:
        action = cal(when)
        self.assertEqual(action.error, "", when)
        self.assertEqual((action.start, action.end), (start, end), when)
        self.assertIsNone(action.all_day_start, when)

    def test_24_hour_ranges(self) -> None:
        for when in ("2026-10-09 15:00-16:00", "2026-10-09 15:00 - 16:00", "2026-10-09 15:00\u201316:00",
                     "2026-10-09 15:00 \u2013 16:00", "2026-10-09 15:00 \u2014 16:00",
                     "2026-10-09 15:00 to 16:00", "2026/10/09 15:00-16:00", "2026-10-09T15:00-16:00",
                     "2026-10-09, 15:00-16:00", "2026-10-09 from 15:00 to 16:00"):
            with self.subTest(when=when):
                self.assertTimed(when, dt(9, 15), dt(9, 16))

    def test_12_hour_forms(self) -> None:
        for when in ("2026-10-09 3:00 PM-4:00 PM", "2026-10-09 3:00PM-4:00PM", "2026-10-09 3pm-4pm",
                     "2026-10-09 3 PM - 4 PM", "2026-10-09 3 p.m. - 4 p.m.", "2026-10-09 3pm\u20134pm",
                     "2026-10-09 3-4pm", "2026-10-09 3 - 4 PM", "2026-10-09 3pm-4",
                     "2026-10-09 at 3pm to 4pm"):
            with self.subTest(when=when):
                self.assertTimed(when, dt(9, 15), dt(9, 16))

    def test_12_hour_minutes(self) -> None:
        cases = {
            "2026-10-09 3 PM - 4:30 PM": (dt(9, 15), dt(9, 16, 30)),
            "2026-10-09 3PM-4:30PM": (dt(9, 15), dt(9, 16, 30)),
            "2026-10-09 3:30-4pm": (dt(9, 15, 30), dt(9, 16)),
            "2026-10-09 10:30-11:00 AM": (dt(9, 10, 30), dt(9, 11)),
            "2026-10-09 9:15 am - 9:45 am": (dt(9, 9, 15), dt(9, 9, 45)),
        }
        for when, (start, end) in cases.items():
            with self.subTest(when=when):
                self.assertTimed(when, start, end)

    def test_missing_meridiem_takes_the_shorter_event(self) -> None:
        cases = {
            "2026-10-09 11:30-12:30 PM": (dt(9, 11, 30), dt(9, 12, 30)),
            "2026-10-09 9-5pm": (dt(9, 9), dt(9, 17)),
            "2026-10-09 11am-1": (dt(9, 11), dt(9, 13)),
            "2026-10-09 9-17": (dt(9, 9), dt(9, 17)),
        }
        for when, (start, end) in cases.items():
            with self.subTest(when=when):
                self.assertTimed(when, start, end)

    def test_noon_and_midnight(self) -> None:
        cases = {
            "2026-10-09 12pm-1pm": (dt(9, 12), dt(9, 13)),
            "2026-10-09 12am-1am": (dt(9, 0), dt(9, 1)),
            "2026-10-09 noon-1:30pm": (dt(9, 12), dt(9, 13, 30)),
            "2026-10-09 noon-1": (dt(9, 12), dt(9, 13)),
            "2026-10-09 11am-noon": (dt(9, 11), dt(9, 12)),
            "2026-10-09 22:00-midnight": (dt(9, 22), dt(10, 0)),
            "2026-10-09 22:00-24:00": (dt(9, 22), dt(10, 0)),
        }
        for when, (start, end) in cases.items():
            with self.subTest(when=when):
                self.assertTimed(when, start, end)

    def test_end_before_start_is_next_day(self) -> None:
        for when in ("2026-10-09 23:00-01:00", "2026-10-09 11pm-1am", "2026-10-09 11-1am",
                     "2026-10-09 11 PM - 1 AM"):
            with self.subTest(when=when):
                self.assertTimed(when, dt(9, 23), dt(10, 1))

    def test_single_time_lasts_an_hour(self) -> None:
        cases = {
            "2026-10-09 15:00": (dt(9, 15), dt(9, 16)),
            "2026-10-09 3pm": (dt(9, 15), dt(9, 16)),
            "2026-10-09 at 3:30 PM": (dt(9, 15, 30), dt(9, 16, 30)),
            "2026-10-09 9:30": (dt(9, 9, 30), dt(9, 10, 30)),   # HH:MM without AM/PM is 24-hour
            "2026-10-09 23:30": (dt(9, 23, 30), dt(10, 0, 30)),
        }
        for when, (start, end) in cases.items():
            with self.subTest(when=when):
                self.assertTimed(when, start, end)

    def test_weekday_prefix_is_checked_and_dropped(self) -> None:
        for when in ("Fri 2026-10-09 3pm-4pm", "Friday, 2026-10-09, 15:00-16:00",
                     "2026-10-09 (Fri) 15:00-16:00", "on Fri. 2026-10-09 3-4pm"):
            with self.subTest(when=when):
                self.assertTimed(when, dt(9, 15), dt(9, 16))

    def test_timed_span_over_two_dates(self) -> None:
        self.assertTimed("2026-10-09 15:00 to 2026-10-11 11:00", dt(9, 15), dt(11, 11))
        self.assertTimed("2026-10-09 3pm - 2026-10-10 9am", dt(9, 15), dt(10, 9))
        self.assertTimed("Fri 2026-10-09 3pm to Sat 2026-10-10 9am", dt(9, 15), dt(10, 9))

    def test_times_are_naive_wall_times(self) -> None:
        action = cal("2026-10-09 15:00-16:00")
        self.assertIsNone(action.start.tzinfo)
        self.assertIsNone(action.end.tzinfo)


class WhenAllDayTests(unittest.TestCase):

    def assertAllDay(self, when: str, first: date, last: date) -> None:
        action = cal(when)
        self.assertEqual(action.error, "", when)
        self.assertEqual((action.all_day_start, action.all_day_end), (first, last), when)
        self.assertIsNone(action.start, when)
        self.assertTrue(action.all_day, when)

    def test_single_day(self) -> None:
        for when in ("2026-10-05", "2026-10-05 all day", "2026-10-05 (all-day)", "Mon 2026-10-05",
                     "2026-10-05 All Day", "2026/10/5"):
            with self.subTest(when=when):
                self.assertAllDay(when, date(2026, 10, 5), date(2026, 10, 5))

    def test_multi_day_range_is_inclusive(self) -> None:
        for when in ("2027-03-22 to 2027-03-26", "2027-03-22 - 2027-03-26", "2027-03-22\u20132027-03-26",
                     "2027-03-22 through 2027-03-26", "Mon 2027-03-22 to Fri 2027-03-26",
                     "2027-03-22 to 2027-03-26 all day"):
            with self.subTest(when=when):
                self.assertAllDay(when, date(2027, 3, 22), date(2027, 3, 26))

    def test_same_start_and_end_day(self) -> None:
        self.assertAllDay("2026-10-05 to 2026-10-05", date(2026, 10, 5), date(2026, 10, 5))


class WhenErrorTests(unittest.TestCase):

    def assertError(self, when: str, fragment: str) -> None:
        action = cal(when)
        self.assertFalse(action.actionable, when)
        self.assertEqual(action.kind, CALENDAR, when)
        self.assertIn(fragment, action.error, when)
        self.assertEqual(action.describe(TODAY), "")
        self.assertEqual(action.spoken(TODAY), "")

    def test_missing_date(self) -> None:
        for line in ("Calendar: Sync", "Calendar: Sync |", "Calendar: Sync |  | weekly",
                     "Calendar: Sync | Friday 3pm", "Calendar: Sync | next week"):
            with self.subTest(line=line):
                action = parse_action_line(line)
                self.assertFalse(action.actionable)
                self.assertIn("no date", action.error)
                self.assertEqual(action.title, "Sync")

    def test_bad_times(self) -> None:
        cases = {
            "2026-10-09 25:00": 'could not read the time "25:00"',
            "2026-10-09 15:75": 'could not read the time "15:75"',
            "2026-10-09 13pm": 'could not read the time "13pm"',
            "2026-10-09 0am": 'could not read the time "0am"',
            "2026-10-09 15:00 PT": 'could not read the time "15:00 pt"',
            "2026-10-09 15:00-16:00-17:00": "could not read the time",
            "2026-10-09 soonish": "could not read the time",
        }
        for when, fragment in cases.items():
            with self.subTest(when=when):
                self.assertError(when, fragment)

    def test_ambiguous_hours_need_am_pm(self) -> None:
        for when in ("2026-10-09 3-4", "2026-10-09 9", "2026-10-09 3:00-4"):
            with self.subTest(when=when):
                self.assertError(when, "needs AM/PM or HH:MM")

    def test_zero_length(self) -> None:
        self.assertError("2026-10-09 3pm-3pm", "ends when it starts")
        self.assertError("2026-10-09 15:00-15:00", "ends when it starts")

    def test_impossible_dates(self) -> None:
        for when in ("2026-02-30", "2026-13-01", "2026-10-09 to 2026-10-32"):
            with self.subTest(when=when):
                self.assertError(when, "is not a real date")

    def test_weekday_mismatch(self) -> None:
        self.assertError("Thu 2026-10-09 3pm", "2026-10-09 is a Friday, not Thu")
        self.assertError("2026-10-05 to Sun 2026-10-09", "is a Friday, not Sun")

    def test_bad_ranges(self) -> None:
        self.assertError("2027-03-26 to 2027-03-22", "the end date is before the start date")
        self.assertError("2026-10-09 15:00 to 2026-10-09 14:00", "ends before it starts")
        self.assertError("2026-10-09 15:00 to 2026-10-10", "both dates or for neither")
        self.assertError("2026-10-05 and 2026-10-06", "could not read the dates")
        self.assertError("2026-10-05 2026-10-06 2026-10-07", "could not read the date")
        self.assertError("soon 2026-10-05", "could not read the date")

    def test_time_and_all_day_conflict(self) -> None:
        self.assertError("2026-10-05 all day 3pm", 'both a time and "all day"')


# --------------------------------------------------------------------------
# <repeat>
# --------------------------------------------------------------------------

class RepeatTests(unittest.TestCase):

    def test_words_map_to_rrules(self) -> None:
        cases = {
            "daily": ("daily", "RRULE:FREQ=DAILY"),
            "Every day": ("daily", "RRULE:FREQ=DAILY"),
            "weekdays": ("weekdays", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
            "every weekday": ("weekdays", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
            "weekly": ("weekly", "RRULE:FREQ=WEEKLY"),
            "Weekly.": ("weekly", "RRULE:FREQ=WEEKLY"),
            "biweekly": ("biweekly", "RRULE:FREQ=WEEKLY;INTERVAL=2"),
            "bi-weekly": ("biweekly", "RRULE:FREQ=WEEKLY;INTERVAL=2"),
            "every other week": ("biweekly", "RRULE:FREQ=WEEKLY;INTERVAL=2"),
            "monthly": ("monthly", "RRULE:FREQ=MONTHLY"),
            "yearly": ("yearly", "RRULE:FREQ=YEARLY"),
            "annually": ("yearly", "RRULE:FREQ=YEARLY"),
        }
        for words, (repeat, rrule) in cases.items():
            with self.subTest(repeat=words):
                action = cal("2026-10-09 15:00-16:00", words)
                self.assertTrue(action.actionable, action.error)
                self.assertEqual((action.repeat, action.rrule), (repeat, rrule))

    def test_one_off_words(self) -> None:
        for words in ("", "once", "Once", "none", "one-off", "does not repeat", "-"):
            with self.subTest(repeat=words):
                action = cal("2026-10-09 15:00-16:00", words)
                self.assertTrue(action.actionable, action.error)
                self.assertEqual((action.repeat, action.rrule), ("", ""))

    def test_until(self) -> None:
        for words in ("weekly until 2026-12-11", "Weekly, until 2026-12-11.", "weekly till 2026-12-11",
                      "weekly through 2026-12-11", "weekly ending 2026-12-11"):
            with self.subTest(repeat=words):
                action = cal("2026-10-09 15:00-16:00", words)
                self.assertEqual(action.repeat, "weekly until 2026-12-11")
                self.assertEqual(action.rrule, "RRULE:FREQ=WEEKLY;UNTIL=20261211T235959Z")

    def test_until_for_all_day_is_a_date(self) -> None:
        action = cal("2026-10-05", "weekly until 2026-12-07")
        self.assertEqual(action.rrule, "RRULE:FREQ=WEEKLY;UNTIL=20261207")
        action = cal("2026-10-05", "weekdays until 2026-10-30")
        self.assertEqual(action.rrule, "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;UNTIL=20261030")

    def test_counts(self) -> None:
        for words in ("weekly x 10", "weekly x10", "Weekly X 10", "weekly \u00d7 10", "weekly 10 times",
                      "weekly, 10 times", "weekly for 10 times"):
            with self.subTest(repeat=words):
                action = cal("2026-10-09 15:00-16:00", words)
                self.assertTrue(action.actionable, action.error)
                self.assertEqual(action.repeat, "weekly x 10")
                self.assertEqual(action.rrule, "RRULE:FREQ=WEEKLY;COUNT=10")
        action = cal("2026-10-09", "biweekly x 6")
        self.assertEqual(action.rrule, "RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=6")

    def test_a_count_of_one_is_a_one_off(self) -> None:
        action = cal("2026-10-09 15:00-16:00", "daily x 1")
        self.assertTrue(action.actionable)
        self.assertEqual((action.repeat, action.rrule), ("", ""))

    def test_bad_repeats(self) -> None:
        cases = {
            "every other blue moon": 'could not read the repeat "every other blue moon"',
            "fortnight-ish": "could not read the repeat",
            "until 2026-12-11": "has an end but no repeat",
            "once x 3": "has an end but no repeat",
            "weekly until Dec 11": 'could not read the end date in "weekly until Dec 11"',
            "weekly until 2026-12-32": "is not a real date",
            "weekly x 0": "the repeat count must be 1 to 999",
            "weekly x 1000": "the repeat count must be 1 to 999",
            "weekly until 2026-10-01": "the repeat ends before the event starts",
        }
        for words, fragment in cases.items():
            with self.subTest(repeat=words):
                action = cal("2026-10-09 15:00-16:00", words)
                self.assertFalse(action.actionable)
                self.assertIn(fragment, action.error)
                self.assertEqual(action.rrule, "")

    def test_until_on_the_start_day_is_allowed(self) -> None:
        action = cal("2026-10-09 15:00-16:00", "daily until 2026-10-09")
        self.assertTrue(action.actionable, action.error)


# --------------------------------------------------------------------------
# describe() and spoken()
# --------------------------------------------------------------------------

class WordingTests(unittest.TestCase):

    def assertWording(self, when: str, repeat: str, describe: str, spoken: str) -> None:
        action = cal(when, repeat)
        self.assertEqual(action.error, "")
        self.assertEqual(action.describe(TODAY), describe)
        self.assertEqual(action.spoken(TODAY), spoken)

    def test_time_ranges(self) -> None:
        dot = " \u00b7 "
        self.assertWording("2026-10-09 15:30-16:00", "", f"Fri Oct 9{dot}3:30-4:00 PM",
                           "Sync, Friday October 9, 3:30 to 4 PM")
        self.assertWording("2026-10-09 3pm-4:30pm", "", f"Fri Oct 9{dot}3:00-4:30 PM",
                           "Sync, Friday October 9, 3 to 4:30 PM")
        self.assertWording("2026-10-09 11:30-12:30 PM", "", f"Fri Oct 9{dot}11:30 AM-12:30 PM",
                           "Sync, Friday October 9, 11:30 AM to 12:30 PM")
        self.assertWording("2026-10-09 9-5pm", "", f"Fri Oct 9{dot}9:00 AM-5:00 PM",
                           "Sync, Friday October 9, 9 AM to 5 PM")
        self.assertWording("2026-10-09 9:00-9:15", "", f"Fri Oct 9{dot}9:00-9:15 AM",
                           "Sync, Friday October 9, 9 to 9:15 AM")

    def test_noon_midnight_and_overnight(self) -> None:
        dot = " \u00b7 "
        self.assertWording("2026-10-09 noon-1pm", "", f"Fri Oct 9{dot}12:00-1:00 PM",
                           "Sync, Friday October 9, noon to 1 PM")
        self.assertWording("2026-10-09 11am-noon", "", f"Fri Oct 9{dot}11:00 AM-12:00 PM",
                           "Sync, Friday October 9, 11 AM to noon")
        self.assertWording("2026-10-09 22:00-00:00", "", f"Fri Oct 9{dot}10:00 PM-12:00 AM",
                           "Sync, Friday October 9, 10 PM to midnight")
        self.assertWording("2026-10-09 23:00-01:00", "", f"Fri Oct 9{dot}11:00 PM-1:00 AM (next day)",
                           "Sync, Friday October 9, 11 PM to 1 AM")

    def test_span_over_dates(self) -> None:
        self.assertWording("2026-10-09 15:00 to 2026-10-11 11:00", "",
                           "Fri Oct 9 3:00 PM - Sun Oct 11 11:00 AM",
                           "Sync, from Friday October 9 at 3 PM to Sunday October 11 at 11 AM")

    def test_repeats_in_words(self) -> None:
        dot = " \u00b7 "
        self.assertWording("2026-10-09 9:00-9:15", "weekdays until 2027-01-15",
                           f"Fri Oct 9{dot}9:00-9:15 AM{dot}weekdays until Jan 15, 2027",
                           "Sync, Friday October 9, 9 to 9:15 AM, every weekday until January 15, 2027")
        self.assertWording("2026-10-09", "biweekly x 6",
                           f"Fri Oct 9{dot}all day{dot}every 2 weeks, 6 times",
                           "Sync, Friday October 9, all day, every two weeks, 6 times")
        self.assertWording("2026-10-09 3pm", "daily", f"Fri Oct 9{dot}3:00-4:00 PM{dot}daily",
                           "Sync, Friday October 9, 3 to 4 PM, daily")
        self.assertWording("2026-10-09 3pm", "monthly until 2026-12-31",
                           f"Fri Oct 9{dot}3:00-4:00 PM{dot}monthly until Dec 31",
                           "Sync, Friday October 9, 3 to 4 PM, monthly until December 31")

    def test_years_outside_this_year(self) -> None:
        dot = " \u00b7 "
        self.assertWording("2027-01-01", "", f"Fri Jan 1, 2027{dot}all day", "Sync, Friday January 1, 2027, all day")
        self.assertWording("2026-12-28 to 2027-01-02", "", f"Mon Dec 28 - Sat Jan 2, 2027{dot}all day",
                           "Sync, Monday December 28 to Saturday January 2, 2027, all day")
        self.assertWording("2027-01-04 10:00-11:00", "", f"Mon Jan 4, 2027{dot}10:00-11:00 AM",
                           "Sync, Monday January 4, 2027, 10 to 11 AM")

    def test_default_today_is_the_real_today(self) -> None:
        action = parse_action_line(CHESS)
        self.assertEqual(action.describe(), action.describe(date.today()))
        self.assertEqual(action.spoken(), action.spoken(date.today()))

    def test_spoken_title_reads_symbols(self) -> None:
        action = parse_action_line("Calendar: Q3 review & planning w/ Bob | 2026-10-09 3pm")
        self.assertEqual(action.title, "Q3 review & planning w/ Bob")
        self.assertTrue(action.spoken(TODAY).startswith("Q3 review and planning with Bob, Friday"))

    def test_spoken_is_plain_ascii(self) -> None:
        for line in (CHESS, FILM, ESSAY, SPRING):
            with self.subTest(line=line):
                spoken = parse_action_line(line).spoken(TODAY)
                self.assertTrue(spoken.isascii())
                self.assertNotIn("#", spoken)
                self.assertFalse(spoken.endswith("."))

    def test_informational_lines_have_no_wording(self) -> None:
        for line in ("Reply: Carol about the draft", "Calendar: Sync | 2026-10-09 25:00", "Just a note"):
            with self.subTest(line=line):
                action = parse_action_line(line)
                self.assertEqual(action.describe(TODAY), "")
                self.assertEqual(action.spoken(TODAY), "")

    def test_hand_built_action(self) -> None:
        action = ProposedAction(id="x", kind=CALENDAR, raw="", title="Dentist",
                                start=dt(7, 8, 30), end=dt(7, 9, 15), repeat="yearly")
        self.assertEqual(action.describe(TODAY), "Wed Oct 7 \u00b7 8:30-9:15 AM \u00b7 yearly")
        self.assertEqual(action.spoken(TODAY), "Dentist, Wednesday October 7, 8:30 to 9:15 AM, yearly")


# --------------------------------------------------------------------------
# Kinds and malformed lines
# --------------------------------------------------------------------------

class KindTests(unittest.TestCase):

    def test_other_kinds_are_informational(self) -> None:
        cases = {
            "Reply: Carol about the draft": ("reply", "Carol about the draft"),
            "Todo: renew parking permit": ("todo", "renew parking permit"),
            "To-do: renew parking permit": ("todo", "renew parking permit"),
            "TODO: renew parking permit": ("todo", "renew parking permit"),
            "Frobnicate: the widgets | 2026-10-09": ("frobnicate", "the widgets | 2026-10-09"),
            "Reply to Bob: yes": ("reply", "yes"),
        }
        for line, (kind, title) in cases.items():
            with self.subTest(line=line):
                action = parse_action_line(line)
                self.assertEqual(action.kind, kind)
                self.assertEqual(action.title, title)
                self.assertEqual(action.error, "")
                self.assertFalse(action.actionable)
                self.assertEqual(action.raw, line)

    def test_calendar_kind_is_case_insensitive_with_aliases(self) -> None:
        for line in ("calendar: Sync | 2026-10-09 3pm", "CALENDAR : Sync | 2026-10-09 3pm",
                     "Calendar event: Sync | 2026-10-09 3pm", "Event: Sync | 2026-10-09 3pm"):
            with self.subTest(line=line):
                action = parse_action_line(line)
                self.assertEqual(action.kind, CALENDAR)
                self.assertTrue(action.actionable, action.error)
                self.assertEqual(action.title, "Sync")

    def test_prose_without_a_kind(self) -> None:
        action = parse_action_line("Nothing needs your OK today.")
        self.assertEqual(action.kind, UNKNOWN)
        self.assertEqual(action.error, "")
        self.assertFalse(action.actionable)
        self.assertEqual(action.raw, "Nothing needs your OK today.")

    def test_pipe_line_without_a_kind_is_an_error(self) -> None:
        action = parse_action_line("Chess Club Weekly Meeting | 2026-10-09 15:00-16:00")
        self.assertEqual(action.kind, UNKNOWN)
        self.assertIn("no action type", action.error)
        self.assertFalse(action.actionable)

    def test_a_url_is_not_a_kind(self) -> None:
        action = parse_action_line("https://example.com/agenda")
        self.assertEqual(action.kind, UNKNOWN)
        self.assertEqual(action.raw, "https://example.com/agenda")

    def test_empty_lines(self) -> None:
        for text in ("", "   ", "** **", "\U0001F4C5"):
            with self.subTest(text=text):
                action = parse_action_line(text)
                self.assertEqual(action.kind, UNKNOWN)
                self.assertEqual(action.error, "empty line")
                self.assertFalse(action.actionable)

    def test_missing_title(self) -> None:
        action = parse_action_line("Calendar: | 2026-10-09 3pm")
        self.assertEqual(action.error, "no title")
        self.assertFalse(action.actionable)

    def test_list_markers_and_quotes_are_ignored(self) -> None:
        for line in ("- Calendar: Sync | 2026-10-09 3pm", "* Calendar: Sync | 2026-10-09 3pm",
                     "1. Calendar: Sync | 2026-10-09 3pm", "[ ] Calendar: Sync | 2026-10-09 3pm",
                     "> Calendar: Sync | 2026-10-09 3pm"):
            with self.subTest(line=line):
                action = parse_action_line(line)
                self.assertTrue(action.actionable, action.error)
                self.assertEqual(action.raw, "Calendar: Sync | 2026-10-09 3pm")

    def test_errored_line_keeps_what_it_could_read(self) -> None:
        action = parse_action_line("Calendar: Sync | 2026-10-09 | sometimes | Room 12 | bring laptop")
        self.assertIn("could not read the repeat", action.error)
        self.assertEqual((action.title, action.where, action.notes), ("Sync", "Room 12", "bring laptop"))


# --------------------------------------------------------------------------
# Markdown inside fields
# --------------------------------------------------------------------------

class MarkdownFieldTests(unittest.TestCase):

    def test_emphasis_code_and_emoji_are_stripped(self) -> None:
        action = parse_action_line(
            "- **Calendar:** *Team sync* | `2026-10-09` 3pm | | __Room 12__ | _bring_ ~~old~~ notes \U0001F4C5")
        self.assertTrue(action.actionable, action.error)
        self.assertEqual(action.title, "Team sync")
        self.assertEqual(action.where, "Room 12")
        self.assertEqual(action.notes, "bring old notes")
        self.assertEqual(action.raw, "Calendar: Team sync | 2026-10-09 3pm | | Room 12 | bring old notes")

    def test_links_keep_their_url_in_where_and_notes(self) -> None:
        action = parse_action_line(
            "Calendar: Sync | 2026-10-09 3pm | | [Zoom](https://zoom.us/j/123) | see [doc](https://x.io/d).")
        self.assertEqual(action.where, "Zoom (https://zoom.us/j/123)")
        self.assertEqual(action.notes, "see doc (https://x.io/d).")

    def test_bare_and_angle_urls_survive(self) -> None:
        action = parse_action_line(
            "Calendar: Sync | 2026-10-09 3pm | | <https://meet.google.com/abc-defg-hij> | "
            "agenda at https://example.com/a_b_c.")
        self.assertEqual(action.where, "https://meet.google.com/abc-defg-hij")
        self.assertEqual(action.notes, "agenda at https://example.com/a_b_c.")

    def test_link_label_that_is_a_url(self) -> None:
        action = parse_action_line(
            "Calendar: Sync | 2026-10-09 3pm | | [https://zoom.us/j/1](https://zoom.us/j/1) |")
        self.assertEqual(action.where, "https://zoom.us/j/1")

    def test_title_drops_links_and_urls(self) -> None:
        action = parse_action_line("Calendar: [Team sync](https://notion.so/abc) | 2026-10-09 3pm")
        self.assertEqual(action.title, "Team sync")
        self.assertEqual(action.raw, "Calendar: Team sync (https://notion.so/abc) | 2026-10-09 3pm")

    def test_html_is_removed(self) -> None:
        action = parse_action_line("Calendar: <b>Sync</b> | 2026-10-09 3pm | | Room&nbsp;5 |")
        self.assertEqual(action.title, "Sync")
        self.assertEqual(action.where, "Room 5")

    def test_extra_fields_join_the_notes(self) -> None:
        action = parse_action_line("Calendar: Sync | 2026-10-09 3pm | | Room 5 | bring laptop | and charger")
        self.assertEqual(action.notes, "bring laptop | and charger")

    def test_escaped_markdown_stays_literal(self) -> None:
        action = parse_action_line(r"Calendar: 5 \* 3 review | 2026-10-09 3pm")
        self.assertEqual(action.title, "5 * 3 review")


# --------------------------------------------------------------------------
# Ids
# --------------------------------------------------------------------------

class IdTests(unittest.TestCase):

    def test_id_formula(self) -> None:
        key = "\x1f".join(("calendar", "chess club weekly meeting", "2026-10-09T15:00", "2026-10-09T16:00", "0",
                           "RRULE:FREQ=WEEKLY;UNTIL=20261211T235959Z"))
        self.assertEqual(parse_action_line(CHESS).id, hashlib.sha1(key.encode("utf-8")).hexdigest()[:16])
        key = "\x1f".join(("calendar", "hist essay #1 draft due", "2026-10-05", "2026-10-05", "1", ""))
        self.assertEqual(parse_action_line(ESSAY).id, hashlib.sha1(key.encode("utf-8")).hexdigest()[:16])

    def test_ids_are_16_hex(self) -> None:
        for line in (CHESS, FILM, ESSAY, SPRING, "Reply: x", ""):
            with self.subTest(line=line):
                action_id = parse_action_line(line).id
                self.assertRegex(action_id, r"^[0-9a-f]{16}$")

    def test_stable_across_spelling_where_and_notes(self) -> None:
        base = parse_action_line(CHESS).id
        variants = (
            "Calendar: chess club  weekly MEETING | 2026-10-09 3pm-4pm | Weekly until 2026-12-11",
            "- **Calendar:** Chess Club Weekly Meeting | Fri 2026-10-09 15:00\u201316:00 | weekly till 2026-12-11 "
            "| Room 5 | different notes",
        )
        for line in variants:
            with self.subTest(line=line):
                self.assertEqual(parse_action_line(line).id, base)

    def test_changes_with_time_repeat_or_kind_of_day(self) -> None:
        ids = {
            cal("2026-10-09 15:00-16:00").id,
            cal("2026-10-09 15:00-16:30").id,
            cal("2026-10-09 16:00-17:00").id,
            cal("2026-10-09 15:00-16:00", "weekly").id,
            cal("2026-10-09").id,
            cal("2026-10-09 to 2026-10-10").id,
            cal("2026-10-09 15:00-16:00", title="Other").id,
        }
        self.assertEqual(len(ids), 7)

    def test_informational_lines_differ_by_text(self) -> None:
        first = parse_action_line("Reply: Carol about the draft")
        second = parse_action_line("Reply: Bob about the invoice")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.id, parse_action_line("Reply: Carol about the draft").id)
        broken_a = parse_action_line("Calendar: Sync | 2026-10-09 25:00")
        broken_b = parse_action_line("Calendar: Sync | 2026-10-09 26:00")
        self.assertNotEqual(broken_a.id, broken_b.id)


# --------------------------------------------------------------------------
# extract_actions
# --------------------------------------------------------------------------

class ExtractActionsTests(unittest.TestCase):

    def page(self) -> list[FlatLine]:
        return [
            heading("Daily Briefing", 1),
            heading("Work (2)"),
            bullet("Email from **Bob** about the budget"),
            bullet("Invoice 4411 overdue"),
            heading("Proposed actions (3)"),
            bullet(CHESS),
            bullet(CHESS.replace("Carol's team sync", "duplicate with other notes")),
            FlatLine(NUMBERED, "Reply: Carol about the draft", number=1),
            heading("Later this term", 3),
            bullet(SPRING),
            bullet("Calendar: Sync | 2026-10-09 25:00", depth=1),
            FlatLine(DIVIDER, ""),
            para("   "),
            FlatLine(TO_DO, ESSAY, checked=False),
            heading("Ignore"),
            bullet("Promo: 20% off"),
        ]

    def test_section_between_other_headings(self) -> None:
        lines = self.page()
        found, kept = extract_actions(lines)
        self.assertEqual([a.title for a in found], ["Chess Club Weekly Meeting", "Carol about the draft",
                                                    "Spring break", "Sync", "HIST Essay #1 draft due"])
        self.assertEqual([a.kind for a in found], ["calendar", "reply", "calendar", "calendar", "calendar"])
        self.assertEqual([a.actionable for a in found], [True, False, True, False, True])
        self.assertEqual(found[0].notes, "Carol's team sync")   # the duplicate kept the first
        self.assertEqual(kept, lines[:4] + lines[14:])
        self.assertIsInstance(kept, list)

    def test_no_section_returns_the_lines_unchanged(self) -> None:
        lines = (heading("Work"), bullet("Calendar: Sync | 2026-10-09 3pm"), heading("Ignore"))
        found, kept = extract_actions(lines)
        self.assertEqual(found, [])
        self.assertEqual(kept, list(lines))

    def test_nested_section_ends_at_its_own_level(self) -> None:
        lines = [
            heading("Work"),
            heading("Inbox", 3),
            bullet("Email from Bob"),
            heading("Proposed actions", 3),
            bullet(FILM),
            heading("Calendar", 3),
            bullet("Dentist at 4"),
            heading("Personal"),
            bullet("Call mom"),
        ]
        found, kept = extract_actions(lines)
        self.assertEqual([a.title for a in found], ["Film Club October General Meeting"])
        self.assertEqual(kept, lines[:3] + lines[5:])

    def test_section_ends_at_a_higher_level(self) -> None:
        lines = [heading("Proposed actions", 3), bullet(FILM), heading("Next", 1), bullet("z")]
        found, kept = extract_actions(lines)
        self.assertEqual(len(found), 1)
        self.assertEqual(kept, lines[2:])

    def test_section_at_the_end_of_the_page(self) -> None:
        lines = [heading("Work"), bullet("x"), heading("Proposed actions"), bullet(ESSAY), bullet(FILM)]
        found, kept = extract_actions(lines)
        self.assertEqual(len(found), 2)
        self.assertEqual(kept, lines[:2])

    def test_heading_matching_is_lenient_about_case_counts_and_markup(self) -> None:
        for text in ("PROPOSED ACTIONS", "Proposed actions (2)", "\U0001F4C5 Proposed actions",
                     "**Proposed actions:**", "Proposed  Actions - 2"):
            with self.subTest(heading=text):
                found, kept = extract_actions([heading(text), bullet(ESSAY), heading("Ignore")])
                self.assertEqual(len(found), 1)
                self.assertEqual(kept, [heading("Ignore")])

    def test_only_the_whole_name_matches(self) -> None:
        lines = [heading("Proposed actions for next week"), bullet(ESSAY)]
        self.assertEqual(extract_actions(lines), ([], lines))

    def test_custom_heading_names(self) -> None:
        lines = [heading("Needs approval"), bullet(ESSAY), heading("Work"), bullet("x")]
        found, kept = extract_actions(lines, ("Proposed actions", "Needs approval"))
        self.assertEqual(len(found), 1)
        self.assertEqual(kept, lines[2:])
        found, _ = extract_actions(lines, "needs approval")   # a single string works too
        self.assertEqual(len(found), 1)
        self.assertEqual(extract_actions(lines), ([], lines))

    def test_two_sections_are_both_removed(self) -> None:
        lines = [heading("Proposed actions"), bullet(ESSAY), heading("Work"), bullet("x"),
                 heading("Proposed actions"), bullet(FILM), bullet(ESSAY)]
        found, kept = extract_actions(lines)
        self.assertEqual([a.title for a in found], ["HIST Essay #1 draft due", "Film Club October General Meeting"])
        self.assertEqual(kept, lines[2:4])

    def test_markdown_paragraph_headings(self) -> None:
        lines = [para("## Work"), para("Email from Bob"), para("## Proposed actions"),
                 para("- " + ESSAY), para("### Notes"), para("- Reply: Bob"), para("## Ignore"), para("promo")]
        found, kept = extract_actions(lines)
        self.assertEqual([a.kind for a in found], ["calendar", "reply"])
        self.assertEqual(kept, lines[:2] + lines[6:])

    def test_bold_paragraph_titles_on_a_page_without_heading_blocks(self) -> None:
        lines = [para("**Work**"), bullet("x"), para("**Proposed actions**"), bullet(ESSAY),
                 para("**Calendar: Sync | 2026-10-09 3pm**"), para("**Ignore**"), bullet("promo")]
        found, kept = extract_actions(lines)
        self.assertEqual([a.title for a in found], ["HIST Essay #1 draft due", "Sync"])
        self.assertEqual(kept, lines[:2] + lines[5:])

    def test_plain_label_paragraph_on_a_page_with_headings(self) -> None:
        lines = [heading("Work"), bullet("x"), para("Proposed actions:"), bullet(ESSAY),
                 para("**Not a title here**"), heading("Ignore"), bullet("promo")]
        found, kept = extract_actions(lines)
        self.assertEqual([a.title for a in found], ["HIST Essay #1 draft due", "Not a title here"])
        self.assertEqual(kept, lines[:2] + lines[5:])

    def test_nested_lines_are_parsed_too(self) -> None:
        lines = [heading("Proposed actions"), bullet(ESSAY), bullet(FILM, depth=1), para("a note", depth=2)]
        found, kept = extract_actions(lines)
        self.assertEqual([a.title for a in found], ["HIST Essay #1 draft due", "Film Club October General Meeting",
                                                    "a note"])
        self.assertEqual(kept, [])

    def test_logs_counts_never_text(self) -> None:
        with self.assertLogs(ACTIONS_LOGGER, level="INFO") as captured:
            extract_actions(self.page())
        output = "\n".join(captured.output)
        self.assertIn("5 line(s), 3 actionable, 1 duplicate(s)", output)
        for secret in ("Chess Club", "Carol", "Spring", "meet.google"):
            self.assertNotIn(secret, output)

    def test_fake_notion_page_has_no_section(self) -> None:
        session = FixtureSession(FAKE_PAGE)
        client = NotionClient(token="test-token", session=session, sleep=lambda s: None)
        briefing = fetch_briefing(client, session.page_id, now=NOW)
        found, kept = extract_actions(briefing.lines)
        self.assertEqual(found, [])
        self.assertEqual(kept, list(briefing.lines))


# --------------------------------------------------------------------------
# ActionStore
# --------------------------------------------------------------------------

class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class ActionStoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "briefing-reader" / "actions.json"
        self.clock = FixedClock(NOW)

    def store(self) -> ActionStore:
        return ActionStore(self.path, clock=self.clock)

    def write_raw(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.path.parent.iterdir() if p.name != self.path.name)

    def test_round_trip(self) -> None:
        store = self.store()
        store.set("abc", "created", link="https://calendar.google.com/event?eid=1")
        store.set("def", "denied")
        reopened = self.store()
        self.assertEqual(reopened.get("abc"), {"status": "created", "at": "2026-10-04T13:52:00-07:00",
                                               "link": "https://calendar.google.com/event?eid=1",
                                               "message": ""})
        self.assertEqual(reopened.get("def")["status"], "denied")
        self.assertIsNone(reopened.get("missing"))
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(on_disk), {"abc", "def"})
        self.assertEqual(self.leftovers(), [])

    def test_missing_file_is_empty_and_not_created(self) -> None:
        store = self.store()
        self.assertIsNone(store.get("abc"))
        self.assertFalse(store.is_decided("abc"))
        self.assertFalse(self.path.exists())

    def test_decided_statuses(self) -> None:
        store = self.store()
        for status, decided in (("created", True), ("exists", True), ("denied", True), ("failed", False)):
            with self.subTest(status=status):
                store.set(f"id-{status}", status, message="boom" if status == "failed" else "")
                self.assertEqual(store.is_decided(f"id-{status}"), decided)
        self.assertEqual(self.store().get("id-failed")["message"], "boom")

    def test_failed_then_created(self) -> None:
        store = self.store()
        store.set("abc", "failed", message="Google sign-in timed out")
        self.assertFalse(store.is_decided("abc"))
        store.set("abc", "created", link="https://x")
        self.assertTrue(self.store().is_decided("abc"))
        self.assertEqual(self.store().get("abc")["message"], "")

    def test_unknown_status_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store().set("abc", "approved")
        self.assertFalse(self.path.exists())

    def test_get_returns_a_copy(self) -> None:
        store = self.store()
        store.set("abc", "denied")
        store.get("abc")["status"] = "created"
        self.assertEqual(store.get("abc")["status"], "denied")

    def test_corrupt_file_is_logged_and_treated_as_empty(self) -> None:
        self.write_raw('{"abc": {"status": "created", ')
        with self.assertLogs(ACTIONS_LOGGER, level="WARNING") as captured:
            store = self.store()
        self.assertIn("not valid JSON", "\n".join(captured.output))
        self.assertIsNone(store.get("abc"))
        store.set("def", "denied")
        self.assertEqual(set(json.loads(self.path.read_text(encoding="utf-8"))), {"def"})

    def test_wrong_shapes_are_ignored(self) -> None:
        self.write_raw("[1, 2, 3]")
        with self.assertLogs(ACTIONS_LOGGER, level="WARNING"):
            self.assertIsNone(self.store().get("1"))
        self.write_raw(json.dumps({
            "good": {"status": "denied", "at": "2026-10-04T10:00:00-07:00", "link": 5},
            "bad-status": {"status": "approved"},
            "not-a-dict": "created",
        }))
        with self.assertLogs(ACTIONS_LOGGER, level="WARNING") as captured:
            store = self.store()
        self.assertIn("Ignored 2 malformed", "\n".join(captured.output))
        self.assertEqual(store.get("good"), {"status": "denied", "at": "2026-10-04T10:00:00-07:00",
                                             "link": "", "message": ""})
        self.assertIsNone(store.get("bad-status"))

    def test_byte_order_mark_is_accepted(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"abc": {"status": "exists"}}).encode("utf-8"))
        self.assertTrue(self.store().is_decided("abc"))

    def test_unreadable_path_never_crashes(self) -> None:
        self.path.mkdir(parents=True)   # a folder where the file should be
        with self.assertLogs(ACTIONS_LOGGER, level="WARNING"):
            store = self.store()
        with self.assertLogs(ACTIONS_LOGGER, level="WARNING") as captured:
            store.set("abc", "denied")
        self.assertIn("kept for this run only", "\n".join(captured.output))
        self.assertTrue(store.is_decided("abc"))

    def test_write_is_atomic_via_replace(self) -> None:
        store = self.store()
        with mock.patch.object(actions.os, "replace", wraps=os.replace) as replace:
            store.set("abc", "created")
        replace.assert_called_once()
        source, target = replace.call_args.args
        self.assertEqual(Path(target), self.path)
        self.assertEqual(Path(source).parent, self.path.parent)
        self.assertNotEqual(Path(source), self.path)
        self.assertFalse(Path(source).exists())
        self.assertEqual(self.leftovers(), [])

    def test_failed_replace_keeps_the_old_file(self) -> None:
        store = self.store()
        store.set("abc", "created")
        before = self.path.read_bytes()
        failure = PermissionError(13, "Access is denied")
        with mock.patch.object(actions.os, "replace", side_effect=failure):
            with self.assertLogs(ACTIONS_LOGGER, level="WARNING"):
                store.set("def", "denied")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.leftovers(), [])
        self.assertTrue(store.is_decided("def"))       # kept in memory for this run
        self.assertFalse(self.store().is_decided("def"))

    def test_prune_forgets_old_and_undated_decisions(self) -> None:
        old = (NOW - timedelta(days=61)).isoformat()
        recent = (NOW - timedelta(days=59)).isoformat()
        self.write_raw(json.dumps({
            "old": {"status": "denied", "at": old},
            "recent": {"status": "created", "at": recent},
            "undated": {"status": "created", "at": "yesterday-ish"},
            "naive": {"status": "exists", "at": "2026-10-01T09:00:00"},
        }))
        store = self.store()
        with self.assertLogs(ACTIONS_LOGGER, level="INFO") as captured:
            store.prune(60)
        self.assertIn("Forgot 2 action decision(s)", "\n".join(captured.output))
        self.assertEqual(set(json.loads(self.path.read_text(encoding="utf-8"))), {"recent", "naive"})
        self.assertIsNone(store.get("old"))
        self.assertTrue(store.is_decided("recent"))

    def test_prune_default_age_and_naive_clock(self) -> None:
        self.clock.now = datetime(2026, 10, 4, 12, 0)   # naive clock values are local time
        store = self.store()
        store.set("today", "denied")
        self.clock.now = datetime(2026, 12, 4, 12, 1)   # 61 days later
        store.prune()
        self.assertIsNone(store.get("today"))

    def test_prune_without_old_entries_does_not_write(self) -> None:
        store = self.store()
        store.set("abc", "denied")
        with mock.patch.object(actions.os, "replace") as replace:
            store.prune(60)
        replace.assert_not_called()
        self.assertTrue(store.is_decided("abc"))


# --------------------------------------------------------------------------
# Module hygiene
# --------------------------------------------------------------------------

class ImportTests(unittest.TestCase):

    def run_python(self, code: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, capture_output=True,
                              text=True, timeout=60)

    def test_qt_free_and_no_import_cycle_in_either_order(self) -> None:
        for first, second in (("actions", "text_prep"), ("text_prep", "actions")):
            with self.subTest(first=first):
                code = (f"import sys, briefing_reader.{first}, briefing_reader.{second}; "
                        "from briefing_reader.actions import parse_action_line; "
                        "assert parse_action_line('Calendar: A | 2026-10-09 3pm').actionable; "
                        "assert not any(m.startswith(('PySide6', 'googleapiclient')) for m in sys.modules)")
                result = self.run_python(code)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_source_is_ascii(self) -> None:
        source = Path(actions.__file__).read_bytes()
        self.assertTrue(source.isascii())


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
