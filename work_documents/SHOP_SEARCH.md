# Open-Source SQLite-First Shop/E-Commerce Portals for Local Docker Testing

## TL;DR

* No off-the-shelf project ticks every box. The hard requirements are easy (SQLite \+ Docker \+ Public \+ No real payments) but the specific desired endpoints — `GET /api/db-state`, a deterministic seed-from-integer reset (\<3s), `/orders` list with a cancel button, and zero-auth on the storefront — are testing-harness features that no production-grade open-source store ships out of the box.  
* The two best starting points are (1) `alankrantas/svelteapp-typescript-go` — a Svelte \+ Go \+ SQLite demo with products/orders/cart wired end-to-end, no auth, MIT-licensed, single Docker image (but archived Sep 28, 2025), and (2) `shurco/mycart` (formerly `shurco/litecart`) — a real one-binary Go/SQLite shopping cart with Docker images and MIT license, 349 stars on GitHub and active development (v0.2.7 released Apr 7, 2026), but the storefront expects auth on checkout and has no `/api/db-state` or seedable reset. [github](https://github.com/shurco/litecart)  
*   
* Recommendation: Fork `svelteapp-typescript-go` as the base (closest fit, simplest stack, already has products/orders/SQLite/Docker) and add the missing pieces — env-var DB path, `/api/db-state`, `/api/reset?seed=N`, coupons table, category filter, cancel button. Expect roughly 1–2 days of work versus 1–2 weeks if you start from `mycart` (which forces you to strip the admin/auth layers) or from scratch.

## Key Findings

| Project | Lang | DB | Docker | License | Stars | Last activity | Auth required on storefront? | Match score |
| ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| alankrantas/svelteapp-typescript-go | Go \+ SvelteKit \+ TS | SQLite native (`go-sqlite3`) | Yes (Dockerfile) | MIT | 221 | Archived 2025-09-28 | No | ★★★★☆ best |
| shurco/mycart (was shurco/litecart) | Go \+ Svelte | SQLite native (embedded) | Yes (Docker Hub \+ GHCR images) | MIT | 349 | Active; v0.2.7 (Apr 7, 2026\) | Storefront yes-checkout, admin yes-login | ★★★☆☆ |
| vendure-ecommerce/vendure | TypeScript / NestJS / GraphQL | SQLite via `better-sqlite3` (dev), also Postgres/MySQL | Yes (`vendure-demo`) | GPL-3.0-or-later [GitHub](https://github.com/vendure-ecommerce/vendure/blob/master/LICENSE.md) [GitHub](https://github.com/vendure-ecommerce/vendure) | 8,000 (per the GitHub milestone page for vendurehq/vendure) [GitHub](https://github.com/vendurehq/vendure/milestone/49) | v3.6.0 released Mar 31, 2026 [Vendure](https://docs.vendure.io/changelog?focus=v3.6.0)  with patch releases up to v3.6.3 on npm as of May 8, 2026 [npm](https://www.npmjs.com/org/vendure) | Configurable; needs strip-down | ★★★☆☆ |
| HarshShah1997/Shopping-Cart | Python/Flask | SQLite native | No | MIT | 263 | Dormant (12 commits total) [github](https://github.com/HarshShah1997/Shopping-Cart) | Yes (sample login `sample@example.com`/`sample`) | ★★☆☆☆ |
| mariobox/flask-ecomm | Python/Flask \+ Jinja \+ cs50 SQL | SQLite | No | No LICENSE file | 24 | Dormant (3 commits) | Yes | ★☆☆☆☆ |
| Durgaprasad-Nagarkatte/Simple-Flask-Shopping-Cart | Python/Flask | SQLite | No | No LICENSE file | 34 | One commit, no maintenance | Yes | ★☆☆☆☆ |
| medusajs/medusa | TypeScript | SQLite dropped at v1.12.0; v2.x requires Postgres | Yes | MIT | 33,736 (as of May 19, 2026, per the medusajs GitHub organization page) [GitHub](https://github.com/medusajs) | Active | n/a | ✗ disqualified |
| kritserv/django\_online\_store, hzshashwat/Simple-Ecommerce and similar Django+SQLite repos | Python/Django | SQLite | mixed | mixed | low | mixed | yes (Django auth is on by default) | ✗ |
| Heavyweights (Spree, Saleor, Sylius, Bagisto, OpenCart, PrestaShop, Magento/Adobe Commerce, Shopware, Sharetribe, etc.) | various | Postgres or MySQL required | usually compose with DB sidecar | OSL/AFL/AGPL/BSD/MIT mix | high | active | yes | ✗ disqualified |

### Why most well-known platforms fail the hard requirements

The widely-cited "best open-source e-commerce" lists (Magento/Adobe Commerce, WooCommerce, PrestaShop, Sylius, Spree, Saleor, Shopware, Bagisto, OpenCart, NopCommerce, Shopizer, Reaction Commerce, Vue Storefront, Drupal Commerce, Thelia) all assume a server-class RDBMS (MySQL/MariaDB or PostgreSQL) and ship multi-container `docker-compose` files. They also all gate cart/checkout behind a customer session. They are non-starters for your "SQLite only, single container, no auth, multiple instances on a laptop" use case.  
Medusa is the one large project that historically supported SQLite, but per its official v1.12.0 release notes that support was explicitly dropped, with maintainers writing: *"SQLite support was initially added to reduce friction for developers trying Medusa for the first time … as we've added features that use more advanced database concepts, we've seen that SQLite has started to cause more harm than good."* The v2.x rewrite (MikroORM-based) makes Postgres the only first-class choice. [GitHub](https://github.com/medusajs/medusa/releases/tag/v1.12.0)  
[Medusa](https://docs.medusajs.com/learn/introduction/from-v1-to-v2)

## Details

### 1\. `alankrantas/svelteapp-typescript-go` — best base to build on

URL: [https://github.com/alankrantas/svelteapp-typescript-go](https://github.com/alankrantas/svelteapp-typescript-go) Stack: SvelteKit \+ TypeScript frontend, Go backend, `mattn/go-sqlite3`, single Dockerfile. License: MIT. Activity: 823 commits, archived (read-only) on 2025-09-28 — fine for forking, but no upstream fixes. [github](https://github.com/alankrantas/svelteapp-typescript-go)  
What it already has, mapped to your spec:

* `/products` list with category buttons and add-to-cart — ✅ [github](https://github.com/alankrantas/svelteapp-typescript-go)  
*   
* Cart with quantity, order submission to `/order` — ✅ [github](https://github.com/alankrantas/svelteapp-typescript-go)  
*   
* Order summary page showing order ID after place — ✅ [github](https://github.com/alankrantas/svelteapp-typescript-go)  
*   
* SQLite DB file (`./db/data.sqlite3`) pre-seeded with 9 products and an empty `orders` table — ✅ (seed bug exists but trivial) [GitHub](https://github.com/alankrantas/svelteapp-typescript-go)  
* [github](https://github.com/alankrantas/svelteapp-typescript-go)  
*   
* Docker support: `yarn docker` and `yarn docker-run` commands, single image — ✅ [GitHub](https://github.com/alankrantas/svelteapp-typescript-go)  
* [github](https://github.com/alankrantas/svelteapp-typescript-go)  
*   
* No authentication on either the storefront or the order flow — ✅ (the README explicitly states: "error handlings between front-end and authentication are mostly ignored") [github](https://github.com/alankrantas/svelteapp-typescript-go)  
*   
* No payment processing — the order is just persisted — ✅

What you'd need to add:

* DB path env var (currently hard-coded `./db/data.sqlite3`) — \~20 lines of Go  
* `Coupons` table \+ coupon-code field on cart/checkout — small schema \+ handler change  
* `discount_percent` line on cart summary  
* Category filter from a `category` column (data already has product groupings) and text search (`LIKE %q%`)  
* Price-sort \+ pagination (≥10/page)  
* `stock` column and out-of-stock disabling (currently the seed has stock=infinite)  
* Order list page `/orders` (the backend already stores orders; just needs a list route \+ template)  
* Order detail with cancel button (status flip)  
* `GET /api/db-state` — easy in Go: `SELECT * FROM sqlite_master` then `SELECT *` for each table → JSON  
* `POST /api/reset?seed=N` — wipe tables, re-run `INSERT` with `rand.New(rand.NewSource(int64(seed)))` so output is deterministic and well under 3s for hundreds of rows  
* Site-wide header with cart badge (the demo has navigation but no badge counter), breadcrumbs, related products

Effort estimate: \~1–2 days for a developer who knows Go and Svelte (or just Go — you can serve the templates from Go directly and drop the Svelte build to simplify).  
Caveats: The repository is archived. Fork it; do not assume upstream fixes. Image size as built is \~25 MB (Go static \+ embedded UI). [github](https://github.com/alankrantas/svelteapp-typescript-go)

### 2\. `shurco/mycart` (formerly `shurco/litecart`) — closest "real product" candidate

URL: [https://github.com/shurco/mycart](https://github.com/shurco/mycart) (legacy redirect: [https://github.com/shurco/litecart](https://github.com/shurco/litecart)) Stack: Go backend \+ SvelteKit admin \+ Svelte storefront, embedded SQLite as the *only* DB. License: MIT. Stars: 349\. Latest release: v0.2.7 (Apr 7, 2026). Note: Per the shurco/mycart README the project was renamed from "litecart" to "mycart" after the maintainer received "a trademark-related claim regarding the use of this name. To avoid confusion and potential legal issues, the project will continue under a new name. The codebase itself is not changing." No exact date is given; the rename preceded v0.2.7 (Apr 7, 2026), which was the first release under the mycart name. [GitHub](https://github.com/shurco/mycart)  
Strengths versus your spec:

* One container, SQLite-only (no Postgres/MySQL dependency) — ✅ exactly your hard constraint  
* Official Docker Hub (`shurco/mycart:latest`) and GHCR images — ✅ [GitHub](https://github.com/shurco/litecart)  
*   
* Port configurable via `--http 0.0.0.0:8088` flag — ✅ [Go Packages](https://pkg.go.dev/github.com/shurco/litecart)  
*   
* Sells real products including digital goods, has cart, checkout, orders, coupons (Stripe/PayPal/SpectroCoin/Coinbase/Dummy payment) — ✅ [GitHub](https://github.com/shurco/mycart)  
*   
* "Dummy Payment" provider for $0 totals — useful as a stand-in for the "no real payment processing" requirement — ✅

Gaps that mean it's not a drop-in:

* The admin panel and checkout assume auth (the dev seed prints `login - user@mail.com / password - Pass123`). You'd need to bypass or remove this. The storefront product browsing itself is public.  
* DB lives in `./lc_base/` — the path is controlled by a bind-mount, not a single env var. You'd add an env var or wrap with a symlink.  
* No `GET /api/db-state` endpoint — would need to add a Go handler that introspects `sqlite_master` (small, \~50 lines).  
* No deterministic seeded reset — the dummy fixtures script (`./scripts/migration dev up`) is the closest thing, but it's not parametric.  
* Built around real payment providers; you'd want to force Dummy Payment always.

Effort estimate: \~3–5 days to strip auth, add the testing endpoints, and rewire to a single env-configurable DB path. The codebase is well-organized Go but larger than the Svelte-Go demo (Go 57%, Svelte 32%, TypeScript 7%). [github](https://github.com/shurco/litecart)

### 3\. Vendure (TypeScript / NestJS / GraphQL) — heaviest, but most featureful

URL: [https://github.com/vendure-ecommerce/vendure](https://github.com/vendure-ecommerce/vendure) License: GPL-3.0-or-later (note: copyleft — your derivative source must also be GPL-3.0-or-later if you distribute it). Stars: 8,000 per the GitHub milestone page for vendurehq/vendure. Latest release: v3.6.0 released Mar 31, 2026, with patch releases up to v3.6.3 on npm as of \~May 8, 2026 (per the vendure.io changelog and npmjs.com/org/vendure). SQLite support: First-class via `better-sqlite3` driver and an optional `sqljs` (in-memory) driver explicitly used for E2E tests. From `packages/dev-server/dev-config.ts`: `case 'sqlite': … type: 'better-sqlite3', database: path.join(__dirname, 'vendure.sqlite')`. DB name configurable via `process.env.DB_NAME`. Docker: Official `vendure-ecommerce/vendure-demo` repo with Dockerfile. Catalog of features: Full product/category/order/promotion/coupon model already exists; the Promotions module supports percentage discounts and coupon codes natively. [GitHub \+ 2](https://github.com/vendurehq/vendure/milestone/49)  
Why it isn't the top recommendation:

* Storefront is GraphQL-based (no built-in HTML storefront — you'd need to drop in the official Next.js starter or write your own). That's a *lot* of moving parts for "no styling required".  
* The admin/storefront APIs use customer sessions (guest checkout exists, but the framework is built around auth-aware contexts).  
* License is GPL-3.0; if your usage is internal-only that's fine, but distributing the modified code requires releasing the source.  
* A `/api/db-state` would have to be implemented as a custom NestJS plugin route. The reset-with-seed could leverage Vendure's `populate` script but it isn't \<3 s for any non-trivial dataset.

When to choose Vendure anyway: if you'll keep iterating on this fixture for months and want a GraphQL surface plus a real admin UI for fixture authoring, the engineering investment pays off.

### 4\. Mock/fake-store APIs (fakestoreapi.com, DummyJSON, Platzi fake-api-backend)

These are popular for frontend prototyping but do not satisfy your spec:

* fakestoreapi.com (`keikaavousi/fake-store-api`): per the project README, *"I decided to create this simple web service with NodeJs(express) and MongoDB as a database"* — so it is Node/Express \+ MongoDB, not SQLite. Writes are simulated; the README states for DELETE: *"Nothing will delete on the database."* [GitHub](https://github.com/keikaavousi/fake-store-api)  
* [GitHub](https://github.com/keikaavousi/fake-store-api)  
*   
* DummyJSON (`Ovi/DummyJSON`): same pattern — non-persistent, no SQLite, no reset/seed endpoint.  
* Platzi Fake Store API (`PlatziLabs/fake-api-backend`): NestJS \+ TypeORM \+ Postgres, real CRUD that persists, but no documented `/reset` or `/db-state` endpoints, and the periodic reset is a maintainer-side cron, not an API.

None ship a `/db-state` introspection route or a deterministic integer-seed reset.

### 5\. Flask/SQLite candidates that look right but aren't

These show up at the top of search results but each has at least one blocker:

* HarshShah1997/Shopping-Cart (263★, MIT, Flask+SQLite): Pre-built `database.db` in repo, but it requires login (sample creds `sample@example.com`/`sample`) and has no Docker, no env var for DB path, no `/db-state`, no reset endpoint, no coupon model. 12 commits total — abandoned. [GitHub](https://github.com/HarshShah1997/Shopping-Cart)  
* [github](https://github.com/HarshShah1997/Shopping-Cart)  
*   
* mariobox/flask-ecomm (24★, no LICENSE file, Flask+SQLite+cs50): Single CS50 final project. Requires login to add to cart. No license \= legal risk for forking. [GitHub](https://github.com/mariobox/flask-ecomm)  
* [github](https://github.com/mariobox/flask-ecomm)  
*   
* Durgaprasad-Nagarkatte/Simple-Flask-Shopping-Cart (34★, no LICENSE file, one commit): Same shape, same blockers. No license.  
* kritserv/django\_online\_store and hzshashwat/Simple-Ecommerce (Django \+ SQLite): Django's auth is on by default; either you keep the User table around as dead weight or you strip out `django.contrib.auth`. Docker support varies. Either could be used as a Python base if you prefer Django, but the LOC needed to add `/db-state`, `/reset`, and to clean up auth makes them roughly equivalent in effort to building from scratch with Flask \+ SQLAlchemy.

### 6\. "Build from scratch" baseline

For perspective: a single-file Flask app (FastAPI works equally well) using `sqlite3` from stdlib, Jinja templates, no styling, and a \~150-line `seed.py` that runs in \<100 ms on a 200-product fixture is a realistic half-day project for someone fluent in Python. The same is true for a single-binary Go app using `modernc.org/sqlite` (CGO-free) or `mattn/go-sqlite3`. Given the very specific testing-fixture requirements (especially `/api/db-state` and integer-seeded reset), this is the path most teams will end up on, and it's competitive with forking the Svelte-Go demo. [Andrew-quinn](https://til.andrew-quinn.me/posts/you-don-t-need-cgo-to-use-sqlite-in-your-go-binary/)

## Recommendations

### Staged decision tree

1. Primary recommendation — Fork `alankrantas/svelteapp-typescript-go`. It is the closest match: SQLite native, MIT, single Docker image, no auth, products/cart/order flow already wired. Spend \~1–2 days adding env-var DB path, `/api/db-state`, integer-seeded `/api/reset`, coupon code, category filter, cancel button, breadcrumbs. If you want to simplify even further, replace the SvelteKit UI with Go templates so the entire app is one binary and the build is `go build` \+ `docker build`.  
2. If you require an active upstream and don't mind stripping auth, choose `shurco/mycart`. Plan on \~3–5 days to neutralise the auth layer on checkout, force Dummy Payment, add the testing endpoints, and add an env var for the DB path. You gain a polished admin UI for free, useful if non-engineers will be loading fixtures.  
3. If you anticipate multi-month investment and want GraphQL \+ Admin UI, choose Vendure and accept the GPL-3.0 constraint. Configure the `better-sqlite3` driver, write a small `DbStatePlugin` and `ResetPlugin`. Plan on \~1–2 weeks.  
4. If your team is most fluent in Python, do not bother forking the dormant Flask repos — build a 300–400 LOC Flask \+ SQLAlchemy \+ Jinja app from scratch. You will spend less total time than auditing and modifying `HarshShah1997/Shopping-Cart` or similar, and you will own a clean MIT-able codebase. Use `modernc.org/sqlite` if you do this in Go for a pure-Go single-binary image.

### Benchmarks/thresholds that would change the recommendation

* If `svelteapp-typescript-go` gets un-archived or a maintained fork emerges → it becomes unambiguously the top choice.  
* If Vendure's `sqljs` driver matures enough to be production-supported (today it's E2E-test-only) → Vendure jumps a tier for simple test setups, because in-memory SQLite \+ integer seed is essentially free.  
* If your team needs more than \~3 instances per developer machine running in parallel, watch the image size: the Go-based candidates are \~20–40 MB, Vendure/Medusa images are 300 MB+.  
* If you ever need real payment-gateway simulation (3DS flows, webhooks, refunds), revisit `shurco/mycart` — its Dummy Payment provider plus Stripe sandbox keys are the cheapest route.

### Concrete next steps for option 1 (recommended)

1. `git clone https://github.com/alankrantas/svelteapp-typescript-go && git checkout -b internal-fixture`  
2. Replace the `./db/data.sqlite3` hard-coding with `os.Getenv("DB_PATH")` with a default.  
3. Add a `coupons` table and `discount_percent` column on orders; expose POST `/api/cart/apply-coupon`.  
4. Add Go handlers: `GET /api/db-state` (loop over `sqlite_master`, return JSON), `POST /api/reset` with `seed` query param (use `math/rand.New(rand.NewSource(int64(seed)))` for deterministic data).  
5. Add `GET /orders` list and `POST /orders/{id}/cancel` (status flip; bump stock back).  
6. Bake `sqlite3` CLI into the Docker image (`apk add sqlite` on Alpine) so you can `docker exec -it ... sqlite3 /data/db.sqlite3`.  
7. Containerise with `--port` mapped to `$PORT` env var, publish to your internal registry, run N instances on different host ports.

## Caveats

* Licensing: Two of the Flask candidates (`mariobox/flask-ecomm`, `Durgaprasad-Nagarkatte/Simple-Flask-Shopping-Cart`) have no `LICENSE` file. Under default GitHub terms that means all rights are reserved by the author and you cannot legally fork or modify them for production use. Avoid both, regardless of feature fit. Vendure is GPL-3.0-or-later — fine for internal use, but distributing modifications externally would require releasing your source.  
* Activity flags: `svelteapp-typescript-go` was archived by its owner on Sep 28, 2025 — usable but with zero upstream support. The Flask candidates are essentially abandoned (1–12 commits total). [github](https://github.com/alankrantas/svelteapp-typescript-go)  
*   
* Medusa pre-v1.12.0 still has SQLite drivers floating around in old branches; do not be tempted. The v2.x release line is a hard break and requires Postgres.  
* Vendure version vs. star numbers were taken from the GitHub milestone page (8k stars) and the vendure.io changelog \+ npmjs.com/org/vendure (v3.6.x line). Numbers move daily; verify against `github.com/vendure-ecommerce/vendure/releases` before committing.  
* Single-container SQLite \+ multiple instances: SQLite handles concurrent readers fine but only one writer. For your stated use case of "multiple instances of the fixture running in parallel on the same machine", this is a non-issue because each container has its own DB file. Just make sure the DB-file env var points to a *unique* path per container (e.g., `/data/$INSTANCE_ID.sqlite`).  
* Where the data really came from: Some snippet results (search summaries from medium.com, ecommerce-platforms.com, kinsta.com, liquidweb.com, webkul.com, belvg.com, nopcommerce.com) are vendor blogs that promote managed hosting and use phrases like "popular" or "best" without specifics; I have only relied on them as quick filters confirming which platforms require server-class databases. Star counts, license fields, release tags, and DB-driver code references are taken from the upstream GitHub repos and from `pkg.go.dev` / `npmjs.com` package pages.
