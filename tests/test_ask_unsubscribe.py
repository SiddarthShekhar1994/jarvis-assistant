"""Unsubscribe through Ask (mail extras, part 4): a message keeps its own List-Unsubscribe reading (memory only), the
<mail> block marks it "unsubscribe=available" (never the link or the address), the index knows it, and an
"Unsubscribe: acct | thread | gmid" line becomes the card Jarvis builds from that header: one-click -> an Unsubscribe
card, the list's address -> an Email with a NEW RECIPIENT, its page -> an Open card, an unverified sender or a message
it was not shown -> information only. A line that writes the link, a briefing line or another mode's never works.
Every name, address, host and id here is invented; nothing reaches the network."""

from __future__ import annotations

import unittest
from datetime import datetime

from briefing_reader import actions
from briefing_reader.actions import EMAIL, OPEN, UNSUBSCRIBE, extract_actions
from briefing_reader.ask import cli, validate
from briefing_reader.ask.context import ContextIndex, index_thread, thread_text
from briefing_reader.ask.mail import thread_from_api
from briefing_reader.models import PARAGRAPH, HEADING, FlatLine
from briefing_reader.unsubscribe import ONE_CLICK_VALUE, REFUSAL_WORDS, NOT_VERIFIED
from tests.ask_fakes import gmail_message, gmail_thread, plan_stream
from tests.test_ask_planner import PlannerTestCase

URL = "https://lists.example.com/u/abc123?token=xyz"
AUTH_OK = "mx.google.com; dkim=pass header.i=@example.com; spf=pass; dmarc=pass (p=REJECT) header.from=example.com"
NOW = datetime(2026, 10, 4, 13, 52)
LINE = "Unsubscribe: acct=work | thread=thr0000888 | gmid=msg0000888"


def list_thread(*, unsub=f"<mailto:leave@lists.example.com?subject=stop>, <{URL}>", post=ONE_CLICK_VALUE,
                auth=AUTH_OK) -> dict:  # type: ignore[no-untyped-def]
    message = gmail_message("msg0000888", sender="Acme Deals <deals@example.com>", to="you@example.edu",
                            subject="This week's deals", body="Big savings on everything.",
                            msgid="<deal8@mail.example.com>", at_ms=1791200000000)
    headers = message["payload"]["headers"]
    headers.insert(0, {"name": "Authentication-Results", "value": auth})
    headers.append({"name": "List-Id", "value": "Acme Deals <deals.acme.example.com>"})
    if unsub is not None:
        headers.append({"name": "List-Unsubscribe", "value": unsub})
    if post is not None:
        headers.append({"name": "List-Unsubscribe-Post", "value": post})
    return gmail_thread("thr0000888", [message])


def index_of(data: dict) -> tuple[ContextIndex, object]:  # type: ignore[type-arg]
    thread = thread_from_api(data, "work")
    index = ContextIndex(aliases=frozenset({"personal", "work"}))
    index_thread(index, thread)
    return index, thread


class MailTests(unittest.TestCase):
    def test_the_message_keeps_its_reading_in_memory_only(self) -> None:
        _, thread = index_of(list_thread())
        message = thread.messages[0]
        self.assertIsNotNone(message.unsubscribe)
        self.assertEqual(message.unsubscribe.https, URL)
        self.assertNotIn("lists.example.com", repr(message))
        plain = thread_from_api(list_thread(unsub=None, post=None), "work").messages[0]
        self.assertIsNone(plain.unsubscribe)

    def test_the_mail_block_marks_it_and_never_shows_the_link(self) -> None:
        _, thread = index_of(list_thread())
        text = thread_text(thread)
        self.assertIn("| unsubscribe=available", text)
        for secret in ("lists.example.com", "abc123", "leave@"):
            self.assertNotIn(secret, text)
        plain = thread_from_api(list_thread(unsub=None, post=None), "work")
        self.assertNotIn("unsubscribe", thread_text(plain))

    def test_the_index_knows_the_shown_message(self) -> None:
        index, _ = index_of(list_thread())
        self.assertEqual(set(index.lists), {("work", "msg0000888")})
        self.assertEqual(index.lists[("work", "msg0000888")][0], "thr0000888")


class CardTests(unittest.TestCase):
    def card(self, line: str = LINE, **kw):  # type: ignore[no-untyped-def]
        index, _ = index_of(list_thread(**kw))
        return validate.check_line(line, index, now=NOW, kinds=validate.PLAIN_ASK_KINDS)

    def test_one_click_becomes_an_unsubscribe_card_from_the_header(self) -> None:
        card = self.card()
        self.assertEqual((card.kind, card.error, card.source), (UNSUBSCRIBE, "", actions.SOURCE_ASK))
        self.assertEqual((card.field("url"), card.field("method"), card.field("sender"), card.field("list")),
                         (URL, "one_click", "deals@example.com", "deals.acme.example.com"))
        self.assertEqual(card.title, "Unsubscribe from Acme Deals")
        self.assertTrue(card.held)
        self.assertIn(UNSUBSCRIBE, validate.PLAIN_ASK_KINDS)
        self.assertNotIn(UNSUBSCRIBE, validate.ASK_KINDS)      # fix round 1: never the default (an agent task's)

    def test_a_list_address_becomes_an_email_with_a_new_recipient(self) -> None:
        card = self.card(post=None)
        self.assertEqual((card.kind, card.error, card.account), (EMAIL, "", "work"))
        self.assertEqual(card.recipients(), ("leave@lists.example.com",))
        self.assertEqual(card.unverified, frozenset({"leave@lists.example.com"}))
        self.assertIn(validate.UNSUBSCRIBE_MAILTO_WARNING, card.warnings)
        self.assertEqual((card.title, card.body), ("stop", "Unsubscribe"))
        self.assertTrue(card.sends_mail)

    def test_a_page_only_becomes_an_open_card(self) -> None:
        card = self.card(unsub=f"<{URL}>", post=None)
        self.assertEqual((card.kind, card.error, card.link, card.web), (OPEN, "", URL, True))
        self.assertEqual(card.title, "Unsubscribe page of Acme Deals")

    def test_an_unverified_sender_is_information_only(self) -> None:
        card = self.card(auth="mx.google.com; dmarc=fail")
        self.assertEqual(card.error, REFUSAL_WORDS[NOT_VERIFIED])
        self.assertFalse(card.decidable)

    def test_a_line_may_name_only_a_shown_message_and_write_nothing_more(self) -> None:
        self.assertEqual(self.card(LINE.replace("msg0000888", "msg0000999")).error, validate.UNSUBSCRIBE_UNKNOWN)
        self.assertEqual(self.card(LINE.replace("thr0000888", "thr0000999")).error, validate.UNSUBSCRIBE_UNKNOWN)
        self.assertIn("no account", self.card(LINE.replace("acct=work", "acct=school")).error)
        for extra in (f" | url={URL}", " | method=one_click", " | sender=deals@example.com",
                      " | list=deals.acme.example.com"):
            with self.subTest(extra=extra):
                self.assertEqual(self.card(LINE + extra).error, validate.UNSUBSCRIBE_WRITTEN)

    def test_other_sources_and_modes_never_get_one(self) -> None:
        index, _ = index_of(list_thread())
        mode = validate.check_line(LINE, index, now=NOW, kinds=frozenset({actions.REPLY}),
                                   refusal="A meeting brief only drafts replies")
        self.assertEqual(mode.error, actions.ONLY_ASK_UNSUBSCRIBE)
        lines = [FlatLine(HEADING, "Proposed actions", level=2), FlatLine(PARAGRAPH, LINE + f" | url={URL}")]
        (briefed,), _ = extract_actions(lines)
        self.assertEqual(briefed.error, actions.ONLY_ASK_UNSUBSCRIBE)
        self.assertFalse(briefed.decidable)


class AgentTests(unittest.TestCase):
    """Fix round 1 (medium 3): an agent task's cards (validate.to_cards with its default kinds, as agent/owner.py's
    propose_action calls it) never get a working Unsubscribe card, even for a message whose header Jarvis read."""

    def test_the_default_kinds_make_it_information_only(self) -> None:
        index, _ = index_of(list_thread())
        cards = validate.to_cards([LINE], index, now=NOW)
        card, = cards.cards
        self.assertEqual((card.kind, card.error), (UNSUBSCRIBE, actions.ONLY_ASK_UNSUBSCRIBE))
        self.assertFalse(card.decidable)
        self.assertEqual(cards.refused, 1)

    def test_the_agent_passes_no_kinds(self) -> None:
        import inspect

        from briefing_reader.agent import owner

        source = inspect.getsource(owner)
        call = source[source.index("cards = to_cards(args[\"lines\"]"):]
        self.assertNotIn("PLAIN_ASK_KINDS", call[:300])


class PromptTests(unittest.TestCase):
    def test_both_planner_prompts_name_the_line_and_its_rule(self) -> None:
        for path in (cli.PROMPT_FILE, cli.ASSETS / "planner_prompt_agent.md"):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="ascii")
                self.assertIn("  Unsubscribe: acct=<alias> | thread=<thread id> | gmid=<Gmail id>\n", text)
                self.assertIn("unsubscribe=available", text)


class PlannerUnsubscribeTests(PlannerTestCase):
    SEARCH = {"say": "Let me find the Acme deals mail.", "lines": [],
              "gmail_search": {"account": "work", "query": "acme deals", "why": "the list"}}

    def test_a_two_pass_ask_proposes_the_card_from_the_header(self) -> None:
        self.reader.threads["thr0000888"] = list_thread()
        self.reader.found = ["thr0000888"]
        ask = self.make(plan_stream(self.SEARCH), plan_stream({"say": "I've lined up the unsubscribe.",
                                                              "lines": [LINE]}))
        with self.assertLogs("briefing_reader", "DEBUG") as logs:
            outcome = ask.plan("unsubscribe me from the Acme deals emails")
        self.assertTrue(outcome.ok, outcome)
        card, = outcome.cards
        self.assertEqual((card.kind, card.error, card.field("url")), (UNSUBSCRIBE, "", URL))
        stdin = self.stdin(1)
        self.assertIn("unsubscribe=available", stdin)
        self.assertNotIn("lists.example.com", stdin)
        text = "\n".join(logs.output)
        for secret in ("lists.example.com", "abc123", "deals@example.com", "deals.acme"):
            self.assertNotIn(secret, text)
        self.assertNotIn("lists.example.com", outcome.summary())


if __name__ == "__main__":
    unittest.main()
