"""Instant answers: everyday questions Jarvis answers himself, typed or spoken, in well under a second and without
the owner's Claude plan (owner decisions 2026-11-10 and, round 2, 2026-11-11). Pure: no Qt, no network, nothing
logged.

    understand(text, now=, flags=, address=, hour24=)
        -> InstantReply | NetAsk | TimerAsk | ReminderAsk | CalendarAsk | TownAsk | None

``None`` means "not one of these, read for sure": the request goes to Claude exactly as before. Every pattern is a
FULL match of the request after the usual lead-in ("jarvis,", "hey jarvis", "please", "can you tell me") and a final
"." "!" "?" or "please" are put aside, with number words read as digits (instant_maths.spoken_numbers). A word left
over sends the request to Claude: nothing is guessed.

On this PC (an ``InstantReply`` at once):
* the time and the date ("what time is it", "what's the date", "what day is it", "what year is it"), and the time in
  another place from ``PLACES`` (about 150 cities, countries with one time zone, and US zone names) through zoneinfo -
  a place not listed, or listed in two zones, goes to Claude, and so does any place when this PC has no zone data;
* dates: "what day is the 25th", "what day is Christmas", "is the 4th of July a Friday", "days until Christmas", "how
  many days since 1 January", "days between 1 March and 1 June", "what's the date in 3 weeks", "10 days from now",
  "what was the date 5 days ago", tomorrow and yesterday; the fixed holidays only (``HOLIDAYS``: Easter and
  Thanksgiving move, so they go to Claude). A date without a year is its next one (its last one for "since");
* sums, percentages and conversions (instant_maths).

Asked of the app (the app answers them):
* ``TimerAsk``: set a timer ("set a timer for 10 minutes", "a 5 minute timer", "timer 90 seconds", "an hour and a
  half timer", "remind me in 20 minutes to stretch"), ask what is left on them, cancel one or all. Timers run from
  TIMER_MIN_S to TIMER_MAX_S: outside that the answer says so;
* ``NetAsk``: the weather (Open-Meteo) or a currency conversion (Frankfurter, the European Central Bank's reference
  rates) - only while ``InstantFlags.weather`` / ``currency`` are on; instant_net carries them out. The weather
  without a town uses ``InstantFlags.town`` (the town set by voice or typing, else [instant] town), and with neither
  the request goes to Claude. A currency name is read only where it names one currency ("dollars" are US dollars;
  "pesos" or "kroner" alone are not read).
* Round 2. ``ReminderAsk`` (with the timers): a reminder at a clock time today or tomorrow ("remind me at 5 pm to
  call mum", "remind me to call mum at 5", "set a reminder for 9:30 am", "remind me tomorrow at 9 am to stretch"),
  what reminders there are, cancel one ("cancel the 5 pm reminder") or all. Further ahead, repeating, or a label
  that names another day goes to Claude.
* ``CalendarAsk`` (only while ``InstantFlags.calendar`` is on; instant_calendar answers it from the calendars the app
  reads): the next meeting ("what's my next meeting", "do I have any more meetings today"), a day ("what's on my
  calendar tomorrow", "how many meetings do I have today"), free or busy at a time ("am I free at 3", "do I have
  anything at 4 pm tomorrow").
* ``TownAsk`` (with the weather): the weather's town by voice or typing ("my town is San Jose, California", "I live
  in Oslo", "use Paris for the weather"), "what's my town", "forget my town".

Clock times (reminders, "am I free at"): "5 pm", "5:30 p.m.", "17:30", "noon", "half past 3", "quarter to 6". A bare
hour ("at 3") is the next one within 12 hours, and the answer says which; with "tomorrow" a bare hour is read only
when one of its two readings falls between 7 AM and 10:59 PM ("4 tomorrow" is 4 PM; "7 tomorrow" goes to Claude).
"this morning", "this afternoon", "tonight" and "tomorrow evening" pick the half of the day.

Replies: ``InstantReply.text`` is shown (the conversation, the bar), ``speech`` is said, short, with the owner's form
of address (persona.with_address). Neither is ever logged: a reply's repr names its kind only.

Public API
==========
``understand``; ``InstantReply``, ``NetAsk``, ``TimerAsk``, ``ReminderAsk``, ``CalendarAsk``, ``TownAsk``,
``InstantFlags``; ``clean(text)``; ``duration_words(seconds)``; ``spoken_clock(moment)``; ``local_at(day, hour,
minute, now)``; ``KIND_*``, ``CAL_*``, ``REMINDER_*``, ``TOWN_*``; ``PLACES``; ``HOLIDAYS``; ``CURRENCIES``;
``TIMER_MIN_S``, ``TIMER_MAX_S``, ``REMINDER_MAX_S``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from typing import Union

from . import instant_maths as maths
from .persona import with_address
from .text_prep import format_time

KIND_TIME, KIND_DATE, KIND_PLACE_TIME, KIND_DATE_MATHS = "time", "date", "place_time", "date_maths"
KIND_MATHS, KIND_UNITS, KIND_TIMER, KIND_WEATHER, KIND_CURRENCY = "maths", "units", "timer", "weather", "currency"
KIND_CALENDAR, KIND_REMINDER, KIND_TOWN = "calendar", "reminder", "town"
TIMER_MIN_S = 10
TIMER_MAX_S = 4 * 3600
REMINDER_MAX_S = 48 * 3600
TIMER_SET, TIMER_CANCEL, TIMER_LEFT = "set", "cancel", "left"
REMINDER_SET, REMINDER_LIST, REMINDER_CANCEL = "set", "list", "cancel"
CAL_NEXT, CAL_MORE, CAL_DAY, CAL_COUNT, CAL_FREE = "next", "more", "day", "count", "free"
TOWN_SET, TOWN_SAY, TOWN_FORGET = "set", "say", "forget"
LABEL_MAX = 40


@dataclass(frozen=True)
class InstantReply:
    kind: str
    text: str = field(repr=False)            # shown: the conversation (and the bar, without ``brief``)
    speech: str = field(repr=False)          # said
    source: str = ""                         # the credit line under a network answer ("Weather data by Open-Meteo.com")
    brief: str = field(default="", repr=False)   # the bar's one line when ``text`` is long (a calendar's day); shown

    def __repr__(self) -> str:
        return f"InstantReply({self.kind})"


@dataclass(frozen=True)
class NetAsk:
    """A question the network answers (instant_net): the weather in ``town`` (``when``: "now" or "tomorrow";
    ``rain``: "will it rain" / "snow" asks only that), or ``amount`` of ``base`` in ``target`` (ISO codes)."""

    kind: str
    town: str = field(default="", repr=False)
    when: str = "now"
    rain: str = ""
    amount: Fraction = Fraction(1)
    base: str = ""
    target: str = ""
    town_given: bool = True                  # False: the [instant] town (the request named none)

    def __repr__(self) -> str:
        return f"NetAsk({self.kind})"


@dataclass(frozen=True)
class TimerAsk:
    """A timer request: TIMER_SET ``seconds`` with ``label`` (the owner's words, never logged), TIMER_LEFT, or
    TIMER_CANCEL (``every``: all of them; ``seconds``: the one of that length; else the only one)."""

    action: str
    seconds: int = 0
    label: str = field(default="", repr=False)
    every: bool = False
    bare: bool = False       # TIMER_LEFT without the word "timer" ("how much time is left"): only while one runs

    def __repr__(self) -> str:
        return f"TimerAsk({self.action})"


@dataclass(frozen=True)
class ReminderAsk:
    """A reminder request: REMINDER_SET ``at`` (aware, local; today or tomorrow, at most REMINDER_MAX_S ahead) with
    ``label`` (the owner's words, never logged or said), REMINDER_LIST, or REMINDER_CANCEL (``every``: all of them;
    ``clocks``: the one at one of these (hour 0-23, minute) wall-clock times - two for a bare hour - on ``day`` (0
    today, 1 tomorrow, -1 either); none of those: the only one)."""

    action: str
    at: datetime | None = None
    label: str = field(default="", repr=False)
    every: bool = False
    clocks: tuple[tuple[int, int], ...] = ()
    day: int = -1

    def __repr__(self) -> str:
        return f"ReminderAsk({self.action})"


@dataclass(frozen=True)
class CalendarAsk:
    """A calendar question the app answers from the calendars it reads (instant_calendar): CAL_NEXT (the next
    meeting), CAL_MORE (any more today), CAL_DAY and CAL_COUNT (``day``: 0 today, 1 tomorrow), CAL_FREE (free or
    busy ``at``: aware, local; ``busy``: the question asked "busy" or "anything", so "yes" means busy). ``noun``: the
    word asked about ("meeting", "event", "appointment", "call")."""

    action: str
    day: int = 0
    at: datetime | None = None
    busy: bool = False
    noun: str = "meeting"

    def __repr__(self) -> str:
        return f"CalendarAsk({self.action})"


@dataclass(frozen=True)
class TownAsk:
    """The weather's town: TOWN_SET ``town`` (as typed; the app looks it up and confirms the place it found),
    TOWN_SAY or TOWN_FORGET. ``town`` is never logged."""

    action: str
    town: str = field(default="", repr=False)

    def __repr__(self) -> str:
        return f"TownAsk({self.action})"


@dataclass(frozen=True)
class InstantFlags:
    weather: bool = False
    currency: bool = False
    timers: bool = True              # timers and reminders (the app's timer book)
    town: str = field(default="", repr=False)
    calendar: bool = False           # the app can read the calendars (CalendarAsk)


Answer = Union[InstantReply, NetAsk, TimerAsk, ReminderAsk, CalendarAsk, TownAsk, None]

# --------------------------------------------------------------------------
# Reading the request
# --------------------------------------------------------------------------

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})
_LEAD_IN = re.compile(r"^(?:(?:hey|hi|ok|okay|yo)\s+)?(?:jarvis[,:!.]?\s*)?(?:please\s+)?"
                      r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:tell\s+me\s+)?|tell\s+me\s+|"
                      r"do\s+you\s+know\s+|i\s+(?:want|need|would\s+like|'d\s+like)\s+to\s+know\s+)?(?:please\s+)?")
_TAIL_OFF = re.compile(r"(?:[\s,]+(?:please|jarvis|sir|thanks|thank\s+you))*[\s.!?]*$")


def clean(text: str) -> str:
    """The request as the patterns read it: NFKC, curly quotes straight, lower case, one space, the lead-in and a
    final "please" / "." / "?" put aside, number words as digits."""
    if any(unicodedata.category(char) == "No" for char in str(text or "")):
        return ""                                  # "5²", "½": NFKC would turn them into other numbers
    words = unicodedata.normalize("NFKC", str(text or "")).translate(_QUOTES)
    words = "".join(" " if unicodedata.category(char) == "Cc" else char for char in words
                    if unicodedata.category(char) != "Cf")
    words = " ".join(words.casefold().split())
    words = _TAIL_OFF.sub("", _LEAD_IN.sub("", words, count=1)).strip(" ,")
    return maths.spoken_numbers(words)


def _as_typed(words: str, text: str) -> str:
    """``words`` (as cleaned: lower case) as the owner typed them when they are there word for word, else as they are
    ("remind me in 5 minutes to call Ana": "call Ana")."""
    typed = " ".join(unicodedata.normalize("NFKC", str(text or "")).split())
    folded = typed.casefold()
    at = folded.find(words)
    return typed[at:at + len(words)] if at >= 0 and len(folded) == len(typed) else words


def understand(text: str, *, now: datetime, flags: InstantFlags = InstantFlags(), address: str = "sir",
               hour24: bool = False) -> Answer:
    """What Jarvis answers himself for ``text`` (see the module docs); None: it goes to Claude. ``now``: the local
    time, aware (datetime.now().astimezone())."""
    words = clean(text)
    if not words or len(words) > 160:
        return None
    if now.utcoffset() is None:
        now = now.astimezone()
    for reader in (_timer, _reminder, _clock, _place_time, _dates, _calendar):
        found = reader(words, now, flags, address, hour24)
        if found is not None:
            if isinstance(found, (TimerAsk, ReminderAsk)) and found.label:
                found = replace(found, label=_as_typed(found.label, text))
            return found
    if flags.weather:
        found = _town(words) or _weather(words, flags)
        if isinstance(found, TownAsk) and found.town:
            found = replace(found, town=_as_typed(found.town, text))
        if found is not None:
            return found
    if flags.currency:
        found = _currency(words)
        if found is not None:
            return found
    return _maths(words, address)


# --------------------------------------------------------------------------
# The clock
# --------------------------------------------------------------------------

_NOW = r"(?:\s+(?:right\s+)?now)?(?:\s+here)?"
_TIME_RE = re.compile(r"(?:what(?:'s|\s+is)\s+the\s+(?:current\s+)?time|what\s+time\s+is\s+it|what\s+time\s+it\s+is|"
                      r"(?:the\s+)?(?:current\s+)?time|whats\s+the\s+time|time\s+check|have\s+you\s+got\s+the\s+time|"
                      r"got\s+the\s+time)" + _NOW)
_DATE_RE = re.compile(r"(?:what(?:'s|\s+is)\s+(?:the\s+date|today'?s\s+date|the\s+date\s+today|today|the\s+day"
                      r"(?:\s+today)?|the\s+day\s+of\s+the\s+week)|what\s+date\s+is\s+it(?:\s+today)?|"
                      r"what\s+day\s+(?:of\s+the\s+week\s+)?is\s+it(?:\s+today)?|which\s+day\s+is\s+it(?:\s+today)?|"
                      r"today'?s\s+date|the\s+date|date|whats\s+the\s+date|what\s+is\s+the\s+date\s+today)")
_YEAR_RE = re.compile(r"what\s+year\s+is\s+it|what(?:'s|\s+is)\s+the\s+year|which\s+year\s+is\s+it")
_MONTH_RE = re.compile(r"what\s+month\s+is\s+it|what(?:'s|\s+is)\s+the\s+month|which\s+month\s+is\s+it")


def _long_date(day: date, *, year: bool = True) -> str:
    return f"{day:%A} {day.day} {day:%B}" + (f" {day.year}" if year else "")


def _clock(words: str, now: datetime, _flags: InstantFlags, address: str, hour24: bool) -> InstantReply | None:
    if _TIME_RE.fullmatch(words):
        return InstantReply(KIND_TIME, f"It's {format_time(now, hour24=hour24)}, {_long_date(now.date())}.",
                            with_address(f"It's {format_time(now)}, {{sir}}.", address))
    if _DATE_RE.fullmatch(words):
        return InstantReply(KIND_DATE, f"Today is {_long_date(now.date())}.",
                            with_address(f"It's {now:%A}, {now.day} {now:%B}, {{sir}}.", address))
    if _YEAR_RE.fullmatch(words):
        return InstantReply(KIND_DATE, f"It's {now.year}.", with_address(f"It's {now.year}, {{sir}}.", address))
    if _MONTH_RE.fullmatch(words):
        return InstantReply(KIND_DATE, f"It's {now:%B} {now.year}.",
                            with_address(f"It's {now:%B}, {{sir}}.", address))
    return None


# Where the time is asked for: a city, a country with one time zone, or a US zone's name. A place in two zones
# ("Texas", "Australia", "Portland") is not here: it goes to Claude.
_ZONES = {
    "America/New_York": "new york|new york city|nyc|manhattan|brooklyn|boston|philadelphia|washington dc|dc|"
                        "atlanta|miami|orlando|detroit|pittsburgh|baltimore|charlotte|eastern time|eastern",
    "America/Toronto": "toronto|montreal|ottawa|quebec city",
    "America/Chicago": "chicago|dallas|houston|austin|san antonio|new orleans|minneapolis|nashville|st louis|"
                       "saint louis|memphis|milwaukee|central time",
    "America/Winnipeg": "winnipeg",
    "America/Mexico_City": "mexico city|guadalajara|monterrey",
    "America/Denver": "denver|salt lake city|albuquerque|boise|mountain time",
    "America/Edmonton": "calgary|edmonton",
    "America/Phoenix": "phoenix|arizona|tucson",
    "America/Los_Angeles": "los angeles|la|san francisco|sf|san jose|san diego|sacramento|oakland|fresno|seattle|"
                           "las vegas|california|pacific time|silicon valley|palo alto|mountain view|"
                           "santa clara|cupertino|berkeley|long beach",
    "America/Vancouver": "vancouver",
    "America/Anchorage": "anchorage|alaska",
    "Pacific/Honolulu": "honolulu|hawaii",
    "America/Halifax": "halifax",
    "America/Sao_Paulo": "sao paulo|são paulo|rio de janeiro|rio|brasilia",
    "America/Argentina/Buenos_Aires": "buenos aires|argentina",
    "America/Bogota": "bogota|bogotá|colombia",
    "America/Lima": "peru",
    "America/Santiago": "santiago de chile",
    "America/Caracas": "caracas|venezuela",
    "Europe/London": "london|uk|united kingdom|england|britain|great britain|scotland|wales|edinburgh|manchester|"
                     "glasgow|liverpool|belfast|northern ireland|leeds|bristol|oxford|cambridge uk",
    "Europe/Dublin": "dublin|ireland",
    "Europe/Lisbon": "lisbon|porto|portugal",
    "Europe/Madrid": "madrid|barcelona|seville|spain",
    "Europe/Paris": "paris|france|lyon|marseille|toulouse",
    "Europe/Brussels": "brussels|belgium|antwerp",
    "Europe/Amsterdam": "amsterdam|netherlands|holland|rotterdam|the hague",
    "Europe/Berlin": "berlin|germany|munich|frankfurt|hamburg|cologne|stuttgart|dusseldorf|düsseldorf",
    "Europe/Zurich": "zurich|zürich|geneva|bern|switzerland",
    "Europe/Vienna": "vienna|austria",
    "Europe/Rome": "rome|italy|milan|venice|florence|naples",
    "Europe/Prague": "prague|czech republic|czechia",
    "Europe/Warsaw": "warsaw|poland|krakow|kraków",
    "Europe/Budapest": "budapest|hungary",
    "Europe/Stockholm": "stockholm|sweden",
    "Europe/Oslo": "oslo|norway",
    "Europe/Copenhagen": "copenhagen|denmark",
    "Europe/Helsinki": "helsinki|finland",
    "Europe/Athens": "athens|greece",
    "Europe/Istanbul": "istanbul|ankara|turkey|türkiye",
    "Europe/Kyiv": "kyiv|kiev|ukraine",
    "Europe/Bucharest": "bucharest|romania",
    "Europe/Moscow": "moscow|st petersburg|saint petersburg",
    "Atlantic/Reykjavik": "reykjavik|iceland",
    "Asia/Jerusalem": "jerusalem|tel aviv|israel",
    "Africa/Cairo": "cairo|egypt",
    "Asia/Riyadh": "riyadh|jeddah|mecca|saudi arabia",
    "Asia/Dubai": "dubai|abu dhabi|uae|united arab emirates",
    "Asia/Qatar": "doha|qatar",
    "Asia/Tehran": "tehran|iran",
    "Asia/Karachi": "karachi|lahore|islamabad|pakistan",
    "Asia/Kolkata": "india|delhi|new delhi|mumbai|bombay|bangalore|bengaluru|chennai|madras|kolkata|calcutta|pune|"
                    "ahmedabad|jaipur|goa|kochi|lucknow|chandigarh|noida|gurgaon|gurugram",
    "Asia/Colombo": "colombo|sri lanka",
    "Asia/Kathmandu": "kathmandu|nepal",
    "Asia/Dhaka": "dhaka|bangladesh",
    "Asia/Bangkok": "bangkok|thailand|phuket",
    "Asia/Ho_Chi_Minh": "ho chi minh city|saigon|hanoi|vietnam",
    "Asia/Kuala_Lumpur": "kuala lumpur|malaysia",
    "Asia/Singapore": "singapore",
    "Asia/Jakarta": "jakarta",
    "Asia/Manila": "manila|philippines",
    "Asia/Hong_Kong": "hong kong",
    "Asia/Shanghai": "beijing|shanghai|china|shenzhen|guangzhou|chengdu",
    "Asia/Taipei": "taipei|taiwan",
    "Asia/Seoul": "seoul|south korea|korea|busan",
    "Asia/Tokyo": "tokyo|japan|osaka|kyoto|yokohama",
    "Australia/Sydney": "sydney|canberra",
    "Australia/Melbourne": "melbourne",
    "Australia/Brisbane": "brisbane",
    "Australia/Perth": "western australia",
    "Australia/Adelaide": "adelaide",
    "Pacific/Auckland": "auckland|wellington|new zealand",
    "Africa/Lagos": "lagos|abuja|nigeria",
    "Africa/Nairobi": "nairobi|kenya",
    "Africa/Johannesburg": "johannesburg|cape town|pretoria|south africa",
    "Africa/Casablanca": "casablanca|morocco",
    "Africa/Accra": "accra|ghana",
    "Etc/UTC": "utc|gmt|zulu|coordinated universal time|greenwich mean time",
}
PLACES: dict[str, str] = {name: zone for zone, names in _ZONES.items() for name in names.split("|")}
# How a place's name is shown and said when it is not just its words in title case.
_PLACE_NAMES = {"nyc": "New York", "new york city": "New York", "la": "Los Angeles", "sf": "San Francisco",
                "dc": "Washington, D.C.", "washington dc": "Washington, D.C.", "uk": "the UK",
                "united kingdom": "the UK", "uae": "the UAE", "united arab emirates": "the UAE", "usa": "the US",
                "netherlands": "the Netherlands", "philippines": "the Philippines", "the hague": "The Hague",
                "est": "Eastern time", "edt": "Eastern time", "et": "Eastern time", "eastern": "Eastern time",
                "eastern time": "Eastern time", "central time": "Central time", "mountain time": "Mountain time",
                "pacific time": "Pacific time", "pst": "Pacific time", "pdt": "Pacific time", "pt": "Pacific time",
                "utc": "UTC", "gmt": "GMT", "zulu": "UTC", "coordinated universal time": "UTC",
                "greenwich mean time": "GMT", "st louis": "St. Louis", "st petersburg": "St. Petersburg",
                "czech republic": "the Czech Republic", "cambridge uk": "Cambridge", "rio": "Rio de Janeiro",
                "santiago de chile": "Santiago", "sao paulo": "São Paulo"}
_PLACE_TIME_RE = re.compile(
    r"(?:what(?:'s|\s+is)\s+the\s+(?:current\s+)?time|what\s+time\s+is\s+it|what\s+time\s+it\s+is|(?:the\s+)?"
    r"(?:current\s+)?time|(?:the\s+)?local\s+time|whats\s+the\s+time)(?:\s+(?:right\s+)?now)?\s+in\s+(?P<place>.+?)"
    r"(?:\s+(?:right\s+)?now)?"
    r"|what\s+time\s+is\s+it\s+(?:right\s+)?now\s+in\s+(?P<place2>.+)")


def _place_name(name: str) -> str:
    if name in _PLACE_NAMES:
        return _PLACE_NAMES[name]
    return " ".join(word if word in ("de", "of") else word[:1].upper() + word[1:] for word in name.split())


def _zone(name: str):  # -> ZoneInfo | None
    zone = PLACES.get(name)
    if zone is None:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(zone)
    except Exception:  # noqa: BLE001 - no zone data on this PC: not answered for sure
        return None


def _place_time(words: str, now: datetime, _flags: InstantFlags, address: str, hour24: bool) -> InstantReply | None:
    match = _PLACE_TIME_RE.fullmatch(words)
    if not match:
        return None
    name = (match.group("place") or match.group("place2") or "").strip()
    name = re.sub(r"^the\s+(?=uk$|us$|uae$|netherlands$|philippines$|czech republic$)", "", name)
    zone = _zone(name)
    if zone is None:
        return None
    there = now.astimezone(zone)
    shown = _place_name(name)
    gap = (there.utcoffset() - now.utcoffset()).total_seconds() / 3600
    if gap == 0:
        relative = "the same time as here"
    else:
        hours = abs(gap)
        amount = f"{hours:g}" if hours % 1 in (0, 0.5) else f"{hours:.2f}".rstrip("0")
        unit = "hour" if hours == 1 else "hours"
        relative = f"{amount} {unit} {'ahead of' if gap > 0 else 'behind'} you"
    day = "" if there.date() == now.date() else f" on {there:%A}"
    return InstantReply(KIND_PLACE_TIME,
                        f"In {shown} it's {format_time(there, hour24=hour24)}{day} ({relative}).",
                        with_address(f"In {shown} it's {format_time(there)}{day}, {{sir}}.", address))


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
           "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
           "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}
HOLIDAYS = {"christmas": (12, 25), "christmas day": (12, 25), "xmas": (12, 25), "christmas eve": (12, 24),
            "boxing day": (12, 26), "new year's eve": (12, 31), "new years eve": (12, 31), "new year's day": (1, 1),
            "new years day": (1, 1), "new year": (1, 1), "new year's": (1, 1), "new years": (1, 1),
            "the new year": (1, 1), "valentine's day": (2, 14), "valentines day": (2, 14), "valentine's": (2, 14),
            "valentines": (2, 14), "halloween": (10, 31), "st patrick's day": (3, 17), "st patricks day": (3, 17),
            "saint patrick's day": (3, 17), "st. patrick's day": (3, 17)}
_HOLIDAY_NAMES = {(12, 25): "Christmas", (12, 24): "Christmas Eve", (12, 26): "Boxing Day",
                  (12, 31): "New Year's Eve", (1, 1): "New Year's Day", (2, 14): "Valentine's Day",
                  (10, 31): "Halloween", (3, 17): "St Patrick's Day"}
_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
                  "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
                  "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
                  "nineteenth": 19, "twentieth": 20, "thirtieth": 30}
_MONTH = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + ")"
_ORD = (r"(?:\d{1,2}(?:st|nd|rd|th)|(?:(?:20|30)\s+)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)"
        r"|" + "|".join(name for name, value in _ORDINAL_WORDS.items() if value >= 10) + ")")
_DAY = r"(?:" + _ORD + r"|\d{1,2})"
_HOLIDAY = "(?:" + "|".join(re.escape(name) for name in sorted(HOLIDAYS, key=len, reverse=True)) + ")"
_DATE = (r"(?:(?:the\s+)?(?P<d1>" + _DAY + r")(?:\s+of)?\s+(?P<m1>" + _MONTH + r")(?:,?\s+(?P<y1>\d{4}))?"
         r"|(?P<m2>" + _MONTH + r")\s+(?:the\s+)?(?P<d2>" + _DAY + r")(?:,?\s+(?P<y2>\d{4}))?"
         r"|(?:the\s+)?(?P<d3>" + _ORD + r")|the\s+(?P<d4>\d{1,2})"
         r"|(?P<iso>\d{4}-\d{2}-\d{2})|(?P<hol>" + _HOLIDAY + r")(?:\s+(?P<y4>\d{4}))?"
         r"|(?P<rel>today|tomorrow|yesterday))")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _named(pattern: str, prefix: str) -> str:
    return re.sub(r"\(\?P<(\w+)>", lambda match: f"(?P<{prefix}{match.group(1)}>", pattern)


_A, _B = _named(_DATE, "a_"), _named(_DATE, "b_")
_SPAN = r"(?P<unit>days|weeks|months|sleeps)"
_DATE_FORMS = (
    ("weekday", re.compile(r"what\s+day\s+(?:of\s+the\s+week\s+)?(?:is|was|will\s+be|will\s+it\s+be\s+on|is\s+it\s+on)\s+"
                           + _A + r"(?:\s+on)?")),
    ("weekday", re.compile(r"what\s+day\s+(?:of\s+the\s+week\s+)?(?:does|did|will)\s+" + _A +
                           r"\s+(?:fall\s+on|land\s+on|fall|be\s+on|be)")),
    ("weekday", re.compile(r"(?:what\s+is|what's)\s+the\s+day\s+(?:of\s+the\s+week\s+)?(?:on|for)\s+" + _A)),
    ("is_weekday", re.compile(r"(?:is|was|will)\s+" + _A + r"\s+(?:be\s+)?(?:on\s+)?a\s+(?P<wd>" + "|".join(_WEEKDAYS)
                              + r")")),
    ("when", re.compile(r"when\s+is\s+(?P<a_hol>" + _HOLIDAY + r")")),
    ("until", re.compile(r"(?:how\s+many\s+)?" + _SPAN + r"\s+(?:are\s+)?(?:there\s+)?(?:left\s+)?(?:until|till|til|"
                         r"to|before)\s+" + _A)),
    ("until", re.compile(r"how\s+long\s+(?:is\s+it\s+|is\s+left\s+)?(?:until|till|til|to|before)\s+" + _A)),
    ("until", re.compile(r"how\s+many\s+(?P<unit>days|weeks)\s+away\s+is\s+" + _A)),
    ("since", re.compile(r"(?:how\s+many\s+)?(?P<unit>days|weeks)\s+(?:has\s+it\s+been\s+|have\s+passed\s+|is\s+it\s+)?"
                         r"since\s+" + _A)),
    ("since", re.compile(r"how\s+long\s+(?:has\s+it\s+been\s+|is\s+it\s+)?since\s+" + _A)),
    ("since", re.compile(r"how\s+many\s+(?P<unit>days|weeks)\s+ago\s+was\s+" + _A)),
    ("between", re.compile(r"(?:how\s+many\s+)?(?P<unit>days|weeks)\s+(?:are\s+there\s+)?between\s+" + _A + r"\s+and\s+"
                           + _B)),
    ("between", re.compile(r"(?:how\s+many\s+)?(?P<unit>days|weeks)\s+from\s+" + _A + r"\s+(?:to|until|till)\s+" + _B)),
)
_COUNT = r"(?:(?P<n>\d{1,4})|(?P<one>a|an|one))\s+(?P<span>days?|weeks?)"
_OFFSET_FORMS = (
    (1, re.compile(r"(?:what(?:'s|\s+is|\s+will\s+be)\s+the\s+date|what\s+date\s+(?:is\s+it|will\s+it\s+be|is)|"
                   r"what\s+day\s+(?:is\s+it|will\s+it\s+be|is)|what's\s+the\s+day|the\s+date)\s+in\s+" + _COUNT
                   + r"(?:\s+(?:from\s+(?:now|today)|time))?")),
    (1, re.compile(r"(?:what(?:'s|\s+is)\s+|what\s+(?:date|day)\s+is\s+|what\s+(?:date|day)\s+will\s+it\s+be\s+)?"
                   + _COUNT + r"\s+(?:from\s+(?:now|today)|after\s+today|from\s+today's\s+date)")),
    (1, re.compile(r"in\s+" + _COUNT + r"\s+what(?:'s|\s+is|\s+will\s+be)\s+the\s+date")),
    (-1, re.compile(r"(?:what\s+(?:was\s+the\s+date|date\s+was\s+it|day\s+was\s+it|was\s+the\s+day)\s+)?" + _COUNT
                    + r"\s+ago")),
)
_RELATIVE_FORMS = (
    (1, re.compile(r"what(?:'s|\s+is)\s+(?:the\s+date\s+)?tomorrow(?:'s\s+date)?|what\s+day\s+is\s+(?:it\s+)?tomorrow|"
                   r"what\s+(?:date|day)\s+will\s+it\s+be\s+tomorrow|tomorrow's\s+date|what\s+is\s+tomorrow's\s+date")),
    (-1, re.compile(r"what\s+was\s+(?:the\s+date\s+)?yesterday(?:'s\s+date)?|what\s+day\s+was\s+(?:it\s+)?yesterday|"
                    r"yesterday's\s+date|what\s+was\s+yesterday's\s+date|what\s+date\s+was\s+it\s+yesterday")),
)


def _ordinal(text: str) -> int:
    text = " ".join(text.split())
    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?", text)
    if match:
        return int(match.group(1))
    tens = 0
    if text.startswith(("20 ", "30 ")):
        tens, text = int(text[:2]), text[3:]
    return tens + _ORDINAL_WORDS.get(text, 99)


def _date_of(match: re.Match, prefix: str, today: date, direction: int) -> tuple[date, bool] | None:
    """The date a _DATE match names and whether a year was given; None when it is no real date. Without a year:
    the next one from today (``direction`` 1), or the last one (-1); a bare day of the month likewise."""
    groups = match.groupdict()
    get = lambda name: groups.get(prefix + name)  # noqa: E731
    try:
        if get("rel"):
            return today + timedelta(days={"today": 0, "tomorrow": 1, "yesterday": -1}[get("rel")]), True
        if get("iso"):
            return date.fromisoformat(get("iso")), True
        if get("hol"):
            month, day = HOLIDAYS[get("hol")]
            year = get("y4")
        elif get("m1") or get("m2"):
            month = _MONTHS[get("m1") or get("m2")]
            day = _ordinal(get("d1") or get("d2"))
            year = get("y1") or get("y2")
        else:
            day = _ordinal(get("d3") or get("d4"))
            return _day_of_month(day, today, direction), False
    except (ValueError, KeyError):
        return None
    if year:
        try:
            return date(int(year), month, day), True
        except ValueError:
            return None
    for step in range(0, 9):                       # Feb 29 waits for its year (at most 8 away)
        year_of = today.year + step * direction
        try:
            found = date(year_of, month, day)
        except ValueError:
            if not (month == 2 and day == 29):
                return None
            continue
        if (direction > 0 and found >= today) or (direction < 0 and found <= today):
            return found, False
    return None


def _day_of_month(day: int, today: date, direction: int) -> date | None:
    if not 1 <= day <= 31:
        raise ValueError("no such day")
    year, month = today.year, today.month
    for _step in range(3):
        try:
            found = date(year, month, day)
        except ValueError:
            found = None
        if found is not None and ((direction > 0 and found >= today) or (direction < 0 and found <= today)):
            return found
        month += direction
        if month == 13:
            year, month = year + 1, 1
        elif month == 0:
            year, month = year - 1, 12
    raise ValueError("no such day soon")


def _called(found: date, match: re.Match, prefix: str) -> str:
    """The date as said back: a holiday by its name, else its day and month (and year when not this one)."""
    holiday = match.group(prefix + "hol") if (prefix + "hol") in match.groupdict() else None
    if holiday:
        return _HOLIDAY_NAMES[HOLIDAYS[holiday]]
    return f"{found.day} {found:%B}"


def _span_words(days: int, unit: str) -> str:
    days = abs(days)
    if unit == "weeks" and days >= 7:
        weeks, rest = divmod(days, 7)
        text = f"{weeks} week{'s' if weeks != 1 else ''}"
        return text + (f" and {rest} day{'s' if rest != 1 else ''}" if rest else "")
    return f"{days} day{'s' if days != 1 else ''}"


def _dates(words: str, now: datetime, _flags: InstantFlags, address: str, _hour24: bool) -> InstantReply | None:
    today = now.date()
    for step, pattern in _RELATIVE_FORMS:
        if pattern.fullmatch(words):
            day = today + timedelta(days=step)
            verb = "Tomorrow is" if step > 0 else "Yesterday was"
            return InstantReply(KIND_DATE_MATHS, f"{verb} {_long_date(day)}.",
                                with_address(f"{verb} {day:%A}, {day.day} {day:%B}, {{sir}}.", address))
    for step, pattern in _OFFSET_FORMS:
        match = pattern.fullmatch(words)
        if not match:
            continue
        count = int(match.group("n")) if match.group("n") else 1
        days = count * (7 if match.group("span").startswith("week") else 1)
        if days > 36500:
            return None
        day = today + timedelta(days=days * step)
        span = f"{count} {match.group('span').rstrip('s')}{'s' if count != 1 else ''}"
        lead = f"In {span}" if step > 0 else f"{span[:1].upper()}{span[1:]} ago"
        verb = "it will be" if step > 0 else "it was"
        return InstantReply(KIND_DATE_MATHS, f"{lead} {verb} {_long_date(day)}.",
                            with_address(f"{lead} {verb} {day:%A}, {day.day} {day:%B}, {{sir}}.", address))
    for kind, pattern in _DATE_FORMS:
        match = pattern.fullmatch(words)
        if match:
            return _date_answer(kind, match, today, address)
    return None


def _next_on_or_after(day: date, ref: date) -> date | None:
    for year in (ref.year, ref.year + 1):
        try:
            found = date(year, day.month, day.day)
        except ValueError:
            continue
        if found >= ref:
            return found
    return None


def _last_on_or_before(day: date, ref: date) -> date | None:
    for year in (ref.year, ref.year - 1):
        try:
            found = date(year, day.month, day.day)
        except ValueError:
            continue
        if found <= ref:
            return found
    return None


def _date_answer(kind: str, match: re.Match, today: date, address: str) -> InstantReply | None:
    groups = match.groupdict()
    unit = (groups.get("unit") or "days").replace("sleeps", "days")
    if unit == "months":
        return None                                        # months have no one length: Claude
    if kind == "between":
        first, second = _date_of(match, "a_", today, 1), _date_of(match, "b_", today, 1)
        if first is None or second is None:
            return None
        (start, start_year), (end, end_year) = first, second
        if not end_year:                       # a date with no year: the first such day on or after the other one
            end = _next_on_or_after(end, start)
        elif not start_year:                   # ... or the last such day on or before it
            start = _last_on_or_before(start, end)
        if start is None or end is None:
            return None
        gap = abs((end - start).days)
        shown = [_called(day, match, prefix) + (f" {day.year}" if day.year != today.year else "")
                 for day, prefix in ((start, "a_"), (end, "b_"))]
        text = f"{_span_words(gap, unit)} between {shown[0]} and {shown[1]}."
        return InstantReply(KIND_DATE_MATHS, text[:1].upper() + text[1:],
                            with_address(f"{_span_words(gap, unit)}, {{sir}}.", address))
    past = kind in ("weekday", "is_weekday") and re.search(r"\b(?:was|did)\b", match.group(0)) is not None
    direction = -1 if kind == "since" or past else 1   # "what day was Christmas": the last one
    found = _date_of(match, "a_", today, direction)
    if found is None:
        return None
    day, _year_given = found
    called = _called(day, match, "a_")
    if kind in ("weekday", "is_weekday", "when"):
        verb = "is" if day >= today else "was"
        if day == today:
            verb = "is today,"
        text_day = f"{day:%A} {day.day} {day:%B}" + (f" {day.year}" if day.year != today.year else "")
        if kind == "is_weekday":
            asked = groups["wd"]
            yes = f"{day:%A}".casefold() == asked
            text = (f"Yes, {called} {verb} a {day:%A} ({text_day})." if yes else
                    f"No, {called} {verb} a {day:%A} ({text_day}).")
            speech = f"{'Yes' if yes else 'No'}, {{sir}}: it's a {day:%A}." if verb != "was" else \
                f"{'Yes' if yes else 'No'}, {{sir}}: it was a {day:%A}."
            return InstantReply(KIND_DATE_MATHS, text, with_address(speech, address))
        if kind == "when":
            gap = (day - today).days
            away = "today" if gap == 0 else f"in {_span_words(gap, 'days')}"
            return InstantReply(KIND_DATE_MATHS, f"{called} is on {text_day}, {away}.",
                                with_address(f"{called} is on {day:%A}, {day.day} {day:%B}, {{sir}}.", address))
        if day == today:
            return InstantReply(KIND_DATE_MATHS, f"{called} is today, {text_day}.",
                                with_address(f"{called} is today, {{sir}}.", address))
        lead = called if match.groupdict().get("a_hol") else f"{called}" + (
            f" {day.year}" if day.year != today.year else "")
        return InstantReply(KIND_DATE_MATHS, f"{lead} {verb} a {day:%A}.",
                            with_address(f"{lead} {verb} a {day:%A}, {{sir}}.", address))
    gap = (day - today).days
    if kind == "until":
        if gap == 0:
            return InstantReply(KIND_DATE_MATHS, f"{called} is today ({_long_date(day)}).",
                                with_address(f"{called} is today, {{sir}}!", address))
        if gap < 0:
            return None
        text = f"{_span_words(gap, unit)} until {called} ({_long_date(day)})."
        return InstantReply(KIND_DATE_MATHS, text[:1].upper() + text[1:],
                            with_address(f"{_span_words(gap, unit)} until {called}, {{sir}}.", address))
    if gap > 0:
        return None
    if gap == 0:
        return InstantReply(KIND_DATE_MATHS, f"{called} is today.", with_address(f"{called} is today, {{sir}}.",
                                                                               address))
    text = f"{_span_words(gap, unit)} since {called} ({_long_date(day)})."
    return InstantReply(KIND_DATE_MATHS, text[:1].upper() + text[1:],
                        with_address(f"{_span_words(gap, unit)} since {called}, {{sir}}.", address))


# --------------------------------------------------------------------------
# Timers
# --------------------------------------------------------------------------

_N = maths.NUMBER
_SECOND = r"(?:s|sec|secs|second|seconds)"
_MINUTE = r"(?:min|mins|minute|minutes)"
_HOUR = r"(?:h|hr|hrs|hour|hours)"
def _part(number: str, unit: str) -> str:
    return "(?P<" + number + ">" + _N + r")\s*-?\s*(?P<" + unit + ">" + _HOUR + "|" + _MINUTE + "|" + _SECOND + ")"


_DURATION = ("(?:"
             + _part("n1", "u1") + r"(?:\s+(?:and\s+)?" + _part("n2", "u2") + r")?"
             + r"(?:\s+(?:and\s+)?" + _part("n3", "u3") + r")?"
             + r"|(?P<half_a>an|a|1|one)\s+(?P<half_u>hour|minute)\s+and\s+a\s+half"
             + r"|(?P<half_n>\d{1,3})\s+and\s+a\s+half\s+(?P<half_u2>hours|minutes)"
             + r"|half\s+(?:an|a)\s+(?P<half_of>hour|minute)"
             + r"|(?:a\s+)?quarter\s+(?:of\s+)?(?:an\s+)?hour"
             + r"|(?:an|a)\s+(?P<one_u>hour|minute|second))")
_LABEL = r"(?:\s+(?:for|called|named|labelled|labeled|to)\s+(?P<label>[^\d].{0,60}))?"
_TIMER_SET_FORMS = (
    re.compile(r"(?:(?:set|start|make|create|put\s+on|begin|run)\s+(?:me\s+)?(?:a\s+|an\s+|one\s+)?(?:new\s+)?)?"
               r"(?:timer|countdown)\s+(?:for\s+|of\s+)?(?P<dur>" + _DURATION + r")" + _LABEL),
    re.compile(r"(?:(?:set|start|make|create|put\s+on|begin|run)\s+(?:me\s+)?)?(?:a\s+|an\s+|one\s+)?(?P<dur>"
               + _DURATION + r")\s+(?:timer|countdown)" + _LABEL),
    re.compile(r"(?:remind|tell|alert|ping)\s+me\s+in\s+(?P<dur>" + _DURATION + r")(?:\s+(?:to|that|about)\s+"
               r"(?P<label>[^\d].{0,60}))?"),
    re.compile(r"remind\s+me\s+(?:to|about)\s+(?P<label>[^\d].{0,60}?)\s+in\s+(?P<dur>" + _DURATION + r")"),
    re.compile(r"let\s+me\s+know\s+in\s+(?P<dur>" + _DURATION + r")"),
)
_TIMER_WORD = r"(?:timers?|countdowns?)"
_TIMER_CANCEL_RE = re.compile(r"(?:cancel|stop|end|clear|delete|remove|kill|turn\s+off|dismiss)\s+"
                              r"(?:(?P<every>all)\s+(?:of\s+)?)?(?:the\s+|my\s+|that\s+|this\s+|those\s+|these\s+)?"
                              r"(?:(?P<dur>" + _DURATION + r")\s+)?" + _TIMER_WORD)
_TIMER_LEFT_RE = re.compile(
    r"how\s+(?:much\s+time|long)\s+(?:is\s+|'s\s+)?(?:there\s+)?(?:left|remaining|to\s+go)(?:\s+on\s+(?:the|my)\s+"
    + _TIMER_WORD + r")?"
    r"|(?:what(?:'s|\s+is)\s+)?(?:the\s+)?time\s+(?:left|remaining)(?:\s+on\s+(?:the|my)\s+" + _TIMER_WORD + r")?"
    r"|what(?:'s|\s+is)\s+left\s+on\s+(?:the|my)\s+" + _TIMER_WORD +
    r"|when\s+(?:does|will|do)\s+(?:the|my)\s+" + _TIMER_WORD + r"\s+(?:end|go\s+off|finish|ring|be\s+done)"
    r"|(?:check|show)(?:\s+me)?\s+(?:the|my)\s+" + _TIMER_WORD +
    r"|(?:do\s+i\s+have|are\s+there|is\s+there)\s+(?:any|a)\s+" + _TIMER_WORD + r"(?:\s+(?:running|set|on|going))?"
    r"|how\s+long\s+until\s+(?:the|my)\s+" + _TIMER_WORD + r"(?:\s+(?:ends|goes\s+off|is\s+done|rings))?"
    r"|(?:my\s+|the\s+)?" + _TIMER_WORD + r"\s+status|status\s+of\s+(?:the|my)\s+" + _TIMER_WORD)


def _seconds_of(match: re.Match) -> int | None:
    """The duration a _DURATION match names in whole seconds (None: not whole seconds)."""
    groups = match.groupdict()
    per = lambda unit: 3600 if re.fullmatch(_HOUR, unit) else 60 if re.fullmatch(_MINUTE, unit) else 1  # noqa: E731
    total = Fraction(0)
    if groups.get("half_a"):
        total = Fraction(3, 2) * per(groups["half_u"])
    elif groups.get("half_n"):
        total = (int(groups["half_n"]) + Fraction(1, 2)) * per(groups["half_u2"])
    elif groups.get("half_of"):
        total = Fraction(1, 2) * per(groups["half_of"])
    elif groups.get("one_u"):
        total = Fraction(per(groups["one_u"]))
    elif groups.get("n1"):
        units = []
        for number, unit in (("n1", "u1"), ("n2", "u2"), ("n3", "u3")):
            if groups.get(number):
                size = per(groups[unit])
                if size in units:
                    return None                          # "5 minutes 3 minutes"
                units.append(size)
                total += Fraction(groups[number].replace(",", "")) * size
        if units != sorted(units, reverse=True):
            return None                                  # "30 seconds 1 minute"
    else:
        total = Fraction(15 * 60)                        # a quarter of an hour
    if total.denominator != 1:
        return None
    return int(total)


def duration_words(seconds: int) -> str:
    """"1 hour 30 minutes", "90 seconds" is "1 minute 30 seconds", "10 minutes"."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{value} {name}{'s' if value != 1 else ''}" for value, name in
             ((hours, "hour"), (minutes, "minute"), (secs, "second")) if value]
    return " ".join(parts) or "0 seconds"


def _timer(words: str, _now: datetime, flags: InstantFlags, address: str, _hour24: bool) -> Answer:
    if not flags.timers:
        return None
    match = _TIMER_CANCEL_RE.fullmatch(words)
    if match:
        seconds = None
        if match.group("dur"):
            inner = re.fullmatch(_DURATION, match.group("dur"))
            seconds = _seconds_of(inner) if inner else None
            if seconds is None:
                return None
        return TimerAsk(TIMER_CANCEL, seconds=seconds or 0, every=bool(match.group("every")))
    if _TIMER_LEFT_RE.fullmatch(words):
        return TimerAsk(TIMER_LEFT, bare=re.search(r"\b(?:timers?|countdowns?)\b", words) is None)
    for pattern in _TIMER_SET_FORMS:
        match = pattern.fullmatch(words)
        if not match:
            continue
        inner = re.fullmatch(_DURATION, match.group("dur"))
        seconds = _seconds_of(inner) if inner else None
        if seconds is None:
            return None
        if not TIMER_MIN_S <= seconds <= TIMER_MAX_S:
            return InstantReply(KIND_TIMER, "Timers run from 10 seconds to 4 hours; nothing was set.",
                                with_address("Timers run from 10 seconds to 4 hours, {sir}.", address))
        label = " ".join((match.groupdict().get("label") or "").split())[:LABEL_MAX].strip(" .,!?")
        return TimerAsk(TIMER_SET, seconds=seconds, label=label)
    return None


# --------------------------------------------------------------------------
# Clock times (round 2): reminders and "am I free at"
# --------------------------------------------------------------------------

_AP = r"a\.?\s?m\.?|p\.?\s?m\.?"
_CLOCK = (r"(?:(?P<noon>noon|midday|12\s+noon)"
          r"|(?P<half>half\s+past|(?:a\s+)?quarter\s+past|(?:a\s+)?quarter\s+to)\s+(?P<hh>\d{1,2})(?:\s*(?P<ap2>" + _AP
          + r"))?"
          r"|(?P<h>\d{1,2})(?:(?::|\.|\s+)(?P<m>\d{2}))?(?:\s*(?P<ap>" + _AP + r"))?(?:\s+o'?\s?clock)?)")
_PARTS = r"morning|afternoon|evening|night"
_PART_OF = {"tonight": "evening"}
# A bare hour after "tomorrow" is read only when one of its two readings falls in these hours (7 AM to 10:59 PM).
_WAKING = range(7, 23)


@dataclass(frozen=True)
class _Clock:
    """A clock time as said: ``hour`` 0-23 with ``explicit`` (am/pm, noon, a 24-hour time), else ``hour`` 1-12
    and ``minute`` with no half of the day."""

    hour: int
    minute: int
    explicit: bool

    def readings(self) -> tuple[tuple[int, int], ...]:
        """The (hour 0-23, minute) it can mean: one, or a bare hour's two (AM first)."""
        if self.explicit:
            return ((self.hour, self.minute),)
        return ((self.hour % 12, self.minute), (self.hour % 12 + 12, self.minute))


def _clock_of(match: re.Match, prefix: str = "") -> _Clock | None:
    """The _CLOCK match (its groups under ``prefix``) as a _Clock; None when it is no real time ("13 pm", "5:75")."""
    groups = match.groupdict()
    get = lambda name: groups.get(prefix + name)  # noqa: E731
    if get("noon"):
        return _Clock(12, 0, True)
    minute = 0
    if get("half"):
        hour, said = int(get("hh")), " ".join(get("half").split())
        minute = 30 if said.startswith("half") else 15 if said.endswith("past") else 45
        if said.endswith(" to"):
            hour = hour - 1 if hour > 1 else 12
        meridiem = get("ap2")
        digits = get("hh")
    else:
        digits = get("h") or ""
        if not digits:
            return None
        hour = int(digits)
        minute = int(get("m")) if get("m") else 0
        meridiem = get("ap")
    if not 0 <= minute <= 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        return _Clock(hour % 12 + (12 if meridiem.startswith("p") else 0), minute, True)
    if 13 <= hour <= 23 or (get("m") and (hour == 0 or digits.startswith("0"))):
        return _Clock(hour, minute, True)            # "17:30", "09:00": the 24-hour clock
    if 1 <= hour <= 12:
        return _Clock(hour, minute, False)
    return None


def _uses_system_zone(now: datetime) -> bool:
    """``now`` came from datetime.now().astimezone(): a fixed offset that is this PC's own at that moment."""
    tz = now.tzinfo
    if not isinstance(tz, timezone):
        return False
    try:
        return now.replace(tzinfo=None).astimezone().utcoffset() == now.utcoffset()
    except (OverflowError, OSError, ValueError):
        return False


def local_at(day: date, hour: int, minute: int, now: datetime) -> datetime:
    """``day`` at hour:minute on the wall clock of ``now``'s zone (aware). With this PC's own zone the system's
    rules give the offset, so "tomorrow at 9" is right across a change of the clocks; any other zone as it is."""
    naive = datetime(day.year, day.month, day.day, hour, minute)
    if now.tzinfo is not None and not isinstance(now.tzinfo, timezone):
        return naive.replace(tzinfo=now.tzinfo)                  # a real zone (ZoneInfo): its own rules
    if _uses_system_zone(now):
        try:
            return naive.astimezone()
        except (OverflowError, OSError, ValueError):
            pass
    return naive.replace(tzinfo=now.tzinfo or timezone.utc)


def wall(moment: datetime, now: datetime) -> datetime:
    """``moment`` on the wall clock ``now`` is on (see local_at)."""
    if now.tzinfo is not None and not isinstance(now.tzinfo, timezone):
        return moment.astimezone(now.tzinfo)
    if _uses_system_zone(now):
        try:
            return moment.astimezone()
        except (OverflowError, OSError, ValueError):
            pass
    return moment.astimezone(now.tzinfo) if now.tzinfo is not None else moment


def _with_part(clock: _Clock, part: str) -> _Clock | None:
    """The clock in ``part`` of the day ("morning": AM, "afternoon": 12-6 PM, "evening": 5-11 PM, "night": 7-11 PM);
    None when they don't fit ("9 am tonight", "12 at night")."""
    spans = {"morning": (0, 12), "afternoon": (12, 19), "evening": (17, 24), "night": (19, 24)}
    first, last = spans[part]
    for hour, minute in clock.readings():
        if first <= hour < last and not (part == "morning" and hour == 0):
            return _Clock(hour, minute, True)
    return None


def _resolve(clock: _Clock, day_word: str, part: str, now: datetime, *, inclusive: bool) -> datetime | None:
    """When the owner means (aware, local): see the module docs. ``day_word``: "", "today", "tomorrow" or "tonight";
    ``inclusive``: a time in the minute it is now counts as still to come (a question; a reminder needs a later one).
    None when it can't be read for sure."""
    part = part or _PART_OF.get(day_word, "")
    if part:
        clock = _with_part(clock, part)
        if clock is None:
            return None
    floor = now.replace(second=0, microsecond=0)
    ahead = (lambda moment: moment >= floor) if inclusive else (lambda moment: moment > now)  # noqa: E731
    today = now.date()
    tomorrow = today + timedelta(days=1)
    if day_word == "tomorrow":
        readings = clock.readings()
        if not clock.explicit:
            readings = tuple(reading for reading in readings if reading[0] in _WAKING)
            if len(readings) != 1:
                return None                              # "tomorrow at 7": 7 AM or 7 PM, not for sure
        return local_at(tomorrow, *readings[0], now)
    days = (today,) if day_word in ("today", "tonight") else (today, tomorrow)
    for day in days:
        for hour, minute in clock.readings():
            moment = local_at(day, hour, minute, now)
            if ahead(moment):
                return moment
    return None


def spoken_clock(moment: datetime) -> str:
    """"5 PM", "5:30 PM", "12 PM" - a time as said (the 12-hour clock, no ":00")."""
    hour = moment.hour % 12 or 12
    meridiem = "AM" if moment.hour < 12 else "PM"
    return f"{hour} {meridiem}" if moment.minute == 0 else f"{hour}:{moment.minute:02d} {meridiem}"


# --------------------------------------------------------------------------
# Reminders (round 2)
# --------------------------------------------------------------------------

_WHEN_AT = ("(?:(?P<d1>today|tomorrow|tonight)\\s+)?(?:(?:this\\s+|in\\s+the\\s+)?(?P<p1>morning|afternoon|evening)\\s+)?"
            "(?:at|for|by|around)\\s+" + _CLOCK + "(?:\\s+(?P<d2>today|tomorrow|tonight))?"
            "(?:\\s+(?:this\\s+|in\\s+the\\s+|at\\s+)?(?P<p2>" + _PARTS + "))?")
_R_LABEL = r"(?P<label>[a-zà-ÿ'\"].{0,60}?)"
_REMINDER_SET_FORMS = (
    re.compile(r"remind\s+me\s+" + _WHEN_AT + r"(?:\s+(?:to|about|that)\s+" + _R_LABEL + r")?"),
    re.compile(r"remind\s+me\s+(?:(?P<d0>today|tomorrow|tonight)\s+)?(?:to|about|that)\s+" + _R_LABEL + r"\s+"
               + _WHEN_AT),
    re.compile(r"(?:(?:set|create|add|make|schedule|put\s+in)\s+(?:me\s+)?)?(?:a\s+|an\s+)?reminder\s+" + _WHEN_AT
               + r"(?:\s+(?:to|about|that|for)\s+" + _R_LABEL + r")?"),
    re.compile(r"(?:(?:set|create|add|make|schedule|put\s+in)\s+(?:me\s+)?)?(?:a\s+|an\s+)?reminder\s+(?:to|about|for)\s+"
               + _R_LABEL + r"\s+" + _WHEN_AT),
)
# A label that names another day, a repeat or another time ("call mum on friday", "stretch every day"): Claude's.
_FAR_LABEL = re.compile(
    r"\b(?:(?:mon|tues|wednes|thurs|fri|satur|sun)days?|weekends?|weeks?|months?|years?|every|each|daily|weekly|"
    r"monthly|yearly|annually|next|tomorrow|tonight|today|morning|afternoon|evening|night|noon|midnight|o'?clock|"
    r"january|february|march|april|june|july|august|september|october|november|december)\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\b|\b(?:a\.?m|p\.?m)\b|\b(?:at|on|by|in|until|till|from)\s+\d|\bmay\s+\d|\d\s+may\b")
_REMINDER_WORD = r"reminders?"
_REMINDER_LIST_RE = re.compile(
    r"(?:what|which)\s+reminders\s+(?:do\s+i\s+have|have\s+i\s+got|are\s+(?:there|set|on))(?:\s+(?:set|today|left))?"
    r"|(?:do\s+i\s+have|have\s+i\s+got|are\s+there|is\s+there)\s+(?:any\s+|a\s+)?reminders?(?:\s+(?:set|today|left|on))?"
    r"|(?:(?:what\s+are|show(?:\s+me)?|list|check|read(?:\s+me)?)\s+)?(?:all\s+)?(?:my|the)\s+reminders"
    r"|(?:what(?:'s|\s+is)|when(?:'s|\s+is)|whats|whens)\s+my\s+(?:next\s+)?reminder|(?:my\s+)?reminders")
_REMINDER_CANCEL_RE = re.compile(
    r"(?:cancel|delete|remove|clear|drop|forget|turn\s+off|dismiss)\s+(?:(?P<every>all)\s+(?:of\s+)?)?"
    r"(?:the\s+|my\s+|that\s+|this\s+|those\s+|these\s+)?(?:(?P<pre>" + _named(_CLOCK, "a_") + r")\s+)?" + _REMINDER_WORD
    + r"(?:\s+(?:at|for)\s+(?P<post>" + _named(_CLOCK, "b_") + r"))?(?:\s+(?P<day>today|tomorrow))?")


def _reminder(words: str, now: datetime, flags: InstantFlags, address: str, hour24: bool) -> Answer:
    if not flags.timers or "remind" not in words:
        return None
    match = _REMINDER_CANCEL_RE.fullmatch(words)
    if match:
        prefix = "a_" if match.group("pre") else "b_" if match.group("post") else ""
        clock = _clock_of(match, prefix) if prefix else None
        if prefix and clock is None:
            return None
        day = {"today": 0, "tomorrow": 1}.get(match.group("day") or "", -1)
        return ReminderAsk(REMINDER_CANCEL, every=bool(match.group("every")),
                           clocks=clock.readings() if clock is not None else (), day=day)
    if _REMINDER_LIST_RE.fullmatch(words):
        return ReminderAsk(REMINDER_LIST)
    for pattern in _REMINDER_SET_FORMS:
        match = pattern.fullmatch(words)
        if not match:
            continue
        groups = match.groupdict()
        clock = _clock_of(match)
        days = {groups.get(name) for name in ("d0", "d1", "d2")} - {None}
        parts = {groups.get(name) for name in ("p1", "p2")} - {None}
        if clock is None or len(days) > 1 or len(parts) > 1:
            return None                                  # "today at 5 tomorrow": not for sure
        label = " ".join((groups.get("label") or "").split()).strip(" .,!?\"'")
        if label and _FAR_LABEL.search(label):
            return None                                  # another day, a repeat or another time: Claude's
        day_word = next(iter(days), "")
        part = next(iter(parts), "")
        if day_word == "tonight" and part in ("morning", "afternoon"):
            return None
        at = _resolve(clock, day_word, part, now, inclusive=False)
        if at is None:
            if day_word in ("today", "tonight") and clock.explicit:
                said = local_at(now.date(), clock.hour, clock.minute, now)
                return InstantReply(KIND_REMINDER, f"It's already past {format_time(said, hour24=hour24)} today; "
                                    f"nothing was set.", with_address(f"That time has passed today, {{sir}}.",
                                                                     address))
            return None
        if (at - now).total_seconds() > REMINDER_MAX_S:
            return None
        return ReminderAsk(REMINDER_SET, at=at, label=label[:LABEL_MAX].strip())
    return None


# --------------------------------------------------------------------------
# The calendar (round 2)
# --------------------------------------------------------------------------

_CAL_NOUN = r"(?P<noun>meetings?|events?|appointments?|calls?)"
_CAL_SPOT = r"(?:calendar|schedule|agenda|diary)"
_CAL_DAY = r"(?P<day>today|tomorrow)"
_WHAT = r"(?:what(?:'s|\s+is)|whats)"
_CAL_FORMS = (
    (CAL_NEXT, re.compile(r"(?:" + _WHAT + r"|when(?:'s|\s+is)|whens|what\s+time\s+is)\s+my\s+next\s+" + _CAL_NOUN
                          + r"(?:\s+today)?")),
    (CAL_NEXT, re.compile(_WHAT + r"\s+next\s+(?:on|in)\s+my\s+" + _CAL_SPOT + r"(?:\s+today)?")),
    (CAL_NEXT, re.compile(r"when\s+(?:do\s+i\s+have|is)\s+my\s+next\s+" + _CAL_NOUN)),
    (CAL_NEXT, re.compile(r"(?:my\s+)?next\s+" + _CAL_NOUN)),
    (CAL_MORE, re.compile(r"(?:do\s+i\s+have|have\s+i\s+got|are\s+there|is\s+there)\s+(?:any\s+)?(?:more|other)\s+"
                          + _CAL_NOUN + r"(?:\s+left)?(?:\s+today)?")),
    (CAL_MORE, re.compile(r"any\s+more\s+" + _CAL_NOUN + r"(?:\s+left)?(?:\s+today)?")),
    (CAL_DAY, re.compile(_WHAT + r"\s+on\s+my\s+" + _CAL_SPOT + r"(?:\s+(?:for\s+)?" + _CAL_DAY + r")?")),
    (CAL_DAY, re.compile(_WHAT + r"\s+on\s+" + _CAL_DAY + r"'?s\s+" + _CAL_SPOT)),
    (CAL_DAY, re.compile(_WHAT + r"\s+(?:in\s+)?my\s+" + _CAL_SPOT + r"\s+(?:look\s+like\s+)?(?:for\s+)?" + _CAL_DAY)),
    (CAL_DAY, re.compile(r"what\s+(?:do\s+i\s+have|have\s+i\s+got)\s+(?:on\s+)?(?:(?:for\s+)?" + _CAL_DAY + r"|on\s+my\s+"
                         + _CAL_SPOT + r"(?:\s+(?:for\s+)?(?P<day2>today|tomorrow))?)")),
    (CAL_DAY, re.compile(r"what\s+does\s+my\s+(?:day|" + _CAL_SPOT + r")\s+look\s+like(?:\s+" + _CAL_DAY + r")?")),
    (CAL_DAY, re.compile(r"what\s+does\s+" + _CAL_DAY + r"\s+look\s+like")),
    (CAL_DAY, re.compile(r"how\s+does\s+my\s+(?:day|" + _CAL_SPOT + r")\s+look(?:\s+" + _CAL_DAY + r")?")),
    (CAL_DAY, re.compile(r"what\s+" + _CAL_NOUN + r"\s+(?:do\s+i\s+have|have\s+i\s+got|are\s+on\s+my\s+" + _CAL_SPOT
                         + r")(?:\s+" + _CAL_DAY + r")?")),
    (CAL_DAY, re.compile(r"(?:do\s+i\s+have|have\s+i\s+got)\s+(?:anything|any\s+" + _CAL_NOUN + r")\s+(?:on\s+)?"
                         + _CAL_DAY)),
    (CAL_DAY, re.compile(r"(?:show|read|tell)\s+(?:me\s+)?my\s+" + _CAL_SPOT + r"(?:\s+(?:for\s+)?" + _CAL_DAY + r")?")),
    (CAL_DAY, re.compile(r"(?:my\s+)?" + _CAL_SPOT + r"\s+(?:for\s+)?" + _CAL_DAY)),
    (CAL_COUNT, re.compile(r"how\s+many\s+" + _CAL_NOUN + r"\s+(?:do\s+i\s+have|have\s+i\s+got|are\s+there|are\s+on\s+my\s+"
                           + _CAL_SPOT + r")(?:\s+(?:on\s+)?" + _CAL_DAY + r")?")),
    (CAL_COUNT, re.compile(r"how\s+many\s+" + _CAL_NOUN + r"\s+(?:on\s+)?" + _CAL_DAY)),
    (CAL_COUNT, re.compile(r"how\s+busy\s+(?:am\s+i|is\s+my\s+(?:day|" + _CAL_SPOT + r"))(?:\s+" + _CAL_DAY + r")?")),
)
_CAL_ONE = r"(?:meeting|event|appointment|call)"
_CAL_FREE_RE = re.compile(
    r"(?:(?P<free>am\s+i\s+(?:free|available)|is\s+my\s+" + _CAL_SPOT + r"\s+(?:free|clear|open))"
    r"|(?P<busy>am\s+i\s+(?:busy|booked)|(?:do\s+i\s+have|have\s+i\s+got|is\s+there)\s+(?:anything|something|an?\s+"
    + _CAL_ONE + r"|any\s+" + _CAL_NOUN + r")(?:\s+on)?(?:\s+my\s+" + _CAL_SPOT + r")?))"
    r"\s+(?:(?P<d1>today|tomorrow|tonight)\s+)?(?:(?:this\s+|in\s+the\s+)?(?P<p1>morning|afternoon|evening)\s+)?"
    r"(?:at|around|for)\s+" + _CLOCK + r"(?:\s+(?P<d2>today|tomorrow|tonight))?"
    r"(?:\s+(?:this\s+|in\s+the\s+|at\s+)?(?P<p2>" + _PARTS + r"))?")


def _noun(found: str | None) -> str:
    return (found or "meeting").rstrip("s") or "meeting"


def _calendar(words: str, now: datetime, flags: InstantFlags, _address: str, _hour24: bool) -> CalendarAsk | None:
    if not flags.calendar:
        return None
    match = _CAL_FREE_RE.fullmatch(words)
    if match:
        groups = match.groupdict()
        clock = _clock_of(match)
        days = {groups.get(name) for name in ("d1", "d2")} - {None}
        parts = {groups.get(name) for name in ("p1", "p2")} - {None}
        if clock is None or len(days) > 1 or len(parts) > 1:
            return None
        day_word, part = next(iter(days), ""), next(iter(parts), "")
        if day_word == "tonight" and part in ("morning", "afternoon"):
            return None
        at = _resolve(clock, day_word, part, now, inclusive=True)
        if at is None:
            return None
        return CalendarAsk(CAL_FREE, day=(at.date() - now.date()).days, at=at, busy=bool(groups.get("busy")),
                           noun=_noun(groups.get("noun")))
    for action, pattern in _CAL_FORMS:
        match = pattern.fullmatch(words)
        if match:
            groups = match.groupdict()
            day = 1 if "tomorrow" in (groups.get("day"), groups.get("day2")) else 0
            noun = _noun(groups.get("noun")) if action != CAL_DAY else "event"
            if action == CAL_NEXT and "next on" in words:
                noun = "event"
            return CalendarAsk(action, day=day, noun=noun)
    return None


# --------------------------------------------------------------------------
# The weather's town (round 2)
# --------------------------------------------------------------------------

_TOWN_NAME = r"(?P<town>[a-zà-ÿ][a-zà-ÿ .'\-]{0,38}[a-zà-ÿ.](?:,\s*[a-zà-ÿ][a-zà-ÿ .'\-]{0,30}[a-zà-ÿ.])?)"
_TOWN_WORD = r"(?:weather\s+)?(?:town|city|location|home\s+town|hometown)"
_TOWN_SET_FORMS = (
    re.compile(r"my\s+(?:home\s+)?" + _TOWN_WORD + r"\s+is\s+(?:now\s+)?" + _TOWN_NAME),
    re.compile(r"i\s+(?:live|am\s+based|'m\s+based)\s+in\s+" + _TOWN_NAME),
    re.compile(r"i(?:\s+have|'ve)?\s+moved\s+to\s+" + _TOWN_NAME),
    re.compile(r"(?:set|change|make|update|switch)\s+my\s+" + _TOWN_WORD + r"\s+to\s+" + _TOWN_NAME),
    re.compile(r"(?:use|make)\s+" + _TOWN_NAME + r"\s+(?:for\s+(?:the\s+|my\s+)?weather|my\s+" + _TOWN_WORD + r")"),
)
_TOWN_SAY_RE = re.compile(
    _WHAT + r"\s+my\s+" + _TOWN_WORD + r"|where\s+do\s+i\s+live|(?:what|which)\s+(?:town|city)\s+(?:do\s+you\s+(?:have|use)"
    r"(?:\s+for\s+(?:me|the\s+weather))?|is\s+(?:the|my)\s+weather\s+for|am\s+i\s+in\s+for\s+the\s+weather)")
_TOWN_FORGET_RE = re.compile(r"(?:forget|clear|remove|delete|reset|unset|drop)\s+my\s+" + _TOWN_WORD)
_NOT_A_TOWN = re.compile(r"(?:a|an|my|your|his|her|our|their|this|that|here|there|it)\b")


def _town(words: str) -> TownAsk | None:
    if _TOWN_FORGET_RE.fullmatch(words):
        return TownAsk(TOWN_FORGET)
    if _TOWN_SAY_RE.fullmatch(words):
        return TownAsk(TOWN_SAY)
    for pattern in _TOWN_SET_FORMS:
        match = pattern.fullmatch(words)
        if not match:
            continue
        town = " ".join(match.group("town").split()).strip(" .")
        if not town or town in _NOT_TOWNS or _TIME_WORDS.search(town) or _NOT_A_TOWN.match(town):
            return None
        return TownAsk(TOWN_SET, town=town)
    return None


# --------------------------------------------------------------------------
# The network's answers: weather and currencies
# --------------------------------------------------------------------------

_TOWN = r"(?P<town>[a-zà-ÿ][a-zà-ÿ .'\-]{0,38}[a-zà-ÿ.](?:,\s*[a-zà-ÿ][a-zà-ÿ .'\-]{0,30}[a-zà-ÿ.])?)"
_WHEN = r"(?P<when>today|tonight|now|right\s+now|tomorrow|this\s+morning|this\s+afternoon|this\s+evening)"
_IN_TOWN = r"(?:\s+(?:in|for|at)\s+" + _TOWN + r"|\s+(?:here|outside|around\s+here))?"
_WEATHER_FORMS = (
    re.compile(r"(?:what(?:'s|\s+is)|how(?:'s|\s+is)|whats|hows)\s+the\s+weather(?:\s+like)?(?:\s+" + _named(_WHEN, "a_")
               + r")?" + _IN_TOWN + r"(?:\s+" + _WHEN + r")?"),
    re.compile(r"(?:the\s+)?weather(?:\s+forecast)?" + _IN_TOWN + r"(?:\s+" + _WHEN + r")?"),
    re.compile(r"(?:what(?:'s|\s+is)\s+)?(?:the\s+)?(?:weather\s+)?forecast" + _IN_TOWN + r"(?:\s+" + _WHEN + r")?"),
    re.compile(r"(?:what(?:'s|\s+is)\s+)?the\s+weather\s+(?:going\s+to\s+be\s+|gonna\s+be\s+)?like\s+"
               + _named(_WHEN, "a_") + _IN_TOWN),
    re.compile(r"(?:is|will)\s+it\s+(?:going\s+to\s+|gonna\s+)?(?P<rain>rain|snow)(?:\s+" + _named(_WHEN, "a_") + r")?"
               + _IN_TOWN + r"(?:\s+" + _WHEN + r")?"),
    re.compile(r"(?:is\s+it|will\s+it\s+be)\s+(?P<rain>raining|snowing)(?:\s+" + _named(_WHEN, "a_") + r")?"
               + _IN_TOWN + r"(?:\s+" + _WHEN + r")?"),
    re.compile(r"do\s+i\s+need\s+an?\s+(?P<rain>umbrella)(?:\s+" + _named(_WHEN, "a_") + r")?" + _IN_TOWN
               + r"(?:\s+" + _WHEN + r")?"),
    re.compile(r"(?:what(?:'s|\s+is)\s+)?the\s+temperature(?:\s+outside)?" + _IN_TOWN + r"(?:\s+" + _WHEN + r")?"),
    re.compile(r"how\s+(?:hot|cold|warm)\s+is\s+it(?:\s+outside)?" + _IN_TOWN + r"(?:\s+" + _WHEN + r")?"),
)
_NOT_TOWNS = {"here", "my area", "my town", "my city", "outside", "today", "tomorrow", "tonight", "now", "right now",
              "the morning", "the afternoon", "the evening", "the weekend", "this weekend", "the week", "this week",
              "next week", "my location", "home", "work", "the office", "the moment"}
# A word of time in what would be the town ("london tomorrow morning", "may", "spring"): not a town for sure.
_TIME_WORDS = re.compile(r"\b(?:today|tonight|tomorrow|now|morning|afternoon|evening|night|weekend|week|month|year|"
                         r"spring|summer|autumn|fall|winter|"
                         + "|".join(sorted({name for name in _MONTHS if len(name) > 3} | {"may"})) + r")\b")


def _weather(words: str, flags: InstantFlags) -> NetAsk | None:
    for pattern in _WEATHER_FORMS:
        match = pattern.fullmatch(words)
        if not match:
            continue
        groups = match.groupdict()
        town = " ".join((groups.get("town") or "").split()).strip(" .")
        when = groups.get("when") or groups.get("a_when") or "now"
        trailing = re.fullmatch(r"(.+?)\s+" + _named(_WHEN, "t_"), town)
        if trailing:                                      # "in paris tomorrow": the town is "paris"
            if groups.get("when") or groups.get("a_when"):
                return None
            town, when = trailing.group(1), trailing.group("t_when")
        given = bool(town) and town not in _NOT_TOWNS
        if town in ("here", "my area", "my town", "my city", "home", "my location"):
            town = ""
        if town and town in _NOT_TOWNS:
            return None
        if not town:
            town = " ".join(str(flags.town or "").split())
            given = False
            if not town:
                return None                               # no town: Claude, as before
        elif _TIME_WORDS.search(town):
            return None
        if when not in ("now", "right now", "today", "tomorrow"):
            return None                                   # tonight, this morning ...: not what the answer gives
        rain = {"raining": "now_rain", "snowing": "now_snow", "umbrella": "rain"}.get(groups.get("rain") or "",
                                                                                      groups.get("rain") or "")
        if rain.startswith("now_") and when == "tomorrow":
            rain = rain[4:]                               # "will it be raining tomorrow": the day's chance
        return NetAsk(KIND_WEATHER, town=town, when="tomorrow" if when == "tomorrow" else "now", rain=rain,
                      town_given=given)
    return None


# ISO code -> (singular, plural, the words read as it). Only the European Central Bank's reference currencies
# (Frankfurter's list); a name shared by several of them ("peso", "krone", "krona", "dollar" with no country but the
# US one) is read only with its country.
CURRENCIES = {
    "USD": ("US dollar", "US dollars", "dollar|dollars|us dollar|us dollars|usd|bucks|american dollars"),
    "EUR": ("euro", "euros", "euro|euros|eur"),
    "GBP": ("British pound", "British pounds", "pound|pounds|pound sterling|pounds sterling|british pound|"
                                                "british pounds|sterling|gbp|quid"),
    "JPY": ("Japanese yen", "Japanese yen", "yen|japanese yen|jpy"),
    "CNY": ("Chinese yuan", "Chinese yuan", "yuan|chinese yuan|renminbi|rmb|cny"),
    "INR": ("Indian rupee", "Indian rupees", "rupee|rupees|indian rupee|indian rupees|inr"),
    "CAD": ("Canadian dollar", "Canadian dollars", "canadian dollar|canadian dollars|cad"),
    "AUD": ("Australian dollar", "Australian dollars", "australian dollar|australian dollars|aud"),
    "NZD": ("New Zealand dollar", "New Zealand dollars", "new zealand dollar|new zealand dollars|nzd"),
    "SGD": ("Singapore dollar", "Singapore dollars", "singapore dollar|singapore dollars|sgd"),
    "HKD": ("Hong Kong dollar", "Hong Kong dollars", "hong kong dollar|hong kong dollars|hkd"),
    "CHF": ("Swiss franc", "Swiss francs", "swiss franc|swiss francs|franc|francs|chf"),
    "SEK": ("Swedish krona", "Swedish kronor", "swedish krona|swedish kronor|sek"),
    "NOK": ("Norwegian krone", "Norwegian kroner", "norwegian krone|norwegian kroner|nok"),
    "DKK": ("Danish krone", "Danish kroner", "danish krone|danish kroner|dkk"),
    "ISK": ("Icelandic króna", "Icelandic krónur", "icelandic krona|icelandic kronur|isk"),
    "MXN": ("Mexican peso", "Mexican pesos", "mexican peso|mexican pesos|mxn"),
    "PHP": ("Philippine peso", "Philippine pesos", "philippine peso|philippine pesos|php"),
    "BRL": ("Brazilian real", "Brazilian reais", "brazilian real|brazilian reais|real|reais|brl"),
    "ZAR": ("South African rand", "South African rand", "rand|south african rand|zar"),
    "TRY": ("Turkish lira", "Turkish lira", "turkish lira|lira|try"),
    "PLN": ("Polish zloty", "Polish zloty", "zloty|zlotys|polish zloty|pln"),
    "HUF": ("Hungarian forint", "Hungarian forint", "forint|forints|hungarian forint|huf"),
    "CZK": ("Czech koruna", "Czech korunas", "koruna|czech koruna|czech crowns|czk"),
    "RON": ("Romanian leu", "Romanian lei", "leu|lei|romanian leu|romanian lei|ron"),
    "BGN": ("Bulgarian lev", "Bulgarian leva", "lev|leva|bulgarian lev|bgn"),
    "ILS": ("Israeli shekel", "Israeli shekels", "shekel|shekels|israeli shekel|israeli shekels|ils|nis"),
    "THB": ("Thai baht", "Thai baht", "baht|thai baht|thb"),
    "MYR": ("Malaysian ringgit", "Malaysian ringgit", "ringgit|malaysian ringgit|myr"),
    "IDR": ("Indonesian rupiah", "Indonesian rupiah", "rupiah|indonesian rupiah|idr"),
    "KRW": ("South Korean won", "South Korean won", "won|korean won|south korean won|krw"),
}
_CURRENCY_NAMES = {name: code for code, (_one, _many, names) in CURRENCIES.items() for name in names.split("|")}
_CUR = "(?:" + "|".join(re.escape(name) for name in sorted(_CURRENCY_NAMES, key=len, reverse=True)) + ")"
_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_AMOUNT_C = r"(?:(?P<sym>[$€£₹])\s?)?(?P<amount>" + _N + r")(?:\s*(?P<scale>k|thousand|million))?"
_CURRENCY_FORMS = (
    re.compile(r"(?:(?:convert|change|exchange)\s+)?" + _AMOUNT_C + r"(?:\s+(?P<base>" + _CUR + r"))?\s+(?:to|in|into"
               r"|as)\s+(?P<target>" + _CUR + r")"),
    re.compile(r"(?:how\s+much|what)\s+(?:is|are)\s+" + _AMOUNT_C + r"(?:\s+(?P<base>" + _CUR + r"))?\s+in\s+"
               r"(?P<target>" + _CUR + r")"),
    re.compile(r"how\s+many\s+(?P<target>" + _CUR + r")\s+(?:is|are|for|in|make|to)\s+(?:(?P<one>a|an|one)\s+|"
               + _AMOUNT_C + r"\s+)?(?P<base>" + _CUR + r")"),
    re.compile(r"(?:what(?:'s|\s+is)\s+)?(?:the\s+)?(?:exchange\s+|conversion\s+|currency\s+)?rate\s+(?:from\s+|for\s+|"
               r"between\s+|of\s+)?(?P<base>" + _CUR + r")\s+(?:to|and|in|into|vs|versus|against)\s+(?P<target>" + _CUR
               + r")"),
    re.compile(r"(?P<base>" + _CUR + r")\s+(?:to|vs|versus|in)\s+(?P<target>" + _CUR + r")(?:\s+(?:exchange\s+)?"
               r"rate)?"),
)


def _currency(words: str) -> NetAsk | None:
    for pattern in _CURRENCY_FORMS:
        match = pattern.fullmatch(words)
        if not match:
            continue
        groups = match.groupdict()
        symbol = groups.get("sym")
        base_name = groups.get("base")
        if symbol and base_name and _CURRENCY_NAMES[base_name] != _SYMBOL[symbol]:
            return None                                   # "$5 euros": two currencies for one amount
        base = _CURRENCY_NAMES[base_name] if base_name else _SYMBOL.get(symbol or "")
        target = _CURRENCY_NAMES[groups["target"]]
        if not base or base == target:
            return None
        amount = Fraction(groups["amount"].replace(",", "")) if groups.get("amount") else Fraction(1)
        scale = groups.get("scale")
        if scale:
            amount *= {"k": 1000, "thousand": 1000, "million": 10 ** 6}[scale]
        if amount <= 0 or amount > 10 ** 12:
            return None
        return NetAsk(KIND_CURRENCY, amount=amount, base=base, target=target)
    return None


# --------------------------------------------------------------------------
# Sums, percentages and units
# --------------------------------------------------------------------------

def _unit_words(unit: maths.Unit, amount: str, *, spoken: bool) -> str:
    one = amount in ("1", "-1")
    if spoken:
        return unit.singular if one else unit.plural
    if unit.shown == unit.plural and one:
        return unit.singular
    return unit.shown


def _join_unit(amount: str, unit: maths.Unit, *, spoken: bool = False) -> str:
    words = _unit_words(unit, amount, spoken=spoken)
    return f"{amount}{words}" if words in ("°C", "°F") else f"{amount} {words}"


def _maths(words: str, address: str) -> InstantReply | None:
    conversion = maths.convert(words)
    if conversion is not None:
        about = "" if conversion.exact else "about "
        text = (f"{_join_unit(conversion.amount, conversion.source)} = {about}"
                f"{_join_unit(conversion.value, conversion.target)}.")
        said = (f"{_join_unit(conversion.amount, conversion.source, spoken=True)} is {about}"
                f"{_join_unit(conversion.spoken, conversion.target, spoken=True)}, {{sir}}.")
        return InstantReply(KIND_UNITS, text, with_address(said[:1].upper() + said[1:], address))
    calc = maths.percent(words) or maths.calculate(words)
    if calc is None:
        return None
    if calc.zero_division:
        return InstantReply(KIND_MATHS, "You can't divide by zero, so there's no answer.",
                            with_address("You can't divide by zero, {sir}.", address))
    about = "" if calc.exact else "about "
    spoken = calc.spoken or calc.value
    return InstantReply(KIND_MATHS, f"{calc.shown} {'=' if calc.exact else '≈'} {calc.value}",
                        with_address(f"That's {about}{spoken}, {{sir}}.", address))
