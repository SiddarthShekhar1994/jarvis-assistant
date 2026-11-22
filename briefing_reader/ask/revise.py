"""A revise run's answer (mail extras, part 2): the owner's plain words rewrite one message in the Edit dialog.

    read_revise_result(event, init)     the run's result event -> ReviseAnswer or AskFailure (read strictly: exactly
                                        say / body / question within the schema's caps; a plain-text JSON answer only
                                        when the run had no StructuredOutput tool; anything else GARBLED)
    check_revision(body, draft, words)  "" when Jarvis offers the rewritten text, else a reason word: empty, too long,
                                        an email address or a link that is neither in the draft nor in the owner's
                                        words, a control or invisible character

The run sees only the draft (its subject and text) and the owner's words: no thread, notes, search or web. Its text is
offered in the dialog, never kept before Save and never sent before Send. Nothing here is logged; the reprs hold sizes.
"""

from __future__ import annotations

import json
import re
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..format_chars import invisible_char
from .stream import GARBLED, AskFailure, InitSummary, failure, result_failure

MAX_BODY = 5000
MAX_SAY = 200
MAX_QUESTION = 200
MAX_WORDS = 300

REVISE_EMPTY = "empty"
REVISE_TOO_LONG = "too_long"
REVISE_NEW_ADDRESS = "new_address"
REVISE_NEW_LINK = "new_link"
REVISE_CONTROL = "control"
REVISE_REASONS = (REVISE_EMPTY, REVISE_TOO_LONG, REVISE_NEW_ADDRESS, REVISE_NEW_LINK, REVISE_CONTROL)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)
# A link as a mail app would make one: a scheme, a bare www. host, or a bare host name with its top-level domain
# ("acme-login.example/reset"); matched on the NFKC form, so full-width look-alikes count too.
_LINK_RE = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]{1,15}://|www\.)[^\s<>\"']+"
                      r"|(?<![\w@.-])(?:[\w-]+\.)+[^\W\d_]{2,24}(?![\w@-])(?:/[^\s<>\"']*)?", re.IGNORECASE)
# An address in any script (the context's own pattern is ASCII only): "ana@ex\u00e4mple.com" counts.
_ADDRESS_RE = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]{1,64}@[\w-]+(?:\.[\w-]+)+")
# What the run saw of the draft and the words: context.data() shows < > | as these, so a rewrite that hands them back
# is not new.
_SHOWN = str.maketrans({"<": "\u2039", ">": "\u203a", "|": "/"})
_KEYS = {"say", "body", "question"}


@dataclass(frozen=True)
class ReviseAnswer:
    say: str = field(repr=False)
    body: str = field(repr=False)
    question: str = field(repr=False)
    via_fallback: bool = False

    def __repr__(self) -> str:
        return f"ReviseAnswer({len(self.body)} character(s){', question' if self.question else ''})"


def _string(value: Any, cap: int) -> bool:
    return isinstance(value, str) and len(value) <= cap


def answer_from(value: Any, *, fallback: bool = False) -> ReviseAnswer | None:
    """The run's JSON -> ReviseAnswer, or None when anything does not fit the schema."""
    if not isinstance(value, dict) or set(value) != _KEYS:
        return None
    say, body, question = value["say"], value["body"], value["question"]
    if not _string(say, MAX_SAY) or not _string(body, MAX_BODY) or not _string(question, MAX_QUESTION):
        return None
    body = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return ReviseAnswer(" ".join(say.split()), body, " ".join(question.split()), fallback)


def read_revise_result(event: Mapping[str, Any], init: InitSummary | None) -> ReviseAnswer | AskFailure:
    """The result event of a revise run -> ReviseAnswer, or AskFailure (stream.result_failure first)."""
    found = result_failure(event, init)
    if found is not None:
        return found
    assert init is not None
    value = event.get("structured_output")
    if value is not None:
        answer = answer_from(value)
        return answer if answer is not None else failure(GARBLED, "shape")
    if init.structured_tool:
        return failure(GARBLED, "no_structured_output")
    text = event.get("result") if isinstance(event.get("result"), str) else ""
    fenced = _FENCE_RE.match(text or "")
    try:
        parsed = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        return failure(GARBLED, "fallback")
    answer = answer_from(parsed, fallback=True)
    return answer if answer is not None else failure(GARBLED, "fallback")


def _addresses(text: str) -> set[str]:
    return {match.group(0).casefold().rstrip(".") for match in _ADDRESS_RE.finditer(text or "")}


def _links(text: str) -> set[str]:
    return {match.group(0).casefold().rstrip(".,;:!?)]}") for match in _LINK_RE.finditer(text or "")}


def _bad_character(text: str) -> bool:
    for char in text:
        if char in "\n\t":
            continue
        if invisible_char(char) or unicodedata.category(char) in ("Cc", "Cs", "Co", "Cn", "Zl", "Zp"):
            return True
    return False


def check_revision(body: str, draft: str, words: str) -> str:
    """"" when the rewritten ``body`` may be offered, else why not (REVISE_*)."""
    if not isinstance(body, str) or not body.strip():
        return REVISE_EMPTY
    if len(body) > MAX_BODY:
        return REVISE_TOO_LONG
    if _bad_character(body):
        return REVISE_CONTROL
    seen = f"{draft or ''}\n{words or ''}"
    # The draft and words as given and as the run saw them (data(): < > | shown as other characters), all NFKC, so a
    # full-width at sign or full stop is the address or link it looks like, and a link the run handed back as shown is known.
    known = unicodedata.normalize("NFKC", f"{seen}\n{seen.translate(_SHOWN)}")
    text = unicodedata.normalize("NFKC", body)
    if _addresses(text) - _addresses(known):
        return REVISE_NEW_ADDRESS
    known_links = _links(known)
    known_hosts = {_host_of(link) for link in known_links}
    for link in _links(text) - known_links:
        if "/" in link or _host_of(link) not in known_hosts:   # a bare host of a link the draft has is that site
            return REVISE_NEW_LINK
    return ""


def _host_of(link: str) -> str:
    """The host of a link as _LINK_RE found it ("docs.example.org" for "https://docs.example.org/a")."""
    rest = link.split("://", 1)[1] if "://" in link else link
    return rest.split("/", 1)[0].split("?", 1)[0].removeprefix("www.")
