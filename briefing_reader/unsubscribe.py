"""Unsubscribing from a mailing list by its own header (mail extras, part 3). Qt-free.

A message's List-Unsubscribe header (RFC 2369) names how its list stops: an https link, a mailto: address, or both;
List-Unsubscribe-Post (RFC 8058) says the https link takes one POST with no page to visit. Jarvis reads the target only
from the message's own headers (Gmail's full message), never from a model or a mail body, and only for a sender Gmail
verified (DMARC passed). Nothing here runs by itself: the card's Unsubscribe click, its hold, then one call.

    read_headers(headers) -> ListHeader      the message's headers (Gmail's [{name, value}]) as what they offer: the
                                             https link, the mailto (one plain address, a short subject and body),
                                             one-click, Gmail's verdict, the list's id and name, the sender
    dmarc_passed(headers) -> bool            Gmail's own verdict: the FIRST Authentication-Results header is
                                             mx.google.com's and says dmarc=pass (standing.mailwatch uses it too)
    choose(header) -> ONE_CLICK | MAILTO | PAGE | a refusal word
                                             one-click first (both headers, a public host on 443, verified), then the
                                             list's address (an Email card), then its page (an Open card)
    url_problem(url) -> str                  "" when the one-click link may be posted to (https, a public host by
                                             name, no user or password, port 443)
    one_click(url, *, post=real_post, resolve=real_resolve) -> OneClick
                                             ONE POST "List-Unsubscribe=One-Click" (form encoded), no cookies, no proxy,
                                             no redirect followed, 10 s in all, at most 64 KB read; 2xx done, 3xx not
                                             confirmed (failed), 4xx failed, 5xx / a timeout / no answer unknown; never
                                             retried; refused while the tests run

Logs hold the method word, the HTTP status, the duration and reason words only: never a URL, a host, an address or a
list id.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .actions import email_address, public_host_problem
from .capture_net import NetRefusedInTests, address_problem, real_resolve

logger = logging.getLogger(__name__)

ONE_CLICK = "one_click"
MAILTO = "mailto"
PAGE = "page"
METHODS = (ONE_CLICK, MAILTO, PAGE)
# Why there is no way to unsubscribe here (an information-only card says it in words: REFUSAL_WORDS).
NOT_VERIFIED = "not_verified"
NO_HEADER = "no_header"
NO_METHOD = "no_method"
REFUSAL_WORDS = {
    NOT_VERIFIED: "Gmail could not verify this sender, so Jarvis won't unsubscribe - mark it as spam in Gmail instead",
    NO_HEADER: "This message names no way to unsubscribe - look for the link at the bottom of the email",
    NO_METHOD: "This message's unsubscribe link is not one Jarvis may use - look for the link in the email",
}

ONE_CLICK_VALUE = "List-Unsubscribe=One-Click"       # List-Unsubscribe-Post's one value, and the POST's whole body
CONTENT_TYPE = "application/x-www-form-urlencoded"
USER_AGENT = "JarvisAssistant/unsubscribe"
TIMEOUT_S = 10.0
READ_CAP = 64 * 1024
MAILTO_SUBJECT_CAP = 100
MAILTO_BODY_CAP = 200
URL_CAP = 2048
TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"

DONE = "done"
FAILED = "failed"
UNKNOWN = "unknown"
# A failed one-click's reason words (logged and shown).
REASON_REFUSED = "refused"           # the link or its address is not a public https one: nothing was sent
REASON_REDIRECT = "not_confirmed"    # a 3xx: the server sent Jarvis elsewhere (never followed)
REASON_CLIENT = "rejected"           # a 4xx
REASON_SERVER = "server_error"       # a 5xx
REASON_TIMEOUT = "no_answer"         # a timeout or a dropped connection after the POST went

# One resinfo of Authentication-Results (RFC 8601), on the text with its comments and quoted strings taken out:
# "method=result" then its properties ("header.from=example.com").
_RESULT_RE = re.compile(r"\s*([A-Za-z0-9_.-]+)\s*=\s*([A-Za-z0-9_-]+)(.*)", re.DOTALL)
_HEADER_FROM_RE = re.compile(r"(?:^|\s)header\.from\s*=\s*([^\s;]+)", re.IGNORECASE)
_ENTRY_RE = re.compile(r"<([^<>]{1,2048})>")
_LIST_ID_RE = re.compile(r"<([A-Za-z0-9._-]{1,200})>")


def _header(headers: Any, name: str) -> str:
    """The FIRST header ``name`` of a Gmail headers list ([{name, value}]; "" when none)."""
    for entry in headers if isinstance(headers, list) else ():
        if isinstance(entry, dict) and str(entry.get("name", "")).casefold() == name:
            value = entry.get("value")
            return value if isinstance(value, str) else ""
    return ""


def _without_comments(text: str) -> str:
    """``text`` with its (comments) - nested ones too - and "quoted strings" taken out (each becomes a space), so words
    inside them (an envelope sender "dmarc=pass@spam.example", a reason) never read as a result."""
    out: list[str] = []
    depth = 0
    quoted = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\" and (quoted or depth):
            escaped = True
            continue
        if quoted:
            if char == '"':
                quoted = False
                out.append(" ")
            continue
        if char == "(":
            depth += 1
            continue
        if depth:
            if char == ")":
                depth -= 1
                if not depth:
                    out.append(" ")
            continue
        if char == '"':
            quoted = True
            continue
        out.append(char)
    return "" if quoted or depth else "".join(out)


def dmarc_passed(headers: Any) -> bool:
    """Gmail's verdict: the FIRST Authentication-Results header (Gmail puts its own on top; a sender may add others
    below) is mx.google.com's, and - its comments and quoted strings taken out - holds exactly one result whose method
    is exactly "dmarc", and that result is "pass"; when it names header.from, that is the From address's own domain.
    No dmarc result (or a header that can't be read) is not verified. Never raises."""
    try:
        stripped = _without_comments(_header(headers, "authentication-results"))
        parts = stripped.split(";")
        if not parts or parts[0].strip().casefold() != "mx.google.com":
            return False
        found = [match for match in (_RESULT_RE.fullmatch(part) for part in parts[1:]) if match is not None
                 and match.group(1).casefold() == "dmarc"]
        if len(found) != 1 or found[0].group(2).casefold() != "pass":
            return False
        claimed = _HEADER_FROM_RE.search(found[0].group(3))
        if claimed is not None:
            sender = email_address(_header(headers, "from"))
            domain = sender.rpartition("@")[2].casefold()
            if not domain or claimed.group(1).strip().rstrip(".").casefold() != domain:
                return False
        return True
    except Exception:  # noqa: BLE001 - a header Jarvis can't read is not verified
        return False


@dataclass(frozen=True)
class Mailto:
    """The list's own address to write to, with its short subject and body (other fields dropped)."""

    address: str
    subject: str = field(default="", repr=False)
    body: str = field(default="", repr=False)

    def __repr__(self) -> str:
        return f"Mailto({len(self.subject)}/{len(self.body)} characters)"


@dataclass(frozen=True)
class ListHeader:
    """What a message's headers offer for unsubscribing. Memory only; the repr holds what kinds are there."""

    https: str = field(default="", repr=False)
    mailto: Mailto | None = field(default=None, repr=False)
    one_click: bool = False
    verified: bool = False
    list_id: str = field(default="", repr=False)
    name: str = field(default="", repr=False)
    sender: str = field(default="", repr=False)

    @property
    def present(self) -> bool:
        return bool(self.https or self.mailto)

    def __repr__(self) -> str:
        kinds = [word for word, there in (("https", self.https), ("mailto", self.mailto),
                                          ("one-click", self.one_click), ("verified", self.verified)) if there]
        return f"ListHeader({', '.join(kinds) or 'none'})"


def _clean(text: str, cap: int) -> str:
    """One line of plain words (no control or format characters), at most ``cap`` characters."""
    out = "".join(" " if not char.isprintable() else char for char in str(text or ""))
    out = " ".join(out.split())
    return out[:cap]


def _mailto(target: str) -> Mailto | None:
    """mailto:address?subject=..&body=.. -> Mailto, or None unless it names exactly one plain address (a link Python
    can't split, or with too many fields, is None too: a sender writes this header, so it never raises)."""
    try:
        parts = urlsplit(target)
        if parts.scheme.casefold() != "mailto":
            return None
        path = unquote(parts.path)
        query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=20)
    except ValueError:
        return None
    lower = {key.casefold(): values for key, values in query.items()}
    extra = [value for value in lower.get("to", []) if value.strip()]
    if "," in path or ";" in path or extra or len(lower.get("subject", [])) > 1 or len(lower.get("body", [])) > 1:
        return None
    address = email_address(path)
    if not address:
        return None
    subject = _clean((lower.get("subject") or [""])[0], MAILTO_SUBJECT_CAP)
    body = _clean((lower.get("body") or [""])[0], MAILTO_BODY_CAP)
    return Mailto(address, subject, body)


def read_headers(headers: Any) -> ListHeader:
    """A message's headers (Gmail's [{name, value}]) -> ListHeader: the first https and the first mailto entry of the
    first List-Unsubscribe header, one-click only when List-Unsubscribe-Post is exactly ONE_CLICK_VALUE, Gmail's
    verdict, the List-Id (its id in <>, casefolded, and its name before it) and the From address. Never raises: any
    sender writes these headers, so one Jarvis can't read is an empty ListHeader (nothing offered)."""
    try:
        return _read_headers(headers)
    except Exception as exc:  # noqa: BLE001 - a hostile or broken header offers nothing
        logger.info("Unsubscribe: a List-Unsubscribe header could not be read (%s)", type(exc).__name__)
        return ListHeader()


def _read_headers(headers: Any) -> ListHeader:
    raw = _header(headers, "list-unsubscribe")
    https, mailto = "", None
    for entry in _ENTRY_RE.findall(raw)[:6]:
        target = "".join(entry.split())   # folded header lines may split it
        scheme = target.split(":", 1)[0].casefold()
        if scheme == "https" and not https and len(target) <= URL_CAP:
            https = target
        elif scheme == "mailto" and mailto is None:
            mailto = _mailto(target)
    post = _header(headers, "list-unsubscribe-post").strip()
    list_raw = _header(headers, "list-id")
    found = _LIST_ID_RE.search(list_raw)
    list_id = found.group(1).casefold() if found else ""
    name = _clean(list_raw[:found.start()] if found else "", 80).strip('"').strip()
    sender = email_address(_header(headers, "from"))
    return ListHeader(https=https, mailto=mailto, one_click=bool(https) and post == ONE_CLICK_VALUE,
                      verified=dmarc_passed(headers), list_id=list_id, name=name, sender=sender)


def url_problem(url: str) -> str:
    """"" when ``url`` may take the one-click POST: https only, a host that is a public name (never this PC, its
    network or a bare address that is not public), no user name or password, no port but 443."""
    text = str(url or "")
    if not text or len(text) > URL_CAP or any(char.isspace() or not char.isprintable() for char in text):
        return REASON_REFUSED
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return REASON_REFUSED
    if parts.scheme != "https" or not parts.hostname:
        return REASON_REFUSED
    if parts.username is not None or parts.password is not None or port not in (None, 443):
        return REASON_REFUSED
    if public_host_problem(parts.hostname.casefold()):
        return REASON_REFUSED
    return ""


def choose(header: ListHeader) -> str:
    """ONE_CLICK, MAILTO or PAGE, in that order of preference, or why none (NOT_VERIFIED, NO_HEADER, NO_METHOD)."""
    if not header.present:
        return NO_HEADER
    if not header.verified:
        return NOT_VERIFIED
    if header.https and header.one_click and not url_problem(header.https):
        return ONE_CLICK
    if header.mailto is not None:
        return MAILTO
    if header.https and not url_problem(header.https):
        return PAGE
    return NO_METHOD


@dataclass(frozen=True)
class OneClick:
    outcome: str            # DONE / FAILED / UNKNOWN
    status: int = 0         # the HTTP status (0: none came)
    reason: str = ""        # REASON_* when not DONE
    ms: int = 0


Poster = Callable[[str, float, int], int]


CONNECT_S = 5.0     # at most this of the whole TIMEOUT_S goes to connecting


def post_timeouts(left: float) -> tuple[float, float]:
    """requests' (connect, read) timeouts out of the ``left`` seconds of the whole one-click: connecting takes at most
    CONNECT_S of them and reading the rest, so the two together never pass ``left`` (each alone would allow it twice)."""
    left = max(0.2, left)
    connect = min(CONNECT_S, left / 2)
    return connect, left - connect


def real_post(url: str, timeout: float, cap: int) -> int:
    """ONE POST of ONE_CLICK_VALUE to ``url`` (a requests Session with trust_env off: no proxy, .netrc or certificate
    settings from the environment; no cookies; redirects never followed), at most ``cap`` bytes of the answer read
    within ``timeout`` seconds in all (post_timeouts). The status. Raises NetRefusedInTests while the tests run,
    TimeoutError when the answer is still coming at the deadline (the POST went: unknown), and whatever requests
    raises."""
    if os.environ.get(TEST_GUARD_ENV, "") == "1":
        raise NetRefusedInTests("no real network in the tests")
    import requests

    deadline = time.monotonic() + timeout
    with requests.Session() as session:
        session.trust_env = False
        session.cookies.clear()
        with session.post(url, data=ONE_CLICK_VALUE, allow_redirects=False, stream=True,
                          timeout=post_timeouts(timeout),
                          headers={"Content-Type": CONTENT_TYPE, "User-Agent": USER_AGENT}) as response:
            read = 0
            for chunk in response.iter_content(8192):
                read += len(chunk)
                if read > cap:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("the answer took too long")
            return int(response.status_code)


def one_click(url: str, *, post: Poster = real_post, resolve: Callable[[str], list[str]] = real_resolve,
              monotonic: Callable[[], float] = time.monotonic) -> OneClick:
    """The one POST (see the module docs); never raises, never retried."""
    started = monotonic()

    def done(outcome: str, status: int = 0, reason: str = "") -> OneClick:
        ms = int((monotonic() - started) * 1000)
        logger.info("Unsubscribe: one-click %s (HTTP %d, %s, %d ms)", outcome, status, reason or "ok", ms)
        return OneClick(outcome, status, reason, ms)

    if url_problem(url):
        return done(FAILED, reason=REASON_REFUSED)
    try:
        addresses = list(resolve(urlsplit(url).hostname or ""))
    except Exception as exc:  # noqa: BLE001 - not resolved: nothing was sent
        logger.info("Unsubscribe: the host was not resolved (%s)", type(exc).__name__)
        return done(FAILED, reason=REASON_REFUSED)
    if address_problem(addresses):
        return done(FAILED, reason=REASON_REFUSED)
    left = TIMEOUT_S - (monotonic() - started)
    if left <= 0:
        return done(FAILED, reason=REASON_TIMEOUT)
    try:
        status = int(post(url, left, READ_CAP))
    except NetRefusedInTests:
        return done(FAILED, reason=REASON_REFUSED)
    except Exception as exc:  # noqa: BLE001 - the POST may have gone: unknown, never retried
        logger.info("Unsubscribe: no clear answer (%s)", type(exc).__name__)
        return done(UNKNOWN, reason=REASON_TIMEOUT)
    if 200 <= status < 300:
        return done(DONE, status)
    if 300 <= status < 400:
        return done(FAILED, status, REASON_REDIRECT)
    if 400 <= status < 500:
        return done(FAILED, status, REASON_CLIENT)
    return done(UNKNOWN, status, REASON_SERVER)


def headers_of(message: Mapping[str, Any]) -> list[dict[str, str]]:
    """The headers list of a Gmail users.messages.get answer ([] when it has none)."""
    payload = message.get("payload") if isinstance(message, Mapping) else None
    headers = payload.get("headers") if isinstance(payload, Mapping) else None
    return [entry for entry in headers if isinstance(entry, dict)] if isinstance(headers, list) else []


def mailto_words(header: ListHeader) -> Sequence[str]:
    """(subject, body) for the list's address: the header's own, else Jarvis's plain "Unsubscribe"."""
    mailto = header.mailto
    if mailto is None:
        return ("", "")
    return (mailto.subject or "Unsubscribe", mailto.body or "Unsubscribe")
