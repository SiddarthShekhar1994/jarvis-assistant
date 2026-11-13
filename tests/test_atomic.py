"""Tests for atomic.replace: a save that another process is reading at that moment (a standing check) waits a
moment and tries again instead of keeping a decision for this run only; any other error is raised at once; the
last try's error is raised. On Windows a real reader holding the file open is ridden over, for actions.json
(ActionStore) and recipients.json (RecipientHistory) too. Nothing outside a temporary folder is touched."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from briefing_reader import atomic
from briefing_reader.actions import ActionStore
from briefing_reader.recipients import RecipientHistory


class ReplaceTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_a_busy_file_is_tried_again_until_it_goes(self) -> None:
        calls: list[int] = []
        real = os.replace

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(13, "Access is denied")
            real(src, dst)

        part, path = self.dir / "a.tmp", self.dir / "a.json"
        part.write_text("new", encoding="utf-8")
        path.write_text("old", encoding="utf-8")
        sleeps: list[float] = []
        with mock.patch.object(atomic.os, "replace", flaky):
            atomic.replace(part, path, sleep=sleeps.append)
        self.assertEqual(path.read_text(encoding="utf-8"), "new")
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(sleeps), 2)

    def test_another_error_is_raised_at_once(self) -> None:
        def broken(src, dst):
            raise FileNotFoundError(2, "gone")

        sleeps: list[float] = []
        with mock.patch.object(atomic.os, "replace", broken), self.assertRaises(FileNotFoundError):
            atomic.replace(self.dir / "x", self.dir / "y", sleep=sleeps.append)
        self.assertEqual(sleeps, [])

    def test_the_last_try_s_error_is_raised(self) -> None:
        def busy(src, dst):
            raise PermissionError(13, "Access is denied")

        sleeps: list[float] = []
        with mock.patch.object(atomic.os, "replace", busy), self.assertRaises(PermissionError):
            atomic.replace(self.dir / "x", self.dir / "y", attempts=4, sleep=sleeps.append)
        self.assertEqual(len(sleeps), 3)

    def test_a_sharing_violation_counts_as_busy(self) -> None:
        error = OSError(13, "The process cannot access the file")
        error.winerror = 32
        self.assertTrue(atomic.busy(error))
        self.assertFalse(atomic.busy(OSError(2, "No such file")))

    @unittest.skipUnless(sys.platform == "win32", "Windows refuses a replace over an open file")
    def test_a_real_reader_is_ridden_over(self) -> None:
        part, path = self.dir / "b.tmp", self.dir / "b.json"
        part.write_text("new", encoding="utf-8")
        path.write_text("old", encoding="utf-8")
        reader = open(path, encoding="utf-8")   # noqa: SIM115 - held open on purpose
        threading.Timer(0.15, reader.close).start()
        atomic.replace(part, path)
        self.assertEqual(path.read_text(encoding="utf-8"), "new")


@unittest.skipUnless(sys.platform == "win32", "Windows refuses a replace over an open file")
class StoresTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_a_decision_survives_a_brief_reader_of_actions_json(self) -> None:
        path = self.dir / "actions.json"
        store = ActionStore(path, clock=lambda: datetime(2026, 11, 12, 18, 0).astimezone())
        store.set("a1", "denied")
        reader = open(path, encoding="utf-8")   # noqa: SIM115 - a standing check reading it
        threading.Timer(0.15, reader.close).start()
        started = time.monotonic()
        store.set("a2", "done")
        self.assertLess(time.monotonic() - started, 2.0)
        again = ActionStore(path)
        self.assertEqual(again.get("a2")["status"], "done")
        self.assertEqual(again.get("a1")["status"], "denied")

    def test_a_recipient_survives_a_brief_reader_of_recipients_json(self) -> None:
        path = self.dir / "recipients.json"
        history = RecipientHistory(path)
        history.add(["ana@example.edu"])
        reader = open(path, encoding="utf-8")   # noqa: SIM115 - a standing check reading it
        threading.Timer(0.15, reader.close).start()
        history.add(["sam@example.org"])
        again = RecipientHistory(path)
        self.assertTrue(again.knows("sam@example.org"))
        self.assertTrue(again.knows("ana@example.edu"))


if __name__ == "__main__":
    unittest.main()
