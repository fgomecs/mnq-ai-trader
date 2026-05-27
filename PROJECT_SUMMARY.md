# MNQ AI Trader — Complete Project Summary
*For AI reading this cold. Dense, accurate, no padding. Last verified: 2026-05-27 (V4.5.0).*

**Companion docs:** `CHANGELOG.md` (versioned changelog), `CLAUDE.md` (AI-assistant guidance + audit-tag reference), `README.md` (user-facing intro, install, run), `TEST_PLAN.md` (test roadmap, Phase 1-3 complete), `KNOWLEDGE_BASE.md` (academic research consumed by `claude_brain.py` prompts), `ROADMAP.md` (completed + deferred features).

---

## What This Is

Paper-trading bot for **MNQ (Micro E-mini Nasdaq-100)** futures. Pulls live L1+L2 data from IBKR (TWS/Gateway) via `ib_async`, scores market structure with pure Python signal pre-filters, sends snapshots to Claude for entry + position decisions, and executes bracket orders. Hard constraints: $50K simulated account, `MAX_CONTRACTS` env-driven (default 1, user runs 4), daily loss cap (MAX_DAILY_LOSS_PCT × ACCOUNT_SIZE, default $10,000). **Not live money.**

**Strategy:** ICT (Inner Circle Trader) methodology. Opening Range Breakout with pullback entry. CHoCH (Change of Character) confirmation. Dual-sided bias — OR direction is a starting preference, not a law. Kill zones (NY AM 8:30–11, NY PM 1:30–4 ET). Session type classifier (TREND/RANGE/NEWS/HOLIDAY/UNKNOWN) routes strategy and thresholds.

**Current operational config** (per `.env`): `CLAUDE_ENTRY_MODEL=claude-sonnet-4-6`, `CLAUDE_POSITION_MODEL=claude-sonnet-4-6` (cost-optimized), `MAX_CONTRACTS=4`, `MIN_THESIS_PROBABILITY=55`, `FEATURE_DEAD_ZONE=false`.

**Performance reference** — 2026-05-26 session (OB_BOUNCE strategy, end-to-end broker-commission capture): 8 trades, 4W / 4L, **+$55.36 net P&L** (after $9.30 broker commission), 50% win rate.

**Version as of this document:** 4.5.0. Added: real broker `commissionReportEvent` capture (dedupe + reconnect-safe), trade JSONL persistence end-to-end, dashboard + journal commission breakdown, **139-test pytest suite** (29% line coverage), migration `ib_insync → ib_async`, DOM to 40 levels, 1-second real-time bars, backtester bias seeding + safe `--no-live-claude` default, pre-market dashboard reset, `EOD_SCHEDULE_TIME` → 15:55, `load_dotenv` absolute-path fix. Risk caps unchanged. Full diff in `CHANGELOG.md`.

---

## Architecture

### Three concurrent threads

| Thread | Location | Cadence | Job |
|--------|----------|---------|-----|
| Main loop | `main.run_cycle()` | 0.5s sleep; pre-filter gated to 5s | snapshot → pre-filter → Claude → executor |
| Protection loop | `Executor._fast_protection_loop` daemon | 5s | stop/target check, broker reconciliation, orphan detection |
| Dashboard ticker | `main._fast_dashboard_ticker` daemon | 1 Hz | writes `price_data.json`, patches `dashboard_data.json` every 10s |

Plus: **pre-market sleep** (`_wait_for_market_hours()`) blocks `main()` before IBKR connect. 30-min poll during weekends/holidays/early closes; 60s poll on pre-market mornings. Writes `botSleeping=true` to dashboard.

### Data flow per cycle

```
ibkr_feed.get_snapshot()               → snapshot dict (~65 fields)
  → claude_brain.pre_filter_signal()   → (worth_calling: bool, reason: str)
    → session_classifier threshold routing (RANGE=7, TREND=3, UNKNOWN=5)
    → claude_brain.analyze_market()    → decision dict  [Opus 4.7, skip-cache A.1]
      → executor.execute()             → bracket order (market + stop + limit)
        → dashboard_writer.update_dashboard()   → dashboard_data.json
        → data_recorder.record_snapshot/decision() → data/YYYY-MM-DD.jsonl
```

### Shared state warning
`Executor` instance shared across all three threads. Internal `_lock` guards all mutation. **Never call IBKR APIs inside the lock** (P1.1 fix — `_get_market_price()` reads `_last_price` outside the lock to avoid blocking the protection thread).

---

## Python Files — Complete Reference

### `main.py` (~1166 lines)
**Role:** Session state machine and orchestration entry point.

**Key enums/functions:**

| Symbol | Purpose |
|--------|---------|
| `SessionState` (enum) | PRE_SESSION, PRE_MARKET, OR_FORMING, OR_ESTABLISHED, PRIME_WINDOW, DEAD_ZONE, AFTERNOON_PRIME, CLOSING, AFTER_HOURS |
| `get_session_state(now_et)` | Maps `HHMM` integer to `SessionState`. All thresholds from `config.py`. |
| `can_enter(state, confluence_score, snapshot=None)` | Returns `(bool, reason)`. Checks HOLIDAY block first. Dead zone requires score ≥ `DEAD_ZONE_CONFLUENCE_THRESHOLD` (8) or VWAP magnet override. FEATURE_DEAD_ZONE=false disables restriction. |
| `_should_call_claude_now(executor, snapshot)` | Event-driven position trigger. Returns `(bool, reason)` on: adverse move ≥ 10t, delta flip, stop proximity ≤ 30t, target proximity ≤ 20t, giveback ≥ 30t from ≥40t peak, structure pullback. |
| `_reset_position_tracking()` | Clears `_last_position_price`, `_peak_profit_ticks`, etc. |
| `run_premarket(feed)` | Called once at 8:30 ET. Loads 5-day memory, calls `analyze_premarket`. Sets `premarket_done=True`. |
| `run_cycle(feed, executor)` | Main loop body. Session gating → snapshot → session classifier (fires once at OR_ESTABLISHED) → watchlist refresh → in-position check or entry scan → dashboard update. |
| `end_of_day(feed, executor)` | Cancels orders, saves daily summary, calls `reset_session_state()` (P2.8), flushes recorder, resets `_session_type_classified`, optionally runs EOD learning session. |
| `_fast_dashboard_ticker(feed, executor)` | 1 Hz daemon. Polls IBKR ticker; synthesizes bid/ask if delayed mode. Uses tick counters (not time.time()%N) for account refresh and live patch cadence. |
| `_patch_dashboard_live(feed, executor, price, account)` | Writes OR data, session state, P&L to dashboard every 10s independent of Claude. |
| `is_trading_hours(now_et)` | Returns False on Saturday, Sunday before 18:00 ET, CME holidays, and after early-close time. Fail-open. |
| `_wait_for_market_hours()` | Blocks until 08:20 ET on a trading day. Loops in 30-min ticks on weekends/holidays, 60s ticks on pre-market mornings. Writes `bot_sleeping=True` to dashboard. |
| `main()` | Boot sequence: delete stale `dashboard_data.json` (C.6), `_wait_for_market_hours()`, connect IBKR, `initialize_bars()`, `restore_tick_state()`, start protection loop, start dashboard ticker, build watchlist, schedule EOD at 16:05. |

**Module-level state (not persisted across days):**
- `last_analysis_time`, `last_position_time`, `last_watchlist_time` — interval trackers
- `premarket_done` — gate for `run_premarket`
- `analysis_log` — list of entry scan records, cleared at EOD
- `_last_position_price`, `_last_position_delta`, `_peak_profit_ticks` — event trigger tracking
- `_fast_ticker_running`, `_last_snapshot_lock`, `_last_snapshot` — ticker state
- `_cme_calendar` — lazy-loaded exchange_calendars instance
- `_session_type_classified` — True once session_classifier fires for the day
- `_post_news_analyzed` — True once post-news watchlist refresh fires
- `_ticker_account_counter`, `_ticker_patch_counter` — tick counters for dashboard cadence

**C.6:** On `main()` boot, `dashboard_data.json` is deleted so stale EOD reasoning doesn't persist.
**C.4:** `_flush_log()` called after every BUY/SELL/CLOSE to ensure entries land on disk before crash.
**P1.3:** `_position_entry_time` global removed. Use `executor.entry_timestamp` instead.

**Imports from:** `config`, `logger`, `news_calendar`, `ibkr_feed`, `claude_brain`, `executor`, `session_classifier`, `memory_manager`, `dashboard_writer`, `data_recorder`, `learning_session` (lazy at EOD).

---

### `claude_brain.py` (~1928 lines)
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
| `update_watchlist(snapshot)` | Sonnet call every 5min. Returns structured watchlist JSON with bias, setups, levels. V4.4: receives session type context string. Applies V3.0 bias validation rules (90min decay, 80pt invalidation, DOJI→NO_TRADE, MTF override). |
| `get_watchlist()` | Returns `_session_watchlist` dict. |
| `pre_filter_signal(snapshot)` | Pure Python. Scores 25+ bull and bear signals (V4.4 adds: gap fill, pivot R2/S2, candle patterns at OB/FVG, OR extreme fade, VWAP reversion, sweep reversal, opening drive, post-news). Routes threshold by session type. Returns `(bool, reason)`. Never calls API. |
| `analyze_market(snapshot)` | Opus entry call. Prepends session type context (`sctx`). Checks skip-cache (A.1) first. Builds static (watchlist+stable context) and dynamic (snapshot+volatile) blocks. Records to backtest files. |
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
- Dynamic block: snapshot + volatile session bits + perf context → uncached (changes every call)
- Session type context (`sctx`) prepended to dynamic block — always fresh

---

### `ibkr_feed.py` (~2467 lines)
**Role:** IBKR market data connection and snapshot assembly.

**Snapshot fields (~65 total):**

Core price: `last_price`, `bid`, `ask`, `bid_size`, `ask_size`, `volume`, `session_high`, `session_low`, `vwap`

ICT: `htf_bias`, `market_structure`, `choch`, `inducement`, `fair_value_gaps`, `fvg_levels`, `order_blocks`, `ob_levels`, `liquidity_pools`, `liq_levels`, `session_levels`, `daily_zones`

OR: `opening_range`, `or_high`, `or_low`, `or_open`, `or_close`, `or_direction`, `or_broken_up`, `or_broken_down`, `or_break_attempts`, `or_relative_volume`, `or_volume`, `mins_since_or`, `or_breakout_candle_low`, `or_pullback_in_progress`, `or_pullback_low`, `or_entry_zone_active`

MTF/OFI: `mtf_alignment`, `mtf_score`, `ofi`, `delta_trend`, `cumulative_delta`, `delta_last_bar`, `delta_is_live`

DOM: `dom` (text), `dom_available`, `dom_resistance_wall`, `dom_support_wall`, `dom_buy_pressure`, `dom_imbalance`, `dom_vacuum_above`, `dom_vacuum_below`, `dom_nearest_magnet`, `dom_cluster_above`, `dom_cluster_below`, `dom_iceberg_ask`, `dom_iceberg_bid`, `dom_spoof_ask`, `dom_spoof_bid`, `dom_sweep_up`, `dom_sweep_down`

Volume profile: `volume_profile`, `vp_poc`, `vp_vah`, `vp_val`, `vp_status`, `vp_above_vah`, `vp_below_val`, `vp_inside_va`

Tape: `tape_analysis`, `tape_bias`, `tape_text`

News: `news_text`, `news_danger_zone`, `next_high_impact`, `next_event_full`, `next_event_minutes`, `recent_event`, `events_today`, `ibkr_headlines`, `ibkr_headlines_text`

Session context: `time_et`, `session_phase`, `killzone`, `amd_phase`, `data_mode`, `current_position`, `daily_pnl`, `daily_loss_remaining`, `consecutive_losses`

Bar data: `candles`, `candle_patterns`, `bars_1min`, `bars_5min`, `currentBarOpen`

Pre-market: `premarket_high`, `premarket_low`

**V4.4 fields:**
- `gap` — dict: `{gap_size, gap_direction, gap_fill_probability}`. Computed at each snapshot.
- `pivots` — dict: `{pivot, r1, r2, s1, s2}`. Cached per daily bar, not recomputed each snapshot.
- `first_candle_1min_high/low` — float: 9:30 ET 1-min bar H/L. Captured at bar close.
- `first_candle_5min_high/low` — float: derived from 9:30–9:34 1-min bars at 9:34 close.
- `vwap_extension` — signed pts from VWAP. `vwap_extension_abs` — absolute value.
- `or_2x_extension_up/down` — bool: price beyond OR range × `OR_EXTREME_FADE_MULTIPLIER`.
- `or_extreme_zone` — bool: either extension flag is True.
- `opening_drive_up/down` — bool: first 5-min candle ≥ `OPENING_DRIVE_MIN_POINTS`.
- `opening_drive_fade_short/long` — bool: drive + rejection wick ≥ `OPENING_DRIVE_REJECTION_PCT` of body.
- `post_news_window` — bool: `POST_NEWS_WINDOW_MINUTES` to `+POST_NEWS_WINDOW_DURATION` after HIGH-impact event.

**Key methods:**

| Method | Notes |
|--------|-------|
| `connect()` | Connects to IBKR Gateway. Sets up contract via conId or symbol fallback. Starts tick stream + DOM stream. |
| `initialize_bars()` | One-time historical fetch at startup. Starts `reqRealTimeBars` subscription. Refreshes ICT levels. |
| `_start_realtime_bars()` | Accumulates 5-sec RT bars into 1-min cache. Fires `_refresh_ict_levels()` every 5-min bar. Captures first candle levels at 9:30/9:34. |
| `_refresh_ict_levels()` | Recomputes FVGs, OBs, CHoCH, MTF, HTF bias, delta trend, OFI, daily zones from cached bars. Writes to `_ict_cache`. |
| `get_snapshot(...)` | Assembles full snapshot dict. Uses cached data — no blocking bar fetches. Target <1s. |
| `_compute_dom_signals()` | Full 20-level DOM analysis. Iceberg/spoof/sweep/cluster detection from `_dom_history`. |
| `_compute_ofi()` | OFI from DOM history. Acceleration, divergence from price. |
| `_compute_volume_profile(price)` | POC, VAH, VAL from live tick-level histogram. |
| `_compute_gap()` | Gap size/direction/probability from daily bars. |
| `_compute_pivot_points()` | R1/R2/S1/S2 from prior daily bar. Cached per bar. |
| `_calculate_opening_range(bars, now_et)` | 9:30–9:45 ET 3-bar OR. Direction: BULL/BEAR/DOJI. Relative volume vs 14-day avg. |
| `_update_or_pullback_tracking()` | 3-stage OR pullback tracker: breakout → pullback → entry zone. |
| `restore_tick_state()` | B.2: Restores `tick_delta` + `volume_profile` from `memory/tick_state.json` if same trading day. |
| `maybe_persist_tick_state()` | B.1: Saves tick state every 30s. |

**Note on opening drive (Phase 3):** When `FEATURE_OPENING_DRIVE_FADE=true`, uses the 9:30 5-min bar (found by date+hour+minute match in `_bars_5min`, not `_bars_5min[0]` which is the oldest cached bar) for body/wick calculations.

---

### `executor.py` (~1004 lines)
**Role:** Order placement, position tracking, protection loop.

**Key attributes:**
- `current_position` — int: +1 long, -1 short, 0 flat
- `entry_price`, `stop_price`, `target_price` — floats
- `entry_timestamp` — epoch time of actual fill (P1.3 — set on fill, not on order submit)
- `trade_mode` — "SCALP" or "SWING" from Claude's decision
- `daily_pnl`, `trades_today`, `consecutive_losses` — session accumulators
- `daily_loss_remaining` — decrements toward 0; entries blocked when ≤ 0
- `_claude_trail_stop` — D.2: Claude's last explicit stop level (floors auto-trail)
- `_last_price` — cached price for reads outside the lock (P1.1)

**Key methods:**

| Method | Notes |
|--------|-------|
| `execute(decision)` | Routes BUY/SELL → `_enter_trade`, CLOSE → `_close_position`, TRAIL → trail stop update. |
| `_enter_trade(decision)` | Safety checks, R-budget, LIMIT vs MARKET mode, bracket order. Sets `entry_timestamp` on fill. |
| `_close_position(price, reason)` | Race-safe close: broker position check before + after cancel. |
| `_fast_protection_loop()` | Daemon. Every 5s: check stop/target, reconcile broker, orphan detection. |
| `_check_stop_and_target()` | Never acts on `stop_price ≤ 0` (FIX 3). |
| `_auto_trail_long/short()` | Milestone-based auto-trail. Never loosens past `_claude_trail_stop` (D.2). |
| `_safety_checks(decision)` | R-budget gate, daily loss cap, max contracts. |
| `update_position_from_ibkr()` | Pulls broker position for reconciliation. |
| `start_protection_loop()` | Starts daemon thread. |

---

### `session_classifier.py` (~173 lines)
**Role:** Day-type classification. Pure Python — no API calls.

**SessionType class:** `TREND / RANGE / NEWS / HOLIDAY / UNKNOWN` (string constants).

**Module-level state:**
- `_current` — string, defaults to `UNKNOWN`. Set once per day at OR_ESTABLISHED. Reset to `UNKNOWN` at EOD.

**Key functions:**

| Function | Notes |
|----------|-------|
| `classify_session_type(snapshot, or_range, avg_volume_20d)` | Priority: HOLIDAY (volume) → NEWS (gap/danger) → TREND (range+MTF+vol) → RANGE (range/DOJI/conflicted) → UNKNOWN. |
| `set_session_type(t)` | Called by `main.run_cycle()` once at OR_ESTABLISHED. |
| `get_current_session_type()` | Called by `claude_brain.pre_filter_signal()` and `analyze_market()`. |
| `get_session_type_context(t)` | Returns one-paragraph string injected into every Claude prompt. |

**Classification rules (checked in order):**
1. HOLIDAY: `volume < avg_volume_20d × 0.50` (requires avg_volume_20d > 0)
2. NEWS: `gap_size ≥ SESSION_CLASSIFIER_NEWS_GAP_MIN` (100pts) OR `news_danger_zone=True`
3. TREND: `or_range ≥ 50pts` AND MTF in `(BULLISH_ALIGNED, BEARISH_ALIGNED)` AND `rel_vol ≥ 0.90`
4. RANGE: `or_range ≤ 35pts` OR `or_direction == "DOJI"` OR `mtf == "CONFLICTED"`
5. UNKNOWN: fallback

---

### `config.py` (~387 lines)
**Role:** All configuration constants.

26 feature flags, ~120 typed constants across 20+ sections. All env-overridable via `.env`.

**V4.4 additions:**
- Session classifier thresholds: `SESSION_CLASSIFIER_TREND_OR_MIN=50`, `SESSION_CLASSIFIER_RANGE_OR_MAX=35`, `SESSION_CLASSIFIER_NEWS_GAP_MIN=100`, `SESSION_RANGE_SIGNAL_THRESHOLD=7`, `SESSION_NEWS_THESIS_GATE=80`
- Gap: `GAP_SMALL_THRESHOLD=63`, `GAP_MEDIUM_THRESHOLD=147`, `GAP_LARGE_THRESHOLD=210`
- Phase 2: `VWAP_REVERSION_MIN_EXTENSION=80`, `OR_EXTREME_FADE_MULTIPLIER=2.0`, `DEAD_ZONE_VWAP_MAGNET_MIN_EXT=60`, `DEAD_ZONE_VWAP_MAGNET_THRESHOLD=6`
- Phase 3: `OPENING_DRIVE_MIN_POINTS=80`, `OPENING_DRIVE_REJECTION_PCT=0.60`, `POST_NEWS_WINDOW_MINUTES=45`, `POST_NEWS_WINDOW_DURATION=30`

**Session timing (config.py defaults vs .env overrides):**
```
SESSION_AFTERNOON_PRIME_END  config default: 1530  |  .env override: 1555
EOD_SCHEDULE_TIME            config default: 15:30  |  .env override: 16:05
```
All other session times match their config defaults.

---

### `dashboard_writer.py` (~330 lines)
**Role:** JSON state writes.

Two outputs:
- `price_data.json` — 1 Hz lightweight price blob (`update_price_only`)
- `dashboard_data.json` — full state with merge logic (`update_dashboard`)

**Key design:**
- Atomic writes via `tempfile.mkstemp` + `os.replace` — torn writes can't corrupt the file
- Merge logic preserves `reasoning`, `candleText`, `sessionLevels`, `newsText`, and all ICT/structure fields when fast-ticker writes lack the full snapshot
- P1.6: `reasoning` block carries `iso_ts` so dashboard can show stale indicator (grey text after 5 min)
- Logger imported at module level (not inside `update_dashboard` hot path)

---

### `data_recorder.py` (~200 lines)
**Role:** JSONL session recording.

Thread-safe singleton (`recorder`). Writes `snapshots_YYYY-MM-DD.jsonl` and `decisions_YYYY-MM-DD.jsonl` to `data/`. Throttled: one snapshot write per 5 seconds max. Strips large fields (`_SNAPSHOT_EXCLUDE`) before writing. `flush_and_close()` called at EOD or Ctrl+C.

---

### `memory_manager.py`
**Role:** Cross-session learning memory.

Writes `memory/session_YYYY-MM-DD.jsonl` at EOD. `load_recent_memory(days=5)` reads last N days. `generate_morning_review()` formats them for pre-market injection. `save_trade_to_memory()` captures each trade for learning synthesis.

---

### `news_calendar.py`
**Role:** Economic calendar and news gating.

Sources: FRED (forexfactory-style scrape), hardcoded recurring events (FOMC, CPI, NFP, PPI, GDP, jobless claims). `get_news_snapshot()` returns: `news_text`, `news_danger_zone` (bool), `next_high_impact`, `next_event_full`, `next_event_minutes`, `recent_event`. Cached 10 min internally; timing-sensitive fields refreshed every snapshot cycle.

---

### `notifier.py`
**Role:** Pushover iPhone push notifications.

Functions: `notify_premarket`, `notify_or_established`, `notify_trade_entered`, `notify_trade_exited`, `notify_stop_to_breakeven`, `notify_eod_summary`, `notify_backtest`, `notify_learning_done`, `notify_error`, `notify_loss_warning`, `notify_bot_sleeping`, `notify_bot_awake`, `notify_ibkr_disconnected`, `notify_ibkr_reconnected`, `notify_consecutive_losses`. All silent (return False) when `PUSHOVER_TOKEN` not set.

---

### `watchdog.py`
**Role:** Standalone health monitor. Run in a separate terminal.

Every 30s:
1. `is_main_running()` — checks for `main.py` in `wmic process` output. Fail-open.
2. `dashboard_age_secs()` — reads `dashboard_data.json` timestamp. Returns 0 when `botSleeping=true` (not stale).
3. `is_gateway_running()` — TCP probe to `IBKR_HOST:IBKR_PORT`.

Sends Pushover alert (`priority=1`) when: bot missing for 2+ checks, dashboard stale >120s, or Gateway unreachable. `ALERT_COOLDOWN=300s` prevents alert storms. Exits when bot is confirmed gone.

---

### `strategy_stats.py`
**Role:** Per-strategy performance tracking.

Tracks wins/losses/P&L per strategy string (from `decision["strategy"]`). Wilson 95% CI. `record_trade()` called after executor close. Requires 20+ trades per strategy before activating performance-weighted pre-filter adjustments.

---

### `learning_session.py`
**Role:** EOD learning orchestrator.

Sequence: load today's trades + analysis log → run `ablation_runner.run_ablation()` → send ablation report + trade summary to Claude Sonnet for synthesis → save report to `memory/` and `reports/` → call `version_manager.eod_commit()` → call `journal_exporter.export_journal()`.

---

### `ablation_runner.py` (~300 lines)
**Role:** Feature ablation engine.

For each flag in `ABLATION_FLAGS` (12 flags — safety features excluded), disables it and re-runs `backtester.run_backtest()`. Compares P&L delta to baseline. Verdict: HELPS / HURTS / NEUTRAL (±$2 threshold). Outputs sorted markdown table. `SAFETY_FEATURES` never toggled.

---

### `backtester.py`
**Role:** JSONL replay engine.

Reads `data/snapshots_YYYY-MM-DD.jsonl`. Runs current `pre_filter_signal()` against each snapshot. If pre-filter passes and a cached Claude decision exists, uses it (free). Otherwise optionally calls live Claude API (~$0.05). `SimExecutor` tracks simulated fills. Returns `{daily_pnl, trade_count, win_rate, wins, losses}`.

---

### `journal_exporter.py`
**Role:** Rebuilds `journal_data.json` from all `decisions_*.jsonl` files.

Computes: equity curve, by-strategy stats (trades/wins/losses/pnl/win_rate), by-hour stats, OFI signal performance, thesis probability buckets, avg R:R, overall win rate, profitability zone (PROFITABLE/DEVELOPING/NEEDS_WORK/NOT_PROFITABLE), weekly R:R summary, zone history.

---

### `version_manager.py`
**Role:** `.env` BOT_VERSION management.

`read_version()`, `write_version(v)`, `bump_version(current, level)`, `eod_commit()`. Does not touch git — version bump only. CLI: `py -3.11 version_manager.py --bump minor`.

---

### `logger.py`
**Role:** Shared logging setup.

Uses `BASE_DIR` env var (not hardcoded path) for log directory. Two outputs: `logs/trading_YYYYMMDD.log` (file) + stdout. Additional helpers: `log_analysis()`, `log_trade()`, `log_error()`, `log_daily_summary()`.

---

## Snapshot Dict — V4.4 Complete Field Reference

```
~65 fields total. Key additions in V4.4:

gap: {
  gap_size: float,           # pts between prev close and today open
  gap_direction: "UP"/"DOWN"/"NONE",
  gap_fill_probability: float  # 0.79/0.52/0.28/0.12 by size
}

pivots: {
  pivot: float, r1: float, r2: float, s1: float, s2: float
}

first_candle_1min_high: float   # 9:30 ET 1-min bar high
first_candle_1min_low: float
first_candle_5min_high: float   # 9:30–9:35 ET range high
first_candle_5min_low: float

vwap_extension: float           # signed pts from VWAP (+ = above)
vwap_extension_abs: float       # abs value, used by VWAP magnet

or_2x_extension_up: bool        # price > or_high + OR_range×multiplier
or_2x_extension_down: bool      # price < or_low - OR_range×multiplier
or_extreme_zone: bool           # either extension flag

opening_drive_up: bool          # first 5-min candle ≥80pts bullish
opening_drive_down: bool
opening_drive_fade_short: bool  # drive_up + rejection wick
opening_drive_fade_long: bool   # drive_down + rejection wick

post_news_window: bool          # 45–75 min after HIGH-impact event
```

---

## JSON Schemas

### `dashboard_data.json` — Key fields

```json
{
  "timestamp": "ISO",          "time_et": "HH:MM:SS",
  "data_mode": "LIVE L2|DELAYED|BOT SLEEPING",
  "botSleeping": bool,         "wakeTime": "string",
  "botVersion": "4.4.1",
  "position": "FLAT|LONG|SHORT",
  "entryPrice": null|float,    "stopPrice": null|float,    "targetPrice": null|float,
  "currentPrice": null|float,  "dailyPnl": float,          "maxLoss": float,
  "claudeStatus": "string",
  "lastDecision": "BUY|SELL|HOLD|CLOSE|TRAIL",
  "lastReasoning": "string",   "lastConfidence": "LOW|MEDIUM|HIGH",
  "lastStrategy": "string",    "lastConfluence": "string",
  "lastConfluenceScore": int,  "thesisProbability": int,
  "reasoning": {"time", "iso_ts", "decision", "confidence", "reasoning"},
  "bias": "BULLISH|BEARISH|NEUTRAL|MIXED",
  "amdPhase": "string",        "killzone": "string",       "htfBias": "string",
  "sessionLevels": "string",   "confluence": ["string"],
  "fair_value_gaps": "string", "fvg_levels": [],
  "order_blocks": "string",    "ob_levels": [],
  "choch": "string",           "inducement": "string",     "candle_patterns": "string",
  "mtf_alignment": "string",   "delta_trend": "string",    "market_structure": "string",
  "tape_bias": "string",       "tape_text": "string",
  "bars1min": [],              "bars5min": [],              "currentBarOpen": null|float,
  "orHigh": null|float,        "orLow": null|float,
  "orBrokenUp": bool,          "orBrokenDown": bool,        "orAttempts": int,
  "or_direction": null|str,    "or_relative_volume": null|float,
  "newsText": "string",        "newsDangerZone": bool,     "ibkrHeadlines": [],
  "account": {},               "netLiq": float,             "ibkrPnl": float,
  "trades": [{"time","action","entry","exit","pnl","mode","exit_reason"}]
}
```

### `price_data.json` — Fast ticker (1 Hz)

```json
{"t":"HH:MM:SS","price":0,"bid":0,"ask":0,"volume":0,"position":"FLAT","entry":0,"stop":0,"target":0,"pnl":0.0,"netLiq":0,"unrealized":0}
```

### `decisions_YYYY-MM-DD.jsonl` — Decision record (one per line)

```json
{"ts":"ISO","ts_et":"HH:MM:SS","bot_version":"4.4.1","type":"decision","model":"claude-opus-4-7","cost_usd":0.0,"pre_filter_reason":"BULL 5 signals...","snapshot":{...slim...},"raw_response":"...","decision":{"decision":"BUY","mode":"SCALP","stop_price":0,"target_price":0,"confidence":"HIGH","thesis_probability":78,"reasoning":"..."}}
```

---

## Invariants / Rules

Do not break these — they represent hard-won fixes:

| Tag | Location | Rule |
|-----|----------|------|
| P1.1 | `executor._fast_protection_loop` | Never call IBKR APIs inside `_lock` |
| P1.3 | `executor._enter_trade` | `entry_timestamp` set on fill, not on order submit |
| P1.7 | `claude_brain.parse_decision` | BUY/SELL → HOLD if stop_price ≤ 0 |
| P2.8 | `main.end_of_day` | `reset_session_state()` wipes all claude_brain module state |
| A.1  | `claude_brain._maybe_skip_call` | Skip-cache — do not remove, cuts cost ~70% |
| A.2  | `claude_brain._build_user_content` | Static block cached, dynamic block never cached |
| D.2  | `executor._auto_trail_long/short` | `_claude_trail_stop` floors auto-trail |
| C.4  | `main._flush_log` | Flush after every BUY/SELL/CLOSE |
| C.6  | `main.main()` | Delete `dashboard_data.json` on boot |
| B.1  | `ibkr_feed.maybe_persist_tick_state` | Persist tick state every 30s |
| B.2  | `ibkr_feed.restore_tick_state` | Restore tick state on startup if same day |
| C.3  | `main.run_premarket` | Build watchlist before pre-market |
| D.1  | `executor._safety_checks` | R-budget gate |
| FIX 1+2 | `executor._close_position` | Race-safe broker sync |
| FIX 3 | `executor._check_stop_and_target` | Never act on `stop_price ≤ 0` |
| FIX 4 | `executor._fast_protection_loop` | Periodic broker reconciliation |
| FIX 5 | `executor._post_close_orphan_check_safe` | Orphan check on caller thread |
| FIX 6 | `executor._record_pnl` | $1000/contract P&L sanity bound |

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
CONTRACT_CONID=             # get from IBKR contract search
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
reports/                  # ablation + learning reports
*.log, *.csv
__pycache__/, *.pyc
```

**Note on `reports/`:** Reports generated by `learning_session.py` are git-ignored. Pre-market learning injection reads from `memory/`, not `reports/`.

---

## Dependencies

```
ib_async           pip install ib_async            # IBKR connection, orders (v4.5.0+; replaces ib_insync)
anthropic          pip install anthropic           # Claude API
pandas             pip install pandas             # bar data
pytz               pip install pytz               # timezone handling
python-dotenv      pip install python-dotenv      # .env loading
schedule           pip install schedule           # EOD scheduler
exchange_calendars pip install exchange-calendars # CME holidays/early close
```

`requirements.txt` is committed. Install with: `pip install -r requirements.txt`

---

## Running

```bash
# Live bot (boots at 8:20 ET after waiting for market hours)
py -3.11 main.py

# Health watchdog (separate terminal)
py -3.11 watchdog.py

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
py -3.11 -m http.server 8080 --bind 0.0.0.0
# then open: localhost:8080/dashboard.html | mobile.html | journal.html

# Demo mode (no IBKR, no Claude)
py -3.11 demo.py
```

---

## CLAUDE.md Additions Not Covered Above

### Bidirectional OR bias (V3.0) — hard rule
Do not reintroduce "LONG_ONLY" logic that blocks one side entirely. The bot must be able to short on bull days and long on bear days when structure demands it. Pre-filter requires 3+ signals with bias, 5+ counter-trend.

### Session type classification — when UNKNOWN is acceptable
UNKNOWN fires when the OR doesn't fit TREND or RANGE cleanly. The bot still trades (5-signal threshold). After 2-3 weeks of data, tune `SESSION_CLASSIFIER_TREND_OR_MIN` and `SESSION_CLASSIFIER_RANGE_OR_MAX` based on observed UNKNOWN days.

### Phase 2-3 feature activation
Never enable Phase 2-3 features (FEATURE_OR_EXTREME_FADE, FEATURE_VWAP_REVERSION, FEATURE_OPENING_DRIVE_FADE, etc.) without first: (1) verifying detection accuracy on recorded sessions via backtester, (2) confirming minimum 20 events of the type to measure win rate. Activate one at a time, not all at once.

### Contract rolls
MNQ rolls quarterly (Mar/Jun/Sep/Dec). Update `CONTRACT_EXPIRY` and `CONTRACT_CONID` in `.env` or IBKR will reject orders. If a session won't connect, check expiry first.

### Risk change confirmation
Any change that loosens risk caps, daily loss limits, or hold-time gates must be flagged for explicit user confirmation. Claude Code has full autonomy for all other changes.

### Session continuity (B.2)
`restore_tick_state()` restores `tick_delta` and `volume_profile` from `memory/tick_state.json` if the same trading day. Bot can restart mid-session without losing cumulative delta history.

---

*End of PROJECT_SUMMARY.md*
