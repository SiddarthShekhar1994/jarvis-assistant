"""Ask Jarvis from the command line (no window, no Qt):

    --ask-check                      is Ask ready? Claude Code found, its version and flags, a
                                     claude.ai plan sign-in, the work folder, which credential
                                     variables are kept from it, mail reading, usage and the
                                     extra-usage pause. Runs only
                                     claude --version, --help and auth status: no model request.
    --ask-text "..." --ask-dry-run   the exact text Jarvis would send to Claude Code first, with
                                     email text shown as counts, and the command line; no model
                                     request and nothing counted against the caps
    --ask-text "..."                 one real Ask (one or two planner runs on your Claude plan):
                                     prints what the planner said, the cards and an init summary

These print to the console only. The log gets what the app logs anyway (kinds, counts, durations):
never the request, the context or the planner's text.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from ..actions import card_view
from ..config import Config
from . import cli
from .context import BriefingContext
from .planner import AskPlanner, ClaudeEngine, GoogleSources, Readiness, briefing_from_page
from .usage import USAGE_FILE, UsageLog

logger = logging.getLogger(__name__)

ACTIONS_FILE = "actions.json"   # as ui.ACTIONS_FILE (ui imports Qt; this module must not)
STAGE_WORDS = {"context": "Reading your calendar...", "planning": "Planning (uses your Claude plan)...",
               "mail": "Reading your mail...", "planning_again": "Planning with your mail (uses your Claude plan)..."}


def google_sources(config: Config) -> GoogleSources:
    """The app's Google objects for Ask (read-only use; nothing here signs in)."""
    from ..executor import build_accounts, build_calendars, build_readers, build_senders

    accounts = build_accounts(config)
    calendars = build_calendars(config, accounts) if config.calendar.enabled else {}
    return GoogleSources(config, calendars, build_senders(config, accounts), build_readers(config, accounts))


def cli_briefing(config: Config, from_file: str | None, *, now: datetime, out: TextIO) -> BriefingContext | None:
    """Today's briefing for a command-line Ask: the saved fixture (--from-file) or the Notion page
    (a read), its decided cards left out; None (said so) when it can't be read."""
    from ..actions import ActionStore
    from ..notion_client import FixtureSession, NotionClient, NotionError, fetch_briefing

    try:
        if from_file:
            session = FixtureSession(Path(from_file))
            client = NotionClient("", session=session, notion_version=config.notion_version)
            page_id = session.page_id or config.page_id
        else:
            if not config.notion_token or not config.page_id:
                out.write("Briefing: not loaded (NOTION_TOKEN / BRIEFING_PAGE_ID are not set)\n")
                return None
            client = NotionClient(config.notion_token, notion_version=config.notion_version)
            page_id = config.page_id
        page = fetch_briefing(client, page_id, now=now)
    except (NotionError, OSError, ValueError) as exc:
        out.write(f"Briefing: not loaded ({type(exc).__name__})\n")
        return None
    store = ActionStore(config.data_dir / ACTIONS_FILE)
    return briefing_from_page(page, config=config, now=now, decided=store.is_decided)


def _readiness_lines(ready: Readiness, config: Config, environ: Mapping[str, str]) -> list[str]:
    enabled = "yes" if config.ask.enabled else "no (set enabled = true under [ask] in config.toml)"
    lines = [f"  [ask] enabled: {enabled}"]
    if ready.exe is None:
        lines.append(f"  Claude Code: not found - {ready.message}")
        return lines
    lines.append(f"  Claude Code: {ready.version or 'unknown version'} ({ready.exe.source})")
    lines.append(f"    {ready.exe.path}")
    if ready.problem == "workdir":
        lines.append(f"  Work folder: {ready.message}")
        return lines
    if ready.problem == "cli_unsupported":
        lines.append(f"  Flags: {ready.message}")
        return lines
    lines.append("  Flags: every flag Ask needs is there; --safe-mode/--restricted: "
                 + ("used" if ready.hardened else "available, not used ([ask] hardened_flags)" if ready.hardening
                    else "not available"))
    auth = ready.auth
    if auth is not None:
        state = "OK" if auth.ok else auth.message
        lines.append(f"  Sign-in: {auth.method or 'none'} ({auth.provider or 'unknown provider'}) - {state}")
    elif ready.message:
        lines.append(f"  Sign-in: {ready.message}")
    removed, money = cli.removed_env(environ)
    kept = len([name for name in environ if name.upper() in cli.CHILD_ENV_ALLOWED])
    lines.append(f"  Child environment: {kept} variable(s) kept (Windows basics, proxies), {len(removed)} removed"
                 + (f", including {', '.join(money)}" if money else "") + "; values are never read or shown")
    return lines


def ask_check(config: Config, out: TextIO, *, environ: Mapping[str, str] | None = None,
              runner: cli.Runner | None = None, sources: GoogleSources | None = None,
              usage: UsageLog | None = None) -> int:
    """Print whether Ask is ready (no model request). 0 when it is ready to run, 1 otherwise."""
    environ = os.environ if environ is None else environ
    engine = ClaudeEngine(config.ask, config.data_dir, runner=runner, environ=environ)
    ready = engine.readiness(refresh=True)
    out.write("Ask Jarvis check (no Claude request was made)\n")
    for line in _readiness_lines(ready, config, environ):
        out.write(line + "\n")
    sources = sources if sources is not None else google_sources(config)
    try:
        accounts = sources.accounts()
    except Exception as exc:  # noqa: BLE001 - only a report
        out.write(f"  Accounts: could not be read ({type(exc).__name__})\n")
        accounts = []
    for account in accounts:
        out.write(f"  Account {account.alias}: calendar {account.calendar}, sending "
                  f"{'set up' if account.send_mail else 'not set up'}, mail reading {account.read_mail}\n")
    usage = usage or UsageLog(config.data_dir / USAGE_FILE, max_per_hour=config.ask.max_per_hour,
                              max_per_day=config.ask.max_per_day)
    hour, day = usage.counts()
    out.write(f"  Usage: {hour} of {config.ask.max_per_hour} planner runs this hour, {day} of "
              f"{config.ask.max_per_day} today\n")
    caps = usage.check()
    if caps.held:
        out.write(f"  Paused: {caps.message}\n")
    ok = ready.ok and config.ask.enabled and not caps.held
    out.write(f"Ready: {'yes' if ok else 'no'}\n")
    return 0 if ok else 1


def ask_dry_run(text: str, config: Config, out: TextIO, *, briefing: BriefingContext | None,
                environ: Mapping[str, str] | None = None, runner: cli.Runner | None = None,
                sources: GoogleSources | None = None) -> int:
    """Print what the first planner run would get (email text as counts) and its command line."""
    environ = os.environ if environ is None else environ
    engine = ClaudeEngine(config.ask, config.data_dir, runner=runner, environ=environ)
    planner = AskPlanner(config, engine, sources if sources is not None else google_sources(config))
    dry = planner.dry_run(text, briefing=briefing)
    out.write("Ask Jarvis dry run (no Claude request was made; nothing was counted)\n")
    out.write("---- stdin (email text shown as counts) ----\n")
    out.write(dry.prompt)
    out.write("---- end of stdin ----\n")
    if dry.argv:
        out.write("Command line (the prompt goes to stdin, never here):\n  " + subprocess.list2cmdline(dry.argv) + "\n")
    else:
        out.write(f"Command line: not built ({dry.readiness.message})\n")
    out.write("Context: " + ", ".join(f"{name} {count}" for name, count in dry.counts.items())
              + f", mail {dry.mail.threads} thread(s), {dry.mail.messages} message(s), {dry.mail.chars} characters\n")
    if not dry.readiness.ok:
        out.write(f"Not ready to run: {dry.readiness.message}\n")
    return 0


def ask_once(text: str, config: Config, out: TextIO, *, briefing: BriefingContext | None,
             environ: Mapping[str, str] | None = None, runner: cli.Runner | None = None,
             sources: GoogleSources | None = None, now: Callable[[], datetime] | None = None) -> int:
    """One real Ask (the owner's spike): one or two planner runs on the Claude plan."""
    environ = os.environ if environ is None else environ
    engine = ClaudeEngine(config.ask, config.data_dir, runner=runner, environ=environ)
    planner = AskPlanner(config, engine, sources if sources is not None else google_sources(config), clock=now)
    page_ids = [action.id for action in (briefing.pending if briefing else ())]
    outcome = planner.plan(text, briefing=briefing, page_ids=page_ids,
                           on_stage=lambda stage: out.write(STAGE_WORDS.get(stage, stage) + "\n"))
    if outcome.init is not None:
        init = outcome.init
        structured = "yes" if init.structured_tool else "no"
        out.write(f"Init: apiKeySource={init.api_key_source}, tools={list(init.tools)}, "
                  f"mcp_servers={init.mcp_servers}, model={init.model}, version={init.version}, "
                  f"structured_output={structured}\n")
    for number, stats in enumerate(outcome.stats, 1):
        out.write(f"Run {number}: {stats.duration_ms / 1000:.1f} s, {stats.turns} turn(s), {stats.input_tokens} in / "
                  f"{stats.output_tokens} out / {stats.cache_read_tokens} cache-read / {stats.cache_write_tokens} "
                  f"cache-write tokens, exit {stats.exit_code}\n")
    if outcome.search:
        out.write(f"Mail search ({outcome.search_account}): {outcome.search}\n")
    if outcome.mail.threads:
        out.write(f"Mail read: {outcome.mail.threads} thread(s), {outcome.mail.messages} message(s)\n")
    if not outcome.ok:
        out.write(f"Not planned: {outcome.message}\n")
        return 1
    out.write(f"Say: {outcome.say}\n")
    if outcome.question:
        out.write(f"Question: {outcome.question}\n")
    if outcome.message:
        out.write(f"Note: {outcome.message}\n")
    today = (now or (lambda: datetime.now().astimezone()))().date()
    for card in outcome.cards:
        view = card_view(card, today)
        extra = f" (unverified recipients: {', '.join(sorted(card.unverified))})" if card.unverified else ""
        out.write(f"Card: ASK {view.kind_label.upper()} | {view.title} | {view.detail}{extra}\n")
    if not outcome.cards:
        out.write("Cards: none\n")
    out.write(f"Total: {outcome.runs} planner run(s), {outcome.duration_ms / 1000:.1f} s\n")
    return 0


def run(args: Any, config: Config, out: TextIO | None) -> int:
    """The __main__ entry for --ask-check / --ask-text [--ask-dry-run]."""
    stream: Any = out if out is not None else _NullOut()
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:   # a redirected console may not take every character of an event title or a briefing
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass
    if args.ask_check:
        return ask_check(config, stream)
    now = datetime.now().astimezone()
    briefing = cli_briefing(config, getattr(args, "from_file", None), now=now, out=stream)
    if args.ask_dry_run:
        return ask_dry_run(args.ask_text, config, stream, briefing=briefing)
    return ask_once(args.ask_text, config, stream, briefing=briefing)


class _NullOut:
    def write(self, _text: str) -> int:
        return 0
