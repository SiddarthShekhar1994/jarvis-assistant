"""Jarvis's voice, made on this PC (owner decisions 2026-11-12): the app's side. No Qt, and nothing heavy is imported.

``[voice] engine = "local"`` (the default) makes the briefing and everything Jarvis says here, with Kokoro-82M run by
onnxruntime in a warm child process (local_voice_worker) and misaki turning words into sounds. When the local voice
can't run, the Windows voice (also on this PC) speaks instead - never Microsoft's online voice. ``engine = "online"``
is edge-tts as before. ``is_local(config)`` is the one test the private-line gates use: the Windows fallback is local
too, so it holds before the download as well.

What it needs
=============
* The add-ons, which the owner installs (Jarvis never runs pip; ``pip_lines()`` is the command): onnxruntime and numpy,
  misaki 0.9.4 (Apache-2.0; its release says "Python below 3.13" but it is pure Python, so the line carries
  ``--ignore-requires-python``), spaCy 3.8 with its en_core_web_sm model (MIT), num2words (LGPL-2.1, used unchanged as
  a library), and torch with transformers (the small unknown-word model). Never espeak-ng or phonemizer (GPL-3.0):
  misaki's ``[en]`` extra and the kokoro / kokoro-onnx packages bring those in, so none of them is used.
* The models (``MODELS``), downloaded only when the owner clicks Download in Settings > Voice
  (meeting_speech.ModelDownloader: pinned revisions, sizes and SHA-256 checked, resumable) under ``<data dir>\\models``:
  Kokoro-82M's ONNX export with word timings (onnx-community/Kokoro-82M-v1.0-ONNX-timestamped, Apache-2.0: the model,
  its phoneme table and the 28 English voices) and the two 3 MB unknown-word models
  (PeterReid/graphemes_to_phonemes_en_gb / _us, Apache-2.0) - about 346 MB in all.

Public API
==========
``ENGINE_LOCAL``, ``ENGINE_ONLINE``, ``VOICE_NAMES``, ``DEFAULT_VOICE``, ``is_local(config)``, ``voice_label(name)``,
``british(name)``.
``KOKORO``, ``G2P_GB``, ``G2P_US``, ``MODELS``, ``SIZE_BYTES``, ``SIZE_TEXT``, ``models_root(config)``,
``model_dirs(root)``, ``models_ready(root)``, ``models_missing(root)``.
``ADDONS``, ``addons_missing()``, ``pip_lines(missing=None, python=None)``, ``missing_reason(root)``.
``sentence_groups(text, most=GROUP_CHARS)``, ``speed_for(percent)``, ``gain_for(percent)``,
``join_wavs(parts, out, gap_ms=0)`` -> (duration_ms, each part's duration_ms).
``LocalResult``, ``LocalVoiceError`` (``.code``: ERR_*), ``LocalVoiceClient`` (one per app, shared by the briefing's
and Jarvis's speech threads): ``ready()``, ``warm()``, ``synthesize(text, out, voice=, speed=, gain=, urgent=)``,
``running()``, ``close()``.

The text travels only on the pipe to the worker; nothing here logs it (ids, counts, codes and times only).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import wave
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DEFAULT_LOCAL_VOICE, LOCAL_VOICES, VOICE_ENGINE_LOCAL, VOICE_ENGINE_ONLINE
from .meeting_speech import SpeechModel, model_dir, model_ready

logger = logging.getLogger(__name__)

ENGINE_LOCAL, ENGINE_ONLINE = VOICE_ENGINE_LOCAL, VOICE_ENGINE_ONLINE
VOICE_NAMES = LOCAL_VOICES
DEFAULT_VOICE = DEFAULT_LOCAL_VOICE
SAMPLE_RATE = 24_000
SAMPLE_WIDTH = 2                       # 16-bit
GROUP_CHARS = 220                      # a request's text: about 2 s on this PC's CPU, so his lines never wait long
LONG_CHARS = 400                       # a longer sentence is cut at a clause mark (else a space) into requests this long
THREADS = 8
READY_TIMEOUT_S = 180.0                # loading spaCy, misaki's dictionaries, torch and the model (a slow disk)
ANSWER_TIMEOUT_S = 90.0                # one request (at most a few sentences)
URGENT_WAIT_S = 10.0                   # his own line waits at most this long for the voice (it may still be loading)
START_TRIES = 2                        # a process that ends or hangs before it is ready this many times in a row: given up
CLOSE_WAIT_S = 3.0
IDLE_MARGIN_S = 5.0                    # a process this close to its idle end is replaced rather than raced
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000
TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"     # "1" while the tests run: no real voice process is ever started
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"}

ERR_ADDON = "addon_missing"            # an add-on is not installed
ERR_MODEL = "model_missing"            # the models are not downloaded (or not whole)
ERR_FAILED = "failed"                  # loading or making the speech failed
ERR_VOICE = "unknown_voice"            # not one of VOICE_NAMES
ERR_PATH = "bad_path"                  # the WAV's place is outside the audio folder
ERR_TOO_LONG = "too_long"              # more text than one request takes
ERR_ENDED = "ended"                    # the process ended without an answer
ERR_TIMEOUT = "timeout"                # no answer in time
ERR_START = "start_failed"             # the process could not be started
ERR_REFUSED = "refused"                # under the test guard, with no fake process given
ERR_CLOSED = "closed"                  # the app is closing
ERR_BUSY = "busy"                      # his line waited URGENT_WAIT_S for the voice (loading): the Windows voice says it
ERR_LOAD = "load_failed"               # the process could not load (remembered: not started again until reset)
MAX_TEXT_CHARS = 4000                  # one request (GROUP_CHARS is the usual; sentence_groups keeps far below it)
# Errors about one line, not the voice: the Windows voice says that line, and the next one asks the local voice again.
LINE_ERRORS = frozenset({ERR_TOO_LONG, ERR_FAILED, ERR_VOICE, ERR_PATH, ERR_BUSY})

# --------------------------------------------------------------------------
# Voices and the engine setting
# --------------------------------------------------------------------------

_ACCENTS = {"a": "American", "b": "British"}


def is_local(config: Any) -> bool:
    """The voice is made on this PC (``[voice] engine = "local"``): the gates may let lines that could be private be
    said. False for anything else, a missing setting included (fail-closed: such lines stay on screen)."""
    voice = getattr(config, "voice", None)
    return getattr(voice, "engine", None) == ENGINE_LOCAL


def british(name: str) -> bool:
    """A British voice (its words are read the British way); the American ones start with "a"."""
    return str(name or "").startswith("b")


def voice_label(name: str) -> str:
    """"bm_george" -> "George (British)"; an unknown name as it is."""
    text = str(name or "")
    if text not in VOICE_NAMES:
        return text
    accent = _ACCENTS.get(text[:1], "")
    first = text.split("_", 1)[1].capitalize()
    return f"{first} ({accent})" if accent else first


# Settings > Voice's picker (owner decision 2026-11-12): every voice a button; a click makes it his voice from the next
# line on (kept in the data folder's voice.json, which wins over [voice] local_voice) and he says PREVIEW_LINE in it.
PICK_FILE = "voice.json"
PREVIEW_LINE = "Hey, {sir}. Let's get to work."
PREVIEW_KEY = "voice-preview"
VOICE_GROUPS = (("British men", "bm_"), ("British women", "bf_"), ("American men", "am_"), ("American women", "af_"))


def voice_groups() -> list[tuple[str, list[str]]]:
    """[(heading, [voice names])] in the picker's order: British first, men first."""
    return [(heading, [name for name in VOICE_NAMES if name.startswith(prefix)]) for heading, prefix in VOICE_GROUPS]


def first_name(name: str) -> str:
    """"bm_george" -> "George"."""
    return str(name).split("_", 1)[-1].capitalize()


class VoicePick:
    """The voice picked in Settings > Voice, kept in ``path`` (``<data dir>\\voice.json``: {"local_voice": name}).
    ``get()`` is "" when none was picked or the file holds no known voice; ``set(name)`` writes it (False when it could
    not be written - the pick still holds for this run)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def get(self) -> str:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return ""
        name = data.get("local_voice") if isinstance(data, dict) else None
        return name if name in VOICE_NAMES else ""

    def set(self, name: str) -> bool:
        if name not in VOICE_NAMES:
            return False
        from .prefs import _write_atomic

        return _write_atomic(self.path, json.dumps({"local_voice": name}, indent=2) + "\n")


# --------------------------------------------------------------------------
# The models (downloaded only on the owner's click)
# --------------------------------------------------------------------------

KOKORO_REPO = "onnx-community/Kokoro-82M-v1.0-ONNX-timestamped"
KOKORO_REVISION = "dd4401a9add81ac692d20e240d22ec9dda82cc29"      # pinned: the files are exactly these
G2P_GB_REVISION = "d8357d5067fa26a5c34134d6bbcf4bbf000c0ac8"
G2P_US_REVISION = "a5631b285d18d59483c32c0c3379cb9fac924f4b"
MODEL_FILE = "onnx/model.onnx"
VOCAB_FILE = "tokenizer.json"
G2P_FILES = ("config.json", "generation_config.json", "model.safetensors")

KOKORO = SpeechModel("kokoro-82m", KOKORO_REPO, "kokoro-82m-v1.0", "about 340 MB",
                     (VOCAB_FILE, MODEL_FILE, *(f"voices/{name}.bin" for name in VOICE_NAMES)), KOKORO_REVISION)
G2P_GB = SpeechModel("g2p-en-gb", "PeterReid/graphemes_to_phonemes_en_gb", "g2p-en-gb", "about 3 MB", G2P_FILES,
                     G2P_GB_REVISION)
G2P_US = SpeechModel("g2p-en-us", "PeterReid/graphemes_to_phonemes_en_us", "g2p-en-us", "about 3 MB", G2P_FILES,
                     G2P_US_REVISION)
MODELS = (KOKORO, G2P_GB, G2P_US)
# Their sizes as Hugging Face lists them at the pinned revisions (the download's progress is weighed by them).
SIZE_BYTES = {KOKORO.name: 3_497 + 325_532_171 + 28 * 522_240,
              G2P_GB.name: 1_246 + 191 + 3_011_692,
              G2P_US.name: 1_257 + 191 + 3_011_692}
SIZE_TEXT = "about 346 MB"


def models_root(config: Any) -> Path:
    """``<data dir>\\models`` (shared with the speech models)."""
    from .meetings import models_root as root

    return root(config)


def model_dirs(root: Path) -> dict[str, Path]:
    """Each model's folder: {"kokoro-82m": ..., "g2p-en-gb": ..., "g2p-en-us": ...}."""
    return {model.name: model_dir(Path(root), model) for model in MODELS}


def models_missing(root: Path) -> list[SpeechModel]:
    """The models not whole on this PC (none: all three are)."""
    return [model for model in MODELS if not model_ready(model_dir(Path(root), model), model)]


def models_ready(root: Path) -> bool:
    return not models_missing(root)


# --------------------------------------------------------------------------
# The add-ons (installed by the owner)
# --------------------------------------------------------------------------

SPACY_MODEL_URL = ("https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/"
                   "en_core_web_sm-3.8.0-py3-none-any.whl")
# (module, what pip installs). misaki goes on its own line: its release says Python below 3.13.
ADDONS = (("onnxruntime", "onnxruntime"), ("numpy", "numpy"), ("torch", "torch"), ("transformers", "transformers"),
          ("spacy", '"spacy>=3.8,<3.9"'), ("num2words", '"num2words>=0.5.14"'),
          ("en_core_web_sm", f'"{SPACY_MODEL_URL}"'), ("misaki", '"misaki==0.9.4"'))
_PIP_NAMES = {"spacy": "spacy", "num2words": "num2words", "en_core_web_sm": "en_core_web_sm", "misaki": "misaki"}


def addons_missing(find_spec: Callable[[str], Any] | None = None) -> list[str]:
    """The add-ons' modules that are not installed, in ADDONS' order (looked up, never imported)."""
    find = find_spec or importlib.util.find_spec
    missing = []
    for module, _spec in ADDONS:
        try:
            found = find(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(module)
    return missing


def _python_head(python: str | None) -> str:
    path = Path(python or sys.executable)
    if path.name.lower() == "pythonw.exe":
        path = path.with_name("python.exe")
    text = str(path).replace("\\", "/")
    return f'& "{text}"' if " " in text else text


def pip_lines(missing: Iterable[str] | None = None, python: str | None = None) -> list[str]:
    """The owner's command for the add-ons in ``missing`` (default: what addons_missing finds): one line for the rest,
    one for misaki (with --ignore-requires-python). [] when nothing is missing."""
    names = set(addons_missing() if missing is None else missing)
    head = _python_head(python)
    rest = [spec for module, spec in ADDONS if module in names and module != "misaki"]
    lines = []
    if rest:
        lines.append(f"{head} -m pip install {' '.join(rest)}")
    if "misaki" in names:
        lines.append(f'{head} -m pip install --ignore-requires-python "misaki==0.9.4"')
    return lines


def missing_reason(root: Path, *, addons: Callable[[], list[str]] | None = None) -> str:
    """Why the local voice can't run yet: ERR_ADDON, ERR_MODEL or "" (it can)."""
    if (addons or addons_missing)():
        return ERR_ADDON
    if not models_ready(root):
        return ERR_MODEL
    return ""


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "st", "prof", "sr", "jr", "vs", "etc", "no", "approx", "dept",
                            "e.g", "i.e", "a.m", "p.m", "u.s", "u.k", "inc", "ltd", "co", "mt", "ft", "jan", "feb",
                            "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"})
# A sentence ends at . ! ? or an ellipsis (closing quotes and brackets go with it) before a space and a capital, a
# digit or an opening quote; "a.m. tomorrow" and "Dr. Smith" do not end one.
_END_RE = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s+[\"'“‘(\[]?[A-Z0-9])")
_WORD_BEFORE_RE = re.compile(r"([A-Za-z][A-Za-z.]*)$")
_TERMINAL = ".!?…:;,"


def _abbreviation_end(line: str, match: re.Match[str]) -> bool:
    """The full stop of ``match`` closes an abbreviation ("Dr.", "e.g.", "a.m."), not a sentence."""
    if match.group(0) != ".":
        return False
    word = _WORD_BEFORE_RE.search(line[:match.start()])
    return word is not None and word.group(1).lower().rstrip(".") in _ABBREVIATIONS


def _sentences(line: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in _END_RE.finditer(line):
        if _abbreviation_end(line, match):
            continue
        end = match.end()
        piece = line[start:end].strip()
        if piece:
            parts.append(piece)
        start = end
    rest = line[start:].strip()
    if rest:
        parts.append(rest)
    return parts


_CLAUSE_RE = re.compile(r"[,;:—–](?=\s)|\s-(?=\s)")


def _split_long(sentence: str, most: int = LONG_CHARS) -> list[str]:
    """A sentence longer than ``most`` in pieces of at most ``most`` characters, each cut after its last clause mark
    (a comma, a semicolon, a colon, a dash) in reach, else at its last space."""
    pieces: list[str] = []
    rest = sentence.strip()
    while len(rest) > most:
        window = rest[:most]
        cut = max((match.end() for match in _CLAUSE_RE.finditer(window)), default=-1)
        if cut <= most // 3:
            space = window.rfind(" ")
            cut = space if space > most // 3 else most
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return pieces


def sentence_groups(text: str, most: int = GROUP_CHARS) -> list[str]:
    """``text`` as requests for the worker: whole sentences, joined while they stay within ``most`` characters; a
    sentence longer than LONG_CHARS is cut at its clause marks (so no request holds the voice for long, and none comes
    near MAX_TEXT_CHARS). Each line is a sentence of its own; a line with no closing mark gets a full stop, so two
    lines never run into one."""
    limit = max(40, int(most))
    sentences: list[str] = []
    for line in str(text or "").splitlines():
        for sentence in _sentences(" ".join(line.split())):
            sentences.extend(_split_long(sentence) if len(sentence) > LONG_CHARS else [sentence])
        if sentences and sentences[-1] and sentences[-1][-1] not in _TERMINAL and line.strip():
            sentences[-1] = sentences[-1] + "."
    groups: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= limit:
            current = f"{current} {sentence}"
        else:
            groups.append(current)
            current = sentence
    if current:
        groups.append(current)
    return groups


def speed_for(percent: int) -> float:
    """[voice] rate (+15 -> 1.15), within the voice's 0.5 .. 2.0."""
    return round(min(max(1.0 + int(percent) / 100.0, 0.5), 2.0), 3)


def gain_for(percent: int) -> float:
    """[voice] volume (-20 -> 0.8), within 0 .. 2 (the worker keeps a louder voice from clipping)."""
    return round(min(max(1.0 + int(percent) / 100.0, 0.0), 2.0), 3)


def join_wavs(parts: Sequence[Path], out: Path, gap_ms: int = 0) -> tuple[int, tuple[int, ...]]:
    """The WAVs in ``parts`` (24 kHz mono 16-bit, as the worker writes them) one after another in ``out`` (written as
    ``<out>.part``, then renamed), ``gap_ms`` of quiet between them: (total ms, each part's ms). ValueError for a part
    in another format."""
    out = Path(out)
    part_path = out.with_name(out.name + ".part")
    lengths: list[int] = []
    gap = b"\x00\x00" * int(SAMPLE_RATE * max(0, int(gap_ms)) / 1000)
    frames_total = 0
    try:
        with wave.open(str(part_path), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(SAMPLE_WIDTH)
            target.setframerate(SAMPLE_RATE)
            for index, path in enumerate(parts):
                with wave.open(str(path), "rb") as source:
                    if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != \
                            (1, SAMPLE_WIDTH, SAMPLE_RATE):
                        raise ValueError("not 24 kHz mono 16-bit")
                    frames = source.readframes(source.getnframes())
                if index and gap:
                    target.writeframes(gap)
                    frames_total += len(gap) // SAMPLE_WIDTH
                target.writeframes(frames)
                count = len(frames) // SAMPLE_WIDTH
                frames_total += count
                lengths.append(round(count * 1000 / SAMPLE_RATE))
        os.replace(part_path, out)
    finally:
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass
    return round(frames_total * 1000 / SAMPLE_RATE), tuple(lengths)


# --------------------------------------------------------------------------
# The app's client of the worker process
# --------------------------------------------------------------------------

class LocalVoiceError(Exception):
    """The local voice gave no audio; ``code`` is one of ERR_* (safe to log; never any text)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LocalResult:
    """One request's answer: the WAV's length, when each word starts (ms from the WAV's start, the word as the worker
    read it), how long making it took and, when this request started the process, how long it took to load."""

    duration_ms: int
    words: tuple[tuple[int, str], ...] = ()
    ms: int = 0
    load_ms: int = 0

    def __repr__(self) -> str:                    # never the words themselves
        return f"LocalResult({self.duration_ms} ms, {len(self.words)} words, made in {self.ms} ms)"


class LocalVoiceClient:
    """See the module docstring. Thread-safe; one request at a time, Jarvis's own lines (``urgent``) before the
    briefing's. ``spawn(command, environ)`` (tests) replaces the real process; without it, under the test guard
    (JARVIS_DESKTOP_TEST_GUARD=1) nothing is ever started."""

    def __init__(self, models_root: Path, out_root: Path, *, voice: str = DEFAULT_VOICE, threads: int = THREADS,
                 keep_warm_s: float = 0.0, python: str | None = None, root: Path | None = None,
                 spawn: Callable[[list[str], Mapping[str, str]], Any] | None = None,
                 addons: Callable[[], list[str]] | None = None, ready_timeout_s: float = READY_TIMEOUT_S,
                 answer_timeout_s: float = ANSWER_TIMEOUT_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.models_root = Path(models_root)
        self.out_root = Path(out_root)
        self.voice = voice if voice in VOICE_NAMES else DEFAULT_VOICE
        self._threads = max(1, int(threads))
        self._keep_warm_s = max(0.0, float(keep_warm_s))
        self._python = python or sys.executable
        self._root = root or Path(__file__).resolve().parent.parent
        self._spawn = spawn or self._popen
        self._addons = addons or addons_missing
        self._ready_timeout_s, self._answer_timeout_s = ready_timeout_s, answer_timeout_s
        self._clock = clock
        self._cond = threading.Condition()
        self._busy = False
        self._urgent_waiting = 0
        self._closed = False
        self._proc: Any = None
        self._lines: queue.Queue = queue.Queue()
        self._last_used = 0.0
        self._next_id = 0
        self._broken = ""                     # why the process could not load (ERR_LOAD + its code); "" when it can
        self._start_failures = 0              # starts in a row that ended or hung before the process was ready
        self.last_load_ms = 0                 # how long the last start took to be ready (Settings, the log)

    # ---- state --------------------------------------------------------------------------------------------------

    def ready(self) -> bool:
        """Every add-on is installed, every model is whole and the process has not failed to load (file reads only)."""
        return not self.reason()

    def reason(self) -> str:
        """Why the local voice can't run: ERR_ADDON, ERR_MODEL (see missing_reason), "load_failed:<code>" once the
        process could not load (remembered until ``forget_failure``: a fresh add-on means a restart of Jarvis), or ""."""
        return self._broken or missing_reason(self.models_root, addons=self._addons)

    def forget_failure(self) -> None:
        """A download ended (or the owner fixed something): the next line may start the process again."""
        self._broken, self._start_failures = "", 0

    def running(self) -> bool:
        proc = self._proc
        try:
            return proc is not None and proc.poll() is None
        except (OSError, AttributeError):
            return False

    # ---- the process ------------------------------------------------------------------------------------------------

    def _command(self) -> list[str]:
        dirs = model_dirs(self.models_root)
        return [self._python, "-B", "-m", "briefing_reader.local_voice_worker",
                "--model", str(dirs[KOKORO.name]), "--g2p-gb", str(dirs[G2P_GB.name]),
                "--g2p-us", str(dirs[G2P_US.name]), "--out-root", str(self.out_root),
                "--threads", str(self._threads), "--idle-s", str(self._keep_warm_s), "--voice", self.voice]

    @staticmethod
    def _environ() -> dict[str, str]:
        env = dict(os.environ)
        env.update(OFFLINE_ENV)
        return env

    def _popen(self, command: list[str], environ: Mapping[str, str]) -> Any:
        if os.environ.get(TEST_GUARD_ENV, "") == "1":
            raise PermissionError("no real voice process under the test guard")
        flags = BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW if os.name == "nt" else 0
        return subprocess.Popen(command, cwd=str(self._root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=dict(environ), creationflags=flags)

    @staticmethod
    def _pump(proc: Any, lines: queue.Queue) -> None:
        try:
            for raw in proc.stdout:
                try:
                    line = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                except (ValueError, UnicodeDecodeError):
                    continue                      # not one of the worker's lines (a library's print): skipped
                if isinstance(line, dict):
                    lines.put(line)
        except (OSError, ValueError):
            pass
        lines.put(None)                           # the process's output ended

    def _start(self) -> tuple[str, int]:
        """("" or an error code, load_ms). Called holding the turn. A process that says it could not load - or ends or
        hangs before it is ready START_TRIES times in a row - is not started again (``reason`` says why) until
        ``forget_failure``: each try costs a full load of about 1.3 GB."""
        error, load_ms = self._start_once()
        if error in (ERR_CLOSED, ERR_REFUSED, ""):
            return error, load_ms
        if error.startswith(ERR_LOAD):
            self._broken = error
        else:
            self._start_failures += 1
            if self._start_failures >= START_TRIES:
                self._broken = f"{ERR_LOAD}:{error}"
        logger.info("Local voice: could not start (%s)%s", error,
                    "; not started again until the next download or restart" if self._broken else "")
        return error, 0

    def _start_once(self) -> tuple[str, int]:
        try:
            proc = self._spawn(self._command(), self._environ())
        except PermissionError:
            return ERR_REFUSED, 0
        except OSError:
            return ERR_START, 0
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, args=(proc, lines), name="local-voice-out", daemon=True).start()
        self._proc, self._lines = proc, lines
        with self._cond:
            closed = self._closed
        if closed:                                # close() came while it started: it goes at once
            self._kill()
            return ERR_CLOSED, 0
        deadline = self._clock() + self._ready_timeout_s
        while True:
            try:
                ready = lines.get(timeout=max(0.01, deadline - self._clock()))
            except queue.Empty:
                self._kill()
                return ERR_TIMEOUT, 0
            if ready is None:
                self._kill()
                return (ERR_CLOSED if self._closed else ERR_ENDED), 0
            if "ready" not in ready:
                continue
            if not ready.get("ready"):
                self._kill()
                error = ready.get("error")
                return f"{ERR_LOAD}:{error if error in (ERR_ADDON, ERR_MODEL, ERR_FAILED) else ERR_FAILED}", 0
            load_ms = int(ready.get("load_ms") or 0)
            self.last_load_ms = load_ms
            self._start_failures = 0
            self._last_used = self._clock()
            logger.info("Local voice: ready in %.1f s", load_ms / 1000)
            return "", load_ms

    def _send(self, request: dict) -> dict | None:
        """The worker's answer to ``request`` (its id), None when the process was gone, or an ERR_TIMEOUT answer."""
        proc = self._proc
        try:
            proc.stdin.write((json.dumps(request, ensure_ascii=True) + "\n").encode("ascii"))
            proc.stdin.flush()
        except (OSError, ValueError, AttributeError):
            return None
        deadline = self._clock() + self._answer_timeout_s
        while True:
            try:
                answer = self._lines.get(timeout=max(0.01, deadline - self._clock()))
            except queue.Empty:
                self._kill()
                return {"ok": False, "error": ERR_TIMEOUT}
            if answer is None:
                return None
            if answer.get("id") == request["id"]:
                return answer

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=CLOSE_WAIT_S)
        except (OSError, subprocess.TimeoutExpired, AttributeError):
            pass

    # ---- the turn (one request at a time; urgent ones first) ---------------------------------------------------------

    def _acquire(self, urgent: bool, timeout: float | None = None) -> bool:
        """The turn (True), or False when ``timeout`` seconds went by first."""
        deadline = None if timeout is None else self._clock() + timeout
        with self._cond:
            if urgent:
                self._urgent_waiting += 1
            try:
                while self._busy or (not urgent and self._urgent_waiting > 0):
                    if deadline is None:
                        self._cond.wait()
                        continue
                    left = deadline - self._clock()
                    if left <= 0:
                        return False
                    self._cond.wait(left)
            finally:
                if urgent:
                    self._urgent_waiting -= 1
                    self._cond.notify_all()       # a waiting briefing request looks again
            self._busy = True
            return True

    def _release(self) -> None:
        with self._cond:
            self._busy = False
            self._cond.notify_all()

    # ---- what the app asks ----------------------------------------------------------------------------------------

    def warm(self) -> None:
        """Start the process in the background now (the first line then needs no wait); nothing when the voice can't
        run, the process runs already or the app is closing."""
        if self._closed or self.running() or not self.ready():
            return

        def work() -> None:
            self._acquire(False)
            try:
                if not self._closed and not self.running():
                    self._start()                 # a failure is logged there, and remembered when it is the load
            finally:
                self._release()

        threading.Thread(target=work, name="local-voice-warm", daemon=True).start()

    def synthesize(self, text: str, out: Path, *, voice: str | None = None, speed: float = 1.0, gain: float = 1.0,
                   urgent: bool = False) -> LocalResult:
        """``text`` as a WAV at ``out`` (inside the audio folder). Raises LocalVoiceError. Blocking: never on the GUI
        thread."""
        if self._closed:
            raise LocalVoiceError(ERR_CLOSED)
        if len(text) > MAX_TEXT_CHARS:
            raise LocalVoiceError(ERR_TOO_LONG)
        reason = self.reason()
        if reason:
            raise LocalVoiceError(reason)
        if not self._acquire(urgent, URGENT_WAIT_S if urgent else None):
            raise LocalVoiceError(ERR_BUSY)       # still loading, or a long request: the Windows voice says this one
        try:
            return self._synthesize(text, Path(out), voice or self.voice, float(speed), float(gain))
        finally:
            self._release()

    def _synthesize(self, text: str, out: Path, voice: str, speed: float, gain: float) -> LocalResult:
        load_ms = 0
        if (self.running() and self._keep_warm_s > 0
                and self._clock() - self._last_used > self._keep_warm_s - IDLE_MARGIN_S):
            self._kill()                          # about to go idle on its own: a fresh one, not a race
        for attempt in range(2):
            if self._closed:
                raise LocalVoiceError(ERR_CLOSED)
            if not self.running():
                error, load_ms = self._start()
                if error:
                    raise LocalVoiceError(error)
            self._next_id += 1
            request = {"id": self._next_id, "text": text, "voice": voice, "speed": speed, "gain": gain,
                       "out": str(out)}
            answer = self._send(request)
            if answer is None and attempt == 0:
                self._kill()                      # it ended just then (idle, or crashed): started again, once
                continue
            if answer is None:
                raise LocalVoiceError(ERR_ENDED)
            if not answer.get("ok"):
                code = str(answer.get("error") or ERR_FAILED)
                raise LocalVoiceError(code if code.isidentifier() else ERR_FAILED)
            self._last_used = self._clock()
            words = tuple((int(item[0]), str(item[1])) for item in answer.get("words") or ()
                          if isinstance(item, (list, tuple)) and len(item) == 2)
            return LocalResult(int(answer.get("duration_ms") or 0), words, int(answer.get("ms") or 0), load_ms)
        raise LocalVoiceError(ERR_ENDED)

    def close(self) -> None:
        """The app closes: the process is asked to end, and killed if it has not within a few seconds. Never waits for
        a request on its way (that one gets ERR_CLOSED); a start on its way kills its new process itself."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        proc = self._proc
        if proc is None:
            return
        try:
            proc.stdin.write(b'{"quit": true}\n')
            proc.stdin.flush()
            proc.stdin.close()
            proc.wait(timeout=CLOSE_WAIT_S)
            self._proc = None
        except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
            self._kill()
