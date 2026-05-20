"""
tasks/cancel_order.py — CancelRecentOrderTask

Goal: "Cancel the most recent existing order in the account."

Verifier checks:
  1. The order recorded at setup() time (most-recent placed order by created_at)
     now has status == "cancelled".
"""

import requests

from gym_env.tasks.base import AbstractTask


class CancelRecentOrderTask(AbstractTask):
    """Cancel the most recent pre-seeded placed order."""

    def seed_requirements(self) -> dict:
        # The seeder always puts required_orders[0] in slot 0 (most recent timestamp).
        return {
            "required_orders": [{"status": "placed"}],
        }

    def setup(self, page, base_url: str) -> str:
        state = requests.get(f"{base_url}/api/db-state").json()
        placed = [o for o in state["orders"] if o["status"] == "placed"]

        if not placed:
            raise RuntimeError(
                "No placed orders found after seeding — check seed config"
            )

        # Most recent = largest created_at timestamp.
        self._target_order_id: str = max(placed, key=lambda o: o["created_at"])["id"]

        return "Cancel the most recent existing order in the account."

    def verify(self, base_url: str, page) -> tuple[float, bool]:
        state = requests.get(f"{base_url}/api/db-state").json()
        order = next(
            (o for o in state["orders"] if o["id"] == self._target_order_id), None
        )

        if order and order["status"] == "cancelled":
            return 1.0, True
        return 0.0, False
