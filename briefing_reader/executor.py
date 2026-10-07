"""Carrying out approved proposals: which service acts for which account, and how.

    Executor(backends, accounts)   picks the backend for a proposal: its kind and the
                                   ``[accounts.<alias>]`` table of its account in config.toml
    CalendarBackend(calendars)     Google Calendar, one gcal.GoogleCalendar per account alias:
                                   a Calendar event or a Todo's block (always the "personal"
                                   calendar, as before), and an RSVP, Move or Cancel (the line's
                                   ``acct=``)
    run_action(executor, action)   one job of the action worker: for an RSVP, Move or Cancel,
                                   "running" is saved before the call and the result after it
    build_accounts(config)         one google_auth.GoogleAccount per alias (the single sign-in
                                   of older versions becomes "personal")
    build_calendars(config)        one gcal.GoogleCalendar per alias that has the calendar feature
    apply_edit(action, edit)       the Edit dialog's changes (memory only; same id)
    check_event(action, details)   the card's check line: Google's own title, time and organizer,
                                   and whether Jarvis may act on that event
    sign_in_note(alias, ...)       what a card says while its account cannot be used

``readiness`` answers without the network or a sign-in why a proposal cannot be
carried out ("" when it can). ``prepare`` signs the account in when needed (a
browser sign-in, only ever after a click); ``execute`` makes the one call and
returns an ExecResult or raises ExecError, whose ``outcome`` says whether the
change certainly did not happen ("failed") or may have happened ("unknown":
never retried by itself). ``peek`` reads the event without ever signing in.
Nothing here retries a change: a Retry is a new click on the card.

Composio is a seam only: ``backend = "composio"`` is accepted by config.py and
answered here with "Composio is not built into this version"; a later backend
implements the same small protocol and nothing else changes.

Qt-free and blocking (callers use the worker thread). Logs name only action
ids, kinds, account aliases ("work", "personal", else "other"), statuses and
HTTP status codes.
"""

from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol

from .actions import (
    CALENDAR,
    CANCEL,
    COUNTDOWN_KINDS,
    MOVE,
    RSVP,
    STATUS_CREATED,
    STATUS_EXISTS,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SENT,
    STATUS_UNKNOWN,
    TODO,
    EditInvalid,
    ProposedAction,
    edit_action,
    sent_text,
    stated_when,
    when_text,
)
from .config import BACKEND_COMPOSIO, BACKEND_GOOGLE, DEFAULT_ACCOUNT, AccountConfig
from .gcal import (
    NO_TITLE,
    CalendarError,
    CalendarNotSignedIn,
    CalendarUnknownOutcome,
    EventDetails,
    EventGone,
    GoogleCalendar,
    NotAllowed,
)
from .google_auth import (
    CALENDAR_FEATURE,
    FEATURE_SCOPES,
    PROBLEM_BLOCKED,
    PROBLEM_DENIED,
    PROBLEM_EXPIRED,
    PROBLEM_FAILED,
    PROBLEM_SCOPE,
    PROBLEM_SETUP,
    PROBLEM_SIGNED_OUT,
    PROBLEM_TIMEOUT,
    GoogleAccount,
    logged_alias,
    migrate_legacy_token,
)

if TYPE_CHECKING:
    from .actions import ActionStore
    from .config import Config

logger = logging.getLogger(__name__)

STAGE_SIGNIN = "signin"          # the browser sign-in is open
STAGE_SIGNED_IN = "signedin"     # signed in just now
STAGE_WORKING = "working"        # the call to Google is on its way
OUTCOME_FAILED = "failed"        # certainly not done (the card keeps Deny / Retry)
OUTCOME_UNKNOWN = "unknown"      # may have happened: check before retrying, never retried by itself
CALENDAR_SETUP_NOTE = "Google Calendar is not set up yet - see README step 8"
COMPOSIO_NOTE = "Composio is not built into this version"
NOT_SAVED_MESSAGE = "Could not save the decision before sending, so nothing was sent"
NOTHING_TO_DO = "There is nothing for Jarvis to carry out on this card"
_NOTIFY = {"all": "all", "external": "externalOnly", "none": "none"}
_SEPARATOR = " \u00b7 "   # middle dot, as on the cards
_RESPONSE_WORDS = {"accepted": "you accepted", "declined": "you declined", "tentative": "you said maybe",
                   "needsAction": "you haven't answered"}

OnStage = Callable[[str], None]


def _no_stage(_stage: str) -> None:
    pass


def account_of(action: ProposedAction) -> str:
    """The alias that acts for ``action``: its ``acct=`` for an RSVP, Move or Cancel; a Calendar
    event and a Todo's block always go to the personal calendar (as in earlier versions)."""
    if action.kind in COUNTDOWN_KINDS:
        return action.account
    return DEFAULT_ACCOUNT


# --------------------------------------------------------------------------
# Edits
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionEdit:
    """The Edit dialog's changes to one card, kept in memory only (keyed by action id).

    ``None`` = unchanged. ``to``, ``cc``, ``subject`` and ``confirmed_new`` are for the
    replies of a later version and are refused here.
    """

    answer: str | None = None          # RSVP: yes / no / maybe
    notify: str | None = None          # RSVP / Move / Cancel: all / external / none
    start: datetime | None = None      # Move
    end: datetime | None = None
    body: str | None = None            # the note
    to: tuple[str, ...] | None = None
    cc: tuple[str, ...] | None = None
    subject: str | None = None
    confirmed_new: frozenset[str] = frozenset()


class EditError(ValueError):
    """Why an edit cannot be used (shown in the dialog, which stays open)."""


def apply_edit(action: ProposedAction, edit: ActionEdit) -> ProposedAction:
    """``action`` with ``edit`` applied and checked like a line; the id never changes.

    Exactly what this returns is what the card shows and what Approve carries out.
    """
    if edit.to is not None or edit.cc is not None or edit.subject is not None or edit.confirmed_new:
        raise EditError("Recipients and subjects can't be edited in this version")
    try:
        return edit_action(action, answer=edit.answer, notify=edit.notify, start=edit.start,
                           end=edit.end, body=edit.body)
    except EditInvalid as exc:
        raise EditError(str(exc)) from None


# --------------------------------------------------------------------------
# Results and errors
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecResult:
    status: str             # STATUS_CREATED / STATUS_EXISTS / STATUS_SENT
    link: str = ""          # the event's page (htmlLink), "" when there is none (a cancelled event)
    result_text: str = ""   # "Accepted", "Moved", "Already cancelled" ... ("" = the card's default)


class ExecError(Exception):
    """Why a proposal was not carried out; the message is safe to show (never logged here).

    ``outcome``: OUTCOME_FAILED (not done) or OUTCOME_UNKNOWN (may have happened).
    ``problem``: google_auth's PROBLEM_* for a sign-in or setup problem, else "".
    """

    def __init__(self, message: str, *, outcome: str = OUTCOME_FAILED, problem: str = "",
                 status: int | None = None) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.problem = problem
        self.status = status


@dataclass(frozen=True)
class EventCheck:
    """The card's check line for an RSVP, Move or Cancel.

    Everything but ``allowed`` / ``reason`` / ``mismatch`` is shown on the card only, never logged.
    """

    text: str               # "Google: Project sync \u00b7 Thu Oct 8 \u00b7 12:00-1:00 PM \u00b7 you organize"
    allowed: bool           # Jarvis may carry the card out on this event
    reason: str = ""        # why not (the card's amber note)
    mismatch: str = ""      # MISMATCH_*: Google's title or time is not the line's ("" = they agree)
    title: str = ""         # Google's own title, whole
    identity: str = ""      # the address Google answered as (see EventDetails.account_email)
    link: str = ""          # Google's page of the event (htmlLink)
    tooltip: str = ""       # the whole check: Google's whole title, and what the briefing said


@dataclass(frozen=True)
class RunOutcome:
    """What one run_action call ended with."""

    status: str                         # STATUS_CREATED / EXISTS / SENT / FAILED / UNKNOWN
    result: ExecResult | None = None
    error: ExecError | None = None


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

class Backend(Protocol):
    name: str

    def handles(self, action: ProposedAction) -> bool: ...
    def readiness(self, action: ProposedAction) -> str: ...
    def signed_in(self, action: ProposedAction) -> bool: ...
    def prepare(self, action: ProposedAction, *, on_stage: OnStage) -> None: ...
    def execute(self, action: ProposedAction, *, on_stage: OnStage,
                interactive: bool = True) -> ExecResult: ...
    def peek(self, action: ProposedAction) -> EventDetails: ...


class CalendarBackend:
    """Google Calendar for every alias in ``calendars`` (alias -> gcal.GoogleCalendar or a fake)."""

    name = BACKEND_GOOGLE
    KINDS = frozenset({CALENDAR, TODO, RSVP, MOVE, CANCEL})

    def __init__(self, calendars: Mapping[str, Any], *,
                 now: Callable[[], datetime] | None = None) -> None:
        self._calendars = dict(calendars)
        self._now = now or (lambda: datetime.now().astimezone())

    def handles(self, action: ProposedAction) -> bool:
        return action.kind in self.KINDS

    def calendar(self, alias: str) -> Any | None:
        return self._calendars.get(alias)

    def aliases(self) -> list[str]:
        return list(self._calendars)

    def readiness(self, action: ProposedAction) -> str:
        """"" when the account's calendar is there and set up; never the network."""
        calendar = self._calendars.get(account_of(action))
        if calendar is None or not _call_bool(calendar, "is_configured"):
            return CALENDAR_SETUP_NOTE
        return ""

    def signed_in(self, action: ProposedAction) -> bool:
        calendar = self._calendars.get(account_of(action))
        return calendar is not None and _call_bool(calendar, "is_signed_in")

    def prepare(self, action: ProposedAction, *, on_stage: OnStage) -> None:
        """Sign the account in first when there is no usable sign-in (opens the browser)."""
        calendar = self._require(action)
        if self.signed_in(action):
            return
        on_stage(STAGE_SIGNIN)
        try:
            calendar.sign_in()
        except CalendarError as exc:
            raise _exec_error(exc) from None
        on_stage(STAGE_SIGNED_IN)

    def execute(self, action: ProposedAction, *, on_stage: OnStage,
                interactive: bool = True) -> ExecResult:
        """The one call. Without ``interactive`` nothing opens the browser: a missing sign-in is
        a failure (nothing was sent)."""
        calendar = self._require(action)
        if interactive:
            self.prepare(action, on_stage=on_stage)
        elif not self.signed_in(action):
            raise ExecError(_not_signed_in_text(account_of(action)), problem=PROBLEM_SIGNED_OUT)
        on_stage(STAGE_WORKING)
        field = action.field
        try:
            if action.kind in (CALENDAR, TODO):
                event = action.block_event() if action.kind == TODO else action
                if event is None:
                    raise ExecError("This to-do has no block time")
                created = calendar.create_event(event)
                return ExecResult(STATUS_EXISTS if created.existed else STATUS_CREATED, created.link)
            send_updates = _NOTIFY.get(field("notify", "all"), "all")
            calendar_id = field("cal", "primary")
            if action.kind == RSVP:
                change = calendar.respond(field("event"), field("answer"), comment=action.body,
                                          send_updates=send_updates, calendar_id=calendar_id,
                                          interactive=interactive)
            elif action.kind == MOVE:
                if action.start is None or action.end is None:
                    raise ExecError("This move has no new time")
                change = calendar.move(field("event"), action.start, action.end, send_updates=send_updates,
                                       calendar_id=calendar_id, now=self._now(), interactive=interactive)
            else:
                change = calendar.cancel(field("event"), send_updates=send_updates, calendar_id=calendar_id,
                                         interactive=interactive)
        except CalendarError as exc:
            raise _exec_error(exc) from None
        return ExecResult(STATUS_SENT, change.link, sent_text(action, already=change.already))

    def peek(self, action: ProposedAction) -> EventDetails:
        """Google's own view of the card's event; never signs in (CalendarNotSignedIn instead)."""
        calendar = self._require(action)
        return calendar.get_event(action.field("event"), calendar_id=action.field("cal", "primary"),
                                  interactive=False)

    def _require(self, action: ProposedAction) -> Any:
        calendar = self._calendars.get(account_of(action))
        if calendar is None:
            raise ExecError(CALENDAR_SETUP_NOTE, problem=PROBLEM_SETUP)
        return calendar


def _exec_error(exc: CalendarError) -> ExecError:
    outcome = OUTCOME_UNKNOWN if isinstance(exc, CalendarUnknownOutcome) else OUTCOME_FAILED
    message = str(exc) or type(exc).__name__
    return ExecError(message, outcome=outcome, problem=getattr(exc, "problem", ""),
                     status=getattr(exc, "status", None))


def _call_bool(target: Any, name: str) -> bool:
    try:
        return bool(getattr(target, name)())
    except Exception as exc:  # noqa: BLE001 - only decides what the card offers
        logger.debug("Could not check %s (%s)", name, type(exc).__name__)
        return False


def _not_signed_in_text(alias: str) -> str:
    return f"Not signed in to the {alias} account; nothing was sent - click Sign in"


# --------------------------------------------------------------------------
# Executor
# --------------------------------------------------------------------------

class Executor:
    """Chooses the backend for a proposal and carries it out (one call; see the module docs)."""

    def __init__(self, backends: Sequence[Backend], accounts: Mapping[str, AccountConfig]) -> None:
        self._backends = list(backends)
        self._accounts = dict(accounts)

    @property
    def accounts(self) -> Mapping[str, AccountConfig]:
        return dict(self._accounts)

    def backend_for(self, action: ProposedAction) -> Backend | None:
        """The backend of the action's account that handles its kind, or None.

        A Calendar event and a Todo's block always use Google (the personal calendar, as in
        earlier versions); an RSVP, Move or Cancel uses its account's ``backend``.
        """
        name = BACKEND_GOOGLE
        if action.kind in COUNTDOWN_KINDS:
            account = self._accounts.get(action.account)
            if account is None:
                return None
            name = account.backend
        return next((backend for backend in self._backends
                     if backend.name == name and backend.handles(action)), None)

    def readiness(self, action: ProposedAction) -> str:
        """"" when ``action`` can be carried out now, else why not (no network, no sign-in)."""
        if not action.actionable:
            return NOTHING_TO_DO
        if action.kind in COUNTDOWN_KINDS:
            alias = action.account
            account = self._accounts.get(alias)
            if account is None:
                return f'No account named "{alias}" in config.toml [accounts]'
            if account.backend == BACKEND_COMPOSIO:
                return COMPOSIO_NOTE
            if CALENDAR_FEATURE not in account.features:
                return (f"The {alias} account is not set up for calendar actions "
                        f"(config.toml [accounts.{alias}] features)")
        backend = self.backend_for(action)
        if backend is None:
            return "Jarvis can't carry this out in this version"
        return backend.readiness(action)

    def signed_in(self, action: ProposedAction) -> bool:
        """The account that acts for ``action`` has a usable saved sign-in (no network)."""
        backend = self.backend_for(action)
        return backend is not None and backend.signed_in(action)

    def prepare(self, action: ProposedAction, *, on_stage: OnStage = _no_stage) -> None:
        """Sign the action's account in when needed (the browser; only after a click)."""
        self._backend(action).prepare(action, on_stage=on_stage)

    def execute(self, action: ProposedAction, *, on_stage: OnStage = _no_stage,
                interactive: bool = True) -> ExecResult:
        """One call to the backend; ExecResult, or ExecError with its outcome. Never retried."""
        backend = self._backend(action)
        logger.info("Carrying out action %s (%s, %s)", action.id, action.kind,
                    logged_alias(account_of(action)))
        return backend.execute(action, on_stage=on_stage, interactive=interactive)

    def peek(self, action: ProposedAction) -> EventDetails:
        """Google's own view of the card's event (never signs in)."""
        return self._backend(action).peek(action)

    def _backend(self, action: ProposedAction) -> Backend:
        reason = self.readiness(action)
        if reason:
            raise ExecError(reason, problem=PROBLEM_SETUP)
        backend = self.backend_for(action)
        assert backend is not None
        return backend


# --------------------------------------------------------------------------
# One job of the action worker
# --------------------------------------------------------------------------

def run_action(executor: Executor, action: ProposedAction, *, store: ActionStore | None = None,
               writes_running: bool = False, on_stage: OnStage = _no_stage) -> RunOutcome:
    """Carry out one approved proposal, exactly once (never retried here or anywhere else).

    ``writes_running`` (an RSVP, Move or Cancel whose undo countdown ran out): sign the account
    in first when needed, save "running" (with kind and account) to ``store`` right before the
    call - nothing is sent when that could not be saved -, make the one call without any
    further sign-in, and save the result over "running": "sent", "failed" (certainly not
    done) or "unknown" (it may have happened: the call was sent but no clear answer came
    back, or something unexpected broke after "running" was saved). The result is saved here,
    on the worker thread, so it is kept even when the app quits meanwhile; a crash leaves
    "running", which the next start turns into "unknown".

    Otherwise (a Calendar event, a Todo's block) nothing is saved here: the caller saves the
    result (created / exists / failed) as before. Never raises for an ExecError or a bug in a
    backend (a bug is logged by type only: its message could carry a token).
    """
    running = False
    result: ExecResult | None = None
    error: ExecError | None = None
    try:
        if writes_running:
            if store is None:
                raise ExecError(NOT_SAVED_MESSAGE)
            executor.prepare(action, on_stage=on_stage)
            if not store.set(action.id, STATUS_RUNNING, kind=action.kind, account=action.account):
                raise ExecError(NOT_SAVED_MESSAGE)
            running = True
            result = executor.execute(action, on_stage=on_stage, interactive=False)
        else:
            result = executor.execute(action, on_stage=on_stage)
    except ExecError as exc:
        error = exc
    except Exception as exc:  # noqa: BLE001 - a bug must not end the worker
        logger.error("Carrying out action %s failed unexpectedly (%s)", action.id, type(exc).__name__)
        logger.debug("Where:\n%s", "".join(traceback.format_tb(exc.__traceback__)))
        error = ExecError(f"unexpected error ({type(exc).__name__})",
                          outcome=OUTCOME_UNKNOWN if running else OUTCOME_FAILED)
    if error is not None:
        status = STATUS_UNKNOWN if running and error.outcome == OUTCOME_UNKNOWN else STATUS_FAILED
        logger.info("Action %s (%s, %s) not carried out: %s%s", action.id, action.kind,
                    logged_alias(account_of(action)), status,
                    f" (HTTP {error.status})" if error.status else "")
    else:
        assert result is not None
        status = result.status
    if writes_running and store is not None:
        if result is not None:
            store.set(action.id, status, link=result.link, message=result.result_text, kind=action.kind,
                      account=action.account)
        else:
            store.set(action.id, status, message=str(error or ""), kind=action.kind, account=action.account)
    return RunOutcome(status, result, error)


# --------------------------------------------------------------------------
# Accounts and calendars from config.toml
# --------------------------------------------------------------------------

def account_features(config: Config, alias: str) -> tuple[str, ...]:
    """What ``alias`` signs in for: its configured features; "personal" always has the
    calendar (Calendar proposals, to-do blocks and the agenda use it)."""
    account = config.accounts.get(alias)
    features = [name for name in (account.features if account is not None else ()) if name in FEATURE_SCOPES]
    if alias == DEFAULT_ACCOUNT and CALENDAR_FEATURE not in features:
        features.insert(0, CALENDAR_FEATURE)
    return tuple(features)


def google_aliases(config: Config) -> list[str]:
    """"personal" and every ``[accounts.<alias>]`` with the google backend, in config order."""
    aliases = [DEFAULT_ACCOUNT]
    for alias, account in config.accounts.items():
        if alias != DEFAULT_ACCOUNT and account.backend == BACKEND_GOOGLE and account_features(config, alias):
            aliases.append(alias)
    return aliases


def build_accounts(config: Config, *, flow_factory: Callable[[Any, list[str]], Any] | None = None,
                   refresh: Callable[[Any], None] | None = None) -> dict[str, GoogleAccount]:
    """One GoogleAccount per Google alias, sharing the OAuth client of ``[calendar]
    client_secret``; tokens in ``config.data_dir`` (google_token_<alias>.json).

    The single google_token.json of older versions is moved to the personal account first
    (google_auth.migrate_legacy_token), so nobody has to sign in again. Nothing here opens the
    browser or talks to Google.
    """
    migrate_legacy_token(config.data_dir)
    accounts: dict[str, GoogleAccount] = {}
    for alias in google_aliases(config):
        accounts[alias] = GoogleAccount(
            alias, client_secret_path=config.calendar.client_secret_path, data_dir=config.data_dir,
            features=account_features(config, alias), flow_factory=flow_factory, refresh=refresh)
    return accounts


def build_calendars(config: Config, accounts: Mapping[str, GoogleAccount] | None = None, *,
                    service_factory: Callable[[Any], Any] | None = None) -> dict[str, GoogleCalendar]:
    """One GoogleCalendar per alias with the calendar feature ("personal" always; it uses
    ``[calendar] calendar_id``, the others "primary"). ``accounts`` defaults to
    build_accounts(config)."""
    if accounts is None:
        accounts = build_accounts(config)
    calendars: dict[str, GoogleCalendar] = {}
    for alias, account in accounts.items():
        if CALENDAR_FEATURE not in account.features:
            continue
        calendar_id = config.calendar.calendar_id if alias == DEFAULT_ACCOUNT else "primary"
        calendars[alias] = GoogleCalendar(account=account, calendar_id=calendar_id,
                                          service_factory=service_factory)
    return calendars


# --------------------------------------------------------------------------
# What a card says: the check line and sign-in problems
# --------------------------------------------------------------------------

def check_event(action: ProposedAction, details: EventDetails, today: date) -> EventCheck:
    """What the card says Google has for its event, and whether Jarvis may act on it.

    The text leads with Google's own title and time - what shows that the line's event id
    is the event the card means -, then who organizes it and your answer, and ends with the
    address Google answered as: "Google: Project sync \u00b7 Thu Oct 8 \u00b7 12:00-1:00 PM \u00b7
    you organize \u00b7 as ana@example.edu". A long title is shortened (the tooltip has all of
    it). When Google's title or time is not the line's, ``mismatch`` says which and the text
    starts "Google (other time): ..." instead.
    """
    title = _short_title(details.title)
    parts = [when_text(details, today)]
    if details.series:
        parts.append("whole repeating series")
    elif details.recurring_instance:
        parts.append("one occurrence")
    if details.organizer_self:
        parts.append("you organize")
    else:
        parts.append(f"organized by {details.organizer}" if details.organizer else "organized by someone else")
    reason = ""
    if action.kind == RSVP:
        if details.self_email:
            parts.append(_RESPONSE_WORDS.get(details.self_response, "you haven't answered"))
        else:
            reason = "You are not on this event's guest list, so Jarvis can't answer it - open it instead"
    elif action.kind == MOVE:
        if details.series:
            reason = "This is a whole repeating series; Jarvis moves single events only - open it"
        elif not (details.organizer_self or details.guests_can_modify):
            reason = "You don't organize this event, so Jarvis can't move it - open it to answer instead"
        elif details.all_day:
            reason = "All-day events can't be moved from here - open it"
        elif not details.organizer_self:
            parts.append("guests may change it")
    elif action.kind == CANCEL:
        if details.series:
            reason = "This is a whole repeating series; Jarvis cancels single events only - open it"
        elif not details.organizer_self:
            reason = "You don't organize this event, so Jarvis can't cancel it - open it to decline instead"
    identity = details.account_email
    if identity:
        parts.append(f"as {identity}")
    mismatch = event_mismatch(action, details)
    head = f"Google ({_MISMATCH_WORDS[mismatch]})" if mismatch else "Google"
    rest = _SEPARATOR.join(part for part in parts if part)
    text = f"{head}: {title}{_SEPARATOR}{rest}"
    tips = [f"{head}: {details.title}{_SEPARATOR}{rest}"]
    if mismatch:
        stated = stated_when(action)
        said = [action.field("title"), when_text(stated, today) if stated is not None else ""]
        tips.append("The briefing says: " + _SEPARATOR.join(part for part in said if part))
    return EventCheck(text, allowed=not reason, reason=reason, mismatch=mismatch, title=details.title,
                      identity=identity, link=details.link, tooltip="\n".join(tips))


MISMATCH_TITLE = "title"
MISMATCH_TIME = "time"
MISMATCH_BOTH = "title+time"
_MISMATCH_WORDS = {MISMATCH_TITLE: "other title", MISMATCH_TIME: "other time",
                   MISMATCH_BOTH: "other title and time"}
_CHECK_TITLE_CHARS = 40   # Google's title on the check line; the tooltip has all of it
_WORDS_RE = re.compile(r"[^\W_]+")


def _short_title(title: str) -> str:
    if len(title) <= _CHECK_TITLE_CHARS:
        return title
    cut = title[:_CHECK_TITLE_CHARS]
    if " " in cut[_CHECK_TITLE_CHARS // 2:]:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:-") + "\u2026"


def event_mismatch(action: ProposedAction, details: EventDetails) -> str:
    """How Google's event differs from what the line says about it: MISMATCH_TITLE when neither
    title contains the other (case, spacing and punctuation aside), MISMATCH_TIME when its start
    is not the line's at= (as a wall time or as an instant on this PC), MISMATCH_BOTH, or "".
    A line without title= or at= (or an event without a title) is not compared on that part."""
    title = False
    said, google = _words(action.field("title")), _words(details.title)
    if said and google and details.title != NO_TITLE:
        title = f" {said} " not in f" {google} " and f" {google} " not in f" {said} "
    time = False
    stated = stated_when(action)
    if stated is not None:
        if stated.all_day_start is not None:
            day = details.all_day_start or (details.start.date() if details.start is not None else None)
            time = day is not None and day != stated.all_day_start
        elif stated.start is not None:
            start = details.start
            time = start is None or stated.start.replace(second=0, microsecond=0) not in (
                start.replace(tzinfo=None, second=0, microsecond=0),
                start.astimezone().replace(tzinfo=None, second=0, microsecond=0))
    if title and time:
        return MISMATCH_BOTH
    return MISMATCH_TITLE if title else (MISMATCH_TIME if time else "")


def _words(text: str) -> str:
    return " ".join(_WORDS_RE.findall(text.casefold()))


def check_failure(action: ProposedAction, error: BaseException) -> EventCheck:
    """The check line when Google could not show the event (never allowed)."""
    alias = action.account
    problem = getattr(error, "problem", "")
    if isinstance(error, CalendarNotSignedIn) or problem == PROBLEM_SIGNED_OUT:
        return EventCheck(f"Not signed in to the {alias} account - Sign in to check this event",
                          allowed=False)
    if problem in (PROBLEM_EXPIRED, PROBLEM_BLOCKED, PROBLEM_DENIED, PROBLEM_SCOPE, PROBLEM_TIMEOUT):
        return EventCheck(sign_in_note(alias, problem=problem, message=str(error)), allowed=False)
    if isinstance(error, EventGone):
        return EventCheck("Google: event not found (deleted, cancelled or a wrong id)", allowed=False,
                          reason="Google can't find this event, so Jarvis won't act on it")
    text = str(error) or type(error).__name__
    if isinstance(error, NotAllowed):
        return EventCheck(f"Google: {text}", allowed=False, reason=text)
    if problem == PROBLEM_SETUP:
        return EventCheck(text, allowed=False)
    return EventCheck(f"Couldn't check the event with Google: {text}", allowed=False)


def sign_in_note(alias: str, *, signed_in: bool = False, problem: str = "", message: str = "") -> str:
    """What a card of ``alias`` says while that account cannot be used ("" when it can).

    ``problem`` / ``message`` are the account's last sign-in problem (GoogleCalendar
    .sign_in_problem()); the messages are google_auth's, which never name an address.
    """
    if signed_in:
        return ""
    if problem == PROBLEM_EXPIRED:
        return f"The {alias} account's Google sign-in expired or was revoked - click Sign in to sign in again"
    if problem == PROBLEM_BLOCKED:
        return message or (f"Google blocked the sign-in: the {alias} account's administrator does not "
                           "allow this app")
    if problem == PROBLEM_DENIED:
        return f"Google sign-in for the {alias} account was cancelled or access was denied - click Sign in to try again"
    if problem == PROBLEM_SCOPE:
        return f"Google did not allow Calendar access for the {alias} account - click Sign in and tick every box"
    if problem == PROBLEM_TIMEOUT:
        return message or f"Google sign-in for the {alias} account was not finished in time - click Sign in to try again"
    if problem == PROBLEM_SETUP:
        return message or CALENDAR_SETUP_NOTE
    if problem == PROBLEM_FAILED and message:
        return f"{message} - click Sign in to try again"
    return f"Not signed in to the {alias} account - click Sign in"
