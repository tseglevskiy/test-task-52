"""
Parallel gym demo: 4 concurrent ShopEnv instances.

Uses multiprocessing.Pool (spawn context) — one OS process per env.
Each env instance gets its own Docker container (shopgym:latest) on a
unique port with a unique DB file — true production-like isolation.

Prerequisites:
    docker build -t shopgym:latest shop/

Usage:
    gym_env/.venv/bin/python demo/parallel_demo.py

Expected output:
    Instance 1 | cancel_order   | oracle | 5/5 = 100%
    Instance 2 | cancel_order   | random | 0/5 = 0%
    Instance 3 | apply_coupon   | oracle | 3/3 = 100%
    Instance 4 | buy_cheapest   | oracle | 3/3 = 100%
"""

import multiprocessing
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent

IMAGE = "shopgym:latest"


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def _start_container(instance_id: int, db_path: Path, jsonl_path: Path, port: int) -> str:
    """
    Start a named Docker container for this instance.
    Returns the container name.
    Raises RuntimeError if docker run fails.
    """
    name = f"shopgym_demo_{instance_id}"

    # Pre-create host files — Docker bind-mount requires them to exist
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch(exist_ok=True)
    jsonl_path.touch(exist_ok=True)

    # Remove any leftover container from a previous run
    subprocess.run(
        ["docker", "rm", "-f", name],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    result = subprocess.run(
        [
            "docker", "run", "-d",
            "--name", name,
            "-v", f"{db_path.resolve()}:/app/shop.db",
            "-v", f"{jsonl_path.resolve()}:/app/shop.jsonl",
            "-p", f"{port}:5000",
            IMAGE,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker run failed: {result.stderr.strip()}")
    return name


def _stop_container(name: str) -> None:
    subprocess.run(["docker", "stop", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["docker", "rm",   name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_for_health(base_url: str, retries: int = 40, delay: float = 0.5) -> bool:
    for _ in range(retries):
        try:
            r = requests.get(f"{base_url}/api/health", timeout=1)
            if r.json()["status"] == "ok":
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


# ---------------------------------------------------------------------------
# Worker — runs in a child process
# ---------------------------------------------------------------------------

def _log(instance_id: int, msg: str) -> None:
    """Print a timestamped progress line for this instance (visible during the run)."""
    import time as _time
    ts = _time.strftime("%H:%M:%S")
    print(f"  [{ts}] instance {instance_id}: {msg}", flush=True)


def run_env_worker(args):
    """
    Runs in a child process. Starts a Docker container, runs episodes, returns results.

    Args:
        args: (instance_id, task_name, n_episodes, policy_name)

    Returns:
        dict with instance, task, policy, episodes, successes, success_rate.
    """
    instance_id, task_name, n_episodes, policy_name = args

    port = 5100 + instance_id
    db_path    = ROOT / "_tmp" / f"demo_{instance_id}" / "shop.db"
    jsonl_path = ROOT / "_tmp" / f"demo_{instance_id}" / "shop.jsonl"
    base_url   = f"http://localhost:{port}"

    _log(instance_id, f"starting container on port {port} ...")
    try:
        container_name = _start_container(instance_id, db_path, jsonl_path, port)
    except RuntimeError as e:
        _log(instance_id, f"ERROR: {e}")
        return {"instance": instance_id, "error": str(e)}

    try:
        _log(instance_id, "waiting for health check ...")
        if not _wait_for_health(base_url):
            _log(instance_id, "ERROR: health check timed out")
            return {"instance": instance_id, "error": "Container health check timed out"}
        _log(instance_id, "container ready")

        sys.path.insert(0, str(ROOT))
        from gym_env.env import ShopEnv
        from tasks.cancel_order import CancelRecentOrderTask
        from tasks.buy_cheapest import BuyCheapestInCategoryTask
        from tasks.apply_coupon import ApplyCouponWithQuantityTask
        import demo.oracles as oracles

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
        task_class = task_map[task_name]

        env = ShopEnv(base_url=base_url, task_class=task_class)

        successes = 0
        for ep in range(n_episodes):
            _log(instance_id, f"episode {ep + 1}/{n_episodes} ({task_name}, {policy_name}) ...")
            obs, info = env.reset(seed=ep)

            if policy_name == "oracle" and task_name in oracle_map:
                reward, terminated = oracle_map[task_name](env, obs)
            else:
                reward, terminated = _run_random_policy(env)

            result_str = "✓" if (terminated and reward > 0) else "✗"
            _log(instance_id, f"episode {ep + 1}/{n_episodes} done — reward={reward} {result_str}")
            if terminated and reward > 0:
                successes += 1

        env.close()
        _log(instance_id, f"done — {successes}/{n_episodes} successes, stopping container ...")
        return {
            "instance": instance_id,
            "task": task_name,
            "policy": policy_name,
            "episodes": n_episodes,
            "successes": successes,
            "success_rate": successes / n_episodes,
        }
    finally:
        _stop_container(container_name)


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
