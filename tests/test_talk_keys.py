"""The talk key: the hotkey agent's third key ([talk] key, registered only while [talk] is on), the message it
sends a running app (TALK_ACTIVATION) and the app's handling of it (AppController._talk_activation). Like
test_hotkey, nothing reaches the real user32 or kernel32 (FakeWin32), and the app is a stand-in."""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest import mock

from briefing_reader import __main__ as entry
from briefing_reader import hotkey, ui
from briefing_reader.hotkey import MOD_ALT, MOD_CONTROL, run_agent
from tests.test_hotkey import LOGGER, FakeWin32, NoRealCase

SLOTS = {"AM": "10:12", "PM": "23:42"}
SPACE = 0x20


class TalkKeyAgentTests(NoRealCase):
    def agent(self, api: FakeWin32, **kwargs: object) -> tuple[int, list[str]]:
        presses: list[str] = []
        kwargs = {key: (lambda value=value: presses.append(value)) if key.startswith("on_") else value
                  for key, value in kwargs.items()}
        with mock.patch.object(hotkey, "_api", return_value=api):
            rc = run_agent("ctrl+alt+j", lambda: presses.append("window"), mutex_name="Local\\briefing-reader-fake",
                           **kwargs)
        return rc, presses

    def test_the_talk_key_is_a_third_id_its_presses_are_its_own_and_it_is_released(self) -> None:
        api = FakeWin32(messages=(hotkey.TALK_HOTKEY_ID, hotkey.HOTKEY_ID, hotkey.HUD_HOTKEY_ID, hotkey.TALK_HOTKEY_ID))
        with self.assertLogs(LOGGER, level="INFO") as logs:
            rc, presses = self.agent(api, hud_combo="ctrl+alt+h", on_hud_press="hud",
                                     talk_combo="ctrl+alt+space", on_talk_press="talk")
        self.assertEqual((rc, presses), (0, ["talk", "window", "hud", "talk"]))
        self.assertIn(("RegisterHotKey", hotkey.TALK_HOTKEY_ID, MOD_CONTROL | MOD_ALT | hotkey.MOD_NOREPEAT, SPACE),
                      api.named("RegisterHotKey"))
        self.assertIn(("UnregisterHotKey", hotkey.TALK_HOTKEY_ID), api.named("UnregisterHotKey"))
        self.assertIn("Listening for the talk key Ctrl+Alt+Space", "\n".join(logs.output))

    def test_without_the_talk_key_nothing_new_is_registered(self) -> None:
        api = FakeWin32(messages=(hotkey.TALK_HOTKEY_ID, hotkey.HOTKEY_ID))
        with self.assertLogs(LOGGER, level="INFO"):
            rc, presses = self.agent(api)
        self.assertEqual((rc, presses), (0, ["window"]))                 # a talk id that is not ours is no press
        self.assertEqual([call[1] for call in api.named("RegisterHotKey")], [hotkey.HOTKEY_ID])

    def test_a_talk_key_that_is_another_key_or_not_valid_is_not_registered(self) -> None:
        for talk_combo in ("ctrl+alt+j", "Alt+Ctrl+H", "ctrl+alt+tab", "space"):
            with self.subTest(talk_combo=talk_combo):
                api = FakeWin32(messages=(hotkey.HOTKEY_ID,))
                with self.assertLogs(LOGGER, level="INFO") as logs:
                    rc, presses = self.agent(api, hud_combo="ctrl+alt+h", on_hud_press="hud",
                                             talk_combo=talk_combo, on_talk_press="talk")
                self.assertEqual((rc, presses), (0, ["window"]))
                self.assertNotIn(hotkey.TALK_HOTKEY_ID, [call[1] for call in api.named("RegisterHotKey")])
                self.assertTrue(any("talk key" in line and "WARNING" in line for line in logs.output))

    def test_a_talk_key_another_program_holds_leaves_the_window_key_working(self) -> None:
        api = FakeWin32(messages=(hotkey.HOTKEY_ID, hotkey.TALK_HOTKEY_ID), refuse=(hotkey.TALK_HOTKEY_ID,))
        with self.assertLogs(LOGGER, level="INFO") as logs:
            rc, presses = self.agent(api, talk_combo="ctrl+alt+space", on_talk_press="talk")
        self.assertEqual((rc, presses), (0, ["window"]))
        self.assertIn("Could not register the talk key Ctrl+Alt+Space", "\n".join(logs.output))
        self.assertNotIn(("UnregisterHotKey", hotkey.TALK_HOTKEY_ID), api.named("UnregisterHotKey"))


class TalkKeyMainTests(unittest.TestCase):
    def config(self, enabled: object = True, key: str = "ctrl+alt+space", desktop: bool = False) -> SimpleNamespace:
        return SimpleNamespace(hotkey=SimpleNamespace(enabled=True, combo="ctrl+alt+j"),
                               desktop=SimpleNamespace(enabled=desktop, hud_key="ctrl+alt+h"),
                               talk=SimpleNamespace(enabled=enabled, key=key))

    def test_the_message(self) -> None:
        self.assertEqual(entry.TALK_ACTIVATION, {"cmd": "activate", "run": None, "now": False, "open": True,
                                                 "talk": True})

    def test_with_talk_off_the_agent_registers_nothing_new(self) -> None:
        for config in (self.config(enabled=False), self.config(enabled="yes"), self.config(key="")):
            with self.subTest(config=config), mock.patch.object(hotkey, "run_agent", return_value=0) as agent:
                entry._run_hotkey_agent(config, SLOTS)
            self.assertEqual(agent.call_args.kwargs, {})

    def test_with_talk_on_each_tap_sends_the_talk_message(self) -> None:
        with mock.patch.object(hotkey, "run_agent", return_value=0) as agent, \
                self.assertLogs("briefing_reader.main", level="INFO") as logs:
            entry._run_hotkey_agent(self.config(desktop=True), SLOTS)
        self.assertIn("the talk key is listened for too", "\n".join(logs.output))
        kwargs = agent.call_args.kwargs
        self.assertEqual(sorted(kwargs), ["hud_combo", "on_hud_press", "on_talk_press", "talk_combo"])
        self.assertEqual(kwargs["talk_combo"], "ctrl+alt+space")
        talk = kwargs["on_talk_press"]
        self.assertIsInstance(talk, hotkey.PressDispatcher)
        talk._spawn = lambda work: work()
        clock = [100.0]
        talk._clock = lambda: clock[0]
        name = entry._instance_name()
        with mock.patch.object(hotkey, "mutex_exists", return_value=True), \
                mock.patch.object(hotkey, "send_activation", return_value=True) as send, \
                self.assertLogs("briefing_reader.hotkey", level="INFO"):
            self.assertEqual(talk(), "dispatched")
            clock[0] += 0.5                                   # the second tap, half a second later, counts
            self.assertEqual(talk(), "dispatched")
            clock[0] += 0.1                                   # a bounce does not
            self.assertEqual(talk(), "ignored")
        self.assertEqual(send.call_args_list, [mock.call(name, entry.TALK_ACTIVATION)] * 2)

    def test_with_no_app_running_the_talk_key_opens_jarvis(self) -> None:
        with mock.patch.object(hotkey, "run_agent", return_value=0) as agent:
            entry._run_hotkey_agent(self.config(), SLOTS)
        talk = agent.call_args.kwargs["on_talk_press"]
        talk._spawn = lambda work: work()
        with mock.patch.object(hotkey, "mutex_exists", return_value=False), \
                mock.patch.object(entry, "_start_detached", return_value=True) as start, \
                mock.patch.object(entry, "_windowless_python", return_value="pythonw.exe"), \
                self.assertLogs("briefing_reader.hotkey", level="INFO"):
            talk()
        start.assert_called_once_with(["--open", "--slots", "am=10:12,pm=23:42"], executable="pythonw.exe")


class TalkActivationTests(unittest.TestCase):
    """AppController.handle_activation and _talk_activation on a stand-in app."""

    def app(self, *, state: str = ui.STATE_READING, talk: object = None, in_control: bool = False) -> SimpleNamespace:
        app = SimpleNamespace(state=state, talk=talk, _hud_active=lambda: in_control,
                              desktop=SimpleNamespace(in_control=lambda: in_control),
                              _activation_while_waiting=mock.Mock(), _open_activation=mock.Mock(),
                              _desktop_activation=mock.Mock())
        app._talk_activation = lambda: ui.AppController._talk_activation(app)
        return app

    def activate(self, app: SimpleNamespace, message: dict) -> None:
        with self.assertLogs("briefing_reader.ui", logging.INFO):
            ui.AppController.handle_activation(app, message)

    def test_the_talk_message_toggles_the_talk(self) -> None:
        talk = mock.Mock()
        app = self.app(talk=talk)
        self.activate(app, dict(entry.TALK_ACTIVATION))
        talk.toggle.assert_called_once_with()
        app._open_activation.assert_not_called()
        app._desktop_activation.assert_not_called()

    def test_with_no_talk_controller_it_opens_jarvis_as_the_window_key_does(self) -> None:
        app = self.app()
        self.activate(app, dict(entry.TALK_ACTIVATION))
        app._open_activation.assert_called_once_with(ask=False)        # its keyword is required (review, finding 2)
        waiting = self.app(state=ui.STATE_WAITING)
        self.activate(waiting, dict(entry.TALK_ACTIVATION))
        waiting._activation_while_waiting.assert_called_once_with(ui.LAUNCH_OPEN, None)

    def test_while_jarvis_is_in_control_it_is_ignored(self) -> None:
        talk = mock.Mock()
        app = self.app(talk=talk, in_control=True)
        self.activate(app, dict(entry.TALK_ACTIVATION))
        talk.toggle.assert_not_called()
        app._open_activation.assert_not_called()

    def test_while_quitting_nothing_happens(self) -> None:
        talk = mock.Mock()
        app = self.app(state=ui.STATE_QUITTING, talk=talk)
        self.activate(app, dict(entry.TALK_ACTIVATION))
        talk.toggle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
