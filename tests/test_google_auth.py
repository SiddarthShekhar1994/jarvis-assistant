"""Tests for briefing_reader.google_auth: per-account sign-ins, the legacy token, partial grants,
rejected and blocked sign-ins, and the HTTP client that never sends a change twice.

Nothing here touches the network or a browser: the OAuth flow is a fake, token refreshes are
injected, and every socket connection raises NetworkUsed (a BaseException, so no broad handler
in the code under test can hide it). Every token, secret and address is invented and unique
per test; nothing reads the owner's data folder.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import socket
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from google.auth.exceptions import RefreshError, TransportError
from google_auth_oauthlib.flow import WSGITimeoutError
from oauthlib.oauth2.rfc6749.errors import AccessDeniedError, CustomOAuth2Error

from briefing_reader import google_auth
from briefing_reader.config import REDACTED, RedactingFilter
from briefing_reader.google_auth import (
    CALENDAR_FEATURE,
    FEATURE_SCOPES,
    LEGACY_TOKEN,
    PROBLEM_BLOCKED,
    PROBLEM_CONFIRM,
    PROBLEM_DENIED,
    PROBLEM_EXPIRED,
    PROBLEM_FAILED,
    PROBLEM_IDENTITY,
    PROBLEM_SCOPE,
    PROBLEM_SETUP,
    PROBLEM_SIGNED_OUT,
    PROBLEM_TIMEOUT,
    SIGN_IN_PROMPT,
    AccountAuthError,
    AccountBindings,
    AccountError,
    AccountNotSignedIn,
    AccountSetupError,
    GoogleAccount,
    RequestNotResent,
    account_token_path,
    features_granted,
    logged_alias,
    migrate_legacy_token,
    scopes_for,
    single_send_http,
    token_path,
    valid_alias,
)

AUTH_LOGGER = "briefing_reader.google_auth"
CLIENT_ID = "1234567890-fakeclient.apps.googleusercontent.com"
EVENTS = "https://www.googleapis.com/auth/calendar.events"
SETTINGS = "https://www.googleapis.com/auth/calendar.settings.readonly"
CAL_SCOPES = [EVENTS, SETTINGS]


class NetworkUsed(BaseException):
    """Raised by the socket guard; a BaseException so broad handlers cannot hide it."""


def _no_network(*args: Any, **kwargs: Any) -> Any:
    raise NetworkUsed("a test tried to open a network connection")


def _utc_naive(delta: timedelta) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) + delta


class FakeCreds:
    """What InstalledAppFlow.run_local_server returns: ``scopes`` are the requested ones,
    ``granted_scopes`` what Google's token answer said (None when it said nothing)."""

    def __init__(self, suffix: str, client_secret: str, *, granted: list[str] | None = None,
                 requested: list[str] | None = None) -> None:
        self.token = f"ya29.fake-signed-in-token-{suffix}"
        self.refresh_token = f"1//fake-signed-in-refresh-{suffix}"
        self.client_secret = client_secret
        self.scopes = list(requested or CAL_SCOPES)
        self.granted_scopes = granted
        self.id_token: str | None = None
        self.valid = True

    def to_json(self) -> str:
        return json.dumps({
            "token": self.token, "refresh_token": self.refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token", "client_id": CLIENT_ID,
            "client_secret": self.client_secret, "scopes": self.scopes,
            "expiry": f"{_utc_naive(timedelta(hours=1)).isoformat()}Z"})


class FakeFlow:
    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []
        self.relax_during_call: list[str | None] = []

    def run_local_server(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        self.relax_during_call.append(os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE"))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, BaseException):
            raise result
        return result


class AuthTestCase(unittest.TestCase):
    def setUp(self) -> None:
        for target, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                             (socket, "create_connection"), (socket, "getaddrinfo")):
            patcher = mock.patch.object(target, name, _no_network)
            patcher.start()
            self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data_dir = self.root / "data"
        self.secret_path = self.root / "google_client_secret.json"
        self.suffix = uuid.uuid4().hex
        self.client_secret = f"GOCSPX-fake-client-secret-{self.suffix}"
        self.access_token = f"ya29.fake-access-token-{self.suffix}"
        self.refresh_token = f"1//fake-refresh-token-{self.suffix}"
        self.flow = FakeFlow([FakeCreds(self.suffix, self.client_secret)])
        self.flow_args: list[tuple[Path, list[str]]] = []
        self.refreshes: list[Any] = []

    # -- builders --

    def write_client_secret(self) -> None:
        self.secret_path.write_text(json.dumps({"installed": {
            "client_id": CLIENT_ID, "client_secret": self.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]}}), encoding="utf-8")

    def write_token(self, path: Path, *, scopes: Any = CAL_SCOPES, expired: bool = False,
                    token: str | None = None) -> Path:
        info: dict[str, Any] = {
            "token": token or self.access_token, "refresh_token": self.refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token", "client_id": CLIENT_ID,
            "client_secret": self.client_secret,
            "expiry": f"{_utc_naive(timedelta(hours=-2 if expired else 1)).isoformat()}Z"}
        if scopes is not None:
            info["scopes"] = scopes
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(info), encoding="utf-8")
        return path

    def flow_factory(self, path: Path, scopes: list[str]) -> FakeFlow:
        self.flow_args.append((path, scopes))
        return self.flow

    def account(self, alias: str = "work", *, refresh: Any = None, **kwargs: Any) -> GoogleAccount:
        return GoogleAccount(alias, client_secret_path=self.secret_path, data_dir=self.data_dir,
                             flow_factory=self.flow_factory, refresh=refresh or self.refreshes.append, **kwargs)

    def assert_redacted(self, *secrets: str) -> None:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "values: %s", (" ".join(secrets),), None)
        RedactingFilter().filter(record)
        for secret in secrets:
            self.assertNotIn(secret, record.getMessage())
        self.assertIn(REDACTED, record.getMessage())


# --------------------------------------------------------------------------
# Aliases, paths and the legacy token
# --------------------------------------------------------------------------

class PathAndAliasTests(AuthTestCase):
    def test_one_token_file_per_alias(self) -> None:
        self.assertEqual(token_path(self.data_dir, "work"), self.data_dir / "google_token_work.json")
        self.assertEqual(token_path(self.data_dir, "personal"), self.data_dir / "google_token_personal.json")
        for bad in ("", "Work", "../work", "work mail", "a" * 25, "1work", "work.json"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                token_path(self.data_dir, bad)

    def test_valid_and_logged_aliases(self) -> None:
        self.assertTrue(valid_alias("lab-2"))
        self.assertFalse(valid_alias("Lab"))
        self.assertFalse(valid_alias(None))   # type: ignore[arg-type]
        self.assertEqual([logged_alias(a) for a in ("work", "personal", "lab-2", "")],
                         ["work", "personal", "other", "-"])

    def test_scrub_alias_writes_other_aliases_as_other(self) -> None:
        self.assertEqual(google_auth.scrub_alias("Not signed in (lab-2 account); lab-2's sign-in", "lab-2"),
                         "Not signed in (other account); other's sign-in")
        self.assertEqual(google_auth.scrub_alias("the school account, schooling, lab-school", "school"),
                         "the other account, schooling, lab-school")
        self.assertEqual(google_auth.scrub_alias("the work account", "work"), "the work account")
        self.assertEqual(google_auth.scrub_alias("no alias", ""), "no alias")

    def test_personal_keeps_using_a_legacy_token_that_could_not_be_moved(self) -> None:
        legacy = self.write_token(self.data_dir / LEGACY_TOKEN)
        self.assertEqual(account_token_path(self.data_dir, "personal"), legacy)
        self.assertEqual(account_token_path(self.data_dir, "work"), self.data_dir / "google_token_work.json")
        own = self.write_token(self.data_dir / "google_token_personal.json")
        self.assertEqual(account_token_path(self.data_dir, "personal"), own)

    def test_the_account_picks_its_own_token(self) -> None:
        self.write_client_secret()
        work = self.write_token(self.data_dir / "google_token_work.json", token=f"ya29.work-{self.suffix}")
        self.assertEqual(self.account("work").token_path, work)
        self.assertTrue(self.account("work").is_signed_in(CALENDAR_FEATURE))
        self.assertFalse(self.account("personal").is_signed_in())
        creds = self.account("work").credentials(interactive=False, need=CALENDAR_FEATURE)
        self.assertEqual(creds.token, f"ya29.work-{self.suffix}")
        with self.assertRaises(AccountNotSignedIn):
            self.account("personal").credentials(interactive=False)
        self.assertEqual(self.flow.calls, [])

    def test_needs_a_data_dir_or_a_token_path(self) -> None:
        with self.assertRaises(ValueError):
            GoogleAccount("work", client_secret_path=self.secret_path)
        explicit = self.root / "elsewhere.json"
        self.assertEqual(GoogleAccount("", client_secret_path=self.secret_path, token_path=explicit).token_path,
                         explicit)


class MigrationTests(AuthTestCase):
    def test_moves_the_legacy_token_to_personal_once(self) -> None:
        legacy = self.write_token(self.data_dir / LEGACY_TOKEN)
        content = legacy.read_bytes()
        with self.assertLogs(AUTH_LOGGER, level="INFO") as logs:
            self.assertTrue(migrate_legacy_token(self.data_dir))
        target = self.data_dir / "google_token_personal.json"
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(sorted(p.name for p in self.data_dir.iterdir()), [target.name])   # moved, not copied
        self.assertIn("personal", "\n".join(logs.output))
        self.assertNotIn(str(self.data_dir), "\n".join(logs.output))
        self.assertFalse(migrate_legacy_token(self.data_dir))   # nothing left to move

    def test_an_existing_personal_token_is_never_overwritten(self) -> None:
        legacy = self.write_token(self.data_dir / LEGACY_TOKEN, token=f"ya29.old-{self.suffix}")
        own = self.write_token(self.data_dir / "google_token_personal.json", token=f"ya29.new-{self.suffix}")
        self.assertFalse(migrate_legacy_token(self.data_dir))
        self.assertIn(f"ya29.new-{self.suffix}", own.read_text(encoding="utf-8"))
        self.assertTrue(legacy.exists())

    def test_no_legacy_token_or_no_folder(self) -> None:
        self.assertFalse(migrate_legacy_token(self.data_dir))
        self.assertFalse(self.data_dir.exists())

    def test_a_failed_move_keeps_the_old_file_in_use(self) -> None:
        self.write_client_secret()
        legacy = self.write_token(self.data_dir / LEGACY_TOKEN)
        with mock.patch.object(google_auth.os, "replace", side_effect=PermissionError(13, "in use", str(legacy))), \
                self.assertLogs(AUTH_LOGGER, level="WARNING") as logs:
            self.assertFalse(migrate_legacy_token(self.data_dir))
        self.assertNotIn(str(self.data_dir), "\n".join(logs.output))
        self.assertTrue(legacy.exists())
        personal = self.account("personal")
        self.assertEqual(personal.token_path, legacy)
        self.assertTrue(personal.is_signed_in(CALENDAR_FEATURE))

    def test_the_migrated_sign_in_works_without_signing_in_again(self) -> None:
        self.write_client_secret()
        self.write_token(self.data_dir / LEGACY_TOKEN, scopes=None)   # files of older versions may lack scopes
        migrate_legacy_token(self.data_dir)
        personal = self.account("personal")
        self.assertTrue(personal.is_signed_in(CALENDAR_FEATURE))
        self.assertEqual(personal.granted_features(), frozenset({CALENDAR_FEATURE}))
        creds = personal.credentials(interactive=True, need=CALENDAR_FEATURE)
        self.assertEqual(creds.token, self.access_token)
        self.assertEqual(self.flow.calls, [])


# --------------------------------------------------------------------------
# Sign-in per account
# --------------------------------------------------------------------------

class SignInTests(AuthTestCase):
    def test_signs_in_one_account_and_saves_only_its_token(self) -> None:
        self.write_client_secret()
        personal = self.write_token(self.data_dir / "google_token_personal.json")
        before = personal.read_bytes()
        work = self.account("work")
        self.assertFalse(work.is_signed_in())
        with self.assertLogs(AUTH_LOGGER, level="INFO") as logs:
            work.sign_in()
        self.assertEqual(self.flow_args, [(self.secret_path, CAL_SCOPES)])
        (kwargs,) = self.flow.calls
        self.assertEqual(kwargs["prompt"], SIGN_IN_PROMPT)
        self.assertEqual(SIGN_IN_PROMPT, "select_account consent")
        self.assertEqual((kwargs["port"], kwargs["open_browser"], kwargs["timeout_seconds"]), (0, True, 300))
        self.assertIn("work account", kwargs["success_message"])
        saved = json.loads((self.data_dir / "google_token_work.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["refresh_token"], self.flow.results[0].refresh_token)
        self.assertEqual(saved["scopes"], CAL_SCOPES)
        self.assertEqual(personal.read_bytes(), before)
        self.assertTrue(work.is_signed_in(CALENDAR_FEATURE))
        self.assertEqual(work.problem(), ("", ""))
        text = "\n".join(logs.output)
        self.assertIn("Google (work): signed in", text)
        self.assertNotIn(self.flow.results[0].token, text)

    def test_other_aliases_are_logged_as_other(self) -> None:
        self.write_client_secret()
        with self.assertLogs(AUTH_LOGGER, level="INFO") as logs:
            self.account("lab-2").sign_in()
        self.assertIn("Google (other): signed in", "\n".join(logs.output))
        self.assertNotIn("lab-2", "\n".join(logs.output))

    def test_tokens_and_the_client_secret_are_registered_for_redaction(self) -> None:
        self.write_client_secret()
        self.account().sign_in()
        creds = self.flow.results[0]
        self.assert_redacted(creds.token, creds.refresh_token, self.client_secret)

    def test_a_saved_token_is_registered_when_read(self) -> None:
        self.write_client_secret()
        self.write_token(self.data_dir / "google_token_work.json")
        self.account().credentials(interactive=False)
        self.assert_redacted(self.access_token, self.refresh_token)

    def test_not_configured_or_not_signed_in(self) -> None:
        work = self.account()
        self.assertFalse(work.is_configured())
        with self.assertRaises(AccountSetupError) as ctx:
            work.sign_in()
        self.assertEqual(ctx.exception.problem, PROBLEM_SETUP)
        self.write_client_secret()
        with self.assertRaises(AccountNotSignedIn) as ctx2:
            work.credentials(interactive=False)
        self.assertEqual(ctx2.exception.problem, PROBLEM_SIGNED_OUT)
        self.assertEqual(self.flow.calls, [])

    def test_interactive_credentials_sign_in_when_there_is_no_token(self) -> None:
        self.write_client_secret()
        creds = self.account().credentials(interactive=True, need=CALENDAR_FEATURE)
        self.assertEqual(len(self.flow.calls), 1)
        self.assertEqual(creds.token, self.flow.results[0].token)

    def test_requested_scopes_follow_the_features(self) -> None:
        self.assertEqual(scopes_for((CALENDAR_FEATURE,)), CAL_SCOPES)
        self.assertEqual(scopes_for(()), [])
        self.assertEqual(self.account(features=("calendar", "telepathy")).features, ("calendar",))
        self.assertEqual(self.account().requested_scopes(), CAL_SCOPES)
        self.assertEqual(set(FEATURE_SCOPES), {CALENDAR_FEATURE, google_auth.GMAIL_FEATURE,
                                               google_auth.GMAIL_READ_FEATURE})
        self.assertEqual(FEATURE_SCOPES[google_auth.GMAIL_FEATURE],
                         ("https://www.googleapis.com/auth/gmail.send",))   # send only: never read
        # Read only (Ask Jarvis): never a scope that could send, change or delete mail.
        self.assertEqual(FEATURE_SCOPES[google_auth.GMAIL_READ_FEATURE],
                         ("https://www.googleapis.com/auth/gmail.readonly",))
        self.assertEqual(scopes_for(("gmail_read", "calendar", "gmail_send")),
                         CAL_SCOPES + ["https://www.googleapis.com/auth/gmail.send",
                                       "https://www.googleapis.com/auth/gmail.readonly"])


class PartialGrantTests(AuthTestCase):
    def partial_sign_in(self) -> GoogleAccount:
        self.write_client_secret()
        self.flow.results = [FakeCreds(self.suffix, self.client_secret, granted=[EVENTS])]
        work = self.account()
        with self.assertLogs(AUTH_LOGGER, level="WARNING") as logs:
            work.sign_in()
        self.assertIn("without every permission (calendar missing)", "\n".join(logs.output))
        return work

    def test_an_unticked_box_still_signs_in_with_the_granted_scopes_saved(self) -> None:
        work = self.partial_sign_in()
        saved = json.loads(work.token_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["scopes"], [EVENTS])
        self.assertTrue(work.is_signed_in())
        self.assertFalse(work.is_signed_in(CALENDAR_FEATURE))
        self.assertEqual(work.granted_features(), frozenset())
        problem, message = work.problem()
        self.assertEqual(problem, PROBLEM_SCOPE)
        self.assertIn("tick every box", message)
        self.assertIn("work account", message)
        # A fresh object (the next start) reads the same from the file.
        self.assertEqual(self.account().granted_features(), frozenset())

    def test_the_saved_credentials_only_ever_ask_for_what_was_granted(self) -> None:
        work = self.partial_sign_in()
        creds = work.credentials(interactive=False)
        self.assertEqual(list(creds.scopes), [EVENTS])

    def test_a_missing_permission_is_a_scope_problem_or_a_new_full_sign_in(self) -> None:
        work = self.partial_sign_in()
        with self.assertRaises(AccountAuthError) as ctx:
            work.credentials(interactive=False, need=CALENDAR_FEATURE)
        self.assertEqual(ctx.exception.problem, PROBLEM_SCOPE)
        self.assertEqual(len(self.flow.calls), 1)
        self.flow.results = [FakeCreds(uuid.uuid4().hex, self.client_secret, granted=CAL_SCOPES)]
        creds = work.credentials(interactive=True, need=CALENDAR_FEATURE)
        self.assertEqual(len(self.flow.calls), 2)
        self.assertEqual(self.flow_args[-1][1], CAL_SCOPES)   # every scope again: one full consent
        self.assertEqual(creds.token, self.flow.results[0].token)
        self.assertEqual(work.granted_features(), frozenset({CALENDAR_FEATURE}))
        self.assertEqual(work.problem(), ("", ""))

    def test_still_missing_after_the_new_sign_in(self) -> None:
        work = self.partial_sign_in()
        with self.assertLogs(AUTH_LOGGER, level="WARNING"), self.assertRaises(AccountAuthError) as ctx:
            work.credentials(interactive=True, need=CALENDAR_FEATURE)
        self.assertEqual(ctx.exception.problem, PROBLEM_SCOPE)

    def test_relaxed_scope_mode_is_set_only_during_the_flow(self) -> None:
        self.write_client_secret()
        for before in (None, "0"):
            with self.subTest(before=before), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
                if before is not None:
                    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = before
                self.flow.relax_during_call.clear()
                self.account().sign_in()
                self.assertEqual(self.flow.relax_during_call, ["1"])
                self.assertEqual(os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE"), before)

    def test_relaxed_scope_mode_is_restored_after_a_failure(self) -> None:
        self.write_client_secret()
        self.flow.results = [RuntimeError("boom")]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
            with self.assertRaises(AccountError):
                self.account().sign_in()
            self.assertNotIn("OAUTHLIB_RELAX_TOKEN_SCOPE", os.environ)

    def test_granted_features_and_token_file_scopes(self) -> None:
        self.assertEqual(features_granted(CAL_SCOPES), frozenset({CALENDAR_FEATURE}))
        self.assertEqual(features_granted([EVENTS]), frozenset())
        self.assertEqual(features_granted(CAL_SCOPES, ()), frozenset())
        self.write_token(self.data_dir / "google_token_work.json", scopes=" ".join(CAL_SCOPES))
        self.assertEqual(self.account().granted_features(), frozenset({CALENDAR_FEATURE}))
        self.write_token(self.data_dir / "google_token_work.json", scopes=[SETTINGS])
        self.assertEqual(self.account().granted_features(), frozenset())

    def test_the_scope_changed_warning_is_still_mapped(self) -> None:
        self.write_client_secret()
        warning = Warning('Scope has changed from "a b" to "a".')
        warning.token = {"access_token": self.access_token}   # type: ignore[attr-defined]
        self.flow.results = [warning]
        with self.assertRaises(AccountAuthError) as ctx:
            self.account().sign_in()
        self.assertEqual(ctx.exception.problem, PROBLEM_SCOPE)
        self.assertNotIn(self.access_token, str(ctx.exception))


# --------------------------------------------------------------------------
# Rejected, revoked, blocked and cancelled sign-ins
# --------------------------------------------------------------------------

class RejectedSignInTests(AuthTestCase):
    def expired_account(self, error: BaseException) -> GoogleAccount:
        self.write_client_secret()
        self.write_token(self.data_dir / "google_token_work.json", expired=True)

        def refresh(_creds: Any) -> None:
            raise error

        return self.account(refresh=refresh)

    def test_invalid_grant_forgets_the_token_and_asks_to_sign_in_again(self) -> None:
        work = self.expired_account(RefreshError("invalid_grant: Token has been expired or revoked.",
                                                 {"error": "invalid_grant"}))
        self.assertTrue(work.is_signed_in(CALENDAR_FEATURE))   # expired but refreshable
        with self.assertLogs(AUTH_LOGGER, level="WARNING") as logs, \
                self.assertRaises(AccountAuthError) as ctx:
            work.credentials(interactive=False, need=CALENDAR_FEATURE)
        self.assertEqual(ctx.exception.problem, PROBLEM_EXPIRED)
        self.assertIn("work account expired or was revoked; sign in again", str(ctx.exception))
        self.assertFalse(work.token_path.exists())
        self.assertFalse(work.is_signed_in())
        self.assertEqual(work.problem()[0], PROBLEM_EXPIRED)
        self.assertIn("removed the saved sign-in", "\n".join(logs.output))
        with self.assertRaises(AccountNotSignedIn):
            work.credentials(interactive=False)
        self.assertEqual(self.flow.calls, [])

    def test_an_administrator_block_is_told_apart(self) -> None:
        work = self.expired_account(RefreshError("admin_policy_enforced: blocked by the admin"))
        with self.assertLogs(AUTH_LOGGER, level="WARNING"), self.assertRaises(AccountAuthError) as ctx:
            work.credentials(interactive=False)
        self.assertEqual(ctx.exception.problem, PROBLEM_BLOCKED)
        self.assertIn("administrator does not allow this app (admin_policy_enforced)", str(ctx.exception))
        self.assertFalse(work.token_path.exists())
        self.assertEqual(work.problem()[0], PROBLEM_BLOCKED)

    def test_temporary_refresh_failures_keep_the_token(self) -> None:
        for error in (RefreshError("temporarily unavailable", retryable=True),
                      TransportError(f"connection failed for {self.refresh_token}")):
            with self.subTest(error=type(error).__name__):
                work = self.expired_account(error)
                with self.assertRaises(AccountError) as ctx:
                    work.credentials(interactive=False)
                self.assertEqual(ctx.exception.problem, PROBLEM_FAILED)
                self.assertNotIn(self.refresh_token, str(ctx.exception))
                self.assertTrue(work.token_path.exists())

    def test_a_good_refresh_is_saved(self) -> None:
        new_token = f"ya29.fake-refreshed-{uuid.uuid4().hex}"

        def refresh(creds: Any) -> None:
            creds.token = new_token
            creds.expiry = _utc_naive(timedelta(hours=1))

        self.write_client_secret()
        self.write_token(self.data_dir / "google_token_work.json", expired=True)
        work = self.account(refresh=refresh)
        self.assertEqual(work.credentials(interactive=False).token, new_token)
        saved = json.loads(work.token_path.read_text(encoding="utf-8"))
        self.assertEqual((saved["token"], saved["scopes"]), (new_token, CAL_SCOPES))
        self.assert_redacted(new_token)

    def sign_in_fails(self, error: BaseException, alias: str = "work") -> AccountError:
        self.write_client_secret()
        self.flow.results = [error]
        account = self.account(alias)
        with self.assertRaises(AccountError) as ctx:
            account.sign_in()
        self.assertFalse(account.token_path.exists())
        self.assertEqual(account.problem(), (ctx.exception.problem, str(ctx.exception)))
        return ctx.exception

    def test_school_blocks_the_app(self) -> None:
        for error in (CustomOAuth2Error(error="admin_policy_enforced", description="blocked"),
                      CustomOAuth2Error(error="access_denied", description="org_internal: only members")):
            with self.subTest(error=error.description):
                exc = self.sign_in_fails(error)
                self.assertEqual(exc.problem, PROBLEM_BLOCKED)
                self.assertIn("work account's administrator does not allow this app", str(exc))

    def test_access_denied(self) -> None:
        exc = self.sign_in_fails(AccessDeniedError(description="The user denied access"))
        self.assertEqual(exc.problem, PROBLEM_DENIED)
        self.assertEqual(str(exc), "Google sign-in for the work account was cancelled or access was denied")

    def test_timeout_says_a_block_may_be_the_reason(self) -> None:
        exc = self.sign_in_fails(WSGITimeoutError("Timed out waiting for response from authorization server"))
        self.assertEqual(exc.problem, PROBLEM_TIMEOUT)
        self.assertIn("work account was not finished in time", str(exc))
        self.assertIn("Access blocked", str(exc))

    def test_other_oauth_errors_and_failures(self) -> None:
        exc = self.sign_in_fails(CustomOAuth2Error(error="invalid_grant", description="code reused"))
        self.assertEqual((exc.problem, str(exc)), (PROBLEM_FAILED, "Google sign-in failed (invalid_grant)"))
        exc = self.sign_in_fails(RuntimeError(f"boom {self.access_token}"))
        self.assertEqual(str(exc), "Google sign-in failed (RuntimeError)")
        self.assertIsNone(exc.__cause__)




# --------------------------------------------------------------------------
# Which Google account an alias is (accounts.json)
# --------------------------------------------------------------------------

GMAIL = "https://www.googleapis.com/auth/gmail.send"
OPENID = "openid"
EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
ALL_SCOPES = [OPENID, EMAIL_SCOPE, EVENTS, SETTINGS, GMAIL]
SUB_A = "100000000000000000001"
SUB_B = "100000000000000000002"


def make_id_token(sub: str = SUB_A, email: str = "ana@example.edu", *, aud: Any = CLIENT_ID,
                  iss: str = "https://accounts.google.com", verified: Any = True, **extra: Any) -> str:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    claims = {"iss": iss, "aud": aud, "sub": sub, "email": email, "email_verified": verified, **extra}
    return f"{part({'alg': 'RS256'})}.{part(claims)}.fake-signature-{uuid.uuid4().hex}"


class IdentityTests(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_client_secret()
        self.bindings = AccountBindings(self.data_dir / "accounts.json")

    def bound_account(self, alias: str = "work", **kwargs: Any) -> GoogleAccount:
        kwargs.setdefault("features", ("calendar", "gmail_send"))
        return self.account(alias, bindings=self.bindings, **kwargs)

    def creds(self, *, sub: str = SUB_A, email: str = "ana@example.edu", granted: list[str] | None = None,
              id_token: Any = "make") -> FakeCreds:
        creds = FakeCreds(uuid.uuid4().hex, self.client_secret, granted=granted or ALL_SCOPES,
                          requested=ALL_SCOPES)
        creds.id_token = make_id_token(sub, email) if id_token == "make" else id_token
        return creds

    def saved(self, account: GoogleAccount) -> dict[str, Any]:
        return json.loads(account.token_path.read_text(encoding="utf-8"))

    def test_identity_scopes_are_asked_for_with_every_feature(self) -> None:
        self.assertEqual(self.bound_account().requested_scopes(), ALL_SCOPES)
        self.assertEqual(self.bound_account(features=("gmail_send",)).requested_scopes(), [OPENID, EMAIL_SCOPE, GMAIL])
        self.assertEqual(self.account("work").requested_scopes(), CAL_SCOPES)   # no bindings: as before
        legacy = GoogleAccount("", client_secret_path=self.secret_path, token_path=self.root / "t.json",
                               bindings=self.bindings)
        self.assertEqual(legacy.requested_scopes(), CAL_SCOPES)   # the one-account setup never binds
        self.flow.results = [self.creds()]
        work = self.bound_account()
        work.sign_in()
        self.assertEqual(self.flow_args[-1][1], ALL_SCOPES)   # one full consent for every feature

    def test_the_first_sign_in_binds_the_alias_and_marks_the_token(self) -> None:
        self.flow.results = [self.creds()]
        work = self.bound_account()
        self.assertEqual((work.bound_email(), work.identity_confirmed()), ("", False))
        with self.assertLogs(AUTH_LOGGER, level="INFO") as logs:
            work.sign_in()
        self.assertEqual(work.binding().email, "ana@example.edu")
        self.assertEqual(work.binding().sub, SUB_A)
        self.assertEqual((work.bound_email(), work.token_sub(), work.identity_confirmed()),
                         ("ana@example.edu", SUB_A, True))
        self.assertEqual(self.saved(work)[google_auth.TOKEN_SUB_KEY], SUB_A)
        self.assertNotIn("id_token", self.saved(work))
        on_disk = json.loads((self.data_dir / "accounts.json").read_text(encoding="utf-8"))
        self.assertEqual(set(on_disk), {"work"})
        self.assertEqual((on_disk["work"]["email"], on_disk["work"]["sub"]), ("ana@example.edu", SUB_A))
        self.assertEqual(work.granted_features(), frozenset({"calendar", "gmail_send"}))
        text = "\n".join(logs.output)
        self.assertIn("Google (work): bound to the account it signed in with", text)
        self.assertNotIn("ana@example.edu", text)
        self.assertNotIn(SUB_A, text)
        # A fresh object (the next start) reads the same from the files.
        again = self.bound_account()
        self.assertEqual((again.bound_email(), again.identity_confirmed()), ("ana@example.edu", True))

    def test_the_same_account_again_keeps_the_binding(self) -> None:
        self.flow.results = [self.creds()]
        work = self.bound_account()
        work.sign_in()
        self.flow.results = [self.creds(email="ana.example@example.edu")]   # same account, new address
        work.sign_in()
        self.assertEqual((work.binding().sub, work.bound_email(), work.identity_confirmed()),
                         (SUB_A, "ana.example@example.edu", True))

    def test_another_google_account_is_refused_and_the_old_sign_in_kept(self) -> None:
        self.flow.results = [self.creds()]
        work = self.bound_account()
        work.sign_in()
        self.assertTrue(work.confirm_binding("ana@example.edu"))   # only a binding you confirmed refuses
        before = work.token_path.read_bytes()
        intruder = self.creds(sub=SUB_B, email="eve@example.com")
        self.flow.results = [intruder]
        with self.assertLogs(AUTH_LOGGER, level="WARNING") as logs, self.assertRaises(AccountAuthError) as ctx:
            work.sign_in()
        self.assertEqual(ctx.exception.problem, google_auth.PROBLEM_IDENTITY)
        self.assertIn("not the Google account set up as the work account", str(ctx.exception))
        self.assertEqual(work.token_path.read_bytes(), before)   # the new tokens were dropped
        self.assertEqual((work.binding().sub, work.bound_email()), (SUB_A, "ana@example.edu"))
        self.assertEqual(work.problem()[0], google_auth.PROBLEM_IDENTITY)
        self.assertTrue(work.identity_confirmed())
        self.assertNotEqual(work.credentials(interactive=False).token, intruder.token)
        for text in ("\n".join(logs.output), str(ctx.exception)):
            for private in ("eve@example.com", "ana@example.edu", SUB_A, SUB_B, intruder.token):
                self.assertNotIn(private, text)

    def test_an_account_bound_to_another_alias_is_refused(self) -> None:
        self.flow.results = [self.creds()]
        personal = self.bound_account("personal")
        personal.sign_in()
        self.assertTrue(personal.confirm_binding("ana@example.edu"))
        self.flow.results = [self.creds()]   # the same Google account again, now for "work"
        work = self.bound_account("work")
        with self.assertLogs(AUTH_LOGGER, level="WARNING"), self.assertRaises(AccountAuthError) as ctx:
            work.sign_in()
        self.assertEqual(ctx.exception.problem, google_auth.PROBLEM_IDENTITY)
        self.assertIn("already set up as the personal account", str(ctx.exception))
        self.assertFalse(work.token_path.exists())
        self.assertIsNone(work.binding())
        self.bindings.bind("lab-2", "lab@example.edu", SUB_B)
        self.bindings.confirm("lab-2", "lab@example.edu")
        self.flow.results = [self.creds(sub=SUB_B, email="lab@example.edu")]
        with self.assertLogs(AUTH_LOGGER, level="WARNING"), self.assertRaises(AccountAuthError) as ctx:
            work.sign_in()
        self.assertIn("already set up as another account", str(ctx.exception))   # other aliases unnamed
        self.assertNotIn("lab-2", str(ctx.exception))

    def test_a_sign_in_that_does_not_name_the_account(self) -> None:
        self.flow.results = [self.creds(id_token=None)]
        work = self.bound_account()
        work.sign_in()   # not bound yet: kept; the calendar works, sending waits for a binding
        self.assertTrue(work.is_signed_in("calendar"))
        self.assertEqual((work.binding(), work.token_sub(), work.identity_confirmed()), (None, "", False))
        self.assertNotIn(google_auth.TOKEN_SUB_KEY, self.saved(work))
        self.flow.results = [self.creds()]
        work.sign_in()
        self.assertTrue(work.identity_confirmed())
        before = work.token_path.read_bytes()
        self.flow.results = [self.creds(id_token=None)]
        with self.assertLogs(AUTH_LOGGER, level="WARNING"), self.assertRaises(AccountAuthError) as ctx:
            work.sign_in()   # bound: an unnamed sign-in could be anyone, so it is not kept
        self.assertEqual(ctx.exception.problem, google_auth.PROBLEM_IDENTITY)
        self.assertEqual(work.token_path.read_bytes(), before)

    def test_id_tokens_that_do_not_count(self) -> None:
        read = google_auth.identity_from_id_token
        self.assertEqual(read(make_id_token(), CLIENT_ID), ("ana@example.edu", SUB_A))
        self.assertEqual(read(make_id_token(email="Ana@Example.EDU", aud=["x", CLIENT_ID], iss="accounts.google.com",
                                            verified="true"), CLIENT_ID), ("Ana@example.edu", SUB_A))
        bad = (make_id_token(aud="someone-else.apps.googleusercontent.com"), make_id_token(iss="https://evil.example"),
               make_id_token(verified=False), make_id_token(verified="false"), make_id_token(sub=""),
               make_id_token(sub="1 2"), make_id_token(sub=5), make_id_token(email="not an address"),
               make_id_token(email="Eve <eve@example.com>"), make_id_token(email="a@example.edu\nBcc: b@example.com"),
               make_id_token(email=None), "a.b", "a.!!!.c", "a." + base64.urlsafe_b64encode(b"[1]").decode() + ".c",
               None, 5, "")
        for token in bad:
            with self.subTest(token=str(token)[-40:]):
                self.assertIsNone(read(token, CLIENT_ID))
        self.assertIsNone(read(make_id_token(), ""))

    def test_a_refresh_keeps_the_account_id(self) -> None:
        self.flow.results = [self.creds()]
        work = self.bound_account()
        work.sign_in()
        info = self.saved(work)
        info["expiry"] = f"{_utc_naive(timedelta(hours=-2)).isoformat()}Z"
        work.token_path.write_text(json.dumps(info), encoding="utf-8")
        new_token = f"ya29.fake-refreshed-{uuid.uuid4().hex}"

        def refresh(creds: Any) -> None:
            creds.token = new_token
            creds.expiry = _utc_naive(timedelta(hours=1))

        fresh = self.bound_account(refresh=refresh)
        self.assertEqual(fresh.credentials(interactive=False, need="gmail_send").token, new_token)
        saved = self.saved(fresh)
        self.assertEqual((saved["token"], saved[google_auth.TOKEN_SUB_KEY]), (new_token, SUB_A))
        self.assertEqual(saved[google_auth.TOKEN_ASKED_KEY], ALL_SCOPES)   # what the sign-in asked for, too
        self.assertTrue(fresh.identity_confirmed())

    def test_the_migrated_token_works_for_the_calendar_only(self) -> None:
        self.write_token(self.data_dir / LEGACY_TOKEN, scopes=None)   # an older version's file: no scopes
        migrate_legacy_token(self.data_dir)
        personal = self.bound_account("personal")
        self.assertEqual(personal.granted_features(), frozenset({"calendar"}))   # not gmail_send
        self.assertEqual(personal.refused_features(), frozenset())   # never asked for: not refused
        self.assertTrue(personal.is_signed_in("calendar"))
        self.assertFalse(personal.is_signed_in("gmail_send"))
        self.assertEqual((personal.bound_email(), personal.identity_confirmed()), ("", False))
        creds = personal.credentials(interactive=False, need="calendar")
        self.assertEqual(list(creds.scopes), CAL_SCOPES)   # refreshes ask only for what was granted
        with self.assertRaises(AccountAuthError) as ctx:
            personal.credentials(interactive=False, need="gmail_send")
        self.assertEqual(ctx.exception.problem, PROBLEM_SCOPE)
        self.assertIn("sending email for the personal account", str(ctx.exception))
        self.flow.results = [self.creds()]
        personal.credentials(interactive=True, need="gmail_send")   # one new sign-in, which binds
        self.assertEqual(self.flow_args[-1][1], ALL_SCOPES)
        self.assertTrue(personal.identity_confirmed())

    def test_unticking_send_keeps_the_calendar_and_still_binds(self) -> None:
        self.flow.results = [self.creds(granted=[OPENID, EMAIL_SCOPE, EVENTS, SETTINGS])]
        work = self.bound_account()
        with self.assertLogs(AUTH_LOGGER, level="WARNING") as logs:
            work.sign_in()
        self.assertIn("without every permission (gmail_send missing)", "\n".join(logs.output))
        self.assertEqual(work.granted_features(), frozenset({"calendar"}))
        self.assertEqual(self.saved(work)["scopes"], [OPENID, EMAIL_SCOPE, EVENTS, SETTINGS])
        problem, message = work.problem()
        self.assertEqual(problem, PROBLEM_SCOPE)
        self.assertIn("Google did not allow sending email for the work account", message)
        self.assertTrue(work.identity_confirmed())   # which account it is is known all the same
        self.assertTrue(work.is_signed_in("calendar"))
        # The token file says sending was asked for and not granted (a refusal), also next run.
        self.assertEqual(self.saved(work)[google_auth.TOKEN_ASKED_KEY], ALL_SCOPES)
        self.assertEqual(self.bound_account().refused_features(), frozenset({"gmail_send"}))

    def test_the_id_token_is_registered_for_redaction(self) -> None:
        creds = self.creds()
        self.flow.results = [creds]
        self.bound_account().sign_in()
        self.assert_redacted(creds.id_token)

    def test_a_first_binding_waits_for_your_confirmation(self) -> None:
        """Google's chooser makes it easy to pick the wrong account: a new binding is asked about once
        ("Signed in as X for 'work' - is that right?") and kept confirmed in accounts.json."""
        self.flow.results = [self.creds()]
        work = self.bound_account()
        self.assertEqual((work.pending_confirmation(), work.binding_confirmed()), ("", False))   # not bound
        work.sign_in()
        self.assertEqual((work.pending_confirmation(), work.binding_confirmed(), work.identity_confirmed()),
                         ("ana@example.edu", False, True))
        self.assertFalse(work.confirm_binding("eve@example.com"))   # not the address the question showed
        self.assertEqual(work.pending_confirmation(), "ana@example.edu")
        with self.assertLogs(AUTH_LOGGER, level="INFO") as logs:
            self.assertTrue(work.confirm_binding(" Ana@Example.edu "))
        self.assertIn("Google (work): you confirmed the account it is bound to", "\n".join(logs.output))
        self.assertNotIn("ana@example.edu", "\n".join(logs.output))
        self.assertEqual((work.pending_confirmation(), work.binding_confirmed()), ("", True))
        on_disk = json.loads((self.data_dir / "accounts.json").read_text(encoding="utf-8"))
        self.assertIs(on_disk["work"]["confirmed"], True)
        self.assertTrue(on_disk["work"]["confirmed_at"])
        self.assertEqual(self.bound_account().pending_confirmation(), "")   # the next start
        self.flow.results = [self.creds()]
        work.sign_in()   # the same account again: still confirmed
        self.assertEqual(work.pending_confirmation(), "")
        self.flow.results = [self.creds(email="ana.lima@example.edu")]   # same account, a new address
        work.sign_in()
        self.assertEqual(work.pending_confirmation(), "ana.lima@example.edu")   # asked again
        with self.assertLogs(AUTH_LOGGER, level="INFO"):
            work.disconnect()
        self.assertEqual((work.pending_confirmation(), work.binding_confirmed()), ("", False))
        self.flow.results = [self.creds(sub=SUB_B, email="ben@example.edu")]
        work.sign_in()   # "No, use another account": the next sign-in binds, unconfirmed again
        self.assertEqual(work.pending_confirmation(), "ben@example.edu")
        unbound = GoogleAccount("", client_secret_path=self.secret_path, token_path=self.root / "t.json")
        self.assertEqual((unbound.pending_confirmation(), unbound.confirm_binding("ana@example.edu")), ("", False))

    def test_a_binding_of_the_older_version_is_asked_about_once(self) -> None:
        self.bindings.path.parent.mkdir(parents=True, exist_ok=True)
        self.bindings.path.write_text(json.dumps({"personal": {"email": "ana@example.edu", "sub": SUB_A,
                                                               "bound_at": "2026-10-07T19:00:29-07:00"}}),
                                      encoding="utf-8")
        personal = self.bound_account("personal")
        self.assertEqual(personal.pending_confirmation(), "ana@example.edu")
        self.assertTrue(personal.confirm_binding("ana@example.edu"))
        on_disk = json.loads(self.bindings.path.read_text(encoding="utf-8"))["personal"]
        self.assertEqual((on_disk["bound_at"], on_disk["confirmed"]), ("2026-10-07T19:00:29-07:00", True))

    def test_an_unconfirmed_binding_never_locks_the_alias(self) -> None:
        """The live case: the wrong account was picked, the question closed unanswered, then the
        sign-in was lost (expired, revoked). Signing in with the RIGHT account must not be refused:
        it replaces the binding you never confirmed, unconfirmed, and is asked about."""
        self.flow.results = [self.creds(sub=SUB_B, email="eve@example.com")]   # the wrong account
        personal = self.bound_account("personal")
        personal.sign_in()
        self.assertEqual(personal.pending_confirmation(), "eve@example.com")
        with self.assertLogs(AUTH_LOGGER, level="WARNING"):
            personal.forget("Google rejected it", problem=PROBLEM_EXPIRED)
        self.flow.results = [self.creds()]   # now the right one
        with self.assertLogs(AUTH_LOGGER, level="INFO") as logs:
            personal.sign_in()
        self.assertIn("Google (personal): bound to another Google account (you had not confirmed the earlier one)",
                      "\n".join(logs.output))
        for private in ("eve@example.com", "ana@example.edu", SUB_A, SUB_B):
            self.assertNotIn(private, "\n".join(logs.output))
        binding = personal.binding()
        self.assertEqual((binding.sub, binding.email, binding.confirmed), (SUB_A, "ana@example.edu", False))
        self.assertEqual(personal.pending_confirmation(), "ana@example.edu")   # asked about, not trusted
        self.assertEqual(personal.change_problem()[0], PROBLEM_CONFIRM)
        self.assertTrue(personal.identity_confirmed())
        # A binding you DID confirm still refuses another account (and keeps its own sign-in).
        self.assertTrue(personal.confirm_binding("ana@example.edu"))
        self.flow.results = [self.creds(sub=SUB_B, email="eve@example.com")]
        with self.assertLogs(AUTH_LOGGER, level="WARNING"), self.assertRaises(AccountAuthError) as ctx:
            personal.sign_in()
        self.assertEqual(ctx.exception.problem, PROBLEM_IDENTITY)
        self.assertEqual((personal.binding().sub, personal.binding().confirmed), (SUB_A, True))
        with self.assertRaises(ValueError):   # the bindings never replace a confirmed one either
            self.bindings.bind("personal", "eve@example.com", SUB_B)

    def test_swapped_bindings_can_be_fixed_in_the_app(self) -> None:
        """Both names bound to each other's account, neither confirmed (the live incident's likely
        state). "No, use another account" on personal, then picking the account work holds, moves it
        to personal (one Google account is one name); work then knows no account and signs in again."""
        self.flow.results = [self.creds(sub=SUB_B, email="ben@work.example.edu")]   # personal picked WORK
        personal = self.bound_account("personal")
        personal.sign_in()
        self.flow.results = [self.creds(email="ana@example.com")]   # work picked PERSONAL
        work = self.bound_account("work")
        work.sign_in()
        with self.assertLogs(AUTH_LOGGER, level="INFO"):
            personal.disconnect()   # "No, use another account"
        self.flow.results = [self.creds(email="ana@example.com")]   # the right one, held by work
        with self.assertLogs(AUTH_LOGGER, level="INFO") as logs:
            personal.sign_in()
        text = "\n".join(logs.output)
        self.assertIn("Google (work): no longer bound to a Google account", text)
        self.assertNotIn("ana@example.com", text)
        self.assertEqual((personal.binding().sub, personal.pending_confirmation()), (SUB_A, "ana@example.com"))
        self.assertIsNone(work.binding())
        # work's saved sign-in still names that account: nothing is sent or changed for it until
        # it signs in again (and is asked about).
        self.assertEqual(work.change_problem()[0], PROBLEM_IDENTITY)
        self.assertFalse(work.identity_confirmed())
        self.flow.results = [self.creds(sub=SUB_B, email="ben@work.example.edu")]
        work.sign_in()   # the account personal had (released by the disconnect) is free now
        self.assertEqual(work.pending_confirmation(), "ben@work.example.edu")
        # An account you confirmed for another name is never taken.
        self.assertTrue(work.confirm_binding("ben@work.example.edu"))
        self.flow.results = [self.creds(sub=SUB_B, email="ben@work.example.edu")]
        with self.assertLogs(AUTH_LOGGER, level="WARNING"), self.assertRaises(AccountAuthError) as ctx:
            personal.sign_in()
        self.assertIn("already set up as the work account", str(ctx.exception))
        self.assertEqual(personal.binding().sub, SUB_A)
        with self.assertRaises(ValueError):
            self.bindings.bind("lab", "ben@work.example.edu", SUB_B, release="work")

    def test_changes_need_a_confirmed_binding_of_the_saved_sign_in(self) -> None:
        """change_problem fails closed: only the token's own, confirmed account may change things."""
        work = self.bound_account()
        self.assertEqual(work.change_problem()[0], PROBLEM_SIGNED_OUT)   # no saved sign-in
        self.flow.results = [self.creds()]
        work.sign_in()
        self.assertEqual(work.change_problem()[0], PROBLEM_CONFIRM)
        self.assertTrue(work.needs_confirmation())
        work.confirm_binding("ana@example.edu")
        self.assertEqual(work.change_problem(), ("", ""))
        self.assertFalse(work.needs_confirmation())
        # accounts.json deleted (or one entry): the token still names the account -> unknown.
        self.bindings.path.unlink()
        problem, message = work.change_problem()
        self.assertEqual(problem, PROBLEM_IDENTITY)
        self.assertIn("sign in again to confirm it", message)
        self.assertNotIn("@", message)
        self.assertTrue(work.needs_confirmation())
        # accounts.json unreadable (a slip while editing it by hand): unknown too, never "empty".
        self.bindings.path.write_text('{"work": {"email": "ana@example.edu", "sub": "%s",}' % SUB_A,
                                      encoding="utf-8")
        with self.assertLogs(AUTH_LOGGER, level="WARNING"):
            self.assertEqual(work.change_problem()[0], PROBLEM_IDENTITY)
        # Bound, confirmed, but the saved sign-in is another account's (or names none).
        self.bindings.path.unlink()
        self.bindings.bind("work", "ben@example.edu", SUB_B)
        self.bindings.confirm("work", "ben@example.edu")
        self.assertEqual(work.change_problem()[0], PROBLEM_IDENTITY)
        info = self.saved(work)
        info.pop(google_auth.TOKEN_SUB_KEY)
        work.token_path.write_text(json.dumps(info), encoding="utf-8")
        self.assertEqual(work.change_problem()[0], PROBLEM_IDENTITY)
        # A token that names no account and no binding (older versions): the calendar as before.
        self.bindings.path.unlink()
        self.assertEqual(work.change_problem(), ("", ""))
        # The one-account setup never binds and is never held back.
        legacy = GoogleAccount("", client_secret_path=self.secret_path, token_path=self.root / "t.json")
        self.assertEqual((legacy.change_problem(), legacy.needs_confirmation()), (("", ""), False))

    def test_disconnect_keeps_the_binding_when_the_sign_in_cannot_be_deleted(self) -> None:
        """"No, use another account" while the token file is locked: nothing is unbound (a token left
        without its binding would be an account nobody confirmed) and the caller is told."""
        self.flow.results = [self.creds(sub=SUB_B, email="eve@example.com")]
        work = self.bound_account()
        work.sign_in()
        real_unlink = Path.unlink

        def locked(path: Path, *args: Any, **kwargs: Any) -> None:
            if path == work.token_path:
                raise PermissionError(13, "The process cannot access the file")
            real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", locked), self.assertLogs(AUTH_LOGGER, level="WARNING") as logs, \
                self.assertRaises(AccountError) as ctx:
            work.disconnect()
        self.assertIn("Could not delete the work account's saved Google sign-in", str(ctx.exception))
        self.assertIn("could not delete the saved sign-in", "\n".join(logs.output))
        self.assertTrue(work.token_path.exists())
        self.assertEqual((work.binding().sub, work.pending_confirmation()), (SUB_B, "eve@example.com"))
        self.assertEqual(work.change_problem()[0], PROBLEM_CONFIRM)   # still nothing sent or changed
        with self.assertLogs(AUTH_LOGGER, level="INFO"):
            work.disconnect()   # once the file is free it works
        self.assertFalse(work.token_path.exists())
        self.assertIsNone(work.binding())

    def test_disconnect_forgets_the_sign_in_and_the_account(self) -> None:
        self.flow.results = [self.creds()]
        work = self.bound_account()
        work.sign_in()
        with self.assertLogs(AUTH_LOGGER, level="INFO"):
            work.disconnect()
        self.assertFalse(work.token_path.exists())
        self.assertIsNone(work.binding())
        self.flow.results = [self.creds(sub=SUB_B, email="ben@example.edu")]
        work.sign_in()   # now any account may be picked again
        self.assertEqual(work.bound_email(), "ben@example.edu")


class AccountBindingsTests(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.data_dir / "accounts.json"
        self.bindings = AccountBindings(self.path)

    def test_bind_get_alias_of_unbind(self) -> None:
        self.assertIsNone(self.bindings.get("work"))
        with self.assertLogs(AUTH_LOGGER, level="INFO") as logs:
            self.assertTrue(self.bindings.bind("work", "ana@example.edu", SUB_A))
            self.assertTrue(self.bindings.bind("lab-2", "lab@example.edu", SUB_B))
            self.assertTrue(self.bindings.unbind("lab-2"))
        self.assertFalse(self.bindings.unbind("lab-2"))
        self.assertEqual((self.bindings.get("work").email, self.bindings.alias_of(SUB_A)), ("ana@example.edu", "work"))
        self.assertEqual(self.bindings.alias_of(SUB_B), "")
        text = "\n".join(logs.output)
        for private in ("ana@example.edu", "lab@example.edu", SUB_A, SUB_B, "lab-2"):
            self.assertNotIn(private, text)
        self.assertIn("Google (other)", text)
        self.assertEqual(self.path.read_bytes().count(b"\r"), 0)
        for bad in (("Work", "a@example.edu", SUB_A), ("work", "", SUB_A), ("work", "a@example.edu", "")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.bindings.bind(*bad)

    def test_unreadable_or_malformed_files(self) -> None:
        self.path.parent.mkdir(parents=True)
        for text in ("{broken", "[]"):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertLogs(AUTH_LOGGER, level="WARNING") as logs:
                    self.assertIsNone(self.bindings.get("work"))
                self.assertNotIn(text, "\n".join(logs.output))
        self.path.write_text(json.dumps({
            "work": {"email": "ana@example.edu", "sub": SUB_A}, "Bad Alias": {"email": "b@example.edu", "sub": SUB_B},
            "personal": {"email": "Eve <eve@example.com>", "sub": SUB_B}, "lab": {"email": "x@example.edu", "sub": 5},
            "home": "nope"}), encoding="utf-8")
        with self.assertLogs(AUTH_LOGGER, level="WARNING") as logs:
            self.assertEqual(self.bindings.get("work"), google_auth.Binding("ana@example.edu", SUB_A, ""))
        self.assertIn("Ignored 4 malformed account binding(s)", "\n".join(logs.output))
        self.assertNotIn("eve@example.com", "\n".join(logs.output))

    def test_confirm(self) -> None:
        with self.assertLogs(AUTH_LOGGER, level="INFO"):
            self.bindings.bind("work", "ana@example.edu", SUB_A)
        self.assertFalse(self.bindings.get("work").confirmed)
        self.assertFalse(self.bindings.confirm("personal", "ana@example.edu"))   # not bound
        self.assertFalse(self.bindings.confirm("work", "eve@example.com"))
        with self.assertLogs(AUTH_LOGGER, level="INFO"):
            self.assertTrue(self.bindings.confirm("work", "ANA@example.edu"))
        self.assertTrue(self.bindings.confirm("work", "ana@example.edu"))   # again: nothing changes
        binding = self.bindings.get("work")
        self.assertEqual((binding.confirmed, binding.email, binding.sub), (True, "ana@example.edu", SUB_A))
        self.assertTrue(binding.confirmed_at)
        self.assertEqual(self.path.read_bytes().count(b"\r"), 0)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["work"]["confirmed"] = "true"   # only a JSON true counts
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(AccountBindings(self.path).get("work").confirmed)
        self.assertFalse(self.bindings.confirm("work", 5))   # type: ignore[arg-type]

    def test_a_failed_write_keeps_the_binding_for_this_run(self) -> None:
        blocked = AccountBindings(self.data_dir / "accounts.json" / "not-a-folder" / "accounts.json")
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{}", encoding="utf-8")
        with self.assertLogs(AUTH_LOGGER, level="WARNING"):
            self.assertFalse(blocked.bind("work", "ana@example.edu", SUB_A))
        self.assertEqual(blocked.get("work").sub, SUB_A)
        self.assertEqual(blocked.alias_of(SUB_A), "work")


# --------------------------------------------------------------------------
# The HTTP client: a change is sent at most once
# --------------------------------------------------------------------------

class FakeResponse(dict):
    def __init__(self) -> None:
        super().__init__({"status": "200", "content-type": "application/json"})

    def read(self) -> bytes:
        return b"{}"


class FakeConnection:
    """An httplib2 connection stand-in: answers come from ``answers`` (exceptions are raised)."""

    host = "www.googleapis.com"

    def __init__(self, answers: list[Any]) -> None:
        self.sock: Any = object()
        self.answers = list(answers)
        self.requests: list[str] = []
        self.closed = 0

    def connect(self) -> None:
        self.sock = object()

    def close(self) -> None:
        self.closed += 1
        self.sock = None

    def request(self, method: str, uri: str, body: Any = None, headers: Any = None) -> None:
        self.requests.append(method)

    def getresponse(self) -> Any:
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class SingleSendHttpTests(unittest.TestCase):
    def test_a_change_is_never_sent_a_second_time(self) -> None:
        for method in ("PATCH", "DELETE", "POST", "PUT", "patch"):
            with self.subTest(method=method):
                conn = FakeConnection([http.client.BadStatusLine(""), FakeResponse()])
                with self.assertRaises(RequestNotResent):
                    single_send_http(30)._conn_request(conn, "/calendar/v3/x", method, b"{}", {})
                self.assertEqual(conn.requests, [method])
                self.assertGreaterEqual(conn.closed, 1)

    def test_a_read_may_be_sent_again_on_a_stale_connection(self) -> None:
        conn = FakeConnection([http.client.BadStatusLine(""), FakeResponse()])
        response, content = single_send_http(30)._conn_request(conn, "/calendar/v3/x", "GET", None, {})
        self.assertEqual(conn.requests, ["GET", "GET"])
        self.assertEqual((response.status, content), (200, b"{}"))

    def test_a_change_with_an_answer_goes_through_once(self) -> None:
        conn = FakeConnection([FakeResponse()])
        response, content = single_send_http(30)._conn_request(conn, "/calendar/v3/x", "PATCH", b"{}", {})
        self.assertEqual(conn.requests, ["PATCH"])
        self.assertEqual((response.status, content), (200, b"{}"))

    def test_timeout_and_type(self) -> None:
        import httplib2

        client = single_send_http(12)
        self.assertIsInstance(client, httplib2.Http)
        self.assertEqual(client.timeout, 12)
        self.assertIs(type(client), type(single_send_http(30)))
        self.assertFalse(issubclass(RequestNotResent, (OSError, http.client.HTTPException)))


if __name__ == "__main__":
    unittest.main()
