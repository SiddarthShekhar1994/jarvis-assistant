"""Tests for the standing agents' and rules' cards in NEEDS YOUR OK (the source-trust fix).

A third source, SOURCE_STANDING, sits between ASK and BRIEFING with its own group header (AGENTS) and labels
("RULE · CALENDAR"). Only a SOURCE_BRIEFING card is the briefing's own: a standing card is never read out in the
spoken "Needs your OK", never handed to Ask as the briefing's, and never counted as a page card; a card the
briefing already has is information only; at most _STANDING_CARDS_MAX stay; a card of another source is refused.
Qt runs offscreen; nothing reaches Google, Notion or claude.exe.
"""

from __future__ import annotations

import dataclasses
import unittest

from PySide6.QtWidgets import QApplication

from briefing_reader import ui
from briefing_reader.actions import (MODE_STANDING_AGENT, MODE_STANDING_RULE, SOURCE_ASK, SOURCE_BRIEFING,
                                     SOURCE_STANDING, TODO, parse_action_line)
from briefing_reader.ask.validate import ALREADY_LISTED
from tests.ui_fakes import B_CALENDAR, CAL_LINE, MOVE_LINE, AppHarness, fonts, settle

_app: QApplication | None = None

TODO_LINE = "Todo: acct=personal | title=Pay the Acme invoice | due=2026-10-08 17:00 | block= | link="


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_standing_cards", "-platform", "offscreen"])
    fonts()


def standing_card(line: str, mode: str = MODE_STANDING_RULE):
    return dataclasses.replace(parse_action_line(line), source=SOURCE_STANDING, mode=mode)


def ask_card(line: str):
    return dataclasses.replace(parse_action_line(line), source=SOURCE_ASK)


def labels(c: ui.AppController) -> list[str]:
    return [card.kind_label.full_text() for card in c.window.reading.approvals.cards()]


class StandingCardTests(unittest.TestCase):

    def setUp(self) -> None:
        self.app = AppHarness(self)
        self.app.reading()
        self.c = self.app.c

    def test_standing_cards_get_their_own_group_between_ask_and_briefing(self) -> None:
        self.c._take_ask_cards([ask_card(CAL_LINE)])
        self.c._take_standing_cards([standing_card(TODO_LINE), standing_card(MOVE_LINE, MODE_STANDING_AGENT)])
        settle()
        approvals = self.c.window.reading.approvals
        self.assertEqual(approvals.group_headers(), [("ASK", 1), ("AGENTS", 2), ("BRIEFING", 2)])
        self.assertEqual([a.source for a in self.c._actions],
                         [SOURCE_ASK, SOURCE_STANDING, SOURCE_STANDING, SOURCE_BRIEFING, SOURCE_BRIEFING])
        self.assertEqual(labels(self.c)[:3], ["ASK · CALENDAR", "RULE · TODO", "AGENT · MOVE · WORK"])

    def test_standing_cards_alone_still_get_the_headers(self) -> None:
        self.c._take_standing_cards([standing_card(TODO_LINE)])
        settle()
        self.assertEqual(self.c.window.reading.approvals.group_headers(), [("AGENTS", 1), ("BRIEFING", 2)])

    def test_the_spoken_needs_your_ok_leaves_out_standing_cards(self) -> None:
        before = self.c._build_script(include_note=False)
        self.c._take_standing_cards([standing_card(TODO_LINE), standing_card(CAL_LINE)])
        after = self.c._build_script(include_note=False)
        self.assertEqual(before.sections, after.sections)
        self.assertEqual({a.source for a in self.c._pending_page_actions()}, {SOURCE_BRIEFING})
        self.assertEqual(len(self.c._pending_actions()), 4)            # they still need your OK

    def test_ask_never_sees_a_standing_card_as_the_briefings(self) -> None:
        card = standing_card(TODO_LINE)
        self.c._take_standing_cards([card])
        _context, page_ids = self.c._ask_context()
        self.assertNotIn(card.id, page_ids)
        self.assertEqual(set(page_ids), {a.id for a in self.c._source_lists[SOURCE_BRIEFING]})
        self.assertNotIn(card.id, [a.id for a in self.c._pending_page_actions()])

    def test_a_standing_card_the_briefing_also_has_is_information_only(self) -> None:
        duplicate = standing_card(B_CALENDAR)
        self.c._take_standing_cards([duplicate])
        mine = [a for a in self.c._actions if a.source == SOURCE_STANDING]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].error, ALREADY_LISTED)
        self.assertEqual(sum(1 for a in self.c._actions if a.id == duplicate.id), 1)   # the briefing's card

    def test_a_standing_card_an_ask_card_already_has_is_left_out(self) -> None:
        self.c._take_ask_cards([ask_card(CAL_LINE)])
        self.c._take_standing_cards([standing_card(CAL_LINE)])
        same = parse_action_line(CAL_LINE).id
        self.assertEqual([a.source for a in self.c._actions if a.id == same], [SOURCE_ASK])

    def test_a_card_of_another_source_or_mode_is_refused(self) -> None:
        with self.assertLogs("briefing_reader.ui", "WARNING") as logs:
            self.c._take_standing_cards([ask_card(CAL_LINE), standing_card(MOVE_LINE, mode=""),
                                         standing_card(TODO_LINE)])
        self.assertEqual([a.source for a in self.c._actions if a.source != SOURCE_BRIEFING], [SOURCE_STANDING])
        self.assertTrue(any("2 card(s) of another source refused" in line for line in logs.output))

    def test_a_new_list_replaces_the_old_one(self) -> None:
        self.c._take_standing_cards([standing_card(TODO_LINE)])
        self.c._take_standing_cards([standing_card(CAL_LINE)])
        mine = [a.title for a in self.c._actions if a.source == SOURCE_STANDING]
        self.assertEqual(mine, ["Study block"])

    def test_at_most_forty_standing_cards_stay(self) -> None:
        cards = [standing_card(f"Calendar: Block {n} | 2026-10-06 19:00-20:00") for n in range(45)]
        self.c._take_standing_cards(cards)
        mine = [a for a in self.c._actions if a.source == SOURCE_STANDING]
        self.assertEqual(len(mine), ui._STANDING_CARDS_MAX)
        self.assertEqual(mine[0].title, "Block 0")                   # the list's order is kept; the last go

    def test_a_standing_todo_reaches_the_day_plan_as_data_from_standing(self) -> None:
        self.c._take_standing_cards([standing_card(TODO_LINE)])
        pending = [a for a in self.c._pending_actions() if a.kind == TODO and a.source == SOURCE_STANDING]
        self.assertEqual(len(pending), 1)
        from briefing_reader.ask.modes import todo_facts

        facts = todo_facts(pending)
        self.assertEqual([fact.source for fact in facts], [SOURCE_STANDING])


if __name__ == "__main__":
    unittest.main()
