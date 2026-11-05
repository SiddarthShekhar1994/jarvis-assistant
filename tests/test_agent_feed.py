"""The feed the owner sees (part A3): the panel through a whole task, its activity line, Hide and Show, the result card
in every outcome, Show and Folder (and that nothing reaches the owner's browser or a default app), each card that waits,
the click-only ones refusing words, Stop in each way, no global Esc, the bar's typing, the consent card's versions,
Home's chip and counts, the spoken summary, the engine's start, the focus never taken.

Qt runs offscreen and shows nothing. Fakes throughout: tests.agent_fakes.FakeEngine really POSTs to the real endpoint on
loopback, under the real controller and the app; the desktop's backend is a fake over a temporary folder; the HUD's
native seam records. Every value is invented; files go only to temporary folders.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import unittest
from dataclasses import replace
from typing import Any
from unittest import mock

from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from briefing_reader import agent_feed, agent_ui, desktop_ui, desktop_view, hud, hud_overlay, live, settings_view, snapshot_engine, ui
from briefing_reader.agent import runner as ar
from briefing_reader.agent import web
from briefing_reader.agent_feed import TaskFeed, spoken_summary
from briefing_reader.prefs import PREFS_FILE, AssistantPrefs
from tests.agent_fakes import FAST, FakeBrowser, FakeEngine, FakePage
from tests.ask_fakes import plan_stream
from tests.desktop_fakes import FakeStopKeys, desktop_disk
from tests.test_agent_app import SUMMARY, TASK_PLAN, TYPED, WEB_TIMING, AgentApp
from tests.test_desktop_keys import HUD_KEY
from tests.ui_fakes import fonts, settle, wait_for

_app: QApplication | None = None
STEPS = [{"text": "Find the three laptops", "status": "doing"}, {"text": "Compare them", "status": "todo"},
         {"text": "Write the table", "status": "todo"}]
RESULT = "https://www.example.org/laptops/quiet?id=7"
QUERY = "three quiet invented laptops"
LONG = "Compared all three and the Quiet Two wins. " + " ".join(f"Sentence number {n} of the long report goes here." for n in range(40))


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_agent_feed", "-platform", "offscreen"])
    fonts()


class Stages:
    """Where a script waits for the test: ``reach(n)`` on the engine's thread, ``wait(n)`` and ``go(n)`` on the
    test's."""

    def __init__(self) -> None:
        self.at: dict[int, threading.Event] = {}
        self.on: dict[int, threading.Event] = {}

    def _pair(self, number: int) -> tuple[threading.Event, threading.Event]:
        return self.at.setdefault(number, threading.Event()), self.on.setdefault(number, threading.Event())

    def reach(self, number: int) -> None:
        at, on = self._pair(number)
        at.set()
        on.wait(20)

    def go(self, number: int) -> None:
        self._pair(number)[1].set()


def plan(*states: str) -> list[dict[str, str]]:
    return [{"text": step["text"], "status": state} for step, state in zip(STEPS, states)]


class FeedApp(AgentApp):
    def make_agent(self, *runs: Any, **harness: Any) -> Any:
        harness.setdefault("desktop_consent", desktop_ui.AGENT_CONSENT_VERSION)
        harness.setdefault("browser", FakeBrowser())
        self.fake_browser = harness["browser"]
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.fake = desktop_disk(tmp.name)
        harness.setdefault("desktop_backend", lambda: self.fake)
        return super().make_agent(*runs, **harness)

    def start(self, script: Any, **harness: Any) -> FakeEngine:
        engine = FakeEngine(script)
        self.make_agent(plan_stream(TASK_PLAN), engine, **harness)
        self.type_it(TYPED)
        self.started_task()
        settle()
        return engine

    def reached(self, stages: Stages, number: int) -> None:
        stages._pair(number)
        self.assertTrue(wait_for(stages.at[number].is_set, timeout=10), f"stage {number}")
        settle()

    @property
    def feed(self) -> TaskFeed:
        return self.c.task_feed

    def head(self) -> str:
        return self.feed.panel().content().head

    def click(self, widget: Any) -> None:
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        settle()

    def press(self, key: str) -> None:
        """A click on the card's button, the card no longer new."""
        self.feed._card_at -= 5
        self.click(self.feed.card.buttons()[key])

    def asked_card(self) -> hud_overlay.TaskCard:
        self.assertTrue(wait_for(lambda: self.feed.waits() == 1, timeout=10), "a card waits")
        settle()
        return self.feed.card

    def card_words(self) -> str:
        return self.feed.card.task().body


def whole(engine: FakeEngine, stages: Stages | None = None) -> None:
    engine.wait_message()
    engine.call("update_plan", steps=plan("doing", "todo", "todo"))
    if stages:
        stages.reach(1)
    engine.call("update_plan", steps=plan("done", "doing", "todo"))
    if stages:
        stages.reach(2)
    engine.call("write_file", name="table.md", text="| a | b |")
    engine.call("update_plan", steps=plan("done", "done", "done"))
    engine.call("finish", summary=SUMMARY, outcome="done", outputs=["table.md"])
    engine.end_turn("Done.")


class PanelTests(FeedApp):
    def test_the_panel_through_a_whole_task(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.assertTrue(self.desktop.is_hud())                       # it moved to the HUD
        self.reached(stages, 1)
        panel = self.feed.panel()
        self.assertTrue(panel.isVisible())
        self.assertRegex(self.head(), r"^WORKING - 1 OF 3 - \d+:\d\d$")
        self.assertEqual([(row.text, row.state) for row in panel.content().rows],
                         [("Find the three laptops", "doing"), ("Compare them", "todo"), ("Write the table", "todo")])
        self.assertEqual(panel.shown_rows(), ["Find the three laptops", "Compare them", "Write the table"])
        self.assertEqual(panel.content().tone, hud_overlay.TONE_CYAN)
        self.assertTrue(self.feed.buttons().isVisible())
        self.assertFalse(self.feed.buttons().stop_button.isHidden())
        self.assertTrue(self.feed._tick.isActive())
        stages.go(1)
        self.reached(stages, 2)
        self.assertRegex(self.head(), r"^WORKING - 2 OF 3 - ")
        stages.go(2)
        end = self.ended()
        self.assertEqual(end.outcome, "done")
        settle()
        self.assertFalse(self.feed._tick.isActive())
        self.assertRegex(self.head(), r"^DONE - 3 STEPS - \d+:\d\d$")   # stamped
        self.assertEqual(panel.content().tone, hud_overlay.TONE_DONE)
        self.assertFalse(self.feed.buttons().isVisible())            # no Stop any more
        self.assertEqual(self.feed.activity(), agent_feed.ENDED_ACTIVITY)

    def test_the_header_s_clock_ticks_only_while_it_works(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.reached(stages, 1)
        with mock.patch.object(self.c.agent, "worked_s", return_value=134.0):
            self.feed._on_tick()
        self.assertEqual(self.head(), "WORKING - 1 OF 3 - 2:14")
        stages.go(1)
        stages.go(2)
        self.ended()

    def test_at_most_about_8_rows_and_the_step_at_work_stays_in_view(self) -> None:
        rows = [hud_overlay.PlanRow(f"Step {n}", "done" if n < 10 else "doing" if n == 10 else "todo") for n in range(14)]
        shown, more = hud_overlay.panel_rows(rows, hud_overlay.PANEL_MAX_ROWS)
        self.assertEqual(len(shown), 8)
        self.assertEqual(more, 6)
        self.assertIn(rows[10], shown)
        panel = hud_overlay.PlanPanel(native=hud_overlay.NoNative(), hide_from_capture=False)
        self.addCleanup(panel.deleteLater)
        panel.set_panel(hud_overlay.PanelContent("WORKING - 11 OF 14 - 0:10", rows=tuple(rows), activity="Writing a file"))
        self.assertEqual(panel.more_count(), 6)
        panel.set_limits(400, hud_overlay.PANEL_COMPACT + 20)        # kept on screen: fewer rows, never off it
        self.assertLessEqual(panel.height(), hud_overlay.PANEL_COMPACT + 40)
        self.assertEqual(len(panel.shown_rows()), 1)
        self.assertEqual(panel.cut_texts(), [])

    def test_click_through_except_its_buttons_and_never_the_focus(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.reached(stages, 1)
        panel, buttons = self.feed.panel(), self.feed.buttons()
        self.assertTrue(panel.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.assertFalse(buttons.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        for window in (panel, buttons):
            self.assertTrue(window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertEqual(buttons.stop_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(buttons.show_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual([call for call in self.native.keys if call[0] == "activate"], [])
        self.assertIsNone(QApplication.focusWidget())
        stages.go(1)
        stages.go(2)
        self.ended()

    def test_clear_of_the_bar_chips_cards_and_window_and_kept_on_screen(self) -> None:
        stages = Stages()
        self.start(lambda engine: (engine.wait_message(), engine.call("update_plan", steps=STEPS),
                                   engine.call("ask_owner", question="Blue or Red?", choices=["Blue", "Red"]),
                                   engine.end_turn("Asked."), stages.reach(1)))
        card = self.asked_card()
        overlay = self.desktop.overlay
        hud_window = overlay.hud
        for area, watch in ((QRect(0, 0, 1920, 1040), QRect(944, 200, 960, 824)), (QRect(0, 0, 1280, 680), QRect(624, 200, 640, 464)),
                            (QRect(0, 0, 1024, 720), QRect(368, 200, 640, 504)), (QRect(0, 0, 1920, 1040), None)):
            hud_window.place_in(area)
            hud_window.set_pending(1)
            self.fake_browser.rect = watch
            self.feed._place()
            settle()
            panel = self.feed.panel().geometry()
            where = f"{area.getRect()} {watch.getRect() if watch else None}"
            self.assertFalse(panel.intersects(hud_window.occupied_rect()), where)
            self.assertFalse(card.geometry().intersects(hud_window.occupied_rect()), where)
            self.assertFalse(panel.intersects(card.geometry()), where)
            self.assertTrue(area.contains(panel), where)
            self.assertTrue(area.contains(card.geometry()), where)
            if watch is not None:
                self.assertFalse(panel.intersects(watch), where)
                self.assertFalse(card.geometry().intersects(watch), where)
            self.assertTrue(panel.contains(self.feed.buttons().geometry()), where)
        stages.go(1)
        self.c.agent.stop("test")
        self.ended()

    def test_a_long_caption_or_a_chip_moves_it_down(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.reached(stages, 1)
        top = self.feed.panel().y()
        self.desktop.overlay.hud.set_instant([("volume", "Volume - 20%")], undo_all=True)   # a row of chips more
        settle()
        self.assertGreater(self.feed.panel().y(), top)
        self.assertGreaterEqual(self.fake_browser.kept_clear, 1)     # and the window keeps clear of it too
        stages.go(1)
        stages.go(2)
        self.ended()


class ActivityTests(FeedApp):
    def script(self, stages: Stages) -> Any:
        def run(engine: FakeEngine) -> None:
            engine.wait_message()
            stages.reach(1)
            engine.call("web_search", query=QUERY)
            stages.reach(2)
            engine.call("open_page", number=1)
            stages.reach(3)
            engine.call("open_page", url=RESULT)
            engine.call("read_more")
            stages.reach(4)
            engine.call("write_file", name="a.md", text="x")
            stages.reach(5)
            engine.call("fetch_text", url=RESULT, question="the price?")
            stages.reach(6)
            engine.call("finish", summary=SUMMARY, outcome="done")
            engine.end_turn()
        return run

    def pages(self) -> FakeBrowser:
        return FakeBrowser({web.ENGINES[0].url(QUERY): FakePage(results=[("Laptops", RESULT, "Quiet.")]),
                            RESULT: FakePage(title="Laptops", text="Words.")})

    def test_it_moves_though_the_plan_does_not_and_shows_hosts_only(self) -> None:
        stages = Stages()
        with self.assertLogs("briefing_reader", level=logging.INFO) as logs:
            engine = FakeEngine(self.script(stages))
            from tests.test_agent_fetch import good_stream
            self.make_agent(plan_stream(TASK_PLAN), engine, good_stream(), browser=self.pages(), timing=WEB_TIMING)
            self.type_it(TYPED)
            self.reached(stages, 1)
            self.assertEqual(self.feed.activity(), agent_feed.STARTING_ACTIVITY)
            shown = []
            for stage in (1, 2, 3, 4, 5):
                stages.go(stage)
                self.reached(stages, stage + 1)
                shown.append(self.feed.activity())
                self.assertEqual(self.feed.panel().content().activity, shown[-1])
            stages.go(6)
            self.ended()
        self.assertEqual(shown, ["Searching the web", "Reading www.example.org", "Reading on", "Writing a file",
                                 "Reading www.example.org another way"])
        self.assertNotIn("quiet", " ".join(shown))
        for words in shown:
            self.assertNotIn(words, self.said())                      # never spoken
        said = " ".join(logs.output)
        self.assertNotIn("Searching the web", said)                   # never logged
        self.assertNotIn("example.org", said)
        self.assertNotIn(STEPS[0]["text"], said)

    def test_no_tool_at_work_for_a_while_says_it_is_thinking(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.reached(stages, 1)
        self.assertTrue(self.feed._quiet.isActive())
        self.feed._on_quiet()
        self.assertEqual(self.feed.activity(), agent_feed.THINKING_ACTIVITY)
        stages.go(1)
        stages.go(2)
        self.ended()

    def test_a_bad_address_shows_no_part_of_it(self) -> None:
        from briefing_reader.agent import tools

        ctx = tools.RunContext()
        self.assertEqual(tools.doing_words("open_page", {"url": "http://evil.example/?d=secret"}, ctx), tools.READING_PAGE)
        self.assertEqual(tools.doing_words("open_page", {"url": "https://www.example.org/secret/path?q=1"}, ctx),
                         "Reading www.example.org")
        self.assertEqual(tools.doing_words("update_plan", {}, ctx), "")
        self.assertEqual(tools.doing_words("say", {}, ctx), "")
        self.assertEqual(tools.doing_words("ask_owner", {}, ctx), "Waiting for you")


class HideShowTests(FeedApp):
    def test_hide_on_the_strip_and_show_on_the_panel(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.reached(stages, 1)
        buttons = self.feed.buttons()
        self.assertTrue(buttons.show_button.isHidden())
        self.fake_browser.owner_hidden = True                         # the strip's Hide
        self.c.agent.windowHidden.emit(True)
        settle()
        self.assertFalse(buttons.show_button.isHidden())
        self.assertTrue(self.c.agent.running)                         # the task carries on behind it
        self.click(buttons.show_button)
        self.assertFalse(self.fake_browser.owner_hidden)
        self.assertTrue(buttons.show_button.isHidden())
        stages.go(1)
        stages.go(2)
        self.ended()


class ResultCardTests(FeedApp):
    def end_with(self, script: Any, **harness: Any) -> hud_overlay.TaskCard:
        self.start(script, **harness)
        self.ended()
        settle()
        self.assertIsNotNone(self.feed.card)
        return self.feed.card

    def test_done(self) -> None:
        card = self.end_with(whole)
        task = card.task()
        self.assertRegex(task.kicker, r"^DONE - 3 STEPS - \d+:\d\d$")
        self.assertEqual(task.tone, hud_overlay.TONE_DONE)
        self.assertEqual(task.body, SUMMARY)
        self.assertEqual([row.name for row in task.rows], ["table.md"])
        self.assertEqual([button.text for button in task.buttons], ["Done"])
        self.assertTrue(card.isVisible())
        self.assertEqual(card.cut_texts(), [])
        self.assertTrue(self.fake_browser.holds[-1])                  # the window stays while the card stays
        self.assertEqual(self.fake_browser.closed, 0)
        self.assertEqual(self.desktop.waiting_count(), 0)             # nothing waits: Done is no decision

    def test_partly_done(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("update_plan", steps=plan("done", "doing", "todo"))
            engine.call("finish", summary="Some of it.", outcome="partial")
            engine.end_turn()

        task = self.end_with(script).task()
        self.assertRegex(task.kicker, r"^PARTLY DONE - 1 OF 3 - ")
        self.assertEqual(task.tone, hud_overlay.TONE_CYAN)            # amber only where a decision waits
        self.assertEqual(self.feed.panel().content().rows[1].state, "failed")   # stamped

    def test_failed(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("finish", summary="None of it.", outcome="failed")
            engine.end_turn()

        task = self.end_with(script).task()
        self.assertRegex(task.kicker, r"^FAILED - \d+ STEPS? - \d+:\d\d$")
        self.assertEqual(task.tone, hud_overlay.TONE_CORAL)
        self.assertEqual(task.rows, ())

    def test_stopped_lists_the_files_it_wrote(self) -> None:
        stages = Stages()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("update_plan", steps=plan("doing", "todo", "todo"))
            engine.call("write_file", name="notes.md", text="so far")
            stages.reach(1)

        self.start(script)
        self.reached(stages, 1)
        self.click(self.feed.buttons().stop_button)
        end = self.ended()
        settle()
        task = self.feed.card.task()
        self.assertEqual(end.outcome, "stopped")
        self.assertRegex(task.kicker, r"^STOPPED - 0 OF 3 - ")
        self.assertEqual(task.tone, hud_overlay.TONE_CORAL)
        self.assertEqual(task.body, "Stopped, sir. Files already written stay in the workspace.")
        self.assertEqual([row.name for row in task.rows], ["notes.md"])
        self.assertEqual(self.feed.panel().content().rows[0].state, "failed")
        self.assertEqual(self.feed.panel().content().tone, hud_overlay.TONE_CORAL)
        stages.go(1)

    def test_a_long_report_is_all_there_and_scrolls(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("finish", summary=LONG[:1500], outcome="done")
            engine.end_turn()

        card = self.end_with(script)
        self.assertEqual(card.body.text(), LONG[:1500])
        self.desktop.overlay.hud.place_in(QRect(0, 0, 1280, 680))
        self.fake_browser.rect = QRect(624, 200, 640, 464)
        self.feed._place()
        settle()
        self.assertGreater(card.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(card.scroll.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(card.scroll.verticalScrollBar().focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertTrue(QRect(0, 0, 1280, 680).contains(card.geometry()))

    def test_its_words_are_never_read_as_html(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("finish", summary="<b>Bold</b> <img src=x> & more", outcome="done")
            engine.end_turn()

        card = self.end_with(script)
        self.assertEqual(card.body.text(), "<b>Bold</b> <img src=x> & more")
        self.assertIsInstance(card.body, hud_overlay._PlainBody)      # it paints a text layout: no rich text anywhere

    def test_done_dismisses_everything(self) -> None:
        card = self.end_with(whole)
        self.press("done")
        self.assertIsNone(self.feed.card)
        self.assertFalse(card.isVisible() if hud_overlay.shiboken6.isValid(card) else False)
        self.assertFalse(self.feed.panel().isVisible())
        self.assertFalse(self.feed.active())
        self.assertEqual(self.fake_browser.closed, 1)                 # the window goes with it

    def test_a_new_desktop_request_takes_it_away_as_it_does_a_desktop_card(self) -> None:
        self.end_with(whole)
        self.c._desktop_request("open Downloads")
        settle()
        self.assertIsNone(self.feed.card)
        self.assertFalse(self.feed.active())

    def test_a_new_task_takes_the_last_result_away(self) -> None:
        self.end_with(whole)
        first = self.c.agent.last_end
        engine = FakeEngine(whole)
        self.harness.factory.runner.runs.append(engine)
        self.c.agent.start(TYPED)
        self.assertTrue(wait_for(lambda: self.c.agent.last_end is not first, timeout=10))
        settle()
        self.assertEqual(self.fake_browser.closed, 1)
        self.assertEqual(len([card for card in self.desktop.overlay.cards() if isinstance(card, hud_overlay.TaskCard)]), 1)


class OutputTests(FeedApp):
    def finished(self) -> hud_overlay.TaskCard:
        self.start(whole)
        self.ended()
        settle()
        return self.feed.card

    def test_show_opens_it_in_the_sealed_window_and_folder_in_explorer_only(self) -> None:
        card = self.finished()
        with mock.patch("PySide6.QtGui.QDesktopServices.openUrl") as open_url, \
                mock.patch("webbrowser.open") as browser, \
                mock.patch("os.startfile", create=True) as start_file, \
                mock.patch("subprocess.Popen") as popen:
            self.feed._card_at -= 5
            buttons = card.row_buttons()
            self.click(buttons["show:table.md"])
            self.assertEqual(self.fake_browser.shown, ["table.md"])   # the sealed watch window
            self.click(buttons["folder:table.md"])
            self.assertTrue(wait_for(lambda: self.fake.opened, timeout=5))
            settle()
        path = str(self.c.agent.last_folder.path / "table.md")
        self.assertEqual(self.fake.opened, [("show_in_folder", path)])   # never open_document, open_url or launch_app
        for call in (open_url, browser, start_file, popen):
            call.assert_not_called()

    def test_the_card_s_way_never_names_a_way_out(self) -> None:
        import inspect

        for module in (agent_feed, agent_ui):
            source = inspect.getsource(module)
            for word in ("QDesktopServices", "webbrowser", "startfile", "open_document", "open_url"):
                self.assertNotIn(word, source.replace("never open_document", ""), module.__name__)

    def test_a_name_that_is_no_good_is_nothing(self) -> None:
        self.finished()
        agent = self.c.agent
        for name in ("../secret.md", "C:\\secret.md", "missing.md", "a/../../b.md"):
            self.assertEqual(agent.output_path(name), "", name)
            self.assertFalse(agent.show_output(name), name)
        self.assertEqual(self.fake_browser.shown, [])

    def test_folder_says_so_when_windows_refuses(self) -> None:
        card = self.finished()
        self.fake.open_errors["show_in_folder"] = ValueError("refused by the last guard")
        self.click(card.row_buttons()["folder:table.md"])
        self.assertTrue(wait_for(lambda: "couldn't show" in self.c.ask.bar.status(), timeout=5))

    def test_turned_off_folder_does_nothing(self) -> None:
        self.finished()
        path = self.c.agent.output_path("table.md")
        self.desktop.turn_off()
        self.assertFalse(self.desktop.reveal_task_file(path))
        settle()
        self.assertEqual(self.fake.opened, [])


class QuestionCardTests(FeedApp):
    @staticmethod
    def script(choices: Any = ("Blue", "Red")) -> Any:
        def run(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("update_plan", steps=plan("doing", "todo", "todo"))
            engine.call("ask_owner", question="Blue or Red?", **({"choices": list(choices)} if choices else {}))
            engine.end_turn("Asked.")
            answer = engine.wait_message()
            engine.call("finish", summary=answer, outcome="done")
            engine.end_turn()
        return run

    def test_its_question_and_choices_and_a_click_answers(self) -> None:
        self.start(self.script())
        card = self.asked_card()
        task = card.task()
        self.assertEqual((task.body, task.tone), ("Blue or Red?", hud_overlay.TONE_AMBER))
        self.assertEqual([button.text for button in task.buttons], ["Blue", "Red"])
        self.assertEqual(task.note, agent_feed.QUESTION_NOTE)
        self.assertRegex(self.head(), r"^WAITING - 1 OF 3 - ")        # amber only while a card waits
        self.assertEqual(self.feed.panel().content().tone, hud_overlay.TONE_AMBER)
        self.assertEqual(self.feed.activity(), agent_feed.WAITING_ACTIVITY)
        self.assertEqual(self.desktop.waiting_count(), 1)
        self.press("choice:1")
        self.assertEqual(self.ended().summary, "The owner answers your question: Red")
        self.assertEqual(self.desktop.waiting_count(), 0)

    def test_typing_answers_it_and_says_thanks(self) -> None:
        self.start(self.script())
        self.asked_card()
        self.type_it("the red one")
        self.assertIsNone(self.feed._question)
        self.assertEqual(self.ended().summary, "The owner answers your question: the red one")

    def test_a_free_question_says_to_type(self) -> None:
        self.start(self.script(choices=()))
        task = self.asked_card().task()
        self.assertEqual(task.buttons, ())
        self.assertEqual(task.note, agent_feed.QUESTION_FREE_NOTE)
        self.c.agent.stop("test")
        self.ended()

    def test_a_card_that_has_only_just_appeared_takes_no_click(self) -> None:
        self.start(self.script())
        self.asked_card()
        self.feed._card_at = self.feed._clock()
        self.click(self.feed.card.buttons()["choice:0"])
        self.assertEqual(self.desktop.waiting_count(), 1)             # it may have come under the pointer
        self.press("choice:0")
        self.assertEqual(self.ended().summary, "The owner answers your question: Blue")

    def test_the_app_s_click_guards_are_asked(self) -> None:
        self.start(self.script())
        self.asked_card()
        with mock.patch.object(self.desktop, "_click_guard", return_value=True) as guard:
            self.press("choice:0")
        guard.assert_called_once_with(agent_feed.GUARD_ID)
        self.assertEqual(self.desktop.waiting_count(), 1)
        self.c.agent.stop("test")
        self.ended()

    def test_no_answer_goes_with_words_saying_so(self) -> None:
        self.start(self.script(), timing=replace(FAST, question_s=0.3))
        expired: list[Any] = []
        self.c.agent.questionExpired.connect(expired.append)
        with mock.patch.object(self.feed, "_words") as words:
            self.asked_card()
            self.assertTrue(wait_for(lambda: expired, timeout=5))
            settle()
        words.assert_called_once_with("No answer came, so that question went, sir.", hud.TONE_IDLE, speak=False)
        end = self.ended()
        self.assertEqual(end.outcome, "partial")                      # the owner never answered: never "all of it"
        self.assertEqual(self.desktop.waiting_count(), 0)
        settle()
        self.assertEqual(self.feed.card.task().tone, hud_overlay.TONE_CYAN)   # the result, not the question


class LongChoiceTests(unittest.TestCase):
    def test_a_long_choice_is_cut_on_its_button_and_never_runs_off_the_card(self) -> None:
        card = hud_overlay.TaskCard(native=hud_overlay.NoNative(), hide_from_capture=False)
        self.addCleanup(card.deleteLater)
        words = "A very long choice of sixty characters that goes on and on.."
        self.assertEqual(len(words), 60)
        from briefing_reader import holo

        spec = hud_overlay.CardButton("choice:0", words, holo.AMBER_GHOST)
        card.set_task(hud_overlay.TaskContent("A question for you", "Which?", hud_overlay.TONE_AMBER, (spec,)))
        card.set_limits(hud_overlay.TASK_CARD_MIN_WIDTH, 600)
        self.assertEqual(card.cut_texts(), [])
        self.assertTrue(card.shown_buttons()[0].endswith("..."))
        self.assertEqual(card.task().buttons[0].text, words)         # what a click answers is whole


class WebCardTests(FeedApp):
    QUERY = "Halberd contract terms"

    def web_task(self, timing: Any = WEB_TIMING) -> tuple[FakeEngine, dict[str, Any]]:
        said: dict[str, Any] = {}
        tainted = threading.Event()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            tainted.wait(10)
            said["first"] = engine.call("web_search", query=self.QUERY)
            engine.end_turn("Waiting.")
            said["next"] = engine.wait_message()
            engine.end_turn("Still waiting.")
            said["last"] = engine.wait_message()
            engine.call("finish", summary=SUMMARY, outcome="done")
            engine.end_turn("Done.")

        browser = FakeBrowser({web.ENGINES[0].url(self.QUERY): FakePage(results=[("Terms", RESULT, "")])})
        engine = FakeEngine(script)
        self.make_agent(plan_stream(TASK_PLAN), engine, browser=browser, timing=timing)
        self.type_it(TYPED)
        self.assertTrue(wait_for(lambda: self.c.agent.firewall is not None, 10))
        self.c.agent.firewall.taint.add(["Project Halberd"])
        tainted.set()
        return engine, said

    def test_it_shows_the_exact_words_and_only_a_click_allows(self) -> None:
        _engine, said = self.web_task()
        card = self.asked_card()
        task = card.task()
        self.assertEqual(task.body, f'Jarvis wants to search the web for "{self.QUERY}"')
        self.assertEqual(task.tone, hud_overlay.TONE_AMBER)
        self.assertEqual([button.text for button in task.buttons], ["Don't", "Allow"])
        self.assertEqual(task.note, agent_feed.CLICK_ONLY_NOTE)
        self.assertEqual(card.keyboard_controls(), [])                # no key of the owner's can press it
        self.assertTrue(card.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(all(button.focusPolicy() == Qt.FocusPolicy.NoFocus for button in card.buttons().values()))
        self.assertFalse(card.take_keyboard())
        self.assertNotIn(task.body, self.said())                      # never said aloud
        for typed in ("yes", "Allow", "allow it", "okay"):            # typed or dictated: words, and nothing more
            self.type_it(typed)
            self.assertEqual(self.desktop.waiting_count(), 1, typed)
        self.assertEqual(self.fake_browser.loads, [])
        self.assertEqual(self.c.ask.bar.text(), "")                   # they went to the task as the owner's
        self.press("choice:0")
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertTrue(wait_for(lambda: "next" in said, timeout=10))
        self.c.agent.stop("test")
        self.ended()

    def test_don_t_is_a_refusal(self) -> None:
        _engine, said = self.web_task()
        self.asked_card()
        self.press("choice:1")
        self.assertTrue(wait_for(lambda: "next" in said, timeout=10))
        self.assertEqual(said["next"], ar.WEB_DECLINED_TEXT)
        self.assertEqual(self.fake_browser.loads, [])
        self.c.agent.stop("test")
        self.ended()

    def test_no_answer_is_a_refusal_with_words_and_the_task_goes_on(self) -> None:
        _engine, said = self.web_task(timing=replace(WEB_TIMING, web_card_s=0.3))
        self.asked_card()
        self.assertTrue(wait_for(lambda: "next" in said, timeout=10))
        settle()
        self.assertEqual(said["next"], ar.WEB_UNANSWERED_TEXT)
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertIsNone(self.feed.card)
        self.assertTrue(self.c.agent.running)
        self.assertIn("No answer came", self.c.ask.bar.status())
        self.c.agent.stop("test")
        self.ended()


class KeepGoingCardTests(FeedApp):
    def task(self, **harness: Any) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="One.")
            engine.call("write_file", name="a.md", text="a")
            engine.end_turn("Waiting.")
            engine.wait_message()
            engine.call("finish", summary="All of it.", outcome="done")
            engine.end_turn()

        self.make_agent(plan_stream(TASK_PLAN), FakeEngine(script), agent="max_turns = 5\n", **harness)
        self.c.agent._engine.agent = mock.Mock(max_turns=1, max_minutes=20, model="opus", effort="")
        self.type_it(TYPED)

    def test_click_only(self) -> None:
        self.task()
        card = self.asked_card()
        task = card.task()
        self.assertIn("Keep going?", task.body)
        self.assertEqual(task.tone, hud_overlay.TONE_AMBER)
        self.assertEqual([button.text for button in task.buttons], ["Stop here", "Keep going"])
        self.assertEqual(task.note, agent_feed.CLICK_ONLY_NOTE)
        for typed in ("yes", "keep going", "no"):                     # words never answer it
            self.type_it(typed)
            self.assertEqual(self.desktop.waiting_count(), 1, typed)
        self.assertTrue(self.c.agent.running)
        self.press("choice:0")
        self.assertEqual(self.ended().outcome, "done")

    def test_stop_here(self) -> None:
        self.task()
        self.asked_card()
        self.press("choice:1")
        self.assertEqual(self.desktop.waiting_count(), 0)
        end = self.ended()
        self.assertEqual(end.outcome, "partial")

    def test_no_answer_goes_with_words(self) -> None:
        self.task(timing=replace(FAST, keep_going_s=0.3))
        expired: list[Any] = []
        self.c.agent.questionExpired.connect(expired.append)
        with mock.patch.object(self.feed, "_words") as words:
            self.asked_card()
            self.assertTrue(wait_for(lambda: expired, timeout=5))
            settle()
        words.assert_called_once_with("No answer came, so the task stops here, sir.", hud.TONE_IDLE, speak=False)
        self.assertEqual(self.ended().outcome, "partial")
        self.assertEqual(self.desktop.waiting_count(), 0)


class StopTests(FeedApp):
    def running(self) -> tuple[FakeEngine, Stages]:
        stages = Stages()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("update_plan", steps=plan("doing", "todo", "todo"))
            engine.call("ask_owner", question="Blue or Red?", choices=["Blue", "Red"])
            engine.end_turn("Asked.")
            stages.reach(1)

        engine = self.start(script)
        self.asked_card()
        return engine, stages

    def assert_stopped(self, engine: FakeEngine, *, card: bool = True) -> None:
        end = self.ended()
        self.assertEqual(end.outcome, "stopped")
        self.assertTrue(wait_for(lambda: engine.killed, timeout=5))    # the process is killed
        self.assertEqual(self.desktop.waiting_count(), 0)              # the undecided card withdrawn
        self.assertFalse(self.c.agent.answer("x-1", "Blue"))
        self.assertEqual(self.agent_tasks()[0].status, live.STATUS_CANCELLED)   # the plan stamped, in Live too
        if card:
            self.assertEqual(self.feed.card.task().tone, hud_overlay.TONE_CORAL)
            self.assertRegex(self.head(), r"^STOPPED - ")
            self.assertEqual(self.feed.panel().content().rows[0].state, "failed")
        self.assertGreaterEqual(self.fake_browser.stopped, 1)          # the window goes at once

    def test_the_panel_s_stop(self) -> None:
        engine, stages = self.running()
        self.click(self.feed.buttons().stop_button)
        self.assert_stopped(engine)
        stages.go(1)

    def test_typed_stop_and_cancel_and_nothing_else(self) -> None:
        for words in ("stop", "Cancel.", "jarvis, please stop"):
            with self.subTest(words):
                engine, stages = self.running()
                self.type_it(words)
                self.assert_stopped(engine)
                self.assertEqual(self.c.ask.bar.text(), "")
                stages.go(1)

    def test_esc_while_the_hud_s_bar_has_the_keyboard(self) -> None:
        engine, stages = self.running()
        bar = self.desktop.bar()
        bar.set_text("half a thought")
        bar._on_escape()                                               # Esc first clears what was typed
        settle()
        self.assertTrue(self.c.agent.running)
        self.assertEqual(bar.text(), "")
        bar._on_escape()                                               # then stops the task
        self.assert_stopped(engine)
        stages.go(1)

    def test_turn_off(self) -> None:
        engine, stages = self.running()
        self.c._on_desktop_turn_off()
        end = self.ended()
        self.assertEqual(end.outcome, "stopped")
        self.assertTrue(wait_for(lambda: engine.killed, timeout=5))
        settle()
        self.assertIsNone(self.feed.card)                              # nothing stays: the HUD can't show
        self.assertFalse(self.feed.active())
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertEqual(self.reading.task_chip_text(), "")
        stages.go(1)

    def test_turn_off_with_a_result_card_up(self) -> None:
        self.start(whole)
        self.ended()
        settle()
        self.assertIsNotNone(self.feed.card)
        self.c._on_desktop_turn_off()
        settle()
        self.assertIsNone(self.feed.card)
        self.assertEqual(self.fake_browser.closed, 1)

    def test_quit(self) -> None:
        engine, stages = self.running()
        self.c.agent.shutdown()
        self.assertTrue(wait_for(lambda: engine.killed, timeout=5))
        self.assertEqual(self.c.agent.last_end.outcome, "stopped")
        self.assertGreaterEqual(self.fake_browser.shut, 1)
        stages.go(1)

    def test_a_late_line_after_the_stop_is_dropped(self) -> None:
        stages = Stages()
        answer: dict[str, Any] = {}

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            stages.reach(1)
            try:
                answer["late"] = engine.call("say", text="A late line.")
            except Exception:  # noqa: BLE001 - it is gone, as it should be
                answer["late"] = None

        self.start(script)
        self.reached(stages, 1)
        self.c.agent.stop("test")
        stages.go(1)
        self.ended()
        settle()
        self.assertNotIn("A late line.", self.said())

    def test_no_global_esc_is_held_for_a_task(self) -> None:
        import inspect

        keys = FakeStopKeys()
        engine = FakeEngine(whole)
        self.make_agent(plan_stream(TASK_PLAN), engine)
        self.desktop.stop_keys = keys
        self.type_it(TYPED)
        self.ended()
        settle()
        self.assertEqual((keys.arms, keys.disarms), (0, 0))
        for module in (agent_ui, agent_feed, ar, web):
            self.assertNotIn("stopkeys", inspect.getsource(module).replace("``stopkeys``", ""), module.__name__)
        self.assertNotIn("stop_keys", inspect.getsource(agent_feed))


class BarTests(FeedApp):
    def test_it_takes_typing_and_says_so(self) -> None:
        stages = Stages()

        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("update_plan", steps=STEPS)
            stages.reach(1)
            engine.end_turn("Working.")
            said = engine.wait_message()
            engine.call("finish", summary=said, outcome="done")
            engine.end_turn()

        self.start(script)
        self.reached(stages, 1)
        bar = self.c.ask.bar
        self.assertIs(bar, self.desktop.bar())
        self.assertFalse(bar.is_running())                            # not read only, as a plain Ask's bar is
        self.assertFalse(bar.input.isReadOnly())
        self.type_it("use the cheaper ones too")
        self.assertEqual(bar.text(), "")
        self.assertEqual(bar.status(), "Passing that on, sir.")
        self.assertEqual(bar.tone(), hud.TONE_DONE)
        stages.go(1)
        self.assertEqual(self.ended().summary, "The owner says: use the cheaper ones too")
        self.assertNotIn("Passing that on, sir.", self.said())         # shown, not said

    def test_a_plain_ask_s_bar_stays_read_only_while_it_runs(self) -> None:
        self.make_agent(plan_stream({"say": "It's at two.", "question": "", "lines": []}))
        release = threading.Event()
        planner = self.c.ask.planner
        real = planner.plan
        with mock.patch.object(planner, "plan", side_effect=lambda *a, **k: (release.wait(5), real(*a, **k))[1]):
            self.c.ask.bar.set_text("when is the sync?")
            self.c.ask.bar.submit()
            settle()
            self.assertTrue(self.c.ask.bar.is_running())
            self.assertTrue(self.c.ask.bar.input.isReadOnly())
            release.set()
            self.assertTrue(wait_for(lambda: not self.c.ask.busy, timeout=5))
        self.assertFalse(self.c.ask.bar.is_running())
        self.assertFalse(self.c.agent.running)

    def test_a_click_started_run_can_t_take_the_bar_from_a_task(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.reached(stages, 1)
        self.assertEqual(self.c._ask_blocked(), ui.TASK_RUNNING_TEXT)
        stages.go(1)
        stages.go(2)
        self.ended()
        settle()
        self.assertEqual(self.c._ask_blocked(), "")

    def test_with_no_task_the_bar_s_esc_is_as_it_was(self) -> None:
        self.make_agent()
        dismissed: list[bool] = []
        self.desktop.bar().statusDismissed.connect(lambda: dismissed.append(True))
        self.desktop.bar()._on_escape()
        self.assertEqual(dismissed, [True])


class ConsentTests(FeedApp):
    ROW = desktop_view.CONSENT_TASK_ROW.format(engine="DuckDuckGo")

    def test_every_line_of_the_row_shows_and_it_says_what_a_task_may_read(self) -> None:
        for words in ("calendar", "mail", "contacts", "notes", "allowed folders", "waits for your click"):
            self.assertIn(words, self.ROW)
        self.make_agent(desktop_consent=2)
        self.c._show_desktop_consent(desktop_ui.ENTRY_REQUEST)
        settle()
        card = self.c._desktop_consent_card
        label = card._task_row.label
        self.assertEqual(label.text(), self.ROW)
        self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        self.assertTrue(card.rect().contains(card.panel.geometry()))
        self.assertTrue(card.panel.rect().contains(label.mapTo(card.panel, label.rect().bottomRight())))

    def card(self) -> desktop_view.ConsentCard:
        self.assertIsNotNone(self.c._desktop_consent_card, "the card was made")
        return self.c._desktop_consent_card

    def stored(self) -> Any:
        return AssistantPrefs(self.harness.config.data_dir / PREFS_FILE)

    def asked_in_the_window(self, version: int) -> FakeEngine:
        engine = FakeEngine(whole)
        self.make_agent(plan_stream(TASK_PLAN), plan_stream(TASK_PLAN), engine, desktop_consent=version)
        self.type_it(TYPED)
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, timeout=10))
        settle()
        return engine

    def test_v3_a_task_starts(self) -> None:
        self.start(whole, desktop_consent=3)
        self.assertEqual(self.ended().outcome, "done")

    def test_v2_the_updated_card_at_the_first_task_request(self) -> None:
        engine = self.asked_in_the_window(2)
        self.assertFalse(self.c.agent.running)
        self.assertTrue(self.card().is_open())
        self.assertEqual(self.card().new_rows(), [self.ROW])          # only its row is new
        self.assertIn(self.ROW, self.card().texts())
        self.assertEqual(self.c.ask.bar.text(), TYPED)                # the words wait for Enter
        self.assertEqual(self.c.ask.bar.status(), "Tasks need your OK on the updated card first, sir - nothing was started.")
        self.assertEqual(self.desktop.consent_version(), 2)           # nothing was stored
        self.assertTrue(self.desktop.consent_current())               # what v2 allowed stays allowed
        self.card()._on_turn_on()
        settle()
        self.assertEqual(self.desktop.consent_version(), 3)
        self.assertEqual(self.stored().desktop_consent_version, 3)
        self.assertFalse(self.card().is_open())
        self.c.ask.bar.submit()                                       # Enter: the words that waited
        self.assertTrue(wait_for(lambda: self.c.agent.running or self.c.agent.last_end is not None, timeout=10))
        self.assertEqual(self.ended().outcome, "done")
        self.assertIsNone(engine.failure)

    def test_v1_all_the_new_rows_marked_and_what_it_allowed_stays(self) -> None:
        self.asked_in_the_window(1)
        self.assertTrue(self.card().is_open())
        new = self.card().new_rows()
        self.assertEqual(len(new), 3)
        self.assertEqual(new[-1], self.ROW)
        self.assertTrue(self.desktop.consent_given())
        self.assertFalse(self.desktop.consent_current())
        self.assertFalse(self.desktop.consent_for_tasks())

    def test_v0_tasks_are_not_offered_and_the_first_card_has_their_row_unmarked(self) -> None:
        self.make_agent(desktop_consent=False)
        self.assertFalse(self.c.agent.offered())
        self.c._show_desktop_consent(desktop_ui.ENTRY_DOCK)
        settle()
        self.assertIn(self.ROW, self.card().texts())
        self.assertEqual(self.card().new_rows(), [])
        self.card()._on_turn_on()
        settle()
        self.assertEqual(self.stored().desktop_consent_version, 3)

    def test_on_the_hud_it_waits_for_the_window(self) -> None:
        engine = FakeEngine(whole)
        self.make_agent(plan_stream(TASK_PLAN), engine, desktop_consent=2)
        self.enter()
        self.type_it(TYPED)
        self.assertTrue(wait_for(lambda: not self.c.ask.busy, timeout=10))
        settle()
        self.assertFalse(self.c.agent.running)
        self.assertEqual(self.c.ask.bar.status(),
                         "Tasks need your OK on the updated card first, sir - Bring Jarvis back to read it.")
        self.back()
        settle()
        self.assertTrue(self.card().is_open())
        self.assertEqual(self.card().new_rows(), [self.ROW])

    def test_turn_off_clears_it(self) -> None:
        self.make_agent(desktop_consent=3)
        self.assertTrue(self.desktop.consent_for_tasks())
        self.c._on_desktop_turn_off()
        self.assertFalse(self.desktop.consent_for_tasks())
        self.assertEqual(self.stored().desktop_consent_version, 0)
        self.assertFalse(self.stored().desktop_turned_on)

    def test_the_card_names_the_engine_it_goes_to(self) -> None:
        self.make_agent(desktop_consent=False, agent='search_engine = "bing"\n')
        self.c._show_desktop_consent(desktop_ui.ENTRY_DOCK)
        settle()
        self.assertIn(desktop_view.CONSENT_TASK_ROW.format(engine="Bing"), self.card().texts())

    def test_settings_says_so(self) -> None:
        self.make_agent(desktop_consent=2)
        rows = dict(settings_view.desktop_rows(self.harness.config, turned_on=True, current=True,
                                               tasks=not self.c._task_update_due()))
        self.assertEqual(rows["Your OK"], settings_view.TURNED_ON_NO_TASKS)

    def test_the_row_is_plain_about_what_goes_where(self) -> None:
        for words in ("work out the steps", "window you can watch", "go straight to DuckDuckGo", "waits for your click"):
            self.assertIn(words, self.ROW)
        self.assertNotRegex(self.ROW, r"\b(?:he|him|his)\b")


class FeatureOffConsentTests(AgentApp):
    """With [agent] off: the card as it was, version 2 stored, no feed."""

    def test_as_it_was(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.make("", desktop_consent=False, desktop_backend=lambda: desktop_disk(tmp.name))
        self.assertIsNone(self.c.agent)
        self.assertIsNone(self.c.task_feed)
        self.c._show_desktop_consent(desktop_ui.ENTRY_DOCK)
        settle()
        card = self.c._desktop_consent_card
        self.assertEqual(len([text for text in card.texts() if "Give me a task" in text]), 0)
        self.assertIsNone(card._task_row)
        self.assertTrue(self.desktop.turn_on())
        self.assertEqual(self.desktop.consent_version(), desktop_ui.CONSENT_VERSION)
        self.assertFalse(self.desktop.needs_task_update())
        self.assertIsNone(self.reading.task_chip)
        self.assertIsNone(self.desktop.overlay._feed)


class HomeChipTests(FeedApp):
    def test_back_in_the_window_it_carries_on_and_the_hud_key_brings_it_back(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.reached(stages, 1)
        panel = self.feed.panel()
        self.assertTrue(panel.isVisible())
        self.assertFalse(self.fake_browser.away)
        self.back()
        self.assertFalse(panel.isVisible())                           # the panel and the watch window hide
        self.assertTrue(self.fake_browser.away)
        self.assertTrue(self.c.agent.running)                         # the task carries on
        self.assertEqual(self.reading.task_chip_text(), "Working - 1 of 3")
        self.assertFalse(self.reading.task_stop.isHidden())
        stages.go(1)
        self.reached(stages, 2)
        self.assertEqual(self.reading.task_chip_text(), "Working - 2 of 3")   # it goes on moving
        self.c.handle_activation(dict(HUD_KEY))
        settle()
        self.assertTrue(self.desktop.is_hud())
        self.assertTrue(panel.isVisible())
        self.assertFalse(self.fake_browser.away)
        stages.go(2)
        self.ended()

    def test_its_click_goes_to_live_and_its_stop_stops(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.reached(stages, 1)
        self.back()
        self.click(self.reading.task_chip)
        self.assertEqual(self.reading.current_tab(), hud.TAB_LIVE)
        self.click(self.reading.task_stop)
        self.assertEqual(self.ended().outcome, "stopped")
        stages.go(1)
        stages.go(2)
        settle()
        self.assertEqual(self.reading.task_chip_text(), "Task stopped")
        self.assertTrue(self.reading.task_stop.isHidden())

    def test_a_task_that_ends_in_the_window_waits_on_the_chip(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.reached(stages, 1)
        self.back()
        stages.go(1)
        stages.go(2)
        self.ended()
        settle()
        self.assertEqual(self.reading.task_chip_text(), "Task done")
        self.assertFalse(self.feed.card.isVisible())                  # data until the HUD shows
        self.click(self.reading.task_chip)
        self.assertTrue(self.desktop.is_hud())
        self.assertTrue(self.feed.card.isVisible())
        self.assertTrue(self.feed.panel().isVisible())
        self.press("done")
        self.assertEqual(self.reading.task_chip_text(), "")

    def test_a_card_waiting_counts_in_need_your_ok(self) -> None:
        self.start(QuestionCardTests.script())
        self.asked_card()
        self.assertEqual(self.desktop.waiting_count(), 1)
        pending = self.reading.pending_count()
        self.assertIn(hud_overlay.needs_ok_text(pending + 1), self.hud().chip_texts())   # counted, as a desktop card is
        self.assertIn(self.reading.look()[0], ("needs", "speaking"))
        self.back()
        settle()
        self.assertEqual(self.c._desktop_waiting.count(), 1)           # Approvals' reminder: Show me
        self.assertEqual(self.reading._desktop_waiting, 1)             # Home's chip and the Dock's count
        self.c._on_desktop_show_me()                                  # Show me: onto the HUD, where the card is
        settle()
        self.assertTrue(self.desktop.is_hud())
        self.assertTrue(self.feed.card.isVisible())
        self.press("choice:0")
        self.assertEqual(self.desktop.waiting_count(), 0)
        self.assertEqual(self.reading._desktop_waiting, 0)
        self.assertNotIn(hud_overlay.needs_ok_text(pending + 1), self.hud().chip_texts())
        self.ended()


class VoiceTests(FeedApp):
    def test_the_whole_report_on_the_card_and_only_its_start_said(self) -> None:
        def script(engine: FakeEngine) -> None:
            engine.wait_message()
            engine.call("say", text="Reading three reviews now.")
            engine.call("finish", summary=LONG[:1500], outcome="done")
            engine.end_turn()

        self.start(script)
        self.ended()
        settle()
        self.assertEqual(self.feed.card.task().body, LONG[:1500])
        said = self.said()
        self.assertIn("Reading three reviews now.", said)             # say lines as ever
        spoken = next(words for words in said if words.startswith("Compared all three"))
        self.assertLessEqual(len(spoken), 203)
        self.assertEqual(spoken, spoken_summary(LONG[:1500]))
        self.assertIn(LONG[:1500], self.talk())                       # all of it in the conversation

    def test_spoken_summary(self) -> None:
        self.assertEqual(spoken_summary(""), "")
        self.assertEqual(spoken_summary("Yes."), "Yes.")
        self.assertEqual(spoken_summary("One. Two. Three."), "One. Two.")
        first = "A" * 150 + "."
        self.assertEqual(spoken_summary(f"{first} {'B' * 100}."), first)
        long_one = " ".join(["word"] * 100) + "."
        cut = spoken_summary(long_one)
        self.assertLessEqual(len(cut), 203)
        self.assertTrue(cut.endswith("..."))

    def test_a_stopped_task_says_nothing_more(self) -> None:
        stages = Stages()
        self.start(lambda engine: whole(engine, stages))
        self.reached(stages, 1)
        self.c.agent.stop("test")
        self.ended()
        settle()
        self.assertNotIn("Stopped, sir. Files already written stay in the workspace.", self.said())
        stages.go(1)
        stages.go(2)


class EngineStartTests(FeedApp):
    def test_chromium_starts_as_a_task_starts_before_the_switch(self) -> None:
        order: list[str] = []
        engine = FakeEngine(whole)

        def factory(config: Any, planner: Any, **kwargs: Any) -> agent_ui.AgentController:
            return agent_ui.AgentController(config, planner, timing=FAST, **kwargs)

        workspace = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(workspace.cleanup)
        self.make(f"\n[agent]\nenabled = true\nworkspace = '{workspace.name}'\n", runs=(plan_stream(TASK_PLAN), engine),
                  agent_factory=factory, desktop_consent=3)
        self.c.ask.start()
        self.assertTrue(wait_for(lambda: self.c.ask.ready is not None))
        settle()
        real_enter = self.desktop.enter_hud
        with mock.patch.object(snapshot_engine, "warm_up", side_effect=lambda: order.append("warm") or 0.0) as warm, \
                mock.patch.object(self.desktop, "enter_hud", side_effect=lambda: order.append("switch") or real_enter()):
            self.c.ask.bar.set_text(TYPED)
            self.c.ask.bar.submit()
            self.assertTrue(wait_for(lambda: order, timeout=10))
            settle()
        warm.assert_called_once()
        self.assertEqual(order[:2], ["warm", "switch"])
        self.assertTrue(wait_for(lambda: self.c.agent.last_end is not None, timeout=15))

    def test_a_task_with_a_browser_given_starts_no_engine(self) -> None:
        with mock.patch.object(snapshot_engine, "warm_up") as warm:
            self.start(whole)
            self.ended()
        warm.assert_not_called()

    def test_on_a_test_platform_it_does_nothing(self) -> None:
        self.assertEqual(snapshot_engine.warm_up(), 0.0)


class CopyTests(unittest.TestCase):
    def test_no_he_or_him_for_the_owner(self) -> None:
        import re

        from briefing_reader.ask import cli

        prompt = cli.AGENT_PROMPT_FILE.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b(?:he|him|his|himself)\b", prompt, re.IGNORECASE))
        shown = [desktop_view.CONSENT_TASK_ROW, agent_feed.QUESTION_NOTE, agent_feed.CLICK_ONLY_NOTE, agent_feed.NOT_SHOWN_TEXT,
                 *agent_feed.EXPIRED_WORDS.values(), desktop_ui.TASK_UPDATE_TEXT, desktop_ui.TASK_UPDATE_HUD_TEXT,
                 settings_view.TURNED_ON_NO_TASKS]
        for words in shown:
            self.assertIsNone(re.search(r"\b(?:he|him|his)\b", words), words)

    def test_the_prompt_teaches_what_the_real_run_showed(self) -> None:
        from briefing_reader.ask import cli

        prompt = cli.AGENT_PROMPT_FILE.read_text(encoding="utf-8")
        for words in ("when a step starts and again when it ends", "never saved up in batches", "not narration of each step",
                      "call fetch_text", "lead with the answer in its first sentence", "only that is spoken aloud"):
            self.assertIn(words, " ".join(prompt.split()), words)

    def test_the_window_s_words(self) -> None:
        from briefing_reader.agent import browser

        self.assertEqual(browser.REFUSED_WORDS.format(host="www.example.com"), "www.example.com keeps automated browsers out")
        self.assertEqual(browser.REFUSAL_STATUSES, frozenset({401, 403, 429}))


if __name__ == "__main__":
    unittest.main()
