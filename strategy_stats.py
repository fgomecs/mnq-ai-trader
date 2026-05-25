"""
Strategy Performance Tracker for MNQ AI Trader.

Session 2 fix from audit:
  P1.4 — Recommendations now require ≥20 trades (was 5) AND use Wilson
         lower-bound on win rate, not raw point estimate. 5-trade samples
         have a 95% CI roughly 17%–92%; recommending PRIORITIZE off that
         is noise-chasing.

Stats file: <MEMORY_DIR>/strategy_stats.json
"""

import json
import math
import os
import tempfile
from datetime import datetime
from typing import Optional

import pytz

from logger import logger

try:
    from config import MEMORY_DIR
except ImportError:
    MEMORY_DIR = "C:\\trading\\mnq-ai-trader\\memory"

STATS_FILE = os.path.join(MEMORY_DIR, "strategy_stats.json")
eastern    = pytz.timezone("US/Eastern")

# P1.4 — recommendation thresholds
MIN_TRADES_FOR_DISPLAY     = 3    # show in rankings list
MIN_TRADES_FOR_INSTRUCTION = 20   # generate "PRIORITIZE/REDUCE" instructions
WILSON_Z                   = 1.96 # 95% confidence


# ─── Bucket helpers ────────────────────────────────────────

def _empty_bucket() -> dict:
    return {
        "trades": 0, "wins": 0, "losses": 0, "breakeven": 0,
        "total_pnl": 0.0,
        "_total_win_pnl": 0.0,
        "_total_loss_pnl": 0.0,
        "avg_win": 0.0, "avg_loss": 0.0,
        "win_rate": 0.0, "expectancy": 0.0,
        "last_updated": "",
        "recent_streak": 0,
        "last_5": [],
    }


def _default_stats() -> dict:
    return {
        "meta": {
            "created":      datetime.now().isoformat(),
            "last_updated": "",
            "total_trades": 0,
            "version":      2,
        },
        "strategies": {k: _empty_bucket() for k in [
            "ORB_BREAKOUT", "ICT_SWEEP_REVERSAL", "VWAP_RECLAIM",
            "OB_BOUNCE", "FVG_FILL", "CHOCH_ENTRY", "COMBINED", "UNKNOWN",
        ]},
        "factors": {k: _empty_bucket() for k in [
            "OR_BULL", "OR_BEAR", "SWEEP", "CHOCH",
            "ABOVE_VWAP", "BELOW_VWAP", "DELTA_POS", "DELTA_NEG",
            "NY_AM_KZ", "NY_PM_KZ", "LONDON_KZ", "ASIAN_KZ",
            "OB", "FVG", "RELVOL_100", "RELVOL_200", "RELVOL_300",
            "INDUCEMENT", "PRIOR_DAY_LEVEL",
        ]},
        "killzones": {k: _empty_bucket() for k in [
            "NY_AM", "NY_PM", "LONDON", "ASIAN", "DEAD", "OUTSIDE",
        ]},
        "score_brackets": {k: _empty_bucket() for k in ["1-3", "4-5", "6-7", "8-10"]},
        "or_direction":   {k: _empty_bucket() for k in ["BULL", "BEAR", "DOJI", "NONE"]},
        "last_7_days":  [],
        "last_30_days": [],
    }


# ─── Wilson confidence bound ──────────────────────────────

def _wilson_lower_bound(wins: int, trades: int, z: float = WILSON_Z) -> float:
    """
    Wilson score interval lower bound for a binomial proportion.

    Returns the conservative estimate of the true win rate. Use this instead
    of raw wins/trades when making "is this strategy actually good?" calls.

    Example: 3W/5T raw=60% but Wilson lower=23%. A strategy at 5T:60% is
    statistically indistinguishable from a coin flip.
    """
    if trades == 0:
        return 0.0
    p     = wins / trades
    denom = 1 + z*z / trades
    center = p + z*z / (2 * trades)
    spread = z * math.sqrt((p * (1 - p) + z*z / (4 * trades)) / trades)
    return max(0.0, (center - spread) / denom)


# ─── Load / Save ───────────────────────────────────────────

def load_stats() -> dict:
    os.makedirs(MEMORY_DIR, exist_ok=True)
    if not os.path.exists(STATS_FILE):
        return _default_stats()
    try:
        with open(STATS_FILE, "r") as f:
            stats = json.load(f)
        # Forward-fill any keys added in newer versions
        default = _default_stats()
        for section, value in default.items():
            if section not in stats:
                stats[section] = value
            elif isinstance(value, dict):
                for key, v in value.items():
                    if key not in stats[section]:
                        stats[section][key] = v
        return stats
    except Exception as e:
        logger.error(f"Stats load error: {e}")
        return _default_stats()


def save_stats(stats: dict) -> None:
    # Atomic write — strategy_stats.json is read on every trade record and
    # at Claude pre-market injection. A torn write would crash both paths.
    os.makedirs(MEMORY_DIR, exist_ok=True)
    stats["meta"]["last_updated"] = datetime.now().isoformat()
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=MEMORY_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(stats, f, indent=2)
        os.replace(tmp, STATS_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ─── Bucket update ─────────────────────────────────────────

def _update_bucket(bucket: dict, pnl: float) -> None:
    """Mutate *bucket* in-place with one new trade result."""
    bucket["trades"]    += 1
    bucket["total_pnl"] += pnl
    bucket["last_updated"] = datetime.now().isoformat()

    if pnl > 0:
        bucket["wins"]           += 1
        bucket["_total_win_pnl"] += pnl
        bucket["recent_streak"]   = max(0, bucket["recent_streak"]) + 1
        bucket["last_5"]          = (["W"] + bucket["last_5"])[:5]
    elif pnl < 0:
        bucket["losses"]          += 1
        bucket["_total_loss_pnl"] += abs(pnl)
        bucket["recent_streak"]    = min(0, bucket["recent_streak"]) - 1
        bucket["last_5"]           = (["L"] + bucket["last_5"])[:5]
    else:
        bucket["breakeven"]     += 1
        bucket["recent_streak"]  = 0
        bucket["last_5"]         = (["B"] + bucket["last_5"])[:5]

    t = bucket["trades"]
    w = bucket["wins"]
    l = bucket["losses"]

    bucket["win_rate"]  = round(w / t * 100, 1) if t else 0.0
    bucket["avg_win"]   = round(bucket["_total_win_pnl"]  / w, 2) if w else 0.0
    bucket["avg_loss"]  = round(bucket["_total_loss_pnl"] / l, 2) if l else 0.0

    wr = w / t if t else 0.0
    lr = l / t if t else 0.0
    bucket["expectancy"] = round(wr * bucket["avg_win"] - lr * bucket["avg_loss"], 2)


# ─── Record trade ──────────────────────────────────────────

_KZ_MAP = {
    "NY AM": "NY_AM", "NY_AM": "NY_AM",
    "NY PM": "NY_PM", "NY_PM": "NY_PM",
    "LONDON": "LONDON",
    "ASIAN": "ASIAN", "ASIA": "ASIAN",
    "DEAD": "DEAD",
}


def _kz_key(raw: str) -> str:
    raw_upper = raw.upper()
    for fragment, key in _KZ_MAP.items():
        if fragment in raw_upper:
            return key
    return "OUTSIDE"


def record_trade(trade: dict) -> None:
    """Update all stat buckets after a closed trade.

    Required keys: pnl, strategy, confluence, confluence_score,
                   killzone, or_direction
    """
    stats = load_stats()
    pnl   = float(trade.get("pnl", 0))

    # Strategy
    strategy = trade.get("strategy", "UNKNOWN")
    if strategy not in stats["strategies"]:
        stats["strategies"][strategy] = _empty_bucket()
    _update_bucket(stats["strategies"][strategy], pnl)

    # Confluence factors
    confluence_str = trade.get("confluence", "")
    if confluence_str:
        for raw_factor in confluence_str.split("+"):
            factor_key = raw_factor.strip().upper().replace(" ", "_").replace("-", "_")
            if not factor_key:
                continue
            if factor_key not in stats["factors"]:
                stats["factors"][factor_key] = _empty_bucket()
            _update_bucket(stats["factors"][factor_key], pnl)

    # Confluence score bracket
    score = int(trade.get("confluence_score", 0))
    bracket = "8-10" if score >= 8 else "6-7" if score >= 6 else "4-5" if score >= 4 else "1-3"
    _update_bucket(stats["score_brackets"][bracket], pnl)

    # Kill zone
    _update_bucket(stats["killzones"][_kz_key(trade.get("killzone", ""))], pnl)

    # OR direction
    or_dir = trade.get("or_direction", "NONE")
    if or_dir not in stats["or_direction"]:
        or_dir = "NONE"
    _update_bucket(stats["or_direction"][or_dir], pnl)

    # Global counter
    stats["meta"]["total_trades"] += 1

    save_stats(stats)
    logger.info(
        f"Stats updated — strategy:{strategy} score:{score} pnl:${pnl:.2f}"
    )


# ─── Claude context ────────────────────────────────────────

def _streak_arrow(streak: int) -> str:
    if streak >= 3:  return "🔥"
    if streak >= 1:  return "📈"
    if streak <= -3: return "❄️"
    if streak <= -1: return "📉"
    return "➡️"


def _generate_instructions(stats: dict, total: int) -> list[str]:
    """
    P1.4 — Instructions only fire when:
      - bucket has ≥ MIN_TRADES_FOR_INSTRUCTION trades (was 5, now 20), AND
      - Wilson lower-bound on win rate clears a threshold (or its inverse for warnings)

    This kills the "5 lucky trades = chase this strategy" failure mode.
    """
    instructions: list[str] = []

    # Strategy-level — only if we have real volume
    eligible_strats = [
        (n, b) for n, b in stats["strategies"].items()
        if b["trades"] >= MIN_TRADES_FOR_INSTRUCTION
    ]
    if eligible_strats:
        # Best: high Wilson lower bound on win rate AND positive expectancy
        candidates_best = [
            (n, b, _wilson_lower_bound(b["wins"], b["trades"]))
            for n, b in eligible_strats
        ]
        # Sort by Wilson lower bound descending
        candidates_best.sort(key=lambda x: x[2], reverse=True)

        if candidates_best and candidates_best[0][2] >= 0.55 and candidates_best[0][1]["expectancy"] > 10:
            n, b, wlb = candidates_best[0]
            instructions.append(
                f"PRIORITIZE {n} — E:${b['expectancy']:.2f} over {b['trades']}T "
                f"(Wilson lower bound on WR: {wlb*100:.0f}%, statistically real)"
            )

        # Worst: low Wilson UPPER bound on WR (inverse of lower) AND negative expectancy
        # Approximate: if win rate point estimate + spread is still bad, warn.
        candidates_worst = sorted(
            eligible_strats, key=lambda x: x[1]["expectancy"]
        )
        if candidates_worst:
            n, b = candidates_worst[0]
            wlb_loss = _wilson_lower_bound(b["losses"], b["trades"])
            # If lower bound on loss rate is high enough that we're confident
            # it's losing more often than winning, warn.
            if b["expectancy"] < -10 and wlb_loss >= 0.55:
                instructions.append(
                    f"REDUCE {n} — E:${b['expectancy']:.2f}, "
                    f"loss rate Wilson lower bound {wlb_loss*100:.0f}%, skip"
                )

    # Score brackets — same Wilson-bounded logic
    low  = stats["score_brackets"].get("1-3",  {})
    high = stats["score_brackets"].get("8-10", {})

    if low.get("trades", 0) >= MIN_TRADES_FOR_INSTRUCTION:
        loss_wlb = _wilson_lower_bound(low["losses"], low["trades"])
        if loss_wlb >= 0.55:
            instructions.append(
                f"MINIMUM SCORE 6 — low-score (1-3) trades lose ≥{loss_wlb*100:.0f}% statistically"
            )

    if high.get("trades", 0) >= MIN_TRADES_FOR_INSTRUCTION:
        win_wlb = _wilson_lower_bound(high["wins"], high["trades"])
        if win_wlb >= 0.55:
            instructions.append(
                f"HIGH SCORE TRADES WORKING — {high['win_rate']:.0f}%WR on 8-10 "
                f"(Wilson lower bound {win_wlb*100:.0f}%, trust them)"
            )

    # Dead zone warning
    dead = stats["killzones"].get("DEAD", {})
    if dead.get("trades", 0) >= MIN_TRADES_FOR_INSTRUCTION:
        loss_wlb = _wilson_lower_bound(dead["losses"], dead["trades"])
        if loss_wlb >= 0.55:
            instructions.append(
                f"AVOID DEAD ZONE — losing ≥{loss_wlb*100:.0f}% of the time 11am-1:30pm "
                f"(Wilson lower bound)"
            )

    # Best factor (still useful but require larger N)
    best_factors = [
        (n, b) for n, b in stats["factors"].items()
        if b["trades"] >= MIN_TRADES_FOR_INSTRUCTION
    ]
    if best_factors:
        # Find factor with highest Wilson lower bound on win rate
        ranked = sorted(
            [(n, b, _wilson_lower_bound(b["wins"], b["trades"])) for n, b in best_factors],
            key=lambda x: x[2], reverse=True,
        )
        if ranked and ranked[0][2] >= 0.65:
            n, b, wlb = ranked[0]
            instructions.append(
                f"REQUIRE {n} when possible — {b['win_rate']:.0f}%WR when present "
                f"(Wilson lower bound {wlb*100:.0f}%)"
            )

    if not instructions:
        if total < MIN_TRADES_FOR_INSTRUCTION:
            instructions.append(
                f"Insufficient data ({total} trades) for weighted recommendations. "
                f"Apply full framework equally. Patterns emerge after "
                f"{MIN_TRADES_FOR_INSTRUCTION}+ trades per bucket."
            )
        else:
            instructions.append(
                "No bucket yet shows statistically significant edge or bleed. "
                "Continue framework as-is; let the sample grow."
            )
    return instructions


def generate_performance_context() -> str:
    """Concise performance brief for Claude's entry analysis."""
    stats = load_stats()
    total = stats["meta"]["total_trades"]

    if total < MIN_TRADES_FOR_INSTRUCTION:
        return (
            f"PERFORMANCE STATS: {total} trades recorded — "
            f"insufficient data for weighted recommendations ({MIN_TRADES_FOR_INSTRUCTION} minimum). "
            f"Apply framework equally."
        )

    lines = [f"STRATEGY PERFORMANCE STATS ({total} total trades):"]

    # Strategies (min display threshold)
    strats = sorted(
        [(n, b) for n, b in stats["strategies"].items() if b["trades"] >= MIN_TRADES_FOR_DISPLAY],
        key=lambda x: x[1]["expectancy"], reverse=True,
    )
    lines.append("\nSTRATEGY RANKINGS (by expectancy $):")
    for name, b in strats[:6]:
        wlb_note = ""
        if b["trades"] >= MIN_TRADES_FOR_INSTRUCTION:
            wlb = _wilson_lower_bound(b["wins"], b["trades"]) * 100
            wlb_note = f" CI≥{wlb:.0f}%"
        lines.append(
            f"  {_streak_arrow(b.get('recent_streak', 0))} {name}: "
            f"{b['trades']}T {b['win_rate']:.0f}%WR{wlb_note} E:${b['expectancy']:.2f} "
            f"[{''.join(b.get('last_5', []))}]"
        )

    # Confluence factors
    factors = sorted(
        [(n, b) for n, b in stats["factors"].items() if b["trades"] >= MIN_TRADES_FOR_DISPLAY],
        key=lambda x: x[1]["expectancy"], reverse=True,
    )
    lines.append("\nCONFLUENCE FACTORS — TOP 5:")
    for name, b in factors[:5]:
        lines.append(f"  ✅ {name}: {b['win_rate']:.0f}%WR E:${b['expectancy']:.2f} ({b['trades']}T)")

    lines.append("\nCONFLUENCE FACTORS — BOTTOM 3 (be cautious):")
    for name, b in factors[-3:]:
        if b["expectancy"] < 0:
            lines.append(f"  ⚠️  {name}: {b['win_rate']:.0f}%WR E:${b['expectancy']:.2f} ({b['trades']}T)")

    lines.append("\nCONFLUENCE SCORE PERFORMANCE:")
    for bracket, b in sorted(stats["score_brackets"].items(), reverse=True):
        if b["trades"] > 0:
            lines.append(f"  Score {bracket}: {b['win_rate']:.0f}%WR E:${b['expectancy']:.2f} ({b['trades']}T)")

    lines.append("\nKILL ZONE PERFORMANCE:")
    for kz, b in sorted(stats["killzones"].items(), key=lambda x: x[1]["expectancy"], reverse=True):
        if b["trades"] > 0:
            lines.append(f"  {kz}: {b['win_rate']:.0f}%WR E:${b['expectancy']:.2f} ({b['trades']}T)")

    lines.append("\nOR DIRECTION PERFORMANCE:")
    for direction, b in stats["or_direction"].items():
        if b["trades"] > 0:
            lines.append(f"  OR {direction}: {b['win_rate']:.0f}%WR E:${b['expectancy']:.2f} ({b['trades']}T)")

    lines.append("\nAI WEIGHT INSTRUCTIONS (statistically validated, N≥20):")
    for instr in _generate_instructions(stats, total):
        lines.append(f"  → {instr}")

    return "\n".join(lines)


# ─── Dashboard export ──────────────────────────────────────

def get_dashboard_stats() -> dict:
    stats = load_stats()

    def _fmt(items):
        return [
            {"name": n, "trades": b["trades"], "win_rate": b["win_rate"],
             "expectancy": b["expectancy"], "last_5": b.get("last_5", []),
             "streak": b.get("recent_streak", 0)}
            for n, b in items
        ]

    strats  = sorted([(n, b) for n, b in stats["strategies"].items() if b["trades"] > 0],
                     key=lambda x: x[1]["expectancy"], reverse=True)
    factors = sorted([(n, b) for n, b in stats["factors"].items() if b["trades"] > 0],
                     key=lambda x: x[1]["expectancy"], reverse=True)

    return {
        "total_trades": stats["meta"]["total_trades"],
        "strategies":   _fmt(strats[:8]),
        "factors":      _fmt(factors[:10]),
        "score_brackets": {
            k: {"win_rate": v["win_rate"], "expectancy": v["expectancy"], "trades": v["trades"]}
            for k, v in stats["score_brackets"].items() if v["trades"] > 0
        },
        "killzones": {
            k: {"win_rate": v["win_rate"], "expectancy": v["expectancy"], "trades": v["trades"]}
            for k, v in stats["killzones"].items() if v["trades"] > 0
        },
    }


print("Strategy stats loaded")
