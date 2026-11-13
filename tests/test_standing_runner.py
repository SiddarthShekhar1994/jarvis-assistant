"""Tests for one standing check (standing.runner) end to end with fakes: nothing is read while [standing] or Ask is
off, before the consent or under Pause all; a rule's first sight is a baseline, new mail makes one run, one run file
with its card and receipts, the mark and the counts move, and the owner is told (a running Jarvis by the activation,
a closed one started, quiet hours kept for later); nothing new makes no run; a failed run leaves the mark so the mail
is checked again, and nothing is proposed twice; a plan limit or a near-limit warning holds every run, extra usage
holds Ask too; caps make one "capped" file a day; Run now, a timed agent's baseline, its due slot and a missed one;
the deadline; no subject, address or report word in the log. The lock really keeps a second check out, also one in
another process, and a check that died holding it never keeps the next out. ``--standing-run`` does nothing while
the tests run. Mail, Gmail and Claude Code are fakes; everything is in a temporary folder."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from briefing_reader import __main__ as entry
from briefing_reader.ask import stream
from briefing_reader.ask.stream import AskFailure, LimitSignal
from briefing_reader.config import AskConfig, StandingConfig
from briefing_reader.standing import model, runner
from briefing_reader.standing.agents import AgentAnswer
from briefing_reader.standing.engine import StandingResult
from briefing_reader.standing.lock import RunnerLock
from briefing_reader.standing.rules import Result, RuleAnswer
from briefing_reader.standing.store import (AppState, Definitions, RunFiles, RunnerState, RunRequests, StandingFiles,
                                            Usage)
from tests.test_standing_google import FakeReader, message
from tests.test_standing_model import AGENT, RULE

PROJECT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 12, 10, 0).astimezone()        # 10:00 on this PC: outside the quiet hours


class Reader(FakeReader):
    alias = "personal"

    def __init__(self, messages: list[dict]) -> None:
        super().__init__(messages)
        self.calls = 0

    def available(self) -> tuple[str, str]:
        return "", ""

    def list_messages(self, query: str, *, max_results: int):
        self.calls += 1
        return super().list_messages(query, max_results=max_results)

    def own_addresses(self) -> tuple[str, ...]:
        return ("owner@example.com",)


class Engine:
    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []
        self.next: list[Any] = []

    def run(self, prompt: str, *, kind: str, model: str, timeout_s: float, read: Any, cancel: Any = None):
        self.prompts.append((kind, model))
        if self.next:
            return self.next.pop(0)
        if kind == "rule":
            return StandingResult(answer=RuleAnswer({1: Result(True, {"amount": "$120.00", "due": "2026-11-20",
                                                                       "vendor": "Acme"}),
                                                     2: Result(False)}))
        return StandingResult(answer=AgentAnswer("Acme wrote twice.", "Acme wrote", False, ()))


class CheckTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        self.files = StandingFiles(self.data_dir)
        self.now = T0
        self.config = SimpleNamespace(data_dir=self.data_dir, standing=StandingConfig(enabled=True),
                                      ask=AskConfig(enabled=True), accounts={"personal": None, "work": None})
        self.reader = Reader([])
        self.engine = Engine()
        self.told: list[list[str]] = []
        self.started: list[bool] = []
        self.app_open = False
        self.extra_holds: list[datetime] = []
        rule = dict(RULE, account="personal", tidy=None)
        Definitions(self.files).save([], [model.rule_from(rule)])
        AppState(self.files).set_consent(True)

    def deps(self, **changes: Any) -> runner.Deps:
        values = dict(readers={"personal": self.reader}, engine=self.engine, clock=lambda: self.now,
                      app_running=lambda: self.app_open, tell_app=lambda runs: self.told.append(list(runs)) or True,
                      start_app=lambda: self.started.append(True) or True,
                      hold_extra_usage=self.extra_holds.append, own=lambda: ["owner@example.com"])
        values.update(changes)
        return runner.Deps(**values)

    def check(self, **changes: Any) -> runner.CheckResult:
        return runner.run_check(self.config, self.deps(**changes), origin=runner.ORIGIN_TASK)

    def later(self, minutes: int = 6) -> None:
        self.now += timedelta(minutes=minutes)

    def mail(self, *numbers: int) -> None:
        for n in numbers:
            body = "Invoice 42 from Acme. Please pay $120.00 by November 20." if n % 2 else "A newsletter"
            item = message(n, self.now - timedelta(minutes=1), body=body)
            self.reader.messages[item["id"]] = item

    def test_nothing_is_read_while_off_before_consent_or_paused(self) -> None:
        cases = [("off", lambda: setattr(self.config, "standing", StandingConfig(enabled=False))),
                 ("off", lambda: setattr(self.config, "ask", AskConfig(enabled=False))),
                 ("no_consent", lambda: AppState(self.files).set_consent(False)),
                 ("paused", lambda: AppState(self.files).set_paused_all(True))]
        for status, change in cases:
            with self.subTest(status=status):
                self.setUp()
                change()
                self.assertEqual(self.check().status, status)
                self.assertEqual((self.reader.calls, self.engine.prompts), (0, []))

    def test_a_rule_baseline_then_new_mail_makes_one_run(self) -> None:
        self.assertEqual(self.check().runs, [])                         # first sight: the mark is set
        self.assertEqual(self.engine.prompts, [])
        self.later()
        self.mail(1, 2)
        result = self.check()
        self.assertEqual(self.engine.prompts, [("rule", "haiku")])
        self.assertEqual(len(result.runs), 1)
        record = RunFiles.read(RunFiles(self.files).paths()[0])
        self.assertEqual((record["status"], record["counts"]["mails"], len(record["cards"])), ("ok", 2, 1))
        self.assertEqual(record["cards"][0]["kind"], "todo")
        self.assertEqual([receipt["kind"] for receipt in record["receipts"]], ["card"])
        today = Usage(self.files).today()
        self.assertEqual((today.runs, today.checks_of(RULE["id"])), (1, 2))
        self.assertEqual(self.started, [True])                          # Jarvis was closed: started quietly
        self.assertEqual(result.told, result.runs)
        self.later()
        self.check()
        self.assertEqual(len(self.engine.prompts), 1)                   # nothing new: no run

    def test_a_running_jarvis_is_told_and_quiet_hours_wait(self) -> None:
        self.check()
        self.later()
        self.mail(1)
        self.app_open = True
        result = self.check()
        self.assertEqual(self.told, [result.runs])
        self.app_open = False
        self.config.standing = StandingConfig(enabled=True, quiet_from="00:00", quiet_until="23:59")
        self.later()
        self.mail(3)
        quiet = self.check()
        self.assertEqual((self.started, quiet.told), ([], []))
        self.assertEqual(RunnerState(self.files).data["announce"], quiet.runs)
        self.config.standing = StandingConfig(enabled=True)
        self.later()
        after = self.check()
        self.assertEqual(after.told, quiet.runs)
        self.assertEqual(self.started, [True])

    def test_a_failed_run_keeps_the_mark_and_nothing_is_proposed_twice(self) -> None:
        self.check()
        self.later()
        self.mail(1)
        self.engine.next = [StandingResult(failure=stream.failure(stream.TIMEOUT, "timeout"))]
        failed = self.check()
        self.assertEqual(RunFiles.read(RunFiles(self.files).paths()[-1])["status"], "failed")
        self.later()
        again = self.check()
        self.assertEqual(len(self.engine.prompts), 2)                   # the same mail, checked again
        self.assertEqual(len(again.runs), 1)
        self.later()
        self.reader.messages["m0000001"]["internalDate"] = str(int(self.now.timestamp() * 1000))
        state = RunnerState(self.files)
        state.mail(RULE["id"], "personal")["seen"] = []
        state.save()
        self.check()
        cards = [len(RunFiles.read(path).get("cards") or []) for path in RunFiles(self.files).paths()]
        self.assertEqual(sum(cards), 1)                                 # proposed once
        del failed

    def test_a_plan_limit_or_a_warning_holds_every_run(self) -> None:
        self.check()
        self.later()
        self.mail(1)
        resets = self.now + timedelta(hours=2)
        self.engine.next = [StandingResult(failure=stream.failure(stream.LIMIT, stream.REJECTED),
                                           limit=LimitSignal(False, resets))]
        self.check()
        self.later()
        self.mail(3)
        self.check()
        self.assertEqual(len(self.engine.prompts), 1)                   # held
        self.now = resets + timedelta(minutes=1)
        self.check()
        self.assertEqual(len(self.engine.prompts), 2)
        self.engine.next = [StandingResult(answer=RuleAnswer({}), warning=True,
                                           warning_resets=self.now + timedelta(hours=1))]
        self.later()
        self.mail(5)
        self.check()
        self.assertEqual(RunnerState(self.files).hold_until()[1], "warning")

    def test_extra_usage_holds_ask_too(self) -> None:
        self.check()
        self.later()
        self.mail(1)
        self.engine.next = [StandingResult(failure=AskFailure(stream.LIMIT, "m", stream.OVERAGE),
                                           limit=LimitSignal(True, self.now + timedelta(hours=3)))]
        self.check()
        self.assertEqual(len(self.extra_holds), 1)
        self.assertEqual(RunnerState(self.files).hold_until()[1], "overage")

    def test_caps_make_one_capped_file_a_day(self) -> None:
        self.config.standing = StandingConfig(enabled=True, max_runs_per_day=1)
        self.check()
        for n in (1, 3, 5):
            self.later()
            self.mail(n)
            self.check()
        statuses = [RunFiles.read(path)["status"] for path in RunFiles(self.files).paths()]
        self.assertEqual(statuses, ["ok", "capped"])
        self.assertEqual(len(self.engine.prompts), 1)

    def test_run_now_and_a_timed_agent(self) -> None:
        agent = model.agent_from(dict(AGENT, mail=[{"account": "personal", "query": "from:billing@acme.example"}],
                                      trigger={"kind": "time", "times": ["12:30"]}, calendar_days=0))
        Definitions(self.files).save([agent], [])
        self.check()                                                    # a baseline at 12:00 (the 12:30 slot is ahead)
        self.now = T0.replace(hour=12, minute=31)
        self.mail(1)
        self.check()
        self.assertEqual(self.engine.prompts, [])                       # the mail mark's own baseline came first
        self.now = T0.replace(hour=12, minute=40)
        self.mail(3)
        RunRequests(self.files).ask(agent.id)
        result = self.check()
        self.assertEqual(self.engine.prompts, [("agent", "sonnet")])
        record = RunFiles.read(RunFiles(self.files).paths()[-1])
        self.assertEqual((record["trigger"], record["report"]), ("manual", "Acme wrote twice."))
        self.now = T0.replace(hour=16, minute=45) + timedelta(days=1)   # the next day's 12:30 slot, missed by hours
        self.check()
        self.assertEqual(RunFiles.read(RunFiles(self.files).paths()[-1])["status"], "missed")
        del result

    def test_the_deadline_stops_starting_jobs(self) -> None:
        self.check()
        self.later()
        self.mail(1)
        result = self.check(deadline_s=0)
        self.assertEqual((result.status, self.engine.prompts), ("late", []))

    def test_the_log_holds_no_mail_words(self) -> None:
        with self.assertLogs("briefing_reader.standing", "INFO") as logs:
            self.check()
            self.later()
            self.mail(1, 2)
            self.check()
        text = "\n".join(logs.output).casefold()
        for word in ("acme", "invoice", "billing@", "120.00", "owner@"):
            self.assertNotIn(word, text)

    def test_describe_says_counts_and_times_only(self) -> None:
        self.check()
        lines = runner.describe(self.config, now=self.now)
        self.assertIn("Standing agents and rules: on", lines[0])
        self.assertTrue(any("1 rule(s); live: 1" in line for line in lines))
        self.assertFalse(any("acme" in line.casefold() for line in lines))


class LockTests(unittest.TestCase):

    @unittest.skipUnless(sys.platform == "win32", "a Windows mutex")
    def test_another_process_s_check_keeps_this_one_out_until_it_ends(self) -> None:
        name = f"Local\\jarvis-test-standing-{time.time_ns()}"
        child = textwrap.dedent("""
            import sys, time
            sys.path.insert(0, sys.argv[1])
            from briefing_reader.standing.lock import RunnerLock
            lock = RunnerLock(sys.argv[2])
            print("held" if lock.acquire() else "busy", flush=True)
            time.sleep(float(sys.argv[3]))
            if sys.argv[4] == "release":
                lock.release()
        """)
        proc = subprocess.Popen([sys.executable, "-B", "-c", child, str(PROJECT), name, "2", "release"],
                                stdout=subprocess.PIPE, text=True)
        self.assertEqual(proc.stdout.readline().strip(), "held")
        self.assertFalse(RunnerLock(name).acquire())
        proc.wait(timeout=60)
        proc.stdout.close()
        mine = RunnerLock(name)
        self.assertTrue(mine.acquire())
        self.assertFalse(mine.abandoned)
        mine.release()
        died = subprocess.Popen([sys.executable, "-B", "-c", child, str(PROJECT), name, "0", "keep"],
                                stdout=subprocess.PIPE, text=True)
        self.assertEqual(died.stdout.readline().strip(), "held")
        died.wait(timeout=60)
        died.stdout.close()
        after = RunnerLock(name)
        self.assertTrue(after.acquire())                               # it died holding it: never kept out
        after.release()


class CommandLineTests(unittest.TestCase):

    def test_standing_run_does_nothing_while_the_tests_run(self) -> None:
        config = SimpleNamespace(standing=StandingConfig(enabled=True))
        with self.assertLogs("briefing_reader.main", "INFO") as logs:
            self.assertEqual(entry._run_standing(config, check_only=False, origin="task"), 0)
        self.assertTrue(any("no real check while the tests run" in line for line in logs.output))

    def test_the_flags_parse(self) -> None:
        args = entry.build_parser().parse_args(["--standing-run", "--origin", "app"])
        self.assertTrue(args.standing_run)
        self.assertEqual(args.origin, "app")
        shown = entry.build_parser().parse_args(["--standing-show"])
        self.assertEqual(entry.launch_kind(shown), "standing")
        with self.assertRaises(SystemExit):
            entry.main(["--standing-show", "--standing-run"])


if __name__ == "__main__":
    unittest.main()
