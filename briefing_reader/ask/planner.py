"""One Ask from request to cards (at most two planner runs), and the checks that need no model request.

    ClaudeEngine(ask_config, data_dir, runner, environ)
        readiness(refresh=False)   Readiness: claude.exe found, its flags probed, a claude.ai
                                   subscription sign-in, an empty work folder. No model request;
                                   cached until a run fails
        run(prompt, allow_search, cancel)   RunResult of one planner run (stream.run_planner)
    GoogleSources(config, calendars, senders, readers)   what Jarvis may tell the planner about each
                                   account (no sign-in ever; reads only)
    AskPlanner(config, engine, sources, usage)
        plan(command, briefing, page_ids, cancel, on_stage) -> AskOutcome
        dry_run(command, briefing) -> DryRun (the exact stdin text, email text as counts)
    briefing_context(...)          the briefing as Ask may see it (pending cards, sections, deadlines)

An Ask: caps and readiness first (refused without a run), then the context: the calendar of every
signed-in account, the briefing, and the text of briefing threads the request clearly names (on
accounts with "gmail_read"). The first run may ask to search mail instead of planning
({account, query, why}); Jarvis checks that search (ask.mail.check_query), runs it read-only on
that account, takes at most 3 threads, trims them and runs the planner once more with them as
quoted data (both runs count against the caps). Every line becomes a card through
validate.to_cards. Thread text lives only in this call's memory: it is never logged, saved or
returned.

The UI calls plan() on its Ask worker thread (it blocks: Google reads and up to two CLI runs);
``on_stage`` reports STAGE_* as it goes, and setting ``cancel`` ends a running CLI within a second.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from ..actions import ProposedAction
from ..config import Config
from ..gcal import CalendarError, CalendarNotSignedIn, EventBrief, local_timezone
from ..gmail import GmailError
from ..google_auth import PROBLEM_EXPIRED, PROBLEM_SCOPE, PROBLEM_SETUP, PROBLEM_SIGNED_OUT, logged_alias
from . import cli
from .context import (
    COMMAND_CAP,
    AccountInfo,
    AskContext,
    BriefingContext,
    ContextIndex,
    briefing_from_script,
    build_prompt,
    day_window,
)
from .mail import (
    MAX_THREADS,
    MailReport,
    MailThread,
    QueryRefused,
    SearchGrounding,
    briefing_threads,
    check_query,
    report,
    thread_from_api,
    trim_threads,
)
from .stream import CANCELLED, AskFailure, InitSummary, Plan, RunResult, RunStats, run_planner
from .usage import USAGE_FILE, CapCheck, UsageLog
from .validate import Cards, to_cards

logger = logging.getLogger(__name__)

STAGE_CONTEXT = "context"          # reading the calendar (and named briefing threads)
STAGE_PLANNING = "planning"        # the first planner run
STAGE_MAIL = "mail"                # running the search the planner asked for
STAGE_PLANNING_AGAIN = "planning_again"   # the second run, with the mail

# Refusals before any run (AskOutcome.kind), besides stream's failure kinds.
EMPTY = "empty"
DISABLED = "disabled"
CAPS = "caps"
CLI_MISSING = "cli_missing"
NOT_SUBSCRIPTION = "not_subscription"
WORKDIR = "workdir"
NOT_READY = "not_ready"

AUTH_FRESH_S = 5.0        # run() trusts an auth status this recent; older, it asks again
OVERAGE_PAUSE = timedelta(hours=5)   # the pause after an extra-usage signal that names no reset time

DISABLED_MESSAGE = "Ask Jarvis is off ([ask] enabled = false in config.toml)"
CLI_MISSING_MESSAGE = "Claude Code not found: set JARVIS_CLAUDE_EXE in .env or install it (README: Ask Jarvis)"


# --------------------------------------------------------------------------
# The engine: one claude.exe, checked
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Readiness:
    ok: bool
    # "" | cli_missing | cli_unsupported | not_signed_in | not_subscription | workdir | error
    problem: str = ""
    message: str = ""                 # safe to show and log (no path, no address)
    exe: cli.ClaudeExe | None = None
    version: str = ""
    hardening: bool = False           # --safe-mode and --restricted are both available
    hardened: bool = False            # ... and [ask] hardened_flags uses them
    auth: cli.AuthStatus | None = None


class ClaudeEngine:
    """The owner's Claude Code CLI for Ask: readiness (no model request) and planner runs."""

    def __init__(self, ask: Any, data_dir: Path, *, runner: cli.Runner | None = None,
                 environ: Mapping[str, str] | None = None,
                 locate: Callable[[Mapping[str, str]], cli.ClaudeExe | None] = cli.locate_claude,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ask = ask
        self.work_folder = Path(data_dir) / cli.WORK_FOLDER
        self.runner = runner or cli.SubprocessRunner()
        self._environ = environ
        self._locate = locate
        self._clock = clock
        self._lock = threading.Lock()
        self._ready: Readiness | None = None
        self._auth_at: float | None = None      # self._clock() of the last passing auth status

    def _env_source(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    def readiness(self, *, refresh: bool = False) -> Readiness:
        """Is Ask's CLI usable now? Cached for the app session once it is; a failed check (or a
        failed run) is checked again next time. Runs only --version, --help and auth status. The
        cache is for the chip and the refusals: run() asks auth status again before every run."""
        with self._lock:
            if self._ready is not None and not refresh:
                return self._ready
        ready = self._check()
        with self._lock:
            self._ready = ready if ready.ok else None
            self._auth_at = self._clock() if ready.ok else None
        logger.info("Ask: readiness %s (Claude Code %s from %s, sign-in %s, hardening %s)",
                    "ok" if ready.ok else ready.problem, ready.version or "?",
                    ready.exe.source if ready.exe else "-",
                    ready.auth.method if ready.auth and ready.auth.method else "-",
                    "on" if ready.hardened else "off")
        return ready

    def _check(self) -> Readiness:
        environ = self._env_source()
        exe = self._locate(environ)
        if exe is None:
            return Readiness(False, CLI_MISSING, CLI_MISSING_MESSAGE)
        env = cli.clean_env(environ)
        problem = cli.prepare_work_folder(self.work_folder)
        if problem:
            return Readiness(False, WORKDIR, problem, exe)
        try:
            probe = cli.probe(exe.path, self.runner, env, self.work_folder)
        except OSError as exc:
            return Readiness(False, "error", f"Claude Code could not be started ({type(exc).__name__})", exe)
        if not probe.ok:
            return Readiness(False, "cli_unsupported", probe.message, exe, probe.version, probe.hardening)
        try:
            auth = cli.auth_status(exe.path, self.runner, env, self.work_folder)
        except OSError as exc:
            return Readiness(False, "error", f"Claude Code could not be started ({type(exc).__name__})", exe,
                             probe.version, probe.hardening)
        hardened = probe.hardening and bool(getattr(self.ask, "hardened_flags", True))
        if not auth.ok:
            return Readiness(False, auth.problem or "error", auth.message, exe, probe.version, probe.hardening,
                             hardened, auth)
        return Readiness(True, "", "", exe, probe.version, probe.hardening, hardened, auth)

    def argv(self, ready: Readiness, *, allow_search: bool) -> list[str]:
        assert ready.exe is not None
        return cli.build_argv(ready.exe.path, model=self.ask.model, max_turns=self.ask.max_turns,
                              schema=cli.schema_text(allow_search=allow_search), hardened=ready.hardened)

    def preflight(self) -> tuple[Readiness, AskFailure | None]:
        """Right before a planner run: readiness (the CLI and its flags cached), an empty work
        folder, and the sign-in again: it must still be a claude.ai plan sign-in (`claude auth
        status`, no model request), whatever it was when Jarvis started. (readiness, why not)."""
        from .stream import NOT_SIGNED_IN, failure

        ready = self.readiness()
        if not ready.ok:
            return ready, failure(ready.problem if ready.problem in ("not_signed_in", "cli_unsupported") else "error",
                                  ready.problem, ready.message)
        problem = cli.prepare_work_folder(self.work_folder)
        if problem:
            self.forget()
            return ready, failure("error", WORKDIR, problem)
        auth = self._fresh_auth(ready, cli.clean_env(self._env_source()))
        if not auth.ok:
            self.forget()
            logger.info("Ask: the Claude Code sign-in is no longer a claude.ai plan sign-in (%s); no planner run",
                        auth.problem or "error")
            kind = NOT_SIGNED_IN if auth.problem in ("not_signed_in", NOT_SUBSCRIPTION) else "error"
            return ready, failure(kind, auth.problem or "auth", auth.message)
        return ready, None

    def run(self, prompt: str, *, allow_search: bool, cancel: threading.Event | None = None) -> RunResult:
        """One planner run with ``prompt`` on stdin, after preflight()."""
        from .stream import failure

        ready, refused = self.preflight()
        if refused is not None:
            return RunResult(failure=refused)
        if cancel is not None and cancel.is_set():
            return RunResult(failure=failure(CANCELLED, "cancel"))
        env = cli.clean_env(self._env_source())
        result = run_planner(self.runner, self.argv(ready, allow_search=allow_search), prompt,
                             env=env, cwd=self.work_folder,
                             timeout_s=float(self.ask.timeout_seconds), allow_search=allow_search, cancel=cancel,
                             clock=self._clock)
        init = result.init
        logger.info("Ask: planner run %s in %.1f s (%d turn(s), %d in / %d out / %d cached tokens; tools %s, "
                    "structured output %s, exit %s)", result.outcome, result.stats.duration_ms / 1000,
                    result.stats.turns, result.stats.input_tokens, result.stats.output_tokens,
                    result.stats.cache_read_tokens, ",".join(init.tools) if init else "-",
                    "yes" if init and init.structured_tool else "no", result.stats.exit_code)
        if result.failure is not None and result.failure.kind != CANCELLED:
            self.forget()
        return result

    def _fresh_auth(self, ready: Readiness, env: Mapping[str, str]) -> cli.AuthStatus:
        """`claude auth status` now, unless readiness ran it moments ago (AUTH_FRESH_S)."""
        with self._lock:
            checked_at = self._auth_at
        if ready.auth is not None and checked_at is not None and self._clock() - checked_at < AUTH_FRESH_S:
            return ready.auth
        assert ready.exe is not None
        try:
            auth = cli.auth_status(ready.exe.path, self.runner, env, self.work_folder)
        except OSError as exc:
            return cli.AuthStatus(False, problem="error",
                                  message=f"Claude Code could not be started ({type(exc).__name__})")
        if auth.ok:
            with self._lock:
                self._auth_at = self._clock()
        return auth

    def forget(self) -> None:
        """Check readiness again before the next run."""
        with self._lock:
            self._ready = None
            self._auth_at = None


CHIP_OK = "OK"
CHIP_SIGN_IN = "SIGN IN"
CHIP_LIMIT = "LIMIT"
CHIP_OFF = "OFF"
CHIP_ERR = "ERR"


def chip_state(enabled: bool, ready: Readiness | None, caps: CapCheck | None = None) -> tuple[str, str]:
    """(the header's `claude` chip state, its tooltip): OFF while [ask] is off, SIGN IN without a
    claude.ai plan sign-in, ERR when Claude Code is missing, unsupported or its folder is not empty,
    LIMIT when the caps refuse the next run, else OK (with the runs left this hour)."""
    if not enabled:
        return CHIP_OFF, DISABLED_MESSAGE
    if ready is None:
        return CHIP_ERR, "Ask has not checked Claude Code yet"
    if not ready.ok:
        if ready.problem in ("not_signed_in", NOT_SUBSCRIPTION):
            return CHIP_SIGN_IN, ready.message
        return CHIP_ERR, ready.message
    if caps is not None and not caps.allowed:
        return CHIP_LIMIT, caps.message
    left = f"; {caps.left_hour} planner run(s) left this hour" if caps is not None else ""
    return CHIP_OK, f"Ask uses your Claude plan (Claude Code {ready.version or '?'}){left}"


# --------------------------------------------------------------------------
# What Jarvis may tell the planner about each account
# --------------------------------------------------------------------------

class Sources(Protocol):
    def accounts(self) -> list[AccountInfo]: ...

    def time_zone(self) -> str: ...

    def event_briefs(self, alias: str, start: datetime, end: datetime,
                     calendars: Sequence[str]) -> tuple[list[EventBrief], str]: ...

    def reader(self, alias: str) -> Any | None: ...


READ_NOT_ALLOWED = "no (not allowed in the Google sign-in)"
READ_NEEDS_SIGN_IN = "no (needs a Google sign-in)"
READ_OFF = "no (turned off: [ask] read_mail)"
READ_NOT_SET_UP = "no (not set up)"
READ_LIMIT = "no (the Ask limit leaves one run)"
_READ_WORDS = {PROBLEM_SCOPE: READ_NOT_ALLOWED, PROBLEM_SIGNED_OUT: READ_NEEDS_SIGN_IN,
               PROBLEM_EXPIRED: READ_NEEDS_SIGN_IN, PROBLEM_SETUP: READ_NOT_SET_UP}


class GoogleSources:
    """The app's own Google objects (executor.build_calendars / build_senders / build_readers),
    used read-only and never to sign in."""

    def __init__(self, config: Config, calendars: Mapping[str, Any], senders: Mapping[str, Any] | None = None,
                 readers: Mapping[str, Any] | None = None) -> None:
        self.config = config
        self.calendars = dict(calendars)
        self.senders = dict(senders or {})
        self.readers = dict(readers or {})

    def aliases(self) -> list[str]:
        names = list(self.config.accounts)
        for group in (self.calendars, self.senders, self.readers):
            names += [alias for alias in group if alias not in names]
        return names

    def accounts(self) -> list[AccountInfo]:
        found = []
        for alias in self.aliases():
            calendar = self.calendars.get(alias)
            sender = self.senders.get(alias)
            reader = self.readers.get(alias)
            address = ""
            for handle in (sender, calendar):
                account = getattr(handle, "account", None)
                getter = getattr(account, "bound_email", None)
                if callable(getter):
                    address = getter() or address
                if address:
                    break
            if calendar is None:
                status = "no"
            else:
                status = "yes" if _call_bool(calendar, "is_signed_in") else "not signed in"
            read = READ_NOT_SET_UP
            if not self.config.ask.read_mail:
                read = READ_OFF
            elif reader is not None:
                problem, _ = reader.available()
                read = "yes" if not problem else _READ_WORDS.get(problem, "no (not available now)")
            zone = ""
            if calendar is not None and status == "yes":
                try:
                    zone = calendar.timezone(interactive=False)
                except Exception:  # noqa: BLE001 - only what the planner is told
                    zone = ""
            found.append(AccountInfo(alias, address, status, sender is not None, read, zone))
        return found

    def time_zone(self) -> str:
        calendar = self.calendars.get("personal")
        if calendar is not None and _call_bool(calendar, "is_signed_in"):
            try:
                return calendar.timezone(interactive=False)
            except Exception:  # noqa: BLE001
                pass
        return local_timezone()

    def event_briefs(self, alias: str, start: datetime, end: datetime,
                     calendars: Sequence[str]) -> tuple[list[EventBrief], str]:
        calendar = self.calendars.get(alias)
        if calendar is None:
            return [], "no"
        try:
            return list(calendar.list_event_briefs(start, end, calendars)), "yes"
        except CalendarNotSignedIn:
            return [], "not signed in"
        except CalendarError:
            return [], "error"
        except Exception as exc:  # noqa: BLE001 - one account's calendar must not stop the Ask
            logger.warning("Ask: reading the %s calendar failed unexpectedly (%s)", logged_alias(alias),
                           type(exc).__name__)
            return [], "error"

    def reader(self, alias: str) -> Any | None:
        return self.readers.get(alias)


def unreadable_note(account: str, known: AccountInfo | None) -> str:
    """Why Jarvis did not read ``account``'s mail, in plain words with what to do first."""
    if known is None:
        return f"Jarvis didn't search mail: there is no {account or 'such'} account"
    why = known.read_mail
    if why == READ_NOT_ALLOWED or (why == READ_NEEDS_SIGN_IN and known.calendar == "yes"):
        return f'Jarvis couldn\'t read your {account} mail: click "Allow {account} mail" to let it'
    if why == READ_NEEDS_SIGN_IN:
        return f"Jarvis couldn't read your {account} mail: sign in to the {account} account first"
    if why == READ_OFF:
        return f"Jarvis didn't read your {account} mail: reading mail is off ([ask] read_mail)"
    if why == READ_NOT_SET_UP:
        return f'Jarvis can\'t read {account} mail: add "gmail_read" to that account in config.toml'
    if why == READ_LIMIT:
        return "Jarvis didn't search your mail: this hour's Ask limit leaves only one planner run"
    return f"Jarvis couldn't read your {account} mail right now"


def needs_mail_sign_in(account: str, ctx: AskContext) -> str:
    """``account`` when one more Google sign-in would let Jarvis read its mail ("" otherwise)."""
    known = next((item for item in ctx.accounts if item.alias == account), None)
    if known is not None and (known.read_mail == READ_NOT_ALLOWED
                              or (known.read_mail == READ_NEEDS_SIGN_IN and known.calendar == "yes")):
        return account
    return ""


def _call_bool(handle: Any, name: str) -> bool:
    getter = getattr(handle, name, None)
    try:
        return bool(getter()) if callable(getter) else False
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# One Ask
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AskOutcome:
    ok: bool
    kind: str                                    # "ok", a refusal (EMPTY, DISABLED, CAPS...) or a failure kind
    message: str = ""                            # Jarvis's own words for the status line
    say: str = ""                                # the planner's (shown, spoken from P3; never logged)
    question: str = ""
    cards: tuple[ProposedAction, ...] = ()
    dropped: int = 0                             # lines beyond [ask] max_cards
    refused: int = 0                             # cards that are information only
    runs: int = 0                                # planner runs this Ask used (they count against the caps)
    duration_ms: int = 0
    mail: MailReport = field(default_factory=MailReport)
    search: str = ""                             # the Gmail search Jarvis ran (shown; never logged)
    search_account: str = ""
    mail_needed: str = ""                        # the account whose mail the planner wanted but one more
                                                 # Google sign-in must allow ("Allow <alias> mail")
    init: InitSummary | None = None
    stats: tuple[RunStats, ...] = ()
    caps: CapCheck | None = None                 # what is left after this Ask

    def summary(self) -> str:
        """For the log: kinds and counts only."""
        kinds: dict[str, int] = {}
        for card in self.cards:
            name = "refused" if card.error else card.kind
            kinds[name] = kinds.get(name, 0) + 1
        listed = ", ".join(f"{kind} {count}" for kind, count in kinds.items()) or "none"
        return (f"{self.kind}: {len(self.cards)} card(s) ({listed}), "
                f"{self.dropped} dropped, {self.runs} run(s), {self.duration_ms / 1000:.1f} s, "
                f"mail {self.mail.threads} thread(s) / {self.mail.messages} message(s)")


@dataclass(frozen=True)
class DryRun:
    prompt: str                       # exactly what would go to stdin, email text as counts
    argv: tuple[str, ...]             # the command line ("" when Claude Code was not found)
    readiness: Readiness
    counts: Mapping[str, int]
    mail: MailReport


class AskPlanner:
    def __init__(self, config: Config, engine: ClaudeEngine, sources: Sources, *, usage: UsageLog | None = None,
                 clock: Callable[[], datetime] | None = None, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.engine = engine
        self.sources = sources
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.usage = usage or UsageLog(config.data_dir / USAGE_FILE, max_per_hour=config.ask.max_per_hour,
                                       max_per_day=config.ask.max_per_day, clock=self.clock)
        self._monotonic = monotonic
        self._busy = threading.Lock()

    # ---- context ------------------------------------------------------------------------------

    def _context(self, command: str, briefing: BriefingContext | None, now: datetime,
                 on_stage: Callable[[str], None],
                 cancel: threading.Event | None = None) -> tuple[AskContext, list[str]]:
        """The context (Google reads only). Cancel is checked between reads: the rest is skipped and
        the caller sees the event set."""
        on_stage(STAGE_CONTEXT)
        ask = self.config.ask
        accounts = self.sources.accounts()
        start, end = day_window(now, ask.days_back, ask.days_ahead)
        events: dict[str, tuple[EventBrief, ...]] = {}
        updated = []
        for account in accounts:
            if account.calendar != "yes" or (cancel is not None and cancel.is_set()):
                updated.append(account)
                continue
            briefs, status = self.sources.event_briefs(account.alias, start, end, ask.calendars)
            if status == "yes":
                events[account.alias] = tuple(briefs)
            updated.append(replace(account, calendar=status))
        notes: list[str] = []
        threads: list[MailThread] = []
        readable = {account.alias for account in updated if account.read_mail == "yes"}
        if briefing is not None and readable:
            for ref in briefing_threads(command, briefing.pending, readable):
                if cancel is not None and cancel.is_set():
                    break
                thread = self._read_thread(ref.account, ref.thread_id, notes)
                if thread is not None:
                    threads.append(thread)
        ctx = AskContext(now=now, time_zone=self.sources.time_zone(), command=command, accounts=tuple(updated),
                         events=events, briefing=briefing, mail=tuple(trim_threads(threads)), owner=self._owner())
        return ctx, notes

    def _owner(self) -> str:
        """[assistant] address: the word Jarvis's answers address the owner with ("" for none)."""
        assistant = getattr(self.config, "assistant", None)
        return str(getattr(assistant, "address", "") or "")

    def _read_thread(self, alias: str, thread_id: str, notes: list[str]) -> MailThread | None:
        reader = self.sources.reader(alias)
        if reader is None:
            return None
        try:
            thread = thread_from_api(reader.thread(thread_id), alias)
        except GmailError as exc:
            note = f"Jarvis couldn't read a {alias} thread ({exc})"
            if note not in notes:
                notes.append(note)
            return None
        except Exception as exc:  # noqa: BLE001 - reading is optional; the Ask goes on without it
            logger.warning("Ask: reading a %s thread failed unexpectedly (%s)", logged_alias(alias), type(exc).__name__)
            return None
        return thread

    # ---- runs ---------------------------------------------------------------------------------

    def _run(self, prompt: str, *, allow_search: bool, cancel: threading.Event | None) -> RunResult:
        _ready, refused = self.engine.preflight()   # a run refused before it starts is not counted
        if refused is not None:
            return RunResult(failure=refused, started=False)
        run = self.usage.start()
        result: RunResult | None = None
        try:
            result = self.engine.run(prompt, allow_search=allow_search, cancel=cancel)
            if result.limit is not None and result.limit.overage:
                self._pause_for_overage(result.limit.resets_at)
            return result
        finally:
            stats = result.stats if result is not None else RunStats()
            self.usage.finish(run, outcome=result.outcome if result is not None else "error",
                              duration_ms=stats.duration_ms, turns=stats.turns, input_tokens=stats.input_tokens,
                              output_tokens=stats.output_tokens, cache_read_tokens=stats.cache_read_tokens,
                              cache_write_tokens=stats.cache_write_tokens)

    def _pause_for_overage(self, resets_at: datetime | None) -> None:
        """Claude Code said a run would use extra usage: no run (in any Jarvis process) until the
        plan's limit resets (OVERAGE_PAUSE when it did not say when)."""
        now = self.clock()
        until = resets_at if resets_at is not None and resets_at > now else now + OVERAGE_PAUSE
        until = self.usage.hold(until)
        logger.warning("Ask: Claude Code reported extra usage (usage credits); Ask is paused for %d minute(s)",
                       max(1, round((until - now).total_seconds() / 60)))

    # ---- plan ---------------------------------------------------------------------------------

    def plan(self, command: str, *, briefing: BriefingContext | None = None, page_ids: Sequence[str] = (),
             cancel: threading.Event | None = None,
             on_stage: Callable[[str], None] = lambda stage: None) -> AskOutcome:
        """One Ask (see the module docs). Never raises for Google or the CLI; one at a time."""
        if not self._busy.acquire(blocking=False):
            return AskOutcome(False, "busy", "Jarvis is already planning; wait or Cancel it")
        started = self._monotonic()
        try:
            outcome = self._plan(" ".join((command or "").split())[:COMMAND_CAP], briefing, page_ids, cancel,
                                 on_stage)
        except Exception as exc:  # noqa: BLE001 - an Ask must never take the app down
            logger.warning("Ask: stopped by an unexpected error (%s)", type(exc).__name__)
            outcome = AskOutcome(False, "error", "Ask stopped because of an unexpected error; nothing was proposed")
        finally:
            self._busy.release()
        outcome = replace(outcome, duration_ms=int((self._monotonic() - started) * 1000), caps=self.usage.check())
        logger.info("Ask: %s", outcome.summary())
        return outcome

    def _refusal(self) -> AskOutcome | None:
        if not self.config.ask.enabled:
            return AskOutcome(False, DISABLED, DISABLED_MESSAGE)
        caps = self.usage.check()
        if not caps.allowed:
            return AskOutcome(False, CAPS, caps.message, caps=caps)
        ready = self.engine.readiness()
        if not ready.ok:
            return AskOutcome(False, ready.problem or NOT_READY, ready.message)
        return None

    def _plan(self, command: str, briefing: BriefingContext | None, page_ids: Sequence[str],
              cancel: threading.Event | None, on_stage: Callable[[str], None]) -> AskOutcome:
        if not command:
            return AskOutcome(False, EMPTY, "Type what you'd like Jarvis to do")
        refusal = self._refusal()
        if refusal is not None:
            return refusal
        now = self.clock()
        ctx, notes = self._context(command, briefing, now, on_stage, cancel)
        if cancel is not None and cancel.is_set():
            return AskOutcome(False, CANCELLED, "Cancelled; nothing was proposed")
        searchable = {account.alias for account in ctx.accounts if account.read_mail == "yes"}
        allow_search = bool(searchable) and self.usage.check(runs=2).allowed
        if searchable and not allow_search:
            ctx = replace(ctx, accounts=tuple(replace(account, read_mail=READ_LIMIT)
                                              if account.alias in searchable else account for account in ctx.accounts))
        prompt, index = build_prompt(ctx)
        logger.info("Ask: context of %d characters (%s; mail %d thread(s))", len(prompt),
                    ", ".join(f"{name} {count}" for name, count in index.counts().items()), len(ctx.mail))
        on_stage(STAGE_PLANNING)
        first = self._run(prompt, allow_search=allow_search, cancel=cancel)
        del prompt
        if first.failure is not None or first.plan is None:
            failure = first.failure
            return AskOutcome(False, failure.kind if failure else "error", failure.message if failure else "",
                              runs=1 if first.started else 0, init=first.init,
                              stats=(first.stats,) if first.started else (), mail=report(ctx.mail))
        plan, final_index, runs, stats = first.plan, index, 1, [first.stats]
        mail_threads = list(ctx.mail)
        search_text = search_account = mail_needed = ""
        if plan.search is not None and allow_search:
            search = plan.search
            mail_note, found, ran = self._search(search.account, search.query, ctx, searchable, on_stage, cancel,
                                                 index.search_grounding())
            if cancel is not None and cancel.is_set():
                return AskOutcome(False, CANCELLED, "Cancelled; nothing was proposed", runs=1, init=first.init,
                                  stats=tuple(stats), mail=report(ctx.mail))
            if ran:
                search_text, search_account = ran, search.account
            if mail_note:
                notes.append(mail_note)
                mail_needed = needs_mail_sign_in(search.account, ctx)
            elif found:
                caps = self.usage.check()
                if not caps.allowed:
                    notes.append(caps.message)
                else:
                    mail_threads = trim_threads(list(ctx.mail) + found)
                    second_ctx = replace(ctx, mail=tuple(mail_threads), accounts=tuple(
                        replace(account, read_mail="no (mail already read for this request)")
                        if account.read_mail == "yes" else account for account in ctx.accounts))
                    second_prompt, final_index = build_prompt(second_ctx)
                    on_stage(STAGE_PLANNING_AGAIN)
                    second = self._run(second_prompt, allow_search=False, cancel=cancel)
                    del second_prompt
                    if second.started:
                        runs, stats = 2, stats + [second.stats]
                    if second.failure is not None or second.plan is None:
                        failure = second.failure
                        return AskOutcome(False, failure.kind if failure else "error",
                                          failure.message if failure else "", runs=runs,
                                          init=second.init or first.init,
                                          stats=tuple(stats), mail=report(mail_threads), search=search_text,
                                          search_account=search_account)
                    plan = second.plan
        outcome = self._finish(plan, final_index, now=now, page_ids=page_ids, notes=notes, runs=runs,
                               stats=tuple(stats), init=first.init, mail=report(mail_threads), search=search_text,
                               search_account=search_account)
        return replace(outcome, mail_needed=mail_needed)

    def _search(self, account: str, query: str, ctx: AskContext, searchable: set[str],
                on_stage: Callable[[str], None], cancel: threading.Event | None,
                grounding: SearchGrounding) -> tuple[str, list[MailThread], str]:
        """(a note when nothing could be read, the new threads, the search as it ran ("" when it
        did not run)). The search must name only what the owner typed (``grounding``)."""
        if account not in searchable:
            known = next((item for item in ctx.accounts if item.alias == account), None)
            return unreadable_note(account, known), [], ""
        try:
            query = check_query(query, grounding)
        except QueryRefused as exc:
            logger.info("Ask: refused a mail search the planner asked for")
            reason = str(exc)
            return f"Jarvis didn't search your mail: {reason[:1].lower()}{reason[1:]}", [], ""
        reader = self.sources.reader(account)
        if reader is None:
            return f"Jarvis can't read the {account} account's mail", [], ""
        if cancel is not None and cancel.is_set():
            return "", [], ""
        on_stage(STAGE_MAIL)
        seen = {(thread.account, thread.thread_id) for thread in ctx.mail}
        try:
            ids = reader.search(query, max_threads=MAX_THREADS)
        except GmailError as exc:
            return f"Jarvis couldn't search your mail ({exc})", [], query
        notes: list[str] = []
        found: list[MailThread] = []
        for thread_id in ids:
            if cancel is not None and cancel.is_set():
                return "", [], query
            if len(found) + len(ctx.mail) >= MAX_THREADS or (account, thread_id) in seen:
                continue
            thread = self._read_thread(account, thread_id, notes)
            if thread is not None:
                found.append(thread)
        if not found:
            return notes[0] if notes else "Jarvis found no email matching that search; nothing was read", [], query
        return "", found, query

    def _finish(self, plan: Plan, index: ContextIndex, *, now: datetime, page_ids: Sequence[str], notes: list[str],
                runs: int, stats: tuple[RunStats, ...], init: InitSummary | None, mail: MailReport, search: str,
                search_account: str) -> AskOutcome:
        cards: Cards = to_cards(plan.lines, index, now=now, link_hosts=self.config.actions.link_hosts,
                                page_ids=page_ids, max_cards=self.config.ask.max_cards)
        message_parts = list(notes)
        if cards.dropped:
            message_parts.append(f"{cards.dropped} more proposal(s) were left out")
        if plan.via_fallback:
            message_parts.append("(read from the planner's plain text)")
        return AskOutcome(True, "ok", "; ".join(message_parts), say=plan.say, question=plan.question,
                          cards=cards.cards, dropped=cards.dropped, refused=cards.refused, runs=runs, init=init,
                          stats=stats, mail=mail, search=search, search_account=search_account)

    # ---- dry run ------------------------------------------------------------------------------

    def dry_run(self, command: str, *, briefing: BriefingContext | None = None) -> DryRun:
        """What plan() would send first, without any planner run (and without counting against the
        caps): the stdin text with each email's text replaced by its length, and the argv."""
        command = " ".join((command or "").split())[:COMMAND_CAP]
        now = self.clock()
        ctx, _ = self._context(command, briefing, now, lambda stage: None)
        prompt, index = build_prompt(ctx, redact_mail=True)
        ready = self.engine.readiness()
        argv: tuple[str, ...] = ()
        if ready.exe is not None:
            searchable = any(account.read_mail == "yes" for account in ctx.accounts)
            argv = tuple(cli.build_argv(ready.exe.path, model=self.config.ask.model,
                                        max_turns=self.config.ask.max_turns,
                                        schema=cli.schema_text(allow_search=searchable),
                                        hardened=ready.hardened))
        return DryRun(prompt, argv, ready, index.counts(), report(ctx.mail))


# --------------------------------------------------------------------------
# The briefing as Ask sees it
# --------------------------------------------------------------------------

def deadline_lines(deadlines: Sequence[Any]) -> list[str]:
    """agenda.Deadline items as "title | YYYY-MM-DD[ HH:MM]" lines."""
    lines = []
    for item in deadlines:
        due = getattr(item, "due", None)
        if isinstance(due, datetime):
            when = due.strftime("%Y-%m-%d %H:%M")
        elif isinstance(due, date):
            when = due.isoformat()
        else:
            continue
        lines.append(f"{getattr(item, 'title', '')} | {when}")
    return lines


def briefing_context(briefing: Any, pending: Sequence[ProposedAction], deadlines: Sequence[Any] = (), *,
                     config: Config, now: datetime) -> BriefingContext:
    """BriefingContext from what the app holds: ``briefing`` the page (models.Briefing) without its
    Proposed actions and Deadlines sections (AppController._briefing), ``pending`` the briefing's
    cards still waiting for a decision, ``deadlines`` agenda.Deadline items. The Ignore sections are
    left out."""
    from ..text_prep import build_script

    sections: Sequence[Any] = ()
    if briefing is not None:
        script = build_script(briefing, now=now, include_note=False, ignore_names=config.sections.ignore,
                              announce=config.sections.announce, actions=())
        sections = script.sections
    return briefing_from_script(sections, pending, deadline_lines(deadlines))


def briefing_from_page(page: Any, *, config: Config, now: datetime, decided: Callable[[str], bool] = lambda _id: False
                       ) -> BriefingContext:
    """BriefingContext from a freshly fetched page (the command line): its Deadlines and Proposed
    actions sections are taken out as the app does; ``decided`` tells which card ids are decided."""
    from ..actions import extract_actions
    from ..agenda import extract_deadlines

    headings = config.actions.headings
    deadlines, lines = extract_deadlines(page.lines, stop_names=headings)
    actions, lines = extract_actions(lines, headings, link_hosts=config.actions.link_hosts)
    pending = [action for action in actions if action.decidable and not decided(action.id)]
    return briefing_context(replace(page, lines=tuple(lines)), pending, deadlines, config=config, now=now)
