"""
Unit tests for task verifiers.
Run with: gym_env/.venv/bin/python -m pytest gym_env/tests/test_verifiers.py -v

No Docker or HTTP needed — requests.get is mocked.

Each test:
1. Instantiates the task and sets its episode state directly.
2. Patches requests.get to return a fake db-state dict.
3. Calls task.verify() and asserts (1.0, True) for the success state.
4. Also tests the negative path: asserts (0.0, False) for the incomplete state.

The fake state dicts document what a successful DB state looks like for each task.
"""

from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _mock_get(state_dict):
    """Return a mock requests.get that returns state_dict from .json()."""
    mock = MagicMock()
    mock.return_value.json.return_value = state_dict
    return mock


# ---------------------------------------------------------------------------
# Test: CancelRecentOrderTask
# ---------------------------------------------------------------------------

def test_cancel_order_verifier():
    from tasks.cancel_order import CancelRecentOrderTask

    task = CancelRecentOrderTask()
    task._target_order_id = "order-abc"

    # --- Success: order is cancelled ---
    success_state = {
        "orders": [
            {"id": "order-abc", "status": "cancelled", "created_at": 1716000000},
        ],
        "order_items": [],
        "products": [],
        "coupons": [],
    }
    with patch("requests.get", _mock_get(success_state)):
        reward, terminated = task.verify("http://localhost:5001", None)
    assert reward == 1.0
    assert terminated is True

    # --- Negative: order still placed ---
    pending_state = {
        "orders": [
            {"id": "order-abc", "status": "placed", "created_at": 1716000000},
        ],
        "order_items": [],
        "products": [],
        "coupons": [],
    }
    with patch("requests.get", _mock_get(pending_state)):
        reward, terminated = task.verify("http://localhost:5001", None)
    assert reward == 0.0
    assert terminated is False


# ---------------------------------------------------------------------------
# Test: BuyCheapestInCategoryTask
# ---------------------------------------------------------------------------

def test_buy_cheapest_verifier():
    from tasks.buy_cheapest import BuyCheapestInCategoryTask

    task = BuyCheapestInCategoryTask()
    task._pre_order_ids = {"old-order-1", "old-order-2"}
    task._min_price = 29.99

    # The cheapest Electronics product has id "prod-elec-1", price 29.99.
    # A new order "new-order-1" bought that product.
    success_state = {
        "orders": [
            {
                "id": "new-order-1",
                "status": "placed",
                "created_at": 1716003601,
                "shipping_address": "Alice Smith\n123 Main St\nSpringfield, IL 62701",
                "coupon_code": None,
                "discount_pct": 0.0,
                "subtotal": 29.99,
                "total": 29.99,
            },
            {"id": "old-order-1", "status": "placed", "created_at": 1716000000,
             "shipping_address": "Bob\n1 Other St\nAnytown, CA 90210",
             "coupon_code": None, "discount_pct": 0.0, "subtotal": 50.0, "total": 50.0},
        ],
        "products": [
            {"id": "prod-elec-1", "sku": "SKU-ELEC1", "name": "Cheap Widget",
             "category": "Electronics", "price": 29.99},
            {"id": "prod-other-1", "sku": "SKU-OTH1", "name": "Cheap Other",
             "category": "Clothing", "price": 9.99},
        ],
        "order_items": [
            {"id": "item-1", "order_id": "new-order-1", "product_id": "prod-elec-1",
             "sku": "SKU-ELEC1", "name": "Cheap Widget", "quantity": 1,
             "unit_price": 29.99},
        ],
        "coupons": [],
    }
    with patch("requests.get", _mock_get(success_state)):
        reward, terminated = task.verify("http://localhost:5001", None)
    assert reward == 1.0
    assert terminated is True

    # --- Negative: item is from wrong category (Clothing, not Electronics) ---
    wrong_category_state = {
        "orders": [
            {
                "id": "new-order-2",
                "status": "placed",
                "created_at": 1716003602,
                "shipping_address": "Alice Smith\n123 Main St\nSpringfield, IL 62701",
                "coupon_code": None,
                "discount_pct": 0.0,
                "subtotal": 29.99,
                "total": 29.99,
            },
        ],
        "products": [
            {"id": "prod-elec-1", "sku": "SKU-ELEC1", "name": "Cheap Widget",
             "category": "Electronics", "price": 29.99},
            {"id": "prod-cloth-1", "sku": "SKU-CLOTH1", "name": "Cheap Shirt",
             "category": "Clothing", "price": 29.99},
        ],
        "order_items": [
            # Agent bought the Clothing item (same price, wrong category).
            {"id": "item-2", "order_id": "new-order-2", "product_id": "prod-cloth-1",
             "sku": "SKU-CLOTH1", "name": "Cheap Shirt", "quantity": 1,
             "unit_price": 29.99},
        ],
        "coupons": [],
    }
    task2 = BuyCheapestInCategoryTask()
    task2._pre_order_ids = set()  # no pre-existing orders
    task2._min_price = 29.99
    with patch("requests.get", _mock_get(wrong_category_state)):
        reward, terminated = task2.verify("http://localhost:5001", None)
    assert reward == 0.0
    assert terminated is False


# ---------------------------------------------------------------------------
# Test: ApplyCouponWithQuantityTask
# ---------------------------------------------------------------------------

def test_apply_coupon_verifier():
    from tasks.apply_coupon import ApplyCouponWithQuantityTask

    task = ApplyCouponWithQuantityTask()
    task._pre_order_ids = {"old-order-1"}

    subtotal = 59.98
    expected_total = round(subtotal * 0.9, 2)  # 53.98

    # --- Success: correct SKU, qty=2, 10% discount applied ---
    success_state = {
        "orders": [
            {
                "id": "new-order-1",
                "status": "placed",
                "created_at": 1716003601,
                "shipping_address": "Alice\n10 Any St\nAnytown, CA 90210",
                "coupon_code": "SAVE10",
                "discount_pct": 10.0,
                "subtotal": subtotal,
                "total": expected_total,
            },
            {"id": "old-order-1", "status": "placed", "created_at": 1716000000,
             "shipping_address": "Bob\n1 St\nCity, TX 75001",
             "coupon_code": None, "discount_pct": 0.0, "subtotal": 20.0, "total": 20.0},
        ],
        "products": [
            {"id": "prod-e7421", "sku": "SKU-E7421", "name": "Gadget Pro",
             "category": "Electronics", "price": 29.99},
        ],
        "order_items": [
            {"id": "item-1", "order_id": "new-order-1", "product_id": "prod-e7421",
             "sku": "SKU-E7421", "name": "Gadget Pro", "quantity": 2,
             "unit_price": 29.99},
        ],
        "coupons": [
            {"id": "coupon-1", "code": "SAVE10", "discount_pct": 10.0, "active": 1},
        ],
    }
    with patch("requests.get", _mock_get(success_state)):
        reward, terminated = task.verify("http://localhost:5001", None)
    assert reward == 1.0
    assert terminated is True

    # --- Negative: correct SKU but quantity=1, not 2 ---
    wrong_qty_state = {
        "orders": [
            {
                "id": "new-order-2",
                "status": "placed",
                "created_at": 1716003602,
                "shipping_address": "Alice\n10 Any St\nAnytown, CA 90210",
                "coupon_code": "SAVE10",
                "discount_pct": 10.0,
                "subtotal": 29.99,
                "total": round(29.99 * 0.9, 2),
            },
        ],
        "products": [
            {"id": "prod-e7421", "sku": "SKU-E7421", "name": "Gadget Pro",
             "category": "Electronics", "price": 29.99},
        ],
        "order_items": [
            {"id": "item-2", "order_id": "new-order-2", "product_id": "prod-e7421",
             "sku": "SKU-E7421", "name": "Gadget Pro", "quantity": 1,
             "unit_price": 29.99},
        ],
        "coupons": [
            {"id": "coupon-1", "code": "SAVE10", "discount_pct": 10.0, "active": 1},
        ],
    }
    task2 = ApplyCouponWithQuantityTask()
    task2._pre_order_ids = set()
    with patch("requests.get", _mock_get(wrong_qty_state)):
        reward, terminated = task2.verify("http://localhost:5001", None)
    assert reward == 0.0
    assert terminated is False
