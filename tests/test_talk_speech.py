"""Talking to Jarvis: the warm speech process (talk_speech.serve), the app's side of it (SpeechClient) and one
talk's microphone (talk.TalkSession). The speech model and the microphone are fakes: no model is loaded, no device
is opened and no process is started (the client's process is serve() on a thread, over real pipes)."""

from __future__ import annotations

import io
import json
import os
import struct
import tempfile
import threading
import time
import unittest
from array import array
from pathlib import Path

from briefing_reader import talk, talk_speech
from briefing_reader.talk import TalkError, TalkSession
from briefing_reader.talk_speech import SpeechClient
from tests.test_talk import ROOM, VOICE, noise, tone


def clip(pcm: bytes) -> bytes:
    return struct.pack("<I", len(pcm)) + pcm


class EchoTranscriber:
    """Says how many samples it was given; "boom" on a clip of exactly 2 samples."""

    def __init__(self) -> None:
        self.clips: list[int] = []

    def transcribe(self, pcm: bytes) -> str:
        self.clips.append(len(pcm) // 2)
        if len(pcm) == 4:
            raise RuntimeError("boom")
        return f"heard {len(pcm) // 2} samples"


def serve_lines(stdin: bytes, load=None, **kwargs) -> tuple[int, list[dict]]:
    out = io.StringIO()
    code = talk_speech.serve(io.BytesIO(stdin), out, load or EchoTranscriber, **kwargs)
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


class ServeTests(unittest.TestCase):
    def test_ready_then_one_answer_per_clip_then_quit(self) -> None:
        code, lines = serve_lines(clip(b"\x00\x00" * 10) + clip(b"\x00\x00" * 3) + struct.pack("<I", 0))
        self.assertEqual(code, 0)
        self.assertTrue(lines[0]["ready"])
        self.assertEqual([(line["ok"], line["text"]) for line in lines[1:]],
                         [(True, "heard 10 samples"), (True, "heard 3 samples")])

    def test_the_end_of_its_input_ends_it(self) -> None:
        code, lines = serve_lines(clip(b"\x01\x00" * 4))
        self.assertEqual((code, len(lines)), (0, 2))

    def test_a_clip_that_fails_does_not_end_it(self) -> None:
        code, lines = serve_lines(clip(b"\x01\x00" * 2) + clip(b"\x01\x00" * 5))
        self.assertEqual(code, 0)
        self.assertEqual([line.get("error", line.get("text")) for line in lines[1:]],
                         [talk_speech.ERR_FAILED, "heard 5 samples"])

    def test_a_clip_too_long_is_refused_and_ends_it(self) -> None:
        code, lines = serve_lines(struct.pack("<I", talk_speech.MAX_CLIP_BYTES + 2))
        self.assertEqual(code, 1)
        self.assertEqual(lines[1], {"ok": False, "error": talk_speech.ERR_TOO_LONG})

    def test_each_failure_to_load_has_its_code(self) -> None:
        def missing_addon():
            raise ImportError("faster_whisper")

        def missing_model():
            raise talk_speech.ModelMissing("folder")

        def broken():
            raise RuntimeError("a long message that holds a folder path C:\\Users\\someone")

        for load, error in ((missing_addon, talk_speech.ERR_ADDON), (missing_model, talk_speech.ERR_MODEL),
                            (broken, talk_speech.ERR_FAILED)):
            with self.subTest(error=error):
                code, lines = serve_lines(b"", load)
                self.assertEqual((code, lines), (2, [{"ready": False, "error": error}]))

    def test_idle_ends_it(self) -> None:
        read_end, write_end = os.pipe()
        with os.fdopen(read_end, "rb") as inp, os.fdopen(write_end, "wb") as _keep_open:
            out = io.StringIO()
            began = time.monotonic()
            code = talk_speech.serve(inp, out, EchoTranscriber, idle_s=0.2)
        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - began, 5.0)

    def test_it_writes_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            here = os.getcwd()
            os.chdir(folder)
            try:
                serve_lines(clip(tone(500, VOICE)) + struct.pack("<I", 0))
            finally:
                os.chdir(here)
            self.assertEqual(os.listdir(folder), [])

    def test_the_real_command_line(self) -> None:
        client = SpeechClient(Path("C:/models/faster-whisper-small.en"), hints="Jarvis, Spotify.", python="py.exe")
        command = client._command()
        self.assertEqual(command[:4], ["py.exe", "-B", "-m", "briefing_reader.talk_speech"])
        self.assertEqual(command[command.index("--hints") + 1], "Jarvis, Spotify.")


class FakeProc:
    """serve() on a thread, over real pipes, standing for the speech process. When serve() returns, only its output
    closes (a real process would exit): on Windows, closing the input while its reader thread waits on it blocks,
    so the input closes when this side closes its end (kill, close) and the reader has seen the end."""

    def __init__(self, load, idle_s: float = 60.0) -> None:
        child_in_r, in_w = os.pipe()
        out_r, child_out_w = os.pipe()
        self.stdin = os.fdopen(in_w, "wb", buffering=0)
        self.stdout = os.fdopen(out_r, "rb")
        self._child_in = os.fdopen(child_in_r, "rb", buffering=0)
        child_out = os.fdopen(child_out_w, "w", encoding="utf-8", newline="\n")
        self.code: int | None = None

        def run() -> None:
            try:
                self.code = talk_speech.serve(self._child_in, child_out, load, idle_s=idle_s)
            finally:
                child_out.close()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def poll(self):
        return None if self.thread.is_alive() else (self.code if self.code is not None else 0)

    def kill(self) -> None:
        try:
            self.stdin.close()
        except OSError:
            pass

    def wait(self, timeout=None):
        self.thread.join(timeout)
        return self.poll()

    def tidy(self) -> None:
        """Closes what is left once the reader has had its end (the test's cleanup)."""
        self.kill()
        time.sleep(0.05)
        for stream in (self.stdout, self._child_in):
            try:
                stream.close()
            except OSError:
                pass


class SlowTranscriber(EchoTranscriber):
    def transcribe(self, pcm: bytes) -> str:
        time.sleep(0.6)
        return "late"


class SpeechClientTests(unittest.TestCase):
    def client(self, load=EchoTranscriber, idle_s: float = 60.0, **kwargs) -> tuple[SpeechClient, list[FakeProc]]:
        procs: list[FakeProc] = []

        def spawn(_command):
            proc = FakeProc(load, idle_s=idle_s)
            procs.append(proc)
            return proc

        client = SpeechClient(Path("C:/models/x"), spawn=spawn, **kwargs)
        self.addCleanup(lambda: [proc.tidy() for proc in procs])
        self.addCleanup(client.close)
        return client, procs

    def test_it_starts_the_process_on_the_first_talk_and_keeps_it(self) -> None:
        client, procs = self.client()
        first = client.understand(b"\x01\x00" * 8)
        second = client.understand(b"\x01\x00" * 3)
        self.assertEqual((first.ok, first.text, second.text), (True, "heard 8 samples", "heard 3 samples"))
        self.assertEqual(len(procs), 1)
        self.assertTrue(client.running())

    def test_an_idle_process_is_started_again(self) -> None:
        client, procs = self.client(idle_s=0.2)
        self.assertTrue(client.understand(b"\x01\x00" * 2 * 3).ok)
        procs[0].wait(5)
        self.assertFalse(client.running())
        heard = client.understand(b"\x01\x00" * 6)
        self.assertEqual((heard.ok, heard.text, len(procs)), (True, "heard 6 samples", 2))

    def test_a_process_that_cannot_load_says_why_and_is_not_retried(self) -> None:
        def missing():
            raise ImportError("faster_whisper")

        client, procs = self.client(load=missing)
        heard = client.understand(b"\x01\x00" * 4)
        self.assertEqual((heard.ok, heard.error, len(procs)), (False, talk_speech.ERR_ADDON, 1))

    def test_no_answer_in_time_is_a_timeout_and_the_process_goes(self) -> None:
        client, _procs = self.client(load=SlowTranscriber, answer_timeout_s=0.1)
        heard = client.understand(b"\x01\x00" * 4)
        self.assertEqual((heard.ok, heard.error), (False, talk_speech.ERR_TIMEOUT))
        self.assertFalse(client.running())

    def test_nothing_to_understand_and_too_much_start_nothing(self) -> None:
        client, procs = self.client()
        self.assertEqual(client.understand(b""), talk_speech.Heard(True))
        self.assertEqual(client.understand(b"\x00" * (talk_speech.MAX_CLIP_BYTES + 2)).error, talk_speech.ERR_TOO_LONG)
        self.assertEqual(procs, [])

    def test_close_ends_the_process(self) -> None:
        client, procs = self.client()
        client.understand(b"\x01\x00" * 2 * 2)
        client.close()
        procs[0].wait(5)
        self.assertFalse(procs[0].thread.is_alive())
        self.assertFalse(client.running())


class FakeStream:
    """A device stream: 48 kHz stereo int16 given out in pieces, like meeting_audio's _QueueStream."""

    rate, channels, fmt = 48_000, 2, "int16"

    def __init__(self, mono16k: bytes, piece_ms: int = 100, fail_after: int | None = None) -> None:
        samples = array("h")
        samples.frombytes(mono16k)
        stereo48 = array("h")
        for sample in samples:                          # each 16 kHz sample three times, on both channels
            stereo48.extend((sample, sample) * 3)
        self._data = stereo48.tobytes()
        self._piece = 48_000 * 2 * 2 * piece_ms // 1000
        self._reads = 0
        self._fail_after = fail_after
        self.closed = False

    def read_available(self) -> bytes:
        if self.closed:
            raise OSError("closed")
        self._reads += 1
        if self._fail_after is not None and self._reads > self._fail_after:
            raise OSError("the device stopped")
        piece, self._data = self._data[:self._piece], self._data[self._piece:]
        return piece

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    def __init__(self, stream: FakeStream | None = None, devices=None, refuse: bool = False) -> None:
        from briefing_reader.meeting_audio import DeviceInfo

        self.devices = devices if devices is not None else [DeviceInfo("1", "Desk mic", True), DeviceInfo("2", "Headset")]
        self.stream = stream
        self.refuse = refuse
        self.opened = []

    def inputs(self):
        return list(self.devices)

    def default_input(self):
        return next((device for device in self.devices if device.is_default), None)

    def open_mic(self, device):
        if self.refuse:
            raise OSError("in use")
        self.opened.append(device.name)
        return self.stream


def talk_through(session: TalkSession, polls: int = 2000) -> str:
    for _ in range(polls):
        reason = session.poll()
        if reason:
            return reason
    return ""


class TalkSessionTests(unittest.TestCase):
    def test_a_talk_ends_by_quiet_closes_the_mic_and_keeps_the_speech(self) -> None:
        stream = FakeStream(noise(300, ROOM) + tone(1200, VOICE) + noise(2000, ROOM))
        session = TalkSession.start(FakeBackend(stream))
        self.assertEqual(talk_through(session), talk.END_QUIET)
        self.assertTrue(stream.closed and session.closed)
        audio = session.clip()
        self.assertGreater(len(audio), 1200 * 32)                 # the second of speech is in it, at 16 kHz
        session.forget()
        self.assertEqual(session.clip(), b"")

    def test_the_picked_microphone_is_used_while_it_is_there(self) -> None:
        backend = FakeBackend(FakeStream(noise(100, ROOM)))
        TalkSession.start(backend, "Headset")
        TalkSession.start(backend, "Gone mic")
        self.assertEqual(backend.opened, ["Headset", "Desk mic"])

    def test_a_cancel_closes_the_mic_and_keeps_nothing(self) -> None:
        stream = FakeStream(noise(300, ROOM) + tone(800, VOICE) + noise(3000, ROOM))
        session = TalkSession.start(FakeBackend(stream))
        for _ in range(5):
            session.poll()
        self.assertEqual(session.end(talk.END_CANCEL), talk.END_CANCEL)
        self.assertTrue(stream.closed)
        self.assertEqual(session.clip(), b"")

    def test_a_second_tap_keeps_even_soft_speech(self) -> None:
        stream = FakeStream(noise(2000, -58.0))
        session = TalkSession.start(FakeBackend(stream))
        for _ in range(10):
            session.poll()
        session.end(talk.END_OWNER)
        self.assertTrue(stream.closed)
        self.assertGreater(len(session.clip()), 0)

    def test_a_stopped_device_ends_the_talk_with_nothing_kept(self) -> None:
        stream = FakeStream(noise(300, ROOM) + tone(3000, VOICE), fail_after=4)
        session = TalkSession.start(FakeBackend(stream))
        self.assertEqual(talk_through(session), talk.END_DEVICE)
        self.assertTrue(session.closed)
        self.assertEqual(session.clip(), b"")

    def test_a_muted_microphone_gives_nothing_to_understand(self) -> None:
        stream = FakeStream(bytes(16_000 * 2 * 9))
        session = TalkSession.start(FakeBackend(stream))
        self.assertEqual(talk_through(session), talk.END_NO_SPEECH)
        self.assertTrue(session.muted)
        self.assertEqual(session.clip(), b"")

    def test_the_clock_ends_a_talk_whose_microphone_delivers_nothing(self) -> None:
        now = [0.0]
        stream = FakeStream(b"")
        session = TalkSession.start(FakeBackend(stream), clock=lambda: now[0])
        self.assertEqual(session.poll(), "")
        now[0] = talk.BACKSTOP_S
        self.assertEqual(session.poll(), talk.END_CAP)
        self.assertTrue(stream.closed)

    def test_what_cannot_start_says_why(self) -> None:
        cases = ((None, talk.ERR_NO_AUDIO_ADDON), (FakeBackend(FakeStream(b""), devices=[]), talk.ERR_NO_MICROPHONE),
                 (FakeBackend(FakeStream(b""), refuse=True), talk.ERR_MIC_BUSY))
        for backend, code in cases:
            with self.subTest(code=code), self.assertRaises(TalkError) as caught:
                TalkSession.start(backend)
            self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
