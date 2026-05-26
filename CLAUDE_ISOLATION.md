# Claude Isolation Architecture

## Problem

When Claude Code encounters errors (broken tools, unexpected page states), it
falls back to reading project source files — `Read`, `Glob`, `Bash` — to
understand what's happening.  If its working directory is the project root, it
can read `agent_eval/mcp_server.py`, `README.md`, `TASK.md`, etc., which:

- Leaks implementation details that bias the evaluation
- Lets Claude "cheat" by reading task definitions or verifier logic
- Makes the evaluation measure "can Claude read source code" rather than
  "can Claude operate a browser"

## Precautions

### 1. Clean working directory + opaque MCP config

Claude's `cwd` is `/tmp/shopgym_{session_id}/` — a fresh temp directory
containing only:

```
.mcp.json    ← opaque MCP config
server.py    ← proxy script (mcp_proxy.py, docstring stripped)
hook.py      ← allow/deny hook (no task details)
```

The `.mcp.json` uses `/usr/bin/python3` (system Python, resolved via
`shutil.which`) and references only `/tmp/` paths — no project directory
is visible anywhere in the config Claude can read:

```json
{
  "mcpServers": {
    "browser": {
      "command": "/usr/bin/python3",
      "args": ["/tmp/shopgym_{id}/server.py", "/tmp/shopgym_{id}/mcp.sock"]
    }
  },
  "hooks": {
    "PreToolUse": [{
      "matcher": "*",
      "hooks": [{"type": "command", "command": "/usr/bin/python3 /tmp/shopgym_{id}/hook.py"}]
    }]
  }
}
```

Both `server.py` and `hook.py` use only Python stdlib — no venv needed.

### 2. MCP server hidden behind a Unix socket

The real MCP server (`mcp_server.py`) runs in the project directory and is
never visible to Claude.  Claude's `server.py` is an opaque stdio↔socket
bridge that connects to a Unix socket; it has no knowledge of what's on the
other end.

### 3. Tool allowlist — two independent layers

**Layer 1 — `--allowedTools` CLI flag** (process level): Claude's process is
started with an explicit whitelist of the 8 `mcp__browser__*` tools.  Any
other tool call is rejected before it reaches the hook.

**Layer 2 — `PreToolUse` hook** (per-call level): `hook.py` runs before every
tool call and returns `{"decision": "deny"}` for anything not prefixed
`mcp__browser__`.

### 4. `--permission-mode dontAsk` and `--max-turns 50`

`--permission-mode dontAsk` means denied tool calls fail immediately rather
than blocking the session.  `--max-turns 50` bounds cost and prevents runaway
loops.

### 5. Errors returned as strings, not exceptions

`mcp_server.py`'s `_run_tool()` catches all exceptions and returns
`"ERROR: <first line>"` as a normal tool result instead of raising.  Claude
receives the error and retries; the browser session stays alive; Claude has no
reason to fall back to filesystem exploration.

### 6. Goal string contains everything Claude needs

The goal string includes the shop URL, the explicit list of 8 tools, and
selector syntax hints.  Claude has no reason to explore the filesystem.

---

## Process Tree

```
task_runner.py
  ├── mcp_socket_server.py        ← hidden from Claude; in project dir
  │     └── mcp_server.py         ← FastMCP browser server (stdio pipes)
  │           └── chromium        ← Playwright browser
  └── claude (cwd = /tmp/shopgym_{id}/)
        └── server.py             ← copy of mcp_proxy.py, docstring stripped
              └── Unix socket ────┘ connects to mcp_socket_server.py
```


