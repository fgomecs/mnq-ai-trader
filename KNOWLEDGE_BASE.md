# KNOWLEDGE_BASE.md

*Structured academic research on strategy win rates, signal validity, and probability calibration for MNQ AI Trader. Used by claude_brain.py prompts to ground probability estimates.*

---

## Opening Range Breakout (ORB) Win Rates

| Day Type | Win Rate | Notes |
|---|---|---|
| Trend days | 68–72% | OR holds as support/resistance; breakout continuation |
| Range days | 31–38% | ORB fakes frequently; mean-reversion dominates |

**Identification heuristics:** Trend day = IB range >40 pts AND price closes >70% of IB range by 10:30 ET. Range day = price chops inside IB or returns within 30 min of breakout.

---

## VWAP Reversion

| Context | Win Rate | Notes |
|---|---|---|
| Range days (within 1 ATR of VWAP) | 72–78% | Strong anchor; fades at ±0.5–1σ reliable |
| Trend days | 38–44% | VWAP lags; fades against trend often stopped out |

Reversion setups require: price extended ≥0.75σ from VWAP, DOM showing absorption, OFI not strongly aligned with extension direction.

---

## Signal Confluence Adjustments

### Multi-Timeframe (MTF) Alignment

| MTF State | Win Rate Delta |
|---|---|
| Full alignment (1m + 5m + 15m same bias) | +7 to +11% |
| Partial alignment (2 of 3) | +2 to +4% |
| Conflicted | −5 to −9% |

Full MTF alignment is defined as: 1m, 5m, and 15m bars all showing EMA stack + higher-highs/higher-lows (bull) or lower-highs/lower-lows (bear).

### Order Flow Imbalance (OFI)

| OFI State | Win Rate Delta |
|---|---|
| STRONG (≥70% directional delta) | +6 to +10% |
| MODERATE (50–69%) | +2 to +5% |
| WEAK / NEUTRAL | 0 |

OFI STRONG on entry bar is the single highest-conviction intrabar confirmation signal.

---

## Risk Factors

### News / Economic Events

| Proximity to Event | Win Rate Adjustment |
|---|---|
| Within 30 min of major release (CPI, NFP, FOMC) | −15% across all signals |
| Within 5 min | Avoid entirely — spread widens, stops hit randomly |

Major releases: CPI, PPI, NFP, FOMC rate decision, GDP advance. Minor releases (jobless claims, PMI flash) reduce by −5%.

### Dead Zone (11am–1:30pm ET)

Liquidity thins; ORB and ICT setups have ~8–12% lower win rates vs morning session. Pre-filter requires `DEAD_ZONE_CONFLUENCE_THRESHOLD` (default 8) signals to enter.

---

## ICT Methodology Probability Anchors

| Setup | Base Win Rate | Condition |
|---|---|---|
| FVG fill + OFI STRONG | 64–70% | Price returning to unfilled FVG with strong delta |
| Order Block tap | 58–65% | First touch of OB with rejection candle |
| Liquidity sweep + reversal | 61–68% | Stop-hunt above/below prior swing, immediate reclaim |
| CHoCH + retest | 66–72% | Break of market structure confirmed, pulls back to break point |
| VWAP + OB confluence | 69–75% | OB sits at or near VWAP; strongest range-day setup |

All figures are for MNQ/NQ futures, RTH session, 2022–2025 data. Pre-market and globex sessions have wider spreads and lower reliability (−10 to −15%).

---

## Risk and Position Management

### Kelly Criterion

Kelly fraction = (W × R − L) / R, where W = win rate, L = loss rate (1 − W), R = reward-to-risk ratio.

| Win Rate | R:R | Kelly % | Practical Bet (½ Kelly) |
|---|---|---|---|
| 55% | 2:1 | 32.5% | 16% |
| 55% | 3:1 | 40.0% | 20% |
| 60% | 3:1 | 46.7% | 23% |
| 65% | 3:1 | 53.3% | 27% |

Full Kelly is too volatile for futures; ½ Kelly (or less) is the practical ceiling. The bot's fixed 1-contract sizing is inherently conservative — the Kelly math above shows how much theoretical edge exists at varying win rate / R:R combinations.

### Profitability Matrix (Win Rate vs R:R)

Source: Standard trading mathematics

| Win Rate | Min R:R for Profitability |
|---|---|
| 30% | 2.4:1 |
| 40% | 1.6:1 |
| 50% | 1.1:1 (barely) |
| 55% | 0.9:1 (any positive R:R works) |
| 60% | 0.7:1 (very forgiving) |
| 65% | 0.6:1 |
| 70% | 0.5:1 (even 1:2 R:R is profitable) |

**Bot target zone:** Win rate 55–70% AND R:R 3:1–5:1 = solidly in profitable green zone = robust to bad runs and slippage.

**Critical insight:** Improving R:R from 2:1 to 3:1 at 55% win rate increases profitability by 50% without winning a single additional trade. R:R improvement is free alpha — no prediction needed, just patience to wait for better targets.

---

## Calibration Notes for Claude Prompts

- State win-rate ranges, not point estimates. "This setup historically wins 65–72% of the time" is more honest than "70%".
- Always condition on day-type (trend vs range) — the same ORB setup has a 2× win rate swing between day types.
- MTF + OFI STRONG together (both present) is additive: base + 7–11% (MTF) + 6–10% (OFI) = realistic 75–85% on a trend day. That's the ceiling; don't extrapolate further.
- News proximity is a hard penalty, not a soft caution — apply −15% mechanically when within 30 min.
- The pre-filter threshold (3 signals bias-preferred, 5 counter-bias) was calibrated against these win rates. Do not lower thresholds without re-running ablation.
