"""The owner's Claude Code for standing runs (no Qt): Ask's own checks, with a standing job's prompt and schema.

    StandingEngine(ask_config, files, *, runner=None, environ=None, locate=cli.locate_claude)
        run(prompt, *, kind, model, timeout_s, read, cancel=None) -> StandingResult
            one locked-down run, exactly as Ask's planner runs: readiness and the claude.ai sign-in checked first
            (ClaudeEngine.preflight: `claude auth status` again before every run), an empty work folder of its own
            (standing-run), no tools, no MCP, the deny-all settings, ``--json-schema`` of the kind's schema, the
            kind's system prompt, nothing saved, at most MAX_TURNS turns, the init and every event checked as Ask's
            are (StructuredOutput only), the answer read strictly by ``read``. Its environment is the private run's
            (no telemetry, no error reports): its stdin holds the owner's mail.
        A "near your limit" warning from Claude Code (a rate_limit_event with status allowed_warning) is noted on the
        result (``warning``, with the reset it names): the runner then holds every standing run until that reset, so
        the rest of the plan stays the owner's. Extra usage, or the plan's own limit, stop the run (stream.run_cli).

Kinds: KIND_RULE (a rule's check), KIND_AGENT (an agent's run), KIND_SETUP (a sentence compiled). Their prompts and
schemas are in standing/assets. Nothing of a prompt or an answer is logged.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..ask import cli, stream
from ..ask.planner import ClaudeEngine
from ..ask.stream import AskFailure, LimitSignal, RunStats
from .store import StandingFiles

logger = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent / "assets"
KIND_RULE, KIND_AGENT, KIND_SETUP = "rule", "agent", "setup"
PROMPTS = {KIND_RULE: ASSETS / "rule_prompt.md", KIND_AGENT: ASSETS / "agent_prompt.md",
           KIND_SETUP: ASSETS / "setup_prompt.md"}
SCHEMAS = {KIND_RULE: ASSETS / "rule_schema.json", KIND_AGENT: ASSETS / "agent_schema.json",
           KIND_SETUP: ASSETS / "setup_schema.json"}
MAX_TURNS = 3                   # the answer, a schema retry and one spare


@dataclass(frozen=True)
class StandingResult:
    answer: Any = None
    failure: AskFailure | None = None
    stats: RunStats = RunStats()
    limit: LimitSignal | None = None
    warning: bool = False                  # Claude Code said the plan is near its limit
    warning_resets: datetime | None = None
    started: bool = True

    @property
    def outcome(self) -> str:
        return "ok" if self.failure is None else self.failure.kind


def schema_text(kind: str) -> str:
    return cli.compact_json(json.loads(SCHEMAS[kind].read_text(encoding="utf-8")))


class StandingEngine:

    def __init__(self, ask_config: Any, files: StandingFiles, *, runner: cli.Runner | None = None,
                 environ: Mapping[str, str] | None = None,
                 locate: Callable[[Mapping[str, str]], cli.ClaudeExe | None] = cli.locate_claude) -> None:
        self.claude = ClaudeEngine(ask_config, files.data_dir, runner=runner, environ=environ, locate=locate)
        self.claude.work_folder = files.work_folder        # its own empty folder, never Ask's
        self._environ = environ

    def readiness(self) -> Any:
        return self.claude.readiness()

    def run(self, prompt: str, *, kind: str, model: str, timeout_s: float,
            read: Callable[[Mapping[str, Any], Any], Any], cancel: threading.Event | None = None) -> StandingResult:
        if kind not in PROMPTS:
            raise ValueError("not a standing run kind")
        ready, refused = self.claude.preflight()
        if refused is not None:
            return StandingResult(failure=refused, started=False)
        assert ready.exe is not None
        argv = cli.build_argv(ready.exe.path, model=model, max_turns=MAX_TURNS, schema=schema_text(kind),
                              hardened=ready.hardened, prompt_file=PROMPTS[kind], settings_file=cli.SETTINGS_FILE,
                              streaming=False)
        env = {**cli.clean_env(self._environ), **cli.PRIVATE_RUN_ENV}
        warned: list[datetime | None] = []

        def event_check(event: Mapping[str, Any]) -> None:
            if event.get("type") == "rate_limit_event":
                info = event.get("rate_limit_info")
                if isinstance(info, dict) and info.get("status") == "allowed_warning":
                    signal_time = info.get("resetsAt")
                    warned.append(stream._epoch_time(signal_time, lambda: datetime.now().astimezone()))
            stream.check_event(event)

        result = stream.run_cli(self.claude.runner, argv, prompt, env=env, cwd=self.claude.work_folder,
                                timeout_s=float(timeout_s), read=read, event_check=event_check, cancel=cancel)
        logger.info("Standing: a %s run %s in %.1f s (%d turn(s), %d in / %d out tokens, exit %s)", kind,
                    result.outcome, result.stats.duration_ms / 1000, result.stats.turns, result.stats.input_tokens,
                    result.stats.output_tokens, result.stats.exit_code)
        if result.failure is not None and result.failure.kind != stream.CANCELLED:
            self.claude.forget()
        return StandingResult(answer=result.answer, failure=result.failure, stats=result.stats, limit=result.limit,
                              warning=bool(warned), warning_resets=next((at for at in warned if at), None))


def structured(event: Mapping[str, Any], init: Any) -> Mapping[str, Any] | AskFailure:
    """The result event's structured output, or why there is none (stream.result_failure, then the shape)."""
    found = stream.result_failure(event, init)
    if found is not None:
        return found
    data = event.get("structured_output")
    if not isinstance(data, dict):
        return stream.failure(stream.GARBLED, "no_structured_output")
    return data
