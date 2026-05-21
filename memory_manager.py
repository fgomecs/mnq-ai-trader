import os
import json
from datetime import datetime, timedelta
import pytz
from logger import logger

MEMORY_DIR = "C:\\trading\\memory"
eastern = pytz.timezone('US/Eastern')

def ensure_memory_dir():
    os.makedirs(MEMORY_DIR, exist_ok=True)

def save_daily_summary(trades: list, daily_pnl: float, analysis_log: list):
    """Generate and save end of day summary"""
    ensure_memory_dir()
    today = datetime.now(eastern).strftime('%Y-%m-%d')
    filepath = os.path.join(MEMORY_DIR, f"summary_{today}.md")

    # Trade stats
    total_trades = len(trades)
    winners = [t for t in trades if t.get('pnl', 0) > 0]
    losers = [t for t in trades if t.get('pnl', 0) < 0]
    win_rate = (len(winners) / total_trades * 100) if total_trades > 0 else 0
    avg_winner = sum(t['pnl'] for t in winners) / len(winners) if winners else 0
    avg_loser = sum(t['pnl'] for t in losers) / len(losers) if losers else 0

    # Best and worst trades
    best_trade = max(trades, key=lambda t: t.get('pnl', 0)) if trades else None
    worst_trade = min(trades, key=lambda t: t.get('pnl', 0)) if trades else None

    # Collect setup types used
    setups_used = {}
    for t in trades:
        mode = t.get('mode', 'UNKNOWN')
        setups_used[mode] = setups_used.get(mode, 0) + 1

    # Recent analysis reasoning patterns
    hold_reasons = []
    entry_reasons = []
    for entry in analysis_log[-20:]:
        decision = entry.get('decision', '')
        reasoning = entry.get('reasoning', '')[:200]
        if decision == 'HOLD':
            hold_reasons.append(reasoning)
        elif decision in ['BUY', 'SELL']:
            entry_reasons.append(f"{decision}: {reasoning}")

    summary = f"""# Daily Trading Summary — {today}

## P&L
- Daily P&L: ${daily_pnl:.2f}
- Total Trades: {total_trades}
- Winners: {len(winners)} | Losers: {len(losers)}
- Win Rate: {win_rate:.1f}%
- Avg Winner: ${avg_winner:.2f} | Avg Loser: ${avg_loser:.2f}

## Trade Log
"""
    for i, trade in enumerate(trades, 1):
        summary += f"""
### Trade {i}
- Direction: {trade.get('action', 'N/A')}
- Entry: {trade.get('entry', 'N/A')} | Exit: {trade.get('exit', 'N/A')}
- P&L: ${trade.get('pnl', 0):.2f}
- Mode: {trade.get('mode', 'N/A')}
- Entry Reason: {trade.get('reasoning', 'N/A')[:300]}
- Exit Reason: {trade.get('exit_reason', 'N/A')[:200]}
"""

    summary += f"""
## Setups Used
"""
    for mode, count in setups_used.items():
        summary += f"- {mode}: {count} trades\n"

    summary += f"""
## Best Trade
{f"${best_trade['pnl']:.2f} — {best_trade.get('reasoning', '')[:200]}" if best_trade else "No trades"}

## Worst Trade  
{f"${worst_trade['pnl']:.2f} — {worst_trade.get('reasoning', '')[:200]}" if worst_trade else "No trades"}

## Why I Stayed Out (sample HOLD reasons)
"""
    for reason in hold_reasons[:5]:
        summary += f"- {reason}\n"

    summary += f"""
## What I Was Seeing At Entries
"""
    for reason in entry_reasons[:5]:
        summary += f"- {reason}\n"

    summary += f"""
## Self Assessment
- Did I follow ICT framework? {"Yes — entries based on FVG/OB confluence" if total_trades > 0 else "No trades taken"}
- Did I respect killzones? To be reviewed
- Did I read AMD cycle correctly? To be reviewed
- What setups worked today? {', '.join([m for m, c in setups_used.items() if c > 0])}
- Key lesson: {"Profitable day — review winning setups" if daily_pnl > 0 else "Losing day — review what went wrong" if daily_pnl < 0 else "No trades — market conditions unclear"}
"""

    with open(filepath, 'w') as f:
        f.write(summary)

    logger.info(f"Daily summary saved: {filepath}")
    return filepath


def load_recent_memory(days: int = 5) -> str:
    """Load last N days of summaries for pre-session context"""
    ensure_memory_dir()
    memory_text = ""

    for i in range(days, 0, -1):
        date = (datetime.now(eastern) - timedelta(days=i)).strftime('%Y-%m-%d')
        filepath = os.path.join(MEMORY_DIR, f"summary_{date}.md")

        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                content = f.read()
            memory_text += f"\n{'='*40}\n{content}\n"

    if not memory_text:
        return "No previous session data available yet — this is the first session."

    return f"""
═══════════════════════════════════════
MEMORY: LAST {days} TRADING SESSIONS
═══════════════════════════════════════
{memory_text}
═══════════════════════════════════════
Use this context to:
- Recognize if today's price action is similar to recent sessions
- Apply lessons from recent wins/losses
- Identify setups that have been working vs failing recently
- Adjust confidence based on recent performance patterns
═══════════════════════════════════════
"""


def save_trade_to_memory(trade: dict):
    """Append individual trade to today's trade log"""
    ensure_memory_dir()
    today = datetime.now(eastern).strftime('%Y-%m-%d')
    filepath = os.path.join(MEMORY_DIR, f"trades_{today}.json")

    trades = []
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                trades = json.load(f)
        except:
            trades = []

    trades.append({
        "timestamp": datetime.now().isoformat(),
        **trade
    })

    with open(filepath, 'w') as f:
        json.dump(trades, f, indent=2)


def load_todays_trades() -> list:
    """Load today's trades from memory"""
    ensure_memory_dir()
    today = datetime.now(eastern).strftime('%Y-%m-%d')
    filepath = os.path.join(MEMORY_DIR, f"trades_{today}.json")

    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except:
            return []
    return []


print("Memory manager loaded successfully")
