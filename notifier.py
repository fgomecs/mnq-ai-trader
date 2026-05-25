"""
notifier.py — Push notifications via Pushover
iPhone push notifications for all key bot events.
Requires: PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN in .env
"""

import os
import urllib.request
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader")) / ".env")

PUSHOVER_USER  = os.getenv("PUSHOVER_USER_KEY", "")
PUSHOVER_TOKEN = os.getenv("PUSHOVER_API_TOKEN", "")
NOTIFY_ENABLED = os.getenv("NOTIFY_ENABLED", "true").lower() == "true"
PUSHOVER_URL   = "https://api.pushover.net/1/messages.json"


def _clean(s: str) -> str:
    """Remove characters that cause Pushover 400 errors."""
    return str(s).encode("ascii", "ignore").decode("ascii")[:500]


def notify(title: str, message: str = "", priority: int = 0) -> bool:
    if not NOTIFY_ENABLED or not PUSHOVER_USER or not PUSHOVER_TOKEN:
        return False
    try:
        data = urllib.parse.urlencode({
            "token":    PUSHOVER_TOKEN,
            "user":     PUSHOVER_USER,
            "title":    _clean(title),
            "message":  _clean(message or title),
            "priority": priority,
        }).encode("utf-8")
        req = urllib.request.Request(PUSHOVER_URL, data=data)
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as e:
        print(f"[notifier] Failed: {e}")
        return False


def notify_premarket(summary: str) -> bool:
    return notify("PRE-MARKET READY", summary[:200])

def notify_or_established(direction: str, or_high: float, or_low: float) -> bool:
    icons = {"BULL": "BULL", "BEAR": "BEAR", "DOJI": "DOJI"}
    label = icons.get(direction, direction)
    return notify(
        f"OR {label} -- {round(or_high-or_low,0):.0f}pt range",
        f"High: {or_high:.2f} | Low: {or_low:.2f}"
    )

def notify_trade_entered(direction: str, entry: float, stop: float, target: float) -> bool:
    rr = round(abs(target-entry)/abs(entry-stop), 1) if abs(entry-stop) > 0 else 0
    icon = "LONG" if direction == "LONG" else "SHORT"
    return notify(
        f"{icon} ENTERED @ {entry:.2f}",
        f"Stop: {stop:.2f} | Target: {target:.2f} | R:R {rr}:1",
        priority=1
    )

def notify_trade_exited(direction: str, entry: float, exit_price: float, pnl: float, reason: str) -> bool:
    result = "WIN" if pnl >= 0 else "LOSS"
    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    return notify(
        f"{direction} CLOSED {pnl_str} [{result}]",
        f"Entry: {entry:.2f} -> Exit: {exit_price:.2f} | {reason}",
        priority=1
    )

def notify_stop_to_breakeven(direction: str, entry: float) -> bool:
    return notify(
        "STOP -> BREAKEVEN",
        f"{direction} @ {entry:.2f} -- free trade, risk eliminated"
    )

def notify_eod_summary(daily_pnl: float, wins: int, losses: int, net_liq: float, version: str) -> bool:
    total = wins + losses
    wr = round(wins/total*100) if total > 0 else 0
    pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"
    return notify(
        f"EOD: {pnl_str} | {wins}W {losses}L",
        f"Win Rate: {wr}% | Net Liq: ${net_liq:,.0f} | v{version}"
    )

def notify_backtest(date_str: str, daily_pnl: float, wins: int, losses: int, win_rate: float) -> bool:
    pnl_str = f"+${daily_pnl:.2f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):.2f}"
    return notify(
        f"BACKTEST {date_str}",
        f"P&L: {pnl_str} | {wins}W {losses}L | {win_rate:.0f}% WR",
        priority=-1
    )

def notify_learning_done(version: str, key_finding: str = "") -> bool:
    return notify(
        f"LEARNING DONE -- v{version}",
        key_finding[:150] if key_finding else "EOD learning complete.",
        priority=-1
    )

def notify_error(location: str, error: str) -> bool:
    return notify(
        "BOT ERROR",
        f"{location} -- {str(error)[:150]}",
        priority=1
    )

def notify_loss_warning(used: float, limit: float) -> bool:
    pct = round(used/limit*100)
    return notify(
        f"LOSS WARNING -- {pct}% used",
        f"${used:.0f} of ${limit:.0f} daily limit",
        priority=1
    )

def notify_bot_sleeping(wake_time: str) -> bool:
    return notify("BOT SLEEPING", f"Wakes: {wake_time}", priority=-1)

def notify_bot_awake() -> bool:
    return notify("BOT AWAKE", "Connected -- pre-market in 10 min")

def notify_ibkr_disconnected() -> bool:
    return notify("IBKR DISCONNECTED", "Lost connection -- attempting reconnect", priority=1)

def notify_ibkr_reconnected() -> bool:
    return notify("IBKR RECONNECTED", "Connection restored")

def notify_consecutive_losses(count: int, daily_pnl: float) -> bool:
    return notify(
        f"{count} CONSECUTIVE LOSSES",
        f"Daily P&L: -${abs(daily_pnl):.2f} -- consider sitting out",
        priority=1
    )


print("Notifier loaded --", "Pushover ready" if PUSHOVER_TOKEN else "NOT configured")
