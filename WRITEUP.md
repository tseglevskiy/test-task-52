# ShopGym — Submission Writeup

---

## Architecture

ShopGym is a three-layer stack. The **shop** is a stateless Flask app backed by a single SQLite file, running in Docker (or as a subprocess for the demo). It exposes a public storefront (product listing, cart, checkout, orders) and two internal API endpoints: `POST /api/reset` wipes and re-seeds the database deterministically from a `SeedConfig`, and `GET /api/db-state` returns the full DB as JSON for verifiers. The **gym environment** (`gym_env/`) is a Gymnasium-compatible Python class (`ShopEnv`) that drives a headless Playwright Chromium browser against the shop, calls `env.reset()` to reseed, and delegates reward computation to a pluggable task object. The **task layer** (`tasks/`) contains one file per task; each task declares what the DB must contain before the episode, what goal string the agent receives, and how to verify success by calling `GET /api/db-state`. Parallelism is achieved by giving each env instance its own shop process (or Docker container) on a unique port with a unique DB file — no shared mutable state anywhere.

Each layer has its own README with full API and design details (`gym_env/README.md`, `shop/README.md`, `shop/SEEDING.md`, `shop/DOCKER.md`, `gym_env/TASK.md`).

---

## Decisions

### 1. Deterministic reset: two-phase seeder and a virtual clock

The hardest design problem was making the DB fully deterministic while still letting the gym layer inject task-specific requirements — a known SKU, a specific coupon, a pre-placed order to cancel. The naive approach (draw random data, then patch it) breaks determinism: any change to the required items shifts the RNG sequence and changes everything else. The solution is a strict two-phase algorithm. Phase 1 exhausts all RNG draws with no knowledge of task requirements — products, prices, order items, shipping addresses are all generated in a fixed call order. Phase 2 applies overrides in memory before writing to the DB. Adding or removing a `required_product` never shifts the prices or names of any other product.

SKUs are derived from SHA-256 rather than the RNG, so they don't consume a draw and are immune to `PYTHONHASHSEED`. Timestamps were the other tricky part: `time.time()` can never touch the database, but order dates still need to look realistic and agent-created orders must always appear newer than pre-seeded ones. The solution is a fixed integer anchor (`BASE_TS = 1_716_000_000`, a date in 2024) for pre-seeded orders, and a virtual clock stored in `shop_meta` for agent-created orders — the app reads `next_order_ts`, uses it as `created_at`, then increments it by 3600. No wall-clock calls anywhere in DB-facing code.

### 2. Separation of concerns: shop owns its schema, tasks own nothing

The shop, the gym environment, and the tasks are three separate layers with a strict dependency rule: only the shop knows about its database schema. Tasks and verifiers never open the SQLite file directly — they call `GET /api/db-state`, which returns the full DB as JSON. This means a column rename in the shop breaks nothing outside the shop. It also means task classes only need a `base_url` and can run against any deployment.

The seeder lives inside the shop (`shop/seed.py`) rather than in the gym layer, because it has to know the internal schema to write it. But it exposes a data-driven interface (`SeedConfig`) that the gym layer passes in — the shop fulfills the request without knowing which task it's for. This keeps the shop independently testable and deployable: the same Docker image serves any task, and adding a new task requires zero changes to the shop.

Instance isolation follows the same principle: each env instance gets its own SQLite file on the host, mounted into its own Docker container. The file is the entire state of that instance — it can be copied, diffed, or archived without a running process. No shared mutable state between instances, no connection pooling, no cross-instance session cookies.

### 3. Action space and oracle design: what does an agent actually need?

Writing the scripted oracles forced a concrete answer to the question of what the action space needs to support. The oracles use only `env.step()` — no direct API calls — so every UI interaction has to be expressible as an action. This surfaced two design choices.

First, the primary interaction primitive is `click_by_role` (Playwright's `get_by_role(...).first`) rather than CSS selectors. This mirrors how a real agent would navigate — by semantic meaning, not by fragile paths that break when the HTML changes. The accessibility tree (`obs["axtree"]`, from Playwright's `aria_snapshot()`) is the observation that makes this possible: it exposes the same semantic structure the oracle uses to find elements, at roughly 5× less data than raw HTML.

Second, the oracle for `apply_coupon` needed to find a product by SKU when the product name is random. The solution — click the category nav link, then regex-parse the axtree to find the row containing the SKU and extract the product name link — revealed that the axtree format is the right observation for this kind of structured lookup. A real agent would do the same thing: parse the visible text to find the target, then click it. The action space and observation format were designed together with this use case in mind.

---

## What I'd do with more time

**Richer action space.** The current action space covers the happy path for all three tasks but is missing hover, drag, multi-select, and file upload. A real agent training on the open web would need all of these. The `actions.py` dispatch table makes adding new action types straightforward — each is a ~5-line Playwright call.

**Observation compression.** The screenshot is currently a raw PNG passed as a numpy array. For training at scale, this should be JPEG-compressed or downsampled. The axtree is plain text; a tokenized or embedded representation would be more useful to a transformer policy.

**Episode replay and debugging tools.** The shop already writes a structured JSONL event log of every server-side interaction. What's missing is the tooling to consume it: a way to extract the full sequence of (axtree, screenshot, action, reward) tuples for a given episode and replay or visualise them offline. Right now, debugging a failed episode means re-running it with `run_one.py` and reading the live log — there's no way to go back and inspect what the agent saw at step 3 of a run that finished an hour ago. A lightweight episode recorder in `ShopEnv` that writes observations and actions to a file alongside the JSONL log would make post-hoc analysis much more practical.

**Run a real agent against it.** The gym is built and the oracles prove it's solvable — the natural next step is to plug in an actual LLM-based agent and see what happens. The Gymnasium interface is standard, so any agent that can consume a text observation and emit JSON actions should work without changes to the gym. Watching where a real agent fails (wrong element, wrong quantity, wrong order of steps) would immediately surface which parts of the action space and observation format need improvement.

---

## What I cut, and known issues

**No search in the demo.** The shop has a working `?q=` text search endpoint, but none of the three tasks use it and the oracle doesn't exercise it. It's tested indirectly via the seeder tests but has no dedicated gym task.

**Vocabulary is small.** `vocab.py` ships with 5 categories × 8 products (40 products total). The requirements doc called for 10 categories × 100 products. The seeder raises `ValueError` if you ask for more than the vocabulary has, so this is a hard limit until the vocabulary is expanded. Expanding it requires only replacing `vocab.py` — no code changes.

**No episode timeout enforcement.** `ShopEnv` has a `max_steps` parameter but the random policy in the demo doesn't respect `truncated`. A real training loop should handle `truncated=True` as a failed episode.

**Cart is session-scoped, not reset-scoped.** `POST /api/reset` wipes products, orders, and coupons but does not clear `cart_items` or `cart_meta`. If an agent adds items to the cart and then the env is reset without closing the browser (which would generate a new session cookie), the old cart persists. In practice `env.reset()` navigates to the homepage which starts a fresh Playwright context, so this hasn't caused issues — but it's a latent bug if someone reuses a browser session across resets.

**Oracle for `apply_coupon` is fragile on SKU regex.** The oracle parses `obs["axtree"]` with a regex to find the product name for `SKU-E7421`. If Playwright's `aria_snapshot()` format changes, or if the product name contains characters that break the regex, the oracle will return `(0.0, False)` with an error message. A more robust approach would be to click the Electronics category link and then use `get_by_text(sku)` to find the row — but that requires a different Playwright API than the current action space exposes.

The top-level `README.md` also has a section on intentional scope limits (the last one) — deliberate cuts that are architectural choices rather than time constraints.