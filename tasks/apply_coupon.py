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

    def check_trajectory(self, trajectory: list[dict]) -> dict:
        """Deterministic checks for apply_coupon trajectory."""
        violations = []

        # Index steps by position for ordering checks
        steps = list(trajectory)

        # Collect typed texts with their step index
        typed = [
            (i, s["args"].get("text", ""), s["args"].get("selector", "").lower())
            for i, s in enumerate(steps)
            if s.get("tool") == "type_text"
        ]
        click_selectors = [
            (i, s["args"].get("selector", "").lower())
            for i, s in enumerate(steps)
            if s.get("tool") == "click"
        ]

        # Check 1: Coupon code SAVE10 was typed
        coupon_steps = [i for i, text, _ in typed if text.upper() == "SAVE10"]
        if not coupon_steps:
            violations.append("Agent never typed the coupon code SAVE10.")

        # Check 2: Apply Coupon button was clicked
        apply_coupon_clicked = any(
            "apply coupon" in sel or "apply" in sel
            for _, sel in click_selectors
        )
        if not apply_coupon_clicked:
            violations.append("Agent never clicked the Apply Coupon button.")

        # Check 3: Quantity 2 was set (type_text with text="2" on a number input,
        # or any type_text with text="2" that appears before "Add to Cart" click)
        add_to_cart_steps = [
            i for i, sel in click_selectors
            if "add to cart" in sel or "addtocart" in sel.replace(" ", "")
        ]
        qty_2_steps = [
            i for i, text, sel in typed
            if text == "2" and ("number" in sel or "qty" in sel or "quantity" in sel)
        ]
        # Fallback: any "2" typed before the first Add to Cart click
        if not qty_2_steps and add_to_cart_steps:
            first_add = min(add_to_cart_steps)
            qty_2_steps = [i for i, text, _ in typed if text == "2" and i < first_add]
        if not qty_2_steps:
            violations.append(
                "Agent never set quantity to 2 (no type_text with text='2' "
                "on a quantity input before Add to Cart)."
            )

        # Check 4: Coupon must be applied BEFORE checkout submit
        submit_steps = [
            i for i, sel in click_selectors
            if 'type="submit"' in sel or sel == "button[type=\"submit\"]"
               or "submit" in sel
        ]
        if coupon_steps and submit_steps:
            first_coupon = min(coupon_steps)
            first_submit = min(submit_steps)
            if first_coupon > first_submit:
                violations.append(
                    "Coupon code SAVE10 was typed AFTER checkout was submitted."
                )

        passed = len(violations) == 0
        checks = [
            f"{'✓' if not any('SAVE10' in v for v in violations) else '✗'} Typed coupon SAVE10",
            f"{'✓' if apply_coupon_clicked else '✗'} Clicked Apply Coupon",
            f"{'✓' if qty_2_steps else '✗'} Set quantity to 2",
            f"{'✓' if not any('AFTER' in v for v in violations) else '✗'} Coupon before checkout",
        ]
        return {
            "passed": passed,
            "violations": violations,
            "reasoning": "; ".join(checks),
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
