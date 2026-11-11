"""Talking to Jarvis: the warm speech process and the app's side of it (owner decisions 2026-11-09).

``python -m briefing_reader.talk_speech --model DIR [--threads 4] [--idle-s 600] [--hints TEXT] [--device auto|cpu]``
loads the speech model (faster-whisper) once and turns each talk's audio into text, so only the first talk after a
while waits for the model. "auto" puts it on an NVIDIA GPU in float16 when NVIDIA's libraries and a CUDA device are
there (gpu.load_model, warmed once), else - or on any failure there - on the CPU in int8, as before. It runs at below-normal priority (SpeechClient starts it so), and it ends on its own
after ``--idle-s`` seconds without a talk, when its input closes, or on a clip of length zero.

Protocol. stdin takes clips: a 4-byte little-endian length, then that many bytes of 16 kHz mono 16-bit audio (at
most MAX_CLIP_BYTES). stdout answers with one JSON line each: first ``{"ready": true, "load_ms": N, "device": "cuda"|"cpu"}`` (or
``{"ready": false, "error": CODE}``, and the process ends), then for each clip ``{"ok": true, "text": "...",
"ms": N}`` or ``{"ok": false, "error": CODE}``. The words travel only on this pipe: the process logs nothing,
writes no file and keeps no audio.

:class:`SpeechClient` is the app's side (no Qt). ``understand(pcm)`` starts the process if it is not running,
sends the clip and waits for its answer (blocking: never on the GUI thread); a process that ended meanwhile (it
went idle) is started again once. ``close()`` ends it. ``spawn`` (tests) replaces the real process.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

from . import gpu, talk

MAX_CLIP_BYTES = talk.SAMPLE_RATE * talk.BYTES_PER_SAMPLE * int(talk.MAX_TALK_S + 5)
IDLE_S = 600.0
THREADS = 4
READY_TIMEOUT_S = 90.0            # loading the model (the first talk after a while; a slow disk)
ANSWER_TIMEOUT_S = 60.0           # understanding one clip (at most MAX_TALK_S of speech)
CLOSE_WAIT_S = 3.0
IDLE_MARGIN_S = 5.0               # a process this close to its idle end is replaced rather than raced
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000
SPEECH_MODULE = "faster_whisper"

ERR_ADDON = "addon_missing"       # faster-whisper is not installed
ERR_MODEL = "model_missing"       # the model's folder is not ready
ERR_FAILED = "failed"             # the model could not load, or understanding failed
ERR_TOO_LONG = "too_long"         # a clip over MAX_CLIP_BYTES
ERR_ENDED = "ended"               # the process ended without an answer
ERR_TIMEOUT = "timeout"           # no answer in time
ERR_START = "start_failed"        # the process could not be started


class Transcriber(Protocol):
    def transcribe(self, pcm: bytes) -> str: ...


class ModelMissing(RuntimeError):
    pass


class FasterWhisperTalk:
    """faster-whisper behind ``transcribe(pcm) -> text``: the GPU in float16 or the CPU in int8 (``device``: see the
    module docs; ``self.device`` says which), beam 1, its voice filter, no previous-text conditioning, the hint
    prompt; talk.clean_text keeps what it is sure of."""

    def __init__(self, model_dir: Path, *, threads: int = THREADS, hints: str = "",
                 device: str = gpu.DEVICE_AUTO) -> None:
        from .meeting_speech import model_ready

        if not model_ready(Path(model_dir), _model_entry(Path(model_dir))):
            raise ModelMissing(str(model_dir))
        import numpy as np
        from faster_whisper import WhisperModel

        self._np = np
        self._hints = hints or None
        if device != gpu.DEVICE_CPU:
            gpu.prepare()                                # NVIDIA's libraries on this process's DLL search path
        self._load_args = (WhisperModel, Path(model_dir), threads)
        self._model, self.device = gpu.load_model(WhisperModel, Path(model_dir), device=device, threads=threads,
                                                  warm=gpu.warm_up, local_files_only=True)

    def to_cpu(self) -> None:
        """The GPU failed on a clip: the CPU's model from now on (the GPU's is let go first)."""
        factory, folder, threads = self._load_args
        self._model = None
        gc.collect()
        self._model, self.device = gpu.load_model(factory, folder, device=gpu.DEVICE_CPU, threads=threads,
                                                  local_files_only=True)

    def transcribe(self, pcm: bytes) -> str:
        samples = self._np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=self._np.int16).astype(self._np.float32)
        samples /= 32768.0
        segments, _info = self._model.transcribe(samples, language="en", beam_size=1, vad_filter=True,
                                                 condition_on_previous_text=False, initial_prompt=self._hints,
                                                 without_timestamps=True)
        return talk.clean_text(list(segments))


def _model_entry(folder: Path) -> Any:
    """The SpeechModel whose folder ``folder`` is (its files are what model_ready checks); None for an unknown one."""
    from .meeting_speech import SPEECH_MODELS

    return next((model for model in SPEECH_MODELS.values() if model.folder == folder.name), None)


def _write(out: IO[str], payload: dict) -> None:
    out.write(json.dumps(payload, ensure_ascii=False) + "\n")
    out.flush()


def _read_exact(stream: IO[bytes], count: int) -> bytes | None:
    data = bytearray()
    while len(data) < count:
        piece = stream.read(count - len(data))
        if not piece:
            return None
        data += piece
    return bytes(data)


_EOF = object()
_TOO_LONG = object()


def _reader(stream: IO[bytes], clips: queue.Queue) -> None:
    """Clips from ``stream`` into ``clips``: bytes, b"" (quit), _TOO_LONG or _EOF (then it stops)."""
    while True:
        header = _read_exact(stream, 4)
        if header is None:
            clips.put(_EOF)
            return
        (length,) = struct.unpack("<I", header)
        if length > MAX_CLIP_BYTES:
            clips.put(_TOO_LONG)
            return
        if length == 0:
            clips.put(b"")
            return
        data = _read_exact(stream, length)
        if data is None:
            clips.put(_EOF)
            return
        clips.put(data)


def serve(inp: IO[bytes], out: IO[str], load: Callable[[], Transcriber], *, idle_s: float = IDLE_S,
          clock: Callable[[], float] = time.monotonic) -> int:
    """The process's loop (see the module docstring); returns its exit code."""
    began = clock()
    try:
        transcriber = load()
    except ImportError:
        _write(out, {"ready": False, "error": ERR_ADDON})
        return 2
    except ModelMissing:
        _write(out, {"ready": False, "error": ERR_MODEL})
        return 2
    except Exception:  # noqa: BLE001 - any failure to load is reported by its code, never its text
        _write(out, {"ready": False, "error": ERR_FAILED})
        return 2
    device = getattr(transcriber, "device", gpu.CPU)
    _write(out, {"ready": True, "load_ms": int((clock() - began) * 1000),
                 "device": device if device in (gpu.CUDA, gpu.CPU) else gpu.CPU})
    clips: queue.Queue = queue.Queue()
    threading.Thread(target=_reader, args=(inp, clips), name="talk-speech-in", daemon=True).start()
    while True:
        try:
            clip = clips.get(timeout=idle_s)
        except queue.Empty:
            return 0                                  # idle: the next talk starts the process again
        if clip is _EOF or clip == b"":
            return 0
        if clip is _TOO_LONG:
            _write(out, {"ok": False, "error": ERR_TOO_LONG})
            return 1
        began = clock()
        try:
            text = transcriber.transcribe(clip)
        except Exception:  # noqa: BLE001 - one clip that fails never ends the process
            text = _on_the_cpu(transcriber, clip)        # the GPU failed: the CPU's model, and this clip again
            if text is None:
                _write(out, {"ok": False, "error": ERR_FAILED})
                continue
        device = getattr(transcriber, "device", gpu.CPU)
        _write(out, {"ok": True, "text": text, "ms": int((clock() - began) * 1000),
                     "device": device if device in (gpu.CUDA, gpu.CPU) else gpu.CPU})


def _on_the_cpu(transcriber: Any, clip: bytes) -> str | None:
    """A clip failed: when the model ran on the GPU, it moves to the CPU and the clip is understood again there
    (its words); None when it was on the CPU already, or the CPU fails too."""
    if getattr(transcriber, "device", gpu.CPU) != gpu.CUDA or not hasattr(transcriber, "to_cpu"):
        return None
    try:
        transcriber.to_cpu()
        return transcriber.transcribe(clip)
    except Exception:  # noqa: BLE001 - reported by its code
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="briefing_reader.talk_speech")
    parser.add_argument("--model", required=True)
    parser.add_argument("--threads", type=int, default=THREADS)
    parser.add_argument("--idle-s", type=float, default=IDLE_S)
    parser.add_argument("--hints", default="")
    parser.add_argument("--device", choices=gpu.DEVICES, default=gpu.DEVICE_AUTO)
    args = parser.parse_args(argv)
    # The pipes SpeechClient gives it, by number: under pythonw sys.stdout may be None even with a pipe attached.
    out = open(1, "w", encoding="utf-8", newline="\n", closefd=False)  # noqa: SIM115
    inp = open(0, "rb", buffering=0, closefd=False)  # noqa: SIM115
    return serve(inp, out, lambda: FasterWhisperTalk(Path(args.model), threads=args.threads, hints=args.hints,
                                                     device=args.device),
                 idle_s=max(5.0, args.idle_s))


# --------------------------------------------------------------------------
# The app's side
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Heard:
    ok: bool
    text: str = ""
    error: str = ""
    ms: int = 0                 # understanding the clip
    load_ms: int = 0            # loading the model, when this talk started the process (0 when it was warm)


class SpeechClient:
    """See the module docstring. Thread-safe: one ``understand`` at a time."""

    def __init__(self, model_dir: Path, *, threads: int = THREADS, idle_s: float = IDLE_S, hints: str = "",
                 python: str | None = None, root: Path | None = None,
                 spawn: Callable[[list[str]], Any] | None = None, ready_timeout_s: float = READY_TIMEOUT_S,
                 answer_timeout_s: float = ANSWER_TIMEOUT_S, device: str = gpu.DEVICE_AUTO) -> None:
        self.model_dir = Path(model_dir)
        self._device_setting = device if device in gpu.DEVICES else gpu.DEVICE_AUTO
        self.device = ""                       # where the process said it runs (gpu.CUDA or gpu.CPU; "" before)
        self._threads, self._idle_s, self._hints = threads, idle_s, hints
        self._python = python or sys.executable
        self._root = root or Path(__file__).resolve().parent.parent
        self._spawn = spawn or self._popen
        self._ready_timeout_s, self._answer_timeout_s = ready_timeout_s, answer_timeout_s
        self._lock = threading.Lock()
        self._proc: Any = None
        self._lines: queue.Queue = queue.Queue()
        self._last_used = 0.0                  # when the process last answered (time.monotonic)

    def _command(self) -> list[str]:
        return [self._python, "-B", "-m", "briefing_reader.talk_speech", "--model", str(self.model_dir),
                "--threads", str(self._threads), "--idle-s", str(self._idle_s), "--hints", self._hints,
                "--device", self._device_setting]

    def _popen(self, command: list[str]) -> Any:
        flags = BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW if os.name == "nt" else 0
        return subprocess.Popen(command, cwd=str(self._root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, creationflags=flags)

    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def _pump(self, proc: Any, lines: queue.Queue) -> None:
        try:
            for raw in proc.stdout:
                try:
                    lines.put(json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw))
                except ValueError:
                    continue
        except (OSError, ValueError):
            pass
        lines.put(None)                               # the process's output ended

    def _start(self) -> tuple[str, int]:
        """("" or an error code, load_ms). A process that ends, hangs or fails before it is ready while the GPU may be
        used (it can take the process down rather than fail) is started again on the CPU, which stays for this run."""
        error, load_ms = self._start_once()
        if error in (ERR_ENDED, ERR_TIMEOUT, ERR_FAILED) and self._device_setting == gpu.DEVICE_AUTO:
            self._device_setting = gpu.DEVICE_CPU
            error, load_ms = self._start_once()
        return error, load_ms

    def _start_once(self) -> tuple[str, int]:
        """("" or an error code, load_ms)."""
        try:
            proc = self._spawn(self._command())
        except OSError:
            return ERR_START, 0
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, args=(proc, lines), name="talk-speech-out", daemon=True).start()
        self._proc, self._lines = proc, lines
        try:
            ready = lines.get(timeout=self._ready_timeout_s)
        except queue.Empty:
            self._kill()
            return ERR_TIMEOUT, 0
        if not isinstance(ready, dict) or not ready.get("ready"):
            self._kill()
            error = ready.get("error") if isinstance(ready, dict) else ""
            return (error if error in (ERR_ADDON, ERR_MODEL, ERR_FAILED) else ERR_ENDED), 0
        device = ready.get("device")
        self.device = device if device in (gpu.CUDA, gpu.CPU) else gpu.CPU
        return "", int(ready.get("load_ms") or 0)

    def _send(self, pcm: bytes) -> dict | None:
        """The answer to ``pcm``, or None when the process was gone before it was sent."""
        proc = self._proc
        try:
            proc.stdin.write(struct.pack("<I", len(pcm)) + pcm)
            proc.stdin.flush()
        except (OSError, ValueError, AttributeError):
            return None
        try:
            answer = self._lines.get(timeout=self._answer_timeout_s)
        except queue.Empty:
            self._kill()
            return {"ok": False, "error": ERR_TIMEOUT}
        return answer if isinstance(answer, dict) else None    # its output ended: it went idle, or failed

    def understand(self, pcm: bytes) -> Heard:
        if not pcm:
            return Heard(True)
        if len(pcm) > MAX_CLIP_BYTES:
            return Heard(False, error=ERR_TOO_LONG)
        with self._lock:
            load_ms = 0
            if self.running() and time.monotonic() - self._last_used > self._idle_s - IDLE_MARGIN_S:
                self._kill()                           # about to go idle on its own: a fresh one, not a race
            for attempt in range(2):
                if not self.running():
                    error, load_ms = self._start()
                    if error:
                        return Heard(False, error=error)
                answer = self._send(pcm)
                if answer is None and attempt == 0:
                    self._gpu_gave_out(self._exit_code())
                    self._kill()                       # it ended just then (idle, or failed): start it again, once
                    continue
                if isinstance(answer, dict) and answer.get("error") == ERR_TIMEOUT:
                    self._gpu_gave_out(None)           # hung: a GPU's hang would come back with the next talk
                if isinstance(answer, dict) and answer.get("device") in (gpu.CUDA, gpu.CPU):
                    self.device = answer["device"]
                if answer is None:
                    return Heard(False, error=ERR_ENDED, load_ms=load_ms)
                self._last_used = time.monotonic()
                if answer.get("ok"):
                    return Heard(True, str(answer.get("text") or ""), ms=int(answer.get("ms") or 0), load_ms=load_ms)
                return Heard(False, error=str(answer.get("error") or ERR_FAILED), load_ms=load_ms)
        return Heard(False, error=ERR_ENDED)

    def _exit_code(self) -> int | None:
        """The ended process's exit code (None: it has not ended within a second)."""
        proc = self._proc
        try:
            return proc.wait(timeout=1.0) if proc is not None else 0
        except (subprocess.TimeoutExpired, OSError, AttributeError):
            return None

    def _gpu_gave_out(self, code: int | None) -> None:
        """On the GPU, a process that crashed (an exit code other than 0: 0 is going idle) or hung: the CPU from now
        on, for this run."""
        if self.device == gpu.CUDA and code != 0 and self._device_setting == gpu.DEVICE_AUTO:
            self._device_setting = gpu.DEVICE_CPU

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=CLOSE_WAIT_S)
        except (OSError, subprocess.TimeoutExpired, AttributeError):
            pass

    def close(self) -> None:
        """Ends the process: a zero-length clip asks it to, then it is killed if it has not."""
        with self._lock:
            proc = self._proc
            if proc is None:
                return
            try:
                proc.stdin.write(struct.pack("<I", 0))
                proc.stdin.flush()
                proc.stdin.close()
                proc.wait(timeout=CLOSE_WAIT_S)
                self._proc = None
            except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
                self._kill()


if __name__ == "__main__":
    sys.exit(main())
