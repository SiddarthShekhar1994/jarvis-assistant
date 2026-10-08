"""Ask Jarvis: a typed request becomes proposals (NEEDS YOUR OK cards), planned by the owner's own
signed-in Claude Code CLI on the claude.ai plan. Nothing here carries anything out: the cards go
through the same click, undo countdown and executor.run_action path as the briefing's.

    cli.py       finds claude.exe, probes its flags, checks the sign-in (auth status), builds the
                 child environment from an allowlist, builds the argv; SubprocessRunner (no
                 window, Job Object, timeout, Cancel) and the Runner protocol tests replace
    stream.py    reads the stream-json events: the init guard, the rate-limit guard (extra usage
                 or the plan's limit stops the run), then the result -> Plan or a failure
    mail.py      Gmail thread text for the planner: parsing, quoted-history and HTML stripping
                 (linear), size caps, and the check of a search the planner asks for (only what
                 the owner typed)
    context.py   the planner's input (now, accounts, calendar, briefing, contacts, mail, command)
                 and the ContextIndex of every id and address Jarvis supplied
    validate.py  the planner's lines -> cards: kinds Ask may propose, provenance of every id and
                 recipient, caps; source="ask"
    usage.py     the hourly and daily caps and the extra-usage pause (ask_usage.json: counts,
                 durations, tokens only; shared by every Jarvis process under a lock file)
    planner.py   one Ask from request to cards (at most two planner runs, each after a fresh auth
                 status), --ask-check and the dry run

Qt-free. No paid API: the CLI runs only on a claude.ai subscription sign-in (checked before
every run), with an allowlisted environment (no credential variable), no tools, no MCP servers and
no settings files, and a run Claude Code says would use extra usage is stopped; Jarvis never reads
Claude credentials. Logs carry counts, durations, kinds and ids only: never the
request, the context, the planner's text, email text or addresses.
"""
