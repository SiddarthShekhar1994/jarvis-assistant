"""Tests for standing.store: versioned files with one writer each; a damaged or newer definitions file stays
read-only (a save never wipes it), a definition that does not check is left out and counted; the app's state (consent,
Pause all, marks) and the runner's (jobs, mail marks, proposals, holds, the heartbeat) round-trip and stay bounded;
run files are written once, read back only when their id is their name's, and pruned by age or by Clear; Run now
requests are taken once; the daily counts follow the local date and hold their caps across two real processes.
Everything happens in a temporary folder."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from briefing_reader.standing import model, store
from briefing_reader.standing.store import (AppState, Definitions, RunFiles, RunnerState, RunRequests, StandingFiles,
                                            Usage, read_json, write_json)
from tests.test_standing_model import AGENT, RULE

PROJECT = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.files = StandingFiles(Path(self.tmp.name))


class JsonTests(Base):

    def test_round_trip_with_its_version_and_no_leftovers(self) -> None:
        path = self.files.app
        self.assertTrue(write_json(path, {"a": 1, "version": 99}))
        data, problem = read_json(path)
        self.assertEqual((data, problem), ({"a": 1, "version": model.SCHEMA_VERSION}, ""))
        self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])
        self.assertNotIn(b"\r", path.read_bytes())

    def test_missing_damaged_and_newer(self) -> None:
        path = self.files.state
        self.assertEqual(read_json(path), (None, ""))
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(read_json(path), (None, store.DAMAGED))
        path.write_text(json.dumps({"version": model.SCHEMA_VERSION + 1}), encoding="utf-8")
        self.assertEqual(read_json(path), (None, store.NEWER))
        path.write_text(json.dumps([1, 2]), encoding="utf-8")
        self.assertEqual(read_json(path), (None, store.DAMAGED))


class DefinitionsTests(Base):

    def test_save_and_load_and_a_bad_one_is_counted(self) -> None:
        defs = Definitions(self.files)
        agent, rule = model.agent_from(AGENT), model.rule_from(RULE)
        self.assertTrue(defs.save([agent], [rule]))
        data, _ = read_json(self.files.definitions)
        data["rules"].append({"id": "r-bad"})
        write_json(self.files.definitions, data)
        with self.assertLogs("briefing_reader.standing.store", "WARNING"):
            agents, rules = defs.load()
        self.assertEqual((agents, rules, defs.skipped), ([agent], [rule], 1))
        self.assertFalse(defs.read_only)

    def test_a_damaged_or_newer_file_is_never_overwritten(self) -> None:
        for content in ("{damaged", json.dumps({"version": model.SCHEMA_VERSION + 1, "agents": []})):
            with self.subTest(content=content):
                self.files.definitions.write_text(content, encoding="utf-8")
                defs = Definitions(self.files)
                with self.assertLogs("briefing_reader.standing.store", "WARNING"):
                    self.assertEqual(defs.load(), ([], []))
                    self.assertFalse(defs.save([model.agent_from(AGENT)], []))
                self.assertTrue(defs.read_only)
                self.assertEqual(self.files.definitions.read_text(encoding="utf-8"), content)

    def test_the_interval_floor_applies_on_load(self) -> None:
        data = dict(AGENT, trigger={"kind": "every", "every_minutes": 45})
        write_json(self.files.definitions, {"agents": [data], "rules": []})
        with self.assertLogs("briefing_reader.standing.store", "WARNING"):
            self.assertEqual(Definitions(self.files, min_every=60).load(), ([], []))
        self.assertEqual(len(Definitions(self.files, min_every=30).load()[0]), 1)


class AppStateTests(Base):

    def test_consent_pause_and_marks(self) -> None:
        state = AppState(self.files)
        self.assertFalse(state.consented())
        self.assertTrue(state.set_consent(True))
        self.assertTrue(state.set_paused_all(True))
        self.assertTrue(state.mark("withdrawn", ["c1", "c2"]))
        self.assertTrue(state.mark("undone", ["t1"]))
        again = AppState(self.files)
        self.assertTrue(again.consented() and again.paused_all)
        self.assertTrue(again.is_withdrawn("c2") and again.is_undone("t1") and not again.is_announced("x"))
        with self.assertRaises(ValueError):
            again.mark("deleted", ["x"])

    def test_marks_stay_bounded(self) -> None:
        state = AppState(self.files)
        state.mark("announced", [f"{n:012x}" for n in range(store.MAX_MARKS + 50)])
        self.assertEqual(len(AppState(self.files).data["announced"]), store.MAX_MARKS)

    def test_a_damaged_file_is_not_overwritten(self) -> None:
        self.files.app.write_text("{damaged", encoding="utf-8")
        state = AppState(self.files)
        with self.assertLogs("briefing_reader.standing.store", "WARNING"):
            self.assertFalse(state.set_consent(True))
        self.assertEqual(self.files.app.read_text(encoding="utf-8"), "{damaged")


class RunnerStateTests(Base):

    def test_round_trip_bounds_and_forget(self) -> None:
        state = RunnerState(self.files)
        state.job("a-0123456789ab")["due_key"] = "2026-11-12 08:00"
        mail = state.mail("r-0123456789ab", "work")
        mail["seen"].extend(str(n) for n in range(store.MAX_SEEN + 20))
        mail["mark_ms"] = 1700000000000
        state.note_proposed("r-0123456789ab", "m123", date(2026, 11, 12))
        state.set_hold(datetime(2026, 11, 12, 20, 0).astimezone(), "limit")
        state.checked("task", "ok")
        state.to_announce("0123456789ab")
        state.to_announce("not-a-run-id")
        self.assertTrue(state.save())
        again = RunnerState(self.files)
        self.assertEqual(again.job("a-0123456789ab")["due_key"], "2026-11-12 08:00")
        self.assertEqual(len(again.mail("r-0123456789ab", "work")["seen"]), store.MAX_SEEN)
        self.assertTrue(again.proposed("r-0123456789ab", "m123"))
        self.assertEqual(again.hold_until()[1], "limit")
        self.assertEqual(again.last_check()[1:], ("task", "ok"))
        self.assertEqual(again.take_announce(), ["0123456789ab"])
        again.forget(["a-0123456789ab"])
        self.assertEqual(list(again.data["mail"]), [])
        self.assertEqual(list(again.data["jobs"]), ["a-0123456789ab"])


class RunFilesTests(Base):

    def test_written_once_listed_read_and_pruned(self) -> None:
        runs = RunFiles(self.files)
        now = datetime(2026, 11, 12, 18, 0).astimezone()
        old = runs.write({"id": "aaaaaaaaaaaa", "status": "ok"}, now - timedelta(days=9))
        new = runs.write({"id": "bbbbbbbbbbbb", "status": "ok"}, now - timedelta(hours=1))
        with self.assertRaises(FileExistsError):
            runs.write({"id": "bbbbbbbbbbbb"}, now - timedelta(hours=1))
        with self.assertRaises(ValueError):
            runs.write({"id": "../evil"}, now)
        self.assertEqual(runs.paths(), [old, new])
        self.assertEqual(runs.read(new)["status"], "ok")
        renamed = new.with_name(new.name.replace("bbbbbbbbbbbb", "cccccccccccc"))
        os.replace(new, renamed)
        self.assertIsNone(runs.read(renamed))                      # its id is not its name's
        self.assertEqual(runs.prune(7, now), 1)
        self.assertEqual(runs.paths(), [renamed])
        self.assertEqual(runs.prune(7, now, before=now), 1)        # Clear
        self.assertEqual(runs.paths(), [])


class RequestsTests(Base):

    def test_run_now_requests_are_taken_once(self) -> None:
        requests = RunRequests(self.files)
        self.assertTrue(requests.ask("a-0123456789ab"))
        self.assertTrue(requests.ask("r-0123456789ab"))
        self.assertTrue(requests.ask("a-0123456789ab"))
        self.assertEqual(sorted(requests.pending()), ["a-0123456789ab", "r-0123456789ab"])
        self.assertEqual(sorted(requests.take()), ["a-0123456789ab", "r-0123456789ab"])
        self.assertEqual(requests.take(), [])
        with self.assertRaises(ValueError):
            requests.ask("..\\standing")


class UsageTests(Base):

    def test_caps_by_local_date(self) -> None:
        clock = {"now": datetime(2026, 11, 12, 23, 50).astimezone()}
        usage = Usage(self.files, clock=lambda: clock["now"])
        allowed = [usage.try_run("a-1", total_cap=5, job_cap=2) for _ in range(3)]
        self.assertEqual(allowed, [True, True, False])
        allowed = [usage.try_run(f"a-{n}", total_cap=5, job_cap=2) for n in range(2, 6)]
        self.assertEqual(allowed, [True, True, True, False])
        usage.add("checks", "r-1", 8)
        usage.add("tidy", "r-1", 2)
        today = usage.today()
        self.assertEqual((today.runs, today.job("a-1"), today.checks_of("r-1"), today.tidy_of("r-1")), (5, 2, 8, 2))
        clock["now"] += timedelta(minutes=20)                      # past midnight: a new day
        self.assertEqual(usage.today().runs, 0)
        self.assertTrue(usage.try_run("a-1", total_cap=5, job_cap=2))

    def test_two_processes_share_one_cap(self) -> None:
        script = textwrap.dedent("""
            import sys
            from pathlib import Path
            sys.path.insert(0, sys.argv[1])
            from briefing_reader.standing.store import StandingFiles, Usage
            usage = Usage(StandingFiles(Path(sys.argv[2])))
            print(sum(usage.try_run("a-" + sys.argv[3], total_cap=30, job_cap=100) for _ in range(25)))
        """)
        procs = [subprocess.Popen([sys.executable, "-B", "-c", script, str(PROJECT), self.tmp.name, str(n)],
                                  stdout=subprocess.PIPE, text=True) for n in range(2)]
        counted = [int(proc.communicate(timeout=120)[0].strip()) for proc in procs]
        self.assertEqual(sum(counted), 30)
        self.assertEqual(Usage(self.files).today().runs, 30)


if __name__ == "__main__":
    unittest.main()
