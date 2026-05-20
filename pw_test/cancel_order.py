"""
cancel_order.py — Playwright implementation of the cancel_recent_order task.

Task spec: "Cancel the most recent existing order in the account."

Navigation rule: only one initial page.goto(BASE_URL) — all subsequent navigation
is by clicking links and buttons visible on the page, just like a real user.
We may use knowledge of the site structure (e.g. which order ID to target) but
we express that knowledge through UI interactions, not URL jumps.

Verifier: reads backend state via GET /api/db-state — no HTML scraping.

Prerequisites:
  1. Start and seed the container:
       bash pw_test/start_docker.sh
  2. Set up the venv (once):
       cd pw_test
       python -m venv .venv
       source .venv/bin/activate
       pip install -r requirements.txt
       playwright install chromium

Run:
  cd pw_test
  source .venv/bin/activate
  python cancel_order.py
"""

import json
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, Page

BASE_URL = "http://localhost:5001"
# Use _tmp/ inside the project root — never /tmp/ (see .clinerules/project-conventions.md)
_THIS_DIR = Path(__file__).parent
SCREENSHOT_DIR = _THIS_DIR.parent / "_tmp" / "pw_test_screenshots"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def db_state() -> dict:
    """Fetch the full DB snapshot from the shop API."""
    with urllib.request.urlopen(f"{BASE_URL}/api/db-state") as resp:
        return json.load(resp)


def most_recent_order(orders: list[dict]) -> dict:
    """Return the order with the largest created_at (most recent)."""
    return max(orders, key=lambda o: o["created_at"])


def save_screenshot(page: Page, name: str) -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"    screenshot → {path}")


# ---------------------------------------------------------------------------
# Task: cancel_recent_order
# ---------------------------------------------------------------------------

def run_cancel_recent_order(page: Page) -> bool:
    """
    Starting from the shop home page, navigate by clicking only:
      Home → click "My Orders" → click "View" for the most recent order
      → click "Cancel Order" → verify via API.

    Returns True if the verifier confirms success.
    """

    # ── SETUP: snapshot pre-state via API ──────────────────────────────────
    pre_state = db_state()
    pre_orders = pre_state["orders"]
    target = most_recent_order(pre_orders)

    print(f"\n[SETUP] Most recent order:")
    print(f"        id     : {target['id']}")
    print(f"        status : {target['status']}")
    print(f"        created: {target['created_at']}")

    if target["status"] != "placed":
        print(f"[SKIP]  Most recent order is '{target['status']}', not 'placed'. Nothing to cancel.")
        return False

    # ── STEP 1: Open the shop home page ────────────────────────────────────
    # This is the only goto — equivalent to a user opening a browser tab.
    print(f"\n[1] Opening shop home page: {BASE_URL}")
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    save_screenshot(page, "01_home")
    print(f"    url: {page.url}")

    # ── STEP 2: Click "My Orders" in the header nav ─────────────────────────
    print(f"\n[2] Clicking 'My Orders' in the nav...")
    page.click("a[href='/orders']")
    page.wait_for_load_state("networkidle")
    save_screenshot(page, "02_orders_list")
    print(f"    url: {page.url}")
    assert "/orders" in page.url, f"Expected /orders page, got {page.url}"

    # ── STEP 3: Click "View" for the most recent order ──────────────────────
    # We know the target order ID from the API pre-state. The orders list
    # renders a "View" link for each order as <a href="/order/<id>">View</a>.
    print(f"\n[3] Clicking 'View' for order {target['id'][:8]}...")
    view_link = page.locator(f"a[href='/order/{target['id']}']")
    count = view_link.count()
    print(f"    found {count} matching View link(s)")
    assert count == 1, f"Expected 1 View link for order {target['id']}, got {count}"
    view_link.click()
    page.wait_for_load_state("networkidle")
    save_screenshot(page, "03_order_detail")
    print(f"    url: {page.url}")
    assert f"/order/{target['id']}" in page.url

    # ── STEP 4: Click "Cancel Order" button ─────────────────────────────────
    print(f"\n[4] Clicking 'Cancel Order'...")
    cancel_btn = page.locator("form[action*='/cancel'] button[type='submit']")
    count = cancel_btn.count()
    print(f"    found {count} Cancel button(s)")
    assert count == 1, f"Expected 1 Cancel button, got {count}"
    cancel_btn.scroll_into_view_if_needed()
    cancel_btn.click()
    page.wait_for_load_state("networkidle")
    save_screenshot(page, "04_after_cancel")
    print(f"    url after cancel: {page.url}")

    # ── STEP 5: Verify via API (backend state, not HTML) ────────────────────
    print(f"\n[5] Verifying via /api/db-state...")
    post_state = db_state()
    post_orders = {o["id"]: o for o in post_state["orders"]}

    if target["id"] not in post_orders:
        print(f"    ERROR: order {target['id']} disappeared from DB!")
        return False

    new_status = post_orders[target["id"]]["status"]
    print(f"    order {target['id'][:8]}... status: {target['status']!r} → {new_status!r}")

    if new_status == "cancelled":
        print(f"\n✓ PASS  cancel_recent_order — order transitioned placed → cancelled")
        return True
    else:
        print(f"\n✗ FAIL  cancel_recent_order — expected 'cancelled', got {new_status!r}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Playwright test: cancel_recent_order")
    print("=" * 60)

    # Quick health check before launching browser
    try:
        with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=3) as resp:
            health = json.load(resp)
        assert health.get("status") == "ok", f"Unexpected health: {health}"
        print(f"[health] {BASE_URL}/api/health → ok")
    except Exception as e:
        print(f"ERROR: Shop is not reachable at {BASE_URL}. Start it first:")
        print(f"  bash pw_test/start_docker.sh")
        raise SystemExit(1) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            passed = run_cancel_recent_order(page)
        finally:
            browser.close()

    print()
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
