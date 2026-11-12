""""Hey Jarvis": the wake phrase's detector and its model files (no Qt, no device; owner decisions 2026-11-12).

The detector follows openWakeWord's streaming pipeline (https://github.com/dscripka/openWakeWord - its code is under
the Apache License 2.0, copyright 2022 David Scripka), re-done here on onnxruntime and numpy so nothing else is needed:

1. 16 kHz mono 16-bit audio, CHUNK_SAMPLES (1280 samples, 80 ms) at a time;
2. a melspectrogram of the chunk and the LOOKBACK_SAMPLES before it (the melspectrogram model), scaled x / 10 + 2;
3. an embedding (96 numbers) of the last MEL_WINDOW (76) mel frames (the embedding model);
4. the wake model's score (0..1) on the last ``frames`` embeddings (16 for "hey jarvis").

The first WARMUP_CHUNKS scores after a reset are 0 (the buffers hold no speech yet), as openWakeWord does; the
embeddings start from those of quiet noise, as its do. A wake is ``patience`` scores in a row at or above
``threshold`` (SENSITIVITIES: the owner's choice in Settings > Talk); the detector says so once, until it is reset.

The model files are openWakeWord's own (its GitHub release v0.5.1). They are licensed CC BY-NC-SA 4.0 - free for
personal, non-commercial use - so they are never part of this repository: :class:`WakeModelDownloader` fetches them
only on the owner's click into their own folder under the models root, each checked against the size and SHA-256
pinned in MODEL_FILES, and :func:`model_ready` accepts only that.

:func:`strip_wake_words` takes "Hey Jarvis" off the front of what a talk heard, for a request said in one breath
("Hey Jarvis, open Spotify").

Privacy: audio is only ever in memory here; nothing is written, logged or sent.
"""

from __future__ import annotations

import importlib.util
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from . import meeting_speech

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2
CHUNK_SAMPLES = 1280                         # 80 ms: one score
CHUNK_BYTES = CHUNK_SAMPLES * BYTES_PER_SAMPLE
CHUNK_MS = CHUNK_SAMPLES * 1000 // SAMPLE_RATE
LOOKBACK_SAMPLES = 480                       # the melspectrogram also sees the 30 ms before each chunk
MEL_WINDOW = 76                              # mel frames per embedding
MEL_STEP = 8                                 # ... one embedding every 8 mel frames (80 ms)
MEL_BINS = 32
FEATURES_KEPT = 120                          # embeddings kept (the wake model reads its last ``frames``)
WAKE_FRAMES = 16                             # "hey jarvis" reads 16 embeddings (1.28 s); its file says so too
WARMUP_CHUNKS = 5                            # the first scores after a reset are 0
NOISE_SECONDS = 4                            # the embeddings start from those of quiet noise ...
NOISE_LEVEL = 1000                           # ... this loud (about -30 dB below full scale) ...
NOISE_SEED = 0                               # ... always the same noise

ERR_ADDON = "addon_missing"                  # onnxruntime or numpy is not installed (or would not load)
ERR_MODEL = "model_missing"                  # the model's folder is not ready
ERR_FAILED = "failed"                        # the model files would not load
RUNTIME_MODULES = ("numpy", "onnxruntime")
ADDON_RUNTIME = "onnxruntime"                # comes with faster-whisper (talk's add-on)


@dataclass(frozen=True)
class Sensitivity:
    """How sure the detector must be: ``patience`` scores in a row at or above ``threshold``."""

    name: str
    label: str
    threshold: float
    patience: int


# Measured 2026-11-12 on 36 clips of Windows' David and Zira voices, clean and with noise at 10 and 5 dB: every
# preset heard all 42 "Hey Jarvis" clips; of 54 other clips, "fewer" fired on 2, "balanced" on 3, "more" on 12.
SENSITIVITIES = {sensitivity.name: sensitivity for sensitivity in (
    Sensitivity("fewer", "Fewer false wakes", 0.7, 4),
    Sensitivity("balanced", "Balanced", 0.5, 3),
    Sensitivity("more", "Hears more", 0.4, 2))}
DEFAULT_SENSITIVITY = "balanced"


def sensitivity_of(name: Any) -> Sensitivity:
    """The preset called ``name``; Balanced for anything else."""
    return SENSITIVITIES.get(str(name or ""), SENSITIVITIES[DEFAULT_SENSITIVITY])


# --------------------------------------------------------------------------
# The model files (openWakeWord's, CC BY-NC-SA 4.0; downloaded only on the owner's click)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str


MODEL_REPO = "dscripka/openWakeWord"
MODEL_RELEASE = "v0.5.1"
MODEL_URL = "https://github.com/{repo}/releases/download/{release}/{name}"
MODEL_FOLDER = "openwakeword-hey-jarvis-v0.1"
# Sizes as GitHub lists them; SHA-256 of the files downloaded from that release on 2026-11-12.
MODEL_FILES = (
    ModelFile("melspectrogram.onnx", 1_087_958, "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f"),
    ModelFile("embedding_model.onnx", 1_326_578, "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f"),
    ModelFile("hey_jarvis_v0.1.onnx", 1_271_370, "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb"),
)
MEL_FILE, EMBEDDING_FILE, WAKE_FILE = (entry.name for entry in MODEL_FILES)
MODEL_SIZE_TEXT = "about 3.7 MB"
MODEL_SOURCE = "openWakeWord's “hey jarvis” on GitHub"
MODEL_LICENCE = "CC BY-NC-SA 4.0: free for personal, non-commercial use"
MANIFEST = meeting_speech.MANIFEST


def model_dir(models_root: Path) -> Path:
    """Where the wake model lives under the models root (the speech models' own root)."""
    return Path(models_root) / MODEL_FOLDER


def model_ready(folder: Path, files: Sequence[ModelFile] = MODEL_FILES) -> bool:
    """Every file there with its pinned size, and the manifest the download wrote after checking each one's size and
    SHA-256 against the pinned ones."""
    try:
        manifest = json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
        sizes, digests = manifest.get("files", {}), manifest.get("sha256", {})
        return all(int(sizes[entry.name]) == entry.size and str(digests[entry.name]) == entry.sha256
                   and (Path(folder) / entry.name).stat().st_size == entry.size for entry in files)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


class WakeModelDownloader(meeting_speech.ModelDownloader):
    """The wake model's files from openWakeWord's GitHub release (only on the owner's click), each checked against
    its pinned size and SHA-256, resumable, with progress and cancel - meeting_speech.ModelDownloader's work, only
    the addresses and the checks come from here. ``files`` (tests) replaces the pinned table."""

    LOG_NAME = "Wake model"

    def __init__(self, models_root: Path, *, files: Sequence[ModelFile] = MODEL_FILES, **kwargs: Any) -> None:
        self._pinned = {entry.name: entry for entry in files}
        super().__init__(model_dir(models_root), repo=MODEL_REPO, revision=MODEL_RELEASE,
                         files=[entry.name for entry in files], **kwargs)

    def _url(self, name: str) -> str:
        return MODEL_URL.format(repo=MODEL_REPO, release=MODEL_RELEASE, name=name)

    def _expected(self, name: str) -> tuple[int, str]:
        entry = self._pinned[name]
        return entry.size, entry.sha256


# --------------------------------------------------------------------------
# The detector
# --------------------------------------------------------------------------

class WakeError(RuntimeError):
    """The detector could not be made; ``code`` is one of ERR_ADDON, ERR_MODEL, ERR_FAILED."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class WakeSessions(NamedTuple):
    """The three models, each with onnxruntime's ``run(outputs, feeds)`` and ``get_inputs()`` (tests: fakes)."""

    mel: Any
    embedding: Any
    wake: Any


def runtime_missing() -> str:
    """"" when the detector's add-ons (numpy, onnxruntime) are installed, else what to install."""
    for name in RUNTIME_MODULES:
        try:
            if importlib.util.find_spec(name) is None:
                return name if name != "onnxruntime" else ADDON_RUNTIME
        except (ImportError, ValueError):
            return name
    return ""


def load_sessions(folder: Path, *, threads: int = 1) -> WakeSessions:
    """The three models from ``folder`` on the CPU, one thread each (WakeError when they cannot be had)."""
    if not model_ready(folder):
        raise WakeError(ERR_MODEL)
    try:
        import numpy  # noqa: F401 - the detector needs it; a missing one is the add-on's fault
        import onnxruntime as ort
    except ImportError as exc:                       # not installed, or its library would not load
        raise WakeError(ERR_ADDON) from exc
    try:
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, int(threads))
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.log_severity_level = 3               # errors only (and those are reported by their code)

        def session(name: str) -> Any:
            return ort.InferenceSession(str(Path(folder) / name), sess_options=options,
                                        providers=["CPUExecutionProvider"])

        return WakeSessions(session(MEL_FILE), session(EMBEDDING_FILE), session(WAKE_FILE))
    except Exception as exc:  # noqa: BLE001 - a file that will not load is reported by its code, never its text
        raise WakeError(ERR_FAILED) from exc


class WakeDetector:
    """See the module docstring. ``process(chunk)`` takes exactly CHUNK_BYTES and says whether the wake phrase was
    just heard; ``feed(pcm)`` takes any size of piece (the rest waits for the next). ``score`` is the last score,
    ``set_sensitivity(name)`` changes the preset at once, ``reset()`` starts again (after a wake, a hold)."""

    def __init__(self, sessions: WakeSessions, sensitivity: str = DEFAULT_SENSITIVITY) -> None:
        import numpy as np

        self._np = np
        self._mel, self._embedding, self._wake = sessions
        self._mel_input = self._mel.get_inputs()[0].name
        self._embedding_input = self._embedding.get_inputs()[0].name
        wake_input = self._wake.get_inputs()[0]
        self._wake_input = wake_input.name
        shape = list(getattr(wake_input, "shape", []) or [])
        self.frames = int(shape[1]) if len(shape) > 1 and isinstance(shape[1], int) and shape[1] > 0 else WAKE_FRAMES
        self.sensitivity = sensitivity_of(sensitivity)
        self._noise_features: Any = None             # the embeddings of quiet noise, made once
        self._pending = bytearray()
        self.reset()

    def set_sensitivity(self, name: str) -> None:
        self.sensitivity = sensitivity_of(name)

    def reset(self) -> None:
        np = self._np
        self._pending = bytearray()
        self._tail = np.zeros(0, dtype=np.int16)
        self._mels = np.ones((MEL_WINDOW, MEL_BINS), dtype=np.float32)
        if self._noise_features is None:
            noise = np.random.default_rng(NOISE_SEED).integers(-NOISE_LEVEL, NOISE_LEVEL, SAMPLE_RATE * NOISE_SECONDS)
            spec = self._melspec(noise.astype(np.int16))
            windows = [spec[start:start + MEL_WINDOW] for start in range(0, len(spec) - MEL_WINDOW + 1, MEL_STEP)]
            self._noise_features = self._embed(np.stack(windows))
        self._features = self._noise_features.copy()
        self._chunks = 0
        self._run = 0
        self._fired = False
        self.score = 0.0

    def feed(self, pcm: bytes) -> bool:
        """``pcm`` in any size of piece: True when a chunk in it heard the wake phrase (what follows that chunk is
        dropped: the caller resets after a wake)."""
        self._pending += pcm
        while len(self._pending) >= CHUNK_BYTES:
            chunk = bytes(self._pending[:CHUNK_BYTES])
            del self._pending[:CHUNK_BYTES]
            if self.process(chunk):
                self._pending = bytearray()
                return True
        return False

    def process(self, chunk: bytes) -> bool:
        """One chunk (CHUNK_BYTES of 16 kHz mono 16-bit audio): True the first time the wake phrase is heard since
        the last reset."""
        np = self._np
        samples = np.frombuffer(chunk[:CHUNK_BYTES], dtype="<i2")
        both = np.concatenate((self._tail, samples)) if self._tail.size else samples
        self._tail = both[-LOOKBACK_SAMPLES:].astype(np.int16)
        self._mels = np.vstack((self._mels, self._melspec(both)))[-MEL_WINDOW:]
        self._features = np.vstack((self._features, self._embed(self._mels[None, :, :])))[-FEATURES_KEPT:]
        window = self._features[-self.frames:][None, :, :].astype(np.float32)
        score = float(np.asarray(self._wake.run(None, {self._wake_input: window})[0]).reshape(-1)[0])
        self._chunks += 1
        self.score = 0.0 if self._chunks <= WARMUP_CHUNKS else min(1.0, max(0.0, score))
        self._run = self._run + 1 if self.score >= self.sensitivity.threshold else 0
        if self._fired or self._run < self.sensitivity.patience:
            return False
        self._fired = True
        return True

    def _melspec(self, samples: Any) -> Any:
        np = self._np
        out = self._mel.run(None, {self._mel_input: samples.astype(np.float32)[None, :]})[0]
        spec = np.asarray(out, dtype=np.float32).reshape(-1, MEL_BINS)
        return spec / 10.0 + 2.0

    def _embed(self, windows: Any) -> Any:
        np = self._np
        batch = np.asarray(windows, dtype=np.float32)[..., None]
        out = self._embedding.run(None, {self._embedding_input: batch})[0]
        return np.asarray(out, dtype=np.float32).reshape(len(batch), -1)


# --------------------------------------------------------------------------
# What a talk after a wake heard
# --------------------------------------------------------------------------

_WAKE_NAMES = r"(?:jarvis|jarvi|javis|jervis|jarves)"
_WAKE_WORDS_RE = re.compile(
    rf"^[\W_]*(?:(?:hey|hi|hay|hello|okay|ok|o\.\s?k\.?|yo)\b[\s,.!-]*)?{_WAKE_NAMES}(?:'s)?\b[\s,.!?:;-]*",
    re.IGNORECASE)


def strip_wake_words(text: str) -> str:
    """"Hey Jarvis, open Spotify." -> "Open Spotify." (only at the start; "" when nothing else was said)."""
    stripped = _WAKE_WORDS_RE.sub("", str(text or ""), count=1).strip()
    if stripped == str(text or "").strip():
        return stripped
    if not re.search(r"[^\W_]", stripped):
        return ""
    return stripped[:1].upper() + stripped[1:]
