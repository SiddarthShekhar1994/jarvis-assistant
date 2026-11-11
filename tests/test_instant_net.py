"""Instant answers over the network (briefing_reader.instant_net): what is sent (only a town's name, then its
coordinates; only two currency codes), the answers, the cache, and every failure coming back as None so the request
goes to Claude. A fake HTTP seam stands in for the services: the real one refuses while the tests run."""

from __future__ import annotations

import logging
import unittest
from fractions import Fraction

from briefing_reader import instant_net
from briefing_reader.instant import KIND_CURRENCY, KIND_WEATHER, NetAsk

PARIS = {"results": [{"name": "Paris", "latitude": 48.85, "longitude": 2.35, "country": "France", "country_code": "FR",
                      "admin1": "Île-de-France", "population": 2138551},
                     {"name": "Paris", "latitude": 33.66, "longitude": -95.55, "country": "United States",
                      "country_code": "US", "admin1": "Texas", "population": 24171}]}
FORECAST = {"current": {"temperature_2m": 12.4, "apparent_temperature": 8.6, "weather_code": 61, "wind_speed_10m": 9},
            "daily": {"time": ["2026-11-10", "2026-11-11"], "weather_code": [63, 71],
                      "temperature_2m_max": [14.2, 6.0], "temperature_2m_min": [8.1, -1.4],
                      "precipitation_probability_max": [70, 15]}}
RATES = {"amount": 1.0, "base": "USD", "date": "2026-11-10", "rates": {"EUR": 0.9234}}


class FakeGet:
    def __init__(self, replies: dict[str, object]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, params: dict, timeout: float):
        self.calls.append((url, dict(params)))
        assert timeout <= instant_net.TIMEOUT_S
        reply = self.replies[url]
        if isinstance(reply, Exception):
            raise reply
        return reply


class Clock:
    def __init__(self) -> None:
        self.at = 1000.0

    def __call__(self) -> float:
        return self.at


def weather(town: str = "paris", **kwargs: object) -> NetAsk:
    return NetAsk(KIND_WEATHER, town=town, **kwargs)


class WeatherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.get = FakeGet({instant_net.GEOCODE_URL: PARIS, instant_net.FORECAST_URL: FORECAST})
        self.cache = instant_net.NetCache()
        self.clock = Clock()

    def answer(self, ask: NetAsk, **kwargs: object):
        return instant_net.answer(ask, get=self.get, cache=self.cache, monotonic=self.clock, **kwargs)

    def test_now_names_the_place_found_and_sends_only_what_it_needs(self) -> None:
        reply = self.answer(weather())
        self.assertEqual(reply.text, "In Paris, France: 12°C and light rain, feels like 9°; today 8° to 14°, 70% "
                                     "chance of rain.")
        self.assertEqual(reply.speech, "In Paris, France it's 12 degrees and light rain, sir; up to 14 today.")
        self.assertEqual(reply.source, instant_net.WEATHER_CREDIT)
        (geocode, sent), (forecast, asked) = self.get.calls
        self.assertEqual(geocode, instant_net.GEOCODE_URL)
        self.assertEqual(sent, {"name": "paris", "count": 10, "language": "en", "format": "json"})
        self.assertEqual((asked["latitude"], asked["longitude"], asked["temperature_unit"]), (48.85, 2.35, "celsius"))

    def test_tomorrow_rain_snow_fahrenheit_and_a_region(self) -> None:
        self.assertIn("Tomorrow in Paris, France: light snow, -1° to 6°C.", self.answer(weather(when="tomorrow")).text)
        self.assertIn("Rain is likely in Paris, France today: a 70% chance", self.answer(weather(rain="rain")).text)
        self.assertIn("Rain is not likely", self.answer(weather(rain="rain", when="tomorrow")).text)
        self.assertIn("Snow is forecast in Paris, France tomorrow", self.answer(weather(rain="snow", when="tomorrow")).text)
        self.assertIn("°F", instant_net.answer(weather("paris, texas"), get=self.get, cache=instant_net.NetCache(),
                                               fahrenheit=True).text)
        self.assertIn("Paris, Texas", instant_net.answer(weather("paris, texas"), get=self.get,
                                                         cache=instant_net.NetCache()).text)

    def test_two_places_of_one_name_and_raining_now(self) -> None:
        two = {"results": [{"name": "Springfield", "latitude": 39.8, "longitude": -89.6, "country": "United States",
                            "country_code": "US", "admin1": "Illinois", "population": 114394},
                           {"name": "Springfield", "latitude": 37.2, "longitude": -93.3, "country": "United States",
                            "country_code": "US", "admin1": "Missouri", "population": 169176}]}
        get = FakeGet({instant_net.GEOCODE_URL: two, instant_net.FORECAST_URL: FORECAST})
        self.assertIsNone(instant_net.answer(weather("springfield"), get=get, cache=instant_net.NetCache()))
        named = instant_net.answer(weather("springfield, illinois"), get=get, cache=instant_net.NetCache())
        self.assertIn("Springfield, Illinois", named.text)
        self.assertIn("Springfield, Illinois", named.speech)
        now = self.answer(weather(rain="now_rain"))
        self.assertEqual(now.text, "Yes, it's raining in Paris, France right now (light rain).")
        self.assertIn("No, it isn't snowing", self.answer(weather(rain="now_snow")).text)

    def test_the_cache_keeps_places_and_the_weather_ten_minutes(self) -> None:
        self.answer(weather())
        self.answer(weather())
        self.assertEqual(len(self.get.calls), 2)
        self.clock.at += instant_net.WEATHER_KEEP_S + 1
        self.answer(weather())
        self.assertEqual([url for url, _params in self.get.calls][2:], [instant_net.FORECAST_URL])

    def test_any_failure_is_none_so_claude_answers(self) -> None:
        for replies in ({instant_net.GEOCODE_URL: {"results": []}},
                        {instant_net.GEOCODE_URL: {"nothing": 1}},
                        {instant_net.GEOCODE_URL: OSError("offline")},
                        {instant_net.GEOCODE_URL: PARIS, instant_net.FORECAST_URL: TimeoutError()},
                        {instant_net.GEOCODE_URL: PARIS, instant_net.FORECAST_URL: {"current": {}}},
                        {instant_net.GEOCODE_URL: PARIS, instant_net.FORECAST_URL: "not json"}):
            with self.subTest(replies=list(replies.values())[-1]):
                self.assertIsNone(instant_net.answer(weather(), get=FakeGet(replies), cache=instant_net.NetCache()))
        self.assertIsNone(self.answer(weather("paris, germany")))      # no such Paris: not a guess

    def test_the_log_never_holds_the_town(self) -> None:
        with self.assertLogs("briefing_reader.instant_net", level="INFO") as logs:
            self.answer(weather("paris"))
        self.assertNotIn("paris", "\n".join(logs.output).casefold())


class CurrencyTests(unittest.TestCase):
    def test_an_amount_a_rate_and_whole_currencies(self) -> None:
        get = FakeGet({instant_net.RATES_URL: RATES})
        cache = instant_net.NetCache()
        reply = instant_net.answer(NetAsk(KIND_CURRENCY, amount=Fraction(100), base="USD", target="EUR"), get=get,
                                   cache=cache)
        self.assertEqual(reply.text, "100.00 US dollars = 92.34 euros (ECB rate of 10 November).")
        self.assertEqual(reply.speech, "100.00 US dollars is about 92.34 euros, sir.")
        self.assertEqual(get.calls, [(instant_net.RATES_URL, {"base": "USD", "symbols": "EUR"})])
        rate = instant_net.answer(NetAsk(KIND_CURRENCY, base="USD", target="EUR"), get=get, cache=cache)
        self.assertEqual(rate.text, "1 US dollar = 0.9234 euros (ECB rate of 10 November).")
        self.assertEqual(len(get.calls), 1)                                # the day's rate is kept
        yen = FakeGet({instant_net.RATES_URL: {"amount": 1.0, "base": "USD", "date": "2026-11-10",
                                               "rates": {"JPY": 151.234}}})
        self.assertIn("= 15,123 Japanese yen", instant_net.answer(
            NetAsk(KIND_CURRENCY, amount=Fraction(100), base="USD", target="JPY"), get=yen,
            cache=instant_net.NetCache()).text)

    def test_a_bad_reply_is_none(self) -> None:
        for reply in ({"rates": {}}, {"rates": {"EUR": -1}, "date": "2026-11-10"}, {"rates": {"EUR": 0.9}, "date": "x"},
                      OSError("offline")):
            with self.subTest(reply=reply):
                self.assertIsNone(instant_net.answer(NetAsk(KIND_CURRENCY, base="USD", target="EUR"),
                                                     get=FakeGet({instant_net.RATES_URL: reply}),
                                                     cache=instant_net.NetCache()))
        self.assertIsNone(instant_net.answer(NetAsk(KIND_CURRENCY, base="USD", target="XXX"), get=FakeGet({}),
                                             cache=instant_net.NetCache()))


class RealNetworkTests(unittest.TestCase):
    def test_the_real_network_is_refused_while_the_tests_run(self) -> None:
        with self.assertRaises(instant_net.NetRefusedInTests):
            instant_net.real_get(instant_net.RATES_URL, {"base": "USD", "symbols": "EUR"}, 1.0)
        self.assertFalse(instant_net.fahrenheit_region())


SAN_JOSE = {"results": [{"name": "San Jose", "latitude": 37.34, "longitude": -121.89, "country": "United States",
                         "country_code": "US", "admin1": "California", "population": 1013240},
                        {"name": "San Jose", "latitude": 37.43, "longitude": -121.98, "country": "Philippines",
                         "country_code": "PH", "admin1": "Central Luzon", "population": 40000}]}
SPRINGFIELD = {"results": [{"name": "Springfield", "latitude": 39.8, "longitude": -89.64, "country": "United States",
                            "country_code": "US", "admin1": "Illinois", "population": 114394},
                           {"name": "Springfield", "latitude": 37.2, "longitude": -93.29, "country": "United States",
                            "country_code": "US", "admin1": "Missouri", "population": 169176}]}


class FindPlaceTests(unittest.TestCase):
    """Round 2: the weather's town by voice - the same search and the same rule for places of one name."""

    def find(self, town: str, reply: object):
        get = FakeGet({instant_net.GEOCODE_URL: reply, instant_net.FORECAST_URL: FORECAST})
        cache = instant_net.NetCache()
        return instant_net.find_place(town, get=get, cache=cache), get, cache

    def test_found_named_in_full_and_kept_for_its_weather(self) -> None:
        (status, place), get, cache = self.find("San Jose", SAN_JOSE)
        self.assertEqual(status, instant_net.PLACE_FOUND)
        self.assertEqual((place.shown, place.full, place.query, place.region, place.country),
                         ("San Jose, California", "San Jose, California, United States", "San Jose, California",
                          "California", "United States"))
        self.assertEqual(get.calls, [(instant_net.GEOCODE_URL, {"name": "San Jose", "count": 10, "language": "en",
                                                                "format": "json"})])
        self.assertEqual(repr(place), "Place(...)")
        reply = instant_net.answer(weather(place.query), get=get, cache=cache, monotonic=Clock())
        self.assertIn("In San Jose, California:", reply.text)
        self.assertEqual([url for url, _params in get.calls], [instant_net.GEOCODE_URL, instant_net.FORECAST_URL])
        (status, oslo), _get, _cache = self.find("Oslo", {"results": [{"name": "Oslo", "latitude": 59.9, "longitude": 10.7,
                                                                       "country": "Norway", "country_code": "NO",
                                                                       "admin1": "Oslo", "population": 580000}]})
        self.assertEqual((oslo.shown, oslo.full), ("Oslo, Norway", "Oslo, Norway"))

    def test_several_none_and_failed(self) -> None:
        (status, names), _get, _cache = self.find("Springfield", SPRINGFIELD)
        self.assertEqual(status, instant_net.PLACE_SEVERAL)
        self.assertEqual(names, ["Springfield, Missouri, United States", "Springfield, Illinois, United States"])
        (status, place), _get, _cache = self.find("Springfield, Illinois", SPRINGFIELD)
        self.assertEqual((status, place.shown), (instant_net.PLACE_FOUND, "Springfield, Illinois"))
        self.assertEqual(self.find("Xyzzy", {"generationtime_ms": 0.4})[0], (instant_net.PLACE_NONE, None))
        with self.assertLogs("briefing_reader.instant_net", level="INFO") as logs:
            self.assertEqual(self.find("Oslo", OSError("no network"))[0], (instant_net.PLACE_FAILED, None))
            self.assertEqual(self.find("Oslo", {"results": [{"name": "Oslo", "latitude": "x"}]})[0],
                             (instant_net.PLACE_FAILED, None))
        self.assertNotIn("Oslo", "\n".join(logs.output))


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()
