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

from gym_env.tasks.base import AbstractTask


class BuyCheapestInCategoryTask(AbstractTask):
    """Buy the cheapest Electronics item and ship to a fixed address."""

    def seed_requirements(self) -> dict:
        # Electronics is the first category in vocab.py — always included
        # with default n_categories=5. No extra requirements needed.
        return {}

    def setup(self, page, base_url: str) -> str:
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

    def verify(self, base_url: str, page) -> tuple[float, bool]:
        state = requests.get(f"{base_url}/api/db-state").json()
        new_orders = [o for o in state["orders"] if o["id"] not in self._pre_order_ids]

        if not new_orders:
            return 0.0, False

        order = new_orders[0]  # take the first new order
        items = [i for i in state["order_items"] if i["order_id"] == order["id"]]

        # Build a lookup from product_id → category for cross-referencing order items.
        # Needed because order_items stores product_id but not category directly.
        product_category = {p["id"]: p["category"] for p in state["products"]}

        # Check that the purchased item is from Electronics AND has the minimum price.
        # Both conditions are required: an agent that buys the cheapest product across
        # ALL categories (which might be cheaper than any Electronics item) must not pass.
        price_and_category_ok = any(
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

        if price_and_category_ok and address_ok:
            return 1.0, True
        return 0.0, False
