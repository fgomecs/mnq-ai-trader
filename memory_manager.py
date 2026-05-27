"""
Session Memory Manager for MNQ AI Trader.

Session 2 fixes from audit:
  P2.7  — f-string conditional format-spec bug in best/worst P&L lines.
          Crashes the EOD summary write if best or worst is None.
  P0.5  — EOD summary now uses CLAUDE_STRUCTURE_MODEL (Sonnet) instead
          of CLAUDE_MODEL (which aliases to Opus). Coaching output doesn't
          need Opus-level reasoning; Sonnet keeps cost down.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from typing import Optional


def _atomic_write(path: str, write_fn) -> None:
    """Write via tempfile + os.replace so a crash mid-write can't corrupt
    the lessons/trades JSON that the next session loads at startup."""
    dir_ = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            write_fn(fh)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

import anthropic
import pytz

from logger import logger

try:
    from config import MEMORY_DIR, CLAUDE_STRUCTURE_MODEL
except ImportError:
    MEMORY_DIR             = os.path.join(os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"), "memory")
    CLAUDE_STRUCTURE_MODEL = "claude-sonnet-4-6"

eastern = pytz.timezone("US/Eastern")


# ─── helpers ───────────────────────────────────────────────

def _ensure_dir() -> None:
    os.makedirs(MEMORY_DIR, exist_ok=True)


def _lesson_path(date: str) -> str:
    return os.path.join(MEMORY_DIR, f"lessons_{date}.json")


def _load_lessons(date: str) -> Optional[dict]:
    path = _lesson_path(date)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _load_recent_lessons(days: int = 5) -> list[dict]:
    lessons = []
    now_et  = datetime.now(eastern)
    for i in range(1, days + 1):
        date = (now_et - timedelta(days=i)).strftime("%Y-%m-%d")
        l    = _load_lessons(date)
        if l:
            lessons.append(l)
    return lessons


def _call_claude(prompt: str, max_tokens: int) -> str:
    client = anthropic.Anthropic()
    resp   = client.messages.create(
        model=CLAUDE_STRUCTURE_MODEL,   # Sonnet, not Opus
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    # Defensive: concatenate all text blocks
    return "".join(
        block.text for block in resp.content
        if getattr(block, "type", "text") == "text"
    ).strip()


# ─── End-of-Day Summary ────────────────────────────────────

def save_daily_summary(trades: list, daily_pnl: float, analysis_log: list) -> str:
    """
    Ask Claude to extract structured lessons from today's trades.
    Saves both a JSON (lessons) and markdown (human-readable).
    Returns the markdown path.
    """
    _ensure_dir()
    today    = datetime.now(eastern).strftime("%Y-%m-%d")
    md_path  = os.path.join(MEMORY_DIR, f"summary_{today}.md")
    json_path = _lesson_path(today)

    total   = len(trades)
    wins    = [t for t in trades if (t.get("pnl") or 0) > 0]
    losses  = [t for t in trades if (t.get("pnl") or 0) < 0]
    be      = [t for t in trades if (t.get("pnl") or 0) == 0]
    win_rate  = (len(wins) / total * 100) if total else 0.0
    avg_win   = sum((t.get("pnl") or 0) for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss  = sum((t.get("pnl") or 0) for t in losses) / len(losses) if losses else 0.0
    best      = max(trades, key=lambda t: (t.get("pnl") or 0)) if trades else None
    worst     = min(trades, key=lambda t: (t.get("pnl") or 0)) if trades else None

    # P2.7 — precompute the numbers; the previous f-string used
    # `{best['pnl']:.2f if best else 0:.2f}` which is not valid Python
    # (conditional expression can't be in format-spec position) and
    # crashed when best/worst was None.
    best_pnl  = (best.get("pnl")  or 0.0) if best  else 0.0
    worst_pnl = (worst.get("pnl") or 0.0) if worst else 0.0

    trade_detail_lines = []
    for i, t in enumerate(trades, 1):
        # `pnl` can be None on sanity-rejected trades; coerce to 0.0 so the
        # format-string never sees None (which raises TypeError on :.2f).
        # Same defensive read for `entry`/`exit` since those can also be None
        # when the rejection path skipped fill resolution.
        pnl_val   = t.get("pnl")  or 0.0
        entry_val = t.get("entry") if t.get("entry") is not None else "?"
        exit_val  = t.get("exit")  if t.get("exit")  is not None else "?"
        trade_detail_lines.append(
            f"Trade {i}: {t.get('action','?')} @ {entry_val} → "
            f"{exit_val} | P&L: ${pnl_val:.2f}\n"
            f"  Entry: {t.get('reasoning','')[:300]}\n"
            f"  Exit:  {t.get('exit_reason','')[:200]}\n"
            f"  Mode: {t.get('mode','?')} | Duration: {t.get('duration','?')}"
        )
    trade_details = "\n".join(trade_detail_lines)

    lessons_json: dict = {}

    # Day-of-week + bot activity context: prevents Claude from confabulating
    # "must be a holiday" on zero-trade days when the bot was actually active.
    today_dt   = datetime.strptime(today, "%Y-%m-%d")
    weekday    = today_dt.strftime("%A")
    activity_lines = []
    try:
        base_dir = os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader")
        dec_path = os.path.join(base_dir, "data", f"decisions_{today}.jsonl")
        if os.path.exists(dec_path):
            n_calls = sum(1 for _ in open(dec_path, encoding="utf-8"))
            if n_calls:
                activity_lines.append(
                    f"Bot was ACTIVE today — {n_calls} Opus entry calls were made. "
                    f"This was a normal trading session, NOT a holiday or downtime."
                )
    except Exception:
        pass
    activity_context = "\n".join(activity_lines) or "(activity log unavailable)"

    prompt = f"""You are a professional trading coach reviewing today's MNQ futures trades.
Today: {today} ({weekday}) | Net P&L: ${daily_pnl:.2f} | Trades: {total} | Win rate: {win_rate:.0f}%

CONTEXT:
{activity_context}

TRADE DETAILS:
{trade_details}

Respond ONLY with a JSON object (no markdown fences):
{{
  "overall_grade": "A/B/C/D/F",
  "one_line_summary": "single sentence",
  "key_levels_that_mattered": [],
  "what_worked": [],
  "what_failed": [],
  "biggest_mistake": "",
  "best_decision": "",
  "pattern_warnings": [],
  "carry_forward_levels": [],
  "rules_reinforced": [],
  "rules_violated": [],
  "tomorrow_focus": [],
  "dead_zones_respected": true,
  "or_direction_respected": true,
  "choch_waited_for": true,
  "trailing_used": true
}}"""

    try:
        raw          = _call_claude(prompt, max_tokens=1_500)
        # Use the tolerant JSON parser from claude_brain (same bug Sonnet
        # occasionally hits — multi-line strings inside JSON values).
        try:
            from claude_brain import _tolerant_json_parse
            lessons_json = _tolerant_json_parse(raw)
        except ImportError:
            raw          = raw.replace("```json", "").replace("```", "").strip()
            lessons_json = json.loads(raw)
        logger.info(f"EOD grade: {lessons_json.get('overall_grade', '?')}")
    except Exception as e:
        logger.error(f"EOD Claude analysis failed: {e}")
        lessons_json = {
            "overall_grade":  "?",
            "one_line_summary": f"Net ${daily_pnl:.2f}, {total} trades, {win_rate:.0f}%WR",
            "what_worked": [], "what_failed": [],
            "tomorrow_focus": ["Review trades manually"],
            "carry_forward_levels": [], "pattern_warnings": [],
        }

    lessons_json.update({
        "date": today, "pnl": daily_pnl, "trades": total,
        "win_rate": round(win_rate, 1),
        "avg_win":  round(avg_win,  2),
        "avg_loss": round(avg_loss, 2),
    })

    _atomic_write(json_path, lambda f: json.dump(lessons_json, f, indent=2))

    def _bullet(items: list) -> str:
        return "\n".join(f"- {x}" for x in items)

    md = f"""# Trading Summary — {today}
Grade: {lessons_json.get('overall_grade','?')} | P&L: ${daily_pnl:.2f} | Trades: {total} | Win Rate: {win_rate:.0f}%

## One Line
{lessons_json.get('one_line_summary','')}

## Stats
- Winners: {len(wins)} avg ${avg_win:.2f} | Losers: {len(losses)} avg ${avg_loss:.2f} | BE: {len(be)}
- Best: ${best_pnl:.2f} | Worst: ${worst_pnl:.2f}

## What Worked
{_bullet(lessons_json.get('what_worked',[]))}

## What Failed
{_bullet(lessons_json.get('what_failed',[]))}

## Biggest Mistake
{lessons_json.get('biggest_mistake','')}

## Best Decision
{lessons_json.get('best_decision','')}

## Rules Violated
{_bullet(lessons_json.get('rules_violated',[]))}

## Rules Reinforced
{_bullet(lessons_json.get('rules_reinforced',[]))}

## Carry-Forward Levels
{_bullet(lessons_json.get('carry_forward_levels',[]))}

## Pattern Warnings
{_bullet(lessons_json.get('pattern_warnings',[]))}

## Tomorrow's Focus
{chr(10).join(f"{i+1}. {f}" for i,f in enumerate(lessons_json.get('tomorrow_focus',[])))}

## Discipline Checklist
- Dead zones respected : {lessons_json.get('dead_zones_respected','?')}
- OR direction respected: {lessons_json.get('or_direction_respected','?')}
- CHoCH waited for     : {lessons_json.get('choch_waited_for','?')}
- Trailing used        : {lessons_json.get('trailing_used','?')}

## Full Trade Log
{trade_details}
"""

    _atomic_write(md_path, lambda f: f.write(md))

    logger.info(f"Daily summary saved: {md_path}")
    return md_path


# ─── Morning Review ────────────────────────────────────────

def generate_morning_review(current_snapshot: Optional[dict] = None) -> str:
    _ensure_dir()
    today           = datetime.now(eastern).strftime("%Y-%m-%d")
    recent_lessons  = _load_recent_lessons(days=5)

    if not recent_lessons:
        return "No previous session data — first session. Trade clean, follow the framework."

    history_lines = []
    for l in recent_lessons:
        history_lines.append(
            f"Date: {l.get('date','?')} | Grade: {l.get('overall_grade','?')} | "
            f"P&L: ${l.get('pnl',0):.2f} | Win: {l.get('win_rate',0):.0f}%\n"
            f"  Summary   : {l.get('one_line_summary','')}\n"
            f"  Worked    : {', '.join(l.get('what_worked',[])[:3])}\n"
            f"  Failed    : {', '.join(l.get('what_failed',[])[:3])}\n"
            f"  Mistake   : {l.get('biggest_mistake','')}\n"
            f"  Levels    : {', '.join(l.get('carry_forward_levels',[]))}\n"
            f"  Warnings  : {', '.join(l.get('pattern_warnings',[]))}\n"
            f"  Violated  : {', '.join(l.get('rules_violated',[])[:2])}\n"
            f"  Focus tmrw: {', '.join(l.get('tomorrow_focus',[]))}"
        )
    history = "\n\n".join(history_lines)

    snap_text = ""
    if current_snapshot:
        snap_text = (
            f"\nCurrent market context:\n"
            f"  Price      : {current_snapshot.get('last_price')}\n"
            f"  VWAP       : {current_snapshot.get('vwap')}\n"
            f"  OR direction: {current_snapshot.get('or_direction','unknown')}\n"
            f"  OR rel vol : {current_snapshot.get('or_relative_volume','unknown')}\n"
            f"  Kill zone  : {current_snapshot.get('killzone','')}\n"
            f"  AMD phase  : {current_snapshot.get('amd_phase','')}"
        )

    prompt = f"""You are a professional trading coach giving a pre-session briefing for {today}.

RECENT PERFORMANCE:
{history}
{snap_text}

Give a concise pre-session brief (max 300 words, plain text, no markdown headers):
1. Recurring mistakes to avoid today (specific pattern names)
2. Setups that have been working (specific setup names)
3. Carry-forward price levels that still matter
4. One key mental focus for today
5. Pattern warnings from recent days"""

    try:
        brief       = _call_claude(prompt, max_tokens=600)
        grade_trail = " | ".join(
            f"{l.get('date','')[-5:]} {l.get('overall_grade','?')} ${l.get('pnl',0):.0f}"
            for l in recent_lessons[:3]
        )
        logger.info("Morning review generated")
        return (
            f"\n╔══════════════════════════════════════════╗\n"
            f"║       PRE-SESSION BRIEF — {today}      ║\n"
            f"╚══════════════════════════════════════════╝\n\n"
            f"{brief}\n\n"
            f"Recent grades: {grade_trail}\n"
        )
    except Exception as e:
        logger.error(f"Morning review failed: {e}")
        l = recent_lessons[0]
        return (
            f"PRE-SESSION BRIEF — {today}\n"
            f"Last session : {l.get('overall_grade','?')} grade, ${l.get('pnl',0):.2f}\n"
            f"Biggest mistake: {l.get('biggest_mistake','')}\n"
            f"Today's focus  : {' | '.join(l.get('tomorrow_focus',[]))}\n"
            f"Carry levels   : {', '.join(l.get('carry_forward_levels',[]))}\n"
            f"Warnings       : {', '.join(l.get('pattern_warnings',[]))}"
        )


# ─── Entry-analysis context ────────────────────────────────

def load_recent_memory(days: int = 5) -> str:
    """Compact context string for Claude's entry analysis."""
    _ensure_dir()
    recent = _load_recent_lessons(days)

    if not recent:
        return "No previous session data — first session."

    lines = [
        "═══════════════════════════════════════",
        "RECENT SESSION MEMORY",
        "═══════════════════════════════════════",
    ]
    for l in recent:
        lines.append(
            f"\n{l.get('date','')} — Grade {l.get('overall_grade','?')} | "
            f"P&L ${l.get('pnl',0):.2f} | {l.get('win_rate',0):.0f}%WR\n"
            f"  Summary  : {l.get('one_line_summary','')}\n"
            f"  Levels   : {', '.join(l.get('carry_forward_levels',[]))}\n"
            f"  Warnings : {', '.join(l.get('pattern_warnings',[]))}\n"
            f"  Worked   : {', '.join(l.get('what_worked',[])[:2])}\n"
            f"  Violated : {', '.join(l.get('rules_violated',[])[:2])}"
        )

    # Aggregate across days
    all_levels   = []
    all_warnings = []
    mistake_freq: dict[str, int] = {}
    for l in recent:
        all_levels.extend(l.get("carry_forward_levels", []))
        all_warnings.extend(l.get("pattern_warnings", []))
        for m in l.get("rules_violated", []):
            mistake_freq[m] = mistake_freq.get(m, 0) + 1

    recurring = [m for m, cnt in mistake_freq.items() if cnt >= 2]

    if all_levels:
        lines.append(f"\nACTIVE LEVELS  : {' | '.join(set(all_levels))}")
    if all_warnings:
        lines.append(f"WARNINGS       : {' | '.join(set(all_warnings[:5]))}")
    if recurring:
        lines.append(f"RECURRING MISTAKES (fix these): {' | '.join(recurring)}")

    lines += [
        "═══════════════════════════════════════",
        "Apply these lessons. Respect carry-forward levels.",
        "═══════════════════════════════════════",
    ]
    return "\n".join(lines)


# ─── Trade logging ─────────────────────────────────────────

def save_trade_to_memory(trade: dict) -> None:
    _ensure_dir()
    today    = datetime.now(eastern).strftime("%Y-%m-%d")
    filepath = os.path.join(MEMORY_DIR, f"trades_{today}.json")

    trades = []
    if os.path.exists(filepath):
        try:
            with open(filepath) as f:
                trades = json.load(f)
        except Exception:
            trades = []

    trades.append({"timestamp": datetime.now().isoformat(), **trade})

    _atomic_write(filepath, lambda f: json.dump(trades, f, indent=2))


def load_todays_trades() -> list:
    _ensure_dir()
    today    = datetime.now(eastern).strftime("%Y-%m-%d")
    filepath = os.path.join(MEMORY_DIR, f"trades_{today}.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception:
        return []


print("Memory manager loaded")
