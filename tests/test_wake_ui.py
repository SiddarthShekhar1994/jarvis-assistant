""""Hey Jarvis" in the app: the switch and today's counts (wake_ui.WakeStore), the hand-off stream, the chime, the
HEY JARVIS marks, WakeController (the listener's lines, the pauses, a wake handed to the talk, restarts, Settings >
Talk) and the app's hooks. The listener, the talk and the downloads are stand-ins (offscreen Qt): no process, microphone,
speaker or network is used."""

from __future__ import annotations

import base64
import io
import json
import logging
import tempfile
import unittest
import wave
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from briefing_reader import hud_overlay, talk_ui, ui, wake, wake_listen, wake_ui
from briefing_reader.wake_ui import HandoffStream, WakeController, WakeStore

APP = QApplication.instance() or QApplication([])
MIC = "Microphone (FIFINE K678 Microphone)"
STEAM = "Microphone (Steam Streaming Microphone)"


def ready_model(root: Path) -> Path:
    """The pinned files' sizes and a checked manifest (their bytes do not matter to model_ready)."""
    folder = wake.model_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    for entry in wake.MODEL_FILES:
        with open(folder / entry.name, "wb") as fh:
            fh.truncate(entry.size)
    manifest = {"files": {entry.name: entry.size for entry in wake.MODEL_FILES},
                "sha256": {entry.name: entry.sha256 for entry in wake.MODEL_FILES}}
    (folder / wake.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return folder


class FakeTalk(QObject):
    stateChanged = Signal(str)
    wakeTalkEnded = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.state = talk_ui.IDLE
        self.needs: list[str] = []
        self.accept = True
        self.wakes: list[dict] = []

    def missing(self) -> list[str]:
        return list(self.needs)

    def listen_from_wake(self, stream, *, preroll=b"", device_name="", follows_default=False, speaking=False):
        self.wakes.append({"stream": stream, "preroll": preroll, "device": device_name, "default": follows_default,
                           "speaking": speaking})
        return self.accept


class FakeClient:
    def __init__(self, folder, on_line, sensitivity) -> None:
        self.folder, self.on_line, self.sensitivity = folder, on_line, sensitivity
        self.sent: list[dict] = []
        self.started = False
        self.closed = False
        self.refuse = False

    def start(self) -> bool:
        self.started = not self.refuse
        return self.started

    def send(self, payload: dict) -> bool:
        self.sent.append(payload)
        return True

    def close(self) -> None:
        self.closed = True


def audio_line(size: int, byte: int = 7) -> dict:
    return {"audio": base64.b64encode(bytes([byte]) * size).decode("ascii")}


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_off_until_a_saved_on(self) -> None:
        store = WakeStore(self.folder)
        self.assertFalse(store.on)
        self.assertTrue(store.set_on(True))
        self.assertTrue(WakeStore(self.folder).on)
        (self.folder / wake_ui.WAKE_FILE).write_text('{"on": "yes"}', encoding="utf-8")
        self.assertFalse(WakeStore(self.folder).on)                  # only a stored true
        (self.folder / wake_ui.WAKE_FILE).write_text("not json", encoding="utf-8")
        self.assertFalse(WakeStore(self.folder).on)

    def test_a_turn_on_that_cannot_be_saved_stays_off(self) -> None:
        store = WakeStore(self.folder)
        with mock.patch("briefing_reader.prefs._write_atomic", return_value=False):
            self.assertFalse(store.set_on(True))
        self.assertFalse(store.on)
        store.set_on(True)
        with mock.patch("briefing_reader.prefs._write_atomic", return_value=False):
            store.set_on(False)
        self.assertFalse(store.on)                                    # a Turn off counts even unsaved

    def test_the_sensitivity_is_kept_and_unknown_ones_are_balanced(self) -> None:
        store = WakeStore(self.folder)
        self.assertEqual(store.sensitivity, "balanced")
        store.set_sensitivity("fewer")
        self.assertEqual(WakeStore(self.folder).sensitivity, "fewer")
        store.set_sensitivity("loud")
        self.assertEqual(store.sensitivity, "balanced")

    def test_today_s_counts_start_again_each_day(self) -> None:
        today = [date(2026, 11, 12)]
        store = WakeStore(self.folder, today=lambda: today[0])
        store.count(WakeStore.WAKES)
        store.count(WakeStore.WAKES)
        store.count(WakeStore.NOTHING)
        self.assertEqual(WakeStore(self.folder, today=lambda: today[0]).counts(), {"wakes": 2, "nothing": 1})
        today[0] = date(2026, 11, 13)
        self.assertEqual(store.counts(), {"wakes": 0, "nothing": 0})
        store.count(WakeStore.WAKES)
        self.assertEqual(store.counts(), {"wakes": 1, "nothing": 0})

    def test_the_file_holds_statuses_and_numbers_only(self) -> None:
        store = WakeStore(self.folder)
        store.set_on(True)
        store.count(WakeStore.WAKES)
        data = json.loads((self.folder / wake_ui.WAKE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"on", "sensitivity", "today"})
        self.assertEqual(set(data["today"]), {"day", "wakes", "nothing"})


class HandoffStreamTests(unittest.TestCase):
    def test_the_pre_roll_comes_first_then_the_audio(self) -> None:
        closed: list = []
        stream = HandoffStream(10, closed.append)
        self.assertFalse(stream.preroll_complete())
        self.assertFalse(stream.push(b"a" * 6))
        self.assertTrue(stream.push(b"b" * 6))                         # this piece completed the pre-roll
        self.assertFalse(stream.push(b"c" * 3))
        self.assertEqual(stream.preroll, b"a" * 6 + b"b" * 4)
        self.assertEqual(stream.read_available(), b"b" * 2 + b"c" * 3)
        self.assertEqual(stream.read_available(), b"")
        stream.end()
        with self.assertRaises(OSError):
            stream.read_available()
        stream.close()
        stream.close()
        self.assertEqual(closed, [stream])                              # the hand-off ends once

    def test_what_came_is_read_before_the_end_says_so(self) -> None:
        stream = HandoffStream(0, lambda _stream: None)
        self.assertTrue(stream.preroll_complete())
        stream.push(b"xyz")
        stream.end()
        self.assertEqual(stream.read_available(), b"xyz")
        with self.assertRaises(OSError):
            stream.read_available()

    def test_nothing_is_taken_once_closed(self) -> None:
        stream = HandoffStream(0, lambda _stream: None)
        stream.close()
        self.assertFalse(stream.push(b"late"))
        with self.assertRaises(OSError):
            stream.read_available()


class ChimeAndMarkTests(unittest.TestCase):
    def test_the_chime_is_a_short_soft_wav_made_in_code(self) -> None:
        data = wake_ui.chime_wav()
        with wave.open(io.BytesIO(data), "rb") as fh:
            self.assertEqual((fh.getnchannels(), fh.getsampwidth(), fh.getframerate()), (1, 2, wake_ui.CHIME_RATE))
            frames = fh.readframes(fh.getnframes())
            self.assertAlmostEqual(fh.getnframes() / fh.getframerate(), 0.17, delta=0.02)
        peak = max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True)) for i in range(0, len(frames), 2))
        self.assertLess(peak, 32767 * 0.2)

    def test_no_chime_while_the_tests_run(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict("os.environ", {wake_ui.TEST_GUARD_ENV: "1"}):
                wake_ui.play_chime(folder)
            self.assertFalse((Path(folder) / wake_ui.CHIME_FILE).exists())

    def test_the_badge_shows_only_while_on_with_its_look(self) -> None:
        badge = wake_ui.WakeBadge()
        self.assertTrue(badge.isHidden())
        badge.set_state("Listening for “Hey Jarvis”", wake_ui.LOOK_LISTENING)
        self.assertFalse(badge.isHidden())
        self.assertEqual((badge.look(), badge.text(), badge.toolTip()),
                         (wake_ui.LOOK_LISTENING, "HEY JARVIS", "Listening for “Hey Jarvis”"))
        badge.set_state("off", wake_ui.LOOK_OFF)
        self.assertTrue(badge.isHidden())
        badge.grab()                                                     # paints in every look without failing
        for look in (wake_ui.LOOK_PAUSED, wake_ui.LOOK_PROBLEM):
            badge.set_state("x", look)
            badge.grab()

    def test_the_hud_chip_follows_the_look(self) -> None:
        from briefing_reader import hud_overlay as overlay_mod

        chip = overlay_mod.HudChip(overlay_mod.CHIP_WAKE, read_only=True)
        chip.set_look(overlay_mod.CHIP_WAKE_PAUSED)
        self.assertEqual(chip.look(), overlay_mod.CHIP_WAKE_PAUSED)
        chip.set_look("nonsense")
        self.assertEqual(chip.look(), overlay_mod.CHIP_WAKE)


class ControllerTests(unittest.TestCase):
    def make(self, *, on: bool = True, model: bool = True, runtime: str = "", holds=None, in_control=False):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        if model:
            ready_model(root / "models")
        self.holds: list[str] = list(holds or [])
        self.control = [in_control]
        self.talk = FakeTalk()
        self.clients: list[FakeClient] = []
        self.picked = [""]
        store = WakeStore(root)
        if on:
            store.set_on(True)
        self.clock = [100.0]

        def factory(folder, on_line, sensitivity):
            client = FakeClient(folder, on_line, sensitivity)
            self.clients.append(client)
            return client

        self.downloads: list = []

        def downloader(models_root, progress, cancel):
            self.downloads.append(models_root)

            class Done:
                def run(self_inner):
                    ready_model(models_root)
                    progress(10, 10)
                    return ""
            return Done()

        controller = WakeController(SimpleNamespace(data_dir=root), self.talk, holds=lambda: list(self.holds),
                                    in_control=lambda: self.control[0], picked_mic=lambda: self.picked[0],
                                    store=store, models_root=root / "models", client_factory=factory,
                                    downloader_factory=downloader, runtime_missing=lambda: runtime, threaded=False,
                                    clock=lambda: self.clock[0])
        self.changes = []
        controller.changed.connect(lambda: self.changes.append(controller.status()))
        self.addCleanup(controller.shutdown)
        return controller

    @property
    def client(self) -> FakeClient:
        return self.clients[-1]

    def started(self, **kwargs) -> WakeController:
        controller = self.make(**kwargs)
        controller.start()
        self.client.on_line({"ready": True, "load_ms": 250})
        self.client.on_line({"state": "listening", "mic": MIC, "default": True})
        return controller

    def heard(self, preroll: int = 3200, speaking: bool = False) -> None:
        self.client.on_line({"heard": True, "score": 0.97, "speaking": speaking, "preroll_bytes": preroll})
        self.client.on_line(audio_line(preroll))
        self.client.on_line(audio_line(1600, byte=9))

    def test_nothing_listens_before_start_nor_while_off(self) -> None:
        controller = self.make(on=False)
        controller.start()
        self.assertEqual(self.clients, [])
        self.assertEqual(controller.status()[1], wake_ui.LOOK_OFF)
        controller = self.make()
        self.assertEqual(self.clients, [])                              # made, not started: never a listener

    def test_on_and_ready_it_listens_on_the_picked_microphone(self) -> None:
        controller = self.make()
        self.picked[0] = MIC
        controller.start()
        self.assertTrue(self.client.started)
        self.assertEqual(self.client.folder, wake.model_dir(Path(self._tmp.name) / "models"))
        self.client.on_line({"ready": True, "load_ms": 250})
        self.assertEqual(self.client.sent[:2], [{"listen": {"mic": MIC}}, {"sensitivity": "balanced"}])
        self.client.on_line({"state": "listening", "mic": MIC, "default": False})
        self.assertEqual(controller.status(), ("Listening for “Hey Jarvis”", wake_ui.LOOK_LISTENING))
        self.assertIn(MIC, dict(controller.settings_rows())["Hey Jarvis"])

    def test_turning_it_on_and_off(self) -> None:
        controller = self.make(on=False)
        controller.start()
        controller.set_on(True)
        self.assertEqual(len(self.clients), 1)
        controller.set_on(False)
        self.assertTrue(self.client.closed)
        self.assertEqual(controller.status()[1], wake_ui.LOOK_OFF)

    def test_what_is_missing_keeps_it_from_listening_and_says_so(self) -> None:
        controller = self.make(model=False)
        controller.start()
        self.assertEqual(self.clients, [])
        text, look = controller.status()
        self.assertIn("Download", text)
        self.assertEqual(look, wake_ui.LOOK_PROBLEM)
        controller.download()                                            # only on the owner's click
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(len(self.clients), 1)                           # ready: it listens
        controller = self.make(runtime="onnxruntime")
        controller.start()
        self.assertEqual(self.clients, [])
        self.assertIn("onnxruntime", controller.status()[0])
        controller = self.make()
        self.talk.needs = ["model"]
        controller.start()
        self.assertEqual(self.clients, [])
        self.assertIn("what talking needs", controller.status()[0])

    def test_a_wake_hands_its_audio_to_the_talk(self) -> None:
        controller = self.started()
        self.heard(preroll=3200, speaking=True)
        self.assertEqual(len(self.talk.wakes), 1)
        handed = self.talk.wakes[0]
        self.assertEqual(handed["preroll"], bytes([7]) * 3200)
        self.assertEqual((handed["device"], handed["default"], handed["speaking"]), (MIC, True, True))
        stream = handed["stream"]
        self.assertEqual(stream.read_available(), bytes([9]) * 1600)
        self.assertEqual(controller._store.counts()["wakes"], 1)
        stream.close()                                                   # the talk ended
        self.assertEqual(self.client.sent[-1], {"handoff_end": True})

    def test_the_talk_is_handed_nothing_until_the_pre_roll_is_here(self) -> None:
        self.started()
        self.client.on_line({"heard": True, "score": 0.9, "speaking": False, "preroll_bytes": 6400})
        self.client.on_line(audio_line(3200))
        self.assertEqual(self.talk.wakes, [])
        self.client.on_line(audio_line(3200))
        self.assertEqual(len(self.talk.wakes), 1)

    def test_a_wake_while_paused_in_control_or_refused_by_the_talk_is_let_go(self) -> None:
        for setup in ("held", "control", "refused"):
            with self.subTest(setup):
                controller = self.started()
                if setup == "held":
                    self.holds.append(wake_ui.HOLD_MEETING)
                    controller.refresh_holds()
                elif setup == "control":
                    self.control[0] = True
                else:
                    self.talk.accept = False
                self.heard()
                if setup != "refused":
                    self.assertEqual(self.talk.wakes, [])
                self.assertEqual(self.client.sent[-1], {"handoff_end": True})
                self.assertEqual(controller._store.counts()["wakes"], 0)

    def test_pauses_hold_at_once_and_listen_again_after_the_tail(self) -> None:
        controller = self.started()
        self.holds[:] = [wake_ui.HOLD_SPEAKING]
        controller.refresh_holds()
        self.assertEqual(self.client.sent[-1], {"hold": True})
        self.assertEqual(controller.status(), ("“Hey Jarvis” paused while he speaks", wake_ui.LOOK_PAUSED))
        self.holds[:] = [wake_ui.HOLD_SPEAKING, wake_ui.HOLD_MEETING]
        controller.refresh_holds()
        self.assertEqual(sum(1 for sent in self.client.sent if sent == {"hold": True}), 1)
        self.assertIn("while he speaks and while a meeting records", controller.status()[0])
        self.holds[:] = []
        controller.refresh_holds()
        self.assertTrue(controller._tail_timer.isActive())
        self.assertNotEqual(self.client.sent[-1], {"hold": False})       # not yet: his last word's echo
        controller._release_hold()
        self.assertEqual(self.client.sent[-1], {"hold": False})
        self.assertEqual(controller.status()[1], wake_ui.LOOK_LISTENING)

    def test_the_talk_s_own_state_pauses_it(self) -> None:
        controller = self.started()
        self.holds[:] = [wake_ui.HOLD_TALK]
        self.talk.stateChanged.emit(talk_ui.LISTENING)
        self.assertEqual(self.client.sent[-1], {"hold": True})
        self.assertEqual(controller.status()[1], wake_ui.LOOK_PAUSED)

    def test_a_held_listener_that_starts_again_is_held_at_once(self) -> None:
        controller = self.started()
        self.holds[:] = [wake_ui.HOLD_FOCUS]
        controller.refresh_holds()
        self.client.on_line(None)                                         # it ended by itself
        self.assertTrue(controller._restart_timer.isActive())
        controller._restart_timer.stop()
        controller._apply()
        self.client.on_line({"ready": True, "load_ms": 200})
        self.assertIn({"hold": True}, self.client.sent)

    def test_a_listener_that_ends_by_itself_is_started_again_then_stopped(self) -> None:
        controller = self.started()
        for count in range(len(wake_ui.RESTART_DELAYS_S)):
            self.client.on_line(None)
            self.assertTrue(controller._restart_timer.isActive())
            self.assertEqual(controller._restart_timer.interval(), int(wake_ui.RESTART_DELAYS_S[count] * 1000))
            controller._restart_timer.stop()
            controller._apply()
            self.client.on_line({"ready": True, "load_ms": 1})
        self.client.on_line(None)
        self.assertFalse(controller._restart_timer.isActive())
        self.assertIn("kept failing", controller.status()[0])
        clients = len(self.clients)
        controller._apply()
        self.assertEqual(len(self.clients), clients)                      # stopped until turned off and on
        controller.set_on(False)
        controller.set_on(True)
        self.assertEqual(len(self.clients), clients + 1)

    def test_a_long_good_run_counts_the_failures_afresh(self) -> None:
        controller = self.started()
        self.client.on_line(None)
        controller._restart_timer.stop()
        controller._apply()
        self.clock[0] += wake_ui.RESTART_RESET_S + 1
        self.client.on_line(None)
        self.assertEqual(controller._restart_timer.interval(), int(wake_ui.RESTART_DELAYS_S[0] * 1000))

    def test_a_model_or_add_on_the_listener_could_not_load_waits_for_the_owner(self) -> None:
        controller = self.make()
        controller.start()
        self.client.on_line({"ready": False, "error": wake.ERR_ADDON})
        self.assertTrue(self.client.closed)
        self.client.on_line(None)
        self.assertFalse(controller._restart_timer.isActive())
        self.assertIn("onnxruntime", controller.status()[0])

    def test_each_microphone_state_is_said_plainly(self) -> None:
        controller = self.started()
        cases = {
            "virtual_mic": ("virtual microphone", STEAM),
            "no_mic": ("no microphone", ""),
            "mic_busy": ("wouldn't open", MIC),
            "silent_mic": ("only silence", MIC),
            "audio_missing": ("PyAudioWPatch", ""),
        }
        for state, (words, mic) in cases.items():
            with self.subTest(state):
                self.client.on_line({"state": state, "mic": mic, "default": True})
                text, look = controller.status()
                self.assertIn(words, text)
                self.assertEqual(look, wake_ui.LOOK_PROBLEM)
                if state == "virtual_mic":
                    self.assertIn("Settings > Meetings > Microphone", text)
                    self.assertIn(STEAM, text)

    def test_a_newly_picked_microphone_reaches_the_listener(self) -> None:
        controller = self.started()
        self.picked[0] = MIC
        controller.mic_picked()
        self.assertEqual(self.client.sent[-1], {"listen": {"mic": MIC}})

    def test_the_sensitivity_reaches_the_listener_and_is_kept(self) -> None:
        controller = self.started()
        controller.set_sensitivity("more")
        self.assertEqual(self.client.sent[-1], {"sensitivity": "more"})
        self.assertEqual(dict(controller.settings_rows())["Sensitivity"], "Hears more")

    def test_wakes_that_heard_nothing_are_counted(self) -> None:
        controller = self.started()
        self.heard()
        self.talk.wakeTalkEnded.emit(talk_ui.WAKE_HEARD_NOTHING)
        self.assertEqual(controller._store.counts(), {"wakes": 1, "nothing": 1})
        self.assertEqual(dict(controller.settings_rows())["Today"], "1 wake, 1 of them heard nothing")

    def test_the_listener_going_away_mid_talk_ends_the_talk_s_stream(self) -> None:
        self.started()
        self.heard()
        stream = self.talk.wakes[0]["stream"]
        stream.read_available()
        self.client.on_line(None)
        with self.assertRaises(OSError):
            stream.read_available()

    def test_closing_the_app_ends_the_listener(self) -> None:
        controller = self.started()
        controller.shutdown()
        self.assertTrue(self.client.closed)
        self.client.on_line({"state": "listening", "mic": MIC, "default": True})   # a late line changes nothing

    def test_the_panel_and_the_rows(self) -> None:
        controller = self.make(model=False)
        panel = wake_ui.WakePanel()
        controller.attach_panel(panel)
        controller.start()
        self.assertEqual(panel.switch.text(), "Turn off “Hey Jarvis”")
        self.assertFalse(panel.download.isHidden())
        self.assertTrue(panel.sensitivity_buttons["balanced"].isChecked())
        rows = dict(controller.settings_rows())
        self.assertIn("not downloaded", rows["Wake model"])
        panel.sensitivity_buttons["fewer"].click()
        self.assertEqual(controller._store.sensitivity, "fewer")
        panel.download.click()
        self.assertTrue(panel.download.isHidden())
        panel.switch.click()
        self.assertFalse(controller.on)
        self.assertEqual(panel.switch.text(), "Turn on “Hey Jarvis”")

    def test_the_tray_line(self) -> None:
        controller = self.started()
        self.assertEqual(controller.tray_text(), "Jarvis: listening for “Hey Jarvis”")
        self.client.on_line({"state": "virtual_mic", "mic": STEAM, "default": True})
        self.assertIn("can't hear you", controller.tray_text())
        controller.set_on(False)
        self.assertEqual(controller.tray_text(), "")

    def test_its_logs_never_name_the_microphone(self) -> None:
        with self.assertLogs("briefing_reader.wake_ui", logging.INFO) as logs:
            controller = self.started()
            self.client.on_line({"state": "virtual_mic", "mic": STEAM, "default": True})
            self.heard()
            controller.shutdown()
        self.assertFalse(any("FIFINE" in line or "Steam" in line for line in logs.output))


class AppHookTests(unittest.TestCase):
    def test_no_talk_no_wake(self) -> None:
        self.assertIsNone(ui.AppController._create_wake(SimpleNamespace(talk=None)))

    def test_what_pauses_it(self) -> None:
        def stub(**kwargs):
            values = dict(talk=SimpleNamespace(state=talk_ui.IDLE), voice=SimpleNamespace(speaking=False),
                          player=SimpleNamespace(state=ui.IDLE), meetings=SimpleNamespace(is_recording=lambda: False),
                          _focus_quiet=lambda: False)
            values.update(kwargs)
            return SimpleNamespace(**values)

        self.assertEqual(ui.AppController._wake_holds(stub()), [])
        cases = {
            wake_ui.HOLD_TALK: dict(talk=SimpleNamespace(state=talk_ui.UNDERSTANDING)),
            wake_ui.HOLD_SPEAKING: dict(voice=SimpleNamespace(speaking=True)),
            wake_ui.HOLD_MEETING: dict(meetings=SimpleNamespace(is_recording=lambda: True)),
            wake_ui.HOLD_FOCUS: dict(_focus_quiet=lambda: True),
        }
        for hold, values in cases.items():
            with self.subTest(hold):
                self.assertEqual(ui.AppController._wake_holds(stub(**values)), [hold])
        self.assertEqual(ui.AppController._wake_holds(stub(player=SimpleNamespace(state=ui.PLAYING))),
                         [wake_ui.HOLD_SPEAKING])                         # reading the briefing
        self.assertEqual(ui.AppController._wake_holds(stub(meetings=None, voice=None)), [])

    def test_in_control_and_the_tray_mark(self) -> None:
        stub = SimpleNamespace(_hud_active=lambda: True, desktop=SimpleNamespace(in_control=lambda: True))
        self.assertTrue(ui.AppController._wake_in_control(stub))
        stub._hud_active = lambda: False
        self.assertFalse(ui.AppController._wake_in_control(stub))
        for look, mark in ((wake_ui.LOOK_LISTENING, "listening"), (wake_ui.LOOK_PROBLEM, "problem"),
                           (wake_ui.LOOK_PAUSED, ""), (wake_ui.LOOK_OFF, "")):
            stub = SimpleNamespace(wake=SimpleNamespace(look=lambda look=look: look))
            self.assertEqual(ui.AppController._wake_tray_mark(stub), mark)
        self.assertEqual(ui.AppController._wake_tray_mark(SimpleNamespace(wake=None)), "")

    def test_the_tray_icon_s_mark(self) -> None:
        plain = ui.app_icon().pixmap(64, 64).toImage()
        listening = ui.app_icon("listening").pixmap(64, 64).toImage()
        problem = ui.app_icon("problem").pixmap(64, 64).toImage()
        self.assertNotEqual(plain, listening)
        self.assertNotEqual(listening, problem)
        self.assertEqual(plain, ui.app_icon("paused").pixmap(64, 64).toImage())

    def test_the_reading_view_s_marks_and_the_hud(self) -> None:
        badges = [mock.Mock(), mock.Mock()]
        stub = SimpleNamespace(_wake=("", wake_ui.LOOK_OFF), wake_badge=badges[0], _wake_badges=[badges[1]],
                               header=SimpleNamespace(_fit_notes=mock.Mock()), glanceChanged=mock.Mock())
        ui.ReadingView.set_wake(stub, "Listening", wake_ui.LOOK_LISTENING)
        for badge in badges:
            badge.set_state.assert_called_once_with("Listening", wake_ui.LOOK_LISTENING)
        stub.glanceChanged.emit.assert_called_once_with()
        ui.ReadingView.set_wake(stub, "Listening", wake_ui.LOOK_LISTENING)   # unchanged: nothing again
        stub.glanceChanged.emit.assert_called_once_with()
        self.assertEqual(ui.ReadingView.wake_state(stub), ("Listening", wake_ui.LOOK_LISTENING))
        self.assertEqual(hud_overlay.WAKE_CHIP_TEXTS[wake_ui.LOOK_LISTENING], "Hey Jarvis")


if __name__ == "__main__":
    unittest.main()
