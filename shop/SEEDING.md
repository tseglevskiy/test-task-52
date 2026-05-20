# ShopGym — Seeding and Determinism

Everything here explains *why* the seeder works the way it does and how to use it correctly. If you need the route table or data schema, see `README.md`.

---

## The problem this solves

An RL gym is useless if two resets with the same seed produce different starting states. Prices, product ordering, pre-existing orders — all must be identical. At the same time, the gym layer needs to guarantee that certain items exist (a specific coupon code, a specific SKU, a pre-placed order to cancel). These requirements pull in opposite directions: determinism wants a fixed rng sequence; task-specific injection wants to insert things after the fact.

The seeder resolves this with two strictly separated phases.

---

## Core design decisions

### 1. Generate first, inject later

All random draws happen in Phase 1 with no knowledge of task requirements. Only after every rng call has completed does Phase 2 patch specific rows in memory before writing to the database.

This means you can add, remove, or change a `required_product` entry and it will never shift the prices or names of any other product. The rng sequence for a given seed is immutable.

Phase 1 draws (in this exact order, for reproducibility):
- For each category: sample `n_products_per_category` vocab entries, derive price, assign sort_order and UUID
- For each order slot: pick shipping address, pick 1–3 products, pick quantities

Phase 2 injects (no rng calls):
- Override specific product fields (e.g. force a known SKU)
- Insert required coupons verbatim
- Override order statuses for the first N slots

### 2. SKUs come from SHA-256, not the rng

`"SKU-" + sha256(f"{seed}:{cat_idx}:{prod_idx}").hexdigest()[:5].upper()`

Using SHA-256 instead of `rng.choice(...)` means the SKU doesn't consume a rng call and is therefore completely isolated from any other rng draw. It's also immune to `PYTHONHASHSEED`, which affects Python's built-in hash but not hashlib.

### 3. UUIDs: deterministic for seeded rows, random for agent rows

Pre-seeded rows get UUID5 values derived from the seed and their position:
- Product: `uuid5(NAMESPACE_DNS, "product:{seed}:{cat_idx}:{prod_idx}")`
- Coupon: `uuid5(NAMESPACE_DNS, "coupon:{seed}:{index}")`
- Order: `uuid5(NAMESPACE_DNS, "order:{seed}:{slot}")`
- Order item: `uuid5(NAMESPACE_DNS, "order_item:{seed}:{slot}:{item}")`

Agent-created orders and their items get `uuid4()` — non-deterministic, but verifiers don't need to know them in advance; they find new orders by diffing the ID set against the pre-seed snapshot.

No integer IDs anywhere. An agent cannot infer "most recent" from an ID — it has to read `created_at` timestamps from the page.

### 4. No wall-clock time anywhere in DB state

`time.time()` is called exactly once per request, for the JSONL event log (post-hoc analysis only). It never touches the database.

Pre-seeded order timestamps use a fixed anchor: `BASE_TS = 1_716_000_000` (2024-05-18). Slot 0 gets `BASE_TS`, slot 1 gets `BASE_TS - 86400`, and so on. This is a constant, not derived from the current time.

Agent-created orders use a **virtual clock** stored in `shop_meta`: the app reads `next_order_ts`, uses it as `created_at`, then writes `next_order_ts + 3600` back. The virtual clock starts at `BASE_TS + 3600` after each reset. This guarantees agent orders always appear newer than any pre-seeded order, and the sequence is reproducible (no drift from wall time).

### 5. Order slots are ordered newest-first

Slot 0 is always the most recent order (`created_at = BASE_TS`). If you declare a `RequiredOrder` as the first element in the list, it lands in slot 0 and is guaranteed to be the most recent order in the DB — which is exactly what the cancel-recent-order task verifier checks.

### 6. Order items are pre-override snapshots

When Phase 1 builds order slots, it snapshots `(sku, name, price)` from the rng-generated catalog — *before* Phase 2 applies any overrides. This is intentional: it mirrors real e-commerce behaviour where an order captures the product's state at purchase time, not its current state.

Concretely: if you force `sku="SKU-E7421"` onto a product in Phase 2, the pre-existing order items in that order slot will still show the rng-generated SKU. Only the `products` table row gets the override.

### 7. Vocabulary is static source material

`vocab.py` contains realistic product definitions (name, description, price range per entry). It is baked into the Docker image and never modified at runtime. SKUs and exact prices are derived from the seed — they don't live in the vocabulary.

Currently: 10 categories × 100 products each, 20 shipping addresses. If you need a larger vocabulary, replace `vocab.py` — no other code changes required. The seeder raises `ValueError` if you ask for more products than the vocabulary has for a given category.

Category selection uses Python dict declaration order (deterministic in Python 3.7+): the first `n_categories` keys are selected, no rng draw involved.

---

## SeedConfig at a glance

| Field | Default | What it controls |
|---|---|---|
| `seed` | (required) | The single integer that drives all rng and UUID derivation |
| `base_ts` | `1_716_000_000` | Anchor for pre-seeded order timestamps |
| `n_categories` | `5` | How many vocabulary categories to include |
| `n_products_per_category` | `8` | How many products per category |
| `required_products` | `[]` | Products that must exist with specific overrides |
| `required_coupons` | `[]` | Coupons that must exist verbatim |
| `required_orders` | `[]` | Status overrides for the first N order slots |
| `n_filler_orders` | `3` | Total pre-seeded order count (required + filler) |

`RequiredProduct` takes a `category` (which vocabulary slot) and an `overrides` dict. Only keys present in overrides are changed; everything else (name, description, price, sort_order) keeps its rng value.

`RequiredOrder` only specifies `status` (`"placed"` or `"cancelled"`). The items and shipping address for that slot come from the Phase 1 rng draw.

The seeder raises `ValueError` for: more categories than vocabulary has, more products than vocabulary has per category, required product category outside the selected range, duplicate required SKUs, duplicate coupon codes, more required orders than `n_filler_orders`.

---

## How to seed

### CLI (simplest)

```bash
# Bare minimum — all defaults
python seed.py --db /tmp/shop.db --seed 42

# With task-specific requirements
python seed.py --db /tmp/shop.db --seed 42 \
  --n-categories 10 --n-products-per-category 8 \
  --require-product '{"category":"Electronics","sku":"SKU-E7421"}' \
  --require-coupon  '{"code":"SAVE10","discount_pct":10.0}' \
  --require-order   '{"status":"placed"}'
```

`--require-product` JSON: the `category` key identifies which vocabulary slot; every other key is an override applied in Phase 2. Repeatable for multiple required products.

### HTTP reset (while the server is running)

```bash
# Quick — uses SeedConfig defaults
curl -X POST 'http://localhost:5000/api/reset?seed=42'

# Full config
curl -X POST http://localhost:5000/api/reset \
  -H "Content-Type: application/json" \
  -d '{
    "seed": 42,
    "n_categories": 10,
    "n_products_per_category": 8,
    "required_products": [{"category":"Electronics","sku":"SKU-E7421"}],
    "required_coupons":  [{"code":"SAVE10","discount_pct":10.0}],
    "required_orders":   [{"status":"placed"}],
    "n_filler_orders": 3
  }'
```

Returns `{"status":"ok","seed":42,"elapsed_ms":4}`.

All fields except `seed` are optional; missing fields fall back to `SeedConfig` defaults.

### From Python (gym layer)

```python
from seed import SeedConfig, RequiredProduct, RequiredCoupon, RequiredOrder, seed_database

config = SeedConfig(
    seed=env_seed,
    n_categories=10,
    n_products_per_category=8,
    required_products=[RequiredProduct(category="Electronics", overrides={"sku": "SKU-E7421"})],
    required_coupons=[RequiredCoupon(code="SAVE10", discount_pct=10.0)],
    required_orders=[RequiredOrder(status="placed")],
    n_filler_orders=3,
)
seed_database(db_path, config)
```

`seed_database()` is a single SQLite transaction — either the full DB is rebuilt or nothing changes. It also truncates the `.jsonl` event log at the same path.

---

## What the seeder guarantees

- Same seed + same config → byte-identical DB on every run
- Adding or changing a `required_*` entry → only the targeted rows change; all other rng-derived content is unaffected
- `required_orders[0]` always lands in slot 0 (highest `created_at`); agent cannot find a newer pre-seeded order by any means
- Agent-created orders always have `created_at > BASE_TS`; they are always newer than any pre-seeded order
- No `time.time()`, `datetime.now()`, `CURRENT_TIMESTAMP`, or `datetime('now')` anywhere in DB-facing code

---

## Testing determinism

```bash
cd /mnt/d/p/gym/shop
python tests/test_seeder.py
```

Four tests, stdlib `unittest`, no external dependencies:
1. `seed(1)` run twice → identical snapshots
2. `seed(1)` vs `seed(42)` → different catalogs
3. `seed(1)` + required product/coupon/order, run twice → identical
4. `seed(1)` with vs without an override → differ only in the overridden field; all other rows are untouched
