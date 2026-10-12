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

## 8. Election-night mode (scheduled)
See [ARCHITECTURE.md](ARCHITECTURE.md#election-night-mode). It was rehearsed against live SIG order books with injected fake results (a swing, a one-loop flicker, a wave race going against us, a called race), and each rehearsal produced a fix:
- **Rehearsal 1:** one strategy's market ownership blocked trades → ignored on election night
- **Rehearsal 2:** wave bets were being sold at fair → now held until their race is decided
- **Rehearsal 3:** decided races were cashed in for nothing → now only when a bigger edge needs the cash
- **Rehearsal 4:** cash was spent on pre-results mispricings → now only markets that moved ≥5 pts since the switch
- **Rehearsal 5:** cash-ins fired on unconfirmed moves → now they wait for confirmation; the 12k cap applies per race

## Lessons
1. Measure **return per day on capital**, not total P&L. Cash, not ideas, was the bottleneck.
2. Test the **current** regime separately; the opening-chaos data made several dead strategies look brilliant.
3. Value the account two ways: **SIG mark-to-market** (what the leaderboard shows) and **fair value** (what settlement pays). They differed by 5–8k most of the time.
4. Rehearse with fake events against live data before trusting automation with the biggest night.
