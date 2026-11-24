"""A rule's tidy-up at Google (no Qt): star, one user label, or archive the ONE message a rule matched, with a
write-ahead journal and an exact Undo (the owner's choice of 2026-11-12).

    GmailTidy(account, *, service_factory=None)
        the gmail.modify client of an account's OWN tidy-up sign-in (feature gmail_tidy,
        google_token_<alias>.tidy.json; never the account's main sign-in):
        labels_of(message_id) -> frozenset[str]        the message's labels now (format minimal)
        thread_in_inbox(thread_id, but=message_id) -> bool   another message of its conversation is in the inbox
        modify(message_id, *, add=(), remove=()) -> frozenset[str]
                                 users.messages.modify on that one message: only STARRED, one user label (Label_...)
                                 and INBOX may be named (ValueError otherwise; never a conversation, never trash, spam,
                                 read or unread); its labels after
    change_for(rule) -> Change   star: add STARRED; label: add the rule's label; archive: remove INBOX
    TidyJournal(path)            one JSON line per step: "doing" written and flushed BEFORE the call, then "done"
                                 (with the labels after) or "failed". Read back, an entry left at "doing" is
                                 "unknown": Jarvis may or may not have changed it. The runner keeps
                                 standing_tidy.jsonl; the app's Undo keeps standing_tidy_undo.jsonl (one writer each).
    undo_problem(tidy, labels_now, now) -> str     why an Undo can't happen ("" when it can): not done, older than
                                 UNDO_DAYS, or the labels are no longer exactly as Jarvis left them

Nothing of a mail is written here but Gmail's ids and label ids; the log gets kinds, ids of the rule and counts only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .model import TIDY_ARCHIVE, TIDY_LABEL, TIDY_STAR, Rule

logger = logging.getLogger(__name__)

TIDY_FEATURE = "gmail_tidy"
UNDO_DAYS = 7
STARRED, INBOX = "STARRED", "INBOX"
_LABEL_RE_PREFIX = "Label_"
MAX_JOURNAL_BYTES = 2_000_000
DOING, DONE, FAILED, UNKNOWN = "doing", "done", "failed", "unknown"
# Why a tidy-up did not happen at once (kept on the receipt; the Agents screen says it in words).
HELD_BROAD, HELD_NOT_SET_UP, HELD_NOT_SIGNED_IN, HELD_ACCOUNT, HELD_WRITTEN, HELD_CAP, HELD_PAUSED, HELD_FAILED, \
    HELD_ALREADY, HELD_UNKNOWN = ("broad", "not_set_up", "not_signed_in", "account", "written", "cap", "paused",
                                  "failed", "already", "unknown")


class TidyRefused(Exception):
    """Gmail answered and refused the change (a 4xx): nothing changed. Any other failure may have changed it."""


_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def plain_address(address: str) -> bool:
    return bool(_ADDRESS_RE.fullmatch(str(address or "")))


def sender_named(address: str, senders: Iterable[str]) -> bool:
    """The mail's own address is one a rule's from: names: the same address, or in the domain named ("acme.com",
    "@acme.com"). A bare word (Gmail would match it in a display name too) never is."""
    address = str(address or "").casefold()
    if not plain_address(address):
        return False
    domain = address.rpartition("@")[2]
    for value in senders:
        value = str(value or "").casefold().strip()
        if "@" in value and not value.startswith("@"):
            if value == address:
                return True
            continue
        value = value.lstrip("@")
        if "." in value and (domain == value or domain.endswith("." + value)):
            return True
    return False


@dataclass(frozen=True)
class Change:
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()

    def reverse(self) -> Change:
        return Change(self.remove, self.add)

    def done_in(self, labels: Iterable[str]) -> bool:
        """``labels`` already show this change (nothing to do)."""
        labels = set(labels)
        return set(self.add) <= labels and not (set(self.remove) & labels)


def change_for(rule: Rule) -> Change:
    kind = rule.tidy.kind
    if kind == TIDY_STAR:
        return Change(add=(STARRED,))
    if kind == TIDY_LABEL:
        if not rule.tidy.label_id.startswith(_LABEL_RE_PREFIX):
            raise ValueError("not a user label")
        return Change(add=(rule.tidy.label_id,))
    if kind == TIDY_ARCHIVE:
        return Change(remove=(INBOX,))
    raise ValueError("no tidy-up")


def _allowed(names: Iterable[str]) -> tuple[str, ...]:
    names = tuple(names)
    for name in names:
        if name not in (STARRED, INBOX) and not (name.startswith(_LABEL_RE_PREFIX) and len(name) <= 80):
            raise ValueError("a tidy-up names only STARRED, INBOX or one user label")
    if sum(1 for name in names if name.startswith(_LABEL_RE_PREFIX)) > 1:
        raise ValueError("a tidy-up names one user label at most")
    return names


class GmailTidy:
    """See the module docstring. ``account`` is the alias's tidy-up GoogleAccount (features ("gmail_tidy",))."""

    def __init__(self, account: Any, *, service_factory: Callable[[Any], Any] | None = None) -> None:
        from ..gmail import _build_service

        self._account = account
        self._service_factory = service_factory or _build_service
        self._service: Any = None
        self._service_creds: Any = None
        self._generation = -1
        self._lock = threading.Lock()

    @property
    def account(self) -> Any:
        return self._account

    @property
    def alias(self) -> str:
        return str(getattr(self._account, "alias", ""))

    def _get_service(self) -> Any:
        from ..gmail import _account_errors

        with _account_errors():
            creds = self._account.credentials(interactive=False, need=TIDY_FEATURE)
        generation = getattr(self._account, "generation", 0)
        if self._service is None or creds is not self._service_creds or self._generation != generation:
            self._service = self._service_factory(creds)
            self._service_creds, self._generation = creds, generation
        return self._service

    def _execute(self, request: Any) -> Any:
        try:
            return request.execute(num_retries=0)
        except Exception as exc:  # noqa: BLE001 - a 4xx is Gmail's refusal; anything else may or may not have changed
            from ..gmail import _status_of

            if 400 <= _status_of(exc) < 500:
                raise TidyRefused(f"Gmail refused it ({_status_of(exc)})") from None
            raise

    @staticmethod
    def _check_id(value: str) -> str:
        from ..gmail import _GMAIL_ID_RE

        if not isinstance(value, str) or not _GMAIL_ID_RE.fullmatch(value):
            raise ValueError("not a Gmail id")
        return value

    def labels_of(self, message_id: str) -> frozenset[str]:
        message_id = self._check_id(message_id)
        with self._lock:
            service = self._get_service()
            answer = self._execute(service.users().messages().get(userId="me", id=message_id, format="minimal",
                                                                  fields="id,labelIds"))
        return frozenset(str(label) for label in (answer or {}).get("labelIds") or ())

    def thread_in_inbox(self, thread_id: str, *, but: str) -> bool:
        thread_id = self._check_id(thread_id)
        with self._lock:
            service = self._get_service()
            answer = self._execute(service.users().threads().get(userId="me", id=thread_id, format="minimal",
                                                                 fields="messages(id,labelIds)"))
        return any(item.get("id") != but and INBOX in (item.get("labelIds") or ())
                   for item in (answer or {}).get("messages") or () if isinstance(item, dict))

    def modify(self, message_id: str, *, add: Iterable[str] = (), remove: Iterable[str] = ()) -> frozenset[str]:
        message_id = self._check_id(message_id)
        add, remove = _allowed(add), _allowed(remove)
        if not add and not remove:
            raise ValueError("nothing to change")
        with self._lock:
            service = self._get_service()
            answer = self._execute(service.users().messages().modify(
                userId="me", id=message_id, body={"addLabelIds": list(add), "removeLabelIds": list(remove)}))
        return frozenset(str(label) for label in (answer or {}).get("labelIds") or ())


# --------------------------------------------------------------------------------------------------------------
# The journal
# --------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TidyEntry:
    id: str
    state: str                       # DOING (read back: UNKNOWN), DONE, FAILED
    at: str = ""
    rule: str = ""
    account: str = ""
    message: str = ""
    thread: str = ""
    kind: str = ""
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()


class TidyJournal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _append(self, record: Mapping[str, Any]) -> None:
        line = json.dumps(dict(record), ensure_ascii=True, sort_keys=True) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())

    def begin(self, *, rule: str, account: str, message: str, thread: str, kind: str, change: Change,
              before: Iterable[str], now: datetime) -> str:
        """Write "doing" (flushed to disk) before the call; the entry's id."""
        entry_id = secrets.token_hex(6)
        self._append({"id": entry_id, "state": DOING, "at": now.isoformat(timespec="seconds"), "rule": rule,
                      "account": account, "message": message, "thread": thread, "kind": kind,
                      "add": list(change.add), "remove": list(change.remove), "before": sorted(before)})
        return entry_id

    def done(self, entry_id: str, after: Iterable[str], now: datetime) -> None:
        self._append({"id": entry_id, "state": DONE, "at": now.isoformat(timespec="seconds"),
                      "after": sorted(after)})

    def failed(self, entry_id: str, now: datetime) -> None:
        self._append({"id": entry_id, "state": FAILED, "at": now.isoformat(timespec="seconds")})

    def entries(self) -> dict[str, TidyEntry]:
        """Every entry by id, as its last line left it ("doing" with nothing after it reads UNKNOWN)."""
        try:
            with open(self.path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - MAX_JOURNAL_BYTES))     # the newest part: pruned at every check anyway
                raw = fh.read(MAX_JOURNAL_BYTES)
        except OSError:
            return {}
        found: dict[str, dict[str, Any]] = {}
        for line in raw.decode("utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue                     # a line cut by a crash
            if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                continue
            entry = found.setdefault(record["id"], {})
            entry.update({key: value for key, value in record.items() if value is not None})
        result: dict[str, TidyEntry] = {}
        for entry_id, data in found.items():
            state = data.get("state")
            state = UNKNOWN if state == DOING else state if state in (DONE, FAILED) else UNKNOWN

            def names(key: str) -> tuple[str, ...]:
                value = data.get(key)
                return tuple(str(item) for item in value) if isinstance(value, list) else ()

            result[entry_id] = TidyEntry(entry_id, state, str(data.get("at") or ""), str(data.get("rule") or ""),
                                         str(data.get("account") or ""), str(data.get("message") or ""),
                                         str(data.get("thread") or ""), str(data.get("kind") or ""),
                                         names("add"), names("remove"), names("before"), names("after"))
        return result


    def prune(self, before: datetime) -> int:
        """Drop the entries begun before ``before`` (rewritten atomically by its one writer); how many went."""
        entries = self.entries()
        keep = [entry for entry in entries.values()
                if (_parse(entry.at) or before) >= before]
        if len(keep) == len(entries):
            return 0
        lines = []
        for entry in keep:
            record = {"id": entry.id, "state": DOING if entry.state == UNKNOWN else entry.state, "at": entry.at,
                      "rule": entry.rule, "account": entry.account, "message": entry.message,
                      "thread": entry.thread, "kind": entry.kind, "add": list(entry.add),
                      "remove": list(entry.remove), "before": list(entry.before), "after": list(entry.after)}
            lines.append(json.dumps(record, ensure_ascii=True, sort_keys=True))
        try:
            fd, part = tempfile.mkstemp(prefix=f"{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("".join(line + "\n" for line in lines))
                fh.flush()
                os.fsync(fh.fileno())
            from .. import atomic

            atomic.replace(part, self.path)
        except OSError:
            return 0
        return len(entries) - len(keep)


def _parse(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else moment.astimezone()


def undo_problem(tidy: Mapping[str, Any], labels_now: Iterable[str], now: datetime, done_at: datetime | None) -> str:
    """Why a tidy-up can't be undone now ("" when it can): it was not done, it is older than UNDO_DAYS, or the labels
    Jarvis changed are no longer as he left them (the owner or another program changed them; other labels, such as
    UNREAD, do not count)."""
    if not tidy.get("done"):
        return "not_done"
    if done_at is None or now - done_at > timedelta(days=UNDO_DAYS):
        return "too_old"
    add, remove = tidy.get("add"), tidy.get("remove")
    if not isinstance(add, list) or not isinstance(remove, list):
        return "changed"
    labels_now = set(labels_now)
    if not set(str(item) for item in add) <= labels_now or set(str(item) for item in remove) & labels_now:
        return "changed"
    return ""
