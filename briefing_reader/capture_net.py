"""Fetching a calendar link once, on the owner's "Read it" click (no Qt): a one-time import, never a subscription.

    link_host(url) -> str        the host a webcal / https link names ("" when it is not one Jarvis would fetch):
                                 what the tray's chip names before the click
    fetch_calendar(url, *, get=real_get, resolve=real_resolve, monotonic=)
                                 -> Fetched(content, problem, host): webcal:// is read as https://, only https, only a
                                 public host on every hop - its name (actions.public_host_problem) AND every address it
                                 resolves to (``resolve``: a public, global address only; a public name that leads to
                                 this PC or its network is refused) - no user name or password in the link, no port but
                                 443, at most MAX_REDIRECTS redirects, at most capture.MAX_FILE_BYTES read (streamed),
                                 TIMEOUT_S for the whole fetch, a plain User-Agent and nothing else (no cookies, no
                                 sign-in, no proxy or .netrc from the environment)
    real_get(url, timeout, cap) -> (status, location, body)
                                 one GET without following redirects (a requests Session with trust_env off, its
                                 reads stopped at the deadline); refuses while the tests run
    real_resolve(host) -> addresses
                                 the host's addresses (socket.getaddrinfo); refuses while the tests run

Logs hold the status, the byte count, the hop count and the duration only: never the link, its host or the content.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from .actions import public_host_problem
from .capture import MAX_FILE_BYTES

logger = logging.getLogger(__name__)

TIMEOUT_S = 10.0
MAX_REDIRECTS = 3
USER_AGENT = "JarvisAssistant/calendar-import"
TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"
NOT_HTTPS, NOT_PUBLIC, TOO_MANY_REDIRECTS, TOO_LARGE, FAILED, UNREACHABLE = (
    "not_https", "not_public", "too_many_redirects", "too_large", "failed", "unreachable")
PROBLEM_WORDS = {
    NOT_HTTPS: "Jarvis fetches only https and webcal calendar links",
    NOT_PUBLIC: "Jarvis fetches only from public web hosts",
    TOO_MANY_REDIRECTS: "The link sent Jarvis on too many times",
    TOO_LARGE: "The calendar is over 2 MB",
    FAILED: "The calendar's site did not send it",
    UNREACHABLE: "Jarvis couldn't reach the calendar's site",
}
_REDIRECTS = frozenset({301, 302, 303, 307, 308})

Getter = Callable[[str, float, int], "tuple[int, str, bytes]"]


class NetRefusedInTests(RuntimeError):
    """The real network was asked for while the tests run."""


@dataclass(frozen=True)
class Fetched:
    content: bytes | None
    problem: str = ""
    host: str = ""


def _checked(url: str) -> tuple[str, str, str]:
    """(the https URL, its host, a problem)."""
    text = str(url or "").strip()
    if text[:9].casefold() == "webcal://":
        text = "https://" + text[9:]
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return "", "", NOT_HTTPS
    if parts.scheme.casefold() != "https" or not parts.hostname:
        return "", "", NOT_HTTPS
    host = parts.hostname.casefold()
    if parts.username is not None or parts.password is not None or port not in (None, 443):
        return "", host, NOT_PUBLIC
    if public_host_problem(host):
        return "", host, NOT_PUBLIC
    return text, host, ""


def link_host(url: str) -> str:
    """The host a calendar link names, when Jarvis would fetch it ("" otherwise)."""
    _, host, problem = _checked(url)
    return host if not problem else ""


class Deadline(TimeoutError):
    """The whole fetch took longer than TIMEOUT_S."""


def real_resolve(host: str) -> list[str]:
    """Every address ``host`` resolves to. Raises NetRefusedInTests while the tests run, and OSError."""
    if os.environ.get(TEST_GUARD_ENV, "") == "1":
        raise NetRefusedInTests("no real network in the tests")
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def address_problem(addresses: list[str]) -> str:
    """NOT_PUBLIC when any address is not a public, global one (loopback, private, link-local ...); "" when all are."""
    if not addresses:
        return NOT_PUBLIC
    for text in addresses:
        try:
            if not ipaddress.ip_address(text.split("%", 1)[0]).is_global:
                return NOT_PUBLIC
        except ValueError:
            return NOT_PUBLIC
    return ""


def real_get(url: str, timeout: float, cap: int) -> tuple[int, str, bytes]:
    """One GET of ``url`` without following redirects: (status, Location, at most ``cap`` + 1 bytes of the body), the
    body read until ``timeout`` seconds after the start at most (Deadline). A Session with trust_env off: no proxy,
    .netrc or certificate settings from the environment. Raises NetRefusedInTests while the tests run, and whatever
    requests raises."""
    if os.environ.get(TEST_GUARD_ENV, "") == "1":
        raise NetRefusedInTests("no real network in the tests")
    import requests

    deadline = time.monotonic() + timeout
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, timeout=timeout, allow_redirects=False, stream=True,
                         headers={"User-Agent": USER_AGENT, "Accept": "text/calendar, */*;q=0.5"}) as response:
            location = str(response.headers.get("Location") or "")
            body = bytearray()
            if response.status_code == 200:
                for chunk in response.iter_content(65536):
                    body += chunk
                    if len(body) > cap:
                        break
                    if time.monotonic() > deadline:
                        raise Deadline("the calendar took too long")
            return response.status_code, location, bytes(body)


def fetch_calendar(url: str, *, get: Getter = real_get, resolve: Callable[[str], list[str]] = real_resolve,
                   monotonic: Callable[[], float] = time.monotonic) -> Fetched:
    """The calendar at ``url`` (see the module's docstring); never raises."""
    started = monotonic()
    target, host, problem = _checked(url)
    first_host = host
    if problem:
        logger.info("Capture: calendar link refused (%s)", problem)
        return Fetched(None, problem, first_host)
    hops = 0
    while True:
        try:
            problem = address_problem(list(resolve(urlsplit(target).hostname or "")))
        except Exception as exc:  # noqa: BLE001 - not resolved: nothing was fetched
            logger.info("Capture: calendar host not resolved (%s)", type(exc).__name__)
            return Fetched(None, UNREACHABLE, first_host)
        if problem:
            logger.info("Capture: calendar host refused (%s, by its address)", problem)
            return Fetched(None, problem, first_host)
        left = TIMEOUT_S - (monotonic() - started)
        if left <= 0:
            logger.info("Capture: calendar fetch failed (Deadline, %d hop(s))", hops)
            return Fetched(None, UNREACHABLE, first_host)
        try:
            status, location, body = get(target, left, MAX_FILE_BYTES)
        except Exception as exc:  # noqa: BLE001 - no network, a timeout, a refusal: nothing was read
            logger.info("Capture: calendar fetch failed (%s, %d hop(s), %d ms)", type(exc).__name__, hops,
                        int((monotonic() - started) * 1000))
            return Fetched(None, UNREACHABLE, first_host)
        if status in _REDIRECTS and location:
            hops += 1
            if hops > MAX_REDIRECTS:
                logger.info("Capture: calendar fetch refused (%s)", TOO_MANY_REDIRECTS)
                return Fetched(None, TOO_MANY_REDIRECTS, first_host)
            target, _, problem = _checked(urljoin(target, location))
            if problem:
                logger.info("Capture: calendar redirect refused (%s)", problem)
                return Fetched(None, problem, first_host)
            continue
        elapsed = int((monotonic() - started) * 1000)
        if status != 200:
            logger.info("Capture: calendar fetch answered %d (%d hop(s), %d ms)", status, hops, elapsed)
            return Fetched(None, FAILED, first_host)
        if len(body) > MAX_FILE_BYTES:
            logger.info("Capture: calendar fetch refused (%s, %d ms)", TOO_LARGE, elapsed)
            return Fetched(None, TOO_LARGE, first_host)
        logger.info("Capture: calendar fetched (status %d, %d bytes, %d hop(s), %d ms)", status, len(body), hops,
                    elapsed)
        return Fetched(bytes(body), "", first_host)
