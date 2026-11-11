"""Jarvis, a true agent, part A4: the private tools on their own (agent.owner over the real desktop service, the real
Ask checks and the real firewall): what each gives back, what it feeds the firewall, its caps, what is never read, what
is offered. The whole road, over the real endpoint and in the app, is tests.test_agent_things'; the hostile corpus is
tests.test_agent_hostile's.

Every name is invented; files live on a pretend disk in a temporary folder; nothing real is read or reached.
"""

from __future__ import annotations

import ntpath
import os
import unittest
from types import SimpleNamespace
from typing import Any

from briefing_reader import agent_ui
from briefing_reader import config as app_config
from briefing_reader.actions import parse_action_line
from briefing_reader.agent import firewall as fw
from briefing_reader.agent import owner, policy, tools, web
from briefing_reader.ask.context import AccountInfo
from briefing_reader.desktop import backend as dbk
from briefing_reader.desktop import guard, service
from briefing_reader.desktop import validate as dv
from briefing_reader.desktop.context import FileItem
from briefing_reader.desktop.refs import FILE, RefIndex
from tests.ask_fakes import gmail_message, gmail_thread
from tests.desktop_fakes import DOWNLOADS, pretend_path
from tests.owner_fakes import (ALL_CONNECTED, ATTACKER, BODY_WORD, CONTENT_WORD, DATA_DIR, EVENT_ID, FILE_WORD, GUEST,
                               GUEST_ADDRESS, LETTER, NOTE_WORD, PROJECT_DIR, SAID_WORD, SECRET_WORD, SUBJECT_WORD,
                               THREAD_ID, OwnerBench, invented_thread)


class Base(unittest.TestCase):
    def bench(self, goal: str = "summarise my week", **kwargs: object) -> OwnerBench:
        return OwnerBench(self, goal, **kwargs)

    def assert_clean_run(self, bench: OwnerBench) -> None:
        self.assertFalse(bench.firewall.strict)
        self.assertFalse(bench.firewall.taint)

    def ref_of(self, result: tools.ToolResult, name: str) -> str:
        """The ref of the row naming ``name`` in a listing."""
        for row in result.text.splitlines():
            if name in row:
                return row.split()[0]
        self.fail("no such row")


class CalendarTests(Base):
    def test_events_come_with_the_ids_a_line_needs_and_taint_but_no_strict(self) -> None:
        bench = self.bench()
        result = bench.call("calendar_find", **{"from": "2026-11-05", "to": "2026-11-12"})
        self.assertFalse(result.is_error)
        self.assertIn(f"event={EVENT_ID}", result.text)
        self.assertIn("cal=primary", result.text)
        self.assertIn(GUEST_ADDRESS, result.text)
        self.assertIn("<untrusted", result.text)
        self.assertTrue(bench.firewall.taint.hits(SECRET_WORD))
        self.assertTrue(bench.firewall.taint.hits(GUEST_ADDRESS))
        self.assertFalse(bench.firewall.strict)
        self.assertEqual(bench.things.sources.calls[0][0], "personal")

    def test_words_narrow_it_and_bad_days_are_refused(self) -> None:
        bench = self.bench()
        none = bench.call("calendar_find", **{"from": "2026-11-05", "to": "2026-11-12", "words": "budget"})
        self.assertIn("No events", none.text)
        self.assertFalse(bench.firewall.taint)
        found = bench.call("calendar_find", **{"from": "2026-11-05", "to": "2026-11-12", "words": SECRET_WORD})
        self.assertIn(EVENT_ID, found.text)
        for start, end in (("2026-11-12", "2026-11-05"), ("Friday", "2026-11-05"), ("2026-11-05", "2030-01-01")):
            with self.subTest(start=start):
                self.assertEqual(bench.call("calendar_find", **{"from": start, "to": end}).code, "bad_input")

    def test_a_calendar_that_cant_be_read_is_said_plainly(self) -> None:
        bench = self.bench()
        bench.things.sources.failing["personal"] = "error"
        result = bench.call("calendar_find", **{"from": "2026-11-05", "to": "2026-11-12"})
        self.assertIn("could not be read", result.text)


class ContactsMemoryConversationTests(Base):
    def test_contacts_find_people_of_the_calendar_and_taint(self) -> None:
        bench = self.bench()
        result = bench.call("contacts_find", name="Priya")
        self.assertIn(GUEST_ADDRESS, result.text)
        self.assertTrue(bench.firewall.taint.hits(GUEST_ADDRESS))
        self.assertTrue(bench.owner._index.knows(GUEST_ADDRESS))     # noqa: SLF001 - what a line may name
        self.assertFalse(bench.firewall.strict)
        self.assertIn("Nothing matched", bench.call("contacts_find", name="Nobody").text)

    def test_contacts_find_people_of_the_briefing_and_mail_read_in_this_run(self) -> None:
        bench = self.bench(f"find the {SUBJECT_WORD} mail")
        line = ("Email: acct=personal | to=Ana Lima <ana@example.edu> | cc= | subject=Hi | due= | link= | body=Hello.")
        bench.things.briefing = lambda: SimpleNamespace(pending=(parse_action_line(line),))
        self.assertIn("Ana Lima (ana@example.edu)", bench.call("contacts_find", name="ana").text)
        self.assertTrue(bench.firewall.taint.hits("ana@example.edu"))
        self.assertIn("Nothing matched", bench.call("contacts_find", name="mallory").text)
        bench.things.reader.threads[THREAD_ID] = invented_thread(sender=f"Mallory <{ATTACKER}>")
        bench.call("mail_search", words=SUBJECT_WORD)
        self.assertIn(ATTACKER, bench.call("contacts_find", name="mallory").text)

    def test_notes_taint_and_their_addresses_stay_known_not_typed(self) -> None:
        bench = self.bench()
        bench.things.notes = (f"My sister {NOTE_WORD} is at sister@example.org.",)
        result = bench.call("memory_read")
        self.assertIn(NOTE_WORD, result.text)
        self.assertTrue(bench.firewall.taint.hits(NOTE_WORD))
        self.assertFalse(bench.firewall.strict)
        self.assertTrue(bench.owner._index.knows("sister@example.org"))        # noqa: SLF001
        self.assertFalse(bench.owner._index.typed("sister@example.org"))       # noqa: SLF001

    def test_the_conversation_makes_the_run_strict_and_is_never_the_goal(self) -> None:
        bench = self.bench("do that thing we discussed")
        result = bench.call("conversation_read")
        self.assertIn(SAID_WORD, result.text)
        self.assertIn("<untrusted", result.text)
        self.assertTrue(bench.firewall.strict)
        self.assertTrue(bench.firewall.taint.hits(SAID_WORD))
        self.assertEqual(bench.firewall.said(), "do that thing we discussed")

    def test_no_conversation_is_said_plainly_but_still_strict(self) -> None:
        bench = self.bench()
        bench.things.turns = ()
        self.assertIn("Nothing was said", bench.call("conversation_read").text)
        self.assertTrue(bench.firewall.strict)
        self.assertFalse(bench.firewall.taint)                     # nothing to match: strict holds all the same
        self.assertEqual(bench.call("open_page", url="https://example.org/x").code, fw.WAITING)
        bench.decline()
        self.assertEqual(bench.call("web_search", query="Dell").code, fw.WAITING)


class MailTests(Base):
    def test_a_search_in_the_owners_words_runs_and_gives_no_body(self) -> None:
        bench = self.bench(f"find the {SUBJECT_WORD} mail from Priya")
        result = bench.call("mail_search", words=SUBJECT_WORD)
        self.assertFalse(result.is_error)
        self.assertEqual(bench.things.reader.searches, [SUBJECT_WORD])
        self.assertIn(f"thread={THREAD_ID}", result.text)
        self.assertNotIn(BODY_WORD, result.text)
        self.assertIn("[text not shown", result.text)
        self.assertFalse(bench.firewall.strict)                       # only its headers went back
        self.assertTrue(bench.firewall.taint.hits(GUEST_ADDRESS))

    def test_words_the_owner_did_not_say_are_refused_as_ask_refuses_them(self) -> None:
        bench = self.bench("summarise my week")
        result = bench.call("mail_search", words="Orionhold")
        self.assertTrue(result.is_error)
        self.assertEqual(result.code, owner.MAIL_REFUSED)
        self.assertIn("words you didn't type", result.text)
        self.assertEqual(bench.things.reader.searches, [])

    def test_passwords_and_addresses_are_refused_even_in_the_owners_words(self) -> None:
        bench = self.bench("look for my password reset and https://example.org")
        for words in ("password", "https://example.org", "from:security@example.org"):
            with self.subTest(words=words):
                self.assertEqual(bench.call("mail_search", words=words).code, owner.MAIL_REFUSED)
        self.assertEqual(bench.things.reader.searches, [])

    def test_a_person_the_calendar_supplied_counts_only_when_the_owner_named_them(self) -> None:
        bench = self.bench("what did Priya send me")
        bench.call("calendar_find", **{"from": "2026-11-05", "to": "2026-11-12"})
        self.assertFalse(bench.call("mail_search", words=f"from:{GUEST_ADDRESS}").is_error)
        other = self.bench("what happened this week")
        other.call("calendar_find", **{"from": "2026-11-05", "to": "2026-11-12"})
        self.assertEqual(other.call("mail_search", words=f"from:{GUEST_ADDRESS}").code, owner.MAIL_REFUSED)

    def test_later_words_of_the_owner_count_and_a_read_thread_does_not_ground_a_search(self) -> None:
        bench = self.bench("find the Orionhold mail")
        bench.call("mail_search", words="Orionhold")
        bench.call("mail_read", thread=THREAD_ID)
        self.assertEqual(bench.call("mail_search", words=BODY_WORD).code, owner.MAIL_REFUSED)
        bench.owner_says(f"now look for {BODY_WORD}")
        self.assertFalse(bench.call("mail_search", words=BODY_WORD).is_error)

    def test_reading_gives_the_body_as_data_and_makes_it_strict(self) -> None:
        bench = self.bench("find the Orionhold mail")
        bench.call("mail_search", words="Orionhold")
        result = bench.call("mail_read", thread=THREAD_ID)
        self.assertIn(BODY_WORD, result.text)
        self.assertIn("<untrusted", result.text)
        self.assertTrue(bench.firewall.strict)
        self.assertTrue(bench.firewall.taint.hits(BODY_WORD))

    def holding_a_key(self, bench_goal: str = "find the Orionhold mail") -> tuple[Any, Any]:
        """rf-10: a thread of two: one message with a key in it, one without."""
        key = ("Here is the new key.\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n"
               "-----END OPENSSH PRIVATE KEY-----\npassword: Hunter2Hunter2Hunter2 for Ravenscar")
        thread = gmail_thread(THREAD_ID, [
            gmail_message("msg0000001", sender=f"{GUEST} <{GUEST_ADDRESS}>", to="me@example.com", subject="Orionhold keys",
                          body=key, msgid="<CAB7@mail.example.edu>", at_ms=1791200000000),
            gmail_message("msg0000002", sender="me@example.com", to=GUEST_ADDRESS, subject="Re: Orionhold keys",
                          body=f"The {BODY_WORD} figures are 42.", msgid="<CAB8@mail.example.edu>", at_ms=1791200900000)])
        bench = self.bench(bench_goal, threads={THREAD_ID: thread})
        self.assertFalse(bench.call("mail_search", words="Orionhold").is_error)
        return bench, bench.call("mail_read", thread=THREAD_ID)

    def test_a_key_in_a_message_goes_and_the_message_and_the_thread_go_on(self) -> None:
        bench, result = self.holding_a_key()
        self.assertFalse(result.is_error)
        self.assertNotIn("BEGIN OPENSSH", result.text)
        self.assertNotIn("b3BlbnNz", result.text)
        self.assertNotIn("Hunter2", result.text)
        self.assertIn("Here is the new key", result.text)            # the words round it read
        self.assertIn(f"password: {guard.SECRET_MARK} for Ravenscar", result.text)
        self.assertEqual(result.text.count(guard.SECRET_MARK), 3)    # the block, the password, and what the model is told
        self.assertIn(BODY_WORD, result.text)                        # the rest goes on
        self.assertIn(GUEST_ADDRESS, result.text)                    # and its headers
        self.assertTrue(bench.firewall.strict)

    def test_live_warns_of_it_with_no_word_of_it(self) -> None:
        _bench, result = self.holding_a_key()
        self.assertTrue(result.warn)
        self.assertEqual(dict(result.fields)["Left out"], "2 values left out: what looks like a key or a password")
        shown = " ".join([result.note, *(value for _label, value in result.fields)])
        for word in ("Hunter2", "OPENSSH", "Ravenscar", "new key"):
            self.assertNotIn(word, shown)

    def test_what_was_left_out_feeds_nothing_and_jarvis_s_own_words_taint_nothing(self) -> None:
        bench, _result = self.holding_a_key()
        self.assertTrue(bench.firewall.taint.hits("Ravenscar"))      # what stays is read, and is held as any mail's words
        self.assertEqual(bench.firewall.taint.hits("Hunter2Hunter2Hunter2"), ())
        self.assertEqual(bench.firewall.taint.hits("b3BlbnNzaC1rZXktdjEAAAAA"), ())
        self.assertEqual(bench.firewall.taint.hits("looks"), ())     # Jarvis's own mark is no word of theirs
        self.assertTrue(bench.firewall.taint.hits(BODY_WORD))
        self.assertFalse(bench.firewall.read_words.holds(guard.SECRET_MARK))
        self.assertFalse(bench.firewall.read_words.holds("Hunter2Hunter2Hunter2"))

    def test_an_ordinary_thread_has_no_warning_for_it(self) -> None:
        bench = self.bench("find the Orionhold mail")
        bench.call("mail_search", words="Orionhold")
        result = bench.call("mail_read", thread=THREAD_ID)
        self.assertFalse(result.warn)
        self.assertNotIn("Left out", dict(result.fields))
        self.assertNotIn(guard.SECRET_MARK, result.text)
        self.assertNotIn("took out", result.text)

    def test_only_a_thread_a_search_of_this_task_found(self) -> None:
        bench = self.bench("find the Orionhold mail")
        self.assertEqual(bench.call("mail_read", thread=THREAD_ID).code, owner.UNKNOWN)
        self.assertEqual(bench.things.reader.reads, [])
        self.assertFalse(bench.firewall.strict)

    def test_at_most_ten_reads_and_ten_searches(self) -> None:
        bench = self.bench("find the Orionhold mail")
        bench.call("mail_search", words="Orionhold")
        results = [bench.call("mail_read", thread=THREAD_ID) for _ in range(owner.MAIL_READ_CAP + 1)]
        self.assertFalse(any(result.is_error for result in results[:-1]))
        self.assertEqual(results[-1].code, owner.CAP)
        self.assertIn("the most", results[-1].text)
        searches = [bench.call("mail_search", words="Orionhold") for _ in range(owner.MAIL_SEARCH_CAP)]
        self.assertEqual(searches[-1].code, owner.CAP)

    def test_a_failing_mailbox_says_so_without_its_words(self) -> None:
        bench = self.bench("find the Orionhold mail")
        bench.things.reader.error = OSError(f"{SUBJECT_WORD} broke")
        result = bench.call("mail_search", words="Orionhold")
        self.assertEqual(result.code, owner.MAIL_FAILED)
        self.assertNotIn("broke", result.text)

    def test_no_readable_mailbox_is_said_plainly(self) -> None:
        bench = self.bench("find the Orionhold mail")
        bench.things.sources.accounts_list = [AccountInfo("personal", "me@example.com", "yes", True, "no (turned off)",
                                                          "")]
        self.assertEqual(bench.call("mail_search", words="Orionhold").code, owner.MAIL_FAILED)


class FilesTests(Base):
    def test_files_find_gives_names_and_refs_never_paths_and_taints(self) -> None:
        bench = self.bench()
        result = bench.call("files_find", words=FILE_WORD)
        self.assertFalse(result.is_error)
        self.assertIn(f"{FILE_WORD} letter.txt", result.text)
        self.assertNotIn("C:\\", result.text)
        self.assertIn("[readable]", result.text)
        self.assertRegex(result.text, r"\bf\d+ \"")
        self.assertTrue(bench.firewall.taint.hits(FILE_WORD))
        self.assertFalse(bench.firewall.strict)
        self.assertEqual(set(bench.things.fake.threads), {"desktop"})     # on the desktop thread, all of it

    def test_the_allowed_folders_own_names_and_file_types_do_not_taint(self) -> None:
        bench = self.bench()
        bench.call("files_list")
        bench.call("files_list", place="Downloads")
        bench.call("files_find", words="Budget")
        for words in ("best music for downloads", "desktop pictures and videos", "convert docx documents to text"):
            with self.subTest(words=words):
                self.assertEqual(bench.firewall.taint.hits(words), ())

    def test_files_find_names_apps_the_words_name(self) -> None:
        bench = self.bench()
        result = bench.call("files_find", words="Word")
        self.assertRegex(result.text, r"\ba\d+ \"Microsoft Word\"")
        self.assertNotIn("PowerShell", result.text)

    def test_files_list_shows_the_allowed_folders_then_one_of_them(self) -> None:
        bench = self.bench()
        roots = bench.call("files_list")
        self.assertIn("Downloads", roots.text)
        self.assertIn("Documents", roots.text)
        ref = self.ref_of(roots, "Downloads")
        listed = bench.call("files_list", place=ref)
        self.assertIn(f"{FILE_WORD} letter.txt", listed.text)
        self.assertEqual(bench.call("files_list", place="Downloads").text.count("letter.txt"), 1)
        self.assertEqual(bench.call("files_list", place="d99").code, owner.UNKNOWN)
        self.assertEqual(bench.call("files_list", place="Windows").code, owner.UNKNOWN)

    def test_reading_is_by_a_ref_of_this_run_and_makes_it_strict(self) -> None:
        bench = self.bench()
        ref = self.ref_of(bench.call("files_find", words=FILE_WORD), FILE_WORD)
        result = bench.call("file_read", ref=ref)
        self.assertIn(CONTENT_WORD, result.text)
        self.assertIn("<untrusted", result.text)
        self.assertTrue(bench.firewall.strict)
        self.assertTrue(bench.firewall.taint.hits(CONTENT_WORD))

    def test_twelve_thousand_characters_a_call_then_the_offset(self) -> None:
        bench = self.bench()
        words = "".join(f"{number:06d} " for number in range(3000))         # 21,000 characters
        bench.things.fake.make_file(f"{DOWNLOADS}\\long list.txt", words.encode())
        ref = self.ref_of(bench.call("files_find", words="long list"), "long list")
        first = bench.call("file_read", ref=ref)
        self.assertIn("offset 12000", first.text)
        self.assertIn(words[:100], first.text)
        self.assertNotIn(words[12000:12100], first.text)
        second = bench.call("file_read", ref=ref, offset=12000)
        self.assertIn(words[12000:12100], second.text)
        self.assertNotIn("offset", second.text.replace("offset=", ""))
        self.assertIn("all of this file", bench.call("file_read", ref=ref, offset=len(words) + 5).text)
        self.assertEqual(bench.call("file_read", ref=ref, offset=-1).code, "bad_input")

    def test_utf8_utf16_and_windows_text_all_read_and_binary_does_not(self) -> None:
        bench = self.bench()
        fake = bench.things.fake
        fake.make_file(f"{DOWNLOADS}\\u8.md", "caf\u00e9 r\u00e9sum\u00e9".encode())
        fake.make_file(f"{DOWNLOADS}\\u16.csv", "\ufeffcaf\u00e9,1".encode("utf-16-le"))
        fake.make_file(f"{DOWNLOADS}\\win.log", "caf\u00e9".encode("cp1252"))
        fake.make_file(f"{DOWNLOADS}\\pretend.txt", b"\x00\x01\x02binary")
        listed = bench.call("files_list", place="Downloads")
        for name, word in (("u8.md", "caf\u00e9"), ("u16.csv", "caf\u00e9"), ("win.log", "caf\u00e9")):
            with self.subTest(name=name):
                self.assertIn(word, bench.call("file_read", ref=self.ref_of(listed, name)).text)
        self.assertEqual(bench.call("file_read", ref=self.ref_of(listed, "pretend.txt")).code, owner.UNREADABLE)

    def test_other_types_are_said_plainly(self) -> None:
        bench = self.bench()
        ref = self.ref_of(bench.call("files_find", words="Budget"), "Budget 2026.xlsx")
        result = bench.call("file_read", ref=ref)
        self.assertEqual(result.code, owner.UNREADABLE)
        self.assertIn("can't read that type of file yet", result.text)
        self.assertIn(".csv", result.text)
        self.assertFalse(bench.firewall.strict)

    def test_at_most_thirty_reads(self) -> None:
        bench = self.bench()
        ref = self.ref_of(bench.call("files_find", words=FILE_WORD), FILE_WORD)
        results = [bench.call("file_read", ref=ref) for _ in range(owner.FILE_READ_CAP + 1)]
        self.assertFalse(any(result.is_error for result in results[:-1]))
        self.assertEqual(results[-1].code, owner.CAP)

    def test_a_task_is_stopped_or_past_its_time_while_the_desktop_thread_is_busy(self) -> None:
        import threading

        bench = self.bench()
        gate = threading.Event()
        bench.things.worker.submit(gate.wait, 5.0)
        bench.ctx.tool_s = 2.0                                    # web.Call's margin leaves it a second
        result = bench.call("files_find", words=FILE_WORD)
        gate.set()
        self.assertEqual(result.code, web.W_TOO_SLOW)
        bench.things.worker.join(0.0)


class SecretsAndScopeTests(Base):
    """R6. Temp folders and fakes only: each is on the pretend disk."""

    def forged(self, bench: OwnerBench, path: str) -> str:
        """A ref as if it had been listed: what a read checks must hold even then."""
        fake = bench.things.fake
        item = FileItem(path, ntpath.basename(path), identity=fake.identity(path), size=1)
        return bench.owner._refs.issue(FILE, item, item.name)      # noqa: SLF001

    def test_secrets_are_never_listed_and_never_read(self) -> None:
        bench = self.bench()
        fake = bench.things.fake
        secrets = (".env", "token.json", "google_token_personal.json", "client_secret_x.json", "credentials.json",
                   "id_rsa",
                   "wallet.dat", "vault.kdbx", "notes.key", ".npmrc")
        for name in secrets:
            fake.make_file(f"{DOWNLOADS}\\{name}", b"SECRET-VALUE")
        listed = bench.call("files_list", place="Downloads").text
        found = bench.call("files_find", words="token").text
        for name in secrets:
            with self.subTest(name=name):
                self.assertNotIn(name, listed)
                self.assertNotIn(name, found)
                result = bench.call("file_read", ref=self.forged(bench, f"{DOWNLOADS}\\{name}"))
                self.assertTrue(result.is_error)
                self.assertNotIn("SECRET-VALUE", result.text)
        self.assertFalse(bench.firewall.strict)
        self.assertEqual(fake.asked("read_head"), [])

    def test_a_text_file_named_for_secrets_is_listed_but_never_read(self) -> None:
        bench = self.bench()
        fake = bench.things.fake
        for name in ("My passwords.txt", "api keys.md", "bank details.csv", "Accounts.txt", "recovery codes.txt",
                     "logins.csv"):
            fake.make_file(f"{DOWNLOADS}\\{name}", b"SECRET-VALUE")
            with self.subTest(name=name):
                self.assertTrue(guard.sensitive_file_name(name))
                result = bench.call("file_read", ref=self.forged(bench, f"{DOWNLOADS}\\{name}"))
                self.assertIn("secrets or sign-ins", result.text)
        self.assertEqual(fake.asked("read_head"), [])
        self.assertFalse(guard.sensitive_file_name("Trip plan.txt"))

    def test_jarvis_project_and_data_folder(self) -> None:
        bench = self.bench()
        fake = bench.things.fake
        for folder in (PROJECT_DIR, DATA_DIR):
            path = fake.make_file(f"{folder}\\notes.txt", b"SECRET-VALUE")
            with self.subTest(folder=folder):
                self.assertTrue(bench.call("file_read", ref=self.forged(bench, path)).is_error)
        # ... even when one of the allowed folders holds it: it is a place Jarvis never touches.
        bench.things.service._excluded = (*bench.things.service._excluded, f"{DOWNLOADS}\\Jarvis data")   # noqa: SLF001
        bench.things.service._scope = None                         # noqa: SLF001 - read afresh
        path = fake.make_file(f"{DOWNLOADS}\\Jarvis data\\ask_usage.txt", b"SECRET-VALUE")
        self.assertTrue(bench.call("file_read", ref=self.forged(bench, path)).is_error)
        self.assertEqual(fake.asked("read_head"), [])

    def test_a_code_marker_folder(self) -> None:
        bench = self.bench()
        fake = bench.things.fake
        path = fake.make_file(f"{DOWNLOADS}\\tool\\README.txt", b"SECRET-VALUE")
        fake.make_dir(f"{DOWNLOADS}\\tool\\.git")
        self.assertNotIn("README.txt", bench.call("files_find", words="README").text)
        self.assertNotIn("tool", bench.call("files_list", place="Downloads").text)
        self.assertTrue(bench.call("file_read", ref=self.forged(bench, path)).is_error)
        self.assertEqual(fake.asked("read_head"), [])

    def test_a_junction_out_of_scope(self) -> None:
        bench = self.bench()
        fake = bench.things.fake
        outside = fake.make_file(pretend_path("Private", "diary.txt"), b"SECRET-VALUE")
        fake.junction(f"{DOWNLOADS}\\Shortcut", pretend_path("Private"))
        self.assertNotIn("Shortcut", bench.call("files_list", place="Downloads").text)
        self.assertNotIn("diary", bench.call("files_find", words="diary").text)
        for path in (f"{DOWNLOADS}\\Shortcut\\diary.txt", outside):
            with self.subTest(path=path):
                self.assertTrue(bench.call("file_read", ref=self.forged(bench, path)).is_error)
        self.assertEqual(fake.asked("read_head"), [])

    def test_online_only_would_download_so_it_is_refused(self) -> None:
        bench = self.bench()
        fake = bench.things.fake
        fake.set_attributes(LETTER, dbk.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
        ref = self.forged(bench, LETTER)
        result = bench.call("file_read", ref=ref)
        self.assertIn("online-only", result.text)
        self.assertEqual(fake.asked("read_head"), [])

    def test_a_ref_from_another_run_means_nothing_here(self) -> None:
        first = self.bench()
        ref = self.ref_of(first.call("files_find", words=FILE_WORD), FILE_WORD)
        second = self.bench()
        self.assertEqual(second.call("file_read", ref=ref).code, dv.UNKNOWN_REF)
        self.assertEqual(second.call("file_read", ref="C:\\a.txt").code, dv.UNKNOWN_REF)

    def test_a_file_swapped_after_its_ref_was_issued(self) -> None:
        bench = self.bench()
        fake = bench.things.fake
        ref = self.ref_of(bench.call("files_find", words=FILE_WORD), FILE_WORD)
        os.remove(fake.real(LETTER))
        fake.make_file(LETTER, b"SWAPPED-VALUE")
        result = bench.call("file_read", ref=ref)
        self.assertEqual(result.code, owner.UNREADABLE)
        self.assertIn("changed since", result.text)
        self.assertNotIn("SWAPPED-VALUE", result.text)
        self.assertFalse(bench.firewall.strict)

    def test_a_file_swapped_between_the_check_and_the_read(self) -> None:
        bench = self.bench()
        fake = bench.things.fake
        ref = self.ref_of(bench.call("files_find", words=FILE_WORD), FILE_WORD)

        def swap(method: str, _argument: str) -> None:
            if method == "read_head":
                fake.on_call = None
                os.remove(fake.real(LETTER))
                fake.make_file(LETTER, b"SWAPPED-VALUE")

        fake.on_call = swap
        result = bench.call("file_read", ref=ref)
        self.assertEqual(result.code, owner.UNREADABLE)
        self.assertNotIn("SWAPPED-VALUE", result.text)

    def test_the_tasks_own_folder_is_never_handed_on(self) -> None:
        bench = self.bench("open the letter")
        fake = bench.things.fake
        path = fake.make_file(f"{DOWNLOADS}\\Workspace\\page.txt", b"from a page")
        bench.owner._workspace = ntpath.normcase(f"{DOWNLOADS}\\Workspace")   # noqa: SLF001
        self.assertNotIn("page.txt", bench.call("files_find", words="page").text)
        ref = self.forged(bench, path)
        self.assertIn("task's own folder", bench.call("file_read", ref=ref).text)
        self.assertIn("task's own folder", bench.call("open_thing", ref=ref).text)
        self.assertEqual(fake.opened, [])


class ConsentAtEveryCallTests(Base):
    CALLS = (("calendar_find", {"from": "2026-11-05", "to": "2026-11-06"}), ("contacts_find", {"name": "Priya"}),
             ("memory_read", {}), ("conversation_read", {}), ("mail_search", {"words": "week"}),
             ("mail_read", {"thread": THREAD_ID}), ("files_find", {"words": "letter"}), ("files_list", {}),
             ("file_read", {"ref": "f1"}),
             ("propose_action", {"lines": ["Todo: title=x | due=2026-11-06 10:00 | block= | acct= | link="]}),
             ("propose_files", {"steps": [{"skill": "make_folder", "id": "n1", "place": "d1", "name": "x"}]}),
             ("open_thing", {"site": "https://example.org/"}), ("pc_control", {"kind": "volume", "action": "mute"}))

    def test_turn_off_stops_every_one_at_once(self) -> None:
        bench = self.bench("summarise my week")
        bench.things.on = False
        for name, args in self.CALLS:
            with self.subTest(tool=name):
                result = bench.call(name, **args)
                self.assertEqual(result.code, owner.OFF)
                self.assertIn("turned this off", result.text)
        self.assertFalse(bench.firewall.taint)
        self.assertFalse(bench.firewall.strict)
        self.assertEqual(bench.things.sources.calls, [])
        self.assertEqual(bench.things.fake.calls, [])

    def test_an_owner_that_cant_answer_is_a_no(self) -> None:
        bench = self.bench()

        def broken() -> bool:
            raise OSError("no prefs")

        bench.things.consent = broken
        self.assertEqual(bench.call("memory_read").code, owner.OFF)

    def test_a_stopped_run_reads_nothing(self) -> None:
        bench = self.bench()
        bench.stopped = True
        self.assertEqual(bench.call("memory_read").code, web.W_STOPPED)
        self.assertFalse(bench.firewall.taint)

    def test_no_owner_behind_a_tool_refuses_it(self) -> None:
        bench = self.bench()
        bench.ctx.owner = None
        self.assertEqual(bench.call("memory_read").code, tools.NO_OWNER)


class OfferedTests(unittest.TestCase):
    NAMES = {"calendar": {"calendar_find"}, "contacts": {"contacts_find"}, "memory": {"memory_read",
                                                                                      "conversation_read"},
             "mail": {"mail_search", "mail_read"}, "files": {"files_find", "files_list", "file_read", "propose_files"}}

    @staticmethod
    def config(*private: str) -> SimpleNamespace:
        return SimpleNamespace(agent=SimpleNamespace(private=private))

    @staticmethod
    def names(*args: object, **kwargs: object) -> set[str]:
        return {tool.name for tool in policy.offered(*args, **kwargs)}

    def test_all_of_them_when_connected_and_allowed(self) -> None:
        names = self.names(self.config(*app_config.AGENT_PRIVATE), connected=ALL_CONNECTED)
        self.assertEqual(names, {tool.name for tool in tools.TOOLS})

    def test_nothing_of_them_without_a_connection(self) -> None:
        names = self.names(self.config(*app_config.AGENT_PRIVATE))
        for group in self.NAMES.values():
            self.assertFalse(group & names)
        self.assertFalse({"propose_action", "open_thing", "pc_control"} & names)
        self.assertIn("web_search", names)

    def test_no_mail_tools_without_mail_connected(self) -> None:
        names = self.names(self.config(*app_config.AGENT_PRIVATE), connected=ALL_CONNECTED - {"mail"})
        self.assertFalse(self.NAMES["mail"] & names)
        self.assertIn("calendar_find", names)

    def test_private_narrows_it(self) -> None:
        for allowed, group in self.NAMES.items():
            with self.subTest(allowed=allowed):
                names = self.names(self.config(allowed), connected=ALL_CONNECTED)
                for other, tools_of in self.NAMES.items():
                    if other == allowed:
                        self.assertEqual(tools_of & names, group)
                    else:
                        self.assertFalse(tools_of & names)

    def test_anything_unknown_allows_nothing(self) -> None:
        for private in (("gmail",), ("CALENDAR",), (1,), ()):
            with self.subTest(private=private):
                names = self.names(self.config(*private), connected=ALL_CONNECTED)
                self.assertFalse(set().union(*self.NAMES.values()) & names)
        odd = SimpleNamespace(agent=SimpleNamespace(private="calendar"))
        self.assertFalse(self.NAMES["calendar"] & self.names(odd, connected=ALL_CONNECTED))
        self.assertFalse(self.NAMES["mail"] & self.names(self.config("calendar"), connected={"gmail", "calendar"}))

    def test_what_the_app_says_is_connected_with_sources_that_only_know_their_accounts(self) -> None:
        accounts = [AccountInfo("personal", "me@example.com", "not signed in", False, "no (turned off)", "")]
        planner = SimpleNamespace(config=None, sources=SimpleNamespace(accounts=lambda: accounts), memory=None)
        things = agent_ui.AppThings(planner, SimpleNamespace(), conversation=tuple, take_cards=print,
                                    withdraw_cards=print)
        self.assertEqual(things.connected(), {"desk", "files", "conversation"})
        accounts[0] = AccountInfo("personal", "me@example.com", "yes", True, "yes", "")
        planner.memory = tuple
        self.assertEqual(things.connected(), ALL_CONNECTED)

        def broken() -> list:
            raise OSError("no")

        planner.sources = SimpleNamespace(accounts=broken)
        things.sources = planner.sources
        planner.memory = None
        self.assertEqual(things.connected(), {"desk", "files", "conversation"})

    def test_the_setting_reads_as_the_brief_says(self) -> None:
        parse = app_config._parse_agent                         # noqa: SLF001
        self.assertEqual(parse({}).private, app_config.AGENT_PRIVATE)
        self.assertEqual(parse({"agent": {"private": ["mail", " Calendar "]}}).private, ("calendar", "mail"))
        self.assertEqual(parse({"agent": {"private": []}}).private, ())
        for value in ("mail", "calendar", "none", "", False, 0, {"a": 1}):     # present and not a list: nothing
            with self.subTest(value=value), self.assertLogs("briefing_reader.config", "WARNING") as told:
                self.assertEqual(parse({"agent": {"private": value}}).private, ())
                self.assertIn("grants nothing", told.output[0])
        with self.assertLogs("briefing_reader.config", "WARNING"):
            self.assertEqual(parse({"agent": {"private": ["mail", "photos", 3]}}).private, ("mail",))


class TaintTests(unittest.TestCase):
    def test_a_long_run_of_digits_taints_by_any_seven_in_a_row(self) -> None:
        taint = fw.TaintSet("week")
        taint.add(["Account 1234567890123456789 is open"])
        self.assertTrue(taint.hits("look up 1234567890123456789"))
        self.assertTrue(taint.hits("look up 3456789012"))
        self.assertFalse(taint.hits("look up 123456"))

    def test_phone_numbers_still_match_without_their_country_code(self) -> None:
        taint = fw.TaintSet("week")
        taint.add(["call +44 20 7946 0958"])
        self.assertTrue(taint.hits("who is 020 7946 0958"))

    def test_a_word_hidden_in_base64_or_hex_is_that_word(self) -> None:
        taint = fw.TaintSet("week")
        taint.add([f"Dinner with {SECRET_WORD}"])
        for hidden in ("WmVwaHlyaW5l", "WmVwaHlyaW5l".rstrip("="), SECRET_WORD.encode().hex()):
            with self.subTest(hidden=hidden):
                self.assertTrue(taint.hits(f"news about {hidden}"))

    def test_strict_words_are_everything_but_the_owners_and_the_common(self) -> None:
        taint = fw.TaintSet("cheap laptops")
        self.assertEqual(taint.foreign("the best cheap laptops"), ())
        self.assertEqual(taint.foreign("cheap laptops Sam 2026"), ("2026", "sam"))
        self.assertEqual(taint.foreign("%5A%65%70%68%79%72"), ("5a", "65", "68", "70", "72", "79", "zephyr"))
        taint.clear(("sam",))
        self.assertEqual(taint.foreign("cheap laptops Sam"), ())


class ServiceReadTests(unittest.TestCase):
    def test_decoding(self) -> None:
        self.assertEqual(service.decode_text("caf\u00e9".encode()), "caf\u00e9")
        self.assertEqual(service.decode_text(b"\xef\xbb\xbfabc"), "abc")
        self.assertEqual(service.decode_text("\ufeffabc".encode("utf-16-le")), "abc")
        self.assertEqual(service.decode_text(b"caf\xe9 au lait"), "caf\u00e9 au lait")
        self.assertIsNone(service.decode_text(b"ab\x00cd"))

    def test_the_real_backend_refuses_while_the_tests_run(self) -> None:
        self.assertTrue(getattr(dbk.Win32Backend.read_head, "reaches_os", False))


class RefsNeverNamePathsTests(Base):
    def test_a_path_is_never_a_ref(self) -> None:
        index = RefIndex("r")
        self.assertIsNone(index.resolve(LETTER))
        self.assertIsNone(index.resolve("f0"))

    def test_nothing_of_a_refused_read_taints(self) -> None:
        bench = self.bench()
        ref = self.ref_of(bench.call("files_find", words="Budget"), "Budget 2026.xlsx")
        before = repr(bench.firewall.taint)
        bench.call("file_read", ref=ref)
        self.assertEqual(repr(bench.firewall.taint), before)


class ReaderThreadTests(Base):
    def test_threads_found_before_are_not_read_again(self) -> None:
        bench = self.bench("find the Orionhold mail")
        bench.call("mail_search", words="Orionhold")
        bench.call("mail_search", words="Orionhold")
        self.assertEqual(bench.things.reader.reads, [THREAD_ID])

    def test_an_odd_thread_is_left_out(self) -> None:
        bench = self.bench("find the Orionhold mail", threads={"bad": {"messages": []}}, found=("bad",))
        self.assertIn("Nothing matched", bench.call("mail_search", words="Orionhold").text)


if __name__ == "__main__":
    unittest.main()
