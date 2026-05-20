# Running the Parallel Demo

The parallel demo launches 4 concurrent gym instances, runs scripted oracles (and a random policy) against them, and prints success rates. It requires no Docker — Flask runs as lightweight subprocesses.

---

## Quick start

```bash
# 1. Activate the gym virtual environment
cd /mnt/d/p/gym
source gym_env/.venv/bin/activate   # or: gym_env/.venv/bin/python below

# 2. Run the demo
gym_env/.venv/bin/python scripts/parallel_demo.py
```

Expected output (takes ~60–90 s):

```
=== Parallel Demo Results ===
  Instance 1 | cancel_order  | oracle | 5/5 = 100%
  Instance 2 | cancel_order  | random | 0/5 = 0%
  Instance 3 | apply_coupon  | oracle | 3/3 = 100%
  Instance 4 | buy_cheapest  | oracle | 3/3 = 100%
```

---

## What the demo does

| Instance | Task | Policy | What it proves |
|----------|------|--------|---------------|
| 1 | cancel_order | oracle | Verifier correctly awards reward when the task is completed |
| 2 | cancel_order | random | Random actions almost never complete the task — reward is robust |
| 3 | apply_coupon | oracle | Full multi-step checkout (SKU + coupon + form) works end-to-end |
| 4 | buy_cheapest | oracle | Verifier correctly identifies the cheapest Electronics item + address |

Each instance gets:
- Its own Flask process on a dedicated port (5101–5104)
- Its own SQLite database file in `_tmp/demo_<N>/shop.db`
- Its own Playwright Chromium browser

No state is shared between instances. Instances 1–4 run in parallel (one OS process each).

---

## Prerequisites

The demo installs its own Flask subprocess from the `gym_env/.venv`. Make sure the venv was set up:

```bash
# One-time setup (if not already done)
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

**"Flask failed to start"**
The Flask subprocess timed out. Possible causes:
- Port 5101–5104 already in use. Check: `ss -tlnp | grep 510`
- `gym_env/.venv` is missing Flask. Fix: `gym_env/.venv/bin/pip install Flask`

**Demo hangs**
Playwright is launching 4 headless Chromium browsers simultaneously. On a slow machine this can take >30 s. Wait up to 2 minutes before aborting.

**"No module named gym_env"**
Run from the project root (`/mnt/d/p/gym`), not from inside a subdirectory.

---

## Changing the demo

Edit `scripts/parallel_demo.py`:

- **configs list** — change tasks, episode counts, or swap oracle↔random for any instance
- **oracle functions** — `run_cancel_oracle`, `run_apply_coupon_oracle`, `run_buy_cheapest_oracle` show the exact browser steps for each task
- **ports** — instances use `5100 + instance_id` (5101–5104); change if there are conflicts
