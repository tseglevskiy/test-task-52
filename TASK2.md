# TASK2: Web Gym with Real Agent Evaluation

## What I'm building

Same e-commerce substrate as TASK (Docker, SQLite, seeded products/orders/coupons), but instead of a Gymnasium loop driven by a scripted policy, the gym is designed to evaluate a **real external agent** — specifically Claude Code — running autonomously.

The key shift: Gymnasium assumes the agent is a function inside your process (`env.step(action)`). Claude Code is an external process with its own loop. So instead of a Gymnasium interface, the gym exposes a **task runner** that:

1. Gives the agent a natural-language task and a browser
2. Gives the agent an MCP server as its only interface to the browser — the agent can only click, type, and navigate through MCP tools; the MCP server logs every call as it happens
3. Records the full trajectory (every tool call + observation)
4. After completion: validates both **end state** (did the order get cancelled?) and **trajectory** (did it get there the right way?)

---

## Architecture in one diagram

```
Task Runner
  ├── spins up: Docker site + fresh DB (from seed)
  ├── starts: Claude Code  ←→  MCP server (browser tools)
  │                                 └── logs every action to trajectory store
  ├── live view: stream of steps to terminal / browser UI
  └── after agent stops:
        ├── End-state verifier  → reads SQLite directly
        └── Trajectory validator → LLM judge on recorded steps
```

The MCP server is the central piece: it gives the agent click/type/navigate/scroll, and simultaneously records everything. Claude Code can only use MCP tools — all built-in tools (Bash, file I/O) are blocked via PreToolUse hook.

---

## The three tasks (same as TASK)

- **`buy_cheapest_in_category`** — filter Electronics, buy cheapest, ship to given address
- **`apply_coupon_with_quantity`** — correct SKU, correct quantity, apply coupon, checkout
- **`cancel_recent_order`** — find and cancel the most recent pre-seeded order

Each task has:
- A **seed config** (same seed → same catalog, orders, coupons)
- An **end-state verifier** that reads SQLite (no HTML scraping, no LLM)
- A **trajectory validator** (see below)

---

## Trajectory validation

End-state verification alone can miss bad behavior: an agent could cancel an order by constructing `/orders/42/cancel` directly, bypassing the UI entirely — technically correct, but not a skill that transfers to the real web.

Trajectory validation catches this. After each run, the recorded steps are passed to an LLM judge with a task-specific rubric:

- Did the agent navigate to the orders list before selecting one?
- Did it use UI interactions (clicks) rather than direct URL construction?
- Did it avoid unnecessary detours?

The judge returns a structured verdict: `{passed: bool, violations: [...], reasoning: "..."}`.

**Reference:** this pattern is documented in [AgentRewardBench](https://arxiv.org/abs/2504.08942) (McGill, 2025) and [GUIDE](https://arxiv.org/abs/2604.04399) (2026).

---

## Observability — all from Playwright, zero extra code

The MCP server runs Playwright with three options set at context creation:

```python
context = await browser.new_context(
    record_video_dir="runs/{session_id}/",
)
await context.tracing.start(screenshots=True, snapshots=True)
```

This gives:

| What | How | Format |
|---|---|---|
| Full trajectory record | Playwright Trace | `trace.zip` — interactive viewer with DOM snapshots, screenshots, network, timing |
| Video of the session | `recordVideo` | `.webm` — standard video file |
| Live browser observation | Headed mode (non-headless) | Just watch the browser window during the run |

After a run: `playwright show-trace runs/session_id/trace.zip` opens an interactive step-by-step replay in the browser — no separate infrastructure needed.

Headed vs headless is a single flag — no application changes required.

---

## What I'm explicitly not solving

| Problem | Why I'm skipping it |
|---|---|
| LLM judge calibration | Calibrating a judge requires ground-truth labeled trajectories (see AgentRewardBench). This is a demo — I'll use Claude Haiku as judge and treat its output as directionally correct, not ground truth. |
| Gymnasium compatibility | Incompatible with external-agent architecture. Dropping it. The `reset(seed)` concept stays, the interface doesn't. |
| Parallel rollouts at scale | TASK asked for 4 concurrent instances. With Claude Code as the agent, parallel runs mean parallel API calls — cost and rate limits make this impractical for a demo. I'll show the isolation mechanism works, but won't run many parallel episodes. |
| RL training loop | Out of scope per original TASK as well. |
| Auth / payments / CSS | Same as original TASK. |
| Cross-browser | Chromium only. |

