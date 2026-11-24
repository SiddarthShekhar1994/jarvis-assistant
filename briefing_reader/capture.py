"""Capture: what a picture, PDF, text or calendar file holds, as cards (no Qt, no network, nothing logged).

    Series                       a repeat Jarvis can send: FREQ DAILY / WEEKLY / MONTHLY / YEARLY with INTERVAL,
                                 BYDAY, BYMONTHDAY, BYMONTH, BYSETPOS, WKST, UNTIL or COUNT, and skipped dates
    series_rrule(series, all_day)
                                 the card's rrule: one RRULE line (UNTIL written the way actions.py writes it: a
                                 date for an all-day series, the end of that day "T235959Z" for a timed one, which
                                 gcal moves to local time) and one EXDATE line when dates are skipped (a timed
                                 series' in the calendar's wall time, sent with its TZID; an all-day one's as dates)
    check_rrule(text, to_wall=)  an RRULE line -> Series, or the reason Jarvis can't add it (CANT_REPEAT ...): only
                                 the parts above; anything else (BYHOUR, BYWEEKNO, HOURLY ...) is refused
    series_words(series, today)  "every 2 weeks on Tue and Thu until May 1 · 2 dates skipped"
    CapturedItem                 one event (or a whole series) or to-do found in a capture: what Claude's reading
                                 and the .ics parser both produce (decision 1: Jarvis builds every card and rule)
    to_card(item, label, today=) the item's card: a Calendar card (actions.make_calendar, so the same event captured,
                                 asked or briefed has one id) or a Todo card (standing.cards.todo_line, read by
                                 parse_action_line); source and mode "capture", the item's UID; the title, place and
                                 notes through ask.context.data() with any link taken out; "From <label>, p. 2" in
                                 the notes. An item Jarvis can't add is an information-only card with its reason.
    item_to_data / item_from_data
                                 an item as plain JSON values (capture_store keeps them) and back (None when damaged)

The limits (README "Capture") are the constants below; none is a setting.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .actions import MODE_CAPTURE, SOURCE_CAPTURE, ProposedAction, make_calendar, parse_action_line, with_error
from .ask.context import data

# ---- limits (README "Capture"; not settings) ----------------------------------------------------
MAX_CARDS_PER_CAPTURE = 50       # cards one capture makes ("later ones not shown")
MAX_KEPT_CARDS = 100             # capture cards kept in Approvals across captures
MAX_READ_ITEMS = 30              # items one Claude reading may give
MAX_PICTURES = 5                 # pictures one Claude reading is sent
MAX_PAGES = 20                   # PDF pages read
MAX_TEXT_CHARS = 40_000          # text read from a PDF or a text file
MAX_FILE_BYTES = 2_000_000       # a dropped file or a fetched calendar
MAX_COMPONENTS = 500             # events and to-dos in one calendar file
MAX_UPCOMING_DAYS = 400          # how far ahead the calendar is read for the duplicate check
TITLE_CAP = 200
WHERE_CAP = 300
NOTES_CAP = 500
LABEL_CAP = 80

PICTURE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
EXTENSIONS = PICTURE_EXTENSIONS + (".pdf", ".ics", ".txt")

EVENT, TODO_ITEM = "event", "todo"
FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
DAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR")

# Reasons on an information-only card (plain words; never the item's own text).
CANT_REPEAT = "Jarvis can't add this repeat"
CANT_REPEAT_ZONE = "Jarvis can't add this repeat: it is set in another time zone"
CANT_REPEAT_UTC = "Jarvis can't add this repeat: it is set in UTC"
CANT_REPEAT_DAY = "Jarvis can't add this repeat: it doesn't say which day of the month"
UNKNOWN_ZONE = "Jarvis can't tell this event's time zone"
ENDS_BEFORE = "The repeat ends before the event starts"
NO_DUE = "No due date: add it to your list yourself"
NO_DATE = "No date Jarvis could read"

_DAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_SHORT = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_LONG = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")
_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last", -2: "second to last"}
_SEPARATOR = " · "
_MAX_INTERVAL = 99
_MAX_COUNT = 999
_BY_DAY_RE = re.compile(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)")
_UNTIL_RE = re.compile(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?")
_LINK_RE = re.compile(r"(?:[a-z][a-z0-9+.-]{1,20}://|www\.)\S*", re.IGNORECASE)


# --------------------------------------------------------------------------
# Series
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Series:
    """A repeat Jarvis can send. ``by_day`` holds "TU" or, for a monthly or yearly one, "1MO" / "-1FR";
    ``exdates`` the skipped dates (datetimes in the calendar's wall time for a timed series, dates for an all-day
    one); ``until`` is a date (the last day an occurrence may start on), never with ``count``."""

    freq: str
    interval: int = 1
    by_day: tuple[str, ...] = ()
    by_month_day: tuple[int, ...] = ()
    by_month: tuple[int, ...] = ()
    by_set_pos: tuple[int, ...] = ()
    until: date | None = None
    count: int | None = None
    exdates: tuple[date | datetime, ...] = ()
    wkst: str = ""

    def problem(self) -> str:
        """Why Jarvis would not send this series ("" when he would)."""
        if self.freq not in FREQS:
            return CANT_REPEAT
        if not 1 <= self.interval <= _MAX_INTERVAL:
            return CANT_REPEAT
        if self.until is not None and self.count is not None:
            return CANT_REPEAT
        if self.count is not None and not 1 <= self.count <= _MAX_COUNT:
            return CANT_REPEAT
        for code in self.by_day:
            match = _BY_DAY_RE.fullmatch(code)
            if match is None:
                return CANT_REPEAT
            if match.group(1) is not None:
                number = int(match.group(1))
                if self.freq not in ("MONTHLY", "YEARLY") or number == 0 or abs(number) > 5:
                    return CANT_REPEAT
        if any(day == 0 or abs(day) > 31 for day in self.by_month_day) or self.freq == "WEEKLY" and self.by_month_day:
            return CANT_REPEAT
        if any(not 1 <= month <= 12 for month in self.by_month):
            return CANT_REPEAT
        if self.by_set_pos and (not self.by_day or any(pos == 0 or abs(pos) > 366 for pos in self.by_set_pos)):
            return CANT_REPEAT
        if self.wkst and self.wkst not in DAY_CODES:
            return CANT_REPEAT
        return ""


def series_rrule(series: Series, all_day: bool) -> str:
    """The card's rrule (see the module's docstring). ValueError for a series Jarvis would not send."""
    if series.problem():
        raise ValueError(series.problem())
    parts = [f"FREQ={series.freq}"]
    if series.interval > 1:
        parts.append(f"INTERVAL={series.interval}")
    if series.wkst:
        parts.append(f"WKST={series.wkst}")
    if series.by_day:
        parts.append("BYDAY=" + ",".join(series.by_day))
    if series.by_month_day:
        parts.append("BYMONTHDAY=" + ",".join(str(day) for day in series.by_month_day))
    if series.by_month:
        parts.append("BYMONTH=" + ",".join(str(month) for month in series.by_month))
    if series.by_set_pos:
        parts.append("BYSETPOS=" + ",".join(str(pos) for pos in series.by_set_pos))
    if series.until is not None:
        stamp = series.until.strftime("%Y%m%d")
        parts.append(f"UNTIL={stamp}" if all_day else f"UNTIL={stamp}T235959Z")
    elif series.count is not None:
        parts.append(f"COUNT={series.count}")
    rule = "RRULE:" + ";".join(parts)
    skipped = sorted(set(series.exdates), key=_sort_key)
    if skipped:
        if all_day:
            days = sorted({(value.date() if isinstance(value, datetime) else value) for value in skipped})
            rule += "\nEXDATE;VALUE=DATE:" + ",".join(day.strftime("%Y%m%d") for day in days)
        else:
            stamps = [value.strftime("%Y%m%dT%H%M%S") for value in skipped if isinstance(value, datetime)]
            if stamps:
                rule += "\nEXDATE:" + ",".join(stamps)
    return rule


def _sort_key(value: date | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.combine(value, datetime.min.time())


def _ints(text: str) -> tuple[int, ...]:
    values = []
    for part in text.split(","):
        part = part.strip()
        if not re.fullmatch(r"[+-]?\d{1,3}", part):
            raise ValueError(part)
        values.append(int(part))
    return tuple(values)


def check_rrule(text: str, *, to_wall: Callable[[datetime], datetime] | None = None) -> Series | str:
    """An RRULE ("RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20270501T065959Z", the prefix optional) -> Series, or the
    reason Jarvis can't add it. A date-time UNTIL becomes the day it falls on in the calendar's wall time
    (``to_wall`` turns an aware moment into it; this PC's local time when None; a floating one is taken as
    written)."""
    rule = str(text or "").strip()
    if rule.upper().startswith("RRULE:"):
        rule = rule[len("RRULE:"):]
    if not rule or "\n" in rule or len(rule) > 500:
        return CANT_REPEAT
    fields: dict[str, str] = {}
    for part in rule.split(";"):
        if not part.strip():
            continue
        name, sep, value = part.partition("=")
        name, value = name.strip().upper(), value.strip().upper()
        if not sep or not value or name in fields:
            return CANT_REPEAT
        fields[name] = value
    allowed = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYSETPOS", "UNTIL", "COUNT", "WKST"}
    if set(fields) - allowed or "FREQ" not in fields:
        return CANT_REPEAT
    try:
        interval = int(fields.get("INTERVAL", "1"))
        count = int(fields["COUNT"]) if "COUNT" in fields else None
        by_day = tuple(code.strip() for code in fields["BYDAY"].split(",")) if "BYDAY" in fields else ()
        by_month_day = _ints(fields["BYMONTHDAY"]) if "BYMONTHDAY" in fields else ()
        by_month = _ints(fields["BYMONTH"]) if "BYMONTH" in fields else ()
        by_set_pos = _ints(fields["BYSETPOS"]) if "BYSETPOS" in fields else ()
    except ValueError:
        return CANT_REPEAT
    until: date | None = None
    if "UNTIL" in fields:
        until = _until_day(fields["UNTIL"], to_wall)
        if until is None:
            return CANT_REPEAT
    series = Series(fields["FREQ"], interval, by_day, by_month_day, by_month, by_set_pos, until, count,
                    wkst=fields.get("WKST", ""))
    return series.problem() or series


def _until_day(value: str, to_wall: Callable[[datetime], datetime] | None) -> date | None:
    match = _UNTIL_RE.fullmatch(value)
    if match is None:
        return None
    try:
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if match.group(4) is None:
            return day
        moment = datetime(day.year, day.month, day.day, int(match.group(4)), int(match.group(5)),
                          int(match.group(6)))
    except ValueError:
        return None
    if match.group(7):
        aware = moment.replace(tzinfo=timezone.utc)
        try:
            wall = to_wall(aware) if to_wall is not None else aware.astimezone().replace(tzinfo=None)
        except (ValueError, OverflowError, OSError):
            return None
        return wall.date()
    return moment.date()


def _day_list(codes: tuple[str, ...]) -> str:
    names = [_DAY_SHORT[DAY_CODES.index(code)] for code in codes if code in DAY_CODES]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _nth(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _month_days(days: tuple[int, ...]) -> str:
    words = ["last day" if day == -1 else _nth(day) if day > 0 else f"{_nth(-day)} to last day" for day in days]
    if len(words) <= 1:
        return "the " + "".join(words)
    return "the " + ", ".join(words[:-1]) + " and " + words[-1]


def _ordinal_days(series: Series) -> str:
    """"the first Mon", "the last weekday", "the 2nd and 4th Tue"."""
    plain = [code for code in series.by_day if code in DAY_CODES]
    if series.by_set_pos:
        what = "weekday" if sorted(plain, key=DAY_CODES.index) == list(WEEKDAYS) else \
            "day" if len(plain) == 7 else _day_list(tuple(plain))
        positions = " and ".join(_ORDINALS.get(pos, _nth(pos)) for pos in series.by_set_pos)
        return f"the {positions} {what}"
    parts = []
    for code in series.by_day:
        match = _BY_DAY_RE.fullmatch(code)
        if match is None:
            continue
        number = int(match.group(1)) if match.group(1) else 0
        name = _DAY_SHORT[DAY_CODES.index(match.group(2))]
        parts.append(f"the {_ORDINALS.get(number, _nth(number))} {name}" if number else f"every {name}")
    return " and ".join(parts)


def series_words(series: Series, today: date) -> str:
    """"weekly on Tue and Thu until May 1", "every 2 weeks, 6 times · 2 dates skipped", "monthly on the
    first Mon", "weekdays", "yearly"."""
    freq, n = series.freq, series.interval
    unit = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}.get(freq, "time")
    base = {"DAILY": "daily", "WEEKLY": "weekly", "MONTHLY": "monthly", "YEARLY": "yearly"}.get(freq, "repeats")
    word = base if n == 1 else f"every {n} {unit}s"
    if freq == "WEEKLY" and series.by_day:
        if n == 1 and sorted(series.by_day, key=_day_index) == list(WEEKDAYS):
            word = "weekdays"
        else:
            word += " on " + _day_list(tuple(sorted(series.by_day, key=_day_index)))
    elif freq in ("MONTHLY", "YEARLY"):
        if series.by_month and freq == "YEARLY":
            word += " in " + " and ".join(_MONTH_LONG[month - 1] for month in series.by_month)
        if series.by_day:
            word += " on " + _ordinal_days(series)
        elif series.by_month_day:
            word += " on " + _month_days(series.by_month_day)
    elif freq == "DAILY" and series.by_day:
        word += " on " + _day_list(tuple(sorted(series.by_day, key=_day_index)))
    if series.until is not None:
        until = series.until
        day = f"{_MONTH_SHORT[until.month - 1]} {until.day}"
        word += f" until {day}, {until.year}" if until.year != today.year else f" until {day}"
    elif series.count is not None:
        word += f", {series.count} times"
    skipped = len(set(series.exdates))
    if skipped:
        word += f"{_SEPARATOR}{skipped} date{'s' if skipped != 1 else ''} skipped"
    return word


def _day_index(code: str) -> int:
    match = _BY_DAY_RE.fullmatch(code)
    return DAY_CODES.index(match.group(2)) if match else 7


# --------------------------------------------------------------------------
# CapturedItem -> card
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CapturedItem:
    """One event (a one-off or a whole series) or to-do found in a capture.

    An event has naive ``start``/``end`` in the calendar's wall time, or ``all_day_start`` and an inclusive
    ``all_day_end``; a to-do has ``due`` (a date, or a naive datetime). ``uid`` is the calendar file's UID ("" for
    what Claude read); ``where_in_source`` says where it was found ("p. 2"). ``problem`` makes it an
    information-only card that says why Jarvis can't add it."""

    kind: str
    title: str
    start: datetime | None = None
    end: datetime | None = None
    all_day_start: date | None = None
    all_day_end: date | None = None
    where: str = ""
    notes: str = ""
    series: Series | None = None
    due: date | datetime | None = None
    uid: str = ""
    where_in_source: str = ""
    problem: str = ""

    @property
    def first_day(self) -> date | None:
        if self.start is not None:
            return self.start.date()
        return self.all_day_start

    @property
    def last_day(self) -> date | None:
        """The last day of a one-off (None for a series or a to-do)."""
        if self.series is not None or self.kind != EVENT:
            return None
        if self.all_day_start is not None:
            return self.all_day_end or self.all_day_start
        if self.end is not None:
            return self.end.date()
        return self.start.date() if self.start is not None else None


def _no_links(text: str) -> str:
    """``text`` without any link (a capture never carries one onto a card or into the calendar)."""
    return " ".join(_LINK_RE.sub(" ", text or "").split())


def source_note(label: str, where_in_source: str = "") -> str:
    """"From syllabus.pdf, p. 2" ("From <label>" without a page)."""
    label = data(_no_links(label), LABEL_CAP) or "a capture"
    where = data(where_in_source, 20)
    return f"From {label}, {where}" if where else f"From {label}"


def to_card(item: CapturedItem, label: str, *, today: date | None = None) -> ProposedAction:
    """``item``'s card (see the module's docstring)."""
    today = today or date.today()
    title = data(_no_links(item.title), TITLE_CAP) or "(No title)"
    where = data(_no_links(item.where), WHERE_CAP)
    origin = source_note(label, item.where_in_source)
    extra = data(_no_links(item.notes), NOTES_CAP - len(origin) - 3)
    notes = f"{extra} - {origin}" if extra else origin
    if item.kind == TODO_ITEM:
        return _todo_card(item, title, notes)
    problem = item.problem
    repeat = rrule = ""
    all_day = item.all_day_start is not None
    if item.start is None and item.all_day_start is None:
        problem = problem or NO_DATE
    if item.series is not None and not problem:
        problem = item.series.problem()
        first = item.first_day
        if not problem and item.series.until is not None and first is not None and item.series.until < first:
            problem = ENDS_BEFORE
        if not problem:
            rrule = series_rrule(item.series, all_day)
            repeat = series_words(item.series, today)
    raw = _raw_line(title, item, where, notes)
    card = make_calendar(title, start=item.start, end=item.end, all_day_start=item.all_day_start,
                         all_day_end=item.all_day_end, repeat=repeat, rrule=rrule, where=where, notes=notes,
                         raw=raw)
    card = replace(card, source=SOURCE_CAPTURE, mode=MODE_CAPTURE, uid=data(item.uid, 300))
    return with_error(card, problem) if problem else card


def _raw_line(title: str, item: CapturedItem, where: str, notes: str) -> str:
    if item.start is not None:
        when = item.start.strftime("%Y-%m-%d %H:%M")
        if item.end is not None:
            when += "-" + item.end.strftime("%H:%M")
    elif item.all_day_start is not None:
        when = item.all_day_start.isoformat()
        if item.all_day_end is not None and item.all_day_end > item.all_day_start:
            when += f" to {item.all_day_end.isoformat()}"
    else:
        when = ""
    return f"Calendar: {title} | {when} | | {where} | {notes}"


def _todo_card(item: CapturedItem, title: str, notes: str) -> ProposedAction:
    from .standing.cards import todo_line   # standing.cards imports actions, as this module does

    due = item.due
    line = todo_line(title, due if due is not None else "")
    card = parse_action_line(line)
    card = replace(card, source=SOURCE_CAPTURE, mode=MODE_CAPTURE, uid=data(item.uid, 300), notes=notes)
    if due is None:
        return with_error(replace(card, title=title), item.problem or NO_DUE)
    if card.error:
        return with_error(card, card.error)
    return with_error(card, item.problem) if item.problem else card


# --------------------------------------------------------------------------
# Plain JSON values (capture_store)
# --------------------------------------------------------------------------

def _when_text(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="minutes")
    return value.isoformat()


def _when_value(text: Any, *, timed: bool | None) -> date | datetime | None:
    """ISO text -> datetime (with "T") or date; ValueError when it is not one of the expected kind."""
    if text is None:
        return None
    if not isinstance(text, str):
        raise ValueError("not text")
    value: date | datetime = datetime.fromisoformat(text) if "T" in text else date.fromisoformat(text)
    if isinstance(value, datetime) and value.tzinfo is not None:
        raise ValueError("aware")
    if timed is True and not isinstance(value, datetime) or timed is False and isinstance(value, datetime):
        raise ValueError("kind")
    return value


def item_to_data(item: CapturedItem) -> dict[str, Any]:
    series = None
    if item.series is not None:
        s = item.series
        series = {"freq": s.freq, "interval": s.interval, "by_day": list(s.by_day),
                  "by_month_day": list(s.by_month_day), "by_month": list(s.by_month), "by_set_pos": list(s.by_set_pos),
                  "until": _when_text(s.until), "count": s.count, "exdates": [_when_text(v) for v in s.exdates],
                  "wkst": s.wkst}
    return {"kind": item.kind, "title": item.title, "start": _when_text(item.start), "end": _when_text(item.end),
            "all_day_start": _when_text(item.all_day_start), "all_day_end": _when_text(item.all_day_end),
            "where": item.where, "notes": item.notes, "series": series, "due": _when_text(item.due), "uid": item.uid,
            "where_in_source": item.where_in_source, "problem": item.problem}


def _text(value: Any, cap: int) -> str:
    if not isinstance(value, str) or len(value) > cap:
        raise ValueError("text")
    return value


def _int_list(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list) or any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise ValueError("ints")
    return tuple(value)


def item_from_data(value: Any) -> CapturedItem | None:
    """A kept item read back; None when anything in it is not what item_to_data writes."""
    if not isinstance(value, Mapping):
        return None
    try:
        kind = value.get("kind")
        if kind not in (EVENT, TODO_ITEM):
            return None
        series = None
        raw_series = value.get("series")
        if raw_series is not None:
            if kind != EVENT or not isinstance(raw_series, Mapping):
                return None
            count = raw_series.get("count")
            interval = raw_series.get("interval")
            if count is not None and (isinstance(count, bool) or not isinstance(count, int)):
                return None
            if isinstance(interval, bool) or not isinstance(interval, int):
                return None
            by_day = raw_series.get("by_day")
            if not isinstance(by_day, list) or not all(isinstance(code, str) for code in by_day):
                return None
            exdates = raw_series.get("exdates")
            if not isinstance(exdates, list) or len(exdates) > 1000:
                return None
            until = _when_value(raw_series.get("until"), timed=False)
            series = Series(_text(raw_series.get("freq"), 10), interval, tuple(by_day),
                            _int_list(raw_series.get("by_month_day")), _int_list(raw_series.get("by_month")),
                            _int_list(raw_series.get("by_set_pos")), until, count,  # type: ignore[arg-type]
                            tuple(_when_value(v, timed=None) for v in exdates),  # type: ignore[misc]
                            _text(raw_series.get("wkst", ""), 2))
        start = _when_value(value.get("start"), timed=True)
        end = _when_value(value.get("end"), timed=True)
        first = _when_value(value.get("all_day_start"), timed=False)
        last = _when_value(value.get("all_day_end"), timed=False)
        due = _when_value(value.get("due"), timed=None)
        return CapturedItem(kind=kind, title=_text(value.get("title"), 2000), start=start,  # type: ignore[arg-type]
                            end=end, all_day_start=first, all_day_end=last,  # type: ignore[arg-type]
                            where=_text(value.get("where", ""), 2000), notes=_text(value.get("notes", ""), 4000),
                            series=series, due=due, uid=_text(value.get("uid", ""), 1000),
                            where_in_source=_text(value.get("where_in_source", ""), 40),
                            problem=_text(value.get("problem", ""), 300))
    except ValueError:
        return None


def is_past(item: CapturedItem, now: datetime) -> bool:
    """A one-off that has ended by ``now`` (naive wall time), or a series whose last day is behind ``now``'s day
    (only an UNTIL or a COUNT can say so; a COUNT series is walked by ``last_occurrence``)."""
    if item.kind != EVENT:
        return False
    today = now.date()
    if item.series is None:
        if item.all_day_start is not None:
            return (item.all_day_end or item.all_day_start) < today
        if item.start is None:
            return False
        end = item.end if item.end is not None and item.end > item.start else item.start + timedelta(hours=1)
        return end <= now
    last = last_occurrence(item)
    return last is not None and last < today


def last_occurrence(item: CapturedItem) -> date | None:
    """The day a series' last occurrence starts on, when an UNTIL or a COUNT ends it and it can be walked here; None
    for an endless series or one this simple walk can't follow (an ordinal or set position)."""
    series, first = item.series, item.first_day
    if series is None or first is None:
        return None
    if series.until is not None:
        return series.until
    if series.count is None:
        return None
    days = occurrence_days(series, first, limit=series.count)
    return days[-1] if days and len(days) == series.count else None


def occurrence_days(series: Series, first: date, *, limit: int = 1000, until: date | None = None) -> list[date]:
    """The days the series' occurrences start on, from ``first`` (always the first), in order, at most ``limit``
    of them and none after ``until`` (or the series' own UNTIL); [] for an ordinal or set-position series, which this
    walk does not follow. Skipped dates still count toward a COUNT (RFC 5545), and are left in."""
    if series.by_set_pos or any(not code.isalpha() for code in series.by_day) or \
            series.freq == "YEARLY" and series.by_day and not series.by_month:
        return []
    stop = min(d for d in (until, series.until) if d is not None) if (until or series.until) else None
    limit = min(limit, series.count) if series.count is not None else limit
    found: list[date] = []
    n = max(1, series.interval)
    wanted_days = {DAY_CODES.index(code) for code in series.by_day} or None
    months = set(series.by_month) or None
    for day in _candidate_days(series, first, n):
        if stop is not None and day > stop:
            break
        if len(found) >= limit:
            break
        if day < first:
            continue
        if wanted_days is not None and day.weekday() not in wanted_days:
            continue
        if months is not None and day.month not in months:
            continue
        if series.by_month_day and not _month_day_matches(day, series.by_month_day):
            continue
        found.append(day)
    return found


def _month_day_matches(day: date, wanted: tuple[int, ...]) -> bool:
    following = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    length = (following - timedelta(days=1)).day
    return any(day.day == (value if value > 0 else length + 1 + value) for value in wanted)


def _candidate_days(series: Series, first: date, n: int):
    """Every day that could hold an occurrence, in order (the BY parts are checked by the caller), for at most
    about 30 years."""
    horizon = first + timedelta(days=366 * 30)
    if series.freq == "DAILY":
        day = first
        while day <= horizon:
            yield day
            day += timedelta(days=n)
    elif series.freq == "WEEKLY":
        week_day = DAY_CODES.index(series.wkst) if series.wkst in DAY_CODES else 0
        week_start = first - timedelta(days=(first.weekday() - week_day) % 7)
        while week_start <= horizon:
            if series.by_day:
                for offset in range(7):
                    yield week_start + timedelta(days=offset)
            else:
                yield week_start + timedelta(days=(first.weekday() - week_day) % 7)
            week_start += timedelta(weeks=n)
    elif series.freq == "MONTHLY":
        year, month = first.year, first.month
        while date(year, month, 1) <= horizon:
            if series.by_month_day or series.by_day:
                day = date(year, month, 1)
                while day.month == month:
                    yield day
                    day += timedelta(days=1)
            else:
                try:
                    yield date(year, month, first.day)
                except ValueError:
                    pass   # RFC 5545: a 31st in a shorter month has no occurrence
            month += n
            while month > 12:
                month -= 12
                year += 1
    else:   # YEARLY
        year = first.year
        while year <= horizon.year:
            if series.by_month or series.by_month_day or series.by_day:
                day = date(year, 1, 1)
                while day.year == year:
                    if (series.by_month or series.by_month_day or day.month == first.month) and \
                            (series.by_month_day or series.by_day or day.day == first.day):
                        yield day
                    day += timedelta(days=1)
            else:
                try:
                    yield date(year, first.month, first.day)
                except ValueError:
                    pass
            year += n


# --------------------------------------------------------------------------
# What is new: the duplicate check and the capture's summary (part B)
# --------------------------------------------------------------------------

WAITS, ON_CALENDAR, DECIDED, PAST, CANT_ADD = "waits", "on_calendar", "decided", "past", "cant_add"
STATUS_WORDS = {WAITS: "Waits", ON_CALENDAR: "On your calendar", DECIDED: "Already decided", PAST: "Past",
                CANT_ADD: "Can't add"}
NOT_CHECKED = "Not checked against your calendar"
MAX_PAST_ROWS = 10


@dataclass(frozen=True)
class Row:
    """One line of the Capture frame: the status, the item's title and when, and a reason (a CAN'T ADD's)."""

    status: str
    title: str
    when: str = ""
    reason: str = ""
    card_id: str = ""
    repeat: str = ""         # a series' repeat words (the frame's second line)


@dataclass
class Summary:
    """What a capture found: the frame's rows, the cards for Approvals (the new ones and the information-only ones),
    the counts the frame and the log show, and whether the calendar was read for the duplicate check."""

    label: str
    kind: str
    rows: list[Row]
    cards: list[tuple[CapturedItem, ProposedAction]]
    counts: dict[str, int]
    checked: bool = True
    note: str = ""


def check_span(items: list[CapturedItem], now: datetime) -> tuple[datetime, datetime] | None:
    """The span one list_events reads for the duplicate check: from the start of today (or the first one-off's
    day, if later) to the last day anything may be on the calendar, at most MAX_UPCOMING_DAYS ahead; None when no
    event needs it."""
    today = now.date()
    limit = today + timedelta(days=MAX_UPCOMING_DAYS)
    firsts: list[date] = []
    lasts: list[date] = []
    for item in items:
        if item.kind != EVENT or item.first_day is None:
            continue
        firsts.append(max(item.first_day, today))
        if item.series is None:
            lasts.append(item.last_day or item.first_day)
        else:
            lasts.append(last_occurrence(item) or limit)
    if not firsts:
        return None
    first = max(today, min(firsts))
    last = min(limit, max(lasts))
    if last < first:
        return None
    return (datetime.combine(first, datetime.min.time()), datetime.combine(last + timedelta(days=1),
                                                                         datetime.min.time()))


def _event_wall(event: Any, zone: str) -> datetime | None:
    start = getattr(event, "start", None)
    if not isinstance(start, datetime):
        return None
    from . import gcal

    return gcal.wall_in_zone(start, zone).replace(second=0, microsecond=0) if zone else \
        start.replace(tzinfo=None, second=0, microsecond=0)


def _same_title(event: Any, title: str) -> bool:
    return " ".join(str(getattr(event, "title", "") or "").casefold().split()) == " ".join(title.casefold().split())


def on_calendar(item: CapturedItem, card: ProposedAction, events: list[Any], zone: str) -> bool:
    """True when ``item`` is on the calendar already: by its UID (the event's iCalUID or Jarvis's capture UID), or by
    its title and start (a series: an occurrence on one of its days at the same time)."""
    if item.kind != EVENT:
        return False
    uid = card.uid
    if uid and any(uid in (getattr(event, "ical_uid", ""), getattr(event, "capture_uid", "")) for event in events):
        return True
    candidates = [event for event in events if _same_title(event, card.title)]
    if not candidates:
        return False
    if item.series is None:
        if item.start is not None:
            start = item.start.replace(second=0, microsecond=0)
            return any(_event_wall(event, zone) == start for event in candidates)
        return any(getattr(event, "all_day_start", None) == item.all_day_start for event in candidates)
    first = item.first_day
    if first is None:
        return False
    days_seen = [_event_day(event, zone) for event in candidates]
    last_seen = max((day for day in days_seen if day is not None), default=first)
    walk = set(occurrence_days(item.series, first, limit=100_000, until=last_seen))   # from DTSTART to the last event
    days = {DAY_CODES.index(code) for code in item.series.by_day if code in DAY_CODES} or {first.weekday()}
    for event in candidates:
        if item.start is not None:
            wall = _event_wall(event, zone)
            if wall is None or wall.date() < first or wall.time() != item.start.replace(second=0).time():
                continue
            day = wall.date()
        else:
            day = getattr(event, "all_day_start", None)
            if not isinstance(day, date) or day < first:
                continue
        if (day in walk) if walk else (day.weekday() in days):
            return True
    return False


def mark_known(entries: list[tuple[CapturedItem, ProposedAction]], events: list[Any] | None,
               decided: Callable[[str], bool], *, zone: str) -> list[str]:
    """Each (item, card)'s status: CANT_ADD (an information-only card), DECIDED (its id is decided in actions.json),
    ON_CALENDAR (on_calendar; only when ``events`` could be read) or WAITS (new)."""
    statuses = []
    for item, card in entries:
        if card.error:
            statuses.append(CANT_ADD)
        elif decided(card.id):
            statuses.append(DECIDED)
        elif events is not None and on_calendar(item, card, events, zone):
            statuses.append(ON_CALENDAR)
        else:
            statuses.append(WAITS)
    return statuses


def _event_day(event: Any, zone: str) -> date | None:
    wall = _event_wall(event, zone)
    if wall is not None:
        return wall.date()
    day = getattr(event, "all_day_start", None)
    return day if isinstance(day, date) else None


def _when(item: CapturedItem, label: str, today: date) -> str:
    """The item's day and time ("Tue Dec 1 · 3:00-4:30 PM"; a to-do's "Due Tue Dec 1"), without its repeat."""
    try:
        card = to_card(replace(item, problem="", series=None), label, today=today)
    except ValueError:
        return ""
    return card.describe(today) if not card.error else ""


def _repeat(item: CapturedItem, today: date) -> str:
    if item.series is None or item.series.problem():
        return ""
    return series_words(item.series, today)


def summarize(label: str, kind: str, items: list[CapturedItem], *, past: list[CapturedItem] = (),  # type: ignore[assignment]
              events: list[Any] | None, decided: Callable[[str], bool], zone: str, today: date,
              cancelled: int = 0, more: int = 0, unreadable: int = 0) -> Summary:
    """The capture's Summary: every item marked new (WAITS), on your calendar, already decided or can't add, the past
    ones listed (up to MAX_PAST_ROWS) and counted. ``events`` None: the calendar could not be read (the cards are
    still made; the note says so)."""
    rows: list[Row] = []
    cards: list[tuple[CapturedItem, ProposedAction]] = []
    counts = {WAITS: 0, ON_CALENDAR: 0, DECIDED: 0, PAST: len(past), CANT_ADD: 0, "cancelled": cancelled,
              "more": more, "unreadable": unreadable}
    entries = [(item, to_card(item, label, today=today)) for item in items[:MAX_CARDS_PER_CAPTURE]]
    for (item, card), status in zip(entries, mark_known(entries, events, decided, zone=zone)):
        counts[status] += 1
        if status in (WAITS, CANT_ADD):
            cards.append((item, card))
        rows.append(Row(status, card.title, _when(item, label, today), card.error if status == CANT_ADD else "",
                        card.id, _repeat(item, today)))
    for item in list(past)[:MAX_PAST_ROWS]:
        title = data(_no_links(item.title), TITLE_CAP) or "(No title)"
        rows.append(Row(PAST, title, _when(item, label, today), repeat=_repeat(item, today)))
    counts["found"] = len(items) + len(past) + cancelled + more + unreadable
    counts["cards"] = len(cards)
    return Summary(label, kind, rows, cards, counts, checked=events is not None,
                   note="" if events is not None or not any(i.kind == EVENT for i in items) else NOT_CHECKED)
