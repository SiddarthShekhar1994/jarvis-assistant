"""The board's messages, both ways (the new look, part 3; PLAN section 3, DESIGN-SPEC section 8). Qt-free and pure: no
file, no log, no network; nothing here raises but Refused.

In (app -> page): check_message(message, known) -> Checked
    A message is a dict of known fields of the right types, rebuilt here field by field (never the caller's object), and
    its JSON (json.dumps, ASCII only, no NaN or Infinity) is at most MESSAGE_CAP bytes. A "picture" message may also carry
    ONE image: a data:image/jpeg;base64 URL of at most PICTURE_CAP bytes that decodes to a JPEG of at most PICTURE_BYTES.
    Strings are kept exactly as given, control and bidi characters too: the page sets every one as text (textContent),
    never as markup. The types:
        frame    {"id", "kind", "title", "meta", "body", "status", "status_text", "status_meta", "waits", "changed",
                  "pinned", "closed"}: adds a frame (it draws in) or updates the one with that id in place. "body" is the
                  kind's own (KINDS: one checker per kind, as the page has one renderer per kind); "status" ok / failed
                  (status_text says what failed; the page offers Try again) / empty (status_text, status_meta "Checked
                  9:44 AM"); "waits" the words of its amber chip ("" = it waits on nothing).
        lit      {"id"}: the frame Jarvis is talking about ("" = none)
        remove   {"id"}; clear {}
        motion   {"level": lively / calm / off}
        picture  {"id", "src"}: the frame's one picture
    The ids of lit, remove and picture must be frames the app sent (``known``); a new frame past MAX_FRAMES is refused.

Out (page -> app): check_request(kind, payload, known) -> Request
    kind one of REQUEST_KINDS; payload a small JSON object (at most PAYLOAD_CAP characters, no NaN or Infinity) of that
    kind's fields only; every frame id one the app sent (``known``):
        ready {"v"?} | layout {"frames": [{"id", "top", "height"}, ...], "scrolling"} | pin {"id", "on"}
        | focus, open, close, bring_back, retry, go_approvals {"id"}
    Anything else is refused. A log line may carry kind_word(kind) and the reason word, never the payload.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

MESSAGE_CAP = 32 * 1024          # bytes of JSON in one message (a picture's image apart)
PICTURE_CAP = 410 * 1024         # bytes of the one data: URL a picture message may carry
PICTURE_BYTES = 300 * 1024       # bytes of the JPEG in it (the app re-encodes to at most this)
PICTURE_PREFIX = "data:image/jpeg;base64,"
PAYLOAD_CAP = 4096               # characters of a request's payload
MAX_FRAMES = 24                  # frames on the board at once (DESIGN-SPEC 3.5)
ID_CAP = 40
TITLE_CAP = 60                   # characters (DESIGN-SPEC 3.5: title 60, text 400)
META_CAP = 40
TEXT_CAP = 400
WORDS_CAP = 160                  # what failed, or what is empty
WAITS_CAP = 60
PLACE_LIMIT = 1_000_000          # px: a layout's top / height beyond this is not a place

MESSAGE_TYPES = ("frame", "lit", "remove", "clear", "motion", "picture")
STATUSES = ("ok", "failed", "empty")
MOTION_LEVELS = ("lively", "calm", "off")
REQUEST_KINDS = ("ready", "layout", "focus", "open", "close", "pin", "bring_back", "retry", "go_approvals")
_ID_KINDS = frozenset({"focus", "open", "close", "bring_back", "retry", "go_approvals"})
_ID = re.compile(r"[A-Za-z0-9_-]{1,%d}" % ID_CAP)
_BASE64 = re.compile(r"[A-Za-z0-9+/]*={0,2}")
_JPEG = b"\xff\xd8\xff"

# Reason words (a log line may carry them; never a value).
R_SHAPE = "shape"                # not a dict / not the right type of field
R_FIELD = "field"                # a field the type does not have, or one it needs is missing
R_TYPE = "type"                  # an unknown message type
R_KIND = "kind"                  # an unknown frame kind / request kind
R_ID = "id"                      # not an id, or not a frame the app sent
R_TOO_LONG = "too_long"          # a string over its cap
R_SIZE = "size"                  # the JSON over MESSAGE_CAP / the payload over PAYLOAD_CAP
R_PICTURE = "picture"            # not one JPEG data: URL within the caps
R_JSON = "json"                  # a payload that is not plain JSON
R_FULL = "full"                  # a new frame past MAX_FRAMES


class Refused(ValueError):
    """A message or request that does not pass; ``reason`` is one of the R_* words."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Checked:
    """A message that passed: its type, the JSON text the page gets, and the frame id it is about ("" = none)."""

    type: str
    text: str
    frame_id: str = ""

    def __repr__(self) -> str:   # never the text
        return f"Checked(type={self.type!r}, bytes={len(self.text)}, id={self.frame_id!r})"


@dataclass(frozen=True)
class Request:
    """A request of the page that passed. ``places``: a layout's (id, top, height) per frame, in the view's px."""

    kind: str
    frame_id: str = ""
    on: bool = False
    places: tuple[tuple[str, int, int], ...] = ()
    scrolling: bool = False


def kind_word(kind: Any) -> str:
    """What a log line may say a request was: its kind when it is on the list, else "unknown"."""
    return kind if isinstance(kind, str) and kind in REQUEST_KINDS else "unknown"


# --------------------------------------------------------------------------
# Fields
# --------------------------------------------------------------------------

def _fields(value: Any, allowed: Collection[str], required: Collection[str] = ()) -> dict[str, Any]:
    if type(value) is not dict:
        raise Refused(R_SHAPE)
    for key in value:
        if type(key) is not str or key not in allowed:
            raise Refused(R_FIELD)
    if any(key not in value for key in required):
        raise Refused(R_FIELD)
    return value


def _text(value: Any, cap: int) -> str:
    if type(value) is not str:
        raise Refused(R_SHAPE)
    if len(value) > cap:
        raise Refused(R_TOO_LONG)
    return value


def _flag(value: Any) -> bool:
    if type(value) is not bool:
        raise Refused(R_SHAPE)
    return value


def _choice(value: Any, choices: Collection[str]) -> str:
    if type(value) is not str or value not in choices:
        raise Refused(R_FIELD)
    return value


def _frame_id(value: Any) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise Refused(R_ID)
    return value


def _known_id(value: Any, known: Collection[str]) -> str:
    frame_id = _frame_id(value)
    if frame_id not in known:
        raise Refused(R_ID)
    return frame_id


# --------------------------------------------------------------------------
# The kinds: one body checker per kind (the page has one renderer per kind)
# --------------------------------------------------------------------------

def _text_body(body: Any) -> dict[str, Any]:
    """The generic kind: a frame of plain text (title and meta are the frame's own)."""
    fields = _fields(body, ("text",))
    return {"text": _text(fields.get("text", ""), TEXT_CAP)}


KINDS: dict[str, Callable[[Any], dict[str, Any]]] = {"text": _text_body}


# --------------------------------------------------------------------------
# In: the app's messages
# --------------------------------------------------------------------------

_FRAME_FIELDS = ("type", "id", "kind", "title", "meta", "body", "status", "status_text", "status_meta", "waits",
                 "changed", "pinned", "closed")


def _frame(value: dict[str, Any], known: Collection[str]) -> tuple[dict[str, Any], str]:
    fields = _fields(value, _FRAME_FIELDS, ("type", "id", "kind", "title"))
    frame_id = _frame_id(fields["id"])
    if frame_id not in known and len(known) >= MAX_FRAMES:
        raise Refused(R_FULL)
    kind = fields["kind"]
    if type(kind) is not str or kind not in KINDS:
        raise Refused(R_KIND)
    clean = {
        "type": "frame", "id": frame_id, "kind": kind,
        "title": _text(fields["title"], TITLE_CAP),
        "meta": _text(fields.get("meta", ""), META_CAP),
        "body": KINDS[kind](fields.get("body", {})),
        "status": _choice(fields.get("status", "ok"), STATUSES),
        "status_text": _text(fields.get("status_text", ""), WORDS_CAP),
        "status_meta": _text(fields.get("status_meta", ""), META_CAP),
        "waits": _text(fields.get("waits", ""), WAITS_CAP),
        "changed": _flag(fields.get("changed", False)),
        "pinned": _flag(fields.get("pinned", False)),
        "closed": _flag(fields.get("closed", False)),
    }
    return clean, frame_id


def _picture(value: dict[str, Any], known: Collection[str]) -> tuple[dict[str, Any], str]:
    fields = _fields(value, ("type", "id", "src"), ("type", "id", "src"))
    frame_id = _known_id(fields["id"], known)
    src = fields["src"]
    if type(src) is not str or len(src) > PICTURE_CAP or not src.startswith(PICTURE_PREFIX):
        raise Refused(R_PICTURE)
    data = src[len(PICTURE_PREFIX):]
    if not data or len(data) % 4 or not _BASE64.fullmatch(data):
        raise Refused(R_PICTURE)
    try:
        jpeg = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise Refused(R_PICTURE) from None
    if len(jpeg) > PICTURE_BYTES or not jpeg.startswith(_JPEG):
        raise Refused(R_PICTURE)
    return {"type": "picture", "id": frame_id, "src": src}, frame_id


def check_message(message: Any, known: Collection[str] = ()) -> Checked:
    """``message`` as the page gets it (see the module doc), or Refused. ``known``: the ids of the frames the app sent."""
    if type(message) is not dict:
        raise Refused(R_SHAPE)
    kind = message.get("type")
    if type(kind) is not str or kind not in MESSAGE_TYPES:
        raise Refused(R_TYPE)
    image = 0
    frame_id = ""
    if kind == "frame":
        clean, frame_id = _frame(message, known)
    elif kind == "picture":
        clean, frame_id = _picture(message, known)
        image = len(clean["src"])
    elif kind in ("lit", "remove"):
        fields = _fields(message, ("type", "id"), ("type", "id"))
        if kind == "lit" and fields["id"] == "":
            clean = {"type": "lit", "id": ""}
        else:
            frame_id = _known_id(fields["id"], known)
            clean = {"type": kind, "id": frame_id}
    elif kind == "clear":
        _fields(message, ("type",))
        clean = {"type": "clear"}
    else:   # motion
        fields = _fields(message, ("type", "level"), ("type", "level"))
        clean = {"type": "motion", "level": _choice(fields["level"], MOTION_LEVELS)}
    try:
        text = json.dumps(clean, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        raise Refused(R_SHAPE) from None
    if len(text) - image > MESSAGE_CAP:
        raise Refused(R_SIZE)
    return Checked(kind, text, frame_id)


# --------------------------------------------------------------------------
# Out: the page's requests
# --------------------------------------------------------------------------

def _no_constants(name: str) -> Any:
    raise Refused(R_JSON)   # NaN, Infinity, -Infinity: not JSON


def _place(value: Any, low: int) -> int:
    if type(value) is not int or not low <= value <= PLACE_LIMIT:
        raise Refused(R_SHAPE)
    return value


def check_request(kind: Any, payload: Any, known: Collection[str]) -> Request:
    """The page's ``post(kind, payload)`` as a Request (see the module doc), or Refused."""
    if type(kind) is not str or kind not in REQUEST_KINDS:
        raise Refused(R_KIND)
    if type(payload) is not str:
        raise Refused(R_SHAPE)
    if len(payload) > PAYLOAD_CAP:
        raise Refused(R_SIZE)
    try:
        value = json.loads(payload, parse_constant=_no_constants)
    except Refused:
        raise
    except (ValueError, RecursionError):
        raise Refused(R_JSON) from None
    if kind == "ready":
        fields = _fields(value, ("v",))
        if "v" in fields and (type(fields["v"]) is not int or fields["v"] != 1):
            raise Refused(R_FIELD)
        return Request("ready")
    if kind == "layout":
        fields = _fields(value, ("frames", "scrolling"), ("frames",))
        frames = fields["frames"]
        if type(frames) is not list or len(frames) > MAX_FRAMES:
            raise Refused(R_SHAPE)
        places: list[tuple[str, int, int]] = []
        for item in frames:
            place = _fields(item, ("id", "top", "height"), ("id", "top", "height"))
            places.append((_known_id(place["id"], known), _place(place["top"], -PLACE_LIMIT),
                           _place(place["height"], 0)))
        if len({place[0] for place in places}) != len(places):
            raise Refused(R_ID)
        return Request("layout", places=tuple(places), scrolling=_flag(fields.get("scrolling", False)))
    if kind == "pin":
        fields = _fields(value, ("id", "on"), ("id", "on"))
        return Request("pin", _known_id(fields["id"], known), on=_flag(fields["on"]))
    if kind not in _ID_KINDS:
        raise Refused(R_KIND)
    fields = _fields(value, ("id",), ("id",))
    return Request(kind, _known_id(fields["id"], known))
