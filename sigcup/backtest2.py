"""Backtest round 2: what can add profit BEFORE election night, in today's market.

Tests only the post-chaos regime (default: trades from Oct 5 on) and measures capital
efficiency, because cash is the bottleneck.

  converge : the live "taker" -- buy when SIG is >= e away from Polymarket, sell when SIG catches up
  spike    : resting limit orders k from the 1h average (pm_filter: skip if Polymarket moved too,
             i.e. the move was real news)
  leadlag  : Polymarket moved >= d over L, SIG didn't -> follow it
  pairsum  : D + R prices sum to >= 1 + e -> buy NO on both, sell both when the sum normalizes
             (if it never does, the set still pays >= 1 at settlement)

Rules: decide on bar t, fill on bar t+1 (limit orders fill at their limit when touched),
pay `cost` per side, 1,000 per trade, one position per market.  Trades still open at the
end are marked at Polymarket's price.  "cap_pnl" limits fills to 25% of the candle volume.

Columns: pnl (total), h1/h2 (each half of the window -- both should be positive),
open (trades still open at the end), cap_k_days (capital tied up, in 1,000-days),
ret_day% (profit per day per unit of capital in use), proj_10k (projected profit over the
remaining days to the election per 10,000 of capital, at the backtest's pace).

    python -m sigcup.backtest2 [--since 2026-10-05] [--cost 0.005]
"""
from __future__ import annotations

import argparse
import itertools
import json
from datetime import datetime, timezone
from typing import Dict, List

import numpy as np
import pandas as pd

from .client import ROOT

HIST = ROOT / "data" / "hist"
STAKE = 1000.0
BARS_PER_DAY = 288
ELECTION = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)


def load(since: str) -> Dict:
    sig = pd.read_csv(HIST / "sig_5m.csv", parse_dates=["time"])
    pm = pd.read_csv(HIST / "pm_5m.csv", parse_dates=["time"])
    sig["exchange"], pm["exchange"] = sig.exchange.astype(str), pm.exchange.astype(str)
    idx = pd.date_range(sig.time.min().floor("5min"), sig.time.max().floor("5min"), freq="5min", tz="UTC")
    pm["time"] = pm.time.dt.floor("5min")

    def piv(df, col, f):
        return df.pivot_table(index="time", columns="exchange", values=col, aggfunc=f).reindex(idx)

    close = piv(sig, "close", "last").ffill()
    p = piv(pm, "p", "last").ffill()
    cols = list(close.columns.intersection(p.columns))
    return {"idx": idx, "cols": cols, "C": close[cols].values, "P": p[cols].values,
            "Lo": piv(sig, "low", "min")[cols].values, "Hi": piv(sig, "high", "max")[cols].values,
            "V": piv(sig, "volume", "sum")[cols].fillna(0).values,
            "start": int(idx.searchsorted(pd.Timestamp(since, tz="UTC")))}


def trade(D, j, i0, i1, side, entry, exit_px, is_open, frac=1.0) -> dict:
    """entry/exit_px in YES terms, costs already applied."""
    side_price = entry if side > 0 else 1 - entry
    move = (exit_px - entry) * side
    pnl = STAKE / max(side_price, 0.01) * move
    return {"j": j, "i0": i0, "i1": i1, "pts": move, "pnl": pnl, "cap_pnl": pnl * frac,
            "days": max(i1 - i0, 1) / BARS_PER_DAY, "open": is_open}


def summarize(name: str, params: dict, trades: List[dict], D) -> dict:
    n, s = len(D["idx"]), D["start"]
    if not trades:
        return {"strategy": name, **params, "trades": 0}
    tr = pd.DataFrame(trades)
    half = s + (n - s) // 2
    window_days = (n - s) / BARS_PER_DAY
    cap_days = tr.days.sum() * STAKE / 1000
    days_left = max(0.0, (ELECTION - D["idx"][-1].to_pydatetime()).total_seconds() / 86400)
    avg_capital = tr.days.sum() * STAKE / window_days  # average capital in use
    pnl = tr.pnl.sum()
    return {"strategy": name, **params, "trades": len(tr), "win%": round(100 * (tr.pts > 0).mean()),
            "avg_pts": round(tr.pts.mean(), 4), "pnl": round(pnl), "cap_pnl": round(tr.cap_pnl.sum()),
            "h1": round(tr[tr.i0 < half].pnl.sum()), "h2": round(tr[tr.i0 >= half].pnl.sum()),
            "open": int(tr.open.sum()), "hold_d": round(tr.days.mean(), 2),
            "cap_k_days": round(cap_days, 1), "ret_day%": round(100 * pnl / max(cap_days * 1000, 1), 2),
            "proj_10k": round(pnl / window_days / max(avg_capital, 1) * 10000 * days_left)}


# ---- strategies ----------------------------------------------------------------------------
def converge(D, e: float, band: float, max_hold_d, cost: float) -> List[dict]:
    C, P, V = D["C"], D["P"], D["V"]
    n, out = len(C), []
    hold = int(max_hold_d * BARS_PER_DAY) if max_hold_d else n
    for j in range(C.shape[1]):
        t = D["start"]
        while t < n - 2:
            s, p = C[t, j], P[t, j]
            if np.isnan(s) or np.isnan(p) or not 0.03 <= s <= 0.97 or V[t, j] <= 0 or abs(p - s) < e:
                t += 1
                continue
            side = 1 if p > s else -1
            i0 = t + 1
            entry = C[i0, j] + cost * side
            k = i0 + 1
            while k < n - 1 and k < i0 + hold and (P[k, j] - C[k, j]) * side > band:
                k += 1
            if k >= n - 1:  # still open at the end: mark at Polymarket
                out.append(trade(D, j, i0, n - 1, side, entry, P[n - 1, j], True))
                break
            x = C[min(k + 1, n - 1), j] - cost * side
            out.append(trade(D, j, i0, k + 1, side, entry, x, False))
            t = k + 1
    return out


def spike(D, k: float, hold_h: float, pm_filter: bool, cost: float) -> List[dict]:
    C, P, V, Lo, Hi = D["C"], D["P"], D["V"], D["Lo"], D["Hi"]
    n, out, hold = len(C), [], int(hold_h * 12)
    m = pd.DataFrame(C).rolling(12, min_periods=12).mean().shift(1).values
    for j in range(C.shape[1]):
        t = D["start"]
        while t < n - 1:
            mu = m[t, j]
            if np.isnan(mu) or not 0.08 <= mu <= 0.92 or V[t, j] <= 0:
                t += 1
                continue
            side = 1 if Lo[t, j] <= mu - k else -1 if Hi[t, j] >= mu + k else 0
            if not side or (pm_filter and not np.isnan(P[t, j]) and
                            (P[t, j] - P[max(t - 12, 0), j]) * side <= -k / 2):
                t += 1
                continue
            entry = mu - k * side
            kk = t + 1
            while kk < n - 1 and kk < t + hold and (C[kk, j] - mu) * side < -0.005:
                kk += 1
            side_price = entry if side > 0 else 1 - entry
            frac = min(1.0, 0.25 * V[t, j] / (STAKE / side_price))
            out.append(trade(D, j, t, kk, side, entry, C[kk, j] - cost * side, kk >= n - 1, frac))
            t = kk + 1
    return out


def leadlag(D, L: int, d: float, hold_h: float, cost: float) -> List[dict]:
    C, P, V = D["C"], D["P"], D["V"]
    n, out, hold = len(C), [], int(hold_h * 12)
    for j in range(C.shape[1]):
        t = max(D["start"], L)
        while t < n - 2:
            dp, ds = P[t, j] - P[t - L, j], C[t, j] - C[t - L, j]
            if np.isnan(dp) or np.isnan(ds) or abs(dp) < d or ds * np.sign(dp) >= 0.5 * abs(dp) \
                    or not 0.03 <= C[t, j] <= 0.97:
                t += 1
                continue
            side = int(np.sign(dp))
            i0 = t + 1
            entry = C[i0, j] + cost * side
            k = i0 + 1
            while k < n - 1 and k < i0 + hold and (C[k, j] - C[i0, j]) * side < 0.7 * abs(dp) \
                    and (P[k, j] - P[t, j]) * side > -abs(dp):
                k += 1
            x = C[min(k + 1, n - 1), j] - cost * side
            out.append(trade(D, j, i0, k + 1, side, entry, x, k >= n - 1))
            t = k + 1
    return out


def pairsum(D, e: float, band: float, cost: float) -> List[dict]:
    pm_map = json.loads((ROOT / "data" / "pm_map.json").read_text())
    col = {ex: i for i, ex in enumerate(D["cols"])}
    races: Dict[str, Dict[str, int]] = {}
    for ex, r in pm_map.items():
        if ex in col:
            races.setdefault(r["race"], {})[r["party"]] = col[ex]
    C, V = D["C"], D["V"]
    n, out = len(C), []
    for legs in races.values():
        if "democratic" not in legs or "republican" not in legs:
            continue
        a, b = legs["democratic"], legs["republican"]
        s_ = C[:, a] + C[:, b]
        t = D["start"]
        while t < n - 2:
            if np.isnan(s_[t]) or s_[t] < 1 + e or (V[t, a] <= 0 and V[t, b] <= 0):
                t += 1
                continue
            i0 = t + 1
            cost_set = 2 - s_[i0] + 2 * cost
            k = i0 + 1
            while k < n - 1 and s_[k] > 1 + band:
                k += 1
            if k >= n - 1:  # never normalized: hold, the set pays at least 1
                pnl_set, is_open, i1 = 1 - cost_set, True, n - 1
            else:
                pnl_set, is_open, i1 = (2 - s_[k + 1] - 2 * cost) - cost_set, False, k + 1
            sets = STAKE / cost_set
            out.append({"j": a, "i0": i0, "i1": i1, "pts": pnl_set, "pnl": sets * pnl_set,
                        "cap_pnl": sets * pnl_set, "days": max(i1 - i0, 1) / BARS_PER_DAY, "open": is_open})
            t = i1
    return out


def run(since: str, cost: float) -> pd.DataFrame:
    D = load(since)
    idx = D["idx"]
    print(f"{len(D['cols'])} markets | trades from {idx[D['start']]:%b %d %H:%M} to {idx[-1]:%b %d %H:%M} UTC"
          f" | cost {cost}/side | election in {(ELECTION - idx[-1].to_pydatetime()).days} days")
    rows = []
    for e, mh in itertools.product((0.03, 0.05, 0.08), (3, None)):
        rows.append(summarize("converge", {"e": e, "max_hold_d": mh or "end"}, converge(D, e, 0.005, mh, cost), D))
    for k, h, f in itertools.product((0.02, 0.03, 0.05, 0.08), (6, 24), (False, True)):
        rows.append(summarize("spike", {"k": k, "hold_h": h, "pm_filter": f}, spike(D, k, h, f, cost), D))
    for L, d, h in itertools.product((12, 36), (0.02, 0.03), (6, 24)):
        rows.append(summarize("leadlag", {"L_h": L / 12, "d": d, "hold_h": h}, leadlag(D, L, d, h, cost), D))
    for e in (0.01, 0.02, 0.03):
        rows.append(summarize("pairsum", {"e": e}, pairsum(D, e, 0.0, cost), D))
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-10-05")
    ap.add_argument("--cost", type=float, default=0.005)
    a = ap.parse_args()
    res = run(a.since, a.cost)
    res.to_csv(ROOT / "data" / f"backtest2_cost{a.cost}.csv", index=False)
    pd.set_option("display.width", 250, "display.max_rows", 200, "display.max_columns", 30)
    for s, g in res.groupby("strategy", sort=False):
        g = g.dropna(axis=1, how="all").drop(columns="strategy")
        print(f"\n== {s} ==")
        print(g.sort_values("pnl", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
