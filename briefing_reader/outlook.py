"""An Outlook / Microsoft account through Microsoft Graph: reading its calendar and its mail's headers, and the
changes the owner approves on a card (an event added, moved, answered or cancelled; a reply or an email sent).

The readers and writers have the shape of the Google ones, so the app needs few changes for an account with
``backend = "microsoft"`` (ms_auth.MicrosoftAccount holds its sign-in):

    OutlookCalendar(account)         like gcal.GoogleCalendar for reading: alias, calendar_id, is_configured(),
                                     is_signed_in(), sign_in_problem(), list_events(start, end) -> gcal.CalendarEvent
                                     (GET /me/calendarView, asked in UTC and turned into local, aware times as gcal's
                                     are; all-day events as dates with an inclusive last day). Never signs in: without
                                     a saved sign-in gcal.CalendarNotSignedIn before any request.
    OutlookMailReader(account)       like gmail.GmailReader's header reads: available(), recent(days=, max_results=),
                                     unread(days=, max_results=) (the canvas Now group's unread frame; read with the
                                     agenda, kept in memory only) and message_metadata(id) -> the dict Gmail's users.messages.get(format=
                                     "metadata") gives (id, threadId, labelIds, internalDate, payload.headers with From,
                                     To, Cc, Reply-To, Subject, Date, Message-ID) plus "snippet" (Graph's bodyPreview).
                                     GET /me/mailFolders/inbox/messages with a $select: never a body or attachment.
                                     Errors are gmail.MailReadError with the google_auth PROBLEM_*.

Writes (only ever called by outlook_actions' backends, from executor.run_action / run_undo after the owner's click):

    OutlookCalendar.get_event(id)    -> gcal.EventDetails (GET /me/events/{id}; the card's check line, service "Outlook")
    .create_event(action, ...)       POST /me/events (after looking for the same event with calendarView: the
                                     same title at the same start is "already there", nothing is added)
    .respond(id, answer, ...)        POST /me/events/{id}/accept | decline | tentativelyAccept (sendResponse)
    .move(id, start, end, ...)       PATCH /me/events/{id} (start, end; Outlook sends the change to every guest)
    .cancel(id, ...)                 POST /me/events/{id}/cancel when it has guests (they get the cancellation and
                                     the note), else DELETE /me/events/{id}
    .undo_create / undo_respond / undo_move   reverse exactly that change unless the event is gone or changed
                                     since (its changeKey): gcal.EventChanged
    OutlookSender(account)           like gmail.GmailSender: ready() (ms_auth write_problem), from_address(),
                                     check_message(id, ...) (GET: the message a reply answers is still there),
                                     reply(id, mail) (POST /me/messages/{id}/reply with the card's recipients and
                                     text as the whole body) and send(mail) (POST /me/sendMail)

A change is one request, never retried (not even after a 401): 2xx is done; 5xx or a lost answer after the request
went out is CalendarUnknownOutcome / gmail.GmailUnknownOutcome (it may have happened); every other answer is a plain
failure (nothing changed). Every call is blocking: callers use a worker thread. A read's 401 is answered with one
refresh of the token and one more try; a second 401 deletes the sign-in (connect again). A read's 403 keeps the
sign-in and turns that read off until the next sign-in. Logs carry the alias ("work", "personal", else "other"),
counts, HTTP statuses and the request's kind only: never a title, a subject, an address, an id, a token or a URL's
query. Qt-free.
"""

from __future__ import annotations

import email.utils
import json
import logging
import re
import threading
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .gcal import (
    DEFAULT_EVENT_MINUTES,
    EVENT_FOOTER,
    NO_TITLE,
    CalendarAuthError,
    CalendarError,
    CalendarEvent,
    CalendarNotSignedIn,
    CalendarSetupError,
    CalendarUnknownOutcome,
    ChangeResult,
    EventChanged,
    EventDetails,
    EventGone,
    EventResult,
    NotAllowed,
    build_event_body,
    local_timezone,
    zone_offset,
)
from .gmail import GmailError, GmailUnknownOutcome, MailReadError, OutgoingMail, SentMail
from .google_auth import (
    PROBLEM_EXPIRED,
    PROBLEM_FAILED,
    PROBLEM_SCOPE,
    PROBLEM_SETUP,
    PROBLEM_SIGNED_OUT,
    AccountAuthError,
    AccountError,
    AccountNotSignedIn,
    AccountSetupError,
)
from .ms_auth import (
    GRAPH_ROOT,
    HTTP_TIMEOUT_S,
    NO_CLIENT_ID_MESSAGE,
    WRITE_CALENDAR,
    WRITE_MAIL,
    MicrosoftAccount,
)

logger = logging.getLogger(__name__)

OUTLOOK_CALENDAR_ID = "outlook"     # CalendarEvent.calendar_id of an Outlook event (never a Google calendar's id)
EVENT_FIELDS = "id,subject,isAllDay,isCancelled,start,end,location,webLink,showAs,responseStatus,iCalUId"
MAIL_FIELDS = ("id,conversationId,subject,from,toRecipients,ccRecipients,replyTo,receivedDateTime,bodyPreview,"
               "isRead,internetMessageId")
MAX_PAGES = 5
PAGE_SIZE = 50
MAIL_DAYS_MAX = 14
MAIL_MAX = 50
UNREAD_DAYS = 2              # the canvas's unread frame: the inbox's unread mail of the last two days
UNREAD_MAX = 10
SNIPPET_CAP = 200
_MESSAGE_ID_RE = re.compile(r"[A-Za-z0-9_=+/-]{1,512}")
_FRACTION_RE = re.compile(r"(\.\d{1,6})\d*")


class _GraphFailure(Exception):
    """One Graph read failed: ``problem`` (google_auth PROBLEM_* or "" for a plain failure), a safe message."""

    def __init__(self, message: str, *, problem: str = "", status: int | None = None, unknown: bool = False) -> None:
        super().__init__(message)
        self.problem = problem
        self.status = status
        self.unknown = unknown          # a change that may have happened (5xx, or no answer after it went out)


class _Graph:
    """GETs to Microsoft Graph for one account (the account's token and HTTP session)."""

    def __init__(self, account: MicrosoftAccount, what_name: str) -> None:
        self.account = account
        self.name = what_name           # "calendar" / "mail": the words of the messages
        self.log_name = f"{account.log_name} {what_name}"
        self.refused: tuple[str, str] = ("", "")
        self.refused_generation = -1

    def get(self, url: str, params: dict[str, str] | None, what: str, *, utc: bool = False) -> dict[str, Any]:
        account = self.account
        alias = account.alias
        if self.refused[0] and self.refused_generation == account.generation:
            raise _GraphFailure(self.refused[1], problem=self.refused[0])
        for attempt in (0, 1):
            try:
                token = account.access_token(force_refresh=attempt == 1)
            except AccountSetupError as exc:
                raise _GraphFailure(str(exc), problem=PROBLEM_SETUP) from None
            except AccountNotSignedIn as exc:
                raise _GraphFailure(str(exc), problem=PROBLEM_SIGNED_OUT) from None
            except AccountAuthError as exc:
                raise _GraphFailure(str(exc), problem=exc.problem or PROBLEM_EXPIRED, status=exc.status) from None
            except AccountError as exc:
                raise _GraphFailure(str(exc), problem="", status=exc.status) from None
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            if utc:
                headers["Prefer"] = 'outlook.timezone="UTC"'
            try:
                response = account._get_session().get(url, params=params, headers=headers,  # noqa: SLF001
                                                        timeout=HTTP_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - requests' errors share no small base; a safe message
                logger.info("%s: %s failed (%s)", self.log_name, what, type(exc).__name__)
                raise _GraphFailure(f"Could not reach Outlook while {what} ({type(exc).__name__})") from None
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 401 and attempt == 0:
                logger.info("%s: Outlook answered 401 while %s; refreshing the sign-in once", self.log_name, what)
                continue
            if status != 200:
                logger.info("%s: Outlook answered %d while %s", self.log_name, status, what)
            if status == 200:
                try:
                    body = response.json()
                except Exception:  # noqa: BLE001 - not JSON
                    raise _GraphFailure(f"Outlook sent an answer Jarvis can't read while {what}",
                                        status=status) from None
                return body if isinstance(body, dict) else {}
            if status == 401:
                message = f"Outlook refused the {alias} account's sign-in; connect it again"
                account.forget("Outlook answered 401", problem=PROBLEM_EXPIRED, message=message)
                raise _GraphFailure(message, problem=PROBLEM_EXPIRED, status=status)
            if status == 403:
                self.refused = (PROBLEM_SCOPE, f"Outlook did not allow reading the {alias} account's {self.name}; "
                                               "connect it again and accept every permission")
                self.refused_generation = account.generation
                raise _GraphFailure(self.refused[1], problem=PROBLEM_SCOPE, status=status)
            if status == 404:
                raise _GraphFailure(f"Outlook has no such item ({status})", status=status)
            if status in (429, 503):
                raise _GraphFailure("Outlook is limiting requests right now; try again later", status=status)
            raise _GraphFailure(f"Outlook could not be read just now ({status})", status=status)
        raise _GraphFailure("Outlook could not be read just now")   # pragma: no cover - the loop always returns

    def change(self, method: str, url: str, body: dict[str, Any] | None, what: str) -> dict[str, Any]:
        """One change (POST / PATCH / DELETE), exactly once: never retried, not even after a 401 (see the module
        doc). The JSON answer ({} for 202 / 204); _GraphFailure otherwise (``unknown`` when it may have happened)."""
        account = self.account
        alias = account.alias
        try:
            token = account.access_token()
        except AccountSetupError as exc:
            raise _GraphFailure(str(exc), problem=PROBLEM_SETUP) from None
        except AccountNotSignedIn as exc:
            raise _GraphFailure(str(exc), problem=PROBLEM_SIGNED_OUT) from None
        except AccountAuthError as exc:
            raise _GraphFailure(str(exc), problem=exc.problem or PROBLEM_EXPIRED, status=exc.status) from None
        except AccountError as exc:
            raise _GraphFailure(f"{exc}; nothing was changed", status=exc.status) from None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                   "Prefer": 'outlook.timezone="UTC"'}
        extra: dict[str, Any] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
            extra["json"] = body
        try:
            response = account._get_session().request(method, url, headers=headers,  # noqa: SLF001
                                                      timeout=HTTP_TIMEOUT_S, **extra)
        except Exception as exc:  # noqa: BLE001 - requests' errors share no small base; a safe message
            name = type(exc).__name__
            logger.info("%s: %s failed (%s)", self.log_name, what, name)
            if name in _NOT_SENT_ERRORS:
                raise _GraphFailure(f"Could not reach Outlook while {what} ({name}); nothing was changed") from None
            raise _GraphFailure(f"No clear answer from Outlook while {what} ({name}); it may have happened - check "
                                "Outlook before retrying", unknown=True) from None
        status = int(getattr(response, "status_code", 0) or 0)
        logger.info("%s: Outlook answered %d while %s", self.log_name, status, what)
        if 200 <= status < 300:
            if status in (202, 204):
                return {}
            try:
                answer = response.json()
            except Exception:  # noqa: BLE001 - done, but the answer can't be read: no stamp, no Undo
                return {}
            return answer if isinstance(answer, dict) else {}
        if status == 401:
            raise _GraphFailure(f"Outlook refused the {alias} account's sign-in; nothing was changed - sign in again",
                                problem=PROBLEM_EXPIRED, status=status)
        if status == 403:
            raise _GraphFailure(f"Outlook did not allow this for the {alias} account; nothing was changed - sign in "
                                "again and accept every permission", problem=PROBLEM_SCOPE, status=status)
        if status in (404, 410):
            raise _GraphFailure("Outlook has no such item any more; nothing was changed", status=status)
        if status in (409, 412):
            raise _GraphFailure("It changed in Outlook meanwhile; nothing was changed", status=status)
        if status == 429:
            raise _GraphFailure("Outlook is limiting requests right now; nothing was changed - try again later",
                                status=status)
        if status >= 500 or status == 0:
            raise _GraphFailure(f"Outlook had a problem ({status}); it may have happened - check Outlook before "
                                "retrying", status=status or None, unknown=True)
        raise _GraphFailure(f"Outlook refused the change ({status}); nothing was changed", status=status)


# requests' errors raised before anything was sent (the request never left this PC)
_NOT_SENT_ERRORS = frozenset({"ConnectTimeout", "SSLError", "InvalidURL", "InvalidSchema", "MissingSchema",
                              "InvalidHeader", "InvalidJSONError"})


def _next_link(body: dict[str, Any], prefix: str) -> str:
    """Graph's @odata.nextLink when it stays on the same Graph collection ("" otherwise: never another host)."""
    link = body.get("@odata.nextLink")
    return link if isinstance(link, str) and link.startswith(prefix + "?") else ""


def _utc_text(moment: datetime) -> str:
    aware = moment if moment.utcoffset() is not None else moment.astimezone()
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _graph_wall(raw: Any) -> datetime | None:
    """A naive datetime from Graph's dateTime text ("2026-11-28T17:00:00.0000000": seven decimals cut to six)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = _FRACTION_RE.sub(lambda match: match.group(1), raw.strip().rstrip("Z"))
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    return value.replace(tzinfo=None)


def _graph_instant(value: Any) -> datetime | None:
    """An aware local datetime from Graph's {dateTime, timeZone} (asked in UTC; an IANA zone is read too, any other
    name as this PC's time)."""
    if not isinstance(value, dict):
        return None
    wall = _graph_wall(value.get("dateTime"))
    if wall is None:
        return None
    zone = str(value.get("timeZone") or "UTC").strip()
    if zone.upper() in ("UTC", "Z", "ETC/UTC", "GMT"):
        return wall.replace(tzinfo=timezone.utc).astimezone()
    offset = zone_offset(zone, wall.replace(tzinfo=timezone.utc))
    if offset is None:
        return wall.astimezone()
    return wall.replace(tzinfo=timezone(offset)).astimezone()


def _graph_day(value: Any) -> date | None:
    wall = _graph_wall(value.get("dateTime")) if isinstance(value, dict) else None
    return wall.date() if wall is not None else None


def event_from(item: Any) -> CalendarEvent | None:
    """One calendarView item -> gcal.CalendarEvent (None when cancelled or without a readable start)."""
    if not isinstance(item, dict) or item.get("isCancelled") is True:
        return None
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    response = item.get("responseStatus") if isinstance(item.get("responseStatus"), dict) else {}
    uid = item.get("iCalUId")
    fields: dict[str, Any] = {
        "title": " ".join(str(item.get("subject") or "").split()) or NO_TITLE,
        "location": " ".join(str(location.get("displayName") or "").split()),
        "link": str(item.get("webLink") or "") if isinstance(item.get("webLink"), str) else "",
        "calendar_id": OUTLOOK_CALENDAR_ID,
        "declined": response.get("response") == "declined",
        "free": item.get("showAs") == "free",
        "ical_uid": uid.strip() if isinstance(uid, str) else "",
    }
    if item.get("isAllDay") is True:
        first = _graph_day(item.get("start"))
        if first is None:
            return None
        after = _graph_day(item.get("end"))                  # exclusive, as Google's
        last = after - timedelta(days=1) if after is not None and after > first else first
        return CalendarEvent(all_day_start=first, all_day_end=last, **fields)
    begin = _graph_instant(item.get("start"))
    if begin is None:
        return None
    finish = _graph_instant(item.get("end"))
    if finish is None or finish < begin:
        finish = begin
    return CalendarEvent(start=begin, end=finish, **fields)


def _sort_key(event: CalendarEvent) -> tuple[datetime, int, str]:
    if event.start is not None:
        return event.start, 1, event.title.casefold()
    assert event.all_day_start is not None
    return datetime.combine(event.all_day_start, datetime.min.time()).astimezone(), 0, event.title.casefold()


class OutlookCalendar:
    """Reading one Outlook account's calendar (see the module doc). Calls are serialised by an internal lock;
    ``is_configured`` / ``is_signed_in`` read files only."""

    def __init__(self, account: MicrosoftAccount) -> None:
        self._account = account
        self._graph = _Graph(account, "calendar")
        self._lock = threading.RLock()

    @property
    def alias(self) -> str:
        return self._account.alias

    @property
    def account(self) -> MicrosoftAccount:
        return self._account

    @property
    def calendar_id(self) -> str:
        return OUTLOOK_CALENDAR_ID

    def is_configured(self) -> bool:
        return self._account.is_configured()

    def is_signed_in(self) -> bool:
        return self._account.is_signed_in()

    def sign_in_problem(self) -> tuple[str, str]:
        if not self.is_configured():
            return PROBLEM_SETUP, NO_CLIENT_ID_MESSAGE
        return self._account.problem()

    def list_events(self, start: datetime, end: datetime,
                    calendar_ids: Sequence[str] | str = ()) -> list[CalendarEvent]:
        """The events of the account's own calendar between ``start`` and ``end``, sorted by start (``calendar_ids``
        is accepted for gcal's shape and not used: an Outlook account has its one calendar here). Never signs in."""
        del calendar_ids
        startc = start if start.utcoffset() is not None else start.astimezone()
        endc = end if end.utcoffset() is not None else end.astimezone()
        if endc <= startc:
            return []
        with self._lock:
            url = f"{GRAPH_ROOT}/me/calendarView"
            params: dict[str, str] | None = {
                "startDateTime": _utc_text(start), "endDateTime": _utc_text(end), "$select": EVENT_FIELDS,
                "$orderby": "start/dateTime", "$top": str(PAGE_SIZE)}
            events: list[CalendarEvent] = []
            seen: set[tuple[str, str]] = set()
            for _page in range(MAX_PAGES):
                try:
                    body = self._graph.get(url, params, "reading the calendar", utc=True)
                except _GraphFailure as exc:
                    raise _calendar_error(exc, self.alias) from None
                for item in body.get("value") or ():
                    event = event_from(item)
                    if event is None:
                        continue
                    key = (event.ical_uid or str(item.get("id") or ""),
                           event.start.isoformat() if event.start else str(event.all_day_start))
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(event)
                url = _next_link(body, f"{GRAPH_ROOT}/me/calendarView")
                params = None
                if not url:
                    break
            events.sort(key=_sort_key)
            logger.info("%s: %d event(s)", self._graph.log_name, len(events))
            return events

    # ---- the owner's own changes (outlook_actions only; never by itself) ----------------------------

    def sign_in(self) -> None:
        """The browser sign-in (a card's Sign in click; ms_auth.MicrosoftAccount.sign_in), as gcal's errors."""
        try:
            self._account.sign_in()
        except AccountSetupError as exc:
            raise CalendarSetupError(str(exc), problem=PROBLEM_SETUP) from None
        except AccountAuthError as exc:
            raise CalendarAuthError(str(exc), status=exc.status, problem=exc.problem or PROBLEM_FAILED) from None
        except AccountError as exc:
            raise CalendarError(str(exc), status=exc.status, problem=exc.problem) from None

    def write_problem(self) -> tuple[str, str]:
        """Why Jarvis may not change this calendar now (ms_auth write_problem; ("", "") when it may). Files only."""
        return self._account.write_problem(WRITE_CALENDAR)

    def address(self) -> str:
        return self._account.address()

    def get_event(self, event_id: str, *, calendar_id: str | None = None, interactive: bool = False) -> EventDetails:
        """Outlook's own view of one event (GET /me/events/{id}); EventGone when there is none or it was cancelled.
        Never signs in (``calendar_id`` and ``interactive`` are accepted for gcal's shape)."""
        del calendar_id, interactive
        url = _event_url(event_id)
        with self._lock:
            try:
                item = self._graph.get(url, {"$select": DETAIL_FIELDS}, "reading the event", utc=True)
            except _GraphFailure as exc:
                if exc.status in (404, 410):
                    raise EventGone("Outlook has no such event (deleted, cancelled or a wrong id)",
                                    status=exc.status) from None
                raise _calendar_error(exc, self.alias) from None
        if item.get("isCancelled") is True:
            raise EventGone("The event was cancelled")
        return details_from(item, event_id, self._account.address())

    def find_existing(self, action: Any) -> CalendarEvent | None:
        """An event already in this calendar with the card's title and start (calendarView around it), or None."""
        start, end = _search_window(action)
        title = " ".join(action.title.split()).casefold()
        for event in self.list_events(start, end):
            if " ".join(event.title.split()).casefold() != title:
                continue
            if action.start is not None and event.start is not None \
                    and event.start == _local_instant(action.start):
                return event
            if action.start is None and event.all_day_start == action.all_day_start:
                return event
        return None

    def create_event(self, action: Any, *, footer: str = EVENT_FOOTER, on_request: Any = None) -> EventResult:
        """Add the card's event (POST /me/events) unless the same one is there already (``existed``). Its guests: none
        (Jarvis invites nobody). A repeating event is refused (NotAllowed: not built for Outlook yet)."""
        body = event_body(action, footer=footer)
        with self._lock:
            existing = self.find_existing(action)
            if existing is not None:
                logger.info("%s: the event is there already", self._graph.log_name)
                return EventResult(event_id="", link=existing.link, existed=True)
            url = f"{GRAPH_ROOT}/me/events"
            _tell(on_request, "POST", "/me/events", body)
            answer = self._change("POST", url, body, "adding the event")
        event_id = str(answer.get("id") or "")
        logger.info("%s: added an event", self._graph.log_name)
        return EventResult(event_id=event_id, link=_text(answer.get("webLink")), existed=False,
                           stamp=_change_key(answer))

    def respond(self, event_id: str, answer: str, *, comment: str = "", send_response: bool = True,
                on_request: Any = None) -> ChangeResult:
        """Answer an invitation ("yes" / "no" / "maybe"; Graph's accept / decline / tentativelyAccept, the organizer
        told only when ``send_response``). ``already`` when Outlook has that answer and there is no note."""
        verb = RESPONSE_ACTIONS.get(answer)
        if verb is None:
            raise CalendarError("The answer must be yes, no or maybe")
        with self._lock:
            details = self.get_event(event_id)
            if details.organizer_self:
                raise NotAllowed("You organise this event, so there is no invitation to answer")
            if details.self_response == _GOOGLE_RESPONSES[answer] and not comment:
                logger.info("%s: invitation already answered", self._graph.log_name)
                return ChangeResult(event_id=event_id, link=details.link, already=True)
            body = {"comment": comment, "sendResponse": bool(send_response)}
            _tell(on_request, "POST", f"/me/events/{{id}}/{verb}", body)
            self._change("POST", f"{_event_url(event_id)}/{verb}", body, "answering the invitation")
            stamp = self._stamp_after(event_id)
        logger.info("%s: answered an invitation", self._graph.log_name)
        return ChangeResult(event_id=event_id, link=details.link, already=False, stamp=stamp, before=details)

    def move(self, event_id: str, start: datetime, end: datetime, *, tell_guests: bool = True,
             now: datetime | None = None, on_request: Any = None) -> ChangeResult:
        """Give an event you organise a new start and end (naive wall times on this PC, sent as their instants in
        UTC): PATCH /me/events/{id}. Outlook sends the change to every guest, so a card that says to tell nobody is
        refused for an event with guests (NotAllowed). Refused like gcal.move for a series, an all-day event, one
        you don't organise and a start already begun; ``already`` when it is at that time."""
        if end.replace(tzinfo=None) <= start.replace(tzinfo=None):
            raise CalendarError("The new end is not after the new start")
        begin, finish = _local_instant(start), _local_instant(end)
        with self._lock:
            details = self.get_event(event_id)
            if details.series:
                raise NotAllowed("This is a whole repeating series; Jarvis moves single events or occurrences only "
                                 "- open it in Outlook")
            if not details.organizer_self:
                raise NotAllowed("You don't organise this event, so it can't be moved - answer the invitation instead")
            if details.start is None or details.end is None:
                raise NotAllowed("All-day events can't be moved from here")
            if (details.start, details.end) == (begin.replace(second=0, microsecond=0),
                                                finish.replace(second=0, microsecond=0)):
                return ChangeResult(event_id=event_id, link=details.link, already=True)
            if begin <= (now or datetime.now().astimezone()):
                raise NotAllowed("The new time has already begun")
            if details.attendee_count and not tell_guests:
                raise NotAllowed(TELLS_GUESTS_NOTE)
            body = {"start": _utc_body(begin), "end": _utc_body(finish)}
            _tell(on_request, "PATCH", "/me/events/{id}", body)
            answer = self._change("PATCH", _event_url(event_id), body, "moving the event")
        logger.info("%s: moved an event", self._graph.log_name)
        return ChangeResult(event_id=event_id, link=_text(answer.get("webLink")) or details.link, already=False,
                            stamp=_change_key(answer), before=details)

    def cancel(self, event_id: str, *, comment: str = "", tell_guests: bool = True,
               on_request: Any = None) -> ChangeResult:
        """Cancel an event you organise: with guests POST /me/events/{id}/cancel (each gets the cancellation, with
        ``comment``), without DELETE /me/events/{id}. ``already`` when Outlook has no such event any more."""
        with self._lock:
            try:
                details = self.get_event(event_id)
            except EventGone:
                return ChangeResult(event_id=event_id, link="", already=True)
            if details.series:
                raise NotAllowed("This is a whole repeating series; Jarvis cancels single events or occurrences "
                                 "only - open it in Outlook")
            if not details.organizer_self:
                raise NotAllowed("You don't organise this event - decline it instead")
            try:
                if details.attendee_count:
                    if not tell_guests:
                        raise NotAllowed(TELLS_GUESTS_NOTE)
                    body = {"comment": comment}
                    _tell(on_request, "POST", "/me/events/{id}/cancel", body)
                    self._change("POST", f"{_event_url(event_id)}/cancel", body, "cancelling the event")
                else:
                    _tell(on_request, "DELETE", "/me/events/{id}", None)
                    self._change("DELETE", _event_url(event_id), None, "cancelling the event")
            except EventGone:
                return ChangeResult(event_id=event_id, link="", already=True)
        logger.info("%s: cancelled an event", self._graph.log_name)
        return ChangeResult(event_id=event_id, link="", already=False)

    # ---- Undo: exactly Jarvis's own change, unless the event changed since ----------------------------

    def _unchanged(self, event_id: str, stamp: str, *, uncertain: bool) -> EventDetails | None:
        try:
            details = self.get_event(event_id)
        except EventGone:
            if uncertain:
                return None
            raise EventChanged("The event is gone since Jarvis's change") from None
        if not stamp or details.stamp != stamp:
            raise EventChanged("The event changed since Jarvis's change")
        return details

    def undo_create(self, event_id: str, *, stamp: str, uncertain: bool = False) -> ChangeResult:
        """Delete the event Jarvis added (it has no guests: nobody is told) unless it is gone or changed since."""
        with self._lock:
            details = self._unchanged(event_id, stamp, uncertain=uncertain)
            if details is None:
                return ChangeResult(event_id=event_id, link="", already=True)
            if details.attendee_count:
                raise EventChanged("Guests were added since Jarvis's change")
            self._change("DELETE", _event_url(event_id), None, "removing the event")
        logger.info("%s: removed an event (undo)", self._graph.log_name)
        return ChangeResult(event_id=event_id, link="", already=False)

    def undo_respond(self, event_id: str, response: str, *, stamp: str, send_response: bool = True,
                     uncertain: bool = False) -> ChangeResult:
        """Put your earlier answer back (Google's words: accepted / declined / tentative; Outlook can't go back to
        "not answered", so that has no Undo), with the answer's own ``send_response``."""
        verb = {"accepted": "accept", "declined": "decline", "tentative": "tentativelyAccept"}.get(response)
        if verb is None:
            raise CalendarError("There is no earlier answer to put back")
        with self._lock:
            try:
                details = self._unchanged(event_id, stamp, uncertain=False)
            except EventChanged:
                if uncertain and self._answer_is(event_id, response):
                    return ChangeResult(event_id=event_id, link="", already=True)
                raise
            assert details is not None
            self._change("POST", f"{_event_url(event_id)}/{verb}", {"comment": "", "sendResponse": send_response},
                         "putting your answer back")
        logger.info("%s: put an answer back (undo)", self._graph.log_name)
        return ChangeResult(event_id=event_id, link=details.link, already=False)

    def _answer_is(self, event_id: str, response: str) -> bool:
        try:
            return self.get_event(event_id).self_response == response
        except CalendarError:
            return False

    def undo_move(self, event_id: str, start: datetime, end: datetime, *, stamp: str,
                  uncertain: bool = False) -> ChangeResult:
        """Move the event back to ``start`` / ``end`` (aware: Outlook's times before Jarvis's move); its guests get
        the change again, as with the move."""
        with self._lock:
            try:
                details = self._unchanged(event_id, stamp, uncertain=False)
            except EventChanged:
                if uncertain and self._time_is(event_id, start, end):
                    return ChangeResult(event_id=event_id, link="", already=True)
                raise
            assert details is not None
            body = {"start": _utc_body(start), "end": _utc_body(end)}
            answer = self._change("PATCH", _event_url(event_id), body, "moving the event back")
        logger.info("%s: moved an event back (undo)", self._graph.log_name)
        return ChangeResult(event_id=event_id, link=_text(answer.get("webLink")) or details.link, already=False,
                            stamp=_change_key(answer))

    def _time_is(self, event_id: str, start: datetime, end: datetime) -> bool:
        try:
            details = self.get_event(event_id)
        except CalendarError:
            return False
        return details.start == start and details.end == end

    def _stamp_after(self, event_id: str) -> str:
        """The event's changeKey right after a change whose answer has none ("" when it can't be read: no Undo)."""
        try:
            return self.get_event(event_id).stamp
        except CalendarError:
            return ""

    def _change(self, method: str, url: str, body: dict[str, Any] | None, what: str) -> dict[str, Any]:
        try:
            return self._graph.change(method, url, body, what)
        except _GraphFailure as exc:
            if exc.unknown:
                raise CalendarUnknownOutcome(str(exc), status=exc.status) from None
            if exc.status in (404, 410):
                raise EventGone(str(exc), status=exc.status) from None
            raise _calendar_error(exc, self.alias) from None


TELLS_GUESTS_NOTE = ("Outlook tells every guest of this change - set who gets an email to everyone in Edit, or open "
                     "it in Outlook")
DETAIL_FIELDS = ("id,subject,isAllDay,isCancelled,isOrganizer,start,end,organizer,attendees,responseStatus,type,"
                 "seriesMasterId,originalStartTimeZone,webLink,changeKey")
RESPONSE_ACTIONS = {"yes": "accept", "no": "decline", "maybe": "tentativelyAccept"}
_GOOGLE_RESPONSES = {"yes": "accepted", "no": "declined", "maybe": "tentative"}
# Graph's responseStatus.response -> gcal's words (EventDetails.self_response)
_RESPONSE_WORDS = {"accepted": "accepted", "declined": "declined", "tentativelyAccepted": "tentative",
                   "notResponded": "needsAction", "none": "needsAction", "organizer": ""}
_GRAPH_ID_RE = re.compile(r"[A-Za-z0-9_=+/-]{1,1024}")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _event_url(event_id: str) -> str:
    if not isinstance(event_id, str) or not _GRAPH_ID_RE.fullmatch(event_id):
        raise CalendarError("That is not an Outlook event id")
    return f"{GRAPH_ROOT}/me/events/{quote(event_id, safe='')}"


def _change_key(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return _text(item.get("changeKey")) or _text(item.get("@odata.etag"))


def _local_instant(moment: datetime) -> datetime:
    """A naive wall time on this PC (as the cards hold them) as an aware local instant; an aware one as it is."""
    return moment if moment.utcoffset() is not None else moment.astimezone()


def _utc_body(moment: datetime) -> dict[str, str]:
    """{dateTime, timeZone} of ``moment``'s instant in UTC (Outlook shows it in the mailbox's own zone)."""
    wall = _local_instant(moment).astimezone(timezone.utc).replace(tzinfo=None)
    return {"dateTime": wall.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}


def _search_window(action: Any) -> tuple[datetime, datetime]:
    if action.start is not None:
        begin = _local_instant(action.start)
        return begin - timedelta(minutes=1), begin + timedelta(minutes=1)
    first = action.all_day_start
    begin = datetime.combine(first, datetime.min.time()).astimezone()
    return begin, begin + timedelta(days=1)


def event_body(action: Any, *, footer: str = EVENT_FOOTER) -> dict[str, Any]:
    """The POST /me/events body for a Calendar card or a Todo's block: its title, a plain-text body (the notes, then
    ``footer``, as gcal writes the description), the place, and its time - a timed event as its instants in UTC, an
    all-day one as midnights in this PC's time zone (Outlook's end is exclusive, as Google's). CalendarError /
    NotAllowed for what can't be added (no time; a repeating event)."""
    if getattr(action, "rrule", ""):
        raise NotAllowed("Repeating events can't be added to an Outlook account from here yet - add it in Outlook")
    google = build_event_body(action, "UTC", footer=footer)   # the same checks and description as a Google event
    body: dict[str, Any] = {"subject": google["summary"],
                            "body": {"contentType": "text", "content": google["description"]}}
    if action.where.strip():
        body["location"] = {"displayName": action.where.strip()}
    if action.start is not None:
        start = action.start.replace(tzinfo=None)
        end = action.end.replace(tzinfo=None) if action.end is not None else None
        if end is None or end <= start:
            end = start + timedelta(minutes=DEFAULT_EVENT_MINUTES)
        body["start"], body["end"] = _utc_body(start), _utc_body(end)
    else:
        first = action.all_day_start
        last = action.all_day_end if action.all_day_end and action.all_day_end >= first else first
        zone = local_timezone()
        body["isAllDay"] = True
        body["start"] = {"dateTime": f"{first.isoformat()}T00:00:00", "timeZone": zone}
        body["end"] = {"dateTime": f"{(last + timedelta(days=1)).isoformat()}T00:00:00", "timeZone": zone}
    return body


def details_from(item: dict[str, Any], event_id: str, address: str = "") -> EventDetails:
    """One Graph event -> gcal.EventDetails (``address``: the account's own, for "you are a guest"; service
    "Outlook", so the card's check line says whose view it is)."""
    organizer = item.get("organizer") if isinstance(item.get("organizer"), dict) else {}
    box = organizer.get("emailAddress") if isinstance(organizer.get("emailAddress"), dict) else {}
    attendees = [entry for entry in item.get("attendees") or () if isinstance(entry, dict)]
    response = item.get("responseStatus") if isinstance(item.get("responseStatus"), dict) else {}
    organizer_self = item.get("isOrganizer") is True
    begin = finish = None
    first = last = None
    if item.get("isAllDay") is True:
        first = _graph_day(item.get("start"))
        after = _graph_day(item.get("end"))
        if first is not None:
            last = after - timedelta(days=1) if after is not None and after > first else first
    else:
        begin = _graph_instant(item.get("start"))
        finish = _graph_instant(item.get("end"))
        if begin is not None and (finish is None or finish < begin):
            finish = begin
    kind = _text(item.get("type"))
    name = " ".join(str(box.get("name") or box.get("address") or "").split())
    return EventDetails(
        event_id=_text(item.get("id")) or event_id, calendar_id=OUTLOOK_CALENDAR_ID,
        title=" ".join(str(item.get("subject") or "").split()) or NO_TITLE,
        start=begin, end=finish, all_day_start=first, all_day_end=last,
        status="cancelled" if item.get("isCancelled") is True else "confirmed",
        organizer_self=organizer_self, guests_can_modify=False,
        self_email="" if organizer_self else address,
        self_response=_RESPONSE_WORDS.get(_text(response.get("response")), ""),
        attendee_count=len(attendees), recurring_instance=kind in ("occurrence", "exception"),
        series=kind == "seriesMaster", time_zone=_text(item.get("originalStartTimeZone")),
        link=_text(item.get("webLink")), organizer=name,
        organizer_email=_text(box.get("address")),
        stamp=_change_key(item), service="Outlook")


def _tell(on_request: Any, method: str, path: str, body: dict[str, Any] | None) -> None:
    """The LIVE view's hook: the request's method, its path (ids as {id}) and its exact body, right before it goes;
    whatever goes wrong in it changes nothing."""
    if on_request is None:
        return
    try:
        on_request(method, path, json.loads(json.dumps(body)) if body is not None else None)
    except Exception as exc:  # noqa: BLE001 - only what the LIVE view shows
        logger.debug("Live view: the request hook failed (%s)", type(exc).__name__)


def _calendar_error(exc: _GraphFailure, alias: str) -> CalendarError:
    if exc.problem == PROBLEM_SETUP:
        return CalendarSetupError(str(exc), status=exc.status, problem=PROBLEM_SETUP)
    if exc.problem == PROBLEM_SIGNED_OUT:
        return CalendarNotSignedIn(str(exc) or f"Not signed in to Outlook ({alias} account)",
                                   problem=PROBLEM_SIGNED_OUT)
    if exc.problem:
        return CalendarAuthError(str(exc), status=exc.status, problem=exc.problem)
    return CalendarError(str(exc), status=exc.status)


# --------------------------------------------------------------------------
# Mail: headers and snippet only
# --------------------------------------------------------------------------

def _address(entry: Any) -> str:
    """'Name <address>' (or the address alone) of one Graph recipient; "" when it has none."""
    box = entry.get("emailAddress") if isinstance(entry, dict) else None
    if not isinstance(box, dict):
        return ""
    address = " ".join(str(box.get("address") or "").split())
    name = " ".join(str(box.get("name") or "").split())
    if not address or "@" not in address:
        return ""
    return email.utils.formataddr((name, address)) if name and name != address else address


def _addresses(entries: Any) -> str:
    if not isinstance(entries, list):
        return ""
    return ", ".join(item for item in (_address(entry) for entry in entries) if item)


def message_from(item: Any) -> dict[str, Any] | None:
    """One Graph message -> the dict Gmail's users.messages.get(format="metadata") gives, plus "snippet"."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
        return None
    received = _graph_wall(item.get("receivedDateTime"))
    stamp = received.replace(tzinfo=timezone.utc) if received is not None else None
    headers: list[dict[str, str]] = []

    def add(name: str, value: str) -> None:
        if value:
            headers.append({"name": name, "value": value})

    add("From", _address(item.get("from")))
    add("To", _addresses(item.get("toRecipients")))
    add("Cc", _addresses(item.get("ccRecipients")))
    add("Reply-To", _addresses(item.get("replyTo")))
    add("Subject", " ".join(str(item.get("subject") or "").split()))
    add("Date", email.utils.format_datetime(stamp) if stamp is not None else "")
    message_id = item.get("internetMessageId")
    add("Message-ID", message_id.strip() if isinstance(message_id, str) else "")
    snippet = " ".join(str(item.get("bodyPreview") or "").split())
    labels = ["INBOX"] + ([] if item.get("isRead") is True else ["UNREAD"])
    conversation = item.get("conversationId")
    return {"id": item["id"], "threadId": conversation if isinstance(conversation, str) and conversation else item["id"],
            "labelIds": labels,
            "internalDate": str(int(stamp.timestamp() * 1000)) if stamp is not None else "",
            "snippet": snippet[:SNIPPET_CAP], "payload": {"headers": headers}}


class OutlookMailReader:
    """Reading one Outlook account's inbox headers (see the module doc)."""

    def __init__(self, account: MicrosoftAccount) -> None:
        self._account = account
        self._graph = _Graph(account, "mail")
        self._lock = threading.RLock()
        self.log_name = self._graph.log_name

    @property
    def alias(self) -> str:
        return self._account.alias

    def available(self) -> tuple[str, str]:
        """("", "") when this account's mail can be read now; else (PROBLEM_*, why). No network."""
        if not self._account.is_configured():
            return PROBLEM_SETUP, NO_CLIENT_ID_MESSAGE
        if not self._account.is_signed_in():
            return PROBLEM_SIGNED_OUT, f"Not signed in to the {self.alias} account"
        refused = self._graph.refused
        if refused[0] and self._graph.refused_generation == self._account.generation:
            return refused
        return "", ""

    def recent(self, *, days: int = 1, max_results: int = 25) -> list[dict[str, Any]]:
        """The inbox's newest messages of the last ``days`` days (1..14), at most ``max_results`` (1..50), newest
        first, as message_from shapes them."""
        days = max(1, min(MAIL_DAYS_MAX, int(days)))
        cap = max(1, min(MAIL_MAX, int(max_results)))
        since = datetime.now(timezone.utc) - timedelta(days=days)
        params = {"$select": MAIL_FIELDS, "$filter": f"receivedDateTime ge {_utc_text(since)}",
                  "$orderby": "receivedDateTime desc", "$top": str(cap)}
        with self._lock:
            body = self._read(f"{GRAPH_ROOT}/me/mailFolders/inbox/messages", params, "listing new mail")
        found = [shape for shape in (message_from(item) for item in body.get("value") or ()) if shape is not None]
        logger.info("%s: %d message(s)", self.log_name, len(found[:cap]))
        return found[:cap]

    def unread(self, *, days: int = UNREAD_DAYS, max_results: int = UNREAD_MAX) -> list[dict[str, Any]]:
        """The inbox's unread messages of the last ``days`` days (1..14), at most ``max_results`` (1..50), newest
        first, as message_from shapes them (headers and snippet only). Graph wants the sorted property first in the
        filter, so it reads "receivedDateTime ge ... and isRead eq false"."""
        days = max(1, min(MAIL_DAYS_MAX, int(days)))
        cap = max(1, min(MAIL_MAX, int(max_results)))
        since = datetime.now(timezone.utc) - timedelta(days=days)
        params = {"$select": MAIL_FIELDS, "$filter": f"receivedDateTime ge {_utc_text(since)} and isRead eq false",
                  "$orderby": "receivedDateTime desc", "$top": str(cap)}
        with self._lock:
            body = self._read(f"{GRAPH_ROOT}/me/mailFolders/inbox/messages", params, "listing unread mail")
        found = [shape for shape in (message_from(item) for item in body.get("value") or ())
                 if shape is not None and "UNREAD" in shape["labelIds"]]
        logger.info("%s: %d unread message(s)", self.log_name, len(found[:cap]))
        return found[:cap]

    def message_metadata(self, message_id: str) -> dict[str, Any]:
        """One message's headers and snippet (MailReadError 404 when there is none)."""
        if not isinstance(message_id, str) or not _MESSAGE_ID_RE.fullmatch(message_id):
            raise MailReadError("That is not an Outlook message id")
        with self._lock:
            body = self._read(f"{GRAPH_ROOT}/me/messages/{quote(message_id, safe='')}", {"$select": MAIL_FIELDS},
                              "reading a message's headers")
        shape = message_from(body)
        if shape is None:
            raise MailReadError("Outlook has no such message", status=404)
        return shape

    def _read(self, url: str, params: dict[str, str], what: str) -> dict[str, Any]:
        problem, message = self.available()
        if problem:
            raise MailReadError(message, problem=problem)
        try:
            return self._graph.get(url, params, what, utc=True)
        except _GraphFailure as exc:
            raise MailReadError(str(exc), status=exc.status, problem=exc.problem) from None


# --------------------------------------------------------------------------
# Mail: sending what the owner approved
# --------------------------------------------------------------------------

MESSAGE_GONE_MESSAGE = "The message this answers is gone from Outlook (deleted or moved away); nothing was sent"
OTHER_CONVERSATION_MESSAGE = "That Outlook message is not in the conversation the card names; nothing was sent"


def graph_recipients(addresses: Sequence[str]) -> list[dict[str, Any]]:
    """Plain addresses as Graph's recipients (no names: a name never decides who an address is)."""
    return [{"emailAddress": {"address": address}} for address in addresses]


def reply_body(mail: OutgoingMail) -> dict[str, Any]:
    """POST /me/messages/{id}/reply's body: exactly the card's To and Cc, and its text as the whole plain-text body
    (Outlook keeps the conversation and writes "RE: <subject>" itself). Never a Bcc, never an attachment."""
    message: dict[str, Any] = {"toRecipients": graph_recipients(mail.to),
                               "body": {"contentType": "Text", "content": mail.body}}
    if mail.cc:
        message["ccRecipients"] = graph_recipients(mail.cc)
    return {"message": message}


def new_mail_body(mail: OutgoingMail) -> dict[str, Any]:
    """POST /me/sendMail's body: the card's subject, To, Cc and text (plain), kept in Sent Items."""
    message: dict[str, Any] = {"subject": mail.subject, "body": {"contentType": "Text", "content": mail.body},
                               "toRecipients": graph_recipients(mail.to)}
    if mail.cc:
        message["ccRecipients"] = graph_recipients(mail.cc)
    return {"message": message, "saveToSentItems": True}


class OutlookSender:
    """Sending one Outlook account's replies and emails (gmail.GmailSender's shape; see the module doc). Only
    outlook_actions.OutlookMailBackend calls ``reply`` / ``send``, at the end of a card's hidden hold."""

    def __init__(self, account: MicrosoftAccount) -> None:
        self._account = account
        self._graph = _Graph(account, "mail")
        self._lock = threading.RLock()
        self.log_name = self._graph.log_name

    @property
    def alias(self) -> str:
        return self._account.alias

    @property
    def account(self) -> MicrosoftAccount:
        return self._account

    def is_configured(self) -> bool:
        return self._account.is_configured()

    def is_signed_in(self) -> bool:
        return self._account.is_signed_in()

    def from_address(self) -> str:
        """The account's own address (ms_auth: read at the sign-in); "" when unknown."""
        return self._account.address()

    def ready(self) -> tuple[str, str]:
        """(PROBLEM_*, why) it can't send now; ("", "") when it can (files only)."""
        return self._account.write_problem(WRITE_MAIL)

    def sign_in(self) -> None:
        self._account.sign_in()

    def check_message(self, message_id: str, *, conversation: str = "", internet_id: str = "") -> None:
        """Right before a reply: the message it answers is still in this mailbox and is the one the card names
        (its conversation and Message-ID when the card gives them). GmailError otherwise (nothing was sent)."""
        url = _message_url(message_id)
        with self._lock:
            try:
                item = self._graph.get(url, {"$select": "id,conversationId,internetMessageId"},
                                       "checking the message a reply answers", utc=True)
            except _GraphFailure as exc:
                if exc.status in (404, 410):
                    raise GmailError(MESSAGE_GONE_MESSAGE, status=exc.status) from None
                raise GmailError(f"{exc}; nothing was sent", status=exc.status, problem=exc.problem) from None
        if conversation and _text(item.get("conversationId")) != conversation:
            raise GmailError(OTHER_CONVERSATION_MESSAGE)
        if internet_id and _text(item.get("internetMessageId")).strip() != internet_id:
            raise GmailError(OTHER_CONVERSATION_MESSAGE)

    def reply(self, message_id: str, mail: OutgoingMail, *, on_request: Any = None) -> SentMail:
        """The one reply (POST /me/messages/{id}/reply; 202: sent). GmailUnknownOutcome when it may have gone."""
        url = f"{_message_url(message_id)}/reply"
        body = reply_body(mail)
        with self._lock:
            _tell(on_request, "POST", "/me/messages/{id}/reply", body)
            self._send("POST", url, body, "sending the reply")
        logger.info("%s: sent a reply to %d recipient(s)", self.log_name, mail.recipient_count)
        return SentMail(message_id="", thread_id="", link="")

    def send(self, mail: OutgoingMail, *, on_request: Any = None) -> SentMail:
        """The one new email (POST /me/sendMail; 202: sent). GmailUnknownOutcome when it may have gone."""
        body = new_mail_body(mail)
        with self._lock:
            _tell(on_request, "POST", "/me/sendMail", body)
            self._send("POST", f"{GRAPH_ROOT}/me/sendMail", body, "sending the email")
        logger.info("%s: sent an email to %d recipient(s)", self.log_name, mail.recipient_count)
        return SentMail(message_id="", thread_id="", link="")

    def _send(self, method: str, url: str, body: dict[str, Any], what: str) -> None:
        try:
            self._graph.change(method, url, body, what)
        except _GraphFailure as exc:
            message = str(exc).replace("nothing was changed", "nothing was sent")
            if exc.unknown:
                raise GmailUnknownOutcome(message.replace("check Outlook", "check Sent Items"), status=exc.status,
                                          problem=exc.problem) from None
            if exc.status in (404, 410):
                raise GmailError(MESSAGE_GONE_MESSAGE, status=exc.status) from None
            raise GmailError(message, status=exc.status, problem=exc.problem) from None


def _message_url(message_id: str) -> str:
    if not isinstance(message_id, str) or not _MESSAGE_ID_RE.fullmatch(message_id):
        raise GmailError("That is not an Outlook message id; nothing was sent")
    return f"{GRAPH_ROOT}/me/messages/{quote(message_id, safe='')}"
