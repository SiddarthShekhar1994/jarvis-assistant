"""When a standing job is due (no Qt, no clock of its own: ``now`` is always passed in, as an aware local time).

    slot_key(day, hhmm)          a timed slot's key, "YYYY-MM-DD HH:MM": its LOCAL date and wall-clock time, so a
                                 daylight-saving change neither skips a run (02:30 on the night the clocks go
                                 forward runs at the first check after 03:00) nor repeats one (01:30 twice in the
                                 night they go back is one key)
    latest_slot(trigger, now)    the newest slot at or before ``now`` on an allowed weekday (its key and naive
                                 wall-clock moment), or None
    due_time(trigger, now, last_key, catch_up_hours) -> Due
                                 a timed job: DUE when the latest slot is newer than ``last_key`` and at most
                                 ``catch_up_hours`` old (at least CHECK_WINDOW_MIN: a check comes every 5
                                 minutes); MISSED when it is older; else NOT_DUE. ``last_key`` "" (a job seen for
                                 the first time) is a BASELINE: the latest slot is taken as done, nothing runs
    due_every(trigger, now, last_run) -> Due
                                 an interval job: DUE once ``every_minutes`` passed since its last run, however long
                                 the PC slept (one catch-up run, never a backlog); no last run is a BASELINE
    next_time(trigger, now) / next_every(trigger, last_run, now)   the next run to show ("Next: Tue 8:00 AM")
    in_quiet(now, quiet_from, quiet_until)    quiet hours (they may run past midnight; from == until: never quiet)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .model import TRIGGER_EVERY, TRIGGER_TIME, Trigger

NOT_DUE, DUE, MISSED, BASELINE = "not_due", "due", "missed", "baseline"
CHECK_WINDOW_MIN = 10        # a slot is still on time this many minutes late even with catch_up_hours 0
_LOOKBACK_DAYS = 8


@dataclass(frozen=True)
class Due:
    state: str                 # NOT_DUE, DUE, MISSED or BASELINE
    key: str = ""              # the slot's key (timed jobs), to record as the job's last due key

    @property
    def runs(self) -> bool:
        return self.state == DUE


def slot_key(day: date, hhmm: str) -> str:
    return f"{day.isoformat()} {hhmm}"


def _hhmm(text: str) -> time:
    hours, _, minutes = text.partition(":")
    return time(int(hours), int(minutes))


def _naive(now: datetime) -> datetime:
    """``now`` as local wall-clock time without a zone (how slots are compared)."""
    if now.tzinfo is not None:
        now = now.astimezone()
    return now.replace(tzinfo=None, fold=0)


def _allowed(trigger: Trigger, day: date) -> bool:
    return not trigger.weekdays or day.weekday() in trigger.weekdays


def latest_slot(trigger: Trigger, now: datetime) -> tuple[str, datetime] | None:
    if trigger.kind != TRIGGER_TIME or not trigger.times:
        return None
    wall = _naive(now)
    for back in range(_LOOKBACK_DAYS):
        day = wall.date() - timedelta(days=back)
        if not _allowed(trigger, day):
            continue
        for hhmm in sorted(trigger.times, reverse=True):
            moment = datetime.combine(day, _hhmm(hhmm))
            if moment <= wall:
                return slot_key(day, hhmm), moment
    return None


def due_time(trigger: Trigger, now: datetime, last_key: str, catch_up_hours: int) -> Due:
    found = latest_slot(trigger, now)
    if found is None:
        return Due(NOT_DUE)
    key, moment = found
    if not last_key:
        return Due(BASELINE, key)
    if key <= last_key:
        return Due(NOT_DUE)
    window = timedelta(minutes=max(CHECK_WINDOW_MIN, int(catch_up_hours) * 60))
    if _naive(now) - moment > window:
        return Due(MISSED, key)
    return Due(DUE, key)


def due_every(trigger: Trigger, now: datetime, last_run: datetime | None) -> Due:
    if trigger.kind != TRIGGER_EVERY or trigger.every_minutes <= 0:
        return Due(NOT_DUE)
    if last_run is None:
        return Due(BASELINE)
    if now - last_run >= timedelta(minutes=trigger.every_minutes):
        return Due(DUE)
    return Due(NOT_DUE)


def next_time(trigger: Trigger, now: datetime) -> datetime | None:
    """The next slot after ``now`` (an aware local time), or None."""
    if trigger.kind != TRIGGER_TIME or not trigger.times:
        return None
    wall = _naive(now)
    for ahead in range(_LOOKBACK_DAYS + 1):
        day = wall.date() + timedelta(days=ahead)
        if not _allowed(trigger, day):
            continue
        for hhmm in sorted(trigger.times):
            moment = datetime.combine(day, _hhmm(hhmm))
            if moment > wall:
                return moment.astimezone()
    return None


def next_every(trigger: Trigger, last_run: datetime | None, now: datetime) -> datetime | None:
    if trigger.kind != TRIGGER_EVERY or trigger.every_minutes <= 0:
        return None
    if last_run is None:
        return now + timedelta(minutes=trigger.every_minutes)
    return max(now, last_run + timedelta(minutes=trigger.every_minutes))


def windows_busy() -> bool:
    """Windows says the owner is busy (SHQueryUserNotificationState): presentation mode, a full-screen program or
    game, quiet time, or nobody present. False off Windows, while the tests run, or when it can't be asked."""
    import os
    import sys

    if sys.platform != "win32" or os.environ.get("JARVIS_DESKTOP_TEST_GUARD") == "1":
        return False
    try:
        import ctypes

        state = ctypes.c_int(0)
        result = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except Exception:  # noqa: BLE001 - unknown: not busy
        return False
    # 1 not present, 2 busy, 3 full-screen Direct3D, 4 presentation mode, 5 accepts notifications, 6 quiet time,
    # 7 a full-screen app.
    return result == 0 and state.value in (1, 2, 3, 4, 6, 7)


def in_quiet(now: datetime, quiet_from: str, quiet_until: str) -> bool:
    try:
        start, end = _hhmm(quiet_from), _hhmm(quiet_until)
    except (ValueError, TypeError):
        return False
    if start == end:
        return False
    moment = _naive(now).time()
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end
