"""Jarvis filling in pages, part 2: the driving window (agent.browser with drive=True, snapshot_engine's drive sandbox
and seal, snapshot_gate's cut).

Most of it runs the real web engine, each case in a process of its own (tests/drive_browser_probe.py), so QtWebEngine
never starts inside the shared unittest process. LOCAL PAGES ONLY, or a listener or a little site of the probe's own on
loopback standing where the network gate's servers would be: nothing ever reaches a real site. The rest (the window's
chips and ring, the tabs' rules, the seal's order, the client's seam) needs no engine. Skipped when this PySide6 has no
QtWebEngine 6.8+.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest import mock

from PySide6.QtCore import QEventLoop, QObject, QRect, QRectF, Qt, QTimer, QUrl
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import hud_overlay, snapshot_engine, snapshots
from briefing_reader.agent import browser as agent_browser
from briefing_reader.agent import drive, web
from briefing_reader.agent.browser import WatchWindow, big_rect
from tests.ui_fakes import fonts

PROBE = Path(__file__).with_name("drive_browser_probe.py")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASES = ("drive_local", "drive_gate", "drive_send", "drive_fixes")
REPORTS: dict[str, dict] = {}
FAILURES: dict[str, str] = {}
_app: QApplication | None = None


def _run(case: str) -> tuple[str, dict | None, str]:
    env = {key: value for key, value in os.environ.items() if key not in ("QTWEBENGINE_DISABLE_SANDBOX",
                                                                          "QTWEBENGINE_CHROMIUM_FLAGS")}
    env.update(QT_QPA_PLATFORM="offscreen", JARVIS_DESKTOP_TEST_GUARD="1", PYTHONIOENCODING="utf-8")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(snapshots.OFFLINE_FLAGS)
    try:
        # sys.executable is the interpreter itself, never the py launcher.
        done = subprocess.run([sys.executable, "-B", str(PROBE), case], cwd=PROJECT_ROOT, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=170)
    except subprocess.TimeoutExpired:
        return case, None, "the probe did not finish within 170 s"
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    if done.returncode != 0 or not lines:
        tail = "\n".join(line for line in done.stderr.splitlines() if "gpu_channel_manager" not in line)[-2000:]
        return case, None, f"rc={done.returncode}\n{tail}"
    return case, json.loads(lines[-1]), ""


def setUpModule() -> None:
    global _app
    _app = QApplication.instance() or QApplication(["test_agent_drive_browser", "-platform", "offscreen"])
    fonts()
    if not snapshot_engine.webengine_available():
        return
    with ThreadPoolExecutor(max_workers=2) as pool:
        for case, report, failure in pool.map(_run, CASES):
            if report is not None:
                REPORTS[case] = report
            else:
                FAILURES[case] = failure
    for case in list(FAILURES):                # a machine busy with the whole suite: once more, alone
        case, report, failure = _run(case)
        if report is not None:
            REPORTS[case] = report
            del FAILURES[case]
        else:
            FAILURES[case] = failure


def pump(seconds: float, until: Any = None) -> bool:
    """The event loop for a while (deferred deletes and timers run), or until ``until()`` holds."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if until is not None and until():
            return True
        loop = QEventLoop()
        QTimer.singleShot(10, loop.quit)
        loop.exec()
    return bool(until()) if until is not None else True


# --------------------------------------------------------------------------
# The real engine (a process per case)
# --------------------------------------------------------------------------

@unittest.skipUnless(snapshot_engine.webengine_available(), "this PySide6 has no QtWebEngine 6.8+")
class ProbeCase(unittest.TestCase):
    def report(self, case: str) -> dict:
        if case in FAILURES:
            self.fail(f"probe {case} failed: {FAILURES[case]}")
        self.assertIn(case, REPORTS)
        report = REPORTS[case]
        self.assertEqual(report["platform"], "offscreen")
        self.assertEqual(report["children_end"], 0, "no renderer is left")
        return report


class FixesProbeTests(ProbeCase):
    """The review's fixes, in a real page's own scripts (drive_fixes)."""

    def fixes(self) -> dict:
        report = self.report("drive_fixes")
        self.assertEqual(report["opened"], "Fixes")
        return report

    def test_h1_what_jarvis_typed_stays_his_when_a_new_list_renumbers(self) -> None:
        report = self.fixes()
        self.assertEqual(report["renumbered"], ["e1", "e2"])          # a field came first: First is e2 now
        self.assertEqual(report["go"][1], [["First", "Ada Lovelace"]])
        self.assertEqual(report["go"][3], [])                         # and it is no "value the page set"

    def test_h3_a_submit_s_own_action_and_target_win(self) -> None:
        report = self.fixes()
        self.assertEqual(report["go"][0], "collect.example.net")
        self.assertEqual(report["go"][2], ["www.example.org", "collect.example.net"])
        self.assertEqual(report["win"], "refused:" + drive.D_OTHER_WINDOW)

    def test_m1_a_press_that_only_clicks_names_no_form(self) -> None:
        report = self.fixes()
        self.assertEqual(report["plain"][0], "")
        self.assertEqual(report["box"][0], "")
        self.assertEqual(report["plain"][2], ["www.example.org"])

    def test_m3_a_value_longer_than_the_card_refuses_it(self) -> None:
        self.assertEqual(self.fixes()["long"], "refused:" + drive.D_TOO_LONG)

    def test_m2_the_press_checks_in_its_own_task(self) -> None:
        self.assertEqual(self.fixes()["changed"], [[{"changed": 1}, False], "0"])

    def test_l3_a_form_that_does_not_go_and_one_a_script_takes_over(self) -> None:
        report = self.fixes()
        self.assertEqual(report["invalid"], [[{"ok": 1, "how": "submit", "fired": False, "prevented": False}, False],
                                             "0"])
        self.assertEqual(report["prevented"], [[{"ok": 1, "how": "submit", "fired": True, "prevented": True}, False],
                                               "held"])

    def test_l4_a_list_from_where_the_page_stands(self) -> None:
        above, names = self.fixes()["from_here"]
        self.assertGreater(above, 0)
        self.assertEqual(names, ["Low"])


class TypingProbeTests(ProbeCase):
    def test_the_controls_of_a_real_page(self) -> None:
        report = self.report("drive_local")
        self.assertEqual(report["opened"], "Sign up")
        self.assertEqual(report["controls"], [["textbox", "Your name"], ["textarea", "A note"], ["select", "Size"],
                                              ["submit", "Send it"], ["link", "More about it"]])

    def test_typing_shows_in_the_dom_and_the_window_is_sealed_first(self) -> None:
        report = self.report("drive_local")
        self.assertEqual(report["sealed_before"], [False, []])
        self.assertEqual(report["typed"], [len("Ada Lovelace"), True, ["drive"]])
        self.assertEqual(report["dom_name"], "Ada Lovelace")

    def test_while_sealed_the_page_s_own_submit_and_beacons_go_nowhere(self) -> None:
        value, out, held = self.report("drive_local")["while_sealed"]
        self.assertEqual(value, "Ada Lovelace")                       # its own submit did not take the page away
        self.assertIn("self-submit:tried", out)
        self.assertIn("beacon:refused", out)
        self.assertNotIn("beacon:LOADED", out)
        self.assertGreaterEqual(held, 2)                              # the submit and the beacons, refused and counted

    def test_a_list_s_later_option_is_chosen(self) -> None:
        self.assertEqual(self.report("drive_local")["chose"], "Large")

    def test_the_card_has_the_exact_values(self) -> None:
        card = self.report("drive_local")["card"]
        self.assertIn(["value", "Your name", "Ada Lovelace", False], card)
        self.assertIn(["value", "Size", "Large", False], card)
        self.assertEqual(card[0][:2], ["press", "Jarvis wants to press 'Send it' on www.example.org"])

    def test_don_t_destroys_the_page_before_the_seal_lifts(self) -> None:
        report = self.report("drive_local")
        self.assertTrue(report["declined"])
        self.assertEqual(report["declined_events"], [[False, False]])   # one lift, with the page's profile gone by then
        self.assertEqual(report["after_decline"], [1, [2], False, []])  # a fresh empty tab, nothing sealed

    def test_the_ring_is_over_the_field_inside_the_page(self) -> None:
        report = self.report("drive_local")
        ring = report["ring"]
        self.assertIsNotNone(ring)
        x, y, wide, tall = ring
        width, height = report["page_area"]
        self.assertGreater(wide, 20)
        self.assertGreater(tall, 5)
        self.assertTrue(0 <= x and x + wide <= width and 0 <= y and y + tall <= height, ring)

    def test_the_view_never_takes_the_focus(self) -> None:
        asked, focus_inside, any_has_focus, policies = self.report("drive_local")["focus"]
        self.assertEqual(asked, [])
        self.assertFalse(focus_inside)
        self.assertFalse(any_has_focus)
        self.assertEqual(policies, ["NoFocus"])


class TabsProbeTests(ProbeCase):
    def test_a_link_to_a_new_window_opens_as_a_new_tab(self) -> None:
        self.assertEqual(self.report("drive_local")["blank_link"], ["More", 2, [2, 3], 3])

    def test_three_tabs_and_no_fourth(self) -> None:
        report = self.report("drive_local")
        self.assertEqual(report["three"], ["Three", 3, [2, 3, 4]])
        self.assertEqual(report["four"], [f"refused:{drive.D_TABS}", 3, [2, 3, 4]])
        self.assertIs(report["fourth_at_the_window"], False)            # the window refuses one too

    def test_the_strip_names_the_tabs_the_shown_one_lit(self) -> None:
        report = self.report("drive_local")
        self.assertEqual(report["chips"], [["two.example.org", False], ["more.example.net", False],
                                           ["three.example.org", True]])
        self.assertEqual(report["switched"], [1, "Sign up", 2, [["two.example.org", True], ["more.example.net", False],
                                                                 ["three.example.org", False]]])

    def test_a_tab_not_shown_is_still_read_and_closed(self) -> None:
        report = self.report("drive_local")
        self.assertEqual(report["read_a_tab_not_shown"], "Three")
        self.assertEqual(report["closed"], [2, [2, 3], 2, [["two.example.org", True], ["more.example.net", False]]])
        self.assertEqual(report["act_no_tab"], [False, None])


class SandboxProbeTests(ProbeCase):
    def test_only_a_drive_tab_may_send_a_form(self) -> None:
        report = self.report("drive_local")
        self.assertEqual(report["auto_drive"][0], "Auto")
        self.assertGreaterEqual(report["auto_drive"][1], 1)            # its own submit reached the request rules
        self.assertEqual(report["auto_plain"], 0)                      # a research window's sandbox stops it first
        self.assertEqual(report["sandboxes"], [snapshot_engine.DRIVE_SANDBOX, snapshot_engine.SANDBOX])

    def test_no_dialog_pop_up_picker_or_download_in_a_drive_tab(self) -> None:
        report = self.report("drive_local")
        said = dict(line.split("=", 1) for line in report["hooks_text"].splitlines())
        self.assertEqual((said["open"], said["confirm"], said["prompt"]), ("null", "false", "null"))
        self.assertEqual(report["hooks"].get("downloads", 0), 0)
        self.assertEqual(report["top_levels"], ["WatchWindow"])
        self.assertTrue(report["downloads_watched"])
        self.assertEqual(report["downloads_new_entries"], [])
        self.assertFalse(report["downloads_mtime_changed"])
        self.assertEqual(report["download_folder_entries"], [])


class BigProbeTests(ProbeCase):
    def test_the_result_opens_big_never_activated_and_the_page_goes(self) -> None:
        report = self.report("drive_local")
        self.assertEqual(report["big_now"], [True, True, True])        # at once: big, the page still there, frozen
        self.assertEqual(report["big_after"], [True, True, False, True, agent_browser.FINISHED_TAG, []])
        before, screen = report["before_big"]
        grown, shown = report["big_size"]
        self.assertTrue(shown)
        self.assertGreater(grown, before)                             # it grew
        self.assertGreaterEqual(grown, int(screen * 0.6))             # to most of its screen


class GateProbeTests(ProbeCase):
    def test_tunnels_open_before_typing_are_cut_and_none_after(self) -> None:
        report = self.report("drive_gate")
        self.assertEqual(report["opened"], "Sign up")
        counts, taken, open_now = report["before"]
        self.assertGreaterEqual(counts["tunnels"], 3)
        self.assertGreaterEqual(open_now, 3)
        self.assertEqual(report["typed"], 3)
        self.assertEqual(report["sealed"], [True, True])
        self.assertTrue(report["all_cut"])
        (at_counts, at_taken), (after_counts, after_taken, after_open, held) = report["at_seal"], report["after"]
        self.assertEqual(after_counts["tunnels"], at_counts["tunnels"])  # no tunnel after the seal ...
        self.assertEqual(after_taken, at_taken)                          # ... and no connection reached the server
        self.assertEqual(after_open, 0)
        self.assertGreaterEqual(after_counts["refused"], at_counts["refused"])
        self.assertGreaterEqual(held, 1)
        self.assertEqual(report["dom_name"], "Ada")

    def test_the_seal_lifts_once_the_page_is_gone_and_the_gate_works_again(self) -> None:
        report = self.report("drive_gate")
        closed, events, gate_sealed, drive_sealed = report["closed"]
        self.assertEqual(closed, 1)
        self.assertEqual(events, [[False, False]])
        self.assertEqual((gate_sealed, drive_sealed), (False, False))
        self.assertEqual(report["again"], ["Sign up", True])


class SendProbeTests(ProbeCase):
    def test_nothing_reaches_the_site_before_the_owner_s_send(self) -> None:
        report = self.report("drive_send")
        self.assertTrue(report["loaded"])
        hits, refused, held, seals, cuts = report["while_sealed"]
        self.assertEqual(hits, [])
        self.assertGreaterEqual(held, 2)
        self.assertEqual(seals, [True])
        self.assertEqual(cuts, 1)
        self.assertEqual(report["post_while_sealed"], [[], True])

    def test_after_send_the_get_reaches_the_site_with_the_values_typed(self) -> None:
        report = self.report("drive_send")
        self.assertEqual(report["get_outcome"], [drive.S_SENT, "Thanks"])
        self.assertEqual([hit[:2] for hit in report["get_hits"]], [["GET", "/got"]])
        values = report["get_values"]
        self.assertEqual(values["name"], ["Ada Lovelace"])
        self.assertEqual([value.replace("\r\n", "\n") for value in values["note"]], ["Line one\nLine two & more"])
        self.assertEqual(values["size"], ["Large"])
        self.assertEqual(values["token"], ["t-123"])
        self.assertEqual(report["sealed_after_get"], [False, [True, False]])
        self.assertEqual(report["allows_get"], [["www.example.org"], []])   # the Send reached the card's host alone

    def test_after_send_the_post_reaches_the_site_with_the_values_typed(self) -> None:
        report = self.report("drive_send")
        self.assertEqual(report["post_outcome"], [drive.S_SENT, "Thanks"])
        self.assertEqual(report["post_hits"], [["POST", "/posted"]])
        values = report["post_values"]
        self.assertEqual(values["name"], ["Ada Lovelace"])
        self.assertEqual([value.replace("\r\n", "\n") for value in values["note"]], ["Line one\nLine two & more"])
        self.assertEqual(values["size"], ["Large"])

    def test_a_stop_while_sealed_destroys_the_page_before_the_seal_lifts(self) -> None:
        lifted, events, before, after, no_window = self.report("drive_send")["stopped"]
        self.assertTrue(lifted)
        self.assertEqual(events, [[False, False]])
        self.assertEqual((before, after), ([], []))
        self.assertTrue(no_window)


# --------------------------------------------------------------------------
# Without the engine
# --------------------------------------------------------------------------

class SandboxAndSealTests(unittest.TestCase):
    def test_the_drive_sandbox_adds_forms_and_nothing_else(self) -> None:
        self.assertEqual(snapshot_engine.DRIVE_SANDBOX, snapshot_engine.SANDBOX + " allow-forms")
        for word in ("allow-downloads", "allow-modals", "allow-top-navigation", "allow-popups-to-escape-sandbox"):
            self.assertNotIn(word, snapshot_engine.DRIVE_SANDBOX)
        self.assertIn("'" + snapshot_engine.DRIVE_SANDBOX + "'", snapshot_engine._seed_script(snapshot_engine.DRIVE_SANDBOX))
        self.assertEqual(snapshot_engine._seed_script(snapshot_engine.SANDBOX), snapshot_engine._SEED_SCRIPT)
        wider = snapshot_engine._seed_script("allow-scripts allow-downloads")
        self.assertEqual(wider, snapshot_engine._SEED_SCRIPT)          # anything else is the plain sandbox

    def test_who_gets_which_sandbox(self) -> None:
        camera = snapshot_engine.PageCamera()
        self.addCleanup(camera.deleteLater)
        self.assertEqual(camera._sandbox(None), snapshot_engine.SANDBOX)    # research pictures
        plain = agent_browser.AgentBrowser()
        driving = agent_browser.AgentBrowser(drive=True)
        self.addCleanup(plain.deleteLater)
        self.addCleanup(driving.deleteLater)
        self.assertEqual(plain._sandbox(None), snapshot_engine.SANDBOX)
        self.assertEqual(driving._sandbox(None), snapshot_engine.DRIVE_SANDBOX)
        driving._request = agent_browser._Request("x", "", "x", object(), "t", lambda _r: None, "", 100)
        self.assertEqual(driving._sandbox(None), snapshot_engine.SANDBOX)  # a task's own file never

    def test_the_drive_seal_is_a_reason_of_its_own_and_cut_ends_connections(self) -> None:
        class Gate:
            def __init__(self) -> None:
                self.told: list[bool] = []
                self.cuts = 0

            def seal(self, sealed: bool) -> None:
                self.told.append(sealed)

            def cut(self) -> int:
                self.cuts += 1
                return 4

        state = snapshot_engine._EngineState()
        state.gate = Gate()
        with mock.patch.object(snapshot_engine, "_ENGINE", state):
            snapshot_engine.seal_drive(True)
            snapshot_engine.seal_gate(True)
            snapshot_engine.seal_gate(False)           # a page the agent wrote going lifts only its own reason
            self.assertTrue(snapshot_engine.gate_sealed())
            self.assertEqual(state.gate.told, [True])
            self.assertEqual(snapshot_engine.cut_gate(), 4)
            snapshot_engine.seal_drive(False)
            self.assertFalse(snapshot_engine.gate_sealed())
            self.assertEqual(state.gate.told, [True, False])
            state.gate = object()                      # a gate that has nothing to cut
            self.assertEqual(snapshot_engine.cut_gate(), 0)
            state.gate = None
            self.assertEqual(snapshot_engine.cut_gate(), 0)

    def test_the_send_s_hosts_go_through_only_while_the_drive_seal_is_the_one_reason(self) -> None:
        class Gate:
            def __init__(self) -> None:
                self.told: list[Any] = []

            def seal(self, sealed: bool) -> None:
                self.told.append(sealed)

            def allow(self, hosts: Any) -> None:
                self.told.append(tuple(hosts))

        state = snapshot_engine._EngineState()
        state.gate = Gate()
        with mock.patch.object(snapshot_engine, "_ENGINE", state):
            snapshot_engine.seal_drive(True)
            snapshot_engine.seal_drive_only(["www.example.org"])
            snapshot_engine.seal_gate(True)              # a page the agent wrote: nothing goes through
            snapshot_engine.seal_gate(False)
            snapshot_engine.seal_drive_only(())
            self.assertTrue(snapshot_engine.gate_sealed())
        self.assertEqual(state.gate.told, [True, ("www.example.org",), (), ("www.example.org",), ()])


class _Url:
    def __init__(self, scheme: str, host: str = "") -> None:
        self._scheme, self._host = scheme, host

    def scheme(self) -> str:
        return self._scheme

    def host(self) -> str:
        return self._host


class TabRulesTests(unittest.TestCase):
    """AgentBrowser's tabs and seal with no page at all (no engine is started)."""

    def setUp(self) -> None:
        self.state = snapshot_engine._EngineState()
        patcher = mock.patch.object(snapshot_engine, "_ENGINE", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.browser = agent_browser.AgentBrowser(drive=True)
        self.addCleanup(self.browser.shutdown)

    def ask(self, op: str, *args: Any) -> Any:
        box: list[Any] = []
        getattr(self.browser, op)(*args, done=box.append)
        pump(3, lambda: bool(box))
        return box[0] if box else "no answer"

    def test_up_to_three_tabs_each_id_once(self) -> None:
        self.assertEqual(self.browser.tabs(), (1,))
        self.assertTrue(self.ask("new_tab", 2))
        self.assertFalse(self.ask("new_tab", 2))
        self.assertTrue(self.ask("new_tab", 3))
        self.assertFalse(self.ask("new_tab", 4))
        self.assertEqual((self.browser.tabs(), self.browser.tab_shown()), ((1, 2, 3), 3))
        self.assertTrue(self.ask("switch_tab", 1))
        self.assertFalse(self.ask("switch_tab", 9))
        self.assertEqual(self.browser.tab_shown(), 1)

    def test_closing_the_shown_tab_shows_the_last_one(self) -> None:
        self.ask("new_tab", 2)
        self.ask("new_tab", 3)
        self.ask("switch_tab", 2)
        self.assertTrue(self.ask("close_tab", 2, False))
        self.assertEqual((self.browser.tabs(), self.browser.tab_shown()), ((1, 3), 3))
        self.assertTrue(self.ask("close_tab", 9, False))             # gone already: nothing to do

    def test_an_act_on_no_tab_or_an_empty_one(self) -> None:
        self.assertEqual(self.ask("act", 7, "1", 0.0, 1.0), drive.ActResult(ok=False))
        self.assertEqual(self.ask("act", 1, "1", 0.0, 1.0), drive.ActResult(web.NO_PAGE))

    def test_sealed_no_web_page_loads_and_nothing_of_a_page_goes(self) -> None:
        self.assertFalse(self.browser._held_out(_Url("https")))
        self.assertTrue(self.ask("seal", True))
        self.assertTrue(self.browser.drive_sealed())
        self.assertIn(snapshot_engine.SEAL_DRIVE, self.state.seals)
        for scheme, held in (("https", True), ("http", True), ("wss", True), ("about", False), ("data", False),
                             ("blob", False)):
            self.assertEqual(self.browser._held_out(_Url(scheme)), held, scheme)
        result = self.ask("load", "https://www.example.org/", 5.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, snapshots.R_LOAD_FAILED)
        self.assertEqual(self.browser.hook_counts().get("held"), 1)

    def test_the_seal_lifts_only_once_what_went_while_sealed_is_deleted(self) -> None:
        self.ask("seal", True)
        doomed = QObject()
        self.browser._sealed_doomed.append(doomed)              # as a destroyed page's profile, not deleted yet
        box: list[Any] = []
        self.browser.close_tab(1, True, done=box.append)
        pump(0.2)
        self.assertEqual(box, [])
        self.assertTrue(self.browser.drive_sealed())
        self.assertIn(snapshot_engine.SEAL_DRIVE, self.state.seals)
        doomed.deleteLater()
        self.assertTrue(pump(3, lambda: bool(box)))
        self.assertEqual(box, [True])
        self.assertFalse(self.browser.drive_sealed())
        self.assertNotIn(snapshot_engine.SEAL_DRIVE, self.state.seals)

    def test_a_newer_seal_is_not_lifted_by_an_older_close(self) -> None:
        self.ask("seal", True)
        doomed = QObject()
        self.browser._sealed_doomed.append(doomed)
        box: list[Any] = []
        self.browser.close_tab(1, True, done=box.append)
        self.ask("seal", True)                                  # a new value went in meanwhile
        doomed.deleteLater()
        self.assertTrue(pump(3, lambda: bool(box)))
        self.assertTrue(self.browser.drive_sealed())

    def test_the_owner_s_send_lifts_the_seal_for_the_card_s_hosts_alone(self) -> None:
        self.ask("seal", True)
        seen: list[Any] = []
        real = snapshot_engine.seal_drive_only

        def noted(hosts: Any) -> None:
            seen.append((sorted(hosts), self.browser._held_out(_Url("https", "www.example.org")),
                         self.browser._held_out(_Url("https", "other.example.net")),
                         self.browser._held_out(_Url("wss", "www.example.org"))))
            real(hosts)

        with mock.patch.object(snapshot_engine, "seal_drive_only", noted):
            result = self.ask("press_approved", 1, "1", ("www.example.org",), 0.0, 1.0)
        self.assertEqual(result, drive.ActResult(web.NO_PAGE))
        self.assertEqual(seen[0], (["www.example.org"], False, True, True))   # while it presses: that host alone
        self.assertEqual(seen[-1][0], [])
        self.assertTrue(self.browser.drive_sealed())              # no new page came: sealed again as before
        self.assertIn(snapshot_engine.SEAL_DRIVE, self.state.seals)
        self.assertEqual(self.state.drive_hosts, frozenset())
        self.assertTrue(self.browser._held_out(_Url("https", "www.example.org")))

    def test_an_approved_press_s_page_is_never_handed_to_the_owner_s_browser(self) -> None:
        self.ask("press_approved", 1, "1", (), 0.0, 1.0)
        self.browser._addresses[1] = "https://www.example.org/sent?name=Ada"
        self.assertEqual(self.browser.open_address(), "")
        self.browser._end_tabs()
        self.assertEqual(self.browser.open_address(), "")

    def test_the_run_s_end_lifts_the_seal_once_the_pages_are_gone(self) -> None:
        self.ask("seal", True)
        self.browser.close_all()
        self.assertTrue(pump(3, lambda: not self.browser.drive_sealed()))


class RingTargetTests(unittest.TestCase):
    def test_the_element_a_drive_script_acts_on(self) -> None:
        self.assertEqual(agent_browser._ring_target(drive.type_script("g1", 3, "x")), ("g1", 3))
        self.assertEqual(agent_browser._ring_target(drive.choose_script("g1", 2, "a")), ("g1", 2))
        self.assertEqual(agent_browser._ring_target(drive.click_script("g1", 4)), ("g1", 4))
        self.assertEqual(agent_browser._ring_target(drive.press_script("g1", 5)), ("g1", 5))
        self.assertEqual(agent_browser._ring_target(drive.scroll_script("g1", "", 6)), ("g1", 6))
        for source in (drive.scroll_script("g1", "down"), drive.controls_script("g1"), drive.form_script("g1", 1, [2]),
                       web.read_script(), "", "/*drive:type*/\nvar ARGS = {bad;\n"):
            self.assertIsNone(agent_browser._ring_target(source))


class BigRectTests(unittest.TestCase):
    def test_four_fifths_of_the_work_area_in_its_middle(self) -> None:
        area = QRect(0, 0, 1920, 1040)
        rect = big_rect(area)
        self.assertEqual((rect.width(), rect.height()), (1536, 832))
        self.assertEqual(rect.center().x(), area.center().x())
        for area in (QRect(-1280, 100, 1280, 700), QRect(0, 0, 3, 3), QRect(100, 50, 640, 400)):
            self.assertTrue(area.contains(big_rect(area)))
            self.assertTrue(area.contains(big_rect(area, QRect(0, 0, 5000, 5000))))

    def test_never_smaller_than_the_window_is(self) -> None:
        area = QRect(1920, 0, 1920, 1040)
        whole = QRect(1936, 16, 1500, 1008)                        # on a screen of its own it nearly fills it
        rect = big_rect(area, whole)
        self.assertEqual((rect.width(), rect.height()), (1536, 1008))
        self.assertTrue(area.contains(rect))


class WindowChipsAndRingTests(unittest.TestCase):
    def window(self) -> WatchWindow:
        window = WatchWindow(native=hud_overlay.NoNative())
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        window.resize(900, 500)
        return window

    def test_one_tab_names_the_page_as_ever_more_are_chips(self) -> None:
        window = self.window()
        window.set_page("www.example.org")
        window.set_tabs([("www.example.org", True)])
        plain = window.grab().toImage()
        window.set_tabs([("www.example.org", False), ("x" * 300 + ".example.net", True), ("", False)])
        self.assertEqual(window.tab_chips(), (("www.example.org", False), ("x" * 80, True), ("", False)))
        chips = window.grab().toImage()
        self.assertNotEqual(plain, chips)
        self.assertEqual(window.cut_texts(), [])

    def test_the_ring_is_drawn_over_the_page_and_takes_nothing(self) -> None:
        window = self.window()
        self.assertIsNone(window.ring_rect())
        window.ring(QRectF(10, 20, 200, 30))
        self.assertEqual(window.ring_rect(), QRectF(10, 20, 200, 30))
        ring = window._ring
        self.assertTrue(ring.isVisibleTo(window))
        self.assertTrue(ring.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        for child in window.findChildren(QWidget):
            self.assertEqual(child.focusPolicy(), Qt.FocusPolicy.NoFocus)
        window.ring(QRectF())
        self.assertIsNone(window.ring_rect())
        window.ring(None)
        self.assertFalse(ring.isVisibleTo(window))


class ClientSeamTests(unittest.TestCase):
    """BrowserClient as agent.drive's DriveBrowser, over a stand-in browser (no engine)."""

    class Stand(QObject):
        def __init__(self, parent: QObject) -> None:
            super().__init__(parent)
            self.calls: list[tuple] = []
            self.stopped = 0

        def new_tab(self, tab: int, done: Any) -> None:
            self.calls.append(("new_tab", tab))
            done(True)

        def switch_tab(self, tab: int, done: Any) -> None:
            self.calls.append(("switch_tab", tab))
            done(tab == 1)

        def close_tab(self, tab: int, unseal: bool, done: Any) -> None:
            self.calls.append(("close_tab", tab, unseal))
            done(True)

        def seal(self, cut: bool, done: Any) -> None:
            self.calls.append(("seal", cut))
            done(True)

        def act(self, tab: int, source: str, settle_s: float, timeout_s: float, done: Any) -> None:
            self.calls.append(("act", tab, settle_s))
            done(drive.ActResult('{"ok": 1}', True, "https://www.example.org/next"))

        def press_approved(self, tab: int, source: str, hosts: Any, settle_s: float, timeout_s: float,
                           done: Any) -> None:
            self.calls.append(("press_approved", tab, hosts))
            done("not an ActResult")

        def stop_now(self) -> None:
            self.stopped += 1

        def end_run(self, linger: bool, hold: bool = False, big: bool = False) -> None:
            self.calls.append(("end_run", linger, hold, big))

    def test_each_op_reaches_the_gui_thread_and_answers(self) -> None:
        stands: list[Any] = []

        def factory(parent: QObject) -> Any:
            stands.append(self.Stand(parent))
            return stands[-1]

        client = agent_browser.BrowserClient(factory=factory, poll_s=0.005)
        self.addCleanup(client.deleteLater)
        box: dict[str, Any] = {}

        def work() -> None:
            never = lambda: False  # noqa: E731
            box["new"] = client.new_tab(2, timeout_s=2, cancelled=never)
            box["switch"] = client.switch_tab(5, timeout_s=2, cancelled=never)
            box["close"] = client.close_tab(2, unseal=True, timeout_s=2, cancelled=never)
            box["seal"] = client.seal(True, timeout_s=2, cancelled=never)
            box["act"] = client.act(1, "x", settle_s=0.5, timeout_s=2, cancelled=never)
            box["press"] = client.press_approved(1, "x", ["www.example.org"], settle_s=25, timeout_s=2,
                                                 cancelled=never)
            box["done"] = True

        threading.Thread(target=work, daemon=True).start()
        self.assertTrue(pump(10, lambda: "done" in box))
        self.assertEqual((box["new"], box["switch"], box["close"], box["seal"]), (True, False, True, True))
        self.assertEqual(box["act"], drive.ActResult('{"ok": 1}', True, "https://www.example.org/next"))
        self.assertEqual(box["press"], drive.ActResult(ok=False))
        self.assertEqual(stands[0].calls[:6], [("new_tab", 2), ("switch_tab", 5), ("close_tab", 2, True),
                                               ("seal", True), ("act", 1, 0.5),
                                               ("press_approved", 1, ("www.example.org",))])
        client.end_run(linger=True, hold=True, big=True)
        self.assertEqual(stands[0].calls[-1], ("end_run", True, True, True))

    def test_a_drive_step_given_up_on_leaves_the_page_to_its_own_limit(self) -> None:
        stand = self.Stand(None)
        client = agent_browser.BrowserClient(factory=lambda parent: stand)
        self.addCleanup(client.deleteLater)
        client._browser = stand
        for op, stops in (("act", 0), ("press_approved", 0), ("seal", 0), ("script", 1)):
            job = agent_browser._Job(op, ())
            client._dropped(job)
            self.assertEqual(stand.stopped, stops, op)


if __name__ == "__main__":
    unittest.main()
