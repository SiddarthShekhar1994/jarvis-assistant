"""Jarvis, a true agent, part A1: whole tasks end to end. A fake engine (tests.agent_fakes.FakeEngine) reads the MCP
config file as the real CLI does and really POSTs to the real endpoint over HTTP, on loopback: plan, say, files, finish;
ask_owner and the answer as the next message; a failing tool and a retry; a message mid-run; Stop mid-tool with a late
result dropped; the budget reached, then yes and no; the plan's limit; the guard on every init and tool_use. The real
Claude Code is never started: a test proves that too. Every value is invented; files go only to temporary folders."""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from briefing_reader import config as app_config
from briefing_reader.agent import policy, tools
from briefing_reader.agent import runner as ar
from briefing_reader.agent.endpoint import TOO_LONG_TEXT
from briefing_reader.agent.runner import AgentRun, Timing, check_agent_init, first_message, owner_message
from briefing_reader.agent.tools import Registry, ToolResult
from briefing_reader.agent.workspace import TaskFolder
from briefing_reader.ask import cli
from briefing_reader.ask.stream import GuardTrip
from briefing_reader.config import AgentConfig
from tests.agent_fakes import (FAST, FIXTURES, FakeEngine, FakeEngineRunner, agent_engine, base_engine, init_event,
                               rate_limit_event, result_event)
from tests.ask_fakes import FakeProcess

GOAL = "compare Sam's three invented laptops and write me a table"
WHEN = datetime(2026, 11, 4, 18, 30, tzinfo=timezone.utc)
SAMPLE_TOOLS = ("ask_owner", "finish", "read_page", "slow_step", "update_plan", "web_search")


def noop_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


class RunCase(unittest.TestCase):
    """One AgentRun over a FakeEngine, its events recorded."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.folder = TaskFolder.create(self.root / "ws", "words", WHEN)
        self.events: list[ar.AgentEvent] = []
        self.ended = threading.Event()
        self.engine: FakeEngine | None = None
        self.run_: AgentRun | None = None
        self.on_event: Callable[[ar.AgentEvent], None] | None = None

    def hear(self, event: ar.AgentEvent) -> None:
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)
        if event.kind == ar.E_ENDED:
            self.ended.set()

    def go(self, script: Any, *, goal: str = GOAL, agent: dict | None = None, extra_tools: tuple = (),
           timing: Timing = FAST, engine_kwargs: dict | None = None, **run: Any) -> AgentRun:
        process = script if isinstance(script, FakeProcess) else FakeEngine(script, **(engine_kwargs or {}))
        self.engine = process
        self.runner = FakeEngineRunner(process)
        self.agent_engine = agent_engine(self.root, self.runner, **(agent or {}))
        registry = Registry((*policy.offered(), *extra_tools))
        self.registry = registry
        self.run_ = AgentRun(goal, engine=self.agent_engine, registry=registry, folder=self.folder, on_event=self.hear,
                             timing=timing, **run)
        self.addCleanup(self.stop_it)
        self.run_.start()
        return self.run_

    def stop_it(self) -> None:
        if self.run_ is not None:
            self.run_.stop("test ended")
            self.run_.join(5)

    def end(self, timeout: float = 15.0) -> ar.AgentEnd:
        self.assertTrue(self.ended.wait(timeout), f"it did not end: {[event.kind for event in self.events]}")
        self.assertTrue(self.run_.join(5), "its thread ended")
        end = self.run_.ended
        assert end is not None
        return end

    def assert_clean(self) -> None:
        """The run is over: no process, no endpoint, no MCP config, no run folder; the script saw nothing go wrong."""
        self.assertTrue(self.engine.killed or self.engine.stdin_closed)
        self.assertTrue(self.engine.closed)
        self.assertIsNone(self.engine.failure)
        self.assertTrue(self.run_._endpoint is None or self.run_._endpoint.closed)
        runs = self.agent_engine.runs_folder
        self.assertEqual(list(runs.rglob("*")) if runs.exists() else [], [])
        self.assertEqual(list(self.agent_engine.work_folder.iterdir()), [])

    def kinds(self, *only: str) -> list[str]:
        return [event.kind for event in self.events if not only or event.kind in only]

    def of(self, kind: str) -> list[ar.AgentEvent]:
        return [event for event in self.events if event.kind == kind]

    def until(self, predicate: Callable[[], Any], timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail(f"never happened; events: {self.kinds()}")

    def question(self) -> ar.OwnerQuestion:
        self.until(lambda: self.of(ar.E_QUESTION))
        return self.of(ar.E_QUESTION)[-1].question


def whole_task(engine: FakeEngine) -> None:
    engine.wait_message()
    engine.call("update_plan", steps=[{"text": "Compare them", "status": "doing"}, {"text": "Write it", "status": "todo"}])
    engine.call("say", text="Looking at the three now.")
    engine.call("write_file", name="table.md", text="| a | b |\n")
    read = engine.call("read_file", name="table.md")
    assert "| a | b |" in engine.text_of(read)
    assert Path(engine.argv[engine.argv.index("--mcp-config") + 1]).is_file()     # there for the length of the run
    listed = engine.call("list_files")
    assert "table.md" in engine.text_of(listed)
    engine.call("update_plan", steps=[{"text": "Compare them", "status": "done"}, {"text": "Write it", "status": "done"}])
    engine.call("finish", summary="Wrote the table.", outcome="done", outputs=["table.md"])
    engine.end_turn("Done.")


class WholeTaskTests(RunCase):
    def test_plan_say_files_finish(self) -> None:
        self.go(whole_task)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.own_words), ("done", ar.C_FINISHED, False))
        self.assertEqual(end.summary, "Wrote the table.")
        self.assertEqual(end.outputs, ("table.md",))
        self.assertEqual(end.tools, 7)
        self.assertEqual(end.turns, 8)                  # seven calls, each its own message, and the last words
        self.assertEqual((end.input_tokens, end.output_tokens), (120, 40))
        self.assertEqual(self.of(ar.E_SAY)[0].text, "Looking at the three now.")
        plans = self.of(ar.E_PLAN)
        self.assertEqual([step.status for step in plans[-1].steps], ["done", "done"])
        self.assertEqual(self.kinds()[0], ar.E_STARTED)
        self.assertEqual(self.kinds()[-1], ar.E_ENDED)
        starts = [event.call for event in self.of(ar.E_TOOL_START)]
        ends = [event.call for event in self.of(ar.E_TOOL_END)]
        self.assertEqual(starts, list(range(1, 8)))     # one step per tool call
        self.assertEqual(ends, starts)
        self.assertEqual((self.folder.path / "table.md").read_text(encoding="utf-8"), "| a | b |\n")
        self.assert_clean()

    def test_the_process_is_started_as_the_spec_says(self) -> None:
        self.go(whole_task, agent={"model": "opus", "effort": "high"})
        self.end()
        engine = self.engine
        argv = engine.argv
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], self.registry.allowed())
        self.assertEqual(engine.cwd, self.agent_engine.work_folder)
        self.assertEqual(self.agent_engine.work_folder.name, cli.AGENT_WORK_FOLDER)
        mcp_file = Path(argv[argv.index("--mcp-config") + 1])
        self.assertEqual(mcp_file.parent.parent, self.agent_engine.runs_folder)
        self.assertNotEqual(mcp_file.parent, engine.cwd)
        self.assertFalse(mcp_file.exists())             # gone with the run
        self.assertEqual(engine.env["MCP_TOOL_TIMEOUT"], "120000")
        self.assertNotIn("ANTHROPIC_API_KEY", engine.env)
        self.assertNotIn("NOTION_TOKEN", engine.env)

    def test_the_token_is_never_in_argv_the_environment_or_a_log(self) -> None:
        secret_file = "Sam-private-table.md"
        secret_say = "Sam's laptop is the private one."

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text=secret_say)
            engine.call("write_file", name=secret_file, text="private body words")
            engine.call("write_file", name="../private-refused.md", text="private body words")
            engine.call("finish", summary="private summary words", outcome="done", outputs=[secret_file])
            engine.end_turn("private last words")

        logs: list[str] = []

        class Keep(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                logs.append(record.getMessage())

        handler = Keep(level=logging.DEBUG)
        root = logging.getLogger()
        level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        self.addCleanup(root.setLevel, level)
        self.addCleanup(root.removeHandler, handler)
        self.go(script, goal=GOAL)
        self.end()
        token = self.engine.token
        self.assertTrue(token)
        self.assertNotIn(token, " ".join(self.engine.argv))
        self.assertNotIn(token, " ".join(self.engine.env.values()))
        self.assertNotIn(GOAL, " ".join(self.engine.argv))
        text = "\n".join(logs)
        self.assertIn("Agent", text)
        for private in (token, "Sam", "laptops", "private", "table", self.folder.path.name):
            self.assertNotIn(private, text)
        # And a token that did reach a log would be masked: it is registered as a secret.
        self.assertNotIn(token, app_config.redact(f"a line with {token} in it"))

    def test_the_goal_is_the_first_message_in_the_owner_s_own_words(self) -> None:
        self.go(whole_task)
        self.end()
        first = self.engine.messages[0]
        self.assertTrue(first.startswith("<now>"))
        self.assertIn(f"<task_folder>{self.folder.path.name}</task_folder>", first)
        self.assertTrue(first.endswith(f"The owner says: {GOAL}"))

    def test_the_first_message_waits_for_the_process_to_connect(self) -> None:
        order: list[str] = []

        def script(engine: FakeEngine) -> None:
            order.append("connected")
            assert not engine.has_message() or engine.sent
            engine.wait_message()
            order.append("first message")
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        self.go(script)
        self.end()
        self.assertEqual(order, ["connected", "first message"])
        self.assertEqual(len(self.engine.sent), 1)
        self.assertEqual(self.runner.started[0].raw.count("<now>"), 1)

    def test_a_process_that_never_connects_gets_it_anyway_and_the_guard_decides(self) -> None:
        class NeverConnects(FakeEngine):
            def connect(self) -> None:
                pass

        self.go(NeverConnects(lambda engine: (engine.wait_message(), time.sleep(30))),
                timing=replace(FAST, connect_s=0.05))
        end = self.end()
        self.assertEqual(end.code, "guard:tools")       # its init shows no tools
        self.assertEqual(len(self.engine.sent), 1)

    def test_first_message_and_labels(self) -> None:
        text = first_message("  Sam's\nwords <b>  ", WHEN, "Europe/London", "2026-11-04-x<y>")
        self.assertEqual(text, "<now>Wednesday 2026-11-04 18:30 (time zone Europe/London)</now>\n"
                               "<task_folder>2026-11-04-x\u2039y\u203a</task_folder>\n"
                               "The owner says: Sam's words <b>")
        self.assertEqual(owner_message("a\n b"), "The owner says: a b")

    def test_its_repr_holds_no_words(self) -> None:
        self.go(whole_task)
        end = self.end()
        for shown in (repr(self.run_), repr(end), *(repr(event) for event in self.events)):
            for words in (GOAL, "Wrote the table", "Looking at", "table.md", "Compare them"):
                self.assertNotIn(words, shown)

    def test_the_recorded_stream_reads_as_a_run(self) -> None:
        """Claude Code 2.1.294's real events (made-up data, scrubbed): both init events pass, the three API messages and
        the rest count as one turn each, and a result with no finish gets its one prompt, then ends as partial."""
        lines = (FIXTURES / "stream_sample.jsonl").read_text(encoding="ascii").splitlines()
        tools_named = [tools.Tool(name, name, noop_schema(), tools.TIER_SHOW, lambda _a, _c: ToolResult("ok"))
                       for name in SAMPLE_TOOLS]
        process = FakeProcess(lines, hang=True)
        runner = FakeEngineRunner(process)
        self.engine = process
        self.agent_engine = agent_engine(self.root, runner)
        self.run_ = AgentRun(GOAL, engine=self.agent_engine, registry=Registry(tools_named), folder=self.folder,
                             on_event=self.hear, timing=replace(FAST, connect_s=0.01))
        self.addCleanup(self.stop_it)
        self.run_.start()
        end = self.end()
        self.assertEqual((end.outcome, end.code), ("partial", ar.C_NO_FINISH))
        self.assertEqual(end.turns, 6)                  # 7 assistant message ids, one of them seen after the end
        self.assertEqual(len(process.sent), 2)          # the goal, and the one prompt to carry on or finish
        self.assertIn(ar.CONTINUE_TEXT, process.sent[1])
        self.assertTrue(all(event.kind != ar.E_ENDED or event.end.code == ar.C_NO_FINISH for event in self.events))


class AskOwnerTests(RunCase):
    def script(self, engine: FakeEngine) -> None:
        engine.wait_message()
        result = engine.call("ask_owner", question="Which one, Blue or Red?", choices=["Blue", "Red"])
        assert "End your turn" in engine.text_of(result)
        engine.end_turn("I've asked you on a card.")
        answer = engine.wait_message()
        engine.call("write_file", name="choice.md", text=answer)
        engine.call("finish", summary="Used your choice.", outcome="done", outputs=["choice.md"])
        engine.end_turn()

    def test_the_answer_goes_in_as_the_next_message(self) -> None:
        run = self.go(self.script)
        question = self.question()
        self.assertEqual((question.kind, question.text, question.choices),
                         (ar.Q_TASK, "Which one, Blue or Red?", ("Blue", "Red")))
        self.until(lambda: run._idle)                   # the result came: it is idle and uses nothing
        self.assertEqual(run.waiting, question)
        self.assertEqual(len(self.engine.sent), 1)      # the goal: nothing else goes in by itself
        self.assertTrue(run.answer(question.id, "Blue, please"))
        end = self.end()
        self.assertEqual(end.outcome, "done")
        self.assertEqual(self.engine.messages[1], "The owner answers your question: Blue, please")
        self.assertIn("Blue, please", self.folder.read("choice.md")[1])
        self.assertEqual(self.engine.inits, 2)          # one init per user message, both checked
        self.assertEqual(self.kinds(ar.E_QUESTION, ar.E_QUESTION_GONE), [ar.E_QUESTION, ar.E_QUESTION_GONE])
        self.assert_clean()

    def test_typing_answers_the_question_that_waits(self) -> None:
        run = self.go(self.script)
        self.question()
        self.until(lambda: run._idle)
        self.assertEqual(run.owner_says("the red\none"), "answered")
        self.end()
        self.assertEqual(self.engine.messages[1], "The owner answers your question: the red one")

    def test_an_answer_before_the_turn_ended_waits_for_the_result(self) -> None:
        gate = threading.Event()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?")
            gate.wait(10)
            assert not engine.has_message()             # D5: not while it is still ending its turn
            engine.end_turn("Asked.")
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script)
        question = self.question()
        self.assertTrue(run.answer(question.id, "Blue"))
        self.assertEqual(len(self.engine.sent), 1)      # still only the goal
        gate.set()
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(self.engine.messages[1], "The owner answers your question: Blue")
        self.assertIsNone(self.engine.failure)

    def test_only_the_question_that_waits_can_be_answered(self) -> None:
        run = self.go(self.script)
        question = self.question()
        self.assertFalse(run.answer("someone-else", "Blue"))
        self.assertEqual(run.waiting, question)
        self.assertTrue(run.answer(question.id, "Blue"))
        self.assertFalse(run.answer(question.id, "Red"))   # answered: a second click does nothing
        self.end()
        self.assertFalse(run.answer(question.id, "Red"))

    def test_a_second_question_while_one_waits(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="First?")
            second = engine.call("ask_owner", question="Second?")
            assert "already" in engine.text_of(second)
            engine.end_turn("Asked.")
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script)
        question = self.question()
        self.until(lambda: run._idle)
        self.assertEqual(len(self.of(ar.E_QUESTION)), 1)
        run.answer(question.id, "Yes")
        self.end()
        self.assertIsNone(self.engine.failure)

    def test_no_answer_ends_it_as_partial_with_its_report(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?")
            engine.end_turn("Asked.")
            told = engine.wait_message()
            assert told == ar.WRAP_UP_SILENT
            engine.call("finish", summary="I wrote what I could.", outcome="done")
            engine.end_turn()

        self.go(script, timing=replace(FAST, question_s=0.05))
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_FINISHED, "I wrote what I could."))
        self.assertIsNone(self.engine.failure)

    def test_no_answer_and_no_report(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?")
            engine.end_turn("Asked.")
            engine.wait_message()
            engine.end_turn("I'd rather not.")

        self.go(script, timing=replace(FAST, question_s=0.05))
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.own_words), ("partial", ar.C_NO_ANSWER, True))
        self.assertEqual(end.summary, ar.NO_ANSWER_WORDS)
        self.assert_clean()


class FailingToolTests(RunCase):
    def test_a_failing_tool_and_a_retry(self) -> None:
        seen: list[dict] = []

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            seen.append(engine.call("write_file", name="../outside.md", text="no"))
            seen.append(engine.call("write_file", name="report.exe", text="no"))
            seen.append(engine.call("write_file", name="report.md"))        # bad input: no text
            seen.append(engine.call("write_file", name="report.md", text="yes"))
            seen.append(engine.call("finish", summary="Done.", outcome="done", outputs=["nothing.md"]))
            seen.append(engine.call("finish", summary="Done.", outcome="done", outputs=["report.md"]))
            engine.end_turn()

        self.go(script)
        end = self.end()
        self.assertEqual([bool(result.get("isError")) for result in seen], [True, True, True, False, True, False])
        self.assertIn('stay inside the task folder', FakeEngine.text_of(seen[0]))
        self.assertIn("isn't allowed", FakeEngine.text_of(seen[1]))
        self.assertIn('"text"', FakeEngine.text_of(seen[2]))
        self.assertEqual((end.outcome, end.outputs), ("done", ("report.md",)))
        self.assertEqual(self.folder.listing(), [("report.md", 3)])
        codes = [event.result.code for event in self.of(ar.E_TOOL_END)]
        self.assertEqual(codes, ["parent", "extension", "bad_input", "", "refused", ""])
        self.assert_clean()

    def test_a_tool_past_the_timeout_answers_and_its_late_result_is_dropped(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        wrote: list[Any] = []

        def slow(_args: Any, ctx: tools.RunContext) -> ToolResult:
            release.wait(10)
            try:
                wrote.append(ctx.folder.write("late.md", "late", may_commit=lambda: not ctx.cancelled()))
            except tools.WorkspaceRefused as refused:
                wrote.append(refused.code)
            return ToolResult("late")

        slow_tool = tools.Tool("slow", "Slow.", noop_schema(), tools.TIER_SHOW, slow, "Slow")
        answers: list[dict] = []

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            answers.append(engine.call("slow"))
            release.set()
            engine.call("finish", summary="Tried another way.", outcome="partial")
            engine.end_turn()

        self.go(script, extra_tools=(slow_tool,), timing=replace(FAST, tool_s=0.2))
        end = self.end()
        self.assertTrue(answers[0]["isError"])
        self.assertEqual(FakeEngine.text_of(answers[0]), TOO_LONG_TEXT)
        self.assertEqual(end.outcome, "partial")
        self.until(lambda: wrote)
        self.assertEqual(wrote, ["stopped"])            # the handler came back late: it changed nothing
        self.assertEqual(self.folder.listing(), [])
        ends = [event for event in self.of(ar.E_TOOL_END) if event.tool == "slow"]
        self.assertEqual(len(ends), 1)                  # only the give-up: the late one is dropped
        self.assertEqual(ends[0].result.code, "too_long")


class MidRunTests(RunCase):
    def blocking(self) -> tuple[tools.Tool, threading.Event, threading.Event]:
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def handler(_args: Any, _ctx: tools.RunContext) -> ToolResult:
            entered.set()
            release.wait(10)
            return ToolResult("slow step done")

        return tools.Tool("block", "Blocks.", noop_schema(), tools.TIER_SHOW, handler, "Blocks"), entered, release

    def test_a_message_while_a_tool_runs_arrives_after_it_returns(self) -> None:
        tool, entered, release = self.blocking()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("block")
            said = engine.wait_message()                # delivered once the tool has returned
            engine.call("finish", summary=f"Heard: {said}", outcome="done")
            engine.end_turn()

        run = self.go(script, extra_tools=(tool,))
        self.assertTrue(entered.wait(10))
        self.assertEqual(run.owner_says("use the\nblue one too"), "sent")
        self.assertEqual(len(self.engine.sent), 2)      # it went in at once: the CLI holds it until the tool is back
        release.set()
        end = self.end()
        self.assertEqual(self.engine.messages[1], "The owner says: use the blue one too")
        self.assertEqual(end.summary, "Heard: The owner says: use the blue one too")

    def test_a_result_while_a_message_is_on_its_way_is_not_idle(self) -> None:
        tool, entered, release = self.blocking()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("block")
            engine.end_turn("That's all I had.")        # its turn ended; the message was queued meanwhile
            time.sleep(0.1)
            engine.wait_message()
            engine.call("finish", summary="Carried on.", outcome="done")
            engine.end_turn()

        run = self.go(script, extra_tools=(tool,))
        self.assertTrue(entered.wait(10))
        self.assertEqual(run.owner_says("one more thing"), "sent")
        release.set()
        self.assertEqual(self.end().outcome, "done")
        self.assertNotIn(ar.CONTINUE_TEXT, self.engine.messages)   # no prompt: it was not idle
        self.assertIsNone(self.engine.failure)

    def test_a_message_that_never_shows_as_delivered(self) -> None:
        """Nothing came for a while after a result with a message on its way: idle after all, and it goes on."""
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.end_turn("Hello?")
            engine.wait_message()                       # the one prompt
            engine.call("finish", summary="Fine.", outcome="done")
            engine.end_turn()

        run = self.go(script, timing=replace(FAST, replay_s=0.05))
        run._unreplayed = 5                             # as if replays had been lost
        self.assertEqual(self.end().outcome, "done")

    def test_stop_mid_tool_kills_at_once_and_drops_the_late_result(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        entered = threading.Event()
        outcome: list[Any] = []

        def handler(_args: Any, ctx: tools.RunContext) -> ToolResult:
            entered.set()
            release.wait(10)
            try:
                outcome.append(ctx.folder.write("late.md", "late", may_commit=lambda: not ctx.cancelled()))
            except tools.WorkspaceRefused as refused:
                outcome.append(refused.code)
            return ToolResult("late result")

        tool = tools.Tool("block", "Blocks.", noop_schema(), tools.TIER_SHOW, handler, "Blocks")

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("write_file", name="before.md", text="kept")
            engine.call("block")
            raise AssertionError("it was killed mid-tool")

        run = self.go(script, extra_tools=(tool,))
        self.assertTrue(entered.wait(10))
        began = time.monotonic()
        run.stop("the owner's Stop")
        self.assertLess(time.monotonic() - began, 1.0)  # at once
        self.assertTrue(self.engine.killed)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), (ar.OUTCOME_STOPPED, ar.C_STOPPED, ar.STOPPED_WORDS))
        before = list(self.events)
        release.set()                                   # the tool comes back now
        self.until(lambda: outcome)
        time.sleep(0.05)
        self.assertEqual(outcome, ["stopped"])
        self.assertEqual(self.folder.listing(), [("before.md", 4)])   # what was written stays; the late write did not happen
        self.assertEqual(self.events, before)           # nothing more is heard
        self.assertEqual([event.tool for event in self.of(ar.E_TOOL_END)], ["write_file"])
        self.assertIsNone(self.engine.failure)
        self.assert_clean()
        self.assertEqual(run.owner_says("too late"), "refused")

    def test_a_call_after_the_stop_is_refused(self) -> None:
        run = self.go(lambda engine: (engine.wait_message(), time.sleep(30)))
        self.until(lambda: self.of(ar.E_STARTED))
        endpoint = run._endpoint
        run.stop()
        self.end()
        self.assertTrue(endpoint.closed)
        self.assertEqual(run._invoke("say", {"text": "late"}, mock.Mock()).code, ar.STOPPED_CODE)
        self.assertEqual(self.of(ar.E_SAY), [])

    def test_a_stop_before_the_process_started(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        run_box: dict[str, Any] = {}

        original = FakeEngineRunner.run

        def slow_quick(runner: Any, *args: Any, **kwargs: Any) -> Any:
            run_box["checking"] = True
            gate.wait(10)
            return original(runner, *args, **kwargs)

        with mock.patch.object(FakeEngineRunner, "run", slow_quick):
            run = self.go(FakeEngine(lambda engine: engine.wait_message()))
            self.until(lambda: run_box.get("checking"))
            run.stop()
            end = self.run_.ended
            self.assertEqual(end.outcome, ar.OUTCOME_STOPPED)
            self.assertTrue(self.ended.is_set())        # told at once, though `claude auth status` is still running
            gate.set()
            self.assertTrue(run.join(5))
        self.assertEqual(self.runner.started, [])       # and no process ever started
        self.assertFalse(self.agent_engine.runs_folder.exists() and list(self.agent_engine.runs_folder.iterdir()))


class BudgetTests(RunCase):
    def script(self, engine: FakeEngine) -> None:
        engine.wait_message()
        engine.call("say", text="One.")
        engine.call("write_file", name="a.md", text="a")     # the second message: the budget is reached
        held = engine.call("write_file", name="b.md", text="b")
        assert "reached its budget" in engine.text_of(held)
        assert not held.get("isError")
        engine.end_turn("Waiting for you.")
        told = engine.wait_message()
        engine.messages_seen = told
        engine.call("write_file", name="b.md", text="b")
        engine.call("finish", summary="All of it.", outcome="done", outputs=["a.md", "b.md"])
        engine.end_turn()

    def test_reached_then_yes(self) -> None:
        run = self.go(self.script, agent={"max_turns": 2})
        question = self.question()
        self.assertEqual(question.kind, ar.Q_KEEP_GOING)
        self.assertIn("Keep going?", question.text)
        self.assertIn("2 turns", question.text)
        self.assertEqual(question.choices, ("Keep going", "Stop here"))
        self.until(lambda: run._idle)
        self.assertTrue(run.answer(question.id, "Keep going"))
        end = self.end()
        self.assertEqual((end.outcome, end.code), ("done", ar.C_FINISHED))
        self.assertEqual(self.engine.messages[1], ar.KEEP_GOING_YES)
        self.assertEqual(self.folder.listing(), [("a.md", 1), ("b.md", 1)])
        self.assertIsNone(self.engine.failure)

    def no_script(self, engine: FakeEngine) -> None:
        engine.wait_message()
        engine.call("say", text="One.")
        engine.call("write_file", name="a.md", text="a")
        engine.call("write_file", name="b.md", text="b")     # held
        engine.end_turn("Waiting for you.")
        told = engine.wait_message()
        assert told == ar.WRAP_UP_NO
        held = engine.call("write_file", name="c.md", text="c")
        assert ar.WRAP_UP_TOOL_TEXT in engine.text_of(held)
        engine.call("finish", summary="Stopped with a.md written.", outcome="done", outputs=["a.md"])
        engine.end_turn()

    def test_reached_then_no(self) -> None:
        run = self.go(self.no_script, agent={"max_turns": 2})
        question = self.question()
        self.until(lambda: run._idle)
        self.assertTrue(run.answer(question.id, "Stop here"))
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_FINISHED, "Stopped with a.md written."))
        self.assertEqual(end.outputs, ("a.md",))
        self.assertEqual(self.folder.listing(), [("a.md", 1)])
        self.assertIsNone(self.engine.failure)

    def test_reached_then_no_and_it_never_reports(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")
            engine.call("write_file", name="b.md", text="b")
            engine.end_turn("Waiting.")
            engine.wait_message()
            engine.end_turn("No.")

        run = self.go(script, agent={"max_turns": 2})
        question = self.question()
        self.until(lambda: run._idle)
        run.answer(question.id, "no")
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_OWNER_NO, ar.OWNER_NO_WORDS))
        self.assert_clean()

    def test_reached_and_no_answer(self) -> None:
        self.go(self.no_script_silent, agent={"max_turns": 2}, timing=replace(FAST, keep_going_s=0.05))
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_FINISHED, "Written so far."))
        self.assertIsNone(self.engine.failure)

    def no_script_silent(self, engine: FakeEngine) -> None:
        engine.wait_message()
        engine.call("say", text="One.")
        engine.call("write_file", name="a.md", text="a")
        engine.call("write_file", name="b.md", text="b")
        engine.end_turn("Waiting for you.")
        told = engine.wait_message()
        assert told == ar.WRAP_UP_SILENT
        engine.call("finish", summary="Written so far.", outcome="partial")
        engine.end_turn()

    def test_wrapping_up_has_its_limit(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")
            engine.call("write_file", name="b.md", text="b")
            engine.end_turn("Waiting.")
            engine.wait_message()
            time.sleep(30)                              # it never reports and never ends its turn

        run = self.go(script, agent={"max_turns": 2}, timing=replace(FAST, wrap_up_s=0.1))
        question = self.question()
        self.until(lambda: run._idle)
        run.answer(question.id, "no")
        self.assertEqual(self.end().code, ar.C_OWNER_NO)

    def test_words_that_are_not_a_yes_go_in_as_his_message_too(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")
            engine.end_turn("Waiting.")
            engine.wait_message()
            engine.call("finish", summary="Used the blue one.", outcome="done")
            engine.end_turn()

        run = self.go(script, agent={"max_turns": 2})
        self.question()
        self.until(lambda: run._idle)
        self.assertEqual(run.owner_says("just use the blue one"), "answered")
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(self.engine.messages[1], f"{ar.KEEP_GOING_YES}\nThe owner says: just use the blue one")

    def test_minutes_with_the_clock_resting_while_he_decides(self) -> None:
        now = [1000.0]
        gate = threading.Event()
        self.addCleanup(gate.set)

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            gate.wait(10)
            held = engine.call("write_file", name="a.md", text="a")
            assert "reached its budget" in engine.text_of(held)
            engine.end_turn("Waiting.")
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script, agent={"max_minutes": 1}, clock=lambda: now[0],
                      timing=replace(FAST, init_s=1e9, question_s=1e9, keep_going_s=1e9, after_finish_s=1e9, wrap_up_s=1e9))
        self.until(lambda: self.of(ar.E_TOOL_END))
        now[0] += 61.0
        question = self.question()
        self.assertIn("That's 1 minute on this task", question.text)
        gate.set()
        self.until(lambda: run._idle)
        worked = run.budget.worked_s
        now[0] += 3600.0                                # an hour deciding
        self.assertAlmostEqual(run.budget.worked_s, worked)
        self.assertTrue(run.answer(question.id, "yes"))
        self.assertEqual(self.end().outcome, "done")
        self.assertLess(self.run_.ended.worked_s, 100)

    def test_a_turn_with_two_tool_calls_is_one_turn(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            message = engine.new_turn()
            engine.call("say", text="One.", message_id=message)
            engine.call("write_file", name="a.md", text="a", message_id=message)
            engine.call("finish", summary="Done.", outcome="done", message_id=message)
            engine.end_turn()

        self.go(script, agent={"max_turns": 5})
        end = self.end()
        self.assertEqual(end.turns, 2)                  # that message, and the last words
        self.assertEqual(self.of(ar.E_QUESTION), [])


class NoFinishTests(RunCase):
    def test_a_result_with_no_wait_and_no_finish_gets_one_prompt(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("write_file", name="a.md", text="a")
            engine.end_turn("I wrote it.")
            told = engine.wait_message()
            assert told == ar.CONTINUE_TEXT
            engine.call("finish", summary="Wrote it.", outcome="done", outputs=["a.md"])
            engine.end_turn()

        self.go(script)
        self.assertEqual(self.end().outcome, "done")
        self.assertIsNone(self.engine.failure)

    def test_then_it_ends_as_partial(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.end_turn("I wrote it.")
            engine.wait_message()
            engine.end_turn("Really, I did.")

        self.go(script)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary, end.own_words),
                         ("partial", ar.C_NO_FINISH, ar.NO_FINISH_WORDS, True))
        self.assertEqual(len(self.engine.sent), 2)      # the goal, and the one prompt
        self.assert_clean()

    def test_work_after_the_prompt_earns_another(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.end_turn("Thinking.")
            engine.wait_message()
            engine.call("write_file", name="a.md", text="a")
            engine.end_turn("More thinking.")
            engine.wait_message()
            engine.end_turn("Enough.")

        self.go(script)
        self.assertEqual(self.end().code, ar.C_NO_FINISH)
        self.assertEqual(self.engine.messages[1:], [ar.CONTINUE_TEXT, ar.CONTINUE_TEXT])

    def test_after_finish_it_is_given_a_moment_and_no_more(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            held = engine.call("say", text="And another thing.")
            assert ar.ALREADY_FINISHED_TEXT in engine.text_of(held)
            time.sleep(30)                              # never a last word

        self.go(script, timing=replace(FAST, after_finish_s=0.1))
        end = self.end()
        self.assertEqual((end.outcome, end.code), ("done", ar.C_FINISHED))
        self.assertTrue(self.engine.stdin_closed)
        self.assertEqual(self.of(ar.E_SAY), [])

    def test_it_ends_by_itself_after_finish(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="failed")
            raise SystemExit

        self.go(script)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("failed", ar.C_FINISHED, "Done."))


class LimitTests(RunCase):
    RESET = WHEN + timedelta(hours=2)

    def clocked(self, moment: datetime | None = None) -> list[datetime]:
        now = [moment or WHEN]
        self.wall = now
        return now

    def test_the_plan_s_limit_pauses_and_says_when_then_asks_at_the_reset(self) -> None:
        now = self.clocked()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("write_file", name="a.md", text="a")
            engine.emit(rate_limit_event(resets_at=self.RESET.timestamp()))
            engine.emit(result_event("You've hit your limit", is_error=True, api_error_status=429))
            told = engine.wait_message()
            assert told == ar.RESUME_YES
            engine.call("finish", summary="Done after the wait.", outcome="done", outputs=["a.md"])
            engine.end_turn()

        run = self.go(script, now=lambda: now[0])
        self.until(lambda: self.of(ar.E_NOTICE))
        notice = self.of(ar.E_NOTICE)[0]
        self.assertEqual(notice.code, "limit")
        self.assertIn("limit is reached", notice.text)
        self.assertIn("8:30 PM", notice.text)           # when
        self.assertIsNone(run.ended)
        self.until(lambda: run._idle)
        now[0] = self.RESET + timedelta(seconds=1)
        question = self.question()
        self.assertEqual(question.kind, ar.Q_RESUME)
        self.assertIn("has reset", question.text)
        self.assertTrue(run.answer(question.id, "Carry on"))
        end = self.end()
        self.assertEqual((end.outcome, end.summary), ("done", "Done after the wait."))
        self.assertIsNone(self.engine.failure)

    def test_no_at_the_reset(self) -> None:
        now = self.clocked()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(rate_limit_event(resets_at=self.RESET.timestamp()))
            engine.emit(result_event("limit", is_error=True, api_error_status=429))
            time.sleep(30)

        run = self.go(script, now=lambda: now[0])
        self.until(lambda: self.of(ar.E_NOTICE))
        now[0] = self.RESET + timedelta(seconds=1)
        run.answer(self.question().id, "no")
        end = self.end()
        self.assertEqual((end.outcome, end.code), ("partial", ar.C_OWNER_NO))
        self.assertEqual(len(self.engine.sent), 1)      # nothing could answer: no message
        self.assert_clean()

    def test_what_he_types_while_it_holds_goes_in_when_it_has_reset(self) -> None:
        now = self.clocked()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(rate_limit_event(resets_at=self.RESET.timestamp()))
            engine.emit(result_event("limit", is_error=True, api_error_status=429))
            engine.wait_message()
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script, now=lambda: now[0])
        self.until(lambda: self.of(ar.E_NOTICE))
        self.assertEqual(run.owner_says("use the blue one"), "held")
        self.assertEqual(len(self.engine.sent), 1)
        self.assertEqual(self.of(ar.E_NOTICE)[-1].code, "held")
        now[0] = self.RESET + timedelta(seconds=1)
        run.answer(self.question().id, "yes")
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(self.engine.messages[1:], [ar.RESUME_YES, "The owner says: use the blue one"])

    def test_no_time_given_he_says_when(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(result_event("You've hit your limit", is_error=True, api_error_status=429))
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        run = self.go(script)
        self.until(lambda: self.of(ar.E_NOTICE))
        self.assertEqual(self.of(ar.E_NOTICE)[0].text, ar.LIMIT_NO_TIME_WORDS)
        self.until(lambda: run._idle)
        self.assertEqual(run.owner_says("carry on now"), "sent")
        self.assertEqual(self.end().outcome, "done")

    def test_a_limit_text_with_its_time(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(result_event("You've hit your limit - resets 3pm (Europe/London)", is_error=True))
            time.sleep(30)

        self.go(script)
        self.until(lambda: self.of(ar.E_NOTICE))
        self.assertIn("resets 3pm (Europe/London)", self.of(ar.E_NOTICE)[0].text)

    def test_extra_usage_is_killed_at_once_and_paused(self) -> None:
        held: list[Any] = []

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(rate_limit_event(status="allowed", isUsingOverage=True, resets_at=self.RESET.timestamp()))
            time.sleep(30)

        self.go(script, on_overage=held.append)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("partial", ar.C_OVERAGE, ar.OVERAGE_WORDS))
        self.assertEqual(held[0].timestamp(), self.RESET.timestamp())
        self.assertTrue(self.engine.killed)

    def test_a_plain_allowed_signal_is_nothing(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(rate_limit_event(status="allowed"))
            engine.emit(rate_limit_event(status="allowed_warning"))
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        self.go(script)
        self.assertEqual(self.end().outcome, "done")
        self.assertEqual(self.of(ar.E_NOTICE), [])

    def test_stop_while_it_holds(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(rate_limit_event(resets_at=self.RESET.timestamp()))
            time.sleep(30)

        run = self.go(script, now=lambda: WHEN)
        self.until(lambda: self.of(ar.E_NOTICE))
        run.stop()
        self.assertEqual(self.end().outcome, ar.OUTCOME_STOPPED)


class GuardTests(RunCase):
    def assert_guard(self, reason: str) -> None:
        end = self.end()
        self.assertEqual((end.outcome, end.code), ("failed", f"guard:{reason}"))
        self.assertEqual(end.summary, ar.GUARD_WORDS)
        self.assertTrue(self.engine.killed)
        self.assert_clean()

    def idle(self, engine: FakeEngine) -> None:
        engine.wait_message()
        time.sleep(30)

    def test_init_with_an_extra_tool(self) -> None:
        names = [tool.name for tool in policy.offered()]
        self.go(self.idle, engine_kwargs={"tools": [*names, "extra"]})
        self.assert_guard("tools")

    def test_init_with_a_built_in_tool(self) -> None:
        self.go(self.idle, engine_kwargs={"init_changes": [{"tools": ["mcp__jarvis__say", "Bash"]}]})
        self.assert_guard("tools")

    def test_init_with_a_tool_missing(self) -> None:
        self.go(self.idle, engine_kwargs={"tools": ["say"]})
        self.assert_guard("tools")

    def test_init_with_skills(self) -> None:
        self.go(self.idle, engine_kwargs={"init_changes": [{"skills": ["pdf"]}]})
        self.assert_guard("skills")

    def test_init_with_the_server_not_connected(self) -> None:
        down = {"mcp_servers": [{"name": "jarvis", "status": "failed", "source": "dynamic"}]}
        self.go(self.idle, engine_kwargs={"init_changes": [down]})
        self.assert_guard("server_not_connected")

    def test_init_with_no_server_or_another_too(self) -> None:
        for servers in ([], [{"name": "jarvis", "status": "connected"}, {"name": "other", "status": "connected"}]):
            with self.subTest(servers=servers):
                self.setUp()
                self.go(self.idle, engine_kwargs={"init_changes": [{"mcp_servers": servers}]})
                self.assert_guard("mcp_servers")

    def test_init_with_an_api_key(self) -> None:
        self.go(self.idle, engine_kwargs={"init_changes": [{"apiKeySource": "ANTHROPIC_API_KEY"}]})
        self.assert_guard("api_key_source")

    def test_init_in_another_permission_mode(self) -> None:
        self.go(self.idle, engine_kwargs={"init_changes": [{"permissionMode": "bypassPermissions"}]})
        self.assert_guard("permission_mode")

    def test_every_init_is_checked_not_only_the_first(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("ask_owner", question="Blue or Red?")
            engine.end_turn("Asked.")
            engine.wait_message()
            time.sleep(30)

        run = self.go(script, engine_kwargs={"init_changes": [{}, {"skills": ["surprise"]}]})
        question = self.question()
        self.until(lambda: run._idle)
        run.answer(question.id, "Blue")
        self.assert_guard("skills")
        self.assertEqual(self.engine.inits, 2)

    def test_a_tool_use_outside_the_set(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.assistant([{"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": {"command": "dir"}}])
            time.sleep(30)

        self.go(script)
        self.assert_guard("tool_use")

    def test_a_tool_use_of_another_server(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.assistant([{"type": "mcp_tool_use", "id": "toolu_x", "name": "mcp__other__say", "input": {}}])
            time.sleep(30)

        self.go(script)
        self.assert_guard("tool_use")

    def test_seen_in_the_stream_first(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.chunk_start({"type": "tool_use", "id": "toolu_x", "name": "WebFetch", "input": {}})
            time.sleep(30)

        self.go(script)
        self.assert_guard("tool_use")

    def test_a_server_tool_outside_the_set(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.chunk_start({"type": "server_tool_use", "id": "srv_x", "name": "web_search", "input": {}})
            time.sleep(30)

        self.go(script)
        self.assert_guard("tool_use")

    def test_nothing_before_a_checked_init(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.assistant([{"type": "text", "text": "Hello."}])
            time.sleep(30)

        self.go(script)
        self.assert_guard("no_init")

    def test_a_result_before_any_init(self) -> None:
        self.go(lambda engine: (engine.emit(result_event("Hello.")), time.sleep(30)))
        self.assert_guard("no_init")

    def test_no_init_in_time(self) -> None:
        self.go(lambda engine: time.sleep(30), timing=replace(FAST, init_s=0.1))
        self.assert_guard("no_init")

    def test_the_check_itself_passes_the_real_shape(self) -> None:
        lines = (FIXTURES / "stream_sample.jsonl").read_text(encoding="ascii").splitlines()
        inits = [json.loads(line) for line in lines if '"subtype":"init"' in line]
        self.assertEqual(len(inits), 2)
        expected = frozenset(cli.AGENT_TOOL_PREFIX + name for name in SAMPLE_TOOLS)
        for event in inits:
            summary = check_agent_init(event, expected)
            self.assertEqual((summary.mcp_servers, summary.version, summary.permission_mode), (1, "2.1.294", "dontAsk"))
        with self.assertRaises(GuardTrip):
            check_agent_init(inits[0], expected - {cli.AGENT_TOOL_PREFIX + "finish"})
        with self.assertRaises(GuardTrip):
            check_agent_init({"type": "system", "subtype": "status"}, expected)
        with self.assertRaises(GuardTrip):
            check_agent_init(init_event(SAMPLE_TOOLS, skills=None), expected)


class EndingBadlyTests(RunCase):
    def test_it_ends_by_itself(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            raise SystemExit

        self.go(script)
        end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("failed", ar.C_EXIT, ar.EXIT_WORDS))

    def test_a_claude_code_that_does_not_know_a_flag(self) -> None:
        def script(engine: FakeEngine) -> None:
            raise SystemExit

        self.go(script, engine_kwargs={"stderr": "error: unknown option '--replay-user-messages'"})
        self.assertEqual(self.end().code, "cli_unsupported")

    def test_signed_out_mid_task(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(result_event("Please run /login", is_error=True, api_error_status=401))
            time.sleep(30)

        self.go(script)
        end = self.end()
        self.assertEqual((end.outcome, end.code), ("failed", "not_signed_in"))
        self.assertIn("claude auth login", end.summary)

    def test_a_failed_result(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(result_event("Something private went wrong", subtype="error_during_execution", is_error=True))
            time.sleep(30)

        self.go(script)
        end = self.end()
        self.assertEqual((end.outcome, end.summary), ("failed", ar.EXIT_WORDS))

    def test_the_backstop(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.emit(result_event("", subtype="error_max_turns", is_error=True))
            time.sleep(30)

        self.go(script)
        end = self.end()
        self.assertEqual((end.outcome, end.code), ("partial", ar.C_MAX_TURNS))

    def test_garbage_is_skipped(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.emit("not json at all")
            engine.emit("[1, 2]")
            engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn()

        self.go(script)
        self.assertEqual(self.end().outcome, "done")

    def test_a_line_too_long(self) -> None:
        class TooLong(FakeEngine):
            def read_line(self, timeout: float) -> str | None:
                if self.messages:
                    raise cli.OutputTooLong()
                return super().read_line(timeout)

        self.go(TooLong(lambda engine: (engine.wait_message(), time.sleep(30))))
        self.assertEqual(self.end().code, ar.C_GARBLED)

    def test_a_process_that_cannot_start(self) -> None:
        process = FakeEngine(lambda engine: None)
        run_runner = FakeEngineRunner(process)
        run_runner.start = mock.Mock(side_effect=OSError(2, "gone"))
        engine = agent_engine(self.root, run_runner)
        self.run_ = AgentRun(GOAL, engine=engine, registry=Registry(policy.offered()), folder=self.folder,
                             on_event=self.hear, timing=FAST)
        self.addCleanup(self.stop_it)
        self.run_.start()
        end = self.end()
        self.assertEqual(end.outcome, "failed")
        self.assertEqual(list(engine.runs_folder.rglob("*")), [])
        self.assertTrue(self.run_._endpoint.closed)

    def test_an_unexpected_error_in_the_run(self) -> None:
        run = self.go(whole_task)
        with mock.patch.object(run, "_on_init", side_effect=RuntimeError("private")):
            end = self.end()
        self.assertEqual((end.outcome, end.code, end.summary), ("failed", ar.C_INTERNAL, ar.INTERNAL_WORDS))
        self.assertNotIn("private", repr(end))


class ChecksBeforeAnyProcessTests(RunCase):
    def run_with(self, **runner: Any) -> ar.AgentEnd:
        process = FakeEngine(lambda engine: None)
        self.engine = process
        self.runner = FakeEngineRunner(process, **runner)
        self.agent_engine = agent_engine(self.root, self.runner)
        self.run_ = AgentRun(GOAL, engine=self.agent_engine, registry=Registry(policy.offered()), folder=self.folder,
                             on_event=self.hear, timing=FAST)
        self.addCleanup(self.stop_it)
        self.run_.start()
        return self.end()

    def test_signed_out(self) -> None:
        end = self.run_with(auth='{"loggedIn": false}')
        self.assertEqual((end.outcome, end.code), ("failed", "not_signed_in"))
        self.assertEqual(self.runner.started, [])

    def test_a_claude_code_without_a_flag_a_task_needs(self) -> None:
        from tests.ask_fakes import HELP_TEXT

        end = self.run_with(help_text=HELP_TEXT.replace("--replay-user-messages", "--replay-something"))
        self.assertEqual((end.outcome, end.code), ("failed", "cli_unsupported"))
        self.assertIn("--replay-user-messages", end.summary)
        self.assertEqual(self.runner.started, [])

    def test_a_work_folder_that_is_not_empty(self) -> None:
        folder = self.root / "data" / cli.AGENT_WORK_FOLDER
        folder.mkdir(parents=True)
        (folder / "CLAUDE.md").write_text("Planted.")
        end = self.run_with()
        self.assertEqual(end.outcome, "failed")
        self.assertIn("must be empty", end.summary)
        self.assertNotIn(str(self.root), end.summary)
        self.assertEqual(self.runner.started, [])

    def test_what_an_earlier_run_left_goes(self) -> None:
        stale = self.root / "data" / cli.AGENT_RUNS_FOLDER / "old"
        stale.mkdir(parents=True)
        (stale / "mcp.json").write_text("{}")
        self.go(whole_task)
        self.end()
        self.assertFalse(stale.exists())

    def test_the_guard_refuses_the_real_cli_under_the_tests(self) -> None:
        real = cli.SubprocessRunner()
        engine = ar.AgentEngine(AgentConfig(enabled=True), base_engine(self.root, real))
        self.run_ = AgentRun(GOAL, engine=engine, registry=Registry(policy.offered()), folder=self.folder,
                             on_event=self.hear, timing=FAST)
        self.addCleanup(self.stop_it)
        with mock.patch("subprocess.Popen") as popen, mock.patch("subprocess.run") as run_it:
            self.run_.start()
            end = self.end()
        popen.assert_not_called()
        run_it.assert_not_called()
        self.assertEqual((end.outcome, end.code), ("failed", ar.C_TEST_GUARD))
        self.assertIsNone(self.run_._endpoint)


if __name__ == "__main__":
    unittest.main()
