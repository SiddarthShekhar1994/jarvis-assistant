"""Tests for briefing_reader.recipients: the NEW RECIPIENT check and the hashed history of earlier
recipients. Addresses are invented; nothing reads the owner's data folder."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from briefing_reader import recipients
from briefing_reader.recipients import (
    KNOWN,
    NEW,
    NO_RECIPIENT_LEFT,
    OWN,
    TRUSTED,
    RecipientHistory,
    address_key,
    classify,
    in_domains,
    review,
)

LOGGER = "briefing_reader.recipients"
PDT = timezone(timedelta(hours=-7))


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 7, 9, 0, tzinfo=PDT)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "data" / "recipients.json"

    def test_remembers_hashes_never_addresses(self) -> None:
        history = RecipientHistory(self.path, clock=Clock())
        self.assertFalse(history.knows("ana@example.edu"))
        with self.assertLogs(LOGGER, level="INFO") as logs:
            self.assertTrue(history.add(["ana@example.edu", "Ben@Example.edu", "ana@example.edu", ""]))
        self.assertIn("Remembered 2 recipient(s)", "\n".join(logs.output))
        raw = self.path.read_bytes()
        self.assertNotIn(b"@", raw)
        self.assertNotIn(b"ana", raw)
        self.assertNotIn(b"\r", raw)
        data = json.loads(raw)
        self.assertEqual(set(data), {hashlib.sha256(b"ana@example.edu").hexdigest(),
                                     hashlib.sha256(b"ben@example.edu").hexdigest()})
        self.assertEqual(address_key(" ANA@example.EDU "), hashlib.sha256(b"ana@example.edu").hexdigest())
        again = RecipientHistory(self.path)   # the next start
        self.assertTrue(again.knows("ANA@EXAMPLE.EDU"))
        self.assertTrue(again.knows("ben@example.edu"))
        self.assertFalse(again.knows("cy@example.edu"))
        self.assertEqual(len(again), 2)
        for text in logs.output:
            self.assertNotIn("example.edu", text)

    def test_keeps_the_most_recent(self) -> None:
        history = RecipientHistory(self.path, clock=Clock())
        with mock.patch.object(recipients, "MAX_REMEMBERED", 3), self.assertLogs(LOGGER, level="INFO"):
            for name in ("a", "b", "c", "d"):
                history.add([f"{name}@example.edu"])
        self.assertEqual(len(history), 3)
        self.assertFalse(history.knows("a@example.edu"))
        self.assertTrue(history.knows("d@example.edu"))

    def test_unreadable_or_malformed_files(self) -> None:
        self.path.parent.mkdir(parents=True)
        for text in ("{broken", "[1, 2]"):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertLogs(LOGGER, level="WARNING"):
                    self.assertFalse(RecipientHistory(self.path).knows("ana@example.edu"))
        good = address_key("ana@example.edu")
        self.path.write_text(json.dumps({good: "2026-10-01T09:00:00-07:00", "ana@example.edu": "x", "f" * 63: "y",
                                         address_key("b@example.edu"): 5}), encoding="utf-8")
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            history = RecipientHistory(self.path)
            self.assertTrue(history.knows("ana@example.edu"))
        self.assertIn("Ignored 3 malformed", "\n".join(logs.output))
        self.assertNotIn("ana@example.edu", "\n".join(logs.output))

    def test_a_failed_write_keeps_them_for_this_run(self) -> None:
        self.path.parent.mkdir(parents=True)
        blocked = RecipientHistory(self.path / "not-a-folder" / "recipients.json")
        self.path.write_text("{}", encoding="utf-8")
        with self.assertLogs(LOGGER, level="WARNING"):
            self.assertFalse(blocked.add(["ana@example.edu"]))
        self.assertTrue(blocked.knows("ana@example.edu"))


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.history = RecipientHistory(Path(tmp.name) / "recipients.json")
        with self.assertLogs(LOGGER, level="INFO"):
            self.history.add(["ben@example.com"])

    def test_own_trusted_known_new(self) -> None:
        kinds = classify(["You@Example.edu", "ana@example.edu", "dean@lab.example.edu", "ben@example.com",
                          "eve@badexample.edu", "eve@example.edu.evil.com", "cy@example.org"],
                         own="you@example.edu", trusted_domains=("example.edu",), history=self.history)
        self.assertEqual(kinds, {"You@Example.edu": OWN, "ana@example.edu": TRUSTED, "dean@lab.example.edu": TRUSTED,
                                 "ben@example.com": KNOWN, "eve@badexample.edu": NEW,
                                 "eve@example.edu.evil.com": NEW, "cy@example.org": NEW})

    def test_defaults_trust_nobody(self) -> None:
        self.assertEqual(classify(["ana@example.edu"], own="", trusted_domains=(), history=None),
                         {"ana@example.edu": NEW})

    def test_in_domains(self) -> None:
        self.assertTrue(in_domains("a@Sub.Example.EDU", ["example.edu"]))
        self.assertTrue(in_domains("a@example.edu", ["@Example.edu"]))
        self.assertFalse(in_domains("a@notexample.edu", ["example.edu"]))
        self.assertFalse(in_domains("a@example.edu", ["sub.example.edu"]))
        self.assertFalse(in_domains("not-an-address", ["example.edu"]))
        self.assertFalse(in_domains("a@example.edu", [""]))


class ReviewTests(unittest.TestCase):
    def test_new_recipients_need_a_tick(self) -> None:
        result = review(("ana@example.edu", "eve@example.com"), ("cy@example.org",), own="you@example.edu",
                        trusted_domains=("example.edu",))
        self.assertEqual((result.to, result.cc), (("ana@example.edu", "eve@example.com"), ("cy@example.org",)))
        self.assertEqual(result.new, ("eve@example.com", "cy@example.org"))
        self.assertEqual(result.unconfirmed, ("eve@example.com", "cy@example.org"))
        self.assertFalse(result.ready)
        self.assertEqual((result.kind_of("ANA@example.edu"), result.kind_of("cy@example.org"), result.kind_of("x@y.z")),
                         (TRUSTED, NEW, ""))
        ticked = review(("ana@example.edu", "eve@example.com"), ("cy@example.org",), own="you@example.edu",
                        trusted_domains=("example.edu",), confirmed={"EVE@example.com", "cy@example.org"})
        self.assertEqual((ticked.new, ticked.unconfirmed, ticked.ready),
                         (("eve@example.com", "cy@example.org"), (), True))

    def test_the_own_address_is_not_a_recipient(self) -> None:
        result = review(("ana@example.edu", "You@example.edu"), ("you@example.edu",), own="you@example.edu",
                        confirmed={"ana@example.edu"})
        self.assertEqual((result.to, result.cc, result.own_dropped), (("ana@example.edu",), (), True))
        self.assertEqual(result.kinds, (("ana@example.edu", NEW), ("You@example.edu", OWN), ("you@example.edu", OWN)))
        self.assertTrue(result.ready)
        only_me = review(("you@example.edu",), ("cy@example.org",), own="you@example.edu", confirmed={"cy@example.org"})
        self.assertEqual((only_me.problem, only_me.ready, only_me.cc), (NO_RECIPIENT_LEFT, False, ("cy@example.org",)))
        unknown_own = review(("you@example.edu",), (), own="")   # the account is not confirmed yet
        self.assertEqual((unknown_own.to, unknown_own.new, unknown_own.own_dropped),
                         (("you@example.edu",), ("you@example.edu",), False))


if __name__ == "__main__":
    unittest.main()
