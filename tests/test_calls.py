"""Tests for calls.py (smaller items part 6): another app using the microphone, read from a fake registry reader
(never the real registry: the real reader refuses under the test guard and default_reader gives none). Packaged and
NonPackaged keys are read; Jarvis's own programs are left out; a stop time set is not a call; a malformed key is not
a call and its warning names no app; a short gap is held before "over"; the standing check's busy probe includes the
call (and only with quiet_in_calls on), so a check during a call opens nothing; logs hold no app's name."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from briefing_reader import calls
from briefing_reader.config import AskConfig, StandingConfig, load_config
from briefing_reader.standing import runner, schedule

OWN = r"C:\Users\Owner\AppData\Local\Programs\Python\Python313\pythonw.exe"
OTHER = r"C:\Program Files\Callapp\callapp.exe"
STORE = "Contoso.Meet_8wekyb3d8bbwe"


def key_name(path: str) -> str:
    return path.replace("\\", "#")


class FakeReader:
    """The ConsentStore\\microphone key as a dict: {path: values}, paths below it; NonPackaged's children."""

    def __init__(self, packaged: dict | None = None, non_packaged: dict | None = None, *,
                 broken: set[str] | None = None) -> None:
        self.tree: dict[str, dict] = {}
        for name, values in (packaged or {}).items():
            self.tree[name] = values
        self.non_packaged = {key_name(path): values for path, values in (non_packaged or {}).items()}
        self.broken = broken or set()
        self.asked: list[str] = []

    def subkeys(self, path: str) -> list[str]:
        self.asked.append("subkeys:" + path)
        if path in self.broken:
            raise OSError(2, "gone")
        if path == "":
            return [*self.tree, calls.NON_PACKAGED]
        if path == calls.NON_PACKAGED:
            return list(self.non_packaged)
        return []

    def values(self, path: str) -> dict:
        self.asked.append("values:" + path)
        if path in self.broken:
            raise OSError(2, "gone")
        if path.startswith(calls.NON_PACKAGED + "\\"):
            return dict(self.non_packaged[path.split("\\", 1)[1]])
        return dict(self.tree[path])


def using(start: int = 133_000_000_000_000_000) -> dict:
    return {calls.START: start, calls.STOP: 0}


def stopped() -> dict:
    return {calls.START: 133_000_000_000_000_000, calls.STOP: 133_000_000_100_000_000}


OWN_SET = frozenset({calls.key_name(OWN)})
BOOT = 133_000_000_000_000_000 - 10_000_000 * 3600        # an hour before using()'s start


class DetectTests(unittest.TestCase):
    def test_a_packaged_app_using_the_microphone(self) -> None:
        self.assertTrue(calls.mic_in_use_by_others(FakeReader({STORE: using()}), OWN_SET))
        self.assertFalse(calls.mic_in_use_by_others(FakeReader({STORE: stopped()}), OWN_SET))   # a stop time set

    def test_a_non_packaged_program_using_it(self) -> None:
        self.assertTrue(calls.mic_in_use_by_others(FakeReader(non_packaged={OTHER: using()}), OWN_SET))
        self.assertFalse(calls.mic_in_use_by_others(FakeReader(non_packaged={OTHER: stopped()}), OWN_SET))

    def test_jarvis_s_own_programs_are_left_out(self) -> None:
        reader = FakeReader(non_packaged={OWN: using()})
        self.assertFalse(calls.mic_in_use_by_others(reader, OWN_SET))
        self.assertNotIn("values:" + calls.NON_PACKAGED + "\\" + key_name(OWN), reader.asked)   # not even read
        upper = FakeReader(non_packaged={OWN.upper(): using()})                                # any case
        self.assertFalse(calls.mic_in_use_by_others(upper, OWN_SET))
        both = FakeReader(non_packaged={OWN: using(), OTHER: using()})
        self.assertTrue(calls.mic_in_use_by_others(both, OWN_SET))

    def test_own_exes_names_this_python_and_its_siblings(self) -> None:
        own = calls.own_exes()
        folder = Path(sys.executable).parent
        for name in ("python.exe", "pythonw.exe"):
            self.assertIn(key_name(str(folder / name)).casefold(), own)

    def test_a_hash_in_the_python_folder_still_leaves_jarvis_out(self) -> None:
        odd = r"C:\Tools#2\Python313\pythonw.exe"
        with mock.patch.object(sys, "executable", odd), mock.patch.object(sys, "_base_executable", odd, create=True):
            own = calls.own_exes()
        self.assertFalse(calls.mic_in_use_by_others(FakeReader(non_packaged={odd: using()}), own))
        self.assertTrue(calls.mic_in_use_by_others(FakeReader(non_packaged={odd: using(), OTHER: using()}), own))

    def test_no_stop_time_yet_is_in_use(self) -> None:
        self.assertTrue(calls.mic_in_use_by_others(FakeReader({STORE: {calls.START: 133_000_000_000_000_000}}),
                                                   OWN_SET))

    def test_an_entry_from_before_the_last_start_is_not_a_call(self) -> None:
        reader = FakeReader({STORE: using()})
        self.assertTrue(calls.mic_in_use_by_others(reader, OWN_SET, boot=lambda: BOOT))
        later_boot = 133_000_000_000_000_000 + 10_000_000          # the PC started after that entry began
        self.assertFalse(calls.mic_in_use_by_others(reader, OWN_SET, boot=lambda: later_boot))
        self.assertTrue(calls.mic_in_use_by_others(reader, OWN_SET, boot=mock.Mock(side_effect=OSError("x"))))

    def test_never_started_is_not_a_call(self) -> None:
        self.assertFalse(calls.mic_in_use_by_others(FakeReader({STORE: {calls.START: 0, calls.STOP: 0}}), OWN_SET))

    def test_a_malformed_key_is_not_a_call_and_its_warning_names_no_app(self) -> None:
        cases = [FakeReader({STORE: {calls.START: "soon", calls.STOP: 0}}),
                 FakeReader({STORE: {calls.STOP: 0}}),
                 FakeReader({STORE: {calls.START: True, calls.STOP: False}}),
                 FakeReader({STORE: using()}, broken={STORE}),
                 FakeReader(non_packaged={OTHER: using()}, broken={calls.NON_PACKAGED})]
        for reader in cases:
            with self.subTest(asked=len(reader.asked)), self.assertLogs("briefing_reader.calls", "WARNING") as logs:
                self.assertFalse(calls.mic_in_use_by_others(reader, OWN_SET))
            text = "\n".join(logs.output)
            self.assertIn("could not be read", text)
            for word in ("Contoso", "Callapp", "callapp", "Program Files", "#"):
                self.assertNotIn(word, text)

    def test_a_malformed_key_does_not_hide_another_app_in_use(self) -> None:
        reader = FakeReader({STORE: {calls.START: "x"}}, non_packaged={OTHER: using()})
        with self.assertLogs("briefing_reader.calls", "WARNING"):
            self.assertTrue(calls.mic_in_use_by_others(reader, OWN_SET))

    def test_no_list_at_all_is_not_a_call(self) -> None:
        with self.assertLogs("briefing_reader.calls", "WARNING"):
            self.assertFalse(calls.mic_in_use_by_others(FakeReader(broken={""}), OWN_SET))


class RealReaderTests(unittest.TestCase):
    def test_the_real_registry_is_unreachable_under_the_test_guard(self) -> None:
        self.assertEqual(os.environ.get(calls.TEST_GUARD_ENV), "1")
        self.assertIsNone(calls.default_reader())
        trap = mock.MagicMock(side_effect=AssertionError("the registry was opened"))
        fake_winreg = SimpleNamespace(OpenKey=trap, HKEY_CURRENT_USER=0, KEY_READ=0x20019)
        with mock.patch.dict(sys.modules, {"winreg": fake_winreg}):
            with self.assertRaises(calls.GuardError):
                calls.WinregReader().subkeys("")
            with self.assertRaises(calls.GuardError):
                calls.WinregReader().values(STORE)
            with self.assertLogs("briefing_reader.calls", "WARNING"):
                self.assertFalse(calls.mic_in_use_by_others(calls.WinregReader(), OWN_SET))
            self.assertFalse(calls.mic_in_use_by_others())                  # the default: no reader at all
            self.assertFalse(calls.CallWatch().poll())
        trap.assert_not_called()

    def test_the_boot_time_is_never_asked_under_the_test_guard(self) -> None:
        self.assertIsNone(calls.default_boot())
        trap = mock.MagicMock(side_effect=AssertionError("Windows was asked"))
        with mock.patch.dict(sys.modules, {"ctypes": SimpleNamespace(windll=trap, c_ulonglong=int)}):
            with self.assertRaises(calls.GuardError):
                calls.real_boot_filetime()
            with self.assertLogs("briefing_reader.calls", "WARNING"):
                self.assertFalse(calls.mic_in_use_by_others(FakeReader({STORE: using()}), OWN_SET,
                                                            boot=calls.real_boot_filetime))
        trap.assert_not_called()

    def test_the_real_reader_only_opens_keys_for_reading(self) -> None:
        source = Path(calls.__file__).read_text(encoding="utf-8")
        for word in ("CreateKey", "SetValue", "DeleteKey", "DeleteValue", "KEY_WRITE", "KEY_ALL_ACCESS",
                     "KEY_SET_VALUE", "SetSystem", "SystemParametersInfo"):
            self.assertFalse(word in source, word)
        self.assertIn("winreg.KEY_READ", source)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class WatchTests(unittest.TestCase):
    def test_on_at_once_over_after_the_hold_and_a_gap_is_held(self) -> None:
        clock, busy = Clock(), [False]
        watch = calls.CallWatch(lambda: busy[0], clock=clock)
        self.assertEqual((watch.poll_s, watch.hold_s), (3.0, 6.0))
        self.assertIsNone(watch.poll())
        busy[0] = True
        with self.assertLogs("briefing_reader.calls", "INFO") as logs:
            self.assertEqual(watch.poll(), calls.ON)
        self.assertEqual(logs.output, ["INFO:briefing_reader.calls:Calls: quiet (another app is using the microphone)"])
        self.assertTrue(watch.on_call)
        clock.now += 3
        self.assertIsNone(watch.poll())
        busy[0] = False                              # a short gap (the call app reopens its microphone)
        clock.now += 3
        self.assertIsNone(watch.poll())
        clock.now += 3
        self.assertIsNone(watch.poll())
        busy[0] = True
        clock.now += 3
        self.assertIsNone(watch.poll())              # flapping held: still the same call
        self.assertTrue(watch.on_call)
        busy[0] = False
        clock.now += 3
        free_at = clock.now
        self.assertIsNone(watch.poll())
        clock.now += 3
        self.assertIsNone(watch.poll())
        clock.now = free_at + 6
        with self.assertLogs("briefing_reader.calls", "INFO") as logs:
            self.assertEqual(watch.poll(), calls.OFF)
        self.assertEqual(logs.output, ["INFO:briefing_reader.calls:Calls: over after 0 min"])
        self.assertFalse(watch.on_call)
        self.assertIsNone(watch.poll())

    def test_the_minutes_of_a_long_call(self) -> None:
        clock, busy = Clock(), [True]
        watch = calls.CallWatch(lambda: busy[0], clock=clock)
        watch.poll()
        clock.now += 25 * 60
        busy[0] = False
        watch.poll()
        clock.now += 6
        with self.assertLogs("briefing_reader.calls", "INFO") as logs:
            self.assertEqual(watch.poll(), calls.OFF)
        self.assertIn("over after 25 min", logs.output[0])

    def test_a_failing_probe_is_not_a_call_and_stop_ends_one(self) -> None:
        watch = calls.CallWatch(mock.Mock(side_effect=OSError("x")))
        self.assertIsNone(watch.poll())
        self.assertFalse(watch.on_call)
        watch = calls.CallWatch(lambda: True)
        watch.poll()
        self.assertEqual(watch.stop(), calls.OFF)
        self.assertIsNone(watch.stop())


class StandingTests(unittest.TestCase):
    def test_the_busy_probe_includes_the_call_only_with_the_switch_on(self) -> None:
        self.assertTrue(schedule.busy(True, windows=lambda: False, call=lambda: True))
        self.assertFalse(schedule.busy(False, windows=lambda: False, call=lambda: True))
        self.assertTrue(schedule.busy(False, windows=lambda: True, call=lambda: False))
        self.assertFalse(schedule.busy(True, windows=lambda: False, call=lambda: False))
        self.assertFalse(schedule.busy(True, windows=lambda: False, call=mock.Mock(side_effect=OSError("x"))))
        self.assertFalse(schedule.busy(True))        # the real probes: nothing is read while the tests run

    def test_a_check_during_a_call_opens_nothing(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config = SimpleNamespace(data_dir=Path(tmp.name), standing=StandingConfig(enabled=True),
                                 ask=AskConfig(enabled=True), accounts={})
        on_call = [True]
        started: list[bool] = []
        check = runner._Check(config, runner.Deps(
            readers={}, engine=None,
            clock=lambda: datetime(2026, 11, 12, 10, 0).astimezone(), app_running=lambda: False,
            start_app=lambda: started.append(True) or True,
            busy=lambda: schedule.busy(True, windows=lambda: False, call=lambda: on_call[0])), runner.ORIGIN_TASK)
        check.state.to_announce("run-1")
        check.tell()
        self.assertEqual((started, check.result.told), ([], []))
        on_call[0] = False
        check.tell()                                 # kept for the next check: told once the call is over
        self.assertEqual((started, check.result.told), ([True], ["run-1"]))

    def test_the_real_deps_probe_follows_the_setting(self) -> None:
        source = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertIn('schedule.busy(bool(getattr(getattr(config, "voice", None), "quiet_in_calls", True)))', source)


class ConfigTests(unittest.TestCase):
    def test_quiet_in_calls_defaults_on_and_can_be_turned_off(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        environ = {"LOCALAPPDATA": str(root / "local")}
        self.assertTrue(load_config(root, environ=environ).voice.quiet_in_calls)
        (root / "config.toml").write_text("[voice]\nquiet_in_calls = false\n", encoding="utf-8")
        self.assertFalse(load_config(root, environ=environ).voice.quiet_in_calls)


if __name__ == "__main__":
    unittest.main()
