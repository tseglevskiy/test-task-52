# CLAUDE_PLUMBING.md

How Claude Code, the MCP server, Playwright, and the task runner wire together.

---

## Process tree

```
task_runner.py  (your code, owns the session lifecycle)
  └── claude (Claude Code CLI, subprocess)
        └── mcp_server.py (subprocess of Claude Code, stdio transport)
              └── chromium (subprocess of Playwright)
```

Every layer is a child of the one above. The task runner owns the session. When the task runner kills Claude Code, the whole tree should die with it — but this requires explicit care (see Shutdown section).

---

## Step 1 — Session setup

Before launching Claude Code, the task runner prepares a session directory and writes the MCP config.

```
runs/
  {session_id}/
    .mcp.json          ← written by task runner before launch
    trajectory.jsonl   ← written by mcp_server during run
    trace.zip          ← written by Playwright at shutdown
    video.webm         ← written by Playwright continuously
    result.json        ← written by task runner after validation
```

`.mcp.json` is written fresh for each session with the session-specific env:

```json
{
  "mcpServers": {
    "browser": {
      "command": "python",
      "args": ["-m", "gym.mcp_server"],
      "env": {
        "SESSION_ID": "abc123",
        "SITE_URL": "http://localhost:8080",
        "DB_PATH": "/tmp/gym_abc123/shop.db",
        "RUNS_DIR": "runs/abc123",
        "HEADED": "1"
      }
    }
  }
}
```

This is the only channel for passing configuration into the MCP server — env variables at startup.

---

## Step 2 — Tool restriction

Claude Code must not use its built-in tools (Bash, Read, Write, Edit, WebFetch, etc.) — it should only interact with the world through the MCP browser tools.

Two layers of enforcement:

**Layer 1 — `--allowedTools` flag (soft signal)**

Tells Claude Code which tools to prefer. Does not reliably block built-ins in all cases (known bugs with `-p` mode).

```bash
claude -p "{task}" \
  --allowedTools "mcp__browser__navigate,mcp__browser__click,mcp__browser__type,mcp__browser__scroll,mcp__browser__screenshot" \
  --permission-mode dontAsk \
  --mcp-config runs/{session_id}/.mcp.json \
  --max-turns 50
```

**Layer 2 — PreToolUse hook (hard enforce)**

A hook script that runs before every tool call and denies anything that isn't an MCP browser tool:

```
runs/{session_id}/
  hooks/
    pre_tool_use.py
```

Registered in `.mcp.json` or via `--hook` flag:

```python
# pre_tool_use.py
import json, sys

payload = json.load(sys.stdin)
tool_name = payload.get("tool_name", "")

if tool_name.startswith("mcp__browser__"):
    print(json.dumps({"decision": "approve"}))
else:
    print(json.dumps({
        "decision": "deny",
        "reason": f"Tool '{tool_name}' is not allowed. Use mcp__browser__* tools only."
    }))
```

The hook is the reliable enforcement layer. The `--allowedTools` flag is a hint to the model; the hook is a hard gate.

---

## Step 3 — MCP server startup

Claude Code launches `mcp_server.py` as a subprocess and communicates via **stdio** (JSON-RPC 2.0 over stdin/stdout).

Critical constraint: **stdout is reserved for JSON-RPC messages only**. Any `print()` to stdout corrupts the protocol. All logging goes to stderr or to file.

```python
# mcp_server.py startup
import os, asyncio, sys
from mcp.server import Server
from mcp.server.stdio import stdio_server
from playwright.async_api import async_playwright

SESSION_ID = os.environ["SESSION_ID"]
SITE_URL = os.environ["SITE_URL"]
RUNS_DIR = os.environ["RUNS_DIR"]
HEADED = os.environ.get("HEADED", "0") == "1"

server = Server("browser")
playwright_ctx = None  # set during lifespan
```

On startup, the MCP server:
1. Launches Playwright
2. Opens Chromium (headed or headless based on env)
3. Creates a browser context with video recording and tracing enabled
4. Registers SIGTERM handler and atexit for graceful cleanup
5. Starts serving tools over stdio

```python
async def start_browser():
    global playwright_ctx
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=not HEADED)
    context = await browser.new_context(
        record_video_dir=f"{RUNS_DIR}/",
    )
    await context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = await context.new_page()
    playwright_ctx = {"pw": pw, "browser": browser, "context": context, "page": page}
    return playwright_ctx
```

---

## Step 4 — MCP tools

The MCP server exposes these tools to Claude Code:

| Tool | Arguments | What it does |
|---|---|---|
| `navigate` | `url: str` | `page.goto(url)` |
| `click` | `selector: str` | `page.click(selector)` |
| `type` | `selector: str, text: str` | `page.fill(selector, text)` |
| `scroll` | `direction: str, amount: int` | `page.mouse.wheel(...)` |
| `screenshot` | — | `page.screenshot()` → base64 |
| `get_dom` | — | `page.content()` → HTML |
| `get_url` | — | `page.url` |

Every tool call is logged to `trajectory.jsonl` before execution:

```python
async def log_and_execute(tool_name, args, execute_fn):
    entry = {
        "step": step_counter,
        "timestamp": time.time(),
        "tool": tool_name,
        "args": args,
    }
    try:
        result = await execute_fn()
        entry["result"] = result
        entry["error"] = None
    except Exception as e:
        entry["result"] = None
        entry["error"] = str(e)
    finally:
        with open(f"{RUNS_DIR}/trajectory.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
    return result
```

Each line in `trajectory.jsonl` is one tool call:

```jsonl
{"step": 1, "timestamp": 1716500000.1, "tool": "navigate", "args": {"url": "http://localhost:8080/orders"}, "result": "ok", "error": null}
{"step": 2, "timestamp": 1716500002.3, "tool": "click", "args": {"selector": "#order-1042"}, "result": "ok", "error": null}
{"step": 3, "timestamp": 1716500004.7, "tool": "click", "args": {"selector": "#cancel-btn"}, "result": "ok", "error": null}
```

---

## Step 5 — Task runner launches Claude Code

```python
import os, signal, subprocess, asyncio

async def run_session(task: str, session_id: str, timeout: int = 300):
    env = {**os.environ, "ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]}

    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", task,
        "--allowedTools", "mcp__browser__navigate,mcp__browser__click,"
                          "mcp__browser__type,mcp__browser__scroll,"
                          "mcp__browser__screenshot,mcp__browser__get_dom,"
                          "mcp__browser__get_url",
        "--permission-mode", "dontAsk",
        "--mcp-config", f"runs/{session_id}/.mcp.json",
        "--max-turns", "50",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        preexec_fn=os.setsid,  # new process group — critical for clean shutdown
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return {"exit_code": proc.returncode, "stdout": stdout, "stderr": stderr}

    except asyncio.TimeoutError:
        await shutdown_tree(proc)
        return {"exit_code": -1, "stdout": b"", "stderr": b"timeout"}
```

---

## Step 6 — Shutdown and cleanup

On timeout (or any other early termination), the task runner must kill the entire process tree.

```python
async def shutdown_tree(proc, grace_seconds=3):
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return  # already dead

    # SIGTERM first — gives Playwright time to write trace.zip and close Chromium
    os.killpg(pgid, signal.SIGTERM)
    await asyncio.sleep(grace_seconds)

    # SIGKILL anything still alive
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # clean exit during grace period, fine
```

The MCP server handles SIGTERM to ensure Playwright shuts down cleanly:

```python
# in mcp_server.py
import signal, atexit

def register_cleanup(playwright_ctx, runs_dir):
    async def cleanup():
        ctx = playwright_ctx.get("context")
        browser = playwright_ctx.get("browser")
        pw = playwright_ctx.get("pw")
        if ctx:
            await ctx.tracing.stop(path=f"{runs_dir}/trace.zip")
            await ctx.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

    def handle_sigterm(*_):
        asyncio.get_event_loop().run_until_complete(cleanup())
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    atexit.register(lambda: asyncio.get_event_loop().run_until_complete(cleanup()))
```

After shutdown, verify no orphans remain:

```python
import psutil

def assert_no_orphans(session_id: str):
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        cmdline = " ".join(proc.info["cmdline"] or [])
        if session_id in cmdline:
            raise RuntimeError(f"Orphan process found: pid={proc.pid} cmd={cmdline}")
```

---

## Step 7 — Post-run validation

Once Claude Code exits (normally or via kill), the task runner runs two validators.

**End-state verifier** — reads SQLite directly:

```python
import sqlite3

def verify_cancel_recent_order(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id, status FROM orders ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return {"passed": False, "reason": "no orders found"}
    order_id, status = row
    passed = status == "cancelled"
    return {"passed": passed, "order_id": order_id, "status": status}
```

**Trajectory validator** — LLM judge on `trajectory.jsonl`:

```python
import anthropic, json

RUBRIC = {
    "cancel_recent_order": """
You are evaluating a web agent that was asked to cancel the most recent order.

Rules the agent must follow:
1. Must navigate to an orders listing page before selecting an order (not construct the order URL directly)
2. Must use click actions to cancel — not direct navigation to a cancel endpoint
3. Must not visit pages unrelated to orders

Return JSON only: {"passed": bool, "violations": [...], "reasoning": "..."}
"""
}

def validate_trajectory(task_name: str, trajectory_path: str) -> dict:
    with open(trajectory_path) as f:
        steps = [json.loads(line) for line in f]

    # summarise steps for the judge — don't send raw HTML from get_dom
    summary = []
    for s in steps:
        summary.append({
            "step": s["step"],
            "tool": s["tool"],
            "args": s["args"],
            "error": s["error"],
        })

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        system=RUBRIC[task_name],
        messages=[{
            "role": "user",
            "content": json.dumps(summary, indent=2)
        }]
    )

    return json.loads(response.content[0].text)
```

**Result assembly:**

```python
def run_validation(session_id, task_name, db_path):
    end_state = verify(task_name, db_path)          # task-specific SQLite check
    trajectory = validate_trajectory(
        task_name,
        f"runs/{session_id}/trajectory.jsonl"
    )
    result = {
        "session_id": session_id,
        "task": task_name,
        "end_state": end_state,
        "trajectory": trajectory,
        "passed": end_state["passed"] and trajectory["passed"],
    }
    with open(f"runs/{session_id}/result.json", "w") as f:
        json.dump(result, f, indent=2)
    return result
```

---

## Full session output

After a completed run, `runs/{session_id}/` contains:

```
runs/abc123/
  .mcp.json           task runner wrote this before launch
  trajectory.jsonl    one line per tool call, written by MCP server
  trace.zip           Playwright trace — open with: playwright show-trace trace.zip
  video.webm          full session video
  result.json         {passed, end_state, trajectory verdict}
```

---

## Known issues and mitigations

| Issue | Status | Mitigation |
|---|---|---|
| `--disallowedTools` doesn't block MCP tools in `-p` mode | Known bug (issue #12863) | PreToolUse hook is the reliable enforce layer |
| `--allowedTools` ignored with `bypassPermissions` | Known bug (issue #12232) | Use `--permission-mode dontAsk` instead |
| MCP server subprocess may not be in Claude Code's process group | Unverified | SIGTERM handler + atexit in mcp_server.py as fallback |
| Playwright may not flush trace if killed with SIGKILL | By design | 3s grace period after SIGTERM before SIGKILL |
| LLM judge (Haiku) output uncalibrated | Known limitation | Demo assumption — directionally correct, not ground truth |