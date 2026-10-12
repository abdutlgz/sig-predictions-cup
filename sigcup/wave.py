"""Aggressive strategy: the "wave" bet.

Close races move together with the national mood.  Bet a small budget on ONE party winning
many competitive races (Polymarket 25-75%), buying each at or below Polymarket's fair value.
Good night for that party -> most bets win (~2x the budget); bad night -> most lose.
High risk, small positive edge.  Hold to election night; sell called races and move the cash
into races still open on the same side (pyramiding).

    python -m sigcup.wave                          # compare D-wave vs R-wave baskets (dry run)
    python -m sigcup.wave --party democratic --budget 3000 --live
"""
from __future__ import annotations

import argparse
from typing import Dict, List, Optional, Sequence

from .bot import MANUAL, OWNERS, _load, _save
from .client import Client
from .fill import take
from .pmfeed import build_map, fair_values


def basket(c: Client, party: str, budget: float, n: int, lo: float, hi: float) -> List[dict]:
    pm_map = build_map(c)
    fair = fair_values(pm_map)
    quotes = c.prices(list(pm_map))
    races: Dict[str, Dict[str, str]] = {}
    for ex, r in pm_map.items():
        races.setdefault(r["race"], {})[r["party"]] = ex
    other = "republican" if party == "democratic" else "democratic"
    owners, manual = _load(OWNERS, {}), set(_load(MANUAL, []))
    taken = lambda ex: ex in manual or owners.get(ex, "wave") != "wave"
    picks = []
    for race, legs in races.items():
        if party not in legs or legs[party] not in fair or any(taken(e) for e in legs.values()):
            continue
        p = fair[legs[party]]
        if not lo <= p <= hi:
            continue
        # two ways to bet on `party`: YES on party, or NO on the other party
        opts = []
        q = quotes.get(legs[party]) or {}
        if q.get("bestAsk") is not None:
            opts.append({"ex": legs[party], "side": "yes", "price": q["bestAsk"], "fair": p})
        if other in legs and legs[other] in fair:
            qo = quotes.get(legs[other]) or {}
            if qo.get("bestBid") is not None:
                opts.append({"ex": legs[other], "side": "no", "price": 1 - qo["bestBid"], "fair": 1 - fair[legs[other]]})
        if not opts:
            continue
        best = max(opts, key=lambda o: o["fair"] - o["price"])
        best.update(race=race, edge=best["fair"] - best["price"], title=pm_map[best["ex"]]["title"])
        if best["edge"] >= 0:
            picks.append(best)
    picks = sorted(picks, key=lambda o: -o["edge"])[:n]
    for o in picks:
        o["budget"] = budget / max(len(picks), 1)
    return picks


def show(party: str, picks: List[dict]) -> None:
    if not picks:
        print(f"\n{party.upper()} wave: no races at or below fair value")
        return
    stake = sum(o["budget"] for o in picks)
    win_all = sum(o["budget"] / o["price"] for o in picks)
    exp = sum(o["budget"] / o["price"] * o["fair"] for o in picks)
    print(f"\n{party.upper()} wave: {len(picks)} races, stake {stake:,.0f} | expected payout {exp:,.0f}"
          f" ({exp / stake - 1:+.1%}) | if all win {win_all:,.0f} ({win_all / stake:.1f}x) | if all lose 0")
    for o in picks:
        print(f"   {o['side'].upper():<3} {o['title'][9:60]:<52} price {o['price']:.3f} fair {o['fair']:.3f}"
              f" edge {o['edge']:+.3f}  stake {o['budget']:,.0f}")


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--party", choices=["democratic", "republican"])
    ap.add_argument("--budget", type=float, default=3000)
    ap.add_argument("--races", type=int, default=8)
    ap.add_argument("--lo", type=float, default=0.25)
    ap.add_argument("--hi", type=float, default=0.75)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args(argv)
    c = Client()
    parties = [a.party] if a.party else ["democratic", "republican"]
    for party in parties:
        picks = basket(c, party, a.budget, a.races, a.lo, a.hi)
        show(party, picks)
        if a.live and a.party:
            owners = _load(OWNERS, {})
            owners.update({o["ex"]: "wave" for o in picks})
            _save(OWNERS, owners)  # bot leaves these alone (held to election night)
            for o in picks:
                res = take(c, o["ex"], o["side"], o["budget"], round(o["fair"], 3))
                print(f"   placed {o['title'][9:60]}: filled {(res or {}).get('quantityTraded', 0)}"
                      f" for {(res or {}).get('totalCost', 0)}")


if __name__ == "__main__":
    main()
