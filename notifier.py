"""
notifier.py — Pushover push notifications for MNQ AI Trader

Reads PUSHOVER_USER_KEY, PUSHOVER_API_TOKEN, and NOTIFY_ENABLED from env.
Uses stdlib urllib so no extra dependency is required.
Failures are swallowed — notifications must never break the trading loop.
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from dotenv import load_dotenv

load_dotenv()

PUSHOVER_USER  = os.getenv("PUSHOVER_USER_KEY", "").strip()
PUSHOVER_TOKEN = os.getenv("PUSHOVER_API_TOKEN", "").strip()
NOTIFY_ENABLED = os.getenv("NOTIFY_ENABLED", "true").strip().lower() == "true"

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def notify(title: str, message: str, priority: int = 0) -> None:
    """Send a Pushover notification. Silent on failure."""
    if not NOTIFY_ENABLED or not PUSHOVER_USER or not PUSHOVER_TOKEN:
        return
    try:
        data = urllib.parse.urlencode({
            "token":    PUSHOVER_TOKEN,
            "user":     PUSHOVER_USER,
            "title":    title,
            "message":  message,
            "priority": priority,
        }).encode("utf-8")
        req = urllib.request.Request(PUSHOVER_URL, data=data)
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        print(f"[notifier] Failed to send notification: {e}")


# ─── Trading lifecycle ────────────────────────────────────

def notify_premarket(analysis_summary: str) -> None:
    notify("🌅 PRE-MARKET ANALYSIS", analysis_summary)


def notify_or_established(direction: str, or_high: float, or_low: float) -> None:
    notify(
        f"📊 OPENING RANGE: {direction}",
        f"OR High: {or_high}\nOR Low: {or_low}",
    )


def notify_trade_entered(direction: str, entry: float, stop: float, target: float) -> None:
    notify(
        f"🎯 ENTERED {direction} @ {entry}",
        f"Stop: {stop}\nTarget: {target}",
        priority=1,
    )


def notify_trade_exited(direction: str, entry: float, exit_price: float, pnl: float, reason: str) -> None:
    emoji = "✅" if pnl > 0 else "❌"
    notify(
        f"{emoji} EXIT {direction} @ {exit_price}",
        f"Entry: {entry}\nP&L: ${pnl:.2f}\nReason: {reason}",
        priority=1,
    )


def notify_stop_to_breakeven(direction: str, entry: float) -> None:
    notify(f"🛡️ STOP → BREAKEVEN ({direction})", f"Entry: {entry} — risk-free")


def notify_eod_summary(daily_pnl: float, wins: int, losses: int, net_liq: float, version: str) -> None:
    emoji = "🟢" if daily_pnl > 0 else ("🔴" if daily_pnl < 0 else "⚪")
    notify(
        f"{emoji} EOD SUMMARY  P&L: ${daily_pnl:+.2f}",
        f"Wins: {wins}  Losses: {losses}\nNet Liq: ${net_liq:,.2f}\nVersion: {version}",
    )


def notify_backtest(date_str: str, daily_pnl: float, wins: int, losses: int, win_rate: float) -> None:
    notify(
        f"📈 BACKTEST {date_str}  P&L: ${daily_pnl:+.2f}",
        f"Wins: {wins}  Losses: {losses}  Win rate: {win_rate:.1%}",
    )


def notify_learning_done(version: str, key_finding: str = "") -> None:
    notify(f"🧠 LEARNING DONE  v{version}", key_finding or "Ablation + learning complete")


# ─── Risk / warnings ──────────────────────────────────────

def notify_error(location: str, error: str) -> None:
    notify(f"🚨 ERROR @ {location}", error[:300], priority=2)


def notify_loss_warning(used: float, limit: float) -> None:
    notify(
        "⚠️ DAILY LOSS WARNING",
        f"Used ${used:.2f} of ${limit:.2f} cap ({used/limit:.0%})",
        priority=1,
    )


def notify_consecutive_losses(count: int, daily_pnl: float) -> None:
    notify(
        f"⚠️ {count} CONSECUTIVE LOSSES",
        f"Daily P&L: -${abs(daily_pnl):.2f}",
        priority=1,
    )


# ─── System / connection ──────────────────────────────────

def notify_bot_sleeping(wake_time: str) -> None:
    notify("😴 BOT SLEEPING", f"Next wake: {wake_time}")


def notify_bot_awake() -> None:
    notify("🌅 BOT AWAKE", "Connected — pre-market in 10 min")


def notify_ibkr_disconnected() -> None:
    notify("📡 IBKR DISCONNECTED", "Lost connection — attempting reconnect", priority=1)


def notify_ibkr_reconnected() -> None:
    notify("📡 IBKR RECONNECTED", "Connection restored")


print("Notifier loaded —", "Pushover ready" if PUSHOVER_TOKEN else "NOT configured")
