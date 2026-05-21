import json
import os
from datetime import datetime
import pytz

DASHBOARD_FILE = "C:\\trading\\dashboard_data.json"
eastern = pytz.timezone('US/Eastern')


def update_dashboard(
    position=0,
    entry_price=None,
    stop_price=None,
    target_price=None,
    daily_pnl=0,
    max_loss=500,
    trades=None,
    last_decision=None,
    last_reasoning=None,
    last_confidence=None,
    bias="NEUTRAL",
    amd_phase="",
    confluence=None,
    session_levels="",
    left_on_table=None,
    claude_status="ANALYZING"
):
    """Write current state to dashboard JSON file"""
    try:
        now = datetime.now(eastern)

        # Determine position string
        if position > 0:
            pos_str = "LONG"
        elif position < 0:
            pos_str = "SHORT"
        else:
            pos_str = "FLAT"

        # Extract confluence from reasoning
        detected_confluence = []
        if last_reasoning:
            reasoning_upper = last_reasoning.upper()
            concepts = [
                "FVG", "ORDER BLOCK", "OB", "VWAP", "DELTA",
                "DOM", "KILLZONE", "AMD", "LIQUIDITY", "HTF",
                "SWING", "SCALP", "MOMENTUM", "BREAKOUT"
            ]
            for c in concepts:
                if c in reasoning_upper:
                    detected_confluence.append(c)

        if confluence:
            detected_confluence.extend(confluence)

        # Determine bias from reasoning
        detected_bias = bias
        if last_reasoning:
            r = last_reasoning.upper()
            if "BULLISH" in r and "BEARISH" not in r:
                detected_bias = "BULLISH"
            elif "BEARISH" in r and "BULLISH" not in r:
                detected_bias = "BEARISH"
            elif "BULLISH" in r and "BEARISH" in r:
                detected_bias = "MIXED"

        # Build trade list for dashboard
        trade_list = []
        if trades:
            for t in trades:
                trade_list.append({
                    "time": t.get("time", ""),
                    "direction": "LONG" if t.get("entry_direction") == "BUY" else "SHORT",
                    "entry": t.get("entry", "--"),
                    "exit": t.get("exit", "--"),
                    "pnl": t.get("pnl", 0),
                    "mode": t.get("mode", "--"),
                    "confluence": t.get("confluence", []),
                    "note": t.get("exit_reason", "")[:80]
                })

        # Detect left on table
        left_on_table_items = left_on_table or []
        if trades:
            for t in trades:
                if t.get("pnl", 0) > 0 and t.get("exit_reason", "").upper() == "TARGET HIT":
                    pass  # Good exit
                elif t.get("pnl", 0) > 0 and "STOP" not in t.get("exit_reason", "").upper():
                    # Exited with profit but not at target — potential left on table
                    potential = t.get("target_price", 0)
                    actual_exit = t.get("exit", 0)
                    if potential and actual_exit and float(potential) != float(actual_exit):
                        left_on_table_items.append(
                            f"Trade at {t.get('entry')} — exited ${t.get('pnl',0):.2f}, "
                            f"target was {potential}"
                        )

        data = {
            "timestamp": now.isoformat(),
            "time_et": now.strftime("%H:%M:%S"),
            "position": pos_str,
            "entryPrice": entry_price,
            "stopPrice": stop_price,
            "targetPrice": target_price,
            "dailyPnl": round(daily_pnl, 2),
            "maxLoss": max_loss,
            "claudeStatus": claude_status,
            "bias": detected_bias,
            "amdPhase": amd_phase,
            "confluence": list(set(detected_confluence)),
            "sessionLevels": session_levels,
            "leftOnTable": left_on_table_items,
            "trades": trade_list,
            "reasoning": {
                "time": now.strftime("%H:%M:%S"),
                "decision": last_decision or "HOLD",
                "confidence": last_confidence or "",
                "reasoning": (last_reasoning or "")[:500]
            } if last_decision else None
        }

        with open(DASHBOARD_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    except Exception as e:
        print(f"Dashboard write error: {e}")


print("Dashboard writer loaded successfully")
