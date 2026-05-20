#!/usr/bin/env bash
# start_docker.sh — starts and seeds a fresh shopgym container for Playwright testing
#
# Usage:  bash pw_test/start_docker.sh
# Stops existing shopgym_pw container if running, then starts fresh.

set -e

CONTAINER=shopgym_pw
PORT=5001
# Use _tmp/ inside the project root — never /tmp/ (see .clinerules/project-conventions.md)
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST_DIR="$REPO_ROOT/_tmp/gym_pw"

echo "==> Cleaning up any existing container named $CONTAINER..."
docker stop $CONTAINER 2>/dev/null && docker rm $CONTAINER 2>/dev/null || true

echo "==> Creating host DB/log files at $HOST_DIR..."
mkdir -p "$HOST_DIR"
# Remove old DB so we start truly fresh
rm -f "$HOST_DIR/shop.db" "$HOST_DIR/shop.jsonl"
touch "$HOST_DIR/shop.db" "$HOST_DIR/shop.jsonl"

echo "==> Starting container $CONTAINER on port $PORT..."
docker run -d --name "$CONTAINER" \
  -v "$HOST_DIR/shop.db:/app/shop.db" \
  -v "$HOST_DIR/shop.jsonl:/app/shop.jsonl" \
  -p "$PORT:5000" \
  shopgym:latest

echo "==> Waiting for health check..."
for i in $(seq 1 20); do
  STATUS=$(curl -s "http://localhost:$PORT/api/health" 2>/dev/null || echo "")
  if echo "$STATUS" | grep -q '"ok"'; then
    echo "    ✓ Container healthy (attempt $i)"
    break
  fi
  echo "    ... waiting ($i/20)"
  sleep 0.5
done

if ! echo "$STATUS" | grep -q '"ok"'; then
  echo "ERROR: Container did not become healthy in time."
  docker logs "$CONTAINER"
  exit 1
fi

echo "==> Seeding the database (seed=42, default gym config)..."
RESET_RESP=$(curl -s -X POST "http://localhost:$PORT/api/reset" \
  -H "Content-Type: application/json" \
  -d '{
    "seed": 42,
    "n_categories": 10,
    "n_products_per_category": 8,
    "required_products": [{"category": "Electronics", "sku": "SKU-E7421"}],
    "required_coupons":  [{"code": "SAVE10", "discount_pct": 10.0}],
    "required_orders":   [{"status": "placed"}],
    "n_filler_orders": 3
  }')

echo "    Reset response: $RESET_RESP"
if ! echo "$RESET_RESP" | grep -q '"ok"'; then
  echo "ERROR: Reset did not return ok."
  exit 1
fi

echo ""
echo "==> Verifying seeded state..."
curl -s "http://localhost:$PORT/api/db-state" | python3 -c "
import json, sys
d = json.load(sys.stdin)
orders = d['orders']
most_recent = max(orders, key=lambda o: o['created_at'])
print(f'  products      : {len(d[\"products\"])}')
print(f'  SKU-E7421     : {any(p[\"sku\"]==\"SKU-E7421\" for p in d[\"products\"])}')
print(f'  SAVE10 coupon : {any(c[\"code\"]==\"SAVE10\" for c in d[\"coupons\"])}')
print(f'  orders        : {len(orders)}')
print(f'  most recent order id     : {most_recent[\"id\"]}')
print(f'  most recent order status : {most_recent[\"status\"]}')
"

echo ""
echo "✓ Done. Shop is running at http://localhost:$PORT/"
echo "  Run the Playwright test:"
echo "    cd pw_test && source .venv/bin/activate && python cancel_order.py"
