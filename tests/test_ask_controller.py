"""Tests for Ask Jarvis in the app: ask_ui.AskController wired into the AppController.

Off by default (nothing changes); on: the readiness check and every planner run happen on the
"ask" thread, one Ask at a time, Cancel ends a run and proposes nothing, the status line follows
the stages and shows the answer or the reason, the briefing pauses on Enter and the orb says
PLANNING, the cards land under ASK, the header's claude chip follows readiness and limits, the
gmail_read link signs the account in again (on the action worker), --ask opens the bar without
playing or settling a slot, and the logs never hold the request, the answer or an address.
claude.exe is never started (tests.ask_fakes' runner), nothing reaches Google or Notion, and Qt
runs offscreen.
"""

from __future__ import annotations

import json
import logging
import threading
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from briefing_reader import ask_ui, hud, live, ui
from briefing_reader.actions import SOURCE_ASK
from briefing_reader.ask import planner as ask_planner
from briefing_reader.google_auth import PROBLEM_SCOPE
from briefing_reader.runstate import RunState, handled_slot_key
from tests.ask_fakes import FakeClock, FakeProcess, FakeReader, plan_stream, stream
from tests.ask_fakes import stream as stream_lines
from tests.ui_fakes import (
    CAL_LINE,
    COMMAND,
    GUEST,
    NOW,
    SAY,
    AppHarness,
    fonts,
    plan,
    settle,
    wait_for,
)

_app: QApplication | None = None
AUTH_NONE = json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"})


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_ask_controller", "-platform", "offscreen"])
    fonts()
    ask_planner.cli.forget_probes()


class GatedProcess(FakeProcess):
    """A planner run whose lines after the init event wait for ``gate``."""

    def __init__(self, lines, gate: threading.Event) -> None:
        super().__init__(lines)
        self.gate = gate
        self.given = 0

    def read_line(self, timeout: float):
        if self.killed:
            return None
        if self.given >= 1 and not self.gate.is_set():
            self.gate.wait(min(timeout, 0.05))
            if not self.gate.is_set():
                return ""
        self.given += 1
        return super().read_line(timeout)


def ui_NOW_PLUS_TWO_HOURS():  # noqa: N802 - a time after the hour's cap is over
    from datetime import timedelta

    return NOW + timedelta(hours=2)


def submit(c: ui.AppController, text: str = COMMAND) -> None:
    c.window.reading.command_bar.set_text(text)
    c.window.reading.command_bar.input.returnPressed.emit()
    settle()


class AskOffTests(unittest.TestCase):

    def test_with_ask_off_nothing_changes(self) -> None:
        app = AppHarness(self, ask=False)
        app.reading()
        c = app.c
        self.assertIsNone(c.ask)
        self.assertFalse(c.window.reading.ask_available())
        self.assertIsNone(c.window.header.service_chip(ui.ASK_CHIP))
        self.assertEqual(c.window.reading.approvals.group_headers(), [])
        labels = [card.kind_label.full_text() for card in c.window.reading.approvals.cards()]
        self.assertEqual(labels, ["CALENDAR", "TODO"])
        self.assertTrue(all(action.source == "briefing" for action in c._actions))
        self.assertFalse(app.factory.runner)   # the planner was never built


class AskControllerTests(unittest.TestCase):

    def make(self, *runs, **kwargs) -> AppHarness:
        app = AppHarness(self, runs=runs, **kwargs)
        app.reading()
        app.c.ask.start()
        self.assertTrue(wait_for(lambda: app.c.ask.ready is not None), "readiness checked")
        return app

    def bar(self, app: AppHarness) -> hud.CommandBar:
        return app.c.window.reading.command_bar

    def test_readiness_runs_on_the_ask_thread_and_sets_the_chip(self) -> None:
        app = self.make()
        c = app.c
        self.assertTrue(c.window.reading.ask_available())
        chip = c.window.header.service_chip(ui.ASK_CHIP)
        self.assertEqual(chip.status(), hud.STATUS_OK)
        self.assertIn("20 planner run(s) left this hour", chip.toolTip())
        self.assertEqual(set(app.factory.quick_threads), {"ask"})   # --version, --help, auth status
        tails = [argv[1:] for argv, _env in app.factory.runner.quick]
        self.assertEqual(tails, [["--version"], ["--help"], ["auth", "status", "--json"]])
        self.assertFalse(app.factory.runner.started)                # no model request
        bar = self.bar(app)
        self.assertEqual((bar.status(), bar.tone()), (ask_ui.IDLE_HINT, hud.TONE_IDLE))

    def test_an_ask_shows_its_cards_under_ask(self) -> None:
        gate = threading.Event()
        app = self.make(GatedProcess(plan(), gate))
        c, bar = app.c, self.bar(app)
        c.player.state = "playing"
        submit(c)
        self.assertTrue(c.ask.busy and bar.is_running())
        self.assertTrue(wait_for(lambda: c.player.state == "paused"))   # the briefing pauses once planning starts
        self.assertTrue(wait_for(lambda: c.ask.stage() == ask_planner.STAGE_PLANNING))
        self.assertTrue(bar.status().startswith("Planning..."))
        self.assertEqual(c.window.reading.state_label.text(), ui.ASK_PLANNING_LABEL)
        self.assertEqual(c.window.reading.orb.state(), hud.ORB_WORKING)
        gate.set()
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        self.assertEqual((bar.status(), bar.tone()), (SAY, hud.TONE_DONE))
        self.assertEqual(bar.text(), "")                               # cleared after an answer
        self.assertFalse(bar.is_running())
        self.assertNotEqual(c.window.reading.state_label.text(), ui.ASK_PLANNING_LABEL)
        reading = c.window.reading
        self.assertEqual(reading.approvals.group_headers(), [("ASK", 2), ("BRIEFING", 2)])
        labels = [card.kind_label.full_text() for card in reading.approvals.cards()]
        self.assertEqual(labels, ["ASK \u00b7 MOVE \u00b7 WORK", "ASK \u00b7 EMAIL \u00b7 WORK", "CALENDAR", "TODO"])
        asked = [action for action in c._actions if action.source == SOURCE_ASK]
        self.assertEqual(len(asked), 2)
        self.assertEqual(asked[1].unverified, frozenset({GUEST}))       # not typed by you: NEW until ticked
        newest = reading.activity.entries()[0]
        self.assertEqual((newest[1], newest[2]), (hud.TAG_ASK, "2 proposals"))
        self.assertIn("1 planner run", newest[3])
        self.assertIn("2 PROPOSALS UNDER ASK", bar.meta())
        self.assertIn("ask", app.calendars["work"].threads)            # read on the ask thread, never signed in
        self.assertNotIn(("sign_in",), app.calendars["work"].calls)

    def test_one_ask_at_a_time(self) -> None:
        gate = threading.Event()
        app = self.make(GatedProcess(plan(), gate))
        c, bar = app.c, self.bar(app)
        submit(c)
        c.ask.submit("another request")
        self.assertIn("already planning", bar.status())
        gate.set()
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        self.assertEqual(len(app.factory.runner.started), 1)

    def test_cancel_ends_the_run_and_proposes_nothing(self) -> None:
        gate = threading.Event()
        process = GatedProcess(plan(), gate)
        app = self.make(process)
        c, bar = app.c, self.bar(app)
        submit(c)
        self.assertTrue(wait_for(lambda: c.ask.stage() == ask_planner.STAGE_PLANNING))
        self.assertTrue(wait_for(lambda: process.given >= 1), "the CLI is running")
        bar.button.click()
        self.assertEqual(bar.status(), ask_ui.CANCELLING_TEXT)
        self.assertFalse(bar.button.isEnabled())
        self.assertTrue(wait_for(lambda: not c.ask.busy, 3))
        self.assertTrue(process.killed)
        self.assertEqual(bar.status(), ask_ui.CANCELLED_TEXT)
        self.assertFalse([action for action in c._actions if action.source == SOURCE_ASK])
        self.assertEqual(bar.text(), COMMAND)                          # the draft stays

    def test_a_timeout_shows_why_and_proposes_nothing(self) -> None:
        clock = FakeClock()
        app = self.make(FakeProcess(stream("success")[:1], clock=clock, hang=True), engine_clock=clock)
        c, bar = app.c, self.bar(app)
        submit(c)
        self.assertTrue(wait_for(lambda: not c.ask.busy, 10))
        self.assertEqual((bar.status(), bar.tone()), ("Took too long; nothing was proposed", hud.TONE_ERROR))
        self.assertFalse([action for action in c._actions if action.source == SOURCE_ASK])
        newest = c.window.reading.activity.entries()[0]
        self.assertEqual((newest[1], newest[2]), (hud.TAG_STOP, "Ask: nothing was proposed"))

    def test_not_signed_in_to_a_claude_plan_runs_nothing(self) -> None:
        app = self.make(auth=AUTH_NONE)
        c, bar = app.c, self.bar(app)
        chip = c.window.header.service_chip(ui.ASK_CHIP)
        self.assertEqual(chip.status(), hud.STATUS_WARN)
        self.assertIn("SIGN IN", chip.toolTip())
        self.assertIn("claude auth login --claudeai", bar.status())
        submit(c)
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        self.assertFalse(app.factory.runner.started)
        self.assertIn("claude auth login --claudeai", bar.status())

    def test_a_google_sign_in_change_rechecks_only_the_mail(self) -> None:
        app = self.make(auth=AUTH_NONE)   # not ready: a full check would run the CLI again
        c = app.c
        quick = len(app.factory.runner.quick)
        reader = FakeReader("work", problem=(PROBLEM_SCOPE, "Google did not allow reading email for the work account"))
        app.factory.planner.sources.readers["work"] = reader
        c._on_account_status({"work": (True, "", "")})
        self.assertTrue(wait_for(lambda: self.bar(app).link() == "Allow work mail"))
        self.assertEqual(len(app.factory.runner.quick), quick)          # no --version / --help / auth status
        self.assertFalse(c.ask.ready.ok)                                 # the last answer is kept

    def test_no_ask_while_a_google_sign_in_is_open(self) -> None:
        app = self.make()
        c, bar = app.c, self.bar(app)
        c._connect_alias = "work"
        submit(c)
        self.assertFalse(c.ask.busy)
        self.assertEqual((bar.status(), bar.tone()), (ui.ASK_SIGN_IN_FIRST, hud.TONE_WARN))

    def test_the_hourly_limit_refuses_and_the_chip_says_limit(self) -> None:
        app = AppHarness(self, runs=(plan(lines=(CAL_LINE,)),), max_per_hour=1)
        app.reading()
        c = app.c
        c.ask.start()
        self.assertTrue(wait_for(lambda: c.ask.ready is not None))
        submit(c, "block two hours on Tuesday")
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        submit(c, "and one more")
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        bar = self.bar(app)
        self.assertTrue(bar.status().startswith("Ask limit reached; try again after "), bar.status())
        self.assertEqual(bar.tone(), hud.TONE_WARN)
        self.assertEqual(len(app.factory.runner.started), 1)
        chip = c.window.header.service_chip(ui.ASK_CHIP)
        self.assertTrue(wait_for(lambda: "LIMIT" in chip.toolTip()))

    def test_the_mail_link_signs_the_account_in_again(self) -> None:
        reader = FakeReader("work", problem=(PROBLEM_SCOPE, "Google did not allow reading email for the work "
                                                            "account; sign in again and tick that box"))
        app = self.make(readers={"work": reader, "personal": FakeReader("personal")})
        c, bar = app.c, self.bar(app)
        self.assertEqual(bar.link(), "Allow work mail")
        self.assertIn("tick the box for reading email", bar.link_button.toolTip())
        app.calendars["work"].on_sign_in = lambda: setattr(reader, "problem", ("", ""))
        bar.link_button.click()
        self.assertTrue(wait_for(lambda: ("sign_in",) in app.calendars["work"].calls))
        self.assertEqual(app.calendars["work"].threads - {"ask"}, {"calendar"})   # the action worker's sign-in
        self.assertTrue(wait_for(lambda: bar.link() == ""))

    def test_no_mail_link_for_an_account_that_is_not_signed_in_at_all(self) -> None:
        reader = FakeReader("work", problem=("signedout", "Not signed in to the work account"))
        app = AppHarness(self, readers={"work": reader})
        app.calendars["work"].signed_in = False
        app.reading()
        app.c.ask.start()
        self.assertTrue(wait_for(lambda: app.c.ask.ready is not None))
        self.assertEqual(self.bar(app).link(), "")

    def test_esc_on_an_empty_field_brings_back_the_idle_line(self) -> None:
        app = self.make(plan(lines=(CAL_LINE,)))
        c, bar = app.c, self.bar(app)
        submit(c, "block two hours on Tuesday")
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        self.assertEqual(bar.tone(), hud.TONE_DONE)
        bar.statusDismissed.emit()
        self.assertEqual((bar.status(), bar.tone()), (ask_ui.IDLE_HINT, hud.TONE_IDLE))

    def test_the_logs_never_hold_the_request_the_answer_or_an_address(self) -> None:
        app = self.make(plan())
        c = app.c
        with self.assertLogs("briefing_reader", level="DEBUG") as logs:
            submit(c)
            self.assertTrue(wait_for(lambda: not c.ask.busy))
            settle()
        text = "\n".join(logs.output)
        for secret in (COMMAND, SAY, GUEST, "Jarvis test sync", "sk-ant-dummy"):
            self.assertNotIn(secret, text)
        self.assertIn("Ask 1 started", text)

    def busy_spy(self, app: AppHarness) -> list[bool]:
        seen: list[bool] = []
        app.c.ask.busyChanged.connect(seen.append)
        return seen

    def test_a_refused_enter_never_pauses_the_briefing(self) -> None:
        """Claude Code missing, not signed in, or a limit reached: Enter shows why, and the
        briefing keeps playing (no PLANNING, no pause)."""
        missing = self.make()
        missing.factory.planner.engine._locate = lambda _env: None
        missing.factory.planner.engine.forget()
        signed_out = self.make(auth=AUTH_NONE)
        capped = AppHarness(self, runs=(plan(lines=(CAL_LINE,)),), max_per_hour=1)
        capped.reading()
        capped.c.ask.start()
        self.assertTrue(wait_for(lambda: capped.c.ask.ready is not None))
        submit(capped.c, "block two hours on Tuesday")
        self.assertTrue(wait_for(lambda: not capped.c.ask.busy))
        for name, app, words in (("cli missing", missing, "JARVIS_CLAUDE_EXE"),
                                 ("not signed in", signed_out, "claude auth login --claudeai"),
                                 ("cap", capped, "Ask limit reached")):
            with self.subTest(name):
                c, bar = app.c, self.bar(app)
                seen = self.busy_spy(app)
                started = len(app.factory.runner.started)
                c.player.state = "playing"
                submit(c, "one more request")
                self.assertTrue(wait_for(lambda: not c.ask.busy))
                settle()
                self.assertEqual(c.player.state, "playing")
                self.assertEqual(seen, [])
                self.assertEqual(len(app.factory.runner.started), started)
                self.assertNotEqual(c.window.reading.state_label.text(), ui.ASK_PLANNING_LABEL)
                self.assertIn(words, bar.status())
                self.assertEqual(bar.text(), "one more request")      # the draft stays

    def test_a_question_back_or_nothing_to_propose_keeps_the_draft(self) -> None:
        app = self.make(plan(say="", question="Which Friday did you mean?", lines=()),
                        plan(say="I couldn't find that meeting.", lines=()), plan(lines=(CAL_LINE,)))
        c, bar = app.c, self.bar(app)
        for expected in ("Which Friday did you mean?", "I couldn't find that meeting."):
            submit(c, "move the sync to Friday")
            self.assertTrue(wait_for(lambda: not c.ask.busy))
            self.assertEqual(bar.status(), expected)
            self.assertEqual(bar.text(), "move the sync to Friday")   # edit it and press Enter again
        submit(c, "block two hours on Tuesday")
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        self.assertEqual(bar.text(), "")                               # a card to decide: cleared

    def test_the_idle_bar_says_when_a_limit_is_reached(self) -> None:
        app = AppHarness(self, runs=(plan(lines=(CAL_LINE,)),), max_per_hour=1)
        app.reading()
        c, bar = app.c, self.bar(app)
        c.ask.start()
        self.assertTrue(wait_for(lambda: c.ask.ready is not None))
        submit(c, "block two hours on Tuesday")
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        bar.statusDismissed.emit()                                     # the answer is put away
        self.assertTrue(bar.status().startswith("Ask limit reached; try again after "), bar.status())
        self.assertEqual(bar.tone(), hud.TONE_WARN)
        self.assertFalse(bar.is_folded())
        c.ask._limit_message = "Your Claude plan's usage limit is reached (it resets 3pm); nothing was proposed"
        c.ask.caps = c.ask.planner.usage.check(now=ui_NOW_PLUS_TWO_HOURS())
        bar.statusDismissed.emit()
        self.assertTrue(bar.status().startswith("Your Claude plan's usage limit is reached"), bar.status())

    def test_the_idle_hint_folds_away_and_comes_back_with_an_answer(self) -> None:
        app = self.make(plan(lines=(CAL_LINE,)))
        c, bar = app.c, self.bar(app)
        self.assertTrue(bar.is_folded())                               # only the field: the transcript keeps its room
        self.assertIn("NEEDS YOUR OK", bar.input.toolTip())
        submit(c, "block two hours on Tuesday")
        self.assertFalse(bar.is_folded())
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        self.assertFalse(bar.is_folded())                              # the answer shows
        bar.statusDismissed.emit()
        self.assertTrue(bar.is_folded())

    def test_mail_the_planner_could_not_read_says_what_to_do(self) -> None:
        reader = FakeReader("work", problem=(PROBLEM_SCOPE, "Google did not allow reading email for the work account"))
        search = plan_stream({"say": "I looked for Ana's budget email.", "lines": [],
                              "gmail_search": {"account": "work", "query": "from:ana budget", "why": "x"}})
        app = self.make(search, readers={"work": reader, "personal": FakeReader("personal")})
        c, bar = app.c, self.bar(app)
        c.window.resize(900, 600)
        settle()
        self.assertTrue(bar.is_compact())
        submit(c, "find Ana's budget email")
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        settle()
        status = bar.status()
        self.assertEqual(status, 'Jarvis couldn\'t read your work mail: click "Allow work mail" to let it. '
                                 'I looked for Ana\'s budget email.')   # what to do first
        self.assertNotIn("((", status)
        self.assertTrue(wait_for(lambda: bar.link() == "Allow work mail"))
        self.assertTrue(bar.link_shown())                              # beside the note, even compact
        self.assertEqual(bar.text(), "find Ana's budget email")

    def test_no_mail_sign_in_while_an_ask_runs(self) -> None:
        reader = FakeReader("work", problem=(PROBLEM_SCOPE, "Google did not allow reading email for the work account"))
        gate = threading.Event()
        process = GatedProcess(plan(lines=(CAL_LINE,)), gate)
        app = self.make(process, readers={"work": reader})
        c, bar = app.c, self.bar(app)
        self.assertTrue(bar.link_shown())
        submit(c, "block two hours on Tuesday")
        self.assertTrue(wait_for(lambda: process.given >= 1))
        self.assertFalse(bar.link_shown())                             # hidden while it runs
        c.sign_in_for_mail("work")                                     # and refused if called anyway
        settle()
        self.assertNotIn(("sign_in",), app.calendars["work"].calls)
        gate.set()
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        self.assertTrue(wait_for(lambda: bar.link_shown()))

    def test_shutdown_cancels_a_running_ask(self) -> None:
        gate = threading.Event()
        process = GatedProcess(plan(), gate)
        app = self.make(process)
        c = app.c
        submit(c)
        self.assertTrue(wait_for(lambda: c.ask.stage() == ask_planner.STAGE_PLANNING))
        self.assertTrue(wait_for(lambda: process.given >= 1), "the CLI is running")
        c.ask.shutdown()
        c.ask.join(3)
        self.assertTrue(process.killed)
        self.assertFalse(c.ask.busy)


class DefaultPlannerTests(unittest.TestCase):

    def test_the_apps_planner_reads_mail_on_the_shared_accounts_and_runs_nothing(self) -> None:
        from briefing_reader.executor import build_accounts
        from briefing_reader.gmail import GmailReader

        app = AppHarness(self, ask=False)
        config = app.config
        accounts = build_accounts(config)
        planner = ask_ui.default_planner(config, {}, {}, accounts)
        self.assertIsInstance(planner, ask_planner.AskPlanner)
        self.assertIsInstance(planner.engine, ask_planner.ClaudeEngine)
        readers = planner.sources.readers
        self.assertEqual(set(readers), {"personal", "work"})             # both have "gmail_read"
        self.assertTrue(all(isinstance(reader, GmailReader) for reader in readers.values()))
        self.assertIs(readers["work"]._account, accounts["work"])         # one sign-in per account
        self.assertEqual(ask_ui.default_planner(config, {}, {}, {}).sources.readers, {})   # injected Google


class AskModeTests(unittest.TestCase):

    def test_ask_mode_opens_the_bar_without_playing_or_settling(self) -> None:
        app = AppHarness(self, ask_mode=True, run_state=None)
        state = RunState(app.root / "runstate.json")
        c = app.c
        c.run_state = state
        c.slots = {"AM": "13:40"}
        app.briefing()
        with mock.patch.object(ui, "force_foreground"), \
                mock.patch.object(hud.CommandBar, "focus_input") as focused:
            c.open_ask()
            settle()
        self.assertEqual(c.state, ui.STATE_READING)
        self.assertTrue(c.window.is_reading_view())
        key = handled_slot_key(NOW, c.slots)
        self.assertIsNotNone(key)
        self.assertEqual(c.player.starts, [])                          # nothing played
        focused.assert_called()
        self.assertFalse(state.is_handled(key))                        # no slot settled
        self.assertEqual(c.window.reading.play_button.text(), "Play")
        self.assertIsNone(c._record_done())                            # Done would leave it open
        self.assertFalse(state.is_handled(key))
        c.toggle_play()                                                # Play: heard now, so it settles
        self.assertEqual(len(c.player.starts), 1)
        self.assertTrue(state.is_handled(key))

    def test_an_activation_with_ask_opens_the_bar(self) -> None:
        app = AppHarness(self)
        c = app.c
        app.briefing()
        c.state = ui.STATE_PROMPT
        with mock.patch.object(ui, "force_foreground"), \
                mock.patch.object(hud.CommandBar, "focus_input") as focused:
            c.handle_activation({"cmd": "activate", "run": None, "now": False, "ask": True})
            settle()
        self.assertEqual(c.state, ui.STATE_READING)
        self.assertEqual(c.player.starts, [])
        focused.assert_called()

    def test_open_ask_while_ask_is_off_says_so(self) -> None:
        app = AppHarness(self, ask=False)
        c = app.c
        app.briefing()
        c.state = ui.STATE_PROMPT
        with mock.patch.object(ui, "force_foreground"):
            c.open_ask()
        self.assertEqual(c.state, ui.STATE_READING)
        self.assertIn("[ask] enabled = false", c._note)


class AskLiveTests(unittest.TestCase):
    """The Ask's LIVE view task (live.LiveStream through AskController(live=)): one task per accepted
    Ask, from "Your request" to "Answer and cards", finished as the Ask ended."""

    make = AskControllerTests.make
    bar = AskControllerTests.bar

    def stream_of(self, app: AppHarness) -> live.LiveStream:
        stream = getattr(app.c, "live", None)
        if not isinstance(stream, live.LiveStream):   # before the app owns one: give the controller its own
            stream = live.LiveStream()
            app.c.ask.live = stream
        return stream

    @staticmethod
    def asks(stream: live.LiveStream) -> list[live.TaskView]:
        return [task for task in stream.snapshot() if task.kind == live.TASK_ASK]

    def test_an_ask_is_one_task_from_request_to_cards(self) -> None:
        gate = threading.Event()
        app = self.make(GatedProcess(plan(), gate))
        c = app.c
        stream = self.stream_of(app)
        self.assertEqual(c.ask.task_id, 0)
        submit(c)
        (task,) = self.asks(stream)
        self.assertEqual((task.title, task.status), (COMMAND, live.STATUS_RUNNING))
        self.assertEqual(c.ask.task_id, task.id)
        first = task.steps[0]
        self.assertEqual((first.kind, first.title, first.status), (live.ASK_REQUEST, "Your request", live.STATUS_OK))
        fields = {field.label: field.value for field in first.fields}
        self.assertEqual(fields["You typed"], COMMAND)
        self.assertTrue(fields["Briefing given"].startswith("today's briefing: "))
        self.assertTrue(wait_for(lambda: any(step.kind == live.PLANNER_RUN and step.status == live.STATUS_RUNNING
                                             for step in self.asks(stream)[0].steps)))
        gate.set()
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        (task,) = self.asks(stream)
        # The Email goes to a guest you didn't type: the Ask ends CHECK (amber), not a plain DONE.
        self.assertEqual((task.status, task.summary), (live.STATUS_WARN, "2 cards, 1 recipient you didn't type"))
        kinds = [step.kind for step in task.steps]
        self.assertEqual((kinds[0], kinds[-1]), (live.ASK_REQUEST, live.ASK_CARDS))
        self.assertEqual(kinds, [live.ASK_REQUEST, live.ASK_CHECKS, live.CALENDAR_READ, live.PLANNER_RUN,
                                 live.ASK_VALIDATE, live.ASK_CARDS])
        cards = task.steps[-1]
        self.assertEqual(cards.summary, "2 cards under NEEDS YOUR OK")
        self.assertEqual([item.text for item in cards.items][0],
                         "ASK MOVE - work - Jarvis test sync (event jts0001aa) - to Fri Oct 9 2:00-3:00 PM - notify all")
        self.assertTrue(cards.items[1].text.startswith("ASK EMAIL - work - to "))
        self.assertTrue(cards.items[1].text.endswith(" - Jarvis test sync moved"))
        self.assertEqual([item.status for item in cards.items], [live.STATUS_OK, live.STATUS_WARN])
        self.assertEqual(cards.items[1].note, "1 recipient you didn't type - check the card before Send")
        answer = next(field for field in cards.fields if field.label == "Answer")
        self.assertEqual(answer.link, "tab:jarvis")
        self.assertEqual(next(field.value for field in cards.fields if field.label == "Jarvis says"), SAY)
        self.assertEqual(c.ask.last_task_id, task.id)
        self.assertTrue(all(step.status != live.STATUS_RUNNING for step in task.steps))

    def test_refused_submits_create_no_task(self) -> None:
        gate = threading.Event()
        app = self.make(GatedProcess(plan(), gate))
        c = app.c
        stream = self.stream_of(app)
        submit(c, "   ")                       # empty
        self.assertEqual(self.asks(stream), [])
        c._connect_alias = "work"              # blocked: a Google sign-in is open
        submit(c)
        self.assertEqual(self.asks(stream), [])
        c._connect_alias = None
        submit(c)
        c.ask.submit("another request")        # busy
        self.assertEqual(len(self.asks(stream)), 1)
        gate.set()
        self.assertTrue(wait_for(lambda: not c.ask.busy))

    def test_cancel_and_shutdown(self) -> None:
        gate = threading.Event()
        process = GatedProcess(plan(), gate)
        app = self.make(process)
        c, bar = app.c, self.bar(app)
        stream = self.stream_of(app)
        submit(c)
        self.assertTrue(wait_for(lambda: process.given >= 1), "the CLI is running")
        bar.button.click()
        self.assertTrue(wait_for(lambda: not c.ask.busy, 3))
        (task,) = self.asks(stream)
        self.assertEqual(task.status, live.STATUS_CANCELLED)
        run_step = next(step for step in task.steps if step.kind == live.PLANNER_RUN)
        self.assertEqual(run_step.status, live.STATUS_CANCELLED)
        self.assertIn("Cancel clicked", [note.text for note in run_step.notes])
        self.assertEqual(c.ask.last_task_id, task.id)

    def test_shutdown_during_a_run_finishes_the_task(self) -> None:
        gate = threading.Event()
        process = GatedProcess(plan(), gate)
        app = self.make(process)
        c = app.c
        stream = self.stream_of(app)
        submit(c)
        self.assertTrue(wait_for(lambda: process.given >= 1), "the CLI is running")
        c.ask.shutdown()
        c.ask.join(3)
        (task,) = self.asks(stream)
        self.assertEqual((task.status, task.summary), (live.STATUS_CANCELLED, "Jarvis closed"))
        run_step = next(step for step in task.steps if step.kind == live.PLANNER_RUN)
        self.assertEqual((run_step.status, run_step.summary), (live.STATUS_CANCELLED, "Jarvis closed"))

    def test_a_refusal_and_a_failure_finish_the_task(self) -> None:
        app = self.make(auth=AUTH_NONE)
        c = app.c
        stream = self.stream_of(app)
        submit(c)
        self.assertTrue(wait_for(lambda: not c.ask.busy))
        (task,) = self.asks(stream)
        self.assertEqual(task.status, live.STATUS_BLOCKED)
        self.assertEqual([step.kind for step in task.steps], [live.ASK_REQUEST, live.ASK_CHECKS])
        clock = FakeClock()
        timeout = self.make(FakeProcess(stream_lines("success")[:1], clock=clock, hang=True), engine_clock=clock)
        stream = self.stream_of(timeout)
        submit(timeout.c)
        self.assertTrue(wait_for(lambda: not timeout.c.ask.busy, 10))
        (task,) = self.asks(stream)
        self.assertEqual((task.status, task.summary), (live.STATUS_FAILED, "Took too long; nothing was proposed"))
        run_step = next(step for step in task.steps if step.kind == live.PLANNER_RUN)
        self.assertEqual((run_step.status, run_step.summary),
                         (live.STATUS_FAILED, "Took too long; nothing was proposed"))


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()
