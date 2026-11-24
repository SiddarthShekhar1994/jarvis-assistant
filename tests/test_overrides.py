"""Settings changed in the app (overrides.py): settings.json merged into config.toml's values before the parsers.

No Qt. Every file lives in a temporary folder; the repo's config.toml is read, never written (checked here)."""

from __future__ import annotations

import inspect
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from briefing_reader import __main__ as entry
from briefing_reader import config, overrides
from briefing_reader.config import load_config

_REPO_CONFIG = config.PROJECT_ROOT / config.CONFIG_FILE_NAME
_REPO_BYTES = _REPO_CONFIG.read_bytes()

# A value other than the code default for every editable key (and inside the parser's limits).
CHANGED = {
    "voice.rate": "+10%", "voice.volume": "-20%", "voice.keep_warm_minutes": 30, "voice.quiet_in_calls": False,
    "assistant.speak": False, "assistant.greet": False, "assistant.greeting_context": False,
    "assistant.speak_replies": False, "assistant.speak_results": False, "assistant.scheduled_prompt": True,
    "display.clock": "24h", "ui.motion": "calm", "live.auto_open": "window",
    "productivity.quiet_from": "21:30", "productivity.quiet_until": "07:15", "productivity.prep_lead_minutes": 25,
    "instant.temperature": "celsius", "talk.quiet_ms": 800, "talk.keep_warm_minutes": 20,
}


def tearDownModule() -> None:
    if _REPO_CONFIG.read_bytes() != _REPO_BYTES:
        raise AssertionError("the repo's config.toml changed during test_overrides")


class Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.local = self.root / "local"
        self.environ = {"NOTION_TOKEN": "", "LOCALAPPDATA": str(self.local)}
        self.file = overrides.settings_file(self.local / "briefing-reader")

    def write(self, values: dict, version: int = 1) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({"version": version, "values": values}), encoding="utf-8")

    def load(self, root: Path | None = None, **extra: str) -> config.Config:
        return load_config(root or self.root, environ={**self.environ, **extra})


class EditableKeysTests(Case):
    def test_the_list_holds_only_comfort_settings(self) -> None:
        self.assertEqual(set(overrides.EDITABLE), set(CHANGED))
        for key, spec in overrides.EDITABLE.items():
            table, name = overrides.split(key)
            with self.subTest(key=key):
                self.assertNotEqual(name, "enabled")
                self.assertNotIn(table, ("accounts", "actions", "ask", "research", "agent", "standing", "desktop",
                                         "schedule", "hotkey", "calendar", "agenda", "meetings", "docs"))
                self.assertNotIn(name, ("engine", "speak_private", "key", "combo", "hud_key", "model", "read_mail",
                                        "weather", "currency", "calendar", "plan_offer", "prep_offers"))
                self.assertIn(spec.kind, overrides.KINDS)
                self.assertNotEqual(CHANGED[key], overrides.default(key), "the test value differs from the default")

    def test_every_key_round_trips_through_load_config(self) -> None:
        store = overrides.SettingsStore(self.file)
        self.assertTrue(store.update(CHANGED))
        saved = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(saved, {"version": 1, "values": CHANGED})
        with self.assertNoLogs("briefing_reader.config", level="WARNING"):
            cfg = self.load()
        self.assertEqual(cfg.overridden, frozenset(CHANGED))
        for key, value in CHANGED.items():
            with self.subTest(key=key):
                self.assertEqual(overrides.current(cfg, key), value)
                self.assertEqual(cfg.toml_values[key], overrides.default(key))   # no config.toml: the defaults
        hash(cfg)                                                                # still hashable

    def test_no_file_and_a_corrupt_file_are_config_toml_as_today(self) -> None:
        (self.root / "config.toml").write_text('[voice]\nrate = "-10%"\n[display]\nclock = "24h"\n', encoding="utf-8")
        plain = self.load()
        self.assertEqual(plain.overridden, frozenset())
        for text in ("{not json", '["a list"]', '{"version": 1, "values": 3}', b"\xff\xfe\x00".decode("latin-1")):
            with self.subTest(text=text[:12]):
                self.file.parent.mkdir(parents=True, exist_ok=True)
                self.file.write_text(text, encoding="utf-8")
                with self.assertLogs("briefing_reader.overrides", level="WARNING"):
                    cfg = self.load()
                self.assertEqual((cfg.voice, cfg.display, cfg.overridden), (plain.voice, plain.display, frozenset()))
        self.write({"voice.rate": "+30%"}, version=2)                          # a newer app's file
        with self.assertLogs("briefing_reader.overrides", level="WARNING") as logs:
            self.assertEqual(self.load().voice.rate, "-10%")
        self.assertIn("another version", "\n".join(logs.output))

    def test_settings_json_wins_over_config_toml(self) -> None:
        (self.root / "config.toml").write_text('[voice]\nrate = "-10%"\nvolume = "+5%"\n[talk]\nquiet_ms = 5000\n'
                                               '[ui]\nmotion = false\n', encoding="utf-8")
        self.write({"voice.rate": "+20%", "ui.motion": "lively"})
        cfg = self.load()
        self.assertEqual((cfg.voice.rate, cfg.voice.volume, cfg.ui.motion), ("+20%", "+5%", "lively"))
        self.assertEqual(cfg.overridden, frozenset({"voice.rate", "ui.motion"}))
        self.assertEqual(dict(cfg.toml_values), {"voice.rate": "-10%", "ui.motion": "off"})
        # config.toml's value as its parser reads it: clamped like the parser clamps it
        doc = {"talk": {"quiet_ms": 5000}, "voice": {"rate": "+400%"}, "ui": {"motion": True}}
        self.assertEqual(overrides.toml_value("talk.quiet_ms", doc), cfg.talk.quiet_ms)
        self.assertEqual(overrides.toml_value("voice.rate", doc), "+100%")
        self.assertEqual(overrides.toml_value("ui.motion", doc), "lively")
        self.assertEqual(overrides.toml_value("live.auto_open", {}), "tab")

    def test_a_bad_value_is_left_out_with_a_warning_naming_only_the_key(self) -> None:
        self.write({"voice.rate": "LOUDVALUE%", "talk.quiet_ms": 9000, "display.clock": "SECRETCLOCK",
                    "assistant.speak": "yes", "productivity.quiet_from": "25:00"})
        with self.assertLogs("briefing_reader.overrides", level="WARNING") as logs:
            cfg = self.load()
        text = "\n".join(logs.output)
        for key in ("voice.rate", "talk.quiet_ms", "display.clock", "assistant.speak", "productivity.quiet_from"):
            self.assertIn(key, text)
        for value in ("LOUDVALUE", "9000", "SECRETCLOCK", "yes", "25:00"):
            self.assertNotIn(value, text)
        self.assertEqual((cfg.voice.rate, cfg.talk.quiet_ms, cfg.display.clock, cfg.assistant.speak,
                          cfg.productivity.quiet_from), ("+0%", 500, "12h", True, "22:00"))
        self.assertEqual(cfg.overridden, frozenset())

    def test_an_enabled_or_schedule_key_is_ignored_and_env_still_wins(self) -> None:
        self.write({"talk.enabled": True, "schedule.am": "05:00", "ask.model": "opus", "instant.weather": False,
                    "agent.enabled": True, "x" * 300: 1, "voice.rate": "+5%"})
        with self.assertLogs("briefing_reader.overrides", level="WARNING") as logs:
            cfg = self.load(JARVIS_TALK_ENABLED="false")
        text = "\n".join(logs.output)
        self.assertIn("talk.enabled", text)
        self.assertIn("schedule.am", text)
        self.assertIn("an unknown entry", text)                    # a key of no known shape is not echoed
        self.assertNotIn("xxxx", text)
        self.assertFalse(cfg.talk.enabled)
        self.assertEqual(cfg.schedule.am, config.ScheduleConfig().am)
        self.assertEqual((cfg.ask.model, cfg.instant.weather, cfg.agent.enabled), ("sonnet", True, False))
        self.assertEqual(cfg.overridden, frozenset({"voice.rate"}))
        with self.assertLogs("briefing_reader.overrides", level="WARNING"):
            self.assertTrue(self.load(JARVIS_TALK_ENABLED="true").talk.enabled)

    def test_a_table_config_toml_holds_as_a_value_keeps_its_warning(self) -> None:
        (self.root / "config.toml").write_text('voice = "loud"\n', encoding="utf-8")
        self.write({"voice.rate": "+5%"})
        with self.assertLogs("briefing_reader", level="WARNING") as logs:
            cfg = self.load()
        self.assertIn("should be a [voice] table", "\n".join(logs.output))
        self.assertEqual((cfg.voice.rate, cfg.overridden), ("+0%", frozenset()))

    def test_the_repo_config_toml_is_read_never_written(self) -> None:
        self.write({"voice.rate": "+15%", "display.clock": "24h"})
        cfg = self.load(config.PROJECT_ROOT)
        self.assertEqual((cfg.voice.rate, cfg.display.clock), ("+15%", "24h"))
        store = overrides.SettingsStore(self.file)
        self.assertTrue(store.update({"talk.quiet_ms": 700}))
        self.assertTrue(store.reset(["voice.rate"]))
        self.assertEqual(self.load(config.PROJECT_ROOT).overridden, frozenset({"display.clock", "talk.quiet_ms"}))
        self.assertEqual(_REPO_CONFIG.read_bytes(), _REPO_BYTES)

    def test_every_process_loads_the_same_values(self) -> None:
        # The app, the hotkey agent and a standing run all start in __main__._run, which loads the config once
        # (load_config) before it branches; two loads see the same values.
        source = inspect.getsource(entry._run)
        load_at = source.index("config = load_config()")
        self.assertLess(load_at, source.index("_run_standing(config"))
        self.assertLess(load_at, source.index("_run_hotkey_agent(config"))
        self.write({"voice.rate": "+10%", "talk.quiet_ms": 900})
        first, second = self.load(), self.load()
        self.assertEqual((first.voice, first.talk, first.overridden), (second.voice, second.talk, second.overridden))

    def test_the_owners_data_folder_is_never_read_in_tests(self) -> None:
        self.assertFalse(overrides.readable(Path("C:/Users/Owner/AppData/Local/briefing-reader/settings.json")))
        self.assertTrue(overrides.readable(self.file))
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read")):
            self.assertEqual(overrides.load(Path("C:/Users/Owner/AppData/Local/briefing-reader/settings.json")), {})
        with mock.patch.dict("os.environ", {overrides.TEST_GUARD_ENV: ""}):
            self.assertTrue(overrides.readable(Path("C:/Users/Owner/AppData/Local/briefing-reader/settings.json")))


class FixRoundTests(Case):
    """The review's findings on parts 3-5 (each fails without its fix)."""

    def test_a_deeply_nested_file_never_stops_a_start(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        text = '{"version": 1, "values": {"voice.rate": ' + "[" * 30000 + "]" * 30000 + "}}"
        self.assertLess(len(text), overrides.MAX_BYTES)
        self.file.write_text(text, encoding="utf-8")
        with self.assertLogs("briefing_reader.overrides", level="WARNING") as logs:
            cfg = self.load()                                                 # no RecursionError
        self.assertIn("is not readable JSON", "\n".join(logs.output))
        self.assertEqual((cfg.voice.rate, cfg.overridden), ("+0%", frozenset()))

    def test_a_big_file_is_never_read_whole(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_bytes(b" " * (overrides.MAX_BYTES * 3))
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read whole")), \
                self.assertLogs("briefing_reader.overrides", level="WARNING") as logs:
            self.assertEqual(overrides.load(self.file), {})
        self.assertIn("too big", "\n".join(logs.output))

    def test_save_never_writes_outside_the_temporary_folder_in_tests(self) -> None:
        owner = Path("C:/Users/Owner/AppData/Local/briefing-reader/settings.json")
        with mock.patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
                mock.patch.object(overrides.tempfile, "mkstemp", side_effect=AssertionError("mkstemp")), \
                self.assertLogs("briefing_reader.overrides", level="WARNING"):
            self.assertFalse(overrides.save(owner, {"voice.rate": "+5%"}))

    def test_the_store_reads_the_file_afresh(self) -> None:
        store = overrides.SettingsStore(self.file)
        self.assertTrue(store.update({"voice.rate": "+5%"}))
        self.file.unlink()                                                    # deleted while Jarvis runs
        self.assertEqual(store.values(), {})
        self.assertTrue(store.update({"talk.quiet_ms": 700}))
        self.assertEqual(overrides.load(self.file), {"talk.quiet_ms": 700})   # the old +5% is not written back
        self.write({"talk.quiet_ms": 700, "display.clock": "24h"})            # edited by hand meanwhile
        self.assertTrue(store.reset(["talk.quiet_ms"]))
        self.assertEqual(overrides.load(self.file), {"display.clock": "24h"})

    def test_a_file_of_another_version_is_kept_aside_before_a_save(self) -> None:
        self.write({"voice.rate": "+30%", "future.key": 1}, version=2)
        before = self.file.read_bytes()
        store = overrides.SettingsStore(self.file)
        with mock.patch.object(overrides, "_backup", return_value=False):
            self.assertFalse(store.update({"voice.volume": "-5%"}))          # no copy: nothing replaces it
        self.assertEqual(self.file.read_bytes(), before)
        with self.assertLogs("briefing_reader.overrides", level="WARNING") as logs:
            self.assertTrue(store.update({"voice.volume": "-5%"}))
        self.assertIn("kept as settings.json.bak", "\n".join(logs.output))
        self.assertEqual(self.file.with_name("settings.json.bak").read_bytes(), before)
        self.assertEqual(overrides.load(self.file), {"voice.volume": "-5%"})
        self.assertTrue(store.reset(["voice.volume"]))


class StoreTests(Case):
    def test_update_and_reset_log_key_names_only(self) -> None:
        store = overrides.SettingsStore(self.file)
        self.assertEqual(store.values(), {})
        with self.assertLogs("briefing_reader.overrides", level="INFO") as logs:
            self.assertTrue(store.update({"voice.rate": "+35%", "productivity.quiet_from": "9:05"}))
            self.assertTrue(store.reset(["voice.rate", "talk.quiet_ms"]))
        self.assertEqual(store.values(), {"productivity.quiet_from": "09:05"})
        text = "\n".join(logs.output)
        self.assertIn("Settings: voice.rate changed here", text)
        self.assertIn("Settings: voice.rate back to config.toml", text)
        self.assertNotIn("35", text)
        self.assertNotIn("09:05", text)
        self.assertNotIn("talk.quiet_ms", text)                                 # it was not there
        self.assertEqual(overrides.SettingsStore(self.file).values(), {"productivity.quiet_from": "09:05"})

    def test_a_value_out_of_limits_is_refused(self) -> None:
        store = overrides.SettingsStore(self.file)
        for key, value in (("voice.rate", "+150%"), ("talk.quiet_ms", 100), ("display.clock", "36h"),
                           ("assistant.speak", 1), ("talk.enabled", True), ("voice.keep_warm_minutes", 2.5)):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    store.update({key: value})
        self.assertFalse(self.file.exists())

    def test_a_failed_write_changes_nothing(self) -> None:
        store = overrides.SettingsStore(self.file)
        self.assertTrue(store.update({"voice.rate": "+5%"}))
        with mock.patch.object(overrides.atomic, "replace", side_effect=PermissionError(13, "denied")), \
                self.assertLogs("briefing_reader.overrides", level="WARNING") as logs:
            self.assertFalse(store.update({"voice.rate": "+25%"}))
            self.assertFalse(store.reset(["voice.rate"]))
        self.assertIn("could not be saved (PermissionError)", "\n".join(logs.output))
        self.assertEqual(store.values(), {"voice.rate": "+5%"})
        self.assertEqual(overrides.load(self.file), {"voice.rate": "+5%"})
        self.assertEqual([p.name for p in self.file.parent.iterdir()], ["settings.json"])   # no temporary left

    def test_the_file_holds_only_editable_keys(self) -> None:
        self.assertTrue(overrides.save(self.file, {"voice.rate": "+5%", "talk.enabled": True, "ask.model": "x"}))
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")),
                         {"version": 1, "values": {"voice.rate": "+5%"}})
        self.assertNotIn(b"\r", self.file.read_bytes())


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
