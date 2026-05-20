# Tasks — What They Are and How to Write One

A **task** defines a goal for the agent to accomplish inside the shop. It is not a policy (it doesn't click anything). It is not an evaluator that reads the browser screen. It is a self-contained unit that answers three questions:

1. **What does the DB need before the episode starts?** (`seed_requirements`)
2. **What is the agent trying to do?** (`setup` → returns goal string)
3. **Did the agent do it?** (`verify` → reads DB state via HTTP)

All three live in one file, one class. Adding a new task means adding one file to `tasks/`.

---

## The AbstractTask interface

```python
# gym_env/tasks/base.py

class AbstractTask(ABC):

    @abstractmethod
    def seed_requirements(self) -> dict:
        """
        Return a dict that is merged into the POST /api/reset body.
        Declare what the DB must contain before the episode starts.

        Keys (all optional — omit if you don't need them):
          "required_products": [{"category": str, "sku": str, ...}]
          "required_coupons":  [{"code": str, "discount_pct": float}]
          "required_orders":   [{"status": "placed" | "cancelled"}]

        Return {} if you have no special requirements.
        """

    @abstractmethod
    def setup(self, page: Page, base_url: str) -> str:
        """
        Called once per episode, after the DB is seeded and the browser
        has navigated to base_url.

        1. Take a pre-episode snapshot (e.g. record existing order IDs).
        2. Return the goal string — the natural-language task description.
        """

    @abstractmethod
    def verify(self, base_url: str, page: Page) -> tuple[float, bool]:
        """
        Called after every env.step(). Check if the task is done.

        Query the shop via GET {base_url}/api/db-state.
        Do NOT open the SQLite file directly.

        Return:
          (1.0, True)  — success, episode ends
          (0.0, False) — not done yet, episode continues
          (0.0, True)  — definitive failure (use sparingly)
        """
```

---

## The `/api/db-state` response

`verify()` reads state via `GET {base_url}/api/db-state`. The response shape:

```json
{
  "products": [
    {"id": "...", "sku": "SKU-E7421", "name": "...", "category": "Electronics", "price": 68.98}
  ],
  "coupons": [
    {"id": "...", "code": "SAVE10", "discount_pct": 10.0, "active": 1}
  ],
  "orders": [
    {
      "id": "...", "created_at": 1716003601, "status": "placed",
      "shipping_address": "Alice Smith\n123 Main St\nSpringfield, IL 62701",
      "coupon_code": "SAVE10", "discount_pct": 10.0,
      "subtotal": 59.98, "total": 53.98
    }
  ],
  "order_items": [
    {
      "id": "...", "order_id": "...", "product_id": "...",
      "sku": "SKU-E7421", "name": "...", "quantity": 2, "unit_price": 29.99
    }
  ]
}
```

Key points for verifiers:
- `order_items` does **not** include `category` — join via `product_id → products` if you need it
- `created_at` is a Unix epoch integer (never `datetime.now()` — fully deterministic)
- Agent-created orders always have `created_at > BASE_TS` (virtual clock starts at `BASE_TS + 3600`)
- `shipping_address` is a multiline string: `"{name}\n{street}\n{city}, {state} {zip}"`
- Use `abs(a - b) < 0.01` for float price comparisons (SQLite REAL, IEEE 754)

---

## Detecting new orders

The standard pattern for tasks that involve placing an order:

```python
def setup(self, page, base_url):
    state = requests.get(f"{base_url}/api/db-state").json()
    self._pre_order_ids = {o["id"] for o in state["orders"]}  # snapshot
    return "Your goal string here."

def verify(self, base_url, page):
    state = requests.get(f"{base_url}/api/db-state").json()
    new_orders = [o for o in state["orders"] if o["id"] not in self._pre_order_ids]
    if not new_orders:
        return 0.0, False
    order = new_orders[0]
    # ... check order contents ...
    return 1.0, True
```

Agent-created orders have `uuid4()` IDs (unpredictable), while pre-seeded orders have `uuid5()` IDs (deterministic). The ID-set diff works reliably regardless.

---

## Existing tasks

The three concrete tasks live in `tasks/`. See **[`tasks/README.md`](../tasks/README.md)** for:
- What each task does (goal, seeder requirements, agent steps, verifier logic)
- Which oracle function in `scripts/parallel_demo.py` demonstrates it

---

## How to add a new task

1. **Create `tasks/your_task.py`**

```python
import requests
from gym_env.tasks.base import AbstractTask

class YourTask(AbstractTask):

    def seed_requirements(self) -> dict:
        # Declare what the seeder must put in the DB.
        # Return {} if no special requirements.
        return {
            "required_products": [...],
            "required_coupons":  [...],
            "required_orders":   [...],
        }

    def setup(self, page, base_url: str) -> str:
        state = requests.get(f"{base_url}/api/db-state").json()
        # Snapshot whatever you need to detect success in verify().
        self._pre_order_ids = {o["id"] for o in state["orders"]}
        # Return the goal string shown to the agent.
        return "Do the thing."

    def verify(self, base_url: str, page) -> tuple[float, bool]:
        state = requests.get(f"{base_url}/api/db-state").json()
        # Check whether the agent completed the task.
        # Return (1.0, True) for success, (0.0, False) to keep going.
        ...
        return 0.0, False
```

2. **Export it from `tasks/__init__.py`**

```python
from .your_task import YourTask
```

3. **Write a verifier unit test in `gym_env/tests/test_verifiers.py`**

```python
def test_your_task_verifier():
    from tasks.your_task import YourTask
    task = YourTask()
    task._pre_order_ids = set()  # or whatever state you need

    success_state = {
        "orders": [...],
        "order_items": [...],
        "products": [...],
        "coupons": [],
    }
    with patch("requests.get", _mock_get(success_state)):
        reward, terminated = task.verify("http://localhost:5001", None)
    assert reward == 1.0 and terminated is True

    # Also test the negative path
    with patch("requests.get", _mock_get(incomplete_state)):
        reward, terminated = task.verify("http://localhost:5001", None)
    assert reward == 0.0 and terminated is False
```

Run: `gym_env/.venv/bin/python -m pytest gym_env/tests/test_verifiers.py -v`

4. **Optionally add a scripted oracle to `scripts/parallel_demo.py`** to verify the task is solvable and the verifier rewards correctly.

---

## Common pitfalls

**Don't open the SQLite file directly.** Always use `GET {base_url}/api/db-state`. The shop owns its data model.

**Don't rely on rendered HTML or LLM output.** Verifiers must read backend state. An LLM judge or an HTML scraper introduces non-determinism that undermines the gym's guarantees.

**Don't share state between episodes.** Set all instance variables (`_target_order_id` etc.) inside `setup()` every episode. If you set them in `__init__`, a crashed `reset()` may leave stale state.

**Float tolerance.** Prices and totals are stored as SQLite REAL (IEEE 754 double). Use `abs(a - b) < 0.01` not `==`.

**Pre-order IDs snapshot must happen in `setup()`**, not `verify()`. `verify()` is called after every step — by the time it runs, the agent may have already placed an order.
