# SIG Predictions Cup: Plan v2 (Oct 12 → Nov 4)

> **Oct 15 update: aggressive mode.** After the −6.7k loop bug (rank ~1,360/1,626) the user chose maximum aggression. Top 3 (+810k) is out of reach (the best single bet pays ~6×).
> - Sold 22 safe-seat positions (63.3k, −5.7k vs fair) and bet **64.4k on "Democrats keep the U.S. Senate"** (111,225 YES sh, avg 0.579, fair 0.635, ~+10% EV): about +46.8k if it wins (rank ~40), −64.4k if not.
> - Election mode raised to maximum: 5k trades (2× for big gaps), 40k per race, cash-ins at ≥ 0.93, and a 10-pt minimum edge in slow-count / runoff races.

_Updated 2026-10-12. Replaces the Oct 6 plan. Tools: see README.md._

## Where we stand
| | Value |
|---|---|
| Expected profit at settlement (Polymarket fair value) | **+8,400** |
| Leaderboard (SIG prices) | ~+400, rank ~419 / 1,466 |
| Top 10% / top 5% lines (Oct 12) | +5,841 / **+17,066** |
| Cash | ~2k (reserve); everything else invested |
| Server bot | buy-and-sell ("taker", 40k budget) + unwinding existing pairs; election switch scheduled |

**Goal:** finish in the top 5%, which needs roughly **+9k more**. The target is **+3–4k from better recycling before Nov 3** and **+5k or more on election night**.

## What the research found (Oct 12)
1. **Only one pre-election strategy is robust:** buy SIG prices that are 3+ pts away from Polymarket, then sell when SIG catches up. It earns ~1%/day on capital in use, holds in both halves of the test window, and survives at 2× costs. Spike catcher, lead-lag, gap-reversion and new pairs don't hold up in today's market.
2. **Close races recycle; long shots don't.** In the Oct 5–12 backtest:
   | Fair value | Gaps that closed | Median days | Return per day |
   |---|---|---|---|
   | 30–70% (close races) | **79%** | **1.2** | **1.41%** |
   | 70–90% / 10–30% | 28–48% | 1.1–4.2 | 0.8–1.2% |
   | <10% / >90% (long shots) | 19–30% | 1.6–3.2 | ~0.9% |

   SIG's overpricing of long shots is sticky, so those positions mostly pay out only at settlement. The bot currently ranks opportunities by gap size alone, so most of its capital sits in the slow bucket.
3. **Fair values are reliable.** Where Kalshi, Polymarket and SIG all list a race (28 so far), Kalshi and Polymarket differ by a median of 1 pt and never more than 3. 8 current edges are confirmed by both. Kalshi is also a usable backup feed (`sigcup/kalshi.py`).
4. **Settlement:** markets settle automatically, by a cron job with up to 12 retries, after **noon ET Nov 4**. Nothing turns into cash during election night unless we sell it.
5. **Slow or delayed races** won't reach 0/1 on election night:
   - **Georgia Senate and Governor:** a runoff on **Dec 1** if no one tops 50%.
   - **Alaska Senate and Governor:** the count takes about 2 weeks; certification is around Nov 30.
   - **Maine federal races (Senate, ME-02):** ranked-choice rounds add days if no one tops 50%.
   - **California House and Governor; also WA, AZ, NV:** mail counts can take a week or more (2022 House control was called 8+ days later).
   - Final standings come after **all** markets resolve, so these still count. They just tie up capital on the night.

## Plan
### Now → Oct 29: grow expected value through faster recycling
- [x] **A. Rank buy-and-sell by expected return per day, not gap size.** (live Oct 12: close races weighted 1.5x, leaning 1.2x, long shots 1.0x; entry threshold 3 pts for 30–70% races) Score each gap as gap ÷ expected days to close (using the bucket table above). Allow a slightly lower entry threshold (3 pts) for 30–70% races, since they recycle within about a day. 
- [x] **B. Confirm edges with Kalshi when it lists the race.** (live Oct 12: covers 26 races, refreshed every 5 min, off in election mode) Skip the trade if Polymarket and Kalshi disagree by more than 3 pts.
- [x] Hourly CO-08 hedge unwind attempt, only at or above cost (running).
- [ ] Reviews on **Oct 15, 19, 22**: bot P&L per strategy, recycling rate, rank, and the top-5% line.

### Oct 28: rehearsal (automatic, 10:00am ET)
- [ ] Review `data/rehearsal_2026-10-28.log` and the Polymarket mapping refresh.
- [ ] Extend the rehearsal scenario: a Georgia runoff (price stays ~50%), an Alaska slow count, and a Polymarket outage (Kalshi fallback).

### Oct 30 – Nov 2: build election-night cash
- [ ] **C. Stop new long-shot entries** (fair <10% or >90%). They won't recycle before the night.
- [ ] Raise the daily bot's cash reserve step by step to ~15–20k of dry powder by Nov 3.
- [ ] **Wave decision:** enlarge the wave bet, hold it, or trim it, based on polls and rank.
- [ ] Election mode: require a bigger edge in slow-count / runoff races (GA, AK, ME, CA, WA, AZ, NV).
- [ ] Final server check: disk, memory, timers, `data/overrides.json` empty.

### Nov 3–4: election night (automatic switch at 5:45pm ET)
- The bot trades only markets whose Polymarket price moved ≥5 pts since the switch, confirmed over 2 loops. Trades scale with the edge, up to 12k per race. Decided winners are cashed in at ≥0.95 only to fund bigger edges. Wave bets ride until their race is decided.
- You watch the log and enter manual race calls if Polymarket lags (`sigcup.call`). Emergency stop: `touch data/STOP`.
- Polls close (ET): 6pm KY/IN (east), 7pm GA/VA/VT/SC, 7:30 NC/OH/WV, 8pm most of the East, 9pm TX/MI/WI/AZ/CO…, 10pm MT/NV, 11pm CA/OR/WA/ID, 1am AK.
- Trading stops at **12:00pm ET Nov 4**; the bot stops at 12:01.

## Researched and rejected
| Idea | Why not |
|---|---|
| Spike catcher | The edge came from Oct 1–3 chaos; 2 spikes/day now; would buy real result moves on election night |
| Follow Polymarket's moves | Negative after costs |
| Gap reversion (gap vs its usual level) | Loses at realistic costs |
| New free-money pairs | ≤0.45%/day, negative at 2× costs |
| Market making around SIG's price | SIG's own market-maker keeps spreads at 0.5–1 pt; no room |
| Collateral relief on hedged sets | The tournament has no linked markets, so no cash is freed |
| Paid real-time results (AP API) | Paid; Polymarket reacts within seconds anyway, plus manual calls |

## Risks
- Polymarket being wrong on a race: mitigated by B (Kalshi confirmation) and diversification (≤2–3k per market daily, ≤12k per race on election night).
- Server or API outage on election night: auto-restart, plus Kalshi as a fallback idea, plus manual calls from a phone over SSH.
- Long-shot positions locked until settlement: accepted, since they pay at settlement. Plan C stops adding to them from Oct 30.
- The top-5% line moving up: re-check it at every review.
