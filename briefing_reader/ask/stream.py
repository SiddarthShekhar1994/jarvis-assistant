"""One planner run: Claude Code's stream-json output, checked event by event.

    check_init(event)        InitSummary, or GuardTrip: the first event must be system/init with
                             apiKeySource "none", no MCP server and no tool but StructuredOutput
    rate_limit_signal(event) a rate_limit_event that says the run is off the plan: extra usage
                             (usage credits: isUsingOverage, overageInUse, rateLimitType
                             "overage", or the plan "rejected" with overage allowed) or the plan's
                             own limit ("rejected")
    read_result(event, init) the result event -> Plan, or AskFailure(kind)
    run_cli(..., read, init_check, event_check)   one CLI run end to end: no init within 20 s, any
                             guard trip (GuardTrip) or stop (RunStop) from the two checks, a
                             rate-limit signal, the overall timeout and Cancel all kill the process;
                             a run is never retried. ``read(result_event, init)`` turns the result
                             into a value (a Plan -> RunResult.plan, anything else ->
                             RunResult.answer) or an AskFailure. ``progress(what, value)`` hears
                             "started" (True: a process started ahead of the question got the prompt),
                             "init" (InitSummary), "turn" (n), "result" and "stopped"
                             (AskFailure) as they happen (for the LIVE view; a callback that raises
                             changes nothing); ``capture`` keeps the raw reply in RunResult.reply;
                             a command line that holds its stdin (cli.holds_stdin) gets the prompt as one
                             stream-json message and its stdin is closed at the result; ``process`` is
                             a CLI started ahead (ask.prestart) that gets the prompt instead of a new one
                             (it ended before any output: failure detail PRESTART_DIED, and the engine
                             runs cold, the same counted run); ``after_start`` is called once the prompt
                             is on its way
    SayWatch(on_words)       the words of the StructuredOutput tool's "say" as its JSON is written (the
                             stream_event chunks of --include-partial-messages): only that tool's input
                             counts, nothing else is ever shown. ``on_words(text, complete)``; the final
                             validated plan stays the source of truth
    run_planner(...)         run_cli with the planner's checks and read_result (web research uses
                             run_cli with its own: ask.research.run_research); ``on_words`` feeds a SayWatch

Failure kinds (AskFailure.kind): not_signed_in, limit, max_turns, schema_retries, timeout,
cancelled, guard, garbled, cli_unsupported, error, research_cap. Their messages are Jarvis's own words (the only
model or CLI text in one is a sanitized usage-limit reset time) and are safe to show and log; the
stream itself (the plan, the result text, stderr) is never logged (RunResult.reply, kept only with
``capture`` for the LIVE view, never is either). ``total_cost_usd`` is a
client-side estimate, not a charge on a subscription: it is never read.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .cli import OutputTooLong, Process, Runner, holds_stdin, stream_message

logger = logging.getLogger(__name__)

NOT_SIGNED_IN = "not_signed_in"
LIMIT = "limit"
MAX_TURNS = "max_turns"
SCHEMA_RETRIES = "schema_retries"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
GUARD = "guard"
GARBLED = "garbled"
CLI_UNSUPPORTED = "cli_unsupported"
ERROR = "error"
RESEARCH_CAP = "research_cap"   # web research wanted more searches or page reads than allowed
FAILURE_KINDS = (NOT_SIGNED_IN, LIMIT, MAX_TURNS, SCHEMA_RETRIES, TIMEOUT, CANCELLED, GUARD, GARBLED,
                 CLI_UNSUPPORTED, ERROR, RESEARCH_CAP)

STRUCTURED_TOOL = "StructuredOutput"
INIT_TIMEOUT_S = 20.0
POLL_S = 0.25                 # Cancel and the timeouts are checked at least this often
SAY_CAP = 300
QUESTION_CAP = 200
LINES_CAP = 8
LINE_CAP = 12000
QUERY_CAP = 200
WHY_CAP = 200
WEB_QUESTION_CAP = 200
REMEMBER_CAP = 160                 # one suggested note (cli.REMEMBER_PROPERTY)
REMEMBER_ITEMS = 1
ACCOUNT_CAP = 24
KIND_PREFIXES = ("Calendar:", "Email:", "Reply:", "RSVP:", "Move:", "Cancel:", "Todo:", "Open:", "Slack:",
                 "Share:")

NOTHING = "nothing was proposed"
MESSAGES = {
    NOT_SIGNED_IN: "Run: claude auth login --claudeai (Claude Code is not signed in to your claude.ai plan)",
    LIMIT: f"Your Claude plan's usage limit is reached; {NOTHING}",
    MAX_TURNS: f"The planner ran out of turns; {NOTHING} - try a simpler request",
    SCHEMA_RETRIES: f"The planner's answer did not fit Jarvis's format; {NOTHING}",
    TIMEOUT: f"Took too long; {NOTHING}",
    CANCELLED: f"Cancelled; {NOTHING}",
    GUARD: f"Claude Code started with something Ask does not allow, so it was stopped; {NOTHING}",
    GARBLED: f"The planner's answer could not be read; {NOTHING}",
    CLI_UNSUPPORTED: "This Claude Code is not supported by Ask yet",
    ERROR: f"Claude Code failed; {NOTHING}",
    RESEARCH_CAP: f"Jarvis stopped the web research: it wanted more searches or page reads than allowed; {NOTHING}",
}
OVERAGE = "overage"           # AskFailure.detail: Claude Code said this run uses extra usage
REJECTED = "rejected"         # AskFailure.detail: Claude Code said the plan's limit is reached
PRESTART_DIED = "prestart_died"   # AskFailure.detail: a process started ahead ended before any output (run cold)
NO_INIT = "no_init"           # AskFailure.detail: no init event in time
# How a run's process came (RunResult.start; the per-Ask log line says it).
START_AHEAD = "prestarted"
START_COLD = "cold"
START_FELL_BACK = "cold after a prestarted one failed"
STREAM_EXIT_S = 1.5           # after the result a CLI that held its stdin has this long to end before it is killed
OVERAGE_MESSAGE = ("Turn usage credits off at claude.ai/settings/usage: Claude Code said this request would use "
                   "them (extra usage), so Ask stopped it; " + NOTHING)

_CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_WORD_RE = re.compile(r"[A-Za-z0-9._\-\[\]]{1,80}")
_LIMIT_RE = re.compile(r"usage limit|rate.?limit|limit (?:reached|exceeded)|hit your (?:usage )?limit|"
                       r"out of (?:extra )?usage|(?:5-hour|weekly|session) limit", re.IGNORECASE)
_AUTH_RE = re.compile(r"invalid api key|please run /login|not logged in|log ?in again|oauth token (?:has )?expired|"
                      r"authentication_error|authentication failed|unauthori[sz]ed|\b401\b|credit balance",
                      re.IGNORECASE)
_UNKNOWN_OPTION_RE = re.compile(r"unknown option|unknown command|error: option .* argument|invalid choice",
                                re.IGNORECASE)
_RESET_RE = re.compile(r"resets?\s+(?:at\s+)?([0-9]{1,2}(?::[0-9]{2})?\s*(?:[ap]\.?m\.?)?"
                       r"(?:\s*\([A-Za-z_/+\-]{1,40}\))?)", re.IGNORECASE)
_RESET_EPOCH_RE = re.compile(r"\|\s*(\d{10})\b")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class InitSummary:
    """What the init event said (safe to log: no path, no prompt)."""

    api_key_source: str
    tools: tuple[str, ...]
    mcp_servers: int
    model: str
    version: str
    structured_tool: bool        # StructuredOutput is available: the result carries structured_output
    permission_mode: str = ""    # permissionMode as a plain word ("dontAsk"; "" when not said)


@dataclass(frozen=True)
class MailSearch:
    """The first run's request to read mail (planner text: shown to the owner, never logged)."""

    account: str
    query: str
    why: str


@dataclass(frozen=True)
class WebRequest:
    """The first run's request for web research (planner text: shown to the owner, never logged;
    the question goes to the research only when it passes ask.research.check_question)."""

    question: str
    why: str


@dataclass(frozen=True)
class Plan:
    say: str
    question: str
    lines: tuple[str, ...]
    search: MailSearch | None = None
    via_fallback: bool = False   # read from the result text (StructuredOutput was not available)
    web: WebRequest | None = None   # web_research (only when the run was allowed to ask for it)
    remember: tuple[str, ...] = ()  # a suggested note (only when the run was allowed to suggest one)


@dataclass(frozen=True)
class AskFailure:
    kind: str
    message: str
    detail: str = ""             # a safe reason code ("no_init", "tools", "exit 1"); logged


@dataclass(frozen=True)
class RunStats:
    duration_ms: int = 0
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    subtype: str = ""
    exit_code: int | None = None


@dataclass(frozen=True)
class LimitSignal:
    """A rate_limit_event that stopped the run (fields safe to log)."""

    overage: bool                    # extra usage (usage credits) would pay for this run
    resets_at: datetime | None       # when the plan's limiting window resets (None when not said)


@dataclass(frozen=True)
class RunResult:
    plan: Plan | None = None
    failure: AskFailure | None = None
    init: InitSummary | None = None
    stats: RunStats = field(default_factory=RunStats)
    limit: LimitSignal | None = None   # set when a rate_limit_event stopped the run
    started: bool = True               # False: refused before any process (not a planner run)
    # With run_planner(capture=True) only: the planner's raw reply as it came (structured_output as
    # indented JSON, else the result text). Shown in the LIVE view; never logged or saved.
    reply: str = field(default="", repr=False)
    # What run_cli's ``read`` made of the result when it is not a Plan (web research: a
    # ResearchAnswer, built from web text: shown, never logged).
    answer: Any = field(default=None, repr=False)
    web: Any = None                    # web research: the run's research.WebLog (its repr is counts only)
    start: str = ""                    # START_*: how its process came ("": the plain path, no prestart seam)
    first_words_s: float | None = None     # seconds from the prompt's send to the say's first words (None: none came)
    say_complete_s: float | None = None    # ... and to the say written whole

    @property
    def outcome(self) -> str:
        """"ok" or the failure kind (what usage.json and the log record)."""
        return "ok" if self.failure is None else self.failure.kind


class GuardTrip(Exception):
    """The run is not what Ask allows; ``reason`` is a fixed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RunStop(Exception):
    """A check of run_cli stops the run with ``failure`` (its own words; the process is killed)."""

    def __init__(self, failure: AskFailure) -> None:
        super().__init__(failure.kind)
        self.failure = failure


def failure(kind: str, detail: str = "", message: str = "") -> AskFailure:
    return AskFailure(kind, message or MESSAGES.get(kind, MESSAGES[ERROR]), detail)


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

def parse_event(line: str) -> dict[str, Any] | None:
    """One stream-json line as a dict with a "type", else None (garbage is skipped, never shown)."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) and isinstance(event.get("type"), str) else None


def _word(value: Any) -> str:
    return value if isinstance(value, str) and _WORD_RE.fullmatch(value) else ("other" if value else "")


def check_init(event: Mapping[str, Any]) -> InitSummary:
    """The init event as Ask requires it, else GuardTrip (the caller kills the process)."""
    if event.get("type") != "system" or event.get("subtype") != "init":
        raise GuardTrip("not_init")
    if event.get("apiKeySource") != "none":
        raise GuardTrip("api_key_source")
    servers = event.get("mcp_servers")
    if not isinstance(servers, list) or servers:
        raise GuardTrip("mcp_servers")
    tools = event.get("tools")
    if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
        raise GuardTrip("tools")
    if set(tools) - {STRUCTURED_TOOL}:
        raise GuardTrip("tools")
    return InitSummary(api_key_source="none", tools=tuple(tools), mcp_servers=0, model=_word(event.get("model")),
                       version=_word(event.get("claude_code_version")), structured_tool=STRUCTURED_TOOL in tools,
                       permission_mode=_word(event.get("permissionMode")))


def check_event(event: Mapping[str, Any]) -> None:
    """Any later event: a tool call other than StructuredOutput trips the guard. A ``stream_event`` (a chunk of
    --include-partial-messages) is normal: the whole message it belongs to still comes as an ``assistant`` event."""
    if event.get("type") != "assistant":
        return
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    for block in content if isinstance(content, list) else ():
        if isinstance(block, dict) and block.get("type") in ("tool_use", "server_tool_use", "mcp_tool_use") \
                and block.get("name") != STRUCTURED_TOOL:
            raise GuardTrip("tool_use")


def _epoch_time(value: Any, now: Callable[[], datetime]) -> datetime | None:
    """A Unix time in the owner's local time: with the PC's own clock (``now`` in this PC's zone) the offset in force
    AT that moment - a summer-time change between now and then moves it, so "resets 12:30 PM" is the wall clock's
    then, never now's offset applied to it; with a clock in another fixed zone (a test's) in that zone."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    try:
        moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
        current = now()
        zone = current.tzinfo
        if zone is None or zone.utcoffset(current) == current.astimezone().utcoffset():
            return moment.astimezone()
        return moment.astimezone(zone)
    except (OverflowError, OSError, ValueError):
        return None


def rate_limit_signal(event: Mapping[str, Any], *,
                      now: Callable[[], datetime] = lambda: datetime.now().astimezone()) -> LimitSignal | None:
    """A rate_limit_event that means this run is not paid for by the plan, else None. Claude Code
    2.1.293 emits {"type": "rate_limit_event", "rate_limit_info": {status, rateLimitType,
    resetsAt, overageStatus, isUsingOverage, overageInUse, ...}} when the limits it reads from the
    API's response headers change. Extra usage (usage credits) is any of: isUsingOverage or
    overageInUse true, rateLimitType "overage", or status "rejected" with overageStatus allowed;
    status "rejected" alone is the plan's own limit. "allowed" / "allowed_warning" on the plan
    are not a signal. A malformed event is ignored (Claude Code drops those too)."""
    if event.get("type") != "rate_limit_event":
        return None
    info = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return None
    rejected = info.get("status") == "rejected"
    overage = (info.get("isUsingOverage") is True or info.get("overageInUse") is True
               or info.get("rateLimitType") == "overage"
               or (rejected and info.get("overageStatus") in ("allowed", "allowed_warning")))
    if not overage and not rejected:
        return None
    return LimitSignal(overage, _epoch_time(info.get("resetsAt"), now))


def _limit_failure(signal: LimitSignal, now: datetime) -> AskFailure:
    if signal.overage:
        return failure(LIMIT, OVERAGE, OVERAGE_MESSAGE)
    if signal.resets_at is not None:
        return failure(LIMIT, REJECTED, f"Your Claude plan's usage limit is reached (it resets "
                                        f"{clock_words(signal.resets_at, now)}); {NOTHING}")
    return failure(LIMIT, REJECTED)


def clock_words(moment: datetime, now: datetime) -> str:
    """"2:52 PM", with the weekday ("Mon 2:52 PM") when it is 20 hours or more after ``now``."""
    hour = moment.hour % 12 or 12
    text = f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
    try:
        later = moment - now >= timedelta(hours=20)
    except TypeError:
        later = False
    return f"{moment.strftime('%a')} {text}" if later else text


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def stats_of(event: Mapping[str, Any], *, measured_ms: int = 0, exit_code: int | None = None) -> RunStats:
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    return RunStats(duration_ms=_count(event.get("duration_ms")) or measured_ms, turns=_count(event.get("num_turns")),
                    input_tokens=_count(usage.get("input_tokens")), output_tokens=_count(usage.get("output_tokens")),
                    cache_read_tokens=_count(usage.get("cache_read_input_tokens")),
                    cache_write_tokens=_count(usage.get("cache_creation_input_tokens")),
                    subtype=_word(event.get("subtype")), exit_code=exit_code)


def _one_line(text: str) -> str:
    return " ".join(_CONTROL_RE.sub(" ", text).split())


_NOT_A_REQUEST = WebRequest("", "")   # web_research that does not fit the schema: the answer is garbled


def _web_request(wanted: Any) -> WebRequest | None:
    """A web_research value as the schema allows it; None for an empty question (no web research
    asked: the rest of the answer stands, as with an empty gmail_search); _NOT_A_REQUEST when it
    does not fit (the whole answer is then garbled)."""
    if not isinstance(wanted, dict):
        return _NOT_A_REQUEST
    question, why = wanted.get("question"), wanted.get("why", "")
    if not isinstance(question, str) or not isinstance(why, str):
        return _NOT_A_REQUEST
    if len(question) > WEB_QUESTION_CAP or len(why) > WHY_CAP:
        return _NOT_A_REQUEST
    question = _one_line(question)
    return WebRequest(question, _one_line(why)) if question else None


def _remember(wanted: Any) -> tuple[str, ...] | None:
    """A ``remember`` value as the schema allows it (a list of at most one string of at most 160
    characters; empty ones dropped); None when it does not fit (the whole answer is then garbled)."""
    if not isinstance(wanted, list) or len(wanted) > REMEMBER_ITEMS:
        return None
    if not all(isinstance(item, str) and len(item) <= REMEMBER_CAP for item in wanted):
        return None
    return tuple(text for text in (_one_line(item) for item in wanted) if text)


def _plan_from(data: Any, *, allow_search: bool, allow_web: bool = False, fallback: bool = False,
               allow_remember: bool = False) -> Plan | None:
    """structured_output (or the fallback's JSON) shape-checked locally; None when it does not fit.
    ``gmail_search`` / ``web_research`` / ``remember`` are read only when allowed (ignored otherwise)."""
    if not isinstance(data, dict):
        return None
    say, question, lines = data.get("say", ""), data.get("question", ""), data.get("lines")
    if not isinstance(say, str) or not isinstance(question, str) or not isinstance(lines, list):
        return None
    say, question = _one_line(say), _one_line(question)
    if len(say) > SAY_CAP or len(question) > QUESTION_CAP or len(lines) > LINES_CAP:
        return None
    if not all(isinstance(line, str) and len(line) <= LINE_CAP for line in lines):
        return None
    search = None
    wanted = data.get("gmail_search")
    if wanted is not None and allow_search:
        if not isinstance(wanted, dict):
            return None
        account, query, why = wanted.get("account"), wanted.get("query"), wanted.get("why", "")
        if not isinstance(account, str) or not isinstance(query, str) or not isinstance(why, str):
            return None
        if len(account) > ACCOUNT_CAP or len(query) > QUERY_CAP or len(why) > WHY_CAP:
            return None
        search = MailSearch(account.strip().casefold(), query.strip(), _one_line(why))
    web = None
    if allow_web and data.get("web_research") is not None:
        web = _web_request(data.get("web_research"))
        if web is _NOT_A_REQUEST:
            return None
    remember: tuple[str, ...] = ()
    if allow_remember and data.get("remember") is not None:
        found = _remember(data.get("remember"))
        if found is None:
            return None
        remember = found
    kept = tuple(line.strip() for line in lines if line.strip())
    return Plan(say, question, kept, search, fallback, web, remember)


def _fallback_plan(text: str, *, allow_search: bool, allow_web: bool = False,
                   allow_remember: bool = False) -> Plan | None:
    """Only when init showed no StructuredOutput: the result text as JSON (code fences dropped),
    else its lines that start with a known kind."""
    text = text or ""
    fenced = _FENCE_RE.match(text)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        data = None
    plan = _plan_from(data, allow_search=allow_search, allow_web=allow_web, fallback=True,
                      allow_remember=allow_remember) if data is not None else None
    if plan is not None:
        return plan
    found = [line.strip() for line in text.splitlines() if line.strip().startswith(KIND_PREFIXES)]
    if not found:
        return None
    return Plan("", "", tuple(found[:LINES_CAP]), None, True)


def _reset_words(text: str, now: Callable[[], datetime]) -> str:
    """"resets 3pm (Europe/London)" from a usage-limit text, sanitized ("" when none)."""
    match = _RESET_RE.search(text or "")
    if match:
        words = " ".join(match.group(1).split())
        return words if re.fullmatch(r"[\w :/()+.\-]{1,60}", words) else ""
    epoch = _RESET_EPOCH_RE.search(text or "")
    if epoch:
        try:
            moment = datetime.fromtimestamp(int(epoch.group(1))).astimezone(now().tzinfo)
        except (OverflowError, OSError, ValueError):
            return ""
        hour = moment.hour % 12 or 12
        return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
    return ""


def classify_text(text: str, *, now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
                  status: Any = None) -> AskFailure | None:
    """A usage limit or a sign-in problem in a result / stderr text (or an api_error_status)."""
    if status in (401, 403) or (text and _AUTH_RE.search(text)):
        return failure(NOT_SIGNED_IN, "auth")
    if status == 429 or (text and _LIMIT_RE.search(text)):
        reset = _reset_words(text or "", now)
        message = (f"Your Claude plan's usage limit is reached (it resets {reset}); {NOTHING}" if reset
                   else MESSAGES[LIMIT])
        return failure(LIMIT, "limit", message)
    return None


def result_failure(event: Mapping[str, Any], init: InitSummary | None, *,
                   now: Callable[[], datetime] = lambda: datetime.now().astimezone()) -> AskFailure | None:
    """The result event's failure, when it is one (the subtypes, an error, no checked init first);
    None for a success after a checked init (its answer is then read by the caller)."""
    subtype = event.get("subtype")
    text = event.get("result") if isinstance(event.get("result"), str) else ""
    status = event.get("api_error_status")
    if subtype == "error_max_turns":
        return failure(MAX_TURNS, "error_max_turns")
    if subtype == "error_max_structured_output_retries":
        return failure(SCHEMA_RETRIES, "error_max_structured_output_retries")
    if subtype != "success" or event.get("is_error") is True:
        return classify_text(text, now=now, status=status) or failure(ERROR, _word(subtype) or "result_error")
    if init is None:
        return failure(GUARD, "no_init")
    return None


def read_result(event: Mapping[str, Any], init: InitSummary | None, *, allow_search: bool, allow_web: bool = False,
                now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
                allow_remember: bool = False) -> Plan | AskFailure:
    """The result event -> Plan or AskFailure. A plan needs a checked init first. ``remember`` is
    read only with ``allow_remember`` (a run that was offered it in its schema)."""
    found = result_failure(event, init, now=now)
    if found is not None:
        return found
    assert init is not None
    text = event.get("result") if isinstance(event.get("result"), str) else ""
    data = event.get("structured_output")
    if data is not None:
        plan = _plan_from(data, allow_search=allow_search, allow_web=allow_web, allow_remember=allow_remember)
        return plan if plan is not None else failure(GARBLED, "shape")
    if init.structured_tool:
        return failure(GARBLED, "no_structured_output")
    plan = _fallback_plan(text, allow_search=allow_search, allow_web=allow_web, allow_remember=allow_remember)
    return plan if plan is not None else failure(GARBLED, "fallback")


# --------------------------------------------------------------------------
# The reply's words as they are written
# --------------------------------------------------------------------------

_JSON_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_REPLACEMENT = "\ufffd"
SAY_KEY = "say"


class _JsonString:
    """One JSON string's body, a character at a time (an escape or a surrogate pair may be split anywhere between
    chunks); ``text()`` is what is decoded so far: half of a surrogate pair waits for its other half."""

    def __init__(self) -> None:
        self._chars: list[str] = []
        self._escape = False
        self._hex: str | None = None    # the digits of a \uXXXX so far
        self._high = 0                  # a high surrogate waiting for its low half

    def text(self) -> str:
        return "".join(self._chars)

    def push(self, char: str) -> bool:
        """One more character; True at the closing quote."""
        if self._hex is not None:
            self._hex += char
            if len(self._hex) == 4:
                digits, self._hex = self._hex, None
                try:
                    self._unit(int(digits, 16))
                except ValueError:
                    self._flush_high()
                    self._chars.append(_REPLACEMENT)
            return False
        if self._escape:
            self._escape = False
            if char == "u":
                self._hex = ""
            else:
                self._flush_high()
                self._chars.append(_JSON_ESCAPES.get(char, char))
            return False
        if char == "\\":
            self._escape = True
            return False
        if char == '"':
            self._flush_high()
            return True
        self._unit(ord(char))           # a raw half of a pair too: a chunk may end between the two
        return False

    def _unit(self, unit: int) -> None:
        if 0xD800 <= unit <= 0xDBFF:
            self._flush_high()
            self._high = unit
        elif 0xDC00 <= unit <= 0xDFFF:
            if self._high:
                self._chars.append(chr(0x10000 + ((self._high - 0xD800) << 10) + (unit - 0xDC00)))
                self._high = 0
            else:
                self._chars.append(_REPLACEMENT)
        else:
            self._flush_high()
            self._chars.append(chr(unit))

    def _flush_high(self) -> None:
        if self._high:
            self._high = 0
            self._chars.append(_REPLACEMENT)


class _SayScanner:
    """Walks one JSON object as it is written and decodes its top-level "say" (any other key's value is skipped,
    whatever it holds). Anything that is not the JSON it expects ends the walk (``broken``): nothing more is shown,
    and the final plan decides. ``done``: the say's closing quote came."""

    _START, _KEY_OR_END, _KEY, _COLON, _VALUE, _SAY, _SKIP_STRING, _SKIP_ATOM, _SKIP_NESTED = range(9)

    def __init__(self) -> None:
        self.done = False
        self.broken = False
        self._state = self._START
        self._key = _JsonString()
        self._say: _JsonString | None = None
        self._depth = 0                 # how deep inside a skipped object or list
        self._skip_escape = False

    def text(self) -> str:
        return self._say.text() if self._say is not None else ""

    def feed(self, chunk: str) -> None:
        for char in chunk:
            if self.done or self.broken:
                return
            self._step(char)

    def _step(self, char: str) -> None:
        state = self._state
        if state == self._SAY:
            assert self._say is not None
            self.done = self._say.push(char)
        elif state == self._START:
            if char == "{":
                self._state = self._KEY_OR_END
            elif not char.isspace():
                self.broken = True
        elif state == self._KEY_OR_END:
            if char == '"':
                self._key = _JsonString()
                self._state = self._KEY
            elif char != "," and not char.isspace():
                self.broken = True      # the object ended (or is not JSON) with no say: nothing more to find
        elif state == self._KEY:
            if self._key.push(char):
                self._state = self._COLON
        elif state == self._COLON:
            if char == ":":
                self._state = self._VALUE
            elif not char.isspace():
                self.broken = True
        elif state == self._VALUE:
            self._value(char)
        elif state == self._SKIP_STRING:
            if self._skip_escape:
                self._skip_escape = False
            elif char == "\\":
                self._skip_escape = True
            elif char == '"':
                self._state = self._SKIP_NESTED if self._depth else self._KEY_OR_END
        elif state == self._SKIP_ATOM:
            if char == ",":
                self._state = self._KEY_OR_END
            elif char == "}":
                self.broken = True
        elif state == self._SKIP_NESTED:
            if char == '"':
                self._state = self._SKIP_STRING
            elif char in "{[":
                self._depth += 1
            elif char in "}]":
                self._depth -= 1
                if not self._depth:
                    self._state = self._KEY_OR_END

    def _value(self, char: str) -> None:
        if char.isspace():
            return
        if char == '"':
            if self._key.text() == SAY_KEY:
                self._say = _JsonString()
                self._state = self._SAY
            else:
                self._state = self._SKIP_STRING
        elif char in "{[":
            self._depth = 1
            self._state = self._SKIP_NESTED
        else:
            self._state = self._SKIP_ATOM   # a number, true, false, null (a "say" that is one is no say)


class SayWatch:
    """The words of the reply's "say" as its JSON is written, from a run's ``stream_event`` chunks (feed, for
    run_cli's ``watch``). Only the input of a StructuredOutput tool call of the main conversation counts: another
    tool's input, a text block or a sub-agent's chunk is never looked at.

    ``on_words(text, complete)`` hears the words so far, one line (as stream.Plan.say has them; a space at the end
    says the last word has ended), each time they grow, and once ``complete`` when the closing quote came. A second
    call of the tool (the CLI's retry after its schema check) starts over, heard only from where its words differ from
    the first's (the same words again: nothing new). More than SAY_CAP characters is no say a plan can have: nothing
    more is heard. ``first_at`` / ``complete_at``: ``clock()`` when the first words came and the say was whole. A
    callback that raises changes nothing."""

    def __init__(self, on_words: Callable[[str, bool], None], *, clock: Callable[[], float] = time.monotonic) -> None:
        self._on_words = on_words
        self._clock = clock
        self._scan: _SayScanner | None = None
        self._index: Any = None
        self._earlier = ""              # what an earlier call of the tool wrote whole
        self._heard = ("", False)
        self.first_at: float | None = None
        self.complete_at: float | None = None

    def feed(self, event: Mapping[str, Any]) -> None:
        if event.get("type") != "stream_event" or event.get("parent_tool_use_id"):
            return
        inner = event.get("event")
        if not isinstance(inner, Mapping):
            return
        kind = inner.get("type")
        if kind == "content_block_delta":
            self._delta(inner)
        elif kind == "content_block_start":
            block = inner.get("content_block")
            if isinstance(block, Mapping) and block.get("type") == "tool_use" and block.get("name") == STRUCTURED_TOOL:
                self._index, self._scan = inner.get("index"), _SayScanner()
            else:
                self._scan = None
        elif kind == "message_start" or (kind == "content_block_stop" and inner.get("index") == self._index):
            self._scan = None

    def _delta(self, inner: Mapping[str, Any]) -> None:
        scan = self._scan
        delta = inner.get("delta")
        if scan is None or inner.get("index") != self._index or not isinstance(delta, Mapping):
            return
        chunk = delta.get("partial_json")
        if delta.get("type") != "input_json_delta" or not isinstance(chunk, str) or scan.done or scan.broken:
            return
        scan.feed(chunk)
        raw = scan.text()
        text = _one_line(raw)
        if not text or len(text) > SAY_CAP:
            return
        complete = scan.done
        if self._earlier and (text == self._earlier if complete else self._earlier.startswith(text)):
            return                      # the tool called again: the same words so far
        shown = text + " " if not complete and raw[-1:].isspace() else text
        if (shown, complete) == self._heard:
            return
        self._heard = (shown, complete)
        if self.first_at is None:
            self.first_at = self._clock()
        if complete:
            self.complete_at = self._clock()
            self._earlier = text
        try:
            self._on_words(shown, complete)
        except Exception:  # noqa: BLE001 - a broken callback never changes the run
            pass


# --------------------------------------------------------------------------
# The engine loop
# --------------------------------------------------------------------------

Progress = Callable[[str, Any], None]


def _report(progress: Progress | None, what: str, value: Any = None) -> None:
    """Tell ``progress`` (the LIVE view); whatever it does, the run goes on unchanged."""
    if progress is None:
        return
    try:
        progress(what, value)
    except Exception:  # noqa: BLE001 - a broken callback never changes the run
        pass


def raw_reply(event: Mapping[str, Any] | None) -> str:
    """The planner's reply in a result event as it came: structured_output as indented JSON, else
    the result text ("" when there is neither)."""
    if not isinstance(event, Mapping):
        return ""
    data = event.get("structured_output")
    if data is not None:
        try:
            return json.dumps(data, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""
    text = event.get("result")
    return text if isinstance(text, str) else ""


Reader = Callable[[Mapping[str, Any], InitSummary | None], Any]   # (result event, init) -> value or AskFailure


def _close_stdin(process: Process) -> None:
    """End of input for a process that holds its stdin (a runner's process without the method has none to hold)."""
    closer = getattr(process, "close_stdin", None)
    if callable(closer):
        try:
            closer()
        except OSError:
            pass


def _discard_quietly(process: Process) -> None:
    try:
        process.kill()
        process.close()
    except OSError:
        pass


def run_planner(runner: Runner, argv: Sequence[str], prompt: str, *, env: Mapping[str, str], cwd: Path,
                timeout_s: float, allow_search: bool, cancel: threading.Event | None = None,
                clock: Callable[[], float] = time.monotonic, init_timeout_s: float = INIT_TIMEOUT_S,
                poll_s: float = POLL_S,
                now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
                progress: Progress | None = None, capture: bool = False, allow_web: bool = False,
                allow_remember: bool = False, on_words: Callable[[str, bool], None] | None = None,
                process: Process | None = None, after_start: Callable[[], None] | None = None) -> RunResult:
    """One planner run (run_cli with check_init, check_event and read_result). ``allow_search`` /
    ``allow_web`` / ``allow_remember``: the run may ask to read mail / for web research / suggest a
    note to remember. ``on_words``: the say's words as they are written (SayWatch; the result's first_words_s
    and say_complete_s say when, from the send). ``process`` / ``after_start``: run_cli's."""
    watch = SayWatch(on_words, clock=clock) if on_words is not None else None
    sent = clock()
    result = run_cli(runner, argv, prompt, env=env, cwd=cwd, timeout_s=timeout_s,
                     read=lambda event, init: read_result(event, init, allow_search=allow_search, allow_web=allow_web,
                                                          now=now, allow_remember=allow_remember),
                     cancel=cancel, clock=clock, init_timeout_s=init_timeout_s, poll_s=poll_s, now=now,
                     progress=progress, capture=capture, watch=watch.feed if watch is not None else None,
                     process=process, after_start=after_start)
    if watch is None or watch.first_at is None:
        return result
    return replace(result, first_words_s=watch.first_at - sent,
                   say_complete_s=watch.complete_at - sent if watch.complete_at is not None else None)


def run_cli(runner: Runner, argv: Sequence[str], prompt: str, *, env: Mapping[str, str], cwd: Path,
            timeout_s: float, read: Reader, init_check: Callable[[Mapping[str, Any]], InitSummary] = check_init,
            event_check: Callable[[Mapping[str, Any]], None] = check_event,
            cancel: threading.Event | None = None,
            clock: Callable[[], float] = time.monotonic, init_timeout_s: float = INIT_TIMEOUT_S,
            poll_s: float = POLL_S,
            now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
            progress: Progress | None = None, capture: bool = False,
            watch: Callable[[Mapping[str, Any]], None] | None = None, process: Process | None = None,
            after_start: Callable[[], None] | None = None) -> RunResult:
    """Start the CLI with ``prompt`` on stdin and read its events until the result, a guard trip
    or stop, the timeout or Cancel (the process is killed then). Never raises for the CLI's
    behaviour. ``init_check`` checks the first event (InitSummary, or GuardTrip / RunStop),
    ``event_check`` every later one (GuardTrip / RunStop stop the run), ``read`` the result
    event. ``progress`` / ``capture``: see the module docs (neither changes the run). ``watch`` hears
    every stream_event that passed the checks (a callback that raises changes nothing). ``process``: a CLI
    started ahead of this question gets the prompt instead of a new one; ``after_start``: called once the prompt
    is on its way (either way)."""
    started = clock()
    stream_input = holds_stdin(argv)
    payload = stream_message(prompt) if stream_input else prompt
    ahead = process is not None
    if process is not None:
        if not process.send(payload):   # it ended while it waited: nothing was sent
            _discard_quietly(process)
            return RunResult(failure=failure(ERROR, PRESTART_DIED))
    else:
        try:
            process = runner.start(argv, stdin_text=payload, env=env, cwd=cwd)
        except OSError as exc:
            logger.warning("Ask: Claude Code could not be started (%s)", type(exc).__name__)
            return RunResult(failure=failure(ERROR, "start"))
    _report(progress, "started", True if ahead else None)
    if after_start is not None:
        try:
            after_start()
        except Exception as exc:  # noqa: BLE001 - only prepares the next request
            logger.warning("Ask: the next Claude Code could not be prepared (%s)", type(exc).__name__)
    init: InitSummary | None = None
    result_event: Mapping[str, Any] | None = None
    stop: AskFailure | None = None
    limit: LimitSignal | None = None
    garbage = 0
    turns = 0
    output = False       # an event other than Claude Code's own start came
    try:
        while True:
            if cancel is not None and cancel.is_set():
                stop = failure(CANCELLED, "cancel")
                break
            elapsed = clock() - started
            if elapsed >= timeout_s:
                stop = failure(TIMEOUT, "timeout")
                break
            if init is None and elapsed >= init_timeout_s:
                stop = failure(GUARD, NO_INIT)
                break
            wait = min(poll_s, max(0.0, timeout_s - elapsed))
            if init is None:
                wait = min(wait, max(0.0, init_timeout_s - elapsed))
            try:
                line = process.read_line(wait)
            except OutputTooLong:
                stop = failure(GARBLED, "line_too_long")
                break
            if line is None:
                break
            if not line:
                continue
            event = parse_event(line)
            if event is None:
                garbage += 1
                continue
            limit = rate_limit_signal(event, now=now)
            if limit is not None:   # off the plan (extra usage) or at its limit: stop before any more turns
                stop = _limit_failure(limit, now())
                break
            try:
                if init is None:
                    if event.get("type") == "result":
                        result_event = event   # e.g. a sign-in error before any init: never a plan
                        _report(progress, "result")
                        break
                    init = init_check(event)
                    _report(progress, "init", init)
                    continue
                event_check(event)
            except GuardTrip as trip:
                stop = failure(GUARD, trip.reason)
                break
            except RunStop as stopped:
                stop = stopped.failure
                break
            if event.get("type") != "system":
                output = True
            if event.get("type") == "stream_event":
                if watch is not None:
                    try:
                        watch(event)
                    except Exception:  # noqa: BLE001 - what is shown never changes the run
                        pass
                continue
            if event.get("type") == "assistant":
                turns += 1
                _report(progress, "turn", turns)
            if event.get("type") == "result":
                result_event = event
                _report(progress, "result")
                break
    finally:
        _close_stdin(process)   # read until the result, then close it: a CLI that holds its stdin ends now
        if stop is not None:
            process.kill()
            _report(progress, "stopped", stop)
        exit_code = process.wait((STREAM_EXIT_S if stream_input else 5.0) if stop is None else 2.0)
        if exit_code is None:
            process.kill()
            exit_code = process.wait(2.0)
        tail = process.stderr_tail()
        process.close()
    measured = int((clock() - started) * 1000)
    if garbage:
        logger.info("Ask: skipped %d unreadable output line(s)", garbage)
    if stop is not None:
        return RunResult(failure=stop, init=init, stats=RunStats(duration_ms=measured, exit_code=exit_code),
                         limit=limit)
    if result_event is None:
        if ahead and not output:   # started ahead, it ended before any output: the engine runs cold
            return RunResult(failure=failure(ERROR, PRESTART_DIED), init=init,
                             stats=RunStats(duration_ms=measured, exit_code=exit_code))
        if _UNKNOWN_OPTION_RE.search(tail):
            found = failure(CLI_UNSUPPORTED, "unknown_option")
        else:
            found = classify_text(tail, now=now) or failure(ERROR, f"exit {exit_code}" if exit_code is not None
                                                            else "no_result")
        return RunResult(failure=found, init=init, stats=RunStats(duration_ms=measured, exit_code=exit_code))
    stats = stats_of(result_event, measured_ms=measured, exit_code=exit_code)
    outcome = read(result_event, init)
    reply = raw_reply(result_event) if capture else ""
    if isinstance(outcome, AskFailure):
        return RunResult(failure=outcome, init=init, stats=stats, reply=reply)
    if isinstance(outcome, Plan):
        return RunResult(plan=outcome, init=init, stats=stats, reply=reply)
    return RunResult(init=init, stats=stats, reply=reply, answer=outcome)
