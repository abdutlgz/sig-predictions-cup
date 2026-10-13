# Architecture

## The bot loop ([`sigcup/bot.py`](../sigcup/bot.py))
Every `interval` seconds (20 s daily, 2 s on election night):

1. **Fetch in parallel** (thread pool): Polymarket fair values, SIG prices for all markets, positions, cash. That takes ~1 s.
2. **Overrides:** manual race calls from `data/overrides.json` replace fair values.
3. **Exits:** sell any held position once SIG pays at least fair − 0.5 pt (it has "caught up"), freeing cash.
4. **Strategies,** each with its own budget:
   - `taker`: buy gaps ≥ 3.5 pts (3 pts in close races), ranked by expected return per day, skipped if Kalshi disagrees
   - `pairs`: open or unwind NO/NO sets in two-party races (budget 0 now = unwind only)
   - `maker`, `spike`: kept for reference, disabled
5. **Housekeeping:** every ~5 minutes, cancel open orders the bot isn't tracking (e.g. after a `502 ORDER_STATUS_UNKNOWN`).

**Market ownership:** each market belongs to one strategy (`data/bot_owners.json`), so results per strategy stay separable. Positions that existed before the bot started (`data/manual_positions.json`) are never touched in daily modes.

## Presets
| Preset | Use | Key settings |
|---|---|---|
| `test` | first live sandbox | 7.5k cap, taker + maker |
| `multi` | **daily bot on the server** | taker 40k + pairs (unwind only), rank by speed, Kalshi check, 2k cash reserve |
| `election` | Nov 3, 5:45pm ET → Nov 4 noon | 2 s loop, manages all positions, see below |

## Safety rules
- **Dry run by default;** `--live` is required to send orders.
- **Kill switch:** `touch data/STOP` makes the bot cancel all its orders and exit (systemd won't restart it after a clean exit).
- **Idempotent orders:** every order carries a unique `idempotencyKey`, so retries after timeouts or `502` can't double-place.
- **Nothing rests by accident:** taker buys and exits are limit orders at a computed worst price; any unfilled remainder is cancelled immediately.
- **Atomic pairs:** both legs of a hedge go in one multi-leg order (all or nothing).
- **Price floors:** the hedge unwinder ([`unwind.py`](../sigcup/unwind.py)) sells only if total proceeds ≥ cost.
- **Caps:** per-market (2k daily / 12k per race on election night), per-strategy budgets, cash reserve.
- **Anti-churn:** max 6 trades per market per 6 h and a 60-trades/hour global brake (12 / 300 on election night) block new buys. This was added after a buy/sell loop between two strategies cost 6.7k (see STRATEGIES.md).
- **Connection loss:** after 2 failed loops in a row, resting quotes are pulled until data is back.

## Election-night mode
Switched on automatically by a systemd timer at **5:45pm ET on Nov 3**, just before the first polls close (6pm ET in parts of KY/IN).

| Feature | Why |
|---|---|
| **Trades only markets whose Polymarket price moved ≥ 5 pts since the switch** (baseline saved to `data/night_baseline.json`) | Keeps cash for result-driven moves instead of ordinary pre-election mispricings |
| **2-loop confirmation** | A one-tick flicker in a thin Polymarket book can't trigger trades or cash-ins |
| **Edge-scaled size** (3k × gap/4 pts, up to 2×), **12k cap per race** (YES-D and NO-R count as one bet) | Bigger dislocations get more money without doubling up on one race |
| **Manages every position** (earlier ones, pairs, taker) | Sells anything SIG now values at or above fair |
| **Wave bets ride** until their race is decided; clearly lost ones (fair < 15%) are sold if SIG still pays more | Keeps the upside we bought, rescues value from losers |
| **Cash-in:** decided winners (fair ≥ 0.97) are sold at ≥ 0.95, but only when a confirmed opportunity at least 3 pts bigger needs the cash | Nothing settles until noon Nov 4, so selling is the only way to recycle during the night |
| **Manual calls:** `python3 -m sigcup.call "Georgia Senate" democratic` | Overrides Polymarket if it lags a race call |

**Rehearsal** ([`rehearse.py`](../sigcup/rehearse.py)) runs this mode in dry run against live SIG order books while injecting fake Polymarket moves. Loops take 0.8–2 s on the e2-micro server.

## Data files (`data/`, git-ignored)
| File | Contents |
|---|---|
| `bot_multi.log`, `bot_election.log` | live logs |
| `bot_trades.csv` | every fill the bot placed |
| `pm_map.json` | SIG exchange → Polymarket token mapping |
| `manual_positions.json`, `bot_owners.json`, `bot_start.json`, `manual_sales.json` | bot state and P&L bookkeeping |
| `hist/` | 5-minute SIG and Polymarket price history for backtests |
| `overrides.json`, `night_baseline.json`, `STOP` | election-night controls |
