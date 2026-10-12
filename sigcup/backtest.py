"""Backtest intraday strategies on 5-minute history (data/hist, from sigcup.history).

Strategies (one position per market at a time, 1,000 SUSQies per trade):
  fade     : SIG price jumps >= k away from its own rolling mean -> bet it reverts
  leadlag  : Polymarket moves >= d in L bars while SIG hasn't -> bet SIG follows
  gaprev   : the SIG-minus-Polymarket gap moves >= k away from its usual (rolling median)
             level -> bet the gap returns to normal

Honesty rules: decide on bar t, fill at bar t+1's price, pay `cost` per side
(spread + slippage), skip prices outside 0.03-0.97, report first and second half
separately (if only one half makes money, it's probably luck).

    python -m sigcup.backtest
    python -m sigcup.backtest --cost 0.01
"""
from __future__ import annotations

import argparse
import itertools
from typing import Callable, List

import numpy as np
import pandas as pd

from .client import ROOT

HIST = ROOT / "data" / "hist"
STAKE = 1000.0


def load_grid():
    sig = pd.read_csv(HIST / "sig_5m.csv", parse_dates=["time"])
    pm = pd.read_csv(HIST / "pm_5m.csv", parse_dates=["time"])
    sig["exchange"], pm["exchange"] = sig.exchange.astype(str), pm.exchange.astype(str)
    start, end = sig.time.min().floor("5min"), sig.time.max().floor("5min")
    idx = pd.date_range(start, end, freq="5min", tz="UTC")
    s_close = sig.pivot_table(index="time", columns="exchange", values="close", aggfunc="last").reindex(idx).ffill()
    s_vol = sig.pivot_table(index="time", columns="exchange", values="volume", aggfunc="sum").reindex(idx).fillna(0)
    pm["time"] = pm.time.dt.floor("5min")
    p = pm.pivot_table(index="time", columns="exchange", values="p", aggfunc="last").reindex(idx).ffill()
    cols = s_close.columns.intersection(p.columns)
    return s_close[cols], s_vol[cols], p[cols]


def simulate(sig: pd.DataFrame, vol: pd.DataFrame, entry_fn: Callable, exit_fn: Callable,
             max_hold: int, cost: float) -> pd.DataFrame:
    """entry_fn(ex, t) -> +1 (buy YES) / -1 (buy NO) / 0 ; exit_fn(ex, t, side, i0) -> bool"""
    trades = []
    S = sig.values
    for j, ex in enumerate(sig.columns):
        t, n = 1, len(sig)
        while t < n - 1:
            side = entry_fn(j, t)
            p0 = S[t + 1, j]
            if side == 0 or not (0.03 <= p0 <= 0.97) or vol.values[t, j] <= 0:
                t += 1
                continue
            i0 = t + 1
            k = i0 + 1
            while k < min(n - 1, i0 + max_hold) and not exit_fn(j, k, side, i0):
                k += 1
            p1 = S[min(k + 1, n - 1), j]  # exit fills a bar later too
            move = (p1 - p0) * side - 2 * cost
            side_price = p0 if side > 0 else 1 - p0
            trades.append({"exchange": ex, "t0": sig.index[i0], "t1": sig.index[min(k + 1, n - 1)],
                           "side": side, "pts": move, "pnl": STAKE / side_price * move})
            t = k + 1
    return pd.DataFrame(trades)


def summarize(name: str, params: dict, tr: pd.DataFrame, mid) -> dict:
    if tr.empty:
        return {"strategy": name, **params, "trades": 0}
    a, b = tr[tr.t0 < mid], tr[tr.t0 >= mid]
    return {"strategy": name, **params, "trades": len(tr), "win%": round(100 * (tr.pts > 0).mean()),
            "avg_pts": round(tr.pts.mean(), 4), "pnl": round(tr.pnl.sum()),
            "pnl_1st_half": round(a.pnl.sum()), "pnl_2nd_half": round(b.pnl.sum()),
            "worst": round(tr.pnl.min()), "hold_h": round((tr.t1 - tr.t0).dt.total_seconds().mean() / 3600, 1)}


def spike_catcher(cost: float, mid) -> List[dict]:
    """Resting limit orders k below/above the rolling mean; filled at the limit when a candle's
    low/high touches it (no entry cost), exit at market (pay cost) when price is back near the mean."""
    sig = pd.read_csv(HIST / "sig_5m.csv", parse_dates=["time"])
    sig["exchange"] = sig.exchange.astype(str)
    idx = pd.date_range(sig.time.min().floor("5min"), sig.time.max().floor("5min"), freq="5min", tz="UTC")
    piv = lambda col, f: sig.pivot_table(index="time", columns="exchange", values=col, aggfunc=f).reindex(idx)
    close = piv("close", "last").ffill()
    low, high, vol = piv("low", "min"), piv("high", "max"), piv("volume", "sum").fillna(0)
    C, Lo, Hi = close.values, low.values, high.values
    out = []
    for w, k, h, min_vol in itertools.product((12, 36), (0.03, 0.05, 0.08), (24, 72), (0, 500)):
        m = close.rolling(w, min_periods=w).mean().shift(1).values
        trades = []
        for j, ex in enumerate(close.columns):
            t, n = w, len(close)
            while t < n - 1:
                mu = m[t, j]
                if np.isnan(mu) or not (0.08 <= mu <= 0.92) or vol.values[t, j] < max(min_vol, 1):
                    t += 1
                    continue
                side = 1 if Lo[t, j] <= mu - k else -1 if Hi[t, j] >= mu + k else 0
                if not side:
                    t += 1
                    continue
                p0 = mu - k * side  # our resting limit
                kk = t + 1
                while kk < min(n - 1, t + h) and (C[kk, j] - mu) * side < -0.005:
                    kk += 1
                move = (C[kk, j] - p0) * side - cost
                side_price = p0 if side > 0 else 1 - p0
                trades.append({"exchange": ex, "t0": close.index[t], "t1": close.index[kk], "side": side,
                               "pts": move, "pnl": STAKE / side_price * move})
                t = kk + 1
        out.append(summarize("spike", {"window_h": w / 12, "k": k, "max_hold_h": h / 12, "min_vol": min_vol},
                             pd.DataFrame(trades), mid))
    return out


def run(cost: float) -> pd.DataFrame:
    sig, vol, pm = load_grid()
    S, P = sig.values, pm.values
    mid = sig.index[len(sig) // 2]
    print(f"{sig.shape[1]} markets, {sig.index[0]:%b %d %H:%M} -> {sig.index[-1]:%b %d %H:%M} UTC, cost {cost}/side")
    out: List[dict] = []

    # --- fade: deviation from own rolling mean
    for w, k, h in itertools.product((12, 36), (0.02, 0.03, 0.05), (24, 72)):
        m = sig.rolling(w, min_periods=w).mean().shift(1).values

        def ent(j, t, m=m, k=k):
            if np.isnan(m[t, j]):
                return 0
            d = S[t, j] - m[t, j]
            return -1 if d >= k else 1 if d <= -k else 0

        def ext(j, t, side, i0, m=m):
            return (S[t, j] - m[i0 - 1, j]) * side >= -0.005  # back near the old mean

        out.append(summarize("fade", {"window_h": w / 12, "k": k, "max_hold_h": h / 12},
                             simulate(sig, vol, ent, ext, h, cost), mid))

    # --- leadlag: Polymarket moved, SIG didn't (yet)
    for L, d, h in itertools.product((6, 24), (0.02, 0.03, 0.05), (24, 144)):
        dP = (pm - pm.shift(L)).values
        dS = (sig - sig.shift(L)).values

        def ent(j, t, dP=dP, dS=dS, d=d):
            x = dP[t, j]
            if np.isnan(x) or abs(x) < d or np.isnan(dS[t, j]):
                return 0
            return int(np.sign(x)) if dS[t, j] * np.sign(x) < 0.5 * abs(x) else 0

        def ext(j, t, side, i0, dP=dP, d=d):
            caught = (S[t, j] - S[i0, j]) * side >= 0.7 * d
            pm_reversed = (P[t, j] - P[i0 - 1, j]) * side <= -d
            return caught or pm_reversed

        out.append(summarize("leadlag", {"lookback_h": L / 12, "d": d, "max_hold_h": h / 12},
                             simulate(sig, vol, ent, ext, h, cost), mid))

    # --- gaprev: SIG-PM gap vs its own usual level
    gap = sig - pm
    for w, k, h in itertools.product((72, 288), (0.02, 0.03, 0.05), (24, 144)):
        med = gap.rolling(w, min_periods=w // 2).median().shift(1).values
        G = gap.values

        def ent(j, t, med=med, k=k):
            if np.isnan(med[t, j]) or np.isnan(G[t, j]):
                return 0
            d = G[t, j] - med[t, j]
            return -1 if d >= k else 1 if d <= -k else 0

        def ext(j, t, side, i0, med=med):
            return (G[t, j] - med[i0 - 1, j]) * side >= -0.005

        out.append(summarize("gaprev", {"window_h": w / 12, "k": k, "max_hold_h": h / 12},
                             simulate(sig, vol, ent, ext, h, cost), mid))
    out += spike_catcher(cost, mid)
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=0.005, help="cost per side in price points")
    a = ap.parse_args()
    res = run(a.cost)
    res.to_csv(ROOT / "data" / f"backtest_cost{a.cost}.csv", index=False)
    pd.set_option("display.width", 200, "display.max_rows", 100)
    for s, g in res.groupby("strategy"):
        print(f"\n== {s} ==")
        print(g.drop(columns="strategy").sort_values("pnl", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
