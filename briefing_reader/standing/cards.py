"""A standing job's cards (no Qt): the lines Jarvis writes himself, their stable ids, and how they are kept.

    todo_line / calendar_line / reply_line     a card's key=value (or Calendar) line built by Jarvis: every value
                                 through ask.context.data() (one line; "|" can't open a field, "<" ">" can't open a
                                 block), a body escaped as the format says ("\\n", "\\\\"); never a model's link
    rebuild(card, *, gmail_link)  a validated Todo / Calendar / Reply card's line written again by Jarvis: what an
                                 agent's model wrote is kept field by field, its link dropped (a reply gets Jarvis's
                                 own thread link)
    stable_id(job_id, kind, key)  a Todo's or Calendar's id from the job and its source (the mail, or the job's own
                                 key for one from no mail), so a reworded title is never a new card; a Reply keeps
                                 the normal id (account and message), the same as an Ask's reply to that message
    record(card, *, job, source, recipients) / card_from(record, ...)
                                 how a card is kept in its run file and read back by the app: the line is parsed
                                 again, its kind checked, its id rebuilt, EVERY recipient unverified (a NEW
                                 RECIPIENT tick unless the owner sent to it before), its link Jarvis's own

Nothing here is logged; a record holds a card's line, which may hold a draft and a sender's address.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Any

from ..actions import (CALENDAR, MODE_STANDING_AGENT, MODE_STANDING_RULE, REPLY, SOURCE_STANDING, STANDING_MODES,
                       TODO, ProposedAction, parse_action_line, with_error)
from ..ask.context import data

KINDS = (TODO, CALENDAR, REPLY)
BODY_CAP = 5000
TITLE_CAP = 200
SUBJECT_CAP = 250
_ID_RE = re.compile(r"[0-9a-f]{16}")


def _body(text: str) -> str:
    """A body as the key=value format reads it: "\\" doubled, a line break as "\\n", nothing invisible."""
    cleaned = "\n".join(data(line) for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()[:BODY_CAP]
    return cleaned.replace("\\", "\\\\").replace("\n", "\\n")


def _due(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return data(str(value or ""), 16)


def todo_line(title: str, due: date | datetime | str, *, account: str = "", link: str = "") -> str:
    return (f"Todo: title={data(title, TITLE_CAP)} | due={_due(due)} | block= | acct={data(account, 24)} | "
            f"link={data(link, 2000)}")


def calendar_line(title: str, start: datetime | date, end: datetime | date | None = None, *, where: str = "",
                  notes: str = "") -> str:
    if isinstance(start, datetime):
        when = start.strftime("%Y-%m-%d %H:%M")
        if isinstance(end, datetime):
            when += f"-{end.strftime('%H:%M')}"
    else:
        when = start.isoformat()
        if isinstance(end, date) and not isinstance(end, datetime) and end > start:
            when += f" to {end.isoformat()}"
    return f"Calendar: {data(title, TITLE_CAP)} | {when} | | {data(where, 300)} | {data(notes, 500)}"


def reply_line(account: str, thread: str, msgid: str, gmid: str, to: Sequence[str], subject: str, body: str,
               link: str = "") -> str:
    subject = data(subject, SUBJECT_CAP)
    if not subject.casefold().startswith("re:"):
        subject = f"Re: {subject}" if subject else "Re:"
    msgid = str(msgid or "").strip().strip("<>")     # data() would make its angle brackets look-alikes
    return (f"Reply: acct={data(account, 24)} | thread={data(thread, 64)} | msgid={data(msgid, 500)} | "
            f"gmid={data(gmid, 64)} | to={', '.join(data(address, 254) for address in to)} | cc= | "
            f"subject={subject} | replied=no | due= | link={data(link, 2000)} | body={_body(body)}")


def rebuild(card: ProposedAction, *, gmail_link: str = "") -> str:
    """A validated Todo, Calendar or Reply card's line, written again by Jarvis from its fields; its link dropped
    (``gmail_link``: Jarvis's own, for a reply or a to-do from mail). ValueError for another kind."""
    if card.kind == TODO:
        return todo_line(card.title, card.field("due"), account=card.account, link=gmail_link)
    if card.kind == CALENDAR:
        if card.start is not None:
            return calendar_line(card.title, card.start, card.end, where=card.where, notes=card.notes)
        if card.all_day_start is not None:
            return calendar_line(card.title, card.all_day_start, card.all_day_end, where=card.where, notes=card.notes)
        raise ValueError("a calendar card without a time")
    if card.kind == REPLY:
        return reply_line(card.account, card.field("thread"), card.field("msgid"), card.field("gmid"),
                          card.mail_recipients(), card.field("subject"), card.body, gmail_link)
    raise ValueError("a standing job proposes only to-dos, events and replies")


def stable_id(job_id: str, kind: str, key: str) -> str:
    return hashlib.sha1(f"standing\x1f{job_id}\x1f{kind}\x1f{key}".encode("utf-8")).hexdigest()[:16]


def with_job(card: ProposedAction, *, job_kind: str, job_id: str, key: str) -> ProposedAction:
    """``card`` as a standing job's: its source and mode set, a Todo's or Calendar's id made stable."""
    mode = MODE_STANDING_RULE if job_kind == "rule" else MODE_STANDING_AGENT
    card = replace(card, source=SOURCE_STANDING, mode=mode)
    if card.kind in (TODO, CALENDAR) and not card.error:
        card = replace(card, id=stable_id(job_id, card.kind, key))
    return card


def record(card: ProposedAction, *, line: str, job: Mapping[str, str], key: str,
           source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What a run file keeps of a card: its id, kind, mode, the line Jarvis wrote, the job, the key its id comes
    from, and the source mail's facts (ids, sender, subject) for the receipt and the Reply's click-time check."""
    return {"id": card.id, "kind": card.kind, "mode": card.mode, "line": line, "job": dict(job), "key": key,
            "source": dict(source or {})}


def card_from(item: Any, *, aliases: Sequence[str], link_hosts: Sequence[str] = ()) -> ProposedAction | None:
    """A kept card read back by the app: its line parsed again (an unreadable or changed one is dropped: None), its
    kind one a standing job may propose, its account one Jarvis has, its id rebuilt as the runner made it, every
    recipient unverified and its link Jarvis's own (a reply's thread link, kept in the record's source)."""
    if not isinstance(item, Mapping):
        return None
    line, job, key = item.get("line"), item.get("job"), item.get("key")
    mode = item.get("mode")
    if not isinstance(line, str) or not isinstance(job, Mapping) or not isinstance(key, str) or mode not in STANDING_MODES:
        return None
    job_id, job_kind = job.get("id"), job.get("kind")
    if not isinstance(job_id, str) or job_kind not in ("agent", "rule"):
        return None
    card = parse_action_line(line, link_hosts=link_hosts)
    if card.error or card.kind not in KINDS:
        return None
    if card.kind in (REPLY,) or (card.kind == TODO and card.account):
        if card.account not in aliases:
            return None
    card = with_job(card, job_kind=job_kind, job_id=job_id, key=key)
    if card.id != item.get("id") or not _ID_RE.fullmatch(card.id or ""):
        return None
    if card.kind == REPLY:
        card = replace(card, unverified=frozenset(address.casefold() for address in card.mail_recipients()))
    return card


def refused(card: ProposedAction, reason: str) -> ProposedAction:
    return with_error(card, reason)
