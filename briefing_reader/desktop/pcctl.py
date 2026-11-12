"""Controlling the PC: volume, music, Jarvis's own timers and screenshots (SPEC sections 2.1, 2.2, 6.1 and 14;
owner decisions D7 and D8; Phase 2, part 4; no Qt).

Every control here is INSTANT (SPEC 2.1): it acts at once and keeps what it changed (``PcChange``: the state before
and after) so that Undo all (P2.5) can put it back. Every OS call goes through a seam, ``PcBackend``: the app uses
``Win32Pc``; tests use tests/desktop_fakes.FakePc. While the test guard is on (backend.TEST_GUARD_ENV) constructing
``Win32Pc`` raises RealBackendInTests - and so does EVERY public method of it (``_real``; its ``reaches_os``
attribute marks each), before anything else happens.

* Volume and mute of the default output (``Win32Pc.audio``, ``set_level``, ``set_mute``): Core Audio's
  IMMDeviceEnumerator, IMMDevice and IAudioEndpointVolume, written by hand for comtypes (SPEC 14: not pycaw) and
  defined only when first used. ``PcControl.volume`` keeps the level and mute it found (Undo all puts them back) and
  makes at most one change per ``VOLUME_CAP_S`` (300 ms): one sooner is refused (``TOO_SOON``). Set, up and down
  turn the sound back on, as Windows' own volume keys do.
* Music (``media_state``, ``media_control``): the Global System Media Transport Controls through winrt
  (Windows.Media.Control) when it can be imported - the player's state is known, so a pause or a play can be put
  back. winrt is optional (SPEC 14): it is imported only when asked (``gsmtc()``), never at import, and when it is
  missing the media keys are pressed instead (``media_key``: SendInput) - the state is then unknown, and Jarvis says
  plainly that it can't be put back.
* Jarvis's own timers (``TimerBook``): 1 to 240 minutes, a label of at most 40 characters, at most ``MAX_TIMERS`` at
  once; each deadline is kept in a small file in the app's data folder (``timers_folder``: never the real one while
  the tests run), so it is restored when Jarvis starts again; the deadlines are wall-clock times, so a timer whose
  end came while the PC slept ends as soon as it is checked after the resume (desktop_ui checks at least every
  ``TIMER_CHECK_S``). The app says it ended with its toast and a short sound (``Win32Pc.chime``) and shows a HUD chip
  while one runs (``chip_text``). Instant answers' round 2 keeps reminders at a clock time in the same book and file
  (``add_until``, ``Timer.kind`` REMINDER_KIND, up to REMINDER_MAX_S ahead, MAX_REMINDERS at once); an entry with no
  kind is a timer, so every older file still loads, and the desktop's "cancel the timers" leaves reminders alone.
* Screenshots (``capture``): the whole screen or the active window, saved as a PNG (``png_bytes``: written here, no
  image library) to the screenshots folder - [desktop] screenshots_dir, else Jarvis\\Screenshots in the user folder
  (D8); local only: a folder in OneDrive or not on a fixed drive is never used (the default then). Jarvis's own HUD
  windows are left out of the picture: the app hands their window numbers over and the capture makes sure each is
  excluded from capture for the grab (D7; restored after) - if one can't be, nothing is taken. The file is written
  through the desktop backend (``save_png``: never over anything; a taken name gets " (2)"). A screenshot is never
  sent anywhere. Its Undo puts the file in the Recycle Bin if it is unchanged (its sha256), through desktop.fileops'
  Recycler seam (P2.6: desktop.recycle.RecycleBin - every pre-check of the bin first; refused, it says why plainly).
* P2.5, focus mode (``PcControl.focus``; SPEC 2.1, 10.3; owner decision D4): Jarvis's own quiet for 1 to 240 minutes
  (``FocusBook``: one session, kept in a small file next to the timers so a restart of Jarvis stays quiet until its
  end; asked again while it runs, it starts again from now and keeps its id), the music paused - only through the
  player's own controls, whose state is known: with the state unknown nothing is pressed (a blind play/pause key could
  start the music) and Jarvis says he left it - and a louder volume turned down to ``FOCUS_VOLUME``. What focus changed
  on this PC is kept as its own changes (``PcChange.group``: the session's id), so the HUD's chips and Undo put them
  back. Windows' Do Not Disturb is the owner's switch: Jarvis only opens its settings page (``open_dnd``: the one
  fixed call, guard.DND_OPENER_URI) and reads it back, read-only (``dnd_state``: DND_ON only when Windows says so -
  a Windows focus session, else SHQueryUserNotificationState's quiet time; DND_UNKNOWN when neither can be read).
* P2.5, Undo for the HUD's chips (``undo_changes``): changes put back newest first, each checked against the PC as it
  is now ("changed since - left as it is"); a run of volume changes is put back with ONE change of the level (the
  300 ms cap would refuse a second one at once), a run of the player's with one call; ``undo_summary`` says what came
  back and, plainly, what did not.

``PcControl`` (the desktop thread's: desktop.service owns it) carries out a ``PcAsk`` (a local route's, from
desktop.routes) and returns a ``PcChange``; ``change_words`` is Jarvis's own sentence for it (the HUD's caption:
British and short - "Volume 30, sir.", "Paused, sir.", "Timer set for 10 minutes, sir."), ``live_note`` its line in
Live (levels, states, counts and kinds only). Logs carry kinds, actions, levels as numbers, states, counts and codes
only - never a label, a path or a window's title.

Public API
==========
``PcBackend`` (Protocol): ``audio()``, ``set_level(level)``, ``set_mute(muted)``, ``media_state()``,
``media_control(action)``, ``media_key(action)``, ``capture(mode, hud=())``, ``chime()``, ``dnd_state()``,
``open_notifications()``. P2.5: ``FocusSession``, ``FocusDone``, ``FocusBook(folder, now=)`` (``load()``,
``active()``, ``start(minutes)``, ``end(focus_id="")``, ``take_due()``, ``seconds_left()``, ``clear()``);
``PcControl(focus=)`` ``.focus(minutes)``, ``.undo_changes(changes)``, ``.dnd_state()``, ``.open_dnd()``;
``focus_words``, ``focus_end_words``, ``chip_words(kind, changes)``, ``focus_chip_text``, ``dnd_chip_text``,
``undo_summary(results)``, ``undo_note``; ``INSTANT_KINDS``, ``FOCUS_VOLUME``, ``DND_ON``/``DND_OFF``/``DND_UNKNOWN``.
``Win32Pc()``;
``gsmtc()``; ``png_bytes(width, height, bgra)``. ``AudioState(level, muted)``, ``PcAsk(kind, action, level, step,
minutes, label)``, ``Timer``, ``Shot``, ``PcChange``; ``NoDevice``, ``NoWindow``, ``HudShows``,
``MediaUnavailable``, ``TimersFull``, ``RemindersFull``. ``TimerBook(folder, now=)`` (``load()``, ``running(kind=)``,
``add(minutes, label)``, ``add_seconds(seconds, label)``, ``add_until(ends_at, label)``, ``cancel_all(kind=)``,
``restore(timers)``, ``next_end()``, ``seconds_left()``, ``take_due()``, ``clear()``);
``timers_folder(
data_dir)``; ``clean_label(text)``. ``PcControl(backend_factory, disk=, timers=, screenshots_dir=, clock=, now=,
recycler=)`` (``carry_out(ask, cancel=, hud=)``, ``volume``, ``media``, ``timer``, ``cancel_timers``,
``screenshot``, ``shots_folder()``, ``changes()``, ``undo(change)``, ``chime()``). ``change_words(change,
address=)``, ``undo_words(change, address=)``, ``end_words(timer, late_s, address=, hour24=)``, ``chip_text(timers,
hour24=)``, ``live_note(change)``, ``code_words(code)``, ``shot_name(moment, number=)``. Constants: ``PC_SKILLS``,
``VOLUME_STEP``, ``VOLUME_CAP_S``, ``TIMER_MINUTES``, ``LABEL_MAX``, ``MAX_TIMERS``, ``TIMER_CHECK_S``,
``TIMER_LATE_S``, ``SCREEN``, ``ACTIVE_WINDOW``, the media states and the codes.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import ntpath
import os
import re
import struct
import threading
import time
import uuid
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, TypeVar

from ..persona import clock_text, with_address
from . import guard, paths
from . import validate as dv
from .backend import DRIVE_FIXED, PNG_SIGNATURE, RealBackendInTests, cloud_tag, link_tag, real_backend_blocked

logger = logging.getLogger(__name__)

PC_SKILLS = frozenset({dv.VOLUME, dv.MEDIA, dv.TIMER, dv.CANCEL_TIMER, dv.SCREENSHOT, dv.FOCUS_MODE})
FOCUS_VOLUME = 20                    # focus mode turns a louder volume down to this (SPEC 10.3's "VOLUME 20%")
FOCUS_FILE = "focus.json"
DND = "dnd"                          # Windows' Do Not Disturb: its settings page and its read-back (no skill)
DND_ON, DND_OFF, DND_UNKNOWN = "on", "off", "unknown"
_QUNS_ACCEPTS_NOTIFICATIONS, _QUNS_QUIET_TIME = 5, 6     # SHQueryUserNotificationState's answers read here
# The HUD's chips of instant changes (P2.5), one per kind, in this order; a timer set and timers cancelled share one.
CHIP_FOCUS, CHIP_MUSIC, CHIP_VOLUME, CHIP_TIMERS, CHIP_SHOTS = "focus", "music", "volume", "timers", "shots"
INSTANT_KINDS = (CHIP_FOCUS, CHIP_MUSIC, CHIP_VOLUME, CHIP_TIMERS, CHIP_SHOTS)
_CHIP_OF = {dv.FOCUS_MODE: CHIP_FOCUS, dv.MEDIA: CHIP_MUSIC, dv.VOLUME: CHIP_VOLUME, dv.TIMER: CHIP_TIMERS,
            dv.CANCEL_TIMER: CHIP_TIMERS, dv.SCREENSHOT: CHIP_SHOTS}
VOLUME_STEP = 10                     # "louder", "volume up": this many points (the planner's step: 5-30)
VOLUME_CAP_S = 0.3                   # at most one volume change per 300 ms (SPEC 2.1)
TIMER_MINUTES = (1, 240)
TIMER_SECONDS = (10, 240 * 60)       # instant answers' timers (add_seconds): 10 seconds to 4 hours
LABEL_MAX = 40
MAX_TIMERS = 5
# Round 2 of instant answers: a reminder at a clock time (add_until) shares the book and its file, up to 48 hours
# ahead and at most MAX_REMINDERS at once (its own count: timers keep their five).
TIMER_KIND, REMINDER_KIND = "timer", "reminder"
REMINDER_MAX_S = 48 * 3600
REMINDER_LATE_S = 48 * 3600          # a reminder missed while Jarvis was closed is still said this long after its time
MAX_REMINDERS = 10
TIMER_CHECK_S = 30.0                 # the app checks the timers at least this often (a resume is caught by then)
TIMER_LATE_S = 12 * 3600             # an end missed by more than this (Jarvis was closed) is dropped quietly
LATE_WORDS_S = 120                   # an end noticed later than this says when it was ("ended at 10:32 AM")
MEDIA_TIMEOUT_S = 4.0                # the player is waited for this long at most
TIMERS_FILE = "timers.json"
SHOTS_FOLDER = ("Jarvis", "Screenshots")    # under the user folder (D8)
SCREEN, ACTIVE_WINDOW = "screen", "active_window"
SHOT_MODES = (SCREEN, ACTIVE_WINDOW)
SET, CANCEL, UNDO = "set", "cancel", "undo"
MAX_NAME_TRIES = 50
MAX_PIXELS = 80_000_000             # a capture larger than this is refused (bytes held in memory)

# The player's state (GSMTC's playback status), or UNKNOWN when it can't be asked (winrt missing: the media keys).
PLAYING, PAUSED, STOPPED, NO_SESSION, UNKNOWN = "playing", "paused", "stopped", "none", "unknown"
VIA_PLAYER, VIA_KEYS = "player", "keys"
# The media keys (VK_MEDIA_*): play and pause are one key, which is why its state can't be known.
MEDIA_KEYS = {"play": 0xB3, "pause": 0xB3, "next": 0xB0, "previous": 0xB1}

# Codes (desktop.executor maps them to its statuses: OS_ERROR failed, STOPPED cancelled, any other refused).
OS_ERROR = "os_error"                # the same word as desktop.executor.OS_FAILED
STOPPED = "cancelled"                # the same word as desktop.executor.STOPPED
UNAVAILABLE = "pc_unavailable"       # the PC's controls can't be reached (no seam)
TOO_SOON = "too_soon"                # the volume's rate cap
NO_DEVICE = "no_device"              # Windows has no default output
NO_PLAYER = "no_player"              # the player knows of nothing playing
NOT_TAKEN = "not_taken"              # the player did not take it
NO_TIMER = "no_timer"
TIMERS_FULL = "timers_full"
NO_WINDOW = "no_window"              # no active window of another app to capture
HUD_SHOWS = "hud_shows"              # Jarvis's own HUD could not be left out of the picture: nothing taken
NO_FOLDER = "no_folder"              # the screenshots folder can't be used
CHANGED = "changed"                  # Undo: it changed since - left as it is
CANT_UNDO = "cant_undo"              # Undo: this can't be put back
GONE = "gone"                        # Undo: the file or the timer is gone already
CODE_WORDS = {
    UNAVAILABLE: "this PC's controls couldn't be reached",
    TOO_SOON: "it came too soon after the last volume change",
    NO_DEVICE: "Windows has no speakers set up",
    NO_PLAYER: "nothing was playing",
    NOT_TAKEN: "the player didn't take it",
    NO_TIMER: "no timer was running",
    TIMERS_FULL: f"{MAX_TIMERS} timers were running already",
    NO_WINDOW: "there was no window to capture",
    HUD_SHOWS: "Jarvis couldn't be kept out of the picture",
    NO_FOLDER: "the screenshots folder can't be used",
    CHANGED: "it changed since - left as it is",
    CANT_UNDO: "it can't be put back",
    GONE: "it's gone already",
}


def code_words(code: str) -> str:
    return CODE_WORDS.get(code, "")


class NoDevice(OSError):
    """Windows has no default output device."""


class NoWindow(OSError):
    """No active window of another app to capture."""


class HudShows(OSError):
    """A HUD window could not be left out of capture: nothing was taken."""


class MediaUnavailable(RuntimeError):
    """The player's controls (GSMTC) can't be used: winrt is not installed."""


class TimersFull(RuntimeError):
    """MAX_TIMERS timers run already."""


class RemindersFull(TimersFull):
    """MAX_REMINDERS reminders are set already."""


# --------------------------------------------------------------------------
# What the controls report
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AudioState:
    """The default output's level (0-100) and whether it is muted."""

    level: int
    muted: bool


@dataclass(frozen=True)
class PcAsk:
    """A PC control asked for (a local route's, desktop.routes): its skill, action ("set", "up", "down", "mute",
    "unmute"; "play", "pause", "next", "previous"; SCREEN, ACTIVE_WINDOW; SET, CANCEL), a volume's level or step, a
    timer's minutes and label (the owner's own words: never logged). Memory only."""

    kind: str
    action: str = ""
    level: int | None = None
    step: int | None = None
    minutes: int | None = None
    label: str = field(default="", repr=False)

    def __repr__(self) -> str:
        return f"PcAsk({self.kind}, {self.action})"


@dataclass(frozen=True)
class Timer:
    """One of Jarvis's timers: its id, minutes, when it was set and when it ends (wall clock), its label. ``kind``
    REMINDER_KIND: a reminder at a clock time (round 2 of instant answers; ``ends_at`` is the time asked for)."""

    timer_id: str
    minutes: int
    set_at: datetime
    ends_at: datetime
    label: str = field(default="", repr=False)
    seconds: int = 0           # instant answers' timers: the length in seconds (0: ``minutes`` whole minutes)
    kind: str = TIMER_KIND

    def __repr__(self) -> str:
        if self.kind == REMINDER_KIND:
            return f"Timer({self.timer_id}, reminder)"
        return f"Timer({self.timer_id}, {self.minutes} min)"

    @property
    def reminder(self) -> bool:
        return self.kind == REMINDER_KIND

    @property
    def length_s(self) -> int:
        """How long the timer runs, in seconds."""
        return self.seconds or self.minutes * 60


@dataclass(frozen=True)
class Shot:
    """A screenshot saved: its file (never logged), sha256 and size, and what was captured."""

    path: str = field(repr=False)
    sha256: str = field(repr=False)
    size: int = 0
    mode: str = SCREEN


@dataclass(frozen=True)
class PcChange:
    """What came of a PC control: its skill and action; ``code`` "" when it was done, else why not (a refusal,
    OS_ERROR or STOPPED); ``changed`` whether anything changed on this PC (False: it was so already, or not done);
    ``undoable`` whether Undo all can put it back; the state before and after (AudioState; a player's state; a Timer
    or the timers cancelled; a Shot); ``via`` how the music was reached (VIA_PLAYER, VIA_KEYS); a winerror; ``group``
    the focus session's id when focus mode made it (P2.5: its Undo puts it back with focus). Memory only; its repr
    names the kind, the action and the code."""

    kind: str
    action: str = ""
    code: str = ""
    changed: bool = False
    undoable: bool = False
    before: Any = field(default=None, repr=False)
    after: Any = field(default=None, repr=False)
    via: str = ""
    winerror: int = 0
    group: str = ""

    def __repr__(self) -> str:
        return f"PcChange({self.kind}, {self.action}{', ' + self.code if self.code else ''})"


@dataclass(frozen=True)
class FocusSession:
    """Focus mode (P2.5): its id (kept when it starts again while it runs), its minutes, when it was set and when it
    ends (wall clock)."""

    focus_id: str
    minutes: int
    set_at: datetime
    ends_at: datetime

    def __repr__(self) -> str:
        return f"FocusSession({self.focus_id}, {self.minutes} min)"


@dataclass(frozen=True)
class FocusDone:
    """What focus mode did as it started: its session, what it changed on this PC (``changes``: the music paused, the
    volume turned down - each a PcChange of its own with the session's id as its group), how the music was found
    (PAUSED: paused now; UNKNOWN: the player can't be asked, so it was left alone; "": nothing to pause) and whether
    it started again while it ran (``restarted``). Memory only."""

    session: FocusSession
    changes: tuple[PcChange, ...] = ()
    music: str = ""
    restarted: bool = False


# --------------------------------------------------------------------------
# The seam and the real one
# --------------------------------------------------------------------------

_F = TypeVar("_F", bound=Callable[..., Any])


def _real(method: _F) -> _F:
    """An OS-touching method of Win32Pc: it raises RealBackendInTests while the test guard is on, before anything
    else happens (``reaches_os`` marks it; the tests check every public method has it)."""
    @functools.wraps(method)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        if real_backend_blocked():
            raise RealBackendInTests(f"Win32Pc.{method.__name__} never runs while the tests run")
        return method(self, *args, **kwargs)

    guarded.reaches_os = True                     # type: ignore[attr-defined]
    return guarded                                # type: ignore[return-value]


class PcBackend(Protocol):
    def audio(self) -> AudioState:
        """The default output's level and mute (NoDevice when Windows has none)."""
        ...

    def set_level(self, level: int) -> None:
        """The default output's level (0-100)."""
        ...

    def set_mute(self, muted: bool) -> None:
        """Mute or unmute the default output."""
        ...

    def media_state(self) -> str:
        """The current player's state (PLAYING, PAUSED, STOPPED, NO_SESSION), or UNKNOWN when it can't be asked."""
        ...

    def media_control(self, action: str) -> bool:
        """Ask the current player to play, pause or skip (GSMTC); False when it did not take it, MediaUnavailable
        when the player's controls can't be used."""
        ...

    def media_key(self, action: str) -> None:
        """Press the media key for ``action`` (play and pause are one key)."""
        ...

    def capture(self, mode: str, hud: Sequence[int] = ()) -> bytes:
        """The screen or the active window as a PNG, the windows ``hud`` left out (HudShows when one can't be;
        NoWindow when there is no window to capture)."""
        ...

    def chime(self) -> None:
        """A short sound (a timer's end)."""
        ...

    def dnd_state(self) -> str:
        """Windows' Do Not Disturb, read only: DND_ON, DND_OFF or DND_UNKNOWN (P2.5)."""
        ...

    def open_notifications(self) -> None:
        """Open Windows' notification settings (guard.DND_OPENER_URI, the one fixed call) for the owner's own switch."""
        ...


class Win32Pc:
    """The real PC controls (Windows); see the module docstring. Every public method raises RealBackendInTests while
    the test guard is on."""

    def __init__(self) -> None:
        if real_backend_blocked():
            raise RealBackendInTests("the real PC controls are never constructed while the tests run")

    @_real
    def audio(self) -> AudioState:
        endpoint = _endpoint()
        return AudioState(_level(endpoint.GetMasterVolumeLevelScalar()), bool(endpoint.GetMute()))

    @_real
    def set_level(self, level: int) -> None:
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
            raise ValueError("refused by the last guard")
        _endpoint().SetMasterVolumeLevelScalar(level / 100.0, None)

    @_real
    def set_mute(self, muted: bool) -> None:
        _endpoint().SetMute(1 if muted else 0, None)

    @_real
    def media_state(self) -> str:
        module = gsmtc()
        if module is None:
            return UNKNOWN
        return str(_gsmtc_run(module, _gsmtc_state))

    @_real
    def media_control(self, action: str) -> bool:
        if action not in dv.MEDIA_ACTIONS:
            raise ValueError("refused by the last guard")
        module = gsmtc()
        if module is None:
            raise MediaUnavailable("the player's controls can't be used")
        return bool(_gsmtc_run(module, functools.partial(_gsmtc_action, action=action)))

    @_real
    def media_key(self, action: str) -> None:
        if action not in MEDIA_KEYS:
            raise ValueError("refused by the last guard")
        _press_media_key(MEDIA_KEYS[action])

    @_real
    def capture(self, mode: str, hud: Sequence[int] = ()) -> bytes:
        if mode not in SHOT_MODES:
            raise ValueError("refused by the last guard")
        return _grab(mode, tuple(int(hwnd) for hwnd in hud if int(hwnd)))

    @_real
    def chime(self) -> None:
        _beep()

    @_real
    def dnd_state(self) -> str:
        return _dnd_read()

    @_real
    def open_notifications(self) -> None:
        from .backend import _shell_open

        _shell_open(guard.DND_OPENER_URI)             # the one fixed page: never anything a plan or a name gives


def _level(scalar: Any) -> int:
    return max(0, min(100, int(round(float(scalar) * 100))))


# ---- volume: Core Audio through comtypes (SPEC 14: hand-written, not pycaw) -------------------------------------

_E_RENDER, _E_MULTIMEDIA = 0, 1
_CLSCTX_INPROC_SERVER, _CLSCTX_ALL = 0x1, 0x17
_E_NOTFOUND = -2147023728            # HRESULT_FROM_WIN32(ERROR_NOT_FOUND): no default output


@functools.lru_cache(maxsize=1)
def _audio_types() -> Any:
    """IMMDeviceEnumerator, IMMDevice and IAudioEndpointVolume for comtypes (only the vtable slots up to the methods
    used, in their order), and MMDeviceEnumerator's class id - defined at the first use, never at import."""
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    guid = ctypes.POINTER(GUID)
    level = ctypes.c_float

    class IAudioEndpointVolume(IUnknown):
        _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
        _methods_ = [
            COMMETHOD([], HRESULT, "RegisterControlChangeNotify", (["in"], ctypes.c_void_p, "pNotify")),
            COMMETHOD([], HRESULT, "UnregisterControlChangeNotify", (["in"], ctypes.c_void_p, "pNotify")),
            COMMETHOD([], HRESULT, "GetChannelCount", (["out"], ctypes.POINTER(wintypes.UINT), "pnChannelCount")),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevel", (["in"], level, "fLevelDB"), (["in"], guid, "pguidCtx")),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar", (["in"], level, "fLevel"),
                      (["in"], guid, "pguidCtx")),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevel", (["out"], ctypes.POINTER(level), "pfLevelDB")),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar", (["out"], ctypes.POINTER(level), "pfLevel")),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevel", (["in"], wintypes.UINT, "nChannel"),
                      (["in"], level, "fLevelDB"), (["in"], guid, "pguidCtx")),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar", (["in"], wintypes.UINT, "nChannel"),
                      (["in"], level, "fLevel"), (["in"], guid, "pguidCtx")),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevel", (["in"], wintypes.UINT, "nChannel"),
                      (["out"], ctypes.POINTER(level), "pfLevelDB")),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar", (["in"], wintypes.UINT, "nChannel"),
                      (["out"], ctypes.POINTER(level), "pfLevel")),
            COMMETHOD([], HRESULT, "SetMute", (["in"], wintypes.BOOL, "bMute"), (["in"], guid, "pguidCtx")),
            COMMETHOD([], HRESULT, "GetMute", (["out"], ctypes.POINTER(wintypes.BOOL), "pbMute")),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], guid, "iid"), (["in"], wintypes.DWORD, "dwClsCtx"),
                      (["in"], ctypes.c_void_p, "pActivationParams"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "ppInterface")),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], ctypes.c_int, "dataFlow"),
                      (["in"], wintypes.DWORD, "dwStateMask"), (["out"], ctypes.POINTER(ctypes.c_void_p), "ppDevices")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], ctypes.c_int, "dataFlow"),
                      (["in"], ctypes.c_int, "role"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)), "ppEndpoint")),
        ]

    return SimpleNamespace(comtypes=comtypes, volume=IAudioEndpointVolume, device=IMMDevice,
                           enumerator=IMMDeviceEnumerator, clsid=GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"))


def _endpoint() -> Any:
    """The default output's IAudioEndpointVolume (render, multimedia), made on this thread (COM set up first)."""
    from .backend import _com_ready

    _com_ready()
    types = _audio_types()
    enumerator = types.comtypes.CoCreateInstance(types.clsid, types.enumerator, _CLSCTX_INPROC_SERVER)
    try:
        device = enumerator.GetDefaultAudioEndpoint(_E_RENDER, _E_MULTIMEDIA)
    except Exception as exc:  # noqa: BLE001 - COMError: no default output, or another failure
        if int(getattr(exc, "hresult", 0) or 0) == _E_NOTFOUND:
            raise NoDevice(0, "no default output") from None
        raise
    unknown = device.Activate(types.volume._iid_, _CLSCTX_ALL, None)
    return unknown.QueryInterface(types.volume)


# ---- music: GSMTC through winrt (optional), else the media keys ---------------------------------------------------

_GSMTC_MODULE = "winrt.windows.media.control"
_GSMTC_STATES = {4: PLAYING, 5: PAUSED}      # GlobalSystemMediaTransportControlsSessionPlaybackStatus


def gsmtc() -> Any:
    """winrt's Windows.Media.Control (the player's controls), or None when it can't be imported - winrt is optional
    (SPEC 14): the media keys are pressed instead. Imported only when asked, never when this module is."""
    import importlib

    try:
        return importlib.import_module(_GSMTC_MODULE)
    except Exception:  # noqa: BLE001 - ImportError, or a broken install: the media keys then
        return None


def _gsmtc_run(module: Any, work: Callable[[Any], Any], timeout: float = MEDIA_TIMEOUT_S) -> Any:
    """``work(module)`` (a coroutine function) on a short-lived thread of its own - its own event loop and apartment,
    never the desktop thread's - waited for ``timeout`` at most (TimeoutError after)."""
    box: dict[str, Any] = {}

    def run() -> None:
        import asyncio

        try:
            box["value"] = asyncio.run(asyncio.wait_for(work(module), timeout))
        except BaseException as exc:  # noqa: BLE001 - handed to the caller
            box["error"] = exc

    thread = threading.Thread(target=run, name="desktop-media", daemon=True)
    thread.start()
    thread.join(timeout + 1.0)
    if thread.is_alive():
        raise TimeoutError("the player did not answer")
    if "error" in box:
        raise box["error"]
    return box.get("value")


async def _gsmtc_session(module: Any) -> Any:
    manager = await module.GlobalSystemMediaTransportControlsSessionManager.request_async()
    return manager.get_current_session()


async def _gsmtc_state(module: Any) -> str:
    session = await _gsmtc_session(module)
    if session is None:
        return NO_SESSION
    return _GSMTC_STATES.get(int(session.get_playback_info().playback_status), STOPPED)


async def _gsmtc_action(module: Any, *, action: str) -> bool:
    session = await _gsmtc_session(module)
    if session is None:
        return False
    call = {"play": session.try_play_async, "pause": session.try_pause_async,
            "next": session.try_skip_next_async, "previous": session.try_skip_previous_async}[action]
    return bool(await call())


def _press_media_key(vk: int) -> None:
    """The media key ``vk`` pressed and let go (SendInput; an extended key)."""
    import ctypes
    from ctypes import wintypes

    class KeyInput(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MouseInput(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class Either(ctypes.Union):
        _fields_ = [("mi", MouseInput), ("ki", KeyInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", Either)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    extended, up = 0x0001, 0x0002                       # KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP
    presses = (Input * 2)()
    for number, flags in enumerate((extended, extended | up)):
        presses[number].type = 1                        # INPUT_KEYBOARD
        presses[number].u.ki = KeyInput(vk, 0, flags, 0, 0)
    if user32.SendInput(2, presses, ctypes.sizeof(Input)) != 2:
        raise ctypes.WinError(ctypes.get_last_error())


def _beep() -> None:
    """A short system sound, played without waiting (MessageBeep: the Windows sound for an information message)."""
    import winsound

    winsound.MessageBeep(winsound.MB_ICONASTERISK)


# ---- Do Not Disturb, read only (P2.5; D4: Windows gives apps no supported way to switch it) ----------------------

_FOCUS_SHELL_MODULE = "winrt.windows.ui.shell"


def _dnd_read() -> str:
    """Windows' Do Not Disturb as far as it can be READ (nothing is ever written): a Windows focus session through
    winrt's FocusSessionManager.IsFocusActive when that module can be imported and answers (Windows turns Do Not
    Disturb on for a focus session), else SHQueryUserNotificationState (its quiet time: on; notifications accepted:
    off). DND_UNKNOWN when neither says - the HUD's chip then never says "On"."""
    import importlib

    try:
        module = importlib.import_module(_FOCUS_SHELL_MODULE)
        manager_type = getattr(module, "FocusSessionManager", None)
        if manager_type is not None and bool(manager_type.is_supported):
            manager = manager_type.get_default()
            if manager is not None and bool(manager.is_focus_active):
                return DND_ON
    except Exception as exc:  # noqa: BLE001 - not installed, not supported or refused: the next way
        logger.debug("Desktop pc: no focus session read-back (%s)", type(exc).__name__)
    try:
        import ctypes

        state = ctypes.c_int(0)
        shell32 = ctypes.WinDLL("shell32")
        if shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:       # S_OK
            if state.value == _QUNS_QUIET_TIME:
                return DND_ON
            if state.value == _QUNS_ACCEPTS_NOTIFICATIONS:
                return DND_OFF
    except Exception as exc:  # noqa: BLE001 - can't be read: unknown
        logger.debug("Desktop pc: no notification state read-back (%s)", type(exc).__name__)
    return DND_UNKNOWN


# ---- screenshots: GDI, the HUD left out (D7), written as a PNG here ----------------------------------------------

_SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 76, 77, 78, 79
_SRCCOPY, _CAPTUREBLT = 0x00CC0020, 0x40000000
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_WDA_EXCLUDEFROMCAPTURE = 0x11


@functools.lru_cache(maxsize=1)
def _gdi() -> Any:
    """user32's, gdi32's and dwmapi's functions the capture uses, with their prototypes (made once)."""
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    handle, hwnd, dword, boolean, integer = (wintypes.HANDLE, wintypes.HWND, wintypes.DWORD, wintypes.BOOL,
                                             ctypes.c_int)

    def proto(function: Any, args: list[Any], result: Any) -> None:
        function.argtypes, function.restype = args, result

    proto(user32.GetDC, [hwnd], handle)
    proto(user32.ReleaseDC, [hwnd, handle], integer)
    proto(user32.GetSystemMetrics, [integer], integer)
    proto(user32.GetForegroundWindow, [], hwnd)
    proto(user32.GetWindowThreadProcessId, [hwnd, ctypes.POINTER(dword)], dword)
    proto(user32.IsWindow, [hwnd], boolean)
    proto(user32.IsWindowVisible, [hwnd], boolean)
    proto(user32.IsIconic, [hwnd], boolean)
    proto(user32.GetWindowRect, [hwnd, ctypes.POINTER(wintypes.RECT)], boolean)
    proto(user32.GetWindowDisplayAffinity, [hwnd, ctypes.POINTER(dword)], boolean)
    proto(user32.SetWindowDisplayAffinity, [hwnd, dword], boolean)
    proto(dwmapi.DwmGetWindowAttribute, [hwnd, dword, ctypes.c_void_p, dword], ctypes.c_long)
    proto(dwmapi.DwmFlush, [], ctypes.c_long)
    proto(gdi32.CreateCompatibleDC, [handle], handle)
    proto(gdi32.CreateCompatibleBitmap, [handle, integer, integer], handle)
    proto(gdi32.SelectObject, [handle, handle], handle)
    proto(gdi32.BitBlt, [handle, integer, integer, integer, integer, handle, integer, integer, dword], boolean)
    proto(gdi32.GetDIBits, [handle, handle, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.UINT], integer)
    proto(gdi32.DeleteObject, [handle], boolean)
    proto(gdi32.DeleteDC, [handle], boolean)

    class Header(ctypes.Structure):
        _fields_ = [("biSize", dword), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", dword),
                    ("biSizeImage", dword), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", dword), ("biClrImportant", dword)]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", Header), ("bmiColors", dword * 3)]

    return SimpleNamespace(ctypes=ctypes, wintypes=wintypes, user32=user32, gdi32=gdi32, dwmapi=dwmapi,
                           Header=Header, BitmapInfo=BitmapInfo)


def _grab(mode: str, hud: tuple[int, ...]) -> bytes:
    """The screen (every monitor) or the active window as on the screen, as a PNG: the HUD's windows are excluded
    from capture for the grab (those not excluded yet are, and are put back after; one that can't be: HudShows)."""
    win = _gdi()
    left, top, width, height = _shot_rect(win, mode)
    held = _hold_out(win, hud)
    try:
        if held:
            win.dwmapi.DwmFlush()                       # the change is composed before the grab
        bgra = _bits(win, left, top, width, height)
    finally:
        for hwnd, affinity in reversed(held):
            win.user32.SetWindowDisplayAffinity(hwnd, affinity)
    return png_bytes(width, height, bgra)


def _shot_rect(win: Any, mode: str) -> tuple[int, int, int, int]:
    """(left, top, width, height) in screen pixels: the virtual screen, or the active window's frame on it."""
    user32, ctypes = win.user32, win.ctypes
    left, top = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN), user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
    right = left + user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
    bottom = top + user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
    if mode == ACTIVE_WINDOW:
        hwnd = user32.GetForegroundWindow()
        pid = win.wintypes.DWORD(0)
        if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            raise NoWindow(0, "no active window")
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) == os.getpid():
            raise NoWindow(0, "the active window is Jarvis's own")
        rect = win.wintypes.RECT()
        if win.dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
                                            ctypes.sizeof(rect)) != 0 and not user32.GetWindowRect(hwnd,
                                                                                                  ctypes.byref(rect)):
            raise NoWindow(0, "the active window has no frame")
        left, top = max(left, rect.left), max(top, rect.top)
        right, bottom = min(right, rect.right), min(bottom, rect.bottom)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise NoWindow(0, "nothing to capture")
    if width * height > MAX_PIXELS:
        raise OSError(0, "too large to capture")
    return left, top, width, height


def _hold_out(win: Any, hud: tuple[int, ...]) -> list[tuple[int, int]]:
    """Each shown HUD window excluded from capture: (window, its affinity before) for those changed here."""
    user32, ctypes = win.user32, win.ctypes
    held: list[tuple[int, int]] = []
    try:
        for hwnd in hud:
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
                continue
            affinity = win.wintypes.DWORD(0)
            if not user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity)):
                raise HudShows(0, "a HUD window's capture setting can't be read")
            if affinity.value == _WDA_EXCLUDEFROMCAPTURE:
                continue
            if not user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE):
                raise HudShows(0, "a HUD window can't be left out of capture")
            held.append((hwnd, int(affinity.value)))
    except BaseException:
        for hwnd, affinity in reversed(held):
            user32.SetWindowDisplayAffinity(hwnd, affinity)
        raise
    return held


def _bits(win: Any, left: int, top: int, width: int, height: int) -> bytes:
    """The screen's pixels in the rectangle, top-down BGRA (BitBlt with CAPTUREBLT, then GetDIBits)."""
    user32, gdi32, ctypes = win.user32, win.gdi32, win.ctypes
    screen = user32.GetDC(None)
    if not screen:
        raise ctypes.WinError(ctypes.get_last_error())
    memory = bitmap = None
    try:
        memory = gdi32.CreateCompatibleDC(screen)
        bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
        if not memory or not bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        old = gdi32.SelectObject(memory, bitmap)
        done = gdi32.BitBlt(memory, 0, 0, width, height, screen, left, top, _SRCCOPY | _CAPTUREBLT)
        gdi32.SelectObject(memory, old)                 # GetDIBits wants the bitmap out of the DC
        if not done:
            raise ctypes.WinError(ctypes.get_last_error())
        info = win.BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(win.Header)
        info.bmiHeader.biWidth, info.bmiHeader.biHeight = width, -height      # top-down
        info.bmiHeader.biPlanes, info.bmiHeader.biBitCount = 1, 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        if gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(info), 0) != height:
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.raw
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory:
            gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)


def png_bytes(width: int, height: int, bgra: bytes, *, level: int = 6) -> bytes:
    """A PNG (8-bit RGB, no filter, zlib) of ``width`` x ``height`` top-down BGRA pixels (the 4th byte ignored)."""
    width, height = int(width), int(height)
    count = width * height
    if width <= 0 or height <= 0 or len(bgra) < count * 4:
        raise ValueError("not a picture")
    pixels = bytes(bgra[:count * 4])
    rgb = bytearray(count * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = pixels[2::4], pixels[1::4], pixels[0::4]
    stride = width * 3
    raw = bytearray((stride + 1) * height)           # each row: filter 0, then its pixels
    for row in range(height):
        start = row * (stride + 1) + 1
        raw[start:start + stride] = rgb[row * stride:(row + 1) * stride]

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw), level)) + \
        chunk(b"IEND", b"")


# --------------------------------------------------------------------------
# Jarvis's own timers
# --------------------------------------------------------------------------

_LABEL_LEAD = re.compile(r"^(?:to|about|for|that)\s+", re.IGNORECASE)


def clean_label(text: Any) -> str:
    """A timer's label as kept: the owner's words with hidden characters dropped, spaces collapsed, a leading "to" /
    "about" / "for" / "that" left out ("remind me in 5 minutes to stretch": "stretch"), at most LABEL_MAX
    characters."""
    words = " ".join(guard.strip_hidden(str(text or "")).split())
    words = _LABEL_LEAD.sub("", words).strip(" .!?")
    return words[:LABEL_MAX].rstrip()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _like(moment: datetime, reference: datetime) -> datetime:
    """``moment`` as aware or naive as ``reference`` (a file written by another clock never breaks a comparison)."""
    if (moment.tzinfo is None) == (reference.tzinfo is None):
        return moment
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _local(moment: datetime) -> datetime:
    return moment.astimezone() if moment.tzinfo is not None else moment


def timers_folder(data_dir: Any) -> Path | None:
    """Where the timers are kept: the app's data folder's "desktop" folder, or None without a data folder. Never the
    app's real data folder while the tests run (RealBackendInTests): they pass a temporary one."""
    if not data_dir:
        return None
    folder = Path(data_dir) / "desktop"
    if real_backend_blocked():
        from ..config import default_data_dir         # late: config imports the desktop backend

        real = os.path.normcase(os.path.abspath(str(default_data_dir())))
        mine = os.path.normcase(os.path.abspath(str(folder)))
        if mine == real or mine.startswith(real + os.sep):
            raise RealBackendInTests("the app's real data folder is never used while the tests run")
    return folder


class TimerBook:
    """Jarvis's running timers (see the module docstring): in memory, and in ``folder``/timers.json so that they are
    restored when Jarvis starts again (``load``). Safe to use from the desktop thread and the GUI thread. ``saved``
    is False after a save failed (the timers then live in memory only). ``now()``: the wall clock. Round 2 of instant
    answers: reminders at a clock time (``add_until``; ``kind`` REMINDER_KIND) share the book and its file, with their
    own count (``max_reminders``); ``running(kind)`` and ``cancel_all(kind)`` take one kind."""

    def __init__(self, folder: str | Path | None, *, now: Callable[[], datetime] | None = None,
                 max_timers: int = MAX_TIMERS, max_reminders: int = MAX_REMINDERS) -> None:
        self.folder = Path(folder) if folder else None
        self._now = now or _utc_now
        self._max = int(max_timers)
        self._max_reminders = int(max_reminders)
        self._lock = threading.Lock()
        self._timers: list[Timer] = []
        self.saved = True

    def now(self) -> datetime:
        return self._now()

    def load(self) -> int:
        """The timers kept in the file (those of a good shape; the rest dropped), replacing those in memory; how
        many there are."""
        loaded: list[Timer] = []
        path = self.folder / TIMERS_FILE if self.folder is not None else None
        try:
            if path is not None and path.is_file():
                entries = json.loads(path.read_text(encoding="utf-8")).get("timers", [])
                loaded = [timer for timer in (_timer_from(entry) for entry in entries) if timer is not None]
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            logger.warning("Desktop pc: the saved timers could not be read (%s)", type(exc).__name__)
            loaded = []
        timers = [timer for timer in loaded if timer.kind == TIMER_KIND][:self._max]
        reminders = [timer for timer in loaded if timer.kind == REMINDER_KIND][:self._max_reminders]
        with self._lock:
            self._timers = sorted(timers + reminders, key=lambda timer: timer.ends_at)
            return len(self._timers)

    def running(self, kind: str | None = None) -> tuple[Timer, ...]:
        """The timers and reminders still to end, the soonest first (``kind``: only that kind)."""
        with self._lock:
            return tuple(timer for timer in self._timers if kind is None or timer.kind == kind)

    def _room(self, kind: str) -> None:
        """TimersFull / RemindersFull when ``kind`` has its most already. Called with the lock held."""
        count = sum(1 for timer in self._timers if timer.kind == kind)
        if kind == REMINDER_KIND and count >= self._max_reminders:
            raise RemindersFull("too many reminders")
        if kind == TIMER_KIND and count >= self._max:
            raise TimersFull("too many timers")

    def add(self, minutes: int, label: str = "") -> Timer:
        """A new timer of ``minutes`` (TIMER_MINUTES: else ValueError), kept and saved (TimersFull at MAX_TIMERS)."""
        if isinstance(minutes, bool) or not isinstance(minutes, int) \
                or not TIMER_MINUTES[0] <= minutes <= TIMER_MINUTES[1]:
            raise ValueError("a timer runs 1 to 240 minutes")
        now = self._now()
        timer = Timer(uuid.uuid4().hex[:8], minutes, now, now + timedelta(minutes=minutes), clean_label(label))
        with self._lock:
            self._room(TIMER_KIND)
            self._timers = sorted([*self._timers, timer], key=lambda item: item.ends_at)
            self._save()
        return timer

    def add_seconds(self, seconds: int, label: str = "") -> Timer:
        """Instant answers' timer of ``seconds`` (TIMER_SECONDS: else ValueError), kept and saved like ``add`` (its
        ``minutes`` rounded up, so a file read by an older Jarvis still holds a good timer; TimersFull at MAX_TIMERS)."""
        if isinstance(seconds, bool) or not isinstance(seconds, int) \
                or not TIMER_SECONDS[0] <= seconds <= TIMER_SECONDS[1]:
            raise ValueError("a timer runs 10 seconds to 4 hours")
        now = self._now()
        timer = Timer(uuid.uuid4().hex[:8], max(1, -(-seconds // 60)), now, now + timedelta(seconds=seconds),
                      clean_label(label), seconds)
        with self._lock:
            self._room(TIMER_KIND)
            self._timers = sorted([*self._timers, timer], key=lambda item: item.ends_at)
            self._save()
        return timer

    def add_until(self, ends_at: datetime, label: str = "") -> Timer:
        """A reminder at ``ends_at`` (aware; later than now and at most REMINDER_MAX_S ahead: else ValueError), kept
        and saved with the timers (RemindersFull at MAX_REMINDERS). Its ``minutes`` and ``seconds`` say how far
        ahead it was set."""
        if not isinstance(ends_at, datetime):
            raise ValueError("a reminder needs a time")
        now = self._now()
        ends = _like(ends_at, now)
        if ends.tzinfo is not None:
            ends = ends.astimezone(timezone.utc)
        ahead = (ends - _like(now, ends)).total_seconds()
        if not 0 < ahead <= REMINDER_MAX_S:
            raise ValueError("a reminder is at most 48 hours ahead")
        seconds = max(1, int(-(-ahead // 1)))
        timer = Timer(uuid.uuid4().hex[:8], max(1, -(-seconds // 60)), now, ends, clean_label(label), seconds,
                      REMINDER_KIND)
        with self._lock:
            self._room(REMINDER_KIND)
            self._timers = sorted([*self._timers, timer], key=lambda item: item.ends_at)
            self._save()
        return timer

    def cancel(self, timer_id: str) -> Timer | None:
        with self._lock:
            found = next((timer for timer in self._timers if timer.timer_id == timer_id), None)
            if found is not None:
                self._timers = [timer for timer in self._timers if timer is not found]
                self._save()
            return found

    def cancel_all(self, kind: str | None = None) -> tuple[Timer, ...]:
        """Every timer and reminder cancelled (``kind``: only that kind); the ones that went."""
        with self._lock:
            gone = tuple(timer for timer in self._timers if kind is None or timer.kind == kind)
            if gone:
                self._timers = [timer for timer in self._timers if not (kind is None or timer.kind == kind)]
                self._save()
            return gone

    def restore(self, timers: Sequence[Timer]) -> tuple[Timer, ...]:
        """Timers cancelled before (an Undo) back, those still to end and not running; the ones put back."""
        now = self._now()
        with self._lock:
            ids = {timer.timer_id for timer in self._timers}
            back: list[Timer] = []
            for kind, most in ((TIMER_KIND, self._max), (REMINDER_KIND, self._max_reminders)):
                room = most - sum(1 for timer in self._timers if timer.kind == kind)
                back += [timer for timer in timers if timer.kind == kind and timer.timer_id not in ids
                         and _like(timer.ends_at, now) > now][:max(0, room)]
            if back:
                self._timers = sorted([*self._timers, *back], key=lambda item: item.ends_at)
                self._save()
            return tuple(back)

    def next_end(self) -> datetime | None:
        with self._lock:
            return self._timers[0].ends_at if self._timers else None

    def seconds_left(self) -> float | None:
        """Seconds until the first timer ends (0 when it has; None without a timer)."""
        ends = self.next_end()
        if ends is None:
            return None
        now = self._now()
        return max(0.0, (_like(ends, now) - now).total_seconds())

    def take_due(self) -> tuple[list[tuple[Timer, float]], int]:
        """The timers whose end has come, taken out: (each with how many seconds late it is noticed, the number
        dropped quietly - ended more than TIMER_LATE_S ago, while Jarvis was closed; a reminder REMINDER_LATE_S)."""
        now = self._now()
        ended: list[tuple[Timer, float]] = []
        dropped = 0
        with self._lock:
            keep: list[Timer] = []
            for timer in self._timers:
                late = (now - _like(timer.ends_at, now)).total_seconds()
                if late < 0:
                    keep.append(timer)
                elif late > (REMINDER_LATE_S if timer.reminder else TIMER_LATE_S):
                    dropped += 1
                else:
                    ended.append((timer, late))
            if len(keep) != len(self._timers):
                self._timers = keep
                self._save()
        return ended, dropped

    def clear(self) -> int:
        return len(self.cancel_all())

    def _save(self) -> None:
        """The timers to the file (a new file put in place: never half written). Called with the lock held."""
        if self.folder is None:
            return
        entries = [{"id": timer.timer_id, "minutes": timer.minutes, "label": timer.label,
                    "set_at": timer.set_at.isoformat(), "ends_at": timer.ends_at.isoformat(),
                    **({"seconds": timer.seconds} if timer.seconds else {}),
                    **({"kind": timer.kind} if timer.kind != TIMER_KIND else {})}
                   for timer in self._timers]
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            temp = self.folder / (TIMERS_FILE + ".new")
            temp.write_text(json.dumps({"version": 1, "timers": entries}), encoding="utf-8")
            os.replace(temp, self.folder / TIMERS_FILE)
            self.saved = True
        except OSError as exc:
            self.saved = False
            logger.warning("Desktop pc: the timers could not be saved (%s %s)", type(exc).__name__,
                           getattr(exc, "winerror", "") or "")


def _timer_from(entry: Any) -> Timer | None:
    """One entry of the file as a Timer; None when it is of no good shape. An entry with no "kind" is a timer (every
    file before round 2 of instant answers)."""
    try:
        kind = entry.get("kind", TIMER_KIND)
        if kind not in (TIMER_KIND, REMINDER_KIND):
            return None
        reminder = kind == REMINDER_KIND
        minutes = entry["minutes"]
        most_minutes = -(-REMINDER_MAX_S // 60) if reminder else TIMER_MINUTES[1]
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not TIMER_MINUTES[0] <= minutes <= most_minutes:
            return None
        timer_id = str(entry["id"])
        if not re.fullmatch(r"[0-9a-f]{8}", timer_id):
            return None
        seconds = entry.get("seconds", 0)
        low, high = (1, REMINDER_MAX_S) if reminder else TIMER_SECONDS
        if isinstance(seconds, bool) or not isinstance(seconds, int) or (seconds and not low <= seconds <= high):
            return None
        return Timer(timer_id, minutes, datetime.fromisoformat(str(entry["set_at"])),
                     datetime.fromisoformat(str(entry["ends_at"])), clean_label(entry.get("label", "")), seconds,
                     kind)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


class FocusBook:
    """Focus mode's session (P2.5): at most one, in memory and in ``folder``/focus.json (ids, minutes and times only),
    so that Jarvis started again stays quiet until its end (``load``: one whose end came while Jarvis was closed is
    dropped). Safe to use from the desktop thread and the GUI thread. ``now()``: the wall clock."""

    def __init__(self, folder: str | Path | None, *, now: Callable[[], datetime] | None = None) -> None:
        self.folder = Path(folder) if folder else None
        self._now = now or _utc_now
        self._lock = threading.Lock()
        self._session: FocusSession | None = None
        self.saved = True

    def now(self) -> datetime:
        return self._now()

    def load(self) -> FocusSession | None:
        """The session kept in the file while it is still to end (else none, and the file is cleared)."""
        session: FocusSession | None = None
        path = self.folder / FOCUS_FILE if self.folder is not None else None
        try:
            if path is not None and path.is_file():
                session = _focus_from(json.loads(path.read_text(encoding="utf-8")).get("focus"))
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            logger.warning("Desktop pc: the saved focus could not be read (%s)", type(exc).__name__)
        now = self._now()
        with self._lock:
            stale = session is not None and _like(session.ends_at, now) <= now
            self._session = None if stale else session
            if stale:
                self._save()
            return self._session

    def active(self) -> FocusSession | None:
        with self._lock:
            return self._session

    def start(self, minutes: int, focus_id: str = "") -> tuple[FocusSession, bool]:
        """Focus for ``minutes`` (TIMER_MINUTES: else ValueError) from now, saved, with ``focus_id`` (a new one when
        "" or not of 8 hex digits); one still running starts again from now and keeps its own id: (the session,
        whether it started again)."""
        if isinstance(minutes, bool) or not isinstance(minutes, int) \
                or not TIMER_MINUTES[0] <= minutes <= TIMER_MINUTES[1]:
            raise ValueError("focus runs 1 to 240 minutes")
        if not re.fullmatch(r"[0-9a-f]{8}", str(focus_id or "")):
            focus_id = uuid.uuid4().hex[:8]
        now = self._now()
        with self._lock:
            current = self._session
            again = current is not None and _like(current.ends_at, now) > now
            focus_id = current.focus_id if again and current is not None else focus_id
            self._session = FocusSession(focus_id, minutes, now, now + timedelta(minutes=minutes))
            self._save()
            return self._session, again

    def end(self, focus_id: str = "") -> FocusSession | None:
        """Focus over now (only that session's when ``focus_id`` is given): the session it was, or None."""
        with self._lock:
            current = self._session
            if current is None or (focus_id and current.focus_id != focus_id):
                return None
            self._session = None
            self._save()
            return current

    def take_due(self) -> tuple[FocusSession | None, float]:
        """The session whose end has come, taken out, with how many seconds late it is noticed; (None, 0) else."""
        now = self._now()
        with self._lock:
            current = self._session
            if current is None:
                return None, 0.0
            late = (now - _like(current.ends_at, now)).total_seconds()
            if late < 0:
                return None, 0.0
            self._session = None
            self._save()
            return current, late

    def seconds_left(self) -> float | None:
        """Seconds until focus ends (0 when it has; None without one)."""
        session = self.active()
        if session is None:
            return None
        now = self._now()
        return max(0.0, (_like(session.ends_at, now) - now).total_seconds())

    def clear(self) -> FocusSession | None:
        return self.end()

    def _save(self) -> None:
        """The session to the file (a new file put in place). Called with the lock held."""
        if self.folder is None:
            return
        session = self._session
        entry = None if session is None else {"id": session.focus_id, "minutes": session.minutes,
                                              "set_at": session.set_at.isoformat(),
                                              "ends_at": session.ends_at.isoformat()}
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            temp = self.folder / (FOCUS_FILE + ".new")
            temp.write_text(json.dumps({"version": 1, "focus": entry}), encoding="utf-8")
            os.replace(temp, self.folder / FOCUS_FILE)
            self.saved = True
        except OSError as exc:
            self.saved = False
            logger.warning("Desktop pc: focus could not be saved (%s %s)", type(exc).__name__,
                           getattr(exc, "winerror", "") or "")


def _focus_from(entry: Any) -> FocusSession | None:
    if not entry:
        return None
    try:
        minutes = entry["minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, int) \
                or not TIMER_MINUTES[0] <= minutes <= TIMER_MINUTES[1]:
            return None
        focus_id = str(entry["id"])
        if not re.fullmatch(r"[0-9a-f]{8}", focus_id):
            return None
        return FocusSession(focus_id, minutes, datetime.fromisoformat(str(entry["set_at"])),
                            datetime.fromisoformat(str(entry["ends_at"])))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


# --------------------------------------------------------------------------
# The controls (the desktop thread's)
# --------------------------------------------------------------------------

def shot_name(moment: datetime, number: int = 1) -> str:
    """"Screenshot 2026-10-29 10.22.05.png" (local time; " (2)" before the type for the second try)."""
    stamp = _local(moment).strftime("%Y-%m-%d %H.%M.%S")
    return f"Screenshot {stamp}{f' ({number})' if number > 1 else ''}.png"


def _stopped(cancel: Any) -> bool:
    return cancel is not None and cancel.is_set()


def _winerror(exc: BaseException) -> int:
    return int(getattr(exc, "winerror", 0) or 0)


class PcControl:
    """See the module docstring. ``backend_factory``: makes the PC seam at the first use, on the desktop thread (the
    real one refuses to be made while the tests run - then nothing can be controlled, and Jarvis says so);
    ``disk()``: the desktop backend (the screenshots folder's facts, its new file, a shot's hash for Undo);
    ``timers``: the TimerBook; ``screenshots_dir``: [desktop] screenshots_dir ("" = the default, D8); ``clock``: a
    monotonic clock (the volume's rate cap); ``now``: the wall clock (a shot's name); ``recycler``: desktop.fileops'
    Recycle Bin seam (desktop.recycle.RecycleBin in the app; NoRecycleBin when none); ``focus``: focus mode's
    FocusBook (P2.5)."""

    def __init__(self, backend_factory: Callable[[], PcBackend], *, disk: Callable[[], Any] | None = None,
                 timers: TimerBook | None = None, screenshots_dir: str = "",
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] | None = None,
                 recycler: Any = None, focus: FocusBook | None = None) -> None:
        self._factory = backend_factory
        self._backend: PcBackend | None = None
        self._failed = False
        self._disk = disk or (lambda: None)
        self.timers = timers
        self.focus_book = focus
        self._shots_dir = str(screenshots_dir or "").strip()
        self._clock = clock
        self._now = now or _utc_now
        self._recycler = recycler
        self._last_volume: float | None = None
        self._changes: list[PcChange] = []

    def backend(self) -> PcBackend | None:
        """The PC seam, made at the first call; None when it can't be (logged by its type once)."""
        if self._backend is None and not self._failed:
            try:
                self._backend = self._factory()
            except Exception as exc:  # noqa: BLE001 - RealBackendInTests in the tests; anything else: no controls
                self._failed = True
                logger.warning("Desktop pc: the PC's controls can't be reached (%s)", type(exc).__name__)
        return self._backend

    def changes(self) -> tuple[PcChange, ...]:
        """The changes made this session, oldest first (P2.5's Undo all puts them back, newest first)."""
        return tuple(self._changes)

    def _keep(self, change: PcChange) -> PcChange:
        if change.changed:
            self._changes.append(change)
            del self._changes[:-50]
        return change

    def carry_out(self, ask: PcAsk, *, cancel: Any = None, hud: Sequence[int] = ()) -> PcChange:
        """``ask`` carried out (nothing once ``cancel`` is set; ``hud``: the HUD's windows, left out of a
        screenshot)."""
        kind = getattr(ask, "kind", "")
        if _stopped(cancel):
            return PcChange(kind, getattr(ask, "action", ""), STOPPED)
        if kind == dv.VOLUME:
            return self.volume(ask.action, level=ask.level, step=ask.step, cancel=cancel)
        if kind == dv.MEDIA:
            return self.media(ask.action, cancel=cancel)
        if kind == dv.TIMER:
            return self.timer(ask.minutes, ask.label, cancel=cancel)
        if kind == dv.CANCEL_TIMER:
            return self.cancel_timers(cancel=cancel)
        if kind == dv.SCREENSHOT:
            return self.screenshot(ask.action or SCREEN, hud=hud, cancel=cancel)
        if kind == dv.FOCUS_MODE:
            return self.focus(ask.minutes, cancel=cancel)
        return PcChange(kind, getattr(ask, "action", ""), dv.SHAPE)

    # ---- volume -----------------------------------------------------------------------------------

    def volume(self, action: str, *, level: int | None = None, step: int | None = None,
               cancel: Any = None) -> PcChange:
        """Set, up, down, mute or unmute the default output (see the module docstring)."""
        step = VOLUME_STEP if step is None else step
        if action not in dv.VOLUME_ACTIONS or (action == "set") != (level is not None) \
                or (level is not None and (isinstance(level, bool) or not 0 <= int(level) <= 100)) \
                or not 1 <= int(step) <= 100:
            return self._refused(PcChange(dv.VOLUME, action, dv.SHAPE))
        pc = self.backend()
        if pc is None:
            return self._refused(PcChange(dv.VOLUME, action, UNAVAILABLE))
        now = self._clock()
        if self._last_volume is not None and now - self._last_volume < VOLUME_CAP_S:
            return self._refused(PcChange(dv.VOLUME, action, TOO_SOON))
        try:
            before = pc.audio()
        except NoDevice:
            return self._refused(PcChange(dv.VOLUME, action, NO_DEVICE))
        except Exception as exc:  # noqa: BLE001 - Windows did not say: nothing changed
            return self._failed_change(PcChange(dv.VOLUME, action, OS_ERROR, winerror=_winerror(exc)), exc)
        after = {"set": AudioState(int(level or 0), False),
                 "up": AudioState(min(100, before.level + int(step)), False),
                 "down": AudioState(max(0, before.level - int(step)), False),
                 "mute": AudioState(before.level, True),
                 "unmute": AudioState(before.level, False)}[action]
        return self._set_audio(pc, action, before, after, cancel)

    def _set_audio(self, pc: PcBackend, action: str, before: AudioState, after: AudioState, cancel: Any,
                   kind: str = dv.VOLUME, group: str = "") -> PcChange:
        if after == before:
            return PcChange(kind, action, "", False, False, before, before, group=group)
        if _stopped(cancel):                             # asked again right before the OS call
            return PcChange(kind, action, STOPPED, before=before, group=group)
        self._last_volume = self._clock()                # the cap counts every change asked of Windows
        try:
            if after.level != before.level:
                pc.set_level(after.level)
            if after.muted != before.muted:
                pc.set_mute(after.muted)
        except Exception as exc:  # noqa: BLE001 - Windows did not do it (all of it, or part)
            return self._failed_change(PcChange(kind, action, OS_ERROR, before=before, winerror=_winerror(exc),
                                                group=group), exc)
        logger.info("Desktop pc: volume %s, %d -> %d%s", action, before.level, after.level,
                    "" if after.muted == before.muted else (", muted" if after.muted else ", unmuted"))
        return self._keep(PcChange(kind, action, "", True, True, before, after, group=group))

    # ---- music ------------------------------------------------------------------------------------

    def media(self, action: str, *, cancel: Any = None) -> PcChange:
        """Play, pause, next or previous: through the player's controls when they can be asked (its state known: a
        play or a pause can be put back), else the media key (the state unknown: it can't be put back)."""
        if action not in dv.MEDIA_ACTIONS:
            return self._refused(PcChange(dv.MEDIA, action, dv.SHAPE))
        pc = self.backend()
        if pc is None:
            return self._refused(PcChange(dv.MEDIA, action, UNAVAILABLE))
        try:
            state = pc.media_state()
        except Exception as exc:  # noqa: BLE001 - the player's controls failed: the media key then
            logger.info("Desktop pc: the player's state could not be read (%s)", type(exc).__name__)
            state = UNKNOWN
        if state == UNKNOWN:
            return self._media_key(pc, action, cancel)
        if state == NO_SESSION:
            return self._refused(PcChange(dv.MEDIA, action, NO_PLAYER, before=state, via=VIA_PLAYER))
        if (action == "pause" and state != PLAYING) or (action == "play" and state == PLAYING):
            return PcChange(dv.MEDIA, action, "", False, False, state, state, VIA_PLAYER)
        if _stopped(cancel):
            return PcChange(dv.MEDIA, action, STOPPED, before=state, via=VIA_PLAYER)
        try:
            taken = pc.media_control(action)
        except MediaUnavailable:
            return self._media_key(pc, action, cancel)
        except Exception as exc:  # noqa: BLE001 - the player did not answer
            return self._failed_change(PcChange(dv.MEDIA, action, OS_ERROR, before=state, via=VIA_PLAYER,
                                                winerror=_winerror(exc)), exc)
        if not taken:
            return self._refused(PcChange(dv.MEDIA, action, NOT_TAKEN, before=state, via=VIA_PLAYER))
        after = {"pause": PAUSED, "play": PLAYING}.get(action, state)
        logger.info("Desktop pc: media %s through the player (%s -> %s)", action, state, after)
        return self._keep(PcChange(dv.MEDIA, action, "", True, action in ("play", "pause"), state, after,
                                   VIA_PLAYER))

    def _media_key(self, pc: PcBackend, action: str, cancel: Any) -> PcChange:
        if _stopped(cancel):
            return PcChange(dv.MEDIA, action, STOPPED, before=UNKNOWN, via=VIA_KEYS)
        try:
            pc.media_key(action)
        except Exception as exc:  # noqa: BLE001 - Windows did not take the key
            return self._failed_change(PcChange(dv.MEDIA, action, OS_ERROR, before=UNKNOWN, via=VIA_KEYS,
                                                winerror=_winerror(exc)), exc)
        logger.info("Desktop pc: media %s by its key (state unknown: can't be put back)", action)
        return self._keep(PcChange(dv.MEDIA, action, "", True, False, UNKNOWN, UNKNOWN, VIA_KEYS))

    # ---- timers -----------------------------------------------------------------------------------

    def timer(self, minutes: Any, label: str = "", *, cancel: Any = None) -> PcChange:
        """A new Jarvis timer of ``minutes`` (1-240), its label at most LABEL_MAX characters."""
        if isinstance(minutes, bool) or not isinstance(minutes, int) \
                or not TIMER_MINUTES[0] <= minutes <= TIMER_MINUTES[1]:
            return self._refused(PcChange(dv.TIMER, SET, dv.SHAPE))
        if self.timers is None:
            return self._refused(PcChange(dv.TIMER, SET, UNAVAILABLE))
        if _stopped(cancel):
            return PcChange(dv.TIMER, SET, STOPPED)
        try:
            timer = self.timers.add(minutes, label)
        except TimersFull:
            return self._refused(PcChange(dv.TIMER, SET, TIMERS_FULL))
        logger.info("Desktop pc: timer set, %d min (%d running%s)", minutes, len(self.timers.running()),
                    "" if self.timers.saved else ", not saved")
        return self._keep(PcChange(dv.TIMER, SET, "", True, True, None, timer))

    def cancel_timers(self, *, cancel: Any = None) -> PcChange:
        """Every running Jarvis timer cancelled (kept for Undo all: those still to end come back). Reminders (instant
        answers' round 2) stay: they are cancelled by name."""
        if self.timers is None:
            return self._refused(PcChange(dv.CANCEL_TIMER, CANCEL, UNAVAILABLE))
        if not self.timers.running(TIMER_KIND):
            return self._refused(PcChange(dv.CANCEL_TIMER, CANCEL, NO_TIMER))
        if _stopped(cancel):
            return PcChange(dv.CANCEL_TIMER, CANCEL, STOPPED)
        gone = self.timers.cancel_all(TIMER_KIND)
        logger.info("Desktop pc: %d timer(s) cancelled", len(gone))
        return self._keep(PcChange(dv.CANCEL_TIMER, CANCEL, "", True, True, gone, ()))

    # ---- screenshots ------------------------------------------------------------------------------

    def shots_folder(self) -> tuple[str, str]:
        """(the screenshots folder, "") or ("", NO_FOLDER): [desktop] screenshots_dir when it is a plain local folder
        on a fixed drive outside OneDrive (D8), else Jarvis\\Screenshots in the user folder."""
        disk = self._disk()
        if disk is None:
            return "", UNAVAILABLE
        try:
            profile = disk.known_folder("profile")
        except Exception:  # noqa: BLE001 - no user folder: only a configured folder can be used
            profile = None
        default = ntpath.join(profile, *SHOTS_FOLDER) if profile else ""
        for folder in (self._shots_dir, default):
            if not folder:
                continue
            problem = self._folder_problem(disk, folder)
            if not problem:
                return folder, ""
            logger.info("Desktop pc: a screenshots folder was not used (%s)", problem)
        return "", NO_FOLDER

    @staticmethod
    def _folder_problem(disk: Any, folder: str) -> str:
        problem = paths.lexical_problem(folder)
        if problem:
            return problem
        try:
            if disk.drive_type(folder.replace("/", "\\")[:3]) != DRIVE_FIXED:
                return "not_fixed"
            onedrive = disk.known_folder("onedrive")
            key, cloud = ntpath.normcase(folder).rstrip("\\"), ntpath.normcase(onedrive or "").rstrip("\\")
            if cloud and (key == cloud or key.startswith(cloud + "\\")):
                return "onedrive"
            probe = folder
            while probe and not disk.exists(probe):          # the deepest part that is there must be a plain folder
                parent = ntpath.dirname(probe.rstrip("\\"))
                probe = "" if parent == probe else parent
            if probe:
                info = disk.file_info(probe)
                if not info.is_dir or link_tag(info.reparse_tag) or cloud_tag(info.reparse_tag) or info.online_only:
                    return "not_plain"
        except Exception as exc:  # noqa: BLE001 - can't tell: not used
            return type(exc).__name__
        return ""

    def _make_folder(self, disk: Any, folder: str) -> None:
        missing: list[str] = []
        probe = folder.rstrip("\\")
        while probe and not disk.exists(probe):
            missing.append(probe)
            parent = ntpath.dirname(probe)
            if parent == probe:
                break
            probe = parent
        for part in reversed(missing):
            disk.make_folder(part)

    def screenshot(self, mode: str = SCREEN, *, hud: Sequence[int] = (), cancel: Any = None) -> PcChange:
        """The screen or the active window saved as a PNG in the screenshots folder, the HUD's windows ``hud`` left
        out of it; never sent anywhere."""
        if mode not in SHOT_MODES:
            return self._refused(PcChange(dv.SCREENSHOT, mode, dv.SHAPE))
        pc, disk = self.backend(), self._disk()
        if pc is None or disk is None:
            return self._refused(PcChange(dv.SCREENSHOT, mode, UNAVAILABLE))
        folder, code = self.shots_folder()
        if code:
            return self._refused(PcChange(dv.SCREENSHOT, mode, code))
        if _stopped(cancel):
            return PcChange(dv.SCREENSHOT, mode, STOPPED)
        try:
            data = pc.capture(mode, tuple(hud))
        except NoWindow:
            return self._refused(PcChange(dv.SCREENSHOT, mode, NO_WINDOW))
        except HudShows:
            return self._refused(PcChange(dv.SCREENSHOT, mode, HUD_SHOWS))
        except Exception as exc:  # noqa: BLE001 - Windows did not give the picture
            return self._failed_change(PcChange(dv.SCREENSHOT, mode, OS_ERROR, winerror=_winerror(exc)), exc)
        moment = self._now()
        try:
            self._make_folder(disk, folder)
            path = ""
            for number in range(1, MAX_NAME_TRIES + 1):
                candidate = ntpath.join(folder, shot_name(moment, number))
                if disk.exists(candidate):
                    continue
                try:
                    disk.save_png(candidate, data)
                except FileExistsError:
                    continue
                path = candidate
                break
            if not path:
                raise FileExistsError(183, "no free name")
        except Exception as exc:  # noqa: BLE001 - not saved: nothing was kept
            return self._failed_change(PcChange(dv.SCREENSHOT, mode, OS_ERROR, winerror=_winerror(exc)), exc)
        shot = Shot(path, hashlib.sha256(data).hexdigest(), len(data), mode)
        logger.info("Desktop pc: screenshot of the %s saved (%d KB)", "screen" if mode == SCREEN else "window",
                    max(1, len(data) // 1024))
        return self._keep(PcChange(dv.SCREENSHOT, mode, "", True, True, None, shot))

    # ---- focus mode (P2.5) ------------------------------------------------------------------------

    def focus(self, minutes: Any, *, cancel: Any = None) -> PcChange:
        """Focus mode for ``minutes`` (1-240; see the module docstring): the music paused through the player's own
        controls only, a louder volume turned down to FOCUS_VOLUME (each kept as a change of its own, its group the
        session's id) and the session started - again from now when one runs (its id kept); the cancel asked again
        right before each OS call. Its ``after`` is a FocusDone; without the PC's controls focus still starts (Jarvis's
        own quiet needs none)."""
        if isinstance(minutes, bool) or not isinstance(minutes, int) \
                or not TIMER_MINUTES[0] <= minutes <= TIMER_MINUTES[1]:
            return self._refused(PcChange(dv.FOCUS_MODE, SET, dv.SHAPE))
        book = self.focus_book
        if book is None:
            return self._refused(PcChange(dv.FOCUS_MODE, SET, UNAVAILABLE))
        if _stopped(cancel):
            return PcChange(dv.FOCUS_MODE, SET, STOPPED)
        current = book.active()
        focus_id = current.focus_id if current is not None else uuid.uuid4().hex[:8]
        changes: list[PcChange] = []
        music = ""
        pc = self.backend()
        if pc is not None:
            music, paused = self._focus_music(pc, focus_id, cancel)
            lowered = self._focus_volume(pc, focus_id, cancel)
            changes = [change for change in (paused, lowered) if change is not None]
        if _stopped(cancel):
            return PcChange(dv.FOCUS_MODE, SET, STOPPED)
        session, again = book.start(minutes, focus_id)
        logger.info("Desktop pc: focus mode %s, %d min (music %s, volume %s%s)", "again" if again else "on", minutes,
                    music or "-", "down" if any(change.kind == dv.VOLUME for change in changes) else "as it was",
                    "" if book.saved else ", not saved")
        return self._keep(PcChange(dv.FOCUS_MODE, SET, "", True, True, current,
                                   FocusDone(session, tuple(changes), music, again), group=session.focus_id))

    def _focus_music(self, pc: PcBackend, focus_id: str, cancel: Any) -> tuple[str, PcChange | None]:
        """Focus pauses what plays through the player's own controls only: (PAUSED and the change; PLAYING when the
        player did not take it; UNKNOWN when its state can't be read - then nothing is pressed, as a blind play/pause
        key could start the music; "" when nothing plays)."""
        try:
            state = pc.media_state()
        except Exception as exc:  # noqa: BLE001 - the player's controls failed: left alone
            logger.info("Desktop pc: the player's state could not be read (%s)", type(exc).__name__)
            state = UNKNOWN
        if state == UNKNOWN:
            return UNKNOWN, None
        if state != PLAYING or _stopped(cancel):
            return "", None
        try:
            taken = pc.media_control("pause")
        except Exception as exc:  # noqa: BLE001 - MediaUnavailable, or the player did not answer: left playing
            logger.info("Desktop pc: focus could not pause the player (%s)", type(exc).__name__)
            taken = False
        if not taken:
            return PLAYING, None
        logger.info("Desktop pc: media pause through the player (focus)")
        return PAUSED, self._keep(PcChange(dv.MEDIA, "pause", "", True, True, PLAYING, PAUSED, VIA_PLAYER,
                                           group=focus_id))

    def _focus_volume(self, pc: PcBackend, focus_id: str, cancel: Any) -> PcChange | None:
        """Focus turns a volume louder than FOCUS_VOLUME down to it (a muted one, a quieter one, one changed less than
        VOLUME_CAP_S ago or no speakers: left as it is)."""
        if self._last_volume is not None and self._clock() - self._last_volume < VOLUME_CAP_S:
            return None
        try:
            before = pc.audio()
        except Exception as exc:  # noqa: BLE001 - NoDevice, or Windows did not say: left as it is
            logger.info("Desktop pc: focus left the volume (%s)", type(exc).__name__)
            return None
        if before.muted or before.level <= FOCUS_VOLUME:
            return None
        change = self._set_audio(pc, "set", before, AudioState(FOCUS_VOLUME, False), cancel, group=focus_id)
        return change if change.changed else None

    def dnd_state(self) -> str:
        """Windows' Do Not Disturb as read back (read only): DND_ON, DND_OFF or DND_UNKNOWN (no seam, or it can't be
        read)."""
        pc = self.backend()
        if pc is None:
            return DND_UNKNOWN
        try:
            state = str(pc.dnd_state())
        except Exception as exc:  # noqa: BLE001 - can't be read: unknown
            logger.info("Desktop pc: Do Not Disturb could not be read (%s)", type(exc).__name__)
            return DND_UNKNOWN
        return state if state in (DND_ON, DND_OFF) else DND_UNKNOWN

    def open_dnd(self, *, cancel: Any = None) -> PcChange:
        """Windows' notification settings opened for the owner's own switch (his click on the HUD's chip: the one fixed
        page, guard.DND_OPENER_URI). Nothing on this PC is changed by Jarvis."""
        pc = self.backend()
        if pc is None:
            return self._refused(PcChange(DND, "open", UNAVAILABLE))
        if _stopped(cancel):
            return PcChange(DND, "open", STOPPED)
        try:
            pc.open_notifications()
        except Exception as exc:  # noqa: BLE001 - Windows did not open it
            return self._failed_change(PcChange(DND, "open", OS_ERROR, winerror=_winerror(exc)), exc)
        logger.info("Desktop pc: Windows' notification settings opened (the owner's click)")
        return PcChange(DND, "open")

    # ---- Undo (P2.5's Undo all; the owner's click) -----------------------------------------------------

    def undo(self, change: PcChange, *, cancel: Any = None) -> PcChange:
        """``change`` put back if it can be and nothing changed since: the volume and mute as they were, the player
        as it was (its own controls only), a timer cancelled, cancelled timers back, a screenshot put in the Recycle
        Bin (unchanged; through the Recycler seam - its reason said plainly when the bin didn't take it). A PcChange
        of action UNDO: its code "" when it was put back."""
        kind = change.kind
        if not change.changed or not change.undoable:
            return PcChange(kind, UNDO, CANT_UNDO)
        if _stopped(cancel):
            return PcChange(kind, UNDO, STOPPED)
        if kind == dv.VOLUME:
            return self._undo_volume(change, cancel)
        if kind == dv.MEDIA:
            return self._undo_media(change, cancel)
        if kind == dv.TIMER:
            gone = self.timers.cancel(change.after.timer_id) if self.timers is not None else None
            return PcChange(kind, UNDO, "" if gone is not None else GONE, gone is not None)
        if kind == dv.CANCEL_TIMER:
            back = self.timers.restore(change.before or ()) if self.timers is not None else ()
            return PcChange(kind, UNDO, "" if back else GONE, bool(back), after=back)
        if kind == dv.SCREENSHOT:
            return self._undo_shot(change)
        if kind == dv.FOCUS_MODE:
            return self._end_focus(change)
        return PcChange(kind, UNDO, CANT_UNDO)

    def undo_changes(self, changes: Sequence[PcChange], *,
                     cancel: Any = None) -> tuple[tuple[PcChange, PcChange], ...]:
        """``changes`` (oldest first, as they were made) put back newest first, each checked against this PC as it is
        now - and each comes back with its Undo, newest first (a PcChange of action UNDO: code "" when it was put back,
        or was as it was already). A run of volume changes is put back with ONE change of the level and mute (the 300 ms
        cap would refuse a second one at once), a run of the player's with one call; focus mode's ends its session."""
        ordered = [change for change in reversed(list(changes)) if isinstance(change, PcChange)]
        results: dict[int, PcChange] = {}
        volumes = [change for change in ordered if change.kind == dv.VOLUME]
        if volumes:
            results.update(self._undo_volumes(volumes, cancel))
        players = [change for change in ordered if change.kind == dv.MEDIA]
        if players:
            results.update(self._undo_players(players, cancel))
        from . import fileops

        halted = False                       # p2r-14: the Recycle Bin didn't take a screenshot - none more goes to it
        for change in ordered:
            if id(change) in results:
                continue
            if halted and change.kind == dv.SCREENSHOT:
                results[id(change)] = PcChange(change.kind, UNDO, fileops.R_BIN_STOPPED, group=change.group)
                continue
            results[id(change)] = self.undo(change, cancel=cancel)
            halted = halted or (change.kind == dv.SCREENSHOT and results[id(change)].code in fileops.BIN_STOPS)
        return tuple((change, results[id(change)]) for change in ordered)

    def _undo_volumes(self, changes: list[PcChange], cancel: Any) -> dict[int, PcChange]:
        """The volume's changes (newest first) put back with at most one change: each checked against the state the
        newer ones leave behind (the PC's own state first) - one that does not match was changed since and is left."""
        results: dict[int, PcChange] = {}
        undoable: list[PcChange] = []
        for change in changes:
            if change.changed and change.undoable and isinstance(change.before, AudioState) \
                    and isinstance(change.after, AudioState):
                undoable.append(change)
            else:
                results[id(change)] = PcChange(dv.VOLUME, UNDO, CANT_UNDO, group=change.group)

        def each(code: str, winerror: int = 0) -> dict[int, PcChange]:
            for change in undoable:
                results[id(change)] = PcChange(dv.VOLUME, UNDO, code, winerror=winerror, group=change.group)
            return results

        if not undoable:
            return results
        pc = self.backend()
        if pc is None:
            return each(UNAVAILABLE)
        if self._last_volume is not None and self._clock() - self._last_volume < VOLUME_CAP_S:
            return each(TOO_SOON)
        try:
            now = pc.audio()
        except Exception as exc:  # noqa: BLE001 - can't tell: left as it is
            return each(OS_ERROR, _winerror(exc))
        state, back = now, []
        for change in undoable:
            if state == change.after:
                state = change.before
                back.append(change)
            else:
                results[id(change)] = PcChange(dv.VOLUME, UNDO, CHANGED, before=state, group=change.group)
        code, winerror, changed = "", 0, False
        if back and state != now:
            done = self._set_audio(pc, UNDO, now, state, cancel)
            if done.changed and self._changes and self._changes[-1] is done:
                self._changes.pop()                      # an Undo is not itself a change to put back
            code, winerror, changed = done.code, done.winerror, done.changed
        for change in back:
            results[id(change)] = PcChange(dv.VOLUME, UNDO, code, changed, before=now,
                                           after=None if code else state, winerror=winerror, group=change.group)
        return results

    def _undo_players(self, changes: list[PcChange], cancel: Any) -> dict[int, PcChange]:
        """The player's changes (newest first) put back with at most one call, checked as the volume's are; one made by
        the media key can't be put back (its state was never known)."""
        results: dict[int, PcChange] = {}
        undoable: list[PcChange] = []
        for change in changes:
            if change.changed and change.undoable and change.via == VIA_PLAYER:
                undoable.append(change)
            else:
                results[id(change)] = PcChange(dv.MEDIA, UNDO, CANT_UNDO, via=change.via, group=change.group)
        if not undoable:
            return results
        pc = self.backend()
        if pc is None:
            for change in undoable:
                results[id(change)] = PcChange(dv.MEDIA, UNDO, UNAVAILABLE, group=change.group)
            return results
        try:
            now = pc.media_state()
        except Exception:  # noqa: BLE001 - can't tell: each is then changed since, left as it is
            now = UNKNOWN
        state, back = now, []
        for change in undoable:
            if state == change.after:
                state = change.before
                back.append(change)
            else:
                results[id(change)] = PcChange(dv.MEDIA, UNDO, CHANGED, before=state, via=VIA_PLAYER,
                                               group=change.group)
        code, winerror, taken = "", 0, False
        if back and state != now:
            if _stopped(cancel):
                code = STOPPED
            else:
                try:
                    taken = bool(pc.media_control("play" if state == PLAYING else "pause"))
                    code = "" if taken else NOT_TAKEN
                except Exception as exc:  # noqa: BLE001 - the player did not answer: left as it is
                    code, winerror = OS_ERROR, _winerror(exc) if isinstance(exc, OSError) else 0
            if taken:
                logger.info("Desktop pc: the player put back (%s -> %s)", now, state)
        for change in back:
            results[id(change)] = PcChange(dv.MEDIA, UNDO, code, taken, before=now, after=None if code else state,
                                           via=VIA_PLAYER, winerror=winerror, group=change.group)
        return results

    def _end_focus(self, change: PcChange) -> PcChange:
        """Focus mode over (Jarvis's own quiet: no OS call). The app ends it as the owner clicks, so it may be over
        already - it is put back either way."""
        done = change.after if isinstance(change.after, FocusDone) else None
        if done is not None and self.focus_book is not None:
            self.focus_book.end(done.session.focus_id)
        return PcChange(dv.FOCUS_MODE, UNDO, "", True, group=change.group)

    def _undo_volume(self, change: PcChange, cancel: Any) -> PcChange:
        pc = self.backend()
        if pc is None:
            return PcChange(dv.VOLUME, UNDO, UNAVAILABLE)
        if self._last_volume is not None and self._clock() - self._last_volume < VOLUME_CAP_S:
            return PcChange(dv.VOLUME, UNDO, TOO_SOON)
        try:
            now = pc.audio()
        except Exception as exc:  # noqa: BLE001 - can't tell: left as it is
            return PcChange(dv.VOLUME, UNDO, OS_ERROR, winerror=_winerror(exc))
        if now != change.after:
            return PcChange(dv.VOLUME, UNDO, CHANGED, before=now)
        back = self._set_audio(pc, UNDO, now, change.before, cancel)
        if back.changed and self._changes and self._changes[-1] is back:
            self._changes.pop()                          # an Undo is not itself a change to put back
        return back

    def _undo_media(self, change: PcChange, cancel: Any) -> PcChange:
        pc = self.backend()
        if pc is None or change.via != VIA_PLAYER:
            return PcChange(dv.MEDIA, UNDO, CANT_UNDO if pc is not None else UNAVAILABLE)
        try:
            state = pc.media_state()
        except Exception:  # noqa: BLE001 - can't tell: left as it is
            state = UNKNOWN
        if state != change.after:
            return PcChange(dv.MEDIA, UNDO, CHANGED, before=state)
        if _stopped(cancel):
            return PcChange(dv.MEDIA, UNDO, STOPPED)
        action = "play" if change.before == PLAYING else "pause"
        try:
            taken = pc.media_control(action)
        except Exception as exc:  # noqa: BLE001 - the player did not answer
            return PcChange(dv.MEDIA, UNDO, OS_ERROR, winerror=_winerror(exc) if isinstance(exc, OSError) else 0)
        return PcChange(dv.MEDIA, UNDO, "" if taken else NOT_TAKEN, bool(taken), before=state, after=change.before,
                        via=VIA_PLAYER)

    def _undo_shot(self, change: PcChange) -> PcChange:
        from . import fileops

        shot, disk = change.after, self._disk()
        if disk is None:
            return PcChange(dv.SCREENSHOT, UNDO, UNAVAILABLE)
        try:
            if not disk.exists(shot.path):
                return PcChange(dv.SCREENSHOT, UNDO, GONE)
            if disk.content_hash(shot.path) != shot.sha256:
                return PcChange(dv.SCREENSHOT, UNDO, CHANGED)
            recycler = self._recycler if self._recycler is not None else fileops.NoRecycleBin()
            recycler.recycle(shot.path)
        except fileops.RecycleUnavailable as exc:
            return PcChange(dv.SCREENSHOT, UNDO, exc.reason)
        except Exception as exc:  # noqa: BLE001 - left as it is
            return PcChange(dv.SCREENSHOT, UNDO, OS_ERROR, winerror=_winerror(exc))
        return PcChange(dv.SCREENSHOT, UNDO, "", True)

    def chime(self) -> bool:
        """The short sound at a timer's end (False when it could not be played)."""
        pc = self.backend()
        if pc is None:
            return False
        try:
            pc.chime()
        except Exception as exc:  # noqa: BLE001 - the toast still says it
            logger.info("Desktop pc: no sound (%s)", type(exc).__name__)
            return False
        return True

    # ---- logging ----------------------------------------------------------------------------------

    @staticmethod
    def _refused(change: PcChange) -> PcChange:
        logger.info("Desktop pc: %s %s not done (%s)", change.kind, change.action or "-", change.code)
        return change

    @staticmethod
    def _failed_change(change: PcChange, exc: BaseException) -> PcChange:
        logger.warning("Desktop pc: %s %s failed (%s %s)", change.kind, change.action or "-", type(exc).__name__,
                       change.winerror or "")
        return change


# --------------------------------------------------------------------------
# Jarvis's own words (the HUD's caption, the toast, the chip) and Live's notes
# --------------------------------------------------------------------------

_DONE_WORDS = {
    (dv.VOLUME, "mute"): "Muted, {sir}.",
    (dv.VOLUME, "unmute"): "Unmuted, {sir}.",
    (dv.MEDIA, "pause"): "Paused, {sir}.",
    (dv.MEDIA, "play"): "Playing, {sir}.",
    (dv.MEDIA, "next"): "Next track, {sir}.",
    (dv.MEDIA, "previous"): "Previous track, {sir}.",
    (dv.SCREENSHOT, SCREEN): "Screenshot saved to your screenshots folder, {sir}.",
    (dv.SCREENSHOT, ACTIVE_WINDOW): "Screenshot of the window saved to your screenshots folder, {sir}.",
}
_ALREADY_WORDS = {
    (dv.VOLUME, "mute"): "It's already muted, {sir}.",
    (dv.VOLUME, "unmute"): "The sound is already on, {sir}.",
    (dv.MEDIA, "pause"): "It's already paused, {sir}.",
    (dv.MEDIA, "play"): "It's already playing, {sir}.",
}
_KEY_WORDS = {
    "play": "Pressed play/pause, {sir} - I can't tell what was playing, so it can't be put back.",
    "pause": "Pressed play/pause, {sir} - I can't tell what was playing, so it can't be put back.",
    "next": "Pressed next track, {sir} - it can't be put back.",
    "previous": "Pressed previous track, {sir} - it can't be put back.",
}
_NOT_DONE_WORDS = {
    UNAVAILABLE: "I can't reach this PC's controls right now, {sir} - nothing was changed.",
    STOPPED: "Stopped - nothing was changed, {sir}.",
    TOO_SOON: "That was too soon after the last volume change, {sir} - nothing was changed.",
    NO_DEVICE: "I can't find your speakers, {sir} - nothing was changed.",
    NO_PLAYER: "Nothing is playing, {sir} - nothing was changed.",
    NOT_TAKEN: "The player didn't respond, {sir} - nothing was changed.",
    TIMERS_FULL: f"{MAX_TIMERS} timers are running already, {{sir}} - nothing was set.",
    NO_TIMER: "There's no timer running, {sir}.",
    NO_WINDOW: "There's no window to capture, {sir} - nothing was saved.",
    HUD_SHOWS: "I couldn't keep Jarvis out of the picture, {sir} - nothing was saved.",
    NO_FOLDER: "Your screenshots folder can't be used, {sir} - nothing was saved.",
}
_SHAPE_WORDS = {
    dv.VOLUME: "The volume goes from 0 to 100, {sir} - nothing was changed.",
    dv.TIMER: "Timers run from 1 to 240 minutes, {sir} - nothing was set.",
    dv.FOCUS_MODE: "Focus mode runs from 1 to 240 minutes, {sir} - nothing was changed.",
}
_FAILED_WORDS = {
    dv.VOLUME: "Windows didn't change the volume, {sir}.",
    dv.MEDIA: "Windows couldn't reach the player, {sir}.",
    dv.SCREENSHOT: "Windows couldn't take the screenshot, {sir}.",
}


def _minutes(count: int) -> str:
    return f"{count} minute{'' if count == 1 else 's'}"


def change_words(change: PcChange | None, address: str = "sir") -> str:
    """Jarvis's sentence for what came of a PC control (the HUD's caption): British and short, a level or a count at
    most - never a label, a path or a window's title. "" for None."""
    if change is None:
        return ""
    kind, action, code = change.kind, change.action, change.code
    if code == OS_ERROR:
        template = _FAILED_WORDS.get(kind, "Windows couldn't do that, {sir}.")
    elif code == dv.SHAPE:
        template = _SHAPE_WORDS.get(kind, "I can't do that as it was asked, {sir} - nothing was changed.")
    elif code:
        template = _NOT_DONE_WORDS.get(code) or "That couldn't be done, {sir} - nothing was changed."
    elif kind == dv.VOLUME and action in ("set", "up", "down"):
        level = change.after.level if isinstance(change.after, AudioState) else 0
        if change.changed:
            template = f"Volume {level}, {{sir}}."
        elif action == "set":
            template = f"The volume is already {level}, {{sir}}."
        else:
            template = f"The volume is already at {level}, {{sir}}."
    elif kind == dv.MEDIA and change.via == VIA_KEYS:
        template = _KEY_WORDS.get(action, "Pressed the media key, {sir} - it can't be put back.")
    elif not change.changed and (kind, action) in _ALREADY_WORDS:
        template = _ALREADY_WORDS[(kind, action)]
    elif kind == dv.TIMER:
        minutes = change.after.minutes if isinstance(change.after, Timer) else 0
        template = f"Timer set for {_minutes(minutes)}, {{sir}}."
    elif kind == dv.CANCEL_TIMER:
        count = len(change.before or ())
        template = "Timer cancelled, {sir}." if count == 1 else f"{count} timers cancelled, {{sir}}."
    elif kind == dv.FOCUS_MODE and isinstance(change.after, FocusDone):
        return focus_words(change.after, address)
    else:
        template = _DONE_WORDS.get((kind, action), "Done, {sir}.")
    return with_address(template, address)


def focus_words(done: FocusDone, address: str = "sir") -> str:
    """Focus mode's caption as it starts: "Focus mode is on, sir. I've paused the music and turned the volume down to
    20. I'll tell you when the 30 minutes are up." - what it left alone said plainly; counts and levels only."""
    did: list[str] = []
    for change in done.changes:
        if change.kind == dv.MEDIA:
            did.append("paused the music")
        elif change.kind == dv.VOLUME and isinstance(change.after, AudioState):
            did.append(f"turned the volume down to {change.after.level}")
    text = "Focus mode starts again, {sir}." if done.restarted else "Focus mode is on, {sir}."
    if did:
        text += f" I've {' and '.join(did)}."
    if done.music == UNKNOWN:
        text += " I can't tell if music is playing, so I left it."
    elif done.music == PLAYING:
        text += " The player didn't pause, so the music is still on."
    minutes = done.session.minutes
    text += " I'll tell you when the minute is up." if minutes == 1 else \
        f" I'll tell you when the {minutes} minutes are up."
    return with_address(text, address)


def focus_end_words(session: FocusSession, late_s: float = 0.0, address: str = "sir", hour24: bool = False) -> str:
    """The toast at focus mode's end: "Your 30-minute focus is up, sir." (noticed late: when it ended)."""
    span = f"{session.minutes}-minute"
    if late_s > LATE_WORDS_S:
        return with_address(f"Your {span} focus ended at {clock_text(_local(session.ends_at), hour24)}, {{sir}}.",
                            address)
    return with_address(f"Your {span} focus is up, {{sir}}.", address)


def focus_chip_text(session: FocusSession, hour24: bool = False) -> str:
    """The HUD's focus chip: "Focus \u00b7 ends 10:30 AM" (static: no seconds ticking - only the orb loops)."""
    return f"Focus \u00b7 ends {clock_text(_local(session.ends_at), hour24)}"


def dnd_chip_text(state: str) -> str:
    """The Do Not Disturb chip: "On" only when Windows was read back as on, else the opener's "Turn on"."""
    return "Do Not Disturb \u00b7 On" if state == DND_ON else "Do Not Disturb \u00b7 Turn on"


def chip_of(change: PcChange) -> str:
    """The HUD chip a change shows on (INSTANT_KINDS; "" for none)."""
    return _CHIP_OF.get(change.kind, "")


_MUSIC_CHIP = {(VIA_PLAYER, "pause"): "Music \u00b7 paused", (VIA_PLAYER, "play"): "Music \u00b7 playing",
               (VIA_KEYS, "pause"): "Music \u00b7 play/pause pressed",
               (VIA_KEYS, "play"): "Music \u00b7 play/pause pressed"}


def chip_words(kind: str, changes: Sequence[PcChange], hour24: bool = False) -> str:
    """What a chip of instant changes says (its words, then the HUD's Undo): the newest change of its ``kind``
    ("Volume \u00b7 20%", "Music \u00b7 paused", "Timer set \u00b7 10 min", "Screenshot \u00b7 saved", "Focus \u00b7
    ends 10:30 AM"); "" when it has none. Levels, states and counts only - never a label or a path."""
    mine = [change for change in changes if chip_of(change) == kind]
    if not mine:
        return ""
    newest = mine[-1]
    if kind == CHIP_FOCUS:
        done = newest.after
        return focus_chip_text(done.session, hour24) if isinstance(done, FocusDone) else "Focus"
    if kind == CHIP_VOLUME:
        after = newest.after if isinstance(newest.after, AudioState) else None
        if after is None:
            return "Volume"
        return "Volume \u00b7 muted" if after.muted else f"Volume \u00b7 {after.level}%"
    if kind == CHIP_MUSIC:
        if newest.action in ("next", "previous"):
            return f"Music \u00b7 {newest.action} track"
        return _MUSIC_CHIP.get((newest.via, newest.action), "Music")
    if kind == CHIP_TIMERS:
        if newest.kind == dv.TIMER:
            minutes = newest.after.minutes if isinstance(newest.after, Timer) else 0
            return f"Timer set \u00b7 {minutes} min"
        count = len(newest.before or ())
        return "Timer cancelled" if count == 1 else f"{count} timers cancelled"
    if kind == CHIP_SHOTS:
        return "Screenshot \u00b7 saved" if len(mine) == 1 else f"{len(mine)} screenshots \u00b7 saved"
    return ""


def can_undo(change: PcChange) -> bool:
    """Whether Undo may put ``change`` back (False: the media key's, whose state was never known)."""
    return bool(change.changed and change.undoable)


_PUT_BACK = {dv.MEDIA: "music", dv.VOLUME: "volume", dv.TIMER: "timer", dv.CANCEL_TIMER: "timers",
             dv.SCREENSHOT: "screenshot"}
_LEFT_WORDS = {
    CHANGED: "The {noun} changed since - left as it is.",
    TOO_SOON: "The volume changed a moment ago - try Undo again.",
    UNAVAILABLE: "I can't reach this PC's controls right now.",
    NOT_TAKEN: "The player didn't respond.",
    STOPPED: "Stopped before the {noun}.",
    GONE: "The {noun} had gone already.",
}
_GONE_WORDS = {dv.TIMER: "That timer had ended already.", dv.CANCEL_TIMER: "Those timers had ended already."}
NO_BIN_SHOT_WORDS = "The screenshot stays - the Recycle Bin can't be reached right now."
_CANT_WORDS = {dv.MEDIA: "What the media key did can't be put back."}
_PART_ORDER = {dv.MEDIA: 0, dv.VOLUME: 1, dv.TIMER: 2, dv.CANCEL_TIMER: 3, dv.SCREENSHOT: 4}   # as SPEC 10.3 says it
_OS_WORDS = {dv.VOLUME: "Windows didn't put the volume back.", dv.MEDIA: "Windows couldn't reach the player.",
             dv.SCREENSHOT: "Windows couldn't put the screenshot in the Recycle Bin."}


def _back_part(change: PcChange, result: PcChange) -> str:
    """What a change put back is now, in a word or two ("music playing", "volume 60%", "timer cancelled")."""
    if change.kind == dv.VOLUME:
        state = result.after if isinstance(result.after, AudioState) else result.before
        if not isinstance(state, AudioState):
            return "volume"
        return "sound muted" if state.muted else f"volume {state.level}%"
    if change.kind == dv.MEDIA:
        state = result.after if result.after in (PLAYING, PAUSED) else change.before
        return "music playing" if state == PLAYING else "music paused"
    if change.kind == dv.TIMER:
        return "timer cancelled"
    if change.kind == dv.CANCEL_TIMER:
        count = len(result.after or ())
        return "timer back" if count == 1 else f"{count} timers back"
    if change.kind == dv.SCREENSHOT:
        return "screenshot in the Recycle Bin"
    return ""


def undo_summary(results: Sequence[tuple[PcChange, PcChange]], address: str = "sir") -> str:
    """Jarvis's words for an Undo of the HUD's chips (one chip's, or Undo all's): what came back - "Focus ended, sir.
    Back as it was: music playing, volume 60%." - then, plainly, what did not ("The screenshot stays - the Recycle Bin
    can't be reached right now."). Kinds, levels and counts only."""
    from . import fileops

    focus_ended = any(change.kind == dv.FOCUS_MODE and not result.code for change, result in results)
    parts: list[tuple[int, str]] = []
    left: list[str] = []
    for change, result in results:
        if change.kind == dv.FOCUS_MODE:
            continue
        if not result.code:
            part = _back_part(change, result)
            if part and part not in (said for _rank, said in parts):
                parts.append((_PART_ORDER.get(change.kind, 9), part))
            continue
        noun = _PUT_BACK.get(change.kind, "change")
        if result.code in fileops.UNDO_WORDS and change.kind == dv.SCREENSHOT:
            words = NO_BIN_SHOT_WORDS if result.code == fileops.R_NO_BIN else fileops.UNDO_WORDS[result.code]
        elif result.code == CANT_UNDO:
            words = _CANT_WORDS.get(change.kind, f"The {noun} can't be put back.")
        elif result.code == OS_ERROR:
            words = _OS_WORDS.get(change.kind, "Windows couldn't put it back.")
        elif result.code == GONE and change.kind in _GONE_WORDS:
            words = _GONE_WORDS[change.kind]
        else:
            words = _LEFT_WORDS.get(result.code, "The {noun} couldn't be put back.").format(noun=noun)
        if words not in left:
            left.append(words)
    back = ", ".join(part for _rank, part in sorted(parts, key=lambda item: item[0]))   # music, volume, timers ...
    if focus_ended:
        text = "Focus ended, {sir}." + (f" Back as it was: {back}." if back else "")
    elif back:
        text = f"Back as it was, {{sir}}: {back}."
    else:
        text = "Nothing was put back, {sir}." if left else "Back as it was, {sir}."
    if left:
        text += " " + " ".join(left)
    return with_address(text, address)


def undo_note(change: PcChange, result: PcChange) -> str:
    """An Undo's line in Live: "volume - put back" / "media - left: changed" (kinds and codes only)."""
    kind = change.kind + (" (focus)" if change.group and change.kind != dv.FOCUS_MODE else "")
    return f"{kind} - put back" if not result.code else f"{kind} - left: {result.code}"


def undo_words(change: PcChange, address: str = "sir") -> str:
    """An Undo's words: "Put back, sir." or why not (a screenshot the bin didn't take: "The Recycle Bin is off for
    that drive - left as it is.")."""
    from . import fileops

    if not change.code:
        return with_address("Put back, {sir}.", address)
    if change.code in fileops.UNDO_WORDS:
        return fileops.UNDO_WORDS[change.code]
    reason = code_words(change.code) or "it couldn't be put back"
    return with_address(f"Not put back, {{sir}} - {reason}.", address)


def end_words(timer: Timer, late_s: float = 0.0, address: str = "sir", hour24: bool = False) -> str:
    """The toast at a timer's end: "Your 10-minute timer is up, sir." (its label after a dash), or, noticed late
    (Jarvis was closed, or the PC asleep), when it ended. A reminder: "Your 5:00 PM reminder, sir." (its label after a
    dash)."""
    if timer.reminder:
        text = with_address(f"Your {clock_text(_local(timer.ends_at), hour24)} reminder, {{sir}}.", address)
        return f"{text[:-1]} - {timer.label}." if timer.label else text
    span = f"{timer.minutes}-minute"
    if late_s > LATE_WORDS_S:
        text = f"Your {span} timer ended at {clock_text(_local(timer.ends_at), hour24)}, {{sir}}."
    else:
        text = f"Your {span} timer is up, {{sir}}."
    text = with_address(text, address)
    return f"{text[:-1]} - {timer.label}." if timer.label else text


def chip_text(timers: Sequence[Timer], hour24: bool = False) -> str:
    """The HUD chip while a timer runs: "Timer \u00b7 ends 10:32 AM" ("2 timers \u00b7 next ends 10:32 AM"); "" for
    none. Static: no seconds ticking (only the orb loops). A reminder (instant answers' round 2) that comes first:
    "Reminder \u00b7 5:00 PM", with how many more after it ("\u00b7 2 more")."""
    if not timers:
        return ""
    first = min(timers, key=lambda timer: timer.ends_at)
    ends = clock_text(_local(first.ends_at), hour24)
    if first.reminder:
        others = len(timers) - 1
        return f"Reminder \u00b7 {ends}" + (f" \u00b7 {others} more" if others else "")
    only_timers = [timer for timer in timers if not timer.reminder]
    reminders = len(timers) - len(only_timers)
    text = f"Timer \u00b7 ends {ends}" if len(only_timers) == 1 else f"{len(only_timers)} timers \u00b7 next ends {ends}"
    return text + (f" \u00b7 {reminders} reminder{'s' if reminders != 1 else ''}" if reminders else "")


def live_note(change: Any) -> str:
    """A PC control's line in Live (what it did): levels, states, counts and kinds only - never a label or a path;
    "" when it was not done (the code says why)."""
    if not isinstance(change, PcChange) or change.code:
        return ""
    before, after = change.before, change.after
    if change.kind == dv.VOLUME and isinstance(before, AudioState):
        shown = after if isinstance(after, AudioState) else before
        note = f"level {before.level} -> {shown.level}"
        if shown.muted != before.muted:
            note += ", muted" if shown.muted else ", unmuted"
        return note
    if change.kind == dv.MEDIA:
        if change.via == VIA_KEYS:
            return "a media key: the player's state unknown - can't be put back"
        if change.via == VIA_PLAYER:
            return f"the player's controls: {before or UNKNOWN} -> {after or before or UNKNOWN}"
        return ""
    if change.kind == dv.TIMER and isinstance(after, Timer):
        return f"{_minutes(after.minutes)}"
    if change.kind == dv.CANCEL_TIMER and change.changed:
        return f"{len(before or ())} cancelled"
    if change.kind == dv.SCREENSHOT and isinstance(after, Shot):
        return f"{'the screen' if after.mode == SCREEN else 'the active window'}, 1 file saved on this PC"
    if change.kind == dv.FOCUS_MODE and isinstance(after, FocusDone):
        notes = [f"{_minutes(after.session.minutes)}{' again' if after.restarted else ''}", "Jarvis quiet"]
        for sub in after.changes:
            if sub.kind == dv.MEDIA:
                notes.append("the player paused")
            elif isinstance(sub.before, AudioState) and isinstance(sub.after, AudioState):
                notes.append(f"level {sub.before.level} -> {sub.after.level}")
        if after.music == UNKNOWN:
            notes.append("the player's state unknown - left alone")
        return ", ".join(notes)
    return ""
