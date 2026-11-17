# SIG Predictions Cup 2026: trading bot & strategy research

An automated trading system for the [Susquehanna (SIG) Predictions Cup](https://sig.com/predictions/), a simulated prediction-market competition on the 2026 U.S. midterm elections (Oct 1 to Nov 4, 2026). Each student gets 100,000 SUSQies of play money to trade ~237 binary markets ("Will the Democratic Party win the Georgia Senate?"). The final score is the balance after all markets resolve. Bots are allowed through the platform's API.

The project covers the full loop: **market research → backtesting → a live multi-strategy bot on a cloud server → an automated election-night mode.**

## Results (as of Nov 16, 2026; one market still to settle)
| | |
|---|---|
| Expected final result | **150,028 (+50,028, +50%)**, about **rank 108 of 1,969 (top 6%)** |
| Current leaderboard | rank 111 of 1,969. 19 of 20 held markets have settled; cash is 138,758 |
| Still open | U.S. House: 11,270 shares on Democrats, already resolved that way on Polymarket and waiting for SIG's settlement |
| Goals | Top 10% ✅ (line: rank 196, +27.8k). Top 5% ❌, short by ~3.5k (rank 98, +53.6k) |
| Rank over time | ~1,360 after the Oct 13 loop bug → ~200 before election night → 160 at the close of trading → 111 as markets settled |
| Biggest single trade | "Democrats keep the Senate": 111k shares bought at 0.579 (Oct 15). Trimmed at 0.674 and 0.740, and the rest was cashed in at 0.931 on election night |
| Election night | 490 automated trades in 83 markets, ≈ +9k vs holding (rough estimate) |
| Infrastructure | Google Cloud e2-micro (free tier), systemd service + scheduled election-night switch |

Timeline and lessons: [docs/STRATEGIES.md](docs/STRATEGIES.md). The Oct 13 buy/sell-loop bug (−6.7k) is described [here](docs/STRATEGIES.md#incident-buysell-loop-oct-1213-67k).

## How it works
```
 Polymarket CLOB  ──(fair value, ~1 s for 228 markets)──┐
 Kalshi API       ──(cross-check, every 5 min)──────────┤
                                                        ▼
                                   sigcup.bot (Python, GCP e2-micro, systemd)
                                   loop: fetch in parallel → exits → strategies
                                                        │
                                                        ▼
                         SIG "Super Market" API (orders, positions, order books)
```
1. **Fair value:** Polymarket's mid-price for the same race (deep, real-money market), cross-checked against Kalshi.
2. **Edge:** SIG's play-money market systematically compresses prices toward 50%. Long shots run ~4 pts too high and favorites ~3 pts too low.
3. **Trade:** buy whatever SIG prices below fair value, sell when SIG catches up, reuse the cash. Ranked by expected return per day.
4. **Election night:** the bot switches itself at 5:45pm ET on Nov 3 to a fast mode. It runs 2-second loops, trades only markets whose Polymarket price moved on real results, sizes trades by edge, and cashes in decided races to fund new trades.

## Strategies tried
| Strategy | Verdict | Details |
|---|---|---|
| Cross-market arbitrage (NO on both parties when the pair costs < 1) | ✅ +1.3k risk-free, but low return per day | [docs/STRATEGIES.md](docs/STRATEGIES.md#1-cross-market-arbitrage) |
| Value: buy SIG mispricings vs Polymarket, hold | ✅ +2.9k expected from an 8-bet basket | [§2](docs/STRATEGIES.md#2-fair-value-vs-polymarket) |
| **Buy-and-sell on the gap to Polymarket ("taker")** | ✅ **main strategy**, ~1–1.4%/day on capital in use | [§3](docs/STRATEGIES.md#3-buy-and-sell-on-the-gap-taker) |
| Passive market making ("maker") | ❌ kept buying the cheap side; just a copy of the value strategy | [§4](docs/STRATEGIES.md#4-passive-market-making) |
| Spike catcher (resting orders far from the price) | ❌ huge in the Oct 1–3 chaos, ~0 since | [§5](docs/STRATEGIES.md#5-spike-catcher) |
| Lead-lag (follow Polymarket's moves) | ❌ negative after costs | [§6](docs/STRATEGIES.md#6-rejected-lead-lag-gap-reversion-fade) |
| "Wave" bet (one party across 8 close races) | 🎲 aggressive sleeve, 3k, +7% EV, 1.7× upside | [§7](docs/STRATEGIES.md#7-aggressive-wave-bet) |
| Election-night mode | ✅ ran automatically Nov 3–4: 490 trades, ≈ +9k vs holding | [§8](docs/STRATEGIES.md#8-election-night-mode) |

## Repository layout
| Path | What it is |
|---|---|
| [`sigcup/client.py`](sigcup/client.py) | SIG API client: auth, retries, idempotent orders, multi-leg orders |
| [`sigcup/pmfeed.py`](sigcup/pmfeed.py), [`kalshi.py`](sigcup/kalshi.py) | Fair-value feeds (Polymarket CLOB batch midpoints; Kalshi cross-check) |
| [`sigcup/bot.py`](sigcup/bot.py) | The bot: strategies `taker`, `pairs`, `maker`, `spike`; presets `test`, `daily`, `multi`, `election` |
| [`sigcup/arb.py`](sigcup/arb.py), [`unwind.py`](sigcup/unwind.py) | Arbitrage scanner; hedge unwinder (sells only at or above cost) |
| [`sigcup/fairvalue.py`](sigcup/fairvalue.py), [`wave.py`](sigcup/wave.py) | Mispricing sheet vs Polymarket; aggressive wave basket |
| [`sigcup/history.py`](sigcup/history.py), [`backtest.py`](sigcup/backtest.py), [`backtest2.py`](sigcup/backtest2.py) | 5-minute price history + two rounds of backtests |
| [`sigcup/rehearse.py`](sigcup/rehearse.py), [`races.py`](sigcup/races.py), [`call.py`](sigcup/call.py) | Election-night rehearsal with fake results, race board by poll-closing time, manual race calls |
| [`sigcup/botreport.py`](sigcup/botreport.py), [`standing.py`](sigcup/standing.py) | P&L at SIG vs fair prices; leaderboard percentiles |
| [`deploy/`](deploy/) | systemd service, election-night switch/stop/rehearsal timers, install scripts |
| [`PLAN.md`](PLAN.md) | The plan through Nov 4 and how it played out |

## Quick start
```bash
pip install -r requirements.txt
cp .env.example .env           # add SIG_API_KEY (My Profile → API Keys on the platform)
python3 -m sigcup.fairvalue    # where SIG disagrees with Polymarket
python3 -m sigcup.bot --mode multi --once       # one dry-run loop (no orders)
python3 -m sigcup.botreport    # account + bot P&L at SIG and fair prices
```
Every trading command is a **dry run unless `--live`** is passed. `touch data/STOP` makes a running bot cancel its orders and exit.

## Documentation
- [docs/STRATEGIES.md](docs/STRATEGIES.md): every strategy tried, with backtests and live results
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the bot works, safety rules, election-night mode
- [docs/APIS.md](docs/APIS.md): how SIG, Polymarket and Kalshi are connected
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): server setup, systemd units, timers, operations

---
*Play-money competition; nothing here is financial advice. The SIG API reference is available to participants on the platform (`/api/v1/docs`) and isn't redistributed here.*
