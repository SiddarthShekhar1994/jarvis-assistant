"""Proposed actions: read the briefing's "Proposed actions" section and remember decisions.

The scheduled briefing task (README: "Instructions for the Claude briefing
task") writes one line per proposal under a heading named
"Proposed actions":

    Calendar: <title> | <when> | <repeat> | <where> | <notes>

    parse_action_line(text)    one line -> ProposedAction (never raises)
    extract_actions(lines)     pull that section out of a page's FlatLines
    ActionStore(path)          persisted decisions (created / exists / denied / failed)

Only "Calendar:" lines are actionable. Other kinds ("Reply:", "Todo:") and
lines that cannot be parsed stay informational; ``error`` says why a line could
not be read. Nothing here talks to Google: gcal.py does that, and only after an
explicit Approve.

Qt-free. Briefing content is personal, so only counts and action ids are
logged, never text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from .models import DIVIDER, HEADING, PARAGRAPH, FlatLine

logger = logging.getLogger(__name__)

CALENDAR = "calendar"
UNKNOWN = "unknown"
DEFAULT_HEADINGS = ("Proposed actions",)
DEFAULT_DURATION = timedelta(minutes=60)

STATUS_CREATED = "created"   # Approve created the event
STATUS_EXISTS = "exists"     # Approve found it already on the calendar
STATUS_DENIED = "denied"
STATUS_FAILED = "failed"     # not final: the proposal stays pending and can be retried
STATUSES = (STATUS_CREATED, STATUS_EXISTS, STATUS_DENIED, STATUS_FAILED)
DECIDED_STATUSES = frozenset({STATUS_CREATED, STATUS_EXISTS, STATUS_DENIED})

_WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEKDAY_LONG = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTH_SHORT = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_LONG = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")
_SEPARATOR = " \u00b7 "   # middle dot, as in the HUD artboard
_DAY_MINUTES = 24 * 60
_MAX_COUNT = 999

_FORMAT_HINT = '"Calendar: title | YYYY-MM-DD HH:MM-HH:MM | repeat | where | notes"'
_NO_DATE = "no date (expected YYYY-MM-DD, optionally with HH:MM-HH:MM)"


def _text_prep() -> ModuleType:
    # Imported on use: text_prep imports this module for build_script(actions=...).
    from . import text_prep
    return text_prep


# --------------------------------------------------------------------------
# ProposedAction
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ProposedAction:
    """One line of the "Proposed actions" section.

    Timed events have naive ``start``/``end`` in local wall time (the
    calendar's time zone); all-day events have ``all_day_start`` and an
    inclusive ``all_day_end`` instead. ``repeat`` holds normalized words
    ("weekly until 2026-12-11", "daily x 5") and ``rrule`` the matching RRULE
    ("" = one-off). ``error`` says why the line could not be read.
    """

    id: str
    kind: str
    raw: str
    title: str = ""
    start: datetime | None = None
    end: datetime | None = None
    all_day_start: date | None = None
    all_day_end: date | None = None
    repeat: str = ""
    rrule: str = ""
    where: str = ""
    notes: str = ""
    error: str = ""

    @property
    def actionable(self) -> bool:
        return self.kind == CALENDAR and not self.error

    @property
    def all_day(self) -> bool:
        return self.all_day_start is not None

    def describe(self, today: date | None = None) -> str:
        """Display detail: "Fri Oct 9 \u00b7 3:00-4:00 PM \u00b7 weekly until Dec 11" ("" if not actionable).

        Years are shown only for dates outside ``today``'s year (default: the real today).
        """
        if not self.actionable:
            return ""
        today = today or date.today()
        parts = _describe_when(self, today)
        repeat = _repeat_words(self.repeat, today, spoken=False)
        return _SEPARATOR.join(part for part in (*parts, repeat) if part)

    def spoken(self, today: date | None = None) -> str:
        """"Chess Club Weekly Meeting, Friday October 9, 3 to 4 PM, weekly until December 11".

        No final period (the caller adds one); "" if not actionable.
        """
        if not self.actionable:
            return ""
        today = today or date.today()
        title = _spoken_title(self.title)
        repeat = _repeat_words(self.repeat, today, spoken=True)
        return ", ".join(part for part in (title, *_spoken_when(self, today), repeat) if part)


def _action_id(action: ProposedAction) -> str:
    """sha1 of (kind, title, start, end, all-day, rrule); where and notes do not count.

    A line that is not actionable has no reliable fields, so its raw text
    stands in for the title.
    """
    if action.actionable:
        name = action.title
        if action.all_day_start is not None:
            first, last = action.all_day_start.isoformat(), _iso(action.all_day_end)
        else:
            first, last = _iso(action.start), _iso(action.end)
    else:
        name, first, last = action.raw, "", ""
    parts = (action.kind, " ".join(name.casefold().split()), first, last,
             "1" if action.all_day else "0", action.rrule if action.actionable else "")
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _iso(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    return value.isoformat()


def _make_action(kind: str, raw: str, **fields: Any) -> ProposedAction:
    action = ProposedAction(id="", kind=kind, raw=raw, **fields)
    return replace(action, id=_action_id(action))


# --------------------------------------------------------------------------
# parse_action_line
# --------------------------------------------------------------------------

class _LineError(ValueError):
    """Why a proposal line could not be read (shown on its card)."""


# "Calendar:" / "**Calendar**:" (markdown is stripped first). "https://" is not a kind.
_KIND_RE = re.compile(r"(?P<label>[A-Za-z][A-Za-z_-]*(?:\s+[A-Za-z][A-Za-z_-]*){0,2})\s*:(?!//)\s*")
_CALENDAR_LABELS = frozenset({"calendar", "cal", "calendar event", "calendar invite", "event", "invite"})
_KIND_ALIASES = {"to do": "todo", "follow up": "followup"}


def parse_action_line(text: str) -> ProposedAction:
    """One line of the section -> ProposedAction. Never raises: problems go to ``error``."""
    raw = _clean_line(text or "")
    if not raw:
        return _make_action(UNKNOWN, raw, error="empty line")
    match = _KIND_RE.match(raw)
    if match is None:
        # Prose ("Nothing to approve today.") is informational; a "|" line lost its kind.
        error = f"no action type (expected {_FORMAT_HINT})" if "|" in raw else ""
        return _make_action(UNKNOWN, raw, title=raw, error=error)
    kind = _kind_word(match.group("label"))
    body = raw[match.end():].strip()
    if kind != CALENDAR:
        return _make_action(kind, raw, title=body)
    return _parse_calendar(raw, body)


def _kind_word(label: str) -> str:
    words = " ".join(re.split(r"[\s_-]+", label.casefold())).strip()
    if words in _CALENDAR_LABELS:
        return CALENDAR
    return _KIND_ALIASES.get(words) or words.split()[0]


def _parse_calendar(raw: str, body: str) -> ProposedAction:
    fields = [field.strip() for field in body.split("|")]
    fields += [""] * (5 - len(fields))
    title_text, when_text, repeat_text, where = fields[:4]
    notes = " | ".join(field for field in fields[4:] if field)
    found: dict[str, Any] = {"title": _title_text(title_text), "where": where, "notes": notes}
    try:
        if not found["title"]:
            raise _LineError("no title")
        if not when_text:
            raise _LineError(_NO_DATE)
        when = _parse_when(when_text)
        found.update(start=when.start, end=when.end, all_day_start=when.all_day_start,
                     all_day_end=when.all_day_end)
        repeat = _read_repeat(repeat_text)
        if repeat is not None:
            if repeat.until is not None and repeat.until < when.first_day:
                raise _LineError("the repeat ends before the event starts")
            found.update(repeat=repeat.words, rrule=repeat.rrule(all_day=when.all_day))
    except _LineError as exc:
        return _make_action(CALENDAR, raw, error=str(exc), **found)
    return _make_action(CALENDAR, raw, **found)


def _title_text(text: str) -> str:
    # The title is spoken and shown as a heading: a link or URL in it is noise.
    return _text_prep().strip_markdown(text) or text


# ---- markdown cleanup that keeps URLs ------------------------------------

_URL_RE = re.compile(r"(?:\b(?:https?|ftp)://|\bwww\.)(?:\([^\s()<>|]*\)|[^\s()<>|])+", re.I)
_URL_TRAILING = ".,;:!?'\"*_~"
_MD_LINK_RE = re.compile(
    r"(?<!!)\[([^\[\]]*)\]\(\s*<?((?:[^()\s<>]|\([^()\s]*\))+)>?(?:\s+\"[^\"]*\")?\s*\)")
_ANGLE_URL_RE = re.compile(r"<((?:https?|ftp)://[^\s<>]+)>", re.I)
# URLs are parked on private-use characters while strip_markdown runs (it removes URLs).
_PARK_BASE = 0xF000
_PARK_LIMIT = 0x400
_PARKED_RE = re.compile("[\uf000-\uf3ff]")


def _clean_line(text: str) -> str:
    """Markdown, HTML and emoji removed like text_prep.strip_markdown, but URLs kept.

    A meeting link is the most useful "where", so "[Zoom](https://...)"
    becomes "Zoom (https://...)" and bare URLs stay.
    """
    text = _PARKED_RE.sub("", text)
    text = _MD_LINK_RE.sub(_link_text, text)
    text = _ANGLE_URL_RE.sub(r"\1", text)
    urls: list[str] = []

    def park(match: re.Match[str]) -> str:
        url = match.group(0)
        core = url.rstrip(_URL_TRAILING)
        if len(urls) >= _PARK_LIMIT:
            return url
        urls.append(core)
        return chr(_PARK_BASE + len(urls) - 1) + url[len(core):]

    text = _URL_RE.sub(park, text)
    text = _text_prep().strip_markdown(text)
    return _PARKED_RE.sub(lambda m: urls[ord(m.group()) - _PARK_BASE], text)


def _link_text(match: re.Match[str]) -> str:
    label, url = match.group(1).strip(), match.group(2)
    if not label or label == url or _URL_RE.fullmatch(label):
        return url
    if _URL_RE.match(url):
        return f"{label} ({url})"
    return label   # a relative or in-app link has nothing worth keeping


# ---- <when> ---------------------------------------------------------------

@dataclass(frozen=True)
class _When:
    start: datetime | None = None
    end: datetime | None = None
    all_day_start: date | None = None
    all_day_end: date | None = None

    @property
    def all_day(self) -> bool:
        return self.all_day_start is not None

    @property
    def first_day(self) -> date:
        if self.all_day_start is not None:
            return self.all_day_start
        assert self.start is not None
        return self.start.date()


_WEEKDAY_INDEX = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2,
    "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}
_WEEKDAY_WORD = "(?:" + "|".join(sorted(_WEEKDAY_INDEX, key=len, reverse=True)) + r")\.?"
_DASH_RE = re.compile("[\u2010-\u2015\u2212]")      # hyphens, en/em dash, minus sign
_WHEN_NOISE_RE = re.compile(r"[()\[\],;]")
_SPACES_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)")
_ALL_DAY_RE = re.compile(r"\ball[\s-]?day\b")
_RANGE_WORDS = r"(?:to|through|thru|until|till)"
_RANGE_SEP_RE = re.compile(rf"\s*(?:-|\b{_RANGE_WORDS}\b)\s*")
_LEAD_RE = re.compile(r"^(?:(?:on|from|at)\b|@|t(?=\d))\s*")
_WEEKDAY_LEAD_RE = re.compile(rf"^({_WEEKDAY_WORD})(?:\s+|$)")
# Between two dates: an optional time for the first one, the range word, an optional weekday.
_MIDDLE_RE = re.compile(
    rf"(?:(?:at\s+)?(?P<time>\S.*?)\s*)?(?:-|\b{_RANGE_WORDS}\b)\s*(?P<weekday>{_WEEKDAY_WORD})?")


def _parse_when(text: str) -> _When:
    """"2026-10-09 15:00-16:00", "2026-10-09 3pm", "2026-10-05", "2027-03-22 to 2027-03-26"."""
    shown = " ".join(text.split())
    s = _SPACES_RE.sub(" ", _WHEN_NOISE_RE.sub(" ", _DASH_RE.sub("-", text).casefold()))
    s, all_day_marks = _ALL_DAY_RE.subn(" ", s)
    s = _SPACES_RE.sub(" ", s).strip().rstrip(".").strip()
    dates = list(_DATE_RE.finditer(s))
    if not dates:
        raise _LineError(_NO_DATE)
    if len(dates) > 2:
        raise _LineError(f'could not read the date "{shown}"')
    first = _make_date(dates[0])
    _check_prefix(s[:dates[0].start()], first, shown)
    if len(dates) == 1:
        rest = _take_weekday(_strip_lead(s[dates[0].end():]), first)
        if not rest:
            return _When(all_day_start=first, all_day_end=first)
        if all_day_marks:
            raise _LineError(f'both a time and "all day" in "{shown}"')
        start_minutes, end_minutes = _parse_time_range(rest)
        start = datetime.combine(first, time()) + timedelta(minutes=start_minutes)
        return _When(start=start, end=start + timedelta(minutes=end_minutes - start_minutes))
    return _parse_two_dates(s, dates, first, shown, bool(all_day_marks))


def _parse_two_dates(s: str, dates: list[re.Match[str]], first: date, shown: str,
                     all_day: bool) -> _When:
    second = _make_date(dates[1])
    middle = _MIDDLE_RE.fullmatch(s[dates[0].end():dates[1].start()].strip())
    if middle is None:
        raise _LineError(f'could not read the dates "{shown}" (expected "YYYY-MM-DD to YYYY-MM-DD")')
    if middle.group("weekday"):
        _check_weekday(middle.group("weekday"), second)
    first_time = (middle.group("time") or "").strip()
    second_time = _strip_lead(s[dates[1].end():])
    if not first_time and not second_time:
        if second < first:
            raise _LineError("the end date is before the start date")
        return _When(all_day_start=first, all_day_end=second)
    if not (first_time and second_time):
        raise _LineError(f'give a time for both dates or for neither in "{shown}"')
    if all_day:
        raise _LineError(f'both a time and "all day" in "{shown}"')
    start = datetime.combine(first, time()) + timedelta(minutes=_lone_minutes(_read_clock(first_time)))
    end = datetime.combine(second, time()) + timedelta(minutes=_lone_minutes(_read_clock(second_time)))
    if end <= start:
        raise _LineError("the event ends before it starts")
    return _When(start=start, end=end)


def _make_date(match: re.Match[str]) -> date:
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        raise _LineError(f'"{match.group(0)}" is not a real date') from None


def _strip_lead(text: str) -> str:
    text = text.strip()
    while True:
        stripped = _LEAD_RE.sub("", text, count=1).strip()
        if stripped == text:
            return text
        text = stripped


def _check_prefix(prefix: str, day: date, shown: str) -> None:
    """Only an optional weekday ("Fri", "on Friday") may come before the date."""
    prefix = _strip_lead(prefix)
    if not prefix:
        return
    if _take_weekday(prefix, day):
        raise _LineError(f'could not read the date "{shown}"')


def _take_weekday(text: str, day: date) -> str:
    """Drop a leading weekday after checking it matches ``day``; return the rest."""
    match = _WEEKDAY_LEAD_RE.match(text)
    if match is None:
        return text
    _check_weekday(match.group(1), day)
    return _strip_lead(text[match.end():])


def _check_weekday(word: str, day: date) -> None:
    if _WEEKDAY_INDEX[word.rstrip(".")] != day.weekday():
        raise _LineError(f"{day.isoformat()} is a {_WEEKDAY_LONG[day.weekday()]}, "
                         f"not {word.rstrip('.').capitalize()}")


# ---- times ------------------------------------------------------------------

_TIME_TOKEN_RE = re.compile(r"(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?\s*(?:(?P<mer>[ap])\.?(?:m\.?)?)?")
_TWELVE_HOURS = 12 * 60


@dataclass(frozen=True)
class _Clock:
    """One written time. ``style``: "24h", "12h" (AM/PM given), "word" (noon/midnight)
    or "open" (1-12 without AM/PM: either half of the day)."""

    text: str
    minutes: int          # after midnight, reading an "open" time as AM
    style: str
    has_minutes: bool


def _read_clock(token: str) -> _Clock:
    token = token.strip()
    if token in ("noon", "12 noon"):
        return _Clock(token, _TWELVE_HOURS, "word", True)
    if token in ("midnight", "12 midnight"):
        return _Clock(token, 0, "word", True)
    match = _TIME_TOKEN_RE.fullmatch(token)
    if match is None:
        raise _LineError(f'could not read the time "{token}"')
    hour, minute = int(match.group("hour")), int(match.group("minute") or 0)
    has_minutes = match.group("minute") is not None
    meridiem = match.group("mer")
    if minute > 59:
        raise _LineError(f'could not read the time "{token}"')
    if meridiem:
        if not 1 <= hour <= 12:
            raise _LineError(f'could not read the time "{token}"')
        offset = _TWELVE_HOURS if meridiem == "p" else 0
        return _Clock(token, (hour % 12) * 60 + offset + minute, "12h", has_minutes)
    if hour > 24 or (hour == 24 and minute):
        raise _LineError(f'could not read the time "{token}"')
    style = "24h" if hour == 0 or hour >= 13 else "open"
    return _Clock(token, hour * 60 + minute, style, has_minutes)


def _lone_minutes(clock: _Clock) -> int:
    """A time on its own: "open" ones are 24-hour when written as HH:MM."""
    if clock.style == "open" and not clock.has_minutes:
        raise _LineError(f'the time "{clock.text}" needs AM/PM or HH:MM (24-hour)')
    return clock.minutes


def _parse_time_range(text: str) -> tuple[int, int]:
    """"15:00-16:00", "3pm - 4:30 PM", "3-4pm", "11:30-12:30 PM" -> (start, end) minutes.

    The end may pass midnight (end before start = next day). A single time lasts 60 minutes.
    """
    parts = _RANGE_SEP_RE.split(text)
    if len(parts) > 2 or not all(part.strip() for part in parts):
        raise _LineError(f'could not read the time "{text}"')
    clocks = [_read_clock(part) for part in parts]
    if len(clocks) == 1:
        start = _lone_minutes(clocks[0])
        if start >= _DAY_MINUTES:
            raise _LineError(f'could not read the time "{text}"')
        return start, start + int(DEFAULT_DURATION.total_seconds() // 60)
    start, duration = _resolve_pair(clocks[0], clocks[1], text)
    return start, start + duration


def _resolve_pair(first: _Clock, second: _Clock, text: str) -> tuple[int, int]:
    """(start minutes, duration) for a range, filling in a missing AM/PM.

    An "open" side takes whichever half of the day gives the shorter event:
    "3-4pm" is 3 to 4 PM, "9-5pm" 9 AM to 5 PM, "11-1am" 11 PM to 1 AM.
    """
    if first.style == "open" and second.style == "open":
        if not (first.has_minutes and second.has_minutes):
            raise _LineError(f'the time "{text}" needs AM/PM or HH:MM (24-hour)')
        candidates = [(first.minutes, second.minutes)]
    else:
        candidates = [(start, end) for start in _readings(first, second)
                      for end in _readings(second, first)]
    best: tuple[int, int] | None = None
    for start, end in candidates:
        duration = _duration(start, end)
        if start < _DAY_MINUTES and duration and (best is None or duration < best[1]):
            best = (start, duration)
    if best is None:
        raise _LineError(f'the time "{text}" ends when it starts')
    return best


def _readings(clock: _Clock, other: _Clock) -> list[int]:
    if clock.style != "open" or (clock.has_minutes and other.style == "24h"):
        return [clock.minutes]
    return [clock.minutes, (clock.minutes + _TWELVE_HOURS) % _DAY_MINUTES]


def _duration(start: int, end: int) -> int:
    """Minutes from start to end, wrapping past midnight; 0 when they are the same time."""
    if end % _DAY_MINUTES == start:
        return 0
    return end - start if end > start else end - start + _DAY_MINUTES


# ---- <repeat> -------------------------------------------------------------

_REPEAT_ALIASES = {
    "daily": "daily", "every day": "daily", "each day": "daily", "everyday": "daily",
    "weekdays": "weekdays", "every weekday": "weekdays", "each weekday": "weekdays",
    "on weekdays": "weekdays", "weekdays only": "weekdays", "mon-fri": "weekdays",
    "monday-friday": "weekdays",
    "weekly": "weekly", "every week": "weekly", "each week": "weekly",
    "biweekly": "biweekly", "bi-weekly": "biweekly", "fortnightly": "biweekly",
    "every 2 weeks": "biweekly", "every two weeks": "biweekly", "every other week": "biweekly",
    "monthly": "monthly", "every month": "monthly", "each month": "monthly",
    "yearly": "yearly", "annually": "yearly", "annual": "yearly", "every year": "yearly",
    "each year": "yearly",
}
_NO_REPEAT = frozenset({
    "", "once", "one-off", "one off", "one time", "one-time", "single", "none", "no", "never",
    "no repeat", "does not repeat", "doesn't repeat", "-", "n/a", "na",
})
_RRULE_FREQ = {
    "daily": "FREQ=DAILY",
    "weekdays": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "weekly": "FREQ=WEEKLY",
    "biweekly": "FREQ=WEEKLY;INTERVAL=2",
    "monthly": "FREQ=MONTHLY",
    "yearly": "FREQ=YEARLY",
}
_REPEAT_DISPLAY = {"weekdays": "weekdays", "biweekly": "every 2 weeks"}
_REPEAT_SPOKEN = {"weekdays": "every weekday", "biweekly": "every two weeks"}
_REPEAT_RE = re.compile(
    r"(?P<freq>.*?)\s*(?:"
    r"\b(?:until|till|through|thru|ending(?:\s+on)?|ends?(?:\s+on)?)\s+(?P<until>\S.*)"
    r"|(?:x|\u00d7)\s*(?P<count>\d+)"
    r"|(?:for\s+)?(?P<times>\d+)\s*(?:times|occurrences|x)"
    r")?"
)
_REPEAT_HINT = "use daily, weekdays, weekly, biweekly, monthly or yearly"


@dataclass(frozen=True)
class _Repeat:
    freq: str
    until: date | None = None
    count: int | None = None

    @property
    def words(self) -> str:
        if self.until is not None:
            return f"{self.freq} until {self.until.isoformat()}"
        if self.count is not None:
            return f"{self.freq} x {self.count}"
        return self.freq

    def rrule(self, *, all_day: bool) -> str:
        rule = "RRULE:" + _RRULE_FREQ[self.freq]
        if self.until is not None:
            # gcal sends the start with the calendar's time zone, so a timed UNTIL is UTC.
            stamp = self.until.strftime("%Y%m%d")
            rule += f";UNTIL={stamp}" if all_day else f";UNTIL={stamp}T235959Z"
        elif self.count is not None:
            rule += f";COUNT={self.count}"
        return rule


def _read_repeat(text: str) -> _Repeat | None:
    """"weekly until 2026-12-11", "daily x 5", "monthly 3 times" -> _Repeat; "" / "once" -> None."""
    shown = " ".join(text.split())
    s = _SPACES_RE.sub(" ", _WHEN_NOISE_RE.sub(" ", _DASH_RE.sub("-", text).casefold()))
    s = s.strip().rstrip(".").strip()
    match = _REPEAT_RE.fullmatch(s)
    if match is None:   # unreachable: the pattern accepts any text
        raise _LineError(f'could not read the repeat "{shown}" ({_REPEAT_HINT})')
    freq = match.group("freq").strip()
    until_text = match.group("until")
    count_text = match.group("count") or match.group("times")
    if freq in _NO_REPEAT:
        if until_text or count_text:
            raise _LineError(f'"{shown}" has an end but no repeat ({_REPEAT_HINT})')
        return None
    if freq not in _REPEAT_ALIASES:
        raise _LineError(f'could not read the repeat "{shown}" ({_REPEAT_HINT})')
    freq = _REPEAT_ALIASES[freq]
    if until_text:
        date_match = _DATE_RE.fullmatch(until_text.rstrip("."))
        if date_match is None:
            raise _LineError(f'could not read the end date in "{shown}" (expected YYYY-MM-DD)')
        return _Repeat(freq, until=_make_date(date_match))
    if count_text:
        count = int(count_text)
        if not 1 <= count <= _MAX_COUNT:
            raise _LineError(f"the repeat count must be 1 to {_MAX_COUNT}")
        return None if count == 1 else _Repeat(freq, count=count)
    return _Repeat(freq)


# --------------------------------------------------------------------------
# describe() / spoken() wording
# --------------------------------------------------------------------------

def _short_day(day: date, show_year: bool) -> str:
    text = f"{_WEEKDAY_SHORT[day.weekday()]} {_MONTH_SHORT[day.month - 1]} {day.day}"
    return f"{text}, {day.year}" if show_year else text


def _long_day(day: date, show_year: bool) -> str:
    text = f"{_WEEKDAY_LONG[day.weekday()]} {_MONTH_LONG[day.month - 1]} {day.day}"
    return f"{text}, {day.year}" if show_year else text


def _years_shown(first: date, last: date, today: date) -> tuple[bool, bool]:
    """A range names its year once, at the end, unless it spans two years."""
    show_last = last.year != today.year
    show_first = first.year != today.year and first.year != last.year
    return show_first, show_last


def _meridiem(moment: datetime) -> str:
    return "AM" if moment.hour < 12 else "PM"


def _clock_text(moment: datetime) -> str:
    return f"{moment.hour % 12 or 12}:{moment.minute:02d}"


def _is_short(start: datetime, end: datetime) -> bool:
    """Same day, or past midnight but under 24 hours: one date and a time range."""
    return end - start < timedelta(days=1) and (end.date() - start.date()).days <= 1


def _describe_when(action: ProposedAction, today: date) -> list[str]:
    if action.all_day_start is not None:
        first = action.all_day_start
        last = action.all_day_end or first
        if last == first:
            return [_short_day(first, first.year != today.year), "all day"]
        show_first, show_last = _years_shown(first, last, today)
        return [f"{_short_day(first, show_first)} - {_short_day(last, show_last)}", "all day"]
    start, end = action.start, action.end
    if start is None:
        return []
    end = end or start + DEFAULT_DURATION
    if _is_short(start, end):
        return [_short_day(start.date(), start.year != today.year), _display_times(start, end)]
    show_first, show_last = _years_shown(start.date(), end.date(), today)
    return [f"{_short_day(start.date(), show_first)} {_clock_text(start)} {_meridiem(start)} - "
            f"{_short_day(end.date(), show_last)} {_clock_text(end)} {_meridiem(end)}"]


def _display_times(start: datetime, end: datetime) -> str:
    """"3:00-4:00 PM", "11:30 AM-12:30 PM", "11:00 PM-1:00 AM (next day)"."""
    if start.date() == end.date() and _meridiem(start) == _meridiem(end):
        return f"{_clock_text(start)}-{_clock_text(end)} {_meridiem(end)}"
    text = f"{_clock_text(start)} {_meridiem(start)}-{_clock_text(end)} {_meridiem(end)}"
    if end.date() > start.date() and end.time() != time():
        text += " (next day)"
    return text


def _spoken_title(title: str) -> str:
    spoken = _text_prep().to_spoken(title) or title
    return spoken.rstrip(".!?").strip() or title


def _spoken_when(action: ProposedAction, today: date) -> list[str]:
    if action.all_day_start is not None:
        first = action.all_day_start
        last = action.all_day_end or first
        if last == first:
            return [_long_day(first, first.year != today.year), "all day"]
        show_first, show_last = _years_shown(first, last, today)
        return [f"{_long_day(first, show_first)} to {_long_day(last, show_last)}", "all day"]
    start, end = action.start, action.end
    if start is None:
        return []
    end = end or start + DEFAULT_DURATION
    if _is_short(start, end):
        return [_long_day(start.date(), start.year != today.year), _spoken_times(start, end)]
    show_first, show_last = _years_shown(start.date(), end.date(), today)
    return [f"from {_long_day(start.date(), show_first)} at {_spoken_clock(start, True)} "
            f"to {_long_day(end.date(), show_last)} at {_spoken_clock(end, True)}"]


def _spoken_clock(moment: datetime, with_meridiem: bool) -> str:
    """"3", "3:30", "noon", "midnight" (plus " PM" when asked, never after noon/midnight)."""
    if moment.minute == 0 and moment.hour in (0, 12):
        return "midnight" if moment.hour == 0 else "noon"
    hour = moment.hour % 12 or 12
    text = str(hour) if moment.minute == 0 else f"{hour}:{moment.minute:02d}"
    return f"{text} {_meridiem(moment)}" if with_meridiem else text


def _spoken_times(start: datetime, end: datetime) -> str:
    """"3 to 4 PM", "3:30 to 4 PM", "11 AM to 1 PM", "noon to 1 PM"."""
    words = {"noon", "midnight"}
    first, last = _spoken_clock(start, False), _spoken_clock(end, False)
    same_half = start.date() == end.date() and _meridiem(start) == _meridiem(end)
    if same_half and first not in words and last not in words:
        return f"{first} to {last} {_meridiem(end)}"
    return f"{_spoken_clock(start, True)} to {_spoken_clock(end, True)}"


def _repeat_words(repeat: str, today: date, *, spoken: bool) -> str:
    """"weekly until Dec 11" / "every two weeks, 6 times"; unknown text is shown as written."""
    if not repeat:
        return ""
    try:
        parsed = _read_repeat(repeat)
    except _LineError:
        return repeat
    if parsed is None:
        return ""
    names = _REPEAT_SPOKEN if spoken else _REPEAT_DISPLAY
    word = names.get(parsed.freq, parsed.freq)
    if parsed.until is not None:
        until = parsed.until
        show_year = until.year != today.year
        if spoken:
            day = f"{_MONTH_LONG[until.month - 1]} {until.day}"
        else:
            day = f"{_MONTH_SHORT[until.month - 1]} {until.day}"
        return f"{word} until {day}, {until.year}" if show_year else f"{word} until {day}"
    if parsed.count is not None:
        return f"{word}, {parsed.count} times"
    return word


# --------------------------------------------------------------------------
# extract_actions
# --------------------------------------------------------------------------

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(?=\S)")
_WHOLE_BOLD_RE = re.compile(r"^(\*\*|__)(?=\S)((?:(?!\1).)+?)(?<=\S)\1$", re.S)
_NON_WORD_RE = re.compile(r"[\W_]+")
_PARAGRAPH_TITLE_LEVEL = 2   # text_prep reads titles on a page without heading blocks like a heading_2
_TITLE_MAX_CHARS = 60
_TITLE_MAX_WORDS = 6


def extract_actions(lines: Sequence[FlatLine],
                    heading_names: Sequence[str] = DEFAULT_HEADINGS,
                    ) -> tuple[list[ProposedAction], list[FlatLine]]:
    """Pull the "Proposed actions" section out of a page.

    The section starts at a heading (any level) whose cleaned title matches
    one of ``heading_names`` (case-insensitive, counts and emoji ignored) and
    runs until the next heading of the same or a higher level; deeper
    subheadings belong to it. Returns (the parsed non-empty lines in page
    order, the page without the section). Duplicate ids keep the first.
    """
    names = (heading_names,) if isinstance(heading_names, str) else tuple(heading_names)
    wanted = {key for key in map(_heading_key, names) if key}
    structural = not any(line.kind == HEADING for line in lines)
    plain_level = _PARAGRAPH_TITLE_LEVEL if structural else max(
        (line.level or 1 for line in lines if line.kind == HEADING), default=1) + 1

    actions: list[ProposedAction] = []
    kept: list[FlatLine] = []
    seen: set[str] = set()
    open_level: int | None = None
    sections = duplicates = 0
    for line in lines:
        title = _title_of(line, structural)
        if open_level is not None:
            if title is None or title[1] > open_level:
                if title is None and _has_text(line):
                    action = parse_action_line(line.text)
                    if action.id in seen:
                        duplicates += 1
                    else:
                        seen.add(action.id)
                        actions.append(action)
                continue
            open_level = None
        open_level = _section_level(line, title, wanted, plain_level)
        if open_level is not None:
            sections += 1
            continue
        kept.append(line)

    if sections:
        logger.info("Proposed actions: %d line(s), %d actionable, %d duplicate(s) dropped",
                    len(actions), sum(1 for a in actions if a.actionable), duplicates)
    return actions, kept


def _heading_key(text: str) -> str:
    title = _text_prep().clean_heading(text)[0]
    return _NON_WORD_RE.sub(" ", title.casefold()).strip()


def _has_text(line: FlatLine) -> bool:
    return line.kind != DIVIDER and bool(_text_prep().strip_markdown(line.text))


def _title_of(line: FlatLine, structural: bool) -> tuple[str, int] | None:
    """(text, level) when the line is a heading.

    Heading blocks always are. A top-level paragraph is one when written as a
    markdown heading ("## Work") or, on a page without heading blocks, bold as
    a whole - the same titles text_prep reads as sections. A line with "|" is
    a proposal, never a title.
    """
    if line.kind == HEADING:
        return (line.text, line.level or 1) if _heading_key(line.text) else None
    if line.kind != PARAGRAPH or line.depth != 0 or "|" in line.text:
        return None
    text = line.text.strip()
    markdown = _MD_HEADING_RE.match(text)
    if markdown is not None:
        body = text[markdown.end():]
        return (body, len(markdown.group(1))) if _is_title_like(body) else None
    if structural and _WHOLE_BOLD_RE.match(text) and _is_title_like(text):
        return text, _PARAGRAPH_TITLE_LEVEL
    return None


def _is_title_like(text: str) -> bool:
    prep = _text_prep()
    title = prep.clean_heading(text)[0]
    return (bool(title) and len(title) <= _TITLE_MAX_CHARS and len(title.split()) <= _TITLE_MAX_WORDS
            and not prep.strip_markdown(text).endswith((".", "!", "?")))


def _section_level(line: FlatLine, title: tuple[str, int] | None, wanted: set[str],
                   plain_level: int) -> int | None:
    """The level of a "Proposed actions" heading, or None for any other line.

    A plain top-level paragraph that says just "Proposed actions:" also opens
    the section, ranked below every heading block.
    """
    if title is not None:
        return title[1] if _heading_key(title[0]) in wanted else None
    if line.kind == PARAGRAPH and line.depth == 0 and "|" not in line.text:
        if _heading_key(line.text) in wanted:
            return plain_level
    return None


# --------------------------------------------------------------------------
# ActionStore
# --------------------------------------------------------------------------

def _local_now() -> datetime:
    return datetime.now().astimezone()


def _aware(moment: datetime) -> datetime:
    return moment if moment.utcoffset() is not None else moment.astimezone()


class ActionStore:
    """Persisted decisions: %LOCALAPPDATA%\\briefing-reader\\actions.json  {id: {status, at, link, message}}.

    "failed" is remembered (for the card's message) but is not a decision: the
    proposal stays pending and can be approved again. A corrupt or unreadable
    file is logged and treated as empty; a failed write is logged and the
    decision is kept in memory for this run. Writes are atomic (temporary
    file + os.replace), so a crash never leaves half a file.
    """

    def __init__(self, path: Path, clock: Callable[[], datetime] = _local_now) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, str]] = _load_entries(self.path)

    def get(self, action_id: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(action_id)
            return dict(entry) if entry is not None else None

    def set(self, action_id: str, status: str, *, link: str = "", message: str = "") -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown action status {status!r}")
        entry = {"status": status, "at": self._clock().isoformat(timespec="seconds"),
                 "link": link or "", "message": message or ""}
        with self._lock:
            self._entries[action_id] = entry
            _write_entries(self.path, self._entries)
        logger.info("Action %s: %s", action_id, status)

    def is_decided(self, action_id: str) -> bool:
        entry = self.get(action_id)
        return entry is not None and entry["status"] in DECIDED_STATUSES

    def prune(self, max_age_days: int = 60) -> None:
        """Forget decisions older than ``max_age_days`` (and ones without a readable time)."""
        cutoff = _aware(self._clock()) - timedelta(days=max_age_days)
        with self._lock:
            old = [key for key, entry in self._entries.items() if not _newer_than(entry, cutoff)]
            for key in old:
                del self._entries[key]
            if old:
                _write_entries(self.path, self._entries)
        if old:
            logger.info("Forgot %d action decision(s) older than %d days", len(old), max_age_days)


def _newer_than(entry: dict[str, str], cutoff: datetime) -> bool:
    try:
        at = datetime.fromisoformat(entry.get("at", ""))
    except (TypeError, ValueError):
        return False
    return _aware(at) >= cutoff


def _load_entries(path: Path) -> dict[str, dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:
        logger.warning("Could not read %s (%s); starting with no saved decisions",
                       path, type(exc).__name__)
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("%s is not valid JSON; starting with no saved decisions", path)
        return {}
    if not isinstance(data, dict):
        logger.warning("%s is not a table of decisions; starting with no saved decisions", path)
        return {}
    entries: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        entry = _clean_entry(value)
        if entry is not None:
            entries[str(key)] = entry
    if len(entries) != len(data):
        logger.warning("Ignored %d malformed decision(s) in %s", len(data) - len(entries), path)
    return entries


def _clean_entry(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict) or value.get("status") not in STATUSES:
        return None
    return {name: value[name] if isinstance(value.get(name), str) else ""
            for name in ("status", "at", "link", "message")}


def _write_entries(path: Path, entries: dict[str, dict[str, str]]) -> bool:
    """Atomically replace ``path`` with ``entries`` as JSON; False (logged) on failure."""
    data = json.dumps(entries, indent=2, sort_keys=True) + "\n"
    part: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, part = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(part, path)
        part = None
        return True
    except OSError as exc:
        logger.warning("Could not save %s (%s); the decision is kept for this run only",
                       path, exc.strerror or type(exc).__name__)
        return False
    finally:
        if part is not None:
            try:
                os.unlink(part)
            except OSError:
                pass
