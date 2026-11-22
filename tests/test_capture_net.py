"""Fetching a calendar link (part B): https only, public hosts on every hop, redirects, size cap; faked, no network."""

from __future__ import annotations

import logging
import unittest
from pathlib import Path

from briefing_reader import capture, capture_net
from briefing_reader.capture_net import (FAILED, NOT_HTTPS, NOT_PUBLIC, TOO_LARGE, TOO_MANY_REDIRECTS, UNREACHABLE,
                                         fetch_calendar, link_host)

PUBLIC = lambda _host: ["93.184.216.34"]  # noqa: E731 - every host resolves to a public address
BODY = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"


class FakeGet:
    def __init__(self, answers):
        self.answers = list(answers)
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float, cap: int):
        self.urls.append(url)
        self.timeout, self.cap = timeout, cap
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class NetTests(unittest.TestCase):
    def test_webcal_is_read_as_https(self) -> None:
        get = FakeGet([(200, "", BODY)])
        fetched = fetch_calendar("webcal://calendar.example.org/term.ics", get=get, resolve=PUBLIC,
                                 monotonic=lambda: 0.0)
        self.assertEqual(fetched.content, BODY)
        self.assertEqual(fetched.host, "calendar.example.org")
        self.assertEqual(get.urls, ["https://calendar.example.org/term.ics"])
        self.assertEqual((get.timeout, get.cap), (10.0, capture.MAX_FILE_BYTES))

    def test_refused_links_are_never_fetched(self) -> None:
        cases = {"http://calendar.example.org/x.ics": NOT_HTTPS, "ftp://example.org/x.ics": NOT_HTTPS,
                 "https://localhost/x.ics": NOT_PUBLIC, "https://192.168.1.5/x.ics": NOT_PUBLIC,
                 "https://intranet/x.ics": NOT_PUBLIC, "https://user:pw@example.org/x.ics": NOT_PUBLIC,
                 "https://example.org:8443/x.ics": NOT_PUBLIC, "https://printer.local/x.ics": NOT_PUBLIC,
                 "not a link": NOT_HTTPS, "file:///C:/x.ics": NOT_HTTPS}
        for url, problem in cases.items():
            with self.subTest(url=url):
                get = FakeGet([])
                self.assertEqual(fetch_calendar(url, get=get, resolve=PUBLIC).problem, problem)
                self.assertEqual(get.urls, [])
                self.assertEqual(link_host(url), "")
        self.assertEqual(link_host("webcal://Cal.Example.org/a.ics"), "cal.example.org")

    def test_every_redirect_is_checked(self) -> None:
        get = FakeGet([(302, "https://cdn.example.net/term.ics", b""), (301, "/final.ics", b""), (200, "", BODY)])
        self.assertEqual(fetch_calendar("https://example.org/t.ics", get=get, resolve=PUBLIC).content, BODY)
        self.assertEqual(get.urls, ["https://example.org/t.ics", "https://cdn.example.net/term.ics",
                                    "https://cdn.example.net/final.ics"])
        for location in ("http://example.org/x.ics", "https://127.0.0.1/x.ics", "https://router.lan/x.ics"):
            with self.subTest(location=location):
                get = FakeGet([(302, location, b"")])
                fetched = fetch_calendar("https://example.org/t.ics", get=get, resolve=PUBLIC)
                self.assertIsNone(fetched.content)
                self.assertIn(fetched.problem, (NOT_HTTPS, NOT_PUBLIC))
                self.assertEqual(len(get.urls), 1)

    def test_at_most_three_redirects(self) -> None:
        get = FakeGet([(302, f"https://example.org/{n}", b"") for n in range(4)])
        self.assertEqual(fetch_calendar("https://example.org/t.ics", get=get, resolve=PUBLIC).problem, TOO_MANY_REDIRECTS)
        self.assertEqual(len(get.urls), 4)

    def test_failures(self) -> None:
        self.assertEqual(fetch_calendar("https://example.org/t", resolve=PUBLIC, get=FakeGet([(404, "", b"")])).problem, FAILED)
        self.assertEqual(fetch_calendar("https://example.org/t", resolve=PUBLIC, get=FakeGet([OSError("down")])).problem, UNREACHABLE)
        big = b"x" * (capture.MAX_FILE_BYTES + 1)
        self.assertEqual(fetch_calendar("https://example.org/t", resolve=PUBLIC, get=FakeGet([(200, "", big)])).problem, TOO_LARGE)

    def test_real_get_refuses_under_the_test_guard(self) -> None:
        with self.assertRaises(capture_net.NetRefusedInTests):
            capture_net.real_get("https://example.org/t.ics", 1.0, 10)
        self.assertEqual(fetch_calendar("https://example.org/t.ics").problem, UNREACHABLE)

    def test_logs_hold_no_link_or_host(self) -> None:
        with self.assertLogs(capture_net.logger, logging.INFO) as logs:
            fetch_calendar("webcal://secret-school.example.org/private-term.ics", resolve=PUBLIC, get=FakeGet([(200, "", BODY)]))
            fetch_calendar("https://secret-school.example.org/x", resolve=PUBLIC, get=FakeGet([(500, "", b"")]))
            fetch_calendar("https://localhost/secret", resolve=PUBLIC, get=FakeGet([]))
        text = "\n".join(logs.output)
        self.assertNotIn("secret", text)
        self.assertNotIn("example", text)
        self.assertIn("bytes", text)


class AddressTests(unittest.TestCase):
    """Review item 10: the host's addresses are checked on every hop; the whole fetch has one deadline."""

    def test_a_public_name_that_leads_to_this_pc_or_its_network_is_refused(self) -> None:
        for addresses in (["192.168.1.20"], ["127.0.0.1"], ["93.184.216.34", "10.0.0.5"], ["fe80::1%3"], [],
                          ["not an address"]):
            with self.subTest(addresses=addresses):
                get = FakeGet([(200, "", BODY)])
                fetched = fetch_calendar("https://calendar.example.org/t.ics", get=get,
                                         resolve=lambda _host, a=addresses: a)
                self.assertEqual(fetched.problem, NOT_PUBLIC)
                self.assertEqual(get.urls, [])
        hosts: list[str] = []

        def resolve(host: str) -> list[str]:
            hosts.append(host)
            return ["127.0.0.1"] if host == "cdn.example.net" else ["93.184.216.34"]

        get = FakeGet([(302, "https://cdn.example.net/t.ics", b"")])
        self.assertEqual(fetch_calendar("https://example.org/t.ics", get=get, resolve=resolve).problem, NOT_PUBLIC)
        self.assertEqual(hosts, ["example.org", "cdn.example.net"])
        self.assertEqual(len(get.urls), 1)

    def test_one_deadline_for_the_whole_fetch(self) -> None:
        clock = iter([0.0, 4.0, 11.0, 11.0])
        get = FakeGet([(302, "https://example.org/b.ics", b""), (200, "", BODY)])
        fetched = fetch_calendar("https://example.org/a.ics", get=get, resolve=PUBLIC, monotonic=lambda: next(clock))
        self.assertEqual(fetched.problem, UNREACHABLE)
        self.assertEqual(len(get.urls), 1)
        self.assertEqual(get.timeout, 6.0)                       # what was left of the 10 s for the first hop

    def test_resolving_refuses_under_the_test_guard(self) -> None:
        with self.assertRaises(capture_net.NetRefusedInTests):
            capture_net.real_resolve("example.org")
        source = Path(capture_net.__file__).read_text(encoding="utf-8")
        self.assertIn("session.trust_env = False", source)       # no proxy or .netrc from the environment


if __name__ == "__main__":
    unittest.main()
