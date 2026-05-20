"""tasks — concrete task implementations for ShopGym."""

from .buy_cheapest import BuyCheapestInCategoryTask
from .apply_coupon import ApplyCouponWithQuantityTask
from .cancel_order import CancelRecentOrderTask

__all__ = [
    "BuyCheapestInCategoryTask",
    "ApplyCouponWithQuantityTask",
    "CancelRecentOrderTask",
]
