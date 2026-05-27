# Changelog

All notable changes to MNQ AI Trader are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [4.5.1] — 2026-05-27

### Reverted — IBKR Gateway firehose mitigation
**Production incident 2026-05-27 09:14 ET**: Gateway killed the socket after
the EWriter buffer grew to ~5MB. The combination of `reqMktDepth(numRows=40)`,
`reqRealTimeBars(barSize=1)`, and `reqTickByTickData("AllLast")` produced
more wire traffic than the asyncio loop could drain.

- `reqMktDepth` numRows reverted **40 → 20** (back to V3.x default).
- `reqRealTimeBars` barSize reverted **1 → 5** (also restores IBKR API
  spec compliance — barSize=1 was unofficial and frequently rejected by TWS).
- Pin tests added (`tests/test_firehose_limits.py`) to prevent silent
  re-bumping.

### Added — DOM signal throttling (defensive)
- `DOM_THROTTLE_SECS` (default 0.1) caches processed DOM features so a
  burst of `get_snapshot()` calls doesn't multiply CPU cost.
  `_compute_dom_signals` → wraps `_compute_dom_signals_impl`;
  `_get_live_dom` → wraps `_get_live_dom_impl`.
- `DOM_UPDATE_RATE_WARN_HZ` (default 200) — wire-rate monitor logs a
  one-shot warning when sustained `dom_ticker.updateEvent` rate exceeds
  threshold, naming the firehose culprits so the operator can lower.
- 9 tests in `tests/test_dom_throttle.py` covering cache hit/miss/disable,
  counter, warning fire/latch/no-fire-below-threshold, env knobs.

## [4.5.0] — 2026-05-26

### Added — Broker commission capture (end-to-end)
- `Executor` subscribes to `commissionReportEvent` and accumulates real per-fill
  commissions into `_broker_commission_pending`. Dedupe by `execId`.
- `_record_pnl` drains the pending bucket on every close, tags the trade row
  with `commission_source` (`"broker"` / `"none"`), and deducts from PnL.
- 0.3s `ib.sleep` before each `_record_pnl` so the exit fill's commission lands
  in the right trade's bucket (5 call sites).
- Boot-time priming of `_seen_exec_ids` from `ib.fills()` before handler
  subscription — IBKR's startup replay no longer pollutes the bucket.
- Reconnect priming wired to `connectedEvent`; `mark_disconnect()` records a
  UTC cutoff so fills that arrived during the outage are NOT primed and their
  replayed `commissionReport` flows through normally.
- Hardened timestamp comparison via `_coerce_utc` — accepts naive datetimes,
  ISO strings, epoch numbers; unknown shapes don't crash the reprime.

### Added — Trade persistence
- `data_recorder.record_trade()` now writes a `type="trade"` row including
  `commission`, `commission_source`, `hold_seconds`, `ts_et`, `action`, and
  both `entry`/`entry_price` spellings.
- Wired through `Executor._record_pnl` (single call site) so all five close
  paths reach the JSONL automatically.
- `journal_exporter.build_journal()` now emits a `trades[]` array of the
  19 fields the journal UI consumes (date, time, direction, setup, entry,
  exit, hold_time_min, pre_filter_score, ofi_signal, thesis_prob,
  session_bias, net_pnl, pnl, result, entry_time, exit_time, commission,
  commission_source, exit_reason), plus a new `commission_sources` section
  with per-source trades/wins/losses/pnl/win_rate/commission_total.

### Added — Dashboard surface
- `dashboard_data.json` carries `dailyPnl`, `dailyCommissions`, `dailyNetPnl`.
- Per-trade rows include `commission` and `commission_source`.
- Mobile dashboard (`mobile.html`): "Net ±$X · Comm $Y" line under headline PnL.
- Ticker (`ticker.html`): small amber "comm $X.XX" under Daily P&L stat,
  sourced from `dashboard_data.json` (full write) — not `price_data.json`.
- Journal HTML + Journal Mobile: new "Commission Sources" card with columns
  Source / Trades / Win% / P&L / Commission, sorted broker → simulated → none.

### Added — Test infrastructure (139 tests, 14 files)
- `tests/conftest.py`: `mock_snapshot`, `mock_decision`, `mock_watchlist`
  (LONG_PREFERRED default), `sample_env`.
- `tests/test_regression.py` (13): one regression per fixed bug BUG-001
  through BUG-010 (some split into two cases for fuller coverage).
- `tests/test_smoke.py` (10): imports, config env-overrides for
  MAX_CONTRACTS and CLAUDE_ENTRY_MODEL, feature flags, dashboard write.
- `tests/test_parse_decision.py` (18): every parser scenario — truncation,
  prose preamble, code fences, parametrized CONFIDENCE/MODE,
  parenthetical-number bug.
- `tests/test_pre_filter.py` (13): bias gates, hard blocks, RANGE-day
  threshold, OFI feature on/off, OR entry zone.
- `tests/test_executor.py` (15): `_record_pnl` 4 directions + sanity reject +
  commission deduction + trades_today, `_reconcile_overfill` 3 cases,
  commission dedupe, reprime, mark_disconnect, outage-window preservation.
- `tests/test_risk.py` (9): MAX_CONTRACTS cap, MIN_THESIS_PROBABILITY 3 cases,
  consecutive_losses, FEATURE_THESIS_GATE bypass, FEATURE_R_BUDGET.
- `tests/test_session.py` (8): every session-state boundary, FEATURE_DEAD_ZONE
  on/off, HOLIDAY hard block.
- `tests/test_dashboard.py` (7): JSON validity, daily commission / net PnL
  fields, trades array passthrough, stale-state clear.
- `tests/test_backtester.py` (8): loaders, infer_session_bias, end-to-end
  mini backtest with seeded bias, zero-API-call default, SimExecutor cap,
  ASCII print_report.
- `tests/test_commission.py` (8): full commission pipeline.
- `tests/test_market_data.py` (9): empty/None/extreme/garbage snapshots.
- `tests/test_api_resilience.py` (9): empty/None/JSON-only/whitespace/
  out-of-range/unknown-token inputs to parse_decision.
- `tests/test_eod.py` (6): empty trades, all-loss, multi-day equity curve,
  end_of_day callable.
- `tests/test_stress.py` (4): 500 _record_pnl calls, 2000 commissionReports
  (dedupe idempotent), 1000 pre_filter under 5s, 50KB parse under 2s.

### Added — Test discipline
- `Makefile` at project root: `make test` / `make smoke` / `make regression`.
- `TEST_PLAN.md`: quality roadmap with Phase 1–4 punch list.
- `CLAUDE.md` `## Development Discipline` and `## Pre-Commit Checklist`
  sections — every code change must include pytest tests, tests must
  pass before commit, regression test written before the code fix.

### Changed — Library migration
- **ib_insync → ib_async** across `executor.py`, `ibkr_feed.py`, `main.py`,
  and `requirements.txt`. Comments / docstrings swept for consistency.

### Changed — Data subscription
- DOM levels increased from 20 to 40 (`reqMktDepth(numRows=40)`).
- Real-time bars switched from 5-second to 1-second
  (`reqRealTimeBars(barSize=1)`). NOTE: IBKR API officially supports only
  5-second bars on this call — if TWS rejects at runtime, revert to 5 or
  aggregate from the existing `reqTickByTickData("AllLast")` stream.
- Tick-by-tick stream — already wired at `ibkr_feed.py:595`; verified.

### Changed — Backtester
- Seeds `claude_brain._session_watchlist` with `infer_session_bias()`
  before the replay loop. Previously every backtest reported 0/N pre-filter
  passes because the bias module-global was never set.
- Default flipped to `--no-live-claude` — opt-in `--live-claude` for API
  spend. Plain `py -3.11 backtester.py --date <date>` is now safe.
- `print_report` uses ASCII separators only (no more cp1252 crash).

### Changed — Operational
- `EOD_SCHEDULE_TIME` default `15:30` → `15:55`, freeing the 15:30–15:55
  CLOSING window as exit-only management time.
- `run_premarket()` clears `dashboard_data.json` once per trading day so
  yesterday's EOD state doesn't bleed through overnight.
- `config.py` `load_dotenv()` now reads `.env` from `config.py`'s own
  directory rather than cwd — bot works correctly regardless of where
  it's invoked from.

### Fixed
- **BUG-001 follow-up** (`memory_manager.save_daily_summary` per-trade detail
  crashed on `pnl=None`): defensive `or 0.0` / explicit None check on
  `pnl` / `entry` / `exit` before f-string formatting.
- **BUG-002** (`_extract_int` concatenated all digits — `"65 (was 70)"`
  became `6570` → clamped to 100, letting low-confidence trades through
  the thesis gate): now anchors to first contiguous digit run via regex.
- **`parse_decision(None)` crashed on `.strip()`**: defensive type guard
  at function entry returns the safe HOLD default.
- **`pre_filter_signal` crashed when `snapshot["ofi"] = None`** (also
  `daily_zones`, `candle_patterns`, `last_price`, `choch`): `or {}` /
  `or ""` / `or 0` guards on every read site.

### Risk caps untouched
No changes to `MAX_DAILY_LOSS_USD`, `MAX_SESSION_R_LOSS`, `MAX_CONTRACTS`,
hold-time gates, or news-blackout gate logic. The `EOD_SCHEDULE_TIME` shift
and dashboard clear are operational, not risk-policy.

## [4.4.x] — 2026-05-25 and earlier
See `git log` for the prior cadence; this is the first formal changelog
entry. Older bugfixes are referenced in commit messages.
