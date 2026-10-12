"""Risk-free arbitrage scanner for mutually exclusive market sets.

If outcomes are mutually exclusive (e.g. "<Race> - Republican" vs "<Race> - Democratic"),
at most one YES can pay, so buying NO on every member of an N-set costs N - sum(bid_YES)
and pays at least N - 1.  Profit >= sum(bid_YES) - 1 per set, whoever wins.

The reverse (sum(ask_YES) < 1 -> buy YES on all) is only risk-free if the set is
exhaustive; it is reported as "conditional" and never auto-executed.

Usage:
    python -m sigcup.arb                         # scan, dry run
    python -m sigcup.arb --watch 30              # rescan every 30s
    python -m sigcup.arb --execute --max-cost 20000
"""
from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .client import ApiError, Client, on_tick

_PARTY = r"(?P<party>Republican|Democratic|Democrat|Independent|GOP|Other)"
PARTY_RES = [
    # "Will the Republican Party win the Rhode Island Senate?"
    re.compile(rf"^Will (?:the |an? )?{_PARTY}(?: Party| candidate)? win (?:the )?(?P<race>.+?)\??$", re.I),
    # "Rhode Island Senate - Republican"
    re.compile(rf"^(?P<race>.+?)\s*[-–—:]\s*{_PARTY}\b", re.I),
]


def parse_party(title: str):
    for rx in PARTY_RES:
        m = rx.match(title.strip())
        if m:
            return m["race"].strip(), m["party"]
    return None

Level = Tuple[float, float]  # (price, quantity)


@dataclass
class Group:
    label: str
    exchange_ids: List[str]
    titles: List[str]
    exhaustive: bool = False
    relationship_id: Optional[str] = None

    @property
    def two_party(self) -> bool:
        return len(self.exchange_ids) == 2 and not any("independent" in t.lower() for t in self.titles)


@dataclass
class Opportunity:
    group: Group
    kind: str                 # "riskfree_no" | "conditional_yes"
    sets: int                 # number of complete sets
    edge_per_set: float       # worst-case profit per set (top of book)
    profit: float             # worst-case total profit
    cost: float               # cash required
    limits: List[float] = field(default_factory=list)  # per-leg limit price, in the leg's own side terms
    ladders: List[List[Level]] = field(default_factory=list, repr=False)

    @property
    def roc(self) -> float:
        return self.profit / self.cost if self.cost else 0.0


def make_opp(g: Group, kind: str, ladders: List[List[Level]], min_edge: float,
             budget: float) -> Optional[Opportunity]:
    buy_no = kind == "riskfree_no"
    sets, profit, cost, worst = walk_sets(ladders, min_edge, budget, buy_no=buy_no)
    if not sets:
        return None
    top = [lad[0][0] for lad in ladders]
    edge = sum(top) - 1 if buy_no else 1 - sum(top)
    limits = [on_tick(1 - p) for p in worst] if buy_no else list(worst)
    return Opportunity(g, kind, sets, edge, profit, cost, limits, ladders)


def allocate(opps: List[Opportunity], budget: float, min_edge: float,
             max_per_race: float = 0) -> List[Opportunity]:
    """Spend one shared budget on risk-free opportunities, best return on cash first."""
    out, left = [], budget
    for o in sorted(opps, key=lambda o: o.roc, reverse=True):
        if not o.kind.startswith("riskfree"):
            continue
        cap = min(left, max_per_race) if max_per_race else left
        if cap < 1:
            break
        sized = make_opp(o.group, o.kind, o.ladders, min_edge, cap)
        if sized:
            out.append(sized)
            left -= sized.cost
    return out


def walk_sets(ladders: Sequence[Sequence[Level]], min_edge: float, budget: float,
              buy_no: bool) -> Tuple[int, float, float, List[float]]:
    """Consume books level by level while a full set is still profitable.

    buy_no=True : ladders are YES bids (desc); set cost = N - sum(bid), payout >= N-1.
    buy_no=False: ladders are YES asks (asc);  set cost = sum(ask),     payout  = 1 (if exhaustive).
    Returns (sets, profit, cost, worst_prices_in_YES_terms).
    """
    n = len(ladders)
    if n < 2 or any(not lad for lad in ladders):
        return 0, 0.0, 0.0, []
    idx = [0] * n
    left = [lad[0][1] for lad in ladders]
    worst = [lad[0][0] for lad in ladders]
    sets = 0
    profit = cost = 0.0
    while True:
        px = [ladders[i][idx[i]][0] for i in range(n)]
        if buy_no:
            unit_cost, payout = n - sum(px), n - 1
        else:
            unit_cost, payout = sum(px), 1
        edge = payout - unit_cost
        if edge < min_edge - 1e-9 or unit_cost <= 0:
            break
        q = int(min(left))
        if budget > 0:
            q = min(q, int((budget - cost) // unit_cost))
        if q <= 0:
            break
        sets += q
        profit += q * edge
        cost += q * unit_cost
        worst = px
        for i in range(n):
            left[i] -= q
            if left[i] <= 1e-9:
                idx[i] += 1
                if idx[i] >= len(ladders[i]):
                    return sets, profit, cost, worst
                left[i] = ladders[i][idx[i]][1]
    return sets, profit, cost, worst


def build_groups(c: Client, markets: list) -> List[Group]:
    groups: Dict[frozenset, Group] = {}
    title_of = {}
    for m in markets:
        for ex in m.get("exchanges", []):
            title_of[ex["id"]] = m["title"] + ("" if not m.get("isMultiOutcome") else f" [{ex.get('option')}]")

    # 1) Engine-declared mutually exclusive sets (authoritative).
    try:
        for r in c.relationships("mutually_exclusive"):
            if r.get("status") != "active":
                continue
            ids = [n["exchangeId"] for n in r.get("nodes", []) if n.get("role") in (None, "member")]
            if len(ids) >= 2:
                groups[frozenset(ids)] = Group(r.get("label") or r["id"][:8], ids,
                                               [title_of.get(i, i) for i in ids],
                                               bool(r.get("isExhaustive")), r["id"])
    except ApiError as e:
        print(f"[warn] relationships unavailable: {e}")

    # 2) Title-parsed party pairs: "<Race> - Republican" / "<Race> - Democratic".
    by_race: Dict[str, List[Tuple[str, str]]] = {}
    for m in markets:
        if m.get("status") != "open" or m.get("isMultiOutcome") or len(m.get("exchanges", [])) != 1:
            continue
        parsed = parse_party(m["title"])
        if parsed:
            by_race.setdefault(parsed[0].lower(), []).append((m["exchanges"][0]["id"], m["title"]))
    for race, members in by_race.items():
        ids = [i for i, _ in members]
        if len(ids) >= 2 and frozenset(ids) not in groups:
            groups[frozenset(ids)] = Group(race, ids, [t for _, t in members])

    # 3) Multi-outcome markets whose options are one winner among candidates.
    #    Left to the engine relationships above; not assumed here.
    return list(groups.values())


def scan(c: Client, min_edge: float, budget: float = 0, depth: int = 20) -> List[Opportunity]:
    """budget=0 sizes each opportunity to full book depth (use allocate() to share cash)."""
    markets = c.markets(status="open")
    groups = build_groups(c, markets)
    all_ids = sorted({i for g in groups for i in g.exchange_ids})
    quotes = c.prices(all_ids)

    opps: List[Opportunity] = []
    for g in groups:
        q = [quotes.get(i) or {} for i in g.exchange_ids]
        bids = [x.get("bestBid") for x in q]
        asks = [x.get("bestAsk") for x in q]
        # Cheap top-of-book prefilter before pulling full books.
        no_hit = all(b is not None for b in bids) and sum(bids) - 1 >= min_edge
        yes_hit = all(a is not None for a in asks) and 1 - sum(asks) >= min_edge
        if not (no_hit or yes_hit):
            continue
        books = [c.orderbook(i, depth=depth) for i in g.exchange_ids]
        if no_hit:
            lad = [[(l["price"], l["quantity"]) for l in b["bids"]] for b in books]
            o = make_opp(g, "riskfree_no", lad, min_edge, budget)
            if o:
                opps.append(o)
        if yes_hit:
            lad = [[(l["price"], l["quantity"]) for l in b["asks"]] for b in books]
            o = make_opp(g, "riskfree_yes" if g.exhaustive else "conditional_yes", lad, min_edge, budget)
            if o:
                opps.append(o)
    opps.sort(key=lambda o: o.profit, reverse=True)
    return opps


def show_violations(c: Client, min_edge: float) -> None:
    try:
        vs = c.constraint_violations(min_violation=min_edge)
    except ApiError as e:
        print(f"[warn] constraints unavailable: {e}")
        return
    if not vs:
        return
    print(f"\nEngine-reported constraint violations ({len(vs)}):")
    for v in vs[:25]:
        print(f"  {v['type']:<18} viol={v['violationAmount']:.3f}  {v.get('reason', '')[:90]}")
        for t in v.get("suggestedCorrectiveTrades", [])[:4]:
            print(f"      -> {t['action']} {t['outcomeSide']} ex{t['exchangeId']} "
                  f"@{t.get('currentPrice')}  {t.get('marketTitle') or ''}")


def execute(c: Client, o: Opportunity, confirm: bool) -> None:
    side = "no" if o.kind == "riskfree_no" else "yes"
    legs = [c.leg(ex, side, "buy", o.sets, px) for ex, px in zip(o.group.exchange_ids, o.limits)]
    desc = ", ".join(f"buy {o.sets} {side.upper()} ex{l['exchangeId']}@{l['price']}" for l in legs)
    if confirm and input(f"  Execute [{desc}]? [y/N] ").strip().lower() != "y":
        return
    try:
        res = c.multi_leg(legs)
        print(f"  placed: {res}")
    except ApiError as e:
        print(f"  FAILED: {e}")


def report(opps: List[Opportunity]) -> None:
    if not opps:
        print("No arbitrage above threshold.")
        return
    print(f"{'kind':<16}{'race':<38}{'edge':>7}{'ROC%':>7}{'sets':>8}{'cost':>11}{'profit':>10}")
    for o in opps:
        print(f"{o.kind:<16}{o.group.label[:37]:<38}{o.edge_per_set:>7.3f}{100 * o.roc:>7.2f}{o.sets:>8}"
              f"{o.cost:>11.0f}{o.profit:>10.0f}")
    print(f"{'TOTAL':<54}{'':>14}{sum(o.cost for o in opps):>11.0f}{sum(o.profit for o in opps):>10.0f}")


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-edge", type=float, default=0.01, help="min worst-case profit per set (default 0.01)")
    ap.add_argument("--budget", "--max-cost", type=float, default=0,
                    help="total cash to spend across all trades (0 = balance)")
    ap.add_argument("--max-per-race", type=float, default=0, help="cash cap per race (0 = none)")
    ap.add_argument("--two-party-only", action="store_true",
                    help="skip races with an Independent / 3+ outcomes (resolution-overlap risk)")
    ap.add_argument("--execute", action="store_true", help="place risk-free trades via atomic multi-leg orders")
    ap.add_argument("--yes", action="store_true", help="skip per-trade confirmation")
    ap.add_argument("--watch", type=float, default=0, help="rescan every N seconds")
    args = ap.parse_args(argv)

    c = Client()
    t = c.tournament()
    print(f"Tournament: {t['name']} ({t['slug']})")
    while True:
        bal = c.balance()
        budget = min(args.budget, bal) if args.budget else bal
        print(f"\n[{time.strftime('%H:%M:%S')}] balance {bal:,.0f}  budget {budget:,.0f}")
        opps = scan(c, args.min_edge)
        if args.two_party_only:
            opps = [o for o in opps if o.group.two_party]
        plan = allocate(opps, budget, args.min_edge, args.max_per_race)
        print("\nAllocation (risk-free, best return on cash first):")
        report(plan)
        cond = [o for o in opps if o.kind == "conditional_yes"]
        if cond:
            print("\nConditional (not auto-traded; needs one listed party to win):")
            report(cond)
        show_violations(c, args.min_edge)
        if args.execute:
            for o in plan:
                execute(c, o, confirm=not args.yes)
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
