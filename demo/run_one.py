"""
demo/run_one.py — Run a single task+policy episode with full logging.

Usage:
    gym_env/.venv/bin/python demo/run_one.py [task] [policy] [seed]

    task:   cancel_order | apply_coupon | buy_cheapest  (default: cancel_order)
    policy: oracle | random                             (default: oracle)
    seed:   integer                                     (default: 0)

Examples:
    gym_env/.venv/bin/python demo/run_one.py cancel_order oracle 0
    gym_env/.venv/bin/python demo/run_one.py apply_coupon oracle 0
    gym_env/.venv/bin/python demo/run_one.py buy_cheapest oracle 0

Flask runs on port 5199. All Flask output is printed to stdout so you can
see exactly what requests are being made at each step.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
PORT = 5199
BASE_URL = f"http://localhost:{PORT}"


# ---------------------------------------------------------------------------
# Flask lifecycle
# ---------------------------------------------------------------------------

def _start_flask(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch(exist_ok=True)

    flask_cmd = (
        "import os, sys; sys.path.insert(0, os.getcwd()); "
        "from app import create_app; "
        f"create_app(os.environ['DATABASE_PATH']).run("
        f"host='0.0.0.0', port={PORT}, debug=False, use_reloader=False)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", flask_cmd],
        cwd=ROOT / "shop",
        env={**os.environ, "DATABASE_PATH": str(db_path)},
        stdout=sys.stdout,   # Flask logs flow to our stdout
        stderr=sys.stderr,
    )
    print(f"[run_one] Flask started (PID {proc.pid}) on port {PORT}")
    return proc


def _wait_for_flask():
    print(f"[run_one] Waiting for Flask at {BASE_URL}/api/health ...")
    for i in range(40):
        try:
            r = requests.get(f"{BASE_URL}/api/health", timeout=1)
            if r.json()["status"] == "ok":
                print(f"[run_one] Flask is up (attempt {i+1})")
                return True
        except Exception as e:
            print(f"[run_one]   attempt {i+1}: {e}")
            time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Traced step wrapper
# ---------------------------------------------------------------------------

def _step(env, action_dict, label=""):
    action = json.dumps(action_dict)
    print(f"[step] {label or action_dict['type']} → {action}")
    result = env.step(action)
    obs, reward, terminated, truncated, info = result
    print(f"       url={obs['url']}  reward={reward}  terminated={terminated}")
    return result


# ---------------------------------------------------------------------------
# Traced oracle wrappers (delegate to demo.oracles, but print each step)
# ---------------------------------------------------------------------------

def _run_cancel_oracle(env, obs):
    print("\n[oracle] cancel_order — My Orders → View → Cancel Order")
    _step(env, {"type": "click_by_role", "role": "link", "name": "My Orders"}, "click My Orders")
    _step(env, {"type": "click_by_role", "role": "link", "name": "View"}, "click View (first order)")
    obs, reward, terminated, truncated, info = _step(
        env, {"type": "click_by_role", "role": "button", "name": "Cancel Order"}, "click Cancel Order"
    )
    return reward, terminated


def _run_apply_coupon_oracle(env, obs):
    import re
    print("\n[oracle] apply_coupon — Electronics → SKU-E7421 → qty=2 → SAVE10 → checkout")

    obs, *_ = _step(env, {"type": "click_by_role", "role": "link", "name": "Electronics"}, "click Electronics")

    print("[oracle] Searching axtree for SKU-E7421 ...")
    match = re.search(r'- row "[^"]*SKU-E7421[^"]*".*?- link "([^"]+)"', obs["axtree"], re.DOTALL)
    if not match:
        print("[oracle] ERROR: SKU-E7421 not found in axtree!")
        print("--- axtree snippet ---")
        print(obs["axtree"][:3000])
        return 0.0, False
    product_name = match.group(1)
    print(f"[oracle] Found product: {product_name!r}")

    _step(env, {"type": "click_by_role", "role": "link", "name": product_name}, f"click '{product_name}'")
    _step(env, {"type": "click_by_role", "role": "spinbutton", "name": "Quantity:"}, "focus qty")
    _step(env, {"type": "press", "key": "Control+a"}, "select all")
    _step(env, {"type": "type", "text": "2"}, "type 2")
    _step(env, {"type": "click_by_role", "role": "button", "name": "Add to Cart"}, "Add to Cart")
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "Coupon code:"}, "focus coupon")
    _step(env, {"type": "type", "text": "SAVE10"}, "type SAVE10")
    _step(env, {"type": "click_by_role", "role": "button", "name": "Apply Coupon"}, "Apply Coupon")
    _step(env, {"type": "click_by_role", "role": "button", "name": "Proceed to Checkout"}, "Proceed to Checkout")
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "Full Name *"}, "focus name")
    _step(env, {"type": "type", "text": "Alice Smith"}, "type name")
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "Street Address *"}, "focus street")
    _step(env, {"type": "type", "text": "10 Any Street"}, "type street")
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "City *"}, "focus city")
    _step(env, {"type": "type", "text": "Springfield"}, "type city")
    _step(env, {"type": "select_option", "selector": "select[name='state']", "value": "IL"}, "select IL")
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "ZIP Code *"}, "focus zip")
    _step(env, {"type": "type", "text": "62701"}, "type zip")
    obs, reward, terminated, truncated, info = _step(
        env, {"type": "click_by_role", "role": "button", "name": "Place Order"}, "Place Order"
    )
    return reward, terminated


def _run_buy_cheapest_oracle(env, obs):
    print("\n[oracle] buy_cheapest — Electronics → Price: Low→High → View → checkout")
    _step(env, {"type": "click_by_role", "role": "link", "name": "Electronics"}, "click Electronics")
    _step(env, {"type": "click_by_role", "role": "link", "name": "Price: Low\u2192High"}, "click Price: Low→High")
    _step(env, {"type": "click_by_role", "role": "link", "name": "View"}, "click View (cheapest)")
    _step(env, {"type": "click_by_role", "role": "button", "name": "Add to Cart"}, "Add to Cart")
    _step(env, {"type": "click_by_role", "role": "button", "name": "Proceed to Checkout"}, "Proceed to Checkout")
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "Full Name *"}, "focus name")
    _step(env, {"type": "type", "text": "Alice Smith"}, "type name")
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "Street Address *"}, "focus street")
    _step(env, {"type": "type", "text": "123 Main St"}, "type street")
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "City *"}, "focus city")
    _step(env, {"type": "type", "text": "Springfield"}, "type city")
    _step(env, {"type": "select_option", "selector": "select[name='state']", "value": "IL"}, "select IL")
    _step(env, {"type": "click_by_role", "role": "textbox", "name": "ZIP Code *"}, "focus zip")
    _step(env, {"type": "type", "text": "62701"}, "type zip")
    obs, reward, terminated, truncated, info = _step(
        env, {"type": "click_by_role", "role": "button", "name": "Place Order"}, "Place Order"
    )
    return reward, terminated


def _run_random_policy(env, max_steps=20):
    print(f"\n[policy] random — up to {max_steps} steps")
    for i in range(max_steps):
        action = env.action_space.sample()
        obs, reward, terminated, truncated, info = env.step(action)
        print(f"  step {i+1}: url={obs['url']}  reward={reward}  terminated={terminated}")
        if terminated:
            return reward, True
    return 0.0, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    task_name = sys.argv[1] if len(sys.argv) > 1 else "cancel_order"
    policy    = sys.argv[2] if len(sys.argv) > 2 else "oracle"
    seed      = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    print(f"\n[run_one] task={task_name}  policy={policy}  seed={seed}")
    print(f"[run_one] Flask port: {PORT}")

    db_path = ROOT / "_tmp" / "run_one" / "shop.db"
    flask_proc = _start_flask(db_path)

    try:
        if not _wait_for_flask():
            print("[run_one] ERROR: Flask did not start in time")
            return

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
        if task_name not in task_map:
            print(f"[run_one] Unknown task {task_name!r}. Choose: {list(task_map)}")
            return

        print(f"\n[run_one] Creating ShopEnv ...")
        env = ShopEnv(base_url=BASE_URL, task_class=task_map[task_name])

        print(f"[run_one] env.reset(seed={seed}) ...")
        obs, info = env.reset(seed=seed)
        print(f"[run_one] goal: {info['goal']}")
        print(f"[run_one] initial url: {obs['url']}")

        if policy == "oracle" and task_name == "cancel_order":
            reward, terminated = _run_cancel_oracle(env, obs)
        elif policy == "oracle" and task_name == "apply_coupon":
            reward, terminated = _run_apply_coupon_oracle(env, obs)
        elif policy == "oracle" and task_name == "buy_cheapest":
            reward, terminated = _run_buy_cheapest_oracle(env, obs)
        else:
            reward, terminated = _run_random_policy(env)

        env.close()

        print(f"\n[run_one] === RESULT ===")
        print(f"  task={task_name}  policy={policy}  seed={seed}")
        print(f"  reward={reward}  terminated={terminated}")
        print(f"  {'SUCCESS' if terminated and reward > 0 else 'FAILURE'}")

    finally:
        print(f"\n[run_one] Killing Flask (PID {flask_proc.pid}) ...")
        flask_proc.kill()
        flask_proc.wait()
        print("[run_one] Done.")


if __name__ == "__main__":
    main()
