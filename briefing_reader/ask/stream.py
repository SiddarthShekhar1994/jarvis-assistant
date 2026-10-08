"""One planner run: Claude Code's stream-json output, checked event by event.

    check_init(event)        InitSummary, or GuardTrip: the first event must be system/init with
                             apiKeySource "none", no MCP server and no tool but StructuredOutput
    rate_limit_signal(event) a rate_limit_event that says the run is off the plan: extra usage
                             (usage credits: isUsingOverage, overageInUse, rateLimitType
                             "overage", or the plan "rejected" with overage allowed) or the plan's
                             own limit ("rejected")
    read_result(event, init) the result event -> Plan, or AskFailure(kind)
    run_planner(...)         one run end to end: no init within 20 s, any guard trip, a rate-limit
                             signal, the overall timeout and Cancel all kill the process; a run is
                             never retried

Failure kinds (AskFailure.kind): not_signed_in, limit, max_turns, schema_retries, timeout,
cancelled, guard, garbled, cli_unsupported, error. Their messages are Jarvis's own words (the only
model or CLI text in one is a sanitized usage-limit reset time) and are safe to show and log; the
stream itself (the plan, the result text, stderr) is never logged. ``total_cost_usd`` is a
client-side estimate, not a charge on a subscription: it is never read.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .cli import OutputTooLong, Runner

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
FAILURE_KINDS = (NOT_SIGNED_IN, LIMIT, MAX_TURNS, SCHEMA_RETRIES, TIMEOUT, CANCELLED, GUARD, GARBLED,
                 CLI_UNSUPPORTED, ERROR)

STRUCTURED_TOOL = "StructuredOutput"
INIT_TIMEOUT_S = 20.0
POLL_S = 0.25                 # Cancel and the timeouts are checked at least this often
SAY_CAP = 300
QUESTION_CAP = 200
LINES_CAP = 8
LINE_CAP = 12000
QUERY_CAP = 200
WHY_CAP = 200
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
}
OVERAGE = "overage"           # AskFailure.detail: Claude Code said this run uses extra usage
REJECTED = "rejected"         # AskFailure.detail: Claude Code said the plan's limit is reached
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


@dataclass(frozen=True)
class MailSearch:
    """The first run's request to read mail (planner text: shown to the owner, never logged)."""

    account: str
    query: str
    why: str


@dataclass(frozen=True)
class Plan:
    say: str
    question: str
    lines: tuple[str, ...]
    search: MailSearch | None = None
    via_fallback: bool = False   # read from the result text (StructuredOutput was not available)


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

    @property
    def outcome(self) -> str:
        """"ok" or the failure kind (what usage.json and the log record)."""
        return "ok" if self.failure is None else self.failure.kind


class GuardTrip(Exception):
    """The run is not what Ask allows; ``reason`` is a fixed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
                       version=_word(event.get("claude_code_version")), structured_tool=STRUCTURED_TOOL in tools)


def check_event(event: Mapping[str, Any]) -> None:
    """Any later event: a tool call other than StructuredOutput trips the guard."""
    if event.get("type") != "assistant":
        return
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    for block in content if isinstance(content, list) else ():
        if isinstance(block, dict) and block.get("type") in ("tool_use", "server_tool_use", "mcp_tool_use") \
                and block.get("name") != STRUCTURED_TOOL:
            raise GuardTrip("tool_use")


def _epoch_time(value: Any, now: Callable[[], datetime]) -> datetime | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(value)).astimezone(now().tzinfo)
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


def _plan_from(data: Any, *, allow_search: bool, fallback: bool = False) -> Plan | None:
    """structured_output (or the fallback's JSON) shape-checked locally; None when it does not fit."""
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
    kept = tuple(line.strip() for line in lines if line.strip())
    return Plan(say, question, kept, search, fallback)


def _fallback_plan(text: str, *, allow_search: bool) -> Plan | None:
    """Only when init showed no StructuredOutput: the result text as JSON (code fences dropped),
    else its lines that start with a known kind."""
    text = text or ""
    fenced = _FENCE_RE.match(text)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        data = None
    plan = _plan_from(data, allow_search=allow_search, fallback=True) if data is not None else None
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


def read_result(event: Mapping[str, Any], init: InitSummary | None, *, allow_search: bool,
                now: Callable[[], datetime] = lambda: datetime.now().astimezone()) -> Plan | AskFailure:
    """The result event -> Plan or AskFailure. A plan needs a checked init first."""
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
    data = event.get("structured_output")
    if data is not None:
        plan = _plan_from(data, allow_search=allow_search)
        return plan if plan is not None else failure(GARBLED, "shape")
    if init.structured_tool:
        return failure(GARBLED, "no_structured_output")
    plan = _fallback_plan(text, allow_search=allow_search)
    return plan if plan is not None else failure(GARBLED, "fallback")


# --------------------------------------------------------------------------
# The engine loop
# --------------------------------------------------------------------------

def run_planner(runner: Runner, argv: Sequence[str], prompt: str, *, env: Mapping[str, str], cwd: Path,
                timeout_s: float, allow_search: bool, cancel: threading.Event | None = None,
                clock: Callable[[], float] = time.monotonic, init_timeout_s: float = INIT_TIMEOUT_S,
                poll_s: float = POLL_S,
                now: Callable[[], datetime] = lambda: datetime.now().astimezone()) -> RunResult:
    """Start the CLI with ``prompt`` on stdin and read its events until the result, a guard trip,
    the timeout or Cancel (the process is killed then). Never raises for the CLI's behaviour."""
    started = clock()
    try:
        process = runner.start(argv, stdin_text=prompt, env=env, cwd=cwd)
    except OSError as exc:
        logger.warning("Ask: Claude Code could not be started (%s)", type(exc).__name__)
        return RunResult(failure=failure(ERROR, "start"))
    init: InitSummary | None = None
    result_event: Mapping[str, Any] | None = None
    stop: AskFailure | None = None
    limit: LimitSignal | None = None
    garbage = 0
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
                stop = failure(GUARD, "no_init")
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
                        break
                    init = check_init(event)
                    continue
                check_event(event)
            except GuardTrip as trip:
                stop = failure(GUARD, trip.reason)
                break
            if event.get("type") == "result":
                result_event = event
                break
    finally:
        if stop is not None:
            process.kill()
        exit_code = process.wait(5.0 if stop is None else 2.0)
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
        if _UNKNOWN_OPTION_RE.search(tail):
            found = failure(CLI_UNSUPPORTED, "unknown_option")
        else:
            found = classify_text(tail, now=now) or failure(ERROR, f"exit {exit_code}" if exit_code is not None
                                                            else "no_result")
        return RunResult(failure=found, init=init, stats=RunStats(duration_ms=measured, exit_code=exit_code))
    stats = stats_of(result_event, measured_ms=measured, exit_code=exit_code)
    outcome = read_result(result_event, init, allow_search=allow_search, now=now)
    if isinstance(outcome, AskFailure):
        return RunResult(failure=outcome, init=init, stats=stats)
    return RunResult(plan=outcome, init=init, stats=stats)
