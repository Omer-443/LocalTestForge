"""Synthetic target. Human-owned fixture."""

MAX_DISCOUNT = 50
MEMBER_BONUS = 5


def apply_discount(price: float, percent: float, is_member: bool = False) -> float:
    """Return `price` after applying a percentage discount, rounded to 2 decimals.

    - `percent` must be between 0 and 100 inclusive, otherwise ValueError.
    - `price` must be non-negative, otherwise ValueError.
    - Regular discounts are capped at MAX_DISCOUNT percent.
    - Members get MEMBER_BONUS extra percentage points, applied after the cap.
    """
    if price < 0:
        raise ValueError("price must be non-negative")
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    effective = min(percent, MAX_DISCOUNT)
    if is_member:
        effective += MEMBER_BONUS
    return round(price * (1 - effective / 100), 2)