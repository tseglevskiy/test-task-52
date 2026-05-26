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
