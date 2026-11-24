"""Add all: one click (and one more confirm) adds a batch of own-calendar cards (mail extras, part 1).

Only cards that add an event to the personal calendar take part: a Calendar event and a Todo's block
(own calendar, no guests, find-existing first, 10-minute Undo each, exactly as on their own click).
RSVP / Move / Cancel tell people, a Reply / Email sends, an Unsubscribe tells a third party and can't
be undone, hand-offs only record Done, a day plan's block keeps its one click per block, and a card
with an amber note waits for its own look: none of them ever goes in Add all.

    eligible(actions, *, decided, jobs, signed_in, confirmed, now, noted=(), tried=())
        the cards Add all would add now, in card order
    rows(actions, today)      what the confirm dialog lists for each (title, detail): what it adds
    digest(rows)              one short hash of exactly that list (the confirm re-checks it)
    stop_reason(status)       "" when a step's result lets the batch go on, else its reason word
    log_end(added, total, reason)   the one log line of a batch (counts and a reason word only)

Qt-free and pure: nothing here adds anything (the app steps through its own per-card path). Logs hold
counts and reason words only, never a title, a time or an id list.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from .actions import (CALENDAR, MODE_PLAN, STATUS_CREATED, STATUS_EXISTS, STATUS_FAILED, STATUS_UNKNOWN, TODO,
                      ProposedAction, card_view)

logger = logging.getLogger(__name__)

MIN_BATCH = 2                    # the bar shows only while at least this many cards could go together
STEP_GAP_MS = 300                # the pause between one card's result and the next card's start

# Why a batch stopped (the activity row's and the log's reason word).
REASON_DONE = ""                 # every card went
REASON_FAILED = "failed"
REASON_UNKNOWN = "unknown"
REASON_SIGN_IN = "sign-in"
REASON_STOPPED = "stopped"       # the owner's Stop
REASON_QUIT = "quit"
REASON_HOLD = "hold"             # a Send's / Cancel's hidden hold started meanwhile
REASON_CHANGED = "changed"       # the next card no longer goes in Add all (decided, edited, noted ...)
REASON_NOT_READY = "not ready"   # the calendar or its account can't add it now
REASONS = (REASON_FAILED, REASON_UNKNOWN, REASON_SIGN_IN, REASON_STOPPED, REASON_QUIT, REASON_HOLD,
           REASON_CHANGED, REASON_NOT_READY)

_GOES_ON = frozenset({STATUS_CREATED, STATUS_EXISTS})


@dataclass(frozen=True)
class Row:
    """One card as the confirm dialog lists it: what it adds, in the card's own words."""

    action_id: str
    kind: str        # "Event" or "Block"
    title: str
    detail: str      # "Tue Oct 6 · 5:00-6:00 PM · Student union, room 2 · Personal calendar"


def adds_own_event(action: ProposedAction) -> bool:
    """A Calendar event or a Todo's block (not a day plan's block): it only adds an event to the
    personal calendar, which tells nobody and which Undo deletes."""
    if not action.actionable or action.mode == MODE_PLAN:
        return False
    if action.kind == CALENDAR:
        return True
    return action.kind == TODO and action.block_event() is not None


def _passed(action: ProposedAction, now: datetime) -> bool:
    """The event (or block) starts before ``now`` (a naive wall time in the calendar's zone)."""
    event = action.block_event() if action.kind == TODO else action
    if event is None:
        return True
    if event.start is not None:
        return event.start.replace(tzinfo=None) < now.replace(tzinfo=None)
    if event.all_day_start is not None:
        last = event.all_day_end or event.all_day_start
        return last < now.date()
    return True


def eligible(actions: Sequence[ProposedAction], *, decided: Collection[str], jobs: Collection[str],
             signed_in: bool, confirmed: bool, now: datetime, noted: Collection[str] = (),
             tried: Collection[str] = ()) -> list[ProposedAction]:
    """The cards Add all would add now, in card order.

    Each is actionable and decidable, not decided (``decided``), not running (``jobs``), a Calendar
    event or a Todo with a block, not a day plan's block, with no amber note (its own ``warnings``,
    or an id in ``noted``: a note the app shows on its card now), not tried before (``tried``: a
    failed or unknown result waits for its own Retry), and not in the past (``now``). Nothing goes
    while the personal account is not signed in or its Google account not confirmed.
    """
    if not signed_in or not confirmed:
        return []
    out: list[ProposedAction] = []
    seen: set[str] = set()
    for action in actions:
        if action.id in seen:
            continue
        seen.add(action.id)
        if not action.decidable or not adds_own_event(action):
            continue
        if action.id in decided or action.id in jobs or action.id in noted or action.id in tried:
            continue
        if action.warnings or _passed(action, now):
            continue
        out.append(action)
    return out


def rows(actions: Sequence[ProposedAction], today: date) -> tuple[Row, ...]:
    """What each card adds, as its card says it (card_view): a Calendar event's when, where and
    calendar; a Todo's block time."""
    out: list[Row] = []
    for action in actions:
        if action.kind == TODO:
            block = action.block_event()
            when = block.describe(today) if block is not None else ""
            detail = " · ".join(part for part in (when, "Personal calendar") if part)
            out.append(Row(action.id, "Block", action.title, detail))
        else:
            view = card_view(action, today)
            out.append(Row(action.id, "Event", view.title, view.detail))
    return tuple(out)


def digest(listed: Sequence[Row]) -> str:
    """A short hash of exactly these rows (ids, kinds, titles and details, in order)."""
    hasher = hashlib.sha256()
    for row in listed:
        for part in (row.action_id, row.kind, row.title, row.detail):
            hasher.update(part.encode("utf-8", "surrogatepass"))
            hasher.update(b"\x1f")
        hasher.update(b"\x1e")
    return hasher.hexdigest()[:24]


def stop_reason(status: str) -> str:
    """"" when a step's result lets the batch go on (added, or already on the calendar), else why it
    stops ("failed", "unknown")."""
    if status in _GOES_ON:
        return REASON_DONE
    if status == STATUS_UNKNOWN:
        return REASON_UNKNOWN
    if status == STATUS_FAILED:
        return REASON_FAILED
    return REASON_FAILED


def summary(added: int, total: int, reason: str) -> str:
    """The activity row: "Added 3 of 5" or "Added 3 of 5; stopped: failed"."""
    text = f"Added {added} of {total}"
    return f"{text}; stopped: {reason}" if reason else text


def log_end(added: int, total: int, reason: str) -> None:
    logger.info("Add all ended: %d of %d added; %s", added, total, reason or "all done")
