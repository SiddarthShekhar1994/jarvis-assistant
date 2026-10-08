"""Tests for briefing_reader.ask.stream: the init guard, the result, and the engine loop's kills.

Every run is a recorded (invented) stream from tests/fixtures/ask played by a fake process; the
timeouts run on a fake clock, so nothing waits and claude.exe is never started.
"""

from __future__ import annotations

import json
import logging
import threading
import unittest
from datetime import datetime
from pathlib import Path

from briefing_reader.ask import stream as st
from briefing_reader.ask.stream import (
    CANCELLED,
    CLI_UNSUPPORTED,
    ERROR,
    GARBLED,
    GUARD,
    LIMIT,
    MAX_TURNS,
    NOT_SIGNED_IN,
    SCHEMA_RETRIES,
    TIMEOUT,
    GuardTrip,
    check_init,
    run_planner,
)
from tests.ask_fakes import NOW, FakeClock, FakeProcess, FakeRunner, init_event, plan_stream, result_event, stream

ARGV = ["claude.exe", "-p"]


class Engine(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()

    def run_with(self, process: FakeProcess | list[str], *, allow_search: bool = False,
                 cancel: threading.Event | None = None, timeout_s: float = 90.0) -> st.RunResult:
        runner = FakeRunner(process, clock=self.clock)
        self.runner = runner
        return run_planner(runner, ARGV, "the prompt", env={"PATH": "x"}, cwd=Path("."), timeout_s=timeout_s,
                           allow_search=allow_search, cancel=cancel, clock=self.clock, now=lambda: NOW)

    def process(self) -> FakeProcess:
        return self.runner.processes[-1]


class InitGuardTests(unittest.TestCase):
    def test_the_init_ask_allows(self) -> None:
        init = check_init(init_event())
        self.assertEqual((init.api_key_source, init.tools, init.mcp_servers, init.structured_tool),
                         ("none", ("StructuredOutput",), 0, True))
        self.assertEqual((init.model, init.version), ("claude-sonnet-4-5-20250929", "2.1.293"))
        self.assertFalse(check_init(init_event(tools=[])).structured_tool)

    def test_trips(self) -> None:
        cases = {
            "api_key_source": [init_event(apiKeySource="ANTHROPIC_API_KEY"), init_event(apiKeySource="apiKeyHelper"),
                               {k: v for k, v in init_event().items() if k != "apiKeySource"}],
            "mcp_servers": [init_event(mcp_servers=[{"name": "x", "status": "connected"}]), init_event(mcp_servers=None)],
            "tools": [init_event(tools=["Bash"]), init_event(tools=["StructuredOutput", "WebFetch"]),
                      init_event(tools="StructuredOutput"), init_event(tools=[1])],
            "not_init": [{"type": "assistant"}, init_event(subtype="status")],
        }
        for reason, events in cases.items():
            for event in events:
                with self.subTest(reason=reason, event=event.get("apiKeySource", event.get("tools"))):
                    with self.assertRaises(GuardTrip) as caught:
                        check_init(event)
                    self.assertEqual(caught.exception.reason, reason)


class ResultTests(Engine):
    def test_success_with_structured_output(self) -> None:
        result = self.run_with(stream("success"))
        self.assertIsNone(result.failure)
        plan = result.plan
        self.assertTrue(plan.say.startswith("Right."))
        self.assertEqual(len(plan.lines), 2)
        self.assertTrue(plan.lines[0].startswith("Move: acct=work"))
        self.assertIsNone(plan.search)
        self.assertFalse(plan.via_fallback)
        self.assertEqual((result.stats.turns, result.stats.input_tokens, result.stats.output_tokens,
                          result.stats.cache_read_tokens, result.stats.duration_ms, result.stats.exit_code),
                         (2, 5200, 420, 3000, 8400, 0))
        self.assertEqual(result.outcome, "ok")
        self.assertFalse(self.process().killed)
        self.assertTrue(self.process().closed)
        self.assertEqual(self.runner.started[0].stdin, "the prompt")
        self.assertNotIn("the prompt", self.runner.started[0].argv)

    def test_the_mail_search_only_when_allowed(self) -> None:
        allowed = self.run_with(stream("search"), allow_search=True)
        self.assertEqual(allowed.plan.search, st.MailSearch("work", "from:ana@example.edu subject:budget newer_than:30d",
                                                            "the budget email from Ana"))
        self.assertEqual(allowed.plan.lines, ())
        self.assertIsNone(self.run_with(stream("search"), allow_search=False).plan.search)

    def test_structured_tool_but_no_structured_output_is_garbled(self) -> None:
        result = self.run_with(stream("no_structured_with_tool"))
        self.assertEqual(result.failure.kind, GARBLED)

    def test_fallback_only_when_init_had_no_structured_tool(self) -> None:
        as_json = self.run_with(stream("fallback_json"))
        self.assertTrue(as_json.plan.via_fallback)
        self.assertEqual(len(as_json.plan.lines), 2)
        self.assertTrue(as_json.plan.say)
        as_lines = self.run_with(stream("fallback_lines"))
        self.assertEqual([line.split(":")[0] for line in as_lines.plan.lines], ["Move", "Email"])
        self.assertEqual(as_lines.plan.say, "")
        prose = [stream("fallback_lines")[0], json.dumps(result_event(None, result="I can't help with that."))]
        self.assertEqual(self.run_with(prose).failure.kind, GARBLED)

    def test_shape_is_checked_locally(self) -> None:
        bad_plans = [{"say": "x" * 301, "lines": []}, {"say": "ok", "lines": ["a"] * 9},
                     {"say": "ok", "lines": ["x" * 12001]}, {"say": "ok"}, {"say": 3, "lines": []},
                     {"say": "ok", "question": "q" * 201, "lines": []}, ["not", "a", "dict"],
                     {"say": "ok", "lines": [], "gmail_search": {"account": "work", "query": "q" * 201, "why": ""}}]
        for plan in bad_plans:
            with self.subTest(plan=str(plan)[:40]):
                result = self.run_with(plan_stream(plan) if isinstance(plan, dict) else
                                       [stream("success")[0], json.dumps(result_event(plan))], allow_search=True)
                self.assertEqual(result.failure.kind, GARBLED)
        good = self.run_with(plan_stream({"say": "Fine.\nNext\u2028line", "lines": ["  Move: x ", " "]}))
        self.assertEqual((good.plan.say, good.plan.lines), ("Fine. Next line", ("Move: x",)))

    def test_error_subtypes(self) -> None:
        self.assertEqual(self.run_with(stream("max_turns")).failure.kind, MAX_TURNS)
        self.assertEqual(self.run_with(stream("schema_retries")).failure.kind, SCHEMA_RETRIES)
        limit = self.run_with(stream("limit")).failure
        self.assertEqual(limit.kind, LIMIT)
        self.assertEqual(limit.message, "Your Claude plan's usage limit is reached (it resets 3pm (Europe/London)); "
                                        "nothing was proposed")
        epoch = [stream("success")[0], json.dumps(result_event(None, subtype="success", is_error=True,
                                                               result="Claude AI usage limit reached|1791900000"))]
        self.assertRegex(self.run_with(epoch).failure.message, r"resets \d{1,2}:\d\d [AP]M")
        status = [stream("success")[0], json.dumps(result_event(None, subtype="error_during_execution", is_error=True,
                                                                result="", api_error_status=429))]
        self.assertEqual(self.run_with(status).failure.kind, LIMIT)
        other = [stream("success")[0], json.dumps(result_event(None, subtype="error_during_execution", is_error=True,
                                                               result="Something odd"))]
        self.assertEqual(self.run_with(other).failure.kind, ERROR)

    def test_a_sign_in_error_before_init_is_never_a_plan(self) -> None:
        result = self.run_with(stream("auth_error"))
        self.assertEqual(result.failure.kind, NOT_SIGNED_IN)
        self.assertIsNone(result.plan)
        sneaky = [json.dumps(result_event({"say": "ok", "lines": []}))]   # a result with a plan but no init
        self.assertEqual(self.run_with(sneaky).failure.kind, GUARD)


class KillTests(Engine):
    def assert_killed(self, result: st.RunResult, kind: str, detail: str = "") -> None:
        self.assertEqual(result.failure.kind, kind)
        if detail:
            self.assertEqual(result.failure.detail, detail)
        self.assertIsNone(result.plan)
        self.assertTrue(self.process().killed)
        self.assertTrue(self.process().closed)

    def test_guard_trips_kill_the_process(self) -> None:
        for name, detail in (("api_key", "api_key_source"), ("mcp", "mcp_servers"), ("bash_tool", "tools"),
                             ("tool_use", "tool_use")):
            with self.subTest(name=name):
                self.assert_killed(self.run_with(stream(name)), GUARD, detail)

    def test_no_init_within_20_seconds(self) -> None:
        result = self.run_with(FakeProcess([], clock=self.clock, hang=True))
        self.assert_killed(result, GUARD, "no_init")
        self.assertGreaterEqual(self.clock.t - 1000.0, 20.0)
        self.assertLess(self.clock.t - 1000.0, 21.0)

    def test_overall_timeout(self) -> None:
        result = self.run_with(FakeProcess(stream("success")[:2], clock=self.clock, hang=True), timeout_s=90)
        self.assert_killed(result, TIMEOUT)
        self.assertEqual(result.failure.message, "Took too long; nothing was proposed")
        self.assertLess(self.clock.t - 1000.0, 91.0)

    def test_cancel(self) -> None:
        cancel = threading.Event()
        process = FakeProcess(stream("success")[:1], clock=self.clock, hang=True, cancel_after=3, cancel=cancel)
        result = self.run_with(process, cancel=cancel)
        self.assert_killed(result, CANCELLED)
        self.assertLess(self.clock.t - 1000.0, 1.0)   # checked every POLL_S: well within a second

    def test_line_too_long(self) -> None:
        self.assert_killed(self.run_with(FakeProcess(stream("success"), clock=self.clock, too_long_at=1)), GARBLED,
                           "line_too_long")

    def test_truncated_stream_and_unknown_option(self) -> None:
        truncated = self.run_with(FakeProcess(stream("truncated"), clock=self.clock, exit_code=1))
        self.assertEqual((truncated.failure.kind, truncated.failure.detail), (ERROR, "exit 1"))
        unknown = self.run_with(FakeProcess([], clock=self.clock, exit_code=1,
                                            stderr="error: unknown option '--permission-prompts'"))
        self.assertEqual(unknown.failure.kind, CLI_UNSUPPORTED)
        logged_out = self.run_with(FakeProcess([], clock=self.clock, exit_code=1,
                                               stderr="Not logged in. Please run /login"))
        self.assertEqual(logged_out.failure.kind, NOT_SIGNED_IN)

    def test_garbage_lines_are_skipped_never_logged(self) -> None:
        with self.assertLogs("briefing_reader.ask.stream", level="INFO") as logs:
            result = self.run_with(stream("garbage"))
        self.assertIsNone(result.failure, result.failure)
        self.assertEqual(len(result.plan.lines), 2)
        text = "\n".join(logs.output)
        self.assertIn("skipped 3 unreadable output line(s)", text)   # prose, broken JSON, a JSON list
        self.assertNotIn("Debugger", text)

    def test_start_failure(self) -> None:
        class Broken(FakeRunner):
            def start(self, argv, *, stdin_text, env, cwd):  # type: ignore[no-untyped-def]
                raise FileNotFoundError("gone")

        with self.assertLogs("briefing_reader.ask.stream", level="WARNING"):
            result = run_planner(Broken(), ARGV, "p", env={}, cwd=Path("."), timeout_s=90, allow_search=False,
                                 clock=self.clock)
        self.assertEqual((result.failure.kind, result.failure.detail), (ERROR, "start"))


class RateLimitTests(Engine):
    """Claude Code's rate_limit_event (2.1.293): extra usage or the plan's limit stops the run."""

    @staticmethod
    def with_event(info: dict) -> list[str]:
        lines = stream("success")
        event = {"type": "rate_limit_event", "rate_limit_info": info, "uuid": "u1", "session_id": "s1"}
        return [lines[0], json.dumps(event)] + lines[1:]

    def test_extra_usage_stops_the_run_and_no_plan_is_accepted(self) -> None:
        result = self.run_with(stream("overage"))   # recorded shape: rejected on the plan, overage allowed
        self.assertEqual((result.failure.kind, result.failure.detail), (LIMIT, st.OVERAGE))
        self.assertEqual(result.failure.message, st.OVERAGE_MESSAGE)
        self.assertIn("claude.ai/settings/usage", result.failure.message)
        self.assertIsNone(result.plan)
        self.assertTrue(self.process().killed)
        self.assertTrue(result.limit.overage)
        self.assertEqual(result.limit.resets_at.timestamp(), 1791401400)
        self.assertEqual(self.process().lines[-1][:20], '{"type": "result", "')   # stopped before the result

    def test_every_extra_usage_sign_stops_it(self) -> None:
        for info in ({"status": "allowed", "isUsingOverage": True},
                     {"status": "allowed", "overageInUse": True},
                     {"status": "allowed_warning", "rateLimitType": "overage"},
                     {"status": "rejected", "overageStatus": "allowed_warning"}):
            with self.subTest(info=info):
                result = self.run_with(self.with_event(info))
                self.assertEqual((result.failure.kind, result.failure.detail), (LIMIT, st.OVERAGE))
                self.assertTrue(self.process().killed)

    def test_the_plans_own_limit_stops_it_too(self) -> None:
        reset = int(NOW.timestamp()) + 2 * 3600
        result = self.run_with(self.with_event({"status": "rejected", "rateLimitType": "five_hour",
                                                "resetsAt": reset, "overageStatus": "rejected",
                                                "overageDisabledReason": "org_level_disabled"}))
        self.assertEqual((result.failure.kind, result.failure.detail), (LIMIT, st.REJECTED))
        self.assertFalse(result.limit.overage)
        self.assertEqual(result.failure.message, "Your Claude plan's usage limit is reached (it resets 11:30 AM); "
                                                 "nothing was proposed")
        self.assertTrue(self.process().killed)

    def test_plan_usage_within_the_limit_goes_on(self) -> None:
        for info in ({"status": "allowed", "rateLimitType": "five_hour", "utilization": 0.4},
                     {"status": "allowed_warning", "rateLimitType": "seven_day", "isUsingOverage": False},
                     {"status": "allowed", "rateLimitType": "seven_day_overage_included", "isUsingOverage": False},
                     "not a dict", None):
            with self.subTest(info=info):
                result = self.run_with(self.with_event(info))
                self.assertIsNone(result.failure, result.failure)
                self.assertIsNone(result.limit)
                self.assertEqual(len(result.plan.lines), 2)

    def test_a_rate_limit_event_before_init_is_still_a_limit(self) -> None:
        lines = self.with_event({"status": "allowed", "isUsingOverage": True})
        result = self.run_with([lines[1], lines[0]] + lines[2:])
        self.assertEqual((result.failure.kind, result.failure.detail), (LIMIT, st.OVERAGE))

    def test_the_signal_reads_only_the_fields_it_needs(self) -> None:
        self.assertIsNone(st.rate_limit_signal({"type": "assistant", "rate_limit_info": {"isUsingOverage": True}}))
        signal = st.rate_limit_signal({"type": "rate_limit_event",
                                       "rate_limit_info": {"isUsingOverage": True, "resetsAt": "soon"}},
                                      now=lambda: NOW)
        self.assertEqual(signal, st.LimitSignal(True, None))

    def test_clock_words(self) -> None:
        self.assertEqual(st.clock_words(NOW.replace(hour=14, minute=5), NOW), "2:05 PM")
        self.assertEqual(st.clock_words(NOW.replace(hour=0, minute=0) + st.timedelta(days=3), NOW), "Sat 12:00 AM")


class MessagesTests(unittest.TestCase):
    def test_every_kind_has_plain_words(self) -> None:
        for kind in st.FAILURE_KINDS:
            message = st.failure(kind).message
            self.assertTrue(message)
            self.assertNotIn("{", message)

    def test_reset_words_are_sanitized(self) -> None:
        nasty = "usage limit reached; resets 3pm <script>alert(1)</script>"
        found = st.classify_text(nasty, now=lambda: datetime(2026, 10, 7, 9, 0).astimezone())
        self.assertEqual(found.kind, LIMIT)
        self.assertNotIn("<", found.message)


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
