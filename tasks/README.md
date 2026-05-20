# tasks/ — Concrete Task Implementations

This directory contains the three task classes for ShopGym. Each file is a self-contained task definition: what to seed, what the goal is, and how to verify success.

For the full task API, design decisions, and a step-by-step guide to writing a new task, see **[`gym_env/TASK.md`](../gym_env/TASK.md)**.

---

## What's here

```
tasks/
├── __init__.py        # exports all three task classes
├── cancel_order.py    # CancelRecentOrderTask
├── apply_coupon.py    # ApplyCouponWithQuantityTask
└── buy_cheapest.py    # BuyCheapestInCategoryTask
```

---

## Task 1 — `cancel_order.py`

**Class:** `CancelRecentOrderTask`

**Goal:** *"Cancel the most recent existing order in the account."*

**What the seeder provides:** One pre-existing order with `status="placed"`.

**What the agent must do:**
1. Navigate to `/orders`
2. Open the most recent order (top of the list)
3. Click "Cancel Order"

**What the verifier checks:** The order that existed at `setup()` time with the largest `created_at` timestamp now has `status == "cancelled"`.

**Oracle:** `run_cancel_oracle` in `scripts/parallel_demo.py` — 2 steps: navigate to order page, click "Cancel Order" by ARIA role.

---

## Task 2 — `apply_coupon.py`

**Class:** `ApplyCouponWithQuantityTask`

**Goal:** *"Add 2 units of SKU-E7421 to the cart, apply coupon SAVE10, and complete checkout."*

**What the seeder provides:** A product with `sku="SKU-E7421"` in Electronics, and a coupon `SAVE10` with 10% discount.

**What the agent must do:**
1. Find the product with SKU-E7421 (browse or search)
2. Set quantity to 2 and add to cart
3. Apply coupon code `SAVE10`
4. Complete checkout with any valid US address

**What the verifier checks:**
- A new order appeared since `setup()`
- The order has a line item for SKU-E7421 with `quantity == 2`
- `discount_pct == 10.0`
- `total == subtotal * 0.9` (within float tolerance)

**Oracle:** `run_apply_coupon_oracle` in `scripts/parallel_demo.py` — 18 steps: navigate to product, set qty=2, add to cart, apply SAVE10, fill checkout form, place order.

---

## Task 3 — `buy_cheapest.py`

**Class:** `BuyCheapestInCategoryTask`

**Goal:** *"Buy the cheapest item in the 'Electronics' category and ship it to 123 Main St, Springfield, IL 62701."*

**What the seeder provides:** Default seeding always includes an Electronics category with 8 products. No special requirements.

**What the agent must do:**
1. Browse or filter to the Electronics category
2. Identify the cheapest product (sort by price or compare manually)
3. Add it to cart (qty=1)
4. Complete checkout with the exact address: `123 Main St, Springfield, IL 62701`

**What the verifier checks:**
- A new order appeared since `setup()`
- The ordered item's `unit_price` matches the minimum Electronics price recorded at `setup()` time (within 0.01)
- The item's `product_id` maps to `category == "Electronics"` in the products table (cross-reference required — `order_items` doesn't store category)
- The `shipping_address` contains all four components: `"123 Main St"`, `"Springfield"`, `"IL"`, `"62701"`

**Oracle:** `run_buy_cheapest_oracle` in `scripts/parallel_demo.py` — 13 steps: navigate to cheapest Electronics product, add to cart, fill checkout with the required address, place order.

---

## Adding a new task

See **[`gym_env/TASK.md`](../gym_env/TASK.md)** for:
- The `AbstractTask` interface (`seed_requirements`, `setup`, `verify`)
- The `/api/db-state` response shape
- The "detect new orders" pattern
- Step-by-step checklist: file → export → test → oracle
- Common pitfalls
