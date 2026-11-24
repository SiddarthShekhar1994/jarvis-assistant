"""Jarvis filling in pages, part 3, in the app: the consent row (version 4) that turns the drive tools on, the Send card
on the HUD (amber, every value exactly, its tags, Don't and Send, click only, counted on Approvals), Pause and Resume
on the panel, the result that opens big, and the watch window's "Open in my browser".

Qt runs offscreen and shows nothing. The task's browser is tests.agent_fakes.FakeDriveBrowser (no web engine, no
network); the real Claude Code is never started (FakeEngine). "Open in my browser" is always a fake here: the real one
is never called (and under the test guard it opens nothing anyway). Every value is invented.
"""

from __future__ import annotations

import logging
import threading
import unittest
from dataclasses import replace
from typing import Any
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from briefing_reader import agent_feed, desktop_ui, desktop_view, hud_overlay, snapshot_engine
from briefing_reader.agent import browser as agent_browser
from briefing_reader.agent import drive, policy, tools, web
from briefing_reader.agent import runner as ar
from briefing_reader.agent.browser import WatchWindow
from briefing_reader.config import AGENT_RANGES, AgentConfig, load_config
from tests.agent_fakes import FAST, FakeControl, FakeDriveBrowser, FakeEngine, FakePage
from tests.ask_fakes import plan_stream
from tests.test_agent_app import TASK_PLAN, TYPED
from tests.test_agent_drive import DONE_URL, EMAIL, FORM_URL, NAME, form_page, pages
from tests.test_agent_feed import FeedApp, Stages
from tests.ui_fakes import fonts, settle, wait_for

TIMING = replace(FAST, tool_s=30.0)
ELSEWHERE = "https://forms.example.net/collect"
_app: QApplication | None = None


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_agent_drive_app", "-platform", "offscreen"])
    fonts()


def drive_tool_names() -> set[str]:
    return {tool.name for tool in tools.DRIVE_TOOLS}


class DriveApp(FeedApp):
    """A task in the app whose browser is a FakeDriveBrowser; the options the controller makes it with are kept."""

    def drive_agent(self, script: Any, *, version: int = desktop_ui.DRIVE_CONSENT_VERSION, agent: str = "",
                    site: dict[str, FakePage] | None = None, timing: Any = TIMING) -> FakeEngine:
        engine = FakeEngine(script)
        self.drive_browser = FakeDriveBrowser(site if site is not None else pages())
        self.make_agent(plan_stream(TASK_PLAN), engine, browser=self.drive_browser, desktop_consent=version,
                        agent=agent, timing=timing)
        self.options: list[dict[str, Any]] = []

        def make(_parent: Any, **options: Any) -> FakeDriveBrowser:
            self.options.append(options)
            return self.drive_browser

        self.c.agent._browser_factory = make
        return engine

    def go(self) -> None:
        self.type_it(TYPED)
        self.started_task()
        settle()


# --------------------------------------------------------------------------
# Consent version 4 and the config keys
# --------------------------------------------------------------------------

class DriveConsentTests(DriveApp):
    ROW = desktop_view.consent_task_row("DuckDuckGo", "Bing and Google", local=True)

    @staticmethod
    def held(stages: Stages) -> Any:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            stages.reach(1)
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn("Done.")

        return script

    def offered_now(self, stages: Stages) -> tuple[set[str], Any]:
        self.reached(stages, 1)
        run = self.c.agent.run()
        names = set(run.registry.names())
        session = run._context.web
        stages.go(1)
        self.ended()
        return names, session

    def test_v3_no_drive_tools_and_the_init_tool_set_is_unchanged(self) -> None:
        stages = Stages()
        self.drive_agent(self.held(stages), version=3)
        self.go()
        names, session = self.offered_now(stages)
        before = {tool.name for tool in policy.offered(self.c.config, connected=self.c.agent._things.connected())}
        self.assertEqual(names, before)                               # exactly what a task was offered before
        self.assertFalse(names & drive_tool_names())
        self.assertNotIsInstance(session, drive.DriveSession)
        self.assertIsNone(self.c.agent.firewall.link_of)
        self.assertEqual(self.options, [{"avoid": self.c.agent._window_avoid,
                                         "hide_from_capture": True}])  # no drive=True
        self.assertEqual(self.drive_browser.bigs, [False])
        self.assertFalse(self.desktop.consent_for_drive())
        self.assertTrue(self.desktop.consent_for_tasks())

    def test_v4_the_drive_tools_their_session_firewall_and_window(self) -> None:
        stages = Stages()
        self.drive_agent(self.held(stages))
        self.go()
        names, session = self.offered_now(stages)
        self.assertTrue(drive_tool_names() <= names)
        self.assertIsInstance(session, drive.DriveSession)
        self.assertEqual(self.c.agent.firewall.link_of, session.link_address)
        self.assertEqual(self.options[0].get("drive"), True)
        self.assertEqual(session.caps.count(drive.CAP_PAGES), 0)

    def test_v4_with_drive_off_in_config_tasks_are_as_before(self) -> None:
        stages = Stages()
        self.drive_agent(self.held(stages), agent="drive = false\n")
        self.go()
        names, session = self.offered_now(stages)
        self.assertFalse(names & drive_tool_names())
        self.assertNotIsInstance(session, drive.DriveSession)
        self.assertNotIn("drive", self.options[0])

    def test_the_caps_come_from_config(self) -> None:
        stages = Stages()
        self.drive_agent(self.held(stages), agent="max_pages = 7\nmax_actions = 20\nmax_sends = 2\n")
        self.go()
        self.reached(stages, 1)
        caps = self.c.agent.run()._context.web.caps
        self.assertEqual(caps._step, {drive.CAP_PAGES: 7, drive.CAP_ACTIONS: 20, drive.CAP_SENDS: 2})
        stages.go(1)
        self.ended()

    def card(self) -> desktop_view.ConsentCard:
        self.assertIsNotNone(self.c._desktop_consent_card, "the card was made")
        return self.c._desktop_consent_card

    def test_a_v3_card_marks_only_the_new_row_and_turn_on_stores_v4(self) -> None:
        self.drive_agent(self.held(Stages()), version=3)
        self.c._show_desktop_consent(desktop_ui.ENTRY_REQUEST)
        settle()
        card = self.card()
        self.assertEqual(desktop_view.CONSENT_DRIVE_ROW, "He may fill in public pages in his own window; nothing typed "
                                                         "leaves before your click on its card.")
        self.assertIn(desktop_view.CONSENT_DRIVE_ROW, card.texts())
        self.assertEqual(card.new_rows(), [desktop_view.CONSENT_DRIVE_ROW])
        texts = card.texts()
        self.assertEqual(texts.index(desktop_view.CONSENT_DRIVE_ROW), texts.index(self.ROW) + 1)   # right after tasks'
        label = card._drive_row.label
        self.assertTrue(card.panel.rect().contains(label.mapTo(card.panel, label.rect().bottomRight())))
        card._on_turn_on()
        settle()
        self.assertEqual(self.desktop.consent_version(), desktop_ui.DRIVE_CONSENT_VERSION)
        self.assertTrue(self.desktop.consent_for_drive())

    def test_a_first_card_has_the_row_unmarked(self) -> None:
        self.drive_agent(self.held(Stages()), version=False)
        self.c._show_desktop_consent(desktop_ui.ENTRY_DOCK)
        settle()
        self.assertIn(desktop_view.CONSENT_DRIVE_ROW, self.card().texts())
        self.assertEqual(self.card().new_rows(), [])

    def test_with_drive_off_no_row_and_turn_on_stores_v3(self) -> None:
        self.drive_agent(self.held(Stages()), version=False, agent="drive = false\n")
        self.c._show_desktop_consent(desktop_ui.ENTRY_DOCK)
        settle()
        self.assertNotIn(desktop_view.CONSENT_DRIVE_ROW, self.card().texts())
        self.card()._on_turn_on()
        settle()
        self.assertEqual(self.desktop.consent_version(), desktop_ui.AGENT_CONSENT_VERSION)
        self.assertFalse(self.desktop.consent_for_drive())


class DriveConfigTests(unittest.TestCase):
    def test_defaults_and_ranges(self) -> None:
        agent = AgentConfig()
        self.assertEqual((agent.drive, agent.max_pages, agent.max_actions, agent.max_sends), (True, 40, 150, 5))
        for name in ("max_pages", "max_actions", "max_sends"):
            low, high = AGENT_RANGES[name]
            self.assertLessEqual(low, getattr(agent, name))
            self.assertGreaterEqual(high, getattr(agent, name))

    def test_out_of_range_and_odd_values_are_the_defaults(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text("[agent]\ndrive = \"yes\"\nmax_pages = 0\nmax_actions = 99999\nmax_sends = 3\n",
                            encoding="utf-8")
            with self.assertLogs("briefing_reader.config", level=logging.WARNING):
                agent = load_config(Path(folder), environ={}).agent
        # A word that is no boolean is the default; a number out of its range is held at the range's end.
        self.assertEqual((agent.drive, agent.max_pages, agent.max_actions, agent.max_sends), (True, 5, 1000, 3))
        self.assertEqual(policy.drive_caps(type("C", (), {"agent": agent})())._step,
                         {drive.CAP_PAGES: 5, drive.CAP_ACTIONS: 1000, drive.CAP_SENDS: 3})


# --------------------------------------------------------------------------
# The Send card
# --------------------------------------------------------------------------

class SendCardTests(DriveApp):
    def send_task(self, site: dict[str, FakePage] | None = None,
                  timing: Any = TIMING) -> tuple[Stages, dict[str, Any]]:
        stages = Stages()
        said: dict[str, Any] = {"heard": []}

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("open_page", url=FORM_URL)
            engine.call("list_controls")
            engine.call("type_text", ref="e1", text=NAME)
            engine.call("type_text", ref="e2", text=EMAIL)
            stages.reach(1)
            said["press"] = engine.call("press", ref="e5")
            engine.end_turn("Waiting for the owner.")
            while True:                                 # the owner's words come in as theirs: they answer nothing
                message = engine.wait_message()
                said["heard"].append(message)
                if message.startswith("Jarvis:"):
                    said["next"] = message
                    break
                engine.end_turn("Still waiting for the card.")
            engine.call("finish", summary="Sent it.", outcome="done")
            engine.end_turn("Done.")

        self.drive_agent(script, site=site, timing=timing)
        self.go()
        self.reached(stages, 1)
        self.c.agent.firewall.taint.add([NAME])        # the name came from the owner's mail, say
        stages.go(1)
        return stages, said

    def test_the_card_every_value_exactly_its_tags_and_buttons(self) -> None:
        self.send_task()
        card = self.asked_card()
        task = card.task()
        self.assertEqual(task.kicker, "SEND ON www.example.org?")
        self.assertEqual(task.body, "Jarvis wants to press 'Apply now' on www.example.org")
        self.assertEqual(task.tone, hud_overlay.TONE_AMBER)
        self.assertEqual([button.text for button in task.buttons], ["Don't", "Send"])
        self.assertEqual(task.note, drive.SEND_NOTE)
        rows = card.field_rows()
        self.assertEqual(rows[0], ("Full name", NAME, drive.TAINT_TAG, True))
        self.assertEqual(rows[1][:2], ("Email", EMAIL))
        self.assertIn(("", "Goes to: www.example.org", "", False), rows)
        self.assertEqual(card.cut_texts(), [])
        self.assertEqual(card.keyboard_controls(), [])               # no key of the owner's can press it
        self.assertTrue(card.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(all(button.focusPolicy() == Qt.FocusPolicy.NoFocus for button in card.buttons().values()))
        self.assertFalse(any(task.body in line for line in self.said()))   # never said aloud: a page's words
        self.assertEqual(self.drive_browser.presses, [])
        self.c.agent.stop("test")
        self.ended()

    def test_typed_or_spoken_words_never_answer_it(self) -> None:
        self.send_task()
        self.asked_card()
        for typed in ("Send", "yes send it", "ok"):
            self.type_it(typed)
            self.assertEqual(self.desktop.waiting_count(), 1, typed)
        self.assertEqual(self.drive_browser.presses, [])
        self.assertIn("only a click answers that card", self.c.ask.bar.status())
        self.c.agent.stop("test")
        self.ended()

    def test_a_card_just_up_takes_no_click_then_send_presses(self) -> None:
        _stages, said = self.send_task()
        self.asked_card()
        self.click(self.feed.card.buttons()["choice:0"])              # 0.6 s guard: it has only just appeared
        self.assertEqual(self.desktop.waiting_count(), 1)
        self.assertEqual(self.drive_browser.presses, [])
        self.press("choice:0")
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertTrue(wait_for(lambda: "next" in said, timeout=10))
        self.assertTrue(said["next"].startswith(tools.SENT_TEXT))
        self.assertEqual(len(self.drive_browser.presses), 1)
        self.assertEqual(self.ended().outcome, "done")
        self.assertEqual(self.drive_browser.bigs, [True])            # a done result: the window opens big

    def test_don_t_no_press(self) -> None:
        _stages, said = self.send_task()
        self.asked_card()
        self.press("choice:1")
        self.assertTrue(wait_for(lambda: "next" in said, timeout=10))
        self.assertEqual(said["next"], ar.SUBMIT_DECLINED_TEXT)
        self.assertEqual(self.drive_browser.presses, [])
        self.ended()

    def test_counted_on_approvals_with_show_me(self) -> None:
        self.send_task()
        self.asked_card()
        self.assertEqual(self.desktop.waiting_count(), 1)
        self.back()
        settle()
        self.assertEqual(self.c._desktop_waiting.count(), 1)           # Approvals' reminder: Show me
        self.c._on_desktop_show_me()
        settle()
        self.assertTrue(self.desktop.is_hud())
        self.assertTrue(self.feed.card.isVisible())
        self.assertEqual(self.feed.card.task().kicker, "SEND ON www.example.org?")
        self.c.agent.stop("test")
        self.ended()

    def test_stop_withdraws_it_and_a_late_click_does_nothing(self) -> None:
        self.send_task()
        card = self.asked_card()
        question = self.feed._question
        button = card.buttons()["choice:0"]
        self.feed._card_at -= 5
        self.c.agent.stop("stop")
        end = self.ended()
        self.assertEqual(end.outcome, "stopped")
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertIsNot(self.feed.card, card)                       # the Send card went (the result card is up)
        self.feed._on_choice(0)                                      # a late click
        self.assertFalse(self.c.agent.answer(question.id, ar.SUBMIT_SEND_WORD))
        del button
        settle()
        self.assertEqual(self.drive_browser.presses, [])
        self.assertTrue(wait_for(lambda: self.drive_browser.closed_tabs, timeout=5))
        self.assertEqual(self.drive_browser.bigs, [False])

    def test_a_form_going_elsewhere_is_amber_with_its_tag(self) -> None:
        site = pages()
        site[FORM_URL] = form_page(forms=[("post", ELSEWHERE)])
        self.send_task(site)
        rows = self.asked_card() and self.feed.card.field_rows()
        self.assertIn(("", "Goes to: forms.example.net", agent_feed.DEST_TAG, True), rows)
        self.c.agent.stop("test")
        self.ended()

    def test_no_answer_goes_with_words(self) -> None:
        expired: list[Any] = []
        self.send_task(timing=replace(TIMING, submit_card_s=0.5))
        self.c.agent.questionExpired.connect(expired.append)
        with mock.patch.object(self.feed, "_words") as words:
            self.asked_card()
            self.assertTrue(wait_for(lambda: expired, timeout=5))
            settle()
        words.assert_called_once_with("No answer came, so nothing was sent, sir. The task goes on.", mock.ANY,
                                      speak=False)
        self.assertEqual(self.drive_browser.presses, [])
        self.ended()


# --------------------------------------------------------------------------
# Pause and Resume, and the end
# --------------------------------------------------------------------------

class PauseTests(DriveApp):
    def paused_task(self, version: int = desktop_ui.DRIVE_CONSENT_VERSION) -> tuple[Stages, dict[str, Any]]:
        stages = Stages()
        said: dict[str, Any] = {}

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("update_plan", found="", steps=[{"text": "Fill it in", "status": "doing"}])
            stages.reach(1)
            said["held"] = engine.call("open_page", url=FORM_URL)
            engine.end_turn("Paused.")
            said["next"] = engine.wait_message()
            engine.call("finish", summary="Done.", outcome="done")
            engine.end_turn("Done.")

        self.drive_agent(script, version=version)
        self.go()
        self.reached(stages, 1)
        return stages, said

    def test_pause_holds_the_calls_and_resume_says_so(self) -> None:
        stages, said = self.paused_task()
        buttons = self.feed.buttons()
        self.assertFalse(buttons.pause_button.isHidden())
        self.assertTrue(buttons.resume_button.isHidden())
        self.click(buttons.pause_button)
        self.assertTrue(self.c.agent.paused())
        self.assertTrue(buttons.pause_button.isHidden())
        self.assertFalse(buttons.resume_button.isHidden())
        self.assertEqual(self.feed.activity(), agent_feed.PAUSED_ACTIVITY)
        self.assertEqual(buttons.pause_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        stages.go(1)
        self.assertTrue(wait_for(lambda: "held" in said, timeout=10))
        self.assertEqual(self.drive_browser.loads, [])               # nothing loaded while paused
        self.click(buttons.resume_button)
        self.assertFalse(self.c.agent.paused())
        self.assertFalse(buttons.pause_button.isHidden())
        self.assertTrue(wait_for(lambda: "next" in said, timeout=10))
        self.assertEqual(said["next"], ar.OWNER_RESUMED_TEXT)
        self.ended()
        self.assertTrue(buttons.pause_button.isHidden())             # the task ended: neither shows
        self.assertTrue(buttons.resume_button.isHidden())

    def test_a_task_that_fills_in_no_pages_has_no_pause(self) -> None:
        stages, _said = self.paused_task(version=3)
        buttons = self.feed.buttons()
        self.assertTrue(buttons.pause_button.isHidden())
        self.assertTrue(buttons.resume_button.isHidden())
        self.assertFalse(self.c.agent.can_pause())
        self.assertFalse(self.c.agent.pause())
        stages.go(1)
        self.ended()


# --------------------------------------------------------------------------
# "Open in my browser" (no engine is started; the opener is always a fake)
# --------------------------------------------------------------------------

class OpenInMyBrowserTests(unittest.TestCase):
    PAGE = "https://www.example.org/apply?step=2"

    def setUp(self) -> None:
        self.state = snapshot_engine._EngineState()
        patcher = mock.patch.object(snapshot_engine, "_ENGINE", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.opened: list[str] = []
        self.browser = agent_browser.AgentBrowser(drive=True, open_url=self.opened.append)
        self.addCleanup(self.browser.shutdown)
        self.window = self.browser._make_window()
        self.window.resize(agent_browser.MIN_WIDTH, 500)

    def ask(self, op: str, *args: Any) -> Any:
        box: list[Any] = []
        getattr(self.browser, op)(*args, done=box.append)
        wait_for(lambda: bool(box), timeout=3)
        return box[0] if box else "no answer"

    def page_up(self, address: str, tab: int = 1) -> None:
        self.browser._addresses[tab] = address
        self.browser._show_tabs()

    def test_hidden_until_a_public_page_is_shown(self) -> None:
        self.assertFalse(self.window.open_shown())
        self.page_up(self.PAGE)
        self.assertTrue(self.window.open_shown())
        button = self.window.open_button
        self.assertEqual(button.text(), "Open in my browser")
        self.assertEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertLess(button.geometry().right(), self.window.hide_button.geometry().left()
                        + 2 * agent_browser.holo.HoloButton.MARGIN)
        self.assertEqual(self.window.cut_texts(), [])

    def test_a_click_hands_over_the_address_only_once_a_moment(self) -> None:
        self.page_up(self.PAGE)
        self.window.open_button.click()
        self.window.open_button.click()                              # within the guard: nothing more
        self.assertEqual(self.opened, [self.PAGE])

    def test_never_while_sealed(self) -> None:
        self.page_up(self.PAGE)
        self.assertTrue(self.ask("seal", True))
        self.assertFalse(self.window.open_shown())
        self.browser._on_open_clicked()
        self.assertEqual(self.opened, [])

    def test_never_an_address_the_link_check_refuses(self) -> None:
        for address in ("http://www.example.org/", "https://127.0.0.1/", "https://user:pw@www.example.org/",
                        "https://www.example.org:8443/", "jarvis-task://x/a.html", ""):
            self.page_up(address)
            self.assertFalse(self.window.open_shown(), address)
            self.browser._on_open_clicked()
        self.assertEqual(self.opened, [])

    def test_not_in_a_research_window(self) -> None:
        plain = agent_browser.AgentBrowser(open_url=self.opened.append)
        self.addCleanup(plain.shutdown)
        window = plain._make_window()
        plain._addresses[1] = self.PAGE
        plain._show_tabs()
        self.assertFalse(window.open_shown())
        plain._on_open_clicked()
        self.assertEqual(self.opened, [])

    def test_a_closed_tab_s_address_goes_and_the_last_page_s_stays_at_the_end(self) -> None:
        self.assertTrue(self.ask("new_tab", 2))
        self.page_up("https://www.example.org/two", 2)
        self.assertTrue(self.window.open_shown())
        self.assertTrue(self.ask("close_tab", 2, False))
        self.assertFalse(self.window.open_shown())                   # tab 1 has no page
        self.page_up(self.PAGE)
        self.browser._end_tabs()
        self.assertEqual(self.browser.open_address(), self.PAGE)

    def test_the_real_opener_opens_nothing_under_the_test_guard(self) -> None:
        with mock.patch("PySide6.QtGui.QDesktopServices.openUrl") as open_url, \
                mock.patch.dict("os.environ", {"JARVIS_DESKTOP_TEST_GUARD": "1"}):
            agent_browser.owner_browser(self.PAGE)
        open_url.assert_not_called()

    def test_a_failing_opener_is_only_a_warning(self) -> None:
        def broken(_url: str) -> None:
            raise OSError("no")

        self.browser._open_url = broken
        self.page_up(self.PAGE)
        with self.assertLogs("briefing_reader.agent.browser", level=logging.WARNING) as logs:
            self.browser._on_open_clicked()
        self.assertNotIn("example", "\n".join(logs.output))


class WatchWindowOpenTests(unittest.TestCase):
    def test_the_strip_s_words_end_before_open(self) -> None:
        window = WatchWindow(native=hud_overlay.NoNative())
        self.addCleanup(window.deleteLater)
        window.resize(900, 500)
        before = window._strip_right()
        window.set_open_shown(True)
        self.assertLess(window._strip_right(), before)
        self.assertLessEqual(window._strip_right(), window.open_button.geometry().left()
                             + agent_browser.holo.HoloButton.MARGIN)
        window.set_tabs([("www.example.org", True), ("www.example.net", False)])
        self.assertEqual(window.cut_texts(), [])


if __name__ == "__main__":
    unittest.main()
