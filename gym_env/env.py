"""
gym_env/env.py — ShopEnv(gymnasium.Env)

A Gymnasium environment that drives a running shop Flask container via
a Playwright browser. One env instance = one browser + one shop container.

Usage:
    env = ShopEnv(base_url="http://localhost:5001", task_class=CancelRecentOrderTask)
    obs, info = env.reset(seed=42)
    obs, reward, terminated, truncated, info = env.step(action_json_str)
    env.close()
"""

from __future__ import annotations

import string

import numpy as np
import requests
from gymnasium import spaces
import gymnasium

from playwright.sync_api import sync_playwright

from .observation import build_observation
from .actions import execute_action


class ShopEnv(gymnasium.Env):
    """
    Gymnasium environment wrapping a ShopGym Flask container.

    Observation space:
        url        — Text(max_length=2048, charset=string.printable)
        axtree     — Text(max_length=500_000, charset=string.printable)
        screenshot — Box(0, 255, shape=(H, W, 3), dtype=uint8)
        goal       — Text(max_length=512, charset=string.printable)

    Action space:
        Text(max_length=2048, charset=string.printable)
        JSON-encoded action string; see gym_env/actions.py for supported types.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        base_url: str,
        task_class: type,
        screenshot_shape: tuple = (540, 960),  # (H, W) — height first
        render_mode: str | None = None,
    ):
        """
        Args:
            base_url:          Shop container URL, e.g. "http://localhost:5001".
            task_class:        Concrete AbstractTask subclass (not an instance).
            screenshot_shape:  (H, W) in pixels. Default (540, 960).
            render_mode:       Unused; kept for Gymnasium API compatibility.
        """
        super().__init__()

        # Store parameters
        self._base_url = base_url
        self._H, self._W = screenshot_shape
        self.render_mode = render_mode

        # Instantiate task — one object per env; setup() refreshes its state each episode.
        self._task = task_class()

        # Browser handles — all None until reset() is called.
        self._playwright = None
        self._browser = None
        self._page = None
        self._goal: str = ""

        # Define spaces.
        # IMPORTANT: charset=string.printable must be on every Text space.
        # Gymnasium's Text defaults to alphanumeric only. Real URLs contain "://",
        # actions contain "{}", '"', spaces — without the explicit charset,
        # `obs in observation_space` silently returns False.
        self.observation_space = spaces.Dict({
            "url":        spaces.Text(max_length=2048, charset=string.printable),
            "axtree":     spaces.Text(max_length=500_000, charset=string.printable),
            "screenshot": spaces.Box(0, 255, shape=(self._H, self._W, 3), dtype=np.uint8),
            "goal":       spaces.Text(max_length=512, charset=string.printable),
        })
        self.action_space = spaces.Text(max_length=2048, charset=string.printable)

    # ------------------------------------------------------------------
    # Gymnasium API
    # ------------------------------------------------------------------

    def reset(self, *, seed=None, options=None):
        """
        Reset the environment for a new episode.

        1. Close the previous browser (idempotent).
        2. POST /api/reset to wipe and reseed the DB.
        3. Launch a fresh Playwright browser and navigate to base_url.
        4. Call task.setup() to snapshot pre-state and get the goal string.
        5. Return the first observation.

        Args:
            seed:    Integer seed forwarded to /api/reset. Defaults to 0.
            options: Unused (Gymnasium API compatibility).

        Returns:
            (obs, info) where info contains {"goal": str}.
        """
        super().reset(seed=seed)  # required — seeds self.np_random

        # 1. Close previous browser (idempotent — checks for None).
        self._close_browser()

        # 2. Reseed the DB via the shop's HTTP reset endpoint.
        #    Pass seed=0 if seed is None (reproducible default).
        effective_seed = seed if seed is not None else 0
        body = {"seed": effective_seed}
        body.update(self._task.seed_requirements())  # task contributes required_* fields
        resp = requests.post(f"{self._base_url}/api/reset", json=body, timeout=10)
        resp.raise_for_status()

        # 3. Launch fresh Playwright browser.
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._page = self._browser.new_page(
            viewport={"width": self._W, "height": self._H}
        )
        self._page.goto(self._base_url)
        self._page.wait_for_load_state("networkidle")

        # 4. Task setup — takes pre-state snapshot, returns goal string.
        self._goal = self._task.setup(self._page, self._base_url)

        return self._observe(), {"goal": self._goal}

    def step(self, action: str):
        """
        Execute one action and return (obs, reward, terminated, truncated, info).

        Action errors are non-fatal — the agent is penalised by not completing
        the task rather than by an exception propagating.

        Args:
            action: JSON-encoded action string.

        Returns:
            (obs, reward, terminated, truncated, info)
        """
        try:
            execute_action(self._page, action)
        except Exception:
            # Non-fatal — bad actions simply don't move the page.
            pass

        obs = self._observe()
        reward, terminated = self._task.verify(self._base_url, self._page)
        truncated = False  # TimeLimit wrapper handles this if needed
        info = {}
        return obs, float(reward), terminated, truncated, info

    def close(self):
        """Release browser resources."""
        self._close_browser()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _close_browser(self) -> None:
        """Idempotent teardown of page / browser / playwright."""
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _observe(self) -> dict:
        """Build and return the current observation."""
        return build_observation(self._page, self._goal)
