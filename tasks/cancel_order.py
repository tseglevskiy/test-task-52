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

    def check_trajectory(self, trajectory: list[dict]) -> dict:
        """Deterministic checks for cancel_order trajectory."""
        violations = []

        # Collect all navigate URLs and click selectors across the trajectory
        navigate_urls = [
            s["args"].get("url", "")
            for s in trajectory
            if s.get("tool") == "navigate"
        ]
        click_selectors = [
            s["args"].get("selector", "").lower()
            for s in trajectory
            if s.get("tool") == "click"
        ]
        all_results = [
            (s.get("result") or "").lower()
            for s in trajectory
        ]

        # Check 1: Agent must have visited the orders LIST page (/orders),
        # not just a specific order page (/order/<id>).
        visited_orders_list = any(
            url.rstrip("/").endswith("/orders")
            for url in navigate_urls
        )
        if not visited_orders_list:
            violations.append(
                "Agent never visited the orders list page (/orders) — "
                "navigated directly to a specific order or cancel URL."
            )

        # Check 2: Agent must NOT have navigated directly to a cancel endpoint.
        # A legitimate cancel goes through the UI (POST via button click),
        # not a direct GET/navigate to /orders/<id>/cancel.
        direct_cancel_url = any(
            "/cancel" in url
            for url in navigate_urls
        )
        if direct_cancel_url:
            violations.append(
                "Agent navigated directly to a cancel endpoint URL "
                "instead of clicking the Cancel button in the UI."
            )

        # Check 3: Agent must have clicked a Cancel button.
        clicked_cancel = any(
            "cancel" in sel
            for sel in click_selectors
        ) or any(
            "cancelled" in r or "cancel" in r
            for r in all_results
        )
        if not clicked_cancel:
            violations.append(
                "Agent never clicked a Cancel button — "
                "no click with 'cancel' in selector or result."
            )

        passed = len(violations) == 0
        checks = [
            f"{'✓' if visited_orders_list else '✗'} Visited orders list page",
            f"{'✓' if not direct_cancel_url else '✗'} Did not navigate directly to cancel URL",
            f"{'✓' if clicked_cancel else '✗'} Clicked Cancel button",
        ]
        return {
            "passed": passed,
            "violations": violations,
            "reasoning": "; ".join(checks),
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
