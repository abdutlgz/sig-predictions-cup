"""Morning health check (read-only), run on the server:  python3 -m sigcup.morning

Prints a verdict line, health, churn check, P&L, Senate bet and the change since the last check,
and saves today's numbers to data/morning_check_last.json.  The single write action: if a buy/sell
loop is clearly running (>50 trades in one market or >500 total in 24h), it creates data/STOP.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from pathlib import Path

import pandas as pd

from .client import ROOT, Client
from .pmfeed import build_map, fair_values

DATA = ROOT / "data"
SENATE = "Will the Democratic Party win the U.S. Senate?"


def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    issues = []
    election = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) >= dt.datetime(2026, 11, 3, 22, 45)
    svc = "sigbot-election" if election else "sigbot"
    log = DATA / ("bot_election.log" if election else "bot_multi.log")
    active = sh(f"systemctl is-active {svc}")
    if active != "active":
        issues.append(f"service {svc} is {active}")
    last = sh(f"grep '^\\[' {log} | tail -1")
    text = log.read_text(errors="ignore") if log.exists() else ""
    run = text[text.rfind("Bot LIVE"):] if "Bot LIVE" in text else text  # current run only
    errors = run.count("loop error")
    churn_lines = run.count("CHURN GUARD")
    timers = int(sh("systemctl list-timers --all --no-pager | grep -c sigbot") or 0)

    t = pd.read_csv(DATA / "bot_trades.csv")
    t["ts"] = pd.to_datetime(t.ts)
    r = t[t.ts >= pd.Timestamp.now() - pd.Timedelta(hours=24)]
    per = r.groupby("title").size().sort_values(ascending=False)
    if len(per) and (per.iloc[0] > 50 or len(r) > 500):
        (DATA / "STOP").touch()
        issues.append(f"RUNAWAY LOOP: {per.iloc[0]} trades in one market / {len(r)} total -> bot STOPPED")
    elif (len(per) and per.iloc[0] > 8) or len(r) > 150 or churn_lines:
        issues.append(f"unusual trading: busiest market {per.iloc[0] if len(per) else 0}, total {len(r)}, "
                      f"churn-guard lines {churn_lines}")

    c = Client()
    m = build_map(c)
    fv = fair_values(m)
    e = next(x for x, v in m.items() if v["title"] == SENATE)
    e_r = next(x for x, v in m.items() if v["title"] == SENATE.replace("Democratic", "Republican"))
    pos = {pp["exchangeId"]: pp["quantity"] for pp in c.positions()["positions"]}
    shares = max(pos.get(e, 0), 0) + max(-pos.get(e_r, 0), 0)  # YES on D + NO on R = same bet
    q = c.prices([e])[e]
    p = fv.get(e)
    rep = sh("python3 -m sigcup.botreport")
    std = sh("python3 -m sigcup.standing")
    acc = re.search(r"cash ([\d,]+) \| account at SIG prices ([\d,]+) \| at Polymarket fair ([\d,]+).*P&L vs 100k: ([+-][\d,]+)", rep)
    rank = re.search(r"rank (\d+) of (\d+)", std)
    t10 = re.search(r"top\s+10% = rank\s+\d+: pnl\s+([\d,-]+)", std)
    t5 = re.search(r"top\s+5% = rank\s+\d+: pnl\s+([\d,-]+)", std)
    num = lambda s: float(s.replace(",", ""))
    today = {"date": str(dt.date.today()), "expected_pnl_fair": num(acc[4]), "account_sig": num(acc[2]),
             "rank": int(rank[1]), "total": int(rank[2]), "top10": num(t10[1]), "top5": num(t5[1]),
             "senate_d_prob": p, "cash": num(acc[1])}
    fp = DATA / "morning_check_last.json"
    change = ""
    if fp.exists():
        y = json.loads(fp.read_text())
        d = today["expected_pnl_fair"] - y["expected_pnl_fair"]
        s = (p - y["senate_d_prob"]) * shares
        change = f"since {y['date']}: expected P&L {d:+,.0f} (Senate odds explain {s:+,.0f}, other {d - s:+,.0f})"
        if d - s < -1500:
            issues.append(f"expected P&L fell {d - s:,.0f} beyond the Senate-odds move")
    fp.write_text(json.dumps(today))

    print("VERDICT:", "ATTENTION: " + "; ".join(issues) if issues else "All good")
    print(f"health: {svc} {active} | last loop {last[:20]} | errors this run {errors} | timers {timers}")
    print(f"trading 24h: {len(r)} trades, busiest market {per.iloc[0] if len(per) else 0}"
          + (f" ({per.index[0][9:60]})" if len(per) else ""))
    print(f"account: SIG {today['account_sig']:,.0f} | expected P&L at fair {today['expected_pnl_fair']:+,.0f}"
          f" | cash {today['cash']:,.0f} | rank {today['rank']} of {today['total']}"
          f" | top10% {today['top10']:+,.0f} top5% {today['top5']:+,.0f}")
    print(f"senate bet: {shares:,.0f} shares | Polymarket {p:.3f} | SIG {q['bestBid']}/{q['bestAsk']} (paid 0.579)")
    if change:
        print(change)


if __name__ == "__main__":
    main()
