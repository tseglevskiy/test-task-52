"""
tasks/cancel_order.py — CancelRecentOrderTask

Goal: "Cancel the most recent existing order in the account."

Verifier checks:
  1. The order recorded at setup() time (most-recent placed order by created_at)
     now has status == "cancelled".
"""

import requests

from tasks.base import AbstractTask


class CancelRecentOrderTask(AbstractTask):
    """Cancel the most recent pre-seeded placed order."""

    def seed_requirements(self) -> dict:
        # The seeder always puts required_orders[0] in slot 0 (most recent timestamp).
        return {
            "required_orders": [{"status": "placed"}],
        }

    def rubric(self) -> str:
        return """\
1. The agent must navigate to the orders list page (a URL containing /orders) before selecting a specific order.
2. The agent must cancel the order using UI interaction (clicking a Cancel button), NOT by constructing a direct URL to a cancel endpoint.
3. The agent should not make unnecessary detours (e.g., adding items to cart, going to checkout) unrelated to cancellation.
4. The agent must successfully reach a confirmation that the order was cancelled (e.g., status changed to cancelled, or a success message appeared)."""

    def setup(self, base_url: str) -> str:
        state = requests.get(f"{base_url}/api/db-state").json()
        placed = [o for o in state["orders"] if o["status"] == "placed"]

        if not placed:
            raise RuntimeError(
                "No placed orders found after seeding — check seed config"
            )

        # Most recent = largest created_at timestamp.
        self._target_order_id: str = max(placed, key=lambda o: o["created_at"])["id"]

        return "Cancel the most recent existing order in the account."

    def verify(self, base_url: str) -> dict:
        state = requests.get(f"{base_url}/api/db-state").json()
        order = next(
            (o for o in state["orders"] if o["id"] == self._target_order_id), None
        )

        if order is None:
            return {"passed": False, "order_id": self._target_order_id, "status": None}

        passed = order["status"] == "cancelled"
        return {"passed": passed, "order_id": order["id"], "status": order["status"]}
