"""Add all's rules (mail extras, part 1): only own-calendar adds go together, and only when nothing says to wait.

A Calendar event and a Todo's block go; RSVP / Move / Cancel, Reply / Email, hand-offs, a day plan's block, a card
with an amber note, a card tried before, a decided or running card and a past event never do; nothing goes while the
personal account is not signed in and confirmed. Every title, address and id here is invented."""

from __future__ import annotations

import dataclasses
import logging
import unittest
from datetime import date, datetime

from briefing_reader import batch
from briefing_reader.actions import (MODE_PLAN, STATUS_CREATED, STATUS_EXISTS, STATUS_FAILED, STATUS_UNKNOWN,
                                     parse_action_line)

NOW = datetime(2026, 10, 4, 13, 52)
TODAY = date(2026, 10, 4)
CAL_A = "Calendar: Club general meeting | 2026-10-06 17:00-18:00 | | Student union, room 2 |"
CAL_B = "Calendar: Study block | 2026-10-07 19:00-21:00 | | Library |"
CAL_PAST = "Calendar: Old meeting | 2026-10-01 09:00-10:00 | | |"
CAL_ALLDAY = "Calendar: Book fair | 2026-10-08 | | Hall B |"
TODO_BLOCK = "Todo: title=Read chapter 4 | due=2026-10-09 | block=2026-10-06 19:00-21:00 | acct= | link="
TODO_PLAIN = "Todo: title=Read chapter 5 | due=2026-10-09 | block= | acct= | link="
MOVE = ("Move: acct=work | event=jts0001aa | cal=primary | when=2026-10-09 14:00-15:00 | notify=all | "
        "title=Jarvis test sync | at=2026-10-05 14:00-15:00 | link= | body=")
EMAIL = ("Email: acct=work | to=ana@example.edu | cc= | subject=Budget numbers | due= | link= | "
         "body=Hi Ana,\\nThe numbers are in.")
OPEN = "Open: title=Read the syllabus | link=https://docs.google.com/document/d/abc"


def line(text: str):
    action = parse_action_line(text)
    assert not action.error, action.error
    return action


def pick(actions, **kw):
    args = dict(decided=(), jobs=(), signed_in=True, confirmed=True, now=NOW)
    args.update(kw)
    return batch.eligible(actions, **args)


class EligibleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal_a, self.cal_b, self.todo = line(CAL_A), line(CAL_B), line(TODO_BLOCK)

    def test_calendar_events_and_todo_blocks_go_in_card_order(self) -> None:
        listed = pick([self.cal_a, line(MOVE), self.todo, line(EMAIL), self.cal_b, line(OPEN), line(TODO_PLAIN)])
        self.assertEqual([a.id for a in listed], [self.cal_a.id, self.todo.id, self.cal_b.id])

    def test_an_all_day_event_goes_too(self) -> None:
        self.assertEqual(len(pick([line(CAL_ALLDAY)])), 1)

    def test_sends_changes_and_hand_offs_never_go(self) -> None:
        self.assertEqual(pick([line(MOVE), line(EMAIL), line(OPEN), line(TODO_PLAIN)]), [])

    def test_a_day_plan_block_never_goes(self) -> None:
        block = dataclasses.replace(self.cal_a, mode=MODE_PLAN)
        self.assertEqual(pick([block, self.cal_b]), [self.cal_b])

    def test_a_card_with_an_amber_note_never_goes(self) -> None:
        warned = dataclasses.replace(self.cal_a, warnings=("Check the time",))
        self.assertEqual(pick([warned, self.cal_b]), [self.cal_b])
        self.assertEqual(pick([self.cal_a, self.cal_b], noted={self.cal_a.id}), [self.cal_b])

    def test_decided_running_and_tried_cards_never_go(self) -> None:
        cards = [self.cal_a, self.cal_b, self.todo]
        self.assertEqual(pick(cards, decided={self.cal_a.id}), [self.cal_b, self.todo])
        self.assertEqual(pick(cards, jobs={self.cal_b.id}), [self.cal_a, self.todo])
        self.assertEqual(pick(cards, tried={self.todo.id}), [self.cal_a, self.cal_b])

    def test_a_past_event_never_goes(self) -> None:
        self.assertEqual(pick([line(CAL_PAST), self.cal_a]), [self.cal_a])

    def test_nothing_goes_while_the_personal_account_is_not_signed_in_and_confirmed(self) -> None:
        cards = [self.cal_a, self.cal_b]
        self.assertEqual(pick(cards, signed_in=False), [])
        self.assertEqual(pick(cards, confirmed=False), [])

    def test_a_repeated_id_is_listed_once(self) -> None:
        self.assertEqual(pick([self.cal_a, self.cal_a, self.cal_b]), [self.cal_a, self.cal_b])


class RowsTests(unittest.TestCase):
    def test_rows_say_what_each_card_adds_and_where(self) -> None:
        cal, todo = line(CAL_A), line(TODO_BLOCK)
        rows = batch.rows([cal, todo], TODAY)
        self.assertEqual((rows[0].kind, rows[0].title), ("Event", "Club general meeting"))
        self.assertIn("Tue Oct 6", rows[0].detail)
        self.assertIn("5:00-6:00 PM", rows[0].detail)
        self.assertIn("Student union, room 2", rows[0].detail)
        self.assertTrue(rows[0].detail.endswith("Personal calendar"))
        self.assertEqual((rows[1].kind, rows[1].title), ("Block", "Read chapter 4"))
        self.assertIn("7:00-9:00 PM", rows[1].detail)
        self.assertTrue(rows[1].detail.endswith("Personal calendar"))

    def test_the_digest_changes_with_any_row_and_its_order(self) -> None:
        cal_a, cal_b = line(CAL_A), line(CAL_B)
        first = batch.digest(batch.rows([cal_a, cal_b], TODAY))
        self.assertEqual(first, batch.digest(batch.rows([cal_a, cal_b], TODAY)))
        self.assertNotEqual(first, batch.digest(batch.rows([cal_b, cal_a], TODAY)))
        self.assertNotEqual(first, batch.digest(batch.rows([cal_a], TODAY)))
        moved = line(CAL_B.replace("19:00-21:00", "19:30-21:00"))
        self.assertNotEqual(first, batch.digest(batch.rows([cal_a, moved], TODAY)))


class StopTests(unittest.TestCase):
    def test_added_or_already_there_goes_on_everything_else_stops(self) -> None:
        self.assertEqual(batch.stop_reason(STATUS_CREATED), "")
        self.assertEqual(batch.stop_reason(STATUS_EXISTS), "")
        self.assertEqual(batch.stop_reason(STATUS_FAILED), batch.REASON_FAILED)
        self.assertEqual(batch.stop_reason(STATUS_UNKNOWN), batch.REASON_UNKNOWN)

    def test_the_summary_and_the_log_hold_counts_and_a_reason_word_only(self) -> None:
        self.assertEqual(batch.summary(5, 5, ""), "Added 5 of 5")
        self.assertEqual(batch.summary(3, 5, "failed"), "Added 3 of 5; stopped: failed")
        with self.assertLogs("briefing_reader.batch", logging.INFO) as logs:
            batch.log_end(3, 5, "failed")
        self.assertEqual(logs.records[0].getMessage(), "Add all ended: 3 of 5 added; failed")


if __name__ == "__main__":
    unittest.main()
