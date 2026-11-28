"""Carrying out an approved card for an Outlook / Microsoft account (``backend = "microsoft"``): the executor's two
backends for it, beside the Google ones (executor.CalendarBackend, executor.GmailBackend), with the same protocol and
the same rules.

    OutlookCalendarBackend(calendars)    outlook.OutlookCalendar per alias: a Calendar card that names an Outlook
                                         account (add the event), an RSVP, Move or Cancel (its acct=), each with its
                                         Undo where Google's has one (an added event, an answer, a move)
    OutlookMailBackend(senders, ...)     outlook.OutlookSender per alias: a Reply (Graph's reply to the card's
                                         gmid=, the Outlook message id) or an Email, after the recipient check

Nothing here opens the browser except ``sign_in`` (the owner's click on a card's Sign in or Send). A change is made
only from executor.run_action / run_undo after the owner's click (a Cancel, Reply or Email at the end of its hidden
hold), only while the account's sign-in grants it (ms_auth write_problem: an account signed in with the first,
read-only permissions says "Sign in again to let Jarvis change this account" and its card offers Sign in), and it
is one request, never retried. A Reply checks right before it goes that the message it answers is still in the
mailbox and in the card's conversation. The recipients are checked exactly as for Gmail (recipients.review: OWN
refused, NEW ticked on the card first; gmail.build_message's text checks), once more against the account's own
address right before the send, and remembered after it (recipients.json).

Outlook's own rules, which the card and its LIVE payload say: a move or a cancellation is sent by Outlook to every
guest (it has no quiet change), so a card whose "who gets an email" is not everyone is refused for an event that has
guests; an invitation answer tells the organizer unless the card says nobody (Graph's sendResponse); a reply's
subject is Outlook's own "RE: ..." and the card's text is its whole body (no quoted message below it, as with Gmail);
a repeating event can't be added yet. Only the account's own calendar is changed (cal= must be "primary"). A card
that Ask or a standing agent or rule proposed never acts for an Outlook account (GOOGLE_ONLY_NOTE): they read and
check Google accounts only.

Logs name only action ids, kinds, aliases ("work", "personal", else "other"), counts and statuses. Qt-free.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from . import executor as _x
from .actions import (
    CALENDAR,
    CANCEL,
    EVENT_KINDS,
    MAIL_KINDS,
    MOVE,
    REPLY,
    RSVP,
    SOURCE_ASK,
    SOURCE_STANDING,
    STATUS_CREATED,
    STATUS_EXISTS,
    STATUS_SENT,
    ProposedAction,
    mail_problem,
    sent_text,
)
from .config import BACKEND_MICROSOFT
from .gcal import (
    EVENT_FOOTER,
    NO_TITLE,
    CalendarError,
    EventChanged,
    EventDetails,
    event_footer,
)
from .gmail import GmailError, GmailUnknownOutcome, OutgoingMail, mail_view
from .google_auth import (
    PROBLEM_BLOCKED,
    PROBLEM_FAILED,
    PROBLEM_IDENTITY,
    PROBLEM_SCOPE,
    PROBLEM_SETUP,
    PROBLEM_SIGNED_OUT,
    AccountError,
    logged_alias,
)
from .live import NO_STEP, NO_TASK, STATUS_OK, Field, LiveStep, LiveTask, Payload, quiet
from .recipients import OWN_RECIPIENT_MESSAGE, RecipientHistory, RecipientReview, own_recipients, review

logger = logging.getLogger(__name__)

OUTLOOK_CALENDAR_SETUP_NOTE = ("The {alias} Outlook account's calendar is not set up here (config.toml "
                               "[accounts.{alias}] features, README step 8e)")
OUTLOOK_MAIL_SETUP_NOTE = "Sending from the {alias} Outlook account is not set up yet - see README step 8e"
OTHER_CALENDAR_NOTE = "Jarvis changes only the {alias} Outlook account's own calendar - open this one in Outlook"
NO_MESSAGE_ID_NOTE = "This reply names no Outlook message (gmid=), so Jarvis can't send it - use {tools} instead"
NOT_SIGNED_IN_TEXT = "Not signed in to the {alias} Outlook account; nothing was changed - click Sign in"
# Ask and the standing agents and rules read and check Google accounts only: their cards never act for an Outlook one.
GOOGLE_ONLY_NOTE = {SOURCE_ASK: "Ask Jarvis works with Google accounts only, so it changes nothing for the {alias} "
                                "Outlook account - use Copy or Open",
                    SOURCE_STANDING: "Standing agents and rules work with Google accounts only, so they change "
                                     "nothing for the {alias} Outlook account - use Copy or Open"}
NOTIFY_WORDS = {
    RSVP: {"none": "nobody (the organizer is not told)", "": "the organizer"},
    MOVE: {"all": "every guest (Outlook sends the change to each)",
           "": "every guest - Outlook always tells them, so this is refused for an event with guests (Edit: everyone)"},
    CANCEL: {"all": "every guest (Outlook sends each the cancellation, with your note)",
             "": "every guest - Outlook always tells them, so this is refused for an event with guests (Edit: "
                 "everyone)"},
}
_CALLS = {
    CALENDAR: ("Outlook: add the event", "POST /me/events (after looking for the same event)"),
    RSVP: ("Outlook: answer the invitation", "POST /me/events/{id}/accept | decline | tentativelyAccept"),
    MOVE: ("Outlook: move the event", "PATCH /me/events/{id} (start, end)"),
    CANCEL: ("Outlook: cancel the event", "POST /me/events/{id}/cancel (guests), else DELETE /me/events/{id}"),
}
_DONE_WORDS = {CALENDAR: "ADDED", RSVP: "ANSWERED", MOVE: "MOVED", CANCEL: "CANCELLED EVENT"}
_ALREADY_WORDS = {CALENDAR: "ALREADY THERE", RSVP: "ALREADY ANSWERED", MOVE: "ALREADY AT THAT TIME",
                  CANCEL: "ALREADY GONE"}


def _notify(action: ProposedAction) -> str:
    return action.field("notify", "all") or "all"


def _alias(action: ProposedAction) -> str:
    return _x.account_of(action)


def _write_problem(handle: Any) -> tuple[str, str]:
    """The handle's write_problem() (files only); a check that breaks refuses (PROBLEM_FAILED)."""
    try:
        problem, message = handle.write_problem() if hasattr(handle, "write_problem") else handle.ready()
    except Exception as exc:  # noqa: BLE001 - refused: nothing is changed without a known state
        logger.debug("Could not check the Outlook sign-in (%s)", type(exc).__name__)
        return PROBLEM_FAILED, "Could not check the Outlook sign-in; nothing was changed"
    return str(problem or ""), str(message or "")


def _sign_in_error(exc: Exception) -> _x.ExecError:
    if isinstance(exc, CalendarError):
        return _x.ExecError(str(exc) or type(exc).__name__, problem=exc.problem or PROBLEM_FAILED, status=exc.status)
    if isinstance(exc, AccountError):
        return _x.ExecError(str(exc) or type(exc).__name__, problem=exc.problem or PROBLEM_FAILED, status=exc.status)
    return _x.ExecError(f"unexpected error ({type(exc).__name__})", problem=PROBLEM_FAILED)


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------

class OutlookCalendarBackend:
    """Outlook calendars for every alias in ``calendars`` (alias -> outlook.OutlookCalendar or a fake)."""

    name = BACKEND_MICROSOFT
    KINDS = frozenset({CALENDAR, RSVP, MOVE, CANCEL})   # a Todo's block always goes to the personal Google calendar

    def __init__(self, calendars: Mapping[str, Any], *, now: Any = None) -> None:
        self._calendars = dict(calendars)
        self._now = now or (lambda: datetime.now().astimezone())

    def handles(self, action: ProposedAction) -> bool:
        return action.kind in self.KINDS

    def calendar(self, alias: str) -> Any | None:
        return self._calendars.get(alias)

    def aliases(self) -> list[str]:
        return list(self._calendars)

    def readiness(self, action: ProposedAction) -> str:
        """"" when the account's calendar is here and the card fits Outlook (no network)."""
        alias = _alias(action)
        if action.source in GOOGLE_ONLY_NOTE:
            return GOOGLE_ONLY_NOTE[action.source].format(alias=alias)
        calendar = self._calendars.get(alias)
        if calendar is None or not _x._call_bool(calendar, "is_configured"):
            return OUTLOOK_CALENDAR_SETUP_NOTE.format(alias=alias)
        if action.kind in EVENT_KINDS and action.field("cal", "primary") != "primary":
            return OTHER_CALENDAR_NOTE.format(alias=alias)
        if action.kind == CALENDAR and action.rrule:
            return "Repeating events can't be added to an Outlook account from here yet - add it in Outlook"
        return ""

    def write_problem(self, alias: str) -> tuple[str, str]:
        calendar = self._calendars.get(alias)
        if calendar is None:
            return PROBLEM_SETUP, OUTLOOK_CALENDAR_SETUP_NOTE.format(alias=alias)
        return _write_problem(calendar)

    def signed_in(self, action: ProposedAction) -> bool:
        """It may be changed now without any sign-in (signed in with the permission to change it)."""
        return not self.write_problem(_alias(action))[0]

    def prepare(self, action: ProposedAction, *, on_stage: Any = _x._no_stage) -> None:
        """Never opens the browser (the card's Sign in does): ExecError with the account's problem."""
        alias = _alias(action)
        problem, message = self.write_problem(alias)
        if problem == PROBLEM_SIGNED_OUT:
            raise _x.ExecError(NOT_SIGNED_IN_TEXT.format(alias=alias), problem=problem)
        if problem:
            raise _x.ExecError(message, problem=problem)

    def execute(self, action: ProposedAction, *, on_stage: Any = _x._no_stage, interactive: bool = True,
                live: LiveTask | None = None) -> _x.ExecResult:
        """The one change (a missing sign-in or permission is a failure: nothing was changed)."""
        self.prepare(action)
        calendar = self._calendars[_alias(action)]
        on_stage(_x.STAGE_WORKING)
        task = live or NO_TASK
        payload = _x._show_payload(task, self._preview_quietly(action) if live is not None else None)
        call = _x._call_step(task, *_CALLS.get(action.kind, ("Outlook", "")))
        hook = _request_hook(payload, call) if live is not None and call is not NO_STEP else None
        field = action.field
        try:
            if action.kind == CALENDAR:
                created = calendar.create_event(action, footer=event_footer(action), on_request=hook)
                undo = None
                if not created.existed and created.event_id and created.stamp:
                    undo = _x.UndoRecord(CALENDAR, _alias(action), created.event_id, created.stamp,
                                         send_updates="none", calendar_id="outlook")
                result = _x.ExecResult(STATUS_EXISTS if created.existed else STATUS_CREATED, created.link,
                                       ref=created.event_id, undo=undo)
                _done(call, payload, action, result, already=created.existed)
                return result
            if action.kind == RSVP:
                send = _notify(action) != "none"
                change = calendar.respond(field("event"), field("answer"), comment=action.body, send_response=send,
                                          on_request=hook)
            elif action.kind == MOVE:
                if action.start is None or action.end is None:
                    raise _x.ExecError("This move has no new time")
                change = calendar.move(field("event"), action.start, action.end,
                                       tell_guests=_notify(action) == "all", now=self._now(), on_request=hook)
            else:
                change = calendar.cancel(field("event"), comment=action.body, tell_guests=_notify(action) == "all",
                                         on_request=hook)
        except CalendarError as exc:
            _x._call_failed(call, payload, str(exc), status=exc.status,
                            unknown=isinstance(exc, _x.CalendarUnknownOutcome))
            raise _x._exec_error(exc) from None
        result = _x.ExecResult(STATUS_SENT, change.link, sent_text(action, already=change.already),
                               ref=change.event_id or field("event"), undo=_change_undo(action, change))
        _done(call, payload, action, result, already=change.already)
        return result

    def undo(self, record: _x.UndoRecord) -> _x.ExecResult:
        """Reverse exactly Jarvis's own change (never signs in); UndoRefused when the event is gone or changed."""
        calendar = self._calendars.get(record.alias)
        if calendar is None:
            raise _x.ExecError(OUTLOOK_CALENDAR_SETUP_NOTE.format(alias=record.alias), problem=PROBLEM_SETUP)
        problem, message = _write_problem(calendar)
        if problem:
            raise _x.ExecError(message or NOT_SIGNED_IN_TEXT.format(alias=record.alias), problem=problem)
        try:
            if record.kind == CALENDAR:
                change = calendar.undo_create(record.event_id, stamp=record.stamp, uncertain=record.uncertain)
            elif record.kind == RSVP:
                change = calendar.undo_respond(record.event_id, record.response, stamp=record.stamp,
                                               send_response=record.send_updates != "none",
                                               uncertain=record.uncertain)
            elif record.kind == MOVE and record.start is not None and record.end is not None:
                change = calendar.undo_move(record.event_id, record.start, record.end, stamp=record.stamp,
                                            uncertain=record.uncertain)
            else:
                raise _x.ExecError(_x.NO_UNDO_MESSAGE)
        except EventChanged:
            raise _x.UndoRefused() from None
        except CalendarError as exc:
            raise _x._exec_error(exc) from None
        return _x.ExecResult(_x.UNDONE, _x._text(getattr(change, "link", "")), ref=record.event_id)

    def preview(self, action: ProposedAction) -> Payload:
        """What execute() changes, in words (no network): Outlook's own rules named (who gets an email)."""
        field = action.field
        today = self._now().date()
        alias = _alias(action)
        if action.kind == CALENDAR:
            fields = [Field("Title", action.title or NO_TITLE),
                      Field("When", f"{_x._when_words(action, today)}, in this PC's time zone")]
            if action.where.strip():
                fields.append(Field("Where", action.where.strip()))
            fields += [Field("Guests", "none - nobody is invited or told"), Field("Calendar", f"{alias} (Outlook)")]
            notes = action.notes.strip()
            footer = event_footer(action) or EVENT_FOOTER
            return Payload("Will add exactly this", tuple(fields), "Description",
                           f"{notes}\n\n{footer}" if notes else footer, start_open=False)
        if action.kind not in EVENT_KINDS:
            raise _x.ExecError(_x.NOTHING_TO_DO)
        fields = [Field("Event", field("title") or action.title or NO_TITLE)]
        if action.kind == RSVP:
            fields.append(Field("Answer", _x._ANSWER_WORDS.get(field("answer"), field("answer"))))
            fields.append(Field("Note to the organizer", action.body or "none"))
        elif action.kind == MOVE:
            if action.start is None or action.end is None:
                raise _x.ExecError("This move has no new time")
            fields.append(Field("New time", f"{_x._when_words(action, today)}, in this PC's time zone"))
        else:
            fields.append(Field("Change", "cancel the event"))
            fields.append(Field("Note to the guests", action.body or "none"))
        words = NOTIFY_WORDS[action.kind]
        fields.append(Field("Who gets an email", words.get(_notify(action), words[""])))
        fields += [Field("Event id", field("event"), mono=True), Field("Calendar", f"{alias} (Outlook)")]
        return Payload("Will change exactly this", tuple(fields))

    def _preview_quietly(self, action: ProposedAction) -> Payload | None:
        try:
            return self.preview(action)
        except Exception as exc:  # noqa: BLE001 - only what the LIVE view shows
            logger.debug("Live view: preview failed (%s)", type(exc).__name__)
            return None

    def peek(self, action: ProposedAction) -> EventDetails:
        """Outlook's own view of the card's event (never signs in)."""
        if action.kind not in EVENT_KINDS:
            raise _x.ExecError(_x.NO_EVENT_MESSAGE)
        calendar = self._calendars.get(_alias(action))
        if calendar is None:
            raise _x.ExecError(OUTLOOK_CALENDAR_SETUP_NOTE.format(alias=_alias(action)), problem=PROBLEM_SETUP)
        return calendar.get_event(action.field("event"))

    def sign_in(self, alias: str, *, on_stage: Any = _x._no_stage) -> None:
        """The browser sign-in of ``alias`` (a click on its card's Sign in)."""
        calendar = self._calendars.get(alias)
        if calendar is None:
            raise _x.ExecError(OUTLOOK_CALENDAR_SETUP_NOTE.format(alias=alias), problem=PROBLEM_SETUP)
        on_stage(_x.STAGE_SIGNIN)
        try:
            calendar.sign_in()
        except Exception as exc:  # noqa: BLE001 - every failure is a safe message (outlook / ms_auth)
            raise _sign_in_error(exc) from None
        on_stage(_x.STAGE_SIGNED_IN)


def _change_undo(action: ProposedAction, change: Any) -> _x.UndoRecord | None:
    """The Undo of an answer or a move (None for a Cancel, when nothing changed, or when the stamp or the earlier
    state is unknown; an invitation that was never answered has none: Outlook can't go back to "not answered")."""
    before = getattr(change, "before", None)
    stamp = _x._text(getattr(change, "stamp", ""))
    if action.kind not in (RSVP, MOVE) or getattr(change, "already", True) or before is None or not stamp:
        return None
    alias = _alias(action)
    event_id = _x._text(getattr(change, "event_id", "")) or action.field("event")
    if action.kind == RSVP:
        response = _x._text(getattr(before, "self_response", ""))
        if response not in ("accepted", "declined", "tentative"):
            return None
        return _x.UndoRecord(RSVP, alias, event_id, stamp, send_updates="none" if _notify(action) == "none" else "all",
                             calendar_id="outlook", response=response)
    start, end = getattr(before, "start", None), getattr(before, "end", None)
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return None
    return _x.UndoRecord(MOVE, alias, event_id, stamp, send_updates="all", calendar_id="outlook", start=start,
                         end=end, time_zone=_x._text(getattr(before, "time_zone", "")))


def _request_hook(payload: LiveStep, call: LiveStep) -> Any:
    """outlook's on_request: the exact Graph request, right before it goes (the worker thread)."""

    def heard(method: str, path: str, body: Any) -> None:
        _show_request(payload, call, method, path, body)

    return heard


@quiet()
def _show_request(payload: LiveStep, call: LiveStep, method: str, path: str, body: Any) -> None:
    import json

    call.field("Request", f"{method} {path}", mono=True)
    if payload is not NO_STEP and isinstance(body, dict):
        payload.block("Exact request to Outlook", json.dumps(body, indent=2, ensure_ascii=False))


@quiet()
def _done(call: LiveStep, payload: LiveStep, action: ProposedAction, result: _x.ExecResult, *, already: bool) -> None:
    if call is NO_STEP:
        return
    if result.link:
        call.field("Event", "Open the event in Outlook", link=result.link)
    if already:
        call.field("Already that way", "yes - nothing changed")
        call.done(STATUS_OK, status_text=_ALREADY_WORDS.get(action.kind, "ALREADY SO"), summary=result.result_text)
        if payload is not NO_STEP:
            payload.done(STATUS_OK, status_text="NOTHING SENT", summary="Outlook already had this, so nothing was "
                                                                        "changed")
        return
    call.done(STATUS_OK, status_text=_DONE_WORDS.get(action.kind, "DONE"), summary=result.result_text)
    _x._payload_done(payload, "ADDED" if action.kind == CALENDAR else "CHANGED")


# --------------------------------------------------------------------------
# Mail
# --------------------------------------------------------------------------

def outlook_mail_note(alias: str, problem: str, message: str = "", *, tools: str = "Copy and Open") -> str:
    """What an Outlook Reply / Email card says while it can't send (no address in it)."""
    if problem == PROBLEM_SETUP:
        return message or OUTLOOK_MAIL_SETUP_NOTE.format(alias=alias)
    if problem == PROBLEM_BLOCKED:
        return f"{message or 'The account administrator does not allow this app'} - use {tools} instead"
    if problem == PROBLEM_SIGNED_OUT:
        return f"Send signs in to Outlook for the {alias} account first - then check From and click Send again"
    if problem == PROBLEM_SCOPE:
        return f"{message} - click Send to sign in, then check From and click Send again"
    if message:
        return f"{message} - click Send to try again"
    return f"Send signs in to Outlook for the {alias} account first - then check From and click Send again"


class OutlookMailBackend:
    """Outlook for every alias in ``senders`` (alias -> outlook.OutlookSender or a fake): a Reply or an Email from
    the line's ``acct=``. A message goes out only from ``execute(..., interactive=False)`` (run_action's "running"
    path at the end of Send's hidden hold), as executor.GmailBackend's does."""

    name = BACKEND_MICROSOFT
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
        """"" when the account's sending is here, the card names its Outlook message (a Reply) and its text is
        sendable; no network."""
        if action.source in GOOGLE_ONLY_NOTE:
            return GOOGLE_ONLY_NOTE[action.source].format(alias=action.account)
        sender = self._senders.get(action.account)
        if sender is None or not _x._call_bool(sender, "is_configured"):
            return OUTLOOK_MAIL_SETUP_NOTE.format(alias=action.account)
        if action.kind == REPLY and not action.field("gmid"):
            return NO_MESSAGE_ID_NOTE.format(tools=_x.hand_off_tools(action))
        return mail_problem(action)

    def signed_in(self, action: ProposedAction) -> bool:
        sender = self._senders.get(action.account)
        return sender is not None and not _write_problem(sender)[0]

    def review(self, action: ProposedAction) -> RecipientReview:
        sender = self._senders.get(action.account)
        own = _x._call_text(sender, "from_address") if sender is not None else ""
        return review(action.recipients(), action.cc(), own=own, account=action.account, confirmed=action.confirmed,
                      trusted_domains=self._trusted, history=self._history, unverified=action.unverified)

    def status(self, action: ProposedAction, reason: str = "") -> _x.MailStatus:
        """See executor.MailStatus; ``reason`` is the Executor's readiness answer (not set up: a hand-off)."""
        alias = action.account
        sender = self._senders.get(alias)
        address = _x._call_text(sender, "from_address") if sender is not None else ""
        from_text = f"From: {alias} ({address or _x.UNCONFIRMED_FROM})"
        recipients = self.review(action)
        if reason:
            return _x.MailStatus(alias, address, from_text, PROBLEM_SETUP, reason, hand_off=True, review=recipients)
        problem, message = _write_problem(sender) if sender is not None else (PROBLEM_SETUP, "")
        tools = _x.hand_off_tools(action)
        if problem in (PROBLEM_SETUP, PROBLEM_BLOCKED):
            return _x.MailStatus(alias, address, from_text, problem, outlook_mail_note(alias, problem, message,
                                                                                       tools=tools),
                                 hand_off=True, review=recipients)
        if problem:
            return _x.MailStatus(alias, address, from_text, problem,
                                 outlook_mail_note(alias, problem, message, tools=tools), sign_in=True,
                                 review=recipients)
        note = recipients.problem or (_x.NEW_RECIPIENT_NOTE if recipients.unconfirmed else "")
        return _x.MailStatus(alias, address, from_text, "", note, review=recipients)

    def prepare(self, action: ProposedAction, *, on_stage: Any = _x._no_stage) -> None:
        """Check that the message may go out now. Never opens the browser."""
        self._message(action)

    def execute(self, action: ProposedAction, *, on_stage: Any = _x._no_stage, interactive: bool = True,
                live: LiveTask | None = None) -> _x.ExecResult:
        """The one send (run_action's "running" path only: ``interactive`` must be False)."""
        if interactive:
            raise _x.ExecError(_x.HOLD_ONLY_MESSAGE)
        mail = self._message(action)
        sender = self._senders[action.account]
        if not mail.from_addr:
            raise _x.ExecError(outlook_mail_note(action.account, PROBLEM_SCOPE, "Jarvis doesn't know this account's "
                                                 "address"), problem=PROBLEM_IDENTITY)
        if (own_recipients(mail.to + mail.cc, mail.from_addr)
                or own_recipients(mail.to + mail.cc, _x._call_text(sender, "from_address"))):
            logger.info("Action %s (%s, %s) not sent: a recipient is the sending account itself", action.id,
                        action.kind, logged_alias(action.account))
            raise _x.ExecError(OWN_RECIPIENT_MESSAGE.format(alias=action.account))
        on_stage(_x.STAGE_WORKING)
        task = live or NO_TASK
        payload = _x._show_payload(task, self._payload(action, mail)) if live is not None else NO_STEP
        reply = action.kind == REPLY
        call = _x._call_step(task, "Outlook: send the reply (one call, never retried)" if reply
                             else "Outlook: send the email (one call, never retried)",
                             "POST /me/messages/{id}/reply" if reply else "POST /me/sendMail")
        hook = _request_hook(payload, call) if live is not None and call is not NO_STEP else None
        logger.info("Sending action %s (%s, %s) to %d recipient(s) through Outlook", action.id, action.kind,
                    logged_alias(action.account), mail.recipient_count)
        try:
            if reply:
                sender.check_message(action.field("gmid"), conversation=action.field("thread"),
                                     internet_id=action.field("msgid"))
                sender.reply(action.field("gmid"), mail, on_request=hook)
            else:
                sender.send(mail, on_request=hook)
        except GmailUnknownOutcome as exc:
            _x._call_failed(call, payload, str(exc), status=exc.status, unknown=True)
            raise _x.ExecError(str(exc), outcome=_x.OUTCOME_UNKNOWN, problem=exc.problem, status=exc.status) from None
        except GmailError as exc:
            _x._call_failed(call, payload, str(exc), status=exc.status, unknown=False)
            raise _x.ExecError(str(exc), problem=exc.problem, status=exc.status) from None
        remembered = 0
        if self._history is not None:
            try:
                self._history.add(mail.to + mail.cc)
                remembered = mail.recipient_count
            except Exception as exc:  # noqa: BLE001 - the message went out; only the memory failed
                logger.warning("Could not remember the recipients of action %s (%s)", action.id, type(exc).__name__)
        result = _x.ExecResult(STATUS_SENT, "", sent_text(action))
        _mail_sent(call, payload, remembered)
        return result

    def preview(self, action: ProposedAction) -> Payload:
        """The message execute() would send, exactly (no network, never a sign-in)."""
        return self._payload(action, self._message(action))

    def _payload(self, action: ProposedAction, mail: OutgoingMail) -> Payload:
        try:
            view = mail_view(mail)
        except GmailError as exc:
            raise _x.ExecError(str(exc) or type(exc).__name__, problem=exc.problem) from None
        fields = [Field("From", view.from_addr, mono=True), Field("To", view.to), Field("Cc", view.cc or "none")]
        if action.kind == REPLY:
            fields += [Field("Subject", f"{view.subject} (Outlook writes its own RE: line)"),
                       Field("Outlook message", action.field("gmid"), mono=True),
                       Field("Below the text", "nothing - the text is the whole message")]
        else:
            fields.append(Field("Subject", view.subject))
        return Payload("Will send exactly this", tuple(fields), "Message", view.body)

    def peek(self, action: ProposedAction) -> EventDetails:
        raise _x.ExecError(_x.NO_EVENT_MESSAGE)

    def sign_in(self, alias: str, *, on_stage: Any = _x._no_stage) -> None:
        """The browser sign-in of ``alias`` for sending (a click on the card's Send or Sign in)."""
        sender = self._senders.get(alias)
        if sender is None:
            raise _x.ExecError(OUTLOOK_MAIL_SETUP_NOTE.format(alias=alias), problem=PROBLEM_SETUP)
        on_stage(_x.STAGE_SIGNIN)
        try:
            sender.sign_in()
        except Exception as exc:  # noqa: BLE001 - every failure is a safe message (ms_auth)
            raise _sign_in_error(exc) from None
        on_stage(_x.STAGE_SIGNED_IN)

    def _message(self, action: ProposedAction) -> OutgoingMail:
        """The message exactly as the card shows it, checked like Gmail's (gmail.build_message), or ExecError
        (failed: nothing was sent)."""
        sender = self._senders.get(action.account)
        if sender is None:
            raise _x.ExecError(OUTLOOK_MAIL_SETUP_NOTE.format(alias=action.account), problem=PROBLEM_SETUP)
        problem = mail_problem(action)
        if problem:
            raise _x.ExecError(problem)
        state, message = _write_problem(sender)
        if state:
            raise _x.ExecError(outlook_mail_note(action.account, state, message), problem=state)
        recipients = self.review(action)
        if recipients.own:
            raise _x.ExecError(OWN_RECIPIENT_MESSAGE.format(alias=action.account))
        if recipients.problem:
            raise _x.ExecError(f"{recipients.problem}; nothing was sent")
        if recipients.unconfirmed:
            raise _x.ExecError(_x.NEW_RECIPIENTS_MESSAGE)
        from_addr = _x._call_text(sender, "from_address")
        if not from_addr:
            raise _x.ExecError(outlook_mail_note(action.account, PROBLEM_SCOPE, "Jarvis doesn't know this account's "
                                                 "address"), problem=PROBLEM_IDENTITY)
        subject = action.field("subject") or action.title
        if action.kind == REPLY and not subject.casefold().startswith("re:"):
            subject = f"Re: {subject}"
        mail = OutgoingMail(account=action.account, from_addr=from_addr, to=recipients.to, cc=recipients.cc,
                            subject=subject, body=action.body)
        try:
            mail_view(mail)   # Gmail's checks of the recipients, the subject and the text
        except GmailError as exc:
            raise _x.ExecError(f"{exc}; nothing was sent" if str(exc) else "The message can't be sent",
                               problem=exc.problem) from None
        return mail


@quiet()
def _mail_sent(call: LiveStep, payload: LiveStep, remembered: int) -> None:
    if call is NO_STEP:
        return
    call.field("Recipients remembered", str(remembered))
    call.done(STATUS_OK, status_text="SENT")
    _x._payload_done(payload, "SENT EXACTLY THIS")


def build_outlook_senders(accounts: Mapping[str, Any]) -> dict[str, Any]:
    """One outlook.OutlookSender per Outlook account (every one may send: its sign-in asks for Mail.Send)."""
    from .outlook import OutlookSender

    return {alias: OutlookSender(account) for alias, account in accounts.items()}


__all__ = ["OutlookCalendarBackend", "OutlookMailBackend", "build_outlook_senders", "outlook_mail_note"]

