"""Jarvis, a true agent: a task the owner gives is worked out step by step by his own Claude Code, over tools only
Jarvis serves (SPEC "Jarvis, a true agent"). Qt-free but for ``browser``; the app's side is agent_ui.

Off unless [agent] enabled (or JARVIS_AGENT_ENABLED) is true, with [desktop] and [ask] on: off means no endpoint,
nothing started and no new word in any prompt or schema. Built so far (parts A1, the engine, A2, the browser, and A3,
the feed, whose windows are in hud_overlay and agent_feed):

* ``tools``: the registry - each tool's name, description, input schema, tier and handler; the one source for tools/list
  and --allowedTools: update_plan, say, ask_owner, finish, write_file, read_file, list_files, web_search, open_page,
  read_more, fetch_text, look_at_page and show_file.
* ``fetch``: fetch_text's short run of Claude Code's own declared fetcher, when a site refuses the window.
* ``workspace``: the task's own folder, the one place a task may write without a click (text-like files, with caps).
* ``endpoint``: the local MCP endpoint: 127.0.0.1 only, a per-run token, handlers off the HTTP thread, 45 s at most.
* ``policy``: what is offered, the budgets, the tasks a day and the seam the firewall plugs into.
* ``web``: what a search and a page read are: the engines, the scripts Jarvis runs in an isolated world, the numbered
  addresses, a run's use of the window. ``firewall``: the rules for what may go out to the web (taint, seen addresses).
* ``browser``: the window the owner watches, on the page snapshots' private web engine and network gate. Qt, GUI thread.
* ``runner``: one run: the process, its stream checked event by event, messages over stdin, waits that end the turn.
* ``assets``: ``agent_prompt.md`` and ``agent_settings.json``.

Nothing in this package but ``browser`` imports Qt, nothing starts a process or a shell by itself
(tests/test_agent_guard.py runs the desktop package's scan over it; the run's one process comes through ask.cli's runner
seam), and nothing logs a goal, a query, an address, a page's words, a file name, a tool's arguments, a token or
anything typed: ids, kinds, counts, states, reason codes and durations only.
"""
