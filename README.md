# ShopGym — Web Gym for RL Agents

A minimal web gym: a sandboxed, resettable e-commerce browser environment that an RL trainer can drive to teach a policy how to click, type, and navigate. Customers (AI labs, applied teams) spin up many parallel instances and run rollouts against it.

---

## Repository layout

```
gym/
├── shop/                  # The e-commerce Flask app (complete, do not modify)
│   ├── app.py             # All routes + internal API (/api/reset, /api/db-state)
│   ├── db.py              # SQLite schema
│   ├── seed.py            # Deterministic DB seeder
│   ├── templates/         # Jinja2 HTML templates
│   └── Dockerfile
├── gym_env/               # Gymnasium environment infrastructure
│   ├── env.py             # ShopEnv(gymnasium.Env)
│   ├── actions.py         # JSON action → Playwright call
│   ├── observation.py     # Page snapshot → obs dict
│   ├── tasks/base.py      # AbstractTask interface
│   ├── tests/             # Verifier unit tests + integration smoke test
│   ├── README.md          # Environment API, spaces, design decisions
│   └── TASK.md            # How to write a new task
├── tasks/                 # Concrete task implementations
│   ├── cancel_order.py
│   ├── apply_coupon.py
│   ├── buy_cheapest.py
│   └── README.md          # What each task does, verifier logic, oracle steps
├── scripts/
│   └── parallel_demo.py   # 4 concurrent envs, scripted oracles + random policy
├── docker-compose.yml     # 4 shop instances on ports 5001–5004
├── DEMO.md                # How to run the parallel demo
└── README.md              # This file
```

---

## Architecture

```
Host machine
│
├── gym_env/  (one process per env instance)
│     ShopEnv
│       ├── Playwright Chromium → http://localhost:500N  (browser)
│       ├── task.verify()       → GET /api/db-state      (state check)
│       └── env.reset()         → POST /api/reset        (wipe + reseed)
│
└── Docker container "shop_N"  (port 500N)
      Flask app
      /app/shop.db → volume mount: _tmp/gym_N/shop.db
```

One container = one Flask process = one SQLite file. Each parallel env instance gets a unique port and a unique DB path. No shared mutable state between instances.

For environment API details, observation/action spaces, and reset flow timing, see **[`gym_env/README.md`](gym_env/README.md)**.

---

## Decisions

### Decision 1 — Shop engine: build from scratch (Flask + sqlite3)

**Options considered:** Fork `alankrantas/svelteapp-typescript-go` (Go + SvelteKit, archived), fork `shurco/mycart` (Go + Svelte), Vendure (TypeScript/NestJS/GraphQL), custom Flask app.

**Decision:** Custom Flask app.

**Reasoning:** Every candidate was missing the same set of testing-harness features: integer-seeded reset, `/api/db-state`, no-auth checkout, configurable DB path via env var. These must be added regardless of starting point. The Go options add a second language to a Python project. `shurco/mycart` requires days of auth stripping. Custom Flask gives full control over schema (verifiers are trivial), reset is a single transaction (~100ms), and one language across the whole project.

**Tradeoffs accepted:** Less visual realism than a polished SPA — compensated by realistic page structure (navigation, pagination, multi-step checkout, coupon field, order list) that creates genuine agent difficulty.

---

### Decision 2 — Database: SQLite, state as a file, host-mounted

**Options considered:** PostgreSQL/MySQL in a separate container; SQLite inside the app container (ephemeral); SQLite on host, volume-mounted.

**Decision:** SQLite on host, volume-mounted into the container.

**Reasoning:** No extra container, no server process, no auth. The DB file is a self-contained artifact — it can be copied, archived, diffed with any SQLite tool, and inspected without a running process. Each parallel instance gets its own host path (`_tmp/gym_N/shop.db`), so there is no shared mutable state and no connection pooling. The container can crash and be replaced without touching the DB file.

**Tradeoffs accepted:** SQLite doesn't scale to high write concurrency — not an issue because each instance has its own file and only one Flask worker writes at a time.

---

### Decision 3 — Verifier access: `/api/db-state`, not direct SQLite

**Options considered:** Task verifiers open the SQLite file directly; task verifiers call `GET /api/db-state`.

**Decision:** Verifiers call `GET /api/db-state`.

**Reasoning:** The shop owns its data model. A verifier that opens the SQLite file directly is coupled to the schema — if the shop changes a column name, every verifier breaks. Via `/api/db-state`, verifiers are coupled only to the JSON response shape, which is the shop's stable public contract. Task classes that only need `base_url` can work against any deployment and live in a separate repository.

**Tradeoffs accepted:** Verifier requires the Flask container to be running — in practice never an issue, since the container must be running for the agent to operate anyway.

---

### Decision 4 — Parallelism: one Docker container per instance

**Options considered:** One container with multiple Flask workers; one subprocess per instance; one Docker container per instance.

**Decision:** One Docker container per instance in production (`docker-compose.yml`). For the parallel demo script, Flask runs as subprocesses for speed.

**Reasoning:** True isolation — one container crash doesn't affect others. Restart or reseed one instance without touching others. `docker-compose.yml` with 4 services is the deployable artifact. For the demo, subprocess startup is ~100ms vs ~2–5s for Docker, which is sufficient for demonstrating parallelism locally.

---

### Decision 5 — Checkout address validation: structured US format

**Options considered:** No validation; minimal (non-empty only); structured US validation (state dropdown, ZIP regex).

**Decision:** Structured US validation — state is a `<select>` with all 50 states + DC, ZIP must match `^\d{5}$`.

**Reasoning:** The task specifies shipping to `123 Main St, Springfield, IL 62701`. Without structured validation, an agent that types "IL 62701" into the ZIP field could still get `reward=1.0`. Structured validation forces the agent to correctly interact with a dropdown and type a valid ZIP — realistic friction points on actual e-commerce sites. The verifier checks the stored `shipping_address` string, so if the form rejects malformed input, the agent cannot bypass it.

**Tradeoffs accepted:** US-only scope is a simplification; real shops support international addresses.

---

## Intentional scope limits

These are deliberate cuts for this exercise, not architectural choices. A production gym would revisit each.

| Topic | Restriction | Reason |
|---|---|---|
| **Geography** | US addresses only | Keeps address validation tractable; task spec gives a US address |
| **Authentication** | None | Task spec says skip auth; adds complexity with zero benefit for agent training |
| **Payment** | No payment step | Task spec says skip real payments |
| **ID format** | UUID primary keys (not sequential integers) | Prevents agents from shortcutting tasks by sorting IDs instead of reading displayed dates |
| **CSS/images** | None beyond structural HTML | Task spec: "unstyled HTML is completely fine" |
| **Stock tracking** | No stock column; Add to Cart always succeeds | None of the three tasks involve stock |
| **DOM in observation** | Raw HTML excluded; only `url`, `axtree`, `screenshot` | axtree covers the same semantic content at ~5× less data |
| **RL training loop** | Scripted oracles and random policy only | Task spec: "a baseline policy is plenty" |
| **Cross-browser** | Chromium only | Task spec: "one Chromium target is sufficient" |
| **Timestamps** | All stored as UTC epoch integers; no `datetime.now()` anywhere | Avoids non-determinism from locale-aware wall-clock calls |
