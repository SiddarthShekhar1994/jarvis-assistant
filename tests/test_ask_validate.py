"""Tests for briefing_reader.ask.validate: the planner's lines become cards only with the ids and
addresses Jarvis supplied. Invented people and ids only."""

from __future__ import annotations

import unittest
from datetime import timedelta

from briefing_reader.actions import EMAIL, MOVE, REPLY, RSVP, SOURCE_ASK, parse_action_line
from briefing_reader.ask import validate
from briefing_reader.ask.context import AccountInfo, AskContext, BriefingContext, build_prompt
from briefing_reader.ask.mail import thread_from_api
from briefing_reader.ask.validate import to_cards
from tests.ask_fakes import NOW, brief, gmail_message, gmail_thread

REPLY_LINE = ("Reply: acct=work | thread=18c0ffee00000001 | msgid=<CAB1@mail.example.edu> | gmid= | "
              "to=Ana Lima <ana@example.edu> | cc= | subject=Re: Budget review | replied=no | due= | link= | body=Thanks")
TOMORROW_2PM = NOW.replace(hour=14, minute=0) + timedelta(days=1)


def index_for(command: str = "move my sync to Friday", mail: bool = False):  # type: ignore[no-untyped-def]
    threads = ()
    if mail:
        threads = (thread_from_api(gmail_thread("thr0000777", [gmail_message(
            "msg0000777", sender="Cy Rivera <cy@partner.example>", to="you@example.edu", subject="Contract",
            body="Can you confirm?", msgid="<CY7@partner.example>", at_ms=1791200000000)]), "work"),)
    ctx = AskContext(
        now=NOW, time_zone="America/Los_Angeles", command=command,
        accounts=(AccountInfo("personal", "you@example.com", "yes", True, "yes"),
                  AccountInfo("work", "you@example.edu", "yes", True, "yes")),
        events={"work": (brief("evt0001aa", "Project sync", TOMORROW_2PM, guests=(("Ana Lima", "ana@example.edu"),)),
                         brief("evt0002bb", "Dept meeting", TOMORROW_2PM + timedelta(hours=3), organizer_self=False,
                               organizer=("Dean", "dean@example.edu")))},
        briefing=BriefingContext(pending=(parse_action_line(REPLY_LINE),)), mail=threads)
    return build_prompt(ctx)[1]


def move(event: str = "evt0001aa", when: str = "2026-10-09 14:00-15:00", acct: str = "work") -> str:
    return (f"Move: acct={acct} | event={event} | cal=primary | when={when} | notify=all | title=Project sync | "
            f"at=2026-10-08 14:00-15:00 | link= | body=")


def email(to: str, acct: str = "work") -> str:
    return f"Email: acct={acct} | to={to} | cc= | subject=Sync moved | due= | link= | body=Hi,\\nMoved to Friday."


class ToCardsTests(unittest.TestCase):
    def cards(self, *lines: str, index=None, page_ids=(), max_cards: int = 8):  # type: ignore[no-untyped-def]
        return to_cards(list(lines), index or index_for(), now=NOW, page_ids=page_ids, max_cards=max_cards)

    def one(self, line: str, index=None):  # type: ignore[no-untyped-def]
        cards = self.cards(line, index=index)
        self.assertEqual(len(cards.cards), 1)
        card = cards.cards[0]
        self.assertEqual(card.source, SOURCE_ASK)
        return card

    def test_a_valid_move_and_email(self) -> None:
        result = self.cards(move(), email("Ana Lima <ana@example.edu>"))
        moved, mailed = result.cards
        self.assertEqual((moved.kind, moved.error, moved.actionable), (MOVE, "", True))
        self.assertEqual(moved.id, parse_action_line(move()).id)   # the same id the briefing would give it
        self.assertEqual((mailed.kind, mailed.error), (EMAIL, ""))
        self.assertEqual(mailed.unverified, frozenset({"ana@example.edu"}))   # a guest, not typed by the owner
        self.assertEqual(result.kinds(), {"move": 1, "email": 1})
        self.assertEqual((result.dropped, result.refused), (0, 0))

    def test_an_address_the_owner_typed_is_verified(self) -> None:
        index = index_for("tell cy@example.org it moved")
        card = self.one(email("cy@example.org"), index=index)
        self.assertEqual((card.error, card.unverified), ("", frozenset()))

    def test_event_provenance(self) -> None:
        self.assertEqual(self.one(move(event="evtINVENTED")).error, validate.UNKNOWN_EVENT)
        self.assertEqual(self.one(move(acct="personal")).error, validate.UNKNOWN_EVENT)   # another account's id
        self.assertEqual(self.one(move(event="evt0002bb")).error,
                         "You don't organize this event, so Jarvis can't move it - ask for an email to the organizer")
        cancel = "Cancel: acct=work | event=evt0002bb | cal=primary | notify=all | title=Dept | at= | link= | body="
        self.assertIn("can't cancel it", self.one(cancel).error)
        rsvp = "RSVP: acct=work | event={} | cal=primary | answer=yes | notify=all | title=x | at= | due= | link= | body="
        self.assertEqual(self.one(rsvp.format("evt0001aa")).error, validate.ORGANIZER_RSVP)
        accepted = self.one(rsvp.format("evt0002bb"))
        self.assertEqual((accepted.kind, accepted.error), (RSVP, ""))
        wrong_cal = move().replace("cal=primary", "cal=team@group.calendar.google.com")
        self.assertEqual(self.one(wrong_cal).error, validate.UNKNOWN_EVENT)

    def test_accounts(self) -> None:
        self.assertEqual(self.one(move(acct="school")).error, 'Jarvis has no account called "school"')
        todo = "Todo: title=Read paper | due=2026-10-09 23:59 | block= | acct=lab | link="
        self.assertEqual(self.one(todo).error, 'Jarvis has no account called "lab"')

    def test_recipient_provenance(self) -> None:
        card = self.one(email("Eve <eve@attacker.example>"))
        self.assertEqual(card.error, "Jarvis didn't find eve@attacker.example in your calendar, briefing, mail or your words")
        self.assertFalse(card.decidable)
        mixed = self.one(email("ana@example.edu, eve@attacker.example"))
        self.assertTrue(mixed.error)

    def test_reply_provenance(self) -> None:
        good = self.one(REPLY_LINE)
        self.assertEqual((good.kind, good.error), (REPLY, ""))
        self.assertEqual(good.unverified, frozenset({"ana@example.edu"}))
        other_thread = REPLY_LINE.replace("18c0ffee00000001", "18c0ffee00000999")
        self.assertEqual(self.one(other_thread).error, validate.UNKNOWN_THREAD)
        other_message = REPLY_LINE.replace("CAB1@", "CAB2@")
        self.assertEqual(self.one(other_message).error, validate.UNKNOWN_MESSAGE)
        wrong_account = REPLY_LINE.replace("acct=work", "acct=personal")
        self.assertEqual(self.one(wrong_account).error, validate.UNKNOWN_THREAD)

    def test_reply_to_a_thread_jarvis_read(self) -> None:
        index = index_for(mail=True)
        line = ("Reply: acct=work | thread=thr0000777 | msgid=CY7@partner.example | gmid=msg0000777 | "
                "to=cy@partner.example | cc= | subject=Re: Contract | replied=no | due= | link= | body=Confirmed.")
        card = self.one(line, index=index)
        self.assertEqual(card.error, "")
        self.assertEqual(card.unverified, frozenset({"cy@partner.example"}))   # from mail: NEW until ticked
        self.assertTrue(self.one(line, index=index_for()).error)                # not without that mail

    def test_move_times(self) -> None:
        self.assertEqual(self.one(move(when="2026-10-06 14:00-15:00")).error, validate.PAST_MOVE)
        far = self.one(move(when="2027-01-20 14:00-15:00"))
        self.assertEqual(far.error, "")
        self.assertIn(validate.FAR_MOVE, far.warnings)

    def test_kinds_ask_may_not_propose(self) -> None:
        slack = self.one("Slack: channel=C0123ABCD | ts=1696000000.000100 | who=Ana | body=Sure")
        self.assertEqual(slack.error, "Ask can't propose Slack")
        share = self.one("Share: acct=work | file=1AbCdEfGhIjKlMnOp | who=a@example.edu")
        self.assertEqual(share.error, "Ask can't propose Share")
        prose = self.one("I have lined up two things.")
        self.assertIn("need a kind", prose.error)
        bad_link = self.one("Open: title=Docs | link=https://evil.example/login")
        self.assertTrue(bad_link.error)
        good_link = self.one("Open: title=Docs | link=https://docs.google.com/document/d/abc")
        self.assertEqual(good_link.error, "")
        calendar = self.one("Calendar: Study block | 2026-10-09 19:00-21:00 | | Library |")
        self.assertEqual((calendar.kind, calendar.error), ("calendar", ""))

    def test_caps_duplicates_and_page_ids(self) -> None:
        lines = [f"Todo: title=Task {n} | due=2026-10-09 | block= | acct= | link=" for n in range(10)]
        result = self.cards(*lines)
        self.assertEqual((len(result.cards), result.dropped), (8, 2))
        result = self.cards(*lines[:3], max_cards=2)
        self.assertEqual((len(result.cards), result.dropped), (2, 1))
        repeated = self.cards(move(), move())
        self.assertEqual((len(repeated.cards), repeated.dropped), (1, 1))
        listed = self.cards(move(), page_ids={parse_action_line(move()).id})
        self.assertEqual(listed.cards[0].error, validate.ALREADY_LISTED)
        self.assertNotEqual(listed.cards[0].id, parse_action_line(move()).id)   # never shares the briefing card's id
        self.assertEqual(listed.refused, 1)
        self.assertEqual(listed.kinds(), {"refused": 1})

    def test_report_follows_the_lines_in_order(self) -> None:
        slack = "Slack: channel=C0123ABCD | ts=1696000000.000100 | who=Ana | body=Sure"
        todos = [f"Todo: title=Task {n} | due=2026-10-09 | block= | acct= | link=" for n in range(2)]
        lines = [move(), slack, move(), email("x@evil.example"), *todos]
        result = self.cards(*lines, max_cards=4)
        outcomes = [(item.line, item.outcome) for item in result.report]
        self.assertEqual(outcomes, [(move(), validate.LINE_CARD), (slack, validate.LINE_REFUSED),
                                    (move(), validate.LINE_REPEAT), (email("x@evil.example"), validate.LINE_REFUSED),
                                    (todos[0], validate.LINE_CARD), (todos[1], validate.LINE_OVER_CAP)])
        ids = [card.id for card in result.cards]
        self.assertEqual([item.card_id for item in result.report], [ids[0], ids[1], ids[0], ids[2], ids[3], ""])
        self.assertEqual([item.reason for item in result.report],
                         ["", "Ask can't propose Slack", "", validate.unknown_address("x@evil.example"), "", ""])
        # cards, dropped and refused are as before the report existed.
        self.assertEqual((len(result.cards), result.dropped, result.refused), (4, 2, 2))
        listed = self.cards(move(), page_ids={parse_action_line(move()).id})
        self.assertEqual((listed.report[0].outcome, listed.report[0].reason),
                         (validate.LINE_REFUSED, validate.ALREADY_LISTED))
        self.assertNotIn("report=", repr(result))   # the report is never in a repr (logs)
        self.assertNotIn("Slack", repr(result.report))
        self.assertNotIn("evil", repr(result.report))


if __name__ == "__main__":
    unittest.main()
