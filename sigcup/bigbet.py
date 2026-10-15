"""Aggressive "big bet": raise cash by selling the positions with the smallest haircut vs fair value,
then put it on one side of a chamber-control market (e.g. Democrats keep the U.S. Senate).

The bet is tagged owner "wave" so the daily bot never sells it at fair value; election mode lets it
ride until the race is decided (cash-in) or clearly lost (salvage).

    python -m sigcup.bigbet --party democratic --chamber "U.S. Senate" --raise 60000 --max-price 0.60   # dry run
    python -m sigcup.bigbet ... --live
"""
from __future__ import annotations

import argparse
import time
from typing import Optional, Sequence

from .bot import DATA, MANUAL, OWNERS, _load, _save
from .client import Client, on_tick
from .fill import take
from .pmfeed import build_map, fair_values

SALES = DATA / "manual_sales.json"
SKIP_OWNERS = {"wave", "pairs"}


def sell_plan(c: Client, n: int, yes: bool, ex: str):
    """Walk the book for selling n shares; returns (proceeds, worst price in our side's terms)."""
    book = c.orderbook(ex, depth=100)
    levels = [(l["price"], l["quantity"]) for l in book["bids"]] if yes else \
             [(1 - l["price"], l["quantity"]) for l in book["asks"]]
    got, left, worst = 0.0, n, None
    for px, qty in levels:
        t = min(left, qty)
        got, left, worst = got + t * px, left - t, px
        if left <= 0:
            break
    return (got, worst) if left <= 0 else (None, None)


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--party", default="democratic", choices=["democratic", "republican"])
    ap.add_argument("--chamber", default="U.S. Senate")
    ap.add_argument("--raise", dest="target", type=float, default=60000)
    ap.add_argument("--max-price", type=float, default=0.60)
    ap.add_argument("--max-haircut", type=float, default=0.10, help="never sell a position >10%% below fair")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args(argv)

    c = Client()
    pm_map = build_map(c)
    fair = fair_values(pm_map)
    owners, manual = _load(OWNERS, {}), set(_load(MANUAL, []))
    title = {ex: r["title"] for ex, r in pm_map.items()}
    bet_titles = {f"Will the Democratic Party win the {a.chamber}?", f"Will the Republican Party win the {a.chamber}?"}

    # 1) choose positions to sell: smallest haircut (fair - proceeds) per cash raised
    cands = []
    for p in c.positions()["positions"]:
        ex, q = p["exchangeId"], p["quantity"]
        if not q or owners.get(ex) in SKIP_OWNERS or p["marketTitle"] in bet_titles:
            continue
        yes, n = q > 0, int(abs(q))
        got, worst = sell_plan(c, n, yes, ex)
        if not got:
            continue
        f = fair.get(ex)
        fv = n * (f if yes else 1 - f) if f is not None else p["marketValue"]
        cut = (fv - got) / got
        if cut <= a.max_haircut:
            cands.append((cut, ex, yes, n, got, fv, worst, p["costBasis"]))
    cands.sort()
    plan, raised = [], 0.0
    for cnd in cands:
        if raised >= a.target:
            break
        plan.append(cnd)
        raised += cnd[4]
    haircut = sum(x[5] - x[4] for x in plan)
    print(f"SELL {len(plan)} positions -> ~{raised:,.0f} cash, giving up {haircut:,.0f} of fair value "
          f"({haircut / max(raised, 1):.1%})")
    for cut, ex, yes, n, got, fv, worst, cost in plan:
        print(f"  {'YES' if yes else 'NO ':<3} {title.get(ex, ex)[9:60]:<52} {n:>7,} sh -> {got:>8,.0f}"
              f"  (fair {fv:,.0f}, haircut {cut:+.1%})")

    # 2) the bet: cheaper of YES-<party> or NO-<other party>
    other = "republican" if a.party == "democratic" else "democratic"
    ex_yes = next(ex for ex, r in pm_map.items() if r["title"] == f"Will the {a.party.title()} Party win the {a.chamber}?")
    ex_no = next(ex for ex, r in pm_map.items() if r["title"] == f"Will the {other.title()} Party win the {a.chamber}?")
    q = c.prices([ex_yes, ex_no])
    p_yes, p_no = q[ex_yes]["bestAsk"], 1 - q[ex_no]["bestBid"]
    f_yes = fair.get(ex_yes)
    print(f"\nBET: {a.party} wins the {a.chamber}: SIG YES ask {p_yes:.3f} | via NO on {other} {p_no:.3f}"
          f" | fair {f_yes:.3f} | price cap {a.max_price}")
    if not a.live:
        print("(dry run -- nothing traded)")
        return

    # execute sells
    sold_manual = []
    for cut, ex, yes, n, got, fv, worst, cost in plan:
        side = "yes" if yes else "no"
        res = c.place_order(ex, side, "sell", n, on_tick(worst))
        if res.get("open") and res.get("orderId"):
            c.cancel(res["orderId"])
        print(f"  sold {res.get('quantityTraded')}/{n} {title.get(ex, ex)[9:55]} for {res.get('totalCost')}")
        if ex in manual and res.get("quantityTraded"):
            frac = res["quantityTraded"] / n
            sold_manual.append({"ts": time.strftime("%Y-%m-%d %H:%M"), "what": title.get(ex, ex),
                                "cash": round(float(res.get("totalCost") or 0), 2), "cost": round(cost * frac, 2)})
    if sold_manual:
        _save(SALES, _load(SALES, []) + sold_manual)

    # buy the bet with all cash above a 1,000 buffer, cheapest route first, never above the cap
    budget = c.balance() - 1000
    owners = _load(OWNERS, {})
    for ex, side in sorted([(ex_yes, "yes"), (ex_no, "no")], key=lambda r: p_yes if r[1] == "yes" else p_no):
        if budget < 500:
            break
        res = take(c, ex, side, budget, a.max_price)
        if res and res.get("quantityTraded"):
            owners[ex] = "wave"  # held to election night
            budget -= float(res.get("totalCost") or 0)
            print(f"  BET {side.upper()} {title[ex][9:60]}: {res['quantityTraded']:,} sh for {res['totalCost']:,.0f}"
                  f" (avg {res['totalCost'] / res['quantityTraded']:.3f})")
    _save(OWNERS, owners)
    print(f"cash now {c.balance():,.0f}")


if __name__ == "__main__":
    main()
