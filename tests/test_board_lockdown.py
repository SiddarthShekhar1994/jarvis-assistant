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
memory. Skipped when this PySide6 has no QtWebEngine 6.8+.
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
CASES = ("lockdown", "control", "media", "keys", "fallback", "shell", "kinds", "desk", "desk_x15")
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
        report = self.report("media")
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
        self.assertTrue(page["desk_in"])
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
