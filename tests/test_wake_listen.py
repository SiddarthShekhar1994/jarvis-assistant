""""Hey Jarvis": the listener process (wake_listen) with made-up microphones, a made-up detector and a hand-turned
clock - the protocol, which microphone, the holds, the hand-off of the audio after a wake, a microphone that is
virtual, silent or stops - and the app's side of it (WakeClient) with a made-up process. No microphone is opened and
no process is started."""

from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from briefing_reader import meeting_audio, talk, wake, wake_listen
from briefing_reader.wake_listen import Listener

CHUNK = wake.CHUNK_BYTES
REAL = "Microphone (FIFINE K678 Microphone)"
STEAM = "Microphone (Steam Streaming Microphone)"
WEBCAM = "Microphone (LifeCam HD-5000)"


def loud(size: int) -> bytes:
    """A loud square wave (speech-like level), ``size`` bytes."""
    return (b"\x00\x30" * 8 + b"\x00\xd0" * 8) * (size // 32) + bytes(size % 32)


def quiet(size: int) -> bytes:
    """A very quiet room (not digital silence), ``size`` bytes."""
    return (b"\x03\x00" * 4 + b"\xfd\xff" * 4) * (size // 16) + bytes(size % 16)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeStream:
    def __init__(self, name: str) -> None:
        self.name = name
        self.pieces: list[bytes] = []
        self.fail = False
        self.closed = False
        self.reads = 0

    def read_available(self) -> bytes:
        self.reads += 1
        if self.fail:
            raise OSError("the device stopped")
        piece = b"".join(self.pieces)
        self.pieces = []
        return piece

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    def __init__(self, names=(REAL, WEBCAM), default=REAL, refuse=False) -> None:
        self.names, self.default, self.refuse = list(names), default, refuse
        self.streams: list[FakeStream] = []
        self.closed = False

    def inputs(self):
        return [meeting_audio.DeviceInfo(str(i), name) for i, name in enumerate(self.names)]

    def default_input(self):
        if self.default is None:
            return None
        return meeting_audio.DeviceInfo("d", self.default, True)

    def open_mic(self, device):
        if self.refuse:
            raise OSError("in use")
        stream = FakeStream(device.name)
        self.streams.append(stream)
        return stream

    def close(self) -> None:
        self.closed = True


class Loader:
    """backend_loader: a fresh FakeBackend each time (as PortAudio starting again), made by ``make``."""

    def __init__(self, make=FakeBackend) -> None:
        self.make = make
        self.backends: list[FakeBackend] = []

    def __call__(self):
        backend = self.make()
        if backend is None:
            return None, "PyAudioWPatch (or soundcard)"
        self.backends.append(backend)
        return backend, ""

    @property
    def stream(self) -> FakeStream:
        return self.backends[-1].streams[-1]


class FakeDetector:
    """Hears the wake phrase in a chunk that starts with the bytes b"WAKE"."""

    def __init__(self) -> None:
        self.score = 0.0
        self.chunks: list[bytes] = []
        self.resets = 0
        self.sensitivity = "balanced"

    def process(self, chunk: bytes) -> bool:
        self.chunks.append(chunk)
        heard = chunk.startswith(b"WAKE")
        self.score = 0.93 if heard else 0.01
        return heard

    def reset(self) -> None:
        self.resets += 1

    def set_sensitivity(self, name: str) -> None:
        self.sensitivity = name


def wake_chunk() -> bytes:
    return b"WAKE" + loud(CHUNK - 4)


class ListenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.out = io.StringIO()
        self.loader = Loader()
        self.detector = FakeDetector()
        self.listener = Listener(self.detector, self.out, backend_loader=self.loader, clock=self.clock)

    def lines(self) -> list[dict]:
        return [json.loads(line) for line in self.out.getvalue().splitlines()]

    def states(self) -> list[str]:
        return [line["state"] for line in self.lines() if "state" in line]

    def audio(self) -> bytes:
        return b"".join(base64.b64decode(line["audio"]) for line in self.lines() if "audio" in line)

    def tick(self, seconds: float = wake_listen.POLL_S, *pieces: bytes) -> None:
        self.clock.now += seconds
        if pieces and self.loader.backends and self.loader.backends[-1].streams:
            self.loader.stream.pieces.extend(pieces)
        self.listener.tick()

    def listening(self, pick: str = "") -> FakeStream:
        self.listener.command({"listen": {"mic": pick}})
        self.tick()
        return self.loader.stream

    def test_nothing_is_opened_before_listen(self) -> None:
        for _ in range(5):
            self.tick()
        self.assertEqual(self.loader.backends, [])
        self.assertEqual(self.lines(), [])

    def test_the_picked_microphone_else_the_windows_default(self) -> None:
        stream = self.listening(WEBCAM)
        self.assertEqual(stream.name, WEBCAM)
        self.assertEqual(self.lines()[-1], {"state": "listening", "mic": WEBCAM, "default": False})
        self.listener.command({"listen": {"mic": "Microphone (gone)"}})
        self.assertTrue(stream.closed)
        self.assertTrue(self.loader.backends[0].closed)          # PortAudio let go before the next look
        self.tick()
        self.assertEqual(self.loader.stream.name, REAL)
        self.assertEqual(self.lines()[-1], {"state": "listening", "mic": REAL, "default": True})

    def test_a_virtual_microphone_is_never_listened_to(self) -> None:
        self.loader.make = lambda: FakeBackend(names=(STEAM, REAL), default=STEAM)
        self.listener.command({"listen": {"mic": ""}})
        self.tick()
        self.assertEqual(self.lines(), [{"state": "virtual_mic", "mic": STEAM, "default": True}])
        self.assertEqual(self.loader.backends[0].streams, [])
        self.assertTrue(self.loader.backends[0].closed)
        self.tick(1.0)
        self.assertEqual(len(self.loader.backends), 1)            # tried again only after RETRY_S
        self.tick(wake_listen.RETRY_S)
        self.assertEqual(len(self.loader.backends), 2)
        self.assertEqual(self.states(), ["virtual_mic"])          # said once
        self.listener.command({"listen": {"mic": REAL}})          # the owner picked the real one in Settings
        self.tick()
        self.assertEqual(self.loader.stream.name, REAL)
        self.assertEqual(self.states()[-1], "listening")

    def test_no_microphone_one_that_will_not_open_and_no_audio_library(self) -> None:
        self.loader.make = lambda: FakeBackend(names=(), default=None)
        self.listener.command({"listen": {"mic": ""}})
        self.tick()
        self.assertEqual(self.states(), ["no_mic"])
        self.loader.make = lambda: FakeBackend(refuse=True)
        self.tick(wake_listen.RETRY_S)
        self.assertEqual(self.lines()[-1], {"state": "mic_busy", "mic": REAL, "default": True})
        self.loader.make = lambda: None
        self.tick(wake_listen.RETRY_S)
        self.assertEqual(self.states()[-1], "audio_missing")
        self.loader.make = FakeBackend
        self.tick(wake_listen.RETRY_S)
        self.assertEqual(self.states()[-1], "listening")

    def test_the_detector_hears_the_microphone_chunk_by_chunk(self) -> None:
        stream = self.listening()
        self.tick(0.05, quiet(1000))
        self.tick(0.05, quiet(CHUNK * 2))
        self.assertEqual(len(self.detector.chunks), (1000 + CHUNK * 2) // CHUNK)
        self.assertTrue(all(len(chunk) == CHUNK for chunk in self.detector.chunks))
        self.assertGreater(stream.reads, 0)

    def test_a_wake_hands_over_what_came_before_it_then_the_audio_as_it_comes(self) -> None:
        self.listening()
        before = quiet(CHUNK * 30)                                 # 2.4 s: more than the pre-roll
        self.tick(0.05, before)
        after = loud(CHUNK + 1000)
        self.tick(0.05, wake_chunk() + after)
        heard = [line for line in self.lines() if "heard" in line]
        self.assertEqual(len(heard), 1)
        self.assertEqual(heard[0]["score"], 0.93)
        self.assertEqual(heard[0]["preroll_bytes"], wake_listen.PREROLL_BYTES)
        handed = self.audio()
        self.assertEqual(len(handed) % wake_listen.AUDIO_LINE_BYTES, 0)
        expected = (before + wake_chunk() + after)
        expected_preroll = expected[CHUNK * 31 - wake_listen.PREROLL_BYTES:CHUNK * 31]
        self.assertEqual(handed[:wake_listen.PREROLL_BYTES], expected_preroll)
        self.assertEqual(handed[wake_listen.PREROLL_BYTES:], after[:len(handed) - wake_listen.PREROLL_BYTES])
        fed = len(self.detector.chunks)
        self.tick(0.05, loud(wake_listen.AUDIO_LINE_BYTES * 2))
        self.assertEqual(len(self.detector.chunks), fed)            # the detector rests during the hand-off
        self.assertEqual(self.audio()[wake_listen.PREROLL_BYTES:], (after + loud(wake_listen.AUDIO_LINE_BYTES * 2))[
            :len(self.audio()) - wake_listen.PREROLL_BYTES])

    def test_the_end_of_the_talk_starts_listening_afresh(self) -> None:
        self.listening()
        self.tick(0.05, wake_chunk())
        resets = self.detector.resets
        self.listener.command({"handoff_end": True})
        self.assertEqual(self.detector.resets, resets + 1)
        self.assertFalse(self.listener.handoff)
        self.assertEqual(self.lines()[-1], {"state": "listening", "mic": REAL, "default": True})
        sent = len(self.audio())
        self.tick(0.05, loud(CHUNK * 3))
        self.assertEqual(len(self.audio()), sent)                   # nothing more is handed over
        self.tick(0.05, wake_chunk())
        self.assertEqual(sum(1 for line in self.lines() if "heard" in line), 2)

    def test_a_hold_drops_the_audio_and_closes_the_microphone_after_3_s(self) -> None:
        stream = self.listening()
        self.listener.command({"hold": True})
        self.assertEqual(self.states()[-1], "held")
        self.tick(1.0, wake_chunk())
        self.assertEqual(self.detector.chunks, [])                  # never listened to while held
        self.assertFalse(stream.closed)
        self.tick(2.1)
        self.assertTrue(stream.closed)
        self.assertTrue(self.loader.backends[0].closed)
        self.listener.command({"hold": False})
        self.tick()
        self.assertEqual(len(self.loader.backends), 2)              # PortAudio started afresh
        self.assertEqual(self.states()[-1], "listening")

    def test_a_short_hold_keeps_the_microphone(self) -> None:
        stream = self.listening()
        self.listener.command({"hold": True})
        self.tick(1.0)
        resets = self.detector.resets
        self.listener.command({"hold": False})
        self.assertEqual(self.detector.resets, resets + 1)
        self.assertFalse(stream.closed)
        self.assertEqual(self.states()[-2:], ["held", "listening"])

    def test_a_hold_during_the_hand_off_waits_for_its_end(self) -> None:
        self.listening()
        self.tick(0.05, wake_chunk())
        self.listener.command({"hold": True})                       # the talk the wake started
        self.tick(0.05, loud(wake_listen.AUDIO_LINE_BYTES))
        self.assertGreater(len(self.audio()), 0)
        self.assertNotIn("held", self.states())
        self.listener.command({"handoff_end": True})
        self.assertEqual(self.states()[-1], "held")

    def test_the_microphone_stopping_ends_the_hand_off(self) -> None:
        self.listening()
        self.tick(0.05, wake_chunk())
        self.loader.stream.fail = True
        self.tick()
        self.assertIn({"audio_end": True}, self.lines())
        self.assertFalse(self.listener.handoff)
        self.assertEqual(self.states()[-1], "mic_busy")
        self.tick(wake_listen.RETRY_S)
        self.assertEqual(self.states()[-1], "listening")

    def test_a_hand_off_never_outlasts_its_cap(self) -> None:
        self.listening()
        self.tick(0.05, wake_chunk())
        self.tick(wake_listen.HANDOFF_MAX_S)
        self.assertEqual(self.lines()[-2:], [{"audio_end": True},
                                             {"state": "listening", "mic": REAL, "default": True}])
        self.assertFalse(self.listener.handoff)

    def test_a_silent_microphone_is_said_and_unsaid(self) -> None:
        self.listening()
        for _ in range(int(wake_listen.SILENT_S) + 1):
            self.tick(1.0, bytes(wake.SAMPLE_RATE * 2))
        self.assertEqual(self.states()[-1], "silent_mic")
        self.tick(1.0, quiet(CHUNK * 2))
        self.assertEqual(self.states()[-1], "listening")

    def test_whether_the_owner_went_on_speaking_after_the_wake(self) -> None:
        self.listening()
        self.tick(0.05, quiet(CHUNK * 10) + loud(CHUNK * 3) + wake_chunk())
        self.assertTrue([line for line in self.lines() if "heard" in line][0]["speaking"])
        self.listener.command({"handoff_end": True})
        self.tick(0.05, quiet(CHUNK * 10) + loud(CHUNK * 3) + quiet(CHUNK * 2) + b"WAKE" + quiet(CHUNK - 4))
        self.assertFalse([line for line in self.lines() if "heard" in line][1]["speaking"])

    def test_portaudio_is_started_again_every_ten_minutes(self) -> None:
        first = self.listening()
        self.tick(wake_listen.REFRESH_S)
        self.assertTrue(first.closed)
        self.assertTrue(self.loader.backends[0].closed)
        self.assertEqual(len(self.loader.backends), 2)
        self.assertFalse(self.loader.stream.closed)

    def test_the_sensitivity_reaches_the_detector(self) -> None:
        self.listener.command({"sensitivity": "fewer"})
        self.assertEqual(self.detector.sensitivity, "fewer")

    def test_states_are_said_once(self) -> None:
        self.listening()
        for _ in range(10):
            self.tick(0.05, quiet(CHUNK))
        self.assertEqual(self.states(), ["listening"])

    def test_it_logs_nothing(self) -> None:
        with self.assertNoLogs("briefing_reader", level="DEBUG"):
            self.listening(WEBCAM)
            self.tick(0.05, wake_chunk())
            self.listener.command({"handoff_end": True})
            self.listener.command({"hold": True})
            self.tick(4.0)

    def test_close_lets_the_microphone_go(self) -> None:
        stream = self.listening()
        self.listener.close()
        self.assertTrue(stream.closed)
        self.assertTrue(self.loader.backends[0].closed)


class ServeTests(unittest.TestCase):
    def serve(self, commands: list[dict], **kwargs) -> tuple[int, list[dict]]:
        inp = io.BytesIO(b"".join((json.dumps(command) + "\n").encode() for command in commands))
        out = io.StringIO()
        kwargs.setdefault("backend_loader", Loader())
        kwargs.setdefault("detector_factory", lambda _sessions, _sensitivity: FakeDetector())
        code = wake_listen.serve(inp, out, lambda: object(), **kwargs)
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_ready_then_it_ends_when_its_input_closes(self) -> None:
        code, lines = self.serve([{"listen": {"mic": ""}}])
        self.assertEqual(code, 0)
        self.assertEqual(lines[0]["ready"], True)
        self.assertIsInstance(lines[0]["load_ms"], int)

    def test_quit_ends_it(self) -> None:
        code, lines = self.serve([{"quit": True}, {"listen": {"mic": ""}}])
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 1)

    def test_a_detector_that_cannot_be_made_is_said_by_its_code(self) -> None:
        def missing(_sessions, _sensitivity):
            raise wake.WakeError(wake.ERR_MODEL)

        self.assertEqual(self.serve([], detector_factory=missing), (2, [{"ready": False, "error": "model_missing"}]))

        def broken(_sessions, _sensitivity):
            raise RuntimeError("C:\\Users\\someone\\details")

        self.assertEqual(self.serve([], detector_factory=broken), (2, [{"ready": False, "error": "failed"}]))

    def test_a_listener_that_fails_says_so_and_ends(self) -> None:
        def boom():
            raise ZeroDivisionError

        class Exploding(FakeBackend):
            def open_mic(self, device):
                stream = super().open_mic(device)
                stream.read_available = boom
                return stream

        inp = io.BytesIO(b'{"listen": {"mic": ""}}\n')
        out = io.StringIO()
        with mock.patch.object(wake_listen, "_reader", lambda _inp, commands: commands.put({"listen": {"mic": ""}})):
            with mock.patch.object(Listener, "_read", lambda self, now: boom()):
                code = wake_listen.serve(inp, out, lambda: object(), backend_loader=Loader(Exploding),
                                         detector_factory=lambda _s, _n: FakeDetector())
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out.getvalue().splitlines()[-1]), {"state": "failed"})

    def test_an_output_that_went_away_ends_it(self) -> None:
        class Gone(io.StringIO):
            def write(self, _text):
                raise OSError("the pipe closed")

        code = wake_listen.serve(io.BytesIO(b""), Gone(), lambda: object(), backend_loader=Loader(),
                                 detector_factory=lambda _s, _n: FakeDetector())
        self.assertEqual(code, 1)


class FakeProc:
    def __init__(self, lines: list[dict]) -> None:
        self.stdout = io.BytesIO(b"".join((json.dumps(line) + "\n").encode() for line in lines))
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: setattr(self, "stdin_closed", True)
        self.stdin_closed = False
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True


class ClientTests(unittest.TestCase):
    def test_it_starts_the_listener_and_hands_on_every_line(self) -> None:
        got: list = []
        made: list = []
        proc = FakeProc([{"ready": True, "load_ms": 5}, {"state": "listening", "mic": REAL, "default": True}])

        def spawn(command):
            made.append(command)
            return proc

        client = wake_listen.WakeClient(Path("C:/models/openwakeword-hey-jarvis-v0.1"), got.append,
                                        sensitivity="fewer", python="python.exe", spawn=spawn)
        self.assertTrue(client.start())
        for _ in range(200):
            if got and got[-1] is None:
                break
            import time
            time.sleep(0.01)
        self.assertEqual(made[0][:5], ["python.exe", "-B", "-m", "briefing_reader.wake_listen", "--models"])
        self.assertEqual(made[0][-2:], ["--sensitivity", "fewer"])
        self.assertEqual(got, [{"ready": True, "load_ms": 5}, {"state": "listening", "mic": REAL, "default": True},
                               None])
        self.assertTrue(client.send({"hold": True}))
        self.assertIn(b'{"hold": true}\n', proc.stdin.getvalue())
        client.close()
        self.assertIn(b'{"quit": true}\n', proc.stdin.getvalue())
        self.assertTrue(proc.stdin_closed)
        self.assertFalse(client.send({"hold": False}))

    def test_the_real_listener_is_never_started_while_the_tests_run(self) -> None:
        with mock.patch.dict(os.environ, {wake_listen.TEST_GUARD_ENV: "1"}):
            with mock.patch("subprocess.Popen") as popen:
                client = wake_listen.WakeClient(Path("models"), lambda _line: None)
                self.assertFalse(client.start())
        popen.assert_not_called()

    def test_the_process_gets_one_maths_thread_below_normal_priority_and_no_window(self) -> None:
        client = wake_listen.WakeClient(Path("models"), lambda _line: None, python="python.exe")
        with mock.patch.dict(os.environ, {wake_listen.TEST_GUARD_ENV: "0"}):
            with mock.patch("subprocess.Popen") as popen:
                client._popen(client.command())
        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["env"]["OPENBLAS_NUM_THREADS"], "1")
        self.assertEqual(kwargs["stderr"], wake_listen.subprocess.DEVNULL)
        if os.name == "nt":
            self.assertEqual(kwargs["creationflags"],
                             wake_listen.BELOW_NORMAL_PRIORITY_CLASS | wake_listen.CREATE_NO_WINDOW)


class WavTests(unittest.TestCase):
    def test_a_wav_file_is_given_as_time_passes_then_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.wav"
            with wave.open(str(path), "wb") as fh:
                fh.setnchannels(1)
                fh.setsampwidth(2)
                fh.setframerate(16000)
                fh.writeframes(loud(16000))                          # half a second
            clock = Clock()
            backend = wake_listen.WavBackend(path, clock)
            self.assertEqual([device.name for device in backend.inputs()], ["a WAV file"])
            stream = backend.open_mic(backend.default_input())
            self.assertEqual(stream.read_available(), b"")
            clock.now += 0.25
            self.assertEqual(stream.read_available(), loud(16000)[:8000])
            clock.now += 0.5
            rest = stream.read_available()
            self.assertEqual(rest, loud(16000)[8000:] + bytes(8000))

    def test_only_16_khz_mono_16_bit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.wav"
            with wave.open(str(path), "wb") as fh:
                fh.setnchannels(2)
                fh.setsampwidth(2)
                fh.setframerate(44100)
                fh.writeframes(bytes(400))
            with self.assertRaises(ValueError):
                wake_listen.WavBackend(path)


if __name__ == "__main__":
    unittest.main()
