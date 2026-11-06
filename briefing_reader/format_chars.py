"""Invisible format characters in text from outside Jarvis: one rule for the whole app (no Qt, no I/O, no logs).

A character is a format character when its Unicode category is Cf: bidi controls, zero-width spaces and joiners, the
soft hyphen, the BOM, the tag characters U+E0001 and U+E0020-E007F, U+0600-0605, U+110BD and the rest - text that a
reader does not see but a program does. Memory notes, mail (Ask's searches, headers and bodies), Ask's context blocks
and the desktop guard drop or refuse them with this rule. The line and paragraph separators (Zl, Zp) are not format
characters: each caller keeps its own handling of them (``str.split()`` already reads them as whitespace; the desktop
guard counts them as hidden too).

A task's text (agent.*) needs a wider rule, because a model can be told to write what no one sees: ``invisible_char``
adds to Cf every other character that draws nothing or a blank and is not whitespace - Unicode's
Default_Ignorable_Code_Point (variation selectors U+FE00-FE0F and U+E0100-E01EF, Mongolian's U+180B-180F, U+034F,
Khmer's U+17B4-5, the Hangul fillers U+115F, U+1160, U+3164, U+FFA0, the unassigned tag block) and the Braille blank
U+2800. They are the agent's alone: an ordinary text keeps its emoji's U+FE0F, so ``drop_format`` stays as it is for
everything else.

Public API: ``format_char(char)``, ``has_format(text)``, ``drop_format(text)``; ``invisible_char(char)``,
``has_invisible(text)``, ``drop_invisible(text)``.
"""

from __future__ import annotations

import unicodedata

# Inclusive ranges of what draws nothing or a blank though it is no format character: Default_Ignorable_Code_Point (the
# ones of category Cf are here too, so this table holds the property whole) and the Braille blank.
_INVISIBLE_RANGES = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
                     (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x2800, 0x2800),
                     (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
                     (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
_INVISIBLE = frozenset(code for first, last in _INVISIBLE_RANGES for code in range(first, last + 1))


def format_char(char: str) -> bool:
    """``char`` (one character) is an invisible format character (Unicode category Cf)."""
    return unicodedata.category(char) == "Cf"


def has_format(text: str) -> bool:
    """``text`` holds a format character."""
    return not text.isascii() and any(format_char(char) for char in text)


def drop_format(text: str) -> str:
    """``text`` without its format characters."""
    if text.isascii():
        return text
    return "".join(char for char in text if not format_char(char))


def invisible_char(char: str) -> bool:
    """``char`` (one character) draws nothing or a blank and is not whitespace: a format character, a variation
    selector, a filler and the like (the module docs). A task's rule."""
    return ord(char) in _INVISIBLE or format_char(char)


def has_invisible(text: str) -> bool:
    """``text`` holds such a character."""
    return not text.isascii() and any(invisible_char(char) for char in text)


def drop_invisible(text: str) -> str:
    """``text`` without such characters."""
    if text.isascii():
        return text
    return "".join(char for char in text if not invisible_char(char))
