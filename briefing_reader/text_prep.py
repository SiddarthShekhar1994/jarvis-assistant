"""Prepare a fetched briefing for listening and for the reading panel.

The flow is:

    strip_markdown(text)     display text: markdown, URLs, HTML and emoji removed
    to_spoken(text)          listening text: short sentences, symbols spelled out
    build_script(briefing)   Script of sections (intro, one per heading, the
                             pending proposals, outro)

Everything here is Qt-free and pure (no I/O), so it is easy to unit test.
Briefing content is personal, so only counts are ever logged, never text.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import TYPE_CHECKING

from .models import (
    BULLETED,
    CALLOUT,
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
    TABLE_ROW,
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

if TYPE_CHECKING:
    from .actions import ProposedAction

logger = logging.getLogger(__name__)

OUTRO_TEXT = "That's the end of your briefing."
STALE_PREFIX = "Heads up: this briefing may be stale."
NOT_UPDATED_LABEL = "Not updated yet"
ACTIONS_KEY = "actions"
ACTIONS_TITLE = "Needs your OK"
ACTIONS_CLOSING = "Approve or deny them on the right."
ACTIONS_CLOSING_MIXED = "You can act on them on the right."
_CALENDAR_KIND = "calendar"   # actions.CALENDAR (actions imports this module, so no import here)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# --------------------------------------------------------------------------
# strip_markdown
# --------------------------------------------------------------------------

# Backslash escapes are parked in the private use area while the markdown
# rules run, so "\*" survives as a literal "*".
_PUA_BASE = 0xE000
_PUA_RE = re.compile("[\ue000-\ue07f]")
_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>~|<])")

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s+[^<>]*)?\s*/?>")
_ENTITY_RE = re.compile(r"&(amp|lt|gt|quot|apos|nbsp|#39);")
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " ", "#39": "'"}

_URL_IN_PARENS = r"(?:[^()\s]|\([^()\s]*\))*"
_IMAGE_RE = re.compile(r"!\[([^\[\]]*)\]\(\s*" + _URL_IN_PARENS + r'(?:\s+"[^"]*")?\s*\)')
_LINK_RE = re.compile(r"\[([^\[\]]*)\]\(\s*" + _URL_IN_PARENS + r'(?:\s+"[^"]*")?\s*\)')
_BARE_URL_RE = re.compile(r"(?:\b(?:https?|ftp)://|\bwww\.)(?:\([^\s()<>]*\)|[^\s()<>])+", re.I)
_URL_TRAILING = ".,;:!?'\"*_~"

_HR_RE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.M)
_LEADING_MARKER_RE = re.compile(
    r"^[ \t]*(?:#{1,6}(?=\s|$)|>|[-*+\u2022\u2023\u25e6\u25aa\u25cf]\s|\d{1,3}[.)]\s|\[[ xX]\](?=\s|$))[ \t]*",
    re.M,
)

_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1", re.S)
_BOLD_STAR_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_BOLD_UNDER_RE = re.compile(r"(?<!\w)__(?=[^\s_])(.+?)(?<=[^\s_])__(?!\w)", re.S)
_ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])", re.S)
_ITALIC_UNDER_RE = re.compile(r"(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)", re.S)
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S)
_STRAY_MARKERS_RE = re.compile(r"\*{2,}|~~|`")

_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"         # pictographs, emoticons, flags, skin tones
    "\u2600-\u27bf"                 # misc symbols and dingbats
    "\u2b00-\u2bff"                 # misc symbols and arrows
    "\u231a\u231b\u23e9-\u23f3\u23f8-\u23fa"
    "\u2934\u2935\u3030\u303d\u3297\u3299"
    "\ufe0e\ufe0f\u20e3"            # variation selectors, keycap
    "\u200b-\u200d\u2060\ufeff"     # zero-width characters (incl. ZWJ)
    "\U000E0020-\U000E007F"         # emoji tag sequences
    "]"
)

_EMPTY_BRACKETS_RE = re.compile(r"\(\s*[,;:.\-\u2013\u2014|]*\s*\)|\[\s*\]|\{\s*\}|<\s*>")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?)\]}])(?=\s|$|[,.;:!?)\]}])")
_SPACE_AFTER_OPEN_RE = re.compile(r"([(\[{])\s+")
_REPEATED_SOFT_PUNCT_RE = re.compile(r"([,;:])(?:\s*[,;:])+")
_SOFT_BEFORE_STOP_RE = re.compile(r"[,;:]\s*([.!?])(?=\s|$)")
_SPACED_DOUBLE_STOP_RE = re.compile(r"(?<!\.)\.(?:\s+\.)+(?!\.)")
_DOUBLE_DOT_RE = re.compile(r"(?<!\.)\.\.(?!\.)")
_LEADING_JUNK_RE = re.compile(r"^(?:[,;:|\u00b7\u2022]+\s*|[-\u2013\u2014]+\s+)+")
_TRAILING_SEPARATORS = " ,;|\u00b7\u2022"
_DASHES = "-\u2013\u2014"
_TRAILING_COLON_RE = re.compile(r"\s*:+\s*$")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_markdown(text: str) -> str:
    """Remove markdown, HTML, URLs and emoji; collapse whitespace.

    Emphasis markers are only removed when they wrap words, so snake_case
    names and arithmetic like "5 * 3" are left alone.
    """
    if not text or not text.strip():
        return ""
    text = _PUA_RE.sub("", text)
    text = _ESCAPE_RE.sub(lambda m: chr(_PUA_BASE + ord(m.group(1))), text)
    text = _strip_html(text)
    text = _IMAGE_RE.sub(lambda m: m.group(1), text)
    text = _LINK_RE.sub(lambda m: m.group(1), text)
    text, removed_url = _remove_urls(text)
    text = _HR_RE.sub("", text)
    text = _strip_leading_markers(text)
    text = _strip_emphasis(text)
    text = _EMOJI_RE.sub(" ", text)
    text = _tidy_punctuation(text, removed_url=removed_url)
    return _PUA_RE.sub(lambda m: chr(ord(m.group()) - _PUA_BASE), text)


def _strip_html(text: str) -> str:
    text = _HTML_COMMENT_RE.sub(" ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    return _ENTITY_RE.sub(lambda m: _ENTITIES[m.group(1)], text)


def _remove_urls(text: str) -> tuple[str, bool]:
    removed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        removed = True
        url = match.group(0)
        # Sentence punctuation right after a URL belongs to the sentence.
        return url[len(url.rstrip(_URL_TRAILING)):]

    return _BARE_URL_RE.sub(replace, text), removed


def _strip_leading_markers(text: str) -> str:
    # Loop because markers nest: "> - [ ] task".
    for _ in range(4):
        stripped = _LEADING_MARKER_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text


def _strip_emphasis(text: str) -> str:
    text = _CODE_SPAN_RE.sub(lambda m: m.group(2), text)
    text = _BOLD_STAR_RE.sub(r"\1", text)
    text = _BOLD_UNDER_RE.sub(r"\1", text)
    text = _ITALIC_STAR_RE.sub(r"\1", text)
    text = _ITALIC_UNDER_RE.sub(r"\1", text)
    text = _STRIKE_RE.sub(r"\1", text)
    return _STRAY_MARKERS_RE.sub("", text)


def _tidy_punctuation(text: str, *, removed_url: bool = False) -> str:
    """Clean up what removals leave behind: "( )", " ,", ". .", dangling separators."""
    text = _WHITESPACE_RE.sub(" ", text).strip()
    for _ in range(3):
        before = text
        text = _EMPTY_BRACKETS_RE.sub("", text)
        text = _SPACE_AFTER_OPEN_RE.sub(r"\1", text)
        text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
        text = _REPEATED_SOFT_PUNCT_RE.sub(r"\1", text)
        text = _SOFT_BEFORE_STOP_RE.sub(r"\1", text)
        text = _SPACED_DOUBLE_STOP_RE.sub(".", text)
        text = _DOUBLE_DOT_RE.sub(".", text)
        text = _LEADING_JUNK_RE.sub("", text)
        text = _strip_trailing_separators(text)
        if removed_url:
            # "Zoom: https://..." -> "Zoom", not "Zoom:".
            text = _TRAILING_COLON_RE.sub("", text)
        text = _WHITESPACE_RE.sub(" ", text).strip()
        if text == before:
            break
    return text


def _strip_trailing_separators(text: str) -> str:
    """Drop dangling separators and spaced dashes at the end ("Zoom -" -> "Zoom", "C-" stays)."""
    while True:
        stripped = text.rstrip(_TRAILING_SEPARATORS)
        without_dash = stripped.rstrip(_DASHES)
        if without_dash != stripped and (not without_dash or without_dash[-1].isspace()):
            stripped = without_dash
        if stripped == text:
            return text
        text = stripped


# --------------------------------------------------------------------------
# to_spoken
# --------------------------------------------------------------------------

_ARROW = r"(?:-{1,2}>|={1,2}>|[\u2192\u21d2\u27f6\u27f9\u2794\u279c\u279d\u279e\u27a1\u2b95])"
_ARROW_RE = re.compile(rf"\s*{_ARROW}\ufe0f?\s*")
# Where the clause around an arrow ends. "." "," ":" only count before a space
# so "$1,204", "10:04" and "v1.2" stay whole.
_CLAUSE_EDGE_RE = re.compile(
    rf"[;!?|()\[\]\u2014\u2013\u00b7\u2022]|[.,:](?=\s|$)|\s-\s|{_ARROW}"
)
_CLAUSE_WORD_RE = re.compile(r"[^\W_][\w'$%./:-]*")
# An arrow followed by one of these introduces the next step, not a destination:
# "assigned to you -> please triage" is two sentences, not "to please triage".
_ARROW_ACTION_WORDS = frozenset({
    "please", "pls", "plz", "action", "todo", "need", "needs", "must", "should", "do", "don't",
    "reply", "respond", "triage", "pay", "send", "call", "sign", "schedule", "book", "confirm",
    "approve", "decline", "forward", "archive", "ignore", "unsubscribe", "delete",
})
_SENTENCE_BREAK = " \u2014 "   # _SPLIT_RE turns a spaced em dash into a sentence break

_DAY = r"(?:Mon|Tues?|Wed|Thu(?:rs?)?|Fri|Sat|Sun)"
_DAY_NAMES = {
    "mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
    "thur": "Thursday", "thurs": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}
# These abbreviations are also everyday words ("Sat with Bob", "The Sun").
_WORDLIKE_DAYS = frozenset({"wed", "sat", "sun"})
_MONTH_TOKEN = (r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec|January|February|March"
                r"|April|June|July|August|September|October|November|December)")
_DAY_PAIR_RE = re.compile(
    rf"(?<![\w'])({_DAY})\.?(\s*[-\u2013/&]\s*|\s+(?:to|through|thru|and|or)\s+)({_DAY})(?![\w'])"
)
# The dot of "Fri." is dropped when a date follows ("Fri. 10/6", "Sun. Oct 4").
_DAY_RE = re.compile(
    rf"(?<![\w'])({_DAY})(?:\.(?=\s*[\d,]|\s+{_MONTH_TOKEN}\.?\s+\d))?(?![\w'])"
)
_DAY_CUE_BEFORE_RE = re.compile(
    r"(?i)\b(?:by|on|until|till|before|after|this|next|last|due|every|each|from|through|thru)\s+$"
)
_DIGIT_AFTER_RE = re.compile(r",?\s*\d")
# A month and a day number right after a word-like day make it a date: "Sun Oct 4",
# "Wed, October 7". Without the number it stays a word: "Sun Jun" is a name,
# "The Sun May rise" is a sentence.
_MONTH_AFTER_RE = re.compile(rf",?\s*{_MONTH_TOKEN}\.?(?=\s+\d)")
_MONTH_NAMES = {
    "jan": "January", "feb": "February", "mar": "March", "apr": "April", "jun": "June",
    "jul": "July", "aug": "August", "sep": "September", "sept": "September", "oct": "October",
    "nov": "November", "dec": "December",
}
# A month abbreviation is expanded before a day or a year. The day may end a
# label ("Monday Oct 5: essay due") but not start a time ("Oct 5:30").
_MONTH_RE = re.compile(
    r"(?<![\w'])(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?"
    r"(?=\s+(?:\d{1,2}(?:st|nd|rd|th)?|\d{4})(?!\w|:(?!\s|$)))"
)
# Email shorthand read letter by letter; "Cc" or "Fyi" would sound like words.
_SHORTHAND_RE = re.compile(r"(?i)(?<![\w'])(?:cc|bcc|fyi|asap|eod|eow|eom|tbd|tba|eta|rsvp|pto)(?![\w'])")
# "Re:" / "Fwd:" opening a subject line carry no meaning when heard.
_SUBJECT_PREFIX_RE = re.compile(r"(?i)(^|[:(\"'|\u00b7\u2022\u2013\u2014]\s*)(?:(?:re|fwd?)\s*:\s*)+")
_REGARDING_RE = re.compile(r"(?i)(?<![\w'])re:\s+")

_RANGE_DASH_RE = re.compile(r"(?<=\w)\u2013(?=\w)")
# A spaced hyphen or en dash between two values of the same kind is a range and
# reads "to"; between anything else it stays a sentence break, so
# "Invoice 4411 - 3 days overdue" is not read as a range.
_AMPM = r"[AaPp]\.?[Mm]\.?"
_TIME_RANGE_RE = re.compile(   # "2pm - 3pm", "9:00 AM - 5:00 PM", "10:30 - 11:00 AM"
    rf"(?<![\w:$.])(\d{{1,2}}(?::\d{{2}})?(?:\s?{_AMPM})?)\s+[-\u2013]\s+"
    rf"(?=\d{{1,2}}(?::\d{{2}}(?:\s?{_AMPM})?|\s?{_AMPM})(?![\w:]))"
)
_MONEY_RANGE_RE = re.compile(r"(?<![\w$])(\$\d[\d,]*(?:\.\d+)?)\s+[-\u2013]\s+(?=\$\d)")   # "$40 - $60"
# Unspaced ranges ("6:30-7:45 PM", "5-6 PM", "10:30am-11am", "$40-$60") read "to"
# too. A time range needs an h:mm side or an am/pm, so "3-2", "BIO-2",
# "COVID-19", "2026-10-04" and "555-555-0123" stay as they are.
_UNSPACED_TIME_RANGE_RE = re.compile(
    rf"(?<![\w:$.,/#\-\u2013])(\d{{1,2}}(?::[0-5]\d)?)(\s?{_AMPM})?[-\u2013]"
    rf"(\d{{1,2}}(?::[0-5]\d)?)(\s?{_AMPM})?(?![\w:])"
)
_UNSPACED_MONEY_RANGE_RE = re.compile(
    r"(?<![\w$.,\-])(\$\d[\d,]*(?:\.\d+)?)[-\u2013](?=\$\d)"
)
# "$40-60" and "$5-10" read "$40 to $60": only a rising pair, so "$10-5" stays.
_AMOUNT = r"\d+(?:,\d{3})*(?:\.\d+)?"
_MONEY_TO_NUMBER_RE = re.compile(
    rf"(?<![\w$.,\-])\$({_AMOUNT})[-\u2013]({_AMOUNT})(?![\w$%]|[-\u2013/.,]?\d)"
)
# "noon-1pm", "11am-noon", "noon - 1:30 PM". Spaced, the number needs h:mm or
# am/pm, so "at noon - 2 people confirmed" still splits.
_NOON = r"(?:[Nn]oon|[Mm]idnight)"
_HOUR = r"\d{1,2}(?::[0-5]\d)?"
_NOON_START_RE = re.compile(
    rf"(?<![\w'-])({_NOON})(?:[-\u2013](?={_HOUR}(?:\s?{_AMPM})?(?![\w:]))"
    rf"|\s+[-\u2013]\s+(?=\d{{1,2}}(?::[0-5]\d(?:\s?{_AMPM})?|\s?{_AMPM})(?![\w:])))"
)
_NOON_END_RE = re.compile(
    rf"(?<![\w:$.,/#\-\u2013])({_HOUR})(\s?{_AMPM})?(\s+[-\u2013]\s+|[-\u2013])(?={_NOON}(?![\w'-]))"
)
_FULL_MONTH = ("(?:January|February|March|April|May|June|July|August|September|October|November"
               "|December)")
_DAY_RANGE_RE = re.compile(   # "October 12 - 15", "October 4-6", "October 30-November 2"
    rf"(?<![\w'])({_FULL_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?)(?:\s+[-\u2013]\s+|-)"
    rf"(?=({_FULL_MONTH}\s+)?(\d{{1,2}})(?:st|nd|rd|th)?(?![\w:]|[-\u2013/.]\d))"
)
_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2018": "'", "\u2019": "'",
                         "\u201a": "'", "\u00a0": " "})
_ANGLE_EMAIL_RE = re.compile(r"\s*<[^<>\s@]+@[^<>\s]+>")
# "Work (name@example.edu)" reads "Work": a parenthetical holding nothing but
# e-mail addresses is dropped. Addresses inside a sentence are kept.
_EMAIL = r"[^\s()<>@,;]+@[^\s()<>@,;]+\.[A-Za-z]{2,}"
_PAREN_EMAIL_RE = re.compile(rf"\s*\(\s*{_EMAIL}(?:\s*(?:[,;/]|and)\s*{_EMAIL})*\s*\)")
# ...unless the address is the object of the sentence: "Reply to (x@y.edu) about the hold".
_EMAIL_OBJECT_CUE_RE = re.compile(r"(?i)(?<![\w'-])(?:to|at|from|via|cc|bcc|e-?mail|contact)\s*$")
# "212 W Example Way" reads "212 West Example Way" (only before a street name).
_COMPASS = {"N": "North", "S": "South", "E": "East", "W": "West"}
_STREET_SUFFIX = (r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Ct|Court"
                  r"|Pl|Place|Pkwy|Parkway|Hwy|Highway|Expy|Expressway|Cir|Circle|Ter|Terrace"
                  r"|Trl|Trail|Sq|Square|Loop|Plaza)")
_STREET_DIRECTION_RE = re.compile(
    rf"(?<![\w.,:$#-])(\d{{1,6}}[A-Za-z]?\s+)([NSEW])\.?"
    rf"(?=\s+(?:[A-Z0-9][\w'-]*\s+){{1,3}}{_STREET_SUFFIX}\b)"
)
# "Essay #1" reads "Essay number 1". Longer numbers ("Invoice #4471") are ids
# and keep their "#", so they are not read as one big amount.
_NUMBER_SIGN_RE = re.compile(r"(?<![\w#&])#(?=\d{1,2}(?![\d,.]?\d))")
_AMPERSAND_RE = re.compile(r"\s*&\s*")
_WITHOUT_RE = re.compile(r"(?i)(?<![\w/])w/o(?![\w/])")
_WITH_RE = re.compile(r"(?i)(?<![\w/])w/(?=\s|[A-Za-z])")
_ABBREVIATIONS = (
    (re.compile(r"(?i)(?<![\w.])e\.\s?g\.?(?!\w)"), "for example"),
    (re.compile(r"(?i)(?<![\w.])i\.\s?e\.?(?!\w)"), "that is"),
    (re.compile(r"(?i)(?<![\w.])etc\.?(?!\w)"), "et cetera"),
    (re.compile(r"(?i)(?<![\w.])vs\.?(?=\s)"), "versus"),
)
_ELLIPSIS_RE = re.compile(r"\s*(?:\.{3,}|\u2026)\s*")
_DOT_RUN_RE = re.compile(r"\.{2,}")
# Sentence breaks for listening. A hyphen only splits when spaced, so words
# like "sign-in" and ids like "PROJ-142" stay whole.
_SPLIT_RE = re.compile(
    r"\s+[-\u2013\u2014]{1,3}\s+"   # spaced hyphen / en dash / em dash
    r"|\s*\u2014\s*"                # unspaced em dash
    r"|(?<=\w)--(?=\w)"             # "word--word"
    r"|\s*\|\s*"
    r"|\s*;\s*"
    r"|\s*[\u00b7\u2022]\s*"
)
_PART_LEADING_RE = re.compile(r"^(?:[,;:]+|[.!?]+(?=\s|$))\s*")
_PART_TRAILING_RE = re.compile(r"[\s,;]+$")
_ENDS_SENTENCE_RE = re.compile(r"[.!?][\"')\]]*$")
_ALNUM_RE = re.compile(r"[^\W_]")
_FIRST_WORD_RE = re.compile(r"[^\W_]+")


def to_spoken(text: str) -> str:
    """Display text -> listening text: short sentences, symbols spelled out.

    Beyond the markdown/URL/emoji removal this reads "&" as "and", arrows as
    "to" or as a sentence break, a dash between two times, prices or days
    ("2pm - 3pm", "6:30-7:45 PM") as "to", expands weekday and month
    abbreviations, drops e-mail addresses in "<...>" or on their own in
    parentheses, drops "Re:"/"Fwd:" subject prefixes, reads a mid-sentence
    "re:" as "about" and upper-cases email shorthand (cc, fyi, eod, ...) so
    it is spelled out. Returns "" when nothing worth saying is left.
    """
    if not text or not text.strip():
        return ""
    text = _HTML_COMMENT_RE.sub(" ", text)
    # Before strip_markdown, because some arrows are emoji and would vanish.
    text = _ARROW_RE.sub(_spoken_arrow, text)
    text = strip_markdown(text)
    if not text:
        return ""
    text = text.translate(_QUOTES)
    text = _ANGLE_EMAIL_RE.sub("", text)
    text = _drop_paren_emails(text)
    text = _expand_dates(text)
    text = _spoken_ranges(text)
    text = _STREET_DIRECTION_RE.sub(lambda m: m.group(1) + _COMPASS[m.group(2)], text)
    text = _NUMBER_SIGN_RE.sub("number ", text)
    text = _AMPERSAND_RE.sub(" and ", text)
    text = _WITHOUT_RE.sub("without", text)
    text = _WITH_RE.sub("with ", text)
    text = _SUBJECT_PREFIX_RE.sub(r"\1", text)
    text = _REGARDING_RE.sub("about ", text)
    text = _SHORTHAND_RE.sub(lambda m: m.group().upper(), text)
    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)
    text = _ELLIPSIS_RE.sub(", ", text)
    text = _DOT_RUN_RE.sub(".", text)
    sentences = (_finish_sentence(part) for part in _SPLIT_RE.split(text))
    spoken = " ".join(sentence for sentence in sentences if sentence)
    return _WHITESPACE_RE.sub(" ", spoken).strip()


def _spoken_arrow(match: re.Match[str]) -> str:
    """"2pm -> 3pm" reads "to"; "assigned to you -> please triage" becomes two sentences."""
    left = _CLAUSE_EDGE_RE.split(match.string[:match.start()])[-1]
    right = _CLAUSE_EDGE_RE.split(match.string[match.end():])[0]
    left_words = _CLAUSE_WORD_RE.findall(left)
    right_words = _CLAUSE_WORD_RE.findall(right)
    if not left_words or not right_words:
        return " "  # a leading "-> Reply by Friday" or trailing "Next step ->"
    if right_words[0].casefold() in _ARROW_ACTION_WORDS:
        return _SENTENCE_BREAK
    both_values = _has_digit(left_words[-1]) and _has_digit(right_words[0])
    if len(left_words) <= 2 or both_values:
        return " to "
    return _SENTENCE_BREAK


def _has_digit(word: str) -> bool:
    return any(ch.isdigit() for ch in word)


def _drop_paren_emails(text: str) -> str:
    """"Work (name@example.edu)" -> "Work"; "Reply to (name@example.edu)" keeps the address."""
    removed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        if _EMAIL_OBJECT_CUE_RE.search(match.string[:match.start()]):
            return match.group(0)
        removed = True
        return ""

    text = _PAREN_EMAIL_RE.sub(replace, text)
    # "Work - (name@example.edu)" would leave a dangling "Work -".
    return _tidy_punctuation(text) if removed else text


def _expand_dates(text: str) -> str:
    """Spell out weekday and month abbreviations ("by Fri" -> "by Friday", "Oct 6" -> "October 6")."""
    text = _DAY_PAIR_RE.sub(_spoken_day_pair, text)
    text = _DAY_RE.sub(_spoken_day, text)
    return _MONTH_RE.sub(lambda m: _MONTH_NAMES[m.group(1).lower()], text)


def _spoken_ranges(text: str) -> str:
    """"2pm - 3pm", "5-6 PM", "$40 - $60" and "October 12 - 15" read "to" (not two sentences)."""
    text = _TIME_RANGE_RE.sub(r"\1 to ", text)
    text = _MONEY_RANGE_RE.sub(r"\1 to ", text)
    text = _UNSPACED_TIME_RANGE_RE.sub(_spoken_unspaced_time_range, text)
    text = _NOON_START_RE.sub(r"\1 to ", text)
    text = _NOON_END_RE.sub(_spoken_noon_end, text)
    text = _UNSPACED_MONEY_RANGE_RE.sub(r"\1 to ", text)
    text = _MONEY_TO_NUMBER_RE.sub(_spoken_money_to_number, text)
    text = _DAY_RANGE_RE.sub(_spoken_day_range, text)
    # Any other en dash between two words or numbers ("pages 10–12") reads "to".
    return _RANGE_DASH_RE.sub(" to ", text)


def _spoken_noon_end(match: re.Match[str]) -> str:
    # "11am-noon" and "11:30 - noon" are ranges; "Room 12 - noon meeting" is not.
    hour, ampm, dash = match.groups()
    spaced = dash.strip() != dash
    if int(hour.split(":")[0]) > 24 or (spaced and not (ampm or ":" in hour)):
        return match.group(0)
    return f"{hour}{ampm or ''} to "


def _spoken_money_to_number(match: re.Match[str]) -> str:
    low, high = match.groups()
    if float(low.replace(",", "")) >= float(high.replace(",", "")):
        return match.group(0)
    return f"${low} to ${high}"


def _spoken_unspaced_time_range(match: re.Match[str]) -> str:
    # "6:30-7:45", "5-6 PM", "10:30am-11am" are times; "3-2" alone is a score or a count.
    start, start_ampm, end, end_ampm = match.groups()
    is_time = ":" in start or ":" in end or bool(start_ampm or end_ampm)
    hours_ok = all(int(value.split(":")[0]) <= 24 for value in (start, end))
    if not (is_time and hours_ok):
        return match.group(0)
    return f"{start}{start_ampm or ''} to {end}{end_ampm or ''}"


def _spoken_day_range(match: re.Match[str]) -> str:
    # Without a second month only a rising day ("12 - 15") is a range; "Oct 6 - 3 left" is not.
    first, second = int(match.group(2)), int(match.group(4))
    if match.group(3) or first < second <= 31:
        return f"{match.group(1)} to "
    return match.group(0)


def _spoken_day_pair(match: re.Match[str]) -> str:
    first, joiner, second = match.groups()
    joiner = joiner.strip()
    if joiner in ("-", "–"):
        joiner = "to"
    elif joiner in ("/", "&"):
        joiner = "and"
    return f"{_DAY_NAMES[first.lower()]} {joiner} {_DAY_NAMES[second.lower()]}"


def _spoken_day(match: re.Match[str]) -> str:
    abbreviation = match.group(1)
    if abbreviation.lower() in _WORDLIKE_DAYS:
        before = match.string[:match.start()]
        after = match.string[match.end():]
        if not (_DAY_CUE_BEFORE_RE.search(before) or _DIGIT_AFTER_RE.match(after)
                or _MONTH_AFTER_RE.match(after)):
            return match.group(0)
    return _DAY_NAMES[abbreviation.lower()]


def _finish_sentence(part: str) -> str:
    part = _PART_LEADING_RE.sub("", part.strip())
    part = _PART_TRAILING_RE.sub("", part)
    if not _ALNUM_RE.search(part):
        return ""
    part = _capitalize_first(part)
    part = _TRAILING_COLON_RE.sub(".", part)
    if not _ENDS_SENTENCE_RE.search(part):
        part += "."
    return part


def _capitalize_first(text: str) -> str:
    """Upper-case the first letter unless the first word is not a plain word (2pm, iPhone)."""
    match = _FIRST_WORD_RE.search(text)
    if not match:
        return text
    word = match.group()
    if not word[0].isalpha() or not word[0].islower() or any(c.isupper() for c in word[1:]):
        return text
    return text[:match.start()] + word[0].upper() + text[match.start() + 1:]


def _lowercase_first(text: str) -> str:
    """Lower-case the first letter of a capitalised word; leave acronyms and "I" alone."""
    match = _FIRST_WORD_RE.search(text)
    if not match:
        return text
    word = match.group()
    if len(word) < 2 or not word[0].isupper() or any(c.isupper() for c in word[1:]):
        return text
    return text[:match.start()] + word[0].lower() + text[match.start() + 1:]


# --------------------------------------------------------------------------
# Headings
# --------------------------------------------------------------------------

_COUNT_PATTERNS = (
    re.compile(r"\s*\(\s*(\d+)(?:\s+(?:items?|new|unread|emails?|messages?))?\s*\)$", re.I),
    re.compile(r"\s*\[\s*(\d+)\s*\]$"),
    re.compile(r"\s+[-\u2013\u2014\u00b7]\s*(\d+)$"),
    re.compile(r"\s*:\s*(\d+)$"),
)
_HEADING_EDGE_CHARS = " :-\u2013\u2014|\u00b7\u2022"
_NON_WORD_RE = re.compile(r"[\W_]+")


def clean_heading(text: str) -> tuple[str, int | None]:
    """("Work inbox (3)") -> ("Work inbox", 3). Count forms: "(3)", "[3]", "- 3", ": 3"."""
    title = strip_markdown(text)
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(title)
        if match:
            remainder = title[:match.start()].strip(_HEADING_EDGE_CHARS)
            if remainder:
                return remainder, int(match.group(1))
            break
    return title.strip(_HEADING_EDGE_CHARS) or title, None


def _normalize_name(text: str) -> str:
    return _NON_WORD_RE.sub(" ", text.casefold()).strip()


def is_ignore_heading(title: str, ignore_names: Sequence[str]) -> bool:
    """True if the heading equals or starts with one of the ignore names (case-insensitive)."""
    if isinstance(ignore_names, str):
        ignore_names = (ignore_names,)
    normalized = _normalize_name(title)
    if not normalized:
        return False
    for name in ignore_names:
        wanted = _normalize_name(name)
        if wanted and normalized.startswith(wanted):
            return True
    return False


def _display_title(title: str, count: int | None) -> str:
    return title if count is None else f"{title} ({count})"


def _count_sentence(count: int | None) -> str:
    if count is None:
        return ""
    if count == 0:
        return "No items."
    return "1 item." if count == 1 else f"{count} items."


# --------------------------------------------------------------------------
# Time labels and the stale note
# --------------------------------------------------------------------------

def clock_parts(dt: datetime, *, hour24: bool = False) -> tuple[str, str]:
    """("1:05", "PM") / ("12:00", "AM"); with ``hour24`` ("13:05", "") / ("09:05", "").

    The one clock formatter for speech and screens. Built by hand: strftime's
    %I and %p depend on the locale, and %-I fails on Windows.
    """
    if hour24:
        return f"{dt.hour:02d}:{dt.minute:02d}", ""
    return f"{dt.hour % 12 or 12}:{dt.minute:02d}", "AM" if dt.hour < 12 else "PM"


def format_time(dt: datetime, *, hour24: bool = False) -> str:
    """"10:04 AM" / "11:31 PM" (no leading zero); "10:04" / "23:31" with ``hour24``.

    Speech always uses the 12-hour clock; the screen follows ``[display] clock``.
    """
    digits, meridiem = clock_parts(dt, hour24=hour24)
    return f"{digits} {meridiem}" if meridiem else digits


def _aware(now: datetime) -> datetime:
    return now if now.utcoffset() is not None else now.astimezone()


def describe_updated(updated_at: datetime | None, now: datetime) -> str:
    """"today at 10:04 AM", "yesterday at ...", "on Thursday at ...", "on Oct 1 at ..."."""
    if updated_at is None:
        return ""
    now = _aware(now)
    if updated_at.utcoffset() is None:
        updated_at = updated_at.replace(tzinfo=now.tzinfo)
    local = updated_at.astimezone(now.tzinfo)
    time_text = format_time(local)
    days_ago = (now.date() - local.date()).days
    if days_ago == 0:
        return f"today at {time_text}"
    if days_ago == 1:
        return f"yesterday at {time_text}"
    if 2 <= days_ago <= 6:
        return f"on {_WEEKDAYS[local.weekday()]} at {time_text}"
    date_text = f"{_MONTHS[local.month - 1]} {local.day}"
    if local.year != now.year:
        date_text += f", {local.year}"
    return f"on {date_text} at {time_text}"


_NEVER_RE = re.compile(r"(?i)^(?:never|none|n/?a|pending|not yet\b.*|-+)[.!]*$")


def _never_updated(header: BriefingHeader) -> bool:
    raw = strip_markdown(header.updated_raw or "")
    return not raw or bool(_NEVER_RE.match(raw))


def updated_label(header: BriefingHeader, now: datetime) -> str:
    """UI label: "Updated today at 10:04 AM", "Not updated yet" or "Updated: <raw>"."""
    if header.updated_at is not None:
        return f"Updated {describe_updated(header.updated_at, now)}"
    if _never_updated(header):
        return NOT_UPDATED_LABEL
    return f"Updated: {strip_markdown(header.updated_raw or '')}"


def stale_note(header: BriefingHeader, freshness: Freshness | None, expected_run: str | None,
               now: datetime) -> str | None:
    """A short spoken warning when the briefing is not the one we waited for, else None."""
    if freshness is None or freshness.fresh:
        return None
    when = describe_updated(header.updated_at, now)
    if header.updated_at is None:
        if _never_updated(header):
            return f"{STALE_PREFIX} The page has not been updated yet."
        return f"{STALE_PREFIX} I could not tell when the page was last updated."
    if not freshness.updated_today:
        return f"{STALE_PREFIX} It was last updated {when}."
    run = _run_label(expected_run, None)
    if not freshness.run_matches and run:
        if header.run:
            return (f"Heads up: this may not be the {run} briefing. "
                    f"The page is from the {header.run} run, updated {when}.")
        return (f"Heads up: this may not be the {run} briefing. "
                f"The page does not say which run it is from. It was updated {when}.")
    return f"{STALE_PREFIX} It was last updated {when}."


def _run_label(expected_run: str | None, header_run: str | None) -> str | None:
    for run in (expected_run, header_run):
        if run and run.strip():
            return run.strip().upper()
    return None


def _intro_sentence(run: str | None, when: str) -> str:
    subject = f"your {run} briefing" if run else "your briefing"
    return f"Here's {subject}, updated {when}." if when else f"Here's {subject}."


# --------------------------------------------------------------------------
# Script building
# --------------------------------------------------------------------------

def _convert_line(line: FlatLine) -> ScriptItem | None:
    """FlatLine (not a heading) -> ScriptItem, or None when nothing is left to show or say."""
    if line.kind == DIVIDER:
        return ScriptItem(ITEM_PAUSE, "", "", line.depth)
    if line.kind == CODE:
        display = line.text.rstrip()
        return ScriptItem(ITEM_TEXT, "", display, line.depth) if display.strip() else None

    display = strip_markdown(line.text)
    spoken = to_spoken(line.text)
    kind = ITEM_TEXT
    if line.kind in (BULLETED, NUMBERED, TABLE_ROW):
        kind = ITEM_ENTRY
        if line.kind == NUMBERED and display:
            display = f"{line.number or 1}. {display}"
    elif line.kind == TO_DO:
        kind = ITEM_ENTRY
        if display:
            display = f"[{'x' if line.checked else ' '}] {display}"
        if line.checked and spoken:
            spoken = "Already done: " + _lowercase_first(spoken)
    if not display and not spoken:
        return None
    return ScriptItem(kind, spoken, display, line.depth)


class _SectionBuilder:
    """Collects items for one section, collapsing and trimming pauses."""

    def __init__(self, key: str, title: str = "", ignored: bool = False,
                 heading_level: int | None = None) -> None:
        self.key = key
        self.title = title
        self.ignored = ignored
        self.heading_level = heading_level
        self.items: list[ScriptItem] = []

    def add(self, item: ScriptItem) -> None:
        if item.kind == ITEM_PAUSE:
            previous = self.items[-1].kind if self.items else None
            # No pause at the start, right after the announcement, or twice in a row.
            if previous in (None, ITEM_PAUSE, ITEM_HEADING, ITEM_NOTE, ITEM_INTRO):
                return
        self.items.append(item)

    def build(self) -> Section:
        while self.items and self.items[-1].kind == ITEM_PAUSE:
            self.items.pop()
        return Section(key=self.key, title=self.title, items=tuple(self.items), ignored=self.ignored)


class _AnnounceNames:
    """Case-insensitive heading -> spoken name lookup (keys may carry counts or emoji)."""

    def __init__(self, announce: Mapping[str, str] | None) -> None:
        self._names: dict[str, str] = {}
        for key, value in (announce or {}).items():
            name = clean_heading(str(key))[0].casefold()
            if name and str(value).strip():
                self._names[name] = str(value).strip()

    def knows(self, title: str) -> bool:
        return title.casefold() in self._names

    def spoken_heading(self, title: str, count: int | None) -> str:
        name = self._names.get(title.casefold(), title)
        spoken = to_spoken(name) or to_spoken(title)
        count_text = _count_sentence(count)
        return f"{spoken} {count_text}".strip() if spoken else ""


class _SectionList:
    """The finished sections and the one being filled."""

    def __init__(self, intro: _SectionBuilder) -> None:
        self.finished: list[Section] = []
        self.current = intro

    def start(self, title: str = "", ignored: bool = False, level: int | None = None) -> None:
        """Close the current section and open the next one ("" = untitled, continues the text)."""
        self._close()
        self.current = _SectionBuilder(f"s{len(self.finished)}", title, ignored, level)

    def sections(self) -> list[Section]:
        self._close()
        return self.finished

    def _close(self) -> None:
        current = self.current
        if current.key == "intro" or current.title or current.items:
            self.finished.append(current.build())


# Section titles written as paragraphs, toggles or callouts instead of heading blocks.
_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(?=\S)")
_WHOLE_BOLD_RE = re.compile(r"^(\*\*|__)(?=\S)((?:(?!\1).)+?)(?<=\S)\1$", re.S)
_TITLE_MAX_CHARS = 60
_TITLE_MAX_WORDS = 6
_PARAGRAPH_TITLE_LEVEL = 2   # on a page without heading blocks, like a heading_2
_TITLE_SEPARATORS = "-–—:|·("


def _is_title_like(title: str, text: str) -> bool:
    """A few words, not a sentence."""
    return (len(title) <= _TITLE_MAX_CHARS and len(title.split()) <= _TITLE_MAX_WORDS
            and not strip_markdown(text).endswith((".", "!", "?")))


def _is_ignore_title(title: str, ignore_names: Sequence[str], labelled: bool) -> bool:
    """An ignore-section title that is not a heading block.

    Stricter than is_ignore_heading, so a line such as "Ignore this for now"
    never hides what follows it: the name alone, one word ("Ignored"), the
    name followed by a separator ("Ignore - promos"), or a labelled line
    (count, trailing colon, bold or "#") qualify.
    """
    if not is_ignore_heading(title, ignore_names):
        return False
    if labelled or len(title.split()) == 1:
        return True
    folded = title.casefold()
    for name in (ignore_names,) if isinstance(ignore_names, str) else ignore_names:
        name = str(name).strip().casefold()
        if name and folded.startswith(name):
            rest = folded[len(name):].strip()
            if not rest or rest[0] in _TITLE_SEPARATORS:
                return True
    return False


def _paragraph_title(line: FlatLine, *, structural: bool, sub_level: int, names: _AnnounceNames,
                     ignore_names: Sequence[str]) -> tuple[str, int] | None:
    """(heading text, level) when a top-level paragraph is a section title, else None.

    On a page without heading blocks (``structural``) a paragraph that is bold
    as a whole or starts with markdown "#"s is a title. On every page a
    paragraph named like an ignore section or an announced heading
    ("Ignore (2)", "Due:") is one; with heading blocks present it ranks below
    them (``sub_level``), so real headings keep deciding the sections.
    """
    if line.kind != PARAGRAPH or line.depth != 0:
        return None
    text = line.text.strip()
    markdown = _MARKDOWN_HEADING_RE.match(text)
    body = text[markdown.end():] if markdown else text
    bold = _WHOLE_BOLD_RE.match(body) is not None
    title, count = clean_heading(body)
    if not title or not _is_title_like(title, body):
        return None
    level = len(markdown.group(1)) if markdown else _PARAGRAPH_TITLE_LEVEL
    if structural and (markdown or bold):
        return body, level
    labelled = bool(markdown or bold or count is not None or body.rstrip(" *_").endswith(":"))
    if names.knows(title) or _is_ignore_title(title, ignore_names, labelled):
        return body, (level if structural else sub_level)
    return None


def _ignore_container(line: FlatLine, ignore_names: Sequence[str]) -> tuple[str, int | None] | None:
    """(title, count) for a toggle or callout titled like an ignore section ("Ignore (2)")."""
    if line.kind not in (TOGGLE, CALLOUT):
        return None
    title, count = clean_heading(line.text)
    if not title or not _is_title_like(title, line.text):
        return None
    labelled = count is not None or line.text.rstrip(" *_").endswith(":")
    return (title, count) if _is_ignore_title(title, ignore_names, labelled) else None


def _heading_lines(lines: Sequence[FlatLine], names: _AnnounceNames,
                   ignore_names: Sequence[str]) -> dict[int, tuple[str, int]]:
    """Line index -> (heading text, level) for heading blocks and paragraphs that are titles."""
    headings = {index: (line.text, line.level or 1) for index, line in enumerate(lines)
                if line.kind == HEADING and clean_heading(line.text)[0]}
    structural = not headings
    sub_level = max((level for _, level in headings.values()), default=0) + 1
    for index, line in enumerate(lines):
        found = _paragraph_title(line, structural=structural, sub_level=sub_level, names=names,
                                 ignore_names=ignore_names)
        if found is not None:
            headings[index] = found
    return headings


def _page_title_index(headings: Mapping[int, tuple[str, int]], names: _AnnounceNames,
                      ignore_names: Sequence[str]) -> int | None:
    """The first heading when it alone has the top level and other headings follow (a page title)."""
    if len(headings) < 2:
        return None
    first = min(headings)
    top = min(level for _, level in headings.values())
    if headings[first][1] != top or sum(1 for _, level in headings.values() if level == top) != 1:
        return None
    title = clean_heading(headings[first][0])[0]
    if names.knows(title) or is_ignore_heading(title, ignore_names):
        return None
    return first


def _nested_item(line: FlatLine, names: _AnnounceNames, shift: int) -> ScriptItem | None:
    """A line inside an ignored toggle, indented relative to the toggle."""
    if line.kind == HEADING:
        title, count = clean_heading(line.text)
        display = _display_title(title, count)
        item = ScriptItem(ITEM_SUBHEADING, names.spoken_heading(title, count), display,
                          line.depth) if title else None
    else:
        item = _convert_line(line)
    return None if item is None else replace(item, depth=max(0, item.depth - shift))


def build_script(
    briefing: Briefing,
    *,
    now: datetime,
    expected_run: str | None = None,
    freshness: Freshness | None = None,
    include_note: bool = True,
    ignore_names: Sequence[str] = ("Ignore",),
    announce: Mapping[str, str] | None = None,
    actions: Sequence[ProposedAction] = (),
) -> Script:
    """Turn a briefing into intro, one section per top-level heading, and outro.

    Headings are heading blocks plus top-level paragraphs that are section
    titles (see ``_paragraph_title``). A first heading that alone has the top
    level, with deeper headings after it, is the page title: it is read once
    in the intro and the next level starts the sections. A toggle or callout
    titled like an ignore section is an ignored section holding its children.

    ``actions`` are the pending proposals (the caller drops the decided ones;
    only the ones that take a decision count); when there are any, a "Needs
    your OK" section that summarises them comes right before the outro. It is
    never ignored.
    """
    header = briefing.header
    # Name the run the page says it is; the stale note already covers a mismatch.
    run = _run_label(header.run, expected_run)
    note = stale_note(header, freshness, expected_run, now) if include_note else None
    names = _AnnounceNames(announce)

    intro = _SectionBuilder("intro")
    if note:
        intro.add(ScriptItem(ITEM_NOTE, note, note))
    when = describe_updated(header.updated_at, now)
    intro_display = _intro_sentence(run, when)
    # The stale note already says when the page was updated; do not say it twice.
    intro_spoken = _intro_sentence(run, "") if note else intro_display
    intro.add(ScriptItem(ITEM_INTRO, intro_spoken, intro_display))

    lines = briefing.lines
    headings = _heading_lines(lines, names, ignore_names)
    title_index = _page_title_index(headings, names, ignore_names)
    levels = [level for index, (_, level) in headings.items() if index != title_index]
    section_level = min(levels) if levels else 1
    sections = _SectionList(intro)
    toggle_depth: int | None = None   # set while inside an ignored toggle or callout
    for index, line in enumerate(lines):
        if toggle_depth is not None:
            if line.depth > toggle_depth:
                item = _nested_item(line, names, toggle_depth + 1)
                if item is not None:
                    sections.current.add(item)
                continue
            toggle_depth = None
            sections.start()   # what follows the toggle is read again
        if index in headings:
            _add_heading(sections, headings[index], line, names, ignore_names,
                         section_level=section_level, page_title=index == title_index)
            continue
        container = None if sections.current.ignored else _ignore_container(line, ignore_names)
        if container is not None:
            title, count = container
            display = _display_title(title, count)
            sections.start(display, ignored=True, level=section_level)
            sections.current.add(ScriptItem(ITEM_HEADING, names.spoken_heading(title, count), display))
            toggle_depth = line.depth
            continue
        item = _convert_line(line)
        if item is not None:
            sections.current.add(item)
    finished = sections.sections()
    pending = [action for action in actions if action.decidable]
    if pending:
        finished.append(_actions_section(pending, now.date()))
    finished.append(Section(key="outro", title="",
                            items=(ScriptItem(ITEM_OUTRO, OUTRO_TEXT, OUTRO_TEXT),)))

    logger.debug("Built script: %d section(s), %d item(s), stale note=%s",
                 len(finished), sum(len(s.items) for s in finished), bool(note))
    return Script(sections=tuple(finished), run_label=run,
                  updated_label=updated_label(header, now), stale=bool(note))


_COUNT_WORDS = ("Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine")


def _actions_section(actions: Sequence[ProposedAction], today: date) -> Section:
    """The "Needs your OK" summary: a count, one entry per proposal, and where to decide.

    Calendar invites alone keep their own wording ("Two calendar invites need
    your OK."); a mix says what kinds there are ("Four items need your OK: a
    calendar invite, two replies and a to-do.") and names each Calendar entry.
    """
    calendar_only = all(action.kind == _CALENDAR_KIND for action in actions)
    heading = _actions_count_sentence(len(actions)) if calendar_only else _mixed_count_sentence(actions)
    items = [ScriptItem(ITEM_HEADING, heading, ACTIONS_TITLE)]
    for action in actions:
        detail = action.describe(today)
        display = f"{action.title} - {detail}" if detail else action.title
        spoken = action.spoken(today) + "."
        if not calendar_only and action.kind == _CALENDAR_KIND:
            spoken = "Calendar invite: " + spoken
        items.append(ScriptItem(ITEM_ENTRY, spoken, display))
    items.append(ScriptItem(ITEM_TEXT, ACTIONS_CLOSING if calendar_only else ACTIONS_CLOSING_MIXED, ""))
    return Section(key=ACTIONS_KEY, title=ACTIONS_TITLE, items=tuple(items))


def _count_word(count: int) -> str:
    return _COUNT_WORDS[count] if count < len(_COUNT_WORDS) else str(count)


def _mixed_count_sentence(actions: Sequence[ProposedAction]) -> str:
    """"One item needs your OK: a reply." / "Four items need your OK: a calendar invite, two replies and a to-do." """
    counts: dict[str, int] = {}
    nouns: dict[str, tuple[str, str]] = {}
    for action in actions:   # kinds in order of first appearance
        counts[action.kind] = counts.get(action.kind, 0) + 1
        nouns.setdefault(action.kind, action.speech_noun)
    phrases = [nouns[kind][0] if count == 1 else f"{_count_word(count).lower()} {nouns[kind][1]}"
               for kind, count in counts.items()]
    breakdown = phrases[0] if len(phrases) == 1 else ", ".join(phrases[:-1]) + " and " + phrases[-1]
    total = len(actions)
    if total == 1:
        return f"{_count_word(total)} item needs your OK: {breakdown}."
    return f"{_count_word(total)} items need your OK: {breakdown}."


def _actions_count_sentence(count: int) -> str:
    """"One calendar invite needs your OK." / "Two calendar invites need your OK." """
    number = _COUNT_WORDS[count] if count < len(_COUNT_WORDS) else str(count)
    if count == 1:
        return f"{number} calendar invite needs your OK."
    return f"{number} calendar invites need your OK."


def _add_heading(sections: _SectionList, heading: tuple[str, int], line: FlatLine,
                 names: _AnnounceNames, ignore_names: Sequence[str], *, section_level: int,
                 page_title: bool) -> None:
    """Start a section for a heading, or add it as a subheading of the current one."""
    text, level = heading
    title, count = clean_heading(text)
    display = _display_title(title, count)
    spoken = names.spoken_heading(title, count)
    ignored = is_ignore_heading(title, ignore_names)
    if not page_title and _starts_section(sections.current, level, section_level, ignored):
        sections.start(display, ignored, level)
        sections.current.add(ScriptItem(ITEM_HEADING, spoken, display, line.depth))
    else:
        sections.current.add(ScriptItem(ITEM_SUBHEADING, spoken, display, line.depth))


def _starts_section(current: _SectionBuilder, level: int, section_level: int, ignored: bool) -> bool:
    if current.key == "intro" or level <= section_level or ignored:
        return True
    # A heading at the ignored heading's level (or above) ends the ignored part.
    return (current.ignored and current.heading_level is not None
            and level <= current.heading_level)


def spoken_transcript(script: Script, include_ignored: bool = False) -> str:
    """Every non-empty spoken string of the sections that would be played, one per line."""
    return "\n".join(
        item.spoken
        for index in script.section_indices(include_ignored)
        for item in script.sections[index].items
        if item.spoken
    )
