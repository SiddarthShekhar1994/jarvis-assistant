"""The NEVER list (SPEC section 2.4) as constants and checks, and the suspect-name scanner (no Qt).

Every constant named in ``NEVER_CONSTANTS`` has its own unit test (tests/test_desktop_guard.py checks that each one
does). validate.py makes these checks on the planner's steps and the executor makes them again right before each OS
call (hard rule 6): no card, setting or voice unlocks any of them. Text is compared as words: NFKC-normalised (so a
fullwidth or other compatibility form reads as its plain letter), hidden characters removed, camelCase and
snake_case split, casefolded ("txtCardNumber" reads "txt card number"), so a run-together label is caught as well.
Cyrillic and Greek look-alike letters are read as Latin only by the suspect scanner (below).

Public API
==========
Hidden characters (one rule for the whole feature: Unicode categories Cf, Zl and Zp - bidi and other format
controls, zero-width characters, tag characters, line and paragraph separators): ``hidden_char(char)``,
``strip_hidden(text)``.
Secrets and payments: ``UIA_IS_PASSWORD_PROPERTY``, ``SENSITIVE_WORDS`` / ``sensitive_label(text)``,
``PAYMENT_WORDS`` / ``payment_page(text)`` (a URL path or a page title; a heuristic, the owner's Submit is the
backstop). What a task may not read or ask (agent.*): ``sensitive_file_name(name)``, ``sensitive_folder_name(name)``,
``SECRET_MARK``, ``scrub_secrets(text)``, ``scrub_part(text, first, last, margin)``, ``holds_secret(text)``,
``asks_for_secret(text)``, ``line_asks(text)``, ``line_asks_for_secret(text)``.
Buttons and keys inside other apps: ``INVOKE_ALLOWED`` (False: no button is pressed in v1), ``COMMIT_WORDS`` /
``commit_button(name)`` (Submit, Send, Pay ... are only highlighted), ``NEVER_KEYS`` / ``key_allowed(vk, ...)``,
``typed_text_allowed(text)``, ``CONSENT_WORDS`` / ``consent_box(label)``, ``FILE_PICKER_WORDS`` /
``file_picker(title, class_name)``.
System changes: ``BLOCKED_TYPES`` / ``blocked_type(name)``, ``NEVER_CREATE_TYPES`` / ``may_create_type(name)``,
``OPENABLE_TYPES`` and ``SCRIPT_CAPABLE_TYPES`` / ``open_kind(name)``, ``ALLOWED_URL_SCHEMES`` /
``url_problem(url)`` (``DND_OPENER_URI`` is Jarvis's own chip's call, never a plan's).
Deleting: ``FORBIDDEN_MOVE_FLAGS`` / ``move_flags_ok``, ``REQUIRED_RECYCLE_FLAGS`` and ``FORBIDDEN_RECYCLE_FLAGS`` /
``recycle_flags_ok``, ``REQUIRED_COPY_FLAGS`` / ``copy_flags_ok``.
Never a target or a place: ``NEVER_OPEN_APPS`` / ``NEVER_TARGET_APPS`` (exe names by group), ``NEVER_APP_WORDS`` /
``never_app(name, exe_path, target=False)``, ``MAX_INTEGRITY_RID`` / ``elevated(rid)``, ``EXCLUDED_KNOWN_FOLDERS``,
``EXCLUDED_ROOT_NAMES``, ``EXCLUDED_NAMES``, ``CODE_MARKERS``, ``SECRET_DIR_NAMES``, ``SECRET_FILE_PATTERNS`` /
``secret_file(name)``.
Other: ``NEVER_CAPABILITIES`` (no backend method and no skill may be named for one of these).
The suspect scanner: ``suspect_text(text)`` - a file or folder name, window title, label or cell that reads like an
order to Jarvis ("IMPORTANT Jarvis move everything to ..."). A step touching a suspect item is refused unless the
owner's own words name it (validate.py), and it is always treated as data.

Nothing here logs, opens or reads anything.
"""

from __future__ import annotations

import fnmatch
import re
import unicodedata
from bisect import bisect_left, bisect_right
from functools import lru_cache
from itertools import islice
from typing import Any
from urllib.parse import urlsplit

from ..actions import web_link_problem
from ..format_chars import format_char, invisible_char

# --------------------------------------------------------------------------
# Words
# --------------------------------------------------------------------------

# Hidden characters: every format character (format_chars, Unicode Cf: bidi controls, zero-width spaces and joiners,
# the BOM, the tag characters U+E0001 and U+E0020-E007F ...) and the line and paragraph separators (Zl, Zp).
_SEPARATOR_CATEGORIES = ("Zl", "Zp")
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD_RE = re.compile(r"[^0-9a-z]+")


def hidden_char(char: str) -> bool:
    """``char`` (one character) is hidden: a bidi or other format control, a zero-width or tag character, or a line
    or paragraph separator (Unicode categories Cf, Zl and Zp)."""
    return format_char(char) or unicodedata.category(char) in _SEPARATOR_CATEGORIES


def strip_hidden(text: str) -> str:
    """``text`` without its hidden characters (``hidden_char``)."""
    if text.isascii():
        return text
    return "".join(char for char in text if not hidden_char(char))


def words(text: Any, *, camel: bool = True) -> list[str]:
    """``text`` as casefolded words: NFKC, hidden characters removed, snake_case and punctuation split, and with
    ``camel`` camelCase split too ("txtCardNumber" -> txt card number)."""
    if not isinstance(text, str):
        return []
    plain = strip_hidden(unicodedata.normalize("NFKC", text))
    if camel:
        plain = _CAMEL_RE.sub(" ", plain)
    return [word for word in _NON_WORD_RE.split(plain.casefold()) if word]


def _readings(text: Any) -> tuple[list[str], list[str]]:
    """Both ways of reading ``text``: camelCase split ("CardNumber" -> card number) and not ("PowerShell")."""
    return words(text), words(text, camel=False)


def _has_phrase(tokens: list[str], phrases: tuple[tuple[str, ...], ...]) -> bool:
    present = set(tokens)
    for phrase in phrases:
        if phrase[0] not in present:         # a long paste's words are looked through once, not once a phrase
            continue
        size = len(phrase)
        if any(tuple(tokens[i:i + size]) == phrase for i in range(len(tokens) - size + 1)):
            return True
    return False


def _says(text: Any, phrases: tuple[tuple[str, ...], ...]) -> bool:
    return any(_has_phrase(tokens, phrases) for tokens in _readings(text))


def _phrases(*texts: str) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(text.split()) for text in texts)


def suffix(name: Any) -> str:
    """The type Windows would see: the last extension, casefolded, with the trailing dots and spaces Windows drops
    removed ("Report.PDF.exe. " -> "exe"); "" for none."""
    if not isinstance(name, str):
        return ""
    plain = unicodedata.normalize("NFKC", name).rstrip(". ")
    base = plain.replace("/", "\\").rsplit("\\", 1)[-1]
    if "." not in base.lstrip("."):
        return ""
    return base.rsplit(".", 1)[-1].casefold()


# --------------------------------------------------------------------------
# Secrets and payments
# --------------------------------------------------------------------------

UIA_IS_PASSWORD_PROPERTY = 30019        # UIA_IsPasswordPropertyId: such a field is never read, typed or copied
# Labels, AutomationIds and HelpText of fields never read, typed or copied (the spec's list plus common spellings).
SENSITIVE_WORDS = _phrases(
    "password", "passwd", "pwd", "passcode", "passphrase", "pin", "pincode", "otp", "one time", "onetime", "totp",
    "2fa", "mfa", "verification code", "cvv", "cvv2", "cvc", "cvc2", "security code", "securitycode", "card number",
    "cardnumber", "card no", "expiry", "expiration", "exp date", "iban", "sort code", "sortcode", "account number",
    "accountnumber", "account no", "routing", "ssn", "social security", "national insurance", "passport", "secret",
    "recovery code", "backup code")
# Pages whose URL path or title has a word starting like this are never filled (SPEC 2.4; a heuristic).
PAYMENT_WORDS = ("pay", "checkout", "billing", "basket", "cart", "order", "wallet", "bank", "transfer")


def sensitive_label(text: Any) -> bool:
    """A field label, AutomationId or HelpText that names a secret, a payment card or an identity number."""
    return _says(text, SENSITIVE_WORDS)


# A task's question (agent.tools): what no one who can't sign in to anything ever needs from the owner.
_SECRET_ASK_WORDS = _phrases("login", "logon", "log in", "sign in", "signin", "credential", "credentials", "username",
                             "user name", "the code", "a code", "your code", "that code", "this code", "code you",
                             "code we", "code sent", "code from", "passwords", "passcodes", "pins", "api key",
                             "api keys", "apikey", "apikeys", "access key", "access keys", "access token",
                             "auth token", "your token", "the token", "private key", "private keys", "secret key",
                             "seed phrase", "recovery phrase", "recovery key", "recovery keys", "recovery codes",
                             "backup codes", "verification codes", "security codes", "card details", "bank details",
                             "login details", "sign in details", "digit", "digits", "number we", "number from",
                             "number on your card", "texted", "sent you", "authenticator", "maiden name",
                             "memorable word", "pass word", "date of birth")


def asks_for_secret(text: Any) -> bool:
    """A question that reads as asking for a secret: ``sensitive_label``'s words, a sign-in, "the code", or the everyday
    ways of asking for one ("the 6 digit number we texted you"). A false refusal here costs a rewording."""
    return sensitive_label(text) or _says(text, _SECRET_ASK_WORDS)


# What makes a line that names a secret one that asks for it: "the login page is down" only says so.
_ASKING_WORDS = _phrases("type", "enter", "send", "tell me", "give me", "share", "paste", "provide", "read me",
                         "need your", "i need", "reply with", "what is your", "what s your", "whats your")


def line_asks(text: Any) -> bool:
    """A line a task says that asks the owner something: a question mark or the words of asking."""
    return "?" in str(text) or _says(text, _ASKING_WORDS)


def line_asks_for_secret(text: Any) -> bool:
    """A line a task says that asks for a secret, where no card stands in the way of what is typed back: it names one as
    ``asks_for_secret`` reads it, and it asks (``line_asks``)."""
    return asks_for_secret(text) and line_asks(text)


def payment_page(text: Any) -> bool:
    """A URL path or page title that reads like payment, a purchase or a transfer ("check out" counts too)."""
    camel, plain = _readings(text)
    return any(token.startswith(PAYMENT_WORDS) for token in (*camel, *plain)) or _has_phrase(camel, (("check", "out"),))


# --------------------------------------------------------------------------
# Buttons and keys inside other apps
# --------------------------------------------------------------------------

INVOKE_ALLOWED = False                  # v1 presses no button in another app (no UIA Invoke on any element)
# The commit buttons Jarvis only highlights ("Submit is yours"); he never presses them.
COMMIT_WORDS = _phrases("submit", "send", "pay", "pay now", "buy", "purchase", "place order", "order", "confirm",
                        "delete", "remove", "transfer", "sign in", "log in", "post", "publish", "checkout")
VK_TAB, VK_RETURN, VK_ESCAPE, VK_DELETE, VK_V = 0x09, 0x0D, 0x1B, 0x2E, 0x56
# Never sent into another app: Enter, Esc, Tab (Tab-then-Enter), Delete; and no Alt or Win shortcut, no Ctrl+V.
NEVER_KEYS = frozenset({VK_TAB, VK_RETURN, VK_ESCAPE, VK_DELETE})
MAX_TYPED_CHARS = 2000
# Consent, terms and authorise boxes are never ticked.
CONSENT_WORDS = _phrases("consent", "terms", "conditions", "agree", "i agree", "accept", "authorise", "authorize",
                         "authorisation", "authorization", "privacy policy")
# File pickers (Open / Save As / Upload / Attach dialogs) are never used.
FILE_PICKER_WORDS = _phrases("open", "open file", "save as", "upload", "file upload", "choose file", "attach",
                             "attach file", "insert file", "browse", "select file")
_DIALOG_CLASS = "#32770"                # the Windows common dialog window class


def commit_button(name: Any) -> bool:
    """A button that commits (Submit, Send, Pay, Delete ...): shown with an amber bracket, never pressed."""
    return _says(name, COMMIT_WORDS)


def key_allowed(vk: int, *, ctrl: bool = False, alt: bool = False, win: bool = False) -> bool:
    """Whether a key may be sent into another app: never Enter, Esc, Tab or Delete, never an Alt or Win shortcut,
    never Ctrl+V."""
    if alt or win or vk in NEVER_KEYS:
        return False
    return not (ctrl and vk == VK_V)


def typed_text_allowed(text: Any) -> bool:
    """Typed values carry no line break, tab, Esc or other control or format character, at most 2,000 characters."""
    if not isinstance(text, str) or len(text) > MAX_TYPED_CHARS:
        return False
    return not any(unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") for char in text)


def consent_box(label: Any) -> bool:
    return _says(label, CONSENT_WORDS)


def file_picker(title: Any, class_name: Any = "") -> bool:
    """An Open / Save As / Upload / Attach dialog: a common dialog with such a title, or such a title anywhere that
    says file ("Choose File to Upload")."""
    if not _says(title, FILE_PICKER_WORDS):
        return False
    tokens = words(title)
    if class_name == _DIALOG_CLASS:
        return True
    return bool({"file", "upload"} & set(tokens)) or _has_phrase(tokens, (("save", "as"),))


# --------------------------------------------------------------------------
# System changes: types and addresses
# --------------------------------------------------------------------------

# Never opened, zipped or renamed to (the spec's list, then other types Windows runs or installs).
BLOCKED_TYPES = frozenset({
    "exe", "msi", "bat", "cmd", "ps1", "psm1", "vbs", "vbe", "js", "jse", "wsf", "wsh", "hta", "reg", "scr", "com",
    "cpl", "msc", "jar", "py", "pyw", "appx", "msix", "appinstaller", "application", "appref-ms", "library-ms",
    "search-ms", "settingcontent-ms", "url",
    "pif", "scf", "msp", "mst", "msu", "inf", "gadget", "ps1xml", "psc1", "psd1", "ws", "wsc", "chm", "hlp", "xbap",
    "diagcab", "appxbundle", "msixbundle"})
# Never created by Jarvis (a rename or a new file): shortcuts and internet shortcuts.
NEVER_CREATE_TYPES = frozenset({"lnk", "url"})
# What open_file opens at once (SPEC 2.3); anything else not blocked is "show in folder" only.
OPENABLE_TYPES = frozenset({
    "docx", "odt", "rtf", "txt", "md", "csv", "pdf", "xlsx", "ods", "pptx", "odp",
    "jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "tif", "tiff",
    "mp3", "m4a", "wav", "flac", "ogg",
    "mp4", "mov", "mkv", "avi", "webm"})
# Documents that can run macros or scripts: opened only from a card click ("can run macros / scripts"). The old
# binary Office types (doc, xls, ppt) can carry macros too (owner, 2026-10-21).
SCRIPT_CAPABLE_TYPES = frozenset({"docm", "dotm", "xlsm", "xltm", "xlsb", "xlam", "pptm", "potm", "ppsm", "ppam",
                                  "doc", "xls", "ppt", "html", "htm", "xhtml", "svg", "mht", "mhtml"})
OPEN_DOCUMENT = "document"              # opens at once
OPEN_SCRIPT = "script"                  # opens only from a card click
OPEN_SHORTCUT = "shortcut"              # a .lnk: only when it resolves to an approved app or a document (shown)
OPEN_BLOCKED = "blocked"                # never
OPEN_OTHER = "other"                    # never opened; Show in folder only
ALLOWED_URL_SCHEMES = ("https",)        # file: javascript: data: ms-msdt: search-ms: ms-settings: ... never
# The one fixed Settings page Jarvis's own Do Not Disturb chip opens (Phase 2): never accepted from a plan.
DND_OPENER_URI = "ms-settings:notifications"
URL_SCHEME = "scheme"
URL_ADDRESS = "address"


def blocked_type(name: Any) -> bool:
    return suffix(name) in BLOCKED_TYPES


def may_create_type(name: Any) -> bool:
    """A new name's type may be created: never a blocked type, a shortcut or an internet shortcut."""
    kind = suffix(name)
    return kind not in BLOCKED_TYPES and kind not in NEVER_CREATE_TYPES


def open_kind(name: Any) -> str:
    """How a file may be opened: OPEN_DOCUMENT, OPEN_SCRIPT, OPEN_SHORTCUT, OPEN_BLOCKED or OPEN_OTHER."""
    kind = suffix(name)
    if kind in BLOCKED_TYPES:
        return OPEN_BLOCKED
    if kind in OPENABLE_TYPES:
        return OPEN_DOCUMENT
    if kind in SCRIPT_CAPABLE_TYPES:
        return OPEN_SCRIPT
    if kind == "lnk":
        return OPEN_SHORTCUT
    return OPEN_OTHER


def url_problem(url: Any) -> str:
    """"" for an https address on a public host (actions.web_link_problem: no user name, port 443, no dot
    segments, not this PC or its network); URL_SCHEME for any other scheme, URL_ADDRESS for anything else wrong."""
    if not isinstance(url, str):
        return URL_SCHEME
    try:
        scheme = urlsplit(url.strip()).scheme.casefold()
    except ValueError:
        return URL_ADDRESS
    if scheme not in ALLOWED_URL_SCHEMES:
        return URL_SCHEME
    return URL_ADDRESS if web_link_problem(url) else ""


# --------------------------------------------------------------------------
# Deleting and overwriting
# --------------------------------------------------------------------------

MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_COPY_ALLOWED = 0x2
MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
# A move never overwrites, never crosses a volume (v1) and never waits for a reboot.
FORBIDDEN_MOVE_FLAGS = MOVEFILE_REPLACE_EXISTING | MOVEFILE_COPY_ALLOWED | MOVEFILE_DELAY_UNTIL_REBOOT
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_WANTNUKEWARNING = 0x4000
FOFX_RECYCLEONDELETE = 0x00080000      # defined by value (SPEC 6.2)
# The Recycle Bin only, with Undo, and Windows' own warning when a file would skip the bin.
REQUIRED_RECYCLE_FLAGS = FOFX_RECYCLEONDELETE | FOF_ALLOWUNDO | FOF_WANTNUKEWARNING
FORBIDDEN_RECYCLE_FLAGS = FOF_NOCONFIRMATION
COPY_FILE_FAIL_IF_EXISTS = 0x1
REQUIRED_COPY_FLAGS = COPY_FILE_FAIL_IF_EXISTS


def move_flags_ok(flags: int) -> bool:
    return not int(flags) & FORBIDDEN_MOVE_FLAGS


def recycle_flags_ok(flags: int) -> bool:
    flags = int(flags)
    return flags & REQUIRED_RECYCLE_FLAGS == REQUIRED_RECYCLE_FLAGS and not flags & FORBIDDEN_RECYCLE_FLAGS


def copy_flags_ok(flags: int) -> bool:
    return int(flags) & REQUIRED_COPY_FLAGS == REQUIRED_COPY_FLAGS


# --------------------------------------------------------------------------
# Never a target or a place
# --------------------------------------------------------------------------

# Never opened and never a target (exe names, casefolded), by group.
NEVER_OPEN_APPS: dict[str, frozenset[str]] = {
    "terminal": frozenset({"cmd.exe", "powershell.exe", "powershell_ise.exe", "pwsh.exe", "windowsterminal.exe",
                           "wt.exe", "openconsole.exe", "conhost.exe", "wsl.exe", "wslhost.exe", "bash.exe",
                           "mintty.exe", "git-bash.exe", "git-cmd.exe", "python.exe", "pythonw.exe", "py.exe",
                           "node.exe"}),
    "script_host": frozenset({"wscript.exe", "cscript.exe", "mshta.exe", "rundll32.exe", "regsvr32.exe",
                              "msbuild.exe", "installutil.exe"}),
    "system": frozenset({"regedit.exe", "regedt32.exe", "control.exe", "mmc.exe", "systemsettings.exe",
                         "msconfig.exe", "taskmgr.exe", "sechealthui.exe", "useraccountcontrolsettings.exe",
                         "netsh.exe", "sc.exe", "bcdedit.exe", "diskpart.exe", "manage-bde.exe", "dism.exe",
                         "wusa.exe", "gpedit.exe", "secpol.exe", "compmgmtlauncher.exe", "eventvwr.exe"}),
    "installer": frozenset({"msiexec.exe", "appinstaller.exe", "winget.exe", "setup.exe", "install.exe",
                            "uninstall.exe", "unins000.exe"}),
    "claude": frozenset({"claude.exe"}),
    "password_manager": frozenset({"1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "dashlane.exe",
                                   "lastpass.exe", "nordpass.exe", "keeperpasswordmanager.exe", "roboform.exe",
                                   "enpass.exe", "proton pass.exe"}),
    "money": frozenset({"exodus.exe", "ledger live.exe", "trezor suite.exe", "tws.exe", "terminal64.exe",
                        "thinkorswim.exe", "webull desktop.exe"}),
    "remote": frozenset({"mstsc.exe", "msrdc.exe", "anydesk.exe", "teamviewer.exe", "rustdesk.exe", "parsecd.exe",
                         "vncviewer.exe", "quickassist.exe", "vmware.exe", "vmplayer.exe", "virtualbox.exe",
                         "virtualboxvm.exe", "vmconnect.exe"}),
    # The camera, the microphone and the screen (SPEC 2.4's "Other"): an app that records or captures them.
    "capture": frozenset({"windowscamera.exe", "soundrec.exe", "soundrecorder.exe", "voicerecorder.exe",
                          "snippingtool.exe", "screensketch.exe", "screenclippinghost.exe", "gamebar.exe",
                          "gamebarftserver.exe", "obs64.exe", "obs32.exe", "obs.exe", "psr.exe", "bdcam.exe",
                          "camtasiastudio.exe", "camrecorder.exe", "sharex.exe", "clipchamp.exe", "loom.exe"}),
}
# Opened when the owner names them, but never an automation target (in-control mode never works in them).
NEVER_TARGET_APPS: dict[str, frozenset[str]] = {
    "ide": frozenset({"code.exe", "cursor.exe", "windsurf.exe", "devenv.exe", "pycharm64.exe", "idea64.exe",
                      "rider64.exe", "webstorm64.exe", "clion64.exe", "studio64.exe", "eclipse.exe",
                      "sublime_text.exe", "notepad++.exe"}),
    "explorer": frozenset({"explorer.exe"}),
}
# Start-menu names (and window classes' apps) of the same groups, as words.
NEVER_APP_WORDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "terminal": _phrases("terminal", "cmd", "command prompt", "powershell", "windows terminal", "wsl", "ubuntu",
                         "debian", "kali linux", "opensuse", "git bash", "python", "node js",
                         "developer command prompt", "run dialog"),       # the Start menu's Run (its id's words)
    "system": _phrases("registry editor", "control panel", "services", "task scheduler", "task manager", "settings",
                       "windows security", "group policy", "local security policy", "system configuration",
                       "computer management", "event viewer", "windows update", "bitlocker", "firewall",
                       "device manager", "disk management", "environment variables", "windows tools",
                       "administrative tools"),
    "installer": _phrases("installer", "install", "setup", "uninstall", "app installer", "winget"),
    "claude": _phrases("claude", "claude code"),
    "password_manager": _phrases("1password", "bitwarden", "keepass", "keepassxc", "dashlane", "lastpass",
                                 "nordpass", "keeper", "roboform", "enpass", "proton pass", "password manager"),
    "money": _phrases("bank", "banking", "trading", "trader", "broker", "brokerage", "crypto", "wallet", "coinbase",
                      "binance", "kraken", "metamask", "exodus", "ledger live", "trezor", "robinhood", "etoro",
                      "trading 212", "interactive brokers", "metatrader", "thinkorswim", "webull", "paypal",
                      "revolut", "monzo"),
    "remote": _phrases("remote desktop", "remote desktop connection", "anydesk", "teamviewer", "rustdesk",
                       "parsec", "vnc", "quick assist", "vmware", "virtualbox", "hyper v", "virtual machine"),
    # The camera, the microphone and the screen: "Camera", "Sound Recorder" (once "Voice Recorder"), "Snipping Tool"
    # (once "Snip & Sketch", its id "ScreenSketch"), "Xbox Game Bar" (its id "XboxGamingOverlay"), "OBS Studio",
    # "Steps Recorder" and the like - never opened, whatever the request's words say.
    "capture": _phrases("camera", "webcam", "sound recorder", "voice recorder", "audio recorder", "screen recorder",
                        "screen recording", "snipping tool", "snip sketch", "screen sketch", "game bar",
                        "gaming overlay", "obs", "obs studio", "steps recorder", "bandicam", "camtasia", "sharex",
                        "clipchamp", "loom"),
    "jarvis": _phrases("jarvis", "jarvis assistant", "briefing reader"),     # Jarvis's own: never opened again
}
NEVER_TARGET_WORDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "ide": _phrases("visual studio", "visual studio code", "vs code", "vscode", "cursor", "windsurf", "pycharm",
                    "intellij", "rider", "webstorm", "clion", "android studio", "eclipse", "sublime text"),
    "explorer": _phrases("file explorer", "explorer"),
}
SECURITY_MANDATORY_MEDIUM_RID = 0x2000
MAX_INTEGRITY_RID = SECURITY_MANDATORY_MEDIUM_RID     # a window above Medium integrity is elevated: never a target
# Known folders (backend names) whose whole subtree is never a place.
EXCLUDED_KNOWN_FOLDERS = ("windows", "program_files", "program_files_x86", "program_data", "local_appdata",
                          "roaming_appdata")
# Never a place when right below a drive (casefolded).
EXCLUDED_ROOT_NAMES = frozenset({
    "windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "system volume information",
    "recovery", "config.msi", "$winreagent", "$windows.~bt", "$windows.~ws", "windows.old", "perflogs", "msocache",
    "$sysreset", "documents and settings", "onedrivetemp"})
# Never a place at any depth (casefolded).
EXCLUDED_NAMES = frozenset({"appdata", "$recycle.bin", "system volume information", "windowsapps"})
# A folder holding (or named) one of these is a code folder: never a place (this covers the Jarvis project).
CODE_MARKERS = (".git", ".hg", ".svn", ".env", "venv", ".venv", "node_modules")
# Folders of keys and credentials: never a place.
SECRET_DIR_NAMES = frozenset({".ssh", ".gnupg", ".aws"})
# Secret-shaped files: never listed and never touched (casefolded fnmatch patterns; Jarvis's own Google sign-in
# files too).
SECRET_FILE_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "*.ppk", "id_rsa*", "id_dsa*",
                        "id_ecdsa*", "id_ed25519*", "*.kdbx", "*.kdb", "credentials*.json", "token*.json",
                        "client_secret*.json", "google_token*.json", "*client_secret*.json", ".git-credentials",
                        ".netrc", "_netrc", ".npmrc", ".pypirc", "wallet.dat")


def _exe_name(exe_path: Any) -> str:
    if not isinstance(exe_path, str):
        return ""
    return exe_path.replace("/", "\\").rsplit("\\", 1)[-1].strip().casefold()


def never_app(name: Any, exe_path: Any = "", *, target: bool = False) -> str:
    """The group an app belongs to when Jarvis may never open it ("terminal", "password_manager", ...; "" when he
    may). ``target=True`` also refuses the apps that may be opened but never worked in (IDEs, Explorer)."""
    exe = _exe_name(exe_path)
    groups = [NEVER_OPEN_APPS, NEVER_TARGET_APPS] if target else [NEVER_OPEN_APPS]
    for table in groups:
        for group, names in table.items():
            if exe and exe in names:
                return group
    for table in ([NEVER_APP_WORDS, NEVER_TARGET_WORDS] if target else [NEVER_APP_WORDS]):
        for group, phrases in table.items():
            if _says(name, phrases):
                return group
    return ""


def elevated(integrity_rid: int | None) -> bool:
    """A window whose process runs above Medium integrity, or whose level could not be read, is elevated."""
    return integrity_rid is None or int(integrity_rid) > MAX_INTEGRITY_RID


def secret_file(name: Any) -> bool:
    """A secret-shaped file name (.env, a key, a password database, credentials or token JSON ...)."""
    if not isinstance(name, str):
        return False
    base = unicodedata.normalize("NFKC", strip_hidden(name)).rstrip(". ").casefold()
    base = base.replace("/", "\\").rsplit("\\", 1)[-1]
    return any(fnmatch.fnmatchcase(base, pattern) for pattern in SECRET_FILE_PATTERNS)


# A task may read text files (agent.owner), so a text file NAMED for secrets stays out of it too, whatever its type.
# Words that are also ordinary pieces of words ("secretary", "tokenomics", "classnotes", "environment", "monkey",
# "vaulted") count only as whole words, digits gone ("token2") and the name split at everything but letters and at
# camelCase ("myTokens"):
_SENSITIVE_FILE_WORDS = frozenset({"password", "passwords", "passwd", "passcode", "passcodes", "passphrase", "secret",
                                  "secrets", "credential", "credentials", "cred", "creds", "token", "tokens", "apikey",
                                  "apikeys", "privatekey", "2fa", "otp", "seedphrase", "pin", "pins", "mypin", "mypins",
                                  "ssn", "passport", "iban", "cvv", "wallet", "login", "logins", "account", "accounts",
                                  "env", "pwd", "pwds", "passwrd", "seedwords", "seedword", "mysecrets", "mysecret",
                                  "mypasscodes", "mypasscode", "logindetails"})
# A file's, never a folder's: note apps call theirs a vault, and a folder of boarding passes is one.
_SENSITIVE_FILE_ONLY = frozenset({"vault", "vaults", "pass", "pw", "pws"})
_SENSITIVE_FILE_PHRASES = _phrases("api key", "api keys", "private key", "private keys", "backup code", "backup codes",
                                   "recovery code", "recovery codes", "seed phrase", "seed words", "seed word",
                                   "recovery words", "recovery word", "card number", "card numbers", "bank details",
                                   "social security", "sign in", "log in")
# ... and these long, unmistakable ones anywhere in a name, written as one word or not ("mypasswords",
# "bitwarden_export"):
_SENSITIVE_PARTS = ("password", "passwd", "credential", "apikey", "privatekey", "privkey", "mnemonic", "seedphrase",
                    "backupcode", "recoverycode", "recoverykey", "bitwarden", "lastpass", "1password", "keepass",
                    "dashlane", "netrc", "dotenv", "serviceaccount", "adminsdk", "kubeconfig", "accesskey", "rootkey",
                    "creditcard", "sshkey", "recoveryphrase")
_KEY_OWNERS = frozenset({"aws", "gcp", "gcloud", "azure", "ssh", "api", "private", "root"})   # "key" beside these
_RECOVERY_THINGS = frozenset({"code", "codes", "key", "keys", "phrase", "phrases"})           # "recovery" beside these
_KEY_TYPES = frozenset({"pem", "key", "pfx", "p12", "ppk", "kdbx"})     # before the last extension: "my.pem.txt"
_DIGITS_RE = re.compile(r"\d+")


def _beside(tokens: list[str], word: tuple[str, ...], others: frozenset[str]) -> bool:
    """One of ``word`` stands next to one of ``others`` in ``tokens``."""
    return any(token in word and others & {*tokens[max(0, index - 1):index], *tokens[index + 1:index + 2]}
               for index, token in enumerate(tokens))


def _name_says_secret(name: str, *, folder: bool) -> bool:
    plain = name.translate(_LOOKALIKES)
    for tokens in _readings(plain):
        bare = [word for word in (_DIGITS_RE.sub("", token) for token in tokens) if word]
        said = {*tokens, *bare}
        if _SENSITIVE_FILE_WORDS & said or (not folder and _SENSITIVE_FILE_ONLY & said):
            return True
        if _has_phrase(tokens, _SENSITIVE_FILE_PHRASES) or _has_phrase(bare, _SENSITIVE_FILE_PHRASES):
            return True
        if _beside(bare, ("key", "keys"), _KEY_OWNERS) or _beside(bare, ("recovery",), _RECOVERY_THINGS):
            return True
        joined = "".join(tokens)
        if any(part in joined or part in "".join(bare) for part in _SENSITIVE_PARTS):
            return True
    base = plain.rstrip(". ")
    pieces = base.casefold().split(".")
    stem = base.rsplit(".", 1)[0] if len(pieces) > 1 and not folder else base
    if [_DIGITS_RE.sub("", word) for word in words(stem)] in (["key"], ["keys"]):
        return True
    return not folder and any(piece.strip() in _KEY_TYPES for piece in pieces[:-1])


def sensitive_file_name(name: Any) -> bool:
    """A file's name says it holds secrets or sign-ins ("My passwords.txt", "api keys.md", "accounts.csv", "token2.txt",
    "bitwarden_export.json", "prod.env.txt", "my.pem.txt"): Jarvis reads none of them for a task, whatever its type, on
    top of ``secret_file``. "secretary_letter.docx", "recovery_plan.docx" and "keyboard_notes.txt" are not."""
    if secret_file(name):
        return True
    return isinstance(name, str) and _name_says_secret(name, folder=False)


def sensitive_folder_name(name: Any) -> bool:
    """A folder's name says what it holds is secrets or sign-ins ("Passwords", "Logins", "recovery codes"): the same
    words as a file's, but for "vault": note apps call their folder that ("Obsidian Vault")."""
    return isinstance(name, str) and _name_says_secret(name, folder=True)


# What plainly is a key or a password in a text a task would read (scrub_secrets): the value goes, the text stays. A
# private key's block, a well-known key's prefix, a password in an address, a line naming a secret with a value that
# looks like one, and a sentence saying what a strong secret is.
SECRET_MARK = "[left out: looks like a key or a password]"
_KEY_HEADER_RE = re.compile(r"-----BEGIN [A-Z0-9 ]{0,32}PRIVATE KEY(?: BLOCK)?-----|PuTTY-User-Key-File-\d")
_KEY_END_RE = re.compile(r"-----END [A-Z0-9 ]{0,32}PRIVATE KEY(?: BLOCK)?-----")
_PUTTY_END_RE = re.compile(r"Private-(?:MAC|Hash):[^\n]*")
# A JWT's lookbehind is its own: its class holds - and _, so with the others' a run of eyJ- would be tried at every eyJ.
_KEY_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9])(?:sk-ant-[A-Za-z0-9_\-]{8,}|sk-proj-[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9]{32,}"
                            r"|sk_live_[A-Za-z0-9]{8,}|sk_test_[A-Za-z0-9]{8,}|rk_live_[A-Za-z0-9]{8,}"
                            r"|whsec_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{16}(?![0-9A-Z])"
                            r"|ghp_[A-Za-z0-9]{16,}|gho_[A-Za-z0-9]{16,}|ghs_[A-Za-z0-9]{16,}|ghu_[A-Za-z0-9]{16,}"
                            r"|ghr_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|glpat-[A-Za-z0-9_\-]{16,}"
                            r"|npm_[A-Za-z0-9]{16,}|hf_[A-Za-z0-9]{16,}|gsk_[A-Za-z0-9]{16,}"
                            r"|SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{8,}|ya29\.[A-Za-z0-9_\-]{16,}"
                            r"|xox[abeoprs]-[A-Za-z0-9\-]{8,}|AIza[0-9A-Za-z_\-]{30,})"
                            r"|(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]+)?")
# A password in an address: scheme://user:password@, the user maybe none, the password up to the last @ before the path.
_ADDRESS_PASSWORD_RE = re.compile(r"://[^/\s:@?#]{0,256}:([^/\s?#]{1,256})@")
# In a link's query only these count: a bare token=, auth= or pwd= is how marketing mail and a meeting's link are
# written.
_QUERY_SECRET_RE = re.compile(r"[?&;](?:password|passwd|secret|client_secret|api_key|apikey)=([^&#;\s\"']+)",
                              re.IGNORECASE)
# A name (1), what may stand between it and its mark (2: a bracket, #1, a star, "for bob@example.com"), its mark (3) and
# the first word of its value (4).
_SECRET_LINE_RE = re.compile(r"([A-Za-z][A-Za-z0-9_.\-/ ]{0,63})"
                             r"(\s{0,4}(?:\([^()\n]{0,40}\)|\[[^\[\]\n]{0,40}\]|#\d{1,4}|\*{1,3}"
                             r"|for\s[^\s:=@]{1,64}@[^\s:=]{1,64}))?"
                             r"[\"']?\s{0,40}(=>|->|:=|[:=|])\s{0,40}[\"']?"
                             r"((?:(?:[Bb]earer|[Bb]asic|[Tt]oken|[Aa]pi[Kk]ey)[ \t]{1,4})?[^\s\"',;]{1,400})")
_LABEL_MARK_RE = re.compile(r"=>|->|:=|[:=|]")
# What must follow a mark for a label to end in it: a value's first character ("pin : ," has none). (":=" and "=>" have
# one in their second character, as ":" and "=".)
_VALUE_AFTER_RE = re.compile(r"\s{0,40}[\"']?[^\s\"',;]")
_NAME_REACH, _VALUE_REACH = 240, 480             # as far as the pattern can reach before a mark and after it
# The longest a label can be from its name's start to its value's first character (64 + 137 + 1 + 40 + 2 + 40 + 1 + 1):
# a search of a stretch that found none rules out a label starting more than this before the stretch's end. A stretch
# is this long at least, so that what such a search looks at again is little.
_LONGEST_LABEL, _STRETCH = 286, 4096
# What may stand right before a label's mark, past its spaces (a name's own, 63 at most, and the pattern's 40): a
# name's last character, a bracket's, a star's, a digit's ("#1"), a quote, or within 65 after an "@" the end of "for
# bob@example.com". A mark after anything else ends no label ("aaa#:a"), so it is not searched round.
_BEFORE_MARK = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-/)]*\"'")
_SPACES_BEFORE_MARK = 103
_VALUE_END_RE = re.compile(r"[^\s\"',;]*")      # a value past the pattern's reach goes whole
_LINK_END_RE = re.compile(r"[^\s\"']*")
_VALUE_STOPS = " \t\r\n\"',;"
_JOINED_RE = re.compile(r"(?:[,;][^\s\"',;]+)+")    # "Hun,Hunter22": a label's value with a mark in it
_VALUE_GOES_ON_RE = re.compile(r" [A-Z0-9]|[,;][^\s\"',;]")   # after a first word: a digit group, or that
# Digit groups: "4111 1111 1111 1111", "078 05 1120", and an IBAN's letters after its first group. A group ends at
# anything but a letter or a digit ("789#", '1111"').
_DIGIT_GROUPS_RE = re.compile(r"(?:(?<=[A-Z]{2}\d{2}) [A-Z]{4}(?=\s\d))?"
                              r"(?: (?=[A-Z]{0,7}\d)[A-Z0-9]{1,8}(?![A-Za-z0-9])){1,12}")
_DIGITS_IN_GROUPS = 6
_SECRET_VALUE_MIN, _LONG_VALUE_MIN, _SHORT_VALUE_MIN = 6, 16, 3
# Not a secret whatever the label: an http or https address (a password in it goes on its own), an amount, and under a
# card's or an account's label a masked number. An amount is the whole value: a currency's mark or code (a country's
# letters before a $) or word and digits, maybe a sign or a bracket before it and a mark after it ("($120.00)",
# "US$120.00", "Rs 500/-"), a range ("$20-30", "$130-$165") or an amount a person, a night or a month ("$25/person");
# after the digits, a mark or a code only after a decimal or thousands mark ("120.00 USD", "120.00\u20ac", "1,250 EUR").
# "$3cur3Pass", "$5/Hunter" and "483920$" are none, and so is a whole number before a code or a word: "PIN: 4821
# dollars" and "One-time password: 483920 USD" still go.
_MONEY_CODE, _MONEY_MARK = r"USD|EUR|GBP|INR|AUD|CAD|CHF|JPY|NZD|SGD|AED|Rs\.?|RS\.?", "[$\u20ac\u00a3\u00a5\u20b9]"
_MONEY_WORD = r"(?i:dollars?|pounds?|euros?|rupees?|bucks|pence|cents?)"
_MONEY_COUNTRY = r"(?:US|CA|AU|CDN|NZ|HK|NT|MX|A|C|S|R)?"        # before a $: US$, CA$, A$, R$ ...
_PER_UNIT = (r"(?i:/(?:person|pp|head|adult|child|kid|guest|couple|family|night|day|week|wk|month|mo|year|yr|hour|hr"
             r"|session|class|lesson|seat|ticket|unit|item|user|member|student|kg|lb))")
_LINK_RE = re.compile(r"[<(]?(?i:https?://)")
_AMOUNT_RE = re.compile(r"[(\-+<\[]?"
                        rf"(?:(?:{_MONEY_COUNTRY}{_MONEY_MARK}|(?:{_MONEY_CODE})(?![A-Za-z])|{_MONEY_WORD}\s)"
                        r"\s?\d[\d,.]*"
                        rf"(?:\s?[-\u2013]\s?{_MONEY_COUNTRY}(?:{_MONEY_MARK})?\d[\d,.]*)?"
                        rf"|\d[\d,]*[.,]\d+\s?(?:{_MONEY_MARK}|(?:{_MONEY_CODE})(?![A-Za-z]))"
                        r")"
                        rf"{_PER_UNIT}?"
                        r"(?:/-|\)[.,]?|[.,\]>])?(?=[\s\"',;]|\Z)")
# Masked: two marks or more (or four, one a mark), then four digits at most and maybe a full stop, a comma or a bracket.
_MASKED_RE = re.compile(r"([*xX\u2022\u00b7#\-.]{2,})\d{0,4}[.,)]?")
_MASK_MARKS = "*xX\u2022\u00b7#"
# "The Secret 5-Minute Trick", "Pin 3rd-party apps": a word, for a line with no mark.
_WORD_WITH_A_NUMBER_RE = re.compile(r"(?i:\d+(?:st|nd|rd|th)?-[a-z]+)")
# Past the marks of any line read: the bar holds 32,767 characters, and a part of a file or a kept message is read from
# its reach and margin before it (scrub_part), 12,000 + 1,232 at most.
_LABELS_PER_LINE_CAP = 20_000
_KEY_NAMES = _phrases("api key", "apikey", "api token", "private key", "privatekey", "access key", "accesskey", "auth",
                      "authorization", "bearer", "credential", "credentials", "token", "client secret", "secret key")
_NOT_A_SECRET_VALUE = _phrases("expiry", "expiration", "exp date", "routing")  # sensitive_label's, with plain values
# A postal code is no secret: "PIN code: 560001", "ZIP: 94103". Only as the label's own name: at its end (maybe before
# "no" or "number"), and with no word of a card's, a lock's or a password's before it: "Card PIN code: 482913" and "Zip
# password: ..." are secrets.
_POSTAL_NAMES = _phrases("pin code", "pincode", "postal code", "postcode", "post code", "zip", "zip code", "zipcode")
_POSTAL_TAILS = frozenset({"no", "number", "num"})
_PIN_OWNERS = frozenset({"card", "atm", "debit", "credit", "bank", "account", "login", "upi", "sim", "phone", "door",
                         "lock", "safe", "alarm", "gate", "garage", "voicemail"})
# Labels of a task's reading only, beside sensitive_label's, which the desktop's fields use: Indian identity and bank
# forms. "PAN" counts only in capitals and as the name's last word or before No, Number, Card or "is": a frying pan is
# no number, and nor is a "PAN India launch".
_AC_LABELS = _phrases("a c no", "a c number", "ac no")
_MORE_SECRET_LABELS = (*_phrases("mpin", "tpin", "aadhaar", "aadhar", "ifsc", "voter id", "voterid"), *_AC_LABELS)
_PAN_RE = re.compile(r"\bPAN\b(?:[\s._/-]*(?i:no|number|num|card|is)\b\.?){0,3}[\s.]*$")
# Under these, a short value goes: 3 characters holding a digit ("PIN: 4821", "CVV: 123").
_SHORT_LABELS = _phrases("pin", "mpin", "tpin", "cvv", "cvv2", "cvc", "cvc2", "otp", "passcode", "security code",
                         "securitycode")
_SHORT_TAILS = frozenset({"number", "no", "num", "code", "is"})          # "PIN number", "OTP code", "PIN is"
# A masked number is one only under a card's or an account's label, never a password's.
_NUMBER_NAMES = _phrases("card", "cardnumber", "account", "accountnumber", "acct", "a c", "ac no", "iban", "aadhaar",
                         "aadhar", "ssn", "social security", "national insurance", "passport", "voter id", "voterid",
                         "pan")
_PASSWORD_NAMES = _phrases("password", "passwd", "pwd", "passcode", "passphrase", "pin", "pincode", "mpin", "tpin",
                           "otp", "one time", "onetime", "totp", "2fa", "mfa", "cvv", "cvv2", "cvc", "cvc2",
                           "security code", "securitycode", "verification code", "secret", "token", "key", "pass", "pw")
# "pass" and "pw" are a password's name alone, joined to the word before by "_" (DB_PASS), or after a sign-in word;
# otherwise only a value of 16 characters or more goes ("Parking pass: P1234567" reads).
_PASS_WORDS = _phrases("pass", "pw")
_SIGN_IN_WORDS = frozenset({"wifi", "db", "user", "login", "admin", "root", "email", "account", "router", "network"})
_JOINED_PASS_RE = re.compile(r"[A-Za-z0-9]_(?:pass|pw)\s*$", re.IGNORECASE)
_EVERYDAY_PASSES = _phrases("boarding pass", "bus pass", "rail pass", "train pass", "travel pass", "transit pass",
                            "metro pass", "season pass", "day pass", "ski pass", "gym pass", "guest pass",
                            "visitor pass", "festival pass", "hall pass")
_SHORT, _SECRET, _PASS = "short", "secret", "pass"   # what a label takes: a short value, a value, only a long one
_LABEL_WORDS = (*SENSITIVE_WORDS, *_MORE_SECRET_LABELS)
# What a name must hold for it to say anything (every phrase's first word): most names need no further reading.
_NAME_HINT_RE = re.compile("|".join(sorted({re.escape(phrase[0]) for phrase in (
    *SENSITIVE_WORDS, *_KEY_NAMES, *_MORE_SECRET_LABELS, *_PASS_WORDS, ("key",), ("pan",)) if phrase not in _AC_LABELS},
    key=len, reverse=True)) + r"|a\W{0,3}c\W{0,3}n", re.IGNORECASE)
# A sentence saying what a strong secret is: "Your OTP is 483920", "the password is Hunter22Zq", "login code = 483920",
# "Your OTP code is 483920", "Your PIN number is 4821", "Your one-time password (OTP) is 483920". Its name stands at a
# line's start or after a space, a bracket, a quote, a star or a mark: never in a link's query. A PIN's "code" is a
# postal code's ("Your PIN code is 560001"). Each run of spaces is read once (possessive): a long one with no value
# after it costs its length, not its square.
_SENTENCE_RE = re.compile(r"(?<![^\s(\[\"'\u201c\u2018*>:])"
                          r"(?:(?:password|passcode|mpin|otp|cvv)(?:[ \t]++(?:code|number|no)\b\.?)?"
                          r"|pin(?:[ \t]++(?:number|no)\b\.?)?|one[ \t-]?time[ \t]++(?:code|password)"
                          r"|(?:verification|security|log[ \t-]?in|sign[ \t-]?in|access|authentication|2fa)[ \t]++code)"
                          r"(?:[ \t]{0,4}+\([^()\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]{0,40}+\))?"
                          r"(?:[ \t]++is\b[ \t]*+:?|[ \t]*+=)[ \t]*+[\"'\u201c\u2018]?([^\s\"',;\u201d\u2019]{1,400})",
                          re.IGNORECASE)
_SENTENCE_END = ".!?)"
_NOT_ASCII_RE = re.compile(r"[^\x00-\x7f]")
_LINE_WORD_RE = re.compile(r"\S+")
_BARE_NAME_WORDS = 5                                  # a label with no mark is a few words at a line's start
_LABEL_END_MARKS = ":=|*>-"


def _ends_with(tokens: list[str], phrases: tuple[tuple[str, ...], ...]) -> bool:
    return any(len(tokens) >= len(phrase) and tuple(tokens[-len(phrase):]) == phrase for phrase in phrases)


def _pass_kind(name: str, readings: tuple[list[str], list[str]]) -> str:
    """What a name ending in "pass" or "pw" takes (``readings``: its _readings), but for an everyday pass: alone
    ("Pass", "PW"), joined by "_" to the word before (DB_PASS) or after a sign-in word ("WiFi pass") it is a
    password's (_SECRET); otherwise only a long value (_PASS: "Parking pass: P1234567" reads); "" for none."""
    kind = ""
    for tokens in readings:
        if not _ends_with(tokens, _PASS_WORDS) or _ends_with(tokens, _EVERYDAY_PASSES):
            continue
        before = tokens[:-1]
        if not before or before[-1] in _SIGN_IN_WORDS or before[-2:] == ["wi", "fi"] \
                or _JOINED_PASS_RE.search(name) is not None:
            return _SECRET
        kind = _PASS
    return kind


def _short_label(tokens: list[str]) -> bool:
    """These words end in a name a short value is a secret under (_SHORT_LABELS), maybe before "number", "code" or
    "is"."""
    return any(_ends_with(tokens[:cut], _SHORT_LABELS) and _SHORT_TAILS.issuperset(tokens[cut:])
               for cut in range(len(tokens), max(0, len(tokens) - 2) - 1, -1))


def _postal_name(readings: tuple[list[str], list[str]]) -> bool:
    """A name that is a postal code's own (``readings``: its _readings): it ends in one (maybe before "no" or
    "number"), and no word of a card's, a lock's or a password's stands before it ("Card PIN code", "Zip password")."""
    for tokens in readings:
        cut = len(tokens)
        while cut and tokens[cut - 1] in _POSTAL_TAILS:
            cut -= 1
        for phrase in _POSTAL_NAMES:
            if cut >= len(phrase) and tuple(tokens[cut - len(phrase):cut]) == phrase:
                before = tokens[:cut - len(phrase)]
                if _PIN_OWNERS.isdisjoint(before) and not _has_phrase(before, _LABEL_WORDS):
                    return True
    return False


@lru_cache(maxsize=4096)            # a long text names the same few labels again and again
def _label_kind(name: str, *, bare: bool = False) -> str:
    """What the name of a label takes, for what a task reads: a short value (_SHORT: "PIN", "CVV"), a value (_SECRET:
    sensitive_label's words and these), only a long one (_PASS), or nothing (""): a postal code's name, or one whose
    value is plain (an expiry date). ``bare``: the label has no mark after it, so it must end in the secret's name, and
    a short value never goes."""
    readings = _readings(name)
    if any(_has_phrase(tokens, _NOT_A_SECRET_VALUE) for tokens in readings) or _postal_name(readings):
        return ""
    if bare:
        named = any(_ends_with(tokens, _LABEL_WORDS) for tokens in readings)
    else:                            # sensitive_label's words, and these
        named = any(_has_phrase(tokens, _LABEL_WORDS) for tokens in readings)
    if named or _PAN_RE.search(name) is not None:
        return _SHORT if not bare and any(_short_label(tokens) for tokens in readings) else _SECRET
    return _pass_kind(name, readings)


def _names_a_key(name: str) -> bool:
    """A name a long value is a key beside: _KEY_NAMES', and "<word> key" (OPENAI_KEY, AccountKey)."""
    return _says(name, _KEY_NAMES) or any(len(tokens) >= 2 and tokens[-1] == "key" for tokens in _readings(name))


def _masked(name: str, value: str) -> bool:
    """``value`` is a masked number under a card's or an account's label ``name`` ("****4242.", "XX5678"), never under a
    password's."""
    shown = _MASKED_RE.fullmatch(value)
    if shown is None or not _says(name, _NUMBER_NAMES) or _says(name, _PASSWORD_NAMES):
        return False
    marks = sum(char in _MASK_MARKS for char in shown.group(1))
    return marks >= 2 or (marks and len(shown.group(1)) >= 4)


def _plain_value(name: str, line: str, start: int, end: int) -> bool:
    """What stands at ``start`` (its first word to ``end``) is no secret whatever its label ``name`` says: an address,
    an amount, a masked number under a card's or an account's label."""
    return (_LINK_RE.match(line, start) is not None or _AMOUNT_RE.match(line, start) is not None
            or _masked(name, line[start:end]))


def _looks_secret(value: str, kind: str = _SECRET) -> bool:
    if kind == _PASS:
        return len(value) >= _LONG_VALUE_MIN
    digit = any(char.isdigit() for char in value)
    if kind == _SHORT and len(value) >= _SHORT_VALUE_MIN and digit:
        return True
    return len(value) >= _SECRET_VALUE_MIN and (len(value) >= _LONG_VALUE_MIN or digit)


def _label_value_end(line: str, start: int, end: int) -> int:
    """Where a secret label's value really ends: past the pattern's reach, across digit groups (_DIGITS_IN_GROUPS digits
    in them, on the line), and across a mark inside it when it holds a letter ("Hun,Hunter22")."""
    end = _VALUE_END_RE.match(line, end).end()
    groups = _DIGIT_GROUPS_RE.match(line, end)
    if groups is not None and sum(char.isdigit() for char in line[start:groups.end()]) >= _DIGITS_IN_GROUPS:
        return groups.end()
    joined = _JOINED_RE.match(line, end)
    if joined is not None and any(char.isalpha() for char in line[start:end]):
        return joined.end()
    return end


def _value_end(name: str, between: str | None, mark: str, line: str, start: int, end: int) -> int:
    """Where the value whose first word stands at ``line[start:end]`` ends, after the label ``name`` (``between`` and
    its ``mark``), or 0 when that is no secret."""
    if _NAME_HINT_RE.search(name) is None or _plain_value(name, line, start, end):
        return 0
    kind = _label_kind(name)
    least = _SHORT_VALUE_MIN if kind == _SHORT else _SECRET_VALUE_MIN
    if end - start < least and _VALUE_GOES_ON_RE.match(line, end) is None:
        return 0                                         # too short to be one, whatever its name says: no need to read it
    if kind:
        end = _label_value_end(line, start, end)
        return end if _looks_secret(line[start:end], kind) else 0
    if between is None and mark in (":", "=") and _names_a_key(name) and end - start >= _LONG_VALUE_MIN \
            and not line[start:end].isdigit():
        return _VALUE_END_RE.match(line, end).end()
    return 0


def _secret_end(line: str, match: re.Match[str]) -> int:
    """Where the value a label before it names ends, or 0 when that is no secret. A value the stretch looked at cut
    short is read to its end."""
    name, between, mark, _value = match.groups()
    start, end = match.span(4)
    if end == match.endpos:
        end = _VALUE_END_RE.match(line, end).end()
    return _value_end(name, between, mark, line, start, end)


def _labelled_values(line: str, until: int | None = None) -> list[tuple[int, int]]:
    """Where the values of ``line``'s labels stand, anywhere along it (``until``: of the marks up to there only). What
    was read as a value may be a label itself ("FYI: password: ...", "login=bob:password=..."), so reading goes on from
    a value's start; but for a link, whose query counts only as _QUERY_SECRET_RE reads it. Each read looks at the
    stretch from a label's reach before the next mark that a value can follow, for the first label in it, and moves
    on; a stretch that holds none moves it on past where one could start."""
    found: list[tuple[int, int]] = []
    at = look = 0                                        # where a label may start; where the next mark is looked for
    for _read in range(_LABELS_PER_LINE_CAP):
        mark = _LABEL_MARK_RE.search(line, look)
        if mark is None or (until is not None and mark.start() > until):
            break
        look = mark.end()
        if mark.end() - mark.start() == 1 or mark.group() == "->":
            if _VALUE_AFTER_RE.match(line, mark.end()) is None:
                continue                                 # no label ends in it: a later mark's stretch starts as far back
        if not _name_may_end(line, mark.start()):
            continue                                     # (the same)
        begin = max(at, mark.start() - _NAME_REACH)
        stop = max(mark.end() + _VALUE_REACH, begin + _STRETCH)
        match = _SECRET_LINE_RE.search(line, begin, stop)
        if match is None:
            if stop >= len(line):
                break                                    # none starts anywhere from there on
            at = look = max(mark.end(), stop - _LONGEST_LABEL)
            continue
        start = match.start(4)
        if line.startswith("//", start):
            at = look = _LINK_END_RE.match(line, start).end()
            found += [query.span(1) for query in _QUERY_SECRET_RE.finditer(line, start, at)
                      if _looks_secret(query.group(1))]
            continue
        end = _secret_end(line, match)
        if end:
            found.append((start, end))
        at = look = end or start
    return found


def _name_may_end(line: str, at: int) -> bool:
    """A label's name (or what may stand between it and its mark) may end before a mark at ``at`` in ``line``
    (_BEFORE_MARK)."""
    before = at
    while before > 0 and line[before - 1].isspace():
        before -= 1
        if at - before > _SPACES_BEFORE_MARK:
            return False
    if before == 0:
        return False
    return line[before - 1] in _BEFORE_MARK or "@" in line[max(0, before - 66):before]


def _marked_end(line: str) -> str:
    """``line`` up to its end, past spaces and the stars or underscores of bold, when it ends in a label's mark (":",
    "=", "|"); else ""."""
    ending = line.rstrip().rstrip("*_").rstrip()
    return ending if ending.endswith((":", "=", "|")) else ""


def _said_before_its_value(ending: str) -> bool:
    """``ending`` (a line up to the mark it ends in) may end a sentence's words before its value: "... is:", "... =". """
    if ending.endswith("="):
        return True
    words = ending[:-1].rstrip(" \t")
    return words[-2:].casefold() == "is" and not words[-3:-2].isalnum()


def _line_labels(lines: list[str], starts: list[int], number: int, until: int | None) -> list[tuple[int, int]]:
    """_labelled_values of line ``number`` (``until``: of the marks up to there only), as places in the text. A line
    ending in a label and its mark is read with the next line's first word after it (one blank line at most between),
    as on one line: "Your temporary password is:" then "Drovers88x"; and so is a sentence ending in its mark, for the
    value it gives there ("Your one-time password (OTP) is:" then "483920")."""
    line, here = lines[number], starts[number]
    ending = _marked_end(line)
    for later in range(number + 1, min(number + 3, len(lines)) if ending else 0):
        word = _LINE_WORD_RE.search(lines[later])
        if word is None:
            continue
        joined = len(ending) + 1
        there = starts[later] + word.start() - joined             # where the joined line's places past it stand
        reach = joined if until is None else min(joined, until)
        text = ending + " " + lines[later][word.start():]
        spans = _labelled_values(text, reach)
        if (until is None or until >= joined) and _said_before_its_value(ending):   # (one on the line: _line_values')
            spans += [span for span in _sentence_values(text, joined + len(word.group())) if span[0] >= joined]
        return [(here + start if start < joined else there + start, here + end if end <= len(ending) else there + end)
                for start, end in spans]
    return [(here + start, here + end) for start, end in _labelled_values(line, until)]


def _said_secret(value: str) -> bool:
    """A value a sentence gives a strong secret is one: it holds a digit (3 characters or more), or it is 8 or more
    mixing letters with a symbol or a capital inside, after a small letter or a digit ("case-sensitive",
    "Case-Sensitive", "EXPIRING" and "Wednesday" are words)."""
    if len(value) >= _SHORT_VALUE_MIN and any(char.isdigit() for char in value):
        return True
    if len(value) < 8 or not any(char.isalpha() for char in value):
        return False
    return (any(not char.isalnum() and char not in "-'\u2019" for char in value)
            or any(char.isupper() and (before.islower() or before.isdigit()) for before, char in zip(value, value[1:])))


def _sentence_values(line: str, until: int | None = None) -> list[tuple[int, int]]:
    """Where the values of ``line``'s sentences of the form "<a strong secret's name> is <value>" ("is:", "=") stand
    (``until``: of those ending before there; ``line`` may be a whole text, for a sentence never runs past its line's
    end): "Your OTP is valid for 10 minutes" gives none."""
    found: list[tuple[int, int]] = []
    for said in _SENTENCE_RE.finditer(line, 0, len(line) if until is None else min(len(line), until)):
        start, end = said.span(1)
        if _plain_value("", line, start, end):
            continue
        end = _label_value_end(line, start, end)
        while end > start and line[end - 1] in _SENTENCE_END:
            end -= 1                                     # the sentence's own mark stays
        if _said_secret(line[start:end]):
            found.append((start, end))
    return found


def _bare_value(name: str, kind: str, line: str, start: int) -> int:
    """Where a value with no mark before it, starting at ``start``, ends after the label ``name`` that takes ``kind``,
    or 0 when it is no secret."""
    end = _VALUE_END_RE.match(line, start).end()
    if _plain_value(name, line, start, end):
        return 0
    end = _label_value_end(line, start, end)
    return end if _looks_secret(line[start:end], kind) else 0


def _next_line_value(lines: list[str], number: int, name: str) -> list[tuple[int, int, int]]:
    """The first word of the next line with anything on it, one blank line at most, as the value of line ``number``,
    which is only the label ``name``: [(that line's number, where the value starts and ends)], or [] when it is none. A
    word with a number ("5-Minute") is a word."""
    for later in range(number + 1, min(number + 3, len(lines))):
        line = lines[later]
        word = _LINE_WORD_RE.search(line)
        if word is None:
            continue
        if _WORD_WITH_A_NUMBER_RE.fullmatch(word.group()) is not None:
            return []
        end = _bare_value(name, _label_kind(name, bare=True), line, word.start())
        return [(later, word.start(), end)] if end else []
    return []


def _bare_values(line: str, lines: list[str], number: int) -> list[tuple[int, int, int]]:
    """Values with no mark before them, as _next_line_value says it: after a label at the start of line ``number``, a
    word holding a digit (a table's cells: "Password Hunter22"); after a line that is only a label, with no mark at its
    end (that is _line_labels'), the next line's first word."""
    found: list[tuple[int, int, int]] = []
    if _NAME_HINT_RE.search(line[:200]) is None:
        return found
    words = list(islice(_LINE_WORD_RE.finditer(line), _BARE_NAME_WORDS + 1))
    named: list[str] = []
    for word in words:
        shown = word.group()
        if named and any(char.isdigit() for char in shown):
            kind = _label_kind(" ".join(named), bare=True)
            if named[-1][-1] in ":=|" or not kind or _WORD_WITH_A_NUMBER_RE.fullmatch(shown) is not None:
                return found                     # (one with a mark is _labelled_values')
            end = _bare_value(" ".join(named), kind, line, word.start())
            return [*found, (number, word.start(), end)] if end else found
        named.append(shown)
    name = " ".join(named).rstrip(_LABEL_END_MARKS)
    if named and len(words) <= _BARE_NAME_WORDS and not _marked_end(line) and _label_kind(name, bare=True):
        found += _next_line_value(lines, number, name)
    return found


def _line_values(text: str, *, first_whole: bool = True, until: int | None = None) -> list[tuple[int, int]]:
    """Where the values line by line stand in ``text`` (_sentence_values over the whole of it, _line_labels,
    _bare_values). ``first_whole``: its first line starts at its start (a part of a file may start inside one).
    ``until``: only what a label whose mark, or a sentence whose words, stand before there names. A line with nothing
    on it gives nothing (a value under a label is read from the label's line), and a line with no mark no label."""
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    found = _sentence_values(text, until)
    for number, line in enumerate(lines):
        here = starts[number]
        if until is not None and here > until:
            break
        if line.isspace():
            continue
        if _LABEL_MARK_RE.search(line) is not None:
            found += _line_labels(lines, starts, number, None if until is None else until - here)
        if number or first_whole:
            found += [(starts[later] + start, starts[later] + end)
                      for later, start, end in _bare_values(line, lines, number)]
    return found


def _key_values(text: str) -> list[tuple[int, int]]:
    """Where the keys of ``text`` stand, whatever line they are on: a private key's block from its header to its end (or
    the end of the text), a well-known key's prefix with its token, a password in an address."""
    found: list[tuple[int, int]] = []
    at = 0
    while (header := _KEY_HEADER_RE.search(text, at)) is not None:
        closing = (_KEY_END_RE if header.group().startswith("-") else _PUTTY_END_RE).search(text, header.end())
        if closing is None:
            found.append((header.start(), len(text)))
            break
        found.append((header.start(), closing.end()))
        at = closing.end()
    found += [key.span() for key in _KEY_PREFIX_RE.finditer(text)]
    found += [address.span(1) for address in _ADDRESS_PASSWORD_RE.finditer(text)]
    return found


def _invisible_spots(text: str) -> list[int]:
    """Where the characters a task's reader never sees stand in ``text`` (format_chars.invisible_char): web.clean_text
    drops them, so the words they break up are read as they will arrive."""
    if text.isascii():
        return []
    return [found.start() for found in _NOT_ASCII_RE.finditer(text) if invisible_char(found.group())]


def _without_spots(text: str, spots: list[int]) -> str:
    if not spots:
        return text
    pieces, at = [], 0
    for spot in spots:
        pieces.append(text[at:spot])
        at = spot + 1
    pieces.append(text[at:])
    return "".join(pieces)


def _on_the_text(spans: list[tuple[int, int]], spots: list[int]) -> list[tuple[int, int]]:
    """``spans`` of a text without ``spots``, on the text with them: in order, none touching the next."""
    moved = [spot - number for number, spot in enumerate(spots)]
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        first, last = start + bisect_right(moved, start), end + bisect_right(moved, end - 1)
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def _marked(text: str, spans: list[tuple[int, int]], first: int, last: int) -> tuple[str, int]:
    """``text[first:last]`` with ``spans`` as one mark each, however little of one falls in it. How many."""
    pieces: list[str] = []
    at, count = first, 0
    for start, end in spans:
        start, end = max(start, first), min(end, last)
        if start < end:
            pieces += [text[at:start], SECRET_MARK]
            at, count = end, count + 1
    pieces.append(text[at:last])
    return "".join(pieces), count


def scrub_secrets(text: Any) -> tuple[str, int]:
    """``text`` with what plainly is a key or a password taken out, each as ``SECRET_MARK``, and how many: the value
    goes, never the message. A private key's block whole, from its header to its end or the end of the text; a
    well-known key's token (sk-ant-, sk-proj-, AKIA, ghp_, xox, a JWT ...); a password in an address, the rest of the
    address staying; a label's value (``API_KEY=...``, ``"password": "..."``, ``Authorization: Bearer ...``), anywhere
    along a line, the label staying; after a line ending in a label and its mark, the next line's first word, as on one
    line. With no mark too: a label at a line's start before a word holding a digit, a line that is only a label before
    the next line's first word. Under a PIN's, a CVV's, an OTP's or a passcode's label a value of 3 characters holding
    a digit goes. A sentence saying what a strong secret is ("Your OTP is 483920", "the password is Hunter22Zq") loses
    its value. In a link's query only ``password=``, ``passwd=``, ``secret=``, ``client_secret=`` and ``api_key=``
    count. A value that is an http or https address or an amount, or a masked number under a card's or an account's
    label, is no secret, whatever its label; nor is a postal code. Not a promise about everything: a password in a
    longer sentence stays what it is. Read as it will arrive, invisible characters aside. Bounded: every pattern looks
    at a bounded stretch, or a run it then moves past."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else "", 0
    spots = _invisible_spots(text)
    plain = _without_spots(text, spots)
    return _marked(text, _on_the_text([*_key_values(plain), *_line_values(plain)], spots), 0, len(text))


def scrub_part(text: str, first: int, last: int, margin: int) -> tuple[str, int]:
    """``text[first:last]`` as scrub_secrets would give it read in its place, and how many, for a part of something
    longer. A block or a token is looked for from the text's start, so one that began before the part still goes; a
    line's labels from ``margin`` and a label's reach before ``first``, and the label of the value standing at
    ``first`` from ``margin`` before that value's start, so a long value's tail goes too; and both ``margin`` past the
    part's end, so what the end cuts is still seen. ``first`` and ``last`` stay places in ``text``."""
    last = min(last, len(text))
    head = text[:last + margin]
    spots = _invisible_spots(head)
    plain = _without_spots(head, spots)
    at = first - bisect_left(spots, first)
    near = max(0, at - margin - _NAME_REACH - _VALUE_REACH)
    spans = [*_key_values(plain), *_lines_from(plain, near)]
    run = max(plain.rfind(char, 0, at) for char in _VALUE_STOPS) + 1
    back = max(0, run - margin)
    if back < near:                  # that value began before what is read: its own label, and only its own, from there
        spans += _lines_from(plain, back, until=run + _LONGEST_LABEL)
    return _marked(text, _on_the_text(spans, spots), first, last)


def _lines_from(plain: str, start: int, until: int | None = None) -> list[tuple[int, int]]:
    """_line_values of ``plain`` from ``start`` on (``until``: of what starts before there), as places in ``plain``."""
    whole = start == 0 or plain[start - 1] in "\r\n"
    return [(start + first, start + last) for first, last in
            _line_values(plain[start:], first_whole=whole, until=None if until is None else until - start)]


def holds_secret(text: Any) -> bool:
    """``text`` plainly holds a key or a password: scrub_secrets would take something out of it."""
    return scrub_secrets(text)[1] > 0


# --------------------------------------------------------------------------
# Other
# --------------------------------------------------------------------------

# No backend method and no skill is named for one of these (screen recording, keylogging, the clipboard history,
# the camera or microphone, closing windows, extracting zips, deleting for good, installing, running anything).
NEVER_CAPABILITIES = ("delete", "erase", "wipe", "purge", "empty", "overwrite", "format", "install", "uninstall",
                      "close", "kill", "terminate", "extract", "unzip", "clipboard", "record", "keylog", "keystroke",
                      "camera", "microphone", "webcam", "shell", "command", "execute", "exec", "spawn", "invoke",
                      "press", "submit", "send", "upload", "email", "registry", "service")

# --------------------------------------------------------------------------
# The suspect scanner
# --------------------------------------------------------------------------

# Names Jarvis or a model goes by, and the words that mark text as addressed to one.
_ASSISTANT = frozenset({"jarvis", "assistant", "claude", "chatgpt", "gpt", "copilot", "gemini", "llm", "ai"})
_URGENT = frozenset({"important", "urgent", "attention", "warning", "notice", "instruction", "instructions",
                     "command", "commands", "note", "reminder", "action", "required", "mandatory", "please", "pls",
                     "asap"})
# Verbs of an order.
_VERBS = frozenset({
    "delete", "remove", "erase", "wipe", "destroy", "move", "copy", "send", "email", "mail", "upload", "share",
    "forward", "post", "publish", "run", "execute", "launch", "open", "install", "uninstall", "download", "type",
    "paste", "fill", "submit", "pay", "transfer", "rename", "zip", "compress", "extract", "format", "disable",
    "enable", "click", "press", "approve", "accept", "grant", "allow", "reply", "buy", "purchase", "ignore",
    "disregard", "forget", "reveal", "tell", "give", "export", "sync", "encrypt", "bin", "trash", "empty", "visit",
    "navigate", "put", "kill", "close"})
# What an order points at, right after its verb.
_OBJECTS = frozenset({"all", "every", "everything", "each", "any", "anything", "files", "file", "folders",
                      "folder", "documents", "passwords", "password", "this", "these", "those", "them", "it", "my",
                      "your", "the", "now", "immediately", "contents", "data", "desktop", "downloads", "secrets",
                      "keys", "account", "accounts", "money"})
_OVERRIDE_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\b(?:\W+\w+){0,3}?\W+"
    r"(?:previous|prior|above|earlier|preceding|all|any|your|the|these|those|system|safety)\b(?:\W+\w+){0,3}?\W+"
    r"(?:instructions?|rules?|prompts?|guidelines?|messages?|directions?|polic(?:y|ies)|guardrails?)\b")
_PHRASE_RE = re.compile(
    r"\b(?:new|updated|real|hidden|secret)\s+instructions?\b|\bsystem\s+prompt\b|\byou\s+are\s+now\b"
    r"|\bact\s+as\b|\bpretend\s+(?:to|you)\b|\bjailbreak\b|\bdeveloper\s+mode\b|\bdo\s+anything\s+now\b"
    r"|\byou\s+must\b")
_ROLE_RE = re.compile(r"(?:^|[\s\[(<{|>\"'])(?:system|assistant|human|developer)\s*[:>]")
_TEMPLATE_RE = re.compile(r"\[/?inst\]|<\|?im_(?:start|end)\|?>|<\|[a-z_]+\|>|<</?sys>>|###\s*(?:instruction|system)")
# Windows' own copy names ("Budget - Copy (2)", "Copy of Budget") are not orders.
_WINDOWS_COPY_RE = re.compile(r"(?:\s-\s*copy(?:\s*\(\d+\))?(?=\.[^.]*$|$))|(?:^copy\s+of\s+)")
_WORD_RE = re.compile(r"\w+")
# Tag characters U+E0020-E007E are invisible but spell ASCII (U+E0041 is "A"): the scanner reads what they spell too.
_TAG_OFFSET = 0xE0000
_TAG_FIRST, _TAG_LAST = 0xE0020, 0xE007E
# Cyrillic and Greek letters that look like Latin ones, read as those Latin letters (the common look-alikes only).
_LOOKALIKES = str.maketrans(
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0458\u0455\u0501\u04cf\u043a"
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0423\u0425\u0406\u0408\u0405"
    "\u03b1\u03b9\u03ba\u03bd\u03bf\u03c1\u03c4\u03c5\u03c7"
    "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7",
    "aeopcyxijsdlk" "ABEKMHOPCTYXIJS" "aikvoptux" "ABEZHIKMNOPTYX")
_LOOKALIKE_CHARS = frozenset(chr(code) for code in _LOOKALIKES)


def suspect_text(text: Any) -> bool:
    """True when ``text`` (a name, title, label or cell written by someone else) reads like an order to Jarvis or a
    model: an instruction override ("ignore previous instructions"), a role or prompt marker ("system:", "[INST]"),
    a phrase like "you must" or "new instructions", an assistant's name with an order verb, an urgent word with an
    order verb, or an order verb that opens the text and points at something ("Delete all files", "move everything
    to ..."). What invisible tag characters spell is read as well, Cyrillic and Greek look-alike letters are read as
    Latin, and a word mixing Latin letters with such a look-alike ("Jarvis" with a Cyrillic a) is suspect by itself.
    Ordinary names ("Copy of Budget.xlsx", "Move-in checklist.pdf", "Important dates.docx") pass."""
    if not isinstance(text, str) or not text.strip():
        return False
    spelled = _untag(text)
    return _reads_like_an_order(text) or (spelled != text and _reads_like_an_order(spelled))


def _untag(text: str) -> str:
    """``text`` with its tag characters read as the ASCII they spell."""
    if text.isascii():
        return text
    return "".join(chr(ord(char) - _TAG_OFFSET) if _TAG_FIRST <= ord(char) <= _TAG_LAST else char for char in text)


def _reads_like_an_order(text: str) -> bool:
    plain = unicodedata.normalize("NFKC", strip_hidden(text))
    for word in _WORD_RE.findall(plain):
        if any(char in _LOOKALIKE_CHARS for char in word) and any(char.isascii() and char.isalpha() for char in word):
            return True                          # a Latin word with a Cyrillic or Greek look-alike letter in it
    lowered = _CAMEL_RE.sub(" ", plain.translate(_LOOKALIKES)).casefold()
    if _OVERRIDE_RE.search(lowered) or _PHRASE_RE.search(lowered) or _ROLE_RE.search(lowered) \
            or _TEMPLATE_RE.search(lowered):
        return True
    lowered = _WINDOWS_COPY_RE.sub(" ", lowered)
    tokens = [word for word in _NON_WORD_RE.split(lowered) if word]
    if not tokens:
        return False
    verbs = [i for i, token in enumerate(tokens) if token in _VERBS]
    if not verbs:
        return False
    if _ASSISTANT & set(tokens) or _URGENT & set(tokens[:verbs[0]]):
        return True
    # An order verb at the start (after numbering or a date) that points at something.
    start = 0
    while start < len(tokens) and tokens[start].isdigit():
        start += 1
    if start < len(tokens) and tokens[start] in _VERBS:
        rest = tokens[start + 1:start + 3]
        return any(token in _OBJECTS for token in rest)
    return False


# The strong signals (strong_order): what an ordinary page does not say. A web page is full of opening order verbs
# ("Find a store", "Open all day"), of "you must" and of "System: Windows 11": those are left to the names and labels
# above.
_STRONG_PHRASE_RE = re.compile(
    r"\bsystem\s+prompt\b|\bdo\s+anything\s+now\b|\bjailbreak\s+mode\b|\bdeveloper\s+mode\s+(?:enabled|activated)\b"
    r"|\b(?:your|these\s+are\s+your)\s+(?:new|real|actual|updated|true)\s+instructions?\b"
    r"|\b(?:new|updated|real|hidden|secret)\s+instructions?\s*:"
    r"|\byou\s+are\s+now\s+(?:in\s+)?(?:developer|admin|god|jailbreak|unrestricted|dan)\b")
_STRONG_ROLE_RE = re.compile(
    r"(?:^|[\n\[(<{|>\"'])\s*(?:system|assistant|human|user|developer)\s*:\s*"
    r"(?:you\b|your\b|ignore\b|disregard\b|forget\b|from\s+now\b|always\b|never\b|do\s+not\b|don't\b|please\b|send\b"
    r"|email\b|search\b|open\b|fetch\b|go\s+to\b|visit\b|tell\b|reveal\b)"
    r"|</?(?:system|assistant|human|developer)\s*>|\[/?(?:system|assistant|human)\]")
_STRONG_TEMPLATE_RE = re.compile(
    r"\[/?inst\]|<\|?im_(?:start|end)\|?>|<\|(?:system|user|assistant|endoftext)\|>|<</?sys>>"
    r"|###\s*(?:instructions?|system)\s*:?\s*$")
_STRONG_NAME_RE = re.compile(
    r"^\W*(?:important\W+|urgent\W+|attention\W+|hey\s+|dear\s+|note\s+to\s+|message\s+to\s+)*(?:the\s+)?"
    r"(?:jarvis|claude|chatgpt|gemini|copilot|ai\s+assistant|ai\s+agent|ai\s+model|llm|language\s+model)\s*[,:]"
    r"|\bjarvis\b\W+(?:please\s+)?(?:" + "|".join(sorted(_VERBS)) + r")\b"
    r"|\bif\s+you\s+are\s+an?\s+(?:ai|llm|language\s+model|assistant|agent|bot)\b"
    r"|\b(?:assistants?|agents?|models?|bots?|llms?|ais?)\s+"
    r"(?:reading|viewing|summari[sz]ing|processing|browsing|visiting)\s+this\b")
# Jarvis's own markers (agent.runner, agent.tools): what a page or a mail has no business saying, as strong a sign as
# any.
_STRONG_MARK_RE = re.compile(
    r"\bthe\s+owner\s+(?:says|answers\s+your\s+question)\s*:|</?\s*untrusted\b|\bmcp__jarvis__"
    r"|\b(?:open_page|web_search|fetch_text|propose_action|propose_files|open_thing|pc_control|mail_read|file_read)\b")
_TAG_RUN_MIN = 8                 # tag characters in a row spelling this much are a hidden message (a flag emoji has fewer)


def hidden_message(text: Any) -> bool:
    """True when ``text`` holds a run of invisible tag characters long enough to spell something (a hidden or look-alike
    trick; a flag emoji's few are not one). Read before such characters are stripped for showing."""
    if not isinstance(text, str) or text.isascii():
        return False
    run = 0
    for char in text:
        run = run + 1 if _TAG_FIRST <= ord(char) <= _TAG_LAST else 0
        if run >= _TAG_RUN_MIN:
            return True
    return False


def strong_order(text: Any) -> bool:
    """For web pages, whose ordinary words are full of opening order verbs: True only on a strong signal - an
    instruction override, a role or prompt marker, a model's template, words addressed by name to Jarvis or a model,
    Jarvis's own markers ("The owner says:", the untrusted block's mark, its tools' names), a long run of hidden tag
    characters, or a word mixing Latin letters with a look-alike. ``suspect_text`` stays as it is for short names and
    labels, whose bar is lower."""
    if not isinstance(text, str) or not text.strip():
        return False
    if hidden_message(text):
        return True
    plain = unicodedata.normalize("NFKC", strip_hidden(text))
    for word in _WORD_RE.findall(plain):
        if any(char in _LOOKALIKE_CHARS for char in word) and any(char.isascii() and char.isalpha() for char in word):
            return True
    lowered = plain.translate(_LOOKALIKES).casefold()
    return any(pattern.search(lowered) for pattern in (_OVERRIDE_RE, _STRONG_PHRASE_RE, _STRONG_ROLE_RE,
                                                       _STRONG_TEMPLATE_RE, _STRONG_NAME_RE, _STRONG_MARK_RE))


# Every NEVER constant; each one has its own unit test (tests/test_desktop_guard.py: test_never_<name>).
NEVER_CONSTANTS = (
    "UIA_IS_PASSWORD_PROPERTY", "SENSITIVE_WORDS", "PAYMENT_WORDS", "INVOKE_ALLOWED", "COMMIT_WORDS", "NEVER_KEYS",
    "CONSENT_WORDS", "FILE_PICKER_WORDS", "BLOCKED_TYPES", "NEVER_CREATE_TYPES", "SCRIPT_CAPABLE_TYPES",
    "ALLOWED_URL_SCHEMES", "DND_OPENER_URI", "FORBIDDEN_MOVE_FLAGS", "REQUIRED_RECYCLE_FLAGS",
    "FORBIDDEN_RECYCLE_FLAGS", "REQUIRED_COPY_FLAGS", "NEVER_OPEN_APPS", "NEVER_TARGET_APPS", "NEVER_APP_WORDS",
    "NEVER_TARGET_WORDS", "MAX_INTEGRITY_RID", "EXCLUDED_KNOWN_FOLDERS", "EXCLUDED_ROOT_NAMES", "EXCLUDED_NAMES",
    "CODE_MARKERS", "SECRET_DIR_NAMES", "SECRET_FILE_PATTERNS", "NEVER_CAPABILITIES")
