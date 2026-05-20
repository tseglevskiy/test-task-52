# pw_test — Playwright Scripted Agent Tests

Scripted Playwright tests that prove a browser agent can complete each gym task against a live ShopGym Docker container. These are **not unit tests** — they drive a real browser through the full UI flow and verify success via the backend API (`/api/db-state`), not by scraping HTML.

This folder is the prototype for the `gym_env/` scripted oracle that will be used in the parallel demo.

---

## Contents

| File | Purpose |
|------|---------|
| `start_docker.sh` | Start + seed a fresh `shopgym_pw` container on port 5001 |
| `cancel_order.py` | Implements the `cancel_recent_order` task via Playwright |
| `requirements.txt` | Python dependencies (`playwright>=1.40`) |

Temporary artifacts (DB files, screenshots) go to `_tmp/` at the repo root — never committed.

---

## One-time setup

```bash
cd pw_test
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

---

## Running a test

### 1. Start and seed the container

```bash
# From the repo root:
bash pw_test/start_docker.sh
```

This will:
- Stop any existing `shopgym_pw` container
- Create a fresh DB at `_tmp/gym_pw/shop.db`
- Start the container on port 5001
- Seed it: 80 products, 10 categories, SKU-E7421, SAVE10 coupon, 3 placed orders

### 2. Run the test

```bash
# From pw_test/ with the venv active:
source .venv/bin/activate
python cancel_order.py
```

Expected output:

```
============================================================
Playwright test: cancel_recent_order
============================================================
[health] http://localhost:5001/api/health → ok

[SETUP] Most recent order:
        id     : 4b23ce20-...
        status : placed

[1] Opening shop home page: http://localhost:5001
[2] Clicking 'My Orders' in the nav...
[3] Clicking 'View' for order 4b23ce20...
[4] Clicking 'Cancel Order'...
[5] Verifying via /api/db-state...
    order 4b23ce20... status: 'placed' → 'cancelled'

✓ PASS  cancel_recent_order — order transitioned placed → cancelled
```

Screenshots are saved to `_tmp/pw_test_screenshots/`.

### 3. Tear down

```bash
docker stop shopgym_pw && docker rm shopgym_pw
```

---

## Navigation design

Each test starts with a single `page.goto(BASE_URL)` — equivalent to a user opening a browser tab. All subsequent navigation uses only clicks on links and buttons visible in the UI. No URL jumping.

The verifier always reads from `/api/db-state` (backend state) — never from rendered HTML. This matches how the gym env's reward signal works.

---

## How `cancel_order.py` works

**Task spec:** *"Cancel the most recent existing order in the account."*

**Flow:**
```
API pre-state: identify most recent placed order ID
  → goto home page
  → click "My Orders" (nav header)
  → click "View" for the target order
  → click "Cancel Order" button
  → API post-state: verify status == 'cancelled'
```

**Verifier:** compares `order.status` before and after — not page title, not flash message.
