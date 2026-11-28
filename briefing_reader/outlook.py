"""Reading an Outlook / Microsoft account through Microsoft Graph: its calendar and its mail's headers (read only).

The readers have the shape of the Google ones, so the app needs few changes for an account with
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

Nothing here sends, changes or deletes anything; the sign-in asks only for read permissions (ms_auth). Every call is
blocking: callers use a worker thread. A 401 is answered with one refresh of the token and one more try; a second 401
deletes the sign-in (connect again). A 403 keeps the sign-in and turns the read off until the next sign-in. Logs carry
the alias ("work", "personal", else "other"), counts and HTTP statuses only: never a title, a subject, an address, a
token or a URL's query. Qt-free.
"""

from __future__ import annotations

import email.utils
import logging
import re
import threading
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .gcal import (
    NO_TITLE,
    CalendarAuthError,
    CalendarError,
    CalendarEvent,
    CalendarNotSignedIn,
    CalendarSetupError,
    zone_offset,
)
from .gmail import MailReadError
from .google_auth import (
    PROBLEM_EXPIRED,
    PROBLEM_SCOPE,
    PROBLEM_SETUP,
    PROBLEM_SIGNED_OUT,
    AccountAuthError,
    AccountError,
    AccountNotSignedIn,
    AccountSetupError,
)
from .ms_auth import GRAPH_ROOT, HTTP_TIMEOUT_S, NO_CLIENT_ID_MESSAGE, MicrosoftAccount

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

    def __init__(self, message: str, *, problem: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.problem = problem
        self.status = status


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
