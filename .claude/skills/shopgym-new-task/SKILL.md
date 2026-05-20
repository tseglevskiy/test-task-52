---
name: shopgym-new-task
description: Add a new task to the ShopGym gym environment. Use this skill whenever the user wants to create a new task, add a new goal for the agent, write a new verifier, or add a scripted oracle for a new task. Trigger even if the user just says "add a task", "make a new task", "I want the agent to do X", or "write an oracle for my new task".
---

# ShopGym — Adding a New Task

A task = one Python file in `tasks/` that answers three questions:
1. What must the DB contain before the episode? (`seed_requirements`)
2. What is the agent trying to do? (`setup` → goal string)
3. Did the agent succeed? (`verify` → reads DB via HTTP)

**Full reference:** `gym_env/TASK.md` — read it first. It has the complete `AbstractTask` interface, the `/api/db-state` response shape, the "detect new orders" pattern, and common pitfalls.

---

## Checklist

### 1. Design the task

Answer these before writing any code:

- **Goal string** — one sentence the agent will read, e.g. *"Buy the cheapest item in Electronics and ship to 123 Main St…"*
- **What must be seeded?** — products, coupons, orders? (see `seed_requirements` in `gym_env/TASK.md`)
- **What does the verifier check?** — be specific: which DB fields, which comparisons, float tolerance?
- **What UI steps does the agent need to take?** — sketch the happy path; you'll need this for the oracle

### 2. Write `tasks/your_task.py`

Follow the skeleton in `gym_env/TASK.md` → *"How to add a new task"* section.

Key rules (from `gym_env/TASK.md` → *"Common pitfalls"*):
- Always use `GET {base_url}/api/db-state` — never open the SQLite file directly
- Snapshot pre-episode state in `setup()`, not `verify()`
- Use `abs(a - b) < 0.01` for float price comparisons
- Don't share state between episodes — set all `self._*` vars inside `setup()`

### 3. Export from `tasks/__init__.py`

```python
from .your_task import YourTask
```

### 4. Write a verifier unit test

Add a test to `gym_env/tests/test_verifiers.py`. Look at the existing tests there for the pattern — each test:
1. Instantiates the task and sets its episode state directly (no env needed)
2. Patches `requests.get` to return a fake `db-state` dict
3. Asserts `(1.0, True)` for the success case
4. Asserts `(0.0, False)` for the incomplete/wrong case

Run: `gym_env/.venv/bin/python -m pytest gym_env/tests/test_verifiers.py -v`

### 5. Write a scripted oracle in `demo/oracles.py`

The oracle proves the task is solvable and the verifier rewards correctly. It uses only `env.step()` actions and `obs["axtree"]` parsing — **no `/api/db-state` calls inside the oracle body**.

Add a function `run_your_task_oracle(env, obs)` to `demo/oracles.py`:

```python
def run_your_task_oracle(env, obs):
    """
    Scripted oracle for your_task.

    Steps:
      1. ...
      2. ...
    """
    from demo.oracles import _step  # reuse the helper

    # Navigate using only clicks and axtree parsing.
    # Available action types: click_by_role, click, type, press,
    #                         navigate, scroll, select_option
    # See gym_env/actions.py for the full list.

    _step(env, {"type": "click_by_role", "role": "link", "name": "..."})
    # ...
    obs, reward, terminated, truncated, info = _step(
        env, {"type": "click_by_role", "role": "button", "name": "Place Order"}
    )
    return reward, terminated
```

**Oracle writing tips:**

- Start from the homepage (`base_url/`) — that's where `env.reset()` leaves the browser
- Use `click_by_role` with `role="link"` for nav links and table links; `role="button"` for form buttons; `role="textbox"` for inputs; `role="spinbutton"` for `<input type="number">`
- `click_by_role` picks the **first** matching element (`.first` is applied automatically) — useful for tables with multiple "View" links
- To find a product by SKU when the name is random, click the category nav link first, then regex-parse `obs["axtree"]`:
  ```python
  obs, *_ = _step(env, {"type": "click_by_role", "role": "link", "name": "Electronics"})
  import re
  match = re.search(r'- row "[^"]*YOUR-SKU[^"]*".*?- link "([^"]+)"', obs["axtree"], re.DOTALL)
  product_name = match.group(1)
  ```
- To inspect what the axtree looks like on any page, use `demo/run_one.py` with a custom seed and read the printed output, or add a temporary `print(obs["axtree"])` in the oracle

**Inspect the axtree interactively:**
```bash
gym_env/.venv/bin/python demo/run_one.py your_task oracle 0
```
(Add `print(obs["axtree"])` temporarily to see what Playwright sees on any page.)

### 6. Wire the oracle into `demo/run_one.py` and `demo/parallel_demo.py`

Both runners use dict-based dispatch — no per-task branching needed. Just add your task and oracle to the two maps in each file:

**`demo/run_one.py`** — inside `main()`, add to both dicts:
```python
task_map   = { ..., "your_task": YourTask }
oracle_map = { ..., "your_task": oracles.run_your_task_oracle }
```

**`demo/parallel_demo.py`** — inside `run_env_worker()`, add to both dicts:
```python
task_map   = { ..., "your_task": YourTask }
oracle_map = { ..., "your_task": oracles.run_your_task_oracle }
```

Then optionally add a config entry to `main()` in `parallel_demo.py`:
```python
(5, "your_task", 3, "oracle"),
```

Step logging in `run_one.py` is automatic — it monkey-patches `oracles._step` with a traced version before calling any oracle, so no extra wrapper code is needed.

### 7. Verify end-to-end

```bash
gym_env/.venv/bin/python demo/run_one.py your_task oracle 0
```

Expected final lines:
```
  reward=1.0  terminated=True
  SUCCESS
```

---

## Available shop UI (what the oracle can click)

The shop has these pages and elements — see `shop/templates/` for the full HTML:

| Page | Key elements |
|---|---|
| `/` (product listing) | Category nav links, "Price: Low→High" / "Price: High→Low" sort links, product name links, "View" links per row |
| `/product/<id>` | Quantity spinbutton, "Add to Cart" button |
| `/cart` | "Coupon code:" textbox, "Apply Coupon" button, "Proceed to Checkout" button |
| `/checkout` | "Full Name *", "Street Address *", "City *", "ZIP Code *" textboxes; `select[name='state']` dropdown; "Place Order" button |
| `/orders` | "My Orders" nav link, "View" links per row (sorted newest-first) |
| `/order/<id>` | "Cancel Order" button (only if status=placed) |

Available action types (`gym_env/actions.py`): `click_by_role`, `click`, `type`, `press`, `navigate`, `scroll`, `select_option`.
