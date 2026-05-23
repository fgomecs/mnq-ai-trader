"""
ablation_runner.py — V4.1

Ablation backtest engine for MNQ AI Trader.

Ablation testing: run the backtest with each feature disabled one at a time
to measure its isolated contribution to daily P&L. This tells you:
  - Which features are actually helping
  - Which features are hurting (surprising but common)
  - Which features are neutral (candidates for removal)

Strategy:
  1. Run baseline (all features ON)
  2. For each feature, disable it and run backtest
  3. Compare each result to baseline
  4. Return ranked report: "OFI added +$34 | DOM_ADVANCED added +$12 | DEAD_ZONE hurt -$8"

Called by learning_session.py at EOD.
Can also be run manually: py -3.11 ablation_runner.py --date 2026-05-27
"""

import os
import sys
import copy
import argparse
from datetime import datetime, date
from pathlib import Path
from typing import Optional

# Patch environment before importing backtester (which imports config)
_BASE_DIR = os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader")
sys.path.insert(0, _BASE_DIR)

# ── Feature flag definitions ─────────────────────────────────
# Maps env var name → human label → what it controls
ABLATION_FLAGS = {
    "FEATURE_ORB_BIAS":      "ORB Bias",
    "FEATURE_BIDIRECTIONAL": "Bidirectional",
    "FEATURE_BIAS_DECAY":    "Bias Decay",
    "FEATURE_OFI":           "OFI Score",
    "FEATURE_DOM_ADVANCED":  "DOM Advanced",
    "FEATURE_MTF_SCORE":     "MTF Score",
    "FEATURE_THESIS_GATE":   "Thesis Gate",
    "FEATURE_R_BUDGET":      "R-Budget",
    "FEATURE_NEWS_GATE":     "News Gate",
    "FEATURE_DEAD_ZONE":     "Dead Zone",
    "FEATURE_DUAL_TRAIL":    "Dual Trail",
    "FEATURE_EARLY_EXIT":    "Early Exit",
}

# Safety features never toggled in ablation
SAFETY_FEATURES = {
    "FEATURE_LEARNING_EOD",
    "FEATURE_LEARNING_INJECT",
    "FEATURE_DELTA_LIVE",
}


def _set_env_flags(overrides: dict) -> None:
    """Temporarily override env vars for this process."""
    for key, val in overrides.items():
        os.environ[key] = "true" if val else "false"


def _reset_env_flags() -> None:
    """Reset all feature flags to true (baseline)."""
    for key in ABLATION_FLAGS:
        os.environ[key] = "true"


def _run_backtest_with_flags(date_str: str, flag_overrides: dict) -> Optional[dict]:
    """
    Run backtester with specific feature flags overridden.
    Returns results dict or None on error.
    """
    # Override env
    _set_env_flags(flag_overrides)

    # Force reload of config module (it reads env at import time)
    if "config" in sys.modules:
        del sys.modules["config"]
    if "backtester" in sys.modules:
        del sys.modules["backtester"]
    if "claude_brain" in sys.modules:
        del sys.modules["claude_brain"]

    try:
        import backtester
        results = backtester.run_backtest(
            date_str,
            verbose=False,
            use_claude_for_uncached=False,   # ablation is always free
        )
        return results
    except Exception as e:
        print(f"    [ablation] Backtest error: {e}")
        return None


def run_ablation(date_str: str, verbose: bool = False) -> dict:
    """
    Run full ablation test for a given date.

    Returns dict with:
      baseline:    results with all features ON
      ablations:   {feature_label: {results, delta_pnl, delta_trades, verdict}}
      report_text: human-readable markdown report
    """
    print(f"\n{'='*60}")
    print(f"ABLATION TEST — {date_str}")
    print(f"{'='*60}")

    # ── Step 1: Baseline (all ON) ─────────────────────────────
    print("\n[1/2] Running baseline (all features ON)...")
    _reset_env_flags()
    baseline = _run_backtest_with_flags(date_str, {})

    if not baseline:
        print("    Baseline failed — cannot run ablation")
        return {"error": "baseline failed"}

    base_pnl    = baseline["daily_pnl"]
    base_trades = baseline["trade_count"]
    base_wr     = baseline["win_rate"]

    print(f"    Baseline: {base_trades}T {base_wr}%WR ${base_pnl:+.2f}")

    # ── Step 2: Disable each feature one at a time ────────────
    print(f"\n[2/2] Running {len(ABLATION_FLAGS)} ablation tests...")
    ablations = {}

    for env_key, label in ABLATION_FLAGS.items():
        # Build override: all ON except this one
        _reset_env_flags()
        overrides = {env_key: False}
        _set_env_flags(overrides)

        results = _run_backtest_with_flags(date_str, overrides)

        if results is None:
            ablations[label] = {
                "results":      None,
                "delta_pnl":    0,
                "delta_trades": 0,
                "verdict":      "ERROR",
                "env_key":      env_key,
            }
            continue

        delta_pnl    = base_pnl - results["daily_pnl"]   # positive = feature helped
        delta_trades = base_trades - results["trade_count"]

        # Verdict
        if abs(delta_pnl) < 2.0:
            verdict = "NEUTRAL"
        elif delta_pnl > 0:
            verdict = "HELPS"
        else:
            verdict = "HURTS"

        ablations[label] = {
            "results":      results,
            "delta_pnl":    round(delta_pnl, 2),
            "delta_trades": delta_trades,
            "verdict":      verdict,
            "env_key":      env_key,
        }

        sign = "+" if delta_pnl >= 0 else ""
        print(f"    {label:<20} | Removing it: ${results['daily_pnl']:+.2f} "
              f"| Feature contribution: {sign}{delta_pnl:.2f} | {verdict}")

    # Reset to baseline
    _reset_env_flags()

    # ── Step 3: Build report ──────────────────────────────────
    report = _build_report(date_str, baseline, ablations)

    return {
        "date":      date_str,
        "baseline":  baseline,
        "ablations": ablations,
        "report":    report,
    }


def _build_report(date_str: str, baseline: dict, ablations: dict) -> str:
    """Generate markdown report from ablation results."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M ET")

    lines = [
        f"# Ablation Report — {date_str}",
        f"*Generated: {now}*",
        "",
        "## Baseline (all features ON)",
        f"- Trades: {baseline['trade_count']} | Wins: {baseline['wins']} | "
        f"Losses: {baseline['losses']} | Win rate: {baseline['win_rate']}%",
        f"- **Daily P&L: ${baseline['daily_pnl']:+.2f}**",
        "",
        "## Feature Contribution (removing each feature one at a time)",
        "",
        "| Feature | Without It | Contribution | Verdict |",
        "|---------|-----------|-------------|---------|",
    ]

    # Sort by contribution (most helpful first)
    sorted_ablations = sorted(
        ablations.items(),
        key=lambda x: x[1]["delta_pnl"],
        reverse=True,
    )

    helps   = []
    hurts   = []
    neutral = []

    for label, data in sorted_ablations:
        if data["results"] is None:
            lines.append(f"| {label} | ERROR | — | — |")
            continue

        without_pnl = data["results"]["daily_pnl"]
        delta       = data["delta_pnl"]
        verdict     = data["verdict"]
        sign        = "+" if delta >= 0 else ""
        verdict_emoji = "✅" if verdict == "HELPS" else ("❌" if verdict == "HURTS" else "➖")

        lines.append(
            f"| {label} | ${without_pnl:+.2f} | {sign}${delta:.2f} | "
            f"{verdict_emoji} {verdict} |"
        )

        if verdict == "HELPS":
            helps.append((label, delta))
        elif verdict == "HURTS":
            hurts.append((label, delta))
        else:
            neutral.append(label)

    lines += ["", "## Summary", ""]

    if helps:
        lines.append("**Features contributing positively:**")
        for label, delta in helps:
            lines.append(f"- {label}: +${delta:.2f}")
        lines.append("")

    if hurts:
        lines.append("**Features hurting performance (consider disabling):**")
        for label, delta in hurts:
            lines.append(f"- {label}: ${delta:.2f}")
        lines.append("")

    if neutral:
        lines.append(f"**Neutral features:** {', '.join(neutral)}")
        lines.append("")

    # Recommended config for tomorrow
    lines += ["## Recommended Feature Config for Tomorrow", ""]
    lines.append("```env")
    for label, data in sorted_ablations:
        env_key = data["env_key"]
        # Keep helping + neutral, disable hurting
        verdict = data["verdict"]
        keep    = verdict != "HURTS"
        lines.append(f"{env_key}={'true' if keep else 'false'}  # {verdict}")
    lines.append("```")
    lines.append("")
    lines.append("*Note: Recommendations based on single-day data — "
                 "use caution. Accumulate 5+ days before making permanent changes.*")

    return "\n".join(lines)


def save_report(report_text: str, date_str: str) -> Path:
    """Save ablation report to reports/ directory (committed to git)."""
    base_dir    = Path(os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"))
    reports_dir = base_dir / "reports"
    reports_dir.mkdir(exist_ok=True)
    path = reports_dir / f"ablation_{date_str}.md"
    path.write_text(report_text, encoding="utf-8")
    print(f"[ablation] Report saved: {path}")
    return path


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MNQ Ablation Runner")
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"),
                        help="Date to run ablation on (YYYY-MM-DD)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    results = run_ablation(args.date, verbose=args.verbose)

    if "error" not in results:
        print("\n" + results["report"])
        save_report(results["report"], args.date)


if __name__ == "__main__":
    main()
