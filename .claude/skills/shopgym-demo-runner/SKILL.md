---
name: shopgym-demo-runner
description: Run ShopGym oracle demos and debug individual task episodes. Use this skill whenever the user wants to run the demo, test an oracle, check if a task works, debug a failing episode, run a specific task with a specific seed, or see step-by-step browser logs for any of the three tasks (cancel_order, apply_coupon, buy_cheapest). Trigger even if the user just says "run the demo", "test the oracle", "does cancel_order work?", or "show me what happens when the agent runs buy_cheapest".
---

# ShopGym Demo Runner

This skill runs oracle policy demos against the ShopGym gym environment. All demo code is in `demo/`.

## Project layout (relevant parts)

```
demo/
  oracles.py        — the three oracle policy functions (shared)
  run_one.py        — single-task runner with full step logging
  parallel_demo.py  — 4-instance parallel runner, prints summary table
  DEMO.md           — full documentation
gym_env/            — Gymnasium environment (ShopEnv)
tasks/              — task definitions + verifiers
shop/               — Flask e-commerce app
```

## Running a single task episode

Use `run_one.py` when the user wants to debug, verify, or observe a specific task. It prints every `env.step()` call with the resulting URL and reward, plus all Flask request logs.

```bash
# From the project root
gym_env/.venv/bin/python demo/run_one.py <task> <policy> <seed>
```

| Argument | Options | Default |
|---|---|---|
| `task` | `cancel_order` \| `apply_coupon` \| `buy_cheapest` | `cancel_order` |
| `policy` | `oracle` \| `random` | `oracle` |
| `seed` | any integer | `0` |

**Examples:**
```bash
gym_env/.venv/bin/python demo/run_one.py cancel_order oracle 0
gym_env/.venv/bin/python demo/run_one.py apply_coupon oracle 0
gym_env/.venv/bin/python demo/run_one.py buy_cheapest oracle 3
gym_env/.venv/bin/python demo/run_one.py cancel_order random 0
```

A successful run ends with:
```
[run_one] === RESULT ===
  reward=1.0  terminated=True
  SUCCESS
```

## Running the full parallel demo

Use `parallel_demo.py` when the user wants to see all four configurations run concurrently and get a summary table. It uses Docker — one container per instance — so the image must be built first:

```bash
docker build -t shopgym:latest shop/
gym_env/.venv/bin/python demo/parallel_demo.py
```

Expected output (takes ~90–120 s including Docker startup):
```
=== Parallel Demo Results ===
  Instance 1 | cancel_order   | oracle | 5/5 = 100%
  Instance 2 | cancel_order   | random | 0/5 = 0%
  Instance 3 | apply_coupon   | oracle | 3/3 = 100%
  Instance 4 | buy_cheapest   | oracle | 3/3 = 100%
```

## Checking prerequisites

Before running, verify the venv has all required packages:

```bash
gym_env/.venv/bin/python -c "import gymnasium, playwright, numpy, PIL, flask, requests; print('OK')"
```

If anything is missing:
```bash
gym_env/.venv/bin/pip install gymnasium playwright numpy requests Pillow Flask
gym_env/.venv/bin/playwright install chromium
```

## Diagnosing failures

If a run fails or hangs, check these in order:

1. **Port conflict** — `run_one.py` uses port 5199; `parallel_demo.py` uses 5101–5104.
   Check: `ss -tlnp | grep 51`
   Leftover containers: `docker rm -f shopgym_demo_1 shopgym_demo_2 shopgym_demo_3 shopgym_demo_4`

2. **"docker run failed" / image not found** — build the image first: `docker build -t shopgym:latest shop/`

3. **Flask didn't start** (`run_one.py` only) — look for `"Flask failed to start"` or repeated connection-refused messages in the output. The Flask subprocess logs flow directly to stdout in `run_one.py`.

4. **Oracle returned FAILURE** — the step log will show which action failed (reward stayed 0.0 all the way through). Common causes:
   - `click_by_role` couldn't find the element — check the axtree snippet printed by `run_one.py`
   - SKU-E7421 not found in axtree for `apply_coupon` — the regex failed; the axtree snippet is printed automatically

5. **Slow startup** — Playwright launches a headless Chromium browser. On a slow machine allow up to 2 minutes.

## How the oracles work

All three oracles live in `demo/oracles.py` and use only `env.step()` actions — no direct `/api/db-state` calls.

| Task | Oracle strategy |
|---|---|
| `cancel_order` | Click "My Orders" → click first "View" link (orders sorted newest-first) → click "Cancel Order" |
| `apply_coupon` | Click "Electronics" nav link → regex-parse `obs["axtree"]` to find product name for SKU-E7421 → click it → set qty=2 → apply SAVE10 coupon → checkout |
| `buy_cheapest` | Click "Electronics" → click "Price: Low→High" sort → click first "View" (cheapest) → checkout to 123 Main St, Springfield, IL 62701 |

The `click_by_role` action uses `.first` to handle pages with multiple elements sharing the same accessible name (e.g. multiple "View" links in the orders table).
