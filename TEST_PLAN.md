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

## Phase 1 — TODAY (Regression Suite)
One test per bug fixed on 2026-05-26:

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

## Phase 2 — THIS WEEK (Core Logic Suite)
Build after first real trading session 2026-05-27.
- test_parse_decision.py — 14 scenarios
- test_pre_filter.py — 15 scenarios  
- test_executor.py — 16 scenarios
- test_risk.py — 12 scenarios

## Phase 3 — NEXT WEEK (Integration Suite)
Build after 3 real sessions with data.
- test_session.py — state machine and timing
- test_dashboard.py — output integrity
- test_backtester.py — replay pipeline
- test_commission.py — full commission flow

## Phase 4 — ONGOING (Advanced Suite)
- Market data edge cases
- API resilience
- EOD pipeline
- Stress tests

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
| Phase | Target | Timeline |
|-------|--------|----------|
| Phase 1 Regression | BUG-001 to BUG-010 | Today |
| Phase 2 Core Logic | parse pre_filter executor risk | This week |
| Phase 3 Integration | session dashboard backtester | Next week |
| Phase 4 Advanced | edge cases resilience stress | Ongoing |
| Long-term | 70% line coverage | 1 month |

*Last updated: 2026-05-26*
