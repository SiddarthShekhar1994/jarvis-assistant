"""fetch_text: a page read another way, by Claude Code's own declared fetcher (part A3; no Qt).

Bot protection keeps automated browsers out of many sites, and Jarvis never disguises its window or tries to get past a
refusal. Claude Code's WebFetch, the fetcher web research uses, says who it is and is let in by some of them. So:

    Fetcher(engine, model, cap=)        one task's reads: at most ``cap`` of them (its own cap, not Ask's rate caps)
        fetch(url, question, call)      a separate short run of Claude Code with WebFetch as its only tool, research's
                                        settings file and an empty folder; it sees only the address and the question, on
                                        stdin. The answer's text (at most TEXT_CAP characters), or web.WebRefused

The firewall looks at the call first, exactly as at open_page (agent.firewall). The run is held to that one address: any
other tool or address kills it. It runs inside the tool's time (web.Call) and is killed the moment the call is cancelled
(a Stop, or past the 45 s). While the test guard is on, the real runner is refused before anything runs.

Logs: ids, kinds, counts and durations only; never the address, the question or a word of the answer.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from typing import Any

from ..actions import web_link_problem
from ..ask import cli
from ..ask.research import normalize_url
from ..ask.stream import GuardTrip, parse_event, rate_limit_signal
from ..desktop.backend import real_backend_blocked
from . import web

logger = logging.getLogger(__name__)

FETCH_CAP = 15                    # reads per task
TEXT_CAP = web.TEXT_PART          # characters of an answer given back
MAX_AT_ONCE = 3                   # reads at the same time (each is a process of about 190 MB)
MAX_FETCHES = 2                   # WebFetch calls in one run: the one it was asked for, and one try again
POLL_S = 0.1
FAILED_MARK = "FAILED:"           # how the run says it could not read the page (fetch_prompt.md)
SERVER_FETCH_NAME = "web_fetch"   # the fetcher's name when it runs as a server tool (ask.research knows it too)
QUESTION_TAG, ADDRESS_TAG = "question", "address"

W_FETCH_CAP, W_FETCH_FAILED, W_FETCH_GUARD = "fetch_cap", "fetch_failed", "fetch_guard"
CAP_TEXT = ("That is the most pages read another way for one task ({cap}). Use what you have, try the window, or say "
            "so in your report.")
FAILED_TEXT = "That way could not read this page either. Try another source, or say so in your report."
NOT_AVAILABLE_TEXT = "Reading a page another way isn't available now. Try another source, or say so in your report."
GUARD_TEXT = ("That read was stopped: it went beyond the one address it was given. Try another source, or say so in "
              "your report.")
LIMIT_TEXT = "Claude Code's plan limit stopped that read. Try the window, or say so in your report."


def check_fetch_init(event: Mapping[str, Any]) -> None:
    """A fetch run's init event as Jarvis requires it, else GuardTrip: no API key, no MCP server, no skill, WebFetch and
    nothing else, dontAsk."""
    if event.get("apiKeySource") != "none":
        raise GuardTrip("api_key_source")
    if event.get("mcp_servers") != []:
        raise GuardTrip("mcp_servers")
    if event.get("tools") != [cli.WEB_FETCH_TOOL]:
        raise GuardTrip("tools")
    if event.get("skills", []) != []:
        raise GuardTrip("skills")
    if event.get("permissionMode", "dontAsk") != "dontAsk":
        raise GuardTrip("permission_mode")


def _text_of(event: Mapping[str, Any]) -> str:
    text = event.get("result")
    return text if isinstance(text, str) else ""


class Fetcher:
    """See the module docs. Thread-safe: tool calls may come side by side."""

    def __init__(self, engine: Any, model: str = "sonnet", *, cap: int = FETCH_CAP) -> None:
        self.engine = engine
        self.model = model
        self.cap = int(cap)
        self._lock = threading.Lock()
        self._used = 0
        self._slots = threading.Semaphore(MAX_AT_ONCE)
        self.folder = engine.fetch_folder

    def __repr__(self) -> str:        # counts only
        return f"Fetcher({self._used} of {self.cap})"

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def fetch(self, url: str, question: str, call: web.Call) -> str:
        """The answer's text, or web.WebRefused with Jarvis's own words."""
        problem = web_link_problem(url)
        if problem:
            raise web.WebRefused(web.W_NOT_PUBLIC, web.NOT_PUBLIC_TEXT.format(why=problem))
        with self._lock:
            if self._used >= self.cap:
                raise web.WebRefused(W_FETCH_CAP, CAP_TEXT.format(cap=self.cap))
            self._used += 1
        while not self._slots.acquire(timeout=0.02):
            call.check()
        try:
            call.check()
            return self._run(url, question, call)
        finally:
            self._slots.release()

    def _run(self, url: str, question: str, call: web.Call) -> str:
        began = time.monotonic()
        runner = self.engine.runner
        if isinstance(runner, cli.SubprocessRunner) and real_backend_blocked():
            logger.info("Agent: a read another way was refused (the test guard is on)")
            raise web.WebRefused(W_FETCH_FAILED, NOT_AVAILABLE_TEXT)
        ready = self.engine.base.readiness()
        if not ready.ok or ready.exe is None or ready.research_missing:
            raise web.WebRefused(W_FETCH_FAILED, NOT_AVAILABLE_TEXT)
        if cli.prepare_work_folder(self.folder, what="Reading a page another way", folder=cli.AGENT_FETCH_WORK_FOLDER):
            raise web.WebRefused(W_FETCH_FAILED, NOT_AVAILABLE_TEXT)
        argv = cli.build_fetch_argv(ready.exe.path, model=self.model, hardened=ready.hardened)
        prompt = (f"<{ADDRESS_TAG}>{url}</{ADDRESS_TAG}>\n"
                  f"<{QUESTION_TAG}>{' '.join(question.split())}</{QUESTION_TAG}>")
        try:
            process = runner.start(argv, stdin_text=prompt, env=cli.clean_env(self.engine.base._env_source()),
                                   cwd=self.folder)
        except OSError as exc:
            logger.warning("Agent: a read another way could not be started (%s)", type(exc).__name__)
            raise web.WebRefused(W_FETCH_FAILED, NOT_AVAILABLE_TEXT) from None
        try:
            text = self._read(process, url, call)
        finally:
            for step in (process.kill, lambda: process.wait(2.0), process.close):
                try:
                    step()
                except OSError:
                    pass
        logger.info("Agent: a page was read another way in %.1f s, %d characters", time.monotonic() - began, len(text))
        return text

    def _read(self, process: Any, url: str, call: web.Call) -> str:
        """The stream, until its result: the guard on every event; a Stop or the time up kills it (web.Call.check)."""
        wanted = normalize_url(url)
        fetches = 0
        inited = False
        while True:
            call.check()
            try:
                line = process.read_line(POLL_S)
            except cli.OutputTooLong:
                raise web.WebRefused(W_FETCH_FAILED, FAILED_TEXT) from None
            if line is None:
                logger.info("Agent: a read another way ended with no answer")
                raise web.WebRefused(W_FETCH_FAILED, FAILED_TEXT)
            event = parse_event(line) if line else None
            if event is None:
                continue
            signal = rate_limit_signal(event)
            if signal is not None:
                logger.info("Agent: a read another way was stopped by the plan's limit (overage %s)", signal.overage)
                raise web.WebRefused(W_FETCH_FAILED, LIMIT_TEXT)
            kind = event.get("type")
            try:
                if kind == "system" and event.get("subtype") == "init":
                    check_fetch_init(event)
                    inited = True
                elif kind == "assistant":
                    if not inited:
                        raise GuardTrip("no_init")
                    fetches = self._check_calls(event, wanted, fetches)
                elif kind == "result":
                    return self._answer(event, inited)
            except GuardTrip as trip:
                logger.warning("Agent: a read another way was stopped by the guard (%s)", trip.reason)
                raise web.WebRefused(W_FETCH_GUARD, GUARD_TEXT) from None

    @staticmethod
    def _check_calls(event: Mapping[str, Any], wanted: str, fetches: int) -> int:
        """Its tool calls: WebFetch of that one address and nothing else."""
        message = event.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        for block in content if isinstance(content, list) else ():
            kind = block.get("type") if isinstance(block, Mapping) else ""
            if kind not in ("tool_use", "server_tool_use", "mcp_tool_use"):
                continue
            if block.get("name") not in (cli.WEB_FETCH_TOOL, SERVER_FETCH_NAME):
                raise GuardTrip("tool_use")
            arguments = block.get("input")
            address = arguments.get("url") if isinstance(arguments, Mapping) else None
            if normalize_url(address) != wanted or not wanted:
                raise GuardTrip("address")
            fetches += 1
            if fetches > MAX_FETCHES:
                raise GuardTrip("fetches")
        return fetches

    @staticmethod
    def _answer(event: Mapping[str, Any], inited: bool) -> str:
        if not inited:
            raise GuardTrip("no_init")
        text = web.clean_text(_text_of(event))
        if event.get("subtype") != "success" or event.get("is_error") is True or not text \
                or text.startswith(FAILED_MARK):
            logger.info("Agent: a read another way gave no answer (%s)",
                        "marked" if text.startswith(FAILED_MARK) else "failed")
            raise web.WebRefused(W_FETCH_FAILED, FAILED_TEXT)
        return text[:TEXT_CAP]


__all__ = ["FETCH_CAP", "Fetcher", "TEXT_CAP", "W_FETCH_CAP", "W_FETCH_FAILED", "W_FETCH_GUARD", "check_fetch_init"]
