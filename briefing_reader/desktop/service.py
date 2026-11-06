"""The desktop's work on this PC, as the "desktop" thread does it (SPEC sections 3, 4.1, 5 and 6.4; no Qt).

A ``DesktopService`` belongs to the desktop worker's thread (desktop.worker): every method is called there and only
there, one at a time, so nothing in it is shared with the GUI or the Ask thread. It holds:

* the backend, made there at the first job (``backend_factory``: Win32Backend in the app, a fake in the tests; while
  the test guard is on the real one refuses to be made - then the desktop can't look or open, and says so);
* the scope (paths.load_scope over [desktop] folders and extra_folders, the Jarvis project and its data folder
  excluded), the Start menu (desktop.apps.load) and the folder tree (desktop.index.folder_tree), each kept a while -
  a re-check before an OS call always reads the scope and the Start menu afresh;
* ``resolve(words)``: a typed desktop request - its local route (desktop.routes; a find searches) carried out now
  (desktop.executor), or else the planner's context (``context_for``: the places, the files the owner's words find
  - first in the places his words name -, the Recent items, the files changed in the days his words say, the
  Start-menu apps his words name, within SPEC 4.1's caps; names only reach the planner);
* ``carry_out(plan, words, context, cancel)``: a planner's checked plan carried out (desktop.executor; its file steps'
  card plan made and frozen - DesktopRun.card, P2.2 - and nothing of them done before the owner's click);
* ``click(click, cancel)``: the owner's click on a result card (P1.9: desktop.results.Click - the card's own item,
  re-checked and carried out by executor.run_click);
* ``carry_out_card(card, sha=, ticked=, cancel=, ...)`` (P2.3): the owner's click on a NEEDS YOUR OK card of file
  steps - its frozen plan checked (desktop.cardplan: expired or not the plan shown: nothing runs), then exactly its
  ticked rows through desktop.fileops, journalled in the run's own journal (``journal_folder``: the app's
  LOCALAPPDATA one, a test's temporary one), each row re-checked right before its OS call; Esc held only while the
  batch acts (desktop.stopkeys); each row's end heard as it comes (the card's "Moving 2 of 5");
* ``undo_card(run_id, seqs, cancel=, ...)`` (P2.3): the owner's Undo on the card's stamp - fileops.Undoer, item by
  item, newest first, each re-checked ("Moved since - left where it is."), for [desktop] undo_days only - Jarvis's own
  copies and zips (unchanged) go to the Recycle Bin, items it binned come back out of it;
* P2.6, the Recycle Bin (``recycler()``: desktop.recycle.RecycleBin over this PC's backend and the bin's Windows side,
  made here at the first use over ``recycle_factory`` - the real Win32RecycleBin in the app, a fake in the tests; none
  to reach: fileops.NoRecycleBin, which bins nothing): a card's rows to the bin are checked by it before the card
  shows and carried out through it on the click, and every Undo that bins (a copy, a zip, a screenshot, a part-made
  copy) or restores goes through it;
* ``find(query)``: a find_files step's search;
* P2.4, the PC's controls (``pc()``: desktop.pcctl.PcControl, made here at the first use over ``pc_factory`` - the
  real Win32Pc in the app, a fake in the tests -, the app's TimerBook ``timers`` and [desktop] screenshots_dir): a
  local route to them is carried out like any other (``resolve(words, hud=)``: ``hud`` the HUD's windows, left out
  of a screenshot), and ``chime()`` plays a timer's end sound.
* P2.5: focus mode through the same controls (``focus``: the app's FocusBook); ``undo_pc(changes, cancel=)`` puts the
  HUD's instant changes back (the owner's click on a chip's Undo or on Undo all: PcControl.undo_changes, newest
  first); ``dnd_state()`` reads Windows' Do Not Disturb back (read only) and ``open_dnd(cancel=)`` opens its settings
  page on the owner's click (the one fixed call).
* ``recall_undo(cancel=)`` (p2r-3, D9's 7-day Undo): the owner's "undo" - the newest run within undo_days whose
  items Undo may still put back (fileops.Undoer.undoable over each journal), rebuilt as its stamped card
  (tidy.recalled); nothing is put back here (his click on its Undo goes through ``undo_card`` like any card's);
* ``housekeep(...)`` (p2r-1; a function of its own - it needs no service): the journal's start-up recovery and its
  purge of the runs older than 30 days, at start-up and at the owner's Turn on, then at the first desktop request of
  each new day (desktop_ui; never on a timer).
* A task's reads (agent.owner, part A4): ``find_for_task(words)`` (the files its words find, the places they are in and
  the apps they name: names only), ``list_for_task(place)`` (what one place holds, or the roots), ``read_text(item,
  start=, cap=)`` (a part of a text-like file: re-checked right before the read - the scope as it is now, the same item
  - and never a secret, an online-only file or any other type), ``in_secret_folders(paths)`` (which of them lie in a
  folder named for secrets) and ``pc_for_task(asked, ...)`` (a PC control asked for by a task, carried out as a local
  route is). ``cancel`` stops each before it touches anything.

Each job's ``cancel`` (anything with ``is_set()``) holds the app's stop and THE CONSENT GATE (desktop_ui: the owner's
Turn off): the executor asks it before each step and right before each OS call - once it is set nothing opens, and
nothing more is looked at.

Every run that comes back carries its result cards (``DesktopRun.shown``: desktop.results.present, made here right
after the run - the other matches and the found items re-checked first, the app a file opened in read from the
associations) and, for its file steps, the tidy card (desktop.tidy.make_card over its frozen plan).

Logs carry counts, durations and exception types only.

Public API
==========
``LOCAL``, ``PLANNER``, ``UNAVAILABLE``; ``Resolved(kind, run_id, route, run, context, seconds)``;
``DesktopService(folders=, extra_folders=, excluded=, backend_factory=, sites=, clock=, now=, hour24=,
journal_folder=, undo_days=, pc_factory=, timers=, screenshots_dir=, focus=, recycle_factory=)``: ``backend()``,
``recycler()``, ``pc()``, ``chime()``,
``undo_pc(changes, cancel=)``, ``dnd_state()``, ``open_dnd(cancel=)``,
``scope(fresh=)``, ``apps(fresh=)``, ``tree()``, ``route_context()``, ``resolve(words, planner=, override=, run_id=,
cancel=, hud=)``, ``context_for(words)``, ``carry_out(plan, words, context, cancel)``, ``click(click, cancel)``,
``carry_out_card(card, sha=, ticked=, cancel=, on_start=, on_row=, stop_keys=, on_stop=)``, ``undo_card(run_id, seqs,
cancel=, on_start=, stop_keys=, on_stop=)``, ``find(query)``, ``executor()``; ``days_in(words)``; ``RECENT_FILES``;
``housekeep(journal_folder, backend, cancel=)`` -> ``Housekept``; ``recall_undo(cancel=)`` -> ``Recalled``.
``find_for_task(words, cancel=)``, ``list_for_task(place, cancel=)`` -> ``TaskListing``, ``read_text(item, start=, cap=,
cancel=)`` -> ``TextPart``, ``in_secret_folders(paths, cancel=)``, ``reveal_folder(path, cancel=)``,
``pc_for_task(asked, run_id=, cancel=, hud=)``; ``READABLE_TYPES``, ``READ_*`` codes.
"""

from __future__ import annotations

import codecs
import logging
import ntpath
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from . import apps as desktop_apps
from . import cardplan, guard, paths, pcctl, recycle, stopkeys, tidy
from . import fileops as fo
from . import journal as jn
from . import results as desktop_results
from . import routes as desktop_routes
from . import validate as dv
from .backend import DesktopBackend
from .context import MAX_FILES, MAX_PLACES, AppItem, DesktopContext, Place, SiteItem, build_context
from .executor import DesktopRun, Executor, FindQuery
from .index import (LocalSearch, TreeFolder, file_item, folder_tree, name_score, pick_places, place_item, query_words,
                    recent_files, skipped)
from .journal import Journal, default_folder

logger = logging.getLogger(__name__)

LOCAL, PLANNER, UNAVAILABLE = "local", "planner", "unavailable"
SCOPE_TTL_S = 60.0                      # the scope is read again after this long (a re-check always reads it anew)
APPS_TTL_S = 600.0                      # the Start menu likewise
TREE_TTL_S = 300.0                      # the folder tree likewise
RECENT_FILES = 30                       # Recent items' files a context takes at most
WITHIN_FILES = 40                       # files changed in the days the words say, at most
# Days back the owner's words ask for ("from yesterday", "this week").
_DAYS = ((("today",), 1), (("yesterday",), 2), (("this", "week"), 7), (("last", "week"), 14), (("past", "week"), 7),
         (("this", "month"), 31), (("last", "month"), 62))
# What a task may read of a file (agent.owner): plain text-like types, no parser. Never a program or a config full of
# keys: those are not here, and guard's NEVER list is asked first.
READABLE_TYPES = frozenset({"txt", "text", "md", "markdown", "rst", "csv", "tsv", "json", "jsonl", "ndjson", "log",
                            "html", "htm", "xml", "yaml", "yml", "srt", "vtt", "tex", "bib"})
READ_BYTES_CAP = 8 * 1024 * 1024        # of a file: the first this much, and it says so
SECRET_MARGIN = 256                     # characters either side of a part looked at for a key cut by its end
LIST_ENTRIES_CAP = 2000                 # entries of a folder looked at for a task
READ_TYPE, READ_ONLINE, READ_SECRET, READ_BINARY, READ_STOPPED, READ_UNAVAILABLE, READ_FAILED = (
    "read_type", "read_online", "read_secret", "read_binary", "read_stopped", "read_unavailable", "read_failed")


def decode_text(raw: bytes, *, cut: bool = False) -> str | None:
    """A text file's bytes as text (a byte order mark says which; else UTF-8, else Windows' own); None when they don't
    read as text. ``cut``: they end where the read stopped, maybe inside a character."""
    for mark, name in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                       (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(mark):
            return raw[len(mark):].decode(name.removesuffix("-sig"), errors="replace")
    if b"\x00" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        if cut and exc.start >= len(raw) - 3:   # a character cut at the end of what was read
            return raw[:exc.start].decode("utf-8", errors="replace")
    return raw.decode("cp1252", errors="replace")


@dataclass(frozen=True)
class TextPart:
    """A part of a text file, or why not (``code``): characters ``first`` to ``last`` of what was read; ``more``: there
    are more of them; ``cut``: the file is longer than Jarvis reads; ``hidden``: it holds a hidden message (read before
    such characters are cleaned away). Memory only."""

    code: str = ""
    text: str = field(default="", repr=False)
    first: int = 0
    last: int = 0
    more: bool = False
    cut: bool = False
    hidden: bool = False

    def __repr__(self) -> str:
        return f"TextPart({self.code or 'read'}, {self.last - self.first} characters)"


@dataclass(frozen=True)
class TaskListing:
    """What one place holds, for a task (or the roots): its folders and files, each checked, and how many were left out;
    ``code``: why it can't be listed."""

    code: str = ""
    places: tuple[Place, ...] = field(default=(), repr=False)
    files: tuple[Any, ...] = field(default=(), repr=False)
    left_out: int = 0

    def __repr__(self) -> str:
        return f"TaskListing({self.code or 'ok'}, {len(self.places)} places, {len(self.files)} files)"


def days_in(words: Any) -> int | None:
    """How many days back the owner's words look ("yesterday": 2; "this week": 7), or None."""
    tokens = guard.words(words, camel=False)
    found = [days for phrase, days in _DAYS
             if any(tuple(tokens[i:i + len(phrase)]) == phrase for i in range(len(tokens) - len(phrase) + 1))]
    return max(found) if found else None


@dataclass(frozen=True)
class Resolved:
    """A typed desktop request, resolved on the desktop thread: a local route carried out (LOCAL: ``route``,
    ``run``), the planner's context (PLANNER: ``context``; None when the planner may not run) or nothing at all
    (UNAVAILABLE: the desktop can't look or open now)."""

    kind: str
    run_id: str = ""
    route: Any = field(default=None, repr=False)
    run: DesktopRun | None = None
    context: DesktopContext | None = field(default=None, repr=False)
    seconds: float = 0.0


class DesktopService:
    """See the module docstring. Every method runs on the desktop worker's thread only."""

    def __init__(self, *, folders: Sequence[str], extra_folders: Sequence[str] = (), excluded: Sequence[str] = (),
                 backend_factory: Callable[[], DesktopBackend], sites: Sequence[SiteItem] = (),
                 clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] | None = None, hour24: bool = False,
                 journal_folder: Callable[[], Any] | None = None, undo_days: int = 7,
                 pc_factory: Callable[[], Any] | None = None, timers: pcctl.TimerBook | None = None,
                 screenshots_dir: str = "", focus: pcctl.FocusBook | None = None,
                 recycle_factory: Callable[[], Any] | None = None) -> None:
        self._folders = tuple(folders)
        self._recycle_factory = recycle_factory or recycle.Win32RecycleBin   # refuses to be made while the tests run
        self._recycler: Any = None
        self._pc_factory = pc_factory or pcctl.Win32Pc          # refuses to be made while the tests run
        self._timers = timers
        self._focus = focus
        self._screenshots_dir = str(screenshots_dir or "")
        self._pc: pcctl.PcControl | None = None
        self._extra = tuple(extra_folders)
        self._excluded = tuple(str(path) for path in excluded if path)
        self._factory = backend_factory
        self._sites = tuple(site for site in sites if isinstance(site, SiteItem))
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._hour24 = bool(hour24)
        self._journal_folder = journal_folder or default_folder     # refuses while the tests run: they pass theirs
        self._undo_days = int(undo_days)
        self._backend: DesktopBackend | None = None
        self._backend_failed = False
        self._scope: tuple[float, paths.Scope] | None = None
        self._apps: tuple[float, tuple[AppItem, ...]] | None = None
        self._tree: tuple[float, list[TreeFolder]] | None = None

    # ---- what it keeps ------------------------------------------------------------------------------

    def backend(self) -> DesktopBackend | None:
        """The backend, made at the first call (on this thread); None when it can't be (logged by its type once)."""
        if self._backend is None and not self._backend_failed:
            try:
                self._backend = self._factory()
            except Exception as exc:  # noqa: BLE001 - RealBackendInTests in the tests; anything else: no desktop
                self._backend_failed = True
                logger.warning("Desktop: Jarvis can't look on this PC (%s)", type(exc).__name__)
        return self._backend

    def scope(self, *, fresh: bool = False) -> paths.Scope:
        """The roots and the excluded places (read again when ``fresh`` or after SCOPE_TTL_S)."""
        backend = self.backend()
        if backend is None:
            return paths.Scope()
        now = self._clock()
        if fresh or self._scope is None or now - self._scope[0] > SCOPE_TTL_S:
            self._scope = (now, paths.load_scope(self._folders, self._extra, backend, excluded=self._excluded))
        return self._scope[1]

    def apps(self, *, fresh: bool = False) -> tuple[AppItem, ...]:
        """The Start-menu apps Jarvis may open (read again when ``fresh`` or after APPS_TTL_S; none when it can't be
        read)."""
        backend = self.backend()
        if backend is None:
            return ()
        now = self._clock()
        if fresh or self._apps is None or now - self._apps[0] > APPS_TTL_S:
            try:
                listed = desktop_apps.load(backend).apps
            except Exception as exc:  # noqa: BLE001 - no Start menu: no apps
                logger.warning("Desktop: the Start menu could not be read (%s)", type(exc).__name__)
                listed = ()
            self._apps = (now, listed)
        return self._apps[1]

    def tree(self) -> list[TreeFolder]:
        """The folders below the roots down to 3 levels (read again after TREE_TTL_S)."""
        backend = self.backend()
        if backend is None:
            return []
        now = self._clock()
        if self._tree is None or now - self._tree[0] > TREE_TTL_S:
            self._tree = (now, folder_tree(self.scope(), backend, clock=self._clock))
        return self._tree[1]

    def recycler(self) -> Any:
        """The Recycle Bin (P2.6): desktop.recycle.RecycleBin over the backend and the bin's Windows side, made at the
        first call (on this thread); fileops.NoRecycleBin - nothing is binned - when either can't be made (logged by its
        type once)."""
        if self._recycler is None:
            backend = self.backend()
            if backend is None:
                return fo.NoRecycleBin()
            try:
                seam = self._recycle_factory()
            except Exception as exc:  # noqa: BLE001 - RealBackendInTests in the tests; anything else: no bin
                logger.warning("Desktop: the Recycle Bin can't be reached (%s)", type(exc).__name__)
                self._recycler = fo.NoRecycleBin()
            else:
                self._recycler = recycle.RecycleBin(backend, seam, scope=lambda: self.scope(fresh=True),
                                                    clock=self._clock)
        return self._recycler

    def executor(self) -> Executor | None:
        backend = self.backend()
        if backend is None:
            return None
        return Executor(backend, scope=lambda: self.scope(fresh=True), apps=lambda: self.apps(fresh=True),
                        find=self.find, clock=self._clock, now=self._now, pc=self.pc(), recycler=self.recycler())

    def pc(self) -> pcctl.PcControl:
        """The PC's controls (P2.4), made at the first call; their seam is made at their first use (on this
        thread)."""
        if self._pc is None:
            self._pc = pcctl.PcControl(self._pc_factory, disk=self.backend, timers=self._timers,
                                       screenshots_dir=self._screenshots_dir, clock=self._clock, now=self._now,
                                       focus=self._focus, recycler=_LazyRecycler(self.recycler))
        return self._pc

    def chime(self) -> bool:
        """A timer's end sound (the app's toast says it too)."""
        return self.pc().chime()

    def undo_pc(self, changes: Sequence[pcctl.PcChange], *,
                cancel: Any = None) -> tuple[tuple[pcctl.PcChange, pcctl.PcChange], ...]:
        """P2.5: the HUD's instant changes put back on the owner's click (pcctl.PcControl.undo_changes: newest first,
        each checked against this PC as it is now; ``cancel`` - the app's stop and THE CONSENT GATE - asked right
        before each OS call)."""
        if cancel is not None and cancel.is_set():
            return tuple((change, pcctl.PcChange(change.kind, pcctl.UNDO, pcctl.STOPPED, group=change.group))
                         for change in reversed(list(changes)))
        return self.pc().undo_changes(changes, cancel=cancel)

    def reveal_file(self, path: str, *, cancel: Any = None) -> bool:
        """A task's output shown selected in its folder on the owner's click: the backend's show_in_folder, whose last
        guard stands. It never opens the file. True when it was asked of Windows."""
        if cancel is not None and cancel.is_set():
            return False
        self.backend().show_in_folder(path)
        return True

    def reveal_folder(self, path: str, *, cancel: Any = None) -> bool:
        """The folder of a task's output opened on the owner's click, with nothing selected (a page the agent wrote:
        Enter there would open it in the real browser). True when it was asked of Windows."""
        if cancel is not None and cancel.is_set():
            return False
        self.backend().open_folder(ntpath.dirname(path))
        return True

    def dnd_state(self) -> str:
        """P2.5: Windows' Do Not Disturb read back (read only; pcctl.DND_UNKNOWN when it can't be read)."""
        return self.pc().dnd_state()

    def open_dnd(self, *, cancel: Any = None) -> pcctl.PcChange:
        """P2.5: Windows' notification settings opened on the owner's click on the HUD's chip (the one fixed page)."""
        return self.pc().open_dnd(cancel=cancel)

    # ---- a request ----------------------------------------------------------------------------------

    def route_context(self) -> DesktopContext:
        """What the local routes may name: the roots (as places), every Start-menu app Jarvis may open, the sites."""
        scope = self.scope()
        places = tuple(Place(root.path, (root.label,)) for root in scope.roots)
        return DesktopContext(places, (), self.apps(), self._sites)

    def resolve(self, words: str, *, planner: bool = True,
                override: Callable[[str], DesktopContext] | None = None, run_id: str = "",
                cancel: Any = None, hud: Sequence[int] = ()) -> Resolved:
        """A typed desktop request (see the module docstring). ``override(words)``: a context to use instead of this
        PC's (the tests' invented ones); ``planner``: whether the planner may run (Ask on); ``cancel``: once set (the
        app stopped waiting, or the owner's Turn off), a local route opens nothing and nothing more is looked at;
        ``hud``: the HUD's windows (left out of a screenshot)."""
        started = self._clock()
        if cancel is not None and cancel.is_set():
            return Resolved(UNAVAILABLE, run_id)            # stopped before it began: nothing looked at
        executor = self.executor()
        if executor is None:
            return Resolved(UNAVAILABLE, run_id)
        try:
            routes_context = override(words) if override is not None else self.route_context()
        except Exception as exc:  # noqa: BLE001 - no candidates: the routes that need none still work
            logger.warning("Desktop: the request's candidates could not be read (%s)", type(exc).__name__)
            routes_context = DesktopContext()
        found = desktop_routes.route(words, routes_context, search=self._route_search)
        if found is not None:
            run = self._shown(executor.run_route(found, words=words, run_id=run_id, cancel=cancel, hud=hud), executor,
                              words)
            return Resolved(LOCAL, run_id, found, run, seconds=round(self._clock() - started, 3))
        if not planner:
            return Resolved(PLANNER, run_id, seconds=round(self._clock() - started, 3))
        if cancel is not None and cancel.is_set():
            return Resolved(UNAVAILABLE, run_id)            # stopped: no context is built
        try:
            context = override(words) if override is not None else self.context_for(words)
        except Exception as exc:  # noqa: BLE001 - the planner is then told of nothing
            logger.warning("Desktop: the request's candidates could not be read (%s)", type(exc).__name__)
            context = DesktopContext()
        if not isinstance(context, DesktopContext):
            context = DesktopContext()
        return Resolved(PLANNER, run_id, context=context, seconds=round(self._clock() - started, 3))

    def _route_search(self, words: Sequence[str]) -> list[Any]:
        """The find route's search: the files whose names hold any of ``words`` (desktop.routes keeps those holding
        all of them)."""
        backend = self.backend()
        if backend is None:
            return []
        search = LocalSearch(self.scope(), backend, clock=self._clock, now=self._now)
        return list(search.search(tuple(words)).files)

    def context_for(self, words: str) -> DesktopContext:
        """The planner's context for the owner's ``words`` (see the module docstring)."""
        backend = self.backend()
        if backend is None:
            return DesktopContext()
        scope = self.scope()
        cache: dict[str, bool] = {}
        searched = query_words(words)
        tree = self.tree()
        every = [Place(root.path, (root.label,)) for root in scope.roots] + [Place(f.path, f.parts) for f in tree]
        named = [place.path for place in dv.places_named(words, every)]
        search = LocalSearch(scope, backend, clock=self._clock, now=self._now, cache=cache)
        result = search.search(searched, folders=named or None, limit=MAX_FILES)
        files = list(result.files)
        files += recent_files(scope, backend, limit=RECENT_FILES, cache=cache)
        days = days_in(words)
        if days:
            files += list(search.search((), folders=named or None, within_days=days, limit=WITHIN_FILES).files)
        places = pick_places(scope, backend, tree, words=searched, files=files, named=named, cap=MAX_PLACES,
                             cache=cache)
        context = build_context(places, files, desktop_apps.match(words, self.apps()), self._sites,
                                looked_in=result.looked_in, found=result.found)
        logger.info("Desktop: context of %s from the %s", ", ".join(f"{name} {count}" for name, count in
                                                                     context.counts().items()),
                    result.source or "Recent items")
        return context

    def carry_out(self, plan: dv.DesktopPlan, words: str, context: DesktopContext | None,
                  cancel: Any = None, *, organise: bool = True) -> DesktopRun:
        """A planner's checked plan carried out (desktop.executor), the files it was shown kept for D5's other
        matches; ``organise`` False (the owner's OK is Phase 1's card's): its file steps are refused, no card."""
        executor = self.executor()
        if executor is None:
            return DesktopRun(plan.run_id)
        others = context.files if isinstance(context, DesktopContext) else ()
        return self._shown(executor.run_plan(plan, words=words, others_from=others, cancel=cancel,
                                             organise=organise), executor, words)

    def click(self, click: desktop_results.Click, cancel: Any = None) -> DesktopRun:
        """The owner's click on a result card (desktop.results.Click): the card's own item re-checked and carried out
        (executor.run_click; the click stands for at most ``click.granted``), with the cards of what came of it;
        ``cancel``: once set (the app stopped waiting, or the owner's Turn off), it opens nothing - asked right before
        its OS call."""
        executor = self.executor()
        if executor is None or not isinstance(click, desktop_results.Click):
            return DesktopRun(getattr(click, "run_id", ""))
        run = executor.run_click(click.skill, click.target, granted=click.granted, run_id=click.run_id,
                                 flags=click.flags, via=click.via, words=click.words, others_from=click.others_from,
                                 cancel=cancel)
        return self._shown(run, executor, click.words)

    def _shown(self, run: DesktopRun, executor: Executor, words: str) -> DesktopRun:
        """``run`` with its result cards (desktop.results.present), made now - right after it; none to make (nothing
        opened, waits or was found: refused, failed or stopped) - nothing more is looked at."""
        if not (run.done() or run.waiting() or run.found()):
            return run
        try:
            cards = desktop_results.present(run, executor=executor, scope=self.scope(), backend=executor.backend,
                                            now=self._now(), words=words, hour24=self._hour24)
        except Exception as exc:  # noqa: BLE001 - no cards: the caption still says what happened
            logger.warning("Desktop %s: the result cards could not be made (%s)", run.run_id or "-",
                           type(exc).__name__)
            cards = ()
        waits = any(result.skill in dv.ORGANISE_SKILLS and result.tier == dv.CARD for result in run.waiting())
        if isinstance(run.card, cardplan.CardPlan) and waits:        # p2r-17: never for steps cancelled or refused
            try:
                card = tidy.make_card(run.card, self.scope(), at=self._now())
            except Exception as exc:  # noqa: BLE001 - no card: nothing of its steps can be clicked
                logger.warning("Desktop %s: the tidy card could not be made (%s)", run.run_id or "-",
                               type(exc).__name__)
                card = None
            if card is not None:
                cards = (*cards, card)
        return replace(run, shown=cards)

    # ---- a NEEDS YOUR OK card of file steps (P2.3) -----------------------------------------------------

    def _journal(self, run_id: str) -> Journal | None:
        try:
            return Journal(self._journal_folder(), run_id)
        except Exception as exc:  # noqa: BLE001 - no journal (RealBackendInTests in a test): nothing is done
            logger.warning("Desktop %s: the journal can't be kept (%s); nothing done", run_id or "-",
                           type(exc).__name__)
            return None

    def carry_out_card(self, card: Any, *, sha: str, ticked: Collection[int], cancel: Any = None,
                       on_start: Callable[[bool], None] | None = None,
                       on_row: Callable[[tidy.RowEnd, int, int], None] | None = None,
                       stop_keys: stopkeys.StopKeys | None = None,
                       on_stop: Callable[[], None] | None = None) -> tidy.CardOutcome:
        """The owner's click on a NEEDS YOUR OK card of file steps (``card``: desktop.tidy.TidyCard or its
        cardplan.CardPlan; ``sha``: the plan's sha256 as the card showed it; ``ticked``: the row numbers he left
        ticked): nothing runs when its plan expired or isn't the one shown (cardplan.verify: the code comes back), when
        the desktop can't act or its journal can't be kept (tidy.UNAVAILABLE) or when ``cancel`` was set first (the
        app's Stop, Esc, or THE CONSENT GATE: tidy.STOPPED_FIRST); else Esc is held (``stop_keys``: each press calls
        ``on_stop``; ``on_start(held)``), exactly the chosen rows are carried out through cardplan.carry_out - each
        re-checked right before its OS call, ``cancel`` asked before each - and Esc is let go. ``on_row(end, ended,
        total)`` hears each row's end."""
        started = self._clock()
        plan = getattr(card, "plan", card)
        run_id = str(getattr(plan, "run_id", "") or "")
        if not isinstance(plan, cardplan.CardPlan):
            return tidy.CardOutcome(run_id, tidy.UNAVAILABLE)
        if cancel is not None and cancel.is_set():
            return tidy.CardOutcome(run_id, tidy.STOPPED_FIRST)
        backend = self.backend()
        if backend is None:
            return tidy.CardOutcome(run_id, tidy.UNAVAILABLE)
        code = plan.verify(self._now(), mono=self._clock(), sha=sha)
        if code:
            logger.info("Desktop %s: a card's click was refused (%s)", run_id or "-", code)
            return tidy.CardOutcome(run_id, code)
        journal = self._journal(run_id)
        if journal is None:
            return tidy.CardOutcome(run_id, tidy.UNAVAILABLE)
        numbers = [row.number for row in plan.chosen(ticked)]

        def heard(result: fo.OpResult, ended: int, total: int) -> None:
            if on_row is not None and 0 < ended <= len(numbers):
                on_row(tidy.RowEnd(numbers[ended - 1], result.state, result.reason, result.seq), ended, total)

        ops = fo.FileOps(backend, journal, scope=lambda: self.scope(fresh=True),
                         cancel=cancel.is_set if cancel is not None else None, clock=self._clock, progress=heard,
                         recycler=self.recycler())
        with stopkeys.held(stop_keys, on_stop or (lambda: None)) as esc:
            if on_start is not None:
                on_start(esc)
            code, report = cardplan.carry_out(plan, ops, now=self._now(), mono=self._clock(), sha=sha,
                                              ticked=ticked)
        if code or report is None:
            return tidy.CardOutcome(run_id, code or tidy.UNAVAILABLE, esc=esc)
        return tidy.CardOutcome(run_id, "", tidy.ends_from(numbers, report.results),
                                round(self._clock() - started, 3), esc)

    def undo_card(self, run_id: str, seqs: Sequence[int], *, cancel: Any = None,
                  on_start: Callable[[bool], None] | None = None, stop_keys: stopkeys.StopKeys | None = None,
                  on_stop: Callable[[], None] | None = None) -> tidy.UndoOutcome:
        """The owner's Undo on a card's stamp: the run's items ``seqs`` put back through fileops.Undoer - newest first,
        one at a time, each re-checked (moved since, something new in its old place, changed, too old: left as it
        is, in the card's words) - with Esc held meanwhile, as for the batch. Jarvis's own copies and zips go to the
        Recycle Bin (unchanged only), what it binned comes back out of it, and a part-made copy is binned (P2.6)."""
        started = self._clock()
        if cancel is not None and cancel.is_set():
            return tidy.UndoOutcome(run_id, code=tidy.STOPPED_FIRST)
        backend = self.backend()
        journal = self._journal(run_id) if backend is not None else None
        if backend is None or journal is None:
            return tidy.UndoOutcome(run_id, code=tidy.UNAVAILABLE)
        undoer = fo.Undoer(backend, journal, scope=lambda: self.scope(fresh=True), undo_days=self._undo_days,
                           cancel=cancel.is_set if cancel is not None else None, clock=self._clock,
                           recycler=self.recycler())
        with stopkeys.held(stop_keys, on_stop or (lambda: None)) as esc:
            if on_start is not None:
                on_start(esc)
            report = undoer.undo(list(seqs))
        rows = tuple(tidy.RowEnd(0, result.state, result.reason, result.seq) for result in report.results)
        return tidy.UndoOutcome(run_id, rows, report.stamp(), round(self._clock() - started, 3))

    def recall_undo(self, *, cancel: Any = None) -> Recalled:
        """The owner's "undo" (p2r-3): the newest run - by its newest change - whose items Undo may still put back
        (within undo_days: fileops.Undoer.undoable, the card's own Undo's rule), rebuilt from its journal as its
        stamped card (tidy.recalled). Only reads the journals: nothing is put back here. ``Recalled(None)``: nothing
        to undo; ``code`` tidy.UNAVAILABLE: the journals or this PC can't be read now."""
        if cancel is not None and cancel.is_set():
            return Recalled()
        backend = self.backend()
        try:
            folder = self._journal_folder()
        except Exception as exc:  # noqa: BLE001 - RealBackendInTests in a test without a folder of its own
            logger.info("Desktop: the undo route can't read the journals (%s)", type(exc).__name__)
            return Recalled(code=tidy.UNAVAILABLE)
        if backend is None:
            return Recalled(code=tidy.UNAVAILABLE)
        best: tuple[float, str, dict, tuple[int, ...]] | None = None
        for path in jn.journal_files(folder):
            if cancel is not None and cancel.is_set():
                return Recalled()
            run_id = path.name[:-len(jn.JOURNAL_SUFFIX)]
            journal = Journal(folder, run_id)
            seqs = fo.Undoer(backend, journal, scope=lambda: self.scope(fresh=True),
                             undo_days=self._undo_days).undoable()
            if not seqs:
                continue
            items = journal.items()
            newest = max(float(items[seq].done.at or 0.0) for seq in seqs if items[seq].done is not None)
            if best is None or newest > best[0]:
                best = (newest, run_id, items, seqs)
        if best is None:
            logger.info("Desktop: the undo route found nothing to undo")
            return Recalled()
        state = tidy.recalled(best[1], best[2], self.scope(), best[3])
        logger.info("Desktop %s: the undo route shows its card (%d item(s) to put back)", best[1], len(best[3]))
        return Recalled(state)

    # ---- a task's reads (agent.owner; part A4) ------------------------------------------------------------

    def find_for_task(self, words: str, *, limit: int = MAX_FILES, cancel: Any = None) -> DesktopContext:
        """The files ``words`` find (names only, each checked by the path check), the places they are in, and the apps
        the words name. Unlike ``context_for``, nothing that the words did not ask for: no Recent items, no other
        folders."""
        backend = self.backend()
        if backend is None or (cancel is not None and cancel.is_set()):
            return DesktopContext()
        scope = self.scope()
        cache: dict[str, bool] = {}
        searched = query_words(words)
        search = LocalSearch(scope, backend, clock=self._clock, now=self._now, cache=cache)
        result = search.search(searched, limit=limit)
        if cancel is not None and cancel.is_set():
            return DesktopContext()
        places = pick_places(scope, backend, self.tree(), words=searched, files=result.files, cap=MAX_PLACES,
                             cache=cache, newest=False)
        return build_context(places, result.files, desktop_apps.match(words, self.apps()), self._sites,
                             looked_in=result.looked_in, found=result.found)

    def list_for_task(self, place: Place | None, *, cap: int = 150, cancel: Any = None) -> TaskListing:
        """What ``place`` (a Place listed in this run, re-checked; None: the roots) holds right now: folders first,
        every item through the path check, links, hidden, excluded and secret-shaped ones never listed, an online-only
        folder never gone into (it would download)."""
        executor = self.executor()
        if executor is None:
            return TaskListing(READ_UNAVAILABLE)
        if cancel is not None and cancel.is_set():
            return TaskListing(READ_STOPPED)
        backend, scope = executor.backend, self.scope(fresh=True)
        if place is None:
            return TaskListing(places=tuple(pick_places(scope, backend, (), cap=len(scope.roots), newest=False)))
        code, verdict = executor.check_place(place)
        if code or verdict is None:
            return TaskListing(code or READ_UNAVAILABLE)
        if paths.FLAG_ONLINE_ONLY in verdict.flags:
            return TaskListing(READ_ONLINE)
        try:
            entries = backend.list_folder(verdict.path, limit=LIST_ENTRIES_CAP)
        except OSError:
            return TaskListing(READ_FAILED)
        cache: dict[str, bool] = {}
        places: list[Place] = []
        files: list[Any] = []
        left_out = 0
        for entry in sorted((item for item in entries if not skipped(item)),
                            key=lambda item: (not item.is_dir, -item.mtime_ns)):
            if cancel is not None and cancel.is_set():
                return TaskListing(READ_STOPPED)
            if len(places) + len(files) >= cap:
                left_out += 1
                continue
            child = paths.check_path(ntpath.join(verdict.path, entry.name), scope, backend, cache=cache)
            if entry.is_dir:
                found = place_item(child, (*place.parts, entry.name), backend) if child.ok else None
                places.extend([found] if found is not None else [])
            else:
                found = file_item(child, backend) if child.ok else None
                files.extend([found] if found is not None else [])
        return TaskListing("", tuple(places), tuple(files), left_out)

    def _in_secret_folder(self, path: str) -> bool:
        """A folder between its root and the file is named for secrets ("Documents\\Passwords\\gmail.txt"). Only below
        the root: where the owner's folders themselves lie says nothing."""
        root = self.scope().root_of(path)
        if root is None:
            return False
        try:
            inside = ntpath.relpath(path, root.path)
        except ValueError:
            return False
        return any(guard.sensitive_folder_name(part) for part in inside.split("\\")[:-1])

    def in_secret_folders(self, listed: Sequence[str], *, cancel: Any = None) -> frozenset[str]:
        """Which of these listed paths lie in a folder named for secrets: a task can list them, and read none of
        them."""
        if cancel is not None and cancel.is_set():
            return frozenset(listed)
        return frozenset(path for path in listed if self._in_secret_folder(path))

    def read_text(self, item: Any, *, start: int = 0, cap: int = 12_000, cancel: Any = None) -> TextPart:
        """Characters ``start`` on of a listed text file, ``cap`` at most. Right before the read it is re-checked in the
        scope as it is now (the same final path, the same identity: executor.check_file), it is a type Jarvis reads as
        text, neither its name nor a folder of it below the root says secrets, and its content is on this PC; the handle
        read from must be that same item (backend.read_head). A part that plainly holds a key is refused too
        (guard.holds_secret), with a margin either side, so a key cut by the part's end is still seen. ``cancel``, once
        set: nothing is read."""
        executor = self.executor()
        if executor is None:
            return TextPart(READ_UNAVAILABLE)
        if cancel is not None and cancel.is_set():
            return TextPart(READ_STOPPED)
        code, verdict = executor.check_file(item)
        if code or verdict is None:
            return TextPart(code or READ_UNAVAILABLE)
        name = ntpath.basename(verdict.path)
        if guard.sensitive_file_name(name) or self._in_secret_folder(verdict.path):
            return TextPart(READ_SECRET)
        if guard.suffix(name) not in READABLE_TYPES:
            return TextPart(READ_TYPE)
        if paths.FLAG_ONLINE_ONLY in verdict.flags or getattr(item, "online_only", False):
            return TextPart(READ_ONLINE)
        if cancel is not None and cancel.is_set():
            return TextPart(READ_STOPPED)
        try:
            raw = executor.backend.read_head(verdict.path, max_bytes=READ_BYTES_CAP + 1, identity=item.identity)
        except OSError as exc:
            logger.info("Desktop: a file could not be read for a task (%s %s)", type(exc).__name__,
                        getattr(exc, "winerror", "") or "")
            return TextPart(READ_FAILED)
        whole = decode_text(raw[:READ_BYTES_CAP], cut=len(raw) > READ_BYTES_CAP)
        if whole is None:
            return TextPart(READ_BINARY)
        first = max(0, int(start))
        part = whole[first:first + int(cap)]
        if guard.holds_secret(whole[max(0, first - SECRET_MARGIN):first + int(cap) + SECRET_MARGIN]):
            logger.info("Desktop: a file was not read for a task: it holds what looks like a key")
            return TextPart(READ_SECRET)
        return TextPart("", part, first, first + len(part), first + len(part) < len(whole), len(raw) > READ_BYTES_CAP,
                        guard.hidden_message(part))

    def pc_for_task(self, asked: pcctl.PcAsk, *, words: str = "", run_id: str = "", cancel: Any = None,
                    hud: Sequence[int] = ()) -> DesktopRun:
        """A PC control a task asked for, once its licence was read from the owner's words (agent.owner): carried out as
        a local route is. Its chips and Undo are desktop_ui's, as for any."""
        executor = self.executor()
        if executor is None:
            return DesktopRun(run_id)
        route = desktop_routes.LocalRoute(asked.kind, dv.INSTANT, target=asked)
        return self._shown(executor.run_route(route, words=words, run_id=run_id, cancel=cancel, hud=hud), executor,
                           words)

    def find(self, query: FindQuery) -> tuple[Any, ...]:
        """A find_files step's search: the files (or, for kind "folder", the folders) its words find, checked."""
        backend = self.backend()
        if backend is None:
            return ()
        scope = self.scope()
        if query.kind == "folder":
            places = pick_places(scope, backend, self.tree(), words=query.words, cap=MAX_PLACES)
            return tuple(place for place in places
                         if place.depth and name_score(place.parts[-1] if place.parts else "", query.words))
        search = LocalSearch(scope, backend, clock=self._clock, now=self._now)
        return search.search(query.words, folders=query.folders, kind=query.kind,
                             within_days=query.within_days).files


@dataclass(frozen=True)
class Recalled:
    """What the owner's "undo" found (``DesktopService.recall_undo``): the stamped card's state (tidy.TidyState), or
    None - nothing to undo, or ``code`` tidy.UNAVAILABLE."""

    state: Any = field(default=None, repr=False)
    code: str = ""


@dataclass(frozen=True)
class Housekept:
    """What the journal's housekeeping came to (``housekeep``): whether it ran, the items recovery corrected and the
    runs purged."""

    ran: bool = False
    corrected: int = 0
    purged: int = 0


def housekeep(journal_folder: Callable[[], Any], backend: Callable[[], DesktopBackend | None], *,
              cancel: Any = None) -> Housekept:
    """The journal's housekeeping (SPEC 6.1; p2r-1), on the desktop thread: start-up recovery - every item a crash left
    "doing" corrected by looking where it is, never redone (journal.recover only adds records) - then the purge of
    the runs older than journal.PURGE_DAYS. ``journal_folder()``: the journals' folder (the app's, a test's own);
    ``backend()``: this PC's backend for the looks (none: recovery waits for a later run); ``cancel``: THE CONSENT GATE
    (set: nothing runs). Logs carry counts and exception types only."""
    if cancel is not None and cancel.is_set():
        return Housekept()
    try:
        folder = journal_folder()
    except Exception as exc:  # noqa: BLE001 - RealBackendInTests in a test without a folder of its own
        logger.info("Desktop journal: no housekeeping (%s)", type(exc).__name__)
        return Housekept()
    corrected = purged = 0
    try:
        looks = backend()
    except Exception as exc:  # noqa: BLE001 - no backend: nothing is looked at now
        logger.info("Desktop journal: no recovery now (%s)", type(exc).__name__)
        looks = None
    try:
        if looks is not None:
            corrected = jn.recover(folder, looks).items
        purged = jn.purge(folder)
    except Exception as exc:  # noqa: BLE001 - a bug: logged by its type, tried again at the next housekeeping
        logger.warning("Desktop journal: the housekeeping stopped (%s)", type(exc).__name__)
    return Housekept(True, corrected, purged)


class _LazyRecycler:
    """The Recycle Bin handed to the PC's controls (a screenshot's Undo), made only when one is first needed."""

    def __init__(self, make: Callable[[], Any]) -> None:
        self._make = make

    def check(self, path: str, *, size: int) -> str:
        return self._make().check(path, size=size)

    def recycle(self, path: str, identity: Any = None) -> None:
        self._make().recycle(path, identity)

    def find(self, original: str, *, size: int, is_dir: bool, since: float, until: float) -> Any:
        return self._make().find(original, size=size, is_dir=is_dir, since=since, until=until)

    def restore(self, entry: Any, original: str) -> None:
        self._make().restore(entry, original)
