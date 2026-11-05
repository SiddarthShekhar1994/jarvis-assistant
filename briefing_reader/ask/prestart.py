"""Claude Code started ahead of the question, so an Ask's first words do not wait for it to start.

    Prestarter(runner, extra_files=(), clock=, idle_limit_s=)
        fill(slot, argv, env, cwd)     one idle process for that slot's exact command line (the one it had is let go
                                       when it differs); nothing is sent to it, so it makes no model request
        take(slot, argv, env, cwd)     the idle process when it is exactly what the cold path would start now, else
                                       None (the caller runs cold). Whatever is taken leaves the pool: one question,
                                       one process, one use
        sweep()                        recycles what idled too long, died or whose files changed (the next one starts
                                       at once)
        forget(slot)                   lets go of what that slot holds and wants nothing more (until the next fill)
        shutdown()                     kills everything idle; nothing starts after it
        wants(slot), holds(slot)       what a slot was asked for, whether one waits now

A slot is one kind of question: SLOT_ASK (an Ask's first run) and SLOT_DESKTOP (a desktop request's run, only once one
has run). A second planner run, the other planner modes and web research always start cold.

Nothing here counts anything: the rate caps count at the send (AskPlanner._run), never at the start. A process is
refused when its command line, environment or folder would differ from the cold path's, and is recycled after
IDLE_LIMIT_S idle or when a file it was started with changed (WATCHED_FLAGS in its command line, claude.exe itself,
``extra_files``: config.toml, the schema). Each process is the runner's own: a kill-on-close Job Object (so one left
behind by a crash ends with Jarvis) and, its stdin closing with Jarvis, nothing to wait for anyway.

While the test guard is on (desktop.backend.TEST_GUARD_ENV) the real runner never starts one. The log names slots,
reasons and counts: never a command line, a path or a value of the environment.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..desktop.backend import real_backend_blocked
from . import cli

logger = logging.getLogger(__name__)

SLOT_ASK = "ask"
SLOT_DESKTOP = "desktop"
IDLE_LIMIT_S = 30 * 60.0
SWEEP_EVERY_S = 60.0              # how often the app asks for a sweep
MAX_DEATHS = 3                    # a slot whose processes keep dying is left alone until the next real run
KILL_WAIT_S = 2.0
WATCHED_FLAGS = ("--system-prompt-file", "--settings")

Key = tuple[tuple[str, ...], tuple[tuple[str, str], ...], str]    # the exact argv, environment and folder


def _key(argv: Sequence[str], env: Mapping[str, str], cwd: Path | str) -> Key:
    return tuple(argv), tuple(sorted(env.items())), str(cwd)


def watched_files(argv: Sequence[str]) -> list[Path]:
    """The files a process with this command line read as it started: claude.exe, its prompt and its settings."""
    found = [Path(argv[0])] if argv else []
    for flag in WATCHED_FLAGS:
        if flag in argv and argv.index(flag) + 1 < len(argv):
            found.append(Path(argv[argv.index(flag) + 1]))
    return found


def _signature(paths: Iterable[Path]) -> tuple[tuple[str, int, int], ...]:
    """Each file's size and time (no file: -1): nothing is read."""
    marks = []
    for path in paths:
        try:
            stat = path.stat()
            marks.append((str(path), stat.st_size, stat.st_mtime_ns))
        except OSError:
            marks.append((str(path), -1, -1))
    return tuple(marks)


@dataclass
class _Idle:
    process: Any
    key: Key
    started: float
    files: tuple[tuple[str, int, int], ...]


class Prestarter:
    """See the module docs. Thread-safe: the ask thread fills, takes and sweeps; shutdown comes from the GUI thread."""

    def __init__(self, runner: Any, *, extra_files: Sequence[Path] = (), clock: Callable[[], float] = time.monotonic,
                 idle_limit_s: float = IDLE_LIMIT_S) -> None:
        self._runner = runner
        self._extra = tuple(Path(path) for path in extra_files)
        self._clock = clock
        self._idle_limit_s = float(idle_limit_s)
        self._lock = threading.Lock()
        self._idle: dict[str, _Idle] = {}
        self._wanted: dict[str, Key] = {}
        self._deaths: dict[str, int] = {}
        self._closed = False

    # ---- what the engine asks ------------------------------------------------------------------

    def wants(self, slot: str) -> bool:
        with self._lock:
            return slot in self._wanted

    def holds(self, slot: str) -> bool:
        with self._lock:
            return slot in self._idle

    def fill(self, slot: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path | str, *,
             used: bool = False) -> bool:
        """One idle process for exactly this command line; True when one waits now. ``used``: a real run asked for
        it, so a slot that had given up tries again."""
        key = _key(argv, env, cwd)
        with self._lock:
            if self._closed:
                return False
            self._wanted[slot] = key
            if used:
                self._deaths.pop(slot, None)
            idle = self._idle.get(slot)
            if idle is not None and idle.key == key:
                return True
            old = self._idle.pop(slot, None)
        if old is not None:
            self._let_go(slot, old, "another command line")
        return self._start(slot, key)

    def take(self, slot: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path | str) -> Any | None:
        """The waiting process when it is exactly the cold path's now (it leaves the pool), else None."""
        with self._lock:
            idle = self._idle.pop(slot, None)
        if idle is None:
            return None
        why = self._refusal(slot, idle, _key(argv, env, cwd))
        if why:
            self._let_go(slot, idle, why)
            return None
        return idle.process

    def sweep(self) -> None:
        """Recycle what idled too long, died or whose files changed; start what a slot still wants."""
        with self._lock:
            slots = list(self._wanted) if not self._closed else []
        for slot in slots:
            with self._lock:
                idle = self._idle.get(slot)
                why = self._staleness(slot, idle) if idle is not None else ""
                if why:
                    self._idle.pop(slot, None)
                key = self._wanted.get(slot)
                given_up = self._deaths.get(slot, 0) >= MAX_DEATHS
            if idle is not None and why:
                self._let_go(slot, idle, why)
                idle = None
            if idle is None and key is not None and not given_up:
                self._start(slot, key)

    def forget(self, slot: str) -> None:
        """That slot lets go of what it holds and wants nothing more, until a run fills it again."""
        with self._lock:
            idle = self._idle.pop(slot, None)
            self._wanted.pop(slot, None)
            self._deaths.pop(slot, None)
        if idle is not None:
            self._let_go(slot, idle, "not wanted any more")

    def shutdown(self) -> None:
        """Kill everything idle; nothing starts after this."""
        with self._lock:
            self._closed = True
            idle = list(self._idle.items())
            self._idle.clear()
        for slot, item in idle:
            self._let_go(slot, item, "Jarvis closes")

    # ---- inside --------------------------------------------------------------------------------

    def _files(self, argv: Sequence[str]) -> tuple[tuple[str, int, int], ...]:
        return _signature([*watched_files(argv), *self._extra])

    def _staleness(self, slot: str, idle: _Idle) -> str:
        """Why it may not wait any longer ("" when it may)."""
        if not _alive(idle.process):
            self._deaths[slot] = self._deaths.get(slot, 0) + 1
            return "it ended"
        if self._clock() - idle.started >= self._idle_limit_s:
            return "idle too long"
        if self._files(idle.key[0]) != idle.files:
            return "a file changed"
        return ""

    def _refusal(self, slot: str, idle: _Idle, key: Key) -> str:
        """Why this process may not take the question ("" when it may)."""
        with self._lock:
            why = self._staleness(slot, idle)
        if why:
            return why
        if idle.key[0] != key[0]:
            return "another command line"
        if idle.key[1] != key[1]:
            return "another environment"
        if idle.key[2] != key[2]:
            return "another folder"
        return ""

    def _start(self, slot: str, key: Key) -> bool:
        if isinstance(self._runner, cli.SubprocessRunner) and real_backend_blocked():
            logger.info("Ask: no process started ahead (the test guard is on)")
            return False
        with self._lock:
            if self._closed:
                return False
        argv, env, cwd = key
        try:
            process = self._runner.start(list(argv), stdin_text="", env=dict(env), cwd=Path(cwd))
        except OSError as exc:
            logger.warning("Ask: Claude Code could not be started ahead (%s)", type(exc).__name__)
            return False
        idle = _Idle(process, key, self._clock(), self._files(argv))
        with self._lock:
            closed = self._closed
            old = self._idle.pop(slot, None)
            if not closed:
                self._idle[slot] = idle
        if old is not None:
            self._let_go(slot, old, "a newer one")
        if closed:
            self._let_go(slot, idle, "Jarvis closes")
            return False
        logger.info("Ask: Claude Code started ahead (%s)", slot)
        return True

    def _let_go(self, slot: str, idle: _Idle, why: str) -> None:
        logger.info("Ask: a Claude Code started ahead was let go (%s; %s)", slot, why)
        _kill(idle.process)


def _alive(process: Any) -> bool:
    getter = getattr(process, "alive", None)
    try:
        return bool(getter()) if callable(getter) else True
    except Exception:  # noqa: BLE001 - unknown: as if it ended
        return False


def _kill(process: Any) -> None:
    """The runner's own kill (its Job Object goes with it); a process that will not go is the OS's to end."""
    try:
        process.kill()
        process.wait(KILL_WAIT_S)
        process.close()
    except Exception as exc:  # noqa: BLE001 - never stops the app
        logger.warning("Ask: a Claude Code started ahead could not be closed (%s)", type(exc).__name__)
