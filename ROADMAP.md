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

### V4.6 — Session Type Classifier Tuning
After 2-3 weeks of real session data:
- Validate TREND/RANGE classification accuracy against actual day outcomes
- Tune SESSION_CLASSIFIER_TREND_OR_MIN and SESSION_CLASSIFIER_RANGE_OR_MAX
- Add session type to journal_data.json for per-type P&L analysis
- Add UNKNOWN handling: after 3 UNKNOWN days in a row, classify as RANGE

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

*Last updated: 2026-05-25 (V4.4.1). Add new items here — do not let features get lost in chat.*
