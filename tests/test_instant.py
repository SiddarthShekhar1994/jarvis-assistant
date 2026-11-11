"""Instant answers on this PC (briefing_reader.instant, briefing_reader.instant_maths): what Jarvis answers himself,
what he hands to the app (timers, the network's questions) and - just as important - everything that must still go
to Claude because it can't be read for sure. Pure: no Qt, no network, a fixed clock."""

from __future__ import annotations

import ast
import time
import unittest
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

from briefing_reader import instant, instant_maths
from briefing_reader.instant import (KIND_DATE, KIND_DATE_MATHS, KIND_MATHS, KIND_PLACE_TIME, KIND_TIME, KIND_TIMER,
                                     KIND_UNITS, InstantFlags, InstantReply, NetAsk, TimerAsk, understand)

PACIFIC = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 10, 15, 42, tzinfo=PACIFIC)          # a Tuesday afternoon
ALL = InstantFlags(weather=True, currency=True, town="")
PACKAGE = Path(instant.__file__).parent


def ask(text: str, *, now: datetime = NOW, flags: InstantFlags = ALL, address: str = "sir", hour24: bool = False):
    return understand(text, now=now, flags=flags, address=address, hour24=hour24)


class ReplyTests(unittest.TestCase):
    CASES = (   # (request, kind, words the shown text has, words the speech has)
        ("what time is it?", KIND_TIME, "3:42 PM, Tuesday 10 November 2026", "It's 3:42 PM, sir."),
        ("Jarvis, what's the time", KIND_TIME, "3:42 PM", "3:42 PM"),
        ("hey jarvis what time is it now", KIND_TIME, "3:42 PM", "sir"),
        ("can you tell me what time it is please", KIND_TIME, "3:42 PM", "3:42 PM"),
        ("time", KIND_TIME, "3:42 PM", "3:42 PM"),
        ("what's the date", KIND_DATE, "Tuesday 10 November 2026", "It's Tuesday, 10 November, sir."),
        ("what day is it today?", KIND_DATE, "Tuesday 10 November 2026", "Tuesday"),
        ("what's today", KIND_DATE, "Tuesday", "Tuesday"),
        ("what year is it", KIND_DATE, "2026", "2026"),
        ("what month is it", KIND_DATE, "November 2026", "November"),
        ("what time is it in Tokyo", KIND_PLACE_TIME, "8:42 AM on Wednesday (17 hours ahead of you)", "Tokyo"),
        ("time in london", KIND_PLACE_TIME, "11:42 PM (8 hours ahead of you)", "In London it's 11:42 PM, sir."),
        ("what's the time in new york right now", KIND_PLACE_TIME, "New York it's 6:42 PM (3 hours ahead", "6:42"),
        ("what time is it in india", KIND_PLACE_TIME, "13.5 hours ahead of you", "India"),
        ("what time is it in kathmandu", KIND_PLACE_TIME, "13.75 hours ahead", "Kathmandu"),
        ("time in the uk", KIND_PLACE_TIME, "In the UK it's 11:42 PM", "the UK"),
        ("what time is it in los angeles", KIND_PLACE_TIME, "the same time as here", "3:42 PM"),
        ("what time is it in honolulu", KIND_PLACE_TIME, "2 hours behind you", "Honolulu"),
        ("what day is the 25th", KIND_DATE_MATHS, "25 November is a Wednesday.", "Wednesday"),
        ("what day is christmas", KIND_DATE_MATHS, "Christmas is a Friday.", "Christmas is a Friday, sir."),
        ("what day of the week is the twenty fifth of december", KIND_DATE_MATHS, "25 December is a Friday", "Friday"),
        ("what day does christmas fall on", KIND_DATE_MATHS, "Christmas is a Friday", "Friday"),
        ("is christmas on a friday", KIND_DATE_MATHS, "Yes, Christmas is a Friday", "Yes, sir"),
        ("is the 4th of july a friday", KIND_DATE_MATHS, "No, 4 July is a Sunday (Sunday 4 July 2027)", "No, sir"),
        ("what day was 25 december 2020", KIND_DATE_MATHS, "25 December 2020 was a Friday.", "was a Friday"),
        ("what day is 2027-01-01", KIND_DATE_MATHS, "is a Friday", "Friday"),
        ("days until christmas", KIND_DATE_MATHS, "45 days until Christmas (Friday 25 December 2026).", "45 days"),
        ("how many days until new year's eve?", KIND_DATE_MATHS, "51 days until New Year's Eve", "51 days"),
        ("how many sleeps until christmas", KIND_DATE_MATHS, "45 days", "45 days"),
        ("how many weeks until christmas", KIND_DATE_MATHS, "6 weeks and 3 days until Christmas", "6 weeks"),
        ("how long until halloween", KIND_DATE_MATHS, "355 days until Halloween", "355 days"),
        ("how many days since 1 january", KIND_DATE_MATHS, "313 days since 1 January", "313 days"),
        ("how many days since christmas", KIND_DATE_MATHS, "320 days since Christmas (Thursday 25 December 2025)",
         "320"),
        ("days between 1 march and 1 june", KIND_DATE_MATHS, "92 days between 1 March 2027 and 1 June 2027.", "92 days"),
        ("how many days from 1 january 2026 to 1 january 2027", KIND_DATE_MATHS, "365 days", "365"),
        ("what's the date in 3 weeks", KIND_DATE_MATHS, "In 3 weeks it will be Tuesday 1 December 2026.", "December"),
        ("10 days from now", KIND_DATE_MATHS, "In 10 days it will be Friday 20 November 2026.", "Friday"),
        ("what's a week from today", KIND_DATE_MATHS, "In 1 week it will be Tuesday 17 November 2026", "17"),
        ("what was the date 5 days ago", KIND_DATE_MATHS, "5 days ago it was Thursday 5 November 2026.", "Thursday"),
        ("what day is tomorrow", KIND_DATE_MATHS, "Tomorrow is Wednesday 11 November 2026.", "Wednesday"),
        ("what was yesterday's date", KIND_DATE_MATHS, "Yesterday was Monday 9 November 2026.", "Monday"),
        ("when is halloween", KIND_DATE_MATHS, "Halloween is on Sunday 31 October 2027, in 355 days.", "Halloween"),
        ("what's twelve times seven", KIND_MATHS, "12 × 7 = 84", "That's 84, sir."),
        ("15% of 80", KIND_MATHS, "15% of 80 = 12", "That's 12, sir."),
        ("what is fifteen percent of eighty", KIND_MATHS, "15% of 80 = 12", "12"),
        ("(3+4)^2", KIND_MATHS, "(3 + 4) ^ 2 = 49", "49"),
        ("what's the square root of 144", KIND_MATHS, "√144 = 12", "12"),
        ("what is 2 to the power of 10", KIND_MATHS, "2 ^ 10 = 1,024", "1,024"),
        ("5 squared", KIND_MATHS, "5 ^ 2 = 25", "25"),
        ("what's 1/3", KIND_MATHS, "1 ÷ 3 ≈ 0.3333333333", "That's about 0.333, sir."),
        ("what's 0.1 + 0.2", KIND_MATHS, "0.1 + 0.2 = 0.3", "0.3"),
        ("what's 10 divided by 0", KIND_MATHS, "You can't divide by zero", "You can't divide by zero, sir."),
        ("what's -5 * -3", KIND_MATHS, "−5 × −3 = 15", "15"),
        ("1,250 x 4", KIND_MATHS, "1,250 × 4 = 5,000", "5,000"),
        ("what is twenty five plus seventeen", KIND_MATHS, "25 + 17 = 42", "42"),
        ("one hundred and five minus five", KIND_MATHS, "105 − 5 = 100", "100"),
        ("80 is what percent of 200", KIND_MATHS, "= 40%", "40 percent"),
        ("what percent of 200 is 50", KIND_MATHS, "= 25%", "25 percent"),
        ("increase 80 by 15%", KIND_MATHS, "= 92", "92"),
        ("decrease 80 by 25 percent", KIND_MATHS, "= 60", "60"),
        ("what's 20% off 50", KIND_MATHS, "= 40", "40"),
        ("what's a 15% tip on 80", KIND_MATHS, "= 12", "12"),
        ("5 km in miles", KIND_UNITS, "5 km = about 3.107 miles.", "5 kilometres is about 3.107 miles, sir."),
        ("convert 70 f to c", KIND_UNITS, "70°F = about 21.11°C.", "70 degrees Fahrenheit is about 21.11"),
        ("what's 100 c in f", KIND_UNITS, "100°C = 212°F.", "212 degrees Fahrenheit"),
        ("-40 c in f", KIND_UNITS, "-40°C = -40°F.", "-40"),
        ("how many ounces in a pound", KIND_UNITS, "1 pound = 16 ounces.", "1 pound is 16 ounces, sir."),
        ("how many feet are in a meter", KIND_UNITS, "1 m = about 3.281 feet.", "1 metre is about 3.281 feet"),
        ("5 pounds to kg", KIND_UNITS, "5 pounds = about 2.268 kg.", "kilograms"),
        ("2 cups in ml", KIND_UNITS, "2 US cups = about 473.18 ml.", "millilitres"),
        ("60 mph in km/h", KIND_UNITS, "60 mph = about 96.56 km/h.", "kilometres per hour"),
        ("1 gb in mb", KIND_UNITS, "1 GB = 1,000 MB.", "1,000 megabytes"),
        ("1 acre in square feet", KIND_UNITS, "1 acre = 43,560 square feet.", "43,560"),
        ("how many seconds in a day", KIND_UNITS, "1 day = 86,400 seconds.", "86,400"),
        ("100 miles in km", KIND_UNITS, "100 miles = 160.9344 km.", "160.9"),
        ("set a timer for 5 hours", KIND_TIMER, "Timers run from 10 seconds to 4 hours; nothing was set.", "4 hours"),
        ("set a timer for 5 seconds", KIND_TIMER, "nothing was set", "10 seconds"),
    )

    def test_each_answer(self) -> None:
        for text, kind, shown, said in self.CASES:
            with self.subTest(text=text):
                reply = ask(text)
                self.assertIsInstance(reply, InstantReply)
                self.assertEqual(reply.kind, kind)
                self.assertIn(shown, reply.text)
                self.assertIn(said, reply.speech)

    def test_the_form_of_address_and_the_24_hour_clock(self) -> None:
        self.assertEqual(ask("what time is it", address="").speech, "It's 3:42 PM.")
        self.assertEqual(ask("what time is it", address="boss").speech, "It's 3:42 PM, boss.")
        reply = ask("what time is it", hour24=True)
        self.assertIn("15:42", reply.text)
        self.assertIn("3:42 PM", reply.speech)                   # speech keeps the 12-hour clock
        self.assertIn("08:42", ask("time in tokyo", hour24=True).text)

    def test_a_reply_shows_its_kind_only(self) -> None:
        reply = ask("what's 12 x 7")
        self.assertEqual(repr(reply), "InstantReply(maths)")
        self.assertNotIn("84", repr(reply))
        self.assertEqual(repr(ask("remind me in 5 minutes to call Ana")), "TimerAsk(set)")
        self.assertNotIn("paris", repr(ask("weather in paris")))

    def test_today_counts(self) -> None:
        christmas = datetime(2026, 12, 25, 9, 0, tzinfo=PACIFIC)
        self.assertEqual(ask("days until christmas", now=christmas).text, "Christmas is today (Friday 25 December 2026).")
        self.assertIn("is today", ask("what day is christmas", now=christmas).text)
        boxing = datetime(2026, 12, 26, 9, 0, tzinfo=PACIFIC)
        self.assertIn("364 days until Christmas", ask("days until christmas", now=boxing).text)
        self.assertIn("1 day since Christmas", ask("how many days since christmas", now=boxing).text)

    def test_a_leap_day_waits_for_its_year(self) -> None:
        self.assertIn("29 February 2028 is a Tuesday", ask("what day is 29 february").text)
        self.assertIsNone(ask("what day is 30 february"))
        self.assertIsNone(ask("what day is 29 february 2027"))
        # April has no 31st: the next 31st is May's, and the answer names it.
        self.assertIn("31 May is a Sunday", ask("what day is the 31st", now=datetime(2026, 4, 30, 9, tzinfo=PACIFIC)).text)

    def test_the_review_cases(self) -> None:
        self.assertEqual(ask("what day was christmas").text, "Christmas was a Thursday.")
        self.assertEqual(ask("what day was the 25th").text, "25 October was a Sunday.")
        self.assertIn("No, 4 July was a Saturday", ask("was the 4th of july a friday").text)
        january = datetime(2026, 1, 10, 9, tzinfo=PACIFIC)
        self.assertIn("14 days between 1 January 2027 and 15 January 2027",
                      ask("days between 1 january and 15 january", now=january).text)
        self.assertIn("275 days between 1 March 2027 and 1 December 2027", ask("days between 1 march and 1 december").text)
        self.assertIn("90 days between 1 December and 1 March 2027", ask("days between 1 december and 1 march").text)
        self.assertEqual(ask("days between today and christmas").text[:7], "45 days")
        self.assertEqual(ask("what's 4^0.5").text, "4 ^ 0.5 = 2")
        self.assertEqual(ask("is it raining in paris").rain, "now_rain")
        self.assertEqual(ask("will it be raining in paris tomorrow").rain, "rain")

    def test_a_naive_clock_is_read_as_local(self) -> None:
        self.assertIsInstance(understand("what time is it", now=datetime(2026, 11, 10, 15, 42)), InstantReply)


class HandedOverTests(unittest.TestCase):
    """What the app answers: timers and the network's questions."""

    def test_timers(self) -> None:
        cases = (("set a timer for 10 minutes", 600, ""), ("timer 90 seconds", 90, ""), ("a 5 minute timer", 300, ""),
                 ("set a 5-minute timer", 300, ""), ("set an hour and a half timer", 5400, ""),
                 ("set a timer for 1 hour 30 minutes", 5400, ""), ("set a timer for 2 minutes and 30 seconds", 150, ""),
                 ("set a timer for half an hour", 1800, ""), ("set a timer for ten minutes", 600, ""),
                 ("start a countdown for 45 seconds", 45, ""), ("set a timer for 1.5 minutes", 90, ""),
                 ("timer for 2 and a half minutes", 150, ""), ("set a timer for a quarter of an hour", 900, ""),
                 ("set a timer for an hour", 3600, ""), ("timer 5min", 300, ""),
                 ("set a timer for 10 minutes for the pasta", 600, "the pasta"),
                 ("remind me in 20 minutes to stretch", 1200, "stretch"),
                 ("remind me to call mum in 5 minutes", 300, "call mum"), ("let me know in 3 minutes", 180, ""),
                 ("remind me in 5 minutes to call Ana", 300, "call Ana"),
                 ("set a timer for 4 hours", 14400, ""), ("set a timer for 10 seconds", 10, ""))
        for text, seconds, label in cases:
            with self.subTest(text=text):
                found = ask(text)
                self.assertIsInstance(found, TimerAsk)
                self.assertEqual((found.action, found.seconds, found.label), (instant.TIMER_SET, seconds, label))
        self.assertEqual(ask("cancel the timer"), TimerAsk(instant.TIMER_CANCEL))
        self.assertEqual(ask("stop my timer"), TimerAsk(instant.TIMER_CANCEL))
        self.assertEqual(ask("cancel all timers"), TimerAsk(instant.TIMER_CANCEL, every=True))
        self.assertEqual(ask("cancel all the timers"), TimerAsk(instant.TIMER_CANCEL, every=True))
        self.assertEqual(ask("cancel the 10 minute timer"), TimerAsk(instant.TIMER_CANCEL, seconds=600))
        for text in ("how long is left on my timer", "how much time is left on the timer", "check my timers",
                     "do i have any timers running", "when does my timer go off", "what's left on my timer"):
            with self.subTest(text=text):
                self.assertEqual(ask(text), TimerAsk(instant.TIMER_LEFT))
        self.assertEqual(ask("how much time is left"), TimerAsk(instant.TIMER_LEFT, bare=True))
        self.assertIsNone(ask("set a timer for 10 minutes", flags=InstantFlags(timers=False)))

    def test_the_weather(self) -> None:
        cases = (("weather in paris", "paris", "now", ""), ("what's the weather like in london tomorrow", "london",
                                                            "tomorrow", ""),
                 ("how's the weather in new york", "new york", "now", ""),
                 ("what's the weather in san jose, california", "san jose, california", "now", ""),
                 ("will it rain tomorrow in oslo", "oslo", "tomorrow", "rain"),
                 ("is it going to snow in denver tomorrow", "denver", "tomorrow", "snow"),
                 ("weather in paris tomorrow", "paris", "tomorrow", ""),
                 ("what's the temperature in tokyo", "tokyo", "now", ""),
                 ("do i need an umbrella in seattle", "seattle", "now", "rain"),
                 ("forecast for berlin", "berlin", "now", ""))
        for text, town, when, rain in cases:
            with self.subTest(text=text):
                found = ask(text)
                self.assertIsInstance(found, NetAsk)
                self.assertEqual((found.kind, found.town, found.when, found.rain, found.town_given),
                                 (instant.KIND_WEATHER, town, when, rain, True))
        home = InstantFlags(weather=True, town="Springfield, Illinois")
        for text in ("what's the weather", "weather", "will it rain today", "how cold is it outside",
                     "what's the weather like here"):
            with self.subTest(text=text):
                found = ask(text, flags=home)
                self.assertEqual((found.town, found.town_given), ("Springfield, Illinois", False))
        for text in ("what's the weather", "weather", "weather for the weekend", "what's the weather in 2 days",
                     "will it rain on my parade"):
            with self.subTest(text=text):
                self.assertIsNone(ask(text))                    # no town (or not one): Claude, as before
        self.assertIsNone(ask("weather in paris", flags=InstantFlags()))

    def test_currencies(self) -> None:
        cases = (("100 dollars in euros", 100, "USD", "EUR"), ("convert 50 gbp to usd", 50, "GBP", "USD"),
                 ("$20 in euros", 20, "USD", "EUR"), ("€15 to dollars", 15, "EUR", "USD"),
                 ("how much is 1,000 yen in us dollars", 1000, "JPY", "USD"),
                 ("exchange rate yen to rupees", 1, "JPY", "INR"),
                 ("what's the exchange rate between euros and pounds", 1, "EUR", "GBP"),
                 ("how many euros in a dollar", 1, "USD", "EUR"), ("usd to eur", 1, "USD", "EUR"),
                 ("5 pounds to euros", 5, "GBP", "EUR"), ("200 canadian dollars in mexican pesos", 200, "CAD", "MXN"),
                 ("convert 2.5k usd to chf", 2500, "USD", "CHF"))
        for text, amount, base, target in cases:
            with self.subTest(text=text):
                found = ask(text)
                self.assertIsInstance(found, NetAsk)
                self.assertEqual((found.kind, found.amount, found.base, found.target),
                                 (instant.KIND_CURRENCY, Fraction(amount), base, target))
        for text in ("how many pesos in a dollar", "100 kroner in dollars", "$5 euros in pounds", "100 usd in usd",
                     "0 dollars in euros", "100 dollars in bitcoin"):
            with self.subTest(text=text):
                self.assertIsNone(ask(text))
        self.assertIsNone(ask("100 dollars in euros", flags=InstantFlags()))


class ClaudeStillAnswersTests(unittest.TestCase):
    """Anything that is not read for sure goes to Claude exactly as before."""

    TO_CLAUDE = (
        "what is the meaning of life", "what time is my meeting", "what time is my flight tomorrow",
        "how many days until my trip", "when is my next meeting", "what day is my dentist appointment",
        "convert this pdf to word", "what's 5 apples plus 3", "remember that the wifi password is fast",
        "web: what time is it in tokyo", "research: 15% of 80", "time in springfield", "what time is it in texas",
        "what time is it in australia", "what time is it in portland", "what time is it in the us",
        "time in my timezone", "what day is easter", "how many days until thanksgiving", "what's the date in 3 months",
        "what day is next friday", "what day is 12/25", "how many days until 12/25", "what's 5", "what is -5",
        "what's 80 + 15%", "2(3+4)", "what's 2^5000", "what is 10 to the power of 100", "what's the square root of -4",
        "5 km in kg", "5k in miles", "-500 c in f", "-5 km in miles", "how many tons in a pound",
        "convert 5 in cm", "what is a light year in miles", "how many seconds in a year", "draft an email to Ana",
        "open spotify", "set a timer for 1:30", "set an alarm for 7 am", "wake me up at 6", "set a timer",
        "set a timer for 5 minutes 3 minutes", "set a timer for 30 seconds 1 minute", "cancel my meeting",
        "stop the music", "what's the weather on mars", "plan my day", "prep me for my 2 PM", "any follow-ups?",
        "what's the time difference between london and tokyo", "is it raining", "time to go", "dinner time",
        "what's the date of the meeting", "x", "", "   ", "?", "a" * 400,
        "when is christmas next year", "what's 5² + 1", "what is ½ of 10", "0x10", "24/7", "3-4", "5x5",
        "time in est", "time in pst", "what time is it in perth", "what time is it in lima", "reset the timer",
        "weather in london tomorrow morning", "weather in may", "weather in paris tonight",
    )

    def test_each_goes_to_claude(self) -> None:
        for text in self.TO_CLAUDE:
            with self.subTest(text=text[:60]):
                self.assertIsNone(ask(text))

    def test_no_zone_data_sends_a_place_to_claude(self) -> None:
        from unittest import mock

        with mock.patch("zoneinfo.ZoneInfo", side_effect=Exception("no tzdata")):
            self.assertIsNone(ask("what time is it in tokyo"))
            self.assertIsInstance(ask("what time is it"), InstantReply)


class CalculatorTests(unittest.TestCase):
    def test_spoken_numbers(self) -> None:
        for words, digits in (("twenty five", "25"), ("one hundred and five", "105"), ("three point five", "3.5"),
                              ("a thousand", "1000"), ("twelve hundred", "1200"), ("two thousand and five", "2005"),
                              ("five twenty", "5 20"), ("twenty-one", "21"), ("2 thousand", "2000"),
                              ("a hundred and", "100 and"), ("one million two hundred thousand", "1200000"),
                              ("set a timer", "set a timer"), ("an hour", "an hour"), ("zero", "0")):
            with self.subTest(words=words):
                self.assertEqual(instant_maths.spoken_numbers(words), digits)

    def test_limits(self) -> None:
        self.assertIsNone(instant_maths.calculate("2 ^ 1001"))
        self.assertIsNone(instant_maths.calculate("9 ^ 999"))
        self.assertIsNone(instant_maths.calculate(" + ".join(["1"] * 40)))     # more than MAX_TOKENS tokens
        self.assertIsNone(instant_maths.calculate("1" * 300 + " + 1"))
        self.assertIsNotNone(instant_maths.calculate("2 ^ 100"))
        self.assertTrue(instant_maths.calculate("0 ^ -1").zero_division)
        self.assertTrue(instant_maths.calculate("5 / (2 - 2)").zero_division)
        self.assertEqual(instant_maths.calculate("2 ^ -2").value, "0.25")
        self.assertEqual(instant_maths.calculate("2 ^ 3 ^ 2").value, "512")              # right to left
        self.assertEqual(instant_maths.calculate("-2 ^ 2").value, "-4")
        self.assertEqual(instant_maths.calculate("10 - 2 - 3").value, "5")
        self.assertEqual(instant_maths.calculate("8 / 2 / 2").value, "2")

    def test_it_never_runs_python(self) -> None:
        for name in ("instant.py", "instant_maths.py"):
            tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
            called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name)}
            self.assertFalse(called & {"eval", "exec", "compile", "__import__"}, name)
            self.assertFalse(any(isinstance(node, (ast.Import, ast.ImportFrom)) and "ast" in
                                 [alias.name for alias in node.names] for node in ast.walk(tree)), name)
        for text in ("__import__('os').system('calc')", "1 + 1; import os", "().__class__", "lambda: 1",
                     "open('x')", "2 ** 2 ** 2 ** 2 ** 2"):
            with self.subTest(text=text):
                self.assertIsNone(instant_maths.calculate(text))

    def test_well_under_a_second(self) -> None:
        requests = ("what time is it", "what's 12 x 7", "5 km in miles", "days until christmas",
                    "what time is it in tokyo", "15% of 80", "what is the meaning of life", "set a timer for 10 minutes",
                    "100 dollars in euros", "draft an email to Ana about Friday's plan")
        started = time.perf_counter()
        for number in range(1000):
            ask(requests[number % len(requests)])
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
