"""Instant answers from the calendars (round 2; owner decisions 2026-11-11): the reply to an instant.CalendarAsk from
what the app read of every account's main calendar for today and tomorrow. Pure: no Qt, no Google, nothing logged.

    reply(ask, read, *, now, address="sir", hour24=False) -> InstantReply | None

``CalendarRead`` is one read: each account's events (gcal.CalendarEvent: title, start and end or all-day dates,
location, and whether the owner declined it or it shows him as free), the accounts that couldn't be read (PROBLEM_*)
and the range read; ``covers(now)`` says whether it holds today and tomorrow as they are now.

What is shown (``text``: the conversation; ``brief``: the bar's one line) names each event: its title, its time, its
place and - with two or more accounts - which calendar. What is said (``speech``) holds times, counts and how soon
only, never a title or a place, while the owner's voice is Microsoft's online voice (owner decision 2026-11-11);
``say_titles`` (the voice is made on this PC: owner decision 2026-11-12) names the event the answer is about by its
title too (persona.spoken_title: links, addresses and ids out, cut at a word) - places stay on screen. Events he
declined are left out; all-day events and events shown as "free" are listed but never make him busy. An account that
couldn't be read is said plainly, and no answer says "free" or "nothing" without saying which calendars it covers.
``reply`` is None only when the read does not cover what was asked (the app reads again).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from .instant import (CAL_COUNT, CAL_DAY, CAL_FREE, CAL_MORE, CAL_NEXT, KIND_CALENDAR, CalendarAsk, InstantReply,
                      local_at, spoken_clock, wall)
from .persona import spoken_title, with_address
from .text_prep import clock_parts, format_time

PROBLEM_SIGNED_OUT, PROBLEM_SETUP, PROBLEM_ERROR = "signedout", "setup", "error"
SOURCE = "From Google Calendar"
PLACE_MAX = 60
_SHOWN_PROBLEMS = {PROBLEM_SIGNED_OUT: "its sign-in has expired or was removed, so it needs signing in again",
                   PROBLEM_SETUP: "it isn't set up",
                   PROBLEM_ERROR: "Google didn't answer; try again in a moment"}
_SAID_PROBLEMS = {PROBLEM_SIGNED_OUT: "it needs signing in again", PROBLEM_SETUP: "it isn't set up",
                  PROBLEM_ERROR: "Google didn't answer"}
_ONE_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class CalendarRead:
    """One read of the calendars: ``events`` (alias -> its events, any order), ``problems`` (alias -> PROBLEM_*, the
    accounts that couldn't be read) and the range read (aware). Its repr holds counts only."""

    events: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    problems: Mapping[str, str] = field(default_factory=dict)
    start: datetime | None = None
    end: datetime | None = None

    def __repr__(self) -> str:
        return f"CalendarRead({len(self.events)} read, {len(self.problems)} not)"

    def covers(self, now: datetime) -> bool:
        """It holds all of today and tomorrow as they are at ``now`` (a read from before midnight does not)."""
        if self.start is None or self.end is None:
            return False
        today = now.date()
        try:
            return (self.start <= local_at(today, 0, 0, now)
                    and self.end >= local_at(today + timedelta(days=2), 0, 0, now))
        except TypeError:                                  # naive and aware mixed: never trusted
            return False


def read_range(now: datetime) -> tuple[datetime, datetime]:
    """What a read covers: today from midnight to the end of tomorrow, on ``now``'s wall clock."""
    today = now.date()
    return local_at(today, 0, 0, now), local_at(today + timedelta(days=2), 0, 0, now)


@dataclass(frozen=True)
class _Item:
    alias: str
    title: str = field(repr=False)
    place: str = field(repr=False)
    start: datetime | None = None          # timed: on the wall clock
    end: datetime | None = None
    first: date | None = None              # all-day: the first and last day (inclusive)
    last: date | None = None
    free: bool = False                     # shown as "free": listed, never busy

    @property
    def timed(self) -> bool:
        return self.start is not None

    @property
    def busy(self) -> bool:
        return self.timed and not self.free

    def until(self) -> datetime:
        """When it ends (a zero-length event takes its minute)."""
        assert self.start is not None
        return max(self.end or self.start, self.start + _ONE_MINUTE)


def _place(location: Any) -> str:
    """Where it is, short: a meeting link by its site's name ("meet.google.com"), else the words as written."""
    text = " ".join(str(location or "").split())
    if not text:
        return ""
    if "://" in text or text.startswith("www."):
        try:
            host = urlsplit(text if "://" in text else "https://" + text).hostname or ""
        except ValueError:
            host = ""
        return host.removeprefix("www.") or "online"
    return text if len(text) <= PLACE_MAX else text[:PLACE_MAX - 1].rstrip() + "…"


def _items(read: CalendarRead, now: datetime) -> list[_Item]:
    """Every readable account's events (declined ones left out), by start; an all-day event first on its day."""
    found: list[_Item] = []
    for alias, events in read.events.items():
        if alias in read.problems:
            continue
        for event in events or ():
            if getattr(event, "declined", False):
                continue
            title = " ".join(str(getattr(event, "title", "") or "").split()) or "(No title)"
            place = _place(getattr(event, "location", ""))
            free = bool(getattr(event, "free", False))
            start = getattr(event, "start", None)
            if start is not None:
                end = getattr(event, "end", None) or start
                found.append(_Item(alias, title, place, start=wall(start, now), end=wall(end, now), free=free))
                continue
            first = getattr(event, "all_day_start", None)
            if first is not None:
                last = getattr(event, "all_day_end", None) or first
                found.append(_Item(alias, title, place, first=first, last=max(first, last), free=True))

    def key(item: _Item) -> tuple:
        if item.start is not None:
            return (item.start.date(), 1, item.start, item.title.casefold())
        return (item.first, 0, local_at(item.first, 0, 0, now), item.title.casefold())

    return sorted(found, key=key)


def _on_day(item: _Item, day: date, now: datetime) -> bool:
    if item.start is None:
        return bool(item.first and item.last and item.first <= day <= item.last)
    begins, ends = local_at(day, 0, 0, now), local_at(day + timedelta(days=1), 0, 0, now)
    return begins <= item.start < ends or item.start < begins < item.until()


def _span(item: _Item, hour24: bool, day: date | None = None) -> str:
    """"2:00–3:00 PM", "11:30 AM–12:30 PM", "14:00–15:00"; "All day"; "Until 9:00 AM" for one that began before
    ``day``."""
    if item.start is None:
        if item.first and item.last and item.first != item.last:
            return f"All day (to {item.last:%A} {item.last.day} {item.last:%B})"
        return "All day"
    end = item.end or item.start
    if day is not None and item.start.date() < day:
        return f"Until {format_time(end, hour24=hour24)}"
    if end <= item.start:
        return format_time(item.start, hour24=hour24)
    start_digits, start_half = clock_parts(item.start, hour24=hour24)
    text = (f"{start_digits}–{format_time(end, hour24=hour24)}" if start_half == clock_parts(end, hour24=hour24)[1]
            and end.date() == item.start.date() else f"{format_time(item.start, hour24=hour24)}–"
                                                    f"{format_time(end, hour24=hour24)}")
    return text + (f" {end:%A}" if end.date() != item.start.date() else "")


def _said_span(item: _Item) -> str:
    """"2:30 to 3:30 PM", "11:30 AM to 12:30 PM" (said)."""
    assert item.start is not None
    end = item.end or item.start
    if end <= item.start:
        return f"at {spoken_clock(item.start)}"
    first, second = spoken_clock(item.start), spoken_clock(end)
    if first[-2:] == second[-2:] and end.date() == item.start.date():
        first = first[:-3]
    return f"from {first} to {second}"


def _line(item: _Item, accounts: bool, hour24: bool, day: date | None = None) -> str:
    extra = [part for part in (item.place, item.alias if accounts else "") if part]
    if item.free and item.timed:
        extra.append("shown as free")
    tail = f" ({' · '.join(extra)})" if extra else ""
    return f"• {_span(item, hour24, day)}  {item.title}{tail}"


def _soon(start: datetime, now: datetime) -> str:
    """"in 40 minutes", "in 1 hour and 5 minutes", "in under a minute"."""
    minutes = int((start - now).total_seconds() // 60)
    if minutes < 1:
        return "in under a minute"
    if minutes < 60:
        return f"in {minutes} minute{'s' if minutes != 1 else ''}"
    hours, rest = divmod(minutes, 60)
    text = f"in {hours} hour{'s' if hours != 1 else ''}"
    return text + (f" and {rest} minute{'s' if rest != 1 else ''}" if rest else "")


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'s' if number != 1 else ''}"


def _a(noun: str) -> str:
    return f"an {noun}" if noun[:1] in "aeiou" else f"a {noun}"


def _names(aliases: Sequence[str]) -> str:
    items = list(aliases)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


class _Scope:
    """Which calendars an answer covers, and what it says of those that couldn't be read."""

    def __init__(self, read: CalendarRead) -> None:
        self.read_ok = [alias for alias in read.events if alias not in read.problems]
        order = [*read.events, *(alias for alias in read.problems if alias not in read.events)]
        self.failed = [(alias, read.problems[alias]) for alias in order if alias in read.problems]
        self.accounts = len(self.read_ok) + len(self.failed) > 1

    @property
    def partial(self) -> bool:
        return bool(self.failed) and bool(self.read_ok)

    def on(self) -> str:
        """" on your personal calendar" while another couldn't be read, else ""."""
        return f" on {self.your()}" if self.partial else ""

    def your(self) -> str:
        """"your calendar", or "your personal calendar" while another couldn't be read."""
        if not self.partial:
            return "your calendar"
        return f"your {_names(self.read_ok)} calendar{'s' if len(self.read_ok) > 1 else ''}"

    def shown(self) -> str:
        """The plain line for the screen: "Your work calendar couldn't be read: its sign-in has expired ..."."""
        lines = []
        for problem in dict.fromkeys(problem for _alias, problem in self.failed):
            aliases = [alias for alias, kind in self.failed if kind == problem]
            plural = len(aliases) > 1
            lines.append(f"Your {_names(aliases)} calendar{'s' if plural else ''} couldn't be read: "
                         f"{_SHOWN_PROBLEMS.get(problem, _SHOWN_PROBLEMS[PROBLEM_ERROR])}.")
        if self.partial:
            lines.append(f"This covers your {_names(self.read_ok)} calendar{'s' if len(self.read_ok) > 1 else ''} "
                         f"only.")
        return " ".join(lines)

    def said(self) -> str:
        """" your work calendar couldn't be read" (said; no reason when there are several)."""
        aliases = [alias for alias, _problem in self.failed]
        plural = len(aliases) > 1
        return f"your {_names(aliases)} calendar{'s' if plural else ''} couldn't be read"

    def brief(self) -> str:
        if not self.failed:
            return ""
        aliases = [alias for alias, _problem in self.failed]
        return f" · {_names(aliases)} calendar{'s' if len(aliases) > 1 else ''} couldn't be read"


def _join(*parts: str) -> str:
    return "\n".join(part for part in parts if part)


def _name(item: _Item, titles: bool) -> str:
    """The event's title as said when titles may be said ("" otherwise, or when nothing sayable is left)."""
    return spoken_title(item.title) if titles else ""


def reply(ask: CalendarAsk, read: CalendarRead, *, now: datetime, address: str = "sir",
          hour24: bool = False, say_titles: bool = False) -> InstantReply | None:
    """The answer to ``ask`` from ``read`` (see the module docs); None when the read does not cover it."""
    if now.utcoffset() is None:
        now = now.astimezone()
    if not read.covers(now):
        return None
    scope = _Scope(read)
    if not scope.read_ok:
        return _none_read(scope, address)
    items = _items(read, now)
    answer = {CAL_NEXT: _next, CAL_MORE: _more, CAL_DAY: _day, CAL_COUNT: _day, CAL_FREE: _free}.get(ask.action)
    if answer is None:
        return None
    text, speech, brief = answer(ask, items, now, scope, hour24, titles=say_titles)
    if scope.failed:
        text = _join(text, scope.shown())
        brief += scope.brief()
    return InstantReply(KIND_CALENDAR, text, with_address(speech, address), SOURCE, brief=brief)


def _none_read(scope: _Scope, address: str) -> InstantReply:
    aliases = [alias for alias, _problem in scope.failed]
    plural = len(aliases) > 1
    problems = {problem for _alias, problem in scope.failed}
    reason = _SAID_PROBLEMS.get(next(iter(problems)), _SAID_PROBLEMS[PROBLEM_ERROR]) if len(problems) == 1 else ""
    if plural and reason == _SAID_PROBLEMS[PROBLEM_SIGNED_OUT]:
        reason = "they need signing in again"
    said = (f"I couldn't read your {_names(aliases)} calendar{'s' if plural else ''}, {{sir}}"
            + (f": {reason}." if reason else "."))
    brief = f"Couldn't read your {_names(aliases)} calendar{'s' if plural else ''}"
    return InstantReply(KIND_CALENDAR, scope.shown(), with_address(said, address), SOURCE, brief=brief)


def _next(ask: CalendarAsk, items: list[_Item], now: datetime, scope: _Scope, hour24: bool, *,
          titles: bool = False) -> tuple[str, str, str]:
    noun = ask.noun
    timed = [item for item in items if item.timed]
    on_now = [item for item in timed if item.start <= now < item.until()]
    upcoming = [item for item in timed if item.start > now]
    today = now.date()
    now_line = ""
    if on_now:
        current = on_now[-1]
        now_line = (f"On now: {current.title}, until {format_time(current.end or current.start, hour24=hour24)}"
                    + (f" ({current.alias})" if scope.accounts else "") + ".")
    if not upcoming:
        text = _join(now_line, f"Nothing more on {scope.your()} today or tomorrow.")
        if on_now:
            name = _name(on_now[-1], titles)
            said = (f"Your {noun} now{', ' + name + ',' if name else ''} ends at {spoken_clock(on_now[-1].until())}, "
                    f"{{sir}}, and there's nothing after it{scope.on()} today or tomorrow.")
        else:
            said = f"Nothing on {scope.your()} for the rest of today or tomorrow, {{sir}}."
        if scope.partial:
            said = said[:-1] + f"; {scope.said()}."
        return text, said, "Nothing more on your calendar today or tomorrow"
    first = upcoming[0]
    together = [item for item in upcoming if item.start == first.start]
    when = format_time(first.start, hour24=hour24)
    tomorrow = first.start.date() != today
    said_when = ("tomorrow at " if tomorrow else "at ") + spoken_clock(first.start)
    soon = "" if tomorrow else f", {_soon(first.start, now)}"
    lines = [_line(item, scope.accounts, hour24) for item in together]
    head = (f"Your next {noun}{scope.on()} is {'tomorrow ' if tomorrow else ''}at {when}{soon}:"
            if len(together) == 1 else
            f"Next{scope.on()}, {'tomorrow ' if tomorrow else ''}at {when}{soon} - {len(together)} at once:")
    if tomorrow:
        rest = "Nothing more today."
    elif not any(item.start.date() == today for item in upcoming[len(together):]):
        rest = "Nothing more after it today."
    else:
        rest = ""
    text = _join(now_line, head, *lines, rest)
    names = [name for name in (_name(item, titles) for item in together[:3]) if name]
    if len(together) == 1:
        said = (f"Your next {noun}{scope.on()}{', ' + names[0] + ',' if names else ''} is {said_when}, "
                f"{{sir}}{soon}.")
    else:
        said = (f"You have {len(together)} {noun}s next{scope.on()}, {said_when}, {{sir}}{soon}"
                + (f": {_names(names)}." if names else "."))
    if scope.partial:
        said = said[:-1] + f"; {scope.said()}."
    brief = f"Next: {first.title} {'tomorrow ' if tomorrow else ''}at {when}" + (
        f" ({_soon(first.start, now)})" if not tomorrow else "") + (
        f" and {len(together) - 1} more at once" if len(together) > 1 else "")
    return text, said, brief


def _more(ask: CalendarAsk, items: list[_Item], now: datetime, scope: _Scope, hour24: bool, *,
          titles: bool = False) -> tuple[str, str, str]:
    noun = ask.noun
    today = now.date()
    timed = [item for item in items if item.timed]
    left = [item for item in timed if item.start > now and item.start.date() == today]
    on_now = [item for item in timed if item.start <= now < item.until()]
    if left:
        first = left[0]
        head = f"{len(left)} more today{scope.on()}:"
        text = _join(head, *(_line(item, scope.accounts, hour24) for item in left))
        name = _name(first, titles)
        named = f"{name}, " if name else ""
        if len(left) == 1:
            said = (f"Yes, {{sir}}: one more today{scope.on()}, {named}at {spoken_clock(first.start)}, "
                    f"{_soon(first.start, now)}.")
        else:
            said = (f"Yes, {{sir}}: {len(left)} more today{scope.on()}; the next is {named}at "
                    f"{spoken_clock(first.start)}, {_soon(first.start, now)}.")
        brief = f"{len(left)} more today · next: {first.title} at {format_time(first.start, hour24=hour24)}"
    else:
        tomorrow = [item for item in timed if item.start.date() == today + timedelta(days=1)]
        lines = [f"No more {noun}s today{scope.on()}."]
        if on_now:
            current = on_now[-1]
            lines.insert(0, f"On now: {current.title}, until {format_time(current.until(), hour24=hour24)}.")
        if tomorrow:
            lines.append(f"Tomorrow's first: {tomorrow[0].title} at {format_time(tomorrow[0].start, hour24=hour24)}.")
        text = _join(*lines)
        name = _name(on_now[-1], titles) if on_now else ""
        said = (f"Just the one on now, {{sir}}{': ' + name + ',' if name else ','} until "
                f"{spoken_clock(on_now[-1].until())}." if on_now else f"No more {noun}s today{scope.on()}, {{sir}}.")
        brief = f"No more {noun}s today"
    if scope.partial:
        said = said[:-1] + f"; {scope.said()}."
    return text, said, brief


def _day(ask: CalendarAsk, items: list[_Item], now: datetime, scope: _Scope, hour24: bool, *,
         titles: bool = False) -> tuple[str, str, str]:
    today = now.date()
    day = today + timedelta(days=ask.day)
    word = "today" if ask.day == 0 else "tomorrow"
    on_day = [item for item in items if _on_day(item, day, now)]
    timed = [item for item in on_day if item.timed]
    all_day = [item for item in on_day if not item.timed]
    every = ask.action == CAL_DAY or ask.noun == "event"
    noun = "event" if every else ask.noun
    counted = on_day if every else timed
    named = f"{word.capitalize()}, {day:%A} {day.day} {day:%B}"
    if not on_day:
        text = f"Nothing on {scope.your()} {word} ({day:%A} {day.day} {day:%B})."
        said = f"Nothing on {scope.your()} {word}, {{sir}}."
        if scope.partial:
            said = said[:-1] + f"; {scope.said()}."
        return text, said, f"Nothing on your calendar {word}"
    lines = [_line(item, scope.accounts, hour24, day) for item in on_day]
    text = _join(f"{named}{scope.on()}:", *lines)
    upcoming = [item for item in timed if item.start > now]
    on_now = [item for item in timed if item.start <= now < item.until()]
    if not counted:
        said = (f"No {noun}s {word}{scope.on()}, {{sir}}; just {_count(len(all_day), 'all-day event')}.")
        brief = f"{word.capitalize()}: no {noun}s · {_count(len(all_day), 'all-day event')}"
    elif not timed:
        said = f"You have {_count(len(all_day), 'all-day event')} {word}{scope.on()}, {{sir}}."
        brief = f"{word.capitalize()}: {_count(len(all_day), 'all-day event')}"
    elif ask.day == 1:
        name = _name(timed[0], titles)
        said = (f"You have {_count(len(counted), noun)} tomorrow{scope.on()}, {{sir}}; the first is "
                f"{name + ', ' if name else ''}at {spoken_clock(timed[0].start)}.")
        brief = (f"Tomorrow: {_count(len(counted), noun)} · first: {timed[0].title} at "
                 f"{format_time(timed[0].start, hour24=hour24)}")
    elif upcoming:
        name = _name(upcoming[0], titles)
        said = (f"You have {_count(len(counted), noun)} today{scope.on()}, {{sir}}; the next is "
                f"{name + ', ' if name else ''}at {spoken_clock(upcoming[0].start)}.")
        brief = (f"Today: {_count(len(counted), noun)} · next: {upcoming[0].title} at "
                 f"{format_time(upcoming[0].start, hour24=hour24)}")
    elif on_now:
        name = _name(on_now[-1], titles)
        said = (f"You have {_count(len(counted), noun)} today{scope.on()}, {{sir}}; the last one"
                f"{', ' + name + ',' if name else ''} is on now, until {spoken_clock(on_now[-1].until())}.")
        brief = f"Today: {_count(len(counted), noun)} · the last is on now"
    else:
        said = f"You had {_count(len(counted), noun)} today{scope.on()}, {{sir}}; none are left."
        brief = f"Today: {_count(len(counted), noun)} · none left"
    if scope.partial:
        said = said[:-1] + f"; {scope.said()}."
    return text, said, brief


def _free(ask: CalendarAsk, items: list[_Item], now: datetime, scope: _Scope, hour24: bool, *,
          titles: bool = False) -> tuple[str, str, str]:
    at = ask.at if ask.at is not None else now
    at = wall(at, now)
    day = at.date()
    tomorrow = day != now.date()
    shown_when = format_time(at, hour24=hour24) + (" tomorrow" if tomorrow else "")
    said_when = spoken_clock(at) + (" tomorrow" if tomorrow else "")
    busy = [item for item in items if item.busy and item.start <= at < item.until()]
    later = [item for item in items if item.busy and item.start > at and item.start.date() == day]
    all_day = [item for item in items if not item.timed and _on_day(item, day, now)]
    shown_free = [item for item in items if item.timed and item.free and item.start <= at < item.until()]
    noun = ask.noun
    if busy:
        lines = [_line(item, scope.accounts, hour24) for item in busy]
        text = _join(f"You're busy at {shown_when}{scope.on()}:", *lines)
        first = busy[0]
        name = _name(first, titles)
        what = ((f"you have {name}, {_said_span(first)}" if name else f"you have {_a(noun)} {_said_span(first)}")
                if len(busy) == 1 else
                f"you have {len(busy)} {noun}s then, the first {name + ', ' if name else ''}{_said_span(first)}")
        said = f"{'Yes' if ask.busy else 'No'}, {{sir}}: {what}."
        if scope.partial:
            said = said[:-1] + f". Your {scope.said().removeprefix('your ')}."
        brief = f"Busy at {shown_when}: {first.title} ({_span(first, hour24)})" + (
            f" and {len(busy) - 1} more" if len(busy) > 1 else "")
        return text, said, brief
    extra = []
    if later:
        extra.append(f"Next: {later[0].title} at {format_time(later[0].start, hour24=hour24)}"
                     + (f" ({later[0].alias})" if scope.accounts else "") + ".")
    if shown_free:
        extra.append("Shown as free then: " + "; ".join(item.title for item in shown_free) + ".")
    if all_day:
        extra.append("All day: " + "; ".join(item.title for item in all_day) + ".")
    text = _join(f"You're free at {shown_when}{scope.on()}.", " ".join(extra))
    until = f", until {spoken_clock(later[0].start)}" if later else ""
    if scope.partial:
        said = (f"You're free at {said_when}{scope.on()}, {{sir}}{until}, but {scope.said()}.")
    elif ask.busy:
        said = f"No, {{sir}}: you're free at {said_when}{until}."
    else:
        said = f"You're free at {said_when}, {{sir}}{until}."
    brief = f"Free at {shown_when}" + (f" · next: {later[0].title} at "
                                        f"{format_time(later[0].start, hour24=hour24)}" if later else "")
    return text, said, brief
