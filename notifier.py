"""
notifier.py — Push notification system for MNQ AI Trader
Sends iPhone push notifications via ntfy.sh (free).

Setup:
1. Install ntfy app on iPhone from App Store
2. Subscribe to your topic in the app
3. Set NTFY_TOPIC=your_unique_topic in .env
   Example: NTFY_TOPIC=dobot_mnq_abc123
   Make it unique — anyone who knows your topic can see notifications

Usage:
    from notifier import notify
    notify("Title", "Message body", priority="default")
"""

import os
import requests
from datetime import datetime
import pytz

NTFY_TOPIC   = os.getenv("NTFY_TOPIC", "")
NTFY_URL     = os.getenv("NTFY_URL", "https://ntfy.sh")
NOTIFY_ENABLED = os.getenv("NOTIFY_ENABLED", "true").lower() == "true"

eastern = pytz.timezone("US/Eastern")

def notify(title: str, message: str = "", priority: str = "default", tags: str = "") -> bool:
    """
    Send push notification to iPhone via ntfy.sh

    priority: min, low, default, high, urgent
    tags: comma-separated emoji tags e.g. "white_check_mark,chart_with_upwards_trend"

    Returns True if sent successfully, False otherwise.
    """
    if not NOTIFY_ENABLED or not NTFY_TOPIC:
        return False

    try:
        headers = {
            "Title": title,
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = tags

        url = f"{NTFY_URL}/{NTFY_TOPIC}"
        resp = requests.post(
            url,
            data=message.encode("utf-8"),
            headers=headers,
            timeout=5
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[notifier] Failed to send notification: {e}")
        return False


# ── Convenience functions for each event type ──────────────

def notify_premarket(analysis_summary: str) -> None:
    notify(
        title="📋 PRE-MARKET READY",
        message=analysis_summary[:200],
        priority="default",
        tags="memo"
    )

def notify_or_established(direction: str, or_high: float, or_low: float) -> None:
    range_pts = round(or_high - or_low, 2)
    icons = {"BULL": "📈", "BEAR": "📉", "DOJI": "➡️"}
    icon = icons.get(direction, "📊")
    notify(
        title=f"{icon} OR {direction} — {range_pts}pt range",
        message=f"High: {or_high:.2f} | Low: {or_low:.2f}",
        priority="default",
        tags="chart_with_upwards_trend"
    )

def notify_trade_entered(direction: str, entry: float, stop: float, target: float) -> None:
    rr = round(abs(target - entry) / abs(entry - stop), 1) if abs(entry - stop) > 0 else 0
    icon = "🟢" if direction == "LONG" else "🔴"
    notify(
        title=f"{icon} {direction} ENTERED @ {entry:.2f}",
        message=f"Stop: {stop:.2f} | Target: {target:.2f} | R:R: {rr}:1",
        priority="high",
        tags="bell"
    )

def notify_trade_exited(direction: str, entry: float, exit_price: float, pnl: float, reason: str) -> None:
    won = pnl >= 0
    icon = "✅" if won else "❌"
    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    notify(
        title=f"{icon} {direction} CLOSED {pnl_str}",
        message=f"Entry: {entry:.2f} → Exit: {exit_price:.2f} | {reason}",
        priority="high",
        tags="white_check_mark" if won else "x"
    )

def notify_stop_to_breakeven(direction: str, entry: float) -> None:
    notify(
        title="🔒 STOP → BREAKEVEN",
        message=f"{direction} @ {entry:.2f} — free trade, risk eliminated",
        priority="default",
        tags="lock"
    )

def notify_eod_summary(daily_pnl: float, wins: int, losses: int, net_liq: float, version: str) -> None:
    total = wins + losses
    wr = round(wins / total * 100) if total > 0 else 0
    icon = "📈" if daily_pnl >= 0 else "📉"
    pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"
    notify(
        title=f"{icon} EOD: {pnl_str} | {wins}W {losses}L",
        message=f"Win Rate: {wr}% | Net Liq: ${net_liq:,.0f} | v{version}",
        priority="default",
        tags="chart_with_upwards_trend"
    )

def notify_backtest(date_str: str, daily_pnl: float, wins: int, losses: int, win_rate: float) -> None:
    icon = "📈" if daily_pnl >= 0 else "📉"
    pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"
    notify(
        title=f"🔬 BACKTEST {date_str}",
        message=f"P&L: {pnl_str} | {wins}W {losses}L | {win_rate:.0f}% WR",
        priority="min",
        tags="microscope"
    )

def notify_learning_done(version: str, key_finding: str = "") -> None:
    notify(
        title=f"🧠 LEARNING DONE — v{version}",
        message=key_finding[:150] if key_finding else "EOD learning session complete.",
        priority="min",
        tags="brain"
    )

def notify_error(location: str, error: str) -> None:
    notify(
        title="🚨 BOT ERROR",
        message=f"{location}: {str(error)[:150]}",
        priority="urgent",
        tags="rotating_light"
    )

def notify_loss_warning(used: float, limit: float) -> None:
    pct = round(used / limit * 100)
    notify(
        title=f"⚠️ LOSS WARNING — {pct}% of limit used",
        message=f"${used:.0f} of ${limit:.0f} daily loss limit reached",
        priority="high",
        tags="warning"
    )

def notify_bot_sleeping(wake_time: str) -> None:
    notify(
        title="😴 BOT SLEEPING",
        message=f"Wakes: {wake_time}",
        priority="min",
        tags="zzz"
    )

def notify_bot_awake() -> None:
    now_et = datetime.now(eastern).strftime("%H:%M ET")
    notify(
        title="🌅 BOT AWAKE",
        message=f"Connected at {now_et} — pre-market in 10 min",
        priority="default",
        tags="sunrise"
    )

def notify_ibkr_disconnected() -> None:
    notify(
        title="📡 IBKR DISCONNECTED",
        message="Lost connection to Interactive Brokers — attempting reconnect",
        priority="urgent",
        tags="no_entry_sign"
    )

def notify_ibkr_reconnected() -> None:
    notify(
        title="📡 IBKR RECONNECTED",
        message="Connection restored to Interactive Brokers",
        priority="default",
        tags="white_check_mark"
    )

def notify_consecutive_losses(count: int, daily_pnl: float) -> None:
    notify(
        title=f"⚠️ {count} CONSECUTIVE LOSSES",
        message=f"Daily P&L: -${abs(daily_pnl):.2f} — consider sitting out",
        priority="high",
        tags="warning"
    )


print("Notifier loaded — topic:", NTFY_TOPIC if NTFY_TOPIC else "NOT SET (add NTFY_TOPIC to .env)")
