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
from briefing_reader import orb as orb_mod
from briefing_reader.board import protocol, view
from tests import board_fakes
from tests.ui_fakes import B_CALENDAR, B_TODO, AppHarness, fonts, settle, wait_for

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
                    dict(CANVAS, hold="x" * 25), dict(CANVAS, rec="x" * 41),
                    dict(CANVAS, wake="x" * (protocol.WAKE_CAP + 1)),
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

    def test_the_list_is_the_buttons_and_five_more(self) -> None:
        # (part 2 added the corner's Tidy and the task card's Stop to part 1's undo, stop_rec and drag)
        self.assertEqual(set(protocol.GO_WORDS) - set(protocol.CANVAS_BUTTONS),
                         {"undo", "stop_rec", "drag", "tidy", "stop"})
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
             show: bool = True, resting: bool = False, **kwargs) -> AppHarness:
        self.boards: list = []
        if not resting:
            # Part 3B: the resting group "Now" goes up when the page is ready. These tests check the canvas with
            # nothing on it but what they put there; RestingGroupTests (resting=True) check the group itself.
            from briefing_reader import canvas_now

            patcher = mock.patch.object(canvas_now.NowController, "start", lambda _self: None)
            patcher.start()
            self.addCleanup(patcher.stop)
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


# --------------------------------------------------------------------------
# The canvas, part 2: the task card, the big-number frame, Tidy and Stop
# --------------------------------------------------------------------------

TASK = {"type": "task", "state": "working", "title": "Working on it", "words": "Reading your calendar", "seconds": 4,
        "steps": 1, "sources": 0, "frames": 0, "group": "", "stop": True}


class TaskMessageTests(unittest.TestCase):
    def test_a_task_message_is_rebuilt_field_by_field(self) -> None:
        checked = protocol.check_message(dict(TASK))
        self.assertEqual(checked.type, "task")
        self.assertEqual(json.loads(checked.text), TASK)

    def test_only_the_state_is_needed(self) -> None:
        clean = json.loads(protocol.check_message({"type": "task", "state": "gone"}).text)
        self.assertEqual(clean, dict(TASK, state="gone", title="", words="", seconds=0, steps=0, stop=False))

    def test_states_words_and_counts_are_checked(self) -> None:
        for state in protocol.TASK_STATES:
            protocol.check_message(dict(TASK, state=state))
        for bad in (dict(TASK, state="thinking"), dict(TASK, title="x" * 41), dict(TASK, words="x" * 61),
                    dict(TASK, seconds=-1), dict(TASK, seconds=1.5), dict(TASK, seconds=True), dict(TASK, steps=100),
                    dict(TASK, sources="2"), dict(TASK, frames=25), dict(TASK, stop=1), dict(TASK, html="<b>"),
                    dict(TASK, title=None)):
            with self.assertRaises(protocol.Refused, msg=repr(bad)):
                protocol.check_message(bad)

    def test_done_names_a_group_the_app_sent(self) -> None:
        done = dict(TASK, state="done", group="g1")
        with self.assertRaises(protocol.Refused):
            protocol.check_message(done)
        checked = protocol.check_message(done, (), ("g1",))
        self.assertEqual(checked.group_id, "g1")
        with self.assertRaises(protocol.Refused):
            protocol.check_message(dict(done, group="<g>"), (), ("g1",))

    def test_markup_in_its_words_stays_text(self) -> None:
        words = "<img src=x onerror=1>"
        self.assertEqual(json.loads(protocol.check_message(dict(TASK, words=words)).text)["words"], words)


class NumberFrameTests(unittest.TestCase):
    CHART = {"kind": "chart", "title": "Q4 budget by line", "unit": "USD", "changed": 1, "was": 2900,
             "change": "+500 Software · was 2.9k on Friday",
             "bars": [{"label": "Travel", "value": 1200}, {"label": "Software", "value": 3400}]}
    TABLE = {"kind": "table", "title": "Budget lines", "unit": "USD", "changed": 1,
             "change": "+500 Software · was 2,900 on Friday", "source": "Dana's mail, 8:12 AM",
             "columns": ["Line", "Friday", "Today"], "rows": [["Travel", 1200, 1200], ["Software", 2900, 3400]]}

    def test_a_charts_change_is_its_big_number(self) -> None:
        from briefing_reader.board import ask_frames

        chart = ask_frames.check_frame(self.CHART)
        number = ask_frames.number_frame(chart)
        self.assertEqual((number.kind, number.title, number.meta), ("number", "Software", "USD"))
        self.assertEqual(number.body, {"figure": "+500", "text": "Was 2,900, now 3,400.", "source": "Q4 budget by line"})
        self.assertEqual(number.key, "number:" + chart.key)
        message = ask_frames.build(number, frame_id="n1").messages[0]
        protocol.check_message(message)                                   # the board takes it

    def test_a_fall_and_no_change_read_right(self) -> None:
        from briefing_reader.board import ask_frames

        fall = ask_frames.number_frame(ask_frames.check_frame(dict(self.CHART, was=4000)))
        self.assertEqual(fall.body["figure"], "−600")
        self.assertEqual(fall.body["text"], "Was 4,000, now 3,400.")
        same = ask_frames.number_frame(ask_frames.check_frame(dict(self.CHART, was=3400)))
        self.assertEqual(same.body["figure"], "0")
        half = ask_frames.number_frame(ask_frames.check_frame(dict(
            self.CHART, was=2.9, bars=[{"label": "A", "value": 1}, {"label": "B", "value": 3.4}])))
        self.assertEqual(half.body["figure"], "+0.5")

    def test_a_chart_without_its_old_value_takes_the_delta_lines_figure(self) -> None:
        from briefing_reader.board import ask_frames

        data = {key: value for key, value in self.CHART.items() if key != "was"}
        number = ask_frames.number_frame(ask_frames.check_frame(data))
        self.assertEqual(number.body["figure"], "+500")
        self.assertEqual(number.body["text"], "Was 2.9k on Friday")
        no_figure = ask_frames.number_frame(ask_frames.check_frame(dict(data, change="Software went up")))
        self.assertIsNone(no_figure)

    def test_a_tables_changed_row_is_its_big_number(self) -> None:
        from briefing_reader.board import ask_frames

        number = ask_frames.number_frame(ask_frames.check_frame(self.TABLE))
        self.assertEqual((number.title, number.meta), ("Software", "USD"))
        self.assertEqual(number.body, {"figure": "+500", "text": "Was 2,900 on Friday", "source": "Dana's mail, 8:12 AM"})
        bare = ask_frames.number_frame(ask_frames.check_frame(dict(self.TABLE, change="+500")))
        self.assertEqual(bare.body["text"], "2,900 · 3,400")              # no words after the figure: the row's cells
        self.assertIsNone(ask_frames.number_frame(ask_frames.check_frame(dict(self.TABLE, change="Software rose"))))

    def test_nothing_changed_no_big_number(self) -> None:
        from briefing_reader.board import ask_frames

        for data in (dict(self.CHART, changed=-1), dict(self.TABLE, changed=-1),
                     {"kind": "timeline", "title": "Dates", "nodes": [{"when": "Mon", "words": "x", "state": ""}]}):
            data = {key: value for key, value in data.items() if not (key in ("was", "change") and data.get("changed") == -1)}
            self.assertIsNone(ask_frames.number_frame(ask_frames.check_frame(data)))

    def test_the_number_body_is_checked(self) -> None:
        def frame_of(body: dict) -> dict:
            return {"type": "frame", "id": "n1", "kind": "number", "title": "Software", "body": body}

        protocol.check_message(frame_of({"figure": "+500", "text": "Was 2,900, now 3,400.", "source": "Dana"}))
        for body in ({"figure": "<b>5</b>", "text": "", "source": ""}, {"figure": "", "text": "", "source": ""},
                     {"figure": "+500", "text": "", "source": "", "href": "x"}, {"figure": "+500", "text": ""},
                     {"figure": "five", "text": "", "source": ""}, {"figure": "+500", "text": "x" * 81, "source": ""},
                     {"figure": 500, "text": "", "source": ""}):
            with self.assertRaises(protocol.Refused, msg=repr(body)):
                protocol.check_message(frame_of(body))

    def test_a_folded_big_number_says_its_figure(self) -> None:
        from briefing_reader.board import facts

        self.assertEqual(facts.fact_of({"kind": "number", "title": "Software", "status": "ok",
                                        "body": {"figure": "+500", "text": "", "source": ""}}), "+500")


class _Label:
    def __init__(self, text: str) -> None:
        self._text = text

    def full_text(self) -> str:
        return self._text


class _Card:
    """An Approvals card as the queue reads it (hud.ActionCard's kind label, title, status and actionable)."""

    def __init__(self, kind: str, title: str, status: str = hud.CARD_PENDING, actionable: bool = True) -> None:
        self.kind_label, self._title, self._status, self.actionable = _Label(kind), title, status, actionable

    def title(self) -> str:
        return self._title

    def status(self) -> str:
        return self._status


class RestingFramesTests(unittest.TestCase):
    """Part 3B: the "waits for your OK" frame and the Agents frame, built only from what the app holds (board.resting)
    and checked by the board's protocol; the page mirrors their caps and states."""

    def setUp(self) -> None:
        from briefing_reader import approvals_view
        from briefing_reader.board import resting

        self.resting, self.waiting = resting, approvals_view.WAITING

    def test_nothing_waiting_no_frame_and_one_waiting_its_card(self) -> None:
        resting = self.resting
        self.assertIsNone(resting.from_approvals(0, [], frame_id="r1"))
        self.assertEqual(resting.approval_rows([_Card("REPLY · WORK", "Re: Budget", hud.CARD_SENT)], self.waiting), [])
        rows = resting.approval_rows([_Card("REPLY · WORK", "Re: Budget numbers"),
                                      _Card("TO-DO", "Not a proposal", actionable=False),
                                      _Card("EMAIL · WORK", "Sent already", hud.CARD_SENT),
                                      _Card("EVENT · SCHOOL", "Needs its sign-in", hud.CARD_SIGNIN)], self.waiting)
        self.assertEqual(rows, [("REPLY · WORK", "Re: Budget numbers"), ("EVENT · SCHOOL", "Needs its sign-in")])
        message = resting.from_approvals(2, rows, frame_id="r1").messages[0]
        checked = json.loads(protocol.check_message(message).text)
        self.assertEqual((checked["kind"], checked["title"], checked["meta"]),
                         ("approvals", "Waits for your OK", "2 waiting"))
        self.assertEqual(checked["body"], {"count": 2, "rows": [{"kind": "REPLY · WORK", "title": "Re: Budget numbers"},
                                                                {"kind": "EVENT · SCHOOL", "title": "Needs its sign-in"}]})
        self.assertEqual(checked["waits"], "")            # amber by its kind: no chip, its one button opens Approvals

    def test_many_waiting_the_first_few_and_the_count(self) -> None:
        resting = self.resting
        cards = [("REPLY · WORK", f"Card {n} " + "t" * 70) for n in range(30)]
        message = resting.from_approvals(31, cards, frame_id="r1").messages[0]
        body = json.loads(protocol.check_message(message).text)["body"]
        self.assertEqual(body["count"], 31)                # the desktop HUD's waiting card counts too
        self.assertEqual(len(body["rows"]), resting.APPROVALS_ROWS)
        self.assertTrue(all(len(row["title"]) <= protocol.TITLE_CAP and row["title"].endswith("…")
                            for row in body["rows"]))
        self.assertEqual(json.loads(protocol.check_message(
            resting.from_approvals(5000, cards, frame_id="r1").messages[0]).text)["body"]["count"], 999)
        fewer = resting.from_approvals(1, cards[:3], frame_id="r1").messages[0]     # a count behind its rows: the rows
        self.assertEqual(fewer["body"]["count"], 3)

    def test_words_from_outside_are_cleaned_and_kept_as_text(self) -> None:
        resting = self.resting
        message = resting.from_approvals(1, [("<b>KIND</b>‮", "Pay <script>x</script>")], frame_id="r1").messages[0]
        row = json.loads(protocol.check_message(message).text)["body"]["rows"][0]
        self.assertEqual(row, {"kind": "<b>KIND</b><U+202E>", "title": "Pay <script>x</script>"})
        agents = resting.from_agents([resting.AgentLine("<img src=x>​", "on")], frame_id="r2").messages[0]
        self.assertEqual(json.loads(protocol.check_message(agents).text)["body"]["rows"][0]["name"],
                         "<img src=x><U+200B>")

    def test_no_agents_no_frame(self) -> None:
        self.assertIsNone(self.resting.from_agents([], frame_id="r2"))
        with self.assertRaises(ValueError):
            self.resting.from_agents([], frame_id="not an id")

    def test_agents_their_states_runs_and_what_waits(self) -> None:
        resting = self.resting
        lines = [resting.AgentLine("Morning mail sweep", "on", last="8:00 AM", came=2, next="Tomorrow 8:00 AM", waits=1),
                 resting.AgentLine("Invoices to the sheet", "paused", last="Mon 9:41 AM", came=0),
                 resting.AgentLine("Club digest", "off", came=4),               # never ran: nothing came of it
                 resting.AgentLine("Odd one", "evil", last="9:00 AM", came=True, waits=-3)]
        message = resting.from_agents(lines, frame_id="r2").messages[0]
        checked = json.loads(protocol.check_message(message).text)
        self.assertEqual((checked["kind"], checked["title"], checked["meta"]), ("agents", "Agents", "1 on"))
        self.assertEqual(checked["body"], {"more": 0, "waits": 1, "rows": [
            {"name": "Morning mail sweep", "state": "on", "last": "8:00 AM", "came": 2, "next": "Tomorrow 8:00 AM",
             "waits": 1},
            {"name": "Invoices to the sheet", "state": "paused", "last": "Mon 9:41 AM", "came": 0, "next": "",
             "waits": 0},
            {"name": "Club digest", "state": "off", "last": "", "came": -1, "next": "", "waits": 0},
            {"name": "Odd one", "state": "off", "last": "9:00 AM", "came": -1, "next": "", "waits": 0}]})
        self.assertEqual(resting.from_agents([lines[2]], frame_id="r2").messages[0]["meta"], "None on")

    def test_many_agents_eight_rows_and_the_rest_counted(self) -> None:
        resting = self.resting
        lines = [resting.AgentLine(f"Agent {n} " + "n" * 70, "on", last="8:00 AM", came=1, waits=1) for n in range(12)]
        body = json.loads(protocol.check_message(resting.from_agents(lines, frame_id="r2").messages[0]).text)["body"]
        self.assertEqual((len(body["rows"]), body["more"], body["waits"]), (protocol.ROWS_CAP, 4, 12))
        self.assertTrue(all(len(row["name"]) <= protocol.TITLE_CAP for row in body["rows"]))

    def test_the_two_bodies_are_checked(self) -> None:
        def frame_of(kind: str, body: dict) -> dict:
            return {"type": "frame", "id": "r9", "kind": kind, "title": "T", "body": body}

        row = {"name": "A", "state": "on", "last": "", "came": 0, "next": "", "waits": 0}
        protocol.check_message(frame_of("approvals", {"count": 1, "rows": []}))
        protocol.check_message(frame_of("agents", {"rows": [row], "more": 0, "waits": 0}))
        for kind, body in (
                ("approvals", {"count": 0, "rows": []}),
                ("approvals", {"count": 1, "rows": [{"kind": "K", "title": "T"}] * 2}),
                ("approvals", {"count": 9, "rows": [{"kind": "K", "title": "T"}] * 9}),
                ("approvals", {"count": 1, "rows": [{"kind": "K" * 41, "title": "T"}]}),
                ("approvals", {"count": 1, "rows": [{"kind": "K", "title": "T", "href": "x"}]}),
                ("approvals", {"count": True, "rows": []}),
                ("approvals", {"rows": []}),
                ("agents", {"rows": [], "more": 0, "waits": 0}),
                ("agents", {"rows": [dict(row, state="evil")], "more": 0, "waits": 0}),
                ("agents", {"rows": [dict(row, came=-2)], "more": 0, "waits": 0}),
                ("agents", {"rows": [dict(row, came=1.0)], "more": 0, "waits": 0}),
                ("agents", {"rows": [dict(row, waits=3)], "more": 0, "waits": 2}),
                ("agents", {"rows": [dict(row, last="x" * 25)], "more": 0, "waits": 0}),
                ("agents", {"rows": [dict(row, onclick="x")], "more": 0, "waits": 0}),
                ("agents", {"rows": [row], "waits": 0})):
            with self.assertRaises(protocol.Refused, msg=repr(body)):
                protocol.check_message(frame_of(kind, body))

    def test_the_page_mirrors_the_caps_and_states_and_asks_only_the_corner_words(self) -> None:
        import re
        from pathlib import Path

        js = (Path(view.__file__).parent / "assets" / "board.js").read_text(encoding="utf-8")
        found = re.search(r"var RESTING = \{approvals: (\d+), count: (\d+)\};", js)
        self.assertEqual((int(found.group(1)), int(found.group(2))),
                         (protocol.APPROVALS_MAX, protocol.RESTING_COUNT_MAX))
        states = re.search(r"var AGENT_STATES = \[([^\]]*)\]", js).group(1)
        self.assertEqual(tuple(re.findall(r"'(\w+)'", states)), protocol.AGENT_STATES)
        self.assertIn("restingFoot('', 'approvals', WORDS.openApprovals, true)", js)
        self.assertIn("'agents', WORDS.openAgents, false)", js)
        self.assertTrue({"approvals", "agents"} <= set(protocol.GO_WORDS))
        self.assertEqual(protocol.WHEN_CAP, 24)            # (the page checks an agent's times against TIMELINE.when)
        self.assertIn("var TIMELINE = {nodes: 8, when: 24,", js)

    def test_a_folded_resting_frame_says_its_count(self) -> None:
        from briefing_reader.board import facts

        resting = self.resting
        self.assertEqual(facts.fact_of(resting.from_approvals(3, [], frame_id="r1").messages[0]), "3 waiting")
        self.assertEqual(facts.fact_of(resting.from_agents([resting.AgentLine("A", "on")], frame_id="r2")
                                       .messages[0]), "1 on")


class FakeAsk:
    """A running request as the task card reads it (no thread, no model)."""

    def __init__(self) -> None:
        self.busy = True
        self.running_mode = ""
        self.at = ""

    def stage(self) -> str:
        return self.at


class TaskCardTests(CanvasApp):
    def begin(self) -> tuple[board_fakes.StandInBoard, FakeAsk, list]:
        self.make()
        board = self.started()
        fake = FakeAsk()
        self.canvas()._ask = lambda: fake                                  # noqa: SLF001 - the harness's stand-in
        self.reading.command_bar.set_running(True)
        ticks = [100.0]
        self.canvas()._clock = lambda: ticks[0]                            # noqa: SLF001
        return board, fake, ticks

    def test_an_ask_shows_the_card_in_the_apps_own_words(self) -> None:
        board, fake, ticks = self.begin()
        self.c._look_call("on_answer_started", 7, "What did Dana send, <b>sir</b>?")
        card = self.last("task")
        self.assertEqual((card["state"], card["title"], card["words"]), ("working", "Working on it", "Starting"))
        self.assertEqual((card["steps"], card["sources"], card["frames"], card["group"]), (0, 0, 0, ""))
        self.assertTrue(card["stop"])
        self.assertNotIn("Dana", json.dumps(board.of_type("task")))       # the owner's own words never go to it

    def test_its_words_follow_the_stage_and_its_steps_count_the_finished_ones(self) -> None:
        from briefing_reader import ask_ui
        from briefing_reader.ask import planner

        board, fake, ticks = self.begin()
        self.canvas().on_answer_started(7)
        for count, stage in enumerate((planner.STAGE_CONTEXT, planner.STAGE_PLANNING, planner.STAGE_MAIL)):
            fake.at = stage
            ticks[0] += 2
            self.canvas().sync()
            card = self.last("task")
            self.assertEqual(card["words"], ask_ui.STAGE_TEXTS[stage].rstrip("."))
            self.assertEqual(card["steps"], count)
            self.assertEqual(card["seconds"], 2 * (count + 1))
        self.assertEqual(self.last("task")["words"], "Searching your mail")

    def test_it_is_sent_only_when_it_differs(self) -> None:
        board, fake, ticks = self.begin()
        self.canvas().on_answer_started(7)
        sent = len(board.of_type("task"))
        for _ in range(4):
            ticks[0] += 0.5                                               # the seconds alone: the page counts them
            self.canvas().sync()
        self.assertEqual(len(board.of_type("task")), sent)

    def test_a_planner_mode_heads_it_with_its_own_words(self) -> None:
        from briefing_reader import ask_ui
        from briefing_reader.ask.modes import MODE_PLAN

        board, fake, ticks = self.begin()
        fake.running_mode = MODE_PLAN
        self.canvas().on_answer_started(7)
        self.assertEqual(self.last("task")["title"], ask_ui.MODE_STATUS[MODE_PLAN].rstrip("."))

    def test_the_answers_frames_make_it_their_groups_head(self) -> None:
        board, fake, ticks = self.begin()
        self.canvas().on_answer_started(0)
        ticks[0] += 9
        fake.busy = False
        self.put(3)                                                       # the answer's frames, through the desk
        self.c.look.canvas.on_answer(0)
        card = self.last("task")
        desk = self.c.look.desk
        self.assertEqual(card["state"], "done")
        self.assertEqual(card["group"], desk.feed.groups()[0])
        self.assertEqual((card["frames"], card["seconds"], card["stop"]), (3, 9, False))
        self.assertEqual(self.canvas().counts["task:done"], 1)
        sent = len(board.of_type("task"))
        self.canvas().sync()
        self.assertEqual(len(board.of_type("task")), sent)                # nothing more of it

    def test_an_answer_with_nothing_for_the_canvas_takes_the_card_away(self) -> None:
        board, fake, ticks = self.begin()
        self.c._look_call("on_answer_started", 7, "Thanks")
        fake.busy = False
        self.c._look_call("on_ask_outcome", SimpleNamespace(ok=True, say="Any time, sir.", cards=(), research=None,
                                                             calendar_read=None, mode="", frames=()), 7)
        self.assertEqual(self.last("task")["state"], "gone")
        self.assertEqual(self.canvas().counts["task:gone:no_frames"], 1)

    def test_stop_is_the_ask_lines_own_cancel_and_only_while_a_request_runs(self) -> None:
        board, fake, ticks = self.begin()
        cancelled: list[int] = []
        self.reading.command_bar.cancelRequested.connect(lambda: cancelled.append(1))
        self.canvas().on_answer_started(7)
        self.assertTrue(self.canvas().on_go("stop"))
        self.assertEqual(cancelled, [1])
        self.canvas()._on_ask_stopping(1)                                  # noqa: SLF001 - the Ask says it is stopping
        card = self.last("task")
        self.assertEqual((card["words"], card["stop"]), ("Stopping", False))
        fake.busy = False
        self.canvas()._on_ask_cancelled(1)                                 # noqa: SLF001 - ... and that it stopped
        self.assertEqual(self.last("task")["state"], "gone")
        self.reading.command_bar.set_running(False)
        self.assertFalse(self.canvas().on_go("stop"))                     # nothing runs: the word does nothing
        self.assertEqual(cancelled, [1])
        self.assertEqual(self.canvas().counts["go:unavailable"], 1)

    def test_a_request_that_ended_without_a_word_loses_its_card_after_a_moment(self) -> None:
        board, fake, ticks = self.begin()
        self.canvas().on_answer_started(7)
        fake.busy = False
        self.canvas().sync()
        self.assertEqual(self.last("task")["state"], "working")           # its answer's hook may still come
        ticks[0] += canvas_ui.TASK_GRACE_S + 0.1
        self.canvas().sync()
        self.assertEqual(self.last("task")["state"], "gone")

    def test_the_page_coming_back_gets_the_card_again(self) -> None:
        board, fake, ticks = self.begin()
        self.canvas().on_answer_started(7)
        sent = len(board.of_type("task"))
        self.canvas()._on_ready()                                          # noqa: SLF001 - the renderer came back
        self.assertEqual(len(board.of_type("task")), sent + 1)
        self.assertEqual(self.last("task")["state"], "working")

    def test_an_instant_answer_is_not_the_cards(self) -> None:
        board, fake, ticks = self.begin()
        self.canvas().on_answer_started(7)
        self.c._look_call("on_instant_answer", SimpleNamespace(working=None, speech="Four, sir."), "2 + 2")
        self.assertEqual(self.last("task")["state"], "working")


class CanvasFramesTests(CanvasApp):
    OUTCOME_FRAMES = (NumberFrameTests.CHART, NumberFrameTests.TABLE)

    def outcome(self) -> SimpleNamespace:
        from briefing_reader.board import ask_frames

        frames = tuple(ask_frames.check_frame(data) for data in self.OUTCOME_FRAMES)
        return SimpleNamespace(ok=True, say="Software went up 500, sir.", cards=(), research=None, calendar_read=None,
                               mode="", frames=frames, topic="Dana's budget")

    def test_on_the_canvas_a_change_also_puts_up_its_big_number(self) -> None:
        self.make()
        board = self.started()
        self.c._look_call("on_answer_started", 3, "What changed in the budget?")
        self.c._look_call("on_ask_outcome", self.outcome(), 3)
        settle(4)
        kinds = [message["kind"] for message in board.of_type("frame")]
        self.assertEqual(kinds, ["chart", "number", "table", "number"])   # each right after its chart or table
        numbers = [message for message in board.of_type("frame") if message["kind"] == "number"]
        self.assertEqual(numbers[0]["body"]["figure"], "+500")
        self.assertEqual(numbers[0]["title"], "Software")
        self.assertEqual(self.c.look.desk.counts["frame:number"], 2)

    def test_beside_a_conversation_no_big_number_is_built(self) -> None:
        self.make(canvas=False)
        self.c._look_call("on_answer_started", 3, "What changed in the budget?")
        self.c._look_call("on_ask_outcome", self.outcome(), 3)
        self.assertTrue(wait_for(lambda: bool(self.boards)))
        settle(4)
        self.assertEqual([message["kind"] for message in self.boards[0].of_type("frame")], ["chart", "table"])

    def test_the_same_answer_again_builds_the_number_in_its_place(self) -> None:
        self.make()
        board = self.started()
        for key in (3, 4):
            self.c._look_call("on_answer_started", key, "What changed in the budget?")
            self.c._look_call("on_ask_outcome", self.outcome(), key)
            settle(4)
        ids = {message["id"] for message in board.of_type("frame") if message["kind"] == "number"}
        self.assertEqual(len(ids), 2)                                     # two numbers, each one frame for both answers
        self.assertEqual(self.c.look.desk.feed.frames(), 4)

    def test_tidy_is_a_word_only_while_frames_show(self) -> None:
        self.make()
        self.started()
        self.assertFalse(self.canvas().on_go("tidy"))
        self.put(2)
        self.assertTrue(self.canvas().on_go("tidy"))
        self.assertEqual(self.canvas().counts["go:tidy"], 1)

    def test_the_new_words_go_through_the_bridges_check(self) -> None:
        self.assertEqual(set(protocol.GO_WORDS) - set(protocol.CANVAS_BUTTONS),
                         {"undo", "stop_rec", "drag", "tidy", "stop"})
        for word in ("tidy", "stop"):
            self.assertEqual(protocol.check_request("go", json.dumps({"to": word}), ()).to, word)

    def test_after_clear_view_the_orb_is_back_at_the_centre_while_bring_back_is_offered(self) -> None:
        self.make()
        board = self.started()
        self.put(3)
        self.assertEqual(self.last("orb")["form"], "corner")
        desk = self.c.look.desk
        desk._on_clear()                                                   # noqa: SLF001 - the page's Clear view
        settle(4)
        self.assertTrue(self.reading.desk_shown())                        # the Bring back row keeps the board up ...
        self.assertEqual(self.reading.board_count(), 0)
        self.assertEqual(self.last("orb")["form"], "rings")               # ... and the orb is back at the centre
        desk._on_restore()                                                 # noqa: SLF001 - Bring back
        settle(4)
        self.assertEqual(self.reading.board_count(), 3)
        self.assertEqual(self.last("orb")["form"], "corner")

    def test_no_cue_line_is_asked_for_on_the_canvas(self) -> None:
        self.make()
        board = self.started()
        self.put(2)
        self.c._utterance = "Frame 0 is the one, sir."
        self.c.look.desk.on_speech("Frame 0 is the one, sir.")
        settle(4)
        self.assertTrue(all(not message.get("cue", False) for message in board.of_type("lit")))


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
# Part 3: the real-screen fixes (the page's own: tests/board_probe.py "canvas_fixes")
# --------------------------------------------------------------------------

class RealScreenFixesTests(CanvasApp):
    def test_typing_in_the_ask_line_is_not_listening_on_the_canvas(self) -> None:
        self.make()
        self.started()
        reading, canvas = self.reading, self.canvas()
        reading.command_bar.typingChanged.emit(True)                      # the owner types in the ask line
        canvas.sync()
        self.assertNotEqual(self.last("orb")["state"], "listening")
        self.assertEqual(self.last("orb")["level"], 0)                    # no meter while he types
        self.assertNotEqual(self.last("canvas")["word"], orb_mod.READOUTS[orb_mod.LISTENING])
        reading.set_talking(True)                                         # his voice: that is listening
        canvas.sync()
        self.assertEqual(self.last("orb")["state"], "listening")
        reading.set_talking(False)
        reading.command_bar.typingChanged.emit(False)

    def test_the_ask_lines_status_never_repeats_what_the_caption_says(self) -> None:
        self.make()
        self.started()
        reading, bar = self.reading, self.reading.command_bar
        said = "Dana sent the budget numbers at 8:12, sir, and a reply waits for your OK."
        reading.stream_caption(said, complete=True)
        bar.set_status(said + " Software went up 500 since Friday.", hud.TONE_DONE)
        self.assertFalse(bar.status_shown())                              # the caption says it: once
        reading.captions.clear()                                          # the caption rests (the greeting) ...
        bar.refresh_status()
        self.assertFalse(bar.status_shown())                              # ... and it does not come back under it
        bar.set_status("Claude Code is not signed in: Settings shows how.", hud.TONE_ERROR)
        self.assertTrue(bar.status_shown())                               # what the caption did not say shows
        bar.set_status("Your next meeting is the study group at 1 PM.", hud.TONE_DONE)
        self.assertTrue(bar.status_shown())
        reading.say_caption("Your next meeting is the study group at 1 PM.", spoken=False)
        self.assertFalse(bar.status_shown())                              # ... until the caption says it too

    def test_a_word_or_two_only_matches_the_same_words(self) -> None:
        carried = [ui._caption_words("Done.")]                            # noqa: SLF001
        self.assertFalse(ui._words_carried(ui._caption_words("Nothing was done: the mail could not be read."), carried))
        self.assertTrue(ui._words_carried(ui._caption_words("Done"), carried))

    def test_the_wake_badge_gets_its_few_words_whole(self) -> None:
        from briefing_reader import wake_ui

        self.assertEqual(wake_ui.SHORT_CAP, protocol.WAKE_CAP)
        for words in wake_ui.SHORT_WORDS:
            self.assertLessEqual(len(words), protocol.WAKE_CAP, words)
        self.make()
        self.started()
        reading = self.reading
        reading.set_wake("Not listening: no microphone found.", wake_ui.LOOK_PROBLEM, wake_ui.SHORT_NO_MIC)
        self.canvas().sync()
        self.assertEqual(self.last("canvas")["wake"], "Not listening: no microphone")    # whole, never cut
        self.assertEqual(reading.wake_state(), ("Not listening: no microphone found.", wake_ui.LOOK_PROBLEM))
        reading.set_wake("Hey Jarvis is off", wake_ui.LOOK_OFF, wake_ui.SHORT_OFF)
        self.canvas().sync()
        self.assertEqual(self.last("canvas")["wake"], "")                 # off: no badge


CARDS = (B_CALENDAR, B_TODO)               # the harness's briefing: a calendar card and a to-do card wait


class RestingGroupTests(CanvasApp):
    """Part 3B: the resting group "Now" - up when the page is ready, built only from what the app holds (the harness's
    agenda, its briefing's two waiting cards, a to-do card), kept current with the changed light and no sound, Clear
    view clearing it, the corner's "Now" word only while it is off the canvas, never in an answer's best places."""

    def group(self) -> tuple[str, dict, list]:
        desk = self.c.look.desk
        now = self.canvas().now
        group = desk.feed.group_for(now._answer)                          # noqa: SLF001
        frames = [desk.feed.frame_message(frame_id) for frame_id in desk.feed.group_order(group)] if group else []
        return group, dict(zip([frame["kind"] for frame in frames], frames)), frames

    def test_up_when_the_page_is_ready_with_what_the_app_holds(self) -> None:
        self.make(resting=True, lines=CARDS)
        board = self.started()
        group, kinds, _frames = self.group()
        self.assertTrue(group)
        self.assertEqual(list(kinds), ["approvals", "calendar", "todos"])  # what waits first; no agents: none made
        heads = [message for message in board.of_type("group") if message["id"] == group]
        self.assertEqual(heads[-1]["topic"], "Now")
        self.assertEqual(kinds["approvals"]["body"]["count"], self.reading.needs_ok_count())
        self.assertEqual(kinds["calendar"]["title"], "Today")
        self.assertNotIn("now", self.canvas().buttons())                  # it is up: no "Now" button ...
        self.assertFalse(self.canvas().on_go("now"))                      # ... and the word does nothing
        self.assertEqual(self.canvas().form(), "corner")                  # frames on the canvas: the orb's corner

    def test_a_count_that_changes_gets_the_changed_light_and_no_sound(self) -> None:
        self.make(resting=True, lines=CARDS)
        board = self.started()
        desk, now = self.c.look.desk, self.canvas().now
        _group, kinds, _frames = self.group()
        count = kinds["approvals"]["body"]["count"]
        with mock.patch.object(desk, "sound") as sound:
            self.reading.set_desktop_waiting(3)                           # three more wait (on the desktop HUD)
            now.refresh()
            settle(2)
        _group, kinds, _frames = self.group()
        self.assertEqual(kinds["approvals"]["body"]["count"], count + 3)
        sent = [message for message in board.of_type("frame") if message["id"] == kinds["approvals"]["id"]]
        self.assertTrue(sent[-1]["changed"])                              # the changed light ...
        self.assertNotIn("on_frame_changed", [call.args[0] for call in sound.call_args_list])   # ... never its sound
        before = len(board.of_type("frame"))
        now.refresh()                                                     # the same again: nothing is sent
        self.assertEqual(len(board.of_type("frame")), before)

    def test_a_resting_frame_s_changed_tag_goes_once_its_light_has_played(self) -> None:
        # Canvas part 5A (part 3's leftover): the CHANGED tag stayed after a count changed.
        self.make(resting=True, lines=CARDS)
        board = self.started()
        desk, now = self.c.look.desk, self.canvas().now
        _group, kinds, _frames = self.group()
        frame_id = kinds["approvals"]["id"]
        self.reading.set_desktop_waiting(3)
        now.refresh()
        settle(2)
        self.assertTrue([m for m in board.of_type("frame") if m["id"] == frame_id][-1]["changed"])
        self.assertTrue(desk._pass_timer.isActive())                      # noqa: SLF001 - its light plays first
        desk._pass_timer.timeout.emit()                                    # noqa: SLF001 - (as CHANGED_PASS_MS on)
        settle(2)
        self.assertFalse([m for m in board.of_type("frame") if m["id"] == frame_id][-1]["changed"])   # no tag left
        self.assertFalse(desk.feed.passing())

    def test_a_source_with_nothing_left_takes_its_frame_away(self) -> None:
        self.make(resting=True, lines=CARDS)
        board = self.started()
        _group, kinds, _frames = self.group()
        day = kinds["calendar"]["id"]
        self.c._agenda_events = None                                       # not read or not signed in: no day frame
        self.canvas().now.refresh()
        settle(2)
        self.assertIn({"type": "remove", "id": day}, board.of_type("remove"))
        self.assertNotIn("calendar", self.group()[1])

    def test_a_day_with_nothing_booked_says_so_in_a_short_frame(self) -> None:
        from datetime import timedelta

        from briefing_reader import canvas_now
        from briefing_reader.gcal import CalendarEvent

        self.make(resting=True, lines=CARDS)
        self.started()
        now = self.canvas().now
        self.c._agenda_events = []                                         # read, signed in, nothing today
        now.refresh()
        day = self.group()[1]["calendar"]
        self.assertEqual((day["status"], day["status_text"]), ("empty", canvas_now.NOTHING_BOOKED))
        self.assertEqual(day["body"]["rows"], [])                         # no bare day bar, no "0 events"
        self.assertNotIn("event", day["meta"].lower())
        self.assertTrue(day["meta"])                                      # (its date)
        protocol.check_message(dict(day), {day["id"]}, {day["group"]})
        at = self.c.look.desk._now().replace(hour=15, minute=0, second=0, microsecond=0)   # noqa: SLF001
        self.c._agenda_events = [CalendarEvent("Study group", start=at, end=at + timedelta(hours=1))]
        now.refresh()
        day = self.group()[1]["calendar"]
        self.assertEqual(day["status"], "ok")                             # a day with an event: the frame as before
        self.assertIn("Study group", [row["title"] for row in day["body"]["rows"]])
        self.assertIn("1 event", day["meta"])

    def test_a_frame_the_owner_closed_stays_away_until_now_comes_back(self) -> None:
        self.make(resting=True, lines=CARDS)
        self.started()
        desk, now = self.c.look.desk, self.canvas().now
        todos = self.group()[1]["todos"]["id"]
        desk._on_close(todos)                                              # noqa: SLF001 - the frame's Close
        now.refresh()
        self.assertNotIn("todos", now._ids)                                # noqa: SLF001
        self.assertTrue(desk.feed.flags(todos).get("closed"))             # (it leaves after its Bring back)

    def test_clear_view_clears_it_and_the_now_button_brings_it_back(self) -> None:
        self.make(resting=True, lines=CARDS)
        self.started()
        desk, canvas = self.c.look.desk, self.canvas()
        first, _kinds, _frames = self.group()
        desk._on_clear()                                                   # noqa: SLF001 - Clear view
        settle(2)
        self.assertNotIn("now", canvas.buttons())                         # Bring back is offered: that brings it back
        desk._send(desk.feed.due(desk._clock() + desk.CLEAR_S + 1))        # noqa: SLF001 - the Clear's end
        settle(2)
        canvas.now._has_at = None                                          # noqa: SLF001 - (asked again at once)
        self.assertEqual(desk.feed.frames(), 0)
        self.assertIn("now", canvas.buttons())                            # off the canvas: its button shows ...
        canvas.sync()
        self.assertIn("now", self.last("canvas")["buttons"])
        self.assertTrue(canvas.on_go("now"))                              # ... and brings it back
        settle(2)
        again, kinds, _frames = self.group()
        self.assertNotEqual(again, first)
        self.assertEqual(list(kinds), ["approvals", "calendar", "todos"])
        self.assertNotIn("now", canvas.buttons())
        self.assertEqual(canvas.counts["go:now"], 1)

    def test_it_never_takes_an_answers_best_places(self) -> None:
        self.make(resting=True, lines=CARDS)
        board = self.started()
        desk, canvas = self.c.look.desk, self.canvas()
        now_group, _kinds, _frames = self.group()
        self.put(2)                                                       # an answer: the newest, on top
        answer_group = desk.feed.groups()[0]
        self.assertNotEqual(answer_group, now_group)
        self.assertEqual(desk.feed.groups(), [answer_group, now_group])
        self.assertTrue(desk.feed.group_header(now_group)[2])             # "Now" older: its frames fold
        # Now closed away and brought back while the answer is up: under it, the answer still the newest.
        for frame_id in desk.feed.group_order(now_group):
            desk._send(desk.feed.close(frame_id, desk._clock()))          # noqa: SLF001
        desk._send(desk.feed.due(desk._clock() + desk.CLOSE_S + 1))       # noqa: SLF001
        canvas.now._has_at = None                                          # noqa: SLF001
        self.assertTrue(canvas.on_go("now"))
        settle(2)
        back, _kinds, _frames = self.group()
        self.assertEqual(desk.feed.groups(), [answer_group, back])
        head = [message for message in board.of_type("group") if message["id"] == back][0]
        self.assertTrue(head["below"] and head["older"])                  # the page puts it under the answer
        self.assertFalse(desk.feed.group_header(answer_group)[2])         # the answer stays the newest

    def test_its_group_is_the_resting_group_the_canvas_lays_out_in_even_rows(self) -> None:
        self.make(resting=True, lines=CARDS)
        board = self.started()
        now_group, _kinds, _frames = self.group()
        self.put(2)                                                       # an answer: an ordinary group
        answer_group = self.c.look.desk.feed.groups()[0]
        self.assertNotEqual(answer_group, now_group)
        heads = {message["id"]: message for message in board.of_type("group")}
        self.assertIs(heads[now_group].get("rest"), True)                 # "Now": its frames in even rows
        self.assertNotIn("rest", heads[answer_group])                     # an answer's: never said
        for head in (heads[now_group], heads[answer_group]):
            sent = json.loads(protocol.check_message(dict(head), set(head["frames"]), set(heads)).text)
            self.assertEqual(sent.get("rest"), head.get("rest"))
        for bad in ("yes", 1, None):                                      # only a true or false flag
            with self.assertRaises(protocol.Refused):
                protocol.check_message(dict(heads[now_group], rest=bad), set(heads[now_group]["frames"]), set(heads))

    def test_the_agents_frame_when_the_owner_has_an_agent_and_its_button_works(self) -> None:
        from tests.standing_fakes import STANDING_ON, agent_data, consent, save_jobs

        def prepare(config) -> None:
            save_jobs(config, agents=[agent_data()])
            consent(config)

        self.make(resting=True, lines=CARDS, extra=STANDING_ON, prepare=prepare)
        self.started()
        self.c.standing.busy_probe = lambda: False
        self.c.standing.start_runner = lambda: True
        self.c.standing.reload(announce=False)
        self.canvas().now._agents_changed()                                # noqa: SLF001 - (read at once)
        self.canvas().now.refresh()
        settle(2)
        kinds = self.group()[1]
        self.assertEqual(list(kinds), ["approvals", "calendar", "todos", "agents"])
        self.assertEqual(kinds["agents"]["body"]["rows"][0]["name"], "Morning mail")
        self.assertIn("agents", self.canvas().buttons())                  # its button's word works
        self.assertTrue(self.canvas().on_go("agents"))
        self.assertTrue(self.canvas().on_go("approvals"))

    def test_the_new_word_goes_through_the_bridges_check(self) -> None:
        self.assertIn("now", protocol.CANVAS_BUTTONS)
        self.assertEqual(protocol.check_request("go", json.dumps({"to": "now"}), ()).to, "now")
        message = {"type": "group", "id": "g1", "time": "9:41 AM", "topic": "Now", "frames": [], "older": True,
                   "below": True}
        self.assertTrue(json.loads(protocol.check_message(message, (), ()).text)["below"])
        with self.assertRaises(protocol.Refused):
            protocol.check_message(dict(message, below="yes"), (), ())


class RestingTilesTests(unittest.TestCase):
    """Part 3B: the read-only fallback tiles show the two resting frames as plain tiles (the second screen: counts
    only)."""

    def test_plain_tiles_with_their_words_and_counts(self) -> None:
        from briefing_reader.board import glance, resting

        approvals = resting.from_approvals(5, [("REPLY · WORK", "Re: Budget numbers"),
                                               ("TO-DO", "Pay the invoice")], frame_id="r1").messages[0]
        agents = resting.from_agents([resting.AgentLine("Morning mail sweep", "on", waits=1),
                                      resting.AgentLine("Club digest", "off")], frame_id="r2").messages[0]
        words = glance.tile_layout(approvals, 400).words()
        for text in ("Waits for your OK", "5 wait for your OK", "Re: Budget numbers", "REPLY · WORK", "+3 MORE"):
            self.assertIn(text, words)
        words = glance.tile_layout(agents, 400).words()
        for text in ("Agents", "2 agents · 1 proposal waits", "Morning mail sweep", "On · 1 proposal waits",
                     "Club digest", "Not on yet"):
            self.assertIn(text, words)
        private = glance.tile_layout(agents, 400, private=True).words()
        self.assertIn("2 agents · 1 proposal waits", private)
        self.assertNotIn("Morning mail sweep", private)                     # a name never on the second screen
        self.assertNotIn("Re: Budget numbers", glance.tile_layout(approvals, 400, private=True).words())

