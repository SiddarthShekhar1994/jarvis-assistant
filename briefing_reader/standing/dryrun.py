"""A rule's dry run (no Qt): what it would have done over the last weeks of mail, with nothing done.

    WINDOW_DAYS = 30            the dry run looks this far back (headers only, free: Gmail's own estimate)
    BROAD = 300                 a filter that matches more than this in the window would read everything: refused,
                                with the count, before any Claude run
    NARROW_FOR_TIDY = 90        a tidy-up runs on its own only for a filter this narrow (about 3 mails a day)
    estimate(rule, reader, *, now, batch) -> Preview
        the matches' count and the newest ``batch`` of them (headers); Preview.refusal says why it is too broad
    judge(rule, preview, reader, check, *, now, chars, own) -> DryResult
        the newest matches read (one message each, scrubbed) and judged by ``check`` (one run, counted like any
        other); every row says what a live rule would do with it: the card it would line up, a line in its report,
        and the tidy-up it would make or why not. Nothing is proposed, starred, labelled or archived.
    summary(result, now) -> model.DryRun    what the rule keeps of it (counts)
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .mailwatch import MailRef, read_message, recent
from .model import ACTION_TELL, DryRun, Rule, TIDY_NONE
from .rules import Checked, Item, Outcome, outcomes

WINDOW_DAYS = 30
BROAD = 300
NARROW_FOR_TIDY = 90


@dataclass(frozen=True)
class Preview:
    matched: int
    refs: tuple[MailRef, ...] = ()
    refusal: str = ""

    def __repr__(self) -> str:
        return f"Preview({self.matched} matched, {len(self.refs)} shown, refused {bool(self.refusal)})"


@dataclass(frozen=True)
class DryResult:
    matched: int
    rows: tuple[Outcome, ...] = ()
    unread: int = 0                     # matches that could not be read again
    batch_held: str = ""
    narrow: bool = False                # narrow enough for a tidy-up on its own
    failure: str = ""                   # the check's failure kind ("" = it ran)
    items: tuple[Item, ...] = field(default=(), repr=False)

    @property
    def yes(self) -> int:
        return sum(1 for row in self.rows if row.yes)


def estimate(rule: Rule, reader: Any, *, now: datetime, batch: int) -> Preview:
    refs, matched = recent(reader, rule.query, WINDOW_DAYS, now, max_refs=max(1, batch))
    if matched > BROAD:
        return Preview(matched, tuple(refs), refusal=(
            f"This rule's filter matches about {matched} emails in {WINDOW_DAYS} days: it would read nearly "
            "everything. Name a sender or a subject to narrow it."))
    return Preview(matched, tuple(refs))


def judge(rule: Rule, preview: Preview, reader: Any, check: Callable[[Rule, Sequence[Item]], Any], *, now: datetime,
          chars: int, own: Collection[str] = ()) -> DryResult:
    if preview.refusal:
        return DryResult(preview.matched)
    items: list[Item] = []
    unread = 0
    for ref in preview.refs:
        message, _left_out = read_message(reader, ref, chars=chars)
        if message is None:
            unread += 1
            continue
        items.append(Item(ref, message))
    narrow = preview.matched <= NARROW_FOR_TIDY
    if not items:
        return DryResult(preview.matched, unread=unread, narrow=narrow)
    answer = check(rule, items)
    if not hasattr(answer, "results"):
        return DryResult(preview.matched, unread=unread, narrow=narrow,
                         failure=str(getattr(answer, "kind", "error")))
    checked: Checked = outcomes(rule, items, answer, now=now, own=own)
    return DryResult(preview.matched, checked.outcomes, unread, checked.batch_held, narrow, items=tuple(items))


def would(rule: Rule, row: Outcome) -> str:
    """What a live rule would do with one matched mail (Jarvis's own words; shown on the dry run's row)."""
    if not row.yes:
        return "No - nothing happens"
    if row.card is not None:
        made = {"todo": "a to-do", "event": "an event", "reply": "a reply draft"}.get(rule.action.kind, "a card")
        done = f"Yes - lines up {made}: {row.card.title}" if row.card.title else f"Yes - lines up {made}"
    elif rule.action.kind == ACTION_TELL or row.tell:
        done = "Yes - a line in the rule's report" + (" (no card: a value was missing)" if row.refused else "")
    else:
        done = "Yes"
    if rule.tidy.kind != TIDY_NONE:
        done += f"; {rule.tidy.kind}s it" if not row.tidy_held else f"; would not {rule.tidy.kind} it ({row.tidy_held})"
    return done


def summary(result: DryResult, now: datetime) -> DryRun:
    return DryRun(at=now.isoformat(timespec="seconds"), matched=min(result.matched, 1_000_000),
                  window_days=WINDOW_DAYS, judged=len(result.rows), yes=result.yes)
