"""Carrying out approved proposals: which service acts for which account, and how.

    Executor(backends, accounts)   picks the backend for a proposal: its kind and the
                                   ``[accounts.<alias>]`` table of its account in config.toml
    CalendarBackend(calendars)     Google Calendar, one gcal.GoogleCalendar per account alias:
                                   a Calendar event or a Todo's block (always the "personal"
                                   calendar, as before), and an RSVP, Move or Cancel (the line's
                                   ``acct=``)
    GmailBackend(senders, ...)     Gmail (gmail.send), one gmail.GmailSender per alias: a Reply or
                                   an Email from the line's ``acct=``, after the recipient check
    run_action(executor, action)   one job of the action worker: with ``writes_running`` (the
                                   undo countdown ran out) "running" is saved before the call and
                                   the result after it; a Reply or Email is sent only that way
    mail_status(action)            Executor: what a Reply / Email card shows (From, recipients,
                                   why it can't send yet) and what its Send click does
    pending_confirmation(alias)    Executor: a new binding you have not confirmed yet ("Signed in
                                   as X for 'personal' - is that right?"); nothing is sent or
                                   changed for that alias until confirm_account(), and
                                   disconnect() is "No, use another account"
    change_problem(alias)          Executor: why nothing may be changed for the alias although it
                                   is signed in (fails closed: also a saved sign-in Jarvis has no
                                   binding for, which signs in again)
    build_accounts(config)         one google_auth.GoogleAccount per alias (the single sign-in
                                   of older versions becomes "personal"), bound via accounts.json
    build_calendars(config)        one gcal.GoogleCalendar per alias that has the calendar feature
    build_senders(config)          one gmail.GmailSender per alias that has the gmail_send feature
    build_executor(config, ...)    the Executor of both backends (recipients.json for the history)
    apply_edit(action, edit)       the Edit dialog's changes (memory only; same id)
    check_event(action, details)   the card's check line: Google's own title, time and organizer,
                                   and whether Jarvis may act on that event
    sign_in_note(alias, ...)       what a card says while its account cannot be used

``readiness`` answers without the network or a sign-in why a proposal cannot be
carried out ("" when it can). ``prepare`` signs the account in when needed (a
browser sign-in, only ever after a click; never for email: the card's Send
signs in first, so its From line shows the account before any countdown);
``execute`` makes the one call and returns an ExecResult or raises ExecError,
whose ``outcome`` says whether the change certainly did not happen ("failed")
or may have happened ("unknown": never retried by itself). ``peek`` reads the
event without ever signing in. Nothing here retries a change: a Retry is a new
click on the card, and one approval sends at most one email
(google_auth.single_send_http).

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
    EVENT_KINDS,
    MAIL_KINDS,
    MOVE,
    REPLY,
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
    edit_mail,
    mail_problem,
    open_text,
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
from .gmail import GmailError, GmailSender, GmailUnknownOutcome, OutgoingMail
from .google_auth import (
    ACCOUNTS_FILE,
    CALENDAR_FEATURE,
    FEATURE_SCOPES,
    GMAIL_FEATURE,
    PROBLEM_BLOCKED,
    PROBLEM_CONFIRM,
    PROBLEM_DENIED,
    PROBLEM_EXPIRED,
    PROBLEM_FAILED,
    PROBLEM_IDENTITY,
    PROBLEM_SCOPE,
    PROBLEM_SETUP,
    PROBLEM_SIGNED_OUT,
    PROBLEM_TIMEOUT,
    AccountBindings,
    GoogleAccount,
    logged_alias,
    migrate_legacy_token,
)
from .recipients import (
    OWN_RECIPIENT_MESSAGE,
    RECIPIENTS_FILE,
    RecipientHistory,
    RecipientReview,
    own_recipients,
    review,
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
CLOSING_MESSAGE = "Jarvis was closing when the sign-in finished, so nothing was sent - click again"
NOTHING_TO_DO = "There is nothing for Jarvis to carry out on this card"
MAIL_SETUP_NOTE = "Sending email is not set up yet - see README step 8b"
# A Reply or Email goes out only through run_action(writes_running=True): after the undo
# countdown, with "running" saved first.
COUNTDOWN_ONLY_MESSAGE = "Email is only sent when the Send countdown runs out; nothing was sent"
NEW_RECIPIENTS_MESSAGE = "Tick the new recipients in Edit first; nothing was sent"
NO_EVENT_MESSAGE = "There is no event to check on this card"
UNCONFIRMED_FROM = "account not confirmed yet"
# A new binding you have not confirmed yet (google_auth.PROBLEM_CONFIRM): nothing is sent or changed.
CONFIRM_FIRST_MESSAGE = ("Confirm first that Jarvis signed in to the right Google account for {alias}; "
                         "nothing was changed")
# A saved sign-in Jarvis has no binding for (google_auth.PROBLEM_IDENTITY): sign in again.
UNKNOWN_ACCOUNT_MESSAGE = ("Jarvis doesn't know which Google account the {alias} account's sign-in is; sign in "
                           "again to confirm it - nothing was sent or changed")
CHECK_ACCOUNT_FAILED_MESSAGE = "Could not check which Google account this is; nothing was sent or changed"
# "No, use another account" when the saved sign-in can't be deleted: the binding stays unconfirmed.
DISCONNECT_FAILED_MESSAGE = ("Could not forget the {alias} account's Google sign-in (its file is in use); nothing "
                             "was sent or changed")
_NOTIFY = {"all": "all", "external": "externalOnly", "none": "none"}
_SEPARATOR = " \u00b7 "   # middle dot, as on the cards
_RESPONSE_WORDS = {"accepted": "you accepted", "declined": "you declined", "tentative": "you said maybe",
                   "needsAction": "you haven't answered"}

OnStage = Callable[[str], None]


def _no_stage(_stage: str) -> None:
    pass


# Carried out for the line's acct= (a Calendar event and a Todo's block use the personal calendar).
ACCOUNT_KINDS = EVENT_KINDS | MAIL_KINDS


def account_of(action: ProposedAction) -> str:
    """The alias that acts for ``action``: its ``acct=`` for an RSVP, Move, Cancel, Reply or Email;
    a Calendar event and a Todo's block always go to the personal calendar (as in earlier
    versions)."""
    if action.kind in ACCOUNT_KINDS:
        return action.account
    return DEFAULT_ACCOUNT


# --------------------------------------------------------------------------
# Edits
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionEdit:
    """The Edit dialog's changes to one card, kept in memory only (keyed by action id).

    ``None`` = unchanged. RSVP / Move / Cancel: ``answer``, ``notify``, ``start`` / ``end``,
    ``body`` (the note). Reply / Email: ``to``, ``cc`` (the chips), ``subject`` (an Email's),
    ``body`` (the text as sent) and ``confirmed_new``: every NEW RECIPIENT address ticked in the
    dialog (the whole set each time the dialog is saved; empty = none ticked).
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
    try:
        if action.kind in MAIL_KINDS:
            if edit.answer is not None or edit.notify is not None or edit.start is not None \
                    or edit.end is not None:
                raise EditError("A reply or an email has no answer, time or guest notice to edit")
            return edit_mail(action, to=edit.to, cc=edit.cc, subject=edit.subject, body=edit.body,
                             confirmed=tuple(edit.confirmed_new))
        if edit.to is not None or edit.cc is not None or edit.subject is not None or edit.confirmed_new:
            raise EditError("Recipients and subjects can only be edited on a reply or an email")
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
class MailStatus:
    """What a Reply / Email card shows about sending, and what its Send click does (no network).

    ``sender`` / ``from_text`` are shown on the card only, never logged. ``review`` names every
    recipient as OWN / TRUSTED / KNOWN / NEW (recipients.py) for the chips; None only when the
    card is not a Reply / Email Jarvis sends.
    """

    alias: str
    sender: str                 # the bound account's address ("" while Jarvis doesn't know it)
    from_text: str              # "From: work (ana@example.edu)" / "From: work (account not confirmed yet)"
    problem: str = ""           # google_auth PROBLEM_* why it can't send now ("" = it can)
    note: str = ""              # the card's amber note for that problem ("" when there is none)
    sign_in: bool = False       # a Send click opens the Google sign-in first (no countdown)
    hand_off: bool = False      # Jarvis won't send from here: Copy / Open, and Done instead of Send
    review: RecipientReview | None = None
    confirm: bool = False       # a Send click asks first whether the bound Google account is the right one

    @property
    def refused(self) -> bool:
        """The recipients as they stand can't be sent to (one is the sending account itself, or To
        is empty): a Send click only says so; Edit fixes it."""
        return self.review is not None and bool(self.review.problem or self.review.own)

    @property
    def needs_edit(self) -> bool:
        """A Send click opens the Edit dialog: a NEW RECIPIENT is not ticked yet."""
        return self.review is not None and bool(self.review.unconfirmed)

    @property
    def ready(self) -> bool:
        """A Send click may start the countdown (still subject to a long body's review)."""
        return not self.problem and not self.hand_off and self.review is not None and self.review.ready


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
    def sign_in(self, alias: str, *, on_stage: OnStage) -> None: ...


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
        """Sign the account in first when there is no usable sign-in (opens the browser). Nothing is
        changed unless the saved sign-in is the account's confirmed Google account (ExecError
        PROBLEM_CONFIRM for a binding you have not confirmed, PROBLEM_IDENTITY for a sign-in
        Jarvis has no binding for; before "running" is saved)."""
        calendar = self._require(action)
        if not self.signed_in(action):
            on_stage(STAGE_SIGNIN)
            try:
                calendar.sign_in()
            except CalendarError as exc:
                raise _exec_error(exc) from None
            on_stage(STAGE_SIGNED_IN)
        self._require_confirmed(action, calendar)

    def _require_confirmed(self, action: ProposedAction, calendar: Any) -> None:
        """Fails closed (google_auth.GoogleAccount.change_problem): a Google account you did not
        confirm for the alias, or one Jarvis can't tell, changes nothing."""
        problem, message = _change_problem(calendar)
        if problem == PROBLEM_CONFIRM:
            raise ExecError(CONFIRM_FIRST_MESSAGE.format(alias=account_of(action)), problem=PROBLEM_CONFIRM)
        if problem:
            raise ExecError(message or UNKNOWN_ACCOUNT_MESSAGE.format(alias=account_of(action)), problem=problem)

    def execute(self, action: ProposedAction, *, on_stage: OnStage,
                interactive: bool = True) -> ExecResult:
        """The one call. Without ``interactive`` nothing opens the browser: a missing sign-in is
        a failure (nothing was sent)."""
        calendar = self._require(action)
        if interactive:
            self.prepare(action, on_stage=on_stage)
        elif not self.signed_in(action):
            raise ExecError(_not_signed_in_text(account_of(action)), problem=PROBLEM_SIGNED_OUT)
        self._require_confirmed(action, calendar)
        on_stage(STAGE_WORKING)
        field = action.field
        try:
            if action.kind in (CALENDAR, TODO):
                event = action.block_event() if action.kind == TODO else action
                if event is None:
                    raise ExecError("This to-do has no block time")
                # Without interactive ("running" is saved) nothing may open the browser any more.
                created = (calendar.create_event(event) if interactive
                           else calendar.create_event(event, interactive=False))
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
        if action.kind not in EVENT_KINDS:
            raise ExecError(NO_EVENT_MESSAGE)
        calendar = self._require(action)
        return calendar.get_event(action.field("event"), calendar_id=action.field("cal", "primary"),
                                  interactive=False)

    def sign_in(self, alias: str, *, on_stage: OnStage = _no_stage) -> None:
        """The browser sign-in of ``alias``'s account (a click on Sign in)."""
        calendar = self._calendars.get(alias)
        if calendar is None:
            raise ExecError(CALENDAR_SETUP_NOTE, problem=PROBLEM_SETUP)
        on_stage(STAGE_SIGNIN)
        try:
            calendar.sign_in()
        except CalendarError as exc:
            raise _exec_error(exc) from None
        on_stage(STAGE_SIGNED_IN)

    def _require(self, action: ProposedAction) -> Any:
        calendar = self._calendars.get(account_of(action))
        if calendar is None:
            raise ExecError(CALENDAR_SETUP_NOTE, problem=PROBLEM_SETUP)
        return calendar


class GmailBackend:
    """Gmail for every alias in ``senders`` (alias -> gmail.GmailSender or a fake): a Reply or an
    Email from the line's ``acct=``.

    Nothing here opens the browser except ``sign_in`` (a click). A message goes out only from
    ``execute(..., interactive=False)`` - run_action's "running" path after the undo countdown -
    and only when the saved sign-in is the bound Google account and you confirmed that binding,
    the card's text is sendable (actions.mail_problem), no recipient is the sending account itself
    (checked on the card's recipients, on the message and once more right before the send) and
    every NEW RECIPIENT was ticked in the Edit dialog. After a send the recipients are remembered
    (recipients.json), so they need no tick next time.
    """

    name = BACKEND_GOOGLE
    KINDS = MAIL_KINDS

    def __init__(self, senders: Mapping[str, Any], *, history: RecipientHistory | None = None,
                 trusted_domains: Sequence[str] = ()) -> None:
        self._senders = dict(senders)
        self._history = history
        self._trusted = tuple(trusted_domains)

    def handles(self, action: ProposedAction) -> bool:
        return action.kind in self.KINDS

    def sender(self, alias: str) -> Any | None:
        return self._senders.get(alias)

    def aliases(self) -> list[str]:
        return list(self._senders)

    def readiness(self, action: ProposedAction) -> str:
        """"" when the account's sending is set up and the card's text is sendable; no network."""
        sender = self._senders.get(action.account)
        if sender is None or not _call_bool(sender, "is_configured"):
            return MAIL_SETUP_NOTE
        return mail_problem(action)

    def signed_in(self, action: ProposedAction) -> bool:
        sender = self._senders.get(action.account)
        return sender is not None and _call_bool(sender, "is_signed_in")

    def review(self, action: ProposedAction) -> RecipientReview:
        """The card's recipients, each one OWN (the sending account itself: the card is refused) /
        TRUSTED / KNOWN / NEW, the new ones not yet ticked in Edit."""
        sender = self._senders.get(action.account)
        own = _call_text(sender, "from_address") if sender is not None else ""
        return review(action.recipients(), action.cc(), own=own, account=action.account,
                      confirmed=action.confirmed, trusted_domains=self._trusted, history=self._history)

    def status(self, action: ProposedAction, reason: str = "") -> MailStatus:
        """See MailStatus; ``reason`` is the Executor's readiness answer (not set up: a hand-off)."""
        alias = action.account
        sender = self._senders.get(alias)
        address = _call_text(sender, "from_address") if sender is not None else ""
        from_text = f"From: {alias} ({address or UNCONFIRMED_FROM})"
        recipients = self.review(action)
        if reason:
            return MailStatus(alias, address, from_text, PROBLEM_SETUP, reason, hand_off=True, review=recipients)
        problem, message = _sender_ready(sender)
        tools = hand_off_tools(action)
        if problem in (PROBLEM_SETUP, PROBLEM_BLOCKED):
            note = mail_note(alias, problem, message, tools=tools)
            return MailStatus(alias, address, from_text, problem, note, hand_off=True, review=recipients)
        if problem == PROBLEM_CONFIRM:
            return MailStatus(alias, address, from_text, problem, mail_note(alias, problem, message, tools=tools),
                              review=recipients, confirm=True)
        if problem:
            return MailStatus(alias, address, from_text, problem, mail_note(alias, problem, message, tools=tools),
                              sign_in=True, review=recipients)
        note = recipients.problem or (NEW_RECIPIENT_NOTE if recipients.unconfirmed else "")
        return MailStatus(alias, address, from_text, "", note, review=recipients)

    def prepare(self, action: ProposedAction, *, on_stage: OnStage = _no_stage) -> None:
        """Check that the message may go out now. Never opens the browser: a card's Send signs in
        first, so its From line shows the account before any countdown."""
        self._message(action)

    def execute(self, action: ProposedAction, *, on_stage: OnStage = _no_stage,
                interactive: bool = True) -> ExecResult:
        """The one send (run_action's "running" path only: ``interactive`` must be False)."""
        if interactive:
            raise ExecError(COUNTDOWN_ONLY_MESSAGE)
        mail = self._message(action)
        sender = self._senders[action.account]
        # Once more right before the send, against the message itself and the account as it is
        # now: never without a known sender, never to the sending account's own mailbox.
        if not mail.from_addr:
            raise ExecError(mail_note(action.account, PROBLEM_IDENTITY), problem=PROBLEM_IDENTITY)
        if (own_recipients(mail.to + mail.cc, mail.from_addr)
                or own_recipients(mail.to + mail.cc, _call_text(sender, "from_address"))):
            logger.info("Action %s (%s, %s) not sent: a recipient is the sending account itself", action.id,
                        action.kind, logged_alias(action.account))
            raise ExecError(OWN_RECIPIENT_MESSAGE.format(alias=action.account))
        logger.info("Sending action %s (%s, %s) to %d recipient(s)", action.id, action.kind,
                    logged_alias(action.account), mail.recipient_count)
        on_stage(STAGE_WORKING)
        try:
            sent = sender.send(mail)
        except GmailUnknownOutcome as exc:
            raise ExecError(str(exc), outcome=OUTCOME_UNKNOWN, problem=exc.problem, status=exc.status) from None
        except GmailError as exc:
            raise ExecError(str(exc), problem=exc.problem, status=exc.status) from None
        if self._history is not None:
            try:
                self._history.add(mail.to + mail.cc)
            except Exception as exc:  # noqa: BLE001 - the message went out; only the memory failed
                logger.warning("Could not remember the recipients of action %s (%s)", action.id,
                               type(exc).__name__)
        return ExecResult(STATUS_SENT, getattr(sent, "link", "") or "", sent_text(action))

    def peek(self, action: ProposedAction) -> EventDetails:
        raise ExecError(NO_EVENT_MESSAGE)

    def sign_in(self, alias: str, *, on_stage: OnStage = _no_stage) -> None:
        """The browser sign-in of ``alias`` for sending (a click on the card's Send or Sign in)."""
        sender = self._senders.get(alias)
        if sender is None:
            raise ExecError(MAIL_SETUP_NOTE, problem=PROBLEM_SETUP)
        on_stage(STAGE_SIGNIN)
        try:
            sender.sign_in()
        except GmailError as exc:
            raise ExecError(str(exc) or type(exc).__name__, problem=exc.problem or PROBLEM_FAILED,
                            status=exc.status) from None
        on_stage(STAGE_SIGNED_IN)

    def _message(self, action: ProposedAction) -> OutgoingMail:
        """The message exactly as the card shows it, or ExecError (failed: nothing was sent)."""
        sender = self._senders.get(action.account)
        if sender is None:
            raise ExecError(MAIL_SETUP_NOTE, problem=PROBLEM_SETUP)
        problem = mail_problem(action)
        if problem:
            raise ExecError(problem)
        state, message = _sender_ready(sender)
        if state:
            raise ExecError(mail_note(action.account, state, message), problem=state)
        recipients = self.review(action)
        if recipients.own:   # the card's note names the address; this message (saved, logged) does not
            raise ExecError(OWN_RECIPIENT_MESSAGE.format(alias=action.account))
        if recipients.problem:
            raise ExecError(f"{recipients.problem}; nothing was sent")
        if recipients.unconfirmed:
            raise ExecError(NEW_RECIPIENTS_MESSAGE)
        reply = action.kind == REPLY
        from_addr = _call_text(sender, "from_address")
        if not from_addr:   # the account it sends from must be known (gmail.GmailSender refuses too)
            raise ExecError(mail_note(action.account, PROBLEM_IDENTITY), problem=PROBLEM_IDENTITY)
        return OutgoingMail(account=action.account, from_addr=from_addr,
                            to=recipients.to, cc=recipients.cc, subject=action.field("subject") or action.title,
                            body=action.body, thread_id=action.field("thread") if reply else "",
                            in_reply_to=action.field("msgid") if reply else "")


NEW_RECIPIENT_NOTE = "New recipient: tick it in Edit before Send"


def _sender_ready(sender: Any) -> tuple[str, str]:
    """(PROBLEM_*, message) from the sender's ready(); a failing check counts as not set up."""
    if sender is None:
        return PROBLEM_SETUP, MAIL_SETUP_NOTE
    try:
        problem, message = sender.ready()
    except Exception as exc:  # noqa: BLE001 - only decides what the card offers
        logger.debug("Could not check the sending account (%s)", type(exc).__name__)
        return PROBLEM_SETUP, MAIL_SETUP_NOTE
    return str(problem or ""), str(message or "")


def _call_text(target: Any, name: str) -> str:
    try:
        value = getattr(target, name)()
    except Exception as exc:  # noqa: BLE001 - only decides what a card shows
        logger.debug("Could not read %s (%s)", name, type(exc).__name__)
        return ""
    return value if isinstance(value, str) else ""


def _change_problem(handle: Any) -> tuple[str, str]:
    """Why nothing may be sent or changed for a calendar's / sender's alias now: its
    ``change_problem()`` (google_auth.GoogleAccount's, which fails closed), or for an object
    without one (an older or a test double) only ``pending_confirmation()``. A check that breaks
    refuses too (PROBLEM_FAILED)."""
    getter = getattr(handle, "change_problem", None)
    if not callable(getter):
        return (PROBLEM_CONFIRM, "") if _call_text(handle, "pending_confirmation") else ("", "")
    try:
        problem, message = getter()
    except Exception as exc:  # noqa: BLE001 - refused: nothing is changed without a known account
        logger.warning("Could not check which Google account the sign-in is (%s)", type(exc).__name__)
        return PROBLEM_FAILED, CHECK_ACCOUNT_FAILED_MESSAGE
    if not isinstance(problem, str) or not isinstance(message, str):
        return PROBLEM_FAILED, CHECK_ACCOUNT_FAILED_MESSAGE
    return problem, message


def hand_off_tools(action: ProposedAction) -> str:
    """What a card offers instead when Jarvis can't send it: "Copy and Open" ("Copy" without a
    link)."""
    return "Copy and Open" if open_text(action) else "Copy"


def keep_calendar_hint(alias: str) -> str:
    """How to keep an account's Calendar actions when its administrator blocks sending email."""
    return f'remove "gmail_send" from config.toml [accounts.{alias}] features and sign in again'


_BLOCK_CODE_RE = re.compile(r"\((admin_policy_enforced|org_internal|domainPolicy)\)")


def mail_note(alias: str, problem: str, message: str = "", *, tools: str = "Copy and Open") -> str:
    """What a Reply / Email card of ``alias`` says while it can't send (no address in it).
    ``tools``: what the card offers instead ("Copy and Open", or "Copy" on a card without a link)."""
    if problem == PROBLEM_BLOCKED:
        match = _BLOCK_CODE_RE.search(message)
        code = match.group(1) if match else "admin_policy_enforced"
        return (f"The {alias} account's administrator does not allow this app to send email "
                f"({code}) - use {tools} instead. To keep Calendar actions for {alias}, "
                f"{keep_calendar_hint(alias)}")
    if problem == PROBLEM_SETUP:
        return message or MAIL_SETUP_NOTE
    if problem == PROBLEM_SCOPE:
        return f"Google did not allow sending email for {alias} - click Send to sign in again and tick that box"
    if problem == PROBLEM_EXPIRED:
        return f"The {alias} account's Google sign-in expired or was revoked - click Send to sign in again"
    if problem == PROBLEM_DENIED:
        return f"Google sign-in for the {alias} account was cancelled or access was denied - click Send to try again"
    if problem == PROBLEM_TIMEOUT:
        return (f"Google sign-in for the {alias} account was not finished in time - click Send to try again "
                "(if Google said \"Access blocked\", its administrator does not allow this app)")
    if problem == PROBLEM_IDENTITY:
        if message.startswith(("This is not", "That Google account", "Google did not say")):
            return f"{message} - click Send to sign in with the right account"
        return f"Jarvis doesn't know yet which Google account {alias} is - click Send to sign in and confirm it"
    if problem == PROBLEM_CONFIRM:
        return (f"Is From the right Google account for {alias}? Click Send to confirm it (or to use another "
                "account) - nothing is sent until you do")
    if problem == PROBLEM_FAILED and message:
        return f"{message} - click Send to try again"
    return f"Send signs in to Google for the {alias} account first - then check From and click Send again"


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
        if action.kind in ACCOUNT_KINDS:
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
        if action.kind in ACCOUNT_KINDS:
            alias = action.account
            account = self._accounts.get(alias)
            if account is None:
                return f'No account named "{alias}" in config.toml [accounts]'
            if account.backend == BACKEND_COMPOSIO:
                return COMPOSIO_NOTE
            if action.kind in MAIL_KINDS:
                if GMAIL_FEATURE not in account.features:
                    return (f"The {alias} account is not set up for sending email "
                            f"(config.toml [accounts.{alias}] features)")
            elif CALENDAR_FEATURE not in account.features:
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
        if action.kind not in EVENT_KINDS:
            raise ExecError(NO_EVENT_MESSAGE)
        return self._backend(action).peek(action)

    def mail_status(self, action: ProposedAction) -> MailStatus:
        """What a Reply / Email card shows about sending and what its Send click does (no network);
        see MailStatus. Any other card: a hand-off with Executor.readiness as the note."""
        reason = self.readiness(action)
        backend = self.backend_for(action) if action.sends_mail else None
        status = getattr(backend, "status", None)
        if status is None:
            alias = action.account
            return MailStatus(alias, "", f"From: {alias} ({UNCONFIRMED_FROM})", PROBLEM_SETUP,
                              reason or NOTHING_TO_DO, hand_off=True)
        return status(action, reason)

    def sign_in(self, action: ProposedAction, *, on_stage: OnStage = _no_stage) -> None:
        """The browser sign-in of the account that acts for ``action`` (only after a click on
        Sign in, or on a Send whose account is not ready). Raises ExecError with the problem."""
        reason = self.readiness(action)
        backend = self.backend_for(action)
        if reason or backend is None:
            raise ExecError(reason or NOTHING_TO_DO, problem=PROBLEM_SETUP)
        backend.sign_in(account_of(action), on_stage=on_stage)

    # ---- which Google account an alias is: the first sign-in's question ------------------------

    def _account_handles(self, alias: str) -> list[Any]:
        """The objects that act for ``alias`` (its Gmail sender and its calendar; in the app both
        share one google_auth.GoogleAccount)."""
        handles: list[Any] = []
        for backend in self._backends:
            for getter in ("sender", "calendar"):
                get = getattr(backend, getter, None)
                handle = get(alias) if callable(get) else None
                if handle is not None and all(handle is not other for other in handles):
                    handles.append(handle)
        return handles

    def pending_confirmation(self, alias: str) -> str:
        """The address of the Google account ``alias`` was bound to while you have not confirmed it
        ("" when there is nothing to confirm). Nothing is sent or changed for the alias meanwhile;
        reading its calendar goes on. Files only (no network)."""
        for handle in self._account_handles(alias):
            address = _call_text(handle, "pending_confirmation")
            if address:
                return address
        return ""

    def confirm_account(self, alias: str, address: str) -> bool:
        """You answered Yes to "Signed in as ``address`` for ``alias`` - is that right?" (kept in
        accounts.json). False when ``alias`` is not bound to that address (any more)."""
        confirmed = False
        for handle in self._account_handles(alias):
            confirm = getattr(handle, "confirm_account", None)
            if callable(confirm):
                try:
                    confirmed = bool(confirm(address)) or confirmed
                except Exception as exc:  # noqa: BLE001 - not confirmed: it is asked again
                    logger.warning("Could not confirm the %s account (%s)", logged_alias(alias), type(exc).__name__)
        return confirmed and not self.pending_confirmation(alias)

    def change_problem(self, alias: str) -> str:
        """Why nothing may be sent or changed for ``alias`` now although it may be signed in
        (google_auth PROBLEM_*; "" when it may): PROBLEM_CONFIRM (a binding you have not
        confirmed: ask), PROBLEM_IDENTITY (a saved sign-in Jarvis has no binding for: sign in
        again, which binds and asks), PROBLEM_SIGNED_OUT, or PROBLEM_FAILED when it could not be
        checked. Files only (no network)."""
        for handle in self._account_handles(alias):
            problem, _message = _change_problem(handle)
            if problem:
                return problem
        return ""

    def needs_confirmation(self, alias: str) -> bool:
        """``alias`` has no binding you confirmed, so its next sign-in leaves one to confirm before
        anything is changed (a Calendar event's or Todo block's Approve signs in before the
        countdown then). False for objects that do not say (test doubles)."""
        for handle in self._account_handles(alias):
            if callable(getattr(handle, "needs_confirmation", None)) and _call_bool(handle, "needs_confirmation"):
                return True
        return False

    def disconnect(self, alias: str) -> None:
        """You answered "No, use another account": forget ``alias``'s saved sign-in and which
        Google account it is (blocking: deletes files; run it on the action worker). ExecError
        (DISCONNECT_FAILED_MESSAGE) when the saved sign-in could not be deleted: the binding is
        kept, unconfirmed, and the sign-in must not start."""
        for handle in self._account_handles(alias):
            disconnect = getattr(handle, "disconnect", None)
            if callable(disconnect):
                try:
                    disconnect()
                except Exception as exc:  # noqa: BLE001 - google_auth logged it; the card says why
                    logger.warning("Could not disconnect the %s account (%s)", logged_alias(alias), type(exc).__name__)
                    raise ExecError(DISCONNECT_FAILED_MESSAGE.format(alias=alias), problem=PROBLEM_FAILED) from None

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
               writes_running: bool = False, on_stage: OnStage = _no_stage,
               proceed: Callable[[], bool] | None = None) -> RunOutcome:
    """Carry out one approved proposal, exactly once (never retried here or anywhere else).

    ``writes_running`` (any kind whose undo countdown ran out: a Calendar event, a Todo's
    block, an RSVP, Move, Cancel, Reply or Email): sign the account in first when needed (never
    for email: its card signs in before the countdown), save "running" (with kind and account)
    to ``store`` right before the call - nothing is sent when that could not be saved -, make
    the one call without any further sign-in, and save the result over "running": "created" /
    "exists" / "sent", "failed" (certainly not done) or "unknown" (it may have happened: the
    call was sent but no clear answer came back, or something unexpected broke after
    "running" was saved). The result is saved here, on the worker thread, so it is kept even
    when the app quits meanwhile; a crash leaves "running", which the next start turns into
    "unknown". ``proceed`` (the worker's "the app is not closing") is asked right after the
    sign-in, before "running" is saved: False means nothing is sent (failed, CLOSING_MESSAGE),
    so a browser sign-in that finishes after Quit starts no call.

    Without ``writes_running`` (a Calendar event or a Todo's block approved the older way, with
    no countdown) nothing is saved here: the caller saves the result (created / exists /
    failed). A Reply or Email is never sent that way (failed: nothing was sent). Never raises
    for an ExecError or a bug in a backend (a bug is logged by type only: its message could
    carry a token).
    """
    running = False
    result: ExecResult | None = None
    error: ExecError | None = None
    try:
        if action.kind in MAIL_KINDS and not writes_running:
            raise ExecError(COUNTDOWN_ONLY_MESSAGE)
        if writes_running:
            if store is None:
                raise ExecError(NOT_SAVED_MESSAGE)
            executor.prepare(action, on_stage=on_stage)
            if proceed is not None and not proceed():
                raise ExecError(CLOSING_MESSAGE)
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
    bindings = AccountBindings(config.data_dir / ACCOUNTS_FILE)   # read on use; nothing is written here
    accounts: dict[str, GoogleAccount] = {}
    for alias in google_aliases(config):
        accounts[alias] = GoogleAccount(
            alias, client_secret_path=config.calendar.client_secret_path, data_dir=config.data_dir,
            features=account_features(config, alias), flow_factory=flow_factory, refresh=refresh,
            bindings=bindings)
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


def build_senders(config: Config, accounts: Mapping[str, GoogleAccount] | None = None, *,
                  service_factory: Callable[[Any], Any] | None = None) -> dict[str, GmailSender]:
    """One GmailSender per alias with the gmail_send feature, sharing the account (and token) of
    its calendar. ``accounts`` defaults to build_accounts(config); pass the same mapping as to
    build_calendars so each alias has one sign-in."""
    if accounts is None:
        accounts = build_accounts(config)
    return {alias: GmailSender(account, service_factory=service_factory)
            for alias, account in accounts.items() if GMAIL_FEATURE in account.features}


def recipient_history(config: Config) -> RecipientHistory:
    """The addresses Jarvis sent to before (%LOCALAPPDATA%\\briefing-reader\\recipients.json)."""
    return RecipientHistory(config.data_dir / RECIPIENTS_FILE)


def build_executor(config: Config, calendars: Mapping[str, Any], senders: Mapping[str, Any] | None = None, *,
                   history: RecipientHistory | None = None,
                   now: Callable[[], datetime] | None = None) -> Executor:
    """The Executor of the app: Google Calendar for ``calendars`` and Gmail for ``senders``,
    ``[actions] trusted_domains`` and the recipient history (default: recipient_history(config))."""
    if history is None:
        history = recipient_history(config)
    backends: list[Any] = [CalendarBackend(calendars, now=now)]
    backends.append(GmailBackend(senders or {}, history=history, trusted_domains=config.actions.trusted_domains))
    return Executor(backends, config.accounts)


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


def sign_in_note(alias: str, *, signed_in: bool = False, problem: str = "", message: str = "",
                 sends_mail: bool = False) -> str:
    """What a card of ``alias`` says while that account cannot be used ("" when it can).

    ``problem`` / ``message`` are the account's last sign-in problem (GoogleCalendar
    .sign_in_problem()); the messages are google_auth's, which never name an address.
    ``sends_mail``: the account also has the gmail_send feature, so every sign-in asks for
    sending email too; a block then says how to keep Calendar actions without it.
    """
    if signed_in:
        return ""
    if problem == PROBLEM_EXPIRED:
        return f"The {alias} account's Google sign-in expired or was revoked - click Sign in to sign in again"
    if problem == PROBLEM_BLOCKED:
        note = message or (f"Google blocked the sign-in: the {alias} account's administrator does not "
                           "allow this app")
        if sends_mail:
            note += (f". Every {alias} sign-in also asks to send email; if the administrator only blocks "
                     f"that, {keep_calendar_hint(alias)} to keep Calendar actions")
        return note
    if problem == PROBLEM_DENIED:
        return f"Google sign-in for the {alias} account was cancelled or access was denied - click Sign in to try again"
    if problem == PROBLEM_SCOPE:
        return f"Google did not allow Calendar access for the {alias} account - click Sign in and tick every box"
    if problem == PROBLEM_TIMEOUT:
        return message or f"Google sign-in for the {alias} account was not finished in time - click Sign in to try again"
    if problem == PROBLEM_SETUP:
        return message or CALENDAR_SETUP_NOTE
    if problem == PROBLEM_IDENTITY:
        return (f"{message} - click Sign in" if message
                else f"This is not the Google account set up as the {alias} account - click Sign in with that one")
    if problem == PROBLEM_FAILED and message:
        return f"{message} - click Sign in to try again"
    return f"Not signed in to the {alias} account - click Sign in"
