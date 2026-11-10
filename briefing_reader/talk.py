"""Talking to Jarvis: the part with no Qt and no device (owner decisions 2026-11-09).

The talk key ([talk] key, Ctrl+Alt+Space) opens the microphone for one request. The owner speaks, and a short
quiet ends the talk (``quiet_ms``, [talk] quiet_ms; a little longer after a short start, when people pause
mid-thought). A second tap ends it at once, and Esc cancels it. What was heard goes in as if typed
(AskController.submit_spoken): the same routes and the same gates, so cards still need a click - voice never
approves or undoes.

* :class:`Endpointer` decides when a talk ends, from 16 kHz mono 16-bit audio in 30 ms frames. A frame is speech
  when it is well above the room's level (the room is its quietest frames, learned as the talk goes, creeping up
  slowly while nothing is said), and quiet otherwise. It also gives the microphone's level (0..1) for the orb,
  whether the microphone seemed muted, and the stretch worth understanding (``span``: a second before the speech
  started, to its last speech and a little after).
* :func:`clean_text` keeps what the speech model heard with some confidence: a segment the model itself rates as
  no speech, or barely words, is dropped, and so are its sound tags ("[Music]", "(upbeat music)", "♪").
* :func:`hint_prompt` is the short list of names the model should expect ("Jarvis" first), so "Discord" is not
  written as "this cord".

Privacy: the audio stays in memory and is never written to disk, and nothing here logs what was said.
"""

from __future__ import annotations

import math
import re
import sys
from array import array
from collections.abc import Iterable
from typing import Any

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000          # 480
FRAME_BYTES = FRAME_SAMPLES * BYTES_PER_SAMPLE

QUIET_MS = 500                 # owner decision: about half a second of quiet ends a talk ([talk] quiet_ms)
SHORT_QUIET_EXTRA_MS = 250     # ... a quarter of a second longer after a short start ...
SHORT_SPEECH_MS = 600          # ... (less speech than this so far: "open", a pause, "Spotify")
SPEECH_START_MS = 120          # this much speech in a row starts the request (a click or a knock is shorter)
NO_SPEECH_S = 8.0              # nothing said this long after the key: "I didn't catch that"
MAX_TALK_S = 30.0              # a talk's cap
BEFORE_SPEECH_MS = 1_000       # kept before the speech the endpointer found (a soft first word it missed)
AFTER_SPEECH_MS = 200          # ... and after its last speech

SILENCE_DB = -100.0            # an all-zero frame
MUTED_DB = -85.0               # a talk whose loudest frame stayed below this: a muted microphone
FLOOR_MIN_DB, FLOOR_MAX_DB = -75.0, -30.0      # the room's level stays in here
FLOOR_START_MAX_DB = -40.0     # the room is taken as no louder than this at first, so words from the first frame
                               # count (a talk may start mid-word); a fan this loud still never starts one
SPEECH_ABOVE_DB = 12.0         # speech: this far above the room ...
SPEECH_MIN_DB = -48.0          # ... and never quieter than this
FLOOR_RISE_DB = 0.05           # the room creeps up this much a frame while nothing loud is heard (a fan coming on)
LEVEL_LOW_DB, LEVEL_HIGH_DB = -60.0, -10.0     # the orb's level: 0 at the first, 1 at the second

END_QUIET = "quiet"            # speech, then quiet: what was said goes in
END_CAP = "cap"                # MAX_TALK_S: what was said goes in
END_OWNER = "owner"            # a second tap: what was said goes in
END_NO_SPEECH = "no_speech"    # NO_SPEECH_S and nothing said: "I didn't catch that"
END_CANCEL = "cancel"          # Esc or Stop: nothing goes in
END_DEVICE = "device"          # the microphone stopped: nothing goes in
END_REASONS = (END_QUIET, END_CAP, END_OWNER, END_NO_SPEECH, END_CANCEL, END_DEVICE)
SAID_ENDS = frozenset({END_QUIET, END_CAP, END_OWNER})   # the ends after which what was heard is understood

NO_SPEECH_PROB = 0.6           # clean_text: a segment the model rates as no speech ...
LOW_LOGPROB = -0.7             # ... with words it is unsure of, is dropped ...
MIN_LOGPROB = -1.2             # ... and so is one with barely any words at all
HINT_NAMES = ("Jarvis",)
HINT_PROMPT_CHARS = 200
_TAG_RE = re.compile(r"\[[^\]]{0,60}\]|\([^)]{0,60}\)|[♪♫]+")
_NO_WORDS_RE = re.compile(r"[\W_]*")


def frame_db(pcm: bytes) -> float:
    """The loudness of 16-bit samples in dB below full scale (SILENCE_DB for none or all zero)."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return SILENCE_DB
    total = sum(sample * sample for sample in samples)
    if not total:
        return SILENCE_DB
    return max(SILENCE_DB, 20.0 * math.log10(math.sqrt(total / len(samples)) / 32768.0))


def level_of(db: float) -> float:
    """dB below full scale -> the orb's 0..1."""
    return min(1.0, max(0.0, (db - LEVEL_LOW_DB) / (LEVEL_HIGH_DB - LEVEL_LOW_DB)))


class Endpointer:
    """When a talk ends (see the module docstring). ``feed(pcm)`` takes 16 kHz mono 16-bit audio in any size of
    piece and returns "" while the talk goes on, else its end reason (END_*, the same from then on); ``end(reason)``
    ends it from outside (END_OWNER, END_CANCEL, END_DEVICE). ``level`` (0..1, the last frame), ``muted``,
    ``heard_speech`` and ``span()`` read it."""

    def __init__(self, *, quiet_ms: int = QUIET_MS, no_speech_s: float = NO_SPEECH_S,
                 max_s: float = MAX_TALK_S) -> None:
        self.quiet_ms = int(quiet_ms)
        self._no_speech_frames = max(1, round(no_speech_s * 1000 / FRAME_MS))
        self._max_frames = max(1, round(max_s * 1000 / FRAME_MS))
        self._pending = bytearray()
        self.frames = 0                 # whole frames read
        self.floor_db = 0.0             # the room's level (set by the first frame)
        self.peak_db = SILENCE_DB
        self.level = 0.0
        self.start_frame = -1           # the request's first frame of speech (-1: none yet)
        self.last_speech_frame = -1
        self.speech_frames = 0          # frames of speech since the start
        self._run = 0                   # loud frames in a row, before the start
        self._quiet_run = 0             # quiet frames in a row, after the start
        self.ended = ""

    @property
    def heard_speech(self) -> bool:
        return self.start_frame >= 0

    @property
    def muted(self) -> bool:
        """Nothing above a whisper of noise reached it: a muted microphone, or one that delivers zeros."""
        return self.frames > 0 and self.peak_db < MUTED_DB

    def feed(self, pcm: bytes) -> str:
        if self.ended:
            return self.ended
        self._pending += pcm
        while len(self._pending) >= FRAME_BYTES and not self.ended:
            frame = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            self._frame(frame_db(frame))
        return self.ended

    def end(self, reason: str) -> str:
        if not self.ended:
            self.ended = reason
        return self.ended

    def _quiet_needed_ms(self) -> int:
        short = self.speech_frames * FRAME_MS < SHORT_SPEECH_MS
        return self.quiet_ms + (SHORT_QUIET_EXTRA_MS if short else 0)

    def _frame(self, db: float) -> None:
        index = self.frames
        self.frames += 1
        self.peak_db = max(self.peak_db, db)
        self.level = level_of(db)
        if index == 0:
            self.floor_db = min(FLOOR_START_MAX_DB, max(FLOOR_MIN_DB, db))
        loud = db >= max(self.floor_db + SPEECH_ABOVE_DB, SPEECH_MIN_DB)
        if db < self.floor_db:
            self.floor_db = max(FLOOR_MIN_DB, db)          # the room is at least this quiet
        elif not loud:
            self.floor_db = min(FLOOR_MAX_DB, self.floor_db + FLOOR_RISE_DB)
        if self.start_frame < 0:
            self._run = self._run + 1 if loud else 0
            if self._run * FRAME_MS >= SPEECH_START_MS:
                self.start_frame = index - self._run + 1
                self.speech_frames = self._run
                self.last_speech_frame = index
            elif self.frames >= self._no_speech_frames:
                self.ended = END_NO_SPEECH
        elif loud:
            self.speech_frames += 1
            self.last_speech_frame = index
            self._quiet_run = 0
        else:
            self._quiet_run += 1
            if self._quiet_run * FRAME_MS >= self._quiet_needed_ms():
                self.ended = END_QUIET
        if not self.ended and self.frames >= self._max_frames:
            self.ended = END_CAP

    def span(self, total_bytes: int | None = None) -> tuple[int, int]:
        """(first byte, end byte) of the audio worth understanding, in the audio fed so far (``total_bytes``:
        its length, if more came than whole frames). With no speech found it is all of it: a second tap may end
        a talk spoken too softly to start one, and the model's own no-speech check has the last word."""
        fed = self.frames * FRAME_BYTES if total_bytes is None else int(total_bytes)
        if self.start_frame < 0:
            return 0, fed
        first = max(0, self.start_frame * FRAME_BYTES - BEFORE_SPEECH_MS * SAMPLE_RATE // 1000 * BYTES_PER_SAMPLE)
        last = min(fed, (self.last_speech_frame + 1) * FRAME_BYTES
                   + AFTER_SPEECH_MS * SAMPLE_RATE // 1000 * BYTES_PER_SAMPLE)
        return first, max(first, last)


def _field(segment: Any, name: str) -> Any:
    if isinstance(segment, dict):
        return segment.get(name)
    return getattr(segment, name, None)


def clean_text(segments: Iterable[Any]) -> str:
    """What the speech model heard, from its segments (objects or dicts with ``text``, ``no_speech_prob`` and
    ``avg_logprob``), without what it was unsure was speech and without sound tags; "" when nothing is left."""
    kept: list[str] = []
    for segment in segments:
        try:
            no_speech = float(_field(segment, "no_speech_prob") or 0.0)
            logprob = float(_field(segment, "avg_logprob") or 0.0)
        except (TypeError, ValueError):
            continue
        if (no_speech > NO_SPEECH_PROB and logprob < LOW_LOGPROB) or logprob <= MIN_LOGPROB:
            continue
        text = " ".join(_TAG_RE.sub(" ", str(_field(segment, "text") or "")).split())
        if text and not _NO_WORDS_RE.fullmatch(text):
            kept.append(text)
    return " ".join(kept).strip()


def hint_prompt(names: Iterable[str] = ()) -> str:
    """"Jarvis, Spotify, Discord." - the names the model should expect, each once, at most HINT_PROMPT_CHARS."""
    chosen: list[str] = []
    seen: set[str] = set()
    length = 0
    for name in (*HINT_NAMES, *names):
        text = " ".join(str(name).split())
        key = text.casefold()
        if not text or key in seen:
            continue
        if length + len(text) + 2 > HINT_PROMPT_CHARS:
            break
        seen.add(key)
        chosen.append(text)
        length += len(text) + 2
    return ", ".join(chosen) + "." if chosen else ""
