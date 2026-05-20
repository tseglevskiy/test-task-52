"""
demo/oracles.py — Scripted oracle policies for ShopGym tasks.

Each oracle completes its task using only env.step() actions and
obs["axtree"] parsing — no direct /api/db-state calls.

Exported functions:
    run_cancel_oracle(env, obs)      → (reward, terminated)
    run_apply_coupon_oracle(env, obs) → (reward, terminated)
    run_buy_cheapest_oracle(env, obs) → (reward, terminated)
"""

import json
import re


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _step(env, action_dict):
    """Execute one action and return the full (obs, reward, terminated, truncated, info) tuple."""
    return env.step(json.dumps(action_dict))


# ---------------------------------------------------------------------------
# Oracles
# ---------------------------------------------------------------------------

def run_cancel_oracle(env, obs):
    """
    Scripted oracle for cancel_recent_order.

    Uses only UI interactions — no direct API calls. Finds the most-recent
    order by navigating to the orders list (sorted newest-first) and clicking
    the first "View" link.

    Steps:
      1. Click "My Orders" nav link → /orders, sorted by created_at DESC
      2. Click "View" (first match = most-recent order)
      3. Click "Cancel Order" button

    Returns:
        (reward, terminated) from the final step.
    """
    # /orders is sorted ORDER BY created_at DESC (app.py), so the first
    # "View" link is always the most-recent order.
    _step(env, {"type": "click_by_role", "role": "link", "name": "My Orders"})
    _step(env, {"type": "click_by_role", "role": "link", "name": "View"})
    obs, reward, terminated, truncated, info = _step(
        env, {"type": "click_by_role", "role": "button", "name": "Cancel Order"}
    )
    return reward, terminated


def run_apply_coupon_oracle(env, obs):
    """
    Scripted oracle for apply_coupon_with_quantity.

    Uses only UI interactions — no direct API calls. Finds SKU-E7421 by
    filtering to Electronics and parsing the ARIA observation tree.

    Steps:
      1. Click "Electronics" nav link → product listing filtered to Electronics
      2. Parse obs["axtree"] with regex to find the product name for SKU-E7421
         (Playwright encodes all cell text into the row's accessible name;
          the first link within that row is the product name link)
      3. Click the product name link → product detail page
      4. Set quantity to 2 (focus spinbutton → Ctrl+A → type "2")
      5. Click "Add to Cart"
      6. Type coupon code "SAVE10" and click "Apply Coupon"
      7. Click "Proceed to Checkout"
      8. Fill shipping form and click "Place Order"

    Returns:
        (reward, terminated) from the final step.
    """
    # --- 1. Filter to Electronics ---
    obs, *_ = _step(env, {"type": "click_by_role", "role": "link", "name": "Electronics"})

    # --- 2. Find product name for SKU-E7421 in the axtree ---
    # Playwright aria_snapshot() encodes table rows with all cell text in the
    # row's accessible name, e.g.:
    #   - row "Car Wire Connector Kit Electronics SKU-E7421 $29.99 View":
    #       - link "Car Wire Connector Kit"
    match = re.search(
        r'- row "[^"]*SKU-E7421[^"]*".*?- link "([^"]+)"',
        obs["axtree"], re.DOTALL
    )
    if not match:
        return 0.0, False
    product_name = match.group(1)

    # --- 3. Open product page ---
    _step(env, {"type": "click_by_role", "role": "link", "name": product_name})

    # --- 4. Set quantity to 2 ---
    _step(env, {"type": "click_by_role", "role": "spinbutton", "name": "Quantity:"})
    _step(env, {"type": "press", "key": "Control+a"})
    _step(env, {"type": "type", "text": "2"})

    # --- 5. Add to cart ---
    _step(env, {"type": "click_by_role", "role": "button", "name": "Add to Cart"})

    # --- 6. Apply coupon ---
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "Coupon code:"})
    _step(env, {"type": "type", "text": "SAVE10"})
    _step(env, {"type": "click_by_role", "role": "button", "name": "Apply Coupon"})

    # --- 7. Proceed to checkout ---
    _step(env, {"type": "click_by_role", "role": "button", "name": "Proceed to Checkout"})

    # --- 8. Fill shipping form ---
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "Full Name *"})
    _step(env, {"type": "type", "text": "Alice Smith"})
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "Street Address *"})
    _step(env, {"type": "type", "text": "10 Any Street"})
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "City *"})
    _step(env, {"type": "type", "text": "Springfield"})
    _step(env, {"type": "select_option", "selector": "select[name='state']", "value": "IL"})
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "ZIP Code *"})
    _step(env, {"type": "type", "text": "62701"})

    obs, reward, terminated, truncated, info = _step(
        env, {"type": "click_by_role", "role": "button", "name": "Place Order"}
    )
    return reward, terminated


def run_buy_cheapest_oracle(env, obs):
    """
    Scripted oracle for buy_cheapest_in_category.

    Uses only UI interactions — no direct API calls. Finds the cheapest
    Electronics product via the category filter and price sort.

    Steps:
      1. Click "Electronics" nav link → product listing filtered to Electronics
      2. Click "Price: Low→High" sort link → cheapest product appears first
      3. Click "View" (first match = cheapest product)
      4. Click "Add to Cart" (quantity defaults to 1)
      5. Click "Proceed to Checkout"
      6. Fill shipping form with 123 Main St, Springfield, IL 62701
         (verifier checks each address component independently)
      7. Click "Place Order"

    Returns:
        (reward, terminated) from the final step.
    """
    # --- 1 & 2. Filter to Electronics, sort cheapest first ---
    _step(env, {"type": "click_by_role", "role": "link", "name": "Electronics"})
    # "→" is U+2192, matching the literal text rendered by index.html
    _step(env, {"type": "click_by_role", "role": "link", "name": "Price: Low\u2192High"})

    # --- 3. Open cheapest product (first "View" link) ---
    _step(env, {"type": "click_by_role", "role": "link", "name": "View"})

    # --- 4. Add to cart (qty=1 default is correct for this task) ---
    _step(env, {"type": "click_by_role", "role": "button", "name": "Add to Cart"})

    # --- 5. Proceed to checkout ---
    _step(env, {"type": "click_by_role", "role": "button", "name": "Proceed to Checkout"})

    # --- 6. Fill shipping form ---
    # The verifier checks substring matches: "123 Main St", "Springfield", "IL", "62701"
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "Full Name *"})
    _step(env, {"type": "type", "text": "Alice Smith"})
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "Street Address *"})
    _step(env, {"type": "type", "text": "123 Main St"})
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "City *"})
    _step(env, {"type": "type", "text": "Springfield"})
    _step(env, {"type": "select_option", "selector": "select[name='state']", "value": "IL"})
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "ZIP Code *"})
    _step(env, {"type": "type", "text": "62701"})

    obs, reward, terminated, truncated, info = _step(
        env, {"type": "click_by_role", "role": "button", "name": "Place Order"}
    )
    return reward, terminated
