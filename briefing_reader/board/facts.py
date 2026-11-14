"""A satellite's one fact (the new look, part 5b; DESIGN-SPEC 3.3 "Satellite"): the line a folded frame shows under its
title ("REPLY WAITS", "1 LINE CHANGED", "3 EVENTS · NEXT 10:00 AM"), made by the app from the frame's own data. Qt-free
and pure: no file, no log, no clock; nothing here raises for odd data.

    fact_of(frame) -> str      the fact of a frame message (as board.protocol checks it), at most protocol.FACT_CAP
                               UTF-16 units; the page shows it in the badge role (caps)
    cut(text, cap) -> str      ``text`` within ``cap`` UTF-16 units, ended with "…" when cut (at a space near the end)

What each frame says, first match wins: a failed one "Couldn't load"; an empty one its own words ("Nothing unread,
sir."); one that waits on the owner what waits ("Reply waits"); then its kind's own count and next thing - a day "3
events · next 10:00 AM", a mail "Dana Whitfield · 8:12 AM", a mail list "3 unread · newest 7:02 AM", to-dos "4 items · 1
waits", a page picture "Page picture · 9:05 AM", any other its meta. A changed one starts with "Changed".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import protocol

ELLIPSIS = "…"
DOT = " · "
FAILED = "Couldn't load"
CHANGED = "Changed"
PAGE_PICTURE = "Page picture"
_APPROVALS = " on Approvals"
_MERIDIEMS = ("AM", "PM")


def cut(text: str, cap: int) -> str:
    """``text`` within ``cap`` UTF-16 code units, ended with "…" when cut (at a space near the end when there is one)."""
    text = " ".join(str(text or "").split())
    if protocol.units(text) <= cap:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        size = 2 if ord(char) > 0xFFFF else 1
        if used + size > cap - 1:
            break
        kept.append(char)
        used += size
    head = "".join(kept)
    space = head.rfind(" ")
    if space >= len(head) * 2 // 3:
        head = head[:space]
    return head.rstrip(" ·") + ELLIPSIS


def _words(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _rows(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = body.get("rows")
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _more(row: Mapping[str, Any]) -> int:
    """The N of a "+N MORE" row's meta (0 when it is not one)."""
    meta = _words(row.get("meta"))
    if meta.startswith("+") and meta.upper().endswith(" MORE"):
        digits = meta[1:-5].strip()
        if digits.isdigit():
            return int(digits)
    return 0


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def start_of(time_text: str) -> str:
    """The start of a row's time range ("10:00 AM" of "10:00-10:30 AM", "11:30 AM" of "11:30 AM-1:00 PM", "14:00" of
    "14:00-15:00"); the words as they are when there is no range."""
    text = _words(time_text)
    if "-" not in text:
        return text
    first, _sep, rest = text.partition("-")
    first, rest = first.strip(), rest.strip()
    if first and not first.endswith(_MERIDIEMS):
        for meridiem in _MERIDIEMS:
            if rest.endswith(meridiem):
                return f"{first} {meridiem}"
    return first


def _calendar(frame: Mapping[str, Any], body: Mapping[str, Any]) -> str:
    rows = _rows(body)
    has_day = isinstance(body.get("last"), int) and isinstance(body.get("first"), int) and body["last"] > body["first"]
    if not has_day and not _words(body.get("now")):
        return _words(frame.get("meta"))               # a week: its own count ("12 events")
    events = 0
    upcoming = ""
    for row in rows:
        if not _words(row.get("time")) and not _words(row.get("title")):
            events += _more(row)
            continue
        state = row.get("state")
        if state in ("free", "struck"):
            continue
        events += 1
        if state == "next" and not upcoming:
            upcoming = start_of(row.get("time", ""))
    words = _plural(events, "event")
    return f"{words}{DOT}next {upcoming}" if upcoming else words


def _mail_thread(frame: Mapping[str, Any], body: Mapping[str, Any]) -> str:
    sender, when = _words(body.get("sender")), _words(body.get("time"))
    found = DOT.join(part for part in (sender, when) if part)
    return found or _words(frame.get("meta"))


def _mail_list(frame: Mapping[str, Any], body: Mapping[str, Any]) -> str:
    rows = _rows(body)
    mails = [row for row in rows if _words(row.get("sender")) or _words(row.get("subject"))]
    unread = [row for row in mails if row.get("unread") is True]
    if unread:
        newest = _words(unread[0].get("time"))
        words = f"{len(unread)} unread"
        return f"{words}{DOT}newest {newest}" if newest else words
    meta = _words(frame.get("meta"))
    if meta:
        return meta
    total = len(mails) + sum(_more(row) for row in rows)
    return _plural(total, "mail") if total else ""


def _todos(frame: Mapping[str, Any], body: Mapping[str, Any]) -> str:
    rows = _rows(body)
    items = [row for row in rows if _words(row.get("text"))]
    total = len(items) + sum(_more(row) for row in rows if not _words(row.get("text")))
    waits = sum(1 for row in items if row.get("state") == "waits")
    words = _plural(total, "item")
    return f"{words}{DOT}{waits} waits" if waits else words


def _picture(frame: Mapping[str, Any], body: Mapping[str, Any]) -> str:
    meta = _words(frame.get("meta"))
    return f"{PAGE_PICTURE}{DOT}{meta}" if meta else PAGE_PICTURE


_KINDS = {"calendar": _calendar, "mail_thread": _mail_thread, "mail_list": _mail_list, "todos": _todos,
          "picture": _picture}


def fact_of(frame: Any) -> str:
    """See the module doc."""
    if not isinstance(frame, Mapping):
        return ""
    status = frame.get("status", "ok")
    if status == "failed":
        found = FAILED
    elif status == "empty":
        found = _words(frame.get("status_text")) or _words(frame.get("meta"))
    else:
        waits = _words(frame.get("waits"))
        if waits:
            found = waits[:-len(_APPROVALS)] if waits.endswith(_APPROVALS) else waits
        else:
            body = frame.get("body")
            body = body if isinstance(body, Mapping) else {}
            maker = _KINDS.get(str(frame.get("kind", "")))
            found = maker(frame, body) if maker is not None else _words(frame.get("meta"))
    if frame.get("changed") is True and status == "ok":
        found = f"{CHANGED}{DOT}{found}" if found else CHANGED
    return cut(found, protocol.FACT_CAP)


__all__ = ["CHANGED", "FAILED", "PAGE_PICTURE", "cut", "fact_of", "start_of"]
