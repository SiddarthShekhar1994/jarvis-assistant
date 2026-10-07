"""Who a Reply or Email goes to: the NEW RECIPIENT check ("confirm new people").

Jarvis holds only gmail.send, so it cannot read a thread to prove that an address belongs in it.
Instead an address needs no extra confirmation only when it is

- the sending account's own address (then it is not a recipient at all: it is taken out of To
  and Cc),
- in a domain of ``[actions] trusted_domains``, or a subdomain of one (empty by default), or
- an address Jarvis sent to before (RecipientHistory: recipients.json).

Every other address is NEW: its card shows a red NEW RECIPIENT chip and Send waits until it is
ticked in the Edit dialog ("Send to <address>"; ProposedAction.confirmed, memory only).
Addresses are compared as plain, casefolded addresses; a display name never counts.

    RecipientHistory(path)    %LOCALAPPDATA%\\briefing-reader\\recipients.json: the sha256 of each
                              address Jarvis sent to and when (never the address itself)
    classify(addresses, ...)  address -> OWN / TRUSTED / KNOWN / NEW
    review(to, cc, ...)       what a card shows and Send needs: the recipients without the own
                              address, which are new, which of those are still unconfirmed

Qt-free. Logs carry counts only, never an address or a hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

RECIPIENTS_FILE = "recipients.json"
OWN = "own"            # the sending account itself (taken out of To / Cc)
TRUSTED = "trusted"    # in [actions] trusted_domains
KNOWN = "known"        # Jarvis sent to it before
NEW = "new"            # needs a tick in the Edit dialog before Send
MAX_REMEMBERED = 5000  # the most recent addresses kept in recipients.json
NO_RECIPIENT_LEFT = ("Nobody is left in To once your own address is taken out - "
                     "Edit the recipients")
_KEY_RE = re.compile(r"[0-9a-f]{64}")


def address_key(address: str) -> str:
    """How recipients.json names an address: sha256 of the casefolded address (hex)."""
    return hashlib.sha256(address.strip().casefold().encode("utf-8")).hexdigest()


def in_domains(address: str, domains: Iterable[str]) -> bool:
    """``address`` is in one of ``domains`` or a subdomain of one ("lab.example.edu" counts for
    "example.edu"; "badexample.edu" does not)."""
    _, at, domain = address.rpartition("@")
    if not at:
        return False
    domain = domain.casefold().rstrip(".")
    for trusted in domains:
        trusted = trusted.casefold().strip().lstrip("@").rstrip(".")
        if trusted and (domain == trusted or domain.endswith("." + trusted)):
            return True
    return False


class RecipientHistory:
    """The addresses Jarvis sent to: {sha256(casefold(address)): last sent (ISO)}, at most
    MAX_REMEMBERED (the oldest are dropped). Read once, then kept in memory; writes are atomic.
    An unreadable file counts as empty (logged without its content); a failed write keeps the
    addresses for this run."""

    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._lock = threading.Lock()
        self._entries: dict[str, str] | None = None

    def knows(self, address: str) -> bool:
        with self._lock:
            return address_key(address) in self._load()

    def __len__(self) -> int:
        with self._lock:
            return len(self._load())

    def add(self, addresses: Iterable[str]) -> bool:
        """Remember ``addresses`` as sent to now. True when it was saved to the file."""
        keys = {address_key(address) for address in addresses if address and address.strip()}
        if not keys:
            return True
        now = self._clock().isoformat(timespec="seconds")
        with self._lock:
            entries = self._load()
            for key in keys:
                entries[key] = now
            if len(entries) > MAX_REMEMBERED:
                newest = sorted(entries.items(), key=lambda item: item[1], reverse=True)[:MAX_REMEMBERED]
                entries.clear()
                entries.update(newest)
            saved = self._write(entries)
        logger.info("Remembered %d recipient(s) as sent to through Jarvis", len(keys))
        return saved

    def _load(self) -> dict[str, str]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, str] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as exc:
            logger.warning("Could not read the list of earlier recipients (%s); every recipient counts "
                           "as new until the next send", type(exc).__name__)
            data = {}
        if not isinstance(data, dict):
            logger.warning("The list of earlier recipients is not a table; every recipient counts as new")
            data = {}
        for key, value in data.items():
            if isinstance(key, str) and _KEY_RE.fullmatch(key) and isinstance(value, str):
                entries[key] = value
        if len(entries) != len(data):
            logger.warning("Ignored %d malformed entr(ies) in the list of earlier recipients",
                           len(data) - len(entries))
        self._entries = entries
        return entries

    def _write(self, entries: dict[str, str]) -> bool:
        text = json.dumps(dict(sorted(entries.items())), indent=1) + "\n"
        part: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, part = tempfile.mkstemp(prefix=f"{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(part, self.path)
            part = None
            return True
        except OSError as exc:
            logger.warning("Could not save the list of earlier recipients (%s); kept for this run only",
                           exc.strerror or type(exc).__name__)
            return False
        finally:
            if part is not None:
                try:
                    os.unlink(part)
                except OSError:
                    pass


def classify(addresses: Iterable[str], *, own: str, trusted_domains: Sequence[str],
             history: RecipientHistory | None) -> dict[str, str]:
    """address -> OWN, TRUSTED, KNOWN or NEW (first match wins, in that order)."""
    own_key = own.casefold() if own else ""
    kinds: dict[str, str] = {}
    for address in addresses:
        if own_key and address.casefold() == own_key:
            kinds[address] = OWN
        elif in_domains(address, trusted_domains):
            kinds[address] = TRUSTED
        elif history is not None and history.knows(address):
            kinds[address] = KNOWN
        else:
            kinds[address] = NEW
    return kinds


@dataclass(frozen=True)
class RecipientReview:
    """What a Reply / Email card shows about its recipients and what Send needs.

    ``to`` / ``cc`` are what is sent (the own address taken out). ``kinds`` names every address
    of the card (the own one too) with OWN / TRUSTED / KNOWN / NEW, in To-then-Cc order.
    """

    to: tuple[str, ...]
    cc: tuple[str, ...]
    kinds: tuple[tuple[str, str], ...]
    own_dropped: bool
    new: tuple[str, ...]            # the red NEW RECIPIENT chips
    unconfirmed: tuple[str, ...]    # new and not ticked yet: Send opens the Edit dialog
    problem: str = ""               # why nothing can be sent as it stands ("" = nothing)

    @property
    def recipients(self) -> tuple[str, ...]:
        return self.to + self.cc

    @property
    def ready(self) -> bool:
        """Send may go ahead as far as the recipients go."""
        return not self.problem and not self.unconfirmed

    def kind_of(self, address: str) -> str:
        key = address.casefold()
        return next((kind for item, kind in self.kinds if item.casefold() == key), "")


def review(to: Sequence[str], cc: Sequence[str], *, own: str, confirmed: Collection[str] = (),
           trusted_domains: Sequence[str] = (), history: RecipientHistory | None = None) -> RecipientReview:
    """The recipients of a Reply / Email as a card shows them and Send needs them (no network)."""
    everyone = tuple(to) + tuple(cc)
    kinds = classify(everyone, own=own, trusted_domains=trusted_domains, history=history)
    send_to = tuple(address for address in to if kinds[address] != OWN)
    send_cc = tuple(address for address in cc if kinds[address] != OWN)
    ticked = {address.casefold() for address in confirmed}
    new = tuple(address for address in send_to + send_cc if kinds[address] == NEW)
    unconfirmed = tuple(address for address in new if address.casefold() not in ticked)
    return RecipientReview(to=send_to, cc=send_cc, kinds=tuple(kinds.items()),
                           own_dropped=len(send_to) + len(send_cc) != len(everyone), new=new,
                           unconfirmed=unconfirmed, problem="" if send_to else NO_RECIPIENT_LEFT)
