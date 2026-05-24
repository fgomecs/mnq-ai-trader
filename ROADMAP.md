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

### V4.4 — Second Pre-Market Analysis at 9:20 ET
- Run a second `analyze_premarket()` call at 9:20 ET (10 min before RTH open)
- Focus: overnight range, globex highs/lows, key levels to watch at the open
- Inject result into the first RTH entry prompt as additional context
- Goal: sharpen the opening bias before the OR forms

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

---

## Version Numbering Convention

| Change | Version | Example |
|---|---|---|
| Major architectural change | X.0 | V4.0, V5.0 |
| Significant new feature | X.Y | V4.2, V4.3 |
| Bug fixes, config changes, small improvements | X.Y.Z | V4.1.1, V4.2.1 |

---

*Last updated: 2026-05-24. Add new items here — do not let features get lost in chat.*
