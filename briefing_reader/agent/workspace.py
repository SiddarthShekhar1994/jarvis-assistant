"""The agent's own workspace (SPEC D8): the one place a task may write without a click.

    workspace_root(setting, environ)   the folder: [agent] workspace, or Jarvis\\Workspace in the user folder. It never
                                       lies under OneDrive (WorkspaceRefused, in plain words)
    task_folder_name(goal, when)       "<date>-<slug>": the slug sanitised, at most 40 characters
    TaskFolder.create(root, goal, when)   a new folder for one task
    TaskFolder.write / read / body / listing / exists   the workspace tools' work; only inside this folder
    check_name(name)                   a file's name relative to the task folder, as parts, or WorkspaceRefused

What a name may be: relative to the task folder, in parts split at / or \\. Never "..", an absolute path, a drive
letter, a ":" stream, a reserved device name (CON, NUL, COM1 ... with or without an extension), a trailing dot or space,
a part that starts with a dot or a space, a part ending in a shell class id (paths.shell_class_name: it would open as
Control Panel or another system view), or a character Windows refuses or a reader could not see. The last part's
extension is on ALLOWED_EXTENSIONS: text-like files only, never a program or a script (not even .js: Windows runs that
on a double click; a page's own inline script is fine, nothing here reads inside a file).

The caps: MAX_FILE_BYTES a file, MAX_TASK_BYTES and MAX_TASK_FILES a task. Writes are atomic (a temporary file in the
same folder, flushed, then renamed over the name); ``may_commit`` is asked right before the rename, so a task stopped
meanwhile changes nothing. A path that leaves the task folder through a link or a junction is refused. A page or a table
(MARKED_EXTENSIONS) goes out with the Mark of the Web, as a download would, so a browser or Office treats it as from the
internet if the owner opens it from Explorer: best effort, for a drive without streams has no mark and that is no error.

WorkspaceRefused.code is a fixed word (the logs' and Live's), its text is Jarvis's own words for the model. Nothing here
logs anything, and neither ever holds a name or a path of the owner's.
"""

from __future__ import annotations

import ntpath
import os
import re
import secrets
import tempfile
import threading
import unicodedata
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from ..desktop.backend import real_backend_blocked
from ..desktop.paths import shell_class_name
from ..format_chars import invisible_char

ALLOWED_EXTENSIONS = frozenset({".md", ".txt", ".csv", ".tsv", ".json", ".html", ".htm", ".css", ".svg", ".xml",
                                ".yaml", ".yml", ".log"})
# What can run a script or a formula: every text table Excel opens by default is marked, should it come to be allowed.
MARKED_EXTENSIONS = frozenset({".html", ".htm", ".svg", ".xml", ".csv", ".tsv", ".tab", ".slk", ".dif", ".prn"})
ZONE_STREAM = ":Zone.Identifier"
ZONE_MARK = b"[ZoneTransfer]\r\nZoneId=3\r\n"     # 3: the internet
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TASK_BYTES = 50 * 1024 * 1024
MAX_TASK_FILES = 200
SLUG_CAP = 40
PART_CAP = 100                    # one part of a name
NAME_CAP = 200                    # all of it, within Windows' path limit under a usual workspace
DEPTH_CAP = 5                     # folders inside the task folder
READ_CAP = 100_000                # characters read_file gives back at once
TEMP_PREFIX = ".jarvis-"
WORKSPACE_FOLDERS = ("Jarvis", "Workspace")
_ONEDRIVE_ENV = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
_DEVICE_RE = re.compile(r"(?:con|prn|aux|nul|conin\$|conout\$|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3])",
                        re.IGNORECASE)
_DRIVE_RE = re.compile(r"[A-Za-z]:")
_BAD_CHARS = frozenset('<>"|?*')

# Why a name or a write is refused (WorkspaceRefused.code).
R_EMPTY, R_ABSOLUTE, R_DRIVE, R_STREAM, R_PARENT, R_RESERVED, R_TRAILING, R_BAD_CHAR, R_TOO_LONG = (
    "empty", "absolute", "drive", "stream", "parent", "reserved", "trailing", "bad_char", "too_long")
R_EXTENSION, R_FILE_CAP, R_TASK_BYTES, R_TASK_FILES, R_ESCAPED, R_NOT_FOUND, R_STOPPED, R_OS = (
    "extension", "file_cap", "task_bytes", "task_files", "escaped", "not_found", "stopped", "os_error")
R_ONEDRIVE, R_NO_HOME, R_IN_TESTS = "onedrive", "no_home", "in_tests"

ONEDRIVE_TEXT = ("Your [agent] workspace is under OneDrive, and Jarvis keeps a task's files on this PC only. Choose a "
                 "folder outside OneDrive in config.toml; nothing was started.")
NO_HOME_TEXT = "Jarvis can't tell where your user folder is, so a task has nowhere to work; nothing was started."
IN_TESTS_TEXT = "The default workspace in your user folder is never used while the tests run."


class WorkspaceRefused(Exception):
    """A name, a write or a read the workspace does not allow; ``code`` is a fixed word, the text Jarvis's own words."""

    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


def under_onedrive(path: str | Path, environ: Mapping[str, str]) -> bool:
    """``path`` as written or as it really resolves (a junction too) lies in a OneDrive folder: one the environment
    names, or a folder whose name starts with OneDrive ("OneDrive - Example")."""
    written = str(path)
    for text in (written, os.path.realpath(written)):
        folded = ntpath.normcase(ntpath.normpath(text))
        if any(part.startswith("onedrive") for part in folded.split("\\")):
            return True
        for name in _ONEDRIVE_ENV:
            root = str(environ.get(name) or "").strip()
            if root and _within(folded, ntpath.normcase(ntpath.normpath(root))):
                return True
    return False


def _within(path: str, root: str) -> bool:
    """Both normalised and folded; ``path`` is ``root`` or under it."""
    return path == root or path.startswith(root.rstrip("\\") + "\\")


def workspace_root(setting: str, environ: Mapping[str, str] | None = None) -> Path:
    """The workspace folder (not made here): ``setting`` ([agent] workspace), or Jarvis\\Workspace in the user folder.
    WorkspaceRefused when it lies under OneDrive or no user folder is known. While the test guard is on, the default
    in the real user folder is refused too: the tests give a folder of their own."""
    real = environ is None
    environ = os.environ if environ is None else environ
    setting = (setting or "").strip()
    if setting:
        root = Path(setting)
    else:
        home = str(environ.get("USERPROFILE") or "").strip()
        if not home:
            raise WorkspaceRefused(R_NO_HOME, NO_HOME_TEXT)
        if real and real_backend_blocked():
            raise WorkspaceRefused(R_IN_TESTS, IN_TESTS_TEXT)
        root = Path(home).joinpath(*WORKSPACE_FOLDERS)
    if under_onedrive(root, environ):
        raise WorkspaceRefused(R_ONEDRIVE, ONEDRIVE_TEXT)
    return root


def _slug(goal: str, cap: int = SLUG_CAP) -> str:
    plain = unicodedata.normalize("NFKD", goal or "").encode("ascii", "ignore").decode("ascii").casefold()
    words = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
    if len(words) > cap:
        cut = words[:cap]
        words = cut.rsplit("-", 1)[0] if "-" in cut else cut
    return words.strip("-") or "task"


def task_folder_name(goal: str, when: datetime, *, suffix: str = "") -> str:
    """"2026-11-04-compare-three-laptops": the day, then the goal's words made safe; at most SLUG_CAP characters
    after the day, ``suffix`` ("-2") included."""
    return f"{when.strftime('%Y-%m-%d')}-{_slug(goal, SLUG_CAP - len(suffix))}{suffix}"


def check_name(name: object) -> tuple[str, ...]:
    """``name`` as its parts inside the task folder, or WorkspaceRefused (see the module docs)."""
    if not isinstance(name, str) or not name.strip():
        raise WorkspaceRefused(R_EMPTY, "A file needs a name.")
    if name[0] in "/\\":
        raise WorkspaceRefused(R_ABSOLUTE, "Use a name inside the task folder, never a full path.")
    if _DRIVE_RE.match(name):
        raise WorkspaceRefused(R_ABSOLUTE if name[2:3] in ("/", "\\") else R_DRIVE,
                               "Use a name inside the task folder, never a drive or a full path.")
    if ":" in name:
        raise WorkspaceRefused(R_STREAM, "A file's name can't hold a colon.")
    if len(name) > NAME_CAP:
        raise WorkspaceRefused(R_TOO_LONG, f"That name is too long: at most {NAME_CAP} characters.")
    parts = tuple(name.replace("\\", "/").split("/"))
    if len(parts) > DEPTH_CAP + 1:
        raise WorkspaceRefused(R_TOO_LONG, f"That name goes too deep: at most {DEPTH_CAP} folders.")
    for part in parts:
        _check_part(part)
    suffix = os.path.splitext(parts[-1])[1].casefold()
    if suffix not in ALLOWED_EXTENSIONS:
        kinds = " ".join(sorted(ALLOWED_EXTENSIONS))
        raise WorkspaceRefused(R_EXTENSION, f"That kind of file isn't allowed. The workspace takes only: {kinds}")
    return parts


def _check_part(part: str) -> None:
    if not part:
        raise WorkspaceRefused(R_EMPTY, "A name can't have an empty part (two slashes, or a slash at its end).")
    if part in (".", ".."):
        raise WorkspaceRefused(R_PARENT, 'A name can\'t use "." or "..": stay inside the task folder.')
    if part != part.rstrip(". "):
        raise WorkspaceRefused(R_TRAILING, "A name can't end with a dot or a space.")
    if part[0] in ". ":
        raise WorkspaceRefused(R_TRAILING, "A name can't start with a dot or a space.")
    if len(part) > PART_CAP:
        raise WorkspaceRefused(R_TOO_LONG, f"A part of that name is too long: at most {PART_CAP} characters.")
    for char in part:
        if char in _BAD_CHARS or unicodedata.category(char) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") \
                or invisible_char(char):
            raise WorkspaceRefused(R_BAD_CHAR, 'A name can\'t hold < > " | ? * or characters no one can see.')
    if shell_class_name(part):
        raise WorkspaceRefused(R_BAD_CHAR, "A name can't end in a system class id ({...}).")
    if _DEVICE_RE.fullmatch(part.split(".", 1)[0].rstrip(" ")):
        raise WorkspaceRefused(R_RESERVED, "That name is one Windows keeps for itself (CON, NUL, COM1 ...).")


def _mark_from_the_web(path: str) -> None:
    """The Mark of the Web on ``path``. Best effort: a drive without streams simply has no mark."""
    try:
        with open(path + ZONE_STREAM, "wb") as stream:
            stream.write(ZONE_MARK)
    except OSError:
        pass


def megabytes(count: int) -> str:
    return f"{count / (1024 * 1024):.0f} MB"


class TaskFolder:
    """One task's folder. Thread-safe: tool calls may come side by side."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def __repr__(self) -> str:   # never the name: it holds the owner's words
        return "TaskFolder()"

    @classmethod
    def create(cls, root: Path, goal: str, when: datetime) -> TaskFolder:
        """A new "<date>-<slug>" folder under ``root`` (made when missing); a name already taken gets "-2", "-3" ...
        WorkspaceRefused (R_OS) when it can't be made."""
        try:
            Path(root).mkdir(parents=True, exist_ok=True)
            for number in range(1, 1000):
                suffix = f"-{number}" if number > 1 else ""
                path = Path(root) / task_folder_name(goal, when, suffix=suffix)
                try:
                    path.mkdir()
                except FileExistsError:
                    continue
                return cls(path)
        except OSError as exc:
            raise WorkspaceRefused(R_OS, f"The workspace folder could not be made "
                                         f"({exc.strerror or type(exc).__name__}); nothing was started.") from exc
        raise WorkspaceRefused(R_OS, "The workspace has too many folders for today; nothing was started.")

    def _inside(self, parts: tuple[str, ...]) -> str:
        """The full path of ``parts``, or R_ESCAPED when a link or a junction takes it out of this folder."""
        target = os.path.join(str(self.path), *parts)
        root = ntpath.normcase(os.path.realpath(str(self.path)))
        real = ntpath.normcase(os.path.realpath(target))
        if not real.startswith(root.rstrip("\\") + "\\"):
            raise WorkspaceRefused(R_ESCAPED, "That name leads out of the task folder.")
        return target

    def _files(self) -> dict[str, tuple[str, int]]:
        """Every file in this folder: its folded name -> (its name, its size). Links are not followed or counted, and
        a write's temporary file is not a file of the task."""
        found: dict[str, tuple[str, int]] = {}

        def walk(folder: str, prefix: str) -> None:
            try:
                entries = list(os.scandir(folder))
            except OSError:
                return
            for entry in entries:
                name = f"{prefix}{entry.name}"
                try:
                    if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        walk(entry.path, f"{name}/")
                    elif entry.is_file(follow_symlinks=False) and not entry.name.startswith(TEMP_PREFIX):
                        found[name.casefold()] = (name, entry.stat(follow_symlinks=False).st_size)
                except OSError:
                    continue

        walk(str(self.path), "")
        return found

    def write(self, name: object, text: str, *, may_commit: Callable[[], bool] = lambda: True) -> tuple[str, int]:
        """``text`` (UTF-8) as ``name``, whole or not at all: (the name, its bytes), or WorkspaceRefused."""
        parts = check_name(name)
        body = text.encode("utf-8", "replace")
        if len(body) > MAX_FILE_BYTES:
            raise WorkspaceRefused(R_FILE_CAP, f"That file is too big: at most {megabytes(MAX_FILE_BYTES)} a file.")
        shown = "/".join(parts)
        with self._lock:
            target = self._inside(parts)
            files = self._files()
            old = files.get(shown.casefold())
            total = sum(size for _name, size in files.values()) - (old[1] if old else 0)
            if old is None and len(files) >= MAX_TASK_FILES:
                raise WorkspaceRefused(R_TASK_FILES, f"This task has its {MAX_TASK_FILES} files already: overwrite one "
                                                     "or say so in your report.")
            if total + len(body) > MAX_TASK_BYTES:
                raise WorkspaceRefused(R_TASK_BYTES, f"This task's files would pass {megabytes(MAX_TASK_BYTES)}: "
                                                     "overwrite one with less or say so in your report.")
            self._replace(target, body, may_commit)
        return shown, len(body)

    def _replace(self, target: str, body: bytes, may_commit: Callable[[], bool]) -> None:
        folder = os.path.dirname(target)
        part: str | None = None
        try:
            os.makedirs(folder, exist_ok=True)
            fd, part = tempfile.mkstemp(prefix=TEMP_PREFIX + secrets.token_hex(4), suffix=".tmp", dir=folder)
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            if not may_commit():
                raise WorkspaceRefused(R_STOPPED, "The task was stopped; that file was not written.")
            if os.path.splitext(target)[1].casefold() in MARKED_EXTENSIONS:
                _mark_from_the_web(part)      # on the temporary file: the rename keeps it, so it is never unmarked
            os.replace(part, target)
            part = None
        except OSError as exc:
            raise WorkspaceRefused(R_OS, f"That file could not be written ({exc.strerror or type(exc).__name__})."
                                   ) from exc
        finally:
            if part is not None:
                try:
                    os.unlink(part)
                except OSError:
                    pass

    def read(self, name: object) -> tuple[str, str, bool]:
        """(its name, its text up to READ_CAP characters, whether there is more), or WorkspaceRefused."""
        parts = check_name(name)
        shown = "/".join(parts)
        with self._lock:
            target = self._inside(parts)
            if shown.casefold() not in self._files():
                raise WorkspaceRefused(R_NOT_FOUND, "There is no such file in the task folder: list_files shows them.")
            try:
                if os.path.getsize(target) > MAX_FILE_BYTES:
                    raise WorkspaceRefused(R_FILE_CAP, f"That file is over {megabytes(MAX_FILE_BYTES)}.")
                with open(target, "rb") as handle:
                    body = handle.read(MAX_FILE_BYTES + 1)
            except OSError as exc:
                raise WorkspaceRefused(R_OS, f"That file could not be read ({exc.strerror or type(exc).__name__})."
                                       ) from exc
        text = body.decode("utf-8", "replace")
        return shown, text[:READ_CAP], len(text) > READ_CAP

    def body(self, name: object) -> bytes:
        """The whole of the file ``name``, as it is on disk (show_file's scheme handler), or WorkspaceRefused."""
        parts = check_name(name)
        with self._lock:
            target = self._inside(parts)
            if "/".join(parts).casefold() not in self._files():
                raise WorkspaceRefused(R_NOT_FOUND, "There is no such file in the task folder: list_files shows them.")
            try:
                if os.path.getsize(target) > MAX_FILE_BYTES:
                    raise WorkspaceRefused(R_FILE_CAP, f"That file is over {megabytes(MAX_FILE_BYTES)}.")
                with open(target, "rb") as handle:
                    return handle.read(MAX_FILE_BYTES + 1)
            except OSError as exc:
                raise WorkspaceRefused(R_OS, f"That file could not be read ({exc.strerror or type(exc).__name__})."
                                       ) from exc

    def listing(self) -> list[tuple[str, int]]:
        """Every file: (its name, its bytes), by name."""
        with self._lock:
            files = self._files()
        return sorted(files.values(), key=lambda item: item[0].casefold())

    def discard_if_empty(self) -> None:
        """A task that wrote nothing leaves no folder (only an empty one goes)."""
        try:
            os.rmdir(self.path)
        except OSError:
            pass

    def exists(self, name: object) -> bool:
        """``name`` is a good name of a file this folder holds."""
        try:
            parts = check_name(name)
            with self._lock:
                self._inside(parts)
                return "/".join(parts).casefold() in self._files()
        except WorkspaceRefused:
            return False
