# tasks/ — ShopGym Task Definitions

Each file in this directory defines one evaluation task for the ShopGym agent harness.
A task specifies what the agent must do, how to seed the database for it, and how to
verify that it succeeded — both by inspecting the final DB state and by checking the
recorded trajectory of browser tool calls.

---

## File layout

```
tasks/
  base.py              ← AbstractTask interface (read this first)
  cancel_order.py      ← example: cancel the most recent order
  apply_coupon.py      ← example: add item, apply coupon, checkout
  buy_cheapest.py      ← example: find cheapest Electronics item, checkout
  tests/
    test_verifiers.py  ← pytest tests for the verify() methods
  README.md            ← this file
```

---

## The AbstractTask interface

**Source:** [`tasks/base.py`](base.py)

Every task is a Python class that inherits from `AbstractTask` and implements five methods:

```python
class MyTask(AbstractTask):
    def seed_requirements(self) -> dict: ...
    def setup(self, base_url: str) -> str: ...
    def check_trajectory(self, trajectory: list[dict]) -> dict: ...
    def rubric(self) -> str: ...
    def verify(self, base_url: str) -> dict: ...
```

### `seed_requirements() -> dict`

Returns extra constraints merged into the `POST /api/reset` body alongside the seed.
Use this to guarantee specific products, coupons, or orders exist after seeding.

```python
def seed_requirements(self) -> dict:
    return {
        "required_products": [{"category": "Electronics", "sku": "SKU-E7421"}],
        "required_coupons":  [{"code": "SAVE10", "discount_pct": 10.0}],
        "required_orders":   [{"status": "placed"}],
    }
```

All three keys are optional. An empty `{}` means no extra requirements.

**Seeding reference:** [`shop/seed.py`](../shop/seed.py) — `SeedConfig`, `RequiredProduct`,
`RequiredCoupon`, `RequiredOrder` dataclasses (lines 39–101).

Key seeding facts:
- Default catalog: 5 categories × 8 products = 40 products
- Default orders: 3 pre-seeded filler orders
- `required_orders[0]` gets `created_at = BASE_TS` (most recent timestamp)
- `required_orders[i]` gets `created_at = BASE_TS - i * 86400` (one day earlier per slot)
- Required products are placed in the first available slot of the requested category
- Duplicate SKUs or coupon codes in requirements raise `ValueError`

### `setup(base_url: str) -> str`

Called once per episode **after** the DB has been seeded, **before** the agent runs.

Use it to:
1. Snapshot pre-episode state you'll need in `verify()` (e.g. record existing order IDs)
2. Return the natural-language goal string shown to the agent

```python
def setup(self, base_url: str) -> str:
    state = requests.get(f"{base_url}/api/db-state").json()
    self._pre_order_ids = {o["id"] for o in state["orders"]}
    return "Buy the cheapest item in the 'Electronics' category and ship it to ..."
```

The goal string is shown verbatim to the agent (with some context appended by the
task runner). Keep it concise and unambiguous.

**DB state shape** (from `GET /api/db-state`, defined in [`shop/seed.py`](../shop/seed.py) lines 361–399):
```json
{
  "products":    [{"id", "sku", "name", "category", "price"}],
  "coupons":     [{"id", "code", "discount_pct", "active"}],
  "orders":      [{"id", "created_at", "status", "shipping_address",
                   "coupon_code", "discount_pct", "subtotal", "total"}],
  "order_items": [{"id", "order_id", "product_id", "sku", "name",
                   "quantity", "unit_price"}]
}
```

### `check_trajectory(trajectory: list[dict]) -> dict`

**Deterministic** rule-based check of the agent's browser tool calls.
Called by [`agent_eval/validators/deterministic.py`](../agent_eval/validators/deterministic.py).
No external calls — instant, free, fully reproducible.

```python
def check_trajectory(self, trajectory: list[dict]) -> dict:
    violations = []
    navigate_urls = [s["args"].get("url", "") for s in trajectory if s.get("tool") == "navigate"]
    
    if not any("/orders" in url for url in navigate_urls):
        violations.append("Agent never visited the orders list page.")
    
    return {
        "passed": len(violations) == 0,
        "violations": violations,          # list[str], empty if passed
        "reasoning": "✓ check1; ✗ check2", # human-readable summary
    }
```

**Trajectory step shape** (from [`agent_eval/trajectory.py`](../agent_eval/trajectory.py)):
```json
{
  "step":      1,
  "timestamp": 1779663226.5,
  "tool":      "navigate",
  "args":      {"url": "http://localhost:5299/orders"},
  "result":    "- banner:\n  ...",
  "error":     null,
  "elapsed":   0.56
}
```

Available tools: `navigate`, `click`, `type_text`, `select_option`, `scroll`,
`screenshot`, `get_dom`, `get_url`.

Key patterns for checks:
- `navigate` → `args["url"]` contains the URL navigated to
- `click` → `args["selector"]` is the CSS/text selector clicked
- `type_text` → `args["selector"]` + `args["text"]` is what was typed
- `select_option` → `args["selector"]` + `args["value"]` is what was selected
- `result` contains the DOM text after the action (truncated to 500 chars)
- `error` is non-null if the tool call failed

### `rubric() -> str`

Returns a numbered list of behavioral criteria for the **LLM judge**
([`agent_eval/validators/llm_judge.py`](../agent_eval/validators/llm_judge.py)).
The LLM reads this rubric alongside the trajectory and returns a structured verdict.

```python
def rubric(self) -> str:
    return """\
1. The agent must navigate to the orders list page before selecting a specific order.
2. The agent must cancel using UI interaction, NOT a direct URL to the cancel endpoint.
3. The agent must not make unnecessary detours unrelated to cancellation."""
```

Write rubric items as clear, checkable criteria. The LLM will evaluate each one.

### `verify(base_url: str) -> dict`

Called **after** the agent session ends. Checks the final DB state via HTTP.
Returns `{"passed": bool, ...task-specific fields...}`.

```python
def verify(self, base_url: str) -> dict:
    state = requests.get(f"{base_url}/api/db-state").json()
    order = next((o for o in state["orders"] if o["id"] == self._target_id), None)
    passed = order is not None and order["status"] == "cancelled"
    return {"passed": passed, "order_id": self._target_id, "status": order["status"]}
```

**Rules:**
- Always use `GET {base_url}/api/db-state` — never open the SQLite file directly
- Always return at minimum `{"passed": bool}`
- Add task-specific fields to help diagnose failures (e.g. `qty_ok`, `address_ok`)
- Use state snapshots from `setup()` to detect changes (e.g. new orders since episode start)

---

## How the task runner uses your task

**Source:** [`agent_eval/task_runner.py`](../agent_eval/task_runner.py)

```
1. POST /api/reset  {seed, ...seed_requirements()}   ← seeds the DB
2. task.setup(base_url)                              ← snapshot + goal string
3. [agent runs, browser tool calls recorded]
4. task.verify(base_url)                             ← end-state check
5. DeterministicValidator → task.check_trajectory()  ← rule-based check
6. LLMJudgeValidator → task.rubric()                 ← LLM judge
7. result.json written: passed = all three pass
```

The session passes only if **all three** checks pass:
- `end_state["passed"]` — the DB ended up in the right state
- `trajectory_deterministic["passed"]` — the agent followed required behavioral rules
- `trajectory_llm["passed"]` — the LLM judge found no rubric violations

---

## Adding a new task

### Step 1: Create the task file

```python
# tasks/my_new_task.py
import requests
from tasks.base import AbstractTask

class MyNewTask(AbstractTask):

    def seed_requirements(self) -> dict:
        # Specify what must exist in the DB after seeding.
        # Return {} if no special requirements.
        return {
            "required_products": [{"category": "Electronics", "sku": "SKU-E9999"}],
        }

    def setup(self, base_url: str) -> str:
        state = requests.get(f"{base_url}/api/db-state").json()
        # Snapshot anything you'll need in verify()
        self._pre_order_ids = {o["id"] for o in state["orders"]}
        # Return the goal string shown to the agent
        return "Add SKU-E9999 to the cart and complete checkout."

    def check_trajectory(self, trajectory: list[dict]) -> dict:
        violations = []
        # Inspect tool calls. Examples:
        typed_texts = [s["args"].get("text","") for s in trajectory if s.get("tool")=="type_text"]
        navigate_urls = [s["args"].get("url","") for s in trajectory if s.get("tool")=="navigate"]

        if not any("e9999" in t.lower() for t in typed_texts):
            violations.append("Agent never typed SKU-E9999.")

        passed = len(violations) == 0
        return {
            "passed": passed,
            "violations": violations,
            "reasoning": f"{'✓' if passed else '✗'} Typed SKU-E9999",
        }

    def rubric(self) -> str:
        return """\
1. The agent must find and navigate to the product page for SKU-E9999.
2. The agent must add the item to the cart.
3. The agent must complete the checkout process."""

    def verify(self, base_url: str) -> dict:
        state = requests.get(f"{base_url}/api/db-state").json()
        new_orders = [o for o in state["orders"] if o["id"] not in self._pre_order_ids]
        if not new_orders:
            return {"passed": False, "order_id": None}
        order = new_orders[0]
        items = [i for i in state["order_items"] if i["order_id"] == order["id"]]
        has_sku = any(i["sku"] == "SKU-E9999" for i in items)
        return {"passed": has_sku, "order_id": order["id"], "sku_ok": has_sku}
```

### Step 2: Register the task in the task runner

**File:** [`agent_eval/task_runner.py`](../agent_eval/task_runner.py) — `_load_task()` function (around line 112):

```python
elif task_name == "my_new_task":
    from tasks.my_new_task import MyNewTask
    return MyNewTask
```

Also add `"my_new_task"` to the `--task` choices in `main()` (around line 840).

### Step 3: Register in the deterministic validator

**File:** [`agent_eval/validators/deterministic.py`](../agent_eval/validators/deterministic.py) — `_load_task_instance()` function:

```python
elif task_name == "my_new_task":
    from tasks.my_new_task import MyNewTask
    return MyNewTask()
```

### Step 4: Register in the LLM judge

**File:** [`agent_eval/validators/llm_judge.py`](../agent_eval/validators/llm_judge.py) — `_get_rubric()` function:

```python
elif task_name == "my_new_task":
    from tasks.my_new_task import MyNewTask
    return MyNewTask().rubric()
```

### Step 5: Add a demo script (optional)

Copy [`demo/run_cancel_order.sh`](../demo/run_cancel_order.sh) and change `--task cancel_order`
to `--task my_new_task`.

### Step 6: Write a verifier test

**File:** [`tasks/tests/test_verifiers.py`](tests/test_verifiers.py)

Add a test that starts Flask, seeds the DB, runs the agent action manually via HTTP,
and asserts `verify()` returns `passed: True`.

---

## Existing tasks at a glance

| Task | Goal | Key seed requirements | Verify checks |
|---|---|---|---|
| `cancel_order` | Cancel the most recent placed order | `required_orders: [{status: placed}]` | order.status == "cancelled" |
| `apply_coupon` | Add 2× SKU-E7421, apply SAVE10, checkout | `required_products`, `required_coupons` | qty==2, discount_pct==10, total correct |
| `buy_cheapest` | Buy cheapest Electronics item, ship to fixed address | none (Electronics always present) | min price in Electronics, all 4 address parts |

---

## Notes for agentic readers

- **Do not modify `shop/`** — the shop is a fixed environment. Tasks interact with it only via HTTP.
- **`setup()` is called after seeding** — the DB is already populated when `setup()` runs.
- **`verify()` is called after the agent finishes** — the agent may have changed the DB.
- **`check_trajectory()` receives the raw tool call log** — it does not have access to the live shop.
- **All three validators must pass** for a session to be marked `passed: true` in `result.json`.
- **The `result` field in trajectory steps** contains the DOM text returned by the MCP server after each tool call (truncated to 500 chars). It is useful for checking what the agent saw.
