"""
tasks/buy_cheapest.py — BuyCheapestInCategoryTask

Goal: "Buy the cheapest item in the 'Electronics' category and
      ship it to 123 Main St, Springfield, IL 62701."

Verifier checks:
  1. A new order appeared since setup() snapshot.
  2. The ordered item is in the Electronics category AND has the minimum
     price among Electronics products at episode start.
  3. The shipping address contains all four expected components.
"""

import requests

from tasks.base import AbstractTask


class BuyCheapestInCategoryTask(AbstractTask):
    """Buy the cheapest Electronics item and ship to a fixed address."""

    def seed_requirements(self) -> dict:
        # Electronics is the first category in vocab.py — always included
        # with default n_categories=5. No extra requirements needed.
        return {}

    def check_trajectory(self, trajectory: list[dict]) -> dict:
        """Deterministic checks for buy_cheapest trajectory."""
        violations = []

        # Gather all URLs navigated to and all text typed
        navigate_urls = [
            s["args"].get("url", "")
            for s in trajectory
            if s.get("tool") == "navigate"
        ]
        all_args_str = " ".join(
            str(s.get("args", {}))
            for s in trajectory
        ).lower()
        all_results_str = " ".join(
            (s.get("result") or "")
            for s in trajectory
        ).lower()

        # Collect type_text and select_option steps for address checking
        typed_texts = [
            s["args"].get("text", "")
            for s in trajectory
            if s.get("tool") == "type_text"
        ]
        selected_values = [
            s["args"].get("value", "")
            for s in trajectory
            if s.get("tool") == "select_option"
        ]
        all_entered = typed_texts + selected_values

        # Check 1: Agent browsed the Electronics category.
        # Accept: URL with category=Electronics, or "electronics" in any result/arg.
        browsed_electronics = (
            any("category=electronics" in url.lower() for url in navigate_urls)
            or "electronics" in all_args_str
            or "electronics" in all_results_str
        )
        if not browsed_electronics:
            violations.append(
                "Agent never browsed the Electronics category "
                "(no navigation to /?category=Electronics and no Electronics content seen)."
            )

        # Check 2: All four address components were entered.
        address_components = {
            "123 Main St": False,
            "Springfield": False,
            "IL": False,
            "62701": False,
        }
        for component in address_components:
            if any(component in text for text in all_entered):
                address_components[component] = True

        missing = [c for c, found in address_components.items() if not found]
        if missing:
            violations.append(
                f"Agent did not enter all address components — "
                f"missing: {', '.join(missing)}."
            )

        passed = len(violations) == 0
        checks = [
            f"{'✓' if browsed_electronics else '✗'} Browsed Electronics category",
            f"{'✓' if not missing else '✗'} Entered all address components"
            + (f" (missing: {', '.join(missing)})" if missing else ""),
        ]
        return {
            "passed": passed,
            "violations": violations,
            "reasoning": "; ".join(checks),
        }

    def rubric(self) -> str:
        return """\
1. The agent must browse or filter the Electronics category to identify available products.
2. The agent must identify and select the cheapest item in Electronics (not just any item).
3. The agent must complete checkout with the exact shipping address: 123 Main St, Springfield, IL 62701.
4. All four address components must be entered: street (123 Main St), city (Springfield), state (IL), and ZIP (62701).
5. The agent must not purchase an item from a different category."""

    def setup(self, base_url: str) -> str:
        state = requests.get(f"{base_url}/api/db-state").json()

        # Record all existing order IDs so verify() can detect new ones.
        self._pre_order_ids: set[str] = {o["id"] for o in state["orders"]}

        # Record the minimum price among Electronics products.
        # Used by verify() to confirm the agent bought the cheapest item.
        electronics = [p for p in state["products"] if p["category"] == "Electronics"]
        self._min_price: float = min(p["price"] for p in electronics)

        return (
            "Buy the cheapest item in the 'Electronics' category and "
            "ship it to 123 Main St, Springfield, IL 62701."
        )

    def verify(self, base_url: str) -> dict:
        state = requests.get(f"{base_url}/api/db-state").json()
        new_orders = [o for o in state["orders"] if o["id"] not in self._pre_order_ids]

        if not new_orders:
            return {
                "passed": False,
                "order_id": None,
                "price_ok": False,
                "address_ok": False,
            }

        order = new_orders[0]
        items = [i for i in state["order_items"] if i["order_id"] == order["id"]]

        # Build a lookup from product_id → category for cross-referencing order items.
        product_category = {p["id"]: p["category"] for p in state["products"]}

        # Check that the purchased item is from Electronics AND has the minimum price.
        price_ok = any(
            abs(i["unit_price"] - self._min_price) < 0.01
            and product_category.get(i["product_id"]) == "Electronics"
            for i in items
        )

        # Check all address components separately (the form has separate fields for
        # street, city, state, and ZIP — each must be verified independently).
        addr = order["shipping_address"]
        address_ok = all([
            "123 Main St" in addr,
            "Springfield" in addr,
            "IL" in addr,
            "62701" in addr,
        ])

        passed = price_ok and address_ok
        return {
            "passed": passed,
            "order_id": order["id"],
            "price_ok": price_ok,
            "address_ok": address_ok,
        }
