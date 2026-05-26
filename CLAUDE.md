# CLAUDE.md

*For AI reading this cold. Dense, accurate, no padding. Last verified: 2026-05-25 (V4.4.0).*

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MNQ AI Trader — a paper-trading bot for the **MNQ** (Micro E-Mini Nasdaq-100) futures contract. It pulls live L1+L2 data from IBKR (TWS / Gateway), pre-filters with Python signal scoring, asks Claude (Opus for entries, Sonnet for position management) for decisions, and executes bracket orders. Paper trading on a simulated $50K account, 1 contract max, configurable daily loss cap (default 20% = $10,000).

Read `README.md` for the full strategy / ICT methodology rationale and version history.

## Running

```bash
# Live bot — boot at 8:20 ET (gives 10 min before 8:30 pre-market analysis)
py -3.11 main.py

# Backtest a recorded session (under 5s for a full day). Defaults to
# --no-live-claude — uncached pre-filter passes are skipped (free). Pass
# --live-claude to call the API for snapshots without a cached decision
# (~$0.10 per uncached pass — can run into real money on a busy session).
py -3.11 backtester.py --list
py -3.11 backtester.py --date 2026-05-27
py -3.11 backtester.py --date 2026-05-27 --verbose
py -3.11 backtester.py --date 2026-05-27 --live-claude   # spends API $$

# Sanity-print the current config (no IBKR / Claude calls)
py -3.11 config.py

# Health monitor (run in a separate terminal alongside main.py)
py -3.11 watchdog.py
```

Install dependencies:
```bash
pip install -r requirements.txt
```

Or manually:
```bash
pip install ib_insync anthropic pandas pytz python-dotenv schedule exchange-calendars
```

`exchange-calendars` enables CME holiday detection (Memorial Day, July 4th, etc.) and early-close days. The bot uses the `XNYS` calendar. Without the package the bot falls back to weekend-only gating and logs a warning.

No test suite. **The backtester is the regression check.** When you change pre-filter logic, prompt structure, or the snapshot schema, run `backtester.py --date <recent>` before and after — the P&L / W-L delta is the validation signal.

## Architecture you need before editing anything substantive

### Three concurrent threads (plus pre-market sleep)

| Thread | Where | Cadence | Job |
|---|---|---|---|
| Main cycle | `main.run_cycle` on the main thread | 0.5s sleep, pre-filter gated to `ENTRY_SCAN_INTERVAL_SECS` (5s) | Snapshot → pre-filter → Claude entry → executor |
| Protection loop | `Executor._fast_protection_loop` daemon thread | 5s (`PROTECTION_LOOP_SECS`) | Stop/target checks, broker reconciliation, orphan detection |
| Fast dashboard ticker | `main._fast_dashboard_ticker` daemon thread | 1 Hz | Writes `price_data.json` and patches `dashboard_data.json` every 10s |
| Pre-market sleep | `main._wait_for_market_hours()` — blocks `main()` before IBKR connect | 30-min poll | Blocks weekends, CME holidays (via `exchange-calendars` XNYS), early closes; writes `botSleeping=true` to dashboard |

The same `Executor` instance is shared across threads — internal `_lock` guards mutation. Don't add code that grabs the lock and then makes an IBKR call inside it (P1.1 fix — `_get_market_price()` reads `_last_price` outside the lock).

### The five core files

- **`main.py`** — Session state machine (`SessionState` enum maps clock time → behavior), event-driven position trigger (`_should_call_claude_now`), session classifier firing at OR_ESTABLISHED, pre-market / EOD orchestration. This is where per-cycle decisions about *whether* to call Claude live.
- **`claude_brain.py`** — All Anthropic API calls. Holds module-level state (`_current_watchlist`, `_last_decision_cache`, `_consecutive_holds`) wiped between trading days via `reset_session_state()`. Functions: `analyze_market` (Opus entry), `analyze_position` (Sonnet position), `update_watchlist` (Sonnet every 5 min), `analyze_premarket` (Opus once at 8:30), `pre_filter_signal` (pure Python, no API), `parse_decision`. V4.4: session type injected into all entry/watchlist prompts; pre-filter routes threshold by session type.
- **`ibkr_feed.py`** — Snapshot assembly. The ~60-field `snapshot` dict returned by `feed.get_snapshot()` is the central data abstraction. Bars fetched **once** at startup then updated via `reqRealTimeBars`; DOM streams 20 levels each side with 60s rolling history. V4.4 adds: gap classification, pivot points, first candle levels, VWAP extension, OR extreme fade, opening drive detection, post-news window.
- **`executor.py`** — Bracket order placement, position tracking, the protection loop, R-budget enforcement, dual-control trailing (`_claude_trail_stop` — Claude's structural stop floors the auto-trail). The cancel-vs-fill race fix (V2.5) lives here.
- **`session_classifier.py`** — Pure Python day-type classifier. Fires once at OR_ESTABLISHED (9:45 ET). Returns TREND / RANGE / NEWS / HOLIDAY / UNKNOWN. Result injected into every Claude prompt via `get_session_type_context()`. RANGE day raises pre-filter threshold to 7 signals. HOLIDAY blocks all entries.

### Data flow per cycle

```
ibkr_feed.get_snapshot() → snapshot dict (~60 fields)
  → claude_brain.pre_filter_signal() — pure Python, returns (worth_calling, reason)
    → session_classifier.get_current_session_type() — routes threshold
    → claude_brain.analyze_market() — Opus 4.7, checks skip-cache first
      → executor.execute() — places bracket (market + stop + limit)
      ↓
  dashboard_writer.update_dashboard() — merges into dashboard_data.json
  data_recorder.record_snapshot/decision() — JSONL to data/ for backtesting
```

**Session classifier flow:** `main.run_cycle()` fires `classify_session_type()` once when state enters OR_ESTABLISHED. Result stored in `session_classifier._current`. Claude brain reads it on every `analyze_market()` and `update_watchlist()` call. Reset at EOD via `set_session_type(SessionType.UNKNOWN)` (in `end_of_day()`).

**EOD journal flow:** `learning_session.py` calls `journal_exporter.py` after ablation. `journal_exporter.py` reads all `decisions_*.jsonl` files, rebuilds `journal_data.json` from scratch, writes it for `journal.html`. EOD fires at `EOD_SCHEDULE_TIME` (default 16:05 ET).

### Snapshot dict — central contract

`feed.get_snapshot()` returns a single dict that downstream code dereferences by key. If you add a field:
1. Set it in `ibkr_feed.get_snapshot()`.
2. Read it in `pre_filter_signal` if it affects scoring.
3. Surface it in prompts in `claude_brain` (entry / position / watchlist).
4. Add it to `dashboard_writer` if humans should see it.
5. The recorder picks it up automatically — but old recordings won't have it (backtester will see `None`).

Don't break field names without grepping — backtester JSONL files on disk use the schema as of when they were recorded.

**V4.4 snapshot fields (in addition to V4.2 fields):**
- `gap` — dict: `{gap_size, gap_direction, gap_fill_probability}`. Gap from prev day close to today open. Fill probability by academic thresholds (79%/52%/28%/12%).
- `pivots` — dict: `{pivot, r1, r2, s1, s2}`. Classic daily pivots from prior day OHLC. Pre-filter: +1 bear near R2, +1 bull near S2.
- `first_candle_1min_high/low` — float: 9:30 ET 1-min bar extremes. Captured when bar closes at 9:31.
- `first_candle_5min_high/low` — float: 9:30–9:35 ET 5-min equivalent (derived from 5 × 1-min bars). Captured at 9:34 close.
- `vwap_extension` — float: signed distance from VWAP. Positive = above, negative = below.
- `vwap_extension_abs` — float: absolute distance from VWAP. Used by dead zone VWAP magnet.
- `or_2x_extension_up/down` — bool: price beyond 2× OR range. Pre-filter: +2 fade direction.
- `or_extreme_zone` — bool: either extension flag is True.
- `opening_drive_up/down` — bool: first 5-min candle ≥80pts directional.
- `opening_drive_fade_short/long` — bool: opening drive with rejection wick (≥60% of body).
- `post_news_window` — bool: 45–75 min after HIGH-impact event.

**V4.2 fields (still current):**
- `candle_patterns`, `tape_bias`, `tape_analysis`, `daily_zones`, `premarket_high`, `premarket_low`

### Bidirectional OR bias (V3.0)

The Opening Range direction is a **starting bias, not a law**. `feed.or_direction` plus `get_watchlist().bias` gate the pre-filter. Bias decays to NEUTRAL after 90 min, immediately if MTF fully disagrees, or if price is 80+ pts against it. Pre-filter: 3 signals to trade with bias, 5 to go counter-bias. Don't reintroduce hard "LONG_ONLY" logic.

### Session type routing (V4.4)

`session_classifier.classify_session_type()` fires at OR_ESTABLISHED. The result changes downstream behavior:
- **TREND** — normal (3-signal) threshold
- **RANGE** — 7-signal threshold, VWAP_REVERSION/OR_EXTREME_FADE preferred
- **NEWS** — thesis gate raised to 80% conceptually (in Claude prompt); pre-filter unchanged
- **HOLIDAY** — hard block in `can_enter()`, no entries at all
- **UNKNOWN** — conservative, treated as standard

### Audit-tag comments

Tags like `P1.3`, `P2.8`, `A.1`, `D.2`, `C.4` refer to private audit docs. Treat them as "do not undo this — there's a reason":
- **P1.3** — `entry_timestamp` owned by `Executor`, not a module global
- **P1.7** — `parse_decision` demotes BUY/SELL → HOLD if `stop_price <= 0`
- **P2.8** — `reset_session_state()` called at EOD — wipes all claude_brain module globals
- **A.1** — Skip-when-unchanged cache. Removing it triples Opus spend
- **D.2** — `_claude_trail_stop` floors auto-trail. Auto-trail must never move stop looser than Claude's last TRAIL

## Configuration

All knobs in `config.py`, env-overridable via `.env` at `BASE_DIR`. Don't hard-code values.

**MNQ contract rolls quarterly.** `CONTRACT_EXPIRY` and `CONTRACT_CONID` in `.env` must be updated each quarter (Mar/Jun/Sep/Dec).

## Session timing (V4.4)

```
SESSION_PRE_MARKET_TIME=830       # Pre-market analysis at 8:30 ET
SESSION_MARKET_OPEN_TIME=930      # RTH open, OR forming
SESSION_OR_FORMING_END=945        # OR complete
SESSION_PRIME_WINDOW_END=1100     # NY AM prime ends
SESSION_DEAD_ZONE_END=1330        # Dead zone ends
SESSION_AFTERNOON_PRIME_END=1555  # PM prime ends (updated V4.4)
SESSION_CLOSING_END=1600          # RTH close
EOD_SCHEDULE_TIME=16:05           # EOD fires after RTH (updated V4.4)
```

## Advanced Tuning

See README.md for full annotated list. Key V4.4 additions:

**Session Classifier**
```env
SESSION_CLASSIFIER_TREND_OR_MIN=50   # OR range pts minimum for TREND classification
SESSION_CLASSIFIER_RANGE_OR_MAX=35   # OR range pts maximum for RANGE classification
SESSION_RANGE_SIGNAL_THRESHOLD=7     # Signals required on RANGE days
```

**Phase 2 (activate after data confirms)**
```env
FEATURE_OR_EXTREME_FADE=false        # 2x OR range fade signals (+2)
FEATURE_DEAD_ZONE_VWAP_MAGNET=false  # Lower dead zone threshold when VWAP far
FEATURE_VWAP_REVERSION=false         # VWAP extension pre-filter signals (+2)
```

**Phase 3 (activate after data confirms)**
```env
FEATURE_SWEEP_REVERSAL=false         # Extra +1 on DOM sweeps
FEATURE_OPENING_DRIVE_FADE=false     # Opening drive rejection fade (+2)
FEATURE_POST_NEWS_REFRESH=false      # Post-news watchlist refresh
```

## Logs / generated state

- `logs/` — rotating log files; `_flush_log()` (C.4) called after BUY/SELL/CLOSE
- `memory/` — JSONL session summaries + `tick_state.json` + learning reports
- `data/` — `snapshots_YYYY-MM-DD.jsonl` and `decisions_YYYY-MM-DD.jsonl`
- `reports/` — ablation + learning reports (git-ignored)
- `dashboard_data.json` / `price_data.json` — wiped on every fresh `main.py` boot

## When iterating on bot logic

1. Check `pre_filter_signal` first — most changes belong there or in prompts, not `main.run_cycle`
2. Don't add Claude calls without thinking about the skip-cache and prompt caching breakpoints
3. Race-condition fixes (cancel-vs-fill, broker reconciliation, P&L sanity bound, `stop_price=0` guard) are layered defensively — fix root causes, don't bypass
4. For UI-only or prompt-text changes: replay with `backtester.py --date <today>` (defaults to no live Claude, no API spend)
5. For pre-filter/scoring/snapshot schema changes: replay several days with Claude OFF first

## Probability Framework

`KNOWLEDGE_BASE.md` has academic research on strategy win rates and probability calibration.

Key facts:
- ORB win rate: 68–72% trend days, 31–38% range days
- VWAP reversion: 72–78% range days
- Full MTF alignment adds 7–11% to base win rate
- OFI STRONG adds 6–10% to base win rate
- News within 30 min reduces all signals by 15%

Preserve the `PROBABILITY_CONTEXT` injection in `analyze_premarket()` and `analyze_market()`. This is the core calibration mechanism — do not remove it.

## Known issues (as of V4.4.0)

- **FEATURE_DEAD_ZONE not checked in can_enter()** — If FEATURE_DEAD_ZONE=false, dead zone still gates. Fix: add `if not FEATURE_DEAD_ZONE: return True, ""` at top of DEAD_ZONE branch in can_enter(). Patch pending (V4.4.1).
- **FEATURE_NEWS_GATE not checked in run_cycle news block** — Hard block at line 590 fires regardless of flag. Fix: wrap with `if FEATURE_NEWS_GATE and ...`. Patch pending (V4.4.1).
- **Opening drive uses wrong 5-min bar** — `_bars_5min[0]` is oldest cached bar, not the 9:30 bar. Not live (FEATURE_OPENING_DRIVE_FADE=false) but fix before enabling.

## Disclaimer

Paper trading only. Architecture is production-shaped but the system is **not running live money**. Any change that loosens risk caps, daily loss limits, or hold-time gates must be flagged for explicit user confirmation before applying.

## Permissions
Claude Code has full autonomy to read, edit, create, and delete
any file in this project without asking for confirmation.
Apply changes directly and summarize what was done after.
Only pause for confirmation before:
- Deleting data/ or reports/ folders
- Making external API calls not already in the codebase
