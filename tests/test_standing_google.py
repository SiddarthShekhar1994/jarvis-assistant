"""Tests for Google in a standing run: a background account (persist=False) refreshes in memory only, never saves,
deletes or migrates a sign-in and never signs in, and knows when the saved sign-in is no longer the one it holds;
the app's saves use a temporary file of their own; GmailReader's watch calls (ids, one message's headers, one
message, user labels only); standing.filters refuses every filter that could read more than it says (OR, brackets,
time operators, labels, security senders, sensitive or untyped words, too broad) and keeps Jarvis's own time part
outside; standing.mailwatch finds new mail by internalDate and seen ids, oldest first, a flood waiting for the next
check, and reads the one matched message with its quoted history and secrets taken out. No network: the sockets are
blocked and Gmail is a fake."""

from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from google.auth.exceptions import RefreshError

from briefing_reader import executor
from briefing_reader.desktop.guard import SECRET_MARK
from briefing_reader.google_auth import (CALENDAR_FEATURE, LEGACY_TOKEN, AccountAuthError, AccountError,
                                         AccountNotSignedIn)
from briefing_reader.standing import filters, mailwatch
from briefing_reader.standing.filters import FilterRefused
from tests.test_gmail import READ_SCOPE, SCOPES, FakeReadRequest, SenderTestCase
from tests.test_google_auth import AuthTestCase


class BackgroundAccountTests(AuthTestCase):

    def background(self, refresh: Any = None):
        self.write_client_secret()
        self.write_token(self.data_dir / "google_token_work.json", expired=True)
        return self.account(refresh=refresh, persist=False)

    def test_a_refresh_stays_in_memory(self) -> None:
        work = self.background()
        before = work.token_path.read_bytes()
        creds = work.credentials(interactive=False)
        self.assertIsNotNone(creds)
        self.assertEqual(len(self.refreshes), 1)
        self.assertEqual(work.token_path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.data_dir.iterdir()), ["google_token_work.json"])

    def test_a_rejected_sign_in_is_left_for_the_app(self) -> None:
        def refresh(_creds: Any) -> None:
            raise RefreshError("invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"})

        work = self.background(refresh)
        with self.assertRaises(AccountAuthError):
            work.credentials(interactive=False)
        self.assertTrue(work.token_path.exists())                  # never deleted by a background run

    def test_it_never_signs_in_or_disconnects(self) -> None:
        work = self.background()
        with self.assertRaises(AccountNotSignedIn):
            work.sign_in()
        with self.assertRaises(AccountError):
            work.disconnect()
        self.assertEqual(self.flow.calls, [])
        self.assertTrue(work.token_path.exists())

    def test_still_saved_notices_a_new_sign_in(self) -> None:
        work = self.background()
        self.assertFalse(work.still_saved())                       # nothing held yet
        work.credentials(interactive=False)
        self.assertTrue(work.still_saved())
        info = json.loads(work.token_path.read_text(encoding="utf-8"))
        info["refresh_token"] = "1//another-sign-in"
        work.token_path.write_text(json.dumps(info), encoding="utf-8")
        self.assertFalse(work.still_saved())
        work.token_path.unlink()
        self.assertFalse(work.still_saved())

    def test_background_accounts_never_move_the_old_token(self) -> None:
        self.write_client_secret()
        legacy = self.data_dir / LEGACY_TOKEN
        self.write_token(legacy)
        config = type("C", (), {})()
        config.data_dir = self.data_dir
        config.calendar = type("Cal", (), {"client_secret_path": self.secret_path})()
        config.accounts = {}
        accounts = executor.build_accounts(config, persist=False)
        self.assertTrue(legacy.exists())
        self.assertFalse(accounts["personal"].persist)

    def test_the_app_s_save_uses_a_temporary_file_of_its_own(self) -> None:
        self.write_client_secret()
        self.write_token(self.data_dir / "google_token_work.json", expired=True)
        work = self.account()
        work.credentials(interactive=False, need=CALENDAR_FEATURE)
        self.assertEqual(sorted(p.name for p in self.data_dir.iterdir()), ["google_token_work.json"])


class FakeGmailWatch:
    """users().messages().list / get and users().labels().list only (all read only)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], int]] = []
        self.outcomes: list[Any] = []
        self._part = ""

    def users(self) -> FakeGmailWatch:
        return self

    def messages(self) -> FakeGmailWatch:
        self._part = "messages"
        return self

    def labels(self) -> FakeGmailWatch:
        self._part = "labels"
        return self

    def list(self, **kwargs: Any) -> FakeReadRequest:
        return FakeReadRequest(self, f"{self._part}.list", kwargs)

    def get(self, **kwargs: Any) -> FakeReadRequest:
        return FakeReadRequest(self, f"{self._part}.get", kwargs)


class WatchReaderTests(SenderTestCase):

    def reader(self):
        from briefing_reader import gmail
        from briefing_reader.google_auth import GoogleAccount

        self.write_token(scopes=SCOPES + [READ_SCOPE], asked=SCOPES + [READ_SCOPE])
        self.bind()
        self.service = FakeGmailWatch()
        account = GoogleAccount("work", client_secret_path=self.secret_path, data_dir=self.data_dir,
                                features=("calendar", "gmail_send", "gmail_read"), bindings=self.bindings,
                                flow_factory=lambda path, scopes: self, refresh=lambda creds: None)
        return gmail.GmailReader(account, service_factory=lambda creds: self.service)

    def test_list_messages_reads_ids_and_the_estimate_only(self) -> None:
        reader = self.reader()
        self.service.outcomes = [{"messages": [{"id": "m0000001", "threadId": "t0000001"},
                                               {"id": "bad id!", "threadId": "t0000002"},
                                               {"id": "m0000001", "threadId": "t0000001"}],
                                  "resultSizeEstimate": 7}]
        found, estimate = reader.list_messages("(from:a@b.example) after:100", max_results=500)
        self.assertEqual((found, estimate), ([("m0000001", "t0000001")], 7))
        name, kwargs, _retries = self.service.calls[0]
        self.assertEqual(name, "messages.list")
        self.assertEqual((kwargs["maxResults"], kwargs["includeSpamTrash"]), (100, False))
        self.assertNotIn("snippet", kwargs["fields"])

    def test_one_message_headers_then_the_message(self) -> None:
        reader = self.reader()
        self.service.outcomes = [{"id": "m0000001"}, {"id": "m0000001"}]
        reader.message_metadata("m0000001")
        reader.message("m0000001")
        (first, meta, _), (second, full, _) = self.service.calls
        self.assertEqual((first, meta["format"]), ("messages.get", "metadata"))
        self.assertIn("Authentication-Results", meta["metadataHeaders"])
        self.assertNotIn("snippet", meta["fields"])
        self.assertEqual((second, full["format"]), ("messages.get", "full"))
        with self.assertRaises(Exception):
            reader.message("../../x")

    def test_labels_are_the_owner_s_own_only(self) -> None:
        reader = self.reader()
        self.service.outcomes = [{"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"},
                                             {"id": "Label_7", "name": "  Invoices  ", "type": "user"},
                                             {"id": "SPAM", "name": "SPAM", "type": "user"}]}]
        self.assertEqual(reader.labels(), [("Label_7", "Invoices")])


SENTENCE = "when an invoice from billing@acme.example arrives with invoice in the subject, star it"


class FilterTests(unittest.TestCase):

    def test_a_plain_filter_from_the_owner_s_words(self) -> None:
        text = filters.check_filter("from:billing@acme.example  subject:invoice", SENTENCE, needs_sender=True)
        self.assertEqual(text, "from:billing@acme.example subject:invoice")
        self.assertEqual(filters.senders(text), ["billing@acme.example"])
        self.assertEqual(filters.watch_query(text, 1700000000), f"({text}) after:1700000000")

    def test_everything_that_could_read_more_is_refused(self) -> None:
        cases = [
            "invoice OR -invoice", "from:billing@acme.example OR", "{from:billing@acme.example}",
            "(from:billing@acme.example)", "invoice AND star", "- invoice", "invoice newer_than:2d",
            "invoice after:2026/01/01", "rfc822msgid:<a@b.c>", "label:invoices invoice", "in:anywhere invoice",
            "from:service@paypal.com invoice", "subject:statement invoice", "from:billing@acme.example payroll",
            "in:inbox", "is:unread", "",
        ]
        for query in cases:
            with self.subTest(query=query), self.assertRaises(FilterRefused):
                filters.check_filter(query, SENTENCE)

    def test_a_tidy_up_needs_a_sender(self) -> None:
        with self.assertRaises(FilterRefused):
            filters.check_filter("subject:invoice", SENTENCE, needs_sender=True)
        self.assertEqual(filters.check_filter("subject:invoice", SENTENCE), "subject:invoice")
        with self.assertRaises(FilterRefused):
            filters.check_filter("-from:billing@acme.example subject:invoice", SENTENCE, needs_sender=True)


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def message(n: int, received: datetime, *, sender: str = "Acme Billing <billing@acme.example>",
            subject: str = "Invoice 42", labels: list[str] | None = None, auth: list[str] | None = None,
            body: str = "Please pay $120.00 by November 20.") -> dict:
    headers = [{"name": "From", "value": sender}, {"name": "To", "value": "owner@example.com"},
               {"name": "Subject", "value": subject}, {"name": "Message-ID", "value": f"<m{n}@acme.example>"}]
    for value in auth or ["mx.google.com; dkim=pass header.i=@acme.example; spf=pass; dmarc=pass (p=REJECT)"]:
        headers.insert(0, {"name": "Authentication-Results", "value": value})
    return {"id": f"m{n:07d}", "threadId": f"t{n:07d}", "labelIds": labels or ["INBOX", "UNREAD"],
            "internalDate": str(int(received.timestamp() * 1000)),
            "payload": {"mimeType": "text/plain", "headers": headers, "body": {"data": b64(body)}}}


class FakeReader:
    alias = "work"

    def __init__(self, messages: list[dict]) -> None:
        self.messages = {item["id"]: item for item in messages}
        self.queries: list[tuple[str, int]] = []

    def list_messages(self, query: str, *, max_results: int):
        self.queries.append((query, max_results))
        after = int(query.rsplit("after:", 1)[1])
        found = [(item["id"], item["threadId"]) for item in sorted(self.messages.values(),
                                                                       key=lambda m: -int(m["internalDate"]))
                 if int(item["internalDate"]) // 1000 >= after]
        return found[:max_results], len(found)

    def message_metadata(self, message_id: str) -> dict:
        data = json.loads(json.dumps(self.messages[message_id]))
        data["payload"].pop("body", None)
        return data

    def message(self, message_id: str) -> dict:
        return self.messages[message_id]


NOW = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)


class MailwatchTests(unittest.TestCase):

    def test_first_sight_is_a_baseline(self) -> None:
        batch = mailwatch.new_mail(FakeReader([]), "subject:invoice", {}, NOW, max_new=8)
        self.assertTrue(batch.baseline)
        self.assertEqual(batch.mark_ms, int(NOW.timestamp() * 1000))

    def test_new_mail_oldest_first_seen_and_old_ones_left_out(self) -> None:
        mark = int((NOW - timedelta(minutes=10)).timestamp() * 1000)
        reader = FakeReader([message(1, NOW - timedelta(minutes=30)),       # older than the mark
                             message(2, NOW - timedelta(minutes=5)),
                             message(3, NOW - timedelta(minutes=2)),
                             message(4, NOW - timedelta(minutes=1), labels=["SPAM"]),
                             message(5, NOW - timedelta(minutes=4))])
        batch = mailwatch.new_mail(reader, "subject:invoice", {"mark_ms": mark, "seen": ["m0000005"]}, NOW, max_new=8)
        self.assertEqual([ref.message_id for ref in batch.refs], ["m0000002", "m0000003"])
        self.assertTrue(reader.queries[0][0].startswith("(subject:invoice) after:"))
        self.assertEqual(batch.mark_ms, max(ref.received_ms for ref in batch.refs))
        self.assertIn("m0000003", batch.seen)
        first = batch.refs[0]
        self.assertEqual((first.sender.email, first.subject, first.msgid), ("billing@acme.example", "Invoice 42",
                                                                           "m2@acme.example"))
        self.assertTrue(first.dmarc_pass)
        self.assertNotIn("Acme", repr(first))

    def test_a_flood_waits_for_the_next_check(self) -> None:
        mark = int((NOW - timedelta(hours=1)).timestamp() * 1000)
        reader = FakeReader([message(n, NOW - timedelta(minutes=50 - n)) for n in range(1, 11)])
        first = mailwatch.new_mail(reader, "subject:invoice", {"mark_ms": mark}, NOW, max_new=3)
        self.assertEqual(([r.message_id for r in first.refs], first.more), (["m0000001", "m0000002", "m0000003"], 7))
        self.assertFalse(first.full)
        second = mailwatch.new_mail(reader, "subject:invoice", {"mark_ms": first.mark_ms, "seen": list(first.seen)},
                                    NOW, max_new=3)
        self.assertEqual([r.message_id for r in second.refs], ["m0000004", "m0000005", "m0000006"])
        self.assertEqual(len(reader.queries), 2)

    def test_a_real_flood_says_so(self) -> None:
        mark = int((NOW - timedelta(hours=3)).timestamp() * 1000)
        reader = FakeReader([message(n, NOW - timedelta(minutes=150 - n)) for n in range(1, 121)])
        batch = mailwatch.new_mail(reader, "subject:invoice", {"mark_ms": mark}, NOW, max_new=8)
        self.assertTrue(batch.full)                     # 120 new: one check lists 100
        self.assertEqual(len(batch.refs), 8)

    def test_dmarc_is_gmail_s_own_verdict(self) -> None:
        good = message(1, NOW)
        self.assertTrue(mailwatch.ref_from(good, "work").dmarc_pass)
        failed = message(2, NOW, auth=["mx.google.com; dmarc=fail (p=NONE)"])
        self.assertFalse(mailwatch.ref_from(failed, "work").dmarc_pass)
        forged = message(3, NOW, auth=["evil.example; dmarc=pass", "mx.google.com; dmarc=fail"])
        self.assertFalse(mailwatch.ref_from(forged, "work").dmarc_pass)

    def test_read_message_strips_and_scrubs(self) -> None:
        body = ("Your temporary password is: Xq9-77-zzTop\nPay $120.00 by November 20.\n\n"
                "On Tue, Nov 10, 2026 at 9:00 AM Someone <x@example.com> wrote:\n> older text")
        reader = FakeReader([message(1, NOW, body=body)])
        ref = mailwatch.ref_from(reader.message_metadata("m0000001"), "work")
        text, left_out = mailwatch.read_message(reader, ref, chars=4000)
        self.assertIn(SECRET_MARK, text.text)
        self.assertNotIn("Xq9-77-zzTop", text.text)
        self.assertNotIn("older text", text.text)
        self.assertEqual(left_out, 1)
        short, _ = mailwatch.read_message(reader, ref, chars=200)
        self.assertLessEqual(len(short.text), 200)

    def test_read_message_refuses_a_changed_message(self) -> None:
        reader = FakeReader([message(1, NOW)])
        ref = mailwatch.ref_from(reader.message_metadata("m0000001"), "work")
        reader.messages["m0000001"]["threadId"] = "t9999999"
        self.assertEqual(mailwatch.read_message(reader, ref, chars=4000), (None, 0))

    def test_recent_for_a_dry_run(self) -> None:
        reader = FakeReader([message(n, NOW - timedelta(days=n)) for n in range(1, 6)])
        refs, estimate = mailwatch.recent(reader, "subject:invoice", 30, NOW, max_refs=3)
        self.assertEqual(([r.message_id for r in refs], estimate), (["m0000001", "m0000002", "m0000003"], 5))
        self.assertTrue(mailwatch.anything_new(reader, int((NOW - timedelta(days=2)).timestamp() * 1000)))
        self.assertFalse(mailwatch.anything_new(reader, int((NOW + timedelta(days=1)).timestamp() * 1000)))


if __name__ == "__main__":
    unittest.main()
