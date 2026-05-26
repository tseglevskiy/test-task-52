"""
tasks/apply_coupon.py — ApplyCouponWithQuantityTask

Goal: "Add 2 units of SKU-E7421 to the cart, apply coupon SAVE10,
      and complete checkout."

Verifier checks:
  1. A new order appeared since setup() snapshot.
  2. The order has a line item for SKU-E7421 with quantity == 2.
  3. The order's discount_pct == 10.0.
  4. The order's total == subtotal * 0.9 (within float tolerance).
"""

import requests

from tasks.base import AbstractTask


class ApplyCouponWithQuantityTask(AbstractTask):
    """Add 2 units of a specific SKU, apply a 10% coupon, and checkout."""

    def seed_requirements(self) -> dict:
        return {
            "required_products": [{"category": "Electronics", "sku": "SKU-E7421"}],
            "required_coupons":  [{"code": "SAVE10", "discount_pct": 10.0}],
        }

    def rubric(self) -> str:
        return """\
1. The agent must find and navigate to the product page for SKU-E7421.
2. The agent must set the quantity to 2 before adding to cart (not 1, not 3).
3. The agent must apply the coupon code SAVE10 before checkout is submitted.
4. The agent must complete the checkout process (fill the shipping form and submit the order).
5. The agent should not skip the coupon step or apply it after the order is already placed."""

    def setup(self, base_url: str) -> str:
        state = requests.get(f"{base_url}/api/db-state").json()

        # Record all existing order IDs so verify() can detect new ones.
        self._pre_order_ids: set[str] = {o["id"] for o in state["orders"]}

        return (
            "Add 2 units of SKU-E7421 to the cart, apply coupon SAVE10, "
            "and complete checkout."
        )

    def verify(self, base_url: str) -> dict:
        state = requests.get(f"{base_url}/api/db-state").json()
        new_orders = [o for o in state["orders"] if o["id"] not in self._pre_order_ids]

        if not new_orders:
            return {
                "passed": False,
                "order_id": None,
                "qty_ok": False,
                "discount_ok": False,
                "total_ok": False,
            }

        order = new_orders[0]
        items = [i for i in state["order_items"] if i["order_id"] == order["id"]]

        # Find the SKU-E7421 line item.
        target_item = next((i for i in items if i["sku"] == "SKU-E7421"), None)
        qty_ok = target_item is not None and target_item["quantity"] == 2

        # Total should be: subtotal * (1 - 0.10).
        discount_ok = abs(order.get("discount_pct", 0) - 10.0) < 0.01
        expected_total = order["subtotal"] * 0.9
        total_ok = abs(order["total"] - expected_total) < 0.02

        passed = qty_ok and discount_ok and total_ok
        return {
            "passed": passed,
            "order_id": order["id"],
            "qty_ok": qty_ok,
            "discount_ok": discount_ok,
            "total_ok": total_ok,
        }
