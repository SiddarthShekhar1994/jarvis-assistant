"""The board's frames from what Jarvis already knows (the new look, part 4; PLAN section 4, DESIGN-SPEC 3.3).

Qt-free and pure: no file, no log, no network, no clock (the caller gives ``now``). The one exception: a page picture that
is not already a JPEG within the board's caps is re-encoded with QImage, imported only then. Each builder turns Jarvis's
typed data into the messages BoardView.apply takes (board.protocol.check_message passes every one of them) and never
raises for odd data; only a frame id that is not one raises ValueError (the caller's mistake):

    from_picture(picture, *, frame_id, now, hour24=False, title="")
        a LIVE picture (pictures.Picture; every string in it is cleaned already, and is cleaned again here):
        KIND_EMAIL -> mail_thread (the sender, the time, the text); KIND_OUTGOING -> mail_thread whose reply waits on
        Approvals (the frame's amber chip: "waits" set); KIND_DAY -> calendar (the day bar, the now-line horizon when
        today is shown, rows with time running down and free gaps of 30 min or more); KIND_WEEK -> calendar (one row
        per day: label, first titles, count); KIND_PAGE -> a picture frame and its picture message (the JPEG as a
        data: URL: as it is when it already is a JPEG of at most PICTURE_WIDTH px and protocol.PICTURE_BYTES, else
        re-encoded; when even that does not fit, no picture message, the frame says so and ``refused`` counts it);
        KIND_SEARCH -> text (the query as the title, the results as rows: title and host). A picture that is not ready
        -> the frame in its failed (unavailable: the page offers Try again) or empty (on its way, not kept) state, with
        the picture's own note, and Built's ``not_ready`` set.
    from_research(answer, *, frame_id, title="")
        a web research answer (ask.research.ResearchAnswer): the answer as text, its sources as rows (title and host,
        never a link)
    from_todos(actions, deadlines, *, frame_id, now, statuses=None)
        to-dos: each Todo card (actions.ProposedAction, kind "todo") - it waits while it still waits on the owner's OK,
        is done when ``statuses`` (action id -> actions.STATUS_*, as actions.ActionStore keeps them) says so, else open;
        a dismissed one is left out - and each deadline (agenda.Deadline) not already one of them, open; due words from
        agenda.due_label; done ones last
    from_followups(items, *, frame_id, now=None)
        follow-ups (followups.FollowupItem) as a mail list: sender, subject, WAITING / OWED as the row's meta, the age
        ("20h", "3d": followups.age_words) as its time when ``now`` is given

Each returns Built: ``messages`` in order (the frame, then its picture when it has one) and ``refused`` (pictures left
out). Every string from outside goes through live.clean_line / clean_block (secrets redacted; control, bidi and invisible
characters shown as "<U+202E>"), then is cut to its cap as the page counts it (UTF-16 code units, never inside a
surrogate pair) with "…": title 60, meta 40, text 400, rows 8 (the last row "+N MORE" when more were cut). A host is cut
from its start, so its end ("….example.edu": whose site it is) always shows. Times are Jarvis's own: text_prep's clock,
pictures.time_range, agenda.due_label and its "in 19 min", followups.age_words.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from typing import Any

from .. import actions, agenda, live, persona, pictures
from ..followups import OWED, WAITING, age_words
from ..text_prep import clock_parts, format_time
from . import protocol

ELLIPSIS = "…"
DOT = " · "
MORE = "+{count} MORE"                  # the last row's meta when rows were cut (DESIGN-SPEC 3.5: rows 8)
WAITS_REPLY = "Reply waits on Approvals"
WAITS_EMAIL = "Email waits on Approvals"
RESEARCH_TITLE = "Web research"
TODOS_TITLE = "To-dos"
TODOS_EMPTY = "No to-dos or deadlines."
FOLLOWUPS_TITLE = "Follow-ups"
FREE_TITLE = "Free"
FREE_MIN = 30                           # a free gap this long or longer is a "Free" row
SOON_MIN = 60                           # "in 19 min" up to this far ahead (agenda's rule)
PICTURE_WIDTH = 960                     # px: a page picture on the board is at most this wide ...
MAX_PIXELS = 16_000_000                 # ... and one bigger than this is never decoded to re-encode it
JPEG_QUALITIES = (85, 70, 55, 40)       # tried in turn until the JPEG is within protocol.PICTURE_BYTES
TOO_BIG = pictures.UNAVAILABLE_PREFIX + "too large to show on the board"
SENDER_UNKNOWN = "(sender not known)"
# The words of a calendar card's change, as LIVE's day picture tags its blocks (hud._pic_block_colors).
_ROLE_WORDS = {pictures.ROLE_NEW: "new", pictures.ROLE_AFTER: "new", pictures.ROLE_BEFORE: "was",
               pictures.ROLE_CANCEL: "cancel"}
_STRUCK = frozenset({pictures.ROLE_BEFORE, pictures.ROLE_CANCEL})
_KIND_FOR = {pictures.KIND_EMAIL: "mail_thread", pictures.KIND_OUTGOING: "mail_thread", pictures.KIND_DAY: "calendar",
             pictures.KIND_WEEK: "calendar", pictures.KIND_PAGE: "picture", pictures.KIND_SEARCH: "text"}
_EMPTY_BODIES: dict[str, dict[str, Any]] = {
    "mail_thread": {"sender": "", "time": "", "text": ""}, "calendar": {"rows": []}, "picture": {"caption": ""},
    "text": {"text": ""}, "todos": {"rows": []}, "mail_list": {"rows": []},
}
_SURROGATE_RE = re.compile("[\ud800-\udfff]")
_PART_ESCAPE_RE = re.compile(r"<(?:U(?:\+[0-9A-F]{0,6})?)?$")    # the start of a "<U+202E>" a cut would split
_ADDRESSED_RE = re.compile(r"(.+?) <[^<>\s]+@[^<>\s]+>")
_NON_WORD_RE = re.compile(r"[\W_]+")


@dataclass(frozen=True, repr=False)
class Built:
    """What a builder made: ``messages`` for BoardView.apply in order (the frame first, then its picture when it has
    one), and ``refused``: pictures left out (too big for the board even re-encoded). ``not_ready`` (part 5c fixes,
    round 2): the frame stands for a LIVE picture that is not ready - on its way, failed or not kept - never a
    builder's own final state (an empty to-do list says "empty" and is ready): a board frame that shows a good one
    keeps it meanwhile (board.feed). repr: kinds and counts only."""

    messages: tuple[dict[str, Any], ...] = ()
    refused: int = 0
    not_ready: bool = False

    @property
    def frame(self) -> dict[str, Any] | None:
        return self.messages[0] if self.messages else None

    def __repr__(self) -> str:
        kinds = ", ".join(str(message.get("kind") or message.get("type")) for message in self.messages)
        return f"Built([{kinds}], refused={self.refused})"


# --------------------------------------------------------------------------
# Words: cleaned, then cut as the page counts them
# --------------------------------------------------------------------------

def _units(char: str) -> int:
    return 2 if ord(char) > 0xFFFF else 1


def cut(text: str, cap: int) -> str:
    """``text`` within ``cap`` UTF-16 code units: as it is, else cut (at a space near the end when there is one; never
    inside a surrogate pair, nor inside an escape such as "<U+202E>": a part of one would hide what it shows) and ended
    with "…"."""
    if protocol.units(text) <= cap:
        return text
    budget, used = max(0, cap - 1), 0
    kept: list[str] = []
    for char in text:
        size = _units(char)
        if used + size > budget:
            break
        kept.append(char)
        used += size
    head = _PART_ESCAPE_RE.sub("", "".join(kept))
    space = head.rfind(" ")
    if space >= len(head) * 2 // 3:     # a word ends close to the cut: cut there instead
        head = head[:space]
    return head.rstrip() + ELLIPSIS


def cut_host(text: str, cap: int) -> str:
    """A host within ``cap`` units, cut from its start ("…cs.example.edu"): the end says whose site it is."""
    if protocol.units(text) <= cap:
        return text
    budget, used = max(0, cap - 1), 0
    kept: list[str] = []
    for char in reversed(text):
        size = _units(char)
        if used + size > budget:
            break
        kept.append(char)
        used += size
    return ELLIPSIS + "".join(reversed(kept)).lstrip(" .")


def _escape(text: str) -> str:
    """Lone surrogates (never text) shown as live does its escapes: "<U+D800>"."""
    return _SURROGATE_RE.sub(lambda match: f"<U+{ord(match.group(0)):04X}>", text)


def clean(value: Any, cap: int = 0) -> str:
    """One line from outside: live.clean_line (secrets redacted, line breaks as " ↵ ", control / bidi / invisible
    characters as "<U+202E>"), lone surrogates escaped too, then cut to ``cap`` units ("…"; 0 = not cut)."""
    text = _escape(live.clean_line(value, 0))
    return cut(text, cap) if cap > 0 else text


def clean_text(value: Any, cap: int) -> str:
    """A block from outside as one paragraph (a mail's text): live.clean_block, its breaks as spaces, cut to ``cap``."""
    text = _escape(" ".join(live.clean_block(value, 0)[0].split()))
    return cut(text, cap)


def _check_id(frame_id: Any) -> str:
    if not protocol.is_frame_id(frame_id):
        raise ValueError("frame_id is not a frame id (1 to 40 letters, digits, '-' or '_')")
    return frame_id


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _wall(now: Any) -> datetime:
    """``now`` as a naive wall time (an aware one in its own zone: the app's clock is this PC's)."""
    if isinstance(now, datetime):
        return now.replace(tzinfo=None)
    if isinstance(now, date):
        return datetime.combine(now, clock_time())
    return datetime(2000, 1, 1)


def _frame(frame_id: str, kind: str, title: str, *, meta: str = "", body: dict[str, Any] | None = None,
           status: str = "ok", status_text: str = "", status_meta: str = "", waits: str = "") -> dict[str, Any]:
    return {"type": "frame", "id": frame_id, "kind": kind, "title": cut(title, protocol.TITLE_CAP),
            "meta": cut(meta, protocol.META_CAP), "body": body if body is not None else dict(_EMPTY_BODIES[kind]),
            "status": status, "status_text": cut(status_text, protocol.WORDS_CAP),
            "status_meta": cut(status_meta, protocol.META_CAP), "waits": cut(waits, protocol.WAITS_CAP)}


def _capped(rows: list[dict[str, Any]], more: Callable[[int], dict[str, Any]], hidden: int = 0) -> list[dict[str, Any]]:
    """At most ROWS_CAP rows; when rows were cut (or ``hidden`` ones were never given), the last one is the "+N MORE"
    row ``more(N)`` (only its meta shows)."""
    hidden = max(0, int(hidden or 0))
    if len(rows) <= protocol.ROWS_CAP and not hidden:
        return rows
    keep = min(len(rows), protocol.ROWS_CAP - 1)
    return rows[:keep] + [more(len(rows) - keep + hidden)]


def _more_words(count: int) -> str:
    return MORE.format(count=count)


def _text_more(count: int) -> dict[str, Any]:
    return {"lead": "", "text": "", "meta": _more_words(count)}


def _calendar_more(count: int) -> dict[str, Any]:
    return {"time": "", "title": "", "meta": _more_words(count), "state": ""}


def _todo_more(count: int) -> dict[str, Any]:
    return {"text": "", "meta": _more_words(count), "state": "open"}


def _mail_more(count: int) -> dict[str, Any]:
    return {"sender": "", "time": "", "subject": "", "meta": _more_words(count), "unread": False}


# --------------------------------------------------------------------------
# LIVE's pictures
# --------------------------------------------------------------------------

def from_picture(picture: Any, *, frame_id: str, now: datetime, hour24: bool = False, title: str = "") -> Built:
    """A LIVE picture as the board's frame (and the picture message of a page picture): see the module doc. ``title``
    overrides the frame's title (else the subject, the headline, the query or the caption). Built() for a picture the
    board does not show."""
    frame_id = _check_id(frame_id)
    kind = _KIND_FOR.get(getattr(picture, "kind", ""))
    if kind is None:
        return Built()
    state = getattr(picture, "state", "")
    if state != live.PICTURE_READY or _data_of(picture) is None:
        return Built((_not_ready(picture, kind, frame_id, title),), not_ready=True)
    builder = {pictures.KIND_EMAIL: _mail, pictures.KIND_OUTGOING: _mail, pictures.KIND_DAY: _day,
               pictures.KIND_WEEK: _week, pictures.KIND_SEARCH: _search}.get(picture.kind)
    if builder is not None:
        return Built((builder(picture, frame_id, now, title),))
    return _page(picture, frame_id, now, bool(hour24), title)


def _data_of(picture: Any) -> Any:
    name = {pictures.KIND_EMAIL: "mail", pictures.KIND_OUTGOING: "mail", pictures.KIND_DAY: "day",
            pictures.KIND_WEEK: "week", pictures.KIND_PAGE: "page", pictures.KIND_SEARCH: "search"}[picture.kind]
    return getattr(picture, name, None)


def _title_of(picture: Any, title: str) -> str:
    shown = clean(title, protocol.TITLE_CAP)
    if shown:
        return shown
    data = _data_of(picture)
    if picture.kind in (pictures.KIND_EMAIL, pictures.KIND_OUTGOING) and data is not None:
        shown = clean(data.head.subject or pictures.NO_SUBJECT, protocol.TITLE_CAP)
    elif picture.kind == pictures.KIND_DAY and data is not None:
        shown = clean(data.headline, protocol.TITLE_CAP)
    elif picture.kind == pictures.KIND_SEARCH and data is not None:
        shown = clean(data.query, protocol.TITLE_CAP)
    return shown or clean(getattr(picture, "caption", ""), protocol.TITLE_CAP)


_STATE_WORDS = {live.PICTURE_UNAVAILABLE: "Picture unavailable", live.PICTURE_DROPPED: "Picture not kept",
                live.PICTURE_PENDING: pictures.NOTE_OPENING}


def _not_ready(picture: Any, kind: str, frame_id: str, title: str) -> dict[str, Any]:
    """The frame in its failed (unavailable: Try again re-reads it) or empty (on its way, not kept) state, saying the
    picture's own note."""
    state = getattr(picture, "state", "")
    note = clean(getattr(picture, "note", "") or _STATE_WORDS.get(state, "Picture unavailable"), protocol.WORDS_CAP)
    body = dict(_EMPTY_BODIES[kind])
    page = getattr(picture, "page", None)
    if kind == "picture" and page is not None:
        body["caption"] = _caption(page)
    return _frame(frame_id, kind, _title_of(picture, title), body=body,
                  status="failed" if state == live.PICTURE_UNAVAILABLE else "empty", status_text=note)


def _name_of(sender: str) -> str:
    """"Ana Lima" of "Ana Lima <ana@example.com>"; the address when there is no name."""
    found = _ADDRESSED_RE.fullmatch(sender)
    return found.group(1).strip() if found else sender


def _mail_time(when: str, now: datetime) -> str:
    """A mail's time as its picture says it ("Fri Oct 9 2026, 2:41 PM": pictures' own words), the clock alone when the
    mail is today's."""
    today = _wall(now).date()
    prefix = f"{pictures.day_label(today)} {today.year}, "
    return when[len(prefix):] if when.startswith(prefix) else when


def _mail(picture: Any, frame_id: str, now: datetime, title: str) -> dict[str, Any]:
    mail = picture.mail
    head = mail.head
    account = clean(mail.account, protocol.META_CAP)
    text = clean_text(mail.body, protocol.TEXT_CAP) if mail.body_kept else ""
    if mail.outgoing:      # what a Reply / Email card sends: it waits on the owner's OK, on Approvals only
        body = {"sender": clean(f"To {head.to}" if head.to else "", protocol.TITLE_CAP), "time": "", "text": text}
        waits = WAITS_REPLY if str(mail.reply_line).startswith("Reply") else WAITS_EMAIL
        return _frame(frame_id, "mail_thread", _title_of(picture, title), meta=account, body=body, waits=waits)
    when = _mail_time(clean(head.when), now)
    body = {"sender": clean(_name_of(clean(head.sender)) or SENDER_UNKNOWN, protocol.TITLE_CAP),
            "time": cut(when, protocol.META_CAP), "text": text}
    return _frame(frame_id, "mail_thread", _title_of(picture, title), meta=DOT.join(p for p in (account, when) if p),
                  body=body)


def _range(start_min: int, end_min: int, hour24: bool) -> str:
    """"10:00-10:30 AM" / "10:00-10:30" (pictures.time_range) of minutes after midnight."""
    base = datetime(2000, 1, 1)
    return pictures.time_range(base + timedelta(minutes=start_min), base + timedelta(minutes=end_min), hour24)


def _length(minutes: int) -> str:
    """A free gap's length: "45 min", "1 h 30", "2 h"."""
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


def _tick_text(minute: int, hour24: bool) -> str:
    hour = (minute // 60) % 24
    return str(hour) if hour24 else str(hour % 12 or 12)


def _day(picture: Any, frame_id: str, now: datetime, title: str) -> dict[str, Any]:
    """A calendar card's change on its day, or a day as read: the bar from the first to the last hour (events, the
    next one bright, free gaps dashed, the now tick), the now-line horizon when today is shown, then the rows."""
    day = picture.day
    hour24 = bool(day.hour24)
    single = len(day.days) == 1
    today = single and day.now_column == 0
    wall = _wall(now)
    now_min = wall.hour * 60 + wall.minute if today else -1
    blocks = list(day.blocks)
    timed = sorted((block for block in blocks if not block.all_day and not block.timeless),
                   key=lambda block: (block.column, block.start_min, block.end_min))
    busy = [block for block in timed if block.role not in _STRUCK]
    upcoming = next((block for block in busy if block.start_min > now_min), None) if today else None
    rows: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for block in blocks:
        rows.append(((block.column, 0 if block.all_day else 1 if block.timeless else 2, block.start_min,
                      block.end_min), _block_row(block, day, today, now_min, upcoming, hour24)))
    gaps: list[tuple[int, int]] = []
    if single:
        reach = -1
        for block in busy:
            if reach >= 0 and block.start_min - reach >= FREE_MIN and not (today and block.start_min <= now_min):
                gaps.append((reach, block.start_min))
            reach = max(reach, block.end_min)
    for start, end in gaps:
        rows.append(((0, 2, start, start), {"time": _range(start, end, hour24), "title": FREE_TITLE,
                                            "meta": _length(end - start), "state": "free"}))
    rows.sort(key=lambda pair: pair[0])
    shown = [row for _key, row in rows]
    if len(shown) > protocol.ROWS_CAP:            # free gaps go first: they are the bar's too
        shown = [row for row in shown if row["state"] != "free"]
    body: dict[str, Any] = {"first": 0, "last": 0, "now_min": -1, "ticks": [], "spans": [], "now": "",
                            "rows": _capped(shown, _calendar_more, day.others_more)}
    if single and day.last_min > day.first_min:
        first, last = int(day.first_min), int(day.last_min)
        spans = [{"start": max(first, min(last, block.start_min)), "end": max(first, min(last, block.end_min)),
                  "kind": "next" if block is upcoming else "busy"} for block in busy]
        spans += [{"start": max(first, start), "end": min(last, end), "kind": "free"} for start, end in gaps]
        spans = [span for span in spans if span["end"] > span["start"]]
        spans.sort(key=lambda span: (span["start"], span["end"]))
        body.update(first=first, last=last, spans=spans[:protocol.SPANS_CAP],
                    ticks=[{"at": minute, "text": _tick_text(minute, hour24)}
                           for minute in range(first, last + 1, 120)][:protocol.TICKS_CAP],
                    now_min=now_min if today and first <= now_min <= last else -1)
    if today:
        body["now"] = cut(clock_parts(wall, hour24=hour24)[0], protocol.PILL_CAP)
    events = sum(1 for block in blocks if block.role != pictures.ROLE_BEFORE) + day.others_more   # a move is one
    meta = DOT.join([*(clean(label) for label in day.days), _plural(events, "event")])
    return _frame(frame_id, "calendar", _title_of(picture, title), meta=meta, body=body)


def _block_row(block: Any, day: Any, today: bool, now_min: int, upcoming: Any, hour24: bool) -> dict[str, Any]:
    if block.all_day:
        time_text = agenda.ALL_DAY_TEXT
    elif block.timeless:
        time_text = ""
    else:
        time_text = _range(block.start_min, block.end_min, hour24)
    words: list[str] = []
    state = ""
    if block.role in _ROLE_WORDS:
        state = "struck" if block.role in _STRUCK else "new"
        words.append(_ROLE_WORDS[block.role])
    elif block.chip:
        words.append(clean(block.chip))
    if today and not block.all_day and not block.timeless and block.role not in _STRUCK:
        if block.end_min <= now_min and not state:
            state = "past"
        elif block.start_min <= now_min < block.end_min:
            state = state or "now"
            words.insert(0, "now")
        elif block is upcoming:
            state = state or "next"
            minutes = block.start_min - now_min
            if minutes <= SOON_MIN:
                words.insert(0, f"in {minutes} min")
    if block.timeless:
        words.append(clean(block.time_text))
    if len(day.days) > 1 and 0 <= block.column < len(day.days):
        words.append(clean(day.days[block.column]))
    return {"time": cut(time_text, protocol.META_CAP), "title": clean(block.title or pictures.NO_TITLE,
                                                                       protocol.TITLE_CAP),
            "meta": cut(DOT.join(word for word in words if word), protocol.META_CAP), "state": state}


def _week(picture: Any, frame_id: str, now: datetime, title: str) -> dict[str, Any]:
    """The days of an Ask's calendar read: one row each (its label, its first titles, how many events)."""
    week = picture.week
    rows = [{"time": clean(column.label, protocol.META_CAP),
             "title": clean(DOT.join(column.titles), protocol.TITLE_CAP),
             "meta": _plural(int(column.count), "event"), "state": "today" if column.today else ""}
            for column in week.columns]
    body = {"first": 0, "last": 0, "now_min": -1, "ticks": [], "spans": [], "now": "",
            "rows": _capped(rows, _calendar_more)}
    return _frame(frame_id, "calendar", _title_of(picture, title), meta=_plural(int(week.total), "event"), body=body)


def _search(picture: Any, frame_id: str, now: datetime, title: str) -> dict[str, Any]:
    """One web search: the query as the title, each result's title and host (words, never a link)."""
    search = picture.search
    rows = []
    for hit in search.hits:
        host = clean(hit.host)
        words = clean(hit.title)
        rows.append({"lead": "", "text": cut(words or host or "(no title)", protocol.TITLE_CAP),
                     "meta": cut_host(host, protocol.META_CAP) if words else ""})
    text = "" if rows else pictures.SEARCH_UNREAD if search.unread else pictures.SEARCH_EMPTY
    body = {"text": text, "rows": _capped(rows, _text_more, search.more)}
    return _frame(frame_id, "text", _title_of(picture, title), meta=clean(picture.caption, protocol.META_CAP),
                  body=body)


REDIRECTED = ", redirected to "


def _caption(page: Any) -> str:
    """A page picture's caption: its host as words, and where it was redirected to (each host cut from its start when
    both do not fit: "…example.org")."""
    host = clean(getattr(page, "host", ""))
    final = clean(getattr(page, "final_host", ""))
    if not final:
        return cut_host(host, protocol.TITLE_CAP)
    host = host or "?"
    room = protocol.TITLE_CAP - len(REDIRECTED)
    first = cut_host(host, max(room // 2, room - protocol.units(final)))
    return f"{first}{REDIRECTED}{cut_host(final, room - protocol.units(first))}"


def _page(picture: Any, frame_id: str, now: datetime, hour24: bool, title: str) -> Built:
    page = picture.page
    meta = format_time(_wall(now), hour24=hour24)
    jpeg = board_jpeg(getattr(page, "image", b""))
    if jpeg is not None:
        message = {"type": "picture", "id": frame_id,
                   "src": protocol.PICTURE_PREFIX + base64.b64encode(jpeg).decode("ascii")}
        try:
            protocol.check_message(message, {frame_id})
        except protocol.Refused:
            jpeg = None
    if jpeg is None:
        frame = _frame(frame_id, "picture", _title_of(picture, title), meta=meta, body={"caption": _caption(page)},
                       status="empty", status_text=TOO_BIG)
        return Built((frame,), refused=1)
    frame = _frame(frame_id, "picture", _title_of(picture, title), meta=meta, body={"caption": _caption(page)})
    return Built((frame, message))


# --------------------------------------------------------------------------
# A page picture's JPEG
# --------------------------------------------------------------------------

_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}    # frame headers (not DHT, JPG, DAC)


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a JPEG from its frame header, read without decoding it; None when ``data`` is not one."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    at = 2
    while at + 4 <= len(data):
        if data[at] != 0xFF:
            return None
        marker = data[at + 1]
        if marker == 0xFF:                               # a fill byte
            at += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:     # no length
            at += 2
            continue
        if marker in (0xD9, 0xDA):                       # the end, or the scan, before any frame header
            return None
        length = int.from_bytes(data[at + 2:at + 4], "big")
        if length < 2:
            return None
        if marker in _SOF:
            if at + 9 > len(data):
                return None
            height = int.from_bytes(data[at + 5:at + 7], "big")
            width = int.from_bytes(data[at + 7:at + 9], "big")
            return (width, height) if width and height else None
        at += 2 + length
    return None


def board_jpeg(data: Any) -> bytes | None:
    """``data`` (a page picture's image) as a JPEG within the board's caps: as it is when it already is one of at most
    PICTURE_WIDTH px wide and protocol.PICTURE_BYTES; else re-encoded with QImage (scaled to PICTURE_WIDTH, the quality
    lowered step by step); None when even that does not fit (or it is no picture)."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return None
    data = bytes(data)
    size = jpeg_size(data)
    if size is not None and size[0] <= PICTURE_WIDTH and len(data) <= protocol.PICTURE_BYTES:
        return data
    return _reencode(data)


def _reencode(data: bytes) -> bytes | None:
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
        from PySide6.QtGui import QImage, QImageReader

        source = QBuffer()
        source.setData(QByteArray(data))
        source.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(source)
        size = reader.size()
        if not size.isValid() or size.width() <= 0 or size.height() <= 0 or \
                size.width() * size.height() > MAX_PIXELS:
            return None
        if size.width() > PICTURE_WIDTH:
            reader.setScaledSize(QSize(PICTURE_WIDTH, max(1, round(size.height() * PICTURE_WIDTH / size.width()))))
        image = reader.read()
        source.close()
        if image.isNull():
            return None
        if image.width() > PICTURE_WIDTH:              # a reader that does not scale while it reads
            image = image.scaledToWidth(PICTURE_WIDTH, Qt.TransformationMode.SmoothTransformation)
        image = image.convertToFormat(QImage.Format.Format_RGB32)
        for quality in JPEG_QUALITIES:
            out = QByteArray()
            buffer = QBuffer(out)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            saved = image.save(buffer, "JPG", quality)
            buffer.close()
            if saved and 0 < out.size() <= protocol.PICTURE_BYTES:
                return bytes(out.data())
        return None
    except Exception:  # noqa: BLE001 - no picture rather than an error
        return None


# --------------------------------------------------------------------------
# Research, to-dos, follow-ups
# --------------------------------------------------------------------------

def from_research(answer: Any, *, frame_id: str, title: str = "") -> Built:
    """A web research answer: the answer as text, its sources as rows (each source's title and its host as words; never
    its address, never a link). ``title`` (Ask's topic) else "Web research"."""
    frame_id = _check_id(frame_id)
    rows: list[dict[str, Any]] = []
    for item in tuple(getattr(answer, "sources", ()) or ()):
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue
        name, url = item[0], item[1]
        host = clean(actions.link_host(url) if isinstance(url, str) else "")
        words = clean(name) if isinstance(name, str) else ""
        if not words and not host:
            continue
        rows.append({"lead": "", "text": cut(words or host, protocol.TITLE_CAP),
                     "meta": cut_host(host, protocol.META_CAP) if words else ""})
    body = {"text": clean(getattr(answer, "answer", ""), protocol.TEXT_CAP), "rows": _capped(rows, _text_more)}
    return Built((_frame(frame_id, "text", clean(title, protocol.TITLE_CAP) or RESEARCH_TITLE,
                         meta=_plural(len(rows), "source"), body=body),))


def _title_key(title: Any) -> str:
    return " ".join(_NON_WORD_RE.sub(" ", str(title or "").casefold()).split())


def _due_point(due: Any, now: datetime) -> datetime | None:
    """A due date or time as a naive wall time to sort by (a date at its end, as agenda sorts deadlines)."""
    if isinstance(due, datetime):
        if due.utcoffset() is None:
            return due
        try:
            zone = now.tzinfo if isinstance(now, datetime) and now.utcoffset() is not None else None
            return (due.astimezone(zone) if zone is not None else due.astimezone()).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return due.replace(tzinfo=None)
    if isinstance(due, date):
        return datetime.combine(due, clock_time.max)
    return None


def _due_words(due: Any, now: datetime) -> str:
    if not isinstance(due, (date, datetime)):
        return ""
    try:
        return agenda.due_label(due, now)[0]
    except Exception:  # noqa: BLE001 - an odd date: no words
        return ""


def from_todos(todos: Iterable[Any], deadlines: Iterable[Any], *, frame_id: str, now: datetime,
               statuses: Mapping[str, str] | None = None) -> Built:
    """To-dos (see the module doc): a waiting one an amber diamond and WAITS, a done one a check and DONE, an open one
    its due words; dated ones by their due, undated after them, done ones last."""
    frame_id = _check_id(frame_id)
    decided = statuses if isinstance(statuses, Mapping) else {}
    items: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    keys: list[tuple[Any, str]] = []
    for action in todos or ():
        if getattr(action, "kind", "") != actions.TODO:
            continue
        status = decided.get(getattr(action, "id", ""), "")
        if status == actions.STATUS_DENIED:
            continue
        if status == actions.STATUS_DONE:
            state = "done"
        elif status in actions.DECIDED_STATUSES:
            state = "open"                      # its block was added: the to-do itself is still to do
        elif getattr(action, "decidable", False):
            state = "waits"                     # it waits on the owner's OK (Approvals)
        else:
            continue
        try:
            due = action.due()
        except Exception:  # noqa: BLE001 - an odd line: no due
            due = None
        point = _due_point(due, now)
        title = getattr(action, "title", "")
        items.append(((state == "done", point is None, point or datetime.max),
                      {"text": clean(title or pictures.NO_TITLE, protocol.TITLE_CAP),
                       "meta": _due_words(due, now) if state == "open" else "", "state": state}))
        keys.append((point.date() if point else None, _title_key(title)))
    for deadline in deadlines or ():
        due = getattr(deadline, "due", None)
        point = _due_point(due, now)
        if point is None:
            continue
        key = _title_key(getattr(deadline, "title", ""))
        if any(day == point.date() and other and key and (f" {key} " in f" {other} " or f" {other} " in f" {key} ")
               for day, other in keys):
            continue                            # the same to-do as a card's: the card's row says more
        items.append(((False, False, point),
                      {"text": clean(getattr(deadline, "title", "") or pictures.NO_TITLE, protocol.TITLE_CAP),
                       "meta": _due_words(due, now), "state": "open"}))
    items.sort(key=lambda pair: pair[0])
    rows = [row for _key, row in items]
    if not rows:
        return Built((_frame(frame_id, "todos", TODOS_TITLE, status="empty", status_text=TODOS_EMPTY),))
    waits = sum(1 for row in rows if row["state"] == "waits")
    meta = f"{len(rows)}{DOT}{waits} waits" if waits else str(len(rows))
    return Built((_frame(frame_id, "todos", TODOS_TITLE, meta=meta, body={"rows": _capped(rows, _todo_more)}),))


def from_followups(items: Iterable[Any], *, frame_id: str, now: datetime | None = None) -> Built:
    """Follow-ups as a mail list (see the module doc), in the order given (the app gives the waiting ones first)."""
    frame_id = _check_id(frame_id)
    rows: list[dict[str, Any]] = []
    waiting = owed = 0
    for item in items or ():
        kind = getattr(item, "kind", "")
        if kind == WAITING:
            waiting += 1
        elif kind == OWED:
            owed += 1
        else:
            continue
        age = ""
        since = getattr(item, "since", None)
        if isinstance(since, datetime) and isinstance(now, datetime):
            try:
                age = age_words(since, now)
            except (TypeError, ValueError, OverflowError):
                age = ""
        who = getattr(item, "who", "") or getattr(item, "who_address", "") or SENDER_UNKNOWN
        rows.append({"sender": clean(who, protocol.TITLE_CAP), "time": cut(age, protocol.META_CAP),
                     "subject": clean(getattr(item, "subject", "") or pictures.NO_SUBJECT, protocol.TITLE_CAP),
                     "meta": kind.upper(), "unread": False})
    if not rows:
        return Built((_frame(frame_id, "mail_list", FOLLOWUPS_TITLE, status="empty",
                             status_text=persona.FOLLOWUPS_EMPTY),))
    return Built((_frame(frame_id, "mail_list", FOLLOWUPS_TITLE, meta=persona.followups_meta(waiting, owed),
                         body={"rows": _capped(rows, _mail_more)}),))


__all__ = ["Built", "DOT", "ELLIPSIS", "FOLLOWUPS_TITLE", "FREE_TITLE", "MORE", "PICTURE_WIDTH", "REDIRECTED",
           "RESEARCH_TITLE", "TODOS_EMPTY", "TODOS_TITLE", "TOO_BIG", "WAITS_EMAIL", "WAITS_REPLY", "board_jpeg", "clean",
           "clean_text", "cut", "cut_host", "from_followups", "from_picture", "from_research", "from_todos", "jpeg_size"]
