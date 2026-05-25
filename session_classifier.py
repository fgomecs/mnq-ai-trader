"""
session_classifier.py — V4.4
=============================
Classifies each trading day into one of five session types using
pure Python — no Claude calls, no API cost.

SessionType: TREND / RANGE / NEWS / HOLIDAY / UNKNOWN

Called by main.py once at OR_ESTABLISHED (9:45 ET) after the
opening range is complete. Result injected into every Claude
prompt for the rest of the session.

How it changes bot behavior (enforced in main.py + claude_brain.py):
  TREND   → normal thresholds (3 signals), ORB preferred
  RANGE   → 7 signals required, VWAP_REVERSION preferred
  NEWS    → thesis gate raised to 80%, max 1 trade
  HOLIDAY → hard block, no entries
  UNKNOWN → conservative 5 signals required
"""

import os
import sys

# Ensure project root is importable
sys.path.insert(0, os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"))


# ─── Session Type Enum ─────────────────────────────────────────

class SessionType:
    TREND   = "TREND"
    RANGE   = "RANGE"
    NEWS    = "NEWS"
    HOLIDAY = "HOLIDAY"
    UNKNOWN = "UNKNOWN"


# ─── Module-level state ────────────────────────────────────────
# Reset each day via set_session_type(SessionType.UNKNOWN) in end_of_day()

_current: str = SessionType.UNKNOWN


def set_session_type(t: str) -> None:
    """Set the current session type. Call once at OR_ESTABLISHED."""
    global _current
    _current = t


def get_current_session_type() -> str:
    """Return the current session type string."""
    return _current


# ─── Classifier ────────────────────────────────────────────────

def classify_session_type(
    snapshot: dict,
    or_range: float,
    avg_volume_20d: float = 0,
) -> str:
    """
    Classify the current session into one of five types.

    Args:
        snapshot:       Current snapshot dict from ibkr_feed.get_snapshot()
        or_range:       OR range in points (abs(or_high - or_low))
        avg_volume_20d: 20-day average volume (0 = skip HOLIDAY check)

    Returns:
        SessionType string — TREND / RANGE / NEWS / HOLIDAY / UNKNOWN

    Classification priority (checked in order):
        1. HOLIDAY  — volume < 50% of average (when avg available)
        2. NEWS     — gap >= 100pts OR active danger zone
        3. TREND    — OR range >= 50pts + MTF aligned + volume >= 90%
        4. RANGE    — OR range <= 35pts OR DOJI OR MTF conflicted
        5. UNKNOWN  — fallback
    """
    try:
        from config import (
            SESSION_CLASSIFIER_TREND_OR_MIN,
            SESSION_CLASSIFIER_RANGE_OR_MAX,
            SESSION_CLASSIFIER_NEWS_GAP_MIN,
        )
    except ImportError:
        SESSION_CLASSIFIER_TREND_OR_MIN = 50
        SESSION_CLASSIFIER_RANGE_OR_MAX = 35
        SESSION_CLASSIFIER_NEWS_GAP_MIN = 100

    gap     = snapshot.get("gap") or {}
    mtf     = snapshot.get("mtf_alignment") or ""
    or_dir  = snapshot.get("or_direction") or ""
    rel_vol = snapshot.get("or_relative_volume") or 1.0
    volume  = snapshot.get("volume") or 0
    danger  = snapshot.get("news_danger_zone") or False
    gap_sz  = gap.get("gap_size") or 0

    # 1. HOLIDAY — volume below 50% of 20-day average
    if avg_volume_20d > 0 and volume > 0:
        if volume < avg_volume_20d * 0.50:
            return SessionType.HOLIDAY

    # 2. NEWS — large overnight gap or active news danger zone
    if gap_sz >= SESSION_CLASSIFIER_NEWS_GAP_MIN or danger:
        return SessionType.NEWS

    # 3. TREND — large OR + MTF aligned + strong relative volume
    if (or_range >= SESSION_CLASSIFIER_TREND_OR_MIN
            and mtf in ("BULLISH_ALIGNED", "BEARISH_ALIGNED")
            and float(rel_vol) >= 0.90):
        return SessionType.TREND

    # 4. RANGE — small OR, DOJI open, or conflicted timeframes
    if (or_range <= SESSION_CLASSIFIER_RANGE_OR_MAX
            or or_dir == "DOJI"
            or mtf == "CONFLICTED"):
        return SessionType.RANGE

    # 5. UNKNOWN — doesn't fit cleanly into any category
    return SessionType.UNKNOWN


# ─── Context strings for Claude prompts ───────────────────────

_CONTEXT = {
    SessionType.TREND: (
        "TREND DAY: Strong directional session expected. "
        "ORB pullbacks and continuation entries preferred. "
        "Normal signal thresholds apply (3+). "
        "Base win rate ORB_PULLBACK: 68-72% on trend days."
    ),
    SessionType.RANGE: (
        "RANGE DAY: Choppy mean-reverting session expected. "
        "ORB breakouts will likely fail — base win rate drops to 31-38%. "
        "Favor VWAP_REVERSION (72-78% range day edge) and OR_EXTREME_FADE. "
        "Require 7+ signals. Treat all ORB setups with low confidence."
    ),
    SessionType.NEWS: (
        "NEWS DAY: Macro event driving unpredictable price action. "
        "All ICT signals are less reliable than normal. "
        "Require THESIS_PROBABILITY >= 80 before any entry. "
        "Max 1 trade. Consider wider stops."
    ),
    SessionType.HOLIDAY: (
        "HOLIDAY/LOW LIQUIDITY: Thin market with erratic fills. "
        "DO NOT TRADE — output HOLD on all entry decisions."
    ),
    SessionType.UNKNOWN: (
        "SESSION TYPE UNCLASSIFIED: Conditions unclear. "
        "Use conservative thresholds — require 5+ signals before entry."
    ),
}


def get_session_type_context(t: str = None) -> str:
    """
    Return a one-paragraph context string for Claude prompt injection.
    If t is None, uses the current session type.
    """
    if t is None:
        t = _current
    return _CONTEXT.get(t, _CONTEXT[SessionType.UNKNOWN])


# ─── Standalone test ───────────────────────────────────────────

if __name__ == "__main__":
    print("session_classifier.py loaded OK")
    for st in (SessionType.TREND, SessionType.RANGE,
               SessionType.NEWS, SessionType.HOLIDAY, SessionType.UNKNOWN):
        print(f"\n{st}:")
        print(f"  {get_session_type_context(st)}")
