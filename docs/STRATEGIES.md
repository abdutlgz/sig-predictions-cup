# Strategies: what was tried, how it was tested, what happened

Dates are 2026. "Fair value" is Polymarket's mid-price for the same race. P&L is in SUSQies (start: 100,000).

## Key market facts discovered
- **SIG compresses prices toward 50%.** Measured over 220+ markets on Oct 6 (SIG mid minus Polymarket):

  | Polymarket probability | SIG − Polymarket |
  |---|---|
  | < 10% | **+4.2 pts** (long shots overpriced) |
  | 10–30% | +2.8 |
  | 30–70% | +0.8 |
  | 70–90% | −1.6 |
  | > 90% | **−3.4 pts** (favorites underpriced) |

  There is no party bias (D −0.1, R +1.2 pts).
- **The compression is sticky.** The average SIG-vs-Polymarket gap went 3.0 → 3.4 pts over two days. Prices don't converge on their own.
- **The leaders were made in the opening chaos.** The top 10 show +250k to +900k from 100k. Price spikes of 5+ pts fell from **664/day on Oct 1 to 2/day by Oct 8**.
- **No linked markets in the tournament.** The platform supports collateral relief for related markets, but none are configured, so hedged pairs tie up full cash.
- **Settlement** runs automatically (cron, ≤12 retries) **after noon ET Nov 4**. Nothing turns into cash during election night unless it's sold.
- **Slow races:** Georgia (Dec 1 runoff if no one tops 50%), Alaska (~2-week ranked-choice count), Maine federal races (ranked-choice rounds), CA/WA/AZ/NV mail counts.

---

## 1. Cross-market arbitrage
**Idea:** "R wins" and "D wins" for the same race can't both be true. If the YES bids sum to more than 1, buying NO on both costs `2 − Σbid` and pays at least 1, whoever wins.

**Tooling:** [`arb.py`](../sigcup/arb.py) walks both order books level by level and executes through **atomic multi-leg orders** (both legs or neither).

**Results:**
- The first scan found 39 risk-free gaps, with ~640k of capacity against 100k of cash.
- Trades on Oct 6: Oklahoma +31 (test), Delaware +423, Oklahoma +293, CO-08 +213, Alaska +118, Texas +155 → **~+1,233 locked in**.
- The later `pairs` bot strategy added NH Governor +51, NH Senate +13 and Alaska Governor +12, and recycled 3 pairs early.

**Verdict:** real but slow money. Backtest return was ≤0.45%/day, and negative at 2× costs. Gaps mostly reopen and close within minutes. Retired for new entries on Oct 12. Low-yield hedges were unwound at or above cost ([`unwind.py`](../sigcup/unwind.py)), and the overpriced side of some hedges was sold for **+1,214 expected value**.

## 2. Fair value vs Polymarket
**Idea:** use the compression bias. Buy what SIG prices too low, hold to settlement.

**Tooling:** [`fairvalue.py`](../sigcup/fairvalue.py) maps all 237 SIG markets to Polymarket events by slug pattern (228 matched) and lists edges.

**Results:** an 8-bet basket (Oct 6, 32k) of NO on long shots in safe seats plus YES on Democrats winning the House: **+2,911 expected**. South Dakota Senate was a notable one: SIG priced "Democrat wins" at 5% with no Democratic nominee on the ballot (Polymarket: 0.2%).

## 3. Buy-and-sell on the gap ("taker")
**Idea:** the same edge as §2, but **sell when SIG catches up** so the cash can be reused.

**Backtest** ([`backtest2.py`](../sigcup/backtest2.py), Oct 5–12, decide on bar t and fill on bar t+1, costs per side):

| Entry gap | Exit | Return per day on capital in use | At 2× costs | Both halves positive |
|---|---|---|---|---|
| 3 pts | when SIG converges | 0.98% | 0.72% | ✅ |
| 5 pts | when SIG converges | 1.12% | 0.93% | ✅ |
| 8 pts | when SIG converges | 1.37% | 1.21% | ✅ |
| 3–8 pts | forced after 3 days | 0.83–1.08% | 0.28–0.60% | ❌ weak at high cost |

**Which gaps close** (and therefore recycle cash):

| Fair value of the market | Closed | Median days | Return/day |
|---|---|---|---|
| **30–70% (close races)** | **79%** | **1.2** | **1.41%** |
| 70–90% / 10–30% | 48% / 28% | 1.1 / 4.2 | 1.18% / 0.79% |
| < 10% / > 90% (long shots) | 19% / 30% | 1.6 / 3.2 | 0.92% / 0.93% |

**Live:**
- Oct 7 test (10k): +879 expected after day 1.
- Oct 8–9: first recycling. FL-22 and Georgia Governor sold at fair, ~+180 realized, 3.3k freed.
- Budget raised 20k → 40k on Oct 12.

**Improvements (Oct 12):**
- Rank by expected return per day (close races weighted 1.5×), with a 3-pt entry for close races.
- Skip trades where **Kalshi disagrees with Polymarket by more than 3 pts**. Across the 26 races Kalshi lists, the median disagreement is 1 pt and none exceeds 3.

## 4. Passive market making
**Idea:** rest bids on both sides at fair ± 2 pts in close races and earn the spread.

**Result:** fills only came on the side SIG mispriced, so the bot kept accumulating the cheap side. In practice it was the value strategy again. SIG's own market maker keeps spreads at 0.5–1 pt, which leaves no room. **Dropped.**

## 5. Spike catcher
**Idea:** resting limit orders ~5 pts from the 1-hour average. When someone dumps a market, buy from them and sell when the price reverts. Resting orders don't tie up cash until they fill.

| Window | Result |
|---|---|
| Oct 1–4 (opening chaos) | **+100k–130k** per 1k-per-trade in backtest, 85–97% win rate. Probably how the leaders built their leads |
| Oct 5–12 | second half negative for every parameter set; **0 live fills in 4 days** |

**Dropped Oct 12.** It would also be dangerous on election night, when real result-driven moves look exactly like spikes.

## 6. Rejected: lead-lag, gap reversion, fade
| Idea | Result (backtests at realistic costs) |
|---|---|
| Lead-lag: Polymarket moves ≥2–3 pts, SIG hasn't, follow | negative (−0.6 to −9%/day); too few real moves |
| Gap reversion: SIG-vs-Polymarket gap vs its usual level | barely positive at 0.5-pt costs, negative at 1 pt |
| Fade: SIG jumps from its own average, bet it reverts | profitable only in Oct 1–4; negative since |

## 7. Aggressive "wave" bet
**Idea:** close races move together with the national mood. A small budget on one party across ~8 close races, each bought at or below fair value, gives a fat right tail.

**Placed Oct 8:** a Republican wave, 3k across CO-03, NY-17, Vermont Governor, Texas Governor, ME-02, FL-13, AL-02 and Maine Senate. +7.1% expected; 1.7× if that party has a good night, ~0 if not. Held to election night; election mode rescues value from any wave bet whose race is clearly lost.

## 7b. Big bet: Democrats keep the Senate (Oct 15)
After the loop bug, the user chose maximum aggression. SIG overprices Republicans in chamber control: House R 0.16 vs fair 0.10, Senate R 0.43 vs 0.37. So the cheap, positive-EV high-variance side was **D Senate**.
- Sold 22 safe-seat positions at the smallest haircuts (63.3k cash, −5.7k vs fair) with [`bigbet.py`](../sigcup/bigbet.py).
- Bought **111,225 YES at avg 0.579** (fair 0.635).
- Outcomes: ~63% → +46.8k on the bet; ~37% → −64.4k.

The all-in "Republicans keep the House" jackpot (~6× at ~10%, −41% EV) was rejected. Even its best case (~rank 5) can't reach the top-3 prize line of +810k.

## 7c. Safe-seat baskets (Oct 22, Oct 31)
Close to the election, SIG priced near-certain races at 0.85–0.90 while Polymarket had them at 0.97–0.99, about three times the Oct 6 gap. Two baskets bought these: 20k in 8 races on Oct 22 (+1,861 EV) and 19,969 in 5 races on Oct 31 (+2,311 EV). Each buy was capped at fair − 5 pts. They were picked from early-closing states so election mode could cash them in to fund trades on the night. All 13 races went the expected way.

**Missed chance:** on Nov 2, one player bid 0.835 for ~46k "Democrats keep the Senate" shares while Polymarket and Kalshi had 0.735. Selling everything would have added +4.4k EV and removed all Senate risk. The bid was gone by the next check. Lesson: a mispricing this large on the account's biggest position needs an alert, not a daily check.

## 8. Election-night mode
See [ARCHITECTURE.md](ARCHITECTURE.md#election-night-mode). It was rehearsed against live SIG order books with injected fake results (a swing, a one-loop flicker, a wave race going against us, a called race), and each rehearsal produced a fix:
- **Rehearsal 1:** one strategy's market ownership blocked trades → ignored on election night
- **Rehearsal 2:** wave bets were being sold at fair → now held until their race is decided
- **Rehearsal 3:** decided races were cashed in for nothing → now only when a bigger edge needs the cash
- **Rehearsal 4:** cash was spent on pre-results mispricings → now only markets that moved ≥5 pts since the switch
- **Rehearsal 5:** cash-ins fired on unconfirmed moves → now they wait for confirmation; the 12k cap applies per race
- **Rehearsal 6 (Oct 30):** a one-loop flicker could "confirm" an older gap → only loops after the move count

**How it went (Nov 3, 5:45pm ET → Nov 4):** the timer switched modes on schedule. The bot made **490 trades in 83 markets** with 0 loop errors, and the busiest race had 25 trades (Iowa Senate sales). By a rough estimate (64 of 83 markets valued at Polymarket's latest prices) trading added **≈ +9k** compared with holding the 5:45pm positions.
- **Best:** Iowa Senate. It bought 16,500 Democratic YES at ~0.53 right after the switch and sold them through the night at an average of 0.668, for +2.2k. Many cheap decided-race buys added more.
- **Senate bet:** once the race was effectively decided (~9:30pm ET), the cash-in rule sold all 43,635 shares at an average of 0.931 to fund bigger gaps. That locked in +15.4k vs cost, ~3k below holding to settlement.
- **Worst:** WI-01, −3.2k. A fair-value bet on the Republican lost.
- **Whipsaw:** VA-05, −1.75k. Polymarket swung briefly toward the Democrat, the bot sold, and the Republican won. This is the main weakness of treating Polymarket's live price as truth.

## Incident: the morning check stopped the election bot (Nov 4)
The 8am health check had a runaway rule: create `data/STOP` if the bot traded more than 500 times in 24 hours. The threshold was set for normal days, and election night passed it, so the bot was stopped at 8:07am ET, four hours before the close. The cost was small, because cash was ~0 and no decided winner was priced at or above the 0.93 cash-in level. **Fix:** in election mode the rule now uses 3,000 trades in 24h (50 per market is unchanged). Lesson: every automated safety rule needs its thresholds reviewed for the election-night regime, not just the bot's own.

## Incident: buy/sell loop (Oct 12–13, −6.7k)
- **What happened:** the `pairs` strategy built its list of two-party races once, at startup.
  - Once the new speed ranking (Oct 12) pushed `taker` into close races (Kansas Senate, FL-09, Michigan Senate), `taker` bought one side.
  - `pairs` saw a "half-filled pair" and **sold it to repair the imbalance**, then `taker` bought it again.
- **Cost:** 1,342 round trips overnight × the ~0.7-pt spread = **−6,734** (Kansas −4,012, FL-09 −2,659, Michigan −63). Found at the next check-in.
- **Fix:**
  1. `pairs` re-checks market ownership every loop and never touches a race another strategy trades.
  2. **Anti-churn guard:** a market traded more than 6× in 6 hours gets no new buys (12× on election night).
  3. **Global brake:** all new buys pause if the bot trades more than 60×/hour (300 on election night).

  The bug was reproduced offline and the fix verified before restarting.

## Lessons
0. **Any two automated rules that can undo each other will loop.** Rate-limit trades per market, and check that trading counts look sane at every review.
1. Measure **return per day on capital**, not total P&L. Cash, not ideas, was the bottleneck.
2. Test the **current** regime separately; the opening-chaos data made several dead strategies look brilliant.
3. Value the account two ways: **SIG mark-to-market** (what the leaderboard shows) and **fair value** (what settlement pays). They differed by 5–8k most of the time.
4. Rehearse with fake events against live data before trusting automation with the biggest night.
5. **Monitoring is automation too.** The health check, not the bot, ended election night early.
6. On election night the biggest gains came from cashing in decided races and recycling the cash into others that SIG still priced as uncertain.
