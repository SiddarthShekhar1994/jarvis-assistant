"""Instant answers over the network: the weather (Open-Meteo) and currency conversions (Frankfurter, the European
Central Bank's reference rates). Free services with no key and no account (owner decision 2026-11-10). No Qt.

Only what the answer needs leaves this PC: a town's name to Open-Meteo's geocoding service, then that place's
coordinates to its forecast service; two currency codes to Frankfurter. Nothing else goes with them (a plain
User-Agent, no cookies). Each call waits at most TIMEOUT_S, and any failure - no network, a timeout, a place not
found, a reply of the wrong shape - is ``None``: the app then sends the request to Claude exactly as before. Logs
hold kinds, statuses and durations only, never a town, an amount or a reply.

    answer(ask, *, get, cache, fahrenheit, address, monotonic) -> InstantReply | None
    real_get(url, params, timeout) -> object    the JSON reply (requests); refuses while the tests run
    fahrenheit_region() -> bool                 the Windows region measures in US units (Fahrenheit)

``NetCache`` keeps answers in memory only: places while Jarvis runs, the weather for WEATHER_KEEP_S, rates for
RATES_KEEP_S. Credit lines (``source``): Open-Meteo's data is CC BY 4.0, so its answers carry WEATHER_CREDIT.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from typing import Any

from .instant import CURRENCIES, KIND_CURRENCY, KIND_WEATHER, InstantReply, NetAsk
from .persona import with_address

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
RATES_URL = "https://api.frankfurter.dev/v1/latest"
TIMEOUT_S = 2.5
WEATHER_KEEP_S = 10 * 60
RATES_KEEP_S = 6 * 3600
WEATHER_CREDIT = "Weather data by Open-Meteo.com"
RATES_CREDIT = "European Central Bank reference rate, via Frankfurter"
USER_AGENT = "JarvisAssistant/instant-answers"
TEST_GUARD_ENV = "JARVIS_DESKTOP_TEST_GUARD"
_WHOLE_CURRENCIES = {"JPY", "KRW", "HUF", "IDR", "ISK"}      # shown without cents
# WMO weather codes (Open-Meteo's weather_code) in words.
WEATHER_WORDS = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
                 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle",
                 57: "heavy freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
                 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
                 80: "light showers", 81: "showers", 82: "heavy showers", 85: "light snow showers",
                 86: "snow showers", 95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}
SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})
RAIN_NOW_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99})
AMBIGUOUS_RATIO = 10      # two places of one name: the bigger must have this many times the people, else no answer


class NetRefusedInTests(RuntimeError):
    """The real network was asked for while the tests run."""


def real_get(url: str, params: Mapping[str, Any], timeout: float) -> Any:
    """GET ``url`` with ``params`` and the JSON reply (requests, ``timeout`` seconds). Raises NetRefusedInTests while
    the tests run (JARVIS_DESKTOP_TEST_GUARD=1), and whatever requests raises."""
    if os.environ.get(TEST_GUARD_ENV, "") == "1":
        raise NetRefusedInTests("no real network in the tests")
    import requests

    response = requests.get(url, params=dict(params), timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.json()


def fahrenheit_region() -> bool:
    """The Windows region measures in US units (LOCALE_IMEASURE "1"); False when it can't be read (or not Windows,
    or while the tests run)."""
    if os.environ.get(TEST_GUARD_ENV, "") == "1":
        return False
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(4)
        if ctypes.windll.kernel32.GetLocaleInfoEx(None, 0x0D, buffer, 4):   # LOCALE_IMEASURE
            return buffer.value == "1"
    except Exception:  # noqa: BLE001 - not Windows, or no answer: metric
        pass
    return False


@dataclass
class NetCache:
    places: dict[str, tuple[float, float, str]] = field(default_factory=dict)
    weather: dict[tuple, tuple[float, Any]] = field(default_factory=dict)
    rates: dict[tuple[str, str], tuple[float, float, str]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


def answer(ask: NetAsk, *, get: Callable[[str, Mapping[str, Any], float], Any], cache: NetCache,
           fahrenheit: bool = False, address: str = "sir",
           monotonic: Callable[[], float] = time.monotonic) -> InstantReply | None:
    """The network's answer to ``ask`` (any thread), or None when it can't be had for sure (the app then sends the
    request to Claude). Never raises."""
    started = monotonic()
    try:
        reply = (_weather(ask, get, cache, fahrenheit, address, monotonic) if ask.kind == KIND_WEATHER else
                 _currency(ask, get, cache, address, monotonic) if ask.kind == KIND_CURRENCY else None)
    except Exception as exc:  # noqa: BLE001 - no answer for sure: Claude, as before
        logger.info("Instant: the %s service gave no answer (%s, %d ms)", ask.kind, type(exc).__name__,
                    int((monotonic() - started) * 1000))
        return None
    logger.info("Instant: %s %s (%d ms)", ask.kind, "answered" if reply is not None else "not answered",
                int((monotonic() - started) * 1000))
    return reply


# --------------------------------------------------------------------------
# The weather
# --------------------------------------------------------------------------

def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("not a number")
    return float(value)


def _population(result: Mapping[str, Any]) -> int:
    value = result.get("population")
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def _place(town: str, get, cache: NetCache) -> tuple[float, float, str] | None:
    key = town.casefold()
    with cache.lock:
        if key in cache.places:
            return cache.places[key]
    name, _comma, region = (part.strip() for part in town.partition(","))
    data = get(GEOCODE_URL, {"name": name, "count": 10, "language": "en", "format": "json"}, TIMEOUT_S)
    results = data.get("results") if isinstance(data, Mapping) else None
    if not isinstance(results, list):
        return None
    matches: list[Mapping[str, Any]] = []
    for result in results:
        if not isinstance(result, Mapping) or str(result.get("name", "")).casefold() != name.casefold():
            continue
        if region:
            regions = [str(result.get(key2, "")).casefold() for key2 in ("admin1", "country", "country_code",
                                                                           "admin2")]
            if not any(part and (part == region.casefold() or part.startswith(region.casefold())) for part in regions):
                continue
        matches.append(result)
    if not matches:
        return None                                      # no place of that very name: Claude, as before
    matches.sort(key=_population, reverse=True)
    picked = matches[0]
    if len(matches) > 1 and _population(picked) < AMBIGUOUS_RATIO * max(1, _population(matches[1])):
        return None                                      # two places of that name and neither stands out
    latitude, longitude = _number(picked.get("latitude")), _number(picked.get("longitude"))
    place = str(picked.get("name", name))
    where = str(picked.get("admin1") or "") if str(picked.get("country_code", "")).upper() == "US" else \
        str(picked.get("country") or "")
    shown = f"{place}, {where}" if where and where.casefold() != place.casefold() else place
    found = (latitude, longitude, shown)
    with cache.lock:
        cache.places[key] = found
    return found


def _weather(ask: NetAsk, get, cache: NetCache, fahrenheit: bool, address: str, monotonic) -> InstantReply | None:
    place = _place(ask.town, get, cache)
    if place is None:
        return None
    latitude, longitude, shown = place
    unit = "fahrenheit" if fahrenheit else "celsius"
    key = (round(latitude, 3), round(longitude, 3), unit)
    now = monotonic()
    with cache.lock:
        kept = cache.weather.get(key)
    if kept is not None and now - kept[0] < WEATHER_KEEP_S:
        data = kept[1]
    else:
        data = get(FORECAST_URL, {"latitude": latitude, "longitude": longitude,
                                  "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                                  "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                                           "precipitation_probability_max",
                                  "timezone": "auto", "forecast_days": 2, "temperature_unit": unit,
                                  "wind_speed_unit": "mph" if fahrenheit else "kmh"}, TIMEOUT_S)
        with cache.lock:
            cache.weather[key] = (now, data)
    current, daily = data["current"], data["daily"]
    day = 1 if ask.when == "tomorrow" else 0
    code = int(_number(daily["weather_code"][day]))
    high, low = round(_number(daily["temperature_2m_max"][day])), round(_number(daily["temperature_2m_min"][day]))
    chance_raw = daily.get("precipitation_probability_max", [None, None])[day]
    chance = None if chance_raw is None else round(_number(chance_raw))
    degree = "°F" if fahrenheit else "°C"
    when = "tomorrow" if day else "today"
    if ask.rain in ("now_rain", "now_snow"):
        now_code = int(_number(current["weather_code"]))
        wanted, what = (SNOW_CODES, "snowing") if ask.rain == "now_snow" else (RAIN_NOW_CODES, "raining")
        words_now = WEATHER_WORDS.get(now_code, "unknown")
        if now_code in wanted:
            text, said = (f"Yes, it's {what} in {shown} right now ({words_now}).",
                          f"Yes, {{sir}}: {words_now} in {shown} right now.")
        else:
            text, said = (f"No, it isn't {what} in {shown} right now: {words_now}.",
                          f"No, {{sir}}: {words_now} in {shown} right now.")
        return InstantReply(KIND_WEATHER, text, with_address(said, address), WEATHER_CREDIT)
    if ask.rain:
        if ask.rain == "snow":
            yes = code in SNOW_CODES
            text = (f"Snow is forecast in {shown} {when} ({WEATHER_WORDS.get(code, 'snow')})." if yes else
                    f"No snow is forecast in {shown} {when}; {WEATHER_WORDS.get(code, 'no snow')}.")
            said = f"{'Yes' if yes else 'No'}, {{sir}}: {'snow is' if yes else 'no snow is'} forecast {when}."
        else:
            if chance is None:
                return None
            likely = chance >= 50
            text = (f"Rain is {'likely' if likely else 'not likely'} in {shown} {when}: a {chance}% chance "
                    f"({WEATHER_WORDS.get(code, 'unknown')}).")
            said = (f"{'Probably' if likely else 'Probably not'}, {{sir}}: a {chance} percent chance of rain {when}.")
        return InstantReply(KIND_WEATHER, text, with_address(said, address), WEATHER_CREDIT)
    words = WEATHER_WORDS.get(code)
    if words is None:
        return None
    rain_line = f", {chance}% chance of rain" if chance is not None and chance >= 20 else ""
    if day:
        text = f"Tomorrow in {shown}: {words}, {low}° to {high}{degree}{rain_line}."
        said = f"Tomorrow in {shown}: {words}, {low} to {high} degrees, {{sir}}."
        return InstantReply(KIND_WEATHER, text, with_address(said, address), WEATHER_CREDIT)
    temperature = round(_number(current["temperature_2m"]))
    feels = round(_number(current["apparent_temperature"]))
    now_words = WEATHER_WORDS.get(int(_number(current["weather_code"])), words)
    feels_line = f", feels like {feels}°" if abs(feels - temperature) >= 3 else ""
    text = (f"In {shown}: {temperature}{degree} and {now_words}{feels_line}; today {low}° to {high}°{rain_line}.")
    said = f"In {shown} it's {temperature} degrees and {now_words}, {{sir}}; up to {high} today."
    return InstantReply(KIND_WEATHER, text, with_address(said, address), WEATHER_CREDIT)


# --------------------------------------------------------------------------
# Currencies
# --------------------------------------------------------------------------

def _money(value: float, code: str, *, rate: bool = False) -> str:
    if rate:
        return f"{value:,.4f}".rstrip("0").rstrip(".") if value < 1000 else f"{value:,.2f}"
    if code in _WHOLE_CURRENCIES:
        return f"{round(value):,}"
    return f"{value:,.2f}"


def _named(code: str, amount: float) -> str:
    one, many, _names = CURRENCIES[code]
    return one if amount == 1 else many


def _currency(ask: NetAsk, get, cache: NetCache, address: str, monotonic) -> InstantReply | None:
    if ask.base not in CURRENCIES or ask.target not in CURRENCIES:
        return None
    key = (ask.base, ask.target)
    now = monotonic()
    with cache.lock:
        kept = cache.rates.get(key)
    if kept is not None and now - kept[0] < RATES_KEEP_S:
        _at, rate, day = kept
    else:
        data = get(RATES_URL, {"base": ask.base, "symbols": ask.target}, TIMEOUT_S)
        rate = _number(data["rates"][ask.target])
        day = str(data["date"])
        date.fromisoformat(day)                          # a reply of the right shape only
        if rate <= 0:
            return None
        with cache.lock:
            cache.rates[key] = (now, rate, day)
    amount = float(ask.amount)
    value = amount * rate
    when = date.fromisoformat(day)
    dated = f"ECB rate of {when.day} {when:%B}"
    is_rate = ask.amount == Fraction(1)
    given = _money(amount, ask.base) if not is_rate else "1"
    result = _money(value, ask.target, rate=is_rate)
    text = f"{given} {_named(ask.base, amount)} = {result} {_named(ask.target, value)} ({dated})."
    said = f"{given} {_named(ask.base, amount)} is about {result} {_named(ask.target, value)}, {{sir}}."
    return InstantReply(KIND_CURRENCY, text, with_address(said, address), RATES_CREDIT)
