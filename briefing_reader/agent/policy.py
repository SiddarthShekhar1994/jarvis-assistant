"""What a task may do and for how long (SPEC D9, D10, R3, R4): no Qt, nothing logged but counts.

    agent_on(config)            [agent] enabled, with [desktop] and [ask] on: anything less is "off" - no endpoint,
                                nothing started, no new word in any prompt or schema
    offered(config, tools, connected, drive_consent=)   the tools this version offers a run: every entry of tools.TOOLS
                                whose tier is on OFFERED_TIERS and, for one that needs something (Tool.needs), only when
                                that is connected and, for the owner's things, allowed by [agent] private. Fail-closed:
                                anything unknown offers nothing. The drive tools (tools.DRIVE_TOOLS, and open_page with
                                its tab and line) only with ``drive_consent`` (the owner's Turn on at consent version 4:
                                desktop_ui, part 3) and [agent] drive (drive_on)
    drive_on(config), drive_caps(config)   [agent] drive as it stands (True only when it is True), and a task's caps
                                ([agent] max_pages, max_actions, max_sends: agent.drive.DriveCaps)
    Budget(max_turns, max_minutes, clock)   one task's turns and minutes of work: its clock rests while the owner is
                                being waited for; reached() says which ran out; extend() is the owner's yes to "Keep
                                going?"
    TaskLog(path, per_day, clock)   how many tasks started in the last 24 hours (agent_tasks.json: times only, never
                                words)
    yes_or_no(words)            the owner's typed answer to "Keep going?"
    gate(tool, args, firewall)  the check before every tool call

The firewall (SPEC section 6) plugs in at ``Firewall``: agent.firewall.WebFirewall guards the web tools and open_thing's
site; agent.owner holds the private and change tools to the rest. With none behind it, a web, private, card or instant
tool is refused outright: never a stand-in.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from ..config import AGENT_PRIVATE
from . import drive as agent_drive
from . import tools as agent_tools

logger = logging.getLogger(__name__)

TASKS_FILE = "agent_tasks.json"
DAY = timedelta(days=1)
# What this version offers. A later part adds its tier here when it adds its tools and its firewall.
OFFERED_TIERS = frozenset({agent_tools.TIER_SHOW, agent_tools.TIER_WAIT, agent_tools.TIER_END,
                           agent_tools.TIER_WORKSPACE, agent_tools.TIER_WEB, agent_tools.TIER_PRIVATE,
                           agent_tools.TIER_CARD, agent_tools.TIER_INSTANT, agent_tools.TIER_DRIVE})
# Tiers that reach outside Jarvis's own show, end and workspace tools: never without a firewall.
GUARDED_TIERS = frozenset({agent_tools.TIER_WEB, agent_tools.TIER_PRIVATE, agent_tools.TIER_CARD,
                           agent_tools.TIER_INSTANT, agent_tools.TIER_DRIVE})
_PERMITTED_AS = {"conversation": "memory"}   # what the owner said before a task goes with [agent] private's "memory"
NO_FIREWALL = "no_firewall"       # a tool result's code
NO_FIREWALL_TEXT = "That tool isn't available in this version of Jarvis; say so in your report."
BUDGET_TURNS, BUDGET_MINUTES = "turns", "minutes"

_YES_RE = re.compile(r"(?:yes|y|yeah|yep|ok|okay|sure|keep going|carry on|go on|go ahead|continue|please do)\b.*",
                     re.IGNORECASE)
_NO_RE = re.compile(r"(?:no|n|nope|stop|stop here|enough|leave it|that's enough|don't)\b.*", re.IGNORECASE)


def agent_on(config: Any) -> bool:
    """[agent] enabled, and the two features it stands on ([desktop], [ask]). Fail-closed: anything odd is off."""
    return (getattr(getattr(config, "agent", None), "enabled", False) is True
            and getattr(getattr(config, "desktop", None), "enabled", False) is True
            and getattr(getattr(config, "ask", None), "enabled", False) is True)


def private_allowed(config: Any) -> frozenset[str]:
    """[agent] private as it stands: only names it knows. Anything else allows nothing."""
    listed = getattr(getattr(config, "agent", None), "private", AGENT_PRIVATE)
    if not isinstance(listed, (tuple, list, frozenset, set)):
        return frozenset()
    return frozenset(name for name in listed if name in AGENT_PRIVATE)


def drive_on(config: Any) -> bool:
    """[agent] drive is True (config.py sets its default; anything else, or none, is off)."""
    return getattr(getattr(config, "agent", None), "drive", False) is True


def _cap(config: Any, name: str, default: int) -> int:
    value = getattr(getattr(config, "agent", None), name, default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def drive_caps(config: Any) -> agent_drive.DriveCaps:
    """One task's pages, page actions and Send cards, from [agent] (the defaults for anything odd)."""
    return agent_drive.DriveCaps(_cap(config, "max_pages", agent_drive.DEFAULT_MAX_PAGES),
                                 _cap(config, "max_actions", agent_drive.DEFAULT_MAX_ACTIONS),
                                 _cap(config, "max_sends", agent_drive.DEFAULT_MAX_SENDS))


def offered(config: Any = None, tools: Sequence[agent_tools.Tool] = agent_tools.TOOLS,
            connected: frozenset[str] | set[str] = frozenset(), *,
            drive_consent: bool = False) -> tuple[agent_tools.Tool, ...]:
    """The tools a run is given, in the registry's order: nothing that needs what is not ``connected``, and none of the
    owner's things that [agent] private leaves out. With ``drive_consent`` True and [agent] drive on, the drive tools
    come after them and open_page is the one with a tab and a line; with anything less, exactly what was offered before
    them."""
    allowed = private_allowed(config)

    def on(tool: agent_tools.Tool) -> bool:
        if tool.tier not in OFFERED_TIERS:
            return False
        if not tool.needs:
            return True
        permit = _PERMITTED_AS.get(tool.needs, tool.needs)
        return tool.needs in connected and (permit not in AGENT_PRIVATE or permit in allowed)

    chosen = [tool for tool in tools if on(tool)]
    if drive_consent is True and drive_on(config) and any(tool.name == "open_page" for tool in chosen):
        chosen = [agent_tools.DRIVE_OPEN_PAGE if tool.name == "open_page" else tool for tool in chosen]
        chosen += [tool for tool in agent_tools.DRIVE_TOOLS if tool.tier in OFFERED_TIERS]
    return tuple(chosen)


class Firewall(Protocol):
    """Asked before every web, private, card and instant call (agent.firewall.WebFirewall)."""

    def check(self, tool: agent_tools.Tool, args: Mapping[str, Any]) -> agent_tools.ToolResult | None:
        """None: the call may go ahead. A result: it does not, and this goes back to the model instead."""


def gate(tool: agent_tools.Tool, args: Mapping[str, Any], firewall: Firewall | None) -> agent_tools.ToolResult | None:
    """Before a tool's handler. None: go ahead. See the module docs on the firewall."""
    if tool.tier not in GUARDED_TIERS:
        return None
    if firewall is None:
        return agent_tools.error(NO_FIREWALL_TEXT, NO_FIREWALL)
    return firewall.check(tool, args)


def yes_or_no(words: str) -> bool | None:
    """The owner's words as an answer to "Keep going?": True, False, or None when they say something else."""
    text = " ".join((words or "").split()).strip(" .!")
    if _NO_RE.fullmatch(text):
        return False
    if _YES_RE.fullmatch(text):
        return True
    return None


class Budget:
    """One task's budget. ``turn(message_id)``: the model's messages, each counted once (a message with two tool calls
    comes as two events); ``max_turns`` of them have all they need, and the one after that reaches the budget. Minutes
    count work only: ``rest()`` while the owner is being waited for or the plan's limit holds, ``work()`` when it goes
    on. Thread-safe."""

    def __init__(self, max_turns: int, max_minutes: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._step_turns = self._turns_left = max(1, int(max_turns))
        self._step_seconds = self._seconds_left = max(1.0, float(max_minutes) * 60.0)
        self._seen: set[Any] = set()
        self._turns = 0
        self._worked = 0.0
        self._since: float | None = clock()

    def turn(self, message_id: Any = None) -> None:
        with self._lock:
            if message_id is not None:
                if message_id in self._seen:
                    return
                self._seen.add(message_id)
            self._turns += 1
            self._turns_left -= 1

    def rest(self) -> None:
        with self._lock:
            self._settle()
            self._since = None

    def work(self) -> None:
        with self._lock:
            if self._since is None:
                self._since = self._clock()

    def slept(self, seconds: float) -> None:
        """The PC slept this long just now: that was no work."""
        with self._lock:
            if self._since is not None:
                self._since += seconds

    def _settle(self) -> None:
        if self._since is not None:
            now = self._clock()
            spent = max(0.0, now - self._since)
            self._worked += spent
            self._seconds_left -= spent
            self._since = now

    def reached(self) -> str:
        """Which ran out (BUDGET_TURNS, BUDGET_MINUTES) or ""."""
        with self._lock:
            self._settle()
            if self._turns_left < 0:
                return BUDGET_TURNS
            return BUDGET_MINUTES if self._seconds_left <= 0 else ""

    def extend(self) -> None:
        """The owner said keep going: as much again, from here."""
        with self._lock:
            self._settle()
            self._turns_left = self._step_turns
            self._seconds_left = self._step_seconds

    @property
    def turns(self) -> int:
        with self._lock:
            return self._turns

    @property
    def worked_s(self) -> float:
        with self._lock:
            self._settle()
            return self._worked


class TaskLog:
    """The tasks started in the last 24 hours, for [agent] tasks_per_day: a JSON list of times in the data folder (no
    goal, no name, no word of the owner's). An unreadable file counts as empty; a failed write keeps this process's
    count in memory. Thread-safe."""

    def __init__(self, path: Path, per_day: int, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self.per_day = max(1, int(per_day))
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._lock = threading.Lock()
        self._local: list[datetime] = []

    def _read(self) -> list[datetime]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        moments = []
        for text in raw.get("started", ()) if isinstance(raw, dict) else ():
            try:
                moment = datetime.fromisoformat(text) if isinstance(text, str) else None
            except ValueError:
                moment = None
            if moment is not None and moment.tzinfo is not None:
                moments.append(moment)
        return moments

    def _recent(self, now: datetime) -> list[datetime]:
        return sorted(moment for moment in (*self._read(), *self._local) if timedelta(0) <= now - moment < DAY)

    def left(self) -> int:
        with self._lock:
            return max(0, self.per_day - len(self._recent(self._clock())))

    def allowed(self) -> bool:
        return self.left() > 0

    def record(self) -> None:
        """A task starts now: it counts."""
        with self._lock:
            now = self._clock()
            moments = [*self._recent(now), now]
            part: str | None = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, part = tempfile.mkstemp(prefix=f"{self.path.name}.", suffix=".tmp", dir=self.path.parent)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps({"started": [moment.isoformat(timespec="seconds") for moment in moments]},
                                            indent=1) + "\n")
                os.replace(part, self.path)
                part = None
                self._local.clear()         # the file holds them now
            except OSError as exc:
                self._local.append(now)
                logger.warning("Agent: the task count could not be saved (%s); kept for this run only",
                               exc.strerror or type(exc).__name__)
            finally:
                if part is not None:
                    try:
                        os.unlink(part)
                    except OSError:
                        pass
