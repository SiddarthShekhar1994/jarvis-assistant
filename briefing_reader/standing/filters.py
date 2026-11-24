"""A standing job's Gmail filter (no Qt, no network): checked like Ask's searches, and narrower still.

    check_filter(query, words, *, needs_sender=False) -> str
        ``query`` tidied, when a job may watch mail with it (FilterRefused: why not, safe to show and log):
        - ask.mail.check_query, grounded in the owner's own ``words`` (the sentence they typed): every word of 3+
          letters the filter uses and every address it names must be theirs; security and payment senders,
          sensitive words (codes, passwords, banking, statements), labels, spam and trash are refused; it must narrow;
        - and the rules of a filter that runs on its own, unwatched: one plain set of terms. No OR (beside a
          negation, or last before Jarvis's own time part, it would match every mail), no brackets or braces, no
          lone AND or "-", no time operator (Jarvis adds the time itself), no rfc822msgid;
        - ``needs_sender``: it names a sender with a positive from: (a rule that tidies mail needs one).
    watch_query(query, after_epoch) -> str     "(<query>) after:<epoch>": Jarvis's own time part, always outside
    senders(query) -> list[str]                the positive from: values (what a setup card shows)

Nothing here is logged: a filter holds the owner's words.
"""

from __future__ import annotations

import re

from ..ask.mail import QueryRefused, SearchGrounding, check_query, search_words
from ..actions import email_address

_TOKEN_RE = re.compile(r'"[^"]*"|[(){}]|[^\s(){}"]+')
_TIME_OPERATORS = frozenset({"after", "before", "older_than", "newer_than", "older", "newer"})
_ADDRESS_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


class FilterRefused(ValueError):
    """Why a filter can't watch mail (names no word of it)."""


def grounding_of(words: str) -> SearchGrounding:
    """What a filter may name: the words of the owner's own sentence and the addresses typed in it."""
    text = str(words or "")
    addresses = frozenset(found.casefold() for found in (email_address(item) for item in _ADDRESS_RE.findall(text))
                          if found)
    return SearchGrounding(words=frozenset(word for word in search_words(text) if len(word) >= 2),
                           addresses=addresses)


def check_filter(query: object, words: str, *, needs_sender: bool = False) -> str:
    if not isinstance(query, str):
        raise FilterRefused("The filter is not text")
    tokens = _TOKEN_RE.findall(" ".join(query.split()))
    for token in tokens:
        if token in ("(", ")", "{", "}"):
            raise FilterRefused("A standing filter is one plain set of terms: no brackets or braces")
        if token.upper() in ("OR", "AND") or token == "-":
            raise FilterRefused("A standing filter can't use OR, AND or a lone \"-\": make one rule for each sender")
        name, colon, _value = token.lstrip("-").partition(":")
        if colon and name.casefold() in _TIME_OPERATORS:
            raise FilterRefused("A standing filter names no time: Jarvis looks at new mail himself")
        if colon and name.casefold() == "rfc822msgid":
            raise FilterRefused("A standing filter can't name one email")
    try:
        text = check_query(query, grounding_of(words))
    except QueryRefused as exc:
        raise FilterRefused(str(exc)) from None
    if needs_sender and not senders(text):
        raise FilterRefused("A rule that stars, labels or archives mail must name the sender (from:)")
    return text


def senders(query: str) -> list[str]:
    found: list[str] = []
    for token in _TOKEN_RE.findall(" ".join(str(query or "").split())):
        if token.startswith("-"):
            continue
        name, colon, value = token.partition(":")
        if colon and name.casefold() == "from" and value and value.casefold() not in found:
            found.append(value.casefold())
    return found


def watch_query(query: str, after_epoch: int) -> str:
    """The query a check runs: the checked filter in brackets, then Jarvis's own ``after:`` (whole seconds)."""
    return f"({query}) after:{max(0, int(after_epoch))}"
