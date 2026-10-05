"""Tests for briefing_reader.agenda: the agenda window and rows, and the DEADLINES list.

Everything is offline and pure: no Notion, no Google, no Qt. "Now" is fixed
(Monday 2026-10-05 14:40 PDT) so labels do not depend on the machine clock.
Calendar events are plain CalendarEvent values, as list_events returns them.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

from briefing_reader import agenda, config
from briefing_reader.actions import extract_actions
from briefing_reader.agenda import (
    ALL_DAY_TEXT,
    CALENDAR_SOURCE,
    DEFAULT_HEADINGS,
    DEFAULT_KEYWORDS,
    ORIGIN_CALENDAR,
    ORIGIN_PAGE,
    Deadline,
    EventRow,
    agenda_window,
    deadlines_from_events,
    display_location,
    due_label,
    event_rows,
    events_in_window,
    extract_deadlines,
    merge_deadlines,
    parse_deadline_line,
)
from briefing_reader.gcal import CalendarEvent
from briefing_reader.models import (
    BULLETED,
    DIVIDER,
    HEADING,
    PARAGRAPH,
    Briefing,
    BriefingHeader,
    FlatLine,
)
from briefing_reader.text_prep import build_script

AGENDA_LOGGER = "briefing_reader.agenda"
PDT = timezone(timedelta(hours=-7), "PDT")
NOW = datetime(2026, 10, 5, 14, 40, tzinfo=PDT)
DOT = " \u00b7 "


def at(day: int, hour: int, minute: int = 0, month: int = 10, year: int = 2026,
       tz: timezone | None = PDT) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def timed(title: str, start: datetime, minutes: int = 60, location: str = "") -> CalendarEvent:
    return CalendarEvent(title, start=start, end=start + timedelta(minutes=minutes),
                         location=location)


def all_day(title: str, first: date, last: date | None = None, location: str = "") -> CalendarEvent:
    return CalendarEvent(title, all_day_start=first, all_day_end=last or first, location=location)


def heading(text: str, level: int = 2) -> FlatLine:
    return FlatLine(HEADING, text, level=level)


def bullet(text: str, depth: int = 0) -> FlatLine:
    return FlatLine(BULLETED, text, depth)


def para(text: str, depth: int = 0) -> FlatLine:
    return FlatLine(PARAGRAPH, text, depth)


ESSAY = "Deadline: HIST Essay #1 draft | 2026-10-09 | Canvas"
PSET = "Deadline: CS 101 Problem set 3 | 2026-10-07 23:59 | Gradescope"
CHESS = ("Calendar: Chess Club Weekly Meeting | 2026-10-09 15:00-16:00 | weekly until 2026-12-11 | "
       "https://meet.google.com/aaa-bbbb-ccc | Carol's team sync")


# --------------------------------------------------------------------------
# Module basics
# --------------------------------------------------------------------------

class ModuleTests(unittest.TestCase):
    def test_defaults_match_the_spec(self) -> None:
        self.assertEqual(DEFAULT_HEADINGS, ("Deadlines",))
        self.assertEqual(DEFAULT_KEYWORDS, ("due", "deadline", "exam", "midterm", "final", "quiz",
                                            "submit", "submission", "assignment", "lab report",
                                            "application"))
        self.assertEqual(agenda.DEFAULT_EVENING_FROM_HOUR, 18)
        self.assertEqual(agenda.DEFAULT_DEADLINE_DAYS, 14)
        self.assertEqual(agenda.DEFAULT_DEADLINE_LIMIT, 8)

    def test_import_is_qt_free_and_offline(self) -> None:
        code = ("import sys, briefing_reader.agenda; "
                "print(sorted({m.split('.')[0] for m in sys.modules "
                "if m.split('.')[0] in ('PySide6', 'googleapiclient', 'google_auth_oauthlib', "
                "'oauthlib', 'httplib2', 'requests')}))")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                cwd=config.PROJECT_ROOT, timeout=60, check=True)
        self.assertEqual(result.stdout.strip(), "[]")


# --------------------------------------------------------------------------
# agenda_window
# --------------------------------------------------------------------------

class AgendaWindowTests(unittest.TestCase):
    def test_before_and_after_the_evening_hour(self) -> None:
        cases = [
            (at(5, 9, 0), "TODAY", at(5, 0), at(6, 0)),
            (at(5, 17, 59), "TODAY", at(5, 0), at(6, 0)),
            (at(5, 18, 0), "TOMORROW", at(6, 0), at(7, 0)),
            (at(5, 23, 59), "TOMORROW", at(6, 0), at(7, 0)),
        ]
        for now, label, start, end in cases:
            with self.subTest(now=now):
                self.assertEqual(agenda_window(now), (label, start, end))

    def test_seconds_before_the_evening_hour_still_today(self) -> None:
        now = datetime(2026, 10, 5, 17, 59, 59, 999999, tzinfo=PDT)
        self.assertEqual(agenda_window(now)[0], "TODAY")

    def test_across_midnight(self) -> None:
        self.assertEqual(agenda_window(at(5, 23, 30)), ("TOMORROW", at(6, 0), at(7, 0)))
        self.assertEqual(agenda_window(at(6, 0, 0)), ("TODAY", at(6, 0), at(7, 0)))
        self.assertEqual(agenda_window(at(6, 0, 30)), ("TODAY", at(6, 0), at(7, 0)))

    def test_month_year_and_leap_day_boundaries(self) -> None:
        cases = [
            (at(31, 19, 0, month=12), at(1, 0, month=1, year=2027), at(2, 0, month=1, year=2027)),
            (at(30, 20, 0, month=9), at(1, 0, month=10), at(2, 0, month=10)),
            (at(28, 20, 0, month=2, year=2027), at(1, 0, month=3, year=2027),
             at(2, 0, month=3, year=2027)),
            (at(28, 20, 0, month=2, year=2028), at(29, 0, month=2, year=2028),
             at(1, 0, month=3, year=2028)),
        ]
        for now, start, end in cases:
            with self.subTest(now=now):
                self.assertEqual(agenda_window(now), ("TOMORROW", start, end))

    def test_custom_evening_hour(self) -> None:
        self.assertEqual(agenda_window(at(5, 19, 0), evening_from_hour=20)[0], "TODAY")
        self.assertEqual(agenda_window(at(5, 20, 0), evening_from_hour=20)[0], "TOMORROW")
        self.assertEqual(agenda_window(at(5, 0, 0), evening_from_hour=0)[0], "TOMORROW")
        self.assertEqual(agenda_window(at(5, 23, 59), evening_from_hour=24)[0], "TODAY")

    def test_naive_now_gives_naive_bounds(self) -> None:
        label, start, end = agenda_window(datetime(2026, 10, 5, 9, 15))
        self.assertEqual((label, start, end),
                         ("TODAY", datetime(2026, 10, 5), datetime(2026, 10, 6)))
        self.assertIsNone(start.tzinfo)

    def test_bounds_keep_the_offset_of_now(self) -> None:
        nepal = timezone(timedelta(hours=5, minutes=45))
        now = datetime(2026, 10, 5, 9, 0, tzinfo=nepal)
        label, start, end = agenda_window(now)
        self.assertEqual(start, datetime(2026, 10, 5, tzinfo=nepal))
        self.assertEqual(end, datetime(2026, 10, 6, tzinfo=nepal))
        self.assertEqual(start.utcoffset(), timedelta(hours=5, minutes=45))
        _, start, _ = agenda_window(NOW)
        self.assertEqual(start.utcoffset(), timedelta(hours=-7))

    def test_local_now_follows_daylight_saving_rules(self) -> None:
        # Machine-local "now", as the app makes it; the night before US clocks fall back.
        now = datetime(2026, 10, 31, 19, 0).astimezone()
        label, start, end = agenda_window(now)
        self.assertEqual(label, "TOMORROW")
        self.assertEqual(start, datetime(2026, 11, 1).astimezone())
        self.assertEqual(end, datetime(2026, 11, 2).astimezone())


# --------------------------------------------------------------------------
# events_in_window
# --------------------------------------------------------------------------

class EventsInWindowTests(unittest.TestCase):
    def titles(self, events: list[CalendarEvent], now: datetime = NOW) -> list[str]:
        _, start, end = agenda_window(now)
        return [event.title for event in events_in_window(events, start, end)]

    def test_timed_events_overlapping_the_day(self) -> None:
        events = [
            timed("Early", at(5, 8, 0)),
            timed("Night owl", at(4, 23, 30), minutes=60),     # runs past midnight into today
            timed("Ends at midnight", at(4, 23, 0)),           # 23:00-00:00 yesterday: not today
            timed("Late", at(5, 23, 30)),                      # starts today, ends tomorrow
            timed("Tomorrow", at(6, 0, 0)),                    # starts exactly at the end
            timed("Yesterday", at(4, 9, 0)),
        ]
        self.assertEqual(self.titles(events), ["Early", "Night owl", "Late"])

    def test_a_moment_at_the_start_counts(self) -> None:
        moment = CalendarEvent("Reminder", start=at(5, 0, 0), end=at(5, 0, 0))
        no_end = CalendarEvent("Ping", start=at(5, 12, 0))
        self.assertEqual(self.titles([moment, no_end]), ["Reminder", "Ping"])

    def test_all_day_events_by_their_days(self) -> None:
        events = [
            all_day("Today", date(2026, 10, 5)),
            all_day("Trip", date(2026, 10, 3), date(2026, 10, 7)),
            all_day("Over", date(2026, 10, 1), date(2026, 10, 4)),
            all_day("Next", date(2026, 10, 6)),
        ]
        self.assertEqual(self.titles(events), ["Today", "Trip"])

    def test_tomorrow_window_from_the_evening_hour(self) -> None:
        events = [timed("Lab", at(5, 19, 0)), timed("Standup", at(6, 9, 30)),
                  all_day("Next", date(2026, 10, 6)), all_day("Today", date(2026, 10, 5))]
        self.assertEqual(self.titles(events, at(5, 18, 0)), ["Standup", "Next"])

    def test_times_in_another_zone_are_compared_as_instants(self) -> None:
        eastern = timezone(timedelta(hours=-4))
        # 02:30 EDT on the 6th is 23:30 PDT on the 5th: still today here.
        late = CalendarEvent("Call", start=datetime(2026, 10, 6, 2, 30, tzinfo=eastern),
                             end=datetime(2026, 10, 6, 3, 0, tzinfo=eastern))
        early = CalendarEvent("Sync", start=datetime(2026, 10, 5, 2, 0, tzinfo=eastern),
                              end=datetime(2026, 10, 5, 2, 30, tzinfo=eastern))
        self.assertEqual(self.titles([late, early]), ["Call"])

    def test_naive_window(self) -> None:
        _, start, end = agenda_window(datetime(2026, 10, 5, 9, 15))
        events = [CalendarEvent("Naive", start=datetime(2026, 10, 5, 10), end=datetime(2026, 10, 5, 11)),
                  all_day("Today", date(2026, 10, 5))]
        self.assertEqual([e.title for e in events_in_window(events, start, end)], ["Naive", "Today"])

    def test_empty_and_order_kept(self) -> None:
        self.assertEqual(self.titles([]), [])
        events = [timed("B", at(5, 15, 0)), timed("A", at(5, 9, 0))]
        self.assertEqual(self.titles(events), ["B", "A"])


# --------------------------------------------------------------------------
# event_rows
# --------------------------------------------------------------------------

class EventRowsTests(unittest.TestCase):
    def row(self, event: CalendarEvent, now: datetime = NOW) -> EventRow:
        (only,) = event_rows([event], now)
        return only

    def test_rows_are_display_tuples(self) -> None:
        row = self.row(timed("Lab", at(5, 15, 0), location="Room 4"))
        self.assertIsInstance(row, tuple)
        time_text, title, meta, state = row
        self.assertEqual((time_text, title, meta, state),
                         ("15:00", "Lab", f"in 20 min{DOT}Room 4", "upcoming"))
        self.assertEqual(row.time_text, "15:00")

    def test_upcoming_soon_counts_minutes(self) -> None:
        cases = [
            (at(5, 15, 0), "in 20 min"),
            (at(5, 15, 40), "in 60 min"),
            (at(5, 14, 41), "in 1 min"),
            (datetime(2026, 10, 5, 14, 40, 10, tzinfo=PDT), "in 1 min"),
            (datetime(2026, 10, 5, 14, 59, 30, tzinfo=PDT), "in 20 min"),
        ]
        for start, meta in cases:
            with self.subTest(start=start):
                row = self.row(timed("Lab", start))
                self.assertEqual((row.meta, row.state), (meta, "upcoming"))

    def test_upcoming_later_shows_the_location(self) -> None:
        self.assertEqual(self.row(timed("Lab", at(5, 15, 41), location="Room 4")),
                         EventRow("15:41", "Lab", "Room 4", "upcoming"))
        self.assertEqual(self.row(timed("Lab", at(5, 17, 0))).meta, "")

    def test_now(self) -> None:
        self.assertEqual(self.row(timed("Lecture", at(5, 14, 30), 60)),
                         EventRow("14:30", "Lecture", "now", "now"))
        self.assertEqual(self.row(timed("Lecture", at(5, 14, 30), 60, "Central Library")).meta,
                         f"now{DOT}Central Library")
        self.assertEqual(self.row(timed("Starts now", NOW)).state, "now")

    def test_ended(self) -> None:
        self.assertEqual(self.row(timed("Standup", at(5, 9, 0), 15, location="Room 4")),
                         EventRow("09:00", "Standup", "ended", "past"))
        self.assertEqual(self.row(timed("Ends now", at(5, 13, 40), 60)).state, "past")
        self.assertEqual(self.row(timed("No length", at(5, 14, 0), 0)).meta, "ended")

    def test_location_vs_url(self) -> None:
        later = at(5, 17, 0)
        cases = [
            ("https://meet.google.com/aaa-bbbb-ccc", ""),
            ("meet.google.com/aaa-bbbb-ccc", ""),
            ("www.example.com", ""),
            ("Zoom: https://zoom.us/j/123?pwd=abc", "Zoom"),
            ("Room 4 (meet.google.com/abc-defg-hij)", "Room 4"),
            ("Central Library, Media Lab", "Central Library, Media Lab"),
            ("St. Mary Hall 2.14", "St. Mary Hall 2.14"),
            ("Microsoft Teams Meeting", "Microsoft Teams Meeting"),
            ("", ""),
        ]
        for location, meta in cases:
            with self.subTest(location=location):
                self.assertEqual(self.row(timed("Sync", later, location=location)).meta, meta)
                self.assertEqual(display_location(location), meta)
        soon = self.row(timed("Sync", at(5, 15, 0), location="https://zoom.us/j/1"))
        self.assertEqual(soon.meta, "in 20 min")

    def test_all_day(self) -> None:
        cases = [
            (all_day("Holiday", date(2026, 10, 5), location="Campus"), "now", "Campus"),
            (all_day("Fall break", date(2026, 10, 3), date(2026, 10, 7)), "now", ""),
            (all_day("Tomorrow thing", date(2026, 10, 6)), "upcoming", ""),
            (all_day("Over", date(2026, 10, 1), date(2026, 10, 4)), "past", ""),
        ]
        for event, state, meta in cases:
            with self.subTest(title=event.title):
                self.assertEqual(self.row(event), EventRow(ALL_DAY_TEXT, event.title, meta, state))
        self.assertEqual(ALL_DAY_TEXT, "ALL DAY")

    def test_times_are_shown_in_the_time_zone_of_now(self) -> None:
        utc = timed("Final exam", datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(self.row(utc).time_text, "09:00")
        self.assertEqual(self.row(utc).state, "past")
        late = timed("Late", datetime(2026, 10, 6, 6, 30, tzinfo=timezone.utc))
        self.assertEqual(self.row(late).time_text, "23:30")

    def test_naive_times(self) -> None:
        event = timed("Lab", datetime(2026, 10, 5, 15, 0), location="Room 4")
        self.assertEqual(self.row(event, datetime(2026, 10, 5, 14, 40)),
                         EventRow("15:00", "Lab", f"in 20 min{DOT}Room 4", "upcoming"))

    def test_tomorrow_window(self) -> None:
        evening = at(5, 20, 0)
        rows = event_rows([timed("Lab", at(6, 9, 0), location="Room 4"),
                           all_day("Holiday", date(2026, 10, 6))], evening)
        self.assertEqual(rows, [EventRow(ALL_DAY_TEXT, "Holiday", "", "upcoming"),
                                EventRow("09:00", "Lab", "Room 4", "upcoming")])
        just_after = event_rows([timed("Night owl", at(6, 0, 15))], at(5, 23, 30))
        self.assertEqual(just_after[0].meta, "in 45 min")

    def test_sorted_with_all_day_first(self) -> None:
        events = [timed("Lab", at(5, 15, 0)), timed("Standup", at(5, 9, 0)),
                  all_day("Holiday", date(2026, 10, 5)),
                  timed("Lecture", datetime(2026, 10, 5, 17, 30, tzinfo=timezone.utc))]
        self.assertEqual([row.title for row in event_rows(events, NOW)],
                         ["Holiday", "Standup", "Lecture", "Lab"])
        self.assertEqual(event_rows([], NOW), [])


# --------------------------------------------------------------------------
# parse_deadline_line
# --------------------------------------------------------------------------

class ParseDeadlineLineTests(unittest.TestCase):
    def test_the_spec_format(self) -> None:
        self.assertEqual(parse_deadline_line(ESSAY),
                         Deadline("HIST Essay #1 draft", date(2026, 10, 9), "Canvas", ORIGIN_PAGE))
        self.assertEqual(parse_deadline_line(PSET),
                         Deadline("CS 101 Problem set 3", datetime(2026, 10, 7, 23, 59),
                                  "Gradescope", "page"))

    def test_date_and_time_formats(self) -> None:
        cases = {
            "2026-10-09": date(2026, 10, 9),
            "2026/10/09": date(2026, 10, 9),
            "2026-10-9": date(2026, 10, 9),
            "2026\u201310\u201309": date(2026, 10, 9),
            "Fri 2026-10-09": date(2026, 10, 9),
            "Friday, 2026-10-09": date(2026, 10, 9),
            "due 2026-10-09": date(2026, 10, 9),
            "2026-10-09.": date(2026, 10, 9),
            "2026-10-09 23:59": datetime(2026, 10, 9, 23, 59),
            "2026-10-09 11:59 PM": datetime(2026, 10, 9, 23, 59),
            "2026-10-09 11:59pm": datetime(2026, 10, 9, 23, 59),
            "2026-10-09 9am": datetime(2026, 10, 9, 9, 0),
            "2026-10-09 09:30": datetime(2026, 10, 9, 9, 30),
            "2026-10-09T09:30": datetime(2026, 10, 9, 9, 30),
            "2026-10-09 at 5pm": datetime(2026, 10, 9, 17, 0),
            "Fri 2026-10-09 @ 17:00": datetime(2026, 10, 9, 17, 0),
            "by 2026-10-09 by 17:00": datetime(2026, 10, 9, 17, 0),
            "2026-10-09 noon": datetime(2026, 10, 9, 12, 0),
            "2026-10-09 midnight": datetime(2026, 10, 9, 0, 0),
            "2026-10-09 00:00": datetime(2026, 10, 9, 0, 0),
        }
        for when, due in cases.items():
            with self.subTest(when=when):
                deadline = parse_deadline_line(f"Deadline: Essay | {when} | Canvas")
                self.assertIsNotNone(deadline)
                self.assertEqual(deadline.due, due)
                self.assertEqual(type(deadline.due), type(due))
                self.assertEqual(deadline.has_time, isinstance(due, datetime))

    def test_unreadable_time_keeps_the_date(self) -> None:
        for when in ("2026-10-09 11:59 PM ET", "2026-10-09 EOD", "2026-10-09 24:00",
                     "2026-10-09 5", "2026-10-09 25:00", "2026-10-09 end of day"):
            with self.subTest(when=when):
                deadline = parse_deadline_line(f"Deadline: Essay | {when} | Canvas")
                self.assertEqual(deadline.due, date(2026, 10, 9))
                self.assertFalse(deadline.has_time)

    def test_lenient_layout(self) -> None:
        cases = {
            "- **Deadline:** HIST draft | 2026-10-09 | email": ("HIST draft", "email"),
            "* Deadline: HIST draft | 2026-10-09 | email": ("HIST draft", "email"),
            "DEADLINE: HIST draft | 2026-10-09 | email": ("HIST draft", "email"),
            "Deadline : HIST draft|2026-10-09|email": ("HIST draft", "email"),
            "Deadlines: HIST draft | 2026-10-09 | email": ("HIST draft", "email"),
            "Due: HIST draft | 2026-10-09 | email": ("HIST draft", "email"),
            "HIST draft | 2026-10-09 | email": ("HIST draft", "email"),
            "Deadline: Physics: Lab report | 2026-10-09 | Canvas": ("Physics: Lab report", "Canvas"),
            "Physics: Lab report | 2026-10-09 | Canvas": ("Physics: Lab report", "Canvas"),
            "Deadline: HIST draft | 2026-10-09": ("HIST draft", ""),
            "Deadline: HIST draft | 2026-10-09 |": ("HIST draft", ""),
            "Deadline: HIST draft | 2026-10-09 | Canvas | HIST 20": ("HIST draft", "Canvas | HIST 20"),
            "Deadline: [HIST draft](https://canvas.example.edu/a/1) | 2026-10-09 | "
            "[Canvas](https://canvas.example.edu)": ("HIST draft", "Canvas"),
            "Deadline: HIST draft https://canvas.example.edu/a/1 | 2026-10-09 | "
            "email https://mail.example.com": ("HIST draft", "email"),
        }
        for line, (title, source) in cases.items():
            with self.subTest(line=line):
                deadline = parse_deadline_line(line)
                self.assertIsNotNone(deadline)
                self.assertEqual((deadline.title, deadline.source), (title, source))
                self.assertEqual(deadline.due, date(2026, 10, 9))
                self.assertEqual(deadline.origin, ORIGIN_PAGE)

    def test_malformed_lines_are_skipped(self) -> None:
        for line in ("", "   ", "Nothing due in the next 14 days.", "Deadline: HIST draft",
                     "Deadline: HIST draft due 2026-10-09", "Deadline: | 2026-10-09 | Canvas",
                     "Deadline: **  ** | 2026-10-09", "Deadline: HIST draft | next Friday | x",
                     "Deadline: HIST draft | | Canvas", "Deadline: HIST draft | 2026-02-30 | x",
                     "Deadline: HIST draft | 2026-13-01 | x",
                     "Deadline: HIST draft | 2026-10-09 to 2026-10-11 | x",
                     "Deadline: HIST draft | Oct 9 2026-10-09 | x", "| | |", "---"):
            with self.subTest(line=line):
                self.assertIsNone(parse_deadline_line(line))

    def test_skips_are_logged_at_debug_without_text(self) -> None:
        with self.assertLogs(AGENDA_LOGGER, level="DEBUG") as captured:
            self.assertIsNone(parse_deadline_line("Deadline: Secret essay | someday | Canvas"))
            parse_deadline_line("Deadline: Secret essay | 2026-10-09 4:30 PT | Canvas")
        self.assertTrue(all(line.startswith("DEBUG") for line in captured.output))
        output = "\n".join(captured.output)
        self.assertIn("skipped a line", output)
        for text in ("Secret", "Canvas", "someday", "4:30"):
            self.assertNotIn(text, output)


# --------------------------------------------------------------------------
# extract_deadlines
# --------------------------------------------------------------------------

class ExtractDeadlinesTests(unittest.TestCase):
    def page(self) -> list[FlatLine]:
        return [
            heading("Daily Briefing", 1),
            heading("Work (2)"),
            bullet("Email from **Bob** about the budget"),
            heading("Proposed actions (1)"),
            bullet(CHESS),
            heading("Deadlines (4)"),
            bullet(ESSAY),
            bullet(PSET),
            bullet("Deadline: broken | someday | email"),
            bullet("Deadline: Lab 2 report | 2026-10-08 | TA email", depth=1),
            FlatLine(DIVIDER, ""),
            para("   "),
            heading("Ignore"),
            bullet("Promo: 20% off"),
        ]

    def test_section_is_parsed_and_removed(self) -> None:
        lines = self.page()
        found, kept = extract_deadlines(lines)
        self.assertEqual([d.title for d in found],
                         ["HIST Essay #1 draft", "CS 101 Problem set 3", "Lab 2 report"])
        self.assertEqual(kept, lines[:5] + lines[12:])
        self.assertIsInstance(kept, list)

    def test_after_extract_actions_both_sections_are_gone(self) -> None:
        lines = self.page()
        actions, rest = extract_actions(lines)
        deadlines, kept = extract_deadlines(rest)
        self.assertEqual([a.title for a in actions], ["Chess Club Weekly Meeting"])
        self.assertEqual(len(deadlines), 3)
        self.assertEqual(kept, lines[:3] + lines[12:])

    def test_deadlines_first_then_actions_gives_the_same_result(self) -> None:
        # The controller's order: Deadlines (ending at a "Proposed actions" heading), then actions.
        lines = self.page()
        deadlines, rest = extract_deadlines(lines, stop_names=("Proposed actions",))
        actions, kept = extract_actions(rest)
        self.assertEqual([a.title for a in actions], ["Chess Club Weekly Meeting"])
        self.assertEqual(len(deadlines), 3)
        self.assertEqual(kept, lines[:3] + lines[12:])

    def test_deadlines_nested_inside_proposed_actions(self) -> None:
        lines = [heading("Work"), bullet("x"), heading("Proposed actions"), bullet(CHESS),
                 heading("Deadlines", 3), bullet(ESSAY), bullet(PSET), heading("Ignore"), bullet("promo")]
        deadlines, rest = extract_deadlines(lines, stop_names=("Proposed actions",))
        actions, kept = extract_actions(rest)
        self.assertEqual([d.title for d in deadlines], ["HIST Essay #1 draft", "CS 101 Problem set 3"])
        self.assertEqual([(a.title, a.actionable) for a in actions], [("Chess Club Weekly Meeting", True)])
        self.assertEqual(kept, lines[:2] + lines[7:])

    def test_proposed_actions_nested_inside_deadlines_ends_the_section(self) -> None:
        lines = [heading("Work"), bullet("x"), heading("Deadlines"), bullet(ESSAY),
                 heading("Proposed actions (1)", 3), bullet(CHESS), heading("Ignore"), bullet("promo")]
        deadlines, rest = extract_deadlines(lines, stop_names=("Proposed actions",))
        self.assertEqual([d.title for d in deadlines], ["HIST Essay #1 draft"])
        self.assertEqual(rest, lines[:2] + lines[4:])
        actions, kept = extract_actions(rest)
        self.assertEqual([(a.title, a.actionable) for a in actions], [("Chess Club Weekly Meeting", True)])
        self.assertEqual(kept, lines[:2] + lines[6:])
        # A plain "Proposed actions:" paragraph stops it too; without stop names nothing changes.
        plain = [heading("Deadlines"), bullet(ESSAY), para("Proposed actions:"), bullet(CHESS)]
        self.assertEqual(extract_deadlines(plain, stop_names="Proposed actions")[1], plain[2:])
        self.assertEqual(extract_deadlines(plain)[1], [])

    def test_the_section_is_not_spoken(self) -> None:
        briefing = Briefing(page_id="p", header=BriefingHeader(), lines=tuple(self.page()),
                            fetched_at=NOW)
        _, kept = extract_deadlines(briefing.lines)
        script = build_script(Briefing(page_id="p", header=BriefingHeader(), lines=tuple(kept),
                                       fetched_at=NOW), now=NOW, include_note=False)
        spoken = " ".join(item.spoken for section in script.sections for item in section.items)
        shown = " ".join(item.display for section in script.sections for item in section.items)
        for text in ("Deadline", "Essay", "Problem set", "Gradescope", "Lab 2"):
            self.assertNotIn(text, spoken)
            self.assertNotIn(text, shown)
        self.assertIn("budget", spoken)

    def test_no_section_returns_the_lines_unchanged(self) -> None:
        lines = (heading("Work"), bullet(ESSAY), heading("Ignore"))
        found, kept = extract_deadlines(lines)
        self.assertEqual(found, [])
        self.assertEqual(kept, list(lines))

    def test_heading_matching_is_lenient(self) -> None:
        for text in ("DEADLINES", "Deadlines (2)", "\U0001F4C5 Deadlines", "**Deadlines:**",
                     "deadlines -"):
            with self.subTest(heading=text):
                found, kept = extract_deadlines([heading(text), bullet(ESSAY), heading("Ignore")])
                self.assertEqual(len(found), 1)
                self.assertEqual(kept, [heading("Ignore")])

    def test_only_the_whole_name_matches(self) -> None:
        lines = [heading("Deadlines this week"), bullet(ESSAY)]
        self.assertEqual(extract_deadlines(lines), ([], lines))

    def test_custom_heading_names(self) -> None:
        lines = [heading("Due soon"), bullet(ESSAY), heading("Work"), bullet("x")]
        found, kept = extract_deadlines(lines, ("Deadlines", "Due soon"))
        self.assertEqual(len(found), 1)
        self.assertEqual(kept, lines[2:])
        found, _ = extract_deadlines(lines, "due soon")   # a single string works too
        self.assertEqual(len(found), 1)
        self.assertEqual(extract_deadlines(lines), ([], lines))

    def test_nested_section_ends_at_its_own_level(self) -> None:
        lines = [heading("School"), heading("Deadlines", 3), bullet(ESSAY), heading("Notes", 3),
                 bullet("Bring a pencil"), heading("Personal"), bullet("Call mom")]
        found, kept = extract_deadlines(lines)
        self.assertEqual(len(found), 1)
        self.assertEqual(kept, lines[:1] + lines[3:])

    def test_section_at_the_end_of_the_page(self) -> None:
        lines = [heading("Work"), bullet("x"), heading("Deadlines"), bullet(ESSAY), bullet(PSET)]
        found, kept = extract_deadlines(lines)
        self.assertEqual(len(found), 2)
        self.assertEqual(kept, lines[:2])

    def test_markdown_paragraph_headings(self) -> None:
        lines = [para("## Work"), para("Email from Bob"), para("## Deadlines"),
                 para("- " + ESSAY), para("- " + PSET), para("## Ignore"), para("promo")]
        found, kept = extract_deadlines(lines)
        self.assertEqual([d.source for d in found], ["Canvas", "Gradescope"])
        self.assertEqual(kept, lines[:2] + lines[5:])

    def test_plain_label_paragraph_and_bold_titles(self) -> None:
        lines = [heading("Work"), bullet("x"), para("Deadlines:"), bullet(ESSAY), heading("Ignore")]
        found, kept = extract_deadlines(lines)
        self.assertEqual(len(found), 1)
        self.assertEqual(kept, lines[:2] + lines[4:])
        lines = [para("**Work**"), bullet("x"), para("**Deadlines**"), bullet(ESSAY),
                 para("**Ignore**"), bullet("promo")]
        found, kept = extract_deadlines(lines)
        self.assertEqual(len(found), 1)
        self.assertEqual(kept, lines[:2] + lines[4:])

    def test_empty_section_is_still_removed(self) -> None:
        lines = [heading("Deadlines"), para("Nothing due in the next 14 days."), heading("Work")]
        found, kept = extract_deadlines(lines)
        self.assertEqual(found, [])
        self.assertEqual(kept, [heading("Work")])

    def test_logs_counts_never_text(self) -> None:
        with self.assertLogs(AGENDA_LOGGER, level="INFO") as captured:
            extract_deadlines(self.page())
        output = "\n".join(captured.output)
        self.assertIn("4 line(s), 3 read, 1 skipped", output)
        for text in ("HIST", "Problem", "Gradescope", "broken", "someday"):
            self.assertNotIn(text, output)


# --------------------------------------------------------------------------
# deadlines_from_events
# --------------------------------------------------------------------------

class DeadlinesFromEventsTests(unittest.TestCase):
    def matches(self, title: str, **kwargs: object) -> bool:
        event = timed(title, at(7, 10, 0))
        return bool(deadlines_from_events([event], NOW, **kwargs))

    def test_keywords_match_whole_words(self) -> None:
        cases = {
            "Final exam": True,
            "Finals Week": False,
            "Essay due": True,
            "Duet practice": False,
            "DUE: essay": True,
            "Problem set due.": True,
            "Midterm 2": True,
            "Midterms review": False,
            "Quiz 3": True,
            "Quizzes graded": False,
            "Submit proposal": True,
            "Submitted forms": False,
            "Thesis submission": True,
            "Assignment 4": True,
            "Lab report": True,
            "Physics lab-report draft": True,
            "Lab reporting session": False,
            "Label report": False,
            "Job application": True,
            "Applications open house": False,
            "Deadline extension talk": True,
            "Pre-final review": True,
            "Example class": False,
            "Team sync": False,
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(self.matches(title), expected)

    def test_calendar_deadline_fields(self) -> None:
        start = at(7, 10, 0)
        (deadline,) = deadlines_from_events([timed("Final exam", start, location="Gym")], NOW)
        self.assertEqual(deadline, Deadline("Final exam", start, CALENDAR_SOURCE, ORIGIN_CALENDAR))
        self.assertEqual((deadline.source, deadline.origin), ("Calendar", "calendar"))
        self.assertTrue(deadline.has_time)

    def test_custom_keywords(self) -> None:
        self.assertTrue(self.matches("Thesis defense", keywords=("defense",)))
        self.assertFalse(self.matches("Final exam", keywords=("defense",)))
        self.assertTrue(self.matches("Thesis defense", keywords="Defense"))
        self.assertTrue(self.matches("Lab   report", keywords=("lab report",)))
        self.assertFalse(self.matches("Final exam", keywords=()))
        self.assertFalse(self.matches("Final exam", keywords=("", "  ")))
        self.assertTrue(self.matches("C++ due", keywords=("c++ due",)))

    def test_window(self) -> None:
        cases = [
            (timed("Quiz", at(5, 9, 0)), True),                 # earlier today (kept all day)
            (timed("Quiz", at(4, 23, 0)), False),               # yesterday
            (timed("Quiz", at(19, 14, 40)), True),              # exactly 14 days ahead
            (timed("Quiz", at(19, 14, 41)), False),
            (all_day("Essay due", date(2026, 10, 19)), True),
            (all_day("Essay due", date(2026, 10, 20)), False),
            (all_day("Essay due", date(2026, 10, 4)), False),
            (all_day("Essay due", date(2026, 10, 5)), True),
        ]
        for event, expected in cases:
            with self.subTest(event=event):
                self.assertEqual(bool(deadlines_from_events([event], NOW)), expected)
        self.assertEqual(deadlines_from_events([timed("Quiz", at(9, 10, 0))], NOW, days=3), [])
        self.assertEqual(len(deadlines_from_events([timed("Quiz", at(8, 10, 0))], NOW, days=3)), 1)

    def test_all_day_due_dates(self) -> None:
        events = [all_day("Application window", date(2026, 10, 1), date(2026, 10, 9)),
                  all_day("Exam week", date(2026, 10, 12), date(2026, 10, 16)),
                  all_day("Old exam", date(2026, 9, 28), date(2026, 10, 2))]
        found = deadlines_from_events(events, NOW)
        self.assertEqual([(d.title, d.due) for d in found],
                         [("Application window", date(2026, 10, 9)),
                          ("Exam week", date(2026, 10, 12))])
        self.assertFalse(any(d.has_time for d in found))


# --------------------------------------------------------------------------
# merge_deadlines
# --------------------------------------------------------------------------

def page_item(title: str, due: datetime | date, source: str = "Canvas") -> Deadline:
    return Deadline(title, due, source, ORIGIN_PAGE)


def cal_item(title: str, due: datetime | date) -> Deadline:
    return Deadline(title, due, CALENDAR_SOURCE, ORIGIN_CALENDAR)


class MergeDeadlinesTests(unittest.TestCase):
    def test_page_wins_a_fuzzy_duplicate_on_the_same_day(self) -> None:
        page = [page_item("HIST Essay #1 draft", date(2026, 10, 9))]
        calendar = [cal_item("HIST essay #1 draft DUE", at(9, 23, 59))]
        self.assertEqual(merge_deadlines(page, calendar, NOW), page)
        merged = merge_deadlines([], calendar + [cal_item("hist essay 1 draft", date(2026, 10, 9))],
                                 NOW)
        self.assertEqual(merged, calendar)

    def test_same_title_on_another_day_is_kept(self) -> None:
        page = [page_item("Quiz", date(2026, 10, 7))]
        calendar = [cal_item("Quiz", at(9, 10, 0))]
        self.assertEqual(len(merge_deadlines(page, calendar, NOW)), 2)

    def test_dedupe_matches_whole_words_and_ignores_punctuation(self) -> None:
        day = date(2026, 10, 9)
        self.assertEqual(len(merge_deadlines([page_item("Quiz 1", day), page_item("Quiz 10", day)],
                                             [], NOW)), 2)
        self.assertEqual(len(merge_deadlines([page_item("Problem Set 3", day)],
                                             [cal_item("problem set 3!", day)], NOW)), 1)
        self.assertEqual(len(merge_deadlines([page_item("Lab", day)],
                                             [cal_item("Label printing due", day)], NOW)), 2)

    def test_dedupe_compares_days_in_the_time_zone_of_now(self) -> None:
        page = [page_item("Final exam", date(2026, 10, 9))]
        late_utc = cal_item("Final exam", datetime(2026, 10, 10, 2, 0, tzinfo=timezone.utc))
        self.assertEqual(merge_deadlines(page, [late_utc], NOW), page)   # Oct 9, 7 PM PDT

    def test_past_items_are_dropped_but_today_stays(self) -> None:
        page = [page_item("Yesterday", date(2026, 10, 4)),
                page_item("Last night", datetime(2026, 10, 4, 23, 59)),
                page_item("This morning", datetime(2026, 10, 5, 9, 0)),
                page_item("Today", date(2026, 10, 5))]
        merged = merge_deadlines(page, [cal_item("Exam at 8", at(5, 8, 0))], NOW)
        self.assertEqual([d.title for d in merged], ["Exam at 8", "This morning", "Today"])

    def test_items_beyond_the_window_are_dropped(self) -> None:
        page = [page_item("Day 14", date(2026, 10, 19)), page_item("Day 15", date(2026, 10, 20)),
                page_item("Before cutoff", datetime(2026, 10, 19, 14, 40)),
                page_item("After cutoff", datetime(2026, 10, 19, 14, 41))]
        self.assertEqual([d.title for d in merge_deadlines(page, [], NOW)],
                         ["Before cutoff", "Day 14"])
        self.assertEqual([d.title for d in merge_deadlines(page, [], NOW, days=20)],
                         ["Before cutoff", "After cutoff", "Day 14", "Day 15"])

    def test_sorted_by_due_with_dates_at_the_end_of_their_day(self) -> None:
        page = [page_item("Essay", date(2026, 10, 9)), page_item("Quiz", datetime(2026, 10, 9, 10, 0)),
                page_item("Pset", datetime(2026, 10, 6, 23, 59)), page_item("Reading", date(2026, 10, 7))]
        calendar = [cal_item("Midterm", datetime(2026, 10, 6, 16, 0, tzinfo=timezone.utc))]
        merged = merge_deadlines(page, calendar, NOW)
        self.assertEqual([d.title for d in merged], ["Midterm", "Pset", "Reading", "Quiz", "Essay"])

    def test_ties_sort_by_title(self) -> None:
        day = date(2026, 10, 9)
        merged = merge_deadlines([page_item("beta", day), page_item("Alpha", day)], [], NOW)
        self.assertEqual([d.title for d in merged], ["Alpha", "beta"])

    def test_limit(self) -> None:
        page = [page_item(f"Item {n}", date(2026, 10, 6) + timedelta(days=n)) for n in range(10)]
        self.assertEqual([d.title for d in merge_deadlines(page, [], NOW)],
                         [f"Item {n}" for n in range(8)])
        self.assertEqual(len(merge_deadlines(page, [], NOW, limit=3)), 3)
        self.assertEqual(merge_deadlines(page, [], NOW, limit=0), [])
        self.assertEqual(merge_deadlines([], [], NOW), [])

    def test_naive_now(self) -> None:
        naive_now = datetime(2026, 10, 5, 14, 40)
        merged = merge_deadlines([page_item("Essay", date(2026, 10, 9))],
                                 [cal_item("Quiz", datetime(2026, 10, 7, 10, 0))], naive_now)
        self.assertEqual([d.title for d in merged], ["Quiz", "Essay"])


# --------------------------------------------------------------------------
# due_label
# --------------------------------------------------------------------------

class DueLabelTests(unittest.TestCase):
    def test_labels_and_urgency_at_many_offsets(self) -> None:
        cases = [
            (date(2026, 10, 4), "OVERDUE", "high"),
            (date(2026, 10, 5), "TODAY", "high"),
            (datetime(2026, 10, 5, 0, 0), "TODAY 12:00 AM", "high"),
            (datetime(2026, 10, 5, 9, 0), "TODAY 9:00 AM", "high"),
            (datetime(2026, 10, 5, 12, 0), "TODAY 12:00 PM", "high"),
            (datetime(2026, 10, 5, 17, 59), "TODAY 5:59 PM", "high"),
            (datetime(2026, 10, 5, 18, 0), "TONIGHT 6:00 PM", "high"),
            (datetime(2026, 10, 5, 23, 59), "TONIGHT 11:59 PM", "high"),
            (date(2026, 10, 6), "TOMORROW", "high"),
            (datetime(2026, 10, 6, 0, 30), "TOMORROW", "high"),
            (datetime(2026, 10, 6, 23, 59), "TOMORROW", "high"),
            (date(2026, 10, 7), "2D", "medium"),
            (date(2026, 10, 8), "3D", "medium"),
            (date(2026, 10, 9), "4D", "low"),
            (date(2026, 10, 12), "7D", "low"),
            (date(2026, 10, 18), "13D", "low"),
            (date(2026, 10, 19), "OCT 19", "low"),
            (date(2026, 10, 20), "OCT 20", "low"),
            (date(2026, 11, 1), "NOV 1", "low"),
            (date(2027, 1, 4), "JAN 4", "low"),
        ]
        for due, text, urgency in cases:
            with self.subTest(due=due):
                self.assertEqual(due_label(due, NOW), (text, urgency))

    def test_aware_due_is_shown_in_the_time_zone_of_now(self) -> None:
        self.assertEqual(due_label(datetime(2026, 10, 6, 6, 30, tzinfo=timezone.utc), NOW),
                         ("TONIGHT 11:30 PM", "high"))
        self.assertEqual(due_label(datetime(2026, 10, 6, 7, 30, tzinfo=timezone.utc), NOW),
                         ("TOMORROW", "high"))
        self.assertEqual(due_label(at(5, 15, 0), NOW), ("TODAY 3:00 PM", "high"))

    def test_days_count_calendar_dates_not_hours(self) -> None:
        late = at(5, 23, 50)
        self.assertEqual(due_label(datetime(2026, 10, 6, 0, 10), late), ("TOMORROW", "high"))
        self.assertEqual(due_label(datetime(2026, 10, 7, 0, 10), late), ("2D", "medium"))
        early = at(5, 0, 5)
        self.assertEqual(due_label(datetime(2026, 10, 8, 23, 55), early), ("3D", "medium"))
        self.assertEqual(due_label(datetime(2026, 10, 9, 0, 0), early), ("4D", "low"))

    def test_naive_now(self) -> None:
        naive_now = datetime(2026, 10, 5, 14, 40)
        self.assertEqual(due_label(datetime(2026, 10, 5, 23, 59), naive_now),
                         ("TONIGHT 11:59 PM", "high"))
        self.assertEqual(due_label(date(2026, 10, 8), naive_now), ("3D", "medium"))

    def test_labels_for_merged_page_deadlines(self) -> None:
        (essay, pset) = (parse_deadline_line(ESSAY), parse_deadline_line(PSET))
        merged = merge_deadlines([essay, pset], [], NOW)
        self.assertEqual([due_label(d.due, NOW) for d in merged], [("2D", "medium"), ("4D", "low")])


# --------------------------------------------------------------------------
# Daylight saving changes in the machine's own time zone
# --------------------------------------------------------------------------

def _local(*args: int) -> datetime:
    """A wall time with this machine's offset for it, as the app's datetime.now().astimezone()."""
    return datetime(*args).astimezone()


def _changes_between(first: datetime, second: datetime) -> bool:
    return _local(*first.timetuple()[:6]).utcoffset() != _local(*second.timetuple()[:6]).utcoffset()


@unittest.skipUnless(_changes_between(datetime(2026, 10, 31, 12), datetime(2026, 11, 2, 12)),
                     "this machine's time zone has no daylight saving change on 2026-11-01")
class DaylightSavingTests(unittest.TestCase):
    """The evening before a change, "now" still has the old offset; events keep their own wall times."""

    NOW = _local(2026, 10, 31, 19, 0)   # TOMORROW is the day the clocks change

    def test_tomorrow_rows_keep_their_wall_times(self) -> None:
        label, start, end = agenda_window(self.NOW)
        self.assertEqual(label, "TOMORROW")
        events = [timed("Brunch", _local(2026, 11, 1, 10, 0)), timed("Late show", _local(2026, 11, 1, 23, 30), 15),
                  timed("Next day", _local(2026, 11, 2, 0, 15))]
        inside = events_in_window(events, start, end)
        self.assertEqual([e.title for e in inside], ["Brunch", "Late show"])
        self.assertEqual([(r.time_text, r.title) for r in event_rows(inside, self.NOW)],
                         [("10:00", "Brunch"), ("23:30", "Late show")])

    def test_deadline_days_after_the_change(self) -> None:
        due = _local(2026, 11, 3, 23, 30)
        self.assertEqual(due_label(due, self.NOW), ("3D", "medium"))
        found = deadlines_from_events([timed("Essay due", due)], self.NOW)
        self.assertEqual([d.title for d in found], ["Essay due"])
        self.assertEqual(merge_deadlines([], found, self.NOW)[0].title, "Essay due")

    def test_a_fixed_foreign_offset_is_still_used_as_given(self) -> None:
        # UTC is not this machine's offset (it has a daylight saving change), so UTC wall times count:
        # 03:30 UTC on Nov 1 is "tomorrow" in UTC, although it is still Oct 31 on this machine.
        utc_now = datetime(2026, 10, 31, 19, 0, tzinfo=timezone.utc)
        self.assertEqual(due_label(datetime(2026, 11, 1, 3, 30, tzinfo=timezone.utc), utc_now),
                         ("TOMORROW", "high"))


if __name__ == "__main__":
    unittest.main()
