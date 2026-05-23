"""
Configuration for MNQ AI Trader.
All values environment-driven; defaults baked in for safety.
Edit C:\\trading\\mnq-ai-trader\\.env to change without code edits.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ─── PATHS ─────────────────────────────────────────────────
BASE_DIR       = os.getenv("BASE_DIR", "C:\\trading\\mnq-ai-trader")
LOG_DIR        = f"{BASE_DIR}\\logs"
MEMORY_DIR     = f"{BASE_DIR}\\memory"
DATA_DIR       = f"{BASE_DIR}\\data"          # live session recordings for backtest
DASHBOARD_FILE = f"{BASE_DIR}\\dashboard_data.json"
PRICE_FILE     = f"{BASE_DIR}\\price_data.json"

# ─── RECORDING ─────────────────────────────────────────────
# Set RECORDING_ENABLED=false in .env to disable during backtest runs
RECORDING_ENABLED = _env_bool("RECORDING_ENABLED", True)

# ─── ACCOUNT ───────────────────────────────────────────────
ACCOUNT_SIZE       = _env_int("ACCOUNT_SIZE", 50_000)
MAX_DAILY_LOSS_PCT = _env_float("MAX_DAILY_LOSS_PCT", 0.01)   # 1% default
MAX_DAILY_LOSS_USD = ACCOUNT_SIZE * MAX_DAILY_LOSS_PCT

# Session risk budget (R-units). Stops trading after this many R lost in a day.
# 1R = the dollar risk of the trade's stop distance. (See A.1 in the audit.)
MAX_SESSION_R_LOSS = _env_float("MAX_SESSION_R_LOSS", 3.0)

# ─── CONTRACT ──────────────────────────────────────────────
SYMBOL          = "MNQ"
EXCHANGE        = "CME"
CURRENCY        = "USD"
CONTRACT_EXPIRY = os.getenv("CONTRACT_EXPIRY", "20260618")
CONTRACT_CONID  = _env_int("CONTRACT_CONID", 770561201)

# ─── TICK / POINT VALUES ───────────────────────────────────
TICK_SIZE   = 0.25
TICK_VALUE  = 0.50
POINT_SIZE  = 1.0
POINT_VALUE = 2.00

# ─── RISK PARAMETERS ───────────────────────────────────────
SCALP_STOP_TICKS   = _env_int("SCALP_STOP_TICKS",   100)
SCALP_TARGET_TICKS = _env_int("SCALP_TARGET_TICKS", 200)
SWING_STOP_TICKS   = _env_int("SWING_STOP_TICKS",   120)
SWING_TARGET_TICKS = _env_int("SWING_TARGET_TICKS", 300)
SWING_TRAIL_TICKS  = _env_int("SWING_TRAIL_TICKS",   60)
MAX_CONTRACTS      = _env_int("MAX_CONTRACTS",        1)

# ─── POSITION MGMT TRIGGERS (P3.1 — lifted from main.py magic numbers) ─
# Tick-distance thresholds that cause an event-driven Claude position call.
POS_ADVERSE_MOVE_TICKS  = _env_int("POS_ADVERSE_MOVE_TICKS",  10)
POS_STOP_PROXIMITY_TICKS = _env_int("POS_STOP_PROXIMITY_TICKS", 30)
POS_TARGET_PROXIMITY_TICKS = _env_int("POS_TARGET_PROXIMITY_TICKS", 20)
POS_GIVEBACK_PEAK_TICKS = _env_int("POS_GIVEBACK_PEAK_TICKS", 40)
POS_GIVEBACK_AMOUNT_TICKS = _env_int("POS_GIVEBACK_AMOUNT_TICKS", 30)

# Minimum hold seconds before Claude can decide CLOSE (unless emergency).
MIN_HOLD_SCALP   = _env_int("MIN_HOLD_SCALP",   180)
MIN_HOLD_SWING   = _env_int("MIN_HOLD_SWING",   300)
MIN_HOLD_DEFAULT = _env_int("MIN_HOLD_DEFAULT", 180)

# Emergency-exit override: if within this many ticks of stop, allow CLOSE.
EMERGENCY_STOP_DIST_TICKS = _env_int("EMERGENCY_STOP_DIST_TICKS", 15)

# ─── LOOP CADENCE ──────────────────────────────────────────
ENTRY_SCAN_INTERVAL_SECS  = _env_int("ENTRY_SCAN_INTERVAL_SECS",   5)
POS_INTERVAL_NORMAL_SECS  = _env_int("POS_INTERVAL_NORMAL_SECS",  60)
POS_INTERVAL_ALERT_SECS   = _env_int("POS_INTERVAL_ALERT_SECS",   15)
WATCHLIST_REFRESH_SECS    = _env_int("WATCHLIST_REFRESH_SECS",   300)
PROTECTION_LOOP_SECS      = _env_int("PROTECTION_LOOP_SECS",       5)

# ─── IBKR CONNECTION ───────────────────────────────────────
IBKR_HOST      = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT      = _env_int("IBKR_PORT", 7497)
IBKR_CLIENT_ID = _env_int("IBKR_CLIENT_ID", 1)

# ─── CLAUDE API ────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Model split: high-quality entry decisions, cheaper position management.
# See docs.claude.com for current model IDs.
CLAUDE_ENTRY_MODEL     = os.getenv("CLAUDE_ENTRY_MODEL",     "claude-opus-4-7")
CLAUDE_POSITION_MODEL  = os.getenv("CLAUDE_POSITION_MODEL",  "claude-sonnet-4-6")
CLAUDE_STRUCTURE_MODEL = os.getenv("CLAUDE_STRUCTURE_MODEL", "claude-sonnet-4-6")

# Legacy alias — old code paths may still reference CLAUDE_MODEL.
# Points at the entry model since that's the most-called path.
CLAUDE_MODEL = CLAUDE_ENTRY_MODEL

CLAUDE_MAX_TOKENS = _env_int("CLAUDE_MAX_TOKENS", 1_000)

# Enable prompt caching on static system prompts + performance context.
# Cuts input-token cost ~90% on cached blocks after first call.
CLAUDE_USE_CACHING = _env_bool("CLAUDE_USE_CACHING", True)

# ─── LIVE DATA ─────────────────────────────────────────────
LIVE_DATA_ACTIVE = _env_bool("LIVE_DATA_ACTIVE", False)

# ─── STARTUP CONFIG ECHO ───────────────────────────────────
if __name__ == "__main__":
    _stop_pts   = SCALP_STOP_TICKS   / 4
    _target_pts = SCALP_TARGET_TICKS / 4
    print("Config loaded")
    print(f"  Account size   : ${ACCOUNT_SIZE:,}")
    print(f"  Max daily loss : ${MAX_DAILY_LOSS_USD:,.0f} ({MAX_DAILY_LOSS_PCT:.1%})")
    print(f"  Session R cap  : {MAX_SESSION_R_LOSS}R")
    print(f"  Stop           : {SCALP_STOP_TICKS}t = {_stop_pts:.0f} pts = ${SCALP_STOP_TICKS * TICK_VALUE:.0f}")
    print(f"  Target         : {SCALP_TARGET_TICKS}t = {_target_pts:.0f} pts = ${SCALP_TARGET_TICKS * TICK_VALUE:.0f}")
    print(f"  Entry model    : {CLAUDE_ENTRY_MODEL}")
    print(f"  Position model : {CLAUDE_POSITION_MODEL}")
    print(f"  Caching        : {'ON' if CLAUDE_USE_CACHING else 'OFF'}")
    print(f"  Live data      : {'ON' if LIVE_DATA_ACTIVE else 'OFF (delayed)'}")
