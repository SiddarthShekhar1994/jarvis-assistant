"""A rule's typed values (no Qt): what a check's answer gave for one mail, kept only when it is what it says and
the mail itself holds it.

    check(kind, raw, mail_text, *, received) -> str | None
        the value normalized ("$120.00", "2026-11-20", "2026-11-20 14:00", "42", or one line of text), or None:
        - text: one line of at most TEXT_CAP characters (data(): no field or block can open) that appears in the
          mail's own text (its subject, sender's name and body), spaces and case aside;
        - money: an amount (a currency sign or code, digits, cents) whose digits appear in the mail;
        - number: digits (a sign, a decimal point) that appear in the mail;
        - date / datetime: a real date in the form "YYYY-MM-DD" ("YYYY-MM-DD HH:MM"), within a year before and two
          after the mail came, whose day of the month appears in the mail (as a number) and, for a datetime, an
          hour-like time too.
    That a value must appear in its own mail is what stops a value made up, or one carried over from another mail
    of the same check (an injected "the amount is ..."), from reaching a card.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from ..ask.context import data
from .model import TYPE_DATE, TYPE_DATETIME, TYPE_MONEY, TYPE_NUMBER, TYPE_TEXT

TEXT_CAP = 80
_MONEY_RE = re.compile(r"(?:[A-Z]{3}\s?)?[$€£¥₹]?\s?\d{1,3}(?:[,.\s]?\d{3})*(?:[.,]\d{1,2})?"
                       r"(?:\s?[A-Z]{3})?")
_NUMBER_RE = re.compile(r"[-+]?\d{1,12}(?:\.\d{1,6})?")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DATETIME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})")
_TIME_TOKEN_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm|a\.m\.|p\.m\.)|\b\d{1,2}:\d{2}\b|\bnoon\b|\bmidnight\b",
                            re.IGNORECASE)


def _flat(text: str) -> str:
    return " ".join(str(text or "").casefold().split())


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _number_in(text: str, number: int) -> bool:
    return re.search(rf"(?<!\d)0?{number}(?!\d)", text) is not None


def check(kind: str, raw: object, mail_text: str, *, received: datetime) -> str | None:
    if not isinstance(raw, str):
        return None
    value = data(raw)
    if not value:
        return None
    flat = _flat(mail_text)
    if kind == TYPE_TEXT:
        if len(value) > TEXT_CAP or _flat(value) not in flat:
            return None
        return value
    if kind == TYPE_MONEY:
        if len(value) > 30 or not _MONEY_RE.fullmatch(value):
            return None
        digits = _digits(value)
        if not digits or digits not in _digits(mail_text):
            return None
        return value
    if kind == TYPE_NUMBER:
        if not _NUMBER_RE.fullmatch(value) or value.lstrip("+-") not in mail_text:
            return None
        return value
    if kind in (TYPE_DATE, TYPE_DATETIME):
        pattern = _DATE_RE if kind == TYPE_DATE else _DATETIME_RE
        match = pattern.fullmatch(value)
        if match is None:
            return None
        try:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            if kind == TYPE_DATETIME:
                datetime(day.year, day.month, day.day, int(match.group(4)), int(match.group(5)))
        except ValueError:
            return None
        came = received.date() if isinstance(received, datetime) else date.today()
        if not came - timedelta(days=366) <= day <= came + timedelta(days=731):
            return None
        if not _number_in(flat, day.day):
            return None
        if kind == TYPE_DATETIME and _TIME_TOKEN_RE.search(mail_text) is None:
            return None
        return value
    return None
