# MNQ AI Trader — Test Plan & Quality Roadmap
*Version 1.0 | Created 2026-05-26 | Owner: DoBot Project*

## The Golden Rules
1. No fix without a test — every bug fixed gets a regression test immediately
2. No feature without a test — write the test first, then the feature (TDD)
3. Green before boot — make smoke must pass before starting main.py
4. Green before merge — make test must pass before any git push
5. Claude Code ends with tests — every prompt that changes code adds: "and write pytest tests covering this change"

## Development Discipline (Claude Code Rules)
- Every code change must include pytest tests in tests/
- After writing tests always run: py -3.11 -m pytest tests/ -v
- If tests fail fix the code until they pass — never commit failing tests
- Never ask the user to run tests manually — always run them yourself
- If a bug is found write the regression test BEFORE fixing the code
- Commit message must say how many tests were added and if green

## Phase 1 — Regression Suite — **✅ COMPLETED 2026-05-26**
One test per bug fixed on 2026-05-26. All 10 named bugs covered by 13 tests
in `tests/test_regression.py` (BUG-005, BUG-007, BUG-008 each split into 2
cases for fuller coverage). Status: **green**.

| Bug | Description | Test |
|-----|-------------|------|
| BUG-001 | max_tokens=500 truncated responses | parse_decision on 500-token truncated response returns HOLD not crash |
| BUG-002 | MIN_THESIS_PROBABILITY=1 blocked all trades | Thesis gate passes at prob=72 with threshold=0.55 |
| BUG-003 | Wrong model string claude-opus-4-7 | Config loads CLAUDE_ENTRY_MODEL from env correctly |
| BUG-004 | NameError in _enter_trade | execute() with BUY decision does not raise NameError |
| BUG-005 | 2-contract overfill race condition | _reconcile_overfill with broker=2 intended=1 flattens to 1 |
| BUG-006 | pnl=None crashed save_daily_summary | save_daily_summary with pnl=None does not raise TypeError |
| BUG-007 | Backtester 0/2959 passes no watchlist | Backtest with seeded NEUTRAL bias shows more than 0 passes |
| BUG-008 | Commission replay inflated PnL | Dedupe set primed with 24 execIds drops replayed commissions |
| BUG-009 | DECISION after reasoning caused parse failure | Response with DECISION on line 1 parses correctly |
| BUG-010 | Backtester default called live API | run_backtest makes 0 API calls by default |

## Phase 2 — Core Logic Suite — **✅ COMPLETED 2026-05-26**
- `test_parse_decision.py` — 18 tests (14 scenarios + parametrized expansions)
- `test_pre_filter.py` — 13 tests
- `test_executor.py` — 15 tests
- `test_risk.py` — 9 tests

Surfaced + fixed BUG-002 follow-up: `_extract_int` concatenated digits,
made `"65 (was 70)"` parse as 100; regression test forced fix to first-
contiguous-digit-run regex.

## Phase 3 — Integration Suite — **✅ COMPLETED 2026-05-26**
- `test_session.py` — 8 tests covering every session boundary + HOLIDAY
- `test_dashboard.py` — 7 tests covering JSON validity + commission fields
- `test_backtester.py` — 8 tests covering loaders + mini-replay + ASCII
- `test_commission.py` — 8 tests covering the full pipeline

## Phase 4 — Advanced Suite — **✅ COMPLETED 2026-05-26 (initial pass)**
- `test_market_data.py` — 9 tests (empty/None/extreme/garbage snapshots)
- `test_api_resilience.py` — 9 tests (parse_decision robustness)
- `test_eod.py` — 6 tests (empty/loss session, multi-day equity)
- `test_stress.py` — 4 tests (500 trades, 2000 commissions, 1000 pre_filter, 50KB parse)

Surfaced + fixed two real defensiveness bugs:
- `parse_decision(None)` crashed on `.strip()` → added type guard
- `pre_filter_signal` crashed when `snapshot["ofi"]==None` → added `or {}`
  guards on `ofi`, `daily_zones`, `candle_patterns`, `last_price`, `choch`.

**Phase 4 follow-up — IB mock harness for `executor.py` / `ibkr_feed.py`:**
Current coverage is 26% / 5% respectively because both modules are tightly
coupled to live IBKR objects (`Trade`, `Ticker`, `Fill`, async events). To
reach the 70% long-term target we need a proper mock harness for those
ib_async types — record/replay of `placeOrder`, fill events, `Ticker`
updateEvents, DOM updates. Estimate: 1-2 days of harness work + another day
to retrofit ~30 tests. Not blocking.

## Pre-Session Checklist
Run every morning before booting:
1. make smoke — must be green
2. Verify .env: model, contracts, probability, dead zone
3. Boot: py -3.11 main.py
4. Confirm: commissionReportEvent handler registered
5. Confirm: entry:claude-sonnet-4-6

## Post-Bug Discipline
1. Write regression test first — reproduce the bug
2. Confirm test fails with buggy code
3. Fix the code
4. Confirm test passes
5. Add to test_regression.py with BUG-XXX comment
6. Never remove regression tests — they are permanent

## Coverage Targets
| Phase | Target | Status |
|-------|--------|--------|
| Phase 1 Regression | BUG-001 to BUG-010 | ✅ Complete — 13 tests |
| Phase 2 Core Logic | parse / pre_filter / executor / risk | ✅ Complete — 55 tests |
| Phase 3 Integration | session / dashboard / backtester / commission | ✅ Complete — 31 tests |
| Phase 4 Advanced | edge cases / resilience / EOD / stress | ✅ Complete — 29 tests |
| **Total** | **139 tests, 14 files, 29% line coverage** | **✅ Green** |
| Long-term | 70% line coverage | 🚧 IB mock harness pending for executor/ibkr_feed |

## Coverage breakdown (v4.5.0)

```
Module                 Statements  Cover
config.py                     202    93%
journal_exporter.py           292    83%
notifier.py                    74    65%
dashboard_writer.py           108    58%
data_recorder.py              113    56%
backtester.py                 285    52%
logger.py                      28    50%
memory_manager.py             184    45%
claude_brain.py               730    39%
session_classifier.py          48    38%
executor.py                   713    26%    ← needs IB mock harness
main.py                       640    15%    ← needs IB mock harness
strategy_stats.py             215    13%
news_calendar.py              279    10%
ibkr_feed.py                 1509     5%    ← needs IB mock harness
─────────────────────────────────────────
TOTAL                        5420    29%
```

`learning_session.py` is now imported by a `tests/test_smoke.py` test so
the prior `module-not-imported` warning is silenced.

*Last updated: 2026-05-27*
