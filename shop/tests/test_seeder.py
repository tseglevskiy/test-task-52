"""
tests/test_seeder.py — Determinism tests for shop/seed.py

Tests:
  1. seed(1) run twice → identical DB snapshots
  2. seed(1) vs seed(42) → different product catalogs
  3. seed(1) + required_product(sku=SKU-E7421) run twice → identical snapshots
  4. seed(1) without override vs seed(1) + override → differ ONLY in the
     overridden product's SKU; all other products, orders, and order_items
     are untouched

Run:
  cd /mnt/d/p/gym
  python -m pytest tests/test_seeder.py -v
  # or without pytest:
  python tests/test_seeder.py
"""

import os
import sys
import tempfile
import unittest

# Make shop/ importable regardless of cwd or where the test runner is invoked from.
# __file__ is always at shop/tests/test_seeder.py → parent dir is shop/tests/ → parent of that is shop/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # .../shop/tests
_SHOP_DIR = os.path.dirname(_THIS_DIR)                   # .../shop
sys.path.insert(0, _SHOP_DIR)

from seed import (  # noqa: E402
    SeedConfig,
    RequiredProduct,
    RequiredCoupon,
    RequiredOrder,
    seed_database,
    get_db_snapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_db() -> str:
    """Return path to a fresh temporary SQLite file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _seed(db_path: str, config: SeedConfig) -> dict:
    """Seed db_path with config and return the full snapshot."""
    seed_database(db_path, config)
    return get_db_snapshot(db_path)


def _products_by_id(snapshot: dict) -> dict:
    """Return {product_id: product_dict} for easy comparison."""
    return {p["id"]: p for p in snapshot["products"]}


def _default_config(seed: int, **kwargs) -> SeedConfig:
    """SeedConfig with sensible defaults for tests (10 categories × 8 products)."""
    return SeedConfig(
        seed=seed,
        n_categories=10,
        n_products_per_category=8,
        n_filler_orders=3,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestSeederDeterminism(unittest.TestCase):

    # ── Test 1 ──────────────────────────────────────────────────────────────

    def test_same_seed_twice_produces_identical_snapshots(self):
        """seed(1) run twice must produce bitwise-identical DB snapshots."""
        db1 = _tmp_db()
        db2 = _tmp_db()
        try:
            cfg = _default_config(seed=1)
            snap1 = _seed(db1, cfg)
            snap2 = _seed(db2, cfg)

            self.assertEqual(
                snap1["products"], snap2["products"],
                "Products differ between two runs of seed(1)",
            )
            self.assertEqual(
                snap1["orders"], snap2["orders"],
                "Orders differ between two runs of seed(1)",
            )
            self.assertEqual(
                snap1["order_items"], snap2["order_items"],
                "Order items differ between two runs of seed(1)",
            )
            self.assertEqual(
                snap1["coupons"], snap2["coupons"],
                "Coupons differ between two runs of seed(1)",
            )
        finally:
            os.unlink(db1)
            os.unlink(db2)

    # ── Test 2 ──────────────────────────────────────────────────────────────

    def test_different_seeds_produce_different_catalogs(self):
        """seed(1) and seed(42) must produce different product catalogs."""
        db1 = _tmp_db()
        db2 = _tmp_db()
        try:
            snap1 = _seed(db1, _default_config(seed=1))
            snap42 = _seed(db2, _default_config(seed=42))

            # Collect (name, price) pairs from both catalogs
            pairs1 = {(p["id"], p["sku"], p["price"]) for p in snap1["products"]}
            pairs42 = {(p["id"], p["sku"], p["price"]) for p in snap42["products"]}

            self.assertNotEqual(
                pairs1, pairs42,
                "seed(1) and seed(42) produced identical catalogs — that is wrong",
            )
            # Sanity: same number of products (catalog shape is the same)
            self.assertEqual(
                len(snap1["products"]), len(snap42["products"]),
                "Catalog sizes differ between seeds — shape mismatch",
            )
        finally:
            os.unlink(db1)
            os.unlink(db2)

    # ── Test 3 ──────────────────────────────────────────────────────────────

    def test_same_seed_with_required_product_is_idempotent(self):
        """seed(1) + required_product(sku=SKU-E7421) run twice → identical."""
        db1 = _tmp_db()
        db2 = _tmp_db()
        try:
            cfg = _default_config(
                seed=1,
                required_products=[
                    RequiredProduct(category="Electronics", overrides={"sku": "SKU-E7421"}),
                ],
                required_coupons=[
                    RequiredCoupon(code="SAVE10", discount_pct=10.0),
                ],
                required_orders=[
                    RequiredOrder(status="placed"),
                ],
            )
            snap1 = _seed(db1, cfg)
            snap2 = _seed(db2, cfg)

            self.assertEqual(snap1["products"], snap2["products"])
            self.assertEqual(snap1["orders"], snap2["orders"])
            self.assertEqual(snap1["order_items"], snap2["order_items"])
            self.assertEqual(snap1["coupons"], snap2["coupons"])

            # Verify the required product is actually there
            skus = {p["sku"] for p in snap1["products"]}
            self.assertIn("SKU-E7421", skus, "Required SKU-E7421 not found in catalog")

            # Verify the required coupon is there
            codes = {c["code"] for c in snap1["coupons"]}
            self.assertIn("SAVE10", codes, "Required coupon SAVE10 not found")

            # Verify the most-recent order is 'placed'
            most_recent = max(snap1["orders"], key=lambda o: o["created_at"])
            self.assertEqual(most_recent["status"], "placed",
                             "Most recent order should have status='placed'")
        finally:
            os.unlink(db1)
            os.unlink(db2)

    # ── Test 4 ──────────────────────────────────────────────────────────────

    def test_override_isolates_only_target_product(self):
        """
        seed(1) without override vs seed(1) with sku override on one Electronics
        product must produce identical results EXCEPT for that product's SKU.

        Specifically:
          - All other products (by id) must be byte-for-byte identical
          - order_items snapshots are unchanged (they use pre-override rng values)
          - The overridden product's SKU changes; everything else about it (id,
            name, description, category, price, sort_order) stays the same
        """
        db_base = _tmp_db()
        db_override = _tmp_db()
        try:
            base_cfg = _default_config(seed=1)
            override_cfg = _default_config(
                seed=1,
                required_products=[
                    RequiredProduct(
                        category="Electronics",
                        overrides={"sku": "SKU-E7421"},
                    )
                ],
            )

            snap_base = _seed(db_base, base_cfg)
            snap_over = _seed(db_override, override_cfg)

            prod_base = _products_by_id(snap_base)
            prod_over = _products_by_id(snap_over)

            # Same set of product IDs
            self.assertEqual(
                set(prod_base.keys()), set(prod_over.keys()),
                "Override changed the set of product IDs",
            )

            # Find the overridden product (should be the first Electronics slot = id index 0)
            overridden_id = None
            for pid, p in prod_over.items():
                if p["sku"] == "SKU-E7421":
                    overridden_id = pid
                    break

            self.assertIsNotNone(overridden_id,
                                 "Could not find SKU-E7421 in overridden snapshot")

            # The overridden product's base snapshot should have a different SKU
            self.assertNotEqual(
                prod_base[overridden_id]["sku"],
                prod_over[overridden_id]["sku"],
                "Override did not change the SKU of the target product",
            )

            # All fields OTHER than sku on the overridden product should be identical
            for field in ("id", "name", "category", "price"):
                self.assertEqual(
                    prod_base[overridden_id][field],
                    prod_over[overridden_id][field],
                    f"Override changed '{field}' on the overridden product (should only change sku)",
                )

            # All OTHER products must be completely identical
            for pid, p_base in prod_base.items():
                if pid == overridden_id:
                    continue
                self.assertEqual(
                    p_base,
                    prod_over[pid],
                    f"Override changed product {pid} which was not the override target",
                )

            # order_items must be identical (snapshots are pre-override)
            items_base = sorted(snap_base["order_items"], key=lambda x: x["id"])
            items_over = sorted(snap_over["order_items"], key=lambda x: x["id"])
            self.assertEqual(
                items_base, items_over,
                "Override changed order_items snapshots (they should use pre-override rng values)",
            )

        finally:
            os.unlink(db_base)
            os.unlink(db_override)


# ---------------------------------------------------------------------------
# Entry point (runs without pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
