# Web Gym for RL Agents — Project Record

This file is the living design document for the project. It captures decisions as they are made, the reasoning behind them, open questions, and what's left to build.

---

## What this is

A minimal web gym: a sandboxed, resettable e-commerce browser environment that an RL trainer can drive to teach a policy how to click, type, and navigate. Customers (AI labs, applied teams) spin up many parallel instances and run rollouts against it.

---

## Repository layout (planned)

```
gym/
├── shop/                        # The e-commerce site
│   ├── app.py                   # Flask application (all routes)
│   ├── db.py                    # sqlite3 schema + helpers
│   ├── seed.py                  # CLI: python seed.py --db /path/to.db --seed 42
│   ├── templates/               # Jinja2 HTML templates
│   ├── requirements.txt
│   └── Dockerfile
├── gym_env/
│   ├── __init__.py
│   ├── env.py                   # ShopEnv(gymnasium.Env) — reset / step / close
│   ├── actions.py               # Action dispatcher (click, type, scroll, navigate)
│   ├── observation.py           # Snapshot builder (url, DOM, a11y tree, screenshot)
│   └── tasks/
│       ├── base.py              # AbstractTask: setup() / verify() / teardown()
│       ├── buy_cheapest.py      # BuyCheapestInCategoryTask
│       ├── apply_coupon.py      # ApplyCouponWithQuantityTask
│       └── cancel_order.py      # CancelRecentOrderTask
├── scripts/
│   └── parallel_demo.py         # 4+ concurrent envs, random + scripted policies
├── tests/
│   ├── test_verifiers.py        # Unit tests: verifiers against seeded DB
│   └── test_env.py              # Integration smoke test: reset + step
├── docker-compose.yml           # Spins up 4 shop instances on ports 5001–5004
├── doc/
│   └── writeup.md               # Final submission writeup
├── TASK.md                      # Original task specification
├── SHOP_REQUIREMENTS.md         # Shop feature requirements
├── SHOP_SEARCH.md               # Open-source shop research notes
└── README.md                    # This file
```

---

## Architecture

```
Host machine
│
├── /tmp/gym_1/shop.db   ← SQLite DB files live on the host (artifacts, survive container death)
├── /tmp/gym_2/shop.db
├── /tmp/gym_3/shop.db
└── /tmp/gym_4/shop.db
│
├── Docker container "shop_1"  (port 5001)
│     Flask app (shop/app.py)
│     /app/shop.db → volume mount: /tmp/gym_1/shop.db
│
├── Docker container "shop_2"  (port 5002)  ... and so on
│
└── gym_env/  (runs on host, outside Docker)
      ShopEnv(instance_id=1)
        ├── Playwright Chromium → http://localhost:5001   (browser control)
        ├── task.verify()       → sqlite3.connect("/tmp/gym_1/shop.db")  (direct DB read)
        └── env.reset()         → seed.py --db /tmp/gym_1/shop.db --seed 42  (wipe + reseed)
```

**One container = one Flask process = one SQLite file.**
Each parallel env instance gets a unique port and a unique DB path. No shared mutable state.

### Reset flow

```
env.reset(seed=42)
  1. subprocess: python seed.py --db /tmp/gym_1/shop.db --seed 42   (~100ms)
  2. page.goto("http://localhost:5001/")                              (~200ms first load)
  3. task.setup(page)   # snapshot pre-state, navigate to start URL
  4. return _observe()
```

Target: < 3 seconds total. Seeder does a single SQLite transaction (DROP + CREATE + INSERT).

### Observation

```python
obs = {
    "url":        str,     # page.url
    "dom":        str,     # page.content() — full HTML
    "axtree":     dict,    # page.accessibility.snapshot() — semantic a11y tree
    "screenshot": bytes,   # page.screenshot(type="png")
}
```

### Action space

Typed dict dispatched to Playwright:

```python
{"type": "click",    "x": 320,  "y": 240}
{"type": "type",     "text": "hello"}
{"type": "scroll",   "x": 450,  "y": 300, "delta_y": 300}
{"type": "navigate", "url": "http://localhost:5001/orders"}
```

Coordinate-based clicking — no BID injection needed, works universally with any HTML.

---

## Decisions log

### Decision 1 — Shop engine: build from scratch (Flask + sqlite3)

**Date:** 2026-05-19  
**Options considered:**
- Fork `alankrantas/svelteapp-typescript-go` (Go + SvelteKit, archived Sep 2025)
- Fork `shurco/mycart` (Go + Svelte, active, ~349★)
- Vendure (TypeScript/NestJS/GraphQL, GPL-3.0, 8k★)
- Custom Flask app

**Decision:** Custom Flask app.

**Reasoning:**
1. Every candidate is missing the same set of testing-harness features: integer-seeded reset, `/api/db-state`, no-auth checkout, configurable DB path via env var. These must be added regardless of starting point.
2. `svelteapp-typescript-go` is archived and adds Go to the stack (gym_env is Python). Forking it means learning a codebase to add 8+ missing features — not faster than 500 lines of Python.
3. `shurco/mycart` requires 3-5 days of auth stripping. Not worth it.
4. Custom Flask gives full control over schema (verifiers are trivial), reset is a single transaction (~100ms), one language across the whole project.
5. The researcher's own recommendation: *"build a 300–400 LOC Flask + SQLAlchemy + Jinja app from scratch. You will spend less total time."*

**Tradeoffs accepted:**
- Less "visual realism" than a polished SPA — compensated by realistic page structure (navigation, pagination, multi-step checkout, coupon field, order list) that creates genuine agent difficulty.
- We own all the code — no upstream for bug fixes, but also no upstream breaking changes.

---

### Decision 2 — SQLite on host, volume-mounted into Docker

**Date:** 2026-05-19  
**Options considered:**
- SQLite inside the container only (lose it when container dies)
- SQLite on host, mounted into container (Variant B)
- HTTP API for verifiers (instead of direct file access)

**Decision:** SQLite file lives on host, mounted into container via Docker volume. Verifiers read the file directly with `sqlite3.connect(path)`.

**Reasoning:**
1. DB file survives container restart/death — can inspect, diff, reuse as artifact.
2. Direct file read in verifier: 0ms latency, no HTTP error surface, no network dependency.
3. `DATABASE_PATH` env var in container points to the mounted file. `seed.py` on host writes to the same file.
4. Each parallel instance gets a unique host path (`/tmp/gym_N/shop.db`) → zero shared state.

**Tradeoffs accepted:**
- Verifier code has a filesystem dependency (path must exist on host). Mitigated: path is injected at env construction time and validated in `__init__`.

---

### Decision 3 — Parallelism: one Docker container per instance

**Date:** 2026-05-19  
**Options considered:**
- One container, multiple Flask processes inside (supervisor/gunicorn workers)
- One subprocess per instance (no Docker for dev)
- One Docker container per instance

**Decision:** One Docker container per instance in production (`docker-compose.yml` with 4 services). For the parallel demo script, Flask runs as subprocesses (no Docker) for speed.

**Reasoning:**
1. True isolation: one container crash doesn't affect others.
2. Restart/reseed one instance without touching others.
3. `docker-compose.yml` with 4 services is the deployable artifact that documents the approach.
4. For the demo script, subprocess is faster to start (~100ms vs ~2-5s for Docker) and sufficient for demonstrating parallelism on a local machine.

---

## Tasks

### Task 1 — `buy_cheapest_in_category`
*"Buy the cheapest item in the 'Electronics' category and ship it to `123 Main St, Springfield, IL 62701`."*

**Verifier logic:**
- New order appeared in DB since `setup()` snapshot
- `unit_price = MIN(price) WHERE category='Electronics'` in that order
- `shipping_address` contains `123 Main St`

### Task 2 — `apply_coupon_with_quantity`
*"Add 2 units of `SKU-E7421` to the cart, apply coupon `SAVE10`, and complete checkout."*

**Verifier logic:**
- New order appeared
- Order has line item: product with `sku='SKU-E7421'`, `quantity=2`
- Order total reflects 10% discount (within float tolerance)

### Task 3 — `cancel_recent_order`
*"Cancel the most recent existing order in the account."*

**Verifier logic:**
- The order that existed at `setup()` time with the most recent `created_at` and `status='placed'`
- Now has `status='cancelled'`

---

### Decision 4 — Checkout address validation: structured US format

**Date:** 2026-05-19  
**Options considered:**
- No validation (accept any string)
- Minimal validation (non-empty fields only)
- Structured US address validation (state dropdown, ZIP regex)

**Decision:** Structured US address validation. State is a `<select>` dropdown with all 50 states + DC. ZIP code must match `^\d{5}$` exactly. Name, Street, City are free text but required (non-empty). Validation is server-side; errors re-render the form with values preserved.

**Reasoning:**  
The task specifies shipping to `123 Main St, Springfield, IL 62701`. If we accept any string without validation, an agent that types "IL 62701" into the ZIP field, or leaves State as free text with a typo, could still get `reward=1.0`. That breaks reward robustness — one of the top evaluation criteria. Structured validation forces the agent to correctly interact with a dropdown (find "IL" option in a 51-item list) and type a valid ZIP. These are realistic friction points on actual e-commerce sites. The verifier checks the stored `shipping_address` string against expected values, so if the form rejects malformed input, the agent cannot bypass it.

**Tradeoffs accepted:**
- The state dropdown is an interactive element that requires `page.select_option()` or coordinate-based selection — slightly more complex for the scripted oracle to handle. Acceptable because the oracle is for demonstration only, not for training.
- US-only scope is a simplification; real shops support international addresses. Fine for this exercise.

---

## Assumptions and intentional restrictions

These are not architectural choices — they are deliberate scope limits for this exercise. A production gym would revisit each of them.

| Topic | Restriction | Reason |
|---|---|---|
| **Geography** | US addresses only (50 states + DC dropdown, 5-digit ZIP) | Keeps address validation tractable; task spec gives a US address |
| **Language** | English-only UI | Single locale keeps templates and product names simple; no i18n needed |
| **Timezones** | All timestamps stored as UTC epoch integers; no timezone conversion | Avoids non-determinism from locale-aware `datetime.now()` |
| **Authentication** | None — all storefront pages are publicly accessible | Task spec explicitly says "skip auth"; auth would add complexity with zero benefit for agent training |
| **Payment** | Checkout places an order without any payment step | Task spec says skip real payments |
| **ID format** | All DB primary keys are UUIDs (not sequential integers) | Prevents agents from shortcutting tasks by sorting IDs instead of reading displayed dates/labels |
| **Event log** | Server-side JSONL log written alongside the SQLite file | Implementation detail; documented in SHOP_REQUIREMENTS.md §9 |
| **CSS/images** | None beyond structural HTML | Task spec: "unstyled HTML is completely fine" |
| **Stock tracking** | No `stock` column; "Add to Cart" always succeeds if product exists; no "Out of stock" UI | None of the three tasks involve stock — adding the column would add complexity with zero benefit |

---

## Open questions / to be decided

- [x] ~~Session handling~~ → **Resolved:** cart stored in `cart_items` DB table keyed by `session_id` UUID4 cookie. No filesystem session state in the container.
- [x] ~~`created_at` determinism~~ → **Resolved:** `base_ts` is a fixed integer constant (`1_716_000_000`, 2024-05-18) stored in `SeedConfig`; never derived from `time.time()`. The seeder generates `n_filler_orders` order slots; slot `i` gets `BASE_TS - i * 86400` (slot 0 is most recent). `required_orders` replace slots 0..N-1, so the first `RequiredOrder` always gets `BASE_TS` (most recent). Agent-created orders use a `next_order_ts` virtual clock in `shop_meta`. No wall-clock source anywhere in the seeder or gym layer.
- [ ] Scripted oracle policy for parallel demo: how much of the checkout flow to hard-code vs. random. **Leaning toward:** fully scripted oracle for `cancel_recent_order` (navigate → click cancel), random walk for the others — demonstrating both that verifiers work correctly and that random policy has near-zero success rate.

---

## What's left to build

- [ ] Shop Flask app (routes, schema, templates, realistic UI)
- [ ] seed.py (deterministic DB seeding)
- [ ] gym_env core (env.py, actions.py, observation.py)
- [ ] Three task classes with DB verifiers
- [ ] Parallel demo script
- [ ] Tests (verifier unit tests + integration smoke test)
- [ ] doc/writeup.md
- [ ] Docker/docker-compose setup

---

## What we're explicitly skipping

Per task specification:
- Real RL training loop (baseline policy only)
- Auth / user accounts / real payments
- Visual styling (CSS)
- Cross-browser support (Chromium only)
- Plugin systems or abstraction layers with only one implementation
