"""The duplicate check (part B): by UID, by title and start, a series by an occurrence, by decided ids; past rows."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from briefing_reader import capture, ics
from briefing_reader.capture import (CANT_ADD, DECIDED, NOT_CHECKED, ON_CALENDAR, PAST, WAITS, CapturedItem, Series,
                                     check_span, summarize, to_card)
from briefing_reader.gcal import CalendarEvent

ZONE = "America/Los_Angeles"
PST = timezone(timedelta(hours=-8))
NOW = datetime(2026, 11, 22, 9, 0)
TODAY = NOW.date()


def timed(title: str, start: datetime, **extra) -> CalendarEvent:
    aware = start.replace(tzinfo=PST)
    return CalendarEvent(title=title, start=aware, end=aware + timedelta(hours=1), **extra)


def run(items, events, decided=frozenset(), past=()):
    return summarize("term.ics", "ics", list(items), past=list(past), events=events,
                     decided=lambda card_id: card_id in decided, zone=ZONE, today=TODAY)


LAB = CapturedItem("event", "Chem lab", start=datetime(2026, 12, 1, 15), end=datetime(2026, 12, 1, 16, 30),
                   series=Series("WEEKLY", by_day=("TU", "TH"), until=date(2027, 5, 1)), uid="lab@example.org")
TALK = CapturedItem("event", "Guest talk", start=datetime(2026, 12, 4, 12), end=datetime(2026, 12, 4, 13))
BREAK = CapturedItem("event", "Winter break", all_day_start=date(2026, 12, 21), all_day_end=date(2027, 1, 3))
ESSAY = CapturedItem("todo", "Essay draft", due=date(2026, 12, 1))


class DedupTests(unittest.TestCase):
    def test_new_items_wait(self) -> None:
        summary = run([LAB, TALK, BREAK, ESSAY], [])
        self.assertEqual([row.status for row in summary.rows], [WAITS] * 4)
        self.assertEqual(len(summary.cards), 4)
        self.assertTrue(summary.checked)
        self.assertEqual(summary.note, "")

    def test_by_uid(self) -> None:
        for event in (CalendarEvent("Renamed", all_day_start=date(2027, 2, 2), ical_uid="lab@example.org"),
                      CalendarEvent("Renamed", all_day_start=date(2027, 2, 2), capture_uid="lab@example.org")):
            with self.subTest(event=event):
                self.assertEqual(run([LAB], [event]).rows[0].status, ON_CALENDAR)

    def test_by_title_and_start(self) -> None:
        events = [timed("guest TALK", datetime(2026, 12, 4, 12)),
                  CalendarEvent("Winter Break", all_day_start=date(2026, 12, 21), all_day_end=date(2027, 1, 3))]
        summary = run([TALK, BREAK], events)
        self.assertEqual([row.status for row in summary.rows], [ON_CALENDAR, ON_CALENDAR])
        self.assertEqual(summary.cards, [])
        # Another time is a new event.
        self.assertEqual(run([TALK], [timed("Guest talk", datetime(2026, 12, 4, 13))]).rows[0].status, WAITS)

    def test_series_by_an_occurrence(self) -> None:
        plain = CapturedItem("event", "Chem lab", start=LAB.start, end=LAB.end, series=LAB.series)
        self.assertEqual(run([plain], [timed("Chem lab", datetime(2026, 12, 10, 15))]).rows[0].status, ON_CALENDAR)
        # A Wednesday, or the right day at another time, is not an occurrence.
        self.assertEqual(run([plain], [timed("Chem lab", datetime(2026, 12, 9, 15))]).rows[0].status, WAITS)
        self.assertEqual(run([plain], [timed("Chem lab", datetime(2026, 12, 10, 16))]).rows[0].status, WAITS)
        weekly = CapturedItem("event", "Seminar", all_day_start=date(2026, 12, 2), series=Series("WEEKLY", 2))
        self.assertEqual(run([weekly], [CalendarEvent("Seminar", all_day_start=date(2026, 12, 16))]).rows[0].status,
                         ON_CALENDAR)
        self.assertEqual(run([weekly], [CalendarEvent("Seminar", all_day_start=date(2026, 12, 9))]).rows[0].status,
                         WAITS)

    def test_decided_ids_and_cant_add(self) -> None:
        card = to_card(TALK, "term.ics", today=TODAY)
        bad = CapturedItem("event", "Berlin call", start=datetime(2026, 12, 4, 9), series=Series("WEEKLY"),
                           problem=capture.CANT_REPEAT_ZONE)
        summary = run([TALK, bad, ESSAY], [], decided={card.id, to_card(ESSAY, "x", today=TODAY).id})
        self.assertEqual([row.status for row in summary.rows], [DECIDED, CANT_ADD, DECIDED])
        self.assertEqual(summary.rows[1].reason, capture.CANT_REPEAT_ZONE)
        self.assertEqual(summary.rows[1].when, "Fri Dec 4 · 9:00-10:00 AM")
        self.assertEqual(summary.rows[1].repeat, "weekly")
        self.assertEqual([c.title for _, c in summary.cards], ["Berlin call"])   # information-only, for Approvals
        self.assertEqual(summary.counts[DECIDED], 2)

    def test_calendar_not_read(self) -> None:
        summary = run([TALK], None)
        self.assertEqual(summary.rows[0].status, WAITS)
        self.assertFalse(summary.checked)
        self.assertEqual(summary.note, NOT_CHECKED)
        self.assertEqual(run([ESSAY], None).note, "")     # a to-do never needs the calendar

    def test_past_rows_and_counts(self) -> None:
        past = [CapturedItem("event", f"Old {n}", start=datetime(2026, 10, 1, 9) + timedelta(days=n)) for n in range(12)]
        summary = summarize("t.ics", "ics", [TALK], past=past, events=[], decided=lambda _i: False, zone=ZONE,
                            today=TODAY, cancelled=2, more=0, unreadable=1)
        self.assertEqual([row.status for row in summary.rows].count(PAST), capture.MAX_PAST_ROWS)
        self.assertEqual(summary.counts["found"], 1 + 12 + 2 + 1)
        self.assertEqual(summary.counts[PAST], 12)
        self.assertEqual(summary.counts["cards"], 1)

    def test_reimport_shows_everything_handled(self) -> None:
        text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:lab@example.org\r\nSUMMARY:Chem lab\r\n"
                "DTSTART:20261201T150000\r\nDTEND:20261201T163000\r\nRRULE:FREQ=WEEKLY;BYDAY=TU,TH\r\nEND:VEVENT\r\n"
                "BEGIN:VEVENT\r\nUID:talk\r\nSUMMARY:Guest talk\r\nDTSTART:20261204T120000\r\nEND:VEVENT\r\n"
                "END:VCALENDAR\r\n")
        first = ics.parse_ics(text, zone=ZONE, now=NOW, windows_zone=lambda _n: None)
        summary = run(first.items, [])
        self.assertEqual([row.status for row in summary.rows], [WAITS, WAITS])
        # After Approve: the series carries Jarvis's capture UID; the talk was added (title and start).
        events = [CalendarEvent("Chem lab", start=datetime(2026, 12, 1, 23, tzinfo=timezone.utc),
                                capture_uid="lab@example.org"),
                  timed("Guest talk", datetime(2026, 12, 4, 12))]
        again = run(ics.parse_ics(text, zone=ZONE, now=NOW, windows_zone=lambda _n: None).items, events)
        self.assertEqual([row.status for row in again.rows], [ON_CALENDAR, ON_CALENDAR])
        self.assertEqual(again.cards, [])

    def test_check_span(self) -> None:
        start, end = check_span([TALK, BREAK], NOW)
        self.assertEqual((start, end), (datetime(2026, 12, 4), datetime(2027, 1, 4)))
        start, end = check_span([CapturedItem("event", "x", start=datetime(2026, 1, 1, 9), series=Series("DAILY"))], NOW)
        self.assertEqual((start, end), (datetime(2026, 11, 22), datetime(2027, 12, 28)))   # at most 400 days
        self.assertIsNone(check_span([ESSAY], NOW))


if __name__ == "__main__":
    unittest.main()
