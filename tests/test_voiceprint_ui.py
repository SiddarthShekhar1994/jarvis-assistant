"""The voiceprint in the app (voiceprint_ui): Settings > Talk's "Recognise my voice" and its states, the Download (only on
a click), the recording of three takes (asked again, cancelled, disagreeing, saved), the switch (settings.json) and
Forget, the check of a wake on a fake model, and the wake gate in WakeController (a mismatch opens no talk and says so;
a match or an unchecked wake goes on as before), plus [wake] only_my_voice and the app's hooks. Every model session,
microphone, listener and download is a stand-in (offscreen Qt): nothing is played, recorded, downloaded or sent."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import config as cfg
from briefing_reader import canvas_ui, meeting_audio, overrides, talk_ui, ui, voiceprint, voiceprint_ui, wake_ui
from briefing_reader.board import protocol
from briefing_reader.voiceprint_ui import VoicePanel, VoiceprintController
from briefing_reader.wake_ui import WakeController, WakeStore
from tests.test_voiceprint import room, tone, unit
from tests.test_wake_ui import MIC, FakeClient, FakeTalk, audio_line, ready_model

APP = QApplication.instance() or QApplication([])


def ready_speaker_model(root: Path) -> Path:
    """The pinned file's size and a checked manifest (its bytes do not matter to model_ready)."""
    folder = voiceprint.model_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / voiceprint.MODEL_FILE.name, "wb") as fh:
        fh.truncate(voiceprint.MODEL_FILE.size)
    manifest = {"files": {voiceprint.MODEL_FILE.name: voiceprint.MODEL_FILE.size},
                "sha256": {voiceprint.MODEL_FILE.name: voiceprint.MODEL_FILE.sha256}}
    (folder / voiceprint.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return folder


class FakeSession:
    """A fake onnxruntime session: each run gives the next of ``outputs`` (the last again when they run out)."""

    def __init__(self, outputs) -> None:
        self.outputs = list(outputs)
        self.runs = 0
        self.frames: list[int] = []                  # each run's feature frames (T)

    def get_inputs(self):
        return [SimpleNamespace(name="feats")]

    def run(self, _outputs, feeds):
        assert feeds["feats"].shape[2] == 80
        self.frames.append(int(feeds["feats"].shape[1]))
        value = self.outputs[min(self.runs, len(self.outputs) - 1)]
        self.runs += 1
        if isinstance(value, Exception):
            raise value
        return [np.asarray(value, dtype=np.float32).reshape(1, -1)]


class FakeStream:
    """A microphone that gives ``pcm``, then the room as long as it is read (all zero after an all-zero ``pcm``)."""

    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.filler = bytes(3200) if not any(pcm) else room(0.1, seed=5)
        self.closed = False

    def read_available(self) -> bytes:
        piece, self.pcm = self.pcm[:3200], self.pcm[3200:]
        return piece or self.filler

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    """A device library whose microphone gives the next of ``takes`` (16 kHz mono int16), then quiet."""

    def __init__(self, takes) -> None:
        self.takes = list(takes)
        self.opened: list[str] = []
        self.streams: list[FakeStream] = []
        self.closes = 0

    def inputs(self):
        return [meeting_audio.DeviceInfo("1", MIC, True)]

    def default_input(self):
        return self.inputs()[0]

    def open_mic(self, device):
        self.opened.append(device.name)
        pcm = self.takes.pop(0) if self.takes else room(9.0)
        stream = FakeStream(pcm)
        self.streams.append(stream)
        return stream

    def close(self) -> None:
        self.closes += 1


GOOD_TAKE = room(0.3) + tone(1.0) + room(0.2, seed=2)


class FakeSettings:
    def __init__(self, fail: bool = False) -> None:
        self.values: dict = {}
        self.fail = fail
        self.resets: list = []

    def update(self, changes):
        if self.fail:
            return False
        self.values.update(changes)
        return True

    def reset(self, keys):
        self.resets.append(list(keys))
        for key in keys:
            self.values.pop(key, None)
        return True


class Case(unittest.TestCase):
    def make(self, *, model: bool = True, runtime: str = "", outputs=None, takes=(), enrolled: bool = False,
             settings=None, loader_error: Exception | None = None, config=None, busy=None, threaded=False,
             start_work=None) -> VoiceprintController:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        if model:
            ready_speaker_model(self.root / "models")
        if enrolled:
            voiceprint.TemplateStore(self.data).save(voiceprint.Template.from_embeddings([unit(0)] * 3))
        self.session = FakeSession(outputs or [unit(0)])
        self.loads = 0

        self.loader_error = loader_error

        def loader(folder):
            self.loads += 1
            self.assertEqual(folder, voiceprint.model_dir(self.root / "models"))
            if self.loader_error is not None:
                raise self.loader_error
            return self.session

        self.backend = FakeBackend(takes)
        self.busy = [""] if busy is None else busy
        self.settings = settings or FakeSettings()
        self.downloads: list = []

        def downloader(models_root, progress, cancel):
            self.downloads.append(models_root)

            class Done:
                def run(self_inner):
                    ready_speaker_model(models_root)
                    progress(5, 10)
                    return ""
            return Done()

        controller = VoiceprintController(
            config or SimpleNamespace(data_dir=self.data, assistant=SimpleNamespace(address="sir")),
            data_dir=self.data, models_root=self.root / "models", settings=self.settings, session_loader=loader,
            runtime_missing=lambda: runtime, downloader_factory=downloader,
            backend_loader=lambda: (self.backend, ""), picked_mic=lambda: "", busy=lambda: self.busy[0],
            threaded=threaded, start_work=start_work)
        self.recordings: list[bool] = []
        controller.recordingChanged.connect(self.recordings.append)
        self.addCleanup(controller.shutdown)
        return controller

    @staticmethod
    def run_takes(controller: VoiceprintController, limit: int = 4000) -> None:
        """Polls the microphone (and lets the gaps between takes pass) until the recording ends."""
        for _ in range(limit):
            if not controller.recording():
                return
            if controller._gap.isActive():
                controller._gap.stop()
                controller._open_take()
            else:
                controller._poll()
        raise AssertionError("the recording never ended")


class PanelTests(Case):
    def panel_for(self, controller) -> VoicePanel:
        panel = VoicePanel()
        self.refreshes = []
        controller.attach_panel(panel, refresh=lambda: self.refreshes.append(1))
        return panel

    def test_no_model_shows_download_with_its_size_and_credit(self) -> None:
        controller = self.make(model=False)
        panel = self.panel_for(controller)
        self.assertFalse(panel.download.isHidden())
        self.assertIn("26.5 MB", panel.download.text())
        self.assertIn(voiceprint.CREDIT, panel.credit.text())
        for button in (panel.record, panel.forget, panel.switch, panel.cancel):
            self.assertTrue(button.isHidden())
        self.assertIn("isn't downloaded", panel.status.text())
        controller.download()                                          # only on the owner's click
        self.assertEqual(self.downloads, [self.root / "models"])
        self.assertTrue(panel.download.isHidden())
        self.assertEqual(panel.record.text(), "Record my voice")
        self.assertFalse(panel.record.isHidden())

    def test_downloaded_not_enrolled_shows_record_and_what_is_kept(self) -> None:
        panel = self.panel_for(self.make())
        self.assertFalse(panel.record.isHidden())
        self.assertTrue(panel.forget.isHidden())
        self.assertTrue(panel.switch.isHidden())
        self.assertEqual(panel.keeps.text(), "Jarvis keeps a voice template on this PC (numbers, not a recording). "
                                             "Forget deletes it.")
        self.assertFalse(panel.keeps.isHidden())

    def test_enrolled_shows_record_again_forget_and_the_switch_on(self) -> None:
        controller = self.make(enrolled=True)
        panel = self.panel_for(controller)
        self.assertEqual(panel.record.text(), "Record again")
        self.assertFalse(panel.forget.isHidden())
        self.assertFalse(panel.switch.isHidden())
        self.assertTrue(panel.switch.isChecked())
        self.assertIn("Only my voice wakes Jarvis", panel.switch.text())
        panel.switch.click()                                            # the switch's click turns it off
        self.assertFalse(controller.only_mine())
        self.assertEqual(self.settings.values, {"wake.only_my_voice": False})
        self.assertFalse(panel.switch.isChecked())
        self.assertEqual(len(self.refreshes), 1)                         # Settings > Talk's rows shown again
        panel.forget.click()
        self.assertEqual(len(self.refreshes), 2)
        self.assertFalse(controller.enrolled())
        self.assertFalse((self.data / voiceprint.VOICEPRINT_FILE).exists())
        self.assertEqual(panel.record.text(), "Record my voice")

    def test_without_the_runtime_record_is_not_offered(self) -> None:
        panel = self.panel_for(self.make(runtime="onnxruntime"))
        self.assertTrue(panel.record.isHidden())
        self.assertIn("onnxruntime", panel.status.text())


class RecordingTests(Case):
    def test_three_good_takes_save_a_template_and_no_audio(self) -> None:
        controller = self.make(takes=[GOOD_TAKE] * 3, outputs=[unit(0), unit(0, 1, 0.2), unit(0, 2, 0.2)])
        controller.set_only_mine(False)
        controller.start_recording()
        self.assertEqual(self.recordings, [True])                       # "Hey Jarvis" pauses meanwhile
        self.assertIn("Say “Hey Jarvis”, sir (1 of 3)", controller.status_text())
        self.run_takes(controller)
        self.assertEqual(self.recordings, [True, False])
        self.assertEqual(len(self.backend.opened), 3)                   # each take through the talk's mic path
        self.assertTrue(all(stream.closed for stream in self.backend.streams))
        self.assertEqual(self.session.runs, 3)
        self.assertTrue(controller.enrolled())
        self.assertTrue(controller.only_mine())                          # on after a recording
        self.assertEqual(self.settings.resets, [["wake.only_my_voice"]])
        self.assertEqual(sorted(path.name for path in self.data.iterdir()), [voiceprint.VOICEPRINT_FILE])
        data = json.loads((self.data / voiceprint.VOICEPRINT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"version", "created", "samples", "embeddings", "mean"})
        self.assertEqual(data["samples"], 3)
        self.assertIn("Done", controller.status_text())
        self.assertTrue(controller.will_check())

    def test_a_short_quiet_or_silent_take_is_asked_again(self) -> None:
        short = room(0.3) + tone(0.4) + room(0.2)
        long = room(0.3) + tone(6.0)
        controller = self.make(takes=[short, bytes(64000), long, GOOD_TAKE, GOOD_TAKE, GOOD_TAKE])
        controller.start_recording()
        texts = []
        for _ in range(4000):
            if not controller.recording():
                break
            if controller._gap.isActive():
                texts.append(controller.status_text())
                controller._gap.stop()
                controller._open_take()
            else:
                controller._poll()
        self.assertIn("That was too short. Say “Hey Jarvis”, sir (1 of 3).", texts)
        self.assertTrue(any("Nothing came from the microphone" in text for text in texts))
        self.assertTrue(any("That was too long" in text for text in texts))
        self.assertEqual(len(self.backend.opened), 6)
        self.assertTrue(controller.enrolled())

    def test_takes_that_disagree_keep_nothing(self) -> None:
        controller = self.make(takes=[GOOD_TAKE] * 3, outputs=[unit(0), unit(0), unit(7)])
        controller.start_recording()
        self.run_takes(controller)
        self.assertFalse(controller.enrolled())
        self.assertFalse((self.data / voiceprint.VOICEPRINT_FILE).exists())
        self.assertIn("Those didn't match each other. Press Record my voice to try again, sir.",
                      controller.status_text())

    def test_cancel_keeps_nothing_and_listening_comes_back(self) -> None:
        controller = self.make(takes=[GOOD_TAKE] * 3)
        controller.start_recording()
        for _ in range(40):
            controller._poll()
        controller.cancel_recording()
        self.assertFalse(controller.recording())
        self.assertEqual(self.recordings, [True, False])
        self.assertTrue(self.backend.streams[-1].closed)
        self.assertFalse(controller.enrolled())
        self.assertEqual(list(self.data.iterdir()), [])
        self.assertIn("Nothing was kept", controller.status_text())

    def test_too_many_failed_takes_stop_it(self) -> None:
        controller = self.make(takes=[bytes(64000)] * 10)
        controller.start_recording()
        self.run_takes(controller)
        self.assertEqual(len(self.backend.opened), voiceprint_ui.MAX_TRIES)
        self.assertFalse(controller.enrolled())
        self.assertIn("nothing was kept", controller.status_text())

    def test_not_while_a_talk_listens_nor_without_the_model(self) -> None:
        controller = self.make(busy=[voiceprint_ui.BUSY_TALK])
        controller.start_recording()
        self.assertFalse(controller.recording())
        self.assertEqual(self.backend.opened, [])
        self.assertIn("Finish the talk first", controller.status_text())
        controller = self.make(model=False)
        controller.start_recording()
        self.assertEqual(self.backend.opened, [])


class SwitchAndCheckTests(Case):
    def test_the_switch_saves_and_an_unsaved_off_stays_on(self) -> None:
        controller = self.make(enrolled=True, settings=FakeSettings(fail=True))
        controller.set_only_mine(False)
        self.assertTrue(controller.only_mine())                          # fail-closed
        self.assertIn("couldn't be saved", controller.status_text())
        controller = self.make(enrolled=True)
        controller.set_only_mine(False)
        self.assertEqual(controller.off_reason(), voiceprint_ui.OFF_SWITCH)
        self.assertIn("you turned Only my voice off", dict(controller.settings_rows())["Recognise my voice"])

    def test_the_switch_starts_from_the_config(self) -> None:
        config = SimpleNamespace(data_dir=None, wake=cfg.WakeConfig(only_my_voice=False))
        controller = self.make(enrolled=True, config=config)
        self.assertFalse(controller.only_mine())
        self.assertFalse(controller.will_check())

    def test_why_the_check_is_off(self) -> None:
        cases = {voiceprint.WHY_MODEL: dict(model=False, enrolled=True),
                 voiceprint.WHY_RUNTIME: dict(runtime="onnxruntime", enrolled=True),
                 voiceprint.WHY_NO_TEMPLATE: dict()}
        for why, kwargs in cases.items():
            with self.subTest(why):
                controller = self.make(**kwargs)
                self.assertEqual(controller.off_reason(), why)
                self.assertFalse(controller.will_check())
                self.assertTrue(dict(controller.settings_rows())["Recognise my voice"].startswith("off: "))
        controller = self.make(enrolled=True)
        self.assertTrue(controller.will_check())
        self.assertEqual(dict(controller.settings_rows())["Speaker model"], f"{voiceprint.CREDIT}: on this PC")

    def test_a_check_on_the_fake_model(self) -> None:
        controller = self.make(enrolled=True, outputs=[unit(0, 1, 0.3), unit(5)])
        verdicts = []
        clip = room(0.3) + tone(1.1) + room(0.2)
        with self.assertLogs("briefing_reader.voiceprint_ui", level="INFO") as logs:
            controller.check(clip, verdicts.append)
            controller.check(clip, verdicts.append)
            controller.check(room(0.5) + tone(0.4) + room(0.7), verdicts.append)
        self.assertEqual([verdict.kind for verdict in verdicts], [voiceprint.MATCH, voiceprint.MISMATCH,
                                                                  voiceprint.UNCHECKED])
        self.assertEqual(verdicts[2].reason, voiceprint.WHY_SHORT)
        self.assertEqual(self.loads, 1)                                  # loaded once, kept
        lines = [line.split(":", 2)[2] for line in logs.output]
        self.assertEqual(sum(1 for line in lines if line.startswith("Voiceprint: embedded in ")), 2)
        self.assertIn("Voiceprint: unchecked (short)", lines)
        self.assertIn("too short to check", controller.status_text())

    def test_a_model_that_will_not_load_lets_wakes_through_and_says_so(self) -> None:
        controller = self.make(enrolled=True, loader_error=voiceprint.VoiceprintError(voiceprint.ERR_FAILED))
        verdicts = []
        controller.check(room(0.3) + tone(1.1), verdicts.append)
        self.assertEqual((verdicts[0].kind, verdicts[0].reason), (voiceprint.UNCHECKED, voiceprint.WHY_FAILED))
        self.assertEqual(controller.off_reason(), voiceprint.WHY_FAILED)
        self.assertIn("wouldn't load", dict(controller.settings_rows())["Recognise my voice"])

    def test_a_bad_template_file_is_not_enrolled(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data = Path(tmp.name)
        (data / voiceprint.VOICEPRINT_FILE).write_text('{"version": 9}', encoding="utf-8")
        ready_speaker_model(data)
        with self.assertLogs("briefing_reader.voiceprint", level="WARNING"):
            controller = VoiceprintController(SimpleNamespace(data_dir=data), data_dir=data, models_root=data,
                                              settings=FakeSettings(), threaded=False)
        self.addCleanup(controller.shutdown)
        self.assertFalse(controller.enrolled())
        self.assertIn("could not be used", dict(controller.settings_rows())["Recognise my voice"])


class FakeGate:
    """The voiceprint as WakeController sees it: ``will_check`` and ``check(pcm, done)``, answered later."""

    def __init__(self, on: bool = True) -> None:
        self.on = on
        self.asked: list = []

    def will_check(self) -> bool:
        return self.on

    def check(self, pcm, done) -> None:
        self.asked.append((pcm, done))

    def settings_rows(self):
        return [("Recognise my voice", "on")]


class WakeHarness:
    def started(self, gate) -> WakeController:
        """A WakeController listening on a fake listener, with ``gate`` as its voiceprint."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ready_model(root / "models")
        store = WakeStore(root)
        store.set_on(True)
        self.talk = FakeTalk()
        self.clients: list[FakeClient] = []
        self.clock = [100.0]

        def factory(folder, on_line, sensitivity):
            client = FakeClient(folder, on_line, sensitivity)
            self.clients.append(client)
            return client

        controller = WakeController(SimpleNamespace(data_dir=root), self.talk, holds=lambda: [],
                                    picked_mic=lambda: "", store=store, models_root=root / "models",
                                    client_factory=factory, runtime_missing=lambda: "", threaded=False,
                                    clock=lambda: self.clock[0], voiceprint=gate)
        self.addCleanup(controller.shutdown)
        controller.start()
        self.client = self.clients[-1]
        self.client.on_line({"ready": True, "load_ms": 250})
        self.client.on_line({"state": "listening", "mic": MIC, "default": True})
        return controller

    def heard(self) -> None:
        self.client.on_line({"heard": True, "score": 0.97, "speaking": False, "preroll_bytes": 3200})
        self.client.on_line(audio_line(3200))
        self.client.on_line(audio_line(1600, byte=9))


class GateTests(WakeHarness, unittest.TestCase):
    def test_a_mismatch_opens_no_talk_and_says_so(self) -> None:
        gate = FakeGate()
        controller = self.started(gate)
        self.heard()
        self.assertEqual(self.talk.wakes, [])                             # not before the verdict
        ((pcm, done),) = gate.asked
        self.assertEqual(pcm, bytes([7]) * 3200)                          # the wake clip: the detector's pre-roll
        with self.assertLogs("briefing_reader.wake_ui", level="INFO") as logs:
            done(voiceprint.Verdict(voiceprint.MISMATCH, 0.31))
        self.assertEqual(self.talk.wakes, [])
        self.assertIn("INFO:briefing_reader.wake_ui:Wake: voiceprint mismatch (score 0.31)", logs.output)
        self.assertEqual(self.client.sent[-1], {"handoff_end": True})     # it listens again
        self.assertEqual(controller.short_status(), "That didn't sound like you, sir.")
        self.assertEqual(len(controller.short_status()), wake_ui.SHORT_CAP)
        text, look = controller.status()
        self.assertTrue(text.startswith("That didn't sound like you, sir."))
        self.assertEqual(look, wake_ui.LOOK_LISTENING)
        self.assertEqual(controller._store.counts()["wakes"], 0)
        self.clock[0] += wake_ui.NOT_YOU_MS / 1000 + 0.1
        self.assertEqual(controller.short_status(), wake_ui.SHORT_LISTENING)

    def test_a_match_or_an_unchecked_wake_goes_on_as_before(self) -> None:
        for verdict in (voiceprint.Verdict(voiceprint.MATCH, 0.88), voiceprint.unchecked(voiceprint.WHY_SHORT)):
            with self.subTest(verdict.kind):
                gate = FakeGate()
                controller = self.started(gate)
                self.heard()
                gate.asked[0][1](verdict)
                self.assertEqual(len(self.talk.wakes), 1)
                self.assertEqual(self.talk.wakes[0]["preroll"], bytes([7]) * 3200)
                self.assertEqual(self.talk.wakes[0]["stream"].read_available(), bytes([9]) * 1600)
                self.assertEqual(controller._store.counts()["wakes"], 1)

    def test_no_check_when_the_gate_is_off(self) -> None:
        gate = FakeGate(on=False)
        self.started(gate)
        self.heard()
        self.assertEqual(gate.asked, [])
        self.assertEqual(len(self.talk.wakes), 1)

    def test_a_verdict_for_a_wake_that_was_let_go_meanwhile_starts_nothing(self) -> None:
        gate = FakeGate()
        controller = self.started(gate)
        self.heard()
        controller.set_on(False)                                          # the listener stopped meanwhile
        gate.asked[0][1](voiceprint.Verdict(voiceprint.MATCH, 0.9))
        self.assertEqual(self.talk.wakes, [])

    def test_the_rows_and_the_badge_words_fit(self) -> None:
        controller = self.started(FakeGate())
        self.assertIn(("Recognise my voice", "on"), controller.settings_rows())
        self.assertIn(wake_ui.SHORT_NOT_YOU, wake_ui.SHORT_WORDS)
        self.assertLessEqual(len(wake_ui.SHORT_NOT_YOU), wake_ui.SHORT_CAP)


class RealGateTests(WakeHarness, Case):
    def test_the_controller_and_the_wake_gate_together(self) -> None:
        for output, opens in ((unit(9), False), (unit(0, 1, 0.2), True)):     # another voice, then the owner's
            with self.subTest(opens=opens):
                voice = self.make(enrolled=True, outputs=[output])
                controller = self.started(voice)
                clip = room(0.3) + tone(1.0) + room(0.3)
                self.client.on_line({"heard": True, "score": 0.97, "speaking": False, "preroll_bytes": len(clip)})
                self.client.on_line({"audio": base64.b64encode(clip).decode("ascii")})
                self.assertEqual(len(self.talk.wakes), 1 if opens else 0)
                self.assertEqual(self.session.runs, 1)
                if not opens:
                    self.assertEqual(controller.short_status(), "That didn't sound like you, sir.")


class FixRoundTests(Case):
    """The review's fix round in the app: the save after a Cancel (M1), the short count (M2), each take's window
    (L5), a failed switch reset (L2), the slow check (L3), a model that will not load (L4), a talk or his voice during
    a recording (L6)."""

    @staticmethod
    def wait_for_learning() -> None:
        for thread in list(threading.enumerate()):
            if thread.name == "voiceprint-learn":
                thread.join(5)

    def record_until_learning(self, controller) -> None:
        controller.start_recording()
        for _ in range(4000):
            if controller.status_text().startswith("Learning"):
                return
            if controller._gap.isActive():
                controller._gap.stop()
                controller._open_take()
            else:
                controller._poll()
        raise AssertionError("it never got to learning")

    def test_a_cancel_or_closing_while_it_learns_writes_nothing(self) -> None:
        for how in ("cancel", "shutdown"):
            with self.subTest(how):
                controller = self.make(takes=[GOOD_TAKE] * 3, threaded=True)
                self.record_until_learning(controller)
                if how == "cancel":
                    controller.cancel_recording()
                else:
                    controller.shutdown()
                self.wait_for_learning()
                for _ in range(5):
                    APP.processEvents()
                self.assertFalse(controller.enrolled())
                self.assertEqual(list(self.data.iterdir()), [])

    def test_the_template_is_saved_on_the_qt_thread_when_still_wanted(self) -> None:
        controller = self.make(takes=[GOOD_TAKE] * 3, threaded=True)
        self.record_until_learning(controller)
        self.wait_for_learning()
        self.assertFalse((self.data / voiceprint.VOICEPRINT_FILE).exists())      # the worker never writes it
        for _ in range(5):
            APP.processEvents()
        self.assertTrue((self.data / voiceprint.VOICEPRINT_FILE).exists())
        self.assertTrue(controller.enrolled())

    def test_short_wakes_are_counted_for_this_run(self) -> None:
        controller = self.make(enrolled=True)
        short = room(0.5) + tone(0.3) + room(0.8)
        for _ in range(3):
            controller.check(short, lambda _verdict: None)
        row = dict(controller.settings_rows())["Recognise my voice"]
        self.assertEqual(row, "on: only your voice wakes Jarvis; 3 wakes were too short to check")
        self.assertIn("3 wakes were too short to check, so they were let through.", controller.status_text())
        self.assertEqual(self.session.runs, 0)

    def test_each_take_is_the_wake_window(self) -> None:
        take = room(1.2) + tone(1.0) + room(0.2, seed=2)
        controller = self.make(takes=[take] * 3)
        controller.start_recording()
        self.run_takes(controller)
        self.assertTrue(controller.enrolled())
        frames = 1 + (voiceprint.WAKE_CLIP_BYTES // 2 - voiceprint.FRAME_LENGTH) // voiceprint.FRAME_SHIFT
        self.assertEqual(self.session.frames, [frames] * 3)                       # 1.6 s, not talk's whole clip

    def test_a_failed_switch_reset_says_so(self) -> None:
        settings = FakeSettings()
        settings.reset = lambda keys: False
        controller = self.make(takes=[GOOD_TAKE] * 3, settings=settings)
        controller.start_recording()
        self.run_takes(controller)
        self.assertTrue(controller.enrolled())
        self.assertIn("couldn't be reset", controller.status_text())

    def test_a_slow_check_lets_the_wake_through_and_a_late_verdict_is_let_go(self) -> None:
        held = []
        controller = self.make(enrolled=True, start_work=held.append)
        verdicts = []
        with mock.patch.object(voiceprint_ui, "VERDICT_TIMEOUT_MS", 20):
            controller.check(room(0.3) + tone(1.0), verdicts.append)
        self.assertEqual(verdicts, [])
        with self.assertLogs("briefing_reader.voiceprint_ui", level="INFO") as logs:
            QTest.qWait(120)
        self.assertEqual(verdicts, [voiceprint.unchecked(voiceprint_ui.WHY_SLOW)])
        self.assertIn("INFO:briefing_reader.voiceprint_ui:Voiceprint: unchecked (slow)", logs.output)
        held[0]()                                                                 # the verdict comes after
        self.assertEqual(len(verdicts), 1)

    def test_checks_make_no_timer_of_their_own_and_shutdown_stops_the_one_there_is(self) -> None:
        controller = self.make(enrolled=True)
        before = len(controller.findChildren(QTimer))
        for _ in range(3):
            controller.check(room(0.3) + tone(1.0), lambda _verdict: None)
        self.assertEqual(len(controller.findChildren(QTimer)), before)          # none left for a deferred delete
        held = []
        controller._start_work = held.append
        controller.check(room(0.3) + tone(1.0), lambda _verdict: None)
        self.assertTrue(controller._verdict_timer.isActive())
        controller.shutdown()
        self.assertFalse(controller._verdict_timer.isActive())

    def test_one_failed_run_is_not_a_broken_model(self) -> None:
        controller = self.make(enrolled=True, outputs=[RuntimeError("busy"), unit(0)])
        verdicts = []
        controller.check(room(0.3) + tone(1.0), verdicts.append)
        self.assertEqual(verdicts[0].reason, voiceprint.WHY_FAILED)
        self.assertEqual(controller.off_reason(), "")                            # still checking
        self.assertEqual(controller.state(), voiceprint_ui.STATE_ENROLLED)
        controller.check(room(0.3) + tone(1.0), verdicts.append)
        self.assertEqual(verdicts[1].kind, voiceprint.MATCH)

    def test_one_failed_run_while_learning_is_not_a_broken_model(self) -> None:
        controller = self.make(takes=[GOOD_TAKE] * 6, outputs=[RuntimeError("busy"), unit(0)])
        controller.start_recording()
        self.run_takes(controller)
        self.assertFalse(controller.enrolled())
        self.assertIn("nothing was kept", controller.status_text())
        self.assertTrue(controller.can_record())                                 # Record again is offered
        self.assertEqual(controller.state(), voiceprint_ui.STATE_READY)
        controller.start_recording()
        self.run_takes(controller)
        self.assertTrue(controller.enrolled())

    def test_a_model_that_will_not_load_is_offered_again_and_its_folder_goes_first(self) -> None:
        controller = self.make(enrolled=True, loader_error=voiceprint.VoiceprintError(voiceprint.ERR_FAILED))
        panel = VoicePanel()
        controller.attach_panel(panel)
        controller.check(room(0.3) + tone(1.0), lambda _verdict: None)
        self.assertEqual(controller.state(), voiceprint_ui.STATE_BROKEN)
        self.assertFalse(panel.download.isHidden())
        self.assertIn("again", panel.download.text())
        folder = voiceprint.model_dir(self.root / "models")
        (folder / "leftover.bin").write_bytes(b"x")
        self.loader_error = None
        panel.download.click()
        self.assertEqual(self.downloads, [self.root / "models"])
        self.assertFalse((folder / "leftover.bin").exists())                      # deleted before the download
        self.assertTrue(voiceprint.model_ready(folder))
        self.assertEqual(controller.state(), voiceprint_ui.STATE_ENROLLED)
        self.assertTrue(controller.will_check())

    def test_a_talk_or_his_voice_stops_a_recording(self) -> None:
        for busy, words in ((voiceprint_ui.BUSY_TALK, "a talk started"),
                            (voiceprint_ui.BUSY_SPEAKING, "Jarvis started speaking")):
            with self.subTest(busy):
                controller = self.make(takes=[GOOD_TAKE] * 3)
                controller.start_recording()
                for _ in range(5):
                    controller._poll()
                self.busy[0] = busy
                controller._poll()
                self.assertFalse(controller.recording())
                self.assertEqual(self.recordings, [True, False])
                self.assertTrue(self.backend.streams[-1].closed)
                self.assertIn(f"Stopped: {words}, so nothing was kept. Press Record my voice to try again, sir.",
                              controller.status_text())
                self.assertEqual(list(self.data.iterdir()), [])

    def test_the_app_s_busy_is_a_talk_or_his_voice(self) -> None:
        def stub(holds):
            return SimpleNamespace(_wake_holds=lambda: list(holds))

        self.assertEqual(ui.AppController._voiceprint_busy(stub([])), "")
        self.assertEqual(ui.AppController._voiceprint_busy(stub([wake_ui.HOLD_MEETING])), "")
        self.assertEqual(ui.AppController._voiceprint_busy(stub([wake_ui.HOLD_TALK])), voiceprint_ui.BUSY_TALK)
        self.assertEqual(ui.AppController._voiceprint_busy(stub([wake_ui.HOLD_SPEAKING])),
                         voiceprint_ui.BUSY_SPEAKING)


class CanvasCaptionTests(WakeHarness, unittest.TestCase):
    """H1: the mismatch caption reaches the canvas (HEY JARVIS shows the page's words only for "other")."""

    def canvas_message(self, controller) -> dict:
        reading = SimpleNamespace(
            header=SimpleNamespace(clock_texts=lambda: ("9:41", "Tue 24 Nov"), state_tone=lambda: "holo"),
            recording_state=lambda: ("", ""), wake_state=controller.status, wake_words=controller.short_status,
            state_word=lambda: "", needs_ok_count=lambda: 0, is_new_briefing=lambda: False, hold_word=lambda: "")
        stub = SimpleNamespace(reading=reading, host=SimpleNamespace(wake=controller, talk=None),
                               buttons=lambda _heard: [], _has_meter=lambda: True, _side=lambda: [0, 0])
        stub.hearing = lambda: canvas_ui.CanvasController.hearing(stub)
        message = canvas_ui.CanvasController.canvas_message(stub)
        protocol.check_message(message)
        return message

    def test_the_caption_shows_during_the_window_and_not_after(self) -> None:
        gate = FakeGate()
        controller = self.started(gate)
        self.assertEqual(controller.hearing(), (wake_ui.HEAR_LISTENING, ""))
        self.heard()
        gate.asked[0][1](voiceprint.Verdict(voiceprint.MISMATCH, 0.2))
        self.assertEqual(controller.hearing(), (wake_ui.HEAR_OTHER, ""))
        message = self.canvas_message(controller)
        self.assertEqual((message["hear"], message["wake"]), ("other", "That didn't sound like you, sir."))
        self.clock[0] += wake_ui.NOT_YOU_MS / 1000 + 0.1
        self.assertEqual(controller.hearing(), (wake_ui.HEAR_LISTENING, ""))
        message = self.canvas_message(controller)
        self.assertEqual((message["hear"], message["wake"]), ("listening", wake_ui.SHORT_LISTENING))


class ConfigTests(unittest.TestCase):
    def test_only_my_voice_defaults_on_and_is_a_comfort_key(self) -> None:
        self.assertTrue(cfg.WakeConfig().only_my_voice)
        self.assertTrue(cfg._parse_wake({}).only_my_voice)
        self.assertFalse(cfg._parse_wake({"wake": {"only_my_voice": False}}).only_my_voice)
        with self.assertLogs("briefing_reader.config", level="WARNING"):
            self.assertTrue(cfg._parse_wake({"wake": {"only_my_voice": "no"}}).only_my_voice)
        self.assertEqual(overrides.EDITABLE["wake.only_my_voice"].kind, overrides.KIND_BOOL)
        self.assertIs(overrides.default("wake.only_my_voice"), True)
        self.assertIn("wake", cfg._KNOWN_TABLES)


class AppHookTests(unittest.TestCase):
    def test_recording_pauses_hey_jarvis(self) -> None:
        values = dict(talk=SimpleNamespace(state=talk_ui.IDLE), voice=SimpleNamespace(speaking=False),
                      player=SimpleNamespace(state=ui.IDLE), meetings=None, _focus_quiet=lambda: False,
                      _call_quiet=lambda: False)
        self.assertEqual(ui.AppController._wake_holds(SimpleNamespace(**values, voiceprint=None)), [])
        recording = SimpleNamespace(recording=lambda: True)
        self.assertEqual(ui.AppController._wake_holds(SimpleNamespace(**values, voiceprint=recording)),
                         [wake_ui.HOLD_VOICEPRINT])
        self.assertIn(wake_ui.HOLD_VOICEPRINT, wake_ui.HOLD_TEXTS)

if __name__ == "__main__":
    unittest.main()
