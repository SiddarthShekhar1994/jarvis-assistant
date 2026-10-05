"""Google Calendar access for approved calendar proposals and the day's agenda.

:class:`GoogleCalendar` turns an approved
:class:`~briefing_reader.actions.ProposedAction` into a calendar event and
reads the events the reading window shows:

    sign_in()          OAuth installed-app flow in the user's browser; the token
                       is saved to %LOCALAPPDATA%\\briefing-reader\\google_token.json
    timezone()         the calendar's time zone setting (cached)
    find_existing(a)   an event with the same title and start, if there is one
    create_event(a)    find_existing first, else events.insert
    list_events(s, e)  CalendarEvents between two times; never signs in (raises
                       CalendarNotSignedIn instead)

Every call is blocking and may wait on the network or, for sign-in, on the
user's browser for up to ``open_timeout_s`` seconds, so callers run them on a
worker thread. The Google client libraries are imported lazily: importing this
module stays cheap, needs no Google package and never imports Qt.

The OAuth client secret and the access and refresh tokens are registered with
:func:`config.register_secret` as soon as they are read, so the log redacts
them. Exception messages carry only HTTP statuses, Google's error message
(redacted) and exception type names.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import default_data_dir, redact, register_secret

if TYPE_CHECKING:
    from .actions import ProposedAction

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/calendar.settings.readonly"]
TOKEN_FILE_NAME = "google_token.json"
# Used when neither the calendar's setting nor Windows names a time zone.
LAST_RESORT_TIMEZONE = "UTC"
SETUP_HINT = "Set up Google Calendar: README step 8"
SIGN_IN_SUCCESS_MESSAGE = "briefing-reader is connected to Google Calendar. You can close this tab."
EVENT_FOOTER = "Added by briefing-reader from your Daily Briefing."
DEFAULT_EVENT_MINUTES = 60
NO_TITLE = "(No title)"
NOT_SIGNED_IN_MESSAGE = "Not signed in to Google Calendar"

_READ_RETRIES = 2          # list/settings are safe to retry; insert is not retried
_MAX_LIST_PAGES = 5
_LIST_PAGE_SIZE = 50
_MAX_MESSAGE_LEN = 200
# 403s that are about quota or project setup, not about the user's sign-in.
_RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded",
                                 "dailyLimitExceeded", "RATE_LIMIT_EXCEEDED"})
_SETUP_REASONS = frozenset({"accessNotConfigured", "SERVICE_DISABLED"})
# actions.py writes "until <date>" on a timed event as the end of that day in UTC.
_END_OF_DAY_UNTIL_RE = re.compile(r"UNTIL=(\d{8})T235959Z")
_RULE_PREFIXES = ("RRULE:", "EXRULE:", "RDATE", "EXDATE")
_GOOGLE_MODULES = ("googleapiclient", "google_auth_oauthlib", "google_auth_httplib2",
                   "google.oauth2")
_libraries_available: bool | None = None
# "Europe/Berlin", "America/Argentina/Buenos_Aires", "Etc/GMT+5", "UTC"
_IANA_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*")
_local_timezone: str | None = None


# --------------------------------------------------------------------------
# Errors and results
# --------------------------------------------------------------------------

class CalendarError(Exception):
    """A Calendar failure whose message is safe to show and log (no tokens, no client secret)."""

    def __init__(self, message: str = "", *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class CalendarSetupError(CalendarError):
    """The OAuth client secret file is missing or invalid, or the Calendar API is not enabled."""


class CalendarAuthError(CalendarError):
    """Sign-in was cancelled, denied or timed out, or the saved sign-in was revoked.

    When Google rejects the saved sign-in, the token file is deleted first, so
    the next call signs in again.
    """


class CalendarNotSignedIn(CalendarAuthError):
    """There is no usable saved sign-in and the call may not open the browser to get one.

    Raised by :meth:`GoogleCalendar.list_events`, which only an explicit
    Connect or Approve click may turn into a sign-in.
    """


@dataclass(frozen=True)
class EventResult:
    event_id: str
    link: str            # htmlLink
    existed: bool        # True when a matching event was already on the calendar (nothing created)


@dataclass(frozen=True)
class CalendarEvent:
    """One event from :meth:`GoogleCalendar.list_events`.

    Timed events have time-zone-aware ``start``/``end`` (the offsets Google
    sent). All-day events have ``start``/``end`` None and ``all_day_start``
    plus an inclusive ``all_day_end`` instead (Google's end date is exclusive).
    ``link`` is the event's page on Google Calendar (htmlLink); ``location``
    is the event's location as written, which may be a meeting URL.
    """

    title: str
    start: datetime | None = None
    end: datetime | None = None
    all_day_start: date | None = None
    all_day_end: date | None = None
    location: str = ""
    link: str = ""
    calendar_id: str = "primary"

    @property
    def all_day(self) -> bool:
        return self.all_day_start is not None


def default_token_path() -> Path:
    """%LOCALAPPDATA%\\briefing-reader\\google_token.json"""
    return default_data_dir() / TOKEN_FILE_NAME


def local_timezone() -> str:
    """This PC's time zone as an IANA name ("Europe/Berlin"), else LAST_RESORT_TIMEZONE.

    The fallback for when the calendar's own time zone setting cannot be read.
    Python has no time zone database here (no tzdata), so the name comes from
    ICU, which Windows 10 (1903 and later) and 11 ship as icu.dll and which maps
    the Windows time zone setting to its IANA name. Cached after the first call.
    """
    global _local_timezone
    if _local_timezone is None:
        _local_timezone = _icu_host_timezone() or LAST_RESORT_TIMEZONE
    return _local_timezone


def _icu_host_timezone() -> str | None:
    """ICU's default (host) time zone id, or None when it cannot be read."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        get_zone = ctypes.WinDLL("icu").ucal_getDefaultTimeZone
        get_zone.argtypes = [ctypes.c_wchar_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_int)]
        get_zone.restype = ctypes.c_int32
        buffer = ctypes.create_unicode_buffer(128)
        status = ctypes.c_int(0)
        length = get_zone(buffer, len(buffer), ctypes.byref(status))
    except (OSError, AttributeError, ValueError, TypeError):
        return None
    if status.value > 0 or not 0 < length < len(buffer):   # ICU: an error code > 0 is a failure
        return None
    name = buffer.value[:length]
    if not _IANA_NAME_RE.fullmatch(name) or name == "Etc/Unknown":
        return None
    return name


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class GoogleCalendar:
    """Creates Calendar events for approved proposals. Blocking: use a worker thread.

    Calls that talk to Google are serialised by an internal lock.
    :meth:`is_configured` and :meth:`is_signed_in` never take that lock or
    touch the network, so the UI thread may call them while a sign-in waits.
    """

    def __init__(self, *, client_secret_path: Path, token_path: Path, calendar_id: str = "primary",
                 service_factory: Callable[[Any], Any] | None = None,
                 flow_factory: Callable[[Path, list[str]], Any] | None = None,
                 open_timeout_s: int = 300) -> None:
        self._client_secret_path = Path(client_secret_path)
        self._token_path = Path(token_path)
        self._calendar_id = calendar_id
        self._service_factory = service_factory or _build_service
        self._flow_factory = flow_factory or _installed_app_flow
        self._open_timeout_s = open_timeout_s
        self._lock = threading.RLock()
        self._depth = 0   # nesting of public calls, so a failure is logged once
        self._creds: Any = None
        self._service: Any = None
        self._timezone: str | None = None

    @property
    def calendar_id(self) -> str:
        return self._calendar_id

    @property
    def token_path(self) -> Path:
        return self._token_path

    @contextmanager
    def _guarded(self) -> Iterator[None]:
        """Hold the lock; log a CalendarError once, at the outermost public call.

        CalendarNotSignedIn is the normal state before the first sign-in (the
        agenda asks every few minutes), so it is logged at INFO.
        """
        with self._lock:
            self._depth += 1
            try:
                yield
            except CalendarError as exc:
                if self._depth == 1:
                    log = logger.info if isinstance(exc, CalendarNotSignedIn) else logger.warning
                    log("Google Calendar call failed (%s): %s", type(exc).__name__, exc)
                raise
            finally:
                self._depth -= 1

    def is_configured(self) -> bool:
        """True when the OAuth client secret file exists."""
        return self._client_secret_path.is_file()

    def is_signed_in(self) -> bool:
        """True when a saved sign-in exists that is valid or can be refreshed."""
        if not google_libraries_available():
            return False
        creds = _read_token(self._token_path, quiet=True)
        return creds is not None and bool(creds.valid or creds.refresh_token)

    # ---- sign-in -----------------------------------------------------------

    def sign_in(self) -> None:
        """Open the Google sign-in in the browser and save the token.

        Blocks until the user finishes or ``open_timeout_s`` passes. Raises
        CalendarSetupError (no usable client secret), CalendarAuthError
        (cancelled, denied, timed out) or CalendarError.
        """
        with self._guarded():
            _require_libraries()
            self._read_client_config()
            try:
                flow = self._flow_factory(self._client_secret_path, list(SCOPES))
            except Exception as exc:  # noqa: BLE001 - any failure here is a setup problem
                raise CalendarSetupError(
                    f"{SETUP_HINT} (could not use {self._client_secret_path.name}: "
                    f"{type(exc).__name__})") from None
            logger.info("Google Calendar: waiting for sign-in in the browser (up to %d s)",
                        self._open_timeout_s)
            try:
                creds = flow.run_local_server(
                    port=0, open_browser=True, timeout_seconds=self._open_timeout_s,
                    authorization_prompt_message="", success_message=SIGN_IN_SUCCESS_MESSAGE,
                    prompt="consent")   # consent every time so Google returns a refresh token
            except Exception as exc:  # noqa: BLE001 - mapped to a safe message
                raise _sign_in_error(exc) from None
            _register_credentials(creds)
            self._save_token(creds)
            self._creds = creds
            self._service = None
            self._timezone = None
            logger.info("Google Calendar: signed in")

    def _read_client_config(self) -> dict[str, Any]:
        """Validate the client secret file and register its secret for redaction."""
        name = self._client_secret_path.name
        try:
            data = json.loads(self._client_secret_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raise CalendarSetupError(f"{SETUP_HINT} ({name} was not found)") from None
        except OSError as exc:
            problem = exc.strerror or type(exc).__name__
            raise CalendarSetupError(f"{SETUP_HINT} (could not read {name}: {problem})") from None
        except ValueError:
            raise CalendarSetupError(f"{SETUP_HINT} ({name} is not valid JSON)") from None
        installed = data.get("installed") if isinstance(data, dict) else None
        if not isinstance(installed, dict) or not all(
                isinstance(installed.get(key), str) and installed[key].strip()
                for key in ("client_id", "client_secret")):
            raise CalendarSetupError(
                f"{SETUP_HINT} ({name} is not an OAuth client of type Desktop app)")
        register_secret(installed["client_secret"])
        return data

    def _save_token(self, creds: Any) -> None:
        """Write the token JSON atomically. A failure is logged, not raised."""
        path = self._token_path
        part = path.with_name(path.name + ".part")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            part.write_text(creds.to_json(), encoding="utf-8")
            os.replace(part, path)
        except OSError as exc:
            _unlink_quietly(part)
            logger.warning("Google Calendar: could not save the sign-in to %s (%s); "
                           "you will be asked to sign in again next time",
                           path, exc.strerror or type(exc).__name__)

    def _forget_token(self, reason: str) -> None:
        self._creds = None
        self._service = None
        try:
            self._token_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("Google Calendar: could not delete the saved sign-in %s (%s)",
                           self._token_path, exc.strerror or type(exc).__name__)
            return
        logger.warning("Google Calendar: removed the saved sign-in (%s); "
                       "the next Approve signs in again", reason)

    # ---- credentials and service -------------------------------------------

    def _ensure_credentials(self, *, interactive: bool = True) -> Any:
        """Usable credentials: the saved sign-in (refreshed when needed), else a new sign-in.

        Without ``interactive`` a missing or unrefreshable sign-in raises
        CalendarNotSignedIn instead of opening the browser.
        """
        creds = self._creds
        if creds is None:
            creds = _read_token(self._token_path)
            if creds is None:
                return self._sign_in_or_raise(interactive)
            self._creds = creds
            self._service = None
        if not creds.valid:
            if creds.refresh_token:
                self._refresh(creds)
            else:
                self._forget_token("it cannot be refreshed")
                return self._sign_in_or_raise(interactive)
        return self._creds

    def _sign_in_or_raise(self, interactive: bool) -> Any:
        if not interactive:
            raise CalendarNotSignedIn(NOT_SIGNED_IN_MESSAGE)
        self.sign_in()
        return self._creds

    def _refresh(self, creds: Any) -> None:
        try:
            _refresh_credentials(creds)
        except Exception as exc:  # noqa: BLE001 - mapped to a safe message
            raise self._transport_error(exc, "refreshing the sign-in") from None
        _register_credentials(creds)
        self._save_token(creds)
        self._service = None

    def _get_service(self, *, interactive: bool = True) -> Any:
        if not self.is_configured():
            raise CalendarSetupError(
                f"{SETUP_HINT} ({self._client_secret_path.name} was not found)")
        _require_libraries()
        creds = self._ensure_credentials(interactive=interactive)
        if self._service is None:
            try:
                self._service = self._service_factory(creds)
            except Exception as exc:  # noqa: BLE001 - mapped to a safe message
                raise CalendarError(
                    f"Could not start the Google Calendar client ({type(exc).__name__})") from None
        return self._service

    def _execute(self, request: Any, what: str, *, retries: int = 0,
                 forgiving: bool = False) -> Any:
        """Run one API request, mapping every failure to a CalendarError."""
        from googleapiclient.errors import HttpError

        try:
            result = request.execute(num_retries=retries)
        except HttpError as exc:
            raise self._http_error(exc, what, forgiving=forgiving) from None
        except Exception as exc:  # noqa: BLE001 - socket/ssl/httplib2 errors share no base
            raise self._transport_error(exc, what) from None
        if self._creds is not None:
            _register_credentials(self._creds)   # the library may have refreshed the token
        return result

    def _http_error(self, exc: Any, what: str, *, forgiving: bool) -> CalendarError:
        """401/403 -> CalendarAuthError (token deleted); other statuses -> CalendarError.

        403s about quota stay CalendarError and a disabled Calendar API is a
        CalendarSetupError; neither deletes the token. With ``forgiving`` (a
        read that has a fallback) only 401 is treated as a sign-in problem.
        """
        status = _status_of(exc)
        message = _google_message(exc)
        detail = f"{status}: {message}" if message else str(status)
        reasons = _error_reasons(exc)
        if status == 403 and reasons & _SETUP_REASONS:
            return CalendarSetupError(
                f"{SETUP_HINT}: the Google Calendar API is not enabled ({detail})", status=status)
        auth_problem = status == 401 or (
            status == 403 and not forgiving and not reasons & _RATE_LIMIT_REASONS)
        if auth_problem:
            self._forget_token(f"Google answered {status}")
            return CalendarAuthError(
                f"Google Calendar refused access while {what} ({detail}). "
                "Approve again to sign in.", status=status)
        return CalendarError(f"Google Calendar error while {what} ({detail})", status=status)

    def _transport_error(self, exc: Exception, what: str) -> CalendarError:
        """A non-HTTP failure: a rejected token refresh (token deleted) or the network."""
        from google.auth.exceptions import RefreshError

        if isinstance(exc, RefreshError):
            if exc.retryable:
                return CalendarError("Google could not refresh the sign-in just now; try again")
            self._forget_token("Google rejected it")
            return CalendarAuthError(
                "Google Calendar sign-in expired or was revoked. Approve again to sign in.")
        return CalendarError(f"Could not reach Google Calendar while {what} ({type(exc).__name__})")

    # ---- calendar ------------------------------------------------------------

    def timezone(self) -> str:
        """The calendar's time zone setting (cached), else this PC's zone with a warning.

        The fallback (:func:`local_timezone`) is not cached here, so the next
        call asks Google again.
        """
        with self._guarded():
            if self._timezone is None:
                value = self._read_timezone()
                if value is None:
                    return local_timezone()
                self._timezone = value
            return self._timezone

    def _read_timezone(self) -> str | None:
        service = self._get_service()
        try:
            setting = self._execute(service.settings().get(setting="timezone"),
                                    "reading your time zone", retries=_READ_RETRIES,
                                    forgiving=True)
        except (CalendarAuthError, CalendarSetupError):
            raise
        except CalendarError as exc:
            logger.warning("Google Calendar: could not read your time zone (%s); using this "
                           "PC's time zone, %s", exc, local_timezone())
            return None
        value = setting.get("value") if isinstance(setting, dict) else None
        if not isinstance(value, str) or not value.strip():
            logger.warning("Google Calendar: your settings have no time zone; using this PC's "
                           "time zone, %s", local_timezone())
            return None
        return value.strip()

    def find_existing(self, action: ProposedAction) -> dict | None:
        """The event already on the calendar with the same title and start, else None."""
        with self._guarded():
            _require_actionable(action)
            return self._find_existing(action, self.timezone())

    def _find_existing(self, action: ProposedAction, tz: str) -> dict | None:
        """events.list around the first occurrence, rendered in ``tz``; match title + start."""
        service = self._get_service()
        title = action.title.strip().casefold()
        start = _start_key(action)
        time_min, time_max = _search_window(action)
        page_token: str | None = None
        for _ in range(_MAX_LIST_PAGES):
            query: dict[str, Any] = {
                "calendarId": self._calendar_id, "timeMin": time_min, "timeMax": time_max,
                "singleEvents": True, "q": action.title.strip(), "timeZone": tz,
                "maxResults": 50}
            if page_token:
                query["pageToken"] = page_token
            page = self._execute(service.events().list(**query), "checking the calendar",
                                 retries=_READ_RETRIES)
            page = page if isinstance(page, dict) else {}
            for event in page.get("items") or ():
                if isinstance(event, dict) and _matches(event, title, start):
                    return event
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return None

    def create_event(self, action: ProposedAction) -> EventResult:
        """Add ``action`` to the calendar unless a matching event is already there.

        Signs in first when there is no saved sign-in. Raises CalendarError
        (or a subclass) with a message that is safe to show.
        """
        with self._guarded():
            _require_actionable(action)
            tz = self.timezone()
            existing = self._find_existing(action, tz)
            if existing is not None:
                event_id = str(existing.get("id") or "")
                logger.info("Google Calendar: already on the calendar (event %s)", event_id)
                return EventResult(event_id=event_id, link=str(existing.get("htmlLink") or ""),
                                   existed=True)
            service = self._get_service()
            request = service.events().insert(calendarId=self._calendar_id,
                                              body=build_event_body(action, tz))
            created = self._execute(request, "adding the event")
            created = created if isinstance(created, dict) else {}
            event_id = str(created.get("id") or "")
            logger.info("Google Calendar: created event %s", event_id or "(no id returned)")
            return EventResult(event_id=event_id, link=str(created.get("htmlLink") or ""),
                               existed=False)

    def list_events(self, start: datetime, end: datetime,
                    calendar_ids: Sequence[str] | str = ("primary",)) -> list[CalendarEvent]:
        """The events between ``start`` and ``end`` on each calendar, sorted by start.

        Never signs in: without a usable saved sign-in this raises
        CalendarNotSignedIn before any request and opens nothing. Naive bounds
        are local time. Cancelled events are skipped and an event that is on
        two of the calendars is listed once. A calendar that fails with a plain
        CalendarError (say, an unknown id) is skipped with a warning when
        another one answered; sign-in and setup problems always raise.
        """
        ids = _calendar_ids(calendar_ids)
        with self._guarded():
            if not ids or _as_aware(end) <= _as_aware(start):
                return []
            service = self._get_service(interactive=False)
            time_min, time_max = _rfc3339(start), _rfc3339(end)
            events: list[CalendarEvent] = []
            seen: set[tuple[str, str]] = set()
            failures: list[CalendarError] = []
            for calendar_id in ids:
                try:
                    items = self._list_calendar(service, calendar_id, time_min, time_max)
                except (CalendarAuthError, CalendarSetupError):
                    raise
                except CalendarError as exc:
                    failures.append(exc)
                    continue
                events.extend(_new_events(items, calendar_id, seen))
            if failures and len(failures) == len(ids):
                raise failures[0]
            for exc in failures:
                logger.warning("Google Calendar: skipped a calendar that could not be read (%s)",
                               exc)
            events.sort(key=_event_sort_key)
            logger.debug("Google Calendar: %d event(s) from %d calendar(s)", len(events),
                         len(ids) - len(failures))
            return events

    def _list_calendar(self, service: Any, calendar_id: str, time_min: str,
                       time_max: str) -> list[dict]:
        """Every events.list item of one calendar (up to _MAX_LIST_PAGES pages)."""
        items: list[dict] = []
        page_token: str | None = None
        for _ in range(_MAX_LIST_PAGES):
            query: dict[str, Any] = {
                "calendarId": calendar_id, "timeMin": time_min, "timeMax": time_max,
                "singleEvents": True, "orderBy": "startTime", "maxResults": _LIST_PAGE_SIZE,
                "showDeleted": False}
            if page_token:
                query["pageToken"] = page_token
            page = self._execute(service.events().list(**query), "reading your calendar",
                                 retries=_READ_RETRIES)
            page = page if isinstance(page, dict) else {}
            items.extend(item for item in page.get("items") or () if isinstance(item, dict))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return items


# --------------------------------------------------------------------------
# Event bodies
# --------------------------------------------------------------------------

def build_event_body(action: ProposedAction, time_zone: str) -> dict[str, Any]:
    """The events.insert body for ``action`` in ``time_zone``."""
    _require_actionable(action)
    notes = action.notes.strip()
    body: dict[str, Any] = {
        "summary": action.title.strip(),
        "description": f"{notes}\n\n{EVENT_FOOTER}" if notes else EVENT_FOOTER,
        "reminders": {"useDefault": True},
    }
    if action.where.strip():
        body["location"] = action.where.strip()
    if action.start is not None:
        start = action.start.replace(tzinfo=None)
        end = action.end.replace(tzinfo=None) if action.end is not None else None
        if end is None or end <= start:
            end = start + timedelta(minutes=DEFAULT_EVENT_MINUTES)
        body["start"] = {"dateTime": _wall_time(start), "timeZone": time_zone}
        body["end"] = {"dateTime": _wall_time(end), "timeZone": time_zone}
    else:
        first = action.all_day_start
        assert first is not None
        last = action.all_day_end if action.all_day_end and action.all_day_end >= first else first
        body["start"] = {"date": first.isoformat()}
        body["end"] = {"date": (last + timedelta(days=1)).isoformat()}   # Google's end is exclusive
    rule = action.rrule.strip()
    if rule:
        if not rule.upper().startswith(_RULE_PREFIXES):
            rule = "RRULE:" + rule
        if action.start is not None:
            rule = _localize_until(rule)
        body["recurrence"] = [rule]
    return body


def _wall_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _localize_until(rule: str) -> str:
    """Move an end-of-day UTC UNTIL to the end of that day in local time.

    "weekly until Dec 11" at 5 PM Pacific starts at 01:00Z on Dec 12, which a
    literal UNTIL=20261211T235959Z would cut off. Python has no time zone
    database here (no tzdata), so the machine's own rules are used; this
    assumes the calendar and the PC share a time zone.
    """
    match = _END_OF_DAY_UNTIL_RE.search(rule)
    if match is None:
        return rule
    try:
        day = datetime.strptime(match.group(1), "%Y%m%d").date()
        until = _local_end_of_day_utc(day)
    except (ValueError, OverflowError, OSError):
        return rule
    return f"{rule[:match.start()]}UNTIL={until:%Y%m%dT%H%M%S}Z{rule[match.end():]}"


def _local_end_of_day_utc(day: date) -> datetime:
    """23:59:59 local time on ``day``, as a UTC datetime (machine time zone rules)."""
    local = datetime(day.year, day.month, day.day, 23, 59, 59)
    return local.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Matching existing events
# --------------------------------------------------------------------------

def _require_actionable(action: ProposedAction) -> None:
    if not getattr(action, "actionable", False):
        reason = getattr(action, "error", "")
        raise CalendarError("This proposal cannot be added to the calendar"
                            + (f": {reason}" if reason else ""))
    if action.start is None and action.all_day_start is None:
        raise CalendarError("This proposal has no date")


def _start_key(action: ProposedAction) -> datetime | date:
    if action.start is not None:
        return action.start.replace(tzinfo=None, second=0, microsecond=0)
    assert action.all_day_start is not None
    return action.all_day_start


def _search_window(action: ProposedAction) -> tuple[str, str]:
    """timeMin/timeMax in UTC wide enough for any time zone offset (up to 14 h)."""
    if action.start is not None:
        start = action.start.replace(tzinfo=None, second=0, microsecond=0)
        low, high = start - timedelta(days=1), start + timedelta(days=1)
    else:
        assert action.all_day_start is not None
        day = datetime.combine(action.all_day_start, datetime.min.time())
        low, high = day - timedelta(days=1), day + timedelta(days=2)
    return f"{low:%Y-%m-%dT%H:%M:%S}Z", f"{high:%Y-%m-%dT%H:%M:%S}Z"


def _matches(event: dict, title: str, start: datetime | date) -> bool:
    if event.get("status") == "cancelled":
        return False
    summary = event.get("summary")
    if not isinstance(summary, str) or summary.strip().casefold() != title:
        return False
    when = event.get("start")
    if not isinstance(when, dict):
        return False
    if isinstance(start, datetime):
        return _parse_wall_time(when.get("dateTime")) == start
    return when.get("date") == start.isoformat()


def _parse_wall_time(raw: Any) -> datetime | None:
    """The local wall time of an RFC 3339 dateTime ("2026-10-09T15:00:00-07:00")."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=None, second=0, microsecond=0)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Reading events (list_events)
# --------------------------------------------------------------------------

def _calendar_ids(calendar_ids: Sequence[str] | str) -> list[str]:
    """Stripped, non-empty ids in order without repeats; a single string is one id."""
    raw = (calendar_ids,) if isinstance(calendar_ids, str) else tuple(calendar_ids or ())
    ids: list[str] = []
    for value in raw:
        text = str(value or "").strip()
        if text and text not in ids:
            ids.append(text)
    return ids


def _as_aware(moment: datetime) -> datetime:
    """``moment`` itself when it has a time zone, else read as local time."""
    return moment if moment.utcoffset() is not None else moment.astimezone()


def _rfc3339(moment: datetime) -> str:
    """"2026-10-05T00:00:00-07:00" (seconds precision; a naive time is local)."""
    return _as_aware(moment).isoformat(timespec="seconds")


def _new_events(items: list[dict], calendar_id: str,
                seen: set[tuple[str, str]]) -> Iterator[CalendarEvent]:
    """The usable events of one calendar that ``seen`` does not hold yet (``seen`` is updated)."""
    for item in items:
        event = _event_from(item, calendar_id)
        if event is None:
            continue
        uid = str(item.get("iCalUID") or item.get("id") or "")
        if uid:
            key = (uid, _start_text(event))
            if key in seen:
                continue
            seen.add(key)
        yield event


def _start_text(event: CalendarEvent) -> str:
    """The start as text that is equal for the same moment whatever offset Google used."""
    if event.start is not None:
        return event.start.astimezone(timezone.utc).isoformat()
    return event.all_day_start.isoformat() if event.all_day_start is not None else ""


def _event_from(item: dict, calendar_id: str) -> CalendarEvent | None:
    """One events.list item -> CalendarEvent; None when cancelled or without a readable start."""
    if item.get("status") == "cancelled":
        return None
    start = item.get("start") if isinstance(item.get("start"), dict) else {}
    end = item.get("end") if isinstance(item.get("end"), dict) else {}
    fields: dict[str, Any] = {
        "title": " ".join(str(item.get("summary") or "").split()) or NO_TITLE,
        "location": " ".join(str(item.get("location") or "").split()),
        "link": str(item.get("htmlLink") or ""),
        "calendar_id": calendar_id,
    }
    if start.get("dateTime"):
        begin = _parse_instant(start.get("dateTime"))
        if begin is None:
            return None
        finish = _parse_instant(end.get("dateTime"))
        if finish is None or finish < begin:
            finish = begin
        return CalendarEvent(start=begin, end=finish, **fields)
    first = _parse_day(start.get("date"))
    if first is None:
        return None
    after = _parse_day(end.get("date"))   # exclusive
    last = after - timedelta(days=1) if after is not None and after > first else first
    return CalendarEvent(all_day_start=first, all_day_end=last, **fields)


def _parse_instant(raw: Any) -> datetime | None:
    """An aware datetime from an RFC 3339 dateTime (no offset: local time); None if unreadable."""
    if not isinstance(raw, str):
        return None
    try:
        return _as_aware(datetime.fromisoformat(raw.strip()))
    except (ValueError, OverflowError, OSError):
        return None


def _parse_day(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _event_sort_key(event: CalendarEvent) -> tuple[datetime, int, str]:
    """By start; an all-day event comes first on its day (local midnight)."""
    if event.start is not None:
        return event.start, 1, event.title.casefold()
    assert event.all_day_start is not None
    return datetime.combine(event.all_day_start, time()).astimezone(), 0, event.title.casefold()


# --------------------------------------------------------------------------
# Google library glue (lazy imports; patched or injected in tests)
# --------------------------------------------------------------------------

def _build_service(creds: Any) -> Any:
    from googleapiclient.discovery import build

    # static_discovery uses the API description bundled with the library: no extra request.
    return build("calendar", "v3", credentials=creds, cache_discovery=False, static_discovery=True)


def _installed_app_flow(client_secret_path: Path, scopes: list[str]) -> Any:
    from google_auth_oauthlib.flow import InstalledAppFlow

    # Same as InstalledAppFlow.from_client_secrets_file, but tolerates a UTF-8 BOM.
    with open(client_secret_path, encoding="utf-8-sig") as fh:
        client_config = json.load(fh)
    return InstalledAppFlow.from_client_config(client_config, scopes)


def _refresh_credentials(creds: Any) -> None:
    from google.auth.transport.requests import Request

    creds.refresh(Request())


def google_libraries_available() -> bool:
    """True when the Google client packages from requirements.txt are installed."""
    global _libraries_available
    if _libraries_available is None:
        try:
            _libraries_available = all(importlib.util.find_spec(name) is not None
                                       for name in _GOOGLE_MODULES)
        except (ImportError, ValueError):
            _libraries_available = False
    return _libraries_available


def _require_libraries() -> None:
    if not google_libraries_available():
        raise CalendarSetupError(
            f"{SETUP_HINT} (the Google packages are missing: "
            "py -3.13 -m pip install -r requirements.txt)")


def _read_token(path: Path, *, quiet: bool = False) -> Any | None:
    """Saved credentials, or None when missing or unusable (logged unless quiet; never raised)."""
    log = logger.debug if quiet else logger.warning
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log("Google Calendar: could not read the saved sign-in (%s)",
            exc.strerror or type(exc).__name__)
        return None
    from google.oauth2.credentials import Credentials

    try:
        info = json.loads(text)
        if not isinstance(info, dict):
            raise ValueError("not a JSON object")
        creds = Credentials.from_authorized_user_info(info, SCOPES)
    except (ValueError, TypeError) as exc:
        log("Google Calendar: the saved sign-in is unusable (%s); "
            "the next Approve signs in again", type(exc).__name__)
        return None
    _register_credentials(creds)
    return creds


def _register_credentials(creds: Any) -> None:
    for name in ("token", "refresh_token", "client_secret"):
        value = getattr(creds, name, None)
        if isinstance(value, str):
            register_secret(value)


def _sign_in_error(exc: Exception) -> CalendarError:
    from google_auth_oauthlib.flow import WSGITimeoutError
    from oauthlib.oauth2.rfc6749.errors import OAuth2Error

    if isinstance(exc, (WSGITimeoutError, TimeoutError)):
        return CalendarAuthError("Google sign-in timed out")
    if isinstance(exc, OAuth2Error):
        if exc.error == "access_denied":
            return CalendarAuthError("Google sign-in was cancelled or access was denied")
        return CalendarAuthError(f"Google sign-in failed ({exc.error or type(exc).__name__})")
    if isinstance(exc, Warning) and "scope" in str(exc).casefold():
        # oauthlib raises Warning("Scope has changed ...") when a box was unticked.
        return CalendarAuthError("Google sign-in did not grant every permission; "
                                 "approve again and allow all of them")
    return CalendarError(f"Google sign-in failed ({type(exc).__name__})")


def _status_of(exc: Any) -> int:
    status = getattr(getattr(exc, "resp", None), "status", 0)
    try:
        return int(status)
    except (TypeError, ValueError):
        return 0


def _google_message(exc: Any) -> str:
    raw = getattr(exc, "reason", "")
    text = " ".join(str(raw or "").split())
    text = redact(text)
    if len(text) > _MAX_MESSAGE_LEN:
        text = text[:_MAX_MESSAGE_LEN - 3] + "..."
    return text


def _error_reasons(exc: Any) -> set[str]:
    """The machine-readable reasons in a Google error body (both error formats)."""
    try:
        error = json.loads(getattr(exc, "content", b"").decode("utf-8"))["error"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return set()
    if not isinstance(error, dict):
        return set()
    reasons: set[str] = set()
    if isinstance(error.get("status"), str):
        reasons.add(error["status"])
    for key in ("errors", "details"):
        for item in error.get(key) or ():
            if isinstance(item, dict) and isinstance(item.get("reason"), str):
                reasons.add(item["reason"])
    return reasons


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
