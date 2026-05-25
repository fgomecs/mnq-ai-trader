import os
import json
import logging
from datetime import datetime

# Create logs folder
_log_dir = os.path.join(os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"), "logs")
os.makedirs(_log_dir, exist_ok=True)
log_filename = os.path.join(_log_dir, f"trading_{datetime.now().strftime('%Y%m%d')}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()  # also prints to console
    ]
)

logger = logging.getLogger("TradingBot")

def log_analysis(snapshot: dict, claude_response: str):
    """Log market snapshot and Claude's analysis"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": "analysis",
        "snapshot": snapshot,
        "claude_response": claude_response
    }
    with open(os.path.join(_log_dir, f"analysis_{datetime.now().strftime('%Y%m%d')}.json"), "a") as f:
        f.write(json.dumps(entry) + "\n")
    logger.info(f"ANALYSIS: {claude_response[:200]}...")

def log_trade(action: str, contracts: int, price: float, reason: str):
    """Log trade executions"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": "trade",
        "action": action,
        "contracts": contracts,
        "price": price,
        "reason": reason
    }
    with open(os.path.join(_log_dir, f"trades_{datetime.now().strftime('%Y%m%d')}.json"), "a") as f:
        f.write(json.dumps(entry) + "\n")
    logger.info(f"TRADE: {action} {contracts} MNQ @ {price} | {reason}")

def log_error(error: str):
    """Log errors"""
    logger.error(f"ERROR: {error}")

def log_daily_summary(trades: list, pnl: float):
    """Log end of day summary"""
    logger.info("=" * 50)
    logger.info(f"DAILY SUMMARY")
    logger.info(f"Total Trades: {len(trades)}")
    logger.info(f"Daily P&L: ${pnl:.2f}")
    logger.info("=" * 50)

logger.info("Logger loaded successfully")