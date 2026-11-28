"""Microsoft (Outlook) sign-in per account: read only, first stage.

An ``[accounts.<alias>]`` table with ``backend = "microsoft"`` is an Outlook / Microsoft account (a work or school
account, or a personal outlook.com one). Jarvis signs in to it with the owner's own free app registration (its client
id is ``[microsoft] client_id`` in config.toml; README "Microsoft / Outlook accounts") and keeps one token file per
alias, beside the Google ones:

    token_path(data_dir, alias)       %LOCALAPPDATA%\\briefing-reader\\ms_token_<alias>.json
    MicrosoftAccount(alias, ...)      one account's saved sign-in: is_configured(), is_signed_in(), sign_in(),
                                      access_token(), forget(), disconnect(), problem()
    make_verifier() / challenge(v)    PKCE (RFC 7636, S256)
    authorize_url(...)                the sign-in page the browser opens

The sign-in is OAuth 2.0's authorization code flow with PKCE for a public client (no client secret) on the Microsoft
identity platform's v2.0 endpoints (login.microsoftonline.com/<tenant>; ``[microsoft] tenant``, "common" by default),
with a loopback redirect: Jarvis listens on http://localhost:<a free port>/ for the one answer, the browser shows the
sign-in, and the code that comes back is traded for the tokens. It asks only for MICROSOFT_SCOPES (offline_access for
the refresh token, User.Read, Mail.Read, Calendars.Read): nothing here can send, change or delete anything. The token
is refreshed with its refresh token a minute before it runs out (Microsoft may hand back a new refresh token; it is
kept). A refresh Microsoft rejects (invalid_grant: expired or revoked) deletes the token file, so the next Connect
starts fresh; disconnect() deletes it too.

Everything is blocking (the browser sign-in waits up to ``open_timeout_s``), so callers use a worker thread. Only
``sign_in()`` and a refresh talk to Microsoft, through ``requests`` (an injected session in tests: the tests never
sign in and never reach the network). The tokens are registered with :func:`config.register_secret` as soon as they
are read; the log names an account as "work", "personal" or "other" (google_auth.logged_alias) and never writes a
token, an address, a code or a URL's query. Qt-free.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from . import atomic
from .config import register_secret
from .google_auth import (
    PROBLEM_BLOCKED,
    PROBLEM_DENIED,
    PROBLEM_EXPIRED,
    PROBLEM_FAILED,
    PROBLEM_SETUP,
    PROBLEM_SIGNED_OUT,
    PROBLEM_TIMEOUT,
    AccountAuthError,
    AccountError,
    AccountNotSignedIn,
    AccountSetupError,
    logged_alias,
    valid_alias,
)

logger = logging.getLogger(__name__)

AUTHORITY = "https://login.microsoftonline.com"
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
# Delegated permissions, read only: offline_access gives the refresh token; User.Read is the sign-in itself.
MICROSOFT_SCOPES = ("offline_access", "User.Read", "Mail.Read", "Calendars.Read")
TENANTS = ("common", "consumers", "organizations")
DEFAULT_TENANT = "common"
TOKEN_PATTERN = "ms_token_{alias}.json"
REDIRECT_HOST = "localhost"
HTTP_TIMEOUT_S = 30
REFRESH_EARLY_S = 60            # a token that runs out within this many seconds is refreshed first
NO_CLIENT_ID_MESSAGE = ("Outlook needs an app id first: register a free app and put its client id in config.toml "
                        "[microsoft] client_id (README: Microsoft / Outlook accounts), then restart Jarvis")
NOT_SIGNED_IN_MESSAGE = "Not signed in to Outlook"
SUCCESS_PAGE = ("<!doctype html><meta charset=utf-8><title>Jarvis</title><body style=\"font-family:sans-serif;"
                "background:#000;color:#ddd;padding:3em\">Jarvis is connected to Outlook for the {alias} account. "
                "You can close this tab.</body>")
FAILED_PAGE = ("<!doctype html><meta charset=utf-8><title>Jarvis</title><body style=\"font-family:sans-serif;"
               "background:#000;color:#ddd;padding:3em\">The Outlook sign-in did not finish. You can close this tab "
               "and try Connect again in Jarvis.</body>")
_TENANT_ID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_CLIENT_ID_RE = _TENANT_ID_RE
_CODE_WORD_RE = re.compile(r"[a-z_]{1,40}")
# Microsoft's error codes (AADSTS...) that mean an administrator or a policy does not allow the app.
_BLOCKED_CODES = ("AADSTS65001", "AADSTS90094", "AADSTS50105", "AADSTS53003", "AADSTS900971", "AADSTS50020")
_VERIFIER_CHARS = re.compile(r"[A-Za-z0-9._~-]{43,128}")


# --------------------------------------------------------------------------
# Paths, settings and PKCE
# --------------------------------------------------------------------------

def token_path(data_dir: Path, alias: str) -> Path:
    """<data_dir>\\ms_token_<alias>.json"""
    if not valid_alias(alias):
        raise ValueError("not an account alias")
    return Path(data_dir) / TOKEN_PATTERN.format(alias=alias)


def valid_client_id(client_id: str) -> bool:
    """An Entra application (client) id: a GUID such as 00000000-0000-0000-0000-000000000000."""
    return isinstance(client_id, str) and bool(_CLIENT_ID_RE.fullmatch(client_id.strip()))


def valid_tenant(tenant: str) -> bool:
    """"common", "consumers", "organizations" or a directory (tenant) id GUID."""
    return isinstance(tenant, str) and (tenant in TENANTS or bool(_TENANT_ID_RE.fullmatch(tenant)))


def endpoints(tenant: str) -> tuple[str, str]:
    """(authorize URL, token URL) of the v2.0 endpoints for ``tenant`` (DEFAULT_TENANT when it is not a valid one)."""
    tenant = tenant if valid_tenant(tenant) else DEFAULT_TENANT
    base = f"{AUTHORITY}/{tenant}/oauth2/v2.0"
    return f"{base}/authorize", f"{base}/token"


def make_verifier() -> str:
    """A fresh PKCE code verifier: 64 random characters of RFC 7636's unreserved set."""
    return secrets.token_urlsafe(48)[:64]


def challenge(verifier: str) -> str:
    """The S256 code challenge of ``verifier``: BASE64URL(SHA256(verifier)) without padding."""
    if not isinstance(verifier, str) or not _VERIFIER_CHARS.fullmatch(verifier):
        raise ValueError("not a PKCE code verifier")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def make_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(client_id: str, tenant: str, redirect_uri: str, code_challenge: str, state: str,
                  scopes: tuple[str, ...] = MICROSOFT_SCOPES) -> str:
    """The authorization request (response_type=code, S256 PKCE, the answer in the query; Microsoft's account
    picker every time so the right account is chosen for the alias)."""
    authorize, _token = endpoints(tenant)
    query = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
             "response_mode": "query", "scope": " ".join(scopes), "state": state,
             "code_challenge": code_challenge, "code_challenge_method": "S256", "prompt": "select_account"}
    return f"{authorize}?{urlencode(query)}"


def _register(info: dict[str, Any]) -> None:
    for name in ("access_token", "refresh_token", "id_token"):
        value = info.get(name)
        if isinstance(value, str):
            register_secret(value)


def _error_words(answer: Any) -> tuple[str, str]:
    """(error, the AADSTS codes) of a token endpoint's error answer; the description itself is never kept."""
    if not isinstance(answer, dict):
        return "", ""
    error = answer.get("error")
    error = error if isinstance(error, str) and _CODE_WORD_RE.fullmatch(error) else ""
    description = answer.get("error_description")
    codes = " ".join(re.findall(r"AADSTS\d{4,7}", description)) if isinstance(description, str) else ""
    return error, codes


# --------------------------------------------------------------------------
# The loopback redirect
# --------------------------------------------------------------------------

class _RedirectServer(http.server.HTTPServer):
    """Listens on localhost:<free port> for the one sign-in answer (code and state, or an error)."""

    allow_reuse_address = False

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.answer: dict[str, str] | None = None
        super().__init__((REDIRECT_HOST, 0), _RedirectHandler)

    @property
    def redirect_uri(self) -> str:
        return f"http://{REDIRECT_HOST}:{self.server_address[1]}/"


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    server: _RedirectServer

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        parts = urlsplit(self.path)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        if parts.path != "/" or not ({"code", "error"} & set(params)):
            self.send_response(404)
            self.end_headers()
            return
        if self.server.answer is None:
            self.server.answer = params
        page = SUCCESS_PAGE.format(alias=self.server.alias) if "code" in params else FAILED_PAGE
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        """Never logged: the request line carries the authorization code."""


# --------------------------------------------------------------------------
# MicrosoftAccount
# --------------------------------------------------------------------------

class MicrosoftAccount:
    """The saved Outlook sign-in of one account alias. Blocking: use a worker thread.

    ``is_configured`` and ``is_signed_in`` read files only (no lock, no network), so the UI thread may ask while a
    sign-in waits in the browser. ``access_token`` and ``sign_in`` are serialised by an internal lock. ``session``: the
    HTTP session (requests.Session made on first use; tests pass a fake), ``opener``: opens the sign-in page
    (webbrowser.open), ``clock``: seconds since the epoch. ``persist`` False: a background run's account - its token
    lives in memory only, is never saved or deleted, and it never signs in.
    """

    def __init__(self, alias: str, *, client_id: str, data_dir: Path, tenant: str = DEFAULT_TENANT,
                 session: Any = None, opener: Callable[[str], Any] | None = None,
                 clock: Callable[[], float] | None = None, open_timeout_s: int = 300, persist: bool = True) -> None:
        self.alias = alias
        self.client_id = client_id.strip() if isinstance(client_id, str) else ""
        self.tenant = tenant if valid_tenant(tenant) else DEFAULT_TENANT
        self.token_path = token_path(Path(data_dir), alias)
        self._session = session
        self._opener = opener or webbrowser.open
        self._clock = clock or time.time
        self._open_timeout_s = int(open_timeout_s)
        self.persist = bool(persist)
        self._lock = threading.RLock()
        self._info: dict[str, Any] | None = None
        self._problem = ""
        self._problem_message = ""
        self.generation = 0      # moves on whenever the held token changes
        self.log_name = f"Outlook ({logged_alias(alias)})"
        register_secret(self.client_id)

    # ---- state without the network ------------------------------------------------

    def is_configured(self) -> bool:
        """True when ``[microsoft] client_id`` holds an app's client id (the Connect needs nothing else)."""
        return valid_client_id(self.client_id)

    def setup_problem(self) -> str:
        """Why the Connect can't start ("" when it can): no client id yet."""
        return "" if self.is_configured() else NO_CLIENT_ID_MESSAGE

    def is_signed_in(self) -> bool:
        """A saved sign-in with a refresh token exists (no network)."""
        info = self._info if self._info is not None else self._read()
        return bool(info) and isinstance(info.get("refresh_token"), str) and bool(info["refresh_token"])

    def problem(self) -> tuple[str, str]:
        """(PROBLEM_*, message) of the last failed sign-in or rejected token; ("", "") when none."""
        return self._problem, self._problem_message

    def _set_problem(self, problem: str = "", message: str = "") -> None:
        self._problem, self._problem_message = problem, message

    # ---- tokens ------------------------------------------------------------------------

    def access_token(self, *, force_refresh: bool = False) -> str:
        """A usable access token: the saved one, refreshed first when it runs out within a minute (or ``force_refresh``).
        Never signs in: AccountNotSignedIn without a saved sign-in, AccountSetupError without a client id."""
        with self._lock:
            if not self.is_configured():
                raise AccountSetupError(NO_CLIENT_ID_MESSAGE)
            info = self._info if self._info is not None else self._read()
            if not info or not isinstance(info.get("refresh_token"), str):
                raise AccountNotSignedIn(f"{NOT_SIGNED_IN_MESSAGE} ({self.alias} account)")
            self._info = info
            expires = info.get("expires_at")
            fresh = isinstance(expires, (int, float)) and float(expires) - REFRESH_EARLY_S > self._clock()
            token = info.get("access_token")
            if force_refresh or not fresh or not isinstance(token, str) or not token:
                info = self._refresh(info)
            return str(info["access_token"])

    def _refresh(self, info: dict[str, Any]) -> dict[str, Any]:
        _authorize, token_url = endpoints(self.tenant)
        data = {"client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": info["refresh_token"],
                "scope": " ".join(MICROSOFT_SCOPES)}
        answer = self._post(token_url, data, "refreshing the sign-in")
        if answer[0] != 200:
            error, codes = _error_words(answer[1])
            if error in ("invalid_grant", "interaction_required", "consent_required", "invalid_client",
                         "unauthorized_client") or answer[0] in (400, 401):
                if any(code in codes for code in _BLOCKED_CODES):
                    message = f"Microsoft blocked the {self.alias} account's sign-in: its administrator does not allow this app"
                    self.forget("Microsoft blocked it", problem=PROBLEM_BLOCKED, message=message)
                    raise AccountAuthError(message, problem=PROBLEM_BLOCKED, status=answer[0])
                message = f"The Outlook sign-in for the {self.alias} account expired or was revoked; connect it again"
                self.forget("Microsoft rejected it", problem=PROBLEM_EXPIRED, message=message)
                raise AccountAuthError(message, problem=PROBLEM_EXPIRED, status=answer[0])
            raise AccountError(f"Microsoft could not refresh the {self.alias} account's sign-in just now "
                               f"({answer[0]}); try again", status=answer[0])
        fresh = self._token_info(answer[1], keep_refresh=info.get("refresh_token"))
        self._save(fresh)
        self._info = fresh
        self.generation += 1
        logger.info("%s: the sign-in was refreshed", self.log_name)
        return fresh

    def _token_info(self, answer: Any, *, keep_refresh: Any = None) -> dict[str, Any]:
        """The token file's content from a token endpoint answer (a missing refresh token keeps the old one)."""
        if not isinstance(answer, dict) or not isinstance(answer.get("access_token"), str) \
                or not answer["access_token"]:
            raise AccountError("Microsoft's answer had no access token")
        refresh = answer.get("refresh_token") if isinstance(answer.get("refresh_token"), str) else keep_refresh
        if not isinstance(refresh, str) or not refresh:
            raise AccountError("Microsoft's answer had no refresh token (offline_access was not granted)")
        expires_in = answer.get("expires_in")
        try:
            seconds = max(0.0, float(expires_in))
        except (TypeError, ValueError):
            seconds = 0.0
        info = {"access_token": answer["access_token"], "refresh_token": refresh,
                "expires_at": round(self._clock() + seconds, 3),
                "scope": answer.get("scope") if isinstance(answer.get("scope"), str) else " ".join(MICROSOFT_SCOPES),
                "tenant": self.tenant}
        _register(info)
        return info

    def _post(self, url: str, data: dict[str, str], what: str) -> tuple[int, Any]:
        """One POST to the token endpoint: (HTTP status, the JSON answer or None). The network's own failures are
        AccountError; the answer is never logged."""
        session = self._get_session()
        try:
            response = session.post(url, data=data, timeout=HTTP_TIMEOUT_S,
                                    headers={"Accept": "application/json"})
        except Exception as exc:  # noqa: BLE001 - requests' errors share no small base; mapped to a safe message
            logger.info("%s: %s failed (%s)", self.log_name, what, type(exc).__name__)
            raise AccountError(f"Could not reach Microsoft while {what} ({type(exc).__name__})") from None
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - not JSON: only the status is used
            body = None
        if isinstance(body, dict):
            _register(body)
        logger.info("%s: Microsoft answered %d while %s", self.log_name, status, what)
        return status, body

    def _get_session(self) -> Any:
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    # ---- sign-in -----------------------------------------------------------------------

    def sign_in(self) -> None:
        """Open the Microsoft sign-in in the browser and save the tokens. Blocks until the owner finishes or
        ``open_timeout_s`` passes. AccountSetupError without a client id (nothing opens), AccountAuthError when it
        was cancelled, denied, blocked or timed out, AccountError otherwise."""
        if not self.persist:
            raise AccountNotSignedIn()
        with self._lock:
            if not self.is_configured():
                self._set_problem(PROBLEM_SETUP, NO_CLIENT_ID_MESSAGE)
                raise AccountSetupError(NO_CLIENT_ID_MESSAGE)
            verifier, state = make_verifier(), make_state()
            try:
                server = _RedirectServer(self.alias)
            except OSError as exc:
                raise AccountError(f"Could not listen for the Outlook sign-in on this PC ({type(exc).__name__})") \
                    from None
            try:
                redirect_uri = server.redirect_uri
                url = authorize_url(self.client_id, self.tenant, redirect_uri, challenge(verifier), state)
                logger.info("%s: waiting for sign-in in the browser (up to %d s)", self.log_name, self._open_timeout_s)
                try:
                    self._opener(url)
                except Exception as exc:  # noqa: BLE001 - no browser: a safe message
                    raise AccountError(f"Could not open the browser for the Outlook sign-in ({type(exc).__name__})") \
                        from None
                answer = self._wait(server)
            finally:
                server.server_close()
            try:
                code = self._code_from(answer, state)
                _authorize, token_url = endpoints(self.tenant)
                status, body = self._post(token_url, {
                    "client_id": self.client_id, "grant_type": "authorization_code", "code": code,
                    "redirect_uri": redirect_uri, "code_verifier": verifier, "scope": " ".join(MICROSOFT_SCOPES)},
                    "finishing the sign-in")
                if status != 200:
                    error, codes = _error_words(body)
                    if any(item in codes for item in _BLOCKED_CODES):
                        raise AccountAuthError(f"Microsoft blocked the sign-in: the {self.alias} account's "
                                               "administrator does not allow this app", problem=PROBLEM_BLOCKED,
                                               status=status)
                    raise AccountAuthError(f"The Outlook sign-in failed ({error or status})", status=status)
                info = self._token_info(body)
            except AccountError as exc:
                self._set_problem(exc.problem, str(exc))
                raise
            self._save(info)
            self._info = info
            self.generation += 1
            self._set_problem()
            logger.info("%s: signed in", self.log_name)

    def _wait(self, server: _RedirectServer) -> dict[str, str]:
        deadline = time.monotonic() + self._open_timeout_s
        server.timeout = 0.5
        while server.answer is None:
            if time.monotonic() >= deadline:
                message = f"The Outlook sign-in for the {self.alias} account was not finished in time"
                self._set_problem(PROBLEM_TIMEOUT, message)
                raise AccountAuthError(message, problem=PROBLEM_TIMEOUT)
            server.handle_request()
        return server.answer

    def _code_from(self, answer: dict[str, str], state: str) -> str:
        """The authorization code of the browser's answer, after its state was checked."""
        if not secrets.compare_digest(str(answer.get("state", "")), state):
            raise AccountAuthError("The Outlook sign-in answer did not match this sign-in; try Connect again")
        error = answer.get("error", "")
        if error:
            codes = " ".join(re.findall(r"AADSTS\d{4,7}", answer.get("error_description", "")))
            if any(code in codes for code in _BLOCKED_CODES):
                raise AccountAuthError(f"Microsoft blocked the sign-in: the {self.alias} account's administrator "
                                       "does not allow this app", problem=PROBLEM_BLOCKED)
            if error in ("access_denied", "consent_required"):
                raise AccountAuthError(f"The Outlook sign-in for the {self.alias} account was cancelled or access was "
                                       "denied", problem=PROBLEM_DENIED)
            safe = error if _CODE_WORD_RE.fullmatch(error) else "error"
            raise AccountAuthError(f"The Outlook sign-in failed ({safe})", problem=PROBLEM_FAILED)
        code = answer.get("code", "")
        if not code:
            raise AccountAuthError("The Outlook sign-in gave no code; try Connect again")
        register_secret(code)
        return code

    # ---- forgetting ----------------------------------------------------------------------

    def forget(self, reason: str, *, problem: str = PROBLEM_EXPIRED, message: str = "") -> bool:
        """Delete the saved sign-in (the next Connect signs in again) and remember why. True when none is left."""
        with self._lock:
            self._info = None
            self.generation += 1
            if problem != PROBLEM_SIGNED_OUT:
                self._set_problem(problem, message)
            if not self.persist:
                return True
            try:
                self.token_path.unlink()
            except FileNotFoundError:
                return True
            except OSError as exc:
                logger.warning("%s: could not delete the saved sign-in (%s)", self.log_name,
                               exc.strerror or type(exc).__name__)
                return False
            logger.warning("%s: removed the saved sign-in (%s)", self.log_name, reason)
            return True

    def disconnect(self) -> None:
        """Delete the saved sign-in (Settings > Accounts' Disconnect). AccountError when the file could not go."""
        if not self.persist:
            raise AccountError("A background run can't disconnect an account; nothing was changed")
        if not self.forget("disconnected", problem=PROBLEM_SIGNED_OUT):
            raise AccountError(f"Could not delete the {self.alias} account's saved Outlook sign-in (the file is in "
                               "use); try again")
        self._set_problem()

    # ---- files ---------------------------------------------------------------------------

    def _read(self) -> dict[str, Any] | None:
        try:
            info = json.loads(self.token_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.debug("%s: the saved sign-in is unusable (%s)", self.log_name, type(exc).__name__)
            return None
        if not isinstance(info, dict):
            return None
        _register(info)
        return info

    def _save(self, info: dict[str, Any]) -> None:
        """Write the token JSON atomically, as the Google sign-ins are (a temporary file of its own in the same
        folder, then a replace). A failure is logged, not raised. A background account keeps it in memory."""
        if not self.persist:
            return
        path = self.token_path
        part: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".part", dir=path.parent)
            part = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(info))
            atomic.replace(part, path)
        except OSError as exc:
            if part is not None:
                try:
                    part.unlink()
                except OSError:
                    pass
            logger.warning("%s: could not save the sign-in (%s); you will be asked to connect again next time",
                           self.log_name, exc.strerror or type(exc).__name__)
