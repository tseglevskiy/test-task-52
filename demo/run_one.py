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
# Traced step — injected into oracles module so all oracle calls are logged
# ---------------------------------------------------------------------------

def _traced_step(env, action_dict):
    action = json.dumps(action_dict)
    print(f"[step] {action_dict['type']} → {action}")
    result = env.step(action)
    obs, reward, terminated, truncated, info = result
    print(f"       url={obs['url']}  reward={reward}  terminated={terminated}")
    return result


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
        import demo.oracles as oracles

        # Inject the traced _step so every oracle action is logged
        oracles._step = _traced_step

        task_map = {
            "cancel_order": CancelRecentOrderTask,
            "buy_cheapest": BuyCheapestInCategoryTask,
            "apply_coupon": ApplyCouponWithQuantityTask,
        }
        oracle_map = {
            "cancel_order": oracles.run_cancel_oracle,
            "buy_cheapest": oracles.run_buy_cheapest_oracle,
            "apply_coupon": oracles.run_apply_coupon_oracle,
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

        if policy == "oracle":
            if task_name not in oracle_map:
                print(f"[run_one] No oracle for task {task_name!r}")
                return
            print(f"\n[oracle] {task_name}")
            reward, terminated = oracle_map[task_name](env, obs)
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
