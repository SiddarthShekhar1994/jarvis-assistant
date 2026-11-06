"""The private-data firewall (SPEC section 6, R4; parts A2 and A4): no Qt, nothing logged but counts.

Why: an agent holding private data, reading untrusted pages and able to reach the web could be tricked into sending that
data out inside a search or an address. A run starts clean, knowing only the owner's words. Once a private tool has
answered, whatever goes to the web is checked first, for the rest of the run: that is a fact of the run (note_private),
never the size of the taint set, which a note holding only short words leaves empty and an Allow can empty again.

    TaintSet(owner_words)        the run's private fragments: add(texts) (agent.owner feeds it from each private tool's
                                 result), note_owner(words), hits(text), split_hits(text, recent), foreign(text),
                                 oddly_written(text), clear(hits, words)
    ReadWords()                  runs of other people's words this run has read: feed(text), holds(line)
    looks_encoded(query)         a query that looks like data rather than words
    decoded_forms(text)          ``text`` and what it decodes to, percent-encoded any number of times
    WebFirewall(book)            policy.Firewall for the web tools:
        attach(ask)              how it asks the owner: the run's ask_web(text, choices, on_answer)
        owner_said(words)        the owner's words: what is not tainted now never taints, and while the run holds nothing
                                 private their addresses count as seen; said() gives them back, joined, and messages()
                                 one by one: the words a licence is read from (agent.owner)
        note_private()           a private tool answered, whatever it fed, or the owner typed something while the task ran
                                 (agent.runner): the checks are on from now on, for good
        note_goal(words)         the same, from the start, when the request itself plainly holds something private
                                 (holds_something_private): its words stay the owner's and its addresses seen
        owner_wrote(address)     that address stands in the owner's own words: for a card's link, never a page's load
        go_strict()              the run has read a mail's body, a file's contents or the conversation (strict)
        check(tool, args)        before web_search, open_page, fetch_text and open_thing's site: None, or what goes back
                                 to the model instead
        ask_once(name, exact, text)   the same card, for a change the owner's words don't plainly ask for (pc_control)
        speakable(line)          may this line the model wrote reach the voice? (the app's one gate: agent_ui)
        read_words               the run's ReadWords

The rules. In a clean run searches and public https addresses need no click. Once a private tool has answered,
web_search checks the query's words and open_page must use an address seen in this run (a search result, a link on a
page read, the owner's words before then), unchanged, checked after percent-decoding, and fetch_text is held to both: its
address as open_page's, its question's words as a search's; a hit, an unseen address or an encoded-looking query waits for an
amber card (the owner-question seam, kind "web") that shows the exact words or address, with "Allow" and "Don't". A
click allows that one call and clears those words for the run. No click, no call: a declined or unanswered call is
refused in plain words, so the model adapts, and the same call asked again is refused without a second card, whatever
has been cleared since. While the card is up the same call is told so, with no second card and nothing sent.

Typed or spoken words never allow one (agent.runner), and "only a click" holds at every moment, not just while a card is
up: once a run holds anything private, a later message never clears a word already in the taint set and never makes an
unseen address seen, whatever the sentence says ("no, don't search for that", "yes, search for that"). Those words still
reach the model and still license what the owner's words license (said(), messages()). The goal's words, and a later
word that was not tainted when it was said, stay on the owner's list and never taint.

Strict (part A4, stricter than the spec, because word matching can't cover whole mail bodies or file contents): once a
run has read a mail's body, a file's contents or the conversation, every search and fetch_text question that holds any
word outside the owner's own words and COMMON_WORDS (or SHORT_WORDS, the little words of any sentence), or any symbol
that is not ordinary sentence punctuation (flag letters, Morse, an emoji), goes to the card, and every address must have
been seen in this run, unchanged. The calendar, contacts, notes and file NAMES only taint. It is the same card and the
same click: one allow clears only the words it showed, for this run.

Words are matched as they would read: look-alike letters, accents, invisible characters, cuts and glue aside; reversed,
in ROT13 and with the usual digits as letters; numbers in any form of digit, phone numbers and long runs, and runs of 4
to 6 digits but for years, the same numbers reversed or with zeros before them; base64 and hex of four characters and up.

How a word is written counts as well as how it reads, in any run that holds anything private (TaintSet.oddly_written),
so accents and marks are no way to carry something on a word that folds to the owner's: a word with anything outside
ASCII in it that is not spelled so in the owner's own words goes to the card (no table of look-alikes can be whole, and
a foreign-language query they did not spell takes one click), and so do two or more marks glued to a word, or standing
alone, that are not theirs. One mark at a time, and brackets or quotes round a word, are ordinary writing.

The honest limit: word matching can't catch what is not written as the word. In a run that is not strict (one that has
read only the calendar, contacts, notes or file names) a paraphrase, a word with a letter dropped, an amount or a time
go out with no card, and a strict run still passes anything spelled in common words (an acrostic, yes and no as bits, a
letter's case, one mark on a word). A page can offer links whose choice says something (each is an address seen). The
address rule and the window's having no way to type or post are the strong parts; this is a net under them, not a proof.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
import threading
import unicodedata
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import unquote_plus

from ..actions import link_host, web_link_problem
from ..desktop import guard
from ..format_chars import invisible_char
from .tools import WAIT_TEXT, Tool, ToolResult, error
from .web import AddressBook, owner_addresses

WEB_ALLOW, WEB_DONT = "Allow", "Don't"         # the card's choices; only the first, clicked, allows anything
WEB_CHOICES = (WEB_ALLOW, WEB_DONT)
SEARCH_CARD = 'Jarvis wants to search the web for "{words}"'
OPEN_CARD = "Jarvis wants to open {words}"
FETCH_CARD = "Jarvis wants to read this another way: {words}"
FETCH_JOIN = " - asking: "       # between the address and the question, on the card and in what is compared
# An address that is long or carries something private: its host and what it carries come first, above any fold.
OPEN_HOST_CARD = "Jarvis wants to open {host}.{carries} The whole address: {words}"
FETCH_HOST_CARD = "Jarvis wants to read {host} another way.{carries} The whole address and question: {words}"
CARRIES_TEXT = " It carries: {words}."
ADDRESS_SHOWN = 120              # characters of an address that need no line of their own
CARRIES_SHOWN = 6                # words a card names first; the exact address follows
BUSY_TEXT = ("A question is waiting for the owner already, so that call was not made. End your turn now; the answer "
             "comes as your next message.")
REFUSED_TEXT = ("The owner did not allow that call, so it was not made. Do not try it again: carry on without it, find "
                "another way, or say so in your report.")
# Result codes (the logs' and Live's: fixed words).
WAITING, DECLINED, CARD_BUSY = "waiting", "declined", "card_busy"
WAITING_NOTE = "waiting for your OK"

WORD_MIN = 4                     # a name, title, subject or filename word of this many letters or more is private
GLUED_MIN = 6                    # a word this long counts inside a longer one ("anasmith"); shorter ones are too common
SPLIT_MIN = 5                    # a word this long counts when split across calls
SPLIT_MAX = 64                   # ... and no longer: a run of letters that long is no word, and its work is its square
PIECE_MIN = 2                    # ... into pieces at least this long
RECENT_CALLS = 8                 # the last calls looked at for words split across them
ENCODED_LONG = 40                # a token without spaces this long looks like data, whatever it holds
ENCODED_MIN = 16                 # base64-like from this length
HEX_MIN = 12
DECODE_ROUNDS = 4
PHONE_MIN, PHONE_MAX = 7, 15     # digits in a phone number
PHONE_TAIL = 7                   # ... matched by its last digits: a country code may come or go; a longer run of digits
                                 # (an account or a card number) by any this many of its digits in a row
DIGIT_RUN_MIN = 7                # digits in a row that taint on their own, whatever surrounds them
BASE64_WORD_MIN = 8              # a token this long is also read as what it may decode to (base64 or hex)
BASE64_HITS_MIN = 4              # ... this long when it is only compared with what the run holds ("NDgyMQ": a locker code)
READ_RUN = 6                     # words in a row of what was read that a line may not hold (ReadWords)
READ_RUN_OUTSIDE = 2             # ... at least this many of them outside COMMON_WORDS and SHORT_WORDS
READ_RUNS_CAP = 200_000          # runs a run remembers, the oldest going first
# Bounded, with "@" out of its classes: this runs on text anyone can send. Unbounded it takes minutes on "a@a@a@..." or
# any long run of characters, and the GIL is held throughout: nothing else runs, Stop neither.
_EMAIL_RE = re.compile(r"[^\s<>\"'(),;:@]{1,64}@[^\s<>\"'(),;:@]{1,253}\.[^\s<>\"'(),;:.@]{2,63}")
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-/]{5,22}\d")
_DIGIT_RUN_RE = re.compile(r"\d{%d,}" % DIGIT_RUN_MIN)
_REQUEST_NUMBER_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")       # in a request: a code, an account, a reference
REQUEST_GROUPED_DIGITS = 9       # ... or this many in groups (a card, a phone, an order), which a date never is
_SHORT_NUMBER_RE = re.compile(r"(?<!\d)\d{4,6}(?!\d)")       # a locker code, a PIN, a reference: years left out
_YEAR_RE = re.compile(r"(?:19|20)\d\d")
_ANY_DIGITS_RE = re.compile(r"\d+")
_DIGIT_JOIN_RE = re.compile(r"(?<=\d)[\s.,\-_/]{1,3}(?=\d)")  # "4 8 2 1", "48-21": one number to whoever reads it
_TOKEN_RE = re.compile(r"[^\W_]+")
_BASE64_RE = re.compile(r"[A-Za-z0-9+/_=\-]+")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_PERCENT_RE = re.compile(r"%[0-9a-fA-F]{2}")
_HIDDEN_CATEGORIES = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")
_SYMBOL_CATEGORIES = ("So", "Sk", "Sm")      # what a strict run counts as a word though it holds no letter
_SENTENCE_MARKS = frozenset(",;:!?'\"()&/\u2018\u2019\u201C\u201D\u00AB\u00BB")   # one of these alone is no word
_MARK_RUN_RE = re.compile(r"(?:\W|_)+")
# What may stand at a word's ends as ordinary writing, two marks or more: an opening pair, ("(, and what closes it with
# a sentence's own mark, either way round, ).", ."), or any one mark with a sentence's own, %, or Inc., and in the middle of
# one a range of money or of per cents: $100-$500, 10%-20%.
_START_MARKS_RE = re.compile(r"[(\[{'\"\u2018\u201C\u00AB]{2}")
_END_MARKS_RE = re.compile(r"[)\]}'\"\u2019\u201D\u00BB]{1,2}[.,;:!?]?|[.,;:!?][)\]}'\"\u2019\u201D\u00BB]{1,2}"
                           r"|[^.,;:!?][.,;:!?]|\.[,;:]")
_MIDDLE_MARKS_RE = re.compile(r"[-/][$\u00A3\u20AC\u00A5]|%[-/]")
_SENTENCE_ENDS = ".,;:!?"
# Where a text is cut to see how its words are written: spaces, ASCII's marks, a sentence's curly quotes and dashes, and
# money. None of these is a letter, and a word's own marks are looked at apart (_glued_marks).
_SPELLING_CUT_RE = re.compile(r"[\s!-/:-@\[-`{-~\u2018\u2019\u201C\u201D\u00AB\u00BB\u2013\u2014"
                              r"\u00A2-\u00A5\u20A0-\u20BF]+")
_ROT13 = str.maketrans("abcdefghijklmnopqrstuvwxyz", "nopqrstuvwxyzabcdefghijklm")
_LEET_I = str.maketrans("310457", "eioast")  # the usual digits as letters: 3 e, 1 i or l, 0 o, 4 a, 5 s, 7 t
_LEET_L = str.maketrans("310457", "eloast")

# Words of 4+ letters too common to be private on their own, the words of the web itself, and of a file's place and type
# (the allowed folders' own names, "docx"). Lower case.
COMMON_WORDS = frozenset("""
able about above account across action actually added address after again against agenda ahead allow almost alone along
already also although always among amount another answer anyone anything appointment april area around asked attached
august available away back bank based been before began behind being below best better between bill both bring brought
budget build business busy call called came cannot care case cause certain change changes check checked city class
clear client close come comes coming company complete computer contact content copy could country course create current
customer daily data date dates days deal dear december department design detail details different dinner does doing done
door down draft drive during each early email emails ending enough even event events ever every example file files
final find first five following follow food form forward four free friday from front full further gave general get give
given goes going gone good great group half hand happy have having head hear hello help here high hold home hope hour
hours house however html http https important inbox include including information instead into invoice issue item items
january join july june just keep kind know known large last late later learn least leave left less letter life like
line link list little live local long look looking lunch made mail main make many march material matter maybe mean
meeting meetings member message might minutes monday money month months more morning most move much must name names near
need needs never news next nice night note notes nothing november number october office often once only open order other
others over page paper part party pass past payment people percent perhaps person phone photo place plan plans please
point post power price print private product program project public question quick quite read ready real really reason
receipt receive recent regarding remember reminder reply report request review right room saturday said same schedule
school second section send sent september service services shall share short should show side sign since small some
someone something soon sorry start started state still stop subject such summary sunday support sure system take talk
task team tell term than thank thanks that their them then there these they thing things think this those though three
through thursday ticket time times today together told tomorrow took total travel trip tuesday under until update
updated upon urgent used user using very view visit want wanted watch week weekly weeks well went were what when where
which while whole will with within without wednesday word words work working world would write year years yesterday your
yours
document documents download downloads desktop picture pictures music video videos docx xlsx pptx jpeg json yaml text
""".split())
# The little words of any sentence: a word under WORD_MIN letters can't be private by itself unless it is one of these
# (a name such as "Sam" or "Ann" is not). They go with COMMON_WORDS where a strict run looks at a question's words.
SHORT_WORDS = frozenset("""
a an the of to in on at by for from and or but not no yes is it its as be are was can how why who what do did does i me
my you we us our vs per via with than that this
""".split())


def _unseen(char: str) -> bool:
    """No one could see ``char``: a control, an unassigned or private character, or what draws nothing or a blank
    (format_chars.invisible_char: variation selectors, fillers, the Braille blank ...)."""
    return unicodedata.category(char) in _HIDDEN_CATEGORIES or invisible_char(char)


def visible(text: str) -> str:
    """``text`` as a card shows it: the exact words, with every character no one could see spelled out ("<U+202E>"), so
    a card can't hide what it asks about."""
    return "".join(f"<U+{ord(char):04X}>" if _unseen(char) else char for char in text)


def decoded_forms(text: str) -> list[str]:
    """``text``, then what it percent-decodes to, round after round (double encoding included), then with its HTML
    entities read. At most DECODE_ROUNDS rounds; each is what a server or a page would read."""
    forms = [text]
    current = text
    for _round in range(DECODE_ROUNDS):
        decoded = unquote_plus(current)
        if decoded == current:
            break
        forms.append(decoded)
        current = decoded
    entities = html.unescape(current)
    if entities != current:
        forms.append(entities)
    return forms


def _printable(raw: bytes) -> str:
    """``raw`` as text when it reads as text, else ""."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    return text if text and all(char.isprintable() or char.isspace() for char in text) else ""


def encoded_forms(text: str, shortest: int = BASE64_WORD_MIN) -> list[str]:
    """What the tokens of ``text`` may decode to, as base64 (the web-safe kind too) or hex, when that reads as text: a
    word hidden so is still that word. ``shortest``: the tokens read. Only ever adds something to look through; so only
    what compares those forms with what the run holds reads the short ones (hits): a plain word such as "bill" reads as
    "n)e", and a strict run's rule would count that as words of its own."""
    forms = []
    for token in re.findall(r"[A-Za-z0-9+/_\-]{%d,}={0,2}" % shortest, text or ""):
        body = token.rstrip("=").replace("-", "+").replace("_", "/")
        try:
            decoded = _printable(base64.b64decode(body + "=" * (-len(body) % 4), validate=True))
        except (binascii.Error, ValueError):
            decoded = ""
        if decoded:
            forms.append(decoded)
        if _HEX_RE.fullmatch(token) and len(token) % 2 == 0:
            decoded = _printable(bytes.fromhex(token))
            if decoded:
                forms.append(decoded)
    return forms


def _fold(text: str) -> str:
    """For comparing: what tag characters spell, look-alike letters as Latin, no accents, no invisible characters, one
    case. "Jos\u00e9", "JOSE" and "J\u043ese" (a Cyrillic o) are one word here."""
    plain = guard._untag(text).translate(guard._LOOKALIKES)
    parts = unicodedata.normalize("NFKD", plain)
    return "".join(char for char in parts if not unicodedata.combining(char) and not _unseen(char)).casefold()


def _tokens(folded: str) -> list[str]:
    return _TOKEN_RE.findall(folded)


def _ascii_digits(text: str) -> str:
    """``text`` with every digit as its plain value, whatever form it is written in (fullwidth, Arabic-Indic,
    Devanagari, math, circled)."""
    if text.isascii():
        return text
    return "".join(str(value) if (value := unicodedata.digit(char, None)) is not None else char for char in text)


def _digits(text: str) -> str:
    """The digits of ``text`` and nothing else, each as its value."""
    plain = _ascii_digits(text if text.isascii() else unicodedata.normalize("NFKD", text))
    return "".join(char for char in plain if char.isascii() and char.isdigit())


def holds_something_private(words: str) -> bool:
    """The owner's request itself plainly holds what must not leave by an address a page names: a key or a word for a
    secret, an email address, a long number. Only ever turns the checks on (WebFirewall.note_goal)."""
    plain = _ascii_digits(words or "")
    return (guard.holds_secret(plain) or guard.asks_for_secret(plain) or _EMAIL_RE.search(plain) is not None
            or _REQUEST_NUMBER_RE.search(plain) is not None
            or any(len(_digits(found)) >= REQUEST_GROUPED_DIGITS for found in _PHONE_RE.findall(plain)))


def _digit_runs(text: str) -> set[str]:
    """The numbers ``text`` writes, as itself and with the little marks between their digits gone ("4 8 2 1")."""
    plain = _ascii_digits(text if text.isascii() else unicodedata.normalize("NFKD", text))
    return set(_ANY_DIGITS_RE.findall(plain)) | set(_ANY_DIGITS_RE.findall(_DIGIT_JOIN_RE.sub("", plain)))


def _other_readings(tokens: Iterable[str]) -> set[str]:
    """What a word may be hiding: itself reversed, in ROT13, each letter doubled, and with the usual digits as letters.
    A common word is read as itself only: "live" is no "evil"."""
    found: set[str] = set()
    for token in tokens:
        if len(token) < WORD_MIN or token in COMMON_WORDS or token in SHORT_WORDS:
            continue
        found |= {token[::-1], token.translate(_ROT13)}
        if len(token) % 2 == 0 and token[::2] == token[1::2]:
            found.add(token[::2])
        if any(char.isdigit() for char in token) and any(char.isalpha() for char in token):
            found |= {token.translate(_LEET_I), token.translate(_LEET_L)}
    return found


def _glued_marks(folded: str) -> set[str]:
    """Marks that write something, in any run that holds anything private: two or more in a row glued to a word
    ("laptops--.-..-", "laptops.,.,"), or standing alone ("--.-", "..."). One mark at a time is ordinary writing
    ("well-known", "U.S.", "site:example.com"), and so are brackets or quotes round a word and a sentence's own mark at
    its end (_START_MARKS_RE, _END_MARKS_RE, _MIDDLE_MARKS_RE)."""
    found: set[str] = set()
    for part in folded.split():
        for run in _MARK_RUN_RE.finditer(part):
            marks = run.group()
            if len(marks) < 2:
                continue
            if run.start() == 0 and run.end() == len(part):
                found.add(marks)
            elif run.start() == 0 and _START_MARKS_RE.fullmatch(marks):
                continue
            elif run.end() == len(part) and _END_MARKS_RE.fullmatch(marks):
                continue
            elif _MIDDLE_MARKS_RE.fullmatch(marks):
                continue
            else:
                found.add(marks.rstrip(_SENTENCE_ENDS) or marks)   # "C++," holds what "C++" does
    return found


def _marks(folded: str) -> set[str]:
    """What a strict run counts as a word though it holds no letter or digit: a run of symbols anywhere (flag letters,
    an emoji, "+"), whatever stands alone with no letter in it (Morse, "...", "-") but one ordinary sentence mark, and
    marks glued to a word (_glued_marks)."""
    found = _glued_marks(folded)
    for part in folded.split():
        if not any(char.isalnum() for char in part):
            if len(part) > 1 or part not in _SENTENCE_MARKS:
                found.add(part)
            continue
        run = ""
        for char in part:
            if unicodedata.category(char) in _SYMBOL_CATEGORIES:
                run += char
            elif run:
                found.add(run)
                run = ""
        if run:
            found.add(run)
    return found


def _spelled(text: str) -> list[str]:
    """The pieces of ``text`` written with anything outside ASCII, as they are written (one form, one case): accents,
    look-alike letters, combining marks and other scripts all stay in them, where folding would take them away."""
    return [unicodedata.normalize("NFC", piece).casefold() for piece in _SPELLING_CUT_RE.split(text or "")
            if piece and not piece.isascii()]


def _mixed_letters(token: str) -> bool:
    """``token`` holds a word with a letter that folding leaves outside ASCII beside ASCII letters: a look-alike no
    table knows, or a name like Soren with a stroked o."""
    for word in _tokens(_fold(token)):
        if word.isascii():
            continue
        if any(char.isascii() and char.isalpha() for char in word) and any(
                not char.isascii() and char.isalpha() for char in word):
            return True
    return False


def _crosses(joined: str, word: str, cuts: set[int]) -> bool:
    """``word`` is in ``joined`` over one of the ``cuts`` between its pieces."""
    start = joined.find(word)
    while start >= 0:
        if any(start < cut < start + len(word) for cut in cuts):
            return True
        start = joined.find(word, start + 1)
    return False


def looks_encoded(query: str, letters: bool = True) -> bool:
    """A query that looks like data rather than words: invisible characters, percent-encoding, a very long token without
    spaces, a base64-like or hex-like token, or (``letters``) a word with a letter that reads as none beside ASCII letters.
    "The-Lord-of-the-Rings" and long plain words are words. False for ``letters``: where TaintSet.oddly_written looks at
    how a word is written, and knows how the owner spelled it."""
    if any(_unseen(char) and char not in "\t\n\r " for char in query):
        return True
    if len(_PERCENT_RE.findall(query)) >= 2:
        return True
    for token in query.split():
        if len(token) >= ENCODED_LONG:
            return True
        if len(token) >= HEX_MIN and _HEX_RE.fullmatch(token) and any(c.isdigit() for c in token) \
                and any(c.isalpha() for c in token):
            return True
        if len(token) >= ENCODED_MIN and _BASE64_RE.fullmatch(token) and _mixed(token):
            return True
        if letters and _mixed_letters(token):
            return True
    return False


def _mixed(token: str) -> bool:
    if token.endswith("="):
        return True
    pieces = [piece for piece in re.split(r"[-_+/]", token) if piece]
    if pieces and all(piece.isalpha() for piece in pieces):          # hyphenated words
        return False
    letters = "".join(char for char in token if char.isalpha())
    has_digit = any(char.isdigit() for char in token)
    two_cases = any(char.islower() for char in letters) and any(char.isupper() for char in letters)
    return has_digit or two_cases


class TaintSet:
    """The run's private fragments. Thread-safe; memory only; never in a repr or a log. See the module docs."""

    def __init__(self, owner_words: str = "") -> None:
        self._lock = threading.Lock()
        self._owner: set[str] = set()
        self._words: set[str] = set()
        self._emails: set[str] = set()
        self._phones: set[str] = set()
        self._numbers: set[str] = set()      # runs of 4 to 6 digits, matched only as whole numbers
        self._spelling = ""                  # how the owner wrote what is not ASCII (oddly_written)
        if owner_words:
            self.note_owner(owner_words)

    def __repr__(self) -> str:        # counts only
        return (f"TaintSet({len(self._words)} words, {len(self._emails)} addresses, "
                f"{len(self._phones) + len(self._numbers)} numbers)")

    def owner_words(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._owner)

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._words or self._emails or self._phones or self._numbers)

    def note_owner(self, words: str) -> None:
        """The owner said these: what is not tainted now goes on their list and never taints later. What is tainted stays
        so, whatever the sentence says ("no, don't search for it"): only a click on its card clears a word (clear)."""
        plain = _ascii_digits(words or "")
        folded = _fold(plain)
        tokens = set(_tokens(folded))
        emails = {_fold(found) for found in _EMAIL_RE.findall(plain)}
        phones = {_digits(found) for found in _PHONE_RE.findall(plain)}
        spelled = "".join(f"{piece}\n" for piece in _spelled(words))
        with self._lock:
            self._owner |= ((tokens - self._words - self._phones - self._numbers) | (emails - self._emails)
                            | (phones - self._phones) | _marks(folded))
            self._spelling += spelled

    def add(self, fragments: Iterable[str]) -> int:
        """Text from a private tool's result: its addresses, its phone numbers and other runs of digits (years left
        out), and its words of WORD_MIN+ letters but for the common ones and the owner's own. How many fragments are
        new."""
        before = self._size()
        for text in fragments:
            if isinstance(text, str) and text:
                self._add_one(text)
        return self._size() - before

    def _size(self) -> int:
        with self._lock:
            return len(self._words) + len(self._emails) + len(self._phones) + len(self._numbers)

    def _add_one(self, text: str) -> None:
        text = _ascii_digits(text)
        emails = {_fold(found) for found in _EMAIL_RE.findall(text)}
        rest = _EMAIL_RE.sub(" ", text)
        phones = {_digits(found) for found in _PHONE_RE.findall(rest)}
        phones = {digits for digits in phones if len(digits) >= PHONE_MIN}
        phones |= set(_DIGIT_RUN_RE.findall(rest))
        numbers = {found for found in _SHORT_NUMBER_RE.findall(rest) if not _YEAR_RE.fullmatch(found)}
        words = set(_tokens(_fold(rest)))
        for email in emails:                   # its name part and its own domain's name are words of it too
            name, _at, domain = email.partition("@")
            words |= set(_tokens(name)) | set(_tokens(domain.rpartition(".")[0]))
        kept = {word for word in words if len(word) >= WORD_MIN and any(char.isalpha() for char in word)
                and word not in COMMON_WORDS}
        with self._lock:
            self._emails |= emails - self._owner
            self._phones |= phones - self._owner
            self._numbers |= numbers - self._owner
            self._words |= kept - self._owner

    def hits(self, text: str) -> tuple[str, ...]:
        """The fragments ``text`` carries, as itself or percent-decoded any number of times. A word counts as a word of
        its own, across the pieces it was cut into ("Sam antha", "s-a-m-a-n-t-h-a"), when GLUED_MIN+ letters long inside
        a longer one, and read reversed, in ROT13 or with digits for letters. A number in any form of digit: a phone
        number or a long run as _number_in says, a run of 4 to 6 digits as a whole number."""
        found: set[str] = set()
        with self._lock:
            words, emails = set(self._words), set(self._emails)
            phones, numbers = set(self._phones), set(self._numbers)
        if not (words or emails or phones or numbers):
            return ()
        decoded = decoded_forms(text or "")
        for one in decoded:                    # a short token holds a word or a number at most: each compared whole
            for short in encoded_forms(one, BASE64_HITS_MIN):
                found |= words & set(_tokens(_fold(short)))
                found |= _numbers_in(numbers, _digit_runs(short))
        for form in [*decoded, *(hidden for one in decoded for hidden in encoded_forms(one))]:
            folded = _fold(form)
            tokens = _tokens(folded)
            joined = "".join(tokens)
            cuts, at = set(), 0
            for token in tokens[:-1]:
                at += len(token)
                cuts.add(at)
            digits = _digits(form)
            found |= {email for email in emails if email in folded}
            found |= {phone for phone in phones if _number_in(phone, digits) or _number_in(phone, digits[::-1])}
            found |= _numbers_in(numbers, _digit_runs(form))
            present = set(tokens) | _other_readings(tokens)
            others = (joined[::-1], joined.translate(_ROT13))
            for word in words:
                if word in present or (word in joined and (len(word) >= GLUED_MIN or _crosses(joined, word, cuts))) \
                        or (len(word) >= GLUED_MIN and (word in others[0] or word in others[1])):
                    found.add(word)
        return tuple(sorted(found))

    def foreign(self, text: str) -> tuple[str, ...]:
        """A strict run's rule: the words ``text`` holds, as itself or decoded, that are neither the owner's own nor
        common ones, and its symbols that are not ordinary sentence punctuation (_marks). What it may decode to as
        base64 or hex counts too."""
        owner = self.owner_words()
        found: set[str] = set()
        decoded = decoded_forms(text or "")
        for form in [*decoded, *(hidden for one in decoded for hidden in encoded_forms(one))]:
            folded = _fold(form)
            found |= {token for token in _tokens(folded)
                      if token not in owner and token not in COMMON_WORDS and token not in SHORT_WORDS}
            found |= _marks(folded) - owner
        return tuple(sorted(found))

    def oddly_written(self, text: str) -> bool:
        """``text``, as it would go out, is written in a way folding hides, in any run that holds anything private: a word
        with anything outside ASCII in it (accents, look-alike letters, combining marks, other scripts: no table needed)
        that is not spelled so in the owner's own words, or marks glued to a word that are not theirs either. It takes one
        click, and a foreign-language query the owner spelled that way none."""
        with self._lock:
            spelling, owner = self._spelling, frozenset(self._owner)
        if any(piece not in spelling for piece in _spelled(text)):
            return True
        return bool(_glued_marks(_fold(text or "")) - owner)

    def split_hits(self, text: str, recent: Iterable[str]) -> tuple[str, ...]:
        """The words of SPLIT_MIN+ letters that ``text`` completes with the calls before it: in a row, or any one of
        them and this one, either way round."""
        with self._lock:
            words = {word for word in self._words if SPLIT_MIN <= len(word) <= SPLIT_MAX}
        if not words:
            return ()
        pieces = [piece for piece in recent if piece]
        now = _joined(text)
        found: set[str] = set()
        run, cuts = "", set()
        for piece in [*pieces, now]:
            run += piece
            cuts.add(len(run))
        cuts.discard(len(run))
        for word in words:
            if word in run and _crosses(run, word, cuts):
                found.add(word)
            for piece in pieces:
                if _crosses(piece + now, word, {len(piece)}) or _crosses(now + piece, word, {len(now)}):
                    found.add(word)
        return tuple(sorted(found))

    def pieced_hits(self, text: str, recent: Iterable[Iterable[str]]) -> tuple[str, ...]:
        """The words of SPLIT_MIN+ letters that ``text``'s words complete with the words of the calls before it,
        whatever stood between them ("Zephy news", then "rine news"). ``recent``: those calls' words."""
        with self._lock:
            words = {word for word in self._words if SPLIT_MIN <= len(word) <= SPLIT_MAX}
        now = {token for token in call_words(text) if len(token) >= PIECE_MIN}
        if not words or not now:
            return ()
        pieces = now | {token for call in recent for token in call if len(token) >= PIECE_MIN}
        return tuple(sorted(word for word in words if _pieced(word, pieces, now)))

    def clear(self, fragments: Iterable[str], words: str = "") -> None:
        """The owner allowed these words to go out: they no longer count, for the rest of the run. ``words``: as they were
        written on the card that was allowed (oddly_written)."""
        gone = set(fragments)
        spelled = "".join(f"{piece}\n" for piece in _spelled(words))
        marks = _glued_marks(_fold(words))
        with self._lock:
            self._words -= gone
            self._emails -= gone
            self._phones -= gone
            self._numbers -= gone
            self._owner |= gone | marks
            self._spelling += spelled


class ReadWords:
    """Runs of other people's words this run has read (SPEC 7: "web text never read out"): a line in Jarvis's voice may
    not hold READ_RUN words in a row of them, at least READ_RUN_OUTSIDE of those outside the common words, so a common
    phrase still passes. Fed with what pages, fetch_text, mail and files gave back. Memory only, and only as hashes: no
    word of it stays here. Thread-safe; its repr holds a count."""

    def __init__(self, cap: int = READ_RUNS_CAP) -> None:
        self._lock = threading.Lock()
        self._cap = cap
        self._runs: set[int] = set()
        self._order: deque[int] = deque()

    def __repr__(self) -> str:
        return f"ReadWords({len(self._runs)} runs)"

    @staticmethod
    def _of(text: str) -> list[int]:
        tokens = _tokens(_fold(text or ""))
        if len(tokens) < READ_RUN:
            return []
        outside = [token not in COMMON_WORDS and token not in SHORT_WORDS for token in tokens]
        return [hash(tuple(tokens[start:start + READ_RUN])) for start in range(len(tokens) - READ_RUN + 1)
                if sum(outside[start:start + READ_RUN]) >= READ_RUN_OUTSIDE]

    def feed(self, text: str) -> None:
        runs = self._of(text)
        with self._lock:
            for run in runs:
                if run not in self._runs:
                    self._runs.add(run)
                    self._order.append(run)
            while len(self._order) > self._cap:
                self._runs.discard(self._order.popleft())

    def holds(self, line: str) -> bool:
        runs = self._of(line)
        with self._lock:
            return any(run in self._runs for run in runs)


def call_words(text: str) -> tuple[str, ...]:
    """The words of a call that went out, fully decoded and folded (TaintSet.pieced_hits)."""
    return tuple(_tokens(_fold(decoded_forms(text or "")[-1])))


def _pieced(word: str, pieces: set[str], now: set[str]) -> bool:
    """``word`` is made up of two or more of ``pieces``, one of them from ``now``, in a row."""
    reached: dict[int, set[bool]] = {0: {False}}
    for start in range(len(word)):
        for used in tuple(reached.get(start, ())):
            for end in range(start + PIECE_MIN, len(word) + 1):
                piece = word[start:end]
                if piece != word and piece in pieces:
                    reached.setdefault(end, set()).add(used or piece in now)
    return True in reached.get(len(word), ())


def _number_in(number: str, digits: str) -> bool:
    """A phone number by its last digits; a longer run of digits by any PHONE_TAIL of them in a row."""
    if len(number) <= PHONE_MAX:
        return number[-PHONE_TAIL:] in digits
    return any(number[start:start + PHONE_TAIL] in digits for start in range(len(number) - PHONE_TAIL + 1))


def _numbers_in(numbers: set[str], runs: set[str]) -> set[str]:
    """Which of ``numbers`` (runs of 4 to 6 digits) ``runs`` write: as themselves, reversed, or with zeros before them."""
    readings = runs | {run[::-1] for run in runs}
    plain = {reading.lstrip("0") for reading in readings}
    return {number for number in numbers if number in readings or (number.lstrip("0") or number) in plain}


def _joined(text: str) -> str:
    """``text``, fully decoded, as letters and digits only."""
    return "".join(_tokens(_fold(decoded_forms(text or "")[-1])))


def _busy() -> ToolResult:
    """A card is up, this call's own or another's: the call was not made, and no second card comes."""
    return ToolResult(BUSY_TEXT, code=CARD_BUSY, note="a question waits already")


def _card_words(card: str, exact: str, hits: tuple[str, ...]) -> str:
    """What a card says. The exact words or address, every character spelled out; an address that is long or carries
    something private has its host and those words first, so what matters is never below the fold with Allow in view."""
    address = exact.partition(FETCH_JOIN)[0]
    if card == SEARCH_CARD or (not hits and len(address) <= ADDRESS_SHOWN):
        return card.format(words=visible(exact))
    named = ", ".join(visible(word) for word in hits[:CARRIES_SHOWN]) + (" ..." if len(hits) > CARRIES_SHOWN else "")
    carries = CARRIES_TEXT.format(words=named) if hits else ""
    template = FETCH_HOST_CARD if card == FETCH_CARD else OPEN_HOST_CARD
    return template.format(host=visible(link_host(address) or ""), carries=carries, words=visible(exact))


class WebFirewall:
    """See the module docs. Thread-safe: its own lock is never held while it asks the run, and the run may tell it the
    answer while holding the run's."""

    def __init__(self, book: AddressBook, taint: TaintSet | None = None) -> None:
        self.book = book
        self.taint = taint if taint is not None else TaintSet()
        self._lock = threading.Lock()
        self._ask: Callable[[str, tuple[str, ...], Callable[[bool], None]], bool] | None = None
        self._recent: deque[str] = deque(maxlen=RECENT_CALLS)
        self._recent_words: deque[tuple[str, ...]] = deque(maxlen=RECENT_CALLS)
        self._allowed: set[tuple[str, str]] = set()
        self._refused: set[tuple[str, str]] = set()
        self._waiting: set[tuple[str, str]] = set()     # the call on a card that is up, from _shown to _answered
        self._said: list[str] = []
        self._wrote: set[str] = set()                   # the addresses in the owner's own words (owner_wrote)
        self.private = False
        self.strict = False
        self.cards = 0
        self.read_words = ReadWords()

    def __repr__(self) -> str:
        return f"WebFirewall({self.taint!r}, private {self.private}, strict {self.strict}, {self.cards} cards)"

    def note_private(self) -> None:
        """A private tool answered, whatever it fed: the model holds what it read now, even a note the taint set finds
        no word in, so the checks are on for good. Nothing the owner says or clicks turns them off."""
        self.private = True

    def note_goal(self, words: str) -> None:
        """The task's request, once owner_said has heard it: if it plainly holds something private the checks are on from
        the start, for a page could tell the model to carry that off by an address. Its words stay the owner's and its
        addresses seen."""
        if holds_something_private(words):
            self.note_private()

    def go_strict(self) -> None:
        """The run read a mail's body, a file's contents or the conversation: strict from now on, for good."""
        self.private = True
        self.strict = True

    def said(self) -> str:
        """Everything the owner has said in this task, in order: the goal and their messages. Nothing else ever comes
        here: never a tool's result, a file's name, a page or the model's words."""
        with self._lock:
            return ". ".join(self._said)

    def messages(self) -> tuple[str, ...]:
        """The same, message by message: a later one can take back what an earlier one licensed
        (dv.withdrawn_skills)."""
        with self._lock:
            return tuple(self._said)

    def speakable(self, line: str) -> bool:
        """May a line the model wrote reach the voice, which goes to Microsoft's online service? Yes while the run holds
        nothing private; after that only with no tainted word in it and, in a strict run, no foreign word."""
        if not (self.private or self.strict):
            return True
        return not self.taint.hits(line) and not (self.strict and self.taint.foreign(line))

    def attach(self, ask: Callable[[str, tuple[str, ...], Callable[[bool], None]], bool]) -> None:
        """``ask(text, choices, on_answer)``: the run shows the card (False: a question waits already, nothing shown)
        and tells ``on_answer(allowed)`` of the owner's click, or False when no answer came."""
        self._ask = ask

    def owner_said(self, words: str) -> None:
        """Their words, whenever they come: for said() and messages(), and on their list as note_owner says. An address
        in them counts as seen only while the run holds nothing private: after that, as a word already tainted, it goes
        to a card, and a click answers it. Typed or spoken words never do, whatever the sentence says."""
        self.taint.note_owner(words)
        addresses = owner_addresses(words)
        with self._lock:
            if words:
                self._said.append(" ".join(words.split()).rstrip(" .!?"))
            self._wrote.update(addresses)
        if self.private or self.strict:
            return
        for address in addresses:
            self.book.see(address)

    def owner_wrote(self, address: Any) -> bool:
        """``address`` stands in the owner's own words, exactly, whenever they said it. For a card's link, which a click
        on that card is what sends, and which shows there. Never for a page to load: that is ``book.seen``."""
        with self._lock:
            return address in self._wrote

    def _outgoing(self, tool: Tool, args: Mapping[str, Any]) -> tuple[str, str]:
        """(its card's words, exactly what would go out) of a call the firewall looks at, or ("", "") when none would
        go: a call with bad input is refused by the tool itself."""
        if tool.name == "web_search":
            query = args.get("query")
            return (SEARCH_CARD, query) if isinstance(query, str) and query.strip() else ("", "")
        if tool.name == "open_page":
            address = args.get("url") if "number" not in args else self.book.address(args.get("number"))
            if isinstance(address, str) and address and not web_link_problem(address):
                return OPEN_CARD, address
        if tool.name == "fetch_text":
            address, question = args.get("url"), args.get("question")
            if isinstance(address, str) and isinstance(question, str) and question.strip() \
                    and not web_link_problem(address):
                return FETCH_CARD, f"{address}{FETCH_JOIN}{' '.join(question.split())}"
        if tool.name == "open_thing":         # a site, under open_page's rules; a ref leaves this PC for nothing
            address = args.get("site")
            if isinstance(address, str) and address and not web_link_problem(address):
                return OPEN_CARD, address
        return "", ""

    def check(self, tool: Tool, args: Mapping[str, Any]) -> ToolResult | None:
        """Before every web call. None: it may go. See the module docs."""
        card, exact = self._outgoing(tool, args)
        if not card:
            return None
        call = (tool.name, exact)
        with self._lock:
            if call in self._refused:          # first and always: nothing cleared since brings a declined call back
                return error(REFUSED_TEXT, DECLINED)
            if call in self._waiting:          # its card is up: only a click on it answers it, whatever is seen since
                return _busy()
            hits = None
            if self.private or self.strict:
                if call in self._allowed:
                    self._allowed.discard(call)
                else:
                    hits = self._problem(tool, exact)
            if hits is None:
                self._recent.append(_joined(exact))
                self._recent_words.append(call_words(exact))
                return None
        return self._card(card, call, hits)

    def ask_once(self, name: str, exact: str, text: str) -> ToolResult | None:
        """A change the owner's words don't plainly ask for (agent.owner's pc_control), on the same card under the same
        rules: None once the owner has allowed this very call, for that one time; else the card, or the refusal of a
        call they declined. ``text``: Jarvis's own words for what it would do."""
        call = (name, exact)
        with self._lock:
            if call in self._refused:
                return error(REFUSED_TEXT, DECLINED)
            if call in self._allowed:
                self._allowed.discard(call)
                return None
        return self._shown(text, call, ())   # (its card already up: _shown says the call waits)

    def _problem(self, tool: Tool, exact: str) -> tuple[str, ...] | None:
        """The words it hit (maybe none: an unseen address, an encoded query), or None when the call may go."""
        hits = (set(self.taint.hits(exact)) | set(self.taint.split_hits(exact, self._recent))
                | set(self.taint.pieced_hits(exact, self._recent_words)))
        if self.strict and tool.name in ("web_search", "fetch_text"):
            asked = exact.partition(FETCH_JOIN)[2] if tool.name == "fetch_text" else exact
            hits |= set(self.taint.foreign(asked))
        if hits:
            return tuple(sorted(hits))
        if tool.name == "web_search":
            return () if self._reads_odd(exact) else None
        if tool.name == "fetch_text":         # the address as open_page's, the question's words as a search's
            address, _join, question = exact.partition(FETCH_JOIN)
            return () if self._reads_odd(question) or not self.book.seen(address) else None
        return () if not self.book.seen(exact) else None

    def _reads_odd(self, words: str) -> bool:
        return looks_encoded(words, letters=False) or self.taint.oddly_written(words)

    def _card(self, card: str, call: tuple[str, str], hits: tuple[str, ...]) -> ToolResult:
        return self._shown(_card_words(card, call[1], hits), call, hits)

    def _shown(self, text: str, call: tuple[str, str], hits: tuple[str, ...]) -> ToolResult:
        with self._lock:
            if call in self._waiting:
                return _busy()
            self._waiting.add(call)
        ask = self._ask
        if ask is None or not ask(text, WEB_CHOICES, lambda allowed: self._answered(call, hits, allowed)):
            with self._lock:
                self._waiting.discard(call)
            return _busy()
        with self._lock:
            self.cards += 1
        return ToolResult(WAIT_TEXT, code=WAITING, note=WAITING_NOTE, fields=(("Asked you", text),))

    def _answered(self, call: tuple[str, str], hits: tuple[str, ...], allowed: bool) -> None:
        """Allowed: that one call may go, once, and its words no longer count. Not: it never goes."""
        with self._lock:
            self._waiting.discard(call)
            if allowed:
                self._allowed.add(call)
                written = {"web_search": call[1], "fetch_text": call[1].partition(FETCH_JOIN)[2]}.get(call[0], "")
                self.taint.clear(hits, written)
            else:
                self._refused.add(call)


__all__ = ["COMMON_WORDS", "ReadWords", "SHORT_WORDS", "TaintSet", "WEB_ALLOW", "WEB_CHOICES", "WEB_DONT",
           "WebFirewall", "decoded_forms", "encoded_forms", "looks_encoded", "visible"]
