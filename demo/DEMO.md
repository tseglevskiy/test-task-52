# Running the Demo

The demo launches gym instances, runs scripted oracles (and a random policy) against them, and prints success rates.

Two runners with different backends:
- **`run_one.py`** — Flask as a subprocess. Fast startup (~100 ms), ideal for debugging a single episode.
- **`parallel_demo.py`** — one Docker container per instance. True production-like isolation; requires `shopgym:latest` to be built first.

---

## Files

| File | Purpose |
|---|---|
| `oracles.py` | Scripted oracle policies for all three tasks (shared by both runners) |
| `run_one.py` | Run **one** task+policy episode with full step-by-step logging (Flask subprocess) |
| `parallel_demo.py` | Run all 4 configurations in parallel and print a summary table (Docker) |

---

## Run a single task (recommended for debugging)

No Docker needed — Flask starts as a subprocess automatically.

```bash
# From the project root
gym_env/.venv/bin/python demo/run_one.py <task> <policy> <seed>
```

Arguments (all optional, positional):

| Argument | Values | Default |
|---|---|---|
| `task` | `cancel_order` \| `apply_coupon` \| `buy_cheapest` | `cancel_order` |
| `policy` | `oracle` \| `random` | `oracle` |
| `seed` | any integer | `0` |

**Examples:**

```bash
# Cancel the most recent order using the oracle
gym_env/.venv/bin/python demo/run_one.py cancel_order oracle 0

# Buy the cheapest Electronics item using the oracle, seed 3
gym_env/.venv/bin/python demo/run_one.py buy_cheapest oracle 3

# Apply coupon with a random policy (will almost certainly fail)
gym_env/.venv/bin/python demo/run_one.py apply_coupon random 0
```

`run_one.py` prints every `env.step()` call with the resulting URL and reward, plus all Flask request logs, so you can see exactly what is happening at each step.

---

## Run the full parallel demo

Requires the Docker image to be built first (one-time):

```bash
docker build -t shopgym:latest shop/
```

Then run:

```bash
gym_env/.venv/bin/python demo/parallel_demo.py
```

Each instance gets its own Docker container (`shopgym_demo_1` … `shopgym_demo_4`) on ports 5101–5104, its own SQLite database (`_tmp/demo_<N>/shop.db`), and its own Playwright Chromium browser. Containers are started before episodes and stopped+removed in `finally` — no leftover state between runs.

Expected output (takes ~90–120 s including Docker startup):

```
=== Parallel Demo Results ===
  Instance 1 | cancel_order   | oracle | 5/5 = 100%
  Instance 2 | cancel_order   | random | 0/5 = 0%
  Instance 3 | apply_coupon   | oracle | 3/3 = 100%
  Instance 4 | buy_cheapest   | oracle | 3/3 = 100%
```

---

## What the demo proves

| Instance | Task | Policy | What it proves |
|---|---|---|---|
| 1 | cancel_order | oracle | Verifier correctly awards reward when the task is completed |
| 2 | cancel_order | random | Random actions almost never complete the task — reward is robust |
| 3 | apply_coupon | oracle | Full multi-step checkout (SKU + coupon + form) works end-to-end |
| 4 | buy_cheapest | oracle | Verifier correctly identifies the cheapest Electronics item + address |

---

## Prerequisites

```bash
# Build the Docker image (parallel demo)
docker build -t shopgym:latest shop/

# One-time Python venv setup
python -m venv gym_env/.venv
gym_env/.venv/bin/pip install gymnasium playwright numpy requests Pillow Flask
gym_env/.venv/bin/playwright install chromium
```

Check it works:

```bash
gym_env/.venv/bin/python -c "import gymnasium, playwright, numpy, PIL; print('OK')"
```

---

## Troubleshooting

**"docker run failed" / "No such image"**
Build the image first: `docker build -t shopgym:latest shop/`

**"Container health check timed out"**
Docker may be slow to start on first pull or on a cold machine. Try running `docker run --rm shopgym:latest` manually to check the image works.

**Port already in use**
`parallel_demo.py` uses ports 5101–5104; `run_one.py` uses 5199.
Check: `ss -tlnp | grep 51`
If a previous run left containers behind: `docker rm -f shopgym_demo_1 shopgym_demo_2 shopgym_demo_3 shopgym_demo_4`

**Demo hangs**
Playwright is launching headless Chromium browsers. On a slow machine this can take >30 s. Wait up to 3 minutes before aborting.

**"No module named gym_env"**
Run from the project root, not from inside `demo/`.

---

## Customising

- **`oracles.py`** — edit the oracle step sequences to experiment with different UI paths
- **`parallel_demo.py` configs list** — change tasks, episode counts, or swap oracle↔random
- **Ports** — `run_one.py` uses 5199; `parallel_demo.py` uses `5100 + instance_id` (5101–5104)
- **Docker image** — change `IMAGE = "shopgym:latest"` in `parallel_demo.py` to use a different tag
