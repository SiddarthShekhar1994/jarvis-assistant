"""Talking to Jarvis in the app: talk_ui.TalkController (the talk key's toggle, the states, what is missing, Esc,
what was heard going in), Settings > Talk, AskController.submit_spoken and the app's hooks (AppController
_create_talk, _talk_look). The microphone, the speech model and the app are stand-ins (offscreen Qt)."""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from briefing_reader import ask_ui, config, hud, talk, talk_speech, talk_ui, ui
from briefing_reader.talk_speech import Heard
from tests.test_talk import ROOM, VOICE, noise, tone
from tests.test_talk_speech import FakeBackend, FakeStream

APP = QApplication.instance() or QApplication([])
SAID = "what's on my calendar today"
SPEECH = noise(300, ROOM) + tone(1200, VOICE) + noise(2000, ROOM)


class FakeClient:
    def __init__(self, heard: Heard) -> None:
        self.heard = heard
        self.clips: list[int] = []
        self.closed = False

    def understand(self, pcm: bytes) -> Heard:
        self.clips.append(len(pcm))
        return self.heard

    def close(self) -> None:
        self.closed = True


class FakeStopKeys:
    """desktop.stopkeys' seam: the global Esc, held while listening."""

    def __init__(self, refuse: bool = False) -> None:
        self.on_stop = None
        self.held = False
        self.refuse = refuse

    def arm(self, on_stop) -> bool:
        if self.refuse:
            return False
        self.on_stop, self.held = on_stop, True
        return True

    def disarm(self) -> None:
        self.held = False

    def armed(self) -> bool:
        return self.held


class TalkControllerTests(unittest.TestCase):
    def make(self, *, audio: bytes = SPEECH, heard: Heard | None = None, ready: bool = True, missing_audio: str = "",
             missing_speech: str = "", refuse: bool = False, devices=None, fail_after: int | None = None):
        self.stream = FakeStream(audio, fail_after=fail_after)
        self.stopkeys = FakeStopKeys()
        self.backend = FakeBackend(self.stream, devices=devices, refuse=refuse)
        self.backend.close = mock.Mock()
        self.client = FakeClient(heard or Heard(True, SAID, ms=300))
        self.calls = SimpleNamespace(forward=0, before=0, statuses=[], submitted=[], looks=[], levels=[])
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        cfg = SimpleNamespace(talk=config.TalkConfig(enabled=True), data_dir=Path(folder))
        calls = self.calls

        def forward():
            calls.forward += 1

        def before():
            calls.before += 1

        controller = talk_ui.TalkController(
            cfg, bring_forward=forward, before_talk=before,
            show_status=lambda text, tone, hud_text=None: calls.statuses.append((text, tone)),
            submit=calls.submitted.append, set_look=calls.looks.append, set_level=calls.levels.append,
            models_root=Path(folder), picked_mic=lambda: "", backend_loader=lambda: (self.backend, ""),
            client_factory=lambda _folder, _hints, _idle: self.client, audio_missing=lambda: missing_audio,
            speech_missing=lambda: missing_speech, threaded=False, stopkeys=self.stopkeys)
        controller._model_ready = lambda: ready
        self.addCleanup(controller.shutdown)
        return controller

    def talk_through(self, controller: talk_ui.TalkController, polls: int = 500) -> None:
        for _ in range(polls):
            if controller.state != talk_ui.LISTENING:
                return
            controller._poll()

    def test_a_talk_listens_ends_by_quiet_and_goes_in_as_spoken_words(self) -> None:
        controller = self.make()
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO) as logs:
            controller.toggle()
            self.assertEqual(controller.state, talk_ui.LISTENING)
            self.assertTrue(controller._watching_escape)
            self.talk_through(controller)
        self.assertEqual(self.calls.submitted, [SAID])
        self.assertEqual(self.calls.looks, [talk_ui.LISTENING, talk_ui.UNDERSTANDING, talk_ui.IDLE])
        self.assertEqual((self.calls.forward, self.calls.before), (1, 1))
        self.assertTrue(self.stream.closed)
        self.backend.close.assert_called_once_with()
        self.assertIsNone(self.calls.levels[-1])
        self.assertTrue(any(level and level > 0.5 for level in self.calls.levels))
        self.assertFalse(controller._watching_escape)
        self.assertIn("Listening", self.calls.statuses[0][0])
        self.assertIn("Ctrl+Alt+Space", self.calls.statuses[0][0])
        self.assertFalse(any(SAID in line for line in logs.output))     # never what was said

    def test_a_second_tap_ends_it_now_and_what_was_said_goes_in(self) -> None:
        controller = self.make(audio=noise(300, ROOM) + tone(4000, VOICE))
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
            controller.toggle()
            for _ in range(25):
                controller._poll()
            controller.toggle()
        self.assertEqual((controller.state, self.calls.submitted), (talk_ui.IDLE, [SAID]))
        self.assertTrue(self.stream.closed)

    def test_esc_cancels_and_nothing_goes_in(self) -> None:
        controller = self.make(audio=noise(300, ROOM) + tone(4000, VOICE))
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
            controller.toggle()
            controller._poll()
            event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
            self.assertTrue(controller.eventFilter(APP, event))
        self.assertEqual((controller.state, self.calls.submitted, self.client.clips), (talk_ui.IDLE, [], []))
        self.assertEqual(self.calls.statuses[-1], (talk_ui.CANCELLED, hud.TONE_IDLE))
        self.assertTrue(self.stream.closed)
        self.assertFalse(controller._watching_escape)
        self.assertFalse(controller.eventFilter(APP, event))            # Esc is the app's again
        self.assertFalse(controller.cancel())

    def test_the_global_esc_is_held_only_while_listening_and_cancels(self) -> None:
        controller = self.make(audio=noise(300, ROOM) + tone(4000, VOICE))
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
            controller.toggle()
            self.assertTrue(self.stopkeys.held)
            controller._poll()
            self.stopkeys.on_stop()                           # as the stop keys' own thread would
            APP.processEvents()
        self.assertEqual((controller.state, self.calls.submitted), (talk_ui.IDLE, []))
        self.assertEqual(self.calls.statuses[-1][0], talk_ui.CANCELLED)
        self.assertFalse(self.stopkeys.held)
        self.assertTrue(self.stream.closed)

    def test_the_global_esc_is_released_on_every_end(self) -> None:
        controller = self.make()
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
            controller.toggle()
            self.talk_through(controller)
        self.assertFalse(self.stopkeys.held)

    def test_a_failing_read_closes_the_microphone_and_ends_the_talk(self) -> None:
        controller = self.make(audio=noise(300, ROOM) + tone(4000, VOICE))
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
            controller.toggle()
            controller._session.poll = mock.Mock(side_effect=RuntimeError("boom"))
            controller._poll()
        self.assertEqual((controller.state, self.calls.submitted), (talk_ui.IDLE, []))
        self.assertTrue(self.stream.closed)
        self.assertFalse(self.stopkeys.held)

    def test_a_failure_while_understanding_leaves_the_talk_key_working(self) -> None:
        controller = self.make()
        self.client.understand = mock.Mock(side_effect=RuntimeError("boom"))
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
            controller.toggle()
            self.talk_through(controller)
        self.assertEqual((controller.state, self.calls.statuses[-1][0]), (talk_ui.IDLE, talk_ui.NOT_UNDERSTOOD))

    def test_what_is_missing_is_said_and_nothing_opens(self) -> None:
        for kwargs, words in (({"missing_audio": "PyAudioWPatch"}, "install the add-ons"),
                              ({"missing_speech": "faster-whisper"}, "install the add-ons"),
                              ({"ready": False}, "download the speech model (about 480 MB)")):
            with self.subTest(kwargs=kwargs):
                controller = self.make(**kwargs)
                with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
                    controller.toggle()
                self.assertEqual(controller.state, talk_ui.IDLE)
                self.assertIn(words, self.calls.statuses[-1][0])
                self.assertEqual((self.backend.opened, self.calls.before, self.calls.forward), ([], 0, 1))

    def test_nothing_said_a_muted_microphone_and_a_stopped_one(self) -> None:
        cases = ((noise(9000, ROOM), {}, talk_ui.NOT_CAUGHT),
                 (bytes(16_000 * 2 * 9), {}, talk_ui.muted_text("Desk mic", True)),
                 (noise(300, ROOM) + tone(3000, VOICE), {"fail_after": 3}, talk_ui.DEVICE_STOPPED))
        for audio, kwargs, words in cases:
            with self.subTest(words=words):
                controller = self.make(audio=audio, **kwargs)
                with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
                    controller.toggle()
                    self.talk_through(controller)
                self.assertEqual((controller.state, self.calls.submitted, self.client.clips), (talk_ui.IDLE, [], []))
                self.assertEqual(self.calls.statuses[-1][0], words)
                self.assertTrue(self.stream.closed)

    def test_a_microphone_that_will_not_open(self) -> None:
        for kwargs, code in (({"refuse": True}, talk.ERR_MIC_BUSY), ({"devices": []}, talk.ERR_NO_MICROPHONE)):
            with self.subTest(code=code):
                controller = self.make(**kwargs)
                with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
                    controller.toggle()
                self.assertEqual(controller.state, talk_ui.IDLE)
                self.assertEqual(self.calls.statuses[-1][0], talk_ui.MIC_TEXTS[code])
                self.backend.close.assert_called_once_with()

    def test_what_the_model_could_not_understand(self) -> None:
        cases = ((Heard(False, error=talk_speech.ERR_FAILED), talk_ui.NOT_UNDERSTOOD),
                 (Heard(False, error=talk_speech.ERR_MODEL), "download the speech model"),
                 (Heard(True, "   "), talk_ui.NOT_CAUGHT))
        for heard, words in cases:
            with self.subTest(words=words):
                controller = self.make(heard=heard)
                with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
                    controller.toggle()
                    self.talk_through(controller)
                self.assertEqual(self.calls.submitted, [])
                self.assertIn(words, self.calls.statuses[-1][0])

    def test_the_talk_key_while_understanding_does_nothing(self) -> None:
        controller = self.make()
        controller._state = talk_ui.UNDERSTANDING
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
            controller.toggle()
        self.assertEqual((self.backend.opened, controller.state), ([], talk_ui.UNDERSTANDING))
        controller._state = talk_ui.IDLE

    def test_closing_the_app_mid_talk_closes_the_microphone_and_says_nothing(self) -> None:
        controller = self.make(audio=noise(300, ROOM) + tone(4000, VOICE))
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
            controller.toggle()
            controller._poll()
            shown = len(self.calls.statuses)
            controller.shutdown()
        self.assertTrue(self.stream.closed)
        self.assertEqual((len(self.calls.statuses), self.calls.submitted), (shown, []))
        self.assertFalse(controller._watching_escape)

    def test_the_speech_process_ends_with_the_app(self) -> None:
        controller = self.make()
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO):
            controller.toggle()
            self.talk_through(controller)
        controller.shutdown()
        for _ in range(50):
            if self.client.closed:
                break
            __import__("time").sleep(0.02)
        self.assertTrue(self.client.closed)

    def test_download_only_on_the_click_with_its_progress(self) -> None:
        controller = self.make(ready=False)
        state = {"ready": False}
        controller._model_ready = lambda: state["ready"]
        seen = {}

        class Downloader:
            def __init__(self, root, model, progress, cancel):
                seen.update(root=root, model=model.name)
                self.progress = progress

            def run(self):
                self.progress(240, 480)
                state["ready"] = True
                return ""

        controller._downloader_factory = Downloader
        panel = talk_ui.TalkSettingsPanel(talk_ui.pip_command("C:/Py/pythonw.exe"))
        refresh = mock.Mock()
        controller.attach_panel(panel, refresh=refresh)
        self.assertFalse(panel.download.isHidden())
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO) as logs:
            panel.download.click()
        self.assertEqual(seen["model"], "small.en")
        refresh.assert_called_once_with()
        self.assertTrue(panel.download.isHidden())
        self.assertIn("on this PC", panel.status.text())
        self.assertIn("the model download ended (ready)", "\n".join(logs.output))

    def test_settings_rows(self) -> None:
        controller = self.make(ready=False, missing_audio="PyAudioWPatch")
        rows = dict(controller.settings_rows())
        self.assertEqual(rows["Talk key"], "Ctrl+Alt+Space")
        self.assertEqual(rows["Speech model"], "small.en (about 480 MB): not downloaded yet")
        self.assertEqual(rows["Add-ons"], "missing: PyAudioWPatch")

    def test_silence_names_the_microphone_and_flags_a_virtual_one(self) -> None:
        from briefing_reader.meeting_audio import DeviceInfo

        devices = [DeviceInfo("1", "Microphone (Steam Streaming Microphone)", True),
                   DeviceInfo("2", "Microphone (FIFINE K678 Microphone)")]
        controller = self.make(audio=bytes(16_000 * 2 * 9), devices=devices)
        with self.assertLogs("briefing_reader.talk_ui", logging.INFO) as logs:
            controller.toggle()
            self.talk_through(controller)
        said = self.calls.statuses[-1][0]
        self.assertIn("Steam Streaming Microphone", said)
        self.assertIn("virtual microphone", said)
        self.assertIn("the Windows default", said)
        self.assertIn("Settings > Meetings > Microphone", said)
        self.assertNotIn("Steam", "\n".join(logs.output))             # a device's name is never logged
        self.assertIn("(a virtual microphone)", "\n".join(logs.output))
        row = dict(controller.settings_rows())["Microphone"]
        self.assertTrue(row.startswith("Microphone (Steam Streaming Microphone) (the Windows default) - a virtual"))
        self.assertEqual(talk_ui.muted_text("Microphone (FIFINE K678 Microphone)", False),
                         "I heard nothing at all from \u201cMicrophone (FIFINE K678 Microphone)\u201d (picked in "
                         "Settings > Meetings): is it muted?")
        self.assertEqual(talk_ui.muted_text(""), talk_ui.MUTED)
        for name, virtual in (("Headset Microphone (Oculus Virtual Audio Device)", True), ("CABLE Output (VB-Audio)", True),
                              ("Stereo Mix (Realtek Audio)", True), ("Microphone (FIFINE K678 Microphone)", False),
                              ("Desktop Microphone (3- Microsoft LifeCam HD-5000)", False)):
            with self.subTest(name=name):
                self.assertEqual(talk.virtual_microphone(name), virtual)

    def test_the_key_row_says_when_another_program_has_the_talk_key(self) -> None:
        from briefing_reader import hotkey

        controller = self.make()
        folder = controller._data_dir
        hotkey.write_key_status(folder, {"talk": {"key": "ctrl+alt+space", "status": hotkey.KEY_TAKEN, "error": 1409}})
        self.assertEqual(dict(controller.settings_rows())["Talk key"],
                         "Ctrl+Alt+Space - another program has it; choose another [talk] key in config.toml")
        hotkey.write_key_status(folder, {"talk": {"key": "ctrl+alt+space", "status": hotkey.KEY_LISTENING}})
        self.assertEqual(dict(controller.settings_rows())["Talk key"], "Ctrl+Alt+Space")
        hotkey.write_key_status(folder, {"talk": {"key": "ctrl+alt+t", "status": hotkey.KEY_TAKEN, "error": 1409}})
        self.assertEqual(dict(controller.settings_rows())["Talk key"], "Ctrl+Alt+Space")   # another key's status

    def test_the_pip_line_is_python_exe_and_quoted_only_with_a_space(self) -> None:
        self.assertEqual(talk_ui.pip_command("C:/Py313/pythonw.exe"),
                         "C:/Py313/python.exe -m pip install faster-whisper==1.2.1 PyAudioWPatch==0.2.12.9")
        self.assertTrue(talk_ui.pip_command("C:/Program Files/Py/python.exe").startswith('& "C:/Program Files/'))


class SpokenAskTests(unittest.TestCase):
    def test_spoken_words_go_in_as_typed_ones_logged_by_length_only(self) -> None:
        seen = []
        stub = SimpleNamespace()
        stub.submit = lambda text: seen.append((text, stub._spoken_now))
        with self.assertLogs("briefing_reader.ask_ui", logging.INFO) as logs:
            ask_ui.AskController.submit_spoken(stub, SAID)
        self.assertEqual((seen, stub._spoken_now), ([(SAID, True)], False))
        self.assertFalse(any(SAID in line for line in logs.output))

    def test_live_says_you_said(self) -> None:
        steps = []

        class Step:
            def note(self, _text):
                pass

            def done(self, _status):
                pass

        class Task:
            def step(self, _name, _title, fields):
                steps.append(fields)
                return Step()

        live = SimpleNamespace(task=lambda _kind, _text: Task())
        ask_ui._request_task(live, SAID, None, kind=ask_ui.TASK_WEB, spoken=True)
        ask_ui._request_task(live, SAID, None, kind=ask_ui.TASK_WEB)
        self.assertEqual([fields[0][0] for fields in steps], ["You said", "You typed"])


class AppHookTests(unittest.TestCase):
    def stub(self, *, talk_on: bool = True, ask: object = True) -> QObject:
        """The app stand-in: a QObject (the controller's parent), with what _create_talk reads."""
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        settings = SimpleNamespace(add_group=mock.Mock(), refresh=mock.Mock())
        stub = QObject()
        self.addCleanup(stub.deleteLater)
        stub.config = SimpleNamespace(talk=config.TalkConfig(enabled=talk_on), data_dir=Path(folder))
        stub.ask = SimpleNamespace(talk_status=mock.Mock(), submit_spoken=mock.Mock()) if ask else None
        stub.window = SimpleNamespace(reading=SimpleNamespace(settings_page=settings))
        for name in ("_talk_bring_forward", "_talk_before", "_talk_look", "_talk_level"):
            setattr(stub, name, mock.Mock())
        return stub

    def test_talk_off_or_ask_off_makes_no_controller(self) -> None:
        self.assertIsNone(ui.AppController._create_talk(self.stub(talk_on=False)))
        with self.assertLogs("briefing_reader.ui", logging.INFO):
            self.assertIsNone(ui.AppController._create_talk(self.stub(ask=False)))

    def test_talk_on_makes_the_controller_and_settings_talk(self) -> None:
        stub = self.stub()
        controller = ui.AppController._create_talk(stub)
        self.addCleanup(controller.shutdown)
        self.assertIsInstance(controller, talk_ui.TalkController)
        key, title = stub.window.reading.settings_page.add_group.call_args.args
        self.assertEqual((key, title), (talk_ui.SETTINGS_KEY, talk_ui.SETTINGS_TITLE))

    def test_the_orb_listens_then_understands_then_is_the_app_s_again(self) -> None:
        reading = SimpleNamespace(set_activity_state=mock.Mock(), set_talking=mock.Mock())
        stub = SimpleNamespace(_update_reading_controls=mock.Mock(), _pump_voice=mock.Mock(),
                               window=SimpleNamespace(reading=reading))
        for state, look in ((talk_ui.LISTENING, (hud.ORB_LISTENING, "LISTENING")),
                            (talk_ui.UNDERSTANDING, (hud.ORB_WORKING, "UNDERSTANDING")), (talk_ui.IDLE, None)):
            ui.AppController._talk_look(stub, state)
            self.assertEqual(stub._talk_orb, look)
            reading.set_activity_state.reset_mock()
            ui.AppController._apply_talk_look(stub, reading)
            if look is None:
                reading.set_activity_state.assert_not_called()
            else:
                reading.set_activity_state.assert_called_once_with(look[0], False, look[1])
        self.assertEqual([call.args[0] for call in reading.set_talking.call_args_list], [True, False, False])
        stub._pump_voice.assert_called_once_with()                    # the held voice may speak again

    def test_the_voice_is_held_while_a_talk_lasts(self) -> None:
        for state, held in ((talk_ui.LISTENING, True), (talk_ui.UNDERSTANDING, True), (talk_ui.IDLE, False)):
            self.assertEqual(ui.AppController._talk_live(SimpleNamespace(talk=SimpleNamespace(state=state))), held)
        self.assertFalse(ui.AppController._talk_live(SimpleNamespace(talk=None)))

    def test_a_briefing_playing_or_waiting_to_play_on_is_paused(self) -> None:
        for state, paused in ((ui.PLAYING, True), (ui.WAITING, True), (ui.PAUSED, False), (ui.FINISHED, False)):
            with self.subTest(state=state):
                stub = SimpleNamespace(_owner_playback=mock.Mock(), _update_reading_controls=mock.Mock(),
                                       _script=object(), player=SimpleNamespace(state=state, pause=mock.Mock()))
                ui.AppController._talk_before(stub)
                stub._owner_playback.assert_called_once_with()
                self.assertEqual(stub.player.pause.called, paused)

    def test_the_level_goes_to_home_s_orb(self) -> None:
        hero = SimpleNamespace(set_level=mock.Mock())
        stub = SimpleNamespace(window=SimpleNamespace(reading=SimpleNamespace(hero=hero)))
        ui.AppController._talk_level(stub, 0.4)
        hero.set_level.assert_called_once_with(0.4)


if __name__ == "__main__":
    unittest.main()
