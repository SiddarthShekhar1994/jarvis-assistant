"""The new look, part 4: the board's frames from what Jarvis already knows (briefing_reader.board.frames) and the
protocol's checker for each kind (briefing_reader.board.protocol.KINDS). Offscreen; nothing here starts the web engine.

Covered: each builder from real pictures.py objects - a mail, an outgoing reply (it waits on Approvals), a day with and
without today (the bar, the now-line, past / next / free rows), a card's move over two days, a week, a page picture
ready (as it is, re-encoded, too big), unavailable, on its way and not kept, a web search - and from a research answer,
to-dos with deadlines (waits, done, open, dismissed, duplicates) and follow-ups; every output passes check_message; the
caps (title 60, meta 40, text 400, rows 8 with "+N MORE"), cuts that end in "…" and never split a surrogate pair, as
the page counts; hostile strings (markup, javascript: addresses, control and bidi characters, secrets, 10 000-character
subjects) end as clean text; and each kind's checker against hostile shapes (extra keys, wrong types, huge strings and
lists, numbers out of range, NaN, deep nesting). Every name, address and text is invented.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import unittest
import zlib
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from briefing_reader import config, live, persona, pictures
from briefing_reader.actions import STATUS_CREATED, STATUS_DENIED, STATUS_DONE, parse_action_line
from briefing_reader.agenda import ORIGIN_CALENDAR, Deadline
from briefing_reader.ask.mail import MailMessage, MailPerson, MailThread
from briefing_reader.ask.research import ResearchAnswer
from briefing_reader.board import frames, protocol
from briefing_reader.followups import OWED, WAITING, FollowupItem
from briefing_reader.gcal import EventBrief
from briefing_reader.gmail import MailView
from tests.ui_fakes import tiny_jpeg

PDT = timezone(timedelta(hours=-7))
NOW = datetime(2026, 11, 17, 9, 41, tzinfo=PDT)          # a Tuesday
HOSTILE = "‮gnp.exe\x00\x07⁦​ <img src=x onerror=alert(1)><script>alert(2)</script>"
_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_board_frames", "-platform", "offscreen"])


def at(hour: int, minute: int = 0, day: int = 17) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=PDT)


def passes(built: frames.Built) -> list[dict]:
    """Every message of ``built`` through check_message (the frame first; its picture then knows it): the page's
    messages as JSON."""
    known: set[str] = set()
    found = []
    for message in built.messages:
        checked = protocol.check_message(message, known)
        if checked.type == "frame":
            known.add(checked.frame_id)
        found.append(json.loads(checked.text))
    return found


def frame_of(built: frames.Built) -> dict:
    return passes(built)[0]


def strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in strings(item)]
    return []


def mail_thread(subject: str = "Re: Budget numbers", text: str = "Here are the Q4 numbers.\nThe rest is the same.\n",
                sender: MailPerson | None = None, when: datetime | None = None) -> MailThread:
    message = MailMessage("g1", sender=sender or MailPerson("dana@example.edu", "Dana Whitfield"),
                          to=(MailPerson("me@example.edu", "Me"),), sent_at=when or at(8, 12), subject=subject,
                          text=text)
    return MailThread("work", "t1", subject, (message,))


def events() -> dict[str, list[EventBrief]]:
    return {"work": [EventBrief("e0", "primary", "Breakfast", start=at(8), end=at(8, 30)),
                     EventBrief("e1", "primary", "AI-Trader check-in", start=at(10), end=at(10, 30)),
                     EventBrief("e2", "primary", "Study group", start=at(13), end=at(14)),
                     EventBrief("e3", "primary", "Project Development check-in", start=at(16), end=at(16, 45))]}


def png(width: int, height: int, noise: bool = False) -> bytes:
    image = QImage(os.urandom(width * height * 4), width, height, QImage.Format.Format_RGB32) if noise \
        else QImage(width, height, QImage.Format.Format_RGB32)
    if not noise:
        image.fill(QColor("#e9eef2"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.copy().save(buffer, "PNG")
    buffer.close()
    return bytes(data.data())


def png_header(width: int, height: int) -> bytes:
    """A PNG that says it is ``width`` x ``height`` (its header only: nothing to decode)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))


class WordsTests(unittest.TestCase):
    def test_cuts_end_in_an_ellipsis_as_the_page_counts(self) -> None:
        self.assertEqual(frames.cut("Budget lines", 60), "Budget lines")
        cut = frames.cut("x" * 10_000, 60)
        self.assertEqual((len(cut), cut[-1]), (60, "…"))
        words = frames.cut("City Hack 2026 registration closes on Friday at noon for every team", 40)
        self.assertTrue(words.endswith("…"))
        self.assertLessEqual(protocol.units(words), 40)
        self.assertEqual(words, "City Hack 2026 registration closes on…")    # at a space near the end

    def test_a_cut_never_splits_a_surrogate_pair(self) -> None:
        for text in ("\U0001F600" * 40, "a" + "\U0001F600" * 40, "\U0001F600x" * 30):
            cut = frames.cut(text, 60)
            self.assertLessEqual(protocol.units(cut), 60)
            self.assertTrue(cut.endswith("…"))
            cut.encode("utf-8")                                    # no lone surrogate: it encodes
            self.assertEqual(cut[:-1].rstrip(), text[:len(cut) - 1].rstrip())

    def test_a_cut_never_splits_an_escape(self) -> None:
        shown = frames.clean("x" * 55 + "‮" + "tail words")            # "<U+202E>" would straddle the cap
        cut = frames.cut(shown, 60)
        self.assertEqual(cut, "x" * 55 + "…")
        self.assertEqual(frames.cut("x" * 50 + "<U+202E>abc", 60), "x" * 50 + "<U+202E>a…")

    def test_a_host_is_cut_from_its_start_so_whose_site_shows(self) -> None:
        host = frames.cut_host("login.paypal.com.accounts.secure-update.example.net", 24)
        self.assertTrue(host.startswith("…"))
        self.assertTrue(host.endswith("example.net"))
        self.assertLessEqual(protocol.units(host), 24)

    def test_clean_shows_control_and_bidi_characters_and_hides_secrets(self) -> None:
        self.assertEqual(frames.clean("Invoice ‮gnp.exe\x07"), "Invoice <U+202E>gnp.exe<U+0007>")
        self.assertEqual(frames.clean("a\ud800b"), "a<U+D800>b")                  # a lone surrogate is never text
        self.assertNotIn("sk-ant-0123456789abcdef", frames.clean("key sk-ant-0123456789abcdef"))
        with mock.patch.object(config, "_secrets", ("hunter2-registered-value",)):
            self.assertNotIn("hunter2", frames.clean("the hunter2-registered-value"))
            self.assertNotIn("hunter2", frames.clean_text("line\nthe hunter2-registered-value", 400))
        self.assertEqual(frames.clean_text("Hi Ana,\n\nthe numbers\tare in.\n", 400), "Hi Ana, the numbers are in.")


class MailTests(unittest.TestCase):
    def test_a_mail_is_a_thread_frame_with_sender_time_and_text(self) -> None:
        picture = pictures.email_picture(mail_thread(), tz=PDT)
        built = frames.from_picture(picture, frame_id="m1", now=NOW)
        self.assertEqual(built.refused, 0)
        frame = frame_of(built)
        self.assertEqual((frame["kind"], frame["title"], frame["meta"]),
                         ("mail_thread", "Re: Budget numbers", "work · 8:12 AM"))
        self.assertEqual(frame["body"], {"sender": "Dana Whitfield", "time": "8:12 AM",
                                         "text": "Here are the Q4 numbers. The rest is the same."})
        self.assertEqual(frame["waits"], "")
        self.assertNotIn("Dana", repr(built))                      # a repr names kinds and counts only

    def test_another_day_keeps_its_date_and_hour24(self) -> None:
        picture = pictures.email_picture(mail_thread(when=at(8, 12, day=16)), hour24=True, tz=PDT)
        frame = frame_of(frames.from_picture(picture, frame_id="m1", now=NOW))
        self.assertEqual(frame["body"]["time"], "Mon Nov 16 2026, 08:12")
        self.assertEqual(frame["meta"], "work · Mon Nov 16 2026, 08:12")
        nameless = pictures.email_picture(mail_thread(sender=MailPerson("dana@example.edu")), tz=PDT)
        self.assertEqual(frame_of(frames.from_picture(nameless, frame_id="m2", now=NOW))["body"]["sender"],
                         "dana@example.edu")

    def test_an_outgoing_reply_waits_on_approvals(self) -> None:
        view = MailView("me@example.edu", "dana@example.edu", "", "Re: Budget numbers", "<abc@example.com>", "",
                        "18c0ffee00000001", "Hi Dana,\nthanks - looks right.\n")
        frame = frame_of(frames.from_picture(pictures.outgoing_picture(view, account="work"), frame_id="m2", now=NOW))
        self.assertEqual(frame["waits"], frames.WAITS_REPLY)
        self.assertEqual(frame["body"], {"sender": "To dana@example.edu", "time": "", "text": "Hi Dana, thanks - looks right."})
        self.assertEqual(frame["meta"], "work")
        new = MailView("me@example.edu", "ana@example.edu", "", "Lunch", "", "", "", "Thursday?")
        self.assertEqual(frame_of(frames.from_picture(pictures.outgoing_picture(new, account="work"), frame_id="m3",
                                                      now=NOW))["waits"], frames.WAITS_EMAIL)

    def test_hostile_and_huge_mail_ends_as_clean_capped_text(self) -> None:
        subject = HOSTILE + "S" * 10_000
        text = ("javascript:alert(3) <a href='javascript:alert(4)'>x</a> " + HOSTILE) * 300
        picture = pictures.email_picture(mail_thread(subject=subject, text=text,
                                                     sender=MailPerson("x@example.edu", HOSTILE)), tz=PDT)
        frame = frame_of(frames.from_picture(picture, frame_id="m1", now=NOW))
        self.assertLessEqual(protocol.units(frame["title"]), protocol.TITLE_CAP)
        self.assertTrue(frame["title"].endswith("…"))
        self.assertLessEqual(protocol.units(frame["body"]["text"]), protocol.TEXT_CAP)
        self.assertTrue(frame["body"]["text"].endswith("…"))
        for words in strings(frame):
            self.assertFalse(any(ord(char) < 32 or char in "‮⁦​\x7f" for char in words), words)
        self.assertIn("<U+202E>", frame["title"])                  # shown, not obeyed
        self.assertIn("<img src=x", frame["title"])                # markup stays words: the page sets text


class CalendarTests(unittest.TestCase):
    def test_today_has_its_bar_now_line_and_rows_running_down(self) -> None:
        picture = pictures.plan_picture(events(), (), day=date(2026, 11, 17), now=NOW, hour24=True)
        frame = frame_of(frames.from_picture(picture, frame_id="c1", now=NOW, title="Today"))
        body = frame["body"]
        self.assertEqual((frame["kind"], frame["title"]), ("calendar", "Today"))
        self.assertEqual(frame["meta"], "Tue Nov 17 · 4 events")
        self.assertEqual((body["first"], body["last"], body["now_min"], body["now"]), (420, 1080, 581, "09:41"))
        self.assertEqual([tick["text"] for tick in body["ticks"]], ["7", "9", "11", "13", "15", "17"])
        kinds = [(span["start"], span["end"], span["kind"]) for span in body["spans"]]
        self.assertIn((600, 630, "next"), kinds)
        self.assertIn((780, 840, "busy"), kinds)
        self.assertIn((630, 780, "free"), kinds)
        rows = [(row["time"], row["title"], row["meta"], row["state"]) for row in body["rows"]]
        self.assertEqual(rows[0], ("08:00-08:30", "Breakfast", "", "past"))
        self.assertIn(("10:00-10:30", "AI-Trader check-in", "in 19 min", "next"), rows)
        self.assertIn(("10:30-13:00", "Free", "2 h 30", "free"), rows)
        self.assertIn(("14:00-16:00", "Free", "2 h", "free"), rows)
        self.assertEqual(rows[-1], ("16:00-16:45", "Project Development check-in", "", ""))
        times = [row["time"] for row in body["rows"]]
        self.assertEqual(times, sorted(times))                    # time runs down

    def test_another_day_has_no_now_line_and_twelve_hour_times(self) -> None:
        tomorrow = {"work": [EventBrief("e9", "primary", "Lab", start=at(13, day=18), end=at(14, 15, day=18))]}
        picture = pictures.plan_picture(tomorrow, (), day=date(2026, 11, 18), now=NOW)
        body = frame_of(frames.from_picture(picture, frame_id="c2", now=NOW))["body"]
        self.assertEqual((body["now_min"], body["now"]), (-1, ""))
        self.assertEqual([(row["time"], row["state"]) for row in body["rows"]], [("1:00-2:15 PM", "")])
        self.assertEqual([tick["text"] for tick in body["ticks"]], ["8", "10", "12", "2", "4", "6"])
        self.assertNotIn("next", [span["kind"] for span in body["spans"]])

    def test_a_move_over_two_days_strikes_where_it_was(self) -> None:
        move = parse_action_line("Move: acct=work | event=evt0001aa | cal=primary | when=2026-11-18 15:00-16:00 | "
                                 "notify=all | title=Project sync | at=2026-11-17 14:00-15:00 | link= | body=")
        picture = pictures.day_change_picture(move, now=NOW)
        frame = frame_of(frames.from_picture(picture, frame_id="c3", now=NOW))
        body = frame["body"]
        self.assertEqual((frame["title"], body["first"], body["last"]), ("Move: Project sync", 0, 0))   # no bar
        self.assertEqual([(row["title"], row["meta"], row["state"]) for row in body["rows"]],
                         [("Project sync", "was · Tue Nov 17", "struck"),
                          ("Project sync", "new · Wed Nov 18", "new")])
        self.assertEqual(frame["meta"], "Tue Nov 17 · Wed Nov 18 · 1 event")

    def test_a_week_is_one_row_per_day(self) -> None:
        picture = pictures.week_picture(events(), at(0), at(0, day=24), now=NOW)
        frame = frame_of(frames.from_picture(picture, frame_id="w1", now=NOW))
        rows = frame["body"]["rows"]
        self.assertEqual((frame["title"], frame["meta"], len(rows)), ("Calendar", "4 events", 7))
        self.assertEqual((rows[0]["time"], rows[0]["meta"], rows[0]["state"]), ("Tue 17", "4 events", "today"))
        self.assertTrue(rows[0]["title"].startswith("Breakfast · AI-Trader check-in"))
        self.assertEqual((rows[1]["time"], rows[1]["title"], rows[1]["meta"], rows[1]["state"]),
                         ("Wed 18", "", "0 events", ""))
        self.assertEqual(frame["body"]["first"], frame["body"]["last"])

    def test_a_crowded_day_keeps_eight_rows_the_last_saying_how_many_more(self) -> None:
        many = {"work": [EventBrief(f"e{n}", "primary", f"Meeting {n}", start=at(8 + n // 2, 30 * (n % 2)),
                                    end=at(8 + n // 2, 30 * (n % 2) + 25)) for n in range(14)]}
        picture = pictures.plan_picture(many, (), day=date(2026, 11, 17), now=NOW)
        rows = frame_of(frames.from_picture(picture, frame_id="c4", now=NOW))["body"]["rows"]
        self.assertEqual(len(rows), protocol.ROWS_CAP)
        self.assertEqual(rows[-1], {"time": "", "title": "", "meta": "+7 MORE", "state": ""})
        self.assertNotIn("free", [row["state"] for row in rows])   # free gaps leave first


class PageTests(unittest.TestCase):
    URL = "https://city-hack.example.org/schedule"

    def picture_of(self, built: frames.Built) -> bytes:
        message = passes(built)[1]
        self.assertTrue(message["src"].startswith(protocol.PICTURE_PREFIX))
        return base64.b64decode(message["src"][len(protocol.PICTURE_PREFIX):])

    def test_a_small_jpeg_goes_as_it_is_with_its_frame(self) -> None:
        jpeg = tiny_jpeg(96, 54)
        built = frames.from_picture(pictures.page_ready(self.URL, image=jpeg, width=96, height=54), frame_id="p1",
                                    now=NOW)
        frame, message = passes(built)
        self.assertEqual((frame["kind"], frame["title"], frame["meta"]), ("picture", "Page: city-hack.example.org",
                                                                          "9:41 AM"))
        self.assertEqual(frame["body"], {"caption": "city-hack.example.org"})
        self.assertEqual((message["type"], message["id"]), ("picture", "p1"))
        self.assertEqual(self.picture_of(built), jpeg)
        self.assertEqual(frames.jpeg_size(jpeg), (96, 54))

    def test_a_png_or_a_wide_picture_is_re_encoded_to_a_jpeg_of_at_most_960_px(self) -> None:
        for data, size in ((png(1600, 900), (960, 540)), (png(400, 300), (400, 300))):
            built = frames.from_picture(pictures.page_ready(self.URL, image=data, image_format="PNG", width=size[0],
                                                            height=size[1]), frame_id="p2", now=NOW)
            jpeg = self.picture_of(built)
            self.assertEqual(jpeg[:3], b"\xff\xd8\xff")
            self.assertEqual(frames.jpeg_size(jpeg), size)
            self.assertLessEqual(len(jpeg), protocol.PICTURE_BYTES)

    def test_a_picture_too_big_even_re_encoded_is_refused_with_a_count(self) -> None:
        for data in (png(960, 3000, noise=True), png_header(20_000, 20_000), b"not a picture at all"):
            built = frames.from_picture(pictures.page_ready(self.URL, image=data, width=960, height=3000),
                                        frame_id="p3", now=NOW)
            self.assertEqual((built.refused, len(built.messages)), (1, 1))
            frame = frame_of(built)
            self.assertEqual((frame["status"], frame["status_text"]), ("empty", frames.TOO_BIG))
        self.assertIsNone(frames.board_jpeg(png_header(20_000, 20_000)))   # never decoded: too many pixels

    def test_a_picture_not_ready_shows_its_own_note(self) -> None:
        failed = frame_of(frames.from_picture(pictures.page_unavailable(self.URL, "it timed out"), frame_id="p4",
                                              now=NOW))
        self.assertEqual((failed["status"], failed["status_text"]), ("failed", "Picture unavailable: it timed out"))
        self.assertEqual(failed["body"], {"caption": "city-hack.example.org"})
        pending = frame_of(frames.from_picture(pictures.page_pending(self.URL), frame_id="p5", now=NOW))
        self.assertEqual((pending["status"], pending["status_text"]), ("empty", pictures.NOTE_OPENING))
        # Part 5c fixes, round 2: the builder says the frame stands for a picture not ready (the board keeps a good one
        # meanwhile); a ready one, and a builder's own final state, never say so.
        self.assertTrue(frames.from_picture(pictures.page_pending(self.URL), frame_id="p5", now=NOW).not_ready)
        self.assertTrue(frames.from_picture(pictures.page_unavailable(self.URL, "x"), frame_id="p4", now=NOW).not_ready)
        ready = pictures.page_ready(self.URL, image=tiny_jpeg(), width=48, height=30)
        self.assertFalse(frames.from_picture(ready, frame_id="p8", now=NOW).not_ready)
        self.assertFalse(frames.from_todos([], [], frame_id="t9", now=NOW).not_ready)
        dropped = pictures.page_ready(self.URL, image=tiny_jpeg(), width=48, height=30).dropped(live.PICTURE_TOO_BIG)
        frame = frame_of(frames.from_picture(dropped, frame_id="p6", now=NOW))
        self.assertEqual((frame["status"], frame["status_text"]),
                         ("empty", pictures.DROPPED_PREFIX + live.PICTURE_TOO_BIG))
        mail = pictures.email_picture(mail_thread(), tz=PDT).dropped("memory")
        self.assertEqual(frame_of(frames.from_picture(mail, frame_id="p7", now=NOW))["kind"], "mail_thread")

    def test_a_redirect_is_in_the_caption(self) -> None:
        picture = pictures.page_ready(self.URL, image=tiny_jpeg(), width=48, height=30,
                                      final_url="https://elsewhere.example.net/x")
        self.assertEqual(frame_of(frames.from_picture(picture, frame_id="p8", now=NOW))["body"]["caption"],
                         "city-hack.example.org, redirected to elsewhere.example.net")
        long = pictures.page_ready("https://registration.city-hack.example.org/x", image=tiny_jpeg(), width=48,
                                   height=30, final_url="https://login.accounts.identity-provider.example.net/y")
        caption = frame_of(frames.from_picture(long, frame_id="p9", now=NOW))["body"]["caption"]
        self.assertLessEqual(protocol.units(caption), protocol.TITLE_CAP)
        first, _sep, final = caption.partition(frames.REDIRECTED)
        self.assertTrue(first.endswith("city-hack.example.org"))           # each host keeps its end
        self.assertTrue(final.endswith("provider.example.net"))


class SearchAndResearchTests(unittest.TestCase):
    def test_a_search_is_text_with_the_query_and_results_never_links(self) -> None:
        hits = [(f"Result {n}", f"https://site{n}.example.org/page?q={n}") for n in range(10)]
        hits[1] = (HOSTILE, "javascript:alert(1)")
        picture = pictures.search_picture("city hack schedule", hits + [("More", "https://m.example.org/")] * 3)
        frame = frame_of(frames.from_picture(picture, frame_id="s1", now=NOW))
        rows = frame["body"]["rows"]
        self.assertEqual((frame["kind"], frame["title"], frame["meta"]), ("text", "city hack schedule", "Web search"))
        self.assertEqual(rows[0], {"lead": "", "text": "Result 0", "meta": "site0.example.org"})
        self.assertEqual(rows[1]["meta"], "")                      # a javascript: address has no host
        self.assertEqual(len(rows), protocol.ROWS_CAP)
        self.assertEqual(rows[-1], {"lead": "", "text": "", "meta": "+6 MORE"})   # 13 listed, 7 shown
        text = json.dumps(frame)
        self.assertNotIn("javascript:", text)
        self.assertNotIn("https://", text)
        empty = frame_of(frames.from_picture(pictures.search_picture("nothing", []), frame_id="s2", now=NOW))
        self.assertEqual(empty["body"], {"text": pictures.SEARCH_EMPTY, "rows": []})

    def test_research_is_its_answer_and_sources_as_words(self) -> None:
        answer = ResearchAnswer("The schedule moved to Friday ‮.", (
            ("City Hack schedule", "https://city-hack.example.org/s"), (HOSTILE, "javascript:alert(1)"),
            ("", "https://only-a-host.example.edu/x"), ("", "javascript:void(0)")))
        frame = frame_of(frames.from_research(answer, frame_id="r1"))
        self.assertEqual((frame["kind"], frame["title"], frame["meta"]), ("text", frames.RESEARCH_TITLE, "3 sources"))
        self.assertEqual(frame["body"]["text"], "The schedule moved to Friday <U+202E>.")
        rows = frame["body"]["rows"]
        self.assertEqual(rows[0], {"lead": "", "text": "City Hack schedule", "meta": "city-hack.example.org"})
        self.assertEqual(rows[1]["meta"], "")
        self.assertIn("<U+202E>", rows[1]["text"])
        self.assertEqual(rows[2], {"lead": "", "text": "only-a-host.example.edu", "meta": ""})
        self.assertNotIn("javascript:", json.dumps(frame))
        self.assertEqual(frame_of(frames.from_research(answer, frame_id="r2", title="City Hack"))["title"],
                         "City Hack")


class TodoTests(unittest.TestCase):
    def todo(self, title: str, due: str = "", block: str = "") -> object:
        return parse_action_line(f"Todo: title={title} | due={due} | block={block} | acct=work | link=")

    def test_waits_done_and_open_with_due_words_from_deadlines(self) -> None:
        waiting = self.todo("Send budget comments to Dana", "2026-11-19")
        done = self.todo("Print boarding pass", "2026-11-18")
        blocked = self.todo("Lab report", "2026-11-20", "2026-11-18 19:00-21:00")
        dismissed = self.todo("Old thing", "2026-11-18")
        deadlines = [Deadline("LING essay draft", date(2026, 11, 19)),
                     Deadline("Book the dentist", date(2026, 11, 22)),
                     Deadline("Lab report", date(2026, 11, 20)),                    # the card's own: left out
                     Deadline("Final exam", at(15, day=17), "Calendar", ORIGIN_CALENDAR)]
        statuses = {done.id: STATUS_DONE, blocked.id: STATUS_CREATED, dismissed.id: STATUS_DENIED}
        frame = frame_of(frames.from_todos([waiting, done, blocked, dismissed], deadlines, frame_id="t1", now=NOW,
                                           statuses=statuses))
        rows = [(row["text"], row["meta"], row["state"]) for row in frame["body"]["rows"]]
        self.assertEqual(rows, [("Final exam", "TODAY 3:00 PM", "open"),
                                ("Send budget comments to Dana", "", "waits"),
                                ("LING essay draft", "2D", "open"),
                                ("Lab report", "3D", "open"),
                                ("Book the dentist", "5D", "open"),
                                ("Print boarding pass", "", "done")])
        self.assertEqual((frame["title"], frame["meta"]), (frames.TODOS_TITLE, "6 · 1 waits"))

    def test_nothing_to_do_is_the_empty_state_and_many_are_cut_at_eight(self) -> None:
        empty = frame_of(frames.from_todos([], [], frame_id="t2", now=NOW))
        self.assertEqual((empty["status"], empty["status_text"]), ("empty", frames.TODOS_EMPTY))
        many = [Deadline(f"Task {n}", date(2026, 11, 18) + timedelta(days=n)) for n in range(11)]
        rows = frame_of(frames.from_todos([], many, frame_id="t3", now=NOW))["body"]["rows"]
        self.assertEqual(len(rows), protocol.ROWS_CAP)
        self.assertEqual(rows[-1], {"text": "", "meta": "+4 MORE", "state": "open"})


class FollowupTests(unittest.TestCase):
    def item(self, kind: str, who: str, subject: str, hours: int) -> FollowupItem:
        return FollowupItem(f"r{hours}", "k", kind, "work", thread_id="t", last_gmail_id="g", subject=subject,
                            who=who, who_address="someone@example.edu", since=NOW - timedelta(hours=hours))

    def test_follow_ups_are_a_mail_list_with_waiting_or_owed(self) -> None:
        items = [self.item(WAITING, "Prof. Ana Ruiz", "Lab section moved to Thursday", 30),
                 self.item(OWED, "", "Budget numbers", 75)]
        frame = frame_of(frames.from_followups(items, frame_id="f1", now=NOW))
        self.assertEqual((frame["kind"], frame["title"], frame["meta"]),
                         ("mail_list", frames.FOLLOWUPS_TITLE, persona.followups_meta(1, 1)))
        self.assertEqual(frame["body"]["rows"], [
            {"sender": "Prof. Ana Ruiz", "time": "30h", "subject": "Lab section moved to Thursday", "meta": "WAITING",
             "unread": False},
            {"sender": "someone@example.edu", "time": "3d", "subject": "Budget numbers", "meta": "OWED",
             "unread": False}])
        timeless = frame_of(frames.from_followups(items, frame_id="f2"))
        self.assertEqual([row["time"] for row in timeless["body"]["rows"]], ["", ""])

    def test_none_is_the_empty_state_and_hostile_subjects_are_words(self) -> None:
        empty = frame_of(frames.from_followups([], frame_id="f3"))
        self.assertEqual((empty["status"], empty["status_text"]), ("empty", persona.FOLLOWUPS_EMPTY))
        frame = frame_of(frames.from_followups([self.item(WAITING, HOSTILE, HOSTILE * 400, 2)], frame_id="f4",
                                               now=NOW))
        row = frame["body"]["rows"][0]
        self.assertLessEqual(protocol.units(row["subject"]), protocol.TITLE_CAP)
        self.assertTrue(row["subject"].endswith("…"))
        self.assertIn("<U+202E>", row["sender"])


class BuilderRuleTests(unittest.TestCase):
    def test_frame_ids_are_the_callers_and_must_be_ids(self) -> None:
        answer = ResearchAnswer("x")
        for bad in ("", "a b", "../x", "x" * 41, None, 7):
            with self.assertRaises(ValueError) as caught:
                frames.from_research(answer, frame_id=bad)            # type: ignore[arg-type]
            self.assertNotIn("../x", str(caught.exception))
        self.assertEqual(frame_of(frames.from_research(answer, frame_id="board-7_a"))["id"], "board-7_a")

    def test_a_picture_the_board_does_not_show_gives_nothing(self) -> None:
        self.assertEqual(frames.from_picture(object(), frame_id="x1", now=NOW).messages, ())
        self.assertIsNone(frames.Built().frame)

    def test_every_builder_output_stays_inside_the_caps(self) -> None:
        huge = "W" * 10_000
        built = [
            frames.from_picture(pictures.email_picture(mail_thread(subject=huge, text=huge), tz=PDT), frame_id="a1",
                                now=NOW),
            frames.from_picture(pictures.plan_picture({"work": [EventBrief("e", "primary", huge, start=at(10),
                                                                           end=at(11))]}, (), day=date(2026, 11, 17),
                                                      now=NOW), frame_id="a2", now=NOW),
            frames.from_research(ResearchAnswer(huge, ((huge, "https://" + "h" * 200 + ".example.org/"),)),
                                 frame_id="a3", title=huge),
            frames.from_todos([], [Deadline(huge, date(2026, 11, 18))], frame_id="a4", now=NOW),
            frames.from_followups([FollowupItem("r", "k", WAITING, "w", thread_id="t", last_gmail_id="g",
                                                subject=huge, who=huge)], frame_id="a5", now=NOW),
        ]
        for one in built:
            frame = frame_of(one)
            self.assertLessEqual(protocol.units(frame["title"]), protocol.TITLE_CAP)
            self.assertLessEqual(protocol.units(frame["meta"]), protocol.META_CAP)
            for words in strings(frame["body"]):
                self.assertLessEqual(protocol.units(words), protocol.TEXT_CAP)
            for row in frame["body"].get("rows", []):
                for key, words in row.items():
                    if isinstance(words, str):
                        cap = protocol.TITLE_CAP if key in ("title", "text", "sender", "subject") else protocol.META_CAP
                        self.assertLessEqual(protocol.units(words), cap, key)


# --------------------------------------------------------------------------
# The protocol's checker for each kind
# --------------------------------------------------------------------------

def message(kind: str, body: object, **fields: object) -> dict:
    found = {"type": "frame", "id": "k1", "kind": kind, "title": "Title", "body": body}
    found.update(fields)
    return found


def deep(levels: int) -> list:
    value: list = []
    for _ in range(levels):
        value = [value]
    return value


GOOD = {
    "text": {"text": "Words.", "rows": [{"lead": "1", "text": "A source", "meta": "example.org"}]},
    "calendar": {"first": 480, "last": 1080, "now_min": 581, "ticks": [{"at": 480, "text": "8"}],
                 "spans": [{"start": 600, "end": 630, "kind": "next"}], "now": "9:41",
                 "rows": [{"time": "10:00-10:30", "title": "AI-Trader check-in", "meta": "in 19 min",
                           "state": "next"}]},
    "mail_thread": {"sender": "Dana Whitfield", "time": "8:12 AM", "text": "Here are the numbers."},
    "mail_list": {"rows": [{"sender": "City Hack", "time": "YESTERDAY", "subject": "Registration closes Friday",
                            "meta": "", "unread": True}]},
    "todos": {"rows": [{"text": "Book the dentist", "meta": "5D", "state": "open"}]},
    "picture": {"caption": "city-hack.example.org"},
}


class KindCheckerTests(unittest.TestCase):
    def refused(self, kind: str, body: object) -> str:
        with self.assertRaises(protocol.Refused) as caught:
            protocol.check_message(message(kind, body))
        return caught.exception.reason

    def test_every_kind_on_the_list_and_its_body_rebuilt(self) -> None:
        self.assertEqual(set(protocol.KINDS), {"text", "calendar", "mail_thread", "mail_list", "todos", "picture",
                                               "table", "chart", "timeline", "maths",    # part 6's (test_ask_frames)
                                               "number",                                 # the canvas, part 2 (test_canvas)
                                               "approvals", "agents"})                   # part 3B (test_canvas)
        for kind, body in GOOD.items():
            checked = protocol.check_message(message(kind, body))
            self.assertEqual(json.loads(checked.text)["body"], body, kind)
        # A text frame without rows stays as part 3 sent it; a calendar's missing fields are filled in.
        self.assertEqual(json.loads(protocol.check_message(message("text", {"text": "x"})).text)["body"], {"text": "x"})
        self.assertEqual(json.loads(protocol.check_message(message("calendar", {})).text)["body"],
                         {"first": 0, "last": 0, "now_min": -1, "ticks": [], "spans": [], "now": "", "rows": []})

    def test_no_key_a_kind_does_not_have(self) -> None:
        for kind, body in GOOD.items():
            self.assertEqual(self.refused(kind, {**body, "html": "<b>x</b>"}), protocol.R_FIELD, kind)
            rows = body.get("rows")
            if rows:
                self.assertEqual(self.refused(kind, {**body, "rows": [{**rows[0], "onclick": "x"}]}),
                                 protocol.R_FIELD, kind)
        self.assertEqual(self.refused("calendar", {**GOOD["calendar"], "ticks": [{"at": 480, "text": "8", "x": 1}]}),
                         protocol.R_FIELD)
        self.assertEqual(self.refused("calendar", {**GOOD["calendar"], "spans": [{"start": 600, "end": 630}]}),
                         protocol.R_FIELD)

    def test_wrong_types_are_refused(self) -> None:
        for kind, body in GOOD.items():
            for wrong in ([body], "body", 7, None, True):
                self.assertEqual(self.refused(kind, wrong), protocol.R_SHAPE, (kind, wrong))
        self.assertEqual(self.refused("text", {"rows": "a, b"}), protocol.R_SHAPE)
        self.assertEqual(self.refused("text", {"rows": [["lead", "text"]]}), protocol.R_SHAPE)
        self.assertEqual(self.refused("mail_list", {"rows": [{"sender": 7}]}), protocol.R_SHAPE)
        self.assertEqual(self.refused("mail_list", {"rows": [{"unread": 1}]}), protocol.R_SHAPE)       # a bool
        self.assertEqual(self.refused("mail_list", {"rows": [{"unread": "yes"}]}), protocol.R_SHAPE)
        self.assertEqual(self.refused("todos", {"rows": [{"text": "x", "state": "maybe"}]}), protocol.R_FIELD)
        self.assertEqual(self.refused("calendar", {"rows": [{"state": "soon"}]}), protocol.R_FIELD)
        self.assertEqual(self.refused("calendar", {"first": True, "last": 60}), protocol.R_SHAPE)      # not a number
        self.assertEqual(self.refused("calendar", {"first": 1.0, "last": 60}), protocol.R_SHAPE)
        self.assertEqual(self.refused("calendar", {"first": 0, "last": 60, "now_min": "9:41"}), protocol.R_SHAPE)
        self.assertEqual(self.refused("calendar", {"first": 0, "last": 600,
                                                   "spans": [{"start": 0, "end": 60, "kind": "danger"}]}),
                         protocol.R_FIELD)
        self.assertEqual(self.refused("picture", {"caption": ["x"]}), protocol.R_SHAPE)
        self.assertEqual(self.refused("mail_thread", {"text": {"text": "x"}}), protocol.R_SHAPE)

    def test_numbers_stay_in_the_day_and_on_the_bar(self) -> None:
        bar = {"first": 480, "last": 1080}
        for body in ({"first": -1, "last": 60}, {"first": 0, "last": 1441}, {"first": 600, "last": 480},
                     {**bar, "now_min": 1200}, {**bar, "now_min": -2}, {"first": 0, "last": 0, "now_min": 0},
                     {**bar, "ticks": [{"at": 420, "text": "7"}]}, {**bar, "spans": [{"start": 600, "end": 500,
                                                                                     "kind": "busy"}]},
                     {**bar, "spans": [{"start": 400, "end": 500, "kind": "busy"}]},
                     {"first": 0, "last": 0, "ticks": [{"at": 0, "text": "0"}]},
                     {"first": 0, "last": 0, "spans": [{"start": 0, "end": 0, "kind": "busy"}]},
                     {**bar, "now_min": 10 ** 30}):
            self.assertEqual(self.refused("calendar", body), protocol.R_SHAPE, body)
        for number in (float("nan"), float("inf"), float("-inf")):
            self.assertEqual(self.refused("calendar", {**bar, "now_min": number}), protocol.R_SHAPE)

    def test_strings_lists_and_nesting_have_their_caps(self) -> None:
        self.assertEqual(self.refused("mail_thread", {"text": "x" * (protocol.TEXT_CAP + 1)}), protocol.R_TOO_LONG)
        self.assertEqual(self.refused("mail_thread", {"sender": "x" * (protocol.TITLE_CAP + 1)}), protocol.R_TOO_LONG)
        self.assertEqual(self.refused("mail_list", {"rows": [{"subject": "x" * 61}]}), protocol.R_TOO_LONG)
        self.assertEqual(self.refused("todos", {"rows": [{"meta": "x" * 41}]}), protocol.R_TOO_LONG)
        self.assertEqual(self.refused("calendar", {"now": "x" * (protocol.PILL_CAP + 1)}), protocol.R_TOO_LONG)
        self.assertEqual(self.refused("calendar", {"first": 0, "last": 60, "ticks": [{"at": 0, "text": "123456789"}]}),
                         protocol.R_TOO_LONG)
        self.assertEqual(self.refused("picture", {"caption": "x" * 5_000_000}), protocol.R_TOO_LONG)
        row = {"text": "x", "meta": "", "state": "open"}
        self.assertEqual(protocol.check_message(message("todos", {"rows": [row] * protocol.ROWS_CAP})).type, "frame")
        self.assertEqual(self.refused("todos", {"rows": [row] * (protocol.ROWS_CAP + 1)}), protocol.R_TOO_LONG)
        self.assertEqual(self.refused("todos", {"rows": [row] * 1_000_000}), protocol.R_TOO_LONG)
        self.assertEqual(self.refused("calendar", {"first": 0, "last": 1440,
                                                   "spans": [{"start": 0, "end": 1, "kind": "busy"}] * 33}),
                         protocol.R_TOO_LONG)
        self.assertEqual(self.refused("calendar", {"first": 0, "last": 1440, "ticks": [{"at": 0, "text": "0"}] * 14}),
                         protocol.R_TOO_LONG)
        self.assertEqual(self.refused("todos", {"rows": deep(100_000)}), protocol.R_SHAPE)
        self.assertEqual(self.refused("calendar", {"rows": [deep(100_000)]}), protocol.R_SHAPE)
        self.assertEqual(self.refused("text", {"rows": [{"text": deep(50_000)}]}), protocol.R_SHAPE)

    def test_caps_count_as_the_page_does(self) -> None:
        self.assertEqual(protocol.units("\U0001F600"), 2)
        self.assertEqual(protocol.units("a\ud800"), 2)
        thirty = "\U0001F600" * 30                                  # 30 characters, 60 UTF-16 units
        self.assertEqual(protocol.check_message(message("picture", {"caption": "x"}, title=thirty)).type, "frame")
        self.assertEqual(self.refused("todos", {"rows": [{"text": thirty + "\U0001F600"}]}), protocol.R_TOO_LONG)
        with self.assertRaises(protocol.Refused):
            protocol.check_message(message("picture", {"caption": "x"}, title=thirty + "a"))

    def test_the_body_is_rebuilt_never_the_callers_object(self) -> None:
        body = {"rows": [{"text": "Book the dentist", "meta": "5D", "state": "open"}]}
        checked = protocol.check_message(message("todos", body))
        body["rows"][0]["text"] = "<changed>"
        self.assertIn("Book the dentist", checked.text)
        self.assertNotIn("<changed>", checked.text)
        self.assertTrue(checked.text.isascii())


if __name__ == "__main__":
    unittest.main()
