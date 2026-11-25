"""The voiceprint's maths and files (voiceprint.py): the features (an exact port of the measured prototype), the
embedding on a fake onnxruntime session, the template and its file, the verdict, the enrolment takes' checks and the
pinned model file. No model file, microphone or network is used: every session is a fake."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from briefing_reader import voiceprint, wake
from briefing_reader.voiceprint import Template, TemplateStore, Verdict


def tone(seconds: float, amplitude: int = 6000, freq: float = 220.0) -> bytes:
    count = int(seconds * voiceprint.SAMPLE_RATE)
    t = np.arange(count) / voiceprint.SAMPLE_RATE
    wave = amplitude * np.sin(2 * np.pi * freq * t) * (1 + 0.3 * np.sin(2 * np.pi * 3 * t))
    return wave.astype("<i2").tobytes()


def room(seconds: float, level: int = 20, seed: int = 1) -> bytes:
    count = int(seconds * voiceprint.SAMPLE_RATE)
    return np.random.default_rng(seed).integers(-level, level, count).astype("<i2").tobytes()


def unit(index: int, other: int | None = None, weight: float = 0.0) -> np.ndarray:
    vector = np.zeros(voiceprint.EMBED_DIM)
    vector[index] = 1.0
    if other is not None:
        vector[other] = weight
    return vector / np.linalg.norm(vector)


# The prototype's own code: the prototype measured on the real model (see the plan). The port must give exactly the
# same numbers.
def _proto_mel_banks(n_mels=80, n_fft=512, sr=16000, low=20.0, high=0.0):
    high = sr / 2 + high if high <= 0 else high
    mel = lambda f: 1127.0 * np.log(1.0 + f / 700.0)  # noqa: E731
    lo, hi = mel(low), mel(high)
    delta = (hi - lo) / (n_mels + 1)
    bins = n_fft // 2
    fft_mel = mel(np.arange(bins) * sr / n_fft)
    left = lo + np.arange(n_mels)[:, None] * delta
    center, right = left + delta, left + 2 * delta
    up = (fft_mel[None, :] - left) / (center - left)
    down = (right - fft_mel[None, :]) / (right - center)
    w = np.maximum(0.0, np.minimum(up, down))
    return np.pad(w, ((0, 0), (0, 1)))


_PROTO_BANKS = _proto_mel_banks()


def _proto_fbank(samples):
    x = samples.astype(np.float64) * 32768.0
    flen, fshift = 400, 160
    n = 1 + (len(x) - flen) // fshift
    idx = np.arange(flen)[None, :] + fshift * np.arange(n)[:, None]
    fr = x[idx]
    fr = fr - fr.mean(axis=1, keepdims=True)
    fr = np.concatenate([fr[:, :1] - 0.97 * fr[:, :1], fr[:, 1:] - 0.97 * fr[:, :-1]], axis=1)
    fr = fr * np.hamming(flen)
    spec = np.abs(np.fft.rfft(fr, n=512)) ** 2
    feats = np.log(np.maximum(spec @ _PROTO_BANKS.T, np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


class FakeSession:
    """onnxruntime's InferenceSession as the embedder uses it: get_inputs() and run(None, feeds)."""

    def __init__(self, output=None) -> None:
        self.feeds: list[dict] = []
        self.output = unit(3) if output is None else output

    def get_inputs(self):
        return [SimpleNamespace(name="feats", shape=["B", "T", 80])]

    def run(self, _outputs, feeds):
        self.feeds.append(feeds)
        value = self.output(feeds) if callable(self.output) else self.output
        return [np.asarray(value, dtype=np.float32).reshape(1, -1) * 7.5]     # not unit length: the embedder scales it


class FeaturesTests(unittest.TestCase):
    def test_the_mel_banks_and_fbank_are_the_prototype_s_exactly(self) -> None:
        np.testing.assert_array_equal(voiceprint.mel_banks(), _PROTO_BANKS)
        self.assertEqual(voiceprint.mel_banks().shape, (80, 257))
        pcm = room(0.4) + tone(1.1) + room(0.3, seed=2)
        samples = voiceprint.samples_of(pcm)
        ours = voiceprint.fbank(samples)
        np.testing.assert_array_equal(ours, _proto_fbank(samples))
        self.assertEqual(ours.dtype, np.float32)
        self.assertEqual(ours.shape, (1 + (len(samples) - 400) // 160, 80))
        self.assertLess(float(np.abs(ours.mean(axis=0)).max()), 1e-4)          # CMN: the mean over time is off

    def test_samples_are_read_as_the_prototype_read_its_files(self) -> None:
        pcm = np.array([0, 32767, -32768, 1000], dtype="<i2").tobytes()
        samples = voiceprint.samples_of(pcm + b"\x01")                         # an odd byte is dropped
        self.assertEqual(samples.dtype, np.float32)
        np.testing.assert_array_equal(samples * 32768.0, [0, 32767, -32768, 1000])   # int16 range again in fbank

    def test_a_silent_frame_is_floored_not_minus_infinity(self) -> None:
        feats = voiceprint.fbank(np.zeros(16000, dtype=np.float32))
        self.assertTrue(np.all(np.isfinite(feats)))


class EmbedderTests(unittest.TestCase):
    def test_it_feeds_the_features_and_gives_a_unit_vector(self) -> None:
        session = FakeSession(unit(5, 9, 0.5))
        vector = voiceprint.Embedder(session).embed(room(0.3) + tone(1.0))
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1.0, places=6)
        self.assertAlmostEqual(float(vector @ unit(5, 9, 0.5)), 1.0, places=5)
        (feeds,) = session.feeds
        self.assertEqual(list(feeds), ["feats"])
        feats = feeds["feats"]
        self.assertEqual(feats.dtype, np.float32)
        self.assertEqual((feats.ndim, feats.shape[0], feats.shape[2]), (3, 1, 80))

    def test_a_wrong_output_or_too_little_audio_is_an_error(self) -> None:
        for output in (np.ones(128), np.full(voiceprint.EMBED_DIM, np.nan), np.zeros(voiceprint.EMBED_DIM)):
            with self.subTest(size=output.size), self.assertRaises(voiceprint.VoiceprintError):
                voiceprint.Embedder(FakeSession(output)).embed(tone(1.0))
        with self.assertRaises(voiceprint.VoiceprintError):
            voiceprint.Embedder(FakeSession()).embed(tone(0.01))

    def test_the_session_is_loaded_only_from_a_checked_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(voiceprint.VoiceprintError) as caught:
                voiceprint.load_session(Path(tmp))
            self.assertEqual(caught.exception.code, voiceprint.ERR_MODEL)


class TemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def test_the_score_is_the_cosine_to_the_normalised_mean(self) -> None:
        template = Template.from_embeddings([unit(0), unit(0, 1, 0.2), unit(0, 2, 0.2)])
        self.assertEqual(template.samples, 3)
        mean = np.mean([unit(0), unit(0, 1, 0.2), unit(0, 2, 0.2)], axis=0)
        probe = unit(0, 1, 1.0)
        self.assertAlmostEqual(template.score(probe), float(mean @ probe / np.linalg.norm(mean)), places=6)
        self.assertGreater(template.score(unit(0)), 0.9)
        self.assertLess(template.score(unit(7)), 0.1)

    def test_the_file_holds_only_numbers_a_version_a_time_and_the_count(self) -> None:
        store = TemplateStore(self.folder)
        template = Template.from_embeddings([unit(0), unit(1, 0, 1.0), unit(0, 2, 0.3)])
        self.assertTrue(store.save(template))
        data = json.loads((self.folder / voiceprint.VOICEPRINT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"version", "created", "samples", "embeddings", "mean"})
        self.assertEqual((data["version"], data["samples"], len(data["embeddings"])), (1, 3, 3))
        self.assertTrue(all(isinstance(value, float) for row in data["embeddings"] for value in row))
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), [voiceprint.VOICEPRINT_FILE])
        loaded, why = store.load()
        self.assertEqual(why, "")
        self.assertAlmostEqual(loaded.score(unit(0)), template.score(unit(0)), places=5)

    def test_a_bad_file_is_not_enrolled_and_logged_with_a_word_only(self) -> None:
        store = TemplateStore(self.folder)
        good = Template.from_embeddings([unit(0), unit(0), unit(0)]).to_json()
        cases = {
            "version": dict(good, version=2),
            "length": dict(good, mean=good["mean"][:-1]),
            "count": dict(good, samples=4),
            "number": dict(good, embeddings=[[math.inf] + row[1:] for row in good["embeddings"]]),
        }
        for name, data in cases.items():
            with self.subTest(name):
                store.path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertLogs("briefing_reader.voiceprint", level="WARNING") as logs:
                    template, why = store.load()
                self.assertIsNone(template)
                self.assertIn(why, (voiceprint.BAD_VERSION, voiceprint.BAD_LENGTH, voiceprint.BAD_NUMBER))
                self.assertEqual(logs.output, [f"WARNING:briefing_reader.voiceprint:Voiceprint: the template was not "
                                               f"used ({why})"])
        store.path.write_text("not json", encoding="utf-8")
        self.assertEqual(store.load()[0], None)
        nan_text = json.dumps(good).replace(str(good["mean"][0]), "NaN", 1)
        store.path.write_text(nan_text, encoding="utf-8")
        self.assertEqual(store.load(), (None, voiceprint.BAD_NUMBER))

    def test_no_file_is_not_enrolled_and_forget_deletes_it(self) -> None:
        store = TemplateStore(self.folder)
        self.assertEqual(store.load(), (None, voiceprint.WHY_NO_TEMPLATE))
        store.save(Template.from_embeddings([unit(0)]))
        self.assertTrue(store.exists())
        self.assertTrue(store.forget())
        self.assertFalse(store.exists())
        self.assertTrue(store.forget())                                           # nothing to delete is fine

    def test_a_failed_save_leaves_nothing_behind(self) -> None:
        store = TemplateStore(self.folder)
        with mock.patch("briefing_reader.atomic.replace", side_effect=OSError("busy")):
            self.assertFalse(store.save(Template.from_embeddings([unit(0)])))
        self.assertEqual(list(self.folder.iterdir()), [])


class FakeEmbedder:
    def __init__(self, vector=None, fail=False) -> None:
        self.vector = unit(0) if vector is None else vector
        self.fail = fail
        self.calls = 0

    def embed(self, _pcm):
        self.calls += 1
        if self.fail:
            raise voiceprint.VoiceprintError(voiceprint.ERR_FAILED)
        return self.vector


class VerdictTests(unittest.TestCase):
    TEMPLATE = Template.from_embeddings([unit(0), unit(0), unit(0)])
    CLIP = room(0.3) + tone(1.1) + room(0.2)

    def test_a_match_and_a_mismatch_at_the_threshold(self) -> None:
        self.assertEqual(voiceprint.THRESHOLD, 0.50)
        verdict, took = voiceprint.check(self.CLIP, self.TEMPLATE, FakeEmbedder(unit(0, 1, 0.3)),
                                         clock=iter([10.0, 10.0]).__next__)            # an embedding of 0 ms ...
        self.assertEqual((verdict.kind, verdict.score), (voiceprint.MATCH, 0.96))
        self.assertTrue(verdict.allows)
        self.assertEqual(took, 0)                                                  # ... is still a time
        other = np.zeros(voiceprint.EMBED_DIM)
        other[0], other[1] = 0.45, math.sqrt(1 - 0.45 ** 2)
        verdict, _took = voiceprint.check(self.CLIP, self.TEMPLATE, FakeEmbedder(other))
        self.assertEqual((verdict.kind, verdict.score), (voiceprint.MISMATCH, 0.45))
        self.assertFalse(verdict.allows)

    def test_a_short_clip_is_unchecked_and_let_through(self) -> None:
        embedder = FakeEmbedder(unit(9))
        verdict, took = voiceprint.check(room(0.6) + tone(0.4) + room(0.6), self.TEMPLATE, embedder)
        self.assertEqual((verdict.kind, verdict.reason, took), (voiceprint.UNCHECKED, voiceprint.WHY_SHORT, None))
        self.assertTrue(verdict.allows)
        self.assertEqual(embedder.calls, 0)

    def test_a_failing_model_or_no_template_is_unchecked(self) -> None:
        self.assertEqual(voiceprint.check(self.CLIP, self.TEMPLATE, FakeEmbedder(fail=True))[0],
                         Verdict(voiceprint.UNCHECKED, None, voiceprint.WHY_FAILED))
        self.assertEqual(voiceprint.check(self.CLIP, None, FakeEmbedder())[0].reason, voiceprint.WHY_NO_TEMPLATE)


class TakeTests(unittest.TestCase):
    def test_silent_quiet_short_long_and_fine(self) -> None:
        self.assertEqual(voiceprint.take_problem(bytes(32000)), voiceprint.TAKE_SILENT)
        self.assertEqual(voiceprint.take_problem(b""), voiceprint.TAKE_SILENT)
        self.assertEqual(voiceprint.take_problem(room(2.0)), voiceprint.TAKE_QUIET)
        self.assertEqual(voiceprint.take_problem(room(0.3) + tone(0.3) + room(0.6)), voiceprint.TAKE_SHORT)
        self.assertEqual(voiceprint.take_problem(room(0.3) + tone(0.6) + room(0.3)), "")   # 0.5 s of speech is enough
        self.assertEqual(voiceprint.take_problem(room(0.5) + tone(4.5)), voiceprint.TAKE_LONG)
        self.assertEqual(voiceprint.take_problem(room(0.5) + tone(1.0) + room(0.3)), "")

    def test_speech_is_measured_from_its_first_to_its_last_loud_frame(self) -> None:
        self.assertEqual(voiceprint.speech_seconds(room(1.0)), 0.0)
        self.assertAlmostEqual(voiceprint.speech_seconds(room(0.3) + tone(0.9) + room(0.3)), 0.9, delta=0.07)
        gap = room(0.3) + tone(0.4) + room(0.2, seed=3) + tone(0.4) + room(0.3)
        self.assertAlmostEqual(voiceprint.speech_seconds(gap), 1.0, delta=0.07)        # a pause inside counts

    def test_the_takes_must_agree(self) -> None:
        self.assertEqual(voiceprint.AGREE_MIN, 0.60)
        self.assertTrue(voiceprint.takes_agree([unit(0), unit(0, 1, 0.5), unit(0, 2, 0.5)]))
        self.assertFalse(voiceprint.takes_agree([unit(0), unit(0, 1, 0.5), unit(4)]))
        self.assertFalse(voiceprint.takes_agree([]))


class FixRoundTests(unittest.TestCase):
    """The review's fix round: the check's speech measure (M2), each take's window (L5), leftover temporary files
    (L1)."""

    def test_the_speech_measure_has_no_absolute_floor(self) -> None:
        self.assertEqual((voiceprint.MIN_SPEECH_S, voiceprint.SPEECH_ABOVE_DB), (0.5, 6.0))
        quiet_mic = room(0.3, level=3) + tone(0.9, amplitude=120) + room(0.3, level=3, seed=2)   # about -52 dBFS
        self.assertAlmostEqual(voiceprint.speech_seconds(quiet_mic), 0.9, delta=0.07)
        embedder = FakeEmbedder(unit(0))
        verdict, _took = voiceprint.check(quiet_mic, VerdictTests.TEMPLATE, embedder)
        self.assertEqual(verdict.kind, voiceprint.MATCH)                          # checked, not "short"
        self.assertEqual(embedder.calls, 1)
        silence_then_word = bytes(16000) + tone(0.7, amplitude=200)
        self.assertAlmostEqual(voiceprint.speech_seconds(silence_then_word), 0.7, delta=0.07)
        self.assertEqual(voiceprint.speech_seconds(bytes(32000)), 0.0)

    def test_a_take_s_window_is_the_wake_clip_ending_where_its_speech_ended(self) -> None:
        self.assertEqual(voiceprint.WAKE_CLIP_BYTES, 51200)                      # wake_listen's 1.6 s pre-roll
        clip = bytes(range(256)) * 400                                            # 102400 bytes
        window = voiceprint.take_window(clip, 80001)
        self.assertEqual(window, clip[80000 - 51200:80000])
        self.assertEqual(voiceprint.take_window(clip, 1000), clip[:1000])
        self.assertEqual(len(voiceprint.take_window(clip, 10 ** 9)), 51200)

    def test_leftover_temporary_files_are_swept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / f"{voiceprint.VOICEPRINT_FILE}.abc.tmp").write_text("x", encoding="utf-8")
            (folder / "other.json.abc.tmp").write_text("x", encoding="utf-8")
            store = TemplateStore(folder)                                         # swept as it is made
            self.assertEqual(sorted(path.name for path in folder.iterdir()), ["other.json.abc.tmp"])
            store.save(Template.from_embeddings([unit(0)]))
            (folder / f"{voiceprint.VOICEPRINT_FILE}.def.tmp").write_text("x", encoding="utf-8")
            self.assertTrue(store.forget())                                       # Forget takes them too
            self.assertEqual(sorted(path.name for path in folder.iterdir()), ["other.json.abc.tmp"])


class ModelFileTests(unittest.TestCase):
    def test_the_pinned_file_and_its_own_folder(self) -> None:
        self.assertEqual(voiceprint.MODEL_FILE, wake.ModelFile(
            "wespeaker_en_voxceleb_resnet34.onnx", 26534365,
            "5ef208a9da1453335308a6b6f4e6dfbd7e183a38b604de0a57664f45d257fe94"))
        root = Path("C:/models")
        self.assertEqual(voiceprint.model_dir(root), root / "voiceprint")
        self.assertNotEqual(voiceprint.model_dir(root), wake.model_dir(root))
        self.assertEqual(voiceprint.CREDIT, "Speaker model: WeSpeaker ResNet34 (VoxCeleb), CC BY 4.0")

    def test_ready_only_with_the_checked_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = voiceprint.model_dir(Path(tmp))
            folder.mkdir(parents=True)
            with open(folder / voiceprint.MODEL_FILE.name, "wb") as fh:
                fh.truncate(voiceprint.MODEL_FILE.size)
            self.assertFalse(voiceprint.model_ready(folder))
            manifest = {"files": {voiceprint.MODEL_FILE.name: voiceprint.MODEL_FILE.size},
                        "sha256": {voiceprint.MODEL_FILE.name: "0" * 64}}
            (folder / voiceprint.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
            self.assertFalse(voiceprint.model_ready(folder))                        # another file's checksum
            manifest["sha256"][voiceprint.MODEL_FILE.name] = voiceprint.MODEL_FILE.sha256
            (folder / voiceprint.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(voiceprint.model_ready(folder))

    def test_the_downloader_fetches_the_pinned_release_into_its_folder(self) -> None:
        downloader = voiceprint.VoiceprintModelDownloader(Path("C:/models"), session=object())
        self.assertEqual(downloader.target, Path("C:/models") / "voiceprint")
        self.assertEqual(downloader.files, (voiceprint.MODEL_FILE.name,))
        self.assertEqual(downloader._url(voiceprint.MODEL_FILE.name),
                         "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
                         "wespeaker_en_voxceleb_resnet34.onnx")
        self.assertEqual(downloader._expected(voiceprint.MODEL_FILE.name),
                         (voiceprint.MODEL_FILE.size, voiceprint.MODEL_FILE.sha256))

    def test_the_downloader_writes_the_checked_manifest(self) -> None:
        payload = b"speaker model bytes"
        import hashlib

        pinned = wake.ModelFile("model.onnx", len(payload), hashlib.sha256(payload).hexdigest())

        class Response:
            status_code = 200
            headers: dict = {}

            def iter_content(self, _size):
                return iter([payload])

        class Session:
            urls: list[str] = []

            def get(self, url, **_kwargs):
                self.urls.append(url)
                return Response()

        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            error = voiceprint.VoiceprintModelDownloader(Path(tmp), session=session, files=(pinned,)).run()
            self.assertEqual(error, "")
            self.assertTrue(voiceprint.model_ready(voiceprint.model_dir(Path(tmp)), (pinned,)))
            self.assertEqual(session.urls, ["https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                                            "speaker-recongition-models/model.onnx"])


if __name__ == "__main__":
    unittest.main()
