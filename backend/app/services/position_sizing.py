import math
from dataclasses import dataclass

from app.core.portfolio_config import RISK_PCT_PER_TRADE


@dataclass
class PositionSize:
    shares: int
    allocated_amount: float
    risk_amount: float
    allocation_capped: bool
    rejection_reason: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.rejection_reason is None and self.shares > 0


def size_position(
    entry: float,
    stop_loss: float,
    capital: float,
    risk_appetite: str,
    max_allocation_per_stock: float,
    risk_pct: float | None = None,
) -> PositionSize:
    """shares = floor(max_loss / risk_per_share), then capped so the
    position doesn't exceed max_allocation_per_stock (an absolute rupee
    amount, e.g. capital * MAX_ALLOCATION_PCT[appetite])."""
    if stop_loss >= entry:
        return PositionSize(0, 0.0, 0.0, False, rejection_reason="stop_loss at or above entry")

    risk_pct = (risk_pct if risk_pct is not None else RISK_PCT_PER_TRADE[risk_appetite])
    max_loss = capital * risk_pct
    risk_per_share = entry - stop_loss

    shares = math.floor(max_loss / risk_per_share)
    if shares == 0:
        return PositionSize(0, 0.0, 0.0, False, rejection_reason="position size rounds to 0 shares")

    allocated_amount = shares * entry
    allocation_capped = False
    if allocated_amount > max_allocation_per_stock:
        shares = math.floor(max_allocation_per_stock / entry)
        if shares == 0:
            return PositionSize(0, 0.0, 0.0, False, rejection_reason="allocation cap leaves 0 shares")
        allocated_amount = shares * entry
        allocation_capped = True

    risk_amount = shares * risk_per_share
    return PositionSize(shares, round(allocated_amount, 2), round(risk_amount, 2), allocation_capped)
