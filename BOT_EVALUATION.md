# BOT_EVALUATION.md

*Ongoing performance evaluation and projection framework for MNQ AI Trader. Updated as live/backtest data accumulates.*

---

## Section 1 — Evaluation Methodology

Performance is measured against paper-trading P&L from `data/decisions_*.jsonl` replays via `backtester.py`. Win rate and R:R are computed per-session and rolling 20-trade. Hard daily loss cap (configurable via MAX_DAILY_LOSS_PCT in .env) and 1-contract sizing constrain variance.

---

## Section 2 — Signal Quality Baseline

Pre-filter pass rate target: <15% of ticks reach Claude. Claude BUY/SELL rate target: 20–40% of calls that pass pre-filter. Combined signal selectivity keeps expected-value per trade high.

---

## Section 3 — Entry Score Distribution

Tracks how often each pre-filter signal fires. Used to identify dominant contributors and signals that rarely trigger (candidates for removal or threshold adjustment).

---

## Section 4 — Win Rate by Session Phase

| Phase | Expected Win Rate | Notes |
|---|---|---|
| Morning (9:30–11:00 ET) | 62–70% | Peak liquidity, ORB + ICT setups |
| Dead Zone (11:00–13:30 ET) | 48–55% | Elevated threshold (8 signals) required |
| Afternoon (13:30–16:00 ET) | 55–62% | Re-acceleration setups, lower volume |

---

## Section 5 — OR Day-Type Accuracy

Accuracy of the Opening Range day-type classification (trend vs range) is a key upstream variable. Misclassifying a range day as trend leads to ORB fade entries that the pre-filter should catch but may not.

---

## Section 6 — Claude Decision Quality

Tracks BUY/SELL vs actual outcome by thesis type (FVG, OB, CHoCH, VWAP, ORB). Flags cases where stated thesis probability (per `KNOWLEDGE_BASE.md`) diverges from realized win rate by >10 pp.

---

## Section 7 — Stop and Target Calibration

Average ticks captured vs maximum adverse excursion (MAE) per trade. Identifies whether stops are too tight (stopped before thesis plays out) or targets are too conservative (left too much on the table).

---

## Section 8 — R:R Realized vs Planned

| Metric | Target | Notes |
|---|---|---|
| Planned R:R (at entry) | 3:1–5:1 | Set by Claude stop + target prices |
| Realized R:R (at exit) | ≥2:1 | After trail adjustments and early exits |
| Capture ratio | ≥65% | Realized / planned |

---

## Section 9 — Risk and Drawdown

| Metric | Limit | Notes |
|---|---|---|
| Daily loss cap | MAX_DAILY_LOSS_PCT × ACCOUNT_SIZE | Configurable via .env |
| Max consecutive losses | 3 | Soft warning logged |
| Max drawdown (rolling 10-day) | $1,500 | Review signal thresholds if breached |
| Sharpe (daily P&L, annualized) | Target ≥1.5 | Below 1.0 triggers strategy review |

---

## Section 10 — Performance Projection

Projected per-trade expected value at the bot's target win rate (55–70%) and R:R range (3:1–5:1). Trade value assumes $0.50 per tick (MNQ micro contract), 1 contract, 100-tick stop (= $50 risk unit — matches SCALP_STOP_TICKS=100 default; adjust for actual stop size).

**Summary table — expected value per trade (normalized to 1R = $50 risk):**

| Win Rate | R:R 2:1 | R:R 3:1 | R:R 4:1 | R:R 5:1 |
|---|---|---|---|---|
| 50% | $0 | +$25 | +$50 | +$75 |
| 55% | +$10 | +$37.50 | +$65 | +$92.50 |
| 60% | +$20 | +$50 | +$80 | +$110 |
| 65% | +$30 | +$62.50 | +$95 | +$127.50 |
| 70% | +$40 | +$75 | +$110 | +$145 |

*1R = $50 risk per trade example. Scale proportionally for actual stop size.*

**R:R Sensitivity Analysis:**

At estimated 60% win rate:
- 2:1 R:R: Expected value = +$20 per trade
- 3:1 R:R: Expected value = +$40 per trade (+100%)
- 4:1 R:R: Expected value = +$56 per trade (+180%)
- 5:1 R:R: Expected value = +$70 per trade (+250%)

Raising minimum R:R from 2:1 to 3:1 doubles expected value per trade with zero change to win rate. This is the highest-leverage improvement available that requires no new features or signals.
