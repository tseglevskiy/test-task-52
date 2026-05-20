"""
gym_env/tasks/base.py — AbstractTask interface.

All concrete task classes inherit from this. A task defines:
  - what seed configuration to use (seed_requirements)
  - how to take a pre-episode snapshot and return a goal string (setup)
  - how to check if the agent succeeded (verify)
"""

from abc import ABC, abstractmethod
from playwright.sync_api import Page


class AbstractTask(ABC):
    """
    A task defines:
      - what seed configuration to use (via seed_requirements)
      - how to take a pre-episode snapshot and return a goal string (setup)
      - how to check if the agent succeeded (verify)
    """

    @abstractmethod
    def seed_requirements(self) -> dict:
        """
        Return the task-specific seeding requirements as a dict.
        Merged into the POST /api/reset JSON body alongside the seed.

        Example:
            {
                "required_products": [{"category": "Electronics", "sku": "SKU-E7421"}],
                "required_coupons":  [{"code": "SAVE10", "discount_pct": 10.0}],
                "required_orders":   [{"status": "placed"}],
            }

        Only keys present are sent; missing keys fall back to SeedConfig defaults.
        An empty dict ({}) means the task has no requirements beyond the seed.
        """
        ...

    @abstractmethod
    def setup(self, page: Page, base_url: str) -> str:
        """
        Called once per episode after the DB has been seeded and the browser
        has navigated to base_url.

        Take any pre-episode state snapshot needed by verify() (e.g. record
        the set of existing order IDs so verify() can detect new ones).

        Return the goal string — a natural-language description of what the
        agent must do. This is returned in the reset() info dict.
        """
        ...

    @abstractmethod
    def verify(self, base_url: str, page: Page) -> tuple[float, bool]:
        """
        Called after every step(). Check if the task has been completed.

        Use GET {base_url}/api/db-state — do NOT open the SQLite file directly.
        The shop owns its data model; verifiers use its public API.

        Return (reward, terminated):
          (1.0, True)  — task completed successfully
          (0.0, False) — task not yet completed, episode continues
          (0.0, True)  — task definitively failed (optional, use sparingly)
        """
        ...
