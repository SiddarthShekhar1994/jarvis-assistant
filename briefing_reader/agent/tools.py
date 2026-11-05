"""The agent's tools: the registry (SPEC 5 and 7), the one source for tools/list and --allowedTools.

    Tool(name, description, schema, tier, handler, title)
                                 one tool. ``handler(args, ctx)`` gets input already checked against ``schema`` and
                                 gives a ToolResult; it knows nothing about the engine
    TOOLS                        every tool of this version. A later part adds entries here (and their tiers to
                                 policy.OFFERED_TIERS); nothing else has to change
    Registry(tools)              what one run offers: listing() for tools/list, allowed() for --allowedTools, expected()
                                 for the init check, call(name, args, ctx)
    check_input(value, schema)   "" or the first problem, in words that name fields, never a value
    RunContext                   what a handler may use of its run
    untrusted(source, text)      other people's text as a block the prompt calls data (R5): its closing mark holds a
                                 random word, so the text inside cannot close it

Jarvis's own words go back to the model; a bad input gets a clear error result (isError), never an exception. A handler
that raises gives an error result that names only the exception's type. Live may show what ToolResult.fields holds, in
memory only; the logs never do: nothing here logs anything, and ToolResult's repr holds its code and whether it is an
error.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..ask.cli import AGENT_TOOL_PREFIX
from ..ask.context import data
from .workspace import READ_CAP, TaskFolder, WorkspaceRefused

# What a tool may do, in the words of SPEC 7. Only these decide what is offered and gated (agent.policy).
TIER_SHOW, TIER_WAIT, TIER_END, TIER_WORKSPACE = "show", "wait", "end", "workspace"
TIER_WEB, TIER_PRIVATE, TIER_CARD, TIER_INSTANT = "web", "private", "card", "instant"
TIERS = (TIER_SHOW, TIER_WAIT, TIER_END, TIER_WORKSPACE, TIER_WEB, TIER_PRIVATE, TIER_CARD, TIER_INSTANT)

MAX_PLAN_STEPS = 20
PLAN_TEXT_CAP = 200
SAY_CAP = 200
SAY_EVERY_S = 6.0
QUESTION_CAP = 300
MAX_CHOICES = 5
CHOICE_CAP = 60
SUMMARY_CAP = 1500
MAX_OUTPUTS = 20
OUTCOME_DONE, OUTCOME_PARTIAL, OUTCOME_FAILED = "done", "partial", "failed"
OUTCOMES = (OUTCOME_DONE, OUTCOME_PARTIAL, OUTCOME_FAILED)
STEP_TODO, STEP_DOING, STEP_DONE, STEP_FAILED = "todo", "doing", "done", "failed"
STEP_STATES = (STEP_TODO, STEP_DOING, STEP_DONE, STEP_FAILED)
WAIT_TEXT = ("The owner has it on his screen. End your turn now with one short sentence. His answer will come to you "
             "as your next message.")
ANOTHER_QUESTION_TEXT = ("A question is waiting for the owner already. End your turn now; his answer comes as your "
                         "next message.")
# Result codes (the logs' and Live's: fixed words).
BAD_INPUT, RAISED, RATE, REFUSED, UNKNOWN_TOOL = "bad_input", "raised", "rate", "refused", "unknown_tool"


@dataclass(frozen=True)
class ToolResult:
    """What a tool gives back: its text and pictures for the model, and for Live a few words (``note``) and
    ``fields`` (never logged). ``code``: a fixed word when it did not do what was asked."""

    text: str = field(default="", repr=False)
    pictures: tuple[tuple[str, str], ...] = field(default=(), repr=False)   # (base64 data, media type)
    is_error: bool = False
    code: str = ""
    note: str = field(default="", repr=False)
    fields: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def content(self) -> list[dict[str, str]]:
        """The MCP content blocks."""
        blocks = [{"type": "text", "text": self.text}]
        blocks += [{"type": "image", "data": body, "mimeType": kind} for body, kind in self.pictures]
        return blocks


def error(text: str, code: str = REFUSED) -> ToolResult:
    return ToolResult(text, is_error=True, code=code)


@dataclass(frozen=True)
class PlanStep:
    text: str = field(repr=False)
    status: str = STEP_TODO


@dataclass
class RunContext:
    """What a handler may use of its run. The ``on_*`` callables are the run's (agent.runner.AgentRun): each reports
    to the owner and never raises. ``cancelled()``: the run was stopped or this call was given up on (past the 45 s),
    so whatever this call would still change must not happen."""

    run_id: str = ""
    folder: TaskFolder | None = None
    clock: Callable[[], float] = time.monotonic
    on_plan: Callable[[tuple[PlanStep, ...]], None] = lambda _steps: None
    on_say: Callable[[str], None] = lambda _text: None
    on_question: Callable[[str, tuple[str, ...]], bool] = lambda _question, _choices: True
    on_finish: Callable[[str, str, tuple[str, ...]], None] = lambda _outcome, _summary, _outputs: None
    cancelled: Callable[[], bool] = lambda: False
    last_say: list[float] = field(default_factory=list)   # when the last say went, shared by this run's calls
    say_lock: threading.Lock = field(default_factory=threading.Lock)


def _object(**props: Any) -> dict[str, Any]:
    required = [name for name, schema in props.items() if not schema.pop("optional", False)]
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _text(description: str, cap: int, **more: Any) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": cap, "description": description, **more}


# --------------------------------------------------------------------------
# The input check (the part of JSON Schema these tools use)
# --------------------------------------------------------------------------

def check_input(value: Any, schema: Mapping[str, Any], where: str = "input") -> str:
    """"" when ``value`` fits ``schema``, else the first problem in plain words (fields by name, never a value)."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, Mapping):
            return f"{where} must be an object"
        properties = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in value:
                return f'{where} needs "{name}"'
        if schema.get("additionalProperties") is False:
            extra = sorted(str(name) for name in value if name not in properties)
            if extra:
                return f'{where} has a field that is not allowed: "{data(extra[0], 40)}"'
        for name, inner in properties.items():
            if name in value:
                problem = check_input(value[name], inner, f'"{name}"')
                if problem:
                    return problem
        return ""
    if kind == "array":
        if not isinstance(value, (list, tuple)):
            return f"{where} must be a list"
        if len(value) > schema.get("maxItems", len(value)):
            return f"{where} has too many items: at most {schema['maxItems']}"
        if len(value) < schema.get("minItems", 0):
            return f"{where} needs at least {schema['minItems']} item(s)"
        item = schema.get("items")
        for number, entry in enumerate(value, 1):
            problem = check_input(entry, item, f"{where} item {number}") if item else ""
            if problem:
                return problem
        return ""
    if kind == "string":
        if not isinstance(value, str):
            return f"{where} must be text"
        if len(value) > schema.get("maxLength", len(value)):
            return f"{where} is too long: at most {schema['maxLength']} characters"
        if len(value.strip()) < schema.get("minLength", 0):
            return f"{where} must not be empty"
    elif kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return f"{where} must be a whole number"
    elif kind == "boolean" and not isinstance(value, bool):
        return f"{where} must be true or false"
    if "enum" in schema and value not in schema["enum"]:
        return f"{where} must be one of: " + ", ".join(str(option) for option in schema["enum"])
    return ""


# --------------------------------------------------------------------------
# Other people's text
# --------------------------------------------------------------------------

def untrusted(source: str, text: str) -> str:
    """``text`` inside an untrusted block (R5). The closing mark holds a random word no text can guess, so nothing
    inside can pretend the block ended."""
    mark = secrets.token_hex(4)
    return f'<untrusted source="{data(source, 60)}" id="{mark}">\n{text}\n</untrusted id="{mark}">'


# --------------------------------------------------------------------------
# The handlers
# --------------------------------------------------------------------------

def _update_plan(args: Mapping[str, Any], ctx: RunContext) -> ToolResult:
    steps = tuple(PlanStep(data(step["text"], PLAN_TEXT_CAP), step["status"]) for step in args["steps"])
    ctx.on_plan(steps)
    done = sum(1 for step in steps if step.status == STEP_DONE)
    return ToolResult("The owner sees this plan now.", note=f"{done} of {len(steps)} done",
                      fields=tuple((step.status.upper(), step.text) for step in steps))


def _say(args: Mapping[str, Any], ctx: RunContext) -> ToolResult:
    text = data(args["text"], SAY_CAP)
    if not text:
        return error('"text" must not be empty', BAD_INPUT)
    now = ctx.clock()
    with ctx.say_lock:
        if ctx.last_say and now - ctx.last_say[-1] < SAY_EVERY_S:
            wait = max(1, round(SAY_EVERY_S - (now - ctx.last_say[-1])))
            return error(f"Not said: at most one line every {SAY_EVERY_S:.0f} seconds (wait {wait} s). Carry on with "
                         "the work; say it later if it still matters.", RATE)
        ctx.last_say[:] = [now]
    ctx.on_say(text)
    return ToolResult("Said.", note=text)


def _ask_owner(args: Mapping[str, Any], ctx: RunContext) -> ToolResult:
    question = data(args["question"], QUESTION_CAP)
    choices = tuple(text for text in (data(choice, CHOICE_CAP) for choice in args.get("choices", ())) if text)
    if not question:
        return error('"question" must not be empty', BAD_INPUT)
    if not ctx.on_question(question, choices):
        return ToolResult(ANOTHER_QUESTION_TEXT, code=REFUSED, note="a question waits already")
    return ToolResult(WAIT_TEXT, note=question, fields=tuple(("Choice", choice) for choice in choices))


def _finish(args: Mapping[str, Any], ctx: RunContext) -> ToolResult:
    summary = " ".join(str(args["summary"]).split())
    outputs = tuple(args.get("outputs", ()))
    missing = [name for name in outputs if ctx.folder is None or not ctx.folder.exists(name)]
    if not summary:
        return error('"summary" must not be empty', BAD_INPUT)
    if missing:
        return error(f"{len(missing)} of those outputs is not a file in the task folder. Check with list_files, then "
                     "call finish again with what really exists.", REFUSED)
    ctx.on_finish(args["outcome"], summary, outputs)
    return ToolResult("Recorded. End your turn now with one short sentence.", note=args["outcome"],
                      fields=(("Summary", summary),))


def _folder_refusal(refused: WorkspaceRefused) -> ToolResult:
    return error(refused.text, refused.code)


def _write_file(args: Mapping[str, Any], ctx: RunContext) -> ToolResult:
    assert ctx.folder is not None
    try:
        name, size = ctx.folder.write(args["name"], args["text"], may_commit=lambda: not ctx.cancelled())
    except WorkspaceRefused as refused:
        return _folder_refusal(refused)
    return ToolResult(f"Wrote {name}: {size:,} bytes. Read it back when it matters.", note=f"{size:,} bytes",
                      fields=(("File", name),))


def _read_file(args: Mapping[str, Any], ctx: RunContext) -> ToolResult:
    assert ctx.folder is not None
    try:
        name, text, more = ctx.folder.read(args["name"])
    except WorkspaceRefused as refused:
        return _folder_refusal(refused)
    cut = f"\n(Only the first {READ_CAP:,} characters are shown.)" if more else ""
    return ToolResult(untrusted("a file in the task folder", text) + cut, note=f"{len(text):,} characters",
                      fields=(("File", name),))


def _list_files(_args: Mapping[str, Any], ctx: RunContext) -> ToolResult:
    assert ctx.folder is not None
    rows = ctx.folder.listing()
    if not rows:
        return ToolResult("The task folder is empty.", note="no files")
    lines = "\n".join(f"{name} - {size:,} bytes" for name, size in rows)
    return ToolResult(untrusted("the names in the task folder", lines), note=f"{len(rows)} file(s)")


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: Mapping[str, Any] = field(repr=False)
    tier: str
    handler: Callable[[Mapping[str, Any], RunContext], ToolResult] = field(repr=False)
    title: str = ""                   # the step's title in Live


TOOLS: tuple[Tool, ...] = (
    Tool("update_plan",
         "Show the owner your plan: every step and how it stands. Call it first, then again whenever a step changes or "
         "the plan does.",
         _object(steps={"type": "array", "minItems": 1, "maxItems": MAX_PLAN_STEPS,
                        "description": "The whole plan, in order.",
                        "items": _object(text=_text("One step, in a few plain words.", PLAN_TEXT_CAP),
                                         status={"type": "string", "enum": list(STEP_STATES)})}),
         TIER_SHOW, _update_plan, "The plan"),
    Tool("say",
         "Say one short line to the owner, aloud and on his screen, when something is worth saying. In your own "
         "words, never words copied from a page, a file or a message; no addresses or file paths. At most one every "
         "6 seconds.",
         _object(text=_text("One short spoken sentence.", SAY_CAP)),
         TIER_SHOW, _say, "Said"),
    Tool("ask_owner",
         "Ask the owner something only he can decide, when you are truly stuck or the choice is really his. It "
         "returns at once; end your turn, and his answer comes as your next message.",
         _object(question=_text("One short question.", QUESTION_CAP),
                 choices={"type": "array", "maxItems": MAX_CHOICES, "optional": True,
                          "description": "Up to 5 short answers he can pick; leave it out for a free answer.",
                          "items": _text("A short answer.", CHOICE_CAP)}),
         TIER_WAIT, _ask_owner, "Asked you"),
    Tool("finish",
         "End the task with your report. Call it only when the work is done, or when you cannot go further. Say what "
         "you did, what you produced and what you could not do and why. List only files you checked.",
         _object(summary=_text("A short honest report, in plain words.", SUMMARY_CAP),
                 outcome={"type": "string", "enum": list(OUTCOMES),
                          "description": "done: all of it, checked. partial: some of it. failed: none of it."},
                 outputs={"type": "array", "maxItems": MAX_OUTPUTS, "optional": True,
                          "description": "The files you produced, by their names in the task folder.",
                          "items": _text("A file's name in the task folder.", 200)}),
         TIER_END, _finish, "Finished"),
    Tool("write_file",
         "Write a text file in this task's own folder (a new one, or a whole new version of one). Names are relative "
         "to the folder; only .md .txt .csv .tsv .json .html .htm .css .svg .xml .yaml .yml .log; at most 5 MB a file.",
         _object(name=_text("The file's name in the task folder, e.g. notes/summary.md", 200),
                 text={"type": "string", "description": "The file's whole text."}),
         TIER_WORKSPACE, _write_file, "Wrote a file"),
    Tool("read_file",
         "Read a text file from this task's own folder, to check what you wrote or use it again.",
         _object(name=_text("The file's name in the task folder.", 200)),
         TIER_WORKSPACE, _read_file, "Read a file"),
    Tool("list_files",
         "List the files in this task's own folder, with their sizes.",
         _object(),
         TIER_WORKSPACE, _list_files, "Listed the files"),
)


class Registry:
    """What one run offers, by name, in order. The names on the command line and in tools/list and the set the init
    event must show all come from here."""

    def __init__(self, tools: Sequence[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("two tools with one name")

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def tool(self, name: Any) -> Tool | None:
        return self._tools.get(name) if isinstance(name, str) else None

    def listing(self) -> list[dict[str, Any]]:
        """tools/list's answer."""
        return [{"name": tool.name, "description": tool.description, "inputSchema": tool.schema}
                for tool in self._tools.values()]

    def allowed(self) -> str:
        """--allowedTools' one comma list."""
        return ",".join(AGENT_TOOL_PREFIX + name for name in self._tools)

    def expected(self) -> frozenset[str]:
        """The names the init event must show: exactly these."""
        return frozenset(AGENT_TOOL_PREFIX + name for name in self._tools)

    def call(self, name: str, args: Any, ctx: RunContext) -> ToolResult:
        """``args`` checked against the tool's schema, then its handler. Always a ToolResult."""
        tool = self.tool(name)
        if tool is None:
            return error("There is no such tool.", UNKNOWN_TOOL)
        problem = check_input(args if args is not None else {}, tool.schema)
        if problem:
            return error(f"That call was not made: {problem}. Fix it and call again.", BAD_INPUT)
        try:
            return tool.handler(args if args is not None else {}, ctx)
        except Exception as exc:  # noqa: BLE001 - a broken tool is an error result for the model, never an exception
            return error(f"That tool failed ({type(exc).__name__}). Try another way, or say so in your report.", RAISED)
