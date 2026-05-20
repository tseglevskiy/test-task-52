# ShopGym Docker Guide

## Prerequisites

- Docker installed and running
- The image built (see [Build](#1-build-the-image))

---

## 1. Build the Image

Run once from the `shop/` directory:

```bash
cd /path/to/gym/shop
docker build -t shopgym:latest .
```

The `.dockerignore` excludes `.venv/`, `*.db`, `*.jsonl`, `__pycache__/`, and `tests/` so the build context stays small.

---

## 2. Run a Single Instance

### 2a. Pre-create host files

Docker file bind-mounts require the **host file to exist before `docker run`**.  
Run from the **project root**:

```bash
mkdir -p _tmp/gym_1
touch _tmp/gym_1/shop.db _tmp/gym_1/shop.jsonl
```

### 2b. Start the container

Run from the **project root** so that `$(pwd)` expands to the correct absolute path:

```bash
docker run -d --name shopgym_1 \
  -v $(pwd)/_tmp/gym_1/shop.db:/app/shop.db \
  -v $(pwd)/_tmp/gym_1/shop.jsonl:/app/shop.jsonl \
  -p 5001:5000 \
  shopgym:latest
```

| Flag | Purpose |
|------|---------|
| `-d` | Run in background (detached) |
| `--name shopgym_1` | Container name for easy reference |
| `-v $(pwd)/_tmp/gym_1/shop.db:/app/shop.db` | Bind-mount the SQLite DB file |
| `-v $(pwd)/_tmp/gym_1/shop.jsonl:/app/shop.jsonl` | Bind-mount the JSONL event log |
| `-p 5001:5000` | Map host port 5001 → container port 5000 |

### 2c. Verify it's up

```bash
curl http://localhost:5001/api/health
# → {"status":"ok"}
```

---

## 3. Seed the Instance

Send a `POST /api/reset` with the desired seed configuration:

```bash
curl -X POST http://localhost:5001/api/reset \
  -H "Content-Type: application/json" \
  -d '{
    "seed": 42,
    "n_categories": 10,
    "n_products_per_category": 8,
    "required_products": [{"category": "Electronics", "sku": "SKU-E7421"}],
    "required_coupons":  [{"code": "SAVE10", "discount_pct": 10.0}],
    "required_orders":   [{"status": "placed"}],
    "n_filler_orders": 3
  }'
```

Expected response:

```json
{"status": "ok", "seed": 42, "elapsed_ms": 5}
```

Seeding is **idempotent** — calling `/api/reset` again drops all data and re-seeds from scratch.

---

## 4. Verify the Seeded State

```bash
curl -s http://localhost:5001/api/db-state | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('products      :', len(d['products']))
print('categories    :', len(set(p['category'] for p in d['products'])))
print('SKU-E7421     :', any(p['sku'] == 'SKU-E7421' for p in d['products']))
print('SAVE10 coupon :', any(c['code'] == 'SAVE10' for c in d['coupons']))
print('orders        :', len(d['orders']))
print('order statuses:', [o['status'] for o in d['orders']])
"
```

Expected output:

```
products      : 80
categories    : 10
SKU-E7421     : True
SAVE10 coupon : True
orders        : 3
order statuses: ['placed', 'placed', 'placed']
```

---

## 5. Browse the Shop

Open in a browser:

```
http://localhost:5001/
```

Key routes:

| URL | Page |
|-----|------|
| `/` | Product listing (supports `?category=X` and `?q=search`) |
| `/product/<id>` | Product detail + Add to Cart |
| `/cart` | Shopping cart |
| `/checkout` | Checkout form |
| `/orders` | Order history |
| `/order/<id>` | Single order detail + Cancel |

---

## 6. Tear Down

```bash
docker stop shopgym_1
docker rm shopgym_1
```

The SQLite DB persists on the host at `_tmp/gym_1/shop.db` (inside the project root) even after the container is removed.

---

## 7. Multiple Parallel Instances

Each gym instance gets its own container, host port, and DB path. Run from the **project root**. Example — 4 instances:

```bash
for i in 1 2 3 4; do
  mkdir -p _tmp/gym_$i
  touch _tmp/gym_$i/shop.db _tmp/gym_$i/shop.jsonl

  docker run -d --name shopgym_$i \
    -v $(pwd)/_tmp/gym_$i/shop.db:/app/shop.db \
    -v $(pwd)/_tmp/gym_$i/shop.jsonl:/app/shop.jsonl \
    -p $((5000 + i)):5000 \
    shopgym:latest
done
```

Then seed each independently:

```bash
for i in 1 2 3 4; do
  curl -s -X POST http://localhost:$((5000 + i))/api/reset \
    -H "Content-Type: application/json" \
    -d "{\"seed\": $i, \"n_categories\": 10, \"n_products_per_category\": 8,
         \"required_products\": [{\"category\": \"Electronics\", \"sku\": \"SKU-E7421\"}],
         \"required_coupons\": [{\"code\": \"SAVE10\", \"discount_pct\": 10.0}],
         \"required_orders\": [{\"status\": \"placed\"}],
         \"n_filler_orders\": 3}"
done
```

Stop all at once:

```bash
docker stop shopgym_1 shopgym_2 shopgym_3 shopgym_4
docker rm   shopgym_1 shopgym_2 shopgym_3 shopgym_4
```

---

## 8. Environment Variables

The container reads these env vars (defaults set in the Dockerfile):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `/app/shop.db` | Path to the SQLite database file |
| `LOG_PATH` | `/app/shop.jsonl` | Path to the JSONL event log (optional) |

---

## 9. API Quick Reference

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/health` | Health check → `{"status":"ok"}` |
| `GET` | `/api/db-state` | Full JSON snapshot of all DB tables |
| `POST` | `/api/reset` | Wipe and re-seed the database |
