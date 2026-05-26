# ShopGym: From Gymnasium Loop to Real-Agent Evaluation

This document explains the design evolution from the original specification
([TASK.md](TASK.md)) to the current implementation ([TASK2.md](TASK2.md)).
It is written for both human readers (understanding *why* things changed) and
agentic readers (understanding *what* the system is and how it works today).

---

## The Short Version

| Dimension | V1 (TASK.md) | V2 (TASK2.md / current) |
|---|---|---|
| **Agent model** | Python function called in a loop | Claude Code CLI — external process |
| **Interface** | `env.step(action)` (Gymnasium) | MCP browser tools over stdio |
| **Action format** | Structured dict `{"type": "click", ...}` | Named tool calls (`mcp__browser__click`) |
| **Observation** | Return value of `step()` | Tool return value (ARIA tree / HTML) |
| **Reward** | Scalar float from verifier | Three-layer verdict (see below) |
| **Trajectory** | Not recorded | Full JSONL log, Playwright trace, video |
| **Isolation** | None (agent in same process) | Claude in clean `/tmp/shopgym_{id}/` |
| **Task interface** | `gym.Env` (env-level); no task-level base class | `AbstractTask` with 5 methods |
| **Validation** | End-state only (`/api/db-state`) | End-state + deterministic + LLM judge |
| **Parallelism** | 4+ concurrent `gym.Env` instances | One session per run (API cost constraint) |

The shop itself (`shop/`) is **unchanged** between versions.

---

## V1: Gymnasium-Based Web Gym (TASK.md)

### What it described

TASK.md asked for a classic RL gym: a Python class with a
[Gymnasium](https://gymnasium.farama.org/)-compatible interface.

```python
env = ShopEnv(task="buy_cheapest_in_category", seed=42)
obs, info = env.reset()
while not done:
    action = policy(obs)          # agent is a Python function
    obs, reward, terminated, truncated, info = env.step(action)
```

The **agent** was a Python callable inside the same process as the evaluator.
The **action space** was a structured dict:

```python
{"type": "navigate", "url": "http://localhost:5000/products"}
{"type": "click",    "selector": "#add-to-cart"}
{"type": "type",     "selector": "#qty", "text": "2"}
```

The **observation** was the return value of `step()`: current URL, DOM/
accessibility tree, and a screenshot.

The **reward** was a scalar float computed by a verifier that called
`GET /api/db-state`. Verifiers were task-specific functions, not a shared
interface.

The **parallel rollout demo** was a script that launched 4+ concurrent
`ShopEnv` instances and printed success rates.

### Why it made sense

Gymnasium is the standard interface for RL environments. If the agent is a
learned policy (a neural network), `env.step(action)` is exactly the right
abstraction: the policy produces an action, the environment advances one step,
the policy receives an observation and reward. The gym owns the browser; the
policy is a function.

### What it assumed

- The agent is a **function inside your process** — you call it, it returns.
- You control the action space — actions are structured dicts you define.
- You control the observation — you decide what to include in `obs`.
- Reward is a scalar — the training loop needs a number.
- Parallelism means multiple `gym.Env` instances on one host.

---

## V2: Real-Agent Evaluation Harness (TASK2.md / current)

### What changed and why

The key insight in TASK2.md: **Claude Code is not a function you call**.

Claude Code is an external process with its own reasoning loop. It reads a
natural-language goal, decides what tools to call, calls them, reads the
results, and decides what to do next — all autonomously. You cannot wrap it in
`env.step()`. The Gymnasium interface is simply incompatible with this agent
model.

The shift is from *training* to *evaluation*: instead of running thousands of
rollouts to train a policy, you run one session to evaluate whether a capable
agent can complete a task correctly and via the right behavioral path.

### The new architecture

```
Task Runner  (agent_eval/task_runner.py)
  ├── starts: Flask shop + fresh SQLite DB (seeded deterministically)
  ├── starts: MCP socket server  ←→  MCP server (FastMCP, Playwright)
  │                                        └── logs every tool call → trajectory.jsonl
  ├── launches: Claude Code CLI
  │               cwd = /tmp/shopgym_{id}/   ← clean, no project files
  │               tools = mcp__browser__* only
  │               max-turns = 50
  └── after Claude exits:
        ├── End-state verifier    → task.verify()  via GET /api/db-state
        ├── Deterministic validator → task.check_trajectory()
        └── LLM judge             → task.rubric()  via OpenRouter
```

Claude's **only interface** to the world is 8 MCP browser tools:

| Tool | What it does |
|---|---|
| `mcp__browser__navigate` | Go to a URL |
| `mcp__browser__click` | Click an element by CSS/text selector |
| `mcp__browser__type_text` | Type into a field |
| `mcp__browser__select_option` | Choose a `<select>` option |
| `mcp__browser__scroll` | Scroll the page |
| `mcp__browser__screenshot` | Capture a PNG |
| `mcp__browser__get_dom` | Get the current page DOM |
| `mcp__browser__get_url` | Get the current URL |

All other Claude tools (Bash, file I/O, web search) are blocked by a
`PreToolUse` hook that denies anything not prefixed `mcp__browser__`.

---

## The Three-Layer Validation Model

V1 had one validation layer: call `GET /api/db-state` and check whether the
right rows exist. That is necessary but not sufficient.

Consider `cancel_recent_order`: an agent could construct
`GET /orders/42/cancel` directly, bypassing the UI entirely. The end-state
check passes (the order is cancelled), but the agent has not demonstrated a
skill that transfers to the real web.

V2 keeps that layer unchanged and adds two more:

### Layer 1: End-state verifier (`task.verify()`)

Calls `GET /api/db-state` and checks whether the database reflects the
expected outcome. Returns `{"passed": bool, ...task-specific fields...}`.

**Unchanged from V1.** The shop has always exposed `/api/db-state`; verifiers
have always called it rather than opening SQLite directly.

### Layer 2: Deterministic trajectory validator (`task.check_trajectory()`)

Rule-based checks on the recorded tool calls. Instant, free, fully
reproducible — no API calls. Examples:

- Did the agent call `navigate` at least once?
- Did it call `click` (not just navigate to a direct URL)?
- Did it avoid calling `screenshot` excessively (a sign of confusion)?

Returns `{"passed": bool, "violations": [...], "reasoning": "..."}`.

Implemented in [`agent_eval/validators/deterministic.py`](agent_eval/validators/deterministic.py).

### Layer 3: LLM judge (`task.rubric()`)

The task provides a natural-language rubric. The LLM judge (Claude Haiku via
OpenRouter) reads the full trajectory and evaluates it against the rubric.
Catches subtler behavioral issues that rules cannot express:

- Did the agent navigate to the orders list before selecting one?
- Did it use UI interactions rather than direct URL construction?
- Did it avoid unnecessary detours?

Returns `{"passed": bool, "violations": [...], "reasoning": "..."}`.

Implemented in [`agent_eval/validators/llm_judge.py`](agent_eval/validators/llm_judge.py).

**Overall pass** requires all three layers to pass:
```python
result["passed"] = end_state["passed"] and det_result["passed"] and llm_result["passed"]
```

---

## The Task Interface

V1 had a formal interface at the *environment* level — `gym.Env` with
`reset(seed)` and `step(action)` — but no formal interface at the *task*
level. Each task was expressed as a combination of: a seed config baked into
`reset()`, a reward function inside `step()`, and an ad-hoc verifier function
called at episode end. There was no shared base class or contract that a new
task author would implement.

V2 separates the two concerns and defines [`AbstractTask`](tasks/base.py) with
five methods that a task author must implement:

```python
class AbstractTask(ABC):

    def seed_requirements(self) -> dict:
        """What the DB must contain before the agent runs."""

    def setup(self, base_url: str) -> str:
        """Take a pre-episode snapshot; return the natural-language goal string."""

    def check_trajectory(self, trajectory: list[dict]) -> dict:
        """Deterministic rule-based trajectory check."""

    def rubric(self) -> str:
        """Natural-language rubric for the LLM judge."""

    def verify(self, base_url: str) -> dict:
        """End-state check via GET /api/db-state."""
```

The three concrete tasks are in [`tasks/`](tasks/):
[`buy_cheapest.py`](tasks/buy_cheapest.py),
[`apply_coupon.py`](tasks/apply_coupon.py),
[`cancel_order.py`](tasks/cancel_order.py).

See [`tasks/README.md`](tasks/README.md) for the task authoring guide.

---

## Isolation

V1 had no isolation. The agent ran in the same process as the evaluator and
could, in principle, read any file.

V2 runs Claude in a clean temporary directory with no project files:

```
/tmp/shopgym_{session_id}/
  .mcp.json    ← opaque MCP config (no project paths)
  server.py    ← copy of mcp_proxy.py, module docstring stripped
```

The real MCP server (`agent_eval/mcp_server.py`) is hidden behind a Unix
socket relay. Claude's `.mcp.json` references only files in `/tmp/` — it
cannot discover the project directory, read source code, or find task
definitions.

This matters because Claude, when it encounters errors, falls back to reading
files. Without isolation, it would read `README.md`, `TASK.md`, task verifier
logic, etc. — measuring "can Claude read source code" rather than "can Claude
operate a browser."

Full details: [`CLAUDE_ISOLATION.md`](CLAUDE_ISOLATION.md).

---

## Session Artifacts

V1 produced no artifacts beyond the final reward scalar.

V2 writes a self-contained audit trail for every session to
`_tmp/runs/{session_id}/`. Each session directory is a complete, standalone
record of what happened — useful for human review, research datasets, and
programmatic analysis:

| File | Contents |
|---|---|
| `trajectory.jsonl` | One JSON line per MCP tool call (tool, args, result, timing) |
| `trajectory.txt` | Human-readable trajectory summary |
| `trace.zip` | Playwright trace — open with `playwright show-trace` |
| `video.webm` | Full session recording |
| `screenshots/` | PNG per `screenshot` tool call |
| `shop_seed.db` | SQLite snapshot immediately after seeding (before agent) |
| `shop.db` | SQLite snapshot after agent finished |
| `db_state_seed.json` | Full DB state as JSON before agent ran |
| `db_state_final.json` | Full DB state as JSON after agent ran |
| `shop.jsonl` | Shop event log (add-to-cart, checkout, cancel, etc.) |
| `flask.log` | Flask web server stdout+stderr |
| `result.json` | Final validation result (all three layers) |

A few things this enables that V1 could not:

- **Replay**: `playwright show-trace _tmp/runs/{id}/trace.zip` opens an
  interactive step-by-step browser replay with DOM snapshots, network
  requests, and timing — no extra infrastructure needed.
- **Programmatic analysis**: `trajectory.jsonl` and `result.json` are
  machine-readable. You can load a batch of runs, filter by task or seed,
  compute pass rates, or diff the `db_state_seed.json` / `db_state_final.json`
  pairs to see exactly what the agent changed.
- **Research datasets**: every session is a labeled (goal, trajectory, verdict)
  triple. Accumulate enough runs and you have a dataset for training or
  evaluating trajectory judges, or for studying failure modes.
- **Post-hoc debugging**: the `shop_seed.db` snapshot lets you replay the
  exact starting state; `shop.jsonl` shows every shop event in order;
  `flask.log` shows every HTTP request the agent made.

---

## What Was Explicitly Dropped

| V1 feature | Why dropped in V2 |
|---|---|
| `gym.Env` / `env.step()` interface | Incompatible with external-agent architecture |
| Scalar reward | Replaced by three-layer structured verdict |
| Parallel rollout demo (4+ instances) | Claude Code = parallel API calls; cost/rate-limit impractical for demo |
| Agent as Python function | Claude Code is an external process |
| Trajectory recording | Not present in V1; full JSONL + Playwright trace + video added in V2 |

The `reset(seed=N)` concept is preserved: `POST /api/reset` with a seed
produces the same catalog, orders, and coupons every time.

---

## What Stayed the Same

- **The shop** (`shop/`) — Flask + SQLite, Docker, same seed data, same three tasks.
- **Deterministic seeding** — same seed → same starting state.
- **No LLM for end-state verification** — `verify()` reads the DB via HTTP API, not HTML.
- **Playwright as the browser driver** — now owned by the MCP server rather than the gym env.
- **The three tasks** — `buy_cheapest_in_category`, `apply_coupon_with_quantity`, `cancel_recent_order`.

---

## For Agentic Readers

If you are an agent reading this file to understand the system:

- You interact with the shop exclusively through 8 MCP browser tools.
- Your working directory is a clean temp folder — there are no project files to read.
- The shop runs at `http://localhost:5299` (or the URL given in your goal).
- Your goal string contains everything you need: the URL, the task, and the tool list.
- All non-browser tool calls will be denied immediately by the PreToolUse hook.
- Your session is recorded in full: every tool call, every result, every screenshot.
- After you finish, three validators check your work: end-state, deterministic rules, and an LLM judge.
