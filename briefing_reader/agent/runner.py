"""One task's run: the owner's Claude Code working over Jarvis's own tools (SPEC 5, R2, R8, D5, D9).

    AgentEngine(agent_config, base)     the same claude.exe, sign-in checks and runner as Ask (``base``:
                                        planner.ClaudeEngine); its own empty folder, command line and environment
    check_agent_init(event, expected)   an init event as a run requires it, else GuardTrip: ``tools`` exactly this run's
                                        set, ``skills`` empty, the jarvis server connected, no API key, dontAsk. One
                                        comes per user message, and every one is checked
    AgentRun(goal, engine=, registry=, folder=, on_event=, ...)
        start()                         the run's thread: the checks, the endpoint, the MCP config file, the process
        owner_says(text)                the owner's words go in over stdin, labelled as the owner's (D4). The answer to
                                        an ask_owner question that waits, when one does
        answer(question_id, text)       the owner-question seam: A3's card and the tests call it
        stop()                          kills at once: late tool results are dropped (R8)
        join(timeout)                   the thread and everything it holds are gone

The process keeps its stdin open and Jarvis feeds it messages: the first one holds the goal, in the owner's own words,
and goes in once the process has connected to the endpoint (its init then shows the tools; connect_s at most).
A wait ends the turn (D5): ask_owner returns at once with "end your turn", the ``result`` event means the model is idle
(it uses nothing then), and the answer goes in as the next message; a choice clicked or words typed answer it. The
firewall's card waits the same way (kind Q_WEB), but only a click answers it: answer(id, "Allow"), and so does "Keep
going?" (Q_KEEP_GOING, Q_RESUME). What is typed meanwhile goes in as the owner's words and allows nothing (at the resume
card it waits for the click, with the pause), and the wait's end says so, never "no answer came"; no answer in time to
the firewall's card is a refusal, and the task goes on. A file card waits too (Q_CARD: propose_files'; the card is the
desktop's own), and only its own end says anything: card_at_work(id) gives its clock over to a watch on its rows when the
owner's click starts the batch, card_ended(id, message) puts its end in as the next message, and finish waits for it
unless the task is stopping. With no row ended for BATCH_QUIET_MINUTES (card_progress(id) starts the watch again) the run
stops waiting on it, in true words: the batch is still at work, untouched, and its card has the truth. A choice clicked
answers, but it is the model's words, so it licenses nothing and clears nothing; words typed are the owner's, and those
the model asked for, or that plainly hold something private, turn the firewall on for the rest of the run (_typed).
Words typed in answer to ask_owner's question are marked so for the firewall (owner_said's ``answer``): they license
what they say, but ask for nothing at once, for the question was the model's (agent.owner). A
``result`` with no wait pending and no
finish gets one prompt to carry on or finish, and after another one the task ends as partial. A message sent mid-run is
delivered by Claude Code after the running tool returns (``--replay-user-messages`` shows when; a ``result`` while one is
on its way is not idle). Words typed before the goal has gone in go in right after it, in order.

The budget (agent.policy.Budget): reaching a turn or minute cap asks "Keep going?" through the same seam; tool calls
then return "end your turn" until the answer. Yes: as much again. No, or no answer: the model is told to report and
finish, gets a short while for it and the task ends as partial. The clock rests while the owner is waited for. Reached
while a card or a batch is up, tool calls but finish are held the same way, and "Keep going?" comes when that wait ends.

The plan's limit (stream.rate_limit_signal, or a failed result's text in the plan's own words: stream.task_failure): a
run that would use extra usage is killed at once (the owner's rule: no paid calls). The plan's own limit pauses the task
with words saying when, and when that time comes asks whether to carry on; with no time given it waits for the owner's
word, LIMIT_WAIT_HOURS at most, and then ends as partial. That word is a line typed, or a wait that was up ending on
their click or answer, or with words they typed at it (_let_go): what waited goes in and the task tries again. Whatever
is said or answered meanwhile goes in with the resume, in the order it came. Another limit (a context's, a budget's)
ends the task as any failure does, and Claude's server limiting requests ends it in words that say it isn't the plan's.

The guard (R2): anything but this run's tools in a tool_use, a stream chunk or an init kills the run. Its command line
is cli.build_agent_argv's; the bearer token goes only into the MCP config file in the run's own folder under the data
folder, which is deleted when the run ends. While the test guard is on, the real runner is refused before anything runs.

on_event hears AgentEvents on the run's threads and must not block: the controller (agent_ui) moves them to the GUI
thread. Logs: ids, kinds, counts, states, reason codes and durations only. Nothing is written but the workspace's files
and, for the length of the run, the MCP config (D11).
"""

from __future__ import annotations

import itertools
import logging
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import config as app_config
from ..ask import cli
from ..ask.context import COMMAND_CAP, data, now_line
from ..ask.stream import (CLI_UNSUPPORTED, ERROR, GUARD, LIMIT, NOT_SIGNED_IN, SERVER_BUSY, AskFailure, GuardTrip,
                          InitSummary, LimitSignal, clock_words, exit_failure, failure, limit_reset_time,
                          limit_reset_words, parse_event, rate_limit_signal, task_failure)
from ..desktop import guard
from ..desktop.backend import real_backend_blocked
from ..gcal import local_timezone
from . import policy
from .endpoint import TOOL_TIMEOUT_S, TOO_LONG_CODE, TOO_LONG_TEXT, Call, Endpoint
from .tools import (OUTCOME_DONE, OUTCOME_FAILED, OUTCOME_PARTIAL, PlanStep, Registry, RunContext, ToolResult,
                    doing_words, error)
from .workspace import TaskFolder

logger = logging.getLogger(__name__)

SERVER = cli.AGENT_SERVER
TOOL_BLOCKS = ("tool_use", "server_tool_use", "mcp_tool_use")
MCP_FILE = "mcp.json"
OUTCOME_STOPPED = "stopped"            # AgentEnd.outcome: the owner's Stop, a Turn off, Jarvis closing
# What an AgentRun reports (AgentEvent.kind).
E_STARTED, E_PLAN, E_SAY, E_TOOL_START, E_TOOL_END, E_QUESTION, E_QUESTION_GONE, E_NOTICE, E_TEXT, E_USAGE, E_ENDED = (
    "started", "plan", "say", "tool_start", "tool_end", "question", "question_gone", "notice", "text", "usage", "ended")
E_LIMIT_GONE = "limit_gone"            # the plan's limit let go (it came as an E_NOTICE with the code LIMIT)
# Whose question it is (OwnerQuestion.kind).
Q_TASK, Q_KEEP_GOING, Q_RESUME, Q_WEB, Q_CARD = "task", "keep_going", "resume", "web", "card"
# Why it ended (AgentEnd.code: fixed words, safe to log).
C_FINISHED, C_STOPPED, C_NO_FINISH, C_NO_ANSWER, C_OWNER_NO, C_MAX_TURNS, C_OVERAGE = (
    "finished", "stopped", "no_finish", "no_answer", "owner_no", "max_turns", "overage")
C_NO_CLICK, C_LIMIT_WAIT = "no_click", "limit_wait"     # words came but no click; the limit held, with no word
C_TEST_GUARD, C_INTERNAL, C_EXIT, C_GARBLED = "test_guard", "internal", "exit", "garbled"
C_SERVER_BUSY = SERVER_BUSY            # Claude's server was limiting requests: not the plan's limit
C_BATCH_QUIET = "batch_quiet"          # E_NOTICE's code: a batch at work has gone quiet
HELD = "held"                          # a tool result's code: the call was not made, the model is told to end its turn
WEB_ALLOW_WORD = "Allow"               # the one word on the firewall's card that allows anything
STOPPED_CODE = "stopped"

OWNER_LABEL = "The owner says: "
ANSWER_LABEL = "The owner answers your question: "
KEEP_GOING_YES = "The owner says: keep going. You have as many turns and as much time again."
RESUME_YES = "Jarvis: the plan's limit has reset and the owner says carry on. Carry on from where you were."
CONTINUE_TEXT = ("Jarvis: your turn ended, but the task has not been finished and the owner does not see your plain "
                 "text. If there is more to do, carry on with your tools. If it is done or you cannot go further, call "
                 "finish with an honest report.")
WRAP_UP_NO = ("Jarvis: the owner chose to stop here. Call finish now with outcome partial: what you did, what you "
              "produced and what is left. Call nothing else.")
WRAP_UP_SILENT = ("Jarvis: the owner did not answer, so the task stops here. Call finish now with outcome partial: "
                  "what you did, what you produced and what is left. Call nothing else.")
PAUSED_TEXT = ("Jarvis has paused the task: it has reached its budget and the owner is being asked whether to keep "
               "going. End your turn now with one short sentence. The answer will come to you as your next message.")
BUDGET_AT_A_WAIT_TEXT = ("That call was not made: the task has reached its budget while a card is up, and the owner will "
                         "be asked whether to keep going when that card has ended. End your turn now with one short "
                         "sentence and wait. What comes of it will come to you as your next message.")
BATCH_QUIET_MINUTES = 30               # a file card's batch at work: this long with no row ended, and the task goes on
BATCH_QUIET_TEXT = ("Jarvis: the batch the owner's click started on that card is still at work, but no row of it has "
                    f"ended for {BATCH_QUIET_MINUTES} minutes, so the task no longer waits for it. Its card has the "
                    "truth; what it has changed or not is not known here, so do not say either way. Carry on without "
                    "it, and say in your report that its card has the truth.")
WRAP_UP_TOOL_TEXT = "The task is stopping. Call finish now, and nothing else."
WRAP_UP_NO_CLICK = ("Jarvis: the owner's words came in, but only a click answers that card and none came, so the task "
                    "stops here. Call finish now with outcome partial: what you did, what you produced and what is "
                    "left. Call nothing else.")
WEB_ALLOWED_TEXT = "Jarvis: the owner allowed that one call. Make it again now, exactly as it was."
WEB_DECLINED_TEXT = ("Jarvis: the owner said no, so that call was not made. Do not try it again: carry on without "
                     "it, find another way, or say so in your report.")
WEB_UNANSWERED_TEXT = ("Jarvis: the owner did not answer, so that call was not made. Do not try it again: carry on "
                       "without it, find another way, or say so in your report.")
WEB_NO_CLICK_TEXT = ("Jarvis: the owner's words came in, but only a click allows a call and none came, so that call "
                     "was not made. Do not try it again: carry on without it, find another way, or say so in your "
                     "report.")
CARD_UNANSWERED_TEXT = ("Jarvis: nobody clicked the card in time, so nothing was changed. Do not propose it again: "
                        "carry on without it, or say so in your report.")
CARD_WAITS_TEXT = ("A card waits for the owner, so the task is not finished yet. End your turn now; what they decide "
                   "comes as your next message, and then you can finish.")
ALREADY_FINISHED_TEXT = "The task has finished. End your turn now."
STOPPED_TOOL_TEXT = "The task was stopped."

# Jarvis's own words ({sir}: agent_ui fills it in).
KEEP_GOING_WORDS = "That's {what} on this task, {sir}. Keep going?"
KEEP_GOING_CHOICES = ("Keep going", "Stop here")
CARD_WORDS = "That needs your OK on its card, {sir}."
RESUME_WORDS = "Your Claude plan's limit has reset, {sir}. Shall I carry on with this task?"
LIMIT_WORDS = ("Your Claude plan's limit is reached, {sir}. I've paused this task: it resets {when}, and I'll ask you "
               "then.")
LIMIT_WAIT_HOURS = 6                   # the limit holds with no time given: this long with no word, and the task ends
LIMIT_NO_TIME_WORDS = ("Your Claude plan's limit is reached, {sir}. I've paused this task: tell me to carry on once "
                       f"your plan has reset, or say stop. With no word in {LIMIT_WAIT_HOURS} hours I'll stop it there.")
OVERAGE_WORDS = ("I stopped this task, {sir}: Claude Code said it would use extra usage. Turn usage credits off at "
                 "claude.ai/settings/usage. Files already written stay in the workspace.")
LIMIT_ENDED_WORDS = ("Your Claude plan's limit is reached, {sir}, and Claude Code stopped. Any files are in the "
                     "workspace.")
HELD_WORDS = "I'll pass that on when the plan's limit has reset, {sir}."
HELD_AT_WORK_WORDS = "I'll pass that on when that card's batch has ended, {sir}."    # the limit holds, with no time given
LIMIT_WAITED_WORDS = (f"The plan's limit held this task for {LIMIT_WAIT_HOURS} hours with no word, so I stopped there, "
                      "{sir}. Live has its steps, and any files are in the workspace.")
STOPPED_WORDS = "Stopped, {sir}. Files already written stay in the workspace."
NO_FINISH_WORDS = "The task ended without a report, {sir}. Live has its steps, and any files are in the workspace."
OWNER_NO_WORDS = "Stopped there, as you said, {sir}. Live has its steps, and any files are in the workspace."
NO_ANSWER_WORDS = "No answer came, so I stopped there, {sir}. Live has its steps, and any files are in the workspace."
NO_CLICK_WORDS = ("You typed while the card waited, {sir}, but only a click answers it and none came, so I stopped "
                  "there. Live has its steps, and any files are in the workspace.")
BATCH_QUIET_WORDS = (f"No row of that card's batch has ended for {BATCH_QUIET_MINUTES} minutes, {{sir}}. It is still at "
                     "work and its card has the truth. The task goes on without it.")
MAX_TURNS_WORDS = "That ran past what Claude Code allows one task, {sir}. Any files are in the workspace."
GUARD_WORDS = "Claude Code started with something a task does not allow, so I stopped it, {sir}."
GUARD_TOOL_WORDS = ("Claude Code tried to use something a task does not allow, so I stopped it, {sir}. Any files are in "
                    "the workspace.")
GARBLED_WORDS = "Claude Code's output could not be read, so I stopped the task, {sir}."
EXIT_WORDS = "Claude Code stopped before the task was finished, {sir}. Any files are in the workspace."
SERVER_BUSY_WORDS = ("Claude's server was limiting requests, so the task stopped there, {sir}. It isn't your plan's "
                     "limit: you can ask again in a while. Any files are in the workspace.")
INTERNAL_WORDS = "The task stopped because of an unexpected error, {sir}. Any files are in the workspace."
TEST_GUARD_WORDS = "The real Claude Code is never started while the tests run."
AGENT_UNSUPPORTED_MESSAGE = "This Claude Code lacks what a task needs ({flags}); nothing was started"
# How a wait ended, for Live (E_QUESTION_GONE's text): Jarvis's own words.
HOW_ALLOWED, HOW_DECLINED, HOW_NO_ANSWER = "You allowed it", "You said no, so it was not made", "No answer came"
HOW_NO_CLICK = "You typed, but no click came"
HOW_KEEP_GOING, HOW_STOP_HERE, HOW_CARRY_ON = "You said keep going", "You said stop here", "You said carry on"
HOW_ANSWERED = "You answered"
HOW_ENDED = "The task ended first"
HOW_BATCH_QUIET = "It is still at work, and its card has the truth"
LIMIT_ACTIVITY = "Waiting for your plan's limit to reset"    # the panel's activity line while the limit holds
HELD_NOTE = "not made: the task was waiting for you"
BUDGET_HELD_NOTE = "not made: its budget is reached, and a card is up"
_WORD_RE = re.compile(r"[A-Za-z0-9._\-\[\]]{1,80}")


def _plain(value: Any) -> str:
    return value if isinstance(value, str) and _WORD_RE.fullmatch(value) else ("other" if value else "")


# --------------------------------------------------------------------------
# What a run reports
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class OwnerQuestion:
    """A question for the owner (the ask_owner tool's, or Jarvis's own). Shown, never logged."""

    id: str
    kind: str
    text: str = field(repr=False)
    choices: tuple[str, ...] = field(default=(), repr=False)
    typed: bool = False                 # E_QUESTION_GONE with C_NO_ANSWER: words came meanwhile, but only a click counts


@dataclass(frozen=True)
class AgentEnd:
    """How a task ended. ``summary``: the model's report (``finish``), else Jarvis's own words (``own_words``); shown,
    never logged. ``outputs``: the files it named in its task folder."""

    outcome: str
    code: str
    summary: str = field(default="", repr=False)
    outputs: tuple[str, ...] = field(default=(), repr=False)
    own_words: bool = True
    turns: int = 0
    tools: int = 0
    worked_s: float = 0.0
    input_tokens: int = 0               # the result events' usage, summed: the part not cached ...
    output_tokens: int = 0
    resets_at: datetime | None = None   # a run that would use extra usage: when the plan's limit resets
    cache_read_tokens: int = 0          # ... the input read from the prompt cache, and what was written to it
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    call: int = 0                       # the tool call's number in this run
    tool: str = ""                      # its name
    result: ToolResult | None = None    # E_TOOL_END
    steps: tuple[PlanStep, ...] = ()    # E_PLAN
    text: str = field(default="", repr=False)   # E_SAY, E_NOTICE, E_TEXT, E_TOOL_START's few words: shown, never logged
    code: str = ""                      # E_NOTICE: a fixed word
    question: OwnerQuestion | None = None       # E_QUESTION, E_QUESTION_GONE
    end: AgentEnd | None = None         # E_ENDED
    turns: int = 0                      # E_USAGE
    tools: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class Timing:
    """The run's waits, in seconds (tests make them short)."""

    poll_s: float = 0.1
    connect_s: float = 8.0              # the first message waits this long at most for the process to connect
    init_s: float = 60.0                # no init event within this: the guard stops it
    question_s: float = 30 * 60.0       # the owner's answer to a question
    keep_going_s: float = 10 * 60.0     # ... to "Keep going?"
    web_card_s: float = 10 * 60.0       # ... to the firewall's card: then that one call is refused and the task goes on
    card_s: float = 16 * 60.0           # ... to a file card: its own 15 minutes end first; this only if they did not
    at_work_s: float = BATCH_QUIET_MINUTES * 60.0     # its batch at work: this long with no row ended
    sleep_gap_s: float = 60.0           # this long between two looks at real_clock: the PC slept (_note_sleep)
    real_clock: Callable[[], float] = time.monotonic  # counts through a sleep, on Windows
    wrap_up_s: float = 60.0             # told to report and finish: this long, then it ends as partial
    after_finish_s: float = 20.0        # finish recorded: this long for its last words
    replay_s: float = 5.0               # a message sent never showed as delivered and nothing came: idle after all
    tool_s: float = TOOL_TIMEOUT_S
    resume_margin_s: float = 30.0       # past the limit's reset time by this much before asking
    limit_wait_s: float = LIMIT_WAIT_HOURS * 3600.0   # the limit holds, no time given: this long with no word


# --------------------------------------------------------------------------
# The engine and the init check
# --------------------------------------------------------------------------

class AgentEngine:
    """A task's Claude Code: ``base`` (planner.ClaudeEngine) for claude.exe, the sign-in checks and the runner."""

    def __init__(self, agent: Any, base: Any) -> None:
        self.agent = agent
        self.base = base
        data_dir = Path(base.work_folder).parent
        self.work_folder = data_dir / cli.AGENT_WORK_FOLDER
        self.runs_folder = data_dir / cli.AGENT_RUNS_FOLDER
        self.fetch_folder = data_dir / cli.AGENT_FETCH_WORK_FOLDER

    @property
    def runner(self) -> Any:
        return self.base.runner

    def preflight(self) -> tuple[Any, AskFailure | None]:
        """Right before a task: the base engine's preflight (a fresh `claude auth status`, its folder), this Claude
        Code's flags, this run's empty folder. (readiness, why not). No model request."""
        ready, refused = self.base.preflight()
        if refused is not None:
            return ready, refused
        if ready.agent_missing:
            return ready, failure(CLI_UNSUPPORTED, "agent_flags",
                                  AGENT_UNSUPPORTED_MESSAGE.format(flags=", ".join(ready.agent_missing)))
        problem = cli.prepare_work_folder(self.work_folder, what="A task", folder=cli.AGENT_WORK_FOLDER)
        if problem:
            return ready, failure(ERROR, "workdir", problem)
        return ready, None

    def argv(self, ready: Any, allowed: str, mcp_file: Path) -> list[str]:
        assert ready.exe is not None
        return cli.build_agent_argv(ready.exe.path, model=self.agent.model, effort=self.agent.effort,
                                    allowed_tools=allowed, mcp_config_file=mcp_file)

    def env(self) -> dict[str, str]:
        return cli.agent_env(self.base._env_source())

    def sweep_runs(self) -> None:
        """What an earlier run left when Jarvis died with it (its MCP config: a dead token): gone before a new one.
        Only one task runs at a time, so nothing in use is here."""
        try:
            leftovers = list(self.runs_folder.iterdir()) if self.runs_folder.is_dir() else []
        except OSError:
            return
        for leftover in leftovers:
            shutil.rmtree(leftover, ignore_errors=True)


def check_agent_init(event: Mapping[str, Any], expected: frozenset[str]) -> InitSummary:
    """An init event as a run requires it (R2), else GuardTrip (the run is killed)."""
    if event.get("type") != "system" or event.get("subtype") != "init":
        raise GuardTrip("not_init")
    if event.get("apiKeySource") != "none":
        raise GuardTrip("api_key_source")
    tools = event.get("tools")
    if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools) or set(tools) != expected:
        raise GuardTrip("tools")
    if event.get("skills") != []:
        raise GuardTrip("skills")
    servers = event.get("mcp_servers")
    if not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], Mapping):
        raise GuardTrip("mcp_servers")
    if servers[0].get("name") != SERVER or servers[0].get("status") != "connected":
        raise GuardTrip("server_not_connected")
    if event.get("permissionMode") != "dontAsk":
        raise GuardTrip("permission_mode")
    return InitSummary(api_key_source="none", tools=tuple(tools), mcp_servers=1, model=_plain(event.get("model")),
                       version=_plain(event.get("claude_code_version")), structured_tool=False,
                       permission_mode="dontAsk")


def first_message(goal: str, now: datetime, zone: str, folder_name: str) -> str:
    """The run's first message: the time, the task folder's name and the goal, in the owner's own words."""
    words = " ".join((goal or "").split())[:COMMAND_CAP]
    folder = data(folder_name, 120)
    return f"<now>{now_line(now, zone)}</now>\n<task_folder>{folder}</task_folder>\n{OWNER_LABEL}{words}"


def owner_message(text: str, label: str = OWNER_LABEL) -> str:
    return label + " ".join((text or "").split())[:COMMAND_CAP]


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


@dataclass
class _Wait:
    question: OwnerQuestion
    deadline: float
    on_answer: Callable[[bool], None] | None = None     # the firewall's card: told of the click, or of no answer
    typed: bool = False                 # words came while it waited (only a click answers all but ask_owner's)
    at_work: bool = False               # a file card: the click came and its batch is at work: its clock has stopped


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------

class AgentRun:
    """See the module docs. Thread-safe: its own thread reads the stream, the HTTP threads call the tools, the GUI
    thread says what the owner said. One RLock guards the state; ``on_event`` is called under it and must only queue."""

    def __init__(self, goal: str, *, engine: AgentEngine, registry: Registry, folder: TaskFolder,
                 on_event: Callable[[AgentEvent], None], clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now().astimezone(), timing: Timing = Timing(),
                 firewall: policy.Firewall | None = None, run_id: str = "",
                 on_overage: Callable[[datetime | None], None] | None = None, web: Any = None,
                 fetch: Callable[..., str] | None = None, owner: Any = None) -> None:
        self.run_id = run_id or secrets.token_hex(3)
        self.goal = goal
        self.engine = engine
        self.registry = registry
        self.folder = folder
        self._on_event = on_event
        self._clock = clock
        self._now = now
        self.timing = timing
        self._firewall = firewall
        self._on_overage = on_overage
        self._expected = registry.expected()
        self.budget = policy.Budget(engine.agent.max_turns, engine.agent.max_minutes, clock)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Any = None
        self._endpoint: Endpoint | None = None
        self._run_folder: Path | None = None
        self._end: AgentEnd | None = None
        self._calls = itertools.count(1)
        self._tool_count = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._cache_read_tokens = 0
        self._cache_write_tokens = 0
        self._started_at = clock()
        self._init: InitSummary | None = None
        self._wait: _Wait | None = None
        self._question_ids = itertools.count(1)
        self._idle = False                  # the last result came and nothing has gone in since
        self._unreplayed = 0                # messages sent that have not shown as delivered
        self._replay_since: float | None = None   # a result came while one was on its way
        self._outbox: list[str] = []        # goes in at the next result (or, before the goal has gone in, after it)
        self._goal_sent = False             # the first message has gone in: nothing may go before it
        self._nudged = False                # the one prompt to carry on or finish went, with no tool call since
        self._finished: tuple[str, str, tuple[str, ...]] | None = None
        self._finished_at = 0.0
        self._stdin_closed = False
        self._wrap_up_code = ""             # told to report and finish (C_OWNER_NO, C_NO_ANSWER)
        self._wrap_up_deadline = 0.0
        self._paused = False                # the plan's limit holds
        self._paused_until: datetime | None = None
        self._paused_at = 0.0
        self._held_owner: list[str] = []    # what waits for the limit: the owner's words, a card's answer
        self._owner_word = False            # since this pause began the owner has clicked, answered or typed (_let_go)
        self._garbage = 0
        self._card_id = ""                  # the file card a Q_CARD wait hangs on
        self._line_asked = False            # a line of the model's has asked the owner something (_typed)
        self._last_real = timing.real_clock()     # when the clock was last looked at (_note_sleep)
        self._context = RunContext(run_id=self.run_id, folder=folder, clock=clock, on_plan=self._tool_plan,
                                   on_say=self._tool_say, on_question=self._tool_question, on_finish=self._tool_finish,
                                   web=web, tool_s=timing.tool_s, fetch=fetch, owner=owner, on_card=self._tool_card,
                                   card_gone=self._tool_card_gone, read_words=getattr(firewall, "read_words", None))
        attach = getattr(firewall, "attach", None)
        if callable(attach):
            attach(self.ask_web)
        self._note_owner(goal)
        self._note_if_private(goal)

    def __repr__(self) -> str:              # never the goal
        return f"AgentRun({self.run_id})"

    # ---- from outside ------------------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._main, name="agent", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> bool:
        """True when the run's thread has ended: the process killed and closed, the files gone."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    @property
    def ended(self) -> AgentEnd | None:
        return self._end

    @property
    def waiting(self) -> OwnerQuestion | None:
        wait = self._wait
        return wait.question if wait is not None else None

    @property
    def reported(self) -> bool:
        """The model has called finish: the report stands, whatever comes now."""
        return self._finished is not None

    def click_awaited(self) -> OwnerQuestion | None:
        """The card only a click answers that waits for that click now (not ask_owner's, not a batch at work), or None."""
        wait = self._wait
        return wait.question if wait is not None and wait.question.kind != Q_TASK and not wait.at_work else None

    def _note_owner(self, words: str, *, answer: bool = False) -> None:
        """The owner's own words, for the firewall (WebFirewall.owner_said: they clear nothing, once it holds anything
        private). ``answer``: typed in answer to ask_owner's question, which the model wrote, so they ask for nothing
        at once (pc-8: agent.owner); they still license what they say."""
        heard = getattr(self._firewall, "owner_said", None)
        if callable(heard):
            if answer:
                heard(words, answer=True)
            else:
                heard(words)

    def _note_if_private(self, words: str) -> None:
        note = getattr(self._firewall, "note_if_private", None)
        if callable(note):
            note(words)

    def _typed(self, text: str, kind: str = "") -> None:
        """Words typed while the task runs, before they are noted. A page may have told the model to ask for them, in a
        question or in a line it said, whatever the words of the asking, and they may hold something private whatever came
        before. Then the firewall's checks are on from now on: an address in them is not seen, so the address rule stands
        between them and any page not seen in this run. An ordinary follow-up, nobody having asked, changes nothing."""
        note = getattr(self._firewall, "note_private", None)
        if (kind == Q_TASK or self._line_asked) and callable(note):
            note()
        self._note_if_private(text)

    def stop(self, reason: str = C_STOPPED, *, keep_report: bool = True) -> None:
        """Kills at once (R8): whatever a tool still comes back with is dropped. ``reason``: the log's word. After finish
        the task is done with: only its last words are left to come, so its report and its outcome stand, but for a Turn
        off (not ``keep_report``), which leaves nothing."""
        logger.info("Agent %s: stopped (%s)", self.run_id, reason)
        with self._lock:
            if keep_report and self._finished is not None and self._end is None:
                self._end_finished()
                return
        self._finish_run(OUTCOME_STOPPED, C_STOPPED, STOPPED_WORDS)

    def owner_says(self, text: str) -> str:
        """The owner's words while the task runs: "answered" (the question that waits), "sent", "held" (the plan's limit
        holds, or a card waits while it is paused: they go in with the resume, or, with no time given, as that card ends)
        or "refused" (it has ended). Never past what is on its way already, the goal among it: they go in the order they
        were said."""
        with self._lock:
            if self._end is not None or self._stop.is_set() or self._finished is not None:
                return "refused"
            wait = self._wait
            self._typed(text, wait.question.kind if wait is not None else "")
            self._note_owner(text, answer=wait is not None and wait.question.kind == Q_TASK)
            if wait is not None and wait.question.kind == Q_TASK:   # only ask_owner's: the others are click only
                if self._paused and not self._limit_holds():
                    self._resume("", [])                 # past the reset, or no time given: what is typed tries again
                self.answer(wait.question.id, text)
                return "answered"
            if wait is not None and not wait.at_work:
                wait.typed = True                        # its end says so: never "no answer came"
            if self._paused:
                if self._limit_holds() or wait is not None:
                    self._held_owner.append(owner_message(text))     # the resume lets them go; with no time given,
                    self._owner_word = True              # so does the end of the wait that is up (_let_go)
                    if wait is None or wait.at_work:     # (a click awaited: the app says so: agent_ui.take_request)
                        later = HELD_AT_WORK_WORDS if wait is not None and self._paused_until is None else HELD_WORDS
                        self._emit(AgentEvent(E_NOTICE, text=later, code="held"))
                    return "held"
                self._resume("", [owner_message(text)])  # what waited goes first: the order they were said in
            elif self._outbox or not self._goal_sent:
                self._outbox.append(owner_message(text))  # after what waits for the next result, or the goal: never past it
            else:
                self._send(owner_message(text))
            logger.info("Agent %s: a message from the owner went in", self.run_id)
            return "sent"

    def answer(self, question_id: str, text: str) -> bool:
        """The owner's answer to the question ``question_id`` (a click's words or what was typed). False when that
        question is not waiting any more: nothing happens."""
        with self._lock:
            wait = self._wait
            if wait is None or wait.question.id != question_id or self._end is not None \
                    or wait.question.kind == Q_CARD:
                return False
            self._wait = None
            self._owner_word = self._owner_word or self._paused     # a click, or words: the owner's word (_let_go)
            question = wait.question
            self._emit(AgentEvent(E_QUESTION_GONE, question=question, text=_how_answered(question, text)))
            logger.info("Agent %s: question %s answered", self.run_id, question.kind)
            if question.kind == Q_WEB:
                allowed = text == WEB_ALLOW_WORD
                _tell(wait, allowed)
                self._deliver(WEB_ALLOWED_TEXT if allowed else WEB_DECLINED_TEXT)
                self._let_go()
                return True
            if text not in question.choices:    # a choice clicked is the model's words: never the owner's own
                self._typed(text, question.kind)
                self._note_owner(text, answer=question.kind == Q_TASK)
            if question.kind == Q_TASK:
                self._deliver(owner_message(text, ANSWER_LABEL))
                self._let_go()
                return True
            said = policy.yes_or_no(text)
            if said is False:
                self._start_wrap_up(C_OWNER_NO, WRAP_UP_NO)
                return True
            extra = [] if said is True else [owner_message(text)]    # a yes in other words: they go in too
            if question.kind == Q_RESUME:
                self._resume(RESUME_YES, extra)
            else:
                self.budget.extend()
                self._deliver("\n".join([KEEP_GOING_YES, *extra]))
                self._let_go()
            return True

    # ---- the thread --------------------------------------------------------------------------------

    def _main(self) -> None:
        try:
            if self._prepare():
                self._loop()
        except Exception as exc:  # noqa: BLE001 - a task must never take the app down
            logger.warning("Agent %s: stopped by an unexpected error (%s)", self.run_id, type(exc).__name__)
            self._finish_run(OUTCOME_FAILED, C_INTERNAL, INTERNAL_WORDS)
        finally:
            self._clean_up()

    def _prepare(self) -> bool:
        if isinstance(self.engine.runner, cli.SubprocessRunner) and real_backend_blocked():
            logger.info("Agent %s: refused (the test guard is on)", self.run_id)
            self._finish_run(OUTCOME_FAILED, C_TEST_GUARD, TEST_GUARD_WORDS)
            return False
        ready, refused = self.engine.preflight()
        if refused is not None:
            logger.info("Agent %s: not started (%s)", self.run_id, refused.detail or refused.kind)
            self._finish_run(OUTCOME_FAILED, refused.kind, refused.message)
            return False
        if self._stop.is_set():
            return False
        endpoint = Endpoint(self.registry.listing, self._invoke, lambda name: self.registry.tool(name) is not None,
                            tool_timeout_s=self.timing.tool_s, on_given_up=self._given_up)
        endpoint.start()
        self._endpoint = endpoint
        app_config.register_secret(endpoint.token)
        self.engine.sweep_runs()
        run_folder = self.engine.runs_folder / self.run_id
        run_folder.mkdir(parents=True, exist_ok=True)
        self._run_folder = run_folder
        mcp_file = run_folder / MCP_FILE
        mcp_file.write_text(cli.agent_mcp_config(endpoint.url, endpoint.token), encoding="ascii", newline="\n")
        argv = self.engine.argv(ready, self.registry.allowed(), mcp_file)
        with self._lock:
            if self._stop.is_set():
                return False
            try:
                self._process = self.engine.runner.start(argv, stdin_text="", env=self.engine.env(),
                                                         cwd=self.engine.work_folder)
            except OSError as exc:
                logger.warning("Agent %s: Claude Code could not be started (%s)", self.run_id, type(exc).__name__)
                self._finish_run(OUTCOME_FAILED, ERROR, EXIT_WORDS)
                return False
        logger.info("Agent %s: Claude Code started (%d tools, model %s)", self.run_id, len(self.registry),
                    _plain(self.engine.agent.model))
        began = time.monotonic()
        while not endpoint.listed.wait(0.02) and not self._stop.is_set():
            if time.monotonic() - began >= self.timing.connect_s:
                logger.info("Agent %s: not connected yet; the first message goes in anyway", self.run_id)
                break
        message = first_message(self.goal, self._now(), local_timezone(), self.folder.path.name)
        with self._lock:
            if self._stop.is_set():
                return False
            self._process.send(cli.stream_message(message))
            self._unreplayed += 1
            self._started_at = self._clock()
            self._goal_sent = True
            for waiting in self._take_outbox():          # typed in the first seconds: after the goal, in order
                self._send(waiting)
        return True

    def _loop(self) -> None:
        process = self._process
        while not self._stop.is_set():
            self._tick()
            if self._stop.is_set():
                return
            try:
                line = process.read_line(self.timing.poll_s)
            except cli.OutputTooLong:
                logger.warning("Agent %s: a line of output was too long", self.run_id)
                self._finish_run(OUTCOME_FAILED, C_GARBLED, GARBLED_WORDS)
                return
            if line is None:
                self._process_ended()
                return
            if not line:
                continue
            event = parse_event(line)
            if event is None:
                self._garbage += 1
                continue
            try:
                with self._lock:
                    self._on_stream(event)
            except GuardTrip as trip:
                logger.warning("Agent %s: the guard stopped it (%s)", self.run_id, trip.reason)
                self._finish_run(OUTCOME_FAILED, f"{GUARD}:{trip.reason}",
                                 GUARD_TOOL_WORDS if trip.reason == "tool_use" else GUARD_WORDS)
                return

    def _clean_up(self) -> None:
        """Whatever ended the run: no process, no endpoint, no MCP config, no run folder."""
        with self._lock:
            process, self._process = self._process, None
        if process is not None:
            try:
                _close_stdin(process)
                process.kill()
                process.wait(2.0)
                process.close()
            except OSError:
                pass
        endpoint = self._endpoint
        if endpoint is not None:
            endpoint.close()
        run_folder, self._run_folder = self._run_folder, None
        if run_folder is not None:
            shutil.rmtree(run_folder, ignore_errors=True)
        if self._garbage:
            logger.info("Agent %s: skipped %d unreadable output line(s)", self.run_id, self._garbage)

    def _finish_run(self, outcome: str, code: str, summary: str, *, outputs: tuple[str, ...] = (),
                    own_words: bool = True, resets_at: datetime | None = None) -> None:
        """The first end wins. The process is killed now and the endpoint closed; the thread cleans up the rest."""
        with self._lock:
            if self._end is not None:
                return
            self._stop.set()
            end = AgentEnd(outcome, code, summary, outputs, own_words, self.budget.turns, self._tool_count,
                           self.budget.worked_s, self._input_tokens, self._output_tokens, resets_at,
                           self._cache_read_tokens, self._cache_write_tokens)
            self._end = end
            process = self._process
            wait, self._wait = self._wait, None
            if wait is not None:
                self._emit(AgentEvent(E_QUESTION_GONE, question=wait.question, text=HOW_ENDED))
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass
        endpoint = self._endpoint
        if endpoint is not None:
            endpoint.close()
        logger.info("Agent %s: ended %s (%s), %d turn(s), %d tool call(s), %.0f s, %d in (%d from cache) / %d out tokens",
                    self.run_id, outcome, code, end.turns, end.tools, end.worked_s,
                    end.input_tokens + end.cache_read_tokens + end.cache_write_tokens, end.cache_read_tokens,
                    end.output_tokens)
        self._emit(AgentEvent(E_ENDED, end=end))

    def _emit(self, event: AgentEvent) -> None:
        try:
            self._on_event(event)
        except Exception as exc:  # noqa: BLE001 - what is shown never changes the run
            logger.debug("Agent %s: a report failed (%s)", self.run_id, type(exc).__name__)

    # ---- what the clock says -------------------------------------------------------------------------

    def _note_sleep(self) -> None:
        """The PC slept: the clock jumped between two looks at it. Nothing worked and nobody could answer meanwhile, so no
        clock of this run counts that time: "That's 32 minutes on this task" would be false, and a card must not have run
        out while its owner's PC was asleep."""
        real = self.timing.real_clock()
        gap, self._last_real = real - self._last_real, real
        if gap < self.timing.sleep_gap_s:
            return
        logger.info("Agent %s: the PC slept (about %d minute(s))", self.run_id, round(gap / 60))
        self.budget.slept(gap)
        if self._wait is not None:
            self._wait.deadline += gap
        if self._replay_since is not None:
            self._replay_since += gap
        self._wrap_up_deadline += gap
        self._finished_at += gap
        self._paused_at += gap
        self._started_at += gap

    def _tick(self) -> None:
        with self._lock:
            if self._end is not None:
                return
            self._note_sleep()
            now = self._clock()
            if self._init is None and now - self._started_at >= self.timing.init_s:
                logger.warning("Agent %s: no init event in time", self.run_id)
                self._finish_run(OUTCOME_FAILED, f"{GUARD}:no_init", GUARD_WORDS)
                return
            if self._finished is not None:
                self._tick_finished(now)
                return
            if self._wrap_up_code:
                if now >= self._wrap_up_deadline:
                    self._end_unreported(self._wrap_up_code)
                return
            wait = self._wait
            if wait is not None:
                if now >= wait.deadline and wait.at_work:
                    self._batch_went_quiet(wait)
                elif now >= wait.deadline:
                    self._wait = None
                    typed = wait.typed and wait.question.kind != Q_TASK
                    self._emit(AgentEvent(E_QUESTION_GONE, question=replace(wait.question, typed=typed), code=C_NO_ANSWER,
                                          text=HOW_NO_CLICK if typed else HOW_NO_ANSWER))
                    logger.info("Agent %s: no %s to question %s", self.run_id, "click" if typed else "answer",
                                wait.question.kind)
                    if wait.question.kind == Q_WEB:
                        _tell(wait, False)
                        self._deliver(WEB_NO_CLICK_TEXT if typed else WEB_UNANSWERED_TEXT)
                    elif wait.question.kind == Q_CARD:
                        self._deliver(CARD_UNANSWERED_TEXT)    # (nobody clicked: true however many words came)
                    elif typed:
                        self._start_wrap_up(C_NO_CLICK, WRAP_UP_NO_CLICK)
                    else:
                        self._start_wrap_up(C_NO_ANSWER, WRAP_UP_SILENT)
                    self._let_go()
                return
            if self._paused:
                until = self._paused_until
                if until is not None and self._now() >= until + timedelta(seconds=self.timing.resume_margin_s):
                    self._ask(Q_RESUME, RESUME_WORDS, KEEP_GOING_CHOICES, self.timing.keep_going_s)
                elif until is None and now - self._paused_at >= self.timing.limit_wait_s:
                    logger.info("Agent %s: the limit held with no time and no word", self.run_id)
                    self._finish_run(OUTCOME_PARTIAL, C_LIMIT_WAIT, LIMIT_WAITED_WORDS)
                return
            if self._replay_since is not None and now - self._replay_since >= self.timing.replay_s:
                self._replay_since = None
                self._unreplayed = 0
                self._went_idle()
                return
            if self._idle:
                return
            reached = self.budget.reached()
            if reached:
                self._ask_keep_going(reached)

    def _tick_finished(self, now: float) -> None:
        process = self._process
        if not self._stdin_closed and process is not None:
            self._stdin_closed = True
            _close_stdin(process)
        if now - self._finished_at >= self.timing.after_finish_s:
            self._end_finished()

    def _ask_keep_going(self, reached: str) -> None:
        count, noun = ((self.budget.turns - 1, "turn") if reached == policy.BUDGET_TURNS
                       else (round(self.budget.worked_s / 60), "minute"))
        what = f"{count} {noun}" + ("" if count == 1 else "s")
        logger.info("Agent %s: budget reached (%s)", self.run_id, reached)
        self._ask(Q_KEEP_GOING, KEEP_GOING_WORDS.replace("{what}", what), KEEP_GOING_CHOICES, self.timing.keep_going_s)

    def _ask(self, kind: str, text: str, choices: tuple[str, ...], wait_s: float,
             on_answer: Callable[[bool], None] | None = None) -> None:
        question = OwnerQuestion(f"{self.run_id}-{next(self._question_ids)}", kind, text, choices)
        self._wait = _Wait(question, self._clock() + wait_s, on_answer)
        logger.info("Agent %s: question %s for the owner", self.run_id, kind)
        self._emit(AgentEvent(E_QUESTION, question=question))

    def _start_wrap_up(self, code: str, message: str) -> None:
        """The task stops here: the model is told to report and finish (at the next result when it is mid-turn; a
        rest while the limit holds gets no message: nothing could answer), and gets a short while for it."""
        self._wrap_up_code = code
        self._wrap_up_deadline = self._clock() + self.timing.wrap_up_s
        if self._paused:
            self._end_unreported(code)
            return
        self._deliver(message)

    def _end_unreported(self, code: str) -> None:
        words = {C_OWNER_NO: OWNER_NO_WORDS, C_NO_ANSWER: NO_ANSWER_WORDS, C_NO_CLICK: NO_CLICK_WORDS}.get(code,
                                                                                                           NO_FINISH_WORDS)
        self._finish_run(OUTCOME_PARTIAL, code, words)

    def _end_finished(self) -> None:
        assert self._finished is not None
        outcome, summary, outputs = self._finished
        self._finish_run(outcome, C_FINISHED, summary, outputs=outputs, own_words=False)

    # ---- the stream -----------------------------------------------------------------------------------

    def _on_stream(self, event: Mapping[str, Any]) -> None:
        signal = rate_limit_signal(event, now=self._now)
        if signal is not None:
            self._on_limit(signal)
            return
        kind = event.get("type")
        if kind != "result":
            self._replay_since = None       # something came: not idle yet
        if kind == "system":
            if event.get("subtype") == "init":
                self._on_init(event)
            return
        if kind == "result":
            self._on_result(event)
            return
        if kind == "user":
            self._on_user(event)
        elif self._init is None:
            if kind in ("assistant", "stream_event"):
                raise GuardTrip("no_init")
        elif kind == "stream_event":
            self._check_chunk(event)
        elif kind == "assistant":
            self._on_assistant(event)

    def _on_init(self, event: Mapping[str, Any]) -> None:
        summary = check_agent_init(event, self._expected)
        first = self._init is None
        self._init = summary
        if first:
            logger.info("Agent %s: init checked (%d tools, model %s, Claude Code %s)", self.run_id, len(summary.tools),
                        summary.model or "?", summary.version or "?")
            self._emit(AgentEvent(E_STARTED))

    def _check_tool(self, name: Any) -> None:
        """Anything but this run's tools trips the guard, but for a wrong name on Jarvis's own server: the model got a name
        wrong, and nothing can run it (that server is Jarvis's alone and serves only the run's tools, and Claude Code
        answers that there is no such tool). The task goes on."""
        if name in self._expected:
            return
        if isinstance(name, str) and name.startswith(f"mcp__{SERVER}__"):
            logger.info("Agent %s: the model named a tool that does not exist", self.run_id)
            return
        raise GuardTrip("tool_use")

    def _check_chunk(self, event: Mapping[str, Any]) -> None:
        """A chunk of --include-partial-messages: a tool outside the set is seen here first."""
        inner = event.get("event")
        if not isinstance(inner, Mapping) or inner.get("type") != "content_block_start":
            return
        block = inner.get("content_block")
        if isinstance(block, Mapping) and block.get("type") in TOOL_BLOCKS:
            self._check_tool(block.get("name"))

    def _on_assistant(self, event: Mapping[str, Any]) -> None:
        message = event.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        before = self.budget.turns
        self.budget.turn(message.get("id") if isinstance(message, Mapping) else None)
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, Mapping):
                continue
            if block.get("type") in TOOL_BLOCKS:
                self._check_tool(block.get("name"))
            elif block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip():
                self._line_asked = self._line_asked or guard.line_asks(block["text"])     # (Live shows it)
                self._emit(AgentEvent(E_TEXT, text=block["text"]))
        if self.budget.turns != before:
            self._report_usage()

    def _report_usage(self) -> None:
        self._emit(AgentEvent(E_USAGE, turns=self.budget.turns, tools=self._tool_count,
                              input_tokens=self._input_tokens, output_tokens=self._output_tokens,
                              cache_read_tokens=self._cache_read_tokens, cache_write_tokens=self._cache_write_tokens))

    def _on_user(self, event: Mapping[str, Any]) -> None:
        if event.get("isReplay") is True and self._unreplayed > 0:
            self._unreplayed -= 1
            self._replay_since = None

    def _on_result(self, event: Mapping[str, Any]) -> None:
        usage = event.get("usage") if isinstance(event.get("usage"), Mapping) else {}
        self._input_tokens += _count(usage.get("input_tokens"))
        self._output_tokens += _count(usage.get("output_tokens"))
        self._cache_read_tokens += _count(usage.get("cache_read_input_tokens"))
        self._cache_write_tokens += _count(usage.get("cache_creation_input_tokens"))
        if self._finished is not None:
            self._end_finished()
            return
        subtype, text = event.get("subtype"), event.get("result") if isinstance(event.get("result"), str) else ""
        if subtype == "error_max_turns":
            self._finish_run(OUTCOME_PARTIAL, C_MAX_TURNS, MAX_TURNS_WORDS)
            return
        if subtype != "success" or event.get("is_error") is True:
            self._on_failed_result(subtype, text, event.get("api_error_status"))
            return
        if self._init is None:
            raise GuardTrip("no_init")
        if self._unreplayed > 0:
            if self._replay_since is None:
                self._replay_since = self._clock()
            return
        self._went_idle()

    def _on_failed_result(self, subtype: Any, text: str, status: Any) -> None:
        if self._paused:                    # the limit said so already: the call it stopped fails like this
            self._idle = True
            until = limit_reset_time(text, now=self._now) if self._paused_until is None else None
            if until is not None:           # ... and its text says when, as the event did not: it asks at that time
                self._pause(until, limit_reset_words(text, now=self._now))
            return
        found = task_failure(text, now=self._now, status=status)    # only the plan's own words are its limit
        if found is not None and found.kind == LIMIT:
            self._pause(limit_reset_time(text, now=self._now), limit_reset_words(text, now=self._now))
            self._idle = True
            return
        if found is not None and found.kind == NOT_SIGNED_IN:
            self._finish_run(OUTCOME_FAILED, NOT_SIGNED_IN, found.message)
            return
        if found is not None and found.kind == SERVER_BUSY:
            logger.info("Agent %s: a failed result (the server is limiting requests)", self.run_id)
            self._finish_run(OUTCOME_FAILED, C_SERVER_BUSY, SERVER_BUSY_WORDS)
            return
        logger.info("Agent %s: a failed result (%s)", self.run_id, _plain(subtype) or "result_error")
        self._finish_run(OUTCOME_FAILED, ERROR, EXIT_WORDS)

    def _went_idle(self) -> None:
        """The model's turn ended and nothing is on its way."""
        self._idle = True
        if self._outbox:
            for message in self._take_outbox():
                self._send(message)
            return
        if self._wrap_up_code:
            self._end_unreported(self._wrap_up_code)    # told to finish, and it ended its turn without
            return
        if self._wait is not None or self._paused:
            self.budget.rest()
            return
        if not self._nudged:
            self._nudged = True
            logger.info("Agent %s: its turn ended with no finish; prompted once", self.run_id)
            self._send(CONTINUE_TEXT)
            return
        logger.info("Agent %s: its turn ended again with no finish", self.run_id)
        self._finish_run(OUTCOME_PARTIAL, C_NO_FINISH, NO_FINISH_WORDS)

    def _take_outbox(self) -> list[str]:
        messages, self._outbox = self._outbox, []
        return messages

    def _send(self, message: str) -> None:
        process = self._process
        if process is None or not process.send(cli.stream_message(message)):
            return
        self._unreplayed += 1
        self._replay_since = None
        self._idle = False
        self.budget.work()

    def _deliver(self, message: str) -> None:
        """At once when the model is idle, else as soon as its turn ends (D5); while the plan's limit holds, with the
        resume: nothing could answer it now."""
        if self._paused:
            self._held_owner.append(message)
        elif self._idle:
            self._send(message)
        else:
            self._outbox.append(message)

    def _process_ended(self) -> None:
        process = self._process
        with self._lock:
            if self._end is not None:
                return
            if self._finished is not None:
                self._end_finished()
                return
        code = process.wait(2.0) if process is not None else None
        tail = process.stderr_tail() if process is not None else ""
        found = exit_failure(tail, code, now=self._now)
        logger.info("Agent %s: Claude Code ended by itself (%s)", self.run_id, found.detail or found.kind)
        said = task_failure(tail, now=self._now)        # a limit in the plan's own words only, as for a failed result
        if found.kind in (CLI_UNSUPPORTED, NOT_SIGNED_IN):
            self._finish_run(OUTCOME_FAILED, found.kind, found.message)
        elif said is not None and said.kind == LIMIT:
            self._finish_run(OUTCOME_PARTIAL, LIMIT, LIMIT_ENDED_WORDS)
        elif said is not None and said.kind == SERVER_BUSY:
            self._finish_run(OUTCOME_FAILED, C_SERVER_BUSY, SERVER_BUSY_WORDS)
        else:
            self._finish_run(OUTCOME_FAILED, C_EXIT, EXIT_WORDS)

    # ---- the plan's limit ------------------------------------------------------------------------------

    def _on_limit(self, signal: LimitSignal) -> None:
        if signal.overage:
            logger.warning("Agent %s: Claude Code reported extra usage; killed", self.run_id)
            if self._on_overage is not None:
                try:
                    self._on_overage(signal.resets_at)
                except Exception as exc:  # noqa: BLE001 - the run ends either way
                    logger.warning("Agent %s: the pause could not be kept (%s)", self.run_id, type(exc).__name__)
            self._finish_run(OUTCOME_PARTIAL, C_OVERAGE, OVERAGE_WORDS, resets_at=signal.resets_at)
            return
        if self._paused:
            self._paused_until = signal.resets_at or self._paused_until
            return
        when = clock_words(signal.resets_at, self._now()) if signal.resets_at is not None else ""
        self._pause(signal.resets_at, when)

    def _pause(self, until: datetime | None, when: str) -> None:
        """A time the words name is a time it asks at; with none it waits for the owner, or LIMIT_WAIT_HOURS (_tick)."""
        self._paused = True
        self._paused_until = until
        self._paused_at = self._clock()
        self._owner_word = False
        self.budget.rest()
        logger.info("Agent %s: paused by the plan's limit", self.run_id)
        words = LIMIT_WORDS.replace("{when}", when) if when and until is not None else LIMIT_NO_TIME_WORDS
        self._emit(AgentEvent(E_NOTICE, text=words, code=LIMIT))

    def _limit_holds(self) -> bool:
        return self._paused and self._paused_until is not None and self._now() < self._paused_until

    def _resume(self, message: str, extra: list[str]) -> None:
        """The limit let go: everything that waited goes in, in the order it came."""
        self._paused = False
        self._paused_until = None
        self._emit(AgentEvent(E_LIMIT_GONE))
        held, self._held_owner = self._held_owner, []
        for text in [*([message] if message else []), *self._take_outbox(), *held, *extra]:
            self._send(text)

    def _let_go(self) -> None:
        """A wait has just ended while the plan's limit holds with no time given: when the owner has clicked, answered or
        typed since the pause, what waited goes in now, as a line typed then lets it go, and the task tries again (it
        pauses again if the limit still holds). A wait that only ran out leaves the pause waiting for their word."""
        if (self._paused and self._paused_until is None and self._owner_word and self._held_owner
                and self._wait is None and self._end is None and not self._wrap_up_code):
            logger.info("Agent %s: a wait ended with the owner's word while the limit held; it tries again", self.run_id)
            self._resume("", [])

    # ---- the tools (HTTP threads) ----------------------------------------------------------------------

    def _dropped(self, call: Call) -> bool:
        return self._stop.is_set() or call.abandoned.is_set()

    def _invoke(self, name: str, args: Any, call: Call) -> ToolResult:
        """One tools/call, on its own thread: the run's gates, then the tool through the registry. After a Stop, or past
        the timeout, nothing it does is reported: its result is dropped."""
        if self._stop.is_set():
            return error(STOPPED_TOOL_TEXT, STOPPED_CODE)
        number = next(self._calls)
        call.number = number
        call.name = name
        tool = self.registry.tool(name)
        doing = doing_words(name, args if isinstance(args, Mapping) else {}, self._context)
        with self._lock:
            self._tool_count += 1
            self._emit(AgentEvent(E_TOOL_START, call=number, tool=name, text=doing))
            self._nudged = False
            held = self._held(name)
        result = held
        if result is None and tool is not None:
            result = policy.gate(tool, args if isinstance(args, Mapping) else {}, self._firewall)
        if result is None:
            context = replace(self._context, cancelled=lambda: self._dropped(call))
            result = self.registry.call(name, args, context)
        if self._dropped(call):
            logger.debug("Agent %s: a late tool result was dropped", self.run_id)
            return error(STOPPED_TOOL_TEXT, STOPPED_CODE)
        logger.info("Agent %s: tool %d %s %s", self.run_id, number, name, result.code or "ok")
        self._emit(AgentEvent(E_TOOL_END, call=number, tool=name, result=result))
        return result

    def _given_up(self, call: Call) -> None:
        """The endpoint stopped waiting for a tool (past the timeout): the step ends now; its late result is dropped."""
        if call.number and not self._stop.is_set():
            self._emit(AgentEvent(E_TOOL_END, call=call.number, tool=call.name,
                                  result=error(TOO_LONG_TEXT, TOO_LONG_CODE)))

    def _held(self, name: str) -> ToolResult | None:
        """The call is not made: the task has finished, is stopping, waits for "Keep going?", or has reached its budget
        while a card or a batch is up: one thing waits at a time, so "Keep going?" comes when that wait ends (_tick)."""
        if self._finished is not None:
            return ToolResult(ALREADY_FINISHED_TEXT, code=HELD, note="the task has finished")
        tool = self.registry.tool(name)
        ends = tool is not None and tool.name == "finish"
        if self._wrap_up_code and not ends:
            return ToolResult(WRAP_UP_TOOL_TEXT, code=HELD, note="the task is stopping")
        if ends and not self._wrap_up_code and self._wait is not None and self._wait.question.kind == Q_CARD:
            return ToolResult(CARD_WAITS_TEXT, code=HELD, note="a card waits")
        if not ends:
            wait = self._wait
            if wait is not None and wait.question.kind not in (Q_TASK, Q_WEB, Q_CARD):
                return ToolResult(PAUSED_TEXT, code=HELD, note=HELD_NOTE)
            reached = self.budget.reached() if not self._paused else ""
            if reached and wait is not None:
                logger.info("Agent %s: budget reached (%s) while a wait is on; held", self.run_id, reached)
                return ToolResult(BUDGET_AT_A_WAIT_TEXT, code=HELD, note=BUDGET_HELD_NOTE)
            if reached:
                self._ask_keep_going(reached)
                return ToolResult(PAUSED_TEXT, code=HELD, note=HELD_NOTE)
        return None

    def _tool_plan(self, steps: tuple[PlanStep, ...]) -> None:
        self._emit(AgentEvent(E_PLAN, steps=steps))

    def _tool_say(self, text: str) -> None:
        self._line_asked = self._line_asked or guard.line_asks(text)
        self._emit(AgentEvent(E_SAY, text=text))

    def _tool_question(self, text: str, choices: tuple[str, ...]) -> bool:
        with self._lock:
            if self._wait is not None or self._stop.is_set():
                return False
            self._ask(Q_TASK, text, choices, self.timing.question_s)
            return True

    def ask_web(self, text: str, choices: tuple[str, ...], on_answer: Callable[[bool], None]) -> bool:
        """The firewall's card (any thread): False, and nothing shown, when another question waits or the task has
        stopped. ``on_answer(allowed)`` hears the click, or False when no answer came."""
        with self._lock:
            if self._wait is not None or self._stop.is_set():
                return False
            self._ask(Q_WEB, text, choices, self.timing.web_card_s, on_answer)
            return True

    def _tool_card(self, card_id: str) -> bool:
        """A file card is about to show (any thread): the run waits on it (D5). False when another wait is on, or the
        task is ending: then no card shows."""
        with self._lock:
            if self._wait is not None or self._stop.is_set() or self._finished is not None or self._wrap_up_code:
                return False
            self._card_id = card_id
            self._ask(Q_CARD, CARD_WORDS, (), self.timing.card_s)
            return True

    def _tool_card_gone(self, card_id: str) -> None:
        """It could not be shown after all: nothing waits. When the model's turn ended meanwhile (the tool went past its
        time and the model was told so), nothing is on its way to it either: it gets its one prompt now, or the task ends
        in true words (_went_idle), as at any other end of a turn."""
        with self._lock:
            wait = self._wait
            if wait is not None and wait.question.kind == Q_CARD and self._card_id == card_id:
                self._wait = None
                self._emit(AgentEvent(E_QUESTION_GONE, question=wait.question))
                if self._idle and self._end is None:
                    self._went_idle()
                self._let_go()

    def card_at_work(self, card_id: str, working: bool = True) -> bool:
        """The owner's click started the batch of the file card this run waits on (``working``), or it stopped before
        anything ran and the card waits on: from a click on, its card's clock gives way to a watch on its rows, and only
        the card's own end speaks unless they stop ending (card_progress; _batch_went_quiet): nothing here may say "nothing
        was changed" while it is at work, and it has its own Stop. False when nothing waits on that card."""
        with self._lock:
            wait = self._wait
            if wait is None or wait.question.kind != Q_CARD or self._card_id != card_id or self._end is not None:
                return False
            wait.at_work = bool(working)
            wait.deadline = self._clock() + (self.timing.at_work_s if working else self.timing.card_s)
            logger.info("Agent %s: a file card's batch %s", self.run_id, "is at work" if working else "did not run")
            return True

    def card_progress(self, card_id: str) -> bool:
        """A row of that card's batch ended: the watch on it starts again. False when no batch this run waits on is at
        work."""
        with self._lock:
            wait = self._wait
            if (wait is None or wait.question.kind != Q_CARD or self._card_id != card_id or not wait.at_work
                    or self._end is not None):
                return False
            wait.deadline = self._clock() + self.timing.at_work_s
            return True

    def _batch_went_quiet(self, wait: _Wait) -> None:
        """No row of the batch at work has ended for its while: a share that went away, a download that hangs. The run
        stops waiting on it, in true words: it is still at work, and its card has the truth. Nothing here touches the batch,
        and nothing here can say what it changed."""
        self._wait = None
        self._emit(AgentEvent(E_QUESTION_GONE, question=wait.question, text=HOW_BATCH_QUIET))
        logger.info("Agent %s: a file card's batch has gone quiet; the task goes on", self.run_id)
        self._emit(AgentEvent(E_NOTICE, text=BATCH_QUIET_WORDS, code=C_BATCH_QUIET))
        self._deliver(BATCH_QUIET_TEXT)
        self._let_go()

    def card_ended(self, card_id: str, message: str, how: str = "", *, clicked: bool = False) -> bool:
        """The file card this run waits on ended (the owner's click and what came of it, Not now, its time): ``message``
        goes in as the model's next message; ``how``: Live's words for it; ``clicked``: the owner clicked it (its batch
        ran, or Not now), which is their word while the plan's limit holds (_let_go). False when nothing waits on that
        card: nothing happens."""
        with self._lock:
            wait = self._wait
            if wait is None or wait.question.kind != Q_CARD or self._card_id != card_id or self._end is not None:
                return False
            self._wait = None
            self._owner_word = self._owner_word or (self._paused and (clicked or wait.at_work))
            self._emit(AgentEvent(E_QUESTION_GONE, question=wait.question, text=how))
            logger.info("Agent %s: a file card ended", self.run_id)
            self._deliver(message)
            self._let_go()
            return True

    def _tool_finish(self, outcome: str, summary: str, outputs: tuple[str, ...]) -> None:
        with self._lock:
            if self._wrap_up_code and outcome == OUTCOME_DONE:
                outcome = OUTCOME_PARTIAL             # the owner stopped it, or never answered: never "all of it"
            self._finished = (outcome, summary, outputs)
            self._finished_at = self._clock()
            wait, self._wait = self._wait, None
            if wait is not None:
                self._emit(AgentEvent(E_QUESTION_GONE, question=wait.question, text=HOW_ENDED))


def _how_answered(question: OwnerQuestion, text: str) -> str:
    """Live's words for how a question went: its click, or that words answered it; never the words themselves."""
    if question.kind == Q_WEB:
        return HOW_ALLOWED if text == WEB_ALLOW_WORD else HOW_DECLINED
    if question.kind == Q_TASK:
        return HOW_ANSWERED
    if policy.yes_or_no(text) is False:
        return HOW_STOP_HERE
    return HOW_CARRY_ON if question.kind == Q_RESUME else HOW_KEEP_GOING


def _tell(wait: _Wait, allowed: bool) -> None:
    if wait.on_answer is not None:
        try:
            wait.on_answer(allowed)
        except Exception as exc:  # noqa: BLE001 - no call goes out on an answer that was not heard
            logger.warning("Agent: the firewall did not hear an answer (%s)", type(exc).__name__)


def _close_stdin(process: Any) -> None:
    closer = getattr(process, "close_stdin", None)
    if callable(closer):
        closer()


__all__ = ["AgentEnd", "AgentEngine", "AgentEvent", "AgentRun", "OwnerQuestion", "Timing", "check_agent_init",
           "first_message", "owner_message"]
