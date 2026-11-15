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
lively and off; and every probe's page plays the probe's own motion level whatever this PC's "Show animations" says
(Chromium told no reduced motion), with one probe told reduced motion showing the page's own rule still holds. Part
6, Ask's typed frames: a model's answer with markup, scripts, javascript: and http: addresses and bidi characters in
every string, through board.ask_frames and board.protocol into the real page - only elements its renderers make,
no attribute from data in a frame's body, no request, no script run, its words whole as text; the page refusing what
Python refuses (Infinity, depth 7, 65 nodes, an unknown op ...); maths' elements made from the tree and the page's
reading of plain expressions the same as Python's; no word cut at 448, 300 and 270 px (the timeline in a row from a
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
         "big_x15", "expiry", "reduced", "typed")
# Round 5: what each probe's engine is told of reduced motion (tests/board_probe.py NO_REDUCED_MOTION, REDUCED_MOTION).
NO_REDUCED_MOTION = "--force-prefers-no-reduced-motion"
REDUCED_MOTION = "--force-prefers-reduced-motion"
# Round 5: the media probe's engine lists Chromium's stand-in devices (tests/board_probe.py FAKE_MEDIA_DEVICES), written
# out here so a dropped or misspelled switch fails the test whatever devices this PC has plugged in.
FAKE_MEDIA_DEVICES = "--use-fake-device-for-media-stream"
REPORTS: dict[str, dict] = {}
FAILURES: dict[str, str] = {}
WINDOW_KEYS = ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6", "Ctrl+7", "Ctrl+8", "Ctrl+9", "Ctrl+,",
               "Ctrl+K", "Ctrl+Space", "Ctrl+M", "Ctrl+B"]
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
                                            "failed": ["rgb(23, 15, 18)", "Try again"],  # TILE_ALERT
                                            "empty": "Checked 9:44 AM"})
        focused = report["focused"]
        self.assertEqual((focused["keys"], focused["meta"], focused["controls"]), ("flex", "none", "flex"))
        self.assertIn("ENTER OPEN BIG · P PIN", focused["footer"])
        self.assertIn("DEL CLOSE · ESC ASK BAR", focused["footer"])
        self.assertIn("closed", report["closed"]["classes"])
        self.assertEqual(report["closed"]["row"].split("\n"), ["Closed: Frame m1", "Bring back"])
        self.assertNotIn("closed", report["brought_back"])
        # Each control asked the app, by its own typed signal.
        self.assertEqual(report["seen"], [["ready"], ["approvalsRequested", "w1"], ["retryRequested", "x1"],
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
        # frame) and remove (not its last) then had nothing to give back, and the board's keys were dead.
        report = self.report("expiry")
        self.assertTrue(report["ready"])
        for motion in ("lively", "off"):
            for route in ("group_last", "not_last"):
                with self.subTest(motion=motion, route=route):
                    row = report["expiry"][motion][route]
                    frame = row["frame"]
                    self.assertTrue(row["closed"])
                    self.assertEqual(row["after_delete"], f"bring_back:{frame}")
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
