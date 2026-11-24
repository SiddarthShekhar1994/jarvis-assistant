"""Jarvis filling in pages, part 1 (build plan item 7; no Qt): the controls and their refs, what is never typed, the
seal before the first value, free calls and the calls that bring the Send card, the card's lines, Jarvis's own press
after the owner's click, tabs, the caps, Pause, the spoken lines, the firewall on a link, the policy that offers them, and
the run's Send card waits (Q_SUBMIT: Send, Don't, no answer, typed words, Stop).

The browser here is tests.agent_fakes.FakeDriveBrowser: it reads the drive scripts' op marks and arguments and answers as
the real scripts would. Nothing reaches a network or a real browser; every value is invented; files go only to
temporary folders.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from briefing_reader.agent import drive, policy, tools, web
from briefing_reader.agent import runner as ar
from briefing_reader.agent.firewall import ReadWords, WebFirewall
from briefing_reader.agent.tools import Registry, RunContext
from briefing_reader.agent.workspace import TaskFolder
from briefing_reader.config import AGENT_PRIVATE
from tests.agent_fakes import FAST, FakeControl, FakeDriveBrowser, FakeEngine, FakePage
from tests.test_agent_run import RunCase

WHEN = datetime(2026, 11, 23, 9, 30, tzinfo=timezone.utc)
FORM_URL = "https://www.example.org/apply"
DONE_URL = "https://www.example.org/thanks"
OTHER_URL = "https://www.example.org/other"
HELP_URL = "https://help.example.net/start"
ON = SimpleNamespace(agent=SimpleNamespace(drive=True, private=AGENT_PRIVATE))
OFF = SimpleNamespace(agent=SimpleNamespace(drive=False, private=AGENT_PRIVATE))
NAME, EMAIL = "Ada Okafor", "ada.okafor@example.com"
TIMING = replace(FAST, tool_s=30.0)


def form_page(**changes: Any) -> FakePage:
    """e1 name, e2 email, e3 country, e4 news, e5 Apply now (submit), e6 a link, e7 Show more, e8 a password field."""
    page = FakePage(title="Apply", text="Fill this in.", final=FORM_URL,
                    forms=[("post", "https://www.example.org/apply/send")],
                    controls=[FakeControl("textbox", "Full name", kind="text", form=1, field="name"),
                              FakeControl("textbox", "Email", kind="email", form=1, field="email", required=True),
                              FakeControl("select", "Country", options=["Choose", "Kenya", "Portugal"], value="Choose",
                                          form=1, field="country"),
                              FakeControl("checkbox", "Send me news", form=1, field="news"),
                              FakeControl("submit", "Apply now", form=1, goes_to=DONE_URL),
                              FakeControl("link", "Help", href=HELP_URL, blank=True),
                              FakeControl("button", "Show more"),
                              FakeControl("textbox", "Password", kind="password", form=1, field="pw")])
    for name, value in changes.items():
        setattr(page, name, value)
    return page


def pages() -> dict[str, FakePage]:
    return {FORM_URL: form_page(), DONE_URL: FakePage(title="Thanks", text="We have your application.", final=DONE_URL),
            OTHER_URL: FakePage(title="Other", text="Another page.", final=OTHER_URL),
            HELP_URL: FakePage(title="Help", text="How to apply.", final=HELP_URL)}


def tainted(text: str) -> tuple[str, ...]:
    return ("okafor",) if "okafor" in text.casefold() else ()


class DriveCase(unittest.TestCase):
    """One DriveSession over a FakeDriveBrowser, its tools called through the registry the policy offers."""

    def setUp(self) -> None:
        self.browser = FakeDriveBrowser(pages())
        self.session = drive.DriveSession(self.browser, caps=drive.DriveCaps())
        self.registry = Registry(policy.offered(ON, drive_consent=True))
        self.plans: list[drive.SendPlan] = []
        self.said: list[str] = []
        self.shown: list[str] = []
        self.accept = True
        self.clock = [1000.0]
        self.context = RunContext(run_id="r1", web=self.session, tool_s=45.0, on_submit=self.submit,
                                  taint_hits=tainted, on_say=self.said.append, on_shown=self.shown.append,
                                  read_words=ReadWords(), clock=lambda: self.clock[0])

    def submit(self, plan: drive.SendPlan) -> bool:
        if self.accept:
            self.plans.append(plan)
        return self.accept

    def call(self, name: str, /, **args: Any) -> tools.ToolResult:
        self.clock[0] += 10.0                   # past the line rate, unless a test says otherwise
        return self.registry.call(name, args, self.context)

    def opened(self, address: str = FORM_URL) -> tools.ToolResult:
        result = self.call("open_page", url=address)
        self.assertFalse(result.is_error, result.text)
        return result

    def listed(self) -> tools.ToolResult:
        result = self.call("list_controls")
        self.assertFalse(result.is_error, result.text)
        return result

    def ready(self) -> None:
        self.opened()
        self.listed()

    def page(self, address: str = FORM_URL) -> FakePage:
        return self.browser.pages[address]

    def decided(self, plan: drive.SendPlan, allowed: bool) -> drive.SendOutcome | None:
        heard: list[drive.SendOutcome] = []
        done = threading.Event()

        def report(outcome: drive.SendOutcome) -> None:
            heard.append(outcome)
            done.set()

        plan.decide(allowed, report if allowed else None, lambda: False)
        if allowed:
            self.assertTrue(done.wait(5), "no outcome came")
            return heard[0]
        deadline = time.monotonic() + 5
        while not self.browser.closed_tabs and time.monotonic() < deadline:
            time.sleep(0.005)
        return None


# --------------------------------------------------------------------------
# What is never typed
# --------------------------------------------------------------------------

class NeverTypedTests(unittest.TestCase):
    def control(self, role: str = drive.R_TEXTBOX, **details: Any) -> drive.Control:
        return drive.Control(1, role, **details)

    def test_secret_fields_are_refused(self) -> None:
        for control in (self.control(kind="password", name="Choose one"),
                        self.control(autocomplete="cc-number", name="Number"),
                        self.control(autocomplete="shipping cc-exp", name="Valid until"),
                        self.control(autocomplete="one-time-code", name="Code"),
                        self.control(autocomplete="new-password", name="Again"),
                        self.control(name="PIN"), self.control(name="Card number"),
                        self.control(name="Your answer", hints="cvv2"), self.control(name="IBAN"),
                        self.control(name="Social security number")):
            with self.subTest(control=control):
                self.assertEqual(drive.field_refused(control, FORM_URL, "Apply")[0], drive.D_SECRET_FIELD)

    def test_file_inputs_and_payment_pages_are_refused(self) -> None:
        self.assertEqual(drive.field_refused(self.control(drive.R_FILE, name="CV"), FORM_URL, "Apply")[0],
                         drive.D_FILE_FIELD)
        self.assertEqual(drive.field_refused(self.control(name="Name"), "https://shop.example.org/checkout/step1",
                                             "Your details")[0], drive.D_PAYMENT)
        self.assertEqual(drive.field_refused(self.control(name="Name"), FORM_URL, "Payment details")[0],
                         drive.D_PAYMENT)

    def test_ordinary_fields_are_free(self) -> None:
        for name in ("Full name", "Email", "Why do you want this role?", "Town", "Phone"):
            with self.subTest(name=name):
                self.assertIsNone(drive.field_refused(self.control(name=name, autocomplete="email"), FORM_URL, "Apply"))

    def test_values_never_typed(self) -> None:
        for text, code in (("4111 1111 1111 1111", drive.D_CARD_NUMBER), ("4111-1111-1111-1111", drive.D_CARD_NUMBER),
                           ("5500005555555559", drive.D_CARD_NUMBER), ("GB82 WEST 1234 5698 7654 32", drive.D_IBAN),
                           ("de89370400440532013000", drive.D_IBAN), ("123-45-6789", drive.D_NATIONAL_ID),
                           ("AB 12 34 56 C", drive.D_NATIONAL_ID), ("2345 6789 0123", drive.D_NATIONAL_ID),
                           ("password: Hunter22Zq", drive.D_SECRET_VALUE),
                           ("sk-" + "ant-api03-" + "abcdefghijklmnopqrstuvwxyz0123456789", drive.D_SECRET_VALUE)):
            with self.subTest(text=text):
                self.assertEqual(drive.value_refused(text)[0], code)

    def test_ordinary_values_are_free(self) -> None:
        for text in (NAME, EMAIL, "+1 (206) 555-0142", "4111 1111 1111 1112", "I would like to apply for the role.",
                     "2026-11-23", "SW1A 1AA", ""):
            with self.subTest(text=text):
                self.assertIsNone(drive.value_refused(text))

    def test_commit_words(self) -> None:
        for words in ("Buy now", "Pay", "Sign in", "Post", "Delete", "Confirm", "Subscribe", "Sign up", "Place order"):
            self.assertTrue(drive.commits(words), words)
        for words in ("Show more", "Read the guide", "Close"):
            self.assertFalse(drive.commits(words), words)


# --------------------------------------------------------------------------
# The controls
# --------------------------------------------------------------------------

class ControlsTests(unittest.TestCase):
    def raw(self, rows: list[dict[str, Any]], gen: str = "g1", **more: Any) -> str:
        import json

        return json.dumps({"gen": gen, "url": FORM_URL, "title": "Apply", "controls": rows,
                           "forms": [["post", "https://forms.example.net/x"]], "left": 2, **more})

    def test_checked_and_capped(self) -> None:
        rows = [{"role": "button", "name": f"B{i}"} for i in range(drive.MAX_CONTROLS + 5)]
        found = drive.read_controls(self.raw(rows), "g1")
        self.assertEqual(len(found.controls), drive.MAX_CONTROLS)
        self.assertTrue(found.more)
        self.assertEqual(found.left_out, 2)
        self.assertEqual(found.controls[0].ref, "e1")

    def test_another_gen_is_no_answer(self) -> None:
        self.assertIsNone(drive.read_controls(self.raw([{"role": "button"}]), "g2"))
        self.assertIsNone(drive.read_controls("not json", "g1"))
        self.assertIsNone(drive.read_controls(web.NO_PAGE, "g1"))

    def test_a_role_it_does_not_know_and_a_link_that_is_not_public(self) -> None:
        found = drive.read_controls(self.raw([{"role": "slider"}, {"role": "link", "name": "x",
                                                                   "href": "http://192.168.1.4/a"},
                                              {"role": "link", "name": "y", "href": HELP_URL}]), "g1")
        self.assertEqual([c.role for c in found.controls], ["link", "link"])
        self.assertEqual([c.href for c in found.controls], ["", HELP_URL])

    def test_the_listing_says_what_a_screen_reader_name_is_and_where_a_form_goes(self) -> None:
        found = drive.read_controls(self.raw([{"role": "textbox", "kind": "email", "name": "Email", "sr": 1,
                                               "required": True, "value": "", "form": 1},
                                              {"role": "select", "name": "Country", "options": ["Kenya", "Peru"],
                                               "value": "Kenya", "form": 1}]), "g1")
        rows = drive.listing(found).splitlines()
        self.assertEqual(rows[0], 'e1 textbox (email) "Email" (a screen-reader name only) required value "" '
                                  '[form 1: post to forms.example.net]')
        self.assertIn('chosen "Kenya"; options: "Kenya", "Peru"', rows[1])

    def test_refs(self) -> None:
        self.assertEqual([drive.parse_ref(r) for r in ("e1", "E12", " e3 ", "e0", "x1", "e", 3, "e12345")],
                         [1, 12, 3, 0, 0, 0, 0, 0])

    def test_no_repr_holds_a_value(self) -> None:
        control = drive.Control(1, drive.R_TEXTBOX, name="Secret label", value="ada.okafor@example.com",
                                href=HELP_URL)
        plan = drive.SendPlan("p1", 1, 1, 1, "g", "www.example.org", "Apply", (drive.SendRow("Email", EMAIL),))
        for shown in (repr(control), repr(plan), repr(drive.SendRow("Email", EMAIL)), repr(drive.DriveSession(None))):
            for word in ("okafor", "Secret label", "example", "Apply", "Email"):
                self.assertNotIn(word, shown)


class ListTests(DriveCase):
    def test_refs_inside_an_untrusted_block_and_hidden_ones_left_out(self) -> None:
        self.page().controls.insert(0, FakeControl("textbox", "Hidden", hid=True))
        self.opened()
        result = self.listed()
        self.assertIn("<untrusted ", result.text)
        self.assertIn('e2 textbox (email) "Email" required value ""', result.text)
        self.assertIn("1 control(s) the owner can't see were left out", result.text)
        self.assertNotIn('"Hidden"', result.text)
        self.assertTrue(result.text.startswith("Tab 1 of 1."))

    def test_a_ref_from_another_page_is_refused(self) -> None:
        self.ready()
        self.opened(OTHER_URL)
        result = self.call("type_text", ref="e1", text=NAME)
        self.assertEqual(result.code, drive.D_NO_REF)
        self.assertEqual(self.browser.typed_sealed, [])

    def test_a_stale_ref_says_list_again(self) -> None:
        self.ready()
        self.page().gen = "the page changed"      # what a new document does to the isolated world's store
        result = self.call("type_text", ref="e1", text=NAME)
        self.assertEqual(result.code, drive.D_STALE)
        self.assertIn("list_controls again", result.text)
        self.assertEqual(self.page().controls[0].value, "")

    def test_an_unknown_ref(self) -> None:
        self.ready()
        self.assertEqual(self.call("type_text", ref="e40", text=NAME).code, drive.D_NO_REF)


# --------------------------------------------------------------------------
# The seal, free calls and the Send card
# --------------------------------------------------------------------------

class SealTests(DriveCase):
    def test_the_window_is_sealed_before_the_first_value(self) -> None:
        self.ready()
        result = self.call("type_text", ref="e1", text=NAME)
        self.assertFalse(result.is_error, result.text)
        self.assertIn("sealed", result.text)
        self.assertEqual(self.browser.seals, [True])          # sealed, its open connections cut
        self.assertEqual(self.browser.typed_sealed, [True])
        self.assertEqual(self.page().controls[0].value, NAME)
        self.call("type_text", ref="e2", text=EMAIL)
        self.assertEqual(self.browser.seals, [True])          # once
        self.assertEqual(self.session.holding(), 1)

    def test_no_seal_no_typing(self) -> None:
        self.browser.seal_ok = False
        self.ready()
        result = self.call("type_text", ref="e1", text=NAME)
        self.assertEqual(result.code, drive.D_NO_SEAL)
        self.assertEqual(self.browser.typed_sealed, [])
        self.assertEqual(self.page().controls[0].value, "")

    def test_choose_seals_too(self) -> None:
        self.ready()
        result = self.call("choose", ref="e3", option="Kenya")
        self.assertFalse(result.is_error, result.text)
        self.assertEqual(self.browser.seals, [True])
        self.assertEqual(self.page().controls[2].value, "Kenya")
        self.assertEqual(self.call("choose", ref="e3", option="Mars").code, drive.D_NO_OPTION)
        self.assertFalse(self.call("choose", ref="e4", checked=True).is_error)
        self.assertTrue(self.page().controls[3].checked)
        self.assertEqual(self.call("choose", ref="e4").code, tools.BAD_INPUT)

    def test_while_sealed_nothing_loads_and_links_do_not_open(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        loads = list(self.browser.loads)
        for name, args in (("open_page", {"url": OTHER_URL}), ("open_page", {"url": OTHER_URL, "tab": "new"}),
                           ("web_search", {"query": "quiet laptops"}), ("click", {"ref": "e6"})):
            with self.subTest(name=name, args=args):
                result = self.call(name, **args)
                self.assertEqual(result.code, drive.D_SEALED)
                self.assertIn("close_tab 1", result.text)
        self.assertEqual(self.browser.loads, loads)
        self.assertEqual(self.browser.new_tabs, [])

    def test_never_typed_fields_and_values_are_refused_and_flagged(self) -> None:
        self.ready()
        for name, args, code in (("type_text", {"ref": "e8", "text": "Correct horse"}, drive.D_SECRET_FIELD),
                                 ("type_text", {"ref": "e1", "text": "4111 1111 1111 1111"}, drive.D_CARD_NUMBER),
                                 ("type_text", {"ref": "e1", "text": "GB82 WEST 1234 5698 7654 32"}, drive.D_IBAN)):
            with self.subTest(code=code):
                result = self.call(name, **args)
                self.assertEqual(result.code, code)
                self.assertTrue(result.is_error and result.warn)
        self.assertEqual(self.browser.seals, [])            # nothing refused sealed the window or typed anything
        self.assertEqual(self.browser.typed_sealed, [])

    def test_a_payment_page_is_never_filled_or_sent(self) -> None:
        self.browser.pages[FORM_URL].final = "https://www.example.org/checkout"
        self.ready()
        self.assertEqual(self.call("type_text", ref="e1", text=NAME).code, drive.D_PAYMENT)
        self.assertEqual(self.call("press", ref="e5").code, drive.D_PAYMENT)
        self.assertEqual(self.plans, [])

    def test_the_wrong_tool_for_a_control(self) -> None:
        self.ready()
        self.assertEqual(self.call("type_text", ref="e5", text="x").code, drive.D_WRONG_KIND)
        self.assertEqual(self.call("choose", ref="e1", option="x").code, drive.D_WRONG_KIND)
        self.assertEqual(self.call("click", ref="e1").code, drive.D_WRONG_KIND)
        self.assertEqual(self.call("press", ref="e6").code, drive.D_WRONG_KIND)


class FreeOrCardTests(DriveCase):
    def test_an_ordinary_button_is_clicked_in_place(self) -> None:
        self.ready()
        result = self.call("click", ref="e7")
        self.assertFalse(result.is_error, result.text)
        self.assertIn("stayed where it was", result.text)
        self.assertEqual(self.browser.clicks, [(1, 7)])
        self.assertEqual(self.plans, [])

    def test_a_link_opens_as_open_page_would_in_a_new_tab_when_it_asks_for_a_window(self) -> None:
        self.ready()
        result = self.call("click", ref="e6")
        self.assertFalse(result.is_error, result.text)
        self.assertIn("How to apply.", result.text)
        self.assertEqual(self.browser.loads[-1], HELP_URL)
        self.assertEqual(self.browser.new_tabs, [2])
        self.assertIn("tab 2 of 2", result.text)
        self.assertEqual(self.browser.clicks, [])            # Jarvis opened its address; the page clicked nothing

    def test_a_submit_a_commit_button_and_any_button_once_typed_bring_the_card(self) -> None:
        self.ready()
        result = self.call("click", ref="e5")
        self.assertEqual(result.code, "waiting")
        self.assertEqual(result.text, tools.SEND_WAIT_TEXT)
        self.session.withdraw(self.plans.pop())
        self.page().controls[6].name = "Subscribe"
        self.listed()
        self.assertEqual(self.call("click", ref="e7").code, "waiting")
        self.session.withdraw(self.plans.pop())
        self.page().controls[6].name = "Show more"
        self.listed()
        self.call("type_text", ref="e1", text=NAME)
        self.assertEqual(self.call("click", ref="e7").code, "waiting")
        self.assertEqual(self.browser.clicks, [])
        self.assertEqual(self.browser.presses, [])
        self.assertEqual(self.browser.unapproved, [])

    def test_press_always_brings_the_card_and_a_second_waits_for_the_first(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.assertEqual(self.call("press", ref="e1").code, "waiting")     # Enter in a field
        self.assertEqual(len(self.plans), 1)
        again = self.call("press", ref="e5")
        self.assertEqual(again.code, drive.D_CARD_UP)
        self.assertEqual(self.call("type_text", ref="e2", text=EMAIL).code, drive.D_CARD_UP)

    def test_with_another_wait_up_no_card_and_nothing_waits(self) -> None:
        self.ready()
        self.accept = False
        result = self.call("press", ref="e5")
        self.assertEqual(result.code, tools.REFUSED)
        self.assertEqual(self.call("press", ref="e5").code, tools.REFUSED)     # not "a card waits"


class CardTests(DriveCase):
    def plan(self) -> drive.SendPlan:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.call("type_text", ref="e2", text=EMAIL)
        self.call("choose", ref="e3", option="Kenya")
        self.assertEqual(self.call("press", ref="e5").code, "waiting")
        return self.plans[-1]

    def test_every_value_exactly_with_its_tag_and_where_it_goes(self) -> None:
        plan = self.plan()
        self.assertEqual(plan.kicker(), "SEND ON www.example.org?")
        lines = plan.card_lines()
        self.assertEqual(lines[0].text, "Jarvis wants to press 'Apply now' on www.example.org")
        values = [(line.text, line.value, line.tag, line.amber) for line in lines if line.kind == drive.L_VALUE]
        self.assertEqual(values, [("Full name", NAME, drive.TAINT_TAG, True), ("Email", EMAIL, drive.TAINT_TAG, True),
                                  ("Country", "Kenya", "", False)])
        dest = next(line for line in lines if line.kind == drive.L_DEST)
        self.assertEqual((dest.text, dest.amber), ("Goes to: www.example.org", False))
        self.assertEqual(lines[-1].text, "Only a click sends. The site may pass on what it gets.")
        self.assertFalse(any(line.kind == drive.L_PAGE_SET for line in lines))

    def test_a_form_posting_to_another_host_is_amber(self) -> None:
        self.page().forms = [("post", "https://collect.example.net/in")]
        dest = next(line for line in self.plan().card_lines() if line.kind == drive.L_DEST)
        self.assertEqual((dest.text, dest.amber), ("Goes to: collect.example.net", True))

    def test_a_ticked_terms_box_is_amber_on_the_card(self) -> None:
        self.page().controls[3].name = "I agree to the terms"
        self.ready()
        self.call("choose", ref="e4", checked=True)
        self.call("press", ref="e5")
        row = next(line for line in self.plans[-1].card_lines() if line.kind == drive.L_VALUE)
        self.assertEqual((row.text, row.value, row.tag, row.amber), ("I agree to the terms", "on", drive.CONSENT_TAG,
                                                                    True))

    def test_with_no_form_the_card_says_it_can_not_see_where(self) -> None:
        self.page().controls[6].form = 0
        self.ready()
        self.call("type_text", ref="e1", text="Ada")
        self.call("click", ref="e7")
        dest = next(line for line in self.plans[-1].card_lines() if line.kind == drive.L_DEST)
        self.assertEqual((dest.text, dest.amber), (drive.NO_FORM_DEST, True))

    def test_what_no_one_can_see_is_spelled_out_on_the_card(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text="Ada‮Okafor")
        self.call("press", ref="e5")
        value = next(line for line in self.plans[-1].card_lines() if line.kind == drive.L_VALUE)
        self.assertEqual(value.value, "Ada<U+202E>Okafor")

    def test_the_owner_s_send_presses_the_same_element_and_reports_the_page(self) -> None:
        plan = self.plan()
        outcome = self.decided(plan, True)
        self.assertEqual(outcome.code, drive.S_SENT)
        self.assertEqual(self.browser.presses, [(1, 5, {"name": NAME, "email": EMAIL, "country": "Kenya", "news": "",
                                                        "pw": ""})])
        self.assertFalse(self.browser.sealed)
        self.assertEqual(self.session.holding(), 0)
        self.assertEqual((outcome.title, outcome.address), ("Thanks", DONE_URL))
        message = tools.submit_message(outcome)
        self.assertTrue(message.startswith("Jarvis: the owner clicked Send; it went. The page now shows:"))
        self.assertIn("We have your application.", message)
        self.assertIn("<untrusted ", message)
        self.assertEqual(self.browser.unapproved, [])
        self.assertFalse(self.call("open_page", url=OTHER_URL).is_error)     # the seal is gone with what was sent

    def test_an_edit_after_the_card_means_no_press(self) -> None:
        plan = self.plan()
        self.page().controls[1].value = "someone.else@example.net"   # the page's own script changed it meanwhile
        outcome = self.decided(plan, True)
        self.assertEqual(outcome.code, drive.S_CHANGED)
        self.assertEqual(self.browser.presses, [])
        self.assertTrue(self.browser.sealed)                         # still sealed: nothing went
        self.assertIn("nothing was sent", tools.submit_message(outcome))

    def test_don_t_closes_the_page_while_sealed_and_the_same_send_is_not_tried_again(self) -> None:
        plan = self.plan()
        self.decided(plan, False)
        self.assertEqual(self.browser.closed_tabs, [(1, True, True)])   # destroyed while sealed, then unsealed
        self.assertEqual(self.browser.presses, [])
        self.assertFalse(self.browser.sealed)
        self.assertEqual(self.session.holding(), 0)
        self.assertEqual(self.session.tab_count(), 1)                   # a fresh empty tab took its place
        self.browser.pages[FORM_URL] = form_page()
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.call("type_text", ref="e2", text=EMAIL)
        self.call("choose", ref="e3", option="Kenya")
        result = self.call("press", ref="e5")
        self.assertEqual(result.code, drive.D_DECLINED)
        self.assertEqual(len(self.plans), 1)

    def test_a_page_script_copying_values_into_hidden_fields(self) -> None:
        def copy(page: FakePage, control: FakeControl) -> None:
            page.extra = [[1, "track", control.value, True]]

        self.page().on_type = copy
        self.page().extra = [[1, "token", "abc123", True]]
        plan = self.plan()
        page_set = next(line for line in plan.card_lines() if line.kind == drive.L_PAGE_SET)
        self.assertEqual(page_set.text, "Plus 1 value the page set itself (1 a copy of what Jarvis typed)")
        self.assertTrue(page_set.amber)
        self.page().extra = [[1, "track", "changed after the card", True]]
        self.assertEqual(self.decided(plan, True).code, drive.S_CHANGED)
        self.assertEqual(self.browser.presses, [])


class FixRoundTests(DriveCase):
    """The review of 2026-11-23: each finding's fix (H1 ... L5); every one of these fails without it."""

    def card(self) -> drive.SendPlan:
        self.assertEqual(self.call("press", ref="e5").code, "waiting")
        return self.plans[-1]

    def values(self, plan: drive.SendPlan, kind: str = drive.L_VALUE) -> list[tuple[str, str]]:
        return [(line.text, line.value) for line in plan.card_lines() if line.kind == kind]

    # H1: what Jarvis set is his by its own key, whatever a new list numbers it
    def test_h1_a_new_list_that_renumbers_the_fields_keeps_the_card_true(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.page().controls.insert(0, FakeControl("textbox", "Nickname", form=1, field="nick"))   # the page grew
        self.listed()                                                    # e1 is Nickname now, e2 Full name
        self.assertEqual(self.call("press", ref="e6").code, "waiting")
        plan = self.plans[-1]
        self.assertEqual(self.values(plan), [("Full name", NAME)])
        self.assertEqual(plan.copies, 0)
        self.assertEqual(self.values(plan, drive.L_PAGE_VALUE), [("country", "Choose")])   # the list's own choice

    def test_h1_a_value_whose_answer_never_came_is_on_the_card(self) -> None:
        act = self.browser.act

        def lost(tab: int, source: str, **options: Any) -> drive.ActResult:
            done = act(tab, source, **options)
            return drive.ActResult(None) if "/*drive:type*/" in source else done   # typed, but no answer came

        self.ready()
        self.browser.act = lost  # type: ignore[method-assign]
        self.assertTrue(self.call("type_text", ref="e1", text=NAME).is_error)
        self.browser.act = act  # type: ignore[method-assign]
        self.assertEqual(self.values(self.card()), [("Full name", NAME)])

    def test_h1_a_field_jarvis_filled_that_went_from_the_page_refuses_the_card(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        del self.page().controls[0]
        self.listed()
        self.assertEqual(self.call("press", ref="e4").code, drive.D_MISSING)
        self.assertEqual(self.plans, [])

    # H2: a field takes one value; what the page set is shown whole and matched against everything typed
    def test_h2_typing_again_or_clearing_is_refused(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        for text in ("Someone Else", ""):
            result = self.call("type_text", ref="e1", text=text)
            self.assertEqual(result.code, drive.D_TYPED_ONCE)
            self.assertIn("close_tab 1", result.text)
        self.assertEqual(self.page().controls[0].value, NAME)
        self.assertEqual(self.values(self.card()), [("Full name", NAME)])

    def test_h2_values_the_page_set_are_shown_whole_and_copies_found(self) -> None:
        def copy(page: FakePage, control: FakeControl) -> None:
            page.extra = [[1, "track", f"ref:{control.value}", True], [1, "token", "abc123", True]]

        self.page().on_type = copy
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        plan = self.card()
        lines = [(line.text, line.value, line.tag, line.amber) for line in plan.card_lines()
                 if line.kind == drive.L_PAGE_VALUE]
        self.assertEqual(lines, [("country", "Choose", "", False), ("track", f"ref:{NAME}", drive.COPY_TAG, True),
                                 ("token", "abc123", drive.HIDDEN_TAG, False)])
        self.assertEqual(plan.copies + plan.page_tainted, 1)

    def test_h2_more_page_set_values_than_a_card_shows_is_refused(self) -> None:
        self.page().extra = [[1, f"f{n}", f"v{n}", True] for n in range(drive.PAGE_SET_SHOWN + 1)]
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.assertEqual(self.call("press", ref="e5").code, drive.D_TOO_MANY)

    # H3: where a submit really sends it
    def test_h3_a_submit_s_own_action_wins_and_another_window_is_refused(self) -> None:
        self.page().controls[4].formaction = "https://collect.example.net/in"
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        plan = self.card()
        dest = next(line for line in plan.card_lines() if line.kind == drive.L_DEST)
        self.assertEqual((dest.text, dest.amber), ("Goes to: collect.example.net", True))
        self.assertEqual(plan.hosts, ("www.example.org", "collect.example.net"))
        self.decided(plan, False)
        for target, form_target in (("_blank", ""), ("", "_blank")):
            self.browser.pages[FORM_URL] = page = form_page()
            page.controls[4].formtarget = target
            page.forms = [("post", "https://www.example.org/apply/send", form_target)]
            self.ready()
            self.call("type_text", ref="e2", text=EMAIL)
            self.assertEqual(self.call("press", ref="e5").code, drive.D_OTHER_WINDOW)
            self.call("close_tab", tab=1)

    # M1: a press that only clicks names no form
    def test_m1_a_press_that_only_clicks_sends_no_form(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.assertEqual(self.call("press", ref="e4").code, "waiting")     # a box in the form: a click
        plan = self.plans[-1]
        self.assertEqual(plan.goes_to, "")
        dest = next(line for line in plan.card_lines() if line.kind == drive.L_DEST)
        self.assertEqual(dest.text, drive.NO_FORM_DEST)

    # M3: no value goes that the card could not show whole
    def test_m3_a_value_over_the_cap_refuses_the_card(self) -> None:
        self.page().extra = [[1, "blob", "x" * (drive.TYPE_CAP + 500), True]]
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.assertEqual(self.call("press", ref="e5").code, drive.D_TOO_LONG)

    # M4: the page as it is now, not as it loaded
    def test_m4_the_live_title_and_host_are_judged(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.page().title = "Payment details"
        self.assertEqual(self.call("press", ref="e5").code, drive.D_PAYMENT)
        self.page().title = "Apply"
        self.page().final = "https://elsewhere.example.net/apply"       # it went to another site by itself
        self.listed()
        self.assertEqual(self.call("type_text", ref="e2", text=EMAIL).code, drive.D_MOVED)
        self.assertEqual(self.call("press", ref="e5").code, drive.D_MOVED)
        self.assertEqual(self.plans, [])

    # M5: nothing else of the run reaches the web while a tab holds values
    def test_m5_a_read_another_way_is_refused_while_a_tab_holds_values(self) -> None:
        fetched: list[str] = []
        context = replace(self.context, fetch=lambda address, question, call: fetched.append(address) or "text")
        self.assertIsNone(tools.sealed_now(context))
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        result = tools._fetch_text({"url": OTHER_URL, "question": "What is it?"}, context)
        self.assertEqual((result.is_error, result.code), (True, drive.D_SEALED))
        self.assertEqual(fetched, [])
        self.assertEqual(tools.sealed_now(context).code, drive.D_SEALED)

    # M6: nothing acts on a tab whose card waits; the press's own answer decides
    def test_m6_no_call_acts_on_a_tab_whose_card_waits(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.card()
        for name, args in (("list_controls", {}), ("scroll", {"to": "down"}), ("close_tab", {"tab": 1}),
                           ("type_text", {"ref": "e2", "text": EMAIL})):
            self.assertEqual(self.call(name, **args).code, drive.D_CARD_UP, name)

    def test_m6_a_press_that_finds_the_page_changed_closes_it_and_lifts_the_seal(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        plan = self.card()
        self.browser.before_press = lambda page: setattr(page, "gen", "another")   # a new document at the very last
        self.assertEqual(self.decided(plan, True).code, drive.S_GONE)
        self.assertEqual(self.browser.presses, [])
        self.assertEqual(self.browser.closed_tabs, [(1, True, True)])
        self.assertFalse(self.browser.sealed)

    def test_m2_the_press_checks_the_values_in_its_own_task_and_reaches_the_card_s_hosts_alone(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        plan = self.card()
        self.browser.before_press = lambda page: setattr(page.controls[0], "value", "Someone Else")
        self.assertEqual(self.decided(plan, True).code, drive.S_CHANGED)
        self.assertEqual(self.browser.presses, [])
        self.assertTrue(self.browser.sealed)
        self.assertEqual(self.session.holding(), 1)
        self.assertEqual(self.browser.press_hosts, [("www.example.org",)])

    # L1: the link opened is the link judged
    def test_l1_a_link_that_changed_since_the_firewall_judged_it_is_not_opened(self) -> None:
        self.ready()
        self.assertEqual(self.session.link_address("e6"), HELP_URL)
        self.page().controls[5].href = OTHER_URL
        self.listed()
        self.assertEqual(self.call("click", ref="e6").code, drive.D_STALE)
        self.assertNotIn(OTHER_URL, self.browser.loads)
        self.assertEqual(self.session.link_address("e6"), OTHER_URL)
        self.assertFalse(self.call("click", ref="e6").is_error)

    # L2, L5: a page that did not go stays held, and a declined one is tried again
    def test_l5_a_tab_that_did_not_close_stays_held_and_sealed(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.browser.close_ok = False
        self.assertEqual(self.call("close_tab", tab=1).code, drive.D_NOT_CLOSED)
        self.assertEqual(self.session.holding(), 1)
        self.assertTrue(self.browser.sealed)
        self.assertEqual(self.call("open_page", url=OTHER_URL).code, drive.D_SEALED)
        self.browser.close_ok = True
        self.assertFalse(self.call("close_tab", tab=1).is_error)
        self.assertEqual(self.session.holding(), 0)
        self.assertFalse(self.browser.sealed)

    def test_l2_a_declined_page_that_did_not_go_is_closed_again(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        plan = self.card()
        self.browser.close_ok = False
        threading.Timer(0.5, lambda: setattr(self.browser, "close_ok", True)).start()
        plan.decide(False, None, lambda: False)
        deadline = time.monotonic() + 5
        while self.session.holding() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.session.holding(), 0)
        self.assertGreater(len(self.browser.closed_tabs), 1)
        self.assertFalse(self.browser.sealed)
        self.assertNotIn("was closed", ar.SUBMIT_DECLINED_TEXT + ar.SUBMIT_UNANSWERED_TEXT + ar.SUBMIT_NO_CLICK_TEXT)

    # L3: what happened when no new page came
    def test_l3_a_send_the_page_did_not_take_is_not_reported_sent(self) -> None:
        for press, code, words in (("invalid", drive.S_NOT_SENT, "the page did not send it"),
                                   ("prevent", drive.S_STAYED, "no new page came")):
            self.browser.pages[FORM_URL] = page = form_page()
            page.controls[4].press = press
            self.ready()
            self.call("type_text", ref="e1", text=NAME)
            outcome = self.decided(self.card(), True)
            self.assertEqual(outcome.code, code, press)
            self.assertEqual(outcome.number, 1)
            self.assertIn(words, tools.submit_message(outcome))
            self.assertTrue(self.browser.sealed)                          # sealed again: the page still holds it
            self.assertEqual(self.session.holding(), 1)
            self.assertEqual(self.call("open_page", url=OTHER_URL).code, drive.D_SEALED)
            self.assertFalse(self.call("close_tab", tab=1).is_error)

    # L4: a list from where the page stands
    def test_l4_controls_above_where_the_page_stands_are_counted_and_the_advice_holds(self) -> None:
        found = drive.read_controls('{"gen": "g", "controls": [], "above": 3, "more": true}', "g")
        self.assertEqual((found.above, found.more), (3, True))
        self.assertIn("getBoundingClientRect.call(el).bottom < 0) {{ above += 1".replace("{{", "{"),
                      drive.controls_script("g"))
        self.ready()
        listing = drive.read_controls
        drive.read_controls = lambda raw, gen: replace(listing(raw, gen), above=2, more=True)  # type: ignore[assignment]
        try:
            text = self.call("list_controls").text
        finally:
            drive.read_controls = listing  # type: ignore[assignment]
        self.assertIn("2 control(s) above where the page stands were left out: scroll up", text)
        self.assertIn(f"Only the first {drive.MAX_CONTROLS} from where the page stands are listed", text)

    def test_a_ref_is_the_element_s_place_even_when_a_row_is_no_control(self) -> None:
        found = drive.read_controls('{"gen": "g", "controls": [{"role": "nope"}, {"role": "button", "name": "Go"}]}',
                                    "g")
        self.assertIsNone(found.get(1))
        self.assertEqual(found.get(2).name, "Go")


class PromptFileTests(unittest.TestCase):
    """L6: FILLING IN PAGES reaches only a task offered the drive tools; any other gets the prompt as it always was."""

    def test_the_drive_prompt_only_with_the_drive_tools(self) -> None:
        from briefing_reader.ask import cli
        from tests.agent_fakes import agent_engine
        from tests.ask_fakes import FakeRunner

        plain = cli.AGENT_PROMPT_FILE.read_text(encoding="ascii")
        driving = cli.AGENT_DRIVE_PROMPT_FILE.read_text(encoding="ascii")
        self.assertNotIn("FILLING IN PAGES", plain)
        self.assertIn("You cannot sign in to anything, type into pages, post forms", plain)
        self.assertIn("FILLING IN PAGES", driving)
        self.assertNotIn(b"\r", cli.AGENT_DRIVE_PROMPT_FILE.read_bytes())
        with tempfile.TemporaryDirectory() as root:
            engine = agent_engine(Path(root), FakeRunner())
            ready = SimpleNamespace(exe=SimpleNamespace(path=Path(root) / "claude.exe"))
            mcp = Path(root) / "mcp.json"
            for registry, wanted in ((Registry(policy.offered(ON, drive_consent=True)), cli.AGENT_DRIVE_PROMPT_FILE),
                                     (Registry(policy.offered(ON, drive_consent=False)), cli.AGENT_PROMPT_FILE),
                                     (Registry(policy.offered(OFF, drive_consent=True)), cli.AGENT_PROMPT_FILE)):
                argv = engine.argv(ready, registry.allowed(), mcp)
                self.assertEqual(argv[argv.index("--system-prompt-file") + 1], str(wanted))


class TabTests(DriveCase):
    def test_three_tabs_at_most(self) -> None:
        self.opened()
        for _ in range(2):
            self.assertFalse(self.call("open_page", url=OTHER_URL, tab="new").is_error)
        self.assertEqual(self.session.tab_count(), 3)
        self.assertEqual(self.call("open_page", url=OTHER_URL, tab="new").code, drive.D_TABS)
        self.assertEqual(self.browser.new_tabs, [2, 3])
        result = self.call("switch_tab", tab=1)
        self.assertIn("Tab 1 of 3", result.text)
        self.assertEqual(self.browser.active, 1)
        self.assertEqual(self.call("switch_tab", tab=4).code, drive.D_NO_TAB)

    def test_only_one_tab_holds_values_and_closing_it_unseals_after_it_is_gone(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.browser.pages[OTHER_URL] = form_page(final=OTHER_URL)
        self.session.switch_tab(1, web.Call(lambda: False, 45.0))
        self.assertEqual(self.session.holding(), 1)
        self.call("close_tab", tab=1)
        self.assertEqual(self.browser.closed_tabs, [(1, True, True)])
        self.assertFalse(self.browser.sealed)
        self.ready()
        self.assertFalse(self.call("type_text", ref="e1", text=NAME).is_error)

    def test_typing_into_a_second_tab_while_one_holds_values_is_refused(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME)
        self.session._tabs.append(drive._Tab(9))              # a second tab, opened before the typing
        self.browser.tabs[9] = form_page(final=OTHER_URL)
        self.call("switch_tab", tab=2)
        self.listed()
        result = self.call("type_text", ref="e1", text=NAME)
        self.assertEqual(result.code, drive.D_OTHER_TAB)
        self.assertEqual(self.call("press", ref="e5").code, drive.D_OTHER_TAB)


class CapsTests(unittest.TestCase):
    def test_each_cap_and_the_owner_s_yes(self) -> None:
        caps = drive.DriveCaps(max_pages=2, max_actions=3, max_sends=1)
        self.assertEqual(caps.reached(), "")
        caps.page()
        caps.page()
        self.assertEqual(caps.reached(), drive.CAP_PAGES)
        self.assertEqual(caps.words(drive.CAP_PAGES), "2 pages opened")
        caps.extend()
        self.assertEqual(caps.reached(), "")
        caps.send()
        self.assertEqual(caps.reached(), drive.CAP_SENDS)
        self.assertEqual(drive.keep_going_words(drive.CAP_SENDS, caps), "1 Send cards")

    def test_the_policy_reads_them_and_defaults_what_is_odd(self) -> None:
        caps = policy.drive_caps(SimpleNamespace(agent=SimpleNamespace(max_pages=7, max_actions="lots", max_sends=0)))
        caps._count[drive.CAP_PAGES] = 7
        self.assertEqual(caps.reached(), drive.CAP_PAGES)
        self.assertEqual(caps._step[drive.CAP_ACTIONS], drive.DEFAULT_MAX_ACTIONS)
        self.assertEqual(caps._step[drive.CAP_SENDS], drive.DEFAULT_MAX_SENDS)


class LineTests(DriveCase):
    def test_a_line_is_said_once_its_gates_pass(self) -> None:
        self.opened()
        result = self.call("list_controls", line="Looking at the form now.")
        self.assertEqual(self.said, ["Looking at the form now."])
        self.assertIn(("Said", "Looking at the form now."), result.fields)

    def test_never_a_typed_value_a_secret_a_page_s_words_or_too_soon(self) -> None:
        self.ready()
        self.call("type_text", ref="e1", text=NAME, line=f"Putting in {NAME} now.")
        self.call("type_text", ref="e2", text=EMAIL, line="Adding the address.")
        self.call("list_controls", line=f"I have typed {NAME.upper()} in.")
        self.call("list_controls", line="What is your password?")
        self.context.read_words.feed("The quick brown fox jumps over the lazy dog in the meadow today")
        self.call("list_controls", line="quick brown fox jumps over the lazy dog")
        self.assertEqual(self.said, ["Adding the address."])
        self.call("list_controls", line="One thing.")
        result = self.registry.call("list_controls", {"line": "One more thing."}, self.context)   # the same moment
        self.assertIn(("Not said", "too soon after the last line"), result.fields)
        self.assertEqual(self.said, ["Adding the address.", "One thing."])
        self.assertNotIn(NAME, " ".join(self.shown))

    def test_a_long_line_is_cut_not_refused(self) -> None:
        self.opened()
        result = self.call("list_controls", line="word " * 60)
        self.assertFalse(result.is_error, result.text)
        self.assertLessEqual(len(self.said[0]), drive.LINE_CAP)


class DoingTests(DriveCase):
    def test_the_field_s_name_and_host(self) -> None:
        self.ready()
        self.assertEqual(tools.doing_words("type_text", {"ref": "e2", "text": EMAIL}, self.context),
                         "Typing into Email on www.example.org")
        self.assertEqual(tools.doing_words("press", {"ref": "e5"}, self.context), "Pressing Apply now on www.example.org")
        self.assertEqual(tools.doing_words("list_controls", {}, self.context), "Looking over www.example.org")
        self.assertEqual(tools.doing_words("click", {"ref": "e99"}, self.context), "Clicking on the page")
        self.assertEqual(tools.doing_words("close_tab", {"tab": 1}, self.context), "Closing a tab")


class OfferedTests(unittest.TestCase):
    def names(self, tools_: tuple[tools.Tool, ...]) -> list[str]:
        return [tool.name for tool in tools_]

    def test_only_with_the_consent_and_the_setting(self) -> None:
        before = self.names(policy.offered())
        for config, consent in ((ON, False), (OFF, True), (None, True), (SimpleNamespace(agent=SimpleNamespace(
                drive="yes", private=AGENT_PRIVATE)), True)):
            with self.subTest(config=config, consent=consent):
                offered = policy.offered(config, drive_consent=consent)
                self.assertEqual(self.names(offered), before)
                self.assertNotIn("tab", next(t for t in offered if t.name == "open_page").schema["properties"])
        on = policy.offered(ON, drive_consent=True)
        self.assertEqual(self.names(on), [*before, *(tool.name for tool in tools.DRIVE_TOOLS)])
        opened = next(t for t in on if t.name == "open_page")
        self.assertEqual(set(opened.schema["properties"]), {"url", "number", "tab", "line"})
        self.assertTrue(all(tool.tier == tools.TIER_DRIVE for tool in tools.DRIVE_TOOLS))
        self.assertIn(tools.TIER_DRIVE, policy.GUARDED_TIERS)

    def test_no_firewall_no_drive_call(self) -> None:
        tool = next(t for t in tools.DRIVE_TOOLS if t.name == "type_text")
        self.assertEqual(policy.gate(tool, {}, None).code, policy.NO_FIREWALL)


class FirewallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = drive.DriveSession(FakeDriveBrowser(pages()))
        self.firewall = WebFirewall(self.session.book, link_of=self.session.link_address)
        self.asked: list[str] = []
        self.firewall.attach(lambda text, choices, on_answer: self.asked.append(text) or True)
        self.click = next(t for t in tools.DRIVE_TOOLS if t.name == "click")
        call = web.Call(lambda: False, 45.0)
        self.session.open_page(FORM_URL, call)
        self.session.list_controls(call)

    def test_a_link_is_judged_as_open_page_and_a_button_is_not_its_to_judge(self) -> None:
        self.assertIsNone(self.firewall.check(self.click, {"ref": "e6"}))     # a clean run: free
        self.firewall.note_private()
        held = self.firewall.check(self.click, {"ref": "e6"})
        self.assertEqual(held.code, "waiting")
        self.assertEqual(self.asked, [f"Jarvis wants to open {HELP_URL}"])
        self.assertIsNone(self.firewall.check(self.click, {"ref": "e7"}))     # a button: its Send card judges it

    def test_a_link_seen_in_the_run_goes(self) -> None:
        self.firewall.note_private()
        self.session.book.see(HELP_URL)
        self.assertIsNone(self.firewall.check(self.click, {"ref": "e6"}))


# --------------------------------------------------------------------------
# The run's Send card
# --------------------------------------------------------------------------

class SubmitRunTests(RunCase):
    def drive_run(self, script: Any, timing: ar.Timing = TIMING, caps: drive.DriveCaps | None = None) -> None:
        self.browser = FakeDriveBrowser(pages())
        self.session = drive.DriveSession(self.browser, caps=caps or drive.DriveCaps())
        self.firewall = WebFirewall(self.session.book, link_of=self.session.link_address)
        self.go(script, goal=f"apply for me at {FORM_URL} as {NAME}", extra_tools=tools.DRIVE_TOOLS, timing=timing,
                web=self.session, firewall=self.firewall)

    def filled(self, engine: FakeEngine, said: dict[str, Any]) -> None:
        engine.wait_message()
        engine.call("open_page", url=FORM_URL)
        engine.call("list_controls")
        engine.call("type_text", ref="e1", text=NAME)
        said["press"] = engine.call("press", ref="e5")
        engine.end_turn("Waiting for the owner.")
        said["next"] = engine.wait_message()
        engine.call("finish", summary="Done.", outcome="done")
        engine.end_turn("Done.")

    def test_send_presses_and_the_next_message_says_what_the_page_shows(self) -> None:
        said: dict[str, Any] = {}
        self.drive_run(lambda engine: self.filled(engine, said))
        question = self.question()
        self.assertEqual(question.kind, ar.Q_SUBMIT)
        self.assertEqual(question.choices, ("Send", "Don't"))
        self.assertEqual(question.kicker, "SEND ON www.example.org?")
        self.assertEqual([line.value for line in question.lines if line.kind == drive.L_VALUE], [NAME])
        self.assertTrue(self.run_.answer(question.id, "Send"))
        end = self.end()
        self.assertEqual(end.outcome, "done")
        self.assertEqual(self.engine.text_of(said["press"]), tools.SEND_WAIT_TEXT)
        self.assertTrue(said["next"].startswith(tools.SENT_TEXT))
        self.assertIn("We have your application.", said["next"])
        self.assertEqual(len(self.browser.presses), 1)
        gone = [event.text for event in self.of(ar.E_QUESTION_GONE)]
        self.assertEqual(gone[0], ar.HOW_SENT)

    def test_don_t_no_press_and_the_page_goes(self) -> None:
        said: dict[str, Any] = {}
        self.drive_run(lambda engine: self.filled(engine, said))
        question = self.question()
        self.assertTrue(self.run_.answer(question.id, "Don't"))
        self.end()
        self.assertEqual(said["next"], ar.SUBMIT_DECLINED_TEXT)
        self.assertEqual(self.browser.presses, [])
        self.until(lambda: self.browser.closed_tabs)
        self.assertEqual(self.browser.closed_tabs[0][:2], (1, True))

    def test_no_answer_no_press(self) -> None:
        said: dict[str, Any] = {}
        self.drive_run(lambda engine: self.filled(engine, said), timing=replace(TIMING, submit_card_s=0.3))
        self.end()
        self.assertEqual(said["next"], ar.SUBMIT_UNANSWERED_TEXT)
        self.assertEqual(self.browser.presses, [])
        self.until(lambda: self.browser.closed_tabs)

    def test_typed_or_spoken_words_never_answer_it(self) -> None:
        said: dict[str, Any] = {"heard": []}

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("open_page", url=FORM_URL)
            engine.call("list_controls")
            engine.call("type_text", ref="e1", text=NAME)
            engine.call("press", ref="e5")
            engine.end_turn("Waiting for the owner.")
            while True:                                 # the owner's words come in as theirs: they answer nothing
                message = engine.wait_message()
                said["heard"].append(message)
                if message.startswith("Jarvis:"):
                    said["next"] = message
                    break
                engine.end_turn("Still waiting for the card.")
            engine.call("finish", summary="Not sent.", outcome="partial")
            engine.end_turn("Done.")

        self.drive_run(script, timing=replace(TIMING, submit_card_s=1.0))
        question = self.question()
        self.assertEqual(self.run_.click_awaited(), question)
        self.assertEqual(self.run_.owner_says("Send"), "sent")
        self.assertEqual(self.run_.owner_says("yes, send it"), "sent")
        self.end()
        self.assertEqual(said["next"], ar.SUBMIT_NO_CLICK_TEXT)
        self.assertEqual(said["heard"][0], ar.owner_message("Send"))
        self.assertEqual(self.browser.presses, [])

    def test_stop_withdraws_it_and_a_late_click_does_nothing(self) -> None:
        said: dict[str, Any] = {}
        self.drive_run(lambda engine: self.filled(engine, said))
        question = self.question()
        self.run_.stop()
        self.end()
        self.assertFalse(self.run_.answer(question.id, "Send"))
        self.until(lambda: self.browser.closed_tabs)
        time.sleep(0.05)
        self.assertEqual(self.browser.presses, [])

    def test_the_drive_caps_ask_keep_going(self) -> None:
        said: dict[str, Any] = {}

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("open_page", url=FORM_URL)
            engine.call("list_controls")
            said["held"] = engine.call("list_controls")
            engine.end_turn("Waiting.")
            said["next"] = engine.wait_message()
            said["again"] = engine.call("list_controls")
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn("Done.")

        self.drive_run(script, caps=drive.DriveCaps(max_actions=1))
        self.until(lambda: "held" in said)
        question = self.question()
        self.assertEqual(question.kind, ar.Q_KEEP_GOING)
        self.assertIn("That's 1 page actions on this task", question.text)
        self.run_.answer(question.id, "Keep going")
        self.end()
        self.assertEqual(self.engine.text_of(said["held"]), ar.PAUSED_TEXT)
        self.assertFalse(said["again"].get("isError"))

    def test_pause_holds_web_and_drive_calls_and_resume_says_so(self) -> None:
        said: dict[str, Any] = {}
        paused = threading.Event()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("open_page", url=FORM_URL)
            paused.wait(5)
            said["drive"] = engine.call("list_controls")
            said["web"] = engine.call("open_page", url=OTHER_URL)
            said["plan"] = engine.call("update_plan", found="", steps=[{"text": "Wait", "status": "doing"}])
            engine.end_turn("Paused.")
            said["next"] = engine.wait_message()
            said["after"] = engine.call("list_controls")
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn("Done.")

        self.drive_run(script)
        self.until(lambda: self.browser.loads)
        self.assertTrue(self.run_.pause_drive())
        self.assertFalse(self.run_.pause_drive())
        paused.set()
        self.until(lambda: "plan" in said)
        self.assertTrue(self.run_.resume_drive())
        self.end()
        self.assertEqual(self.engine.text_of(said["drive"]), ar.OWNER_PAUSED_TEXT)
        self.assertEqual(self.engine.text_of(said["web"]), ar.OWNER_PAUSED_TEXT)
        self.assertNotEqual(self.engine.text_of(said["plan"]), ar.OWNER_PAUSED_TEXT)
        self.assertEqual(said["next"], ar.OWNER_RESUMED_TEXT)
        self.assertIn("e1", self.engine.text_of(said["after"]))
        self.assertEqual([e.code for e in self.of(ar.E_NOTICE)], [ar.C_OWNER_PAUSED, ar.C_OWNER_RESUMED])
        self.assertEqual(self.browser.loads, [FORM_URL])

    def test_the_logs_hold_no_value_label_or_host(self) -> None:
        said: dict[str, Any] = {}
        with self.assertLogs("briefing_reader", level=logging.DEBUG) as logs:
            logging.getLogger("briefing_reader.agent").debug("a test line")
            self.drive_run(lambda engine: self.filled(engine, said))
            question = self.question()
            self.run_.answer(question.id, "Send")
            self.end()
        text = "\n".join(logs.output)
        for word in ("Okafor", "Ada", "example.org", "Full name", "Apply now", "Thanks", "apply"):
            self.assertNotIn(word, text)
        self.assertIn("submit", text)


if __name__ == "__main__":
    unittest.main()
