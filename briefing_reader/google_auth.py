"""Google sign-in per account: one OAuth client, one saved sign-in per account alias.

Jarvis signs in to each Google account it acts for ("personal", "work": the
``[accounts.<alias>]`` tables of config.toml) separately, with the same OAuth
client (README step 8), and keeps one token file per alias:

    token_path(data_dir, alias)       %LOCALAPPDATA%\\briefing-reader\\google_token_<alias>.json
    migrate_legacy_token(data_dir)    the single google_token.json of older versions becomes the
                                      "personal" account's token (moved once, never copied), so
                                      nobody has to sign in again
    GoogleAccount(alias, ...)         one account's saved sign-in: is_signed_in(), granted_features(),
                                      credentials(), sign_in(), forget(), problem()

What an account may do comes from its ``features`` (FEATURE_SCOPES). This
version has one feature, "calendar"; a later one adds "gmail_send". Desktop
apps cannot add a scope to an existing grant, so ``sign_in()`` always asks for
every scope of the account's features (a new feature means one new, full
consent). Google's consent screen lets you untick a box: the sign-in still
succeeds (OAUTHLIB_RELAX_TOKEN_SCOPE is set just for the flow), the token is
saved with the scopes Google actually granted (so a refresh never asks for
more), and a feature whose scopes were not all granted is unavailable:
``granted_features()`` leaves it out and ``credentials(need=...)`` says so.

Sign-in problems are told apart (``AccountError.problem``), so a card can say
what happened: no sign-in yet, the sign-in expired or was revoked
(invalid_grant: the token is deleted and the next sign-in starts fresh),
Google blocked it because the account's administrator does not allow this app
(admin_policy_enforced, org_internal), access was denied or cancelled
(access_denied), a box was unticked, or the browser sign-in timed out (Google's
"Access blocked" page does not come back to the app, so a timeout says that
this may be the reason).

``single_send_http(timeout)`` is the HTTP client for the Google services: it
sends a change (anything but GET / HEAD) at most once. httplib2 on its own
sends a request a second time when the first answer was a broken status line;
for a change that first request may already have been carried out, so the
second send is refused (RequestNotResent: an unknown outcome for the caller,
never a silent duplicate).

Everything here is blocking (the browser sign-in waits up to
``open_timeout_s``), so callers use a worker thread. Nothing here talks to
Google except ``sign_in()`` and a token refresh. Qt-free; the Google libraries
are imported lazily. Tokens and the client secret are registered with
:func:`config.register_secret` as soon as they are read; the log names an
account only as "work", "personal" or "other", never by its address.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .config import ACCOUNT_FEATURES, register_secret

logger = logging.getLogger(__name__)

CALENDAR_FEATURE = "calendar"
FEATURE_SCOPES: dict[str, tuple[str, ...]] = {
    CALENDAR_FEATURE: ("https://www.googleapis.com/auth/calendar.events",
                       "https://www.googleapis.com/auth/calendar.settings.readonly"),
}
# What each feature lets Jarvis do, for messages ("Google did not allow Calendar access ...").
FEATURE_WORDS = {CALENDAR_FEATURE: "Calendar access"}
# Not requested in this version: a later one asks for these together with gmail.send, to learn
# which Google account an alias is.
IDENTITY_SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email")
LEGACY_TOKEN = "google_token.json"
TOKEN_PATTERN = "google_token_{alias}.json"
LEGACY_ALIAS = "personal"          # the account google_token.json of older versions belongs to
SETUP_HINT = "Set up Google sign-in: README step 8"
SIGN_IN_SUCCESS_MESSAGE = "briefing-reader is connected to Google Calendar. You can close this tab."
ALIAS_SUCCESS_MESSAGE = ("briefing-reader is connected to Google Calendar for the {alias} account. "
                         "You can close this tab.")
NOT_SIGNED_IN_MESSAGE = "Not signed in to Google"
# "select_account": Google shows its account chooser, so the right account is picked for each
# alias. "consent": every time, so Google returns a refresh token (and asks for any new scope).
SIGN_IN_PROMPT = "select_account consent"

# AccountError.problem: why an account cannot be used right now ("" = no problem known).
PROBLEM_FAILED = "failed"         # anything else (network, an unexpected answer)
PROBLEM_SETUP = "setup"           # no usable client secret file or Google packages
PROBLEM_SIGNED_OUT = "signedout"  # no saved sign-in (and the call may not open the browser)
PROBLEM_EXPIRED = "expired"       # Google rejected the saved sign-in (invalid_grant): sign in again
PROBLEM_BLOCKED = "blocked"       # the account's administrator does not allow this app
PROBLEM_DENIED = "denied"         # access_denied: cancelled in the browser, or not allowed
PROBLEM_SCOPE = "scope"           # a box was unticked: a feature's permission is missing
PROBLEM_TIMEOUT = "timeout"       # the browser sign-in was not finished in time
PROBLEMS = (PROBLEM_FAILED, PROBLEM_SETUP, PROBLEM_SIGNED_OUT, PROBLEM_EXPIRED, PROBLEM_BLOCKED,
            PROBLEM_DENIED, PROBLEM_SCOPE, PROBLEM_TIMEOUT)

_GOOGLE_MODULES = ("googleapiclient", "google_auth_oauthlib", "google_auth_httplib2",
                   "google.oauth2")
BLOCKED_ERRORS = ("admin_policy_enforced", "org_internal")
_ALIAS_RE = re.compile(r"[a-z][a-z0-9_-]{0,23}")
_LOGGED_ALIASES = frozenset({"personal", "work"})
_RELAX_SCOPE = "OAUTHLIB_RELAX_TOKEN_SCOPE"
_relax_lock = threading.Lock()
_libraries_available: bool | None = None

assert set(FEATURE_SCOPES) == set(ACCOUNT_FEATURES), "config.ACCOUNT_FEATURES must name every feature"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class AccountError(Exception):
    """A sign-in failure whose message is safe to show and log (no tokens, no client secret).

    ``problem`` is one of PROBLEMS; ``status`` an HTTP status when there was one.
    """

    def __init__(self, message: str = "", *, problem: str = PROBLEM_FAILED,
                 status: int | None = None) -> None:
        super().__init__(message)
        self.problem = problem
        self.status = status


class AccountSetupError(AccountError):
    """The OAuth client secret file is missing or invalid, or the Google packages are missing."""

    def __init__(self, message: str = "", *, status: int | None = None) -> None:
        super().__init__(message, problem=PROBLEM_SETUP, status=status)


class AccountAuthError(AccountError):
    """The sign-in was refused, cancelled, blocked, timed out, revoked or lacks a permission."""


class AccountNotSignedIn(AccountAuthError):
    """No usable saved sign-in, and the call may not open the browser to get one."""

    def __init__(self, message: str = NOT_SIGNED_IN_MESSAGE) -> None:
        super().__init__(message, problem=PROBLEM_SIGNED_OUT)


class RequestNotResent(Exception):
    """httplib2 was about to send a change a second time (the answer to the first one was
    lost); refused, because the first one may already have been carried out."""


# --------------------------------------------------------------------------
# Paths and aliases
# --------------------------------------------------------------------------

def valid_alias(alias: str) -> bool:
    """"work", "personal", "lab-2": lowercase letters, digits, - or _ (24 at most)."""
    return isinstance(alias, str) and bool(_ALIAS_RE.fullmatch(alias))


def logged_alias(alias: str) -> str:
    """How an alias is written to the log: "work" / "personal" by name, any other one as "other"."""
    if not alias:
        return "-"
    return alias if alias in _LOGGED_ALIASES else "other"


def scrub_alias(text: str, alias: str) -> str:
    """``text`` (a message shown on a card) as it may be logged: an alias other than "work" /
    "personal" is written as "other", like logged_alias()."""
    logged = logged_alias(alias)
    if not alias or not text or logged == alias:
        return text
    return re.sub(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", logged, text)


def token_path(data_dir: Path, alias: str) -> Path:
    """<data_dir>\\google_token_<alias>.json"""
    if not valid_alias(alias):
        raise ValueError("not an account alias")
    return Path(data_dir) / TOKEN_PATTERN.format(alias=alias)


def account_token_path(data_dir: Path, alias: str) -> Path:
    """Where ``alias``'s sign-in is kept: its own file, except that "personal" keeps using an
    older google_token.json that could not be moved (see migrate_legacy_token)."""
    path = token_path(data_dir, alias)
    legacy = Path(data_dir) / LEGACY_TOKEN
    if alias == LEGACY_ALIAS and not path.exists() and legacy.is_file():
        return legacy
    return path


def migrate_legacy_token(data_dir: Path) -> bool:
    """Move google_token.json to google_token_personal.json once (same folder, never copied).

    Nothing happens when there is no old file or the personal one exists already. When the
    move fails the old file stays where it is and the personal account keeps using it
    (logged without paths). True when a file was moved.
    """
    legacy = Path(data_dir) / LEGACY_TOKEN
    target = token_path(data_dir, LEGACY_ALIAS)
    try:
        if not legacy.is_file() or target.exists():
            return False
        os.replace(legacy, target)
    except OSError as exc:
        logger.warning("Google sign-in: could not move the saved sign-in to the personal account (%s); "
                       "using it where it is", exc.strerror or type(exc).__name__)
        return False
    logger.info("Google sign-in: the saved sign-in is now the personal account's")
    return True


def scopes_for(features: Sequence[str]) -> list[str]:
    """Every scope ``features`` need, in FEATURE_SCOPES order, without repeats."""
    scopes: list[str] = []
    for feature, needed in FEATURE_SCOPES.items():
        if feature in features:
            scopes.extend(scope for scope in needed if scope not in scopes)
    return scopes


def features_granted(scopes: Sequence[str], features: Sequence[str] | None = None) -> frozenset[str]:
    """The features (of ``features``, default all) whose scopes are all in ``scopes``."""
    granted = set(scopes)
    wanted = FEATURE_SCOPES if features is None else [name for name in features if name in FEATURE_SCOPES]
    return frozenset(name for name in wanted if set(FEATURE_SCOPES[name]) <= granted)


def blocked_code(text: str) -> str:
    """"admin_policy_enforced" / "org_internal" when ``text`` names one of them, else ""."""
    return next((code for code in BLOCKED_ERRORS if code in (text or "")), "")


# --------------------------------------------------------------------------
# GoogleAccount
# --------------------------------------------------------------------------

class GoogleAccount:
    """The saved Google sign-in of one account alias. Blocking: use a worker thread.

    ``is_configured``, ``is_signed_in`` and ``granted_features`` read files only (no lock, no
    network), so the UI thread may call them while a sign-in waits in the browser.
    ``credentials`` and ``sign_in`` are serialised by an internal lock. Everything that acts
    for an alias shares one account object, so there is one token per alias.

    ``alias`` "" is the one-account setup of older versions (gcal.GoogleCalendar's own
    constructor): its log lines and messages say "Google Calendar" as they did. ``log``,
    ``refresh`` and ``libraries_available`` default to this module's logger and functions;
    gcal passes its own, so its tests keep patching them there.
    """

    def __init__(self, alias: str, *, client_secret_path: Path, data_dir: Path | None = None,
                 token_path: Path | None = None, features: Sequence[str] = (CALENDAR_FEATURE,),
                 flow_factory: Callable[[Path, list[str]], Any] | None = None,
                 open_timeout_s: int = 300, setup_hint: str = SETUP_HINT,
                 success_message: str | None = None, log: logging.Logger | None = None,
                 refresh: Callable[[Any], None] | None = None,
                 libraries_available: Callable[[], bool] | None = None) -> None:
        if token_path is None:
            if data_dir is None:
                raise ValueError("GoogleAccount needs data_dir or token_path")
            token_path = account_token_path(Path(data_dir), alias)
        self.alias = alias
        self.token_path = Path(token_path)
        self.client_secret_path = Path(client_secret_path)
        self.features = tuple(name for name in features if name in FEATURE_SCOPES)
        self._flow_factory = flow_factory or _installed_app_flow
        self._open_timeout_s = open_timeout_s
        self._setup_hint = setup_hint
        if success_message is None:
            success_message = (ALIAS_SUCCESS_MESSAGE.format(alias=alias) if alias
                               else SIGN_IN_SUCCESS_MESSAGE)
        self._success_message = success_message
        self._log = log or logger
        self._refresh_hook = refresh or (lambda creds: _refresh_credentials(creds))
        self._libraries_hook = libraries_available or (lambda: google_libraries_available())
        self._lock = threading.RLock()
        self._creds: Any = None
        self._granted: list[str] | None = None   # scopes of self._creds (None while not loaded)
        self.generation = 0          # changes whenever the credentials object changes or refreshes
        self._problem = ""
        self._problem_message = ""
        # How the log names this account: "Google Calendar" for the one-account setup (its log
        # lines are kept), else "Google (work)" ("other" for an alias that is not a known word).
        self.log_name = f"Google ({logged_alias(alias)})" if alias else "Google Calendar"

    # ---- state without the network ------------------------------------------------

    def requested_scopes(self) -> list[str]:
        """What sign_in() asks Google for: every scope of the account's features."""
        return scopes_for(self.features)

    def is_configured(self) -> bool:
        """True when the OAuth client secret file exists."""
        return self.client_secret_path.is_file()

    def is_signed_in(self, feature: str | None = None) -> bool:
        """True when a saved sign-in exists that is valid or can be refreshed (and, with
        ``feature``, has that feature's permissions). No network."""
        if not self._libraries_hook():
            return False
        creds = self._read_token(quiet=True)
        if creds is None or not (creds.valid or creds.refresh_token):
            return False
        return feature is None or feature in self.granted_features()

    def granted_features(self) -> frozenset[str]:
        """The configured features the sign-in has every permission for (none when signed out)."""
        granted = self._granted if self._creds is not None else None
        if granted is None:
            info = _token_info(self.token_path)
            if info is None:
                return frozenset()
            granted = _info_scopes(info, self.requested_scopes())
        return features_granted(granted, self.features)

    def problem(self) -> tuple[str, str]:
        """(PROBLEM_*, message) of the last failed sign-in or rejected token; ("", "") when none."""
        return self._problem, self._problem_message

    def _set_problem(self, problem: str = "", message: str = "") -> None:
        self._problem, self._problem_message = problem, message

    # ---- credentials -----------------------------------------------------------------

    def credentials(self, *, interactive: bool, need: str | None = None) -> Any:
        """Usable credentials: the saved sign-in (refreshed when needed), else a new sign-in.

        Without ``interactive`` a missing sign-in raises AccountNotSignedIn instead of opening
        the browser. ``need`` names the feature the caller is about to use; a sign-in without
        its permission raises PROBLEM_SCOPE (after one new sign-in when ``interactive``).
        """
        with self._lock:
            self._require_setup()
            creds = self._creds
            if creds is None:
                creds = self._read_token()
                if creds is None:
                    return self._sign_in_or_raise(interactive, need)
                self._creds = creds
                self._granted = list(getattr(creds, "scopes", None) or self.requested_scopes())
                self.generation += 1
            if need and need not in self.granted_features():
                if interactive:
                    return self._sign_in_or_raise(True, need)
                raise AccountAuthError(self.missing_message(need), problem=PROBLEM_SCOPE)
            if not creds.valid:
                if creds.refresh_token:
                    self._refresh(creds)
                else:
                    self.forget("it cannot be refreshed", problem=PROBLEM_SIGNED_OUT)
                    return self._sign_in_or_raise(interactive, need)
            return self._creds

    def _sign_in_or_raise(self, interactive: bool, need: str | None) -> Any:
        if not interactive:
            raise AccountNotSignedIn()
        self.sign_in()
        if need and need not in self.granted_features():
            raise AccountAuthError(self.missing_message(need), problem=PROBLEM_SCOPE)
        return self._creds

    def missing_message(self, feature: str) -> str:
        what = FEATURE_WORDS.get(feature, feature)
        who = f" for the {self.alias} account" if self.alias else ""
        return f"Google did not allow {what}{who}; sign in again and tick every box"

    def _refresh(self, creds: Any) -> None:
        try:
            self._refresh_hook(creds)
        except Exception as exc:  # noqa: BLE001 - mapped to a safe message
            raise self._refresh_error(exc) from None
        _register_credentials(creds)
        self._save_token(creds, _creds_info(creds, self._granted or self.requested_scopes()))
        self.generation += 1

    def _refresh_error(self, exc: Exception) -> AccountError:
        """A failed token refresh: rejected (token deleted) or the network."""
        from google.auth.exceptions import RefreshError

        if isinstance(exc, RefreshError):
            if exc.retryable:
                return AccountError("Google could not refresh the sign-in just now; try again")
            return self.rejected(str(exc))
        return AccountError(f"Could not reach Google while refreshing the sign-in ({type(exc).__name__})")

    def rejected(self, detail: str = "") -> AccountAuthError:
        """Google rejected the saved sign-in (invalid_grant: expired or revoked; or blocked by an
        administrator): the token is deleted and the error to raise is returned.

        ``detail`` (Google's error text) is only inspected, never logged or shown.
        """
        code = blocked_code(detail)
        if code:
            message = self.blocked_message(code)
            self.forget("Google blocked it", problem=PROBLEM_BLOCKED, message=message)
            return AccountAuthError(message, problem=PROBLEM_BLOCKED)
        if self.alias:
            message = f"Google sign-in for the {self.alias} account expired or was revoked; sign in again"
        else:
            message = "Google Calendar sign-in expired or was revoked. Approve again to sign in."
        self.forget("Google rejected it", problem=PROBLEM_EXPIRED, message=message)
        return AccountAuthError(message, problem=PROBLEM_EXPIRED)

    def blocked_message(self, code: str) -> str:
        whose = f"the {self.alias} account's" if self.alias else "this account's"
        return f"Google blocked the sign-in: {whose} administrator does not allow this app ({code})"

    # ---- sign-in -----------------------------------------------------------------------

    def sign_in(self) -> None:
        """Open the Google sign-in in the browser and save the token.

        Blocks until the user finishes or ``open_timeout_s`` passes. Raises AccountSetupError
        (no usable client secret), AccountAuthError (cancelled, denied, blocked, timed out) or
        AccountError. A partial grant (a box unticked) still signs in; see granted_features().
        """
        with self._lock:
            self._require_setup()
            self._read_client_config()
            scopes = self.requested_scopes()
            try:
                flow = self._flow_factory(self.client_secret_path, list(scopes))
            except Exception as exc:  # noqa: BLE001 - any failure here is a setup problem
                raise AccountSetupError(
                    f"{self._setup_hint} (could not use {self.client_secret_path.name}: "
                    f"{type(exc).__name__})") from None
            self._log.info("%s: waiting for sign-in in the browser (up to %d s)", self.log_name,
                           self._open_timeout_s)
            try:
                with _relaxed_scope():
                    creds = flow.run_local_server(
                        port=0, open_browser=True, timeout_seconds=self._open_timeout_s,
                        authorization_prompt_message="", success_message=self._success_message,
                        prompt=SIGN_IN_PROMPT)
            except Exception as exc:  # noqa: BLE001 - mapped to a safe message
                error = self._sign_in_error(exc)
                self._set_problem(error.problem, str(error))
                raise error from None
            _register_credentials(creds)
            granted = _granted_scopes(creds, scopes)
            info = _creds_info(creds, granted)
            if granted != list(scopes) and info is not None:
                creds = _credentials_from_info(info) or creds   # refreshes ask only for what was granted
                _register_credentials(creds)
            self._save_token(creds, info)
            self._creds = creds
            self._granted = list(granted)
            self.generation += 1
            missing = [name for name in self.features if name not in features_granted(granted, self.features)]
            if missing:
                self._set_problem(PROBLEM_SCOPE, self.missing_message(missing[0]))
                self._log.warning("%s: signed in without every permission (%s missing)", self.log_name,
                                  ", ".join(missing))
            else:
                self._set_problem()
                self._log.info("%s: signed in", self.log_name)

    def _sign_in_error(self, exc: Exception) -> AccountError:
        from google_auth_oauthlib.flow import WSGITimeoutError
        from oauthlib.oauth2.rfc6749.errors import OAuth2Error

        if isinstance(exc, (WSGITimeoutError, TimeoutError)):
            if self.alias:
                # Google's "Access blocked" page (admin_policy_enforced) never comes back to the
                # app, so a block looks like a sign-in that was not finished.
                return AccountAuthError(
                    f"Google sign-in for the {self.alias} account was not finished in time; if Google "
                    "said \"Access blocked\", the account's administrator does not allow this app",
                    problem=PROBLEM_TIMEOUT)
            return AccountAuthError("Google sign-in timed out", problem=PROBLEM_TIMEOUT)
        if isinstance(exc, OAuth2Error):
            code = exc.error or ""
            blocked = blocked_code(f"{code} {getattr(exc, 'description', '') or ''}")
            if blocked:
                return AccountAuthError(self.blocked_message(blocked), problem=PROBLEM_BLOCKED)
            if code == "access_denied":
                who = f" for the {self.alias} account" if self.alias else ""
                return AccountAuthError(f"Google sign-in{who} was cancelled or access was denied",
                                        problem=PROBLEM_DENIED)
            safe = code if re.fullmatch(r"[a-z_]{1,40}", code) else type(exc).__name__
            return AccountAuthError(f"Google sign-in failed ({safe})")
        if isinstance(exc, Warning) and "scope" in str(exc).casefold():
            # oauthlib raises Warning("Scope has changed ...") when a box was unticked and the
            # relaxed mode was not in effect.
            return AccountAuthError("Google sign-in did not grant every permission; "
                                    "approve again and allow all of them", problem=PROBLEM_SCOPE)
        return AccountError(f"Google sign-in failed ({type(exc).__name__})")

    def forget(self, reason: str, *, problem: str = PROBLEM_EXPIRED, message: str = "") -> None:
        """Delete the saved sign-in (the next use signs in again) and remember why."""
        with self._lock:
            self._creds = None
            self._granted = None
            self.generation += 1
            if problem != PROBLEM_SIGNED_OUT:
                self._set_problem(problem, message)
            try:
                self.token_path.unlink()
            except FileNotFoundError:
                return
            except OSError as exc:
                self._log.warning("%s: could not delete the saved sign-in (%s)", self.log_name,
                                  exc.strerror or type(exc).__name__)
                return
            if self.alias:
                self._log.warning("%s: removed the saved sign-in (%s); the next sign-in starts fresh",
                                  self.log_name, reason)
            else:
                self._log.warning("Google Calendar: removed the saved sign-in (%s); "
                                  "the next Approve signs in again", reason)

    # ---- files ----------------------------------------------------------------------------

    def _require_setup(self) -> None:
        if not self.is_configured():
            raise AccountSetupError(f"{self._setup_hint} ({self.client_secret_path.name} was not found)")
        if not self._libraries_hook():
            raise AccountSetupError(f"{self._setup_hint} (the Google packages are missing: "
                                    "py -3.13 -m pip install -r requirements.txt)")

    def _read_client_config(self) -> dict[str, Any]:
        """Validate the client secret file and register its secret for redaction."""
        name = self.client_secret_path.name
        try:
            data = json.loads(self.client_secret_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raise AccountSetupError(f"{self._setup_hint} ({name} was not found)") from None
        except OSError as exc:
            problem = exc.strerror or type(exc).__name__
            raise AccountSetupError(f"{self._setup_hint} (could not read {name}: {problem})") from None
        except ValueError:
            raise AccountSetupError(f"{self._setup_hint} ({name} is not valid JSON)") from None
        installed = data.get("installed") if isinstance(data, dict) else None
        if not isinstance(installed, dict) or not all(
                isinstance(installed.get(key), str) and installed[key].strip()
                for key in ("client_id", "client_secret")):
            raise AccountSetupError(f"{self._setup_hint} ({name} is not an OAuth client of type Desktop app)")
        register_secret(installed["client_secret"])
        return data

    def _read_token(self, *, quiet: bool = False) -> Any | None:
        """Saved credentials, or None when missing or unusable (logged unless quiet; never raised).

        The credentials ask for the scopes the file says were granted, so a refresh never asks
        for a permission that was unticked.
        """
        log = self._log.debug if quiet else self._log.warning
        try:
            text = self.token_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            log("%s: could not read the saved sign-in (%s)", self.log_name, exc.strerror or type(exc).__name__)
            return None
        from google.oauth2.credentials import Credentials

        try:
            info = json.loads(text)
            if not isinstance(info, dict):
                raise ValueError("not a JSON object")
            creds = Credentials.from_authorized_user_info(info, _info_scopes(info, self.requested_scopes()))
        except (ValueError, TypeError) as exc:
            if self.alias:
                log("%s: the saved sign-in is unusable (%s); the next sign-in starts fresh", self.log_name,
                    type(exc).__name__)
            else:
                log("Google Calendar: the saved sign-in is unusable (%s); the next Approve signs in again",
                    type(exc).__name__)
            return None
        _register_credentials(creds)
        return creds

    def _save_token(self, creds: Any, info: dict[str, Any] | None = None) -> None:
        """Write the token JSON atomically. A failure is logged, not raised."""
        path = self.token_path
        part = path.with_name(path.name + ".part")
        try:
            text = json.dumps(info) if info is not None else creds.to_json()
            path.parent.mkdir(parents=True, exist_ok=True)
            part.write_text(text, encoding="utf-8")
            os.replace(part, path)
        except OSError as exc:
            _unlink_quietly(part)
            self._log.warning("%s: could not save the sign-in (%s); you will be asked to sign in "
                              "again next time", self.log_name, exc.strerror or type(exc).__name__)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _token_info(path: Path) -> dict[str, Any] | None:
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def _info_scopes(info: dict[str, Any], default: Sequence[str]) -> list[str]:
    """The granted scopes a token file names ("scopes": a list or space-separated text);
    ``default`` for files written before scopes were saved."""
    scopes = info.get("scopes")
    if isinstance(scopes, str):
        scopes = scopes.split()
    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        return list(default)
    return [scope for scope in scopes if scope]


def _granted_scopes(creds: Any, requested: Sequence[str]) -> list[str]:
    """The scopes Google granted (the token response's "scope"), requested ones first; the
    requested ones when the credentials do not say."""
    granted = getattr(creds, "granted_scopes", None)
    if isinstance(granted, str):
        granted = granted.split()
    if not isinstance(granted, (list, tuple)) or not granted:
        return list(requested)
    given = {str(scope) for scope in granted}
    ordered = [scope for scope in requested if scope in given]
    return ordered + sorted(given - set(ordered))


def _creds_info(creds: Any, granted: Sequence[str]) -> dict[str, Any] | None:
    """The token file content with "scopes" set to what was granted (None if unreadable)."""
    try:
        info = json.loads(creds.to_json())
    except (AttributeError, TypeError, ValueError):
        return None
    if not isinstance(info, dict):
        return None
    if list(granted) != _info_scopes(info, granted):
        info["scopes"] = list(granted)
    return info


def _credentials_from_info(info: dict[str, Any]) -> Any | None:
    from google.oauth2.credentials import Credentials

    try:
        return Credentials.from_authorized_user_info(info, _info_scopes(info, ()))
    except (ValueError, TypeError):
        return None


class _relaxed_scope:   # noqa: N801 - used as a context manager
    """OAUTHLIB_RELAX_TOKEN_SCOPE=1 for the duration of one sign-in (restored after)."""

    def __enter__(self) -> None:
        _relax_lock.acquire()
        self._before = os.environ.get(_RELAX_SCOPE)
        os.environ[_RELAX_SCOPE] = "1"

    def __exit__(self, *_exc: Any) -> None:
        try:
            if self._before is None:
                os.environ.pop(_RELAX_SCOPE, None)
            else:
                os.environ[_RELAX_SCOPE] = self._before
        finally:
            _relax_lock.release()


# --------------------------------------------------------------------------
# HTTP: a change is sent at most once
# --------------------------------------------------------------------------

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_single_send_class: type | None = None


def single_send_http(timeout: float) -> Any:
    """An httplib2.Http (``timeout`` seconds per socket operation) that sends anything but a
    GET / HEAD at most once per call: where httplib2 would send it again (the first answer
    was a broken status line), RequestNotResent is raised instead. A 401 answer is still
    re-sent once with a refreshed token by google_auth_httplib2 (each of those is its own
    call); Google did not carry out a request it answered with 401."""
    global _single_send_class
    if _single_send_class is None:
        import httplib2

        class SingleSendHttp(httplib2.Http):
            def _conn_request(self, conn: Any, request_uri: Any, method: Any, body: Any,
                              headers: Any) -> Any:
                if str(method).upper() not in _SAFE_METHODS:
                    conn = _OneRequest(conn)
                return super()._conn_request(conn, request_uri, method, body, headers)

        _single_send_class = SingleSendHttp
    return _single_send_class(timeout=timeout)


class _OneRequest:
    """Wraps one httplib2 connection for one call: ``request`` may be called once."""

    def __init__(self, conn: Any) -> None:
        self._one_request_conn = conn
        self._one_request_sent = False

    def request(self, *args: Any, **kwargs: Any) -> Any:
        if self._one_request_sent:
            try:
                self._one_request_conn.close()
            finally:
                raise RequestNotResent("the change was not sent a second time")
        self._one_request_sent = True
        return self._one_request_conn.request(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._one_request_conn, name)


def _installed_app_flow(client_secret_path: Path, scopes: list[str]) -> Any:
    from google_auth_oauthlib.flow import InstalledAppFlow

    # Same as InstalledAppFlow.from_client_secrets_file, but tolerates a UTF-8 BOM.
    with open(client_secret_path, encoding="utf-8-sig") as fh:
        client_config = json.load(fh)
    return InstalledAppFlow.from_client_config(client_config, scopes)


def _refresh_credentials(creds: Any) -> None:
    from google.auth.transport.requests import Request

    creds.refresh(Request())


def google_libraries_available() -> bool:
    """True when the Google client packages from requirements.txt are installed."""
    global _libraries_available
    if _libraries_available is None:
        try:
            _libraries_available = all(importlib.util.find_spec(name) is not None
                                       for name in _GOOGLE_MODULES)
        except (ImportError, ValueError):
            _libraries_available = False
    return _libraries_available


def _register_credentials(creds: Any) -> None:
    for name in ("token", "refresh_token", "client_secret", "id_token"):
        value = getattr(creds, name, None)
        if isinstance(value, str):
            register_secret(value)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
