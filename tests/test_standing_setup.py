"""Tests for setting standing jobs up from the chat (part 7 of the build).

"agent:" / "rule:" in the bar and an Ask's planner hand-off start ONE setup run on the owner's own words; what it made
is a draft on the Agents screen's setup card (Dry run, Turn on, Edit, Discard). A rule goes on only after its dry run
(Gmail's own count first: a filter that would read nearly everything stops before any Claude run). Turn on needs the
one-time consent card's yes and records each account's Google account; an account not signed in gets its Sign in
<alias> on the draft's row and setup card, which starts the app's own sign-in of that account (on the action worker,
whose fake calendars never open a browser). "run <name>" is an agent's Run now. Qt runs offscreen; the setup and rule
runs are fakes, nothing reaches Google or claude.exe, no process is started.
"""

from __future__ import annotations

import threading
import unittest
from datetime import timedelta
from typing import Any

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import standing_setup, standing_ui, ui
from briefing_reader.ask import cli
from briefing_reader.ask.routes import ROUTE_STANDING, RouteFlags, route
from briefing_reader.standing.engine import KIND_RULE, KIND_SETUP, StandingResult
from briefing_reader.standing.model import STATUS_DRAFT, STATUS_LIVE, STATUS_TRIED
from briefing_reader.standing.rules import Result, RuleAnswer
from briefing_reader.standing.store import AppState, Definitions, RunRequests, Usage
from tests.ask_fakes import plan_stream
from tests.standing_fakes import (AGENT_NAME, STANDING_ON, CalendarAccount, agent_data, consent, files_of,
                                  hold_sign_in, save_jobs)
from tests.test_standing_google import FakeReader, message
from tests.ui_fakes import NOW, AppHarness, fonts, settle, wait_for

_app: QApplication | None = None

WORDS = "when billing@example.org sends an invoice, make me a to-do to pay it"
RULE_ANSWER = {"kind": "rule", "say": "A rule for Example Billing's invoices.", "name": "Invoice watch",
               "rule": {"account": "work", "query": "from:billing@example.org subject:invoice",
                        "question": "Is this an invoice to pay?",
                        "fields": [{"name": "amount", "type": "money", "hint": "the amount due"}],
                        "action": {"kind": "todo", "title": "Pay {amount}"}}}
AGENT_WORDS = "every morning at 7:30 tell me what pat.example@example.org sent"
BOTH_WORDS = "every morning at 7:30 tell me what pat.example@example.org sent to my personal and work mail"
AGENT_ANSWER = {"kind": "agent", "say": "A morning agent.", "name": "Morning mail",
                "agent": {"trigger": {"kind": "time", "times": ["07:30"], "weekdays": []},
                          "mail": [{"account": "work", "query": "from:pat.example@example.org"}],
                          "proposes": ["todo"], "report": "every_run"}}
BOTH_ANSWER = {**AGENT_ANSWER, "agent": {**AGENT_ANSWER["agent"],
                                         "mail": [{"account": "personal", "query": "from:pat.example@example.org"},
                                                  {"account": "work", "query": "from:pat.example@example.org"}]}}


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

    def is_signed_in(self, feature: str | None = None) -> bool:
        return True


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

    def activate(self) -> None:
        """The window is the active one, as when the owner clicks in it (a field focused then may select all)."""
        self.c.window.activateWindow()
        settle()
        self.assertTrue(self.c.window.isActiveWindow())


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
        self.c._google_accounts = {"work": Account("sub-work-1")}      # signed in: what waits is its dry run
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

    def test_an_agents_card_says_it_reads_only_new_mail(self) -> None:
        self.engine.answers.append(AGENT_ANSWER)
        self.type("agent: " + AGENT_WORDS)
        self.assertIn("New mail only: it reads mail that comes after you turn it on, each mail once",
                      self.card().line_texts())

    def test_a_sentence_typed_while_one_is_set_up_comes_back_to_the_bar(self) -> None:
        pending: list = []
        self.s.thread_runner = pending.append
        self.engine.answers.append(RULE_ANSWER)
        self.type("rule: " + WORDS)
        self.c.window.reading.set_tab(ui.SCREEN_HOME)
        self.type("agent: " + AGENT_WORDS)                              # while the first setup still runs
        bar = self.c.window.reading.command_bar
        self.assertEqual(bar.text(), "agent: " + AGENT_WORDS)           # not lost: back in the bar
        self.assertEqual(bar.status(), standing_ui.SETUP_BUSY_TEXT)     # and said where it was typed
        pending.pop()()
        settle()
        self.assertEqual([kind for kind, _m, _p in self.engine.runs], [KIND_SETUP])

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

    def test_a_tidy_up_the_owner_never_asked_for_is_left_out(self) -> None:
        answer = {**RULE_ANSWER, "rule": {**RULE_ANSWER["rule"], "tidy": {"kind": "archive"}}}
        self.engine.answers.append(answer)
        self.type("rule: " + WORDS)                                     # nothing about archiving in the words
        rule = Definitions(files_of(self.app.config)).load()[1][0]
        self.assertEqual(rule.tidy.kind, "")

    def test_the_apps_engine_starts_nothing_while_the_tests_run(self) -> None:
        from briefing_reader import standing_setup

        result = standing_setup.app_engine(self.app.config).run("x", kind="setup", model="sonnet", timeout_s=1,
                                                                read=lambda *_a: None)
        self.assertIsNotNone(result.failure)
        self.assertFalse(result.started)

    def test_new_rule_leaves_the_cursor_after_rule_so_typing_goes_after_it(self) -> None:
        reading = self.c.window.reading
        reading.set_tab(ui.SCREEN_AGENTS)
        self.activate()
        reading.agents_stage.notice_card(standing_ui.START_CARD).button("rule").click()
        settle()
        bar = reading.command_bar
        self.assertEqual(reading.current_tab(), ui.SCREEN_HOME)
        self.assertTrue(bar.input.hasFocus())
        self.assertEqual((bar.input.selectedText(), bar.input.cursorPosition()), ("", len("rule: ")))
        QTest.keyClicks(bar.input, "when billing")                    # never replaces "rule: "
        self.assertEqual(bar.text(), "rule: when billing")

    def test_edit_puts_the_words_back_and_discard_drops_the_draft(self) -> None:
        self.engine.answers.append(RULE_ANSWER)
        self.type("rule: " + WORDS)
        self.activate()
        self.card().button("edit").click()
        settle()
        reading = self.c.window.reading
        bar = reading.command_bar
        self.assertEqual(bar.text(), "rule: " + WORDS)
        self.assertEqual(reading.current_tab(), ui.SCREEN_HOME)            # the bar is on Home, not on Agents
        self.assertEqual((bar.input.selectedText(), bar.input.cursorPosition()), ("", len("rule: " + WORDS)))
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

    def test_a_pause_during_the_dry_run_is_kept(self) -> None:
        self.engine.answers += [RuleAnswer({1: Result(False), 2: Result(True, {"amount": "$40.00"})}),
                                RuleAnswer({1: Result(True, {"amount": "$40.00"}), 2: Result(False)})]
        self.card().button("dry").click()
        settle()
        self.c._google_accounts = {"work": Account("sub-work-1")}
        self.s.reload()
        self.card().button("on").click()
        settle()
        rule_id = Definitions(files_of(self.app.config)).load()[1][0].id
        pending: list = []
        self.s.thread_runner = pending.append
        self.s.dry_run(rule_id)                                        # a second dry run starts
        self.assertTrue(self.c.window.reading.agents_stage.job_row(rule_id).pause_button.isHidden())
        self.s.toggle_pause(rule_id)                                   # paused while it runs
        pending.pop()()
        settle()
        rule = Definitions(files_of(self.app.config)).load()[1][0]
        self.assertEqual(rule.status, "paused")                        # the dry run never undoes the pause
        self.assertEqual((rule.dry_run.judged, rule.bindings), (2, (("work", "sub-work-1"),)))

    def test_a_dry_run_on_an_account_not_signed_in_says_to_sign_it_in(self) -> None:
        self.reader.available = lambda: ("signedout", "Not signed in to the work account")
        self.card().button("dry").click()
        settle()
        self.assertEqual([kind for kind, _m, _p in self.engine.runs], [KIND_SETUP])   # no Claude run
        self.assertEqual(self.reader.queries, [])                                   # and no Gmail call
        self.assertIn("Sign the work account in first", self.card().note_label.text())   # not "try again"
        self.c._google_accounts = {"work": Account("sub-work-1")}      # signed in since, from the Day view
        self.s.reload()
        self.assertNotIn("Sign the work account in first", self.card().note_label.text())   # never stale
        self.assertIn("Dry run", self.card().note_label.text())                            # what comes next

    def test_a_sign_in_made_elsewhere_shows_when_the_agents_screen_comes_up(self) -> None:
        self.engine.answers.append(RuleAnswer({1: Result(False), 2: Result(True, {"amount": "$40.00"})}))
        self.card().button("dry").click()
        settle()
        self.assertFalse(self.card().button("on").isEnabled())          # the work account is not signed in
        reading = self.c.window.reading
        reading.set_tab(ui.SCREEN_HOME)
        self.c._google_accounts = {"work": Account("sub-work-1")}      # signed in from the Day view meanwhile
        reading.set_tab(ui.SCREEN_AGENTS)
        settle()
        self.assertTrue(self.card().button("on").isEnabled())           # at once, not at the next folder check

    def test_a_live_rules_dry_run_card_offers_close_not_edit_or_discard(self) -> None:
        self.engine.answers += [RuleAnswer({1: Result(False), 2: Result(True, {"amount": "$40.00"})}),
                                RuleAnswer({1: Result(False), 2: Result(True, {"amount": "$40.00"})})]
        self.card().button("dry").click()
        settle()
        self.c._google_accounts = {"work": Account("sub-work-1")}
        self.s.reload()
        self.card().button("on").click()
        settle()
        rule_id = Definitions(files_of(self.app.config)).load()[1][0].id
        self.c.window.reading.agents_stage.job_row(rule_id).dry_run_button.click()      # the live rule's own
        settle()
        card = self.card()
        self.assertEqual([button.property("action") for button in card.buttons()], ["dry", "dismiss"])
        self.assertIn("RULE", card.kicker_label.text().upper())
        self.assertNotIn("NEW RULE", card.kicker_label.text().upper())                  # it is on already
        card.button("dismiss").click()                                                  # Close
        settle()
        self.assertIsNone(self.card())
        self.assertEqual([(rule.id, rule.status) for rule in Definitions(files_of(self.app.config)).load()[1]],
                         [(rule_id, STATUS_LIVE)])                                      # one rule, still on

    def test_a_row_says_why_it_cannot_go_on_yet(self) -> None:
        self.engine.answers.append(RuleAnswer({1: Result(False), 2: Result(True, {"amount": "$40.00"})}))
        self.card().button("dry").click()
        settle()
        rule_id = Definitions(files_of(self.app.config)).load()[1][0].id
        row = self.c.window.reading.agents_stage.job_row(rule_id)
        self.assertIn("Sign the work account in first", row.detail_label.text())
        self.assertFalse(row.turn_on_button.isHidden())
        self.assertFalse(row.turn_on_button.isEnabled())                # off, as the setup card's, until it can go
        self.assertIn("Sign the work account in first", row.turn_on_button.toolTip())
        self.c._google_accounts = {"work": Account("sub-work-1")}
        self.s.reload()
        row = self.c.window.reading.agents_stage.job_row(rule_id)
        self.assertTrue(row.turn_on_button.isEnabled())
        self.assertNotIn("Sign the work account in first", row.detail_label.text())

    def test_a_draft_rule_signs_its_account_in_before_its_dry_run(self) -> None:
        work = self.app.calendars["work"]
        work.signed_in = False
        self.c._google_accounts = {"work": CalendarAccount(work, "sub-work-1")}
        self.s.reload()
        card = self.card()
        self.assertEqual(card.note_label.text(),
                         "Sign the work account in first: click Sign in work. Or Edit and name another account.")
        self.assertEqual([button.property("action") for button in card.buttons()],
                         ["signin:work", "dry", "on", "edit", "discard"])
        rule_id = Definitions(files_of(self.app.config)).load()[1][0].id
        row = self.c.window.reading.agents_stage.job_row(rule_id)
        self.assertIn("Sign the work account in first: click Sign in work.", row.detail_label.text())
        self.assertEqual([button.text() for button in row.buttons()], ["Sign in work", "Dry run", "Delete"])
        done = hold_sign_in(work)
        row.sign_in_button("work").click()
        settle()
        done.set()
        self.assertTrue(wait_for(lambda: not self.c._signing_in(), 5.0))
        settle()
        signed = "The work account is signed in. " + standing_setup.DRY_RUN_FIRST     # what comes next
        self.assertEqual(self.card().note_label.text(), signed)
        self.assertIn(signed, self.c.window.reading.agents_stage.job_row(rule_id).detail_label.text())
        self.assertIn(("sign_in",), work.calls)

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


class SignInWordsTests(unittest.TestCase):

    def test_the_words_name_the_button_never_the_day_view(self) -> None:
        self.assertEqual(standing_setup.sign_in_first("work"),
                         "Sign the work account in first: click Sign in work. Or Edit and name another account.")
        self.assertEqual(standing_setup.sign_in_first("work", edit=False),    # a row has no Edit
                         "Sign the work account in first: click Sign in work.")
        self.assertEqual(standing_setup.sign_in_first(["personal", "work"], edit=False),
                         "Sign the personal and work accounts in first: click Sign in personal and Sign in work.")
        for words in (standing_setup.sign_in_first("work"), standing_setup.sign_in_first("work", edit=False)):
            self.assertNotIn("Day view", words)
            self.assertNotIn("Connect", words)


class SignInTests(_Base):
    """The owner's state: consent given, personal signed in, work not; a draft agent reads both."""

    def setUp(self) -> None:
        super().setUp()
        calendars = self.app.calendars
        calendars["work"].signed_in = False
        self.c._google_accounts = {"personal": CalendarAccount(calendars["personal"], "sub-personal-1"),
                                   "work": CalendarAccount(calendars["work"], "sub-work-1")}
        self.engine.answers.append(BOTH_ANSWER)
        self.type("agent: " + BOTH_WORDS)                      # names both accounts: work stays, signed out
        self.agent_id = Definitions(files_of(self.app.config)).load()[0][0].id

    def row(self) -> Any:
        return self.c.window.reading.agents_stage.job_row(self.agent_id)

    def end(self, done: threading.Event) -> None:
        """The owner finishes (or leaves) the sign-in in the browser; the app hears how it went."""
        done.set()
        self.assertTrue(wait_for(lambda: not self.c._signing_in(), 5.0))
        settle()

    def test_only_the_account_not_signed_in_gets_a_sign_in_named_for_it(self) -> None:
        row, card = self.row(), self.card()
        self.assertEqual([button.text() for button in row.buttons()], ["Sign in work", "Turn on", "Delete"])
        self.assertEqual([button.text() for button in card.buttons()], ["Sign in work", "Turn on", "Edit", "Discard"])
        self.assertIn("Sign the work account in first: click Sign in work.", row.detail_label.text())
        self.assertNotIn("Edit", row.detail_label.text())                      # a row has no Edit
        self.assertEqual(card.note_label.text(),
                         "Sign the work account in first: click Sign in work. Or Edit and name another account.")
        for words in (row.detail_label.text(), card.note_label.text(), row.turn_on_button.toolTip()):
            self.assertNotIn("Day view", words)
        self.assertFalse(row.turn_on_button.isEnabled())
        self.assertFalse(card.button("on").isEnabled())

    def test_sign_in_starts_that_accounts_own_sign_in_then_turn_on_goes(self) -> None:
        work, personal = self.app.calendars["work"], self.app.calendars["personal"]
        done = hold_sign_in(work)
        self.row().sign_in_button("work").click()
        settle()
        row, card = self.row(), self.card()
        self.assertIn(standing_ui.SIGN_IN_RUNNING, row.detail_label.text())    # where it was clicked
        self.assertEqual(card.note_label.text(), standing_ui.SIGN_IN_RUNNING)
        self.assertFalse(row.sign_in_button("work").isEnabled())
        self.assertFalse(card.button("signin:work").isEnabled())
        self.end(done)
        self.assertIn(("sign_in",), work.calls)                                # the app's own sign-in of work
        self.assertNotIn(("sign_in",), personal.calls)
        row, card = self.row(), self.card()
        self.assertIsNone(row.sign_in_button("work"))
        self.assertIn("The work account is signed in. Turn on is ready.", row.detail_label.text())
        self.assertEqual(card.note_label.text(), "The work account is signed in. Turn on is ready.")
        self.assertTrue(row.turn_on_button.isEnabled())
        row.turn_on_button.click()
        settle()
        agent = Definitions(files_of(self.app.config)).load()[0][0]
        self.assertEqual((agent.status, dict(agent.bindings)),
                         (STATUS_LIVE, {"personal": "sub-personal-1", "work": "sub-work-1"}))

    def test_a_cancelled_sign_in_says_so_and_turn_on_waits(self) -> None:
        from briefing_reader.gcal import CalendarAuthError
        from briefing_reader.google_auth import PROBLEM_DENIED

        work = self.app.calendars["work"]
        done = hold_sign_in(work, CalendarAuthError("Google sign-in for the work account was cancelled or access "
                                                    "was denied", problem=PROBLEM_DENIED))
        self.card().button("signin:work").click()                              # the setup card's, this time
        settle()
        self.assertEqual(self.card().note_label.text(), standing_ui.SIGN_IN_RUNNING)
        self.end(done)
        said = ("The work account's sign-in was cancelled, or Google did not allow it; nothing changed. Click Sign in "
                "work to try again.")
        row, card = self.row(), self.card()
        self.assertIn(said, row.detail_label.text())
        self.assertEqual(card.note_label.text(), said + " Or Edit and name another account.")
        self.assertTrue(row.sign_in_button("work").isEnabled())                # it can be tried again
        self.assertFalse(row.turn_on_button.isEnabled())
        self.assertFalse(card.button("on").isEnabled())
        self.app.clock.advance(3)                                               # later: what to do, again
        self.s.reload()
        self.assertIn("Sign the work account in first: click Sign in work.", self.row().detail_label.text())

    def test_a_failed_sign_in_says_why_in_the_apps_words(self) -> None:
        from briefing_reader.gcal import CalendarError

        done = hold_sign_in(self.app.calendars["work"], CalendarError("Google sign-in failed (server_error)"))
        self.row().sign_in_button("work").click()
        settle()
        self.end(done)
        row = self.row()
        self.assertIn("Google sign-in failed (server_error); nothing changed. Click Sign in work to try again.",
                      row.detail_label.text())
        self.assertFalse(row.turn_on_button.isEnabled())
        self.assertEqual(Definitions(files_of(self.app.config)).load()[0][0].status, STATUS_DRAFT)

    def test_the_first_sign_in_asks_which_google_account_it_is_as_everywhere(self) -> None:
        work = self.app.calendars["work"]
        asked = {"work": "me@example.edu"}
        self.c._pending_account = lambda alias: asked.get(alias, "") if self.app.calendars[alias].signed_in else ""
        self.c._executor.confirm_account = lambda alias, address: asked.pop(alias, None) is not None
        done = hold_sign_in(work)
        self.row().sign_in_button("work").click()
        settle()
        self.end(done)
        dialog = self.c._account_dialog
        self.assertTrue(dialog is not None and dialog.alias == "work")         # the app's own question
        self.assertIn("The work account is signed in.", self.row().detail_label.text())
        dialog.yes_button.click()
        settle()
        self.assertIsNone(self.c._account_dialog)
        self.assertTrue(self.row().turn_on_button.isEnabled())

    def test_no_use_another_account_opens_the_sign_in_again_and_turn_on_waits(self) -> None:
        work = self.app.calendars["work"]
        asked = {"work": "someone.else@example.org"}
        self.c._pending_account = lambda alias: asked.get(alias, "") if self.app.calendars[alias].signed_in else ""
        self.c._executor.confirm_account = lambda alias, address: asked.pop(alias, None) is not None
        work.disconnect = lambda: setattr(work, "signed_in", False)            # "No": the app forgets it first
        done = hold_sign_in(work)
        self.row().sign_in_button("work").click()
        settle()
        self.end(done)
        again = hold_sign_in(work)
        self.c._account_dialog.no_button.click()                               # the wrong Google account
        row = self.row()
        self.assertIn(standing_ui.SIGN_IN_RUNNING, row.detail_label.text())    # at once: its sign-in opens again
        self.assertFalse(row.turn_on_button.isEnabled())
        settle()
        self.assertFalse(self.row().turn_on_button.isEnabled())
        asked["work"] = "me@example.edu"
        self.end(again)
        self.c._account_dialog.yes_button.click()
        settle()
        self.assertIn("The work account is signed in. Turn on is ready.", self.row().detail_label.text())
        self.assertTrue(self.row().turn_on_button.isEnabled())

    def test_one_sign_in_at_a_time(self) -> None:
        personal, work = self.app.calendars["personal"], self.app.calendars["work"]
        done = hold_sign_in(personal)
        self.c.sign_in_for_mail("personal")                                     # "Allow personal mail" under the bar
        settle()
        row = self.row()
        self.assertFalse(row.sign_in_button("work").isEnabled())               # one sign-in at a time ...
        self.assertIn(standing_ui.SIGN_IN_RUNNING, row.detail_label.text())    # ... and the row says why
        self.end(done)
        self.assertTrue(self.row().sign_in_button("work").isEnabled())
        self.assertNotIn(("sign_in",), work.calls)
        self.c._jobs["card-1"] = "running"                                      # an approval on its way
        self.row().sign_in_button("work").click()
        settle()
        self.assertIn(standing_ui.SIGN_IN_BUSY.format(alias="work"), self.row().detail_label.text())
        self.assertIsNone(self.c._connect_alias)
        del self.c._jobs["card-1"]


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
        stage.notice_card(standing_ui.CONSENT_CARD).button("later").click()        # Not now: the card goes ...
        settle()
        self.assertIsNone(stage.notice_card(standing_ui.CONSENT_CARD))
        line = ("Nothing runs until you say yes on the card that explains what agents read.", "Show the card",
                standing_ui.CONSENT_LINK)
        self.assertIn(line, stage.agents_info().notes)                              # ... and the line brings it back
        stage.linkClicked.emit(standing_ui.CONSENT_LINK)
        settle()
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


class PreferSignedInTests(_Base):

    def accounts(self) -> None:
        signed_out = Account("sub-work-1")
        signed_out.is_signed_in = lambda feature=None: False
        self.c._google_accounts = {"personal": Account("sub-personal-1"), "work": signed_out}

    def test_an_account_the_words_never_named_and_not_signed_in_is_left_out(self) -> None:
        self.accounts()
        self.engine.answers.append(BOTH_ANSWER)
        self.type("agent: " + AGENT_WORDS)                         # names no account
        agent = Definitions(files_of(self.app.config)).load()[0][0]
        self.assertEqual(agent.accounts, ("personal",))

    def test_a_rule_moves_to_the_one_signed_in_account(self) -> None:
        self.accounts()
        self.engine.answers.append(RULE_ANSWER)                      # its model picked work
        self.type("rule: " + WORDS)
        self.assertEqual(Definitions(files_of(self.app.config)).load()[1][0].account, "personal")

    def test_the_card_says_what_was_left_out_and_why_never_the_setups_own_note(self) -> None:
        self.accounts()
        self.engine.answers.append({**BOTH_ANSWER, "say": "A morning look at what Pat sent to your personal and work "
                                                          "mail."})
        self.type("agent: " + AGENT_WORDS)                         # names no account
        texts = self.card().line_texts()
        self.assertFalse(any("personal and work" in line for line in texts))        # it reads personal only
        self.assertIn("Jarvis's note: It reads only the personal account's mail, since work is not signed in to "
                      "Jarvis and your sentence did not name it. To add work, Edit and name it (for example: my work "
                      "mail).", texts)
        self.assertTrue(self.card().button("on").isEnabled())

    def test_a_moved_rules_card_says_which_account_it_watches_and_why(self) -> None:
        self.accounts()
        self.engine.answers.append({**RULE_ANSWER, "say": "A rule on your work mail for Example Billing's invoices."})
        self.type("rule: " + WORDS)
        texts = self.card().line_texts()
        self.assertFalse(any("your work mail" in line for line in texts))
        self.assertIn("Jarvis's note: It watches the personal account, since work is not signed in to Jarvis and your "
                      "sentence did not name it. To watch work instead, Edit and name it (for example: my work "
                      "mail).", texts)

    def test_a_named_account_keeps_the_setups_own_note(self) -> None:
        self.accounts()
        self.engine.answers.append({**BOTH_ANSWER, "say": "A morning look at your personal and work mail."})
        self.type("agent: " + BOTH_WORDS)                          # names both: work stays, signed out
        self.assertIn("Jarvis's note: A morning look at your personal and work mail.", self.card().line_texts())


if __name__ == "__main__":
    unittest.main()
