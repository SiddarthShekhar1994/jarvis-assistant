"""Jarvis on your desktop, P2.6: THE RECYCLE BIN (SPEC 2.1, 2.4, 4.5, 5.4, 6.2, 6.3, 10.3, 15 and 16 P2.6; the owner's
decision "never permanent delete: the Recycle Bin plus Undo only"; hard rules 5, 6, 12 and 14).

Everything here runs over tests.desktop_fakes: the fake backend's pretend disk in a temporary folder and
FakeRecycleBin, the bin's Windows side over a pretend bin there - the real desktop.recycle.Win32RecycleBin is never
made, and each of its methods refuses while the tests run before anything of Windows is asked (IFileOperation,
SHQueryRecycleBin, the bin's "undelete" verb and the registry are never reached). Covered: the sink's verdict (pure);
the flags; recycled, nuked (psiNewlyCreated NULL) -> stopped and failed, deleted-for-good refused before; the bin off,
too small, of unknown size, a removable or network drive, online-only -> refused before any call; restore, restore
after the bin was emptied, restore when the old place is taken; Undo of copies, zips and part-made copies through the
bin (a changed hash: left as it is); crash recovery; intent licensing (the owner's own words only - the corpus's
hostile names never license recycle_files) and the caps; the card's words; the routes; logs never name a file.
Every name is invented.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from unittest import mock

from briefing_reader.ask import desktop_mode as dm
from briefing_reader.ask.modes import MODE_DESKTOP, ModeRequest
from briefing_reader.desktop import backend as dbk
from briefing_reader.desktop import cardplan as cp
from briefing_reader.desktop import fileops as fo
from briefing_reader.desktop import guard
from briefing_reader.desktop import journal as jn
from briefing_reader.desktop import paths, recycle
from briefing_reader.desktop import routes as dr
from briefing_reader.desktop import tidy as dt
from briefing_reader.desktop import validate as dv
from briefing_reader.desktop.context import FileItem, Place
from briefing_reader.desktop.service import DesktopService
from tests.desktop_fakes import DOCS, DOWNLOADS, LING, FakeRecycleBin, desktop_context, desktop_disk
from tests.test_desktop_injection import HOSTILE_FILES, HOSTILE_FOLDERS, corpus_names

DOT = chr(0xB7)
ELLIPSIS = chr(0x2026)
NOW = datetime(2026, 10, 28, 10, 22, tzinfo=timezone.utc)
MONO = 500.0
CANARY = "CANARY_p26"
OLD = DOWNLOADS + "\\LING essay draft (old).docx"            # on the pretend disk (desktop_disk)
SETUP = DOWNLOADS + "\\SpotifySetup.exe"
BUDGET = DOCS + "\\Budget 2026.xlsx"                           # under OneDrive
IN_USE = 0x80070020
RECYCLED_REPORT = recycle.SinkReport(pre_flags=recycle.TSF_DELETE_RECYCLE_IF_POSSIBLE, post_hr=0, in_bin=True)


class BinCase(unittest.TestCase):
    """The pretend disk, a journal and FakeRecycleBin in a temporary folder; ``bin`` is the bin's Windows side,
    ``recycler`` the app's own logic over it (desktop.recycle.RecycleBin)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.fake = desktop_disk(os.path.join(self.tmp, "disk"))
        self.journal = jn.Journal(os.path.join(self.tmp, "journal"), "bin1")
        self.bin = FakeRecycleBin(self.fake, scope=self.scope)
        self.recycler = recycle.RecycleBin(self.fake, self.bin, scope=self.scope)
        self.stop = False

    def scope(self) -> paths.Scope:
        return paths.load_scope(dbk.ROOT_FOLDERS, (), self.fake)

    def item(self, path: str) -> FileItem:
        return FileItem(path, ntpath.basename(path), identity=self.fake.identity(path))

    def place(self, path: str) -> Place:
        return Place(path, (ntpath.basename(path),), identity=self.fake.identity(path))

    def there(self, path: str) -> bool:
        return os.path.lexists(self.fake.real(path))

    def ops(self) -> fo.FileOps:
        return fo.FileOps(self.fake, self.journal, scope=self.scope, cancel=lambda: self.stop, recycler=self.recycler)

    def undoer(self) -> fo.Undoer:
        return fo.Undoer(self.fake, self.journal, scope=self.scope, recycler=self.recycler)

    def bin_rows(self, *paths_: str) -> fo.BatchReport:
        return self.ops().run([fo.FileOp(fo.RECYCLE, (self.item(path),)) for path in paths_])

    def states(self, seq: int) -> list[str]:
        return [record.state for record in self.journal.records() if record.seq == seq]

    def refused_before(self, path: str, reason: str) -> None:
        """``path`` is refused with ``reason`` before the bin's one call, and nothing moves."""
        with self.assertRaises(fo.RecycleUnavailable) as caught:
            self.recycler.recycle(path)
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(self.bin.asked_to_bin(), [])
        self.assertTrue(self.there(path))


# --------------------------------------------------------------------------
# The sink's verdict (pure) and the flags
# --------------------------------------------------------------------------

class DecideTests(unittest.TestCase):
    def test_recycled_only_when_post_delete_item_says_so(self) -> None:
        self.assertEqual(recycle.decide(RECYCLED_REPORT, False), (recycle.RECYCLED, 0))
        self.assertEqual(recycle.decide(RECYCLED_REPORT, True), (recycle.RECYCLED, 0))     # something new there now
        nuked = replace(RECYCLED_REPORT, in_bin=False)
        for still in (False, True):
            self.assertEqual(recycle.decide(nuked, still), (recycle.NOT_RECYCLED, 0))      # psiNewlyCreated NULL

    def test_pre_delete_item_without_the_recycle_flag_is_stopped_first(self) -> None:
        refused = recycle.SinkReport(pre_flags=0, perform_hr=0x80004004, aborted=True)
        self.assertEqual(recycle.decide(refused, True), (recycle.WOULD_NUKE, 0))
        self.assertEqual(recycle.decide(refused, False), (recycle.NOT_RECYCLED, 0))       # gone anyway: stop

    def test_windows_refusing_and_the_owner_saying_no(self) -> None:
        in_use = recycle.SinkReport(pre_flags=0x80, post_hr=IN_USE, in_bin=False, perform_hr=IN_USE)
        self.assertEqual(recycle.decide(in_use, True), (recycle.FAILED, 32))
        self.assertEqual(recycle.decide(in_use, False), (recycle.NOT_RECYCLED, 0))
        self.assertEqual(recycle.decide(recycle.SinkReport(perform_hr=0x80270000), True), (recycle.CANCELLED, 0))
        self.assertEqual(recycle.decide(recycle.SinkReport(aborted=True), True), (recycle.CANCELLED, 0))
        self.assertEqual(recycle.decide(recycle.SinkReport(perform_hr=0x80070005), True), (recycle.FAILED, 5))
        self.assertEqual(recycle.decide(recycle.SinkReport(), False), (recycle.NOT_RECYCLED, 0))   # unconfirmed
        self.assertEqual((recycle.win32_code(0x80070020), recycle.win32_code(-2147024864),
                          recycle.win32_code(0x8027000), recycle.win32_code(0)), (32, 32, 0, 0))

    def test_the_flags_by_value_never_no_confirmation(self) -> None:
        self.assertEqual(guard.FOFX_RECYCLEONDELETE, 0x00080000)
        flags = recycle.RECYCLE_FLAGS
        self.assertTrue(guard.recycle_flags_ok(flags))
        for wanted in (guard.FOFX_RECYCLEONDELETE, guard.FOF_ALLOWUNDO, guard.FOF_WANTNUKEWARNING):
            self.assertEqual(flags & wanted, wanted)
        for never in (guard.FOF_NOCONFIRMATION, 0x0004, 0x0200):                       # nor SILENT / NO_UI's parts
            self.assertFalse(flags & never)
        self.assertEqual(flags, 0x00080000 | 0x0040 | 0x4000 | 0x0400)
        self.assertEqual(fo.BIN_STOPS, {fo.R_WOULD_NUKE, fo.R_NOT_RECYCLED})
        self.assertEqual(fo.UNDO_WORDS[fo.R_BIN_EMPTIED], "Can't be put back - the Recycle Bin was emptied.")


class RealBinGuardTests(unittest.TestCase):
    """The real Windows side is never made, and every method of it refuses while the tests run - before any Windows
    entry point it could reach (booby-trapped here for this thread)."""

    CALLS = {"bin_state": ("C:\\",), "recycle_item": ("C:\\Users\\Owner\\Downloads\\a.txt",),
             "bin_entries": ("a.txt",),
             "restore_entry": (recycle.BinEntry("C:\\Users\\Owner\\Downloads", "a.txt", 1.0, 1, b"x"),
                               "C:\\Users\\Owner\\Downloads\\a.txt")}

    def test_never_constructed_and_every_method_guarded(self) -> None:
        with self.assertRaises(dbk.RealBackendInTests):
            recycle.Win32RecycleBin()
        public = {name for name in vars(recycle.Win32RecycleBin) if not name.startswith("_")}
        self.assertEqual(public, set(self.CALLS))
        self.assertEqual(public, {name for name in vars(recycle.BinSeam) if not name.startswith("_")})
        seam = object.__new__(recycle.Win32RecycleBin)                 # its constructor never ran
        caller = threading.current_thread()
        reached: list[str] = []

        def trap(label: str, original: Any) -> Any:
            def trapped(*args: Any, **kwargs: Any) -> Any:
                if threading.current_thread() is caller:
                    reached.append(label)
                    raise AssertionError(f"the real bin reached {label}")
                return original(*args, **kwargs)
            return trapped

        traps = [mock.patch.object(recycle, name, trap(name, getattr(recycle, name)))
                 for name in ("_com_ready", "_progress_sink", "_bin_folder", "_volume_guid", "_bin_settings",
                              "_policy_off")]
        traps += [mock.patch.object(ctypes, "WinDLL", trap("ctypes.WinDLL", ctypes.WinDLL)),
                  mock.patch.object(os.path, "lexists", trap("os.path.lexists", os.path.lexists))]
        for patch in traps:
            patch.start()
            self.addCleanup(patch.stop)
        for name, args in self.CALLS.items():
            with self.subTest(method=name):
                self.assertTrue(getattr(getattr(recycle.Win32RecycleBin, name), "reaches_os", False))
                with self.assertRaises(dbk.RealBackendInTests):
                    getattr(seam, name)(*args)
        for patch in reversed(traps):
            patch.stop()
        self.assertEqual(reached, [])

    def test_no_name_is_a_never_capability(self) -> None:
        for cls in (recycle.Win32RecycleBin, recycle.BinSeam, recycle.RecycleBin, fo.NoRecycleBin, FakeRecycleBin):
            for name in vars(cls):
                if name.startswith("_"):
                    continue
                for word in name.split("_"):
                    self.assertNotIn(word, guard.NEVER_CAPABILITIES, f"{cls.__name__}.{name}")

    def test_importing_it_reaches_no_windows_module(self) -> None:
        code = ("import sys; import briefing_reader.desktop.recycle as r; "        # pywin32 only once it is used
                "print(sorted(m for m in ('pythoncom', 'pywintypes', 'win32com', 'win32gui') if m in sys.modules))")
        done = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60,
                              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              env={**os.environ, dbk.TEST_GUARD_ENV: "1"})
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        self.assertEqual(done.stdout.strip(), "[]")

    def test_the_service_never_makes_the_real_one_in_a_test(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            fake = desktop_disk(tmp)
            service = DesktopService(folders=dbk.ROOT_FOLDERS, backend_factory=lambda: fake,
                                     journal_folder=lambda: os.path.join(tmp, "journal"))
            with self.assertLogs("briefing_reader.desktop", "WARNING") as logs:
                self.assertIsInstance(service.recycler(), fo.NoRecycleBin)     # RealBackendInTests: no bin
            self.assertIn("RealBackendInTests", "\n".join(logs.output))
            given = DesktopService(folders=dbk.ROOT_FOLDERS, backend_factory=lambda: fake,
                                   recycle_factory=lambda: FakeRecycleBin(fake))
            self.assertIsInstance(given.recycler(), recycle.RecycleBin)
            self.assertIs(given.recycler(), given.recycler())


# --------------------------------------------------------------------------
# The Recycle Bin, as Jarvis uses it
# --------------------------------------------------------------------------

class RecycleBinTests(BinCase):
    def test_recycled_into_the_bin_and_nowhere_else(self) -> None:
        self.recycler.recycle(OLD, self.fake.identity(OLD))
        self.assertFalse(self.there(OLD))
        self.assertEqual((self.bin.recycled, self.bin.nuked), ([OLD], []))
        self.assertEqual([method for method, _a in self.bin.calls], ["bin_state", "recycle_item"])
        self.assertEqual(self.bin.state().items, 1)

    def test_nuked_is_not_recycled_and_stops(self) -> None:
        self.bin.nuke = True
        with self.assertRaises(fo.RecycleUnavailable) as caught:
            self.recycler.recycle(OLD)
        self.assertEqual((caught.exception.reason, caught.exception.stop), (fo.R_NOT_RECYCLED, True))
        self.assertEqual(self.bin.nuked, [OLD])

    def test_deleted_for_good_is_stopped_before_and_it_stays(self) -> None:
        self.bin.refuse = True
        with self.assertRaises(fo.RecycleUnavailable) as caught:
            self.recycler.recycle(OLD)
        self.assertEqual((caught.exception.reason, caught.exception.stop), (fo.R_WOULD_NUKE, True))
        self.assertTrue(self.there(OLD))

    def test_windows_refusing_is_an_os_error_with_its_code(self) -> None:
        self.bin.hresult = IN_USE
        with self.assertRaises(OSError) as caught:
            self.recycler.recycle(OLD)
        self.assertEqual((caught.exception.winerror, fo.os_reason(caught.exception)), (32, fo.R_IN_USE))
        self.bin.hresult, self.bin.silent = 0, True
        with self.assertRaises(OSError) as caught:
            self.recycler.recycle(OLD)
        self.assertEqual(fo.os_reason(caught.exception), fo.R_ACCESS)
        self.assertTrue(self.there(OLD))

    def test_the_bin_off_too_small_or_of_unknown_size_refuses_before_any_call(self) -> None:
        size = self.fake.identity(OLD).size
        for state, reason in ((recycle.BinState(False, 10 ** 9), fo.R_BIN_OFF),
                              (recycle.BinState(True, size + 100, 101), fo.R_BIN_FULL),
                              (recycle.BinState(True, None), fo.R_BIN_UNKNOWN)):
            with self.subTest(reason=reason):
                self.bin.states["C:\\"] = state
                self.refused_before(OLD, reason)
                self.assertEqual(self.recycler.check(OLD, size=size), reason)
        self.bin.states["C:\\"] = recycle.BinState(True, size + 100, 100)        # exactly the room left: it fits
        self.assertEqual(self.recycler.check(OLD, size=size), "")
        self.recycler.recycle(OLD)
        self.assertFalse(self.there(OLD))

    def test_a_removable_or_network_drive_has_no_bin_and_is_asked_first(self) -> None:
        for kind in (dbk.DRIVE_REMOVABLE, dbk.DRIVE_REMOTE):
            with self.subTest(kind=kind):
                self.fake.set_drive("C:\\", kind)
                self.fake.calls.clear()
                self.refused_before(OLD, fo.R_NO_BIN_HERE)
                self.assertEqual([method for method, _a in self.fake.calls], ["drive_type"])   # nothing else asked
                self.assertEqual(self.recycler.check(OLD, size=1), fo.R_NO_BIN_HERE)
        self.assertEqual([method for method, _a in self.bin.calls], [])

    def test_online_only_links_secrets_and_odd_paths_refuse_before_any_call(self) -> None:
        self.fake.set_attributes(OLD, dbk.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
        self.refused_before(OLD, fo.R_ONLINE_ONLY)                     # it would skip the bin
        folder = self.fake.make_dir(DOWNLOADS + "\\Old photos")
        self.fake.make_file(folder + "\\a.jpg")
        self.fake.set_attributes(folder + "\\a.jpg", dbk.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
        self.refused_before(folder, fo.R_ONLINE_ONLY)                  # a folder holding one
        code = self.fake.make_dir(DOWNLOADS + "\\Project")
        self.fake.make_dir(code + "\\.git")
        self.refused_before(code, fo.R_HOLDS_CODE)                     # the subtree walk before any folder
        link = self.fake.junction(DOWNLOADS + "\\Shortcut folder", DOCS)
        self.refused_before(link, fo.R_EXCLUDED)
        secret = self.fake.make_file(DOWNLOADS + "\\.env")
        self.refused_before(secret, fo.R_EXCLUDED)
        for odd in ("\\\\server\\share\\a.txt", "C:relative.txt", DOWNLOADS + "\\a.txt:stream"):
            with self.assertRaises(fo.RecycleUnavailable):
                self.recycler.recycle(odd)
        self.assertEqual(self.bin.asked_to_bin(), [])

    def test_the_identity_is_checked_again_right_before_the_call(self) -> None:
        identity = self.fake.identity(OLD)
        with open(self.fake.real(OLD), "ab") as handle:
            handle.write(b"more")
        with self.assertRaises(fo.RecycleUnavailable) as caught:
            self.recycler.recycle(OLD, identity)
        self.assertEqual(caught.exception.reason, fo.R_CHANGED)
        changed: list[str] = []

        def between(method: str, argument: str) -> None:              # it changes after the bin's own checks
            if method == "file_id" and not changed and self.bin.calls:
                changed.append(argument)
                with open(self.fake.real(OLD), "ab") as handle:
                    handle.write(b"x")

        self.fake.on_call = between
        with self.assertRaises(fo.RecycleUnavailable) as caught:
            self.recycler.recycle(OLD, self.fake.identity(OLD))
        self.assertEqual(caught.exception.reason, fo.R_CHANGED)
        self.assertEqual(self.bin.asked_to_bin(), [])

    def test_find_by_old_path_time_and_size_then_restore(self) -> None:
        identity = self.fake.identity(OLD)
        before = self.bin.clock()
        self.recycler.recycle(OLD)
        after = self.bin.clock()
        entry = self.recycler.find(OLD, size=identity.size, is_dir=False, since=before - 1, until=after + 1)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.original, OLD)
        self.assertIsNone(self.recycler.find(OLD, size=identity.size + 1, is_dir=False, since=before - 1,
                                             until=after + 1))                            # another size
        self.assertIsNone(self.recycler.find(OLD, size=identity.size, is_dir=False, since=after + 10,
                                             until=after + 20))                           # another time
        self.recycler.restore(entry, OLD)
        self.assertTrue(self.there(OLD))
        self.assertEqual(self.fake.identity(OLD).file_id, identity.file_id)              # the same file back
        self.recycler.recycle(OLD)
        entry = self.recycler.find(OLD, size=identity.size, is_dir=False, since=before - 1, until=self.bin.clock())
        self.bin.restore_fails = True
        with self.assertRaises(fo.RecycleUnavailable) as caught:
            self.recycler.restore(entry, OLD)
        self.assertEqual(caught.exception.reason, fo.R_NOT_RESTORED)
        self.bin.clear_bin()
        self.assertIsNone(self.recycler.find(OLD, size=identity.size, is_dir=False, since=before - 1,
                                             until=self.bin.clock()))

    def test_no_bin_to_reach_refuses_everything_plainly(self) -> None:
        none = fo.NoRecycleBin()
        self.assertEqual(none.check(OLD, size=1), fo.R_NO_BIN)
        for call in (lambda: none.recycle(OLD), lambda: none.find(OLD, size=1, is_dir=False, since=0, until=1),
                     lambda: none.restore(None, OLD)):
            with self.assertRaises(fo.RecycleUnavailable) as caught:
                call()
            self.assertEqual((caught.exception.reason, caught.exception.stop), (fo.R_NO_BIN, False))
        self.assertEqual(fo.UNDO_WORDS[fo.R_NO_BIN], "The Recycle Bin can't be reached right now - left as it is.")


# --------------------------------------------------------------------------
# A card's rows to the bin (fileops, journalled)
# --------------------------------------------------------------------------

class RecycleRowTests(BinCase):
    def test_each_row_is_journalled_doing_then_done_with_its_old_place(self) -> None:
        report = self.bin_rows(OLD, SETUP)
        self.assertEqual([(r.state, r.reason) for r in report.results], [(jn.DONE, ""), (jn.DONE, "")])
        self.assertEqual(self.bin.recycled, [OLD, SETUP])
        self.assertEqual(self.states(1), [jn.PLANNED, jn.DOING, jn.DONE])
        done = self.journal.items()[1].done
        self.assertEqual((done.kind, done.src, done.dst), (jn.RECYCLE, OLD, ""))
        self.assertEqual(done.size, 4000)

    def test_a_nuked_row_stops_the_batch_and_is_failed(self) -> None:
        third = self.fake.make_file(DOWNLOADS + "\\third.txt")
        self.bin.nuke = True
        report = self.bin_rows(OLD, SETUP, third)
        self.assertEqual([(r.state, r.reason) for r in report.results],
                         [(jn.FAILED, fo.R_NOT_RECYCLED), (jn.SKIPPED, fo.R_BIN_STOPPED),
                          (jn.SKIPPED, fo.R_BIN_STOPPED)])
        self.assertEqual(self.bin.asked_to_bin(), [OLD])                # nothing more asked of the bin
        self.assertTrue(self.there(SETUP) and self.there(third))

    def test_deleted_for_good_on_the_second_row_stops_the_rest(self) -> None:
        third = self.fake.make_file(DOWNLOADS + "\\third.txt")
        seen = [0]
        original = self.bin.recycle_item

        def second_refused(path: str) -> Any:
            seen[0] += 1
            self.bin.refuse = seen[0] == 2
            return original(path)

        self.bin.recycle_item = second_refused                          # type: ignore[method-assign]
        report = self.bin_rows(OLD, SETUP, third)
        self.assertEqual([(r.state, r.reason) for r in report.results],
                         [(jn.DONE, ""), (jn.FAILED, fo.R_WOULD_NUKE), (jn.SKIPPED, fo.R_BIN_STOPPED)])
        self.assertTrue(self.there(SETUP) and self.there(third))

    def test_pre_checks_refuse_each_row_without_stopping_the_others(self) -> None:
        self.bin.states["C:\\"] = recycle.BinState(False, 10 ** 9)
        report = self.bin_rows(OLD, SETUP)
        self.assertEqual([(r.state, r.reason) for r in report.results],
                         [(jn.SKIPPED, fo.R_BIN_OFF), (jn.SKIPPED, fo.R_BIN_OFF)])    # checked first: never tried
        self.assertEqual(self.bin.asked_to_bin(), [])
        self.assertEqual(self.states(1), [jn.PLANNED, jn.SKIPPED])
        calls = iter([recycle.BinState(True, 10 ** 9), recycle.BinState(False, 10 ** 9)])
        self.bin.state = lambda drive="C:\\": next(calls)                 # type: ignore[method-assign]
        report = self.bin_rows(OLD)                                      # off between the row's check and its call
        self.assertEqual([(r.state, r.reason) for r in report.results], [(jn.FAILED, fo.R_BIN_OFF)])
        self.assertTrue(self.there(OLD))

    def test_the_rows_own_check(self) -> None:
        ops = self.ops()
        self.assertEqual(ops.check(fo.FileOp(fo.RECYCLE, (self.item(OLD),))).reason, "")
        self.assertEqual(ops.check(fo.FileOp(fo.RECYCLE, (self.item(OLD),), self.place(DOCS))).reason, fo.R_SHAPE)
        self.assertEqual(ops.check(fo.FileOp(fo.RECYCLE, (self.item(OLD),), None, "new.docx")).reason, fo.R_SHAPE)
        self.assertEqual(ops.check(fo.FileOp(fo.RECYCLE, (self.place(DOCS),))).reason, fo.R_ROOT)
        budget = ops.check(fo.FileOp(fo.RECYCLE, (self.item(BUDGET),)))
        self.assertEqual((budget.reason, budget.flags), ("", {fo.FLAG_LEAVES_ONEDRIVE}))   # OneDrive removes it too
        self.fake.set_attributes(OLD, dbk.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
        self.assertEqual(ops.check(fo.FileOp(fo.RECYCLE, (self.item(OLD),))).reason, fo.R_ONLINE_ONLY)
        self.bin.states["C:\\"] = recycle.BinState(True, 10)
        self.assertEqual(ops.check(fo.FileOp(fo.RECYCLE, (self.item(SETUP),))).reason, fo.R_BIN_FULL)
        self.assertEqual(self.bin.asked_to_bin(), [])

    def test_a_folder_is_walked_first(self) -> None:
        folder = self.fake.make_dir(DOWNLOADS + "\\Old notes")
        self.fake.make_file(folder + "\\a.txt", b"x" * 10)
        self.fake.make_file(folder + "\\Inner\\b.txt", b"x" * 20)
        check = self.ops().check(fo.FileOp(fo.RECYCLE, (self.place(folder),)))
        self.assertEqual((check.reason, check.bytes), ("", 30))
        self.fake.make_file(folder + "\\Inner\\id_rsa")
        self.assertEqual(self.ops().check(fo.FileOp(fo.RECYCLE, (self.place(folder),))).reason,
                         fo.R_HOLDS_SECRET_FILE)


# --------------------------------------------------------------------------
# Undo: out of the bin, and Jarvis's own outputs into it
# --------------------------------------------------------------------------

class RestoreTests(BinCase):
    def test_undo_takes_them_back_out_newest_first(self) -> None:
        self.bin_rows(OLD, SETUP)
        undone = self.undoer().undo([1, 2])
        self.assertEqual([(r.seq, r.state) for r in undone.results], [(2, jn.UNDONE), (1, jn.UNDONE)])
        self.assertTrue(self.there(OLD) and self.there(SETUP))
        self.assertEqual([argument for method, argument in self.bin.calls if method == "restore_entry"], [SETUP, OLD])
        self.assertEqual(self.states(1)[-2:], [jn.DOING, jn.UNDONE])
        self.assertEqual(undone.stamp(), "PUT BACK 2 OF 2")

    def test_after_the_bin_was_emptied(self) -> None:
        self.bin_rows(OLD)
        self.bin.clear_bin()
        undone = self.undoer().undo([1])
        self.assertEqual((undone.results[0].reason, undone.results[0].words),
                         (fo.R_BIN_EMPTIED, "Can't be put back - the Recycle Bin was emptied."))
        self.assertEqual(undone.stamp(), f"PUT BACK 0 OF 1 {DOT} 1 BIN EMPTIED")
        self.assertEqual(self.states(1)[-1], jn.UNDO_REFUSED)

    def test_when_the_old_place_is_taken(self) -> None:
        self.bin_rows(OLD)
        self.fake.make_file(OLD, b"something new")
        undone = self.undoer().undo([1])
        self.assertEqual((undone.results[0].reason, undone.results[0].words),
                         (fo.R_OCCUPIED, "Something new is in its old place - left where it is."))
        self.assertEqual([m for m, _a in self.bin.calls if m == "restore_entry"], [])   # never over anything
        self.assertEqual(self.fake.identity(OLD).size, len(b"something new"))

    def test_when_its_old_folder_has_gone_or_the_restore_fails(self) -> None:
        folder = self.fake.make_dir(DOWNLOADS + "\\Week 3")
        inside = self.fake.make_file(folder + "\\notes.txt")
        self.bin_rows(inside, OLD)
        os.rmdir(self.fake.real(folder))
        self.bin.restore_fails = True
        undone = self.undoer().undo([1, 2])
        self.assertEqual([(r.seq, r.reason) for r in undone.results],
                         [(2, fo.R_NOT_RESTORED), (1, fo.R_OLD_PLACE_GONE)])
        self.assertEqual(undone.results[0].words, "Windows didn't put it back - it's still in the Recycle Bin.")
        self.bin.restore_fails = False
        self.assertEqual(self.undoer().undo([2]).put_back, 1)            # refused once: offered again

    def test_a_restore_left_doing_by_a_crash(self) -> None:
        self.bin_rows(OLD, SETUP)
        self.undoer().undo([1])
        doing = self.journal.items()[2].done
        self.journal.append(replace(doing, state=jn.DOING, undo=True, at=0.0))     # a crash during its restore
        report = jn.recover(self.journal.folder, self.fake)
        self.assertEqual(report.corrected, {jn.UNDO_REFUSED: 1})          # still in the bin: refused, offered again
        self.assertEqual(self.journal.items()[2].last.reason_code, jn.R_INTERRUPTED)


class UndoThroughTheBinTests(BinCase):
    def test_copies_and_zips_unchanged_go_to_the_bin_a_changed_one_stays(self) -> None:
        ops = self.ops()
        report = ops.run([fo.FileOp(fo.COPY, (self.item(OLD),), self.place(DOCS)),
                          fo.FileOp(fo.ZIP, (self.item(BUDGET),), self.place(DOCS), "Budget.zip")])
        self.assertEqual({r.state for r in report.results}, {jn.DONE})
        copy, zipped = DOCS + "\\LING essay draft (old).docx", DOCS + "\\Budget.zip"
        with open(self.fake.real(copy), "r+b") as handle:
            handle.write(b"Y")                                           # same size, new content: changed
        undone = self.undoer().undo([1, 2])
        self.assertEqual([(r.seq, r.state, r.reason) for r in undone.results],
                         [(2, jn.UNDONE, ""), (1, jn.UNDO_REFUSED, fo.R_CHANGED)])
        self.assertEqual(self.bin.recycled, [zipped])
        self.assertTrue(self.there(copy) and self.there(OLD) and self.there(BUDGET))

    def test_a_bin_that_cant_take_an_output_leaves_it_with_its_reason(self) -> None:
        self.ops().run([fo.FileOp(fo.COPY, (self.item(OLD),), self.place(DOCS))])
        self.bin.states["C:\\"] = recycle.BinState(False, 10 ** 9)
        undone = self.undoer().undo([1])
        self.assertEqual((undone.results[0].reason, undone.results[0].words),
                         (fo.R_BIN_OFF, "The Recycle Bin is off for that drive - left as it is."))
        self.assertEqual(undone.stamp(), f"PUT BACK 0 OF 1 {DOT} 1 BIN OFF")

    def test_a_part_made_copy_of_a_stopped_folder_copy_is_binned_on_undo(self) -> None:
        folder = self.fake.make_dir(DOWNLOADS + "\\Photos")
        for n in range(3):
            self.fake.make_file(folder + f"\\p{n}.jpg", b"x" * 10)
        copies = [0]

        def stop_after_one(method: str, _argument: str) -> None:
            if method == "copy_file":
                copies[0] += 1
                self.stop = copies[0] >= 2

        self.fake.on_call = stop_after_one
        report = self.ops().run([fo.FileOp(fo.COPY, (self.place(folder),), self.place(DOCS))])
        self.fake.on_call = None
        result = report.results[0]
        self.assertEqual((result.state, result.reason), (jn.FAILED, fo.R_STOPPED))
        self.assertTrue(result.leftover.endswith(fo.TEMP_SUFFIX) and self.there(result.leftover))
        self.assertEqual(dt.ends_from([1], report.results)[0].leftover, True)
        self.assertEqual(self.undoer().undoable(), ())                  # nothing done: never offered by itself
        undone = self.undoer().undo([1])
        self.assertEqual((undone.results[0].state, undone.results[0].reason), (jn.UNDONE, ""))
        self.assertFalse(self.there(result.leftover))
        self.assertEqual(self.bin.recycled, [result.leftover])
        self.assertEqual(self.undoer().undo([1]).results[0].reason, fo.R_NOT_UNDOABLE)   # once only

    def test_an_undo_stops_after_an_item_the_bin_did_not_take(self) -> None:
        self.ops().run([fo.FileOp(fo.COPY, (self.item(OLD),), self.place(DOCS)),
                        fo.FileOp(fo.COPY, (self.item(SETUP),), self.place(DOCS)),
                        fo.FileOp(fo.MOVE, (self.item(BUDGET),), self.place(DOWNLOADS))])
        self.bin.nuke = True
        undone = self.undoer().undo([1, 2, 3])
        self.assertEqual([(r.seq, r.reason) for r in undone.results],
                         [(3, ""), (2, fo.R_NOT_RECYCLED), (1, fo.R_BIN_STOPPED)])
        self.assertEqual(self.bin.asked_to_bin(), [DOCS + "\\SpotifySetup.exe"])


class RecoveryTests(BinCase):
    def test_a_recycle_left_doing_is_corrected_by_looking(self) -> None:
        identity = self.fake.identity(OLD)
        gone = self.fake.identity(SETUP)
        self.journal.extend([jn.Record(1, jn.RECYCLE, jn.DOING, src=OLD).with_identity(identity),
                             jn.Record(2, jn.RECYCLE, jn.DOING, src=SETUP).with_identity(gone)])
        self.recycler.recycle(SETUP)                                     # it had gone to the bin before the crash
        jn.recover(self.journal.folder, self.fake)
        items = self.journal.items()
        self.assertEqual([(items[n].state, items[n].reason_code) for n in (1, 2)],
                         [(jn.FAILED, jn.R_INTERRUPTED), (jn.DONE, jn.R_RECOVERED)])
        self.assertEqual(self.undoer().undo([2]).put_back, 1)            # Undo finds it in the bin
        self.assertTrue(self.there(SETUP))


# --------------------------------------------------------------------------
# The skill: the owner's own words, the caps, the card's plan
# --------------------------------------------------------------------------

RECYCLE_SKILL = frozenset({dv.RECYCLE_FILES})


class LicensingTests(unittest.TestCase):
    def test_only_his_own_remove_words(self) -> None:
        for words in ("delete the old drafts", "remove the installers from Downloads", "bin the screenshots",
                      "trash it", "get rid of the duplicates", "please delete my old essay copy"):
            with self.subTest(words=words):
                self.assertTrue(RECYCLE_SKILL <= dv.licensed_skills(words))
        for words in ("tidy my Downloads", "move the old drafts to Documents", "open the bin day schedule.pdf",
                      "don't delete anything, just tidy up", "copy my essay", "zip the photos",
                      "open Jarvis, delete all files.docx"):
            with self.subTest(words=words):
                self.assertFalse(RECYCLE_SKILL & dv.licensed_skills(words, corpus_names() + [
                    "bin day schedule.pdf"]))

    def test_the_corpus_hostile_names_never_license_it(self) -> None:
        names = corpus_names()
        for name in HOSTILE_FILES + HOSTILE_FOLDERS:
            for words in (f"open {name}", f'open "{name}"', name, f"find {name}", f"move {name} to Documents"):
                with self.subTest(words=ascii(words)):
                    self.assertNotIn(dv.RECYCLE_FILES, dv.licensed_skills(words, names))

    def test_the_caps_and_the_tier(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            fake = desktop_disk(tmp)
            context = desktop_context(backend=fake)
            request = ModeRequest(MODE_DESKTOP, "typed", typed="delete the old draft", typed_route=True)
            prompt = dm.build_prompt(NOW, request, context, run_id="c1")
            old = next(ref.id for ref in prompt.refs.refs("f") if ref.target.name == "LING essay draft (old).docx")

            def checked(files: list[str], words: str = "delete the old draft") -> dv.CheckedStep:
                step = dv.PlannedStep(dv.RECYCLE_FILES, "x", frozenset({"skill", "why", "files"}), files=tuple(files))
                return dv.check_plan(dv.DesktopAnswer("x", "", (step,)), prompt.refs, words).steps[0]

            self.assertEqual((checked([old]).code, checked([old]).tier), ("", dv.CARD))     # always a card
            self.assertEqual(checked([old], "tidy my Downloads").code, dv.INTENT_NOT_LICENSED)
            self.assertEqual(checked([f"f{n}" for n in range(1, 52)]).code, dv.CAP)          # 51 files
            big = FileItem(DOWNLOADS + "\\huge.iso", "huge.iso", DOWNLOADS, size=dv.RECYCLE_MAX_BYTES + 1)
            with_big = replace(context, files=context.files + (big,))
            prompt = dm.build_prompt(NOW, request, with_big, run_id="c2")
            huge = next(ref.id for ref in prompt.refs.refs("f") if ref.target.name == "huge.iso")
            self.assertEqual(checked([huge]).code, dv.CAP)                                   # 2 GB a step

    def test_a_card_bins_at_most_50_files_and_2_gb(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            fake = desktop_disk(tmp)
            files = []
            for n in range(55):
                path = fake.make_file(DOWNLOADS + f"\\copy {n}.txt", b"x" * 10)
                files.append(FileItem(path, ntpath.basename(path), DOWNLOADS, size=10))
            context = desktop_context(files=files, backend=fake)
            words = "delete the copies in Downloads"
            request = ModeRequest(MODE_DESKTOP, "typed", typed=words, typed_route=True)
            prompt = dm.build_prompt(NOW, request, context, run_id="c3")
            ids = prompt.refs.ids("f")
            answer = dm.plan_from({"say": "", "steps": [{"skill": "recycle_files", "why": "x", "files": ids[:30]},
                                                         {"skill": "recycle_files", "why": "x", "files": ids[30:55]}]})
            plan = dv.check_plan(answer, prompt.refs, words, run_id="c3", names=dm.context_names(context))
            self.assertEqual([step.code for step in plan.steps], ["", ""])
            scope = paths.load_scope(dbk.ROOT_FOLDERS, (), fake)
            card = cp.build_card(plan, fake, scope, now=NOW, mono=MONO, recycler=FakeRecycleBin(fake))
            self.assertEqual([row.code for row in card.rows], [""] * 50 + [fo.R_RECYCLE_CAP] * 5)
            self.assertEqual(sum(row.ticked for row in card.rows), 50)
            none = cp.build_card(plan, fake, scope, now=NOW, mono=MONO)                       # no bin: none go
            self.assertEqual({row.code for row in none.rows}, {fo.R_NO_BIN})
            bin_ = FakeRecycleBin(fake)
            bin_.states["C:\\"] = recycle.BinState(True, 10 ** 9)
            with mock.patch.object(dv, "RECYCLE_MAX_BYTES", 25):
                card = cp.build_card(plan, fake, scope, now=NOW, mono=MONO, recycler=bin_)
            self.assertEqual([row.code for row in card.rows[:4]], ["", "", fo.R_RECYCLE_CAP, fo.R_RECYCLE_CAP])
            self.assertEqual(bin_.asked_to_bin(), [])                     # a card's plan changes nothing


class CardWordsTests(BinCase):
    WORDS = "delete the old installer and the old essay copy"

    def card(self, steps: list | None = None, words: str = "") -> dt.TidyCard:
        words = words or self.WORDS
        context = desktop_context(backend=self.fake)
        request = ModeRequest(MODE_DESKTOP, "typed", typed=words, typed_route=True)
        prompt = dm.build_prompt(NOW, request, context, run_id="r1")
        by_name = {ref.target.name: ref.id for ref in prompt.refs.refs("f")}
        steps = steps or [{"skill": "recycle_files", "why": "x",
                           "files": [by_name["SpotifySetup.exe"], by_name["LING essay draft (old).docx"],
                                     by_name["Budget 2026.xlsx"]]}]
        checked = dv.check_plan(dm.plan_from({"say": "", "steps": steps}), prompt.refs, words, run_id="r1",
                                names=dm.context_names(context))
        plan = cp.build_card(checked, self.fake, self.scope(), now=NOW, mono=MONO, recycler=self.recycler)
        card = dt.make_card(plan, self.scope(), at=NOW)
        assert card is not None
        return card

    def test_the_waiting_card_in_the_bins_own_words(self) -> None:
        card = self.card()
        self.assertEqual(card.title, "Move these files to the Recycle Bin")
        self.assertEqual([(row.name, row.to) for row in card.rows],
                         [("SpotifySetup.exe", "Recycle Bin"), ("LING essay draft (old).docx", "Recycle Bin"),
                          ("Budget 2026.xlsx", "Recycle Bin")])
        self.assertEqual([row.ticked for row in card.rows], [True, True, True])        # his words asked for each
        self.assertEqual(card.rows[2].flag_words(), "LEAVES ONEDRIVE - REMOVED FROM YOUR OTHER DEVICES")
        state = dt.TidyState(card)
        text = dt.tidy_text(state)
        self.assertEqual((text.kicker, text.tone), (f"TO THE RECYCLE BIN {DOT} NEEDS YOUR OK", dt.AMBER))
        self.assertEqual([button[1] for button in text.buttons], ["Not now", "Move 3 files to the Recycle Bin"])
        self.assertEqual(text.note, "You can get them back from the Recycle Bin, or with Undo. 1 leaves OneDrive - "
                                    "removed from your other devices.")
        state.toggle(2, False)
        state.toggle(3, False)
        text = dt.tidy_text(state)
        self.assertEqual((text.buttons[1][1], text.note), ("Move 1 file to the Recycle Bin",
                                                           "You can get it back from the Recycle Bin, or with Undo."))
        self.assertEqual(self.bin.asked_to_bin(), [])

    def test_its_run_stamp_and_undo(self) -> None:
        state = dt.TidyState(self.card())
        state.start()
        state.started(True)
        state.row_ended(dt.RowEnd(1, jn.DONE, "", 1), 1)
        text = dt.tidy_text(state)
        self.assertEqual(text.kicker, f"BINNING 2 OF 3{ELLIPSIS}")
        self.assertEqual(text.note, "Stop or Esc stops it after this one. Nothing is deleted for good.")
        state.finish(dt.CardOutcome("r1", "", (dt.RowEnd(1, jn.DONE, "", 1), dt.RowEnd(2, jn.DONE, "", 2),
                                               dt.RowEnd(3, jn.DONE, "", 3))), NOW)
        text = dt.tidy_text(state)
        self.assertTrue(text.kicker.startswith(f"BINNED 3 FILES {DOT} "))
        self.assertEqual((text.link, text.link_note), (("undo", "Undo"), "Puts every file back where it was."))
        self.assertEqual(dt.said(state), "Moved 3 files to the Recycle Bin, sir. Undo puts them back.")
        state.start_undo()
        state.finish_undo(dt.UndoOutcome("r1", (dt.RowEnd(0, jn.UNDONE, "", 3), dt.RowEnd(0, jn.UNDONE, "", 2),
                                                dt.RowEnd(0, jn.UNDO_REFUSED, fo.R_BIN_EMPTIED, 1)),
                                         f"PUT BACK 2 OF 3 {DOT} 1 BIN EMPTIED"), NOW)
        text = dt.tidy_text(state)
        self.assertEqual(text.kicker, f"PUT BACK 2 OF 3 {DOT} 1 BIN EMPTIED")
        self.assertEqual([row.words for row in text.rows], ["Can't be put back - the Recycle Bin was emptied.",
                                                            "Put back.", "Put back."])

    def test_stopped_partial_and_refused_in_plain_words(self) -> None:
        state = dt.TidyState(self.card())
        state.start()
        state.finish(dt.CardOutcome("r1", "", (dt.RowEnd(1, jn.DONE, "", 1),
                                               dt.RowEnd(2, jn.FAILED, fo.R_NOT_RECYCLED, 2),
                                               dt.RowEnd(3, jn.SKIPPED, fo.R_BIN_STOPPED, 3))), NOW)
        text = dt.tidy_text(state)
        self.assertTrue(text.kicker.startswith(f"BINNED 1 OF 3 FILES {DOT} "))
        self.assertEqual([row.words for row in text.rows[1:]],
                         ["Windows didn't put it in the Recycle Bin - I stopped there.",
                          "Not done - I stopped after the Recycle Bin didn't take one."])
        self.assertEqual(dt.said(state), "Moved 1 of 3 to the Recycle Bin, sir - see the card for the rest.")
        refused = dt.TidyState(self.card())
        refused.finish(dt.CardOutcome("r1", cp.CARD_EXPIRED), NOW)
        self.assertEqual(dt.tidy_text(refused).note, "This card is more than 15 minutes old, so nothing was moved to "
                                                     "the Recycle Bin. Ask again for a fresh one.")
        self.assertEqual(dt.said(refused), "Nothing was moved to the Recycle Bin, sir - the card was more than 15 "
                                           "minutes old.")

    def test_a_stopped_copys_part_made_copy_is_binned_by_undo_on_his_click(self) -> None:
        steps = [{"skill": "copy_files", "why": "x", "files": ["f8"], "to": "d1"}]      # the old essay copy
        state = dt.TidyState(self.card(steps, "copy the old essay copy into Documents"))
        state.start()
        state.finish(dt.CardOutcome("r1", "", (dt.RowEnd(1, jn.FAILED, fo.R_STOPPED, 1, leftover=True),)), NOW)
        text = dt.tidy_text(state)
        self.assertTrue(text.kicker.startswith(f"STOPPED BY YOU {DOT} NOTHING COPIED {DOT} "))
        self.assertEqual(text.rows[0].words, "Not done - you stopped it. A part-made copy was left - Undo puts it in "
                                             "the Recycle Bin.")
        self.assertEqual((text.link, text.link_note), (("undo", "Undo"), "Puts the part-made copy in the Recycle Bin."))
        self.assertEqual(state.undo_seqs(), (1,))
        state.start_undo()
        state.finish_undo(dt.UndoOutcome("r1", (dt.RowEnd(0, jn.UNDONE, "", 1),), "PUT BACK 1 OF 1"), NOW)
        self.assertEqual(dt.tidy_text(state).rows[0].words, "Its part-made copy is in the Recycle Bin.")

    def test_a_row_that_cant_go_says_why(self) -> None:
        self.fake.set_attributes(OLD, dbk.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
        self.bin.states["C:\\"] = recycle.BinState(True, 1000)          # the installer (900 bytes) fits; 3100 doesn't
        state = dt.TidyState(self.card())
        rows = dt.tidy_text(state).rows
        self.assertEqual([(row.mark, row.words) for row in rows],
                         [(dt.MARK_TICK, ""), (dt.MARK_INFO, "It would skip your PC's Recycle Bin - left for you."),
                          (dt.MARK_INFO, "It's bigger than the Recycle Bin has room for.")])
        self.assertEqual(dt.tidy_text(state).buttons[1][1], "Move 1 file to the Recycle Bin")
        self.bin.states["C:\\"] = recycle.BinState(False, 10 ** 9)
        context = desktop_context(backend=self.fake)
        self.assertEqual(cp.row_words(fo.R_BIN_OFF), "the Recycle Bin is off for that drive")
        self.assertEqual(len(context.files), 8)


class RouteTests(unittest.TestCase):
    def test_removing_files_reaches_the_desktop_and_nothing_else_does(self) -> None:
        for words in ("delete the old screenshots", "remove the installers from my Downloads", "bin my old drafts",
                      "trash the duplicate photos", "get rid of the zip files in Downloads"):
            with self.subTest(words=words):
                self.assertTrue(dr.desktop_request(words))
        for words in ("delete the meeting with Ana", "remove the reminder", "bin it", "delete my email to Ana",
                      "get rid of the follow-up"):
            with self.subTest(words=words):
                self.assertFalse(dr.desktop_request(words))


class CanaryTests(BinCase):
    def test_no_name_or_path_reaches_a_log(self) -> None:
        canary = self.fake.make_file(DOWNLOADS + f"\\{CANARY} notes.txt")
        other = self.fake.make_file(DOWNLOADS + f"\\{CANARY} other.txt")
        with self.assertLogs("briefing_reader.desktop", "DEBUG") as logs:
            self.bin_rows(canary)
            self.undoer().undo([1])
            self.bin.nuke = True
            self.bin_rows(other)
            self.bin.nuke = False
            self.bin.clear_bin()
            self.undoer().undo([1])
        text = "\n".join(logs.output)
        for secret in (CANARY, "Downloads", "Owner", "C:\\", "notes"):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
