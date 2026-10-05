"""Tests for briefing_reader.notion_client: fixture session, flattener, header parsing,
freshness, polling and the HTTP client's retry/auth behaviour. No network access."""

from __future__ import annotations

import itertools
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from briefing_reader import notion_client as nc
from briefing_reader.models import (
    BULLETED,
    CALLOUT,
    CODE,
    DIVIDER,
    HEADING,
    NUMBERED,
    OTHER,
    PARAGRAPH,
    QUOTE,
    TABLE_ROW,
    TO_DO,
    TOGGLE,
    Briefing,
    BriefingHeader,
    FlatLine,
)
from briefing_reader.notion_client import (
    FixtureSession,
    NotionAuthError,
    NotionClient,
    NotionConfigError,
    NotionError,
    check_freshness,
    fetch_briefing,
    flatten_blocks,
    parse_header,
    poll_for_briefing,
    render_plain_text,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_PAGE = FIXTURES / "fake_page.json"
PLACEHOLDER_PAGE = FIXTURES / "placeholder_page.json"
PAGE_ID = "00112233445566778899aabbccddeeff"

# Fixed zones so nothing depends on the machine's time zone.
PDT = timezone(timedelta(hours=-7), "PDT")
UTC = timezone.utc
NOW = datetime(2026, 10, 4, 10, 30, tzinfo=PDT)

FAKE_TOKEN = "secret_" + "a1B2c3D4e5" * 4  # looks like a real token; never a real one

DASH = "—"
THUMBS_UP = "\U0001F44D"
WASTEBASKET = "\U0001F5D1️"

# (kind, text, depth, level, number, checked) for the whole fake page.
EXPECTED_FAKE_PAGE = [
    (PARAGRAPH, "Updated: 2026-10-04 10:04 PDT", 0, None, None, None),
    (PARAGRAPH, "Run: AM", 0, None, None, None),
    (PARAGRAPH, "**12 new** since last run, 3 need action.", 0, None, None, None),
    (DIVIDER, "", 0, None, None, None),
    (HEADING, "Work inbox (3)", 0, 2, None, None),
    (BULLETED, f"**Alice Chen** {DASH} Q3 budget review {DASH} wants numbers by Fri", 0, None, None, None),
    (BULLETED, "Attached: [budget.xlsx](https://example.com/budget.xlsx)", 1, None, None, None),
    (BULLETED, "cc: Bob", 1, None, None, None),
    (BULLETED, f"Bob already replied {THUMBS_UP}", 2, None, None, None),
    (BULLETED, "Jira: PROJ-142 assigned to you -> please triage", 0, None, None, None),
    (BULLETED, "Calendar invite: Design sync, Tue 2pm | Zoom https://zoom.us/j/123", 0, None, None, None),
    (HEADING, "Due", 0, 2, None, None),
    (TO_DO, "Renew parking permit (expires **Oct 6**)", 0, None, None, False),
    (TO_DO, "Submit expense report", 0, None, None, True),
    (NUMBERED, f"Pay electricity bill {DASH} $84.20", 0, None, 1, None),
    (NUMBERED, "Reply to landlord re: lease", 0, None, 2, None),
    (NUMBERED, "Mention the leak", 1, None, 1, None),
    (NUMBERED, "Ask about parking", 1, None, 2, None),
    (HEADING, "Actionable", 0, 2, None, None),
    (CALLOUT, "Security alert: new sign-in from Chrome on Windows", 0, None, None, None),
    (PARAGRAPH, "If this wasn't you, reset your password.", 1, None, None, None),
    (QUOTE, f"\"Can you send the slides before Monday?\" {DASH} Dave", 0, None, None, None),
    (TOGGLE, "Details", 0, None, None, None),
    (BULLETED, "Slides are in the Q3 folder", 1, None, None, None),
    (HEADING, "Follow-ups", 0, 3, None, None),
    (TABLE_ROW, "Who | What", 0, None, None, None),
    (TABLE_ROW, "Sam | Contract draft", 0, None, None, None),
    (PARAGRAPH, "Left column note", 0, None, None, None),
    (PARAGRAPH, "Right column note", 0, None, None, None),
    (CODE, "git pull --rebase", 0, None, None, None),
    (OTHER, "Screenshot of the alert", 0, None, None, None),
    (DIVIDER, "", 0, None, None, None),
    (HEADING, "Newsletters", 0, 2, None, None),
    (BULLETED, f"Morning Brew {DASH} markets up, oil down", 0, None, None, None),
    (BULLETED, "TLDR AI: _three_ new model releases", 0, None, None, None),
    (HEADING, f"{WASTEBASKET} Ignore (14)", 0, 2, None, None),
    (BULLETED, "Promo: 20% off sneakers", 0, None, None, None),
    (BULLETED, "LinkedIn: 5 people viewed your profile", 0, None, None, None),
    (PARAGRAPH, "Weekly digest from Medium", 0, None, None, None),
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_ids = itertools.count(1)


def rich(text: str) -> list[dict]:
    return styled((text, False))


def styled(*parts: tuple[str, bool]) -> list[dict]:
    """Rich text from (text, bold) pieces."""
    return [{"type": "text", "text": {"content": text, "link": None},
             "annotations": {"bold": bold, "italic": False, "strikethrough": False,
                             "underline": False, "code": False, "color": "default"},
             "plain_text": text, "href": None} for text, bold in parts]


def blk(btype: str, text: str | None = None, children: list[dict] | None = None,
        **payload: Any) -> dict:
    """An inline block already in fetch_block_tree shape (children under "_children")."""
    body = dict(payload)
    if text is not None:
        body["rich_text"] = rich(text)
    block = {"object": "block", "id": f"block-{next(_ids)}", "type": btype, btype: body,
             "has_children": bool(children)}
    if children is not None:
        block["_children"] = children
    return block


def as_tuples(lines: list[FlatLine]) -> list[tuple]:
    return [(l.kind, l.text, l.depth, l.level, l.number, l.checked) for l in lines]


def load_fake_tree() -> tuple[FixtureSession, list[dict]]:
    session = FixtureSession(FAKE_PAGE)
    client = NotionClient("test-token", session=session, sleep=lambda s: None)
    return session, client.fetch_block_tree(PAGE_ID)


def header_line(text: str) -> FlatLine:
    return FlatLine(PARAGRAPH, text)


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None,
                 headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def list_page(results: list[dict], next_cursor: str | None = None) -> FakeResponse:
    return FakeResponse(200, {"object": "list", "results": results, "next_cursor": next_cursor,
                              "has_more": next_cursor is not None, "type": "block", "block": {}})


def error_page(status: int, message: str, headers: dict[str, str] | None = None) -> FakeResponse:
    return FakeResponse(status, {"object": "error", "status": status, "code": "x",
                                 "message": message}, headers)


class ScriptedSession:
    """Returns (or raises) the scripted items in order; records every call."""

    def __init__(self, *items: Any) -> None:
        self.items = list(items)
        self.calls: list[dict] = []

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            timeout: float | None = None) -> FakeResponse:
        self.calls.append({"url": url, "params": dict(params or {}),
                           "headers": dict(headers or {}), "timeout": timeout})
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class TreeSession:
    """Serves single-page children lists from a dict keyed by block id."""

    def __init__(self, children: dict[str, list[dict]]) -> None:
        self.children = children
        self.requested: list[str] = []

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            timeout: float | None = None) -> FakeResponse:
        block_id = url.split("/blocks/")[1].split("/")[0]
        self.requested.append(block_id)
        if block_id not in self.children:
            return error_page(404, "not found")
        return list_page(self.children[block_id])


class EndlessSession:
    """Always has another page (for the request cap)."""

    def __init__(self) -> None:
        self.count = 0

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            timeout: float | None = None) -> FakeResponse:
        self.count += 1
        return list_page([], next_cursor=f"cursor-{self.count}")


def make_client(session: Any, token: str = FAKE_TOKEN) -> tuple[NotionClient, list[float]]:
    sleeps: list[float] = []
    return NotionClient(token, session=session, sleep=sleeps.append), sleeps


# --------------------------------------------------------------------------
# FixtureSession and pagination
# --------------------------------------------------------------------------

class FixtureSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = json.loads(FAKE_PAGE.read_text(encoding="utf-8"))

    def test_top_level_and_nested_pagination_followed(self) -> None:
        session, tree = load_fake_tree()
        top_pages = self.raw["responses"][PAGE_ID]
        self.assertGreaterEqual(len(top_pages), 3)
        self.assertEqual(len(tree), sum(len(p["results"]) for p in top_pages))
        self.assertEqual(len(tree), 32)

        page_calls = [c for c in session.calls if c[0] == PAGE_ID]
        self.assertEqual(page_calls, [(PAGE_ID, None)]
                         + [(PAGE_ID, p["next_cursor"]) for p in top_pages[:-1]])
        # Top-level pages are all fetched before any children.
        self.assertEqual(session.calls[:len(top_pages)], page_calls)

        alice = tree[5]
        alice_key = alice["id"]
        alice_id = alice_key.replace("-", "")
        alice_pages = self.raw["responses"][alice_key]
        self.assertEqual(len(alice_pages), 2)
        alice_calls = [c for c in session.calls if c[0] == alice_id]
        self.assertEqual(alice_calls, [(alice_id, None), (alice_id, alice_pages[0]["next_cursor"])])
        self.assertEqual([c["id"] for c in alice["_children"]],
                         [b["id"] for p in alice_pages for b in p["results"]])

        # Every saved response list was requested exactly once per page.
        expected_calls = sum(len(pages) for pages in self.raw["responses"].values())
        self.assertEqual(len(session.calls), expected_calls)
        self.assertEqual(len(set(session.calls)), expected_calls)

    def test_children_stored_on_shallow_copies(self) -> None:
        session, tree = load_fake_tree()
        with_children = [b for b in tree if b.get("has_children")]
        self.assertTrue(all("_children" in b for b in with_children))
        self.assertFalse(any("_children" in b for b in tree if not b.get("has_children")))
        # The fixture's own data is never mutated.
        for pages in session._responses.values():
            for page in pages:
                self.assertFalse(any("_children" in b for b in page["results"]))

    def test_dashed_ids_match_and_page_id_exposed(self) -> None:
        session = FixtureSession(FAKE_PAGE)
        self.assertEqual(session.page_id, PAGE_ID)
        client = NotionClient("test-token", session=session)
        dashed = "00112233-4455-6677-8899-aabbccddeeff"
        blocks = client.list_children(dashed)
        self.assertEqual(len(blocks), 32)
        self.assertEqual(session.calls[0], (PAGE_ID, None))

    def test_unknown_block_is_404(self) -> None:
        session = FixtureSession(FAKE_PAGE)
        response = session.get(f"{nc.NOTION_API}/blocks/00000000000000000000000000000000/children",
                               params={"page_size": 100})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "object_not_found")
        client = NotionClient("test-token", session=session)
        with self.assertRaises(NotionAuthError):
            client.list_children("00000000-0000-0000-0000-000000000000")

    def test_bad_cursor_is_400(self) -> None:
        session = FixtureSession(FAKE_PAGE)
        response = session.get(f"{nc.NOTION_API}/blocks/{PAGE_ID}/children",
                               params={"page_size": 100, "start_cursor": "nope"})
        self.assertEqual(response.status_code, 400)

    def test_fixture_needs_no_token(self) -> None:
        client = NotionClient("", session=FixtureSession(PLACEHOLDER_PAGE))
        self.assertEqual(len(client.fetch_block_tree(PAGE_ID)), 2)


# --------------------------------------------------------------------------
# Flattening
# --------------------------------------------------------------------------

class FlattenTests(unittest.TestCase):
    def test_fake_page_flattens_to_expected_lines(self) -> None:
        _, tree = load_fake_tree()
        lines = flatten_blocks(tree)
        self.assertEqual(as_tuples(lines), EXPECTED_FAKE_PAGE)

    def test_block_ids_are_carried(self) -> None:
        _, tree = load_fake_tree()
        lines = flatten_blocks(tree)
        self.assertTrue(all(line.block_id for line in lines))
        self.assertEqual(lines[0].block_id, tree[0]["id"])
        work_inbox = next(line for line in lines if line.text == "Work inbox (3)")
        self.assertEqual(work_inbox.block_id, tree[4]["id"])

    def test_numbering_resets_for_nested_runs_and_after_interruption(self) -> None:
        tree = [
            blk("numbered_list_item", "a"),
            blk("numbered_list_item", "b", children=[
                blk("numbered_list_item", "b1"),
                blk("numbered_list_item", "b2"),
                blk("numbered_list_item", "b3"),
            ]),
            blk("numbered_list_item", "c"),
            blk("paragraph", "interruption"),
            blk("numbered_list_item", "d"),
            blk("numbered_list_item", "e"),
            blk("bulleted_list_item", "bullet"),
            blk("numbered_list_item", "f", list_start_index=5),
            blk("numbered_list_item", "g"),
        ]
        numbered = [(l.text, l.depth, l.number) for l in flatten_blocks(tree) if l.kind == NUMBERED]
        self.assertEqual(numbered, [
            ("a", 0, 1), ("b", 0, 2), ("b1", 1, 1), ("b2", 1, 2), ("b3", 1, 3), ("c", 0, 3),
            ("d", 0, 1), ("e", 0, 2), ("f", 0, 5), ("g", 0, 6),
        ])

    def test_dropped_empty_paragraph_still_breaks_a_numbered_run(self) -> None:
        tree = [blk("numbered_list_item", "a"), blk("paragraph", ""), blk("numbered_list_item", "b")]
        self.assertEqual([l.number for l in flatten_blocks(tree)], [1, 1])

    def test_paragraphs(self) -> None:
        tree = [
            blk("paragraph", ""),
            blk("paragraph", "   "),
            blk("paragraph", "first line\n\n  second line  \n"),
            blk("paragraph", "parent", children=[blk("paragraph", "child")]),
        ]
        self.assertEqual(as_tuples(flatten_blocks(tree)), [
            (PARAGRAPH, "first line", 0, None, None, None),
            (PARAGRAPH, "second line", 0, None, None, None),
            (PARAGRAPH, "parent", 0, None, None, None),
            (PARAGRAPH, "child", 1, None, None, None),
        ])

    def test_paragraph_bold_as_a_whole_is_marked_bold(self) -> None:
        tree = [
            blk("paragraph", rich_text=styled(("Work inbox", True))),
            blk("paragraph", rich_text=styled(("Ignore ", True), ("(2)", True), ("  ", False))),
            blk("paragraph", rich_text=styled(("Due\nActionable", True))),
            blk("paragraph", rich_text=styled(("**Newsletters**", True))),
            blk("paragraph", rich_text=styled(("Alice", True), (" asked about Q3", False))),
            blk("paragraph", rich_text=styled(("   ", True))),
            blk("bulleted_list_item", rich_text=styled(("Bold bullet", True))),
            blk("toggle", rich_text=styled(("Bold toggle", True))),
        ]
        self.assertEqual([(l.kind, l.text) for l in flatten_blocks(tree)], [
            (PARAGRAPH, "**Work inbox**"),
            (PARAGRAPH, "**Ignore (2)**"),
            (PARAGRAPH, "**Due**"),
            (PARAGRAPH, "**Actionable**"),
            (PARAGRAPH, "**Newsletters**"),
            (PARAGRAPH, "Alice asked about Q3"),
            (BULLETED, "Bold bullet"),
            (TOGGLE, "Bold toggle"),
        ])

    def test_bold_header_lines_still_parse(self) -> None:
        tree = [blk("paragraph", rich_text=styled(("Updated: 2026-10-04 10:04 PDT", True))),
                blk("paragraph", rich_text=styled(("Run: AM", True))),
                blk("paragraph", "Body")]
        header, body = parse_header(flatten_blocks(tree), local_tz=PDT)
        self.assertEqual((header.updated_raw, header.run), ("2026-10-04 10:04 PDT", "AM"))
        self.assertEqual(header.updated_at, datetime(2026, 10, 4, 10, 4, tzinfo=PDT))
        self.assertEqual([l.text for l in body], ["Body"])

    def test_containers_flatten_children_at_same_depth(self) -> None:
        tree = [
            blk("column_list", children=[
                blk("column", children=[blk("paragraph", "left")]),
                blk("column", children=[blk("bulleted_list_item", "right")]),
            ]),
            blk("bulleted_list_item", "outer", children=[
                blk("synced_block", synced_from=None, children=[blk("paragraph", "synced")]),
            ]),
        ]
        self.assertEqual([(l.text, l.depth) for l in flatten_blocks(tree)],
                         [("left", 0), ("right", 0), ("outer", 0), ("synced", 1)])

    def test_toggleable_heading_children_stay_at_heading_depth(self) -> None:
        tree = [blk("heading_1", "Big\nHeading", is_toggleable=True,
                    children=[blk("bulleted_list_item", "inside")])]
        self.assertEqual(as_tuples(flatten_blocks(tree)), [
            (HEADING, "Big Heading", 0, 1, None, None),
            (BULLETED, "inside", 0, None, None, None),
        ])

    def test_child_page_and_database_are_titles_only(self) -> None:
        tree = [
            blk("child_page", title="Archive", children=[blk("paragraph", "must not appear")]),
            blk("child_database", title="Tasks DB"),
        ]
        self.assertEqual(as_tuples(flatten_blocks(tree)), [
            (OTHER, "Archive", 0, None, None, None),
            (OTHER, "Tasks DB", 0, None, None, None),
        ])

    def test_media_equation_and_unknown_blocks(self) -> None:
        tree = [
            blk("bookmark", url="https://example.com/a", caption=[]),
            blk("embed", url="https://example.com/e", caption=rich("Embedded doc")),
            blk("image", caption=[], type="external", external={"url": "https://x/img.png"}),
            blk("video", caption=rich("Demo video")),
            blk("equation", expression="E = mc^2"),
            blk("table_of_contents", color="default"),
            blk("breadcrumb"),
            blk("unsupported"),
            blk("template", "Template text"),
            blk("brand_new_type", children=[blk("paragraph", "kid")]),
            blk("divider", children=None),
            blk("code", "line one\n\n    indented\n", language="python"),
            blk("to_do", "no checked key"),
            blk("table", table_width=3, children=[
                blk("table_row", cells=[rich("A"), [], rich(" C ")]),
                blk("table_row", cells=[[], [], []]),
            ]),
        ]
        self.assertEqual(as_tuples(flatten_blocks(tree)), [
            (OTHER, "https://example.com/a", 0, None, None, None),
            (OTHER, "Embedded doc", 0, None, None, None),
            (OTHER, "Demo video", 0, None, None, None),
            (OTHER, "E = mc^2", 0, None, None, None),
            (OTHER, "Template text", 0, None, None, None),
            (PARAGRAPH, "kid", 1, None, None, None),
            (DIVIDER, "", 0, None, None, None),
            (CODE, "line one", 0, None, None, None),
            (CODE, "    indented", 0, None, None, None),
            (TO_DO, "no checked key", 0, None, None, False),
            (TABLE_ROW, "A | C", 0, None, None, None),
        ])

    def test_quote_and_callout_split_lines_with_children_nested(self) -> None:
        tree = [
            blk("quote", "line 1\nline 2"),
            blk("callout", "Alert", icon={"type": "emoji", "emoji": "!"},
                children=[blk("paragraph", "detail")]),
            blk("toggle", "More\ninfo", children=[blk("bulleted_list_item", "hidden")]),
        ]
        self.assertEqual(as_tuples(flatten_blocks(tree)), [
            (QUOTE, "line 1", 0, None, None, None),
            (QUOTE, "line 2", 0, None, None, None),
            (CALLOUT, "Alert", 0, None, None, None),
            (PARAGRAPH, "detail", 1, None, None, None),
            (TOGGLE, "More info", 0, None, None, None),
            (BULLETED, "hidden", 1, None, None, None),
        ])


class RenderPlainTextTests(unittest.TestCase):
    def test_small_tree(self) -> None:
        tree = [
            blk("heading_1", "Inbox"),
            blk("bulleted_list_item", "a", children=[blk("bulleted_list_item", "b")]),
            blk("numbered_list_item", "one"),
            blk("numbered_list_item", "two"),
            blk("to_do", "done", checked=True),
            blk("to_do", "open", checked=False),
            blk("quote", "q"),
            blk("divider"),
            blk("heading_2", "Next"),
            blk("paragraph", "p"),
            blk("code", "x = 1"),
        ]
        self.assertEqual(render_plain_text(flatten_blocks(tree)), "\n".join([
            "Inbox",
            "- a",
            "  - b",
            "1. one",
            "2. two",
            "[x] done",
            "[ ] open",
            "> q",
            "---",
            "",
            "Next",
            "p",
            "x = 1",
        ]))

    def test_empty(self) -> None:
        self.assertEqual(render_plain_text([]), "")


# --------------------------------------------------------------------------
# Header parsing
# --------------------------------------------------------------------------

class ParseHeaderTests(unittest.TestCase):
    def test_fake_page_header(self) -> None:
        _, tree = load_fake_tree()
        lines = flatten_blocks(tree)
        header, body = parse_header(lines, local_tz=PDT)
        self.assertEqual(header.updated_raw, "2026-10-04 10:04 PDT")
        self.assertEqual(header.updated_at, datetime(2026, 10, 4, 10, 4, tzinfo=PDT))
        self.assertEqual(header.updated_at.utcoffset(), timedelta(hours=-7))
        self.assertEqual(header.run, "AM")
        self.assertEqual(body, lines[2:])
        self.assertEqual(body[0].text, "**12 new** since last run, 3 need action.")

    def test_result_is_converted_to_local_zone(self) -> None:
        header, _ = parse_header([header_line("Updated: 2026-10-04 10:04 PDT")], local_tz=UTC)
        self.assertEqual(header.updated_at, datetime(2026, 10, 4, 17, 4, tzinfo=UTC))
        self.assertEqual(header.updated_at.utcoffset(), timedelta(0))

    def test_updated_never(self) -> None:
        session = FixtureSession(PLACEHOLDER_PAGE)
        tree = NotionClient("", session=session).fetch_block_tree(PAGE_ID)
        header, body = parse_header(flatten_blocks(tree), local_tz=PDT)
        self.assertEqual(header, BriefingHeader(updated_raw="never", updated_at=None, run=None))
        self.assertEqual(len(body), 1)
        self.assertTrue(body[0].text.startswith("This page is overwritten twice a day"))

    def test_header_and_run_on_one_line(self) -> None:
        for text in (
            "Updated: 2026-10-04 22:31 PDT · Run: PM",
            "Updated: 2026-10-04 22:31 PDT | Run: PM",
            "Updated: 2026-10-04 22:31 PDT, Run: PM",
            "Updated: 2026-10-04 22:31 PDT Run: pm",
            f"**Updated:** 2026-10-04 22:31 PDT {DASH} **Run:** PM",
        ):
            with self.subTest(text=text):
                lines = [header_line(text), header_line("Body")]
                header, body = parse_header(lines, local_tz=PDT)
                self.assertEqual(header.updated_raw, "2026-10-04 22:31 PDT")
                self.assertEqual(header.updated_at, datetime(2026, 10, 4, 22, 31, tzinfo=PDT))
                self.assertEqual(header.run, "PM")
                self.assertEqual([l.text for l in body], ["Body"])

    def test_single_paragraph_with_both_lines(self) -> None:
        tree = [blk("paragraph", "Updated: 2026-10-04 22:31 PDT\nRun: PM"), blk("paragraph", "Body")]
        lines = flatten_blocks(tree)
        self.assertEqual(len(lines), 3)
        header, body = parse_header(lines, local_tz=PDT)
        self.assertEqual(header.updated_at, datetime(2026, 10, 4, 22, 31, tzinfo=PDT))
        self.assertEqual(header.run, "PM")
        self.assertEqual([l.text for l in body], ["Body"])

    def test_single_line_containing_a_newline(self) -> None:
        header, body = parse_header([header_line("Updated: 2026-10-04 22:31 PDT\nRun: PM")],
                                    local_tz=PDT)
        self.assertEqual(header.updated_raw, "2026-10-04 22:31 PDT")
        self.assertEqual(header.run, "PM")
        self.assertEqual(body, [])

    def test_time_zone_forms(self) -> None:
        ten_oh_four = datetime(2026, 10, 4, 10, 4, tzinfo=PDT)
        cases = {
            "2026-10-04 17:04 UTC": ten_oh_four,
            "2026-10-04 17:04 GMT": ten_oh_four,
            "2026-10-04 17:04 utc": ten_oh_four,
            "2026-10-04T17:04:00Z": ten_oh_four,
            "2026-10-04T17:04:00.000Z": ten_oh_four,
            "2026-10-04 17:04Z": ten_oh_four,
            "2026-10-04 10:04 -07:00": ten_oh_four,
            "2026-10-04T10:04:00-0700": ten_oh_four,
            "2026-10-04 22:34 +05:30": ten_oh_four,
            "2026-10-04 13:04 EDT": ten_oh_four,
            "2026-10-04 12:04 CDT": ten_oh_four,
            "2026-10-04 09:04 PST": ten_oh_four,
            "2026-10-04 07:04 HST": ten_oh_four,
            "2026-10-04 10:04 AM PDT": ten_oh_four,
            "2026-10-04 10:04am": ten_oh_four,
            "2026-10-04 10:04 pm PDT": datetime(2026, 10, 4, 22, 4, tzinfo=PDT),
            "2026-10-04 12:30 AM PDT": datetime(2026, 10, 4, 0, 30, tzinfo=PDT),
            "2026-10-04 12:30 PM": datetime(2026, 10, 4, 12, 30, tzinfo=PDT),
            "2026-10-04 10:04": ten_oh_four,                 # no zone -> local
            "2026-10-04 10:04 CEST": ten_oh_four,            # unknown zone -> local
            "Sun 2026-10-04 10:04 PDT (morning)": ten_oh_four,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                header, _ = parse_header([header_line(f"Updated: {raw}")], local_tz=PDT)
                self.assertEqual(header.updated_at, expected)
                self.assertEqual(header.updated_at.utcoffset(), timedelta(hours=-7))

    def test_unparseable_values_give_none(self) -> None:
        for raw in ("never", "soon", "2026-13-45 10:04 PDT", "Oct 4, 10:04"):
            with self.subTest(raw=raw):
                header, body = parse_header([header_line(f"Updated: {raw}")], local_tz=PDT)
                self.assertEqual(header.updated_raw, raw)
                self.assertIsNone(header.updated_at)
                self.assertEqual(body, [])

    def test_markdown_emphasis_in_header(self) -> None:
        lines = [header_line("**Updated:** _2026-10-04 10:04 PDT_"), header_line("*Run:* **AM**")]
        header, body = parse_header(lines, local_tz=PDT)
        self.assertEqual(header.updated_raw, "2026-10-04 10:04 PDT")
        self.assertEqual(header.run, "AM")
        self.assertEqual(body, [])

    def test_no_header(self) -> None:
        lines = [header_line("Just a page"), FlatLine(BULLETED, "item")]
        header, body = parse_header(lines, local_tz=PDT)
        self.assertEqual(header, BriefingHeader())
        self.assertEqual(body, lines)

    def test_only_first_scan_lines_are_considered(self) -> None:
        lines = [header_line(f"line {i}") for i in range(6)]
        lines.append(header_line("Updated: 2026-10-04 10:04 PDT"))
        header, body = parse_header(lines, local_tz=PDT)
        self.assertIsNone(header.updated_raw)
        self.assertEqual(body, lines)
        header, body = parse_header(lines, scan=7, local_tz=PDT)
        self.assertEqual(header.updated_raw, "2026-10-04 10:04 PDT")
        self.assertEqual(len(body), 6)

    def test_run_only_and_system_zone_default(self) -> None:
        header, body = parse_header([header_line("Run: PM"), header_line("Updated: 2026-10-04 10:04 PDT")])
        self.assertEqual(header.run, "PM")
        self.assertEqual(header.updated_at, datetime(2026, 10, 4, 10, 4, tzinfo=PDT))
        self.assertIsNotNone(header.updated_at.tzinfo)
        self.assertEqual(body, [])


# --------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------

class FreshnessTests(unittest.TestCase):
    def header(self, updated_at: datetime | None, run: str | None) -> BriefingHeader:
        return BriefingHeader(updated_raw="x" if updated_at else "never", updated_at=updated_at, run=run)

    def test_fresh(self) -> None:
        header = self.header(datetime(2026, 10, 4, 10, 4, tzinfo=PDT), "AM")
        for expected in ("AM", "am"):
            freshness = check_freshness(header, expected, NOW)
            self.assertEqual((freshness.fresh, freshness.updated_today, freshness.run_matches),
                             (True, True, True))

    def test_wrong_run(self) -> None:
        freshness = check_freshness(self.header(datetime(2026, 10, 4, 10, 4, tzinfo=PDT), "AM"), "PM", NOW)
        self.assertEqual((freshness.fresh, freshness.updated_today, freshness.run_matches),
                         (False, True, False))

    def test_yesterday(self) -> None:
        freshness = check_freshness(self.header(datetime(2026, 10, 3, 23, 31, tzinfo=PDT), "AM"), "AM", NOW)
        self.assertEqual((freshness.fresh, freshness.updated_today, freshness.run_matches),
                         (False, False, True))

    def test_never_updated(self) -> None:
        freshness = check_freshness(self.header(None, None), "AM", NOW)
        self.assertEqual((freshness.fresh, freshness.updated_today, freshness.run_matches),
                         (False, False, False))

    def test_no_expected_run(self) -> None:
        freshness = check_freshness(self.header(datetime(2026, 10, 4, 10, 4, tzinfo=PDT), None), None, NOW)
        self.assertEqual((freshness.fresh, freshness.updated_today, freshness.run_matches),
                         (True, True, True))

    def test_day_is_judged_in_nows_zone(self) -> None:
        late_utc = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)  # 19:00 PDT on Oct 4
        self.assertTrue(check_freshness(self.header(late_utc, "PM"), "PM", NOW).updated_today)

    def test_run_that_started_before_midnight_keeps_its_day(self) -> None:
        header = self.header(datetime(2026, 10, 4, 23, 35, tzinfo=PDT), "PM")
        started = datetime(2026, 10, 4, 23, 42, tzinfo=PDT)
        after_midnight = datetime(2026, 10, 5, 0, 12, tzinfo=PDT)
        self.assertFalse(check_freshness(header, "PM", after_midnight).fresh)
        freshness = check_freshness(header, "PM", after_midnight, run_started=started)
        self.assertEqual((freshness.fresh, freshness.updated_today, freshness.run_matches),
                         (True, True, True))
        # A page written after midnight is today's either way; the run still has to match.
        new_day = self.header(datetime(2026, 10, 5, 0, 5, tzinfo=PDT), "PM")
        self.assertTrue(check_freshness(new_day, "PM", after_midnight, run_started=started).fresh)
        self.assertFalse(check_freshness(header, "AM", after_midnight, run_started=started).fresh)

    def test_run_start_more_than_a_day_back_is_not_used(self) -> None:
        header = self.header(datetime(2026, 10, 2, 23, 35, tzinfo=PDT), "PM")
        started = datetime(2026, 10, 2, 23, 42, tzinfo=PDT)
        self.assertFalse(check_freshness(header, "PM", NOW, run_started=started).updated_today)
        # A start "in the future" (clock changed) is ignored too.
        future = datetime(2026, 10, 6, 8, 0, tzinfo=PDT)
        today = self.header(datetime(2026, 10, 4, 9, 0, tzinfo=PDT), "AM")
        self.assertTrue(check_freshness(today, "AM", NOW, run_started=future).fresh)
        self.assertFalse(check_freshness(self.header(future, "AM"), "AM", NOW, run_started=future).fresh)


# --------------------------------------------------------------------------
# Polling
# --------------------------------------------------------------------------

class FakeClock:
    def __init__(self, start: datetime, stop_on_wait: int | None = None) -> None:
        self.current = start
        self.waits: list[float] = []
        self.stop_on_wait = stop_on_wait

    def now(self) -> datetime:
        return self.current

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        if self.stop_on_wait is not None and len(self.waits) >= self.stop_on_wait:
            return True
        self.current += timedelta(seconds=seconds)
        return False


def briefing(updated_at: datetime | None, run: str | None, tag: str = "") -> Briefing:
    header = BriefingHeader(updated_raw=tag or "x", updated_at=updated_at, run=run)
    return Briefing(PAGE_ID, header, (FlatLine(PARAGRAPH, tag),), NOW)


STALE = briefing(datetime(2026, 10, 3, 23, 31, tzinfo=PDT), "PM", "stale")
FRESH = briefing(datetime(2026, 10, 4, 10, 4, tzinfo=PDT), "AM", "fresh")


class ScriptedFetch:
    """Returns/raises the scripted outcomes in order, repeating the last one."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.count = 0

    def __call__(self) -> Briefing:
        outcome = self.outcomes[min(self.count, len(self.outcomes) - 1)]
        self.count += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class PollTests(unittest.TestCase):
    def poll(self, fetch: ScriptedFetch, clock: FakeClock, *, expected_run: str | None = "AM",
             interval_s: float = 60, timeout_s: float = 900):
        attempts: list[tuple] = []
        result = poll_for_briefing(
            fetch, expected_run=expected_run, interval_s=interval_s, timeout_s=timeout_s,
            now=clock.now, wait=clock.wait,
            on_attempt=lambda b, f, e, n: attempts.append((b, f, e, n)),
        )
        return result, attempts

    def test_becomes_fresh_on_third_attempt(self) -> None:
        clock = FakeClock(NOW)
        result, attempts = self.poll(ScriptedFetch(STALE, STALE, FRESH), clock)
        self.assertIs(result.briefing, FRESH)
        self.assertTrue(result.freshness.fresh)
        self.assertIsNone(result.error)
        self.assertEqual(result.attempts, 3)
        self.assertFalse(result.timed_out)
        self.assertFalse(result.stopped)
        self.assertEqual(clock.waits, [60, 60])
        self.assertEqual([a[3] for a in attempts], [1, 2, 3])
        self.assertEqual([a[1].fresh for a in attempts], [False, False, True])

    def test_never_fresh_times_out_with_last_briefing(self) -> None:
        clock = FakeClock(NOW)
        latest = briefing(datetime(2026, 10, 4, 9, 0, tzinfo=PDT), "PM", "latest")
        fetch = ScriptedFetch(*([STALE] * 10), latest)
        result, attempts = self.poll(fetch, clock)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.stopped)
        self.assertEqual(result.attempts, 900 // 60 + 1)
        self.assertIs(result.briefing, latest)
        self.assertFalse(result.freshness.fresh)
        self.assertIsNone(result.error)
        self.assertAlmostEqual(sum(clock.waits), 900)
        self.assertEqual(len(attempts), result.attempts)

    def test_last_wait_is_shortened_to_the_deadline(self) -> None:
        clock = FakeClock(NOW)
        result, _ = self.poll(ScriptedFetch(STALE), clock, timeout_s=150)
        self.assertTrue(result.timed_out)
        self.assertEqual(clock.waits, [60, 60, 30])
        self.assertEqual(result.attempts, 4)

    def test_auth_error_stops_immediately(self) -> None:
        for error in (NotionAuthError("not shared"), NotionConfigError("no token")):
            with self.subTest(error=type(error).__name__):
                clock = FakeClock(NOW)
                with self.assertLogs(nc.logger, "WARNING"):
                    result, attempts = self.poll(ScriptedFetch(error, FRESH), clock)
                self.assertIs(result.error, error)
                self.assertIsNone(result.briefing)
                self.assertIsNone(result.freshness)
                self.assertEqual(result.attempts, 1)
                self.assertFalse(result.timed_out)
                self.assertFalse(result.stopped)
                self.assertEqual(clock.waits, [])
                self.assertEqual(attempts, [(None, None, error, 1)])

    def test_transient_error_keeps_last_good_briefing(self) -> None:
        clock = FakeClock(NOW)
        blip = NotionError("Notion is having trouble (HTTP 503).")
        with self.assertLogs(nc.logger, "WARNING"):
            result, attempts = self.poll(ScriptedFetch(STALE, blip), clock, timeout_s=120)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.attempts, 3)
        self.assertIs(result.briefing, STALE)
        self.assertFalse(result.freshness.fresh)
        self.assertIs(result.error, blip)
        self.assertEqual([(a[0] is STALE, a[2]) for a in attempts],
                         [(True, None), (False, blip), (False, blip)])

    def test_transient_error_then_fresh(self) -> None:
        clock = FakeClock(NOW)
        with self.assertLogs(nc.logger, "WARNING"):
            result, _ = self.poll(ScriptedFetch(STALE, NotionError("blip"), FRESH), clock)
        self.assertIs(result.briefing, FRESH)
        self.assertIsNone(result.error)
        self.assertEqual(result.attempts, 3)

    def test_unexpected_exception_is_wrapped_and_retried(self) -> None:
        clock = FakeClock(NOW)
        with self.assertLogs(nc.logger, "WARNING"):
            result, attempts = self.poll(ScriptedFetch(RuntimeError("boom " + FAKE_TOKEN), FRESH), clock)
        self.assertIs(result.briefing, FRESH)
        self.assertEqual(result.attempts, 2)
        wrapped = attempts[0][2]
        self.assertIsInstance(wrapped, NotionError)
        self.assertIn("RuntimeError", str(wrapped))
        self.assertNotIn(FAKE_TOKEN, str(wrapped))

    def test_stop_requested(self) -> None:
        clock = FakeClock(NOW, stop_on_wait=2)
        result, _ = self.poll(ScriptedFetch(STALE), clock)
        self.assertTrue(result.stopped)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.attempts, 2)
        self.assertIs(result.briefing, STALE)

    def test_no_expected_run_returns_after_first_success(self) -> None:
        clock = FakeClock(NOW)
        result, _ = self.poll(ScriptedFetch(STALE, FRESH), clock, expected_run=None)
        self.assertIs(result.briefing, STALE)
        self.assertEqual(result.attempts, 1)
        self.assertTrue(result.freshness.run_matches)
        self.assertFalse(result.freshness.updated_today)
        self.assertEqual(clock.waits, [])

    def test_no_expected_run_retries_until_a_success(self) -> None:
        clock = FakeClock(NOW)
        with self.assertLogs(nc.logger, "WARNING"):
            result, _ = self.poll(ScriptedFetch(NotionError("offline"), STALE), clock, expected_run=None)
        self.assertIs(result.briefing, STALE)
        self.assertIsNone(result.error)
        self.assertEqual(result.attempts, 2)

    def test_run_started_before_midnight_accepts_that_days_briefing(self) -> None:
        late_pm = briefing(datetime(2026, 10, 4, 23, 58, tzinfo=PDT), "PM", "late")
        after_midnight = datetime(2026, 10, 5, 0, 2, tzinfo=PDT)
        clock = FakeClock(after_midnight)
        result = poll_for_briefing(ScriptedFetch(late_pm), expected_run="PM", interval_s=60,
                                   timeout_s=300, now=clock.now, wait=clock.wait,
                                   run_started=datetime(2026, 10, 4, 23, 50, tzinfo=PDT))
        self.assertTrue(result.freshness.fresh)
        self.assertEqual(result.attempts, 1)
        # Without the run's start the same page counts as yesterday's.
        clock = FakeClock(after_midnight)
        result = poll_for_briefing(ScriptedFetch(late_pm), expected_run="PM", interval_s=60,
                                   timeout_s=300, now=clock.now, wait=clock.wait)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.freshness.fresh)

    def test_broken_callback_does_not_stop_polling(self) -> None:
        clock = FakeClock(NOW)

        def explode(*_args: Any) -> None:
            raise ValueError("callback bug")

        with self.assertLogs(nc.logger, "ERROR"):
            result = poll_for_briefing(ScriptedFetch(STALE, FRESH), expected_run="AM", interval_s=60,
                                       timeout_s=900, now=clock.now, wait=clock.wait, on_attempt=explode)
        self.assertIs(result.briefing, FRESH)


# --------------------------------------------------------------------------
# fetch_briefing
# --------------------------------------------------------------------------

class FetchBriefingTests(unittest.TestCase):
    def test_fake_page(self) -> None:
        client = NotionClient("test-token", session=FixtureSession(FAKE_PAGE))
        result = fetch_briefing(client, PAGE_ID, now=NOW)
        self.assertEqual(result.page_id, PAGE_ID)
        self.assertEqual(result.fetched_at, NOW)
        self.assertEqual(result.header.run, "AM")
        self.assertEqual(result.header.updated_at, datetime(2026, 10, 4, 10, 4, tzinfo=PDT))
        self.assertIsInstance(result.lines, tuple)
        self.assertEqual(as_tuples(list(result.lines)), EXPECTED_FAKE_PAGE[2:])
        self.assertTrue(check_freshness(result.header, "AM", NOW).fresh)

    def test_placeholder_page(self) -> None:
        client = NotionClient("", session=FixtureSession(PLACEHOLDER_PAGE))
        result = fetch_briefing(client, PAGE_ID)
        self.assertIsNone(result.header.updated_at)
        self.assertEqual(result.header.updated_raw, "never")
        self.assertEqual(len(result.lines), 1)
        self.assertIsNotNone(result.fetched_at.tzinfo)


# --------------------------------------------------------------------------
# NotionClient HTTP behaviour
# --------------------------------------------------------------------------

class NotionClientHttpTests(unittest.TestCase):
    def test_headers_params_and_pagination(self) -> None:
        session = ScriptedSession(list_page([{"id": "a"}], next_cursor="cur-1"), list_page([{"id": "b"}]))
        client, sleeps = make_client(session)
        self.assertEqual(client.list_children("blk"), [{"id": "a"}, {"id": "b"}])
        self.assertEqual(sleeps, [])
        first, second = session.calls
        self.assertEqual(first["url"], "https://api.notion.com/v1/blocks/blk/children")
        self.assertEqual(first["params"], {"page_size": 100})
        self.assertEqual(second["params"], {"page_size": 100, "start_cursor": "cur-1"})
        self.assertEqual(first["headers"], {"Authorization": f"Bearer {FAKE_TOKEN}",
                                            "Notion-Version": "2022-06-28",
                                            "Accept": "application/json"})
        self.assertEqual(first["timeout"], 20.0)

    def test_429_waits_retry_after_then_succeeds(self) -> None:
        session = ScriptedSession(error_page(429, "slow down", {"Retry-After": "7"}), list_page([{"id": "a"}]))
        client, sleeps = make_client(session)
        with self.assertLogs(nc.logger, "WARNING"):
            self.assertEqual(client.list_children("blk"), [{"id": "a"}])
        self.assertEqual(sleeps, [7.0])
        self.assertEqual(len(session.calls), 2)

    def test_429_retry_after_is_capped_and_defaulted(self) -> None:
        session = ScriptedSession(error_page(429, "x", {"retry-after": "120"}), error_page(429, "x"),
                                  list_page([]))
        client, sleeps = make_client(session)
        with self.assertLogs(nc.logger, "WARNING"):
            client.list_children("blk")
        self.assertEqual(sleeps, [30.0, 2.0])

    def test_500_retried_then_notion_error(self) -> None:
        session = ScriptedSession(*[error_page(500, "internal")] * 4)
        client, sleeps = make_client(session)
        with self.assertLogs(nc.logger, "WARNING"):
            with self.assertRaises(NotionError) as caught:
                client.list_children("blk")
        self.assertNotIsInstance(caught.exception, NotionAuthError)
        self.assertEqual(caught.exception.status, 500)
        self.assertEqual(sleeps, [1.0, 2.0, 4.0])
        self.assertEqual(len(session.calls), 4)

    def test_503_then_success(self) -> None:
        session = ScriptedSession(FakeResponse(503), FakeResponse(502), list_page([{"id": "a"}]))
        client, sleeps = make_client(session)
        with self.assertLogs(nc.logger, "WARNING"):
            self.assertEqual(client.list_children("blk"), [{"id": "a"}])
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_network_errors_retried(self) -> None:
        session = ScriptedSession(requests.ConnectionError("down"), requests.Timeout("slow"),
                                  list_page([{"id": "a"}]))
        client, sleeps = make_client(session)
        with self.assertLogs(nc.logger, "WARNING"):
            self.assertEqual(client.list_children("blk"), [{"id": "a"}])
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_network_errors_exhausted(self) -> None:
        session = ScriptedSession(*[requests.Timeout("slow")] * 4)
        client, sleeps = make_client(session)
        with self.assertLogs(nc.logger, "WARNING"):
            with self.assertRaises(NotionError) as caught:
                client.list_children("blk")
        self.assertIn("Could not reach Notion", str(caught.exception))
        self.assertEqual(len(sleeps), 3)

    def test_401_is_auth_error_without_retry(self) -> None:
        session = ScriptedSession(error_page(401, "API token is invalid."))
        client, sleeps = make_client(session)
        with self.assertRaises(NotionAuthError) as caught:
            client.list_children("blk")
        self.assertIn("401", str(caught.exception))
        self.assertIn("NOTION_TOKEN", str(caught.exception))
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual((len(session.calls), sleeps), (1, []))

    def test_404_mentions_sharing_and_connections(self) -> None:
        session = ScriptedSession(error_page(404, "Could not find block with ID: blk. Make sure the "
                                                  "relevant pages and databases are shared with your integration."))
        client, sleeps = make_client(session)
        with self.assertRaises(NotionAuthError) as caught:
            client.list_children("blk")
        message = str(caught.exception)
        self.assertIn("shared", message)
        self.assertIn("Connections", message)
        self.assertIn("Could not find block", message)
        self.assertEqual((len(session.calls), sleeps), (1, []))

    def test_403_is_auth_error(self) -> None:
        client, _ = make_client(ScriptedSession(error_page(403, "restricted")))
        with self.assertRaises(NotionAuthError):
            client.list_children("blk")

    def test_other_4xx_is_plain_notion_error(self) -> None:
        client, sleeps = make_client(ScriptedSession(error_page(400, "body failed validation")))
        with self.assertRaises(NotionError) as caught:
            client.list_children("blk")
        self.assertNotIsInstance(caught.exception, NotionAuthError)
        self.assertIn("400", str(caught.exception))
        self.assertIn("body failed validation", str(caught.exception))
        self.assertEqual(sleeps, [])

    def test_non_json_body(self) -> None:
        client, _ = make_client(ScriptedSession(FakeResponse(200, None)))
        with self.assertRaises(NotionError):
            client.list_children("blk")

    def test_missing_token_is_config_error(self) -> None:
        for token in ("", "   "):
            with self.subTest(token=token):
                session = ScriptedSession(list_page([]))
                client, _ = make_client(session, token=token)
                with self.assertRaises(NotionConfigError):
                    client.fetch_block_tree("blk")
                self.assertEqual(session.calls, [])

    def test_token_never_leaks(self) -> None:
        client, _ = make_client(ScriptedSession())
        self.assertNotIn(FAKE_TOKEN, repr(client))
        self.assertNotIn(FAKE_TOKEN, str(client))

        scenarios = [
            [error_page(401, f"bad token {FAKE_TOKEN}")],
            [error_page(404, f"token {FAKE_TOKEN} cannot see this")],
            [error_page(400, f"echo {FAKE_TOKEN}")],
            [error_page(500, FAKE_TOKEN)] * 4,
            [requests.ConnectionError(f"Authorization: Bearer {FAKE_TOKEN}")] * 4,
            [requests.TooManyRedirects(f"Bearer {FAKE_TOKEN}")],
        ]
        for items in scenarios:
            with self.subTest(first=repr(items[0])[:40]):
                client, _ = make_client(ScriptedSession(*items))
                with self.assertLogs(nc.logger, "DEBUG") as logs:
                    with self.assertRaises(NotionError) as caught:
                        client.list_children("blk")
                self.assertNotIn(FAKE_TOKEN, str(caught.exception))
                self.assertNotIn(FAKE_TOKEN, repr(caught.exception))
                self.assertNotIn(FAKE_TOKEN, "\n".join(logs.output))

    def test_invalid_header_message_is_not_logged(self) -> None:
        # requests' InvalidHeader quotes the header value with repr(), so a token
        # with a stray control character would not match exact-string redaction.
        odd_token = "fake-token\x0bwith-a-control-character"
        quoted = repr(f"Bearer {odd_token}")
        session = ScriptedSession(requests.exceptions.InvalidHeader(
            f"Invalid leading whitespace, reserved character(s), or return character(s) "
            f"in header value: {quoted}"))
        client, _ = make_client(session, token=odd_token)
        with self.assertLogs(nc.logger, "DEBUG") as logs:
            with self.assertRaises(NotionError) as caught:
                client.list_children("blk")
        output = "\n".join(logs.output)
        self.assertIn("InvalidHeader", output)
        for text in (output, str(caught.exception)):
            self.assertNotIn("with-a-control-character", text)

    def test_has_more_without_cursor_stops(self) -> None:
        response = FakeResponse(200, {"object": "list", "results": [{"id": "a"}],
                                      "next_cursor": None, "has_more": True})
        session = ScriptedSession(response)
        client, _ = make_client(session)
        with self.assertLogs(nc.logger, "WARNING"):
            self.assertEqual(client.list_children("blk"), [{"id": "a"}])

    def test_repeated_cursor_is_an_error(self) -> None:
        session = ScriptedSession(list_page([], "same"), list_page([], "same"))
        client, _ = make_client(session)
        with self.assertRaises(NotionError):
            client.list_children("blk")


class FetchBlockTreeTests(unittest.TestCase):
    def test_request_cap(self) -> None:
        session = EndlessSession()
        client, _ = make_client(session)
        with self.assertRaises(NotionError) as caught:
            client.fetch_block_tree("blk")
        self.assertEqual(session.count, nc.MAX_TREE_REQUESTS)
        self.assertIn("500", str(caught.exception))

    def test_request_cap_counts_nested_requests(self) -> None:
        children = {f"b{i}": [{"id": f"b{i + 1}", "type": "paragraph", "has_children": True}]
                    for i in range(20)}
        session = TreeSession(children)
        client, _ = make_client(session)
        client.max_tree_requests = 5
        with self.assertRaises(NotionError):
            client.fetch_block_tree("b0", max_depth=50)
        self.assertEqual(len(session.requested), 5)

    def test_child_pages_not_recursed_and_max_depth(self) -> None:
        children = {
            "root": [
                {"id": "page", "type": "child_page", "has_children": True, "child_page": {"title": "Sub"}},
                {"id": "db", "type": "child_database", "has_children": True,
                 "child_database": {"title": "DB"}},
                {"id": "l1", "type": "toggle", "has_children": True, "toggle": {"rich_text": rich("L1")}},
            ],
            "l1": [{"id": "l2", "type": "toggle", "has_children": True, "toggle": {"rich_text": rich("L2")}}],
            "l2": [{"id": "l3", "type": "paragraph", "has_children": False,
                    "paragraph": {"rich_text": rich("L3")}}],
        }
        session = TreeSession(children)
        client, _ = make_client(session)
        tree = client.fetch_block_tree("root")
        self.assertEqual(session.requested, ["root", "l1", "l2"])
        self.assertNotIn("_children", tree[0])
        self.assertNotIn("_children", tree[1])
        self.assertEqual(tree[2]["_children"][0]["_children"][0]["id"], "l3")
        self.assertNotIn("_children", children["root"][2])  # original dicts untouched

        session = TreeSession(children)
        client, _ = make_client(session)
        with self.assertLogs(nc.logger, "WARNING"):
            tree = client.fetch_block_tree("root", max_depth=1)
        self.assertEqual(session.requested, ["root", "l1"])
        self.assertNotIn("_children", tree[2]["_children"][0])


if __name__ == "__main__":
    unittest.main()
