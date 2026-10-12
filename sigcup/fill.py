"""Directional fills: take liquidity on one side up to a worst acceptable price."""
from __future__ import annotations

from typing import Optional

from .client import Client, on_tick


def plan_take(book: dict, side: str, budget: float, max_price: float):
    """Walk the YES-normalized book. Buying YES lifts asks; buying NO hits bids at 1-bid.
    Returns (quantity, cost, limit_price_in_side_terms)."""
    levels = book["asks"] if side == "yes" else [
        {"price": round(1 - l["price"], 3), "quantity": l["quantity"]} for l in book["bids"]]
    qty, cost, limit = 0, 0.0, None
    for l in levels:
        px = l["price"]
        if px > max_price + 1e-9:
            break
        q = int(min(l["quantity"], (budget - cost) // px))
        if q <= 0:
            break
        qty, cost, limit = qty + q, cost + q * px, px
    return qty, cost, limit


def take(c: Client, exchange_id: str, side: str, budget: float, max_price: float,
         key: Optional[str] = None) -> Optional[dict]:
    """Buy `side` on an exchange for up to `budget`, never paying more than `max_price`.
    Any unfilled remainder is cancelled, so nothing is left resting."""
    qty, cost, limit = plan_take(c.orderbook(exchange_id, depth=20), side, budget, max_price)
    if qty <= 0:
        return None
    res = c.place_order(exchange_id, side, "buy", qty, on_tick(limit), key=key)
    if res.get("open") and res.get("orderId"):
        c.cancel(res["orderId"])
    return res
