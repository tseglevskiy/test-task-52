# agent_eval — Agent Evaluation Harness

Evaluates Claude Code against the ShopGym e-commerce site. The agent
interacts with the shop exclusively through MCP browser tools; every
action is recorded; outcomes are validated by deterministic verifiers.

---

## Setup

```bash
cd /path/to/gym
python -m venv agent_eval/.venv
agent_eval/.venv/bin/pip install -r agent_eval/requirements.txt
agent_eval/.venv/bin/playwright install chromium
```

Requires the `claude` CLI to be installed and authenticated.

---

## Running an evaluation

```bash
agent_eval/.venv/bin/python agent_eval/task_runner.py \
    --task cancel_order \
    --seed 0 \
    --claude claude
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--task` | `cancel_order` | Task to evaluate: `cancel_order`, `apply_coupon`, `buy_cheapest` |
| `--seed` | `0` | Integer seed for deterministic DB reset |
| `--claude` | `claude` | Path to the `claude` CLI binary |
| `--headed` | off | Run browser headed/visible (default: headless). Requires a display (not WSL). |
| `--docker` | off | Start shop in Docker instead of subprocess. Requires `docker build -t shopgym-shop:latest shop/` |
| `--timeout` | `300` | Max seconds before killing Claude |

---

## What happens

```
task_runner.py
  1. Starts Flask shop subprocess on port 5299
  2. POST /api/reset  →  deterministic DB seed
  3. task.setup()     →  snapshot pre-state, get goal string
  4. Writes .mcp.json + hooks/pre_tool_use.py to session dir
  5. Launches: claude -p "{goal}" --mcp-config .mcp.json --max-turns 50
       └── mcp_server.py (FastMCP, stdio)
             └── Chromium (Playwright, headless by default)
  6. Streams Claude's output to terminal live
  7. On exit/timeout: SIGTERM → 3s grace → SIGKILL
  8. task.verify()    →  end-state check via GET /api/db-state
  9. StubValidator    →  trajectory check (always passes — stub)
 10. Writes result.json, prints summary
```

---

## Session artifacts

Each run creates `_tmp/runs/{session_id}/`:

```
_tmp/runs/20240518_143201_abc123/
  .mcp.json           MCP server config (written before launch)
  hooks/
    pre_tool_use.py   Blocks non-browser tools
  trajectory.jsonl    One JSON line per MCP tool call
  trajectory.txt      Human-readable step-by-step summary
  trace.zip           Playwright trace (interactive replay)
  video.webm          Full session video
  result.json         Final validation result
  shop.db             SQLite DB snapshot at end of session
```

**View the Playwright trace:**
```bash
playwright show-trace _tmp/runs/{session_id}/trace.zip
```

**Read the trajectory:**
```bash
cat _tmp/runs/{session_id}/trajectory.txt
```

**Check the result:**
```bash
cat _tmp/runs/{session_id}/result.json
```

---

## Tasks

| Task | Goal |
|---|---|
| `cancel_order` | Cancel the most recent existing order in the account |
| `apply_coupon` | Add 2 units of SKU-E7421 to the cart, apply coupon SAVE10, and complete checkout |
| `buy_cheapest` | Buy the cheapest item in the Electronics category and ship to 123 Main St, Springfield, IL 62701 |

Each task has a deterministic verifier in `tasks/` that reads backend state
via `GET /api/db-state` — no HTML scraping, no LLM judge.

---

## Tool restriction

The agent can only use MCP browser tools. All Claude Code built-in tools
(Bash, Read, Write, WebFetch, etc.) are blocked by two layers:

1. `--allowedTools mcp__browser__*` — soft hint to the model
2. `hooks/pre_tool_use.py` — hard gate: denies any non-browser tool call

Available MCP tools: `navigate`, `click`, `type_text`, `select_option`,
`scroll`, `screenshot`, `get_dom`, `get_url`.
`select_option` is used for `<select>` dropdowns (e.g. the State field on checkout).

---

## Trajectory validation

After each session, `StubValidator` is called — it always returns
`{"passed": True, "violations": [], "reasoning": "stub"}`.

To implement a real validator:
1. Subclass `AbstractTrajectoryValidator` in `agent_eval/validators/`
2. Implement `validate(trajectory, task_name, goal) -> dict`
3. Replace `StubValidator()` in `task_runner.py` with your class

The trajectory format (one dict per step in `trajectory.jsonl`) is
documented in `agent_eval/trajectory.py`.

---

## Architecture

```
Host machine
│
├── agent_eval/task_runner.py  (session orchestrator)
│     ├── Flask subprocess  →  http://localhost:5299  (shop)
│     ├── claude subprocess  →  MCP stdio
│     │     └── agent_eval/mcp_server.py  →  Chromium (Playwright)
│     ├── task.verify()  →  GET /api/db-state  (end-state check)
│     └── StubValidator  →  trajectory.jsonl  (trajectory check)
│
└── _tmp/runs/{session_id}/  (all artifacts)
```
