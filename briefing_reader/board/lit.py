"""Which frame a sentence is about (the new look, part 5c; DESIGN-SPEC 1 "the lock-on", 3.4 "Lit", 8 "the cue line").
Qt-free and pure: no file, no log, no clock; nothing here raises for odd data.

While Jarvis speaks, the sentence being said lights the frame it is about. A sentence is about a frame when it names
that frame's own words - its title, a sender, a subject, an event's title, a to-do, a host - matched as whole words,
case-insensitive, and at least one of the words it names is distinctive: 4 letters or more, not a common word, and no
other frame on the board has it. When no frame is named that way, or two are named equally, nothing is lit for that
sentence: it never guesses.

    sentence_spans(text) -> list[(start, end)]     the sentences of ``text`` (ends after . ! ? or a line break; never
                                                   after a title - "Dr.", "Mr." -, "e.g." or "i.e.", after "No."
                                                   before a number, after another short form - "a.m.", "etc.", "Inc." -
                                                   unless an upper-case letter follows (never a dash, "&", a number or
                                                   a word in lower case), nor after "a.m." / "p.m." before a weekday or
                                                   a month; not inside "2.9k" or "8:12")
    spoken_offset(text, words, duration_ms, position_ms) -> int
                                                   how far into ``text`` the voice is (a character offset), from its
                                                   timeline: the word being said (``words``: (start_ms, end_ms, word)
                                                   as voice_fx.VoiceTimeline has them, found in the text in order as
                                                   whole words - a word said otherwise is passed by, never found inside
                                                   another word), else the share of its duration; -1 when that can't be
                                                   told
    sentence_at(spans, offset) -> int              the index of the sentence holding ``offset`` (-1 none)
    tokens(text) -> list[(word, start, end)]       the words of ``text`` (letters and digits; casefolded), with their
                                                   places in it
    frame_words(frame) -> frozenset[str]           a frame message's own words (see above)
    distinctive(frames) -> dict[id, frozenset[str]]   each frame's words no other frame shares (4+ letters, not common)
    match(sentence, frames) -> str                 the id of the frame the sentence is about ("" none)
    locate(sentence, text, after=0) -> int         where ``sentence`` ends in ``text`` (the conversation's current
                                                   turn: the cue line starts there), past its own end punctuation; -1
                                                   when it is not there
    locate_start(sentence, text, after=0) -> int   where ``sentence`` begins in ``text`` (its first word), when it is
                                                   there whole; -1 when it is not (the turn's words before the sentence
                                                   being said read dimmer while he says it)
    sentence_places(utterance, text) -> list[(start, end)]
                                                   where each sentence of ``utterance`` (sentence_spans) is in ``text``,
                                                   in order: its first word and just past its end (as locate_start and
                                                   locate say), (-1, -1) for one not there. When ``text`` holds the
                                                   utterance word for word, each sentence's own place in it; else each
                                                   is looked for after the one before it, so a sentence that repeats
                                                   earlier words ("That's all.") is never found back where they were
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
# break; "Dr." and "e.g." are never an end, "No." is not one before a number ("No. 5"), and the other short forms end
# one only before a capital ("Dana's reply says no. The study group ...", "... from Acme Inc. The study group ...") -
# never before a word in lower case, a number, a dash or "&" ("at 8 a.m. today", "Acme Inc. - the client"), and "a.m."
# / "p.m." not before a weekday or a month ("at 9 a.m. Tuesday").
_END = re.compile(r"[.!?…]+[\"'’”)\]]*(?=\s|$)|\n+")
_TITLES = frozenset({"mr", "mrs", "ms", "dr", "sr", "jr", "prof"})
_NEVER_ENDS = _TITLES | {"e.g", "i.e"}
_BEFORE_NUMBER = frozenset({"no"})
_ABBREVIATIONS = frozenset({"st", "vs", "etc", "a.m", "p.m", "approx", "dept", "inc", "ltd", "co"})
_CLOCK = frozenset({"a.m", "p.m"})
_DAYS_AND_MONTHS = frozenset("""
monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs fri sat sun
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
""".split())
_OPENING = "\"'‘’“”([{"
_PAST_END = ".!?…,;:\"'’”)]"          # what locate passes after a sentence's last word: its end punctuation
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


def _next_char(source: str, at: int) -> str:
    """The first character of the word after ``at`` (spaces and opening quotes or brackets passed by); "" at the end."""
    for char in source[at:]:
        if not char.isspace() and char not in _OPENING:
            return char
    return ""


def _next_word(source: str, at: int) -> str:
    """The word after ``at``, casefolded ("" at the end); asked only once a letter is known to come next."""
    found = _WORD.search(source, at)
    return found.group(0).casefold() if found else ""


def _no_end(word: str, source: str, at: int) -> bool:
    """A full stop after ``word`` does not end its sentence (see _END's note); ``at``: just past the stop."""
    if word in _NEVER_ENDS:
        return True
    following = _next_char(source, at)
    if word in _BEFORE_NUMBER:
        return following.isdigit()
    if word in _ABBREVIATIONS:
        if not following.isupper():
            return True                        # only a capital starts the next sentence after a short form
        return word in _CLOCK and _next_word(source, at) in _DAYS_AND_MONTHS
    return False


def sentence_spans(text: Any) -> list[tuple[int, int]]:
    """See the module doc: (start, end) of each sentence, its own end punctuation in it, leading spaces out."""
    source = str(text or "")
    spans: list[tuple[int, int]] = []
    start = 0
    for found in _END.finditer(source):
        end = found.end()
        if found.group(0)[:1] == ".":
            before = source[start:found.start()].split()
            last = before[-1].casefold().lstrip(_OPENING).rstrip(".") if before else ""
            if _no_end(last, source, end):
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


def _find(said: list[str], needle: list[str], cursor: int) -> int:
    """The index of the first run of ``said`` from ``cursor`` that is ``needle`` word for word; -1 when there is none."""
    size = len(needle)
    for index in range(max(0, cursor), len(said) - size + 1):
        if said[index:index + size] == needle:
            return index
    return -1


def spoken_offset(text: Any, words: Iterable[tuple[int, int, str]] = (), duration_ms: int = 0,
                  position_ms: int = -1) -> int:
    """See the module doc."""
    source = str(text or "")
    try:
        position = int(position_ms)
    except (TypeError, ValueError):
        return -1
    if not source or position < 0:
        return -1
    places = tokens(source)
    said = [word for word, _start, _end in places]
    cursor = 0
    found = -1
    known = False
    for item in words or ():
        try:
            start, _end, word = int(item[0]), int(item[1]), str(item[2] or "")
        except (TypeError, ValueError, IndexError):
            continue
        needle = _words(word)                      # the voice's word as the text's words ("8:12": "8", "12")
        if not needle:
            continue
        known = True
        at = _find(said, needle, cursor)           # whole words: "eight" is never found inside "eighteen"
        if at < 0:
            continue                               # a word the voice says otherwise ("eight twelve"): passed by, the
        if start > position:                       # place kept
            break
        found = places[at][1]
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
    return _past_end(source, found[last][2])


def _past_end(source: str, end: int) -> int:
    """Just past a sentence's last word at ``end`` and the end punctuation right after it."""
    while end < len(source) and source[end] in _PAST_END:
        end += 1
    return end


def locate_start(sentence: Any, text: Any, after: int = 0) -> int:
    """See the module doc: the character offset of the sentence's first word in ``text`` when all its words are there in
    order, the first place at or after ``after``; -1 when they are not."""
    said = _words(sentence)
    source = str(text or "")
    if not said or not source:
        return -1
    found = tokens(source)
    words = [word for word, _start, _end in found]
    begin = next((index for index, (_word, start, _end) in enumerate(found) if start >= max(0, int(after or 0))),
                 len(found))
    at = _find(words, said, begin)
    return found[at][1] if at >= 0 else -1


def sentence_places(utterance: Any, text: Any) -> list[tuple[int, int]]:
    """See the module doc: one (start, end) per sentence of ``utterance``, in order."""
    said = str(utterance or "")
    source = str(text or "")
    spans = sentence_spans(said)
    if not spans or not source:
        return [(-1, -1)] * len(spans)
    mine = tokens(said)
    theirs = tokens(source)
    at = _find([word for word, _start, _end in theirs], [word for word, _start, _end in mine], 0) if mine else -1
    places: list[tuple[int, int]] = []
    if at >= 0:                                    # word for word: each sentence where its own words are
        for first, last in spans:
            inside = [index for index, (_word, start, end) in enumerate(mine) if first <= start and end <= last]
            if inside:
                places.append((theirs[at + inside[0]][1], _past_end(source, theirs[at + inside[-1]][2])))
            else:
                places.append((-1, -1))
        return places
    after = 0                                      # said otherwise: each one after the one before it
    for first, last in spans:
        sentence = said[first:last]
        place = (locate_start(sentence, source, after), locate(sentence, source, after))
        places.append(place)
        if place[1] >= 0:
            after = place[1]
    return places


__all__ = ["COMMON", "MIN_LETTERS", "distinctive", "frame_words", "locate", "locate_start", "match", "sentence_at",
           "sentence_places", "sentence_spans", "spoken_offset", "tokens"]
