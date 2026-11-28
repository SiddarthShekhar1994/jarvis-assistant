"""Sites Jarvis may use signed in: the owner's list, and the rules for a page read signed in as him. No Qt.

The owner signs in himself, in a plain window of Jarvis's own (site_signins_ui.SignInWindow), with a sign-in profile of
Jarvis's own: a folder of its own in the data folder (FOLDER, "site-signins"). Nothing is ever imported, read or copied
from Chrome, Edge or any other browser. Only a task's page on a listed site (the host itself or a subdomain of it)
opens with that profile (agent.browser); every other page, every research page picture, the board and every snapshot
stay off the record as before. Off while the list is empty, which is the default.

    host_problem(text) -> (host, problem)   the owner's typed site checked: a public host name such as "example.com"
                                    (an https:// in front and one "/" after it are let go; a path, a port, a user name,
                                    an address of this PC or its network, a number address or a name many people's
                                    sites share is refused, in Jarvis's plain words). ("example.com", "") when good
    listed_host(url, hosts)         the listed entry an https page is on ("" when none): the host itself or one of its
                                    subdomains; a lookalike such as example.com.evil.net or myexample.com is not
    SiteList(folder)                site_signins.json in the data folder (a file of its own, as wake.json is:
                                    settings.json's overrides never widen what is read or reached): hosts(), add(text),
                                    remove(host), and the hosts whose cookies wait to be deleted (removed()). A damaged,
                                    too big or odd file means no sites (fail-closed). LF, written atomically
    request_refused(method, frame, pressing)   the read-only rule for one request of a signed-in page: a main or inline
                                    frame asked for with anything but GET or HEAD (a POST, PUT, PATCH, DELETE ...) is
                                    refused unless the owner's click on a Send card presses it now
    navigation_refused(kind, main_frame, host, hosts, pressing)   ... and for a navigation: a form submit is refused
                                    unless that click presses it; a main frame never leaves the listed sites
    signs_out(words)                a link's words that would sign out or change the account (never followed signed in)
    LIVE_NOTE                       the step's words in LIVE: "signed in as you on <host>" (the page's host only)

Logs hold counts and reason words only: never a host, an address or a word of a page.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .actions import public_host_problem

logger = logging.getLogger(__name__)

FOLDER = "site-signins"           # the sign-in profile's own folder in the data folder (cookies, site storage)
LIST_FILE = "site_signins.json"   # the owner's list, beside it (Forget every sign-in keeps the list)
FORGET_MARK = "site-signins.forget"   # a Forget that could not delete the folder at once: done at the next start
MAX_SITES = 50
MAX_FILE_BYTES = 64 * 1024
HOST_CAP = 253
LIVE_NOTE = "signed in as you on {host}"
SAFE_METHODS = frozenset({"GET", "HEAD"})

# Names many different people's sites live under: listing one would sign Jarvis in on all of them.
SHARED_NAMES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "com.au", "net.au", "org.au",
    "co.nz", "org.nz", "co.jp", "ne.jp", "or.jp", "co.in", "co.kr", "co.za", "com.br", "com.cn", "com.mx", "com.tr",
    "com.sg", "com.hk", "com.tw", "github.io", "gitlab.io", "blogspot.com", "appspot.com", "herokuapp.com",
    "netlify.app", "vercel.app", "pages.dev", "workers.dev", "web.app", "firebaseapp.com", "azurewebsites.net",
    "cloudfront.net", "amazonaws.com", "onrender.com", "glitch.me", "fly.dev", "ngrok.io", "ngrok-free.app",
})

# Why a typed site is not taken (the owner reads these under the field).
P_EMPTY = "Type a site, such as example.com."
P_SHAPE = "Type just the site's name, such as example.com: no page, port or other part of an address."
P_LOCAL = "Jarvis uses a sign-in only on a public site, never an address of this PC or your network."
P_SHARED = "Many different people's sites share that name. Type the site itself, such as yours.github.io."
P_FULL = f"Jarvis keeps at most {MAX_SITES} sites. Remove one first."
P_LISTED = "That site is on the list already."
P_UNSAVED = "The list could not be saved, so nothing changed."

# The read-only rule's reason words (logs and counts only).
R_WRITE, R_FORM, R_OFF_LIST = "write", "form", "off_list"

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_SIGN_OUT_RE = re.compile(r"\b(?:sign(?:ing)?[\s-]?out|log(?:ging)?[\s-]?out|logout|signout|log[\s-]?off|"
                          r"delete|remove|close\s+(?:my\s+|your\s+)?account|deactivate|unsubscribe|"
                          r"change\s+(?:my\s+|your\s+)?password|reset\s+(?:my\s+|your\s+)?password)\b",
                          re.IGNORECASE)


# --------------------------------------------------------------------------
# The owner's typed site
# --------------------------------------------------------------------------

def host_problem(text: Any) -> tuple[str, str]:
    """(host, "") for a good site name, else ("", Jarvis's words why not). See the module docs."""
    if not isinstance(text, str) or not text.strip():
        return "", P_EMPTY
    name = text.strip()
    lowered = name.casefold()
    if lowered.startswith("https://"):
        name = name[len("https://"):]
        if name.endswith("/") and name.count("/") == 1:
            name = name[:-1]
    if not name or any(char in name for char in "/\\?#@:[]% \t\r\n") or len(name) > HOST_CAP:
        return "", P_SHAPE
    name = name.rstrip(".")
    try:
        name = name.encode("idna").decode("ascii").casefold()
    except (UnicodeError, ValueError):
        return "", P_SHAPE
    labels = name.split(".")
    if not name or len(name) > HOST_CAP or not all(_LABEL_RE.fullmatch(label) for label in labels):
        return "", P_SHAPE
    if public_host_problem(name):
        return "", P_LOCAL
    if name in SHARED_NAMES:
        return "", P_SHARED
    return name, ""


def _clean_host(value: Any) -> str:
    """A host as kept in the list: one host_problem takes as it is, else ""."""
    host, problem = host_problem(value)
    return host if not problem and host == value else ""


# --------------------------------------------------------------------------
# A page's site
# --------------------------------------------------------------------------

def page_host(url: Any) -> str:
    """The host of an https page as the engine gives it (an ASCII name, lower case, no trailing dot), "" for anything
    that is not a plain https page on port 443 with no user name."""
    if not isinstance(url, str) or not url.isascii() or any(char.isspace() for char in url):
        return ""
    try:
        parts = urlsplit(url)
        if parts.scheme.casefold() != "https" or parts.username is not None or parts.password is not None:
            return ""
        if parts.port not in (None, 443):
            return ""
        return (parts.hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def host_listed(host: Any, hosts: Iterable[str]) -> str:
    """The listed entry ``host`` is on: itself, or a subdomain of it ("" when none). Only whole labels count, so a
    lookalike (example.com.evil.net, myexample.com) is never on example.com."""
    if not isinstance(host, str) or not host:
        return ""
    name = host.casefold().rstrip(".")
    best = ""
    for entry in hosts or ():
        if not isinstance(entry, str) or not entry:
            continue
        if (name == entry or name.endswith("." + entry)) and len(entry) > len(best):
            best = entry
    return best


def listed_host(url: Any, hosts: Iterable[str]) -> str:
    """The listed entry the https page ``url`` is on, "" when none (page_host, host_listed)."""
    return host_listed(page_host(url), hosts)


def signs_out(words: Any) -> bool:
    """A link's words that sign out, delete or change the account: Jarvis never follows one signed in."""
    return isinstance(words, str) and bool(_SIGN_OUT_RE.search(words))


# --------------------------------------------------------------------------
# Read only
# --------------------------------------------------------------------------

def request_refused(method: Any, frame: bool, pressing: bool) -> str:
    """The reason word a signed-in page's request is refused for ("" when it may go): a main or inline frame asked for
    with anything but GET or HEAD, unless the owner's click on a Send card presses it now. A script's own reads go
    as they do on any page."""
    if pressing or not frame:
        return ""
    name = method.decode("ascii", "replace") if isinstance(method, (bytes, bytearray)) else str(method or "")
    return "" if name.strip().upper() in SAFE_METHODS else R_WRITE


def navigation_refused(kind: Any, main_frame: bool, host: Any, hosts: Iterable[str], pressing: bool) -> str:
    """The reason word a signed-in page's navigation is refused for ("" when it may go): a form submit unless the
    owner's click on a Send card presses it now; a main frame that would leave the listed sites, always."""
    name = str(getattr(kind, "name", kind) or "")
    if not pressing and ("FormSubmitted" in name or name == "form"):
        return R_FORM
    if main_frame and not host_listed(host, hosts):
        return R_OFF_LIST
    return ""


# --------------------------------------------------------------------------
# The list file
# --------------------------------------------------------------------------

class SiteList:
    """``site_signins.json`` in the data folder: {"sites": [host, ...], "remove_cookies": [host, ...]}. Thread-safe.
    The file is read once; what is in memory is what counts for this run. A file that is not valid JSON, too big, or
    holds anything odd counts as no sites at all (fail-closed): a good entry of a damaged file is not kept either."""

    def __init__(self, folder: Path | str) -> None:
        self.path = Path(folder) / LIST_FILE
        self._lock = threading.Lock()
        self._sites, self._removed = self._load()

    def __repr__(self) -> str:        # counts only
        return f"SiteList({len(self._sites)} site(s))"

    def _load(self) -> tuple[list[str], list[str]]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return [], []
        except OSError as exc:
            logger.warning("Signed-in sites: the list could not be read (%s); no site is used signed in",
                           type(exc).__name__)
            return [], []
        try:
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("too big")
            data = json.loads(raw.decode("utf-8"))
            sites = self._hosts_of(data, "sites")
            removed = self._hosts_of(data, "remove_cookies")
        except (ValueError, UnicodeDecodeError) as exc:
            logger.warning("Signed-in sites: the list is damaged (%s); no site is used signed in",
                           "too_big" if str(exc) == "too big" else "bad_file")
            return [], []
        return sites, [host for host in removed if host not in sites]

    @staticmethod
    def _hosts_of(data: Any, key: str) -> list[str]:
        if not isinstance(data, dict):
            raise ValueError("not an object")
        items = data.get(key, [])
        if not isinstance(items, list) or len(items) > MAX_SITES * 4:
            raise ValueError("not a list")
        hosts: list[str] = []
        for item in items:
            host = _clean_host(item)
            if not host:
                raise ValueError("not a host")
            if host not in hosts:
                hosts.append(host)
        if key == "sites" and len(hosts) > MAX_SITES:
            raise ValueError("too many")
        return hosts

    def hosts(self) -> tuple[str, ...]:
        """The listed sites, in the order they were added (empty: nothing is used signed in)."""
        with self._lock:
            return tuple(self._sites)

    def removed(self) -> tuple[str, ...]:
        """Sites taken off the list whose cookies are still to be deleted from the sign-in profile."""
        with self._lock:
            return tuple(self._removed)

    def add(self, text: Any) -> tuple[str, str]:
        """The owner's Add: (host, "") when it is listed now (and saved), else ("", why not)."""
        host, problem = host_problem(text)
        if problem:
            return "", problem
        with self._lock:
            if host in self._sites:
                return "", P_LISTED
            if len(self._sites) >= MAX_SITES:
                return "", P_FULL
            before = (list(self._sites), list(self._removed))
            self._sites.append(host)
            self._removed = [item for item in self._removed if item != host]
            if not self._save_locked():
                self._sites, self._removed = before
                return "", P_UNSAVED
        logger.info("Signed-in sites: a site was added (%d listed)", len(self._sites))
        return host, ""

    def remove(self, host: Any) -> bool:
        """The owner's Remove: off the list now (also when it could not be saved: never used again in this run), and
        its cookies wait to be deleted (forget_removed). True when saved."""
        with self._lock:
            if host not in self._sites:
                return False
            self._sites = [item for item in self._sites if item != host]
            if host not in self._removed:
                self._removed.append(host)
            saved = self._save_locked()
        logger.info("Signed-in sites: a site was removed (%d listed)", len(self._sites))
        return saved

    def forget_removed(self, hosts: Sequence[str]) -> None:
        """Those sites' cookies were deleted: they wait no more."""
        with self._lock:
            left = [item for item in self._removed if item not in set(hosts)]
            if left != self._removed:
                self._removed = left
                self._save_locked()

    def _save_locked(self) -> bool:
        from .prefs import _write_atomic

        body = json.dumps({"sites": self._sites, "remove_cookies": self._removed}, indent=2) + "\n"
        return _write_atomic(self.path, body)


def cookie_on(domain: Any, hosts: Iterable[str]) -> bool:
    """A cookie's domain (".example.com", "mail.example.com") belongs to one of ``hosts`` or a subdomain of one."""
    if isinstance(domain, (bytes, bytearray)):
        domain = domain.decode("ascii", "replace")
    if not isinstance(domain, str):
        return False
    return bool(host_listed(domain.strip().lstrip("."), hosts))


__all__ = ["FOLDER", "FORGET_MARK", "LIST_FILE", "LIVE_NOTE", "MAX_SITES", "SiteList", "cookie_on", "host_listed",
           "host_problem", "listed_host", "navigation_refused", "page_host", "request_refused", "signs_out"]
