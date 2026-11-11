"""Instant answers' maths, on this PC only: a small safe calculator, percentages and unit conversions (no Qt, no
network, nothing logged).

Owner decision 2026-11-10 ("instant answers"): everyday sums and conversions are answered by Jarvis himself in well
under a second, typed or spoken, without the owner's Claude plan. Whatever this module cannot read for sure is
refused (``None``), and the request goes to Claude exactly as before: nothing is guessed.

The calculator never runs Python on the owner's words (no eval, exec, compile or ast evaluation). ``calculate`` reads
numbers, ``+ - * / ^ ( ) %``, "of" after a percentage and a few words ("plus", "times", "divided by", "squared",
"square root of" ...), and a recursive-descent parser works the value out with exact fractions where it can (floats
only for roots that are not whole and for powers that are not whole). Limits: MAX_INPUT characters, MAX_TOKENS
tokens, whole powers up to MAX_POWER and results up to MAX_DIGITS digits; past them nothing is answered. A division
by zero is a sure answer of its own (``Calc.zero_division``). A percentage added or taken away ("80 + 15%") means
different things on different calculators, so it is refused; "increase 80 by 15%" says which.

``spoken_numbers`` turns number words into digits first ("twenty five" -> "25", "one hundred and five" -> "105",
"three point five" -> "3.5", "a thousand" -> "1000"), so spoken questions read like typed ones.

Units (``UNITS``): length, mass, volume (US kitchen measures; imperial gallons and pints only by name), temperature,
speed, area, time (seconds to weeks: months and years have no one length) and data sizes (KB, MB, GB, TB are
decimal; KiB, MiB, GiB, TiB binary). "ounces" are weight ("fl oz" is volume) and "pounds" are weight (a currency
question is ask.instant_net's). An amount in one unit and a unit of another kind is refused.

Public API
==========
``spoken_numbers(text)``; ``calculate(text) -> Calc | None``; ``percent(text) -> Calc | None``;
``convert(text) -> Conversion | None``; ``format_number(value, *, digits=10) -> (text, exact)``; ``Calc``,
``Conversion``, ``Unit``, ``UNITS``, ``NUMBER``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal
from fractions import Fraction
from typing import Union

Number = Union[Fraction, float]

MAX_INPUT = 200          # characters of an expression
MAX_TOKENS = 64
MAX_POWER = 1000         # the largest whole power read
MAX_DIGITS = 40          # a result with more digits than this before or after the point is not answered
NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"   # "1,250", "12.5", ".5" (never "1,2")


class MathRefused(ValueError):
    """Not something this module reads for sure: the request goes to Claude."""


@dataclass(frozen=True)
class Calc:
    """A worked-out sum: ``shown`` the sum as Jarvis read it ("12 × 7"), ``value`` its result as shown ("84"),
    ``exact`` False when ``value`` is rounded, ``zero_division`` when it divides by zero (``value`` is "")."""

    shown: str
    value: str
    exact: bool = True
    zero_division: bool = False
    spoken: str = ""         # the result rounded for speech ("0.333"); "" = ``value``


@dataclass(frozen=True)
class Unit:
    key: str
    kind: str                # "length", "mass", "volume", "temperature", "speed", "area", "time", "data"
    factor: Fraction         # how many of the kind's base unit one of it is (temperature: unused)
    shown: str               # on screen: "km", "miles", "°F"
    singular: str            # said: "kilometre" / "kilometres"
    plural: str


@dataclass(frozen=True)
class Conversion:
    amount: str              # the amount as given ("5")
    source: Unit
    target: Unit
    value: str               # the result as shown ("3.107")
    exact: bool
    spoken: str              # the result rounded for speech


# --------------------------------------------------------------------------
# Number words
# --------------------------------------------------------------------------

_SMALL = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_SCALES = {"thousand": 10 ** 3, "million": 10 ** 6, "billion": 10 ** 9}
_HYPHENATED = re.compile(r"\b(" + "|".join(_TENS) + r")-(" + "|".join(name for name in _SMALL
                                                                      if 0 < _SMALL[name] < 10) + r")\b")
_NUMERAL = re.compile(NUMBER + r"$")


def spoken_numbers(text: str) -> str:
    """``text`` (lower case) with each run of number words as digits; every other word as it was."""
    words = _HYPHENATED.sub(r"\1 \2", text).split()
    out: list[str] = []
    index = 0
    while index < len(words):
        value, used = _read_number(words, index)
        if used:
            out.append(_plain(value))
            index += used
        else:
            out.append(words[index])
            index += 1
    return " ".join(out)


def _read_number(words: list[str], start: int) -> tuple[Fraction, int]:
    """The number spelled from ``words[start]`` on and how many words it took (0: none starts there)."""
    total, current, index, seen, last = 0, 0, start, False, ""
    count = len(words)
    while index < count:
        word = words[index]
        following = words[index + 1] if index + 1 < count else ""
        if word in ("a", "an") and not seen and following in ("hundred", *_SCALES):
            current, seen, last = 1, True, "small"
        elif not seen and _NUMERAL.match(word) and following in ("hundred", *_SCALES):
            value = Fraction(word.replace(",", ""))
            if value.denominator != 1 and following == "hundred":
                break
            current, seen, last = value, True, "small"
        elif word in _SMALL:
            if seen and not (last == "tens" and _SMALL[word] < 10) and last not in ("hundred", "scale", "and"):
                break
            current += _SMALL[word]
            seen, last = True, "small"
        elif word in _TENS:
            if seen and last not in ("hundred", "scale", "and"):
                break
            current += _TENS[word]
            seen, last = True, "tens"
        elif word == "hundred":
            if not seen or last == "hundred" or current == 0 or current >= 100:
                break
            current *= 100
            last = "hundred"
        elif word in _SCALES:
            if not seen or last == "scale" or (current == 0 and total == 0):
                break
            total += current * _SCALES[word]
            current, last = 0, "scale"
        elif word == "and" and seen and last in ("hundred", "scale") and (following in _SMALL or following in _TENS):
            last = "and"
        elif word == "point" and seen and following in _SMALL and _SMALL[following] < 10:
            digits = []
            index += 1
            while index < count and words[index] in _SMALL and _SMALL[words[index]] < 10:
                digits.append(str(_SMALL[words[index]]))
                index += 1
            return Fraction(total + current) + Fraction("0." + "".join(digits)), index - start
        else:
            break
        index += 1
    if not seen:
        return Fraction(0), 0
    if last == "and":                     # "a hundred and" with nothing after: the "and" is not the number's
        index -= 1
    return Fraction(total + current), index - start


def _plain(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return format(Decimal(value.numerator) / Decimal(value.denominator), "f")


# --------------------------------------------------------------------------
# Showing numbers
# --------------------------------------------------------------------------

def _decimal(value: Number, digits: int) -> Decimal:
    context = Context(prec=digits, rounding=ROUND_HALF_EVEN)
    if isinstance(value, Fraction):
        return context.divide(Decimal(value.numerator), Decimal(value.denominator))
    return context.create_decimal(repr(value))


def _grouped(text: str) -> str:
    """"1234567.5" -> "1,234,567.5" (the sign kept)."""
    sign = "-" if text.startswith("-") else ""
    whole, _point, part = text.lstrip("-").partition(".")
    whole = f"{int(whole):,}" if whole else "0"
    return sign + whole + ("." + part if part else "")


def format_number(value: Number, *, digits: int = 10) -> tuple[str, bool]:
    """``value`` for the screen with thousands separators: exact when it can be shown in full within MAX_DIGITS
    digits (a whole number, a decimal that ends), else rounded to ``digits`` significant digits (exact False).
    MathRefused for a value too large to show or not finite."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MathRefused("not finite")
        if value.is_integer() and abs(value) < 10 ** 15:
            return _grouped(str(int(value))), False
        value_text = format(_decimal(value, digits).normalize(), "f")
        return _grouped(value_text), False
    if value.denominator == 1:
        if len(str(abs(value.numerator))) > MAX_DIGITS:
            raise MathRefused("too many digits")
        return _grouped(str(value.numerator)), True
    whole_digits = len(str(abs(value.numerator) // value.denominator))
    if whole_digits > MAX_DIGITS:
        raise MathRefused("too many digits")
    exact = _terminating(value.denominator)
    if exact:
        text = format(_decimal(value, whole_digits + MAX_DIGITS).normalize(), "f")
        if len(text.lstrip("-").replace(".", "").lstrip("0")) <= max(digits, whole_digits + 6):
            return _grouped(text), True
    text = format(_decimal(value, max(digits, whole_digits)).normalize(), "f")
    return _grouped(text), False


def _terminating(denominator: int) -> bool:
    for prime in (2, 5):
        while denominator % prime == 0:
            denominator //= prime
    return denominator == 1


def spoken_number(value: Number) -> str:
    """``value`` rounded for speech: whole numbers in full, else at most 3 places and 4 significant digits."""
    if isinstance(value, Fraction) and value.denominator == 1:
        return _grouped(str(value.numerator))
    magnitude = abs(float(value))
    if magnitude >= 1000:
        return _grouped(str(round(float(value))))
    if magnitude < 1:
        return format(_decimal(value, 3).normalize(), "f")
    places = max(0, 3 - len(str(int(magnitude))) + 1)
    text = f"{float(value):.{places}f}".rstrip("0").rstrip(".") if places else str(round(float(value)))
    return _grouped(text if text not in ("-0", "") else "0")


# --------------------------------------------------------------------------
# The calculator
# --------------------------------------------------------------------------

_WORDS = (   # (phrase, operator) - read before the tokens, longest first
    (r"to the power of", "^"), (r"raised to the power of", "^"), (r"raised to", "^"), (r"to the power", "^"),
    (r"multiplied by", "*"), (r"times", "*"), (r"divided by", "/"), (r"over", "/"),
    (r"plus", "+"), (r"minus", "-"), (r"per cent", "%"), (r"percent", "%"),
    (r"squared", "^ 2"), (r"cubed", "^ 3"),
    (r"the square root of", "√"), (r"square root of", "√"), (r"sqrt of", "√"), (r"sqrt", "√"),
)
_WORD_RES = [(re.compile(r"(?<![a-z])" + phrase + r"(?![a-z])"), operator) for phrase, operator in _WORDS]
_SYMBOLS = {"×": "*", "x": "*", "·": "*", "÷": "/", "−": "-", "–": "-", "**": "^"}
_TOKEN = re.compile(r"\s*(?:(?P<number>" + NUMBER + r")|(?P<op>\*\*|[-+*/^()%√×x·÷−–])|(?P<of>of)(?![a-z]))")


@dataclass(frozen=True)
class _Value:
    value: Number
    percent: bool = False


def _tokens(text: str) -> list[object]:
    text = text.strip()
    if not text or len(text) > MAX_INPUT:
        raise MathRefused("empty or too long")
    for pattern, operator in _WORD_RES:
        text = pattern.sub(f" {operator} ", text)
    tokens: list[object] = []
    position = 0
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = _TOKEN.match(text, position)
        if match is None:
            raise MathRefused("not a sum")
        if match.group("number"):
            tokens.append(Fraction(match.group("number").replace(",", "")))
        elif match.group("of"):
            tokens.append("of")
        else:
            operator = match.group("op")
            if operator == "x" and not (tokens and (isinstance(tokens[-1], Fraction) or tokens[-1] in (")", "%"))):
                raise MathRefused("an x that is no times")
            tokens.append(_SYMBOLS.get(operator, operator))
        position = match.end()
        if len(tokens) > MAX_TOKENS:
            raise MathRefused("too long")
    return tokens


class _Parser:
    def __init__(self, tokens: list[object]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> object:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> object:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def whole(self) -> _Value:
        value = self.expr()
        if self.index != len(self.tokens):
            raise MathRefused("left over")
        return value

    def expr(self) -> _Value:
        left = self.term()
        while self.peek() in ("+", "-"):
            operator = self.take()
            right = self.term()
            if left.percent or right.percent:
                raise MathRefused("a percentage added or taken away")
            left = _Value(_checked(left.value + right.value if operator == "+" else left.value - right.value))
        return left

    def term(self) -> _Value:
        left = self.unary()
        while self.peek() in ("*", "/", "of"):
            operator = self.take()
            right = self.unary()
            if operator == "of":
                if not left.percent or right.percent:
                    raise MathRefused("of")
                left = _Value(_checked(left.value * right.value))
            elif operator == "*":
                left = _Value(_checked(left.value * right.value))
            else:
                if right.value == 0:
                    raise ZeroDivisionError
                left = _Value(_checked(left.value / right.value))
        return left

    def unary(self) -> _Value:
        if self.peek() == "-":
            self.take()
            inner = self.unary()
            return _Value(-inner.value, inner.percent)
        if self.peek() == "+":
            self.take()
            return self.unary()
        return self.power()

    def power(self) -> _Value:
        base = self.postfix()
        if self.peek() == "^":
            self.take()
            exponent = self.unary()
            if base.percent or exponent.percent:
                raise MathRefused("a percentage power")
            return _Value(_power(base.value, exponent.value))
        return base

    def postfix(self) -> _Value:
        value = self.atom()
        while self.peek() == "%":
            self.take()
            if value.percent:
                raise MathRefused("%%")
            value = _Value(value.value / 100, True)
        return value

    def atom(self) -> _Value:
        token = self.peek()
        if token == "(":
            self.take()
            inner = self.expr()
            if self.peek() != ")":
                raise MathRefused("no closing bracket")
            self.take()
            return inner
        if token == "√":
            self.take()
            inner = self.postfix()
            if inner.percent:
                raise MathRefused("the root of a percentage")
            return _Value(_root(inner.value))
        if isinstance(token, Fraction):
            self.take()
            return _Value(token)
        raise MathRefused("not a sum")


def _checked(value: Number) -> Number:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MathRefused("not finite")
        return value
    if max(len(str(abs(value.numerator))), len(str(value.denominator))) > 2 * MAX_DIGITS:
        raise MathRefused("too many digits")
    return value


def _power(base: Number, exponent: Number) -> Number:
    if isinstance(exponent, Fraction) and exponent.denominator == 1 and isinstance(base, Fraction):
        whole = exponent.numerator
        if abs(whole) > MAX_POWER:
            raise MathRefused("too large a power")
        if base == 0:
            if whole < 0:
                raise ZeroDivisionError
            return Fraction(0 if whole else 1)
        size = max(abs(base.numerator), base.denominator)
        if size > 1 and abs(whole) * math.log10(size) > MAX_DIGITS + 1:
            raise MathRefused("too large")
        return _checked(base ** whole)
    if float(base) < 0 or (float(base) == 0 and float(exponent) <= 0):
        raise MathRefused("a root of a negative number")
    try:
        return _checked(math.pow(float(base), float(exponent)))
    except (OverflowError, ValueError) as exc:
        raise MathRefused("too large") from exc


def _root(value: Number) -> Number:
    if value < 0:
        raise MathRefused("the root of a negative number")
    if isinstance(value, Fraction):
        top, bottom = math.isqrt(value.numerator), math.isqrt(value.denominator)
        if top * top == value.numerator and bottom * bottom == value.denominator:
            return Fraction(top, bottom)
    return math.sqrt(float(value))


_SHOW = {"*": "×", "/": "÷", "-": "−"}
_LEAD = re.compile(r"^(?:(?:what(?:'s| is)|whats|how much is|calculate|compute|work out|solve)\s+)?(?:the\s+)?")
_TAIL = re.compile(r"\s*(?:=|equals?|is what|is)\s*$")


def calculate(text: str) -> Calc | None:
    """The sum ``text`` asks for ("what's 12 x 7", "15% of 80", "(3+4)^2", "square root of 144"), worked out; None
    when it is not a sum read for sure (a bare number is no sum)."""
    words = _TAIL.sub("", _LEAD.sub("", text.strip()))
    try:
        tokens = _tokens(words)
        if not any(token in ("+", "-", "*", "/", "^", "%", "√", "of") for token in tokens) or \
                (len(tokens) == 2 and tokens[0] == "-"):
            return None
        shown = _shown(tokens)
        try:
            value = _Parser(tokens).whole()
        except ZeroDivisionError:
            return Calc(shown, "", exact=True, zero_division=True)
        result, exact = format_number(value.value)
        return Calc(shown, result, exact, spoken=spoken_number(value.value))
    except MathRefused:
        return None


def _shown(tokens: list[object]) -> str:
    parts: list[str] = []
    for index, token in enumerate(tokens):
        if isinstance(token, Fraction):
            parts.append(format_number(token)[0])
            continue
        before = tokens[index - 1] if index else None
        unary = token in ("-", "+") and (before is None or before in ("+", "-", "*", "/", "^", "(", "of", "√"))
        parts.append(("\0" if unary else "") + _SHOW.get(str(token), str(token)))
    text = " ".join(parts)
    for old, new in (("( ", "("), (" )", ")"), (" %", "%"), ("√ ", "√"), ("\0− ", "−"), ("\0+ ", "+")):
        text = text.replace(old, new)
    return text.replace("\0", "")


# --------------------------------------------------------------------------
# Percentages said in words
# --------------------------------------------------------------------------

_N = r"(-?" + NUMBER + r")"
_PCT = r"\s*(?:%|percent|per cent)"
_PERCENT_FORMS = (
    ("share", re.compile(_N + r" is what" + _PCT + r" of " + _N)),
    ("share_rev", re.compile(r"what" + _PCT + r" of " + _N + r" is " + _N)),
    ("up", re.compile(r"(?:increase|raise) " + _N + r" by " + _N + _PCT)),
    ("down", re.compile(r"(?:decrease|reduce|lower) " + _N + r" by " + _N + _PCT)),
    ("off", re.compile(r"(?:what(?:'s| is) )?" + _N + _PCT + r" off(?: of)? " + _N)),
    ("tip", re.compile(r"(?:what(?:'s| is) (?:an? )?)?" + _N + _PCT + r" tip (?:on|for) " + _N)),
)


def percent(text: str) -> Calc | None:
    """A percentage said in words: "80 is what percent of 200" (40%), "increase 80 by 15%" (92), "20% off 50" (40),
    "15% tip on 80" (12). None for anything else."""
    words = " ".join(text.split())
    for kind, pattern in _PERCENT_FORMS:
        match = pattern.fullmatch(words)
        if not match:
            continue
        first, second = (Fraction(group.replace(",", "")) for group in match.groups())
        try:
            if kind == "share":
                if second == 0:
                    return Calc(words, "", zero_division=True)
                value, shown = first / second * 100, f"{format_number(first)[0]} of {format_number(second)[0]}"
            elif kind == "share_rev":
                if first == 0:
                    return Calc(words, "", zero_division=True)
                value, shown = second / first * 100, f"{format_number(second)[0]} of {format_number(first)[0]}"
            elif kind == "up":
                value, shown = first * (1 + second / 100), f"{format_number(first)[0]} + {format_number(second)[0]}%"
            elif kind == "down":
                value, shown = first * (1 - second / 100), f"{format_number(first)[0]} − {format_number(second)[0]}%"
            elif kind == "off":
                value, shown = second * (1 - first / 100), f"{format_number(second)[0]} − {format_number(first)[0]}%"
            else:
                value, shown = second * first / 100, f"{format_number(first)[0]}% of {format_number(second)[0]}"
            result, exact = format_number(value)
        except MathRefused:
            return None
        suffix = "%" if kind in ("share", "share_rev") else ""
        return Calc(shown, result + suffix, exact, spoken=spoken_number(value) + (" percent" if suffix else ""))
    return None


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

def _unit(key: str, kind: str, factor: str, shown: str, singular: str, plural: str,
          aliases: tuple[str, ...]) -> tuple[Unit, tuple[str, ...]]:
    return Unit(key, kind, Fraction(factor), shown, singular, plural), aliases


_UNIT_LIST = (
    _unit("mm", "length", "0.001", "mm", "millimetre", "millimetres", ("mm", "millimeter", "millimeters",
                                                                       "millimetre", "millimetres")),
    _unit("cm", "length", "0.01", "cm", "centimetre", "centimetres", ("cm", "centimeter", "centimeters",
                                                                      "centimetre", "centimetres")),
    _unit("m", "length", "1", "m", "metre", "metres", ("m", "meter", "meters", "metre", "metres")),
    _unit("km", "length", "1000", "km", "kilometre", "kilometres", ("km", "kms", "kilometer", "kilometers",
                                                                    "kilometre", "kilometres")),
    _unit("in", "length", "0.0254", "inches", "inch", "inches", ("in", "inch", "inches")),
    _unit("ft", "length", "0.3048", "feet", "foot", "feet", ("ft", "foot", "feet")),
    _unit("yd", "length", "0.9144", "yards", "yard", "yards", ("yd", "yds", "yard", "yards")),
    _unit("mi", "length", "1609.344", "miles", "mile", "miles", ("mi", "mile", "miles")),
    _unit("nmi", "length", "1852", "nautical miles", "nautical mile", "nautical miles",
          ("nmi", "nautical mile", "nautical miles")),
    _unit("mg", "mass", "0.000001", "mg", "milligram", "milligrams", ("mg", "milligram", "milligrams")),
    _unit("g", "mass", "0.001", "g", "gram", "grams", ("g", "gram", "grams", "gramme", "grammes")),
    _unit("kg", "mass", "1", "kg", "kilogram", "kilograms", ("kg", "kgs", "kilo", "kilos", "kilogram", "kilograms",
                                                            "kilogramme", "kilogrammes")),
    _unit("t", "mass", "1000", "tonnes", "tonne", "tonnes", ("tonne", "tonnes", "metric ton", "metric tons")),
    _unit("oz", "mass", "0.028349523125", "ounces", "ounce", "ounces", ("oz", "ounce", "ounces")),
    _unit("lb", "mass", "0.45359237", "pounds", "pound", "pounds", ("lb", "lbs", "pound", "pounds")),
    _unit("st", "mass", "6.35029318", "stone", "stone", "stone", ("st", "stone", "stones")),
    _unit("ml", "volume", "0.001", "ml", "millilitre", "millilitres", ("ml", "milliliter", "milliliters",
                                                                       "millilitre", "millilitres")),
    _unit("cl", "volume", "0.01", "cl", "centilitre", "centilitres", ("cl", "centiliter", "centiliters",
                                                                      "centilitre", "centilitres")),
    _unit("l", "volume", "1", "l", "litre", "litres", ("l", "liter", "liters", "litre", "litres", "ltr")),
    _unit("m3", "volume", "1000", "m³", "cubic metre", "cubic metres", ("m3", "m³", "cubic meter", "cubic meters",
                                                                        "cubic metre", "cubic metres")),
    _unit("tsp", "volume", "0.00492892159375", "teaspoons", "teaspoon", "teaspoons", ("tsp", "teaspoon",
                                                                                       "teaspoons")),
    _unit("tbsp", "volume", "0.01478676478125", "tablespoons", "tablespoon", "tablespoons", ("tbsp", "tablespoon",
                                                                                              "tablespoons")),
    _unit("floz", "volume", "0.0295735295625", "fl oz", "fluid ounce", "fluid ounces", ("fl oz", "fluid ounce",
                                                                                         "fluid ounces")),
    _unit("cup", "volume", "0.2365882365", "cups", "cup", "cups", ("cup", "cups")),
    _unit("pt", "volume", "0.473176473", "pints", "pint", "pints", ("pt", "pint", "pints", "us pint", "us pints")),
    _unit("qt", "volume", "0.946352946", "quarts", "quart", "quarts", ("qt", "quart", "quarts")),
    _unit("gal", "volume", "3.785411784", "gallons", "gallon", "gallons", ("gal", "gallon", "gallons", "us gallon",
                                                                           "us gallons")),
    _unit("impgal", "volume", "4.54609", "imperial gallons", "imperial gallon", "imperial gallons",
          ("imperial gallon", "imperial gallons")),
    _unit("imppt", "volume", "0.56826125", "imperial pints", "imperial pint", "imperial pints",
          ("imperial pint", "imperial pints")),
    _unit("c", "temperature", "1", "°C", "degree Celsius", "degrees Celsius",
          ("c", "°c", "celsius", "centigrade", "degrees c", "degrees celsius", "degree celsius",
           "degrees centigrade")),
    _unit("f", "temperature", "1", "°F", "degree Fahrenheit", "degrees Fahrenheit",
          ("f", "°f", "fahrenheit", "degrees f", "degrees fahrenheit", "degree fahrenheit")),
    _unit("k", "temperature", "1", "K", "kelvin", "kelvin", ("k", "kelvin", "kelvins", "degrees kelvin")),
    _unit("mps", "speed", "1", "m/s", "metre per second", "metres per second",
          ("m/s", "mps", "meters per second", "metres per second", "meter per second", "metre per second")),
    _unit("kmh", "speed", "5/18", "km/h", "kilometre per hour", "kilometres per hour",
          ("km/h", "kph", "kmh", "kmph", "km per hour", "kilometers per hour", "kilometres per hour",
           "kilometer per hour", "kilometre per hour")),
    _unit("mph", "speed", "0.44704", "mph", "mile per hour", "miles per hour", ("mph", "miles per hour",
                                                                               "mile per hour")),
    _unit("kn", "speed", "463/900", "knots", "knot", "knots", ("kn", "kt", "kts", "knot", "knots")),
    _unit("m2", "area", "1", "m²", "square metre", "square metres", ("m2", "m²", "sq m", "square meter",
                                                                     "square meters", "square metre", "square metres")),
    _unit("cm2", "area", "0.0001", "cm²", "square centimetre", "square centimetres",
          ("cm2", "cm²", "sq cm", "square centimeter", "square centimeters", "square centimetre",
           "square centimetres")),
    _unit("km2", "area", "1000000", "km²", "square kilometre", "square kilometres",
          ("km2", "km²", "sq km", "square kilometer", "square kilometers", "square kilometre", "square kilometres")),
    _unit("in2", "area", "0.00064516", "square inches", "square inch", "square inches",
          ("sq in", "in2", "in²", "square inch", "square inches")),
    _unit("ft2", "area", "0.09290304", "square feet", "square foot", "square feet",
          ("sq ft", "ft2", "ft²", "sqft", "square foot", "square feet")),
    _unit("mi2", "area", "2589988.110336", "square miles", "square mile", "square miles",
          ("sq mi", "mi2", "mi²", "square mile", "square miles")),
    _unit("acre", "area", "4046.8564224", "acres", "acre", "acres", ("acre", "acres")),
    _unit("ha", "area", "10000", "hectares", "hectare", "hectares", ("ha", "hectare", "hectares")),
    _unit("s", "time", "1", "seconds", "second", "seconds", ("s", "sec", "secs", "second", "seconds")),
    _unit("min", "time", "60", "minutes", "minute", "minutes", ("min", "mins", "minute", "minutes")),
    _unit("h", "time", "3600", "hours", "hour", "hours", ("h", "hr", "hrs", "hour", "hours")),
    _unit("day", "time", "86400", "days", "day", "days", ("day", "days")),
    _unit("week", "time", "604800", "weeks", "week", "weeks", ("wk", "wks", "week", "weeks")),
    _unit("byte", "data", "1", "bytes", "byte", "bytes", ("byte", "bytes")),
    _unit("kb", "data", "1000", "KB", "kilobyte", "kilobytes", ("kb", "kilobyte", "kilobytes")),
    _unit("mb", "data", "1000000", "MB", "megabyte", "megabytes", ("mb", "megabyte", "megabytes")),
    _unit("gb", "data", "1000000000", "GB", "gigabyte", "gigabytes", ("gb", "gigabyte", "gigabytes")),
    _unit("tb", "data", "1000000000000", "TB", "terabyte", "terabytes", ("tb", "terabyte", "terabytes")),
    _unit("kib", "data", "1024", "KiB", "kibibyte", "kibibytes", ("kib", "kibibyte", "kibibytes")),
    _unit("mib", "data", "1048576", "MiB", "mebibyte", "mebibytes", ("mib", "mebibyte", "mebibytes")),
    _unit("gib", "data", "1073741824", "GiB", "gibibyte", "gibibytes", ("gib", "gibibyte", "gibibytes")),
    _unit("tib", "data", "1099511627776", "TiB", "tebibyte", "tebibytes", ("tib", "tebibyte", "tebibytes")),
)
UNITS: dict[str, Unit] = {unit.key: unit for unit, _aliases in _UNIT_LIST}
_ALIASES: dict[str, Unit] = {alias: unit for unit, aliases in _UNIT_LIST for alias in aliases}
_UNIT_RE = "(?:" + "|".join(re.escape(alias) for alias in sorted(_ALIASES, key=len, reverse=True)) + ")"
_AMOUNT = r"(?P<amount>-?" + NUMBER + r")"
_ONE = r"(?:(?P<one>a|an|one)|" + _AMOUNT + r")"
_LEAD_UNITS = r"(?:(?:what(?:'s| is)|whats|how much is|how many is|how far is|how long is|convert)\s+)?"
_CONVERT_FORMS = (
    re.compile(_LEAD_UNITS + _AMOUNT + r"\s*(?P<source>" + _UNIT_RE + r")\s+(?:to|in|into|as)\s+(?P<target>"
               + _UNIT_RE + r")"),
    re.compile(r"how many\s+(?P<target>" + _UNIT_RE + r")\s+(?:are\s+)?(?:there\s+)?in\s+" + _ONE + r"\s*"
               r"(?P<source>" + _UNIT_RE + r")"),
    re.compile(r"how many\s+(?P<target>" + _UNIT_RE + r")\s+(?:is|are|make)\s+" + _ONE + r"\s*(?P<source>"
               + _UNIT_RE + r")"),
    re.compile(_AMOUNT + r"\s*(?P<source>" + _UNIT_RE + r")\s+(?:is|are|equals|in)\s+how many\s+(?P<target>"
               + _UNIT_RE + r")"),
)


def convert(text: str) -> Conversion | None:
    """The conversion ``text`` asks for ("5 km in miles", "convert 70 f to c", "how many ounces in a pound"); None
    when it is not one read for sure (units of two kinds, a unit not listed, a negative length ...)."""
    words = " ".join(text.split())
    for pattern in _CONVERT_FORMS:
        match = pattern.fullmatch(words)
        if not match:
            continue
        source, target = _ALIASES[match.group("source")], _ALIASES[match.group("target")]
        if source.kind != target.kind or source is target:
            return None
        given = match.group("amount")
        amount = Fraction(given.replace(",", "")) if given else Fraction(1)
        if amount < 0 and source.kind != "temperature":
            return None
        if source.kind == "temperature":
            if _temperature(amount, source.key, "k") < 0:
                return None                       # colder than absolute zero: no such temperature
            value = _temperature(amount, source.key, target.key)
        else:
            value = amount * source.factor / target.factor
        try:
            shown, exact = format_number(value, digits=4)
            if exact and len(shown.replace(",", "").partition(".")[2]) > 4:
                shown, exact = format_number(Fraction(_decimal(value, max(4, _whole_digits(value) + 2))), digits=4)
                exact = False
        except MathRefused:
            return None
        amount_shown = format_number(amount)[0] if given else "1"
        return Conversion(amount_shown, source, target, shown, exact, spoken_number(value))
    return None


def _whole_digits(value: Fraction) -> int:
    return len(str(abs(value.numerator) // value.denominator))


def _temperature(amount: Fraction, source: str, target: str) -> Fraction:
    celsius = {"c": amount, "f": (amount - 32) * Fraction(5, 9), "k": amount - Fraction(27315, 100)}[source]
    return {"c": celsius, "f": celsius * Fraction(9, 5) + 32, "k": celsius + Fraction(27315, 100)}[target]
