"""What the board holds (the new look, part 5a; DESIGN-SPEC 3.1 and 3.5): one group per answer and the frames in it,
kept in Python, and the messages that put them on the page (BoardView.apply). Qt-free and pure: no file, no log, no
clock (the caller says when); nothing here raises for odd data.

    BoardFeed(max_frames=protocol.MAX_FRAMES, max_groups=protocol.MAX_GROUPS)
        frame_id(answer, source, merge="") -> str
            the frame id a source's frame takes, made before its builder runs (board.frames builders take it): the
            same id again for the same source of the same answer (an update in place: a page picture that comes ready),
            and the id of the frame already there when ``merge`` names one of the answer's frames (a mail thread and
            the reply to it that waits on Approvals: "account:thread id" for both)
        put(answer, source, messages, *, when, topic="", merge="", approvals="", rank=0.0) -> list[dict]
            a builder's messages (board.frames.Built.messages: the frame, then its picture) for ``answer``'s group,
            and returns what to send, in order: the room made (whole groups leaving), the group (new: on top of the
            board, its header ``when`` - "9:41 AM" - and ``topic``, else the first frame's title, at most 40
            characters; the group before it then older), the frame with its group, its picture, the group's new order
            when it changed, and the bar's counts when they changed. A frame and the reply that waits on it are ONE
            frame: the mail as read, with the reply's amber chip ("Reply waits on Approvals"), whose go_approvals opens
            the reply's card (``approvals``: that card's action id). In a group a frame that waits on the owner comes
            first, then the frames in the order Jarvis speaks of them (``rank``, then their arrival)
        set_compact(compact) -> list[dict]         the bar without its frames count (the folded Dock's column)
        set_turn(answer, entry_id)                 the conversation turn that put the answer's frames up
        turn_frames(entry_id) -> (group id, count) its group and how many frames it has on the board now ("", 0: none)
        approvals_for(frame_id) -> str             the card a frame's go_approvals opens ("" none)
        frames(), waits(), pinned(), groups(), group_of(frame_id), group_for(answer), frame_ids(), counts()
        clear()                                    nothing on the board any more (the app's own reset)

Limits (DESIGN-SPEC 3.5): ``max_frames`` frames and ``max_groups`` groups; past them the oldest group with no pinned
frame leaves whole (never the answer being added to); when none can leave, the new frame is refused (counted). Frames
last for the session only: nothing here is ever saved.
"""

from __future__ import annotations

import json
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import protocol

_ELLIPSIS = "…"


def _cut(text: str, cap: int) -> str:
    """``text`` within ``cap`` UTF-16 code units, ended with "…" when cut (at a space near the end when there is one)."""
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
    return head.rstrip() + _ELLIPSIS


def _same(message: dict[str, Any]) -> str:
    return json.dumps(message, sort_keys=True, ensure_ascii=True)


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
    sent: str = ""                 # the frame message the page has (unchanged ones are not sent again)
    sent_picture: str = ""

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
    sent: str = ""
    turn: int = 0


class BoardFeed:
    """See the module doc."""

    def __init__(self, *, max_frames: int = protocol.MAX_FRAMES, max_groups: int = protocol.MAX_GROUPS) -> None:
        self.max_frames = max(1, min(int(max_frames), protocol.MAX_FRAMES))
        self.max_groups = max(1, min(int(max_groups), protocol.MAX_GROUPS))
        self._frames: dict[str, _Frame] = {}
        self._groups: dict[str, _Group] = {}          # in creation order
        self._by_answer: dict[Hashable, str] = {}     # answer -> its group id
        self._ids: dict[tuple[Hashable, str], str] = {}   # (answer, source) / (answer, "merge:" + key) -> frame id
        self._compact = False
        self._bar_sent = ""
        self._seq = 0
        self._next_frame = 0
        self._next_group = 0
        self._counts = {"frames": 0, "groups": 0, "left": 0, "refused": 0, "merged": 0}

    # ---- ids ---------------------------------------------------------------------------------------------------------

    def frame_id(self, answer: Hashable, source: str, merge: str = "") -> str:
        """See the module doc."""
        found = self._ids.get((answer, source))
        if found is None and merge:
            found = self._ids.get((answer, "merge:" + merge))
        if found is None:
            self._next_frame += 1
            found = f"f{self._next_frame}"
        self._ids[(answer, source)] = found
        if merge:
            self._ids.setdefault((answer, "merge:" + merge), found)
        return found

    # ---- reading ---------------------------------------------------------------------------------------------------

    def frames(self) -> int:
        return len(self._frames)

    def waits(self) -> int:
        return sum(1 for frame in self._frames.values() if frame.waits())

    def pinned(self) -> int:
        return sum(1 for frame in self._frames.values() if frame.pinned)

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

    def frame_message(self, frame_id: str) -> dict[str, Any] | None:
        frame = self._frames.get(frame_id)
        return frame.shown()[0] if frame is not None else None

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
                return group.id, len(group.frames)
        return "", 0

    def counts(self) -> dict[str, int]:
        """Counts only (for the log): frames and groups made, groups that left, frames refused, frames merged."""
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
            topic: str = "", merge: str = "", approvals: str = "", rank: float = 0.0) -> list[dict[str, Any]]:
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
        if frame is None:
            out += self._make_room(answer, new_group=group is None)
            if len(self._frames) >= self.max_frames or (group is None and len(self._groups) >= self.max_groups):
                self._counts["refused"] += 1
                return out
            if group is None:
                group, made = self._new_group(answer, when, topic or str(frame_message.get("title", "")))
                out += made
            self._seq += 1
            frame = _Frame(frame_id, group.id, merge, self._seq, float(rank))
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
        shown, shown_picture, _card = frame.shown()
        shown["id"] = frame_id
        shown["group"] = group.id
        text = _same(shown)
        if text != frame.sent:
            frame.sent = text
            out.append(shown)
            if group.id and frame_id not in group.page_order:
                group.page_order.append(frame_id)   # the page puts a new frame at its group's end
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

    def _new_group(self, answer: Hashable, when: str, topic: str) -> tuple[_Group, list[dict[str, Any]]]:
        out: list[dict[str, Any]] = []
        for other in self._groups.values():          # the one before it goes quiet (INK_4)
            if not other.older:
                other.older = True
                out += self._group_message(other)
        self._next_group += 1
        self._seq += 1
        group = _Group(f"g{self._next_group}", answer, _cut(" ".join(str(when or "").split()), protocol.TIME_CAP),
                       _cut(" ".join(str(topic or "").split()), protocol.TOPIC_CAP), self._seq)
        self._groups[group.id] = group
        self._by_answer[answer] = group.id
        self._counts["groups"] += 1
        out += self._group_message(group)
        return group, out

    def _group_message(self, group: _Group) -> list[dict[str, Any]]:
        """The group as the page should have it: its header and the order of its frames the page has."""
        message = {"type": "group", "id": group.id, "time": group.time, "topic": group.topic,
                   "frames": list(group.page_order), "older": group.older}
        group.sent = _same(message)
        return [message]

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
        new group) fits."""
        out: list[dict[str, Any]] = []
        while len(self._frames) + 1 > self.max_frames or (new_group and len(self._groups) + 1 > self.max_groups):
            victim = next((group for group in sorted(self._groups.values(), key=lambda group: group.seq)
                           if group.answer != answer and not any(self._frames[frame_id].pinned
                                                                 for frame_id in group.frames)), None)
            if victim is None:
                break
            out += self._drop_group(victim)
        return out

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

    def set_pinned(self, frame_id: str, on: bool) -> None:
        """A frame pinned or not (part 5b's P): a group with a pinned frame never leaves for room."""
        frame = self._frames.get(frame_id)
        if frame is not None:
            frame.pinned = bool(on)

    def clear(self) -> None:
        """Nothing on the board any more (the app's reset: the page is told with a clear message)."""
        self._frames.clear()
        self._groups.clear()
        self._by_answer.clear()
        self._ids.clear()
        self._bar_sent = ""

    def resend(self) -> list[dict[str, Any]]:
        """Every message that puts the board as it is now on a page that has nothing (a renderer that restarted): the
        groups oldest first (each new one goes on top), each one's frames and pictures, then the order and the bar."""
        out: list[dict[str, Any]] = []
        for group in sorted(self._groups.values(), key=lambda group: group.seq):
            group.sent = ""
            group.page_order = []
            out += self._group_message(group)
            for frame_id in group.frames:
                frame = self._frames[frame_id]
                shown, picture, _card = frame.shown()
                shown["id"] = frame_id
                shown["group"] = group.id
                frame.sent = _same(shown)
                out.append(shown)
                group.page_order.append(frame_id)
                if picture is not None:
                    message = dict(picture)
                    message["id"] = frame_id
                    frame.sent_picture = _same(message)
                    out.append(message)
            out += self._group_message(group)
        out += self._bar_if_changed(force=True)
        return out


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


__all__ = ["BoardFeed", "spoken_rank"]
