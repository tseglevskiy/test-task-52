# ShopGym — Shop Reference

This document is the authoritative reference for the `shop/` package. It describes what the shop does, how it is seeded, every route it exposes, the data model, the seeder contract, the event log, and the Docker lifecycle. It is complete enough to recreate the shop from scratch.

---

## File layout

```
shop/
├── app.py            # Flask application factory + all routes
├── db.py             # CREATE_TABLES_SQL constant + get_db() + init_db()
├── seed.py           # Dataclasses (SeedConfig etc.) + seed_database() + get_db_snapshot() + CLI
├── vocab.py          # Static product vocabulary: VOCAB dict + ADDRESSES list
├── requirements.txt  # Flask>=3.0,<4.0  (only external dependency)
├── Dockerfile        # python:3.12-slim, exposes 5000, reads DATABASE_PATH + LOG_PATH env vars
├── tests/
│   └── test_seeder.py  # Determinism unit tests (stdlib unittest, no pytest required)
├── templates/
│   ├── base.html     # Site-wide header (logo, category nav, cart badge)
│   ├── index.html    # Product listing: filter / search / sort / paginate
│   ├── product.html  # Product detail: breadcrumb, add-to-cart form, related products
│   ├── cart.html     # Cart: line items, coupon field, totals
│   ├── checkout.html # Checkout: US address form (50-state select), order summary
│   ├── order.html    # Order detail + confirmation banner + cancel button
│   ├── orders.html   # Order history table
│   └── 404.html      # Custom 404 page with link back to /
├── README.md           # This file — routes, schema, UI, Docker
└── SEEDING.md          # Seeding design decisions, determinism guarantees, usage guide
```

---

## Running locally

```bash
# 1. Activate the venv
cd shop && source .venv/bin/activate

# 2. Seed the database
python seed.py --db /tmp/shop.db --seed 42 \
  --require-product '{"category":"Electronics","sku":"SKU-E7421"}' \
  --require-coupon  '{"code":"SAVE10","discount_pct":10.0}' \
  --require-order   '{"status":"placed"}'

# 3. Start the server
DATABASE_PATH=/tmp/shop.db python app.py
# → http://localhost:5000

# Re-seed at any time without restarting (< 5 ms):
curl -X POST 'http://localhost:5000/api/reset?seed=42'
```

---

## 1. Use Cases

### UC-1: Browse and buy the cheapest item in a category
1. Open the product listing page (`/`)
2. Filter by category (e.g. "Electronics")
3. Sort by Price: Low→High to find the cheapest item
4. Open the product detail page
5. Set quantity to 1, click "Add to Cart"
6. Open the cart (`/cart`)
7. Click "Proceed to Checkout"
8. Fill in a US shipping address (name, street, city, state dropdown, 5-digit ZIP)
9. Click "Place Order"
10. Order confirmation page shown with order ID (`/order/<id>?confirmed=1`)

### UC-2: Add a specific SKU with quantity, apply a coupon, checkout
1. Find product with SKU `SKU-E7421` (via search or browsing)
2. Open the product detail page
3. Set quantity to 2, click "Add to Cart"
4. Open the cart
5. Type coupon code `SAVE10` into the coupon field, click "Apply Coupon"
6. Discount shown (10% off subtotal)
7. Click "Proceed to Checkout"
8. Fill in shipping address, click "Place Order"
9. Order confirmation page shown

### UC-3: Cancel the most recent existing order
1. Navigate to the order list page (`/orders`)
2. Identify the most recently placed order (sorted by date descending)
3. Open that order's detail page (`/order/<id>`)
4. Click "Cancel Order"
5. Order status changes to `cancelled`; page reloads showing updated status

---

## 1.1 Feature summary

**Catalog:** paginated product list, category filter, text search (SQL LIKE on name + description), price sort (asc/desc), product detail with breadcrumb and related products.

**Cart:** server-side, keyed by session cookie. Add/update/remove line items. Apply or remove a coupon code. Shows subtotal, discount, and total.

**Checkout:** US address form (name, street, city, state `<select>` with all 50 states + DC, ZIP validated as `^\d{5}$`). Server-side validation; errors re-render the form with values preserved. Placing an order creates the `orders` + `order_items` rows, clears the cart, and redirects to the order detail page with `?confirmed=1`.

**Order management:** list of all orders newest-first; single order detail page (reused for confirmation); cancel a `placed` order (button hidden if already `cancelled`). No JavaScript confirmation dialogs.

**Navigation:** site-wide header on every page (ShopGym logo, one link per category, cart badge "Cart (N)", My Orders link). The active category is bold in both the header nav and the listing page's filter bar.

**Error handling:** custom 404 page (`templates/404.html`) with a link back to `/`.

---

## 1.2 Page Inventory

| Page | URL pattern | Primary purpose |
|---|---|---|
| Product listing | `/` | Browse, filter, search, sort products |
| Product detail | `/product/<id>` | View product, set quantity, add to cart |
| Cart | `/cart` | Review items, apply coupon, go to checkout |
| Checkout | `/checkout` | Enter shipping address, place order |
| Order detail / confirmation | `/order/<id>` | Order detail; confirmation banner if `?confirmed=1`; cancel button if `status='placed'` |
| Order list | `/orders` | All orders, newest first |

---

## 2. Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Product listing |
| `/product/<id>` | GET | Product detail |
| `/cart` | GET | View cart |
| `/cart/add` | POST | Add item to cart (`product_id`, `quantity`) |
| `/cart/update` | POST | Update quantity; `quantity=0` removes the item; `quantity<0` → 400 |
| `/cart/remove` | POST | Remove item (`product_id`) |
| `/cart/coupon` | POST | Apply coupon (`code`); empty `code` removes the applied coupon |
| `/checkout` | GET | Checkout form |
| `/checkout` | POST | Validate + place order → redirect to `/order/<id>?confirmed=1` |
| `/order/<id>` | GET | Order detail / confirmation |
| `/orders` | GET | Order history |
| `/orders/<id>/cancel` | POST | Cancel order (only if `status='placed'`) |
| `/api/db-state` | GET | Full DB snapshot as JSON (for verifiers) |
| `/api/reset` | POST | Wipe + reseed DB; accepts `?seed=N` or a JSON `SeedConfig` body |
| `/api/health` | GET | Returns `{"status":"ok"}` (for gym startup polling) |

### Listing page query parameters
- `?category=Electronics` — filter by category (case-insensitive exact match)
- `?q=cable` — full-text search on name and description (SQL LIKE, case-insensitive)
- `?sort=price_asc` | `?sort=price_desc` | `?sort=default` — sort order (default = `ORDER BY sort_order ASC`)
- `?page=2` — pagination (12 items per page)
- Parameters are combinable: `/?category=Electronics&sort=price_asc&page=2`

### Cart persistence
Cart is stored server-side in `cart_items` (one row per product per session) and `cart_meta` (applied coupon per session), both keyed by a `session_id` UUID4 cookie. The cookie is set `httponly`, `samesite=Lax`, `max_age=30 days`. No filesystem session state; no Flask `session` object.

### Coupon handling
- `POST /cart/coupon` with a non-empty `code`: validates against `coupons` table (case-insensitive); if valid and active → UPSERT `cart_meta`; if not found → redirect to `/cart?coupon_error=...`; if inactive → redirect with error.
- `POST /cart/coupon` with empty `code`: DELETE from `cart_meta` (removes applied coupon).

---

## 3. Data Model

All primary keys are **UUID TEXT** (36-char hyphenated). No integer IDs, no `AUTOINCREMENT`, no `DEFAULT CURRENT_TIMESTAMP`, no `datetime('now')` anywhere in SQL.

```sql
CREATE TABLE IF NOT EXISTS products (
    id          TEXT PRIMARY KEY,
    sku         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       REAL NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS coupons (
    id              TEXT PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    discount_pct    REAL NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1   -- 1=active, 0=inactive
);

CREATE TABLE IF NOT EXISTS orders (
    id               TEXT PRIMARY KEY,
    created_at       INTEGER NOT NULL,           -- Unix epoch seconds, never NOW()
    status           TEXT NOT NULL DEFAULT 'placed',  -- 'placed' | 'cancelled'
    shipping_address TEXT NOT NULL,
    coupon_code      TEXT,                       -- NULL if no coupon
    discount_pct     REAL NOT NULL DEFAULT 0.0,
    subtotal         REAL NOT NULL,
    total            REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id          TEXT PRIMARY KEY,
    order_id    TEXT NOT NULL REFERENCES orders(id),
    product_id  TEXT NOT NULL REFERENCES products(id),
    sku         TEXT NOT NULL,        -- denormalized snapshot at order time
    name        TEXT NOT NULL,        -- denormalized snapshot at order time
    quantity    INTEGER NOT NULL,
    unit_price  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cart_items (
    session_id  TEXT NOT NULL,
    product_id  TEXT NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (session_id, product_id)
);

CREATE TABLE IF NOT EXISTS cart_meta (
    session_id   TEXT PRIMARY KEY,
    coupon_code  TEXT
);

CREATE TABLE IF NOT EXISTS shop_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Seeder writes: ('next_order_ts', str(BASE_TS + 3600))
-- App reads on each checkout POST, uses it as created_at, then increments by 3600
```

### UUID assignment

| Table | Who assigns | Formula |
|---|---|---|
| `products` | Seeder | `uuid5(NAMESPACE_DNS, f"product:{seed}:{cat_idx}:{prod_idx}")` |
| `coupons` | Seeder | `uuid5(NAMESPACE_DNS, f"coupon:{seed}:{row_index}")` |
| `orders` (pre-seeded) | Seeder | `uuid5(NAMESPACE_DNS, f"order:{seed}:{slot_index}")` |
| `order_items` (pre-seeded) | Seeder | `uuid5(NAMESPACE_DNS, f"order_item:{seed}:{slot_index}:{item_index}")` |
| `orders` (agent-created) | App | `uuid4()` — verifiers find new orders via ID-set diff |
| `order_items` (agent-created) | App | `uuid4()` |
| `session_id` | App | `uuid4()` on first request with no cookie |

### Determinism invariants
- No integer IDs, no sequences, no autoincrement anywhere
- No `DEFAULT CURRENT_TIMESTAMP` or `datetime('now')` in any SQL statement
- No `time.time()` or `datetime.now()` call in the seeder or in any DB write in the app
- Seeder uses `random.Random(seed)` (instance-based, not the global `random` module)
- SKUs are `sha256`-derived — immune to `PYTHONHASHSEED`
- Agent-created orders always have `created_at > BASE_TS` (virtual clock starts at `BASE_TS + 3600`)

---

## 4. Seeder

For the full seeding design — two-phase algorithm, determinism decisions, vocabulary format, `SeedConfig` fields, and usage examples — see **[SEEDING.md](SEEDING.md)**.

Quick reference:

```bash
# CLI — reseed the database
python seed.py --db /tmp/shop.db --seed 42 \
  --require-product '{"category":"Electronics","sku":"SKU-E7421"}' \
  --require-coupon  '{"code":"SAVE10","discount_pct":10.0}' \
  --require-order   '{"status":"placed"}'
```

`get_db_snapshot(db_path)` returns the full DB state as a dict — callable directly by verifiers with no HTTP or running Flask process required.

---

## 5. Internal API

These endpoints are for the gym layer only — not exposed to the agent.

### `GET /api/health`
Returns `{"status":"ok"}`. Gym polls this after container start before calling reset.

### `GET /api/db-state`
Returns full DB as JSON:
```json
{
  "products":    [{"id":"...","sku":"SKU-E7421","name":"...","category":"Electronics","price":68.98}],
  "coupons":     [{"id":"...","code":"SAVE10","discount_pct":10.0,"active":1}],
  "orders":      [{"id":"...","created_at":1716000000,"status":"placed","shipping_address":"...","coupon_code":null,"discount_pct":0.0,"subtotal":59.98,"total":59.98}],
  "order_items": [{"id":"...","order_id":"...","product_id":"...","sku":"...","name":"...","quantity":2,"unit_price":29.99}]
}
```

### `POST /api/reset`
Accepts either:
- `?seed=N` query param (no body) → `SeedConfig(seed=N)` with all defaults
- JSON body with full `SeedConfig` fields (all except `seed` are optional)

Returns: `{"status":"ok","seed":42,"elapsed_ms":4}`

```bash
# Quick reset
curl -X POST 'http://localhost:5000/api/reset?seed=42'

# Full config
curl -X POST http://localhost:5000/api/reset \
  -H "Content-Type: application/json" \
  -d '{"seed":42,"n_categories":10,"n_products_per_category":8,
       "required_products":[{"category":"Electronics","sku":"SKU-E7421"}],
       "required_coupons":[{"code":"SAVE10","discount_pct":10.0}],
       "required_orders":[{"status":"placed"}],
       "n_filler_orders":3}'
```

---

## 6. UI Details

- **Header (every page):** `ShopGym` logo (links to `/`), one link per seeded category, active category shown in bold, `Cart (N)` count badge (live from DB), `My Orders` link.
- **Listing page:** h1 shows active category name when filtering (e.g. "Electronics Products") with an `[All Products]` link; "All" is bold when no filter active. Category links are bold when active. Sort indicator is bold for the active sort. Product count line. Pagination with `← Previous` / `Next →`.
- **Product detail:** breadcrumb `Home > Category > Name`. SKU, price, quantity input (`min=1`), "Add to Cart" button. "Related Products" section: up to 3 links, cyclic within category, filled from other categories if needed.
- **Cart:** line items table with Update (quantity input) and Remove buttons per row. Coupon field + "Apply Coupon" button; if a coupon is applied, shows "Remove Coupon" instead. Subtotal, discount line (only when coupon applied), total. "Proceed to Checkout" and "Continue Shopping" links. "Your cart is empty" message when empty.
- **Checkout:** Full Name, Street Address, City (all free text, required), State (`<select>` with all 50 US states + DC, abbreviations as values), ZIP (text, server-validated `^\d{5}$`). Order summary table. "Place Order" button. Validation errors shown inline next to the invalid field; form values preserved on re-render.
- **Order detail (`/order/<id>`):** Order ID (`<code>`), date (human-readable from epoch), status badge (green for `placed`, grey for `cancelled`). Line items table. Shipping address in `<pre>`. "Cancel Order" button only if `status='placed'` — no JavaScript confirmation dialog. Confirmation banner (`border:2px solid green`) when `?confirmed=1` is present. "Back to My Orders" and "Continue Shopping" links.
- **Orders list:** table with columns Order ID (truncated + `…`), Date, Status (coloured text), Total. Each row links to `/order/<id>`. "No orders yet" message when empty.
- **404 page:** "404 – Page Not Found" heading, brief message, links to `/`, `/orders`, `/cart`.

---

## 7. Docker and Bootstrap

### Running in Docker

```bash
mkdir -p /tmp/gym_1
touch /tmp/gym_1/shop.db /tmp/gym_1/shop.jsonl

docker run \
  -v /tmp/gym_1/shop.db:/app/shop.db \
  -v /tmp/gym_1/shop.jsonl:/app/shop.jsonl \
  -e DATABASE_PATH=/app/shop.db \
  -e LOG_PATH=/app/shop.jsonl \
  -p 5001:5000 \
  shop:latest
```

Docker bind-mounting a **file** (not a directory) requires the file to exist on the host before `docker run`. Both `.db` and `.jsonl` must be pre-created with `touch`.

### Startup

On start, `create_app()` calls `init_db(db_path)` which runs `CREATE TABLE IF NOT EXISTS` for all tables. This is idempotent — safe on an empty file, a pre-seeded file, or a mid-episode file. The app then serves `/api/health` immediately. The gym polls `/api/health` before calling `/api/reset`.

### Statelessness

The Flask process holds zero in-memory state between requests:
- No global dicts, caches, or counters
- Cart is stored in `cart_items` + `cart_meta` (DB tables), keyed by `session_id` cookie
- JSONL log is opened in `"a"` mode per request (no persistent file handle)
- No background threads

### Implementation stack

| Component | Choice |
|---|---|
| Framework | Flask 3.x + Jinja2 templates |
| Database | `sqlite3` stdlib (WAL mode, foreign keys ON) |
| UUIDs | `uuid` stdlib (`uuid4()` for app rows, `uuid5()` for seeder rows) |
| Seeding PRNG | `random.Random(seed)` — instance-based, not global `random` |
| SKU derivation | `hashlib.sha256` — deterministic, immune to `PYTHONHASHSEED` |
| HTTP server | Flask dev server (single-threaded, sufficient for one agent per instance) |
| External deps | Flask only (`Flask>=3.0,<4.0`; Werkzeug, Jinja2 pulled transitively) |

---

## 8. Event Log

### Location
`Path(db_path).with_suffix(".jsonl")` — same directory as the DB file. Truncated to 0 bytes at the start of each `seed_database()` call. Appended on every request.

### Format
One JSON object per line:
```json
{
  "ts":         1700001234.567,
  "session_id": "f3a4b5c6-...",
  "method":     "POST",
  "path":       "/cart/coupon",
  "params":     {"code": "SAVE10"},
  "result":     {"status": "ok", "discount_pct": 10.0, "discount_amount": 5.99}
}
```

`ts` uses `time.time()` — the only wall-clock call in the codebase. All DB timestamps use the virtual clock.

### Event result shapes

| Event | method + path | `result` |
|---|---|---|
| Browse listing | GET `/` | `{n_products, n_pages, current_page}` |
| View product | GET `/product/<id>` | `{sku, name, category, price}` |
| View cart | GET `/cart` | `{n_items, subtotal, coupon_code, total}` |
| Add to cart | POST `/cart/add` | `{status: ok\|not_found, cart_total_items}` |
| Update cart | POST `/cart/update` | `{status: ok\|not_found\|invalid_quantity}` |
| Remove from cart | POST `/cart/remove` | `{status: ok\|not_found}` |
| Apply/remove coupon | POST `/cart/coupon` | `{status: ok\|removed\|invalid_code\|inactive, discount_pct?, discount_amount?}` |
| View checkout | GET `/checkout` | `{n_items, subtotal, total}` |
| Submit checkout | POST `/checkout` | `{status: ok\|validation_error, order_id?, errors?}` |
| View order | GET `/order/<id>` | `{status, total, n_items}` |
| View orders | GET `/orders` | `{n_orders}` |
| Cancel order | POST `/orders/<id>/cancel` | `{status: ok\|already_cancelled\|not_found}` |

Log writes are per-route (not via `after_request`). If a write fails, the error is printed to stderr and the response proceeds normally.

---

## 9. Out of Scope

- User login, registration, or authentication
- Payment processing
- CSS beyond minimal inline styles (status badge colours)
- Product images
- Email notifications
- Admin panel
- Multi-currency or tax calculation
- Shipping cost (shipping is free; total = subtotal − discount)
- Stock tracking (`products` has no `stock` column; "Add to Cart" always succeeds if the product exists)
- Cross-browser support (Chromium-only via Playwright)

---

## 10. Implementation Notes

### `line_items` key in `_get_cart_info()`
The cart info dict uses `"line_items"` (not `"items"`) as the key for the list of cart rows. This avoids a Jinja2 ambiguity where `cart.items` resolves to the Python dict's built-in `.items()` method rather than the `"items"` key. Templates reference `cart.line_items`.

### Context processor DB connections
The `@app.context_processor` that injects `categories` and `cart_count` into every template opens two separate DB connections — one for the category query and one for the cart count. This avoids a subtle bug where a failed aggregate query would silently zero out both values due to a shared `except Exception` block.

### Categories SQL
`SELECT category FROM products GROUP BY category ORDER BY MIN(sort_order)` — `GROUP BY` must precede `ORDER BY`. The simpler `SELECT DISTINCT` form would require a subquery to maintain sort order.
