"""Capture core (part A): Series, its RRULE and words, CapturedItem -> card (no Qt, no network)."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from briefing_reader import capture, gcal
from briefing_reader.actions import (CALENDAR, MODE_CAPTURE, SOURCE_CAPTURE, TODO, card_view, make_calendar,
                                     parse_action_line)
from briefing_reader.capture import (CANT_REPEAT, ENDS_BEFORE, NO_DUE, CapturedItem, Series, check_rrule,
                                     item_from_data, item_to_data, occurrence_days, series_rrule, series_words,
                                     to_card)

TODAY = date(2026, 11, 22)
TZ = "America/Los_Angeles"


def lab(**changes) -> CapturedItem:
    item = CapturedItem("event", "Chem lab", start=datetime(2026, 11, 24, 15, 0), end=datetime(2026, 11, 24, 16, 30))
    return replace(item, **changes)


class SeriesTests(unittest.TestCase):
    def test_rrule_and_exdate_lines(self) -> None:
        series = Series("WEEKLY", 2, ("TU", "TH"), until=date(2027, 5, 1),
                        exdates=(datetime(2026, 12, 3, 15, 0), datetime(2026, 12, 1, 15, 0)))
        self.assertEqual(series_rrule(series, all_day=False),
                         "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH;UNTIL=20270501T235959Z\n"
                         "EXDATE:20261201T150000,20261203T150000")
        all_day = Series("YEARLY", until=date(2030, 10, 5), exdates=(date(2028, 10, 5),))
        self.assertEqual(series_rrule(all_day, all_day=True),
                         "RRULE:FREQ=YEARLY;UNTIL=20301005\nEXDATE;VALUE=DATE:20281005")
        self.assertEqual(series_rrule(Series("DAILY", count=5), all_day=False), "RRULE:FREQ=DAILY;COUNT=5")

    def test_simple_series_match_the_line_parser(self) -> None:
        """A captured weekly series has the same rrule (so the same id) as the briefing's "weekly until" line."""
        line = parse_action_line("Calendar: Chess club | 2026-10-09 15:00-16:00 | weekly until 2026-12-11")
        item = CapturedItem("event", "Chess club", start=datetime(2026, 10, 9, 15, 0), end=datetime(2026, 10, 9, 16, 0),
                            series=Series("WEEKLY", until=date(2026, 12, 11)))
        self.assertEqual(to_card(item, "club.ics", today=TODAY).rrule, line.rrule)
        self.assertEqual(to_card(item, "club.ics", today=TODAY).id, line.id)
        weekdays = parse_action_line("Calendar: Standup | 2026-10-09 09:00-09:15 | weekdays")
        self.assertEqual(series_rrule(Series("WEEKLY", by_day=capture.WEEKDAYS), all_day=False), weekdays.rrule)

    def test_check_rrule_takes_only_known_parts(self) -> None:
        series = check_rrule("RRULE:FREQ=WEEKLY;WKST=SU;INTERVAL=2;BYDAY=TU,TH;UNTIL=20270501T065959Z",
                             to_wall=lambda m: gcal.wall_in_zone(m, TZ))
        self.assertIsInstance(series, Series)
        self.assertEqual((series.freq, series.interval, series.by_day, series.wkst), ("WEEKLY", 2, ("TU", "TH"), "SU"))
        self.assertEqual(series.until, date(2027, 4, 30))   # 06:59:59Z is Apr 30 in Los Angeles
        self.assertEqual(check_rrule("FREQ=MONTHLY;BYDAY=1MO").by_day, ("1MO",))
        self.assertEqual(check_rrule("FREQ=YEARLY;UNTIL=20301005").until, date(2030, 10, 5))
        for bad in ("FREQ=WEEKLY;BYHOUR=9", "FREQ=HOURLY", "FREQ=MINUTELY;INTERVAL=5", "FREQ=YEARLY;BYWEEKNO=20",
                    "FREQ=WEEKLY;BYDAY=1MO", "FREQ=WEEKLY;COUNT=3;UNTIL=20270101", "FREQ=DAILY;INTERVAL=0",
                    "FREQ=MONTHLY;BYMONTHDAY=32", "BYDAY=MO", "FREQ=WEEKLY;FREQ=DAILY", "", "FREQ=WEEKLY;UNTIL=soon",
                    "FREQ=WEEKLY\nEXDATE:20270101"):
            with self.subTest(rule=bad):
                self.assertEqual(check_rrule(bad), CANT_REPEAT)

    def test_words(self) -> None:
        cases = [
            (Series("WEEKLY", 2, ("TU", "TH"), until=date(2027, 5, 1),
                    exdates=(datetime(2026, 12, 1, 15), datetime(2026, 12, 3, 15))),
             "every 2 weeks on Tue and Thu until May 1, 2027 · 2 dates skipped"),
            (Series("WEEKLY", by_day=("TH", "TU"), until=date(2026, 12, 11)), "weekly on Tue and Thu until Dec 11"),
            (Series("WEEKLY", by_day=capture.WEEKDAYS), "weekdays"),
            (Series("DAILY", count=6), "daily, 6 times"),
            (Series("DAILY", 3), "every 3 days"),
            (Series("MONTHLY", by_day=("1MO",)), "monthly on the first Mon"),
            (Series("MONTHLY", by_month_day=(15,)), "monthly on the 15th"),
            (Series("MONTHLY", by_day=capture.WEEKDAYS, by_set_pos=(-1,)), "monthly on the last weekday"),
            (Series("YEARLY", exdates=(date(2028, 1, 1),)), "yearly · 1 date skipped"),
        ]
        for series, words in cases:
            with self.subTest(words=words):
                self.assertEqual(series_words(series, TODAY), words)

    def test_occurrence_walk(self) -> None:
        self.assertEqual(occurrence_days(Series("WEEKLY", by_day=("MO", "WE"), count=5), date(2026, 11, 23)),
                         [date(2026, 11, 23), date(2026, 11, 25), date(2026, 11, 30), date(2026, 12, 2),
                          date(2026, 12, 7)])
        self.assertEqual(occurrence_days(Series("MONTHLY", count=3), date(2027, 1, 31)),
                         [date(2027, 1, 31), date(2027, 3, 31), date(2027, 5, 31)])
        self.assertEqual(occurrence_days(Series("WEEKLY", 2, until=date(2026, 12, 31)), date(2026, 12, 1)),
                         [date(2026, 12, 1), date(2026, 12, 15), date(2026, 12, 29)])
        self.assertEqual(occurrence_days(Series("MONTHLY", by_day=("1MO",), count=3), date(2026, 12, 7)), [])
        item = CapturedItem("event", "x", all_day_start=date(2026, 1, 5), series=Series("WEEKLY", count=3))
        self.assertEqual(capture.last_occurrence(item), date(2026, 1, 19))
        self.assertTrue(capture.is_past(item, datetime(2026, 11, 22, 9)))
        self.assertFalse(capture.is_past(replace(item, series=Series("WEEKLY")), datetime(2026, 11, 22, 9)))


class CardTests(unittest.TestCase):
    def test_one_off_event_shares_the_lines_id(self) -> None:
        line = parse_action_line("Calendar: Chem lab | 2026-11-24 15:00-16:30")
        card = to_card(lab(), "syllabus.pdf", today=TODAY)
        self.assertEqual(card.id, line.id)
        self.assertEqual(make_calendar("Chem lab", start=datetime(2026, 11, 24, 15), end=datetime(2026, 11, 24, 16, 30)).id,
                         line.id)
        self.assertEqual((card.kind, card.source, card.mode), (CALENDAR, SOURCE_CAPTURE, MODE_CAPTURE))
        self.assertTrue(card.actionable)

    def test_series_card(self) -> None:
        item = lab(series=Series("WEEKLY", 2, ("TU", "TH"), until=date(2027, 5, 1),
                                 exdates=(datetime(2026, 12, 1, 15),)),
                   uid="abc@example.org", where_in_source="p. 2")
        card = to_card(item, "syllabus.pdf", today=TODAY)
        self.assertEqual(card.uid, "abc@example.org")
        self.assertEqual(card.notes, "From syllabus.pdf, p. 2")
        self.assertIn("EXDATE:20261201T150000", card.rrule)
        view = card_view(card, TODAY)
        self.assertIn("every 2 weeks on Tue and Thu until May 1, 2027", view.detail)
        self.assertIn("1 date skipped", view.detail)
        self.assertEqual(view.approve_text, "Approve")
        self.assertIn("every 2 weeks on Tue and Thu", card.spoken(TODAY))
        self.assertNotEqual(card.id, to_card(lab(), "syllabus.pdf", today=TODAY).id)
        # The uid is not part of the id.
        self.assertEqual(card.id, to_card(replace(item, uid=""), "syllabus.pdf", today=TODAY).id)

    def test_cant_add_is_information_only(self) -> None:
        for item, reason in ((lab(problem=capture.CANT_REPEAT_ZONE), capture.CANT_REPEAT_ZONE),
                             (lab(series=Series("WEEKLY", until=date(2026, 11, 1))), ENDS_BEFORE),
                             (lab(series=Series("SECONDLY")), CANT_REPEAT),
                             (CapturedItem("event", "x"), capture.NO_DATE)):
            with self.subTest(reason=reason):
                card = to_card(item, "a.ics", today=TODAY)
                self.assertEqual(card.error, reason)
                self.assertFalse(card.decidable)
                self.assertEqual(card_view(card, TODAY).detail, reason)

    def test_text_goes_through_data_and_links_go(self) -> None:
        item = lab(title="Lab <b>|</b> https://evil.example.com/x now", where="Room 4 www.example.org",
                   notes="bring\ngoggles")
        card = to_card(item, "notes.txt", today=TODAY)
        self.assertEqual(card.title, "Lab ‹b›/‹/b› now")
        self.assertEqual(card.where, "Room 4")
        self.assertEqual(card.notes, "bring goggles - From notes.txt")
        self.assertNotIn("http", card.raw + card.notes + card.where)

    def test_todo_card(self) -> None:
        card = to_card(CapturedItem("todo", "Essay draft", due=date(2026, 12, 1)), "board.png", today=TODAY)
        self.assertEqual((card.kind, card.source, card.mode), (TODO, SOURCE_CAPTURE, MODE_CAPTURE))
        self.assertEqual(card.id, parse_action_line("Todo: title=Essay draft | due=2026-12-01").id)
        self.assertEqual(card_view(card, TODAY).approve_text, "Done")
        timed = to_card(CapturedItem("todo", "Quiz", due=datetime(2026, 12, 1, 23, 59)), "b.png", today=TODAY)
        self.assertEqual(timed.field("due"), "2026-12-01T23:59")
        self.assertEqual(to_card(CapturedItem("todo", "Essay"), "b.png", today=TODAY).error, NO_DUE)

    def test_event_body_of_a_series(self) -> None:
        item = lab(series=Series("WEEKLY", by_day=("TU",), count=10, exdates=(datetime(2026, 12, 1, 15),)),
                   uid="u-1")
        card = to_card(item, "term.ics", today=TODAY)
        body = gcal.build_event_body(card, TZ, footer=gcal.event_footer(card))
        self.assertEqual(body["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=10",
                                              f"EXDATE;TZID={TZ}:20261201T150000"])
        self.assertEqual(body["extendedProperties"], {"private": {"jarvisCaptureUid": "u-1"}})
        self.assertEqual(body["description"], "From term.ics\n\nAdded by Jarvis from a capture.")

    def test_item_round_trip(self) -> None:
        items = [lab(series=Series("WEEKLY", 2, ("TU", "TH"), until=date(2027, 5, 1),
                                   exdates=(datetime(2026, 12, 1, 15),), wkst="SU"), uid="x"),
                 CapturedItem("event", "Break", all_day_start=date(2026, 12, 21), all_day_end=date(2027, 1, 3)),
                 CapturedItem("todo", "Essay", due=datetime(2026, 12, 1, 23, 59), where_in_source="p. 3")]
        for item in items:
            with self.subTest(title=item.title):
                self.assertEqual(item_from_data(item_to_data(item)), item)
        for damaged in (None, [], {"kind": "mail"}, {**item_to_data(items[0]), "start": "2026-11-24"},
                        {**item_to_data(items[0]), "start": "2026-11-24T15:00+01:00"},
                        {**item_to_data(items[0]), "title": 5}):
            with self.subTest(damaged=str(damaged)[:40]):
                self.assertIsNone(item_from_data(damaged))

    def test_until_in_utc_uses_the_wall_converter(self) -> None:
        seen = []

        def to_wall(moment: datetime) -> datetime:
            seen.append(moment)
            return datetime(2027, 1, 1, 1, 0)

        self.assertEqual(check_rrule("FREQ=DAILY;UNTIL=20261231T230000Z", to_wall=to_wall).until, date(2027, 1, 1))
        self.assertEqual(seen, [datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)])



class ReviewCoreTests(unittest.TestCase):
    def test_a_todo_without_a_due_keeps_its_title(self) -> None:
        """Review item 9: the information-only card shows the to-do's title, not the card's raw line."""
        card = to_card(CapturedItem("todo", "Bring forms"), "term.ics", today=TODAY)
        self.assertEqual(card.error, NO_DUE)
        self.assertEqual(card.title, "Bring forms")
        self.assertEqual(card_view(card, TODAY).title, "Bring forms")

    def test_a_long_series_far_from_its_start_matches_an_occurrence(self) -> None:
        """Review item 7: the occurrence walk reaches events years after DTSTART."""
        from briefing_reader.gcal import CalendarEvent

        item = CapturedItem("event", "Standup", start=datetime(2020, 1, 6, 9), end=datetime(2020, 1, 6, 9, 15),
                            series=Series("DAILY"))
        card = to_card(item, "x.ics", today=TODAY)
        event = CalendarEvent("Standup", start=datetime(2026, 11, 24, 9, 0))
        self.assertTrue(capture.on_calendar(item, card, [event], ""))


if __name__ == "__main__":
    unittest.main()
