# MNQ AI Trader — Complete Project Summary
*For AI reading this cold. Dense, accurate, no padding. Last verified: 2026-05-25 (V4.3).*

**Companion docs:** `CLAUDE.md` (AI-assistant guidance + audit-tag reference), `README.md` (user-facing intro, install, run), `KNOWLEDGE_BASE.md` (academic win-rate / probability calibration research consumed by `claude_brain.py` prompts), `BOT_EVALUATION.md` (performance evaluation framework), `ROADMAP.md` (completed work + planned V4.4 session replay / session-type classifier).

---

## What This Is

Paper-trading bot for **MNQ (Micro E-mini Nasdaq-100)** futures. Pulls live L1+L2 data from IBKR (TWS/Gateway), scores market structure with pure Python signal pre-filters, sends snapshots to Claude (Opus 4.7 for entries, Sonnet 4.6 for position management) for decisions, and executes bracket orders. Hard constraints: $50K simulated account, 1 contract max, configurable daily loss cap (MAX_DAILY_LOSS_PCT × ACCOUNT_SIZE). **Not live money.**

**Strategy:** ICT (Inner Circle Trader) methodology. Opening Range Breakout with pullback entry. CHoCH (Change of Character) confirmation. Dual-sided bias — OR direction is a starting preference, not a law. Kill zones (NY AM 8:30–11, NY PM 1:30–4 ET).

**Version as of this document:** 4.3.x (patch auto-bumped at EOD by `learning_session.py`). V4.2 added snapshot enrichment (candle patterns, tape bias, daily zones, premarket H/L) and `FEATURE_DOJI_MTF_OVERRIDE`. V4.3 added `notifier.py` Pushover push notifications, raised entry R:R floor to 3:1, and injected `PROBABILITY_CONTEXT` knowledge base into Opus prompts.

---

## Architecture

### Three concurrent threads

| Thread | Location | Cadence | Job |
|--------|----------|---------|-----|
| Main loop | `main.run_cycle()` | 0.5s sleep; pre-filter gated to 5s | snapshot → pre-filter → Claude → executor |
| Protection loop | `Executor._fast_protection_loop` daemon | 5s | stop/target check, broker reconciliation, orphan detection |
| Dashboard ticker | `main._fast_dashboard_ticker` daemon | 1 Hz | writes `price_data.json`, patches `dashboard_data.json` every 10s |

### Data flow per cycle

```
ibkr_feed.get_snapshot()        → snapshot dict (~50 fields)
  → claude_brain.pre_filter_signal()   → (worth_calling: bool, reason: str)
    → claude_brain.analyze_market()    → decision dict  [Opus 4.7, skip-cache A.1]
      → executor.execute()             → bracket order (market + stop + limit)
        → dashboard_writer.update_dashboard()   → dashboard_data.json
        → data_recorder.record_snapshot/decision() → data/YYYY-MM-DD.jsonl
```

### Shared state warning
`Executor` instance shared across all three threads. Internal `_lock` guards all mutation. **Never call IBKR APIs inside the lock** (P1.1 fix — `_get_market_price()` reads `_last_price` outside the lock to avoid blocking the protection thread).

---

## Python Files — Complete Reference

### `main.py` (~1050 lines)
**Role:** Session state machine and orchestration entry point.

**Key enums/functions:**

| Symbol | Purpose |
|--------|---------|
| `SessionState` (enum) | PRE_SESSION, PRE_MARKET, OR_FORMING, OR_ESTABLISHED, PRIME_WINDOW, DEAD_ZONE, AFTERNOON_PRIME, CLOSING, AFTER_HOURS |
| `get_session_state(now_et)` | Maps `HHMM` integer to `SessionState`. All thresholds from `config.py`. |
| `can_enter(state, confluence_score)` | Returns `(bool, reason)`. Dead zone requires score ≥ `DEAD_ZONE_CONFLUENCE_THRESHOLD` (8). |
| `_should_call_claude_now(executor, snapshot)` | Event-driven position trigger. Returns `(bool, reason)` on: adverse move ≥ 10t, delta flip, stop proximity ≤ 30t, target proximity ≤ 20t, giveback ≥ 30t from ≥40t peak, structure pullback. |
| `_reset_position_tracking()` | Clears `_last_position_price`, `_peak_profit_ticks`, etc. |
| `run_premarket(feed)` | Called once at 8:30 ET. Loads 5-day memory, calls `analyze_premarket`. Sets `premarket_done=True`. |
| `run_cycle(feed, executor)` | Main loop body. Session gating → snapshot → watchlist refresh → in-position check or entry scan → dashboard update. |
| `end_of_day(feed, executor)` | Cancels orders, saves daily summary, calls `reset_session_state()` (P2.8), flushes recorder, optionally runs EOD learning session. |
| `_fast_dashboard_ticker(feed, executor)` | 1 Hz daemon. Polls IBKR ticker; synthesizes bid/ask if delayed mode. Calls `update_price_only()` every second, `_patch_dashboard_live()` every 10s. |
| `_patch_dashboard_live(feed, executor, price, account)` | Writes OR data, session state, P&L to dashboard every 10s independent of Claude. |
| `is_trading_hours(now_et)` | Returns False on Saturday, Sunday before 18:00 ET, CME holidays, and after early-close time. Fail-open (returns True if calendar unavailable). |
| `_wait_for_market_hours()` | Blocks until 08:20 ET on a trading day. Loops in 30-min ticks on weekends/holidays, 60s ticks on pre-market mornings. Writes `bot_sleeping=True` to dashboard during sleep. |
| `_get_cme_calendar()` | Lazy-loads `exchange_calendars.get_calendar("XNYS")`. Returns None on failure. Uses XNYS (NYSE) because MNQ follows NYSE holiday schedule. |
| `_is_cme_session(date_et)` | Returns True if date is a valid trading session per XNYS calendar. |
| `_cme_early_close_time_et(date_et)` | Returns early close HHMM int for early-close days (e.g. day before Thanksgiving), else None. |
| `_next_session_label(date_et)` | Returns human-readable label for next trading day (e.g. "Tuesday May 26"). Uses `sessions_in_range()`. |
| `main()` | Boot sequence: clear stale dashboard, `_wait_for_market_hours()`, connect IBKR, `initialize_bars()`, `restore_tick_state()`, start protection loop, start dashboard ticker, build watchlist, schedule EOD. |

**Module-level state (not persisted across days):**
- `last_analysis_time`, `last_position_time`, `last_watchlist_time` — interval trackers
- `premarket_done` — gate for `run_premarket`
- `analysis_log` — list of entry scan records, cleared at EOD
- `_last_position_price`, `_last_position_delta`, `_peak_profit_ticks` — event trigger tracking
- `_fast_ticker_running`, `_last_snapshot_lock`, `_last_snapshot` — ticker state
- `_cme_calendar` — lazy-loaded exchange_calendars instance

**C.6:** On `main()` boot, `dashboard_data.json` is deleted so stale EOD reasoning doesn't persist.

**C.4:** `_flush_log()` called after every BUY/SELL/CLOSE to ensure entries land on disk before crash.

**P1.3:** `_position_entry_time` global was removed. Use `executor.entry_timestamp` instead.

**Imports from:** `config`, `logger`, `news_calendar`, `ibkr_feed`, `claude_brain`, `executor`, `memory_manager`, `dashboard_writer`, `data_recorder`, `learning_session` (lazy at EOD).

---

### `claude_brain.py` (~1690 lines)
**Role:** All Anthropic API calls. Session state management. Pre-filter scoring.

**Models used:**
- `CLAUDE_ENTRY_MODEL` = `claude-opus-4-7` — entry decisions, pre-market analysis
- `CLAUDE_POSITION_MODEL` = `claude-sonnet-4-6` — in-position management
- `CLAUDE_STRUCTURE_MODEL` = `claude-sonnet-4-6` — watchlist/game plan

**System prompts (three, module-level strings):**
- `SYSTEM_PROMPT` — entry analysis. ICT methodology, OR bias rules, kill zones, stop/target requirements, thesis probability calibration, exact output format.
- `POSITION_SYSTEM` — position management. Trail rules, close triggers, output format.
- `STRUCTURE_SYSTEM` — watchlist/game plan. Bias framework, dual-sided setup analysis, JSON output format with all fields.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `update_watchlist(snapshot)` | Sonnet call every 5min. Returns structured watchlist JSON with bias, setups, levels. Applies V3.0 bias validation rules (DOJI→NO_TRADE, MTF override, 90min decay, 80pt invalidation). |
| `get_watchlist()` | Returns `_session_watchlist` dict. |
| `pre_filter_signal(snapshot)` | Pure Python. Scores 14+ bull and bear signals. Returns `(bool, reason)`. Never calls API. |
| `analyze_market(snapshot)` | Opus entry call. Checks skip-cache (A.1) first. Builds static (watchlist+stable context) and dynamic (snapshot+volatile) blocks. Records to backtest files. |
| `analyze_position(snapshot, position, entry_price, stop_price, target_price, trade_mode)` | Sonnet position management call. Uncached user content. Returns HOLD/CLOSE/TRAIL + new_stop + reasoning. |
| `analyze_premarket(snapshot, memory_context)` | Opus pre-market game plan. Injects `load_learning_for_premarket()` if `FEATURE_LEARNING_INJECT`. |
| `parse_decision(text, allow_zero_stop)` | Parses Claude's line-by-line response into dict. P1.7: demotes BUY/SELL to HOLD if stop_price ≤ 0. V4.0: demotes if thesis_probability < `MIN_THESIS_PROBABILITY`. |
| `parse_position_decision(text)` | Parses DECISION/NEW_STOP/CONFIDENCE/THESIS_STATUS/REASONING. |
| `reset_session_state()` | P2.8: Wipes `_session_watchlist`, `_session_context`, `_last_entry_call`, resets cost tracker. Called at EOD. |
| `update_session_context(snapshot, decision, reasoning)` | Updates `_session_context` after every call. Tracks `consecutive_holds`, `setups_passed`. |
| `get_cost_summary()` | Returns total USD spent, calls by model, calls by purpose, skipped call count. |
| `_build_system(prompt)` | Returns list with `cache_control: ephemeral` if caching on, else plain string. |
| `_build_user_content(static_blocks, dynamic_block)` | Last static block gets `cache_control`, dynamic block is uncached. |
| `_log_cache_usage(resp, model, purpose)` | Computes call cost from token counts. Updates `_cost_tracker`. Logs hit rate. |
| `_maybe_skip_call(snapshot)` | A.1: Returns cached HOLD if price unchanged (<5pt), no new bar, watchlist not fresh, age <3min. |
| `_tolerant_json_parse(raw)` | Strips fences, tries strict parse, escapes bare control chars in strings, regex fallback. Used for watchlist JSON. |

**Module-level state (all wiped by `reset_session_state()`):**
- `_session_watchlist` — current game plan dict
- `_watchlist_time` — epoch time of last watchlist update
- `_session_context` — dict with OR direction, last decision, consecutive holds, etc.
- `_last_entry_call` — skip-cache state: `{ts, price, last_bar_ts, watchlist_ts, decision}`
- `_cost_tracker` — `{total_usd, by_model, by_purpose, skipped_calls}`

**Prompt caching strategy (A.2):**
- System prompt (~2500 tokens) → cached via `cache_control: ephemeral` (5-min TTL)
- Static user block: watchlist + stable session context (~800 tokens) → cached
- Dynamic user block: snapshot + volatile bits → uncached, changes every call
- Result: ~60–70% cache hit rate within 5-min window → ~10% of standard token cost

**Pre-filter signal scoring:** Both bull and bear scored independently. Signals with weights:
- Above/below OR high/low: +2
- CHoCH bullish/bearish: +2
- Entry zone active: +2
- DOM sweep up/down: +2
- OFI STRONG_BUY/SELL (if `FEATURE_OFI`): +2
- OFI BUY/SELL: +1
- OFI accelerating: +1
- Above/below VWAP: +1
- Delta positive/negative: +1
- MTF aligned/partial: +1
- DOM bid/ask heavy: +1
- DOM buy pressure threshold: +1
- DOM vacuum above/below: +1
- DOM iceberg bid/ask: +1
- DOM cluster magnet nearby: +1
- Volume profile breakout (above VAH / below VAL): +1
- Above/below POC in value area: +1

Thresholds: `PRE_FILTER_SIGNAL_THRESHOLD` (3) for bias direction; `COUNTER_TREND_SIGNAL_THRESHOLD` (5) for counter-trend.

---

### `ibkr_feed.py` (~2000+ lines)
**Role:** IBKR connection, real-time data, snapshot assembly. Central data abstraction.

**Key public methods:**

| Method | Purpose |
|--------|---------|
| `connect()` | Connects IBKR, qualifies contract, starts tick/DOM streams if `LIVE_DATA_ACTIVE`. |
| `initialize_bars()` | One-time historical bar fetch at startup. Subscribes to 5-sec real-time bars. Calls `_refresh_ict_levels()`. |
| `get_snapshot(current_position, daily_pnl, ...)` | Returns 50-field dict from cached bars + live price. Calls `get_news_snapshot()`. Calls `maybe_persist_tick_state()`. |
| `restore_tick_state()` | B.2: Reads `memory/tick_state.json` on startup. Restores `tick_delta` and `volume_profile` if same trading day. |
| `maybe_persist_tick_state()` | B.1: Saves tick state to disk every 30s. Called from snapshot path. |
| `get_account_data()` | Returns `{net_liquidation, unrealized_pnl, realized_pnl, excess_liquidity}`. |
| `disconnect()` | Cancels DOM/RT bar subscriptions, disconnects IB. |

**Internal state:**
- `_bars_1min`, `_bars_5min`, `_bars_15min`, `_bars_daily` — bar caches, protected by `_bar_lock`
- `_ict_cache` — FVGs, OBs, liquidity pools, CHoCH, MTF alignment, OFI — refreshed on each 5-min bar close
- `_dom_history` — list of last 12 DOM snapshots `{ts, asks:{p:s}, bids:{p:s}}` for iceberg/spoof/sweep detection
- `tick_delta` — cumulative signed trade volume (bid=negative, ask=positive)
- `or_high`, `or_low`, `or_direction`, `or_broken_up`, `or_broken_down`, `or_break_count`
- `or_pullback_low`, `or_entry_zone_active` — pullback tracking for ORB entry model
- `volume_profile` — dict `{price: volume}` for session

**Real-time bar accumulation:** 5-sec `reqRealTimeBars` → 12 bars → synthetic 1-min bar appended to `_bars_1min`. ICT levels refreshed every 5-min boundary (`t % 5 == 0`).

**Tick stream (LIVE_DATA_ACTIVE only):** `reqTickByTickData("AllLast")`. Classifies each trade as buy (≥ ask) or sell (≤ bid) for true delta. Falls back to last-trade-price comparison. Resets `_delta_last_bar` each minute. Updates `volume_profile`.

**DOM stream (LIVE_DATA_ACTIVE only):** `reqMktDepth(numRows=20)`. Stores snapshots in `_dom_history`. Detects:
- **Iceberg:** Level shrinks ≥60% then recovers ≥70% — replenishing hidden size
- **Spoof:** Level ≥ `DOM_LARGE_SIZE` appeared, then vanished — manipulation flag
- **Sweep:** ≥3 consecutive DOM levels consumed in one direction
- **Cluster:** Group of large orders within 5 ticks of each other

**IBKR news (tick 292):** Subscribed via QQQ (Nasdaq ETF proxy — futures don't support news subscriptions). `_on_tick_news` handler stores last 10 headlines in `_ibkr_headlines`. Injected into entry prompt as `ibkr_headlines_text`.

**Snapshot dict (~50 fields):**
```
# Time / Price
time_et, last_price, bid, ask, volume

# Delta / Order Flow
cumulative_delta, delta_trend, delta_last_bar, delta_is_live

# Opening Range
or_direction (BULL/BEAR/DOJI/PENDING), or_high, or_low
or_broken_up, or_broken_down, or_break_attempts, or_relative_volume
or_pullback_low, or_entry_zone_active, mins_since_or

# Multi-timeframe
mtf_alignment (text), mtf_score (dict: score 0-100, bull_tfs, bear_tfs)
htf_bias (text), market_structure (text)

# ICT Levels
fair_value_gaps (text), order_blocks (text), liquidity_pools (text)
choch (text), inducement (text), session_levels (text)

# Context
killzone, amd_phase, session_phase
vwap, session_high, session_low
candles (text: last 1m/5m bars, ~1200 chars)

# V4.2 enrichment
candle_patterns (text — engulfing/hammer/star/inside-bar on 1m/5m, via _detect_candle_patterns)
tape_bias (AGGRESSIVE_BUYING / AGGRESSIVE_SELLING / NEUTRAL)
tape_analysis (dict: large_print_count_60s, tape_bull_pressure, tape_bear_pressure, tape_bias, tape_text, recent_large_prints)
daily_zones (dict: demand_zones, supply_zones, near_demand, near_supply, zones_text — from _find_daily_zones on daily bar reversals)
premarket_high, premarket_low (4am-9am ET globex extremes, computed in _update_session_levels)
prev_week_high, prev_week_low

# DOM
dom (text: 20 levels), dom_imbalance, dom_buy_pressure
dom_resistance_wall, dom_support_wall
dom_vacuum_above, dom_vacuum_below
dom_cluster_above, dom_cluster_below
dom_iceberg_bid, dom_iceberg_ask
dom_spoof_bid, dom_spoof_ask
dom_sweep_up, dom_sweep_down

# OFI
ofi (dict: score, signal, acceleration, divergence, text)

# Volume Profile
volume_profile (text), vp_poc, vp_vah, vp_val, vp_status
vp_above_vah, vp_below_val, vp_inside_va

# News
news_danger_zone, news_text, news_countdown
next_event_full, next_event_minutes, recent_event
ibkr_headlines_text

# Risk (injected by main.py)
current_position, daily_pnl, daily_loss_remaining, consecutive_losses

# Backtester annotation
_pre_filter_reason
```

**Adding a snapshot field:** Set in `get_snapshot()` → read in `pre_filter_signal` if it affects scoring → surface in prompts in `claude_brain` → add to `dashboard_writer` → recorder picks up automatically (old JSONL files will have `None` for new fields).

---

### `executor.py` (~890 lines)
**Role:** Order placement, position tracking, protection loop, P&L accounting.

**Class:** `Executor(ib_instance, contract, paper=True)`

**Key state:**
- `current_position` — signed contract count (positive = long, negative = short, 0 = flat)
- `entry_price`, `stop_price`, `target_price`, `trade_mode` — set on fill
- `entry_timestamp` — epoch time of fill (P1.3 — owned here, not a module global)
- `daily_pnl`, `daily_loss_remaining`, `consecutive_losses`
- `session_r_spent` — R units risked this session (D.1)
- `_last_price` — updated by `update_price()` from fast ticker and protection loop
- `_claude_trail_stop` — last stop set by Claude TRAIL; auto-trail never moves stop looser (D.2)
- `_stop_order_id`, `_target_order_id` — IBKR order IDs for bracket
- `_closing_in_progress` — mutex flag, prevents duplicate close attempts
- `_needs_close` — string flag set by protection thread, consumed on main thread

**Key methods:**

| Method | Purpose |
|--------|---------|
| `execute(decision)` | Dispatches BUY/SELL/CLOSE/HOLD. Acquires `_lock`. Safety checks. |
| `start_protection_loop()` | Spawns `_fast_protection_loop` daemon thread. |
| `_fast_protection_loop()` | Every 5s: check stop/target against `_last_price`; reconcile with broker every 4 loops (FIX 4). |
| `_enter_trade(direction, contracts, ...)` | Places market order; waits 1.5s for fill; places stop + limit bracket. Sets all position state. P1.2: `outsideRth=True`. |
| `_close_position(price, reason)` | FIX 1+2: pre-flight broker check (abort if already flat), cancel bracket, post-cancel recheck, market close. P&L via `_record_pnl`. Orphan check. |
| `_record_pnl(entry, exit, contracts, was_long, reason)` | Computes P&L. FIX 6: rejects if |pnl| > $1000/contract (sanity bound). Updates `daily_pnl`, `session_r_spent`, `trades_today`. |
| `_broker_position()` | Queries IBKR `positions()` for MNQ contract size. Bypasses local state. |
| `_reconcile_with_broker()` | Protection thread detects drift, sets `_needs_close` with "RECONCILE:" prefix. |
| `_handle_reconcile_on_main(reason)` | Main thread consumes reconciliation. Handles: broker flat but local thinks in position; unexpected broker position; size mismatch. |
| `_infer_recent_exit_fill(was_long, entry_price)` | Scans last 10 `ib.fills()` for matching exit execution. Used when bracket fires before our close. |
| `update_position_from_ibkr()` | Syncs local position with broker. Called from `run_cycle`. |
| `check_pending_close()` | Main thread consumes `_needs_close`. Handles RECONCILE prefix specially. |
| `_auto_trail_long/short(price)` | At +50t: stop → entry. At +100t: stop → entry+25t. At +150t: stop → entry+50t. D.2: never moves stop looser than `_claude_trail_stop`. |
| `_get_market_price()` | Returns `_last_price`. No IBKR calls (P1.1). |
| `_cancel_all_orders_and_wait(timeout=5)` | Cancels all open orders, waits up to 5s for confirmation. |

**Race condition fixes summary:**
- **FIX 1+2:** Pre-flight + post-cancel broker checks before submitting close order
- **FIX 3:** Skip stop check if `stop_price ≤ 0`, log warning throttled to 1/min
- **FIX 4:** Periodic broker reconciliation (every 4 protection loops)
- **FIX 5:** Orphan check runs synchronously on caller thread (not background thread)
- **FIX 6:** P&L sanity bound $1000/contract

---

### `config.py` (~300 lines)
**Role:** Single source of truth for all constants. All env-overridable via `.env`.

**Helpers:** `_env_float(key, default)`, `_env_int(key, default)`, `_env_bool(key, default)` — gracefully fall back to defaults on missing/invalid env vars.

**`get_active_features()` → dict** — returns all 15 feature flags and their current state.
**`features_summary()` → str** — one-line "ON:... | OFF:..." log string.

**When run directly (`py -3.11 config.py`):** Prints version, account size, max loss, model, feature summary.

**Critical constants (defaults):**
```
ACCOUNT_SIZE=50000, MAX_DAILY_LOSS_USD=500, MAX_SESSION_R_LOSS=3.0
CONTRACT_EXPIRY=20260618, CONTRACT_CONID=770561201   ← update quarterly
SCALP_STOP_TICKS=100, SCALP_TARGET_TICKS=200
SWING_STOP_TICKS=120, SWING_TARGET_TICKS=300
ENTRY_SCAN_INTERVAL_SECS=5, POS_INTERVAL_NORMAL_SECS=60
PROTECTION_LOOP_SECS=5, WATCHLIST_REFRESH_SECS=300
PRE_FILTER_SIGNAL_THRESHOLD=3, COUNTER_TREND_SIGNAL_THRESHOLD=5
SKIP_CACHE_PRICE_DELTA=5.0, SKIP_CACHE_MAX_AGE_SECS=180
MIN_THESIS_PROBABILITY=70
DEAD_ZONE_CONFLUENCE_THRESHOLD=8
```

---

### `dashboard_writer.py` (~290 lines)
**Role:** Writes `dashboard_data.json` (full state, read by browser) and `price_data.json` (lightweight, 1 Hz).

**Key functions:**

| Function | Purpose |
|----------|---------|
| `update_dashboard(**kwargs)` | Full state write. Merges with existing file to preserve fields the fast ticker doesn't update (reasoning, ICT levels, news). P1.6: `reasoning` block carries `iso_ts` for age display. |
| `update_price_only(price, bid, ask, ...)` | Fast write. No merge. Called at 1 Hz from dashboard ticker. |

**Merge logic:** When fast ticker writes (no `last_decision` arg), existing `reasoning`, `candleText`, `sessionLevels`, `newsText`, ICT fields (fair_value_gaps, order_blocks, etc.) are preserved from the previous full write.

**bot_sleeping field:** When `bot_sleeping=True` is passed, `data_mode` is set to "BOT SLEEPING" and `botSleeping`/`wakeTime` fields are written. Dashboard reads these to show dormant state.

---

### `ibkr_feed.py` — ICT computation methods (internal)

| Method | Computes |
|--------|---------|
| `_find_fvgs(bars_5, price)` | Fair Value Gaps within `FVG_PROXIMITY_POINTS` (100pts) of current price. 3-bar pattern. |
| `_find_order_blocks(bars_5, price)` | Last bearish/bullish candles before strong move, within `OB_PROXIMITY_POINTS` (150pts). |
| `_find_liquidity_pools(bars_5, price)` | Equal highs/lows within `LIQUIDITY_POOL_TOLERANCE` (2pts) of each other. |
| `_detect_choch(bars_1)` | Swing high/low comparison on 1-min bars. Returns "BULLISH_CHOCH", "BEARISH_CHOCH", or "NEUTRAL". |
| `_detect_inducement(bars_5, now_et)` | Liquidity sweep setup. |
| `_analyze_market_structure(bars_15, bars_5)` | HH/HL or LH/LL on 15-min. |
| `_calculate_htf_bias(bars_daily, bars_15)` | Daily: 3 consecutive higher closes = BULLISH. |
| `_check_mtf_alignment(bars_1, bars_5, bars_15)` | Returns BULLISH_ALIGNED / BEARISH_ALIGNED / CONFLICTED / PARTIAL_BULL / PARTIAL_BEAR. |
| `_check_mtf_score(bars_1, bars_5, bars_15)` | Returns `{score: 0-100, bull_tfs: N, bear_tfs: N}`. |
| `_compute_ofi()` | Order Flow Imbalance from DOM history. Returns `{score, signal, acceleration, divergence, text}`. |
| `_compute_volume_profile()` | Builds session VP from tick data. Returns POC, VAH, VAL. |
| `_determine_amd_phase(now_et)` | Accumulation (0-9:30), Manipulation (9:30-10:30), Distribution (10:30+) or PM cycle. |
| `_get_killzone(now_et)` | Returns "NY_AM_KZ", "NY_PM_KZ", or "NO_KZ". |
| `_update_or_pullback_tracking()` | After OR break: tracks pullback, sets `or_entry_zone_active` when pullback resolves. |

---

### `executor.py` — Auto-trail milestones

| Profit (ticks) | Action |
|----------------|--------|
| +50 (long: price < entry) | Stop → entry (breakeven) |
| +120 | Stop → entry + 30 ticks |
| +180 | Stop → entry + 60 ticks |

D.2: `effective_floor = max(proposed, _claude_trail_stop)` — Claude's structural stop always wins.

---

### `backtester.py` (~350+ lines)
**Role:** Replays recorded sessions. No IBKR connection. Full day in <5 seconds.

**CLI:**
```
py -3.11 backtester.py --list
py -3.11 backtester.py --date 2026-05-27
py -3.11 backtester.py --date 2026-05-27 --verbose
py -3.11 backtester.py --date 2026-05-27 --no-live-claude
```

**`run_backtest(date_str, verbose, use_claude_for_uncached)` → dict**
Returns: `{daily_pnl, trade_count, wins, losses, win_rate, trades: list}`.

**Mechanism:**
1. Load `data/snapshots_YYYY-MM-DD.jsonl`
2. Load `data/decisions_YYYY-MM-DD.jsonl` → keyed by `ts_et` (HH:MM)
3. For each snapshot: run current `pre_filter_signal()` code
4. If passes: look up cached decision by timestamp. Cache hit = free (<1ms). Miss = call Claude API (unless `--no-live-claude`).
5. `SimExecutor` simulates position state, applies stops/targets from subsequent snapshots.

**Called by:** `ablation_runner.py`, manually for regression testing.

---

### `ablation_runner.py` (~300 lines)
**Role:** Ablation testing — disable each feature flag, run backtest, measure contribution.

**`ABLATION_FLAGS` dict:** 12 env var keys mapped to human labels. Excludes `FEATURE_LEARNING_EOD`, `FEATURE_LEARNING_INJECT`, `FEATURE_DELTA_LIVE` (safety features never toggled). FEATURE_DOJI_MTF_OVERRIDE is also excluded from ablation (not listed in ABLATION_FLAGS).

**`run_ablation(date_str, verbose)` → dict**
Returns: `{date, baseline, ablations: {label: {results, delta_pnl, delta_trades, verdict}}, report}`.

**Algorithm:**
1. Baseline: all flags ON
2. For each flag: force-reload `config`, `backtester`, `claude_brain` modules (they read env at import time); run backtest with one flag OFF
3. `delta_pnl = baseline_pnl - without_feature_pnl` (positive = feature helped)
4. Verdict: HELPS if delta > $2, HURTS if delta < -$2, NEUTRAL otherwise

**`save_report(report_text, date_str)` → Path** — writes `reports/ablation_YYYY-MM-DD.md`.

**Note: git operations were removed (commit 1df1ea0).** This module only computes results; no git calls.

---

### `learning_session.py` (~300 lines)
**Role:** EOD learning orchestrator. Called from `main.end_of_day()`.

**`run_learning_session(date_str, session_summary, trades)` → str (report path)**
1. Check for session data in `data/snapshots_*.jsonl`
2. Run ablation via `ablation_runner.run_ablation()`
3. Load last 5 learning reports for trend context
4. Call Claude Sonnet for synthesis (600-word max, 5 sections: observations, patterns, feature recs, entry quality, tomorrow's focus)
5. Save report to `reports/learning_YYYY-MM-DD.md` AND `memory/learning_YYYY-MM-DD.md`
6. Export journal via `journal_exporter.run()`
7. Bump version via `version_manager.eod_commit()` (patch bump)

**`load_learning_for_premarket(n_days=3)` → str**
Reads last N `memory/learning_*.md` files, extracts "## Claude's Analysis" section (≤600 chars each), returns formatted block for pre-market prompt injection.


---

### `version_manager.py` (~110 lines)
**Role:** Reads and writes `BOT_VERSION` in `.env`. No git operations.

**`read_version()` → str** — reads `BOT_VERSION=` from `.env`.
**`write_version(version)` → None** — updates `BOT_VERSION=` line in `.env`.
**`bump_version(current, level)` → str** — "patch"=+0.0.1, "minor"=+0.1.0, "major"=+1.0.0.
**`eod_commit(session_summary, bump, extra_message)` → str** — bumps version in `.env`, returns new version string. Does NOT commit to git (git automation removed).

**CLI:** `py -3.11 version_manager.py --bump minor --message "added X"` or `--show`.

---

### `journal_exporter.py` (~315 lines)
**Role:** Builds `journal_data.json` from all recorded `decisions_*.jsonl` files.

**`build_journal(starting_balance, account_name)` → dict** — scans all available dates in `data/`, accumulates stats.

**`run()` → None** — entry point. Reads `ACCOUNT_SIZE` from config, writes `journal_data.json` to `BASE_DIR`. Called by `learning_session.run_learning_session()` at EOD.

**Records consumed:**
- `type="trade"` — from `decisions_YYYY-MM-DD.jsonl` (written by executor)
- `type="decision"` — Claude API call records, matched to trades via timestamp

**`_match_decision(trade, decisions)` → dict|None** — finds the latest BUY/SELL decision record at or before the trade timestamp.

**Output schema:** See `dashboard_data.json and journal_data.json Schemas` section below.

---

### `memory_manager.py` (~400 lines)
**Role:** Session memory. EOD lesson extraction. Morning review.

**`load_recent_memory(days=5)` → str** — Loads last 5 days' `lessons_*.json`, returns formatted context string for Claude prompts. Highlights recurring mistakes (≥2 days), carry-forward levels, warnings.

**`save_daily_summary(trades, daily_pnl, analysis_log)` → str** — Asks Sonnet to extract structured lessons (grade, what worked, what failed, rules violated, carry-forward levels). Saves `memory/lessons_YYYY-MM-DD.json` and `memory/summary_YYYY-MM-DD.md`.

**`generate_morning_review(current_snapshot)` → str** — Asks Sonnet for pre-session brief (max 300 words): recurring mistakes, working setups, carry-forward levels, mental focus, warnings.

**`save_trade_to_memory(trade)` / `load_todays_trades()` → list** — Per-trade JSON append to `memory/trades_YYYY-MM-DD.json`.

---

### `news_calendar.py` (~350+ lines)
**Role:** Economic calendar. Danger zone gating. News text for prompts.

**Sources:**
1. **FRED API** (primary) — free, official. Set `FRED_API_KEY` in `.env`. Covers NFP, CPI, PPI, GDP, PCE, Retail Sales, Jobless Claims, JOLTS, etc.
2. **Hardcoded recurring schedule** (fallback) — day-of-week + time-of-day rules for all weekly/monthly releases. Works with zero network access.
3. **FOMC hardcoded dates** — `FOMC_DATES_2026`, `FOMC_DECISION_DATES_2026`.

**`get_news_snapshot(ib=None)` → dict** — Returns:
```
{news_danger_zone, news_text, news_countdown,
 next_high_impact, next_event_full, next_event_minutes,
 recent_event, events_today: list, ibkr_headlines: list}
```
Danger zone = HIGH impact event within `NEWS_DANGER_WINDOW_MINS` (45 min) before or `NEWS_RECOVERY_MINS` (30 min) after.

**`prefetch_calendar()` → None** — Called at bot startup. Warms up FRED cache.

---

### `data_recorder.py` (~200 lines)
**Role:** Thread-safe JSONL recorder. Singleton `recorder` instance.

**Files:**
- `data/snapshots_YYYY-MM-DD.jsonl` — one record per `get_snapshot()` call (≥5s cadence). Excludes large text fields (`candles`, `dom_text`, `volume_profile`, `news_text`, `events_today`, `opening_range`, `htf_bias`, `market_structure`).
- `data/decisions_YYYY-MM-DD.jsonl` — one record per Claude API call + one `type="trade"` record per trade.

**`recorder.record_snapshot(snapshot)` → None** — Rate-limited to 1 per 5s.
**`recorder.record_decision(snapshot, raw_response, parsed_decision, model, cost_usd, pre_filter_reason)` → None** — Always writes.
**`recorder.flush_and_close()` → None** — Called at EOD.

**Decision record schema:**
```json
{"type": "decision", "ts": "ISO-UTC", "ts_et": "HH:MM",
 "snapshot": {...}, "raw_response": "...", "decision": {...},
 "model": "claude-opus-4-7", "cost_usd": 0.0042,
 "pre_filter_reason": "BULL 4 signals [...]", "bot_version": "4.1.0"}
```

**Trade record schema:**
```json
{"type": "trade", "ts": "ISO-UTC",
 "entry": 21345.50, "exit": 21370.25, "pnl": 49.50,
 "direction": "LONG", "mode": "SCALP", "reason": "TARGET HIT"}
```

---

### `strategy_stats.py` (~200+ lines)
**Role:** Per-strategy win rate and expectancy tracking. Optional module — graceful degradation if missing.

**Stats file:** `memory/strategy_stats.json`.

**`record_trade(strategy, mode, pnl, confluence_score)` → None**
**`generate_performance_context()` → str** — Returns formatted context string for Claude prompts. Only generates PRIORITIZE/REDUCE instructions after ≥20 trades per strategy (P1.4 Wilson lower-bound).

---

### `logger.py` (~50 lines)
**Role:** Rotating log file setup. Module-level `logger` instance.

**`log_daily_summary(trades, daily_pnl)` → None** — Writes formatted EOD summary to log.

**Log location:** `logs/` directory (rotating, not committed).

**C.4:** `_flush_log()` in `main.py` manually flushes all handlers after BUY/SELL/CLOSE.

---

### `notifier.py` (~150 lines, V4.3)
**Role:** iPhone push notifications via Pushover HTTP API. Best-effort, fail-safe — bot runs normally when keys absent.

**Env vars:**
- `PUSHOVER_USER_KEY` — user key from pushover.net
- `PUSHOVER_API_TOKEN` — app token
- `NOTIFY_ENABLED` (default `true`) — global kill switch

**Implementation notes:** Uses stdlib `urllib.request` (no extra deps). `_clean()` strips non-ASCII and truncates to 500 chars to avoid Pushover 400 errors. `notify()` returns `True` on success / `False` on disabled or failure; failures log to shared `logger` if importable, else stdout.

**Notification surface:**
| Function | Trigger | Priority |
|----------|---------|----------|
| `notify_premarket(summary)` | After `analyze_premarket` completes (`main.py`) | 0 |
| `notify_or_established(direction, high, low)` | First snapshot after OR forms | 0 |
| `notify_trade_entered(direction, entry, stop, target)` | `executor._enter_trade` post-fill | 1 |
| `notify_trade_exited(direction, entry, exit, pnl, reason)` | `executor._record_pnl` | 1 |
| `notify_stop_to_breakeven(direction, entry)` | `_auto_trail_long/short` when stop hits entry | 0 |
| `notify_consecutive_losses(count, daily_pnl)` | After exit when ≥3 consecutive losses | 1 |
| `notify_loss_warning(used, limit)` | After exit when `|daily_pnl| ≥ 0.9 × MAX_DAILY_LOSS_USD` | 1 |
| `notify_eod_summary(pnl, wins, losses, net_liq, version)` | `main.end_of_day` | 0 |
| `notify_bot_sleeping(wake_time)` / `notify_bot_awake()` | `_wait_for_market_hours` enter/exit | -1 / 0 |
| `notify_ibkr_disconnected()` / `notify_ibkr_reconnected()` | Wired to `feed.ib.disconnectedEvent` / `connectedEvent` | 1 / 0 |
| `notify_error(location, error)` | `main()` top-level except | 1 |
| `notify_backtest(date, pnl, wins, losses, wr)` | Backtester (manual) | -1 |
| `notify_learning_done(version, key_finding)` | After EOD learning session | -1 |

**Wired in:** `main.py` (premarket, OR, EOD, IBKR events, errors, sleep/wake), `executor.py` (entries, exits, stop→BE, loss warning, consecutive losses). Import is try-wrapped (`_notify_available` flag); missing module never breaks the bot.

---

### `demo.py` (~200+ lines)
**Role:** Standalone demo that populates dashboards without IBKR. No trading.

Writes synthetic `dashboard_data.json` and `price_data.json` with sample data for UI development/testing.

---

## HTML Files — Complete Reference

### `dashboard.html`
**Purpose:** Desktop browser trading dashboard. 3-column layout.

**Polls:** `dashboard_data.json` every 2 seconds, `price_data.json` implicitly via JS interval.

**Layout:**
- **Top bar:** Price (28px), bid/ask/spread, version badge, status pills (position, kill zone, AMD phase, OR direction), ET clock (Intl.DateTimeFormat).
- **Market status bar:** Session state, countdown timer, schedule slots color-coded by phase.
- **3-column main grid:**
  - Left (240px): Position display, P&L, entry/stop/target levels, OR high/low, session high/low, ICT levels (FVG, OB, liquidity, CHoCH).
  - Center: Decision badge (BUY/SELL/HOLD with color), thesis probability meter, reasoning block with `iso_ts` age display (greyed out if >5 min old), confluence score, strategy label, AMD phase, kill zone.
  - Right (360px): OR context, MTF alignment, HTF bias, delta trend, volume, VWAP, market structure.
- **Bottom row:** IBKR headlines (left), economic calendar (right).
- **Price chart:** 1-min/5-min toggle, VWAP curve, trade entry/exit markers, pan/zoom (canvas-based).

**Key element IDs updated by bot:**
`#price-main`, `#price-sub` (bid/ask), `#clock`, `#data-mode`, `#version-badge`,
`#position-display`, `#position-pnl`, `#entry-val`, `#stop-val`, `#target-val`,
`#decision-badge`, `#reasoning-block`, `#reasoning-age`, `#thesis-bias`,
`#confluence-score`, `#strategy-label`, `#amd-phase`, `#killzone`,
`#or-high`, `#or-low`, `#or-direction`, `#mtf-alignment`, `#htf-bias`,
`#delta-trend`, `#volume`, `#vwap`, `#market-structure`,
`#news-text`, `#ibkr-headlines`, `#session-high`, `#session-low`,
`#chart-canvas` (price chart).

**bot_sleeping field:** When `botSleeping: true` in JSON, dashboard shows a full-screen "BOT SLEEPING" overlay with `wakeTime` label and reason. Clears when bot wakes.

**Reads from:** `dashboard_data.json` (full state), `price_data.json` (1 Hz price).

---

### `mobile.html`
**Purpose:** iPhone-optimized trading dashboard. Add-to-home-screen via Tailscale.

**Polls:** `dashboard_data.json` every 5 seconds.

**Layout:** Single column, cards stacked vertically. Large text for readability.
- Top bar: price, clock, data mode.
- Market Status card: session state (color-coded), countdown.
- Position card: direction, P&L, entry/stop/target.
- Decision card: last Claude decision + confidence + reasoning (truncated).
- OR/Structure card: OR direction, MTF, CHoCH.
- News card: danger zone banner + next event.

**BOT OFFLINE indicator:** Red banner shown when `dashboard_data.json` hasn't updated in >30s.

**bot_sleeping display:** Shows "BOT SLEEPING" state with wake time when `botSleeping: true`.

**Key element IDs:** `#ms-state`, `#ms-countdown`, `#price`, `#clock`, `#data-mode`, `#pos-dir`, `#pos-pnl`, `#entry`, `#stop`, `#target`, `#decision`, `#reasoning`, `#or-dir`, `#mtf`, `#news-status`.

---

### `journal.html`
**Purpose:** Trade journal analytics dashboard. Sidebar navigation with multiple views.

**Polls:** `journal_data.json` (no auto-refresh — built at EOD by `journal_exporter.py`).

**Navigation pages (sidebar):**
- **Overview:** Equity curve chart (Chart.js line), 4 metric cards (total P&L, win rate, total trades, avg/trade), daily P&L bar chart.
- **Trades:** Filterable trade log table with date, direction, entry/exit, P&L, mode, exit reason. Filters by LONG/SHORT/WIN/LOSS/SCALP/SWING.
- **Analytics:** By-strategy breakdown table, by-hour performance heatmap, OFI signal performance table, thesis probability bucket performance table.

**Sidebar footer:** Account name, current equity, total P&L change.

**Key element IDs:** `#sidebar`, `#main`, `.page` (Overview/Trades/Analytics), `.metrics-grid`, `#equity-chart`, `#daily-chart`, `#trades-table`, `#strategy-table`, `#hour-table`, `#ofi-table`, `#thesis-table`, `.nav-item.active`.

**Chart.js dependency:** Loaded from CDN (`cdn.jsdelivr.net/npm/chart.js@4.4.0`).

**JetBrains Mono / Inter:** Loaded from Google Fonts.

---

## Feature Flags — All 15

Set in `.env` or toggled by `ablation_runner.py`. All default to `true`.

### Strategy / Bias
| Flag | Default | Controls |
|------|---------|---------|
| `FEATURE_ORB_BIAS` | true | Opening Range direction used as LONG_PREFERRED / SHORT_PREFERRED starting bias. If false, watchlist defaults to NEUTRAL. |
| `FEATURE_BIDIRECTIONAL` | true | Allow shorts on bull OR days and longs on bear OR days. If false, only trade in OR direction. |
| `FEATURE_BIAS_DECAY` | true | After 90 min, if MTF+CHoCH+price all disagree with OR bias → flip to NEUTRAL. If false, OR bias persists all day. |
| `FEATURE_DOJI_MTF_OVERRIDE` | true | On DOJI OR days (no clear direction), allow trades when MTF is `BULLISH_ALIGNED` or `BEARISH_ALIGNED` — requires 5+ pre-filter signals. (V4.2) |

### Predictive Signals
| Flag | Default | Controls |
|------|---------|---------|
| `FEATURE_OFI` | true | Order Flow Imbalance score from DOM history. Adds OFI signals to pre-filter scoring (+1 to +2 points). |
| `FEATURE_DOM_ADVANCED` | true | Iceberg / spoof / sweep / cluster detection from 20-level DOM history. Adds dom_sweep (+2), iceberg (+1), cluster (+1) signals. |
| `FEATURE_MTF_SCORE` | true | Numeric MTF alignment score (0–100, with bull_tfs/bear_tfs count). If false, `mtf_score` returns `{score: 0, ...}`. |
| `FEATURE_DELTA_LIVE` | true | True bid/ask delta classification via live tick stream (LIVE_DATA_ACTIVE mode only). If false, uses signed-volume approximation. |

### Entry Gates
| Flag | Default | Controls |
|------|---------|---------|
| `FEATURE_THESIS_GATE` | true | Block entries when `thesis_probability < MIN_THESIS_PROBABILITY` (70). If false, entries pass regardless of Claude's probability score. |
| `FEATURE_R_BUDGET` | true | Stop new entries once `session_r_spent >= MAX_SESSION_R_LOSS` (3.0R). If false, no R-budget cap. |
| `FEATURE_NEWS_GATE` | true | Hard block on entries during `news_danger_zone` (within 45 min of HIGH-impact event). If false, entries allowed near news. |
| `FEATURE_DEAD_ZONE` | true | Reduce entry threshold in dead zone (11am–1:30pm ET) to confluence ≥ 8. If false, normal threshold applies in dead zone. |

### Position Management
| Flag | Default | Controls |
|------|---------|---------|
| `FEATURE_DUAL_TRAIL` | true | Claude's TRAIL decisions set `_claude_trail_stop` floor; auto-trail never moves stop looser than this (D.2). If false, auto-trail moves freely. |
| `FEATURE_EARLY_EXIT` | true | Allow Claude to CLOSE positions early before stop/target. If false, positions only exit via stop/target or EOD. |

### Learning
| Flag | Default | Controls |
|------|---------|---------|
| `FEATURE_LEARNING_EOD` | true | Run ablation + learning session at EOD (4 PM ET). If false, EOD learning is skipped. |
| `FEATURE_LEARNING_INJECT` | true | Inject last 3 days' learning findings into pre-market prompt. If false, pre-market runs without learning context. |

**Safety note:** Stop loss enforcement, R-budget tracking (the tracking itself, not the gate), race-condition fixes (FIX 1–6), P&L sanity bound, and broker reconciliation are **always on** — not gated by any feature flag.

**`ACTIVE_FEATURE_SET`** (env var): "LIVE" during normal trading. Ablation runner sets this to the test name (e.g. "NO_OFI") so recorded decisions can be filtered by configuration.

---

## JSON Schemas

### `dashboard_data.json`

Written by `dashboard_writer.update_dashboard()`. Read by `dashboard.html` and `mobile.html` every 2–5 seconds.

```json
{
  // Meta
  "timestamp":      "2026-05-24T09:45:12.345-04:00",
  "time_et":        "09:45:12",
  "data_mode":      "LIVE L2 | DELAYED | BOT SLEEPING",
  "botVersion":     "4.1.2",
  "botSleeping":    false,
  "wakeTime":       "",

  // Position
  "position":       "LONG | SHORT | FLAT",
  "entryPrice":     21345.50,
  "stopPrice":      21295.50,
  "targetPrice":    21445.50,
  "currentPrice":   21370.25,
  "dailyPnl":       49.50,
  "maxLoss":        500.0,

  // Claude decision
  "claudeStatus":        "SCANNING — last: HOLD",
  "lastDecision":        "HOLD | BUY | SELL | CLOSE",
  "lastReasoning":       "string, max 500 chars",
  "lastConfidence":      "HIGH | MEDIUM | LOW",
  "lastStrategy":        "ORB_BREAKOUT | ORB_PULLBACK | ...",
  "lastConfluence":      "OR_BULL + SWEEP + CHOCH_BEAR + ...",
  "lastConfluenceScore": 7,
  "thesisProbability":   82,
  "lastThesisStatus":    "INTACT | WEAKENING | INVALIDATED",

  // Reasoning block with age
  "reasoning": {
    "time":       "09:45:12",
    "iso_ts":     "2026-05-24T09:45:12.345-04:00",
    "decision":   "HOLD",
    "confidence": "LOW",
    "reasoning":  "string"
  },

  // Market bias / context
  "bias":           "BULLISH | BEARISH | MIXED | NEUTRAL",
  "amdPhase":       "ACCUMULATION | MANIPULATION | DISTRIBUTION",
  "htfBias":        "string",
  "killzone":       "NY_AM_KZ | NY_PM_KZ | NO_KZ",
  "confluence":     ["FVG", "VWAP", "DELTA"],

  // Session levels
  "sessionLevels":  "string",
  "sessionHigh":    21500.0,
  "sessionLow":     21200.0,

  // ICT Levels (string, formatted for display)
  "fair_value_gaps":  "string",
  "order_blocks":     "string",
  "liquidity_pools":  "string",
  "choch":            "BULLISH_CHOCH | BEARISH_CHOCH | NEUTRAL",
  "inducement":       "string",
  "mtf_alignment":    "BULLISH_ALIGNED | CONFLICTED | PARTIAL_BULL | ...",
  "delta_trend":      "string",
  "market_structure": "string",

  // Price / order flow
  "bid":          21369.75,
  "ask":          21370.00,
  "volume":       45230,
  "vwap":         21355.50,
  "cumDelta":     1243,
  "deltaLastBar": 87,
  "candleText":   "string",

  // Opening Range
  "orHigh":             21400.0,
  "orLow":              21350.0,
  "orBrokenUp":         true,
  "orBrokenDown":       false,
  "orAttempts":         2,
  "or_direction":       "BULL | BEAR | DOJI | PENDING",
  "or_relative_volume": 142.5,

  // News
  "newsText":       "string",
  "newsDangerZone": false,
  "nextHighImpact": "Initial Jobless Claims @ 08:30",
  "nextEventFull":  "string",
  "newsEvents":     [{"time":"08:30","title":"...","impact":"HIGH"}],
  "ibkrHeadlines":  [{"time":"09:23","headline":"...","provider":"BZ"}],

  // Chart data
  "bars1min":       [{"t": "09:30", "o": 21300, "h": 21320, "l": 21295, "c": 21315, "v": 1200}],
  "bars5min":       [{"t": "09:30", "o": ..., ...}],
  "currentBarOpen": 21365.0,
  "tradeMarkers":   [{"t": "09:45", "price": 21345, "type": "entry", "dir": "LONG"}],

  // Account
  "account":    {"net_liquidation": 50049.50, "unrealized_pnl": 49.50, ...},
  "netLiq":     50049.50,
  "ibkrPnl":    0.0,
  "unrealized": 49.50,

  // Trades log
  "trades": [
    {"time": "09:45:12", "action": "SELL", "entry": 21345.50, "exit": 21445.50,
     "pnl": 100.0, "mode": "SCALP", "exit_reason": "TARGET HIT"}
  ],

  // Deprecated / unused
  "leftOnTable": []
}
```

`price_data.json` (lightweight, 1 Hz):
```json
{"t": "09:45:12", "price": 21370.25, "bid": 21369.75, "ask": 21370.00,
 "volume": 45230, "position": "LONG", "entry": 21345.50,
 "stop": 21295.50, "target": 21445.50, "pnl": 49.50,
 "netLiq": 50049.50, "unrealized": 49.50}
```

---

### `journal_data.json`

Written by `journal_exporter.run()` at EOD. Read by `journal.html` on page load.

```json
{
  "account":          "MNQ Paper ($50,000)",
  "starting_balance": 50000.0,
  "last_updated":     "2026-05-24T19:30:00+00:00",

  "equity_curve": [
    {"date": "2026-05-23", "equity": 50049.50, "daily_pnl": 49.50, "trades": 3}
  ],

  "by_strategy": {
    "SCALP": {"trades": 5, "wins": 3, "losses": 2, "pnl": 87.50, "win_rate": 60.0},
    "SWING": {"trades": 2, "wins": 1, "losses": 1, "pnl": -12.50, "win_rate": 50.0}
  },

  "by_hour": {
    "9":  {"trades": 3, "wins": 2, "losses": 1, "pnl": 75.00, "win_rate": 66.7},
    "10": {"trades": 2, "wins": 1, "losses": 1, "pnl": 0.00,  "win_rate": 50.0}
  },

  "daily_pnl": [
    {"date": "2026-05-23", "pnl": 49.50, "trades": 3, "wins": 2, "losses": 1}
  ],

  "ofi_performance": {
    "STRONG_BUY":  {"trades": 3, "wins": 2, "losses": 1, "pnl": 75.0, "win_rate": 66.7},
    "BUY":         {"trades": 2, "wins": 1, "losses": 1, "pnl": 25.0, "win_rate": 50.0},
    "NEUTRAL":     {"trades": 1, "wins": 0, "losses": 1, "pnl": -25.0, "win_rate": 0.0},
    "SELL":        {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,  "win_rate": 0.0},
    "STRONG_SELL": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,  "win_rate": 0.0}
  },

  "thesis_buckets": {
    "70-75": {"trades": 1, "wins": 0, "losses": 1, "pnl": -25.0, "win_rate": 0.0},
    "75-80": {"trades": 2, "wins": 1, "losses": 1, "pnl": 25.0,  "win_rate": 50.0},
    "80-85": {"trades": 2, "wins": 2, "losses": 0, "pnl": 100.0, "win_rate": 100.0},
    "85+":   {"trades": 1, "wins": 0, "losses": 1, "pnl": -25.0, "win_rate": 0.0}
  }
}
```

**Source records:** `data/decisions_YYYY-MM-DD.jsonl` records with `type="trade"` and `type="decision"`. `_match_decision()` links trade records to their triggering decision by timestamp proximity.

---

## Recent Changes (This Session)

### 0. V4.3 — Push notifications (commits `d018fab`, `a41ee18`, `cb7b5e5`)
Added `notifier.py` (Pushover HTTP). Wired into `main.py` (premarket, OR established, EOD summary, IBKR connect/disconnect, errors, bot sleeping/awake) and `executor.py` (trade entered/exited, stop→breakeven, consecutive losses, loss warning). Env vars: `PUSHOVER_USER_KEY`, `PUSHOVER_API_TOKEN`, `NOTIFY_ENABLED`. Import is try-wrapped — bot runs normally without keys. `notify()` returns `True` on success.

### 0a. V4.3 — Calibration & UI
- R:R minimum raised from 2:1 to 3:1 in `SYSTEM_PROMPT` (commit `5b7a72c`)
- `PROBABILITY_CONTEXT` knowledge base added and injected into Opus prompts to anchor THESIS_PROBABILITY (commit `ad44000`)
- `KNOWLEDGE_BASE.md` added and referenced in CLAUDE.md (commit `de9749a`)
- Profitability matrix + R:R sensitivity analysis added to journal (commits `e3aec72`, `4964068`)
- Navy color scheme applied across `dashboard.html`, `mobile.html`, `journal.html`

### 0b. V4.2 — Snapshot enrichment
- `_detect_candle_patterns()` in `ibkr_feed.py` → `candle_patterns` field on snapshot (engulfing, hammer, shooting star, morning/evening star, inside-bar breakout, on 1m + 5m)
- `_get_tape_analysis()` → `tape_bias` (AGGRESSIVE_BUYING/SELLING/NEUTRAL) + `tape_analysis` dict (large_print_count_60s, pressure stats, recent_large_prints). Pre-filter adds ±2 signals.
- `_find_daily_zones()` → `daily_zones` dict (demand/supply zones from daily bar reversals). Pre-filter adds +1 bull near demand, +1 bear near supply.
- `premarket_high` / `premarket_low` (4am–9am ET globex extremes) computed in `_update_session_levels()`. Pre-filter adds 4 signals.
- `FEATURE_DOJI_MTF_OVERRIDE` flag: on DOJI OR days, allow trades when MTF aligned and 5+ signals fire.

### 1. Git automation removal (commit `1df1ea0`)
**Problem:** `version_manager.py`, `learning_session.py`, and `ablation_runner.py` had `subprocess` git calls (add/commit/push/tag) that ran at EOD. These were fragile (SSH keys, network, dirty working tree).

**Change:** All git subprocess calls removed. `version_manager.eod_commit()` now only bumps `BOT_VERSION` in `.env`. No git operations happen automatically. `learning_session.run_learning_session()` parameter `auto_commit` was removed.

**Side effect (known bug):** `main.py:694` still passes `auto_commit=True` to `run_learning_session()`, which no longer accepts that parameter. This will raise `TypeError` at EOD when `FEATURE_LEARNING_EOD=true`. **Fix needed:** Remove `auto_commit=True` from the call in `main.py`.

### 2. Trading hours gate (commit `0fd261a`)
**Added to `main.py`:**
- `is_trading_hours(now_et)` — returns False on weekends, holidays, after early close
- `_wait_for_market_hours()` — sleeps until 08:20 ET. Loops in 30-min ticks off-hours, 60s on trading-day mornings. Writes `bot_sleeping=True` to dashboard during sleep.
- `_get_cme_calendar()` — lazy-loads XNYS calendar from `exchange_calendars`
- Boot sequence now calls `_wait_for_market_hours()` before IBKR connect

**Dependency added:** `exchange_calendars` — `pip install exchange-calendars`. Uses XNYS (NYSE) calendar since MNQ follows NYSE holiday schedule (not CME agricultural calendar).

**Fail-open behavior:** If `exchange_calendars` unavailable, `is_trading_hours` returns True (bot proceeds). Only weekends are always blocked (weekday check is native Python).

### 3. Market holiday and early close support (commits `081937a`, `f7990ca`)
**Added to `main.py`:**
- `_is_cme_session(date_et)` — queries `cal.is_session()` for the date
- `_cme_early_close_time_et(date_et)` — checks `cal.early_closes`, converts close time to ET HHMM int
- `_next_session_label(date_et)` — uses `cal.sessions_in_range()` for accurate next trading day
- `_wait_for_market_hours()` now logs distinct reasons: "Weekend", "Market holiday", "Early close"
- Bugfix: early close comparison was checking time-of-day in minutes correctly

### 4. Dashboard sleeping state fields (commit `c1ecdbc`)
**Added to `dashboard_writer.update_dashboard()`:**
- `bot_sleeping: bool = False` — new parameter
- `wake_time: str = ""` — new parameter
- `data_mode` field: "BOT SLEEPING" when `bot_sleeping=True`
- `botSleeping` and `wakeTime` written to JSON

**Dashboard HTML updated:** `dashboard.html` and `mobile.html` show sleeping overlay / status when `botSleeping: true`.

### 5. Trading journal (commit `014f0b8`)
**New files:**
- `journal.html` — multi-page analytics dashboard with equity curve, trade log, per-strategy/hour/OFI/thesis stats
- `journal_exporter.py` — builds `journal_data.json` from all `decisions_*.jsonl` files

**Integration:** `learning_session.run_learning_session()` calls `journal_exporter.run()` at EOD (step 7 in the run sequence).

**`journal_data.json`** not ignored by `.gitignore` — user should decide whether to commit it.

---

## Current Known Bugs and Pending Fixes

### Critical (will cause runtime error)
1. **`main.py:694` — `auto_commit=True` kwarg** — `run_learning_session()` was refactored to remove `auto_commit` parameter, but `main.py` still passes it. Raises `TypeError` at EOD when `FEATURE_LEARNING_EOD=true`. **Fix:** Remove `auto_commit=True` from the call at line 694.

### Design / behavioral issues
2. **`data_recorder.py` `BOT_VERSION = "3.0"`** — Hardcoded at module level. Should read from `config.VERSION`. Recorded decisions are tagged with "3.0" regardless of actual version.

3. **`learning_session.run_learning_session` double-saves to `reports/` and `memory/`** — Reports written to `reports/learning_*.md` AND `memory/learning_*.md`. The `reports/` folder is listed in `.gitignore` (so reports are NOT committed automatically). But `load_learning_for_premarket()` reads from `memory/`, which is also in `.gitignore` — so on a fresh clone, no learning history is available.

4. **`journal_data.json` location** — Written to `BASE_DIR` root (same as dashboard JSONs). Not in `.gitignore`, so it may accumulate in the working tree. No auto-clean at boot (unlike `dashboard_data.json`).

5. **`strategy_stats.py` — optional import failure logged at INFO not WARNING** — If `strategy_stats` is missing, `_get_perf_ctx = None` is set silently. Should log once at startup.

6. **Dashboard early-close display** — `dashboard.html` / `mobile.html` market status bar doesn't account for early close. The status bar shows "RTH CLOSE @ 16:00" regardless of actual close time on early-close days.

---

## Audit Tag Reference

Tags like `P1.3`, `A.1`, `D.2` in comments are references to a private audit document. They mean **"do not undo this — there is a known reason."**

| Tag | Location | Meaning |
|-----|----------|---------|
| P1.1 | `executor._get_market_price()` | No blocking IBKR calls inside executor lock |
| P1.2 | `executor._enter_trade()` | `outsideRth=True` on all orders — futures trade 24/5 |
| P1.3 | `executor.entry_timestamp` | Owned by executor, set on fill. No module global. No IBKR sync race. |
| P1.6 | `dashboard_writer` reasoning block | `iso_ts` in reasoning block so dashboard can compute age |
| P1.7 | `claude_brain.parse_decision()` | BUY/SELL → HOLD if `stop_price ≤ 0` |
| P2.5 | `executor._close_position()` | Pre-flight + post-cancel broker checks |
| P2.8 | `main.end_of_day()` | Calls `reset_session_state()` to wipe day's module globals |
| A.1 | `claude_brain._maybe_skip_call()` | Skip Opus if conditions unchanged (60–70% cost reduction) |
| A.2 | `claude_brain._build_user_content()` | Cache static blocks, don't cache dynamic snapshot |
| A.3 | `claude_brain._log_cache_usage()` | Cost tracking per model/purpose |
| B.1 | `ibkr_feed.maybe_persist_tick_state()` | Save tick state every 30s |
| B.2 | `ibkr_feed.restore_tick_state()` | Restore tick state on startup if same day |
| C.3 | `main.run_premarket()` | Build watchlist before pre-market so Claude has game plan |
| C.4 | `main._flush_log()` | Force flush after BUY/SELL/CLOSE |
| C.6 | `main.main()` | Delete stale dashboard JSON on boot |
| D.1 | `executor._safety_checks()` | R-budget gate — stop entries after MAX_SESSION_R_LOSS |
| D.2 | `executor._auto_trail_long/short()` | Claude's TRAIL stop floors auto-trail |
| FIX 1+2 | `executor._close_position()` | Race-safe broker sync |
| FIX 3 | `executor._check_stop_and_target()` | Never act on `stop_price ≤ 0` |
| FIX 4 | `executor._fast_protection_loop()` | Periodic broker reconciliation |
| FIX 5 | `executor._post_close_orphan_check_safe()` | Orphan check on caller thread, not background |
| FIX 6 | `executor._record_pnl()` | $1000/contract P&L sanity bound |

---

## Configuration Reference

### `.env` file location
`{BASE_DIR}/.env` — loaded by `python-dotenv` at import. Defaults are in `config.py`. Copy `env.example` to `.env`.

**Minimum required fields:**
```
ANTHROPIC_API_KEY=sk-ant-...
BASE_DIR=C:\trading\mnq-ai-trader
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
```

**Must update quarterly (contract roll):**
```
CONTRACT_EXPIRY=20260918   # next roll: Sep 2026
CONTRACT_CONID=770561201   # get from IBKR contract search
```

### `.gitignore` — what is NOT committed
```
env, .env, *.key          # secrets
dashboard_data.json       # generated at runtime
price_data.json
tick_state.json
data/                     # JSONL recordings
memory/                   # session summaries, tick state
logs/                     # rotating log files
reports/                  # ablation + learning reports  ← also git-ignored
*.log, *.csv
__pycache__/, *.pyc
.DS_Store, Thumbs.db
```

**Note on `reports/`:** Reports generated by `learning_session.py` are git-ignored. An earlier commit (`2f3a9b8`) reversed this but the current `.gitignore` puts them back as ignored. Pre-market learning injection reads from `memory/`, not `reports/`.

---

## Dependencies

```
ib_insync          pip install ib_insync          # IBKR connection, orders
anthropic          pip install anthropic           # Claude API
pandas             pip install pandas             # bar data
pytz               pip install pytz               # timezone handling
python-dotenv      pip install python-dotenv      # .env loading
schedule           pip install schedule           # EOD scheduler
exchange_calendars pip install exchange-calendars # CME holidays/early close
```

requirements.txt is committed. Install with: pip install -r requirements.txt

---

## Running

```bash
# Live bot (boots at 8:20 ET after waiting for market hours)
py -3.11 main.py

# Backtest a recorded session
py -3.11 backtester.py --list
py -3.11 backtester.py --date 2026-05-27
py -3.11 backtester.py --date 2026-05-27 --verbose
py -3.11 backtester.py --date 2026-05-27 --no-live-claude

# EOD learning session (manual)
py -3.11 learning_session.py --date 2026-05-27

# Ablation test (manual)
py -3.11 ablation_runner.py --date 2026-05-27

# Export journal data
py -3.11 journal_exporter.py

# Sanity-print config
py -3.11 config.py

# Version management
py -3.11 version_manager.py --show
py -3.11 version_manager.py --bump minor

# Serve dashboards locally
py -3.11 -m http.server 8080
# then open: http://localhost:8080/dashboard.html
#            http://localhost:8080/mobile.html
#            http://localhost:8080/journal.html

# Demo mode (no IBKR, no Claude)
py -3.11 demo.py
```

---

## CLAUDE.md Additions Not Covered Above

### What the backtester validates
Pre-filter logic, prompt structure, snapshot schema changes. Run `--date <recent>` before and after changes. P&L / W-L delta is the validation signal. Old recordings won't have new snapshot fields (will be `None`/missing on replay).

### Bidirectional OR bias (V3.0) — hard rule
Do not reintroduce "LONG_ONLY" logic that blocks one side entirely. The bot must be able to short on bull days and long on bear days when structure demands it. Pre-filter requires 3+ signals with bias, 5+ counter-trend.

### Contract rolls
MNQ rolls quarterly (Mar/Jun/Sep/Dec). Update `CONTRACT_EXPIRY` and `CONTRACT_CONID` in `.env` or IBKR will reject orders. If a session won't connect, check expiry first.

### `reports/` folder
Currently git-ignored. The ablation runner and learning session write here. If you want learning reports committed, remove `reports/` from `.gitignore`. The pre-market injection reads from `memory/`, not `reports/`, so this doesn't affect learning injection.

### Risk change confirmation
Any change that loosens risk caps, daily loss limits, or hold-time gates must be flagged for explicit user confirmation. The CLAUDE.md `## Permissions` section grants Claude full autonomy for read/edit/create/delete but the risk-cap constraint overrides it.

### Session continuity (B.2)
`restore_tick_state()` restores `tick_delta` and `volume_profile` from `memory/tick_state.json` if the same trading day. Bot can restart mid-session without losing cumulative delta history.

---

*End of PROJECT_SUMMARY.md*
