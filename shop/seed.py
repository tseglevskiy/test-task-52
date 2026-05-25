"""
shop/seed.py — Deterministic database seeder.

Public API:
  seed_database(db_path, config)   — wipe + rebuild DB in one transaction
  get_db_snapshot(db_path)         — read-only snapshot dict (for verifiers)

Dataclasses:
  RequiredProduct, RequiredCoupon, RequiredOrder, SeedConfig

CLI:
  python seed.py --db /path/to/shop.db --seed 42 [options]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from db import get_db, init_db
from vocab import ADDRESSES, VOCAB

# ---------------------------------------------------------------------------
# UUID namespace — all uuid5 calls use this
# ---------------------------------------------------------------------------
NAMESPACE = uuid.NAMESPACE_DNS


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RequiredProduct:
    """
    A product that MUST appear in the catalog with the given category and
    optional field overrides applied on top of the rng-generated entry.

    Only fields present in `overrides` are changed; all others keep their
    rng-generated values.  Overrides are applied in Phase 2, AFTER all rng
    draws complete — so adding/removing overrides never shifts the rng
    sequence for the same seed.
    """
    category: str                          # must match a VOCAB key in selected range
    overrides: dict = field(default_factory=dict)
    # Common override keys: "sku", "name", "description", "price"


@dataclass
class RequiredCoupon:
    """A coupon that MUST exist in the DB with this exact code and discount."""
    code: str           # e.g. "DISCOUNT10"
    discount_pct: float # e.g. 10.0  (means 10%)
    active: bool = True


@dataclass
class RequiredOrder:
    """
    A pre-existing order that MUST exist after seeding.

    Required orders replace the first len(required_orders) order slots:
    - slot 0 → created_at = BASE_TS  (most recent)
    - slot i → created_at = BASE_TS - i * 86400

    Items and shipping address come from the Phase 1 rng draw for that slot.
    Only `status` is overridden in Phase 2.
    """
    status: str   # "placed" | "cancelled"


@dataclass
class SeedConfig:
    """
    Full specification for a deterministic database seed.
    Passed to seed_database() by the gym layer.

    Raises ValueError (via _validate_config) if constraints are violated.
    """
    seed: int
    base_ts: int = 1_716_000_000
    # Fixed Unix epoch anchor — NEVER derived from time.time() or wall clock.
    # 1_716_000_000 ≈ 2024-05-18.  Override per task or test as needed.

    # Catalog shape
    n_categories: int = 5
    n_products_per_category: int = 8

    # Gym-specific requirements
    required_products: list[RequiredProduct] = field(default_factory=list)
    required_coupons: list[RequiredCoupon] = field(default_factory=list)
    required_orders: list[RequiredOrder] = field(default_factory=list)

    # Total pre-seeded orders; required_orders replace slots 0..N-1
    n_filler_orders: int = 3


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_config(config: SeedConfig) -> None:
    """Raise ValueError if config violates any constraint."""
    vocab_cats = list(VOCAB.keys())

    if config.n_categories > len(vocab_cats):
        raise ValueError(
            f"n_categories={config.n_categories} exceeds vocabulary size {len(vocab_cats)}"
        )

    selected_cats = vocab_cats[: config.n_categories]

    for cat in selected_cats:
        avail = len(VOCAB[cat])
        if config.n_products_per_category > avail:
            raise ValueError(
                f"Vocabulary exhausted: requested {config.n_products_per_category} "
                f"products from '{cat}' but only {avail} are available"
            )

    selected_cat_set = set(selected_cats)
    for rp in config.required_products:
        if rp.category not in selected_cat_set:
            raise ValueError(
                f"required_product.category='{rp.category}' is not among the "
                f"first {config.n_categories} vocabulary categories: {selected_cats}"
            )

    # Duplicate SKU overrides
    skus = [rp.overrides["sku"] for rp in config.required_products if "sku" in rp.overrides]
    if len(skus) != len(set(skus)):
        raise ValueError(f"Duplicate SKUs in required_products: {skus}")

    # Duplicate coupon codes
    codes = [rc.code for rc in config.required_coupons]
    if len(codes) != len(set(codes)):
        raise ValueError(f"Duplicate coupon codes in required_coupons: {codes}")

    if len(config.required_orders) > config.n_filler_orders:
        raise ValueError(
            f"len(required_orders)={len(config.required_orders)} > "
            f"n_filler_orders={config.n_filler_orders}"
        )


def _derive_sku(seed: int, cat_idx: int, prod_idx: int) -> str:
    """
    Deterministic SKU — immune to PYTHONHASHSEED.
    "SKU-" + first 5 hex chars of sha256(f"{seed}:{cat_idx}:{prod_idx}").upper()
    """
    digest = hashlib.sha256(f"{seed}:{cat_idx}:{prod_idx}".encode()).hexdigest()
    return "SKU-" + digest[:5].upper()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def seed_database(db_path: str, config: SeedConfig) -> None:
    """
    Wipe and rebuild the database at db_path deterministically.

    The algorithm is split into two phases so that changing required_* fields
    never disturbs the rng-generated filler content for the same seed.

    Phase 1 — all rng draws (no required_* influence)
    Phase 2 — required_* injection (no rng calls)
    Single SQLite transaction for atomicity.
    """
    # ── Validation ────────────────────────────────────────────────────────
    _validate_config(config)

    seed = config.seed
    base_ts = config.base_ts

    # ── Phase 1: rng draws ────────────────────────────────────────────────
    rng = random.Random(seed)

    selected_cats = list(VOCAB.keys())[: config.n_categories]  # no rng draw

    # Build rng-phase product list
    # Each entry: dict with id, sku, name, description, category, price, sort_order
    rng_products: list[dict] = []
    sort_counter = 0

    for cat_idx, cat in enumerate(selected_cats):
        sampled = rng.sample(VOCAB[cat], k=config.n_products_per_category)
        for prod_idx, vocab_entry in enumerate(sampled):
            low, high = vocab_entry["price_range"]
            price = round(rng.uniform(low, high), 2)
            product_id = str(uuid.uuid5(NAMESPACE, f"product:{seed}:{cat_idx}:{prod_idx}"))
            sku = _derive_sku(seed, cat_idx, prod_idx)
            rng_products.append({
                "id": product_id,
                "sku": sku,
                "name": vocab_entry["name"],
                "description": vocab_entry["description"],
                "category": cat,
                "price": price,
                "sort_order": sort_counter,
            })
            sort_counter += 1

    # Build rng-phase order slots
    # Snapshot items from rng_products (pre-override) — intentional
    rng_order_slots: list[dict] = []  # each: {order_id, address, items: [{product, qty}]}

    for i in range(config.n_filler_orders):
        address = rng.choice(ADDRESSES)
        k = rng.randint(1, 3)
        picked_products = rng.sample(rng_products, k=k)
        items = []
        for item_idx, prod in enumerate(picked_products):
            qty = rng.randint(1, 3)
            items.append({
                "id": str(uuid.uuid5(NAMESPACE, f"order_item:{seed}:{i}:{item_idx}")),
                "product_id": prod["id"],
                "sku": prod["sku"],        # snapshot at order time (pre-override)
                "name": prod["name"],      # snapshot at order time (pre-override)
                "quantity": qty,
                "unit_price": prod["price"],
            })
        order_id = str(uuid.uuid5(NAMESPACE, f"order:{seed}:{i}"))
        subtotal = round(sum(it["unit_price"] * it["quantity"] for it in items), 2)
        rng_order_slots.append({
            "id": order_id,
            "address": address,
            "items": items,
            "subtotal": subtotal,
        })

    # ── Phase 2: required_* injection (no rng calls) ──────────────────────

    # 2a. Apply required_product overrides onto rng_products
    # Track how many required_products have been assigned per category
    cat_assignment_counter: dict[str, int] = {}
    final_products = [dict(p) for p in rng_products]  # mutable copy

    for rp in config.required_products:
        cat = rp.category
        idx_in_cat = cat_assignment_counter.get(cat, 0)
        cat_assignment_counter[cat] = idx_in_cat + 1

        # Find the product at position idx_in_cat within rp.category
        cat_products = [p for p in final_products if p["category"] == cat]
        if idx_in_cat >= len(cat_products):
            raise ValueError(
                f"Not enough product slots in category '{cat}' for required_product index {idx_in_cat}"
            )
        target_product = cat_products[idx_in_cat]

        # Find and patch in-place in final_products
        for fp in final_products:
            if fp["id"] == target_product["id"]:
                fp.update(rp.overrides)
                break

    # 2b. Build coupon rows
    coupon_rows: list[dict] = []
    for i, rc in enumerate(config.required_coupons):
        coupon_id = str(uuid.uuid5(NAMESPACE, f"coupon:{seed}:{i}"))
        coupon_rows.append({
            "id": coupon_id,
            "code": rc.code,
            "discount_pct": rc.discount_pct,
            "active": 1 if rc.active else 0,
        })

    # 2c. Build order rows with timestamps and statuses
    order_rows: list[dict] = []
    order_item_rows: list[list[dict]] = []

    for i, slot in enumerate(rng_order_slots):
        created_at = base_ts - i * 86400
        if i < len(config.required_orders):
            status = config.required_orders[i].status
        else:
            status = "placed"  # deterministic fallback for filler slots

        order_rows.append({
            "id": slot["id"],
            "created_at": created_at,
            "status": status,
            "shipping_address": slot["address"],
            "coupon_code": None,
            "discount_pct": 0.0,
            "subtotal": slot["subtotal"],
            "total": slot["subtotal"],
        })
        order_item_rows.append(slot["items"])

    # shop_meta: virtual clock starts just above BASE_TS so agent orders are
    # always newer than any pre-seeded order
    next_order_ts = base_ts + 3600

    # ── Commit: single transaction ─────────────────────────────────────────
    init_db(db_path)  # ensure schema exists (idempotent)

    with get_db(db_path) as conn:
        # Wipe all mutable tables (order matters — FKs)
        conn.execute("DELETE FROM cart_meta")
        conn.execute("DELETE FROM cart_items")
        conn.execute("DELETE FROM order_items")
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM coupons")
        conn.execute("DELETE FROM products")
        conn.execute("DELETE FROM shop_meta")

        # Insert products
        conn.executemany(
            "INSERT INTO products (id, sku, name, description, category, price, sort_order) "
            "VALUES (:id, :sku, :name, :description, :category, :price, :sort_order)",
            final_products,
        )

        # Insert coupons
        conn.executemany(
            "INSERT INTO coupons (id, code, discount_pct, active) "
            "VALUES (:id, :code, :discount_pct, :active)",
            coupon_rows,
        )

        # Insert orders + order_items
        for order, items in zip(order_rows, order_item_rows):
            conn.execute(
                "INSERT INTO orders "
                "(id, created_at, status, shipping_address, coupon_code, discount_pct, subtotal, total) "
                "VALUES (:id, :created_at, :status, :shipping_address, "
                ":coupon_code, :discount_pct, :subtotal, :total)",
                order,
            )
            for item in items:
                conn.execute(
                    "INSERT INTO order_items "
                    "(id, order_id, product_id, sku, name, quantity, unit_price) "
                    "VALUES (:id, :order_id, :product_id, :sku, :name, :quantity, :unit_price)",
                    {"order_id": order["id"], **item},
                )

        # Virtual clock
        conn.execute(
            "INSERT INTO shop_meta (key, value) VALUES ('next_order_ts', ?)",
            (str(next_order_ts),),
        )

    # Truncate JSONL event log so each episode starts clean
    log_path = Path(db_path).with_suffix(".jsonl")
    try:
        with open(log_path, "w"):
            pass  # truncate to 0 bytes
    except OSError:
        pass  # log file may not exist yet; that's fine


def get_db_snapshot(db_path: str) -> dict:
    """
    Return a read-only snapshot of the current DB state.
    Callable directly by verifiers — no HTTP, no Flask process required.
    """
    with get_db(db_path) as conn:
        products = [
            dict(row)
            for row in conn.execute(
                "SELECT id, sku, name, category, price FROM products"
            )
        ]
        coupons = [
            dict(row)
            for row in conn.execute(
                "SELECT id, code, discount_pct, active FROM coupons"
            )
        ]
        orders = [
            dict(row)
            for row in conn.execute(
                "SELECT id, created_at, status, shipping_address, "
                "coupon_code, discount_pct, subtotal, total FROM orders"
            )
        ]
        order_items = [
            dict(row)
            for row in conn.execute(
                "SELECT id, order_id, product_id, sku, name, quantity, unit_price "
                "FROM order_items"
            )
        ]

    return {
        "products": products,
        "coupons": coupons,
        "orders": orders,
        "order_items": order_items,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_required_product(s: str) -> RequiredProduct:
    """Parse JSON string like '{"category": "Electronics", "sku": "SKU-E7421"}'."""
    data = json.loads(s)
    category = data.pop("category")
    return RequiredProduct(category=category, overrides=data)


def _parse_required_coupon(s: str) -> RequiredCoupon:
    """Parse JSON string like '{"code": "DISCOUNT10", "discount_pct": 10.0}'."""
    data = json.loads(s)
    return RequiredCoupon(
        code=data["code"],
        discount_pct=float(data["discount_pct"]),
        active=bool(data.get("active", True)),
    )


def _parse_required_order(s: str) -> RequiredOrder:
    """Parse JSON string like '{"status": "placed"}'."""
    data = json.loads(s)
    return RequiredOrder(status=data["status"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the ShopGym SQLite database")
    parser.add_argument("--db", required=True, help="Path to shop.db")
    parser.add_argument("--seed", required=True, type=int, help="Integer RNG seed")
    parser.add_argument("--n-categories", type=int, default=5)
    parser.add_argument("--n-products-per-category", type=int, default=8)
    parser.add_argument(
        "--require-product",
        action="append",
        default=[],
        metavar="JSON",
        help='JSON object with "category" key and optional overrides. Repeatable.',
    )
    parser.add_argument(
        "--require-coupon",
        action="append",
        default=[],
        metavar="JSON",
        help='JSON object with "code" and "discount_pct". Repeatable.',
    )
    parser.add_argument(
        "--require-order",
        action="append",
        default=[],
        metavar="JSON",
        help='JSON object with "status". Repeatable.',
    )
    parser.add_argument("--n-filler-orders", type=int, default=3)
    parser.add_argument("--base-ts", type=int, default=1_716_000_000)

    args = parser.parse_args()

    config = SeedConfig(
        seed=args.seed,
        base_ts=args.base_ts,
        n_categories=args.n_categories,
        n_products_per_category=args.n_products_per_category,
        required_products=[_parse_required_product(s) for s in args.require_product],
        required_coupons=[_parse_required_coupon(s) for s in args.require_coupon],
        required_orders=[_parse_required_order(s) for s in args.require_order],
        n_filler_orders=args.n_filler_orders,
    )

    seed_database(args.db, config)
    print(f"Seeded {args.db} with seed={args.seed}")
