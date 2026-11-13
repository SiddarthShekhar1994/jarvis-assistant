"""Standing agents and rules (no Qt): jobs the owner set up from a chat that wake on their own.

    model      the definitions (Agent, Rule) and their checks
    store      the files in the data folder (one writer each, versioned) and the daily run counts
    schedule   when a job is due (a time on chosen weekdays, an interval), missed runs, quiet hours

Every run is a planner mode with no tools: Jarvis reads the job's sources himself and the run only proposes
cards and writes a report; a rule may also star, label or archive the mail it matched (owner answers of
2026-11-12), each with an Undo receipt. Logs hold ids, kinds, counts, durations and statuses only.
"""
