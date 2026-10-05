"""Tests for briefing_reader.text_prep: markdown stripping, speech conversion and
script building.

The core tests load the saved fake Notion page (tests/fixtures/fake_page.json)
through NotionClient + FixtureSession + fetch_briefing, exactly as the app
does, and assert on the resulting Script. No network access.
"""

from __future__ import annotations

import re
import string
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

from briefing_reader.actions import parse_action_line
from briefing_reader.models import (
    BULLETED,
    CODE,
    DIVIDER,
    HEADING,
    ITEM_ENTRY,
    ITEM_HEADING,
    ITEM_INTRO,
    ITEM_NOTE,
    ITEM_OUTRO,
    ITEM_PAUSE,
    ITEM_SUBHEADING,
    ITEM_TEXT,
    NUMBERED,
    PARAGRAPH,
    TO_DO,
    TOGGLE,
    Briefing,
    BriefingHeader,
    FlatLine,
    Freshness,
    Script,
    ScriptItem,
    Section,
)
from briefing_reader.notion_client import (
    FixtureSession,
    NotionClient,
    check_freshness,
    fetch_briefing,
    flatten_blocks,
    parse_header,
)
from briefing_reader.text_prep import (
    ACTIONS_CLOSING,
    ACTIONS_KEY,
    ACTIONS_TITLE,
    build_script,
    clean_heading,
    describe_updated,
    format_time,
    is_ignore_heading,
    spoken_transcript,
    stale_note,
    strip_markdown,
    to_spoken,
    updated_label,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_PAGE = FIXTURES / "fake_page.json"
PLACEHOLDER_PAGE = FIXTURES / "placeholder_page.json"
PAGE_ID = "00112233445566778899aabbccddeeff"

# Fixed zones so nothing depends on the machine's time zone.
PDT = timezone(timedelta(hours=-7), "PDT")
UTC = timezone.utc
NOW = datetime(2026, 10, 4, 10, 30, tzinfo=PDT)           # the fake page says 2026-10-04 10:04 PDT
NEXT_MORNING = datetime(2026, 10, 5, 8, 0, tzinfo=PDT)
SAME_NIGHT = datetime(2026, 10, 4, 23, 50, tzinfo=PDT)

DASH = "\u2014"
THUMBS_UP = "\U0001F44D"
WASTEBASKET = "\U0001F5D1\ufe0f"

FRESH_INTRO = "Here's your AM briefing, updated today at 10:04 AM."
OUTRO = "That's the end of your briefing."

# What a listener may hear: plain ASCII words, digits and everyday punctuation.
ALLOWED_SPOKEN_CHARS = frozenset(string.ascii_letters + string.digits + " .,:!?'\"()$%-/")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27bf\u2b00-\u2bff\ufe0f\u200d\u20e3]")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def load_briefing(path: Path = FAKE_PAGE, now: datetime = NOW) -> Briefing:
    """Fetch a saved page through the real client code path."""
    session = FixtureSession(path)
    client = NotionClient(token="test-token", session=session, sleep=lambda s: None)
    return fetch_briefing(client, session.page_id, now=now)


def script_for(path: Path = FAKE_PAGE, *, now: datetime = NOW, expected_run: str | None = "AM",
               **kwargs) -> Script:
    briefing = load_briefing(path, now)
    freshness = check_freshness(briefing.header, expected_run, now)
    return build_script(briefing, now=now, expected_run=expected_run, freshness=freshness, **kwargs)


def section_by_title(script: Script, title: str) -> Section:
    for section in script.sections:
        if section.title == title:
            return section
    raise AssertionError(f"no section titled {title!r} in {[s.title for s in script.sections]}")


def item_with_display(section: Section, display: str):
    for item in section.items:
        if item.display == display:
            return item
    raise AssertionError(f"no item displayed as {display!r} in section {section.key}")


def all_items(script: Script):
    return [item for section in script.sections for item in section.items]


def synthetic(*lines: FlatLine, header: BriefingHeader | None = None) -> Briefing:
    return Briefing(page_id=PAGE_ID, header=header or BriefingHeader(), lines=tuple(lines),
                    fetched_at=NOW)


def heading(text: str, level: int = 2) -> FlatLine:
    return FlatLine(HEADING, text, 0, level=level)


def bullet(text: str, depth: int = 0) -> FlatLine:
    return FlatLine(BULLETED, text, depth)


def para(text: str, depth: int = 0) -> FlatLine:
    return FlatLine(PARAGRAPH, text, depth)


DIVIDER_LINE = FlatLine(DIVIDER, "")


def block(btype: str, text: str, children: list[dict] | None = None, *, bold: bool = False,
          **payload) -> dict:
    """A Notion block in fetch_block_tree shape, for pages built through the real flattener."""
    rich = [{"type": "text", "text": {"content": text}, "annotations": {"bold": bold},
             "plain_text": text}]
    data = {"object": "block", "id": f"blk-{len(text)}-{btype}", "type": btype,
            btype: {"rich_text": rich, **payload}, "has_children": bool(children)}
    if children:
        data["_children"] = children
    return data


def page(*blocks: dict) -> Briefing:
    """A briefing from Notion blocks, flattened and header-parsed exactly as fetch_briefing does."""
    header, body = parse_header(flatten_blocks(list(blocks)), local_tz=PDT)
    return Briefing(page_id=PAGE_ID, header=header, lines=tuple(body), fetched_at=NOW)


def titled_sections(script: Script) -> list[tuple[str, bool]]:
    return [(s.title, s.ignored) for s in script.sections[1:-1]]


# --------------------------------------------------------------------------
# The saved fake page: structure
# --------------------------------------------------------------------------

class FakePageStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.briefing = load_briefing()
        cls.script = script_for()

    def test_fixture_loaded_through_notion_client(self) -> None:
        self.assertEqual(self.briefing.page_id, PAGE_ID)
        self.assertEqual(self.briefing.header.run, "AM")
        self.assertIsNotNone(self.briefing.header.updated_at)
        self.assertGreater(len(self.briefing.lines), 20)

    def test_section_keys_titles_and_ignored_flags(self) -> None:
        sections = self.script.sections
        self.assertEqual([s.key for s in sections], ["intro", "s1", "s2", "s3", "s4", "s5", "outro"])
        self.assertEqual([s.title for s in sections],
                         ["", "Work inbox (3)", "Due", "Actionable", "Newsletters", "Ignore (14)", ""])
        self.assertEqual([s.ignored for s in sections], [False, False, False, False, False, True, False])
        self.assertTrue(self.script.has_ignored)

    def test_titles_have_no_emoji_or_markdown(self) -> None:
        for section in self.script.sections:
            self.assertIsNone(EMOJI_RE.search(section.title), section.title)
            self.assertNotIn("*", section.title)
        self.assertNotIn(WASTEBASKET, section_by_title(self.script, "Ignore (14)").title)

    def test_each_heading_section_starts_with_its_announcement(self) -> None:
        expected = {
            "s1": ("Work inbox (3)", "Work inbox. 3 items."),
            "s2": ("Due", "Due."),
            "s3": ("Actionable", "Actionable."),
            "s4": ("Newsletters", "Newsletters."),
            "s5": ("Ignore (14)", "Ignore. 14 items."),
        }
        for section in self.script.sections[1:-1]:
            first = section.items[0]
            self.assertEqual(first.kind, ITEM_HEADING)
            self.assertEqual((first.display, first.spoken), expected[section.key])
            self.assertEqual(sum(item.kind == ITEM_HEADING for item in section.items), 1)

    def test_follow_ups_is_a_subheading_inside_actionable(self) -> None:
        actionable = section_by_title(self.script, "Actionable")
        sub = item_with_display(actionable, "Follow-ups")
        self.assertEqual(sub.kind, ITEM_SUBHEADING)
        self.assertEqual(sub.spoken, "Follow-ups.")
        self.assertNotIn("Follow-ups", [s.title for s in self.script.sections])
        # Content after the subheading stays in the same section.
        self.assertEqual(item_with_display(actionable, "Sam | Contract draft").spoken,
                         "Sam. Contract draft.")

    def test_intro_section(self) -> None:
        intro = self.script.sections[0]
        self.assertEqual([item.kind for item in intro.items], [ITEM_INTRO, ITEM_TEXT])
        self.assertEqual(intro.items[0].spoken, FRESH_INTRO)
        self.assertEqual(intro.items[0].display, FRESH_INTRO)
        self.assertEqual(intro.items[1].display, "12 new since last run, 3 need action.")
        self.assertEqual(intro.items[1].spoken, "12 new since last run, 3 need action.")

    def test_outro_section(self) -> None:
        outro = self.script.sections[-1]
        self.assertEqual(outro.key, "outro")
        self.assertEqual(len(outro.items), 1)
        self.assertEqual(outro.items[0].kind, ITEM_OUTRO)
        self.assertEqual((outro.items[0].spoken, outro.items[0].display), (OUTRO, OUTRO))

    def test_script_metadata_when_fresh(self) -> None:
        self.assertEqual(self.script.run_label, "AM")
        self.assertEqual(self.script.updated_label, "Updated today at 10:04 AM")
        self.assertFalse(self.script.stale)
        self.assertNotIn(ITEM_NOTE, [item.kind for item in all_items(self.script)])

    def test_dividers_at_section_edges_are_dropped(self) -> None:
        # The fake page's two dividers end the intro and the Actionable section.
        for section in self.script.sections:
            kinds = [item.kind for item in section.items]
            self.assertNotIn(ITEM_PAUSE, kinds, section.key)
        self.assertEqual(section_by_title(self.script, "Actionable").items[-1].display,
                         "Screenshot of the alert")

    def test_code_is_displayed_but_not_spoken(self) -> None:
        code = item_with_display(section_by_title(self.script, "Actionable"), "git pull --rebase")
        self.assertEqual(code.kind, ITEM_TEXT)
        self.assertEqual(code.spoken, "")
        self.assertNotIn("git pull", spoken_transcript(self.script, include_ignored=True))


# --------------------------------------------------------------------------
# The saved fake page: speech
# --------------------------------------------------------------------------

class FakePageSpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = script_for()
        cls.transcript = spoken_transcript(cls.script)
        cls.full_transcript = spoken_transcript(cls.script, include_ignored=True)

    def test_headings_are_announced(self) -> None:
        lines = self.transcript.splitlines()
        for announcement in ("Work inbox. 3 items.", "Due.", "Actionable.", "Newsletters."):
            self.assertIn(announcement, lines)

    def test_transcript_order(self) -> None:
        lines = self.transcript.splitlines()
        self.assertEqual(lines[0], FRESH_INTRO)
        self.assertEqual(lines[-1], OUTRO)
        positions = [lines.index(a) for a in
                     ("Work inbox. 3 items.", "Due.", "Actionable.", "Follow-ups.", "Newsletters.")]
        self.assertEqual(positions, sorted(positions))

    def test_ignore_section_skipped_by_default(self) -> None:
        for word in ("Promo", "LinkedIn", "Medium", "Ignore"):
            self.assertNotIn(word, self.transcript)
        self.assertEqual(self.script.section_indices(), [0, 1, 2, 3, 4, 6])

    def test_ignore_section_read_with_include_ignored(self) -> None:
        for text in ("Ignore. 14 items.", "Promo: 20% off sneakers.",
                     "LinkedIn: 5 people viewed your profile.", "Weekly digest from Medium."):
            self.assertIn(text, self.full_transcript.splitlines())
        lines = self.full_transcript.splitlines()
        self.assertLess(lines.index("Ignore. 14 items."), lines.index(OUTRO))

    def test_bullets_become_short_sentences(self) -> None:
        inbox = section_by_title(self.script, "Work inbox (3)")
        alice = inbox.items[1]
        self.assertEqual(alice.kind, ITEM_ENTRY)
        self.assertEqual(alice.display, f"Alice Chen {DASH} Q3 budget review {DASH} wants numbers by Fri")
        self.assertEqual(alice.spoken, "Alice Chen. Q3 budget review. Wants numbers by Friday.")

    def test_pipe_splits_and_url_is_dropped(self) -> None:
        inbox = section_by_title(self.script, "Work inbox (3)")
        invite = item_with_display(inbox, "Calendar invite: Design sync, Tue 2pm | Zoom")
        self.assertEqual(invite.spoken, "Calendar invite: Design sync, Tuesday 2pm. Zoom.")

    def test_arrow_before_an_action_starts_a_new_sentence(self) -> None:
        inbox = section_by_title(self.script, "Work inbox (3)")
        jira = item_with_display(inbox, "Jira: PROJ-142 assigned to you -> please triage")
        self.assertEqual(jira.spoken, "Jira: PROJ-142 assigned to you. Please triage.")

    def test_nested_items_keep_depth(self) -> None:
        inbox = section_by_title(self.script, "Work inbox (3)")
        got = [(item.display, item.depth, item.spoken) for item in inbox.items[2:5]]
        self.assertEqual(got, [
            ("Attached: budget.xlsx", 1, "Attached: budget.xlsx."),
            ("cc: Bob", 1, "CC: Bob."),
            ("Bob already replied", 2, "Bob already replied."),
        ])

    def test_to_dos(self) -> None:
        due = section_by_title(self.script, "Due")
        open_item = item_with_display(due, "[ ] Renew parking permit (expires Oct 6)")
        self.assertEqual(open_item.kind, ITEM_ENTRY)
        self.assertEqual(open_item.spoken, "Renew parking permit (expires October 6).")
        done = item_with_display(due, "[x] Submit expense report")
        self.assertEqual(done.kind, ITEM_ENTRY)
        self.assertEqual(done.spoken, "Already done: submit expense report.")

    def test_numbered_items_prefix_display_only(self) -> None:
        due = section_by_title(self.script, "Due")
        numbered = [(item.display, item.depth, item.spoken) for item in due.items[3:]]
        self.assertEqual(numbered, [
            (f"1. Pay electricity bill {DASH} $84.20", 0, "Pay electricity bill. $84.20."),
            ("2. Reply to landlord re: lease", 0, "Reply to landlord about lease."),
            ("1. Mention the leak", 1, "Mention the leak."),
            ("2. Ask about parking", 1, "Ask about parking."),
        ])

    def test_actionable_block_types(self) -> None:
        actionable = section_by_title(self.script, "Actionable")
        got = [(item.kind, item.depth, item.spoken) for item in actionable.items]
        self.assertEqual(got, [
            (ITEM_HEADING, 0, "Actionable."),
            (ITEM_TEXT, 0, "Security alert: new sign-in from Chrome on Windows."),
            (ITEM_TEXT, 1, "If this wasn't you, reset your password."),
            (ITEM_TEXT, 0, '"Can you send the slides before Monday?" Dave.'),
            (ITEM_TEXT, 0, "Details."),
            (ITEM_ENTRY, 1, "Slides are in the Q3 folder."),
            (ITEM_SUBHEADING, 0, "Follow-ups."),
            (ITEM_ENTRY, 0, "Who. What."),
            (ITEM_ENTRY, 0, "Sam. Contract draft."),
            (ITEM_TEXT, 0, "Left column note."),
            (ITEM_TEXT, 0, "Right column note."),
            (ITEM_TEXT, 0, ""),
            (ITEM_TEXT, 0, "Screenshot of the alert."),
        ])

    def test_hyphenated_words_are_not_split(self) -> None:
        self.assertIn("Follow-ups.", self.transcript.splitlines())
        self.assertIn("new sign-in from", self.transcript)
        self.assertIn("PROJ-142", self.transcript)

    def test_newsletters(self) -> None:
        news = section_by_title(self.script, "Newsletters")
        self.assertEqual([item.spoken for item in news.items], [
            "Newsletters.",
            "Morning Brew. Markets up, oil down.",
            "TLDR AI: three new model releases.",
        ])

    def test_markdown_stripped_in_display_and_speech(self) -> None:
        news = section_by_title(self.script, "Newsletters")
        item_with_display(news, "TLDR AI: three new model releases")
        inbox = section_by_title(self.script, "Work inbox (3)")
        item_with_display(inbox, "Attached: budget.xlsx")
        for item in all_items(self.script):
            for text in (item.display, item.spoken):
                self.assertNotIn("**", text)
                self.assertNotIn("](", text)
                self.assertNotIn("_three_", text)
                self.assertIsNone(EMOJI_RE.search(text), text)
            self.assertNotIn("http", item.spoken)
            self.assertNotIn("zoom.us", item.display)
            self.assertNotIn(THUMBS_UP, item.display)

    def test_every_spoken_string_is_clean(self) -> None:
        wrapped_underscore = re.compile(r"(?<!\w)_\w[^_]*_(?!\w)")
        for section in self.script.sections:
            for item in section.items:
                spoken = item.spoken
                with self.subTest(section=section.key, spoken=spoken):
                    self.assertNotIn("*", spoken)
                    self.assertIsNone(wrapped_underscore.search(spoken))
                    self.assertNotIn("http", spoken)
                    self.assertNotIn("www.", spoken)
                    self.assertNotIn("](", spoken)
                    self.assertNotIn("..", spoken)
                    self.assertNotIn(". .", spoken)
                    self.assertNotIn("()", spoken)
                    self.assertNotIn("( )", spoken)
                    self.assertEqual(spoken, spoken.strip())
                    self.assertNotIn("  ", spoken)
                    self.assertEqual(set(spoken) - ALLOWED_SPOKEN_CHARS, set())
                    if spoken:
                        self.assertIn(spoken[-1], ".!?\"')")

    def test_display_strings_are_plain(self) -> None:
        for item in all_items(self.script):
            extra = {ch for ch in item.display if ord(ch) > 0x7E} - {DASH}
            self.assertEqual(extra, set(), item.display)

    def test_announce_mapping_overrides_spoken_name(self) -> None:
        announce = MappingProxyType({"WORK INBOX": "Your work email", "due (2)": "Due today",
                                     "Newsletters": "Reading list"})
        script = script_for(announce=announce)
        inbox = section_by_title(script, "Work inbox (3)")
        self.assertEqual(inbox.items[0].spoken, "Your work email. 3 items.")
        self.assertEqual(inbox.items[0].display, "Work inbox (3)")
        self.assertEqual(section_by_title(script, "Due").items[0].spoken, "Due today.")
        self.assertEqual(section_by_title(script, "Newsletters").items[0].spoken, "Reading list.")
        self.assertEqual(section_by_title(script, "Actionable").items[0].spoken, "Actionable.")
        lines = spoken_transcript(script).splitlines()
        self.assertIn("Your work email. 3 items.", lines)
        self.assertNotIn("Work inbox. 3 items.", lines)

    def test_custom_ignore_names(self) -> None:
        script = script_for(ignore_names=("Newsletters", "Ignore"))
        self.assertTrue(section_by_title(script, "Newsletters").ignored)
        self.assertNotIn("Morning Brew", spoken_transcript(script))
        nothing_ignored = script_for(ignore_names=())
        self.assertFalse(nothing_ignored.has_ignored)
        self.assertIn("Promo: 20% off sneakers.", spoken_transcript(nothing_ignored))


# --------------------------------------------------------------------------
# Intro sentence and stale note (fake page and placeholder)
# --------------------------------------------------------------------------

class IntroAndStaleNoteTests(unittest.TestCase):
    def intro_items(self, script: Script) -> list[tuple[str, str]]:
        return [(item.kind, item.spoken) for item in script.sections[0].items
                if item.kind in (ITEM_NOTE, ITEM_INTRO)]

    def test_fresh_has_no_note(self) -> None:
        briefing = load_briefing()
        freshness = check_freshness(briefing.header, "AM", NOW)
        self.assertTrue(freshness.fresh)
        self.assertIsNone(stale_note(briefing.header, freshness, "AM", NOW))
        self.assertIsNone(stale_note(briefing.header, None, "AM", NEXT_MORNING))

    def test_intro_uses_header_run_without_expected_run(self) -> None:
        script = script_for(expected_run=None)
        self.assertEqual(self.intro_items(script), [(ITEM_INTRO, FRESH_INTRO)])
        self.assertEqual(script.run_label, "AM")

    def test_intro_names_the_pages_own_run(self) -> None:
        # The page says AM; waiting for PM must not make the intro claim it is the PM briefing.
        script = script_for(now=SAME_NIGHT, expected_run="pm", include_note=False)
        self.assertEqual(self.intro_items(script),
                         [(ITEM_INTRO, "Here's your AM briefing, updated today at 10:04 AM.")])
        self.assertEqual(script.run_label, "AM")

    def test_intro_falls_back_to_expected_run(self) -> None:
        header = BriefingHeader(updated_raw="x", updated_at=datetime(2026, 10, 4, 9, 0, tzinfo=PDT))
        script = build_script(synthetic(header=header), now=NOW, expected_run="pm")
        self.assertEqual(self.intro_items(script),
                         [(ITEM_INTRO, "Here's your PM briefing, updated today at 9:00 AM.")])
        self.assertEqual(script.run_label, "PM")

    def test_intro_without_run_or_time(self) -> None:
        script = build_script(synthetic(para("Hello")), now=NOW)
        self.assertEqual(self.intro_items(script), [(ITEM_INTRO, "Here's your briefing.")])
        self.assertIsNone(script.run_label)
        header = BriefingHeader(updated_raw="x", updated_at=datetime(2026, 10, 4, 9, 0, tzinfo=PDT))
        script = build_script(synthetic(header=header), now=NOW)
        self.assertEqual(self.intro_items(script),
                         [(ITEM_INTRO, "Here's your briefing, updated today at 9:00 AM.")])

    def test_updated_time_is_described_in_nows_zone(self) -> None:
        briefing = load_briefing()
        in_utc = replace(briefing, header=replace(briefing.header,
                                                  updated_at=briefing.header.updated_at.astimezone(UTC)))
        script = build_script(in_utc, now=NOW, expected_run="AM")
        self.assertEqual(script.sections[0].items[0].spoken, FRESH_INTRO)
        self.assertEqual(script.updated_label, "Updated today at 10:04 AM")

    def test_yesterday_is_stale(self) -> None:
        script = script_for(now=NEXT_MORNING, expected_run="AM")
        note = "Heads up: this briefing may be stale. It was last updated yesterday at 10:04 AM."
        # The note already says when; the intro does not repeat the time.
        self.assertEqual(self.intro_items(script), [
            (ITEM_NOTE, note),
            (ITEM_INTRO, "Here's your AM briefing."),
        ])
        self.assertEqual(script.sections[0].items[0].display, note)
        # The reading panel still shows the full intro sentence.
        self.assertEqual(script.sections[0].items[1].display,
                         "Here's your AM briefing, updated yesterday at 10:04 AM.")
        self.assertTrue(script.stale)
        self.assertEqual(script.updated_label, "Updated yesterday at 10:04 AM")
        self.assertEqual(spoken_transcript(script).splitlines()[:2], [note, "Here's your AM briefing."])
        self.assertEqual(spoken_transcript(script).count("10:04"), 1)

    def test_wrong_run_is_stale(self) -> None:
        script = script_for(now=SAME_NIGHT, expected_run="PM")
        self.assertEqual(script.sections[0].items[0].kind, ITEM_NOTE)
        self.assertEqual(
            script.sections[0].items[0].spoken,
            "Heads up: this may not be the PM briefing. The page is from the AM run, "
            "updated today at 10:04 AM.",
        )
        self.assertEqual(script.sections[0].items[1].spoken, "Here's your AM briefing.")
        self.assertTrue(script.stale)

    def test_stale_pm_page_intro_does_not_repeat_the_time(self) -> None:
        # The real page: written by the PM run at 11:47 PM, read the next day.
        header = BriefingHeader(updated_raw="x", updated_at=datetime(2026, 10, 3, 23, 47, tzinfo=PDT),
                                run="PM")
        freshness = check_freshness(header, None, NOW)
        script = build_script(synthetic(para("Hello"), header=header), now=NOW, freshness=freshness)
        self.assertEqual(spoken_transcript(script).splitlines()[:2], [
            "Heads up: this briefing may be stale. It was last updated yesterday at 11:47 PM.",
            "Here's your PM briefing.",
        ])
        # Without the note the intro keeps the time.
        script = build_script(synthetic(para("Hello"), header=header), now=NOW, freshness=freshness,
                              include_note=False)
        self.assertEqual(spoken_transcript(script).splitlines()[0],
                         "Here's your PM briefing, updated yesterday at 11:47 PM.")
        header = replace(header, updated_at=datetime(2026, 10, 4, 23, 47, tzinfo=PDT))
        script = build_script(synthetic(para("Hello"), header=header), now=SAME_NIGHT,
                              freshness=check_freshness(header, "PM", SAME_NIGHT), expected_run="PM")
        self.assertFalse(script.stale)
        self.assertEqual(spoken_transcript(script).splitlines()[0],
                         "Here's your PM briefing, updated today at 11:47 PM.")

    def test_wrong_run_when_page_has_no_run(self) -> None:
        header = BriefingHeader(updated_raw="x", updated_at=datetime(2026, 10, 4, 9, 12, tzinfo=PDT))
        freshness = Freshness(fresh=False, updated_today=True, run_matches=False)
        note = stale_note(header, freshness, "AM", NOW)
        self.assertTrue(note.startswith("Heads up: this may not be the AM briefing."))
        self.assertIn("updated today at 9:12 AM", note)

    def test_include_note_false_suppresses_note(self) -> None:
        script = script_for(now=NEXT_MORNING, expected_run="AM", include_note=False)
        self.assertNotIn(ITEM_NOTE, [item.kind for item in all_items(script)])
        self.assertFalse(script.stale)
        self.assertNotIn("Heads up", spoken_transcript(script))

    def test_no_freshness_means_no_note(self) -> None:
        briefing = load_briefing(now=NEXT_MORNING)
        script = build_script(briefing, now=NEXT_MORNING, expected_run="AM", freshness=None)
        self.assertFalse(script.stale)
        self.assertEqual(script.sections[0].items[0].kind, ITEM_INTRO)

    def test_unparseable_timestamp_note(self) -> None:
        header = BriefingHeader(updated_raw="sometime soon", updated_at=None, run="AM")
        freshness = check_freshness(header, "AM", NOW)
        note = stale_note(header, freshness, "AM", NOW)
        self.assertTrue(note.startswith("Heads up: this briefing may be stale."))
        self.assertNotIn("not been updated yet", note)


class PlaceholderPageTests(unittest.TestCase):
    def test_builds_intro_paragraph_and_outro(self) -> None:
        script = build_script(load_briefing(PLACEHOLDER_PAGE), now=NOW)
        self.assertEqual([s.key for s in script.sections], ["intro", "outro"])
        intro = script.sections[0]
        self.assertEqual([item.kind for item in intro.items], [ITEM_INTRO, ITEM_TEXT])
        self.assertEqual(intro.items[0].spoken, "Here's your briefing.")
        paragraph = intro.items[1]
        self.assertTrue(paragraph.display.startswith("This page is overwritten twice a day"))
        self.assertIn("The briefing-reader app on the PC reads it aloud.", paragraph.spoken)
        self.assertTrue(paragraph.spoken.endswith("Do not edit by hand."))
        self.assertEqual(script.updated_label, "Not updated yet")
        self.assertFalse(script.has_ignored)
        self.assertFalse(script.stale)

    def test_stale_note_says_not_updated_yet(self) -> None:
        script = script_for(PLACEHOLDER_PAGE, expected_run="AM")
        self.assertEqual([item.kind for item in script.sections[0].items],
                         [ITEM_NOTE, ITEM_INTRO, ITEM_TEXT])
        self.assertEqual(script.sections[0].items[0].spoken,
                         "Heads up: this briefing may be stale. The page has not been updated yet.")
        self.assertEqual(script.sections[0].items[1].spoken, "Here's your AM briefing.")
        self.assertTrue(script.stale)
        self.assertEqual(script.updated_label, "Not updated yet")


# --------------------------------------------------------------------------
# Script building on hand-made briefings
# --------------------------------------------------------------------------

class BuildScriptSyntheticTests(unittest.TestCase):
    def kinds(self, section: Section) -> list[str]:
        return [item.kind for item in section.items]

    def test_divider_becomes_a_single_pause_inside_sections_only(self) -> None:
        briefing = synthetic(
            DIVIDER_LINE, para("Intro text"), DIVIDER_LINE,
            heading("A"), DIVIDER_LINE, bullet("x"), DIVIDER_LINE, DIVIDER_LINE, bullet("y"), DIVIDER_LINE,
            heading("B"), bullet("z"), DIVIDER_LINE, FlatLine(CODE, "only code"), DIVIDER_LINE, bullet("w"),
        )
        script = build_script(briefing, now=NOW)
        intro, a, b = script.sections[0], script.sections[1], script.sections[2]
        self.assertEqual(self.kinds(intro), [ITEM_INTRO, ITEM_TEXT])
        self.assertEqual(self.kinds(a), [ITEM_HEADING, ITEM_ENTRY, ITEM_PAUSE, ITEM_ENTRY])
        self.assertEqual(self.kinds(b), [ITEM_HEADING, ITEM_ENTRY, ITEM_PAUSE, ITEM_TEXT, ITEM_PAUSE,
                                         ITEM_ENTRY])
        pause = a.items[2]
        self.assertEqual((pause.spoken, pause.display), ("", ""))

    def test_no_headings_gives_intro_and_outro(self) -> None:
        script = build_script(synthetic(para("Just a note"), bullet("and a bullet")), now=NOW)
        self.assertEqual([s.key for s in script.sections], ["intro", "outro"])
        self.assertEqual([item.spoken for item in script.sections[0].items],
                         ["Here's your briefing.", "Just a note.", "And a bullet."])

    def test_empty_body(self) -> None:
        script = build_script(synthetic(), now=NOW)
        self.assertEqual([s.key for s in script.sections], ["intro", "outro"])
        self.assertEqual(spoken_transcript(script), f"Here's your briefing.\n{OUTRO}")

    def test_section_level_is_the_smallest_heading_level(self) -> None:
        briefing = synthetic(
            heading("Main", 1), heading("Sub one", 2), bullet("a"),
            heading("Sub two", 3), bullet("b"), heading("Second", 1), bullet("c"),
        )
        script = build_script(briefing, now=NOW)
        self.assertEqual([s.title for s in script.sections], ["", "Main", "Second", ""])
        self.assertEqual(self.kinds(script.sections[1]),
                         [ITEM_HEADING, ITEM_SUBHEADING, ITEM_ENTRY, ITEM_SUBHEADING, ITEM_ENTRY])
        self.assertEqual(script.sections[1].items[1].spoken, "Sub one.")

    def test_nested_ignore_heading_gets_its_own_section(self) -> None:
        briefing = synthetic(
            heading("Inbox", 2), bullet("keep 1"),
            heading("Ignored (2)", 3), bullet("promo"), heading("Detail", 4), bullet("more promo"),
            heading("Later", 3), bullet("keep 2"),
            heading("Done", 2), bullet("keep 3"),
        )
        script = build_script(briefing, now=NOW)
        self.assertEqual([(s.title, s.ignored) for s in script.sections], [
            ("", False), ("Inbox", False), ("Ignored (2)", True), ("Later", False), ("Done", False),
            ("", False),
        ])
        self.assertEqual(self.kinds(script.sections[2]),
                         [ITEM_HEADING, ITEM_ENTRY, ITEM_SUBHEADING, ITEM_ENTRY])
        transcript = spoken_transcript(script)
        self.assertNotIn("promo", transcript.lower())
        self.assertIn("Keep 2.", transcript)

    def test_subheading_inside_ignored_section_stays_ignored(self) -> None:
        briefing = synthetic(heading("Ignore", 2), heading("Promos", 3), bullet("sale"),
                             heading("Next", 2), bullet("ok"))
        script = build_script(briefing, now=NOW)
        self.assertEqual([(s.title, s.ignored) for s in script.sections],
                         [("", False), ("Ignore", True), ("Next", False), ("", False)])
        self.assertNotIn("Sale", spoken_transcript(script))
        self.assertIn("Sale.", spoken_transcript(script, include_ignored=True))

    def test_count_wording_and_heading_forms(self) -> None:
        briefing = synthetic(heading("**Due** [1]"), heading("Done: 0"), heading("Inbox - 12"))
        script = build_script(briefing, now=NOW)
        self.assertEqual([(s.title, s.items[0].spoken) for s in script.sections[1:-1]], [
            ("Due (1)", "Due. 1 item."),
            ("Done (0)", "Done. No items."),
            ("Inbox (12)", "Inbox. 12 items."),
        ])

    def test_emoji_only_heading_is_skipped(self) -> None:
        briefing = synthetic(heading("Inbox"), bullet("a"), heading("\U0001F4E5"), bullet("b"))
        script = build_script(briefing, now=NOW)
        self.assertEqual([s.key for s in script.sections], ["intro", "s1", "outro"])
        self.assertEqual([item.spoken for item in script.sections[1].items], ["Inbox.", "A.", "B."])

    def test_lines_that_are_only_urls_or_emoji_are_dropped(self) -> None:
        briefing = synthetic(heading("Links"), bullet("https://example.com/x"), para(THUMBS_UP),
                             bullet("[](https://example.com)"), bullet("Real item"))
        section = build_script(briefing, now=NOW).sections[1]
        self.assertEqual([(item.display, item.spoken) for item in section.items],
                         [("Links", "Links."), ("Real item", "Real item.")])

    def test_checked_to_do_keeps_acronyms(self) -> None:
        briefing = synthetic(FlatLine(TO_DO, "PROJ-142 closed", checked=True),
                             FlatLine(TO_DO, "**Call** Bob", checked=False),
                             FlatLine(NUMBERED, "First", number=None))
        items = build_script(briefing, now=NOW).sections[0].items[1:]
        self.assertEqual([(item.display, item.spoken) for item in items], [
            ("[x] PROJ-142 closed", "Already done: PROJ-142 closed."),
            ("[ ] Call Bob", "Call Bob."),
            ("1. First", "First."),
        ])

    def test_keys_are_sequential(self) -> None:
        briefing = synthetic(*[heading(f"H{i}") for i in range(1, 5)])
        script = build_script(briefing, now=NOW)
        self.assertEqual([s.key for s in script.sections], ["intro", "s1", "s2", "s3", "s4", "outro"])


# --------------------------------------------------------------------------
# Section titles in other forms: a page title, paragraphs, toggles
# --------------------------------------------------------------------------

HEADER_BLOCKS = (block("paragraph", "Updated: 2026-10-04 22:31 PDT"), block("paragraph", "Run: PM"))
EVENING = datetime(2026, 10, 4, 22, 40, tzinfo=PDT)


class PageTitleTests(unittest.TestCase):
    def test_lone_top_heading_is_read_once_as_the_page_title(self) -> None:
        briefing = synthetic(
            heading(f"Email triage {DASH} Sun Oct 4, PM", 1),
            heading("Work inbox (1)", 2), bullet("Alice Chen"),
            heading("Due", 2), FlatLine(TO_DO, "Renew parking permit", checked=False),
            heading("Ignore (2)", 2), bullet("Promo: 20% off sneakers"), bullet("LinkedIn: 5 people"),
        )
        script = build_script(briefing, now=NOW)
        self.assertEqual([s.key for s in script.sections], ["intro", "s1", "s2", "s3", "outro"])
        self.assertEqual(titled_sections(script),
                         [("Work inbox (1)", False), ("Due", False), ("Ignore (2)", True)])
        intro = script.sections[0]
        self.assertEqual([item.kind for item in intro.items], [ITEM_INTRO, ITEM_SUBHEADING])
        self.assertEqual(intro.items[1].display, f"Email triage {DASH} Sun Oct 4, PM")
        transcript = spoken_transcript(script)
        self.assertEqual(transcript.count("Email triage."), 1)
        self.assertIn("Work inbox. 1 item.\nAlice Chen.\nDue.\nRenew parking permit.", transcript)
        self.assertNotIn("Promo", transcript)

    def test_no_page_title_unless_the_first_heading_alone_is_on_top(self) -> None:
        script = build_script(synthetic(heading("Main", 1), bullet("a")), now=NOW)
        self.assertEqual(titled_sections(script), [("Main", False)])
        cases = {
            "not first": (heading("Inbox", 2), heading("Big", 1), heading("Due", 2)),
            "known section name": (heading("Work inbox", 1), heading("Alice", 2), heading("Bob", 2)),
            "ignore heading": (heading("Ignore", 1), heading("Promo", 2), heading("Spam", 2)),
        }
        for name, lines in cases.items():
            with self.subTest(name):
                script = build_script(synthetic(*lines), now=NOW, announce={"Work inbox": "Work inbox"})
                self.assertEqual([item.kind for item in script.sections[0].items], [ITEM_INTRO])


class ParagraphTitleTests(unittest.TestCase):
    def test_bold_paragraphs_are_titles_on_a_page_without_headings(self) -> None:
        briefing = page(
            *HEADER_BLOCKS,
            block("paragraph", "Work inbox", bold=True),
            block("bulleted_list_item", f"Alice Chen {DASH} Q3 budget review"),
            block("paragraph", "Due", bold=True),
            block("to_do", "Renew parking permit", checked=False),
            block("paragraph", "Ignore (2)", bold=True),
            block("bulleted_list_item", "Promo: 20% off sneakers"),
            block("bulleted_list_item", "LinkedIn: 5 people viewed your profile"),
        )
        script = build_script(briefing, now=EVENING, expected_run="PM")
        self.assertEqual(titled_sections(script),
                         [("Work inbox", False), ("Due", False), ("Ignore (2)", True)])
        self.assertEqual(script.sections[2].items[0], ScriptItem(ITEM_HEADING, "Due.", "Due"))
        self.assertEqual(spoken_transcript(script).split("\n"), [
            "Here's your PM briefing, updated today at 10:31 PM.", "Work inbox.",
            "Alice Chen. Q3 budget review.", "Due.", "Renew parking permit.", OUTRO,
        ])
        self.assertIn("Ignore. 2 items.\nPromo: 20% off sneakers.",
                      spoken_transcript(script, include_ignored=True))

    def test_literal_bold_and_markdown_titles(self) -> None:
        for marked in ("**{}**", "__{}__", "## {}"):
            with self.subTest(marked=marked):
                briefing = synthetic(para(marked.format("Work inbox")), bullet("Alice"),
                                     para(marked.format("Ignore (1)")), bullet("Promo"))
                script = build_script(briefing, now=NOW)
                self.assertEqual(titled_sections(script), [("Work inbox", False), ("Ignore (1)", True)])
                self.assertNotIn("Promo", spoken_transcript(script))

    def test_markdown_headings_inside_one_paragraph(self) -> None:
        briefing = page(*HEADER_BLOCKS, block(
            "paragraph", "## Work inbox (1)\n- Alice Chen: Q3 budget\n## Ignore (2)\n- Promo: 20% off\n"
                         "- LinkedIn digest"))
        script = build_script(briefing, now=EVENING)
        self.assertEqual(titled_sections(script), [("Work inbox (1)", False), ("Ignore (2)", True)])
        self.assertEqual(spoken_transcript(script).split("\n")[1:-1],
                         ["Work inbox. 1 item.", "Alice Chen: Q3 budget."])

    def test_colon_titles_named_in_the_config(self) -> None:
        briefing = synthetic(para("Work inbox:"), bullet("Alice"), para("Due:"), bullet("Rent"),
                             para("Ignore:"), bullet("Promo"))
        script = build_script(briefing, now=NOW, announce={"Work inbox": "Work inbox", "Due": "Due"})
        self.assertEqual(titled_sections(script),
                         [("Work inbox", False), ("Due", False), ("Ignore", True)])
        # Without the announce names only the ignore title is recognised.
        script = build_script(briefing, now=NOW)
        self.assertEqual(titled_sections(script), [("Ignore", True)])
        self.assertIn("Work inbox.\nAlice.\nDue.\nRent.", spoken_transcript(script))

    def test_bold_lines_under_real_headings_stay_text(self) -> None:
        briefing = page(
            *HEADER_BLOCKS,
            block("heading_2", "Work inbox (2)"),
            block("paragraph", "Alice Chen", bold=True), block("bulleted_list_item", "Q3 budget"),
            block("paragraph", "## Bob"),
            block("heading_2", "Due"), block("to_do", "Pay rent", checked=False),
            block("paragraph", "Ignore (1)"), block("bulleted_list_item", "Promo"),
        )
        script = build_script(briefing, now=EVENING)
        self.assertEqual(titled_sections(script),
                         [("Work inbox (2)", False), ("Due", False), ("Ignore (1)", True)])
        work = script.sections[1]
        self.assertEqual([(item.kind, item.display) for item in work.items], [
            (ITEM_HEADING, "Work inbox (2)"), (ITEM_TEXT, "Alice Chen"), (ITEM_ENTRY, "Q3 budget"),
            (ITEM_TEXT, "Bob"),
        ])
        transcript = spoken_transcript(script)
        self.assertIn("Pay rent.", transcript)
        self.assertNotIn("Promo", transcript)

    def test_sentences_are_not_titles(self) -> None:
        briefing = page(
            *HEADER_BLOCKS,
            block("paragraph", "Ignore this for now"), block("bulleted_list_item", "keep me"),
            block("paragraph", "Call Bob about the lease today.", bold=True),
            block("paragraph", "A bold line that is far too long to be a section title", bold=True),
            block("bulleted_list_item", "still read"),
        )
        script = build_script(briefing, now=EVENING)
        self.assertEqual([s.key for s in script.sections], ["intro", "outro"])
        transcript = spoken_transcript(script)
        for spoken in ("Ignore this for now.", "Keep me.", "Call Bob about the lease today.",
                       "Still read."):
            self.assertIn(spoken, transcript)


class IgnoreToggleTests(unittest.TestCase):
    def test_ignore_toggle_is_an_ignored_section(self) -> None:
        briefing = page(
            *HEADER_BLOCKS,
            block("heading_2", "Work inbox (1)"), block("bulleted_list_item", "Alice"),
            block("heading_2", "Newsletters"), block("bulleted_list_item", "Morning Brew"),
            block("toggle", "Ignore (2)", children=[
                block("bulleted_list_item", "Promo: 20% off sneakers"),
                block("bulleted_list_item", "LinkedIn: 5 people viewed your profile",
                      children=[block("bulleted_list_item", "nested detail")]),
            ]),
        )
        script = build_script(briefing, now=EVENING)
        self.assertEqual(titled_sections(script),
                         [("Work inbox (1)", False), ("Newsletters", False), ("Ignore (2)", True)])
        self.assertEqual([(item.kind, item.display, item.depth) for item in script.sections[3].items], [
            (ITEM_HEADING, "Ignore (2)", 0),
            (ITEM_ENTRY, "Promo: 20% off sneakers", 0),
            (ITEM_ENTRY, "LinkedIn: 5 people viewed your profile", 0),
            (ITEM_ENTRY, "nested detail", 1),
        ])
        transcript = spoken_transcript(script)
        self.assertTrue(transcript.endswith(f"Newsletters.\nMorning Brew.\n{OUTRO}"))
        self.assertNotIn("Promo", transcript)
        self.assertIn("Ignore. 2 items.\nPromo: 20% off sneakers.\nLinkedIn: 5 people viewed your profile.",
                      spoken_transcript(script, include_ignored=True))

    def test_lines_after_the_toggle_are_read_again(self) -> None:
        briefing = page(
            *HEADER_BLOCKS,
            block("heading_2", "Newsletters"), block("bulleted_list_item", "Morning Brew"),
            block("toggle", "**Ignore** (2)", children=[block("bulleted_list_item", "Promo")]),
            block("bulleted_list_item", "TLDR AI"),
            block("heading_2", "Due"), block("to_do", "Pay rent", checked=False),
        )
        script = build_script(briefing, now=EVENING)
        self.assertEqual([(s.key, s.title, s.ignored) for s in script.sections], [
            ("intro", "", False), ("s1", "Newsletters", False), ("s2", "Ignore (2)", True),
            ("s3", "", False), ("s4", "Due", False), ("outro", "", False),
        ])
        transcript = spoken_transcript(script)
        self.assertIn("Morning Brew.\nTLDR AI.\nDue.\nPay rent.", transcript)
        self.assertNotIn("Promo", transcript)

    def test_ignore_callout_and_ordinary_toggles(self) -> None:
        briefing = page(
            *HEADER_BLOCKS,
            block("heading_2", "Actionable"),
            block("toggle", "Details", children=[block("bulleted_list_item", "Slides are in the Q3 folder")]),
            block("callout", "Ignored:", children=[block("paragraph", "Spam digest")]),
        )
        script = build_script(briefing, now=EVENING)
        self.assertEqual(titled_sections(script), [("Actionable", False), ("Ignored", True)])
        transcript = spoken_transcript(script)
        self.assertIn("Details.\nSlides are in the Q3 folder.", transcript)
        self.assertNotIn("Spam", transcript)

    def test_toggle_inside_an_ignored_section_keeps_it_ignored(self) -> None:
        briefing = synthetic(heading("Ignore (3)"), FlatLine(TOGGLE, "Ignore details"),
                             bullet("promo", 1), bullet("more promo"), heading("Due"), bullet("rent"))
        script = build_script(briefing, now=NOW)
        self.assertEqual(titled_sections(script), [("Ignore (3)", True), ("Due", False)])
        self.assertNotIn("promo", spoken_transcript(script).lower())
        self.assertIn("Rent.", spoken_transcript(script))


# --------------------------------------------------------------------------
# strip_markdown
# --------------------------------------------------------------------------

class StripMarkdownTests(unittest.TestCase):
    def check(self, cases: dict[str, str]) -> None:
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(strip_markdown(raw), expected)

    def test_empty_and_whitespace(self) -> None:
        self.check({"": "", "   ": "", "\n\t": "", "a \n  b": "a b"})

    def test_emphasis(self) -> None:
        self.check({
            "**bold** text": "bold text",
            "__bold__ text": "bold text",
            "*italic* text": "italic text",
            "TLDR AI: _three_ new model releases": "TLDR AI: three new model releases",
            "***both***": "both",
            "~~struck~~ text": "struck text",
            "`code` here": "code here",
            "**unclosed bold": "unclosed bold",
        })

    def test_things_that_are_not_emphasis_are_kept(self) -> None:
        self.check({
            "snake_case_name": "snake_case_name",
            "my_var and _private_var": "my_var and _private_var",
            "5 * 3 = 15": "5 * 3 = 15",
            "2*3*4": "2*3*4",
            "Note*": "Note*",
            "\\*literal\\*": "*literal*",
        })

    def test_links_images_and_urls(self) -> None:
        self.check({
            "Attached: [budget.xlsx](https://example.com/budget.xlsx)": "Attached: budget.xlsx",
            "![logo](https://example.com/logo.png) Acme": "logo Acme",
            "[Wiki](https://en.wikipedia.org/wiki/Foo_(bar)) page": "Wiki page",
            "https://example.com": "",
            "www.example.com/path": "",
            "see (https://example.com) now": "see now",
            "Zoom: https://zoom.us/j/123": "Zoom",
            "Tue 2pm | Zoom https://zoom.us/j/123": "Tue 2pm | Zoom",
            "Done. https://example.com.": "Done.",
            "Call Bob, https://example.com, today": "Call Bob, today",
            "(https://a.example) (https://b.example)": "",
        })

    def test_leading_markers(self) -> None:
        self.check({
            "- item": "item", "* item": "item", "+ item": "item", "\u2022 item": "item",
            "1. item": "item", "2) item": "item", "> quote": "quote",
            "# Title": "Title", "### Title": "Title", "[ ] task": "task", "[x] task": "task",
            "- [ ] task": "task", "> - [x] nested": "nested", "---": "", "* * *": "",
            "#1 priority": "#1 priority", "10.5% growth": "10.5% growth", "-5 degrees": "-5 degrees",
        })

    def test_html(self) -> None:
        self.check({
            "<b>bold</b> text<br/>more": "bold text more",
            "<!-- hidden --> shown": "shown",
            "Tom &amp; Jerry": "Tom & Jerry",
            "Alice <alice@example.com>": "Alice <alice@example.com>",
            "a < b > c": "a < b > c",
        })

    def test_emoji(self) -> None:
        self.check({
            f"Bob already replied {THUMBS_UP}": "Bob already replied",
            f"{WASTEBASKET} Ignore (14)": "Ignore (14)",
            "\U0001F525 Hot \U0001F44D\U0001F3FD deal \u2705": "Hot deal",
            "\U0001F1FA\U0001F1F8 flag": "flag",
            "\U0001F468\u200d\U0001F469\u200d\U0001F467 family": "family",
            "1\ufe0f\u20e3 keycap": "1 keycap",
            "\U0001F4CC \u2014 Note": "Note",
            "\U0001F680": "",
        })

    def test_everyday_text_survives(self) -> None:
        self.check({
            "Pay $84.20 by Fri": "Pay $84.20 by Fri",
            "20% off": "20% off",
            "PROJ-142 and Q3 at 2pm": "PROJ-142 and Q3 at 2pm",
            "re: lease, cc: Bob": "re: lease, cc: Bob",
            "new sign-in; Follow-ups": "new sign-in; Follow-ups",
            f"Morning Brew {DASH} markets up": f"Morning Brew {DASH} markets up",
            "U.S. economy, .NET 8": "U.S. economy, .NET 8",
        })

    def test_leftover_punctuation_is_tidied(self) -> None:
        self.check({"end. .": "end.", "end..": "end.", "Wait...": "Wait...", "Notes ( )": "Notes",
                    "Item -": "Item", "Item |": "Item", "C-": "C-"})


# --------------------------------------------------------------------------
# to_spoken
# --------------------------------------------------------------------------

class ToSpokenTests(unittest.TestCase):
    def check(self, cases: dict[str, str]) -> None:
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(to_spoken(raw), expected)

    def test_nothing_to_say(self) -> None:
        self.check({"": "", "   ": "", "https://example.com/x": "", THUMBS_UP: "", "---": "",
                    "[](https://example.com)": "", " | ": "", "...": ""})

    def test_splits_into_short_sentences(self) -> None:
        self.check({
            f"**Alice Chen** {DASH} Q3 budget review {DASH} wants numbers by Fri":
                "Alice Chen. Q3 budget review. Wants numbers by Friday.",
            "Alice Chen\u2014Q3 budget": "Alice Chen. Q3 budget.",
            "One \u2013 two": "One. Two.",
            "One - two": "One. Two.",
            "Who | What": "Who. What.",
            "a; b; c": "A. B. C.",
            "Tue \u00b7 2pm": "Tuesday. 2pm.",
            "word--word": "Word. Word.",
        })

    def test_hyphenated_words_are_not_split(self) -> None:
        self.check({
            "Follow-ups for the new sign-in": "Follow-ups for the new sign-in.",
            "PROJ-142 assigned": "PROJ-142 assigned.",
            "-5 degrees": "-5 degrees.",
        })

    def test_spaced_dash_ranges_read_to(self) -> None:
        self.check({
            "Invitation: Design sync @ Wed Oct 7, 2026 2pm - 3pm (PDT)":
                "Invitation: Design sync @ Wednesday October 7, 2026 2pm to 3pm (PDT).",
            "Office hours 9:00 AM – 5:00 PM": "Office hours 9:00 AM to 5:00 PM.",
            "Standup 10:30 – 11:00 AM tomorrow": "Standup 10:30 to 11:00 AM tomorrow.",
            "Lunch 12 - 1pm": "Lunch 12 to 1pm.",
            "Budget range $40 - $60 per seat": "Budget range $40 to $60 per seat.",
            "Conference Oct 12 - 15 in Austin": "Conference October 12 to 15 in Austin.",
            "Trip Oct 30 - Nov 2": "Trip October 30 to November 2.",
        })

    def test_spaced_dash_between_other_values_still_splits(self) -> None:
        self.check({
            "Invoice 4411 - 3 days overdue": "Invoice 4411. 3 days overdue.",
            "Invoice #1234 - 2 days overdue": "Invoice #1234. 2 days overdue.",
            "Acme invoice - due Friday": "Acme invoice. Due Friday.",
            "Q3 review at 2pm - bring slides": "Q3 review at 2pm. Bring slides.",
            "Due Oct 6 - 3 left": "Due October 6. 3 left.",
            "10 - 20 people": "10. 20 people.",
            f"2pm {DASH} 3pm": "2pm. 3pm.",
        })

    def test_symbols_spelled_out(self) -> None:
        self.check({
            "A -> B": "A to B.",
            "A => B": "A to B.",
            "A \u2192 B": "A to B.",
            "A \u27a1\ufe0f B": "A to B.",
            "Tom & Jerry": "Tom and Jerry.",
            "Q&A": "Q and A.",
            "Lunch w/ Sam w/o Bob": "Lunch with Sam without Bob.",
            "Fruit, e.g. apples": "Fruit, for example apples.",
            "Fruit, i.e. apples": "Fruit, that is apples.",
            "Apples, pears, etc.": "Apples, pears, et cetera.",
            "and/or": "And/or.",
        })

    def test_arrows_read_as_to_or_as_a_sentence_break(self) -> None:
        self.check({
            # Short "from -> to" pairs and value changes read "to".
            "Status: open -> closed": "Status: open to closed.",
            "Meeting moved 2pm -> 3pm": "Meeting moved 2pm to 3pm.",
            "Price drop: $499 \u2192 $379": "Price drop: $499 to $379.",
            "Seattle \u2192 Portland on Tue": "Seattle to Portland on Tuesday.",
            "A -> B -> C": "A to B to C.",
            # An arrow that introduces the next step or a consequence splits.
            "Jira: PROJ-142 assigned to you -> please triage":
                "Jira: PROJ-142 assigned to you. Please triage.",
            "Invoice #4471 overdue \u2192 pay by Fri": "Invoice #4471 overdue. Pay by Friday.",
            "Q3 budget review -> due Fri": "Q3 budget review. Due Friday.",
            "Alice -> action: send the deck": "Alice. Action: send the deck.",
            # Leading and trailing arrows are dropped, never read as "to".
            "\u2192 Reply by Friday": "Reply by Friday.",
            "-> **Reply** today": "Reply today.",
            "Action: \u2192 reply": "Action: reply.",
            "Next step ->": "Next step.",
        })

    def test_weekday_and_month_abbreviations_are_spelled_out(self) -> None:
        self.check({
            "Wants numbers by Fri": "Wants numbers by Friday.",
            "Design sync, Tue 2pm": "Design sync, Tuesday 2pm.",
            "1:1 moved to Thu": "1:1 moved to Thursday.",
            "Due Fri. 10/6": "Due Friday 10/6.",
            "Mon\u2013Fri": "Monday to Friday.",
            "Open Mon-Fri. Closed weekends": "Open Monday to Friday. Closed weekends.",
            "Tue/Thu 2pm": "Tuesday and Thursday 2pm.",
            "Sat & Sun": "Saturday and Sunday.",
            # Wed / Sat / Sun are also words: only expanded with a date-like context.
            "Wed 3pm standup": "Wednesday 3pm standup.",
            "RSVP by Wed": "RSVP by Wednesday.",
            "Brunch on Sun.": "Brunch on Sunday.",
            "Sat with Bob to review the plan": "Sat with Bob to review the plan.",
            "The Sun reports record sales": "The Sun reports record sales.",
            "Wed in June": "Wed in June.",
            # Months only when a day or year follows.
            "Renew permit (expires Oct 6)": "Renew permit (expires October 6).",
            "Sept 30 deadline": "September 30 deadline.",
            "Since Dec. 20, 2025": "Since December 20, 2025.",
            "May 5 dentist": "May 5 dentist.",
            "Mar the finish": "Mar the finish.",
            "Monday, Friday and Tuesday stay": "Monday, Friday and Tuesday stay.",
        })

    def test_email_shorthand_and_subject_prefixes(self) -> None:
        self.check({
            "cc: Bob": "CC: Bob.",
            "bcc: Carol": "BCC: Carol.",
            "fyi the meeting moved": "FYI the meeting moved.",
            "Need this asap": "Need this ASAP.",
            "Reply by eod": "Reply by EOD.",
            "Reply to landlord re: lease": "Reply to landlord about lease.",
            "Alice, re: lease": "Alice, about lease.",
            "Re: Q3 budget": "Q3 budget.",
            "RE: Fwd: Invoice": "Invoice.",
            "Fw: lunch?": "Lunch?",
            f"Alice Chen {DASH} Re: Q3 budget": "Alice Chen. Q3 budget.",
            "Subject: Fwd: Invoice": "Subject: Invoice.",
            # Words that merely contain the letters are left alone.
            "Reorg: details; beta access; accepted": "Reorg: details. Beta access. Accepted.",
            "cc'd Bob": "Cc'd Bob.",
        })

    def test_everyday_tokens_survive(self) -> None:
        self.check({
            f"Pay electricity bill {DASH} $84.20": "Pay electricity bill. $84.20.",
            "Promo: 20% off sneakers": "Promo: 20% off sneakers.",
            "Q3 review at 2pm": "Q3 review at 2pm.",
            "2pm sync": "2pm sync.",
            "iPhone update": "iPhone update.",
            "snake_case_name stays": "Snake_case_name stays.",
            "AWS bill: $1,204.33 (up 12%)": "AWS bill: $1,204.33 (up 12%).",
            "Approve PTO request for Dave": "Approve PTO request for Dave.",
        })

    def test_sentence_endings(self) -> None:
        self.check({
            "Why?": "Why?",
            "Done!": "Done!",
            "Already ended.": "Already ended.",
            "Attached:": "Attached.",
            "trailing comma,": "Trailing comma.",
            '"Can you send the slides before Monday?" \u2014 Dave':
                '"Can you send the slides before Monday?" Dave.',
            "\u201cCurly?\u201d \u2014 Dave": '"Curly?" Dave.',
            "Renew parking permit (expires **Oct 6**)": "Renew parking permit (expires October 6).",
        })

    def test_no_stray_punctuation_after_url_removal(self) -> None:
        self.check({
            "Notes (https://example.com)": "Notes.",
            "Done. https://example.com.": "Done.",
            "Zoom: https://zoom.us/j/1": "Zoom.",
            "Calendar invite: Design sync, Tue 2pm | Zoom https://zoom.us/j/123":
                "Calendar invite: Design sync, Tuesday 2pm. Zoom.",
            "Link - https://example.com": "Link.",
            "Wait... what": "Wait, what.",
            "Wait\u2026": "Wait.",
            "Alice <alice@example.com> \u2014 lunch": "Alice. Lunch.",
        })

    def test_never_produces_double_periods(self) -> None:
        for raw in ("end. .", "end..", "a... b...", "x. https://e.com. y.", "Done.. really..",
                    "One. - Two.", "etc.", "e.g."):
            with self.subTest(raw=raw):
                spoken = to_spoken(raw)
                self.assertNotIn("..", spoken)
                self.assertNotIn(". .", spoken)

    def test_email_only_parentheticals_are_dropped(self) -> None:
        self.check({
            "Work (first.last@example.edu)": "Work.",
            "Personal (someone.name42@example.com)": "Personal.",
            "Work ( first.last@example.edu )": "Work.",
            "Shared inboxes (a@example.com, b@example.org)": "Shared inboxes.",
            "Ping Bob (bob@example.com) about the lease": "Ping Bob about the lease.",
            "Ping Bob (bob@example.com).": "Ping Bob.",
            "Invite from Carol (carol@example.edu), accepted": "Invite from Carol, accepted.",
            # No dangling separator is left behind.
            "Work - (first.last@example.edu)": "Work.",
            # Addresses that are part of the sentence stay.
            "Write to bob@example.com today": "Write to bob@example.com today.",
            "Notes (email bob@example.com)": "Notes (email bob@example.com).",
            "Bob (the landlord)": "Bob (the landlord).",
            # An address that is the object of the sentence stays too.
            "Reply to (registrar@example.edu) about the hold":
                "Reply to (registrar@example.edu) about the hold.",
            "Forward it to (bob@example.com).": "Forward it to (bob@example.com).",
        })

    def test_weekday_before_a_month_is_spelled_out(self) -> None:
        self.check({
            "Sun Oct 4, dinner": "Sunday October 4, dinner.",
            "Sun October 4": "Sunday October 4.",
            "Sat Oct 10": "Saturday October 10.",
            "Wed, Oct 7": "Wednesday, October 7.",
            "Wed Oct 7, 2026": "Wednesday October 7, 2026.",
            "Sun. Oct 4 brunch": "Sunday October 4 brunch.",
            "Fri. Oct 9": "Friday October 9.",
            "Mon Oct 5": "Monday October 5.",
            "Thu, Nov 12": "Thursday, November 12.",
            "Sat May 9": "Saturday May 9.",
            "Sat March 7": "Saturday March 7.",
            # Word-like abbreviations without a date stay words.
            "The Sun May rise": "The Sun May rise.",
            "Sun with Bob": "Sun with Bob.",
            "Wed in June": "Wed in June.",
            "Brunch on Sun. October is busy": "Brunch on Sunday. October is busy.",
            # A month without a day number does not make a date ("Sun Jun" is a name).
            "Meeting with Sun Jun about the lab": "Meeting with Sun Jun about the lab.",
            "Wed Oct": "Wed Oct.",
        })

    def test_month_before_a_day_and_a_colon(self) -> None:
        self.check({
            "Monday Oct 5: HIST essay draft": "Monday October 5: HIST essay draft.",
            "Due Oct 5:": "Due October 5.",
            "Tuesday Oct 6: midterm 3 PM": "Tuesday October 6: midterm 3 PM.",
            # A colon that starts a time is not a label.
            "Oct 5:30": "Oct 5:30.",
            "Meet at 5:30": "Meet at 5:30.",
        })

    def test_unspaced_ranges_read_to(self) -> None:
        en = "–"
        self.check({
            "Sun Oct 4, 6:30-7:45 PM: dinner": "Sunday October 4, 6:30 to 7:45 PM: dinner.",
            "Film Club General Meeting, Tuesday Oct 6, 5-6 PM.":
                "Film Club General Meeting, Tuesday October 6, 5 to 6 PM.",
            "Weekly meeting, Fridays 3-4 PM, starting Oct 9":
                "Weekly meeting, Fridays 3 to 4 PM, starting October 9.",
            "Standup 10:30am-11am": "Standup 10:30am to 11am.",
            "Lunch 11 a.m.-1 p.m. today": "Lunch 11 a.m. to 1 p.m. today.",
            "Shift 9:00-17:00": "Shift 9:00 to 17:00.",
            "Tickets $40-$60": "Tickets $40 to $60.",
            f"Shift 6:30{en}7:45 PM": "Shift 6:30 to 7:45 PM.",
            f"Shift 5{en}6 PM": "Shift 5 to 6 PM.",
            f"Tickets $40{en}$60": "Tickets $40 to $60.",
            "6:30-7:45 PM Dinner with Sam": "6:30 to 7:45 PM Dinner with Sam.",
        })

    def test_unspaced_day_money_and_noon_ranges_read_to(self) -> None:
        en = "–"
        self.check({
            "Conference Oct 12-15 in Austin": "Conference October 12 to 15 in Austin.",
            "Trip Oct 30-Nov 2": "Trip October 30 to November 2.",
            "May 5-6 finals": "May 5 to 6 finals.",
            "Tickets $40-60 each": "Tickets $40 to $60 each.",
            "Cover $5-10": "Cover $5 to $10.",
            "Rent $1,200-1,500": "Rent $1,200 to $1,500.",
            "Paid $20-25/hr": "Paid $20 to $25/hr.",
            f"Tickets $40{en}60": "Tickets $40 to $60.",
            "Lunch noon-1pm": "Lunch noon to 1pm.",
            "Lunch noon - 1:30 PM": "Lunch noon to 1:30 PM.",
            "Lunch 11am-noon": "Lunch 11am to noon.",
            "Lunch 11:30 AM - noon": "Lunch 11:30 AM to noon.",
            "Party 9pm-midnight": "Party 9pm to midnight.",
            # Not ranges: falling pairs, other units, a count after "noon".
            "Due Oct 6-3 left": "Due October 6-3 left.",
            "Refund $10-5": "Refund $10-5.",
            "Raise $5-10k": "Raise $5-10k.",
            "Lunch at noon - 2 people confirmed": "Lunch at noon. 2 people confirmed.",
            "Room 12 - noon meeting": "Room 12. Noon meeting.",
        })

    def test_unspaced_dashes_that_are_not_ranges_stay(self) -> None:
        for raw in ("BIO-2 Reading Assignment 5", "PROJ-142 assigned", "COVID-19 test", "On 2026-10-04",
                    "Call 555-555-0123", "Call 1-800-555-1234", "New sign-in", "Follow-ups",
                    "Final score 3-2", "Released v2-3", "Ages 5-12", "Room 3-4", "Stamp T10:00-07:00",
                    "Rows 25-26 pm"):
            with self.subTest(raw=raw):
                spoken = to_spoken(raw)
                self.assertNotIn(" to ", spoken)
                self.assertEqual(spoken, raw + ".")

    def test_street_direction_and_short_numbers(self) -> None:
        self.check({
            "Dinner, 212 W Example Way, Springfield":
                "Dinner, 212 West Example Way, Springfield.",
            "Pick up at 100 N. 5th Ave": "Pick up at 100 North 5th Ave.",
            "Office 40 E Main St": "Office 40 East Main St.",
            "HIST Essay #1 first draft": "HIST Essay number 1 first draft.",
            "Problem Set #3 target": "Problem Set number 3 target.",
            "#1 priority": "Number 1 priority.",
            # Not a street, or an id: left alone.
            "Section 5 W Building": "Section 5 W Building.",
            "Form 5 S Corp": "Form 5 S Corp.",
            "Invoice #4471 overdue": "Invoice #4471 overdue.",
            "C# 10 notes": "C# 10 notes.",
        })


class AccountSectionFormatTests(unittest.TestCase):
    """A typical briefing layout: account sections named with their address."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.briefing = synthetic(
            para("Due tomorrow (Sunday Oct 4) and right after:"),
            bullet("Sun Oct 4, 6:30-7:45 PM: Dinner, 212 W Example Way, Springfield."),
            bullet("Monday Oct 5: HIST Essay #1 first draft and BIO-2 Reading Assignment 5 due 11:59 PM."),
            heading("Work (first.last@example.edu)", 2),
            heading("Due", 3),
            bullet("Club October General Meeting, Tuesday Oct 6, 5-6 PM."),
            bullet("Weekly Meeting, Fridays 3-4 PM, starting Oct 9."),
            heading("Ignore", 3),
            bullet("Promo email"),
            heading("Personal (someone.name42@example.com)", 2),
            heading("Due", 3),
            bullet("Pick up groceries, Sun Oct 4, 3:30-4:05 PM."),
            heading("Tomorrow's calendar (Sunday Oct 4)", 2),
            bullet("6:30-7:45 PM Dinner, Springfield."),
        )
        cls.script = build_script(cls.briefing, now=NOW)
        cls.lines = spoken_transcript(cls.script).splitlines()

    def test_account_headings_are_spoken_without_their_address(self) -> None:
        self.assertIn("Work.", self.lines)
        self.assertIn("Personal.", self.lines)
        self.assertNotIn("@", "\n".join(self.lines))
        # The reading panel still shows the address.
        titles = [section.title for section in self.script.sections]
        self.assertIn("Work (first.last@example.edu)", titles)
        self.assertIn("Personal (someone.name42@example.com)", titles)

    def test_dates_times_and_addresses_read_naturally(self) -> None:
        self.assertEqual(self.lines[1:], [
            "Due tomorrow (Sunday October 4) and right after.",
            "Sunday October 4, 6:30 to 7:45 PM: Dinner, 212 West Example Way, Springfield.",
            "Monday October 5: HIST Essay number 1 first draft and BIO-2 Reading Assignment 5 due "
            "11:59 PM.",
            "Work.",
            "Due.",
            "Club October General Meeting, Tuesday October 6, 5 to 6 PM.",
            "Weekly Meeting, Fridays 3 to 4 PM, starting October 9.",
            "Personal.",
            "Due.",
            "Pick up groceries, Sunday October 4, 3:30 to 4:05 PM.",
            "Tomorrow's calendar (Sunday October 4).",
            "6:30 to 7:45 PM Dinner, Springfield.",
            OUTRO,
        ])

    def test_display_text_is_unchanged(self) -> None:
        displays = [item.display for section in self.script.sections for item in section.items]
        self.assertIn("Sun Oct 4, 6:30-7:45 PM: Dinner, 212 W Example Way, Springfield.", displays)
        self.assertIn("Monday Oct 5: HIST Essay #1 first draft and BIO-2 Reading Assignment 5 due "
                      "11:59 PM.", displays)
        self.assertIn("Club October General Meeting, Tuesday Oct 6, 5-6 PM.", displays)


# --------------------------------------------------------------------------
# Headings
# --------------------------------------------------------------------------

class CleanHeadingTests(unittest.TestCase):
    def test_counts(self) -> None:
        cases = {
            "Work inbox (3)": ("Work inbox", 3),
            "Work inbox (3 items)": ("Work inbox", 3),
            f"{WASTEBASKET} Ignore (14)": ("Ignore", 14),
            "**Newsletters** [5]": ("Newsletters", 5),
            "Actionable - 2": ("Actionable", 2),
            "Due: 4": ("Due", 4),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_heading(raw), expected)

    def test_without_counts(self) -> None:
        cases = {
            "Due": ("Due", None),
            "## Due": ("Due", None),
            "Top 10": ("Top 10", None),
            "Q3": ("Q3", None),
            "Follow-ups": ("Follow-ups", None),
            "Work inbox:": ("Work inbox", None),
            "": ("", None),
            "\U0001F4E5": ("", None),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_heading(raw), expected)

    def test_count_only_heading_keeps_its_text(self) -> None:
        title, count = clean_heading("(3)")
        self.assertTrue(title)
        self.assertIsNone(count)


class IsIgnoreHeadingTests(unittest.TestCase):
    def test_matches(self) -> None:
        for title in ("Ignore", "ignore", "IGNORE", "Ignored", "Ignore (14)", "Ignore - promos",
                      f"{WASTEBASKET} Ignore", "Ignore: newsletters"):
            with self.subTest(title=title):
                self.assertTrue(is_ignore_heading(title, ("Ignore",)))

    def test_non_matches(self) -> None:
        for title in ("Newsletters", "Not ignored", "Work inbox", ""):
            with self.subTest(title=title):
                self.assertFalse(is_ignore_heading(title, ("Ignore",)))
        self.assertFalse(is_ignore_heading("Ignore", ()))
        self.assertFalse(is_ignore_heading("Ignore", ("",)))

    def test_custom_names(self) -> None:
        names = ("Promotions", "Low priority")
        self.assertTrue(is_ignore_heading("promotions (9)", names))
        self.assertTrue(is_ignore_heading("Low-priority", names))
        self.assertFalse(is_ignore_heading("Ignore", names))


# --------------------------------------------------------------------------
# Time labels
# --------------------------------------------------------------------------

class FormatTimeTests(unittest.TestCase):
    def test_formats(self) -> None:
        cases = {(10, 4): "10:04 AM", (23, 31): "11:31 PM", (0, 0): "12:00 AM", (12, 0): "12:00 PM",
                 (9, 5): "9:05 AM", (12, 30): "12:30 PM", (0, 59): "12:59 AM", (13, 0): "1:00 PM"}
        for (hour, minute), expected in cases.items():
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(format_time(datetime(2026, 10, 4, hour, minute, tzinfo=PDT)), expected)


class DescribeUpdatedTests(unittest.TestCase):
    def at(self, month: int, day: int, hour: int, minute: int, year: int = 2026,
           tz: timezone = PDT) -> datetime:
        return datetime(year, month, day, hour, minute, tzinfo=tz)

    def test_none(self) -> None:
        self.assertEqual(describe_updated(None, NOW), "")

    def test_today_and_yesterday(self) -> None:
        self.assertEqual(describe_updated(self.at(10, 4, 10, 4), NOW), "today at 10:04 AM")
        self.assertEqual(describe_updated(self.at(10, 4, 0, 0), NOW), "today at 12:00 AM")
        self.assertEqual(describe_updated(self.at(10, 3, 23, 31), self.at(10, 4, 0, 10)),
                         "yesterday at 11:31 PM")

    def test_weekday_for_two_to_six_days(self) -> None:
        # 2026-10-04 is a Sunday.
        self.assertEqual(describe_updated(self.at(10, 2, 9, 0), NOW), "on Friday at 9:00 AM")
        self.assertEqual(describe_updated(self.at(10, 1, 9, 0), NOW), "on Thursday at 9:00 AM")
        self.assertEqual(describe_updated(self.at(9, 28, 12, 0), NOW), "on Monday at 12:00 PM")

    def test_older_and_future_use_the_date(self) -> None:
        self.assertEqual(describe_updated(self.at(9, 27, 9, 0), NOW), "on Sep 27 at 9:00 AM")
        self.assertEqual(describe_updated(self.at(10, 5, 8, 0), NOW), "on Oct 5 at 8:00 AM")
        self.assertEqual(describe_updated(self.at(12, 20, 9, 0, year=2025), NOW),
                         "on Dec 20, 2025 at 9:00 AM")

    def test_converted_to_nows_zone(self) -> None:
        self.assertEqual(describe_updated(self.at(10, 4, 17, 4, tz=UTC), NOW), "today at 10:04 AM")
        # 05:00 UTC on Oct 5 is still Oct 4 in Pacific time.
        self.assertEqual(describe_updated(self.at(10, 5, 5, 0, tz=UTC), self.at(10, 4, 23, 0)),
                         "today at 10:00 PM")

    def test_naive_values_do_not_crash(self) -> None:
        self.assertTrue(describe_updated(datetime(2026, 10, 4, 10, 4), NOW).endswith("10:04 AM"))
        self.assertTrue(describe_updated(self.at(10, 4, 10, 4), datetime(2026, 10, 4, 11, 0)))


class UpdatedLabelTests(unittest.TestCase):
    def test_labels(self) -> None:
        parsed = BriefingHeader(updated_raw="2026-10-04 10:04 PDT",
                                updated_at=datetime(2026, 10, 4, 10, 4, tzinfo=PDT), run="AM")
        self.assertEqual(updated_label(parsed, NOW), "Updated today at 10:04 AM")
        self.assertEqual(updated_label(parsed, NEXT_MORNING), "Updated yesterday at 10:04 AM")
        self.assertEqual(updated_label(BriefingHeader(), NOW), "Not updated yet")
        self.assertEqual(updated_label(BriefingHeader(updated_raw="never"), NOW), "Not updated yet")
        self.assertEqual(updated_label(BriefingHeader(updated_raw="**Never**"), NOW), "Not updated yet")
        self.assertEqual(updated_label(BriefingHeader(updated_raw="sometime soon"), NOW),
                         "Updated: sometime soon")


# --------------------------------------------------------------------------
# Pending calendar proposals ("Needs your OK")
# --------------------------------------------------------------------------

CHESS_LINE = ("Calendar: Chess Club Weekly Meeting | 2026-10-09 15:00-16:00 | weekly until 2026-12-11 "
            "| https://meet.google.com/aaa-bbbb-ccc | Carol's team sync")
FILM_LINE = "Calendar: Film Club October General Meeting | 2026-10-06 17:00-18:00 | | Central Library |"
ESSAY_LINE = "Calendar: HIST Essay #1 draft due | 2026-10-05 |  |  | first draft + peer review"
MIDDLE_DOT = "\u00b7"


class ActionsSectionTests(unittest.TestCase):
    def build(self, *lines: str, now: datetime = NOW, **kwargs) -> Script:
        actions = [parse_action_line(line) for line in lines]
        return script_for(now=now, actions=actions, **kwargs)

    def actions_section(self, script: Script) -> Section:
        sections = [s for s in script.sections if s.key == ACTIONS_KEY]
        self.assertEqual(len(sections), 1, [s.key for s in script.sections])
        return sections[0]

    def test_no_actions_leaves_the_script_unchanged(self) -> None:
        plain = script_for()
        self.assertEqual(script_for(actions=()), plain)
        self.assertEqual(script_for(actions=[]), plain)
        self.assertNotIn(ACTIONS_KEY, [s.key for s in plain.sections])

    def test_section_comes_right_before_the_outro(self) -> None:
        plain = script_for()
        script = self.build(CHESS_LINE)
        self.assertEqual(script.sections[:-2], plain.sections[:-1])
        self.assertEqual([s.key for s in script.sections[-2:]], [ACTIONS_KEY, "outro"])
        self.assertEqual(script.sections[-1], plain.sections[-1])

    def test_one_action(self) -> None:
        section = self.actions_section(self.build(CHESS_LINE))
        self.assertEqual(section.title, ACTIONS_TITLE)
        self.assertEqual(section.title, "Needs your OK")
        self.assertFalse(section.ignored)
        self.assertEqual(section.items, (
            ScriptItem(ITEM_HEADING, "One calendar invite needs your OK.", "Needs your OK"),
            ScriptItem(ITEM_ENTRY,
                       "Chess Club Weekly Meeting, Friday October 9, 3 to 4 PM, weekly until December 11.",
                       f"Chess Club Weekly Meeting - Fri Oct 9 {MIDDLE_DOT} 3:00-4:00 PM {MIDDLE_DOT} "
                       "weekly until Dec 11"),
            ScriptItem(ITEM_TEXT, "Approve or deny them on the right.", ""),
        ))
        self.assertEqual(ACTIONS_CLOSING, "Approve or deny them on the right.")

    def test_several_actions_keep_their_order(self) -> None:
        section = self.actions_section(self.build(CHESS_LINE, FILM_LINE, ESSAY_LINE))
        self.assertEqual(section.items[0].spoken, "Three calendar invites need your OK.")
        entries = [item for item in section.items if item.kind == ITEM_ENTRY]
        self.assertEqual([item.display for item in entries], [
            f"Chess Club Weekly Meeting - Fri Oct 9 {MIDDLE_DOT} 3:00-4:00 PM {MIDDLE_DOT} weekly until Dec 11",
            f"Film Club October General Meeting - Tue Oct 6 {MIDDLE_DOT} 5:00-6:00 PM",
            f"HIST Essay #1 draft due - Mon Oct 5 {MIDDLE_DOT} all day",
        ])
        self.assertEqual(entries[2].spoken, "HIST Essay number 1 draft due, Monday October 5, all day.")
        self.assertEqual(section.items[-1].kind, ITEM_TEXT)

    def test_count_words(self) -> None:
        expected = {2: "Two calendar invites need your OK.", 9: "Nine calendar invites need your OK.",
                    10: "10 calendar invites need your OK.", 12: "12 calendar invites need your OK."}
        for count, sentence in expected.items():
            lines = [f"Calendar: Event {n} | 2026-10-{10 + n:02d} 09:00" for n in range(count)]
            with self.subTest(count=count):
                section = self.actions_section(self.build(*lines))
                self.assertEqual(section.items[0].spoken, sentence)
                self.assertEqual(len(section.items), count + 2)

    def test_actions_that_cannot_be_approved_are_left_out(self) -> None:
        script = self.build("Reply: Recruiter about interview slots", "Calendar: Broken | someday")
        self.assertNotIn(ACTIONS_KEY, [s.key for s in script.sections])
        section = self.actions_section(self.build("Reply: Recruiter", FILM_LINE, "Calendar: Broken | x"))
        self.assertEqual(section.items[0].spoken, "One calendar invite needs your OK.")
        self.assertEqual([item.display for item in section.items if item.kind == ITEM_ENTRY],
                         [f"Film Club October General Meeting - Tue Oct 6 {MIDDLE_DOT} 5:00-6:00 PM"])

    def test_spoken_in_order_and_played_by_default(self) -> None:
        script = self.build(FILM_LINE)
        index = [s.key for s in script.sections].index(ACTIONS_KEY)
        self.assertIn(index, script.section_indices(False))
        lines = spoken_transcript(script).splitlines()
        self.assertEqual(lines[-4:], [
            "One calendar invite needs your OK.",
            "Film Club October General Meeting, Tuesday October 6, 5 to 6 PM.",
            "Approve or deny them on the right.",
            OUTRO,
        ])

    def test_dates_are_described_relative_to_now(self) -> None:
        later = datetime(2027, 1, 5, 9, 0, tzinfo=PDT)
        section = self.actions_section(
            self.build("Calendar: Year end | 2026-12-30 10:00", now=later, expected_run=None))
        entry = section.items[1]
        self.assertEqual(entry.display,
                         f"Year end - Wed Dec 30, 2026 {MIDDLE_DOT} 10:00-11:00 AM")
        self.assertEqual(entry.spoken, "Year end, Wednesday December 30, 2026, 10 to 11 AM.")

    def test_kept_with_a_stale_note_and_ignore_names(self) -> None:
        script = self.build(FILM_LINE, now=NEXT_MORNING, ignore_names=("Ignore", "Needs your OK"))
        self.assertTrue(script.stale)
        section = self.actions_section(script)
        self.assertFalse(section.ignored)
        self.assertEqual(script.sections[-2].key, ACTIONS_KEY)

    def test_only_the_spoken_text_is_read_for_the_closing_line(self) -> None:
        section = self.actions_section(self.build(FILM_LINE))
        closing = section.items[-1]
        self.assertEqual((closing.kind, closing.display), (ITEM_TEXT, ""))
        self.assertTrue(all(set(item.spoken) <= ALLOWED_SPOKEN_CHARS for item in section.items))


if __name__ == "__main__":
    unittest.main()
