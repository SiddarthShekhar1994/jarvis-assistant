"""Jarvis, a true agent, part A2: the agent's own browser, the window the owner watches.

Most of it runs the real web engine: each case in a process of its own (tests/agent_browser_probe.py), so QtWebEngine
never starts inside the shared unittest process. LOCAL PAGES ONLY: offscreen the browser refuses every network address
and blocks every network request, and Chromium is pointed at a proxy that is not there; the gate cases run the
production settings through the network gate with a fake resolver and connector, so the one server anything can reach is
a listener of the probe. The rest (the window's flags and place, the scheme's setup) needs no engine. Skipped when this
PySide6 has no QtWebEngine 6.8+.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget

from briefing_reader import hud, hud_overlay, snapshot_engine, snapshots
from briefing_reader.agent import browser as agent_browser
from briefing_reader.agent import tools, web
from briefing_reader.agent.browser import FINISHED_TAG, READING_TAG, WatchWindow, watch_rect
from tests.ui_fakes import fonts, settle

PROBE = Path(__file__).with_name("agent_browser_probe.py")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASES = ("search", "read", "picture", "crash", "timeout", "stop", "quit", "hooks", "focus", "linger", "file", "gate",
         "gate_control", "task", "refused", "feed_window", "owner_show", "warm_up", "feed_task")
MARK = "PROBE-WORDS-7f3a"
PAGE = "https://www.example.org/page"
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
    _app = QApplication.instance() or QApplication(["test_agent_browser", "-platform", "offscreen"])
    fonts()
    if not snapshot_engine.webengine_available():
        return
    with ThreadPoolExecutor(max_workers=4) as pool:
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


def inside(result: dict) -> str:
    """The inside of a result's untrusted block."""
    lines = result["text"].split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("<untrusted "))
    end = next(i for i, line in enumerate(lines) if line.startswith("</untrusted "))
    return "\n".join(lines[start + 1:end])


@unittest.skipUnless(snapshot_engine.webengine_available(), "this PySide6 has no QtWebEngine 6.8+")
class ProbeCase(unittest.TestCase):
    def report(self, case: str) -> dict:
        if case in FAILURES:
            self.fail(f"probe {case} failed: {FAILURES[case]}")
        self.assertIn(case, REPORTS)
        report = REPORTS[case]
        self.assertEqual(report["platform"], "offscreen")
        self.assertTrue(report["registered"])
        self.assertEqual(report["children_end"], 0, "no renderer is left")
        return report


class SearchProbeTests(ProbeCase):
    def results(self, name: str) -> dict:
        return self.report("search")["results"][name]

    def test_duckduckgo_s_real_markup(self) -> None:
        result = self.results("duckduckgo")
        self.assertEqual(result["asked"], ["duckduckgo.com"])
        self.assertEqual(inside(result).split("\n"), [
            "1. DDG first", "   https://www.example.org/ddg-one", "   The first snippet.",
            "2. DDG second", "   https://www.example.net/ddg-two"])       # not the local one, not the link outside
        self.assertEqual(result["note"], "2 result(s) from DuckDuckGo")
        self.assertTrue(result["live"])
        self.assertEqual(result["pictures"], 0)

    def test_bing_s_real_markup_with_its_redirect_links(self) -> None:
        result = self.results("bing")
        self.assertEqual(inside(result).split("\n"), [
            "1. Bing first", "   https://www.bing.com/ck/a?!&&p=abc&u=a1aHR0cHM6Ly93d3c", "   Bing snippet."])  # no ad

    def test_google_s_real_markup(self) -> None:
        result = self.results("google")
        self.assertEqual(inside(result).split("\n"), [
            "1. Google first", "   https://www.example.org/g-one", "   Google snippet."])

    def test_results_a_page_draws_by_script_are_waited_for(self) -> None:
        self.assertIn("1. Drawn late", inside(self.results("late")))

    def test_the_chain_no_results_then_a_captcha_then_the_third(self) -> None:
        result = self.results("chain")
        self.assertEqual(result["asked"], ["duckduckgo.com", "www.bing.com", "www.google.com"])
        self.assertIn("Google first", result["text"])
        self.assertFalse(result["is_error"])

    def test_all_walls_says_so_and_touched_nothing(self) -> None:
        result = self.results("walls")
        self.assertTrue(result["is_error"])
        self.assertEqual(result["code"], web.W_NO_RESULTS)
        for words in ("DuckDuckGo asked for a captcha", "Bing showed a consent wall", "Google refused the visit"):
            self.assertIn(words, result["text"])
        self.assertLess(result["took"], 25)                            # a wall is left at once, none waited on
        self.assertEqual(result["asked"], ["duckduckgo.com", "www.bing.com", "www.google.com"])   # no link on them


class ReadProbeTests(ProbeCase):
    def test_title_address_text_and_links_as_the_page_really_is(self) -> None:
        report = self.report("read")
        opened = report["opened"]
        body = inside(opened)
        self.assertIn("Title: The real title", body)
        self.assertIn(f"Address: {PAGE}", body)
        self.assertIn(f"{MARK} on the page", body)
        self.assertNotIn("HIDDEN-WORDS", body)                         # what the owner can't see is not read either
        self.assertEqual(report["numbers"], ["https://www.example.org/first", "https://www.example.org/second?x=1#y",
                                             "https://www.example.org/labelled", "", "", "", ""])
        self.assertIn("1. First link - https://www.example.org/first", body)
        self.assertIn("3. Labelled link - https://www.example.org/labelled", body)
        for hidden in ("/hidden", "localhost", "http://www", "/relative", "/icon"):
            self.assertNotIn(hidden, body)
        self.assertTrue(opened["live"])
        self.assertEqual(opened["pictures"], 0)
        self.assertEqual(report["number_one_not_a_page"], web.W_TEST_RUN)  # offscreen: no page it has no HTML for

    def test_the_page_s_scripts_can_neither_change_nor_see_the_reading(self) -> None:
        report = self.report("read")
        self.assertNotIn("HACKED", report["opened"]["text"])
        self.assertIn(MARK, report["opened"]["text"])
        # JSON.stringify, getOwnPropertyDescriptor, querySelectorAll, getClientRects, slice, push, innerText, title,
        # links and body were all replaced in the page's own world, each counting its use: none was walked into.
        self.assertEqual(report["calls_seen_by_the_page"], 0)

    def test_read_more_gives_the_next_part_and_the_window_really_scrolls(self) -> None:
        report = self.report("read")
        first, more = inside(report["long_first"]), inside(report["long_more"])
        self.assertIn("Line number 0 of", first)
        self.assertIn("characters 1 to", first)
        self.assertNotIn("Line number 0 of", more)
        self.assertLessEqual(len(first), web.TEXT_PART + 100)
        self.assertLessEqual(len(more), web.TEXT_PART + 100)
        self.assertIn("call read_more", report["long_first"]["text"])
        self.assertEqual(report["top_before"], 0)
        self.assertGreater(report["top_after_one"], 5000)
        self.assertGreater(report["top_at_the_end"], report["top_after_one"])
        self.assertGreaterEqual(report["top_at_the_end"], (report["height"] - report["inner"]) * 0.8)
        self.assertGreaterEqual(report["parts"], 6)
        self.assertEqual(report["after_the_end"], "That was all of this page's text.")

    def test_the_address_it_ended_on(self) -> None:
        report = self.report("read")
        self.assertIn("Address: https://www.example.net/ended-here", inside(report["moved"]))
        self.assertTrue(report["final_seen"])


class PictureProbeTests(ProbeCase):
    def test_look_at_page_is_at_most_1280_wide_and_300_kb(self) -> None:
        report = self.report("picture")
        self.assertEqual(report["opened"], "")
        self.assertGreater(report["view_width"], 1280)                 # the window is wider than the cap ...
        self.assertEqual(report["wide"][:2], [True, 1280])             # ... so the picture is scaled to it
        self.assertGreater(report["wide"][3], 300_000)                 # ... and this page does not fit 300 KB at 1280
        self.assertTrue(report["jpeg"])
        self.assertEqual(report["kind"], "image/jpeg")
        self.assertLessEqual(report["bytes"], 300_000)
        width = int(report["note"].split(" x ")[0])
        self.assertLess(width, 1280)                                   # it was made smaller until it fit
        self.assertGreaterEqual(width, agent_browser.MIN_PICTURE_WIDTH)
        self.assertTrue(report["live"])

    def test_one_that_can_never_fit_says_so(self) -> None:
        report = self.report("picture")
        self.assertEqual(report["small"][0], True)
        self.assertLessEqual(report["small"][2], 60_000)
        self.assertEqual(report["none"], [False, "too_large"])
        self.assertEqual(report["look_too_big"], web.W_TOO_BIG)


class CrashProbeTests(ProbeCase):
    def test_a_crash_with_a_page_open_ends_in_plain_words(self) -> None:
        report = self.report("crash")
        self.assertFalse(report["page_after_idle_crash"])
        code, is_error, text, took = report["read_more"]
        self.assertEqual((code, is_error), (web.W_NO_PAGE, True))
        self.assertIn("No page is open", text)
        self.assertLess(took, 3)
        self.assertEqual(report["look"][0], web.W_NO_PAGE)

    def test_a_crash_during_a_load_ends_in_plain_words_not_a_hang(self) -> None:
        report = self.report("crash")
        code, text, took = report["during_load"]
        self.assertEqual(code, web.W_CRASHED)
        self.assertIn("crashed", text)
        self.assertLess(took, 5)

    def test_the_next_page_opens(self) -> None:
        self.assertEqual(self.report("crash")["next_page_opens"], [False, True])


class TimeoutProbeTests(ProbeCase):
    def test_a_hung_page_is_given_up_on_in_time(self) -> None:
        report = self.report("timeout")
        ok, _kind, still_loading, took = report["load"]
        self.assertLess(took, 6)                                       # the 2 s limit, not a hang
        if ok:
            self.assertTrue(still_loading)
        code, is_error, text, took = report["tool"]
        self.assertTrue(is_error)
        self.assertIn(code, (web.W_TOO_SLOW, web.W_NO_ANSWER))
        self.assertLess(took, 9)                                       # the tool's own time
        self.assertNotIn(PAGE, text)

    def test_a_page_on_its_way_to_another_is_read_when_it_has_arrived(self) -> None:
        ok, still_loading, took = self.report("timeout")["waited_for_the_redirect"]
        self.assertTrue(ok)
        self.assertFalse(still_loading)
        self.assertGreaterEqual(took, 1.7)

    def test_it_leaves_no_renderer(self) -> None:
        self.assertEqual(self.report("timeout")["children_after"], 0)


class StopProbeTests(ProbeCase):
    def test_a_stop_during_a_load_releases_the_waiter_at_once(self) -> None:
        report = self.report("stop")
        self.assertTrue(report["ready"])
        self.assertTrue(report["still_waiting"])
        self.assertLess(report["released_in"], 0.5)
        self.assertEqual(report["result"], [web.W_STOPPED, web.STOPPED_TEXT])

    def test_the_page_and_the_window_go_at_once(self) -> None:
        report = self.report("stop")
        self.assertFalse(report["page_right_after"])
        self.assertFalse(report["window_right_after"])
        self.assertEqual(report["children_after"], 0)

    def test_nothing_after_it(self) -> None:
        self.assertEqual(self.report("stop")["after_the_stop"], "cancelled")

    def test_a_call_given_up_on_stops_its_page_and_the_run_goes_on(self) -> None:
        code, took, page, window = self.report("stop")["cancelled"]
        self.assertEqual(code, web.W_STOPPED)
        self.assertLess(took, 0.5)
        self.assertFalse(page)
        self.assertTrue(window)


class QuitProbeTests(ProbeCase):
    def test_quitting_mid_load_with_no_event_loop_leaves_nothing(self) -> None:
        report = self.report("quit")
        self.assertGreaterEqual(report["before"]["children"], 1)
        self.assertEqual(report["before"]["windows"], ["JarvisWatch"])
        self.assertEqual(report["windows_right_after"], [])
        self.assertFalse(report["browser_page"])
        self.assertEqual(report["views"], 0)
        self.assertEqual(report["children_after"], 0)
        self.assertFalse(report["profile_warning"])

    def test_quitting_with_a_page_the_agent_wrote_open_lets_the_gate_go(self) -> None:
        report = self.report("quit")
        self.assertTrue(report["held_with_a_file_open"])
        self.assertFalse(report["held_after_quit"])


class HooksProbeTests(ProbeCase):
    def test_popups_dialogs_permissions_and_choosers_are_refused(self) -> None:
        report = self.report("hooks")
        self.assertEqual(report["opened"], "")
        said = dict(line.split("=", 1) for line in report["text"].splitlines())
        self.assertEqual(said["script"], "ran")
        self.assertEqual(said["open"], "null")
        self.assertEqual(said["confirm"], "false")
        self.assertEqual(said["prompt"], "null")
        self.assertEqual(said["notify"], "denied")
        self.assertEqual(said["geo"], "1")                             # PERMISSION_DENIED
        self.assertEqual(said["clip"], "refused")
        self.assertGreaterEqual(report["hooks"].get("permissions", 0), 2)
        self.assertEqual(report["new_top_levels"], ["WatchWindow"])    # the one window: nothing opened beside it

    def test_downloads_never_start(self) -> None:
        report = self.report("hooks")
        self.assertEqual(report["hooks"].get("downloads", 0), 0)
        self.assertTrue(report["downloads_watched"])
        self.assertEqual(report["downloads_new_entries"], [])
        self.assertFalse(report["downloads_mtime_changed"])
        self.assertEqual(report["download_folder_entries"], [])

    def test_a_form_can_t_post(self) -> None:
        code, text, posted, _hooks = self.report("hooks")["form"]
        self.assertEqual((code, text, posted), ("", "post tried", 0))

    def test_a_page_closing_itself_closes_nothing(self) -> None:
        code, visible, picture = self.report("hooks")["closing"]
        self.assertEqual(code, "")
        self.assertTrue(visible)
        self.assertTrue(picture)


class FocusProbeTests(ProbeCase):
    def test_the_hud_s_flags_on_the_real_window(self) -> None:
        report = self.report("focus")
        self.assertEqual(report["window_type"], "Tool")
        for name in ("frameless", "on_top", "no_focus_flag", "native_no_focus", "show_without_activating", "q_show",
                     "parent", "visible"):
            self.assertIs(report[name], True, name)

    def test_a_page_that_keeps_asking_for_the_focus_never_gets_it(self) -> None:
        report = self.report("focus")
        self.assertEqual(report["asked"], [])                          # never an activation, a raise or a focus of its own
        self.assertFalse(report["focus_in_window"])
        self.assertEqual(report["page_has_focus_inside"], "edit")      # inside its own document it got what it asked ...
        self.assertEqual(report["inner"], ["NoFocus"])                 # ... and every widget of the window stays NoFocus
        self.assertEqual((report["window_policy"], report["view_policy"]), ("NoFocus", "NoFocus"))
        self.assertIs(report["view_enabled"], False)
        self.assertEqual(report["inner_enabled"], [False])

    def test_mouse_and_keyboard_are_off(self) -> None:
        report = self.report("focus")
        self.assertIs(report["mouse"], True)
        self.assertEqual(report["input_reaches_the_page"], {})
        self.assertFalse(report["focus_after_input_in_window"])
        self.assertEqual(report["control_input_reaches_the_page"].get("mousedown"), "yes")   # the test can tell


class LingerProbeTests(ProbeCase):
    def test_the_strip_says_what_it_reads_and_the_last_pages(self) -> None:
        report = self.report("linger")
        self.assertEqual(report["strip"], [READING_TAG, "www.example.net", ["www.example.org"]])
        self.assertEqual(report["cut"], [])

    def test_when_the_run_ends_the_page_goes_at_once_and_the_window_a_little_later(self) -> None:
        report = self.report("linger")
        after = report["after_end"]
        self.assertFalse(after["page"])
        self.assertTrue(after["visible"])
        self.assertTrue(after["covered"])                              # a still of it
        self.assertEqual(after["strip"][0], FINISHED_TAG)
        self.assertEqual(report["children_soon"], 0)                   # no renderer while it lingers
        self.assertTrue(report["still_there"])
        self.assertTrue(report["gone"])
        self.assertEqual(report["top_levels"], [])

    def test_a_new_run_ends_what_the_last_one_left_and_a_stop_leaves_nothing(self) -> None:
        report = self.report("linger")
        self.assertEqual(report["lingering"], ["JarvisWatch"])
        self.assertEqual(report["after_close_now"], [])
        self.assertEqual(report["after_a_stop"], [])

    def test_the_gate_lets_go_when_a_page_the_agent_wrote_is_gone_though_the_window_lingers(self) -> None:
        report = self.report("linger")
        self.assertTrue(report["held_with_a_file_open"])
        self.assertTrue(report["gate_free_while_lingering"])
        self.assertTrue(report["lingering_with_its_still"])

    def test_a_stop_after_the_end_takes_a_lingering_window_with_it(self) -> None:
        self.assertEqual(self.report("linger")["stop_after_the_end"], [])


class FileProbeTests(ProbeCase):
    def test_a_page_of_the_task_with_its_own_pictures_and_inline_script(self) -> None:
        report = self.report("file")
        self.assertEqual(report["shown"]["code"], "")
        self.assertIn(MARK, report["shown"]["text"])
        self.assertIn("Title: My report", report["shown"]["text"])
        self.assertIn("script-ran", report["read_after"])
        self.assertIn("own:LOADED", report["read_after"])
        self.assertIn("deep:LOADED", report["read_after"])
        self.assertEqual(report["own_width"], 40)
        self.assertEqual(report["strip"][1], "Task folder \u00b7 report.html")

    def test_nothing_else_loads_from_it(self) -> None:
        text = self.report("file")["read_after"]
        for name in ("remote", "parent", "other", "file", "fetch"):
            self.assertIn(f"{name}:refused", text)
        self.assertNotIn("remote:LOADED", text)

    def test_a_text_and_what_can_t_run(self) -> None:
        report = self.report("file")
        self.assertIn(f"Plain text {MARK}\nsecond line", report["plain"]["text"])
        self.assertIn('<script>window.__ran = 1</script>', report["json"]["text"])   # shown as the text it is
        self.assertIs(report["json_script_ran"], False)
        self.assertIn("xml words", report["xml"]["text"])
        self.assertIn("<?xml-stylesheet", report["xml"]["text"])
        self.assertEqual(report["svg"]["code"], "")
        self.assertEqual(report["look"], ["", 1])

    def test_a_name_with_spaces_accents_and_marks_is_served_as_itself(self) -> None:
        odd = self.report("file")["odd_names"]
        self.assertEqual(len(odd), 4)
        for name, text in odd.items():
            with self.subTest(name=name):
                self.assertIn(f"The words of {name}", text)

    def test_names_that_are_not_this_tasks_are_refused(self) -> None:
        refused = self.report("file")["refused"]
        self.assertEqual(set(refused.values()), {"parent", "absolute", "not_found"})
        self.assertEqual(sorted(refused.values()), ["absolute", "absolute", "not_found", "parent", "parent"])

    def test_addresses_that_are_not_this_tasks_serve_nothing(self) -> None:
        report = self.report("file")
        self.assertEqual(len(report["direct"]), 6)
        for address, text in report["direct"].items():
            with self.subTest(address=address):
                self.assertNotIn("-OTHER", text)
                self.assertNotIn("Secret", text)
        self.assertTrue(report["own_again"])

    def test_a_web_page_can_t_reach_the_scheme(self) -> None:
        report = self.report("file")
        code, width, sealed, _blocked = report["web_page"]
        self.assertEqual((code, width, sealed), ("", 0, False))
        self.assertEqual(report["web_scheme_refused"], web.W_NOT_PUBLIC)

    def test_its_address_has_a_token_of_its_own(self) -> None:
        self.assertEqual(len(self.report("file")["token"]), 16)

    def test_if_such_a_page_never_went_the_next_web_page_fails_closed(self) -> None:
        ok, kind, took, held = self.report("file")["stuck_gate"]
        self.assertEqual((ok, kind, held), (False, "load_failed", True))
        self.assertLess(took, 8)


class GateProbeTests(ProbeCase):
    """Production network settings; the gate's resolver and connector are fakes; a listener of the probe stands for
    every server. The control shows what such a page reaches when the gate is not sealed."""

    def test_the_control_a_page_the_agent_wrote_reaches_the_gate_if_it_is_not_sealed(self) -> None:
        report = self.report("gate_control")
        self.assertIn("turn.leak.example.org", report["resolved"])     # the name carries data ...
        self.assertIn("preconnect.leak.example.org", report["resolved"])
        self.assertGreater(report["gate"]["tunnels"], 0)
        self.assertTrue(any(hit == "0003" for hit in report["hits"]), report["hits"])   # ... a TURN request got through

    def test_sealed_nothing_at_all_gets_out(self) -> None:
        report = self.report("gate")
        self.assertEqual(report["shown"], "")
        self.assertIn("script-ran", report["text"])
        self.assertIn("fetch:refused", report["text"])
        self.assertTrue(report["sealed_while_open"])
        self.assertEqual(report["resolved"], [])                       # no name looked up, in any way
        self.assertEqual(report["hosts"], [])                          # the gate did not even read one
        self.assertEqual(report["hits"], [])                           # nothing reached the "server"
        self.assertEqual(report["gate"]["tunnels"], 0)
        self.assertGreaterEqual(report["gate"]["refused"], 1)

    def test_the_interceptor_refuses_what_it_sees(self) -> None:
        for case in ("gate", "gate_control"):
            report = self.report(case)
            self.assertGreaterEqual(report["blocked"], 2)              # the frame, the page's own navigation
            self.assertTrue(report["https_refused"])

    def test_past_that_page_the_gate_works_again(self) -> None:
        report = self.report("gate")
        self.assertFalse(report["sealed_after"])
        self.assertIn("after.example.org", report["after_resolved"])
        self.assertTrue(report["after_hits"])

    def test_production_flags(self) -> None:
        flags = self.report("gate")["flags"]
        self.assertTrue(any(flag.startswith("--proxy-server=127.0.0.1:") for flag in flags))
        self.assertNotIn("--proxy-server=127.0.0.1:9", flags)
        self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", flags)
        self.assertIn(snapshots.RESOLVER_RULES, flags)


class WholeTaskProbeTests(ProbeCase):
    """The real controller, the real browser, the real endpoint; only Claude Code is a fake."""

    def test_a_whole_web_task(self) -> None:
        report = self.report("task")
        self.assertTrue(report["started"])
        self.assertTrue(report["ended"])
        self.assertEqual(report["failure"], "")
        self.assertEqual(report["end"][:4], ["done", "finished", "Read two pages and wrote the report.", ["report.html"]])
        texts = report["texts"]
        self.assertIn("1. DDG first", texts["search"])
        self.assertIn("<untrusted", texts["search"])
        self.assertIn(MARK, texts["open"])
        self.assertIn("3. More here - https://www.example.org/more", texts["open"])
        self.assertIn("Line number 0 of", texts["link"])
        self.assertIn("call read_more", texts["link"])
        self.assertNotIn("Line number 0 of", texts["more"])
        self.assertIn(f"{MARK} report", texts["show"])
        self.assertEqual(report["look_image"], [None, "image/jpeg"])

    def test_live_has_its_steps_with_the_window_s_pictures(self) -> None:
        steps = self.report("task")["steps"]
        by_title = {step["title"]: step for step in steps}
        for title in ("Searched the web", "Opened a page", "Read on", "Looked at the page", "Showed a file"):
            with self.subTest(title=title):
                self.assertEqual(by_title[title]["status"], "ok")
                self.assertEqual((by_title[title]["picture"], by_title[title]["state"]), ("page", "ready"))
                self.assertGreater(by_title[title]["bytes"], 1000)
        self.assertEqual(by_title["Searched the web"]["fields"]["Searched for"], "quiet invented laptops")
        self.assertEqual(by_title["Showed a file"]["fields"]["File"], "report.html")
        self.assertEqual(by_title["Showed a file"]["caption"], "File: report.html")
        self.assertEqual(by_title["Opened a page"]["caption"], "Page: www.example.org")

    def test_the_window_goes_with_the_task(self) -> None:
        self.assertEqual(self.report("task")["windows_after"], [])


class WatchRectTests(unittest.TestCase):
    AREA = QRect(0, 0, 1920, 1040)

    def test_the_right_part_of_the_main_screen(self) -> None:
        rect = watch_rect(self.AREA, None, False)
        self.assertEqual(rect.right(), self.AREA.right() - agent_browser.MARGIN)
        self.assertEqual(rect.width(), 960)
        self.assertGreater(rect.left(), self.AREA.width() // 3)
        self.assertEqual(rect.top(), agent_browser.MARGIN)
        self.assertEqual(rect.bottom(), self.AREA.bottom() - agent_browser.MARGIN)

    def test_clear_of_the_hud(self) -> None:
        hud_rect = QRect(580, 16, 760, 120)
        rect = watch_rect(self.AREA, hud_rect, False)
        self.assertFalse(rect.intersects(hud_rect))
        self.assertEqual(rect.top(), hud_rect.bottom() + 1 + agent_browser.GAP)
        beside = QRect(10, 16, 300, 120)                               # not in its way: it stays at the top
        self.assertEqual(watch_rect(self.AREA, beside, False).top(), agent_browser.MARGIN)

    def test_a_screen_of_its_own(self) -> None:
        rect = watch_rect(QRect(1920, 0, 1280, 720), None, True)
        self.assertTrue(QRect(1920, 0, 1280, 720).contains(rect))
        self.assertEqual(rect.width(), 1280 - 2 * agent_browser.MARGIN)
        wide = watch_rect(QRect(0, 0, 3840, 2160), None, True)
        self.assertEqual((wide.width(), wide.height()), (agent_browser.MAX_WIDE, agent_browser.MAX_TALL))

    def test_always_inside_however_small_the_screen(self) -> None:
        for area in (QRect(0, 0, 800, 600), QRect(-1280, 100, 1280, 700), QRect(0, 0, 500, 300), QRect(100, 50, 640, 400)):
            for avoid in (None, QRect(area.x(), area.y() + 16, area.width(), 400)):
                for whole in (True, False):
                    with self.subTest(area=area, avoid=avoid, whole=whole):
                        rect = watch_rect(area, avoid, whole)
                        self.assertTrue(area.contains(rect), rect)
                        self.assertGreater(rect.width(), 0)
                        self.assertGreater(rect.height(), 0)


class WatchWindowTests(unittest.TestCase):
    """No engine here: the window as a widget."""

    def window(self, **options: object) -> WatchWindow:
        window = WatchWindow(native=hud_overlay.NoNative(), **options)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        return window

    def test_the_hud_s_flags(self) -> None:
        window = self.window()
        window.setGeometry(100, 100, 800, 500)
        window.show_quietly()
        settle()
        flags = window.windowFlags()
        self.assertEqual(window.windowType(), Qt.WindowType.Tool)
        for flag in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowStaysOnTopHint,
                     Qt.WindowType.WindowDoesNotAcceptFocus):
            self.assertTrue(flags & flag, flag)
        self.assertEqual(flags, hud_overlay.HUD_FLAGS | Qt.WindowType.Window | flags & ~(hud_overlay.HUD_FLAGS
                                                                                         | Qt.WindowType.Window))
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertEqual(window.focusPolicy(), Qt.FocusPolicy.NoFocus)
        for child in window.findChildren(QWidget):
            self.assertEqual(child.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertIsNone(window.parent())
        handle = window.windowHandle()
        self.assertTrue(handle.flags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertIs(handle.property("_q_showWithoutActivating"), True)

    def test_left_out_of_capture_with_the_hud_s_setting(self) -> None:
        class Native(hud_overlay.NoNative):
            asked: list[int] = []

            def exclude_from_capture(self, hwnd: int) -> bool:
                self.asked.append(hwnd)
                return True

        native = Native()
        window = WatchWindow(native=native, hide_from_capture=True)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        window.show_quietly()
        settle()
        self.assertEqual(len(native.asked), 1)
        self.assertTrue(window.excluded_from_capture())

    def test_the_strip_and_the_last_few_pages(self) -> None:
        window = self.window()
        self.assertEqual(window.strip_texts(), ("Jarvis is reading", "", ()))
        for host in ("one.example.org", "two.example.org", "two.example.org", "three.example.org", "four.example.org",
                     "five.example.org"):
            window.set_page(host)
        self.assertEqual(window.strip_texts()[1:], ("five.example.org",
                                                    ("four.example.org", "three.example.org", "two.example.org")))
        window.set_page("three.example.org")                           # back to one it has: not listed twice
        self.assertEqual(window.strip_texts()[1:], ("three.example.org",
                                                    ("five.example.org", "four.example.org", "two.example.org")))
        window.update_host("moved.example.org")                        # a redirect: no new entry
        self.assertEqual(window.strip_texts()[1], "moved.example.org")
        self.assertEqual(len(window.strip_texts()[2]), 3)
        self.assertEqual(window.cut_texts(), [])

    def test_it_paints_the_panel_and_no_word_is_lost(self) -> None:
        window = self.window()
        window.resize(640, 400)
        window.set_page("www.example.org")
        window.set_page("x" * 200 + ".example.org")                    # cut with an ellipsis, in the strip only
        image = window.grab().toImage()
        self.assertGreater(image.pixelColor(320, 200).alpha(), 200)    # the panel: opaque, not see-through
        self.assertEqual(window.cut_texts(), [])

    def test_a_cover_shows_and_goes(self) -> None:
        from PySide6.QtGui import QColor, QPixmap

        window = self.window()
        window.resize(640, 400)
        picture = QPixmap(100, 100)
        picture.fill(QColor("#e63946"))
        window.cover(picture)
        self.assertTrue(window.covered())
        window.cover(None)
        self.assertFalse(window.covered())
        window.cover(QPixmap())
        self.assertFalse(window.covered())


class BrowserHelperTests(unittest.TestCase):
    """Without the engine."""

    def test_nothing_of_the_web_engine_is_imported_with_the_module(self) -> None:
        code = ("import sys; import briefing_reader.agent.browser; "
                "print(sorted(m for m in sys.modules if m.startswith(('PySide6.QtWebEngine', 'PySide6.QtQuick'))))")
        done = subprocess.run([sys.executable, "-B", "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True,
                              timeout=120, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(done.stdout.strip(), "[]")

    def test_the_scheme_only_before_the_application(self) -> None:
        self.assertFalse(agent_browser.register_scheme())              # a QApplication exists in this process
        code = ("from PySide6.QtWidgets import QApplication\n"
                "from briefing_reader.agent import browser\n"
                "print(browser.scheme_ready(), browser.register_scheme(), browser.register_scheme(), browser.scheme_ready())\n"
                "app = QApplication(['x', '-platform', 'offscreen'])\n"
                "print(browser.scheme_ready(), browser.register_scheme())\n"
                "from PySide6.QtWebEngineCore import QWebEngineUrlScheme as S\n"
                "scheme = S.schemeByName(b'jarvis-task')\n"
                "print(int(scheme.flags().value) == int((S.Flag.SecureScheme | S.Flag.LocalScheme"
                " | S.Flag.LocalAccessAllowed).value), scheme.syntax().name)\n")
        done = subprocess.run([sys.executable, "-B", "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True,
                              timeout=120, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(done.stdout.split(), ["False", "True", "True", "True", "True", "True", "True", "Path"])

    def test_tasks_off_nothing_of_it_is_imported_before_the_application(self) -> None:
        code = ("import sys\n"
                "from types import SimpleNamespace as N\n"
                "from briefing_reader import __main__ as entry\n"
                "from briefing_reader.config import AgentConfig\n"
                "entry._prepare_agent_window(N())\n"
                "entry._prepare_agent_window(N(agent=AgentConfig()))\n"
                "print('off', sorted(m for m in sys.modules if m.startswith('PySide6.QtWebEngine')"
                " or m == 'briefing_reader.agent.browser'))\n"
                "entry._prepare_agent_window(N(agent=AgentConfig(enabled=True), desktop=N(enabled=False), ask=N(enabled=True)))\n"
                "print('half', any(m.startswith('PySide6.QtWebEngine') for m in sys.modules))\n"
                "entry._prepare_agent_window(N(agent=AgentConfig(enabled=True), desktop=N(enabled=True), ask=N(enabled=True)))\n"
                "from briefing_reader.agent import browser\n"
                "print('on', browser.scheme_ready())\n")
        done = subprocess.run([sys.executable, "-B", "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True,
                              timeout=120, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(done.stdout.splitlines(), ["off []", "half False", "on True"])

    def test_tools_see_the_window_s_limits(self) -> None:
        self.assertEqual(web.LOAD_TIMEOUT_S, 25.0)
        self.assertEqual((web.PICTURE_WIDTH, web.PICTURE_BYTES), (1280, 300_000))
        self.assertEqual((web.TEXT_PART, web.MAX_LINKS, web.QUERY_CAP), (12_000, 60, 300))
        self.assertEqual(agent_browser.LINGER_S, 10.0)
        by_name = {tool.name: tool for tool in tools.TOOLS}
        self.assertEqual(by_name["web_search"].schema["properties"]["query"]["maxLength"], 300)
        self.assertEqual({by_name[name].tier for name in ("web_search", "open_page", "read_more", "look_at_page")},
                         {tools.TIER_WEB})
        self.assertEqual(by_name["show_file"].tier, tools.TIER_WORKSPACE)

    def test_fonts_are_there_for_the_strip(self) -> None:
        self.assertTrue(hud.mono_font(10, 500, 0.12).family())


class RefusedTests(ProbeCase):
    """A real HTTP status through the real engine, from a little server on loopback standing where the gate would."""

    def outcomes(self) -> dict:
        return self.report("refused")["outcomes"]

    def test_401_403_and_429_are_a_refusal_told_apart_with_words_in_the_window(self) -> None:
        outcomes = self.outcomes()
        for path, status in (("403", 403), ("403empty", 403), ("401", 401), ("429", 429)):
            outcome = outcomes[path]
            self.assertFalse(outcome["ok"], path)
            self.assertEqual((outcome["kind"], outcome["status"]), ("site_refused", status), path)
            self.assertEqual(outcome["notice"], ["www.example.org keeps automated browsers out", agent_browser.REFUSED_NOTE], path)
            self.assertFalse(outcome["uniform"], path)               # words on its dark page, not emptiness

    def test_another_failure_says_something_else(self) -> None:
        outcome = self.outcomes()["404"]
        self.assertEqual((outcome["ok"], outcome["kind"], outcome["status"]), (False, "load_failed", 0))
        self.assertEqual(outcome["notice"], ["www.example.org did not load", agent_browser.NOT_LOADED_NOTE])

    def test_a_page_that_comes_leaves_no_words(self) -> None:
        outcome = self.outcomes()["200"]
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["notice"], ["", ""])

    def test_nothing_is_tried_again_and_no_identity_but_the_engine_s(self) -> None:
        report = self.report("refused")
        self.assertEqual(report["hits"], ["403", "403empty", "401", "429", "404", "200", "403"])    # each asked for once
        self.assertEqual(len(report["agents"]), 1)                   # one user agent for every request ...
        self.assertEqual(report["agents"][0], report["profile_agent_default"])    # ... the engine's own
        self.assertIn("QtWebEngine", report["agents"][0])            # it says what it is


class FeedWindowTests(ProbeCase):
    def test_the_user_agent_is_never_set(self) -> None:
        report = self.report("feed_window")
        self.assertEqual(report["agent"], report["agent_default"])
        self.assertIn("QtWebEngine", report["agent"])

    def test_no_disguise_anywhere_in_the_source(self) -> None:
        for name in ("agent/browser.py", "agent/web.py", "agent/fetch.py", "agent/tools.py", "snapshot_engine.py", "agent_ui.py",
                     "agent_feed.py"):
            source = (PROJECT_ROOT / "briefing_reader" / name).read_text(encoding="utf-8")
            for word in ("setHttpUserAgent", "setHttpHeader", "setHttpAcceptLanguage", "--user-agent", "User-Agent"):
                self.assertNotIn(word, source, name)

    def test_hide_and_show(self) -> None:
        report = self.report("feed_window")
        self.assertTrue(report["shown_first"])
        self.assertTrue(report["hide_in_strip"]["policy"])
        self.assertTrue(report["hide_in_strip"]["inside"])
        self.assertEqual(report["hide_in_strip"]["cut"], [])
        self.assertEqual(report["hidden"], {"visible": False, "asked": [True], "owner": True})
        self.assertFalse(report["second"]["is_error"])               # the task carries on behind it
        self.assertIn("Words", report["second"]["text"])
        self.assertFalse(report["second_visible"])                   # a new page does not bring it back
        self.assertTrue(report["back_visible"])

    def test_a_picture_while_hidden_is_a_picture_or_says_it_is_hidden(self) -> None:
        look = self.report("feed_window")["look_hidden"]
        self.assertTrue(look["pictures"] == 1 or look["code"] == web.W_HIDDEN, look)

    def test_away_while_the_jarvis_window_is_back(self) -> None:
        report = self.report("feed_window")
        self.assertFalse(report["away_visible"])
        self.assertEqual(report["third_code"], "")
        self.assertFalse(report["third_visible"])
        self.assertTrue(report["after_away_visible"])

    def test_while_jarvis_reads_the_wheel_moves_nothing(self) -> None:
        self.assertEqual(self.report("feed_window")["scroll_while_reading"], 0)

    def test_the_strip_says_a_page_is_read_another_way(self) -> None:
        report = self.report("feed_window")
        self.assertEqual(report["other_way_tag"], agent_browser.OTHER_WAY_TAG)
        self.assertEqual(report["after_other_way_tag"], READING_TAG)

    def test_the_focus_stays_where_it_was(self) -> None:
        report = self.report("feed_window")
        self.assertTrue(report["focus_kept"])
        self.assertFalse(report["took_focus"])


class OwnerShowTests(ProbeCase):
    def test_the_window_stays_while_the_card_stays(self) -> None:
        held = self.report("owner_show")["held"]
        self.assertEqual(held, {"visible": True, "tag": FINISHED_TAG, "page": False})

    def test_show_opens_it_sealed_in_the_window_and_the_wheel_scrolls_it(self) -> None:
        report = self.report("owner_show")
        self.assertTrue(report["delivered"])
        shown = report["shown"]
        self.assertTrue(shown["ok"])
        self.assertTrue(shown["visible"])
        self.assertTrue(shown["sealed"])                             # the network gate refuses everything meanwhile
        self.assertEqual(shown["scheme"], f"{web.SCHEME}:")          # Jarvis's own local scheme
        self.assertEqual(shown["title"], "Report")
        self.assertEqual(shown["strip"][0], agent_browser.SHOWING_TAG)
        self.assertTrue(report["scrolled"])

    def test_nothing_is_left_when_the_card_goes(self) -> None:
        report = self.report("owner_show")
        self.assertTrue(report["window_gone"])
        self.assertFalse(report["sealed_after"])


class FeedOverTheRealBrowserTests(ProbeCase):
    """The panel, Hide and Show, the result card, Show and Folder over the real controller, browser, endpoint and HUD windows."""

    def test_through_a_whole_task(self) -> None:
        report = self.report("feed_task")
        self.assertEqual(report["failure"], "")
        self.assertTrue(report["started"] and report["first_page"] and report["second_page"] and report["ended"])
        first = report["after_first"]
        self.assertTrue(first["panel"] and first["window"] and first["clear"] and first["show_hidden"])
        self.assertEqual(first["activity"], "Reading www.example.org")
        self.assertRegex(first["head"], r"^WORKING - 1 OF 2 - \d+:\d\d$")
        self.assertEqual(report["after_hide"], {"window": False, "show_hidden": False, "buttons": True, "running": True})
        self.assertFalse(report["second_window"])                    # the task carried on behind it
        self.assertEqual(report["second_activity"], "Reading www.example.org")
        self.assertEqual(report["after_show"], {"window": True, "show_hidden": True})
        result = report["result"]
        self.assertRegex(result["kicker"], r"^DONE - 2 STEPS - \d+:\d\d$")
        self.assertEqual(result["body"], "The answer is yes. It took two pages.")
        self.assertEqual(result["rows"], ["report.html"])
        self.assertTrue(result["window"])                            # the window stays while the card stays
        self.assertEqual(result["tag"], FINISHED_TAG)
        self.assertFalse(result["page"])                             # its page went
        self.assertTrue(report["shown"])
        self.assertTrue(report["sealed"])
        self.assertEqual(report["revealed"], ["report.html"])
        self.assertEqual(report["after_done"], {"card": True, "panel": False, "active": False, "shown": []})
        self.assertFalse(report["sealed_after"])


class WarmUpTests(ProbeCase):
    def test_it_starts_chromium_and_nothing_else_once(self) -> None:
        report = self.report("warm_up")
        self.assertEqual(report["no_warm_up_here"], 0.0)             # never by itself on a test platform
        self.assertGreater(report["first"], 0.0)
        self.assertTrue(report["started"])
        self.assertEqual(report["again"], 0.0)
        self.assertEqual(report["windows_after"], [])                # no page, no renderer, no window
        self.assertEqual(report["renderers_after"], 0)

    def test_the_first_web_step_then_blocks_the_gui_far_less(self) -> None:
        report = self.report("warm_up")
        self.assertEqual(report["opened"], "")
        self.assertLess(report["first_step_gap"], report["warm_gap"])


if __name__ == "__main__":
    unittest.main()
