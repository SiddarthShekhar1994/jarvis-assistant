"""How many planner runs Ask Jarvis made: the hourly and daily caps, and the extra-usage pause.

    UsageLog(path, max_per_hour=20, max_per_day=60, clock=...)
        check(runs=1)    CapCheck: may ``runs`` more planner runs start now? (and how many are left)
        start()          records a run as it starts (it counts even if Jarvis stops mid-run)
        finish(run, ...) adds its duration, turns, token totals and outcome kind
        hold(until)      no run may start before ``until`` (Claude Code said a run would use extra
                         usage: Ask pauses until the plan's limit resets)

%LOCALAPPDATA%\\briefing-reader\\ask_usage.json holds one entry per run: a random id, when it
started, how long it took, turns, input / output / cache token totals and the outcome kind; and
the pause, when there is one. Never the request, the context or anything the planner wrote.
Entries older than two days are pruned.

The file is the count for every Jarvis process (the app and ``--ask-text`` from the command line):
each check and each write reads it again under a lock file (ask_usage.json.lock), so runs made
by another process count here too and a write never drops them. An unreadable file counts as
empty (logged once, without its content); a failed write keeps this process's runs in memory, so
the caps still hold for it.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

USAGE_FILE = "ask_usage.json"
KEEP = timedelta(days=2)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
MAX_HOLD = timedelta(days=8)
LOCK_TIMEOUT_S = 3.0
_FIELDS = ("duration_ms", "turns", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


@dataclass(frozen=True)
class CapCheck:
    allowed: bool
    left_hour: int
    left_day: int
    message: str = ""          # why not (safe to show and log: no request text)
    retry_at: datetime | None = None
    held: bool = False         # refused by the extra-usage pause (not by a count)


def _clock_words(moment: datetime, now: datetime | None = None) -> str:
    hour = moment.hour % 12 or 12
    text = f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
    if now is not None:
        try:
            if moment - now >= timedelta(hours=20):
                return f"{moment.strftime('%a')} {text}"
        except TypeError:
            pass
    return text


def hold_message(until: datetime, now: datetime | None = None) -> str:
    return (f"Ask is paused until {_clock_words(until, now)}: turn usage credits off at claude.ai/settings/usage "
            "(Claude Code said a request would use them)")


class _FileLock:
    """An exclusive lock on ``path`` shared by every process (msvcrt on Windows, fcntl elsewhere).
    When it can't be had within ``timeout`` the caller goes on without it (logged once)."""

    def __init__(self, path: Path, timeout: float = LOCK_TIMEOUT_S) -> None:
        self.path = path
        self.timeout = timeout
        self.locked = False
        self._fh: Any = None

    def __enter__(self) -> _FileLock:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "a+b")   # noqa: SIM115 - closed in __exit__
        except OSError:
            self._fh = None
            return self
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._lock()
                self.locked = True
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    return self
                time.sleep(0.02)

    def _lock(self) -> None:
        if sys.platform == "win32":
            import msvcrt

            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, *_exc: Any) -> None:
        if self._fh is None:
            return
        try:
            if self.locked:
                if sys.platform == "win32":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.locked = False
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


class UsageLog:
    def __init__(self, path: Path, *, max_per_hour: int = 20, max_per_day: int = 60,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.max_per_hour = max(1, int(max_per_hour))
        self.max_per_day = max(1, int(max_per_day))
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._lock = threading.Lock()
        # This process's runs by id: they count even when the file could not be written.
        self._local: dict[str, dict[str, Any]] = {}
        self._local_hold: datetime | None = None
        self._warned: set[str] = set()

    # ---- the file ----------------------------------------------------------------------------

    def _warn_once(self, key: str, text: str, *args: Any) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(text, *args)

    def _read(self) -> tuple[list[dict[str, Any]], datetime | None]:
        """The file's entries and pause (an unreadable file counts as empty)."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return [], None
        except (OSError, ValueError) as exc:
            self._warn_once("read", "Ask: could not read the usage counts (%s); counting from now",
                            type(exc).__name__)
            return [], None
        if not isinstance(data, dict):
            return [], None
        entries: list[dict[str, Any]] = []
        runs = data.get("runs")
        for item in runs if isinstance(runs, list) else ():
            if not isinstance(item, dict) or not isinstance(item.get("at"), str):
                continue
            at = _aware(item["at"])
            if at is None:
                continue
            entry: dict[str, Any] = {"at": at, "outcome": str(item.get("outcome") or "")[:30]}
            run_id = item.get("id")
            if isinstance(run_id, str) and run_id.isalnum() and len(run_id) <= 40:
                entry["id"] = run_id
            for name in _FIELDS:
                value = item.get(name)
                entry[name] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
            entries.append(entry)
        hold = _aware(data.get("hold_until")) if isinstance(data.get("hold_until"), str) else None
        return entries, hold

    def _state(self, now: datetime) -> tuple[list[dict[str, Any]], datetime | None]:
        """Every run that counts now (the file's and this process's unsaved ones) and the pause."""
        entries, hold = self._read()
        for run_id in [run_id for run_id, entry in self._local.items() if not _recent(entry, now)]:
            del self._local[run_id]
        saved = {entry.get("id") for entry in entries}
        entries = [entry for entry in entries if _recent(entry, now)]
        entries += [dict(entry) for run_id, entry in self._local.items() if run_id not in saved]
        holds = [moment for moment in (hold, self._local_hold) if moment is not None and moment > now]
        if self._local_hold is not None and self._local_hold <= now:
            self._local_hold = None
        return entries, (max(holds) if holds else None)

    def _write(self, entries: list[dict[str, Any]], hold: datetime | None) -> bool:
        data: dict[str, Any] = {"runs": [{"id": entry.get("id", ""), "at": entry["at"].isoformat(timespec="seconds"),
                                          "outcome": entry["outcome"], **{name: entry[name] for name in _FIELDS}}
                                         for entry in sorted(entries, key=lambda item: item["at"])]}
        if hold is not None:
            data["hold_until"] = hold.isoformat(timespec="seconds")
        part: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, part = tempfile.mkstemp(prefix=f"{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(data, indent=1) + "\n")
            os.replace(part, self.path)
            part = None
            return True
        except OSError as exc:
            logger.warning("Ask: could not save the usage counts (%s); kept for this run only",
                           exc.strerror or type(exc).__name__)
            return False
        finally:
            if part is not None:
                try:
                    os.unlink(part)
                except OSError:
                    pass

    # ---- reading ---------------------------------------------------------------------------

    def counts(self, now: datetime | None = None) -> tuple[int, int]:
        """(runs in the last hour, runs in the last 24 hours)."""
        now = now or self._clock()
        with self._lock, _FileLock(self.lock_path):
            entries, _ = self._state(now)
        return (sum(1 for entry in entries if now - entry["at"] < HOUR),
                sum(1 for entry in entries if now - entry["at"] < DAY))

    def hold_until(self, now: datetime | None = None) -> datetime | None:
        """When the extra-usage pause ends (None when there is none)."""
        now = now or self._clock()
        with self._lock, _FileLock(self.lock_path):
            return self._state(now)[1]

    def check(self, runs: int = 1, now: datetime | None = None) -> CapCheck:
        """May ``runs`` more planner runs start now?"""
        now = now or self._clock()
        with self._lock, _FileLock(self.lock_path):
            entries, hold = self._state(now)
        entries.sort(key=lambda entry: entry["at"])
        hour = [entry["at"] for entry in entries if now - entry["at"] < HOUR]
        day = [entry["at"] for entry in entries if now - entry["at"] < DAY]
        left_hour, left_day = self.max_per_hour - len(hour), self.max_per_day - len(day)
        if hold is not None:
            return CapCheck(False, max(0, left_hour), max(0, left_day), hold_message(hold, now), hold, held=True)
        if left_day < runs:
            index = max(0, len(day) - self.max_per_day + runs - 1)
            retry = day[index] + DAY if day else now
            return CapCheck(False, max(0, left_hour), max(0, left_day),
                            f"Ask's daily limit reached; try again after {_clock_words(retry, now)} "
                            f"({self.max_per_day} planner runs a day: [ask] max_per_day)", retry)
        if left_hour < runs:
            index = max(0, len(hour) - self.max_per_hour + runs - 1)
            retry = hour[index] + HOUR if hour else now
            return CapCheck(False, max(0, left_hour), max(0, left_day),
                            f"Ask limit reached; try again after {_clock_words(retry, now)} "
                            f"({self.max_per_hour} planner runs an hour: [ask] max_per_hour)", retry)
        return CapCheck(True, left_hour, left_day)

    # ---- writing ---------------------------------------------------------------------------

    def start(self, now: datetime | None = None) -> str:
        """Record a run that starts now; returns its handle for finish()."""
        now = now or self._clock()
        run_id = uuid.uuid4().hex[:16]
        entry: dict[str, Any] = {"id": run_id, "at": now, "outcome": "started"}
        entry.update({name: 0 for name in _FIELDS})
        with self._lock, _FileLock(self.lock_path):
            self._local[run_id] = entry
            entries, hold = self._state(now)
            self._write(entries, hold)
        return run_id

    def finish(self, run: str, *, outcome: str, duration_ms: int = 0, turns: int = 0, input_tokens: int = 0,
               output_tokens: int = 0, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> None:
        values = {"duration_ms": duration_ms, "turns": turns, "input_tokens": input_tokens,
                  "output_tokens": output_tokens, "cache_read_tokens": cache_read_tokens,
                  "cache_write_tokens": cache_write_tokens}
        now = self._clock()
        with self._lock, _FileLock(self.lock_path):
            local = self._local.get(run)
            if local is not None:
                local["outcome"] = str(outcome)[:30]
                for name, value in values.items():
                    local[name] = int(value) if isinstance(value, int) and value >= 0 else 0
            entries, hold = self._state(now)
            for entry in entries:
                if entry.get("id") == run and local is not None:
                    entry.update({key: local[key] for key in ("outcome", *_FIELDS)})
            self._write(entries, hold)

    def hold(self, until: datetime, now: datetime | None = None) -> datetime:
        """Pause Ask until ``until`` (kept to at most MAX_HOLD from now); returns the pause's end."""
        now = now or self._clock()
        until = min(until, now + MAX_HOLD)
        with self._lock, _FileLock(self.lock_path):
            if self._local_hold is None or until > self._local_hold:
                self._local_hold = until
            entries, hold = self._state(now)
            hold = max(hold, until) if hold is not None else until
            self._write(entries, hold)
        return hold


def _aware(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _recent(entry: dict[str, Any], now: datetime) -> bool:
    return now - entry["at"] < KEEP and entry["at"] <= now + HOUR
