"""Instant answers from the calendars (round 2; briefing_reader.instant_calendar): the replies from fake events of two
accounts - all-day events, overlaps, an event declined or shown as free, a signed-out account, a read from before
midnight - and above all that what is said never holds a title or a place. Pure: no Qt, no Google, a fixed clock."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from briefing_reader import gcal, instant, instant_calendar
from briefing_reader.gcal import CalendarEvent
from briefing_reader.instant import CAL_COUNT, CAL_DAY, CAL_FREE, CAL_MORE, CAL_NEXT, CalendarAsk
from briefing_reader.instant_calendar import PROBLEM_ERROR, PROBLEM_SIGNED_OUT, CalendarRead

PACIFIC = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 10, 13, 20, tzinfo=PACIFIC)          # a Tuesday, 1:20 PM
TODAY = NOW.date()
TOMORROW = TODAY + timedelta(days=1)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=PACIFIC)


def timed(title: str, day: date, start: tuple[int, int], end: tuple[int, int], **extra) -> CalendarEvent:
    return CalendarEvent(title, at(day, *start), at(day, *end), **extra)


PERSONAL = [
    timed("Standup", TODAY, (9, 0), (9, 30)),
    timed("Lunch with Priya", TODAY, (12, 0), (13, 30), location="Cafe Rosa"),
    timed("Physio", TODAY, (14, 30), (15, 0), location="https://meet.google.com/abc-defg-hij"),
    timed("Skipped party", TODAY, (15, 0), (16, 0), declined=True),
    timed("Focus block", TODAY, (16, 0), (17, 0), free=True),
    CalendarEvent("Diwali", all_day_start=TODAY, all_day_end=TODAY),
    timed("Gym", TOMORROW, (7, 0), (8, 0)),
]
WORK = [
    timed("Design review", TODAY, (14, 0), (15, 0), location="Room 4"),
    timed("Dentist", TOMORROW, (16, 0), (17, 0), location="123 Main St"),
]
SECRETS = ("Standup", "Lunch", "Priya", "Cafe Rosa", "Physio", "meet.google", "Skipped party", "Focus block", "Diwali",
           "Gym", "Design review", "Room 4", "Dentist", "Main St")


def a_read(personal=PERSONAL, work=WORK, problems=None, now: datetime = NOW) -> CalendarRead:
    start, end = instant_calendar.read_range(now)
    events = {"personal": list(personal), "work": list(work)}
    return CalendarRead(events=events, problems=dict(problems or {}), start=start, end=end)


def reply(ask: CalendarAsk, read: CalendarRead | None = None, *, now: datetime = NOW, hour24: bool = False):
    return instant_calendar.reply(ask, read or a_read(), now=now, address="sir", hour24=hour24)


ASKS = (CalendarAsk(CAL_NEXT), CalendarAsk(CAL_MORE), CalendarAsk(CAL_DAY), CalendarAsk(CAL_DAY, day=1),
        CalendarAsk(CAL_COUNT), CalendarAsk(CAL_COUNT, day=1), CalendarAsk(CAL_FREE, at=at(TODAY, 14, 15)),
        CalendarAsk(CAL_FREE, at=at(TODAY, 15, 30), busy=True), CalendarAsk(CAL_FREE, at=at(TOMORROW, 16)))


class SpeechTests(unittest.TestCase):
    def test_what_is_said_never_holds_a_title_or_a_place(self) -> None:
        reads = (a_read(), a_read(problems={"work": PROBLEM_SIGNED_OUT}), a_read(personal=[], work=[]),
                 a_read(problems={"personal": PROBLEM_ERROR, "work": PROBLEM_SIGNED_OUT}))
        for read in reads:
            for ask in ASKS:
                with self.subTest(ask=ask, read=read):
                    found = reply(ask, read)
                    for secret in SECRETS:
                        self.assertNotIn(secret.casefold(), found.speech.casefold())

    def test_the_screen_shows_titles_times_places_and_calendars(self) -> None:
        found = reply(CalendarAsk(CAL_DAY))
        self.assertIn("Today, Tuesday 10 November:", found.text)
        self.assertIn("• 2:00–3:00 PM  Design review (Room 4 · work)", found.text)
        self.assertIn("• 2:30–3:00 PM  Physio (meet.google.com · personal)", found.text)
        self.assertIn("• All day  Diwali (personal)", found.text)
        self.assertIn("Focus block (personal · shown as free)", found.text)
        self.assertNotIn("Skipped party", found.text)               # declined: left out
        self.assertEqual(found.kind, instant.KIND_CALENDAR)
        self.assertEqual(found.source, instant_calendar.SOURCE)
        self.assertNotIn("Design", repr(found))


class ReplyTests(unittest.TestCase):
    def test_the_next_meeting(self) -> None:
        found = reply(CalendarAsk(CAL_NEXT))
        self.assertEqual(found.speech, "Your next meeting is at 2 PM, sir, in 40 minutes.")
        self.assertIn("On now: Lunch with Priya, until 1:30 PM (personal).", found.text)
        self.assertIn("Your next meeting is at 2:00 PM, in 40 minutes:", found.text)
        self.assertEqual(found.brief, "Next: Design review at 2:00 PM (in 40 minutes)")
        self.assertEqual(reply(CalendarAsk(CAL_NEXT, noun="call")).speech, "Your next call is at 2 PM, sir, in 40 minutes.")

    def test_the_next_one_tomorrow_and_none(self) -> None:
        evening = at(TODAY, 18, 0)
        found = reply(CalendarAsk(CAL_NEXT), a_read(now=evening), now=evening)
        self.assertEqual(found.speech, "Your next meeting is tomorrow at 7 AM, sir.")
        self.assertIn("Nothing more today.", found.text)
        empty = reply(CalendarAsk(CAL_NEXT), a_read(personal=[], work=[]))
        self.assertEqual(empty.speech, "Nothing on your calendar for the rest of today or tomorrow, sir.")

    def test_two_at_once(self) -> None:
        two = [timed("A", TODAY, (14, 0), (15, 0)), timed("B", TODAY, (14, 0), (14, 30))]
        found = reply(CalendarAsk(CAL_NEXT), a_read(personal=two, work=[]))
        self.assertEqual(found.speech, "You have 2 meetings next, at 2 PM, sir, in 40 minutes.")
        self.assertIn("2 at once", found.text)

    def test_any_more_today(self) -> None:
        found = reply(CalendarAsk(CAL_MORE))
        self.assertEqual(found.speech, "Yes, sir: 3 more today; the next is at 2 PM, in 40 minutes.")
        late = at(TODAY, 17, 30)
        none = reply(CalendarAsk(CAL_MORE), a_read(now=late), now=late)
        self.assertEqual(none.speech, "No more meetings today, sir.")
        self.assertIn("Tomorrow's first: Gym at 7:00 AM.", none.text)

    def test_a_day(self) -> None:
        # Today: 6 events (the declined one left out), 5 of them at a set time (the all-day one is not a meeting).
        self.assertEqual(reply(CalendarAsk(CAL_DAY)).speech, "You have 6 events today, sir; the next is at 2 PM.")
        self.assertEqual(reply(CalendarAsk(CAL_DAY, day=1)).speech,
                         "You have 2 events tomorrow, sir; the first is at 7 AM.")
        self.assertEqual(reply(CalendarAsk(CAL_COUNT)).speech, "You have 5 meetings today, sir; the next is at 2 PM.")
        night = at(TODAY, 22, 0)
        self.assertEqual(reply(CalendarAsk(CAL_COUNT), a_read(now=night), now=night).speech,
                         "You had 5 meetings today, sir; none are left.")
        holiday = [CalendarEvent("Diwali", all_day_start=TODAY, all_day_end=TODAY)]
        self.assertEqual(reply(CalendarAsk(CAL_COUNT), a_read(personal=holiday, work=[])).speech,
                         "No meetings today, sir; just 1 all-day event.")
        self.assertEqual(reply(CalendarAsk(CAL_DAY), a_read(personal=holiday, work=[])).speech,
                         "You have 1 all-day event today, sir.")
        self.assertEqual(reply(CalendarAsk(CAL_DAY, day=1), a_read(personal=[], work=[])).speech,
                         "Nothing on your calendar tomorrow, sir.")

    def test_free_or_busy(self) -> None:
        busy = reply(CalendarAsk(CAL_FREE, at=at(TODAY, 14, 15)))
        self.assertEqual(busy.speech, "No, sir: you have a meeting from 2 to 3 PM.")
        self.assertIn("You're busy at 2:15 PM:", busy.text)
        self.assertIn("Design review", busy.text)
        overlap = reply(CalendarAsk(CAL_FREE, at=at(TODAY, 14, 45), busy=True))
        self.assertEqual(overlap.speech, "Yes, sir: you have 2 meetings then, the first from 2 to 3 PM.")
        declined = reply(CalendarAsk(CAL_FREE, at=at(TODAY, 15, 30)))          # only a declined event then
        self.assertEqual(declined.speech, "You're free at 3:30 PM, sir.")
        shown_free = reply(CalendarAsk(CAL_FREE, at=at(TODAY, 16, 30), busy=True))   # "show as free" and all day
        self.assertEqual(shown_free.speech, "No, sir: you're free at 4:30 PM.")
        self.assertIn("Shown as free then: Focus block.", shown_free.text)
        self.assertIn("All day: Diwali.", shown_free.text)
        until = reply(CalendarAsk(CAL_FREE, at=at(TOMORROW, 10)))
        self.assertEqual(until.speech, "You're free at 10 AM tomorrow, sir, until 4 PM.")
        self.assertEqual(reply(CalendarAsk(CAL_FREE, at=at(TOMORROW, 16), busy=True)).speech,
                         "Yes, sir: you have a meeting from 4 to 5 PM.")

    def test_the_24_hour_clock_on_screen_only(self) -> None:
        found = reply(CalendarAsk(CAL_NEXT), hour24=True)
        self.assertIn("14:00–15:00", found.text)
        self.assertIn("2 PM", found.speech)


class CoverageTests(unittest.TestCase):
    def test_an_account_that_could_not_be_read_is_said_plainly(self) -> None:
        read = a_read(problems={"work": PROBLEM_SIGNED_OUT})
        found = reply(CalendarAsk(CAL_FREE, at=at(TODAY, 14, 15)), read)
        # The design review is on the work calendar: free on what could be read, and never just "free".
        self.assertEqual(found.speech, "You're free at 2:15 PM on your personal calendar, sir, until 2:30 PM, but your "
                                       "work calendar couldn't be read.")
        self.assertIn("Your work calendar couldn't be read: its sign-in has expired or was removed", found.text)
        self.assertIn("This covers your personal calendar only.", found.text)
        self.assertIn("work calendar couldn't be read", found.brief)
        nxt = reply(CalendarAsk(CAL_NEXT), read)
        self.assertEqual(nxt.speech, "Your next meeting on your personal calendar is at 2:30 PM, sir, in 1 hour and 10 "
                                     "minutes; your work calendar couldn't be read.")
        empty = reply(CalendarAsk(CAL_DAY, day=1), a_read(work=[], personal=[], problems={"work": PROBLEM_ERROR}))
        self.assertEqual(empty.speech, "Nothing on your personal calendar tomorrow, sir; your work calendar couldn't be "
                                       "read.")

    def test_none_read(self) -> None:
        found = reply(CalendarAsk(CAL_NEXT), a_read(problems={"personal": PROBLEM_SIGNED_OUT,
                                                              "work": PROBLEM_SIGNED_OUT}))
        self.assertEqual(found.speech, "I couldn't read your personal and work calendars, sir: they need signing in "
                                       "again.")
        self.assertIn("couldn't be read", found.text)

    def test_a_read_from_before_midnight_does_not_answer(self) -> None:
        yesterday = NOW - timedelta(days=1)
        self.assertIsNone(reply(CalendarAsk(CAL_NEXT), a_read(now=yesterday)))
        self.assertIsNone(reply(CalendarAsk(CAL_NEXT), CalendarRead(events={"personal": []})))
        self.assertTrue(a_read().covers(NOW))
        self.assertEqual(repr(a_read()), "CalendarRead(2 read, 0 not)")


class GoogleEventTests(unittest.TestCase):
    def test_declined_and_shown_as_free_come_from_google(self) -> None:
        item = {"id": "e1", "summary": "Party", "start": {"dateTime": "2026-11-10T15:00:00-08:00"},
                "end": {"dateTime": "2026-11-10T16:00:00-08:00"}, "transparency": "transparent",
                "attendees": [{"email": "me@example.com", "self": True, "responseStatus": "declined"},
                              {"email": "sam@example.com", "responseStatus": "accepted"}]}
        event = gcal._event_from(item, "primary")
        self.assertEqual((event.declined, event.free), (True, True))
        plain = gcal._event_from({"id": "e2", "summary": "Sync", "start": {"dateTime": "2026-11-10T15:00:00-08:00"},
                                  "end": {"dateTime": "2026-11-10T16:00:00-08:00"},
                                  "attendees": "not a list"}, "primary")
        self.assertEqual((plain.declined, plain.free), (False, False))


if __name__ == "__main__":
    unittest.main()
