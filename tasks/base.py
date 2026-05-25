"""
tasks/base.py — AbstractTask interface for TASK2 agent evaluation.

All concrete task classes inherit from this. A task defines:
  - what seed configuration to use (seed_requirements)
  - how to take a pre-episode snapshot and return a goal string (setup)
  - how to check if the agent succeeded (verify)

Unlike the TASK1 Gymnasium version, there is no Page argument anywhere.
The task runner owns the browser (via the MCP server). Tasks only need
the shop's base_url to call GET /api/db-state.
"""

from abc import ABC, abstractmethod


class AbstractTask(ABC):
    """
    A task defines:
      - what seed configuration to use (via seed_requirements)
      - how to take a pre-episode snapshot and return a goal string (setup)
      - how to check if the agent succeeded (verify)

    No Playwright Page is passed to any method. The task runner owns the
    browser; tasks interact with the shop only via its HTTP API.
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
    def setup(self, base_url: str) -> str:
        """
        Called once per episode after the DB has been seeded.

        Take any pre-episode state snapshot needed by verify() (e.g. record
        the set of existing order IDs so verify() can detect new ones).

        Return the goal string — a natural-language description of what the
        agent must do. This is shown to the agent as its task prompt.

        Args:
            base_url: Shop base URL, e.g. "http://localhost:5199".
        """
        ...

    @abstractmethod
    def verify(self, base_url: str) -> dict:
        """
        Called after the agent session ends. Check if the task was completed.

        Use GET {base_url}/api/db-state — do NOT open the SQLite file directly.
        The shop owns its data model; verifiers use its public API.

        Args:
            base_url: Shop base URL, e.g. "http://localhost:5199".

        Returns:
            dict with at minimum {"passed": bool}, plus task-specific fields
            that explain why the task passed or failed. Examples:
              cancel_order:  {"passed": True, "order_id": "...", "status": "cancelled"}
              buy_cheapest:  {"passed": False, "order_id": None, "price_ok": False, "address_ok": False}
              apply_coupon:  {"passed": True, "order_id": "...", "qty_ok": True, ...}
        """
        ...
