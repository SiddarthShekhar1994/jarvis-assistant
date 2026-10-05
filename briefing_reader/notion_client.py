"""Notion REST client, block flattener, header parsing and polling.

Everything here is Qt-free. The flow is:

    NotionClient.fetch_block_tree(page_id)   raw block tree, children under "_children"
    flatten_blocks(tree)                     list[FlatLine]
    parse_header(lines)                      BriefingHeader + body lines
    check_freshness(header, run, now)        is this the briefing we were waiting for?

``fetch_briefing`` chains these, and ``poll_for_briefing`` repeats it until the
page is fresh or a deadline passes. ``FixtureSession`` serves saved API
responses so tests and ``--from-file`` never touch the network.

The Notion token is only ever placed in the Authorization header. It is never
put into exception messages, log lines or ``repr`` output.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

import requests

from .models import (
    BULLETED,
    CALLOUT,
    CODE,
    DIVIDER,
    HEADING,
    NUMBERED,
    OTHER,
    PARAGRAPH,
    QUOTE,
    TABLE_ROW,
    TO_DO,
    TOGGLE,
    Briefing,
    BriefingHeader,
    FlatLine,
    Freshness,
)

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
DEFAULT_NOTION_VERSION = "2022-06-28"
PAGE_SIZE = 100
MAX_TREE_REQUESTS = 500
RETRY_AFTER_DEFAULT_S = 2.0
RETRY_AFTER_CAP_S = 30.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})

# Block types whose children are separate pages; never fetched or flattened.
_NO_RECURSE_TYPES = frozenset({"child_page", "child_database"})

_SECRET_PATTERN = re.compile(r"\b(?:secret_|ntn_)[A-Za-z0-9]{20,}")


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class NotionError(Exception):
    """A Notion failure whose message is safe to show and log (never contains the token)."""

    def __init__(self, message: str = "", *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NotionAuthError(NotionError):
    """401 (bad token), 403 or 404 (page not shared with the integration). Not retried."""


class NotionConfigError(NotionError):
    """The Notion token is missing."""


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------

class _RequestBudget:
    """Counts HTTP requests made during one tree fetch and enforces a hard cap."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def spend(self) -> None:
        if self.used >= self.limit:
            raise NotionError(
                f"The Notion page is too large to read (more than {self.limit} requests)."
            )
        self.used += 1


class NotionClient:
    """Minimal Notion REST client for reading block children."""

    # Per-tree request cap; an instance attribute may override it (tests).
    max_tree_requests: int = MAX_TREE_REQUESTS

    def __init__(
        self,
        token: str,
        *,
        notion_version: str = DEFAULT_NOTION_VERSION,
        session: Any | None = None,
        timeout: float = 20.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = (token or "").strip()
        self._notion_version = notion_version
        self._owns_session = session is None
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout
        self._max_retries = max(0, int(max_retries))
        self._sleep = sleep

    def __repr__(self) -> str:
        token_state = "set" if self._token else "missing"
        return (
            f"{type(self).__name__}(notion_version={self._notion_version!r}, "
            f"timeout={self._timeout!r}, max_retries={self._max_retries!r}, token={token_state})"
        )

    @property
    def notion_version(self) -> str:
        return self._notion_version

    def close(self) -> None:
        """Close the HTTP session if this client created it."""
        if self._owns_session:
            self._session.close()

    def list_children(self, block_id: str) -> list[dict]:
        """All direct children of a block or page, following pagination."""
        return self._list_children(block_id, None)

    def fetch_block_tree(self, block_id: str, *, max_depth: int = 10) -> list[dict]:
        """Children of ``block_id`` with nested children stored under ``"_children"``.

        Blocks with children get a shallow copy carrying the extra key. Child
        pages and databases are not entered. ``max_depth`` is the number of
        nesting levels fetched below the top-level list.
        """
        budget = _RequestBudget(self.max_tree_requests)
        tree = self._fetch_tree(block_id, 0, max_depth, budget)
        logger.debug("Fetched block tree with %d request(s)", budget.used)
        return tree

    # -- internals ---------------------------------------------------------

    def _fetch_tree(self, block_id: str, level: int, max_depth: int,
                    budget: _RequestBudget) -> list[dict]:
        blocks = self._list_children(block_id, budget)
        result: list[dict] = []
        for block in blocks:
            if _should_recurse(block):
                if level < max_depth:
                    block = dict(block)
                    block["_children"] = self._fetch_tree(block["id"], level + 1, max_depth, budget)
                else:
                    logger.warning("Skipping blocks nested deeper than %d levels", max_depth)
            result.append(block)
        return result

    def _list_children(self, block_id: str, budget: _RequestBudget | None) -> list[dict]:
        url = f"{NOTION_API}/blocks/{block_id.strip()}/children"
        results: list[dict] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, Any] = {"page_size": PAGE_SIZE}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get_json(url, params, budget)
            page = data.get("results")
            if not isinstance(page, list):
                raise NotionError("Notion returned an unexpected response (no results list).")
            results.extend(item for item in page if isinstance(item, dict))
            if not data.get("has_more"):
                return results
            cursor = data.get("next_cursor")
            if not cursor:
                logger.warning("Notion said has_more without a next_cursor; stopping pagination")
                return results
            if cursor in seen_cursors:
                raise NotionError("Notion pagination did not advance; giving up.")
            seen_cursors.add(cursor)

    def _get_json(self, url: str, params: dict[str, Any],
                  budget: _RequestBudget | None) -> dict:
        """GET with retries for 429, 5xx and network errors; raises NotionError subclasses."""
        self._ensure_token()
        retries = 0
        while True:
            if budget is not None:
                budget.spend()
            logger.debug("GET %s params=%s", url, params)
            try:
                response = self._session.get(
                    url, params=params, headers=self._headers(), timeout=self._timeout
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                logger.warning("Notion request failed: %s", self._redact(str(exc)))
                failure = NotionError(
                    f"Could not reach Notion ({type(exc).__name__}). Check the internet connection."
                )
                delay = _backoff_delay(retries)
            except requests.RequestException as exc:
                # Only the type: e.g. InvalidHeader quotes the Authorization value.
                logger.warning("Notion request failed (%s)", type(exc).__name__)
                raise NotionError(f"Notion request failed ({type(exc).__name__}).") from None
            else:
                status = int(response.status_code)
                if 200 <= status < 300:
                    return self._decode(response)
                if status == 429:
                    failure = NotionError(
                        "Notion is rate limiting requests (HTTP 429). Try again in a minute.",
                        status=status,
                    )
                    delay = _retry_after_delay(getattr(response, "headers", None))
                elif status in RETRY_STATUSES:
                    failure = NotionError(
                        f"Notion is having trouble (HTTP {status}). Try again later.", status=status
                    )
                    delay = _backoff_delay(retries)
                else:
                    raise self._error_for(status, response)
            if retries >= self._max_retries:
                raise failure
            retries += 1
            logger.warning("%s Retry %d of %d in %.1f s.", failure, retries, self._max_retries, delay)
            self._sleep(delay)

    def _ensure_token(self) -> None:
        # Saved fixtures need no token; everything else does.
        if not self._token and not isinstance(self._session, FixtureSession):
            raise NotionConfigError(
                "NOTION_TOKEN is not set. Add it to the .env file next to the project."
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": self._notion_version,
            "Accept": "application/json",
        }

    def _decode(self, response: Any) -> dict:
        try:
            data = response.json()
        except ValueError:
            raise NotionError("Notion returned a response that is not JSON.") from None
        if not isinstance(data, dict):
            raise NotionError("Notion returned an unexpected response.")
        return data

    def _error_for(self, status: int, response: Any) -> NotionError:
        if status == 401:
            return NotionAuthError(
                "Notion rejected the token (401). Check NOTION_TOKEN in .env.", status=status
            )
        message = self._notion_message(response)
        detail = f" Notion said: {message}" if message else ""
        if status in (403, 404):
            return NotionAuthError(
                f"The briefing page was not found or is not shared with the integration ({status}). "
                "Open the page in Notion, then use the ... menu > Connections > add your integration."
                f"{detail}",
                status=status,
            )
        return NotionError(f"Notion API error (HTTP {status}).{detail}", status=status)

    def _notion_message(self, response: Any) -> str:
        try:
            data = response.json()
        except Exception:  # noqa: BLE001 - any undecodable body just means "no message"
            return ""
        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, str):
            return ""
        return self._redact(" ".join(message.split())[:300])

    def _redact(self, text: str) -> str:
        if self._token:
            text = text.replace(self._token, "[REDACTED]")
        return _SECRET_PATTERN.sub("[REDACTED]", text)


def _should_recurse(block: dict) -> bool:
    return (
        block.get("has_children") is True
        and block.get("type") not in _NO_RECURSE_TYPES
        and bool(block.get("id"))
    )


def _backoff_delay(retries: int) -> float:
    return float(2 ** retries)  # 1, 2, 4, ... seconds


def _retry_after_delay(headers: Any) -> float:
    value = _header(headers, "Retry-After")
    try:
        delay = float(value) if value is not None else RETRY_AFTER_DEFAULT_S
    except (TypeError, ValueError):
        delay = RETRY_AFTER_DEFAULT_S
    return min(max(delay, 0.0), RETRY_AFTER_CAP_S)


def _header(headers: Any, name: str) -> Any:
    """Case-insensitive header lookup that works for plain dicts too."""
    if not headers:
        return None
    value = headers.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    for key, item in headers.items():
        if str(key).lower() == lowered:
            return item
    return None


# --------------------------------------------------------------------------
# Fixture session (tests and --from-file)
# --------------------------------------------------------------------------

def _normalize_id(value: str) -> str:
    return value.strip().replace("-", "").lower()


class FixtureResponse:
    """The parts of ``requests.Response`` that NotionClient uses."""

    def __init__(self, status_code: int, payload: Any,
                 headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {"Content-Type": "application/json"})
        self._payload = payload

    @property
    def text(self) -> str:
        return json.dumps(self._payload)

    def json(self) -> Any:
        return copy.deepcopy(self._payload)


class FixtureSession:
    """requests-like session that serves saved Notion API responses from a JSON fixture file (tests and --from-file).

    Format: ``{"page_id": "<32hex>", "responses": {"<block_id>": [<list page 1>, <list page 2>, ...]}}``.
    Page N > 1 is chosen by ``params["start_cursor"]`` equal to page N-1's ``next_cursor``.
    ``calls`` records ``(block_id, start_cursor)`` for every request, with the
    block id normalised to 32 lowercase hex characters.
    """

    _URL_PATTERN = re.compile(r"/blocks/([^/?#]+)/children")

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(data.get("responses"), dict):
            raise ValueError(f"{self.path.name} is not a Notion fixture (missing 'responses').")
        self.page_id: str = _normalize_id(str(data.get("page_id", "")))
        self._responses: dict[str, list[dict]] = {
            _normalize_id(key): list(pages) for key, pages in data["responses"].items()
        }
        self.calls: list[tuple[str, str | None]] = []

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            timeout: float | None = None) -> FixtureResponse:
        cursor = (params or {}).get("start_cursor")
        match = self._URL_PATTERN.search(url)
        block_id = _normalize_id(match.group(1)) if match else ""
        self.calls.append((block_id, cursor))
        pages = self._responses.get(block_id)
        if not pages:
            return _error_response(404, "object_not_found",
                                   f"Could not find block with ID: {block_id or url}.")
        if not cursor:
            return FixtureResponse(200, pages[0])
        for previous, page in zip(pages, pages[1:]):
            if previous.get("next_cursor") == cursor:
                return FixtureResponse(200, page)
        return _error_response(400, "validation_error", f"start_cursor {cursor} is not valid.")

    def close(self) -> None:
        """No-op; present so the fixture can stand in for ``requests.Session``."""


def _error_response(status: int, code: str, message: str) -> FixtureResponse:
    return FixtureResponse(status, {"object": "error", "status": status, "code": code,
                                    "message": message})


# --------------------------------------------------------------------------
# Flattening
# --------------------------------------------------------------------------

_MEDIA_TYPES = frozenset({"bookmark", "embed", "link_preview", "image", "video", "file", "pdf", "audio"})
_URL_MEDIA_TYPES = frozenset({"bookmark", "embed", "link_preview"})
_SAME_DEPTH_CONTAINERS = frozenset({"column_list", "column", "synced_block"})
_HEADING_LEVELS = {"heading_1": 1, "heading_2": 2, "heading_3": 3}
_SINGLE_LINE_KINDS = {
    "bulleted_list_item": BULLETED,
    "numbered_list_item": NUMBERED,
    "to_do": TO_DO,
    "toggle": TOGGLE,
}
_MULTI_LINE_KINDS = {"paragraph": PARAGRAPH, "quote": QUOTE, "callout": CALLOUT}


def flatten_blocks(blocks: list[dict], depth: int = 0) -> list[FlatLine]:
    """Flatten a block tree (children under ``"_children"``) into display lines.

    Text is plain text, except that a paragraph whose visible text is bold as
    a whole is wrapped in ``**`` (text_prep treats such a line as a title).
    """
    lines: list[FlatLine] = []
    number = 0  # position within the current run of numbered siblings; 0 = no run
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "numbered_list_item":
            number = _next_number(block, number)
        else:
            number = 0
        lines.extend(_flatten_block(block, depth, number))
    return lines


def _flatten_block(block: dict, depth: int, number: int) -> list[FlatLine]:
    btype = str(block.get("type", ""))
    payload = block.get(btype)
    if not isinstance(payload, dict):
        payload = {}
    block_id = str(block.get("id", ""))
    children = block.get("_children") or []

    if btype in _HEADING_LEVELS:
        lines = _make(HEADING, _one_line(_rich_text(payload)), depth, block_id,
                      level=_HEADING_LEVELS[btype])
        # Toggleable heading children are section content, not nested items.
        return lines + flatten_blocks(children, depth)

    if btype in _MULTI_LINE_KINDS:
        kind = _MULTI_LINE_KINDS[btype]
        segments = _segments(_rich_text(payload))
        if kind == PARAGRAPH and _all_bold(payload.get("rich_text")):
            # A paragraph that is bold as a whole is usually a label or section
            # title; keep that visible to text_prep as markdown bold.
            segments = [_as_bold(segment) for segment in segments]
        lines = [FlatLine(kind, segment, depth, block_id=block_id) for segment in segments]
        return lines + flatten_blocks(children, depth + 1)

    if btype in _SINGLE_LINE_KINDS:
        kind = _SINGLE_LINE_KINDS[btype]
        extra: dict[str, Any] = {}
        if kind == NUMBERED:
            extra["number"] = number
        elif kind == TO_DO:
            extra["checked"] = bool(payload.get("checked"))
        lines = _make(kind, _one_line(_rich_text(payload)), depth, block_id, **extra)
        return lines + flatten_blocks(children, depth + 1)

    if btype == "divider":
        return [FlatLine(DIVIDER, "", depth, block_id=block_id)]

    if btype == "code":
        return [FlatLine(CODE, source.rstrip(), depth, block_id=block_id)
                for source in _rich_text(payload).splitlines() if source.strip()]

    if btype == "table":
        return flatten_blocks(children, depth)  # rows are table_row blocks

    if btype == "table_row":
        return _make(TABLE_ROW, _table_row_text(payload), depth, block_id)

    if btype in _SAME_DEPTH_CONTAINERS:
        return flatten_blocks(children, depth)

    if btype in _NO_RECURSE_TYPES:
        return _make(OTHER, _one_line(str(payload.get("title", ""))), depth, block_id)

    if btype in _MEDIA_TYPES:
        text = _one_line(_plain(payload.get("caption")))
        if not text and btype in _URL_MEDIA_TYPES:
            text = str(payload.get("url") or "").strip()
        return _make(OTHER, text, depth, block_id) + flatten_blocks(children, depth + 1)

    if btype == "equation":
        return _make(OTHER, _one_line(str(payload.get("expression", ""))), depth, block_id)

    # breadcrumb, table_of_contents, unsupported, template, anything new.
    return (_make(OTHER, _one_line(_rich_text(payload)), depth, block_id)
            + flatten_blocks(children, depth + 1))


def _table_row_text(payload: dict) -> str:
    cells = payload.get("cells") or []
    texts = (_one_line(_plain(cell)) for cell in cells if isinstance(cell, list))
    return " | ".join(text for text in texts if text)


def _next_number(block: dict, previous: int) -> int:
    if previous:
        return previous + 1
    payload = block.get("numbered_list_item")
    start = payload.get("list_start_index") if isinstance(payload, dict) else None
    if isinstance(start, int) and not isinstance(start, bool) and start > 0:
        return start
    return 1


def _make(kind: str, text: str, depth: int, block_id: str, **extra: Any) -> list[FlatLine]:
    text = text.strip()
    if not text:
        return []
    return [FlatLine(kind, text, depth, block_id=block_id, **extra)]


def _rich_text(payload: dict) -> str:
    return _plain(payload.get("rich_text"))


def _plain(rich_text: Any) -> str:
    if not isinstance(rich_text, list):
        return ""
    return "".join(_item_text(item) for item in rich_text if isinstance(item, dict))


def _item_text(item: dict) -> str:
    text = item.get("plain_text")
    if text is None:
        text = (item.get("text") or {}).get("content", "")
    return str(text)


def _all_bold(rich_text: Any) -> bool:
    """True when every rich-text piece with visible text has the bold annotation."""
    if not isinstance(rich_text, list):
        return False
    seen = False
    for item in rich_text:
        if not isinstance(item, dict) or not _item_text(item).strip():
            continue
        annotations = item.get("annotations")
        if not (isinstance(annotations, dict) and annotations.get("bold") is True):
            return False
        seen = True
    return seen


def _as_bold(text: str) -> str:
    return text if text.startswith("**") and text.endswith("**") and len(text) > 4 else f"**{text}**"


def _segments(text: str) -> list[str]:
    return [part.strip() for part in text.splitlines() if part.strip()]


def _one_line(text: str) -> str:
    return " ".join(_segments(text))


# --------------------------------------------------------------------------
# Plain-text rendering (debugging)
# --------------------------------------------------------------------------

def render_plain_text(lines: Sequence[FlatLine]) -> str:
    """Readable plain-text rendering of flattened lines (debug output, --dump)."""
    out: list[str] = []
    for line in lines:
        if line.kind == HEADING and out:
            out.append("")
        out.append("  " * line.depth + _plain_body(line))
    return "\n".join(out)


def _plain_body(line: FlatLine) -> str:
    if line.kind == DIVIDER:
        return "---"
    if line.kind == BULLETED:
        return f"- {line.text}"
    if line.kind == NUMBERED:
        return f"{line.number or 1}. {line.text}"
    if line.kind == TO_DO:
        return f"[{'x' if line.checked else ' '}] {line.text}"
    if line.kind == QUOTE:
        return f"> {line.text}"
    return line.text


# --------------------------------------------------------------------------
# Header parsing and freshness
# --------------------------------------------------------------------------

_UPDATED_RE = re.compile(r"(?i)\bupdated\s*:")
_RUN_RE = re.compile(r"(?i)\brun\s*:\s*(am|pm)\b")
_RUN_LABEL_RE = re.compile(r"(?i)\brun\s*:")
_RAW_SEPARATORS = (" · ", " | ")
_TIMESTAMP_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?"
    r"\s*(?:([AaPp][Mm])\b)?\s*"
    r"([Zz]\b|[+-]\d{2}:?\d{2}\b|[A-Za-z]{2,5}\b)?"
)
_TZ_ABBREVIATIONS = {
    "UTC": 0, "GMT": 0, "Z": 0,
    "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7, "AKST": -9, "AKDT": -8, "HST": -10,
}


def parse_header(lines: Sequence[FlatLine], *, scan: int = 6,
                 local_tz: tzinfo | None = None) -> tuple[BriefingHeader, list[FlatLine]]:
    """Find "Updated: ..." and "Run: AM|PM" in the first ``scan`` lines.

    Returns the header and the remaining body lines (header lines removed).
    """
    updated_raw: str | None = None
    found_updated = False
    run: str | None = None
    used: set[int] = set()
    for index in range(min(scan, len(lines))):
        text = lines[index].text.replace("*", "").replace("_", "")
        if not found_updated:
            match = _UPDATED_RE.search(text)
            if match:
                found_updated = True
                updated_raw = _clean_updated_raw(text[match.end():]) or None
                used.add(index)
        if run is None:
            match = _RUN_RE.search(text)
            if match:
                run = match.group(1).upper()
                used.add(index)
    updated_at = parse_timestamp(updated_raw, local_tz=local_tz) if updated_raw else None
    body = [line for index, line in enumerate(lines) if index not in used]
    return BriefingHeader(updated_raw=updated_raw, updated_at=updated_at, run=run), body


def _clean_updated_raw(rest: str) -> str:
    rest = rest.split("\n", 1)[0]
    for separator in _RAW_SEPARATORS:
        rest = rest.split(separator, 1)[0]
    run_label = _RUN_LABEL_RE.search(rest)
    if run_label:
        rest = rest[:run_label.start()]
    return rest.strip().rstrip(",;|·-–—").strip()


def parse_timestamp(raw: str, *, local_tz: tzinfo | None = None) -> datetime | None:
    """Parse "2026-10-04 10:04 PDT" style text into an aware datetime in local time.

    Unknown or missing time zones are read as local time. Returns None when no
    timestamp is found or it is not a valid date.
    """
    match = _TIMESTAMP_RE.search(raw)
    if not match:
        return None
    year, month, day, hour, minute = (int(match.group(i)) for i in range(1, 6))
    second = int(match.group(6) or 0)
    hour = _apply_meridiem(hour, match.group(7))
    try:
        naive = datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
    source_tz = _parse_tz(match.group(8))
    if source_tz is not None:
        aware = naive.replace(tzinfo=source_tz)
    elif local_tz is not None:
        aware = naive.replace(tzinfo=local_tz)
    else:
        aware = naive.astimezone()  # naive -> system local zone
    return aware.astimezone(local_tz) if local_tz is not None else aware.astimezone()


def _apply_meridiem(hour: int, meridiem: str | None) -> int:
    if not meridiem or not 1 <= hour <= 12:
        return hour
    if meridiem.upper() == "AM":
        return 0 if hour == 12 else hour
    return hour if hour == 12 else hour + 12


def _parse_tz(token: str | None) -> tzinfo | None:
    if not token:
        return None
    if token[0] in "+-":
        digits = token[1:].replace(":", "")
        hours, minutes = int(digits[:2]), int(digits[2:])
        if hours > 23 or minutes > 59:
            return None
        offset = timedelta(hours=hours, minutes=minutes)
        return timezone(-offset if token[0] == "-" else offset)
    hours = _TZ_ABBREVIATIONS.get(token.upper())
    if hours is None:
        logger.debug("Unknown time zone %r in Updated line; using local time", token)
        return None
    return timezone(timedelta(hours=hours))


def check_freshness(header: BriefingHeader, expected_run: str | None, now: datetime, *,
                    run_started: datetime | None = None) -> Freshness:
    """Is the header from today (in ``now``'s zone) and from the expected run?

    ``run_started`` is when the run began waiting for this briefing. An update
    from that day also counts as today's while it is at most one day back, so
    a PM briefing that was fresh at 23:42 is not called stale after midnight.
    """
    days = {now.date()}
    if run_started is not None:
        started = run_started.astimezone(now.tzinfo).date()
        if 0 <= (now.date() - started).days <= 1:
            days.add(started)
    updated_today = (
        header.updated_at is not None
        and header.updated_at.astimezone(now.tzinfo).date() in days
    )
    run_matches = not expected_run or header.run == expected_run.strip().upper()
    return Freshness(fresh=updated_today and run_matches,
                     updated_today=updated_today, run_matches=run_matches)


# --------------------------------------------------------------------------
# Fetching and polling
# --------------------------------------------------------------------------

def fetch_briefing(client: NotionClient, page_id: str, *, now: datetime | None = None) -> Briefing:
    """Fetch, flatten and parse the briefing page."""
    lines = flatten_blocks(client.fetch_block_tree(page_id))
    header, body = parse_header(lines)
    fetched_at = now if now is not None else datetime.now().astimezone()
    logger.info("Fetched briefing: %d body line(s), updated=%r, run=%s",
                len(body), header.updated_raw, header.run)
    return Briefing(page_id=page_id, header=header, lines=tuple(body), fetched_at=fetched_at)


@dataclass(frozen=True)
class PollResult:
    """Outcome of ``poll_for_briefing``."""

    briefing: Briefing | None        # last successfully fetched briefing (None if every attempt failed)
    freshness: Freshness | None
    error: NotionError | None        # last error, if the last attempt failed
    attempts: int
    timed_out: bool                  # gave up waiting
    stopped: bool                    # stop was requested


def poll_for_briefing(
    fetch: Callable[[], Briefing],
    *,
    expected_run: str | None,
    interval_s: float,
    timeout_s: float,
    now: Callable[[], datetime],
    wait: Callable[[float], bool],
    on_attempt: Callable[[Briefing | None, Freshness | None, NotionError | None, int], None] | None = None,
    run_started: datetime | None = None,
) -> PollResult:
    """Fetch until the briefing is fresh, a fatal error occurs, time runs out or stop is requested.

    ``wait(seconds)`` blocks and returns True when a stop was requested.
    ``run_started`` is passed on to ``check_freshness`` (a run that began
    before midnight still accepts that day's briefing).
    """
    deadline = now() + timedelta(seconds=timeout_s)
    last_briefing: Briefing | None = None
    last_freshness: Freshness | None = None
    attempts = 0
    while True:
        attempts += 1
        briefing, error = _attempt_fetch(fetch)
        freshness = None
        if briefing is not None:
            freshness = check_freshness(briefing.header, expected_run, now(), run_started=run_started)
            last_briefing, last_freshness = briefing, freshness
        _notify(on_attempt, briefing, freshness, error, attempts)

        fatal = isinstance(error, (NotionAuthError, NotionConfigError))
        satisfied = freshness is not None and (not expected_run or freshness.fresh)
        timed_out = stopped = False
        if not (fatal or satisfied):
            current = now()
            if current >= deadline:
                logger.info("Gave up waiting for a fresh briefing after %d attempt(s)", attempts)
                timed_out = True
            else:
                remaining = (deadline - current).total_seconds()
                stopped = bool(wait(min(interval_s, remaining)))
                if not stopped:
                    continue
        return PollResult(briefing=last_briefing, freshness=last_freshness, error=error,
                          attempts=attempts, timed_out=timed_out, stopped=stopped)


def _attempt_fetch(fetch: Callable[[], Briefing]) -> tuple[Briefing | None, NotionError | None]:
    try:
        return fetch(), None
    except NotionError as exc:
        logger.warning("Briefing fetch failed: %s", exc)
        return None, exc
    except Exception as exc:  # noqa: BLE001 - polling must survive anything fetch raises
        logger.warning("Briefing fetch failed unexpectedly (%s)", type(exc).__name__, exc_info=True)
        return None, NotionError(f"Unexpected error while fetching the briefing ({type(exc).__name__}).")


def _notify(on_attempt: Callable[..., None] | None, briefing: Briefing | None,
            freshness: Freshness | None, error: NotionError | None, attempt: int) -> None:
    if on_attempt is None:
        return
    try:
        on_attempt(briefing, freshness, error, attempt)
    except Exception:  # noqa: BLE001 - a broken callback must not stop polling
        logger.exception("on_attempt callback failed")
