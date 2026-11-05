"""Jarvis, a true agent: a task the owner gives is worked out step by step by his own Claude Code, over tools only
Jarvis serves (SPEC "Jarvis, a true agent"). Qt-free; the app's side is agent_ui.

Off unless [agent] enabled (or JARVIS_AGENT_ENABLED) is true, with [desktop] and [ask] on: off means no endpoint,
nothing started and no new word in any prompt or schema. Built so far (part A1, the engine):

* ``tools``: the registry - each tool's name, description, input schema, tier and handler; the one source for tools/list
  and --allowedTools. This part has update_plan, say, ask_owner, finish, write_file, read_file and list_files.
* ``workspace``: the task's own folder, the one place a task may write without a click (text-like files, with caps).
* ``endpoint``: the local MCP endpoint: 127.0.0.1 only, a per-run token, handlers off the HTTP thread, 45 s at most.
* ``policy``: what is offered, the budgets, the tasks a day and the seam the firewall joins in later parts.
* ``runner``: one run: the process, its stream checked event by event, messages over stdin, waits that end the turn.
* ``assets``: ``agent_prompt.md`` and ``agent_settings.json``.

Nothing in this package imports Qt or starts a process or a shell by itself (tests/test_agent_guard.py runs the desktop
package's scan over it; the run's one process comes through ask.cli's runner seam), and nothing logs a goal, a query, a
file name, a tool's arguments, a token or anything typed: ids, kinds, counts, states, reason codes and durations only.
"""
