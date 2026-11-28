"""Outlook readers (outlook.py): Graph JSON fixtures mapped to the shapes Google's readers give - gcal.CalendarEvent for
the calendar, the Gmail metadata dict for mail - with a fake HTTP session. Nothing reaches the network."""

from __future__ import annotations

import json
import logging
import unittest
from datetime import date, datetime, timedelta, timezone
from typing import Any

from briefing_reader import ms_auth, outlook
from briefing_reader.gcal import CalendarAuthError, CalendarEvent, CalendarNotSignedIn, CalendarSetupError
from briefing_reader.gmail import MailReadError
from briefing_reader.google_auth import PROBLEM_EXPIRED, PROBLEM_SCOPE, PROBLEM_SETUP, PROBLEM_SIGNED_OUT
from tests.test_microsoft_auth import NOW, FakeResponse, FakeSession, NoNetwork

GRAPH = "https://graph.microsoft.com/v1.0"
TIMED = {"id": "AAMk-1", "subject": "  Lab   meeting ", "isAllDay": False, "isCancelled": False,
         "start": {"dateTime": "2026-11-30T17:00:00.0000000", "timeZone": "UTC"},
         "end": {"dateTime": "2026-11-30T18:30:00.0000000", "timeZone": "UTC"},
         "location": {"displayName": "Room 4"}, "webLink": "https://outlook.office365.com/owa/?itemid=AAMk-1",
         "showAs": "busy", "responseStatus": {"response": "accepted"}, "iCalUId": "uid-1"}
ALL_DAY = {"id": "AAMk-2", "subject": "Conference", "isAllDay": True,
           "start": {"dateTime": "2026-11-30T00:00:00.0000000", "timeZone": "UTC"},
           "end": {"dateTime": "2026-12-02T00:00:00.0000000", "timeZone": "UTC"},
           "location": {}, "showAs": "free", "responseStatus": {"response": "declined"}, "iCalUId": "uid-2"}
CANCELLED = dict(TIMED, id="AAMk-3", isCancelled=True, iCalUId="uid-3")
MESSAGE = {"id": "AAMkAGI2-msg=", "conversationId": "AAQkAGI2-conv", "subject": "Lab  results",
           "from": {"emailAddress": {"name": "Ana Lee", "address": "ana@example.edu"}},
           "toRecipients": [{"emailAddress": {"name": "", "address": "me@example.edu"}},
                            {"emailAddress": {"name": "Ben", "address": "ben@example.edu"}}],
           "ccRecipients": [], "replyTo": [], "receivedDateTime": "2026-11-28T09:15:00Z",
           "bodyPreview": "Here are the\r\n numbers from today", "isRead": False,
           "internetMessageId": "<abc123@example.edu>"}


class ReaderTestCase(NoNetwork):
    def signed_in(self, session: FakeSession) -> ms_auth.MicrosoftAccount:
        self.write_token()
        return self.account(session)


class CalendarMappingTests(ReaderTestCase):
    def test_a_timed_event_becomes_a_calendar_event_in_local_time(self) -> None:
        event = outlook.event_from(TIMED)
        self.assertIsInstance(event, CalendarEvent)
        assert event is not None and event.start is not None and event.end is not None
        self.assertEqual(event.start, datetime(2026, 11, 30, 17, 0, tzinfo=timezone.utc))
        self.assertEqual(event.end - event.start, timedelta(minutes=90))
        self.assertIsNotNone(event.start.utcoffset())
        self.assertEqual(event.start.utcoffset(), event.start.astimezone().utcoffset())   # this PC's zone
        self.assertEqual(event.title, "Lab meeting")
        self.assertEqual(event.location, "Room 4")
        self.assertEqual(event.calendar_id, outlook.OUTLOOK_CALENDAR_ID)
        self.assertEqual(event.ical_uid, "uid-1")
        self.assertFalse(event.declined)
        self.assertFalse(event.free)
        self.assertFalse(event.all_day)

    def test_an_all_day_event_has_dates_with_an_inclusive_last_day(self) -> None:
        event = outlook.event_from(ALL_DAY)
        assert event is not None
        self.assertTrue(event.all_day)
        self.assertEqual((event.all_day_start, event.all_day_end), (date(2026, 11, 30), date(2026, 12, 1)))
        self.assertIsNone(event.start)
        self.assertTrue(event.declined)
        self.assertTrue(event.free)

    def test_cancelled_or_unreadable_events_are_left_out(self) -> None:
        self.assertIsNone(outlook.event_from(CANCELLED))
        self.assertIsNone(outlook.event_from({"subject": "x", "start": {"dateTime": "soon"}}))
        self.assertIsNone(outlook.event_from("not a dict"))
        self.assertEqual(outlook.event_from(dict(TIMED, subject="")).title, "(No title)")

    def test_another_zone_name_is_read_as_an_offset(self) -> None:
        item = dict(TIMED, start={"dateTime": "2026-11-30T09:00:00", "timeZone": "America/New_York"})
        event = outlook.event_from(item)
        assert event is not None and event.start is not None
        self.assertEqual(event.start.astimezone(timezone.utc).hour, 14)


class CalendarReadTests(ReaderTestCase):
    def test_list_events_asks_calendar_view_in_utc_and_sorts(self) -> None:
        session = FakeSession(FakeResponse(200, {"value": [TIMED, ALL_DAY, CANCELLED]}))
        calendar = outlook.OutlookCalendar(self.signed_in(session))
        start = datetime(2026, 11, 30, 0, 0, tzinfo=timezone(timedelta(hours=-8)))
        events = calendar.list_events(start, start + timedelta(days=2), ("primary",))
        self.assertEqual([event.title for event in events], ["Conference", "Lab meeting"])
        (url, params, headers), = session.gets
        self.assertEqual(url, f"{GRAPH}/me/calendarView")
        self.assertEqual(params["startDateTime"], "2026-11-30T08:00:00Z")
        self.assertEqual(params["endDateTime"], "2026-12-02T08:00:00Z")
        self.assertIn("subject", params["$select"])
        self.assertNotIn("body", params["$select"].split(","))
        self.assertEqual(headers["Prefer"], 'outlook.timezone="UTC"')
        self.assertTrue(headers["Authorization"].startswith("Bearer "))

    def test_next_pages_are_followed_only_on_graph_itself(self) -> None:
        second = f"{GRAPH}/me/calendarView?$skiptoken=abc"
        session = FakeSession(FakeResponse(200, {"value": [TIMED], "@odata.nextLink": second}),
                              FakeResponse(200, {"value": [ALL_DAY], "@odata.nextLink": "https://evil.example/x?y"}))
        calendar = outlook.OutlookCalendar(self.signed_in(session))
        start = datetime(2026, 11, 30, tzinfo=timezone.utc)
        events = calendar.list_events(start, start + timedelta(days=3))
        self.assertEqual(len(events), 2)
        self.assertEqual([call[0] for call in session.gets], [f"{GRAPH}/me/calendarView", second])
        self.assertIsNone(session.gets[1][1])

    def test_never_signs_in_and_never_asks_without_a_sign_in(self) -> None:
        session = FakeSession()
        calendar = outlook.OutlookCalendar(self.account(session))
        start = datetime(2026, 11, 30, tzinfo=timezone.utc)
        with self.assertRaises(CalendarNotSignedIn):
            calendar.list_events(start, start + timedelta(days=1))
        self.assertEqual((session.gets, session.posts), ([], []))
        self.assertFalse(calendar.is_signed_in())

    def test_without_a_client_id_it_is_a_setup_problem(self) -> None:
        self.write_token()
        calendar = outlook.OutlookCalendar(self.account(FakeSession(), client_id=""))
        self.assertFalse(calendar.is_configured())
        self.assertEqual(calendar.sign_in_problem(), (PROBLEM_SETUP, ms_auth.NO_CLIENT_ID_MESSAGE))
        start = datetime(2026, 11, 30, tzinfo=timezone.utc)
        with self.assertRaises(CalendarSetupError):
            calendar.list_events(start, start + timedelta(days=1))

    def test_a_401_refreshes_once_then_a_second_401_drops_the_sign_in(self) -> None:
        refreshed = {"access_token": "second-access-token-0123456789", "expires_in": 3600}
        session = FakeSession(FakeResponse(401, {}), FakeResponse(200, refreshed), FakeResponse(401, {}))
        account = self.signed_in(session)
        calendar = outlook.OutlookCalendar(account)
        start = datetime(2026, 11, 30, tzinfo=timezone.utc)
        with self.assertRaises(CalendarAuthError) as caught:
            calendar.list_events(start, start + timedelta(days=1))
        self.assertEqual(caught.exception.problem, PROBLEM_EXPIRED)
        self.assertEqual(len(session.posts), 1)          # one refresh
        self.assertEqual(len(session.gets), 2)
        self.assertEqual(session.gets[1][2]["Authorization"], "Bearer second-access-token-0123456789")
        self.assertFalse(account.token_path.exists())

    def test_a_401_then_a_good_answer_reads_the_events(self) -> None:
        refreshed = {"access_token": "second-access-token-0123456789", "expires_in": 3600}
        session = FakeSession(FakeResponse(401, {}), FakeResponse(200, refreshed),
                              FakeResponse(200, {"value": [TIMED]}))
        calendar = outlook.OutlookCalendar(self.signed_in(session))
        start = datetime(2026, 11, 30, tzinfo=timezone.utc)
        self.assertEqual(len(calendar.list_events(start, start + timedelta(days=1))), 1)

    def test_a_403_keeps_the_sign_in(self) -> None:
        session = FakeSession(FakeResponse(403, {}))
        account = self.signed_in(session)
        start = datetime(2026, 11, 30, tzinfo=timezone.utc)
        with self.assertRaises(CalendarAuthError) as caught:
            outlook.OutlookCalendar(account).list_events(start, start + timedelta(days=1))
        self.assertEqual(caught.exception.problem, PROBLEM_SCOPE)
        self.assertTrue(account.token_path.exists())

    def test_the_log_names_no_title_and_no_token(self) -> None:
        session = FakeSession(FakeResponse(200, {"value": [TIMED]}))
        calendar = outlook.OutlookCalendar(self.signed_in(session))
        start = datetime(2026, 11, 30, tzinfo=timezone.utc)
        with self.assertLogs("briefing_reader", logging.DEBUG) as logs:
            calendar.list_events(start, start + timedelta(days=1))
        text = "\n".join(logs.output)
        self.assertNotIn("Lab", text)
        self.assertNotIn("Room 4", text)
        self.assertNotIn("access-token", text)
        self.assertNotIn("2026-11-30T00", text)


class MailTests(ReaderTestCase):
    def test_a_message_has_the_gmail_metadata_shape_plus_a_snippet(self) -> None:
        shape = outlook.message_from(MESSAGE)
        assert shape is not None
        self.assertEqual(set(shape), {"id", "threadId", "labelIds", "internalDate", "snippet", "payload"})
        self.assertEqual(shape["id"], "AAMkAGI2-msg=")
        self.assertEqual(shape["threadId"], "AAQkAGI2-conv")
        self.assertEqual(shape["labelIds"], ["INBOX", "UNREAD"])
        self.assertEqual(shape["internalDate"],
                         str(int(datetime(2026, 11, 28, 9, 15, tzinfo=timezone.utc).timestamp() * 1000)))
        self.assertEqual(shape["snippet"], "Here are the numbers from today")
        headers = {item["name"]: item["value"] for item in shape["payload"]["headers"]}
        self.assertEqual(headers["From"], "Ana Lee <ana@example.edu>")
        self.assertEqual(headers["To"], "me@example.edu, Ben <ben@example.edu>")
        self.assertEqual(headers["Subject"], "Lab results")
        self.assertEqual(headers["Message-ID"], "<abc123@example.edu>")
        self.assertIn("28 Nov 2026 09:15:00", headers["Date"])
        self.assertNotIn("Cc", headers)
        self.assertEqual(outlook.message_from(dict(MESSAGE, isRead=True))["labelIds"], ["INBOX"])

    def test_recent_reads_the_inbox_headers_only(self) -> None:
        session = FakeSession(FakeResponse(200, {"value": [MESSAGE, {"no": "id"}]}))
        reader = outlook.OutlookMailReader(self.signed_in(session))
        self.assertEqual(reader.available(), ("", ""))
        found = reader.recent(days=30, max_results=500)
        self.assertEqual(len(found), 1)
        (url, params, _headers), = session.gets
        self.assertEqual(url, f"{GRAPH}/me/mailFolders/inbox/messages")
        self.assertEqual(params["$top"], "50")
        self.assertTrue(params["$filter"].startswith("receivedDateTime ge "))
        fields = params["$select"].split(",")
        self.assertIn("bodyPreview", fields)
        self.assertNotIn("body", fields)
        self.assertNotIn("attachments", fields)

    def test_message_metadata_checks_the_id_and_quotes_it(self) -> None:
        session = FakeSession(FakeResponse(200, MESSAGE))
        reader = outlook.OutlookMailReader(self.signed_in(session))
        self.assertEqual(reader.message_metadata("AAMkAGI2-msg=")["id"], "AAMkAGI2-msg=")
        self.assertEqual(session.gets[0][0], f"{GRAPH}/me/messages/AAMkAGI2-msg%3D")
        with self.assertRaises(MailReadError):
            reader.message_metadata("../me/sendMail")

    def test_available_says_why_mail_cant_be_read(self) -> None:
        self.assertEqual(outlook.OutlookMailReader(self.account(FakeSession(), client_id="")).available()[0],
                         PROBLEM_SETUP)
        reader = outlook.OutlookMailReader(self.account(FakeSession()))
        self.assertEqual(reader.available()[0], PROBLEM_SIGNED_OUT)
        with self.assertRaises(MailReadError) as caught:
            reader.recent()
        self.assertEqual(caught.exception.problem, PROBLEM_SIGNED_OUT)

    def test_a_403_turns_reading_off_until_the_next_sign_in(self) -> None:
        session = FakeSession(FakeResponse(403, {}))
        reader = outlook.OutlookMailReader(self.signed_in(session))
        with self.assertRaises(MailReadError) as caught:
            reader.recent()
        self.assertEqual(caught.exception.problem, PROBLEM_SCOPE)
        self.assertEqual(reader.available()[0], PROBLEM_SCOPE)
        with self.assertRaises(MailReadError):
            reader.recent()
        self.assertEqual(len(session.gets), 1)            # no second request while refused

    def test_the_log_names_no_subject_or_address(self) -> None:
        session = FakeSession(FakeResponse(200, {"value": [MESSAGE]}))
        reader = outlook.OutlookMailReader(self.signed_in(session))
        with self.assertLogs("briefing_reader", logging.DEBUG) as logs:
            reader.recent()
        text = "\n".join(logs.output)
        for word in ("Lab", "ana@", "Ana Lee", "numbers"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
