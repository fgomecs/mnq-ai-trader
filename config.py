import os
from dotenv import load_dotenv

load_dotenv()

# IBKR Connection
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", 7497))
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", 1))

# Account
ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", 50000))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", 0.01))
MAX_DAILY_LOSS_USD = ACCOUNT_SIZE * MAX_DAILY_LOSS_PCT

# MNQ Contract
SYMBOL = "MNQ"
EXCHANGE = "CME"
CURRENCY = "USD"

# Session Times (Eastern)
SESSION_START = "09:45"
SESSION_END = "15:30"
AVOID_START = "11:30"
AVOID_END = "13:30"
OPENING_AVOID_END = "09:45"

# Trading Parameters
SCALP_STOP_TICKS = 4
SCALP_TARGET_TICKS = 8
SWING_TRAIL_TICKS = 6
MAX_CONTRACTS = 1
TICK_SIZE = 0.25
TICK_VALUE = 0.50

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-5"

# Analysis interval (seconds)
ANALYSIS_INTERVAL = 30

print("Config loaded successfully")
print(f"Max daily loss: ${MAX_DAILY_LOSS_USD}")

# Live data switch — set True when CME Real-Time L2 subscription is active
LIVE_DATA_ACTIVE = False