# PROJECT_SUMMARY.md — MNQ AI Trader

Complete codebase reference for an AI reading this cold. Dense and accurate. No fluff.

---

## What This Is

Paper-trading bot for **MNQ** (Micro E-Mini Nasdaq-100 futures). Pulls live L1+L2 data from Interactive Brokers (TWS/Gateway via ib_insync), pre-filters with pure-Python signal scoring, asks Claude (Opus 4.7 for entries, Sonnet 4.6 for position management) for decisions, and executes bracket orders. Paper trading on a simulated $50K account. Hard limits: 1 contract max, $500 daily loss cap, 3R session loss cap.

ICT methodology: Opening Range, CHoCH, FVG, Order Blocks, Liquidity Pools, AMD phases, Kill Zones.

**Running the bot:**
```bash
py -3.11 main.py                                     # live bot, boot at 8:20 ET
py -3.11 backtester.py --date 2026-05-27             # replay a recorded session
py -3.11 backtester.py --list                        # show available recorded dates
py -3.11 demo.py                                     # dashboard demo, no IBKR/Claude needed
py -3.11 config.py                                   # sanity-print config, no API calls
```

No requirements.txt. Install manually:
```
pip install ib_insync anthropic pandas pytz python-dotenv schedule
```

---

## Three Concurrent Loops

| Loop | Thread | Cadence | Job |
|---|---|---|---|
| Main cycle | Main thread | 0.5s sleep; entry scan gated to `ENTRY_SCAN_INTERVAL_SECS` (5s) | Snapshot → pre-filter → Claude entry → executor |
| Protection loop | Daemon thread | 5s (`PROTECTION_LOOP_SECS`) | Stop/target checks, broker reconciliation, orphan detection |
| Fast dashboard ticker | Daemon thread | 1 Hz | Writes `price_data.json`; patches `dashboard_data.json` every 10s |

Same `Executor` instance shared across threads — internal `_lock` guards mutation. Never grab the lock and make an IBKR call inside it (P1.1 fix).

---

## Data Flow Per Cycle

```
ibkr_feed.get_snapshot()          →  snapshot dict (~50 fields)
  → claude_brain.pre_filter_signal()  — pure Python, returns (bool, reason)
    → claude_brain.analyze_market()   — Opus 4.7, skip-cache checked first (A.1)
      → executor.execute()            — places bracket: market + stop + limit
      ↓
  dashboard_writer.update_dashboard() — merges into dashboard_data.json
  data_recorder.record_snapshot/decision() — JSONL to data/ for backtesting
```

---

## Python Files

### `main.py`
Session state machine and main event loop. Coordinates all three threads.

**`SessionState` enum:**
`PRE_SESSION | PRE_MARKET | OR_FORMING | OR_ESTABLISHED | PRIME_WINDOW | DEAD_ZONE | AFTERNOON_PRIME | CLOSING | AFTER_HOURS`

**Key functions:**
- `get_session_state(now_et) → SessionState` — maps clock time to state
- `can_enter(state, confluence_score) → (bool, reason)` — dead zone requires score ≥ 8
- `_should_call_claude_now(executor, snapshot) → bool` — event-driven trigger: adverse move, delta flip, stop proximity, target proximity, giveback. Fires between scan intervals when position needs attention.
- `run_cycle(feed, executor)` — per-tick logic: pre-market at 8:30, watchlist refresh every 5 min, position management if in trade, entry scanning otherwise
- `end_of_day(feed, executor)` — cancels orders, saves memory summary, resets `claude_brain` session state, optionally runs learning session (`FEATURE_LEARNING_EOD`)
- `_fast_dashboard_ticker(feed, executor)` — 1 Hz daemon writing `price_data.json`; patches `dashboard_data.json` every 10s
- `_patch_dashboard_live(feed, executor, price, account)` — writes OR direction, session levels, P&L, position every 10s regardless of Claude activity

**Audit tags in this file:**
- `P1.3` — `entry_timestamp` owned by Executor, not a module global

**Known bug (as of this session):** `main.py:685–690` calls `run_learning_session(..., auto_commit=True)`. The `auto_commit` parameter was removed from `learning_session.py` (git automation removal). This will raise `TypeError` at EOD when `FEATURE_LEARNING_EOD=true`. Fix: remove `auto_commit=True` from that call.

---

### `config.py`
Single source of truth for all configuration. All values environment-overridable via `.env`. No trading logic — just constants and helpers.

**Paths:**
- `BASE_DIR` — default `C:\trading\mnq-ai-trader`; override via env
- `LOG_DIR / MEMORY_DIR / DATA_DIR` — subdirs of BASE_DIR
- `DASHBOARD_FILE` = `dashboard_data.json`, `PRICE_FILE` = `price_data.json`

**Risk parameters:**
- `ACCOUNT_SIZE` = 50000
- `MAX_DAILY_LOSS_PCT` = 0.01 → $500 hard stop
- `MAX_SESSION_R_LOSS` = 3.0 R-units (FEATURE_R_BUDGET gates this)
- `MAX_CONTRACTS` = 1
- `MIN_THESIS_PROBABILITY` = 70 (FEATURE_THESIS_GATE gates this)

**Contract (update quarterly — Mar/Jun/Sep/Dec):**
- `CONTRACT_EXPIRY` = "20260618"
- `CONTRACT_CONID` = 770561201
- `SYMBOL` = "MNQ"

**Claude models:**
- `CLAUDE_ENTRY_MODEL` = "claude-opus-4-7" (entries — most capable, most expensive)
- `CLAUDE_POSITION_MODEL` = "claude-sonnet-4-6" (position management)
- `CLAUDE_STRUCTURE_MODEL` = "claude-sonnet-4-6" (watchlist/structure updates)

**Timing:**
- `ENTRY_SCAN_INTERVAL_SECS` = 5
- `PROTECTION_LOOP_SECS` = 5
- `WATCHLIST_REFRESH_MINS` = 5
- `OR_FORMATION_MINS` = 15

**Helper functions:**
- `features_summary() → str` — one-line summary of active flags for logging
- `get_active_features() → dict` — all 15 feature flags as bool dict

---

### `claude_brain.py`
All Anthropic API calls. Module-level state must be wiped at EOD via `reset_session_state()` (P2.8 — leaks across days if not called).

**Module globals (wiped by `reset_session_state()`):**
- `_session_watchlist` — current Claude watchlist/bias output
- `_watchlist_time` — last watchlist update time
- `_session_context` — accumulated session context string
- `_last_entry_call` — snapshot fingerprint for skip cache (A.1)
- `_cost_tracker` — API cost accumulator

**Key functions:**

`pre_filter_signal(snapshot) → (bool, reason)`
Pure Python, no API. Scores 10+ bullish/bearish signals. Returns `True` (worth calling Claude) if score ≥ 3 on bias-preferred side, or ≥ 5 counter-bias. This is the primary cost control — most ticks never reach Claude.

Signals scored: OR position, CHoCH, VWAP relationship, cumulative delta, MTF alignment, OFI score, DOM sweeps/icebergs/clusters/vacuum, news gate, dead zone gate, thesis gate placeholder.

`analyze_market(snapshot) → dict`
Opus 4.7 entry analysis. Returns parsed decision dict. Checks skip-cache first (A.1). Injects: system prompt (cached), watchlist block (cached), snapshot fields (uncached), strategy stats context, learning history.

`analyze_position(snapshot, position, entry_price, stop_price, target_price, trade_mode) → dict`
Sonnet 4.6 position management. Returns position decision (HOLD/TRAIL/CLOSE/PARTIAL). Called from `_should_call_claude_now` trigger points.

`update_watchlist(snapshot) → dict`
Sonnet 4.6. Updates `_session_watchlist` every 5 min with current bias, key levels, MTF state. Bias validation: DOJI→NO_TRADE, full MTF disagreement overrides OR direction, structural decay after 90 min, large adverse move invalidation.

`analyze_premarket(snapshot, memory_context) → str`
Opus 4.7. Runs once at 8:30 ET. Sets session context. Reads `_session_watchlist`, recent memory, optionally injects learning reports (`FEATURE_LEARNING_INJECT`).

`parse_decision(text, allow_zero_stop=False) → dict`
Parses Claude's JSON response. P1.7: demotes BUY/SELL → HOLD if `stop_price ≤ 0`. V4.0: demotes BUY/SELL → HOLD if `thesis_probability < MIN_THESIS_PROBABILITY`. Returns: `{decision, stop_price, target_price, confidence, strategy, thesis_probability, reasoning}`.

`parse_position_decision(text) → dict`
Parses position management response. Returns: `{action, new_stop, reasoning}`.

`_maybe_skip_call(snapshot) → (bool, cached_decision)`
A.1 skip-cache: returns prior HOLD for free when `<5pt price move AND no new bar AND watchlist fresh AND <3 min elapsed`. Targets ~60-70% Opus call reduction.

`_build_system(prompt) → dict`
Wraps system prompt with `cache_control` when `CLAUDE_USE_CACHING=true`.

`_tolerant_json_parse(raw) → dict`
Handles bare newlines in Claude's JSON output (common parse failure mode).

**Three system prompts (module-level constants):**
- `SYSTEM_PROMPT` — entry analysis (ICT methodology, risk rules, output format)
- `POSITION_SYSTEM` — position management (trailing, defense, exit criteria)
- `STRUCTURE_SYSTEM` — watchlist/structure (bias assessment, level identification)

---

### `ibkr_feed.py`
IBKR connection and snapshot assembly. ~1916 lines. Central data abstraction: the `snapshot` dict (~50 fields) returned by `get_snapshot()` is consumed by every Claude prompt, every pre-filter call, and the backtester recorder.

**Connection:** `ib_insync.IB`. Reconnect logic built in. Subscribes to MNQ L2 (DOM), 1-min and 5-min bars via `reqRealTimeBars`. Bars fetched once at startup via `initialize_bars()`, then updated incrementally.

**Key method: `get_snapshot(current_position, daily_pnl, daily_loss_remaining, consecutive_losses) → dict`**

Snapshot fields (~50 total):
```
timestamp, time_et, data_mode
currentPrice, bid, ask, volume, cumDelta, deltaLastBar
sessionHigh, sessionLow, vwap
orHigh, orLow, orBrokenUp, orBrokenDown, or_direction, or_relative_volume
bars1min, bars5min             — list of {t, o, h, l, c, v, forming, vwap}
position, entryPrice, stopPrice, targetPrice
dailyPnl, maxLoss, netLiq, daily_loss_remaining, consecutive_losses
fair_value_gaps, order_blocks, liquidity_pools
choch, inducement, mtf_alignment, delta_trend
ofi_score                      — -100 to +100
dom_signals                    — iceberg/spoof/sweep/cluster summary
newsText, newsDangerZone, nextEventFull, nextEventMinutes
ibkrHeadlines                  — [{time, provider, headline}]
htfBias, killzone, amdPhase
sessionLevels
```

**Important computed internals:**

`_calculate_opening_range()` — idempotent (P0.3 fix prevents `or_break_count` inflation on re-calls). Sets `or_high`, `or_low`, `or_direction` based on first 15 min of RTH (9:30–9:45 ET).

`_compute_ofi()` — Order Flow Imbalance, Cont/Kukanov/Stoikov (2014) formula. Returns score -100 to +100. Used in pre-filter.

`_check_mtf_alignment()` / `_check_mtf_score()` — 1min/5min/15min structure checks (HH/HL/LH/LL). Used in watchlist update and pre-filter.

DOM history: 12 snapshots (~60s rolling) for iceberg/spoof/sweep/cluster detection (`FEATURE_DOM_ADVANCED`). Tracks bid/ask size changes across levels to detect hidden large orders and stop hunts.

Tick state persistence (B.1/B.2/B.3): saves `tick_delta` + `volume_profile` to `memory/tick_state.json` every 30s. Restored on same-day restart.

IBKR live news: tick 292 on QQQ subscription → `_on_tick_news()` handler populates `ibkrHeadlines`.

**ICT level detection:**
- Fair Value Gaps (FVGs): 3-bar pattern, bullish/bearish, tracks fill status
- Order Blocks (OBs): last bearish bar before bullish impulse (bull OB) and vice versa
- Liquidity Pools: swing highs/lows above/below current price
- CHoCH (Change of Character): first HH after LL sequence or LH after HH
- Inducement: short-term liquidity grab before expected move

---

### `executor.py`
Bracket order placement, position tracking, protection loop, R-budget, dual-control trailing.

**Key state:**
- `current_position` — "LONG" / "SHORT" / "FLAT"
- `entry_price`, `stop_price`, `target_price`
- `trade_mode` — "NORMAL" / "AGGRESSIVE" / "DEFENSIVE"
- `entry_timestamp` (P1.3 — owned here, not a global in main.py)
- `daily_pnl`, `daily_loss_remaining`, `session_r_spent`
- `_claude_trail_stop` — D.2: Claude's last TRAIL stop; floors the auto-trail
- `trades_today` — list of closed trade dicts

**Key methods:**

`execute(decision) → bool`
Runs `_safety_checks()` first. If BUY/SELL passes, places bracket via `_place_bracket_order()`. Returns True if order placed.

`_safety_checks(decision) → (bool, reason)`
Checks: daily loss gate, R-budget gate (`FEATURE_R_BUDGET`), low confidence skip, already-in-position skip.

`_fast_protection_loop()`
Daemon thread, every 5s. Checks current price vs stop/target. Reconciles with broker every 20s (`_reconcile_with_broker()`). Detects orphan orders (open orders with no known position). Calls `_close_position()` on stop/target hit.

`_close_position(reason)`
Pre-flight broker check → place market close → post-cancel recheck → `_infer_recent_exit_fill()`. The cancel-vs-fill race fix (V2.5/P2.5).

`_record_pnl(fill_price)`
P&L sanity bound: rejects if `abs(pnl) > $1000/contract` (detects corrupted state, e.g., fill price in wrong units).

`_auto_trail_long()` / `_auto_trail_short()`
Milestones: 50 ticks → breakeven, 100 ticks → +25t, 150 ticks → +50t above entry.
D.2: `_claude_trail_stop` always floors the result — auto-trail can never move stop looser than Claude's last TRAIL instruction.

`update_trail_from_claude(new_stop)`
Called when Claude returns a TRAIL action in `analyze_position`. Updates `_claude_trail_stop`.

---

### `dashboard_writer.py`
Writes `dashboard_data.json` (full, every ~10s) and `price_data.json` (1 Hz lightweight).

`update_dashboard(**kwargs)`
Merges with existing file to preserve reasoning/ICT fields across fast-ticker writes. P1.6: `reasoning` block includes `iso_ts` for age display and stale detection (>5 min → grey out in dashboard).

`update_price_only(price, bid, ask, volume, position, entry_price, stop_price, target_price, daily_pnl, account)`
Lightweight 1 Hz write. Only touches price/position fields; leaves reasoning/ICT fields untouched.

---

### `memory_manager.py`
Persistent session memory across trading days.

`save_daily_summary(trades, daily_pnl, analysis_log)`
Asks Claude Sonnet for structured lessons from the day → saves JSON + markdown to `memory/`. JSON schema: `{date, pnl, trades, wins, losses, key_lessons: [], mistakes: [], market_conditions: str}`.

`load_recent_memory(days=5) → str`
Compact context string (last 5 sessions' lessons) injected into Claude entry analysis prompt.

`generate_morning_review(current_snapshot=None) → str`
Pre-session briefing from recent lessons. Called optionally at pre-market.

`save_trade_to_memory(trade)` / `load_todays_trades()`
Intraday trade persistence for same-day restarts.

---

### `news_calendar.py`
Economic calendar and news danger zones.

**Priority chain:** ForexFactory JSON feed (free, no key) → FRED API (free, needs key) → hardcoded fallback schedule.

`get_news_snapshot(ib=None) → dict`
Returns:
```python
{
  "news_text": str,              # one-line summary for dashboard
  "events_today": list,          # [{time, name, impact, currency}]
  "news_danger_zone": bool,      # True if currently in buffer around HIGH/MEDIUM event
  "next_high_impact": str,       # e.g. "NFP 08:30 ET"
  "next_event_full": str,        # full next event description
  "next_event_minutes": int,     # minutes until next event
  "recent_event": str,           # most recent past event today
}
```

**Danger zone buffers:**
- HIGH impact: 15 min before → 30 min after
- MEDIUM impact: 10 min before → 10 min after

**FOMC hardcoded for 2026** — warning logged if `datetime.now().year != fomc_year`. Must update annually.

`prefetch_calendar()` — called at startup to warm cache. ForexFactory is the primary source; FRED is fallback for US events only.

---

### `data_recorder.py`
JSONL recording for backtester replay. Singleton: `recorder = DataRecorder()`.

Files written to `data/`:
- `snapshots_YYYY-MM-DD.jsonl` — one snapshot per record, throttled to 5s minimum interval; excludes large text fields
- `decisions_YYYY-MM-DD.jsonl` — one decision per record; includes `raw_response`, `parsed_decision`, `model`, `cost_usd`, `pre_filter_reason`

`record_snapshot(snapshot)` — stores snapshot with timestamp.
`record_decision(snapshot, raw_response, parsed_decision, model, cost_usd, pre_filter_reason)` — stores full decision context; this is what the backtester uses as replay cache.
`flush_and_close()` — called at EOD.

If a snapshot key is added to `ibkr_feed.get_snapshot()`, old JSONL files won't have it — backtester sees `None` for that key on historical replays.

---

### `strategy_stats.py`
Per-trade performance tracking with statistical rigor.

**Tracked dimensions:**
- Per strategy type (ORB_PULLBACK, OB_BOUNCE, FVG_FILL, CHOCH_ENTRY, etc.)
- Per confluence factor
- Per kill zone (NY AM, NY PM, etc.)
- Per score bracket (3–4, 5–6, 7–8, 9+)
- Per OR direction (BULL/BEAR)

`_wilson_lower_bound(wins, trades, z=1.96) → float`
P1.4: conservative win rate estimate. Used for recommendations to avoid overfitting small samples.

`generate_performance_context() → str`
Called from `claude_brain.analyze_market` to inject current stats into entry prompt. Only generates recommendations for strategies/conditions with ≥ 20 trades (`MIN_TRADES_FOR_INSTRUCTION`) AND Wilson lower bound ≥ 55%.

---

### `logger.py`
Standard Python logging. FileHandler + StreamHandler.

**Hardcoded Windows path:** `C:\trading\logs\` — does not use `config.LOG_DIR`. If running on Linux/Mac, log files will fail silently (StreamHandler still works).

Functions: `log_analysis`, `log_trade`, `log_error`, `log_daily_summary`.
`_flush_log()` (C.4): called after BUY/SELL/CLOSE so entries land on disk before any crash.

---

### `backtester.py`
Replay engine. Under 5s for a full trading day. Uses cached Claude decisions — no API spend unless `use_claude_for_uncached=True` and the pre-filter now passes where it didn't before.

`run_backtest(date_str, verbose=False, use_claude_for_uncached=True) → dict`
Loads `data/snapshots_YYYY-MM-DD.jsonl` + `data/decisions_YYYY-MM-DD.jsonl`. Replays current pre-filter logic against recorded snapshots. Uses cached decisions from `decisions_*.jsonl` for cache hits.

`SimExecutor`
Minimal simulated executor — no IBKR, no threading, no real orders. Tracks position, P&L, trades.

**Return dict:**
```python
{
  "date": str,
  "elapsed_secs": float,
  "snapshots": int,           # total snapshots replayed
  "pre_filter_passes": int,   # how many passed pre-filter
  "cache_hits": int,          # decisions served from cache
  "api_calls": int,           # live Claude calls made
  "trades": list,             # [{entry, exit, pnl, direction, strategy, ...}]
  "daily_pnl": float,
  "trade_count": int,
  "wins": int,
  "losses": int,
  "win_rate": float,          # percentage
}
```

**The backtester is the regression check.** When changing pre-filter logic, prompt structure, or snapshot schema: run before and after — P&L / W-L delta is the validation signal.

---

### `ablation_runner.py`
Feature ablation testing. Disables each `FEATURE_*` flag one at a time, runs backtester, measures P&L delta against baseline.

`ABLATION_FLAGS` (12 flags tested):
`FEATURE_ORB_BIAS`, `FEATURE_BIDIRECTIONAL`, `FEATURE_BIAS_DECAY`, `FEATURE_OFI`, `FEATURE_DOM_ADVANCED`, `FEATURE_MTF_SCORE`, `FEATURE_THESIS_GATE`, `FEATURE_R_BUDGET`, `FEATURE_NEWS_GATE`, `FEATURE_DEAD_ZONE`, `FEATURE_DUAL_TRAIL`, `FEATURE_EARLY_EXIT`

`SAFETY_FEATURES` (never toggled — excluded from ablation):
`FEATURE_LEARNING_EOD`, `FEATURE_LEARNING_INJECT`, `FEATURE_DELTA_LIVE`

`run_ablation(date_str, verbose=False) → dict`
1. Runs baseline (all flags ON).
2. For each flag: disable it, run backtest, measure delta.
3. Verdict: `HELPS` (delta > $2), `HURTS` (delta < -$2), `NEUTRAL` (|delta| < $2).

Returns: `{date, baseline, ablations: {label: {results, delta_pnl, delta_trades, verdict, env_key}}, report: str}`

`save_report(report_text, date_str) → Path` — saves to `reports/ablation_YYYY-MM-DD.md`.

Called by `learning_session.py`. Can run standalone: `py -3.11 ablation_runner.py --date 2026-05-27`.

---

### `learning_session.py`
EOD learning orchestrator. Runs after trading stops (~4:00 PM ET).

**Steps:**
1. Check if session data exists (`data/snapshots_YYYY-MM-DD.jsonl`)
2. Run ablation backtest (if data exists)
3. Load last 5 learning reports for trend context
4. Ask Claude Sonnet 4.6 for insights (key observations, pattern analysis, feature recommendations, entry quality, tomorrow's focus, confidence rating)
5. Build and save learning report to `reports/learning_YYYY-MM-DD.md` AND `memory/learning_YYYY-MM-DD.md`
6. Bump version in `.env` via `version_manager.eod_commit()`

`run_learning_session(date_str, session_summary="", trades=None) → str`
Returns path to saved report.

`load_learning_for_premarket(n_days=3) → str`
Called by `claude_brain.analyze_premarket` when `FEATURE_LEARNING_INJECT=true`. Loads last N learning reports, extracts "Claude's Analysis" section, returns formatted block injected into pre-market prompt.

---

### `version_manager.py`
Version tracking only. No git operations (git automation removed in this session).

`read_version() → str` — reads `BOT_VERSION` from `.env`.
`write_version(version)` — writes `BOT_VERSION` to `.env`.
`bump_version(current, level) → str` — level: "patch" / "minor" / "major".
`eod_commit(session_summary="", bump="patch", extra_message="") → str` — bumps version in `.env`, returns new version string. Called by `learning_session.py` at EOD.

CLI: `py -3.11 version_manager.py --bump minor` / `--show`

---

### `demo.py`
Dashboard demo mode. Pumps realistic fake MNQ data into `dashboard_data.json` so all dashboard components update without IBKR or Claude API.

`MarketSim` class: self-contained price simulation with phase state machine (SCANNING → ANALYSIS → ENTERING → IN_TRADE → EXITING). Generates realistic bars, trades, DOM signals, ICT levels.

`build_snapshot() → dict` — produces same schema as `ibkr_feed.get_snapshot()`. Includes `demoMode: True` and `simTimeEt` fields.

Run: `py -3.11 demo.py` then open `http://localhost:8080/dashboard.html`.

---

## HTML Files

### `dashboard.html`
Full trading dashboard. Reads `dashboard_data.json` every 200ms (`?t=timestamp` cache-bust). Standalone HTML/JS — no build step.

**Layout:** 3-panel grid + market status bar + bottom news/calendar strip.
- Left panel: position details, key price levels
- Center panel: Claude analysis, candlestick chart, trade log
- Right panel: market context (ICT levels, MTF, stats)
- Market status bar: session schedule with countdown
- Bottom strip: news headlines + economic calendar

**Data source:** polls `dashboard_data.json` at 200ms. Switches to `price_data.json` for in-between price updates.

**Offline detection:** >30s since last JSON write → shows offline banner, greys UI.

**Stale reasoning detection:** `reasoning.iso_ts` field; >5 min old → grey italic styling.

**Candlestick chart:** Canvas API. 1-min and 5-min bars switchable. Overlays: VWAP curve, OR high/low lines, session H/L, trade entry/exit markers, current entry/stop/target lines. Pan + zoom supported.

**"Jarvis" visualization:** animated neural network SVG/Canvas in center panel.

**Key element IDs updated by the bot:**

| Element ID | What it shows |
|---|---|
| `price-main` | Current price |
| `bid` / `ask` | L1 bid/ask |
| `pill-status` | Claude status (SCANNING/ANALYZING/IN POSITION) |
| `pill-position` | LONG/SHORT/FLAT |
| `pill-killzone` | Current kill zone |
| `pill-amd` | AMD phase |
| `decision-badge` | BUY/SELL/HOLD/CLOSE |
| `meta-conf` | Confidence level |
| `meta-score` | Confluence score |
| `meta-prob` | Thesis probability |
| `meta-strat` | Strategy name |
| `reasoning-block` | Full reasoning text |
| `reasoning-age` | Age of last reasoning |
| `bias-val` | Bias (LONG_PREFERRED etc) |
| `lv-stop/entry/target/vwap/sh/sl/orh/orl/delta/dlb/pnl/loss/trades/netliq` | Level values |
| `or-badge` | OR direction badge |
| `or-detail` | OR levels detail |
| `or-rvol` | OR relative volume |
| `htf-bias-text` | HTF bias description |
| `ict-fvg/ob/liq/choch/ind/mtf` | ICT level text blocks |
| `stat-trades/wins/losses/wr` | Session stats |
| `session-levels-text` | Session levels summary |
| `trades-body` | Trade log table rows |
| `headlines-container` | News headlines list |
| `calendar-container` | Economic calendar events |
| `version-badge` | Bot version |

---

### `mobile.html`
Responsive card layout for phones. Named "DoBot". Same `dashboard_data.json` source, 200ms polling.

No price chart, no ICT detail panels, no animated visualization. Simplified card layout: market status, price, position, Claude decision, bias, reasoning, risk, stats, news.

**Key element IDs:**

| Element ID | What it shows |
|---|---|
| `clock` | Current ET time |
| `version` | Bot version |
| `data-mode` | LIVE L2 / SIM / DEMO |
| `ms-state` / `ms-countdown` | Market session state + countdown |
| `price-main` / `price-sub` | Price + change |
| `offline-badge` | Shown when data stale |
| `pos-display` | LONG/SHORT/FLAT |
| `pos-pnl` | Position P&L |
| `pos-stop/entry/target` | Position levels |
| `decision-badge` | BUY/SELL/HOLD |
| `meta-conf/prob/score` | Confidence/probability/score |
| `bias-val` | Current bias |
| `reasoning` | Last reasoning text |
| `risk-pnl/loss` | Daily P&L / loss remaining |
| `st-trades/wins/losses/wr` | Session stats |
| `news-text` / `news-dot` | News summary + danger indicator |

---

## `dashboard_data.json` Schema

Full field reference. Written by `dashboard_writer.update_dashboard()` and `demo.py`.

```json
{
  "timestamp":           "ISO8601 string",
  "time_et":             "HH:MM:SS",
  "data_mode":           "LIVE L2 | SIM | LIVE L2 (DEMO)",
  "botVersion":          "4.1.0",

  "position":            "LONG | SHORT | FLAT",
  "entryPrice":          123.45,
  "stopPrice":           120.00,
  "targetPrice":         130.00,
  "currentPrice":        124.50,
  "bid":                 124.25,
  "ask":                 124.75,

  "dailyPnl":            250.00,
  "maxLoss":             500.00,
  "netLiq":              50250.00,

  "claudeStatus":        "SCANNING | ANALYZING | IN POSITION",
  "lastDecision":        "BUY | SELL | HOLD | CLOSE",
  "lastConfidence":      "HIGH | MEDIUM | LOW",
  "lastStrategy":        "ORB_PULLBACK | OB_BOUNCE | FVG_FILL | ...",
  "lastConfluence":      "OR_BULL + CHOCH_BULL + ...",
  "lastConfluenceScore": 6,
  "thesisProbability":   78,

  "reasoning": {
    "time":     "HH:MM:SS",
    "iso_ts":   "ISO8601 string",
    "decision": "HOLD",
    "reasoning":"Full Claude reasoning text..."
  },
  "lastReasoning": "Full Claude reasoning text...",

  "bias":      "LONG_PREFERRED | SHORT_PREFERRED | NEUTRAL | NO_TRADE",
  "amdPhase":  "ACCUMULATION | MANIPULATION | DISTRIBUTION | REVERSAL",
  "killzone":  "NY AM Kill Zone | NY PM Kill Zone | Outside Kill Zone",
  "htfBias":   "BEARISH — Daily below 20EMA...",

  "sessionLevels": "OR high: 29665 | OR low: 29630 | VWAP: 29645",

  "fair_value_gaps":  "BULL FVG 29648.50-29652.00 (active)",
  "order_blocks":     "BEAR OB 29672.00-29678.00 | BULL OB 29622.00-29628.00",
  "liquidity_pools":  "Buy-side: 29720.00 | Sell-side: 29580.00",
  "choch":            "BULLISH CHoCH — HH/HL on 1m",
  "inducement":       "None detected",
  "mtf_alignment":    "PARTIAL_BULL (2/3 TF bullish)",
  "delta_trend":      "POSITIVE — net buyers last 3 bars",

  "vwap":            29645.00,
  "sessionHigh":     29700.00,
  "sessionLow":      29590.00,
  "volume":          45000,
  "cumDelta":        1250,
  "deltaLastBar":    35,

  "orHigh":          29665.00,
  "orLow":           29630.00,
  "orBrokenUp":      true,
  "orBrokenDown":    false,
  "or_direction":    "BULL",
  "or_relative_volume": 112.5,

  "newsText":        "No major USD events in next hour",
  "newsDangerZone":  false,
  "nextEventFull":   "CPI 08:30 ET — HIGH impact",
  "nextEventMinutes": 45,

  "ibkrHeadlines": [
    {"time": "10:32 ET", "provider": "BRF", "headline": "Nasdaq holds..."}
  ],

  "bars1min": [
    {"t": "2026-05-27T09:30", "o": 29630, "h": 29645, "l": 29625, "c": 29640, "v": 1200, "forming": false, "vwap": 29635}
  ],
  "bars5min": [ /* same schema */ ],

  "tradeMarkers": [
    {"t": "2026-05-27T09:45", "price": 29655, "dir": "LONG", "exit": 29680, "pnl": 50.0}
  ],

  "trades": [
    {
      "time": "09:45", "action": "BUY", "direction": "LONG",
      "entry": 29655, "exit": 29680, "pnl": 50.0,
      "exit_reason": "Target hit", "strategy": "ORB_PULLBACK"
    }
  ],

  "demoMode":    true,
  "simTimeEt":   "10:15:30",
  "simMarketState": "NY AM PRIME",
  "simMarketClass": "am-prime"
}
```

---

## `price_data.json` Schema

Lightweight 1 Hz write from fast dashboard ticker:
```json
{
  "price":       29650.25,
  "bid":         29650.00,
  "ask":         29650.50,
  "volume":      47000,
  "position":    "FLAT",
  "entryPrice":  null,
  "stopPrice":   null,
  "targetPrice": null,
  "dailyPnl":    125.00,
  "netLiq":      50125.00,
  "timestamp":   "ISO8601 string"
}
```

---

## All 15 Feature Flags

Set in `.env`, read by `config.py`. All default to `true` unless noted.

| Flag | Default | Controls |
|---|---|---|
| `FEATURE_ORB_BIAS` | true | Opening Range direction is used as session bias. If false, pre-filter ignores OR direction entirely. |
| `FEATURE_BIDIRECTIONAL` | true | Allows trading counter to OR bias (requires 5+ signals instead of 3+). If false, only bias-direction trades allowed. |
| `FEATURE_BIAS_DECAY` | true | Decays OR bias to NEUTRAL after 90 min, on full MTF disagreement, or after 80pt adverse move. If false, bias remains fixed all day. |
| `FEATURE_OFI` | true | Order Flow Imbalance score included in pre-filter signal scoring. OFI ≥ +30 = bullish signal; ≤ -30 = bearish. |
| `FEATURE_DOM_ADVANCED` | true | Advanced DOM analysis: iceberg detection, spoofing detection, sweep detection, DOM cluster magnets. Uses 60s rolling DOM history. |
| `FEATURE_MTF_SCORE` | true | Multi-timeframe alignment score used in pre-filter. MTF agreement = +1 signal; disagreement = blocks counter-bias trades. |
| `FEATURE_THESIS_GATE` | true | Blocks BUY/SELL entries if Claude's `thesis_probability` < `MIN_THESIS_PROBABILITY` (70). Handled in `parse_decision`. |
| `FEATURE_R_BUDGET` | true | Blocks new entries after `MAX_SESSION_R_LOSS` (3.0) R-units lost in session. Tracked in `executor.session_r_spent`. |
| `FEATURE_NEWS_GATE` | true | Blocks entries during high/medium impact news danger zones. Uses `news_calendar.get_news_snapshot()`. |
| `FEATURE_DEAD_ZONE` | true | Enforces dead zone rules: blocks entries 11:30–13:30 ET unless confluence score ≥ 8. |
| `FEATURE_DUAL_TRAIL` | true | Dual-control trailing (D.2): Claude's structural stop floors the auto-trail. Auto-trail can never move stop looser than Claude's last TRAIL. |
| `FEATURE_EARLY_EXIT` | true | Early exit logic in position management. Claude can return early exit signals before stop/target. |
| `FEATURE_LEARNING_EOD` | true | Runs `learning_session.run_learning_session()` at end of day after trading stops. |
| `FEATURE_LEARNING_INJECT` | true | Injects last 3 days' learning reports into pre-market analysis prompt (`analyze_premarket`). |
| `FEATURE_DELTA_LIVE` | true | Live tick delta and cumulative delta computation in `ibkr_feed`. If false, delta fields return zero. |

**Safety features never toggled in ablation:** `FEATURE_LEARNING_EOD`, `FEATURE_LEARNING_INJECT`, `FEATURE_DELTA_LIVE`.

---

## Recent Session Changes

### 1. Git Automation Removed
**Motivation:** Bot should never touch git — that's a manual human action only.

**`version_manager.py`:**
- Removed `import subprocess`
- Removed entire "Git helpers" section: `_run_git`, `get_changed_files`, `get_diff_summary`, `git_add_all`, `git_commit`, `git_push`, `git_tag`, `git_log_since`
- `eod_commit()` now only bumps version in `.env` — no git operations
- Removed `--tag` CLI flag, removed git log from `--show`

**`learning_session.py`:**
- Removed `auto_commit` parameter from `run_learning_session()`
- Step 7 now only bumps version; no git commit/push
- Removed `--no-commit` CLI flag

**`ablation_runner.py`:**
- Removed stale "committed to git" from `save_report()` docstring only; no functional changes

**`.gitignore`:**
- Added: `reports/`, `*.log`, `*.csv`
- Pre-existing: `.env`, `env`, `*.key`, `dashboard_data.json`, `price_data.json`, `tick_state.json`, `data/`, `memory/`, `logs/`, `__pycache__/`, `*.pyc`, `*.pyo`, `*.pyd`, `.DS_Store`, `Thumbs.db`

### 2. Features NOT YET Implemented (mentioned, not in codebase)
- **`exchange_calendars` trading hours gate** — not present in any .py file
- **Holiday support** — not present; bot may attempt to connect on market holidays
- **`journal.html`** — does not exist
- **`journal_exporter.py`** — does not exist

---

## Known Bugs and Pending Issues

### BUG 1 — `main.py:689` TypeError at EOD (CRITICAL)
**Location:** `main.py`, line 685–690, inside `end_of_day()`
**Problem:** Calls `run_learning_session(..., auto_commit=True)` but `auto_commit` parameter was removed from `learning_session.py` during git automation removal.
**Effect:** `TypeError: run_learning_session() got an unexpected keyword argument 'auto_commit'` raised at EOD when `FEATURE_LEARNING_EOD=true`. Learning session is skipped due to `except Exception` catch.
**Fix:** Remove `auto_commit=True` from the call:
```python
run_learning_session(
    date_str        = date_str,
    session_summary = session_summary,
    trades          = executor.trades_today,
)
```

### BUG 2 — `logger.py` Hardcoded Windows Path
**Location:** `logger.py`, file handler setup
**Problem:** Log directory hardcoded as `C:\trading\logs\` instead of reading `config.LOG_DIR`.
**Effect:** On Linux/Mac, FileHandler silently fails. Log entries only go to stdout/stderr.
**Fix:** Replace hardcoded path with `config.LOG_DIR`.

### MAINTENANCE — FOMC Dates Hardcoded
**Location:** `news_calendar.py`
**Problem:** FOMC meeting dates hardcoded for 2026. Warning is logged when `datetime.now().year != fomc_year`.
**Action required:** Update annually. Also: ForexFactory is the primary calendar source — if it goes down, fallback is FRED (requires `FRED_API_KEY` in `.env`), then hardcoded schedule.

### MAINTENANCE — Contract Expiry
**Location:** `config.py` / `.env`
**Problem:** `CONTRACT_EXPIRY` and `CONTRACT_CONID` must be updated each quarter (Mar/Jun/Sep/Dec). Current values: expiry `20260618`, conid `770561201`.
**Effect:** IBKR rejects orders immediately if expiry is past.

### DESIGN NOTE — Snapshot Schema Versioning
No schema versioning on JSONL files. If a field is renamed or removed from `ibkr_feed.get_snapshot()`, backtester replays of old recordings will see `None` or `KeyError` for that field. Be careful when evolving the snapshot schema.

### DESIGN NOTE — strategy_stats.py Minimum Trades
Recommendations require ≥ 20 trades. In early days of running the bot, `generate_performance_context()` returns minimal/no recommendations. This is intentional — Wilson lower bound is unreliable on small samples.

---

## Audit Tag Reference

These tags appear as code comments and mean "do not undo this — there's a specific reason":

| Tag | Location | What it protects |
|---|---|---|
| P0.3 | `ibkr_feed.py` | `_calculate_opening_range()` idempotency — prevents `or_break_count` inflation on re-calls |
| P1.1 | `executor.py` | `_get_market_price()` reads `_last_price` outside the lock — no IBKR call inside lock |
| P1.3 | `executor.py` / `main.py` | `entry_timestamp` owned by Executor, not a module global in main.py |
| P1.4 | `strategy_stats.py` | Wilson lower bound for win rate estimates — `≥20 trades` minimum |
| P1.6 | `dashboard_writer.py` | `iso_ts` field in reasoning block for age/stale detection |
| P1.7 | `claude_brain.py` | `parse_decision` demotes BUY/SELL→HOLD if `stop_price ≤ 0` — surfaces parse failures |
| P2.5 | `executor.py` | Cancel-vs-fill race fix: pre-flight check, post-cancel recheck, `_infer_recent_exit_fill()` |
| P2.8 | `claude_brain.py` | `reset_session_state()` called at EOD — module globals must be wiped or they leak across days |
| A.1 | `claude_brain.py` | Skip-when-unchanged cache — removing it triples Opus spend |
| B.1/B.2/B.3 | `ibkr_feed.py` | Tick state persistence to `memory/tick_state.json` every 30s for same-day restart recovery |
| C.4 | `logger.py` | `_flush_log()` called after BUY/SELL/CLOSE — entries on disk before any crash |
| D.1 | `executor.py` | R-budget: `session_r_spent` tracking; `FEATURE_R_BUDGET` gates new entries |
| D.2 | `executor.py` | `_claude_trail_stop` floors auto-trail — Claude's structural stop always wins |

---

## Key Invariants

1. **`.env` is never committed to git.** All secrets (API keys, account credentials) live in `.env`. `.gitignore` excludes it. `.env.example` is the committed template.

2. **One contract maximum.** Hard-coded in `config.MAX_CONTRACTS = 1` and enforced in `executor._safety_checks()`.

3. **$500 daily loss cap.** `MAX_DAILY_LOSS_PCT = 0.01` on $50K account. `executor.daily_loss_remaining` checked before every entry.

4. **No git operations in bot code.** All git is manual. Version bumps only touch `.env`.

5. **Pre-filter runs every 5s; position management runs event-driven.** Don't add polling logic inside `_should_call_claude_now` — it fires on events (adverse move, delta flip, stop proximity, target proximity, giveback).

6. **Backtester is the regression check.** Before and after any pre-filter/prompt/schema change: `py -3.11 backtester.py --date <recent>`. P&L / W-L delta is the signal.

7. **Paper trading only.** `LIVE_DATA_ACTIVE` in `.env` controls whether live IBKR data is used. Any change that loosens risk caps, daily loss limits, or hold-time gates requires explicit human confirmation.

---

## Environment Variables (`.env.example` reference)

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...
BASE_DIR=C:\trading\mnq-ai-trader

# IBKR
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1
ACCOUNT_SIZE=50000
LIVE_DATA_ACTIVE=true

# Contract (update quarterly)
CONTRACT_EXPIRY=20260618
CONTRACT_CONID=770561201

# Claude models
CLAUDE_ENTRY_MODEL=claude-opus-4-7
CLAUDE_POSITION_MODEL=claude-sonnet-4-6
CLAUDE_STRUCTURE_MODEL=claude-sonnet-4-6
CLAUDE_USE_CACHING=true

# Risk
MIN_THESIS_PROBABILITY=70
MAX_SESSION_R_LOSS=3.0
MAX_DAILY_LOSS_PCT=0.01

# Version
BOT_VERSION=4.1.0

# Optional
FRED_API_KEY=

# Feature flags (all default true)
FEATURE_ORB_BIAS=true
FEATURE_BIDIRECTIONAL=true
FEATURE_BIAS_DECAY=true
FEATURE_OFI=true
FEATURE_DOM_ADVANCED=true
FEATURE_MTF_SCORE=true
FEATURE_THESIS_GATE=true
FEATURE_R_BUDGET=true
FEATURE_NEWS_GATE=true
FEATURE_DEAD_ZONE=true
FEATURE_DUAL_TRAIL=true
FEATURE_EARLY_EXIT=true
FEATURE_LEARNING_EOD=true
FEATURE_LEARNING_INJECT=true
FEATURE_DELTA_LIVE=true
```

---

## File Tree

```
mnq-ai-trader/
├── main.py               # Session state machine, 3-thread orchestration
├── config.py             # All configuration, env-overridable
├── claude_brain.py       # All Anthropic API calls, pre-filter
├── ibkr_feed.py          # IBKR connection, snapshot assembly (~1916 lines)
├── executor.py           # Bracket orders, protection loop, trailing
├── dashboard_writer.py   # Writes dashboard_data.json + price_data.json
├── memory_manager.py     # Session memory persistence
├── news_calendar.py      # Economic calendar + news danger zones
├── data_recorder.py      # JSONL recording for backtester
├── strategy_stats.py     # Per-strategy performance tracking
├── logger.py             # Python logging (Windows path hardcoded)
├── backtester.py         # Replay engine — regression check
├── ablation_runner.py    # Feature ablation testing
├── learning_session.py   # EOD learning orchestrator
├── version_manager.py    # Version tracking in .env
├── demo.py               # Dashboard demo, no IBKR/Claude needed
├── dashboard.html        # Full trading dashboard
├── mobile.html           # Mobile card layout ("DoBot")
├── .env.example          # Template for .env (never commit .env)
├── CLAUDE.md             # Instructions for AI assistants on this repo
├── PROJECT_SUMMARY.md    # This file
└── .gitignore
```

---

*Generated 2026-05-24. Covers codebase state after git automation removal (learning_session.py, version_manager.py, ablation_runner.py, .gitignore updated). Known bugs section is current as of this date.*
