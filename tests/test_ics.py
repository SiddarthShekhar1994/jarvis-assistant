"""Calendar files (part B): ics.parse_ics on made-up files (standard library only, nothing sent anywhere)."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from briefing_reader import capture, ics
from briefing_reader.capture import CANT_REPEAT, CANT_REPEAT_UTC, CANT_REPEAT_ZONE, UNKNOWN_ZONE, to_card

ZONE = "America/Los_Angeles"
NOW = datetime(2026, 11, 22, 9, 0)


def cal(*events: str, method: str = "") -> str:
    head = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//Test//EN\r\n"
    if method:
        head += f"METHOD:{method}\r\n"
    return head + "".join(events) + "END:VCALENDAR\r\n"


def event(*lines: str) -> str:
    return "BEGIN:VEVENT\r\n" + "".join(line + "\r\n" for line in lines) + "END:VEVENT\r\n"


def parse(text: str | bytes, **kw) -> ics.IcsResult:
    kw.setdefault("windows_zone", lambda name: {"Pacific Standard Time": ZONE,
                                                 "W. Europe Standard Time": "Europe/Berlin"}.get(name))
    return ics.parse_ics(text, zone=ZONE, now=NOW, **kw)


GOOGLE_EXPORT = cal(
    "BEGIN:VTIMEZONE\r\nTZID:America/Los_Angeles\r\nX-LIC-LOCATION:America/Los_Angeles\r\nBEGIN:DAYLIGHT\r\n"
    "TZOFFSETFROM:-0800\r\nTZOFFSETTO:-0700\r\nTZNAME:PDT\r\nDTSTART:19700308T020000\r\n"
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\nEND:DAYLIGHT\r\nEND:VTIMEZONE\r\n",
    event("DTSTART;TZID=America/Los_Angeles:20261201T150000", "DTEND;TZID=America/Los_Angeles:20261201T163000",
          "RRULE:FREQ=WEEKLY;WKST=SU;UNTIL=20270501T065959Z;BYDAY=TU,TH",
          "EXDATE;TZID=America/Los_Angeles:20261203T150000",
          "DTSTAMP:20261120T000000Z", "UID:chem-lab@example.org", "SEQUENCE:0", "STATUS:CONFIRMED",
          "SUMMARY:Chem lab\\, section 2", "LOCATION:Hall 4\\; room 12",
          "DESCRIPTION:Bring goggles\\nand a lab coat"),
    event("DTSTART;VALUE=DATE:20261221", "DTEND;VALUE=DATE:20270104", "UID:break@example.org",
          "SUMMARY:Winter break"),
)


class IcsTests(unittest.TestCase):
    def test_google_style_export(self) -> None:
        result = parse(GOOGLE_EXPORT)
        self.assertEqual(result.problem, "")
        lab, rest = result.items
        self.assertEqual(lab.title, "Chem lab, section 2")
        self.assertEqual(lab.where, "Hall 4; room 12")
        self.assertEqual(lab.notes, "Bring goggles and a lab coat")
        self.assertEqual((lab.start, lab.end), (datetime(2026, 12, 1, 15), datetime(2026, 12, 1, 16, 30)))
        self.assertEqual(lab.series.by_day, ("TU", "TH"))
        self.assertEqual(lab.series.until, date(2027, 4, 30))
        self.assertEqual(lab.series.exdates, (datetime(2026, 12, 3, 15),))
        self.assertEqual(lab.uid, "chem-lab@example.org")
        self.assertEqual((rest.all_day_start, rest.all_day_end), (date(2026, 12, 21), date(2027, 1, 3)))
        card = to_card(lab, "term.ics", today=NOW.date())
        self.assertTrue(card.actionable)
        self.assertEqual(card.rrule, "RRULE:FREQ=WEEKLY;WKST=SU;BYDAY=TU,TH;UNTIL=20270430T235959Z\n"
                                     "EXDATE:20261203T150000")

    def test_folding_and_escapes(self) -> None:
        text = cal(event("UID:f1", "DTSTART:20261210T180000", "SUMMARY:A very long title that is fol",
                         " ded over\r\n\tthree lines", "DESCRIPTION:one\\, two\\; three\\\\ four\\Nfive"))
        text = text.replace("SUMMARY:A very long title that is fol\r\n ded over\r\n\tthree lines\r\n",
                            "SUMMARY:A very long title that is fol\r\n ded over\r\n\t three lines\r\n")
        item = parse(text).items[0]
        self.assertEqual(item.title, "A very long title that is folded over three lines")
        self.assertEqual(item.notes, "one, two; three\\ four five")
        # Bare LF files are read too.
        self.assertEqual(parse(text.replace("\r\n", "\n")).items[0].title, item.title)

    def test_floating_utc_and_zones(self) -> None:
        result = parse(cal(
            event("UID:u1", "DTSTART:20261210T180000Z", "DTEND:20261210T190000Z", "SUMMARY:UTC one-off"),
            event("UID:u2", "DTSTART:20261210T180000", "SUMMARY:Floating"),
            event("UID:u3", 'DTSTART;TZID="W. Europe Standard Time":20261210T180000', "DURATION:PT45M",
                  "SUMMARY:Berlin by its Windows name"),
            event("UID:u4", "DTSTART;TZID=Mars/Olympus:20261210T180000", "SUMMARY:Unknown zone"),
        ))
        by_title = {item.title: item for item in result.items}
        self.assertEqual(by_title["UTC one-off"].start, datetime(2026, 12, 10, 10, 0))     # 18:00Z is 10 AM PST
        self.assertEqual(by_title["Floating"].start, datetime(2026, 12, 10, 18, 0))
        self.assertEqual(by_title["Floating"].end, datetime(2026, 12, 10, 19, 0))          # an hour by default
        berlin = by_title["Berlin by its Windows name"]
        self.assertEqual((berlin.start, berlin.end), (datetime(2026, 12, 10, 9, 0), datetime(2026, 12, 10, 9, 45)))
        self.assertEqual(by_title["Unknown zone"].problem, UNKNOWN_ZONE)
        self.assertFalse(to_card(by_title["Unknown zone"], "x.ics").decidable)

    def test_series_in_another_zone_or_utc_is_information_only(self) -> None:
        result = parse(cal(
            event("UID:s1", "DTSTART;TZID=Europe/Berlin:20261210T180000", "RRULE:FREQ=WEEKLY", "SUMMARY:Berlin"),
            event("UID:s2", "DTSTART:20261210T180000Z", "RRULE:FREQ=WEEKLY", "SUMMARY:UTC"),
            event("UID:s3", "DTSTART;TZID=US/Pacific:20261210T180000", "RRULE:FREQ=WEEKLY", "SUMMARY:Same rules"),
            event("UID:s4", "DTSTART:20261210T180000", "RRULE:FREQ=WEEKLY;BYHOUR=9,10", "SUMMARY:By hour"),
            event("UID:s5", "DTSTART:20261210T180000", "RRULE:FREQ=WEEKLY", "RDATE:20261225T180000",
                  "SUMMARY:With RDATE"),
        ))
        problems = {item.title: item.problem for item in result.items}
        self.assertEqual(problems, {"Berlin": CANT_REPEAT_ZONE, "UTC": CANT_REPEAT_UTC, "Same rules": "",
                                    "By hour": CANT_REPEAT, "With RDATE": CANT_REPEAT})

    def test_all_day_ranges_and_duration(self) -> None:
        result = parse(cal(
            event("UID:d1", "DTSTART;VALUE=DATE:20261205", "SUMMARY:One day"),
            event("UID:d2", "DTSTART;VALUE=DATE:20261205", "DTEND;VALUE=DATE:20261206", "SUMMARY:Also one day"),
            event("UID:d3", "DTSTART;VALUE=DATE:20261205", "DURATION:P3D", "SUMMARY:Three days"),
            event("UID:d4", "DTSTART:20261205T220000", "DURATION:PT3H", "SUMMARY:Past midnight"),
        ))
        spans = {item.title: (item.all_day_start, item.all_day_end, item.end) for item in result.items}
        self.assertEqual(spans["One day"][:2], (date(2026, 12, 5), date(2026, 12, 5)))
        self.assertEqual(spans["Also one day"][:2], (date(2026, 12, 5), date(2026, 12, 5)))
        self.assertEqual(spans["Three days"][:2], (date(2026, 12, 5), date(2026, 12, 7)))
        self.assertEqual(spans["Past midnight"][2], datetime(2026, 12, 6, 1, 0))

    def test_moved_and_cancelled_occurrences(self) -> None:
        result = parse(cal(
            event("UID:r1", "DTSTART:20261201T090000", "DTEND:20261201T100000", "RRULE:FREQ=DAILY;COUNT=10",
                  "SUMMARY:Standup"),
            event("UID:r1", "RECURRENCE-ID:20261203T090000", "DTSTART:20261203T110000", "DTEND:20261203T120000",
                  "SUMMARY:Standup (late)"),
            event("UID:r1", "RECURRENCE-ID:20261205T090000", "DTSTART:20261205T090000", "STATUS:CANCELLED",
                  "SUMMARY:Standup"),
        ))
        series = next(item for item in result.items if item.series is not None)
        self.assertEqual(sorted(series.series.exdates), [datetime(2026, 12, 3, 9), datetime(2026, 12, 5, 9)])
        moved = next(item for item in result.items if item.title == "Standup (late)")
        self.assertEqual(moved.start, datetime(2026, 12, 3, 11))
        self.assertIsNone(moved.series)
        self.assertEqual(moved.uid, "r1;20261203T090000")
        self.assertEqual(result.cancelled, 1)
        self.assertEqual(len(result.items), 2)

    def test_cancelled_and_method_cancel_propose_nothing(self) -> None:
        result = parse(cal(event("UID:c1", "DTSTART:20261210T180000", "STATUS:CANCELLED", "SUMMARY:Off")))
        self.assertEqual((result.items, result.cancelled), ([], 1))
        result = parse(cal(event("UID:c2", "DTSTART:20261210T180000", "SUMMARY:Meeting"),
                           event("UID:c3", "DTSTART:20261211T180000", "SUMMARY:Other"), method="CANCEL"))
        self.assertEqual((result.items, result.cancelled), ([], 2))

    def test_highest_sequence_wins(self) -> None:
        result = parse(cal(
            event("UID:q1", "SEQUENCE:2", "DTSTART:20261210T150000", "SUMMARY:Second version"),
            event("UID:q1", "SEQUENCE:1", "DTSTART:20261210T140000", "SUMMARY:First version"),
        ))
        self.assertEqual([item.title for item in result.items], ["Second version"])

    def test_vtodo(self) -> None:
        result = parse(cal(
            "BEGIN:VTODO\r\nUID:t1\r\nSUMMARY:Essay draft\r\nDUE;VALUE=DATE:20261201\r\nEND:VTODO\r\n",
            "BEGIN:VTODO\r\nUID:t2\r\nSUMMARY:Lab report\r\nDUE:20261202T235900\r\nEND:VTODO\r\n",
            "BEGIN:VTODO\r\nUID:t3\r\nSUMMARY:Done one\r\nSTATUS:COMPLETED\r\nDUE;VALUE=DATE:20261201\r\nEND:VTODO\r\n",
            "BEGIN:VTODO\r\nUID:t4\r\nSUMMARY:Overdue\r\nDUE;VALUE=DATE:20261101\r\nEND:VTODO\r\n",
        ))
        self.assertEqual([(item.kind, item.title, item.due) for item in result.items],
                         [("todo", "Essay draft", date(2026, 12, 1)),
                          ("todo", "Lab report", datetime(2026, 12, 2, 23, 59))])
        self.assertEqual(sorted(item.title for item in result.past), ["Done one", "Overdue"])

    def test_past_entries_are_counted(self) -> None:
        result = parse(cal(
            event("UID:p1", "DTSTART:20261121T150000", "DTEND:20261121T160000", "SUMMARY:Yesterday"),
            event("UID:p2", "DTSTART:20261001T150000", "RRULE:FREQ=WEEKLY;COUNT=3", "SUMMARY:Finished series"),
            event("UID:p3", "DTSTART:20261001T150000", "RRULE:FREQ=WEEKLY;UNTIL=20261231T235959Z",
                  "SUMMARY:Ongoing series"),
            event("UID:p4", "DTSTART:20261122T080000", "DTEND:20261122T083000", "SUMMARY:Earlier today"),
        ))
        self.assertEqual(sorted(item.title for item in result.past), ["Earlier today", "Finished series", "Yesterday"])
        self.assertEqual([item.title for item in result.items], ["Ongoing series"])

    def test_garbage_and_limits(self) -> None:
        self.assertEqual(parse(b"\x00\xff not a calendar at all").problem, ics.NOT_CALENDAR)
        self.assertEqual(parse("BEGIN:VEVENT\r\nSUMMARY:x\r\nEND:VEVENT\r\n").problem, ics.NOT_CALENDAR)
        self.assertEqual(parse(b"BEGIN:VCALENDAR\r\n" + b"X" * (capture.MAX_FILE_BYTES + 1)).problem, ics.TOO_LARGE)
        many = cal(*(event(f"UID:m{n}", "DTSTART:20261210T150000", f"SUMMARY:E{n}") for n in range(501)))
        self.assertEqual(parse(many).problem, ics.TOO_MANY)
        fifty_plus = cal(*(event(f"UID:k{n}", f"DTSTART:2026121{n % 10}T15{n:02d}00", f"SUMMARY:K{n}")
                           for n in range(55)))
        result = parse(fifty_plus)
        self.assertEqual((len(result.items), result.more), (50, 5))
        broken = parse(cal(event("UID:b1", "DTSTART:2026-12-10", "SUMMARY:Bad date"),
                           event("UID:b2", "SUMMARY:No start"), "garbage line without colon\r\n",
                           event("UID:b3", "DTSTART:20261310T150000", "SUMMARY:Month 13")))
        self.assertEqual((broken.items, broken.unreadable), ([], 3))

    def test_cp1252_file(self) -> None:
        text = cal(event("UID:w1", "DTSTART:20261210T150000", "SUMMARY:Café meeting")).encode("cp1252")
        self.assertEqual(parse(text).items[0].title, "Café meeting")



def early(text: str) -> ics.IcsResult:
    """The review's probes are read on Oct 1, 2026 (ics_probe.py's now)."""
    return ics.parse_ics(text, zone=ZONE, now=datetime(2026, 10, 1, 12, 0), windows_zone=lambda _name: None)


class ReviewTests(unittest.TestCase):
    """Review items 3 and 6 (ics_probe.py)."""

    def test_a_date_exdate_of_a_timed_series_skips_at_its_start_time(self) -> None:
        result = early(cal(event("UID:a", "SUMMARY:Seminar", "DTSTART;TZID=America/Los_Angeles:20261006T090000",
                                 "DTEND;TZID=America/Los_Angeles:20261006T100000", "RRULE:FREQ=WEEKLY;COUNT=10",
                                 "EXDATE;VALUE=DATE:20261013")))
        self.assertEqual(result.items[0].series.exdates, (datetime(2026, 10, 13, 9, 0),))

    def test_moved_occurrences_of_a_series_jarvis_cant_add_are_kept(self) -> None:
        result = early(cal(
            event("UID:b", "SUMMARY:Lab", "DTSTART;TZID=America/Los_Angeles:20261006T090000",
                  "RRULE:FREQ=WEEKLY;BYHOUR=9"),
            event("UID:b", "RECURRENCE-ID;TZID=America/Los_Angeles:20261013T090000", "SUMMARY:Lab (moved)",
                  "DTSTART;TZID=America/Los_Angeles:20261014T140000")))
        titles = {item.title: item for item in result.items}
        self.assertEqual(titles["Lab"].problem, CANT_REPEAT)
        self.assertEqual(titles["Lab (moved)"].start, datetime(2026, 10, 14, 14, 0))

    def test_an_updated_copy_of_a_one_off_replaces_it(self) -> None:
        result = early(cal(
            event("UID:c", "SUMMARY:Review", "DTSTART;TZID=America/Los_Angeles:20261020T090000"),
            event("UID:c", "RECURRENCE-ID;TZID=America/Los_Angeles:20261020T090000", "SUMMARY:Review",
                  "DTSTART;TZID=America/Los_Angeles:20261021T150000")))
        self.assertEqual([(item.start, item.uid) for item in result.items], [(datetime(2026, 10, 21, 15, 0), "c")])

    def test_an_until_before_the_start_time_ends_the_day_before(self) -> None:
        result = early(cal(event("UID:d", "SUMMARY:Chem", "DTSTART;TZID=America/Los_Angeles:20261027T180000",
                                 "DTEND;TZID=America/Los_Angeles:20261027T190000",
                                 "RRULE:FREQ=WEEKLY;UNTIL=20261111T015959Z")))
        self.assertEqual(result.items[0].series.until, date(2026, 11, 9))     # Nov 10 17:59:59 is before 18:00
        result = early(cal(event("UID:e", "SUMMARY:Chem", "DTSTART;TZID=America/Los_Angeles:20261027T180000",
                                 "RRULE:FREQ=WEEKLY;UNTIL=20261111T020000Z")))
        self.assertEqual(result.items[0].series.until, date(2026, 11, 10))    # 18:00 itself is still in

    def test_an_unreadable_exdate_is_counted(self) -> None:
        result = early(cal(event("UID:f", "SUMMARY:S", "DTSTART:20261006T090000", "RRULE:FREQ=WEEKLY",
                                 "EXDATE;TZID=Mars/Olympus:20261013T090000")))
        self.assertEqual(result.unreadable, 1)


if __name__ == "__main__":
    unittest.main()
