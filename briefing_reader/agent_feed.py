"""A task's feed, what the owner sees of it (SPEC 8; part A3): the HUD's plan panel, its cards, the result card, Stop
and Home's chip. Qt, GUI thread; made by the app only while [agent] is on (off: nothing of this exists or is imported).

    TaskFeed(agent, desktop, reading, words=, reveal_live=)
        A task moves to the HUD as a desktop request does. While it runs: the panel under the bar, with its header, its
        rows and Jarvis's own activity line, moving even when the model does not update its plan; Stop on it, and Show
        when the owner hid the watch window. If the owner brings the Jarvis window back, the task carries on: the panel
        and the watch window hide, Home shows "Working - 3 of 6" (click: Live) with a Stop, and the HUD key brings them
        back.
    spoken_summary(summary)  the first sentence or two of a report, at most about 200 characters: only that is said
    aloud activity words come from agent.tools.doing_words: hosts only, on screen only, never logged, never spoken

The cards (hud_overlay.TaskCard, one at a time, amber while it waits, counted in "N need your OK"):

* ask_owner's: the question and its choices as buttons; words typed or dictated in the bar answer it too (the agent's
  gate, agent_ui.take_request). Its note says, in Jarvis's own words, that he never needs a password or a code.
* the firewall's: the exact words or address, Don't and Allow. Keep going, and its plan-limit twin. These are click
only:
  they take no keyboard, the bar's words never reach them (agent.runner), a click passes the app's guards and a card
  that has only just appeared does not take one yet.
* one that times out goes, with words saying so.
* a card of file steps (propose_files) is the desktop's own, with its own guards, journal and Undo: here the panel only
  says WAITING, in amber, until its end. It counts in "N need your OK" as it always does.

The result card: the outcome, the steps and the time in its kicker, the whole report (scrolling), the outputs with Show
(in the sealed watch window: agent_ui.show_output) and Folder (the guarded backend's way of showing a file in Explorer:
desktop_ui.reveal_task_file), and Done. A page the agent wrote could carry private data out through the owner's real
browser or a default app, so nothing here ever hands one to them: for a page, Folder opens the folder with nothing
selected (desktop_ui.reveal_task_folder) and its row says plainly to use Show. The window stays while the card stays.

Global Esc is never held for a task: a task can run 20 minutes while the owner works in other apps, and Esc is theirs.
Esc stops a task only while the HUD's bar has the keyboard.

Logs: kinds and counts only, never a word of a plan, a question or a report.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, QTimer

from . import holo, hud, hud_overlay
from . import orb as orb_mod
from .agent import firewall as agent_firewall
from .agent import runner as agent_runner
from .agent.tools import OUTCOME_DONE, OUTCOME_FAILED, OUTCOME_PARTIAL, STEP_DOING, STEP_DONE, STEP_FAILED
from .hud_overlay import CardButton, PanelContent, PlanRow, TaskContent, TaskRow
from .persona import sentences, with_address

logger = logging.getLogger(__name__)

WORKING_WORD, WAITING_WORD = "WORKING", "WAITING"
END_WORDS = {OUTCOME_DONE: "DONE", OUTCOME_PARTIAL: "PARTLY DONE", OUTCOME_FAILED: "FAILED",
             agent_runner.OUTCOME_STOPPED: "STOPPED"}
END_TONES = {OUTCOME_DONE: hud_overlay.TONE_DONE, OUTCOME_PARTIAL: hud_overlay.TONE_CYAN,
             OUTCOME_FAILED: hud_overlay.TONE_CORAL, agent_runner.OUTCOME_STOPPED: hud_overlay.TONE_CORAL}
CHIP_WORKING = "Working"
CHIP_COUNT = "Working - {step} of {steps}"
CHIP_ENDED = {OUTCOME_DONE: "Task done", OUTCOME_PARTIAL: "Task partly done", OUTCOME_FAILED: "Task failed",
              agent_runner.OUTCOME_STOPPED: "Task stopped"}
STARTING_ACTIVITY = "Working out the steps"
THINKING_ACTIVITY = "Thinking it over"
WAITING_ACTIVITY = "Waiting for you"
ENDED_ACTIVITY = "Finished"
STOPPED_ACTIVITY = "Stopped"
QUESTION_KICKER = "A question for you"
OK_KICKER = "Needs your OK"
QUESTION_NOTE = "Or type your answer in the bar. Jarvis never needs a password or a code."
QUESTION_FREE_NOTE = "Type your answer in the bar. Jarvis never needs a password or a code."
CLICK_ONLY_NOTE = "Only a click answers this."
ALLOW_TEXT, DONT_TEXT = agent_firewall.WEB_ALLOW, agent_firewall.WEB_DONT
DONE_TEXT = "Done"
DONE_KEY = "done"
EXPIRED_WORDS = {agent_runner.Q_TASK: "No answer came, so that question went, {sir}.",
                 agent_runner.Q_KEEP_GOING: "No answer came, so the task stops here, {sir}.",
                 agent_runner.Q_RESUME: "No answer came, so the task stops here, {sir}.",
                 agent_runner.Q_WEB: "No answer came, so Jarvis left that out, {sir}. The task goes on.",
                 agent_runner.Q_CARD: "No answer came, so nothing was changed, {sir}. The task goes on."}
NOT_SHOWN_TEXT = "That file couldn't be shown in the window, {sir}."
SPOKEN_CAP = 200
QUIET_MS = 4000                 # no tool at work for this long: "Thinking it over"
TICK_MS = 1000                  # the header's clock
CLICK_ARM_S = 0.6               # a card that has only just appeared takes no click: it may have come under the pointer
CHOICE_KEY = "choice:"
GUARD_ID = "dsk:task"           # the click guards' id for a task's cards (desktop_ui's namespace)
SHOW_KEY, FOLDER_KEY = "show:", "folder:"
PAGE_TYPES = (".html", ".htm", ".svg")   # an output a double click would open in the owner's real browser
PAGE_ROW_NOTE = "A page Jarvis wrote: open it with Show, not from the folder."


def spoken_summary(summary: str, cap: int = SPOKEN_CAP) -> str:
    """What is said of a final report: its first sentence, and the second when both fit in about ``cap`` characters; a
    longer first sentence is cut at a word. Everything of it is on the card."""
    parts = sentences(summary)
    if not parts:
        return ""
    words = parts[0]
    if len(words) > cap:
        return words[:cap].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."
    if len(parts) > 1 and len(words) + 1 + len(parts[1]) <= cap:
        words = f"{words} {parts[1]}"
    return words


def _a_page(name: str) -> bool:
    return name.casefold().endswith(PAGE_TYPES)


def plan_counts(steps: Any) -> tuple[int, int]:
    """(the step it is on, how many there are): the one at work, else how many have ended."""
    ended = sum(1 for step in steps if step.status in (STEP_DONE, STEP_FAILED))
    doing = any(step.status == STEP_DOING for step in steps)
    return min(len(steps), ended + int(doing)), len(steps)


def clock_text(seconds: float) -> str:
    """"2:14", "1:02:03": tabular minutes and seconds of work."""
    whole = max(0, int(seconds))
    minutes, second = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02d}:{second:02d}" if hours else f"{minute}:{second:02d}"


def head_text(word: str, steps: Any, seconds: float) -> str:
    """The panel's header: `WORKING - 3 OF 6 - 2:14`."""
    step, count = plan_counts(steps)
    return f"{word} - {step} OF {count} - {clock_text(seconds)}" if count else f"{word} - {clock_text(seconds)}"


def kicker_text(end: Any, steps: Any) -> str:
    """The result card's kicker: `DONE - 6 STEPS - 4:12`; a task that did not get to its end says how far it got."""
    word = END_WORDS.get(end.outcome, END_WORDS[OUTCOME_PARTIAL])
    step, count = plan_counts(steps)
    done = sum(1 for item in steps if item.status == STEP_DONE)
    if count and end.outcome != OUTCOME_DONE:
        return f"{word} - {done} OF {count} - {clock_text(end.worked_s)}"
    number = count or end.tools
    return f"{word} - {number} STEP{'S' if number != 1 else ''} - {clock_text(end.worked_s)}"


class TaskFeed(QObject):
    """See the module docs."""

    def __init__(self, agent: Any, desktop: Any, reading: Any, *, words: Callable[..., None],
                 reveal_live: Callable[[str], None], clock: Callable[[], float] = time.monotonic,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.agent = agent
        self.desktop = desktop
        self.reading = reading
        self.overlay = desktop.overlay
        self._words = words
        self._reveal_live = reveal_live
        self._clock = clock
        self._address = str(getattr(getattr(agent.config, "assistant", None), "address", "sir"))
        self._running = False
        self._ended: Any = None             # the AgentEnd the result card shows, until Done
        self._steps: tuple[Any, ...] = ()
        self._activity = ""
        self._waiting = False               # a card waits for the owner
        self._question: Any = None
        self.card: hud_overlay.TaskCard | None = None   # the one card: a wait's, or the result
        self._card_at = 0.0
        self._placing = False
        self._shut = False
        self._wired: Any = None
        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._on_tick)
        self._quiet = QTimer(self)
        self._quiet.setSingleShot(True)
        self._quiet.timeout.connect(self._on_quiet)
        agent.hold_window = True            # the window stays while the result card stays
        agent.taskStarted.connect(self._on_started)
        agent.planChanged.connect(self._on_plan)
        agent.activityChanged.connect(self._on_activity)
        agent.stepEnded.connect(self._on_step_ended)
        agent.questionAsked.connect(self._on_question)
        agent.questionGone.connect(self._on_question_gone)
        agent.questionExpired.connect(self._on_question_expired)
        agent.taskEnded.connect(self._on_ended)
        agent.windowHidden.connect(self._on_window_hidden)
        agent.windowPlaced.connect(self._place)
        desktop.hudChanged.connect(self._on_hud_changed)
        self.overlay.hud.shapeChanged.connect(self._on_hud_shape)
        reading.taskChipClicked.connect(self._on_chip)
        reading.taskStopClicked.connect(self._on_stop)
        desktop.bar().set_escape_hook(self._on_escape)

    # ---- what the tests and the harness look at ---------------------------------------------------------

    def panel(self) -> hud_overlay.PlanPanel | None:
        feed = self.overlay._feed                           # noqa: SLF001 - its own windows
        return feed[0] if feed is not None else None

    def buttons(self) -> hud_overlay.PanelButtons | None:
        feed = self.overlay._feed                           # noqa: SLF001 - its own windows
        return feed[1] if feed is not None else None

    def activity(self) -> str:
        return self._activity

    def active(self) -> bool:
        """A task runs, or its result card has not been dismissed yet."""
        return self._running or self._ended is not None

    def waits(self) -> int:
        return 1 if self._waiting else 0

    # ---- a task starts --------------------------------------------------------------------------------------

    def _on_started(self, _run_id: str) -> None:
        if self._shut:
            return
        self._clear()
        self._running = True
        self._activity = STARTING_ACTIVITY
        panel, buttons = self.overlay.feed_panel()
        if self._wired is not panel:                        # once per window
            self._wired = panel
            panel.shapeChanged.connect(self._place)
            buttons.stopClicked.connect(self._on_stop)
            buttons.showClicked.connect(self._on_show_window)
        self._render()
        self.overlay.set_feed_shown(True)
        self._tick.start()
        self.reading.set_task_look(orb_mod.THINKING)
        self.desktop.enter_hud()                            # no change when it can't, or when it is on the HUD already
        self.agent.set_window_away(not self.desktop.is_hud())
        self._sync_chip()
        self._place()
        logger.info("Task feed: on the %s", "HUD" if self.desktop.is_hud() else "window's chip")

    def _clear(self) -> None:
        """Nothing of the last task is left: its card, its panel, its window, its chip."""
        self._drop_card()
        self._ended = None
        self._running = False
        self._steps = ()
        self._waiting = False
        self._question = None
        self._tick.stop()
        self._quiet.stop()
        self.overlay.set_feed_shown(False)
        self.desktop.set_task_waiting(0)
        self.reading.set_task_look("")
        self.reading.set_task_chip("")

    # ---- its plan and what it does --------------------------------------------------------------------------

    def _on_plan(self, steps: Any) -> None:
        if self._running:
            self._steps = tuple(steps)
            self._render()
            self._sync_chip()

    def _on_activity(self, words: str) -> None:
        if not self._running or not words:
            return
        self._quiet.stop()
        if not self._waiting:
            self._activity = words
            self._render()

    def _on_step_ended(self) -> None:
        if self._running:
            self._quiet.start(QUIET_MS)

    def _on_quiet(self) -> None:
        if not self._running or self._waiting:
            return
        if self.agent.tools_running():
            self._quiet.start(QUIET_MS)
            return
        self._activity = THINKING_ACTIVITY
        self._render()

    def _on_tick(self) -> None:
        panel = self.panel()
        if panel is not None and self._running:
            panel.set_head(self._head())

    def _head(self) -> str:
        return head_text(WAITING_WORD if self._waiting else WORKING_WORD, self._steps, self.agent.worked_s())

    def _rows(self) -> tuple[PlanRow, ...]:
        return tuple(PlanRow(step.text, step.status) for step in self._steps)

    def _render(self) -> None:
        panel, buttons = self.overlay.feed_panel()
        if self._ended is not None:
            tone = END_TONES.get(self._ended.outcome, hud_overlay.TONE_CYAN)
            head = kicker_text(self._ended, self._steps)
        else:
            tone = hud_overlay.TONE_AMBER if self._waiting else hud_overlay.TONE_CYAN
            head = self._head()
        buttons.set_buttons(self._running, self.agent.window_hidden(), tone)
        panel.set_panel(PanelContent(head, tone, self._rows(), self._activity))
        self._place()
        if self.overlay.is_shown() and (panel.isHidden() or buttons.any_shown() == buttons.isHidden()):
            self.overlay.set_feed_shown(True)                       # its buttons came or went

    def _place(self) -> None:
        """Clear of the bar, the chips and the watch window, kept on screen (hud_overlay.HudOverlay.place_feed)."""
        if self._placing or self._shut or self.overlay._feed is None:   # noqa: SLF001 - its own windows
            return
        self._placing = True
        try:
            self.overlay.place_feed(self.agent.window_rect(), self.card)
            others = getattr(self.desktop, "place_cards_again", None)
            if others is not None:
                others()
        finally:
            self._placing = False

    def _on_hud_shape(self) -> None:
        if self.active():
            self.agent.keep_window_clear()
            self._place()

    # ---- the HUD and the window --------------------------------------------------------------------------------

    def _on_hud_changed(self, entered: bool) -> None:
        """On the HUD the panel and the watch window show; back in the window they hide (the HUD's windows go with the
        HUD), and Home's chip says it goes on. What is on its way, and the cards, come back with the HUD."""
        if self._shut or not self.active():
            return
        self.agent.set_window_away(not entered)
        self._sync_chip()
        if entered:
            self._render()

    def _sync_chip(self) -> None:
        """Home's chip while the HUD is away: what the task is at, and its Stop; after its end, how it ended."""
        if self._ended is not None:
            text = CHIP_ENDED.get(self._ended.outcome, CHIP_ENDED[OUTCOME_PARTIAL])
        elif self._running:
            step, count = plan_counts(self._steps)
            text = CHIP_COUNT.format(step=step, steps=count) if count else CHIP_WORKING
        else:
            text = ""
        self.reading.set_task_chip(text, can_stop=self._running)

    def _on_chip(self) -> None:
        """Working: Live, with this task in view; ended: onto the HUD, where its result card waits."""
        if self._running:
            task = self.agent.live_task_id()
            if task:
                self._reveal_live(f"live:{task}")
        elif self._ended is not None:
            self.desktop.enter_hud()

    # ---- Stop, Hide and Show ----------------------------------------------------------------------------------------

    def _on_stop(self) -> None:
        if self._running:
            logger.info("Task feed: Stop was clicked")
            self.agent.stop("stop")

    def _on_escape(self) -> None:
        """Esc in the HUD's empty bar while it has the keyboard. Never a global key: see the module docs."""
        if self._running:
            logger.info("Task feed: Esc in the bar")
            self.agent.stop("esc")

    def _on_show_window(self) -> None:
        self.agent.set_window_hidden(False)
        self._render()

    def _on_window_hidden(self, _hidden: bool) -> None:
        if self.active():
            self._render()

    # ---- the cards
    # ------------------------------------------------------------------------------------------------------

    def _new_card(self, content: TaskContent) -> hud_overlay.TaskCard:
        self._drop_card()
        card = self.overlay.new_task_card()
        card.buttonClicked.connect(self._on_card_click)
        card.set_task(content)
        self.card = card
        self._card_at = self._clock()
        self._place()
        self.overlay.adopt_card(card)
        self._place()
        return card

    def _drop_card(self) -> None:
        card, self.card = self.card, None
        if card is not None and shiboken6.isValid(card):
            self.overlay.remove_card(card)

    def _on_question(self, question: Any) -> None:
        if not self._running or self._shut:
            return
        self._question = question
        self._waiting = True
        self._quiet.stop()
        self._activity = WAITING_ACTIVITY
        self.reading.set_task_look(orb_mod.NEEDS)
        if question.kind != agent_runner.Q_CARD:           # its card is the desktop's, which counts itself
            self.desktop.set_task_waiting(1)
            self._new_card(self._question_content(question))
        self._render()
        logger.info("Task feed: a card waits (%s)", question.kind)

    @staticmethod
    def _question_content(question: Any) -> TaskContent:
        amber = hud_overlay.TONE_AMBER
        if question.kind == agent_runner.Q_TASK:
            buttons = tuple(CardButton(f"{CHOICE_KEY}{index}", choice, holo.AMBER_GHOST)
                            for index, choice in enumerate(question.choices))
            return TaskContent(QUESTION_KICKER, question.text, amber, buttons,
                               QUESTION_NOTE if buttons else QUESTION_FREE_NOTE)
        if question.kind == agent_runner.Q_WEB:
            return TaskContent(OK_KICKER, question.text, amber,
                               (CardButton(f"{CHOICE_KEY}1", DONT_TEXT, holo.AMBER_GHOST),
                                CardButton(f"{CHOICE_KEY}0", ALLOW_TEXT, holo.AMBER_COMMIT)), CLICK_ONLY_NOTE)
        go, stop = question.choices
        return TaskContent(OK_KICKER, question.text, amber, (CardButton(f"{CHOICE_KEY}1", stop, holo.AMBER_GHOST),
                                                             CardButton(f"{CHOICE_KEY}0", go, holo.AMBER_COMMIT)),
                           CLICK_ONLY_NOTE)

    def _on_question_gone(self, question: Any) -> None:
        """Answered, or it moved on: the card goes, and the panel is cyan again."""
        current = self._question
        if current is None or question is None or current.id != question.id:
            return
        self._settle_question()

    def _settle_question(self) -> None:
        self._question = None
        self._waiting = False
        self.desktop.set_task_waiting(0)
        if self._ended is None:
            self._drop_card()
        if self._running:
            self.reading.set_task_look(orb_mod.THINKING)
            self._activity = THINKING_ACTIVITY
            self._render()

    def _on_question_expired(self, question: Any) -> None:
        """A card that timed out goes, with words saying so. Not said aloud: the owner may be anywhere."""
        words = EXPIRED_WORDS.get(getattr(question, "kind", ""), EXPIRED_WORDS[agent_runner.Q_TASK])
        logger.info("Task feed: a card timed out (%s)", getattr(question, "kind", ""))
        self._words(with_address(words, self._address), hud.TONE_IDLE, speak=False)

    def _on_card_click(self, key: str) -> None:
        if key.startswith(CHOICE_KEY):
            self._on_choice(int(key[len(CHOICE_KEY):]))
        elif key == DONE_KEY:
            self._dismiss()
        elif key.startswith(SHOW_KEY):
            self._show_output(key[len(SHOW_KEY):])
        elif key.startswith(FOLDER_KEY):
            self._show_folder(key[len(FOLDER_KEY):])

    def _guarded(self) -> bool:
        """A click to ignore: the card has only just appeared, or the app's click guards say so (too soon after another
        decision, or the cards are locked): fail-closed."""
        if self._clock() - self._card_at < CLICK_ARM_S:
            return True
        guard = getattr(self.desktop, "_click_guard", None)
        try:
            return bool(guard(GUARD_ID)) if guard is not None else False
        except Exception as exc:  # noqa: BLE001 - unknown: ignored
            logger.warning("Task feed: the click guard could not be asked (%s); ignored", type(exc).__name__)
            return True

    def _on_choice(self, index: int) -> None:
        """A click on a choice of the card that waits: its own words are the answer. Only the card's own words can be
        "Allow" or "Keep going": what is typed never reaches here."""
        question = self._question
        if question is None or self._guarded():
            return
        if question.kind == agent_runner.Q_WEB:
            choice = ALLOW_TEXT if index == 0 else DONT_TEXT
        elif 0 <= index < len(question.choices):
            choice = question.choices[index]
        else:
            return
        logger.info("Task feed: a card was answered by a click (%s)", question.kind)
        self.agent.answer(question.id, choice)
        self._settle_question()

    # ---- the end
    # ---------------------------------------------------------------------------------------------------------

    def _on_ended(self, end: Any) -> None:
        """Stamped: the panel says how it ended and takes no more clicks, the undecided card is withdrawn, the result
        card comes. After a Turn off nothing stays: the HUD can't show."""
        if self._shut or not self._running:
            return
        self._running = False
        self._tick.stop()
        self._quiet.stop()
        self._waiting = False
        self._question = None
        self.desktop.set_task_waiting(0)
        self.reading.set_task_look("")
        self._drop_card()
        if not self.desktop.consent_given():
            self._clear()
            return
        self._ended = end
        stopped = end.outcome == agent_runner.OUTCOME_STOPPED
        self._activity = STOPPED_ACTIVITY if stopped else ENDED_ACTIVITY
        self._steps = self._stamped(self._steps, end)
        self._sync_chip()
        self._new_card(self._result_content(end))
        self._render()
        logger.info("Task feed: the result card (%s, %d output(s))", end.outcome, len(self.card.task().rows))

    @staticmethod
    def _stamped(steps: tuple[Any, ...], end: Any) -> tuple[Any, ...]:
        """The plan as the task ended: all of it done when the report says so (a model need not have ticked its last
        steps); else a step still at work did not end well."""
        if end.outcome == OUTCOME_DONE:
            return tuple(step if step.status == STEP_FAILED else type(step)(step.text, STEP_DONE) for step in steps)
        return tuple(type(step)(step.text, STEP_FAILED) if step.status == STEP_DOING else step for step in steps)

    def _result_content(self, end: Any) -> TaskContent:
        rows = tuple(TaskRow(name, f"{SHOW_KEY}{name}", f"{FOLDER_KEY}{name}", PAGE_ROW_NOTE if _a_page(name) else "")
                     for name in self.agent.outputs_for(end))
        return TaskContent(kicker_text(end, self._steps), end.summary,
                           END_TONES.get(end.outcome, hud_overlay.TONE_CYAN), (CardButton(DONE_KEY, DONE_TEXT, holo.GHOST),),
                           rows=rows)

    def _show_output(self, name: str) -> None:
        """Show: in the sealed watch window, never the owner's browser or a default app."""
        logger.info("Task feed: an output is shown in the window")
        if not self.agent.show_output(name):
            self._words(with_address(NOT_SHOWN_TEXT, self._address), hud.TONE_ERROR, speak=False)
        self._render()

    def _show_folder(self, name: str) -> None:
        """Folder: its folder opens with it selected, through the guarded backend; the file itself is never opened. A
        page is not selected: Enter there would open it in the owner's real browser, with nothing sealed."""
        path = self.agent.output_path(name)
        if path:
            logger.info("Task feed: an output is shown in its folder")
            if _a_page(name):
                self.desktop.reveal_task_folder(path)
            else:
                self.desktop.reveal_task_file(path)

    def _dismiss(self) -> None:
        """Done: the card, the stamped panel, the window and the chip go."""
        logger.info("Task feed: the result card was put away")
        self._clear()
        self.agent.close_window()

    def new_request(self) -> None:
        """A desktop request starts: its cards come now, and a result left up goes, as a desktop card does."""
        if self._ended is not None and not self._running:
            self._dismiss()

    def consent_gone(self) -> None:
        """Turned off with a result card up: nothing stays."""
        if not self._running:
            self._clear()
            self.agent.close_window()

    def shutdown(self) -> None:
        """Jarvis closes: no timer, no card; the HUD's windows go with the HUD."""
        self._shut = True
        self._tick.stop()
        self._quiet.stop()
        card, self.card = self.card, None
        if card is not None and shiboken6.isValid(card):
            self.overlay.remove_card(card)


__all__ = ["CHIP_COUNT", "CHIP_ENDED", "CHIP_WORKING", "TaskFeed", "clock_text", "head_text", "kicker_text",
           "plan_counts", "spoken_summary"]
