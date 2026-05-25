# ShopGym Demo — Running Agent Evaluations

This folder contains ready-to-run scripts for each evaluation task. Each script
starts the shop, resets the database to a deterministic state, launches Claude
Code as a browser agent, and reports whether the task passed or failed.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ (3.12 recommended) | `python --version` |
| `claude` CLI | latest | Must be installed and authenticated |
| git | any | To clone the repo |

### Install the Claude Code CLI

Follow the official instructions at <https://docs.anthropic.com/en/docs/claude-code>.
After installation, verify it works:

```bash
claude --version
claude whoami        # must show your authenticated account
```

Find the full path to the binary (you'll need it if it's not on `PATH`):

```bash
which claude
# e.g. /home/yourname/.local/bin/claude
```

---

## One-time environment setup

Run these commands once from the **repository root** (the `gym/` directory):

```bash
# 1. Create the Python virtual environment for the evaluation harness
python -m venv agent_eval/.venv

# 2. Install Python dependencies
agent_eval/.venv/bin/pip install -r agent_eval/requirements.txt

# 3. Install Chromium (the browser the agent drives)
agent_eval/.venv/bin/playwright install chromium
```

> **WSL users:** Playwright runs headless by default, so no display server is
> needed. The `--headed` flag (visible browser window) requires an X11 server
> such as VcXsrv or WSLg. The demo scripts always run headless.

### Shop dependencies (Flask)

The shop runs as a subprocess using its own virtual environment. Set it up once:

```bash
python -m venv shop/.venv
shop/.venv/bin/pip install -r shop/requirements.txt
```

If `shop/.venv` does not exist, `task_runner.py` falls back to the system
Python — which may or may not have Flask installed. Setting up `shop/.venv`
is the safest option.

---

## Running a task

Make the scripts executable (once):

```bash
chmod +x demo/run_cancel_order.sh demo/run_apply_coupon.sh demo/run_buy_cheapest.sh
```

Then run any task from the repository root:

```bash
# If 'claude' is on your PATH:
./demo/run_cancel_order.sh
./demo/run_apply_coupon.sh
./demo/run_buy_cheapest.sh

# If 'claude' is not on PATH, pass the full path as the first argument:
./demo/run_cancel_order.sh /home/yourname/.local/bin/claude

# Or set the CLAUDE_BIN environment variable:
CLAUDE_BIN=/home/yourname/.local/bin/claude ./demo/run_cancel_order.sh
```

---

## The three tasks

### `cancel_order` — Cancel the most recent order

```bash
./demo/run_cancel_order.sh
```

**What the agent must do:**
1. Navigate to the Orders page
2. Open the most recent order
3. Click "Cancel Order"

**How it's verified:** The backend DB is queried via `GET /api/db-state`. The
verifier confirms the most recent order's status is `"cancelled"`.

**Typical duration:** ~60 seconds, ~7 tool calls.

---

### `apply_coupon` — Add a specific product with a coupon and check out

```bash
./demo/run_apply_coupon.sh
```

**What the agent must do:**
1. Find product SKU-E7421 in the Electronics category
2. Set quantity to 2 and add it to the cart
3. Apply coupon code `SAVE10`
4. Complete checkout

**How it's verified:** The verifier confirms an order exists with SKU-E7421,
quantity 2, the SAVE10 discount applied, and status `"completed"`.

**Typical duration:** ~90 seconds, ~10–15 tool calls.

---

### `buy_cheapest` — Buy the cheapest Electronics item

```bash
./demo/run_buy_cheapest.sh
```

**What the agent must do:**
1. Browse the Electronics category
2. Identify the cheapest item (by price)
3. Add it to the cart and complete checkout
4. Enter shipping address: `123 Main St, Springfield, IL 62701`

**How it's verified:** The verifier confirms the purchased item is the
lowest-priced Electronics product and the shipping address matches exactly.

**Typical duration:** ~90–120 seconds, ~12–18 tool calls.

---

## What to expect while it runs

The terminal streams live output with labeled prefixes:

```
[task_runner] ===== SESSION START =====
[task_runner] session_id: 20240518_143201_abc123
[task_runner] task:       cancel_order
[task_runner] Flask subprocess started (PID 12345) on port 5299
[task_runner] Waiting for Flask at http://localhost:5299/api/health ...
[task_runner] Flask is up (attempt 3)
[task_runner] DB reset OK (seed=0)
[task_runner] goal: Cancel the most recent order ...
[task_runner] MCP socket server ready: READY /tmp/shopgym_.../mcp.sock
[task_runner] Launching Claude Code ...

============================================================
[claude] I'll help you cancel the most recent order. Let me start by taking a screenshot.
[claude] tool_use: mcp__browser__screenshot  {}
[tool]   screenshot saved: screenshots/step_001.png
[claude] tool_use: mcp__browser__navigate  {'url': 'http://localhost:5299/orders'}
[tool]   ok
[claude] tool_use: mcp__browser__click  {'selector': 'text=Order #42'}
[tool]   ok
[claude] tool_use: mcp__browser__click  {'selector': 'text=Cancel Order'}
[tool]   ok
[claude] === DONE: Task completed successfully ===
============================================================

[task_runner] Running end-state verifier ...
[task_runner] end_state: {'passed': True, 'order_id': 42, 'status': 'cancelled'}

[task_runner] ===== SESSION RESULT =====
  ✓ PASSED
  task:       cancel_order
  duration:   57.3s
  end_state:  {'passed': True, ...}

  Artifacts in:  /path/to/gym/_tmp/runs/20240518_143201_abc123
```

**Prefix guide:**

| Prefix | Source |
|---|---|
| `[task_runner]` | Orchestrator: Flask, DB reset, session lifecycle |
| `[claude]` | Claude's reasoning text and tool calls |
| `[tool]` | Tool results returned to Claude |
| `[mcp]` | MCP server / Playwright internal logs |

---

## Session artifacts

Every run creates a timestamped directory under `_tmp/runs/`:

```
_tmp/runs/20240518_143201_abc123/
  result.json         ← start here: passed/failed + full details
  trajectory.txt      ← human-readable step-by-step log
  trajectory.jsonl    ← machine-readable: one JSON line per tool call
  screenshots/        ← PNG screenshots taken by the agent
    step_001.png
    step_002.png
    ...
  trace.zip           ← Playwright interactive trace
  video.webm          ← full session screen recording
  shop.db             ← SQLite DB snapshot at end of session
```

### Reading the result

```bash
# Quick pass/fail check
cat _tmp/runs/20240518_143201_abc123/result.json

# Example output:
{
  "session_id": "20240518_143201_abc123",
  "task": "cancel_order",
  "seed": 0,
  "duration_s": 57.3,
  "exit_code": 0,
  "end_state": {"passed": true, "order_id": 42, "status": "cancelled"},
  "trajectory": {"passed": true, "violations": [], "reasoning": "stub"},
  "passed": true
}
```

`"passed": true` means the end-state verifier confirmed the task was completed
correctly by reading the backend database — not by scraping HTML or asking an
LLM to judge.

`"passed": false` means the agent either did not complete the task, completed
it incorrectly, or timed out. Check `trajectory.txt` to see where it went wrong.

### Reading the trajectory

```bash
cat _tmp/runs/20240518_143201_abc123/trajectory.txt
```

Shows each tool call in order: tool name, inputs, result, and a timestamp.

### Interactive Playwright trace

```bash
# Requires playwright to be installed (it is, in agent_eval/.venv)
agent_eval/.venv/bin/playwright show-trace _tmp/runs/20240518_143201_abc123/trace.zip
```

Opens a browser with a step-by-step replay: DOM snapshots, screenshots, and
network requests for every action the agent took.

### Screenshots

```bash
ls _tmp/runs/20240518_143201_abc123/screenshots/
# step_001.png  step_002.png  ...

# View one (Linux with display):
xdg-open _tmp/runs/20240518_143201_abc123/screenshots/step_001.png
```

---

## Advanced options

The demo scripts use sensible defaults. For more control, call `task_runner.py`
directly:

```bash
agent_eval/.venv/bin/python agent_eval/task_runner.py \
    --task cancel_order \
    --seed 42 \
    --claude /path/to/claude \
    --timeout 600
```

| Flag | Default | Description |
|---|---|---|
| `--task` | `cancel_order` | `cancel_order`, `apply_coupon`, or `buy_cheapest` |
| `--seed` | `0` | Integer seed — same seed always produces the same DB state |
| `--claude` | `claude` | Path to the `claude` CLI binary |
| `--headed` | off | Show the browser window (requires a display — not WSL without X11) |
| `--docker` | off | Run the shop in Docker instead of a subprocess |
| `--timeout` | `300` | Kill Claude after this many seconds |

### Running with a different seed

Each seed produces a different but deterministic database state (different
product prices, order history, etc.). Seed 0 is the default for demos.

```bash
agent_eval/.venv/bin/python agent_eval/task_runner.py \
    --task buy_cheapest --seed 7 --claude claude
```

### Running with Docker shop

If you prefer to run the shop in Docker instead of a subprocess:

```bash
# Build the image once
docker build -t shopgym-shop:latest shop/

# Then use --docker
agent_eval/.venv/bin/python agent_eval/task_runner.py \
    --task cancel_order --docker --claude claude
```

---

## Troubleshooting

### `claude binary not found`

The `claude` CLI is not on your `PATH`. Pass the full path:

```bash
./demo/run_cancel_order.sh $(which claude)
```

Or install Claude Code CLI following <https://docs.anthropic.com/en/docs/claude-code>.

### `agent_eval/.venv not found`

Run the one-time setup:

```bash
python -m venv agent_eval/.venv
agent_eval/.venv/bin/pip install -r agent_eval/requirements.txt
agent_eval/.venv/bin/playwright install chromium
```

### `Flask did not start in time`

Port 5299 may be in use. Check with:

```bash
lsof -i :5299
# or
ss -tlnp | grep 5299
```

Kill the conflicting process or wait for it to finish.

### `ModuleNotFoundError: No module named 'flask'`

The shop's venv is missing. Set it up:

```bash
python -m venv shop/.venv
shop/.venv/bin/pip install -r shop/requirements.txt
```

### `Executable doesn't exist at .../chromium`

Chromium wasn't installed for Playwright. Run:

```bash
agent_eval/.venv/bin/playwright install chromium
```

### Claude times out or `passed: false`

- Check `trajectory.txt` to see the last tool call before timeout/failure.
- Check `screenshots/` to see what the browser looked like at each step.
- Open the Playwright trace for a full interactive replay:
  ```bash
  agent_eval/.venv/bin/playwright show-trace _tmp/runs/{session_id}/trace.zip
  ```
- Try increasing the timeout: `--timeout 600`

### WSL: `--headed` shows no window

WSL does not have a display server by default. Either:
- Use the default headless mode (no `--headed` flag) — this is what the demo scripts do.
- Install an X11 server (VcXsrv, Xming) or use WSLg (Windows 11).

---

## How it works (brief)

```
demo/run_cancel_order.sh
  └── agent_eval/task_runner.py
        ├── Flask subprocess  →  http://localhost:5299  (shop + SQLite)
        ├── mcp_socket_server.py  (Unix-socket listener, hidden from Claude)
        │     └── mcp_server.py  (FastMCP, stdio)
        │           └── Chromium  (Playwright, headless)
        └── claude -p "{goal}" --mcp-config .mcp.json
              └── mcp_proxy.py  (opaque stdio↔socket bridge)
                    └── Unix socket ──→ mcp_socket_server.py
```

Claude runs in a clean isolated directory (`/tmp/shopgym_{id}/`) with no
access to project source files. It can only interact with the shop through
seven MCP browser tools: `navigate`, `click`, `type_text`, `scroll`,
`screenshot`, `get_dom`, `get_url`. All other Claude Code built-in tools
(Bash, file read/write, web fetch, etc.) are blocked by a hook.

For full architecture details see [`CLAUDE_ISOLATION.md`](../CLAUDE_ISOLATION.md).
