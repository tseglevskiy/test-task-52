# gym_env — ShopGym Gymnasium Environment

This package implements the Gymnasium-compatible environment that wraps the ShopGym Flask shop. It handles browser lifecycle, observation building, action dispatch, and task coordination.

---

## File layout

```
gym_env/
├── __init__.py          # exports ShopEnv, AbstractTask
├── env.py               # ShopEnv(gymnasium.Env) — reset / step / close
├── actions.py           # JSON action string → Playwright call (7 action types)
├── observation.py       # page snapshot → {url, axtree, screenshot, goal} dict
├── requirements.txt     # gymnasium, playwright, numpy, requests, Pillow
├── tasks/
│   ├── __init__.py      # exports AbstractTask
│   └── base.py          # AbstractTask ABC — the interface for all tasks
└── tests/
    ├── test_verifiers.py  # unit tests for task.verify() — no Docker needed
    └── test_env.py        # integration smoke test — needs shop Docker on port 5001

tasks/                   # concrete task implementations (consumers of gym_env)
├── __init__.py
├── buy_cheapest.py      # BuyCheapestInCategoryTask
├── apply_coupon.py      # ApplyCouponWithQuantityTask
└── cancel_order.py      # CancelRecentOrderTask
```

`tasks/` is a sibling of `gym_env/`, not inside it. Task classes are consumers of `gym_env`, not part of the environment infrastructure. New tasks go in `tasks/`. See `gym_env/TASK.md` for how to write one.

---

## Architecture

```
Host machine
│
├── gym_env/  (runs on host, one process per env instance)
│     ShopEnv
│       ├── Playwright Chromium → http://localhost:500N  (browser control)
│       ├── task.verify()       → GET /api/db-state      (state inspection)
│       └── env.reset()         → POST /api/reset        (DB wipe + reseed)
│
└── Docker container "shop_N"  (port 500N)
      Flask app (shop/app.py)
      /app/shop.db → volume mount: _tmp/gym_N/shop.db
```

**One container = one Flask process = one SQLite file.**
Each parallel env instance gets a unique port and a unique DB path. No shared mutable state between instances.

### Reset flow

```
env.reset(seed=42)
  1. _close_browser()           # close previous Playwright browser (idempotent)
  2. POST /api/reset            # wipe DB, reseed deterministically (~100ms)
     {"seed": 42,
      "required_orders": [{"status": "placed"}]}   ← from task.seed_requirements()
  3. playwright.chromium.launch(headless=True)      (~400ms)
  4. page.goto(base_url)
  5. task.setup(page, base_url) # snapshot pre-state, get goal string (~50ms)
  6. build_observation(page)    # url + axtree + screenshot + goal (~100ms)

Total: ~650ms. Well under 3s target.
```

### Browser lifetime

A fresh Playwright `Browser` is launched on every `reset()` call and closed before the next `reset()`. This adds ~400ms per reset but guarantees absolute episode isolation — no reasoning needed about what cookies, localStorage, or Playwright `BrowserContext` isolation covers. For this scale (tens of episodes per instance) the cost is irrelevant.

---

## Observation space

```python
observation_space = spaces.Dict({
    "url":        spaces.Text(max_length=2048,    charset=string.printable),
    "axtree":     spaces.Text(max_length=500_000, charset=string.printable),
    "screenshot": spaces.Box(0, 255, shape=(H, W, 3), dtype=np.uint8),
    "goal":       spaces.Text(max_length=512,     charset=string.printable),
})
```

| Key | Type | Content |
|-----|------|---------|
| `url` | str | Current page URL (`page.url`) |
| `axtree` | str | ARIA snapshot string (`page.aria_snapshot()`) — semantic accessibility tree |
| `screenshot` | ndarray | `(H, W, 3)` uint8 RGB array (`page.screenshot()` decoded via PIL) |
| `goal` | str | Task goal string — constant within an episode, repeated every step |

**`charset=string.printable` is mandatory on every `Text` space.** The default Gymnasium charset is alphanumeric only. Real URLs contain `://`, ARIA snapshots contain `"`, `[`, `/` — without the explicit charset, `obs in observation_space` silently returns `False` and training code breaks in subtle ways.

**`goal` in every obs.** The goal is included in the observation dict (not only in the `reset()` info dict) so the agent always knows its task without needing cross-step memory. This follows the convention used by goal-conditioned environments (BrowserGym, MiniGrid).

**ARIA snapshot vs raw HTML.** `page.aria_snapshot()` returns only meaningful semantic nodes — buttons, links, inputs, headings, with their ARIA roles and accessible names. Raw HTML (`page.content()`) is ~5× larger and full of CSS/layout noise. The axtree is what an LLM or structured agent actually needs to reason about what's on the page.

**String sanitization.** `observation.py` strips non-ASCII characters from `url`, `axtree`, and `goal` before returning them. This is needed because product names and placeholder text (e.g. "Search products…") may contain Unicode like the ellipsis `…` (U+2026) that isn't in `string.printable`, which would break `obs in observation_space`.

---

## Action space

```python
action_space = spaces.Text(max_length=2048, charset=string.printable)
```

Actions are JSON-encoded strings. Seven types are supported:

```python
{"type": "click",         "x": float, "y": float}
{"type": "type",          "text": str}
{"type": "scroll",        "x": float, "y": float, "delta_y": float, "delta_x": float}
{"type": "navigate",      "url": str}
{"type": "click_by_role", "role": str, "name": str}
{"type": "press",         "key": str}
{"type": "select_option", "selector": str, "value": str}
```

| Type | Playwright call | Notes |
|------|----------------|-------|
| `click` | `page.mouse.click(x, y)` | Coordinate-based, no actionability wait |
| `type` | `page.keyboard.type(text)` | Types literal text character by character |
| `scroll` | `page.mouse.move(x,y)` + `page.mouse.wheel(dx, dy)` | Moves mouse first so the right element scrolls |
| `navigate` | `page.goto(url)` + `wait_for_load_state("networkidle")` | Blocks until page fully loaded |
| `click_by_role` | `page.get_by_role(role, name=name).click()` + `wait_for_load_state` | Clicks by ARIA role + accessible name — works directly from axtree |
| `press` | `page.keyboard.press(key)` | Keyboard shortcuts: `"Control+a"`, `"Enter"`, `"Tab"` |
| `select_option` | `page.locator(selector).select_option(value)` + `wait_for_load_state` | `<select>` dropdown elements |

**`click` vs `click_by_role`:** Coordinate-based `click` requires the agent to reason about pixel positions from the screenshot. `click_by_role` uses ARIA semantics from the axtree — no coordinates needed. Scripted oracles use `click_by_role`; a vision model would use `click`.

**Action errors are non-fatal.** If `execute_action()` raises (bad JSON, unknown type, element not found, timeout), `env.step()` catches the exception and continues — the agent receives the unchanged observation and zero reward, not a crash.

---

## `ShopEnv` API

```python
from gym_env.env import ShopEnv
from tasks.cancel_order import CancelRecentOrderTask

env = ShopEnv(
    base_url="http://localhost:5001",
    task_class=CancelRecentOrderTask,
    screenshot_shape=(540, 960),   # (H, W), optional, default (540, 960)
    render_mode=None,              # unused, kept for Gymnasium compatibility
)

obs, info = env.reset(seed=42)
# obs:  dict with keys "url", "axtree", "screenshot", "goal"
# info: {"goal": str}

obs, reward, terminated, truncated, info = env.step(action_json_str)
# reward:     1.0 on success, 0.0 otherwise
# terminated: True if task is done (success or definitive failure)
# truncated:  always False — use gymnasium.wrappers.TimeLimit if needed

env.close()   # releases Playwright browser
```

`reset()` requires the shop container at `base_url` to already be running. It calls `POST /api/reset` to wipe and reseed the DB, then launches a fresh browser.

`step()` is non-fatal with respect to bad actions — if the action fails, the page is unchanged and `verify()` returns the same result as before.

---

## Decisions

### D1 — Verifiers use `/api/db-state`, not direct SQLite

Task verifiers call `GET {base_url}/api/db-state` rather than opening the SQLite file directly. This keeps verifiers coupled only to the shop's JSON contract, not its schema. A verifier that reads the DB file directly would break if the shop changes a column name or table structure. It also means task classes only need `base_url` — they work against any deployment.

### D2 — Fresh browser per episode, not per session

`env.reset()` closes the old `Browser` and launches a new one. The alternative (reuse browser, create new `BrowserContext` per episode) would save ~400ms per reset but requires understanding Playwright's isolation guarantees at the browser level and handling browser crashes between episodes. For this exercise, correctness over speed.

### D3 — Task state is instance-local, not shared

`_target_order_id`, `_pre_order_ids`, `_min_price` etc. are instance variables on the task object. Each `ShopEnv` instance has its own task object. In parallel mode (one process per env), there is no shared state — this is why process-level parallelism works cleanly without locks.

### D4 — `seed_requirements()` keeps task config co-located

Each task declares what it needs in the DB (a placed order, a specific SKU, a coupon) via `seed_requirements()`. This is merged into the `POST /api/reset` body by `env.reset()`. The alternative — hard-coding requirements in the env or passing them at construction time — would scatter the task definition across multiple files. A new task developer adds one file, not three.

### D5 — `multiprocessing.get_context("spawn")` for parallelism

The parallel demo uses `spawn` instead of the Linux default `fork`. Forking a process that has already imported Playwright (or that will) can deadlock inside Playwright's browser process manager because file descriptors and locks are duplicated. `spawn` starts a fresh Python interpreter, so each worker imports and initialises Playwright cleanly.

---

## Running tests

```bash
# Verifier unit tests — no Docker needed, runs in milliseconds
gym_env/.venv/bin/python -m pytest gym_env/tests/test_verifiers.py -v

# Integration smoke test — requires shop Docker on port 5001
mkdir -p _tmp/gym_1 && touch _tmp/gym_1/shop.db _tmp/gym_1/shop.jsonl
docker build -t shopgym:latest shop/
docker run -d --name shopgym_test \
    -v $(pwd)/_tmp/gym_1/shop.db:/app/shop.db \
    -v $(pwd)/_tmp/gym_1/shop.jsonl:/app/shop.jsonl \
    -p 5001:5000 shopgym:latest
gym_env/.venv/bin/python -m pytest gym_env/tests/test_env.py -v -s
docker stop shopgym_test && docker rm shopgym_test
```

---

## Docker compose (4 instances)

Pre-flight:
```bash
for i in 1 2 3 4; do mkdir -p _tmp/gym_$i && touch _tmp/gym_$i/shop.db _tmp/gym_$i/shop.jsonl; done
docker build -t shopgym:latest shop/
```

Start all 4 shop instances:
```bash
docker compose up -d
```

This brings up containers on ports 5001–5004, each with its own DB file. Stop with `docker compose down`.
