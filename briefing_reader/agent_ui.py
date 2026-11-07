"""Jarvis, a true agent, in the app: the controller of the one task that may run (SPEC 5, D4, R1, R8; parts A1, A2).

    AgentController(config, planner, live=, consent=, ...)     made by the app only while agent.policy.agent_on(config)
        offered()              may a first planner run hand a request to a task now? (any thread: on, the owner's
                               Turn on, no task running, Claude Code able to plan, no extra-usage pause).
                               AskPlanner.agent_allowed asks it. The day's tasks used up, or a Claude Code too old for
                               tasks, still offer it: start() then says so, in words, where a plain Ask could not know
        refusal()              why a task can't start now, in Jarvis's own words; "" when it can
        start(words)           one task, with the owner's own words (R1: only the owner's request starts one). False and
                               a plain reason when it can't start
        take_request(text)     AskController's gate (D4): while a task runs, what is typed goes in as the owner's
                               message (the answer, when a question waits; its own choice's word is that answer, whatever
                               the word, but a stop word only once a task), or stops it ("stop", "cancel", with what is
                               said around them: stop_words).
                               While a card only a click answers waits, the bar says so (clickNeeded). Nothing runs: False
        answer(id, text)       the owner-question seam: a card's click and the tests answer through it
        stop(reason)           ends the task at once (R8): the process is killed and anything late is dropped.
                               ``stopped_by`` says so until the next task: the voice says nothing more of it (the app)
        set_window_hidden(h), set_window_away(a), window_hidden(), window_rect(), keep_window_clear(), close_window()
                               the watch window, for the feed (agent_feed): the owner's Hide and Show, the Jarvis window
                               coming back, where it is. The page goes on behind it
        worked_s(), tools_running(), live_task_id()   for the panel's clock and activity line, and Home's chip
        set_window_resting(r)  a file card waits (agent_feed): the watch window is off the screen meanwhile
        spoken(line, instead=), spoken_question(text), spoken_report(end, opening)
                               the one gate on the way to the voice for a line the model wrote (the module docs)
        outputs_for(end), show_output(name), output_path(name)   the result card's Show (the sealed window) and Folder
        shutdown()             Jarvis closes: the task is killed and waited for, no process is left. Nothing starts
                               after it
    Signals (GUI thread):
        taskStarted(str)       a task started: its run id
        planChanged(object)    its plan now (tuple of tools.PlanStep; agent_feed's panel)
        activityChanged(str)   Jarvis's own few words for what it does now (tools.doing_words): on screen only
        stepEnded()            a tool call ended
        said(str)              a line it said (the say tool): the app's caption and voice
        noticed(str, str)      Jarvis's own words about the task and their tone (paused by the plan's limit ...)
        acknowledged(str)      a few words that what was typed went in (shown, not said)
        clickNeeded(str)       words came while a card only a click answers waits ("sent" or "held"): the feed says so
        cardAtWork(bool)       the owner's click started a file card's batch (False: it ran nothing, and the card waits on)
        limitPaused(bool)      the plan's limit holds the task now (False: it let go)
        questionAsked(object)  a runner.OwnerQuestion for the owner, its {sir} filled in
        questionGone(object)   it is not waiting any more (answered, or the task moved on)
        questionExpired(object)  it timed out (just before questionGone)
        windowHidden(bool)     the strip's Hide was clicked
        windowPlaced()         the watch window came or moved
        taskEnded(object)      a runner.AgentEnd, its {sir} filled in

Events come from the run's threads through a queued signal; a Stop drops what is still on its way. Live: a TASK_AGENT
task, with one step per tool call (the words in it stay on screen and in memory: D11), a web step with the picture of
the page in the window on it, and it never opens LIVE by itself. Logs: run ids, kinds, states and counts only, never
what was typed, searched for or read.

The voice: every line the model wrote (the plan's "found", say, a question, the report's opening) comes to spoken()
before it reaches the voice, whose words go to Microsoft's online service as an Ask's answers do. Such a line goes on
only while the run holds nothing private, or when it has no tainted word in it, no key, email address or long number of
its own and, in a strict run, no foreign word (WebFirewall.speakable); else it stays on the caption and on its card,
unsaid, with Jarvis's own fixed words in its
place for a question or a report. [agent] speak_private = true skips the gate. Jarvis's own fixed lines never come here.

The owner's things (part A4): ``things`` (AppThings, made by the app) is what a run's agent.owner.OwnerAccess reads and
shows through, on the right threads: Google's objects on the tool's own, files on the "desktop" thread, whatever shows
on the GUI thread. What is connected and allowed decides what a run is offered (policy.offered). A card of file steps a
run waits on tells it how it ended (DesktopController.tidyEnded). A Stop or a Turn off withdraws the run's undecided
cards at once: a card of file steps goes (one at work stops after the row on its way, with its stamp and Undo), and its
Ask cards leave Approvals unless they were decided or are on their way. At a natural end its Ask cards stay: the report
says what is waiting.

The web (part A2): each run has its own browser client (agent.browser), session and firewall. The window comes only with
the first web or show step; when a run ends its page goes at once and the window a little later, or when the result card
goes (``hold_window``), at once after a Stop or a Turn off; a new run first ends what an older one left, and shutdown
leaves nothing. Chromium itself starts as the task starts (snapshot_engine.warm_up), before the switch and the panel
move: its first start blocks the GUI thread for a second or more, and in the middle of a task it would freeze them.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal

from . import hud
from . import live as live_events
from . import pictures, snapshot_engine
from .agent import fetch as agent_fetch
from .agent import firewall as agent_firewall
from .agent import owner as agent_owner
from .agent import policy
from .agent import runner as agent_runner
from .agent import web
from .agent.browser import BrowserClient
from .agent.fetch import Fetcher
from .agent.runner import AgentEnd, AgentEngine, AgentEvent, AgentRun, OwnerQuestion, Timing
from .agent.tools import ERROR_CODES, OUTCOME_DONE, OUTCOME_FAILED, OUTCOME_PARTIAL, Registry, Tool
from .agent.web import WebSession
from .agent.workspace import TaskFolder, WorkspaceRefused, check_name, workspace_root
from .ask.stream import GUARD, LIMIT
from .persona import with_address

logger = logging.getLogger(__name__)

# "Stop" or "cancel" with what is said around them, on words with their commas and capitals gone (plain_words): a whole
# message of these. A sentence that merely holds the word ("stop at the second shop", "don't stop looking") is not one.
STOP_RE = re.compile(r"(?:(?:ok|okay|hey|hi|oh|um|uh|right|no|wait|please|jarvis) )*(?:stop|cancel)(?: (?:stop|cancel))*"
                     r"(?: (?:it|that|this|this task|the task|now|right now|here|there|please|jarvis|thanks|thank you))*")
STOP_CAP = 80                     # longer than this is not a bare "stop"
REQUEST_STEP = "Your request"
TOOLS_TEXT = "{count} - Jarvis's own, on this PC; nothing else"
BUDGET_TEXT = "{turns} turns and {minutes} minutes, then it asks you"
HELD_STATUS = "HELD"
WAITING_STATUS = "WAITING"
FILE_NOTE = "Opened on this PC from the task's own folder"
RUNNING_TEXT = "A task is running already, {sir}; say stop first if you want another."
LIMIT_TEXT = "That's the most tasks for one day ({count}), {sir}; nothing was started."
HOLD_TEXT = "Tasks are paused until your plan's limit resets, {sir}: Claude Code said a run would use extra usage."
OFF_TEXT = "Tasks need Jarvis on your desktop turned on, {sir}; nothing was started."
TOO_OLD_TEXT = "This Claude Code is too old for tasks, {sir}: update it and ask again. Nothing was started."
TAKEN_TEXT = {"sent": "Passing that on, {sir}.", "answered": "Thank you, {sir}."}
TURNED_OFF = "turned off"         # stop()'s reason for a Turn off: nothing stays, even a report
STOPPED_BY_OWNER = "stopped"      # AgentController.stopped_by: their Stop, in the words of any end (else TURNED_OFF)
# What is said in place of a line of the model's that may hold something private (AgentController.spoken).
QUESTION_LINE = "I've a question for you, {sir}. It's on the card."
REPORT_LINES = {OUTCOME_DONE: "That's done, {sir}. It's on the card.",
                OUTCOME_PARTIAL: "That's partly done, {sir}. It's on the card.",
                OUTCOME_FAILED: "That failed, {sir}. It's on the card.",
                agent_runner.OUTCOME_STOPPED: "Stopped, {sir}. It's on the card."}
END_STATUS = {OUTCOME_DONE: live_events.STATUS_OK, OUTCOME_FAILED: live_events.STATUS_FAILED,
              agent_runner.OUTCOME_STOPPED: live_events.STATUS_CANCELLED}
END_WORDS = {OUTCOME_DONE: "done", OUTCOME_FAILED: "failed", agent_runner.OUTCOME_STOPPED: "stopped"}
PARTLY = "partly done"
LIMIT_ACTIVITY = agent_runner.LIMIT_ACTIVITY
WENT_WELL = (agent_runner.HOW_ALLOWED, agent_runner.HOW_ANSWERED, agent_runner.HOW_KEEP_GOING, agent_runner.HOW_CARRY_ON)
MAX_LISTED = 20                   # files a result card lists when the model did not name them
HOP_S = 10.0                      # what a tool waits for the GUI thread at most, for something to show
CODE_WORDS = {agent_runner.HELD: "held - the task is waiting or stopping",
              "too_long": "took too long and was given up on", "bad_input": "refused: its input did not fit",
              "rate": "refused: too soon after the last one", "raised": "failed", "refused": "refused",
              agent_firewall.DECLINED: "you did not allow it", agent_firewall.CARD_BUSY: "a question waits already",
              web.W_NOT_PUBLIC: "refused: not a public https page", web.W_NO_PAGE: "no page is open",
              web.W_REFUSED: "the site refuses automated browsers", web.W_HIDDEN: "you hid the window: no picture now",
              agent_fetch.W_FETCH_CAP: "that was the most for one task",
              agent_fetch.W_FETCH_FAILED: "it could not be read",
              agent_fetch.W_FETCH_GUARD: "stopped: it went beyond the one address",
              web.W_NO_RESULTS: "no search page gave results", web.W_NOT_LOADED: "the page did not load",
              web.W_TOO_SLOW: "it took too long", web.W_NO_ANSWER: "the page stopped answering",
              web.W_STOPPED: "stopped", web.W_BUSY: "the window was busy",
              web.W_NO_NUMBER: "no address has that number",
              web.W_CRASHED: "the page crashed", web.W_NO_ENGINE: "this PC has no web engine",
              web.W_TOO_BIG: "the picture would not fit", web.W_NO_WINDOW: "the window is not available",
              web.W_TEST_RUN: "no page is opened in a test run"}


def plain_words(text: str) -> str:
    """Typed or dictated words with their punctuation and capitals gone (an apostrophe stays: "don't" is its own word)."""
    return " ".join(re.sub(r"[^\w']+", " ", text or "").split()).casefold()


def stop_words(text: str) -> bool:
    """The owner's words are "stop" or "cancel" (D4), as they come typed or dictated ("Stop, please.", "Okay, stop."), not
    a sentence that happens to use them, nor a question ("stop?")."""
    plain = plain_words(text)
    return "?" not in (text or "") and len(plain) <= STOP_CAP and STOP_RE.fullmatch(plain) is not None


def tokens_text(end: AgentEnd) -> str:
    """Live's Tokens row: all the input the model was given, cached or not, and its output. Never a cost."""
    given = end.input_tokens + end.cache_read_tokens + end.cache_write_tokens
    cached = f" ({end.cache_read_tokens:,} from cache)" if end.cache_read_tokens else ""
    return f"{given:,} in{cached} / {end.output_tokens:,} out"


class _Bridge(QObject):
    """Connected queued: the slot runs on the GUI thread."""

    event = Signal(int, object)      # the task's number, a runner.AgentEvent


class _Hop(QObject):
    """A tool's thread has something done on the GUI thread, and waits for it (a little while: ``HOP_S``)."""

    job = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.job.connect(self._on_job, Qt.ConnectionType.QueuedConnection)

    def _on_job(self, job: Callable[[], None]) -> None:
        job()

    def run(self, work: Callable[[], Any], default: Any = None) -> Any:
        if QThread.currentThread() is self.thread():
            return work()
        future: Future = Future()

        def job() -> None:
            if not future.set_running_or_notify_cancel():
                return                # nobody waits any more: it does nothing
            try:
                future.set_result(work())
            except Exception as exc:  # noqa: BLE001 - it goes to the thread that asked
                future.set_exception(exc)

        try:
            self.job.emit(job)
        except RuntimeError:
            return default            # the app is going
        try:
            return future.result(timeout=HOP_S)
        except FutureTimeout:
            future.cancel()           # still queued: it must not happen later, when it has been said not to
            return default


class AppThings:
    """What the app gives a task of the owner's things (agent.owner.Things), on the right threads: the planner's sources
    and notes as Ask reads them, the conversation and the cards on the GUI thread, files on the "desktop" thread."""

    def __init__(self, planner: Any, desktop: Any, *, conversation: Callable[[], Any],
                 take_cards: Callable[[Any], None], withdraw_cards: Callable[[Any], None],
                 briefing: Callable[[], Any] = lambda: None,
                 parent: QObject | None = None) -> None:
        self.planner = planner
        self.desktop = desktop
        self.config = planner.config
        self.sources = planner.sources
        self._conversation = conversation
        self._take_cards = take_cards
        self._withdraw_cards = withdraw_cards
        self._briefing = briefing
        self._hop = _Hop(parent)

    def listen(self, slot: Callable[[Any, str], None]) -> None:
        self.desktop.tidyEnded.connect(slot)

    def listen_at_work(self, slot: Callable[[Any, bool], None], progress: Callable[[Any], None]) -> None:
        self.desktop.tidyAtWork.connect(slot)
        self.desktop.tidyProgress.connect(progress)

    def connected(self) -> frozenset[str]:
        """What may be offered now, asked of this PC only (GUI thread, as a task starts). Unknown: not connected."""
        found = {"desk", "files", "conversation"}
        try:
            ask = getattr(self.sources, "connected", None)
            if callable(ask):
                calendar, mail, send = ask()
            else:
                accounts = self.sources.accounts()
                calendar = any(account.calendar == "yes" for account in accounts)
                mail = any(account.read_mail == "yes" for account in accounts)
                send = any(account.send_mail for account in accounts)
        except Exception as exc:  # noqa: BLE001 - unknown: not connected
            logger.warning("Agent: what is connected could not be read (%s)", type(exc).__name__)
            calendar = mail = send = False
        found |= {name for name, on in (("calendar", calendar), ("mail", mail), ("contacts", calendar or mail),
                                        ("actions", calendar or send), ("memory", self.planner.memory is not None))
                  if on}
        return frozenset(found)

    def ready(self) -> None:
        self.desktop.task_ready()

    def consent(self) -> bool:
        return self.desktop.consent_for_tasks()

    def now(self) -> datetime:
        return self.planner.clock()

    def memory(self) -> Sequence[str]:
        source = self.planner.memory
        return tuple(source()) if source is not None else ()

    def conversation(self) -> Sequence[Any]:
        return self._hop.run(self._conversation, ()) or ()

    def briefing(self) -> Any:
        return self._hop.run(self._briefing)

    def job(self, method: str, *args: Any, stop: threading.Event, **kwargs: Any) -> Future:
        return self.desktop.task_job(method, *args, stop=stop, **kwargs)

    def before_open(self) -> None:
        self._hop.run(self.desktop.task_before_open)

    def show_run(self, run: Any, cancelled: Callable[[], bool] = lambda: False) -> bool:
        """Asked again on the GUI thread, where a Stop runs: a run stopped on the way here shows nothing (R8)."""
        return bool(self._hop.run(lambda: not cancelled() and self.desktop.task_show(run), False))

    def hud_windows(self) -> Sequence[int]:
        return self._hop.run(self.desktop._hud_windows, ()) or ()              # noqa: SLF001 - its own controller's

    def pc_done(self, asked: Any, run: Any) -> None:
        self._hop.run(lambda: self.desktop.task_pc_done(asked, run))

    def take_cards(self, cards: Sequence[Any], cancelled: Callable[[], bool] = lambda: False) -> bool:
        def take() -> bool:
            if cancelled():                # as show_run: a run stopped on the way here takes no card
                return False
            self._take_cards(list(cards))
            return True

        return bool(self._hop.run(take, False))

    def withdraw(self, run_ids: Sequence[str], card_ids: Sequence[str], *, cards: bool) -> None:
        """GUI thread: a task's undecided cards go (the module docs)."""
        self.desktop.withdraw_task(run_ids, cards=cards)
        if card_ids:
            self._withdraw_cards(card_ids)


class _Feed:
    """A task's LIVE task: quiet, as everything in Live is. Holds each tool call's step while it runs."""

    def __init__(self, live: Any, words: str, config: Any, folder_name: str, tools: int) -> None:
        self.task = live_events.NO_TASK
        self.steps: dict[int, Any] = {}
        self.wait_step: Any = None        # the step of the wait that is on (WAITING): it says how it went when it ends
        self.waiting = False
        self.how_came = ""                # it ended before its tool's own step did
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
        self._picture(step, result.live_picture)
        if result.code == agent_runner.HELD:
            step.done(live_events.STATUS_WARN, status_text=HELD_STATUS, summary=result.note or CODE_WORDS[result.code])
        elif result.code == agent_firewall.WAITING:
            step.done(live_events.STATUS_WARN, status_text=WAITING_STATUS, summary=result.note)
            if self.waiting and self.wait_step is None:
                self.wait_step = step
        elif result.is_error:
            words = CODE_WORDS.get(result.code) or ERROR_CODES.get(result.code, result.code)
            step.done(live_events.STATUS_WARN, summary=result.note or words)
        else:
            step.done(live_events.STATUS_WARN if result.warn else live_events.STATUS_OK, summary=result.note)
        how, self.how_came = self.how_came, ""
        if how and result.code == agent_firewall.WAITING:
            self.wait_step = None
            self._stamp(step, how)
        elif how:
            self.task.note(how)

    @staticmethod
    def _picture(step: Any, shot: Any) -> None:
        """A web step's picture, grabbed from the window (the model never gets it unless it asked to look)."""
        if shot is None:
            return
        address, taken = shot
        picture = pictures.page_ready(address, image=taken.image, image_format=taken.image_format, width=taken.width,
                                      height=taken.height, final_url=taken.final_url, took_ms=taken.took_ms,
                                      blocked=taken.blocked)
        if not address.startswith("https://"):     # a file of the task's own folder (show_file)
            name = live_events.clean_line(address, 120)
            picture = replace(picture, caption=f"File: {name}", note=FILE_NOTE, alt=f"Picture of the file {name}")
        step.picture(picture)

    @live_events.quiet()
    def note(self, text: str) -> None:
        if self.task.keep_text:
            self.task.note(text)

    @live_events.quiet()
    def asked(self, question: OwnerQuestion) -> None:
        self.waiting, self.wait_step, self.how_came = True, None, ""
        if question.kind not in (agent_runner.Q_TASK, agent_runner.Q_WEB, agent_runner.Q_CARD):   # their tools' steps
            step = self.task.step(live_events.AGENT_TOOL, "Asked you", fields=[("Question", question.text)])
            step.done(live_events.STATUS_WARN, status_text="WAITING", summary="waiting for you")
            self.wait_step = step

    @live_events.quiet()
    def gone(self, how: str) -> None:
        """A wait ended: the step that waited says how it went, in Jarvis's own words (a question's own step said only
        that it was asked: a note says it)."""
        step, self.wait_step = self.wait_step, None
        self.waiting = False
        if not how:
            return
        if step is not None:
            self._stamp(step, how)
        elif self.steps:
            self.how_came = how           # its tool's own step is still on its way
        else:
            self.task.note(how)

    @staticmethod
    def _stamp(step: Any, how: str) -> None:
        good = how in WENT_WELL or how.startswith(agent_owner.CARD_HOW_DONE)
        step.done(live_events.STATUS_OK if good else live_events.STATUS_WARN, summary=how)

    @live_events.quiet()
    def you_said(self, status: str, click: bool = False) -> None:
        """What was typed. "answered": gone() says so, for a click or for words."""
        if status != "answered":
            self.task.note({"held": "Your message waits", "sent": "Your message went in"}.get(
                status, "Your message came too late") + (": the card still needs a click" if click else ""))

    @live_events.quiet()
    def ended(self, end: AgentEnd) -> None:
        for step in self.steps.values():
            step.done(live_events.STATUS_CANCELLED, summary="dropped")
        self.steps.clear()
        if self.wait_step is not None:
            self.gone(agent_runner.HOW_ENDED)
        words = END_WORDS.get(end.outcome, PARTLY)
        step = self.task.step(live_events.AGENT_END, "How it ended", fields=[
            ("Outcome", words), ("Why", end.code), ("Turns", str(end.turns)), ("Tool calls", str(end.tools)),
            ("Worked", live_events.elapsed_text(end.worked_s)),
            ("Tokens", tokens_text(end))])
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
    clickNeeded = Signal(str)
    cardAtWork = Signal(bool)
    limitPaused = Signal(bool)
    questionAsked = Signal(object)
    questionGone = Signal(object)
    taskEnded = Signal(object)
    activityChanged = Signal(str)
    stepEnded = Signal()
    questionExpired = Signal(object)
    windowHidden = Signal(bool)
    windowPlaced = Signal()

    def __init__(self, config: Any, planner: Any, *, live: Any = None, consent: Callable[[], bool] = lambda: False,
                 tasks_consent: Callable[[], bool] | None = None,
                 environ: Mapping[str, str] | None = None, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now().astimezone(), timing: Timing = Timing(),
                 tools: Sequence[Tool] | None = None, parent: QObject | None = None,
                 window_avoid: Callable[[], Any] | None = None,
                 browser_factory: Callable[..., Any] | None = None, things: Any = None) -> None:
        super().__init__(parent)
        self.config = config
        self.planner = planner
        self.live = live
        self._consent = consent           # the owner's Turn on: a first planner run may offer a task
        self._tasks_consent = tasks_consent or consent   # ... the card that lists tasks: a task may start
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
        self._window_avoid = window_avoid
        self._browser_factory = browser_factory
        self._browser: Any = None         # this run's (or, while its window lingers, the last run's) browser client
        self.hold_window = False          # a window that lingers stays until close_window (the result card's)
        self._away = False                # the Jarvis window is back: the watch window stays off the screen
        self.firewall: agent_firewall.WebFirewall | None = None
        self._things = things             # the owner's things (AppThings; None: a task reaches none of them)
        self._owner: agent_owner.OwnerAccess | None = None
        if things is not None:
            things.listen(self._on_card_ended)
            things.listen_at_work(self._on_card_at_work, self._on_card_progress)
        self._too_old = False             # this Claude Code plans, but lacks what a task needs (offered says)
        self._run: AgentRun | None = None
        self._seq = 0
        self._feed: _Feed | None = None
        self._stopping = False
        self._shut = False
        self.stopped_by = ""              # this task: the owner's Stop or Turn off came (TURNED_OFF, STOPPED_BY_OWNER)
        self._stop_chosen = False         # this task: a stop word has answered a question that offered it (take_request)
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
                    or self.planner.usage.hold_until() is not None):
                return False
            ready = self._engine.base.readiness()
            self._too_old = bool(ready.agent_missing)
            return bool(ready.ok)
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
        things = self._things
        connected = things.connected() if things is not None else frozenset()
        registry = Registry(self._tools if self._tools is not None
                            else policy.offered(self.config, connected=connected))
        session = self._new_session()
        self.firewall = agent_firewall.WebFirewall(session.book)
        owner = None
        if things is not None:
            things.ready()
            owner = agent_owner.OwnerAccess(things, self.firewall, workspace=root)
        self._owner = owner
        self._feed = _Feed(self.live, words, self.config, folder.path.name, len(registry))
        self._stopping = False
        self.stopped_by = ""
        self.last_folder = folder
        self._stop_chosen = False

        def on_event(event: AgentEvent, seq: int = seq) -> None:
            try:
                self._bridge.event.emit(seq, event)
            except RuntimeError:
                pass                      # the bridge went with the app: nothing to tell

        run = AgentRun(words, engine=self._engine, registry=registry, folder=folder, on_event=on_event,
                       clock=self._clock, now=self._now, timing=self.timing, on_overage=self._pause_for_overage,
                       firewall=self.firewall, web=session, fetch=self._new_fetcher().fetch, owner=owner)
        if owner is not None:
            owner.run_id = run.run_id
        self._run = run
        logger.info("Agent %s: a task starts", run.run_id)
        run.start()
        if self._browser_factory is None:
            snapshot_engine.warm_up()     # here, before the switch and the panel move (the module docs)
        self.taskStarted.emit(run.run_id)
        return True

    def spoken(self, line: str, instead: str = "") -> str:
        """The one gate on the way to the voice for a line the model wrote: the line, or ``instead`` (Jarvis's own fixed
        words, {sir} filled in), or "": it stays on the caption, unsaid. See the module docs. Fail-closed. After the owner's
        Stop or Turn off nothing more is said, whatever was on its way."""
        if self.stopped_by:
            return ""
        if getattr(self.config.agent, "speak_private", False) is True:
            return line
        wall = self.firewall
        try:
            if wall is not None and wall.speakable(line):
                return line
        except Exception as exc:  # noqa: BLE001 - unknown: not said
            logger.warning("Agent: a line could not be checked (%s); not said", type(exc).__name__)
        return self._words(instead) if instead else ""

    def spoken_question(self, text: str) -> str:
        return self.spoken(text, QUESTION_LINE)

    def spoken_report(self, end: AgentEnd, opening: str) -> str:
        return self.spoken(opening, REPORT_LINES.get(end.outcome, REPORT_LINES[OUTCOME_PARTIAL]))

    def _new_session(self) -> WebSession:
        """This run's use of the window. One window at a time: what the last run left goes first. Nothing of the web
        engine starts until the first web step."""
        self._close_browser()
        self._browser = self._new_browser()
        return WebSession(self._browser, self.config.agent.search_engine)

    def _new_browser(self) -> Any:
        make = self._browser_factory or BrowserClient
        desktop = getattr(self.config, "desktop", None)
        browser = make(self, avoid=self._window_avoid,
                       hide_from_capture=bool(getattr(desktop, "hide_from_capture", True)))
        for name, slot in (("hideAsked", lambda: self.windowHidden.emit(True)),
                           ("windowPlaced", self.windowPlaced.emit)):
            signal = getattr(browser, name, None)
            if signal is not None:
                signal.connect(slot)
        hide = getattr(browser, "set_hidden", None)
        if callable(hide):
            hide(away=self._away)
        return browser

    def _new_fetcher(self) -> Fetcher:
        """This run's fetch_text, on [research] model, with its own cap."""
        research = getattr(self.config, "research", None)
        return Fetcher(self._engine, str(getattr(research, "model", "sonnet") or "sonnet"))

    def _close_browser(self) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            browser.close_now()
            browser.deleteLater()

    def _refusal(self) -> str:
        if self._run is not None:
            return self._words(RUNNING_TEXT)
        if not policy.agent_on(self.config) or self._tasks_consent() is not True:
            return self._words(OFF_TEXT)
        if not self.tasks.allowed():
            return self._words(LIMIT_TEXT.replace("{count}", str(self.tasks.per_day)))
        if self._too_old:
            return self._words(TOO_OLD_TEXT)
        if self.planner.usage.hold_until() is not None:
            return self._words(HOLD_TEXT)
        return ""

    def refusal(self) -> str:
        """Why start() would refuse now, in Jarvis's own words ("": it would not): the app asks before it says that a
        task starts, so nothing promises work that won't happen."""
        refused = self._refusal()
        if refused:
            return refused
        try:
            workspace_root(self.config.agent.workspace, self._environ)
        except WorkspaceRefused as why:
            return self._words(why.text)
        return ""

    @staticmethod
    def _its_choice(run: AgentRun, text: str) -> bool:
        """The words are one of the choices of the question that waits: that is its answer, whatever the word. Only for
        ask_owner's question: the click-only cards' words are nobody's answer."""
        question = run.waiting
        if question is None or question.kind != agent_runner.Q_TASK:
            return False
        return plain_words(text) in {plain_words(choice) for choice in question.choices}

    def take_request(self, text: str) -> bool:
        """AskController's gate (D4). True: it was taken - the task stops, or the words went in as the owner's. Typed or
        dictated words never answer a card only a click answers: clickNeeded says so, and how to reach it. A stop word
        that is the waiting question's own choice answers it, once a task: a page may have had the model offer "Stop" to
        swallow the owner's, so a second stop word stops the task, whatever the question offers."""
        run = self._run
        if run is None or run.ended is not None:
            return False
        if stop_words(text):
            if self._stop_chosen or not self._its_choice(run, text):
                self.stop("typed")
                return True
            self._stop_chosen = True
        status = run.owner_says(text)
        if status == "refused":
            return False
        click = status in ("sent", "held") and run.click_awaited() is not None
        if self._feed is not None:
            self._feed.you_said(status, click)
        if click:
            self.clickNeeded.emit(status)
        elif status in TAKEN_TEXT:
            self.acknowledged.emit(self._words(TAKEN_TEXT[status]))
        return True

    def answer(self, question_id: str, text: str) -> bool:
        """The owner-question seam. False: that question is not waiting any more."""
        run = self._run
        return run.answer(question_id, text) if run is not None else False

    # ---- the watch window and the result card, for the feed ------------------------------------------------

    def _browser_call(self, name: str, default: Any, *args: Any, **kwargs: Any) -> Any:
        call = getattr(self._browser, name, None)
        return call(*args, **kwargs) if callable(call) else default

    def set_window_hidden(self, hidden: bool) -> None:
        """The owner's Hide and Show: the task carries on behind it. Their Show wins over a file card's rest too."""
        if not hidden:
            self._browser_call("set_resting", None, False)
        self._browser_call("set_hidden", None, owner=bool(hidden))

    def set_window_resting(self, resting: bool) -> None:
        """A file card waits, or its batch works: the task is resting and the card needs the room on every screen, so
        the watch window is off the screen meanwhile. As it ends, the owner's own Hide still holds."""
        self._browser_call("set_resting", None, bool(resting))

    def set_window_away(self, away: bool) -> None:
        """The Jarvis window is back (True): the watch window hides meanwhile. On the HUD again it comes back."""
        self._away = bool(away)
        self._browser_call("set_hidden", None, away=self._away)

    def window_hidden(self) -> bool:
        """It is off the screen for a reason a Show can lift, and there is a window to bring back."""
        return bool(self._browser_call("owner_hid", False)) or bool(self._browser_call("resting", False))

    def window_rect(self) -> Any:
        return self._browser_call("window_rect", None)

    def keep_window_clear(self) -> None:
        self._browser_call("keep_clear", None)

    def close_window(self) -> None:
        """The result card went: the window that stayed with it goes."""
        self._close_browser()

    def worked_s(self) -> float:
        """How long this task has worked (the clock rests while the owner is waited for)."""
        run = self._run
        return run.budget.worked_s if run is not None else 0.0

    def tools_running(self) -> bool:
        return self._feed is not None and bool(self._feed.steps)

    def live_task_id(self) -> int:
        return int(getattr(self._feed.task, "id", 0) or 0) if self._feed is not None else 0

    def outputs_for(self, end: AgentEnd) -> tuple[str, ...]:
        """What the result card lists: the files the report named, else what the task's folder holds."""
        if end.outputs:
            return tuple(end.outputs)
        folder = self.last_folder
        try:
            return tuple(name for name, _size in folder.listing())[:MAX_LISTED] if folder is not None else ()
        except OSError:
            return ()

    def output_path(self, name: str) -> str:
        """The full path of a good name of a file in this task's folder, "" when it is no such file."""
        folder = self.last_folder
        try:
            parts = check_name(name)
        except WorkspaceRefused:
            return ""
        if folder is None or not folder.exists("/".join(parts)):
            return ""
        return str(folder.path.joinpath(*parts))

    def show_output(self, name: str) -> bool:
        """The result card's Show: the file in the sealed watch window (a local scheme, a profile of its own, the
        network gate sealed), never the owner's browser or a default app. False when it can't be shown."""
        folder = self.last_folder
        if self._shut or not self.output_path(name) or folder is None:
            return False
        if self._browser is None:
            self._browser = self._new_browser()
        show = getattr(self._browser, "show_for_owner", None)
        if not callable(show):
            return False
        self._browser_call("set_hidden", None, owner=False, away=False)
        show("/".join(check_name(name)), folder)
        return True

    def _withdraw(self, *, cards: bool) -> None:
        """The run's undecided cards go (the module docs): at once on a Stop, a Turn off or a guard's stop (``cards``),
        and at any end the card of file steps nothing waits on any more. Never while Jarvis closes: the app's going."""
        owner, things = self._owner, self._things
        if owner is None or things is None or self._shut:
            return
        try:
            things.withdraw(tuple(owner.run_ids), tuple(owner.card_ids) if cards else (), cards=cards)
        except Exception as exc:  # noqa: BLE001 - never a reason for a Stop not to end
            logger.warning("Agent: its cards could not be withdrawn (%s)", type(exc).__name__)

    def _on_card_ended(self, state: Any, ending: str) -> None:
        """A card of file steps ended: the run that waits on it hears how, in plain words, as its next message (and
        whether the owner clicked it: their word while the plan's limit holds)."""
        run = self._run
        if run is not None:
            run.card_ended(state.card.run_id, agent_owner.card_message(state, ending),
                           agent_owner.card_how(state, ending),
                           clicked=ending in (agent_owner.CARD_CARRIED, agent_owner.CARD_NOT_NOW))

    def _on_card_at_work(self, state: Any, working: bool) -> None:
        """The owner's click started that card's batch, or it ran nothing and the card waits on: the run's clock for it
        stops, or starts again."""
        run = self._run
        if run is not None and run.card_at_work(state.card.run_id, working):
            self.cardAtWork.emit(bool(working))

    def _on_card_progress(self, state: Any) -> None:
        """A row of that card's batch ended: the run's watch on it starts again."""
        run = self._run
        if run is not None:
            run.card_progress(state.card.run_id)

    def stop(self, reason: str = "stop") -> None:
        """R8. In the moment after the model's finish the task is done with: a Stop ends it now as it would have ended,
        with its report on its card and what that report says waits. A Turn off leaves nothing, report or not. After
        either the voice says nothing more of it (``stopped_by``)."""
        run = self._run
        reported = run is not None and run.reported and reason != TURNED_OFF
        if run is not None:
            self.stopped_by = TURNED_OFF if reason == TURNED_OFF else STOPPED_BY_OWNER
            self._stopping = not reported     # what is still on its way to this thread is dropped
            run.stop(reason, keep_report=reason != TURNED_OFF)
            if not reported:
                self._withdraw(cards=True)
        if self._browser is not None and not reported:
            self._browser.stop()          # R8: a load is released at once, the page and the window go

    def shutdown(self) -> None:
        """Jarvis closes: nothing starts after this, the process is gone and the task stamped."""
        self._shut = True
        run = self._run
        browser, self._browser = self._browser, None
        if browser is not None:
            browser.shutdown()            # a load is released at once; no page, no renderer, no window
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
            if event.text:
                self.activityChanged.emit(event.text)
        elif kind == agent_runner.E_TOOL_END:
            feed.tool_end(event.call, event.result)
            self.stepEnded.emit()
        elif kind == agent_runner.E_PLAN:
            self.planChanged.emit(event.steps)
        elif kind == agent_runner.E_SAY:
            self.said.emit(event.text)
        elif kind == agent_runner.E_TEXT:
            feed.note(event.text)
        elif kind == agent_runner.E_NOTICE:
            feed.note(self._words(event.text))
            # The plan's limit: nothing can be decided now, so never amber (a decision waiting); a limit is coral, as a
            # desktop request's is, and it shows on the HUD's caption, as an idle tone would not.
            self.noticed.emit(self._words(event.text), hud.TONE_ERROR if event.code == LIMIT else hud.TONE_DONE)
            if event.code == LIMIT:
                self.activityChanged.emit(LIMIT_ACTIVITY)
                self.limitPaused.emit(True)
        elif kind == agent_runner.E_LIMIT_GONE:
            self.limitPaused.emit(False)
        elif kind == agent_runner.E_QUESTION and event.question is not None:
            # The firewall's card shows the exact words or address, whatever they are.
            question = (event.question if event.question.kind == agent_runner.Q_WEB
                        else replace(event.question, text=self._words(event.question.text)))
            feed.asked(question)
            self.questionAsked.emit(question)
        elif kind == agent_runner.E_QUESTION_GONE:
            feed.gone(event.text)
            if event.code == agent_runner.C_NO_ANSWER:
                self.questionExpired.emit(event.question)
                if event.question.kind == agent_runner.Q_CARD:   # the model was told nothing changed: so it stays
                    self._withdraw(cards=False)
            self.questionGone.emit(event.question)

    def _end(self, end: AgentEnd | None) -> None:
        """The task ended: Live is stamped and the app told. Once."""
        run, self._run = self._run, None
        if run is None or end is None:
            return
        end = replace(end, summary=self._words(end.summary)) if end.own_words else end
        self.last_end = end
        self._withdraw(cards=end.outcome == agent_runner.OUTCOME_STOPPED or end.code.startswith(GUARD))
        self._owner = None
        if self._browser is not None:     # its page goes now; its window later, or with the result card: at once on a Stop
            self._browser.end_run(linger=end.outcome != agent_runner.OUTCOME_STOPPED, hold=self.hold_window)
        if self.last_folder is not None:
            self.last_folder.discard_if_empty()   # a task that wrote nothing leaves nothing
        feed, self._feed = self._feed, None
        if feed is not None:
            feed.ended(end)
        self.taskEnded.emit(end)
