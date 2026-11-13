"""Tests for setting standing jobs up from the chat (part 7 of the build).

"agent:" / "rule:" in the bar and an Ask's planner hand-off start ONE setup run on the owner's own words; what it made
is a draft on the Agents screen's setup card (Dry run, Turn on, Edit, Discard). A rule goes on only after its dry run
(Gmail's own count first: a filter that would read nearly everything stops before any Claude run). Turn on needs the
one-time consent card's yes and records each account's Google account. "run <name>" is an agent's Run now. Qt runs
offscreen; the setup and rule runs are fakes, nothing reaches Google or claude.exe, no process is started.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from typing import Any

from PySide6.QtWidgets import QApplication

from briefing_reader import standing_ui, ui
from briefing_reader.ask import cli
from briefing_reader.ask.routes import ROUTE_STANDING, RouteFlags, route
from briefing_reader.standing.engine import KIND_RULE, KIND_SETUP, StandingResult
from briefing_reader.standing.model import STATUS_DRAFT, STATUS_LIVE, STATUS_TRIED
from briefing_reader.standing.rules import Result, RuleAnswer
from briefing_reader.standing.store import AppState, Definitions, RunRequests, Usage
from tests.ask_fakes import plan_stream
from tests.standing_fakes import AGENT_NAME, STANDING_ON, agent_data, consent, files_of, save_jobs
from tests.test_standing_google import FakeReader, message
from tests.ui_fakes import NOW, AppHarness, fonts, settle

_app: QApplication | None = None

WORDS = "when billing@example.org sends an invoice, make me a to-do to pay it"
RULE_ANSWER = {"kind": "rule", "say": "A rule for Example Billing's invoices.", "name": "Invoice watch",
               "rule": {"account": "work", "query": "from:billing@example.org subject:invoice",
                        "question": "Is this an invoice to pay?",
                        "fields": [{"name": "amount", "type": "money", "hint": "the amount due"}],
                        "action": {"kind": "todo", "title": "Pay {amount}"}}}
AGENT_WORDS = "every morning at 7:30 tell me what pat.example@example.org sent"
AGENT_ANSWER = {"kind": "agent", "say": "A morning agent.", "name": "Morning mail",
                "agent": {"trigger": {"kind": "time", "times": ["07:30"], "weekdays": []},
                          "mail": [{"account": "work", "query": "from:pat.example@example.org"}],
                          "proposes": ["todo"], "report": "every_run"}}


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_standing_setup", "-platform", "offscreen"])
    fonts()


class FakeEngine:
    """A StandingEngine that answers from a list: setup answers (dicts) and rule answers (RuleAnswer)."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.runs: list[tuple[str, str, str]] = []           # (kind, model, prompt)

    def run(self, prompt: str, *, kind: str, model: str, timeout_s: float, read: Any, cancel: Any = None):
        self.runs.append((kind, model, prompt))
        return StandingResult(answer=self.answers.pop(0))


class Account:
    def __init__(self, sub: str) -> None:
        self.sub = sub

    def binding(self) -> Any:
        return type("Binding", (), {"sub": self.sub, "email": "me@example.edu"})()


class _Base(unittest.TestCase):
    consented = True

    def setUp(self) -> None:
        self.app = AppHarness(self, config_extra=STANDING_ON, prepare=self.prepare)
        self.c = self.app.c
        self.s = self.c.standing
        self.c._locked = lambda: False
        self.s.busy_probe = lambda: False
        self.s.thread_runner = lambda work: work()
        self.started: list[bool] = []
        self.s.start_runner = lambda: self.started.append(True) or True
        self.engine = FakeEngine()
        self.s.engine_factory = lambda: self.engine
        self.app.reading(autoplay=False)
        self.s.reload()

    def prepare(self, config) -> None:
        if self.consented:
            consent(config)

    def card(self) -> Any:
        return self.c.window.reading.agents_stage.notice_card(standing_ui.SETUP_CARD)

    def type(self, text: str) -> None:
        bar = self.c.window.reading.command_bar
        bar.set_text(text)
        bar.submit()
        settle()


class RouteTests(unittest.TestCase):

    def test_agent_and_rule_take_the_sentence_and_run_takes_a_live_agents_name(self) -> None:
        flags = RouteFlags(standing=True, standing_names=("Morning mail",))
        self.assertEqual((route("rule: " + WORDS, flags).kind, route("rule: " + WORDS, flags).part,
                          route("rule: " + WORDS, flags).payload), (ROUTE_STANDING, "rule", WORDS))
        self.assertEqual(route("Agent:" + AGENT_WORDS, flags).part, "agent")
        found = route("run morning mail now", flags)
        self.assertEqual((found.kind, found.part, found.payload), (ROUTE_STANDING, "run", "Morning mail"))
        self.assertEqual(route("run the numbers for me", flags).kind, "none")       # a normal Ask
        self.assertEqual(route("rule: " + WORDS, RouteFlags()).kind, "none")        # [standing] off

    def test_the_hand_off_is_offered_only_while_standing_is_on(self) -> None:
        import json

        self.assertIn("standing_job", json.loads(cli.schema_text(allow_search=True, allow_standing=True))["properties"])
        self.assertNotIn("standing_job", json.loads(cli.schema_text(allow_search=True))["properties"])


class SetupTests(_Base):

    def test_rule_in_the_bar_makes_a_draft_on_the_setup_card(self) -> None:
        self.engine.answers.append(RULE_ANSWER)
        self.type("rule: " + WORDS)
        (kind, model, prompt), = self.engine.runs
        self.assertEqual((kind, model), (KIND_SETUP, "sonnet"))
        self.assertIn(f"<command>{WORDS}</command>", prompt)              # the owner's own words, alone
        self.assertNotIn("<mail>", prompt)
        _agents, rules = Definitions(files_of(self.app.config)).load()
        self.assertEqual([(rule.name, rule.status) for rule in rules], [("Invoice watch", STATUS_DRAFT)])
        card = self.card()
        self.assertEqual(card.title_label.text(), "Invoice watch")
        self.assertIn("NEW RULE", card.kicker_label.text().upper())
        texts = card.line_texts()
        self.assertTrue(any("from:billing@example.org subject:invoice" in line for line in texts))
        self.assertTrue(any("goes to your Claude plan" in line for line in texts))
        self.assertFalse(card.button("on").isEnabled())                    # a rule needs its dry run first
        self.assertIn("Dry run", card.note_label.text())
        self.assertEqual(self.c.window.reading.current_tab(), ui.SCREEN_AGENTS)
        self.assertEqual(Usage(files_of(self.app.config), clock=lambda: NOW).peek().runs, 1)   # counted

    def test_a_sentence_that_is_no_standing_job_says_so(self) -> None:
        self.engine.answers.append({"kind": "neither", "say": "That is a one-off."})
        self.type("rule: what is the weather like today in town")
        card = self.card()
        self.assertEqual(card.line_texts(), [standing_ui.NEITHER_TEXT])
        self.assertEqual(Definitions(files_of(self.app.config)).load(), ([], []))

    def test_a_filter_the_owner_never_typed_is_refused(self) -> None:
        answer = {**RULE_ANSWER, "rule": {**RULE_ANSWER["rule"], "query": "from:payroll@example.org"}}
        self.engine.answers.append(answer)
        self.type("rule: " + WORDS)
        self.assertEqual(Definitions(files_of(self.app.config)).load(), ([], []))
        self.assertIn("Couldn't set that up".upper(), self.card().kicker_label.text().upper())

    def test_edit_puts_the_words_back_and_discard_drops_the_draft(self) -> None:
        self.engine.answers.append(RULE_ANSWER)
        self.type("rule: " + WORDS)
        self.card().button("edit").click()
        settle()
        self.assertEqual(self.c.window.reading.command_bar.text(), "rule: " + WORDS)
        self.assertEqual(Definitions(files_of(self.app.config)).load(), ([], []))
        self.engine.answers.append(AGENT_ANSWER)
        self.type("agent: " + AGENT_WORDS)
        self.card().button("discard").click()
        settle()
        self.assertEqual(Definitions(files_of(self.app.config)).load(), ([], []))
        self.assertIsNone(self.card())


class DryRunAndTurnOnTests(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.reader = FakeReader([message(1, NOW - timedelta(days=2), sender="Example Billing <billing@example.org>",
                                          subject="Invoice 1043", body="Please pay $40.00 by October 8."),
                                  message(2, NOW - timedelta(days=1), sender="Example Billing <billing@example.org>",
                                          subject="Invoice news", body="Our newsletter.")])
        self.reader.own_addresses = lambda: ["me@example.edu"]
        self.s.reader_for = lambda alias: self.reader
        self.engine.answers.append(RULE_ANSWER)
        self.type("rule: " + WORDS)

    def test_the_dry_run_judges_recent_mail_and_tries_the_rule(self) -> None:
        self.engine.answers.append(RuleAnswer({1: Result(False), 2: Result(True, {"amount": "$40.00"})}))
        self.card().button("dry").click()
        settle()
        kinds = [kind for kind, _model, _prompt in self.engine.runs]
        self.assertEqual(kinds, [KIND_SETUP, KIND_RULE])
        self.assertEqual(self.engine.runs[1][1], "haiku")
        _agents, rules = Definitions(files_of(self.app.config)).load()
        rule = rules[0]
        self.assertEqual(rule.status, STATUS_TRIED)
        self.assertEqual((rule.dry_run.matched, rule.dry_run.judged, rule.dry_run.yes), (2, 2, 1))
        texts = self.card().line_texts()
        self.assertTrue(any(line.startswith("Dry run: about 2 matching mails") for line in texts))
        self.assertTrue(any("lines up a to-do: Pay $40.00" in line for line in texts))
        self.assertTrue(self.card().button("on").isEnabled() is False)     # no Google account recorded yet
        self.assertIn("Sign the work account in first", self.card().note_label.text())

    def test_a_filter_that_reads_nearly_everything_stops_before_any_run(self) -> None:
        self.reader.list_messages = lambda query, max_results: ([("m0000001", "t0000001")], 450)
        self.card().button("dry").click()
        settle()
        self.assertEqual([kind for kind, _m, _p in self.engine.runs], [KIND_SETUP])
        self.assertIn("about 450 emails", self.card().note_label.text())
        self.assertEqual(Definitions(files_of(self.app.config)).load()[1][0].status, STATUS_DRAFT)

    def test_turn_on_records_the_google_account_and_goes_live(self) -> None:
        self.engine.answers.append(RuleAnswer({1: Result(False), 2: Result(True, {"amount": "$40.00"})}))
        self.card().button("dry").click()
        settle()
        self.c._google_accounts = {"work": Account("sub-work-1")}
        self.s.reload()
        self.card().button("on").click()
        settle()
        rule = Definitions(files_of(self.app.config)).load()[1][0]
        self.assertEqual((rule.status, rule.bindings), (STATUS_LIVE, (("work", "sub-work-1"),)))
        self.assertIsNone(self.card())
        self.assertEqual(self.c.window.reading.agents_stage.job_row(rule.id).badge.text(), "LIVE")


class ConsentTests(_Base):
    consented = False

    def test_nothing_turns_on_before_the_consent_cards_yes(self) -> None:
        self.engine.answers.append(AGENT_ANSWER)
        self.type("agent: " + AGENT_WORDS)
        stage = self.c.window.reading.agents_stage
        consent_card = stage.notice_card(standing_ui.CONSENT_CARD)
        self.assertIsNotNone(consent_card)
        self.assertTrue(any("goes to your Claude plan" in line for line in consent_card.line_texts()))
        self.c._google_accounts = {"work": Account("sub-work-1")}
        self.s.reload()
        self.assertFalse(self.card().button("on").isEnabled())
        self.assertIn("say yes", self.card().note_label.text())
        stage.notice_card(standing_ui.CONSENT_CARD).button("yes").click()
        settle()
        self.assertTrue(AppState(files_of(self.app.config)).consented())
        self.assertIsNone(stage.notice_card(standing_ui.CONSENT_CARD))
        self.card().button("on").click()
        settle()
        agent = Definitions(files_of(self.app.config)).load()[0][0]
        self.assertEqual(agent.status, STATUS_LIVE)


class RunByNameTests(_Base):

    def prepare(self, config) -> None:
        save_jobs(config, agents=[agent_data()])
        consent(config)

    def test_run_and_the_agents_name_is_its_run_now(self) -> None:
        self.type(f"run {AGENT_NAME.lower()}")
        self.assertEqual(RunRequests(files_of(self.app.config)).pending(), ["a-0000000000b1"])
        self.assertEqual(self.started, [True])
        texts = [entry.text_label.text() for entry in self.c.window.reading.conversation.entry_widgets()]
        self.assertTrue(any(text.startswith(f"Running {AGENT_NAME} now") for text in texts))

    def test_run_while_everything_is_paused_says_so(self) -> None:
        AppState(files_of(self.app.config)).set_paused_all(True)
        self.s.reload()
        self.type(f"run {AGENT_NAME}")
        self.assertEqual(RunRequests(files_of(self.app.config)).pending(), [])
        texts = [entry.text_label.text() for entry in self.c.window.reading.conversation.entry_widgets()]
        self.assertTrue(any("paused" in text for text in texts))


class HandOffTests(unittest.TestCase):

    def test_an_asks_first_run_hands_the_owners_own_words_to_the_setup(self) -> None:
        plan = {"say": "I'll set that up.", "question": "", "lines": [],
                "standing_job": {"kind": "rule", "why": "make a to-do for every invoice from billing"}}
        app = AppHarness(self, config_extra=STANDING_ON, runs=[plan_stream(plan)], prepare=consent)
        c = app.c
        engine = FakeEngine(RULE_ANSWER)
        c.standing.engine_factory = lambda: engine
        c.standing.thread_runner = lambda work: work()
        app.reading(autoplay=False)
        c.ask.submit(WORDS)
        from tests.ui_fakes import wait_for

        self.assertTrue(wait_for(lambda: bool(engine.runs), 10.0))
        settle()
        self.assertIn(f"<command>{WORDS}</command>", engine.runs[0][2])
        self.assertNotIn("make a to-do for every invoice from billing", engine.runs[0][2])   # never the planner's
        argv = app.factory.runner.started[0].argv
        self.assertTrue(any("standing_job" in str(part) for part in argv))   # offered while [standing] is on


if __name__ == "__main__":
    unittest.main()
