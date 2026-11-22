"""Jarvis's soft sounds, made in code (the new look; design contract section 7).

No sound file is in the repository. Each sound is built here from its recipe (sine tones and a glass-like voice,
standard library only, 48 kHz mono 16-bit), written once to the data folder's ``sounds`` folder as
``sound_<name>_v<RECIPE_VERSION>.wav`` and played through a preloaded ``QSoundEffect``. So a sound never cuts
"Hey Jarvis"'s chime, which keeps winsound and its own rules (wake_ui).

Events (``SoundBoard.play(event, variant)``)
---------------------------------------------
- ``HEARD``: the talk key or "Hey Jarvis" opened listening (skipped within 1 s of the wake chime).
- ``THINKING``: a request went in and no answer came within 400 ms.
- ``FRAME``: a frame finished drawing in (the first four of a group, one step up a scale each: ``variant`` 0-3).
- ``SETTLED``: the last frame of a group of two or more landed.
- ``WAITS``: a new item waits on the owner (amber).
- ``CHANGED``: a changed figure's light started.
- ``DONE``: an approved card's action finished.
- ``FAILED``: a frame or a card's action failed.
- ``OPEN_BIG`` / ``CLOSE_BIG``: a frame opened big / went back.

When a sound plays
------------------
``play`` returns "" when the sound played, else why it did not (logged at debug level as the event and that word,
never anything else). In order:

- ``tests``: never while the tests run (JARVIS_DESKTOP_TEST_GUARD=1): no test ever touches an audio device;
- ``off``: [look] sounds = "off", or Settings > Look's Off on this PC (``enabled()`` is False);
- ``loading``: the sounds are still being built (a worker thread at start), or the player could not load one;
- the host's ``quiet()`` answer (muted, quiet hours, focus mode, a meeting records, the PC is locked, Windows says
  busy or a full-screen app runs);
- ``speaking``: Jarvis speaks or a briefing plays, or did within the last 250 ms (``set_speaking``); the sound is
  dropped, never queued;
- ``hidden``: a board sound (FRAME, SETTLED, CHANGED, OPEN_BIG, CLOSE_BIG) while the window is not visible;
- ``motion``: FRAME and CHANGED while motion is off (they accompany motion);
- ``gate``: within 120 ms of the last sound;
- ``repeat``: the same event within 2 s.

Every recipe's peak is at most ``MAX_PEAK`` (0.08 of full scale). Qt-free apart from ``SoundBoard``.
"""

from __future__ import annotations

import io
import logging
import math
import os
import threading
import time
import wave as wave_mod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QUrl, Signal

logger = logging.getLogger(__name__)

TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"
RATE = 48_000                     # samples a second (mono, 16-bit)
RECIPE_VERSION = 1                # bump when a recipe changes: the files are written again
MAX_PEAK = 0.08                   # no sound is louder than this (of full scale)
FOLDER = "sounds"                 # under the data folder
FADE_MS = 4.0                     # every note ends with this fade (no click)
GATE_S = 0.12                     # no two sounds closer than this
REPEAT_S = 2.0                    # the same event at most this often
SPEECH_TAIL_S = 0.25              # nothing this soon after Jarvis's speech or a briefing stops
HEARD_AFTER_CHIME_S = 1.0         # "Hey Jarvis"'s own chime already said it heard

HEARD, THINKING, FRAME, SETTLED, WAITS = "heard", "thinking", "frame", "settled", "waits"
CHANGED, DONE, FAILED, OPEN_BIG, CLOSE_BIG = "changed", "done", "failed", "open_big", "close_big"
EVENTS = (HEARD, THINKING, FRAME, SETTLED, WAITS, CHANGED, DONE, FAILED, OPEN_BIG, CLOSE_BIG)
BOARD_EVENTS = frozenset({FRAME, SETTLED, CHANGED, OPEN_BIG, CLOSE_BIG})   # silent while the window is hidden
MOTION_EVENTS = frozenset({FRAME, CHANGED})                                # silent while motion is off
FRAME_STEPS = (1.0, 9 / 8, 5 / 4, 3 / 2)                                   # a frame's step up the scale, by order

SINE, GLASS = "sine", "glass"
# The glass voice: partials at these ratios and levels; each decays this fraction of the note's own tau.
GLASS_PARTIALS = ((1.0, 1.0, 1.0), (2.76, 0.30, 0.5), (5.40, 0.12, 0.28))

# Why a sound did not play (``SoundBoard.play``'s answers).
R_TESTS, R_OFF, R_LOADING, R_SPEAKING, R_HIDDEN, R_MOTION = "tests", "off", "loading", "speaking", "hidden", "motion"
R_GATE, R_REPEAT, R_UNKNOWN = "gate", "repeat", "unknown"


@dataclass(frozen=True)
class Note:
    """One tone of a recipe. Times in ms from the sound's start; ``tau`` is the exponential decay after the
    attack and hold; ``glide_to`` (Hz, 0 for none) slides the pitch linearly over the note."""

    freq: float
    start: float
    length: float
    gain: float = 1.0
    attack: float = 4.0
    hold: float = 0.0
    tau: float = 40.0
    voice: str = SINE
    glide_to: float = 0.0


@dataclass(frozen=True)
class Recipe:
    notes: tuple[Note, ...]
    peak: float

    @property
    def length_ms(self) -> float:
        return max(note.start + note.length for note in self.notes)


def _glide(freq: float, to: float, start: float, length: float, **kw: Any) -> Note:
    return Note(freq, start, length, glide_to=to, **kw)


RECIPES: dict[str, Recipe] = {
    HEARD: Recipe((Note(990.0, 0, 55, tau=40), Note(1320.0, 55, 55, tau=40)), peak=0.07),
    THINKING: Recipe((Note(330.0, 0, 80, attack=12, tau=60), Note(247.0, 80, 80, attack=12, tau=60)), peak=0.05),
    SETTLED: Recipe((Note(440.0, 0, 900, gain=1.0, attack=30, hold=60, tau=220),
                     Note(660.0, 0, 900, gain=0.6, attack=30, hold=60, tau=220),
                     Note(990.0, 0, 900, gain=0.35, attack=30, hold=60, tau=220)), peak=0.06),
    WAITS: Recipe((Note(660.0, 0, 120, tau=90), Note(495.0, 120, 120, tau=90)), peak=0.08),
    CHANGED: Recipe((Note(2093.0, 0, 600, tau=220, voice=GLASS), Note(2637.0, 30, 600, tau=220, voice=GLASS),
                     Note(3136.0, 60, 600, tau=220, voice=GLASS)), peak=0.035),
    DONE: Recipe((Note(1320.0, 0, 600, tau=160, voice=GLASS), Note(1760.0, 0, 600, tau=160, voice=GLASS)),
                 peak=0.05),
    FAILED: Recipe((Note(330.0, 0, 700, attack=20, tau=160), Note(349.0, 0, 700, attack=20, tau=160)), peak=0.06),
    OPEN_BIG: Recipe((_glide(520.0, 780.0, 0, 160, attack=10, hold=60, tau=60),), peak=0.04),
    CLOSE_BIG: Recipe((_glide(780.0, 520.0, 0, 160, attack=10, hold=60, tau=60),), peak=0.04),
}
for _step, _ratio in enumerate(FRAME_STEPS):
    RECIPES[f"{FRAME}{_step}"] = Recipe((Note(1760.0 * _ratio, 0, 200, tau=90, voice=GLASS),), peak=0.035)


def sound_names() -> tuple[str, ...]:
    """Every sound built: the events, with the frame's four steps as frame0-frame3."""
    return tuple(name for name in RECIPES)


def sound_name(event: str, variant: int = 0) -> str:
    """The recipe an event plays (FRAME by its step, 0-3; other events have one)."""
    if event == FRAME:
        return f"{FRAME}{max(0, min(len(FRAME_STEPS) - 1, int(variant)))}"
    return event


def _envelope(note: Note, t_ms: float) -> float:
    if t_ms < 0.0 or t_ms >= note.length:
        return 0.0
    if t_ms < note.attack:
        level = t_ms / note.attack
    elif t_ms < note.attack + note.hold:
        level = 1.0
    else:
        level = math.exp(-(t_ms - note.attack - note.hold) / max(1e-6, note.tau))
    left = note.length - t_ms
    if left < FADE_MS:
        level *= left / FADE_MS
    return level


def render(recipe: Recipe) -> list[float]:
    """The sound as samples in -1..1, scaled so its loudest sample is exactly ``recipe.peak``."""
    count = int(round(recipe.length_ms * RATE / 1000.0))
    out = [0.0] * count
    for note in recipe.notes:
        first = int(round(note.start * RATE / 1000.0))
        last = min(count, first + int(round(note.length * RATE / 1000.0)))
        partials = GLASS_PARTIALS if note.voice == GLASS else ((1.0, 1.0, 1.0),)
        for ratio, level, decay in partials:
            shaped = Note(note.freq, 0.0, note.length, attack=note.attack, hold=note.hold, tau=note.tau * decay)
            phase = 0.0
            span = max(1, last - first)
            for index in range(first, last):
                local = index - first
                freq = note.freq
                if note.glide_to:
                    freq = note.freq + (note.glide_to - note.freq) * (local / span)
                phase += 2.0 * math.pi * freq * ratio / RATE
                out[index] += note.gain * level * _envelope(shaped, local * 1000.0 / RATE) * math.sin(phase)
    loudest = max((abs(sample) for sample in out), default=0.0)
    if loudest <= 0.0:
        return out
    scale = min(recipe.peak, MAX_PEAK) / loudest
    return [sample * scale for sample in out]


def wav_bytes(samples: list[float]) -> bytes:
    """``samples`` (-1..1) as a 48 kHz mono 16-bit WAV file."""
    buffer = io.BytesIO()
    with wave_mod.open(buffer, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(b"".join(int(round(max(-1.0, min(1.0, s)) * 32767)).to_bytes(2, "little", signed=True)
                                for s in samples))
    return buffer.getvalue()


def file_name(name: str) -> str:
    return f"sound_{name}_v{RECIPE_VERSION}.wav"


WAV_HEADER = 44                   # a 48 kHz mono 16-bit WAV file's header (the wave module's), before the samples


def file_size(recipe: Recipe) -> int:
    """The size of ``recipe``'s WAV file, known without rendering it: the header and two bytes a sample."""
    return WAV_HEADER + 2 * int(round(recipe.length_ms * RATE / 1000.0))


def build_files(folder: Path, *, rendered: list[str] | None = None) -> dict[str, Path]:
    """Every sound written to ``folder`` (only where missing or a different size), older versions removed; the
    paths by name. A sound is rendered only when its file of this recipe version is missing or not the size its recipe
    gives (the final review's resilience 4: a normal start renders nothing); ``rendered`` collects the names it did.
    Raises OSError when the folder cannot be written."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    keep = set()
    for name, recipe in RECIPES.items():
        path = folder / file_name(name)
        keep.add(path.name)
        if not path.is_file() or path.stat().st_size != file_size(recipe):
            path.write_bytes(wav_bytes(render(recipe)))
            if rendered is not None:
                rendered.append(name)
        paths[name] = path
    for old in folder.glob("sound_*_v*.wav"):     # only Jarvis's own sounds, of another recipe version
        if old.name not in keep:
            try:
                old.unlink()
            except OSError:
                pass
    return paths


def guarded() -> bool:
    """The tests run (JARVIS_DESKTOP_TEST_GUARD=1): no sound is ever played."""
    return os.environ.get(TEST_GUARD_ENV, "") == "1"


class _QtPlayer:
    """One preloaded QSoundEffect per sound (GUI thread)."""

    def __init__(self, paths: dict[str, Path], parent: QObject) -> None:
        from PySide6.QtMultimedia import QSoundEffect

        self._effects: dict[str, Any] = {}
        for name, path in paths.items():
            effect = QSoundEffect(parent)
            effect.setSource(QUrl.fromLocalFile(str(path)))
            effect.setVolume(1.0)              # the level is in the samples
            self._effects[name] = effect

    def play(self, name: str) -> bool:
        effect = self._effects.get(name)
        if effect is None:
            return False
        from PySide6.QtMultimedia import QSoundEffect

        if effect.status() != QSoundEffect.Status.Ready:
            return False
        effect.play()
        return True


class SoundBoard(QObject):
    """Plays Jarvis's soft sounds under the rules in the module doc. GUI thread, except the build at ``start``."""

    readyChanged = Signal(bool)
    _built = Signal(object)                     # (paths or None), from the worker thread

    def __init__(self, folder: Path | str, *, quiet: Callable[[], str] | None = None,
                 enabled: Callable[[], bool] | None = None, visible: Callable[[], bool] | None = None,
                 motion_off: Callable[[], bool] | None = None, clock: Callable[[], float] = time.monotonic,
                 player_factory: Callable[[dict[str, Path], QObject], Any] | None = None,
                 test_guard: bool | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._folder = Path(folder)
        self._quiet = quiet or (lambda: "")
        self._enabled = enabled or (lambda: True)
        self._visible = visible or (lambda: True)
        self._motion_off = motion_off or (lambda: False)
        self._clock = clock
        self._factory = player_factory or (lambda paths, owner: _QtPlayer(paths, owner))
        self._guard = guarded() if test_guard is None else bool(test_guard)
        self._player: Any = None
        self._started = False
        self._speaking = False
        self._speech_end = -1e9
        self._chime_at = -1e9
        self._last_any = -1e9
        self._last: dict[str, float] = {}
        self._built.connect(self._on_built)

    # ---- set-up ----------------------------------------------------------------------------

    def start(self) -> None:
        """Build the sounds on a worker thread (once); they play once ready. Nothing at all under the test guard."""
        if self._started or self._guard:
            return
        self._started = True
        thread = threading.Thread(target=self._build, name="jarvis-sounds", daemon=True)
        thread.start()

    def can_start(self) -> bool:
        """``start`` would build now: not started yet, and not under the test guard."""
        return not self._started and not self._guard

    def _build(self) -> None:
        began = time.monotonic()
        rendered: list[str] = []
        try:
            paths = build_files(self._folder, rendered=rendered)
        except Exception as exc:  # noqa: BLE001 - no sounds this run; Jarvis works as before
            logger.info("Sounds: not built (%s)", type(exc).__name__)
            paths = None
        else:
            logger.info("Sounds: %d ready, %d built in %.2f s", len(paths), len(rendered), time.monotonic() - began)
        self._built.emit(paths)

    def _on_built(self, paths: object) -> None:
        if not isinstance(paths, dict):
            self.readyChanged.emit(False)
            return
        self.use_files(paths)

    def use_files(self, paths: dict[str, Path]) -> None:
        """Play from these files (``start`` calls this; tests give their own player)."""
        try:
            self._player = self._factory(paths, self)
        except Exception as exc:  # noqa: BLE001 - no player: silent
            logger.info("Sounds: no player (%s)", type(exc).__name__)
            self._player = None
        self.readyChanged.emit(self._player is not None)

    @property
    def ready(self) -> bool:
        return self._player is not None

    # ---- what the app tells it -------------------------------------------------------------

    def set_speaking(self, speaking: bool) -> None:
        """Jarvis's voice or a briefing plays (True) or stopped (False: the 250 ms tail starts)."""
        speaking = bool(speaking)
        if self._speaking and not speaking:
            self._speech_end = self._clock()
        self._speaking = speaking

    def note_wake_chime(self) -> None:
        """"Hey Jarvis"'s chime just played: HEARD is skipped for a second."""
        self._chime_at = self._clock()

    # ---- playing ---------------------------------------------------------------------------

    def held(self, event: str) -> str:
        """Why ``event`` would not play now ("" when it would), without playing it or counting it."""
        if event not in EVENTS:
            return R_UNKNOWN
        if self._guard:
            return R_TESTS
        try:
            if not self._enabled():
                return R_OFF
        except Exception:  # noqa: BLE001 - unknown: off
            return R_OFF
        if self._player is None:
            return R_LOADING
        try:
            reason = str(self._quiet() or "")
        except Exception:  # noqa: BLE001 - unknown: quiet
            reason = "quiet"
        if reason:
            return reason
        now = self._clock()
        if self._speaking or now - self._speech_end < SPEECH_TAIL_S:
            return R_SPEAKING
        if event in BOARD_EVENTS:
            try:
                if not self._visible():
                    return R_HIDDEN
            except Exception:  # noqa: BLE001 - unknown: hidden
                return R_HIDDEN
        if event in MOTION_EVENTS:
            try:
                if self._motion_off():
                    return R_MOTION
            except Exception:  # noqa: BLE001
                return R_MOTION
        if event == HEARD and now - self._chime_at < HEARD_AFTER_CHIME_S:
            return R_REPEAT
        if now - self._last_any < GATE_S:
            return R_GATE
        if now - self._last.get(event, -1e9) < REPEAT_S:
            return R_REPEAT
        return ""

    def play(self, event: str, variant: int = 0) -> str:
        """Play ``event`` now if nothing holds it (see the module doc): "" when it played, else the reason."""
        reason = self.held(event)
        if not reason:
            try:
                played = bool(self._player.play(sound_name(event, variant)))
            except Exception:  # noqa: BLE001 - the player failed: silent this time
                played = False
            if played:
                now = self._clock()
                self._last_any = now
                self._last[event] = now
                return ""
            reason = R_LOADING
        logger.debug("Sounds: %s held (%s)", event, reason)
        return reason
