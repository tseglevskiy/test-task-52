# ShopGym — Agent Evaluation Harness

A sandboxed, resettable e-commerce environment for evaluating real agents
(Claude Code) on browser tasks. The agent interacts with the shop exclusively
through MCP browser tools; every action is recorded; outcomes are validated
by deterministic verifiers.

---

## TL;DR — Running an evaluation

```bash
# Setup (once)
python -m venv agent_eval/.venv
agent_eval/.venv/bin/pip install -r agent_eval/requirements.txt
agent_eval/.venv/bin/playwright install chromium

# Quick demo scripts (uses 'claude' from PATH, or pass path as first arg)
chmod +x demo/*.sh
./demo/run_cancel_order.sh
./demo/run_apply_coupon.sh
./demo/run_buy_cheapest.sh

# Or call task_runner.py directly
agent_eval/.venv/bin/python agent_eval/task_runner.py \
    --task cancel_order \
    --seed 0 \
    --claude /path/to/claude
```

Claude runs headless by default. Artifacts land in `_tmp/runs/{session_id}/`.

For step-by-step setup and task descriptions see **[`demo/README.md`](demo/README.md)**.  
For full harness details see **[`agent_eval/README.md`](agent_eval/README.md)**.

---

## Repository layout

```
gym/
├── shop/                      # The e-commerce Flask app (do not modify)
│   ├── app.py                 # All routes + /api/reset + /api/db-state
│   ├── db.py                  # SQLite schema
│   ├── seed.py                # Deterministic DB seeder
│   ├── vocab.py               # Static product vocabulary
│   ├── templates/             # Jinja2 HTML templates
│   ├── Dockerfile
│   ├── README.md              # Route table, data schema, API reference
│   ├── SEEDING.md             # Seeder design and determinism guarantees
│   └── DOCKER.md              # How to build, run, and tear down Docker instances
├── tasks/                     # Concrete task implementations + verifier tests
│   ├── base.py                # AbstractTask interface (seed_requirements, setup, check_trajectory, rubric, verify)
│   ├── cancel_order.py        # CancelRecentOrderTask
│   ├── apply_coupon.py        # ApplyCouponWithQuantityTask
│   ├── buy_cheapest.py        # BuyCheapestInCategoryTask
│   ├── README.md              # How to write a new task (start here)
│   └── tests/
│       └── test_verifiers.py  # Unit tests for task verifiers (no Docker needed)
├── agent_eval/                # Agent evaluation harness
│   ├── mcp_server.py          # FastMCP browser server: tools + trajectory logging
│   ├── mcp_socket_server.py   # Unix-socket listener wrapping mcp_server.py
│   ├── mcp_proxy.py           # stdio↔socket relay (copied into Claude's clean cwd)
│   ├── task_runner.py         # CLI orchestrator: Flask → reset → Claude → verify
│   ├── trajectory.py          # TrajectoryWriter: JSONL + human-readable .txt
│   ├── validators/
│   │   ├── base.py            # AbstractTrajectoryValidator interface
│   │   ├── deterministic.py   # DeterministicValidator — rule-based, instant, no API
│   │   ├── llm_judge.py       # LLMJudgeValidator — OpenRouter LLM judge
│   │   └── stub.py            # StubValidator — always passes (kept for offline testing)
│   ├── requirements.txt
│   └── README.md              # Full usage docs, artifact layout, how to add a validator
├── demo/                      # Ready-to-run demo scripts (start here)
│   ├── run_cancel_order.sh    # Run the cancel_order task
│   ├── run_apply_coupon.sh    # Run the apply_coupon task
│   ├── run_buy_cheapest.sh    # Run the buy_cheapest task
│   └── README.md              # Full setup + usage guide (start here)
├── docker-compose.yml         # 4 shop instances on ports 5001-5004
├── TASK.md                    # Original task specification
├── TASK2.md                   # TASK2 specification (this implementation)
└── CLAUDE_ISOLATION.md        # Isolation architecture: why and how Claude is sandboxed
```

---

## Architecture

```
task_runner.py  (session orchestrator)
  |
  ├── Flask subprocess  →  http://localhost:5299  (shop + SQLite)
  |
  ├── mcp_socket_server.py  (Unix-socket listener, hidden from Claude)
  |     └── mcp_server.py  (FastMCP, stdio pipes)
  |           └── Chromium  (Playwright, headless)
  |
  └── claude  (cwd = clean /tmp/shopgym_{id}/, no project files)
        └── mcp_proxy.py  (opaque stdio↔socket bridge in Claude's cwd)
              └── Unix socket ──→ mcp_socket_server.py

_tmp/runs/{session_id}/  (all session artifacts)
  shop_seed.db        SQLite snapshot before agent ran
  shop.db             SQLite snapshot after agent ran
  db_state_seed.json  full DB state as JSON before agent ran
  db_state_final.json full DB state as JSON after agent ran
  shop.jsonl          shop event log (add-to-cart, checkout, cancel, etc.)
  trajectory.jsonl    one line per MCP tool call
  trajectory.txt      human-readable step summary
  screenshots/        PNG per screenshot tool call
  trace.zip           Playwright trace (interactive replay)
  video.webm          session recording
  result.json         {passed, end_state, trajectory_deterministic, trajectory_llm, ...}
```

**Isolation**: Claude's working directory is a clean `/tmp/shopgym_{id}/`
containing only an opaque proxy script and a `.mcp.json` that references
only `/tmp/` paths. Claude cannot read project source, task definitions,
or verifier logic. See **[`CLAUDE_ISOLATION.md`](CLAUDE_ISOLATION.md)** for details.

One Flask process = one SQLite file. The agent can only interact with the
shop through MCP browser tools — all Claude Code built-in tools are blocked
by a PreToolUse hook registered in `.mcp.json`.

---

## Tasks

| Task | What the agent must do |
|---|---|
| `cancel_order` | Navigate to Orders → open the most recent order → cancel it |
| `apply_coupon` | Find SKU-E7421 in Electronics → add qty 2 → apply coupon SAVE10 → checkout |
| `buy_cheapest` | Find the cheapest Electronics item → buy it → ship to 123 Main St, Springfield, IL |

Each task is validated three ways: end-state DB check, deterministic
trajectory rules, and an LLM judge — all must pass for `result.passed = true`.

See **[`tasks/README.md`](tasks/README.md)** for the full task authoring guide.

---

## Adding a new task

1. Create `tasks/your_task.py` — subclass `AbstractTask`, implement all five methods:
   `seed_requirements()`, `setup()`, `check_trajectory()`, `rubric()`, `verify()`
2. Register in `_load_task()` in `agent_eval/task_runner.py`
3. Register in `_load_task_instance()` in `agent_eval/validators/deterministic.py`
4. Register in `_get_rubric()` in `agent_eval/validators/llm_judge.py`
5. Add a verifier unit test in `tasks/tests/test_verifiers.py`

Full step-by-step guide with code examples: **[`tasks/README.md`](tasks/README.md)**

---

## Viewing session artifacts

```bash
# Interactive Playwright trace replay
playwright show-trace _tmp/runs/{session_id}/trace.zip

# Human-readable step log
cat _tmp/runs/{session_id}/trajectory.txt

# Screenshots taken by the agent
ls _tmp/runs/{session_id}/screenshots/

# Full result JSON
cat _tmp/runs/{session_id}/result.json
```

---

## Running verifier unit tests

```bash
# Setup (once)
python -m venv tasks/.venv
tasks/.venv/bin/pip install pytest requests

# Run
tasks/.venv/bin/python -m pytest tasks/tests/test_verifiers.py -v
```

No Docker or running Flask needed — `requests.get` is mocked.

---

## Key design decisions

### Shop engine: Flask + SQLite
Custom Flask app gives full control over schema, reset is a single
transaction (~100ms), and one language across the whole project.
`/api/reset` wipes and re-seeds deterministically from a `SeedConfig`;
`/api/db-state` returns the full DB as JSON for verifiers.

### No Gymnasium
Gymnasium's `env.step(action)` assumes the agent is a function inside
your process. Claude Code is an external process with its own reasoning
loop. Dropping Gymnasium and building a task runner that hands the agent
a browser via MCP is the right interface for this use case.

### MCP as sole interface + isolation
The agent can only interact with the world through MCP browser tools.
All Claude Code built-in tools are blocked by a PreToolUse hook. The MCP
server is hidden behind a Unix socket relay so Claude cannot read its
source or discover project paths. See `CLAUDE_ISOLATION.md`.

### Explicit observation (no auto-DOM)
`navigate`, `click`, `type_text`, and `scroll` return `"ok"`. The agent
calls `get_dom` or `screenshot` explicitly when it needs to observe state.
This keeps tool results small, gives the agent control over observation
frequency, and makes the trajectory easier to read.

### Playwright tracing as observability
`playwright show-trace` gives an interactive step-by-step replay with
DOM snapshots, screenshots, and network — better than anything you'd
build yourself in this time budget.

### Three-layer validation
Every session runs three independent validators:

1. **End-state verifier** (`task.verify()`) — checks the final DB state via
   `GET /api/db-state`. Deterministic, instant, task-specific.

2. **Deterministic trajectory validator** (`DeterministicValidator`) — rule-based
   checks on the recorded tool calls. Defined in each task file as
   `check_trajectory()`. Instant, free, fully reproducible. Catches clear-cut
   violations like navigating directly to a cancel URL instead of using the UI.

3. **LLM judge** (`LLMJudgeValidator`) — sends the trajectory to an LLM via
   OpenRouter with a task-specific rubric (defined in `task.rubric()`). Catches
   subtler behavioral issues. Requires `OPENROUTER_API_KEY` in `.env`.

All three must pass for `result["passed"]` to be `true`.
