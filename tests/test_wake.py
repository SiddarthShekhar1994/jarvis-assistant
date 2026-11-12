""""Hey Jarvis", the parts with no Qt and no device: the detector (wake.WakeDetector) on made-up models, the presets,
the wake words taken off what was heard, and the model files: pinned sizes and SHA-256, the download (only through a
fake web session) and the check before loading. No model file, microphone or network is used."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from briefing_reader import meeting_speech, wake

HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
if HAVE_NUMPY:
    import numpy as np


class _Input:
    def __init__(self, name: str, shape: list) -> None:
        self.name, self.shape = name, shape


class FakeMel:
    """The melspectrogram model: one frame per 160 samples after the first 400, every bin the chunk's mean level."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get_inputs(self) -> list[_Input]:
        return [_Input("input", ["batch_size", "samples"])]

    def run(self, _outputs, feeds):
        x = feeds["input"]
        self.calls.append(tuple(x.shape))
        frames = max(1, (x.shape[1] - 400) // 160 + 1)
        return [np.full((1, 1, frames, 32), float(np.abs(x).mean()) / 1000.0, dtype=np.float32)]


class FakeEmbedding:
    """The embedding model: each window's mean, 96 times."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get_inputs(self) -> list[_Input]:
        return [_Input("input_1", ["n", 76, 32, 1])]

    def run(self, _outputs, feeds):
        x = feeds["input_1"]
        self.calls.append(tuple(x.shape))
        return [np.repeat(x.mean(axis=(1, 2, 3)).reshape(-1, 1, 1, 1), 96, axis=3).astype(np.float32)]


class ScriptedWake:
    """The wake model: its scores come from a script, one per chunk (0 once it runs out)."""

    def __init__(self, scores=(), frames=16) -> None:
        self.scores = list(scores)
        self.calls: list[tuple] = []
        self.frames = frames

    def get_inputs(self) -> list[_Input]:
        return [_Input("x.1", [1, self.frames, 96])]

    def run(self, _outputs, feeds):
        x = feeds["x.1"]
        self.calls.append(tuple(x.shape))
        return [np.array([[self.scores.pop(0) if self.scores else 0.0]], dtype=np.float32)]


CHUNK = bytes(wake.CHUNK_BYTES)
WARM = [0.0] * wake.WARMUP_CHUNKS


def detector(scores=(), sensitivity=wake.DEFAULT_SENSITIVITY, frames=16):
    mel, embedding, model = FakeMel(), FakeEmbedding(), ScriptedWake(scores, frames)
    return wake.WakeDetector(wake.WakeSessions(mel, embedding, model), sensitivity), mel, embedding, model


def fires(det, count: int) -> list[bool]:
    return [det.process(CHUNK) for _ in range(count)]


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class DetectorTests(unittest.TestCase):
    def test_the_first_scores_after_a_reset_are_zero(self) -> None:
        det, *_ = detector([0.9] * 20)
        self.assertEqual(fires(det, wake.WARMUP_CHUNKS), [False] * wake.WARMUP_CHUNKS)
        self.assertEqual(det.score, 0.0)
        self.assertEqual(fires(det, 3), [False, False, True])     # Balanced: three in a row at 0.5 or more

    def test_patience_counts_scores_in_a_row(self) -> None:
        det, *_ = detector(WARM + [0.6, 0.6, 0.4, 0.6, 0.6, 0.6])
        self.assertEqual(fires(det, wake.WARMUP_CHUNKS + 6)[wake.WARMUP_CHUNKS:], [False] * 5 + [True])

    def test_each_preset_has_its_threshold_and_patience(self) -> None:
        cases = {"fewer": (0.7, 4), "balanced": (0.5, 3), "more": (0.4, 2)}
        for name, (threshold, patience) in cases.items():
            with self.subTest(name):
                self.assertEqual((wake.SENSITIVITIES[name].threshold, wake.SENSITIVITIES[name].patience),
                                 (threshold, patience))
                det, *_ = detector(WARM + [threshold - 0.01] * 10, name)
                self.assertFalse(any(fires(det, wake.WARMUP_CHUNKS + 10)))
                det, *_ = detector(WARM + [threshold + 0.005] * patience, name)    # (a model's float32 score)
                self.assertEqual(fires(det, wake.WARMUP_CHUNKS + patience), [False] * (wake.WARMUP_CHUNKS + patience - 1)
                                 + [True])

    def test_it_says_so_once_until_it_is_reset(self) -> None:
        det, *_ = detector(WARM + [0.9] * 6 + WARM + [0.9] * 3)
        self.assertEqual(fires(det, wake.WARMUP_CHUNKS + 6).count(True), 1)
        det.reset()
        self.assertEqual(fires(det, wake.WARMUP_CHUNKS + 3)[-1], True)

    def test_a_new_preset_counts_at_once(self) -> None:
        det, *_ = detector(WARM + [0.45, 0.45])
        det.set_sensitivity("more")
        self.assertEqual(fires(det, wake.WARMUP_CHUNKS + 2)[-1], True)

    def test_an_unknown_preset_is_balanced(self) -> None:
        self.assertEqual(wake.sensitivity_of("loud").name, "balanced")
        self.assertEqual(wake.sensitivity_of(None).name, "balanced")
        det, *_ = detector(sensitivity="nonsense")
        self.assertEqual(det.sensitivity.name, "balanced")

    def test_feed_takes_any_size_of_piece(self) -> None:
        det, _mel, _embedding, model = detector()
        for size in (1000, 2000, 3000, 7):
            self.assertFalse(det.feed(bytes(size)))
        self.assertEqual(len(model.calls), (1000 + 2000 + 3000 + 7) // wake.CHUNK_BYTES)

    def test_feed_stops_at_a_wake_and_drops_the_rest(self) -> None:
        det, _mel, _embedding, model = detector(WARM + [0.9] * 3 + [0.9] * 10)
        self.assertTrue(det.feed(bytes(wake.CHUNK_BYTES * 12 + 100)))
        self.assertEqual(len(model.calls), wake.WARMUP_CHUNKS + 3)
        self.assertFalse(det.feed(b""))                         # nothing of that piece waits

    def test_the_melspectrogram_also_sees_the_30_ms_before_each_chunk(self) -> None:
        det, mel, *_ = detector()
        noise_call = mel.calls[0]
        self.assertEqual(noise_call, (1, wake.SAMPLE_RATE * wake.NOISE_SECONDS))
        fires(det, 3)
        self.assertEqual(mel.calls[1:], [(1, 1280), (1, 1760), (1, 1760)])

    def test_the_models_read_the_last_76_mel_frames_and_the_last_16_embeddings(self) -> None:
        det, _mel, embedding, model = detector()
        fires(det, 2)
        self.assertEqual(embedding.calls[-1], (1, 76, 32, 1))
        self.assertEqual(model.calls, [(1, 16, 96), (1, 16, 96)])

    def test_the_wake_model_s_own_frame_count_is_used(self) -> None:
        det, _mel, _embedding, model = detector(frames=8)
        fires(det, 1)
        self.assertEqual(model.calls, [(1, 8, 96)])
        mel, embedding = FakeMel(), FakeEmbedding()
        symbolic = ScriptedWake()
        symbolic.get_inputs = lambda: [_Input("x.1", ["batch", "n", 96])]
        self.assertEqual(wake.WakeDetector(wake.WakeSessions(mel, embedding, symbolic)).frames, wake.WAKE_FRAMES)

    def test_a_reset_starts_from_the_same_quiet_noise_without_making_it_again(self) -> None:
        det, mel, *_ = detector()
        first = det._features.copy()
        fires(det, 4)
        det.reset()
        self.assertTrue(np.array_equal(det._features, first))
        self.assertEqual(sum(1 for call in mel.calls if call[1] == wake.SAMPLE_RATE * wake.NOISE_SECONDS), 1)
        self.assertEqual(det.score, 0.0)

    def test_scores_are_kept_between_0_and_1(self) -> None:
        det, *_ = detector(WARM + [1.7, -0.3])
        fires(det, wake.WARMUP_CHUNKS + 1)
        self.assertEqual(det.score, 1.0)
        fires(det, 1)
        self.assertEqual(det.score, 0.0)


class StripTests(unittest.TestCase):
    def test_the_wake_words_come_off_the_front(self) -> None:
        cases = {
            "Hey Jarvis, open Spotify.": "Open Spotify.",
            "hey jarvis open spotify": "Open spotify",
            "Jarvis, what's the time?": "What's the time?",
            "Okay Jarvis. Remind me at 5.": "Remind me at 5.",
            "Hey, Jarvis - pause the music": "Pause the music",
            "Hi Jervis, lights off": "Lights off",
            "OK Jarvis's turn the volume down": "Turn the volume down",
        }
        for heard, expected in cases.items():
            with self.subTest(heard):
                self.assertEqual(wake.strip_wake_words(heard), expected)

    def test_only_the_wake_words_is_nothing(self) -> None:
        for heard in ("Hey Jarvis.", "Jarvis!", "hey, jarvis", "  Jarvis  ", ""):
            with self.subTest(heard):
                self.assertEqual(wake.strip_wake_words(heard), "")

    def test_anything_else_is_left_alone(self) -> None:
        for heard in ("Open Spotify.", "Tell Jarvis a joke", "Jarvisville is a town", "Hey there, open Notion"):
            with self.subTest(heard):
                self.assertEqual(wake.strip_wake_words(heard), heard)


# --------------------------------------------------------------------------
# The model files
# --------------------------------------------------------------------------

def _file(name: str, body: bytes) -> wake.ModelFile:
    return wake.ModelFile(name, len(body), hashlib.sha256(body).hexdigest())


BODIES = {"melspectrogram.onnx": b"mel" * 700, "embedding_model.onnx": b"emb" * 900, "hey_jarvis_v0.1.onnx": b"hj" * 800}
TABLE = tuple(_file(name, body) for name, body in BODIES.items())


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status_code, self._body, self.headers = status, body, {}

    def iter_content(self, size: int):
        for start in range(0, len(self._body), size):
            yield self._body[start:start + size]


class FakeHttp:
    """A requests-like session serving ``bodies`` by file name (a Range header gets a 206)."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies
        self.gets: list[tuple[str, dict]] = []

    def head(self, *_args, **_kwargs):
        raise AssertionError("no listing: the sizes and checks are pinned")

    def get(self, url, headers=None, stream=False, timeout=None):
        headers = dict(headers or {})
        self.gets.append((url, headers))
        body = self.bodies[url.rsplit("/", 1)[-1]]
        if "Range" in headers:
            start = int(headers["Range"].split("=")[1].rstrip("-"))
            return FakeResponse(206, body[start:])
        return FakeResponse(200, body)


class ModelFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.folder = wake.model_dir(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_pinned_files_are_openwakeword_s_release_and_never_in_the_repository(self) -> None:
        self.assertEqual([entry.name for entry in wake.MODEL_FILES],
                         ["melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx"])
        self.assertEqual(sum(entry.size for entry in wake.MODEL_FILES), 3_685_906)
        for entry in wake.MODEL_FILES:
            self.assertRegex(entry.sha256, r"^[0-9a-f]{64}$")
        repository = Path(wake.__file__).resolve().parent.parent
        self.assertEqual([path for path in repository.rglob("*.onnx")], [])

    def test_the_download_fetches_from_the_release_checks_and_writes_the_manifest(self) -> None:
        http = FakeHttp(BODIES)
        seen: list[tuple[int, int]] = []
        code = wake.WakeModelDownloader(self.root, files=TABLE, session=http,
                                        progress=lambda done, total: seen.append((done, total))).run()
        self.assertEqual(code, "")
        self.assertEqual([url for url, _headers in http.gets],
                         [f"https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/{name}" for name in BODIES])
        for name, body in BODIES.items():
            self.assertEqual((self.folder / name).read_bytes(), body)
        manifest = json.loads((self.folder / wake.MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["repo"], "dscripka/openWakeWord")
        self.assertEqual(manifest["revision"], "v0.5.1")
        self.assertEqual(manifest["sha256"], {entry.name: entry.sha256 for entry in TABLE})
        self.assertTrue(wake.model_ready(self.folder, TABLE))
        self.assertEqual(seen[-1], (sum(len(body) for body in BODIES.values()),) * 2)

    def test_a_file_that_fails_its_check_is_not_kept(self) -> None:
        bad = dict(BODIES, **{"embedding_model.onnx": b"x" * len(BODIES["embedding_model.onnx"])})
        code = wake.WakeModelDownloader(self.root, files=TABLE, session=FakeHttp(bad)).run()
        self.assertEqual(code, meeting_speech.ERR_CHECK)
        self.assertFalse((self.folder / "embedding_model.onnx").exists())
        self.assertFalse(wake.model_ready(self.folder, TABLE))

    def test_a_download_carries_on_where_it_stopped(self) -> None:
        self.folder.mkdir(parents=True)
        body = BODIES["melspectrogram.onnx"]
        (self.folder / "melspectrogram.onnx.part").write_bytes(body[:1000])
        http = FakeHttp(BODIES)
        self.assertEqual(wake.WakeModelDownloader(self.root, files=TABLE, session=http).run(), "")
        self.assertEqual(http.gets[0][1].get("Range"), "bytes=1000-")
        self.assertEqual((self.folder / "melspectrogram.onnx").read_bytes(), body)

    def test_files_already_there_are_checked_again_without_the_network(self) -> None:
        self.folder.mkdir(parents=True)
        for name, body in BODIES.items():
            (self.folder / name).write_bytes(body)
        http = FakeHttp(BODIES)
        self.assertEqual(wake.WakeModelDownloader(self.root, files=TABLE, session=http).run(), "")
        self.assertEqual(http.gets, [])
        self.assertTrue(wake.model_ready(self.folder, TABLE))

    def test_a_cancel_stops_it(self) -> None:
        cancel = threading.Event()
        cancel.set()
        code = wake.WakeModelDownloader(self.root, files=TABLE, session=FakeHttp(BODIES), cancel=cancel).run()
        self.assertEqual(code, meeting_speech.ERR_CANCELLED)
        self.assertFalse(wake.model_ready(self.folder, TABLE))

    def test_its_log_lines_say_wake_model(self) -> None:
        with self.assertLogs("briefing_reader.meeting_speech", level="INFO") as logs:
            wake.WakeModelDownloader(self.root, files=TABLE, session=FakeHttp(BODIES)).run()
        self.assertTrue(all(record.getMessage().startswith("Wake model:") for record in logs.records))

    def _ready_folder(self, table=wake.MODEL_FILES, sizes=None, digests=None) -> None:
        """The pinned files' sizes (their bytes do not matter to model_ready) and a manifest."""
        self.folder.mkdir(parents=True, exist_ok=True)
        for entry in table:
            with open(self.folder / entry.name, "wb") as fh:
                fh.truncate((sizes or {}).get(entry.name, entry.size))
        manifest = {"files": {entry.name: entry.size for entry in table},
                    "sha256": {entry.name: (digests or {}).get(entry.name, entry.sha256) for entry in table}}
        (self.folder / wake.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")

    def test_ready_needs_every_file_its_size_and_the_checked_manifest(self) -> None:
        self.assertFalse(wake.model_ready(self.folder))
        self._ready_folder()
        self.assertTrue(wake.model_ready(self.folder))
        self._ready_folder(sizes={"hey_jarvis_v0.1.onnx": 10})
        self.assertFalse(wake.model_ready(self.folder))
        self._ready_folder(digests={"melspectrogram.onnx": "0" * 64})
        self.assertFalse(wake.model_ready(self.folder))
        self._ready_folder()
        (self.folder / wake.MANIFEST).write_text("not json", encoding="utf-8")
        self.assertFalse(wake.model_ready(self.folder))

    def test_loading_says_what_is_missing(self) -> None:
        with self.assertRaises(wake.WakeError) as caught:
            wake.load_sessions(self.folder)
        self.assertEqual(caught.exception.code, wake.ERR_MODEL)
        self._ready_folder()
        with mock.patch.dict(sys.modules, {"onnxruntime": None}):
            with self.assertRaises(wake.WakeError) as caught:
                wake.load_sessions(self.folder)
        self.assertEqual(caught.exception.code, wake.ERR_ADDON)

    def test_loading_uses_one_cpu_thread_and_reports_a_bad_file_by_its_code(self) -> None:
        self._ready_folder()
        made: list[tuple[str, object, list]] = []

        class Options:
            pass

        def session(path, sess_options=None, providers=None):
            made.append((Path(path).name, sess_options, providers))
            return object()

        runtime = types.SimpleNamespace(SessionOptions=Options, InferenceSession=session,
                                        ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL="sequential"))
        with mock.patch.dict(sys.modules, {"onnxruntime": runtime}):
            sessions = wake.load_sessions(self.folder)
        self.assertIsInstance(sessions, wake.WakeSessions)
        self.assertEqual([name for name, _options, _providers in made],
                         ["melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx"])
        options = made[0][1]
        self.assertEqual((options.intra_op_num_threads, options.inter_op_num_threads), (1, 1))
        self.assertEqual(made[0][2], ["CPUExecutionProvider"])

        def broken(*_args, **_kwargs):
            raise RuntimeError("C:\\Users\\someone\\secret path in the message")

        runtime.InferenceSession = broken
        with mock.patch.dict(sys.modules, {"onnxruntime": runtime}):
            with self.assertRaises(wake.WakeError) as caught:
                wake.load_sessions(self.folder)
        self.assertEqual(caught.exception.code, wake.ERR_FAILED)
        self.assertEqual(str(caught.exception), wake.ERR_FAILED)

    def test_runtime_missing_names_the_add_on(self) -> None:
        real = importlib.util.find_spec
        with mock.patch("importlib.util.find_spec", lambda name: None if name == "onnxruntime" else real(name)):
            self.assertEqual(wake.runtime_missing(), "onnxruntime")
        with mock.patch("importlib.util.find_spec", lambda name: object()):
            self.assertEqual(wake.runtime_missing(), "")


if __name__ == "__main__":
    unittest.main()
