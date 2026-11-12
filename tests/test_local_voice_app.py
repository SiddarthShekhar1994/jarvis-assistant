"""Jarvis's voice made on this PC, in the app: the synthesizer's local path (tts.SpeechSynthesizer engine="local"),
the controller's wiring (one shared client, the factories, the chips), Settings > Voice and its Download. The local
voice is a fake that writes short tone WAVs; edge-tts and the Windows voice are stubs; nothing is played, downloaded
or started."""

from __future__ import annotations

import dataclasses
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtWidgets import QApplication

from briefing_reader import hud, local_voice as lv, settings_view, tts
from briefing_reader.local_voice import LocalResult, LocalVoiceError
from briefing_reader.tts import LOCAL, SAPI, SpeechSynthesizer
from tests.test_local_voice import make_models, tone_wav, wav_ms
from tests.test_tts import FakeClock, assert_sane_marks, entry, make_section, two_segment_section
from tests.ui_fakes import AppHarness, fonts, settle

SECRET = "Dinner with Ana Lima at the Ivy"
_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_local_voice_app", "-platform", "offscreen"])
    fonts()


class FakeLocal:
    """The app's LocalVoiceClient: 100 ms of tone a word, in a real WAV; ``reason`` and ``error`` set by a test."""

    def __init__(self, reason: str = "", error: BaseException | None = None) -> None:
        self.reason_code = reason
        self.error = error
        self.calls: list[tuple] = []
        self.warmed = 0
        self.closed = False

    def reason(self) -> str:
        return self.reason_code

    def synthesize(self, text, out, *, voice, speed, gain, urgent):
        self.calls.append((text, voice, speed, gain, urgent))
        if self.error is not None:
            raise self.error
        words = text.split()
        tone_wav(Path(out), 100 * len(words))
        return LocalResult(100 * len(words), tuple((index * 100, word) for index, word in enumerate(words)), ms=3)

    def warm(self) -> None:
        self.warmed += 1

    def forget_failure(self) -> None:
        self.forgotten = getattr(self, "forgotten", 0) + 1

    def close(self) -> None:
        self.closed = True


class StubSynth(SpeechSynthesizer):
    """The synthesizer with edge-tts and the Windows voice stubbed: edge fails the test if asked."""

    def __init__(self, out_dir: Path, clock: FakeClock, *, rate: str = "+0%", volume: str = "+0%", **kwargs) -> None:
        super().__init__(voice="en-GB-RyanNeural", rate=rate, volume=volume, offline_voice="", out_dir=out_dir,
                         clock=clock, **kwargs)
        self.edge_calls: list[str] = []
        self.sapi_calls: list[str] = []

    def _edge_to_file(self, text, path):
        self.edge_calls.append(text)
        path.write_bytes(b"mp3")
        return 1000, ((0, "x"),)

    def _sapi_to_file(self, text, path):
        self.sapi_calls.append(text)
        path.write_bytes(b"wav")
        return 1000 + len(text)


class LocalSynthTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name)
        self.clock = FakeClock()

    def make(self, local, **kwargs) -> StubSynth:
        kwargs.setdefault("engine", LOCAL)
        return StubSynth(self.out, self.clock, local=local, **kwargs)

    def test_a_section_is_made_by_the_local_voice(self) -> None:
        local = FakeLocal()
        synth = self.make(local)
        audio = synth.synthesize_section(two_segment_section())
        self.assertEqual(audio.engine, LOCAL)
        self.assertEqual(len(audio.segments), 2)
        self.assertEqual((synth.edge_calls, synth.sapi_calls), ([], []))
        first = audio.segments[0]
        self.assertEqual(first.path.suffix, ".wav")
        self.assertEqual(wav_ms(first.path), first.duration_ms)
        assert_sane_marks(self, first.marks, [0, 1], first.duration_ms)
        self.assertGreater(first.marks[1][0], 0)                       # item 2 starts at its first word
        self.assertTrue(first.words and first.envelope)
        self.assertEqual(sorted(path.name for path in self.out.iterdir() if "-" in path.stem), [])   # parts gone
        self.assertEqual(synth.last_engine, LOCAL)

    def test_long_text_goes_in_groups_and_the_word_times_run_on(self) -> None:
        local = FakeLocal()
        synth = self.make(local)
        text = " ".join(f"Sentence {index} has six words here." for index in range(12))
        audio = synth.synthesize_section(make_section(entry(text)))
        self.assertGreater(len(local.calls), 1)
        segment = audio.segments[0]
        starts = [start for start, _end, _word in segment.words]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(len(segment.words), 72)
        self.assertEqual(segment.duration_ms, 7200)
        self.assertEqual(wav_ms(segment.path), 7200)

    def test_one_line_s_error_is_the_windows_voice_for_that_line_only(self) -> None:
        for code in (lv.ERR_TOO_LONG, lv.ERR_FAILED, lv.ERR_BUSY):
            with self.subTest(code=code):
                local = FakeLocal(error=LocalVoiceError(code))
                synth = self.make(local)
                self.assertEqual(synth.synthesize_section(make_section(entry("One."))).engine, SAPI)
                local.error = None
                audio = synth.synthesize_section(make_section(entry("Two."), key="s2"))
                self.assertEqual((audio.engine, len(local.calls), synth.edge_calls), (LOCAL, 2, []))   # no cooldown

    def test_a_closing_app_makes_nothing_more(self) -> None:
        local = FakeLocal(error=LocalVoiceError(lv.ERR_CLOSED))
        synth = self.make(local)
        with self.assertRaises(tts.TtsError):
            synth.synthesize_section(make_section(entry("Late line.")))
        self.assertEqual((synth.sapi_calls, synth.edge_calls), ([], []))

    def test_it_never_uses_the_online_voice_even_when_it_fails(self) -> None:
        local = FakeLocal(error=LocalVoiceError(lv.ERR_ENDED))
        synth = self.make(local)
        with self.assertLogs("briefing_reader.tts", level="WARNING") as captured:
            audio = synth.synthesize_section(make_section(entry(SECRET)))
        self.assertEqual((audio.engine, synth.edge_calls, len(synth.sapi_calls)), (SAPI, [], 1))
        self.assertNotIn("Ana", "\n".join(captured.output))
        synth.synthesize_section(make_section(entry("Second line."), key="s2"))
        self.assertEqual(len(local.calls), 1)                          # the cooldown: the Windows voice meanwhile
        self.clock.now += 61
        local.error = None
        audio = synth.synthesize_section(make_section(entry("Third line."), key="s3"))
        self.assertEqual((audio.engine, len(local.calls), synth.edge_calls), (LOCAL, 2, []))

    def test_when_it_cannot_run_the_windows_voice_speaks_without_asking(self) -> None:
        local = FakeLocal(reason=lv.ERR_MODEL)
        synth = self.make(local)
        with self.assertLogs("briefing_reader.tts", level="INFO") as captured:
            synth.synthesize_section(make_section(entry("One.")))
            synth.synthesize_section(make_section(entry("Two."), key="s2"))
        self.assertEqual((local.calls, synth.edge_calls, len(synth.sapi_calls)), ([], [], 2))
        self.assertEqual(sum("can't run yet" in line for line in captured.output), 1)
        none = self.make(None)
        self.assertEqual(none.synthesize_section(make_section(entry("Three."), key="s3")).engine, SAPI)
        self.assertEqual(none.edge_calls, [])

    def test_a_line_said_again_comes_from_the_cache(self) -> None:
        local = FakeLocal()
        synth = self.make(local)
        synth.synthesize_section(make_section(entry("Good morning, sir.")))
        synth.synthesize_section(make_section(entry("Good morning, sir."), key="again"))
        self.assertEqual(len(local.calls), 1)

    def test_his_lines_are_urgent_and_speed_and_volume_reach_the_voice(self) -> None:
        local = FakeLocal()
        synth = self.make(local, rate="+20%", volume="-50%", urgent=True, local_voice="bf_emma")
        synth.synthesize_section(make_section(entry("Hello.")))
        self.assertEqual(local.calls, [("Hello.", "bf_emma", 1.2, 0.5, True)])

    def test_the_online_mode_is_unchanged(self) -> None:
        local = FakeLocal()
        synth = StubSynth(self.out, self.clock, local=local)        # engine defaults to online
        audio = synth.synthesize_section(make_section(entry("Hello there.")))
        self.assertEqual((audio.engine, local.calls, len(synth.edge_calls)), (tts.EDGE, [], 1))


class FakeSapiEngine:
    def __init__(self, names: list[str], current: int = 0) -> None:
        self.voices = [SimpleNamespace(id=f"id{index}", name=name) for index, name in enumerate(names)]
        self.voice = self.voices[current].id if self.voices else None

    def getProperty(self, name):  # noqa: N802 - pyttsx3's names
        return self.voices if name == "voices" else self.voice

    def setProperty(self, name, value):  # noqa: N802
        if name == "voice":
            self.voice = value


class WindowsVoiceTests(unittest.TestCase):
    def test_with_the_local_voice_an_online_windows_voice_is_never_used(self) -> None:
        engine = FakeSapiEngine(["Microsoft Aria Online (Natural)", "Microsoft David Desktop"])
        SpeechSynthesizer._keep_sapi_on_this_pc(engine)
        self.assertEqual(engine.voice, "id1")
        local_only = FakeSapiEngine(["Microsoft Zira Desktop"])
        SpeechSynthesizer._keep_sapi_on_this_pc(local_only)
        self.assertEqual(local_only.voice, "id0")
        with self.assertRaises(tts.TtsError):
            SpeechSynthesizer._keep_sapi_on_this_pc(FakeSapiEngine(["Microsoft Guy Online (Natural)"]))

    def test_a_named_online_voice_is_passed_over_in_local_mode(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            synth = SpeechSynthesizer(voice="v", rate="+0%", volume="+0%", offline_voice="Aria", out_dir=Path(tmp),
                                      engine=LOCAL)
            engine = FakeSapiEngine(["Microsoft Aria Online (Natural)", "Microsoft David Desktop"])
            synth._configure_sapi(engine)
            self.assertEqual(engine.voice, "id1")
            online = SpeechSynthesizer(voice="v", rate="+0%", volume="+0%", offline_voice="Aria", out_dir=Path(tmp))
            engine = FakeSapiEngine(["Microsoft David Desktop", "Microsoft Aria Online (Natural)"])
            online._configure_sapi(engine)
            self.assertEqual(engine.voice, "id1")          # the online mode keeps the owner's pick, as before


class SettingsRowsTests(unittest.TestCase):
    def config(self, engine: str) -> SimpleNamespace:
        voice = SimpleNamespace(engine=engine, local_voice="bm_george", voice="en-GB-RyanNeural", rate="+0%",
                                volume="+0%", offline_voice="")
        return SimpleNamespace(voice=voice, assistant=SimpleNamespace(speak=True, speak_replies=True,
                                                                      speak_results=True, greet=True, address="sir"))

    def test_the_local_voice_rows(self) -> None:
        rows = dict(settings_view.settings_rows(self.config("local"), "voice"))
        self.assertEqual(rows["Made"], "On this PC - nothing he says leaves it")
        self.assertEqual(rows["Voice"], "George (British)")
        self.assertEqual(rows["Lines that may be private"], "Said aloud (the voice is on this PC)")
        self.assertEqual(rows["When that voice can't run"], "Windows' default voice (also on this PC)")
        self.assertEqual(rows["Speed"], "Normal")

    def test_the_online_voice_rows(self) -> None:
        rows = dict(settings_view.settings_rows(self.config("online"), "voice"))
        self.assertIn("Microsoft", rows["Made"])
        self.assertEqual(rows["Voice"], "en-GB-RyanNeural")
        self.assertEqual(rows["Lines that may be private"], "Kept on screen (the online voice would hear them)")
        self.assertIn("When that voice is out of reach", rows)


class FakeDownloader:
    def __init__(self, root: Path, model, progress, cancel, error: str = "", log: list | None = None) -> None:
        self.root, self.model, self.progress, self.error = root, model, progress, error
        self.log = log if log is not None else []

    def run(self) -> str:
        self.log.append(self.model.name)
        self.progress(5, 10)
        if self.error:
            return self.error
        make_models(self.root) if self.model is lv.MODELS[-1] else None
        return ""


class VoiceDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def make(self, error: str = "", addons=lambda: []):
        from briefing_reader import local_voice_ui

        log, ready = [], []
        download = local_voice_ui.VoiceDownload(
            self.root, on_ready=lambda: ready.append(1), threaded=False, addons=addons,
            downloader_factory=lambda root, model, progress, cancel: FakeDownloader(root, model, progress, cancel,
                                                                                     error, log))
        panel = local_voice_ui.VoiceSettingsPanel()
        self.addCleanup(panel.deleteLater)
        download.attach_panel(panel)
        return download, panel, log, ready

    def test_download_fetches_each_model_then_the_voice_warms(self) -> None:
        download, panel, log, ready = self.make()
        self.assertIn("downloaded only when you click Download", panel.status.text())
        self.assertFalse(panel.download.isHidden())
        download.download()
        self.assertEqual(log, [model.name for model in lv.MODELS])
        self.assertEqual(ready, [1])
        self.assertTrue(download.ready())
        self.assertIn("George (British)", panel.status.text())
        self.assertTrue(panel.download.isHidden())
        download.download()                                    # whole already: nothing more
        self.assertEqual(len(log), 3)

    def test_a_stopped_download_says_so_and_carries_on_at_the_next_click(self) -> None:
        download, panel, log, ready = self.make(error="network")
        download.download()
        self.assertEqual((log, ready), (["kokoro-82m"], []))
        self.assertIn("The download stopped (network)", panel.status.text())
        self.assertFalse(download.downloading())

    def test_the_panel_never_says_on_this_pc_while_it_cannot_run(self) -> None:
        from briefing_reader import local_voice_ui

        make_models(self.root)
        for reason, words in ((lv.ERR_ADDON, "its add-ons are missing"),
                              (f"{lv.ERR_LOAD}:{lv.ERR_FAILED}", "couldn't start (load_failed:failed)"),
                              ("", "The local voice is on this PC: George (British)")):
            with self.subTest(reason=reason):
                download = local_voice_ui.VoiceDownload(self.root, threaded=False, addons=lambda: [],
                                                        reason=lambda reason=reason: reason)
                panel = local_voice_ui.VoiceSettingsPanel()
                self.addCleanup(panel.deleteLater)
                download.attach_panel(panel)
                self.assertIn(words, panel.status.text())
                self.assertEqual("on this PC: George" in panel.status.text(), reason == "")

    def test_missing_addons_show_the_owner_lines(self) -> None:
        _download, panel, _log, _ready = self.make(addons=lambda: ["spacy", "misaki"])
        self.assertFalse(panel.pip.isHidden())
        self.assertIn("--ignore-requires-python", panel.pip.text())
        self.assertNotIn("@", panel.pip.text())
        _download, panel, _log, _ready = self.make()
        self.assertTrue(panel.pip.isHidden())


class AppVoiceTests(unittest.TestCase):
    def make(self, extra: str = "") -> AppHarness:
        harness = AppHarness(self, config_extra=extra)
        return harness

    def test_the_local_voice_is_one_client_shared_by_both_speech_threads(self) -> None:
        harness = self.make()
        c = harness.c
        self.assertEqual(c.config.voice.engine, "local")
        client = c._ensure_local_voice()
        self.assertIs(c._ensure_local_voice(), client)
        say = c._synth_factory(harness.root / "say", urgent=True)()
        briefing = c._synth_factory(harness.root, urgent=False)()
        self.assertIs(say._local, client)
        self.assertIs(briefing._local, client)
        self.assertEqual((say._urgent, briefing._urgent, say._local_mode), (True, False, True))
        self.assertEqual(client.voice, "bm_george")
        self.assertFalse(client.running())                     # nothing starts: not downloaded, and the test guard

    def test_the_online_voice_makes_no_client(self) -> None:
        harness = self.make('\n[voice]\nengine = "online"\n')
        c = harness.c
        self.assertIsNone(c._ensure_local_voice())
        self.assertIsNone(c.voice_download)
        synth = c._synth_factory(harness.root, urgent=False)()
        self.assertFalse(synth._local_mode)

    def test_the_voice_chip_says_where_he_is_made(self) -> None:
        c = self.make().c
        c._engine = LOCAL
        self.assertEqual(c._voice_chip(), (hud.STATUS_OK, "Voice: on this PC - George (British)"))
        c._audio = {0: SimpleNamespace(engine=SAPI, segments=(1,))}
        status, text = c._voice_chip()
        self.assertEqual(status, hud.STATUS_WARN)
        self.assertIn("the local voice can't run now", text)

    def test_settings_voice_shows_its_part_and_a_download_warms_the_voice(self) -> None:
        harness = self.make()
        c = harness.c
        stage = c.window.reading.settings_page
        stage.select_group("voice")
        settle()
        rows = dict(stage.rows())
        self.assertEqual(rows["Made"], "On this PC - nothing he says leaves it")
        panel = stage._extras["voice"]
        self.assertTrue(panel.isVisibleTo(stage))
        stage.select_group("times")
        self.assertFalse(panel.isVisibleTo(stage))
        fake = FakeLocal()
        c._local_client = fake
        c._local_voice_downloaded()
        self.assertEqual((fake.warmed, fake.forgotten), (1, 1))     # a failed load before it is forgotten

    def test_start_warms_the_voice_and_shutdown_ends_it(self) -> None:
        harness = self.make()
        c = harness.c
        fake = FakeLocal()
        c._start_tts = lambda: None
        c._ensure_local_voice = lambda: fake
        c._local_client = fake
        with mock.patch.object(c, "_start_calendar", lambda: None):
            c.start()
        self.assertEqual(fake.warmed, 1)
        c.shutdown()
        for _ in range(50):
            if fake.closed:
                break
            settle(1)
        self.assertTrue(fake.closed)
        self.assertIsNone(c._local_client)


if __name__ == "__main__":
    unittest.main()
