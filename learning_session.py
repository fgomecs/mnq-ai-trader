"""
learning_session.py — V4.1

EOD learning session orchestrator for MNQ AI Trader.

Runs at 4:00 PM ET after trading stops:
  1. Run ablation backtest on today's session
  2. Ask Claude to synthesize findings into actionable insights
  3. Save learning report to memory/ (injected into tomorrow's pre-market)
  4. Bump version in .env via version_manager
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
REPORTS_DIR = BASE_DIR / "reports"

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


def _analyze_commission_drag(trades: list) -> dict:
    """
    Analyze commission drag and trade duration for sizing guidance.

    Returns a dict with:
      commission_total     — total commissions paid today
      gross_pnl            — raw P&L before commissions
      drag_pct             — commissions as % of gross profit (None if no gross profit)
      avg_hold_secs        — average trade hold time in seconds
      n_trades             — number of valid trades
      flags                — list of flag strings (may be empty)
      summary              — human-readable one-liner
    """
    valid = [t for t in trades if t.get("pnl") is not None]
    if not valid:
        return {"commission_total": 0, "gross_pnl": 0, "drag_pct": None,
                "avg_hold_secs": 0, "n_trades": 0, "flags": [], "summary": "No trades."}

    commission_total = sum(t.get("commission", 0.0) for t in valid)
    # gross_pnl = net P&L + commissions back out (pnl stored is already net when simulated)
    net_pnl    = sum(t["pnl"] for t in valid)
    gross_pnl  = net_pnl + commission_total

    drag_pct = None
    if gross_pnl > 0:
        drag_pct = (commission_total / gross_pnl) * 100

    hold_times = [t.get("hold_seconds", 0) for t in valid if t.get("hold_seconds", 0) > 0]
    avg_hold_secs = sum(hold_times) / len(hold_times) if hold_times else 0

    flags = []
    if drag_pct is not None and drag_pct > 3.0:
        flags.append(
            f"COMMISSION_DRAG: {drag_pct:.1f}% of gross profit eaten by commissions "
            f"(${commission_total:.2f} on ${gross_pnl:.2f} gross). "
            "Too many small trades — target sizing is too small or R:R too tight."
        )
    if avg_hold_secs > 0 and avg_hold_secs < 300:
        avg_min = avg_hold_secs / 60
        flags.append(
            f"SCALP_HEAVY: Avg hold time {avg_min:.1f} min (<5 min threshold). "
            "Review target sizing — exits happening before structure target is reached."
        )

    if flags:
        summary = f"⚠ Sizing flags: {len(flags)} — " + " | ".join(flags)
    elif commission_total > 0:
        drag_str = f"{drag_pct:.1f}%" if drag_pct is not None else "N/A"
        summary = f"Commission drag: {drag_str} (${commission_total:.2f}) — within acceptable range."
    else:
        summary = "Commissions not simulated — no drag analysis."

    return {
        "commission_total": round(commission_total, 2),
        "gross_pnl":        round(gross_pnl, 2),
        "drag_pct":         round(drag_pct, 1) if drag_pct is not None else None,
        "avg_hold_secs":    round(avg_hold_secs),
        "n_trades":         len(valid),
        "flags":            flags,
        "summary":          summary,
    }


def _analyze_truncation(date_str: str) -> dict:
    """
    Scan today's entry decisions for max_tokens truncation. A truncated
    response has no parseable `DECISION: BUY|SELL|HOLD` line; parse_decision
    silently returns a default HOLD and the Opus spend is wasted.
    """
    import re
    path = DATA_DIR / f"decisions_{date_str}.jsonl"
    out = {"total": 0, "truncated": 0, "rate_pct": 0.0,
           "total_cost": 0.0, "wasted_cost": 0.0, "wasted_pct": 0.0}
    if not path.exists():
        return out
    pat = re.compile(r"(?m)^\s*\**\s*DECISION\s*:\s*(BUY|SELL|HOLD)")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "decision":
            continue
        raw  = d.get("raw_response") or ""
        cost = d.get("cost_usd", 0.0) or 0.0
        out["total"]      += 1
        out["total_cost"] += cost
        if not pat.search(raw):
            out["truncated"]   += 1
            out["wasted_cost"] += cost
    if out["total"]:
        out["rate_pct"] = round(100 * out["truncated"] / out["total"], 1)
    if out["total_cost"]:
        out["wasted_pct"] = round(100 * out["wasted_cost"] / out["total_cost"], 1)
    out["total_cost"]  = round(out["total_cost"],  2)
    out["wasted_cost"] = round(out["wasted_cost"], 2)
    return out


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
    commission_analysis: dict | None = None,
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
                hold_min = t.get("hold_seconds", 0) / 60
                comm_str = f" comm=${t.get('commission', 0):.2f}" if t.get("commission", 0) > 0 else ""
                trade_lines.append(
                    f"  {t.get('time','?')} {t.get('action','?')} "
                    f"entry={t.get('entry',0):.2f} exit={t.get('exit',0):.2f} "
                    f"P&L=${t.get('pnl',0) or 0:+.2f}{comm_str} "
                    f"hold={hold_min:.1f}min [{t.get('exit_reason','?')}]"
                )
            trade_text = "\n".join(trade_lines)
        else:
            trade_text = "  No trades today."

        # Commission drag context (injected when SIMULATE_COMMISSIONS=true)
        commission_section = ""
        if commission_analysis and commission_analysis.get("commission_total", 0) > 0:
            ca = commission_analysis
            hold_min = ca["avg_hold_secs"] / 60
            commission_section = f"""
Commission & Sizing Analysis:
- Total commissions: ${ca['commission_total']:.2f} on {ca['n_trades']} trades
- Gross P&L (before fees): ${ca['gross_pnl']:.2f}
- Commission drag: {f"{ca['drag_pct']:.1f}%" if ca['drag_pct'] is not None else 'N/A (no gross profit)'}
- Avg hold time: {hold_min:.1f} minutes
- Sizing flags: {', '.join(ca['flags']) if ca['flags'] else 'None'}
"""

        prompt = f"""You are analyzing the performance of an MNQ futures trading bot.
Today's session summary: {session_summary}

Today's trades:
{trade_text}
{commission_section}
Ablation test results (each feature disabled one at a time to measure contribution):
{ablation_report}

Prior learning history (last 5 sessions):
{recent_learnings}

Please provide:

1. **Key observations** (3-5 bullet points): What do the ablation results tell us about which features are working?

2. **Pattern analysis**: Are there recurring patterns across multiple sessions? What market conditions seemed to favor or hurt our features?

3. **Feature recommendations**: Based on today's data, which features should we watch carefully? (Don't recommend disabling based on a single day — note if it's consistent across sessions)

4. **Entry quality analysis**: Looking at the actual trades, were entries well-timed? What could Claude have done better?

5. **Sizing guidance for tomorrow**: Based on commission drag and hold times, is the bot targeting appropriately? Recommend specific adjustments if flags were raised (e.g., widen targets, avoid trades below a minimum R:R).

6. **Tomorrow's focus**: 2-3 specific things to watch for in tomorrow's session given today's learnings.

7. **Confidence note**: Rate your confidence in these recommendations (Low/Medium/High) given the sample size.

Be concise, specific, and honest. Avoid generic advice. Reference specific P&L numbers and feature names from the ablation report.
Keep total response under 700 words."""

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
) -> str:
    """
    Run full EOD learning session.

    Args:
        date_str:        Date string YYYY-MM-DD
        session_summary: One-line summary of the live session
        trades:          List of trade dicts from executor.trades_today

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

    # ── 4. Commission drag analysis ───────────────────────────
    commission_analysis = _analyze_commission_drag(trades or [])

    # ── 4b. Truncation analysis (max_tokens detection) ────────
    truncation = _analyze_truncation(date_str)
    if truncation["truncated"]:
        print(f"[learning] WARN Truncated entry calls: {truncation['truncated']}/{truncation['total']} "
              f"({truncation['rate_pct']}%) - ${truncation['wasted_cost']} wasted")
    if commission_analysis["flags"]:
        print(f"[learning] ⚠ Sizing flags: {commission_analysis['summary']}")
    elif commission_analysis["commission_total"] > 0:
        print(f"[learning] Commission drag: {commission_analysis['summary']}")

    # ── 5. Ask Claude for synthesis ───────────────────────────
    print("[learning] Asking Claude for insights...")
    insights = _ask_claude_for_insights(
        ablation_report     = ablation_report,
        session_summary     = session_summary or "No summary available",
        recent_learnings    = recent,
        trades              = trades or [],
        commission_analysis = commission_analysis,
    )

    # ── 6. Build and save learning report ────────────────────
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

    # Commission drag section (only appears when commissions were simulated)
    if commission_analysis["commission_total"] > 0:
        ca = commission_analysis
        hold_min = ca["avg_hold_secs"] / 60
        drag_str = f"{ca['drag_pct']:.1f}%" if ca["drag_pct"] is not None else "N/A"
        report_lines += [
            "## Commission & Sizing Analysis",
            f"- Commissions paid: ${ca['commission_total']:.2f} ({ca['n_trades']} trades)",
            f"- Gross P&L: ${ca['gross_pnl']:.2f} | Drag: {drag_str}",
            f"- Avg hold time: {hold_min:.1f} min",
        ]
        for flag in ca["flags"]:
            report_lines.append(f"- ⚠ {flag}")
        report_lines.append("")

    # Truncation section — only appears when any entry call truncated
    if truncation["total"]:
        report_lines += [
            "## Opus Truncation Analysis",
            f"- Entry calls: {truncation['total']} | "
            f"Truncated: {truncation['truncated']} ({truncation['rate_pct']}%)",
            f"- Opus spend: ${truncation['total_cost']} | "
            f"Wasted on truncated calls: ${truncation['wasted_cost']} ({truncation['wasted_pct']}%)",
        ]
        if truncation["rate_pct"] >= 5.0:
            report_lines.append(
                f"- ⚠ Truncation rate ≥5% — raise max_tokens in analyze_market "
                "or shorten the response format."
            )
        report_lines.append("")

    report_lines += [
        "## Claude's Analysis",
        insights,
        "",
        "---",
        "## Full Ablation Report",
        ablation_report,
    ]

    report_text = "\n".join(report_lines)

    # Save to reports/ and memory/ (local context injection)
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

    # ── 7. Export journal ─────────────────────────────────────
    try:
        from journal_exporter import run as export_journal
        export_journal()
    except Exception as e:
        print(f"[learning] Journal export failed: {e}")

    # ── 8. Bump version ───────────────────────────────────────
    try:
        from version_manager import eod_commit
        pnl_str = f"${baseline.get('daily_pnl', 0):+.2f}" if baseline else "no trades"
        new_ver = eod_commit(
            session_summary = f"{date_str} | {pnl_str} | "
                              f"{baseline.get('trade_count', 0)} trades",
            bump = "patch",
        )
        print(f"[learning] Version bumped to v{new_ver}")
    except Exception as e:
        print(f"[learning] Version bump failed: {e}")

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

        # Extract sizing flags if present (inject before the analysis)
        sizing_note = ""
        if "## Commission & Sizing Analysis" in text:
            sizing_block = text.split("## Commission & Sizing Analysis")[1]
            sizing_block = sizing_block.split("##")[0].strip()
            # Only surface lines containing flags (⚠) or drag percentage
            flag_lines = [l for l in sizing_block.splitlines() if "⚠" in l or "Drag:" in l or "hold time" in l]
            if flag_lines:
                sizing_note = "**Sizing flags:** " + " | ".join(l.lstrip("- ").strip() for l in flag_lines) + "\n"

        # Extract just the Claude Analysis section
        if "## Claude's Analysis" in text:
            analysis = text.split("## Claude's Analysis")[1]
            analysis = analysis.split("---")[0].strip()[:600]
        else:
            analysis = text[:400]

        sections.append(f"**{date_part}:**\n{sizing_note}{analysis}")

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
    parser.add_argument("--summary", default="", help="Session summary text")
    args = parser.parse_args()

    run_learning_session(
        date_str        = args.date,
        session_summary = args.summary,
    )


if __name__ == "__main__":
    main()
