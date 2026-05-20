# Shop Requirements

> This document specifies what the shop must do, how it should be seeded, and the API contract between the shop and the gym layer. It is intended for the developer building the shop.

---

## 1. Use Cases (from task specification)

The shop must support the following user journeys, derived from the three gym tasks:

### UC-1: Browse and buy the cheapest item in a category
1. User opens the product listing page
2. User filters by category (e.g. "Electronics")
3. User sorts or scans to find the cheapest item
4. User opens the product detail page
5. User sets quantity to 1 and clicks "Add to Cart"
6. User opens the cart
7. User clicks "Proceed to Checkout"
8. User fills in a shipping address (name, street, city, state, zip)
9. User clicks "Place Order"
10. Order confirmation page is shown with an order ID

### UC-2: Add a specific SKU with quantity, apply a coupon, checkout
1. User finds product with SKU `SKU-E7421` (via search or browsing)
2. User opens the product detail page
3. User sets quantity to 2 and clicks "Add to Cart"
4. User opens the cart
5. User types coupon code `SAVE10` into the coupon field and clicks "Apply"
6. Discount is shown in the cart (10% off subtotal)
7. User clicks "Proceed to Checkout"
8. User fills in a shipping address
9. User clicks "Place Order"
10. Order confirmation page is shown

### UC-3: Cancel the most recent existing order
1. User navigates to the order list page (`/orders`)
2. User identifies the most recently placed order (sorted by date descending)
3. User opens that order's detail page
4. User clicks "Cancel Order"
5. Order status changes to `cancelled`
6. Confirmation is shown on the page

---

## 1.1 Feature List (derived from use cases)

**Catalog**
- Display a paginated list of products
- Filter products by category
- Search products by name/description (text search)
- Sort products by price (ascending / descending)
- Show product detail: name, SKU, price, description

**Cart**
- Add a product to the cart with a specified quantity
- View all items currently in the cart
- Update quantity of a cart item
- Remove a cart item
- Apply a coupon code and show the resulting discount
- Show coupon error if code is invalid or inactive
- Show subtotal, discount amount, and final total

**Checkout**
- Enter a shipping address with US format validation (see section 2 for field rules)
- State selected from a dropdown of all 50 US states + DC (not a free-text field)
- ZIP code validated as exactly 5 digits (`\d{5}`)
- Review order summary before placing
- Place an order (no payment required)
- Receive an order confirmation with an order ID

**Order management**
- View a list of all past orders sorted by date (newest first)
- View the detail of a single order (items, address, total, status)
- Cancel an order that is in `placed` status

**Navigation / chrome**
- Site-wide header with category links and a cart badge showing item count
- Breadcrumb trail on product detail page

---

## 1.2 Page Inventory

| Page | URL pattern | Primary purpose |
|---|---|---|
| Product listing | `/` | Browse, filter, search, sort products |
| Product detail | `/product/<id>` | View product, set quantity, add to cart |
| Cart | `/cart` | Review items, apply coupon, go to checkout |
| Checkout | `/checkout` | Enter shipping address, place order |
| Order / confirmation | `/order/<id>` | Single template: order detail, cancel if placed, confirmation banner if `?confirmed=1` |
| Order list | `/orders` | See all orders with status and date |

---

## 2. Pages and Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Product listing: filter, search, sort, paginate |
| `/product/<id>` | GET | Product detail: name, SKU, price, add-to-cart form |
| `/cart` | GET | View cart: line items, coupon input, totals |
| `/cart/add` | POST | Add item to cart (product_id, quantity) |
| `/cart/update` | POST | Update line item quantity |
| `/cart/remove` | POST | Remove line item |
| `/cart/coupon` | POST | Apply or remove coupon code |
| `/checkout` | GET | Checkout form: shipping address, order summary |
| `/checkout` | POST | Submit order → redirect to confirmation |
| `/order/<id>` | GET | Order confirmation / order detail |
| `/orders` | GET | Order history: list all orders, status badges |
| `/orders/<id>/cancel` | POST | Cancel an order (only if status=`placed`) |
| `/api/db-state` | GET | Internal: returns full DB as JSON (for verifiers) |
| `/api/reset` | POST | Internal: wipe and reseed DB from `?seed=N` |
| `/api/health` | GET | Returns `{"status": "ok"}` — for startup probing |

### Listing page (`/`) query parameters
- `?category=Electronics` — filter by category name (exact match, case-insensitive)
- `?q=cable` — full-text search on product name and description (SQL LIKE)
- `?sort=price_asc` or `?sort=price_desc` — price sort; omitting `sort` (or `?sort=default`) uses `ORDER BY sort_order ASC`
- `?page=2` — pagination (default 12 items per page)
- Parameters are combinable: `/?category=Electronics&sort=price_asc`

### Cart persistence
The cart is stored server-side in the database, keyed by a session token stored in a cookie (`session_id`). This makes the cart durable across page loads and avoids filesystem state in the container.

Schema: `cart_items(session_id TEXT, product_id TEXT, quantity INT)` — one row per product per session. The currently applied coupon code (if any) is stored in a separate `cart_meta` table keyed by `session_id`.

### Coupon removal
To remove an applied coupon, the agent POSTs to `/cart/coupon` with an empty `code` field. The server deletes the row from `cart_meta` and returns `{"status": "removed"}`. This mirrors the "Apply / Remove" toggle found on real e-commerce sites.

### Cart quantity update edge case
`POST /cart/update` with `quantity=0` removes the item from the cart entirely (equivalent to `/cart/remove`). This matches standard e-commerce behaviour (e.g. Amazon, eBay). `quantity < 0` returns a 400 validation error.

---

## 3. Data Model

All primary keys are **UUIDs stored as TEXT** (36-char hyphenated format). This prevents agents from inferring ordering or recency from ID values (e.g. finding "the most recent order" by sorting IDs). An agent must navigate to the orders page and read `created_at` dates, not exploit ID structure.

```sql
-- Products
CREATE TABLE products (
    id          TEXT PRIMARY KEY,      -- UUID, assigned by seeder deterministically
    sku         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       REAL NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0  -- stable listing order; filled by seeder
);

-- Coupons
CREATE TABLE coupons (
    id              TEXT PRIMARY KEY,  -- UUID, assigned by seeder deterministically
    code            TEXT NOT NULL UNIQUE,
    discount_pct    REAL NOT NULL,     -- e.g. 10.0 for 10%
    active          INTEGER NOT NULL DEFAULT 1   -- 1=active, 0=inactive
);

-- Orders
CREATE TABLE orders (
    id               TEXT PRIMARY KEY, -- UUID: seeder uses uuid5(seed+index); app uses uuid4()
    created_at       INTEGER NOT NULL, -- Unix epoch seconds (integer, never NOW())
    status           TEXT NOT NULL DEFAULT 'placed',  -- 'placed' | 'cancelled'
    shipping_address TEXT NOT NULL,
    coupon_code      TEXT,             -- NULL if no coupon used
    discount_pct     REAL NOT NULL DEFAULT 0.0,
    subtotal         REAL NOT NULL,
    total            REAL NOT NULL
);

-- Order line items
CREATE TABLE order_items (
    id          TEXT PRIMARY KEY,      -- UUID
    order_id    TEXT NOT NULL REFERENCES orders(id),
    product_id  TEXT NOT NULL REFERENCES products(id),
    sku         TEXT NOT NULL,         -- denormalized snapshot at order time
    name        TEXT NOT NULL,         -- denormalized snapshot at order time
    quantity    INTEGER NOT NULL,
    unit_price  REAL NOT NULL          -- snapshot of price at order time
);

-- Cart (server-side, keyed by session token cookie)
CREATE TABLE cart_items (
    session_id  TEXT NOT NULL,         -- UUID4 generated on first visit, stored in cookie
    product_id  TEXT NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (session_id, product_id)
);

-- Cart metadata: tracks the applied coupon per session
CREATE TABLE cart_meta (
    session_id   TEXT PRIMARY KEY,     -- matches cart_items.session_id
    coupon_code  TEXT                  -- NULL or the currently applied coupon code
);

-- Shop metadata: virtual clock and other instance-level state
-- Used to assign created_at to agent-created orders without calling time.time()
CREATE TABLE shop_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Seeder inserts: ('next_order_ts', str(BASE_TS + 3600))
-- App reads this on each checkout POST, writes the order, increments by 3600
```

### UUID assignment rules

| Table | Who assigns | How |
|---|---|---|
| `products` | Seeder | `uuid.uuid5(NAMESPACE_DNS, f"product:{seed}:{category}:{row_index}")` |
| `coupons` | Seeder | `uuid.uuid5(NAMESPACE_DNS, f"coupon:{seed}:{row_index}")` |
| `orders` (seeded) | Seeder | `uuid.uuid5(NAMESPACE_DNS, f"order:{seed}:{row_index}")` |
| `orders` (agent-created) | App | `uuid.uuid4()` — non-deterministic, but verifier finds new orders via ID set diff |
| `order_items` | Seeder or App | Same pattern as their parent order |
| `cart_meta.session_id` | App (on first cart/coupon action) | same UUID as `cart_items.session_id` |
| `session_id` | App (on first request) | `uuid.uuid4()` — unique per browser session |

### Determinism constraints on the schema
- **No integer IDs** — all PKs are UUIDs; no sequential integers that could reveal insertion order.
- **No `AUTOINCREMENT`** — there is no integer sequence anywhere.
- **No `DEFAULT CURRENT_TIMESTAMP`** — `created_at` is always set explicitly as an integer Unix epoch.
- **No `datetime('now')`** anywhere in SQL.
- **Seeder UUIDs are deterministic** — `uuid5` with seed-derived names → same seed = same UUIDs for all pre-seeded rows.

---

## 4. Seeding: Requirements and API

### 4.1 Design principle: the shop knows nothing about tasks

The shop's seeder is a **generic, data-driven system**. It knows nothing about which tasks the gym will run. The gym layer is responsible for passing a `SeedConfig` that declares what the catalog must contain. The shop fulfills the request deterministically and raises an error if it cannot (e.g. vocabulary is too small).

This separation means:
- Adding a new task does not require modifying the shop
- The same shop image can serve any gym task
- The seeder is independently testable without any gym code

### 4.2 Static vocabulary (baked into the shop)

The shop ships with a **static vocabulary file** (`shop/vocab.py`) — a large pre-compiled list of realistic product definitions across a fixed set of categories. This file is never modified at runtime; it is the source material from which the seeder draws.

**Vocabulary size (production):** ≥ 200 product definitions across exactly 10 category slots. **For the initial implementation**, the developer ships a **stub vocabulary** with 5 categories × 8 products each plus 5 sample addresses — exactly enough to run `DEFAULT_SEED_CONFIG` without errors. The full 10-category × 20-product vocabulary will be authored separately and dropped in as a replacement — no code changes required, only `vocab.py` content.

| Category slot | Example products |
|---|---|
| Electronics | USB-C cable, Bluetooth speaker, laptop stand, webcam, power bank, ... |
| Books | fiction novels, textbooks, cookbooks, travel guides, ... |
| Clothing | t-shirts, jeans, running shoes, winter jacket, ... |
| Home | kitchen scales, desk lamp, coffee maker, throw pillow, ... |
| Sports | yoga mat, resistance bands, water bottle, jump rope, ... |
| Toys | building blocks, puzzle, remote-control car, ... |
| Beauty | face wash, moisturizer, lip balm, ... |
| Garden | plant pot, pruning shears, watering can, ... |
| Food | olive oil, hot sauce, instant coffee, ... |
| Automotive | phone mount, car charger, microfiber cloth, ... |

Each entry in the vocabulary defines: `name`, `description`, `price_range: (low, high)`. SKUs and exact prices are derived from the seed at runtime, not stored in the vocabulary.

The vocabulary also contains a static **`ADDRESSES`** list of 20 realistic US shipping addresses used by the seeder when generating filler order shipping addresses. Format: plain strings, e.g. `"742 Evergreen Terrace, Springfield, IL 62704"`.

**File:** `shop/vocab.py` — a plain Python module baked into the Docker image. No JSON/YAML parsing needed.

**Format:**

```python
# shop/vocab.py
# STUB implementation (5 categories × 8 products, 5 addresses).
# Sized to match DEFAULT_SEED_CONFIG (n_categories=5, n_products_per_category=8).
# Replace with full vocabulary (10 categories × ≥20 products, 20 addresses) separately.

VOCAB: dict[str, list[dict]] = {
    "Electronics": [
        {"name": "USB-C Charging Cable 2m",      "description": "Braided USB-C to USB-C cable, 2 metres, supports fast charging up to 60W.",               "price_range": (8.99,  29.99)},
        {"name": "Bluetooth Portable Speaker",    "description": "Compact wireless speaker, 8-hour battery, IPX5 water resistance.",                        "price_range": (24.99, 89.99)},
        {"name": "Laptop Stand Adjustable",       "description": "Aluminium laptop stand, adjustable height 15–45 cm, foldable.",                           "price_range": (19.99, 59.99)},
        {"name": "Wireless Keyboard Compact",     "description": "2.4 GHz wireless keyboard, scissor switches, USB nano-receiver included.",                 "price_range": (29.99, 79.99)},
        {"name": "Webcam 1080p HD",               "description": "Full HD webcam with built-in microphone and auto-focus, plug-and-play USB.",               "price_range": (39.99, 99.99)},
        {"name": "Power Bank 20000 mAh",          "description": "20 000 mAh portable charger, dual USB-A + USB-C output, 18 W fast charge.",               "price_range": (29.99, 69.99)},
        {"name": "Noise-Cancelling Earbuds",      "description": "True wireless earbuds with active noise cancellation and 24-hour total battery life.",     "price_range": (49.99, 149.99)},
        {"name": "USB Hub 7-Port",                "description": "Powered 7-port USB 3.0 hub with individual on/off switches and 5 V / 4 A adapter.",        "price_range": (19.99, 49.99)},
    ],
    "Books": [
        {"name": "The Pragmatic Programmer",                      "description": "Classic software engineering guide covering craftsmanship and best practices.",                          "price_range": (14.99, 39.99)},
        {"name": "Clean Code",                                    "description": "A handbook of agile software craftsmanship by Robert C. Martin.",                                        "price_range": (12.99, 34.99)},
        {"name": "Design Patterns",                               "description": "Elements of reusable object-oriented software — the Gang of Four book.",                                "price_range": (29.99, 54.99)},
        {"name": "The Mythical Man-Month",                        "description": "Essays on software engineering and project management by Frederick P. Brooks Jr.",                       "price_range": (12.99, 29.99)},
        {"name": "Structure and Interpretation of Computer Programs", "description": "MIT's foundational CS text covering abstraction, recursion, and interpreters.",                    "price_range": (49.99, 89.99)},
        {"name": "Introduction to Algorithms",                    "description": "Comprehensive algorithms textbook (CLRS), covering sorting, graphs, and complexity.",                   "price_range": (69.99, 119.99)},
        {"name": "You Don't Know JS",                             "description": "Deep dive series into JavaScript's core mechanisms: scope, closures, async, and types.",               "price_range": (9.99,  24.99)},
        {"name": "Refactoring",                                   "description": "Improving the design of existing code by Martin Fowler, with catalog of refactoring techniques.",       "price_range": (29.99, 54.99)},
    ],
    "Clothing": [
        {"name": "Cotton T-Shirt Classic",        "description": "100% ring-spun cotton crew-neck tee, pre-shrunk, available in 8 colours.",                 "price_range": (9.99,  24.99)},
        {"name": "Slim-Fit Jeans",                "description": "Mid-rise slim-fit denim jeans, 98% cotton / 2% elastane, 5-pocket styling.",               "price_range": (29.99, 79.99)},
        {"name": "Running Shoes Lightweight",     "description": "Breathable mesh upper, cushioned midsole, suitable for road running up to 10 km.",         "price_range": (49.99, 129.99)},
        {"name": "Hooded Sweatshirt",             "description": "80% cotton / 20% polyester fleece hoodie with kangaroo pocket and adjustable drawstring.",  "price_range": (24.99, 59.99)},
        {"name": "Winter Jacket Insulated",       "description": "Water-resistant outer shell with 200 g synthetic insulation, zip-off hood.",               "price_range": (79.99, 199.99)},
        {"name": "Canvas Sneakers Low-Top",       "description": "Classic low-top canvas sneakers with vulcanised rubber sole, unisex sizing.",              "price_range": (19.99, 49.99)},
        {"name": "Athletic Shorts 7-Inch",        "description": "Quick-dry polyester athletic shorts with 7-inch inseam, inner brief, and zip pocket.",      "price_range": (14.99, 34.99)},
        {"name": "Merino Wool Socks",             "description": "Fine merino wool crew socks, moisture-wicking, anti-odour, reinforced heel and toe.",       "price_range": (9.99,  24.99)},
    ],
    "Home": [
        {"name": "Desk Lamp LED",                 "description": "Adjustable LED desk lamp with 3 colour temperatures and USB charging port.",                "price_range": (18.99, 49.99)},
        {"name": "Kitchen Scale Digital",         "description": "Digital kitchen scale, 5 kg capacity, 1 g precision, tare function.",                      "price_range": (9.99,  29.99)},
        {"name": "Coffee Maker Drip",             "description": "12-cup drip coffee maker with programmable timer and keep-warm plate.",                     "price_range": (29.99, 79.99)},
        {"name": "Throw Pillow Set 2-Pack",       "description": "Set of two 18×18-inch throw pillows with removable, machine-washable covers.",             "price_range": (19.99, 44.99)},
        {"name": "Bamboo Cutting Board",          "description": "Extra-large bamboo cutting board with juice groove and non-slip feet, 45×30 cm.",           "price_range": (14.99, 39.99)},
        {"name": "Stainless Steel Water Bottle",  "description": "500 ml double-wall vacuum-insulated bottle, keeps cold 24 h / hot 12 h, leak-proof lid.",   "price_range": (14.99, 34.99)},
        {"name": "Blackout Curtains 2-Panel",     "description": "Room-darkening blackout curtain panels, 52×84-inch, energy-saving, grommet top.",          "price_range": (24.99, 59.99)},
        {"name": "Air Purifier Compact",          "description": "True HEPA air purifier for rooms up to 20 m², 3-speed fan, whisper-quiet at 25 dB.",        "price_range": (49.99, 119.99)},
    ],
    "Sports": [
        {"name": "Yoga Mat Non-Slip",             "description": "6 mm TPE yoga mat with alignment lines, non-slip surface, includes carry strap.",           "price_range": (19.99, 49.99)},
        {"name": "Resistance Bands Set 5-Pack",   "description": "Five latex resistance bands from extra-light to extra-heavy, with mesh bag.",               "price_range": (9.99,  24.99)},
        {"name": "Jump Rope Speed",               "description": "Adjustable speed rope with ball-bearing handles and steel cable, suitable for double-unders.", "price_range": (9.99, 29.99)},
        {"name": "Adjustable Dumbbell 20 kg",     "description": "Single adjustable dumbbell 2.5–20 kg in 2.5 kg increments, replaces 8 fixed dumbbells.",    "price_range": (89.99, 199.99)},
        {"name": "Foam Roller High-Density",      "description": "33 cm high-density EVA foam roller for myofascial release, smooth surface.",                "price_range": (14.99, 34.99)},
        {"name": "Running Belt Waist",            "description": "Lightweight stretch waist pack, fits phones up to 6.8-inch, reflective strip.",             "price_range": (9.99,  24.99)},
        {"name": "Gym Gloves Padded",             "description": "Half-finger weight-lifting gloves with wrist support and palm padding, unisex.",            "price_range": (9.99,  24.99)},
        {"name": "Pull-Up Bar Doorframe",         "description": "No-screw doorframe pull-up bar, fits 60–90 cm frames, 150 kg max load.",                   "price_range": (19.99, 49.99)},
    ],
    # Full vocabulary (10 categories × ≥20 products) to be added here
}

ADDRESSES: list[str] = [
    "742 Evergreen Terrace, Springfield, IL 62704",
    "1600 Pennsylvania Avenue NW, Washington, DC 20500",
    "350 Fifth Avenue, New York, NY 10118",
    "1 Infinite Loop, Cupertino, CA 95014",
    "233 S Wacker Dr, Chicago, IL 60606",
    # Full list of 20 addresses to be added here
]
```

**How the seeder uses it:**

```python
rng = random.Random(config.seed)

# Take the first n_categories from VOCAB in dictionary declaration order (deterministic, no rng draw)
selected_cats = list(VOCAB.keys())[:config.n_categories]
# Raises ValueError during validation if n_categories > len(VOCAB) or a required_product.category
# is not among the selected categories.

# For each selected category, sample n_products_per_category entries
for cat in selected_cats:
    available = VOCAB[cat]
    if config.n_products_per_category > len(available):
        raise ValueError(
            f"Vocabulary exhausted: requested {config.n_products_per_category} "
            f"products from '{cat}' but only {len(available)} are available"
        )
    selected = rng.sample(available, k=config.n_products_per_category)
    # derive SKU and price from rng + seed
```

If `n_products_per_category > len(VOCAB[category])`, the seeder raises `ValueError("Vocabulary exhausted: ...")`.

### 4.3 SeedConfig: the contract between gym and shop

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class RequiredProduct:
    """
    A product that MUST appear in the catalog with the given category and field overrides.

    `category` is the only required field — it identifies which vocabulary slot to fill.
    All other product fields (sku, name, description, price) are optional overrides
    applied on top of the rng-generated product entry:

        final_product = {**rng_generated_product, **overrides}

    Only the fields present in `overrides` are changed; all others keep their rng values.
    This means overrides are applied AFTER the full rng phase completes, so adding or
    removing overrides never changes the rng-generated filler content for the same seed.

    CLI / JSON representation: category is extracted from the dict; all other keys become overrides.
    Example: {"category": "Electronics", "sku": "SKU-E7421"}
             → category="Electronics", overrides={"sku": "SKU-E7421"}
    """
    category: str           # must match one of the vocabulary category names
    overrides: dict = field(default_factory=dict)
    # Common override keys: "sku", "name", "description", "price"
    # Any key present in the product schema is accepted; unknown keys are ignored.


@dataclass
class RequiredCoupon:
    """
    A coupon that MUST exist in the DB with this exact code and discount.
    """
    code: str                  # e.g. "SAVE10"
    discount_pct: float        # e.g. 10.0
    active: bool = True


@dataclass
class RequiredOrder:
    """
    A pre-existing order that MUST exist after seeding.
    The gym uses this to set up preconditions (e.g. a placed order to cancel).

    Required orders replace the first len(required_orders) slots out of n_filler_orders total
    generated order slots (same pattern as RequiredProduct). Only `status` is overridden; the
    items and shipping address come from the Phase 1 rng draw for that slot.

    Timestamp assignment: order slot 0 gets created_at = BASE_TS (most recent), slot 1 gets
    BASE_TS - 86400, slot 2 gets BASE_TS - 2*86400, etc. Since required_orders replace slots
    0..N-1, the first required_order is always the most recent order in the DB — which is what
    the cancel_recent_order task verifier checks via MAX(created_at) snapshot.

    Items in each slot are drawn from the seeded catalog (1–3 random products, quantity 1–3).
    """
    status: str                        # "placed" or "cancelled"


@dataclass
class SeedConfig:
    """
    Full specification for a deterministic database seed.
    Passed by the gym layer to seed_database(). The shop does not know which task this is for.

    Raises ValueError if:
    - n_categories > len(VOCAB) (not enough category slots in vocabulary)
    - n_products_per_category exceeds available entries for any selected category
    - a required_product.category is not among the first n_categories of VOCAB
    - duplicate SKUs in required_products
    - duplicate codes in required_coupons
    - len(required_orders) > n_filler_orders (more required orders than available slots)
    """
    seed: int
    base_ts: int = 1_716_000_000
    # Fixed integer Unix timestamp used as the anchor for all pre-seeded order created_at values.
    # Must never be derived from time.time() or any wall-clock source — doing so would make
    # resets non-deterministic. 1_716_000_000 corresponds to 2024-05-18 (a safe fixed past
    # anchor). Override per task or test as needed; the same value → the same timestamps.

    # Catalog shape
    n_categories: int = 5              # how many of the 10 category slots to include
    n_products_per_category: int = 8   # how many products per category (from vocabulary)

    # Gym-specific requirements (injected into the catalog deterministically)
    required_products: list[RequiredProduct] = field(default_factory=list)
    required_coupons: list[RequiredCoupon] = field(default_factory=list)
    required_orders: list[RequiredOrder] = field(default_factory=list)

    # Total number of pre-seeded orders. required_orders replace the first len(required_orders)
    # slots; remaining slots are pure filler with rng-assigned status.
    n_filler_orders: int = 3
```

### 4.4 Seeder functions

```python
def seed_database(db_path: str, config: SeedConfig) -> None:
    """
    Wipe and rebuild the database at db_path deterministically.

    The algorithm is split into two phases so that changing required_* fields never
    disturbs the rng-generated filler content for the same seed.

    ── PHASE 1: rng draws (fixed call order, no required_* influence) ──────────────

    1. Validate config (raises ValueError on constraint violations).
    2. rng = random.Random(config.seed)  — single PRNG, instance-based (not global random).
    3. Select n_categories category slots from VOCAB: take list(VOCAB.keys())[:n_categories]
       (deterministic dictionary order — no rng draw for category selection).
    4. For each selected category (in VOCAB declaration order):
         For each of n_products_per_category products (rng.sample() within the category):
           - assign sort_order: global counter, incremented once per product across all categories
             (e.g. first product of first category = 0, second product = 1, ..., continuing
             across category boundaries — the listing page shows all categories together)
           - derive rng-SKU: "SKU-" + sha256(f"{seed}:{cat_idx}:{prod_idx}".encode())
             .hexdigest()[:5].upper()  where cat_idx and prod_idx are 0-based indices
             into the selected category list and the selected product list respectively
           - rng: derive price: round(rng.uniform(vocab_low, vocab_high), 2)
    5. For each order slot i in range(n_filler_orders):
         - rng: pick shipping address: rng.choice(ADDRESSES)
         - rng: pick k products: rng.sample(catalog, k=rng.randint(1, 3))
         - rng: pick quantity per item: rng.randint(1, 3) per product
         Items snapshot (sku, name, price) is taken from the rng-generated catalog at this point,
         BEFORE any required_product overrides — this is intentional. order_items.sku is a
         denormalized snapshot "at time of order"; it may differ from products.sku after overrides.
         All slots are initially treated as filler (coupon_code=NULL, discount_pct=0.0, total=subtotal).
         The first len(required_orders) slots will have their status replaced in Phase 2.

    ── PHASE 2: required_* injection (no rng calls) ────────────────────────────────

    6. For each RequiredProduct (processed in declaration order): find the rng-generated
       product slot in the specified category at index equal to its 0-based position among all
       RequiredProducts sharing that category (e.g. first "Electronics" required product → slot 0,
       second "Electronics" required product → slot 1, etc.), then apply overrides:
         final_product = {**rng_generated_product, **required_product.overrides}
       Only the product row in `products` is patched — order_items snapshots drawn in Phase 1
       are unchanged.
    7. Insert required_coupons verbatim into the coupons table.
       No filler coupons are generated.
    8. For each order slot i in range(n_filler_orders):
         - created_at = BASE_TS - i * 86400  (slot 0 is most recent, slot 1 is one day older, etc.)
         - status = required_orders[i].status  if i < len(required_orders),  else rng-drawn status
       Insert all n_filler_orders orders with their Phase 1 items, addresses, and Phase 2 status/ts.
       Write ('next_order_ts', str(BASE_TS + 3600)) to shop_meta.

    ── COMMIT ──────────────────────────────────────────────────────────────────────

    9. Write everything in a single SQLite transaction — either the full DB is seeded or nothing.
    10. Must complete in < 3 seconds for any config with n_products_per_category * n_categories <= 200.

    Truncates the JSONL event log file (same path, .jsonl suffix) at the start of seeding
    so each episode begins with an empty log.
    """
    ...


def get_db_snapshot(db_path: str) -> dict:
    """
    Return a read-only snapshot of the current DB state.
    Callable directly by verifiers (no HTTP, no Flask process required).

    Returns:
        {
            "products": [{"id", "sku", "name", "category", "price"}, ...],
            "coupons":  [{"id", "code", "discount_pct", "active"}, ...],
            "orders":   [{"id", "created_at", "status", "shipping_address",
                          "coupon_code", "discount_pct", "subtotal", "total"}, ...],
            "order_items": [{"id", "order_id", "product_id", "sku", "name",
                             "quantity", "unit_price"}, ...],
        }
    """
    ...
```

CLI interface:
```bash
# Basic usage (gym tasks call this via subprocess or HTTP /api/reset)
python seed.py --db /path/to/shop.db --seed 42

# With shape parameters
python seed.py --db /path/to/shop.db --seed 42 --n-categories 5 --n-products-per-category 8

# With required items (JSON)
python seed.py --db /path/to/shop.db --seed 42 \
  --require-product '{"sku": "SKU-E7421", "category": "Electronics"}' \
  --require-coupon  '{"code": "SAVE10", "discount_pct": 10.0}' \
  --require-order   '{"status": "placed"}'
```

### 4.5 How the gym layer uses SeedConfig

The gym defines its own default config per task. Example for the three tasks:

```python
# In gym_env/tasks/base.py — default config used by all three tasks
DEFAULT_SEED_CONFIG = SeedConfig(
    seed=0,            # overridden by env.reset(seed=N)
    base_ts=1_716_000_000,
    n_categories=5,
    n_products_per_category=8,
    required_products=[
        RequiredProduct(category="Electronics", overrides={"sku": "SKU-E7421"}),
    ],
    required_coupons=[
        RequiredCoupon(code="SAVE10", discount_pct=10.0, active=True),
    ],
    required_orders=[
        RequiredOrder(status="placed"),   # slot 0 → gets BASE_TS → most recent order in DB
    ],
    n_filler_orders=3,
)
```

Each task calls `seed_database(db_path, replace(DEFAULT_SEED_CONFIG, seed=env_seed))` on reset. A new task that needs a different SKU or a different coupon just passes a different `SeedConfig` — no shop code changes.

### 4.6 Determinism of derived attributes

| Attribute | How derived |
|---|---|
| **SKU format** | `"SKU-" + hashlib.sha256(f"{seed}:{cat_idx}:{prod_idx}".encode()).hexdigest()[:5].upper()` — deterministic, immune to `PYTHONHASHSEED` |
| **Price** | `round(rng.uniform(vocab_low, vocab_high), 2)` |
| **sort_order** | Global sequential integer across all products in rng draw order (0, 1, 2, …, continuing across category boundaries); listing page `ORDER BY sort_order ASC` by default |
| **Product name / description** | Selected from vocabulary entry (static, not generated) |
| **Required product slot** | Phase 1 picks a vocab entry for the required category via rng (same draw sequence as any other product). Phase 2 applies `{**rng_product, **overrides}` — only fields present in `overrides` are changed; name/description/price keep their rng values unless explicitly overridden. Filler and required order_items snapshots are unaffected (drawn in Phase 1 before overrides). |
| **Filler order shipping_address** | `rng.choice(ADDRESSES)` — drawn from the static `ADDRESSES` vocabulary list (20 entries); no `time.time()` involved |
| **Filler order coupon** | None — filler orders always have `coupon_code=NULL`, `discount_pct=0.0`, `total=subtotal` |
| **Order created_at (pre-seeded)** | `BASE_TS = config.base_ts` (always a fixed integer — never `time.time()`); order slot `i` gets `BASE_TS - i * 86400` (slot 0 = most recent). required_orders replace slots 0..N-1 so the first required_order is always the most recent order in the DB. |
| **Order created_at (agent-created)** | Read `next_order_ts` from `shop_meta`, use it as `created_at`, then write `next_order_ts + 3600` back — monotonically increasing virtual clock, no `time.time()` in DB state |
| **Order items** | 1–3 products picked by `rng.sample(catalog, k)` with `rng.randint(1, 3)` quantity each |
| **Listing page default order** | `ORDER BY sort_order ASC` |
| **Related products** | Collect same-category products (excluding current) sorted by `sort_order`, using the cyclic formula `(i+1)%n, (i+2)%n, (i+3)%n`. If fewer than 3 are available in the same category, fill remaining slots from other categories sorted by `sort_order`. Always shows exactly 3 links (or fewer only if the entire catalog has fewer than 4 products). |

---

## 5. Internal API (`/api/`)

These endpoints are **not** exposed to the agent. They are called by the gym's verifiers and reset mechanism.

### `GET /api/db-state`
Returns the full current state of the database as JSON:
```json
{
  "products": [
    {"id": "a1b2c3d4-...", "sku": "SKU-E7421", "name": "...", "category": "Electronics", "price": 29.99}
  ],
  "coupons": [
    {"id": "e5f6a7b8-...", "code": "SAVE10", "discount_pct": 10.0, "active": 1}
  ],
  "orders": [
    {"id": "c9d0e1f2-...", "created_at": 1700001000, "status": "placed", "shipping_address": "...", "coupon_code": null, "discount_pct": 0.0, "subtotal": 59.98, "total": 59.98}
  ],
  "order_items": [
    {"id": "f3a4b5c6-...", "order_id": "c9d0e1f2-...", "product_id": "a1b2c3d4-...", "sku": "SKU-B0012", "name": "...", "quantity": 2, "unit_price": 29.99}
  ]
}
```

### `POST /api/reset`
Accepts a JSON body containing the full `SeedConfig` serialized as a dict. Calls `seed_database(db_path, config)` and returns:
```json
{"status": "ok", "seed": 42, "elapsed_ms": 87}
```

Request body example:
```json
{
  "seed": 42,
  "base_ts": 1716000000,
  "n_categories": 5,
  "n_products_per_category": 8,
  "required_products": [{"sku": "SKU-E7421", "category": "Electronics"}],
  "required_coupons":  [{"code": "SAVE10", "discount_pct": 10.0, "active": true}],
  "required_orders":   [{"status": "placed"}],
  "n_filler_orders": 3
}
```

All fields except `seed` are optional and fall back to `SeedConfig` defaults. For convenience, a bare `?seed=N` query parameter (no body) is also accepted and is equivalent to `SeedConfig(seed=N)` with all defaults — this allows quick manual resets from curl without constructing JSON.

Used by `ShopEnv.reset()` as an alternative to running the seeder as a subprocess.

### `GET /api/health`
Returns `{"status": "ok"}`. Used by gym startup to poll until the shop is ready.

---

## 6. Realistic UI Requirements (agent difficulty)

The UI should not be trivially navigable. An agent must work to find the right elements:

- **Header (on every page):** site name/logo, category navigation links (one per category), cart icon with item count badge (e.g. "Cart (3)")
- **Listing page:** product cards with name, price, category badge; a filter sidebar or filter bar with category checkboxes or links; a sort dropdown; pagination controls ("Previous / Page 2 of 4 / Next")
- **Product detail page:** breadcrumb (Home > Electronics > Product Name), product name, SKU, price, quantity input (number field, default 1), "Add to Cart" button, "Related products" section with 3 product links (cyclic by sort_order within same category, excluding current product)
- **Cart page:** table of line items (name, quantity, unit price, line total), coupon code input + "Apply Coupon" button, error message if coupon is invalid or inactive, discount line (shown only after valid coupon applied), subtotal, discount amount, total, "Proceed to Checkout" button, "Continue Shopping" link
- **Checkout page:** form fields with US address validation:
  - **Name** — free text, required, non-empty
  - **Street Address** — free text, required, non-empty
  - **City** — free text, required, non-empty
  - **State** — `<select>` dropdown with all 50 US states + DC as `<option>` elements (abbreviations: AL, AK, AZ, ... WY); no free-text entry
  - **ZIP Code** — validated server-side as exactly 5 digits (`^\d{5}$`); error shown inline if invalid
  - Order summary (products, quantities, prices, total); "Place Order" button
  - Validation errors re-render the form with the entered values preserved and error messages next to invalid fields
- **`/order/<id>` page (single template):** order ID, date, status badge, line items table, shipping address, total. "Cancel Order" button shown only if `status='placed'`; after cancellation the page reloads with updated status and button is gone. When arriving via redirect from checkout (`?confirmed=1` query param set by the checkout POST handler), a "Order placed!" confirmation banner is shown at the top; otherwise the page shows plain order detail. A "Continue shopping" link is always present.
- **Orders list page (`/orders`):** table with columns: Order ID, Date, Status (badge: green=placed, grey=cancelled), Total; link to detail page per row; sorted by `created_at` descending (most recent first)

---

## 7. Docker Lifecycle and Bootstrap

### 7.1 Statelessness requirement

The Flask application must be **fully stateless** — it holds no in-memory state between requests or across restarts. All persistent state lives in the SQLite file. The container can die and restart at any point without data loss, as long as the DB file is mounted.

Specifically:
- No global dicts, caches, or counters in Python process memory
- No server-side session storage (filesystem or memory) — the cart is stored in `cart_items` DB table, keyed by `session_id` cookie
- No background threads or queues that accumulate state
- The JSONL event log is opened in append mode (`"a"`) per request — no persistent file handle

### 7.2 Startup behavior

On container start, the Flask app runs an initialization step before accepting requests:

```python
def init_db(db_path: str) -> None:
    """
    Idempotent schema initialization.
    Safe to call on an empty file, a pre-seeded file, or a file from a previous episode.
    Never drops tables or deletes data.
    """
    with sqlite3.connect(db_path) as conn:
        conn.executescript(CREATE_TABLES_SQL)   # all CREATE TABLE IF NOT EXISTS
```

Startup cases:
| File state | Behavior |
|---|---|
| Empty file (0 bytes) | Schema created; no rows; gym must call `/api/reset` before first episode |
| Non-empty, seeded | Schema confirmed idempotently; existing data preserved; ready to serve |
| Non-empty, mid-episode | Same as above; agent can continue from where it left off (or gym resets) |

The `GET /api/health` endpoint returns `{"status": "ok"}` only after `init_db()` completes. The gym polls this endpoint after container start before calling `reset()`.

### 7.3 File pre-creation requirement

Docker bind-mounting a **file** (not a directory) requires the file to exist on the host before `docker run`. The gym infrastructure must create both files before starting the container:

```bash
# Done by gym_env startup or docker-compose entrypoint
mkdir -p /tmp/gym_1
touch /tmp/gym_1/shop.db
touch /tmp/gym_1/shop.jsonl

docker run \
  -v /tmp/gym_1/shop.db:/app/shop.db \
  -v /tmp/gym_1/shop.jsonl:/app/shop.jsonl \
  -e DATABASE_PATH=/app/shop.db \
  -e LOG_PATH=/app/shop.jsonl \
  -p 5001:5000 \
  shop:latest
```

The `docker-compose.yml` handles this via a `volumes:` block with host paths. The parallel demo script creates the files before starting containers or subprocesses.

### 7.4 Implementation stack

To keep the image small and dependencies minimal:

| Component | Choice | Notes |
|---|---|---|
| Web framework | Flask | Routing, Jinja2 templates, `after_request` hooks |
| Database | `sqlite3` (stdlib) | No SQLAlchemy, no ORM |
| UUIDs | `uuid` (stdlib) | `uuid4()` for app-created IDs, `uuid5()` for seeder IDs |
| Seeding PRNG | `random.Random` (stdlib) | Instance-based, not global `random` module |
| JSON | `json` (stdlib) | For event log and API responses |
| HTTP server | Flask dev server (single-threaded) | Sufficient for single-agent workload; swap to `gunicorn` for 500+ parallel instances |

No external Python dependencies beyond Flask and its direct dependencies (Werkzeug, Jinja2, Click, MarkupSafe, itsdangerous).

---

## 8. Explicitly Out of Scope (this exercise)

- User login, registration, or session-based authentication
- Payment forms, card numbers, or payment provider integration
- CSS styling beyond inline or minimal structural HTML
- Product images
- Email notifications
- Admin panel
- Multi-currency support
- Tax calculation
- Shipping cost calculation (shipping is free, total = subtotal minus discount)
- Stock tracking — `products` has no `stock` column; "Add to Cart" always succeeds if the product exists; no "Out of stock" UI or enforcement (none of the three tasks require it)

---

## 9. Determinism Checklist

The following must be verified before the shop is considered complete:

**Seeder correctness**
- [ ] `seed_database(db_path, config)` called twice with the same `config` produces bitwise-identical DB files
- [ ] All products, coupons, orders, and order_items use UUID TEXT primary keys (no integer IDs anywhere)
- [ ] Seeder-assigned UUIDs are deterministic: `uuid5(NAMESPACE_DNS, f"table:{seed}:{index}")` produces the same value on every run
- [ ] No SQL statement contains `CURRENT_TIMESTAMP`, `datetime('now')`, or `strftime`
- [ ] No Python code calls `time.time()`, `datetime.now()`, or `uuid.uuid4()` in the seeder (only `uuid5`)
- [ ] A `required_product` with a given `sku` and `category` always appears in the DB with exactly that SKU, in a product row from the correct category
- [ ] A `required_coupon` with a given `code` and `discount_pct` always appears in the coupons table with `active=1` (when `active=True`)
- [ ] Order slot 0 always gets `created_at = BASE_TS`; slot `i` gets `BASE_TS - i * 86400` — stable across resets with same seed
- [ ] The first `RequiredOrder` in the list always occupies slot 0 (most recent `created_at` in the DB)
- [ ] Agent-created orders receive `created_at` from `shop_meta.next_order_ts` (virtual clock), which is always > BASE_TS (i.e., always newer than any pre-seeded order)
- [ ] `SeedConfig` validation raises `ValueError` when: duplicate SKUs, duplicate coupon codes, `len(required_orders) > n_filler_orders`, or a required_product.category not among the first `n_categories` of VOCAB
- [ ] `SeedConfig` validation raises `ValueError` when `n_products_per_category` exceeds available vocabulary entries for a given category

**Page determinism**
- [ ] Listing page default order (`ORDER BY sort_order ASC`) is stable across resets with the same seed
- [ ] Related products are the same on every reset for the same seed and product ID
- [ ] Category navigation links in the header reflect exactly the categories present in the seeded catalog (no hardcoded list)

---

## 10. Event Log

### 10.1 Purpose

The shop writes a structured event log of all server-visible interactions to a JSONL file alongside the SQLite database. This enables post-hoc analysis of agent behaviour: how many attempts were made before success, which pages were visited, what form values were submitted, where the agent got stuck.

The log captures what the **server sees** — HTTP requests and their outcomes. It does not capture cursor position, scroll depth, or what was rendered on screen (that is the gym layer's responsibility via screenshots and accessibility tree).

### 10.2 File location

```
Path(db_path).with_suffix(".jsonl")
```

Examples:
- DB: `/tmp/gym_1/shop.db` → Log: `/tmp/gym_1/shop.jsonl`
- DB: `/data/instance.db` → Log: `/data/instance.jsonl`

The log file is **appended** on every request. It is **truncated** (not deleted) when `seed_database()` is called, so each episode starts with a clean log. Log entries from the seeder itself (e.g. the reset API call) are not written.

### 10.3 Event record format

Each line is a JSON object (no trailing comma, newline-terminated):

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

Fields:
- `ts` — Unix timestamp with millisecond precision (`time.time()` — this is the one place where wall clock time is allowed, since it is for logging only, not for DB state)
- `session_id` — the session UUID cookie value (identifies which parallel instance/agent this came from)
- `method` — HTTP method (`GET` or `POST`)
- `path` — URL path (without query string for GETs)
- `params` — for GET: query parameters as dict; for POST: form fields submitted (all values, including failed ones)
- `result` — outcome dict; shape varies by event type (see 10.4)

### 10.4 Event types and result shapes

| Event | method + path | `params` | `result` |
|---|---|---|---|
| Browse listing | GET `/` | `{category, q, sort, page}` | `{n_products, n_pages, current_page}` |
| View product | GET `/product/<id>` | `{product_id}` | `{sku, name, category, price}` |
| View cart | GET `/cart` | `{}` | `{n_items, subtotal, coupon_code, total}` |
| Add to cart | POST `/cart/add` | `{product_id, quantity}` | `{status: ok\|not_found, cart_total_items}` |
| Update cart | POST `/cart/update` | `{product_id, quantity}` | `{status: ok\|not_found}` |
| Remove from cart | POST `/cart/remove` | `{product_id}` | `{status: ok\|not_found}` |
| Apply coupon | POST `/cart/coupon` | `{code}` | `{status: ok\|removed\|invalid_code\|inactive, discount_pct?, discount_amount?}` — `removed` when code is empty |
| View checkout | GET `/checkout` | `{}` | `{n_items, subtotal, total}` |
| Submit checkout | POST `/checkout` | `{name, street, city, state, zip, ...}` | `{status: ok\|validation_error, order_id?: "...", errors?: {field: msg}}` |
| View order | GET `/order/<id>` | `{order_id}` | `{status, total, n_items}` |
| View orders list | GET `/orders` | `{}` | `{n_orders}` |
| Cancel order | POST `/orders/<id>/cancel` | `{order_id}` | `{status: ok\|already_cancelled\|not_found}` |

### 10.5 Implementation notes

- Logging is done via a Flask `after_request` hook (or per-route, if cleaner) — it fires after the response is assembled so the result is known.
- The log writer opens the file in append mode (`"a"`) per request; no persistent file handle.
- Log writes must not block the response — if the log write fails (e.g. disk full), log the error to stderr and continue; do not return 500 to the agent.
- The `params` dict for POST `/checkout` includes all form fields **as submitted**, even if validation failed. This is intentional — we want to see what the agent tried.
