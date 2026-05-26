# MNQ AI Trader — Roadmap

## Completed

### V1.0 — Foundation
- IBKR TWS connection via ib_insync
- Live MNQ L1 data (bid/ask/last/volume)
- Claude Opus decision loop (entry) + Sonnet (position management)
- Bracket order execution (market entry + stop + limit target)
- Basic daily P&L tracking and loss cap
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
- Limit order entry mode (ENTRY_MODE=LIMIT): tries limit at Claude's entry_price, slippage guard, timeout fallback to market
- Configurable trail milestones: TRAIL_PROFIT_1_TICKS=120 → lock 30t above entry; TRAIL_PROFIT_2_TICKS=180 → lock 60t above entry (D.2)
- Pre-market high/low tracking (4am–9am ET globex range), 4 pre-filter signals, surfaced in dashboard
- Pushover push notifications (notifier.py): trade entered/exited, stop→BE, EOD summary, IBKR events
- R:R minimum raised to 3:1 in entry prompt
- PROBABILITY_CONTEXT knowledge base injected into Opus prompts

### V4.4 — Phase 1-3 Strategy Expansion
*Shipped 2026-05-25. Phase 2-3 features default False — activate after session data confirms accuracy.*

**Phase 1 (active by default):**
- `session_classifier.py` — pure Python TREND/RANGE/NEWS/HOLIDAY/UNKNOWN classification at OR_ESTABLISHED (9:45 ET)
- Session type injected into every Claude entry prompt and watchlist call
- Pre-filter threshold routing: RANGE days require 7+ signals (vs 3 normally)
- HOLIDAY sessions hard-blocked in `can_enter()`
- First candle 1-min and 5-min H/L tracked as named snapshot fields
- Gap classification: overnight gap size, direction, fill probability (79%/52%/28%/12%)
- Classic daily pivot points R1/R2/S1/S2 from prior day OHLC; pre-filter +1 near R2/S2
- VWAP extension signed field (vwap_extension, vwap_extension_abs)

**Phase 2 (FEATURE_* = false, activate after data):**
- OR 2x extension detection (or_2x_extension_up/down, or_extreme_zone)
- OR extreme fade pre-filter signals (+2) and prompt injection
- Dead zone VWAP magnet: lowers threshold from 8 to 6 when price is 60+ pts from VWAP
- `can_enter()` accepts snapshot kwarg for VWAP magnet check

**Phase 3 (FEATURE_* = false, activate after data):**
- Opening drive detection: first 5-min candle range + rejection wick
- Post-news window field: 45–75 min after HIGH-impact event
- Sweep reversal: extra +1 pre-filter weight on DOM sweeps
- Opening drive fade: +2 pre-filter + prompt injection
- Post-news watchlist refresh: one-time update_watchlist() call on post_news_window

**Session timing updated:**
- SESSION_AFTERNOON_PRIME_END=1555 (was 1530)
- EOD_SCHEDULE_TIME=16:05 (was 15:30)

**Watchdog:**
- `watchdog.py` — standalone health monitor; alerts via Pushover if bot crashes or dashboard stales

**Bug fixes shipped in V4.4.1:**
- FEATURE_DEAD_ZONE now honored in can_enter()
- FEATURE_NEWS_GATE now checked in run_cycle news block
- Opening drive now uses correct 9:30 5-min bar
- See V4.4.1 entry below for full list

### V4.4.1 — Bug Fixes + Code Optimization
*Shipped 2026-05-25.*
- Fixed FEATURE_DEAD_ZONE not honored in can_enter() — was silently blocking dead zone trading even with flag=false
- Fixed FEATURE_NEWS_GATE not checked in run_cycle hard-block — was blocking entries regardless of flag
- Fixed opening drive used _bars_5min[0] (oldest cached bar) — now finds correct 9:30 RTH bar
- Fixed logger.py hardcoded C:\trading\logs path — now uses BASE_DIR env var
- Fixed structure pullback event trigger now fires for both longs and shorts
- Fixed time.time()%N race condition in dashboard ticker — replaced with persistent tick counters
- Code optimization: moved repeated imports to module level across 6 files (pandas 3×, re 2×, datetime, os, json)
- Code optimization: logger import moved from inside update_dashboard() hot path to module level
- Code optimization: added debug logging to silent except/pass blocks in executor.py

---

## Planned



### V4.5 — Second Pre-Market Analysis + First Candle Context
- Run a second `analyze_premarket()` call at 9:20 ET (10 min before RTH open)
- Focus: overnight range, globex highs/lows, key levels to watch at the open
- First 1-min and 5-min candle levels injected into Claude prompt as reference context (not entry triggers)
- Goal: sharpen opening bias before OR forms

### V4.5 — TARGET_2 Executor Support
- Claude outputs TARGET_2 in every response but executor only uses TARGET_1
- Add partial close at TARGET_1 (50% of position) then trail remainder to TARGET_2
- Prerequisite: variable position sizing in V5.0 makes this more meaningful

### V4.5 — Enhanced Data Recording System (HIGHEST PRIORITY — ship before first live session if possible)

The current recorder captures snapshots and Claude decisions. This enhancement turns the recording system into a full research platform that captures everything the bot does and does not do, enabling empirical strategy validation after 20 sessions.

Five new JSONL files added to data/:

1. decisions_YYYY-MM-DD.jsonl (existing — enhanced)
  Add structured pre_filter_breakdown dict to every record:
  bull_signals count, bear_signals count, bull_reasons list, bear_reasons list, threshold_used, session_type, passed bool, direction

2. outcomes_YYYY-MM-DD.jsonl (new)
  Price outcome at 5, 15, and 30 minutes after every decision and shadow record.
  Answers: was Claude right to say HOLD? Did the setup play out even when the bot passed?

3. sessions_YYYY-MM-DD.jsonl (new)
  One SESSION_START and one SESSION_END record per day.
  Captures: total_scans, pre_filter_passes, claude_calls, skip_cache_hits, api_cost_usd, daily_pnl, trades, wins, losses, session_type, or_range, or_relative_volume, day_of_week

4. positions_YYYY-MM-DD.jsonl (new)
  Full position journey: ENTRY, STOP_MOVED, TRAIL, PRICE_UPDATE every 5s, EXIT.
  Captures MAE (max adverse excursion) and MFE (max favorable excursion) on every trade.
  Answers: are stops too tight, are targets too conservative, is Claude position management adding or destroying value?

5. shadow_decisions in decisions JSONL (new record type="shadow")
  Every non-trade decision recorded with full context.
  reason_type: PRE_FILTER_BLOCK | CLAUDE_HOLD | THESIS_GATE | SESSION_GATE | NEWS_GATE | DAILY_LOSS_GATE
  This is the biggest gap in current recording — every HOLD and every blocked scan becomes a learning event.
  Multiplies learning data by approximately 10x per session.

What this enables after 20 sessions:
- Which signal combinations actually have edge (structured pre_filter_breakdown)
- Whether Claude HOLDs were correct or costly (outcome tracking)
- Whether stops are too tight or targets too conservative (position journey MAE/MFE)
- What conditions the bot performs best in (session metadata)
- Empirical thesis probability threshold vs the current 70% guess (shadow + outcome combined)
- Whether DOM and OFI are adding value or noise (signal breakdown vs outcome correlation)

New config constants:
SHADOW_TRADE_ENABLED=true
OUTCOME_TRACKING_ENABLED=true
OUTCOME_TRACK_MINUTES=5,15,30
POSITION_JOURNEY_ENABLED=true
SESSION_METADATA_ENABLED=true

Code changes required:
- data_recorder.py: four new methods (record_shadow_decision, record_outcome, record_session_metadata, record_position_journey), updated flush_and_close(), updated daily_summary()
- claude_brain.py: pre_filter_signal() returns third value (structured breakdown dict)
- main.py: call record_shadow_decision() at every non-trade decision point, schedule outcome checks at 5/15/30 min, add session scan counters, call record_session_metadata() at OR_ESTABLISHED and EOD
- executor.py: call record_position_journey() on entry, stop moves, price updates, and exit; track MAE/MFE per trade
- config.py: new recording constants
- dashboard_writer.py: add shadow_decisions_today, outcome_records_today, session_scans_today to dashboard JSON

All new recording calls wrapped in try/except — recording must never crash the bot.

Priority note: This ships before Trend Rider Mode, before Session Level Scoring, before everything else in V4.5+. The value of every future feature depends on having rich data to validate it. Without this, the bot is flying blind. With it, every session compounds learning exponentially.

### V4.5 — Session Levels as Pre-Filter Signals (HIGH PRIORITY)

Asia high/low, London high/low, previous day high/low, and previous week high/low are currently tracked in the snapshot and injected into Claude prompts as context text but have zero pre-filter scoring weight. These are the most watched levels by institutional traders. This feature gives them explicit pre-filter scores.

Scoring to add in pre_filter_signal():
- Near previous week high/low (within 10 points): +2 bear near high, +2 bull near low
- Near previous day high/low (within 10 points): +2 bear near high, +2 bull near low
- Near London high/low (within 8 points): +1 bear near high, +1 bull near low
- Near Asia high/low (within 8 points): +1 bear near high, +1 bull near low

All levels already exist in the snapshot (prev_day_high, prev_day_low, prev_week_high, prev_week_low, london_high, london_low, asia_high, asia_low). No new data collection needed.

New config constants:
LEVEL_PREV_WEEK_PROXIMITY=10
LEVEL_PREV_DAY_PROXIMITY=10
LEVEL_LONDON_PROXIMITY=8
LEVEL_ASIA_PROXIMITY=8
FEATURE_SESSION_LEVEL_SCORING=false

Can ship before real session data. Backtest on first available sessions to validate scoring weights.

### V4.6 — Session Type Classifier Tuning
After 2-3 weeks of real session data:
- Validate TREND/RANGE classification accuracy against actual day outcomes
- Tune SESSION_CLASSIFIER_TREND_OR_MIN and SESSION_CLASSIFIER_RANGE_OR_MAX
- Add session type to journal_data.json for per-type P&L analysis
- Add UNKNOWN handling: after 3 UNKNOWN days in a row, classify as RANGE

### V4.6 — Trend Rider Mode

When session is classified TREND and MTF is fully aligned, switch from fixed-target to dynamic trend-riding position management.

Entry: standard ORB pullback or structure confirmation as usual.
At TARGET_1 (3:1): close 50% of position (partial close — locks profit, removes pressure).
Remaining 50%: trail with structural stop — move stop to each new HL (bull) or LH (bear) as they form.
Full exit triggers: CHoCH on 5m, MTF flip, major resistance hit (prev day high, weekly high, daily supply zone), or EOD close.

What needs to be built:
- Partial close in executor (currently all-or-nothing — biggest piece)
- Position management prompt rewrite: trend-riding context vs current fixed-target management
- Trend intact state tracking so Claude knows it is managing a runner not a scalp
- No hard limit order placed at TARGET_1 when trend rider mode is active

Gate before enabling:
- Executor partial close working and paper-tested
- Minimum 20 TREND-classified sessions to confirm classifier accuracy
- Backtest confirms trend days actually trend (not chop misclassified as trend)

Build order dependency: Partial close executor must ship before Trend Rider Mode. Variable position sizing (Phase 5) depends on partial close also being ready. Sequence: Partial close → Trend Rider Mode → Variable sizing.

Academic edge: on confirmed trend days with full MTF alignment, dynamic trailing captures 2x-4x more points than a fixed 3:1 target on the same entry.

### V4.6 — Sweep and Reclaim Named Strategy (HIGH PRIORITY)

Currently DOM sweeps are just a +1 pre-filter bonus (FEATURE_SWEEP_REVERSAL). A sweep of a major level followed by an immediate reclaim close is one of the highest-probability reversal setups in futures trading with 71-78% historical edge. It deserves its own named strategy with explicit entry logic, not just a pre-filter weight.

Setup conditions:
1. Price sweeps above a key level (prev week high, prev day high, London high, OR high) by at least 5 points
2. Closes back below the swept level within 1-2 bars
3. DOM shows absorption on the sweep — large bids holding, OFI diverging (price higher but OFI declining)
4. MTF not strongly bullish (otherwise sweep may be a breakout not a fake)

Entry: first confirmed close back below the swept level
Stop: above the sweep extreme (tight — structure is clear)
Target: VWAP, prior consolidation, or next key level below
Mode: SCALP (fast-moving setup, don't overstay)

Same logic applies in reverse for bear sweeps (sweep below prev week low, reclaim above).

This replaces and supersedes FEATURE_SWEEP_REVERSAL. When this strategy is active, the old +1 pre-filter bonus is absorbed into the named strategy scoring.

New config:
FEATURE_SWEEP_RECLAIM_STRATEGY=false
SWEEP_MIN_POINTS=5
SWEEP_RECLAIM_BARS=2

Gate: 20+ sweep events recorded before enabling. Backtest to confirm detection accuracy.

### V4.6 — Trend Rider Mode (Detailed Spec)

When session is classified TREND and MTF is fully aligned, switch from fixed-target to dynamic trend-riding position management.

Entry: standard ORB pullback or structure confirmation as usual.
At TARGET_1 (3:1): close 50% of position (partial close — locks profit, removes pressure).
Remaining 50%: trail with structural stop — move stop to each new HL (bull) or LH (bear) as they form.
Full exit triggers: CHoCH on 5m, MTF flip, major resistance hit (prev day high, prev week high, daily supply zone), or EOD close.

What needs to be built:
- Partial close in executor (currently all-or-nothing — biggest dependency)
- Position management prompt rewrite: trend-riding context vs current fixed-target management
- Trend intact state tracking so Claude knows it is managing a runner not a scalp
- No hard limit order placed at TARGET_1 when trend rider mode is active

Gate before enabling:
- Executor partial close working and paper-tested
- Minimum 20 TREND-classified sessions to confirm classifier accuracy
- Backtest confirms trend days actually trend and not chop misclassified as TREND

Build order: Partial close executor must ship first. Variable position sizing (Phase 5) also depends on partial close. Sequence: Partial close → Trend Rider Mode → Variable sizing.

Academic edge: on confirmed trend days with full MTF alignment, dynamic trailing captures 2x-4x more points than a fixed 3:1 target on the same entry.

New config:
FEATURE_TREND_RIDER=false
TREND_RIDER_PARTIAL_PCT=0.50

### V4.7 — Level-Proximity Tape Reading (Expanded)

DOM and tape signals (OFI, sweeps, icebergs, large prints, absorption) are only meaningful when price is near a key structural level. Currently the pre-filter scores these signals unconditionally regardless of where price is. This feature adds a proximity gate so tape signals are weighted correctly.

How it works:
Before scoring DOM and tape signals in pre_filter_signal(), compute distance from current price to every tracked key level. If price is within LEVEL_PROXIMITY_THRESHOLD (default 10 points) of any key level, DOM and tape signals receive full weight plus LEVEL_PROXIMITY_BONUS. If price is not near any key level, DOM and tape signals are reduced by 50% or ignored entirely.

Priority order for proximity check:
1. Previous week high/low — strongest, institutional reference
2. Previous day high/low — daily traders watching these
3. London high/low — where London positioned
4. Asia high/low — overnight range extremes
5. OR high/low — intraday reference
6. VWAP — mean reversion anchor
7. Pivot R1/R2/S1/S2 — calculated levels
8. FVG zones and order blocks — ICT structural levels
9. Premarket high/low — globex extremes

Tape signals affected:
- DOM sweep (+2): sweep at a key level is high conviction, sweep in no-man's land is noise
- OFI STRONG (+2): absorption at a level is meaningful, OFI in open air is not
- Iceberg detection (+1): only significant when at known S/R
- Large print tape bias (+2): block buying into resistance vs open air are completely different signals
- DOM cluster magnet (+1): cluster at a key level is a magnet, otherwise unreliable

New config:
LEVEL_PROXIMITY_THRESHOLD=10
LEVEL_PROXIMITY_BONUS=1
FEATURE_LEVEL_PROXIMITY_GATE=false

Gate: minimum 20 sessions of baseline data with current unconditional scoring. Backtest proximity-gated vs unconditional DOM scoring on same sessions before enabling.

Dependency: FEATURE_SESSION_LEVEL_SCORING should be enabled first so all key levels are being scored before proximity gating is layered on top.

### V4.6 — Level-Proximity Tape Reading

DOM and tape signals (OFI, sweeps, icebergs, large prints, absorption) are only meaningful when price is near a key structural level. Currently the pre-filter scores these signals unconditionally regardless of where price is. This feature adds a proximity gate so tape signals are weighted correctly.

How it works:
Before scoring DOM and tape signals in pre_filter_signal(), compute the distance from current price to every tracked key level. If price is within a configurable threshold (default 10 points) of any key level, DOM and tape signals receive full weight or a bonus multiplier. If price is not near any key level, DOM and tape signals are reduced or ignored entirely.

Key levels already tracked by the bot (no new data needed):
- OR high and OR low
- VWAP
- Previous day high and low
- Previous week high and low
- Premarket high and low
- Daily pivot R1, R2, S1, S2
- FVG zones (fvg_levels)
- Order blocks (ob_levels)
- Daily demand and supply zones
- Session high and low
- Liquidity pools (liq_levels)

Tape signals affected by this gate:
- DOM sweep (+2) — sweep at a key level is a high-conviction signal, sweep in no-man's land is noise
- OFI STRONG (+2) — absorption at a level is meaningful, OFI in dead space is not
- Iceberg detection (+1) — only significant when at a known S/R
- Large print tape bias (+2) — block buying into resistance vs block buying in open air are completely different
- DOM cluster magnet (+1) — cluster at a key level is a magnet, otherwise unreliable

New config constants to add:
LEVEL_PROXIMITY_THRESHOLD=10     # Points from a key level for tape signals to count fully
LEVEL_PROXIMITY_BONUS=1          # Extra signal weight when price is within threshold of major level
FEATURE_LEVEL_PROXIMITY_GATE=false  # Gated — activate after baseline data collected

Professional context: experienced tape readers ignore L2 entirely until price is within a few ticks of a known level. They watch for absorption (large bids holding as price tests), exhaustion prints (aggressive buying into resistance that stalls), iceberg reveals (size keeps replenishing), and sweep plus reclaim (strongest reversal signal). The bot has all this data — it just needs to know when to use it.

Gate before enabling:
- Minimum 20 sessions of baseline data with current unconditional scoring
- Backtest comparison: proximity-gated vs unconditional DOM scoring on same sessions
- Confirm key level detection is accurate (fvg_levels, ob_levels, liq_levels populated correctly)

---

### PHASE 2 — Range Day Strategy (Days 30-60)

**Priority 4: VWAP Reversion Strategy**
Requires: session type classifier active and classifying RANGE days accurately.
Academic edge: 72-78% win rate on range days.
Entry conditions:
  Price > 80 points from VWAP AND
  OFI diverging (price moving but OFI flat/opposite) AND
  Volume declining on extension AND
  Session type: RANGE or no clear trend
Stop: 30 points beyond the extension extreme.
Target: VWAP retest.
Feature flag: FEATURE_VWAP_REVERSION (default false — activate Phase 2)

**Priority 5: OR Extreme Fade**
Already built — FEATURE_OR_EXTREME_FADE=false.
When price extends 2x the OR range beyond OR high or low.
Activate after first RANGE day confirms detection is accurate.

**Priority 6: Dead Zone VWAP Magnet**
Already built — FEATURE_DEAD_ZONE_VWAP_MAGNET=false.
Activate after confirming 60pt VWAP extension threshold is triggering correctly.

---

### PHASE 3 — Reversal Day Strategy (Days 60-90)

**Priority 7: Sweep Reversal (named strategy)**
Already partially built — FEATURE_SWEEP_REVERSAL=false (extra +1 weight).
Next step: make it a fully named strategy with specific entry logic in Claude prompt:
  Entry: price sweeps above prior high/low with DOM confirmation, closes back inside within 2 bars
  Stop: beyond the sweep extreme
  Target: prior swing in opposite direction

**Priority 8: Opening Drive Fade**
Already built — FEATURE_OPENING_DRIVE_FADE=false.
Fix the _bars_5min[0] bug first, then activate.
Academic edge: 65-72% on reversal days with rejection wick.

**Priority 9: Post-News Reaction Trade**
Already built — FEATURE_POST_NEWS_REFRESH=false.
Activate after confirming post_news_window detection is reliable (fix regex detection first).

---

### PHASE 4 — Trend Optimization (Days 90-120)

**Priority 10: Session Type Classifier → Session Replay Integration**
`replay.py` visual session replay, reading from recorded JSONL.
Shows session type classification, pre-filter decisions, Claude reasoning tick-by-tick.
Prerequisite: add bars_1min + bars_5min arrays to snapshots_*.jsonl recording.

**Priority 11: Pivot Points R2/S2 Fade Strategy**
Academic edge: R2/S2 reversal 71% of the time.
Already in pre-filter (+1 signal). Next: named strategy for R2/S2 confluence entries.

**Priority 12: Gap Classification → Gap Fill Strategy**
Small gap (<63pts): 79% fill probability already in pre-filter.
Named STRATEGY_GAP_FILL with explicit entry/stop/target logic.
Only on range/reversal days.

---

### Strategy Effectiveness Summary

Strategy              Phase   Day Type    Academic Edge      Status
Session Classifier    1       All         Upstream          LIVE (V4.4)
Pivot Points (score)  1       All         71% R2/S2 fade    LIVE (V4.4)
Gap Classification    1       Reversal    79% small gap     LIVE (V4.4, pre-filter only)
OR Extreme Fade       2       Range       65-70%            BUILT, gated
VWAP Reversion        2       Range       72-78%            PLANNED
Dead Zone VWAP Magnet 2       All         Magnet effect     BUILT, gated
Sweep Reversal        3       All         71-78%            BUILT, gated
Opening Drive Fade    3       Reversal    65-72%            BUILT (bug fix needed)
Post-News Reaction    3       News        58-62%            BUILT, gated
Gap Fill Strategy     4       Reversal    79% small gap     PLANNED
Pivot Fade Strategy   4       All         71%               PLANNED

All phases gated by: minimum 20 trades per strategy, positive Wilson lower bound, SQN > 1.0 before advancing.

---

### PHASE 5 — Advanced (Live Money Ready, 120+ days)

**Priority 13: VIX Regime Classification**
VIX < 15: calm — tight stops, normal operation
VIX 15-20: normal
VIX 20-25: elevated — widen stops by 30%
VIX 25-35: high — raise thesis gate to 80%, max 1 trade
VIX > 35: extreme — FEATURE_HARD_KILL fires, no trades
Requires external VIX data source.

**Priority 14: Variable Position Sizing**
1 contract until Phase 4 complete.
Scale only after SQN > 2.0 confirmed.
2 contracts when confluence ≥ 8 AND thesis probability ≥ 80%.
Requires explicit risk-cap review.

**Priority 15: Claude Vision Chart Analysis**
Send screenshot to Claude Vision with each entry call.
Claude describes what it sees and that text feeds into reasoning block.

**Priority 16: Full Market Profile**
Single prints, poor highs/lows, TPO analysis.
Only after all other phases proven profitable.

---

## Version Numbering Convention

| Change | Version | Example |
|---|---|---|
| Major architectural change | X.0 | V4.0, V5.0 |
| Significant new feature | X.Y | V4.2, V4.3 |
| Bug fixes, config changes, small improvements | X.Y.Z | V4.1.1, V4.4.1 |

---

## Recommended Build Order

0. Enhanced Data Recording System — enables empirical validation of everything that follows
1. Partial close executor
2. Session levels as pre-filter signals
3. Sweep and Reclaim named strategy
4. Trend Rider Mode
5. Level-proximity tape reading
6. Variable position sizing

---

*Last updated: 2026-05-25. Add new items here — do not let features get lost in chat.*

---

## Operational Checklist & Known Gaps

### Contract Roll — URGENT (June 18, 2026)

Current contract MNQM6 expires June 18, 2026. IBKR stops accepting orders on expiring contracts a few days before expiry. Update .env before June 14, 2026:

CONTRACT_EXPIRY=20260919
CONTRACT_CONID=           # look up new conId in IBKR contract search for MNQU6

After updating .env restart the bot. Failure to roll will cause order rejections mid-session with no warning.

Next rolls after September: December 2026 (MNQZ6), March 2027 (MNQH7).

---

### Pre-Session Startup Checklist

Every morning before 8:20 ET:
- IBKR TWS or Gateway is running and logged in
- API connections enabled in TWS settings (Edit → Global Configuration → API)
- Two terminals open: py -3.11 main.py and py -3.11 -m http.server 8080 --bind 0.0.0.0
- Optional third terminal: py -3.11 watchdog.py
- Dashboard visible at localhost:8080/dashboard.html before bot boots
- Mobile dashboard reachable at Tailscale IP before bot boots

TODO: Create start_trading.bat to launch all three with one double-click. This reduces morning friction and eliminates the risk of forgetting the dashboard server.

---

### What To Do When Things Go Wrong

IBKR disconnects mid-trade:
- The stop order is already placed at the broker — it will execute without the bot
- The watchdog will send a Pushover IBKR DISCONNECTED alert
- Check the dashboard — if position shows LONG or SHORT and bot is offline, log into IBKR manually and monitor or close the position
- Do not restart main.py while in a position unless you verify the broker position first
- After reconnect the bot will reconcile via update_position_from_ibkr() on next cycle

Bot crashes mid-session:
- Watchdog detects main.py missing after 2 checks (60s) and sends BOT CRASHED alert
- Stop orders remain active at IBKR — position is protected
- Check logs/ for the last error before restarting
- Restart with py -3.11 main.py — bot will reconnect and reconcile position on boot

Daily loss limit hit:
- Bot stops scanning and logs DAILY LOSS LIMIT HIT
- Does not exit existing position — only blocks new entries
- EOD routine still fires at 16:05 and closes any open position
- Do not manually override the loss cap

Dashboard shows stale data:
- Check that the HTTP server is still running in its terminal
- Check that main.py is still running
- Hard refresh the browser with Ctrl+Shift+R
- If bot is sleeping the dashboard will show BOT SLEEPING — this is normal

---

### Monitoring Workflow

Recommended approach while learning the system:
- Desktop dashboard open during session for full detail
- Mobile dashboard on iPhone for away-from-desk awareness
- Pushover alerts for all entries, exits, and errors
- Check logs/trading_YYYYMMDD.log after session for any warnings or errors
- After EOD fires, check journal.html for the session summary

Do not watch every tick. The bot is designed to be left running. Constant monitoring leads to manual intervention which defeats the purpose.

---

### Paper Trading vs Live Differences

Be aware when transitioning from paper to live:
- Paper trading fills at bid/ask midpoint — live fills will have slippage
- Limit orders may not fill at all on fast-moving entries — the bot has a 5-second timeout then converts to market
- Spread on MNQ is typically 1 tick (0.25 points = $0.50) but widens during news events
- Paper P&L will look cleaner than live P&L — expect 10-15% degradation from slippage alone
- Do not go live until paper trading shows consistent positive expectancy over 20+ sessions

---

### Data Protection

The data/ folder contains all recorded sessions and is the most valuable asset in the system. It is not committed to git (git-ignored). Protect it:

TODO: Set up a daily robocopy or backup to an external drive or cloud folder:
robocopy C:\trading\mnq-ai-trader\data D:\backup\mnq-data /MIR /LOG:backup.log

If the PC dies without a backup all session recordings are lost and the learning flywheel resets.

Same applies to memory/ folder which contains all learning reports and tick state.

---

### Known Behavioral Gaps (fix in future versions)

No multi-day loss awareness: The bot treats every day identically. After 3 consecutive losing days it does not reduce aggressiveness or raise thresholds. A human trader would step back and review. Consider adding a consecutive losing days counter that raises MIN_THESIS_PROBABILITY by 5 points per losing day beyond 2.

Paper trading fill quality: SimExecutor assumes fills at the snapshot price. Real limit orders may not fill if price moves through the level too fast. Backtester results will be optimistic compared to live.

Learning system untested on real data: The full EOD pipeline (ablation → synthesis → version bump → journal export) has never run on real session data. Expect a debug pass after the first real session.

Claude probability calibration: The 70% thesis probability gate is uncalibrated. Claude has no feedback loop on whether its stated probabilities are accurate. After 20 sessions compare stated probabilities to actual outcomes and adjust MIN_THESIS_PROBABILITY empirically.

Dead zone data collection: FEATURE_DEAD_ZONE=false means the bot trades freely 11am-1:30pm ET. Historical data suggests this window is net negative. Monitor dead zone entries separately in the journal and restrict if the data confirms the historical pattern.

## Phase 6 — Jarvis Voice & Personal Assistant

### V5.0 — Bot Speaks During Trading

Bot narrates session events in real time via ElevenLabs TTS. New `voice.py` module wraps the ElevenLabs API and exposes a `speak(text, priority)` queue consumed by a daemon thread so audio never blocks `run_cycle`.

Triggers:
- Trade entered (direction, size, stop, target)
- Trade closed (P&L, reason: target/stop/manual)
- Stop moved to breakeven
- Pre-market analysis complete (8:30 ET)
- OR established (9:45 ET)
- Session classified (TREND / RANGE / NEWS / HOLIDAY)
- HOLD with notable reason (skip-cache miss, thesis change)
- EOD summary (P&L, W-L, best/worst trade)
- Loss warning (approaching daily cap)

New `.env`:
```
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
FEATURE_VOICE=false
```

Cost: ~$5/month at expected event volume.

### V5.1 — Two-Way Push-to-Talk

Push-to-talk hotkey opens the mic, Whisper API transcribes, `voice_assistant.py` reads current `dashboard_data.json` + today's `decisions_*.jsonl`, Claude synthesizes a natural trader-style response, ElevenLabs speaks it back. Single round-trip — no continuous listening.

New `.env`:
```
WHISPER_API_KEY=
VOICE_HOTKEY=ctrl+space
FEATURE_VOICE_ASSISTANT=false
```

### V5.2 — Unified Jarvis Router

One voice, two brains. A router classifies each utterance and dispatches:
- Trading questions → **DoBot** (this repo's context: snapshots, decisions, P&L, session state)
- Life admin / general → **OpenClaw** (calendar, email, notes, web)

Both share the same ElevenLabs voice so the experience feels like one assistant.

New `.env`:
```
FEATURE_UNIFIED_JARVIS=false
```

### V5.3 — Wake Word

"Hey DoBot" wake word using Porcupine (commercial, more accurate) or OpenWakeWord (OSS, free). Only armed during active trading hours to avoid false triggers and CPU drain off-session.

New `.env`:
```
FEATURE_WAKE_WORD=false
```

### Jarvis Build Order

1. **V5.0 `voice.py`** — ship before Tuesday. One-way narration is the highest-value, lowest-risk piece.
2. **V5.1 push-to-talk** — two-way interaction without the wake-word complexity.
3. **V5.2 unified OpenClaw router** — extends scope beyond trading.
4. **V5.3 wake word** — last; needs the other layers stable before hands-free makes sense.

### Cost Estimate

| Service | Monthly |
|---|---|
| ElevenLabs (TTS) | $5 |
| Whisper (STT) | $2 |
| Claude (voice synthesis turns) | $3 |
| **Total** | **$10/month** |
