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

## Principles

1. **Claude's `cwd` is a clean temp directory** — `/tmp/shopgym_{session_id}/`
   with no project files.  Claude cannot `ls` or `Read` anything useful.

2. **The MCP server is hidden behind a Unix socket** — Claude's `.mcp.json`
   references only an opaque proxy script.  No project paths appear in the
   config Claude can read.

3. **Errors are returned as strings, not exceptions** — bad selectors, wrong
   URLs, network errors all come back as `"ERROR: ..."` tool results.  Claude
   sees the error and retries; the MCP server never crashes.

4. **Claude is told exactly what it needs** — the goal string includes the site
   URL, the tool list, and selector syntax hints.  Claude has no reason to
   explore the filesystem.

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

## Implementation

### `agent_eval/mcp_proxy.py`

The **client-side** stdio↔socket bridge.  Copied into Claude's workdir as
`server.py` (module docstring stripped so no project name is visible).

- Connects to the Unix socket at the path given as `argv[1]`
- Two threads: `os.read(stdin_fd)` → `sock.sendall()` and
  `sock.recv()` → `stdout.write()` + `stdout.flush()`
- Uses `os.read()` on raw file descriptors (not `file.read(n)`) so data
  arrives immediately without waiting for a full buffer

### `agent_eval/mcp_socket_server.py`

The **server-side** socket listener.  Started by `task_runner.py`; Claude
never sees this file.

- Starts `mcp_server.py` as a subprocess with stdio pipes
- Binds a Unix socket, prints `READY <path>` to stdout, then accepts one
  connection
- Two threads relay bytes bidirectionally:
  - `sock.recv()` → `pipe.write()` + `pipe.flush()` (flush is critical —
    `subprocess.PIPE` stdin is a `BufferedWriter`)
  - `os.read(stdout_fd)` → `sock.sendall()` (raw fd avoids blocking reads)
- Waits for `mcp_server.py` to exit, then cleans up the socket file

### `agent_eval/mcp_server.py` — error handling

`_run_tool()` catches all exceptions and returns `"ERROR: <first line>"` as a
string result instead of raising.  This means:

- FastMCP never sees an exception → no traceback, no MCP error response
- Claude receives the error as a normal tool result and can retry
- The browser session stays alive across bad selector / bad URL errors

### `agent_eval/task_runner.py`

Key changes to `run_session()`:

1. **`_write_hook(session_dir)`** — writes `pre_tool_use.py` to the session
   artifacts dir (not Claude's workdir).  Returns the hook path.

2. **`_setup_claude_workdir(session_id, sock_path, hook_path)`** — creates
   `/tmp/shopgym_{session_id}/` containing:
   - `server.py` — `mcp_proxy.py` with module docstring stripped
   - `.mcp.json` — references `server.py` and the socket path (both in
     `/tmp/`, no project paths)

3. **`_start_mcp_socket_server(...)`** — starts `mcp_socket_server.py`,
   waits for `READY` line on stdout before returning.

4. **`_launch_claude(goal, claude_workdir, ...)`** — passes
   `cwd=str(claude_workdir)` to `asyncio.create_subprocess_exec()`.

5. **Goal wrapper** — the goal string includes:
   - The shop URL (`http://localhost:5299`)
   - Explicit tool list (so Claude doesn't call `ToolSearch`)
   - Playwright selector syntax guide (`text=Cancel Order` preferred)
   - Browser protocol instructions (navigate/click return ARIA tree)

**Ordering matters:** `_setup_claude_workdir()` runs before
`_start_mcp_socket_server()` because the socket file lives inside
`/tmp/shopgym_{id}/` which must exist first.

## What Claude Sees

Claude's working directory `/tmp/shopgym_{session_id}/` contains:

```
.mcp.json    ← opaque MCP config
server.py    ← proxy script (no docstring, starts with "import os")
```

Claude's `.mcp.json`:

```json
{
  "mcpServers": {
    "browser": {
      "command": "/path/to/python",
      "args": [
        "/tmp/shopgym_{id}/server.py",
        "/tmp/shopgym_{id}/mcp.sock"
      ]
    }
  },
  "hooks": { ... }
}
```

All paths are in `/tmp/` — no project directory is visible.

## What Claude Cannot See

- `agent_eval/mcp_server.py` — hidden behind the socket relay
- `agent_eval/task_runner.py` — not in Claude's cwd
- `tasks/cancel_order.py` etc. — task verifier logic
- `shop/` — Flask app source
- `README.md`, `TASK.md`, `TASK2.md` — project documentation
- Any file in `/mnt/d/p/gym/` — Claude's cwd is `/tmp/shopgym_*/`

## What Remains Visible (Unavoidable)

- **Python interpreter path** — `/path/to/agent_eval/.venv/bin/python` appears
  in `.mcp.json` `command` and hook `command`.  This leaks the venv path but
  not the project source code.
- **Hook path** — `_tmp/runs/{session_id}/hooks/pre_tool_use.py` appears in
  the hook command.  This reveals `_tmp/runs/` but the hook file itself only
  contains the allow/deny logic, no task details.
- **Socket path** — `/tmp/shopgym_{id}/mcp.sock` is visible in `.mcp.json`
  args.  It's in `/tmp/` and reveals nothing about the project.

## Session Artifacts

All evaluation artifacts go to `_tmp/runs/{session_id}/` (project dir, not
Claude's temp dir):

```
_tmp/runs/{session_id}/
  .mcp.json           ← not used (Claude uses /tmp/shopgym_*/. mcp.json)
  hooks/
    pre_tool_use.py   ← PreToolUse hook (blocks non-browser tools)
  mcp.sock            ← Unix socket (deleted after session)
  trajectory.jsonl    ← one line per tool call
  trajectory.txt      ← human-readable summary
  trace.zip           ← Playwright trace
  video.webm          ← session recording
  result.json         ← final validation result
  shop.db             ← SQLite DB for this session
```

Claude's temp dir `/tmp/shopgym_{session_id}/` is left in place after the
session (not cleaned up) so it can be inspected for debugging.
