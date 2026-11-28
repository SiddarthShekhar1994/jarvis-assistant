"""Outlook writes (stage 2): the sign-in's new permissions and the read-only sign-in's "Sign in again", the exact
Microsoft Graph request of each change with a fake HTTP session (add, answer, move, cancel, their Undo, a reply and
an email), the executor's routing per backend, the recipient checks, the click-time check that the answered message is
still there, the hold (nothing sent but at its end), and the action worker's sign-in state and Sign in for an Outlook
account. requests' sessions and sockets are patched to fail: nothing reaches the network, nothing signs in."""

from __future__ import annotations

import json
import threading
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from briefing_reader import executor, ms_auth, outlook, outlook_actions, outlook_ui, ui
from briefing_reader.actions import (
    STATUS_CREATED,
    STATUS_FAILED,
    STATUS_SENT,
    ActionStore,
    make_calendar,
    parse_action_line,
)
from briefing_reader.config import AccountConfig
from briefing_reader.gcal import CalendarUnknownOutcome, EventChanged, NotAllowed
from briefing_reader.gmail import GmailError, GmailUnknownOutcome
from briefing_reader.google_auth import PROBLEM_SCOPE
from briefing_reader.recipients import RecipientHistory
from tests.test_microsoft_auth import ACCESS, NOW, REFRESH, FakeResponse, NoNetwork

GRAPH = "https://graph.microsoft.com/v1.0"
ME = "me@school.edu"
EVENT_ID = "AAMkAGI2LWV2ZW50LTAwMQ="
MESSAGE_ID = "AAMkAGI2LW1zZy0wMDE="
CONVERSATION = "AAQkAGI2LWNvbnYtMDAx="
ACCOUNTS = {"personal": AccountConfig("personal"),
            "school": AccountConfig("school", backend="microsoft", features=("calendar", "mail_read"))}
READ_ONLY_SCOPE = "Mail.Read Calendars.Read User.Read openid profile"


def graph_event(**changes: Any) -> dict[str, Any]:
    item = {"id": EVENT_ID, "subject": "Project sync", "isAllDay": False, "isCancelled": False, "isOrganizer": True,
            "start": {"dateTime": "2026-12-08T17:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-12-08T18:00:00.0000000", "timeZone": "UTC"},
            "organizer": {"emailAddress": {"name": "Me", "address": ME}},
            "attendees": [{"emailAddress": {"address": "ana@example.edu"}, "type": "required"}],
            "responseStatus": {"response": "organizer"}, "type": "singleInstance",
            "webLink": "https://outlook.office365.com/owa/?itemid=x", "changeKey": "ck-1"}
    item.update(changes)
    return item


class GraphSession:
    """A fake requests.Session: answers in turn, every call recorded (method, url, params, json)."""

    def __init__(self, *answers: FakeResponse) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, Any, Any]] = []

    def get(self, url: str, *, params: Any, headers: dict[str, str], timeout: float) -> FakeResponse:
        self.calls.append(("GET", url, params, None))
        return self._answer()

    def request(self, method: str, url: str, *, headers: dict[str, str], timeout: float,
                json: Any = None) -> FakeResponse:   # noqa: A002 - requests' own name
        assert headers["Authorization"] == f"Bearer {ACCESS}"
        self.calls.append((method, url, None, json))
        return self._answer()

    def post(self, url: str, *, data: dict[str, str], timeout: float, headers: dict[str, str]) -> FakeResponse:
        self.calls.append(("TOKEN", url, dict(data), None))
        return self._answer()

    def _answer(self) -> FakeResponse:
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def changes(self) -> list[tuple[str, str, Any]]:
        return [(method, url, body) for method, url, _params, body in self.calls if method not in ("GET", "TOKEN")]


def ok(body: Any = None, status: int = 200) -> FakeResponse:
    return FakeResponse(status, body if body is not None else {})


class WriteTestCase(NoNetwork):
    def signed_in(self, *answers: FakeResponse, scope: str = "", address: str = ME) -> tuple[Any, GraphSession]:
        changes: dict[str, Any] = {"address": address} if address else {}
        if scope:
            changes["scope"] = scope
        self.write_token(**changes)
        session = GraphSession(*answers)
        return self.account(session), session

    def calendar(self, *answers: FakeResponse, **kwargs: Any) -> tuple[outlook.OutlookCalendar, GraphSession]:
        account, session = self.signed_in(*answers, **kwargs)
        return outlook.OutlookCalendar(account), session

    def sender(self, *answers: FakeResponse, **kwargs: Any) -> tuple[outlook.OutlookSender, GraphSession]:
        account, session = self.signed_in(*answers, **kwargs)
        return outlook.OutlookSender(account), session


# --------------------------------------------------------------------------
# The sign-in: permissions, the address, the read-only sign-in
# --------------------------------------------------------------------------

class PermissionTests(WriteTestCase):
    def test_the_sign_in_asks_to_change_the_calendar_and_send_mail_but_not_mail_readwrite(self) -> None:
        self.assertIn("Calendars.ReadWrite", ms_auth.MICROSOFT_SCOPES)
        self.assertIn("Mail.Send", ms_auth.MICROSOFT_SCOPES)
        self.assertNotIn("Mail.ReadWrite", ms_auth.MICROSOFT_SCOPES)   # reply needs no draft

    def test_a_full_sign_in_may_change_both(self) -> None:
        account, _session = self.signed_in()
        self.assertEqual(account.write_problem(ms_auth.WRITE_CALENDAR), ("", ""))
        self.assertEqual(account.write_problem(ms_auth.WRITE_MAIL), ("", ""))
        self.assertEqual(account.address(), ME)

    def test_a_read_only_sign_in_says_sign_in_again(self) -> None:
        account, _session = self.signed_in(scope=READ_ONLY_SCOPE)
        for what in (ms_auth.WRITE_CALENDAR, ms_auth.WRITE_MAIL):
            self.assertEqual(account.write_problem(what), (PROBLEM_SCOPE, ms_auth.SIGN_IN_AGAIN_MESSAGE))
        self.assertEqual(ms_auth.SIGN_IN_AGAIN_MESSAGE, "Sign in again to let Jarvis change this account")

    def test_graph_url_scopes_count(self) -> None:
        account, _session = self.signed_in(
            scope="https://graph.microsoft.com/Calendars.ReadWrite https://graph.microsoft.com/Mail.Send")
        self.assertEqual(account.write_problem(ms_auth.WRITE_CALENDAR), ("", ""))

    def test_mail_needs_the_address(self) -> None:
        account, _session = self.signed_in(address="")
        self.assertEqual(account.write_problem(ms_auth.WRITE_MAIL), (PROBLEM_SCOPE, ms_auth.NO_ADDRESS_MESSAGE))
        self.assertEqual(account.write_problem(ms_auth.WRITE_CALENDAR), ("", ""))

    def test_a_read_only_sign_in_refreshes_with_its_own_permissions_only(self) -> None:
        self.write_token(scope=READ_ONLY_SCOPE, expires_at=NOW - 1, address=ME)
        session = GraphSession(ok({"access_token": ACCESS, "expires_in": 3600, "scope": READ_ONLY_SCOPE}))
        account = self.account(session)
        account.access_token()
        (_method, _url, data, _body), = session.calls
        self.assertEqual(data["scope"].split(), ["offline_access", "User.Read", "Mail.Read"])
        self.assertNotIn("ReadWrite", data["scope"])
        self.assertNotIn("Mail.Send", data["scope"])
        self.assertEqual(account.address(), ME)                       # kept across the refresh
        self.assertEqual(ms_auth.refresh_scopes({}), ms_auth.READ_SCOPES)

    def test_the_address_is_read_from_me_after_the_sign_in(self) -> None:
        session = GraphSession(ok({"mail": None, "userPrincipalName": "Ana.Lee@school.edu"}))
        account = self.account(session)
        self.assertEqual(account._read_address(ACCESS), "Ana.Lee@school.edu")
        (method, url, params, _body), = session.calls
        self.assertEqual((method, url, params), ("GET", f"{GRAPH}/me", {"$select": "mail,userPrincipalName"}))
        self.assertEqual(self.account(GraphSession(ok({}, 403)))._read_address(ACCESS), "")


# --------------------------------------------------------------------------
# Calendar requests
# --------------------------------------------------------------------------

def move_card(**changes: str) -> Any:
    fields = {"acct": "school", "event": EVENT_ID, "when": "2026-12-08 14:00-15:00", "notify": "all",
              "title": "Project sync"}
    fields.update(changes)
    action = parse_action_line("Move: " + " | ".join(f"{key}={value}" for key, value in fields.items()))
    assert not action.error, action.error
    return action


def utc_wall(moment: datetime) -> dict[str, str]:
    instant = moment.astimezone().astimezone(timezone.utc)
    return {"dateTime": instant.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}


class CalendarRequestTests(WriteTestCase):
    NOW_ = datetime(2026, 12, 1, 9, 0).astimezone()

    def test_add_looks_first_then_posts_the_event(self) -> None:
        calendar, session = self.calendar(ok({"value": []}), ok({"id": "AAMk-new=", "changeKey": "ck-9",
                                                                 "webLink": "https://outlook.office365.com/x"}, 201))
        card = make_calendar("Chess club", start=datetime(2026, 12, 9, 15, 0), end=datetime(2026, 12, 9, 16, 0),
                             where="Room 4", notes="Bring a board")
        created = calendar.create_event(card)
        self.assertEqual((created.event_id, created.stamp, created.existed), ("AAMk-new=", "ck-9", False))
        look, post = session.calls
        self.assertEqual(look[:2], ("GET", f"{GRAPH}/me/calendarView"))
        self.assertEqual(post[:2], ("POST", f"{GRAPH}/me/events"))
        body = post[3]
        self.assertEqual(body["subject"], "Chess club")
        self.assertEqual(body["start"], utc_wall(datetime(2026, 12, 9, 15, 0)))
        self.assertEqual(body["end"], utc_wall(datetime(2026, 12, 9, 16, 0)))
        self.assertEqual(body["location"], {"displayName": "Room 4"})
        self.assertEqual(body["body"]["contentType"], "text")
        self.assertTrue(body["body"]["content"].startswith("Bring a board\n\n"))
        self.assertNotIn("attendees", body)

    def test_an_event_already_there_is_not_added_again(self) -> None:
        start = datetime(2026, 12, 9, 15, 0).astimezone().astimezone(timezone.utc)
        same = {"id": "AAMk-1", "subject": "Chess  club", "isAllDay": False,
                "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
                "end": {"dateTime": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}}
        calendar, session = self.calendar(ok({"value": [same]}))
        created = calendar.create_event(make_calendar("Chess club", start=datetime(2026, 12, 9, 15, 0),
                                                      end=datetime(2026, 12, 9, 16, 0)))
        self.assertTrue(created.existed)
        self.assertEqual(session.changes(), [])

    def test_an_all_day_event_uses_midnights(self) -> None:
        body = outlook.event_body(make_calendar("Conference", all_day_start=date(2026, 12, 10),
                                                all_day_end=date(2026, 12, 11)))
        self.assertTrue(body["isAllDay"])
        self.assertEqual(body["start"]["dateTime"], "2026-12-10T00:00:00")
        self.assertEqual(body["end"]["dateTime"], "2026-12-12T00:00:00")   # exclusive

    def test_a_repeating_event_is_refused(self) -> None:
        card = parse_action_line("Calendar: Gym | 2026-12-09 07:00-08:00 | weekly")
        with self.assertRaises(NotAllowed):
            outlook.event_body(card)

    def test_move_patches_start_and_end_in_utc(self) -> None:
        calendar, session = self.calendar(ok(graph_event()), ok(graph_event(changeKey="ck-2")))
        card = move_card()
        change = calendar.move(EVENT_ID, card.start, card.end, now=self.NOW_)
        (method, url, body), = session.changes()
        self.assertEqual((method, url), ("PATCH", f"{GRAPH}/me/events/{EVENT_ID.replace('=', '%3D')}"))
        self.assertEqual(body, {"start": utc_wall(card.start), "end": utc_wall(card.end)})
        self.assertEqual((change.stamp, change.already), ("ck-2", False))
        self.assertEqual(change.before.start, datetime(2026, 12, 8, 17, 0, tzinfo=timezone.utc))

    def test_a_quiet_move_of_an_event_with_guests_is_refused_unsent(self) -> None:
        calendar, session = self.calendar(ok(graph_event()))
        card = move_card(notify="none")
        with self.assertRaises(NotAllowed) as caught:
            calendar.move(EVENT_ID, card.start, card.end, tell_guests=False, now=self.NOW_)
        self.assertIn("Outlook tells every guest", str(caught.exception))
        self.assertEqual(session.changes(), [])

    def test_a_move_of_an_event_you_dont_organise_is_refused(self) -> None:
        calendar, session = self.calendar(ok(graph_event(isOrganizer=False)))
        card = move_card()
        with self.assertRaises(NotAllowed):
            calendar.move(EVENT_ID, card.start, card.end, now=self.NOW_)
        self.assertEqual(session.changes(), [])

    def test_cancel_with_guests_posts_cancel_with_the_note(self) -> None:
        calendar, session = self.calendar(ok(graph_event()), ok(status=202))
        calendar.cancel(EVENT_ID, comment="Sorry, moving to next week")
        (method, url, body), = session.changes()
        self.assertEqual((method, url.rsplit("/", 1)[1], body), ("POST", "cancel",
                                                                 {"comment": "Sorry, moving to next week"}))

    def test_cancel_without_guests_deletes(self) -> None:
        calendar, session = self.calendar(ok(graph_event(attendees=[])), ok(status=204))
        calendar.cancel(EVENT_ID)
        (method, _url, body), = session.changes()
        self.assertEqual((method, body), ("DELETE", None))

    def test_cancel_of_a_gone_event_is_already_done(self) -> None:
        calendar, session = self.calendar(ok({"error": {}}, 404))
        self.assertTrue(calendar.cancel(EVENT_ID).already)
        self.assertEqual(session.changes(), [])

    def test_answer_posts_accept_with_send_response_then_reads_the_stamp(self) -> None:
        invite = graph_event(isOrganizer=False, responseStatus={"response": "declined"})
        calendar, session = self.calendar(ok(invite), ok(status=202), ok(dict(invite, changeKey="ck-3")))
        change = calendar.respond(EVENT_ID, "yes", comment="See you there", send_response=False)
        (method, url, body), = session.changes()
        self.assertEqual((method, url.rsplit("/", 1)[1]), ("POST", "accept"))
        self.assertEqual(body, {"comment": "See you there", "sendResponse": False})
        self.assertEqual((change.stamp, change.before.self_response), ("ck-3", "declined"))

    def test_undo_of_an_added_event_deletes_it_only_while_unchanged(self) -> None:
        mine = graph_event(attendees=[], changeKey="ck-9")
        calendar, session = self.calendar(ok(mine), ok(status=204))
        calendar.undo_create(EVENT_ID, stamp="ck-9")
        self.assertEqual([call[0] for call in session.changes()], ["DELETE"])
        calendar, session = self.calendar(ok(dict(mine, changeKey="ck-10")))
        with self.assertRaises(EventChanged):
            calendar.undo_create(EVENT_ID, stamp="ck-9")
        self.assertEqual(session.changes(), [])

    def test_undo_of_a_move_patches_the_old_time_back(self) -> None:
        before = datetime(2026, 12, 8, 17, 0, tzinfo=timezone.utc)
        calendar, session = self.calendar(ok(graph_event(changeKey="ck-2")), ok(graph_event(changeKey="ck-4")))
        calendar.undo_move(EVENT_ID, before, before + timedelta(hours=1), stamp="ck-2")
        (method, _url, body), = session.changes()
        self.assertEqual(method, "PATCH")
        self.assertEqual(body["start"], {"dateTime": "2026-12-08T17:00:00", "timeZone": "UTC"})

    def test_a_server_error_or_a_lost_answer_is_unknown_and_a_refused_connection_is_not(self) -> None:
        import requests

        card = move_card()
        for failure in (ok(status=503), requests.exceptions.ReadTimeout("slow"), requests.exceptions.ConnectionError()):
            calendar, session = self.calendar(ok(graph_event()), failure)
            with self.assertRaises(CalendarUnknownOutcome):
                calendar.move(EVENT_ID, card.start, card.end, now=self.NOW_)
            self.assertEqual(len(session.calls), 2)   # never retried
        calendar, _session = self.calendar(ok(graph_event()), requests.exceptions.ConnectTimeout())
        with self.assertRaises(Exception) as caught:
            calendar.move(EVENT_ID, card.start, card.end, now=self.NOW_)
        self.assertNotIsInstance(caught.exception, CalendarUnknownOutcome)

    def test_a_401_on_a_change_is_not_retried(self) -> None:
        calendar, session = self.calendar(ok(graph_event()), ok(status=401))
        card = move_card()
        with self.assertRaises(Exception):
            calendar.move(EVENT_ID, card.start, card.end, now=self.NOW_)
        self.assertEqual(len(session.changes()), 1)
        self.assertNotIn("TOKEN", [call[0] for call in session.calls])

    def test_event_details_say_outlook(self) -> None:
        details = outlook.details_from(graph_event(isOrganizer=False, responseStatus={"response": "notResponded"},
                                                   type="occurrence"), EVENT_ID, ME)
        self.assertEqual((details.service, details.self_email, details.self_response), ("Outlook", ME, "needsAction"))
        self.assertTrue(details.recurring_instance)
        check = executor.check_event(move_card(), outlook.details_from(graph_event(), EVENT_ID, ME), date(2026, 12, 1))
        self.assertTrue(check.text.startswith("Outlook"), check.text)
        self.assertTrue(check.allowed)


# --------------------------------------------------------------------------
# Mail requests
# --------------------------------------------------------------------------

class MailRequestTests(WriteTestCase):
    def mail(self, **changes: Any) -> Any:
        fields = {"account": "school", "from_addr": ME, "to": ("ana@example.edu",), "cc": ("ben@example.edu",),
                  "subject": "Re: Lab results", "body": "Thanks Ana\nSee you"}
        fields.update(changes)
        return outlook.OutgoingMail(**fields)

    def test_the_reply_checks_the_message_then_posts_reply_with_the_cards_recipients_and_text(self) -> None:
        sender, session = self.sender(ok({"id": MESSAGE_ID, "conversationId": CONVERSATION,
                                          "internetMessageId": "<abc123@example.edu>"}), ok(status=202))
        sender.check_message(MESSAGE_ID, conversation=CONVERSATION, internet_id="<abc123@example.edu>")
        sender.reply(MESSAGE_ID, self.mail())
        check, post = session.calls
        self.assertEqual(check[0], "GET")
        self.assertEqual((post[0], post[1].rsplit("/", 1)[1]), ("POST", "reply"))
        self.assertEqual(post[3], {"message": {
            "toRecipients": [{"emailAddress": {"address": "ana@example.edu"}}],
            "ccRecipients": [{"emailAddress": {"address": "ben@example.edu"}}],
            "body": {"contentType": "Text", "content": "Thanks Ana\nSee you"}}})
        self.assertNotIn("bccRecipients", json.dumps(post[3]))

    def test_a_gone_message_sends_nothing(self) -> None:
        sender, session = self.sender(ok({"error": {}}, 404))
        with self.assertRaises(GmailError) as caught:
            sender.check_message(MESSAGE_ID)
        self.assertEqual(str(caught.exception), outlook.MESSAGE_GONE_MESSAGE)
        self.assertEqual(session.changes(), [])

    def test_a_message_of_another_conversation_sends_nothing(self) -> None:
        sender, _session = self.sender(ok({"id": MESSAGE_ID, "conversationId": "AAQkOTHER-conv0000="}))
        with self.assertRaises(GmailError):
            sender.check_message(MESSAGE_ID, conversation=CONVERSATION)

    def test_a_new_email_posts_send_mail(self) -> None:
        sender, session = self.sender(ok(status=202))
        sender.send(self.mail(subject="Hello", cc=()))
        (method, url, body), = session.changes()
        self.assertEqual((method, url), ("POST", f"{GRAPH}/me/sendMail"))
        self.assertEqual(body["message"]["subject"], "Hello")
        self.assertTrue(body["saveToSentItems"])
        self.assertNotIn("ccRecipients", body["message"])

    def test_a_send_without_a_clear_answer_is_unknown(self) -> None:
        sender, session = self.sender(ok(status=500))
        with self.assertRaises(GmailUnknownOutcome):
            sender.send(self.mail())
        self.assertEqual(len(session.calls), 1)


# --------------------------------------------------------------------------
# The executor: routing, readiness, recipients, hold, Undo
# --------------------------------------------------------------------------

def reply_card(**changes: str) -> Any:
    fields = {"acct": "school", "thread": CONVERSATION, "msgid": "<abc123@example.edu>", "gmid": MESSAGE_ID,
              "to": "ana@example.edu", "subject": "Lab results", "body": "Thanks Ana"}
    fields.update(changes)
    action = parse_action_line("Reply: " + " | ".join(f"{key}={value}" for key, value in fields.items() if value))
    assert not action.error, action.error
    return action


class ExecutorTests(WriteTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = ActionStore(self.dir / "actions.json")
        self.history = RecipientHistory(self.dir / "recipients.json")

    def build(self, *answers: FakeResponse, **kwargs: Any) -> tuple[executor.Executor, GraphSession]:
        account, session = self.signed_in(*answers, **kwargs)
        google = SimpleNamespace(is_configured=lambda: True, is_signed_in=lambda: True)
        backends = [executor.CalendarBackend({"personal": google}),
                    executor.GmailBackend({}, history=self.history),
                    outlook_actions.OutlookCalendarBackend({"school": outlook.OutlookCalendar(account)},
                                                           now=lambda: datetime(2026, 12, 1, 9).astimezone()),
                    outlook_actions.OutlookMailBackend({"school": outlook.OutlookSender(account)},
                                                       history=self.history)]
        return executor.Executor(backends, ACCOUNTS), session

    def test_routing_per_backend(self) -> None:
        run, _session = self.build()
        self.assertIsInstance(run.backend_for(move_card()), outlook_actions.OutlookCalendarBackend)
        self.assertIsInstance(run.backend_for(reply_card()), outlook_actions.OutlookMailBackend)
        personal = move_card(acct="personal", event="abc123def456ghi789")
        self.assertIsInstance(run.backend_for(personal), executor.CalendarBackend)
        self.assertIsInstance(run.backend_for(make_calendar("Chess", start=datetime(2026, 12, 9, 15))),
                              executor.CalendarBackend)   # a Calendar card names no account: personal's Google one
        named = replace(make_calendar("Chess", start=datetime(2026, 12, 9, 15)), account="school")
        self.assertIsInstance(run.backend_for(named), outlook_actions.OutlookCalendarBackend)
        self.assertEqual(run.readiness(move_card()), "")

    def test_a_google_card_with_an_outlook_id_is_refused(self) -> None:
        run, _session = self.build()
        self.assertEqual(run.readiness(move_card(acct="personal")), executor.NOT_GOOGLE_EVENT_NOTE)

    def test_ask_and_standing_cards_never_act_for_outlook(self) -> None:
        run, _session = self.build()
        for source in ("ask", "standing"):
            self.assertIn("Google accounts only", run.readiness(replace(move_card(), source=source)))
            self.assertIn("Google accounts only", run.readiness(replace(reply_card(), source=source)))

    def test_a_read_only_sign_in_changes_nothing_and_its_card_offers_sign_in(self) -> None:
        run, session = self.build(scope=READ_ONLY_SCOPE)
        card = move_card()
        self.assertEqual(run.readiness(card), "")
        self.assertFalse(run.signed_in(card))
        outcome = executor.run_action(run, card, store=self.store, writes_running=True)
        self.assertEqual(outcome.status, STATUS_FAILED)
        self.assertEqual(str(outcome.error), ms_auth.SIGN_IN_AGAIN_MESSAGE)
        self.assertEqual(outcome.error.problem, PROBLEM_SCOPE)
        status = run.mail_status(reply_card())
        self.assertTrue(status.sign_in)
        self.assertIn(ms_auth.SIGN_IN_AGAIN_MESSAGE, status.note)
        self.assertEqual(session.calls, [])

    def test_a_move_runs_once_and_its_undo_puts_the_time_back(self) -> None:
        run, session = self.build(ok(graph_event()), ok(graph_event(changeKey="ck-2")),
                                  ok(graph_event(changeKey="ck-2")), ok(graph_event(changeKey="ck-5")))
        card = move_card()
        outcome = executor.run_action(run, card, store=self.store, writes_running=True)
        self.assertEqual(outcome.status, STATUS_SENT, outcome.error)
        record = outcome.result.undo
        self.assertEqual((record.kind, record.alias, record.stamp), ("move", "school", "ck-2"))
        undone = executor.run_undo(run, card, record, store=self.store)
        self.assertEqual(undone.status, executor.UNDONE, undone.error)
        self.assertEqual([call[0] for call in session.changes()], ["PATCH", "PATCH"])
        self.assertEqual(session.changes()[1][2]["start"], {"dateTime": "2026-12-08T17:00:00", "timeZone": "UTC"})

    def test_an_added_event_has_an_undo(self) -> None:
        run, session = self.build(ok({"value": []}), ok({"id": "AAMk-new=", "changeKey": "ck-9"}, 201))
        card = replace(make_calendar("Chess", start=datetime(2026, 12, 9, 15), end=datetime(2026, 12, 9, 16)),
                       account="school")
        outcome = executor.run_action(run, card, store=self.store, writes_running=True)
        self.assertEqual(outcome.status, STATUS_CREATED, outcome.error)
        self.assertEqual((outcome.result.undo.event_id, outcome.result.undo.stamp), ("AAMk-new=", "ck-9"))

    def test_a_reply_waits_for_its_new_recipient_tick_then_sends_once(self) -> None:
        run, session = self.build(ok({"id": MESSAGE_ID, "conversationId": CONVERSATION,
                                      "internetMessageId": "<abc123@example.edu>"}), ok(status=202))
        card = reply_card()
        status = run.mail_status(card)
        self.assertTrue(status.needs_edit)                 # ana@example.edu is NEW
        self.assertEqual(status.from_text, f"From: school ({ME})")
        outcome = executor.run_action(run, card, store=self.store, writes_running=True)
        self.assertEqual(str(outcome.error), executor.NEW_RECIPIENTS_MESSAGE)
        self.assertEqual(session.calls, [])
        ticked = executor.apply_edit(card, executor.ActionEdit(confirmed_new=frozenset({"ana@example.edu"})))
        self.assertTrue(run.mail_status(ticked).ready)
        outcome = executor.run_action(run, ticked, store=self.store, writes_running=True)
        self.assertEqual(outcome.status, STATUS_SENT, outcome.error)
        self.assertEqual(self.store.get(ticked.id)["status"], STATUS_SENT)
        self.assertEqual([call[0] for call in session.calls], ["GET", "POST"])
        self.assertTrue(self.history.knows("ana@example.edu"))   # remembered: no tick next time

    def test_a_reply_to_yourself_is_refused(self) -> None:
        run, session = self.build()
        card = reply_card(to=ME)
        self.assertTrue(run.mail_status(card).refused)
        outcome = executor.run_action(run, card, store=self.store, writes_running=True)
        self.assertEqual(outcome.status, STATUS_FAILED)
        self.assertEqual(session.calls, [])

    def test_mail_goes_only_at_the_end_of_the_hold(self) -> None:
        run, session = self.build()
        card = executor.apply_edit(reply_card(), executor.ActionEdit(confirmed_new=frozenset({"ana@example.edu"})))
        with self.assertRaises(executor.ExecError) as caught:
            run.execute(card)                              # the click itself never sends
        self.assertEqual(str(caught.exception), executor.HOLD_ONLY_MESSAGE)
        outcome = executor.run_action(run, card, store=self.store, writes_running=False)
        self.assertEqual(str(outcome.error), executor.HOLD_ONLY_MESSAGE)
        self.assertEqual(session.calls, [])

    def test_a_reply_whose_message_is_gone_sends_nothing(self) -> None:
        run, session = self.build(ok({"error": {}}, 404))
        card = executor.apply_edit(reply_card(), executor.ActionEdit(confirmed_new=frozenset({"ana@example.edu"})))
        outcome = executor.run_action(run, card, store=self.store, writes_running=True)
        self.assertEqual((outcome.status, str(outcome.error)), (STATUS_FAILED, outlook.MESSAGE_GONE_MESSAGE))
        self.assertEqual(session.changes(), [])

    def test_a_reply_without_its_outlook_message_is_a_hand_off(self) -> None:
        run, _session = self.build()
        card = reply_card(gmid="")
        self.assertIn("names no Outlook message", run.readiness(card))
        self.assertTrue(run.mail_status(card).hand_off)

    def test_the_preview_says_what_outlook_will_do(self) -> None:
        run, _session = self.build()
        values = run.preview(move_card()).values()
        self.assertIn("every guest", values["Who gets an email"])
        values = run.preview(move_card(notify="none")).values()
        self.assertIn("refused for an event with guests", values["Who gets an email"])
        card = executor.apply_edit(reply_card(), executor.ActionEdit(confirmed_new=frozenset({"ana@example.edu"})))
        values = run.preview(card).values()
        self.assertEqual(values["From"], ME)
        self.assertIn("RE:", values["Subject"])

    def test_build_executor_adds_the_outlook_backends_only_when_asked(self) -> None:
        config = SimpleNamespace(accounts=ACCOUNTS, actions=SimpleNamespace(trusted_domains=()))
        plain = executor.build_executor(config, {}, {}, history=self.history)
        self.assertIsNone(plain.backend_for(move_card()))
        self.assertIn("Outlook account", plain.readiness(move_card()))
        both = executor.build_executor(config, {}, {}, history=self.history, outlook_calendars={}, outlook_senders={})
        self.assertIsInstance(both.backend_for(move_card()), outlook_actions.OutlookCalendarBackend)


class OutlookIdLineTests(NoNetwork):
    def test_outlook_ids_are_read_from_lines(self) -> None:
        self.assertEqual(move_card().field("event"), EVENT_ID)
        card = reply_card()
        self.assertEqual((card.field("thread"), card.field("gmid")), (CONVERSATION, MESSAGE_ID))
        self.assertTrue(parse_action_line("Move: acct=school | event=has space | when=2026-12-08 14:00-15:00").error)


# --------------------------------------------------------------------------
# The action worker and Settings
# --------------------------------------------------------------------------

class WorkerTests(WriteTestCase):
    def test_the_sign_in_check_says_whether_an_outlook_account_may_be_changed(self) -> None:
        heard: list[Any] = []
        bridge = SimpleNamespace(accountStatus=SimpleNamespace(emit=heard.append))
        account, _session = self.signed_in(scope=READ_ONLY_SCOPE)
        worker = ui._ActionWorker(None, {}, None, bridge, threading.Event(),
                                  outlook={"school": outlook.OutlookCalendar(account)})
        self.addCleanup(worker.stop)
        worker._check_sign_ins()
        self.assertEqual(heard[-1]["school"], (False, PROBLEM_SCOPE, ms_auth.SIGN_IN_AGAIN_MESSAGE))
        self.write_token(address=ME)    # all the permissions now
        worker._check_sign_ins()
        self.assertEqual(heard[-1]["school"], (True, "", ""))

    def test_a_cards_sign_in_click_signs_a_read_only_outlook_account_in_again(self) -> None:
        calls: list[str] = []
        heard: list[tuple[Any, ...]] = []
        calendar = SimpleNamespace(is_signed_in=lambda: True, write_problem=lambda: (PROBLEM_SCOPE, "again"),
                                   sign_in=lambda: calls.append("sign_in"))
        bridge = SimpleNamespace(accountConnect=SimpleNamespace(emit=lambda *args: heard.append(args)))
        worker = ui._ActionWorker(None, {}, None, bridge, threading.Event(), outlook={"school": calendar})
        self.addCleanup(worker.stop)
        worker._connect("school")
        self.assertEqual(calls, ["sign_in"])
        self.assertEqual(heard[-1][1], ui._CONNECT_SIGNED_IN)

    def test_a_held_reply_jarvis_closes_on_is_not_sent_without_a_usable_sign_in(self) -> None:
        store = ActionStore(self.dir / "actions.json")
        account, session = self.signed_in(scope=READ_ONLY_SCOPE)
        run = executor.Executor([outlook_actions.OutlookMailBackend({"school": outlook.OutlookSender(account)})],
                                ACCOUNTS)
        worker = ui._ActionWorker(run, {}, store, SimpleNamespace(), threading.Event())
        self.addCleanup(worker.stop)
        card = reply_card()
        worker._exec(ui._ExecJob(card, writes_running=True, expected_from=ME, flush=True))
        self.assertEqual(store.get(card.id)["status"], STATUS_FAILED)
        self.assertEqual(session.calls, [])


class SettingsReadOnlyTests(NoNetwork):
    def test_a_read_only_account_offers_sign_in_again(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication(["test_microsoft_write", "-platform", "offscreen"])
        calls: list[str] = []
        account = SimpleNamespace(is_configured=lambda: True, is_signed_in=lambda: True,
                                  write_problem=lambda what: (PROBLEM_SCOPE, ms_auth.SIGN_IN_AGAIN_MESSAGE),
                                  sign_in=lambda: calls.append("sign_in"), disconnect=lambda: calls.append("off"))
        connect = outlook_ui.OutlookConnect({"school": account}, threaded=False)
        panel = outlook_ui.OutlookAccountsPanel(["school"])
        connect.attach_panel(panel)
        self.assertEqual(panel.states["school"].text(), outlook_ui.READ_ONLY_TEXT)
        self.assertEqual(panel.buttons["school"].text(), outlook_ui.SIGN_IN_AGAIN_TEXT)
        self.assertIn(ms_auth.SIGN_IN_AGAIN_MESSAGE, panel.notes["school"].text())
        panel.buttons["school"].click()
        self.assertEqual(calls, ["sign_in"])


class NothingLoggedTests(WriteTestCase):
    def test_no_address_subject_or_id_reaches_the_log(self) -> None:
        calendar, _session = self.calendar(ok(graph_event()), ok(graph_event(changeKey="ck-2")))
        card = move_card()
        with self.assertLogs("briefing_reader.outlook", "DEBUG") as logs:
            calendar.move(EVENT_ID, card.start, card.end, now=datetime(2026, 12, 1, 9).astimezone())
        text = "\n".join(logs.output)
        for secret in (ME, "Project sync", EVENT_ID, ACCESS, REFRESH, "ana@example.edu"):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
