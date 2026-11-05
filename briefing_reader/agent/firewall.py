"""The private-data firewall (SPEC section 6, R4; parts A2 and A4): no Qt, nothing logged but counts.

Why: an agent holding private data, reading untrusted pages and able to reach the web could be tricked into sending that
data out inside a search or an address. A run starts clean, knowing only the owner's words. Once a private tool has
given it something, whatever goes to the web is checked first.

    TaintSet(owner_words)        the run's private fragments: add(texts) (agent.owner feeds it from each private tool's
                                 result), note_owner(words), hits(text), split_hits(text, recent), foreign(text),
                                 clear(hits)
    looks_encoded(query)         a query that looks like data rather than words
    decoded_forms(text)          ``text`` and what it decodes to, percent-encoded any number of times
    WebFirewall(book)            policy.Firewall for the web tools:
        attach(ask)              how it asks the owner: the run's ask_web(text, choices, on_answer)
        owner_said(words)        the owner's words never taint, and their addresses count as seen; said() gives them
                                 back, joined: the words a licence is read from (agent.owner)
        go_strict()              the run has read a mail's body, a file's contents or the conversation (strict)
        check(tool, args)        before web_search, open_page, fetch_text and open_thing's site: None, or what goes back
                                 to the model instead

The rules. In a clean run searches and public https addresses need no click. With any taint, web_search checks the
query's words and open_page must use an address seen in this run (a search result, a link on a page read, the owner's
words), unchanged, checked after percent-decoding, and fetch_text is held to both: its address as open_page's, its
question's words as a search's; a hit, an unseen address or an encoded-looking query waits for an
amber card (the owner-question seam, kind "web") that shows the exact words or address, with "Allow" and "Don't". A
click allows that one call and clears those words for the run. No click, no call: a declined or unanswered call is
refused in plain words, so the model adapts, and the same call asked again is refused without a second card. Typed or
spoken words never allow one (agent.runner).

Strict (part A4, stricter than the spec, because word matching can't cover whole mail bodies or file contents): once a
run has read a mail's body, a file's contents or the conversation, every search and fetch_text question that holds any
word outside the owner's own words and COMMON_WORDS (or SHORT_WORDS, the little words of any sentence) goes to the card,
and every address must have been seen in this run, unchanged. The calendar, contacts, notes and file NAMES only taint.
It is the same card and the same click: one allow clears only the words it showed, for this run.

The honest limit: word matching can't catch a determined paraphrase in a run that is not strict (one that has read only
the calendar, contacts, notes or file names), and a page can offer links whose choice says something (each is an address
seen). The address rule and the window's having no way to type or post are the strong parts; this is a net under them,
not a proof.
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

from ..actions import web_link_problem
from ..desktop import guard
from .tools import WAIT_TEXT, Tool, ToolResult, error
from .web import AddressBook, owner_addresses

WEB_ALLOW, WEB_DONT = "Allow", "Don't"         # the card's choices; only the first, clicked, allows anything
WEB_CHOICES = (WEB_ALLOW, WEB_DONT)
SEARCH_CARD = 'Jarvis wants to search the web for "{words}"'
OPEN_CARD = "Jarvis wants to open {words}"
FETCH_CARD = "Jarvis wants to read this another way: {words}"
FETCH_JOIN = " - asking: "       # between the address and the question, on the card and in what is compared
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
_EMAIL_RE = re.compile(r"[^\s<>\"'(),;:]+@[^\s<>\"'(),;:]+\.[^\s<>\"'(),;:.]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-/]{5,22}\d")
_DIGIT_RUN_RE = re.compile(r"\d{%d,}" % DIGIT_RUN_MIN)
_TOKEN_RE = re.compile(r"[^\W_]+")
_BASE64_RE = re.compile(r"[A-Za-z0-9+/_=\-]+")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_PERCENT_RE = re.compile(r"%[0-9a-fA-F]{2}")
_HIDDEN_CATEGORIES = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")

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


def visible(text: str) -> str:
    """``text`` as a card shows it: the exact words, with every character no one could see spelled out ("<U+202E>"), so
    a card can't hide what it asks about."""
    return "".join(f"<U+{ord(char):04X}>" if unicodedata.category(char) in _HIDDEN_CATEGORIES else char
                   for char in text)


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


def encoded_forms(text: str) -> list[str]:
    """What the tokens of ``text`` may decode to, as base64 (the web-safe kind too) or hex, when that reads as text: a
    word hidden so is still that word. Only ever adds something to look through."""
    forms = []
    for token in re.findall(r"[A-Za-z0-9+/_\-]{%d,}={0,2}" % BASE64_WORD_MIN, text or ""):
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
    return "".join(char for char in parts if not unicodedata.combining(char)
                   and unicodedata.category(char) not in _HIDDEN_CATEGORIES).casefold()


def _tokens(folded: str) -> list[str]:
    return _TOKEN_RE.findall(folded)


def _digits(text: str) -> str:
    return "".join(char for char in text if char.isascii() and char.isdigit())


def _crosses(joined: str, word: str, cuts: set[int]) -> bool:
    """``word`` is in ``joined`` over one of the ``cuts`` between its pieces."""
    start = joined.find(word)
    while start >= 0:
        if any(start < cut < start + len(word) for cut in cuts):
            return True
        start = joined.find(word, start + 1)
    return False


def looks_encoded(query: str) -> bool:
    """A query that looks like data rather than words: invisible characters, percent-encoding, a very long token without
    spaces, or a base64-like or hex-like token. "The-Lord-of-the-Rings" and long plain words are words."""
    if any(unicodedata.category(char) in _HIDDEN_CATEGORIES and char not in "\t\n\r " for char in query):
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
        if owner_words:
            self.note_owner(owner_words)

    def __repr__(self) -> str:        # counts only
        return f"TaintSet({len(self._words)} words, {len(self._emails)} addresses, {len(self._phones)} numbers)"

    def owner_words(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._owner)

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._words or self._emails or self._phones)

    def note_owner(self, words: str) -> None:
        """The owner said these: they never taint, and any that did no longer do."""
        tokens = set(_tokens(_fold(words or "")))
        emails = {_fold(found) for found in _EMAIL_RE.findall(words or "")}
        phones = {_digits(found) for found in _PHONE_RE.findall(words or "")}
        with self._lock:
            self._owner |= tokens | emails | phones
            self._words -= tokens
            self._emails -= emails
            self._phones -= phones

    def add(self, fragments: Iterable[str]) -> int:
        """Text from a private tool's result: its addresses, its phone numbers, and its words of WORD_MIN+ letters but
        for the common ones and the owner's own. How many fragments are new."""
        before = self._size()
        for text in fragments:
            if isinstance(text, str) and text:
                self._add_one(text)
        return self._size() - before

    def _size(self) -> int:
        with self._lock:
            return len(self._words) + len(self._emails) + len(self._phones)

    def _add_one(self, text: str) -> None:
        emails = {_fold(found) for found in _EMAIL_RE.findall(text)}
        rest = _EMAIL_RE.sub(" ", text)
        phones = {_digits(found) for found in _PHONE_RE.findall(rest)}
        phones = {digits for digits in phones if len(digits) >= PHONE_MIN}
        phones |= set(_DIGIT_RUN_RE.findall(rest))
        words = set(_tokens(_fold(rest)))
        for email in emails:                   # its name part and its own domain's name are words of it too
            name, _at, domain = email.partition("@")
            words |= set(_tokens(name)) | set(_tokens(domain.rpartition(".")[0]))
        kept = {word for word in words if len(word) >= WORD_MIN and any(char.isalpha() for char in word)
                and word not in COMMON_WORDS}
        with self._lock:
            self._emails |= emails - self._owner
            self._phones |= phones - self._owner
            self._words |= kept - self._owner

    def hits(self, text: str) -> tuple[str, ...]:
        """The fragments ``text`` carries, as itself or percent-decoded any number of times. A word counts as a word of
        its own, across the pieces it was cut into ("Sam antha", "s-a-m-a-n-t-h-a"), or when GLUED_MIN+ letters long
        inside a longer one."""
        found: set[str] = set()
        with self._lock:
            words, emails, phones = set(self._words), set(self._emails), set(self._phones)
        if not (words or emails or phones):
            return ()
        decoded = decoded_forms(text or "")
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
            found |= {phone for phone in phones if _number_in(phone, digits)}
            present = set(tokens)
            for word in words:
                if word in present or (word in joined and (len(word) >= GLUED_MIN or _crosses(joined, word, cuts))):
                    found.add(word)
        return tuple(sorted(found))

    def foreign(self, text: str) -> tuple[str, ...]:
        """A strict run's rule: the words ``text`` holds, as itself or decoded, that are neither the owner's own nor
        common ones. What it may decode to as base64 or hex counts too."""
        owner = self.owner_words()
        found: set[str] = set()
        decoded = decoded_forms(text or "")
        for form in [*decoded, *(hidden for one in decoded for hidden in encoded_forms(one))]:
            found |= {token for token in _tokens(_fold(form))
                      if token not in owner and token not in COMMON_WORDS and token not in SHORT_WORDS}
        return tuple(sorted(found))

    def split_hits(self, text: str, recent: Iterable[str]) -> tuple[str, ...]:
        """The words of SPLIT_MIN+ letters that ``text`` completes with the calls before it: in a row, or any one of
        them and this one, either way round."""
        with self._lock:
            words = {word for word in self._words if len(word) >= SPLIT_MIN}
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
            words = {word for word in self._words if len(word) >= SPLIT_MIN}
        now = {token for token in call_words(text) if len(token) >= PIECE_MIN}
        if not words or not now:
            return ()
        pieces = now | {token for call in recent for token in call if len(token) >= PIECE_MIN}
        return tuple(sorted(word for word in words if _pieced(word, pieces, now)))

    def clear(self, fragments: Iterable[str]) -> None:
        """The owner allowed these words to go out: they no longer count, for the rest of the run."""
        gone = set(fragments)
        with self._lock:
            self._words -= gone
            self._emails -= gone
            self._phones -= gone
            self._owner |= gone


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


def _joined(text: str) -> str:
    """``text``, fully decoded, as letters and digits only."""
    return "".join(_tokens(_fold(decoded_forms(text or "")[-1])))


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
        self._said: list[str] = []
        self.strict = False
        self.cards = 0

    def __repr__(self) -> str:
        return f"WebFirewall({self.taint!r}, strict {self.strict}, {self.cards} cards)"

    def go_strict(self) -> None:
        """The run read a mail's body, a file's contents or the conversation: strict from now on, for good."""
        self.strict = True

    def said(self) -> str:
        """Everything the owner has said in this task, in order: the goal and their messages. Nothing else ever comes
        here: never a tool's result, a file's name, a page or the model's words."""
        with self._lock:
            return ". ".join(self._said)

    def attach(self, ask: Callable[[str, tuple[str, ...], Callable[[bool], None]], bool]) -> None:
        """``ask(text, choices, on_answer)``: the run shows the card (False: a question waits already, nothing shown)
        and tells ``on_answer(allowed)`` of the owner's click, or False when no answer came."""
        self._ask = ask

    def owner_said(self, words: str) -> None:
        self.taint.note_owner(words)
        with self._lock:
            if words:
                self._said.append(" ".join(words.split()).rstrip(" .!?"))
        for address in owner_addresses(words):
            self.book.see(address)

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
            hits = None
            if self.taint or self.strict:
                if call in self._refused:
                    return error(REFUSED_TEXT, DECLINED)
                if call in self._allowed:
                    self._allowed.discard(call)
                else:
                    hits = self._problem(tool, exact)
            if hits is None:
                self._recent.append(_joined(exact))
                self._recent_words.append(call_words(exact))
                return None
        return self._card(card, call, hits)

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
            return () if looks_encoded(exact) else None
        if tool.name == "fetch_text":         # the address as open_page's, the question's words as a search's
            address, _join, question = exact.partition(FETCH_JOIN)
            return () if looks_encoded(question) or not self.book.seen(address) else None
        return () if not self.book.seen(exact) else None

    def _card(self, card: str, call: tuple[str, str], hits: tuple[str, ...]) -> ToolResult:
        text = card.format(words=visible(call[1]))
        ask = self._ask
        if ask is None or not ask(text, WEB_CHOICES, lambda allowed: self._answered(call, hits, allowed)):
            return ToolResult(BUSY_TEXT, code=CARD_BUSY, note="a question waits already")
        with self._lock:
            self.cards += 1
        return ToolResult(WAIT_TEXT, code=WAITING, note=WAITING_NOTE, fields=(("Asked you", text),))

    def _answered(self, call: tuple[str, str], hits: tuple[str, ...], allowed: bool) -> None:
        """Allowed: that one call may go, once, and its words no longer count. Not: it never goes."""
        with self._lock:
            if allowed:
                self._allowed.add(call)
                self.taint.clear(hits)
            else:
                self._refused.add(call)


__all__ = ["COMMON_WORDS", "SHORT_WORDS", "TaintSet", "WEB_ALLOW", "WEB_CHOICES", "WEB_DONT", "WebFirewall",
           "decoded_forms", "encoded_forms", "looks_encoded", "visible"]
