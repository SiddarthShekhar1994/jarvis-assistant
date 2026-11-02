"""The Recycle Bin (Jarvis on your desktop, P2.6; SPEC sections 2.1, 2.4, 5.4, 6.2, 6.3 and 17; the owner's decision
"never permanent delete: the Recycle Bin plus Undo only"; hard rules 5, 6, 12, 14 and 16; no Qt).

Nothing Jarvis does deletes a file for good. Putting something in the Recycle Bin - a recycle_files card row the
owner's own words asked for and his click carries out, or an Undo of Jarvis's own copy, zip, screenshot or part-made
copy - goes through ``RecycleBin.recycle``, in this order, every time:

1. the path's text: a plain absolute local path (paths.lexical_problem), never a place Jarvis never touches
   (paths.static_problem: a network or device path, AppData, $Recycle.Bin, a code or secret folder ...), never a
   secret-shaped file;
2. the drive FIRST, before anything on the path is asked (the verified review note): a fixed local drive only - a
   removable or network drive has no Recycle Bin (``fileops.R_NO_BIN_HERE``; SPEC 2.4 never recycles where no bin
   exists);
3. the item: never a link, never online-only - a OneDrive file whose content is in the cloud would skip the bin
   (``fileops.R_ONLINE_ONLY``) -, the same item it was (``identity``: volume, file id, size, mtime), and a folder
   walked first (fileops.walk_subtree: never one holding a scope place, a code marker, a secret folder or file, an
   excluded name, a link or an online-only entry, never one the walk can't finish);
4. the bin of that drive: on (its BitBucket NukeOnDelete, and Windows' NoRecycleFiles policy - both read only), its
   size known (MaxCapacity) and room in it for the item (SHQueryRecycleBin): an item bigger than the room left is
   refused (``fileops.R_BIN_FULL``) - Windows would delete it for good, or push older items out of the bin for it;
5. the identity once more, right before the call;
6. ONE call (``BinSeam.recycle_item``): IFileOperation.DeleteItem with ``RECYCLE_FLAGS`` - FOFX_RECYCLEONDELETE
   (defined here by value), FOF_ALLOWUNDO and FOF_WANTNUKEWARNING (guard.recycle_flags_ok) and FOF_NOERRORUI, never
   FOF_NOCONFIRMATION -, one item per call, with a progress sink: PreDeleteItem without
   TSF_DELETE_RECYCLE_IF_POSSIBLE stops it before anything is deleted (``fileops.R_WOULD_NUKE``), and PostDeleteItem
   with no new item in the bin (psiNewlyCreated NULL) means it was NOT recycled (``fileops.R_NOT_RECYCLED``); both
   stop the batch (fileops.BIN_STOPS). ``decide`` reads the sink's report (pure).

``RecycleBin.check(path, size=)`` makes steps 1, 2 and 4 for a card row before the card shows (nothing changes).

Undo of an item Jarvis binned (SPEC 6.3; fileops.Undoer checks its old folder and that its old place is free first):
``find`` looks for it in the bin by its old path, when it was binned and its size (the bin's IShellFolder2 details
System.Recycle.DeletedFrom and System.Recycle.DateDeleted, and System.Size) - None when the bin was emptied - and
``restore`` asks the bin itself to put it back (its "undelete" verb), confirmed at its old place
(``fileops.R_NOT_RESTORED`` when it isn't).

``BinSeam`` is what the Windows side offers; ``Win32RecycleBin`` is the real one (pywin32: the shell's
IFileOperation, IShellFolder2 and IContextMenu; winreg read only; ctypes for the volume's name) - constructing it,
and every one of its public methods, raises backend.RealBackendInTests while the test guard is on: the tests use
tests.desktop_fakes.FakeRecycleBin. Nothing here empties the bin, deletes anything for good or reads a file's
content. Logs carry kinds, reason codes, counts, HRESULTs and exception types only - never a path or a name.

Public API
==========
``RECYCLE_FLAGS``, ``FOF_NOERRORUI``, ``TSF_DELETE_RECYCLE_IF_POSSIBLE``, ``UNDELETE_VERB``, ``MAX_BIN_ENTRIES``,
``RESTORE_WAIT_S``; outcomes ``RECYCLED``, ``WOULD_NUKE``, ``NOT_RECYCLED``, ``CANCELLED``, ``FAILED``;
``BinState(on, capacity, used, items)`` (``room``), ``BinEntry(folder, name, deleted_at, size, key)``
(``original``), ``SinkReport(pre_flags, post_hr, in_bin, perform_hr, aborted)``, ``decide(report, still_there)``,
``win32_code(hresult)``; ``BinSeam`` (``bin_state(drive)``, ``recycle_item(path)``, ``bin_entries(name)``,
``restore_entry(entry, original)``); ``RecycleBin(backend, seam, scope=, caps=, clock=)`` (``check``, ``recycle``,
``find``, ``restore``: desktop.fileops' Recycler); ``Win32RecycleBin()``.
"""

from __future__ import annotations

import logging
import ntpath
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import fileops as fo
from . import guard, paths
from .backend import (DRIVE_FIXED, FILE_ATTRIBUTE_OFFLINE, FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
                      FILE_ATTRIBUTE_RECALL_ON_OPEN, DesktopBackend, FileIdentity, RealBackendInTests, _com_ready,
                      _real, cloud_tag, real_backend_blocked)

logger = logging.getLogger(__name__)

FOF_NOERRORUI = 0x0400                   # Windows' own error boxes left out (Jarvis says it in words)
# The one set of flags a recycle is ever asked with: the bin only, with Undo and Windows' own warning before anything
# would be deleted for good - never FOF_NOCONFIRMATION (guard.recycle_flags_ok refuses it).
RECYCLE_FLAGS = guard.FOFX_RECYCLEONDELETE | guard.FOF_ALLOWUNDO | guard.FOF_WANTNUKEWARNING | FOF_NOERRORUI
TSF_DELETE_RECYCLE_IF_POSSIBLE = 0x80    # PreDeleteItem's flag: the item goes to the bin (TRANSFER_SOURCE_FLAGS)
UNDELETE_VERB = "undelete"               # the bin's own Restore (a canonical verb of its items' menu)
MAX_BIN_ENTRIES = 10_000                 # bin items looked at, at most, when looking for one
RESTORE_WAIT_S = 2.0                     # how long a restore is waited for at its old place
SW_SHOWNORMAL = 1
_CMF_NORMAL = 0
_E_ABORT = 0x80004004
_COPYENGINE_E_USER_CANCELLED = 0x80270000
_FACILITY_WIN32 = 7
_ONLINE = FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_OFFLINE
_MB = 1024 * 1024
# The bin's own settings, read only: per volume under HKEY_CURRENT_USER, and Windows' policy (current user, then
# the machine) that turns the bin off everywhere.
BITBUCKET_KEY = "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\BitBucket\\Volume"
POLICY_KEY = "Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer"
# The bin's details of an item (shell property keys: PSGUID_DISPLACED's DeletedFrom and DateDeleted, and Size).
_DISPLACED = "{9B174B33-40FF-11D2-A27E-00C04FC30871}"
_PKEY_DELETED_FROM = (_DISPLACED, 2)
_PKEY_DATE_DELETED = (_DISPLACED, 3)
_PKEY_SIZE = ("{B725F130-47EF-101A-A5F1-02608C9EEBAC}", 12)

RECYCLED, WOULD_NUKE, NOT_RECYCLED, CANCELLED, FAILED = ("recycled", "would_delete", "not_recycled", "cancelled",
                                                         "failed")


# --------------------------------------------------------------------------
# What the Windows side reports
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BinState:
    """A drive's Recycle Bin: ``on`` (False: off for that drive, or by Windows' policy), ``capacity`` its size in bytes
    (None: not known - never guessed), ``used`` the bytes in it now and how many ``items``."""

    on: bool
    capacity: int | None = None
    used: int = 0
    items: int = 0

    @property
    def room(self) -> int:
        """The bytes the bin can take now without pushing anything out (0 when its size is not known)."""
        return max(0, int(self.capacity) - max(0, int(self.used))) if self.capacity is not None else 0


@dataclass(frozen=True)
class BinEntry:
    """One item in the Recycle Bin as the bin lists it: the folder it was binned from (System.Recycle.DeletedFrom),
    its name there, when (seconds since the epoch; System.Recycle.DateDeleted), its size (-1 unknown) and the bin's
    own handle on it (``key``: its item id there)."""

    folder: str = field(repr=False)
    name: str = field(repr=False)
    deleted_at: float = -1.0
    size: int = -1
    key: Any = field(default=None, repr=False, compare=False)

    @property
    def original(self) -> str:
        return self.folder.rstrip("\\") + "\\" + self.name

    def __repr__(self) -> str:                      # never a name or a path
        return f"BinEntry(size={self.size})"


@dataclass(frozen=True)
class SinkReport:
    """What IFileOperation's progress sink heard about its one item: PreDeleteItem's flags (None: it never came),
    PostDeleteItem's HRESULT (None: it never came) and whether a new item was in the bin then (psiNewlyCreated not
    NULL); PerformOperations' own HRESULT (0: it returned) and GetAnyOperationsAborted."""

    pre_flags: int | None = None
    post_hr: int | None = None
    in_bin: bool | None = None
    perform_hr: int = 0
    aborted: bool = False


def win32_code(hresult: int) -> int:
    """The Windows error code inside an HRESULT_FROM_WIN32 (0x80070020 -> 32); 0 for any other HRESULT."""
    value = int(hresult) & 0xFFFFFFFF
    return value & 0xFFFF if value & 0x80000000 and (value >> 16) & 0x1FFF == _FACILITY_WIN32 else 0


def _failed(hresult: int | None) -> bool:
    return hresult is not None and bool(int(hresult) & 0x80000000)


def decide(report: SinkReport, still_there: bool) -> tuple[str, int]:
    """The verdict on one recycle (pure): (outcome, winerror). ``still_there``: the item is still at its path after
    the call. RECYCLED only when PostDeleteItem said so (no failure, a new item in the bin); WOULD_NUKE when
    PreDeleteItem came without TSF_DELETE_RECYCLE_IF_POSSIBLE and the item is still there (the
    sink stopped it first); NOT_RECYCLED when it was deleted without going to the bin, or is gone with nothing to say
    it went there (never taken for recycled); CANCELLED when the owner said no to Windows' own question; FAILED (with
    the Windows error) when Windows refused and it is still there."""
    if report.pre_flags is not None and not int(report.pre_flags) & TSF_DELETE_RECYCLE_IF_POSSIBLE:
        return (WOULD_NUKE, 0) if still_there else (NOT_RECYCLED, 0)
    if report.post_hr is not None:
        if _failed(report.post_hr):
            return (FAILED, win32_code(report.post_hr)) if still_there else (NOT_RECYCLED, 0)
        if not report.in_bin:
            return NOT_RECYCLED, 0                       # psiNewlyCreated NULL: deleted, not recycled
        return RECYCLED, 0                               # in the bin (anything at the path now is something new)
    if not still_there:
        return NOT_RECYCLED, 0                           # gone, and nothing said it went to the bin
    perform = int(report.perform_hr) & 0xFFFFFFFF
    if perform in (_COPYENGINE_E_USER_CANCELLED, _E_ABORT) or (report.aborted and not _failed(perform)):
        return CANCELLED, 0
    return FAILED, win32_code(perform)


# --------------------------------------------------------------------------
# The Windows side's seam
# --------------------------------------------------------------------------

class BinSeam(Protocol):
    def bin_state(self, drive: str) -> BinState:
        """The Recycle Bin of ``drive`` ("C:\\") as Windows has it now (read only)."""
        ...

    def recycle_item(self, path: str) -> SinkReport:
        """ONE IFileOperation with RECYCLE_FLAGS for ``path`` and its progress sink's report."""
        ...

    def bin_entries(self, name: str) -> list[BinEntry]:
        """The bin's items called ``name`` (or ``name`` without its extension, as the bin may show it)."""
        ...

    def restore_entry(self, entry: BinEntry, original: str) -> None:
        """The bin's own restore of ``entry`` (its "undelete" verb), waited for at ``original`` a while."""
        ...


# --------------------------------------------------------------------------
# The Recycle Bin, as Jarvis uses it (desktop.fileops' Recycler)
# --------------------------------------------------------------------------

def _root(path: str) -> str:
    return path.replace("/", "\\")[:3]


class RecycleBin:
    """The Recycle Bin over ``backend`` (a DesktopBackend: the drive, the item's facts, a folder's walk) and ``seam``
    (the Windows side: Win32RecycleBin in the app, tests.desktop_fakes.FakeRecycleBin in the tests). ``scope``: the
    scope as it is now (paths.Scope), for a folder's walk (none: only what the folder holds counts). See the module
    docstring."""

    def __init__(self, backend: DesktopBackend, seam: BinSeam, *, scope: Callable[[], paths.Scope] | None = None,
                 caps: fo.SubtreeCaps = fo.SUBTREE_CAPS, clock: Callable[[], float] = time.monotonic) -> None:
        self.backend = backend
        self.seam = seam
        self._scope = scope or paths.Scope
        self._caps = caps
        self._clock = clock

    def __repr__(self) -> str:
        return "RecycleBin()"

    # ---- the pre-checks ---------------------------------------------------------------------------------------

    @staticmethod
    def _text(path: Any) -> str:
        if not isinstance(path, str) or paths.lexical_problem(path):
            return fo.R_OUTSIDE
        problem = paths.static_problem(path)
        if problem:
            return problem
        return fo.R_EXCLUDED if guard.secret_file(path) else ""

    def _drive(self, path: str) -> str:
        """A fixed local drive (asked before anything on the path is): removable and network drives have no bin."""
        try:
            fixed = self.backend.drive_type(_root(path)) == DRIVE_FIXED
        except OSError:
            fixed = False
        return "" if fixed else fo.R_NO_BIN_HERE

    def _room(self, path: str, size: int) -> str:
        """The bin of the item's drive: on, its size known and room in it for ``size`` bytes."""
        try:
            state = self.seam.bin_state(_root(path))
        except Exception as exc:  # noqa: BLE001 - a bin that can't be read takes nothing
            logger.info("Desktop bin: its state could not be read (%s %s)", type(exc).__name__,
                        getattr(exc, "winerror", "") or "")
            return fo.R_NO_BIN
        if not isinstance(state, BinState) or not state.on:
            return fo.R_BIN_OFF
        if state.capacity is None:
            return fo.R_BIN_UNKNOWN
        return fo.R_BIN_FULL if max(0, int(size)) > state.room else ""

    def _item(self, path: str, identity: FileIdentity | None) -> tuple[str, int]:
        """The item now: (reason, its size in bytes) - never a link, never online-only, the same item when
        ``identity`` is given, a folder walked (its files' bytes)."""
        try:
            info = self.backend.file_info(path)
            now = self.backend.file_id(path)
        except OSError:
            return fo.R_CHANGED, 0
        if info.reparse_tag and not cloud_tag(info.reparse_tag):
            return fo.R_EXCLUDED, 0                      # a link: never followed, never binned
        if int(info.attributes) & _ONLINE:
            return fo.R_ONLINE_ONLY, 0                   # its content is in the cloud: it would skip the bin
        if not isinstance(now, FileIdentity) or (identity is not None and not identity.same_item(now)):
            return fo.R_CHANGED, 0
        if not now.is_dir:
            return "", max(0, int(now.size))
        walk = fo.walk_subtree(path, self._scope(), self.backend, content=True, caps=self._caps, clock=self._clock)
        if walk.reason:
            return (fo.R_ONLINE_ONLY if walk.reason == fo.R_HOLDS_ONLINE_ONLY else walk.reason), 0
        return "", walk.bytes

    def _same(self, path: str, identity: FileIdentity) -> bool:
        try:
            return identity.same_item(self.backend.file_id(path))
        except OSError:
            return False

    def _there(self, path: str) -> bool:
        try:
            return bool(self.backend.exists(path))
        except OSError:
            return True                                  # can't tell: never taken for gone

    # ---- desktop.fileops' Recycler ----------------------------------------------------------------------------

    def check(self, path: str, *, size: int) -> str:
        """The bin's own pre-checks for a card row (the text, the drive, the bin on and room for ``size`` bytes):
        "" when it can take it. Nothing is changed and the item's own facts are the caller's (fileops)."""
        return self._text(path) or self._drive(path) or self._room(path, size)

    def recycle(self, path: str, identity: FileIdentity | None = None) -> None:
        """``path`` into the Recycle Bin - every pre-check first, then the one call and its sink's verdict (see the
        module docstring). RecycleUnavailable with the reason when it didn't go (``stop`` for BIN_STOPS); OSError
        when Windows refused (in use, access ...)."""
        reason = self._text(path) or self._drive(path)
        if not reason:
            reason, size = self._item(path, identity)
            if not reason:
                reason = self._room(path, size)
        if reason:
            logger.info("Desktop bin: not binned (%s)", reason)
            raise fo.RecycleUnavailable(reason)
        if identity is None:
            try:
                identity = self.backend.file_id(path)
            except OSError:
                raise fo.RecycleUnavailable(fo.R_CHANGED) from None
        if not self._same(path, identity):               # once more, right before the call
            raise fo.RecycleUnavailable(fo.R_CHANGED)
        try:
            report = self.seam.recycle_item(path)
        except OSError as exc:
            if self._there(path):
                raise
            logger.warning("Desktop bin: the call failed and the item is gone (%s %s)", type(exc).__name__,
                           getattr(exc, "winerror", "") or "")
            raise fo.RecycleUnavailable(fo.R_NOT_RECYCLED) from None
        except Exception as exc:  # noqa: BLE001 - the bin's call broke: only its verdict counts
            if self._there(path):
                logger.info("Desktop bin: the call could not be made (%s)", type(exc).__name__)
                raise fo.RecycleUnavailable(fo.R_NO_BIN) from None
            logger.warning("Desktop bin: the call broke and the item is gone (%s)", type(exc).__name__)
            raise fo.RecycleUnavailable(fo.R_NOT_RECYCLED) from None
        outcome, winerror = decide(report, self._there(path))
        if outcome == RECYCLED:
            logger.info("Desktop bin: binned 1 item")
            return
        logger.warning("Desktop bin: not binned (%s; post %s, pre %s, perform %s)", outcome,
                       hex(report.post_hr) if report.post_hr is not None else "-",
                       hex(report.pre_flags) if report.pre_flags is not None else "-", hex(report.perform_hr))
        if outcome == WOULD_NUKE:
            raise fo.RecycleUnavailable(fo.R_WOULD_NUKE)
        if outcome == NOT_RECYCLED:
            raise fo.RecycleUnavailable(fo.R_NOT_RECYCLED)
        if outcome == CANCELLED:
            raise fo.RecycleUnavailable(fo.R_STOPPED)
        raise OSError(0, "Windows did not put it in the Recycle Bin", None, winerror)

    def find(self, original: str, *, size: int, is_dir: bool, since: float, until: float) -> BinEntry | None:
        """The item binned from ``original`` between ``since`` and ``until`` (its size the same, for a file), as the
        bin lists it now - the one binned nearest the middle of that time; None when the bin doesn't have it."""
        if self._text(original):
            return None
        folder, name = ntpath.dirname(original), ntpath.basename(original)
        names = {name.casefold(), ntpath.splitext(name)[0].casefold()}
        middle = (float(since) + float(until)) / 2
        try:
            listed = list(self.seam.bin_entries(name))
        except Exception as exc:  # noqa: BLE001 - a bin that can't be read gives nothing back now
            logger.info("Desktop bin: its items could not be read (%s %s)", type(exc).__name__,
                        getattr(exc, "winerror", "") or "")
            raise fo.RecycleUnavailable(fo.R_NO_BIN) from None
        found: list[BinEntry] = []
        for entry in listed:
            if not isinstance(entry, BinEntry) or ntpath.normcase(entry.folder.rstrip("\\")) != ntpath.normcase(
                    folder.rstrip("\\")) or entry.name.casefold() not in names:
                continue
            if not since <= entry.deleted_at <= until:
                continue
            if not is_dir and entry.size >= 0 and entry.size != size:
                continue
            found.append(entry)
        if not found:
            logger.info("Desktop bin: the item is not in the bin")
            return None
        return min(found, key=lambda entry: abs(entry.deleted_at - middle))

    def restore(self, entry: BinEntry, original: str) -> None:
        """The bin's own restore of ``entry`` to ``original`` (its text and drive checked first), confirmed there:
        RecycleUnavailable(R_NOT_RESTORED) when it isn't back."""
        reason = self._text(original) or self._drive(original)
        if reason:
            raise fo.RecycleUnavailable(reason)
        failure: Exception | None = None
        try:
            self.seam.restore_entry(entry, original)
        except Exception as exc:  # noqa: BLE001 - only whether it is back counts
            failure = exc
        try:
            back = bool(self.backend.exists(original))
        except OSError:
            back = False
        if back:
            logger.info("Desktop bin: restored 1 item")
            return
        logger.info("Desktop bin: the restore did not bring it back (%s)", type(failure).__name__ if failure else "-")
        if isinstance(failure, OSError):
            raise failure
        raise fo.RecycleUnavailable(fo.R_NOT_RESTORED)


# --------------------------------------------------------------------------
# The real Windows side (only ever made in the app; never while the tests run)
# --------------------------------------------------------------------------

class Win32RecycleBin:
    """The Recycle Bin's Windows side (see the module docstring). Constructing it, and every public method, raises
    RealBackendInTests while the test guard is on."""

    def __init__(self) -> None:
        if real_backend_blocked():
            raise RealBackendInTests("the real Recycle Bin is never reached while the tests run")

    @_real
    def bin_state(self, drive: str) -> BinState:
        root = _root(drive)
        if len(root) != 3 or not root[0].isalpha() or not root[0].isascii() or root[1:] != ":\\":
            raise ValueError("refused by the last guard")
        from win32com.shell import shell

        nuke, capacity_mb = _bin_settings(_volume_guid(root))
        used, items = shell.SHQueryRecycleBin(root)
        on = not _policy_off() and not nuke
        return BinState(on, int(capacity_mb) * _MB if capacity_mb else None, max(0, int(used)), max(0, int(items)))

    @_real
    def recycle_item(self, path: str) -> SinkReport:
        if paths.lexical_problem(path) or not guard.recycle_flags_ok(RECYCLE_FLAGS) \
                or RECYCLE_FLAGS & guard.FOF_NOCONFIRMATION:
            raise ValueError("refused by the last guard")
        _com_ready()
        import pythoncom
        from win32com.shell import shell

        recorder, sink = _progress_sink()
        try:
            operation = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_ALL,
                                                   shell.IID_IFileOperation)
            operation.SetOperationFlags(RECYCLE_FLAGS)
            item = shell.SHCreateItemFromParsingName(path, None, shell.IID_IShellItem)
        except pythoncom.com_error as exc:
            raise OSError(0, "the Recycle Bin's call could not be made", None,
                          win32_code(getattr(exc, "hresult", 0) or 0)) from None
        cookie = operation.Advise(sink)
        perform = 0
        try:
            operation.DeleteItem(item)
            try:
                operation.PerformOperations()
            except pythoncom.com_error as exc:
                perform = int(getattr(exc, "hresult", 0) or 0) & 0xFFFFFFFF
            try:
                aborted = bool(operation.GetAnyOperationsAborted())
            except pythoncom.com_error:
                aborted = True
        finally:
            operation.Unadvise(cookie)
        return SinkReport(recorder.pre_flags, recorder.post_hr, recorder.in_bin, perform, aborted)

    @_real
    def bin_entries(self, name: str) -> list[BinEntry]:
        _com_ready()
        import pythoncom
        from win32com.shell import shellcon

        folder = _bin_folder()
        wanted = {name.casefold(), ntpath.splitext(name)[0].casefold()}
        flags = shellcon.SHCONTF_FOLDERS | shellcon.SHCONTF_NONFOLDERS | shellcon.SHCONTF_INCLUDEHIDDEN
        children = folder.EnumObjects(0, flags)
        found: list[BinEntry] = []
        if children is None:                             # an empty bin
            return found
        for seen, child in enumerate(children):
            if seen >= MAX_BIN_ENTRIES:
                break
            try:
                shown = folder.GetDisplayNameOf(child, shellcon.SHGDN_INFOLDER)
                if not isinstance(shown, str) or shown.casefold() not in wanted:
                    continue
                where = folder.GetDetailsEx(child, _PKEY_DELETED_FROM)
                when = folder.GetDetailsEx(child, _PKEY_DATE_DELETED)
            except pythoncom.com_error:
                continue
            try:
                size = folder.GetDetailsEx(child, _PKEY_SIZE)
            except pythoncom.com_error:
                size = -1
            if isinstance(where, str) and where:
                found.append(BinEntry(where, shown, _epoch(when), int(size) if isinstance(size, int) else -1, child))
        return found

    @_real
    def restore_entry(self, entry: BinEntry, original: str) -> None:
        if paths.lexical_problem(original) or not isinstance(entry, BinEntry) or entry.key is None:
            raise ValueError("refused by the last guard")
        _com_ready()
        import pythoncom
        import win32gui
        from win32com.shell import shell

        folder = _bin_folder()
        try:
            _inout, menu = folder.GetUIObjectOf(0, [entry.key], shell.IID_IContextMenu, 0)
            popup = win32gui.CreatePopupMenu()
            try:
                menu.QueryContextMenu(popup, 0, 1, 0x7FFF, _CMF_NORMAL)
                # fMask 0: never CMIC_MASK_FLAG_NO_UI - without its UI the bin could replace what is in the old place.
                menu.InvokeCommand((0, 0, UNDELETE_VERB, None, None, SW_SHOWNORMAL, 0, None))
            finally:
                win32gui.DestroyMenu(popup)
        except pythoncom.com_error as exc:
            raise OSError(0, "the Recycle Bin did not restore it", None,
                          win32_code(getattr(exc, "hresult", 0) or 0)) from None
        deadline = time.monotonic() + RESTORE_WAIT_S
        while not os.path.lexists(original) and time.monotonic() < deadline:
            time.sleep(0.1)


# --------------------------------------------------------------------------
# Windows helpers (only ever reached through Win32RecycleBin's guarded methods)
# --------------------------------------------------------------------------

def _volume_guid(root: str) -> str:
    """The volume's GUID ("{...}") for a drive's root ("C:\\"), from GetVolumeNameForVolumeMountPointW."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    name_of = kernel32.GetVolumeNameForVolumeMountPointW
    name_of.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    name_of.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(64)
    if not name_of(root, buffer, len(buffer)):
        raise ctypes.WinError(ctypes.get_last_error())
    text = buffer.value
    start, end = text.find("{"), text.find("}")
    if start < 0 or end < start:
        raise OSError(0, "the volume has no name")
    return text[start:end + 1]


def _dword(key: Any, value: str) -> int | None:
    import winreg

    try:
        data, kind = winreg.QueryValueEx(key, value)
    except OSError:
        return None
    return int(data) if kind == winreg.REG_DWORD and isinstance(data, int) else None


def _bin_settings(guid: str) -> tuple[int | None, int | None]:
    """The volume's bin settings, read only: (NukeOnDelete, MaxCapacity in MB); None for one that isn't there."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BITBUCKET_KEY + "\\" + guid, 0, winreg.KEY_READ) as key:
            return _dword(key, "NukeOnDelete"), _dword(key, "MaxCapacity")
    except OSError:
        return None, None


def _policy_off() -> bool:
    """Windows' "Do not move deleted files to the Recycle Bin" policy (NoRecycleFiles), read only."""
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, POLICY_KEY, 0, winreg.KEY_READ) as key:
                if _dword(key, "NoRecycleFiles"):
                    return True
        except OSError:
            continue
    return False


def _bin_folder() -> Any:
    """The Recycle Bin's shell folder (CSIDL_BITBUCKET) as an IShellFolder2."""
    from win32com.shell import shell, shellcon

    desktop = shell.SHGetDesktopFolder()
    location = shell.SHGetSpecialFolderLocation(0, shellcon.CSIDL_BITBUCKET)
    return desktop.BindToObject(location, None, shell.IID_IShellFolder2)


def _epoch(value: Any) -> float:
    """A bin date (pywintypes' datetime) as seconds since the epoch; -1 when it isn't one."""
    try:
        return float(value.timestamp())
    except Exception:  # noqa: BLE001 - no date: never matched
        return -1.0


def _progress_sink() -> tuple[Any, Any]:
    """IFileOperation's progress sink for one recycle: (what it heard, the COM object). PreDeleteItem without
    TSF_DELETE_RECYCLE_IF_POSSIBLE, and PostDeleteItem with no new item in the bin, answer E_ABORT - nothing more of
    the operation goes on."""
    import pythoncom
    from win32com.server.exception import COMException
    from win32com.server.policy import DesignatedWrapPolicy
    from win32com.shell import shell

    class Sink(DesignatedWrapPolicy):
        _com_interfaces_ = [shell.IID_IFileOperationProgressSink]
        _public_methods_ = ["StartOperations", "FinishOperations", "PreRenameItem", "PostRenameItem", "PreMoveItem",
                            "PostMoveItem", "PreCopyItem", "PostCopyItem", "PreDeleteItem", "PostDeleteItem",
                            "PreNewItem", "PostNewItem", "UpdateProgress", "ResetTimer", "PauseTimer", "ResumeTimer"]

        def __init__(self) -> None:
            self._wrap_(self)
            self.pre_flags: int | None = None
            self.post_hr: int | None = None
            self.in_bin: bool | None = None

        def PreDeleteItem(self, flags: int, _item: Any) -> None:  # noqa: N802 - COM's name
            self.pre_flags = int(flags)
            if not int(flags) & TSF_DELETE_RECYCLE_IF_POSSIBLE:
                raise COMException(hresult=_E_ABORT - 0x100000000)       # never deleted for good: stop first

        def PostDeleteItem(self, _flags: int, _item: Any, hr_delete: int, newly_created: Any) -> None:  # noqa: N802
            self.post_hr = int(hr_delete) & 0xFFFFFFFF
            self.in_bin = newly_created is not None
            if not _failed(self.post_hr) and newly_created is None:
                raise COMException(hresult=_E_ABORT - 0x100000000)       # not recycled: nothing more goes on

        def StartOperations(self) -> None:  # noqa: N802
            return None

        def FinishOperations(self, _result: int) -> None:  # noqa: N802
            return None

        def PreRenameItem(self, *_args: Any) -> None:  # noqa: N802
            return None

        def PostRenameItem(self, *_args: Any) -> None:  # noqa: N802
            return None

        def PreMoveItem(self, *_args: Any) -> None:  # noqa: N802
            return None

        def PostMoveItem(self, *_args: Any) -> None:  # noqa: N802
            return None

        def PreCopyItem(self, *_args: Any) -> None:  # noqa: N802
            return None

        def PostCopyItem(self, *_args: Any) -> None:  # noqa: N802
            return None

        def PreNewItem(self, *_args: Any) -> None:  # noqa: N802
            return None

        def PostNewItem(self, *_args: Any) -> None:  # noqa: N802
            return None

        def UpdateProgress(self, *_args: Any) -> None:  # noqa: N802
            return None

        def ResetTimer(self) -> None:  # noqa: N802
            return None

        def PauseTimer(self) -> None:  # noqa: N802
            return None

        def ResumeTimer(self) -> None:  # noqa: N802
            return None

    recorder = Sink()
    return recorder, pythoncom.WrapObject(recorder, shell.IID_IFileOperationProgressSink)
