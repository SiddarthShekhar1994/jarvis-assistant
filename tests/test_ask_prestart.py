"""Claude Code started ahead of the question (ask.prestart): one idle process per exact command line, used once and
replaced, refused when its command line, environment or folder would differ, recycled, killed at shutdown, never started
while Ask is off or with the real runner under the tests' guard; a run on one that dies falls back to cold without a
second count; the caps count at the send. Also what an Ask reads ahead (the sign-in check alongside the context, the
calendars at most RECENT_READ_S old and never past a change Jarvis made), the per-Ask log line, and web research being
offered on a default config.

Fake processes only, except one test of the real runner's stdin with a Python child playing the CLI: claude.exe is never
started.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from briefing_reader.ask import cli, planner, prestart
from briefing_reader.ask.modes import MODE_DESKTOP, MODE_PLAN
from briefing_reader.ask.planner import AskPlanner, ClaudeEngine, GoogleSources
from briefing_reader.ask.prestart import SLOT_ASK, SLOT_DESKTOP, Prestarter
from briefing_reader.ask.stream import START_AHEAD, START_COLD, START_FELL_BACK
from briefing_reader.ask.usage import UsageLog
from briefing_reader.config import PROJECT_ROOT, load_config
from briefing_reader.gcal import EventBrief, GoogleCalendar
from tests.ask_fakes import (
    HELP_TEXT,
    NOW,
    FakeProcess,
    FakeRunner,
    init_event,
    plan_stream,
    result_event,
    stream,
    words_stream,
)
from tests.test_ask_planner import MOVE_LINE, PlannerTestCase

SAY = "Right, sir. Friday has your Project sync at two."
PLAN = {"say": SAY, "question": "", "lines": [MOVE_LINE]}


class TickClock:
    """A clock tests move by hand."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class PoolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.prompt_file = self.root / "prompt.md"
        self.settings = self.root / "settings.json"
        self.config_file = self.root / "config.toml"
        for path in (self.prompt_file, self.settings, self.config_file):
            path.write_text("x", encoding="ascii")
        self.exe = self.root / "claude.exe"
        self.exe.write_bytes(b"MZ")
        self.argv = [str(self.exe), "-p", cli.INPUT_FORMAT_FLAG, "stream-json", "--system-prompt-file",
                     str(self.prompt_file), "--settings", str(self.settings)]
        self.env = {"PATH": "C:/Windows"}
        self.cwd = self.root / "ask"
        self.clock = TickClock()
        self.processes = [FakeProcess([]) for _ in range(12)]
        self.runner = FakeRunner(*self.processes)
        self.pool = Prestarter(self.runner, clock=self.clock, extra_files=(self.config_file,))

    def fill(self, slot: str = SLOT_ASK, **kwargs: Any) -> bool:
        return self.pool.fill(slot, self.argv, self.env, self.cwd, **kwargs)

    def take(self, slot: str = SLOT_ASK) -> Any:
        return self.pool.take(slot, self.argv, self.env, self.cwd)


class PoolTests(PoolTestCase):
    def test_it_starts_one_with_nothing_sent(self) -> None:
        self.assertTrue(self.fill())
        started = self.runner.started[0]
        self.assertEqual((started.argv, started.env, started.cwd, started.raw), (self.argv, self.env, self.cwd, ""))
        self.assertEqual(self.processes[0].sent, [])
        self.assertTrue(self.fill())                               # the same command line: it stays the one
        self.assertEqual(len(self.runner.started), 1)
        self.assertTrue(self.pool.holds(SLOT_ASK) and self.pool.wants(SLOT_ASK))

    def test_used_once(self) -> None:
        self.fill()
        self.assertIs(self.take(), self.processes[0])
        self.assertFalse(self.pool.holds(SLOT_ASK))
        self.assertIsNone(self.take())                             # one question, one process, one use
        self.assertFalse(self.processes[0].killed)                 # it is the run's now

    def test_nothing_to_take_is_a_cold_run(self) -> None:
        self.assertIsNone(self.take())
        self.assertEqual(self.runner.started, [])

    def test_a_process_is_refused_when_anything_would_differ_from_the_cold_path(self) -> None:
        cases = {"argv": (self.argv + ["--more"], self.env, self.cwd),
                 "env": (self.argv, {**self.env, "TEMP": "C:/other"}, self.cwd),
                 "cwd": (self.argv, self.env, self.root / "elsewhere")}
        for name, (argv, env, cwd) in cases.items():
            with self.subTest(differs=name), self.assertLogs("briefing_reader.ask.prestart", level="INFO") as logs:
                self.fill()
                process = self.runner.processes[-1]
                self.assertIsNone(self.pool.take(SLOT_ASK, argv, env, cwd))
                self.assertTrue(process.killed and process.closed)
                self.assertFalse(self.pool.holds(SLOT_ASK))
                self.pool.fill(SLOT_ASK, self.argv, self.env, self.cwd)
                self.assertIsNot(self.take(), process)
                text = "\n".join(logs.output)
                self.assertNotIn("C:/", text)                      # no path or value in the log
                self.assertNotIn(str(self.root), text)

    def test_another_command_line_replaces_the_one_waiting(self) -> None:
        self.fill()
        first = self.processes[0]
        self.pool.fill(SLOT_ASK, self.argv + ["--schema-b"], self.env, self.cwd)
        self.assertTrue(first.killed)
        self.assertEqual(len(self.runner.started), 2)
        self.assertIs(self.pool.take(SLOT_ASK, self.argv + ["--schema-b"], self.env, self.cwd), self.processes[1])

    def test_slots_keep_their_own(self) -> None:
        self.fill()
        self.fill(SLOT_DESKTOP)
        self.assertIs(self.take(SLOT_DESKTOP), self.processes[1])
        self.assertIs(self.take(SLOT_ASK), self.processes[0])

    def test_a_dead_process_is_not_taken(self) -> None:
        self.fill()
        self.processes[0].killed = True
        self.assertIsNone(self.take())

    def test_recycled_after_30_minutes_idle(self) -> None:
        self.fill()
        self.clock.t += 29 * 60
        self.pool.sweep()
        self.assertFalse(self.processes[0].killed)
        self.clock.t += 61
        self.pool.sweep()
        self.assertTrue(self.processes[0].killed and self.processes[0].closed)
        self.assertIs(self.take(), self.processes[1])              # a fresh one at once

    def test_too_old_at_the_moment_of_the_question_it_is_refused(self) -> None:
        self.fill()
        self.clock.t += prestart.IDLE_LIMIT_S + 1
        self.assertIsNone(self.take())
        self.assertTrue(self.processes[0].killed)

    def test_recycled_when_a_file_it_read_changes(self) -> None:
        for path in (self.prompt_file, self.settings, self.exe, self.config_file):
            with self.subTest(file=path.name):
                self.fill()
                process = self.runner.processes[-1]
                self.pool.sweep()
                self.assertFalse(process.killed)
                path.write_text("changed " * 3, encoding="ascii")
                self.pool.sweep()
                self.assertTrue(process.killed)
                self.assertTrue(self.pool.holds(SLOT_ASK))         # the next one read the new file
                self.assertIsNotNone(self.take())

    def test_a_changed_file_refuses_it_at_the_question_too(self) -> None:
        self.fill()
        self.prompt_file.write_text("a new prompt", encoding="ascii")
        self.assertIsNone(self.take())
        self.assertTrue(self.processes[0].killed)

    def test_a_process_that_died_idle_is_replaced_but_not_forever(self) -> None:
        self.fill()
        for count in range(prestart.MAX_DEATHS):
            self.runner.processes[-1].killed = True
            before = len(self.runner.started)
            self.pool.sweep()
            self.assertEqual(len(self.runner.started) - before, 1 if count < prestart.MAX_DEATHS - 1 else 0, count)
        self.pool.sweep()
        self.assertFalse(self.pool.holds(SLOT_ASK))                # it keeps dying: left alone ...
        self.fill(used=True)                                       # ... until a real run asks again
        self.assertTrue(self.pool.holds(SLOT_ASK))

    def test_sweep_starts_what_a_slot_wants_and_nothing_else(self) -> None:
        self.pool.sweep()
        self.assertEqual(self.runner.started, [])
        self.fill()
        self.take()
        self.pool.sweep()
        self.assertTrue(self.pool.holds(SLOT_ASK))

    def test_killed_at_shutdown_and_nothing_starts_after_it(self) -> None:
        self.fill()
        self.fill(SLOT_DESKTOP)
        self.pool.shutdown()
        self.assertTrue(all(process.killed and process.closed for process in self.processes[:2]))
        self.assertFalse(self.pool.holds(SLOT_ASK))
        self.assertFalse(self.fill())
        self.pool.sweep()
        self.assertEqual(len(self.runner.started), 2)

    def test_a_slot_can_be_forgotten(self) -> None:
        self.fill(SLOT_DESKTOP)
        self.fill()
        self.pool.forget(SLOT_DESKTOP)
        self.assertTrue(self.processes[0].killed)
        self.assertFalse(self.pool.wants(SLOT_DESKTOP))
        self.pool.sweep()
        self.assertFalse(self.pool.holds(SLOT_DESKTOP))            # the sweep does not bring it back
        self.assertTrue(self.pool.holds(SLOT_ASK))                 # the other slot keeps its own
        self.assertTrue(self.fill(SLOT_DESKTOP, used=True))        # a later run asks again

    def test_a_start_that_fails_leaves_the_cold_path(self) -> None:
        class Broken(FakeRunner):
            def start(self, argv: Any, *, stdin_text: str, env: Any, cwd: Any) -> Any:
                raise FileNotFoundError("gone")

        pool = Prestarter(Broken(), clock=self.clock)
        with self.assertLogs("briefing_reader.ask.prestart", level="WARNING"):
            self.assertFalse(pool.fill(SLOT_ASK, self.argv, self.env, self.cwd))
        self.assertIsNone(pool.take(SLOT_ASK, self.argv, self.env, self.cwd))

    def test_no_real_process_under_the_tests_guard(self) -> None:
        self.assertEqual(os.environ.get("JARVIS_DESKTOP_TEST_GUARD"), "1")
        pool = Prestarter(cli.SubprocessRunner(), clock=self.clock)
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("a real process")) as popen:
            self.assertFalse(pool.fill(SLOT_ASK, self.argv, self.env, self.cwd))
            pool.sweep()
        popen.assert_not_called()
        self.assertFalse(pool.holds(SLOT_ASK))

    def test_a_file_list_from_the_command_line(self) -> None:
        self.assertEqual(prestart.watched_files(self.argv), [self.exe, self.prompt_file, self.settings])


# --------------------------------------------------------------------------
# The real runner's stdin, with a Python child playing the CLI (never claude.exe)
# --------------------------------------------------------------------------

CHILD = r"""
import json, sys
sys.stdout.write(json.dumps({"type": "system", "subtype": "started"}) + "\n")
sys.stdout.flush()
for line in sys.stdin:
    event = json.loads(line)
    sys.stdout.write(json.dumps({"type": "result", "length": len(event["message"]["content"])}) + "\n")
    sys.stdout.flush()
sys.exit(7)
"""


class RealRunnerStdinTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.script = self.root / "fake_cli.py"
        self.script.write_text(CHILD, encoding="ascii")

    def start(self, stdin_text: str, *flags: str) -> cli.SubprocessProcess:
        process = cli.SubprocessRunner().start([sys.executable, "-B", str(self.script), *flags], stdin_text=stdin_text,
                                               env=dict(os.environ), cwd=self.root)
        self.addCleanup(process.close)
        return process

    def read(self, process: cli.SubprocessProcess) -> dict:
        for _ in range(200):
            line = process.read_line(0.1)
            if line:
                return json.loads(line)
            self.assertIsNotNone(line, "it ended early")
        self.fail("no line")

    def test_it_waits_with_stdin_open_until_it_is_sent_something(self) -> None:
        process = self.start("", cli.INPUT_FORMAT_FLAG, "stream-json")
        self.assertEqual(self.read(process)["subtype"], "started")
        self.assertTrue(process.alive())
        self.assertEqual(process.read_line(0.2), "")               # idle: nothing more until it is asked
        self.assertTrue(process.send(cli.stream_message("twelve chars")))
        self.assertEqual(self.read(process), {"type": "result", "length": 12})
        self.assertTrue(process.alive())                           # it holds on until stdin closes
        process.close_stdin()
        self.assertEqual(process.wait(10), 7)
        self.assertFalse(process.alive())
        self.assertFalse(process.send("too late"))

    def test_the_cold_path_writes_the_message_and_holds_stdin(self) -> None:
        process = self.start(cli.stream_message("abc"), cli.INPUT_FORMAT_FLAG, "stream-json")
        self.assertEqual(self.read(process)["subtype"], "started")
        self.assertEqual(self.read(process)["length"], 3)
        self.assertTrue(process.alive())
        process.close_stdin()
        self.assertEqual(process.wait(10), 7)

    def test_a_plain_command_line_gets_its_end_of_input_at_once(self) -> None:
        process = self.start("")
        self.assertEqual(self.read(process)["subtype"], "started")
        self.assertEqual(process.wait(10), 7)                      # no message: it read the end of its stdin

    def test_killing_it_while_it_idles_ends_it(self) -> None:
        process = self.start("", cli.INPUT_FORMAT_FLAG, "stream-json")
        self.read(process)
        process.kill()
        self.assertIsNotNone(process.wait(10))
        self.assertFalse(process.alive())


# --------------------------------------------------------------------------
# The engine and the planner with the seam
# --------------------------------------------------------------------------

class SeamTestCase(PlannerTestCase):
    def make_ahead(self, *runs: Any, auth: str | None = None, help_text: str | None = None) -> AskPlanner:
        ask = self.make(*runs, auth=auth)
        if help_text is not None:
            self.runner.help_text = help_text
        self.pool = Prestarter(self.runner, clock=self.clock)
        self.engine.prestart = self.pool
        self.addCleanup(self.pool.shutdown)
        return ask

    def counted(self) -> int:
        caps = self.usage.check()
        return self.config.ask.max_per_hour - caps.left_hour


class FirstRunTests(SeamTestCase):
    def test_used_once_and_replaced_right_after_the_send(self) -> None:
        first = FakeProcess(words_stream(PLAN))
        second = FakeProcess(words_stream(PLAN))
        ask = self.make_ahead(first, second, FakeProcess([]))
        ask.prestart_ask()
        self.assertEqual(len(self.runner.started), 1)
        self.assertEqual(self.runner.started[0].raw, "")           # nothing sent: no model request
        self.assertEqual(first.sent, [])
        self.assertEqual(self.counted(), 0)                        # the caps count at the send, not the start
        outcome = ask.plan("what's on Friday?")
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(self.counted(), 1)
        self.assertEqual(len(first.sent), 1)
        event = json.loads(first.sent[0])
        self.assertEqual(event["message"]["content"], self.runner.started[0].stdin)
        self.assertIn("what's on Friday?", event["message"]["content"])
        self.assertNotIn("what's on Friday?", " ".join(self.runner.started[0].argv))
        self.assertEqual(len(self.runner.started), 2)              # its replacement is waiting
        self.assertEqual(self.runner.started[1].argv, self.runner.started[0].argv)
        self.assertEqual(second.sent, [])
        self.assertTrue(first.closed and first.stdin_closed)
        outcome = ask.plan("and Saturday?")
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(len(first.sent), 1)                       # never reused
        self.assertEqual(len(second.sent), 1)
        self.assertEqual(self.counted(), 2)
        self.assertEqual(len(self.runner.started), 3)

    def test_the_process_is_started_as_the_cold_path_would(self) -> None:
        ask = self.make_ahead(FakeProcess(words_stream(PLAN)), FakeProcess([]))
        ask.prestart_ask()
        ahead = self.runner.started[0]
        self.assertTrue(ask.plan("what's on Friday?").ok)
        ready = self.engine.readiness()
        self.assertEqual(ahead.env, cli.clean_env(self.environ))
        self.assertNotIn("ANTHROPIC_API_KEY", ahead.env)
        self.assertEqual(ahead.cwd, self.engine.work_folder)
        # The most used command line: mail may be searched (work's mail can be read), the web may be asked.
        self.assertEqual(ahead.argv, self.engine.argv(ready, allow_search=True, allow_web=True))

    def test_a_miss_runs_cold_and_the_next_one_is_that_command_line(self) -> None:
        first, cold, again = FakeProcess([]), FakeProcess(words_stream(PLAN)), FakeProcess(words_stream(PLAN))
        ask = self.make_ahead(first, cold, again, FakeProcess([]))
        ask.prestart_ask()
        self.sources.accounts_list[1] = self.sources.accounts_list[0]   # no account's mail can be read now
        result = []
        original = self.engine.run

        def run(*args: Any, **kwargs: Any) -> Any:
            result.append(original(*args, **kwargs))
            return result[-1]

        self.engine.run = run   # type: ignore[method-assign]
        self.assertTrue(ask.plan("what's on Friday?").ok)
        self.assertEqual(result[-1].start, START_COLD)
        self.assertTrue(first.killed)                              # another command line: let go
        self.assertEqual(len(cold.sent), 0)                        # it got its prompt as it started
        self.assertEqual(self.runner.started[1].stdin.count("<command>"), 1)
        self.assertEqual(self.runner.started[2].argv, self.runner.started[1].argv)   # that one waits now
        self.assertTrue(ask.plan("and Saturday?").ok)
        self.assertEqual(result[-1].start, START_AHEAD)
        self.assertEqual(len(again.sent), 1)
        self.assertEqual(self.counted(), 2)

    def test_an_environment_that_differs_is_refused(self) -> None:
        first, cold = FakeProcess([]), FakeProcess(words_stream(PLAN))
        ask = self.make_ahead(first, cold, FakeProcess([]))
        ask.prestart_ask()
        self.environ["PATH"] = "C:/Windows;C:/Other"
        self.assertTrue(ask.plan("what's on Friday?").ok)
        self.assertTrue(first.killed)
        self.assertEqual(first.sent, [])
        self.assertEqual(self.runner.started[1].env["PATH"], "C:/Windows;C:/Other")

    def test_a_folder_that_differs_is_refused(self) -> None:
        first, cold = FakeProcess([]), FakeProcess(words_stream(PLAN))
        ask = self.make_ahead(first, cold, FakeProcess([]))
        ask.prestart_ask()
        self.engine.work_folder = self.engine.work_folder.parent / "elsewhere" / "ask"
        self.assertTrue(ask.plan("what's on Friday?").ok)
        self.assertTrue(first.killed)
        self.assertEqual(first.sent, [])

    def test_dead_while_idle_runs_cold_with_one_count(self) -> None:
        first, cold = FakeProcess([]), FakeProcess(words_stream(PLAN))
        ask = self.make_ahead(first, cold, FakeProcess([]))
        ask.prestart_ask()
        first.killed = True
        self.assertTrue(ask.plan("what's on Friday?").ok)
        self.assertEqual(self.counted(), 1)
        self.assertEqual(first.sent, [])

    def test_a_process_that_dies_before_its_first_event_runs_cold_with_one_count(self) -> None:
        dying, cold, spare = FakeProcess([]), FakeProcess(words_stream(PLAN)), FakeProcess([])
        ask = self.make_ahead(dying, spare, cold)
        ask.prestart_ask()
        with self.assertLogs("briefing_reader.ask.planner", level="INFO") as logs:
            outcome = ask.plan("what's on Friday?")
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual((outcome.runs, self.counted()), (1, 1))   # one question, one run counted
        self.assertEqual(len(dying.sent), 1)
        self.assertIn("what's on Friday?", self.runner.started[2].stdin)
        self.assertIn("running cold", "\n".join(logs.output))
        self.assertIn(START_FELL_BACK, "\n".join(logs.output))

    def test_only_its_start_event_then_gone_runs_cold_too(self) -> None:
        dying, spare, cold = FakeProcess(stream("success")[:1]), FakeProcess([]), FakeProcess(words_stream(PLAN))
        ask = self.make_ahead(dying, spare, cold)
        ask.prestart_ask()
        self.assertTrue(ask.plan("what's on Friday?").ok)
        self.assertEqual(self.counted(), 1)

    def test_one_that_never_says_it_started_runs_cold(self) -> None:
        stuck, spare, cold = FakeProcess([], clock=self.clock, hang=True), FakeProcess([]), FakeProcess(words_stream(PLAN))
        ask = self.make_ahead(stuck, spare, cold)
        ask.prestart_ask()
        self.assertTrue(ask.plan("what's on Friday?").ok)
        self.assertTrue(stuck.killed)
        self.assertEqual(self.counted(), 1)

    def test_a_stale_sign_in_in_an_idle_process_runs_cold(self) -> None:
        refused = FakeProcess([json.dumps(result_event(is_error=True, result="Invalid API key - Please run /login"))])
        spare, cold = FakeProcess([]), FakeProcess(words_stream(PLAN))
        ask = self.make_ahead(refused, spare, cold)
        ask.prestart_ask()
        self.assertTrue(ask.plan("what's on Friday?").ok)
        self.assertEqual(self.counted(), 1)

    def test_a_process_that_began_to_answer_is_never_run_again(self) -> None:
        lines = words_stream(PLAN)[:8]
        broken, spare = FakeProcess(lines), FakeProcess([])
        ask = self.make_ahead(broken, spare)
        ask.prestart_ask()
        outcome = ask.plan("what's on Friday?")
        self.assertFalse(outcome.ok)
        self.assertEqual(len(self.runner.started), 2)              # no cold run
        self.assertEqual(self.counted(), 1)

    def test_a_guard_trip_is_never_run_again(self) -> None:
        lines = [json.dumps(init_event(tools=["StructuredOutput", "Bash"])), *words_stream(PLAN)[1:]]
        tripped, spare = FakeProcess(lines), FakeProcess([])
        ask = self.make_ahead(tripped, spare)
        ask.prestart_ask()
        outcome = ask.plan("what's on Friday?")
        self.assertEqual((outcome.ok, outcome.kind), (False, "guard"))
        self.assertTrue(tripped.killed)
        self.assertEqual(len(self.runner.started), 2)

    def test_a_cancel_runs_nothing_cold(self) -> None:
        cancel = threading.Event()
        process = FakeProcess([], cancel=cancel, cancel_after=0, clock=self.clock, hang=True)
        ask = self.make_ahead(process, FakeProcess([]))
        ask.prestart_ask()
        outcome = ask.plan("what's on Friday?", cancel=cancel)
        self.assertEqual(outcome.kind, "cancelled")
        self.assertEqual(len(self.runner.started), 2)

    def test_the_caps_refuse_at_the_send_whatever_waits(self) -> None:
        ask = self.make_ahead(FakeProcess([]))
        ask.prestart_ask()
        for _ in range(self.config.ask.max_per_hour):
            self.usage.finish(self.usage.start(), outcome="ok")
        waiting = self.runner.processes[0]
        outcome = ask.plan("what's on Friday?")
        self.assertEqual(outcome.kind, planner.CAPS)
        self.assertEqual(waiting.sent, [])
        self.assertEqual(len(self.runner.started), 1)

    def test_the_second_run_starts_cold_and_leaves_the_slot_alone(self) -> None:
        self.reader.found = ["thr0000777"]
        search = {"say": "Let me look for that email, sir.", "lines": [],
                  "gmail_search": {"account": "work", "query": "from:ana@example.edu subject:budget", "why": "the budget"}}
        first, spare = FakeProcess(words_stream(search)), FakeProcess([])
        second = FakeProcess(words_stream({"say": "Ana wants it confirmed by Friday, sir.", "lines": []}))
        ask = self.make_ahead(first, spare, second)
        ask.prestart_ask()
        slot_argv = self.runner.started[0].argv
        outcome = ask.plan("what did Ana say in her budget email?")
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual((outcome.runs, self.counted()), (2, 2))
        self.assertEqual(self.runner.started[1].argv, slot_argv)   # the replacement is the first run's command line
        self.assertNotEqual(self.runner.started[2].argv, slot_argv)
        self.assertIn("<mail>", self.runner.started[2].stdin)
        self.assertTrue(self.pool.holds(SLOT_ASK))
        self.assertEqual(spare.sent, [])
        self.assertEqual(second.sent, [])

    def test_not_started_while_ask_is_off_or_not_ready(self) -> None:
        ask = self.make_ahead()
        off = load_config(self.root, environ={"NOTION_TOKEN": "", "LOCALAPPDATA": str(self.root / "local"),
                                              "JARVIS_ASK_ENABLED": "false"})
        ask.config = off
        ask.prestart_ask()
        ask.prestart_sweep()
        ask.read_ahead()
        self.assertEqual(self.runner.started, [])
        self.assertFalse(self.pool.wants(SLOT_ASK))

    def test_not_started_when_not_signed_in(self) -> None:
        auth = json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"})
        ask = self.make_ahead(auth=auth)
        ask.prestart_ask()
        self.assertEqual(self.runner.started, [])

    def test_without_the_seam_nothing_is_started_ahead(self) -> None:
        ask = self.make(words_stream(PLAN))
        ask.prestart_ask()
        ask.prestart_sweep()
        ask.prestart_shutdown()
        self.assertEqual(self.runner.started, [])
        self.assertTrue(ask.plan("what's on Friday?").ok)

    def test_a_claude_code_without_the_flags_runs_as_before(self) -> None:
        older = HELP_TEXT.replace("--include-partial-messages", "--include-partial-message-x")
        cold = FakeProcess(plan_stream(PLAN))
        ask = self.make_ahead(cold, help_text=older)
        ask.prestart_ask()
        self.assertEqual(self.runner.started, [])                  # no process can wait for a question
        heard: list[str] = []
        outcome = ask.plan("what's on Friday?", on_words=lambda *args: heard.append(args[1]))
        self.assertTrue(outcome.ok, outcome)
        started = self.runner.started[0]
        self.assertNotIn(cli.INPUT_FORMAT_FLAG, started.argv)
        self.assertNotIn("--include-partial-messages", started.argv)
        self.assertEqual(started.raw, started.stdin)               # plain text, as before
        self.assertEqual(heard, [])

    def test_prestart_sweep_waits_for_a_running_request(self) -> None:
        ask = self.make_ahead(FakeProcess([]))
        ask.prestart_ask()
        with ask._busy:
            self.clock.t += prestart.IDLE_LIMIT_S + 1
            ask.prestart_sweep()
            self.assertTrue(self.pool.holds(SLOT_ASK))
            self.assertFalse(self.runner.processes[0].killed)

    def test_shutdown_kills_what_waits(self) -> None:
        ask = self.make_ahead(FakeProcess([]))
        ask.prestart_ask()
        ask.prestart_shutdown()
        self.assertTrue(self.runner.processes[0].killed and self.runner.processes[0].closed)
        ask.prestart_ask()
        self.assertEqual(len(self.runner.started), 1)


class DesktopSlotTests(SeamTestCase):
    def test_its_first_run_is_cold_and_the_next_one_is_started_ahead(self) -> None:
        answer = {"say": "x", "question": "", "steps": []}
        first, waiting = FakeProcess(words_stream(answer)), FakeProcess(words_stream(answer))
        self.make_ahead(first, waiting, FakeProcess([]))
        self.engine.readiness()
        result = self.engine.run_mode("<command>\nx\n</command>\n", MODE_DESKTOP)
        self.assertEqual(result.start, START_COLD)
        self.assertEqual(self.runner.started[0].stdin, "<command>\nx\n</command>\n")
        self.assertIn(cli.INPUT_FORMAT_FLAG, self.runner.started[0].argv)
        self.assertTrue(self.pool.holds(SLOT_DESKTOP))
        self.assertFalse(self.pool.holds(SLOT_ASK))                # a desktop request never fills Ask's slot
        result = self.engine.run_mode("<command>\ny\n</command>\n", MODE_DESKTOP)
        self.assertEqual(result.start, START_AHEAD)
        self.assertEqual(len(waiting.sent), 1)

    def test_the_other_modes_keep_plain_stdin_and_no_slot(self) -> None:
        cold = FakeProcess(stream("plan_ok"))
        self.make_ahead(cold)
        self.engine.readiness()
        result = self.engine.run_mode("the plan's stdin", MODE_PLAN)
        started = self.runner.started[0]
        self.assertEqual(started.raw, "the plan's stdin")
        self.assertNotIn(cli.INPUT_FORMAT_FLAG, started.argv)
        self.assertEqual(result.start, "")
        self.assertFalse(self.pool.wants(SLOT_DESKTOP) or self.pool.wants(SLOT_ASK))


class WordsAndLogTests(SeamTestCase):
    def test_each_runs_words_and_whether_it_had_mail(self) -> None:
        self.reader.found = ["thr0000777"]
        search = {"say": "Let me look for that email, sir.", "lines": [],
                  "gmail_search": {"account": "work", "query": "from:ana@example.edu subject:budget", "why": "the budget"}}
        ask = self.make(words_stream(search), words_stream({"say": "Ana wants it by Friday, sir.", "lines": []}))
        heard: list[tuple[int, str, bool, bool]] = []
        outcome = ask.plan("what did Ana say in her budget email?",
                           on_words=lambda number, text, complete, mail: heard.append((number, text, complete, mail)))
        self.assertTrue(outcome.ok, outcome)
        self.assertIn((1, "Let me look for that email, sir.", True, False), heard)
        self.assertIn((2, "Ana wants it by Friday, sir.", True, True), heard)

    def test_a_mode_or_web_research_has_no_words(self) -> None:
        ask = self.make(stream("plan_ok"))
        self.engine.readiness()
        result = self.engine.run_mode("the plan's stdin", MODE_PLAN)
        self.assertIsNone(result.first_words_s)
        self.assertIs(ask.streams_words, True)

    def test_a_broken_on_words_changes_nothing(self) -> None:
        ask = self.make(words_stream(PLAN))

        def broken(*_args: Any) -> None:
            raise RuntimeError("broken")

        outcome = ask.plan("what's on Friday?", on_words=broken)
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.say, SAY)

    def test_one_log_line_per_ask_with_times_and_never_text(self) -> None:
        ask = self.make_ahead(FakeProcess(words_stream(PLAN)), FakeProcess([]))
        ask.prestart_ask()
        with self.assertLogs("briefing_reader", level="DEBUG") as logs:
            outcome = ask.plan("what's on Friday?", on_words=lambda *_args: None)
        self.assertTrue(outcome.ok, outcome)
        lines = [line for line in logs.output if "first words" in line]
        self.assertEqual(len(lines), 1, logs.output)
        self.assertRegex(lines[0], r"first words \d+\.\d\d s, say complete \d+\.\d\d s, prestarted \(context ")
        self.assertIn("calendars", lines[0])
        self.assertIn("sign-in check", lines[0])
        text = "\n".join(logs.output)
        for secret in (SAY, "what's on Friday", "Project sync", "Friday has", "Right, sir"):
            self.assertNotIn(secret, text)

    def test_the_log_line_says_cold_and_no_words(self) -> None:
        ask = self.make(plan_stream(PLAN))
        with self.assertLogs("briefing_reader.ask.planner", level="INFO") as logs:
            self.assertTrue(ask.plan("what's on Friday?", on_words=lambda *_args: None).ok)
        line = next(line for line in logs.output if "first words" in line)
        self.assertIn("first words -, say complete -, cold", line)

    def test_no_run_no_log_line(self) -> None:
        ask = self.make()
        for _ in range(self.config.ask.max_per_hour):
            self.usage.finish(self.usage.start(), outcome="ok")
        with self.assertLogs("briefing_reader.ask.planner", level="INFO") as logs:
            self.assertEqual(ask.plan("what's on Friday?").kind, planner.CAPS)
        self.assertFalse(any("first words" in line for line in logs.output))


class WebResearchOfferedTests(PlannerTestCase):
    """Is web_research really in the first run's schema on a default config?"""

    def default_config(self) -> Any:
        return load_config(PROJECT_ROOT, environ={"NOTION_TOKEN": "", "LOCALAPPDATA": str(self.root / "local"),
                                                  "JARVIS_ASK_ENABLED": "true"})

    def test_on_the_tracked_config_with_ask_on_the_first_run_is_offered_web_research(self) -> None:
        self.config = self.default_config()
        self.assertTrue(self.config.research.enabled and self.config.research.planner_may_ask)
        ask = self.make(plan_stream(PLAN))
        self.assertEqual(ask._web_allowed(), (True, "yes"))
        with self.assertLogs("briefing_reader.ask.planner", level="INFO") as logs:
            self.assertTrue(ask.plan("what time does the museum open?").ok)
        self.assertIn("web_research", self.schema()["properties"])
        self.assertIn("run 1 may ask for: mail search yes, web research yes", "\n".join(logs.output))

    def test_what_takes_it_away_is_said(self) -> None:
        self.config = self.default_config()
        ask = self.make(plan_stream(PLAN))
        folder = self.engine.work_folder.parent / cli.RESEARCH_WORK_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "left-over").write_text("x", encoding="ascii")   # the research folder must stay empty
        allowed, words = ask._web_allowed()
        self.assertFalse(allowed)
        self.assertIn("must be empty", words)
        with self.assertLogs("briefing_reader.ask.planner", level="INFO") as logs:
            self.assertTrue(ask.plan("what time does the museum open?").ok)
        self.assertNotIn("web_research", self.schema()["properties"])
        self.assertIn("web research no", "\n".join(logs.output))

    def test_the_prompt_says_to_use_it_and_never_to_guess(self) -> None:
        prompt = cli.PROMPT_FILE.read_bytes().decode("ascii")
        say = " ".join(prompt.split("\nSAY\n")[1].split("\n\n")[0].split())
        self.assertIn("Answer facts only from the context.", say)
        self.assertIn("A fact not in the context that can change (news, prices, opening hours, people's jobs, anything "
                      "recent): use web_research when it is in the schema; otherwise say plainly you can't see or "
                      "check that.", say)
        self.assertIn("Never guess or fill in from memory, and never present a guess as fact.", say)
        self.assertIn("Things that don't change may be answered briefly.", say)
        self.assertIn("One or two short sentences, up to four when the question needs it, spoken aloud.", say)
        schema = json.loads(cli.schema_text(allow_search=True, allow_web=True))
        self.assertIn("up to four when the question needs it", schema["properties"]["say"]["description"])


# --------------------------------------------------------------------------
# Through real pipes, with a Python child playing the CLI (never claude.exe)
# --------------------------------------------------------------------------

PLAYED_CLI = r"""
import sys
lines_file = sys.argv[1]
if lines_file == "die":
    sys.exit(3)
for message in sys.stdin:           # idle until it is sent something; one question, then its stdin closes
    with open(lines_file, encoding="ascii") as lines:
        for line in lines:
            sys.stdout.write(line)
            sys.stdout.flush()
"""


class PlayedRunner:
    """The real runner's processes, each a Python child that plays ``lines`` once it is asked (``modes``: "die" for a
    child that ends at once). Not a cli.SubprocessRunner, so it is not the guard's business: no claude.exe here."""

    def __init__(self, script: Path, lines_file: Path, modes: list[str] | None = None) -> None:
        self.real = cli.SubprocessRunner()
        self.script, self.lines_file = script, lines_file
        self.modes = list(modes or [])
        self.started: list[cli.SubprocessProcess] = []
        self.quick = FakeRunner()

    def start(self, argv: Any, *, stdin_text: str, env: Any, cwd: Any) -> cli.SubprocessProcess:
        mode = self.modes.pop(0) if self.modes else str(self.lines_file)
        child = [sys.executable, "-B", str(self.script), mode, *list(argv)[1:]]
        process = self.real.start(child, stdin_text=stdin_text, env=env, cwd=cwd)
        self.started.append(process)
        return process

    def run(self, argv: Any, *, env: Any, cwd: Any, timeout: float) -> cli.Completed:
        return self.quick.run(argv, env=env, cwd=cwd, timeout=timeout)   # --version, --help, auth status: as the fake


class ThroughPipesTests(PlannerTestCase):
    def play(self, *, modes: list[str] | None = None, prestarting: bool = True) -> AskPlanner:
        script = self.root / "played_cli.py"
        script.write_text(PLAYED_CLI, encoding="ascii")
        lines_file = self.root / "played_lines.jsonl"
        lines_file.write_text("\n".join(words_stream(PLAN, size=9)) + "\n", encoding="ascii")
        self.environ["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "C:\\Windows")   # what Python needs to start
        self.played = PlayedRunner(script, lines_file, modes)
        pool = Prestarter(self.played) if prestarting else None
        self.engine = ClaudeEngine(self.config.ask, self.config.data_dir, runner=self.played, environ=self.environ,
                                   locate=lambda environ: planner.cli.ClaudeExe(self.exe.path, "standalone install"),
                                   prestart=pool)
        self.usage = UsageLog(self.config.data_dir / "ask_usage.json", max_per_hour=self.config.ask.max_per_hour,
                              max_per_day=self.config.ask.max_per_day, clock=lambda: NOW)
        ask = AskPlanner(self.config, self.engine, self.sources, usage=self.usage, clock=lambda: NOW)
        self.addCleanup(ask.prestart_shutdown)
        self.addCleanup(lambda: [process.close() for process in self.played.started])
        return ask

    def ask_it(self, ask: AskPlanner) -> tuple[Any, list[tuple[str, bool]]]:
        heard: list[tuple[str, bool]] = []
        outcome = ask.plan("what's on Friday?", on_words=lambda _n, text, complete, _m: heard.append((text, complete)))
        return outcome, heard

    def test_started_ahead_it_waits_answers_ends_and_is_replaced(self) -> None:
        ask = self.play()
        ask.prestart_ask()
        idle = self.played.started[0]
        self.assertTrue(idle.alive())
        self.assertEqual(idle.read_line(0.15), "")                 # nothing until it is asked
        outcome, heard = self.ask_it(ask)
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.say, SAY)
        self.assertEqual(heard[-1], (SAY, True))
        self.assertGreater(len(heard), 3)                          # a few words at a time, through the real pipe
        self.assertEqual(outcome.runs, 1)
        self.assertEqual(self.config.ask.max_per_hour - self.usage.check().left_hour, 1)
        self.assertFalse(idle.alive())                             # its stdin closed at the result: it is gone
        self.assertEqual(len(self.played.started), 2)
        replacement = self.played.started[1]
        self.assertTrue(replacement.alive())
        ask.prestart_shutdown()                                    # Jarvis closes: no orphan
        self.assertFalse(replacement.alive())

    def test_cold_it_gets_its_message_as_it_starts_and_ends_at_the_result(self) -> None:
        ask = self.play(prestarting=False)
        outcome, heard = self.ask_it(ask)
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(heard[-1], (SAY, True))
        self.assertEqual(len(self.played.started), 1)
        self.assertFalse(self.played.started[0].alive())

    def test_one_that_dies_runs_cold_inside_the_same_counted_run(self) -> None:
        ask = self.play(modes=["die"])
        ask.prestart_ask()
        outcome, heard = self.ask_it(ask)
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(heard[-1], (SAY, True))
        self.assertEqual(self.config.ask.max_per_hour - self.usage.check().left_hour, 1)
        self.assertEqual(outcome.runs, 1)


# --------------------------------------------------------------------------
# Ahead of the question: the sign-in check and the calendars
# --------------------------------------------------------------------------

class SignInAheadTests(SeamTestCase):
    def auth_runs(self) -> int:
        return sum(1 for argv, _env in self.runner.quick if argv[1:] == ["auth", "status", "--json"])

    def test_it_runs_alongside_the_context_and_the_run_does_not_ask_twice(self) -> None:
        ask = self.make_ahead(words_stream(PLAN), FakeProcess([]))
        self.engine.readiness()
        self.clock.t += planner.AUTH_FRESH_S + 1                   # the check readiness made is old now
        before = self.auth_runs()
        outcome = ask.plan("what's on Friday?")
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(self.auth_runs(), before + 1)

    def test_a_sign_in_lost_since_is_still_refused_with_no_run(self) -> None:
        ask = self.make_ahead()
        self.engine.readiness()
        self.clock.t += planner.AUTH_FRESH_S + 1
        self.runner.auth = json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"})
        outcome = ask.plan("what's on Friday?")
        self.assertEqual(outcome.kind, "not_signed_in")
        self.assertEqual(self.runner.started, [])
        self.assertEqual(self.counted(), 0)

    def test_without_the_seam_the_run_asks_as_it_always_did(self) -> None:
        ask = self.make(words_stream(PLAN))
        self.engine.sign_in_ahead()
        self.assertEqual(self.runner.quick, [])
        self.assertTrue(ask.plan("what's on Friday?").ok)

    def test_a_check_that_fails_is_asked_again_by_the_run(self) -> None:
        ask = self.make_ahead(words_stream(PLAN), FakeProcess([]))
        self.engine.readiness()
        self.clock.t += planner.AUTH_FRESH_S + 1
        real = self.runner.run
        calls: list[int] = []

        def flaky(argv: Any, **kwargs: Any) -> Any:
            calls.append(1)
            if len(calls) == 1:
                raise OSError("busy")
            return real(argv, **kwargs)

        self.runner.run = flaky   # type: ignore[method-assign]
        self.assertTrue(ask.plan("what's on Friday?").ok)
        self.assertEqual(len(calls), 2)


class FakeCalendar:
    def __init__(self, *, counts: bool = True) -> None:
        self.reads = 0
        self.error: Exception | None = None
        if counts:
            self.mutations = 0

    def is_signed_in(self) -> bool:
        return True

    def timezone(self, *, interactive: bool = True) -> str:
        return "America/Los_Angeles"

    def list_event_briefs(self, start: datetime, end: datetime, calendars: Any) -> list[EventBrief]:
        self.reads += 1
        if self.error is not None:
            raise self.error
        return [EventBrief(f"evt{self.reads}", "primary", "Project sync", start=start, end=start + timedelta(hours=1))]


class RecentReadTests(PlannerTestCase):
    START = datetime(2026, 10, 6, tzinfo=NOW.tzinfo)
    END = datetime(2026, 10, 22, tzinfo=NOW.tzinfo)

    def sources_with(self, **calendars: FakeCalendar) -> GoogleSources:
        self.tick = TickClock()
        return GoogleSources(self.config, calendars, clock=self.tick)

    def read(self, sources: GoogleSources, alias: str = "personal") -> tuple[list[EventBrief], str, float]:
        return sources.recent_event_briefs(alias, self.START, self.END, ("primary",))

    def test_reused_while_at_most_60_s_old(self) -> None:
        calendar = FakeCalendar()
        sources = self.sources_with(personal=calendar)
        first, status, age = self.read(sources)
        self.assertEqual((status, age, calendar.reads), ("yes", 0.0, 1))
        self.tick.t += 59
        again, status, age = self.read(sources)
        self.assertEqual((status, age, calendar.reads, [b.event_id for b in again]), ("yes", 59.0, 1, ["evt1"]))
        self.tick.t += 2
        self.read(sources)
        self.assertEqual(calendar.reads, 2)                        # 61 s: read again
        self.assertEqual(first[0].event_id, "evt1")

    def test_never_past_a_change_jarvis_made(self) -> None:
        personal, work = FakeCalendar(), FakeCalendar()
        sources = self.sources_with(personal=personal, work=work)
        self.read(sources)
        personal.mutations += 1
        self.read(sources)
        self.assertEqual(personal.reads, 2)
        work.mutations += 1                                        # an invitation reaches the other account too
        self.read(sources)
        self.assertEqual(personal.reads, 3)

    def test_another_window_is_another_read(self) -> None:
        calendar = FakeCalendar()
        sources = self.sources_with(personal=calendar)
        self.read(sources)
        sources.recent_event_briefs("personal", self.START, self.END + timedelta(days=1), ("primary",))
        self.assertEqual(calendar.reads, 2)

    def test_a_calendar_that_cannot_say_is_always_read(self) -> None:
        calendar = FakeCalendar(counts=False)
        sources = self.sources_with(personal=calendar)
        self.read(sources)
        self.read(sources)
        self.assertEqual(calendar.reads, 2)

    def test_a_failed_read_is_not_kept(self) -> None:
        from briefing_reader.gcal import CalendarError

        calendar = FakeCalendar()
        calendar.error = CalendarError("no")
        sources = self.sources_with(personal=calendar)
        self.assertEqual(self.read(sources)[1], "error")
        calendar.error = None
        self.assertEqual(self.read(sources)[1], "yes")
        self.assertEqual(calendar.reads, 2)

    def test_the_real_calendar_counts_its_changes_whatever_came_of_them(self) -> None:
        calendar = GoogleCalendar.__new__(GoogleCalendar)
        calendar.mutations = 0
        calendar._creds = None
        done = mock.Mock()
        done.execute.return_value = {"id": "x"}
        calendar._execute(done, "reading", retries=1)
        self.assertEqual(calendar.mutations, 0)                    # a read is no change
        calendar._execute(done, "adding", mutation=True)
        self.assertEqual(calendar.mutations, 2)
        lost = mock.Mock()
        lost.execute.side_effect = TimeoutError("no answer")
        with self.assertRaises(Exception):
            calendar._execute(lost, "moving", mutation=True)
        self.assertEqual(calendar.mutations, 4)

    def test_an_ask_uses_the_recent_read_and_says_so_in_live(self) -> None:
        calendar = FakeCalendar()
        self.sources = self.sources_with(personal=calendar)
        ask = self.make(words_stream(PLAN), words_stream(PLAN))
        ask.read_ahead()
        self.assertEqual(calendar.reads, 1)
        with self.assertLogs("briefing_reader.ask.planner", level="INFO") as logs:
            self.tick.t += 5
            self.assertTrue(ask.plan("what's on Friday?", on_words=lambda *_args: None).ok)
        self.assertEqual(calendar.reads, 1)                        # no read of its own
        self.assertIn("evt1", self.stdin())
        self.assertIn("calendars 0.00 s, reused", "\n".join(logs.output))
        calendar.mutations += 1                                    # Jarvis moved something
        self.assertTrue(ask.plan("and now?").ok)
        self.assertEqual(calendar.reads, 2)
        self.assertIn("evt2", self.stdin(1))

    def test_a_mode_always_reads_afresh(self) -> None:
        calendar = FakeCalendar()
        sources = self.sources_with(personal=calendar)
        self.read(sources)
        sources.event_briefs("personal", self.START, self.END, ("primary",))
        self.assertEqual(calendar.reads, 2)

    def test_read_ahead_while_a_request_runs_does_nothing(self) -> None:
        calendar = FakeCalendar()
        self.sources = self.sources_with(personal=calendar)
        ask = self.make()
        with ask._busy:
            ask.read_ahead()
        self.assertEqual(calendar.reads, 0)

    def test_read_ahead_never_raises(self) -> None:
        calendar = FakeCalendar()
        calendar.error = RuntimeError("broken")
        self.sources = self.sources_with(personal=calendar)
        ask = self.make()
        ask.read_ahead()
        self.assertEqual(calendar.reads, 1)
