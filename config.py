"""
Configuration for MNQ AI Trader.
All values environment-driven; defaults baked in for safety.
Edit C:\\trading\\mnq-ai-trader\\.env to change without code edits.

V4.1 additions:
  - VERSION constant (auto-managed by version_manager.py)
  - FEATURE_* flags for ablation testing
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


# ─── VERSION ───────────────────────────────────────────────
# Managed by version_manager.py — do not edit manually.
# Format: MAJOR.MINOR.PATCH
# MAJOR: architectural change | MINOR: new feature | PATCH: bug fix / tuning
VERSION = os.getenv("BOT_VERSION", "4.1.0")

# ─── PATHS ─────────────────────────────────────────────────
BASE_DIR       = os.getenv("BASE_DIR", "C:\\trading\\mnq-ai-trader")
LOG_DIR        = f"{BASE_DIR}\\logs"
MEMORY_DIR     = f"{BASE_DIR}\\memory"
DATA_DIR       = f"{BASE_DIR}\\data"
DASHBOARD_FILE = f"{BASE_DIR}\\dashboard_data.json"
PRICE_FILE     = f"{BASE_DIR}\\price_data.json"

# ─── RECORDING ─────────────────────────────────────────────
RECORDING_ENABLED = _env_bool("RECORDING_ENABLED", True)

# ─── ACCOUNT ───────────────────────────────────────────────
ACCOUNT_SIZE       = _env_int("ACCOUNT_SIZE", 50_000)
MAX_DAILY_LOSS_PCT = _env_float("MAX_DAILY_LOSS_PCT", 0.01)
MAX_DAILY_LOSS_USD = ACCOUNT_SIZE * MAX_DAILY_LOSS_PCT
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

# ─── POSITION MGMT TRIGGERS ────────────────────────────────
POS_ADVERSE_MOVE_TICKS     = _env_int("POS_ADVERSE_MOVE_TICKS",     10)
POS_STOP_PROXIMITY_TICKS   = _env_int("POS_STOP_PROXIMITY_TICKS",   30)
POS_TARGET_PROXIMITY_TICKS = _env_int("POS_TARGET_PROXIMITY_TICKS", 20)
POS_GIVEBACK_PEAK_TICKS    = _env_int("POS_GIVEBACK_PEAK_TICKS",    40)
POS_GIVEBACK_AMOUNT_TICKS  = _env_int("POS_GIVEBACK_AMOUNT_TICKS",  30)
MIN_HOLD_SCALP             = _env_int("MIN_HOLD_SCALP",            180)
MIN_HOLD_SWING             = _env_int("MIN_HOLD_SWING",            300)
MIN_HOLD_DEFAULT           = _env_int("MIN_HOLD_DEFAULT",          180)
EMERGENCY_STOP_DIST_TICKS  = _env_int("EMERGENCY_STOP_DIST_TICKS",  15)

# ─── LOOP CADENCE ──────────────────────────────────────────
ENTRY_SCAN_INTERVAL_SECS = _env_int("ENTRY_SCAN_INTERVAL_SECS",   5)
POS_INTERVAL_NORMAL_SECS = _env_int("POS_INTERVAL_NORMAL_SECS",  60)
POS_INTERVAL_ALERT_SECS  = _env_int("POS_INTERVAL_ALERT_SECS",   15)
WATCHLIST_REFRESH_SECS   = _env_int("WATCHLIST_REFRESH_SECS",   300)
PROTECTION_LOOP_SECS     = _env_int("PROTECTION_LOOP_SECS",       5)

# ─── IBKR CONNECTION ───────────────────────────────────────
IBKR_HOST      = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT      = _env_int("IBKR_PORT", 7497)
IBKR_CLIENT_ID = _env_int("IBKR_CLIENT_ID", 1)

# ─── CLAUDE API ────────────────────────────────────────────
ANTHROPIC_API_KEY      = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_ENTRY_MODEL     = os.getenv("CLAUDE_ENTRY_MODEL",     "claude-opus-4-7")
CLAUDE_POSITION_MODEL  = os.getenv("CLAUDE_POSITION_MODEL",  "claude-sonnet-4-6")
CLAUDE_STRUCTURE_MODEL = os.getenv("CLAUDE_STRUCTURE_MODEL", "claude-sonnet-4-6")
CLAUDE_MODEL           = CLAUDE_ENTRY_MODEL   # legacy alias
CLAUDE_MAX_TOKENS      = _env_int("CLAUDE_MAX_TOKENS", 1_000)
CLAUDE_USE_CACHING     = _env_bool("CLAUDE_USE_CACHING", True)

# ─── LIVE DATA ─────────────────────────────────────────────
LIVE_DATA_ACTIVE = _env_bool("LIVE_DATA_ACTIVE", False)

# ─── V4.0 — THESIS PROBABILITY GATE ────────────────────────
MIN_THESIS_PROBABILITY = _env_int("MIN_THESIS_PROBABILITY", 70)

# ═══════════════════════════════════════════════════════════
# V4.1 FEATURE FLAGS
# ═══════════════════════════════════════════════════════════
# Each flag can be toggled in .env for live trading and
# ablation testing. Safety features (stops, R-budget core,
# race-condition fixes) are NOT flagged — they're always on.
#
# Ablation runner disables these one at a time to measure
# each feature's isolated contribution to daily P&L.
# ═══════════════════════════════════════════════════════════

# ── Strategy / Bias ────────────────────────────────────────
# ORB direction as LONG_PREFERRED / SHORT_PREFERRED starting bias
FEATURE_ORB_BIAS       = _env_bool("FEATURE_ORB_BIAS",       True)
# Allow shorts on bullish OR days and longs on bearish OR days
FEATURE_BIDIRECTIONAL  = _env_bool("FEATURE_BIDIRECTIONAL",  True)
# Bias decays to NEUTRAL after 90min if structure disagrees
FEATURE_BIAS_DECAY     = _env_bool("FEATURE_BIAS_DECAY",     True)

# ── Predictive Signals ─────────────────────────────────────
# V4.0: Order Flow Imbalance score from DOM history
FEATURE_OFI            = _env_bool("FEATURE_OFI",            True)
# V3.1: Iceberg / spoof / sweep / cluster detection (20 levels)
FEATURE_DOM_ADVANCED   = _env_bool("FEATURE_DOM_ADVANCED",   True)
# V3.0: Numeric MTF alignment score (0-100) alongside text label
FEATURE_MTF_SCORE      = _env_bool("FEATURE_MTF_SCORE",      True)
# True bid/ask delta classification (requires live L2)
FEATURE_DELTA_LIVE     = _env_bool("FEATURE_DELTA_LIVE",     True)

# ── Entry Gates ────────────────────────────────────────────
# V4.0: Block entries when thesis probability < MIN_THESIS_PROBABILITY
FEATURE_THESIS_GATE    = _env_bool("FEATURE_THESIS_GATE",    True)
# D.1: Stop new entries after MAX_SESSION_R_LOSS R units lost
FEATURE_R_BUDGET       = _env_bool("FEATURE_R_BUDGET",       True)
# Block entries within danger window around high-impact news
FEATURE_NEWS_GATE      = _env_bool("FEATURE_NEWS_GATE",      True)
# Reduce entry threshold during dead zone (11am-1:30pm ET)
FEATURE_DEAD_ZONE      = _env_bool("FEATURE_DEAD_ZONE",      True)

# ── Position Management ────────────────────────────────────
# D.2: Claude TRAIL decisions anchor auto-trail (Claude always wins)
FEATURE_DUAL_TRAIL     = _env_bool("FEATURE_DUAL_TRAIL",     True)
# Allow Claude to CLOSE positions early before stop/target
FEATURE_EARLY_EXIT     = _env_bool("FEATURE_EARLY_EXIT",     True)

# ── Learning ───────────────────────────────────────────────
# V4.1: Run ablation backtest + learning session at EOD
FEATURE_LEARNING_EOD   = _env_bool("FEATURE_LEARNING_EOD",   True)
# Inject yesterday's learning findings into pre-market prompt
FEATURE_LEARNING_INJECT = _env_bool("FEATURE_LEARNING_INJECT", True)

# ── Active feature set label (set by ablation runner during tests) ──
# Normal trading: "LIVE" — ablation sets this to the test name
ACTIVE_FEATURE_SET = os.getenv("ACTIVE_FEATURE_SET", "LIVE")


def get_active_features() -> dict:
    """Return dict of all feature flags and their current state."""
    return {
        "ORB_BIAS":        FEATURE_ORB_BIAS,
        "BIDIRECTIONAL":   FEATURE_BIDIRECTIONAL,
        "BIAS_DECAY":      FEATURE_BIAS_DECAY,
        "OFI":             FEATURE_OFI,
        "DOM_ADVANCED":    FEATURE_DOM_ADVANCED,
        "MTF_SCORE":       FEATURE_MTF_SCORE,
        "DELTA_LIVE":      FEATURE_DELTA_LIVE,
        "THESIS_GATE":     FEATURE_THESIS_GATE,
        "R_BUDGET":        FEATURE_R_BUDGET,
        "NEWS_GATE":       FEATURE_NEWS_GATE,
        "DEAD_ZONE":       FEATURE_DEAD_ZONE,
        "DUAL_TRAIL":      FEATURE_DUAL_TRAIL,
        "EARLY_EXIT":      FEATURE_EARLY_EXIT,
        "LEARNING_EOD":    FEATURE_LEARNING_EOD,
        "LEARNING_INJECT": FEATURE_LEARNING_INJECT,
    }


def features_summary() -> str:
    """One-line summary of active features for logging."""
    feats = get_active_features()
    on  = [k for k, v in feats.items() if v]
    off = [k for k, v in feats.items() if not v]
    if not off:
        return "ALL FEATURES ON"
    return f"ON:{','.join(on)} | OFF:{','.join(off)}"


# ─── STARTUP CONFIG ECHO ───────────────────────────────────
if __name__ == "__main__":
    print(f"MNQ AI Trader v{VERSION}")
    print(f"  Account size   : ${ACCOUNT_SIZE:,}")
    print(f"  Max daily loss : ${MAX_DAILY_LOSS_USD:,.0f}")
    print(f"  Session R cap  : {MAX_SESSION_R_LOSS}R")
    print(f"  Entry model    : {CLAUDE_ENTRY_MODEL}")
    print(f"  Features       : {features_summary()}")
