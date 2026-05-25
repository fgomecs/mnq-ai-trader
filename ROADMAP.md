# MNQ AI Trader — Roadmap

## Completed

### V1.0 — Foundation
- IBKR TWS connection via ib_insync
- Live MNQ L1 data (bid/ask/last/volume)
- Claude Opus decision loop (entry) + Sonnet (position management)
- Bracket order execution (market entry + stop + limit target)
- Basic daily P&L tracking and $500 loss cap
- HTML dashboard (dashboard.html)

### V2.0 — Intelligence Layer
- L2 DOM streaming (20 levels each side)
- ICT methodology prompt: FVGs, Order Blocks, CHoCH, Inducement, Liquidity Pools
- Opening Range detection and OR bias (LONG_PREFERRED / SHORT_PREFERRED)
- Pre-filter signal scoring (pure Python, gates Claude calls)
- Skip-when-unchanged cache (A.1) — ~60–70% Opus call reduction
- Session memory: `load_recent_memory(days=5)` seeds each day's context
- Thesis probability gate: only enter when Claude's stated probability ≥ threshold
- OFI (Order Flow Imbalance) scoring in pre-filter
- IBKR news headlines injected into entry prompt

### V2.5 — Risk & Execution Hardening
- Cancel-vs-fill race fix in `_close_position` (pre-flight broker check, post-cancel recheck)
- `stop_price=0` guard in `parse_decision` (P1.7) — demotes BUY/SELL → HOLD on bad parse
- P&L sanity bound — clamps unrealised P&L to prevent phantom large values
- Broker reconciliation in protection loop — detects orphaned positions
- `entry_timestamp` ownership moved to Executor (P1.3), not a module global
- `reset_session_state()` wipes all claude_brain module globals at EOD (P2.8)

### V3.0 — Bidirectional OR Bias
- OR direction is a starting bias, not a hard gate — both sides always eligible
- Bias decays to NEUTRAL after 90 min, or on 80+ pt adverse move, or on full MTF disagreement
- Counter-trend signal threshold (5+) vs bias-preferred (3+)
- Volume profile: tick-level volume-at-price histogram, POC detection
- `get_watchlist()` returns LONG_PREFERRED / SHORT_PREFERRED / NEUTRAL / NO_TRADE

### V3.1 — DOM Intelligence
- 60-second rolling DOM history for iceberg/spoof/sweep/cluster detection
- Large DOM absorption events scored in pre-filter (+2 signals)
- Tape analysis: large print detection (≥ LARGE_PRINT_THRESHOLD contracts), AGGRESSIVE_BUYING / AGGRESSIVE_SELLING flags
- `_get_tape_analysis()` injected into snapshot and entry prompt

### V4.0 — Multi-Timeframe & Structure
- MTF alignment: 1m + 5m + 15m trend agreement → BULLISH_ALIGNED / BEARISH_ALIGNED / MIXED
- CHoCH and inducement detection in ICT cache
- Market structure field in snapshot and prompt
- Cumulative delta and delta-last-bar in snapshot
- `update_watchlist()` called every 5 min on Sonnet (cost-efficient)
- `analyze_premarket()` Opus call once at 8:30 ET — seeds daily bias

### V4.1 — Dashboard & Journaling
- Price chart with VWAP curve, trade markers, pan/zoom (dashboard.html)
- EOD journal flow: `learning_session.py` → `journal_exporter.py` → `journal_data.json` → `journal.html`
- Per-strategy stats, by-hour breakdown, OFI performance, thesis probability buckets
- Equity curve in journal
- Mobile dashboard (mobile.html)
- Ablation report before journal export

### V4.1.1 — Fixes & Config
- OR window corrected to first 15 minutes of RTH (9:30–9:45 ET); SESSION_OR_FORMING_END=945
- exchange-calendars XNYS for CME holiday / early-close detection
- Pre-market sleep loop (`_wait_for_market_hours()`) with 30-min poll; writes `botSleeping=true` to dashboard
- Dashboard reasoning block carries ISO timestamp (P1.6) — frontend greys out stale reasoning >5 min
- EOD auto-commit removed from git automation; version bump only

### V4.2 — ICT Levels Expansion
- Daily demand/supply zones from daily bar reversals (`_find_daily_zones()`)
- Candlestick pattern detection on 5m and 1m bars: engulfing, hammer, shooting star, morning/evening star, inside bar breakout (`_detect_candle_patterns()`)
- DOJI MTF override (FEATURE_DOJI_MTF_OVERRIDE): on DOJI OR days, allows trades when MTF is BULLISH/BEARISH_ALIGNED with COUNTER_THRESH (5+) signals
- Candle patterns, tape bias, daily zones surfaced in dashboard and mobile dashboard

### V4.3 — Entries & Trail Milestones
- Limit order entry mode (ENTRY_MODE=LIMIT): tries limit at Claude's entry_price, slippage guard (LIMIT_ORDER_MAX_SLIPPAGE ticks), timeout fallback to market
- Configurable trail milestones: TRAIL_PROFIT_1_TICKS=120 → lock 30t above entry; TRAIL_PROFIT_2_TICKS=180 → lock 60t above entry (replaces hardcoded values, D.2)
- Pre-market high/low tracking (4am–9am ET globex range), 4 pre-filter signals, surfaced in dashboard
- Paper trading daily loss limit raised to $2,000

---

## Planned

### V4.4 — Session Type Classification (highest priority post-Tuesday)

**Daily Session Type Classifier**
Before trading begins, classify the day into one of four session types.
This single classification changes all downstream thresholds.

Session types:
TREND DAY — strong directional move expected. Trade ORB pullbacks
aggressively. Normal thresholds. Best bot performance expected.

RANGE DAY — choppy, mean-reverting. ORB pullbacks will fail.
Raise all signal thresholds significantly. Consider sitting out entirely.
Detected by: narrow overnight range, low pre-market volume,
OR relative volume below 80%, DOJI or near-DOJI open,
MTF conflicted across all timeframes.

NEWS DAY — macro event driving price. Unpredictable.
Reduce confidence in all ICT signals. Widen stops.
Detected by: HIGH impact news within 2 hours,
VIX spike pre-market, overnight gap > 100 points.

HOLIDAY/LOW LIQUIDITY — thin market, erratic fills.
Sit out or reduce to absolute minimum activity.
Detected by: volume below 50% of 20-day average by 10am ET.

**How it changes bot behavior:**
TREND DAY: normal operation, all features active
RANGE DAY: signal threshold raised to 7+, dead zone extended,
          OR break requires 2 confirmations not 1
NEWS DAY:  thesis probability gate raised to 80%,
          stops widened 50%, max 1 trade
HOLIDAY:   FEATURE_HARD_KILL fires, no trades

**Implementation:**
New function classify_session_type() called at 9:30 ET
after pre-market data available.
Injects session type into pre-market Claude prompt and
every entry prompt.
Add FEATURE_SESSION_CLASSIFIER flag (default true).
Log session type prominently at boot and on dashboard.

**Prerequisite:** needs 2-3 weeks of real session data to
validate classifier accuracy. Build the classifier first,
tune the thresholds after data confirms which signals
reliably identify each session type.

**Correlation Awareness (companion feature)**
MNQ correlates strongly with ES, QQQ, VIX, 10-year yield.
On VIX spike days (VIX up 20%+ pre-market) all signals
become less reliable — institutions hedge, correlations break.
Add VIX pre-market reading to news_calendar.py snapshot.
Inject into Claude prompt as macro context.
Gate: if VIX > 25 AND spiking, raise thesis gate to 82%.

### V4.4 — Session Replay Engine (replaces demo.py)

**replay.py — Visual Session Replay**
Command: `py -3.11 replay.py --date 2026-05-27 --speed 2x`

Supported speeds: 0.25x, 0.5x, 1x, 2x, 5x, 10x

Reads `data/snapshots_YYYY-MM-DD.jsonl` and `data/decisions_YYYY-MM-DD.jsonl`.
Rebuilds `dashboard_data.json` and `price_data.json` tick by tick at chosen speed.
Existing `dashboard.html` and `mobile.html` render everything exactly as it happened.

What you see during replay:
- Candles forming bar by bar
- Feature badges lighting up as signals appeared
- Pre-filter decisions shown (PASS/BLOCK + reason)
- Claude reasoning appearing when it fired
- Entry/exit triangles plotting at exact prices
- OR forming then establishing at 9:45
- Bias changes in real time
- P&L updating tick by tick

Purpose: Visual validation of bot decisions.
Identify missed entries, bad exits, correct holds.
Provide feedback on Claude reasoning quality.
Compare different days to find edge patterns.

Prerequisite: `data_recorder.py` must store `bars_1min` and `bars_5min` arrays in snapshots (currently excluded).
Add before Tuesday so first session is replayable.

`demo.py`: keep as system sanity check but mark deprecated.
`replay.py` is the primary visual tool going forward.

**Data Recording Fix (needed before Tuesday)**
Add `bars_1min` (last 50 bars) and `bars_5min` (last 50 bars) to `snapshots_YYYY-MM-DD.jsonl` recording.
Currently excluded to save space but essential for chart replay.
Cap at 50 bars each to control file size.

### V4.4 — Second Pre-Market Analysis at 9:20 ET
- Run a second `analyze_premarket()` call at 9:20 ET (10 min before RTH open)
- Focus: overnight range, globex highs/lows, key levels to watch at the open
- Inject result into the first RTH entry prompt as additional context
- Goal: sharpen the opening bias before the OR forms

**TARGET_2 Executor Support**
- Claude outputs TARGET_2 in every response but executor only uses TARGET_1
- Add partial close at TARGET_1 (50% of position) then trail remainder to TARGET_2
- Enables scale-out exits that lock in profits while letting winners run
- Prerequisite: variable position sizing in V5.0 makes this more meaningful

**First Candle OR Levels (1-min and 5-min)**
- Track the 9:30 first 1-min candle high/low and 9:30 first 5-min candle high/low as named session levels separate from the 15-min OR
- First 1-min candle: micro-OR, early momentum signal
- First 5-min candle: intermediate level, fake move warning zone
- Plot on chart and inject into Claude prompt as reference levels
- Not entry triggers — context and S/R only

### V5.0 — Claude Vision Chart Analysis + Variable Position Sizing
- Send a screenshot of the price chart to Claude Vision with each entry call
- Claude describes what it sees (structure, patterns, key levels) and that text feeds into the reasoning block
- Variable position sizing: 1 contract default, 2 contracts when confluence ≥ 8 AND thesis probability ≥ 80%
- Requires explicit risk-cap review before enabling 2-contract mode

### V5.1 — Tape Reading Rhythm Detection + Additional Levels
- Tape rhythm: detect acceleration/deceleration patterns in large print cadence (not just count)
- Round number awareness: flag proximity to 00/50/25/75 handles in pre-filter and prompt
- Weekly and monthly high/low levels from bar history, injected into session levels
- Enhanced candlestick patterns: 3-bar sequences, pattern quality scoring, context weighting
- Hard kill conditions (FEATURE_HARD_KILL flag): low volume holiday weeks (volume < 50% of 20-day average), consecutive loss days (3+ red days in a row → reduce to 1 trade max). Already hardcoded as kills in some paths — enforce uniformly with flag and log reason when triggered.
- Drawdown Curve Visualization in Journal: `journal.html` equity curve currently shows P&L. Add a second line showing rolling max drawdown so the relationship between features and risk is visible at a glance.

---

## Strategy Expansion Roadmap
Based on academic research in KNOWLEDGE_BASE.md.
Sequenced by statistical edge and implementation complexity.
Each phase requires data validation from previous phase.

---

### PHASE 1 — Foundation (Days 1-30, paper trading)

**Priority 1: Session Type Classifier**
Already in roadmap V4.4. Highest priority.
Unlocks correct strategy selection for all subsequent phases.
Without this: ORB wins 48-52%. With this: 58-64%.
Signals: OR range width, overnight gap, pre-market volume,
VIX level, news events today.
Output: TREND / RANGE / REVERSAL / NEWS / HOLIDAY

**Priority 2: Pivot Points**
Daily classic pivot + R1/R2/S1/S2 calculated from prior day H/L/C.
Add to ibkr_feed._update_session_levels().
Plot on chart as grey horizontal lines.
Score in pre-filter: near R2/S2 on reversal = +1 signal.
Inject into Claude prompt as key reference levels.
Academic edge: R2/S2 reversal 71% of the time.
Implementation: trivial — pure math from prior day OHLC.

**Priority 3: Gap Classification**
Calculate overnight gap: current open vs prior close.
Small gap (< 63 points on MNQ): fill probability 79%.
Medium gap (63-147 points): fill probability 52%.
Large gap (> 147 points): fill probability 28%.
News gap (> 210 points): fill probability 12%.
Add gap_size, gap_direction, gap_fill_probability to snapshot.
Inject into pre-market Claude prompt.
Academic edge: small gap fill 79% reliable.

---

### PHASE 2 — Range Day Strategy (Days 30-60)

**Priority 4: VWAP Reversion Strategy**
Requires: session type classifier (Phase 1) to identify range days.
Academic edge: 72-78% win rate on range days.
Entry conditions:
  Price > 80 points from VWAP AND
  OFI diverging (price moving but OFI flat/opposite) AND
  Volume declining on extension AND
  Session type: RANGE or no clear trend
Stop: 30 points beyond the extension extreme.
Target: VWAP retest.
Add STRATEGY_VWAP_REVERSION to Claude's strategy options.
Add FEATURE_VWAP_REVERSION flag (default false until Phase 2).

**Priority 5: OR Extreme Fade**
When price extends 2x the OR range beyond OR high or low:
This is the institutional overextension zone.
Academic edge: institutions rarely let price extend
2x OR range without reversion.
Fade the extreme: short if 2x above OR high,
long if 2x below OR low.
Stop: 20 points beyond the extreme.
Target: OR midpoint.
Works on range AND reversal days.

**Priority 6: Dead Zone VWAP Magnet**
During 11am-1:30pm ET, price gravitates toward VWAP.
If price is 60+ points from VWAP entering dead zone:
High probability VWAP retest during dead zone.
Currently bot ignores dead zone entirely.
This gives the dead zone a purpose.
Lower threshold: 6 signals (not 8) if VWAP > 60 points away.

---

### PHASE 3 — Reversal Day Strategy (Days 60-90)

**Priority 7: Explicit Sweep Reversal Setup**
Currently sweep is a pre-filter signal (+2).
Needs to become an explicit named strategy.
Entry: price sweeps above prior high/low with DOM confirmation,
then closes back inside within 2 bars.
Stop: beyond the sweep extreme.
Target: prior swing in opposite direction.
Academic edge: 71-78% with DOM confirmation.
Add STRATEGY_SWEEP_REVERSAL to Claude's strategy options.
Add FEATURE_SWEEP_REVERSAL flag.

**Priority 8: Opening Drive Fade**
First 5-min candle moves 80+ points in one direction.
Second candle shows rejection (upper/lower wick > 60% of body).
Fade the opening drive direction.
Stop: beyond the first candle extreme.
Target: 50% retracement of the opening drive.
Academic edge: 65-72% on reversal days.
Most powerful on days where OR eventually goes opposite
to the opening drive direction.

**Priority 9: Post-News Reaction Trade**
Already in roadmap. Add here for sequencing clarity.
30-60 minutes after HIGH impact news:
Direction established, signals more reliable than normal.
Academic edge: slightly elevated accuracy (60+ min post-news).
Add FEATURE_POST_NEWS_REFRESH flag.
Fresh analyze_premarket() call when danger zone clears.

---

### PHASE 4 — Trend Optimization (Days 90-120)

**Priority 10: Trend Continuation Re-entry**
After T1 hit on ORB trade, if trend continues:
Scan for next pullback entry in same direction.
Entry: next HH/HL pullback on 1m after T1.
Stop: below new higher low.
Target: next liquidity level.
Academic edge: 63-68% win rate, 2:1-3:1 R:R.
Requires: TARGET_2 executor support (already in roadmap).
Add FEATURE_TREND_REENTRY flag.

**Priority 11: Market Profile — Initial Balance**
Track the first-hour range (9:30-10:30 ET) as Initial Balance.
IB extension signals:
  Price extends 1x IB: normal range expansion
  Price extends 2x IB: strong trend day confirmed
  Price stays inside IB all day: classic range day
Add IB_high, IB_low, IB_range to snapshot.
Academic edge: IB extension 2x predicts trend day 74% accuracy.
More reliable than OR alone for day classification.

**Priority 12: Momentum Breakout**
Consolidation of 20+ minutes in tight range (< 30 points).
Volume contracting during consolidation.
Explosive break with volume > 150% average.
Enter ON the break (not pullback).
Stop: below consolidation low (for longs).
Target: 2x the consolidation range projected.
Academic edge: 62-68% on trend days with volume.
Contrast with ORB: ORB uses the open as reference,
momentum uses any intraday consolidation.

---

### PHASE 5 — Advanced (Live Money Ready, 120+ days)

**Priority 13: VIX Regime Classification**
VIX < 15: calm, tight stops, normal operation
VIX 15-20: normal, standard operation
VIX 20-25: elevated, widen stops by 30%
VIX 25-35: high, raise thesis gate to 80%, max 1 trade
VIX > 35: extreme, FEATURE_HARD_KILL fires, no trades
Scale stop_ticks = base_stop × (VIX / 15).
Requires external VIX data source.

**Priority 14: Variable Position Sizing**
Already in roadmap V5.0.
Now sequenced correctly after edge is proven.
1 contract until Phase 4 complete.
Scale only after SQN > 2.0 confirmed.

**Priority 15: Full Market Profile**
Single prints, poor highs/lows, TPO analysis.
Predicts daily range and value area migration.
Most complex implementation.
Only after all other phases proven profitable.

---

### Strategy Effectiveness Summary

Strategy              Phase   Day Type    Academic Edge
Pivot Points          1       All         71% R2/S2 fade
Gap Fill              1       Reversal    79% small gaps
VWAP Reversion        2       Range       72-78%
OR Extreme Fade       2       Range       65-70%
Sweep Reversal        3       All         71-78%
Opening Drive Fade    3       Reversal    65-72%
Post-News Reaction    3       News        58-62%
Trend Re-entry        4       Trend       63-68%
Initial Balance       4       All         74% day classification
Momentum Breakout     4       Trend       62-68%
VIX Regime            5       All         Risk management
Variable Sizing       5       All         Multiplier
Market Profile        5       All         Advanced context

All phases gated by: minimum 20 trades per strategy,
positive Wilson lower bound, SQN > 1.0 before advancing.

---

## Version Numbering Convention

| Change | Version | Example |
|---|---|---|
| Major architectural change | X.0 | V4.0, V5.0 |
| Significant new feature | X.Y | V4.2, V4.3 |
| Bug fixes, config changes, small improvements | X.Y.Z | V4.1.1, V4.2.1 |

---

*Last updated: 2026-05-24. Add new items here — do not let features get lost in chat.*
