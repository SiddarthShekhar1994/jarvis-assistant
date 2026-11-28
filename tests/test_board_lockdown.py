"""The new look, part 3: the board's host with the real web engine. Each case runs in a process of its own
(tests/board_probe.py), so QtWebEngine never starts inside the shared unittest process; the cases run side by side.

What it proves, offscreen with Chromium's proxy at a dead address (nothing can reach the network whatever happens):
the board page is up in well under a second once the engine runs; frames whose strings are markup render as text and
load nothing; script running in the page itself (the worst case: injected into its main world) can't get out by any
way it tries - fetch, XHR, a beacon or a web socket to the web or the local network, pictures, a style sheet or a font
from elsewhere, file: and jarvis-task: addresses, a board address outside the allowlist, a new window, navigating
away, a form, an inline frame, workers from blob: or data:, the microphone and camera, notifications, the location, the
clipboard; the page never leaves the board; only the board's own files are ever allowed; its channel takes only the
list and frames the app sent, and survives a deleteLater. The control case shows the same with the page's own policy
taken out: the engine's rules for a local scheme and the request interceptor still let nothing out. The window's keys
still fire and Space never plays the briefing with the focus in the page, also after the renderer is killed; the board
falls back to its native tile, leaving no renderer, when it may not start, is not ready in time or keeps crashing; and
the page shell plays the design's states and motion (lively, calm, off) for real. Part 4's kinds (calendar, mail
thread and list, to-dos, picture, text with rows), built by board.frames, render with every word as text; the mail's
text is whole and clamped by style; the waits chip asks for go_approvals and nothing else; the picture loads nothing but
its data: JPEG and a click on it only focuses its frame; an update in place does not draw in again; the page refuses the
bodies Python refuses; and a board that fell back still shows the frames as read-only tiles. Part 5a's desk, in Jarvis's
own frameless translucent window at 1x and 1.5x: the board in the desk's board column at its place, the bar's words, two
answers' groups (newest on top, the older one quiet, the mail and its waiting reply as ONE frame first), the frames 15
px into the view and 448 wide, a grab of the window showing the board's frames, the first frame in the page's layout
(timed), reveal, a group leaving whole, Space in the page never playing the briefing, one renderer and its
memory. Part 5b's board ways, in the real page with the app's own controller and real clicks and keys: the index strip
(one button per frame in board order, the rules, the in-view brace, the waiting pip, a click scrolling to its frame),
"N more below" at its 16 px edge, the fades and the end pad, satellites (48 px, their facts, a click and Enter
expanding), parking (the page's own reading report, the pill, the dashed strip entry, the scroll holding, Show, the
quiet landing, the 220 ms room), pin and a 7th's note for 4 s, close and Bring back, All | Pinned, Clear and Bring back
and the desk folding when nothing is left, the keyboard (F6 and Shift+F6 round, the arrows, Home / End, Esc), pairs at
1232 x 640 with Left / Right, and the new messages refused by the page when hostile. Part 5c, the lit frame and open
big, with the app's own controller: Jarvis's sentence lights the frame it names - its brackets in its own colour
(AMBER_HI on amber, never cyan), its lit ground, the stub from the view's left edge to it meeting the app's native
part at the same place, both hidden while the board scrolls and back after, an arriving frame traced in LINE_FOCUS
meanwhile, all gone with the speech; the changed lap and the same words sending nothing; three across at 1500 x 960
(Left / Right in the row) and pairs at 1480 x 960; Clear with nothing pinned keeping the desk for its Bring back; a
real double click opening big (the geometry set once, the backdrop the only new picture, the frame scaling from its
rectangle to the whole view, the native dim), a resize keeping it over both columns, real Esc reversing it in order
(the scale-down, the geometry set back once, then the dim lifting), at 900 x 600 and at 1.5x. Part 5c fixes: the app's
layout following a frame's height animation and a picture's load; the hostile messages sent while the page holds the
frame they name, with a control and the reasons dropped; the page's own "laid" at the open (the still's limit at 5 s);
real Tab and Shift+Tab staying in the big frame, the board under it inert and keeping its layout (none for the wide
view, the real width's after); the big frame's new picture; a "laid" only for the step still going on (keys pressed at
the exact moment the steps cross); the backdrop opaque and dimmed as a picture. Round 2: the still lifting only once
the window shows the page laid out at its new size (a layout mark read the moment it is cleared, lively and off, open
and close), the big frame held as it is added, a "laid" too early never lifting it, a close's "laid" never answering
an open asked after its shut, and the focus going back to the board however big ends (Tab in, its group leaving for
room); the hostile list with an item only each of the page's checks catches. Round 3: a step asked while the view still
holds the other step's frame (Enter after the close's shut or as its "laid" is made ready, Esc as the big frame is
added; lively and off) lifting on its own layout and the page's last entry, with no frame of the other layout after
it; the still's beat, time limit and render hook all stopped after each lift, end_now and shutdown; the keyboard never
handed to a sibling leaving with the big frame's group; the dims moving together with the big frame's own progress,
nothing of the desk's or the pill's entrance left on what they dim. Round 4: a group leaving for room while the owner's
click left the page's focus on <body> leaves it there and the board's scroll as it was (no reading focus said; the
answer that made the room, parked while the owner read, lands once quiet), lively and off. Round 5: a closed frame's own
end with the page's expiry before the app's removal keeps the keyboard in the closed frame (no reading or focus report,
no scroll, its keys doing nothing), then a staying frame has it after remove_group and after remove, never <body>,
lively and off (the canvas-pins brief: first the keyboard on its Bring back holds the row past the app's CLOSE_S - no
expiry, no removal, the hold told the app - and moving it to the frame lets the hold go); and every probe's page
plays the probe's own motion level whatever this PC's "Show animations" says (Chromium told no reduced motion), with
one probe told reduced motion showing the page's own rule still holds. Part
6, Ask's typed frames: a model's answer with markup, scripts, javascript: and http: addresses and bidi characters in
every string, through board.ask_frames and board.protocol into the real page - only elements its renderers make,
no attribute from data in a frame's body, no request, no script run, its words whole as text; the page refusing what
Python refuses (Infinity, depth 7, 65 nodes, an unknown op ...); maths' elements made from the tree and the page's
reading of plain expressions the same as Python's (part 7 fixes: a fraction that divides read inline in brackets,
"6 ÷ (1/2)", and laid out without); no word cut at 448, 300 and 270 px (the timeline in a row from a
300 px content width, one node a row under it; maths' step names over their lines); opened big, a table's rows and a
timeline's six nodes using the room. Skipped when this PySide6 has no QtWebEngine 6.8+.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from briefing_reader import snapshot_engine, snapshots
from briefing_reader.board import view

PROBE = Path(__file__).with_name("board_probe.py")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASES = ("lockdown", "control", "media", "keys", "fallback", "shell", "kinds", "desk", "desk_x15", "ways", "lit", "big",
         "big_x15", "expiry", "reduced", "typed", "canvas", "canvas_layout", "canvas_ways", "canvas_fixes",
         "canvas_resting", "canvas_now", "canvas_hear", "canvas_5b", "capture")
# Round 5: what each probe's engine is told of reduced motion (tests/board_probe.py NO_REDUCED_MOTION, REDUCED_MOTION).
NO_REDUCED_MOTION = "--force-prefers-no-reduced-motion"
REDUCED_MOTION = "--force-prefers-reduced-motion"
# Round 5: the media probe's engine lists Chromium's stand-in devices (tests/board_probe.py FAKE_MEDIA_DEVICES), written
# out here so a dropped or misspelled switch fails the test whatever devices this PC has plugged in.
FAKE_MEDIA_DEVICES = "--use-fake-device-for-media-stream"
REPORTS: dict[str, dict] = {}
# Canvas part 5B (the walk's 4): the window's close says it closes Jarvis and that "Hey Jarvis" stops
CLOSE_JARVIS = "Close Jarvis: \u201cHey Jarvis\u201d stops (minimise keeps him listening)"
FAILURES: dict[str, str] = {}
WINDOW_KEYS = ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6", "Ctrl+7", "Ctrl+8", "Ctrl+9", "Ctrl+0",
               "Ctrl+,", "Ctrl+K", "Ctrl+Space", "Ctrl+M", "Ctrl+B"]   # Ctrl+0: Documents (build plan item 5)
NETWORK_TRIES = ("fetch_http", "fetch_https", "fetch_lan", "fetch_loopback", "xhr_http", "xhr_lan", "websocket",
                 "image_http", "image_https", "image_lan", "image_file", "image_task", "css_remote",
                 "css_outside_allowlist", "font_remote")


def _run(case: str) -> tuple[str, dict | None, str]:
    env = {key: value for key, value in os.environ.items() if key not in ("QTWEBENGINE_DISABLE_SANDBOX",
                                                                          "QTWEBENGINE_CHROMIUM_FLAGS")}
    env.update(QT_QPA_PLATFORM="offscreen", JARVIS_DESKTOP_TEST_GUARD="1", PYTHONIOENCODING="utf-8")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(snapshots.OFFLINE_FLAGS)
    env.pop("QT_SCALE_FACTOR", None)
    if case.endswith("_x15"):
        env["QT_SCALE_FACTOR"] = "1.5"           # the desk at 1.5x: the same logical layout, 1.5 device pixels a px
    try:
        # sys.executable is the interpreter itself, never the py launcher.
        done = subprocess.run([sys.executable, "-B", str(PROBE), case], cwd=PROJECT_ROOT, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return case, None, "the probe did not finish within 120 s"
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    if done.returncode != 0 or not lines:
        tail = "\n".join(line for line in done.stderr.splitlines() if "gpu_channel_manager" not in line)[-2000:]
        return case, None, f"rc={done.returncode}\n{tail}"
    return case, json.loads(lines[-1]), ""


def setUpModule() -> None:
    if not snapshot_engine.webengine_available():
        return
    with ThreadPoolExecutor(max_workers=len(CASES)) as pool:
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


@unittest.skipUnless(snapshot_engine.webengine_available(), "this PySide6 has no QtWebEngine 6.8+")
class BoardProbeCase(unittest.TestCase):
    def report(self, case: str) -> dict:
        if case in FAILURES:
            self.fail(f"probe {case} failed: {FAILURES[case]}")
        self.assertIn(case, REPORTS)
        report = REPORTS[case]
        self.assertEqual(report["platform"], "offscreen")
        self.assertTrue(report["registered"], "jarvis-board: is registered before the QApplication")
        self.assertTrue(report["webengine_flags_offline"], "Chromium runs with the dead proxy in tests")
        # Round 5: the page plays the probe's own motion level whatever this PC's "Show animations" says - the switch
        # among Jarvis's own flags in the probe's engine, while the inherited value the app judges stays accepted.
        self.assertEqual(report["motion_flag"], REDUCED_MOTION if case == "reduced" else NO_REDUCED_MOTION)
        self.assertTrue(report["motion_flag_in_engine"])
        self.assertEqual(report["inherited_flags_judged"], "")
        self.assertFalse(report["profile_warning"], "the profile always goes after its page")
        if case != "media":
            self.assertEqual(report["children_end"], 0, "no Chromium process is left")
        return report


class LockdownTests(BoardProbeCase):
    def test_the_board_page_is_ready_soon_after_the_engine_starts(self) -> None:
        report = self.report("lockdown")
        self.assertTrue(report["ready"])
        self.assertLess(report["ready_s"], 10.0)
        self.assertEqual(report["policy_in_page"], "true")
        self.assertEqual(report["applied"], [True, True, True, True])

    def test_markup_in_a_frame_is_text_and_loads_nothing(self) -> None:
        report = self.report("lockdown")
        rendered = report["rendered"]
        for name, expected in report["expected"].items():
            self.assertEqual(rendered[name], expected, name)          # every string as given, as text
        self.assertEqual(rendered["markup"], 0)                       # no element came of any of it
        self.assertEqual(rendered["scripts"], 2)                      # qwebchannel.js and board.js only
        self.assertEqual(rendered["pwned"], "undefined")
        self.assertEqual(rendered["frames"], 3)
        self.assertEqual(report["allowed_before_tries"], {"jarvis-board": 9})   # the page and its 8 files, no request more
        self.assertEqual(report["blocked_before_tries"], {})

    def test_the_one_picture_is_the_app_s_data_jpeg_and_a_2_mb_one_is_refused(self) -> None:
        report = self.report("lockdown")
        rendered = report["rendered"]
        self.assertEqual(rendered["pictures"], 1)
        self.assertTrue(rendered["picture_is_data"])
        self.assertTrue(rendered["picture_loaded"])
        self.assertTrue(report["refused_2mb_picture"])
        self.assertEqual(report["counts"]["refused:picture"], 1)

    def test_every_way_out_fails(self) -> None:
        results = self.report("lockdown")["attempts"]["results"]
        for name in NETWORK_TRIES:
            self.assertIn(name, results)
            self.assertNotIn(results[name], ("loaded", "open"), name)
            self.assertFalse(results[name].startswith("resolved"), name)
        for name in ("fetch_http", "fetch_https", "fetch_lan", "fetch_loopback"):
            self.assertEqual(results[name], "rejected TypeError", name)
        self.assertEqual(results["font_remote"], "rejected NetworkError")
        self.assertEqual(results["window_open"], "null")
        self.assertIn(results["worker_blob"], ("error", "threw SecurityError"))
        self.assertIn(results["worker_data"], ("error", "threw SecurityError"))
        self.assertEqual(results["notifications"], "resolved denied")
        self.assertEqual(results["geolocation"], "error 1")           # PERMISSION_DENIED
        self.assertEqual(results["clipboard"], "rejected NotAllowedError")

    def test_the_page_s_own_policy_stops_each_kind_before_any_request(self) -> None:
        report = self.report("lockdown")
        violations = report["attempts"]["violations"]
        # Every directive of the <meta> policy works there (media-src: default-src 'none'; workers: script-src's own).
        for directive in ("connect-src", "img-src", "style-src-elem", "font-src", "form-action", "frame-src",
                          "worker-src", "base-uri", "object-src", "media-src"):
            self.assertIn(directive, violations)
        results = report["attempts"]["results"]
        self.assertEqual(results["base"], "jarvis-board://board/board.html")   # a <base> elsewhere is ignored
        self.assertEqual(results["audio"], "error")
        self.assertEqual(violations.count("connect-src"), 8)          # 4 fetches, 2 XHRs, the beacon, the web socket
        self.assertEqual(violations.count("img-src"), 5)
        # Nothing it tried reached the network layer; the one board address outside the allowlist is blocked there.
        self.assertEqual(report["counts"]["blocked"], {"jarvis-board": 1})
        self.assertEqual(set(report["counts"]["allowed"]), {"jarvis-board"})
        self.assertEqual(report["counts"]["served"], {"served": 9})

    def test_the_page_never_leaves_the_board(self) -> None:
        report = self.report("lockdown")
        self.assertEqual(report["url"], "jarvis-board://board/board.html")
        self.assertEqual(report["url_in_page"], "jarvis-board://board/board.html")
        self.assertEqual(report["counts"]["refused:navigation"], 3)   # location.href, reload, a link's click
        self.assertEqual(report["frames_after"], 3)                    # not reloaded either
        self.assertGreaterEqual(report["counts"]["refused:permissions"], 2)

    def test_the_bridge_takes_only_the_list_and_frames_the_app_sent(self) -> None:
        report = self.report("lockdown")
        counts = report["counts"]
        self.assertEqual(counts["dropped:unknown"], 1)                # "evil"
        self.assertEqual(counts["why:kind"], 1)
        self.assertEqual(counts["why:id"], 1)                         # open "nope"
        self.assertEqual(counts["why:size"], 1)                       # a 5000-character payload
        self.assertEqual(counts["why:shape"], 1)                      # a layout 10^12 px down
        self.assertEqual(counts["why:field"], 1)                      # pin without on
        self.assertEqual(counts["accepted:open"], 1)
        self.assertEqual(counts["refused:delete"], 1)                 # bridge.deleteLater(): refused ...
        self.assertEqual(counts["accepted:close"], 1)                 # ... and the bridge still works
        self.assertGreater(counts["why:rate"], 0)                     # a flood is cut at POSTS_PER_S
        self.assertLessEqual(counts["accepted:focus"], view.POSTS_PER_S)
        seen = [item for item in report["seen"] if item[0] != "frameFocused"]
        self.assertEqual(seen, [["ready"], ["openRequested", "f1"], ["closeRequested", "f2"]])
        self.assertGreater(report["layouts"], 0)
        # Qt's own methods on the channel: post and QObject's deleteLater (refused); the rest are qwebchannel.js's.
        self.assertEqual(report["bridge_methods"].split("|"), ["deleteLater", "deleteLater()", "post",
                                                               "post(QString,QString)", "propertyUpdate",
                                                               "signalEmitted", "unwrapProperties", "unwrapQObject"])

    def test_the_engine_runs_with_jarvis_s_flags(self) -> None:
        flags = self.report("lockdown")["engine_flags"]
        for flag in ("--disable-gpu", "--mute-audio", snapshots.RESOLVER_RULES, *snapshots.OFFLINE_FLAGS):
            self.assertIn(flag, flags)


class ControlTests(BoardProbeCase):
    def test_without_the_page_s_policy_nothing_gets_out_either(self) -> None:
        report = self.report("control")
        self.assertEqual(report["policy_in_page"], "false")           # the control really ran without it
        self.assertEqual(report["attempts"]["violations"], [])
        results = report["attempts"]["results"]
        for name in NETWORK_TRIES:
            self.assertNotIn(results[name], ("loaded", "open"), name)
            self.assertFalse(results[name].startswith("resolved"), name)
        counts = report["counts"]
        self.assertEqual(set(counts["allowed"]), {"jarvis-board"})
        self.assertEqual(counts["served"], {"served": 9})
        for scheme_word in ("file", "ws", "jarvis-board"):            # what reached the interceptor, blocked there
            self.assertGreaterEqual(counts["blocked"].get(scheme_word, 0), 1, scheme_word)
        self.assertGreaterEqual(counts["refused:navigation"], 4)      # the form and the inline frame too, now
        self.assertEqual(report["url"], "jarvis-board://board/board.html")
        self.assertEqual(report["frames_after"], 3)
        self.assertEqual(results["notifications"], "resolved denied")
        self.assertEqual(results["geolocation"], "error 1")
        self.assertEqual(results["window_open"], "null")
        # What only the policy stops: workers from blob: and data: run without it, and a <base> moves the page's base
        # address (in the lockdown case all three fail).
        self.assertEqual(results["worker_blob"], "ran")
        self.assertEqual(results["base"], "http://example.com/")


class MediaTests(BoardProbeCase):
    def test_the_microphone_and_camera_are_refused_and_no_renderer_is_left(self) -> None:
        # Round 5: the probe's engine lists Chromium's stand-in devices, never this PC's: with its microphone unplugged
        # Chromium said NotFoundError before asking for any permission, and the refusal below was never counted.
        report = self.report("media")
        self.assertIn(FAKE_MEDIA_DEVICES, report["engine_flags"])
        self.assertTrue(report["ready"])
        self.assertTrue(report["media"].startswith("rejected "), report["media"])
        self.assertGreaterEqual(report["counts"]["refused:permissions"], 1)
        self.assertIn("--type=renderer", report["kinds_while_open"])
        # Chromium's video capture service, started to list the devices before the permission was asked for, is all
        # that outlives the board (until the engine ends); never a renderer.
        self.assertFalse(any("renderer" in kind for kind in report["kinds_after"]), report["kinds_after"])
        for kind in report["kinds_after"]:
            self.assertIn("video_capture", kind)


class KeyTests(BoardProbeCase):
    def test_the_window_s_keys_fire_with_the_focus_in_the_page(self) -> None:
        report = self.report("keys")
        self.assertTrue(report["before_focus_in_board"])
        self.assertTrue(report["before_focus_is_proxy"])
        self.assertEqual(report["before_fired"], {key: 1 for key in WINDOW_KEYS})
        self.assertEqual(report["counts"]["keys_sent_on"], 2 * len(WINDOW_KEYS))
        page_keys = report["before_page_keys"]
        self.assertFalse([key for key in page_keys if key.startswith("ctrl+") and key != "ctrl+Control"])

    def test_space_scrolls_the_board_and_never_plays_the_briefing(self) -> None:
        report = self.report("keys")
        self.assertEqual(report["before_plays"], 0)
        self.assertIn(" ", report["before_page_keys"])                # the page had it
        self.assertEqual(report["control_plays"], 1)                  # without the board in _keeps_space it plays

    def test_the_page_s_own_keys_ask_the_app(self) -> None:
        report = self.report("keys")
        self.assertIn(["pinRequested", "f1", True], report["seen"])   # P
        self.assertIn(["openRequested", "f1"], report["seen"])        # Enter

    def test_after_the_renderer_stops_the_board_comes_back_and_the_keys_still_fire(self) -> None:
        report = self.report("keys")
        self.assertEqual((report["readies_before_kill"], report["readies_after_kill"]), (1, 2))
        self.assertTrue(report["ready_after_kill"])
        self.assertEqual(report["counts"]["renderer_stopped"], 1)
        self.assertNotEqual(report["before_proxy"], report["after_proxy"])   # a new focus proxy ...
        self.assertEqual(report["counts"]["key_filter:proxies"], 2)          # ... with the filter on it
        self.assertTrue(report["after_focus_is_proxy"])
        self.assertEqual(report["after_fired"], {key: 1 for key in WINDOW_KEYS})
        self.assertEqual(report["after_plays"], 0)
        self.assertIn(" ", report["after_page_keys"])


class ShellTests(BoardProbeCase):
    """The page shell in the real engine: DESIGN-SPEC 3.4's states and section 5's motion and its calm and off forms."""

    def test_a_frame_draws_in_with_the_design_s_timings(self) -> None:
        report = self.report("shell")
        self.assertTrue(report["ready"])
        self.assertEqual(report["lively"], {"classes": ["frame", "arriving"], "trace": "0.26s", "ticks_at": "0.26s",
                                            "content": "rise"})
        self.assertEqual(report["lively_after"], {"classes": ["frame"], "opacity": "1"})

    def test_the_page_plays_the_probe_s_own_level_whatever_this_pc_s_setting(self) -> None:
        # Round 5: Chromium's reduced motion follows Windows' "Show animations"; the probe's engine is told no reduced
        # motion, so lively is lively here even with that setting off.
        look = self.report("shell")["lively_look"]
        self.assertEqual((look["reduce"], look["k"], look["level"]), (False, "1", ["motion-lively"]))
        self.assertIn("arriving", look["classes"])

    def test_the_page_s_own_reduced_motion_plays_lively_as_off(self) -> None:
        # Round 5: the one probe whose engine is told reduced motion (as that Windows setting off tells it): the page's
        # own rule still holds - K 0 (no draw-in) and board.css's --k 0, at lively.
        report = self.report("reduced")
        self.assertTrue(report["ready"])
        look = report["lively"]
        self.assertEqual((look["reduce"], look["k"], look["level"]), (True, "0", ["motion-lively"]))
        self.assertEqual((look["classes"], look["trace"]), (["frame"], "0s"))

    def test_calm_is_slower_by_1_6_without_the_rise(self) -> None:
        self.assertEqual(self.report("shell")["calm"], {"classes": ["frame", "arriving"], "trace": "0.416s",
                                                        "ticks_at": "0.416s", "content": "show"})

    def test_off_is_each_moment_s_end_state_at_once(self) -> None:
        self.assertEqual(self.report("shell")["off"], {"classes": ["frame", "lit"], "opacity": "1", "brackets": "0s",
                                                       "bracket_opacity": "1"})

    def test_a_changed_figure_gets_one_lap_then_the_tag_and_calm_the_tag_only(self) -> None:
        report = self.report("shell")
        self.assertEqual(report["changed_lively"], {"during": ["frame", "lap"], "lap": "0.9s",
                                                    "after": ["frame", "changed"], "tag": "Changed"})
        self.assertEqual(report["changed_calm"], ["frame", "changed"])

    def test_at_most_six_of_an_arrival_draw_in_80_ms_apart(self) -> None:
        self.assertEqual(self.report("shell")["stagger"], [[1, "0"], [1, "1"], [1, "2"], [1, "3"], [1, "4"], [1, "5"],
                                                           [0, ""]])

    def test_waits_failed_empty_focused_closed_and_brought_back(self) -> None:
        report = self.report("shell")
        self.assertEqual(report["states"], {"waits": ["12px", "inline-flex"],          # radius 12: a shape cue
                                            # TILE_ALERT, its warning and NO button (canvas part 5A: no Try again)
                                            "failed": ["rgb(23, 15, 18)", "Google Calendar didn't answer.", 0],
                                            "empty": "Checked 9:44 AM"})
        focused = report["focused"]
        self.assertEqual((focused["keys"], focused["meta"], focused["controls"]), ("flex", "none", "flex"))
        self.assertIn("ENTER OPEN BIG · P PIN", focused["footer"])
        self.assertIn("DEL CLOSE · ESC ASK BAR", focused["footer"])
        self.assertIn("closed", report["closed"]["classes"])
        self.assertEqual(report["closed"]["row"].split("\n"), ["Closed: Frame m1", "Bring back"])
        self.assertNotIn("closed", report["brought_back"])
        # Each control asked the app, by its own typed signal.
        self.assertEqual(report["seen"], [["ready"], ["approvalsRequested", "w1"],
                                          ["closeRequested", "m1"], ["bringBackRequested", "m1"]])

    def test_titles_wrap_and_no_text_is_cut_in_a_narrow_board(self) -> None:
        wrap = self.report("shell")["wrap"]
        self.assertEqual(wrap["lines"], 2)
        self.assertEqual(wrap["text_cut"], 0)
        self.assertGreater(wrap["words"], 10)
        self.assertFalse(wrap["page_cut"])
        self.assertEqual(wrap["numerals"], "tabular-nums")


class KindsTests(BoardProbeCase):
    """Part 4's kinds in the real page, built by board.frames (DESIGN-SPEC 3.3)."""

    def test_every_kind_renders_with_its_words_as_text(self) -> None:
        report = self.report("kinds")
        self.assertTrue(report["ready"])
        self.assertEqual(report["applied"], [True] * 10)               # nine frames and the page's picture
        self.assertEqual(report["refused_pictures"], 0)
        frames = report["rendered"]["frames"]
        self.assertEqual(set(frames), set(report["kinds"]))
        glyphs = {"mail_thread": "g-mail", "calendar": "g-calendar", "picture": "g-image", "text": "g-read-all",
                  "todos": "g-todos", "mail_list": "g-inbox"}
        for frame_id, words in report["expected"].items():
            found = frames[frame_id]
            self.assertEqual(found["glyph"], glyphs[report["kinds"][frame_id]], frame_id)
            for word in words:
                self.assertIn(word, found["text"], frame_id)          # every word of its data, as text
            self.assertEqual(found["markup"], 0, frame_id)            # no element came of any of it
            self.assertNotIn("arriving", found["classes"])
        self.assertEqual(frames["p1"]["images"], 1)
        self.assertEqual(sum(found["images"] for found in frames.values()), 1)
        self.assertIn("waits", frames["m2"]["classes"])
        self.assertEqual(report["rendered"]["scripts"], 2)
        self.assertEqual(report["rendered"]["pwned"], "undefined")

    def test_the_mails_text_is_whole_and_clamped_to_three_lines_by_style_only(self) -> None:
        mail = self.report("kinds")["rendered"]["mail"]
        self.assertEqual(mail["text"], self.report("kinds")["mail_text"])
        self.assertEqual(mail["lines"], 3)
        self.assertTrue(mail["clamped"])

    def test_the_now_line_stands_between_the_past_and_what_comes_next(self) -> None:
        horizon = self.report("kinds")["rendered"]["horizon"]
        self.assertGreater(horizon["at"], 0)
        self.assertIn("past", horizon["before"])
        self.assertNotIn("past", horizon["after"])

    def test_the_waits_chip_asks_for_approvals_and_nothing_else(self) -> None:
        report = self.report("kinds")
        self.assertEqual(report["chip_click"], [["approvalsRequested", "m2"]])
        self.assertEqual(report["chip_enter"], [["approvalsRequested", "m2"]])
        asked = {item[0] for item in report["seen"]}
        self.assertEqual(asked, {"ready", "approvalsRequested", "frameFocused"})   # no open, close, pin, retry

    def test_the_picture_is_its_data_jpeg_only_and_a_click_only_focuses_its_frame(self) -> None:
        report = self.report("kinds")
        picture = report["rendered"]["picture"]
        self.assertEqual(picture["src"], "data:image/jpeg;base64,")
        self.assertTrue(picture["loaded"])
        self.assertTrue(picture["inSlot"])
        self.assertFalse(picture["draggable"])
        self.assertEqual(picture["filter"], "brightness(0.8)")         # 80 % until focused or lit
        click = report["picture_click"]
        self.assertEqual(click["seen"], [["frameFocused", "p1"]])
        self.assertEqual(click["focused"], "picture frame")
        self.assertEqual(click["filter_focused"], "none")
        self.assertTrue(click["drag_refused"])
        self.assertTrue(click["url_same"])
        self.assertEqual(report["picture_lit_filter"], "none")
        self.assertEqual(report["navigation_refused"], 0)             # it never even tried to go anywhere
        self.assertEqual(report["allowed_before"], {"jarvis-board": 9})
        self.assertEqual(report["allowed_after"], {"jarvis-board": 9})   # nothing more was loaded
        self.assertEqual(report["blocked_after"], {})

    def test_an_update_in_place_does_not_draw_in_again(self) -> None:
        report = self.report("kinds")
        update = report["update"]
        self.assertTrue(update["same"])                                # the same element
        self.assertEqual(update["classes"], "frame")                   # no draw-in, no lap
        self.assertTrue(update["text"])
        self.assertEqual(update["pictures"], 1)                        # the picture stays
        self.assertEqual(update["meta"], "9:42 AM")
        self.assertEqual(report["update_changed"].split(), ["frame", "lap"])   # changed: the lap, only then

    def test_the_page_refuses_bodies_python_refuses(self) -> None:
        report = self.report("kinds")
        self.assertEqual(report["page_refuses"], [False] * 8)
        self.assertEqual(report["bad_frames"], 0)

    def test_no_word_is_cut_at_300_px(self) -> None:
        narrow = self.report("kinds")["narrow"]
        self.assertEqual(narrow["cut"], [])
        self.assertGreater(narrow["words"], 100)
        self.assertFalse(narrow["page_cut"])


class FallbackTests(BoardProbeCase):
    def test_inherited_unsafe_settings_never_start_chromium(self) -> None:
        unsafe = self.report("fallback")["unsafe"]
        self.assertEqual(unsafe["reason"], "unsafe_flags")
        self.assertFalse(unsafe["started"])
        self.assertEqual(unsafe["children"], 0)
        self.assertEqual(unsafe["words"], [view.CANT_START, ""])
        self.assertFalse(unsafe["web_view"])
        self.assertTrue(unsafe["apply"])                              # part 4: still checked, shown as a tile
        self.assertEqual(unsafe["words_after"], [view.CANT_START, "READ-ONLY TILES BELOW"])
        self.assertEqual(unsafe["tiles"], ["x"])
        self.assertFalse(unsafe["started_after"])

    def test_a_page_not_ready_in_time_leaves_the_tile_and_no_renderer(self) -> None:
        late = self.report("fallback")["late"]
        self.assertEqual(late["reason"], "no_ready")
        self.assertTrue(late["fallback"])
        self.assertFalse(late["web_view"])
        self.assertEqual(late["children_after"], 0)

    def test_a_renderer_that_keeps_stopping_is_loaded_again_then_gives_up(self) -> None:
        report = self.report("fallback")
        crash = report["crash"]
        self.assertEqual(crash["stops"], view.MAX_RESTARTS + 1)
        self.assertEqual(crash["renderer_stopped"], view.MAX_RESTARTS + 1)
        self.assertEqual(crash["reason"], "crashed")
        self.assertEqual(crash["children_after"], 0)
        self.assertEqual([item for item in report["seen"] if item[0] == "unavailable"],
                         [["unavailable", "unsafe_flags"], ["unavailable", "no_ready"], ["unavailable", "crashed"]])


if __name__ == "__main__":
    unittest.main()


class DeskTests(BoardProbeCase):
    """Part 5a: the desk with the real engine in Jarvis's own window (tests/board_probe.py desk_case)."""

    def check(self, case: str, ratio: float) -> dict:
        report = self.report(case)
        self.assertTrue(report["ready"])
        self.assertTrue(all(report["applied"]))
        self.assertTrue(report["desk_shown"])
        self.assertTrue(report["board_in_desk"])                      # the one view, in the desk's board column
        self.assertEqual(report["view_rect"][:3], [381, 12, 503])      # seam + 1, 12 down, 503 wide (1120 x 700)
        page = report["page"]
        self.assertEqual(page["bar"], "Board 5 FRAMES 1 WAITS All Pinned Clear")
        self.assertEqual(page["bar_display"], "flex")
        self.assertEqual([(group["time"], group["older"]) for group in page["groups"]],
                         [("9:41 AM", False), ("9:20 AM", True)])       # the newest on top, the older one quiet
        newest = page["groups"][0]
        self.assertEqual(newest["waits"][0], True)                     # what waits first: the mail with its reply
        self.assertEqual(sum(newest["waits"]), 1)
        self.assertEqual(len(newest["frames"]), 4)                     # the mail and the reply are ONE frame
        self.assertEqual(page["chip"], "Reply waits on Approvals CTRL 3")
        # (at 1.5x the view's 503 px are 755 device pixels: a third of a CSS px more)
        self.assertEqual((round(page["frame_left"]), round(page["frame_width"]), round(page["board_top"])),
                         (15, 448, 44))
        # Round 3 (changed on purpose): the desk's entrance runs on the board and the bar, then is dropped - its class
        # used to stay for the session, and the animation left on them kept open big's dim from easing (it jumped).
        self.assertEqual(sorted(report["entrance"]), ["end:bar", "end:board", "start:bar", "start:board"])
        self.assertFalse(page["desk_in"])
        grab = report["grab"]
        self.assertEqual(grab["ratio"], ratio)
        self.assertEqual(grab["color"], grab["tile_amber"])            # the grab shows the board's waiting frame
        # The first frame reaches the page's layout (alone this takes some 20-60 ms; with every probe's Chromium
        # starting side by side it can take a few hundred: measured, and kept well under a second here).
        self.assertGreaterEqual(report["first_frame_layout_ms"], 0)
        self.assertLess(report["first_frame_layout_ms"], 1500)
        self.assertGreater(report["scroll_after_reveal"], 0)           # reveal scrolled to the older group
        self.assertEqual(report["groups_after_remove"], 1)             # ... which then left whole
        self.assertEqual(report["known_after_remove"], 4)
        self.assertEqual(report["page_refused"], [False] * 9)          # the page refuses what Python refuses
        self.assertEqual(report["groups_after_hostile"], 1)
        self.assertTrue(report["space_focus_in_board"])
        self.assertEqual(report["space_plays"], 0)                     # Space in the board never plays the briefing
        self.assertEqual(report["renderers"], 1)                       # one renderer process for the board
        self.assertGreater(report["memory_mb"]["working_set"], 0)
        return report

    def test_the_desk_at_1x(self) -> None:
        self.check("desk", 1.0)

    def test_the_desk_at_1_5x(self) -> None:
        self.check("desk_x15", 1.5)


def _classes(item: dict) -> list[str]:
    return str(item.get("cls", "")).split()


def _more(page: dict) -> int:
    return int(page["more"].split()[0]) if page["more"] else 0


class WaysTests(BoardProbeCase):
    """Part 5b: the board's ways in the real page, with the app's own controller (tests/board_probe.py ways_case)."""

    def ways(self) -> dict:
        report = self.report("ways")
        self.assertTrue(report["ready"])
        return report

    def test_the_index_strip(self) -> None:
        report = self.ways()
        first = report["first"]
        buttons = [item for item in first["strip"] if "ix-rule" not in _classes(item)]
        rules = [item for item in first["strip"] if "ix-rule" in _classes(item)]
        frames = [item for item in first["frames"] if item["shown"]]
        self.assertEqual(len(buttons), len(frames))                    # one 26 px glyph button per frame ...
        self.assertEqual(len(rules), 2)                                # ... a rule between the three groups
        self.assertEqual({item["height"] for item in buttons}, {26})
        self.assertEqual(buttons[1]["top"] - buttons[0]["top"], 30)    # 4 apart
        self.assertTrue(buttons[0]["label"].startswith("Mail: "))      # its kind and title: board order, waits first
        self.assertTrue(buttons[0]["label"].endswith(", waits on you"))
        self.assertEqual([item["pip"] for item in buttons], ["block"] + ["none"] * (len(buttons) - 1))
        self.assertIn("wait", _classes(buttons[0]))
        box = first["box"]
        expected = [item["top"] < box["bottom"] - 40 and item["bottom"] > box["top"] + 6 for item in frames]
        self.assertEqual(["in" in _classes(item) for item in buttons], expected)    # frames in view
        seen = [item for item in buttons if "in" in _classes(item)]
        self.assertEqual(first["brace"], {"top": seen[0]["top"] - 2,
                                          "height": seen[-1]["top"] + seen[-1]["height"] - seen[0]["top"] + 4})
        self.assertEqual(sum(1 for item in buttons if item["tab"] == 0), 1)          # one Tab stop
        after = report["after_strip_click"]                                          # a real click on one far down
        self.assertGreater(after["scrollTop"], 0)
        clicked = [item for item in after["strip"] if "ix-rule" not in _classes(item)][report["strip_click_index"]]
        self.assertIn("in", _classes(clicked))                          # ... scrolled the board to its frame

    def test_n_more_below_at_its_16_px_edge_the_fades_and_the_end_pad(self) -> None:
        report = self.ways()
        edge17, edge15 = report["edge_17"], report["edge_15"]
        frame17 = next(item for item in edge17["frames"] if item["id"] == report["edge_frame"])
        frame15 = next(item for item in edge15["frames"] if item["id"] == report["edge_frame"])
        self.assertAlmostEqual(edge17["box"]["bottom"] - frame17["bottom"], 17, delta=0.6)
        self.assertAlmostEqual(edge15["box"]["bottom"] - frame15["bottom"], 15, delta=0.6)
        self.assertEqual(_more(edge15), _more(edge17) + 1)            # inside the last 16 px: not fully in view
        top = report["at_top"]
        below = sum(1 for item in top["frames"] if item["shown"] and item["bottom"] > top["box"]["bottom"] - 16)
        self.assertEqual(top["more"], f"{below} MORE BELOW")
        self.assertEqual(report["at_end"]["more"], "")                # never silently clipped; nothing below: none
        self.assertIn("44px", top["mask"])                             # the end fade
        self.assertNotIn("28px", top["mask"])
        self.assertIn("28px", edge17["mask"])                          # scrolled down: the top fade too
        end = report["at_end"]
        last = [item for item in end["frames"] if item["shown"]][-1]
        self.assertAlmostEqual(end["box"]["bottom"] - last["bottom"], 40, delta=1)   # the 40 px end pad

    def test_satellites(self) -> None:
        report = self.ways()
        older = report["ids"]["oldest"] + report["ids"]["middle"]
        self.assertEqual(sorted(item["id"] for item in report["sats"]), sorted(older))   # older answers fold
        for item in report["sats"]:
            self.assertAlmostEqual(item["height"], 48, delta=0.5)       # a 48 px row
            self.assertEqual(item["fact"], report["facts"][item["id"]])   # the app's own fact
        self.assertTrue(all(report["facts"].values()))
        for name in ("sat_clicked", "sat_entered"):                    # a real click, a real Enter: in place
            self.assertNotIn("sat", _classes(report[name]), name)
            self.assertGreater(report[name]["height"], 48, name)

    def test_parking(self) -> None:
        report = self.ways()
        self.assertTrue(report["reading_focus"])                       # the page said the focus is in it
        parked = report["parked"]
        self.assertEqual(len(report["parked_groups"]), 1)
        self.assertEqual((parked["pill"], parked["pillText"]), ("flex", "New answer above Show"))
        self.assertEqual(parked["scrollTop"], report["parked_held"])   # the scroll position holds
        self.assertIn("arr", _classes(parked["strip"][0]))             # its dashed entry on top ...
        self.assertIn("parked", _classes(parked["strip"][0]))
        self.assertIn("ix-rule", _classes(parked["strip"][1]))         # ... then a rule
        self.assertEqual(parked["groups"][0]["display"], "none")       # out of sight until it lands
        shown = report["shown_new"]                                    # Show: it lands on top
        self.assertEqual((shown["pill"], shown["scrollTop"]), ("none", 0))
        self.assertEqual({group["display"] for group in shown["groups"]}, {"flex"})
        self.assertEqual(report["parked_after_show"], [])
        self.assertEqual(len(report["parked_again"]), 1)
        self.assertFalse(report["reading_pointer_left"])               # the page said the pointer left
        self.assertEqual(report["reading_after_leaving"], [False, False])
        self.assertEqual(report["landed_before_quiet"], report["parked_again"])
        self.assertEqual(report["landed_after_quiet"], [])             # quiet: it landed by itself
        self.assertEqual(report["room"], [220])                        # a group arrives: room made, 220 ms

    def test_pin_and_a_seventh_s_note(self) -> None:
        report = self.ways()
        self.assertEqual(report["pins_feed"], 6)
        for name in ("seventh", "seventh_full"):                       # folded and full: the why shows
            seventh = next(item for item in report["pins"]["frames"] if item["id"] == report[name])
            self.assertIn("noted", _classes(seventh), name)
            self.assertNotIn("pinned", _classes(seventh), name)
            self.assertEqual(seventh["note"], "6 PINS AT MOST · UNPIN ONE FIRST", name)
        for item in report["pins"]["frames"]:
            if "pinned" in _classes(item):
                self.assertNotIn("sat", _classes(item))                # pinned frames stay full
        self.assertNotIn("noted", _classes(report["note_after_4s"]))
        self.assertEqual(report["note_after_4s"]["note"], "")
        self.assertEqual(len(report["pinned_left"]), 1)

    def test_close_and_bring_back(self) -> None:
        report = self.ways()
        closing = report["closing"]
        self.assertIn("closed", _classes(closing))
        self.assertTrue(closing["closed"].startswith("Closed: "))
        self.assertTrue(closing["closed"].endswith("Bring back"))
        self.assertTrue(report["closing_flags"]["closed"])             # the app knows
        self.assertEqual(report["closing_active"], "f-act")            # the focus on Bring back
        self.assertEqual(report["closing_held"], 1)                    # ... holds it past CLOSE_S (canvas-pins brief)
        self.assertTrue(report["closing_held_feed"]["closed"])
        self.assertEqual(report["closed_gone"], 0)                     # then it left the board
        self.assertEqual(report["closed_gone_feed"], {})
        self.assertNotIn("closed", _classes(report["brought_back"]))
        self.assertFalse(report["brought_back_flags"]["closed"])

    def test_all_and_pinned(self) -> None:
        report = self.ways()
        shown = [item["id"] for item in report["filter_pinned"]["frames"] if item["shown"]]
        self.assertEqual(shown, report["pinned_left"])
        self.assertEqual(len(report["filter_pinned"]["strip"]), 1)
        self.assertEqual(sum(1 for item in report["filter_all"]["frames"] if item["shown"]),
                         len(report["filter_all"]["frames"]))

    def test_clear_and_bring_back(self) -> None:
        report = self.ways()
        cleared = report["cleared"]
        self.assertRegex(cleared["cleared"], r"^Board cleared \d+ FRAMES Bring back$")
        self.assertEqual([item["id"] for item in cleared["frames"] if item["shown"]], report["pinned_left"])
        self.assertTrue(cleared["clearDisabled"])                      # nothing more to clear
        self.assertEqual((report["cleared_count"], report["cleared_desk"]), (1, True))
        self.assertEqual(report["restored"]["cleared"], "")
        self.assertEqual(report["restored_count"], len(report["restored"]["frames"]))
        gone = report["clear_gone"]
        self.assertEqual(gone["cleared"], "")
        self.assertEqual([item["id"] for item in gone["frames"]], report["pinned_left"])
        self.assertEqual(report["clear_gone_feed"], report["pinned_left"])
        self.assertEqual(report["last_clear_held"], [True, True, 0])   # part 5c: the desk stays for Bring back ...
        self.assertEqual((report["last_clear_desk"], report["last_clear_stage"], report["last_clear_count"]),
                         (False, True, 0))                             # ... then nothing left: Home's stage again

    def test_the_keyboard(self) -> None:
        keys = self.ways()["keys"]
        order = keys["order"]
        self.assertTrue(keys["start_ask"])
        self.assertTrue(keys["conversation"])                          # F6: the conversation ...
        self.assertTrue(keys["board_qt"])                              # ... the board ...
        self.assertEqual(keys["board"], order[0])
        self.assertEqual((keys["down"], keys["up"]), (order[1], order[0]))
        self.assertEqual((keys["end"], keys["home"]), (order[-1], order[0]))
        self.assertTrue(keys["strip"])                                 # ... the strip ...
        self.assertTrue(keys["strip_down"])
        self.assertNotEqual(keys["strip_down"], keys["strip"])
        self.assertTrue(keys["back_to_ask"])                           # ... and round
        self.assertTrue(keys["shift_strip"])                           # Shift+F6 back
        self.assertEqual(keys["shift_board"], order[0])
        self.assertTrue(keys["shift_conversation"])
        self.assertTrue(keys["escape"])                                # Esc: the ask bar
        self.assertGreaterEqual(keys["cycle_keys_sent_on"], 4)         # F6 went on to the window from the page

    def test_pairs(self) -> None:
        report = self.ways()
        page = report["pairs_page"]
        self.assertTrue(page["pairs"])                                 # a frames area of 519 px
        first, second = (next(item for item in page["frames"] if item["id"] == frame_id)
                         for frame_id in report["pair_ids"])
        self.assertIn("pair-a", _classes(first))
        self.assertIn("pair-b", _classes(second))
        self.assertEqual(first["top"], second["top"])
        self.assertGreater(second["left"], first["left"])
        self.assertGreaterEqual(min(first["width"], second["width"]), 244)
        left, right = report["pair_ids"]
        self.assertEqual((report["pair_right"], report["pair_left"]), (right, left))   # Right, then Left again
        self.assertNotIn(report["pair_down"], report["pair_ids"])      # Down leaves the pair's row

    def test_the_new_messages_hostile_are_refused_by_the_page(self) -> None:
        report = self.ways()
        self.assertEqual(report["page_refused"], [False] * 9)
        self.assertTrue(report["hostile_changed_nothing"])


def _color(value: object) -> str:
    """A token as the page's computed colour ("rgb(r, g, b)" or "rgba(r, g, b, a)")."""
    from PySide6.QtGui import QColor

    color = value if isinstance(value, QColor) else QColor(value)
    if color.alpha() == 255:
        return f"rgb({color.red()}, {color.green()}, {color.blue()})"
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {round(color.alphaF(), 2):g})"


class LitTests(BoardProbeCase):
    """Part 5c: the lit frame, its cue line's stub, changed, three across and Clear (tests/board_probe.py lit_case)."""

    def lit(self) -> dict:
        report = self.report("lit")
        self.assertTrue(report["ready"])
        return report

    def test_the_sentence_lights_the_frame_it_names_in_its_own_colour(self) -> None:
        from briefing_reader import holo

        report = self.lit()
        mail, day = report["ids"][0], report["ids"][1]
        amber, cyan = report["amber_page"], report["cyan_page"]
        self.assertEqual(amber["lit"], mail)                           # "Dana sent the budget numbers ...": the mail
        self.assertEqual(cyan["lit"], day)                             # "Three meetings today ...": the calendar
        self.assertEqual({b["color"] for b in amber["brackets"]}, {_color(holo.AMBER_HI)})   # never cyan on amber
        self.assertEqual({b["color"] for b in cyan["brackets"]}, {_color(holo.HOLO_HI)})
        self.assertEqual(len(amber["brackets"]), 4)
        box = amber["litBox"]
        corners = sorted((round(b["box"]["left"]), round(b["box"]["top"])) for b in amber["brackets"])
        self.assertEqual(corners[0], (round(box["left"]) - 6, round(box["top"]) - 6))   # 6 px outside its corners
        self.assertEqual({round(b["box"]["width"]) for b in amber["brackets"]}, {14})
        self.assertEqual(round(box["left"]) - 6, 9)                    # the left ones in the view's 15 px pad
        lit_frame = next(f for f in amber["frames"] if f["id"] == mail)
        self.assertEqual(lit_frame["ground"], _color(holo.TILE_AMBER_LIT))
        day_frame = next(f for f in cyan["frames"] if f["id"] == day)
        self.assertEqual(day_frame["ground"], _color(holo.TILE_FOCUS))
        for name in ("amber_native", "cyan_native"):                   # every ring's core glow halves
            self.assertEqual(report[name]["rings"], [0.5] * 5, name)

    def test_the_stub_runs_from_the_view_s_left_edge_and_meets_the_native_part(self) -> None:
        from briefing_reader import holo

        report = self.lit()
        for page, native, color in ((report["amber_page"], report["amber_native"], holo.CUE_AMBER),
                                    (report["cyan_page"], report["cyan_native"], holo.CUE)):
            stub = page["stub"]
            self.assertEqual(stub["visibility"], "visible")
            self.assertEqual(round(stub["line"]["left"]), 0)           # from the view's left edge ...
            dot_centre = stub["dot"]["left"] + stub["dot"]["width"] / 2
            self.assertAlmostEqual(dot_centre, page["litBox"]["left"] - 6, delta=0.6)   # ... to a dot 6 px from it
            self.assertEqual(round(stub["dot"]["width"]), 5)           # a 2.5 px dot
            self.assertEqual(stub["color"], _color(color))
            line_middle = stub["line"]["top"] + stub["line"]["height"] / 2
            path = native["path"]
            self.assertAlmostEqual(line_middle, path["end_y"] - native["board_top"], delta=0.6)   # they meet
            self.assertEqual(path["seam_x"], native["seam"] + 0.5)     # the native part runs along the seam ...
            self.assertLess(native["line_right"], native["view_left"])   # ... and never over the view
            self.assertTrue(native["shown"])

    def test_scrolling_hides_both_parts_and_they_come_back(self) -> None:
        report = self.lit()
        self.assertEqual(report["scrolling_page"]["stub"]["visibility"], "hidden")
        self.assertFalse(report["scrolling_native"]["shown"])
        after, native = report["after_scroll_page"], report["after_scroll_native"]
        self.assertEqual(after["stub"]["visibility"], "visible")
        self.assertNotIn("drawing", after["stub"]["cls"])              # back without the draw-in
        self.assertTrue(native["shown"])
        middle = after["stub"]["line"]["top"] + after["stub"]["line"]["height"] / 2
        self.assertAlmostEqual(middle, native["path"]["end_y"] - native["board_top"], delta=0.6)

    def test_an_arriving_frame_traces_in_line_focus_while_one_is_lit(self) -> None:
        from briefing_reader import holo

        report = self.lit()
        arriving = next(f for f in report["arriving_page"]["frames"] if f["id"] == report["arriving_id"])
        self.assertIn("arriving", arriving["cls"])
        self.assertEqual(arriving["outline"], _color(holo.LINE_FOCUS))

    def test_the_speech_ending_lights_nothing(self) -> None:
        report = self.lit()
        ended, native = report["ended_page"], report["ended_native"]
        self.assertEqual((ended["lit"], ended["has_lit"], ended["stubs"]), ("", False, 0))
        self.assertEqual((native["lit"], native["glow"], native["rings"]), ("", 1.0, [1.0] * 5))
        self.assertFalse(native["shown"])

    def test_changed_one_lap_then_the_tag_and_the_same_words_send_nothing(self) -> None:
        report = self.lit()
        mail = report["ids"][0]
        lapping = next(f for f in report["lap_page"]["frames"] if f["id"] == mail)
        after = next(f for f in report["after_lap_page"]["frames"] if f["id"] == mail)
        self.assertIn("lap", lapping["cls"].split())
        self.assertNotIn("lap", after["cls"].split())
        self.assertIn("changed", after["cls"].split())
        self.assertEqual(report["changed_frames"], 1)                  # sent again, once, in its own place
        self.assertEqual(report["same_again_sent"], 0)                 # the same words: nothing at all
        self.assertEqual(report["feed_counts"]["changed"], 1)

    def test_three_across_from_756_px_and_pairs_below(self) -> None:
        report = self.lit()
        three = [f for f in report["three_page"]["frames"] if f["id"] in report["three_ids"]]
        self.assertEqual([("trio" in f["cls"].split()) for f in three], [True] * 3)
        self.assertEqual(len({round(f["top"]) for f in three}), 1)    # one row
        self.assertGreaterEqual(min(f["width"] for f in three), 244)
        self.assertIn("trios", report["three_page"]["board"].split())
        self.assertEqual(report["three_view"][2], 819)                 # 1500 x 960: a 764 px frames area
        a, b, c = report["three_ids"]
        keys = report["three_keys"]
        self.assertEqual((keys["right"], keys["right2"], keys["left"]), (b, c, b))   # Left / Right in the row
        self.assertNotIn(keys["down"], report["three_ids"])            # Down leaves the row
        pairs = {f["id"]: f for f in report["pairs_page"]["frames"]}
        self.assertEqual(report["pairs_view"][2], 799)                 # 1480 x 960: a 744 px area, pairs
        self.assertNotIn("trios", report["pairs_page"]["board"].split())
        self.assertIn("pair-a", pairs[a]["cls"].split())
        self.assertIn("pair-b", pairs[b]["cls"].split())
        self.assertNotIn("pair-a", pairs[c]["cls"].split())           # the third alone, full width
        self.assertGreater(pairs[c]["width"], 2 * 244)

    def test_clear_with_nothing_pinned_keeps_the_desk_for_its_bring_back(self) -> None:
        report = self.lit()
        self.assertEqual((report["cleared_desk"], report["cleared_held"], report["cleared_count"]), (True, True, 0))
        self.assertRegex(report["cleared_row"], r"^Board cleared \d+ FRAMES Bring back$")
        self.assertTrue(report["cleared_bar"].startswith("Board 0 FRAMES"))
        self.assertTrue(report["cleared_ring_head"])
        self.assertEqual((report["after_clear_desk"], report["after_clear_stage"]), (False, True))

    def test_the_new_messages_hostile_are_refused_by_the_page(self) -> None:
        # Part 5c fixes: sent while the page holds the frame they name (it used to run on an empty board, where any big
        # open was dropped as an unknown id), with a control that the page takes, and the reasons each was dropped for.
        # Round 2 (lockdown 0): an item only each remaining check refuses (a big open wrong only in h, a key too many, a
        # well-formed id the page lacks; a close in the wrong state or with a bad id; a lit with a bad id), so taking
        # any one of the page's checks out lets one through.
        report = self.lit()
        self.assertTrue(report["hostile_known"])                       # the frame named is on the page ...
        self.assertTrue(report["hostile_unknown_absent"])              # ... and "f999" is not
        self.assertEqual(report["page_refused"], [False] * 24)
        self.assertEqual(report["big_after_hostile"], "")
        self.assertEqual(report["hostile_drops"], {"shape": 18, "picture": 5, "id": 1, "size": 0})
        self.assertEqual(report["control"], [True, True])               # a good open (then close) is taken
        self.assertEqual(report["big_after_control"], "")

    def test_a_layout_follows_where_the_frames_end_up(self) -> None:
        # Part 5c fixes: a frame's height animation (close, bring back) or a picture loading after the layout went out
        # moved the frames below with no new layout, so the native part ended away from the page's stub.
        report = self.lit()
        day = report["ids"][1]
        for name in ("closed_above", "back_above", "picture_load"):
            with self.subTest(name=name):
                shown, layout = report[name]["page"], report[name]["layout"]
                frames = {frame["id"]: frame for frame in shown["frames"]}
                self.assertTrue(layout)
                for frame_id, (top, height) in layout.items():
                    if frame_id in frames:
                        self.assertLessEqual(abs(top - round(frames[frame_id]["top"])), 1, frame_id)
                        self.assertLessEqual(abs(height - round(frames[frame_id]["height"])), 1, frame_id)
        self.assertEqual(report["picture_load"]["loaded"], 1)
        for name in ("closed_above", "back_above"):                     # the lit day's two halves still meet
            with self.subTest(name=name):
                shown, native = report[name]["page"], report[name]["native"]
                self.assertEqual(shown["lit"], day)
                stub = shown["stub"]
                middle = stub["line"]["top"] + stub["line"]["height"] / 2
                self.assertAlmostEqual(middle, native["path"]["end_y"] - native["board_top"], delta=0.6)
        closed = next(frame for frame in report["closed_above"]["page"]["frames"] if frame["id"] == report["ids"][0])
        self.assertIn("closed", closed["cls"].split())                  # (it was closed, then brought back)


class BigTests(BoardProbeCase):
    """Part 5c: open big in Jarvis's own window (tests/board_probe.py big_case), at 1x and 1.5x."""

    def check(self, case: str) -> dict:
        report = self.report(case)
        self.assertTrue(report["ready"])
        return report

    def test_open_big_in_two_steps(self) -> None:
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                self.assertEqual(report["big_rect"], [24, 0, 860, 550])     # the column's left edge to the board's
                self.assertTrue(report["veil_shown"])                         # a still over it while it is set
                self.assertFalse(report["veil_after"])                        # gone once the page is laid out
                self.assertEqual(report["open_view"], report["big_rect"])
                self.assertEqual([event[0] for event in report["moves_open"]], ["Move", "Resize"])   # once
                self.assertEqual(report["open_dim"], 0.2)                     # the Dock and the header at 20 %
                mid, done = report["mid_open"], report["open"]
                self.assertNotEqual(mid["transform"], "none")                 # it grows ...
                self.assertLess(mid["card"]["width"], done["card"]["width"])
                self.assertEqual([round(v) for v in done["card"].values()], [0, 0, 860, 550])   # ... to the view
                self.assertEqual(done["transform"], "none")
                self.assertTrue(done["cardFocus"])
                self.assertTrue(done["cardMeta"].endswith("· Open big"))
                self.assertEqual(done["cardFoot"], "ESC BACK TO THE BOARD")
                self.assertEqual(done["boardOpacity"], "0.2")
                backdrop = done["backdrop"]
                # Part 5c fixes: the backdrop stays opaque and dims as a picture (at 0.2 opacity the conversation's
                # own widgets under the translucent view showed through it undimmed).
                self.assertEqual((backdrop["display"], backdrop["src"], backdrop["opacity"], backdrop["filter"]),
                                 ("block", "data:image/jpeg;base64,", "1", "brightness(0.2)"))
                self.assertEqual([round(v) for v in backdrop["box"].values()], [0, 0, 357, 550])
                outside = [image for image in done["images"] if not image["inBoard"] and not image["inCard"]]
                self.assertEqual([image["id"] for image in outside], ["backdrop"])   # the one new picture
                self.assertTrue(all(image["src"] == "data:image/jpeg;base64," for image in done["images"]))
                self.assertEqual(report["blocked_after"], report["blocked_before"])  # nothing else was asked for
                self.assertEqual(set(report["allowed"]), {"jarvis-board"})

    def test_a_resize_keeps_it_over_both_columns_and_900x600_too(self) -> None:
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                self.assertEqual(report["resized_state"], "open")
                self.assertEqual(report["resized_view"], report["resized_rect"])
                self.assertEqual(len(report["moves_resized"]), 2)            # set once for the new layout
                self.assertEqual([round(v) for v in report["resized"]["card"].values()][2:],
                                 report["resized_rect"][2:])
                self.assertEqual(report["small_rect"], [20, 0, 780, 454])     # 900 x 600: the compact column
                self.assertEqual(report["small_view"], report["small_rect"])
                self.assertEqual(len(report["moves_small"]), 2)
                self.assertEqual([round(v) for v in report["small"]["card"].values()], [0, 0, 780, 454])
                self.assertEqual(report["small_closed_view"], [317, 12, 483, 526])

    def test_esc_reverses_it_in_order(self) -> None:
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                mid = report["mid_close"]
                self.assertNotEqual(mid["transform"], "none")                 # the scale-down first ...
                self.assertEqual(report["mid_close_dim"], 0.2)
                self.assertEqual(report["mid_close_view"], report["resized_rect"])   # ... the view still wide
                self.assertEqual([step for _who, step, _at in report["order"]], ["back", "shut", "laid"])
                steps = report["dims_at_steps"][:3]                          # (the later 900 x 600 round after)
                self.assertEqual([step[0] for step in steps], ["back", "shut", "laid"])
                (_b, back_dim, back_view), (_s, shut_dim, shut_view), _laid = steps
                self.assertEqual((back_dim, back_view), (0.2, report["resized_rect"]))
                self.assertEqual(shut_view, report["closed_rect"])           # then the geometry, once ...
                self.assertEqual(shut_dim, 0.2)                               # ... and only then the dim lifts
                self.assertEqual([event[0] for event in report["moves_close"]], ["Move", "Resize"])
                self.assertEqual((report["closed_view"], report["closed_dim"]), (report["closed_rect"], 1.0))
                self.assertGreaterEqual(report["shut_s"], 0.2)                # its 220 ms
                closed = report["closed"]
                self.assertEqual((closed["card"], closed["body"]), (None, "has-bar"))
                self.assertEqual(closed["backdrop"]["display"], "none")
                self.assertFalse(report["layer_after_close"])                 # nothing left over the board ...
                self.assertTrue(report["hit_after_close"])                    # ... a click reaches its frame
                self.assertTrue(report["esc_again_ask"])                      # Esc again: the ask bar

    def test_the_page_says_laid_at_the_open_and_the_still_goes_with_it(self) -> None:
        # Part 5c fixes: the open's "laid" was never checked (the still's time limit lifted it all the same); here the
        # limit is 5 s, so only the page's "laid" and the view's own check can lift it. Round 2 (changed on purpose):
        # "laid" now usually comes before Qt shows the new layout, so the still goes by the view's own check.
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                steps = report["open_steps"]
                laid = [step for step in steps if step[0] == "laid"]
                self.assertTrue(laid, steps)
                self.assertNotIn("back", [step[0] for step in steps])         # before any Esc
                self.assertGreaterEqual(report["big_counts"].get("shown:open", 0), 1)   # the view's own check ...
                self.assertLess(laid[0][1] - report["opened_at"], 5.0)        # ... well before its time limit
                self.assertNotIn("laid:ignored", report["big_counts"])
                self.assertFalse(report["veil_after"])

    def test_the_still_lifts_only_once_qt_shows_the_new_layout(self) -> None:
        # Part 5c fixes, round 2: the still went with the page's "laid" while Qt still showed the view's old frame at its
        # new size for 30-100 ms (open: the narrow board over the conversation; close: the wide frame's left part), and
        # the big frame could show at full size before its growth started. The window is read the moment the still is
        # cleared: the page's own layout mark must be there, magenta wide, cyan at its own size.
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                for motion in ("lively", "off"):
                    self.assertEqual(report[f"lift_marks_{motion}"], [["open", "#ff00ff"], ["closed", "#00ffff"]],
                                     motion)
                # Round 3 (changed on purpose): the quick routes below run with the real cap, which lifts a still whose
                # in-between frame never shows (veil_late, by design); none of the other steps here ever needs it.
                self.assertEqual(sum(report["veil_late_outside_routes"].values()), 0)
                # Added held: unseen and untouched, nothing dimmed yet, and it has the focus.
                self.assertEqual(report["held_at_add"], ["0", "none", False, True])
                self.assertTrue(report["stray_laid_veil"])                    # a "laid" too early never lifts it
                # Esc as the frame starts growing (lively): the open's still goes once Qt shows the wide layout, while
                # the scale-down runs (it is seen), then the close's once the board is its own size again.
                self.assertEqual(report["back_steps"], ["back", "shut", "laid"])
                self.assertEqual(report["back_marks"], [["closing", "#ff00ff"], ["closed", "#00ffff"]])

    def test_a_step_asked_while_the_view_holds_the_other_step_s_frame_lifts_on_its_own_layout(self) -> None:
        # Part 5c fixes, round 3 (stale-big 0): the check was armed at once, so an open asked right after the close's
        # "shut" (or as its "laid" was made ready) lifted on the old wide frame Qt still showed, and a close asked as the
        # big frame was added lifted on the old narrow one - then the in-between frames showed. Each still is read as it
        # is cleared (the layout mark, and the entry mark that tells one wide entry from the next) and every render of
        # the view for 150 ms after it.
        for case in ("big", "big_x15"):
            report = self.check(case)
            for name, route in report["routes"].items():
                with self.subTest(case=case, route=name):
                    want = "closed" if name.startswith("esc-at-grow") else "open"
                    layout = "#ff00ff" if want == "open" else "#00ffff"
                    self.assertEqual(route["state"], want, route)
                    self.assertTrue(route["lifts"], route)
                    for state, mark, _entry in route["lifts"]:            # each still on its own step's layout ...
                        self.assertEqual(mark, "#ff00ff" if state in ("open", "closing") else "#00ffff", route)
                    self.assertEqual(route["lifts"][-1], [want, layout, route["entry"]], route)   # ... the page's last
                    for marks in route["after"]:                          # and no frame of another layout after it
                        self.assertEqual(marks, [layout, route["entry"]], route)

    def test_the_still_s_beat_cap_and_render_hook_stop_however_it_goes(self) -> None:
        # Part 5c fixes, round 3 (stale-big 2): with the real view - after each lift, after end_now and shutdown while a
        # still is up - no beat, no time limit, no Qt Quick window watched and no slot left on its afterRendering.
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                self.assertTrue(report["quick_window"])
                gone = [False, False, True, 0]
                for name in ("stops_open_lift", "stops_close_lift", "stops_end_now", "stops_shutdown"):
                    self.assertEqual(report[name], gone, name)
                for name in ("stops_before_end_now", "stops_before_shutdown"):   # (a still up, judged, all of it on)
                    self.assertEqual(report[name], [True, True, True, True, False, 1], name)

    def test_the_dims_move_together_with_the_frame_at_open_big_and_back(self) -> None:
        # Part 5c fixes, round 3 (item 7): the desk's entrance animation (filled) still held the board's and the bar's
        # opacity, so their dim jumped to 20 % at the open (fully dim under the big frame's first frame) and back at the
        # close, while the backdrop and the strip eased with the frame. The view's own pixels at each render: each
        # patch's dim (0 full light, 1 the dim's end) against the big frame's own progress, lively and calm.
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                desk_in, pill = report["entrance_after_desk_in"], report["entrance_after_pill"]
                self.assertEqual((desk_in["board"], desk_in["bar"], desk_in["desk_in"]), (0, 0, False))
                self.assertEqual((pill["pill_shown"], pill["pill"], pill["pill_arriving"]), (True, 0, False))
                for name, (lit_full, lit_dim) in report["dims_patches"].items():
                    self.assertGreater(lit_full - lit_dim, 3, name)       # each patch has light to lose
                samples = report["dims_samples"]
                for step in ("open", "close"):                            # both patches seen, in some motion
                    self.assertTrue(any({"backdrop", "board"} <= {sample[3] for sample in samples
                                                                   if sample[:2] == [motion, step]}
                                        for motion in ("lively", "calm")), (step, samples))
                frames: dict[tuple, list[float]] = {}
                for motion, step, render, name, dim, growth in samples:
                    self.assertLessEqual(abs(dim - growth), 0.1, (motion, step, render, name, dim, growth))
                    frames.setdefault((motion, step, render), []).append(dim)
                for key, dims in frames.items():                          # and together in each frame
                    self.assertLessEqual(max(dims) - min(dims), 0.1, (key, dims))

    def test_the_keyboard_never_goes_to_a_sibling_leaving_with_the_big_frame(self) -> None:
        # Part 5c fixes, round 3 (stale-big 1): the big frame's group (its frames filling the board's view, the big frame
        # the first of three) left for room; ending big handed the keyboard to the next frame in view - a sibling that
        # was leaving too - and it fell to <body>.
        for case in ("big", "big_x15"):
            report = self.check(case)
            for motion in ("lively", "off"):
                with self.subTest(case=case, motion=motion):
                    room = report[f"room_view_{motion}"]
                    self.assertEqual(room["first_in_view"][:2], room["group"][:2])   # the big frame, then its sibling
                    self.assertEqual(room["before"], "card")
                    self.assertEqual(room["left"], [True, True, True])            # its whole group left for room
                    self.assertIn(room["active"], ["frame:" + frame_id for frame_id in room["board"]])
                    for sibling in room["group"][1:]:                             # never handed to a leaving one
                        self.assertNotIn(sibling, room["trail"])

    def test_a_group_leaving_for_room_leaves_the_owner_s_body_focus_and_the_scroll_as_they_are(self) -> None:
        # Part 5c fixes, round 4 (stale-big 0): round 3's re-check after a group left gave the keyboard to the board
        # whenever the page's focus was on <body> - also a <body> focus the owner made by clicking beside the frames:
        # the frame in view took it and was scrolled whole into view, the page said the focus was in the board, and the
        # answer that made the room, parked while the owner read, never landed once quiet (DESIGN-SPEC 3.5). Round 3's
        # own case (changed on purpose): the owner's click now stays on <body>.
        for case in ("big", "big_x15"):
            report = self.check(case)
            for motion in ("lively", "off"):
                with self.subTest(case=case, motion=motion):
                    body = report["room_body"][motion]
                    self.assertTrue(body["head"], body)                           # a real click on a group's head ...
                    self.assertEqual(body["before"], ["body", True], body)         # ... left the focus on <body>
                    self.assertEqual(body["pick_before"], body["ref"], body)       # the frame the board would pick ...
                    self.assertLess(body["ref_top_before"], -10, body)             # ... cut at its top
                    self.assertGreater(body["ref_top_before"] + body["ref_height"], 6, body)
                    self.assertEqual(len(body["parked"]), 1, body)                # the owner reads: the answer parks
                    self.assertTrue(body["victim_left"], body)                    # its room: a whole group left
                    self.assertFalse(body["victim_shown"], body)
                    self.assertEqual(body["active"], "body", body)                # the focus stays where it was,
                    self.assertEqual(body["scroll"], body["scroll_before"], body)  # nothing scrolls,
                    self.assertAlmostEqual(body["ref_top"], body["ref_top_before"], delta=0.5, msg=body)
                    self.assertNotIn(True, [focus for _pointer, focus in body["posts"]], body)   # no reading focus,
                    self.assertEqual(body["landed"], body["parked"], body)        # and it lands once quiet
            with self.subTest(case=case, board="after"):                          # (the board as before the case:
                _frames, pinned = report["room_body_after"]                       # the lone to-do list, pinned ones
                self.assertEqual(pinned, [False, True, True])                     # under it)

    def test_a_close_s_laid_never_answers_an_open_asked_after_its_shut(self) -> None:
        # Part 5c fixes, round 2 (open-big 0): Enter between the page's "shut" and its narrow resize - the close's "laid"
        # lifted the new open's still before the page was laid out wide.
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                steps = report["enter_at_shut_steps"]
                self.assertEqual(steps[:3], ["back", "shut", "open"], steps)
                self.assertEqual(steps[3:].count("laid"), 1, steps)           # the open's own, nothing else
                self.assertEqual(report["enter_at_shut_ignored"], 0)
                self.assertEqual(report["enter_at_shut_state"], "open")
                self.assertFalse(report["enter_at_shut_veil"])

    def test_the_focus_goes_back_to_the_board_however_big_ends(self) -> None:
        # Part 5c fixes, round 2 (open-big 1): a Tab into the view while big left the page's focus on <body> (Esc then
        # gave it to nothing), and so did the big frame's group leaving for room.
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                self.assertEqual(report["tab_in_active"], "card")              # Tab in: the big frame
                self.assertEqual(report["closed"]["active"], "frame:" + report["page_id"])   # Esc: back to it
                self.assertEqual(report["room_before_active"], "card")
                self.assertTrue(report["room_left"])                           # its group left for room ...
                self.assertEqual(report["room_active"], "frame:" + report["room_ids"][0])   # ... the board has it
                self.assertEqual(report["room_empty_before_active"], "card")
                self.assertEqual(report["room_empty_board"], report["room_empty_ids"][1:])   # nothing else was left:
                self.assertEqual(report["room_empty_active"], "frame:" + report["room_empty_ids"][1])   # the next one

    def test_the_board_under_the_big_frame_takes_no_key_and_keeps_its_layout(self) -> None:
        # Part 5c fixes: Tab left the big frame for the hidden board (its strip, a frame's Close); and the board under
        # it was laid out for the wide view (three across), then again as it closed.
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                self.assertEqual(report["tab_trail"], ["card", "card", "card"])   # Shift+Tab, Tab, Tab: in the card
                self.assertEqual(report["big_after_tabs"], "open")
                self.assertEqual(report["inert_open"], [True, True, True, True])  # the board, its bar, strip, pill
                self.assertEqual(report["closed"]["inert"], [False] * 4)          # ... and back after
                for name in ("open", "resized"):
                    classes = report[name]["rootClass"].split()
                    self.assertNotIn("pairs", classes, name)                  # its layout as before open big
                    self.assertNotIn("trios", classes, name)
                self.assertIn("pairs", report["closed"]["rootClass"].split())  # 1232 x 640 meanwhile: pairs now

    def test_the_big_frame_shows_its_frame_s_new_picture(self) -> None:
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                self.assertNotEqual(report["card_picture_before"], report["card_picture_want"])
                self.assertEqual(report["card_picture"], report["card_picture_want"])

    def test_a_laid_is_only_ever_for_the_step_still_going_on(self) -> None:
        # Part 5c fixes: an open's "laid" made ready just before an Esc, or a close's just before an Enter, lifted the
        # next step's still too early; and an Esc while the backdrop decoded let the frame grow as it closed.
        for case in ("big", "big_x15"):
            with self.subTest(case=case):
                report = self.check(case)
                self.assertEqual(report["esc_at_open_steps"], ["back", "shut", "laid"])   # the close's only
                self.assertFalse(report["esc_at_open_veil"])
                steps = report["enter_at_close_steps"]
                self.assertEqual(steps[:3], ["back", "shut", "open"])
                self.assertEqual(steps[3:].count("laid"), 1)                  # the new open's own, nothing else
                self.assertEqual(report["enter_at_close_state"], "open")
                self.assertFalse(report["enter_at_close_veil"])
                self.assertEqual(report["esc_at_grow_steps"], ["back", "shut", "laid"])   # none from a growth
                self.assertIsNone(report["esc_at_grow_page"]["card"])
                self.assertEqual(report["esc_at_grow_page"]["active"], "frame:" + report["page_id"])


class ExpiryTests(BoardProbeCase):
    """Part 5c fixes, round 5 (focus 0): a closed frame's own end with the app's controller (tests/board_probe.py
    expiry_case), the page's expiry before the app's removal."""

    def test_a_closed_frame_s_own_end_keeps_the_keyboard_on_the_board(self) -> None:
        # The owner closed a frame from the keyboard (its Bring back has the focus). The page's own expiry hid that
        # Bring back before the app's removal came, and the keyboard fell to <body>: remove_group (the group's last
        # frame) and remove (not its last) then had nothing to give back, and the board's keys were dead. The
        # canvas-pins brief: while the keyboard is on that Bring back the row stays (the page holds it and tells the
        # app); once the keyboard is on the frame itself the hold goes and the expiry comes as before.
        report = self.report("expiry")
        self.assertTrue(report["ready"])
        for motion in ("lively", "off"):
            for route in ("group_last", "not_last"):
                with self.subTest(motion=motion, route=route):
                    row = report["expiry"][motion][route]
                    frame = row["frame"]
                    self.assertTrue(row["closed"])
                    self.assertEqual(row["after_delete"], f"bring_back:{frame}")
                    self.assertTrue(row["held"])                                # past CLOSE_S: still there ...
                    self.assertTrue(row["held_shown"])                          # ... its Bring back offered
                    self.assertEqual(row["held_active"], f"bring_back:{frame}")
                    self.assertEqual(row["holds"], [[frame, True], [frame, False]])
                    self.assertTrue(row["marked_before_expiry"])                # (the window's marks came first)
                    self.assertEqual(row["expired"], frame)                     # the page's own expiry ...
                    self.assertTrue(row["expired_first"])                       # ... while the app still had it
                    self.assertEqual(row["at_expiry"], f"frame:{frame}")        # the keyboard stays in the frame ...
                    self.assertEqual(row["reading_posts"], [])                  # ... with no reading report,
                    self.assertEqual(row["focus_posts"], [])                    # no focus report,
                    self.assertIn(row["cut"][0], ("bottom", "top"))             # (its row cut 10 px by the view's
                    self.assertGreaterEqual(row["cut"][1], 9)                   # edge: a focus that scrolls shows)
                    self.assertEqual(row["scroll_at_expiry"], row["scroll_before"])   # and no scroll
                    self.assertTrue(row["keys_in_window"])
                    self.assertEqual(row["keys_asked"], [])                     # Enter, P, Delete there: nothing
                    self.assertEqual(row["after_keys"], f"frame:{frame}")
                    self.assertTrue(row["left"])
                    self.assertEqual(row["group_left"], route == "group_last")  # remove_group, else remove
                    self.assertTrue(row["staying"], row["after"])               # then a staying frame has it
                    self.assertTrue(row["has_focus"])


class TypedFramesTests(BoardProbeCase):
    """Part 6: Ask's typed frames in the real page (tests/board_probe.py "typed")."""

    def test_the_model_s_frames_show_their_words_and_make_nothing_else(self) -> None:
        report = self.report("typed")
        self.assertTrue(report["ready"])
        self.assertEqual(report["dropped"], {"too_many": 1})                   # the fourth of one answer
        self.assertEqual(report["applied"], [True] * 7)
        page = report["page"]
        self.assertEqual(page["pwned"], "undefined")                            # no script of theirs ran
        self.assertEqual(page["links"], 0)                                      # no link, picture, frame, script
        self.assertEqual(page["scripts"], 2)                                    # board.js and qwebchannel.js only
        self.assertEqual(page["everywhere"], [])                                # canvas part 5A: the strip too
        for frame_id, sent in report["sent"].items():
            with self.subTest(frame=frame_id, kind=sent["kind"]):
                shown = page["frames"][frame_id]
                self.assertEqual(shown["badTags"], [])                          # only elements the renderers make
                self.assertEqual(shown["badAttributes"], [])                    # class, scope, aria-hidden, style
                self.assertEqual(shown["dataAttributes"], [])                   # nothing of the data in any of them
                self.assertEqual(shown["title"], sent["title"])
                for words in sent["words"]:
                    self.assertIn(words, shown["text"])                         # whole, as text
        self.assertIn("<script>window.pwned=2</script>", report["sent"]["t0"]["title"])
        self.assertIn("<U+202E>Events<U+2066>", page["frames"]["t0"]["text"])  # bidi shown, never obeyed

    def test_no_request_at_all(self) -> None:
        requests = self.report("typed")["requests"]
        self.assertEqual(requests["allowed_after"], requests["allowed_before"])
        self.assertEqual(requests["allowed_end"], requests["allowed_before"])
        self.assertEqual((requests["blocked_after"], requests["blocked_end"]), ({}, {}))

    def test_the_page_refuses_what_python_refuses(self) -> None:
        report = self.report("typed")
        self.assertEqual(report["python_refuses"], [True] * 15)
        self.assertEqual(report["page_refuses"], [False] * 15)
        self.assertEqual(report["bad_frames"], 0)

    def test_maths_is_made_from_the_tree_and_reads_as_python_says(self) -> None:
        report = self.report("typed")
        page = report["page"]
        self.assertEqual((page["fracTop"], page["fracBottom"], page["sup"], page["idx"], page["rad"]),
                         (["1"], ["3"], ["2"], ["3"], ["13"]))                  # the cube root of 1/3, and x squared
        self.assertEqual(page["answers"], ["x = 1 or x = 3", "0", "0"])
        lines = page["frames"]["t4"]["lines"] + page["frames"]["t5"]["lines"]
        self.assertEqual(lines[:8] + lines[9:17], report["plain_expected"])     # the answers' "0" apart

    def test_each_kind_s_own_marks(self) -> None:
        page = self.report("typed")["page"]
        self.assertEqual(page["hot"], ["Software", "2,900", "3,400", "+500"])   # the changed row, banded
        self.assertEqual(page["zeros"], 3)                                      # its zeros in INK_4
        self.assertEqual((page["ghost"], page["hotBars"]), (1, 1))              # the changed bar and its ghost tick
        self.assertEqual(page["delta"], ["+500", "+500"])                       # the delta lines' figures
        self.assertEqual(page["states"], ["Done", "Next", "Due", "Done", "Done", "Next", "", "", ""])
        self.assertEqual(page["today"], ["Today", "Today"])

    def test_no_word_cut_and_the_narrow_layouts(self) -> None:
        layout = self.report("typed")["layout"]
        for width in ("448", "300", "270"):
            with self.subTest(width=width):
                shown = layout[width]
                self.assertEqual(shown["frameWidth"], int(width))
                self.assertEqual(shown["cut"], [])
                self.assertFalse(shown["pageCut"])
        wide = layout["448"]
        self.assertEqual(len(wide["timelineColumns"].split()), 3)              # in a row ...
        self.assertEqual(len(set(wide["nodeTops"])), 1)
        self.assertEqual(wide["todayPosition"], "absolute")                    # ... TODAY a line across it
        self.assertEqual(len(wide["mathsColumns"].split()), 3)
        for width in ("300", "270"):                                           # a 300 px frame: 270 of content
            narrow = layout[width]
            self.assertEqual(len(narrow["timelineColumns"].split()), 1)       # one node a row ...
            self.assertEqual(narrow["nodeTops"], sorted(set(narrow["nodeTops"])))
            self.assertEqual(narrow["todayPosition"], "static")                # ... TODAY a row of its own
            self.assertEqual(len(narrow["mathsColumns"].split()), 2)           # a step's name over its line

    def test_opened_big_they_use_the_room(self) -> None:
        big = self.report("typed")["big"]
        self.assertEqual((big["table"]["state"], big["table"]["box"]), ("open", [1000, 700]))
        self.assertEqual((big["table"]["rows"], big["table"]["cellFont"]), (4, "15px"))
        self.assertEqual(len(big["timeline"]["timelineColumns"].split()), 6)  # six nodes, in one row
        self.assertEqual(len(set(big["timeline"]["nodeTops"])), 1)

    def test_opened_big_a_long_table_or_maths_fits_or_scrolls_with_its_cue(self) -> None:
        # Part 6 fixes (board 0): a 12-row table with its change words and source and an 8-step maths frame, at the card
        # sizes of a 1120x700 and a 900x600 window (860 x 550, 780 x 454).
        fit = self.report("typed")["fit"]
        self.assertEqual(set(fit), {"table_860x550", "maths_860x550", "table_780x454", "maths_780x454"})
        for name, case in fit.items():
            with self.subTest(case=name):
                top = case["top"]
                self.assertEqual(case["state"], "open")
                self.assertEqual(top["card"], [860, 550] if name.endswith("860x550") else [780, 454])
                self.assertTrue(top["focus"])                                    # the keys are the big frame's
                self.assertIn(top["fit"], ("fit-0", "fit-1", "fit-2", "fit-3"))   # never below the board's own
                height, shown = top["body"][0], top["body"][1]
                if height <= shown:                                              # it fits: nothing hidden at all
                    self.assertEqual(top["hidden"], [])
                    self.assertEqual((top["cut"], top["masked"], top["bar"]), ([False, False], False, 0))
                    if name.startswith("maths"):
                        self.assertTrue(top["answer"])                           # the boxed answer in sight
                    else:
                        self.assertEqual((top["lastRow"], top["source"]), (True, True))
                    continue
                self.assertEqual(top["fit"], "fit-3")                            # the smallest it may be, first
                self.assertEqual((top["cut"], top["masked"]), ([False, True], True))   # the cue: the end fades ...
                self.assertEqual(top["bar"], 8)                                  # ... and the thin bar shows
                keys, most, page = case["keys"], height - shown, max(40, shown - 40)
                self.assertEqual(keys["Down"], min(40, most))                                     # a line
                self.assertEqual(keys["PageDown"], min(most, keys["Down"] + page))                # a page
                self.assertEqual(keys["Space"], min(most, keys["PageDown"] + page))               # Space: a page
                self.assertEqual(keys["ShiftSpace"], max(0, keys["Space"] - page))                # Shift+Space: back
                self.assertEqual(keys["PageUp"], max(0, keys["ShiftSpace"] - page))
                self.assertEqual(keys["Up"], max(0, keys["PageUp"] - 40))
                self.assertEqual((keys["End"], keys["Home"]), (most, 0))
                end = case["end"]
                self.assertEqual((end["lastRow"], end["source"]), (True, True))  # End: the last row and the source
                self.assertEqual(end["cut"], [True, False])                      # (the start fades now)
        self.assertLessEqual(*fit["table_860x550"]["top"]["body"][:2])          # 1120x700: the 12 rows fit
        self.assertGreater(*fit["table_780x454"]["top"]["body"][:2])            # 900x600: they scroll
        self.assertLessEqual(*fit["maths_860x550"]["top"]["body"][:2])
        self.assertLessEqual(*fit["maths_780x454"]["top"]["body"][:2])

    def test_maths_stays_inside_its_frame(self) -> None:
        # Part 6 fixes (board 1): fractions and roots shrink and wrap; one still too wide reads inline (a/b).
        inside = self.report("typed")["inside"]
        self.assertEqual(inside["applied"], [True] * len(inside["applied"]))
        self.assertTrue(inside["pair"]["pairs"])
        # The long frame: its fraction always stays laid out (its sum wraps over its bar); a root stays laid out where
        # its radicand fits its line (the 518 px frame beside the pair; the square root at 300 px) and reads inline,
        # √(…), where it would wrap (beside a long step name at 448 px, and at 270 px).
        for name, widths, stacked in (("pair", [253, 253, 518], [1, 0, 3]), ("300", [300] * 3, [1, 0, 2]),
                                      ("270", [270] * 3, [1, 0, 1]), ("448", [448] * 3, [1, 0, 1])):
            with self.subTest(width=name):
                shown = inside[name]
                self.assertEqual([frame["width"] for frame in shown["frames"]], widths)
                self.assertFalse(shown["pageCut"])
                for frame in shown["frames"]:
                    self.assertEqual(frame["over"], [], frame["title"])          # every element in its content box
                self.assertEqual([frame["stacked"] for frame in shown["frames"]], stacked)
                lines = shown["frames"][2]["lines"]
                self.assertEqual(sum("√(" in line for line in lines), 3 - stacked[2])   # inline: bracketed
        narrow, wide = inside["fallback"]
        self.assertEqual((narrow["stacked"], narrow["inline"]), (0, True))
        self.assertEqual(narrow["text"], inside["fallback_expected"])           # as Python reads it
        self.assertEqual((wide["stacked"], wide["inline"], wide["over"]), (1, False, 0))

    def test_a_fraction_that_divides_reads_in_brackets_only_inline(self) -> None:
        # Part 7 fixes (part 6 fixes' carried-over low): inline, "6 ÷ 1/2" read as (6 ÷ 1)/2.
        inside = self.report("typed")["inside"]
        self.assertTrue(inside["divisor_applied"])
        laid, flat, again = inside["divisor"]
        self.assertEqual(inside["divisor_expected"], "6 ÷ (1/2)")              # as Python reads it ...
        self.assertEqual((flat["stacked"], flat["inline"], flat["text"]), (0, True, "6 ÷ (1/2)"))   # ... the page too
        for shown in (laid, again):                                             # laid out: the bar, no brackets
            self.assertEqual((shown["stacked"], shown["inline"]), (1, False))
            self.assertNotIn("(", shown["text"])


class CanvasProbeTests(BoardProbeCase):
    """The canvas (PLAN-canvas part 1) in the real engine: the page as Home at 1280 x 800 - the orb's three forms and
    the moves between them at each motion level, its states, the corners' words as text with nothing of them in an
    attribute, the buttons' words through the bridge, and the board's frames in the right-hand part. The orb is one
    2D canvas the page draws: what it shows is read as the page's own numbers (board.orb()) and as its pixels."""

    HOSTILE = ("<img", "<script", "javascript:", "onclick", "onmouseover", "iframe", "pwned", "example.com")
    FIT = 0.8875                      # 800 px high: 0.71 of it over the mock's 640 px stage

    def looks(self) -> dict:
        report = self.report("canvas")
        self.assertTrue(report["ready"])
        self.assertEqual(report["sent"], [True, True])
        return report

    def test_a_board_that_was_sent_no_orb_has_no_canvas(self) -> None:
        before = self.looks()["before"]
        self.assertFalse(before["made"])
        self.assertNotIn("canvas", before["html"])

    def test_the_rings_are_seen_at_an_angle_in_the_centre(self) -> None:
        rings = self.looks()["rings"]
        orb = rings["orb"]
        self.assertIn("canvas", rings["html"])
        self.assertEqual((orb["form"], orb["state"]), ("rings", "idle"))
        self.assertAlmostEqual(orb["flat"], 0, delta=0.03)                # turned away ...
        self.assertAlmostEqual(orb["shut"], 0, delta=0.03)                # ... each ring at its own depth
        self.assertAlmostEqual(orb["tilt"], 57, delta=9)                  # the mock's angle (it precesses a little)
        self.assertEqual((orb["name"], orb["axis"]), (0, 1))              # the axis lit, no name
        width, height = rings["win"]
        self.assertAlmostEqual(orb["centre"][0], width / 2, delta=1)      # in the centre
        self.assertAlmostEqual(orb["centre"][1], height * 0.478, delta=1)
        self.assertAlmostEqual(orb["size"], self.FIT, delta=0.01)
        self.assertTrue(orb["looping"])                                   # it moves
        self.assertGreater(rings["pixels"]["cyan"], 400)                  # Jarvis's cyan, lit
        self.assertEqual(rings["pixels"]["amber"], 0)
        self.assertEqual(rings["elements"], 1)                            # one canvas: no layer a ring
        self.assertEqual(rings["counts"]["canvas"], "rings")

    def test_the_turn_to_face_goes_a_little_past_and_the_rings_click_shut(self) -> None:
        report = self.looks()
        turn = report["turn"]
        flats = [sample[1] for sample in turn]
        shuts = [sample[2] for sample in turn]
        self.assertLess(flats[0], 0.3)                                    # it starts turned away
        self.assertGreater(max(flats), 1.03)                              # lively: past facing ...
        self.assertLess(max(flats), 1.3)                                  # ... a little
        self.assertAlmostEqual(flats[-1], 1, delta=0.01)                  # ... and back
        first_shut = next(sample[0] for sample in turn if sample[2] > 0.5)
        first_turned = next(sample[0] for sample in turn if sample[1] > 0.5)
        self.assertGreater(first_shut, first_turned)                      # the rings close after it has turned
        self.assertGreater(max(shuts), 1.01)                              # a touch through, as they click shut
        self.assertAlmostEqual(shuts[-1], 1, delta=0.01)
        settled = next(sample[0] for sample in turn
                       if all(abs(later[1] - 1) < 0.02 and abs(later[2] - 1) < 0.02
                              for later in turn if later[0] >= sample[0]))
        self.assertLess(settled, 1200)                                    # about three quarters of a second

    def test_the_badge_is_flat_facing_with_the_name_in_it(self) -> None:
        badge = self.looks()["badge"]
        orb = badge["orb"]
        self.assertEqual((orb["form"], orb["state"]), ("badge", "speaking"))
        self.assertEqual(badge["cls"], ["flat", "s-speaking"])
        self.assertAlmostEqual(orb["flat"], 1, delta=0.01)
        self.assertAlmostEqual(orb["shut"], 1, delta=0.01)                # every ring in one plane
        self.assertLess(orb["tilt"], 1.5)                                 # facing the owner
        self.assertEqual((orb["name"], orb["axis"]), (1, 0))              # JARVIS shows; the axis is gone
        self.assertAlmostEqual(orb["voice"], 0.8, delta=0.02)             # the main ring follows his voice
        self.assertAlmostEqual(orb["centre"][0], badge["win"][0] / 2, delta=1)

    def test_the_trip_to_the_corner_is_a_curve_with_a_trail_and_a_bounce(self) -> None:
        report = self.looks()
        trip, corner = report["trip"], report["corner"]
        width, height = corner["win"]
        moving = [sample for sample in trip if sample[7]]
        self.assertGreater(len(moving), 12)
        self.assertEqual(max(sample[6] for sample in moving), 3)          # lively: three ghosts behind it
        start, end = (width / 2, height * 0.478), (width - 112, height - 112)
        # a curve, out to the side first: half way along x it has hardly gone down
        half = next(sample for sample in trip if sample[3] > (start[0] + end[0]) / 2)
        self.assertLess(half[4], start[1] + (end[1] - start[1]) * 0.42)
        self.assertGreater(max(sample[3] for sample in trip), end[0] + 1)  # a little past its place ...
        sizes = [sample[5] for sample in trip]
        self.assertLess(min(sizes), corner["orb"]["size"] * 0.985)        # ... and its size settles with a bounce
        orb = corner["orb"]
        self.assertEqual((orb["form"], orb["travelling"], orb["ghosts"]), ("corner", False, 0))
        self.assertAlmostEqual(orb["centre"][0], end[0], delta=1)         # bottom right
        self.assertAlmostEqual(orb["centre"][1], end[1], delta=1)
        self.assertAlmostEqual(orb["size"] / self.FIT, 0.34, delta=0.02)  # about a third of its size
        self.assertEqual(orb["name"], 1)                                  # the badge, there
        back = report["rings_again"]["orb"]
        self.assertEqual((back["form"], back["travelling"]), ("rings", False))
        self.assertAlmostEqual(back["centre"][0], start[0], delta=1)      # back in the centre, the rings open again
        self.assertAlmostEqual(back["flat"], 0, delta=0.03)
        opening = [sample[2] for sample in report["back"]]
        self.assertLess(min(opening), -0.02)                              # they spring open a little wide

    def test_calm_plays_the_moves_slower_with_no_overshoot_and_no_trail(self) -> None:
        report = self.looks()
        turn, trip = report["calm_turn"], report["calm_trip"]
        self.assertLessEqual(max(sample[1] for sample in turn), 1.005)    # it turns to face, not past
        self.assertLessEqual(max(sample[2] for sample in turn), 1.005)
        self.assertAlmostEqual(turn[-1][1], 1, delta=0.02)
        self.assertEqual(max(sample[6] for sample in trip), 0)            # no trail
        width = report["corner"]["win"][0]
        self.assertLessEqual(max(sample[3] for sample in trip), width - 112 + 0.5)   # never past its place
        lively = [sample[0] for sample in report["trip"] if sample[7]][-1]
        calm = [sample[0] for sample in trip if sample[7]][-1]
        self.assertGreater(calm, lively * 1.35)                           # x1.6
        self.assertEqual(report["calm_corner"]["orb"]["centre"], report["corner"]["orb"]["centre"])

    def test_off_cuts_and_nothing_turns(self) -> None:
        report = self.looks()
        cut, badge = report["off_cut"]["orb"], report["off_badge"]["orb"]
        self.assertEqual((cut["form"], cut["k"]), ("rings", 0))
        self.assertEqual((cut["flat"], cut["shut"], cut["travelling"]), (0, 0, False))   # there at once
        self.assertAlmostEqual(cut["centre"][0], report["rings"]["win"][0] / 2, delta=1)
        self.assertFalse(cut["looping"])                                  # no loop draws it: nothing turns
        self.assertEqual((badge["flat"], badge["shut"], badge["name"]), (1, 1, 1))
        self.assertFalse(badge["looping"])
        self.assertEqual(report["off_still"], badge["frames"])            # and no frame was drawn while it rested

    def test_each_state_has_its_own_sign(self) -> None:
        report = self.looks()
        rings = report["rings"]
        listening = report["listening"]["orb"]
        self.assertAlmostEqual(listening["lean"][1], -25, delta=1.5)      # the rings lean toward the owner
        self.assertLess(listening["tilt"], rings["orb"]["tilt"] - 12)     # ... so they open up
        self.assertAlmostEqual(listening["lvl"], 0.62, delta=0.02)        # the tick ring follows the microphone
        self.assertEqual(report["listening"]["bars"], 5)                  # ... and the meter
        self.assertEqual(report["listening"]["level"], "Lvl −29 dB")
        thinking = report["thinking"]["orb"]
        self.assertLess(thinking["rate"]["ticks"], -6)                    # the rings counter-spin, faster
        self.assertGreater(thinking["rate"]["arc"], 2.5)
        self.assertLess(thinking["rate"]["front"], -2.5)
        self.assertGreater(thinking["rate"]["front2"], 2)
        self.assertAlmostEqual(rings["orb"]["rate"]["ticks"], 1, delta=0.05)
        self.assertGreater(report["needs"]["pixels"]["amber"], 25)        # an amber arc on the outer ring
        self.assertAlmostEqual(report["waiting"]["orb"]["rate"]["arc"], 0.4, delta=0.1)
        self.assertEqual(report["muted"]["cls"], ["muted", "s-idle"])
        self.assertTrue(report["muted"]["orb"]["muted"])
        self.assertLess(report["muted"]["pixels"]["cyan"], rings["pixels"]["cyan"] * 0.8)   # low, a slash across
        offline = report["offline"]
        self.assertEqual(offline["cls"], ["offline", "s-idle"])
        self.assertLess(offline["pixels"]["cyan"], rings["pixels"]["cyan"] * 0.2)   # the cyan gone steel
        self.assertGreater(offline["pixels"]["lit"], rings["pixels"]["lit"] * 0.5)

    def test_the_corners_words_are_text_and_nothing_of_them_is_in_an_attribute(self) -> None:
        report = self.looks()
        self.assertTrue(report["hostile_sent"])
        hostile = report["hostile"]
        self.assertEqual(hostile["texts"], {
            "state": "<script>window.pwned=31</script>", "clock": "<img src=http://example.com/c.png>",
            "hold": "\"><b onclick=pwn()>", "rec": "javascript:pwned=32 <iframe src=x>",
            "wake": "' onmouseover='pwned=33"})                           # every word whole, as text
        self.assertEqual(hostile["foreign"], 0)                           # no element was made of them
        self.assertEqual(hostile["pwned"], "undefined")                   # nothing ran
        self.assertGreater(len(hostile["attrs"]), 40)
        for attr in hostile["attrs"]:
            for mark in self.HOSTILE:
                self.assertNotIn(mark, attr)                              # no attribute carries a word of the data
        names = {attr.split("=", 1)[0] for attr in hostile["attrs"]}
        self.assertLessEqual(names, {"id", "class", "type", "role", "aria-label", "aria-hidden", "aria-live",
                                     "aria-pressed", "hidden", "viewBox", "focusable", "style", "width", "height"})
        for attr in hostile["attrs"]:
            if attr.startswith("style="):                                 # the orb's canvas's own place and size only
                self.assertRegex(attr, r"^style=((left|top|width|height): -?[0-9.]+px; ?)+$")
            elif attr.startswith(("width=", "height=")):
                self.assertRegex(attr, r"^(width|height)=[0-9]+$")

    def test_a_press_posts_the_button_s_own_word_and_forged_words_are_dropped(self) -> None:
        report = self.looks()
        # (part 3: at 1280 px the least-used buttons fold under More - still pressed here, as buttons; More posts no
        # word of its own, it opens their list)
        self.assertEqual(report["pressed"], [
            "Undo", "Tue 17 Nov · 9:41 AM", "History", "Briefing", "Approvals", "Follow-ups", "Meetings", "Agents",
            "Live", "Memory", "Settings", "More", "Desktop", "Minimise the window", "Maximise or restore the window",
            CLOSE_JARVIS, "Mute", "Stop"])                               # (part 5B: the window's close names Jarvis)
        self.assertEqual(report["went"], ["undo", "day", "history", "briefing", "approvals", "followups", "meetings",
                                          "agents", "live", "memory", "settings", "desktop", "minimize", "maximize",
                                          "close", "mute", "stop_rec"])
        self.assertEqual(report["console_before_forged"], {})             # the page said nothing on its console so far
        self.assertEqual(report["forged_went"], ["settings"])             # only the word on the list got through
        self.assertEqual(report["requests"]["dropped:go"], 3)
        self.assertEqual(report["requests"]["accepted:go"], 18)

    def test_the_documents_button_shows_and_posts_its_own_word(self) -> None:
        report = self.looks()                                          # build plan item 5: the one page change
        self.assertTrue(report["documents_sent"])
        self.assertEqual(report["documents"], ["Documents"])
        self.assertEqual(report["documents_went"], ["documents"])

    def test_the_page_itself_refuses_hostile_orb_and_canvas_messages(self) -> None:
        report = self.looks()
        self.assertEqual(set(report["page_refused"].values()), {"false"})
        self.assertEqual(len(report["page_refused"]), 10)
        after = report["after_refused"]
        self.assertEqual((after["orb"]["state"], after["orb"]["form"]), ("idle", "rings"))   # nothing changed
        self.assertEqual(after["texts"]["state"], "Standby")
        self.assertEqual(after["foreign"], 0)

    def test_frames_show_across_the_canvas_with_the_orb_in_its_corner(self) -> None:
        # Part 2: the frames' area is the whole canvas between the corners' rows and the caption's strip (it was the
        # right-hand part in part 1), and the orb's corner is kept free by the layout (CanvasLayoutTests).
        report = self.looks()
        shown = report["with_frames"]
        width, height = shown["win"]
        self.assertNotIn("board-off", shown["html"])
        frame, board, orb = shown["frame"], shown["board"], shown["orb"]
        self.assertEqual((board["x"], board["x"] + board["w"]), (28, width - 28))    # from edge to edge
        self.assertEqual(board["y"] + board["h"], height - 160)           # ... over the caption and the ask line
        # (part 3: one frame alone is wider and centred - CanvasFixesTests; it was at the top left)
        self.assertEqual(frame["y"], board["y"])                          # the first frame: at the top ...
        self.assertAlmostEqual(frame["x"] - 28, width - 28 - frame["x"] - frame["w"], delta=2)   # ... centred
        self.assertLessEqual(frame["x"] + frame["w"], width - 28)
        self.assertLessEqual(frame["y"] + frame["h"], board["y"] + board["h"])
        self.assertEqual(orb["form"], "corner")
        self.assertLess(frame["x"] + frame["w"], orb["centre"][0] - orb["main"] / 2 - 20)   # clear of the orb
        self.assertEqual((report["clear_pressed"], report["cleared"]), ("Clear view", 1))   # the board's own clear
        self.assertNotIn("Clear view", report["rings"]["buttons"])        # ... a button only while frames show
        folded = report["folded"]
        self.assertIn("board-off", folded["html"])                        # the desk left: the board is away
        self.assertEqual(folded["orb"]["form"], "rings")

    def test_open_big_grows_over_the_canvas_and_goes_back(self) -> None:
        report = self.looks()
        opened, shut = report["big_open"], report["big_shut"]
        width, height = report["with_frames"]["win"]
        self.assertEqual((opened["state"], opened["title"]), ("open", "Dana's budget"))
        self.assertFalse(opened["wide"])                                  # the board's box is its own: no box to keep
        self.assertEqual(opened["body"], [report["with_frames"]["board"][key] for key in ("x", "y", "w", "h")])
        left, top, wide, tall = opened["card"]
        self.assertEqual((left, top), (28, 58))                           # over the canvas, under the corners' row ...
        self.assertAlmostEqual(left + wide, width - 28, delta=1)
        self.assertAlmostEqual(top + tall, height - 150, delta=1)         # ... and over the ask line
        self.assertTrue(opened["dim"])
        self.assertAlmostEqual(float(opened["canvas"]), 0.2, delta=0.02)  # the orb and the corners dim with the board
        self.assertEqual((shut["state"], shut["layer"], shut["dim"]), ("", False, False))
        self.assertAlmostEqual(float(shut["canvas"]), 1, delta=0.02)
        self.assertEqual(report["big_steps"], ["laid", "shut", "laid"])   # the page's own steps, as beside a conversation

    def test_nothing_of_it_asks_the_network_for_anything(self) -> None:
        report = self.looks()
        self.assertEqual(report["blocked"], {})
        self.assertEqual(list(report["allowed"]), ["jarvis-board"])
        self.assertEqual(report["messages_refused"], {})


class CanvasLayoutTests(BoardProbeCase):
    """The canvas, part 2 (frames across the screen) in the real engine: the layout with 1, 3, 6, 9, 12 and 24 frames
    at 900x600, 1280x800, 1920x1080 and 2560x1440, and each kind in the new style with long words
    (tests/board_probe.py "canvas_layout")."""

    SIZES = ((900, 600), (1280, 800), (1920, 1080), (2560, 1440))
    COUNTS = (1, 3, 6, 9, 12, 24)

    def layouts(self) -> dict:
        report = self.report("canvas_layout")
        self.assertTrue(report["ready"])
        self.assertEqual(len(report["sizes"]), len(self.SIZES) * len(self.COUNTS))
        return report["sizes"]

    def test_nothing_overlaps_and_nothing_leaves_the_frames_area(self) -> None:
        for name, look in self.layouts().items():
            self.assertEqual(look["overlaps"], [], name)
            self.assertEqual(look["outside"], [], name)                   # nothing clipped by the area's edge
            self.assertEqual(look["empty"], [], name)                     # ... and nothing squeezed to nothing

    def test_the_orbs_corner_and_the_captions_strip_stay_free(self) -> None:
        for name, look in self.layouts().items():
            self.assertEqual(look["on_orb"], [], name)
            self.assertEqual(look["strip"], 160, name)                    # under the frames: the caption, the ask line
            width, height = look["win"]
            self.assertEqual(look["area"][0], 28, name)                   # from edge to edge of the canvas
            self.assertEqual(look["area"][0] + look["area"][2], width - 28, name)
            self.assertGreaterEqual(look["area"][1], 64, name)            # under the corners' rows

    def test_every_frame_is_on_the_screen_or_folded_to_a_tile(self) -> None:
        for name, look in self.layouts().items():
            count = int(name.split("@")[0])
            self.assertEqual(look["lost"], [], name)
            self.assertEqual(look["over"], 0, name)
            self.assertEqual(look["full"] + look["tiles"], count, name)
            self.assertEqual(len(look["items"]), count, name)

    def test_the_canvas_never_scrolls(self) -> None:
        for name, look in self.layouts().items():
            self.assertEqual(look["scroll"], ["visible", 0, 0, 0], name)

    def test_a_few_frames_are_all_whole_and_room_decides_how_many_fold(self) -> None:
        layouts = self.layouts()
        for name, look in layouts.items():
            self.assertTrue(look["settled"], name)                         # part 5A: read once the layout settled
        for size in ("1280x800", "1920x1080", "2560x1440"):
            for count in (1, 3, 6):
                self.assertEqual(layouts[f"{count}@{size}"]["tiles"], 0, f"{count}@{size}")
        self.assertEqual(layouts["24@2560x1440"]["tiles"], 0)             # a large screen holds all 24
        self.assertLessEqual(layouts["24@900x600"]["full"], 1)            # the smallest: tiles, one line each
        self.assertGreaterEqual(layouts["24@900x600"]["tiles"], 23)
        self.assertTrue(layouts["24@900x600"]["grid"]["tight"])
        self.assertGreater(layouts["24@1920x1080"]["full"], 6)
        for size in ("900x600", "1280x800", "1920x1080"):                 # more frames never show fewer in all
            shown = [layouts[f"{count}@{size}"]["full"] + layouts[f"{count}@{size}"]["tiles"] for count in self.COUNTS]
            self.assertEqual(shown, list(self.COUNTS))

    def test_the_newest_answers_first_frame_is_top_left_under_its_head(self) -> None:
        for name, look in self.layouts().items():
            first = next(item for item in look["items"] if item["id"] == "f1")
            if first["kind"] != "full":
                continue
            head = look["heads"][0]
            self.assertEqual((head["box"][0], head["box"][1]), (first["box"][0], look["area"][1]), name)
            self.assertEqual(head["box"][3], 30, name)                    # a slim strip over its first frame
            boxes = [item["box"] for item in look["items"]]
            if len(boxes) <= 3 and len({box[1] - (38 if box is first["box"] else 0) for box in boxes}) == 1:
                # part 3: one to three frames side by side are a band, centred (CanvasFixesTests)
                left = min(box[0] for box in boxes) - look["area"][0]
                right = look["area"][0] + look["area"][2] - max(box[0] + box[2] for box in boxes)
                self.assertAlmostEqual(left, right, delta=2, msg=name)
                self.assertEqual(first["box"][0], look["area"][0] + left, name)
            else:
                self.assertEqual(first["box"][0], look["area"][0], name)
            self.assertEqual(first["box"][1], head["box"][1] + 38, name)
            self.assertEqual(head["title"], "Dana's budget", name)

    def test_the_roomier_grid_is_used_while_few_frames_are_up(self) -> None:
        layouts = self.layouts()
        few, many = layouts["3@1920x1080"]["grid"], layouts["12@1920x1080"]["grid"]
        self.assertGreater(few["span"], many["span"])                     # wider frames while there is room
        self.assertLessEqual(few["rows"], many["rows"])

    def test_the_old_bar_and_strip_are_not_drawn(self) -> None:
        for name, look in self.layouts().items():
            self.assertEqual((look["bar"], look["strip_shown"]), ("none", "none"), name)
            self.assertIn("Clear view", look["buttons"], name)            # what the bar's Clear did: the corner's

    def test_nothing_from_data_is_in_an_attribute(self) -> None:
        report = self.report("canvas_layout")
        looks = list(report["sizes"].values())
        for found in report["long"].values():
            looks += [found["look"]] + found["looks"]
        allowed = {"role", "viewBox", "aria-hidden", "focusable", "type", "aria-label", "aria-labelledby", "id",
                   "tabindex", "pathLength", "aria-pressed", "aria-expanded", "alt", "decoding", "draggable", "src",
                   "scope", "aria-live", "hidden", "inert"}
        for look in looks:
            self.assertEqual(look["attr_hits"], [])                       # no mark of the data in any attribute
            self.assertEqual(look["pwned"], "undefined")
            self.assertEqual(look["foreign"], 0)
            self.assertLessEqual(set(look["attr_names"]), allowed)
        self.assertEqual(report["blocked"], {})
        self.assertEqual(list(report["allowed"]), ["jarvis-board"])
        self.assertEqual(report["messages_refused"], {})
        self.assertEqual(report["console"], {})

    def test_each_kind_with_long_words_is_in_the_new_style(self) -> None:
        report = self.report("canvas_layout")
        self.assertEqual(report["long_titles"][1:], [60, 40])
        for size, found in report["long"].items():
            frames = {frame["id"]: frame for frame in found["full"]}
            self.assertEqual(len(frames), 13, size)                       # every kind and state
            whole = [frame for frame in frames.values() if "sat" not in frame["cls"]]
            self.assertGreaterEqual(len(whole), 12, size)
            for frame in whole:
                name = f"{size} {frame['id']}"
                self.assertEqual(frame["radius"], "1px", name)            # square corners
                self.assertEqual(frame["position"], "absolute", name)
                self.assertIn("0px 0px 1", frame["glow"], name)           # a soft outer glow ...
                self.assertIn("inset", frame["glow"], name)               # ... and a faint inner one
                title = frame["title"]
                self.assertEqual((title["caps"], title["size"], title["weight"]), ("uppercase", "19px", "700"), name)
                self.assertIn("Chakra Petch", title["font"], name)
                self.assertTrue(title["inside"], name)                    # a 60-character title wraps inside it
                if frame["id"] != "k9":                                   # (the big number's title: its bar's label)
                    self.assertEqual(title["text"], LONG_TITLE, name)
                self.assertEqual(frame["meta"]["caps"], "uppercase", name)
                self.assertIn("JetBrains Mono", frame["meta"]["font"], name)
                self.assertEqual(frame["glyph"], "none", name)
                self.assertEqual(frame["wide"], [], name)                 # no word cut sideways
                self.assertFalse(frame["body"]["sideways"], name)
                # never silently clipped: a body its place cuts says so (MORE; Enter opens all of it)
                self.assertEqual(frame["body"]["fits"], not frame["body"]["cut"], name)
                self.assertEqual(frame["body"]["more"], "block" if frame["body"]["cut"] else "none", name)
                self.assertGreaterEqual(frame["smallest"], 10, name)      # the mono labels; words are 13 px
            self.assertIn("waits", frames["k1"]["cls"], size)             # amber for what waits on the owner
            self.assertEqual(frames["k1"]["border"], "rgb(245, 177, 74)", size)
            self.assertIn("245, 177, 74", frames["k1"]["glow"], size)
            self.assertIn("failed", frames["k12"]["cls"], size)
            self.assertIn("255, 122, 104", frames["k12"]["glow"], size)
            self.assertIn("empty", frames["k13"]["cls"], size)
            self.assertEqual(frames["k2"]["border"], "rgba(175, 240, 255, 0.8)", size)     # the mock's thin outline
            for look in [found["look"]] + found["looks"]:
                self.assertEqual((look["overlaps"], look["outside"], look["on_orb"], look["lost"]), ([], [], [], []), size)


LONG_TITLE = "A sixty character title that goes on and on to its very cap!"


class CanvasWaysTests(BoardProbeCase):
    """The canvas, part 2, in the real engine at 1280x800: the task card, the arrival at each motion level, hover, the
    keyboard, open big from a grid cell, the Pinned filter, Tidy, Clear view and Bring back (tests/board_probe.py
    "canvas_ways")."""

    def ways(self) -> dict:
        report = self.report("canvas_ways")
        self.assertTrue(report["ready"])
        return report

    @staticmethod
    def starts(samples: list) -> dict:
        found: dict = {}
        for sample in samples:
            for frame_id, delay in sample["boots"]:
                found.setdefault(frame_id, delay)
        return found

    def test_the_task_card_shows_what_he_is_doing_beside_the_orb(self) -> None:
        report = self.ways()
        self.assertEqual(report["task_sent"], [True, True])
        look = report["task_working"]
        card = look["card"]
        self.assertEqual(card["title"], "Working on it")
        self.assertEqual(card["words"], "<script>window.pwned=43</script>")      # the app's words: text, whatever they are
        self.assertEqual((look["pwned"], look["attr_hits"]), ("undefined", []))
        self.assertEqual(card["counts"], "2 steps · 1 source")
        self.assertEqual(card["blocks"], 4)                               # two blocks a finished step
        self.assertTrue(card["stop"])
        self.assertRegex(card["time"], r"^00:0[7-9]$")                    # the page counts on from the app's seconds
        self.assertTrue(card["free"])                                     # nothing else up: beside the orb
        left, top, width, height = card["box"]
        self.assertLess(left + width, 1280 / 2 - 100)                     # left of the orb in the centre
        self.assertLessEqual(top + height, 800 - 160)
        self.assertEqual(look["counts"]["task"], "working")
        self.assertEqual(report["stop_pressed"], "Stop")
        self.assertEqual(report["went"][0], "stop")                       # Stop asks the app, and nothing else

    def test_done_the_card_becomes_the_head_of_its_answers_frames(self) -> None:
        report = self.ways()
        self.assertTrue(report["task_done_sent"])
        after = report["lively_after"]
        self.assertIsNone(after["card"])
        self.assertEqual(after["counts"]["task"], "")
        self.assertEqual(len(after["heads"]), 1)
        head = after["heads"][0]
        self.assertEqual(head["title"], "Dana's budget")                  # its heading is the answer's topic
        self.assertRegex(head["counts"], r"^6 frames · 2 sources · 00:\d\d$")
        self.assertFalse(head["title_cut"])                               # the title before the counts
        self.assertTrue(head["shown"].startswith("6 frames"))             # a narrow head drops whole counts only
        self.assertTrue(head["counts"].startswith(head["shown"]))
        self.assertEqual(head["box"][:2], after["area"][:2])              # the head of the group, top left
        self.assertEqual(head["box"][3], 30)                              # shrunk to a small tile
        self.assertEqual((after["full"], after["tiles"]), (6, 0))
        self.assertEqual((after["overlaps"], after["outside"], after["on_orb"]), ([], [], []))

    def test_lively_frames_boot_one_after_another_with_decode_and_count_up(self) -> None:
        report = self.ways()
        samples = report["lively"]
        starts = self.starts(samples)
        self.assertEqual(sorted(starts), [f"f{n}" for n in range(1, 7)])
        delays = [starts[f"f{n}"] for n in range(1, 7)]
        self.assertEqual(delays, sorted(delays))                          # in reading order ...
        steps = [later - sooner for sooner, later in zip(delays, delays[1:])]
        self.assertTrue(all(80 <= step <= 100 for step in steps), steps)  # ... a short stagger apart
        self.assertGreaterEqual(max(sample["decoding"] for sample in samples), 3)      # headings decode
        values = {sample["value"] for sample in samples}
        figures = {sample["figure"] for sample in samples}
        self.assertGreater(len(values), 3)                                # the chart's numbers count up ...
        self.assertGreater(len(figures), 3)                               # ... and the big number
        last = samples[-1]
        self.assertEqual((last["boots"], last["decoding"], last["value"], last["figure"]), ([], 0, "1.2k", "+500"))
        self.assertEqual([step for _frame, step in report["landed_lively"]], ["outline"] * 6 + ["done"] * 6)

    def test_calm_boots_slower_with_no_decode_and_no_count_up(self) -> None:
        report = self.ways()
        calm, lively = self.starts(report["calm"]), self.starts(report["lively"])
        self.assertEqual(sorted(calm), sorted(lively))
        self.assertAlmostEqual(calm["f6"] - calm["f1"], (lively["f6"] - lively["f1"]) * 1.6, delta=6)    # x1.6
        self.assertEqual(max(sample["decoding"] for sample in report["calm"]), 0)
        self.assertEqual({sample["value"] for sample in report["calm"]}, {"1.2k"})
        self.assertEqual({sample["figure"] for sample in report["calm"]}, {"+500"})
        self.assertEqual(report["calm"][-1]["boots"], [])
        self.assertEqual(len(report["landed_calm"]), 12)

    def test_off_frames_are_there_at_once(self) -> None:
        report = self.ways()
        self.assertEqual(self.starts(report["off"]), {})                  # nothing boots
        self.assertEqual([step for _frame, step in report["landed_off"]], ["done"] * 6)
        self.assertEqual(report["off_after"]["full"], 6)

    def test_hover_brightens_a_frame_and_shows_its_ticks_and_controls(self) -> None:
        hover = self.ways()["hover"]
        self.assertTrue(hover["hovered"])
        self.assertEqual((hover["tick"], hover["controls"]), ("1", "1"))
        self.assertGreater(float(hover["glow"]), 0.5)
        self.assertEqual(hover["border"], "rgb(255, 255, 255)")
        self.assertEqual(hover["other"], "0")                             # the other frames keep theirs away

    def test_the_arrows_move_by_where_the_frames_are(self) -> None:
        report = self.ways()
        moves, boxes = report["moves"], report["move_boxes"]
        self.assertEqual(moves[0], ["start", "f1"])
        at = "f1"
        for name, to in moves[1:5]:
            here, there = boxes[at], boxes[to]
            self.assertNotEqual(to, at, name)
            if name == "Right":
                self.assertGreaterEqual(there[0], here[0] + here[2], name)
            elif name == "Left":
                self.assertLessEqual(there[0] + there[2], here[0], name)
            elif name == "Down":
                self.assertGreaterEqual(there[1], here[1] + here[3], name)
            else:
                self.assertLessEqual(there[1] + there[3], here[1], name)
            at = to
        self.assertEqual(moves[4][1], "f1")                               # right, down, left, up: back where it began
        self.assertEqual(moves[5], ["End", "f6"])
        self.assertEqual(moves[6], ["Home", "f1"])
        self.assertEqual(report["asked"], [["pinRequested", "f1", True], ["openRequested", "f1"]])   # P, then Enter

    def test_open_big_grows_from_its_grid_cell_and_goes_back(self) -> None:
        report = self.ways()
        samples = [sample for sample in report["big"] if sample["big"]]
        frame = report["big"][0]["box"]
        first, last = samples[0]["big"], samples[-1]["big"]
        for mine, its in zip(first, frame):
            self.assertAlmostEqual(mine, its, delta=30)                   # it starts at its frame's own place ...
        self.assertEqual([round(value) for value in last], [28, 58, 1224, 592])    # ... and fills the canvas
        widths = [sample["big"][2] for sample in samples]
        self.assertEqual(widths, sorted(widths))                          # it only ever grows
        self.assertEqual(report["big_shut"], "/false")

    def test_pinned_is_a_filter_in_the_corner_only_while_something_is_pinned(self) -> None:
        report = self.ways()
        self.assertNotIn("Pinned", " ".join(report["before_pin"]))
        self.assertIn("Pinned 1", report["pinned_buttons"])
        self.assertEqual(report["pinned_pressed"], "Pinned 1")
        only = report["pinned_only"]
        self.assertEqual(only["filter"], "pinned")
        self.assertEqual(sorted(item["id"] for item in only["items"] if item["shown"]), ["f1", "f3"])
        self.assertEqual((report["pinned_all"]["filter"], report["pinned_all"]["full"]), ("all", 6))
        self.assertNotIn("Pinned", " ".join(report["end"]["buttons"]))    # nothing pinned any more: no button

    def test_a_close_moves_nothing_and_tidy_packs_again_with_a_spring(self) -> None:
        report = self.ways()
        before = {item["id"]: item["box"] for item in report["before_close"]["items"]}
        closed = {item["id"]: item for item in report["closed"]["items"]}
        self.assertTrue(closed["f2"]["closed"])
        gap = {item["id"]: item["box"] for item in report["gap"]["items"]}
        self.assertNotIn("f2", gap)
        for frame_id, box in gap.items():
            self.assertEqual(box, before[frame_id], frame_id)             # the others stay where they are
        self.assertTrue(report["gap"]["untidy"])
        self.assertIn("Tidy", report["gap"]["buttons"])                   # ... and Tidy shows: there is a gap
        self.assertNotIn("Tidy", report["before_close"]["buttons"])
        self.assertEqual(report["tidy_pressed"], "Tidy")
        self.assertIn("tidy", report["went"])
        tidied = report["tidied"]
        self.assertFalse(tidied["untidy"])
        self.assertNotIn("Tidy", tidied["buttons"])
        self.assertEqual((tidied["overlaps"], tidied["outside"], tidied["on_orb"]), ([], [], []))
        after = {item["id"]: item["box"] for item in tidied["items"]}
        self.assertNotEqual(after["f6"][:2], gap["f6"][:2])               # it moved ...
        xs = [sample["box"][0] for sample in report["tidy"]]
        end = after["f6"][0]
        self.assertAlmostEqual(xs[-1], end, delta=0.5)
        past = min(xs) if xs[0] > end else max(xs)
        self.assertGreater(abs(past - end), 4)                            # ... gliding a little past its place and back
        self.assertGreater(len({round(x) for x in xs}), 6)                # (a glide, not a cut)

    def test_clear_view_draws_each_frame_into_the_orb_before_it_leaves_its_corner(self) -> None:
        report = self.ways()
        samples = report["clear"]
        self.assertEqual(max(sample["lines"] for sample in samples), 5)   # a bright line each
        self.assertEqual(max(sample["folding"] for sample in samples), 5)
        flying = [sample for sample in samples if sample["lines"]]
        self.assertTrue(all(sample["form"] == "corner" for sample in flying))     # the orb waits for them there ...
        self.assertEqual(samples[-1]["form"], "rings")                    # ... then goes back to the centre
        self.assertEqual((samples[-1]["lines"], samples[-1]["folding"]), (0, 0))
        cleared = report["cleared"]
        self.assertEqual((cleared["full"], cleared["tiles"], cleared["lines"]), (0, 0, 0))
        self.assertEqual(report["cleared_row"], "Board cleared5 framesBring back")
        self.assertEqual(report["cleared_orb"]["form"], "rings")
        self.assertAlmostEqual(report["cleared_orb"]["centre"][0], 640, delta=1)

    def test_bring_back_lets_them_out_again(self) -> None:
        report = self.ways()
        samples = report["back"]
        self.assertEqual(max(sample["lines"] for sample in samples), 5)
        self.assertEqual(samples[-1]["form"], "corner")
        back = report["brought_back"]
        self.assertEqual((back["full"], back["tiles"], back["lines"]), (5, 0, 0))
        self.assertEqual((back["overlaps"], back["outside"], back["on_orb"]), ([], [], []))
        tidied = {item["id"]: item["box"] for item in report["tidied"]["items"]}
        self.assertEqual({item["id"]: item["box"] for item in back["items"]}, tidied)     # each in its place again

    def test_a_card_with_frames_up_takes_a_place_on_the_grid_and_goes_when_told(self) -> None:
        report = self.ways()
        card = report["card_again"]
        self.assertFalse(card["free"])
        self.assertIsNone(report["card_gone"])
        self.assertEqual((report["end"]["overlaps"], report["end"]["outside"]), ([], []))

    def test_hostile_task_and_number_messages_are_refused(self) -> None:
        report = self.ways()
        self.assertEqual(set(report["page_refused"].values()), {"false"})
        self.assertEqual(len(report["page_refused"]), 7)
        self.assertEqual(set(report["python_refused"].values()), {False})
        self.assertEqual(report["went_end"], ["stop", "tidy", "tidy"])    # a forged word is dropped; "tidy" is on the list
        end = report["end"]
        self.assertEqual((end["pwned"], end["attr_hits"], end["foreign"]), ("undefined", [], 0))
        self.assertEqual(report["blocked"], {})
        self.assertEqual(list(report["allowed"]), ["jarvis-board"])


class CanvasFixesTests(BoardProbeCase):
    """The canvas, part 3's real-screen fixes in the real engine at the owner's 1389x868 (tests/board_probe.py
    "canvas_fixes"): the top right row on one line with every button, the frames' area never moving, More's list; the
    wake badge's longest words whole; one to three frames using the screen; the task card at the mock's size, the frames
    never moving as it comes and goes."""

    ROWS = ("row_empty", "row_frames", "row_pinned", "row_tidy", "row_open", "row_after_item", "card_row")

    def fixes(self) -> dict:
        report = self.report("canvas_fixes")
        self.assertTrue(report["ready"])
        self.assertTrue(report["corners_sent"])
        return report

    def test_the_top_row_is_one_line_with_every_button_and_the_frames_area_never_moves(self) -> None:
        report = self.fixes()
        for name in self.ROWS + ("row_900x600", "row_1280x800", "row_1920x1080"):
            row = report[name]
            self.assertEqual(row["spread"], 0, name)                       # one line: every middle at the same height
            self.assertLessEqual(row["row"][3], 26, name)
            self.assertEqual(row["area_top"], 64, name)                    # the frames' area: never moved by a button
            self.assertGreaterEqual(row["row"][0], row["tl"][0] + row["tl"][2] + 20, name)   # clear of the top left
            self.assertEqual(row["row"][0] + row["row"][2], row["win"][0] - 28, name)
            self.assertLessEqual(row["tl"][1] + row["tl"][3], 64, name)
            for word in ("Approvals 12", "Minimise the window", "Maximise or restore the window", CLOSE_JARVIS):
                self.assertIn(word, row["shown"], name)                    # never folded
        tidy = report["row_tidy"]
        for word in ("Tidy", "Clear view", "Pinned 1", "More"):
            self.assertIn(word, tidy["shown"])                             # every button at once, on the row ...
        self.assertEqual(sorted(tidy["shown"] + [item["text"] for item in tidy["items"]]),
                         sorted(["History", "Tidy", "Clear view", "Pinned 1", "Briefing", "Approvals 12", "Follow-ups",
                                 "Meetings", "Agents", "Live", "Memory", "Settings", "More", "Desktop",
                                 "Minimise the window", "Maximise or restore the window", CLOSE_JARVIS]))   # or More
        self.assertNotIn("More", report["row_1920x1080"]["shown"])         # room for all: no More
        self.assertEqual(report["row_1920x1080"]["layout"]["top"]["folded"], [])

    def test_mores_list_is_over_the_frames_and_an_item_presses_its_own_button(self) -> None:
        report = self.fixes()
        self.assertEqual(report["more_pressed"], "More")
        opened = report["row_open"]
        menu = opened["menu"]
        self.assertIsNotNone(menu)
        self.assertGreaterEqual(menu[1], opened["row"][1] + opened["row"][3])      # under the row ...
        self.assertEqual(opened["menu_z"], "25")                                    # ... over the frames
        self.assertFalse(opened["menu_in_canvas"])
        self.assertLessEqual(menu[0] + menu[2], opened["win"][0])
        self.assertEqual([item["text"] for item in opened["items"]], ["Follow-ups", "Meetings", "Agents", "Live", "Memory"])
        self.assertEqual(report["item_pressed"], "Memory|true")             # pressed: the list closes
        self.assertEqual(report["went"], ["memory"])                        # ... and the button's own word went
        self.assertIsNone(report["row_after_item"]["menu"])

    def test_the_wake_badges_longest_words_are_whole(self) -> None:
        report = self.fixes()
        longest, size = report["wake_words"]
        for name in self.ROWS + ("row_900x600", "row_1280x800", "row_1920x1080"):
            wake = report[name]["wake"]
            self.assertEqual(wake["text"], longest, name)                   # every letter: never cut
            self.assertFalse(wake["cut"], name)
            self.assertLessEqual(wake["box"][0] + wake["box"][2], report[name]["win"][0] / 2 - 262, name)  # left of the ask line
        self.assertGreater(size, 24)                                        # (part 2 cut it at 24)

    def test_one_to_three_frames_use_the_screen(self) -> None:
        few = self.fixes()["few"]
        for name, look in few.items():
            self.assertEqual((look["overlaps"], look["on_orb"], look["outside"], look["lost"]), ([], [], [], []), name)
            boxes = [item["box"] for item in look["items"]]
            left = min(box[0] for box in boxes) - look["area"][0]
            right = look["area"][0] + look["area"][2] - max(box[0] + box[2] for box in boxes)
            self.assertLessEqual(abs(left - right), 2, name)               # the band centred: no third left black
            if len(boxes) > 1:
                self.assertLess(left + right, look["area"][2] / 3, name)
        self.assertEqual(few["2"]["grid"]["span"], 6)                       # two at 1389: each wider, the row filled
        self.assertEqual((few["2"]["items"][0]["box"][0], few["3"]["items"][0]["box"][0]), (28, 28))
        self.assertGreater(few["1"]["items"][0]["box"][2], 600)             # one: wider too, and centred
        self.assertEqual(few["2@1920x1080"]["grid"]["span"], 6)             # (at 1920 too: two of 925 px)

    def test_the_task_card_keeps_the_mocks_size_and_the_frames_never_move(self) -> None:
        report = self.fixes()
        before = {item["id"]: item["box"] for item in report["card_before"]["items"]}
        for name in ("card_up", "card_step", "card_gone"):
            self.assertEqual({item["id"]: item["box"] for item in report[name]["items"]}, before, name)
            self.assertFalse(report[name]["untidy"], name)                  # ... and Tidy never shows for it
            self.assertNotIn("Tidy", report[name]["buttons"], name)
            self.assertEqual((report[name]["overlaps"], report[name]["on_orb"], report[name]["outside"]), ([], [], []),
                             name)
        up, alone = report["card_up"]["card"], report["card_alone"]["card"]
        self.assertFalse(up["free"])                                        # a free place among the frames ...
        self.assertEqual(up["box"][2], 372)                                 # ... at the mock's size
        self.assertEqual(up["box"][2:], alone["box"][2:])                   # the same card as beside the orb alone
        self.assertEqual(report["card_step"]["card"]["box"], up["box"])     # a step on: it stays where it is
        self.assertIsNone(report["card_gone"]["card"])

    def test_nothing_asks_the_network_and_nothing_was_refused(self) -> None:
        report = self.fixes()
        self.assertEqual(report["blocked"], {})
        self.assertEqual(list(report["allowed"]), ["jarvis-board"])
        self.assertEqual(report["messages_refused"], {})
        self.assertEqual(report["console"], {})


class CanvasRestingTests(BoardProbeCase):
    """The canvas, part 3B: the two resting frames in the real engine at the owner's 1389x868 (also 1920x1080 and
    1280x800; tests/board_probe.py "canvas_resting"): long words, many rows and markup shown whole as text with
    nothing of them in an attribute; "waits for your OK" amber with no chip; each frame's one button inside it, posting
    its corner's own word, and hidden while that corner button is; the page's own refusals of hostile bodies."""

    def resting(self) -> dict:
        report = self.report("canvas_resting")
        self.assertTrue(report["ready"])
        self.assertTrue(report["corners_sent"])
        return report

    def test_long_words_and_many_rows_show_whole_as_text_and_nothing_overlaps(self) -> None:
        report = self.resting()
        approvals, agents = report["built"]
        for name in ("long", "long_1920x1080", "long_1280x800"):
            shown = report[name]
            self.assertEqual(shown["r1"]["ap"], [[row["kind"], row["title"]] for row in approvals["body"]["rows"]], name)
            self.assertEqual([row[0] for row in shown["r2"]["ag"]], [row["name"] for row in agents["body"]["rows"]],
                             name)
            self.assertEqual(shown["r1"]["more"], ["+8 more wait"], name)       # 12 wait, 4 shown
            self.assertEqual(shown["r2"]["more"], ["+2 more agents"], name)     # 10 agents, 8 rows
            self.assertEqual(shown["r2"]["waits"], "5 proposals wait", name)
        for name in ("look", "look_1920x1080", "look_1280x800"):
            look = report[name]
            self.assertEqual((look["overlaps"], look["outside"], look["on_orb"], look["lost"]), ([], [], [], []), name)
            self.assertEqual(look["attr_hits"], [], name)                     # nothing of the data in an attribute
            self.assertEqual((look["pwned"], look["foreign"]), ("undefined", 0), name)
        for one in report["style"]:
            self.assertEqual(one["wide"], [], one["id"])                       # no word cut sideways
            self.assertFalse(one["body"]["sideways"], one["id"])
            self.assertTrue(one["title"]["inside"], one["id"])
            self.assertEqual(one["body"]["more"], "block" if one["body"]["cut"] else "none", one["id"])  # says CUT

    def test_the_rows_say_the_agents_screens_words(self) -> None:
        rows = self.resting()["long"]["r2"]["ag"]
        self.assertEqual(rows[0][1:], ["On", "Ran Yesterday 12:41 PM · 3 proposed · Next Tomorrow 7:30 AM",
                                       "2 proposals wait", "ag-row on"])
        self.assertEqual(rows[1][1:], ["Paused", "Ran Mon 9:41 AM · Nothing new", "", "ag-row paused"])
        self.assertEqual(rows[2][1:], ["Not on yet", "Not run yet", "", "ag-row off"])

    def test_waits_for_your_ok_is_amber_without_a_chip(self) -> None:
        report = self.resting()
        for name in ("long", "short"):
            waits = report[name]["r1"]
            self.assertIn("waits", waits["cls"], name)
            self.assertEqual(waits["border"], "rgb(245, 177, 74)", name)
            self.assertEqual(waits["chip"], "none", name)                      # its button is its own
            self.assertEqual(waits["go"]["border"], "rgb(245, 177, 74)", name)
            self.assertNotIn("waits", report[name]["r2"]["cls"], name)         # the Agents frame: cyan, an amber line
        self.assertEqual(report["short"]["r2"]["waits"], "")                   # nothing waits: no amber line

    def test_one_button_each_inside_its_frame_posting_its_corner_word(self) -> None:
        report = self.resting()
        for name in ("long", "long_1920x1080", "long_1280x800", "short", "with_again"):
            for frame_id, label in (("r1", "Open Approvals"), ("r2", "Open Agents")):
                frame = report[name][frame_id]
                self.assertEqual(frame["buttons"], [label], name)              # the only control in its body
                self.assertTrue(frame["go"]["shown"] and frame["go"]["inside"], name)
        self.assertEqual(report["pressed"], ["Open Approvals", "Open Agents"])
        self.assertEqual(report["went"], ["approvals", "agents"])             # the corners' own words, nothing else
        for frame_id in ("r1", "r2"):
            self.assertTrue(report["corners_without"])
            self.assertTrue(report["without"][frame_id]["go"]["hidden"])       # its corner button gone: so is it
            self.assertEqual(report["without"][frame_id]["buttons"], [])
        self.assertEqual(report["pressed_without"], ["", ""])
        self.assertEqual(report["big"], {"cls": ["big-card", "frame"], "rows": 8, "buttons": ["Open Agents"]})

    def test_the_page_refuses_hostile_bodies_and_nothing_asks_the_network(self) -> None:
        report = self.resting()
        self.assertEqual(set(report["page_refused"].values()), {False})
        self.assertEqual(report["frames_after_hostile"], report["frames_before_hostile"])
        self.assertEqual(report["blocked"], {})
        self.assertEqual(list(report["allowed"]), ["jarvis-board"])
        self.assertEqual(report["messages_refused"], {})
        self.assertEqual(report["console"], {"console:warning": len(report["page_refused"])})   # one per refusal

    def test_the_resting_group_put_under_an_answer_moves_none_of_its_frames(self) -> None:
        report = self.resting()
        before = {item["id"]: item["box"] for item in report["below_before"]["items"]}
        after = {item["id"]: item["box"] for item in report["below_after"]["items"]}
        self.assertEqual(sorted(before), ["f1", "f2", "f3"])
        self.assertEqual({key: after[key] for key in before}, before)        # the answer's places: unchanged
        self.assertEqual(report["below_order"], ["f1", "f2", "f3", "n1", "n2"])   # "Now" under the answer
        look = report["below_after"]
        self.assertEqual((look["overlaps"], look["outside"], look["on_orb"], look["lost"]), ([], [], [], []))

    def test_the_corner_now_shows_only_when_the_app_says_and_posts_its_word(self) -> None:
        report = self.resting()
        self.assertIn("Now", report["row_now"])
        self.assertEqual(report["now_pressed"], "Now")
        self.assertEqual(report["went_now"][-1], "now")
        self.assertNotIn("Now", report["row_without_now"])

    def test_a_short_resting_frame_is_only_as_tall_as_what_it_holds(self) -> None:
        # Canvas part 5A (part 3's leftover): one waiting card, one agent - never a whole row of empty room.
        report = self.resting()
        for name in ("short_look", "short_look_1920"):
            look = report[name]
            self.assertEqual((look["overlaps"], look["outside"], look["on_orb"], look["lost"]), ([], [], [], []),
                             name)
        look = report["short_look_1920"]                                    # a grid row is tallest here (276 px)
        grid, area = look["grid"], look["area"]
        row = (area[3] - (grid["rows"] - 1) * grid["gap"]) / grid["rows"]
        boxes = {item["id"]: item["box"] for item in look["items"]}
        for frame_id in ("r1", "r2"):                                       # less than a whole row each
            self.assertLess(boxes[frame_id][3], row - grid["gap"], f"{frame_id} {row:.0f}")

    def test_a_day_with_nothing_booked_is_only_as_tall_as_its_words(self) -> None:
        report = self.resting()
        look = report["nothing_booked"]
        boxes = {item["id"]: item["box"] for item in look["items"]}
        self.assertEqual(report["nothing_booked_words"], "Nothing booked today")
        self.assertLess(boxes["d1"][3], 140)                                # short, never a row of empty box ...
        self.assertLess(boxes["d1"][3], boxes["r2"][3])                     # ... shorter than a frame with rows
        self.assertEqual((look["overlaps"], look["outside"], look["on_orb"], look["lost"]), ([], [], [], []))


class CanvasHearTests(BoardProbeCase):
    """The window brief in the real engine at the owner's 1389x868 (tests/board_probe.py "canvas_hear"): HEY JARVIS's
    three states, amber only for can't hear you; the can't-hear note with the microphone's name as text and its one
    button; hostile names and messages; the top band the page reports for Windows' hit-test and the maximise button's
    hover."""

    AMBER = "rgb(245, 177, 74)"
    STEAM = "Microphone (Steam Streaming Microphone)"

    def hear(self) -> dict:
        report = self.report("canvas_hear")
        self.assertTrue(report["ready"])
        self.assertTrue(all(report["sent"].values()))
        return report

    def test_hey_jarvis_listening_paused_and_cant_hear_you_amber_only_then(self) -> None:
        report = self.hear()
        words = {"listening": "Listening", "paused": "Paused", "cant_hear": "Can\u2019t hear you"}
        for state, word in words.items():
            hear = report[state]["hear"]
            self.assertEqual(hear["text"], "Hey Jarvis" + word, state)         # the mark and the page's own word
            self.assertIn("s-" + state, hear["cls"])
            self.assertFalse(hear["cut"], state)
            self.assertEqual(hear["color"] == self.AMBER, state == "cant_hear", state)
            self.assertEqual(hear["dot"] == self.AMBER, state == "cant_hear", state)
            self.assertEqual(report[state]["wake"], "", state)                 # (the app's few words not twice)
            self.assertLessEqual(hear["box"][0] + hear["box"][2], report[state]["ask_left"], state)
        self.assertIsNone(report["other"]["hear"])                             # another problem: the app's words
        self.assertEqual(report["other"]["wake"], "Not listening: mic busy")
        self.assertIsNone(report["off"]["hear"])
        self.assertIsNone(report["off"]["note"])
        for state in ("listening", "paused", "other"):
            self.assertIsNone(report[state]["note"], state)                    # no note while he can hear

    def test_the_note_says_plainly_what_to_do_with_its_one_button(self) -> None:
        report = self.hear()
        note = report["cant_hear"]["note"]
        self.assertEqual(note["text"], "Jarvis can\u2019t hear you. The microphone is " + self.STEAM
                         + ". Pick yours in Settings\u00a0>\u00a0Talk.")          # (part 5B: Talk)
        self.assertEqual(note["head"], "rgb(255, 208, 138)")                   # its first words amber
        self.assertEqual((note["button"]["text"], note["button"]["cls"]), ("Pick a microphone", ["c-btn", "need"]))
        self.assertFalse(note["cut"])
        self.assertEqual(report["talk_deaf"]["note"]["text"],
                         "Jarvis can\u2019t hear you. The microphone is Desk mic. Pick yours in Settings\u00a0>\u00a0Talk.")
        self.assertIsNone(report["talk_deaf"]["hear"])                         # "Hey Jarvis" off: the note alone
        self.assertEqual(report["no_name"]["note"]["text"],
                         "Jarvis can\u2019t hear you. Pick your microphone in Settings\u00a0>\u00a0Talk.")
        self.assertIsNone(report["no_button"]["note"]["button"])               # a button only while it can work
        self.assertEqual((report["pressed"], report["went"]), ("Pick a microphone", ["mic_settings"]))

    def test_the_note_stays_clear_of_the_frames_and_the_ask_line(self) -> None:
        report = self.hear()
        for name in ("cant_hear", "talk_deaf", "no_name", "no_button", "with_frames"):
            look = report[name]
            self.assertGreaterEqual(look["bl"][1], look["area_bottom"], name)     # under the frames' area
            self.assertLessEqual(look["bl"][0] + look["bl"][2], look["ask_left"], name)
            box = look["note"]["box"]
            if look["note"]["button"] is not None:
                button = look["note"]["button"]["box"]
                self.assertLessEqual(button[0] + button[2], box[0] + box[2], name)   # inside the note

    def test_a_hostile_name_is_text_and_nothing_of_it_is_an_attribute(self) -> None:
        report = self.hear()
        hostile = report["hostile"]
        self.assertIn("<img src=x onerror=pwned=41>\u202eevil", hostile["note"]["text"])
        self.assertEqual((hostile["foreign"], hostile["pwned"]), (0, "undefined"))
        for name in ("cant_hear", "hostile", "with_frames"):
            for attr in report[name]["attrs"]:
                for mark in ("img", "pwned", "onerror", "Steam", "evil"):
                    self.assertNotIn(mark, attr, name)
            names = {attr.split("=", 1)[0] for attr in report[name]["attrs"]}
            self.assertLessEqual(names, {"class", "type", "role", "aria-label", "aria-hidden", "aria-pressed",
                                         "hidden"}, name)
        self.assertEqual(set(report["page_refused"].values()), {"false"})    # the page's own checks
        self.assertEqual(report["console"], {"console:warning": len(report["page_refused"])})

    def test_the_top_band_the_page_reports_for_windows_hit_test(self) -> None:
        report = self.hear()
        chrome = report["chrome"]
        page = report["chrome_page"]
        self.assertEqual((chrome["w"], chrome["bar"]), (1389, 64))
        self.assertEqual(chrome, page["look"])                                 # what was sent is what is there
        max_box = page["max"]                                                  # left, top, right, bottom
        self.assertLessEqual(chrome["max"][0], max_box[0])
        self.assertGreaterEqual(chrome["max"][0] + chrome["max"][2], max_box[2])
        self.assertEqual(len(chrome["holes"]), len(page["buttons"]) - 1)       # every button but maximise is a hole
        for (left, top, right, bottom), hole in zip([b for b in page["buttons"] if b != max_box], chrome["holes"]):
            self.assertLessEqual(hole[0], left)
            self.assertLessEqual(hole[1], top)
            self.assertGreaterEqual(hole[0] + hole[2], right)
            self.assertGreaterEqual(hole[1] + hole[3], bottom)
        wordmark = page["wordmark"]
        for hole in chrome["holes"]:                                           # the wordmark is the caption: drag
            self.assertTrue(hole[0] >= wordmark[2] or hole[0] + hole[2] <= wordmark[0])
        self.assertEqual((report["resized"]["new"], report["resized"]["w"]), (1, 1280))   # a resize: sent again
        self.assertEqual(report["more_pressed"], "More")
        menu = report["more_open"]["menu"]
        self.assertEqual(report["more_open"]["new"], 1)
        self.assertIn([round(menu[0]) - 2, round(menu[1]) - 2, round(menu[2]) - round(menu[0]) + 4,
                       round(menu[3]) - round(menu[1]) + 4], report["more_open"]["holes"])   # More's list: the page's

    def test_the_maximise_buttons_hover_is_lit_by_the_app(self) -> None:
        report = self.hear()
        self.assertTrue(report["hover_on"]["hot"])
        self.assertEqual(report["hover_on"]["maxBorder"], "rgb(255, 255, 255)")
        self.assertFalse(report["hover_off"]["hot"])

    def test_nothing_asks_the_network_and_nothing_was_refused(self) -> None:
        report = self.hear()
        self.assertEqual(report["blocked"], {})
        self.assertEqual(list(report["allowed"]), ["jarvis-board"])
        self.assertEqual(report["messages_refused"], {})


class CanvasNowRowsTests(BoardProbeCase):
    """The resting group "Now" in the real engine with 3, 4 and 5 frames at 1120x700, the owner's 1389x868 and
    1920x1080 (tests/board_probe.py "canvas_now"): its whole frames in even rows - no row with a lone frame while the
    group shows more than one, the rows' counts within one of each other (the fuller first), a row's frames equally
    wide, equally tall and level, as many across as the width gives room (at least a frame of 340 px), none cut, nothing
    overlapping or over the orb's corner, the group's head over the whole first row. Where those rows cannot hold
    every frame whole but the frames placed one by one at their own heights can (4 frames at 1120x700), that placement
    is used: all whole, nothing cut. Only when neither holds them all whole, the rows and the fold to tiles - never
    lost."""

    def now(self) -> dict:
        report = self.report("canvas_now")
        self.assertTrue(report["ready"])
        self.assertEqual(len(report["sizes"]), 9)
        return report

    def test_the_rows_are_even_at_every_size(self) -> None:
        for key, look in self.now()["sizes"].items():
            with self.subTest(key):
                count = int(key.split("@")[0])
                self.assertTrue(look["settled"])
                self.assertEqual((look["overlaps"], look["outside"], look["on_orb"], look["lost"]), ([], [], [], []))
                rows = look["rows"]
                whole = sum(len(row["ids"]) for row in rows)
                self.assertEqual(whole + look["tiles"], count)              # every frame whole or a tile
                self.assertGreaterEqual(whole, 3)
                if not look["rest"]:                                      # placed one by one: only when all whole
                    self.assertEqual(look["tiles"], 0)
                    self.assertEqual([row["cut"] for row in rows], [[]] * len(rows))
                    continue
                counts = [len(row["ids"]) for row in rows]
                self.assertEqual(counts, sorted(counts, reverse=True))    # the fuller rows first
                self.assertLessEqual(max(counts) - min(counts), 1)       # shared out evenly
                if whole > 1:
                    self.assertNotIn(1, counts)                           # never a lone frame on a row
                for row in rows:
                    self.assertEqual(len(set(row["lines"])), 1, row)      # equally tall ...
                    self.assertEqual(len(set(row["heights"])), 1, row)
                    self.assertEqual(len(set(row["bottoms"])), 1, row)    # ... and level
                    self.assertEqual(len(set(row["cols"])), 1, row)       # equally wide
                    self.assertEqual(row["cut"], [], row)                 # nothing cut
                    self.assertLessEqual(sum(row["cols"]), 12)
                widths = [item for item in look["rows"] for item in item["cols"]]
                self.assertTrue(all(cols * (look["area"][2] + look["grid"]["gap"]) / 12 - look["grid"]["gap"] >= 340
                                    for cols in widths), widths)
                first = rows[0]
                head = look["heads"][0]                                   # the group's head over its first row
                self.assertEqual(len(look["heads"]), 1)
                self.assertAlmostEqual(head[2], round(sum(first["cols"]) * (look["area"][2] + look["grid"]["gap"]) / 12
                                                      - look["grid"]["gap"]), delta=1)

    def test_sensible_columns_for_the_width(self) -> None:
        sizes = self.now()["sizes"]

        def shape(key: str) -> list[int]:
            return [len(row["ids"]) for row in sizes[key]["rows"]]

        self.assertEqual(shape("3@1389x868"), [3])                        # the owner's screen: three across ...
        self.assertEqual(shape("4@1389x868"), [2, 2])                     # ... four as two and two, never 3 + 1
        self.assertEqual(shape("5@1389x868"), [3, 2])
        self.assertEqual(shape("3@1920x1080"), [3])
        self.assertEqual(shape("4@1920x1080"), [4])                       # 1920 wide: four across
        self.assertEqual(shape("5@1920x1080"), [3, 2])                    # five: three and two, never 4 + 1
        for key in ("4@1389x868", "5@1389x868", "4@1920x1080", "5@1920x1080", "3@1389x868", "3@1920x1080",
                    "3@1120x700"):
            self.assertEqual(sizes[key]["tiles"], 0, key)                 # all of them whole where there is room ...
            self.assertTrue(sizes[key]["rest"], key)                      # ... in the even rows
        self.assertEqual(sizes["4@1120x700"]["tiles"], 0)                 # 1120x700, four: the rows would fold one,
        self.assertFalse(sizes["4@1120x700"]["rest"])                     # placing them one by one keeps all whole
        self.assertTrue(sizes["5@1120x700"]["rest"])                      # five: neither holds all whole - the rows,
        self.assertGreater(sizes["5@1120x700"]["tiles"], 0)               # the rest folded to tiles

    def test_the_page_takes_the_group_and_lets_nothing_out(self) -> None:
        report = self.now()
        self.assertEqual(report["messages_refused"], {})                  # its "rest" group message taken
        self.assertEqual(report["blocked"], {})
        self.assertEqual(list(report["allowed"]), ["jarvis-board"])


class Canvas5BTests(BoardProbeCase):
    """Canvas part 5B in the real engine at the owner's 1389x868 (tests/board_probe.py "canvas_5b")."""

    def five(self) -> dict:
        report = self.report("canvas_5b")
        self.assertTrue(report["ready"])
        return report

    @staticmethod
    def overlaps(a, b) -> bool:
        return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]

    def test_no_frame_no_head_and_no_task_card_goes_under_the_side_tile(self) -> None:
        report = self.five()
        self.assertTrue(report["sent_side"])
        side = [28, 64, 300, 180]                                          # the tile: EDGE, BOARD_TOP, its box
        for name in ("side_layout", "side_card"):
            look = report[name]
            for item in look["items"]:
                if item["shown"]:
                    self.assertFalse(self.overlaps(item["box"], side), f"{name} {item['id']} {item['box']}")
            for head in look["heads"]:
                self.assertFalse(self.overlaps(head["box"], side), f"{name} head {head['box']}")
        self.assertFalse(self.overlaps(report["side_card"]["card"]["box"], side))
        firsts = [item["box"] for item in report["no_side_layout"]["items"] if item["shown"]]
        self.assertTrue(any(self.overlaps(box, side) for box in firsts))     # (without it a frame is there)

    def test_a_groups_frames_stay_together_in_their_order(self) -> None:
        report = self.five()
        cells = [(item["cell"][1], item["cell"][0]) for item in report["order"] if item["kind"] == "full"]
        self.assertEqual(cells, sorted(cells))                             # never back into an earlier hole
        groups = [item["group"] for item in report["order"]]
        self.assertEqual(groups, sorted(groups))                           # (g1's, then g2's)

    def test_a_frames_close_is_named_for_its_kind_and_the_windows_for_jarvis(self) -> None:
        report = self.five()
        self.assertEqual(report["names"]["frames"], ["Close Mail frame", "Close Calendar frame", "Close Table frame",
                                                     "Close To-dos frame", "Close Chart frame"])
        window = report["names"]["window"]
        self.assertEqual(window["label"], "Close Jarvis: “Hey Jarvis” stops (minimise keeps him listening)")
        self.assertIsNone(window["title"])                                 # no title attribute (the lockdown) ...
        self.assertEqual(window["tip"], "Closes Jarvis: “Hey Jarvis” stops. Minimise keeps him listening.")
        self.assertEqual(window["tip_hidden"], "true")                     # ... its own tip, drawn by the page
        tip = report["close_tip"]
        self.assertEqual((tip["before"], tip["after"]), ("hidden", "visible"))   # shown on focus (and hover)
        self.assertLessEqual(tip["box"][2], tip["win"])                    # whole on screen

    def test_esc_closes_mores_list(self) -> None:
        report = self.five()
        self.assertEqual(report["more_pressed"], "More")
        self.assertTrue(report["menu_before"]["open"])
        self.assertFalse(report["menu_after"]["open"])

    def test_open_big_is_opaque_and_the_whole_top_band_is_the_pages(self) -> None:
        report = self.five()
        self.assertIn(report["big"]["bg"], ("rgb(3, 10, 13)", "rgb(14, 9, 3)"))   # no alpha: the frames never read through
        self.assertEqual(report["big_chrome"], {"holes": [[0, 0, 1389, 64]], "max": [], "bar": 64, "w": 1389})
        self.assertGreater(report["after_big_chrome"]["holes"], 1)         # back: the buttons again
        self.assertTrue(report["after_big_chrome"]["max"])

    def test_hearing_you_and_the_notes_settings_talk(self) -> None:
        report = self.five()
        self.assertTrue(report["sent_hearing"])
        self.assertEqual(report["hearing"]["hear"]["text"], "Hey JarvisHearing you")
        self.assertIn("s-hearing", report["hearing"]["hear"]["cls"])
        self.assertIn("Pick yours in Settings > Talk.", report["deaf"]["note"])

    def test_a_frame_clicked_looks_otherwise_than_one_at_rest(self) -> None:
        report = self.five()
        self.assertEqual(report["page_focus"], "true")
        focused, rest = report["focus"]["focused"], report["focus"]["rest"]
        self.assertNotEqual(focused["bg"], rest["bg"])
        self.assertEqual(focused["outline"], "solid")
        self.assertEqual(rest["outline"], "none")

    def test_hostile_side_and_hear_are_refused_and_nothing_asks_the_network(self) -> None:
        report = self.five()
        self.assertEqual(set(report["page_refused"].values()), {"false"})
        self.assertEqual(report["blocked"], {})
        self.assertEqual(list(report["allowed"]), ["jarvis-board"])
        self.assertEqual(report["messages_refused"], {})


class CaptureFrameTests(BoardProbeCase):
    """Build plan item 3: the Capture frame in the real canvas page."""

    def test_every_word_is_text_and_each_row_shows_its_state(self) -> None:
        report = self.report("capture")
        self.assertTrue(report["ready"])
        self.assertTrue(report["applied"])
        rendered = report["rendered"]
        self.assertEqual(rendered["markup"], 0)
        self.assertFalse(rendered["attrs"])
        self.assertEqual(rendered["pwned"], "undefined")
        for word in ("Capture \u00b7 fall-term.ics", "Chem lab ‹img src=x onerror=window.pwned=21›",
                     "Essay draft ‹script›window.pwned=22‹/script›", "weekly on Tue and Thu until May 1, 2027",
                     "Jarvis can't add this repeat: it is set in another time zone", "Orientation",
                     "3 wait on Approvals"):
            self.assertIn(word, rendered["text"])
        self.assertEqual([row.split()[1] for row in rendered["rows"]],
                         ["waits", "waits", "waits", "cant_add", "on_calendar", "past"])
        self.assertEqual(rendered["states"], ["Waits", "Waits", "Waits", "Can't add", "On your calendar", "Past"])
        self.assertEqual(rendered["marks"], 4)                       # three diamonds and one check
        self.assertEqual(report["sent"]["waits"], "3 wait on Approvals")

    def test_a_body_that_does_not_fit_is_refused_by_the_page_and_the_protocol(self) -> None:
        report = self.report("capture")
        self.assertEqual(report["page_refuses"], [False] * 8)
        self.assertEqual(report["protocol_refuses"], [True] * 8)
        self.assertEqual(report["bad_frames"], 0)
