"""
learning_session.py — V4.1

EOD learning session orchestrator for MNQ AI Trader.

Runs at 4:00 PM ET after trading stops:
  1. Run ablation backtest on today's session
  2. Ask Claude to synthesize findings into actionable insights
  3. Save learning report to memory/ (injected into tomorrow's pre-market)
  4. Auto-commit + push to GitHub via version_manager
  5. Log session summary

The learning is "soft" — Claude reads findings in tomorrow's pre-market prompt
and adjusts its reasoning accordingly. No automatic .env changes are made.

Called by: main.py at EOD (when FEATURE_LEARNING_EOD=true)
Can also run manually: py -3.11 learning_session.py --date 2026-05-27
"""

import os
import sys
import json
import argparse
from datetime import datetime, date
from pathlib import Path

BASE_DIR   = Path(os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"))
MEMORY_DIR = BASE_DIR / "memory"
DATA_DIR   = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"  # committed to git — analysis, not raw data

sys.path.insert(0, str(BASE_DIR))


def _has_session_data(date_str: str) -> bool:
    """Check if we have recorded data for this date."""
    snap = DATA_DIR / f"snapshots_{date_str}.jsonl"
    return snap.exists() and snap.stat().st_size > 0


def _load_ablation_report(date_str: str) -> str:
    """Load previously saved ablation report if it exists."""
    path = MEMORY_DIR / f"ablation_{date_str}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _load_recent_learnings(n: int = 5) -> str:
    """Load last N learning reports for trend context."""
    reports = sorted(MEMORY_DIR.glob("learning_*.md"), reverse=True)[:n]
    if not reports:
        return "No prior learning reports available."
    summaries = []
    for r in reports:
        date_part = r.stem.replace("learning_", "")
        # Read just first 500 chars (summary section)
        text = r.read_text(encoding="utf-8")[:500]
        summaries.append(f"### {date_part}\n{text}\n---")
    return "\n".join(summaries)


def _ask_claude_for_insights(
    ablation_report: str,
    session_summary: str,
    recent_learnings: str,
    trades: list,
) -> str:
    """
    Ask Claude Sonnet to synthesize ablation results into actionable insights.
    Returns markdown text for the learning report.
    """
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

        trade_text = ""
        if trades:
            trade_lines = []
            for t in trades:
                trade_lines.append(
                    f"  {t.get('entry_time','?')} {t.get('direction','?')} "
                    f"entry={t.get('entry',0):.2f} exit={t.get('exit',0):.2f} "
                    f"P&L=${t.get('pnl',0):+.2f} [{t.get('reason','?')}]"
                )
            trade_text = "\n".join(trade_lines)
        else:
            trade_text = "  No trades today."

        prompt = f"""You are analyzing the performance of an MNQ futures trading bot.
Today's session summary: {session_summary}

Today's trades:
{trade_text}

Ablation test results (each feature disabled one at a time to measure contribution):
{ablation_report}

Prior learning history (last 5 sessions):
{recent_learnings}

Please provide:

1. **Key observations** (3-5 bullet points): What do the ablation results tell us about which features are working?

2. **Pattern analysis**: Are there recurring patterns across multiple sessions? What market conditions seemed to favor or hurt our features?

3. **Feature recommendations**: Based on today's data, which features should we watch carefully? (Don't recommend disabling based on a single day — note if it's consistent across sessions)

4. **Entry quality analysis**: Looking at the actual trades, were entries well-timed? What could Claude have done better?

5. **Tomorrow's focus**: 2-3 specific things to watch for in tomorrow's session given today's learnings.

6. **Confidence note**: Rate your confidence in these recommendations (Low/Medium/High) given the sample size.

Be concise, specific, and honest. Avoid generic advice. Reference specific P&L numbers and feature names from the ablation report.
Keep total response under 600 words."""

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )

        return response.content[0].text

    except Exception as e:
        return f"Claude synthesis unavailable: {e}\n\nRaw ablation results saved above."


def run_learning_session(
    date_str: str,
    session_summary: str = "",
    trades: list = None,
    auto_commit: bool = True,
) -> str:
    """
    Run full EOD learning session.

    Args:
        date_str:        Date string YYYY-MM-DD
        session_summary: One-line summary of the live session
        trades:          List of trade dicts from executor.trades_today
        auto_commit:     Whether to auto-commit to GitHub

    Returns:
        Path to saved learning report
    """
    MEMORY_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)
    print(f"\n{'='*60}")
    print(f"LEARNING SESSION — {date_str}")
    print(f"{'='*60}\n")

    # ── 1. Check for session data ─────────────────────────────
    if not _has_session_data(date_str):
        print(f"[learning] No session data found for {date_str} — skipping ablation")
        ablation_results = None
        ablation_report  = "No trading data recorded for this session."
    else:
        # ── 2. Run ablation ───────────────────────────────────
        print("[learning] Running ablation backtest...")
        from ablation_runner import run_ablation, save_report
        ablation_results = run_ablation(date_str)

        if "error" in ablation_results:
            ablation_report = "Ablation failed — no session data or backtest error."
        else:
            ablation_report = ablation_results.get("report", "")
            save_report(ablation_report, date_str)

    # ── 3. Load recent history ────────────────────────────────
    recent = _load_recent_learnings(5)

    # ── 4. Ask Claude for synthesis ───────────────────────────
    print("[learning] Asking Claude for insights...")
    insights = _ask_claude_for_insights(
        ablation_report  = ablation_report,
        session_summary  = session_summary or "No summary available",
        recent_learnings = recent,
        trades           = trades or [],
    )

    # ── 5. Build and save learning report ────────────────────
    baseline = (ablation_results or {}).get("baseline", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M ET")

    report_lines = [
        f"# Learning Report — {date_str}",
        f"*Generated: {now}*",
        "",
        "## Session Summary",
        session_summary or "No live session summary available.",
        "",
    ]

    if baseline:
        report_lines += [
            "## Live Session Results",
            f"- Trades: {baseline.get('trade_count', 0)} | "
            f"Win rate: {baseline.get('win_rate', 0)}% | "
            f"P&L: ${baseline.get('daily_pnl', 0):+.2f}",
            "",
        ]

    report_lines += [
        "## Claude's Analysis",
        insights,
        "",
        "---",
        "## Full Ablation Report",
        ablation_report,
    ]

    report_text = "\n".join(report_lines)

    # Save to reports/ (committed to git) and memory/ (local context injection)
    report_path = REPORTS_DIR / f"learning_{date_str}.md"
    report_path.write_text(report_text, encoding="utf-8")
    # Also save to memory/ for pre-market injection (load_learning_for_premarket reads here)
    memory_path = MEMORY_DIR / f"learning_{date_str}.md"
    memory_path.write_text(report_text, encoding="utf-8")
    print(f"[learning] Report saved: {report_path}")

    # ── 6. Print key insights ─────────────────────────────────
    print(f"\n{'─'*60}")
    print("LEARNING INSIGHTS:")
    print(f"{'─'*60}")
    print(insights[:800])
    print(f"{'─'*60}\n")

    # ── 7. Auto-commit to GitHub ──────────────────────────────
    if auto_commit:
        try:
            from version_manager import eod_commit
            pnl_str = f"${baseline.get('daily_pnl', 0):+.2f}" if baseline else "no trades"
            new_ver = eod_commit(
                session_summary = f"{date_str} | {pnl_str} | "
                                  f"{baseline.get('trade_count', 0)} trades",
                bump = "patch",
            )
            print(f"[learning] Auto-committed as v{new_ver}")
        except Exception as e:
            print(f"[learning] Auto-commit failed: {e}")

    return str(report_path)


def load_learning_for_premarket(n_days: int = 3) -> str:
    """
    Load recent learning reports for injection into pre-market prompt.
    Called by claude_brain.py when FEATURE_LEARNING_INJECT=true.
    """
    reports = sorted(MEMORY_DIR.glob("learning_*.md"), reverse=True)[:n_days]
    if not reports:
        return ""

    sections = []
    for r in reports:
        date_part = r.stem.replace("learning_", "")
        text      = r.read_text(encoding="utf-8")

        # Extract just the Claude Analysis section
        if "## Claude's Analysis" in text:
            analysis = text.split("## Claude's Analysis")[1]
            analysis = analysis.split("---")[0].strip()[:600]
        else:
            analysis = text[:400]

        sections.append(f"**{date_part}:**\n{analysis}")

    return (
        "═══════════════════════════════════════\n"
        "LEARNING FROM RECENT SESSIONS\n"
        "═══════════════════════════════════════\n"
        + "\n\n".join(sections)
        + "\n═══════════════════════════════════════\n"
    )


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MNQ Learning Session")
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--no-commit", action="store_true",
                        help="Skip auto-commit to GitHub")
    parser.add_argument("--summary", default="", help="Session summary text")
    args = parser.parse_args()

    run_learning_session(
        date_str       = args.date,
        session_summary = args.summary,
        auto_commit    = not args.no_commit,
    )


if __name__ == "__main__":
    main()
