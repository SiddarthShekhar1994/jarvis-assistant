"""Setting a standing job up from the owner's own sentence (no Qt).

    check_words(words) -> str        the owner's sentence as setup takes it: what they typed, one line, at most
                                     model.MAX_WORDS characters (SetupRefused otherwise). Never words a planner wrote:
                                     a hand-off from Ask passes the owner's own request.
    setup_prompt(words, accounts, *, now, time_zone_name) -> str
                                     <now>; <accounts> (each alias and what it can do: read mail, the calendar);
                                     <command>: the sentence alone
    read_setup(event, init) -> Mapping | AskFailure     the answer, read strictly by its schema's shape
    definition(answer, words, *, aliases, readable, labels, min_every, now) -> Agent | Rule | None
        what the answer made, checked by Jarvis: a new id and status draft; every filter through standing.filters
        (grounded in the owner's sentence; a tidy-up needs a sender); a rule's account one that can read mail; a
        label tidy-up's label one of that account's own labels (``labels``: alias -> [(id, name)]), matched by its
        name as the owner said it; the definition checked as model does. None for "neither". SetupRefused: why not,
        in words that hold none of the sentence.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from ..ask.context import data
from ..ask.stream import AskFailure, GARBLED, failure
from .engine import structured
from .filters import FilterRefused, check_filter
from .model import (KIND_AGENT, KIND_RULE, MAX_WORDS, STATUS_DRAFT, TIDY_LABEL, TIDY_NONE, Agent, DefinitionError,
                    Rule, agent_from, new_id, rule_from)


class SetupRefused(ValueError):
    """Why a sentence made no standing job (names no word of the sentence)."""


_NEVER = re.compile(r"(?!)")
_ASKED = {"star": re.compile(r"\bstar", re.IGNORECASE), "label": re.compile(r"\blabel", re.IGNORECASE),
          "archive": re.compile(r"\barchiv|\bout of (?:my |the )?inbox|\bskip (?:the |my )?inbox", re.IGNORECASE)}


def check_words(words: Any) -> str:
    if not isinstance(words, str):
        raise SetupRefused("Tell Jarvis in one sentence what the job should do")
    if "\n" in words.strip() or "\r" in words:
        raise SetupRefused("Set a job up with one sentence, on one line")
    text = " ".join(words.split())
    if len(text) < 8:
        raise SetupRefused("Tell Jarvis in one sentence what the job should do")
    if len(text) > MAX_WORDS:
        raise SetupRefused(f"That is too long for one job (at most {MAX_WORDS} characters)")
    return text


def setup_prompt(words: str, accounts: Mapping[str, Sequence[str]], *, now: datetime, time_zone_name: str) -> str:
    rows = []
    for alias, can in accounts.items():
        rows.append(f"{data(alias, 24)} | read_mail={'yes' if 'read' in can else 'no'} | "
                    f"calendar={'yes' if 'calendar' in can else 'no'}")
    return "\n\n".join([
        f"<now>{now.strftime('%A %Y-%m-%d %H:%M')} (time zone {data(time_zone_name, 60) or 'unknown'})</now>",
        "<accounts>\n" + ("\n".join(rows) or "none") + "\n</accounts>",
        f"<command>{data(words, MAX_WORDS)}</command>"]) + "\n"


def read_setup(event: Mapping[str, Any], init: Any) -> Mapping[str, Any] | AskFailure:
    found = structured(event, init)
    if isinstance(found, AskFailure):
        return found
    if found.get("kind") not in ("agent", "rule", "neither") or not isinstance(found.get("say"), str):
        return failure(GARBLED, "shape")
    return found


def definition(answer: Mapping[str, Any], words: str, *, aliases: Sequence[str], readable: Sequence[str],
               labels: Callable[[str], Sequence[tuple[str, str]]] | None = None, min_every: int = 30,
               now: datetime | None = None, docs_dir: Any = None) -> Agent | Rule | None:
    kind = answer.get("kind")
    if kind == "neither":
        return None
    created = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    name = data(answer.get("name") or "", 40) or ("New rule" if kind == "rule" else "New agent")
    try:
        if kind == KIND_RULE:
            return _rule(answer.get("rule") or {}, words, name, created, aliases, readable, labels)
        if kind == KIND_AGENT:
            return _agent(answer.get("agent") or {}, words, name, created, aliases, readable, min_every, docs_dir)
    except FilterRefused as refused:
        raise SetupRefused(str(refused)) from None
    except DefinitionError as error:
        raise SetupRefused(f"Jarvis could not make that job ({error})") from None
    raise SetupRefused("Jarvis could not tell what kind of job that is")


def _rule(spec: Mapping[str, Any], words: str, name: str, created: str, aliases: Sequence[str],
          readable: Sequence[str], labels: Callable[[str], Sequence[tuple[str, str]]] | None) -> Rule:
    account = spec.get("account") or (readable[0] if len(readable) == 1 else "")
    if account not in aliases:
        raise SetupRefused("Say which account the rule watches (personal, work ...)")
    if account not in readable:
        raise SetupRefused(f'The {account} account can\'t read mail yet (config.toml features "gmail_read")')
    tidy = dict(spec.get("tidy") or {})
    tidy_kind = tidy.get("kind") or TIDY_NONE
    if tidy_kind != TIDY_NONE and not _ASKED.get(tidy_kind, _NEVER).search(words):
        tidy_kind = TIDY_NONE                     # the owner's own words never asked for it: no tidy-up
    query = check_filter(spec.get("query"), words, needs_sender=tidy_kind != TIDY_NONE)
    tidy_data: dict[str, Any] = {"kind": tidy_kind}
    if tidy_kind == TIDY_LABEL:
        wanted = " ".join(str(tidy.get("label_name") or "").split()).casefold()
        found = [(label_id, label) for label_id, label in (labels(account) if labels else ())
                 if label.casefold() == wanted]
        if not wanted or not found:
            raise SetupRefused("Jarvis found no Gmail label of that name: make the label in Gmail first")
        tidy_data.update(label_id=found[0][0], label_name=found[0][1])
    action = dict(spec.get("action") or {})
    action.setdefault("minutes", 60)
    return rule_from({"id": new_id(KIND_RULE), "name": name, "sentence": words, "account": account, "query": query,
                      "question": spec.get("question") or "", "fields": list(spec.get("fields") or []),
                      "action": action, "tidy": tidy_data, "status": STATUS_DRAFT, "created": created})


def lands_of(spec: Any, docs_dir: Any) -> dict[str, Any] | None:
    """Where an agent's reports land (build plan item 5): the document or sheet the owner named - found by its name in
    the docs folder, read only (docs.store.find_by_name) - or a new one Turn on makes (a new sheet's columns checked
    by docs.model). None: none asked for, or [docs] is off here (``docs_dir`` None)."""
    if docs_dir is None or not isinstance(spec, Mapping) or spec.get("kind") not in ("doc", "sheet"):
        return None
    from ..docs import model as docs_model
    from ..docs import store as docs_store

    kind = spec["kind"]
    name = docs_model.clean_name(spec.get("name") or "")
    if not name:
        raise SetupRefused("Say which document or sheet the agent writes to")
    found = docs_store.find_by_name(docs_dir, name, kind)
    if found is not None:
        return {"kind": kind, "id": found.id, "name": found.name, "columns": []}
    columns = list(spec.get("columns") or []) if kind == "sheet" else []
    if kind == "sheet" and not columns:
        raise SetupRefused("Say which columns the new sheet has (Company, Role, Deadline ...)")
    return {"kind": kind, "id": "", "name": name, "columns": columns}


def _agent(spec: Mapping[str, Any], words: str, name: str, created: str, aliases: Sequence[str],
           readable: Sequence[str], min_every: int, docs_dir: Any = None) -> Agent:
    mail = []
    for source in spec.get("mail") or ():
        account = source.get("account") if isinstance(source, Mapping) else None
        if account not in aliases:
            raise SetupRefused("Say which account the agent reads (personal, work ...)")
        if account not in readable:
            raise SetupRefused(f'The {account} account can\'t read mail yet (config.toml features "gmail_read")')
        mail.append({"account": account, "query": check_filter(source.get("query"), words)})
    return agent_from({"id": new_id(KIND_AGENT), "name": name, "words": words, "trigger": spec.get("trigger") or {},
                       "mail": mail, "calendar_days": spec.get("calendar_days") or 0,
                       "notes": bool(spec.get("notes")), "proposes": list(spec.get("proposes") or []),
                       "report": spec.get("report") or "when_needed", "status": STATUS_DRAFT, "created": created,
                       **({"lands": lands} if (lands := lands_of(spec.get("lands"), docs_dir)) else {})},
                      min_every=min_every)
