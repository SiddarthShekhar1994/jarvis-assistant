"""Tests for briefing_reader.runstate: slots, the catch-up and answer windows, and runstate.json.

Every test writes into a temporary folder; the real
%LOCALAPPDATA%\\briefing-reader\\runstate.json is never read or written.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from briefing_reader import runstate
from briefing_reader.runstate import (
    ANSWER_MAX_AGE,
    CATCH_UP_MAX_AGE,
    DEFAULT_SLOTS,
    RUNSTATE_FILE,
    RunState,
    Slot,
    format_slots,
    handled_slot_key,
    normalize_slots,
    parse_slots,
    parse_time_of_day,
    slot_for,
    slot_key_date,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGGER = "briefing_reader.runstate"
SLOTS = {"AM": "10:12", "PM": "23:42"}
PDT = timezone(timedelta(hours=-7), "PDT")


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute)


# --------------------------------------------------------------------------
# Slot times
# --------------------------------------------------------------------------

class SlotTimeTests(unittest.TestCase):
    def test_parse_time_of_day(self) -> None:
        self.assertEqual(parse_time_of_day("10:12").strftime("%H:%M"), "10:12")
        self.assertEqual(parse_time_of_day(" 9:05 ").strftime("%H:%M"), "09:05")
        self.assertEqual(parse_time_of_day("00:00").strftime("%H:%M"), "00:00")
        for bad in ("24:00", "10:60", "1012", "10.12", "10:12 PM", "", None, 1012):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_time_of_day(bad)  # type: ignore[arg-type]

    def test_parse_slots(self) -> None:
        self.assertEqual(parse_slots("am=10:12,pm=23:42"), {"AM": "10:12", "PM": "23:42"})
        self.assertEqual(parse_slots(" AM = 9:05 , Pm=23:42 "), {"AM": "09:05", "PM": "23:42"})
        self.assertEqual(parse_slots("pm=18:05"), {"PM": "18:05"})

    def test_parse_slots_rejects_malformed_text(self) -> None:
        bad = ["", "  ", "am", "am=", "am=25:00", "noon=12:00", "am=10:12,am=11:00",
               "am=10:12;pm=23:42", "am=10:12,", "am:10:12", None]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_slots(text)  # type: ignore[arg-type]

    def test_format_slots_round_trip(self) -> None:
        self.assertEqual(format_slots({"PM": "23:42", "AM": "10:12"}), "am=10:12,pm=23:42")
        self.assertEqual(format_slots({"am": "7:30"}), "am=07:30")
        self.assertEqual(parse_slots(format_slots(DEFAULT_SLOTS)), dict(DEFAULT_SLOTS))
        self.assertEqual(format_slots({}), "")

    def test_normalize_slots(self) -> None:
        self.assertEqual(normalize_slots({"am": "9:05"}), {"AM": "09:05"})
        with self.assertRaises(ValueError):
            normalize_slots({"noon": "12:00"})
        with self.assertRaises(ValueError):
            normalize_slots({"AM": "nine"})


# --------------------------------------------------------------------------
# slot_for / handled_slot_key
# --------------------------------------------------------------------------

class SlotForTests(unittest.TestCase):
    def test_exactly_at_the_slot_counts(self) -> None:
        self.assertEqual(slot_for(at(5, 10, 12), SLOTS), Slot(run="AM", at=at(5, 10, 12)))

    def test_before_the_first_slot_of_the_day_nothing_is_due(self) -> None:
        self.assertIsNone(slot_for(at(5, 10, 11), SLOTS))   # last PM was 10 h 29 min ago
        self.assertIsNone(slot_for(at(5, 4, 0), SLOTS))

    def test_max_age_boundary(self) -> None:
        self.assertEqual(CATCH_UP_MAX_AGE, timedelta(hours=3))
        self.assertEqual(slot_for(at(5, 13, 12), SLOTS).key, "2026-10-05 AM")
        self.assertIsNone(slot_for(at(5, 13, 13), SLOTS))
        self.assertEqual(slot_for(at(5, 15, 0), SLOTS, max_age=timedelta(hours=6)).key,
                         "2026-10-05 AM")

    def test_pm_slot_across_midnight(self) -> None:
        slot = slot_for(at(5, 0, 30), SLOTS)
        self.assertEqual(slot, Slot(run="PM", at=at(4, 23, 42)))
        self.assertEqual(slot.key, "2026-10-04 PM")    # the date of the slot, not of now
        self.assertEqual(slot_for(at(5, 2, 42), SLOTS).key, "2026-10-04 PM")
        self.assertIsNone(slot_for(at(5, 2, 43), SLOTS))

    def test_same_evening(self) -> None:
        self.assertEqual(slot_for(at(4, 23, 50), SLOTS).key, "2026-10-04 PM")

    def test_across_month_and_year_end(self) -> None:
        self.assertEqual(slot_for(datetime(2027, 1, 1, 0, 5), SLOTS).key, "2026-12-31 PM")
        self.assertEqual(slot_for(at(1, 1, 0, month=11), SLOTS).key, "2026-10-31 PM")

    def test_a_slot_just_after_midnight_belongs_to_its_own_date(self) -> None:
        slots = {"AM": "08:00", "PM": "00:15"}
        self.assertEqual(slot_for(at(5, 0, 30), slots).key, "2026-10-05 PM")
        self.assertIsNone(slot_for(at(5, 0, 10), slots))    # before 00:15; yesterday's is 24 h old

    def test_only_the_latest_occurrence_counts(self) -> None:
        slots = {"AM": "10:00", "PM": "11:00"}
        self.assertEqual(slot_for(at(5, 11, 30), slots).run, "PM")
        self.assertEqual(slot_for(at(5, 10, 59), slots).run, "AM")

    def test_aware_now_keeps_its_time_zone(self) -> None:
        now = datetime(2026, 10, 5, 0, 30, tzinfo=PDT)
        slot = slot_for(now, SLOTS)
        self.assertEqual(slot.at, datetime(2026, 10, 4, 23, 42, tzinfo=PDT))
        self.assertEqual(slot.key, "2026-10-04 PM")

    def test_lowercase_runs_and_short_hours(self) -> None:
        self.assertEqual(slot_for(at(5, 9, 30), {"am": "9:05"}).key, "2026-10-05 AM")

    def test_unusable_slots_give_none(self) -> None:
        for slots in ({}, {"AM": "late"}, {"noon": "12:00"}):
            with self.subTest(slots=slots):
                self.assertIsNone(slot_for(at(5, 10, 30), slots))

    def test_one_slot_only(self) -> None:
        self.assertEqual(slot_for(at(5, 23, 45), {"PM": "23:42"}).key, "2026-10-05 PM")
        self.assertIsNone(slot_for(at(5, 11, 0), {"PM": "23:42"}))


class HandledSlotKeyTests(unittest.TestCase):
    def test_answer_after_midnight_settles_the_evening_slot(self) -> None:
        self.assertEqual(ANSWER_MAX_AGE, timedelta(hours=18))
        self.assertEqual(handled_slot_key(at(5, 0, 30), SLOTS), "2026-10-04 PM")
        self.assertEqual(handled_slot_key(at(5, 9, 0), SLOTS), "2026-10-04 PM")
        self.assertEqual(handled_slot_key(at(5, 10, 11), SLOTS), "2026-10-04 PM")

    def test_answer_during_the_day_settles_the_morning_slot(self) -> None:
        self.assertEqual(handled_slot_key(at(5, 10, 12), SLOTS), "2026-10-05 AM")
        self.assertEqual(handled_slot_key(at(5, 18, 0), SLOTS), "2026-10-05 AM")
        self.assertEqual(handled_slot_key(at(5, 23, 41), SLOTS), "2026-10-05 AM")
        self.assertEqual(handled_slot_key(at(5, 23, 42), SLOTS), "2026-10-05 PM")

    def test_no_slot_within_18_hours(self) -> None:
        self.assertIsNone(handled_slot_key(at(6, 4, 13), {"AM": "10:12"}))
        self.assertEqual(handled_slot_key(at(6, 4, 12), {"AM": "10:12"}), "2026-10-05 AM")
        self.assertIsNone(handled_slot_key(at(5, 12, 0), {}))

    def test_slot_key_date(self) -> None:
        self.assertEqual(slot_key_date("2026-10-04 PM"), date(2026, 10, 4))
        for bad in ("2026-10-04", "2026-10-04 pm", "2026-13-01 AM", "x", None):
            with self.subTest(bad=bad):
                self.assertIsNone(slot_key_date(bad))  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# RunState
# --------------------------------------------------------------------------

class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class RunStateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "data"
        self.path = self.dir / RUNSTATE_FILE
        self.clock = Clock(datetime(2026, 10, 5, 0, 30, tzinfo=PDT))

    def state(self) -> RunState:
        return RunState(self.path, clock=self.clock)

    def saved(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


class RunStatePersistenceTests(RunStateTestCase):
    def test_new_state_is_empty_and_writes_nothing(self) -> None:
        state = self.state()
        self.assertFalse(state.is_handled("2026-10-04 PM"))
        self.assertIsNone(state.get("2026-10-04 PM"))
        self.assertFalse(self.path.exists())

    def test_marks_are_saved_and_read_back(self) -> None:
        state = self.state()
        self.assertTrue(state.mark_shown("2026-10-04 PM"))
        self.assertFalse(state.is_handled("2026-10-04 PM"))
        self.clock.now += timedelta(minutes=5)
        self.assertTrue(state.mark_handled("2026-10-04 PM", "read"))
        self.assertEqual(self.saved(), {"2026-10-04 PM": {
            "shown_at": "2026-10-05T00:30:00-07:00", "handled_at": "2026-10-05T00:35:00-07:00",
            "how": "read"}})
        again = self.state()
        self.assertTrue(again.is_handled("2026-10-04 PM"))
        self.assertEqual(again.get("2026-10-04 PM")["how"], "read")
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_the_first_show_and_the_first_answer_win(self) -> None:
        state = self.state()
        state.mark_shown("2026-10-04 PM")
        self.clock.now += timedelta(minutes=10)
        self.assertFalse(state.mark_shown("2026-10-04 PM"))
        state.mark_handled("2026-10-04 PM", "dismissed")
        self.assertFalse(state.mark_handled("2026-10-04 PM", "done"))
        entry = self.state().get("2026-10-04 PM")
        self.assertEqual(entry["shown_at"], "2026-10-05T00:30:00-07:00")
        self.assertEqual(entry["how"], "dismissed")

    def test_handled_without_shown(self) -> None:
        self.state().mark_handled("2026-10-05 AM", "done")
        self.assertEqual(self.saved(), {"2026-10-05 AM": {
            "handled_at": "2026-10-05T00:30:00-07:00", "how": "done"}})

    def test_bad_arguments_raise(self) -> None:
        state = self.state()
        with self.assertRaises(ValueError):
            state.mark_handled("2026-10-04 PM", "later")
        for key in ("today", "2026-10-04", "2026-10-04 pm", ""):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    state.mark_shown(key)
                with self.assertRaises(ValueError):
                    state.mark_handled(key, "read")
        self.assertFalse(self.path.exists())

    def test_two_processes_do_not_lose_each_others_marks(self) -> None:
        first, second = self.state(), self.state()
        first.mark_shown("2026-10-04 PM")
        second.mark_handled("2026-10-05 AM", "read")
        first.mark_handled("2026-10-04 PM", "done")
        self.assertEqual(set(self.saved()), {"2026-10-04 PM", "2026-10-05 AM"})
        self.assertTrue(self.state().is_handled("2026-10-05 AM"))
        self.assertTrue(self.state().is_handled("2026-10-04 PM"))

    def test_bom_file_is_read(self) -> None:
        self.dir.mkdir(parents=True)
        self.path.write_text(json.dumps({"2026-10-04 PM": {"how": "read"}}), encoding="utf-8-sig")
        self.assertTrue(self.state().is_handled("2026-10-04 PM"))


class RunStateCorruptionTests(RunStateTestCase):
    def write_raw(self, text: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    def test_invalid_json_counts_as_empty_and_is_replaced(self) -> None:
        self.write_raw("{not json")
        with self.assertLogs(LOGGER, level="WARNING") as captured:
            state = self.state()
        self.assertIn("not valid JSON", "\n".join(captured.output))
        self.assertFalse(state.is_handled("2026-10-04 PM"))
        with self.assertLogs(LOGGER, level="WARNING"):
            state.mark_handled("2026-10-04 PM", "read")    # the re-read warns again
        self.assertEqual(self.saved()["2026-10-04 PM"]["how"], "read")

    def test_wrong_shapes_count_as_empty(self) -> None:
        for text in ("[]", '"text"', "3", "null"):
            with self.subTest(text=text):
                self.write_raw(text)
                with self.assertLogs(LOGGER, level="WARNING"):
                    self.assertFalse(self.state().is_handled("2026-10-04 PM"))

    def test_malformed_entries_are_dropped(self) -> None:
        self.write_raw(json.dumps({
            "2026-10-04 PM": {"how": "read", "handled_at": "2026-10-05T00:31:00-07:00"},
            "2026-10-03 PM": {"how": "later", "handled_at": "x", "shown_at": "2026-10-03T23:42:00"},
            "yesterday": {"how": "read"},
            "2026-10-02 AM": "read",
            "2026-10-01 AM": {"how": 3},
        }))
        with self.assertLogs(LOGGER, level="WARNING") as captured:
            state = self.state()
        self.assertIn("Ignored 2 malformed", "\n".join(captured.output))
        self.assertTrue(state.is_handled("2026-10-04 PM"))
        self.assertFalse(state.is_handled("2026-10-03 PM"))
        self.assertEqual(state.get("2026-10-03 PM"), {"shown_at": "2026-10-03T23:42:00"})
        self.assertFalse(state.is_handled("2026-10-01 AM"))

    def test_unreadable_path_never_crashes(self) -> None:
        self.path.mkdir(parents=True)   # a folder where the file should be
        with self.assertLogs(LOGGER, level="WARNING"):
            state = self.state()
        with self.assertLogs(LOGGER, level="WARNING") as captured:
            self.assertTrue(state.mark_handled("2026-10-04 PM", "read"))
        self.assertIn("kept for this run only", "\n".join(captured.output))
        self.assertTrue(state.is_handled("2026-10-04 PM"))   # remembered in memory

    def test_missing_folder_is_created(self) -> None:
        self.state().mark_shown("2026-10-04 PM")
        self.assertTrue(self.path.is_file())


class RunStatePruneTests(RunStateTestCase):
    def test_slots_older_than_14_days_are_forgotten_on_write(self) -> None:
        self.dir.mkdir(parents=True)
        self.path.write_text(json.dumps({
            "2026-09-20 PM": {"how": "read"},     # 15 days before Oct 5
            "2026-09-21 AM": {"how": "done"},     # 14 days: kept
            "2026-10-04 PM": {"shown_at": "2026-10-04T23:42:00-07:00"},
        }), encoding="utf-8")
        state = self.state()
        self.assertTrue(state.is_handled("2026-09-20 PM"))   # loading alone changes nothing
        state.mark_handled("2026-10-04 PM", "read")
        self.assertEqual(set(self.saved()), {"2026-09-21 AM", "2026-10-04 PM"})
        self.assertFalse(state.is_handled("2026-09-20 PM"))

    def test_prune_returns_the_count_and_writes_only_when_needed(self) -> None:
        self.dir.mkdir(parents=True)
        self.path.write_text(json.dumps({"2026-09-01 AM": {"how": "read"},
                                         "2026-10-05 AM": {"how": "read"}}), encoding="utf-8")
        state = self.state()
        self.assertEqual(state.prune(), 1)
        self.assertEqual(set(self.saved()), {"2026-10-05 AM"})
        before = self.path.stat().st_mtime_ns
        self.assertEqual(state.prune(), 0)
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        self.assertEqual(state.prune(keep_days=0), 0)
        self.clock.now += timedelta(days=1)
        self.assertEqual(state.prune(keep_days=0), 1)


class RecordTests(RunStateTestCase):
    def test_record_shown_and_answer_use_the_slot_of_now(self) -> None:
        state = self.state()
        now = datetime(2026, 10, 5, 0, 30, tzinfo=PDT)
        with self.assertLogs(LOGGER, level="INFO") as captured:
            self.assertEqual(state.record_shown(now, SLOTS), "2026-10-04 PM")
            self.assertEqual(state.record_answer(now + timedelta(hours=8), SLOTS, "read"),
                             "2026-10-04 PM")
        text = "\n".join(captured.output)
        self.assertIn("Prompt for the 2026-10-04 PM slot shown", text)
        self.assertIn("The 2026-10-04 PM slot is answered (read)", text)
        self.assertTrue(self.state().is_handled("2026-10-04 PM"))

    def test_record_without_a_slot_does_nothing(self) -> None:
        state = self.state()
        self.assertIsNone(state.record_shown(at(5, 12, 0), {}))
        self.assertIsNone(state.record_answer(at(5, 12, 0), {"AM": "bad"}, "done"))
        self.assertFalse(self.path.exists())

    def test_record_answer_never_raises(self) -> None:
        state = self.state()
        with self.assertLogs(LOGGER, level="WARNING"):
            self.assertIsNone(state.record_answer(at(5, 12, 0), SLOTS, "later"))
        self.path.mkdir(parents=True)   # every write fails from now on
        with self.assertLogs(LOGGER, level="WARNING"):
            self.assertEqual(state.record_answer(at(5, 12, 0), SLOTS, "dismissed"), "2026-10-05 AM")
            self.assertEqual(state.record_shown(at(5, 12, 0), SLOTS), "2026-10-05 AM")

    def test_constants(self) -> None:
        self.assertEqual(runstate.ANSWERS, ("read", "dismissed", "done"))
        self.assertEqual(runstate.KEEP_DAYS, 14)
        self.assertEqual(dict(DEFAULT_SLOTS), {"AM": "10:12", "PM": "23:42"})


class ModuleHygieneTests(unittest.TestCase):
    def test_qt_free(self) -> None:
        code = ("import sys, briefing_reader.runstate, briefing_reader.hotkey, briefing_reader.config; "
                "assert not any(m.startswith('PySide6') for m in sys.modules)")
        result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_source_is_ascii(self) -> None:
        self.assertTrue(Path(runstate.__file__).read_bytes().isascii())


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
