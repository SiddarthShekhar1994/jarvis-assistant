"""Outlook sign-in (ms_auth): PKCE, the authorization request, the token store and its refresh, with a fake HTTP
session. Nothing here signs in, opens a browser or reaches the network (requests and sockets are patched to fail)."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import socket
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from briefing_reader import ms_auth
from briefing_reader.google_auth import (
    PROBLEM_BLOCKED,
    PROBLEM_DENIED,
    PROBLEM_EXPIRED,
    PROBLEM_SETUP,
    AccountAuthError,
    AccountError,
    AccountNotSignedIn,
    AccountSetupError,
)

CLIENT_ID = "11111111-2222-3333-4444-555555555555"
ACCESS = "eyJ0eXAiOiJKV1QiLCJhbGciOiJub25lIn0.access-token-value-one"
REFRESH = "M.R3_BAY.refresh-token-value-one-0123456789"
NOW = 1_800_000_000.0


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    """Records every call; answers from ``answers`` in turn."""

    def __init__(self, *answers: FakeResponse) -> None:
        self.answers = list(answers)
        self.posts: list[tuple[str, dict[str, str]]] = []
        self.gets: list[tuple[str, Any, dict[str, str]]] = []

    def post(self, url: str, *, data: dict[str, str], timeout: float, headers: dict[str, str]) -> FakeResponse:
        self.posts.append((url, dict(data)))
        return self.answers.pop(0)

    def get(self, url: str, *, params: Any, headers: dict[str, str], timeout: float) -> FakeResponse:
        self.gets.append((url, params, dict(headers)))
        return self.answers.pop(0)


class NoNetwork(unittest.TestCase):
    """Every test: requests' sessions and sockets fail loudly, so nothing can reach the network."""

    def setUp(self) -> None:
        super().setUp()
        refuse = AssertionError("a test reached for the network")
        for target in (mock.patch("requests.Session.request", side_effect=refuse),
                       mock.patch.object(socket.socket, "connect", side_effect=refuse),
                       mock.patch("socket.create_connection", side_effect=refuse),
                       mock.patch("webbrowser.open", side_effect=refuse)):
            target.start()
            self.addCleanup(target.stop)
        self.dir = Path(tempfile.mkdtemp(prefix="ms-auth-"))

    def account(self, session: Any = None, *, client_id: str = CLIENT_ID, tenant: str = "common",
                clock: Any = None, opener: Any = None) -> ms_auth.MicrosoftAccount:
        return ms_auth.MicrosoftAccount("school", client_id=client_id, tenant=tenant, data_dir=self.dir,
                                        session=session, clock=clock or (lambda: NOW),
                                        opener=opener or (lambda url: self.fail("the browser was opened")))

    def write_token(self, **changes: Any) -> Path:
        info = {"access_token": ACCESS, "refresh_token": REFRESH, "expires_at": NOW + 3600,
                "scope": " ".join(ms_auth.MICROSOFT_SCOPES), "tenant": "common"}
        info.update(changes)
        path = ms_auth.token_path(self.dir, "school")
        path.write_text(json.dumps(info), encoding="utf-8")
        return path


class PkceTests(NoNetwork):
    def test_verifier_is_long_random_and_unreserved(self) -> None:
        first, second = ms_auth.make_verifier(), ms_auth.make_verifier()
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertRegex(first, r"^[A-Za-z0-9._~-]{43,128}$")

    def test_challenge_is_s256_without_padding(self) -> None:
        verifier = ms_auth.make_verifier()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(ms_auth.challenge(verifier), expected)
        self.assertEqual(len(ms_auth.challenge(verifier)), 43)
        self.assertNotIn("=", ms_auth.challenge(verifier))

    def test_challenge_refuses_a_short_or_odd_verifier(self) -> None:
        for bad in ("short", "x" * 129, "a b" * 20, 42):
            with self.assertRaises(ValueError):
                ms_auth.challenge(bad)  # type: ignore[arg-type]

    def test_authorize_url_asks_for_a_code_with_pkce_and_read_scopes(self) -> None:
        url = ms_auth.authorize_url(CLIENT_ID, "consumers", "http://localhost:51234/", "chal", "st8")
        parts = urlsplit(url)
        self.assertEqual((parts.scheme, parts.netloc, parts.path),
                         ("https", "login.microsoftonline.com", "/consumers/oauth2/v2.0/authorize"))
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        self.assertEqual(query["client_id"], CLIENT_ID)
        self.assertEqual(query["response_type"], "code")
        self.assertEqual(query["redirect_uri"], "http://localhost:51234/")
        self.assertEqual(query["code_challenge"], "chal")
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertEqual(query["state"], "st8")
        self.assertEqual(query["scope"].split(), ["offline_access", "User.Read", "Mail.Read", "Calendars.Read"])
        self.assertNotIn("client_secret", query)

    def test_a_bad_tenant_falls_back_to_common(self) -> None:
        self.assertEqual(ms_auth.endpoints("../evil")[1], "https://login.microsoftonline.com/common/oauth2/v2.0/token")
        self.assertTrue(ms_auth.valid_tenant("organizations"))
        self.assertTrue(ms_auth.valid_tenant("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))
        self.assertFalse(ms_auth.valid_tenant("contoso"))

    def test_the_answer_must_carry_this_sign_ins_state(self) -> None:
        account = self.account()
        with self.assertRaises(AccountAuthError):
            account._code_from({"code": "abc", "state": "other"}, "mine")
        with self.assertRaises(AccountAuthError) as caught:
            account._code_from({"error": "access_denied", "state": "mine"}, "mine")
        self.assertEqual(caught.exception.problem, PROBLEM_DENIED)
        with self.assertRaises(AccountAuthError) as caught:
            account._code_from({"error": "invalid_request", "error_description": "AADSTS65001: no consent",
                                "state": "mine"}, "mine")
        self.assertEqual(caught.exception.problem, PROBLEM_BLOCKED)
        self.assertEqual(account._code_from({"code": "the-code-123", "state": "mine"}, "mine"), "the-code-123")


class NoClientIdTests(NoNetwork):
    def test_without_a_client_id_nothing_starts_and_the_message_says_why(self) -> None:
        opened: list[str] = []
        account = self.account(client_id="", opener=opened.append)
        self.assertFalse(account.is_configured())
        self.assertEqual(account.setup_problem(), ms_auth.NO_CLIENT_ID_MESSAGE)
        self.assertIn("[microsoft] client_id", ms_auth.NO_CLIENT_ID_MESSAGE)
        with self.assertRaises(AccountSetupError) as caught:
            account.sign_in()
        self.assertEqual(str(caught.exception), ms_auth.NO_CLIENT_ID_MESSAGE)
        self.assertEqual(account.problem()[0], PROBLEM_SETUP)
        self.assertEqual(opened, [])
        with self.assertRaises(AccountSetupError):
            account.access_token()

    def test_a_client_id_that_is_not_a_guid_is_not_configured(self) -> None:
        self.assertFalse(self.account(client_id="my-app").is_configured())

    def test_a_background_account_never_signs_in(self) -> None:
        account = ms_auth.MicrosoftAccount("school", client_id=CLIENT_ID, data_dir=self.dir, persist=False)
        with self.assertRaises(AccountNotSignedIn):
            account.sign_in()


class TokenStoreTests(NoNetwork):
    def test_token_file_is_named_per_alias_beside_the_google_ones(self) -> None:
        self.assertEqual(ms_auth.token_path(self.dir, "school"), self.dir / "ms_token_school.json")
        with self.assertRaises(ValueError):
            ms_auth.token_path(self.dir, "../x")

    def test_round_trip_a_saved_token_is_used_without_any_request(self) -> None:
        session = FakeSession()
        writer = self.account(session)
        info = writer._token_info({"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600,
                                   "scope": "Mail.Read"})
        writer._save(info)
        reader = self.account(session)
        self.assertTrue(reader.is_signed_in())
        self.assertEqual(reader.access_token(), ACCESS)
        self.assertEqual(session.posts, [])
        saved = json.loads(ms_auth.token_path(self.dir, "school").read_text(encoding="utf-8"))
        self.assertEqual(saved["expires_at"], NOW + 3600)
        self.assertEqual(saved["refresh_token"], REFRESH)
        self.assertEqual(list(self.dir.glob("*.part")), [])

    def test_not_signed_in_without_a_file(self) -> None:
        account = self.account(FakeSession())
        self.assertFalse(account.is_signed_in())
        with self.assertRaises(AccountNotSignedIn):
            account.access_token()

    def test_an_expiring_token_is_refreshed_and_the_new_refresh_token_kept(self) -> None:
        self.write_token(expires_at=NOW + 30)
        session = FakeSession(FakeResponse(200, {"access_token": "new-access-token-0123456789",
                                                 "refresh_token": "new-refresh-token-0123456789",
                                                 "expires_in": 3599, "scope": "Mail.Read Calendars.Read"}))
        account = self.account(session)
        self.assertEqual(account.access_token(), "new-access-token-0123456789")
        (url, data), = session.posts
        self.assertEqual(url, "https://login.microsoftonline.com/common/oauth2/v2.0/token")
        self.assertEqual(data["grant_type"], "refresh_token")
        self.assertEqual(data["refresh_token"], REFRESH)
        self.assertEqual(data["client_id"], CLIENT_ID)
        self.assertNotIn("client_secret", data)
        saved = json.loads(ms_auth.token_path(self.dir, "school").read_text(encoding="utf-8"))
        self.assertEqual(saved["refresh_token"], "new-refresh-token-0123456789")
        self.assertEqual(saved["expires_at"], NOW + 3599)
        self.assertEqual(account.access_token(), "new-access-token-0123456789")   # fresh now: no second POST
        self.assertEqual(len(session.posts), 1)

    def test_a_refresh_without_a_new_refresh_token_keeps_the_old_one(self) -> None:
        self.write_token(expires_at=NOW - 1)
        session = FakeSession(FakeResponse(200, {"access_token": "another-access-token-0123", "expires_in": 60}))
        account = self.account(session)
        account.access_token()
        saved = json.loads(ms_auth.token_path(self.dir, "school").read_text(encoding="utf-8"))
        self.assertEqual(saved["refresh_token"], REFRESH)

    def test_a_rejected_refresh_deletes_the_sign_in(self) -> None:
        path = self.write_token(expires_at=NOW - 1)
        session = FakeSession(FakeResponse(400, {"error": "invalid_grant",
                                                 "error_description": "AADSTS70008: expired"}))
        account = self.account(session)
        with self.assertRaises(AccountAuthError) as caught:
            account.access_token()
        self.assertEqual(caught.exception.problem, PROBLEM_EXPIRED)
        self.assertFalse(path.exists())
        self.assertFalse(account.is_signed_in())
        self.assertEqual(account.problem()[0], PROBLEM_EXPIRED)

    def test_a_server_error_keeps_the_sign_in(self) -> None:
        path = self.write_token(expires_at=NOW - 1)
        account = self.account(FakeSession(FakeResponse(503, None)))
        with self.assertRaises(AccountError) as caught:
            account.access_token()
        self.assertNotIsInstance(caught.exception, AccountAuthError)
        self.assertTrue(path.exists())

    def test_disconnect_deletes_the_file(self) -> None:
        path = self.write_token()
        account = self.account(FakeSession())
        account.disconnect()
        self.assertFalse(path.exists())
        self.assertFalse(account.is_signed_in())
        account.disconnect()   # nothing left: still fine

    def test_no_token_or_code_reaches_the_log(self) -> None:
        self.write_token(expires_at=NOW - 1)
        session = FakeSession(FakeResponse(200, {"access_token": "logged-access-token-0123456789",
                                                 "refresh_token": "logged-refresh-token-0123456789",
                                                 "expires_in": 3600}))
        with self.assertLogs("briefing_reader.ms_auth", logging.DEBUG) as logs:
            self.account(session).access_token()
        text = "\n".join(logs.output)
        for secret in (REFRESH, ACCESS, "logged-access-token", "logged-refresh-token", CLIENT_ID):
            self.assertNotIn(secret, text)
        self.assertIn("Outlook (other)", text)


if __name__ == "__main__":
    unittest.main()
