"""Which frame a sentence is about (the new look, part 5c; DESIGN-SPEC 1 "the lock-on", 3.4 "Lit", 8 "the cue line").
Qt-free and pure: no file, no log, no clock; nothing here raises for odd data.

While Jarvis speaks, the sentence being said lights the frame it is about. A sentence is about a frame when it names
that frame's own words - its title, a sender, a subject, an event's title, a to-do, a host - matched as whole words,
case-insensitive, and at least one of the words it names is distinctive: 4 letters or more, not a common word, and no
other frame on the board has it. When no frame is named that way, or two are named equally, nothing is lit for that
sentence: it never guesses.

    sentence_spans(text) -> list[(start, end)]     the sentences of ``text`` (ends after . ! ? or a line break; not
                                                   after "Dr." or "a.m.", not inside "2.9k" or "8:12")
    spoken_offset(text, words, duration_ms, position_ms) -> int
                                                   how far into ``text`` the voice is (a character offset), from its
                                                   timeline: the word being said (``words``: (start_ms, end_ms, word)
                                                   as voice_fx.VoiceTimeline has them, found in the text in order), else
                                                   the share of its duration; -1 when that can't be told
    sentence_at(spans, offset) -> int              the index of the sentence holding ``offset`` (-1 none)
    tokens(text) -> list[(word, start, end)]       the words of ``text`` (letters and digits; casefolded), with their
                                                   places in it
    frame_words(frame) -> frozenset[str]           a frame message's own words (see above)
    distinctive(frames) -> dict[id, frozenset[str]]   each frame's words no other frame shares (4+ letters, not common)
    match(sentence, frames) -> str                 the id of the frame the sentence is about ("" none)
    locate(sentence, text, after=0) -> int         where ``sentence`` ends in ``text`` (the conversation's current
                                                   turn: the cue line starts there), past its own end punctuation; -1
                                                   when it is not there
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MIN_LETTERS = 4                   # a distinctive word has at least this many letters
TAIL_WORDS = 5                    # locate(): a sentence not there whole is looked for by its last words, this many
TAIL_MIN = 3                      # ... and never by fewer than this

_WORD = re.compile(r"[^\W_]+")
# A sentence ends after . ! ? or … (and the quotes or brackets that close it) before a space or the end, or at a line
# break; "Dr." and "a.m." are not ends.
_END = re.compile(r"[.!?…]+[\"'’”)\]]*(?=\s|$)|\n+")
_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "st", "sr", "jr", "prof", "vs", "etc", "e.g", "i.e", "a.m", "p.m",
                            "no", "approx", "dept", "inc", "ltd", "co"})
# Common words a title can hold that never tell one frame from another (4 letters or more; shorter ones never count).
COMMON = frozenset("""
about above after again against also among another anything around away back been before being below best between
both came come comes could does doing done down during each either else even ever every first from further gets give
given goes going gone good great have having here hers herself himself into itself just keep know last less like
little long look made make many more most much must myself need needs never next none nothing once only other ours
over part same says shall should show shows since some still such sure take than that their theirs them then there
these they thing things this those though through till time told under until upon very want wants well were what
when where which while will with within without would your yours yourself sir jarvis okay please thanks thank right
here's there's that's what's it's let's i'll i've you'll you're we'll they're
""".split())


def tokens(text: Any) -> list[tuple[str, int, int]]:
    """The words of ``text``: (casefolded word, start, end) - letters and digits only ("Dana's" is "dana" and "s",
    "AI-Trader" is "ai" and "trader")."""
    source = str(text or "")
    return [(match.group(0).casefold(), match.start(), match.end()) for match in _WORD.finditer(source)]


def _words(text: Any) -> list[str]:
    return [word for word, _start, _end in tokens(text)]


def _letters(word: str) -> int:
    return sum(1 for char in word if char.isalpha())


def sentence_spans(text: Any) -> list[tuple[int, int]]:
    """See the module doc: (start, end) of each sentence, its own end punctuation in it, leading spaces out."""
    source = str(text or "")
    spans: list[tuple[int, int]] = []
    start = 0
    for found in _END.finditer(source):
        end = found.end()
        if found.group(0)[:1] == ".":
            before = source[start:found.start()].split()
            last = before[-1].casefold().rstrip(".") if before else ""
            if last in _ABBREVIATIONS:
                continue
        piece = source[start:end]
        lead = len(piece) - len(piece.lstrip())
        if piece.strip():
            spans.append((start + lead, start + len(piece.rstrip())))
        start = end
    rest = source[start:]
    if rest.strip():
        lead = len(rest) - len(rest.lstrip())
        spans.append((start + lead, start + len(rest.rstrip())))
    return spans


def sentence_at(spans: Sequence[tuple[int, int]], offset: int) -> int:
    """The index of the sentence that holds ``offset`` (a place between two sentences counts as the next one); -1
    when ``offset`` is negative or there is none."""
    if offset < 0 or not spans:
        return -1
    for index, (_start, end) in enumerate(spans):
        if offset < end:
            return index
    return len(spans) - 1


def spoken_offset(text: Any, words: Iterable[tuple[int, int, str]] = (), duration_ms: int = 0,
                  position_ms: int = -1) -> int:
    """See the module doc."""
    source = str(text or "").casefold()
    try:
        position = int(position_ms)
    except (TypeError, ValueError):
        return -1
    if not source or position < 0:
        return -1
    cursor = 0
    found = -1
    known = False
    for item in words or ():
        try:
            start, _end, word = int(item[0]), int(item[1]), str(item[2] or "")
        except (TypeError, ValueError, IndexError):
            continue
        needle = word.strip().casefold()
        if not needle:
            continue
        known = True
        at = source.find(needle, cursor)
        if at < 0:
            continue                               # a word the voice says otherwise ("eight twelve"): the next one
        if start > position:
            break
        found = at
        cursor = at + len(needle)
    if known:
        return found if found >= 0 else 0
    try:
        duration = int(duration_ms)
    except (TypeError, ValueError):
        return -1
    if duration <= 0:
        return -1
    return min(len(source) - 1, round(len(source) * min(1.0, position / duration)))


def frame_words(frame: Any) -> frozenset[str]:
    """A frame message's own words: its title, a mail's sender, a mail list's senders and subjects, a day's event
    titles (not its free gaps), to-dos' words, a page picture's host and a text frame's hosts (its rows' metas)."""
    if not isinstance(frame, Mapping):
        return frozenset()
    found: list[str] = _words(frame.get("title", ""))
    body = frame.get("body") if isinstance(frame.get("body"), Mapping) else {}
    rows = [row for row in (body.get("rows") or []) if isinstance(row, Mapping)] if isinstance(body.get("rows"),
                                                                                                 list) else []
    kind = frame.get("kind", "")
    if kind == "mail_thread":
        sender = str(body.get("sender", "") or "")
        found += _words(sender[3:] if sender.startswith("To ") else sender)
    elif kind == "mail_list":
        for row in rows:
            found += _words(row.get("sender", "")) + _words(row.get("subject", ""))
    elif kind == "calendar":
        for row in rows:
            if row.get("state") != "free":
                found += _words(row.get("title", ""))
    elif kind == "todos":
        for row in rows:
            found += _words(row.get("text", ""))
    elif kind == "picture":
        found += _words(body.get("caption", ""))
    elif kind == "text":
        for row in rows:
            found += _words(row.get("meta", ""))
    return frozenset(found)


def _telling(word: str) -> bool:
    return _letters(word) >= MIN_LETTERS and word not in COMMON


def distinctive(frames: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """Each frame's own words that tell it from every other frame given: 4+ letters, not common, and no other frame
    has them."""
    words = {frame_id: frame_words(frame) for frame_id, frame in frames.items()}
    seen: dict[str, int] = {}
    for own in words.values():
        for word in own:
            seen[word] = seen.get(word, 0) + 1
    return {frame_id: frozenset(word for word in own if _telling(word) and seen.get(word, 0) == 1)
            for frame_id, own in words.items()}


def match(sentence: Any, frames: Mapping[str, Any]) -> str:
    """See the module doc: the one frame the sentence names by most distinctive words; "" for none or a tie."""
    said = set(_words(sentence))
    if not said or not frames:
        return ""
    scores = {frame_id: len(said & own) for frame_id, own in distinctive(frames).items()}
    best = max(scores.values(), default=0)
    if best <= 0:
        return ""
    winners = [frame_id for frame_id, score in scores.items() if score == best]
    return winners[0] if len(winners) == 1 else ""


def locate(sentence: Any, text: Any, after: int = 0) -> int:
    """See the module doc: the character offset just past the sentence's last word in ``text`` (and the end
    punctuation right after it), the whole sentence first, else its last TAIL_WORDS words when they are there once;
    the first place at or after ``after``. -1 when it is not there."""
    said = _words(sentence)
    source = str(text or "")
    if not said or not source:
        return -1
    found = tokens(source)
    words = [word for word, _start, _end in found]
    begin = next((index for index, (_word, start, _end) in enumerate(found) if start >= max(0, int(after or 0))),
                 len(found))

    def places(needle: list[str]) -> list[int]:
        size = len(needle)
        return [index for index in range(begin, len(words) - size + 1) if words[index:index + size] == needle]

    hits = places(said)
    if hits:
        last = hits[0] + len(said) - 1
    else:
        tail = said[-TAIL_WORDS:]
        if len(tail) < TAIL_MIN:
            return -1
        hits = places(tail)
        if len(hits) != 1:
            return -1
        last = hits[0] + len(tail) - 1
    end = found[last][2]
    while end < len(source) and source[end] in ".!?…,;:\"'’”)]":
        end += 1
    return end


__all__ = ["COMMON", "MIN_LETTERS", "distinctive", "frame_words", "locate", "match", "sentence_at", "sentence_spans",
           "spoken_offset", "tokens"]
