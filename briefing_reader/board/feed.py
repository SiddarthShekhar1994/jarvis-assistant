"""What the board holds (the new look, parts 5a and 5b; DESIGN-SPEC 3.1, 3.3 and 3.5): one group per answer and the frames
in it, kept in Python - the app is the source of truth - and the messages that put them on the page (BoardView.apply).
Qt-free and pure: no file, no log, no clock (the caller says when: ``now`` is a monotonic time in seconds); nothing here
raises for odd data.

    BoardFeed(max_frames=protocol.MAX_FRAMES, max_groups=protocol.MAX_GROUPS, close_s=CLOSE_S, clear_s=CLEAR_S)
        frame_id(answer, source, merge="", key="") -> str
            the frame id a source's frame takes, made before its builder runs (board.frames builders take it): the
            same id again for the same source of the same answer (an update in place: a page picture that comes ready),
            and the id of the frame already there when ``merge`` names one of the answer's frames (a mail thread and
            the reply to it that waits on Approvals: "account:thread id" for both). Part 5c: ``key`` is the frame's
            source - the same thread, day, page or list ("mail:work:t1", "day:2026-11-17") - and a frame of another
            answer built from it, still on the board (not closed, not cleared), lends its id: it is built again
        put(answer, source, messages, *, when, topic="", merge="", approvals="", rank=0.0, park=False, key="",
            not_ready=False) -> list[dict]
            a builder's messages (board.frames.Built.messages: the frame, then its picture) for ``answer``'s group,
            and returns what to send, in order: the room made (whole groups leaving), the group (new: on top of the
            board, its header ``when`` - "9:41 AM" - and ``topic``, else the first frame's title, at most 40
            characters; the group before it then older), the frame with its group, its flags (pinned, closed) and its
            satellite's fact (board.facts), its picture, the group's new order when it changed, and the bar's counts
            when they changed. ``park`` (the owner is reading the board): a NEW group parks - out of sight on the page,
            with the pill "New answer above · Show" - and the others stay as they are until it lands. A frame and the
            reply that waits on it are ONE frame: the mail as read, with the reply's amber chip ("Reply waits on
            Approvals"), whose go_approvals opens the reply's card (``approvals``: that card's action id). In a group a
            frame that waits on the owner comes first, then the frames in the order Jarvis speaks of them (``rank``,
            then their arrival). A frame built again from its ``key`` by a later answer stays in its own place: when
            what it says differs (``substance``: its own data, not the clock's) it is sent again with "changed" (the
            page's one light, then CHANGED stays; take_changed() lists it once) - only when the owner has seen it
            (landed, not closed, not cleared: else it is only brought up to date); when it says the same, nothing at
            all is sent (a waiting reply's newer card still becomes what its chip opens; a newer picture of the same
            page is shown, quietly). A frame that shows what it says keeps it while its new build is a LIVE picture
            on its way or failed (``not_ready``: board.frames.Built says so): it changes only when the new one is
            ready. A builder's own final state is never held back: an emptied to-do list ("empty") is sent, changed
            when the owner has seen the list
        land() -> list[dict]                       the parked groups land (on top); every other group is then older,
                                                   so its unpinned frames fold to satellites on the page
        pin(frame_id, on) / close(frame_id, now) / bring_back(frame_id) -> list[dict]
            the page's own change (it showed it at once) as the app decides it: a 7th pin is refused (MAX_PINS); a
            closed frame leaves ``close_s`` later (due); the frame's message again when the app's flags differ from
            what the page did, and the bar when its counts changed
        clear_unpinned(now) / restore() -> list[dict]
            the bar's Clear: every unpinned frame that has landed goes out of sight (a ``cleared`` message: the row
            "Board cleared · 8 FRAMES · Bring back") and leaves ``clear_s`` later; restore() brings them back
        due(now) -> list[dict]; next_due() -> float | None
            closed frames whose time is up leave (``remove``; their group with them when it is empty), and so do the
            cleared ones; when the next one is due
        set_pinned(frame_id, on) -> bool           pinned or not (False: refused, the 7th); a group with a pinned frame
                                                   never leaves for room, and nothing pinned is ever cleared
        clearing() -> bool                         a Clear's Bring back is offered (its row shows, also with nothing
                                                   left on the board: the desk stays for it)
        take_changed() -> list[str]                the frames sent again with "changed" since the last call
        set_compact(compact) -> list[dict]         the bar without its frames count (the folded Dock's column)
        holds(key) -> bool                         a frame built from ``key`` is on the board (not closed, not cleared):
                                                   the desk builds it again for a later answer about it even when its
                                                   source has emptied ("todos", "followups": their own empty state)
        set_turn(answer, entry_id)                 the conversation turn that put the answer's frames up
        turn_frames(entry_id) -> (group id, count) its group and how many of its frames are on the board now (cleared
                                                   ones are not; ("", 0): none - its token goes)
        approvals_for(frame_id) -> str             the card a frame's go_approvals opens ("" none)
        frames(), waits(), pinned(), cleared(), parked_groups(), groups(), group_of(frame_id), group_for(answer),
        frame_ids(), flags(frame_id), counts(), frame_message(frame_id), picture_message(frame_id)
        clear()                                    nothing on the board any more (the app's own reset)
        resend()                                   the board as it is, for a page that has nothing

    ReadingWatch(quiet_s=QUIET_S)      "the owner reads the board" (DESIGN-SPEC 3.5): the pointer over it, the focus in
                                       it, or a scroll or key in it within ``quiet_s``; note(pointer, focus, now) at
                                       each sign; reading(now); quiet_at(now) (when it stops, None while the pointer or
                                       the focus is there)

Limits (DESIGN-SPEC 3.5): ``max_frames`` frames and ``max_groups`` groups; past them the oldest group with no pinned
frame leaves whole (never the answer being added to; frames a Clear took away leave first); when none can leave, the
new frame is refused (counted). At most MAX_PINS pins. Frames last for the session only: nothing here is ever saved.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import ask_frames, protocol
from .facts import cut as _cut
from .facts import fact_of

CLOSE_S = 8.0          # a closed frame offers Bring back this long, then leaves (DESIGN-SPEC 3.4 "Closed")
CLEAR_S = 8.0          # the cleared row offers Bring back this long (DESIGN-SPEC 3.5 "Clear")
QUIET_S = 8.0          # a sign of reading counts this long; a parked group lands after this much quiet


def _same(message: dict[str, Any]) -> str:
    return json.dumps(message, sort_keys=True, ensure_ascii=True)


_DOT = " · "                                       # board.frames.DOT: between a meta's words
_CLOCK_WORDS = re.compile(r"(?:now|in \d+ min)", re.IGNORECASE)   # a calendar row's words that are the clock's
_MORE = re.compile(r"\+\d+ MORE")                  # board.frames.MORE: the count of the rows that did not fit
_MAIL_DAY = re.compile(r"^.*\b\d{4},\s*")          # a mail's day before its clock ("Fri Nov 13 2026, "): shown only
                                                   # once the mail is not today's
_EVENTS = re.compile(r"\d+ events?")               # a calendar's own count in its header ("23 events")


def _header_counts(kind: str, meta: Any) -> str:
    """The counts a frame's header (its meta) holds as its own data (part 5c fixes, round 2): a calendar's "23 events"
    (its days apart), a to-do list's "9 · 2 waits", a mail list's "3 waiting · 1 owed" - they count rows past "+N MORE"
    too (a week's, every event read); "" for the other kinds, whose meta is a time (the clock's)."""
    meta = str(meta or "")
    if kind == "calendar":
        return _DOT.join(part.strip() for part in meta.split(_DOT) if _EVENTS.fullmatch(part.strip()))
    if kind in ("todos", "mail_list"):
        return meta
    return ""


def _own_words(meta: Any) -> str:
    """A calendar row's meta without the clock's own words ("now", "in 19 min"): its counts ("3 events", "+2 MORE")
    and the event's own words stay."""
    return _DOT.join(part for part in str(meta or "").split(_DOT)
                     if part.strip() and not _CLOCK_WORDS.fullmatch(part.strip()))


# Part 6: words of a timeline's node that are the clock's, never its own ("today", "tomorrow", "in 3 days", "2 h ago").
_RELATIVE_WORDS = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|now|this (?:morning|afternoon|evening|week|month)|(?:next|last) "
    r"(?:week|month)|in \d+ ?(?:min|mins|minutes?|h|hrs?|hours?|d|days?|weeks?)|\d+ ?(?:min|mins|minutes?|h|hrs?|"
    r"hours?|d|days?|weeks?) ago)\b", re.IGNORECASE)


def _no_clock(words: Any) -> str:
    """A model's words without the clock's own ones (a later answer that says "Tomorrow" of the same day is the same)."""
    return " ".join(_RELATIVE_WORDS.sub(" ", str(words or "")).split()).casefold()


def _items(body: dict[str, Any], key: str) -> list[dict[str, Any]]:
    found = body.get(key)
    return [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []


def _table_rows(body: dict[str, Any]) -> list[list[Any]]:
    """A table body's rows (lists of cells; anything else left out)."""
    found = body.get("rows")
    return [row for row in found if isinstance(row, list)] if isinstance(found, list) else []


def substance(frame: dict[str, Any]) -> str:
    """What a frame says, as one string to compare (part 5c's "changed"): its kind, title, status, its header's own
    counts (_header_counts: a week's "23 events") and its body's own data - never the clock's: a day's events (time,
    title and their own words and counts - a week's "3 events", the "+2 MORE" row; not its now line, its "IN 19 MIN" or
    its free gaps), a mail's sender, text and own time (not the day shown before it once it is not today's), a mail
    list's senders, subjects and what waits (not their ages), to-dos' words and states and the "+2 MORE" row's count
    (not their due words); part 6's typed frames, as the owner sees them: the title as its key folds it (case and
    spaces aside), a table's columns and cells and which row changed (not its change's words or its source), a chart's
    bars' labels and shown words ("3.4k": 3400 and 3400.0 are the same) and which changed from what (the old value in
    those words; not its delta line), a timeline's nodes' words and states (never where TODAY stands) - a table's,
    a chart's and a timeline's words without the clock's own ("today", "tomorrow", "in 3 days", "2 days ago") - and
    maths' steps and answer; the other kinds' bodies as they are. Never raises."""
    try:
        kind = str(frame.get("kind", ""))
        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        rows = body.get("rows") if isinstance(body.get("rows"), list) else []
        rows = [row for row in rows if isinstance(row, dict)]
        if kind == "calendar":
            stable: Any = [[row.get("time", ""), row.get("title", ""), _own_words(row.get("meta", ""))]
                           for row in rows if row.get("state") != "free"]
        elif kind == "mail_thread":
            stable = [body.get("sender", ""), _MAIL_DAY.sub("", str(body.get("time", "") or "")), body.get("text", "")]
        elif kind == "mail_list":
            stable = [[row.get("sender", ""), row.get("subject", ""), row.get("meta", "")] for row in rows]
        elif kind == "todos":
            stable = [[row.get("text", ""), row.get("state", ""),
                       row.get("meta", "") if _MORE.fullmatch(str(row.get("meta", "") or "")) else ""]
                      for row in rows]
        elif kind == "table":                      # part 6: its columns and cells, which row changed, its unit
            columns = body.get("columns") if isinstance(body.get("columns"), list) else []
            cells = [[_no_clock(cell) for cell in row] for row in _table_rows(body)]
            stable = [[_no_clock(column) for column in columns], cells, body.get("changed"), body.get("unit")]
        elif kind == "chart":                      # its bars (labels and words), which changed and from what, its unit
            bars = _items(body, "bars")
            was = body.get("was")
            if was is not None:                    # the ghost tick's value in the words of the chart's own scale
                was = ask_frames.chart_word(was, [bar.get("value") for bar in bars])
            stable = [[[_no_clock(bar.get("label")), bar.get("text")] for bar in bars], body.get("changed"), was,
                      body.get("unit")]
        elif kind == "timeline":                   # its nodes' own words and states (never "today", nor where TODAY is)
            stable = [[_no_clock(node.get("when")), _no_clock(node.get("words")), node.get("state")]
                      for node in _items(body, "nodes")]
        elif kind == "maths":                      # its steps and its answer
            stable = [body.get("steps"), body.get("answer")]
        else:
            stable = body
        title = frame.get("title", "")
        if kind in ask_frames.KINDS:               # a typed frame's title as its key folds it (the key names it)
            title = ask_frames.frame_key(kind, title)
        return json.dumps([kind, title, frame.get("status", "ok"), frame.get("status_text", ""),
                           _header_counts(kind, frame.get("meta", "")), stable], sort_keys=True, ensure_ascii=True,
                          default=str)
    except (TypeError, ValueError, AttributeError):
        return ""


@dataclass
class _Part:
    """One source's frame (and picture) as its builder made it."""

    source: str
    frame: dict[str, Any]
    picture: dict[str, Any] | None
    approvals: str
    seq: int


@dataclass
class _Frame:
    """A frame on the board: one source's, or a mail thread's merged with the reply that waits on it."""

    id: str
    group: str
    merge: str
    seq: int                       # its arrival (the order Jarvis speaks of frames ranks first)
    rank: float
    parts: dict[str, _Part] = field(default_factory=dict)
    pinned: bool = False
    closed: bool = False
    cleared: bool = False          # the bar's Clear took it away (out of sight until Bring back or it leaves)
    leave_at: float | None = None  # a closed frame leaves then
    sent: str = ""                 # the frame message the page has (unchanged ones are not sent again)
    sent_picture: str = ""
    key: str = ""                  # its source (part 5c): a later answer built from it builds this frame again
    changed: bool = False          # it was built again and said something else: CHANGED stays

    def shown(self) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
        """(the frame message, its picture message or None, the card go_approvals opens): a merged frame is the mail as
        read with the waiting reply's chip; a single one is itself."""
        parts = sorted(self.parts.values(), key=lambda part: part.seq)
        waiting = [part for part in parts if part.frame.get("waits")]
        read = [part for part in parts if not part.frame.get("waits")]
        if waiting and read:
            base, chip = read[-1], waiting[-1]
            frame = dict(base.frame)
            frame["waits"] = chip.frame.get("waits", "")
            return frame, base.picture, chip.approvals
        last = parts[-1]
        return dict(last.frame), last.picture, last.approvals

    def waits(self) -> bool:
        return any(part.frame.get("waits") for part in self.parts.values())


@dataclass
class _Group:
    id: str
    answer: Hashable
    time: str
    topic: str
    seq: int
    frames: list[str] = field(default_factory=list)   # the board order
    page_order: list[str] = field(default_factory=list)   # the order the page has
    older: bool = False
    parked: bool = False           # it came while the owner read the board: out of sight until it lands
    sent: str = ""
    turn: int = 0


class BoardFeed:
    """See the module doc."""

    def __init__(self, *, max_frames: int = protocol.MAX_FRAMES, max_groups: int = protocol.MAX_GROUPS,
                 close_s: float = CLOSE_S, clear_s: float = CLEAR_S) -> None:
        self.max_frames = max(1, min(int(max_frames), protocol.MAX_FRAMES))
        self.max_groups = max(1, min(int(max_groups), protocol.MAX_GROUPS))
        self.close_s = max(0.0, float(close_s))
        self.clear_s = max(0.0, float(clear_s))
        self._frames: dict[str, _Frame] = {}
        self._groups: dict[str, _Group] = {}          # in creation order
        self._by_answer: dict[Hashable, str] = {}     # answer -> its group id
        self._ids: dict[tuple[Hashable, str], str] = {}   # (answer, source) / (answer, "merge:" + key) -> frame id
        self._dismissed: set[Hashable] = set()        # answers the owner closed or cleared away: no late frame
        self._clear_until: float | None = None        # the cleared frames leave then (Bring back until)
        self._compact = False
        self._bar_sent = ""
        self._seq = 0
        self._next_frame = 0
        self._next_group = 0
        self._just_changed: list[str] = []
        self._counts = {"frames": 0, "groups": 0, "left": 0, "refused": 0, "merged": 0, "pins_refused": 0,
                        "closed": 0, "cleared": 0, "parked": 0, "landed": 0, "changed": 0, "rebuilt": 0}

    # ---- ids ---------------------------------------------------------------------------------------------------------

    def frame_id(self, answer: Hashable, source: str, merge: str = "", key: str = "") -> str:
        """See the module doc."""
        found = self._ids.get((answer, source))
        if found is None and merge:
            found = self._ids.get((answer, "merge:" + merge))
        if found is None and key:
            found = self._keyed(key)
        if found is None:
            self._next_frame += 1
            found = f"f{self._next_frame}"
        self._ids[(answer, source)] = found
        if merge:
            self._ids.setdefault((answer, "merge:" + merge), found)
        return found

    def _keyed(self, key: str) -> str | None:
        """The frame on the board built from ``key`` (not closed, not taken away by a Clear), else None."""
        for frame in self._frames.values():
            if frame.key == key and not frame.closed and not frame.cleared:
                return frame.id
        return None

    def holds(self, key: str) -> bool:
        """A frame built from ``key`` is on the board (not closed, not taken away by a Clear): a later answer about it
        builds it again, also with nothing left in its source (its own empty state)."""
        return bool(key) and self._keyed(str(key)) is not None

    # ---- reading ---------------------------------------------------------------------------------------------------

    def frames(self) -> int:
        """The frames on the board (a closed one until it leaves, a parked one too; never those a Clear took away)."""
        return sum(1 for frame in self._frames.values() if not frame.cleared)

    def waits(self) -> int:
        return sum(1 for frame in self._frames.values() if frame.waits() and not frame.cleared)

    def pinned(self) -> int:
        return sum(1 for frame in self._frames.values() if frame.pinned)

    def cleared(self) -> list[str]:
        """The frames the bar's Clear took away (still offered back), in board order."""
        return [frame_id for frame_id in self.frame_ids() if self._frames[frame_id].cleared]

    def clearing(self) -> bool:
        """A Clear's Bring back is offered now (its row shows until it is used or CLEAR_S pass)."""
        return self._clear_until is not None

    def take_changed(self) -> list[str]:
        """The frames sent again with "changed" (built again, saying something else) since the last call."""
        found, self._just_changed = list(dict.fromkeys(self._just_changed)), []
        return [frame_id for frame_id in found if frame_id in self._frames]

    def parked_groups(self) -> list[str]:
        """The groups parked out of sight (they came while the owner read the board), oldest first."""
        return [group.id for group in sorted(self._groups.values(), key=lambda group: group.seq) if group.parked]

    def groups(self) -> list[str]:
        """The group ids, newest first (the board's order)."""
        return [group.id for group in sorted(self._groups.values(), key=lambda group: -group.seq)]

    def group_order(self, group_id: str) -> list[str]:
        group = self._groups.get(group_id)
        return list(group.frames) if group is not None else []

    def group_header(self, group_id: str) -> tuple[str, str, bool]:
        group = self._groups.get(group_id)
        return (group.time, group.topic, group.older) if group is not None else ("", "", False)

    def frame_ids(self) -> list[str]:
        """Every frame on the board, in board order (the newest group first, each group's own order)."""
        return [frame_id for group_id in self.groups() for frame_id in self._groups[group_id].frames]

    def group_of(self, frame_id: str) -> str:
        frame = self._frames.get(frame_id)
        return frame.group if frame is not None else ""

    def group_for(self, answer: Hashable) -> str:
        return self._by_answer.get(answer, "")

    def is_parked(self, group_id: str) -> bool:
        group = self._groups.get(group_id)
        return bool(group is not None and group.parked)

    def flags(self, frame_id: str) -> dict[str, bool]:
        """A frame's own flags as the app has them ({} when it is not on the board)."""
        frame = self._frames.get(frame_id)
        if frame is None:
            return {}
        return {"pinned": frame.pinned, "closed": frame.closed, "cleared": frame.cleared}

    def frame_message(self, frame_id: str) -> dict[str, Any] | None:
        frame = self._frames.get(frame_id)
        return self._shown(frame) if frame is not None else None

    def picture_message(self, frame_id: str) -> dict[str, Any] | None:
        """The picture message the frame shows (with the frame's id), None without one (part 7: the second screen's
        read-only tiles take it as the page does)."""
        frame = self._frames.get(frame_id)
        picture = frame.shown()[1] if frame is not None else None
        if picture is None:
            return None
        message = dict(picture)
        message["id"] = frame_id
        return message

    def approvals_for(self, frame_id: str) -> str:
        frame = self._frames.get(frame_id)
        return frame.shown()[2] if frame is not None else ""

    def set_turn(self, answer: Hashable, entry_id: int) -> None:
        group = self._groups.get(self._by_answer.get(answer, ""))
        if group is not None and isinstance(entry_id, int) and entry_id > 0:
            group.turn = entry_id

    def turn_frames(self, entry_id: int) -> tuple[str, int]:
        for group in self._groups.values():
            if group.turn and group.turn == entry_id:
                return group.id, sum(1 for frame_id in group.frames if not self._frames[frame_id].cleared)
        return "", 0

    def counts(self) -> dict[str, int]:
        """Counts only (for the log): frames and groups made, groups that left, frames refused and merged, pins refused,
        frames closed and cleared, groups parked and landed."""
        return dict(self._counts)

    # ---- the bar ---------------------------------------------------------------------------------------------------

    def bar(self) -> dict[str, Any]:
        return {"type": "bar", "frames": self.frames(), "waits": self.waits(), "pinned": self.pinned(),
                "compact": self._compact}

    def set_compact(self, compact: bool) -> list[dict[str, Any]]:
        self._compact = bool(compact)
        return self._bar_if_changed(force=False)

    def _bar_if_changed(self, *, force: bool = False) -> list[dict[str, Any]]:
        message = self.bar()
        text = _same(message)
        if text == self._bar_sent and not force:
            return []
        self._bar_sent = text
        return [message]

    def bar_again(self) -> list[dict[str, Any]]:
        """The bar as it is (a page that came up again, or a desk that formed)."""
        return self._bar_if_changed(force=True)

    # ---- putting frames up -----------------------------------------------------------------------------------------

    def put(self, answer: Hashable, source: str, messages: Sequence[dict[str, Any]], *, when: str = "",
            topic: str = "", merge: str = "", approvals: str = "", rank: float = 0.0,
            park: bool = False, key: str = "", not_ready: bool = False) -> list[dict[str, Any]]:
        """See the module doc."""
        frame_message = next((dict(item) for item in messages if isinstance(item, dict)
                              and item.get("type") == "frame"), None)
        if frame_message is None:
            return []
        picture = next((dict(item) for item in messages if isinstance(item, dict) and item.get("type") == "picture"),
                       None)
        frame_id = str(frame_message.get("id", ""))
        if not protocol.is_frame_id(frame_id):
            self._counts["refused"] += 1
            return []
        out: list[dict[str, Any]] = []
        frame = self._frames.get(frame_id)
        group = self._groups.get(self._by_answer.get(answer, ""))
        if frame is not None and key and frame.key == key and (group is None or frame.group != group.id):
            return self._rebuild(frame, source, frame_message, picture, approvals, bool(not_ready))
        if frame is None:
            if group is None and answer in self._dismissed:
                self._counts["refused"] += 1       # the owner closed or cleared this answer away: no late frame
                return []
            out += self._make_room(answer, new_group=group is None)
            group = self._groups.get(self._by_answer.get(answer, ""))   # (a Clear's frames may have left with it)
            if group is None and answer in self._dismissed:
                self._counts["refused"] += 1
                return out
            if len(self._frames) >= self.max_frames or (group is None and len(self._groups) >= self.max_groups):
                self._counts["refused"] += 1
                return out
            if group is None:
                group, made = self._new_group(answer, when, topic or str(frame_message.get("title", "")), park)
                out += made
            self._seq += 1
            frame = _Frame(frame_id, group.id, merge, self._seq, float(rank), key=str(key or ""))
            self._frames[frame_id] = frame
            group.frames.append(frame_id)
            self._counts["frames"] += 1
        elif group is None or frame.group != group.id:
            self._counts["refused"] += 1           # a frame id of another answer: never moved between groups
            return out
        if merge and source not in frame.parts and frame.parts:
            self._counts["merged"] += 1
        self._seq += 1
        frame.parts[source] = _Part(source, frame_message, picture, str(approvals or ""), self._seq)
        if merge and not frame.merge:
            frame.merge = merge
        shown = self._shown(frame)
        text = _same(shown)
        if text != frame.sent:
            frame.sent = text
            out.append(shown)
            if group.id and frame_id not in group.page_order:
                group.page_order.append(frame_id)   # the page puts a new frame at its group's end
        shown_picture = frame.shown()[1]
        if shown_picture is not None:
            picture_message = dict(shown_picture)
            picture_message["id"] = frame_id
            picture_text = _same(picture_message)
            if picture_text != frame.sent_picture:
                frame.sent_picture = picture_text
                out.append(picture_message)
        out += self._order(group)
        out += self._bar_if_changed()
        return out

    def _seen(self, frame: _Frame) -> bool:
        """The owner has the frame in sight as the board shows it: its group landed, not closed, not taken by a Clear."""
        group = self._groups.get(frame.group)
        return group is not None and not group.parked and not frame.closed and not frame.cleared

    def _rebuild(self, frame: _Frame, source: str, message: dict[str, Any], picture: dict[str, Any] | None,
                 approvals: str, not_ready: bool = False) -> list[dict[str, Any]]:
        """A frame on the board built again from its key by another answer (see the module doc): in its own place;
        "changed" when what it says differs and the owner has seen it as it was (never for a frame parked, closed or
        taken by a Clear: it is only brought up to date), nothing at all when it says the same. A frame that shows
        what it says keeps showing it while the new one is a LIVE picture on its way or failed (``not_ready``: never
        "Opening..." or "failed" over a good one) - a builder's own final state ("No to-dos or deadlines.") is what
        the frame says now; the same words with a newer picture (a page captured again) are shown, quietly."""
        before, before_picture, _approvals = frame.shown()
        if not_ready and before.get("status", "ok") == "ok" and not message.get("waits"):
            return []                              # the good one stays until the new one is ready
        old = frame.parts.get(source)
        self._seq += 1
        frame.parts[source] = _Part(source, message, picture, str(approvals or ""), self._seq)
        after, after_picture, _approvals = frame.shown()
        if message.get("waits"):
            pass                                   # a waiting reply's newer card: what its chip opens (no figure)
        elif substance(after) == substance(before):
            if (after_picture or {}).get("src") == (before_picture or {}).get("src"):
                if old is None:                    # the same words again: nothing changes
                    del frame.parts[source]
                else:
                    frame.parts[source] = old
                return []
        elif before.get("status", "ok") == "ok" and not not_ready and self._seen(frame):
            frame.changed = True                   # (also to a final "empty": the list was emptied)
            self._just_changed.append(frame.id)
            self._counts["changed"] += 1
        self._counts["rebuilt"] += 1
        out: list[dict[str, Any]] = []
        shown = self._shown(frame)
        text = _same(shown)
        if text != frame.sent:
            frame.sent = text
            out.append(shown)
        shown_picture = frame.shown()[1]
        if shown_picture is not None:
            picture_message = dict(shown_picture)
            picture_message["id"] = frame.id
            picture_text = _same(picture_message)
            if picture_text != frame.sent_picture:
                frame.sent_picture = picture_text
                out.append(picture_message)
        group = self._groups.get(frame.group)
        if group is not None:
            out += self._order(group)
        return out + self._bar_if_changed()

    def _shown(self, frame: _Frame) -> dict[str, Any]:
        """The frame message as the page should have it: the builder's (merged) frame, its id and group, the app's own
        flags (changed too) and its satellite's fact."""
        shown = frame.shown()[0]
        shown["id"] = frame.id
        shown["group"] = frame.group
        shown["pinned"] = frame.pinned
        shown["closed"] = frame.closed
        shown["changed"] = frame.changed or bool(shown.get("changed", False))
        shown["fact"] = fact_of(shown)
        return shown

    def _new_group(self, answer: Hashable, when: str, topic: str, park: bool) -> tuple[_Group, list[dict[str, Any]]]:
        self._next_group += 1
        self._seq += 1
        group = _Group(f"g{self._next_group}", answer, _cut(str(when or ""), protocol.TIME_CAP),
                       _cut(str(topic or ""), protocol.TOPIC_CAP), self._seq, parked=bool(park))
        self._groups[group.id] = group
        self._by_answer[answer] = group.id
        self._counts["groups"] += 1
        self._counts["parked"] += 1 if park else 0
        out: list[dict[str, Any]] = []
        if not park:
            out += self._refresh_older()           # the one before it goes quiet (INK_4): its frames fold
        out += self._group_message(group)
        return group, out

    def _group_message(self, group: _Group) -> list[dict[str, Any]]:
        """The group as the page should have it: its header, the order of its frames the page has, older, parked."""
        message: dict[str, Any] = {"type": "group", "id": group.id, "time": group.time, "topic": group.topic,
                                   "frames": list(group.page_order), "older": group.older}
        if group.parked:
            message["parked"] = True
        group.sent = _same(message)
        return [message]

    def _refresh_older(self) -> list[dict[str, Any]]:
        """Every group but the newest that has landed is older (its unpinned frames fold to satellites on the page; a
        parked group changes nothing until it lands): a group message for each that changed."""
        landed = [group for group in self._groups.values() if not group.parked]
        newest = max(landed, key=lambda group: group.seq, default=None)
        out: list[dict[str, Any]] = []
        for group in sorted(self._groups.values(), key=lambda group: group.seq):
            if group.parked:
                continue
            older = group is not newest
            if older != group.older:
                group.older = older
                out += self._group_message(group)
        return out

    def _order(self, group: _Group) -> list[dict[str, Any]]:
        """The group's board order: what waits on the owner first, then the order Jarvis speaks of them; a group
        message only when the page's order (a new frame goes to its group's end there) differs from it."""
        group.frames.sort(key=lambda frame_id: (0 if self._frames[frame_id].waits() else 1,
                                                self._frames[frame_id].rank, self._frames[frame_id].seq))
        if group.page_order == group.frames:
            return []
        group.page_order = list(group.frames)
        return self._group_message(group)

    def _make_room(self, answer: Hashable, *, new_group: bool) -> list[dict[str, Any]]:
        """Whole groups leave, the oldest with no pinned frame first (never ``answer``'s), until a new frame (and a
        new group) fits; frames a Clear took away leave before any of them."""
        out: list[dict[str, Any]] = []
        if self._full(new_group) and self._clear_until is not None:
            out += self._finish_clear()
        while self._full(new_group):
            victim = next((group for group in sorted(self._groups.values(), key=lambda group: group.seq)
                           if group.answer != answer and not any(self._frames[frame_id].pinned
                                                                 for frame_id in group.frames)), None)
            if victim is None:
                break
            out += self._drop_group(victim)
        if out:
            out += self._refresh_older()
        return out

    def _full(self, new_group: bool) -> bool:
        return len(self._frames) + 1 > self.max_frames or (new_group and len(self._groups) + 1 > self.max_groups)

    def _drop_group(self, group: _Group) -> list[dict[str, Any]]:
        gone = set(group.frames)
        for frame_id in gone:
            self._frames.pop(frame_id, None)
        self._ids = {key: value for key, value in self._ids.items() if value not in gone}
        self._groups.pop(group.id, None)
        if self._by_answer.get(group.answer) == group.id:
            del self._by_answer[group.answer]
        self._counts["left"] += 1
        return [{"type": "remove_group", "id": group.id}]

    # ---- parking ---------------------------------------------------------------------------------------------------

    def land(self) -> list[dict[str, Any]]:
        """See the module doc: the parked groups land (each one's message without "parked"), then every other group
        is older; the bar again when its counts changed."""
        parked = [group for group in sorted(self._groups.values(), key=lambda group: group.seq) if group.parked]
        if not parked:
            return []
        for group in parked:
            group.parked = False
            self._counts["landed"] += 1
        out = self._refresh_older()
        named = {message["id"] for message in out}
        for group in parked:
            if group.id not in named:
                out += self._group_message(group)
        return out + self._bar_if_changed()

    # ---- the owner's own ways: pin, close, bring back, Clear ----------------------------------------------------

    def set_pinned(self, frame_id: str, on: bool) -> bool:
        """See the module doc: True when the frame is now as asked."""
        frame = self._frames.get(frame_id)
        if frame is None:
            return False
        if on and not frame.pinned and (frame.cleared or self.pinned() >= protocol.MAX_PINS):
            return False
        frame.pinned = bool(on)
        return True

    def _note_page(self, frame: _Frame, **flags: bool) -> None:
        """The page changed ``flags`` by itself (optimistically): what it has now, so a re-send compares with that."""
        if not frame.sent:
            return
        try:
            data = json.loads(frame.sent)
        except ValueError:
            return
        data.update(flags)
        frame.sent = _same(data)

    def _again_if_differs(self, frame: _Frame) -> list[dict[str, Any]]:
        """The frame's message again when what the page has differs from the app's own (its flags)."""
        shown = self._shown(frame)
        text = _same(shown)
        if text == frame.sent:
            return []
        frame.sent = text
        return [shown]

    def pin(self, frame_id: str, on: bool) -> list[dict[str, Any]]:
        """The page's P (it showed the pin, or took it away, at once): see the module doc."""
        frame = self._frames.get(frame_id)
        if frame is None:
            return []
        self._note_page(frame, pinned=bool(on))
        if not self.set_pinned(frame_id, on):
            self._counts["pins_refused"] += 1      # the 7th: the page shows why in the frame's meta
        return self._again_if_differs(frame) + self._bar_if_changed()

    def close(self, frame_id: str, now: float) -> list[dict[str, Any]]:
        """The page's Delete (it shows the "Closed: ..." row with Bring back): the frame leaves ``close_s`` from now."""
        frame = self._frames.get(frame_id)
        if frame is None:
            return []
        self._note_page(frame, closed=True)
        group = self._groups.get(frame.group)
        if not frame.closed and not frame.cleared and group is not None and not group.parked:
            frame.closed = True
            frame.leave_at = float(now) + self.close_s
            self._counts["closed"] += 1
        return self._again_if_differs(frame) + self._bar_if_changed()

    def bring_back(self, frame_id: str) -> list[dict[str, Any]]:
        """The closed row's Bring back (the page showed the frame again at once)."""
        frame = self._frames.get(frame_id)
        if frame is None:
            return []
        self._note_page(frame, closed=False)
        frame.closed = False
        frame.leave_at = None
        return self._again_if_differs(frame) + self._bar_if_changed()

    def clear_unpinned(self, now: float) -> list[dict[str, Any]]:
        """The bar's Clear: every unpinned frame that has landed goes out of sight for ``clear_s`` (one Clear's Bring
        back at a time: the frames of the one before leave now), then leaves; a parked answer stays parked."""
        out: list[dict[str, Any]] = []
        if self._clear_until is not None:
            out += self._finish_clear()
        taken = [frame_id for frame_id in self.frame_ids()
                 if not self._frames[frame_id].pinned and not self._frames[frame_id].cleared
                 and not self.is_parked(self._frames[frame_id].group)]
        if not taken:
            return out + self._bar_if_changed()
        for frame_id in taken:
            self._frames[frame_id].cleared = True
        self._counts["cleared"] += len(taken)
        self._clear_until = float(now) + self.clear_s
        out.append({"type": "cleared", "frames": self.cleared()})
        return out + self._bar_if_changed()

    def restore(self) -> list[dict[str, Any]]:
        """The cleared row's Bring back: the frames the Clear took away are back where they were."""
        if self._clear_until is None:
            return []
        for frame in self._frames.values():
            frame.cleared = False
        self._clear_until = None
        return [{"type": "cleared", "frames": []}] + self._bar_if_changed()

    def next_due(self) -> float | None:
        """When the next closed frame or the cleared ones leave (None: nothing waits to leave)."""
        times = [frame.leave_at for frame in self._frames.values() if frame.closed and frame.leave_at is not None]
        if self._clear_until is not None:
            times.append(self._clear_until)
        return min(times) if times else None

    def due(self, now: float) -> list[dict[str, Any]]:
        """See the module doc."""
        out: list[dict[str, Any]] = []
        for frame_id in self.frame_ids():
            frame = self._frames.get(frame_id)
            if frame is not None and frame.closed and frame.leave_at is not None and frame.leave_at <= now:
                out += self._leave([frame_id])
        if self._clear_until is not None and self._clear_until <= now:
            out += self._finish_clear()
        return out + self._bar_if_changed()

    def _finish_clear(self) -> list[dict[str, Any]]:
        """The frames a Clear took away leave for good (no Bring back any more), then the row goes."""
        self._clear_until = None
        out = self._leave(self.cleared())
        return out + [{"type": "cleared", "frames": []}]

    def _leave(self, frame_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Frames leave the board: ``remove`` each, or ``remove_group`` for a group that is empty now (its answer then
        takes no late frame); the newest group left is no longer older."""
        out: list[dict[str, Any]] = []
        emptied: list[_Group] = []
        for frame_id in list(frame_ids):
            frame = self._frames.pop(frame_id, None)
            if frame is None:
                continue
            self._ids = {key: value for key, value in self._ids.items() if value != frame_id}
            group = self._groups.get(frame.group)
            if group is None:
                continue
            if frame_id in group.frames:
                group.frames.remove(frame_id)
            if frame_id in group.page_order:
                group.page_order.remove(frame_id)
            if group.frames:
                out.append({"type": "remove", "id": frame_id})
            elif group not in emptied:
                emptied.append(group)
        for group in emptied:
            self._dismissed.add(group.answer)
            out += self._drop_group(group)
        if emptied:
            out += self._refresh_older()
        return out

    def clear(self) -> None:
        """Nothing on the board any more (the app's reset: the page is told with a clear message)."""
        self._frames.clear()
        self._groups.clear()
        self._by_answer.clear()
        self._ids.clear()
        self._clear_until = None
        self._bar_sent = ""

    def resend(self) -> list[dict[str, Any]]:
        """Every message that puts the board as it is now on a page that has nothing (a renderer that restarted): the
        groups oldest first (each new one goes on top), each one's frames and pictures, then the order, the frames a
        Clear took away and the bar."""
        out: list[dict[str, Any]] = []
        for group in sorted(self._groups.values(), key=lambda group: group.seq):
            group.sent = ""
            group.page_order = []
            out += self._group_message(group)
            for frame_id in group.frames:
                frame = self._frames[frame_id]
                shown = self._shown(frame)
                frame.sent = _same(shown)
                out.append(shown)
                group.page_order.append(frame_id)
                picture = frame.shown()[1]
                if picture is not None:
                    message = dict(picture)
                    message["id"] = frame_id
                    frame.sent_picture = _same(message)
                    out.append(message)
            out += self._group_message(group)
        if self._clear_until is not None:
            out.append({"type": "cleared", "frames": self.cleared()})
        out += self._bar_if_changed(force=True)
        return out


class ReadingWatch:
    """See the module doc. ``note`` takes the page's own report (its reading request): every report is a sign of
    reading at that moment (the pointer leaving or the focus going too: quiet counts from then)."""

    def __init__(self, quiet_s: float = QUIET_S) -> None:
        self.quiet_s = max(0.0, float(quiet_s))
        self.pointer = False
        self.focus = False
        self.last_touch = -math.inf

    def note(self, pointer: bool, focus: bool, now: float) -> None:
        self.pointer, self.focus = bool(pointer), bool(focus)
        self.last_touch = float(now)

    def reset(self) -> None:
        """Nobody is at the board (it is out of sight)."""
        self.pointer = self.focus = False
        self.last_touch = -math.inf

    def reading(self, now: float) -> bool:
        return self.pointer or self.focus or float(now) - self.last_touch < self.quiet_s

    def quiet_at(self, now: float) -> float | None:
        """When reading stops if nothing more happens (now, if it has); None while the pointer or the focus is there."""
        if self.pointer or self.focus:
            return None
        return max(float(now), self.last_touch + self.quiet_s)


def spoken_rank(say: str, words: Iterable[str]) -> float:
    """Where Jarvis first speaks of a frame in ``say`` (its index, as a share of the line's length), from the frame's
    ``words`` (a kind's own words, a sender's name ...); 1.0 + when it never does (they keep their arrival order)."""
    line = " ".join(str(say or "").split()).casefold()
    if not line:
        return 1.0
    best = len(line) + 1
    for word in words:
        needle = " ".join(str(word or "").split()).casefold()
        if len(needle) < 3:
            continue
        at = line.find(needle)
        if at >= 0:
            best = min(best, at)
    return best / (len(line) + 1) if best <= len(line) else 1.0


__all__ = ["BoardFeed", "CLEAR_S", "CLOSE_S", "QUIET_S", "ReadingWatch", "spoken_rank", "substance"]
