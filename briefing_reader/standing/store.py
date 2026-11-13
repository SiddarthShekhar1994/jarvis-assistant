"""The standing agents' and rules' files in the data folder (no Qt). One writer per file; every file versioned.

    StandingFiles(data_dir)      the paths, all inside the data folder (%LOCALAPPDATA%\\briefing-reader)
    read_json(path) / write_json(path, data)
                                 versioned JSON: written atomically (temporary file, fsync, atomic.replace, which
                                 rides over another process reading it); read back as (data, problem): a missing
                                 file is (None, ""), a damaged, too large or newer one (None, "damaged" | "newer")
    Definitions(files)           standing.json, the agents and rules: written by the APP only, on the owner's clicks.
                                 load() keeps every definition that checks (the rest are counted and logged); a
                                 damaged or newer file is kept READ-ONLY, so a save can never wipe it
    AppState(files)              standing_app.json, written by the APP only: the consent, Pause all, withdrawn cards,
                                 undone receipts, cleared and announced runs
    RunnerState(files)           standing_state.json, written by the RUNNER only (one runner at a time: its mutex):
                                 per job its last run and last due key; per (job, account) the mail it saw and its
                                 high-water mark; what it proposed; the holds; the last check (the heartbeat)
    RunFiles(files)              standing_runs\\<stamp>-<id>.json: one per run, written once by the runner; listed and
                                 read by the app; pruned after keep_days by the runner; Clear deletes at once
    RunRequests(files)           standing_requests\\<job id>.json: Run now (the app writes one, the runner takes it)
    Usage(files)                 standing_usage.json: Claude runs, rule checks and tidy-ups by local date, read and
                                 written under a lock file shared by every process (ask.usage's), so the caps hold
                                 across the app and the runner; try_run() counts a run only when the caps allow it

What these files hold (the owner's sentences, a report, a card's draft, a mail's sender and subject) is never
logged: the log gets file kinds, counts and problem codes only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .. import atomic
from ..ask.usage import FileLock
from .model import (SCHEMA_VERSION, Agent, DefinitionError, Rule, agent_from, rule_from, to_data)

logger = logging.getLogger(__name__)

DEFINITIONS_FILE = "standing.json"
APP_FILE = "standing_app.json"
STATE_FILE = "standing_state.json"
USAGE_FILE = "standing_usage.json"
RUNS_FOLDER = "standing_runs"
REQUESTS_FOLDER = "standing_requests"
TIDY_JOURNAL = "standing_tidy.jsonl"
WORK_FOLDER = "standing-run"          # where a standing run's Claude Code starts: empty, like Ask's
MAX_FILE_BYTES = 4_000_000
MAX_RUN_FILE_BYTES = 2_000_000
MAX_RUN_FILES = 2000
MAX_SEEN = 500                        # mail ids remembered per (job, account)
MAX_PROPOSED = 1000                   # proposals remembered per job
MAX_MARKS = 2000                      # withdrawn / undone / announced entries kept in the app's file
USAGE_DAYS_KEPT = 8
RUN_ID_RE = re.compile(r"[0-9a-f]{12}")
_RUN_NAME_RE = re.compile(r"(\d{8}-\d{6})-([0-9a-f]{12})\.json")
_JOB_ID_RE = re.compile(r"[ar]-[0-9a-f]{12}")
DAMAGED, NEWER, TOO_LARGE = "damaged", "newer", "too_large"


def _now() -> datetime:
    return datetime.now().astimezone()


def stamp(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat(timespec="seconds")


def parse_stamp(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.astimezone()


def new_run_id() -> str:
    return secrets.token_hex(6)


@dataclass(frozen=True)
class StandingFiles:
    data_dir: Path

    @property
    def definitions(self) -> Path:
        return Path(self.data_dir) / DEFINITIONS_FILE

    @property
    def app(self) -> Path:
        return Path(self.data_dir) / APP_FILE

    @property
    def state(self) -> Path:
        return Path(self.data_dir) / STATE_FILE

    @property
    def usage(self) -> Path:
        return Path(self.data_dir) / USAGE_FILE

    @property
    def usage_lock(self) -> Path:
        return Path(self.data_dir) / (USAGE_FILE + ".lock")

    @property
    def runs(self) -> Path:
        return Path(self.data_dir) / RUNS_FOLDER

    @property
    def requests(self) -> Path:
        return Path(self.data_dir) / REQUESTS_FOLDER

    @property
    def tidy_journal(self) -> Path:
        return Path(self.data_dir) / TIDY_JOURNAL

    @property
    def work_folder(self) -> Path:
        return Path(self.data_dir) / WORK_FOLDER


# --------------------------------------------------------------------------
# Versioned JSON
# --------------------------------------------------------------------------

def read_json(path: Path, *, limit: int = MAX_FILE_BYTES) -> tuple[dict[str, Any] | None, str]:
    """(data, "") for a readable file of this or an older version; (None, "") when there is none; (None, problem)
    for a damaged, too large or newer one. Never raises; nothing of the content is logged."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit + 1)
    except FileNotFoundError:
        return None, ""
    except OSError:
        return None, DAMAGED
    if len(raw) > limit:
        return None, TOO_LARGE
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, DAMAGED
    if not isinstance(data, dict):
        return None, DAMAGED
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return None, DAMAGED
    if version > SCHEMA_VERSION:
        return None, NEWER
    return data, ""


def write_json(path: Path, data: Mapping[str, Any], *, what: str = "a standing file") -> bool:
    """Replace ``path`` with ``data`` (``version`` added) atomically; False (logged by ``what`` and the error's type
    only) when it could not be written."""
    payload = {"version": SCHEMA_VERSION, **{key: value for key, value in data.items() if key != "version"}}
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    part: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, part = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        atomic.replace(part, path)
        part = None
        return True
    except OSError as exc:
        logger.warning("Standing: could not save %s (%s)", what, type(exc).__name__)
        return False
    finally:
        if part is not None:
            try:
                os.unlink(part)
            except OSError:
                pass


def _bounded(mapping: Mapping[str, Any], limit: int) -> dict[str, Any]:
    """The last ``limit`` entries of an insertion-ordered mapping."""
    items = list(mapping.items())
    return dict(items[-limit:]) if len(items) > limit else dict(items)


# --------------------------------------------------------------------------
# Definitions (the app writes)
# --------------------------------------------------------------------------

class Definitions:
    """standing.json: {"version", "agents": [...], "rules": [...]}. Written only by the app."""

    def __init__(self, files: StandingFiles, *, min_every: int = 30) -> None:
        self.files = files
        self.min_every = min_every
        self.read_only = False
        self.problem = ""
        self.skipped = 0
        self._lock = threading.Lock()

    def load(self) -> tuple[list[Agent], list[Rule]]:
        """Every agent and rule that checks (model.agent_from / rule_from); the others are counted in ``skipped``.
        A damaged or newer file makes this object read-only (``problem`` says why)."""
        with self._lock:
            data, problem = read_json(self.files.definitions)
            self.problem, self.read_only, self.skipped = problem, bool(problem), 0
            if problem:
                logger.warning("Standing: standing.json is %s; the agents and rules are kept read-only", problem)
                return [], []
            if data is None:
                return [], []
            agents: list[Agent] = []
            rules: list[Rule] = []
            for item in data.get("agents") or ():
                try:
                    agents.append(agent_from(item, min_every=self.min_every))
                except DefinitionError:
                    self.skipped += 1
            for item in data.get("rules") or ():
                try:
                    rules.append(rule_from(item))
                except DefinitionError:
                    self.skipped += 1
            if self.skipped:
                logger.warning("Standing: %d definition(s) in standing.json did not check and are left out",
                               self.skipped)
            return agents, rules

    def save(self, agents: Iterable[Agent], rules: Iterable[Rule]) -> bool:
        with self._lock:
            if self.read_only:
                logger.warning("Standing: standing.json is %s; nothing was saved", self.problem or "read-only")
                return False
            return write_json(self.files.definitions,
                              {"agents": [to_data(agent) for agent in agents],
                               "rules": [to_data(rule) for rule in rules]}, what="standing.json")

    def mtime(self) -> float:
        try:
            return self.files.definitions.stat().st_mtime
        except OSError:
            return 0.0


# --------------------------------------------------------------------------
# The app's own state (the app writes)
# --------------------------------------------------------------------------

CONSENT_VERSION = 1


class AppState:
    """standing_app.json: {"consent": {"version", "at"} | null, "paused_all", "withdrawn": {card id: at},
    "undone": {receipt id: at}, "cleared_before": at, "announced": {run id: at}}. Written only by the app; the
    runner reads it (consent, Pause all, withdrawn cards, undone receipts)."""

    def __init__(self, files: StandingFiles) -> None:
        self.files = files
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {}
        self.problem = ""
        self.reload()

    def reload(self) -> None:
        with self._lock:
            data, problem = read_json(self.files.app)
            self.problem = problem
            self.data = dict(data or {})
            for key in ("withdrawn", "undone", "announced"):
                if not isinstance(self.data.get(key), dict):
                    self.data[key] = {}

    def consented(self) -> bool:
        consent = self.data.get("consent")
        return isinstance(consent, dict) and consent.get("version") == CONSENT_VERSION

    @property
    def paused_all(self) -> bool:
        return self.data.get("paused_all") is True

    def is_withdrawn(self, card_id: str) -> bool:
        return card_id in self.data["withdrawn"]

    def is_undone(self, receipt_id: str) -> bool:
        return receipt_id in self.data["undone"]

    def is_announced(self, run_id: str) -> bool:
        return run_id in self.data["announced"]

    def cleared_before(self) -> datetime | None:
        return parse_stamp(self.data.get("cleared_before"))

    # writes (the app only) -------------------------------------------------

    def _save(self) -> bool:
        if self.problem in (DAMAGED, NEWER, TOO_LARGE):
            logger.warning("Standing: standing_app.json is %s; nothing was saved", self.problem)
            return False
        for key in ("withdrawn", "undone", "announced"):
            self.data[key] = _bounded(self.data.get(key) or {}, MAX_MARKS)
        return write_json(self.files.app, self.data, what="standing_app.json")

    def set_consent(self, on: bool, now: datetime | None = None) -> bool:
        with self._lock:
            self.data["consent"] = {"version": CONSENT_VERSION, "at": stamp(now)} if on else None
            return self._save()

    def set_paused_all(self, paused: bool) -> bool:
        with self._lock:
            self.data["paused_all"] = bool(paused)
            return self._save()

    def mark(self, key: str, ids: Iterable[str], now: datetime | None = None) -> bool:
        """Mark ``ids`` in "withdrawn", "undone" or "announced"."""
        if key not in ("withdrawn", "undone", "announced"):
            raise ValueError(key)
        with self._lock:
            at = stamp(now)
            for item in ids:
                if isinstance(item, str) and item:
                    self.data[key][item] = at
            return self._save()

    def clear_runs(self, now: datetime | None = None) -> bool:
        with self._lock:
            self.data["cleared_before"] = stamp(now)
            return self._save()


# --------------------------------------------------------------------------
# The runner's own state (the runner writes)
# --------------------------------------------------------------------------

class RunnerState:
    """standing_state.json, written only by the runner (under its mutex):

        "jobs":     {job id: {"last_run": at, "due_key": "YYYY-MM-DD HH:MM", "missed": [keys]}}
        "mail":     {"<job id>|<alias>": {"mark_ms": int, "seen": [gmail ids], "checked": at}}
        "proposed": {job id: {key: "YYYY-MM-DD"}}     what it proposed (a card's stable key) and when
        "holds":    {"until": at, "why": code}        no Claude run before ``until`` (a plan limit, a warning)
        "check":    {"at": at, "origin": "task" | "app", "status": code}   the last check (the heartbeat)
        "announce": [run ids]                          runs that need the owner and were not yet told
    """

    def __init__(self, files: StandingFiles) -> None:
        self.files = files
        data, problem = read_json(files.state)
        if problem:
            logger.warning("Standing: standing_state.json is %s; starting it again", problem)
        self.data: dict[str, Any] = dict(data or {})
        for key in ("jobs", "mail", "proposed", "holds", "check"):
            if not isinstance(self.data.get(key), dict):
                self.data[key] = {}
        if not isinstance(self.data.get("announce"), list):
            self.data["announce"] = []

    def save(self) -> bool:
        self.data["mail"] = {key: {**value, "seen": list(value.get("seen") or [])[-MAX_SEEN:]}
                             for key, value in self.data["mail"].items() if isinstance(value, dict)}
        self.data["proposed"] = {job: _bounded(value, MAX_PROPOSED)
                                 for job, value in self.data["proposed"].items() if isinstance(value, dict)}
        self.data["announce"] = [item for item in self.data["announce"]
                                 if isinstance(item, str) and RUN_ID_RE.fullmatch(item)][-200:]
        return write_json(self.files.state, self.data, what="standing_state.json")

    def job(self, job_id: str) -> dict[str, Any]:
        entry = self.data["jobs"].get(job_id)
        if not isinstance(entry, dict):
            entry = self.data["jobs"][job_id] = {}
        return entry

    def mail(self, job_id: str, alias: str) -> dict[str, Any]:
        key = f"{job_id}|{alias}"
        entry = self.data["mail"].get(key)
        if not isinstance(entry, dict):
            entry = self.data["mail"][key] = {}
        if not isinstance(entry.get("seen"), list):
            entry["seen"] = []
        return entry

    def proposed(self, job_id: str, key: str) -> bool:
        return key in (self.data["proposed"].get(job_id) or {})

    def note_proposed(self, job_id: str, key: str, day: date) -> None:
        self.data["proposed"].setdefault(job_id, {})[key] = day.isoformat()

    def forget(self, live_ids: Iterable[str]) -> None:
        """Drop the state of jobs that no longer exist."""
        keep = set(live_ids)
        for key in ("jobs", "proposed"):
            self.data[key] = {job: value for job, value in self.data[key].items() if job in keep}
        self.data["mail"] = {key: value for key, value in self.data["mail"].items()
                             if key.partition("|")[0] in keep}

    def hold_until(self) -> tuple[datetime | None, str]:
        holds = self.data["holds"]
        return parse_stamp(holds.get("until")), str(holds.get("why") or "")

    def set_hold(self, until: datetime | None, why: str = "") -> None:
        self.data["holds"] = {"until": stamp(until), "why": why} if until is not None else {}

    def checked(self, origin: str, status: str, now: datetime | None = None) -> None:
        self.data["check"] = {"at": stamp(now), "origin": origin, "status": status}

    def last_check(self) -> tuple[datetime | None, str, str]:
        check = self.data["check"]
        return parse_stamp(check.get("at")), str(check.get("origin") or ""), str(check.get("status") or "")

    def to_announce(self, run_id: str) -> None:
        if run_id not in self.data["announce"]:
            self.data["announce"].append(run_id)

    def take_announce(self) -> list[str]:
        runs, self.data["announce"] = list(self.data["announce"]), []
        return runs


def read_runner_state(files: StandingFiles) -> dict[str, Any]:
    """What the app shows of the runner's state (read only)."""
    data, _problem = read_json(files.state)
    return dict(data or {})


# --------------------------------------------------------------------------
# Run files (the runner writes once; the app reads; pruned)
# --------------------------------------------------------------------------

class RunFiles:
    def __init__(self, files: StandingFiles) -> None:
        self.files = files

    def write(self, record: Mapping[str, Any], started: datetime) -> Path | None:
        """Write one run's record as a new file (never over another); its path, or None (logged)."""
        run_id = record.get("id")
        if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
            raise ValueError("a run record needs its id")
        path = self.files.runs / f"{started.strftime('%Y%m%d-%H%M%S')}-{run_id}.json"
        if path.exists():
            raise FileExistsError("a run file of that id exists")
        return path if write_json(path, record, what="a run file") else None

    def paths(self) -> list[Path]:
        """The run files, oldest first (by name: their start time)."""
        try:
            names = sorted(entry.name for entry in os.scandir(self.files.runs)
                           if entry.is_file() and _RUN_NAME_RE.fullmatch(entry.name))
        except OSError:
            return []
        return [self.files.runs / name for name in names[-MAX_RUN_FILES:]]

    @staticmethod
    def started_of(path: Path) -> datetime | None:
        match = _RUN_NAME_RE.fullmatch(path.name)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").astimezone()
        except ValueError:
            return None

    @staticmethod
    def read(path: Path) -> dict[str, Any] | None:
        """A run file's record, or None when it is unreadable or its id is not its name's."""
        match = _RUN_NAME_RE.fullmatch(path.name)
        if match is None:
            return None
        data, _problem = read_json(path, limit=MAX_RUN_FILE_BYTES)
        if data is None or data.get("id") != match.group(2):
            return None
        return data

    def prune(self, keep_days: int, now: datetime | None = None, *, before: datetime | None = None) -> int:
        """Delete the run files older than ``keep_days`` (or started before ``before``: Clear); how many went."""
        now = now or _now()
        cutoff = now - timedelta(days=max(1, int(keep_days)))
        if before is not None:
            cutoff = max(cutoff, before)
        gone = 0
        for path in self.paths():
            started = self.started_of(path)
            if started is not None and started < cutoff:
                try:
                    path.unlink()
                    gone += 1
                except OSError:
                    pass
        return gone


# --------------------------------------------------------------------------
# Run now requests (the app writes, the runner takes)
# --------------------------------------------------------------------------

class RunRequests:
    def __init__(self, files: StandingFiles) -> None:
        self.files = files

    def ask(self, job_id: str, now: datetime | None = None) -> bool:
        if not _JOB_ID_RE.fullmatch(job_id or ""):
            raise ValueError("not a job id")
        return write_json(self.files.requests / f"{job_id}.json", {"job": job_id, "at": stamp(now)},
                          what="a Run now request")

    def take(self) -> list[str]:
        """The job ids asked for (each request file deleted as it is taken), oldest first."""
        try:
            entries = sorted((entry.stat().st_mtime, entry.name) for entry in os.scandir(self.files.requests)
                             if entry.is_file() and entry.name.endswith(".json"))
        except OSError:
            return []
        taken: list[str] = []
        for _mtime, name in entries:
            job_id = name[:-5]
            try:
                (self.files.requests / name).unlink()
            except OSError:
                continue
            if _JOB_ID_RE.fullmatch(job_id) and job_id not in taken:
                taken.append(job_id)
        return taken

    def pending(self) -> list[str]:
        try:
            return sorted(entry.name[:-5] for entry in os.scandir(self.files.requests)
                          if entry.is_file() and _JOB_ID_RE.fullmatch(entry.name[:-5]))
        except OSError:
            return []


# --------------------------------------------------------------------------
# Usage: runs by local date, under a lock shared by every process
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class DayCounts:
    runs: int = 0
    jobs: Mapping[str, int] | None = None
    checks: Mapping[str, int] | None = None
    tidy: Mapping[str, int] | None = None

    def job(self, job_id: str) -> int:
        return int((self.jobs or {}).get(job_id, 0))

    def checks_of(self, rule_id: str) -> int:
        return int((self.checks or {}).get(rule_id, 0))

    def tidy_of(self, rule_id: str) -> int:
        return int((self.tidy or {}).get(rule_id, 0))


class Usage:
    """standing_usage.json: {"days": {"YYYY-MM-DD": {"runs": n, "jobs": {id: n}, "checks": {id: n}, "tidy": {id: n}}}}
    by LOCAL date ("a capped job waits until tomorrow"). Every read and write is under the lock file, so the app's
    dry runs and the runner's runs share one count. A damaged file counts as empty (logged once)."""

    def __init__(self, files: StandingFiles, clock: Callable[[], datetime] = _now) -> None:
        self.files = files
        self._clock = clock
        self._mutex = threading.Lock()
        self._warned = False

    def _read(self) -> dict[str, Any]:
        data, problem = read_json(self.files.usage)
        if problem and not self._warned:
            self._warned = True
            logger.warning("Standing: standing_usage.json is %s; counting from zero", problem)
        days = (data or {}).get("days")
        return {"days": dict(days) if isinstance(days, dict) else {}}

    def _write(self, data: dict[str, Any], today: date) -> bool:
        oldest = (today - timedelta(days=USAGE_DAYS_KEPT)).isoformat()
        data["days"] = {day: value for day, value in data["days"].items() if isinstance(day, str) and day >= oldest}
        return write_json(self.files.usage, data, what="standing_usage.json")

    @staticmethod
    def _day(data: dict[str, Any], today: date) -> dict[str, Any]:
        entry = data["days"].get(today.isoformat())
        if not isinstance(entry, dict):
            entry = data["days"][today.isoformat()] = {}
        entry["runs"] = int(entry.get("runs") or 0) if isinstance(entry.get("runs"), int) else 0
        for key in ("jobs", "checks", "tidy"):
            if not isinstance(entry.get(key), dict):
                entry[key] = {}
        return entry

    def today(self) -> DayCounts:
        with self._mutex, FileLock(self.files.usage_lock):
            data = self._read()
            entry = self._day(data, self._clock().date())
            return DayCounts(entry["runs"], dict(entry["jobs"]), dict(entry["checks"]), dict(entry["tidy"]))

    def peek(self) -> DayCounts:
        """Today's counts without the lock (a status to show: the file is always written whole)."""
        data = self._read()
        entry = self._day(data, self._clock().date())
        return DayCounts(entry["runs"], dict(entry["jobs"]), dict(entry["checks"]), dict(entry["tidy"]))

    def try_run(self, job_id: str, *, total_cap: int, job_cap: int) -> bool:
        """Count one Claude run of ``job_id`` today when both caps allow it; False (nothing counted) when not."""
        with self._mutex, FileLock(self.files.usage_lock):
            data = self._read()
            today = self._clock().date()
            entry = self._day(data, today)
            if entry["runs"] >= total_cap or int(entry["jobs"].get(job_id, 0)) >= job_cap:
                return False
            entry["runs"] += 1
            entry["jobs"][job_id] = int(entry["jobs"].get(job_id, 0)) + 1
            self._write(data, today)
            return True

    def add(self, key: str, rule_id: str, count: int) -> None:
        """Add ``count`` rule checks ("checks") or tidy-ups ("tidy") of ``rule_id`` today."""
        if key not in ("checks", "tidy") or count <= 0:
            return
        with self._mutex, FileLock(self.files.usage_lock):
            data = self._read()
            today = self._clock().date()
            entry = self._day(data, today)
            entry[key][rule_id] = int(entry[key].get(rule_id, 0)) + int(count)
            self._write(data, today)
