"""Tests for briefing_reader.hotkey: combos, the press dispatcher and the agent loop.

The dispatcher is tested with fakes. The Windows tests use an unusual combo
(Ctrl+Alt+Shift+F24, which no keyboard has) and a private mutex name, release
the hotkey within seconds, never press a key and never start the app. The
pipe test talks to a QLocalServer in a separate Python process.
"""

from __future__ import annotations

import ctypes
import json
import logging
import subprocess
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path

from briefing_reader import hotkey
from briefing_reader.hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    PressDispatcher,
    agent_mutex_name,
    describe_combo,
    normalize_combo,
    parse_combo,
    run_agent,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGGER = "briefing_reader.hotkey"
TEST_COMBO = "ctrl+alt+shift+F24"
WINDOWS = sys.platform == "win32"


# --------------------------------------------------------------------------
# Combos
# --------------------------------------------------------------------------

class ParseComboTests(unittest.TestCase):
    def test_valid_combos(self) -> None:
        cases = {
            "ctrl+alt+j": (MOD_CONTROL | MOD_ALT, 0x4A),
            "Ctrl + Alt + J": (MOD_CONTROL | MOD_ALT, 0x4A),
            "alt+ctrl+j": (MOD_CONTROL | MOD_ALT, 0x4A),
            "win+shift+5": (MOD_WIN | MOD_SHIFT, 0x35),
            "ctrl+0": (MOD_CONTROL, 0x30),
            "control+windows+k": (MOD_CONTROL | MOD_WIN, 0x4B),
            "F24": (0, 0x87),
            "shift+f1": (MOD_SHIFT, 0x70),
            "ctrl+alt+shift+F24": (MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x87),
            "win+F9": (MOD_WIN, 0x78),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_combo(text), expected)

    def test_invalid_combos(self) -> None:
        bad = ["", "   ", "j", "shift+j", "shift+5", "ctrl+alt", "ctrl", "ctrl+alt+jj", "ctrl++j",
               "+j", "ctrl+alt+", "ctrl+ctrl+j", "control+ctrl+j", "ctrl+alt+F25", "ctrl+alt+F0",
               "hyper+j", "ctrl+alt+-", "ctrl+alt+\u00e9", "ctrl+alt+space", None, 5]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_combo(text)  # type: ignore[arg-type]

    def test_normalize_and_describe(self) -> None:
        self.assertEqual(normalize_combo(" Alt + Ctrl + J "), "ctrl+alt+j")
        self.assertEqual(normalize_combo("WIN+shift+f9"), "shift+win+f9")
        self.assertEqual(describe_combo("ctrl+alt+j"), "Ctrl+Alt+J")
        self.assertEqual(describe_combo("shift+ctrl+f12"), "Ctrl+Shift+F12")
        self.assertEqual(describe_combo("nonsense"), "nonsense")
        with self.assertRaises(ValueError):
            normalize_combo("j")

    def test_agent_mutex_name(self) -> None:
        self.assertEqual(agent_mutex_name("john.doe"), "Local\\briefing-reader-hotkey-johndoe")
        self.assertEqual(agent_mutex_name(""), "Local\\briefing-reader-hotkey-user")
        self.assertTrue(agent_mutex_name().startswith("Local\\briefing-reader-hotkey-"))


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------

class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class DispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.calls: list[str] = []
        self.running = False
        self.activate_ok = True
        self.start_ok = True

    def make(self) -> PressDispatcher:
        def is_app_running() -> bool:
            self.calls.append("running?")
            return self.running

        def activate() -> bool:
            self.calls.append("activate")
            return self.activate_ok

        def start_app() -> bool:
            self.calls.append("start")
            return self.start_ok

        return PressDispatcher(is_app_running=is_app_running, activate=activate, start_app=start_app,
                               clock=self.clock, spawn=lambda work: work())

    def test_running_app_is_activated(self) -> None:
        dispatcher = self.make()
        self.running = True
        with self.assertLogs(LOGGER, level="INFO") as captured:
            self.assertEqual(dispatcher(), "dispatched")
        self.assertEqual(self.calls, ["running?", "activate"])
        self.assertEqual(dispatcher.last_outcome, "activated")
        self.assertIn("asked the running app", "\n".join(captured.output))

    def test_app_is_started_when_not_running(self) -> None:
        dispatcher = self.make()
        with self.assertLogs(LOGGER, level="INFO"):
            dispatcher()
        self.assertEqual(self.calls, ["running?", "start"])
        self.assertEqual(dispatcher.last_outcome, "started")

    def test_presses_within_two_seconds_are_ignored(self) -> None:
        dispatcher = self.make()
        with self.assertLogs(LOGGER, level="INFO"):
            self.assertEqual(dispatcher(), "dispatched")
        self.clock.now += 1.0
        self.assertEqual(dispatcher(), "ignored")
        self.clock.now += 0.99
        self.assertEqual(dispatcher(), "ignored")
        self.clock.now += 0.01      # 2.0 s after the accepted press
        with self.assertLogs(LOGGER, level="INFO"):
            self.assertEqual(dispatcher(), "dispatched")
        self.assertEqual(self.calls.count("start"), 2)

    def test_a_running_app_that_does_not_answer_is_not_started_twice(self) -> None:
        dispatcher = self.make()
        self.running, self.activate_ok = True, False
        with self.assertLogs(LOGGER, level="WARNING"):
            dispatcher()
        self.assertEqual(self.calls, ["running?", "activate"])
        self.assertEqual(dispatcher.last_outcome, "failed")

    def test_start_failure_is_logged(self) -> None:
        dispatcher = self.make()
        self.start_ok = False
        with self.assertLogs(LOGGER, level="WARNING"):
            dispatcher()
        self.assertEqual(dispatcher.last_outcome, "failed")

    def test_errors_never_escape(self) -> None:
        def broken() -> bool:
            raise OSError("secret-looking detail")

        dispatcher = PressDispatcher(is_app_running=broken, activate=broken, start_app=broken,
                                     clock=self.clock, spawn=lambda work: work())
        with self.assertLogs(LOGGER, level="ERROR") as captured:
            dispatcher()
        self.assertEqual(dispatcher.last_outcome, "failed")
        self.assertNotIn("secret-looking detail", "\n".join(captured.output))

    def test_default_spawn_runs_the_work_on_a_thread(self) -> None:
        done = threading.Event()
        names: list[str] = []

        def start_app() -> bool:
            names.append(threading.current_thread().name)
            done.set()
            return True

        dispatcher = PressDispatcher(is_app_running=lambda: False, activate=lambda: True,
                                     start_app=start_app)
        with self.assertLogs(LOGGER, level="INFO"):
            dispatcher()
            self.assertTrue(done.wait(5))
            time.sleep(0.05)
        self.assertEqual(names, ["hotkey-press"])


# --------------------------------------------------------------------------
# Windows: the real agent loop, mutexes and the activation pipe
# --------------------------------------------------------------------------

def _user32():
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    return user32


class AgentThread:
    """run_agent on its own thread with a private mutex; always stopped by the test."""

    def __init__(self, combo: str = TEST_COMBO) -> None:
        self.mutex_name = f"Local\\briefing-reader-hotkey-test-{uuid.uuid4().hex}"
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.pressed = threading.Event()
        self.presses = 0
        self.thread_id = 0
        self.rc: int | None = None
        self.thread = threading.Thread(target=self._run, args=(combo,), name="test-hotkey-agent",
                                       daemon=True)

    def _run(self, combo: str) -> None:
        self.rc = run_agent(combo, self._on_press, self.stop, mutex_name=self.mutex_name,
                            on_ready=self._on_ready)
        self.ready.set()   # also when registration failed

    def _on_ready(self) -> None:
        self.thread_id = threading.get_native_id()
        self.ready.set()

    def _on_press(self) -> None:
        self.presses += 1
        self.pressed.set()

    def start(self) -> AgentThread:
        self.thread.start()
        if not self.ready.wait(5):
            self.close()
            raise AssertionError("the hotkey agent did not start")
        return self

    def close(self) -> None:
        self.stop.set()
        self.thread.join(5)


@unittest.skipUnless(WINDOWS, "the global hotkey needs Windows")
class AgentTests(unittest.TestCase):
    def test_registers_dispatches_and_releases_the_hotkey(self) -> None:
        with self.assertLogs(LOGGER, level="INFO") as captured:
            agent = AgentThread().start()
            try:
                if agent.rc == 1:
                    self.skipTest("Ctrl+Alt+Shift+F24 is registered by another program")
                self.assertIsNone(agent.rc)
                self.assertTrue(agent.thread_id)
                # A WM_HOTKEY as Windows would post it, without pressing anything.
                user32 = _user32()
                self.assertTrue(user32.PostThreadMessageW(agent.thread_id, hotkey.WM_HOTKEY,
                                                          hotkey.HOTKEY_ID, 0))
                self.assertTrue(agent.pressed.wait(5))
                # Another id is not ours.
                user32.PostThreadMessageW(agent.thread_id, hotkey.WM_HOTKEY, hotkey.HOTKEY_ID + 1, 0)
                # A second agent of the same user exits at once.
                self.assertEqual(run_agent(TEST_COMBO, lambda: None, mutex_name=agent.mutex_name), 0)
            finally:
                started = time.monotonic()
                agent.close()
            self.assertFalse(agent.thread.is_alive())
            self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(agent.rc, 0)
        self.assertEqual(agent.presses, 1)
        text = "\n".join(captured.output)
        self.assertIn("Listening for the hotkey Ctrl+Alt+Shift+F24", text)
        self.assertIn("Another hotkey agent is already running", text)
        self.assertIn("released", text)
        # Released: registering the same combo again works (and is undone at once).
        modifiers, vk = parse_combo(TEST_COMBO)
        user32 = _user32()
        self.assertTrue(user32.RegisterHotKey(None, 0x4A53, modifiers | hotkey.MOD_NOREPEAT, vk))
        self.assertTrue(user32.UnregisterHotKey(None, 0x4A53))

    def test_combo_in_use_is_a_warning_and_exit_code_1(self) -> None:
        modifiers, vk = parse_combo(TEST_COMBO)
        user32 = _user32()
        if not user32.RegisterHotKey(None, 0x4A54, modifiers, vk):
            self.skipTest("Ctrl+Alt+Shift+F24 is registered by another program")
        try:
            with self.assertLogs(LOGGER, level="WARNING") as captured:
                agent = AgentThread().start()
                agent.close()
        finally:
            user32.UnregisterHotKey(None, 0x4A54)
        self.assertEqual(agent.rc, 1)
        self.assertIn("another program probably uses it", "\n".join(captured.output))

    def test_stop_before_the_loop_starts(self) -> None:
        agent = AgentThread()
        agent.stop.set()
        with self.assertLogs(LOGGER, level="INFO"):
            agent.thread.start()
            agent.thread.join(5)
        self.assertFalse(agent.thread.is_alive())
        self.assertIn(agent.rc, (0, 1))   # 1 only if another program holds the combo

    def test_invalid_combo_exits_with_2(self) -> None:
        with self.assertLogs(LOGGER, level="WARNING"):
            self.assertEqual(run_agent("shift+j", lambda: None, mutex_name="Local\\unused-test"), 2)

    def test_mutex_exists(self) -> None:
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        name = f"Local\\briefing-reader-test-{uuid.uuid4().hex}"
        self.assertFalse(hotkey.mutex_exists(name))
        handle = kernel32.CreateMutexW(None, False, name)
        try:
            self.assertTrue(hotkey.mutex_exists(name))
        finally:
            kernel32.CloseHandle(handle)
        self.assertFalse(hotkey.mutex_exists(name))

    def test_send_activation_without_a_listener(self) -> None:
        name = f"briefing-reader-test-{uuid.uuid4().hex}"
        started = time.monotonic()
        self.assertFalse(hotkey.send_activation(name, {"cmd": "activate"}, timeout_s=0.3))
        self.assertLess(time.monotonic() - started, 3)

    def test_send_activation_reaches_the_apps_activation_server(self) -> None:
        # The receiving side is the app's own QLocalServer, in a separate process (Qt).
        name = f"briefing-reader-test-{uuid.uuid4().hex}"
        server_code = f"""
import json, sys
from PySide6.QtCore import QCoreApplication, QTimer
from briefing_reader.activation import ActivationServer
app = QCoreApplication([])
server = ActivationServer({name!r})
got = []
def handle(message):
    got.append(message)
    QTimer.singleShot(200, app.quit)
server.set_handler(handle)
assert server.listen()
print("listening", flush=True)
QTimer.singleShot(15000, app.quit)
app.exec()
server.close()
print(json.dumps(got), flush=True)
"""
        proc = subprocess.Popen([sys.executable, "-c", server_code], cwd=PROJECT_ROOT, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "listening")
            message = {"cmd": "activate", "run": None, "now": True}
            self.assertTrue(hotkey.send_activation(name, message, timeout_s=5))
            out, err = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        self.assertEqual(proc.returncode, 0, err[-2000:])
        self.assertEqual(json.loads(out.strip().splitlines()[-1]), [message])


class ModuleHygieneTests(unittest.TestCase):
    def test_source_is_ascii(self) -> None:
        self.assertTrue(Path(hotkey.__file__).read_bytes().isascii())

    def test_non_windows_calls_are_harmless(self) -> None:
        if WINDOWS:
            self.skipTest("only meaningful elsewhere")
        self.assertFalse(hotkey.mutex_exists("x"))
        self.assertFalse(hotkey.send_activation("x", {}))
        self.assertEqual(run_agent("ctrl+alt+j", lambda: None), 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
