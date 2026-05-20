"""
Parallel gym demo: 4 concurrent ShopEnv instances.

Uses multiprocessing.Pool (spawn context) — one OS process per env.
Flask runs as subprocesses (no Docker) for fast startup.

Usage:
    gym_env/.venv/bin/python scripts/parallel_demo.py

Expected output:
    Instance 1 | cancel_order  | oracle | 5/5 = 100%
    Instance 2 | cancel_order  | random | 0/5 = 0%
    Instance 3 | apply_coupon  | oracle | 3/3 = 100%
    Instance 4 | buy_cheapest  | oracle | 3/3 = 100%
"""

import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Worker — runs in a child process
# ---------------------------------------------------------------------------

def run_env_worker(args):
    """
    Runs in a child process. Starts Flask, runs episodes, returns results.

    Args:
        args: (instance_id, task_name, n_episodes, policy_name)

    Returns:
        dict with instance, task, policy, episodes, successes, success_rate.
    """
    instance_id, task_name, n_episodes, policy_name = args

    port = 5100 + instance_id
    db_path = ROOT / "_tmp" / f"demo_{instance_id}" / "shop.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch(exist_ok=True)

    # Start Flask directly via inline Python — the shop uses create_app(db_path)
    # factory pattern which `flask run` cannot call without arguments.
    # We pass DATABASE_PATH via env and inline-Python reads it.
    flask_cmd = (
        "import os, sys; sys.path.insert(0, os.getcwd()); "
        "from app import create_app; "
        f"create_app(os.environ['DATABASE_PATH']).run("
        f"host='0.0.0.0', port={port}, debug=False, use_reloader=False)"
    )
    flask_proc = subprocess.Popen(
        [sys.executable, "-c", flask_cmd],
        cwd=ROOT / "shop",
        env={**os.environ, "DATABASE_PATH": str(db_path)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for health check.
    base_url = f"http://localhost:{port}"
    for _ in range(30):
        try:
            r = requests.get(f"{base_url}/api/health", timeout=1)
            if r.json()["status"] == "ok":
                break
        except Exception:
            time.sleep(0.3)
    else:
        flask_proc.kill()
        return {"instance": instance_id, "error": "Flask failed to start"}

    try:
        # Add project root to sys.path so gym_env and tasks are importable.
        sys.path.insert(0, str(ROOT))
        from gym_env.env import ShopEnv
        from tasks.cancel_order import CancelRecentOrderTask
        from tasks.buy_cheapest import BuyCheapestInCategoryTask

        from tasks.apply_coupon import ApplyCouponWithQuantityTask

        task_map = {
            "cancel_order": CancelRecentOrderTask,
            "buy_cheapest": BuyCheapestInCategoryTask,
            "apply_coupon": ApplyCouponWithQuantityTask,
        }
        task_class = task_map[task_name]

        env = ShopEnv(base_url=base_url, task_class=task_class)

        successes = 0
        for ep in range(n_episodes):
            obs, info = env.reset(seed=ep)

            if policy_name == "oracle" and task_name == "cancel_order":
                reward, terminated = run_cancel_oracle(env, obs, base_url)
            elif policy_name == "oracle" and task_name == "apply_coupon":
                reward, terminated = run_apply_coupon_oracle(env, obs, base_url)
            elif policy_name == "oracle" and task_name == "buy_cheapest":
                reward, terminated = run_buy_cheapest_oracle(env, obs, base_url)
            else:
                reward, terminated = run_random_policy(env, max_steps=20)

            if terminated and reward > 0:
                successes += 1

        env.close()
        return {
            "instance": instance_id,
            "task": task_name,
            "policy": policy_name,
            "episodes": n_episodes,
            "successes": successes,
            "success_rate": successes / n_episodes,
        }
    finally:
        flask_proc.kill()


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

def run_apply_coupon_oracle(env, obs, base_url: str):
    """
    Scripted oracle for apply_coupon_with_quantity.

    Demonstrates what an agent must do to complete this task — this is the
    hardcoded 'ideal policy' that shows the task is solvable and the verifier
    is correct. A real RL agent would learn these steps from observations.

    Steps (all via env.step — no env._page bypass):
      1. Navigate to the SKU-E7421 product page (found via /api/db-state)
      2. Set quantity to 2 (focus spinbutton, select-all, type "2")
      3. Click "Add to Cart" → Flask redirects browser to /cart
      4. Type coupon code "SAVE10" in the coupon text field
      5. Click "Apply Coupon"
      6. Click "Proceed to Checkout"
      7. Fill in the shipping form (name, street, city, state, ZIP)
      8. Click "Place Order" → verifier checks the resulting DB state
    """
    # Oracle privilege: look up the product ID for SKU-E7421 from the API.
    # A real agent would find it by browsing the product listing.
    state = requests.get(f"{base_url}/api/db-state").json()
    product = next(
        (p for p in state["products"] if p["sku"] == "SKU-E7421"), None
    )
    if not product:
        return 0.0, False

    # --- 1. Navigate to product page ---
    env.step(json.dumps({"type": "navigate",
                         "url": f"{base_url}/product/{product['id']}"}))

    # --- 2. Set quantity to 2 ---
    # The quantity field is <input type="number"> → ARIA role "spinbutton"
    # labelled by <label for="quantity">Quantity:</label>.
    env.step(json.dumps({"type": "click_by_role",
                         "role": "spinbutton", "name": "Quantity:"}))
    env.step(json.dumps({"type": "press", "key": "Control+a"}))
    env.step(json.dumps({"type": "type", "text": "2"}))

    # --- 3. Add to cart → Flask redirects to /cart ---
    env.step(json.dumps({"type": "click_by_role",
                         "role": "button", "name": "Add to Cart"}))

    # --- 4 & 5. Apply coupon ---
    # The coupon field: <input type="text" id="code" name="code">
    # labelled by <label for="code">Coupon code:</label>
    env.step(json.dumps({"type": "click_by_role",
                         "role": "textbox", "name": "Coupon code:"}))
    env.step(json.dumps({"type": "type", "text": "SAVE10"}))
    env.step(json.dumps({"type": "click_by_role",
                         "role": "button", "name": "Apply Coupon"}))

    # --- 6. Proceed to checkout ---
    env.step(json.dumps({"type": "click_by_role",
                         "role": "button", "name": "Proceed to Checkout"}))

    # --- 7. Fill shipping form ---
    # Full Name
    env.step(json.dumps({"type": "click_by_role",
                         "role": "textbox", "name": "Full Name *"}))
    env.step(json.dumps({"type": "type", "text": "Alice Smith"}))
    # Street Address
    env.step(json.dumps({"type": "click_by_role",
                         "role": "textbox", "name": "Street Address *"}))
    env.step(json.dumps({"type": "type", "text": "10 Any Street"}))
    # City
    env.step(json.dumps({"type": "click_by_role",
                         "role": "textbox", "name": "City *"}))
    env.step(json.dumps({"type": "type", "text": "Springfield"}))
    # State — <select name="state"> → use select_option by CSS selector
    env.step(json.dumps({"type": "select_option",
                         "selector": "select[name='state']", "value": "IL"}))
    # ZIP Code
    env.step(json.dumps({"type": "click_by_role",
                         "role": "textbox", "name": "ZIP Code *"}))
    env.step(json.dumps({"type": "type", "text": "62701"}))

    # --- 8. Place order — capture reward from verifier ---
    obs, reward, terminated, truncated, info = env.step(
        json.dumps({"type": "click_by_role",
                    "role": "button", "name": "Place Order"})
    )
    return reward, terminated


def run_buy_cheapest_oracle(env, obs, base_url: str):
    """
    Scripted oracle for buy_cheapest_in_category.

    Steps (all via env.step — no env._page bypass):
      1. Find the cheapest Electronics product via /api/db-state (oracle privilege)
      2. Navigate to that product's page
      3. Click "Add to Cart" (quantity defaults to 1 — correct for this task)
      4. Click "Proceed to Checkout"
      5. Fill shipping form with the exact required address:
         123 Main St, Springfield, IL 62701
         (verifier checks each component independently)
      6. Click "Place Order"
    """
    state = requests.get(f"{base_url}/api/db-state").json()
    electronics = [p for p in state["products"] if p["category"] == "Electronics"]
    if not electronics:
        return 0.0, False
    cheapest = min(electronics, key=lambda p: p["price"])

    # --- 1. Navigate to cheapest Electronics product page ---
    env.step(json.dumps({"type": "navigate",
                         "url": f"{base_url}/product/{cheapest['id']}"}))

    # --- 2. Add to cart (qty=1 is the default — no need to change it) ---
    env.step(json.dumps({"type": "click_by_role",
                         "role": "button", "name": "Add to Cart"}))

    # --- 3. Proceed to checkout (no coupon for this task) ---
    env.step(json.dumps({"type": "click_by_role",
                         "role": "button", "name": "Proceed to Checkout"}))

    # --- 4. Fill shipping form ---
    # The verifier checks that the address contains "123 Main St",
    # "Springfield", "IL", and "62701" — use those exact strings.
    env.step(json.dumps({"type": "click_by_role",
                         "role": "textbox", "name": "Full Name *"}))
    env.step(json.dumps({"type": "type", "text": "Alice Smith"}))

    env.step(json.dumps({"type": "click_by_role",
                         "role": "textbox", "name": "Street Address *"}))
    env.step(json.dumps({"type": "type", "text": "123 Main St"}))

    env.step(json.dumps({"type": "click_by_role",
                         "role": "textbox", "name": "City *"}))
    env.step(json.dumps({"type": "type", "text": "Springfield"}))

    env.step(json.dumps({"type": "select_option",
                         "selector": "select[name='state']", "value": "IL"}))

    env.step(json.dumps({"type": "click_by_role",
                         "role": "textbox", "name": "ZIP Code *"}))
    env.step(json.dumps({"type": "type", "text": "62701"}))

    # --- 5. Place order — capture reward from verifier ---
    obs, reward, terminated, truncated, info = env.step(
        json.dumps({"type": "click_by_role",
                    "role": "button", "name": "Place Order"})
    )
    return reward, terminated


def run_random_policy(env, max_steps: int = 20):
    """Random policy: sample random actions for up to max_steps steps."""
    for _ in range(max_steps):
        action = env.action_space.sample()
        obs, reward, terminated, truncated, info = env.step(action)
        if terminated:
            return reward, True
    return 0.0, False


def run_cancel_oracle(env, obs, base_url: str):
    """
    Scripted oracle for cancel_recent_order.

    Uses only data available through the gym's agent API:
    - /api/db-state to find the most-recent placed order ID (oracle privilege)
    - env.step(navigate) to go to the order page
    - env.step(click_by_role) to click the Cancel button by ARIA role+name,
      which is exactly what the axtree exposes to the agent

    No env._page bypass — all actions go through the action dispatcher.
    """
    state = requests.get(f"{base_url}/api/db-state").json()
    placed = sorted(
        [o for o in state["orders"] if o["status"] == "placed"],
        key=lambda o: o["created_at"],
        reverse=True,
    )
    if not placed:
        return 0.0, False

    order_id = placed[0]["id"]

    # Navigate to the order detail page.
    env.step(json.dumps({"type": "navigate", "url": f"{base_url}/order/{order_id}"}))

    # Click the Cancel Order button by ARIA role + accessible name.
    # The axtree exposes this as role="button", name="Cancel Order".
    obs, reward, terminated, truncated, info = env.step(
        json.dumps({"type": "click_by_role", "role": "button", "name": "Cancel Order"})
    )
    return reward, terminated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    configs = [
        (1, "cancel_order", 5, "oracle"),   # scripted oracle — expect 100%
        (2, "cancel_order", 5, "random"),   # random policy  — expect ~0%
        (3, "apply_coupon", 3, "oracle"),   # scripted oracle — expect 100%
        (4, "buy_cheapest", 3, "oracle"),   # scripted oracle — expect 100%
    ]

    # Use "spawn" start method — required on Linux when child processes will
    # launch Playwright (sync_playwright). The default "fork" method copies
    # the parent's file descriptors and can deadlock inside Playwright's
    # browser process manager. "spawn" starts a fresh Python interpreter.
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=4) as pool:
        results = pool.map(run_env_worker, configs)

    print("\n=== Parallel Demo Results ===")
    for r in results:
        if "error" in r:
            print(f"  Instance {r['instance']}: ERROR - {r['error']}")
        else:
            print(
                f"  Instance {r['instance']} | {r['task']:15s} | {r['policy']:6s} | "
                f"{r['successes']}/{r['episodes']} = {r['success_rate']:.0%}"
            )


if __name__ == "__main__":
    main()
