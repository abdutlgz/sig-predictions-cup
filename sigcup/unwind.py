"""Unwind hedged pairs (NO on both parties of a race) -- only at or above what they cost.

Selling NO on a leg lifts that leg's YES asks, so a set sells for N - sum(asks).  Walks both
books together and sells only as many sets as keep total proceeds >= cost, as one atomic
multi-leg order with per-leg price floors.

    python -m sigcup.unwind "CO-08 House race" "Alaska Senate" "Texas Senate"     # dry run
    python -m sigcup.unwind "CO-08 House race" --live
"""
from __future__ import annotations

import argparse
import time
from typing import List, Sequence, Tuple

from .bot import DATA, MANUAL, _load, _save
from .client import ApiError, Client, on_tick

SALES = DATA / "manual_sales.json"  # cash raised from pre-existing positions (not bot profit)


def walk(ladders: List[List[Tuple[float, float]]], per_set: float, max_qty: int,
         min_profit: float = 0.0):
    n, idx = len(ladders), [0] * len(ladders)
    if any(not lad for lad in ladders):
        return 0, None
    left = [lad[0][1] for lad in ladders]
    sets, worst = 0, None
    while sets < max_qty:
        px = [ladders[i][idx[i]][0] for i in range(n)]
        if n - sum(px) - per_set < min_profit - 1e-9:
            break
        q = int(min(min(left), max_qty - sets))
        if q <= 0:
            break
        sets, worst = sets + q, px
        for i in range(n):
            left[i] -= q
            if left[i] <= 0:
                idx[i] += 1
                if idx[i] >= len(ladders[i]):
                    return sets, worst
                left[i] = ladders[i][idx[i]][1]
    return sets, worst


def main(argv: Sequence[str] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("races", nargs="+")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--min-sets", type=int, default=1, help="skip if fewer sets are sellable at >= cost")
    a = ap.parse_args(argv)
    c = Client()
    pos = [p for p in c.positions()["positions"] if p["quantity"]]
    total_cost = total_got = 0.0
    for race in a.races:
        legs = [p for p in pos if p["marketTitle"].endswith(f"win the {race}?")]
        if len(legs) != 2 or not all(p["quantity"] < 0 for p in legs):
            print(f"{race}: not a NO/NO pair ({len(legs)} legs) -- skipped")
            continue
        qty = int(min(abs(p["quantity"]) for p in legs))
        per_set = sum(p["costBasis"] / abs(p["quantity"]) for p in legs)
        ladders = [[(l["price"], l["quantity"]) for l in c.orderbook(p["exchangeId"], depth=50)["asks"]]
                   for p in legs]
        n, worst = walk(ladders, per_set, qty)
        if n < a.min_sets:
            print(f"{race}: only {n:,} sets sellable at or above cost (cost {per_set:.3f}/set) -- kept")
            continue
        floor_value = n * (2 - sum(worst))
        print(f"{race}: {n:,} of {qty:,} sets sellable; cost {n * per_set:,.0f}, "
              f"worst-case proceeds {floor_value:,.0f} ({floor_value - n * per_set:+,.0f})")
        if not a.live:
            continue
        try:
            res = c.multi_leg([c.leg(p["exchangeId"], "no", "sell", n, on_tick(1 - w)) for p, w in zip(legs, worst)])
        except ApiError as e:
            print(f"  FAILED: {e}")
            continue
        got, filled = 0.0, []
        for r in res.get("results", []):
            d = r.get("data") or {}
            if d.get("open") and d.get("orderId"):
                c.cancel(d["orderId"])  # never leave a leg resting
            got += float(d.get("totalCost") or 0)
            filled.append(int(d.get("quantityTraded") or 0))
        total_cost, total_got = total_cost + min(filled) * per_set, total_got + got
        if all(p["exchangeId"] in set(_load(MANUAL, [])) for p in legs):
            _save(SALES, _load(SALES, []) + [{"ts": time.strftime("%Y-%m-%d %H:%M"), "what": race,
                                              "cash": round(got, 2), "cost": round(min(filled) * per_set, 2)}])
        warn = "" if len(set(filled)) == 1 else "  !! legs filled unevenly -- check positions"
        print(f"  SOLD {filled} for {got:,.0f} (cost {min(filled) * per_set:,.0f}, {got - min(filled) * per_set:+,.0f}){warn}")
    if a.live:
        print(f"TOTAL: raised {total_got:,.0f} vs cost {total_cost:,.0f} ({total_got - total_cost:+,.0f}); "
              f"cash now {c.balance():,.0f}")


if __name__ == "__main__":
    main()
