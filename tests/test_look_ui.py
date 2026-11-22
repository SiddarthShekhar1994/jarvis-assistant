"""Tests for briefing_reader.look_ui: the new look's controller (part 1: the soft sounds' moments and Settings > Look).

The sounds never reach an audio device: the controller's board gets a recording player (or, in the app, the test
guard keeps it silent).
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from briefing_reader import config as config_mod, hud, look_ui, prefs, sounds, talk_ui
from briefing_reader.actions import STATUS_CREATED, STATUS_DENIED, STATUS_FAILED, STATUS_SENT
from briefing_reader.player import PLAYING
from tests.ui_fakes import AppHarness

_app = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_look_ui", "-platform", "offscreen"])
    hud.load_fonts(config_mod.PROJECT_ROOT / "fonts")


class RecordingPlayer:
    def __init__(self) -> None:
        self.played: list[str] = []

    def play(self, name: str) -> bool:
        self.played.append(name)
        return True


class FakeHost:
    """What LookController reads from the app."""

    def __init__(self, root: Path, *, sounds_value: str = "soft") -> None:
        self.config = SimpleNamespace(data_dir=root, look=config_mod.LookConfig(sounds=sounds_value))
        self.window = None
        self.voice = SimpleNamespace(muted=False, speaking=False)
        self.productivity = None
        self.ask = SimpleNamespace(busy=False)
        self.player = SimpleNamespace(state="stopped")
        self._utterance = ""
        self.quiet_until = ""
        self.focus = self.meeting = self.locked = False
        self._prefs = prefs.AssistantPrefs(root / prefs.PREFS_FILE)

    def _prefs_store(self) -> prefs.AssistantPrefs:
        return self._prefs

    def _quiet_until_text(self) -> str:
        return self.quiet_until

    def _focus_quiet(self) -> bool:
        return self.focus

    def _meeting_recording(self) -> bool:
        return self.meeting

    def _locked_now(self) -> bool:
        return self.locked


class ControllerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def make(self, **kw) -> tuple[look_ui.LookController, FakeHost, RecordingPlayer]:
        host = FakeHost(self.root, **kw)
        player = RecordingPlayer()
        controller = look_ui.LookController(host, player_factory=lambda _paths, _owner: player, test_guard=False,
                                            start_sounds=False)
        controller.sounds.use_files({})
        controller.sounds._visible = lambda: True        # no window in these tests
        return controller, host, player


class MomentTests(ControllerTestCase):
    def test_heard_plays_once_as_listening_opens(self) -> None:
        controller, _host, player = self.make()
        controller.on_talk_state(talk_ui.LISTENING)
        controller.on_talk_state(talk_ui.LISTENING)
        controller.on_talk_state(talk_ui.UNDERSTANDING)
        self.assertEqual(player.played, [sounds.HEARD])

    def test_heard_skips_the_wake_chime(self) -> None:
        controller, _host, player = self.make()
        controller.on_wake_chime()
        controller.on_talk_state(talk_ui.LISTENING)
        self.assertEqual(player.played, [])

    def test_waits_only_when_more_cards_wait_than_before(self) -> None:
        controller, _host, player = self.make()
        controller.on_pending(2)          # the baseline: what was there at start is not new
        controller.on_pending(2)
        controller.on_pending(1)
        self.assertEqual(player.played, [])
        controller.on_pending(3)
        self.assertEqual(player.played, [sounds.WAITS])

    def test_done_and_failed(self) -> None:
        controller, _host, player = self.make()
        controller.on_action_result(STATUS_DENIED, False)
        self.assertEqual(player.played, [])
        controller.on_action_result(STATUS_CREATED, False)
        controller.sounds._last.clear()
        controller.sounds._last_any = -1e9
        controller.on_action_result(STATUS_FAILED, True)
        self.assertEqual(player.played, [sounds.DONE, sounds.FAILED])
        controller.sounds._last.clear()
        controller.sounds._last_any = -1e9
        controller.on_action_result(STATUS_SENT, False)
        self.assertEqual(player.played[-1], sounds.DONE)

    def wait_ms(self, ms: int) -> None:
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)

    def test_thinking_after_400_ms_of_silence(self) -> None:
        controller, host, player = self.make()
        host.ask.busy = True
        controller.on_ask_started()
        self.wait_ms(300)
        self.assertEqual(player.played, [])
        self.wait_ms(250)
        self.assertEqual(player.played, [sounds.THINKING])

    def test_no_thinking_once_words_came(self) -> None:
        controller, host, player = self.make()
        host.ask.busy = True
        controller.on_ask_started()
        controller.on_ask_answer()
        self.wait_ms(550)
        self.assertEqual(player.played, [])

    def test_no_thinking_when_the_request_already_ended(self) -> None:
        controller, host, player = self.make()
        controller.on_ask_started()
        self.wait_ms(550)
        self.assertEqual(player.played, [])


class QuietTests(ControllerTestCase):
    def test_each_quiet_reason(self) -> None:
        controller, host, player = self.make()
        self.assertEqual(controller._quiet(), "")
        cases = [("voice", "muted"), ("focus", "focus mode"), ("meeting", "a meeting records"), ("locked", "locked")]
        for attribute, reason in cases:
            with self.subTest(reason=reason):
                fresh, fresh_host, _player = self.make()
                if attribute == "voice":
                    fresh_host.voice.muted = True
                else:
                    setattr(fresh_host, attribute, True)
                self.assertEqual(fresh._quiet(), reason)

    def test_quiet_hours_count_only_with_the_bundle(self) -> None:
        controller, host, player = self.make()
        host.quiet_until = "8:00 AM"
        self.assertEqual(controller._quiet(), "")
        host.productivity = object()
        self.assertEqual(controller._quiet(), "quiet hours")

    def test_speech_and_the_briefing_hold_the_sounds(self) -> None:
        controller, host, player = self.make()
        host._utterance = "Three meetings today, sir."
        controller.on_speaking()
        controller.on_pending(0)
        controller.on_pending(1)
        self.assertEqual(player.played, [])
        host._utterance = ""
        host.player.state = PLAYING
        controller.on_speaking()
        controller.on_pending(2)
        self.assertEqual(player.played, [])


class SwitchTests(ControllerTestCase):
    def test_settings_switch_turns_the_sounds_off_on_this_pc(self) -> None:
        controller, host, player = self.make()
        self.assertTrue(controller.sounds_on())
        self.assertTrue(controller.set_sounds_on(False))
        self.assertFalse(controller.sounds_on())
        self.assertTrue(prefs.AssistantPrefs(self.root / prefs.PREFS_FILE).look.sounds_off)
        controller.on_pending(0)
        controller.on_pending(1)
        self.assertEqual(player.played, [])
        controller.set_sounds_on(True)
        self.assertTrue(controller.sounds_on())

    def test_sounds_off_build_nothing_and_turning_them_on_builds_them(self) -> None:
        # The final review's resilience 4 (canvas part 5A): no build and no player while the sounds are off.
        from unittest import mock

        host = FakeHost(self.root)
        host._prefs.set_sounds_off(True)                  # off on this PC (Settings > Look) before the start
        with mock.patch.object(sounds.SoundBoard, "start") as start:
            controller = look_ui.LookController(host, test_guard=False)
            self.assertEqual(start.call_count, 0)
            self.assertFalse(controller.sounds.ready)     # no player either
            controller.set_sounds_on(True)
            self.assertEqual(start.call_count, 1)         # built now
            controller.set_sounds_on(False)
            controller.set_sounds_on(True)
            self.assertEqual(start.call_count, 2)         # (SoundBoard.start itself builds once)
        with mock.patch.object(sounds.SoundBoard, "start") as start:
            look_ui.LookController(FakeHost(self.root / "config_off", sounds_value="off"), test_guard=False)
            self.assertEqual(start.call_count, 0)         # config.toml's off: never built
        with mock.patch.object(sounds.SoundBoard, "start") as start:
            look_ui.LookController(FakeHost(self.root / "on"), test_guard=False)
            self.assertEqual(start.call_count, 1)         # on: built at start, as before

    def test_config_off_wins(self) -> None:
        controller, host, player = self.make(sounds_value="off")
        controller.set_sounds_on(True)
        self.assertFalse(controller.sounds_on())
        self.assertIn((look_ui.SOUNDS_LABEL, look_ui.SOUNDS_OFF_CONFIG_VALUE), controller.settings_rows())


class AppTests(unittest.TestCase):
    def test_the_app_has_settings_look_and_its_switch(self) -> None:
        harness = AppHarness(self)
        app = harness.c
        self.assertIsNotNone(app.look)
        page = app.window.reading.settings_page
        self.assertIn(look_ui.SETTINGS_KEY, page.group_buttons)
        page.select_group(look_ui.SETTINGS_KEY)
        self.assertIn((look_ui.SOUNDS_LABEL, look_ui.SOUNDS_SOFT_VALUE), page.rows())
        panel = app.look.panel
        self.assertEqual(panel.button.text(), look_ui.TURN_OFF_TEXT)
        panel.button.click()
        self.assertEqual(panel.button.text(), look_ui.TURN_ON_TEXT)
        self.assertEqual(panel.line.text(), look_ui.SOUNDS_OFF_TEXT)
        self.assertIn((look_ui.SOUNDS_LABEL, look_ui.SOUNDS_OFF_VALUE), page.rows())
        self.assertTrue(app._prefs_store().look.sounds_off)

    def test_the_tests_never_play_a_sound(self) -> None:
        harness = AppHarness(self)
        look = harness.c.look
        self.assertEqual(look.sounds.play(sounds.DONE), sounds.R_TESTS)
        self.assertFalse(look.sounds.ready)               # nothing was even built


if __name__ == "__main__":
    unittest.main()
