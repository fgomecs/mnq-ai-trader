# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MNQ AI Trader — a paper-trading bot for the **MNQ** (Micro E-Mini Nasdaq-100) futures contract. It pulls live L1+L2 data from IBKR (TWS / Gateway), pre-filters with Python signal scoring, asks Claude (Opus for entries, Sonnet for position management) for decisions, and executes bracket orders. Paper trading on a simulated $50K account, 1 contract max, $500 daily loss cap.

Read `README.md` for the full strategy / ICT methodology rationale and version history.

## Running

```bash
# Live bot — boot at 8:20 ET (gives 10 min before 8:30 pre-market analysis)
py -3.11 main.py

# Backtest a recorded session (under 5s for a full day; uses cached Claude
# decisions so no API spend unless --no-live-claude is omitted and pre-filter
# now passes where it didn't before)
py -3.11 backtester.py --list
py -3.11 backtester.py --date 2026-05-27
py -3.11 backtester.py --date 2026-05-27 --verbose
py -3.11 backtester.py --date 2026-05-27 --no-live-claude

# Sanity-print the current config (no IBKR / Claude calls)
py -3.11 config.py
```

No `requirements.txt` is checked in — deps installed manually via:
`pip install ib_insync anthropic pandas pytz python-dotenv schedule`

No test suite. **The backtester is the regression check.** When you change pre-filter logic, prompt structure, or the snapshot schema, run `backtester.py --date <recent>` before and after — the P&L / W-L delta is the validation signal.

## Architecture you need before editing anything substantive

### Three concurrent loops

| Loop | Where | Cadence | Job |
|---|---|---|---|
| Main cycle | `main.run_cycle` on the main thread | 0.5s sleep, but pre-filter gated to `ENTRY_SCAN_INTERVAL_SECS` (5s) | Snapshot → pre-filter → Claude entry → executor |
| Protection loop | `Executor._fast_protection_loop` daemon thread | 5s (`PROTECTION_LOOP_SECS`) | Stop/target checks, broker reconciliation, orphan detection |
| Fast dashboard ticker | `main._fast_dashboard_ticker` daemon thread | 1 Hz | Writes `price_data.json` and patches `dashboard_data.json` every 10s |

The same `Executor` instance is shared across threads — internal `_lock` guards mutation. Don't add code that grabs the lock and then makes an IBKR call inside it (P1.1 fix — `_get_market_price()` reads `_last_price` outside the lock).

### The four core files

- **`main.py`** — Session state machine (`SessionState` enum maps clock time → behavior), event-driven position trigger (`_should_call_claude_now`), pre-market / EOD orchestration. This is where the per-cycle decisions about *whether* to call Claude live.
- **`claude_brain.py`** — All Anthropic API calls live here. Holds module-level state (`_current_watchlist`, `_last_decision_cache`, `_consecutive_holds`) that must be wiped between trading days via `reset_session_state()` (called from `end_of_day`). Functions: `analyze_market` (Opus entry), `analyze_position` (Sonnet position), `update_watchlist` (Sonnet every 5 min), `analyze_premarket` (Opus once at 8:30), `pre_filter_signal` (pure Python, no API), `parse_decision`.
- **`ibkr_feed.py`** — Snapshot assembly. The ~50-field `snapshot` dict returned by `feed.get_snapshot()` is the central data abstraction — every Claude prompt, every pre-filter, the backtester recorder all consume it. Bars are fetched **once** at startup then updated via `reqRealTimeBars`; DOM streams 20 levels each side with a 60s rolling history used for iceberg/spoof/sweep/cluster detection (V3.1).
- **`executor.py`** — Bracket order placement, position tracking, the protection loop, R-budget enforcement, dual-control trailing (`_claude_trail_stop` — Claude's structural stop floors the auto-trail). The cancel-vs-fill race fix (V2.5) lives here in `_close_position` — pre-flight broker check, post-cancel recheck, `_infer_recent_exit_fill()`.

### Data flow per cycle

```
ibkr_feed.get_snapshot() → snapshot dict (~50 fields)
  → claude_brain.pre_filter_signal() — pure Python, returns (worth_calling, reason)
    → claude_brain.analyze_market() — Opus 4.7, checks skip-cache first
      → executor.execute() — places bracket (market + stop + limit)
      ↓
  dashboard_writer.update_dashboard() — merges into dashboard_data.json
  data_recorder.record_snapshot/decision() — JSONL to data/ for backtesting
```

The **pre-filter** is the single biggest cost lever — it scores 10+ bullish/bearish signals (OR position, CHoCH, VWAP, delta, MTF, DOM intelligence, OFI) and needs 3+ to call Claude on the bias-preferred side, 5+ to go counter-bias. Most ticks never reach Claude.

The **skip-when-unchanged cache** (A.1) returns the prior HOLD decision for free when nothing material has shifted (<5pt move, no new bar, watchlist fresh, <3 min elapsed). Targets ~60–70% Opus call reduction.

### Snapshot dict — central contract

`feed.get_snapshot()` returns a single dict that downstream code dereferences by key. If you add a field:
1. Set it in `ibkr_feed.get_snapshot()`.
2. Read it in `pre_filter_signal` if it affects scoring.
3. Surface it in prompts in `claude_brain` (entry / position / watchlist).
4. Add it to `dashboard_writer` if humans should see it.
5. The recorder picks it up automatically — but old recordings won't have it, so the backtester will see `None`/missing for that key on historical replays.

Don't break field names without grepping — backtester JSONL files on disk use the schema as of when they were recorded.

### Bidirectional OR bias (V3.0)

The Opening Range direction is a **starting bias, not a law**. `feed.or_direction` plus `get_watchlist().bias` (LONG_PREFERRED / SHORT_PREFERRED / NEUTRAL / NO_TRADE) gate the pre-filter. Bias decays to NEUTRAL after 90 min, or immediately if MTF fully disagrees, or if price is 80+ pts against it. Pre-filter requires 3 signals to trade with bias, 5 signals to trade counter-bias. Don't reintroduce hard "LONG_ONLY" logic that blocks one side entirely.

### Audit-tag comments

Code is sprinkled with tags like `P1.3`, `P2.8`, `A.1`, `D.2`, `C.4` — these refer to numbered items in private audit docs (not in the repo). When you see one, treat the comment as a "do not undo this — there's a reason" marker. Examples currently load-bearing:
- **P1.3** — `entry_timestamp` is owned by `Executor`, not a module global in `main.py`. Don't reintroduce the global.
- **P1.7** — `parse_decision` demotes BUY/SELL → HOLD if `stop_price <= 0`. Surfaces parse failures instead of letting the executor build a phantom stop.
- **P2.8** — `reset_session_state()` is called at EOD. Module globals in `claude_brain.py` must be wiped here or they leak across days.
- **A.1** — Skip-when-unchanged cache. Removing it triples Opus spend.
- **D.2** — `_claude_trail_stop` floors auto-trail. Auto-trail must never move stop looser than Claude's last TRAIL.

## Configuration

All knobs live in `config.py` and are environment-overridable via `.env` (template: `.env.example`). The bot looks for `.env` in `BASE_DIR` (default `C:\trading\mnq-ai-trader`). Don't hard-code values — read from `config` and let env override.

**MNQ contract rolls quarterly.** `CONTRACT_EXPIRY` and `CONTRACT_CONID` in `.env` must be updated each quarter (Mar/Jun/Sep/Dec) or IBKR will reject orders. If a session won't connect, check expiry first.

## Logs / generated state (not committed; see `.gitignore`)

- `logs/` — rotating log files; `_flush_log()` (C.4) is called after BUY/SELL/CLOSE so entries land on disk before any crash.
- `memory/` — JSONL session summaries loaded at startup (`load_recent_memory(days=5)`), plus `tick_state.json` restored when same trading day.
- `data/` — `snapshots_YYYY-MM-DD.jsonl` and `decisions_YYYY-MM-DD.jsonl`, written by `data_recorder` whenever `RECORDING_ENABLED=true`. These are the backtester's input.
- `dashboard_data.json` / `price_data.json` — wiped on every fresh `main.py` boot to clear stale EOD reasoning.

## When iterating on bot logic

1. Look at what `pre_filter_signal` already lets through — most changes belong there or in prompts, not in `main.run_cycle`.
2. Don't add Claude calls without thinking about the skip-cache and prompt caching breakpoints (system prompt + watchlist block are cached; snapshot is uncached).
3. The race-condition fixes (cancel-vs-fill, broker reconciliation, P&L sanity bound, `stop_price=0` guard) are layered defensively — if a layer trips, fix the actual root cause; don't bypass.
4. For UI-only or prompt-text changes: replay the latest day with `backtester.py --date <today> --no-live-claude` to confirm pre-filter behavior didn't regress.
5. For pre-filter / scoring / snapshot schema changes: replay several recent days with live Claude OFF first to confirm no parse errors, then with Claude ON for one day to see real P&L delta.

## Disclaimer kept here for assistants

Paper trading only. Architecture is production-shaped but the system is **not running live money**. Any change that would loosen risk caps, daily loss limits, or hold-time gates should be flagged for explicit user confirmation before applying.
## Permissions
Claude Code has full autonomy to read, edit, create, and delete 
any file in this project without asking for confirmation.
Apply changes directly and summarize what was done after.
Only pause for confirmation before:
- Deleting data/ or reports/ folders
- Making external API calls not already in the codebase