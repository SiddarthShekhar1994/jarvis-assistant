"""New mail for standing jobs (no Qt): what a filter matched since the last check, headers first, then the one
matched message (never its whole conversation).

    MailRef         one matched message as its headers tell (gmail.WATCH_HEADERS): account, message and thread
                    ids, when Gmail got it, the sender, To / Cc / Reply-To, subject, Message-ID, labels, whether it
                    passed Gmail's DMARC check, and whether it is list or automatic mail. Never the body.
    Batch           a check's answer: the new refs (oldest first), how many more wait, the job's next mark, and
                    whether this was the job's first sight (BASELINE: the mark is set, nothing is new)
    anything_new(reader, after_ms) -> bool       one cheap probe per account ("any mail at all since T?")
    new_mail(reader, query, mark, now, *, max_new) -> Batch
                    the messages of ``query`` (a checked filter) newer than the job's mark: Gmail's after: with
                    OVERLAP_S of overlap, then each message's internalDate and the seen ids decide; at most
                    ``max_new`` (the rest wait for the next check: the mark moves only past what was taken)
    recent(reader, query, days, *, max_refs) -> (refs, estimate)   a dry run's matches over the last ``days``
    read_message(reader, ref, *, chars) -> (MailMessage | None, left_out)
                    the one matched message's text: quoted history and HTML stripped (ask.mail), what plainly is a key
                    or a password taken out (desktop.guard, as the agent's mail tools do), cut to ``chars``

Logs carry counts only; a ref's repr names its ids' lengths, never a sender or a subject.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from ..ask.mail import CUT_MARK, MailMessage, MailPerson, thread_from_api
from ..desktop import guard
from ..desktop import service as desktop_service
from ..unsubscribe import dmarc_passed   # Gmail's verdict (it lives there: unsubscribing reads it too)
from .filters import watch_query

logger = logging.getLogger(__name__)

OVERLAP_S = 120                  # Gmail's after: is looked at this much earlier than the mark (late arrivals)
LIST_MAX = 100                   # ids one check lists (gmail.WATCH_PAGE_MAX): more new mail than this at once is a flood
_GMAIL_ID_RE = re.compile(r"[0-9A-Za-z_-]{6,64}")
_SKIP_LABELS = frozenset({"DRAFT", "SPAM", "TRASH"})


@dataclass(frozen=True)
class MailRef:
    account: str
    message_id: str               # Gmail's message id
    thread_id: str
    received_ms: int              # internalDate
    sender: MailPerson | None = None
    to: tuple[MailPerson, ...] = ()
    cc: tuple[MailPerson, ...] = ()
    reply_to: tuple[MailPerson, ...] = ()
    subject: str = ""
    msgid: str = ""               # the Message-ID header, "left@right"
    labels: frozenset[str] = frozenset()
    dmarc_pass: bool = False
    listy: bool = False
    auto: bool = False

    def __repr__(self) -> str:
        return f"MailRef({self.account}, {len(self.labels)} label(s))"

    @property
    def received(self) -> datetime:
        return datetime.fromtimestamp(self.received_ms / 1000, tz=timezone.utc)


@dataclass(frozen=True)
class Batch:
    refs: tuple[MailRef, ...] = ()
    more: int = 0
    mark_ms: int = 0
    seen: tuple[str, ...] = ()
    baseline: bool = False
    full: bool = False            # the check's list was full: more new mail than one check looks at (a flood)

    def __repr__(self) -> str:
        return f"Batch({len(self.refs)} new, {self.more} more, baseline {self.baseline}, full {self.full})"


def ref_from(data: Any, account: str) -> MailRef | None:
    """A users.messages.get answer (metadata or full) as a MailRef; None when it is not one, or is a draft, spam or
    trash."""
    if not isinstance(data, Mapping):
        return None
    message_id, thread_id = data.get("id"), data.get("threadId")
    if not (isinstance(message_id, str) and _GMAIL_ID_RE.fullmatch(message_id)
            and isinstance(thread_id, str) and _GMAIL_ID_RE.fullmatch(thread_id)):
        return None
    labels = frozenset(item for item in (data.get("labelIds") or ()) if isinstance(item, str))
    if labels & _SKIP_LABELS:
        return None
    try:
        received = int(data.get("internalDate"))
    except (TypeError, ValueError):
        return None
    thread = thread_from_api({"id": thread_id, "messages": [dict(data)]}, account)
    if thread is None or not thread.messages:
        return None
    message = thread.messages[0]
    headers = (data.get("payload") or {}).get("headers") if isinstance(data.get("payload"), Mapping) else None
    return MailRef(account=account, message_id=message_id, thread_id=thread_id, received_ms=received,
                   sender=message.sender, to=message.to, cc=message.cc, reply_to=message.reply_to,
                   subject=guard.scrub_secrets(message.subject)[0], msgid=message.message_id, labels=labels,
                   dmarc_pass=dmarc_passed(headers), listy=message.listy, auto=message.auto)


def _now_ms(now: datetime) -> int:
    return int(now.timestamp() * 1000)


def anything_new(reader: Any, after_ms: int) -> bool:
    """Has the account any mail at all since ``after_ms`` (less the overlap)? One list call, one id at most."""
    found, _estimate = reader.list_messages(f"after:{max(0, after_ms // 1000 - OVERLAP_S)}", max_results=1)
    return bool(found)


def new_mail(reader: Any, query: str, mark: Mapping[str, Any], now: datetime, *, max_new: int) -> Batch:
    """The new messages of ``query`` since ``mark`` ({"mark_ms": int, "seen": [ids]}), oldest first, at most
    ``max_new``. A mark without "mark_ms" is the job's first sight: the mark is set to ``now`` and nothing is new."""
    seen = [item for item in (mark.get("seen") or ()) if isinstance(item, str)]
    mark_ms = mark.get("mark_ms")
    if isinstance(mark_ms, bool) or not isinstance(mark_ms, int) or mark_ms <= 0:
        return Batch(mark_ms=_now_ms(now), seen=tuple(seen), baseline=True)
    after = mark_ms // 1000 - OVERLAP_S
    # Gmail lists the newest first: one full page, walked from its oldest end, so nothing between the mark and the
    # newest is passed over. Only the headers of what is taken (and of what drops out on the way) are read.
    listed, _estimate = reader.list_messages(watch_query(query, after), max_results=LIST_MAX)
    known = set(seen)
    taken: list[MailRef] = []
    left = 0
    for position, (message_id, _thread_id) in enumerate(reversed(listed)):
        if len(taken) >= max(0, max_new):
            left = len(listed) - position
            break
        if message_id in known:
            continue
        ref = ref_from(reader.message_metadata(message_id), reader.alias)
        if ref is None or ref.received_ms < mark_ms - OVERLAP_S * 1000:
            continue
        taken.append(ref)
    taken.sort(key=lambda item: (item.received_ms, item.message_id))
    # The mark moves only past what was taken: what still waits is newer, and the next check lists it again.
    new_mark = max([mark_ms] + [ref.received_ms for ref in taken])
    return Batch(refs=tuple(taken), more=left, mark_ms=new_mark,
                 seen=tuple(seen + [ref.message_id for ref in taken]), full=len(listed) >= LIST_MAX)


COUNT_IDS = 100


def recent(reader: Any, query: str, days: int, now: datetime, *, max_refs: int) -> tuple[list[MailRef], int]:
    """A dry run's matches: the newest ``max_refs`` messages of ``query`` in the last ``days``, and Gmail's estimate
    of how many match in that window."""
    after = int((now - timedelta(days=max(1, int(days)))).timestamp())
    listed, estimate = reader.list_messages(watch_query(query, after), max_results=max(COUNT_IDS, max_refs))
    refs = [ref for ref in (ref_from(reader.message_metadata(message_id), reader.alias)
                            for message_id, _thread in listed[:max_refs]) if ref is not None]
    return refs, max(estimate, len(listed))      # ids counted up to COUNT_IDS: a tidy-up's 90 never trusts a guess


def read_message(reader: Any, ref: MailRef, *, chars: int) -> tuple[MailMessage | None, int]:
    """The one matched message (format=full) with its text stripped, its secrets taken out and cut to ``chars``;
    how many values were taken out. None when Gmail no longer has it as it was."""
    data = reader.message(ref.message_id)
    if not isinstance(data, Mapping) or data.get("id") != ref.message_id or data.get("threadId") != ref.thread_id:
        return None, 0
    thread = thread_from_api({"id": ref.thread_id, "messages": [dict(data)]}, ref.account)
    if thread is None or not thread.messages:
        return None, 0
    message = thread.messages[0]
    whole = message.text or ""
    cap = max(200, int(chars))
    if len(whole) <= cap:
        text, count = guard.scrub_secrets(whole)
        cut = False
    else:
        keep = cap - len(CUT_MARK)
        text, count = guard.scrub_part(whole, 0, keep, desktop_service.SECRET_MARGIN)
        text, cut = text.rstrip() + CUT_MARK, True
    subject, subject_count = guard.scrub_secrets(message.subject)
    return replace(message, text=text, subject=subject, cut=cut), count + subject_count


def senders_of(refs: Sequence[MailRef]) -> int:
    return len({ref.sender.email.casefold() for ref in refs if ref.sender is not None})
