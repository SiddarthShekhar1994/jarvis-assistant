"""Jarvis's voice made on this PC: the app's side (local_voice), the voice process's parts and loop
(local_voice_worker) and the [voice] settings. No model, misaki, spaCy, torch or onnxruntime is loaded: the speaker
and the model's session are fakes, the client's process is serve() on a thread over real pipes, and no real process is
ever started (the test guard refuses one)."""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import importlib.util

from briefing_reader import config as cfg
from briefing_reader import local_voice as lv
from briefing_reader import local_voice_worker as lw
from briefing_reader import meeting_speech as sp
from tests.test_config import ProjectTestCase

HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
SECRET = "Dinner with Ana Lima at the Ivy"          # stands for a private line: it must never reach a log


def tone_wav(path: Path, ms: int, value: int = 1000) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(lv.SAMPLE_RATE)
        target.writeframes(value.to_bytes(2, "little", signed=True) * (lv.SAMPLE_RATE * ms // 1000))


def wav_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as source:
        return round(source.getnframes() * 1000 / source.getframerate())


def make_models(root: Path) -> None:
    """Every model's files (tiny) with the manifest model_ready checks: the voice counts as downloaded."""
    for model in lv.MODELS:
        folder = sp.model_dir(root, model)
        sizes = {}
        for name in model.files:
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 8)
            sizes[name] = 8
        (folder / sp.MANIFEST).write_text(json.dumps({"files": sizes}), encoding="utf-8")


# --------------------------------------------------------------------------
# Voices, the engine setting, the models and the add-ons
# --------------------------------------------------------------------------

class VoicesTests(unittest.TestCase):
    def test_the_28_english_voices_and_george_first_choice(self) -> None:
        self.assertEqual(len(lv.VOICE_NAMES), 28)
        self.assertEqual(lv.DEFAULT_VOICE, "bm_george")
        self.assertIn("bm_george", lv.VOICE_NAMES)
        self.assertEqual({name[:3] for name in lv.VOICE_NAMES}, {"af_", "am_", "bf_", "bm_"})

    def test_labels_and_accents(self) -> None:
        self.assertEqual(lv.voice_label("bm_george"), "George (British)")
        self.assertEqual(lv.voice_label("af_heart"), "Heart (American)")
        self.assertEqual(lv.voice_label("xx_nobody"), "xx_nobody")
        self.assertTrue(lv.british("bf_emma"))
        self.assertFalse(lv.british("am_michael"))

    def test_is_local_only_when_the_setting_says_so(self) -> None:
        self.assertTrue(lv.is_local(SimpleNamespace(voice=SimpleNamespace(engine="local"))))
        self.assertFalse(lv.is_local(SimpleNamespace(voice=SimpleNamespace(engine="online"))))
        self.assertFalse(lv.is_local(SimpleNamespace(voice=SimpleNamespace())))        # fail-closed
        self.assertFalse(lv.is_local(SimpleNamespace()))


class VoiceConfigTests(ProjectTestCase):
    def test_defaults_are_the_local_voice_george_kept_loaded(self) -> None:
        voice = self.load_quietly().voice
        self.assertEqual((voice.engine, voice.local_voice, voice.keep_warm_minutes), ("local", "bm_george", 0))
        self.assertTrue(lv.is_local(self.load_quietly()))

    def test_settings_are_read(self) -> None:
        self.write_config('[voice]\nengine = "Online"\nlocal_voice = "BF_EMMA"\nkeep_warm_minutes = 30\n')
        voice = self.load_quietly().voice
        self.assertEqual((voice.engine, voice.local_voice, voice.keep_warm_minutes), ("online", "bf_emma", 30))

    def test_bad_values_fall_back_with_a_warning(self) -> None:
        self.write_config('[voice]\nengine = "cloud"\nlocal_voice = "bm_jarvis"\nkeep_warm_minutes = 5000\n')
        voice = self.load_warning("voice.engine", "voice.local_voice", "voice.keep_warm_minutes").voice
        self.assertEqual((voice.engine, voice.local_voice, voice.keep_warm_minutes), ("local", "bm_george", 1440))

    def test_the_shipped_config_has_the_new_lines(self) -> None:
        text = (cfg.PROJECT_ROOT / "config.toml").read_text(encoding="utf-8")
        self.assertIn('engine = "local"', text)
        self.assertIn('local_voice = "bm_george"', text)
        self.assertIn("keep_warm_minutes = 0", text)


class ModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_pinned_revisions_and_the_files(self) -> None:
        for model in lv.MODELS:
            self.assertRegex(model.revision, r"^[0-9a-f]{40}$")
        self.assertIn("onnx/model.onnx", lv.KOKORO.files)
        self.assertIn("tokenizer.json", lv.KOKORO.files)
        self.assertEqual(len([name for name in lv.KOKORO.files if name.startswith("voices/")]), 28)
        self.assertEqual(lv.G2P_GB.files, ("config.json", "generation_config.json", "model.safetensors"))
        self.assertEqual(lv.KOKORO.repo, "onnx-community/Kokoro-82M-v1.0-ONNX-timestamped")

    def test_the_size_said_is_the_size(self) -> None:
        total = sum(lv.SIZE_BYTES.values())
        self.assertTrue(345_500_000 < total < 346_500_000, total)
        self.assertEqual(lv.SIZE_TEXT, "about 346 MB")

    def test_ready_only_when_all_three_are_whole(self) -> None:
        self.assertFalse(lv.models_ready(self.root))
        self.assertEqual([model.name for model in lv.models_missing(self.root)], [m.name for m in lv.MODELS])
        make_models(self.root)
        self.assertTrue(lv.models_ready(self.root))
        (sp.model_dir(self.root, lv.KOKORO) / "voices" / "bm_george.bin").write_bytes(b"short")
        self.assertEqual(lv.models_missing(self.root), [lv.KOKORO])

    def test_the_downloader_puts_files_in_subfolders(self) -> None:
        body = b"model-bytes"

        class Response:
            def __init__(self, status: int, headers: dict, data: bytes = b"") -> None:
                self.status_code, self.headers, self._data = status, headers, data

            def iter_content(self, _size: int):
                yield self._data

        class Session:
            def head(self, _url, allow_redirects=False, timeout=None, headers=None):
                return Response(200, {"Content-Length": str(len(body))})

            def get(self, _url, headers=None, stream=False, timeout=None):
                return Response(200, {}, body)

        model = sp.SpeechModel("t", "repo/t", "t", "tiny", ("onnx/model.onnx", "voices/a.bin"), "f" * 40)
        error = sp.ModelDownloader.for_model(self.root, model, session=Session()).run()
        self.assertEqual(error, "")
        self.assertEqual((self.root / "t" / "onnx" / "model.onnx").read_bytes(), body)
        self.assertTrue(sp.model_ready(sp.model_dir(self.root, model), model))


class AddonsTests(unittest.TestCase):
    def test_missing_modules_are_named_in_order(self) -> None:
        present = {"onnxruntime", "numpy", "torch", "transformers"}
        missing = lv.addons_missing(lambda name: object() if name in present else None)
        self.assertEqual(missing, ["spacy", "num2words", "en_core_web_sm", "misaki"])
        self.assertEqual(lv.addons_missing(lambda name: object()), [])

    def test_the_owner_lines(self) -> None:
        python = "C:/Python313/pythonw.exe"
        lines = lv.pip_lines(["spacy", "num2words", "en_core_web_sm", "misaki"], python=python)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("C:/Python313/python.exe -m pip install "))
        self.assertIn('"spacy>=3.8,<3.9"', lines[0])
        self.assertIn("en_core_web_sm-3.8.0-py3-none-any.whl", lines[0])
        self.assertNotIn("misaki", lines[0])
        self.assertEqual(lines[1], 'C:/Python313/python.exe -m pip install --ignore-requires-python "misaki==0.9.4"')
        everything = " ".join(lv.pip_lines([module for module, _spec in lv.ADDONS], python=python)).lower()
        for gpl in ("espeak", "phonemizer", "kokoro"):
            self.assertNotIn(gpl, everything)

    def test_a_folder_with_spaces_is_quoted_and_nothing_missing_is_no_line(self) -> None:
        line = lv.pip_lines(["misaki"], python="C:/Program Files/Python/python.exe")[0]
        self.assertTrue(line.startswith('& "C:/Program Files/Python/python.exe" -m pip install'))
        self.assertEqual(lv.pip_lines([], python="python.exe"), [])

    def test_why_it_cannot_run(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.assertEqual(lv.missing_reason(root, addons=lambda: ["misaki"]), lv.ERR_ADDON)
            self.assertEqual(lv.missing_reason(root, addons=lambda: []), lv.ERR_MODEL)
            make_models(root)
            self.assertEqual(lv.missing_reason(root, addons=lambda: []), "")


# --------------------------------------------------------------------------
# Text into requests, speed, volume and WAVs
# --------------------------------------------------------------------------

class SentenceGroupsTests(unittest.TestCase):
    def test_a_short_line_is_one_request(self) -> None:
        self.assertEqual(lv.sentence_groups("Good morning, sir. Your briefing is ready."),
                         ["Good morning, sir. Your briefing is ready."])

    def test_long_text_is_cut_between_sentences_within_the_limit(self) -> None:
        sentences = [f"Sentence number {index} says a few more words than it needs to." for index in range(20)]
        groups = lv.sentence_groups(" ".join(sentences), most=150)
        self.assertGreater(len(groups), 5)
        self.assertTrue(all(len(group) <= 150 for group in groups))
        self.assertEqual(" ".join(groups), " ".join(sentences))

    def test_abbreviations_and_times_do_not_end_a_sentence(self) -> None:
        text = "Dr. Smith comes at 9 a.m. tomorrow, e.g. before lunch. Mr. Jones follows."
        self.assertEqual(lv.sentence_groups(text, most=40),
                         ["Dr. Smith comes at 9 a.m. tomorrow, e.g. before lunch.", "Mr. Jones follows."])

    def test_lines_never_run_into_one(self) -> None:
        self.assertEqual(lv.sentence_groups("Project sync at 9:30\nLunch with Ana\n\nDone!"),
                         ["Project sync at 9:30. Lunch with Ana. Done!"])

    def test_one_long_sentence_stays_whole_and_nothing_is_nothing(self) -> None:
        long = "word " * 60 + "end."
        self.assertEqual(lv.sentence_groups(long, most=100), [" ".join(long.split())])
        self.assertEqual(lv.sentence_groups("  \n "), [])

    def test_a_very_long_sentence_is_cut_at_its_clauses(self) -> None:
        clause = "the figures for the north office were higher than the plan"
        text = ", ".join([clause] * 30) + "."
        groups = lv.sentence_groups(text)
        self.assertGreater(len(groups), 3)
        self.assertTrue(all(len(group) <= lv.LONG_CHARS for group in groups), [len(g) for g in groups])
        self.assertTrue(all(group.endswith((",", ".")) for group in groups))
        self.assertEqual(" ".join(" ".join(groups).split()), " ".join(text.split()))
        spaceless = "x" * 1000                                # no space at all: cut at the limit
        self.assertTrue(all(len(group) <= lv.LONG_CHARS + 1 for group in lv.sentence_groups(spaceless)))
        self.assertLess(max(len(group) for group in lv.sentence_groups(" ".join(["word"] * 1500))),
                        lv.MAX_TEXT_CHARS)


class SpeedGainTests(unittest.TestCase):
    def test_rate_and_volume(self) -> None:
        self.assertEqual((lv.speed_for(0), lv.speed_for(15), lv.speed_for(-10)), (1.0, 1.15, 0.9))
        self.assertEqual((lv.speed_for(-90), lv.speed_for(300)), (0.5, 2.0))
        self.assertEqual((lv.gain_for(0), lv.gain_for(-20), lv.gain_for(-150), lv.gain_for(250)), (1.0, 0.8, 0.0, 2.0))


class JoinWavsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_parts_follow_each_other(self) -> None:
        tone_wav(self.root / "a.wav", 300)
        tone_wav(self.root / "b.wav", 500)
        total, lengths = lv.join_wavs([self.root / "a.wav", self.root / "b.wav"], self.root / "out.wav", gap_ms=100)
        self.assertEqual((total, lengths), (900, (300, 500)))
        self.assertEqual(wav_ms(self.root / "out.wav"), 900)
        self.assertFalse((self.root / "out.wav.part").exists())

    def test_another_format_is_refused_and_nothing_is_left(self) -> None:
        with wave.open(str(self.root / "odd.wav"), "wb") as target:
            target.setnchannels(2)
            target.setsampwidth(2)
            target.setframerate(44_100)
            target.writeframes(b"\x00\x00\x00\x00" * 10)
        with self.assertRaises(ValueError):
            lv.join_wavs([self.root / "odd.wav"], self.root / "out.wav")
        self.assertFalse((self.root / "out.wav").exists())
        self.assertFalse((self.root / "out.wav.part").exists())


# --------------------------------------------------------------------------
# The worker's parts
# --------------------------------------------------------------------------

def token(text: str, phonemes: str | None, space: bool = True) -> SimpleNamespace:
    return SimpleNamespace(text=text, phonemes=phonemes, whitespace=" " if space else "")


VOCAB = {" ": 16, ",": 3, ".": 4, "a": 43, "b": 44, "d": 46, "h": 50, "m": 55, "n": 56, "s": 61, "t": 62, "ɜ": 87,
         "ˈ": 156, "ː": 158}


class PiecesTests(unittest.TestCase):
    def test_tokens_become_ids_words_and_stops(self) -> None:
        pieces = lw.token_pieces([token("Good", "ɡˈʊd", False), token(",", ","), token("sir", "sˈɜː", False),
                                  token(".", ".", False)], VOCAB)
        self.assertEqual([piece.word for piece in pieces], ["Good", "", "sir", ""])
        self.assertEqual(pieces[0].ids, (156, 46))                      # ɡ and ʊ have no id: dropped
        self.assertEqual(pieces[1].ids, (3, 16))                       # the comma, then the space after it
        self.assertEqual([piece.stop for piece in pieces], [False, True, False, True])
        self.assertNotIn("Good", repr(pieces[0]))

    def test_a_word_with_no_sound_says_nothing(self) -> None:
        pieces = lw.token_pieces([token("???", None), token("Hm", "hm", False)], VOCAB)
        self.assertEqual([(piece.ids, piece.word) for piece in pieces], [((16,), ""), ((50, 55), "Hm")])

    def test_long_inputs_are_cut_after_a_clause_mark(self) -> None:
        a, comma, b = lw.Piece((1,) * 4, "a"), lw.Piece((3, 16), "", True), lw.Piece((2,) * 4, "b")
        chunks = lw.chunk_pieces([a, comma, b, a], limit=8)
        self.assertEqual([[len(p.ids) for p in chunk] for chunk in chunks], [[4, 2], [4, 4]])
        huge = lw.Piece((5,) * 20, "huge")
        self.assertEqual([len(chunk[0].ids) for chunk in lw.chunk_pieces([huge], limit=8)], [8])
        many = [lw.Piece((1,) * 3, "w")] * 7
        self.assertTrue(all(sum(len(p.ids) for p in chunk) <= 8 for chunk in lw.chunk_pieces(many, limit=8)))

    def test_word_starts_from_the_durations(self) -> None:
        pieces = [lw.Piece((1, 2), "Good"), lw.Piece((3, 16), "", True), lw.Piece((4, 5), "sir")]
        durations = [2.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0]          # pad, 6 ids, pad
        samples = int(sum(durations) * lw.SAMPLES_PER_UNIT)
        self.assertEqual(lw.word_starts(pieces, durations, samples), [(50, "Good"), (150, "sir")])
        self.assertEqual(lw.word_starts(pieces, durations, samples * 2), [(100, "Good"), (300, "sir")])
        self.assertEqual(lw.word_starts(pieces, [0.0] * 8, samples), [])


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class AudioTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_pcm16_gain_and_no_clipping(self) -> None:
        import numpy as np

        quiet = np.frombuffer(lw.pcm16([0.5, -0.5], gain=0.5), dtype="<i2")
        self.assertEqual(quiet.tolist(), [8191, -8191])
        loud = np.frombuffer(lw.pcm16([0.8, -0.4], gain=2.0), dtype="<i2")
        self.assertLessEqual(int(np.abs(loud).max()), int(lw.PEAK * 32767) + 1)
        self.assertEqual(lw.pcm16([], gain=1.0), b"")

    def test_write_wav_and_the_place_check(self) -> None:
        path = self.root / "say" / "a.wav"
        lw.write_wav(path, b"\x01\x00" * lv.SAMPLE_RATE)
        self.assertEqual(wav_ms(path), 1000)
        self.assertFalse(path.with_name("a.wav.part").exists())
        self.assertTrue(lw.inside(path, self.root))
        self.assertFalse(lw.inside(self.root.parent / "x.wav", self.root))
        self.assertFalse(lw.inside(self.root / ".." / "x.wav", self.root))
        self.assertFalse(lw.inside(self.root / "x.exe", self.root))


class FakeSession:
    """The model: every id lasts one unit (the pads two), and the waveform is that long."""

    def __init__(self) -> None:
        self.feeds: list[dict] = []

    def run(self, names, feeds):
        import numpy as np

        self.feeds.append(feeds)
        count = feeds["input_ids"].shape[1]
        durations = np.ones((1, count), dtype=np.float32)
        durations[0, 0] = durations[0, -1] = 2.0
        samples = int(durations.sum()) * lw.SAMPLES_PER_UNIT
        self.names = names
        return np.full((1, samples), 0.25, dtype=np.float32), durations


class FakeG2P:
    """misaki's G2P: every word's sound is its letters (lowercase, those in VOCAB), punctuation as itself."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, text: str):
        self.calls += 1
        tokens = []
        for word in text.split():
            core = word.rstrip(",.")
            tokens.append(token(core, "".join(c for c in core.lower() if c in VOCAB) or None, space=word == core))
            if word != core:
                tokens.append(token(word[len(core):], word[len(core):]))
        return "", tokens


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class KokoroSpeakerTests(unittest.TestCase):
    def speaker(self) -> tuple[lw.KokoroSpeaker, FakeSession, list, list]:
        import numpy as np

        session, made, packs = FakeSession(), [], []

        def g2p(is_british):
            made.append(is_british)
            return FakeG2P()

        def pack(name):
            packs.append(name)
            return np.arange(510 * 256, dtype=np.float32).reshape(510, 1, 256)

        return lw.KokoroSpeaker(session, VOCAB, g2p, pack), session, made, packs

    def test_one_line_into_samples_and_word_times(self) -> None:
        speaker, session, _made, _packs = self.speaker()
        samples, words = speaker.speak("sat on, mat.", "bm_george", 1.1)
        feeds = session.feeds[0]
        ids = feeds["input_ids"][0].tolist()
        self.assertEqual((ids[0], ids[-1]), (0, 0))
        self.assertEqual(feeds["style"].shape, (1, 256))
        self.assertEqual(float(feeds["style"][0, 0]), float((len(ids) - 3) * 256))   # the row of n - 1 sounds
        self.assertAlmostEqual(float(feeds["speed"][0]), 1.1, places=5)
        self.assertEqual(session.names, ["waveform", "durations"])
        self.assertEqual(len(samples), (len(ids) + 2) * lw.SAMPLES_PER_UNIT)
        self.assertEqual([word for _ms, word in words], ["sat", "on", "mat"])
        self.assertEqual(words[0][0], 50)                               # after the leading pad's two units

    def test_the_accent_follows_the_voice_and_each_is_made_once(self) -> None:
        speaker, _session, made, packs = self.speaker()
        for voice in ("bm_george", "bf_emma", "bm_george", "am_adam", "am_adam"):
            speaker.speak("hm", voice, 1.0)
        self.assertEqual(made, [True, False])                           # British once, American once
        self.assertEqual(packs, ["bm_george", "bf_emma", "am_adam"])     # each voice's file read once

    def test_long_text_runs_in_several_inputs_with_times_running_on(self) -> None:
        speaker, session, _made, _packs = self.speaker()
        text = ("ham sat bad, " * 60).strip()
        _samples, words = speaker.speak(text, "bm_george", 1.0)
        self.assertGreater(len(session.feeds), 1)
        self.assertTrue(all(feeds["input_ids"].shape[1] <= lw.MAX_TOKENS + 2 for feeds in session.feeds))
        times = [ms for ms, _word in words]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(words), 180)

    def test_nothing_to_say_is_a_moment_of_quiet(self) -> None:
        speaker, session, _made, _packs = self.speaker()
        samples, words = speaker.speak("??? ...", "bm_george", 1.0)
        self.assertEqual((len(samples), words, session.feeds), (lv.SAMPLE_RATE // 10, [], []))


# --------------------------------------------------------------------------
# The worker's loop
# --------------------------------------------------------------------------

class FakeSpeaker:
    """100 ms of quiet sound a word; "boom" fails."""

    def __init__(self, delay: float = 0.0) -> None:
        self.prepared: list[str] = []
        self.spoken: list[tuple[str, float]] = []
        self.delay = delay

    def prepare(self, voice: str) -> None:
        self.prepared.append(voice)

    def speak(self, text: str, voice: str, speed: float):
        if self.delay:
            time.sleep(self.delay)
        if "boom" in text:
            raise RuntimeError(f"boom in {text}")
        self.spoken.append((voice, speed))
        words = text.split()
        return [0.1] * (lv.SAMPLE_RATE // 10 * len(words)), [(index * 100, word) for index, word in enumerate(words)]


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class HandleTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.speaker = FakeSpeaker()

    def ask(self, **fields) -> dict:
        request = {"id": 7, "text": "hello there", "voice": "bm_george", "speed": 1.2, "gain": 1.0,
                   "out": str(self.root / "a.wav")}
        request.update(fields)
        return lw.handle(request, self.speaker, self.root)

    def test_a_line_becomes_a_wav_with_its_words(self) -> None:
        answer = self.ask()
        self.assertEqual((answer["id"], answer["ok"], answer["duration_ms"]), (7, True, 200))
        self.assertEqual(answer["words"], [[0, "hello"], [100, "there"]])
        self.assertEqual(wav_ms(self.root / "a.wav"), 200)
        self.assertEqual(self.speaker.spoken, [("bm_george", 1.2)])

    def test_what_is_refused(self) -> None:
        self.assertEqual(self.ask(voice="xx_bad")["error"], lv.ERR_VOICE)
        self.assertEqual(self.ask(out=str(self.root.parent / "elsewhere.wav"))["error"], lv.ERR_PATH)
        self.assertEqual(self.ask(out=str(self.root / "a.txt"))["error"], lv.ERR_PATH)
        self.assertEqual(self.ask(text="x" * (lv.MAX_TEXT_CHARS + 1))["error"], lv.ERR_TOO_LONG)
        self.assertEqual(self.ask(text=None)["error"], lv.ERR_FAILED)
        self.assertEqual(self.speaker.spoken, [])

    def test_speed_and_gain_are_kept_in_range(self) -> None:
        self.ask(speed=9, gain="loud")
        self.assertEqual(self.speaker.spoken, [("bm_george", 2.0)])

    def test_a_failure_says_only_its_code(self) -> None:
        answer = self.ask(text=f"boom {SECRET}")
        self.assertEqual(answer, {"id": 7, "ok": False, "error": lv.ERR_FAILED})
        self.assertFalse((self.root / "a.wav").exists())


def serve_lines(stdin: bytes, load, root: Path, **kwargs) -> tuple[int, list[dict]]:
    out = io.StringIO()
    code = lw.serve(io.BytesIO(stdin), out, load, out_root=root, **kwargs)
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


def request_line(rid: int, text: str, root: Path, name: str = "a.wav", voice: str = "bm_george") -> bytes:
    return (json.dumps({"id": rid, "text": text, "voice": voice, "out": str(root / name)}) + "\n").encode()


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class ServeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_ready_then_answers_then_quit(self) -> None:
        speaker = FakeSpeaker()
        stdin = (request_line(1, "one two", self.root) + b"not json\n" + request_line(2, "three", self.root, "b.wav")
                 + b'{"quit": true}\n' + request_line(3, "never", self.root, "c.wav"))
        code, lines = serve_lines(stdin, lambda: speaker, self.root, warm_voice="bm_george")
        self.assertEqual(code, 0)
        self.assertTrue(lines[0]["ready"])
        self.assertEqual([(line["id"], line["duration_ms"]) for line in lines[1:]], [(1, 200), (2, 100)])
        self.assertEqual(speaker.prepared, ["bm_george"])
        self.assertFalse((self.root / "c.wav").exists())

    def test_the_end_of_its_input_ends_it(self) -> None:
        code, lines = serve_lines(request_line(1, "word", self.root), lambda: FakeSpeaker(), self.root)
        self.assertEqual((code, len(lines)), (0, 2))

    def test_loading_failures_say_their_code(self) -> None:
        def raiser(exc):
            def load():
                raise exc
            return load

        for exc, code in ((lw.AddonMissing("en_core_web_sm"), lv.ERR_ADDON), (ImportError("misaki"), lv.ERR_ADDON),
                          (lw.ModelMissing("kokoro"), lv.ERR_MODEL), (RuntimeError(SECRET), lv.ERR_FAILED)):
            status, lines = serve_lines(b"", raiser(exc), self.root)
            self.assertEqual((status, lines), (2, [{"ready": False, "error": code}]))

    def test_idle_ends_it(self) -> None:
        reader, writer = os.pipe()
        stream = os.fdopen(reader, "rb", buffering=0)
        code = lw.serve(stream, io.StringIO(), lambda: FakeSpeaker(), out_root=self.root, idle_s=0.2)
        os.close(writer)                  # the reader thread sees the end first (closing under it blocks on Windows)
        time.sleep(0.05)
        stream.close()
        self.assertEqual(code, 0)

    def test_main_sets_the_offline_switches_first(self) -> None:
        captured = {}

        def fake_serve(_inp, _out, _load, **kwargs):
            captured.update({key: os.environ.get(key) for key in lv.OFFLINE_ENV}, **kwargs)
            return 0

        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(lw, "serve", fake_serve), \
                mock.patch.object(lw, "answer_stream", io.StringIO):
            code = lw.main(["--model", "m", "--g2p-gb", "g", "--g2p-us", "u", "--out-root", str(self.root),
                            "--voice", "bm_george"])
        self.assertEqual(code, 0)
        self.assertEqual({key: captured[key] for key in lv.OFFLINE_ENV}, {key: "1" for key in lv.OFFLINE_ENV})
        self.assertEqual(captured["warm_voice"], "bm_george")


class SpacyRefusalTests(unittest.TestCase):
    def test_spacy_download_raises_instead_of_running_pip(self) -> None:
        cli = SimpleNamespace(download=lambda *a, **k: self.fail("pip ran"))
        fake_spacy = SimpleNamespace(cli=cli)
        with mock.patch.dict("sys.modules", {"spacy": fake_spacy, "spacy.cli": cli}):
            lw.refuse_spacy_download()
            with self.assertRaises(lw.AddonMissing):
                cli.download("en_core_web_sm")


# --------------------------------------------------------------------------
# The app's client
# --------------------------------------------------------------------------

class FakeProc:
    """lw.serve() on a thread, over real pipes, standing for the voice process (as tests/test_talk_speech.FakeProc
    does for the speech process)."""

    def __init__(self, load, root: Path, idle_s: float = 0.0, warm_voice: str = "") -> None:
        child_in_r, in_w = os.pipe()
        out_r, child_out_w = os.pipe()
        self.stdin = os.fdopen(in_w, "wb", buffering=0)
        self.stdout = os.fdopen(out_r, "rb")
        self._child_in = os.fdopen(child_in_r, "rb", buffering=0)
        child_out = os.fdopen(child_out_w, "w", encoding="utf-8", newline="\n")
        self.code: int | None = None

        def run() -> None:
            try:
                self.code = lw.serve(self._child_in, child_out, load, out_root=root, idle_s=idle_s,
                                     warm_voice=warm_voice)
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
        self.kill()
        time.sleep(0.05)
        for stream in (self.stdout, self._child_in):
            try:
                stream.close()
            except OSError:
                pass


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.models = self.root / "models"
        self.audio = self.root / "audio"
        self.audio.mkdir()
        make_models(self.models)

    def client(self, load=None, idle_s: float = 0.0, **kwargs) -> tuple[lv.LocalVoiceClient, list[FakeProc]]:
        procs: list[FakeProc] = []
        commands: list[list[str]] = []

        def spawn(command, environ):
            commands.append(command)
            self.environ = environ
            proc = FakeProc(load or FakeSpeaker, self.audio, idle_s=idle_s,
                            warm_voice=command[command.index("--voice") + 1])
            procs.append(proc)
            return proc

        kwargs.setdefault("addons", lambda: [])
        client = lv.LocalVoiceClient(self.models, self.audio, spawn=spawn, keep_warm_s=idle_s, **kwargs)
        self.commands = commands
        self.addCleanup(lambda: [proc.tidy() for proc in procs])
        self.addCleanup(client.close)
        return client, procs

    def test_it_starts_on_the_first_line_and_keeps_the_process(self) -> None:
        client, procs = self.client()
        first = client.synthesize("one two three", self.audio / "a.wav", speed=1.1)
        second = client.synthesize("four", self.audio / "b.wav", voice="bf_emma")
        self.assertEqual((first.duration_ms, second.duration_ms, len(procs)), (300, 100, 1))
        self.assertEqual(first.words, ((0, "one"), (100, "two"), (200, "three")))
        self.assertEqual(wav_ms(self.audio / "a.wav"), 300)
        self.assertTrue(client.running())
        command = self.commands[0]
        self.assertEqual(command[2:4], ["-m", "briefing_reader.local_voice_worker"])
        self.assertEqual(command[command.index("--voice") + 1], "bm_george")
        self.assertEqual({key: self.environ[key] for key in lv.OFFLINE_ENV}, lv.OFFLINE_ENV)
        self.assertNotIn("one two", repr(first))

    def test_his_lines_go_before_the_briefings(self) -> None:
        order: list[str] = []
        client, _procs = self.client(load=lambda: FakeSpeaker(delay=0.3))
        client.synthesize("warm", self.audio / "w.wav")

        def run(name: str, urgent: bool) -> None:
            client.synthesize(name, self.audio / f"{name}.wav", urgent=urgent)
            order.append(name)

        first = threading.Thread(target=run, args=("briefing1", False))
        first.start()
        time.sleep(0.1)                                    # briefing1 holds the process
        threads = [threading.Thread(target=run, args=("briefing2", False))]
        threads[0].start()
        time.sleep(0.05)
        threads.append(threading.Thread(target=run, args=("reply", True)))
        threads[1].start()
        for thread in [first, *threads]:
            thread.join(10)
        self.assertEqual(order, ["briefing1", "reply", "briefing2"])

    def test_a_process_that_ends_before_answering_is_started_again_once(self) -> None:
        loads = []

        class Dying(FakeSpeaker):
            def speak(self, text, voice, speed):
                raise SystemExit

        def load():
            loads.append(1)
            return Dying() if len(loads) == 1 else FakeSpeaker()

        client, procs = self.client(load=load)
        result = client.synthesize("still said", self.audio / "a.wav")
        self.assertEqual((result.duration_ms, len(procs)), (200, 2))

    def test_a_process_that_cannot_load_says_why_and_is_not_started_again(self) -> None:
        def missing():
            raise ImportError("misaki")

        client, procs = self.client(load=missing)
        with self.assertRaises(lv.LocalVoiceError) as caught:
            client.synthesize("hello", self.audio / "a.wav")
        self.assertEqual((caught.exception.code, len(procs)), (f"{lv.ERR_LOAD}:{lv.ERR_ADDON}", 1))
        self.assertEqual(client.reason(), f"{lv.ERR_LOAD}:{lv.ERR_ADDON}")
        for _ in range(3):                                 # every later line: the Windows voice, no new process
            with self.assertRaises(lv.LocalVoiceError):
                client.synthesize("again", self.audio / "b.wav")
        client.warm()
        time.sleep(0.1)
        self.assertEqual(len(procs), 1)
        client.forget_failure()                            # a download ended: it may try again
        with self.assertRaises(lv.LocalVoiceError):
            client.synthesize("once more", self.audio / "c.wav")
        self.assertEqual(len(procs), 2)

    def test_a_process_that_dies_before_ready_twice_is_given_up(self) -> None:
        def dies():
            raise SystemExit                               # ends before its ready line

        client, procs = self.client(load=dies)
        for expected in (lv.ERR_ENDED, lv.ERR_ENDED):
            with self.assertRaises(lv.LocalVoiceError) as caught:
                client.synthesize("hello", self.audio / "a.wav")
            self.assertEqual(caught.exception.code, expected)
        self.assertEqual(client.reason(), f"{lv.ERR_LOAD}:{lv.ERR_ENDED}")
        self.assertEqual(len(procs), 2)

    def test_his_line_waits_at_most_a_while_for_a_loading_voice(self) -> None:
        class SlowToLoad(FakeSpeaker):
            def prepare(self, voice: str) -> None:
                time.sleep(1.0)

        client, _procs = self.client(load=SlowToLoad)
        client.warm()
        time.sleep(0.1)                                    # the warm-up holds the voice while it loads
        began = time.monotonic()
        with mock.patch.object(lv, "URGENT_WAIT_S", 0.2), self.assertRaises(lv.LocalVoiceError) as caught:
            client.synthesize("good morning", self.audio / "a.wav", urgent=True)
        self.assertEqual(caught.exception.code, lv.ERR_BUSY)
        self.assertLess(time.monotonic() - began, 0.9)
        self.assertIn(lv.ERR_BUSY, lv.LINE_ERRORS)          # the Windows voice says that line; no cooldown
        self.assertEqual(client.synthesize("later", self.audio / "b.wav", urgent=True).duration_ms, 100)

    def test_a_close_while_it_starts_leaves_no_process(self) -> None:
        procs: list[FakeProc] = []
        holder: list[lv.LocalVoiceClient] = []

        def spawn(command, environ):
            proc = FakeProc(FakeSpeaker, self.audio)
            procs.append(proc)
            holder[0].close()                              # the app closes just as the process starts
            return proc

        client = lv.LocalVoiceClient(self.models, self.audio, spawn=spawn, addons=lambda: [])
        holder.append(client)
        self.addCleanup(lambda: [proc.tidy() for proc in procs])
        with self.assertRaises(lv.LocalVoiceError) as caught:
            client.synthesize("hello", self.audio / "a.wav")
        self.assertEqual(caught.exception.code, lv.ERR_CLOSED)
        procs[0].wait(5)
        self.assertFalse(procs[0].thread.is_alive())
        self.assertFalse(client.running())

    def test_no_answer_in_time_is_a_timeout_and_the_process_goes(self) -> None:
        client, _procs = self.client(load=lambda: FakeSpeaker(delay=0.6), answer_timeout_s=0.1)
        with self.assertRaises(lv.LocalVoiceError) as caught:
            client.synthesize("slow", self.audio / "a.wav")
        self.assertEqual(caught.exception.code, lv.ERR_TIMEOUT)
        self.assertFalse(client.running())

    def test_a_failed_line_says_its_code(self) -> None:
        client, _procs = self.client()
        with self.assertRaises(lv.LocalVoiceError) as caught:
            client.synthesize("boom", self.audio / "a.wav")
        self.assertEqual(caught.exception.code, lv.ERR_FAILED)
        self.assertEqual(client.synthesize("fine", self.audio / "b.wav").duration_ms, 100)

    def test_nothing_starts_when_it_cannot_run(self) -> None:
        client, procs = self.client(addons=lambda: ["misaki"])
        with self.assertRaises(lv.LocalVoiceError) as caught:
            client.synthesize("hello", self.audio / "a.wav")
        self.assertEqual((caught.exception.code, procs), (lv.ERR_ADDON, []))
        client.warm()
        time.sleep(0.1)
        self.assertEqual(procs, [])
        empty = lv.LocalVoiceClient(self.root / "nothing", self.audio, spawn=lambda *a: self.fail("started"),
                                    addons=lambda: [])
        with self.assertRaises(lv.LocalVoiceError) as caught:
            empty.synthesize("hello", self.audio / "a.wav")
        self.assertEqual(caught.exception.code, lv.ERR_MODEL)

    def test_warm_starts_it_in_the_background(self) -> None:
        client, procs = self.client()
        client.warm()
        for _ in range(100):
            if client.running():
                break
            time.sleep(0.02)
        self.assertTrue(client.running())
        self.assertEqual(len(procs), 1)
        client.synthesize("now", self.audio / "a.wav")
        self.assertEqual(len(procs), 1)

    def test_under_the_test_guard_no_real_process_starts(self) -> None:
        client = lv.LocalVoiceClient(self.models, self.audio, addons=lambda: [])
        with mock.patch.dict(os.environ, {lv.TEST_GUARD_ENV: "1"}), \
                mock.patch.object(lv.subprocess, "Popen", side_effect=AssertionError("a real process")):
            with self.assertRaises(lv.LocalVoiceError) as caught:
                client.synthesize("hello", self.audio / "a.wav")
        self.assertEqual(caught.exception.code, lv.ERR_REFUSED)

    def test_close_ends_the_process_and_later_lines_are_refused(self) -> None:
        client, procs = self.client()
        client.synthesize("one", self.audio / "a.wav")
        client.close()
        procs[0].wait(5)
        self.assertFalse(procs[0].thread.is_alive())
        with self.assertRaises(lv.LocalVoiceError) as caught:
            client.synthesize("two", self.audio / "b.wav")
        self.assertEqual(caught.exception.code, lv.ERR_CLOSED)

    def test_an_idle_process_is_started_again(self) -> None:
        client, procs = self.client(idle_s=0.2)
        client.synthesize("one", self.audio / "a.wav")
        procs[0].wait(5)
        self.assertFalse(client.running())
        self.assertEqual(client.synthesize("two", self.audio / "b.wav").duration_ms, 100)
        self.assertEqual(len(procs), 2)

    def test_the_words_never_reach_the_log(self) -> None:
        client, _procs = self.client()
        with self.assertLogs("briefing_reader", level="DEBUG") as captured:
            logging.getLogger("briefing_reader").debug("start")
            client.synthesize(SECRET, self.audio / "a.wav")
            with self.assertRaises(lv.LocalVoiceError):
                client.synthesize(f"boom {SECRET}", self.audio / "b.wav")
        joined = "\n".join(captured.output)
        for word in ("Ana", "Lima", "Ivy", "Dinner"):
            self.assertNotIn(word, joined)


if __name__ == "__main__":
    unittest.main()
