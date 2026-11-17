"""The canvas (PLAN-canvas part 1): Home is the board's page with the orb in it. Offscreen; the web engine never starts
here - the board is tests.board_fakes' stand-in (a real BoardView whose page is a plain widget; tests/board_probe.py's
canvas cases run the real page in processes of their own).

What is checked: the two new message kinds and their checks (orb, canvas) and the page's go request with its fixed list
(an unknown word is refused); the view (the newest orb / canvas message waits for the page and never takes a frame's
room; goRequested; the canvas's ground); canvas mode on Home (no Dock, no header, the canvas page with the ask line,
the caption and the chips; ``[look] board = false`` and a factory that is not the canvas's keep Home as it was); the
page started with the window, not at the first Ask; the forms following the board's count and the states (the rings,
the badge while he speaks and a moment after, the corner while the board shows frames); the corners' message (the state
word, the count, which buttons can work); each button doing what its native counterpart does; the other screens
reachable, with Back and Esc; the keyboard in the ask line when the window opens and after an answer; the ask line and
the caption in the canvas's look, and back; open big, Ctrl+B and F6 on the canvas; the fallback (the native ring, the
Dock, the ask line, the read-only tiles); the window (maximised once, F11, never always on top).
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from unittest import mock

from briefing_reader import canvas_ui, canvas_view, dock, holo, home, hud, snapshot_engine, snapshots, ui
from briefing_reader.board import protocol, view
from tests import board_fakes
from tests.ui_fakes import AppHarness, fonts, settle, wait_for

_app: QApplication | None = None

ORB = {"type": "orb", "state": "idle", "form": "rings", "muted": False, "offline": False, "dim": False,
       "away": False, "level": 0, "voice": 0}
CANVAS = {"type": "canvas", "clock": "Tue 17 Nov · 9:41 AM", "word": "Standby", "tone": "holo", "approvals": 0,
          "buttons": ["history", "briefing", "approvals", "settings", "minimize", "maximize", "close"],
          "briefing_new": False, "hold": "", "rec": "", "wake": "", "meter": False}


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_canvas", "-platform", "offscreen"])
    fonts()


def frame(frame_id: str = "f1", title: str = "Dana's budget") -> dict:
    return {"type": "frame", "id": frame_id, "kind": "text", "title": title, "body": {"text": "Made-up words."}}


# --------------------------------------------------------------------------
# The messages (board.protocol)
# --------------------------------------------------------------------------

class OrbMessageTests(unittest.TestCase):
    def test_an_orb_message_is_rebuilt_field_by_field(self) -> None:
        checked = protocol.check_message(dict(ORB, state="speaking", form="badge", level=12, voice=87))
        self.assertEqual(checked.type, "orb")
        self.assertEqual(json.loads(checked.text), dict(ORB, state="speaking", form="badge", level=12, voice=87))

    def test_only_the_state_and_the_form_are_needed(self) -> None:
        sent = json.loads(protocol.check_message({"type": "orb", "state": "idle", "form": "corner"}).text)
        self.assertEqual(sent, dict(ORB, form="corner"))

    def test_every_state_and_form_on_the_list_passes_and_nothing_else(self) -> None:
        for state in protocol.ORB_STATES:
            for form in protocol.ORB_FORMS:
                protocol.check_message(dict(ORB, state=state, form=form))
        for bad in (dict(ORB, state="dancing"), dict(ORB, form="cube"), dict(ORB, state=""), dict(ORB, form=None)):
            with self.assertRaises(protocol.Refused):
                protocol.check_message(bad)

    def test_levels_are_whole_numbers_in_range_never_booleans_or_fractions(self) -> None:
        for bad in (-1, 101, 0.5, True, "50", None):
            for field in ("level", "voice"):
                with self.assertRaises(protocol.Refused, msg=f"{field}={bad!r}"):
                    protocol.check_message(dict(ORB, **{field: bad}))

    def test_flags_are_booleans_and_no_other_field_passes(self) -> None:
        for bad in (dict(ORB, muted=1), dict(ORB, offline="no"), dict(ORB, dim=0), dict(ORB, away="yes"),
                    dict(ORB, words="hello"),
                    dict(ORB, id="f1")):
            with self.assertRaises(protocol.Refused):
                protocol.check_message(bad)


class CanvasMessageTests(unittest.TestCase):
    def test_a_canvas_message_passes_as_it_is(self) -> None:
        checked = protocol.check_message(dict(CANVAS, approvals=2, tone="amber", hold="Sent", rec="Rec 0:42"))
        self.assertEqual(checked.type, "canvas")
        self.assertEqual(json.loads(checked.text)["approvals"], 2)
        self.assertEqual(json.loads(checked.text)["buttons"], CANVAS["buttons"])

    def test_buttons_are_words_of_the_list_each_once(self) -> None:
        protocol.check_message(dict(CANVAS, buttons=list(protocol.CANVAS_BUTTONS)))
        for bad in (["history", "history"], ["delete_everything"], ["history", 3], "history", [["history"]],
                    ["undo"], ["drag"]):
            with self.assertRaises(protocol.Refused, msg=repr(bad)):
                protocol.check_message(dict(CANVAS, buttons=bad))

    def test_the_words_are_capped_and_typed(self) -> None:
        for bad in (dict(CANVAS, clock="x" * (protocol.CLOCK_CAP + 1)), dict(CANVAS, word="x" * 41),
                    dict(CANVAS, hold="x" * 25), dict(CANVAS, rec="x" * 41), dict(CANVAS, wake="x" * 25),
                    dict(CANVAS, tone="pink"), dict(CANVAS, approvals=-1), dict(CANVAS, approvals=1000),
                    dict(CANVAS, approvals=True), dict(CANVAS, meter=1), dict(CANVAS, briefing_new="yes"),
                    dict(CANVAS, label="Approve all"), {"type": "canvas"}):
            with self.assertRaises(protocol.Refused, msg=repr(bad)):
                protocol.check_message(bad)

    def test_markup_stays_text(self) -> None:
        sent = json.loads(protocol.check_message(dict(CANVAS, word="<img src=x onerror=alert(1)>")).text)
        self.assertEqual(sent["word"], "<img src=x onerror=alert(1)>")      # the page sets it with textContent


class GoRequestTests(unittest.TestCase):
    def test_every_word_on_the_list_passes(self) -> None:
        for word in protocol.GO_WORDS:
            request = protocol.check_request("go", json.dumps({"to": word}), ())
            self.assertEqual((request.kind, request.to), ("go", word))

    def test_an_unknown_word_is_refused(self) -> None:
        for payload in ({"to": "delete_everything"}, {"to": ""}, {"to": "Briefing"}, {"to": 3}, {"to": None}, {},
                        {"to": "briefing", "now": True}, {"word": "briefing"}, {"to": ["briefing"]}):
            with self.assertRaises(protocol.Refused, msg=repr(payload)):
                protocol.check_request("go", json.dumps(payload), ())

    def test_the_list_is_the_buttons_and_three_more(self) -> None:
        self.assertEqual(set(protocol.GO_WORDS) - set(protocol.CANVAS_BUTTONS), {"undo", "stop_rec", "drag"})
        self.assertIn("go", protocol.REQUEST_KINDS)
        self.assertEqual(protocol.kind_word("go"), "go")

    def test_the_corner_buttons_are_a_part_f6_reaches(self) -> None:
        self.assertIn("controls", protocol.FOCUS_PARTS)
        protocol.check_message({"type": "focus_part", "part": "controls"})


class FormTests(unittest.TestCase):
    def test_the_form_follows_the_frames_and_the_state(self) -> None:
        for state in protocol.ORB_STATES:
            self.assertEqual(canvas_view.orb_form(state, True), "corner")           # frames: always the corner
            self.assertEqual(canvas_view.orb_form(state, True, True), "corner")
        self.assertEqual(canvas_view.orb_form("speaking", False), "badge")
        for state in ("idle", "listening", "thinking", "needs", "waiting"):
            self.assertEqual(canvas_view.orb_form(state, False), "rings")
            self.assertEqual(canvas_view.orb_form(state, False, True), "badge")     # the moment after he spoke

    def test_levels_are_clamped_whole_numbers(self) -> None:
        self.assertEqual(canvas_view.orb_levels(None, None), (0, 0))
        self.assertEqual(canvas_view.orb_levels(0.62, 1.0), (62, 100))
        self.assertEqual(canvas_view.orb_levels(-3, 7), (0, 100))
        self.assertEqual(canvas_view.orb_levels(float("nan"), "loud"), (0, 0))


# --------------------------------------------------------------------------
# The network gate (snapshot_engine): starting the engine for the canvas never opens a usable gate
# --------------------------------------------------------------------------

class FakeGate:
    """snapshot_gate.ConnectGate's surface: it binds (a port) or not (0), and records every seal, in order."""

    def __init__(self, port: int = 4711) -> None:
        self._port = port
        self.sealed = False
        self.events: list[str] = []

    def start(self) -> int:
        self.events.append("start-sealed" if self.sealed else "start-open")
        return self._port

    def seal(self, sealed: bool) -> None:
        self.sealed = bool(sealed)
        self.events.append("seal" if sealed else "unseal")

    def counts(self) -> dict[str, int]:
        return {}


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gates: list[FakeGate] = []
        self.port = 4711

        def make() -> FakeGate:
            gate = FakeGate(self.port)
            self.gates.append(gate)
            return gate

        for name, value in (("_ENGINE", snapshot_engine._EngineState()), ("_gate_factory", make),
                            ("_network_use", None)):
            patcher = mock.patch.object(snapshot_engine, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def plan(self, *, page: bool) -> tuple[bool, int, str]:
        return snapshot_engine._gate_plan(page=page, test_platform=False)    # noqa: SLF001 - the start's own decision

    def page_shot(self) -> str:
        """What PageCamera.shoot asks of an engine that runs already (not on a test platform)."""
        with mock.patch.object(snapshot_engine, "_test_platform", lambda: False):
            return snapshot_engine._start_engine(page=True)               # noqa: SLF001

    def test_no_page_picture_and_no_agent_possible_the_dead_proxy_and_no_gate_at_all(self) -> None:
        snapshot_engine.set_network_use(False)
        self.assertEqual(self.plan(page=False), (True, 0, ""))            # offline: snapshots.OFFLINE_FLAGS
        self.assertEqual(self.gates, [])                                  # no gate was ever made, none listens
        self.assertTrue(snapshot_engine.gate_sealed())
        flags = snapshots.chromium_flags("", offline=True, gate_port=0)
        self.assertIn(snapshots.OFFLINE_FLAGS[0], flags)                  # Chromium's proxy is the dead address

    def test_otherwise_the_gate_binds_sealed_before_it_listens(self) -> None:
        snapshot_engine.set_network_use(lambda: True)
        self.assertEqual(self.plan(page=False), (False, 4711, ""))
        gate = self.gates[0]
        self.assertEqual(gate.events, ["seal", "start-sealed"])           # sealed first, then it listens
        self.assertTrue(gate.sealed)
        self.assertTrue(snapshot_engine.gate_sealed())

    def test_unsaid_counts_as_possible_and_still_starts_sealed(self) -> None:
        self.assertTrue(snapshot_engine.network_possible())
        self.plan(page=False)
        self.assertTrue(self.gates[0].sealed)

    def test_only_the_first_page_shot_unseals_it(self) -> None:
        self.plan(page=False)                                             # the canvas started the engine
        gate = self.gates[0]
        snapshot_engine._ENGINE.imported = True                           # noqa: SLF001 - the engine runs
        self.assertEqual(snapshot_engine._start_engine(page=False), "")   # noqa: SLF001 - the board again, a warm-up
        self.assertTrue(gate.sealed)                                      # ... still sealed
        self.assertEqual(self.page_shot(), "")
        self.assertFalse(gate.sealed)                                     # the first page picture opened it
        self.assertFalse(snapshot_engine.gate_sealed())
        self.assertEqual(gate.events, ["seal", "start-sealed", "unseal"])

    def test_the_agents_seal_is_its_own_reason(self) -> None:
        self.plan(page=False)
        gate = self.gates[0]
        snapshot_engine.seal_gate(True)                                   # a page the agent wrote is open
        snapshot_engine.seal_gate(False)                                  # ... and gone
        self.assertTrue(gate.sealed)                                      # the start seal still holds
        snapshot_engine._ENGINE.imported = True                           # noqa: SLF001
        snapshot_engine.seal_gate(True)
        self.assertEqual(self.page_shot(), "")                            # a page shot meanwhile
        self.assertTrue(gate.sealed)                                      # the agent page still holds it
        snapshot_engine.seal_gate(False)
        self.assertFalse(gate.sealed)

    def test_a_page_shot_first_gets_a_usable_gate_as_before(self) -> None:
        self.assertEqual(self.plan(page=True), (False, 4711, ""))
        self.assertEqual(self.gates[0].events, ["start-open"])            # never sealed, never asked to unseal
        self.assertFalse(snapshot_engine.gate_sealed())

    def test_the_canvas_still_starts_when_the_gate_cannot_bind(self) -> None:
        self.port = 0
        self.assertEqual(self.plan(page=False), (True, 0, ""))            # the dead proxy: the board needs no network
        self.assertTrue(snapshot_engine._ENGINE.no_gate)                  # noqa: SLF001
        snapshot_engine._ENGINE.imported = True                           # noqa: SLF001
        self.assertEqual(self.page_shot(), snapshots.R_ENGINE_FAILED)     # no page picture in that run

    def test_a_page_shot_still_refuses_to_start_without_its_gate(self) -> None:
        self.port = 0
        self.assertEqual(self.plan(page=True), (False, 0, snapshots.R_ENGINE_FAILED))

    def test_a_test_platform_is_the_dead_proxy_and_never_a_gate(self) -> None:
        self.assertEqual(snapshot_engine._gate_plan(page=False, test_platform=True), (True, 0, ""))   # noqa: SLF001
        self.assertEqual(snapshot_engine._gate_plan(page=True, test_platform=True), (True, 0, ""))    # noqa: SLF001
        self.assertEqual(self.gates, [])

    def test_the_app_says_what_can_ask_for_a_page(self) -> None:
        said: list = []
        with mock.patch.object(snapshot_engine, "set_network_use", said.append):
            host = SimpleNamespace(_snapshots=None, agent=None)
            ui.AppController._say_network_use(host)                       # noqa: SLF001
            self.assertFalse(said[0]())                                   # page pictures off and no agent: nothing can
            host._snapshots = object()
            self.assertTrue(said[0]())
            host._snapshots, host.agent = None, object()
            self.assertTrue(said[0]())


# --------------------------------------------------------------------------
# The view (board.view.BoardView, its page a stand-in)
# --------------------------------------------------------------------------

class ViewTests(unittest.TestCase):
    def make(self, **options) -> board_fakes.StandInBoard:
        board = board_fakes.StandInBoard(**options)
        self.addCleanup(board.shutdown)
        board.resize(640, 480)
        return board

    def test_go_reaches_the_app_and_an_unknown_word_does_not(self) -> None:
        board = self.make()
        board.start()
        settle()
        went: list[str] = []
        board.goRequested.connect(went.append)
        self.assertTrue(board.request("go", to="briefing"))
        self.assertFalse(board.request("go", to="format_c"))
        self.assertEqual(went, ["briefing"])

    def test_the_real_bridge_counts_an_unknown_word_as_dropped(self) -> None:
        board = self.make()
        board.start()
        settle()
        went: list[str] = []
        board.goRequested.connect(went.append)
        bridge = view.BoardBridge(known=board.known_ids, deliver=board._on_request)   # noqa: SLF001 - as _build wires it
        bridge.post("go", json.dumps({"to": "settings"}))
        bridge.post("go", json.dumps({"to": "shutdown_pc"}))
        bridge.post("go", "not json")
        self.assertEqual(went, ["settings"])
        self.assertEqual(bridge.counts()["accepted:go"], 1)
        self.assertEqual(bridge.counts()["dropped:go"], 2)

    def test_the_newest_orb_and_canvas_wait_for_the_page_and_take_no_room(self) -> None:
        board = self.make(ready=False)
        board.start()
        settle()
        self.assertTrue(board.apply(frame("f1")))
        for level in range(200):                                       # a flood of levels before the page is up
            self.assertTrue(board.apply(dict(ORB, state="listening", level=level % 100)))
        self.assertTrue(board.apply(dict(CANVAS, word="One")))
        self.assertTrue(board.apply(dict(CANVAS, word="Two")))
        self.assertEqual(board.sent, [])
        board.say_ready()
        kinds = [message["type"] for message in board.sent]
        self.assertEqual(kinds, ["motion", "frame", "canvas", "orb"])   # the frame kept its place: nothing dropped
        self.assertEqual(board.sent[-1]["level"], 99)
        self.assertEqual(board.sent[-2]["word"], "Two")
        self.assertNotIn("dropped:queue", board.counts())

    def test_a_refused_orb_message_is_counted_and_not_sent(self) -> None:
        board = self.make()
        board.start()
        settle()
        before = len(board.sent)
        self.assertFalse(board.apply(dict(ORB, state="exploding")))
        self.assertEqual(len(board.sent), before)
        self.assertEqual(board.counts()["refused:field"], 1)

    def test_f11_goes_on_to_the_window_from_the_page(self) -> None:
        none, shift = Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier
        self.assertTrue(view.is_full_key(Qt.Key.Key_F11, none))
        self.assertFalse(view.is_full_key(Qt.Key.Key_F11, shift))
        self.assertFalse(view.is_full_key(Qt.Key.Key_F10, none))
        board = self.make()
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_F11, none)
        event.accept()
        self.assertTrue(board._keys.eventFilter(board, event))            # noqa: SLF001 - the board's own key filter
        self.assertFalse(event.isAccepted())                              # not the page's: the window's shortcut fires

    def test_the_canvas_ground_is_opaque_black_and_the_desk_see_through(self) -> None:
        board = self.make()
        self.assertFalse(board.is_canvas())
        self.assertEqual(board._ground(), QColor(0, 0, 0, 0))            # noqa: SLF001 - what the page is given
        board.set_canvas(True)
        self.assertTrue(board.is_canvas())
        self.assertEqual(board._ground(), QColor(0, 0, 0, 255))          # noqa: SLF001
        self.assertTrue(view.BoardView(canvas=True).is_canvas())


# --------------------------------------------------------------------------
# The canvas in the app
# --------------------------------------------------------------------------

class CanvasApp(unittest.TestCase):
    def make(self, *, canvas: bool = True, factory: str = "stand_in", size=(1280, 800), extra: str = "",
             show: bool = True, **kwargs) -> AppHarness:
        self.boards: list = []
        if factory == "stand_in":
            board_factory = board_fakes.stand_in_factory(self.boards, canvas=canvas)
        elif factory == "fallback":
            board_factory = board_fakes.fallback_factory(self.boards, canvas=canvas)
        else:
            board_factory = None
        kwargs.setdefault("lines", ())                                    # no card waits unless a test says so
        harness = AppHarness(self, config_extra=extra, board_factory=board_factory, **kwargs)
        self.c, self.window = harness.c, harness.c.window
        self.reading = self.window.reading
        holo.set_motion_level(holo.MOTION_OFF)
        self.addCleanup(holo.set_motion_level, holo.MOTION_LIVELY)
        if show:
            harness.reading(autoplay=False)
            self.window.setWindowState(Qt.WindowState.WindowNoState)      # a window of its own size, as a test wants it
            self.window.resize(*size)
            self.reading.set_tab(ui.SCREEN_HOME)
            self.reading.set_activity_state(hud.ORB_STANDBY, False)
            settle(6)
        return harness

    def canvas(self) -> canvas_ui.CanvasController:
        return self.c.look.canvas

    def started(self) -> board_fakes.StandInBoard:
        self.assertTrue(wait_for(lambda: bool(self.boards) and self.boards[0].is_ready()))
        settle(4)
        return self.boards[0]

    def last(self, kind: str) -> dict:
        return self.boards[0].of_type(kind)[-1]

    def put(self, count: int = 1) -> None:
        """An answer's frames on the board, as the desk's controller puts them."""
        desk = self.c.look.desk
        self.c._you("What did Dana send?")
        desk.on_answer_started(0, "What did Dana send?")
        self.c._jarvis("Dana sent the budget numbers, sir.")
        answer = desk._answers[desk._current]
        answer.done = True
        messages: list = []
        for index in range(count):
            frame_id = desk.feed.frame_id(answer.key, f"s{index}", "", "")
            messages += desk.feed.put(answer.key, f"s{index}", [frame(frame_id, f"Frame {index}")], when="9:41 AM",
                                      rank=float(index))
        desk._send(messages)
        desk.feed.set_turn(answer.key, desk._answer_entry())
        desk._sync_counts()
        settle(4)


class ModeTests(CanvasApp):
    def test_home_is_the_canvas_no_dock_no_header_no_conversation(self) -> None:
        self.make()
        reading = self.reading
        self.assertIsNotNone(self.canvas())
        self.assertTrue(reading.canvas_on())
        page = reading.canvas_page()
        self.assertTrue(page.isVisible())
        self.assertFalse(reading.dock.isVisible())                        # no sidebar
        self.assertFalse(reading.header.isVisible())                      # the page draws the corners
        self.assertFalse(reading.stage.isVisible())                       # the old ring is not on Home
        self.assertIsNone(reading.desk_page())                            # no conversation column, ever
        self.assertEqual(page.size(), reading.size())                     # the whole of Home
        self.assertTrue(reading.canvas_shown())

    def test_the_ask_line_the_caption_and_the_chips_stand_on_the_canvas(self) -> None:
        self.make()
        reading, page = self.reading, self.reading.canvas_page()
        bar = reading.command_bar
        self.assertTrue(bar.isVisible() and bar.is_canvas())
        self.assertTrue(page.isAncestorOf(bar) and page.isAncestorOf(reading.captions))
        self.assertTrue(reading.captions.is_canvas())
        field = bar.field
        centre = field.mapTo(page, field.rect().center())
        self.assertAlmostEqual(centre.x(), page.width() / 2, delta=2)     # bottom centre
        self.assertGreater(centre.y(), page.height() - 80)
        self.assertLessEqual(field.width(), canvas_view.BAR_WIDTH)
        caption = reading.captions.mapTo(page, reading.captions.rect().bottomLeft())
        self.assertLess(caption.y(), field.mapTo(page, field.rect().topLeft()).y())   # the caption over the line

    def test_board_off_keeps_home_as_it_was(self) -> None:
        self.make(extra="\n[look]\nboard = false\n")
        self.assertIsNone(self.c.look.desk)
        self.assertIsNone(self.c.look.canvas)
        self.assertFalse(self.reading.canvas_on())
        self.assertTrue(self.reading.dock.isVisible())
        self.assertTrue(self.reading.header.isVisible())
        self.assertTrue(self.reading.stage.isVisible())
        self.assertFalse(self.reading.command_bar.is_canvas())
        self.assertFalse(self.reading.header.back_visible())
        self.assertTrue(bool(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))

    def test_a_factory_that_is_not_the_canvas_keeps_the_desk(self) -> None:
        self.make(canvas=False)
        self.assertIsNotNone(self.c.look.desk)
        self.assertIsNone(self.c.look.canvas)
        self.assertFalse(self.reading.canvas_on())
        self.assertTrue(self.reading.dock.isVisible())

    def test_the_page_starts_with_the_window_not_at_the_first_ask(self) -> None:
        self.make(show=False)
        settle(6)
        self.assertEqual(self.boards, [])                                 # not before the window is up
        self.assertTrue(self.reading.canvas_on())                         # ... though Home is the canvas already
        harness_window = self.window
        self.c.enter_reading(autoplay=False)
        harness_window.show()
        board = self.started()                                            # no Ask was made
        self.assertTrue(board.is_canvas())
        self.assertIs(board.parentWidget(), self.reading.canvas_page())
        self.assertEqual(board.size(), self.reading.canvas_page().size())  # full-bleed
        self.assertIsNotNone(self.canvas().ready_ms)
        kinds = [message["type"] for message in board.sent]
        self.assertIn("canvas", kinds)
        self.assertIn("orb", kinds)

    def test_the_window_shows_before_the_page_is_made(self) -> None:
        self.make()
        self.assertTrue(self.window.isVisible())
        self.assertGreater(canvas_ui.START_AFTER_MS, 0)                   # the window paints first
        self.assertTrue(self.reading.canvas_page().isVisible())           # plain black with the ask line meanwhile
        self.started()


class OrbTests(CanvasApp):
    def test_the_first_orb_is_the_rings_at_rest(self) -> None:
        self.make()
        self.started()
        self.assertEqual(dict(self.last("orb"), away=False), dict(ORB))

    def test_away_follows_the_window_being_the_active_one(self) -> None:
        self.make()
        self.started()
        canvas = self.canvas()
        with mock.patch.object(self.window, "isActiveWindow", return_value=True):
            canvas.sync()
            self.assertFalse(self.last("orb")["away"])                    # the owner works in it: 30 a second at rest
        with mock.patch.object(self.window, "isActiveWindow", return_value=False):
            self.window.activeChanged.emit(False)                         # he went to another window
            self.assertTrue(self.last("orb")["away"])                     # ... 10 a second at rest

    def test_each_state_reaches_the_page_as_its_word(self) -> None:
        self.make()
        self.started()
        reading, canvas = self.reading, self.canvas()
        reading.set_talking(True)
        reading.ring_level(0.4)
        canvas.sync()
        self.assertEqual((self.last("orb")["state"], self.last("orb")["level"]), ("listening", 40))
        reading.set_talking(False)
        reading.set_activity_state(hud.ORB_WORKING, False, ui.ASK_PLANNING_LABEL)
        self.assertEqual(self.last("orb")["state"], "thinking")
        self.assertEqual(self.last("orb")["level"], 0)                    # no level but while he listens
        reading.set_activity_state(hud.ORB_SPEAKING, False)
        self.assertEqual((self.last("orb")["state"], self.last("orb")["form"]), ("speaking", "badge"))
        self.assertGreater(self.last("orb")["voice"], 0)                  # his voice's level (a pulse when not timed)
        reading.set_activity_state(hud.ORB_STANDBY, False)
        reading.set_pending(1, 1)
        self.assertEqual(self.last("orb")["state"], "needs")
        reading.set_pending(0, 0)
        reading.set_waiting(True, "AM")
        self.assertEqual(self.last("orb")["state"], "waiting")
        reading.set_waiting(False)
        reading.set_muted_look(True)
        self.assertTrue(self.last("orb")["muted"])
        reading.set_muted_look(False)
        reading.set_offline_voice(True)
        self.assertTrue(self.last("orb")["offline"])

    def test_the_voice_level_is_the_apps_own(self) -> None:
        self.make()
        self.started()
        reading = self.reading
        reading._voice_level = lambda: 0.37                               # noqa: SLF001 - as set_voice leaves it
        reading.set_activity_state(hud.ORB_SPEAKING, False)
        self.canvas().sync()
        self.assertEqual(self.last("orb")["voice"], 37)

    def test_the_badge_lingers_after_he_speaks_then_the_rings(self) -> None:
        self.make()
        self.started()
        reading, canvas = self.reading, self.canvas()
        reading.set_activity_state(hud.ORB_SPEAKING, False)
        self.assertEqual(canvas.form(), "badge")
        reading.set_activity_state(hud.ORB_STANDBY, False)
        self.assertEqual(self.last("orb")["form"], "badge")               # he stopped: the badge stays a moment
        self.assertTrue(canvas._linger.isActive())                        # noqa: SLF001
        canvas._linger_over()                                             # noqa: SLF001 - BADGE_LINGER_MS later
        self.assertEqual(self.last("orb")["form"], "rings")

    def test_a_new_request_ends_the_linger_at_once(self) -> None:
        self.make()
        self.started()
        reading, canvas = self.reading, self.canvas()
        reading.set_activity_state(hud.ORB_SPEAKING, False)
        reading.set_activity_state(hud.ORB_STANDBY, False)
        reading.set_activity_state(hud.ORB_WORKING, False, ui.ASK_PLANNING_LABEL)
        self.assertEqual((self.last("orb")["state"], self.last("orb")["form"]), ("thinking", "rings"))

    def test_the_corner_while_the_board_holds_frames_and_back_when_it_is_empty(self) -> None:
        self.make()
        board = self.started()
        self.assertEqual(self.canvas().form(), "rings")
        self.put(2)
        self.assertTrue(self.reading.desk_shown())                        # frames show on the canvas ...
        self.assertIsNone(self.reading.desk_page())                       # ... and no desk formed
        self.assertEqual(self.last("orb")["form"], "corner")
        self.assertEqual(board.of_type("desk")[-1]["state"], "enter")
        self.reading.set_activity_state(hud.ORB_SPEAKING, False)
        self.assertEqual(self.last("orb")["form"], "corner")              # speaking there: still the corner
        self.reading.set_activity_state(hud.ORB_STANDBY, False)
        desk = self.c.look.desk
        desk._send(desk.feed.clear_unpinned(0.0))
        desk._send(desk.feed.due(1e9))                                    # the Clear's Bring back is over: gone
        settle(4)
        self.assertEqual(self.reading.board_count(), 0)
        self.assertFalse(self.reading.desk_shown())
        self.canvas()._linger_over()                                      # noqa: SLF001
        self.assertEqual(self.last("orb")["form"], "rings")
        self.assertEqual(board.of_type("desk")[-1]["state"], "leave")

    def test_nothing_is_sent_twice(self) -> None:
        self.make()
        board = self.started()
        self.canvas().sync()
        count = len(board.sent)
        for _ in range(5):
            self.canvas().sync()
        self.assertEqual(len(board.sent), count)

    def test_the_page_coming_back_gets_the_corners_and_the_orb_again(self) -> None:
        self.make()
        board = self.started()
        orbs = len(board.of_type("orb"))
        board._phase = view._LOADING                                      # noqa: SLF001 - its renderer stopped, it reloads
        board.say_ready()
        settle()
        self.assertEqual(len(board.of_type("orb")), orbs + 1)
        self.assertEqual(dict(self.last("orb"), away=False), dict(ORB))


class CornerTests(CanvasApp):
    def test_the_corners_message_says_what_the_header_says(self) -> None:
        self.make()
        self.started()
        sent = self.last("canvas")
        time_text, date_text = self.reading.header.clock_texts()
        self.assertEqual(sent["clock"], f"{date_text} · {time_text}")
        self.assertEqual(sent["word"], self.reading.state_word())
        self.assertEqual(sent["tone"], "holo")
        self.assertEqual(sent["approvals"], 0)

    def test_only_buttons_that_can_work_are_shown(self) -> None:
        self.make()
        self.started()
        buttons = self.last("canvas")["buttons"]
        for word in ("history", "briefing", "approvals", "settings", "minimize", "maximize", "close", "day"):
            self.assertIn(word, buttons)
        for word in ("desktop", "agents", "meetings", "followups", "memory", "board"):
            self.assertNotIn(word, buttons, word)                         # off in this config: never drawn
        self.reading.set_memory_available(True)
        self.reading.set_agents_available(True)
        self.canvas().sync()                                              # (else at its next beat, SYNC_MS later)
        self.assertIn("memory", self.last("canvas")["buttons"])
        self.assertIn("agents", self.last("canvas")["buttons"])
        self.assertEqual(self.last("canvas")["buttons"],
                         [word for word in protocol.CANVAS_BUTTONS if word in self.last("canvas")["buttons"]])

    def test_mute_shows_only_with_the_speaker(self) -> None:
        self.make()
        self.started()
        self.window.set_speaker_available(False)
        self.canvas().sync()
        self.assertNotIn("mute", self.last("canvas")["buttons"])
        self.assertFalse(self.canvas().on_go("mute"))                     # not there: pressing it does nothing
        self.window.set_speaker_available(True)
        self.canvas().sync()
        self.assertIn("mute", self.last("canvas")["buttons"])

    def test_approvals_count_is_amber_news_and_the_state_word_follows(self) -> None:
        self.make()
        self.started()
        self.reading.set_pending(2, 2)
        sent = self.last("canvas")
        self.assertEqual(sent["approvals"], 2)
        self.assertEqual(sent["tone"], "amber")
        self.assertEqual(sent["word"], self.reading.state_word())

    def test_a_hold_and_a_recording_show_with_their_controls(self) -> None:
        self.make()
        self.started()
        reading, canvas = self.reading, self.canvas()
        self.assertFalse(canvas.on_go("undo"))                            # no hold: nothing to undo
        self.assertFalse(canvas.on_go("stop_rec"))
        undone: list[int] = []
        stopped: list[int] = []
        reading.holdUndo.connect(lambda: undone.append(1))
        reading.stopRecording.connect(lambda: stopped.append(1))
        reading.set_hold("Sent")
        canvas.sync()
        self.assertEqual(self.last("canvas")["hold"], "Sent")
        self.assertTrue(canvas.on_go("undo"))
        reading._meetings_on = True                                       # noqa: SLF001 - [meetings] on
        reading.set_recording("0:42", warning="No call audio")
        canvas.sync()
        self.assertEqual(self.last("canvas")["rec"], "Rec 0:42 · No call audio")
        self.assertTrue(canvas.on_go("stop_rec"))
        self.assertEqual((undone, stopped), ([1], [1]))

    def test_the_meter_only_when_the_app_measures_the_microphone(self) -> None:
        self.make()
        self.started()
        self.assertFalse(self.last("canvas")["meter"])                    # [talk] off here: no meter drawn
        self.c.talk = object()
        self.canvas().sync()
        self.assertTrue(self.last("canvas")["meter"])


class GoTests(CanvasApp):
    SCREENS = {"briefing": ui.SCREEN_BRIEFING, "approvals": ui.SCREEN_APPROVALS, "live": ui.SCREEN_LIVE,
               "settings": ui.SCREEN_SETTINGS}

    def test_each_screen_opens_over_the_canvas_with_back_and_esc_returns(self) -> None:
        self.make()
        self.started()
        reading, canvas = self.reading, self.canvas()
        reading.set_live_available(True)
        for word, screen in self.SCREENS.items():
            self.assertTrue(canvas.on_go(word), word)
            settle()
            self.assertEqual(reading.current_tab(), screen, word)
            self.assertTrue(reading.header.isVisible(), word)             # today's screen, full window ...
            self.assertTrue(reading.header.back_button.isVisible(), word)  # ... with Back at its top left
            self.assertFalse(reading.dock.isVisible(), word)
            self.assertFalse(reading.canvas_shown())
            if word == "settings":
                QTest.keyClick(reading, Qt.Key.Key_Escape)                # Esc
            else:
                reading.header.back_button.click()                        # Back
            settle()
            self.assertEqual(reading.current_tab(), ui.SCREEN_HOME, word)
            self.assertFalse(reading.header.isVisible(), word)
            self.assertTrue(reading.canvas_shown(), word)

    def test_every_screen_the_sidebar_reached_has_its_word(self) -> None:
        self.make()
        self.started()
        reading, canvas = self.reading, self.canvas()
        for setter in (reading.set_live_available, reading.set_memory_available, reading.set_followups_available,
                       reading.set_meetings_available, reading.set_agents_available):
            setter(True)
        canvas.sync()
        slots = [slot for slot in reading.dock.visible_slots() if slot not in (dock.HOME, dock.DESKTOP)]
        words = set(self.last("canvas")["buttons"])
        self.assertEqual(sorted(slots), sorted(word for word in canvas_ui._SCREEN_SLOTS if word in words))
        for word in slots:                                               # the slot's own word, and it opens its screen
            self.assertTrue(canvas.on_go(word), word)
            self.assertEqual(reading.current_tab(), ui._SLOT_SCREENS[word], word)
            reading.go_home()

    def test_a_screen_that_is_off_cannot_be_opened(self) -> None:
        self.make()
        self.started()
        self.assertFalse(self.canvas().on_go("agents"))
        self.assertFalse(self.canvas().on_go("desktop"))
        self.assertFalse(self.canvas().on_go("board"))
        self.assertFalse(self.canvas().on_go("rm -rf"))
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_HOME)
        self.assertEqual(self.canvas().counts["go:unavailable"], 3)
        self.assertEqual(self.canvas().counts["go:refused"], 1)

    def test_history_and_the_clock_open_their_drawers(self) -> None:
        self.make()
        self.started()
        reading = self.reading
        self.assertTrue(self.canvas().on_go("history"))
        self.assertTrue(reading.transcript_drawer.is_open())
        self.assertTrue(self.canvas().on_go("day"))
        self.assertTrue(reading.day_drawer.is_open())
        self.assertFalse(reading.transcript_drawer.is_open())

    def test_the_page_button_goes_through_the_bridges_check(self) -> None:
        self.make()
        board = self.started()
        self.assertTrue(board.request("go", to="settings"))
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_SETTINGS)
        self.reading.go_home()
        self.assertFalse(board.request("go", to="wipe"))
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_HOME)

    def test_mute_is_the_headers_own(self) -> None:
        self.make()
        self.started()
        self.window.set_speaker_available(True)
        toggled: list[bool] = []
        self.window.header.muteToggled.connect(toggled.append)
        self.assertTrue(self.canvas().on_go("mute"))
        self.assertEqual(toggled, [True])
        self.assertTrue(self.last("orb")["muted"])

    def test_the_windows_own_buttons(self) -> None:
        self.make()
        self.started()
        window, canvas = self.window, self.canvas()
        self.assertFalse(window.isMaximized())
        self.assertTrue(canvas.on_go("maximize"))
        settle()
        self.assertTrue(window.isMaximized())
        self.assertTrue(canvas.on_go("maximize"))
        settle()
        self.assertFalse(window.isMaximized())
        closes: list[int] = []
        window.closeRequested.connect(lambda: closes.append(1))
        self.assertTrue(canvas.on_go("close"))
        self.assertEqual(closes, [1])                                     # the controller decides what closing means
        self.assertTrue(canvas.on_go("minimize"))
        settle()
        self.assertTrue(window.isMinimized())

    def test_shortcuts_still_open_the_screens(self) -> None:
        self.make()
        self.started()
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        settle()
        QTest.keyClick(self.window, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
        settle()
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_BRIEFING)
        QTest.keyClick(self.window, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
        settle()
        self.assertEqual(self.reading.current_tab(), ui.SCREEN_HOME)
        self.assertTrue(self.reading.canvas_shown())


class FocusTests(CanvasApp):
    def activate(self) -> None:
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        settle(4)

    def test_the_keyboard_is_in_the_ask_line_when_the_window_opens(self) -> None:
        self.make()
        self.activate()
        self.started()
        self.assertTrue(self.reading.command_bar.input.hasFocus())

    def test_and_again_after_an_answer_arrives(self) -> None:
        self.make()
        self.activate()
        board = self.started()
        self.reading.setFocus(Qt.FocusReason.OtherFocusReason)
        settle()
        self.assertFalse(self.reading.command_bar.input.hasFocus())
        self.c._look_call("on_ask_outcome", SimpleNamespace(ok=True, say="Done, sir.", cards=(), research=None,
                                                             calendar_read=None, mode="", frames=()), 0)
        settle(4)
        self.assertTrue(self.reading.command_bar.input.hasFocus())
        board.page_widget.setFocus(Qt.FocusReason.OtherFocusReason)       # the owner's keyboard is in the board
        settle()
        self.canvas().on_answer()
        settle(4)
        self.assertTrue(board.page_widget.hasFocus())                     # ... it stays there (he reads)

    def test_and_when_home_comes_back(self) -> None:
        self.make()
        self.activate()
        self.started()
        self.canvas().on_go("settings")
        settle()
        self.reading.go_home()
        settle(4)
        self.assertTrue(self.reading.command_bar.input.hasFocus())

    def test_typing_and_enter_still_submit(self) -> None:
        self.make()
        self.activate()
        self.started()
        bar = self.reading.command_bar
        sent: list[str] = []
        bar.submitted.connect(sent.append)
        QTest.keyClicks(bar.input, "what is on today")
        self.assertFalse(bar.field.canvas_hint_shown())                   # the arrow takes CTRL K's place
        QTest.keyClick(bar.input, Qt.Key.Key_Return)
        self.assertEqual(sent, ["what is on today"])


class AskLineTests(unittest.TestCase):
    def test_the_canvas_look_and_back(self) -> None:
        bar = hud.CommandBar()
        bar.set_holo(True)
        height = bar.field.height()
        bar.set_canvas(True)
        self.assertTrue(bar.is_canvas() and bar.field.is_canvas())
        self.assertEqual(bar.field.height(), hud._CommandField.CANVAS_HEIGHT)
        self.assertTrue(bar.field.canvas_hint_shown())
        bar.set_canvas_placeholder(canvas_ui.PLACEHOLDER_TALK)
        self.assertEqual(bar.input.placeholderText(), canvas_ui.PLACEHOLDER_TALK)
        bar.set_text("hello")
        self.assertFalse(bar.send_button.isHidden())
        bar.set_running(True)
        self.assertFalse(bar.field.canvas_hint_shown())                   # the stop square while a request runs
        bar.set_running(False)
        bar.set_canvas(False)
        self.assertFalse(bar.is_canvas())
        self.assertEqual(bar.field.height(), height)
        self.assertEqual(bar.input.placeholderText(), hud.COMMAND_PLACEHOLDERS[-1])
        self.assertFalse(bar.send_button.isHidden())

    def test_only_the_hologram_bar_takes_the_canvas_look(self) -> None:
        plain = hud.CommandBar()
        plain.set_canvas(True)
        self.assertFalse(plain.is_canvas())

    def test_the_caption_in_the_canvas_type(self) -> None:
        captions = home.CaptionView()
        tall = captions.band_height()
        captions.set_canvas(True)
        self.assertEqual(captions.band_height(), 2 * home.CANVAS_TYPE["caption"][2] + 4)
        self.assertLess(captions.band_height(), tall)
        self.assertEqual(captions._font("caption").pixelSize(), 15)       # noqa: SLF001 - the mock's 15 / 22
        self.assertEqual(captions.sizeHint().width(), home.CANVAS_WIDTH)
        captions.set_canvas(False)
        self.assertEqual(captions.band_height(), tall)


class BoardWaysTests(CanvasApp):
    def test_ctrl_b_folds_the_board_and_the_page_offers_it_back(self) -> None:
        self.make()
        board = self.started()
        self.put(1)
        self.reading.toggle_board()
        settle()
        self.assertFalse(self.reading.desk_shown())
        self.assertEqual(board.of_type("desk")[-1]["state"], "leave")
        self.assertIn("board", self.last("canvas")["buttons"])
        self.assertEqual(self.last("orb")["form"], "rings")
        self.assertTrue(self.canvas().on_go("board"))                     # the page's Show board
        settle()
        self.assertTrue(self.reading.desk_shown())
        self.assertNotIn("board", self.last("canvas")["buttons"])
        self.assertEqual(self.last("orb")["form"], "corner")

    def test_open_big_needs_no_geometry_on_the_canvas(self) -> None:
        self.make()
        board = self.started()
        self.put(1)
        desk = self.c.look.desk
        frame_id = desk.feed.frame_ids()[0]
        geometry = board.geometry()
        self.assertTrue(board.request("open", id=frame_id))
        self.assertEqual(desk.big.state(), "open")
        opened = board.of_type("big")[-1]
        self.assertEqual((opened["state"], opened["id"]), ("open", frame_id))
        self.assertEqual((opened["w"], opened["h"]), (board.width(), board.height()))
        self.assertEqual(board.geometry(), geometry)                      # the view never moved
        self.assertEqual(board.of_type("backdrop"), [])                   # ... and no backdrop was painted
        board.request("big", step="laid")
        board.request("big", step="back")
        board.request("big", step="shut")
        self.assertEqual(desk.big.state(), "")
        self.assertFalse(self.reading.board_big())

    def test_f6_goes_the_board_then_the_corner_buttons(self) -> None:
        self.make()
        board = self.started()
        self.put(1)
        self.window.activateWindow()
        QApplication.setActiveWindow(self.window)
        self.reading.command_bar.focus_input()
        settle()
        self.reading.cycle_focus(True)
        self.assertEqual(board.of_type("focus_part")[-1]["part"], "board")
        self.reading.command_bar.focus_input()
        settle()
        self.reading.cycle_focus(False)
        self.assertEqual(board.of_type("focus_part")[-1]["part"], "controls")
        board.request("focus_out", to="conversation")                     # no conversation here: the ask line
        settle()
        self.assertTrue(self.reading.command_bar.input.hasFocus())

    def test_the_chip_row_keeps_only_what_the_corners_do_not_say(self) -> None:
        self.make()
        self.started()
        reading = self.reading
        reading.set_pending(1, 1)
        reading.set_new_briefing(True, "Your AM briefing is ready to view")
        settle()
        self.assertTrue(reading.approvals_chip.isHidden())                # the page's Approvals 1 says it
        self.assertTrue(reading.briefing_chip.isHidden())                 # ... and its Briefing button
        self.assertTrue(self.last("canvas")["briefing_new"])
        reading.set_answer_chip("Plan ready", 5)
        self.assertFalse(reading.answer_chip.isHidden())


class FallbackTests(CanvasApp):
    def test_a_page_that_cannot_start_gives_home_its_native_ring_and_buttons(self) -> None:
        self.make(factory="fallback")
        self.assertTrue(wait_for(lambda: bool(self.boards) and self.canvas().fallen()))
        settle(6)
        reading = self.reading
        self.assertFalse(reading.canvas_on())
        self.assertTrue(reading.dock.isVisible())                         # native buttons to every screen
        self.assertTrue(reading.header.isVisible())
        self.assertFalse(reading.header.back_visible())
        self.assertTrue(reading.hero.isVisible())                         # the native ring
        bar = reading.command_bar
        self.assertTrue(bar.isVisible() and not bar.is_canvas())          # the ask line, as the pill again
        self.assertFalse(reading.captions.is_canvas())
        self.assertTrue(reading.captions.isVisible())
        self.assertEqual(self.canvas().counts["fallback:no_engine"], 1)
        reading.dock.activate(dock.SETTINGS)
        self.assertEqual(reading.current_tab(), ui.SCREEN_SETTINGS)       # nothing is out of reach

    def test_frames_show_as_read_only_tiles(self) -> None:
        self.make(factory="fallback")
        self.assertTrue(wait_for(lambda: bool(self.boards) and self.canvas().fallen()))
        self.put(2)
        settle(6)
        board = self.boards[0]
        self.assertTrue(self.reading.desk_shown())
        self.assertIsNotNone(self.reading.desk_page())                    # the desk, with the tiles in its board column
        self.assertTrue(self.reading.desk_page().isVisible())
        self.assertEqual([item["title"] for item in board.tiles().frames()], ["Frame 0", "Frame 1"])
        self.assertTrue(board.tiles().isVisible())

    def test_a_renderer_that_dies_for_good_falls_back_with_its_frames(self) -> None:
        self.make()
        board = self.started()
        self.put(2)
        board._fall_back(view.CRASHED)                                    # noqa: SLF001 - past MAX_RESTARTS
        settle(8)
        reading = self.reading
        self.assertTrue(self.canvas().fallen())
        self.assertFalse(reading.canvas_on())
        self.assertTrue(reading.dock.isVisible())
        self.assertTrue(reading.desk_shown())
        self.assertTrue(reading.desk_page().isVisible())                  # the desk formed, the tiles in it
        self.assertEqual(len(board.tiles().frames()), 2)
        self.assertTrue(reading.command_bar.isVisible())
        self.assertFalse(reading.command_bar.is_canvas())
        sent = len(board.sent)
        self.canvas().sync()                                              # nothing more is said to a page that is gone
        self.assertEqual(len(board.sent), sent)


class WindowTests(CanvasApp):
    def test_the_canvas_window_opens_maximised_once_and_is_not_always_on_top(self) -> None:
        harness = self.make(show=False)
        window = self.window
        self.assertFalse(bool(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        harness.reading(autoplay=False)
        settle(4)
        self.assertTrue(window.isMaximized())
        margins = window.layout().contentsMargins()
        self.assertEqual((margins.left(), margins.top(), margins.right(), margins.bottom()), (0, 0, 0, 0))
        window.setWindowState(Qt.WindowState.WindowNoState)               # the owner makes it a window
        window.resize(1000, 700)
        settle(4)
        margins = window.layout().contentsMargins()
        self.assertEqual((margins.left(), margins.top(), margins.right(), margins.bottom()), ui._READING_MARGINS)
        window.show_reading_view()                                        # shown again: the owner's size holds
        settle()
        self.assertFalse(window.isMaximized())

    def test_f11_is_full_screen_and_back(self) -> None:
        self.make()
        window = self.window
        window.activateWindow()
        QApplication.setActiveWindow(window)
        settle()
        QTest.keyClick(window, Qt.Key.Key_F11)
        settle(4)
        self.assertTrue(window.isFullScreen())
        QTest.keyClick(window, Qt.Key.Key_F11)
        settle(4)
        self.assertFalse(window.isFullScreen())
        self.assertFalse(window.isMaximized())                            # back to what it was

    def test_it_still_resizes_down_to_the_minimum(self) -> None:
        self.make()
        window = self.window
        window.resize(ui.READING_MIN_SIZE)
        settle(4)
        self.assertLessEqual(window.width(), ui.READING_MIN_SIZE.width())
        self.assertLessEqual(window.height(), ui.READING_MIN_SIZE.height())
        page = self.reading.canvas_page()
        bar = self.reading.command_bar
        box = bar.field.mapTo(page, bar.field.rect().topLeft())
        self.assertGreaterEqual(box.x(), 0)
        self.assertLessEqual(box.x() + bar.field.width(), page.width())   # the ask line inside at the minimum

    def test_no_f11_and_no_maximise_without_the_canvas(self) -> None:
        self.make(extra="\n[look]\nboard = false\n")
        window = self.window
        window.toggle_fullscreen()
        window.toggle_maximized()
        settle()
        self.assertFalse(window.isFullScreen() or window.isMaximized())
        self.assertFalse(window.start_drag())


if __name__ == "__main__":
    unittest.main()
