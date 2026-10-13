"""Fair-value trading bot with a capped budget and pluggable strategies.

Fair value = Polymarket mid (overridden by data/overrides.json for race calls).

Strategies (run together, each market is owned by one strategy):
  taker : SIG ask <= fair - entry -> buy YES ; SIG bid >= fair + entry -> buy NO   (take liquidity)
  maker : rest bids on both sides at fair -/+ spread in competitive markets; when one fills,
          the opposite quote (and the exit rule) sells it back near fair      (provide liquidity)
Both exit any position once SIG is back within exit_band of fair, freeing the cash.

The bot never touches positions that existed when it was first started (snapshot in
data/manual_positions.json) unless --include-manual.  --capital caps the total cost basis
the bot may hold.  Dry run unless --live.  `touch data/STOP` cancels all orders and exits.

Usage:
    python -m sigcup.bot --mode test                    # 7.5k sandbox, dry run
    python -m sigcup.bot --mode test --live
    python -m sigcup.bot --mode test --live --strategies maker --capital 5000
    python -m sigcup.bot --mode election --live         # election night
    python -m sigcup.botreport                          # bot P&L
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Dict, Optional, Sequence, Set, Tuple

from .client import ROOT, TICK, ApiError, Client, on_tick
from .arb import build_groups, walk_sets
from .fill import take
from .pmfeed import build_map, fair_values

DATA = ROOT / "data"
OVERRIDES = DATA / "overrides.json"
STOP = DATA / "STOP"
LOG = DATA / "bot_trades.csv"
MANUAL = DATA / "manual_positions.json"
OWNERS = DATA / "bot_owners.json"
START = DATA / "bot_start.json"
BASELINE = DATA / "night_baseline.json"


@dataclass
class Config:
    entry: float              # taker: min edge vs fair to open
    min_fill_edge: float      # taker: worst fill still keeps this much edge
    exit_band: float          # sell once SIG is within this of fair
    size: float               # taker: cash per entry
    max_per_market: float     # max cost basis held in one market
    cash_reserve: float       # never spend account cash below this
    interval: float           # seconds between loops
    capital: float = 0        # max total cost basis held by the bot (0 = no cap)
    strategies: Tuple[str, ...] = ("taker",)
    maker_spread: float = 0.02   # maker: quote this far from fair
    maker_size: float = 400      # maker: cash per quote
    maker_markets: int = 10      # maker: how many markets to quote
    max_gap: float = 0.35        # ignore |SIG - fair| above this (likely bad mapping) unless overridden
    max_trades_6h: int = 6       # anti-churn: a market traded more than this in 6 hours gets no new buys
    max_trades_1h: int = 60      # anti-churn: all new buys pause if the bot trades more than this per hour
    exits: bool = True
    budgets: Optional[Dict[str, float]] = None  # per-strategy cost caps; default = capital split equally
    pair_min_edge: float = 0.01      # pairs: open when sum(YES bids) >= 1 + this
    pair_exit_profit: float = 0.005  # pairs: unwind when selling now beats entry cost by this per set
    pair_size: float = 2500          # pairs: max cash per pair
    spike_k: float = 0.05            # spike: rest orders this far from the 1h average price
    spike_size: float = 250          # spike: cash per resting order
    spike_markets: int = 30          # spike: most active markets to cover
    spike_max_hold_h: float = 6      # spike: sell anyway after this long
    max_spread: float = 0.06         # ignore Polymarket books wider than this
    confirm_loops: int = 1           # taker: an edge must show up this many loops in a row
    edge_scaling: bool = False       # taker: bigger edge -> bigger trade (up to 2x size)
    manage_all: bool = False         # election: manage every position (manual, wave, pairs...)
    decided_sell: float = 0.0        # election: sell a decided race (fair >= 0.97) once SIG pays this
    rank_by_speed: bool = False      # taker: rank gaps by expected return per day (close races recycle faster)
    entry_close: float = 0.0         # taker: lower entry threshold for close races (fair 30-70%), 0 = same
    kalshi_confirm: bool = False     # taker: skip if Kalshi (when listed) disagrees with Polymarket by > 3 pts
    require_move: float = 0.0        # election: only trade markets whose Polymarket price moved this much
                                     # since the bot started (result-driven moves; keeps cash for the night)


PRESETS = {
    "test": Config(entry=0.03, min_fill_edge=0.015, exit_band=0.005, size=1000, max_per_market=2000,
                   cash_reserve=0, interval=20, capital=7500, strategies=("taker", "maker")),
    "daily": Config(entry=0.035, min_fill_edge=0.02, exit_band=0.005, size=2500, max_per_market=8000,
                    cash_reserve=10000, interval=30),
    "multi": Config(entry=0.035, min_fill_edge=0.02, exit_band=0.005, size=1000, max_per_market=2000,
                    cash_reserve=2000, interval=20, strategies=("pairs", "spike"),
                    budgets={"pairs": 5000, "spike": 4000},
                    rank_by_speed=True, entry_close=0.03, kalshi_confirm=True),
    "election": Config(entry=0.04, min_fill_edge=0.02, exit_band=0.01, size=3000, max_per_market=12000,
                       cash_reserve=0, interval=2, max_gap=0.95, strategies=("taker",), max_spread=0.10,
                       confirm_loops=2, edge_scaling=True, manage_all=True, decided_sell=0.95,
                       require_move=0.05, max_trades_6h=12, max_trades_1h=300),
}


def floor_tick(p: float) -> float:
    return round(math.floor(p / TICK + 1e-9) * TICK, 3)


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _save(path, obj) -> None:
    DATA.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, indent=1))


def load_overrides(pm_map: Dict[str, dict]) -> Dict[str, float]:
    raw = _load(OVERRIDES, {})
    by_title = {r["title"]: ex for ex, r in pm_map.items()}
    return {by_title.get(k, k): float(v) for k, v in raw.items()}


def log(row: dict) -> None:
    new = not LOG.exists()
    with open(LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


class Bot:
    def __init__(self, c: Client, cfg: Config, live: bool, include_manual: bool = False):
        self.c, self.cfg, self.live = c, cfg, live
        self.pm_map = build_map(c)
        self.ids = list(self.pm_map)
        self.manual: Set[str] = set()
        if not include_manual:
            if not MANUAL.exists():
                _save(MANUAL, sorted(p["exchangeId"] for p in c.positions()["positions"] if p["quantity"]))
            self.manual = set(_load(MANUAL, []))
        if not START.exists():
            _save(START, {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "cash": c.balance()})
        self.owners: Dict[str, str] = _load(OWNERS, {})
        self.quotes: Dict[Tuple[str, str], dict] = {}   # (exchange, side) -> {id, price, qty}
        self.groups = None                               # pairs: two-party races
        self.mids: Dict[str, list] = {}                  # spike: recent (ts, mid) per exchange
        self.spike_universe: Optional[list] = None
        self.spike_entries: Dict[str, float] = _load(DATA / "spike_entries.json", {})
        self.seen: Dict[Tuple[str, str], int] = {}       # taker: consecutive loops an edge was seen
        self.trade_times: Dict[str, list] = {}            # anti-churn: recent trade timestamps per market
        self.churn_warned: Set[str] = set()
        self.fair_hook = None                            # rehearsal: fn(loop, fair) injecting fake moves
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.base_fair: Dict[str, float] = _load(BASELINE, {}) if (live and cfg.require_move) else {}
        self.overrides: Dict[str, float] = {}
        self.race_of: Dict[str, str] = {ex: r["race"] for ex, r in self.pm_map.items()}
        self.kalshi: Dict[str, Tuple[float, float]] = {}  # exchange -> (Kalshi mid, volume)
        self.kalshi_job, self.kalshi_ts = None, 0.0

    # ---- state -------------------------------------------------------------
    def held(self) -> Dict[str, dict]:
        out = {}
        for p in self.c.positions()["positions"]:
            if p.get("settled") or not p["quantity"] or p["exchangeId"] in self.manual:
                continue
            out[p["exchangeId"]] = {"side": "yes" if p["quantity"] > 0 else "no",
                                    "qty": int(abs(p["quantity"])), "cost": p["costBasis"]}
        return out

    def own(self, ex: str, strategy: str) -> bool:
        """Claim a market for a strategy; False if another strategy owns it."""
        cur = self.owners.get(ex)
        if cur and cur != strategy:
            return False
        if not cur:
            self.owners[ex] = strategy
            if self.live:  # dry runs don't claim markets for later live runs
                _save(OWNERS, self.owners)
        return True

    def tag(self, ex: str) -> str:
        return self.owners.get(ex, "-")

    # ---- order helpers -----------------------------------------------------
    def say(self, kind: str, ex: str, side: str, action: str, fair: float, res: Optional[dict], **kw) -> None:
        title = self.pm_map.get(ex, {}).get("title", ex)
        filled = (res or {}).get("quantityTraded", 0)
        print(f"  {'LIVE' if self.live else 'dry '} {self.tag(ex):<5} {kind:<6} {action} {side.upper():<3} "
              f"{title[9:58]:<50} fair {fair:.3f} {json.dumps(kw)}"
              + (f" -> filled {filled} for {res.get('totalCost')}" if res else ""))
        if res and filled:
            log({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "strategy": self.tag(ex), "kind": kind,
                 "title": title, "exchange": ex, "action": action, "side": side, "qty": filled,
                 "cost": res.get("totalCost"), "fair": round(fair, 4)})

    def note_trade(self, ex: str, res: Optional[dict]) -> None:
        if res and res.get("quantityTraded"):
            self.trade_times.setdefault(ex, []).append(time.time())

    def churning(self, ex: str) -> bool:
        """True if this market traded too often recently -- blocks new buys (stops any buy/sell loop)."""
        recent = [t for t in self.trade_times.get(ex, []) if t > time.time() - 6 * 3600]
        self.trade_times[ex] = recent
        last_hour = sum(1 for ts in self.trade_times.values() for t in ts if t > time.time() - 3600)
        if last_hour > self.cfg.max_trades_1h:  # global brake: something is looping somewhere
            if "*" not in self.churn_warned:
                self.churn_warned.add("*")
                print(f"  !! CHURN GUARD: {last_hour} trades in the last hour -- all new buys paused")
            return True
        self.churn_warned.discard("*")
        if len(recent) >= self.cfg.max_trades_6h:
            if ex not in self.churn_warned:
                self.churn_warned.add(ex)
                print(f"  !! CHURN GUARD: {self.pm_map.get(ex, {}).get('title', ex)} traded {len(recent)}x in 6h"
                      f" -- no new buys there for now")
            return True
        return False

    def buy(self, kind: str, ex: str, side: str, fair: float, budget: float, max_price: float, **extra) -> None:
        if self.churning(ex):
            return
        res = None
        if self.live:
            try:
                res = take(self.c, ex, side, budget, max_price)
            except ApiError as e:
                print(f"  ! {kind} {ex}: {e}")
                return
        self.note_trade(ex, res)
        self.say(kind, ex, side, "buy", fair, res, budget=round(budget), max_price=max_price, **extra)

    def sell(self, kind: str, ex: str, side: str, fair: float, qty: int, min_price: float) -> float:
        """Returns the cash raised (estimated in dry runs)."""
        res = None
        if self.live:
            try:
                res = self.c.place_order(ex, side, "sell", qty, min_price)
                if res.get("open") and res.get("orderId"):
                    self.c.cancel(res["orderId"])  # never leave exits resting
            except ApiError as e:
                print(f"  ! {kind} {ex}: {e}")
                return 0.0
        self.note_trade(ex, res)
        self.say(kind, ex, side, "sell", fair, res, qty=qty, min_price=min_price)
        return float(res.get("totalCost") or 0) if res else qty * min_price

    def cancel_quote(self, key: Tuple[str, str]) -> None:
        q = self.quotes.pop(key, None)
        if q and self.live and q.get("id"):
            try:
                self.c.cancel(q["id"])
            except Exception:
                pass  # already filled/gone, or offline (cancel_all retries later)

    def cancel_all(self) -> None:
        for key in list(self.quotes):
            self.cancel_quote(key)
        if self.live:
            try:
                self.c.cancel_all()
            except Exception as e:
                print(f"  ! cancel-all failed (will retry): {e!r}")
                return

    # ---- strategies --------------------------------------------------------
    def exits(self, fair, quotes, held) -> None:
        cfg, band = self.cfg, self.cfg.exit_band
        raised, cashins = 0.0, []
        for ex, h in held.items():
            q, f = quotes.get(ex) or {}, fair.get(ex)
            if f is None or (not cfg.manage_all and self.owners.get(ex) in ("pairs", "spike", "wave")):
                continue  # those manage their own exits / are held to election night
            f_side = f if h["side"] == "yes" else 1 - f
            # what SIG pays for our side right now
            bid_side = q.get("bestBid") if h["side"] == "yes" else (
                None if q.get("bestAsk") is None else 1 - q["bestAsk"])
            if bid_side is None:
                continue
            if cfg.decided_sell and f_side >= 0.97:
                # decided for us: sell only if the cash is needed (see step), at >= decided_sell
                if bid_side >= cfg.decided_sell:
                    cashins.append((f_side - bid_side, ex, h, f))
                continue
            if cfg.manage_all and self.owners.get(ex) == "wave" and f_side > 0.15:
                continue  # wave bets ride until their race is decided (or clearly lost: salvage)
            if bid_side >= f_side - band:
                raised += self.sell("EXIT", ex, h["side"], f, h["qty"], on_tick(max(0.005, f_side - band)))
        return raised, cashins

    def cash_in(self, cashins, need: float, best_edge: float) -> float:
        """Sell decided winners (smallest discount first) until `need` cash is raised -- only those
        whose discount (fair - SIG price) is at least 3 points smaller than the edge we'd buy."""
        raised = 0.0
        for disc, ex, h, f in sorted(cashins, key=lambda x: x[0]):
            if raised >= need or disc > best_edge - 0.03:
                break
            raised += self.sell("CASHIN", ex, h["side"], f, h["qty"], on_tick(self.cfg.decided_sell))
        return raised

    @staticmethod
    def speed(f: float) -> float:
        """Relative return-per-day weight by fair value (Oct 5-12 backtest: close races recycle in ~1 day,
        long shots mostly only pay at settlement)."""
        d = abs(f - 0.5)
        return 1.5 if d <= 0.2 else 1.2 if d <= 0.4 else 1.0

    def refresh_kalshi(self) -> None:
        """Kalshi prices refreshed in the background every 5 minutes (used only as a sanity check)."""
        from .kalshi import kalshi_fair
        if self.kalshi_job and self.kalshi_job.done():
            try:
                self.kalshi = self.kalshi_job.result()
            except Exception as e:
                print(f"  ! kalshi: {e!r}")
            self.kalshi_job = None
        if self.kalshi_job is None and time.time() - self.kalshi_ts > 300:
            self.kalshi_ts = time.time()
            self.kalshi_job = self.pool.submit(kalshi_fair, self.pm_map)

    def kalshi_ok(self, ex: str, f: float) -> bool:
        k = self.kalshi.get(ex)
        return k is None or abs(k[0] - f) <= 0.03

    def moved(self, ex: str, f: float) -> bool:
        if not self.cfg.require_move or ex in self.overrides:
            return True
        b = self.base_fair.get(ex)
        return b is not None and abs(f - b) >= self.cfg.require_move

    def candidates(self, fair, quotes) -> list:
        """Edges the taker will act on this loop (moved + confirmed), used to decide cash-ins."""
        out = []
        need_seen = self.cfg.confirm_loops - 1  # self.seen still holds counts up to the last loop
        for ex, f in fair.items():
            if not self.moved(ex, f):
                continue
            q = quotes.get(ex) or {}
            ok = lambda side: ex in self.overrides or self.seen.get((ex, side), 0) >= need_seen
            if q.get("bestAsk") is not None and f - q["bestAsk"] >= self.cfg.entry and ok("yes"):
                out.append(f - q["bestAsk"])
            if q.get("bestBid") is not None and q["bestBid"] - f >= self.cfg.entry and ok("no"):
                out.append(q["bestBid"] - f)
        return [e for e in out if e <= self.cfg.max_gap]

    def taker(self, fair, quotes, held, overrides, cash, room_total) -> float:
        cfg, spent = self.cfg, 0.0
        cands = []
        for ex, f in fair.items():
            if ex in self.manual:
                continue
            if cfg.kalshi_confirm and not self.kalshi_ok(ex, f) and ex not in overrides:
                continue  # Polymarket and Kalshi disagree: the "edge" may not be real
            q = quotes.get(ex) or {}
            bid, ask = q.get("bestBid"), q.get("bestAsk")
            entry = cfg.entry_close if cfg.entry_close and abs(f - 0.5) <= 0.2 else cfg.entry
            if ask is not None and f - ask >= entry:
                cands.append((f - ask, ex, "yes", f))
            if bid is not None and bid - f >= entry:
                cands.append((bid - f, ex, "no", f))
        race_cost: Dict[str, float] = {}
        if cfg.manage_all:  # election night: cap per RACE (YES-D and NO-R are the same bet)
            for hx, hh in held.items():
                r = self.race_of.get(hx, hx)
                race_cost[r] = race_cost.get(r, 0.0) + hh["cost"]
        keys = {(ex, side) for _, ex, side, _ in cands}
        self.seen = {k: self.seen.get(k, 0) + 1 for k in keys}  # edges must persist confirm_loops
        rank = (lambda c_: c_[0] * self.speed(c_[3])) if cfg.rank_by_speed else (lambda c_: c_[0])
        for edge, ex, side, f in sorted(cands, key=rank, reverse=True):
            if edge > cfg.max_gap and ex not in overrides:
                continue
            if not self.moved(ex, f):
                continue
            if self.seen.get((ex, side), 0) < cfg.confirm_loops and ex not in overrides:
                continue
            h = held.get(ex)
            if h and h["side"] != side:
                continue
            size = cfg.size * (min(2.0, edge / cfg.entry) if cfg.edge_scaling else 1.0)
            budget = min(size, cfg.max_per_market - (h["cost"] if h else 0),
                         cash - cfg.cash_reserve - spent, room_total - spent)
            if cfg.manage_all:
                r = self.race_of.get(ex, ex)
                budget = min(budget, cfg.max_per_market - race_cost.get(r, 0.0))
                race_cost[r] = race_cost.get(r, 0.0) + max(budget, 0.0)
            if budget < 50 or (not cfg.manage_all and not self.own(ex, "taker")):
                continue
            fair_side = f if side == "yes" else 1 - f
            self.buy("ENTER", ex, side, f, budget, on_tick(fair_side - cfg.min_fill_edge), edge=round(edge, 3))
            spent += budget
        return spent

    def maker(self, fair, quotes, held, room_total) -> None:
        cfg = self.cfg
        universe = [ex for ex, f in fair.items()
                    if ex not in self.manual and 0.1 <= f <= 0.9 and self.owners.get(ex, "maker") == "maker"
                    and (quotes.get(ex) or {}).get("bestBid") is not None]
        universe = sorted(universe, key=lambda ex: abs(fair[ex] - 0.5))[:cfg.maker_markets]
        wanted: Dict[Tuple[str, str], Tuple[float, int]] = {}
        budget_left = room_total
        for ex in universe:
            f, h = fair[ex], held.get(ex)
            for side, px in (("yes", floor_tick(f - cfg.maker_spread)), ("no", floor_tick(1 - f - cfg.maker_spread))):
                if px < 0.01:
                    continue
                if h and h["side"] != side:
                    qty = h["qty"]  # opposite quote = passive exit of the inventory
                else:
                    room = cfg.max_per_market - (h["cost"] if h else 0)
                    cash = min(cfg.maker_size, room, budget_left)
                    if cash < px * 10:
                        continue
                    budget_left -= cash
                    qty = int(cash / px)
                wanted[(ex, side)] = (px, qty)
        # cancel quotes no longer wanted or mispriced, place new ones
        for key in list(self.quotes):
            if key not in wanted or self.quotes[key]["price"] != wanted[key][0]:
                self.cancel_quote(key)
        for (ex, side), (px, qty) in wanted.items():
            if (ex, side) in self.quotes or not self.own(ex, "maker"):
                continue
            res = None
            if self.live:
                try:
                    res = self.c.place_order(ex, side, "buy", qty, px)
                except ApiError as e:
                    print(f"  ! QUOTE {ex}: {e}")
                    continue
                if res.get("open") and res.get("orderId"):
                    self.quotes[(ex, side)] = {"id": res["orderId"], "price": px, "qty": qty}
            else:
                self.quotes[(ex, side)] = {"id": None, "price": px, "qty": qty}
            self.say("QUOTE", ex, side, "buy", fair[ex], res if res and res.get("quantityTraded") else None,
                     qty=qty, price=px)

    def multi(self, kind: str, legs: list, fairs: list) -> None:
        res = None
        if self.live:
            try:
                res = self.c.multi_leg(legs)
            except ApiError as e:
                print(f"  ! {kind} pair: {e}")
                return
            for r in res.get("results", []):
                d = r.get("data") or {}
                if d.get("open") and d.get("orderId"):
                    try:
                        self.c.cancel(d["orderId"])  # never leave pair legs resting
                    except Exception:
                        pass
        results = (res or {}).get("results") or [None] * len(legs)
        for leg, f, r in zip(legs, fairs, results):
            self.note_trade(leg["exchangeId"], (r or {}).get("data"))
            self.say(kind, leg["exchangeId"], leg["side"], leg["action"], f if f is not None else float("nan"),
                     (r or {}).get("data"), qty=leg["quantity"], price=leg.get("price"))

    def pairs(self, fair, quotes, held, room_total) -> float:
        cfg, spent = self.cfg, 0.0
        if self.groups is None:
            gs = build_groups(self.c, self.c.markets(status="open"))
            self.groups = [g for g in gs if g.two_party and not set(g.exchange_ids) & self.manual
                           and all(self.owners.get(e, "pairs") == "pairs" for e in g.exchange_ids)]
        for g in self.groups:
            ids = g.exchange_ids
            if any(self.owners.get(i, "pairs") != "pairs" for i in ids):
                continue  # another strategy trades this race now -- never touch its positions
            qs = [quotes.get(i) or {} for i in ids]
            hs = [held.get(i) for i in ids]
            fs = [fair.get(i) for i in ids]
            if any(hs):
                qty = min((h["qty"] if h and h["side"] == "no" else 0) for h in hs)
                # 1) repair imbalance: sell any leg beyond the complete sets
                for i, h in zip(ids, hs):
                    extra = (h["qty"] - qty) if h else 0
                    if h and extra > 0:
                        ask = (quotes.get(i) or {}).get("bestAsk")
                        px = on_tick(max(0.005, 1 - (ask or 1) - 0.01)) if h["side"] == "no" else \
                            on_tick(max(0.005, ((quotes.get(i) or {}).get("bestBid") or 0) - 0.01))
                        self.sell("REPAIR", i, h["side"], fair.get(i) or float("nan"), extra, px)
                if qty <= 0:
                    continue
                # 2) unwind complete sets when selling now beats what we paid
                per_set = sum(h["cost"] / h["qty"] for h in hs)
                asks = [q.get("bestAsk") for q in qs]
                if None in asks or len(ids) - sum(asks) - per_set < cfg.pair_exit_profit:
                    continue
                lad = [[(l["price"], l["quantity"]) for l in self.c.orderbook(i)["asks"]] for i in ids]
                n, worst = self._unwind(lad, per_set, qty)
                if n:
                    self.multi("UNWIND", [self.c.leg(i, "no", "sell", n, on_tick(1 - w)) for i, w in zip(ids, worst)], fs)
                continue
            # 3) open a new risk-free set
            bids = [q.get("bestBid") for q in qs]
            budget = min(cfg.pair_size, room_total - spent)
            if None in bids or sum(bids) - 1 < cfg.pair_min_edge or budget < 100:
                continue
            lad = [[(l["price"], l["quantity"]) for l in self.c.orderbook(i)["bids"]] for i in ids]
            sets, profit, cost, worst = walk_sets(lad, cfg.pair_min_edge, budget, buy_no=True)
            if cost < 100 or any(self.churning(i) for i in ids) or not all(self.own(i, "pairs") for i in ids):
                continue
            self.multi("PAIR", [self.c.leg(i, "no", "buy", sets, on_tick(1 - w)) for i, w in zip(ids, worst)], fs)
            spent += cost
        return spent

    def _unwind(self, ladders, per_set: float, max_qty: int):
        """Selling NO on every leg lifts each leg's YES asks; proceeds per set = N - sum(asks)."""
        n, idx = len(ladders), [0] * len(ladders)
        if any(not lad for lad in ladders):
            return 0, []
        left = [lad[0][1] for lad in ladders]
        sets, worst = 0, []
        while sets < max_qty:
            px = [ladders[i][idx[i]][0] for i in range(n)]
            if n - sum(px) - per_set < self.cfg.pair_exit_profit:
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

    def _seed_spike(self) -> None:
        """Pick the most active markets and seed their 1h average from 5-minute history."""
        act = []
        for ex in self.ids:
            if ex in self.manual or self.owners.get(ex, "spike") != "spike":
                continue
            try:
                cs = self.c.get(f"/exchanges/{ex}/price-history", tournamentId=self.c.tid,
                                resolution="5m", limit=288).get("candles") or []
            except ApiError:
                continue
            if len(cs) < 12:
                continue
            now = time.time()
            self.mids[ex] = [(now - 300 * (12 - i), c_["close"]) for i, c_ in enumerate(cs[-12:])]
            act.append((sum(1 for c_ in cs if c_.get("volume")), ex))
        self.spike_universe = [ex for _, ex in sorted(act, reverse=True)[:self.cfg.spike_markets]]
        print(f"  spike: watching {len(self.spike_universe)} most active markets")

    def spike(self, quotes, held, room_total) -> None:
        cfg, now = self.cfg, time.time()
        if self.spike_universe is None:
            self._seed_spike()
        wanted: Dict[Tuple[str, str], Tuple[float, int]] = {}
        budget_left = room_total
        for ex in self.spike_universe:
            q = quotes.get(ex) or {}
            if q.get("bestBid") is None or q.get("bestAsk") is None:
                continue
            hist = [x for x in self.mids.get(ex, []) if x[0] >= now - 3600] + [(now, (q["bestBid"] + q["bestAsk"]) / 2)]
            self.mids[ex] = hist
            mean = sum(m for _, m in hist[:-1]) / max(len(hist) - 1, 1)
            h = held.get(ex)
            if h and self.owners.get(ex) == "spike":
                # exit: back near the average, or held too long
                t0 = self.spike_entries.setdefault(ex, now)
                back = (q["bestBid"] >= mean - 0.005) if h["side"] == "yes" else (q["bestAsk"] <= mean + 0.005)
                if back or now - t0 > cfg.spike_max_hold_h * 3600:
                    px = q["bestBid"] if h["side"] == "yes" else 1 - q["bestAsk"]
                    self.sell("SPIKE-X", ex, h["side"], mean, h["qty"], on_tick(max(0.005, px - 0.01)))
                    self.spike_entries.pop(ex, None)
                _save(DATA / "spike_entries.json", self.spike_entries)
                continue
            if not 0.08 <= mean <= 0.92:
                continue
            for side, px in (("yes", floor_tick(mean - cfg.spike_k)), ("no", floor_tick(1 - mean - cfg.spike_k))):
                cash = min(cfg.spike_size, budget_left)
                if px < 0.02 or cash < px * 10:
                    continue
                budget_left -= cash / 3  # resting orders rarely all fill; allow ~3x budget in quotes
                wanted[(ex, side)] = (px, int(cash / px))
        for key in [k for k in self.quotes if self.owners.get(k[0]) == "spike" or k[0] in self.spike_universe]:
            if key not in wanted or abs(self.quotes[key]["price"] - wanted[key][0]) >= 0.01:
                self.cancel_quote(key)
        for (ex, side), (px, qty) in wanted.items():
            if (ex, side) in self.quotes or not self.own(ex, "spike"):
                continue
            res = None
            if self.live:
                try:
                    res = self.c.place_order(ex, side, "buy", qty, px)
                except ApiError as e:
                    print(f"  ! SPIKE {ex}: {e}")
                    continue
                if res.get("open") and res.get("orderId"):
                    self.quotes[(ex, side)] = {"id": res["orderId"], "price": px, "qty": qty}
                if res.get("quantityTraded"):
                    self.spike_entries[ex] = now
            else:
                self.quotes[(ex, side)] = {"id": None, "price": px, "qty": qty}
        n = sum(1 for k in self.quotes if self.owners.get(k[0]) == "spike" or k[0] in self.spike_universe)
        print(f"  spike: {n} resting orders, {cfg.spike_k:.2f} from the 1h average")

    def reconcile(self) -> None:
        """Cancel open orders the bot isn't tracking (e.g. left behind by a 502 'status unknown')."""
        if not self.live:
            return
        try:
            res = self.c.get("/orders", status="open", tournamentId=self.c.tid, limit=200)
        except Exception as e:
            print(f"  ! reconcile: {e!r}")
            return
        tracked = {q["id"] for q in self.quotes.values() if q.get("id")}
        stray = [o for o in (res.get("data") or []) if o.get("id") not in tracked]
        for o in stray:
            try:
                self.c.cancel(o["id"])
            except Exception:
                pass
        if stray:
            print(f"  reconcile: cancelled {len(stray)} untracked open orders")

    # ---- loop --------------------------------------------------------------
    def step(self) -> None:
        cfg = self.cfg
        self.loops = getattr(self, "loops", 0) + 1
        if self.loops % 15 == 0:  # ~every 5 minutes
            self.reconcile()
        t0 = time.time()
        jobs = [self.pool.submit(fair_values, self.pm_map, cfg.max_spread), self.pool.submit(self.c.prices, self.ids),
                self.pool.submit(self.held), self.pool.submit(self.c.balance)]
        fair, quotes, held, cash = (j.result() for j in jobs)
        if cfg.require_move:
            new = {ex: f for ex, f in fair.items() if ex not in self.base_fair}
            if new:
                self.base_fair.update(new)
                if self.live:
                    _save(BASELINE, self.base_fair)
        overrides = load_overrides(self.pm_map)
        self.overrides = overrides
        fair.update(overrides)
        if self.fair_hook:
            self.fair_hook(self.loops, fair, overrides)
        self.fetch_s = time.time() - t0
        deployed = sum(h["cost"] for h in held.values())
        # each strategy has its own budget (default: capital split equally)
        share = cfg.capital / len(cfg.strategies) if cfg.capital else float("inf")
        free_cash = max(0.0, cash - cfg.cash_reserve)

        def room(strategy: str) -> float:
            cap = (cfg.budgets or {}).get(strategy, share)
            used = sum(h["cost"] for ex, h in held.items() if self.owners.get(ex) == strategy)
            return max(0.0, min(cap - used, free_cash))

        print(f"[{time.strftime('%H:%M:%S')}] cash {cash:,.0f}  bot deployed {deployed:,.0f}"
              f"{f' / cap {cfg.capital:,.0f}' if cfg.capital else ''}  positions {len(held)}  quotes {len(self.quotes)}")
        if cfg.exits:
            raised, cashins = self.exits(fair, quotes, held)
            cash += raised
            if cashins:
                min_disc = min(c_[0] for c_ in cashins)
                edges = sorted((e for e in self.candidates(fair, quotes) if e >= min_disc + 0.03), reverse=True)
                need = sum(cfg.size * (min(2.0, e / cfg.entry) if cfg.edge_scaling else 1) for e in edges[:5])
                if edges and need > cash:
                    cash += self.cash_in(cashins, need - cash, edges[0])
        spent = 0.0
        if "taker" in cfg.strategies:
            if cfg.kalshi_confirm:
                self.refresh_kalshi()
            spent = self.taker(fair, quotes, held, overrides, cash, room("taker"))
        if "maker" in cfg.strategies:
            self.maker(fair, quotes, held, max(0.0, room("maker") - (spent if share == float("inf") else 0)))
        if "pairs" in cfg.strategies:
            self.pairs(fair, quotes, held, room("pairs"))
        if "spike" in cfg.strategies:
            self.spike(quotes, held, room("spike"))

    def run(self) -> None:
        print(f"Bot {'LIVE' if self.live else 'DRY RUN'}: {self.cfg}\n"
              f"ignoring {len(self.manual)} pre-existing positions")
        errors = 0
        try:
            while not STOP.exists():
                t = time.time()
                try:
                    self.step()
                    errors = 0
                except Exception as e:  # keep running through transient errors
                    errors += 1
                    print(f"  ! loop error #{errors}: {e!r}")
                    if errors == 2 and self.quotes:
                        # blind (no fair values / no connection): don't leave quotes resting
                        print("  ! pulling all resting quotes until the connection recovers")
                        self.cancel_all()
                time.sleep(max(0.0, self.cfg.interval - (time.time() - t)))
            print("STOP file found")
        except KeyboardInterrupt:
            print("interrupted")
        finally:
            print("cancelling resting orders")
            self.cancel_all()


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=list(PRESETS), default="test")
    ap.add_argument("--live", action="store_true", help="actually trade (default: dry run)")
    ap.add_argument("--once", action="store_true", help="run a single loop")
    ap.add_argument("--include-manual", action="store_true", help="also manage pre-existing positions")
    ap.add_argument("--no-exits", action="store_true", help="hold to resolution instead of recycling")
    ap.add_argument("--strategies", help="comma list: taker,maker,pairs")
    ap.add_argument("--budgets", help="per-strategy caps, e.g. pairs=5000,taker=4000")
    for f in ("entry", "size", "max_per_market", "cash_reserve", "interval", "capital",
              "maker_spread", "maker_size", "maker_markets", "spike_k", "spike_size", "spike_markets"):
        ap.add_argument(f"--{f.replace('_', '-')}", type=float)
    args = ap.parse_args(argv)
    cfg = PRESETS[args.mode]
    upd = {k: v for k, v in vars(args).items() if k in cfg.__dataclass_fields__ and v is not None}
    for f in ("maker_markets", "spike_markets"):
        if f in upd:
            upd[f] = int(upd[f])
    if args.strategies:
        upd["strategies"] = tuple(s.strip() for s in args.strategies.split(","))
    if args.budgets:
        upd["budgets"] = {k.strip(): float(v) for k, v in (kv.split("=") for kv in args.budgets.split(","))}
    cfg = replace(cfg, exits=not args.no_exits, **upd)
    STOP.unlink(missing_ok=True)  # clear a previous kill switch
    bot = Bot(Client(), cfg, args.live, args.include_manual or cfg.manage_all)
    if args.once:
        bot.step()
        print(f"  (data fetched in {bot.fetch_s:.1f}s)")
        if args.live:
            bot.cancel_all()
    else:
        bot.run()


if __name__ == "__main__":
    main()
