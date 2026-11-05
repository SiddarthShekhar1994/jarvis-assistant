"""Jarvis, a true agent, in the app: the controller of the one task that may run (SPEC 5, D4, R1, R8; part A1).

    AgentController(config, planner, live=, consent=, ...)     made by the app only while agent.policy.agent_on(config)
        offered()              may a first planner run hand a request to a task now? (any thread: on, the owner's
                               Turn on, no task running, tasks left today, Claude Code able, no extra-usage pause).
                               AskPlanner.agent_allowed asks it
        start(words)           one task, with the owner's own words (R1: only his request starts one). False and a
                               plain reason when it can't start
        take_request(text)     AskController's gate (D4): while a task runs, what is typed goes in as the owner's
                               message (the answer, when a question waits), or stops it ("stop", "cancel"). Nothing
                               runs: False
        answer(id, text)       the owner-question seam: the tests answer through it, and part A3 connects its card to it
        stop(reason)           ends the task at once (R8): the process is killed and anything late is dropped
        shutdown()             Jarvis closes: the task is killed and waited for, no process is left. Nothing starts
                               after it
    Signals (GUI thread):
        taskStarted(str)       a task started: its run id
        planChanged(object)    its plan now (tuple of tools.PlanStep; part A3's panel)
        said(str)              a line it said (the say tool): the app's caption and voice
        noticed(str, str)      Jarvis's own words about the task and their tone (paused by the plan's limit ...)
        acknowledged(str)      a few words that what was typed went in (shown, not said)
        questionAsked(object)  a runner.OwnerQuestion for the owner, its {sir} filled in
        questionGone(object)   it is not waiting any more (answered, or the task moved on)
        taskEnded(object)      a runner.AgentEnd, its {sir} filled in

Events come from the run's threads through a queued signal; a Stop drops what is still on its way. Live: a TASK_AGENT
task, with one step per tool call (the words in it stay on screen and in memory: D11), and it never opens LIVE by
itself. Logs: run ids, kinds, states and counts only, never what was typed.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal

from . import hud
from . import live as live_events
from .agent import policy
from .agent import runner as agent_runner
from .agent.runner import AgentEnd, AgentEngine, AgentEvent, AgentRun, OwnerQuestion, Timing
from .agent.tools import OUTCOME_DONE, OUTCOME_FAILED, Registry, Tool
from .agent.workspace import TaskFolder, WorkspaceRefused, workspace_root
from .ask.stream import LIMIT
from .persona import with_address

logger = logging.getLogger(__name__)

STOP_RE = re.compile(r"(?:jarvis[,:]?\s+)?(?:please\s+)?(?:stop|cancel)"
                     r"(?:\s+(?:it|that|this|the task|now|please))*[.!]?", re.IGNORECASE)
REQUEST_STEP = "Your request"
TOOLS_TEXT = "{count} - Jarvis's own, on this PC; nothing else"
BUDGET_TEXT = "{turns} turns and {minutes} minutes, then it asks you"
HELD_STATUS = "HELD"
RUNNING_TEXT = "A task is running already, {sir}; say stop first if you want another."
LIMIT_TEXT = "That's the most tasks for one day ({count}), {sir}; nothing was started."
HOLD_TEXT = "Tasks are paused until your plan's limit resets, {sir}: Claude Code said a run would use extra usage."
OFF_TEXT = "Tasks need Jarvis on your desktop turned on, {sir}; nothing was started."
TAKEN_TEXT = {"sent": "Passing that on, {sir}.", "answered": "Thank you, {sir}."}
END_STATUS = {OUTCOME_DONE: live_events.STATUS_OK, OUTCOME_FAILED: live_events.STATUS_FAILED,
              agent_runner.OUTCOME_STOPPED: live_events.STATUS_CANCELLED}
END_WORDS = {OUTCOME_DONE: "done", OUTCOME_FAILED: "failed", agent_runner.OUTCOME_STOPPED: "stopped"}
PARTLY = "partly done"
CODE_WORDS = {agent_runner.HELD: "held - the task is waiting or stopping",
              "too_long": "took too long and was given up on", "bad_input": "refused: its input did not fit",
              "rate": "refused: too soon after the last one", "raised": "failed", "refused": "refused"}


def stop_words(text: str) -> bool:
    """The owner's words are "stop" or "cancel" (D4), not a sentence that happens to use them."""
    return STOP_RE.fullmatch(" ".join((text or "").split())) is not None


class _Bridge(QObject):
    """Connected queued: the slot runs on the GUI thread."""

    event = Signal(int, object)      # the task's number, a runner.AgentEvent


class _Feed:
    """A task's LIVE task: quiet, as everything in Live is. Holds each tool call's step while it runs."""

    def __init__(self, live: Any, words: str, config: Any, folder_name: str, tools: int) -> None:
        self.task = live_events.NO_TASK
        self.steps: dict[int, Any] = {}
        self._make(live, words, config, folder_name, tools)

    @live_events.quiet()
    def _make(self, live: Any, words: str, config: Any, folder_name: str, tools: int) -> None:
        if live is None:
            return
        self.task = live.task(live_events.TASK_AGENT, words)
        agent = config.agent
        step = self.task.step(live_events.AGENT_REQUEST, REQUEST_STEP, fields=[
            ("You typed", words), ("Working in", folder_name), live_events.Field("Model", agent.model, mono=True),
            ("Budget", BUDGET_TEXT.format(turns=agent.max_turns, minutes=agent.max_minutes)),
            ("Tools", TOOLS_TEXT.format(count=tools))])
        step.done(live_events.STATUS_OK)

    @live_events.quiet()
    def started(self) -> None:
        self.task.note("Claude Code started: only Jarvis's own tools, checked")

    @live_events.quiet()
    def tool_start(self, call: int, title: str) -> None:
        self.steps[call] = self.task.step(live_events.AGENT_TOOL, title or "A tool", key=f"call:{call}")

    @live_events.quiet()
    def tool_end(self, call: int, result: Any) -> None:
        step = self.steps.pop(call, None)
        if step is None or result is None:
            return
        for label, value in result.fields:
            step.field(label, value)
        if result.code == agent_runner.HELD:
            step.done(live_events.STATUS_WARN, status_text=HELD_STATUS, summary=result.note or CODE_WORDS[result.code])
        elif result.is_error:
            step.done(live_events.STATUS_WARN, summary=result.note or CODE_WORDS.get(result.code, result.code))
        else:
            step.done(live_events.STATUS_OK, summary=result.note)

    @live_events.quiet()
    def note(self, text: str) -> None:
        if self.task.keep_text:
            self.task.note(text)

    @live_events.quiet()
    def asked(self, question: OwnerQuestion) -> None:
        if question.kind != agent_runner.Q_TASK:   # the ask_owner tool has its own step
            step = self.task.step(live_events.AGENT_TOOL, "Asked you", fields=[("Question", question.text)])
            step.done(live_events.STATUS_WARN, status_text="WAITING", summary="waiting for you")

    @live_events.quiet()
    def you_said(self, status: str) -> None:
        self.task.note({"answered": "You answered", "held": "Your message waits for the plan's limit",
                        "sent": "Your message went in"}.get(status, "Your message came too late"))

    @live_events.quiet()
    def ended(self, end: AgentEnd) -> None:
        for step in self.steps.values():
            step.done(live_events.STATUS_CANCELLED, summary="dropped")
        self.steps.clear()
        words = END_WORDS.get(end.outcome, PARTLY)
        step = self.task.step(live_events.AGENT_END, "How it ended", fields=[
            ("Outcome", words), ("Why", end.code), ("Turns", str(end.turns)), ("Tool calls", str(end.tools)),
            ("Worked", live_events.elapsed_text(end.worked_s)),
            ("Tokens", f"{end.input_tokens:,} in / {end.output_tokens:,} out")])
        for name in end.outputs:
            step.item(name)
        status = END_STATUS.get(end.outcome, live_events.STATUS_WARN)
        step.done(status, summary=end.summary[:live_events.SUMMARY_CAP])
        self.task.finish(status, summary=f"{words} - {end.tools} tool call(s)")


class AgentController(QObject):
    """See the module docs."""

    taskStarted = Signal(str)
    planChanged = Signal(object)
    said = Signal(str)
    noticed = Signal(str, str)
    acknowledged = Signal(str)
    questionAsked = Signal(object)
    questionGone = Signal(object)
    taskEnded = Signal(object)

    def __init__(self, config: Any, planner: Any, *, live: Any = None, consent: Callable[[], bool] = lambda: False,
                 environ: Mapping[str, str] | None = None, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now().astimezone(), timing: Timing = Timing(),
                 tools: Sequence[Tool] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.planner = planner
        self.live = live
        self._consent = consent
        self._environ = environ
        self._clock = clock
        self._now = now
        self.timing = timing
        self._tools = tools
        self._engine = AgentEngine(config.agent, planner.engine)
        self.tasks = policy.TaskLog(config.data_dir / policy.TASKS_FILE, config.agent.tasks_per_day, now)
        self._address = str(getattr(getattr(config, "assistant", None), "address", "sir"))
        self._bridge = _Bridge(self)
        self._bridge.event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self._run: AgentRun | None = None
        self._seq = 0
        self._feed: _Feed | None = None
        self._stopping = False
        self._shut = False
        self.last_end: AgentEnd | None = None
        self.last_folder: TaskFolder | None = None

    # ---- what the owner and the router ask ----------------------------------------------------------

    def _words(self, text: str) -> str:
        return with_address(text, self._address)

    @property
    def running(self) -> bool:
        return self._run is not None

    def run(self) -> AgentRun | None:
        return self._run

    def offered(self) -> bool:
        """Any thread (the ask thread asks before each first run). Fail-closed."""
        try:
            if (self._shut or self._run is not None or not policy.agent_on(self.config) or self._consent() is not True
                    or not self.tasks.allowed() or self.planner.usage.hold_until() is not None):
                return False
            ready = self._engine.base.readiness()
            return bool(ready.ok) and not ready.agent_missing
        except Exception as exc:  # noqa: BLE001 - unknown: not offered
            logger.warning("Agent: could not tell whether a task may start (%s)", type(exc).__name__)
            return False

    def start(self, words: str) -> bool:
        """One task with the owner's own words. False: it did not start, and ``noticed`` says why."""
        words = " ".join((words or "").split())
        if self._shut or not words:
            return False
        refused = self._refusal()
        if refused:
            logger.info("Agent: a task was not started")
            self.noticed.emit(refused, hud.TONE_ERROR)
            return False
        try:
            root = workspace_root(self.config.agent.workspace, self._environ)
            folder = TaskFolder.create(root, words, self._now())
        except WorkspaceRefused as why:
            logger.info("Agent: a task was not started (%s)", why.code)
            self.noticed.emit(self._words(why.text), hud.TONE_ERROR)
            return False
        self._seq += 1
        seq = self._seq
        registry = Registry(self._tools if self._tools is not None else policy.offered(self.config))
        self._feed = _Feed(self.live, words, self.config, folder.path.name, len(registry))
        self._stopping = False
        self.last_folder = folder

        def on_event(event: AgentEvent, seq: int = seq) -> None:
            try:
                self._bridge.event.emit(seq, event)
            except RuntimeError:
                pass                      # the bridge went with the app: nothing to tell

        run = AgentRun(words, engine=self._engine, registry=registry, folder=folder, on_event=on_event,
                       clock=self._clock, now=self._now, timing=self.timing, on_overage=self._pause_for_overage)
        self._run = run
        logger.info("Agent %s: a task starts", run.run_id)
        run.start()
        self.taskStarted.emit(run.run_id)
        return True

    def _refusal(self) -> str:
        if self._run is not None:
            return self._words(RUNNING_TEXT)
        if not policy.agent_on(self.config) or self._consent() is not True:
            return self._words(OFF_TEXT)
        if not self.tasks.allowed():
            return self._words(LIMIT_TEXT.replace("{count}", str(self.tasks.per_day)))
        if self.planner.usage.hold_until() is not None:
            return self._words(HOLD_TEXT)
        return ""

    def take_request(self, text: str) -> bool:
        """AskController's gate (D4). True: it was taken - the task stops, or the words went in as the owner's."""
        run = self._run
        if run is None or run.ended is not None:
            return False
        if stop_words(text):
            self.stop("typed")
            return True
        status = run.owner_says(text)
        if status == "refused":
            return False
        if self._feed is not None:
            self._feed.you_said(status)
        if status in TAKEN_TEXT:
            self.acknowledged.emit(self._words(TAKEN_TEXT[status]))
        return True

    def answer(self, question_id: str, text: str) -> bool:
        """The owner-question seam. False: that question is not waiting any more."""
        run = self._run
        return run.answer(question_id, text) if run is not None else False

    def stop(self, reason: str = "stop") -> None:
        run = self._run
        if run is None:
            return
        self._stopping = True             # what is still on its way to this thread is dropped
        run.stop(reason)

    def shutdown(self) -> None:
        """Jarvis closes: nothing starts after this, the process is gone and the task stamped."""
        self._shut = True
        run = self._run
        if run is None:
            return
        self._stopping = True
        run.stop("closing")
        if not run.join(5.0):
            logger.warning("Agent %s: the run's thread did not end in time", run.run_id)
        self._end(run.ended)

    def _pause_for_overage(self, resets_at: datetime | None) -> None:
        self.planner.pause_for_overage(resets_at)

    # ---- the run's events, on the GUI thread ------------------------------------------------------------

    def _on_event(self, seq: int, event: AgentEvent) -> None:
        if seq != self._seq or self._run is None:
            return
        if self._stopping and event.kind != agent_runner.E_ENDED:
            return
        kind, feed = event.kind, self._feed
        if kind == agent_runner.E_ENDED:
            self._end(event.end)
        elif feed is None:
            return
        elif kind == agent_runner.E_STARTED:
            self.tasks.record()           # it counts once Claude Code really started with what was checked
            feed.started()
        elif kind == agent_runner.E_TOOL_START:
            tool = self._run.registry.tool(event.tool)
            feed.tool_start(event.call, tool.title if tool is not None else event.tool)
        elif kind == agent_runner.E_TOOL_END:
            feed.tool_end(event.call, event.result)
        elif kind == agent_runner.E_PLAN:
            self.planChanged.emit(event.steps)
        elif kind == agent_runner.E_SAY:
            self.said.emit(event.text)
        elif kind == agent_runner.E_TEXT:
            feed.note(event.text)
        elif kind == agent_runner.E_NOTICE:
            feed.note(self._words(event.text))
            self.noticed.emit(self._words(event.text), hud.TONE_WARN if event.code == LIMIT else hud.TONE_DONE)
        elif kind == agent_runner.E_QUESTION and event.question is not None:
            question = replace(event.question, text=self._words(event.question.text))
            feed.asked(question)
            self.questionAsked.emit(question)
        elif kind == agent_runner.E_QUESTION_GONE:
            self.questionGone.emit(event.question)

    def _end(self, end: AgentEnd | None) -> None:
        """The task ended: Live is stamped and the app told. Once."""
        run, self._run = self._run, None
        if run is None or end is None:
            return
        end = replace(end, summary=self._words(end.summary)) if end.own_words else end
        self.last_end = end
        if self.last_folder is not None:
            self.last_folder.discard_if_empty()   # a task that wrote nothing leaves nothing
        feed, self._feed = self._feed, None
        if feed is not None:
            feed.ended(end)
        self.taskEnded.emit(end)
