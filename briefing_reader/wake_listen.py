""""Hey Jarvis": the listener process and the app's side of it (no Qt; owner decisions 2026-11-12).

``python -m briefing_reader.wake_listen --models FOLDER [--sensitivity balanced] [--from-wav PATH]`` (FOLDER: the
wake model's own, wake.model_dir) is started by Jarvis
(wake_ui, through :class:`WakeClient`) while "Hey Jarvis" is on, below normal priority and with no window. It opens
the microphone picked in Settings > Meetings (else the Windows default; never a virtual one, which is silent), listens
only for the wake phrase (wake.WakeDetector) and says so; after a wake it hands the audio to Jarvis's talk - the
PREROLL_S before the wake, then the audio as it comes - until Jarvis says that talk ended.

It is a process of its own because PortAudio is counted per process: a listener holding the microphone inside Jarvis
all day would keep every other device list there (talk, meetings, Settings > Meetings) from ever seeing a device
plugged in later. For the same reason it lets PortAudio go whenever its microphone closes - a hold of CLOSE_AFTER_S,
a microphone that stopped - and starts it again every REFRESH_S while listening, to follow a new Windows default.

Protocol. stdin takes one JSON object per line:
  {"listen": {"mic": NAME}}   listen, with the microphone picked in Settings > Meetings ("" = the Windows default)
  {"hold": true | false}      stop / go on listening (a talk, Jarvis speaking, a meeting, focus mode): a hold drops
                              the audio at once and closes the microphone after CLOSE_AFTER_S
  {"sensitivity": NAME}       one of wake.SENSITIVITIES
  {"handoff_end": true}       the talk that took the audio ended
  {"quit": true}              end (so does the input closing: it never outlives Jarvis)
stdout answers with one JSON object per line:
  {"ready": true, "load_ms": N}, or {"ready": false, "error": CODE} and the process ends
  {"state": STATE[, "mic": NAME, "default": BOOL]}   one of STATES (the name only for Jarvis's own screen)
  {"heard": true, "score": S, "speaking": BOOL, "preroll_bytes": N}   a wake ("speaking": still talking after it) ...
  {"audio": BASE64} ...       ... then its audio, AUDIO_LINE_MS a line, the first N bytes from before the wake ...
  {"audio_end": true}         ... until a handoff_end, or this when the listener had to stop it (the microphone
                              stopped, HANDOFF_MAX_S passed)
The audio is 16 kHz mono 16-bit, in memory only: nothing is written to disk, and the process logs nothing (its
stderr goes nowhere). ``--from-wav`` (checks only; the app never uses it) reads a WAV file as time passes instead of
the microphone.

:class:`WakeClient` is the app's side: it starts the process (never while the tests run), sends commands and hands
each line it writes to ``on_line``.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from . import talk, wake

TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"        # "1" while the tests run: the real process is never started
POLL_S = 0.05                     # the loop reads the microphone this often while it listens
IDLE_WAIT_S = 1.0                 # ... and only waits for commands before the first "listen"
CLOSE_AFTER_S = 3.0               # a hold this long closes the microphone and lets PortAudio go
RETRY_S = 5.0                     # a microphone that would not open is tried again this often
REFRESH_S = 600.0                 # PortAudio is started again this often while listening (a new default, new devices)
SILENT_S = 20.0                   # a microphone that gives only digital silence this long is "silent_mic"
PREROLL_S = 1.6                   # handed over from before the wake (all of "Hey Jarvis", for a one-breath request)
PREROLL_BYTES = int(PREROLL_S * wake.SAMPLE_RATE) * wake.BYTES_PER_SAMPLE
AUDIO_LINE_MS = 100
AUDIO_LINE_BYTES = wake.SAMPLE_RATE * AUDIO_LINE_MS // 1000 * wake.BYTES_PER_SAMPLE
HANDOFF_MAX_S = talk.MAX_TALK_S + 10.0              # a hand-off never runs longer than a talk can, and then some
SPEAKING_FRAMES = 5               # the last 150 ms (30 ms frames) ...
SPEAKING_LOUD = 2                 # ... with this many loud ones: the owner went on talking after "Hey Jarvis"
CLOSE_WAIT_S = 3.0
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000
ONE_THREAD = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}   # numpy's maths library
                                  # would otherwise set aside hundreds of MB for threads this process never uses

STATE_LISTENING = "listening"
STATE_HELD = "held"
STATE_VIRTUAL = "virtual_mic"     # the microphone is a virtual one (silent unless its app runs): not listened to
STATE_NO_MIC = "no_mic"
STATE_BUSY = "mic_busy"           # it would not open, or it stopped
STATE_SILENT = "silent_mic"       # it gives only digital silence (muted?); still listened to
STATE_NO_AUDIO = "audio_missing"  # PyAudioWPatch (or soundcard) is not installed
STATE_FAILED = "failed"           # the listener itself failed (then it ends; Jarvis starts it again)
STATES = (STATE_LISTENING, STATE_HELD, STATE_VIRTUAL, STATE_NO_MIC, STATE_BUSY, STATE_SILENT, STATE_NO_AUDIO,
          STATE_FAILED)
STATE_HANDOFF = "handoff"         # (never sent: "heard" says it; when the hand-off ends, the state is always said)


def _write(out: IO[str], payload: dict) -> bool:
    try:
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        out.flush()
        return True
    except (OSError, ValueError):            # Jarvis went away
        return False


class Listener:
    """The listener's state between commands (see the module docstring): ``command(message)`` takes one command,
    ``tick()`` reads the microphone and listens (the loop calls it every POLL_S). ``broken`` once the output went
    away. ``backend_loader`` returns (backend, what is missing) - meeting_audio.load_backend, or WavBackend's."""

    def __init__(self, detector: Any, out: IO[str], *, backend_loader: Callable[[], tuple[Any, str]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.detector = detector
        self._out = out
        self._load_backend = backend_loader
        self._clock = clock
        self.wanted = False               # a "listen" came
        self.mic_pick = ""
        self.held = False
        self._held_since = 0.0
        self.handoff = False
        self._handoff_began = 0.0
        self._backend: Any = None
        self._stream: Any = None
        self._mic: tuple[str, bool] = ("", False)      # the open microphone (name, the Windows default)
        self._opened_pick = ""                          # the pick it was opened with
        self._opened_at = 0.0
        self._retry_at = 0.0
        self._ring = bytearray()          # the last PREROLL_BYTES listened to
        self._pending = bytearray()       # read, not yet a whole chunk
        self._outgoing = bytearray()      # a hand-off's audio not yet sent
        self._state_key: tuple = ()
        self.state = ""
        self.broken = False
        self._reset_levels()

    # ---- commands ---------------------------------------------------------------------------------------------

    def command(self, message: dict) -> None:
        if "listen" in message:
            listen = message.get("listen")
            pick = str((listen.get("mic") if isinstance(listen, dict) else "") or "")
            if pick != self.mic_pick and self._stream is not None and not self.handoff:
                self._close_stream()      # the newly picked microphone, at the next tick
            self.mic_pick = pick
            self.wanted = True
            self._retry_at = 0.0
        if "sensitivity" in message:
            self.detector.set_sensitivity(str(message.get("sensitivity") or ""))
        if "hold" in message:
            if message.get("hold") is True and not self.held:
                self.held, self._held_since = True, self._clock()
                if not self.handoff:
                    self._set_state(STATE_HELD)
            elif message.get("hold") is False and self.held:
                self.held = False
                if not self.handoff:
                    self._resume()
        if message.get("handoff_end") is True:
            self._end_handoff()

    # ---- listening --------------------------------------------------------------------------------------------

    def tick(self) -> None:
        if not self.wanted or self.broken:
            return
        now = self._clock()
        if self.handoff:
            self._tick_handoff(now)
            return
        if self.held:
            if self._stream is not None:
                self._read(now)                       # dropped: nothing is listened to while held
                if now - self._held_since >= CLOSE_AFTER_S:
                    self._close_stream()
            return
        if self._stream is not None and now - self._opened_at >= REFRESH_S:
            self._close_stream()                      # a fresh look at the devices and the Windows default
            self._retry_at = 0.0
        if self._stream is None:
            if now < self._retry_at:
                return
            self._open(now)
            if self._stream is None:
                self._retry_at = now + RETRY_S
                return
        audio = self._read(now)
        if audio:
            self._listen(audio, now)
        if self.state == STATE_LISTENING and now - self._last_sound >= SILENT_S:
            self._set_state(STATE_SILENT, mic=self._mic[0], default=self._mic[1])

    def _open(self, now: float) -> None:
        from .meeting_audio import pick_device

        if self._backend is None:
            try:
                backend, _missing = self._load_backend()
            except Exception:  # noqa: BLE001 - a library that will not start is no audio
                backend = None
            if backend is None:
                self._set_state(STATE_NO_AUDIO)
                return
            self._backend = backend
        try:
            device, follows = pick_device(self._backend.inputs(), self._backend.default_input(), self.mic_pick)
        except Exception:  # noqa: BLE001 - a device library that cannot list is no microphone
            device, follows = None, False
        if device is None:
            self._release_backend()
            self._set_state(STATE_NO_MIC)
            return
        name = str(getattr(device, "name", "") or "")
        if talk.virtual_microphone(name):
            self._release_backend()
            self._set_state(STATE_VIRTUAL, mic=name, default=bool(follows))
            return
        try:
            self._stream = self._backend.open_mic(device)
        except Exception:  # noqa: BLE001 - in use, unplugged, refused by Windows' privacy setting
            self._stream = None
            self._release_backend()
            self._set_state(STATE_BUSY, mic=name, default=bool(follows))
            return
        self._opened_at = now
        self._mic, self._opened_pick = (name, bool(follows)), self.mic_pick
        self._fresh()
        self._set_state(STATE_LISTENING, mic=name, default=bool(follows))

    def _read(self, now: float) -> bytes | None:
        """What the microphone gave since the last read; None when it stopped (then it is closed and tried again)."""
        try:
            return self._stream.read_available()
        except Exception:  # noqa: BLE001 - the device went away, or delivers nothing
            name, follows = self._mic
            self._close_stream()
            self._retry_at = now + RETRY_S
            if self.handoff:
                self._send({"audio_end": True})
                self._end_handoff(state=False)
            if not self.held:                          # held, it is opened again when the hold ends
                self._set_state(STATE_BUSY, mic=name, default=follows)
            return None

    def _listen(self, audio: bytes, now: float) -> None:
        self._pending += audio
        while len(self._pending) >= wake.CHUNK_BYTES:
            chunk = bytes(self._pending[:wake.CHUNK_BYTES])
            del self._pending[:wake.CHUNK_BYTES]
            self._ring += chunk
            if len(self._ring) > PREROLL_BYTES:
                del self._ring[:len(self._ring) - PREROLL_BYTES]
            self._levels(chunk, now)
            if self.detector.process(chunk):
                self._start_handoff(now)
                return

    # ---- the hand-off ------------------------------------------------------------------------------------------

    def _start_handoff(self, now: float) -> None:
        preroll = bytes(self._ring)
        self.handoff, self._handoff_began = True, now
        self._state_key, self.state = (STATE_HANDOFF,), STATE_HANDOFF   # said by "heard"; the state after, always
        self._send({"heard": True, "score": round(float(getattr(self.detector, "score", 0.0)), 3),
                    "speaking": self._speaking(), "preroll_bytes": len(preroll)})
        self._outgoing = bytearray(preroll) + self._pending
        self._pending = bytearray()
        self._ring = bytearray()
        self._flush_audio()

    def _tick_handoff(self, now: float) -> None:
        if self._stream is not None:
            audio = self._read(now)
            if not self.handoff:
                return                                 # the microphone stopped: the hand-off ended with it
            if audio:
                self._outgoing += audio
                self._flush_audio()
        if now - self._handoff_began >= HANDOFF_MAX_S:
            self._send({"audio_end": True})
            self._end_handoff()

    def _flush_audio(self) -> None:
        while len(self._outgoing) >= AUDIO_LINE_BYTES and not self.broken:
            piece = bytes(self._outgoing[:AUDIO_LINE_BYTES])
            del self._outgoing[:AUDIO_LINE_BYTES]
            self._send({"audio": base64.b64encode(piece).decode("ascii")})

    def _end_handoff(self, *, state: bool = True) -> None:
        if not self.handoff:
            return
        self.handoff = False
        self._outgoing = bytearray()
        self._fresh()
        if self._stream is not None and self.mic_pick != self._opened_pick:
            self._close_stream()                       # another microphone was picked meanwhile: it, next
            self._retry_at = 0.0
        if self.held:
            if state:
                self._set_state(STATE_HELD)
        elif state and self._stream is not None:
            self._set_state(STATE_LISTENING, mic=self._mic[0], default=self._mic[1])

    def _resume(self) -> None:
        self._fresh()
        if self._stream is not None:
            self._set_state(STATE_LISTENING, mic=self._mic[0], default=self._mic[1])
        else:
            self._retry_at = 0.0

    def _fresh(self) -> None:
        """Listening starts again from nothing (a new microphone, after a hold or a hand-off)."""
        self.detector.reset()
        self._ring = bytearray()
        self._pending = bytearray()
        self._reset_levels()
        self._last_sound = self._clock()

    # ---- levels: a silent microphone, and whether the owner went on talking ------------------------------------

    def _reset_levels(self) -> None:
        self._level_pending = bytearray()
        self._room_db: float | None = None
        self._recent = deque(maxlen=SPEAKING_FRAMES)
        self._last_sound = 0.0

    def _levels(self, chunk: bytes, now: float) -> None:
        self._level_pending += chunk
        while len(self._level_pending) >= talk.FRAME_BYTES:
            db = talk.frame_db(bytes(self._level_pending[:talk.FRAME_BYTES]))
            del self._level_pending[:talk.FRAME_BYTES]
            if db > talk.MUTED_DB:
                self._last_sound = now
                if self.state == STATE_SILENT:
                    self._set_state(STATE_LISTENING, mic=self._mic[0], default=self._mic[1])
            if self._room_db is None:
                self._room_db = min(talk.FLOOR_START_MAX_DB, max(talk.FLOOR_MIN_DB, db))
            loud = db >= max(self._room_db + talk.SPEECH_ABOVE_DB, talk.SPEECH_MIN_DB)
            if db < self._room_db:
                self._room_db = max(talk.FLOOR_MIN_DB, db)
            elif not loud:
                self._room_db = min(talk.FLOOR_MAX_DB, self._room_db + talk.FLOOR_RISE_DB)
            self._recent.append(loud)

    def _speaking(self) -> bool:
        return sum(self._recent) >= SPEAKING_LOUD

    # ---- the device and the output --------------------------------------------------------------------------------

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - a device that went away
                pass
        self._release_backend()

    def _release_backend(self) -> None:
        backend, self._backend = self._backend, None
        close = getattr(backend, "close", None)
        if callable(close):
            try:
                close()                               # PortAudio let go: the next open sees the devices afresh
            except Exception:  # noqa: BLE001 - a library that already went
                pass

    def _set_state(self, state: str, **fields: Any) -> None:
        key = (state, fields.get("mic"), fields.get("default"))
        if key == self._state_key:
            return
        self._state_key, self.state = key, state
        self._send({"state": state, **fields})

    def _send(self, payload: dict) -> None:
        if not self.broken and not _write(self._out, payload):
            self.broken = True

    def close(self) -> None:
        self._close_stream()


_EOF = object()


def _reader(inp: IO[bytes], commands: queue.Queue) -> None:
    """Commands from ``inp`` into ``commands`` (dicts), then _EOF when it closes."""
    try:
        for raw in inp:
            try:
                message = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(message, dict):
                commands.put(message)
    except (OSError, ValueError):
        pass
    commands.put(_EOF)


def serve(inp: IO[bytes], out: IO[str], load: Callable[[], Any], *,
          backend_loader: Callable[[], tuple[Any, str]] | None = None, sensitivity: str = wake.DEFAULT_SENSITIVITY,
          clock: Callable[[], float] = time.monotonic, detector_factory: Callable[..., Any] | None = None) -> int:
    """The process's loop (see the module docstring); its exit code: 0 quit or input closed, 1 the output went away,
    2 the detector could not be made, 3 the listener failed."""
    began = clock()
    try:
        detector = (detector_factory or wake.WakeDetector)(load(), sensitivity)
    except wake.WakeError as exc:
        _write(out, {"ready": False, "error": exc.code})
        return 2
    except Exception:  # noqa: BLE001 - reported by its code, never its text
        _write(out, {"ready": False, "error": wake.ERR_FAILED})
        return 2
    if not _write(out, {"ready": True, "load_ms": int((clock() - began) * 1000)}):
        return 1
    if backend_loader is None:
        from .meeting_audio import load_backend as backend_loader
    commands: queue.Queue = queue.Queue()
    threading.Thread(target=_reader, args=(inp, commands), name="wake-listen-in", daemon=True).start()
    listener = Listener(detector, out, backend_loader=backend_loader, clock=clock)
    try:
        while not listener.broken:
            try:
                message = commands.get(timeout=POLL_S if listener.wanted else IDLE_WAIT_S)
            except queue.Empty:
                message = None
            while message is not None:
                if message is _EOF or (isinstance(message, dict) and message.get("quit") is True):
                    return 0
                listener.command(message)
                try:
                    message = commands.get_nowait()
                except queue.Empty:
                    message = None
            try:
                listener.tick()
            except Exception:  # noqa: BLE001 - never a loop of failures: say so and end; Jarvis starts it again
                listener._set_state(STATE_FAILED)
                return 3
        return 1
    finally:
        listener.close()


# --------------------------------------------------------------------------
# --from-wav (checks only): a WAV file instead of the microphone
# --------------------------------------------------------------------------

class WavBackend:
    """A 16 kHz mono 16-bit WAV file as the one microphone ("a WAV file"), given as time passes, then quiet."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic) -> None:
        with wave.open(str(path), "rb") as fh:
            if (fh.getframerate(), fh.getnchannels(), fh.getsampwidth()) != (wake.SAMPLE_RATE, 1, 2):
                raise ValueError("the WAV file must be 16 kHz mono 16-bit")
            self._pcm = fh.readframes(fh.getnframes())
        self._clock = clock

    def inputs(self) -> list[Any]:
        from .meeting_audio import DeviceInfo

        return [DeviceInfo("wav", "a WAV file", True)]

    def default_input(self) -> Any:
        return self.inputs()[0]

    def open_mic(self, _device: Any) -> Any:
        return _WavStream(self._pcm, self._clock)

    def close(self) -> None:
        pass


class _WavStream:
    def __init__(self, pcm: bytes, clock: Callable[[], float]) -> None:
        self._pcm, self._clock = pcm, clock
        self._began = clock()
        self._given = 0

    def read_available(self) -> bytes:
        due = int((self._clock() - self._began) * wake.SAMPLE_RATE) * wake.BYTES_PER_SAMPLE
        if due <= self._given:
            return b""
        piece = self._pcm[self._given:due]
        piece += bytes(due - self._given - len(piece))   # quiet after the end
        self._given = due
        return piece

    def close(self) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    for name, value in ONE_THREAD.items():
        os.environ.setdefault(name, value)               # before numpy is imported (by the detector)
    parser = argparse.ArgumentParser(prog="briefing_reader.wake_listen")
    parser.add_argument("--models", required=True)
    parser.add_argument("--sensitivity", choices=tuple(wake.SENSITIVITIES), default=wake.DEFAULT_SENSITIVITY)
    parser.add_argument("--from-wav", default="")
    args = parser.parse_args(argv)
    # The pipes WakeClient gives it, by number: under pythonw sys.stdout may be None even with a pipe attached.
    out = open(1, "w", encoding="utf-8", newline="\n", closefd=False)  # noqa: SIM115
    inp = open(0, "rb", closefd=False)  # noqa: SIM115
    backend_loader = None
    if args.from_wav:
        try:
            wav = WavBackend(Path(args.from_wav))
        except (OSError, ValueError, EOFError, wave.Error):
            _write(out, {"ready": False, "error": STATE_NO_AUDIO})
            return 2
        backend_loader = lambda: (wav, "")  # noqa: E731
    return serve(inp, out, lambda: wake.load_sessions(Path(args.models)), backend_loader=backend_loader,
                 sensitivity=args.sensitivity)


# --------------------------------------------------------------------------
# The app's side
# --------------------------------------------------------------------------

class WakeClient:
    """The app's side of the listener process (no Qt). ``start()`` starts it (False when it could not be: never
    while the tests run); each line it writes reaches ``on_line(dict)`` on a reader thread, then ``on_line(None)``
    once its output ended (it exited); ``send(dict)`` writes a command (False when it is gone); ``close()`` asks it to
    quit and makes sure it did. ``spawn`` (tests) replaces the real process."""

    def __init__(self, models: Path, on_line: Callable[[dict | None], Any], *,
                 sensitivity: str = wake.DEFAULT_SENSITIVITY, python: str | None = None, root: Path | None = None,
                 spawn: Callable[[list[str]], Any] | None = None) -> None:
        self.models = Path(models)
        self._on_line = on_line
        self._sensitivity = wake.sensitivity_of(sensitivity).name
        self._python = python or sys.executable
        self._root = root or Path(__file__).resolve().parent.parent
        self._spawn = spawn or self._popen
        self._lock = threading.Lock()
        self._proc: Any = None

    def command(self) -> list[str]:
        return [self._python, "-B", "-m", "briefing_reader.wake_listen", "--models", str(self.models),
                "--sensitivity", self._sensitivity]

    def _popen(self, command: list[str]) -> Any:
        if os.environ.get(TEST_GUARD_ENV, "") == "1":
            raise OSError("the real listener is never started while the tests run")
        environment = dict(os.environ, **ONE_THREAD)
        flags = BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW if os.name == "nt" else 0
        return subprocess.Popen(command, cwd=str(self._root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=environment, creationflags=flags)

    def start(self) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
            try:
                proc = self._spawn(self.command())
            except OSError:
                return False
            self._proc = proc
        threading.Thread(target=self._pump, args=(proc,), name="wake-listen-out", daemon=True).start()
        return True

    def _pump(self, proc: Any) -> None:
        try:
            for raw in proc.stdout:
                try:
                    message = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self._on_line(message)
        except (OSError, ValueError):
            pass
        self._on_line(None)

    def send(self, payload: dict) -> bool:
        with self._lock:
            proc = self._proc
            if proc is None:
                return False
            try:
                proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
                proc.stdin.flush()
                return True
            except (OSError, ValueError, AttributeError):
                return False

    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def exit_code(self) -> int | None:
        proc = self._proc
        return proc.poll() if proc is not None else None

    def close(self, wait_s: float = CLOSE_WAIT_S) -> None:
        """Asks it to quit (its input closes too), then ends it if it has not within ``wait_s``."""
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.write(b'{"quit": true}\n')
            proc.stdin.flush()
            proc.stdin.close()
        except (OSError, ValueError, AttributeError):
            pass
        try:
            proc.wait(timeout=wait_s)
        except (subprocess.TimeoutExpired, OSError, AttributeError):
            try:
                proc.kill()
                proc.wait(timeout=wait_s)
            except (subprocess.TimeoutExpired, OSError, AttributeError):
                pass


if __name__ == "__main__":
    sys.exit(main())
