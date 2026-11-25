"""The voiceprint: only the owner's voice wakes Jarvis with "Hey Jarvis" (no Qt, no device; owner decisions 2026-11-24).

What it is. A speaker model turns a clip of speech into 256 numbers (an embedding) that sound alike for one voice and
apart for two. The owner says "Hey Jarvis" three times (Settings > Talk > Recognise my voice: voiceprint_ui); the mean of
those embeddings is the voice template. After that, each "Hey Jarvis" the wake detector hears is embedded the same way
and scored against the template (the cosine of the two, -1..1): THRESHOLD or above is the owner (``match``), below is
someone else (``mismatch``: no talk opens). It only filters wakes - it never approves anything - and only "Hey
Jarvis": the talk key and every other way in are never checked.

The model is WeSpeaker's ResNet34 trained on VoxCeleb (``MODEL_FILE``; CC BY 4.0: CREDIT is the credit its licence asks
for). It is never part of this repository: :class:`VoiceprintModelDownloader` fetches it only on the owner's click into
its own folder under the models root (``voiceprint``), checked against the size and SHA-256 pinned here, and
:func:`model_ready` accepts only that.

The features are Kaldi's filterbank as WeSpeaker computes them (:func:`fbank`, ported exactly from the prototype that
was measured on the real model): 80 mel bins, 25 ms frames every 10 ms, no dither, DC removal, pre-emphasis 0.97, a
Hamming window, a 512-point FFT, the power spectrum, Kaldi's mel scale (1127 ln(1 + f/700), 20 Hz to Nyquist), the log
with a float32-eps floor, then the mean over time taken off (CMN). The samples are in int16 range, as the model expects.

A clip with less than MIN_SPEECH_S of speech (SPEECH_ABOVE_DB above its room) is ``unchecked("short")``, which lets the wake through (fail open): a
clipped wake never locks the owner out. So does any other reason the check cannot run (no runtime, no model, a model
that will not load, no template): :class:`Verdict` says which, in a reason word.

The template (:class:`Template`) is kept as VOICEPRINT_FILE in the data folder, written atomically: only the numbers, a
version, a created time and the sample count. No audio is ever written. :meth:`TemplateStore.forget` deletes the file.
A file with a wrong length, a wrong version or a number that is not finite is not used (not enrolled), and the log
says so with a reason word only.

Privacy: audio is only ever in memory here; logs hold counts, durations, statuses, scores rounded to 2 places and
reason words - never audio, embeddings or names.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import atomic, meeting_speech, talk, wake

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2
EMBED_DIM = 256                       # the model's output: [B, 256]
INPUT_NAME = "feats"                  # the model's input: [B, T, 80] float32
MEL_BINS = 80
FRAME_LENGTH = 400                    # 25 ms
FRAME_SHIFT = 160                     # 10 ms
N_FFT = 512
PREEMPH = 0.97
LOW_FREQ = 20.0

THRESHOLD = 0.50                      # measured 2026-11-24: same voice 0.84..0.91, another 0.17..0.35 (see the plan)
AGREE_MIN = 0.60                      # enrolment: every two takes at least this alike, else nothing is kept
MIN_SPEECH_S = 0.5                    # less speech than this in a clip: unchecked ("short"), let through
SPEECH_ABOVE_DB = 6.0                 # speech, for the check: this far above the room (no absolute floor)
WAKE_CLIP_S = 1.6                     # the wake clip: the listener's pre-roll (wake_listen.PREROLL_S) ...
WAKE_CLIP_BYTES = int(WAKE_CLIP_S * SAMPLE_RATE) * BYTES_PER_SAMPLE   # ... and each take's window, the same length
TAKES = 3                             # enrolment: "Hey Jarvis" this many times
TAKE_MAX_S = 4.0                      # a take's speech lasts at most this
MAX_SAMPLES = 10                      # a template file with more embeddings than this is not this app's

VOICEPRINT_FILE = "voiceprint.json"
VERSION = 1

# Verdicts and their reason words (logged and shown in Settings; never anything else)
MATCH, MISMATCH, UNCHECKED = "match", "mismatch", "unchecked"
WHY_SHORT = "short"                   # too little speech to tell
WHY_RUNTIME = "runtime"               # numpy or onnxruntime is not installed
WHY_MODEL = "model"                   # the speaker model is not downloaded (or not whole)
WHY_FAILED = "failed"                 # the speaker model would not load or run
WHY_NO_TEMPLATE = "no_template"       # not enrolled (or the file could not be used)

# Take problems (enrolment asks again)
TAKE_SILENT, TAKE_QUIET, TAKE_SHORT, TAKE_LONG = "silent", "quiet", "short", "long"

# Why a template file was not used (logged as a word)
BAD_VERSION, BAD_LENGTH, BAD_NUMBER, BAD_FILE = "version", "length", "number", "unreadable"

ERR_ADDON, ERR_MODEL, ERR_FAILED = wake.ERR_ADDON, wake.ERR_MODEL, wake.ERR_FAILED
RUNTIME_MODULES = wake.RUNTIME_MODULES


# --------------------------------------------------------------------------
# The model file (WeSpeaker ResNet34 VoxCeleb, CC BY 4.0; downloaded only on the owner's click)
# --------------------------------------------------------------------------

MODEL_REPO = "k2-fsa/sherpa-onnx"
MODEL_RELEASE = "speaker-recongition-models"       # (the release's own spelling)
MODEL_URL = "https://github.com/{repo}/releases/download/{release}/{name}"
MODEL_FOLDER = "voiceprint"
MODEL_FILE = wake.ModelFile("wespeaker_en_voxceleb_resnet34.onnx", 26_534_365,
                            "5ef208a9da1453335308a6b6f4e6dfbd7e183a38b604de0a57664f45d257fe94")
MODEL_FILES = (MODEL_FILE,)
MODEL_SIZE_TEXT = "about 26.5 MB"
CREDIT = "Speaker model: WeSpeaker ResNet34 (VoxCeleb), CC BY 4.0"
MANIFEST = meeting_speech.MANIFEST


def model_dir(models_root: Path) -> Path:
    """Where the speaker model lives under the models root (its own folder, beside the wake model's)."""
    return Path(models_root) / MODEL_FOLDER


def model_ready(folder: Path, files: Sequence[wake.ModelFile] = MODEL_FILES) -> bool:
    """The file there with its pinned size, and the manifest the download wrote after checking its size and SHA-256
    against the pinned ones (wake.model_ready's rules)."""
    return wake.model_ready(folder, files)


class VoiceprintModelDownloader(meeting_speech.ModelDownloader):
    """The speaker model from its GitHub release (only on the owner's click), checked against its pinned size and
    SHA-256, resumable, with progress and cancel - meeting_speech.ModelDownloader's work, as wake.WakeModelDownloader
    does it. ``files`` (tests) replaces the pinned table."""

    LOG_NAME = "Speaker model"

    def __init__(self, models_root: Path, *, files: Sequence[wake.ModelFile] = MODEL_FILES, **kwargs: Any) -> None:
        self._pinned = {entry.name: entry for entry in files}
        super().__init__(model_dir(models_root), repo=MODEL_REPO, revision=MODEL_RELEASE,
                         files=[entry.name for entry in files], **kwargs)

    def _url(self, name: str) -> str:
        return MODEL_URL.format(repo=MODEL_REPO, release=MODEL_RELEASE, name=name)

    def _expected(self, name: str) -> tuple[int, str]:
        entry = self._pinned[name]
        return entry.size, entry.sha256


def runtime_missing() -> str:
    """"" when numpy and onnxruntime are installed, else what to install (wake.runtime_missing)."""
    return wake.runtime_missing()


# --------------------------------------------------------------------------
# The features (ported exactly from the measured prototype: float64 maths, the same window and floor)
# --------------------------------------------------------------------------

def mel_banks(n_mels: int = MEL_BINS, n_fft: int = N_FFT, sr: int = SAMPLE_RATE, low: float = LOW_FREQ,
              high: float = 0.0) -> Any:
    """Kaldi's triangular mel filters, ``n_mels`` x (n_fft // 2 + 1)."""
    import numpy as np

    high = sr / 2 + high if high <= 0 else high

    def mel(f: Any) -> Any:
        return 1127.0 * np.log(1.0 + f / 700.0)

    lo, hi = mel(low), mel(high)
    delta = (hi - lo) / (n_mels + 1)
    bins = n_fft // 2
    fft_mel = mel(np.arange(bins) * sr / n_fft)
    left = lo + np.arange(n_mels)[:, None] * delta
    center, right = left + delta, left + 2 * delta
    up = (fft_mel[None, :] - left) / (center - left)
    down = (right - fft_mel[None, :]) / (right - center)
    w = np.maximum(0.0, np.minimum(up, down))
    return np.pad(w, ((0, 0), (0, 1)))  # n_fft//2+1 columns


_BANKS: Any = None


def _banks() -> Any:
    global _BANKS
    if _BANKS is None:
        _BANKS = mel_banks()
    return _BANKS


def fbank(samples: Any) -> Any:
    """``samples`` (float, -1..1, 16 kHz) -> the model's features [T, 80] float32 (see the module docstring)."""
    import numpy as np

    x = samples.astype(np.float64) * 32768.0
    flen, fshift = FRAME_LENGTH, FRAME_SHIFT
    n = 1 + (len(x) - flen) // fshift
    idx = np.arange(flen)[None, :] + fshift * np.arange(n)[:, None]
    fr = x[idx]
    fr = fr - fr.mean(axis=1, keepdims=True)
    fr = np.concatenate([fr[:, :1] - PREEMPH * fr[:, :1], fr[:, 1:] - PREEMPH * fr[:, :-1]], axis=1)
    fr = fr * np.hamming(flen)
    spec = np.abs(np.fft.rfft(fr, n=N_FFT)) ** 2
    feats = np.log(np.maximum(spec @ _banks().T, np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


def samples_of(pcm: bytes) -> Any:
    """16 kHz mono 16-bit audio -> float32 samples in -1..1 (as the prototype read its files)."""
    import numpy as np

    data = bytes(pcm[: len(pcm) - len(pcm) % BYTES_PER_SAMPLE])
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def seconds_of(pcm: bytes) -> float:
    return len(pcm) // BYTES_PER_SAMPLE / SAMPLE_RATE


def speech_seconds(pcm: bytes) -> float:
    """How long the speech in ``pcm`` lasts: from its first to its last 30 ms frame at least SPEECH_ABOVE_DB above
    the room (its quietest tenth), with no absolute floor - a quiet microphone's wake is still measured. 0.0 when no
    frame is (all silence, or an even hum)."""
    frames = [talk.frame_db(pcm[at:at + talk.FRAME_BYTES])
              for at in range(0, len(pcm) - talk.FRAME_BYTES + 1, talk.FRAME_BYTES)]
    if not frames:
        return 0.0
    room = sorted(frames)[len(frames) // 10]
    loud = [index for index, db in enumerate(frames) if db > talk.SILENCE_DB and db >= room + SPEECH_ABOVE_DB]
    if not loud:
        return 0.0
    return (loud[-1] - loud[0] + 1) * talk.FRAME_MS / 1000.0


def take_window(clip: bytes, speech_end: int) -> bytes:
    """An enrolment take as a wake is heard: the last WAKE_CLIP_BYTES of ``clip`` ending at ``speech_end`` (the byte
    where its speech ended), so the template is made from the same window as each wake's check."""
    end = max(0, min(len(clip), int(speech_end)))
    end -= end % BYTES_PER_SAMPLE
    return bytes(clip[max(0, end - WAKE_CLIP_BYTES):end])


def take_problem(pcm: bytes) -> str:
    """What is wrong with an enrolment take ("" when it is fine): TAKE_SILENT (nothing at all from the microphone),
    TAKE_QUIET (no speech), TAKE_LONG (more than TAKE_MAX_S of speech), TAKE_SHORT (under MIN_SPEECH_S of speech,
    the measure each wake's check uses)."""
    peak = max((talk.frame_db(pcm[at:at + talk.FRAME_BYTES]) for at in range(0, len(pcm), talk.FRAME_BYTES)),
               default=talk.SILENCE_DB)
    if peak < talk.MUTED_DB:
        return TAKE_SILENT
    speech = speech_seconds(pcm)
    if speech <= 0.0:
        return TAKE_QUIET
    if speech > TAKE_MAX_S + 1e-9:
        return TAKE_LONG
    if speech < MIN_SPEECH_S:
        return TAKE_SHORT
    return ""


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------

class VoiceprintError(RuntimeError):
    """The speaker model could not be had; ``code`` is one of ERR_ADDON, ERR_MODEL, ERR_FAILED."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def load_session(folder: Path, *, threads: int = 2) -> Any:
    """The speaker model from ``folder`` on the CPU (VoiceprintError when it cannot be had)."""
    if not model_ready(folder):
        raise VoiceprintError(ERR_MODEL)
    try:
        import numpy  # noqa: F401 - the features need it; a missing one is the add-on's fault
        import onnxruntime as ort
    except ImportError as exc:
        raise VoiceprintError(ERR_ADDON) from exc
    try:
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, int(threads))
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.log_severity_level = 3
        return ort.InferenceSession(str(Path(folder) / MODEL_FILE.name), sess_options=options,
                                    providers=["CPUExecutionProvider"])
    except Exception as exc:  # noqa: BLE001 - a file that will not load is reported by its code, never its text
        raise VoiceprintError(ERR_FAILED) from exc


class Embedder:
    """``embed(pcm)``: 16 kHz mono 16-bit audio -> its unit-length embedding (EMBED_DIM numpy floats). ``session`` has
    onnxruntime's ``run(outputs, feeds)`` and ``get_inputs()`` (tests: a fake). VoiceprintError(ERR_FAILED) when the
    model gives anything but one finite, non-zero EMBED_DIM vector."""

    def __init__(self, session: Any) -> None:
        self._session = session
        try:
            self._input = str(session.get_inputs()[0].name or INPUT_NAME)
        except Exception:  # noqa: BLE001 - the model's own name for it
            self._input = INPUT_NAME

    def embed(self, pcm: bytes) -> Any:
        import numpy as np

        samples = samples_of(pcm)
        if len(samples) < FRAME_LENGTH:
            raise VoiceprintError(ERR_FAILED)
        feats = fbank(samples)
        try:
            out = self._session.run(None, {self._input: feats[None]})[0]
        except Exception as exc:  # noqa: BLE001 - reported by its code
            raise VoiceprintError(ERR_FAILED) from exc
        vector = np.asarray(out, dtype=np.float64).reshape(-1)
        if vector.size != EMBED_DIM or not np.all(np.isfinite(vector)):
            raise VoiceprintError(ERR_FAILED)
        norm = float(np.linalg.norm(vector))
        if not norm:
            raise VoiceprintError(ERR_FAILED)
        return vector / norm


def cosine(a: Any, b: Any) -> float:
    import numpy as np

    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b) / denominator if denominator else 0.0


def takes_agree(embeddings: Sequence[Any], minimum: float = AGREE_MIN) -> bool:
    """Every two enrolment takes at least ``minimum`` alike."""
    for i in range(len(embeddings)):
        for j in range(i + 1, len(embeddings)):
            if cosine(embeddings[i], embeddings[j]) < minimum:
                return False
    return len(embeddings) > 0


# --------------------------------------------------------------------------
# The template and its file
# --------------------------------------------------------------------------

def _finite_vector(values: Any) -> tuple[float, ...] | None:
    if not isinstance(values, list) or len(values) != EMBED_DIM:
        return None
    out = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        out.append(float(value))
    return tuple(out)


@dataclass(frozen=True)
class Template:
    """The enrolment embeddings and their mean. ``score(embedding)`` is the cosine to the normalised mean."""

    embeddings: tuple[tuple[float, ...], ...]
    mean: tuple[float, ...]
    created: str

    @classmethod
    def from_embeddings(cls, embeddings: Sequence[Any], created: str | None = None) -> Template:
        rows = tuple(tuple(float(value) for value in vector) for vector in embeddings)
        if not rows or any(len(row) != EMBED_DIM for row in rows):
            raise ValueError("embeddings of the wrong length")
        mean = tuple(sum(column) / len(rows) for column in zip(*rows))
        stamp = created or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        return cls(rows, mean, stamp)

    @property
    def samples(self) -> int:
        return len(self.embeddings)

    def score(self, embedding: Any) -> float:
        return cosine(self.mean, embedding)

    def to_json(self) -> dict:
        return {"version": VERSION, "created": self.created, "samples": self.samples,
                "embeddings": [[round(value, 7) for value in row] for row in self.embeddings],
                "mean": [round(value, 7) for value in self.mean]}

    @classmethod
    def from_json(cls, data: Any) -> tuple[Template | None, str]:
        """(the template, "") or (None, a reason word: BAD_VERSION, BAD_LENGTH, BAD_NUMBER, BAD_FILE)."""
        if not isinstance(data, dict):
            return None, BAD_FILE
        if data.get("version") != VERSION or isinstance(data.get("version"), bool):
            return None, BAD_VERSION
        samples, rows, mean = data.get("samples"), data.get("embeddings"), data.get("mean")
        created = data.get("created")
        if not isinstance(samples, int) or isinstance(samples, bool) or not 1 <= samples <= MAX_SAMPLES \
                or not isinstance(rows, list) or len(rows) != samples or not isinstance(mean, list) \
                or len(mean) != EMBED_DIM or any(not isinstance(row, list) or len(row) != EMBED_DIM for row in rows):
            return None, BAD_LENGTH
        checked = [_finite_vector(row) for row in rows]
        checked_mean = _finite_vector(mean)
        if checked_mean is None or any(row is None for row in checked) or not any(checked_mean):
            return None, BAD_NUMBER
        return cls(tuple(checked), checked_mean, str(created or "")[:40]), ""  # type: ignore[arg-type]


class TemplateStore:
    """VOICEPRINT_FILE in the data folder: ``load()`` -> (Template or None, reason word), ``save(template)`` (atomic)
    and ``forget()`` (deletes it). Each returns whether it worked; the logs hold reason words only."""

    def __init__(self, folder: Path | str) -> None:
        self.path = Path(folder) / VOICEPRINT_FILE
        self.sweep()                                   # a save cut short (the app ended in it) left one behind

    def sweep(self) -> int:
        """Deletes every leftover ``voiceprint.json.*.tmp`` (a save's temporary file); how many went."""
        gone = 0
        try:
            leftovers = list(self.path.parent.glob(f"{VOICEPRINT_FILE}.*.tmp"))
        except OSError:
            return 0
        for leftover in leftovers:
            try:
                leftover.unlink()
                gone += 1
            except OSError:
                pass
        return gone

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> tuple[Template | None, str]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None, WHY_NO_TEMPLATE
        except (OSError, UnicodeDecodeError):
            logger.warning("Voiceprint: the template was not used (%s)", BAD_FILE)
            return None, BAD_FILE
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        template, why = Template.from_json(data)
        if template is None:
            logger.warning("Voiceprint: the template was not used (%s)", why)
        return template, why

    def save(self, template: Template) -> bool:
        part: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, part = tempfile.mkstemp(prefix=f"{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(template.to_json()) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            atomic.replace(part, self.path)
            part = None
            return True
        except OSError as exc:
            logger.warning("Voiceprint: the template could not be saved (%s)", type(exc).__name__)
            return False
        finally:
            if part is not None:
                try:
                    os.unlink(part)
                except OSError:
                    pass

    def forget(self) -> bool:
        self.sweep()
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            logger.warning("Voiceprint: the template could not be deleted (%s)", type(exc).__name__)
            return False


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    """MATCH, MISMATCH or UNCHECKED (with ``reason``, a WHY_* word). ``score`` (rounded to 2 places) when one was
    made. Only a mismatch keeps a wake out: ``allows``."""

    kind: str
    score: float | None = None
    reason: str = ""

    @property
    def allows(self) -> bool:
        return self.kind != MISMATCH


def unchecked(reason: str) -> Verdict:
    return Verdict(UNCHECKED, None, reason)


def check(pcm: bytes, template: Template | None, embedder: Any, *, threshold: float = THRESHOLD,
          clock: Callable[[], float] | None = None) -> tuple[Verdict, int | None]:
    """The wake clip ``pcm`` against ``template`` with ``embedder``: (the verdict, how long the embedding took in ms;
    None when none was made - a fast one is 0). Never raises."""
    import time

    if template is None:
        return unchecked(WHY_NO_TEMPLATE), None
    if speech_seconds(pcm) < MIN_SPEECH_S:
        return unchecked(WHY_SHORT), None
    tick = clock or time.monotonic
    began = tick()
    try:
        embedding = embedder.embed(pcm)
        score = template.score(embedding)
    except Exception:  # noqa: BLE001 - a model that will not run lets the wake through (fail open)
        return unchecked(WHY_FAILED), int((tick() - began) * 1000)
    took = int((tick() - began) * 1000)
    if not math.isfinite(score):
        return unchecked(WHY_FAILED), took
    rounded = round(score, 2)
    return Verdict(MATCH if score >= threshold else MISMATCH, rounded), took
