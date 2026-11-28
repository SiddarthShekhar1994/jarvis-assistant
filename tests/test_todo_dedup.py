"""One to-do shows once (the walkthrough of 2026-11-28, fix 4).

The TO-DOS frame showed "Bayprofs line up parent and appointed guardian" (the page) and "DUE: Bayprofs: line up parent
+ appointed guardian for NRA..." (a calendar all-day event) as two rows: the deadline merge only took two titles as the
same when one sat inside the other. Now a lead word ("DUE:") never counts, and two titles of the same day are the same
when most of the shorter one's words are in the other; the to-do frame also drops the lead word from what it shows.
Every name is invented.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime

from briefing_reader import actions, agenda
from briefing_reader.board import frames

NOW = datetime(2026, 11, 28, 10, 50)
TODAY = date(2026, 11, 28)


def deadline(title: str, due=TODAY, source: str = "page") -> agenda.Deadline:
    return agenda.Deadline(title=title, due=due, source=source)


class SameTitleTests(unittest.TestCase):
    def test_the_walkthrough_pair_is_one(self) -> None:
        self.assertTrue(agenda.same_title("Bayprofs line up parent and appointed guardian",
                                          "DUE: Bayprofs: line up parent + appointed guardian for NRA lesson"))

    def test_a_lead_word_and_case_never_count(self) -> None:
        self.assertTrue(agenda.same_title("DUE: Engramme sign-in", "engramme sign in"))
        self.assertTrue(agenda.same_title("To-do - Read chapter 2", "Read chapter 2"))

    def test_different_to_dos_stay_apart(self) -> None:
        self.assertFalse(agenda.same_title("Submit LING 21 draft", "Submit LING 21 final"))
        self.assertFalse(agenda.same_title("Call the bank", "Call the dentist"))
        self.assertFalse(agenda.same_title("Pay rent", "Pay phone bill"))

    def test_short_titles_need_one_inside_the_other(self) -> None:
        self.assertTrue(agenda.same_title("Pay rent", "Pay rent online"))
        self.assertFalse(agenda.same_title("Pay rent now", "Rent pay later"))

    def test_strip_lead_keeps_a_title_that_is_only_the_word(self) -> None:
        self.assertEqual(agenda.strip_lead("DUE: Engramme: sign in"), "Engramme: sign in")
        self.assertEqual(agenda.strip_lead("Due:"), "Due:")
        self.assertEqual(agenda.strip_lead("Due diligence memo"), "Due diligence memo")


class MergeTests(unittest.TestCase):
    def test_page_and_calendar_twins_merge_page_first(self) -> None:
        page = [deadline("Bayprofs line up parent and appointed guardian")]
        calendar = [deadline("DUE: Bayprofs: line up parent + appointed guardian for NRA lesson", source="calendar")]
        merged = agenda.merge_deadlines(page, calendar, NOW)
        self.assertEqual([item.title for item in merged], ["Bayprofs line up parent and appointed guardian"])

    def test_twins_on_different_days_both_stay(self) -> None:
        page = [deadline("Bayprofs line up parent and appointed guardian")]
        calendar = [deadline("DUE: Bayprofs: line up parent + appointed guardian", due=date(2026, 11, 30),
                             source="calendar")]
        self.assertEqual(len(agenda.merge_deadlines(page, calendar, NOW)), 2)


class FrameTests(unittest.TestCase):
    def rows(self, todos, deadlines) -> list[dict]:
        built = frames.from_todos(todos, deadlines, frame_id="t1", now=NOW)
        return built.frame["body"]["rows"]

    def test_the_frame_shows_a_twin_once_without_the_lead_word(self) -> None:
        rows = self.rows([], [deadline("DUE: Engramme: sign in to the account"),
                              deadline("Engramme sign in to the account", source="calendar")])
        self.assertEqual([row["text"] for row in rows], ["Engramme: sign in to the account"])

    def test_a_card_and_its_deadline_show_once(self) -> None:
        card = actions.parse_action_line("Todo: title=Bayprofs: line up parent and appointed guardian | "
                                         "due=2026-11-28 | block= | acct= | link=")
        rows = self.rows([card], [deadline("DUE: Bayprofs line up parent + appointed guardian for NRA")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "waits")

    def test_different_to_dos_of_one_day_both_show(self) -> None:
        rows = self.rows([], [deadline("Submit LING 21 draft"), deadline("Submit LING 21 final")])
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
