"""
Parallel gym demo: 4 concurrent ShopEnv instances.

Uses multiprocessing.Pool (spawn context) — one OS process per env.
Flask runs as subprocesses (no Docker) for fast startup.

Usage:
    gym_env/.venv/bin/python demo/parallel_demo.py

Expected output:
    Instance 1 | cancel_order  | oracle | 5/5 = 100%
    Instance 2 | cancel_order  | random | 0/5 = 0%
    Instance 3 | apply_coupon  | oracle | 3/3 = 100%
    Instance 4 | buy_cheapest  | oracle | 3/3 = 100%
"""

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
        sys.path.insert(0, str(ROOT))
        from gym_env.env import ShopEnv
        from tasks.cancel_order import CancelRecentOrderTask
        from tasks.buy_cheapest import BuyCheapestInCategoryTask
        from tasks.apply_coupon import ApplyCouponWithQuantityTask
        from demo.oracles import (
            run_cancel_oracle,
            run_apply_coupon_oracle,
            run_buy_cheapest_oracle,
        )

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
                reward, terminated = run_cancel_oracle(env, obs)
            elif policy_name == "oracle" and task_name == "apply_coupon":
                reward, terminated = run_apply_coupon_oracle(env, obs)
            elif policy_name == "oracle" and task_name == "buy_cheapest":
                reward, terminated = run_buy_cheapest_oracle(env, obs)
            else:
                reward, terminated = _run_random_policy(env)

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


def _run_random_policy(env, max_steps: int = 20):
    """Random policy: sample random actions for up to max_steps steps."""
    for _ in range(max_steps):
        action = env.action_space.sample()
        obs, reward, terminated, truncated, info = env.step(action)
        if terminated:
            return reward, True
    return 0.0, False


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
