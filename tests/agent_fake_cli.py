"""A stand-in for Claude Code as a REAL process, for the tests that need one to die: the app quitting mid-run must leave
no process behind, its own or a child's. It is plain Python started by tests/test_agent_app.py with the task's command
line after two arguments of its own; it never starts anything real. It reads --mcp-config, really POSTs to the endpoint,
writes a real stream's first events, starts a child of its own and waits to be killed.

    python agent_fake_cli.py <file for the pids> <the task's command line, without claude.exe>

A pids file ending in .whole: no child and no waiting: it does a small task to the end, finish and all, and leaves when
its stdin closes, as the real one does.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


def post(url: str, token: str, method: str, params: dict) -> dict:
    parts = urlsplit(url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
    connection.request("POST", parts.path, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
                       {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    answer = json.loads(connection.getresponse().read())
    connection.close()
    return answer


def line(event: dict) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def finish(url: str, token: str) -> None:
    call = {"name": "write_file", "arguments": {"name": "note.md", "text": "From a real process."}}
    line({"type": "assistant", "message": {"id": "m2", "role": "assistant", "content": [
        {"type": "tool_use", "id": "t2", "name": "mcp__jarvis__write_file", "input": call["arguments"]}]}})
    post(url, token, "tools/call", call)
    report = {"name": "finish", "arguments": {"summary": "Wrote the note.", "outcome": "done", "outputs": ["note.md"]}}
    line({"type": "assistant", "message": {"id": "m3", "role": "assistant", "content": [
        {"type": "tool_use", "id": "t3", "name": "mcp__jarvis__finish", "input": report["arguments"]}]}})
    post(url, token, "tools/call", report)
    line({"type": "result", "subtype": "success", "is_error": False, "result": "Done.", "num_turns": 3,
          "usage": {"input_tokens": 10, "output_tokens": 5}})
    sys.stdin.read()                      # until its stdin closes


def main() -> None:
    pids_file, argv = Path(sys.argv[1]), sys.argv[2:]
    config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="ascii"))["mcpServers"]["jarvis"]
    url, token = config["url"], config["headers"]["Authorization"].removeprefix("Bearer ")
    tools = post(url, token, "tools/list", {})["result"]["tools"]
    sys.stdin.readline()                  # the goal
    line({"type": "system", "subtype": "init", "tools": ["mcp__jarvis__" + tool["name"] for tool in tools],
          "mcp_servers": [{"name": "jarvis", "status": "connected"}], "skills": [], "apiKeySource": "none",
          "permissionMode": "dontAsk", "model": "fake", "claude_code_version": "2.1.294"})
    line({"type": "assistant", "message": {"id": "m1", "role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "mcp__jarvis__say", "input": {"text": "Working."}}]}})
    post(url, token, "tools/call", {"name": "say", "arguments": {"text": "Working."}})
    if pids_file.suffix == ".whole":
        finish(url, token)
        return
    child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(120)"])
    temporary = pids_file.with_suffix(".part")
    temporary.write_text(f"{os.getpid()} {child.pid}", encoding="ascii")
    os.replace(temporary, pids_file)
    time.sleep(120)


if __name__ == "__main__":
    main()
