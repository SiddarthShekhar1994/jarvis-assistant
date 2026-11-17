"""Fakes for the second-screen display's tests and stills (the new look, part 7). Nothing here reads the real monitors,
calls Win32 or shows a window on a real screen; every name, address and word is invented (the design's demo content).

    spots(*sizes)                ScreenSpots side by side, left to right ("1920x1080", "2560x1440" ...): a fake
                                 screen list for SecondScreenController(screens=...)
    FakeNative                   the display's OS seam, recording every call (no_activate, exclude_from_capture,
                                 watch_power) and answering the power probes from its own flags; ``refuse`` makes the
                                 capture exclusion fail as Windows would
    FakeProbes                   HideProbes from a dict of flags (locked, display_off, battery_saver, full_screen)
    FakeClock                    a monotonic clock a test steps by hand
    RecordingPainter             a QPainter that keeps every text it draws (glance's tiles and the display's own lines)
    demo_frames(marker="")       the still's nine frames as the board's page gets them (board.protocol checked), in the
                                 still's order; ``marker`` goes into the mail thread's text (the privacy tests)
    outgoing_frame(marker)       a reply that waits on Approvals, as the mail it sends (its text holds ``marker``)
    followups_frame(marker)      the follow-ups as a mail list, each item's own words (its reason, its address)
                                 holding ``marker``
    demo_state(frames=None, **)  the still's DisplayState (needs you, 1 waiting, the briefing, 9 frames, 1 pinned, the
                                 next event at 10:00)
    NOW                          Tue 17 Nov 2026, 9:41 AM (PDT)
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRect, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from briefing_reader import pictures, second_screen
from briefing_reader.actions import parse_action_line
from briefing_reader.agenda import Deadline
from briefing_reader.ask.mail import MailMessage, MailPerson, MailThread
from briefing_reader.board import ask_frames, frames, protocol
from briefing_reader.gcal import CalendarEvent, EventBrief
from briefing_reader.gmail import MailView

PDT = timezone(timedelta(hours=-7))
NOW = datetime(2026, 11, 17, 9, 41, tzinfo=PDT)
MAIL_TEXT = "Here are the Q4 numbers - line 14 changed since Friday, the rest is the same."
REPLY_TEXT = "Thanks Dana, the numbers look right. I will send comments by Thursday."


def spots(*sizes: str, available_drop: int = 48) -> list[second_screen.ScreenSpot]:
    """Screens left to right, each "WxH" (its work area without a bottom taskbar of ``available_drop`` px)."""
    found = []
    x = 0
    for index, size in enumerate(sizes):
        width, height = (int(part) for part in size.split("x"))
        geometry = QRect(x, 0, width, height)
        found.append(second_screen.ScreenSpot(f"fake{index}", geometry,
                                              QRect(x, 0, width, max(1, height - available_drop))))
        x += width
    return found


class FakeNative:
    """See the module doc."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.calls: list[tuple[str, int]] = []
        self.display = True
        self.saver = False
        self.covered = False
        self.released = 0

    def no_activate(self, hwnd: int) -> bool:
        self.calls.append(("no_activate", hwnd))
        return True

    def exclude_from_capture(self, hwnd: int) -> tuple[bool, str]:
        self.calls.append(("exclude_from_capture", hwnd))
        return (False, "refused (winerror 5)") if self.refuse else (True, "")

    def watch_power(self, hwnd: int) -> None:
        self.calls.append(("watch_power", hwnd))

    def native_message(self, message: int) -> bool:
        return False

    def display_off(self) -> bool:
        return not self.display

    def battery_saver(self) -> bool:
        return self.saver

    def full_screen(self, hwnd: int) -> bool:
        return self.covered

    def release(self) -> None:
        self.released += 1

    def names(self) -> list[str]:
        return [name for name, _hwnd in self.calls]


class FakeProbes(second_screen.HideProbes):
    """HideProbes answered from ``flags`` (set a flag to True to make that rule hold)."""

    def __init__(self) -> None:
        self.flags = {"locked": False, "display_off": False, "battery_saver": False, "full_screen": False}
        super().__init__(locked=lambda: self.flags["locked"], display_off=lambda: self.flags["display_off"],
                         battery_saver=lambda: self.flags["battery_saver"],
                         full_screen=lambda: self.flags["full_screen"])


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += float(seconds)
        return self.t


class RecordingPainter(QPainter):
    """A QPainter keeping every text drawn through it (``texts``, with where: ``placed``)."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.texts: list[str] = []
        self.placed: list[tuple[float, float, str]] = []

    def drawText(self, *args: Any) -> None:  # noqa: N802 - Qt override
        text = next((arg for arg in args if isinstance(arg, str)), "")
        self.texts.append(text)
        point = next((arg for arg in args if isinstance(arg, (QPointF, QRectF))), None)
        if isinstance(point, QPointF):
            mapped = self.transform().map(point)
            self.placed.append((mapped.x(), mapped.y(), text))
        elif isinstance(point, QRectF):
            mapped = self.transform().mapRect(point)
            self.placed.append((mapped.x(), mapped.y(), text))
        super().drawText(*args)


def at(hour: int, minute: int = 0) -> datetime:
    return NOW.replace(hour=hour, minute=minute)


def page_jpeg(width: int = 600, height: int = 340) -> bytes:
    """A made-up page picture (a schedule page's blocks), as a JPEG."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#D9DEE4"))
    painter = QPainter(image)
    painter.fillRect(QRectF(0, 0, width, 28), QColor("#2E3A47"))
    for index in range(4):
        painter.fillRect(QRectF(24 + index * 104, 10, 80, 8), QColor("#C8D0D9"))
    painter.fillRect(QRectF(24, 60, 260, 26), QColor("#1F2B38"))
    painter.fillRect(QRectF(24, 96, 180, 10), QColor("#8A97A6"))
    for row in range(4):
        for col in range(2):
            painter.fillRect(QRectF(24 + col * 282, 130 + row * 50, 270, 40),
                             QColor("#3D5A78" if (row + col) % 3 == 1 else "#4F6F91"))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", 80)
    return bytes(data.data())


def _checked(message: dict, known: set[str] | None = None, groups: set[str] | None = None) -> dict:
    return json.loads(protocol.check_message(message, known or set(), groups or set()).text)


def demo_frames(marker: str = "") -> list[tuple[dict, str]]:
    """The still's nine frames (each as the page gets it, and its picture's data: URL or ""), in the still's order."""
    found: list[tuple[dict, str]] = []

    def add(built: Any, waits: str = "") -> None:
        frame = dict(built.messages[0])
        if waits:
            frame["waits"] = waits
        clean = _checked(frame)
        src = ""
        if len(built.messages) > 1:
            src = _checked(built.messages[1], {clean["id"]})["src"]
        found.append((clean, src))

    day = {"work": [EventBrief("e0", "primary", "Breakfast", start=at(8), end=at(8, 30)),
                    EventBrief("e1", "primary", "AI-Trader check-in", start=at(10), end=at(10, 30)),
                    EventBrief("e2", "primary", "Study group", start=at(13), end=at(14)),
                    EventBrief("e3", "primary", "Project Development check-in", start=at(16), end=at(16, 45))]}
    add(frames.from_picture(pictures.plan_picture(day, (), day=date(2026, 11, 17), now=NOW, hour24=True),
                            frame_id="f1", now=NOW, hour24=True, title="Today"))
    text = f"{MAIL_TEXT} {marker}".strip()
    thread = MailThread("work", "t1", "Re: Budget numbers", (MailMessage(
        "g1", sender=MailPerson("dana@example.edu", "Dana Whitfield"), sent_at=at(8, 12), subject="Re: Budget numbers",
        text=text),))
    add(frames.from_picture(pictures.email_picture(thread, tz=PDT), frame_id="f2", now=NOW),
        waits="Reply waits on Approvals")
    for frame_id, raw in (("f3", {"kind": "chart", "title": "Q4 budget by line", "unit": "USD",
                                  "bars": [{"label": "Travel", "value": 1200}, {"label": "Software", "value": 3400},
                                           {"label": "Events", "value": 2100}, {"label": "Hardware", "value": 800}],
                                  "changed": 1, "was": 2900, "change": "+500 Software · was 2.9k on Friday"}),
                          ("f4", {"kind": "table", "title": "Budget lines", "unit": "USD",
                                  "columns": ["Line", "Friday", "Today", "Change"],
                                  "rows": [["Travel", 1200, 1200, 0], ["Software", 2900, 3400, "+500"],
                                           ["Events", 2100, 2100, 0], ["Hardware", 800, 800, 0]], "changed": 1,
                                  "change": "+500 · the other 3 lines unchanged",
                                  "source": "From Dana's mail, 8:12 AM"})):
        add(ask_frames.build(ask_frames.check_frame(raw), frame_id=frame_id))
    todo = parse_action_line("Todo: title=Send budget comments to Dana | due=2026-11-19 | acct=work | link=")
    done = parse_action_line("Todo: title=Print boarding pass | due=2026-11-18 | acct=work | link=")
    add(frames.from_todos([todo, done], [Deadline("LING essay draft", date(2026, 11, 19)),
                                         Deadline("Book the dentist", date(2026, 11, 22))],
                          frame_id="f5", now=NOW, statuses={done.id: "done"}))
    add(ask_frames.build(ask_frames.check_frame(
        {"kind": "timeline", "title": "LING essay",
         "nodes": [{"when": "Mon", "words": "Outline", "state": "done"},
                   {"when": "Wed", "words": "Full draft", "state": "next"},
                   {"when": "Thu 11:59 PM", "words": "Essay", "state": "due"}], "today": 1}), frame_id="f6"))
    rows = [{"sender": "Northwind Outfitters", "time": "7:02 AM", "subject": "Your order has shipped", "meta": "",
             "unread": True},
            {"sender": "City Hack", "time": "Yesterday", "subject": "City Hack 2026: registration closes Friday",
             "meta": "", "unread": True},
            {"sender": "Prof. Ana Ruiz", "time": "Yesterday", "subject": "Lab section moved to Thursday", "meta": "",
             "unread": True}]
    mail_list = {"type": "frame", "id": "f7", "kind": "mail_list", "title": "Personal mail", "meta": "3 unread",
                 "body": {"rows": rows}}
    found.append((_checked(mail_list), ""))
    add(frames.from_picture(pictures.page_ready("https://city-hack.example.org/schedule", image=page_jpeg(),
                                                width=600, height=340), frame_id="f8", now=NOW,
                            title="City Hack schedule"))
    add(ask_frames.build(ask_frames.check_frame(
        {"kind": "maths", "title": "Solve 2x² − 8x + 6 = 0",
         "steps": [{"name": "Divide by 2", "expr": ["=", ["+", ["pow", "x", 2], ["*", -4, "x"], 3], 0]},
                   {"name": "Factor", "expr": ["=", ["*", ["()", ["-", "x", 1]], ["()", ["-", "x", 3]]], 0]}],
         "answer": {"name": "So", "expr": ["or", ["=", "x", 1], ["=", "x", 3]]}}), frame_id="f9"))
    return found


def outgoing_frame(marker: str = "", frame_id: str = "o1") -> dict:
    """A reply that waits on Approvals, shown as the mail it sends (its text holds ``marker``)."""
    view = MailView("me@example.edu", "dana@example.edu", "", "Re: Budget numbers", "<abc@example.com>", "", "t1",
                    f"{REPLY_TEXT} {marker}".strip())
    built = frames.from_picture(pictures.outgoing_picture(view, account="work"), frame_id=frame_id, now=NOW)
    return _checked(built.messages[0])


def followups_frame(marker: str = "", frame_id: str = "l2") -> dict:
    """The follow-ups as a mail list (board.frames.from_followups): senders and subjects only - each item's own words
    (why it waits, its address) hold ``marker`` and never reach the frame."""
    from briefing_reader.followups import OWED, WAITING, FollowupItem

    items = [FollowupItem("r1", "k1", WAITING, "work", thread_id="t1", last_gmail_id="g1", who="Prof. Ana Ruiz",
                          who_address=f"ana.{marker}@example.edu", subject="Lab section moved to Thursday",
                          since=NOW - timedelta(hours=30), reason=f"They wrote last, to you directly {marker}"),
             FollowupItem("r2", "k2", OWED, "work", thread_id="t2", last_gmail_id="g2", who="City Hack",
                          who_address=f"team.{marker}@example.org",
                          subject="City Hack 2026: registration closes Friday", since=NOW - timedelta(days=3),
                          reason=f"You wrote last {marker}")]
    return _checked(frames.from_followups(items, frame_id=frame_id, now=NOW).messages[0])


def demo_events() -> list[CalendarEvent]:
    return [CalendarEvent("Breakfast", start=at(8), end=at(8, 30)),
            CalendarEvent("AI-Trader check-in", start=at(10), end=at(10, 30), location="https://meet.google.com/abc"),
            CalendarEvent("Study group", start=at(13), end=at(14), location="Library, room 2"),
            CalendarEvent("Project Development check-in", start=at(16), end=at(16, 45))]


def demo_state(found: Sequence[tuple[dict, str]] | None = None, **changes: Any) -> second_screen.DisplayState:
    found = list(demo_frames() if found is None else found)
    tiles = tuple(second_screen.private_frame(frame) for frame, _src in found)
    pictures_ = {frame["id"]: src for frame, src in found if src}
    values = dict(look=("needs", False, False), word="NEEDS YOU", tone="amber", needs=1, briefing=True, frames=9,
                  pinned=1, events=second_screen.upcoming_events(demo_events(), NOW), tiles=tiles, pictures=pictures_,
                  updated="9:41 AM")
    values.update(changes)
    return second_screen.DisplayState(**values)


def stand_in_host(**config: Any) -> SimpleNamespace:
    """What the controller reads from the app when there is no app (no window: it is fed by hand)."""
    look = SimpleNamespace(board=config.pop("board", True), second_screen=config.pop("second_screen", False))
    return SimpleNamespace(config=SimpleNamespace(look=look, display=SimpleNamespace(hour24=False)), window=None,
                           _now=lambda: NOW, **config)


def tile_picture_src(width: int, height: int) -> str:
    """A data: URL of a JPEG ``width`` x ``height`` (no protocol check: a test of tile_picture's 4 MP rule)."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#405060"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", 40)
    return protocol.PICTURE_PREFIX + base64.b64encode(bytes(data.data())).decode("ascii")
