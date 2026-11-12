"""Meeting recording, part 1: the speech model (downloaded only on the owner's click) and the transcription.

Speech to text runs on this PC with faster-whisper (owner decision: model medium.en, CPU, int8). The model is
``Systran/faster-whisper-medium.en`` from Hugging Face (about 1.5 GB), stored in
``%LOCALAPPDATA%\\briefing-reader\\models\\faster-whisper-medium.en``. ``ModelDownloader`` fetches it only when
the owner clicks Download (Settings > Meetings or the Meetings screen): with progress, resumable (each file
grows in ``<name>.part`` and continues with an HTTP Range request), file checks (the size Hugging Face lists and
the SHA-256 it lists for its large files), then ``manifest.json``. The app never runs pip: a missing add-on is
named on the Meetings screen (``missing_pieces``).

The transcription is a separate background process at below-normal priority (``TranscriptionProcess``:
``python -m briefing_reader.meeting_speech --meeting DIR --model DIR``) that reports progress as JSON lines on
its stdout (never the text) and writes ``transcript.json`` into the meeting's folder: each channel transcribed on
its own, then merged by time into turns - on a call the microphone as "You" and the call audio as "Others"; in
person (owner decision 2026-10-22) the microphone only, every turn "Room" (one microphone in the room: who spoke
is not known, so nothing is the owner's). Either way a turn ends by TURN_CHARS, so a summary gets every word. The kind
is the meeting record's (``meeting_kind``; a folder without a record whose only recording is ``mic.wav`` is in
person).
``transcribe_meeting(folder, transcriber, progress)`` is that work, with any transcriber (tests: a fake one).

Public API
==========
``MODEL_REPO``, ``MODEL_FILES``, ``MODEL_SIZE_TEXT``, ``model_dir(models_root)``, ``model_ready(dir)``,
``speech_missing()`` (the faster-whisper add-on, or ""), ``missing_pieces(models_root)`` -> list of
``Missing(key, text)`` (add-ons and the model, in the words the Meetings screen shows).
``ModelDownloader(target, session=None, progress=None, cancel=None)``: ``run()`` -> "" or a short error code.
``Segment(start, end, text)``, ``merge_turns(mic, loopback, gap=, speakers=, most=)``, ``meeting_kind(folder)``,
``transcribe_meeting(folder, transcriber, progress, kind=)``, ``read_wav(path)`` (a recording as samples for the
model).
``FasterWhisperTranscriber(model_dir)`` (the add-on behind a small adapter, imported only when used).
``TranscriptionProcess(meeting_dir, model_dir, on_progress, on_done)``: ``start()``, ``cancel()`` (kills the worker
and waits until it ended), ``running()``.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import threading
import wave
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gpu
from . import meetings as mt

logger = logging.getLogger(__name__)

MODEL_NAME = "medium.en"
MODEL_REPO = "Systran/faster-whisper-medium.en"
MODEL_FOLDER = "faster-whisper-medium.en"
MODEL_REVISION = "main"
MODEL_FILES = ("config.json", "tokenizer.json", "vocabulary.txt", "model.bin")
MODEL_SIZE_TEXT = "about 1.5 GB"
MODEL_URL = "https://huggingface.co/{repo}/resolve/{revision}/{name}"
MANIFEST = "manifest.json"
DOWNLOAD_TEXT = f"Download the speech model - {MODEL_SIZE_TEXT} from Hugging Face ({MODEL_REPO})"
SPEECH_MODULE = "faster_whisper"
ADDON_SPEECH = "faster-whisper"
CHUNK = 1 << 20
TIMEOUT_S = 30
MERGE_GAP_S = 1.5
TURN_CHARS = 1_500                # a turn ends before it passes this (in person no change of speaker ends one; on a
                                  # call one person may talk for minutes), and a summary keeps at most 2,000
                                  # characters of a turn (ask.meeting_summary.TURN_CAP)
CANCEL_WAIT_S = 5.0               # a stopped worker is waited for this long (its files are free after)
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA1_RE = re.compile(r"[0-9a-f]{40}")
GIT_PREFIX = "git:"                # a small file's check: its git blob SHA-1 (Hugging Face's X-Linked-Etag)
_IDENTITY = {"Accept-Encoding": "identity"}   # the files as stored: a gzip answer has another size

ERR_NETWORK = "network"
ERR_CHECK = "check_failed"
ERR_DISK = "disk"
ERR_CANCELLED = "cancelled"
ERR_ADDON = "addon_missing"
ERR_MODEL = "model_missing"
ERR_AUDIO = "audio_missing"
ERR_WORKER = "worker_failed"


@dataclass(frozen=True)
class SpeechModel:
    """A faster-whisper model on Hugging Face: what ModelDownloader fetches (only on the owner's click) and the
    folder under the models root it lives in. Meetings use MEETING_MODEL; talking to Jarvis ([talk] model) any of
    SPEECH_MODELS."""

    name: str
    repo: str
    folder: str
    size_text: str
    files: tuple[str, ...] = MODEL_FILES
    revision: str = MODEL_REVISION


MEETING_MODEL = SpeechModel(MODEL_NAME, MODEL_REPO, MODEL_FOLDER, MODEL_SIZE_TEXT)
# The large-v3 family's models carry a preprocessor file and a JSON vocabulary (MIT, like the rest).
LARGE_V3_FILES = ("config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json")
SPEECH_MODELS = {model.name: model for model in (
    SpeechModel("base.en", "Systran/faster-whisper-base.en", "faster-whisper-base.en", "about 145 MB"),
    SpeechModel("small.en", "Systran/faster-whisper-small.en", "faster-whisper-small.en", "about 480 MB"),
    MEETING_MODEL,
    SpeechModel("distil-large-v3", "Systran/faster-distil-whisper-large-v3", "faster-distil-whisper-large-v3",
                "about 1.5 GB", LARGE_V3_FILES))}
_last_device = {"device": ""}      # where the last writing down ran (the app's process: Settings > Meetings)


def last_device() -> str:
    """gpu.CUDA or gpu.CPU: where the last meeting was written down in this run of Jarvis ("" before one)."""
    return _last_device["device"]


# --------------------------------------------------------------------------
# What is missing
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Missing:
    key: str           # "audio_addon", "speech_addon", "model"
    text: str


def model_dir(models_root: Path, model: SpeechModel | None = None) -> Path:
    """Where ``model`` lives (the meetings' model when None)."""
    return Path(models_root) / (model or MEETING_MODEL).folder


def model_ready(folder: Path, model: SpeechModel | None = None) -> bool:
    """Every model file there with the size its manifest recorded after the checks."""
    try:
        manifest = json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
        files = manifest.get("files", {})
        return all((Path(folder) / name).is_file() and (Path(folder) / name).stat().st_size == int(files[name])
                   for name in (model or MEETING_MODEL).files)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def speech_missing() -> str:
    try:
        return "" if importlib.util.find_spec(SPEECH_MODULE) is not None else ADDON_SPEECH
    except (ImportError, ValueError):
        return ADDON_SPEECH


def missing_pieces(models_root: Path, *, audio_missing: Callable[[], str] | None = None,
                   speech: Callable[[], str] | None = None) -> list[Missing]:
    """What the Meetings screen says is missing, exactly (empty: everything is there)."""
    if audio_missing is None:
        from .meeting_audio import backend_missing as audio_missing
    pieces = []
    audio = audio_missing()
    if audio:
        pieces.append(Missing("audio_addon", f"The Python add-on {audio}, to record the microphone and the call "
                                             "audio. It is installed with your OK; Jarvis never installs it."))
    words = (speech or speech_missing)()
    if words:
        pieces.append(Missing("speech_addon", f"The Python add-on {words}, to write the meeting down on this PC. "
                                              "It is installed with your OK; Jarvis never installs it."))
    if not model_ready(model_dir(models_root)):
        pieces.append(Missing("model", f"The speech model {MODEL_NAME} ({MODEL_SIZE_TEXT}). It is downloaded only "
                                       "when you click Download."))
    return pieces


# --------------------------------------------------------------------------
# The model download (only on the owner's click)
# --------------------------------------------------------------------------

class ModelDownloader:
    """See the module docstring. ``progress(done_bytes, total_bytes)`` is called on the downloading thread;
    ``cancel()`` (an Event-like ``is_set``) stops between chunks. ``session`` is a requests-like session
    (``head(url, allow_redirects=, timeout=)`` and ``get(url, headers=, stream=, timeout=)``). ``LOG_NAME`` begins
    its log lines (wake.WakeModelDownloader: "Wake model")."""

    LOG_NAME = "Speech model"

    def __init__(self, target: Path, *, session: Any = None, progress: Callable[[int, int], Any] | None = None,
                 cancel: Any = None, repo: str = MODEL_REPO, revision: str = MODEL_REVISION,
                 files: Sequence[str] = MODEL_FILES) -> None:
        self.target = Path(target)
        self._session = session
        self._progress = progress
        self._cancel = cancel or threading.Event()
        self.repo, self.revision, self.files = repo, revision, tuple(files)

    @classmethod
    def for_model(cls, models_root: Path, model: SpeechModel, **kwargs: Any) -> ModelDownloader:
        """A downloader of ``model`` into its folder under ``models_root`` (session, progress, cancel as above)."""
        return cls(model_dir(models_root, model), repo=model.repo, revision=model.revision, files=model.files,
                   **kwargs)

    def cancel(self) -> None:
        self._cancel.set()

    def _http(self) -> Any:
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _url(self, name: str) -> str:
        return MODEL_URL.format(repo=self.repo, revision=self.revision, name=name)

    def _expected(self, name: str) -> tuple[int, str]:
        """(size, check) from the listing's headers. Hugging Face: a large file answers with X-Linked-Size and
        its SHA-256 as X-Linked-Etag; a small one redirects (its Content-Length is the redirect's own body), so
        the size comes from following it uncompressed and the check is its git blob SHA-1 ("git:<sha1>")."""
        response = self._http().head(self._url(name), allow_redirects=False, timeout=TIMEOUT_S)
        status = int(getattr(response, "status_code", 0))
        if status >= 400:
            raise ConnectionError(f"listing answered {status}")
        headers = {str(k).lower(): str(v) for k, v in dict(response.headers).items()}
        size = headers.get("x-linked-size") or headers.get("content-length") or "0"
        etag = (headers.get("x-linked-etag") or "").strip().strip('"').lower()
        if status in (301, 302, 303, 307, 308) and not headers.get("x-linked-size"):
            follow = self._http().head(self._url(name), allow_redirects=True, timeout=TIMEOUT_S,
                                       headers=dict(_IDENTITY))
            size = str(dict(follow.headers).get("Content-Length") or dict(follow.headers).get("content-length")
                       or size)
        if _SHA256_RE.fullmatch(etag):
            return int(size), etag
        return int(size), GIT_PREFIX + etag if _GIT_SHA1_RE.fullmatch(etag) else ""

    def run(self) -> str:
        """Fetch every file (continuing the ``.part`` files), check them, write the manifest. "" when the model
        is ready, else a short error code (ERR_*). Never raises: a file error (a scanner holding the new file,
        say) is ERR_DISK, so the app always hears that the download ended."""
        try:
            return self._run()
        except OSError as exc:
            logger.info("%s: a file could not be written (%s)", self.LOG_NAME, type(exc).__name__)
            return ERR_DISK
        except Exception as exc:  # noqa: BLE001 - a download never dies silently
            logger.info("%s: the download stopped (%s)", self.LOG_NAME, type(exc).__name__)
            return ERR_NETWORK

    def _run(self) -> str:
        try:
            self.target.mkdir(parents=True, exist_ok=True)
            expected = {name: self._expected(name) for name in self.files}
        except OSError:
            return ERR_DISK
        except Exception as exc:  # noqa: BLE001 - network problems are a code, never a crash
            logger.info("%s: the file list could not be read (%s)", self.LOG_NAME, type(exc).__name__)
            return ERR_NETWORK
        total = sum(size for size, _sha in expected.values())
        done_before = 0
        for name in self.files:
            size, sha = expected[name]
            final = self.target / name
            if final.is_file() and final.stat().st_size == size and not sha:
                done_before += size
                continue
            code = self._fetch(name, size, sha, done_before, total)
            if code:
                logger.info("%s: %s stopped (%s)", self.LOG_NAME, name, code)
                return code
            done_before += size
        manifest = {"repo": self.repo, "revision": self.revision,
                    "files": {name: expected[name][0] for name in self.files},
                    "sha256": {name: expected[name][1] for name in self.files
                               if expected[name][1] and not expected[name][1].startswith(GIT_PREFIX)}}
        try:
            mt._write_json(self.target / MANIFEST, manifest)
        except OSError:
            return ERR_DISK
        logger.info("%s: ready (%d files, %d MB)", self.LOG_NAME, len(self.files), total // (1 << 20))
        return ""

    def _fetch(self, name: str, size: int, sha: str, done_before: int, total: int) -> str:
        final = self.target / name
        part = self.target / f"{name}.part"
        final.parent.mkdir(parents=True, exist_ok=True)          # a file in a subfolder ("onnx/model.onnx")
        if final.is_file() and final.stat().st_size == size:
            final.replace(part)                      # checked again below (its hash, when Hugging Face gave one)
        have = part.stat().st_size if part.exists() else 0
        if have > size:
            part.unlink()
            have = 0
        if have < size:
            headers = dict(_IDENTITY)
            if have:
                headers["Range"] = f"bytes={have}-"
            try:
                response = self._http().get(self._url(name), headers=headers, stream=True, timeout=TIMEOUT_S)
                status = int(getattr(response, "status_code", 0))
                if status >= 400:
                    return ERR_NETWORK
                if have and status != 206:            # the server ignored the Range: start this file again
                    have = 0
                fh = open(part, "ab" if have else "wb")
            except Exception as exc:  # noqa: BLE001 - network problems are a code; the .part file stays
                logger.info("%s: the download could not start (%s)", self.LOG_NAME, type(exc).__name__)
                return ERR_DISK if isinstance(exc, PermissionError) else ERR_NETWORK
            with fh:
                chunks = iter(response.iter_content(CHUNK))
                while True:
                    if self._cancel.is_set():
                        return ERR_CANCELLED
                    try:
                        chunk = next(chunks, None)
                    except Exception as exc:  # noqa: BLE001 - the connection dropped; the .part file stays
                        logger.info("%s: download interrupted (%s)", self.LOG_NAME, type(exc).__name__)
                        return ERR_NETWORK
                    if chunk is None:
                        break
                    if not chunk:
                        continue
                    try:
                        fh.write(chunk)
                    except OSError:
                        return ERR_DISK
                    have += len(chunk)
                    if self._progress is not None:
                        self._progress(done_before + have, total)
        if part.stat().st_size != size:
            return ERR_NETWORK if part.stat().st_size < size else ERR_CHECK
        if sha:
            digest = (GIT_PREFIX + _git_sha1(part, self._cancel) if sha.startswith(GIT_PREFIX)
                      else _sha256(part, self._cancel))
            if self._cancel.is_set():
                return ERR_CANCELLED             # stopped while checking: the whole .part stays (checked next time)
            if digest != sha:
                part.unlink(missing_ok=True)
                return ERR_CHECK
        part.replace(final)
        return ""


def _git_sha1(path: Path, cancel: Any) -> str:
    """The file's git blob SHA-1 (what Hugging Face gives as the X-Linked-Etag of a small file)."""
    digest = hashlib.sha1(b"blob %d\x00" % path.stat().st_size, usedforsecurity=False)
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            if cancel.is_set():
                return ""
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(path: Path, cancel: Any) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            if cancel.is_set():
                return ""
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# The transcription
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str = field(repr=False)


def merge_turns(mic: Iterable[Segment], loopback: Iterable[Segment] = (), gap: float = MERGE_GAP_S, *,
                speakers: dict[str, str] | None = None, most: int = 0) -> list[dict]:
    """Both channels' segments in time order as turns ({speaker, start, end, text}): "You" for the microphone,
    "Others" for the call audio (``speakers``: in person {"mic": "Room"}, no call audio); a speaker's segments
    closer than ``gap`` seconds join one turn - while its text stays within ``most`` characters when that is given
    (TURN_CHARS: in person one speaker stands for everyone, and on a call one person may talk for minutes)."""
    names = speakers or mt.SPEAKERS
    tagged = [(seg, names[mt.MIC]) for seg in mic] + [(seg, names.get(mt.LOOPBACK, mt.SPEAKERS[mt.LOOPBACK]))
                                                      for seg in loopback]
    tagged.sort(key=lambda item: (item[0].start, item[0].end))
    turns: list[dict] = []
    for seg, speaker in tagged:
        text = " ".join(seg.text.split())
        if not text:
            continue
        last = turns[-1] if turns else None
        if (last is not None and last["speaker"] == speaker and seg.start - last["end"] <= gap
                and (not most or len(last["text"]) + 1 + len(text) <= most)):
            last["text"] = f"{last['text']} {text}"
            last["end"] = max(last["end"], round(seg.end, 2))
        else:
            turns.append({"speaker": speaker, "start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
    return turns


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate() or mt.SAMPLE_RATE)
    except (OSError, wave.Error, EOFError):
        return 0.0


def read_wav(path: Path) -> Any:
    """A recording as Whisper hears it: mono float32 samples in [-1, 1] at ``mt.SAMPLE_RATE`` (16 kHz, numpy - it
    comes with the add-on). The recorder writes 16-bit PCM at that rate already; another rate is resampled
    (linear) and more channels are averaged. Reading it here, not through the add-on's own decoder, keeps the
    transcription independent of the PyAV version installed beside faster-whisper (PyAV 15 and later refuse the
    ``metadata_errors`` argument faster-whisper 1.2 passes, which failed every meeting). Raises ValueError (not
    16-bit PCM), wave.Error or EOFError for a file it cannot read."""
    import numpy as np

    with wave.open(str(path), "rb") as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    if width != 2 or channels < 1 or rate <= 0:
        raise ValueError("not 16-bit PCM")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples[: len(samples) // channels * channels].reshape(-1, channels).mean(axis=1)
    if rate != mt.SAMPLE_RATE and len(samples) > 1:
        count = max(1, round(len(samples) * mt.SAMPLE_RATE / rate))
        positions = np.linspace(0, len(samples) - 1, count)
        samples = np.interp(positions, np.arange(len(samples)), samples)
    return np.ascontiguousarray(samples, dtype=np.float32)


class FasterWhisperTranscriber:
    """faster-whisper behind a small adapter: on an NVIDIA GPU in float16 when it can, else the CPU in int8
    (gpu.load_model; ``device`` "auto" or "cpu", ``self.device`` says which);
    ``transcribe(path)`` yields Segments (``vad_filter`` skips the silence, English only). The recording is handed
    over as samples (``read_wav``); only a file that cannot be read that way goes to the add-on's own decoder."""

    def __init__(self, folder: Path, *, threads: int = 0, device: str = gpu.DEVICE_AUTO) -> None:
        module = importlib.import_module(SPEECH_MODULE)
        if device != gpu.DEVICE_CPU:
            gpu.prepare()                                # NVIDIA's libraries on this process's DLL search path
        self._model, self.device = gpu.load_model(module.WhisperModel, Path(folder), device=device,
                                                  threads=threads or max(1, (os.cpu_count() or 4) - 2),
                                                  warm=gpu.warm_up)

    def transcribe(self, path: Path) -> Iterable[Segment]:
        try:
            audio: Any = read_wav(path)
        except (ValueError, wave.Error, EOFError):
            audio = str(path)
        segments, _info = self._model.transcribe(audio, language="en", beam_size=5, vad_filter=True)
        for seg in segments:
            yield Segment(float(seg.start), float(seg.end), str(seg.text))


def meeting_kind(folder: Path) -> str:
    """The kind of the meeting in ``folder``: its record's (meeting.json; a record written before kinds existed is
    a call), else - no record to read - in person when ``mic.wav`` is its only recording, else a call. Only the
    kind is read from the record."""
    folder = Path(folder)
    try:
        data = json.loads((folder / mt.RECORD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        return mt.clean_kind(data.get("kind"))
    mic, loop = (folder / mt.AUDIO_FILES[kind] for kind in mt.CHANNELS)
    return mt.KIND_IN_PERSON if mic.is_file() and not loop.is_file() else mt.KIND_CALL


def transcribe_meeting(folder: Path, transcriber: Any, progress: Callable[[int], Any] | None = None, *,
                       kind: str | None = None) -> dict:
    """Transcribe the meeting in ``folder`` and write ``transcript.json``: on a call ``mic.wav`` ("You") and
    ``loopback.wav`` ("Others") merged by time; in person (``kind``, else ``meeting_kind(folder)``) ``mic.wav``
    only, every turn "Room". ``progress(percent)`` as the audio is worked through (the files together). Returns
    the transcript."""
    folder = Path(folder)
    kind = mt.clean_kind(kind) if kind is not None else meeting_kind(folder)
    channels = mt.channels(kind)
    paths = {channel: folder / mt.AUDIO_FILES[channel] for channel in channels}
    present = {channel: path for channel, path in paths.items() if path.is_file()}
    if not present:
        raise FileNotFoundError("no audio")
    durations = {channel: wav_duration(path) for channel, path in present.items()}
    total = sum(durations.values()) or 1.0
    done_before = 0.0
    last = -1
    found: dict[str, list[Segment]] = {channel: [] for channel in mt.CHANNELS}
    for channel in channels:
        path = present.get(channel)
        if path is None:
            continue
        for seg in transcriber.transcribe(path):
            found[channel].append(seg)
            percent = int(min(99, 100 * (done_before + min(seg.end, durations[channel])) / total))
            if progress is not None and percent > last:
                last = percent
                progress(percent)
        done_before += durations[channel]
        percent = int(min(99, 100 * done_before / total))
        if progress is not None and percent > last:
            last = percent
            progress(percent)
    speakers = mt.speakers(kind)
    transcript = {"version": 1, "kind": kind, "speakers": speakers,
                  "durations": {channel: round(seconds, 1) for channel, seconds in durations.items()},
                  "turns": merge_turns(found[mt.MIC], found[mt.LOOPBACK], speakers=speakers,
                                       most=TURN_CHARS)}
    mt._write_json(folder / mt.TRANSCRIPT_FILE, transcript)
    if progress is not None:
        progress(100)
    return transcript


class TranscriptionProcess:
    """The background process; ``on_progress(percent)`` and ``on_done(error_code)`` ("" = done) are called on
    its reader thread. ``python`` defaults to this interpreter."""

    def __init__(self, meeting_dir: Path, model: Path, *, on_progress: Callable[[int], Any],
                 on_done: Callable[[str], Any], python: str | None = None, device: str = gpu.DEVICE_AUTO) -> None:
        self.meeting_dir, self.model = Path(meeting_dir), Path(model)
        self.device_setting = device if device in gpu.DEVICES else gpu.DEVICE_AUTO
        if _last_device.get("fell_back"):
            self.device_setting = gpu.DEVICE_CPU    # the GPU took a worker down this run: the CPU from now on
        self.device = ""                 # where the worker said it runs (gpu.CUDA or gpu.CPU)
        self._on_progress, self._on_done = on_progress, on_done
        self._python = python or sys.executable
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    def start(self) -> None:
        flags = BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW if os.name == "nt" else 0
        root = str(Path(__file__).resolve().parent.parent)
        self._proc = subprocess.Popen(
            [self._python, "-B", "-m", "briefing_reader.meeting_speech", "--meeting", str(self.meeting_dir),
             "--model", str(self.model), "--device", self.device_setting], cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=flags)
        threading.Thread(target=self._read, name="meeting-transcribe", daemon=True).start()

    def _read(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        error = ERR_WORKER
        kind = ""
        told = False                     # the worker said how it ended (done, or an error code)
        for raw in proc.stdout:
            try:
                message = json.loads(raw.decode("ascii", "replace"))
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("device") in (gpu.CUDA, gpu.CPU):
                self.device = _last_device["device"] = message["device"]
                logger.info("Meetings: writing down on the %s", "GPU" if self.device == gpu.CUDA else "CPU")
            if isinstance(message.get("progress"), int):
                self._on_progress(max(0, min(100, message["progress"])))
            if message.get("done") is True:
                error, told = "", True
            elif isinstance(message.get("error"), str):
                told = True
                error = message["error"][:40]
                found = message.get("kind")
                kind = found[:40] if isinstance(found, str) and found.isidentifier() else ""
        proc.stdout.close()
        code = proc.wait()
        if not told and not self._cancelled and self.device_setting == gpu.DEVICE_AUTO \
                and self.device in (gpu.CUDA, ""):
            # It ended without a word, maybe taken down by the GPU: once more on the CPU, which stays for this run.
            logger.info("Meetings: the writing down ended on its own (exit code %s); again on the CPU", code)
            _last_device["fell_back"] = True
            self.device_setting, self.device = gpu.DEVICE_CPU, ""
            self.start()
            return
        if error and not self._cancelled:
            # The failure's kind only (an exception's type name) and the exit code: never a library message.
            logger.info("Meetings: the writing down failed (%s%s, exit code %s)", error,
                        f", {kind}" if kind else "", code)
        self._on_done(ERR_CANCELLED if self._cancelled else error)

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def cancel(self, wait_s: float = CANCEL_WAIT_S) -> bool:
        """Stop the worker and wait (up to ``wait_s``) until it has ended, so the files it held are free (a
        Delete right after removes them all). True when no worker is left running."""
        self._cancelled = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return True
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            logger.info("Meetings: the writing down did not end within %.0f s", wait_s)
            return False
        return True


def _emit(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    """The worker process: progress and the outcome as JSON lines; never the text, never a library message."""
    parser = argparse.ArgumentParser(prog="briefing_reader.meeting_speech")
    parser.add_argument("--meeting", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", choices=gpu.DEVICES, default=gpu.DEVICE_AUTO)
    args = parser.parse_args(argv)
    if speech_missing():
        _emit({"error": ERR_ADDON})
        return 2
    if not model_ready(Path(args.model)):
        _emit({"error": ERR_MODEL})
        return 2
    try:
        transcriber = FasterWhisperTranscriber(Path(args.model), device=args.device)
        _emit({"device": getattr(transcriber, "device", gpu.CPU)})   # a status: where it runs
        try:
            transcribe_meeting(Path(args.meeting), transcriber, lambda percent: _emit({"progress": percent}))
        except FileNotFoundError:
            raise
        except Exception:  # noqa: BLE001 - on the GPU: the whole meeting again on the CPU (else as before)
            if getattr(transcriber, "device", gpu.CPU) != gpu.CUDA:
                raise
            transcriber = None
            gc.collect()                                 # the GPU's model is let go first
            transcriber = FasterWhisperTranscriber(Path(args.model), device=gpu.DEVICE_CPU)
            _emit({"device": gpu.CPU})
            transcribe_meeting(Path(args.meeting), transcriber, lambda percent: _emit({"progress": percent}))
    except FileNotFoundError:
        _emit({"error": ERR_AUDIO})
        return 1
    except Exception as exc:  # noqa: BLE001 - only the kind of failure goes out
        _emit({"error": ERR_WORKER, "kind": type(exc).__name__})
        return 1
    _emit({"done": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
