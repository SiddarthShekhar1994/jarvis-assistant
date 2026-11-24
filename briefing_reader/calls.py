"""Is the owner on a call? Another app is using the microphone right now (no Qt; smaller items part 6).

    mic_in_use_by_others(read=None, own=None) -> bool
        Windows' own list of which apps use the microphone (the privacy page's "recently used" list), read only:
        HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone. Each
        packaged app is a key there, each other program a key under its NonPackaged key; an app uses the microphone
        now when its LastUsedTimeStart is set and its LastUsedTimeStop is 0 or missing - unless it started before
        the PC last started (left over from a crash or a power cut; ``boot``: real_boot_filetime, now minus
        GetTickCount64, read only, refused while the tests run). Jarvis's own programs (this Python and
        its python / pythonw siblings: so the owner's other Python programs too) are left out, so his own listener
        never counts. ``read`` is a reader (``subkeys(path)`` / ``values(path)``, paths below that key); None is the
        real one (:class:`WinregReader`), or no reader at all off Windows and while the tests run - then False.
    scan(read, own) -> Scan                 the same, with how many entries could not be read
    CallWatch(probe, clock, poll_s=3, hold_s=6)
        ``poll()`` -> ON when a call starts, OFF once the microphone was free for ``hold_s`` (a short gap in a call
        is held), else None; ``on_call``; logs "Calls: quiet (another app is using the microphone)" and "Calls: over
        after N min".

Nothing in Windows is changed: the reader opens keys for reading only (never creates one, never writes a value).
Only a yes/no is kept: never an app's name or path, in memory past one scan or in a log. While the tests run
(JARVIS_DESKTOP_TEST_GUARD=1) the real reader refuses before it touches the registry.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"
ROOT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
NON_PACKAGED = "NonPackaged"
START, STOP = "LastUsedTimeStart", "LastUsedTimeStop"
POLL_S = 3.0                 # how often the app asks
HOLD_S = 6.0                 # the microphone free this long before a call is over (a short gap is held)
ON, OFF = "on", "off"


class Reader(Protocol):
    def subkeys(self, path: str) -> list[str]: ...

    def values(self, path: str) -> dict[str, Any]: ...


class GuardError(RuntimeError):
    """The real registry was asked while the tests run."""


class WinregReader:
    """The real registry, read only (winreg.OpenKey with KEY_READ: no key is ever made, no value set). Paths are below
    ``ROOT`` in HKEY_CURRENT_USER ("" is ROOT itself)."""

    def _open(self, path: str) -> Any:
        if os.environ.get(TEST_GUARD_ENV) == "1":
            raise GuardError("the registry is never read while the tests run")
        import winreg

        full = ROOT + ("\\" + path if path else "")
        return winreg, winreg.OpenKey(winreg.HKEY_CURRENT_USER, full, 0, winreg.KEY_READ)

    def subkeys(self, path: str) -> list[str]:
        winreg, key = self._open(path)
        names: list[str] = []
        with key:
            index = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, index))
                except OSError:
                    return names
                index += 1

    def values(self, path: str) -> dict[str, Any]:
        winreg, key = self._open(path)
        found: dict[str, Any] = {}
        with key:
            for name in (START, STOP):
                try:
                    found[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    continue
        return found


def default_reader() -> Reader | None:
    """The real reader on Windows; None off Windows and while the tests run (nothing is read)."""
    if sys.platform != "win32" or os.environ.get(TEST_GUARD_ENV) == "1":
        return None
    return WinregReader()


def key_name(path: str) -> str:
    """A program's path as its NonPackaged key is named (each backslash a "#", casefolded:
    "c:#program files#app#app.exe"): the key names are compared as they are, so a "#" in a folder's name
    never makes two paths look alike."""
    return path.replace("/", "\\").replace("\\", "#").casefold()


def own_exes() -> frozenset[str]:
    """Jarvis's own programs as the NonPackaged keys name them (``key_name``): this Python and its python.exe /
    pythonw.exe siblings, for the running one and the base one (a virtual environment's)."""
    found: set[str] = set()
    for exe in {sys.executable, getattr(sys, "_base_executable", "") or ""}:
        if not exe:
            continue
        path = Path(exe)
        found.add(key_name(str(path)))
        for sibling in ("python.exe", "pythonw.exe"):
            found.add(key_name(str(path.with_name(sibling))))
    return frozenset(found)


FILETIME_EPOCH_S = 11_644_473_600          # seconds from 1601-01-01 (FILETIME's start) to 1970-01-01


def real_boot_filetime() -> int:
    """When this PC last started, as a FILETIME (100 ns steps since 1601): now minus GetTickCount64 (a read-only
    call). Refused while the tests run, before ctypes is touched."""
    if os.environ.get(TEST_GUARD_ENV) == "1":
        raise GuardError("Windows is never asked while the tests run")
    import ctypes

    tick = ctypes.windll.kernel32.GetTickCount64
    tick.restype = ctypes.c_ulonglong
    up_ms = int(tick())
    return int((time.time() + FILETIME_EPOCH_S) * 10_000_000) - up_ms * 10_000


def default_boot() -> Callable[[], int] | None:
    """The real boot time on Windows; None off Windows and while the tests run (no entry is left out for age)."""
    if sys.platform != "win32" or os.environ.get(TEST_GUARD_ENV) == "1":
        return None
    return real_boot_filetime


@dataclass(frozen=True)
class Scan:
    in_use: bool
    problems: int = 0          # entries that could not be read (taken as not in use)


def _time(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _in_use(values: dict[str, Any], boot: int | None) -> bool:
    """Start set and stop 0 (or no stop yet); an entry started before the last boot is left over from a crash or a
    power cut, never a call now."""
    start, stop = values.get(START), values.get(STOP, 0)
    if not _time(start) or not _time(stop):
        raise ValueError("not a time")
    if boot is not None and start < boot:
        return False
    return start > 0 and stop == 0


def scan(read: Reader | None, own: Iterable[str] = (), boot: int | None = None) -> Scan:
    """See the module doc. Stops at the first app using the microphone. A key that can't be read counts as not in
    use (a problem); the whole list unreadable is not on a call. ``boot``: the last start of the PC as a FILETIME
    (None: no entry is left out for its age)."""
    if read is None:
        return Scan(False)
    own_set = frozenset(name.casefold() for name in own)
    try:
        names = list(read.subkeys(""))
    except GuardError:
        raise
    except Exception:  # noqa: BLE001 - no list (an older Windows): not on a call
        return Scan(False, 1)
    problems = 0
    paths: list[str] = []
    for name in names:
        if name == NON_PACKAGED:
            try:
                paths.extend(f"{NON_PACKAGED}\\{sub}" for sub in read.subkeys(NON_PACKAGED)
                             if sub.casefold() not in own_set)
            except GuardError:
                raise
            except Exception:  # noqa: BLE001
                problems += 1
        else:
            paths.append(name)
    for path in paths:
        try:
            if _in_use(read.values(path), boot):
                return Scan(True, problems)
        except GuardError:
            raise
        except Exception:  # noqa: BLE001 - malformed: not in use
            problems += 1
    return Scan(False, problems)


def _boot_now(boot: Callable[[], int] | None) -> int | None:
    if boot is None:
        return None
    try:
        return int(boot())
    except GuardError:
        raise
    except Exception:  # noqa: BLE001 - unknown: no entry is left out for its age
        return None


def mic_in_use_by_others(read: Reader | None = None, own: Iterable[str] | None = None,
                         boot: Callable[[], int] | None = None) -> bool:
    """True while another app uses the microphone (see the module doc). ``read`` None takes the real reader and
    ``boot`` None the real boot time (neither off Windows and while the tests run)."""
    reader = read if read is not None else default_reader()
    try:
        found = scan(reader, own_exes() if own is None else own, _boot_now(boot if boot is not None
                                                                            else default_boot()))
    except GuardError:
        logger.warning("Calls: the registry is never read while the tests run")
        return False
    if found.problems:
        logger.warning("Calls: %d microphone entr%s could not be read; taken as not in use", found.problems,
                       "y" if found.problems == 1 else "ies")
    return found.in_use


class CallWatch:
    """On / off changes of "another app uses the microphone" (see the module doc). ``probe()`` -> bool (default
    :func:`mic_in_use_by_others`); ``clock()`` monotonic seconds. Logs a problem count only when it changes."""

    def __init__(self, probe: Callable[[], bool] | None = None, *, clock: Callable[[], float] = time.monotonic,
                 poll_s: float = POLL_S, hold_s: float = HOLD_S) -> None:
        self._probe = probe
        self._clock = clock
        self.poll_s = float(poll_s)
        self.hold_s = float(hold_s)
        self._on = False
        self._since = 0.0
        self._free_at: float | None = None
        self._problems = 0

    @property
    def on_call(self) -> bool:
        return self._on

    def _ask(self) -> bool:
        if self._probe is not None:
            try:
                return bool(self._probe())
            except Exception:  # noqa: BLE001 - unknown: not on a call
                return False
        reader = default_reader()
        try:
            found = scan(reader, own_exes(), _boot_now(default_boot()) if reader is not None else None)
        except GuardError:
            return False
        if found.problems != self._problems:
            self._problems = found.problems
            if found.problems:
                logger.warning("Calls: %d microphone entr%s could not be read; taken as not in use",
                               found.problems, "y" if found.problems == 1 else "ies")
        return found.in_use

    def poll(self) -> str | None:
        now = self._clock()
        busy = self._ask()
        if busy:
            self._free_at = None
            if not self._on:
                self._on, self._since = True, now
                logger.info("Calls: quiet (another app is using the microphone)")
                return ON
            return None
        if not self._on:
            return None
        if self._free_at is None:
            self._free_at = now
        if now - self._free_at < self.hold_s:
            return None
        minutes = max(0, int(round((self._free_at - self._since) / 60)))
        self._on, self._free_at = False, None
        logger.info("Calls: over after %d min", minutes)
        return OFF

    def stop(self) -> str | None:
        """The watch ends (switched off, the app quits): OFF when a call was on."""
        if not self._on:
            return None
        self._on, self._free_at = False, None
        logger.info("Calls: no longer watched")
        return OFF
