"""Instant answers in the app (Qt): :class:`InstantController` takes what Ask hands it before Claude, and
:class:`TimerController` keeps Jarvis's one timer book (owner decisions 2026-11-10).

``InstantController.take(words, spoken)`` (AskController.submit asks it after a running task's gate, "web:" and the
memory, follow-up, plan and prep shortcuts, and before the desktop's words): True when Jarvis answers the request
himself. An answer on this PC comes at once (``answered``); a timer request goes to the TimerController; a network
question (instant_net: the weather, a currency) runs on a short-lived thread of its own - never the GUI thread -
with ``waiting`` saying so on the bar meanwhile, and comes back as ``answered``, or as ``fallback`` when the
service gave no answer for sure: the app then sends the words to Claude exactly as before. Nothing here logs a
request, a town, an amount or an answer: kinds, statuses and durations only.

Round 2 (owner decisions 2026-11-11), the calendar: a calendar question (instant.CalendarAsk) is answered from the
app's last read of every account's main calendar for today and tomorrow (instant_calendar), kept CALENDAR_KEEP_S (2
minutes) so a follow-up is instant. Older than that, ``calendar_read()`` (the app's: a job on its calendar worker,
which never signs in) reads again while ``waiting`` says "Checking your calendar..."; the app hands the result back
with ``calendar_done(read)``. No result within CALENDAR_WAIT_S (the worker may be busy with a sign-in): the
question goes to Claude (``fallback``). ``calendar_read`` None, or False when asked (the calendar is off or not set
up): Claude's, as before. ``forget_calendar()`` drops the copy (a card changed the calendar).

Round 2, the weather's town by voice or typing: "my town is San Jose, California" looks the place up on a thread of
its own (instant_net.find_place, the weather's own rule for places of one name), confirms the place it found and
keeps it in ``TownStore`` - instant.json in the data folder, on this PC only (it is text, so never assistant.json,
which holds no text) - where it wins over [instant] town; "what's my town" says it, "forget my town" clears it
(``townChanged`` then). ``settings_rows()`` is Settings > Instant answers (SETTINGS_KEY): what is on, the town and
the services the network answers use.

``TimerController``: the app's timers (desktop.pcctl.TimerBook in the data folder's "desktop" folder, so a restart
restores them; the desktop's HUD shares the same book while [desktop] is on). Set, asked about and cancelled through
``handle(TimerAsk)``; at each end ``ended(text, speech)`` - the app posts its toast, plays the chime and says it - and
``changed()`` whenever the timers change (the app's chips follow). A precise single-shot QTimer waits for the next
end and checks again at least every pcctl.TIMER_CHECK_S, so an end that came while the PC slept is said at the next
check. The timers live in the app, not in the window: hiding the window changes nothing.

Round 2: reminders at a clock time today or tomorrow (``handle_reminder(ReminderAsk)``: set, listed, cancelled) live
in the same book and file (pcctl.REMINDER_KIND). At a reminder's time: the chime, the toast with its label ("It's 5:00
PM: call mum") and his voice with no label ("Sir, your 5 PM reminder."). A timer's label is never said either (the
owner's words never reach the online voice). Home's chip shows whichever comes first: "Reminder 5:00 PM", or the
timer's countdown (``ticking()``: a countdown shows).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from . import instant, instant_calendar, instant_net
from .desktop import pcctl
from .instant import (CalendarAsk, InstantFlags, InstantReply, NetAsk, ReminderAsk, TimerAsk, TownAsk,
                      spoken_clock)
from .persona import with_address
from .text_prep import format_time

logger = logging.getLogger(__name__)

WAITING_TEXT = {instant.KIND_WEATHER: "Checking the weather...", instant.KIND_CURRENCY: "Checking the exchange rate...",
                instant.KIND_CALENDAR: "Checking your calendar...", instant.KIND_TOWN: "Looking the place up..."}
ON_THIS_PC = "on this PC"
CALENDAR_KEEP_S = 120.0          # a read of the calendars answers follow-ups this long (owner decision 2026-11-11)
CALENDAR_WAIT_S = 12.0           # a read that takes longer: the question goes to Claude
INSTANT_FILE = "instant.json"    # round 2: the weather's town set by voice or typing (text: never assistant.json)
SETTINGS_KEY = "instant"
SETTINGS_TITLE = "Instant answers"
SETTINGS_NOTE = ("Everyday questions Jarvis answers himself, typed or spoken, in well under a second and without your "
                 "Claude plan. Anything he can't answer for sure goes to Claude as before.")
SETTINGS_HOW = ("Set the weather's town by saying or typing \"my town is\" and its name; \"forget my town\" clears it. "
                "The rest is [instant] in config.toml (JARVIS_INSTANT_ENABLED in .env turns them all on or off), then "
                "restart Jarvis.")
_TOWN_FIELDS = ("name", "region", "country", "shown", "full", "query")


def instant_file(data_dir: Any) -> Path | None:
    """Where the town is kept: instant.json in the data folder; None without one - and for the app's real data folder
    while the tests run (they pass a temporary one)."""
    if not data_dir:
        return None
    path = Path(data_dir) / INSTANT_FILE
    if os.environ.get(instant_net.TEST_GUARD_ENV, "") == "1":
        from .config import default_data_dir         # late: the config module is large

        real = os.path.normcase(os.path.abspath(str(default_data_dir())))
        mine = os.path.normcase(os.path.abspath(str(path)))
        if mine.startswith(real + os.sep):
            logger.info("Instant: the real data folder is never used while the tests run; the town stays in memory")
            return None
    return path


class TownStore:
    """The weather's town set by voice or typing (owner decision 2026-11-11): ``place()`` (an instant_net.Place) kept
    in instant.json (``path``; None: memory only), read at the start. Never logged; ``saved`` is False after a save
    failed (it then lasts until Jarvis closes)."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._place: instant_net.Place | None = None
        self.saved = True
        self._load()

    def place(self) -> instant_net.Place | None:
        return self._place

    def set(self, place: instant_net.Place) -> bool:
        """Keep ``place`` (True: saved to the file)."""
        self._place = place
        return self._save()

    def forget(self) -> bool:
        """No town of its own any more (True: there was one)."""
        had = self._place is not None
        self._place = None
        if had:
            self._save()
        return had

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            town = json.loads(self.path.read_text(encoding="utf-8")).get("town")
            if town is None:
                return
            texts = {name: town[name] for name in _TOWN_FIELDS}
            if not all(isinstance(value, str) and len(value) <= 160 and value.isprintable() for value in texts.values()) \
                    or not texts["query"] or not texts["shown"]:
                raise ValueError("not a town")
            latitude, longitude = (instant_net._number(town[name]) for name in ("latitude", "longitude"))
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError("not on Earth")
            self._place = instant_net.Place(texts["name"], texts["region"], texts["country"], latitude, longitude,
                                            texts["shown"], texts["full"], texts["query"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Instant: the saved town could not be read (%s); none is used", type(exc).__name__)
            self._place = None

    def _save(self) -> bool:
        """The file rewritten (a new file put in place: never half written)."""
        if self.path is None:
            return True
        place = self._place
        content: dict[str, Any] = {"version": 1}
        if place is not None:
            content["town"] = {**{name: getattr(place, name) for name in _TOWN_FIELDS},
                               "latitude": place.latitude, "longitude": place.longitude}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".new")
            temp.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
            self.saved = True
        except OSError as exc:
            self.saved = False
            logger.warning("Instant: the town could not be saved (%s %s)", type(exc).__name__,
                           getattr(exc, "winerror", "") or "")
        return self.saved


class _Bridge(QObject):
    done = Signal(object, str, bool, float, str)       # reply or None, words, spoken, started, kind
    town = Signal(str, object, str, str, bool, float)  # PLACE_*, Place or candidates, town, words, spoken, started


class InstantController(QObject):
    answered = Signal(object, str, bool, int)          # InstantReply, the words, spoken, milliseconds
    waiting = Signal(str)                              # the bar's words while a network answer is on its way
    fallback = Signal(str, bool)                       # the words, spoken: no answer for sure - Claude, as before
    townChanged = Signal()                             # round 2: the town was set or forgotten (Settings follows)

    def __init__(self, config: Any, *, timers: TimerController | None = None,
                 now: Callable[[], datetime] | None = None,
                 get: Callable[[str, Any, float], Any] | None = None, fahrenheit: bool | None = None,
                 threaded: bool = True, calendar_read: Callable[[], bool] | None = None,
                 monotonic: Callable[[], float] = time.monotonic, store: TownStore | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self.timers = timers
        self._now = now or (lambda: datetime.now().astimezone())
        self._get = get or instant_net.real_get
        self._cache = instant_net.NetCache()
        temperature = getattr(config.instant, "temperature", "auto")
        self._fahrenheit = fahrenheit if fahrenheit is not None else (
            temperature == "fahrenheit" or (temperature == "auto" and instant_net.fahrenheit_region()))
        self._threaded = threaded
        self._shut = False
        self._bridge = _Bridge(self)
        self._bridge.done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)
        # Round 2: the calendars (the app's read; the last one kept CALENDAR_KEEP_S) and the questions waiting for one.
        self._calendar_read = calendar_read if getattr(config.instant, "calendar", True) else None
        self._monotonic = monotonic
        self._calendar_copy: tuple[float, instant_calendar.CalendarRead] | None = None
        self._calendar_waiting: list[tuple[CalendarAsk, str, bool, float]] = []
        self._calendar_timer = QTimer(self)
        self._calendar_timer.setSingleShot(True)
        self._calendar_timer.timeout.connect(self._calendar_late)
        # Round 2: the weather's town set by voice or typing (wins over [instant] town); its place needs no search.
        self.store = store if store is not None else TownStore(None)
        self._bridge.town.connect(self._on_town, Qt.ConnectionType.QueuedConnection)
        if self.store.place() is not None:
            instant_net.remember_place(self._cache, self.store.place())

    def flags(self) -> InstantFlags:
        cfg = self._config.instant
        place = self.store.place()
        return InstantFlags(weather=bool(cfg.weather), currency=bool(cfg.currency), timers=self.timers is not None,
                            town=place.query if place is not None else str(cfg.town or ""),
                            calendar=self._calendar_read is not None)

    def _address(self) -> str:
        return str(getattr(getattr(self._config, "assistant", None), "address", "sir"))

    def _hour24(self) -> bool:
        return bool(getattr(getattr(self._config, "display", None), "hour24", False))

    def take(self, words: str, spoken: bool = False) -> bool:
        """True when Jarvis answers ``words`` himself (see the module docs); False: Claude's, as before."""
        if self._shut or not words:
            return False
        started = time.perf_counter()
        try:
            found = instant.understand(words, now=self._now(), flags=self.flags(), address=self._address(),
                                       hour24=self._hour24())
        except Exception as exc:  # noqa: BLE001 - a request that can't be read is Claude's, as before
            logger.warning("Instant: a request could not be read (%s)", type(exc).__name__)
            return False
        if found is None:
            return False
        if isinstance(found, CalendarAsk):
            return self._ask_calendar(found, words, spoken, started)
        if isinstance(found, TownAsk):
            return self._ask_town(found, words, spoken, started)
        if isinstance(found, (TimerAsk, ReminderAsk)):
            if self.timers is None:
                return False
            reply = (self.timers.handle(found) if isinstance(found, TimerAsk) else
                     self.timers.handle_reminder(found))
            if reply is None:
                return False
            found = reply
        if isinstance(found, InstantReply):
            self._answer(found, words, spoken, started)
            return True
        if isinstance(found, NetAsk):
            self.waiting.emit(WAITING_TEXT.get(found.kind, "One moment..."))
            self._ask_network(found, words, spoken, started)
            return True
        return False

    def _answer(self, reply: InstantReply, words: str, spoken: bool, started: float) -> None:
        ms = max(0, int((time.perf_counter() - started) * 1000))
        logger.info("Instant: a %s answer (%d ms%s)", reply.kind, ms, ", spoken" if spoken else "")
        self.answered.emit(reply, words, spoken, ms)

    def _ask_network(self, ask: NetAsk, words: str, spoken: bool, started: float) -> None:
        def work() -> None:
            reply = instant_net.answer(ask, get=self._get, cache=self._cache, fahrenheit=self._fahrenheit,
                                       address=self._address())
            if not self._shut:
                self._bridge.done.emit(reply, words, spoken, started, ask.kind)

        if self._threaded:
            threading.Thread(target=work, name="instant-net", daemon=True).start()
        else:
            work()

    def _on_done(self, reply: Any, words: str, spoken: bool, started: float, kind: str) -> None:
        if self._shut:
            return
        if reply is None:
            logger.info("Instant: no %s answer; the request goes to Claude", kind)
            self.fallback.emit(words, spoken)
            return
        self._answer(reply, words, spoken, started)

    # ---- round 2: the calendars ---------------------------------------------------------------------------------

    def _calendar_reply(self, ask: CalendarAsk, read: instant_calendar.CalendarRead) -> InstantReply | None:
        try:
            return instant_calendar.reply(ask, read, now=self._now(), address=self._address(), hour24=self._hour24())
        except Exception as exc:  # noqa: BLE001 - an answer that can't be made is Claude's, as before
            logger.warning("Instant: a calendar answer could not be made (%s)", type(exc).__name__)
            return None

    def _ask_calendar(self, ask: CalendarAsk, words: str, spoken: bool, started: float) -> bool:
        """At once from a read under CALENDAR_KEEP_S old, else after a new read (``waiting`` meanwhile)."""
        copy = self._calendar_copy
        if copy is not None and self._monotonic() - copy[0] < CALENDAR_KEEP_S:
            reply = self._calendar_reply(ask, copy[1])
            if reply is not None:
                self._answer(reply, words, spoken, started)
                return True
        if self._calendar_read is None:
            return False
        if not self._calendar_waiting:
            try:
                asked = bool(self._calendar_read())
            except Exception as exc:  # noqa: BLE001 - no read: Claude's, as before
                logger.warning("Instant: the calendars could not be asked for (%s)", type(exc).__name__)
                asked = False
            if not asked:
                return False                                     # off or not set up: Claude's, as before
            self._calendar_timer.start(int(CALENDAR_WAIT_S * 1000))
        self._calendar_waiting.append((ask, words, spoken, started))
        self.waiting.emit(WAITING_TEXT[instant.KIND_CALENDAR])
        return True

    def calendar_done(self, read: instant_calendar.CalendarRead) -> None:
        """The app's read of the calendars came back (GUI thread): kept while at least one account was read, and every
        question waiting for it answered."""
        if self._shut:
            return
        self._calendar_timer.stop()
        if any(alias not in read.problems for alias in read.events):
            self._calendar_copy = (self._monotonic(), read)
        waiting, self._calendar_waiting = self._calendar_waiting, []
        for ask, words, spoken, started in waiting:
            reply = self._calendar_reply(ask, read)
            if reply is None:
                logger.info("Instant: the calendar read did not cover the question; it goes to Claude")
                self.fallback.emit(words, spoken)
            else:
                self._answer(reply, words, spoken, started)

    def _calendar_late(self) -> None:
        waiting, self._calendar_waiting = self._calendar_waiting, []
        if waiting and not self._shut:
            logger.info("Instant: the calendars did not answer within %d s; %d question(s) go to Claude",
                        int(CALENDAR_WAIT_S), len(waiting))
            for _ask, words, spoken, _started in waiting:
                self.fallback.emit(words, spoken)

    def forget_calendar(self) -> None:
        """The calendar changed (a card's event): the next question reads it again."""
        self._calendar_copy = None

    # ---- round 2: the weather's town ----------------------------------------------------------------------------

    def _say(self, template: str) -> str:
        return with_address(template, self._address())

    def _town_now(self) -> tuple[str, str]:
        """(the town in use as shown, where it comes from), ("", "") without one."""
        place = self.store.place()
        if place is not None:
            return place.full or place.shown, "you set it"
        town = " ".join(str(getattr(self._config.instant, "town", "") or "").split())
        return (town, "from config.toml") if town else ("", "")

    def _ask_town(self, ask: TownAsk, words: str, spoken: bool, started: float) -> bool:
        if ask.action == instant.TOWN_SAY:
            town, where = self._town_now()
            if town:
                reply = InstantReply(instant.KIND_TOWN, f"Your weather town is {town} ({where}).",
                                     self._say(f"Your weather town is {town}, {{sir}}."))
            else:
                reply = InstantReply(instant.KIND_TOWN, "No weather town is set. Say or type \"my town is\" and its "
                                     "name.", self._say("You haven't set a town yet, {sir}."))
            self._answer(reply, words, spoken, started)
            return True
        if ask.action == instant.TOWN_FORGET:
            had = self.store.forget()
            if had:
                self.townChanged.emit()
                logger.info("Instant: the weather town was forgotten")
            town, _where = self._town_now()
            after = f" The weather now uses {town} from config.toml." if town else ""
            text = ("Forgot your weather town." if had else "You hadn't set a weather town.") + after
            reply = InstantReply(instant.KIND_TOWN, text, self._say("Done, {sir}: I've forgotten your town." if had
                                                                    else "You hadn't set a town, {sir}."))
            self._answer(reply, words, spoken, started)
            return True
        town = ask.town

        def work() -> None:
            status, found = instant_net.find_place(town, get=self._get, cache=self._cache)
            if not self._shut:
                self._bridge.town.emit(status, found, town, words, spoken, started)

        self.waiting.emit(WAITING_TEXT[instant.KIND_TOWN])
        if self._threaded:
            threading.Thread(target=work, name="instant-town", daemon=True).start()
        else:
            work()
        return True

    def _on_town(self, status: str, found: Any, town: str, words: str, spoken: bool, started: float) -> None:
        """The place search came back (GUI thread): the town kept and confirmed, or plainly why not."""
        if self._shut:
            return
        unchanged = " Your weather town wasn't changed."
        if status == instant_net.PLACE_FOUND and isinstance(found, instant_net.Place):
            saved = self.store.set(found)
            self.townChanged.emit()
            logger.info("Instant: the weather town was set%s", "" if saved else " (kept in memory only)")
            kept = "" if saved else " (It lasts until Jarvis closes: the data folder couldn't be written.)"
            reply = InstantReply(instant.KIND_TOWN, f"Got it: {found.full}.{kept}",
                                 self._say(f"Got it, {{sir}}: {found.shown}."), instant_net.PLACE_CREDIT)
        elif status == instant_net.PLACE_SEVERAL:
            names = "; ".join(found or ())
            reply = InstantReply(instant.KIND_TOWN,
                                 f"There's more than one {town}: {names}. Say the one you mean with its state or "
                                 f"country, like \"my town is {(found or [town])[0]}\".{unchanged}",
                                 self._say(f"There's more than one {town}, {{sir}}. Which state or country?"),
                                 instant_net.PLACE_CREDIT)
        elif status == instant_net.PLACE_NONE:
            reply = InstantReply(instant.KIND_TOWN, f"I couldn't find a place called {town}.{unchanged}",
                                 self._say(f"I couldn't find {town}, {{sir}}."), instant_net.PLACE_CREDIT)
        else:
            reply = InstantReply(instant.KIND_TOWN, f"The place search couldn't be reached; try again in a moment."
                                 f"{unchanged}", self._say("I couldn't reach the place search, {sir}."))
        self._answer(reply, words, spoken, started)

    def settings_rows(self) -> list[tuple[str, str]]:
        """Settings > Instant answers: what is on, the weather's town and the services the network answers use."""
        cfg = self._config.instant
        rows = [("On this PC", "The time and dates, sums and conversions, timers and reminders - none of it leaves "
                               "this PC")]
        if self._calendar_read is not None:
            calendar = ("On - every account's main calendar, read when you ask and kept 2 minutes; titles are shown, "
                        "never said")
        elif not getattr(cfg, "calendar", True):
            calendar = "Off - [instant] calendar"
        else:
            calendar = "Off - Google Calendar is off or not set up"
        rows.append(("Your calendars", calendar))
        rows.append(("Weather", "On - Open-Meteo (open-meteo.com): a town's name, then its coordinates" if cfg.weather
                     else "Off - [instant] weather"))
        town, where = self._town_now()
        rows.append(("Weather town", f"{town} - {where}" if town else
                     "None - say or type \"my town is\" and its name"))
        temperature = getattr(cfg, "temperature", "auto")
        rows.append(("Temperature", {"celsius": "Celsius", "fahrenheit": "Fahrenheit"}.get(
            temperature, f"Auto - {'Fahrenheit' if self._fahrenheit else 'Celsius'} (the Windows region)")))
        rows.append(("Currencies", "On - Frankfurter (frankfurter.dev): the European Central Bank's daily rates"
                     if cfg.currency else "Off - [instant] currency"))
        return rows

    def shutdown(self) -> None:
        self._shut = True
        self._calendar_timer.stop()
        self._calendar_waiting = []
        if self.timers is not None:
            self.timers.shutdown()


def _length_name(seconds: int) -> str:
    """"10-minute", "90-second", "2-hour", "1 hour 30 minutes" (a timer's length as said before "timer")."""
    if seconds % 3600 == 0:
        return f"{seconds // 3600}-hour"
    if seconds < 3600 and seconds % 60 == 0:
        return f"{seconds // 60}-minute"
    if seconds < 60:
        return f"{seconds}-second"
    return instant.duration_words(seconds)


def _left_words(seconds: float) -> str:
    whole = max(0, int(round(seconds)))
    return instant.duration_words(whole) if whole else "less than a second"


def _clock_left(seconds: float) -> str:
    """"4:59" / "1:02:03" for the chip."""
    whole = max(0, int(seconds + 0.999))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _chime() -> None:
    """A timer's end sound: Windows' own "asterisk" sound, not waited for. Never while the tests run."""
    if os.environ.get(instant_net.TEST_GUARD_ENV, "") == "1":
        return
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception as exc:  # noqa: BLE001 - the toast and his voice still say it
        logger.info("Instant: no timer sound (%s)", type(exc).__name__)


class TimerController(QObject):
    ended = Signal(str, str)        # the end's words: shown (the toast) and said
    changed = Signal()

    def __init__(self, folder: str | Path | None, *, address: str = "sir", hour24: bool = False,
                 now: Callable[[], datetime] | None = None, chime: Callable[[], None] | None = None,
                 local: Callable[[datetime], datetime] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.book = pcctl.TimerBook(folder, now=now or (lambda: datetime.now(timezone.utc)))
        self._address = address
        self._hour24 = hour24
        self.chime = chime or _chime
        self._local = local or (lambda moment: moment.astimezone())    # a moment on this PC's wall clock
        self._shut = False
        self._check = QTimer(self)
        self._check.setSingleShot(True)
        self._check.setTimerType(Qt.TimerType.PreciseTimer)
        self._check.timeout.connect(self.check)

    def start(self) -> None:
        """The timers kept from before (one whose end came while Jarvis was closed is said now, or dropped quietly
        when that was more than pcctl.TIMER_LATE_S ago), then the next check."""
        count = self.book.load()
        if count:
            logger.info("Instant: %d timer(s) restored", count)
        self.check()
        if count:
            self.changed.emit()                          # the chips show what was restored

    def poke(self) -> None:
        """The shared book changed elsewhere (the desktop's HUD set or cancelled a timer): check again and say so."""
        if not self._shut:
            self._arm()
            self.changed.emit()

    def running(self) -> tuple[pcctl.Timer, ...]:
        """The timers still to run (reminders are ``reminders()``)."""
        return self.book.running(pcctl.TIMER_KIND)

    def reminders(self) -> tuple[pcctl.Timer, ...]:
        return self.book.running(pcctl.REMINDER_KIND)

    def _say(self, template: str) -> str:
        return with_address(template, self._address)

    def _ends_at(self, timer: pcctl.Timer) -> str:
        return format_time(self._local(timer.ends_at), hour24=self._hour24)

    def handle(self, ask: TimerAsk) -> InstantReply | None:
        """The answer to a timer request (None: not Jarvis's to answer - a bare "how much time is left" with no
        timer running goes to Claude)."""
        if self._shut:
            return None
        if ask.action == instant.TIMER_SET:
            return self._set(ask)
        timers = self.running()
        if ask.action == instant.TIMER_LEFT:
            if not timers:
                if ask.bare:
                    return None
                return InstantReply(instant.KIND_TIMER, "No timer is running.", self._say("No timer is running, {sir}."))
            return self._left(timers)
        if not timers:
            return InstantReply(instant.KIND_TIMER, "No timer is running; nothing was cancelled.",
                                self._say("No timer is running, {sir}."))
        if ask.every:
            gone = self.book.cancel_all(pcctl.TIMER_KIND)
            self._changed()
            count = len(gone)
            noun = "timer" if count == 1 else f"{count} timers"
            return InstantReply(instant.KIND_TIMER, f"Cancelled {'your' if count == 1 else 'all'} "
                                f"{noun if count == 1 else noun}.", self._say(f"Cancelled {noun}, {{sir}}."))
        picked = [timer for timer in timers if not ask.seconds or timer.length_s == ask.seconds]
        if not picked:
            return InstantReply(instant.KIND_TIMER, f"No {_length_name(ask.seconds)} timer is running; nothing was "
                                f"cancelled.", self._say(f"No {_length_name(ask.seconds)} timer is running, {{sir}}."))
        if len(picked) > 1 and not ask.seconds:
            return InstantReply(instant.KIND_TIMER,
                                f"{len(timers)} timers are running; say \"cancel all timers\" or name one, like "
                                f"\"cancel the {_length_name(picked[0].length_s)} timer\". Nothing was cancelled.",
                                self._say(f"You have {len(timers)} timers, {{sir}}. Which one?"))
        timer = picked[0]
        self.book.cancel(timer.timer_id)
        self._changed()
        logger.info("Instant: a timer was cancelled")
        name = _length_name(timer.length_s)
        return InstantReply(instant.KIND_TIMER, f"Cancelled your {name} timer.",
                            self._say(f"Cancelled your {name} timer, {{sir}}."))

    def _set(self, ask: TimerAsk) -> InstantReply | None:
        try:
            timer = self.book.add_seconds(ask.seconds, ask.label)
        except pcctl.TimersFull:
            return InstantReply(instant.KIND_TIMER, f"{pcctl.MAX_TIMERS} timers are running already; cancel one "
                                f"first. Nothing was set.", self._say(f"You already have {pcctl.MAX_TIMERS} timers, "
                                                                       f"{{sir}}."))
        except ValueError:
            return None
        self._changed()
        logger.info("Instant: a %d-second timer was set%s", timer.length_s, "" if self.book.saved else
                    " (kept in memory only)")
        length = instant.duration_words(timer.length_s)
        label = f" for {timer.label}" if timer.label else ""
        return InstantReply(instant.KIND_TIMER, f"Timer set for {length}{label}; it ends at {self._ends_at(timer)}.",
                            self._say(f"Timer set for {length}, {{sir}}."))

    def _left(self, timers: tuple[pcctl.Timer, ...]) -> InstantReply:
        now = self.book.now()
        parts = []
        for timer in timers:
            left = max(0.0, (timer.ends_at - now).total_seconds())
            parts.append((timer, left))
        first, left = parts[0]
        name = _length_name(first.length_s)
        if len(parts) == 1:
            label = f" for {first.label}" if first.label else ""
            return InstantReply(instant.KIND_TIMER,
                                f"{_left_words(left)} left on your {name} timer{label} (it ends at "
                                f"{self._ends_at(first)}).",
                                self._say(f"{_left_words(left)} left, {{sir}}."))
        lines = "; ".join(f"{_left_words(seconds)} on the {_length_name(timer.length_s)} one" for timer, seconds in parts)
        return InstantReply(instant.KIND_TIMER, f"{len(parts)} timers: {lines}.",
                            self._say(f"{len(parts)} timers, {{sir}}; the next ends in {_left_words(left)}."))

    # ---- round 2: reminders at a clock time -------------------------------------------------------------------

    def _when(self, moment: datetime) -> tuple[str, str]:
        """A reminder's time (shown, said): "5:00 PM" / "5 PM", with " tomorrow" when it is tomorrow's."""
        local, today = self._local(moment), self._local(self.book.now()).date()
        day = "" if local.date() == today else " tomorrow" if local.date() == today + timedelta(days=1) else \
            f" on {local:%A}"
        return format_time(local, hour24=self._hour24) + day, spoken_clock(local) + day

    def handle_reminder(self, ask: ReminderAsk) -> InstantReply | None:
        """The answer to a reminder request (instant.ReminderAsk): set, listed or cancelled. Never logged: the label
        or the time."""
        if self._shut:
            return None
        if ask.action == instant.REMINDER_SET:
            return self._remind(ask)
        reminders = self.reminders()
        if ask.action == instant.REMINDER_LIST:
            return self._list_reminders(reminders)
        return self._cancel_reminder(ask, reminders)

    def _remind(self, ask: ReminderAsk) -> InstantReply | None:
        if ask.at is None:
            return None
        try:
            reminder = self.book.add_until(ask.at, ask.label)
        except pcctl.RemindersFull:
            return InstantReply(instant.KIND_REMINDER, f"{pcctl.MAX_REMINDERS} reminders are set already; cancel one "
                                f"first. Nothing was set.", self._say(f"You already have {pcctl.MAX_REMINDERS} "
                                                                       f"reminders, {{sir}}."))
        except ValueError:
            return InstantReply(instant.KIND_REMINDER, "That time has passed; nothing was set.",
                                self._say("That time has passed, {sir}."))
        self._changed()
        logger.info("Instant: a reminder was set (%d min ahead%s)", reminder.minutes,
                    "" if self.book.saved else ", kept in memory only")
        shown, said = self._when(reminder.ends_at)
        label = f": {reminder.label}" if reminder.label else ""
        return InstantReply(instant.KIND_REMINDER, f"Reminder set for {shown}{label}.",
                            self._say(f"Reminder set for {said}, {{sir}}."))

    def _timers_note(self) -> str:
        count = len(self.running())
        if not count:
            return ""
        return (f" {count} timer{'s' if count != 1 else ''} {'is' if count == 1 else 'are'} running: say \"how long "
                f"is left\".")

    def _list_reminders(self, reminders: tuple[pcctl.Timer, ...]) -> InstantReply:
        if not reminders:
            return InstantReply(instant.KIND_REMINDER, "No reminders are set." + self._timers_note(),
                                self._say("You have no reminders, {sir}."))
        lines = []
        for reminder in reminders:
            shown, _said = self._when(reminder.ends_at)
            lines.append(f"• {shown}" + (f" - {reminder.label}" if reminder.label else ""))
        first = self._when(reminders[0].ends_at)[1]
        count = len(reminders)
        said = (f"You have one reminder, {{sir}}, at {first}." if count == 1 else
                f"You have {count} reminders, {{sir}}; the next is at {first}.")
        return InstantReply(instant.KIND_REMINDER, f"{count} reminder{'s' if count != 1 else ''}:\n" + "\n".join(lines),
                            self._say(said))

    def _cancel_reminder(self, ask: ReminderAsk, reminders: tuple[pcctl.Timer, ...]) -> InstantReply:
        if not reminders:
            return InstantReply(instant.KIND_REMINDER, "No reminder is set; nothing was cancelled." + self._timers_note(),
                                self._say("You have no reminders, {sir}."))
        if ask.every:
            gone = self.book.cancel_all(pcctl.REMINDER_KIND)
            self._changed()
            logger.info("Instant: %d reminder(s) cancelled", len(gone))
            count = len(gone)
            words = "your reminder" if count == 1 else f"all {count} reminders"
            return InstantReply(instant.KIND_REMINDER, f"Cancelled {words}.", self._say(f"Cancelled {words}, {{sir}}."))
        picked = list(reminders)
        if ask.clocks:
            today = self._local(self.book.now()).date()
            picked = [reminder for reminder in reminders
                      if (self._local(reminder.ends_at).hour, self._local(reminder.ends_at).minute) in ask.clocks
                      and (ask.day < 0 or self._local(reminder.ends_at).date() == today + timedelta(days=ask.day))]
            if not picked:
                hour, minute = ask.clocks[-1]
                asked = spoken_clock(datetime(2000, 1, 1, hour, minute))
                return InstantReply(instant.KIND_REMINDER, f"No reminder is set for {asked}; nothing was cancelled.",
                                    self._say(f"No reminder at {asked}, {{sir}}."))
        if len(picked) > 1:
            example = self._when(picked[0].ends_at)[1].split(" tomorrow")[0]
            return InstantReply(instant.KIND_REMINDER,
                                f"{len(picked)} reminders are set; say \"cancel the {example} reminder\" or \"cancel "
                                f"all reminders\". Nothing was cancelled.",
                                self._say(f"You have {len(picked)} reminders, {{sir}}. Which one?"))
        reminder = picked[0]
        self.book.cancel(reminder.timer_id)
        self._changed()
        logger.info("Instant: a reminder was cancelled")
        shown, said = self._when(reminder.ends_at)
        label = f" ({reminder.label})" if reminder.label else ""
        return InstantReply(instant.KIND_REMINDER, f"Cancelled your {shown} reminder{label}.",
                            self._say(f"Cancelled your {said} reminder, {{sir}}."))

    def chip_text(self) -> str:
        """Home's chip: whichever comes first - a timer's countdown ("Timer 4:59", "2 timers · 4:59") or the next
        reminder ("Reminder 5:00 PM", "Reminder 9:00 AM tomorrow"); "" without either."""
        timers, reminders = self.running(), self.reminders()
        if reminders and (not timers or reminders[0].ends_at < timers[0].ends_at):
            return f"Reminder {self._when(reminders[0].ends_at)[0]}"
        if not timers:
            return ""
        now = self.book.now()
        left = max(0.0, (timers[0].ends_at - now).total_seconds())
        return f"Timer {_clock_left(left)}" if len(timers) == 1 else f"{len(timers)} timers · {_clock_left(left)}"

    def ticking(self) -> bool:
        """Home's chip counts down (a timer comes first): it changes every second."""
        timers, reminders = self.running(), self.reminders()
        return bool(timers) and not (reminders and reminders[0].ends_at < timers[0].ends_at)

    def _reminder_end(self, reminder: pcctl.Timer, late: float) -> tuple[str, str]:
        """A reminder's time came: the toast's words (its label) and his (no label: "Sir, your 5 PM reminder.")."""
        logger.info("Instant: a reminder came (%d s late)", int(late))
        local = self._local(reminder.ends_at)
        shown, said = format_time(local, hour24=self._hour24), spoken_clock(local)
        if late > 90:
            text = (f"Your {shown} reminder, while Jarvis was away: {reminder.label}." if reminder.label else
                    f"Your {shown} reminder came while Jarvis was away.")
            return text, self._say(f"{{Sir}}, your {said} reminder came while I was away.")
        text = f"It's {shown}: {reminder.label}." if reminder.label else f"It's {shown}: your reminder."
        return text, self._say(f"{{Sir}}, your {said} reminder.")

    def check(self) -> None:
        """The timers against the wall clock: each that has ended says so once (``ended``), then the next check."""
        if self._shut:
            return
        ended, dropped = self.book.take_due()
        if dropped:
            logger.info("Instant: %d timer(s) ended long ago while Jarvis was closed; dropped", dropped)
        for timer, late in ended:
            if timer.reminder:
                text, said = self._reminder_end(timer, late)
            else:
                logger.info("Instant: a %d-second timer ended (%d s late)", timer.length_s, int(late))
                name = _length_name(timer.length_s)
                label = f" for {timer.label}" if timer.label else ""
                if late > 90:
                    text = f"Your {name} timer{label} ended at {self._ends_at(timer)}, while Jarvis was away."
                    said = self._say(f"Your {name} timer ended a while ago, {{sir}}.")
                else:
                    text = f"Your {name} timer{label} is done."
                    said = self._say(f"Your {name} timer is done, {{sir}}.")    # its label is shown, never said
            try:
                self.chime()
            except Exception as exc:  # noqa: BLE001 - the toast and his voice still say it
                logger.info("Instant: no timer sound (%s)", type(exc).__name__)
            self.ended.emit(text, said)
        if ended or dropped:
            self._changed()
        else:
            self._arm()

    def _changed(self) -> None:
        self._arm()
        self.changed.emit()

    def _arm(self) -> None:
        if self._shut:
            return
        left = self.book.seconds_left()
        if left is None:
            self._check.stop()
        else:
            self._check.start(int(min(left, pcctl.TIMER_CHECK_S) * 1000) + 20)

    def shutdown(self) -> None:
        """Jarvis closes: the checks stop; the timers stay in their file for the next start."""
        self._shut = True
        self._check.stop()
