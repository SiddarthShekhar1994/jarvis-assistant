"""The agent's web tools, the part with no Qt (SPEC D6, D7, sections 6 and 7; part A2): what a search and a page read
are.

    ENGINES, engine_order(first)    the search engines' real results pages and how to read them, in the order they are
                                    tried: [agent] search_engine, then the others
    read_script(), results_script(engine), scroll_script(fraction)   JavaScript Jarvis runs in an isolated world: it
                                    shares only the page's DOM, so the page's scripts can neither see it run nor change
                                    a function or a property it uses (DOM clobbering included: Jarvis asks the
                                    prototypes). What the owner can't see is looked for as well, within a bound: a link
                                    transparent, tiny, off the page or the colour of its ground gets no number, and a
                                    page with much of its text so is flagged (screen-reader labels and skip links are
                                    normal)
    read_page(raw), read_results(raw)   what those scripts gave back, checked and capped
    wall_kind(text, url)            a captcha, a consent wall or a block: never answered, Jarvis moves on
    AddressBook                     the run's numbered addresses (results and links keep their number for the whole
                                    run) and every address seen in it, as exact strings: the firewall's list
    WebSession(browser, engine)     one run's use of the window: search, open_page, more, look, show. Thread-safe, one
                                    page at a time (tool calls may come side by side: they wait their turn)
    Browser                         the seam it drives (agent.browser.BrowserClient; a fake in the tests)
    workspace_name(path, token)     a workspace page's address back to a file's name, "" when it is not this task's
    suspect_lines(text)             how many lines hold a strong sign of an order to Jarvis (guard.strong_order)

A WebRefused is a call that did not happen or did not work: its code is a fixed word (the logs' and Live's), its text
Jarvis's own words for the model. Nothing here logs anything, and no repr holds a query, an address, a title or a word
of a page. Every wait is bounded by the call's own deadline, under the 45 s rule, and ends the moment the call is
cancelled.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote, quote_plus, unquote, urlsplit

from .. import snapshots
from ..actions import body_links, web_link_problem
from ..desktop import guard
from ..format_chars import drop_invisible
from .workspace import TaskFolder, WorkspaceRefused, check_name

QUERY_CAP = 300
URL_CAP = 2048
TEXT_PART = 12_000                # characters a tool gives back at a time
TEXT_KEEP = 400_000               # of one page's text, at most
MAX_LINKS = 60                    # numbered links given back for one page
MAX_RESULTS = 10
LINKS_SEEN = 400                  # anchors a page read looks at, at most
BOOK_CAP = 5_000                  # addresses a run remembers
TITLE_CAP = 200
LINK_TEXT_CAP = 120
SNIPPET_CAP = 300
LOAD_TIMEOUT_S = 25.0
SCRIPT_TIMEOUT_S = 5.0
RESULTS_WAIT_S = 4.0              # a results page that draws its results by script: this long at most after it loaded
TEXT_WAIT_S = 2.5                 # ... a page with no text yet
POLL_S = 0.25
MARGIN_S = 3.0                    # a call gives its own plain answer this long before the endpoint gives up on it
MIN_TRY_S = 2.0                   # the fallback chain tries no search page with less time left than this
PICTURE_WIDTH = 1280              # look_at_page: at most this wide ...
PICTURE_BYTES = 300_000           # ... and this big
LIVE_WIDTH = snapshots.MAX_WIDTH  # a page's picture on its step in Live
LIVE_BYTES = 1_500_000
PICTURE_KIND = "image/jpeg"
SCHEME = "jarvis-task"            # Jarvis's own address scheme: a file of this task's folder in the window
SUSPECT_LINE_CAP = 300            # the scanner reads this much of a sentence
HIDDEN_NODES = 2_000              # text nodes read_script looks at for words no one can see, at most ...
HIDDEN_MS = 150                   # ... and for this long
HIDDEN_MIN_CHARS = 300            # such words flag a page from this many characters (screen-reader labels, skip links
HIDDEN_MIN_SHARE = 0.10           # are normal) and this share of its text

# Why a web call did not work (WebRefused.code; the logs' and Live's fixed words).
W_NOT_PUBLIC, W_NO_PAGE, W_NO_RESULTS, W_NOT_LOADED, W_TOO_SLOW, W_NO_ANSWER, W_STOPPED, W_BUSY, W_NO_NUMBER = (
    "not_public", "no_page", "no_results", "not_loaded", "too_slow", "no_answer", "stopped", "busy", "no_number")
W_CRASHED, W_NO_ENGINE, W_TOO_BIG, W_NO_WINDOW, W_TEST_RUN = "crashed", "no_engine", "too_big", "no_window", "test_run"
W_REFUSED, W_HIDDEN = "site_refused", "hidden"
# What a wall is (wall_kind), and how a search page fared.
WALL_CAPTCHA, WALL_CONSENT, WALL_BLOCK = "captcha", "consent", "block"
T_NO_RESULTS, T_NOT_LOADED, T_NO_TIME = "no_results", "not_loaded", "no_time"

NO_WINDOW_TEXT = "Jarvis's web window is not available on this PC. Say so in your report."
NOT_PUBLIC_TEXT = "Jarvis opens only public https pages ({why}). Choose another address."
NO_NUMBER_TEXT = "No address has that number in this task. Use a number from a result or a link you were given."
NO_PAGE_TEXT = "No page is open in the window now. Open one with open_page first."
RESULTS_PAGE_TEXT = "That was a results page, with nothing more to read. Open a result with open_page."
BUSY_TEXT = "The window was busy with other pages for too long. Call this again."
HIDDEN_TEXT = "The owner has hidden the window, so there is no picture of it now. Read the page's text instead."
TOO_SLOW_TEXT = "That took too long and was given up on. Try another way, or say so in your report."
NO_ANSWER_TEXT = "That page stopped answering, so Jarvis closed it. Try another source, or say so in your report."
STOPPED_TEXT = "The task was stopped."
LOAD_WORDS = {
    "site_refused": (W_REFUSED, "This site refuses automated browsers (HTTP {status}). Don't retry it here. Try "
                                "fetch_text or another source."),
    "timeout": (W_TOO_SLOW, "That page did not load within {seconds} seconds. Try another source."),
    "load_failed": (W_NOT_LOADED, "That page did not load: it may be gone, refuse private windows, or be a download, "
                                  "which Jarvis never takes. Try another source."),
    "crashed": (W_CRASHED, "That page crashed the window's page process, so nothing was read from it. Try another "
                           "source, or say so in your report."),
    "no_engine": (W_NO_ENGINE, "This PC's Jarvis has no web engine (QtWebEngine), so it cannot open pages. Say so in "
                               "your report."),
    "test_run": (W_TEST_RUN, "No page is opened in a test run."),
    "cancelled": (W_STOPPED, STOPPED_TEXT),
    "shutdown": (W_STOPPED, STOPPED_TEXT),
}
ENGINE_FAILED_WORDS = (W_NO_WINDOW,
                       "Jarvis's private web window could not be started on this PC. Say so in your report.")
NO_RESULTS_TEXT = ("No search page gave results for that: {tried}. Jarvis never answers a captcha or clicks through a "
                   "wall. Try other words, open an address you know, or say so in your report.")
TRIED_WORDS = {T_NO_RESULTS: "{name} had no results", T_NOT_LOADED: "{name} did not load",
               T_NO_TIME: "there was no time left for {name}", WALL_CAPTCHA: "{name} asked for a captcha",
               WALL_CONSENT: "{name} showed a consent wall", WALL_BLOCK: "{name} refused the visit"}
WALL_NOTE = ("This page looks like a captcha, a consent wall or a block. Jarvis cannot and must not get past it: try "
             "another source.")

_WALL_WORDS = (
    (WALL_CAPTCHA, ("captcha", "i'm not a robot", "im not a robot", "are you a robot", "are you human",
                    "verify you are human", "verify you are a human", "verify that you are human",
                    "confirm you are human", "confirm you are not a robot", "unusual traffic", "automated queries",
                    "automated requests", "bots use duckduckgo too", "select all squares")),
    (WALL_CONSENT, ("before you continue", "accept all cookies", "accept all to continue", "consent to continue",
                    "review your cookie", "we value your privacy")),
    (WALL_BLOCK, ("access denied", "you have been blocked", "has been blocked", "too many requests",
                  "temporarily blocked", "request blocked", "enable javascript and cookies to continue",
                  "please enable javascript")),
)
_WALL_HOST_RE = re.compile(r"(?:^|\.)consent\.[a-z0-9.-]+$")
_WALL_PATH_RE = re.compile(r"^/(?:sorry|captcha|consent)(?:/|$)", re.IGNORECASE)
_WALL_SHORT = 2_500               # a page this short with wall words is the wall; a longer one may only mention them
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_LINE_END_SPACE = " \t\f\v\r"     # what a line's end loses (clean_text)


# --------------------------------------------------------------------------
# The search engines
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchEngine:
    """One engine's real results page and what marks its results (selectors checked on real pages on 2026-11-04)."""

    key: str
    name: str
    address: str
    results: str                  # each result's link
    title: str = ""               # its title inside the link ("": the link's own words)
    box: str = ""                 # the result's box, for its snippet
    snippet: str = ""

    def url(self, query: str) -> str:
        return self.address.format(q=quote_plus(query))


ENGINES: tuple[SearchEngine, ...] = (
    SearchEngine("duckduckgo", "DuckDuckGo", "https://duckduckgo.com/?q={q}&ia=web", "a[data-testid='result-title-a']",
                 box="article", snippet="[data-result='snippet']"),
    SearchEngine("bing", "Bing", "https://www.bing.com/search?q={q}", "li.b_algo h2 a",
                 box="li.b_algo", snippet=".b_caption p, .b_lineclamp2, .b_lineclamp3, .b_lineclamp4"),
    SearchEngine("google", "Google", "https://www.google.com/search?q={q}", "#search a:has(h3)", title="h3",
                 box="[data-hveid], .g", snippet=".VwiC3b, [data-sncf]"),
)


def engine_order(first: str) -> tuple[SearchEngine, ...]:
    """[agent] search_engine, then the others in their order."""
    return tuple(sorted(ENGINES, key=lambda engine: engine.key != first))


# --------------------------------------------------------------------------
# What Jarvis runs in the isolated world
# --------------------------------------------------------------------------

# Run in an isolated world, these see the page's DOM and none of its JavaScript. A page can still shadow a property of
# ``document`` with a named element (<img name="body">, DOM clobbering), so they ask the prototypes.
_JS_HELPERS = """
var DOC = Document.prototype, ELEMENT = Element.prototype, HTML = HTMLElement.prototype;
function got(proto, name, item) { return Object.getOwnPropertyDescriptor(proto, name).get.call(item); }
function within(root, selector) { return ELEMENT.querySelectorAll.call(root, selector); }
function all(selector) { return DOC.querySelectorAll.call(document, selector); }
function words(item) {
  try { return String(got(HTML, 'innerText', item) || ''); } catch (error) { return ''; }
}
function line(item) { return words(item).replace(/\\s+/g, ' ').trim(); }
function bodyText() {
  var body = got(DOC, 'body', document);
  return body ? words(body) : '';
}
function title() { return String(got(DOC, 'title', document) || ''); }
"""
# What the owner watching the window can't see though the page's text holds it, and nothing here may hurt a read: every
# call of it is in its own try. A look is kept per element, so a whole page costs each element one look.
_JS_UNSEEN = """
var NODE = Node.prototype, looks = new WeakMap(), lookStarted = Date.now();
function rgba(text) {
  var found = /rgba?\\(([^)]*)\\)/.exec(String(text || ''));
  if (!found) { return null; }
  var parts = found[1].split(/[,\\/\\s]+/);
  return parts.length < 3 ? null : [parseFloat(parts[0]), parseFloat(parts[1]), parseFloat(parts[2]),
                                    parts.length > 3 ? parseFloat(parts[3]) : 1];
}
function lookOf(item) {
  var known = looks.get(item);
  if (known) { return known; }
  var up = got(NODE, 'parentElement', item);
  var above = up ? lookOf(up) : {gone: false, ground: [255, 255, 255], image: false};
  var style = window.getComputedStyle(item), box = ELEMENT.getBoundingClientRect.call(item);
  var paint = rgba(style.backgroundColor);
  var tiny = style.overflow !== 'visible' && (box.width <= 2 || box.height <= 2);
  var cut = /inset\\((?:50|100)%/.test(style.clipPath)
    || /rect\\(0(?:px)?,? 0(?:px)?,? 0(?:px)?,? 0(?:px)?\\)/.test(style.clip);
  var look = {gone: above.gone || parseFloat(style.opacity) < 0.1 || tiny || cut,
              ground: paint && paint[3] > 0.5 ? paint : above.ground,
              image: above.image || style.backgroundImage !== 'none'};
  looks.set(item, look);
  return look;
}
function unseen(item) {
  var look = lookOf(item), style = window.getComputedStyle(item), box = ELEMENT.getBoundingClientRect.call(item);
  if (look.gone || parseFloat(style.fontSize) < 3) { return true; }
  if (box.right + window.scrollX <= 0 || box.bottom + window.scrollY <= 0
      || box.left + window.scrollX > Math.max(window.innerWidth, 5000)) { return true; }
  var ink = rgba(style.color);
  if (!ink) { return false; }
  if (ink[3] < 0.1) { return true; }
  return !look.image && Math.abs(ink[0] - look.ground[0]) < 9 && Math.abs(ink[1] - look.ground[1]) < 9
    && Math.abs(ink[2] - look.ground[2]) < 9;
}
function linkOnShow(item) {
  try { return Date.now() - lookStarted > {ms} || !unseen(item); } catch (error) { return true; }
}
function hiddenText(whole) {
  try {
    var body = got(DOC, 'body', document), hidden = 0, nodes = 0, node;
    if (!body || whole < 1) { return false; }
    var walker = DOC.createTreeWalker.call(document, body, 4), next = TreeWalker.prototype.nextNode;
    var started = Date.now();
    while (nodes < {nodes} && Date.now() - started < {ms} && (node = next.call(walker))) {
      var size = String(got(CharacterData.prototype, 'data', node)).trim().length;
      var holder = got(NODE, 'parentElement', node);
      if (!size || !holder) { continue; }
      nodes += 1;
      var style = window.getComputedStyle(holder);
      if (!ELEMENT.getClientRects.call(holder).length || style.visibility !== 'visible') { continue; }
      if (unseen(holder)) { hidden += size; }
    }
    return hidden > {chars} && hidden > whole * {share};
  } catch (error) { return false; }
}
""".replace("{ms}", str(HIDDEN_MS)).replace("{nodes}", str(HIDDEN_NODES)).replace("{chars}", str(HIDDEN_MIN_CHARS))
_JS_UNSEEN = _JS_UNSEEN.replace("{share}", str(HIDDEN_MIN_SHARE))


def read_script() -> str:
    """The page: its title, address, text and the links on show, as JSON text."""
    return ("(function () {" + _JS_HELPERS + _JS_UNSEEN + f"""
var text = bodyText(), links = [], seen = {{}}, anchors = got(DOC, 'links', document);
for (var i = 0; i < anchors.length && links.length < {LINKS_SEEN}; i++) {{
  var a = anchors[i], href = String(a.href || '');
  if (!href || seen[href]) {{ continue; }}
  if (!ELEMENT.getClientRects.call(a).length || !linkOnShow(a)) {{ continue; }}
  var label = line(a) || String(ELEMENT.getAttribute.call(a, 'aria-label') || '');
  if (!label) {{ continue; }}
  seen[href] = 1;
  links.push([label.slice(0, {LINK_TEXT_CAP * 2}), href.slice(0, {URL_CAP + 1})]);
}}
var began = Date.now(), hidden = hiddenText(text.length);
return JSON.stringify({{title: title().slice(0, {TITLE_CAP * 2}), url: String(location.href), length: text.length,
                       text: text.slice(0, {TEXT_KEEP}), links: links, hiddenText: hidden,
                       hiddenMs: Date.now() - began}});
}})()""")


def results_script(engine: SearchEngine) -> str:
    """A results page: each result's title, address and snippet, its title and the first of its text, as JSON text."""
    selectors = json.dumps({"results": engine.results, "title": engine.title, "box": engine.box,
                            "snippet": engine.snippet})
    return ("(function () {" + _JS_HELPERS + f"""
var marks = {selectors}, results = [], seen = {{}}, anchors = all(marks.results);
for (var i = 0; i < anchors.length && results.length < {MAX_RESULTS * 2}; i++) {{
  var a = anchors[i], href = String(a.href || '');
  if (!href || seen[href]) {{ continue; }}
  seen[href] = 1;
  var head = marks.title ? within(a, marks.title)[0] : null;
  var box = marks.box ? ELEMENT.closest.call(a, marks.box) : null;
  var note = box && marks.snippet ? within(box, marks.snippet)[0] : null;
  results.push([line(head || a).slice(0, {TITLE_CAP * 2}), href.slice(0, {URL_CAP + 1}),
                note ? line(note).slice(0, {SNIPPET_CAP * 2}) : '']);
}}
return JSON.stringify({{title: title().slice(0, {TITLE_CAP * 2}), url: String(location.href),
                       text: bodyText().slice(0, {_WALL_SHORT * 2}), results: results}});
}})()""")


def scroll_script(fraction: float) -> str:
    """The window scrolls ``fraction`` of the way down the page: where it stands now, as JSON text."""
    share = max(0.0, min(1.0, float(fraction)))
    return ("(function () {" + _JS_HELPERS + f"""
var box = got(DOC, 'scrollingElement', document) || got(DOC, 'documentElement', document);
window.scrollTo(0, Math.round((box.scrollHeight - window.innerHeight) * {share:.6f}));
return JSON.stringify({{top: window.scrollY, height: box.scrollHeight}});
}})()""")


# --------------------------------------------------------------------------
# What came back
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PageRead:
    """A page as read: never in a repr."""

    title: str = field(repr=False)
    url: str = field(repr=False)
    text: str = field(repr=False)
    length: int = 0               # the page's whole text in characters (``text`` stops at TEXT_KEEP)
    links: tuple[tuple[str, str], ...] = field(default=(), repr=False)   # (its visible words, its address)
    hidden: bool = False          # invisible tag characters spelled something (they are gone from ``text``), or much of
                                  # the page's text is where the owner can't see it (read_script)


@dataclass(frozen=True)
class ResultsRead:
    title: str = field(repr=False)
    url: str = field(repr=False)
    text: str = field(repr=False)
    results: tuple[tuple[str, str, str], ...] = field(default=(), repr=False)   # (title, address, snippet)
    hidden: bool = False          # invisible tag characters spelled something (they are gone from the results)


def _text(value: Any, cap: int = 0) -> str:
    text = value if isinstance(value, str) else ""
    return text[:cap] if cap else text


def _json_object(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str):
        return {}
    try:
        found = json.loads(raw)
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def clean_text(text: str) -> str:
    """A page's text as it goes back: no invisible characters (tag characters, bidi controls and variation selectors can
    hide words from whoever reads it on screen), no runs of blank lines. Line by line, with no pattern over the spaces:
    on a page's long run of them a regex here takes minutes and holds the GIL, so nothing else runs, Stop neither."""
    text = (drop_invisible(text).replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n")
            .replace("\u2029", "\n\n"))
    text = "\n".join(line.rstrip(_LINE_END_SPACE) for line in text.split("\n"))
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def one_line(text: str, cap: int) -> str:
    """Other people's words as one row of a list: no way to split it, no invisible characters."""
    words = " ".join(drop_invisible(text).split())
    return words if len(words) <= cap else words[:cap - 3].rstrip() + "..."


def _same_page(first: str, second: str) -> bool:
    return first.partition("#")[0] == second.partition("#")[0]


def read_page(raw: Any) -> PageRead | None:
    """read_script's answer, checked: only links a public https address fits (web_link_problem), and none to the page
    itself. None when it is not an answer."""
    found = _json_object(raw)
    if "text" not in found:
        return None
    url = _text(found.get("url"), URL_CAP * 2)
    links: list[tuple[str, str]] = []
    for item in found.get("links") if isinstance(found.get("links"), list) else ():
        if not (isinstance(item, list) and len(item) == 2):
            continue
        words, address = one_line(_text(item[0]), LINK_TEXT_CAP), _text(item[1])
        if words and not web_link_problem(address) and not _same_page(address, url):
            links.append((words, address))
    size = found.get("length")
    raw_text, raw_title = _text(found.get("text"), TEXT_KEEP), _text(found.get("title"))
    text = clean_text(raw_text)
    return PageRead(one_line(raw_title, TITLE_CAP), url, text,
                    size if isinstance(size, int) and not isinstance(size, bool) else len(text), tuple(links),
                    guard.hidden_message(raw_text) or guard.hidden_message(raw_title)
                    or found.get("hiddenText") is True)


def read_results(raw: Any) -> ResultsRead | None:
    """results_script's answer, checked: only public https addresses. None when it is not an answer."""
    found = _json_object(raw)
    if "results" not in found:
        return None
    results: list[tuple[str, str, str]] = []
    hidden = False
    for item in found.get("results") if isinstance(found.get("results"), list) else ():
        if not (isinstance(item, list) and len(item) == 3):
            continue
        title, address = one_line(_text(item[0]), TITLE_CAP), _text(item[1])
        if title and not web_link_problem(address):
            results.append((title, address, one_line(_text(item[2]), SNIPPET_CAP)))
            hidden = hidden or guard.hidden_message(_text(item[0])) or guard.hidden_message(_text(item[2]))
    return ResultsRead(one_line(_text(found.get("title")), TITLE_CAP), _text(found.get("url"), URL_CAP * 2),
                       _text(found.get("text")), tuple(results[:MAX_RESULTS]), hidden)


def wall_kind(text: str, url: str = "") -> str:
    """"captcha", "consent", "block" when a page's words or address say it is a wall in front of what was asked for,
    else ""."""
    try:
        parts = urlsplit(url)
        if _WALL_HOST_RE.search((parts.hostname or "").casefold()):
            return WALL_CONSENT
        if _WALL_PATH_RE.match(parts.path or ""):
            return WALL_CAPTCHA
    except ValueError:
        pass
    words = " ".join((text or "").replace("\u2019", "'").casefold().split())
    for kind, phrases in _WALL_WORDS:
        if any(phrase in words for phrase in phrases):
            return kind
    return ""


def _results_are_a_wall(read: ResultsRead, query: str) -> str:
    """The wall a results page is, if any. A page with results is a wall only when its address says so, or when it is
    short and its wall words come from neither the results nor the search itself ("how does a captcha work")."""
    kind = wall_kind(read.text, read.url)
    if not kind or not read.results:
        return kind
    if wall_kind("", read.url):
        return kind
    theirs = " ".join(f"{title} {snippet}" for title, _address, snippet in read.results)
    if wall_kind(theirs) or wall_kind(query) or len(read.text) > _WALL_SHORT:
        return ""
    return kind


def suspect_lines(text: str) -> int:
    """How many lines of ``text`` hold a strong sign of an order to Jarvis or a model (guard.strong_order: override
    phrases, role and prompt markers, templates, hidden or look-alike tricks). Not an opening order verb, which ordinary
    pages are full of ("Find a store"). A long paragraph is read a sentence at a time: an article that merely mentions a
    word or two is no order."""
    return sum(1 for line in (text or "").splitlines() if line.strip() and any(
        guard.strong_order(part[:SUSPECT_LINE_CAP]) for part in _SENTENCE_RE.split(line)))


def owner_addresses(words: str) -> list[str]:
    """The web addresses in the owner's own words, as he wrote them; "www.x.org/y" also as the https address it
    means."""
    found: list[str] = []
    for start, end in body_links(words or ""):
        link = words[start:end]
        found.append(link)
        if not link.casefold().startswith(("http://", "https://")):
            found.append(f"https://{link}")
    return found


def workspace_name(path: str, token: str) -> str:
    """A workspace page's path ("/<token>/notes/a%20b.html") as the file's name in the task folder, "" when it is not
    this token's or its name is not a good one: "..", "%2e%2e", a drive, an absolute name, another task's."""
    parts = (path or "").split("/")
    if len(parts) < 3 or parts[0] or not token or parts[1] != token:
        return ""
    try:
        return "/".join(check_name("/".join(unquote(part, errors="strict") for part in parts[2:])))
    except (WorkspaceRefused, UnicodeError):
        return ""


def workspace_address(token: str, name: str) -> str:
    """``name`` (already a good name) as a workspace page's address."""
    return f"{SCHEME}:/{token}/" + "/".join(quote(part, safe="") for part in name.split("/"))


# --------------------------------------------------------------------------
# The run's addresses
# --------------------------------------------------------------------------

class AddressBook:
    """Every address seen in this run, as an exact string (the firewall's list), and the numbers given out. An address
    keeps its number for the whole run, so a result can be opened after reading another page. Thread-safe."""

    def __init__(self, cap: int = BOOK_CAP) -> None:
        self._lock = threading.Lock()
        self._cap = cap
        self._numbers: dict[str, int] = {}
        self._addresses: list[str] = []
        self._seen: set[str] = set()

    def __repr__(self) -> str:        # counts only
        return f"AddressBook({len(self._addresses)} numbered)"

    def number(self, address: str) -> int:
        """Its number (a new one when it has none: 1, 2, ...); 0 when the book is full."""
        with self._lock:
            self._seen.add(address)
            known = self._numbers.get(address)
            if known is not None:
                return known
            if len(self._addresses) >= self._cap:
                return 0
            self._addresses.append(address)
            self._numbers[address] = len(self._addresses)
            return len(self._addresses)

    def address(self, number: Any) -> str:
        """The address with that number, "" when none has it."""
        with self._lock:
            if isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= len(self._addresses):
                return self._addresses[number - 1]
            return ""

    def see(self, address: str) -> None:
        """Seen in this run, with no number: an address in the owner's words, the address a page ended on."""
        with self._lock:
            self._seen.add(address)

    def seen(self, address: Any) -> bool:
        with self._lock:
            return address in self._seen


# --------------------------------------------------------------------------
# One run's use of the window
# --------------------------------------------------------------------------

class WebRefused(Exception):
    """A web call that did not work: ``code`` a fixed word, the text Jarvis's own words for the model."""

    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


class Browser(Protocol):
    """What a session drives; each call blocks its own thread, ``timeout_s`` at most, and returns the moment
    ``cancelled()`` turns true. A load gives a snapshots.ShotResult; ``script`` the script's answer (None: no answer,
    NO_PAGE: no page is open); ``picture`` a ShotResult with the image (not ok: no page, or it did not fit)."""

    def load(self, url: str, *, timeout_s: float, cancelled: Callable[[], bool]) -> snapshots.ShotResult: ...

    def load_file(self, name: str, folder: TaskFolder, *, timeout_s: float,
                  cancelled: Callable[[], bool]) -> snapshots.ShotResult: ...

    def script(self, source: str, *, timeout_s: float, cancelled: Callable[[], bool]) -> Any: ...

    def picture(self, max_width: int, max_bytes: int, *, timeout_s: float,
                cancelled: Callable[[], bool]) -> snapshots.ShotResult: ...

    def scheme_ready(self) -> bool: ...

    def other_way(self, host: str, reading: bool) -> None: ...


NO_PAGE = "<no page>"             # script(): no page is open (not a string a script's JSON can be)


class Call:
    """One tool call's limits: its deadline and whether it was cancelled (Stop, or past the 45 s)."""

    def __init__(self, cancelled: Callable[[], bool], tool_s: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cancelled = cancelled
        self._clock = clock
        self._deadline = clock() + max(1.0, float(tool_s) - MARGIN_S)

    def left(self) -> float:
        return self._deadline - self._clock()

    def check(self) -> None:
        if self.cancelled():
            raise WebRefused(W_STOPPED, STOPPED_TEXT)
        if self.left() <= 0:
            raise WebRefused(W_TOO_SLOW, TOO_SLOW_TEXT)


@dataclass(frozen=True)
class Found:
    """A search that worked. ``results``: (number, title, address, snippet). ``tried``: how the pages before it fared,
    in words. ``shot``: the window's picture for Live, never for the model."""

    engine: str
    address: str = field(repr=False)
    results: tuple[tuple[int, str, str, str], ...] = field(repr=False)
    tried: str = ""
    shot: snapshots.ShotResult | None = field(default=None, repr=False)
    hidden: bool = False          # a result held a hidden message (guard.hidden_message)


@dataclass(frozen=True)
class Opened:
    """A page that opened, or the next part of it. ``first`` and ``last`` number the characters of ``text`` in the
    page's text; ``links``: (number, words, address), only the first time."""

    title: str = field(repr=False)
    address: str = field(repr=False)
    text: str = field(repr=False)
    first: int = 0
    last: int = 0
    total: int = 0
    links: tuple[tuple[int, str, str], ...] = field(default=(), repr=False)
    more: bool = False
    cut: bool = False             # the page's text is longer than Jarvis keeps
    still_loading: bool = False
    wall: bool = False
    shot: snapshots.ShotResult | None = field(default=None, repr=False)
    live_address: str = field(default="", repr=False)
    hidden: bool = False          # the page holds a hidden message (guard.hidden_message)


class _Page:
    """The page in the window: what was read of it and how far."""

    __slots__ = ("title", "address", "text", "offset", "total", "still_loading", "live_address", "hidden")

    def __init__(self, title: str, address: str, text: str, total: int, still_loading: bool, live_address: str,
                 hidden: bool = False) -> None:
        self.title, self.address, self.text, self.total = title, address, text, total
        self.offset = 0
        self.still_loading = still_loading
        self.live_address = live_address
        self.hidden = hidden

    def __repr__(self) -> str:        # never its address or words
        return f"_Page({len(self.text)} characters, at {self.offset})"


def _load_failure(result: snapshots.ShotResult | None, seconds: float) -> WebRefused:
    kind = snapshots.reason_kind(result.reason) if result is not None else "timeout"
    code, text = LOAD_WORDS.get(kind, ENGINE_FAILED_WORDS)
    status = result.http_status if result is not None else 0
    return WebRefused(code, text.format(seconds=round(seconds), status=status))


class WebSession:
    """See the module docs. ``browser`` is None on a PC whose window can't exist: every call says so."""

    def __init__(self, browser: Browser | None, engine: str = "duckduckgo") -> None:
        self.browser = browser
        self.book = AddressBook()
        self.engines = engine_order(engine)
        self._turn = threading.Lock()
        self._page: _Page | None = None
        self._results_shown = False

    def __repr__(self) -> str:
        return f"WebSession({self.book!r})"

    @contextmanager
    def _one_at_a_time(self, call: Call) -> Iterator[Browser]:
        """One page at a time. A call waits its turn, within its own time."""
        browser = self.browser
        if browser is None:
            raise WebRefused(W_NO_WINDOW, NO_WINDOW_TEXT)
        while not self._turn.acquire(timeout=0.02):
            if call.cancelled():
                raise WebRefused(W_STOPPED, STOPPED_TEXT)
            if call.left() <= MIN_TRY_S:
                raise WebRefused(W_BUSY, BUSY_TEXT)
        try:
            call.check()
            yield browser
        finally:
            self._turn.release()

    # ---- web_search --------------------------------------------------------------------------------

    def search(self, query: str, call: Call) -> Found:
        """The query on each search page in turn, in the window, until one gives results. A page that shows a captcha, a
        consent wall or a block is left as it is and the next one tried."""
        with self._one_at_a_time(call) as browser:
            tried: list[tuple[str, str]] = []
            for engine in self.engines:
                left = call.left()
                if left < MIN_TRY_S:
                    tried.append((engine.name, T_NO_TIME))
                    continue
                self._page = None
                self._results_shown = False
                loaded = browser.load(engine.url(query), timeout_s=min(LOAD_TIMEOUT_S, left), cancelled=call.cancelled)
                call.check()
                if not loaded.ok:
                    kind = snapshots.reason_kind(loaded.reason)
                    if kind not in ("timeout", "load_failed", "crashed", "site_refused"):
                        raise _load_failure(loaded, LOAD_TIMEOUT_S)
                    tried.append((engine.name, WALL_BLOCK if kind == "site_refused" else T_NOT_LOADED))
                    continue
                how, read = self._results(browser, engine, query, call)
                if not how and read is not None:
                    self._results_shown = True
                    return self._found(browser, engine, read, tried, loaded, call)
                tried.append((engine.name, how or T_NO_RESULTS))
            raise WebRefused(W_NO_RESULTS, NO_RESULTS_TEXT.format(tried=_tried_words(tried)))

    def _results(self, browser: Browser, engine: SearchEngine, query: str,
                 call: Call) -> tuple[str, ResultsRead | None]:
        """("", what it read), or why not: the wall it shows or T_NO_RESULTS. Asks again for a little while: a results
        page may draw its results by script after it has loaded."""
        started = time.monotonic()
        script = results_script(engine)
        while True:
            call.check()
            raw = browser.script(script, timeout_s=min(SCRIPT_TIMEOUT_S, max(0.1, call.left())),
                                 cancelled=call.cancelled)
            read = read_results(raw) if raw != NO_PAGE else None
            if read is not None:
                wall = _results_are_a_wall(read, query)
                if wall:
                    return wall, None
                if read.results:
                    return "", read
            elif raw is None or raw == NO_PAGE:
                return T_NOT_LOADED, None
            if time.monotonic() - started >= RESULTS_WAIT_S or call.left() <= POLL_S:
                return T_NO_RESULTS, None
            time.sleep(POLL_S)

    def _found(self, browser: Browser, engine: SearchEngine, read: ResultsRead, tried: list[tuple[str, str]],
               loaded: snapshots.ShotResult, call: Call) -> Found:
        rows = []
        for title, address, snippet in read.results:
            number = self.book.number(address)
            if number:
                rows.append((number, title, address, snippet))
        return Found(engine.name, loaded.final_url or read.url, tuple(rows), _tried_words(tried),
                     self._shot(browser, call), read.hidden)

    # ---- open_page, read_more ------------------------------------------------------------------------

    def open_page(self, address: str, call: Call) -> Opened:
        """A public https page in the window, read: its title, final address, the first part of its text, its links."""
        why = web_link_problem(address)
        if why:
            raise WebRefused(W_NOT_PUBLIC, NOT_PUBLIC_TEXT.format(why=why))
        with self._one_at_a_time(call) as browser:
            self._page = None
            self._results_shown = False
            seconds = min(LOAD_TIMEOUT_S, call.left())
            loaded = browser.load(address, timeout_s=seconds, cancelled=call.cancelled)
            call.check()
            if not loaded.ok:
                raise _load_failure(loaded, seconds)
            read = self._read(browser, call)
            final = loaded.final_url or read.url or address
            if not web_link_problem(final):
                self.book.see(final)
            page = _Page(read.title, final, read.text, read.length, loaded.still_loading, final, read.hidden)
            self._page = page
            links = []
            for words, link in read.links[:MAX_LINKS]:
                number = self.book.number(link)
                if number:
                    links.append((number, words, link))
            return self._part(page, tuple(links), self._shot(browser, call))

    def _read(self, browser: Browser, call: Call) -> PageRead:
        """The page as it is now; a page with nothing on it yet is given a moment."""
        started = time.monotonic()
        script = read_script()
        while True:
            call.check()
            raw = browser.script(script, timeout_s=min(SCRIPT_TIMEOUT_S, max(0.1, call.left())),
                                 cancelled=call.cancelled)
            call.check()
            if raw == NO_PAGE:
                raise WebRefused(W_NO_PAGE, NO_PAGE_TEXT)
            read = read_page(raw)
            if read is None:
                raise WebRefused(W_NO_ANSWER, NO_ANSWER_TEXT)
            if read.text or time.monotonic() - started >= TEXT_WAIT_S or call.left() <= POLL_S:
                return read
            time.sleep(POLL_S)

    def _part(self, page: _Page, links: tuple[tuple[int, str, str], ...], shot: snapshots.ShotResult | None) -> Opened:
        first = page.offset
        last = min(len(page.text), first + TEXT_PART)
        if last < len(page.text):              # stop at a line's end when one is near
            end = page.text.rfind("\n", first + TEXT_PART // 2, last)
            last = end + 1 if end >= 0 else last
        page.offset = last
        return Opened(page.title, page.address, page.text[first:last].strip("\n"), first + 1 if last > first else 0,
                      last, len(page.text), links, last < len(page.text), page.total > TEXT_KEEP, page.still_loading,
                      not first and len(page.text) < _WALL_SHORT and bool(wall_kind(page.text, page.address)), shot,
                      page.live_address, page.hidden)

    def more(self, call: Call) -> Opened:
        """The next part of this page's text; the window really scrolls there. A page that grows as it scrolls is read
        again, so what it added comes too."""
        with self._one_at_a_time(call) as browser:
            page = self._page
            if page is None:
                raise WebRefused(W_NO_PAGE, RESULTS_PAGE_TEXT if self._results_shown else NO_PAGE_TEXT)
            done = page.offset / max(1, len(page.text))
            scrolled = browser.script(scroll_script(done), timeout_s=min(SCRIPT_TIMEOUT_S, max(0.1, call.left())),
                                      cancelled=call.cancelled)
            call.check()
            if scrolled == NO_PAGE:
                self._page = None
                raise WebRefused(W_NO_PAGE, NO_PAGE_TEXT)
            if scrolled is None:
                self._page = None
                raise WebRefused(W_NO_ANSWER, NO_ANSWER_TEXT)
            raw = browser.script(read_script(), timeout_s=min(SCRIPT_TIMEOUT_S, max(0.1, call.left())),
                                 cancelled=call.cancelled)
            call.check()
            read = read_page(raw) if raw != NO_PAGE else None
            if read is not None and len(read.text) > len(page.text):
                page.text, page.total = read.text, read.length
            return self._part(page, (), self._shot(browser, call))

    def other_way(self, host: str, reading: bool) -> None:
        """fetch_text starts or ends: the window's strip says this page is read another way. Never waits, never
        raises."""
        tell = getattr(self.browser, "other_way", None)
        if callable(tell):
            try:
                tell(host, reading)
            except Exception:  # noqa: BLE001 - only what the strip says
                pass

    # ---- look_at_page, show_file -----------------------------------------------------------------------

    def look(self, call: Call) -> snapshots.ShotResult:
        """A picture of what the window shows, within the caps."""
        with self._one_at_a_time(call) as browser:
            taken = browser.picture(PICTURE_WIDTH, PICTURE_BYTES,
                                    timeout_s=min(SCRIPT_TIMEOUT_S, max(0.1, call.left())), cancelled=call.cancelled)
            call.check()
            if taken.ok:
                return taken
            if snapshots.reason_kind(taken.reason) == "too_large":
                raise WebRefused(W_TOO_BIG, "That picture would not fit. Read the page's text instead.")
            if snapshots.reason_kind(taken.reason) == "blank":
                raise WebRefused(W_HIDDEN, HIDDEN_TEXT)
            raise WebRefused(W_NO_PAGE, NO_PAGE_TEXT)

    def show(self, folder: TaskFolder, name: str, call: Call) -> Opened:
        """A file of this task's folder in the window, on Jarvis's own scheme; ``name`` is a good name of a file that
        is there. Read as a page is; its address never goes out of this PC."""
        with self._one_at_a_time(call) as browser:
            if not browser.scheme_ready():
                raise WebRefused(W_NO_WINDOW, NO_WINDOW_TEXT)
            self._page = None
            self._results_shown = False
            seconds = min(LOAD_TIMEOUT_S, call.left())
            loaded = browser.load_file(name, folder, timeout_s=seconds, cancelled=call.cancelled)
            call.check()
            if not loaded.ok:
                raise _load_failure(loaded, seconds)
            read = self._read(browser, call)
            page = _Page(read.title or name, name, read.text, read.length, loaded.still_loading, name, read.hidden)
            self._page = page
            return self._part(page, (), self._shot(browser, call))

    def _shot(self, browser: Browser, call: Call) -> snapshots.ShotResult | None:
        """The window's picture for the step in Live. A page without one still counts."""
        if call.cancelled() or call.left() <= 0:
            return None
        taken = browser.picture(LIVE_WIDTH, LIVE_BYTES, timeout_s=min(SCRIPT_TIMEOUT_S, max(0.1, call.left())),
                                cancelled=call.cancelled)
        return taken if taken.ok else None


def _tried_words(tried: list[tuple[str, str]]) -> str:
    return ", ".join(TRIED_WORDS[how].format(name=name) for name, how in tried)


__all__ = ["ENGINES", "NO_PAGE", "SCHEME", "AddressBook", "Browser", "Call", "Found", "Opened", "PageRead",
           "ResultsRead", "SearchEngine", "WebRefused", "WebSession", "engine_order", "owner_addresses", "read_page",
           "read_results", "read_script", "results_script", "scroll_script", "suspect_lines", "wall_kind",
           "workspace_address", "workspace_name"]
