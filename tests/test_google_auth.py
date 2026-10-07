"""Tests for briefing_reader.google_auth: per-account sign-ins, the legacy token, partial grants,
rejected and blocked sign-ins, and the HTTP client that never sends a change twice.

Nothing here touches the network or a browser: the OAuth flow is a fake, token refreshes are
injected, and every socket connection raises NetworkUsed (a BaseException, so no broad handler
in the code under test can hide it). Every token, secret and address is invented and unique
per test; nothing reads the owner's data folder.
"""

from __future__ import annotations

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
    PROBLEM_DENIED,
    PROBLEM_EXPIRED,
    PROBLEM_FAILED,
    PROBLEM_SCOPE,
    PROBLEM_SETUP,
    PROBLEM_SIGNED_OUT,
    PROBLEM_TIMEOUT,
    SIGN_IN_PROMPT,
    AccountAuthError,
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
        self.assertEqual(set(FEATURE_SCOPES), {CALENDAR_FEATURE})   # gmail_send comes with a later version


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
