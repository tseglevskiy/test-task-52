"""
Integration smoke test: env.reset() + a few env.step() calls.

Requires: shop Docker container running on port 5001 with _tmp/gym_1/shop.db mounted.

Setup:
    mkdir -p _tmp/gym_1
    touch _tmp/gym_1/shop.db _tmp/gym_1/shop.jsonl
    docker build -t shopgym:latest shop/
    docker run -d --name shopgym_test \\
        -v $(pwd)/_tmp/gym_1/shop.db:/app/shop.db \\
        -v $(pwd)/_tmp/gym_1/shop.jsonl:/app/shop.jsonl \\
        -p 5001:5000 shopgym:latest

Run with:
    gym_env/.venv/bin/python -m pytest gym_env/tests/test_env.py -v -s

Teardown:
    docker stop shopgym_test && docker rm shopgym_test
"""

import json
import pytest


def test_reset_returns_valid_obs():
    from gym_env.env import ShopEnv
    from tasks.cancel_order import CancelRecentOrderTask

    env = ShopEnv(
        base_url="http://localhost:5001",
        task_class=CancelRecentOrderTask,
    )

    try:
        obs, info = env.reset(seed=42)

        # --- Validate obs keys ---
        assert "url" in obs, "obs missing 'url'"
        assert "axtree" in obs, "obs missing 'axtree'"
        assert "screenshot" in obs, "obs missing 'screenshot'"
        assert "goal" in obs, "obs missing 'goal'"

        # --- Validate obs values ---
        assert obs["url"].startswith("http://localhost:5001"), (
            f"unexpected url: {obs['url']}"
        )
        assert obs["screenshot"].shape == (540, 960, 3), (
            f"unexpected screenshot shape: {obs['screenshot'].shape}"
        )
        assert obs["screenshot"].dtype.name == "uint8", (
            f"unexpected screenshot dtype: {obs['screenshot'].dtype}"
        )
        assert isinstance(obs["goal"], str) and len(obs["goal"]) > 0, (
            "goal should be a non-empty string"
        )

        # --- Goal is also in info ---
        assert "goal" in info
        assert info["goal"] == obs["goal"]

        # --- obs is in observation_space (validates charset on Text spaces) ---
        assert obs in env.observation_space, (
            "obs not contained in observation_space — check charset=string.printable"
        )

        # --- Step with a no-op navigate ---
        action = json.dumps({"type": "navigate", "url": "http://localhost:5001/"})
        obs2, reward, terminated, truncated, info2 = env.step(action)

        assert reward == 0.0
        assert terminated is False
        assert truncated is False
        assert obs2 in env.observation_space, (
            "obs2 not contained in observation_space after step"
        )

    finally:
        env.close()
