"""The canvas's resting frames (PLAN-canvas part 3, "the Hedwig mix"): the "waits for your OK" frame and the Agents
frame, built only from what the app already holds - the cards the Approvals screen shows and the standing agents the
Agents screen shows. Qt-free and pure: no file, no log, no network, no clock, no model; nothing here raises for odd data
(only a frame id that is not one raises ValueError: the caller's mistake).

    approval_rows(cards, waiting) -> list[tuple[str, str]]
        the cards that wait on Approvals (hud.ActionCard: actionable, its status one of ``waiting`` - approvals_view's
        WAITING), each as the Approvals screen's queue words it: its kind label and its title, in the screen's order
    from_approvals(count, rows, *, frame_id) -> Built | None
        the "waits for your OK" frame (kind "approvals"): how many cards wait (``count``: the corner's Approvals number,
        the desktop HUD's waiting cards with them) and the first APPROVALS_ROWS of ``rows``; amber on the page, whose
        one button opens Approvals. None while nothing waits: the frame is there only while something does
    from_agents(lines, *, frame_id) -> Built | None
        the Agents frame (kind "agents"): one row per AgentLine (standing_ui.StandingController.agent_lines: the data
        the Agents screen already shows), at most ROWS_CAP (the rest counted in "more"), the meta "2 on", and how many
        of their proposals wait in all. None while the owner has no agent

Every string from outside goes through board.frames.clean (secrets redacted, control and bidi characters shown as
"<U+202E>") and is cut to its cap as the page counts it. Nothing here approves, sends, runs or undoes anything: the
frames only say what waits and what ran; their buttons open the screens that do.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .. import pictures
from . import protocol
from .frames import Built, _check_id, _frame, clean

APPROVALS_TITLE = "Waits for your OK"
AGENTS_TITLE = "Agents"
APPROVALS_ROWS = 4                       # the first few cards that wait (the rest is the page's "+N more")
APPROVALS_META = "{count} waiting"
AGENTS_META = "{count} on"
NONE_ON = "None on"
CARD_KIND = "Card"                       # a card whose kind label is empty


@dataclass(frozen=True)
class AgentLine:
    """One standing agent as its frame shows it (the Agents screen's own data; every word the app's or the owner's
    own, shown as text). repr: its state and counts only."""

    name: str
    state: str = "on"            # protocol.AGENT_STATES: on, paused, off (not on yet)
    last: str = ""               # when it last ran, in the screen's words ("9:41 AM", "Yesterday 9:41 PM"); "" never
    came: int = -1               # what came of its last run: its proposals; -1 not known (or it did not finish)
    next: str = ""               # when it runs next ("7:30 AM", "Tomorrow 7:30 AM"); "" not planned
    waits: int = 0               # its proposals that wait on Approvals

    def __repr__(self) -> str:
        return f"AgentLine({self.state}, came={self.came}, waits={self.waits})"


def _count(value: Any, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(high, number))


def approval_rows(cards: Iterable[Any], waiting: Collection[str]) -> list[tuple[str, str]]:
    """The waiting cards' kind labels and titles, as the Approvals screen's queue tiles show them."""
    rows: list[tuple[str, str]] = []
    for card in cards or ():
        try:
            if not getattr(card, "actionable", False) or card.status() not in waiting:
                continue
            kind = card.kind_label.full_text()
            title = card.title()
        except Exception:  # noqa: BLE001 - a card going away: left out
            continue
        rows.append((str(kind or ""), str(title or "")))
    return rows


def from_approvals(count: Any, rows: Sequence[tuple[Any, Any]], *, frame_id: str) -> Built | None:
    """The "waits for your OK" frame (see the module doc)."""
    frame_id = _check_id(frame_id)
    shown = [(clean(kind, protocol.META_CAP) or CARD_KIND, clean(title, protocol.TITLE_CAP) or pictures.NO_TITLE)
             for kind, title in list(rows or ())[:APPROVALS_ROWS]]
    total = max(_count(count, protocol.APPROVALS_MAX), len(shown))
    if total <= 0:
        return None
    body = {"count": total, "rows": [{"kind": kind, "title": title} for kind, title in shown]}
    return Built((_frame(frame_id, "approvals", APPROVALS_TITLE, meta=APPROVALS_META.format(count=total), body=body),))


def from_agents(lines: Iterable[AgentLine], *, frame_id: str) -> Built | None:
    """The Agents frame (see the module doc)."""
    frame_id = _check_id(frame_id)
    rows: list[dict[str, Any]] = []
    total = on = waits = 0
    for line in lines or ():
        if not isinstance(line, AgentLine):
            continue
        total += 1
        state = line.state if line.state in protocol.AGENT_STATES else "off"
        on += state == "on"
        own = _count(line.waits, protocol.RESTING_COUNT_MAX)
        waits += own
        if len(rows) >= protocol.ROWS_CAP:
            continue
        came = -1 if not isinstance(line.came, int) or isinstance(line.came, bool) or line.came < 0 \
            else min(protocol.RESTING_COUNT_MAX, line.came)
        rows.append({"name": clean(line.name, protocol.TITLE_CAP) or pictures.NO_TITLE, "state": state,
                     "last": clean(line.last, protocol.WHEN_CAP), "came": came if line.last else -1,
                     "next": clean(line.next, protocol.WHEN_CAP), "waits": own})
    if not rows:
        return None
    body = {"rows": rows, "more": min(protocol.RESTING_COUNT_MAX, total - len(rows)),
            "waits": min(protocol.RESTING_COUNT_MAX, waits)}
    meta = AGENTS_META.format(count=on) if on else NONE_ON
    return Built((_frame(frame_id, "agents", AGENTS_TITLE, meta=meta, body=body),))


__all__ = ["AGENTS_TITLE", "APPROVALS_ROWS", "APPROVALS_TITLE", "AgentLine", "approval_rows", "from_agents",
           "from_approvals"]
