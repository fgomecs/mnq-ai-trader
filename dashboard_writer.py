"""
Dashboard Writer for MNQ AI Trader.

Session 2 fix from audit:
  P1.6 — Reasoning block now carries an ISO timestamp. The merge logic
         that preserves reasoning across fast-ticker writes no longer
         silently shows 30-minute-old reasoning as "current"; the
         dashboard can compute its age and grey it out.

Writes two JSON files:
  - dashboard_data.json : full state, read by the browser dashboard
  - price_data.json     : lightweight price-only, written every second
"""

import json
import os
import tempfile
from datetime import datetime
from typing import Any, Optional


def _atomic_json_write(path: str, payload: dict, indent: Optional[int] = None,
                       ensure_ascii: bool = True) -> None:
    """
    Write JSON via temp file + os.replace so a crash mid-write cannot leave a
    zero-byte / truncated file on disk (which would break the bot or the
    browser dashboard on next read). os.replace is atomic on Windows + POSIX.
    """
    dir_ = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=indent, ensure_ascii=ensure_ascii)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

import pytz

try:
    from logger import logger as _logger
except Exception:
    _logger = None

try:
    from config import DASHBOARD_FILE, PRICE_FILE, LIVE_DATA_ACTIVE, VERSION
except ImportError:
    _base = os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader")
    DASHBOARD_FILE   = os.path.join(_base, "dashboard_data.json")
    PRICE_FILE       = os.path.join(_base, "price_data.json")
    LIVE_DATA_ACTIVE = False
    VERSION          = "?"

eastern = pytz.timezone("US/Eastern")

_CONFLUENCE_KEYWORDS = (
    "FVG", "ORDER BLOCK", "OB", "VWAP", "DELTA", "DOM",
    "KILLZONE", "AMD", "LIQUIDITY", "HTF", "SWING",
    "SCALP", "MOMENTUM", "BREAKOUT",
)


# ─── Fast price-only write (1 Hz) ──────────────────────────

def update_price_only(
    price: float, bid: float, ask: float, volume: float,
    position: int, entry_price: Optional[float],
    stop_price: Optional[float], target_price: Optional[float],
    daily_pnl: float, account: Optional[dict] = None,
    current_bar: Optional[dict] = None,
) -> None:
    """Minimal JSON write — speed is paramount, no merging."""
    try:
        pos_str = "LONG" if position > 0 else "SHORT" if position < 0 else "FLAT"
        data = {
            "t":          datetime.now(eastern).strftime("%H:%M:%S"),
            "price":      price,
            "bid":        bid,
            "ask":        ask,
            "volume":     volume,
            "position":   pos_str,
            "entry":      float(entry_price)  if entry_price  else 0,
            "stop":       float(stop_price)   if stop_price   else 0,
            "target":     float(target_price) if target_price else 0,
            "pnl":        round(daily_pnl, 2),
            "netLiq":     account.get("netLiq")     if account else None,
            "unrealized": account.get("unrealized") if account else None,
            "currentBarOpen": current_bar["open"] if current_bar else None,
            "currentBarHigh": current_bar["high"] if current_bar else None,
            "currentBarLow":  current_bar["low"]  if current_bar else None,
        }
        # Atomic write — a torn write would leave price_data.json invalid
        # and the dashboard's 1Hz reader would error every tick until the
        # next successful write.
        _atomic_json_write(PRICE_FILE, data)
    except Exception:
        pass  # silent — speed-critical path


# ─── Full dashboard write (every Claude cycle) ─────────────

def _snap(snapshot: Optional[dict], key: str, default: Any = None) -> Any:
    return snapshot.get(key, default) if snapshot else default


def _detect_confluence(reasoning: Optional[str], extra: Optional[list]) -> list:
    detected = []
    if reasoning:
        r = reasoning.upper()
        detected = [kw for kw in _CONFLUENCE_KEYWORDS if kw in r]
    if extra:
        detected.extend(extra)
    return list(set(detected))


def _detect_bias(reasoning: Optional[str], default: str) -> str:
    if not reasoning:
        return default
    r = reasoning.upper()
    has_bull = "BULLISH" in r
    has_bear = "BEARISH" in r
    if has_bull and not has_bear:
        return "BULLISH"
    if has_bear and not has_bull:
        return "BEARISH"
    if has_bull and has_bear:
        return "MIXED"
    return default


def update_dashboard(
    position: int = 0,
    entry_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    current_price: Optional[float] = None,
    daily_pnl: float = 0.0,
    max_loss: float = 500.0,
    trades: Optional[list] = None,
    last_decision: Optional[str] = None,
    last_reasoning: Optional[str] = None,
    last_confidence: Optional[str] = None,
    bias: str = "NEUTRAL",
    amd_phase: str = "",
    confluence: Optional[list] = None,
    session_levels: str = "",
    left_on_table: Optional[list] = None,
    claude_status: str = "ANALYZING",
    account: Optional[dict] = None,
    snapshot: Optional[dict] = None,
    bot_sleeping: bool = False,
    wake_time: str = "",
    **kwargs,
) -> None:
    """Write full state to dashboard JSON, merging with existing file
    so the fast ticker never wipes Claude's reasoning."""
    try:
        now = datetime.now(eastern)

        pos_str          = "LONG" if position > 0 else "SHORT" if position < 0 else "FLAT"
        detected_conf    = _detect_confluence(last_reasoning, confluence)
        detected_bias    = _detect_bias(last_reasoning, bias)

        trade_list = [
            {
                "time":       t.get("time", ""),
                "action":     t.get("action", ""),
                "entry":      t.get("entry"),
                "exit":       t.get("exit"),
                "pnl":        t.get("pnl"),
                "mode":       t.get("mode", "--"),
                "exit_reason": (t.get("exit_reason") or "")[:100],
            }
            for t in (trades or [])
        ]

        s = snapshot or {}
        or_high        = s.get("or_high")
        or_low         = s.get("or_low")
        or_broken_up   = s.get("or_broken_up",    False)
        or_broken_down = s.get("or_broken_down",  False)
        or_attempts    = s.get("or_break_attempts", 0)

        # P1.6 — Reasoning block now carries an ISO timestamp.
        # When the fast ticker writes (no Claude reasoning attached), the
        # merge logic below preserves whatever was there last. The dashboard
        # uses `iso_ts` to compute and display age, then greys out anything
        # older than 5 minutes.
        reasoning_block = (
            {
                "time":       now.strftime("%H:%M:%S"),
                "iso_ts":     now.isoformat(),
                "decision":   last_decision or "HOLD",
                "confidence": last_confidence or "",
                "reasoning":  (last_reasoning or "")[:500],
            }
            if last_decision
            else None
        )

        data: dict = {
            "timestamp":   now.isoformat(),
            "time_et":     now.strftime("%H:%M:%S"),
            "data_mode":   "BOT SLEEPING" if bot_sleeping else ("LIVE L2" if LIVE_DATA_ACTIVE else "DELAYED"),
            "botSleeping": bot_sleeping,
            "wakeTime":    wake_time,

            "position":     pos_str,
            "entryPrice":   entry_price,
            "stopPrice":    stop_price,
            "targetPrice":  target_price,
            "currentPrice": current_price,
            "dailyPnl":     round(daily_pnl, 2),
            "maxLoss":      max_loss,

            "claudeStatus":        claude_status or "IDLE",
            "lastDecision":        last_decision or "",
            "lastReasoning":       (last_reasoning or "")[:500],
            "lastConfidence":      last_confidence or "",
            "lastStrategy":        kwargs.get("last_strategy", ""),
            "lastConfluence":      kwargs.get("last_confluence", ""),
            "lastConfluenceScore": kwargs.get("last_confluence_score", 0),
            "thesisProbability":   kwargs.get("thesis_probability", 0),
            "lastThesisStatus":    kwargs.get("last_thesis_status", ""),
            "botVersion":          VERSION,
            "reasoning":           reasoning_block,

            "bias":         detected_bias,
            "amdPhase":     s.get("amd_phase", amd_phase),
            "htfBias":      s.get("htf_bias", ""),
            "killzone":     s.get("killzone", ""),
            "confluence":   detected_conf,
            "sessionLevels": session_levels or s.get("session_levels", ""),
            "leftOnTable":  left_on_table or [],
            "sessionHigh":  s.get("session_high", 0),
            "sessionLow":   s.get("session_low",  0),
            "premktHigh":   s.get("premarket_high"),
            "premktLow":    s.get("premarket_low"),

            # ICT levels (dashboard reads these by these exact keys)
            "fair_value_gaps": s.get("fair_value_gaps", ""),
            "fvg_levels":      s.get("fvg_levels",      []),
            "order_blocks":    s.get("order_blocks",    ""),
            "ob_levels":       s.get("ob_levels",       []),
            "liquidity_pools": s.get("liquidity_pools", ""),
            "liq_levels":      s.get("liq_levels",      []),
            "daily_zones":     s.get("daily_zones",     {}),
            "choch":           s.get("choch",           ""),
            "inducement":      s.get("inducement",      ""),
            "candle_patterns": s.get("candle_patterns", ""),
            "tape_bias":       s.get("tape_bias",       "NEUTRAL"),
            "tape_text":       s.get("tape_text",       ""),
            "mtf_alignment":   s.get("mtf_alignment",   ""),
            "delta_trend":     s.get("delta_trend",     ""),
            "market_structure": s.get("market_structure", ""),

            "bid":          s.get("bid"),
            "ask":          s.get("ask"),
            "volume":       s.get("volume"),
            "vwap":         s.get("vwap"),
            "cumDelta":     s.get("cumulative_delta", 0),
            "deltaLastBar": s.get("delta_last_bar"),
            "candleText":   s.get("candles"),

            "orHigh":             or_high,
            "orLow":              or_low,
            "orBrokenUp":         or_broken_up,
            "orBrokenDown":       or_broken_down,
            "orAttempts":         or_attempts,
            "or_direction":       s.get("or_direction"),
            "or_relative_volume": s.get("or_relative_volume"),

            "newsText":       s.get("news_text", ""),
            "newsDangerZone": s.get("news_danger_zone", False),
            "nextHighImpact": s.get("next_high_impact"),
            "nextEventFull":  s.get("next_event_full"),
            "newsEvents":     s.get("events_today", []),
            "ibkrHeadlines":  s.get("ibkr_headlines", []),
            "bars1min":       s.get("bars_1min", []),
            "bars5min":       s.get("bars_5min", []),
            "currentBarOpen": s.get("currentBarOpen"),
            "tradeMarkers":   s.get("trade_markers", []),

            "account":    account or {},
            "netLiq":     (account or {}).get("net_liquidation", 0),
            "ibkrPnl":    (account or {}).get("realized_pnl",   0),
            "unrealized": (account or {}).get("unrealized_pnl", 0),

            "trades": trade_list,
        }

        # Merge: preserve fields from previous write when not updated this cycle.
        # P1.6 — the reasoning block now carries iso_ts so the dashboard can
        # tell that what it's seeing is X minutes old.
        try:
            if os.path.exists(DASHBOARD_FILE):
                with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if data["reasoning"] is None and existing.get("reasoning"):
                    data["reasoning"] = existing["reasoning"]
                if not data["candleText"] and existing.get("candleText"):
                    data["candleText"] = existing["candleText"]
                if not data["sessionLevels"] and existing.get("sessionLevels"):
                    data["sessionLevels"] = existing["sessionLevels"]
                if not data["newsText"] and existing.get("newsText"):
                    data["newsText"]       = existing["newsText"]
                    data["newsDangerZone"] = existing.get("newsDangerZone", False)
                    data["nextHighImpact"] = existing.get("nextHighImpact")
                    data["newsEvents"]     = existing.get("newsEvents", [])
                # Preserve ICT/structure fields when fast-ticker writes lack
                # the full snapshot. Without this, the dashboard would clear
                # FVG/OB/CHoCH/etc every 10s when the live patch fires.
                for field in ("fair_value_gaps", "fvg_levels", "order_blocks", "ob_levels",
                              "liquidity_pools", "liq_levels", "daily_zones",
                              "choch", "inducement", "candle_patterns",
                              "tape_bias", "tape_text", "mtf_alignment",
                              "delta_trend", "market_structure", "htfBias"):
                    if not data.get(field) and existing.get(field):
                        data[field] = existing[field]
        except Exception as merge_err:
            if _logger:
                _logger.debug(f"Dashboard merge skipped: {merge_err}")

        # Atomic write — dashboard_data.json is read concurrently by the
        # browser; torn writes would intermittently break the UI.
        _atomic_json_write(DASHBOARD_FILE, data, indent=2, ensure_ascii=False)

    except Exception as e:
        # Failure here is non-fatal (next cycle overwrites), but logging via
        # logger ensures the error lands in trading_*.log not just stdout.
        if _logger:
            _logger.warning(f"Dashboard write error: {e}")
        else:
            print(f"Dashboard write error: {e}")


if _logger:
    _logger.info("Dashboard writer loaded")
