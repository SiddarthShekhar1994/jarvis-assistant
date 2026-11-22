"""Unsubscribing by a message's own List-Unsubscribe header (mail extras, part 3), Qt-free.

The header is read strictly (the first https and mailto entries; one plain mailto address with a short subject and
body; one-click only for List-Unsubscribe-Post exactly "List-Unsubscribe=One-Click"; Gmail's DMARC verdict from the
first Authentication-Results only); the method order is one-click, mailto, the page; the one POST is checked, sent once
with exactly that body, never follows a redirect, maps 2xx / 3xx / 4xx / 5xx and no answer; "running" is saved before
it; a line never makes a working card; logs hold no URL, host, address or list id. Every name, address and host here
is invented; nothing reaches the network."""

from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import date
from pathlib import Path

from briefing_reader import actions, executor, unsubscribe as un
from briefing_reader.actions import (HOLD_KINDS, STATUS_RUNNING, STATUS_SENT, STATUS_UNKNOWN, UNSUBSCRIBE, ActionStore,
                                     card_view, make_unsubscribe, parse_action_line)
from briefing_reader.capture_net import NetRefusedInTests

URL = "https://lists.example.com/u/abc123?token=xyz"
AUTH_OK = "mx.google.com; dkim=pass header.i=@example.com; spf=pass; dmarc=pass (p=REJECT) header.from=example.com"
AUTH_FAIL = "mx.google.com; dkim=fail; spf=softfail; dmarc=fail (p=NONE) header.from=example.com"


def headers(*, unsub=f"<mailto:leave@lists.example.com?subject=unsubscribe>, <{URL}>",
            post=un.ONE_CLICK_VALUE, auth=AUTH_OK, list_id="Acme Deals <deals.acme.example.com>",
            sender="Acme Deals <deals@example.com>", extra=()):  # type: ignore[no-untyped-def]
    found = [{"name": "Authentication-Results", "value": auth}] if auth is not None else []
    found += [{"name": "From", "value": sender}, {"name": "List-Id", "value": list_id}]
    if unsub is not None:
        found.append({"name": "List-Unsubscribe", "value": unsub})
    if post is not None:
        found.append({"name": "List-Unsubscribe-Post", "value": post})
    return found + list(extra)


class ReadTests(unittest.TestCase):
    def test_both_entries_one_click_and_verified(self) -> None:
        header = un.read_headers(headers())
        self.assertEqual(header.https, URL)
        self.assertEqual((header.mailto.address, header.mailto.subject, header.mailto.body),
                         ("leave@lists.example.com", "unsubscribe", ""))
        self.assertTrue(header.one_click)
        self.assertTrue(header.verified)
        self.assertEqual((header.list_id, header.name, header.sender),
                         ("deals.acme.example.com", "Acme Deals", "deals@example.com"))
        self.assertEqual(repr(header), "ListHeader(https, mailto, one-click, verified)")

    def test_the_post_value_must_be_exact(self) -> None:
        for value in ("List-Unsubscribe=One-Click ", "list-unsubscribe=one-click", "List-Unsubscribe=One-Click; x=1",
                      "One-Click", ""):
            with self.subTest(value=value):
                self.assertEqual(un.read_headers(headers(post=value)).one_click, value.strip() == un.ONE_CLICK_VALUE)
        self.assertFalse(un.read_headers(headers(post=None)).one_click)
        self.assertFalse(un.read_headers(headers(unsub="<mailto:a@example.com>")).one_click)   # no https: no one-click

    def test_a_mailto_needs_exactly_one_plain_address_and_keeps_short_words(self) -> None:
        for value in ("<mailto:a@example.com,b@example.com>", "<mailto:a@example.com?to=b@example.com>",
                      "<mailto:not-an-address>", "<mailto:a@example.com?subject=x&subject=y>"):
            with self.subTest(value=value):
                self.assertIsNone(un.read_headers(headers(unsub=value)).mailto)
        long = "x" * 300
        mailto = un.read_headers(headers(unsub=f"<mailto:a@example.com?subject={long}&body={long}&cc=c@example.com>"
                                         )).mailto
        self.assertEqual((len(mailto.subject), len(mailto.body)), (100, 200))
        self.assertEqual(mailto.address, "a@example.com")
        self.assertNotIn("x", repr(mailto))

    def test_dmarc_from_the_first_authentication_results_only(self) -> None:
        self.assertFalse(un.read_headers(headers(auth=AUTH_FAIL)).verified)
        self.assertFalse(un.read_headers(headers(auth=None)).verified)
        forged = [{"name": "Authentication-Results", "value": "evil.example; dmarc=pass"}]
        self.assertFalse(un.dmarc_passed(forged + headers()))                  # not Gmail's: first wins
        later = headers(auth=AUTH_FAIL, extra=[{"name": "Authentication-Results", "value": AUTH_OK}])
        self.assertFalse(un.dmarc_passed(later))
        self.assertTrue(un.dmarc_passed(headers()))

    def test_the_standing_watch_uses_the_same_verdict(self) -> None:
        from briefing_reader.standing import mailwatch

        self.assertIs(mailwatch.dmarc_passed, un.dmarc_passed)

    def test_odd_input_is_no_header(self) -> None:
        for odd in (None, "x", [1, 2], [{"name": 3}], [{"name": "List-Unsubscribe", "value": 7}]):
            with self.subTest(odd=str(odd)):
                self.assertFalse(un.read_headers(odd).present)


class ChooseTests(unittest.TestCase):
    def test_the_order_is_one_click_then_mailto_then_the_page(self) -> None:
        self.assertEqual(un.choose(un.read_headers(headers())), un.ONE_CLICK)
        self.assertEqual(un.choose(un.read_headers(headers(post=None))), un.MAILTO)
        self.assertEqual(un.choose(un.read_headers(headers(post=None, unsub=f"<{URL}>"))), un.PAGE)

    def test_no_unsubscribe_for_a_sender_gmail_could_not_verify(self) -> None:
        self.assertEqual(un.choose(un.read_headers(headers(auth=AUTH_FAIL))), un.NOT_VERIFIED)
        self.assertEqual(un.choose(un.read_headers(headers(unsub=None))), un.NO_HEADER)
        self.assertIn("could not verify", un.REFUSAL_WORDS[un.NOT_VERIFIED])

    def test_a_one_click_link_must_be_public_https_on_443(self) -> None:
        for bad in ("http://lists.example.com/u", "https://127.0.0.1/u", "https://localhost/u", "https://10.0.0.8/u",
                    "https://user:pw@lists.example.com/u", "https://lists.example.com:8443/u", "https://printer.local/u",
                    "https://lists.example.com/u x", "ftp://lists.example.com/u"):
            with self.subTest(bad=bad):
                self.assertEqual(un.url_problem(bad), un.REASON_REFUSED)
        self.assertEqual(un.url_problem("https://lists.example.com:443/u"), "")
        header = un.read_headers(headers(unsub="<https://192.168.1.4/u>, <mailto:a@example.com>"))
        self.assertEqual(un.choose(header), un.MAILTO)                        # a local one-click link is never used
        self.assertEqual(un.choose(un.read_headers(headers(unsub="<https://10.1.1.1/u>", post=None))), un.NO_METHOD)


class OneClickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.posts: list[tuple[str, float, int]] = []

    def poster(self, status=None, error=None):  # type: ignore[no-untyped-def]
        def post(url: str, timeout: float, cap: int) -> int:
            self.posts.append((url, timeout, cap))
            if error is not None:
                raise error
            return status
        return post

    def run_once(self, **kw):  # type: ignore[no-untyped-def]
        kw.setdefault("resolve", lambda _host: ["93.184.216.34"])
        return un.one_click(URL, **kw)

    def test_2xx_is_done_after_exactly_one_post(self) -> None:
        with self.assertLogs("briefing_reader.unsubscribe", logging.INFO) as logs:
            result = self.run_once(post=self.poster(202))
        self.assertEqual((result.outcome, result.status), (un.DONE, 202))
        self.assertEqual(len(self.posts), 1)
        url, timeout, cap = self.posts[0]
        self.assertEqual(url, URL)
        self.assertLessEqual(timeout, 10.0)
        self.assertEqual(cap, 64 * 1024)
        text = "\n".join(logs.output)
        for secret in ("lists.example.com", "abc123", "token", "xyz"):
            self.assertNotIn(secret, text)

    def test_3xx_4xx_5xx_and_no_answer(self) -> None:
        for status, outcome, reason in ((301, un.FAILED, un.REASON_REDIRECT), (404, un.FAILED, un.REASON_CLIENT),
                                        (503, un.UNKNOWN, un.REASON_SERVER)):
            with self.subTest(status=status):
                result = self.run_once(post=self.poster(status))
                self.assertEqual((result.outcome, result.reason), (outcome, reason))
        result = self.run_once(post=self.poster(error=TimeoutError("slow")))
        self.assertEqual((result.outcome, result.reason), (un.UNKNOWN, un.REASON_TIMEOUT))
        self.assertEqual(len(self.posts), 4)                                 # each once, never retried

    def test_a_local_address_sends_nothing(self) -> None:
        for addresses in (["127.0.0.1"], ["192.168.1.4"], ["93.184.216.34", "10.0.0.1"], []):
            with self.subTest(addresses=addresses):
                result = un.one_click(URL, post=self.poster(200), resolve=lambda _h, a=addresses: a)
                self.assertEqual((result.outcome, result.reason), (un.FAILED, un.REASON_REFUSED))
        result = un.one_click(URL, post=self.poster(200), resolve=lambda _h: (_ for _ in ()).throw(OSError("dns")))
        self.assertEqual(result.outcome, un.FAILED)
        self.assertEqual(un.one_click("https://127.0.0.1/u", post=self.poster(200)).outcome, un.FAILED)
        self.assertEqual(self.posts, [])

    def test_the_real_post_and_resolve_refuse_in_the_tests(self) -> None:
        with self.assertRaises(NetRefusedInTests):
            un.real_post(URL, 1.0, 10)
        result = un.one_click(URL, resolve=lambda _h: ["93.184.216.34"])   # the real poster: refused, nothing sent
        self.assertEqual((result.outcome, result.reason), (un.FAILED, un.REASON_REFUSED))


class CardTests(unittest.TestCase):
    def card(self):  # type: ignore[no-untyped-def]
        header = un.read_headers(headers())
        return make_unsubscribe("personal", thread="18c2aa01", gmid="18c2aa02", url=header.https,
                                sender=header.sender, list_id=header.list_id, name=header.name)

    def test_the_card_is_a_held_kind_carried_once(self) -> None:
        card = self.card()
        self.assertIn(UNSUBSCRIBE, HOLD_KINDS)
        self.assertNotIn(UNSUBSCRIBE, actions.UNDO_KINDS)
        self.assertNotIn(UNSUBSCRIBE, actions.MAIL_KINDS)
        self.assertNotIn(UNSUBSCRIBE, executor.ACCOUNT_KINDS)
        self.assertTrue(card.held and card.carried and not card.sends_mail and not card.undoable)
        self.assertEqual(card.approve_label(), "Unsubscribe")
        self.assertEqual(actions.sent_text(card), "Unsubscribed")
        self.assertEqual(card.speech_noun, ("a list to unsubscribe from", "lists to unsubscribe from"))
        view = card_view(card, date(2026, 10, 4))
        self.assertEqual(view.title, "Unsubscribe from Acme Deals")
        self.assertEqual(view.detail, "One-click · lists.example.com · personal")
        self.assertEqual(view.body, actions.UNSUBSCRIBE_BODY)
        self.assertEqual((view.open_text, view.copy_text, view.approve_text), ("", "", "Unsubscribe"))
        self.assertEqual(actions.result_text(card, STATUS_UNKNOWN), actions.UNKNOWN_UNSUBSCRIBE)

    def test_one_decision_per_list(self) -> None:
        first = self.card()
        again = make_unsubscribe("personal", thread="18c2bb01", gmid="18c2bb02", url=URL, sender="deals@example.com",
                                 list_id="deals.acme.example.com")
        self.assertEqual(first.id, again.id)
        other = make_unsubscribe("work", thread="18c2bb01", gmid="18c2bb02", url=URL, sender="deals@example.com",
                                 list_id="deals.acme.example.com")
        self.assertNotEqual(first.id, other.id)

    def test_a_line_never_makes_a_working_card(self) -> None:
        line = f"Unsubscribe: acct=personal | thread=18c2aa01 | gmid=18c2aa02 | method=one_click | url={URL}"
        card = parse_action_line(line)
        self.assertEqual(card.kind, UNSUBSCRIBE)
        self.assertEqual(card.error, actions.ONLY_ASK_UNSUBSCRIBE)
        self.assertFalse(card.decidable or card.actionable)
        self.assertEqual((card.field("acct"), card.field("gmid")), ("personal", "18c2aa02"))   # kept for Ask's checks


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.store = ActionStore(Path(tmp.name) / "actions.json")
        self.card = CardTests.card(self)  # type: ignore[arg-type]
        self.seen: list[str] = []

    def make(self, status=200, error=None):  # type: ignore[no-untyped-def]
        def post(url: str, timeout: float, cap: int) -> int:
            self.seen.append((self.store.get(self.card.id) or {}).get("status", ""))
            if error is not None:
                raise error
            return status
        backend = executor.UnsubscribeBackend(post=post, resolve=lambda _h: ["93.184.216.34"])
        return executor.Executor([backend], {})

    def test_running_is_saved_before_the_one_post_and_sent_after(self) -> None:
        run = executor.run_action(self.make(200), self.card, store=self.store, writes_running=True)
        self.assertEqual(self.seen, [STATUS_RUNNING])
        self.assertEqual(run.status, STATUS_SENT)
        self.assertEqual(run.result.result_text, "Unsubscribed")
        self.assertEqual(self.store.get(self.card.id)["status"], STATUS_SENT)

    def test_no_answer_is_unknown_and_a_4xx_failed_never_retried(self) -> None:
        run = executor.run_action(self.make(error=TimeoutError("slow")), self.card, store=self.store,
                                  writes_running=True)
        self.assertEqual(run.status, STATUS_UNKNOWN)
        self.assertEqual(len(self.seen), 1)
        other = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(other.cleanup)
        run = executor.run_action(self.make(404), self.card, store=ActionStore(Path(other.name) / "a.json"),
                                  writes_running=True)
        self.assertEqual(run.status, actions.STATUS_FAILED)

    def test_readiness_checks_the_link_again_and_preview_shows_the_exact_body(self) -> None:
        ex = self.make()
        self.assertEqual(ex.readiness(self.card), "")
        bad = make_unsubscribe("personal", thread="18c2aa01", gmid="18c2aa02", url="https://127.0.0.1/u",
                               sender="deals@example.com", list_id="x.example.com")
        self.assertEqual(ex.readiness(bad), executor.UNSUBSCRIBE_LINK_NOTE)
        run = executor.run_action(ex, bad, store=self.store, writes_running=True)
        self.assertEqual(run.status, actions.STATUS_FAILED)
        self.assertEqual(self.seen, [])
        preview = ex.preview(self.card)
        self.assertEqual(preview.values()["Body"], "List-Unsubscribe=One-Click")
        self.assertTrue(ex.signed_in(self.card))

    def test_the_logs_hold_no_link_host_address_or_list(self) -> None:
        with self.assertLogs("briefing_reader", logging.DEBUG) as logs:
            executor.run_action(self.make(200), self.card, store=self.store, writes_running=True)
        text = "\n".join(logs.output)
        for secret in ("lists.example.com", "abc123", "token", "deals@", "acme", "Acme"):
            self.assertNotIn(secret, text)
        self.assertIn("one_click done (HTTP 200", text)

    def test_the_app_builds_it(self) -> None:
        from briefing_reader.config import load_config

        holder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(holder.cleanup)
        tmp = holder.name
        (Path(tmp) / "config.toml").write_text("", encoding="utf-8")
        config = load_config(Path(tmp), environ={"LOCALAPPDATA": str(Path(tmp) / "local")})
        built = executor.build_executor(config, {}, {})
        self.assertIsInstance(built.backend_for(self.card), executor.UnsubscribeBackend)


# ---- fix round 1 (the review's findings) ------------------------------------------------------------------------------

SPOOF_FAIL = ("mx.google.com; dkim=none; spf=pass (google.com: domain of dmarc=pass@spam.example designates 192.0.2.7 "
              "as permitted sender) smtp.mailfrom=dmarc=pass@spam.example; dmarc=fail (p=NONE sp=NONE dis=NONE) "
              "header.from=example.com")
SPOOF_NONE = ("mx.google.com; spf=softfail (google.com: domain of transitioning dmarc=pass@spam.example does not "
              "designate 192.0.2.7 as permitted sender) smtp.mailfrom=dmarc=pass@spam.example")


def gmail_data(auth: str, *, sender: str = "Acme Deals <deals@example.com>", unsub: str | None = None) -> dict:
    found = [{"name": "Authentication-Results", "value": auth}, {"name": "From", "value": sender},
             {"name": "To", "value": "you@example.edu"}, {"name": "Subject", "value": "Deals"}]
    if unsub is not None:
        found.append({"name": "List-Unsubscribe", "value": unsub})
    return {"id": "msg0000777", "threadId": "thr0000777", "labelIds": ["INBOX"], "internalDate": "1791200000000",
            "payload": {"mimeType": "text/plain", "headers": found, "body": {"data": "SGk", "size": 2}}}


class DmarcTests(unittest.TestCase):
    """High 2: only the result whose method is exactly dmarc counts, after comments and quoted strings are out; no
    dmarc result is not verified; header.from must be the From address's domain when it is named."""

    def verdict(self, auth: str, sender: str = "Acme Deals <deals@example.com>") -> bool:
        return un.dmarc_passed([{"name": "Authentication-Results", "value": auth}, {"name": "From", "value": sender}])

    def test_a_dmarc_pass_written_in_a_comment_or_address_is_never_the_verdict(self) -> None:
        self.assertFalse(self.verdict(SPOOF_FAIL))
        self.assertFalse(self.verdict(SPOOF_NONE))
        self.assertFalse(self.verdict('mx.google.com; spf=pass "dmarc=pass"; dkim=pass'))
        self.assertFalse(self.verdict("mx.google.com; arc=pass (i=1 dmarc=pass) smtp.remote-ip=192.0.2.7"))
        self.assertFalse(self.verdict("mx.google.com; xdmarc=pass; spf=pass"))

    def test_exactly_one_dmarc_result_and_it_passes(self) -> None:
        self.assertTrue(self.verdict(AUTH_OK))
        self.assertTrue(self.verdict("mx.google.com; dkim=pass; DMARC = PASS (p=REJECT)"))
        self.assertFalse(self.verdict("mx.google.com; dmarc=pass; dmarc=fail"))
        self.assertFalse(self.verdict("mx.google.com (unclosed; dmarc=pass"))

    def test_header_from_must_be_the_from_domain(self) -> None:
        self.assertFalse(self.verdict(AUTH_OK, "Bank <alerts@bank.example>"))
        self.assertFalse(self.verdict(AUTH_OK, "no address at all"))
        self.assertTrue(self.verdict("mx.google.com; dmarc=pass (p=NONE) header.from=Example.COM."))
        self.assertTrue(self.verdict("mx.google.com; dmarc=pass (p=NONE)"))            # not named: Gmail's verdict

    def test_both_users_read_the_same_verdict(self) -> None:
        from briefing_reader.standing import mailwatch, rules

        self.assertIs(mailwatch.dmarc_passed, un.dmarc_passed)
        spoofed = mailwatch.ref_from(gmail_data(SPOOF_FAIL), "work")
        self.assertFalse(spoofed.dmarc_pass)
        self.assertEqual(rules._mail_held(None, spoofed, ()), rules.HELD_DMARC)   # a tidy rule holds it back
        good = mailwatch.ref_from(gmail_data(AUTH_OK), "work")
        self.assertTrue(good.dmarc_pass)
        from types import SimpleNamespace

        rule = SimpleNamespace(tidy=SimpleNamespace(kind=rules.TIDY_ARCHIVE))
        self.assertNotEqual(rules._mail_held(rule, good, ()), rules.HELD_DMARC)
        self.assertEqual(un.choose(un.read_headers(headers(auth=SPOOF_FAIL))), un.NOT_VERIFIED)


class HostileHeaderTests(unittest.TestCase):
    """High 1: a bad or hostile List-Unsubscribe header never raises: not in reading it, not in thread_from_api, not
    in the standing watch's ref_from (whose raise stalled every standing mail rule)."""

    MANY = "&".join(f"x{i}=1" for i in range(25))
    HOSTILE = (f"<mailto:leave@lists.example.com?{MANY}>", "<mailto://[leave@lists.example.com>",
               "<https://[::1>", "<mailto:%>", "<" + "a" * 3000 + ">")

    def test_reading_never_raises(self) -> None:
        for value in self.HOSTILE:
            with self.subTest(value=value[:40]):
                header = un.read_headers(headers(unsub=value))
                self.assertIsNone(header.mailto)

    def test_the_thread_and_the_standing_watch_read_on(self) -> None:
        from briefing_reader.ask.mail import thread_from_api
        from briefing_reader.standing import mailwatch

        for value in self.HOSTILE:
            with self.subTest(value=value[:40]):
                data = gmail_data(AUTH_OK, unsub=value)
                thread = thread_from_api({"id": "thr0000777", "messages": [data]}, "work")
                self.assertIsNotNone(thread)
                self.assertIsNotNone(mailwatch.ref_from(data, "work"))

    def test_any_error_in_the_reading_offers_nothing(self) -> None:
        from unittest import mock

        from briefing_reader.ask import mail

        with mock.patch.object(un, "_read_headers", side_effect=RuntimeError("boom")):
            self.assertFalse(un.read_headers(headers()).present)
        with mock.patch.object(un, "read_headers", side_effect=RuntimeError("boom")):
            self.assertIsNone(mail._list_header(headers()))


class TimeoutTests(unittest.TestCase):
    """Low 10: connecting and reading share the one 10 s; an answer still coming at the deadline is unknown."""

    def test_connect_and_read_never_pass_what_is_left(self) -> None:
        for left in (10.0, 6.0, 3.0, 0.5):
            with self.subTest(left=left):
                connect, read = un.post_timeouts(left)
                self.assertLessEqual(connect, un.CONNECT_S)
                self.assertLessEqual(connect + read, max(left, 0.2) + 1e-9)

    def test_a_late_answer_is_unknown(self) -> None:
        def post(url: str, timeout: float, cap: int) -> int:
            raise TimeoutError("the answer took too long")

        result = un.one_click(URL, post=post, resolve=lambda _h: ["93.184.216.34"])
        self.assertEqual((result.outcome, result.reason), (un.UNKNOWN, un.REASON_TIMEOUT))


if __name__ == "__main__":
    unittest.main()
