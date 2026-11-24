"""Calendar files (.ics) read on this PC with the standard library (no Qt, no network, never sent to Claude).

    parse_ics(content, *, zone, now, windows_zone=)
                                 an iCalendar file -> IcsResult: CapturedItems for the events (a whole series is one
                                 item) and to-dos still to come, the past and cancelled ones counted (and named for the
                                 frame), and the problem when the file as a whole is refused (too large, not a
                                 calendar, too many entries)

What is read: line unfolding, parameters (quoted ones too), the text escapes; VCALENDAR with METHOD, VEVENT, VTODO
and VTIMEZONE (only its TZID and an X-LIC-LOCATION); DTSTART / DTEND / DURATION, VALUE=DATE, "Z" times, floating
times and TZID (an IANA name, or a Windows name through ``windows_zone``: the real one asks Windows' ICU, icu.dll,
ucal_getTimeZoneIDForWindowsID; an unknown zone makes the entry an information-only card); RRULE through
capture.check_rrule; EXDATE; RECURRENCE-ID (a moved occurrence is skipped in its series and becomes its own one-off;
a cancelled one is skipped); STATUS:CANCELLED and METHOD:CANCEL propose nothing; a UID that comes twice keeps its
highest SEQUENCE. A one-off converts to the calendar's wall time; a series set in another time zone, in UTC or in an
unknown zone is information-only (decision 9). Limits: MAX_FILE_BYTES, MAX_COMPONENTS, MAX_CARDS_PER_CAPTURE (later
ones counted as "not shown").

Nothing here is logged.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import gcal
from .capture import (CANT_REPEAT, CANT_REPEAT_UTC, CANT_REPEAT_ZONE, EVENT, MAX_CARDS_PER_CAPTURE, MAX_COMPONENTS,
                      MAX_FILE_BYTES, TODO_ITEM, UNKNOWN_ZONE, CapturedItem, Series, check_rrule, is_past)

TOO_LARGE = "too_large"
NOT_CALENDAR = "not_calendar"
TOO_MANY = "too_many"
PROBLEM_WORDS = {TOO_LARGE: "This calendar file is over 2 MB", NOT_CALENDAR: "This is not a calendar file",
                 TOO_MANY: "This calendar file has over 500 entries"}

_DATE_RE = re.compile(r"(\d{4})(\d{2})(\d{2})")
_DATE_TIME_RE = re.compile(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)")
_DURATION_RE = re.compile(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")
_NAME_RE = re.compile(r"[A-Za-z0-9-]+")
_MAX_TEXT = 4000
_DEFAULT_TIMED = timedelta(hours=1)


@dataclass
class IcsResult:
    items: list[CapturedItem] = field(default_factory=list)      # to come: each becomes a card
    past: list[CapturedItem] = field(default_factory=list)       # ended one-offs, finished series, done to-dos
    cancelled: int = 0
    unreadable: int = 0                                          # entries without a usable start or with bad dates
    more: int = 0                                                # past MAX_CARDS_PER_CAPTURE: not shown
    problem: str = ""                                            # TOO_LARGE / NOT_CALENDAR / TOO_MANY


@dataclass
class _Prop:
    name: str
    params: dict[str, str]
    value: str


@dataclass
class _Component:
    kind: str
    props: list[_Prop] = field(default_factory=list)

    def first(self, name: str) -> _Prop | None:
        return next((prop for prop in self.props if prop.name == name), None)

    def all(self, name: str) -> list[_Prop]:
        return [prop for prop in self.props if prop.name == name]

    def text(self, name: str) -> str:
        prop = self.first(name)
        return " ".join(_unescape(prop.value).split()) if prop is not None else ""


# --------------------------------------------------------------------------
# Lines
# --------------------------------------------------------------------------

def _decode(content: bytes | str) -> str:
    if isinstance(content, str):
        return content
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in re.split(r"\r\n|\n|\r", text):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw.strip():
            lines.append(raw)
    return lines


def _split_line(line: str) -> _Prop | None:
    """NAME;PARAM=a;PARAM="b:c":VALUE -> _Prop (None for a line that is not one)."""
    quoted = False
    head_end = -1
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            head_end = index
            break
    if head_end <= 0:
        return None
    head, value = line[:head_end], line[head_end + 1:]
    parts: list[str] = []
    current, quoted = "", False
    for char in head:
        if char == '"':
            quoted = not quoted
            continue
        if char == ";" and not quoted:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    name = parts[0].strip().upper()
    if not _NAME_RE.fullmatch(name):
        return None
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, sep, val = part.partition("=")
        if sep:
            params[key.strip().upper()] = val.strip()
    return _Prop(name, params, value)


def _unescape(value: str) -> str:
    out, index = [], 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            out.append("\n" if nxt in "nN" else nxt)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)[:_MAX_TEXT]


def _components(lines: list[str]) -> tuple[list[_Component], str, int, bool]:
    """(the VEVENT / VTODO / VTIMEZONE components, the calendar's METHOD, how many components in all, whether a
    VCALENDAR was there). Nested components (VALARM, a VTIMEZONE's STANDARD / DAYLIGHT) are passed over."""
    found: list[_Component] = []
    stack: list[_Component] = []
    method, seen, calendar = "", 0, False
    for line in lines:
        prop = _split_line(line)
        if prop is None:
            continue
        if prop.name == "BEGIN":
            kind = prop.value.strip().upper()
            if kind == "VCALENDAR":
                calendar = True
                continue
            seen += 1
            stack.append(_Component(kind))
            continue
        if prop.name == "END":
            kind = prop.value.strip().upper()
            if kind == "VCALENDAR":
                continue
            if stack and stack[-1].kind == kind:
                component = stack.pop()
                if not stack and kind in ("VEVENT", "VTODO", "VTIMEZONE"):
                    found.append(component)
            continue
        if not stack:
            if prop.name == "METHOD":
                method = prop.value.strip().upper()
            continue
        if len(stack) == 1:
            stack[0].props.append(prop)
    return found, method, seen, calendar


# --------------------------------------------------------------------------
# Time zones
# --------------------------------------------------------------------------

_icu_windows: Any = None
_icu_windows_lock = threading.Lock()


def windows_zone_id(name: str) -> str | None:
    """The IANA zone for a Windows zone name ("Pacific Standard Time" -> "America/Los_Angeles"), from Windows' ICU
    (icu.dll ucal_getTimeZoneIDForWindowsID: a read, nothing changes); None when it can't tell."""
    global _icu_windows
    import os

    if os.name != "nt" or not name or len(name) > 100:
        return None
    with _icu_windows_lock:
        if _icu_windows is None:
            _icu_windows = False
            try:
                import ctypes

                icu = ctypes.WinDLL("icu")
                call = icu.ucal_getTimeZoneIDForWindowsID
                call.argtypes = [ctypes.c_wchar_p, ctypes.c_int32, ctypes.c_char_p, ctypes.c_wchar_p, ctypes.c_int32,
                                 ctypes.POINTER(ctypes.c_int)]
                call.restype = ctypes.c_int32
                _icu_windows = call
            except (OSError, AttributeError):
                _icu_windows = False
        call = _icu_windows
    if not call:
        return None
    import ctypes

    buffer = ctypes.create_unicode_buffer(128)
    status = ctypes.c_int(0)
    try:
        length = call(name, len(name), None, buffer, len(buffer), ctypes.byref(status))
    except (OSError, ValueError):
        return None
    if status.value > 0 or not 0 < length < len(buffer):
        return None
    return buffer.value[:length] or None


def _aware_in(wall: datetime, zone: str) -> datetime | None:
    """The instant the wall time ``wall`` names in the IANA ``zone`` (None when the zone can't be known)."""
    try:
        from zoneinfo import ZoneInfo

        return wall.replace(tzinfo=ZoneInfo(zone))
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError, a missing database
        pass
    guess = wall.replace(tzinfo=timezone.utc)
    offset = gcal.zone_offset(zone, guess)
    if offset is None:
        return None
    offset = gcal.zone_offset(zone, guess - offset) or offset
    return (wall - offset).replace(tzinfo=timezone.utc)


def _same_zone(first: str, second: str, moment: datetime) -> bool:
    """True when the two IANA zones keep the same clock now and half a year from ``moment``."""
    if first == second:
        return True
    for probe in (moment, moment + timedelta(days=182)):
        aware = probe.replace(tzinfo=timezone.utc)
        one, two = gcal.zone_offset(first, aware), gcal.zone_offset(second, aware)
        if one is None or two is None or one != two:
            return False
    return True


class _Zones:
    def __init__(self, zone: str, defined: dict[str, str], windows_zone: Callable[[str], str | None]) -> None:
        self.zone = zone
        self._defined = defined
        self._windows = windows_zone
        self._cache: dict[str, str | None] = {}

    def resolve(self, tzid: str) -> str | None:
        """A TZID as an IANA zone name (None when unknown)."""
        name = tzid.strip().strip('"')
        if name.startswith("/"):        # "/mozilla.org/20050126_1/America/New_York"-style ids
            name = "/".join(name.split("/")[-2:])
        if name in self._cache:
            return self._cache[name]
        found: str | None = None
        location = self._defined.get(name, "")
        for candidate in (name, location):
            if candidate and gcal.zone_offset(candidate, datetime(2026, 1, 1, tzinfo=timezone.utc)) is not None:
                found = candidate
                break
        if found is None:
            try:
                mapped = self._windows(name)
            except Exception:  # noqa: BLE001 - an injected mapper's failure means "unknown"
                mapped = None
            if mapped and gcal.zone_offset(mapped, datetime(2026, 1, 1, tzinfo=timezone.utc)) is not None:
                found = mapped
        self._cache[name] = found
        return found


@dataclass(frozen=True)
class _When:
    value: date | datetime          # a date (all day), or a naive wall time in the calendar's zone
    kind: str                       # "date", "floating", "utc", "zone"
    zone: str = ""                  # the IANA zone of a "zone" value
    unknown: bool = False           # a TZID Jarvis could not tell


def _read_when(prop: _Prop | None, zones: _Zones) -> _When | None:
    if prop is None:
        return None
    raw = prop.value.strip().split(",")[0]
    return _read_one(raw, prop.params, zones)


def _read_one(raw: str, params: dict[str, str], zones: _Zones) -> _When | None:
    raw = raw.strip()
    if params.get("VALUE", "").upper() == "DATE" or _DATE_RE.fullmatch(raw):
        match = _DATE_RE.fullmatch(raw[:8]) if len(raw) >= 8 else None
        if match is None:
            return None
        try:
            return _When(date(int(match.group(1)), int(match.group(2)), int(match.group(3))), "date")
        except ValueError:
            return None
    match = _DATE_TIME_RE.fullmatch(raw)
    if match is None:
        return None
    try:
        wall = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)),
                        int(match.group(5)), min(int(match.group(6)), 59))
    except ValueError:
        return None
    if match.group(7):
        return _When(gcal.wall_in_zone(wall.replace(tzinfo=timezone.utc), zones.zone), "utc")
    tzid = params.get("TZID", "")
    if not tzid:
        return _When(wall, "floating")
    name = zones.resolve(tzid)
    if name is None:
        return _When(wall, "zone", unknown=True)
    aware = _aware_in(wall, name)
    if aware is None:
        return _When(wall, "zone", unknown=True)
    return _When(gcal.wall_in_zone(aware, zones.zone).replace(second=0, microsecond=0), "zone", zone=name)


def _duration(text: str) -> timedelta | None:
    match = _DURATION_RE.fullmatch(text.strip().upper())
    if match is None or text.strip().upper() in ("P", "PT"):
        return None
    weeks, days, hours, minutes, seconds = (int(part) if part else 0 for part in match.groups()[1:])
    span = timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    return -span if match.group(1) == "-" else span


# --------------------------------------------------------------------------
# Entries
# --------------------------------------------------------------------------

def _sequence(component: _Component) -> int:
    prop = component.first("SEQUENCE")
    try:
        return int(prop.value.strip()) if prop is not None else 0
    except ValueError:
        return 0


def _key(when: _When | None) -> str:
    if when is None:
        return ""
    return when.value.isoformat() if not isinstance(when.value, datetime) else when.value.isoformat(timespec="minutes")


def parse_ics(content: bytes | str, *, zone: str, now: datetime,
              windows_zone: Callable[[str], str | None] = windows_zone_id) -> IcsResult:
    """The calendar file as items (see the module's docstring). ``zone`` is the calendar's IANA zone and ``now`` the
    naive wall time there."""
    result = IcsResult()
    size = len(content.encode("utf-8") if isinstance(content, str) else content)
    if size > MAX_FILE_BYTES:
        result.problem = TOO_LARGE
        return result
    components, method, seen, calendar = _components(_unfold(_decode(content)))
    if not calendar:
        result.problem = NOT_CALENDAR
        return result
    if seen > MAX_COMPONENTS:
        result.problem = TOO_MANY
        return result
    defined = {}
    for component in components:
        if component.kind == "VTIMEZONE":
            tzid = component.text("TZID")
            if tzid:
                defined[tzid] = component.text("X-LIC-LOCATION")
    zones = _Zones(zone, defined, windows_zone)
    entries = [c for c in components if c.kind in ("VEVENT", "VTODO")]
    if method == "CANCEL":
        result.cancelled = len(entries)
        return result
    # A UID that comes twice keeps its highest SEQUENCE (the last one of equal SEQUENCE).
    masters: dict[str, _Component] = {}
    overrides: dict[tuple[str, str], _Component] = {}
    loose: list[_Component] = []
    for index, component in enumerate(entries):
        uid = component.text("UID")
        recurrence = _read_when(component.first("RECURRENCE-ID"), zones)
        if not uid:
            loose.append(component)
            continue
        if recurrence is not None:
            key = (uid, _key(recurrence))
            if key not in overrides or _sequence(component) >= _sequence(overrides[key]):
                overrides[key] = component
            continue
        uid_key = f"{component.kind}\x1f{uid}"
        if uid_key not in masters or _sequence(component) >= _sequence(masters[uid_key]):
            masters[uid_key] = component
    items: list[CapturedItem] = []
    master_uids = {key.split("\x1f", 1)[1]: comp for key, comp in masters.items() if comp.kind == "VEVENT"}
    for component in [*masters.values(), *loose]:
        if component.kind == "VTODO":
            _add_todo(component, zones, result, items, now)
            continue
        uid = component.text("UID")
        moved = [(key[1], comp) for key, comp in overrides.items() if key[0] == uid] if uid else []
        if moved and not component.all("RRULE"):
            # A one-off with an updated copy (RECURRENCE-ID, no RRULE on the master): the newest copy replaces it.
            newest = max(moved, key=lambda pair: _sequence(pair[1]))[1]
            result.unreadable += len(moved) - 1
            _add_event(newest, [], zones, result, items, now, own_occurrence=True, uid=uid)
            continue
        _add_event(component, moved, zones, result, items, now)
    # An occurrence whose series is not in the file is a one-off of its own.
    for (uid, _), component in overrides.items():
        if uid not in master_uids:
            _add_event(component, [], zones, result, items, now, own_occurrence=True)
    items.sort(key=lambda item: _sort_key(item))
    result.more = max(0, len(items) - MAX_CARDS_PER_CAPTURE)
    result.items = items[:MAX_CARDS_PER_CAPTURE]
    return result


def _sort_key(item: CapturedItem) -> datetime:
    if item.start is not None:
        return item.start
    day = item.all_day_start or (item.due if isinstance(item.due, date) and not isinstance(item.due, datetime)
                                 else None)
    if isinstance(item.due, datetime):
        return item.due
    return datetime.combine(day, datetime.min.time()) if day is not None else datetime.max


def _cancelled(component: _Component) -> bool:
    return component.text("STATUS").upper() == "CANCELLED"


def _add_todo(component: _Component, zones: _Zones, result: IcsResult, items: list[CapturedItem],
              now: datetime) -> None:
    status = component.text("STATUS").upper()
    if status == "CANCELLED":
        result.cancelled += 1
        return
    when = _read_when(component.first("DUE"), zones)
    due = None if when is None or when.unknown else when.value
    item = CapturedItem(TODO_ITEM, component.text("SUMMARY"), due=due, uid=component.text("UID"),
                        notes=component.text("DESCRIPTION"))
    if status == "COMPLETED" or due is not None and _due_day(due) < now.date():
        result.past.append(item)
        return
    items.append(item)


def _due_day(due: date | datetime) -> date:
    return due.date() if isinstance(due, datetime) else due


def _add_event(component: _Component, moved: list[tuple[str, _Component]], zones: _Zones, result: IcsResult,
               items: list[CapturedItem], now: datetime, *, own_occurrence: bool = False, uid: str = "") -> None:
    if _cancelled(component):
        result.cancelled += 1
        return
    start = _read_when(component.first("DTSTART"), zones)
    if start is None:
        result.unreadable += 1
        return
    title = component.text("SUMMARY")
    own_uid = uid
    uid = component.text("UID")
    base = CapturedItem(EVENT, title, where=component.text("LOCATION"), notes=component.text("DESCRIPTION"),
                        uid=uid if not own_occurrence else (own_uid or _occurrence_uid(uid, component)))
    end = _read_when(component.first("DTEND"), zones)
    span = _duration(component.first("DURATION").value) if component.first("DURATION") is not None else None
    if start.kind == "date":
        first = start.value
        last = first
        if end is not None and end.kind == "date" and end.value > first:
            last = end.value - timedelta(days=1)
        elif span is not None and span.days > 1:
            last = first + timedelta(days=span.days - 1)
        item = replace(base, all_day_start=first, all_day_end=last)  # type: ignore[arg-type]
    else:
        begin = start.value
        finish: datetime | None = None
        if end is not None and isinstance(end.value, datetime):
            finish = end.value
        elif span is not None:
            finish = begin + span  # type: ignore[operator]
        if finish is None or finish <= begin:  # type: ignore[operator]
            finish = begin + _DEFAULT_TIMED  # type: ignore[operator]
        item = replace(base, start=begin, end=finish)  # type: ignore[arg-type]
    if start.unknown or (end is not None and end.unknown):
        item = replace(item, problem=UNKNOWN_ZONE)
    rules = component.all("RRULE")
    if rules and not own_occurrence:
        item = _with_series(item, component, rules, start, moved, zones, result, items, now)
    if is_past(item, now):
        result.past.append(item)
        return
    items.append(item)


def _with_series(item: CapturedItem, component: _Component, rules: list[_Prop], start: _When,
                 moved: list[tuple[str, _Component]], zones: _Zones, result: IcsResult, items: list[CapturedItem],
                 now: datetime) -> CapturedItem:
    """``item`` with its series (or the reason it can't have one); moved occurrences become one-offs of their own (also
    when the series itself can't be added). A date-only EXDATE of a timed series skips the occurrence at the series'
    start time; an UNTIL whose time on its day is before that start time ends the day before (a "this and following"
    split). What can't be read is counted (IcsResult.unreadable), never dropped silently."""
    all_day = start.kind == "date"
    begin = start.value.time() if isinstance(start.value, datetime) else None
    skipped: list[date | datetime] = []
    for _recurrence_key, override in moved:
        recurrence = _read_when(override.first("RECURRENCE-ID"), zones)
        if recurrence is None or recurrence.unknown:
            result.unreadable += 1
            continue
        skipped.append(_as_kind(recurrence.value, all_day, begin))
        if not _cancelled(override):
            _add_event(override, [], zones, result, items, now, own_occurrence=True)
        else:
            result.cancelled += 1
    problem = ""
    if len(rules) > 1 or component.first("RDATE") is not None or component.first("EXRULE") is not None:
        problem = CANT_REPEAT
    elif start.unknown:
        problem = UNKNOWN_ZONE
    elif start.kind == "utc":
        problem = CANT_REPEAT_UTC
    elif start.kind == "zone" and not _same_zone(start.zone, zones.zone, now):
        problem = CANT_REPEAT_ZONE
    series_or_reason = check_rrule(rules[0].value, to_wall=lambda m: gcal.wall_in_zone(m, zones.zone))
    if isinstance(series_or_reason, str):
        problem = problem or series_or_reason
        return replace(item, problem=item.problem or problem)
    series: Series = series_or_reason
    for prop in component.all("EXDATE"):
        for raw in prop.value.split(","):
            when = _read_one(raw, prop.params, zones)
            if when is None or when.unknown:
                result.unreadable += 1
                continue
            skipped.append(_as_kind(when.value, all_day, begin))
    if series.until is not None and begin is not None:
        cut = _until_wall(rules[0].value, zones)
        if cut is not None and cut.date() == series.until and cut.time() < begin:
            series = replace(series, until=series.until - timedelta(days=1))
    series = replace(series, exdates=tuple(dict.fromkeys(skipped)))
    return replace(item, series=series, problem=item.problem or problem)


def _as_kind(value: date | datetime, all_day: bool, begin: Any = None) -> date | datetime:
    """A skipped date as the series has them: a date for an all-day series; for a timed one a wall time, a date-only
    value taken at the series' start time ``begin``."""
    if all_day:
        return value.date() if isinstance(value, datetime) else value
    if isinstance(value, datetime):
        return value.replace(second=0, microsecond=0)
    return datetime.combine(value, begin or datetime.min.time())


_UNTIL_TIME_RE = re.compile(r"UNTIL=(\d{8}T\d{6}Z?)", re.IGNORECASE)


def _until_wall(rule: str, zones: _Zones) -> datetime | None:
    """A date-time UNTIL as the calendar's wall time (None for a date-only or unreadable one)."""
    match = _UNTIL_TIME_RE.search(rule)
    if match is None:
        return None
    when = _read_one(match.group(1), {}, zones)
    return when.value if when is not None and isinstance(when.value, datetime) else None


def _occurrence_uid(uid: str, component: _Component) -> str:
    """A moved occurrence's own UID ("<series UID>;<its RECURRENCE-ID>"), so a later capture of the file finds it."""
    recurrence = component.first("RECURRENCE-ID")
    if not uid or recurrence is None:
        return ""
    return f"{uid};{recurrence.value.strip()}"
