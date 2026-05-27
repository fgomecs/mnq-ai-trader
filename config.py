"""
Configuration for MNQ AI Trader.
All values environment-driven; defaults baked in for safety.
Edit C:\\trading\\mnq-ai-trader\\.env to change without code edits.

V4.1 additions:
  - VERSION constant (auto-managed by version_manager.py)
  - FEATURE_* flags for ablation testing

V4.4 additions:
  - Phase 1-3 strategy expansion feature flags and thresholds
  - Session classifier constants
  - Gap classification thresholds
  - Pivot point support
  - VWAP reversion, OR extreme fade, dead zone VWAP magnet
  - Opening drive fade, post-news refresh, sweep reversal
"""

import os
from dotenv import load_dotenv

# Load .env from this file's directory rather than cwd so the bot reads
# the right env vars no matter where it's invoked from.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


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
VERSION = os.getenv("BOT_VERSION", "4.5.1")

# ─── PATHS ─────────────────────────────────────────────────
BASE_DIR       = os.getenv("BASE_DIR", "C:\\trading\\mnq-ai-trader")
LOG_DIR        = f"{BASE_DIR}\\logs"
MEMORY_DIR     = f"{BASE_DIR}\\memory"
DATA_DIR       = f"{BASE_DIR}\\data"
DASHBOARD_FILE = f"{BASE_DIR}\\dashboard_data.json"
PRICE_FILE     = f"{BASE_DIR}\\price_data.json"

# ─── RECORDING ─────────────────────────────────────────────
RECORDING_ENABLED = _env_bool("RECORDING_ENABLED", True)

# ─── NIGHT OWL — 24/7 unrestricted scanning ────────────────
# When True: main._wait_for_market_hours is a no-op (no overnight sleep),
# get_session_state forces PRIME_WINDOW outside the 08:30-09:30 ET window
# (so the pre-market routine still fires at 08:30), and entry-allowed
# states are no longer gated by clock or DEAD_ZONE. EOD schedule unchanged.
# Risk caps and loss limits are still enforced.
NIGHT_OWL = _env_bool("NIGHT_OWL", False)

# ─── ACCOUNT ───────────────────────────────────────────────
ACCOUNT_SIZE       = _env_int("ACCOUNT_SIZE", 50_000)
MAX_DAILY_LOSS_PCT = _env_float("MAX_DAILY_LOSS_PCT", 0.20)
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

# ─── SESSION STATE TIMES (HHMM integers) ───────────────────
SESSION_PRE_MARKET_TIME    = _env_int("SESSION_PRE_MARKET_TIME",    830)   # 8:30 ET
SESSION_MARKET_OPEN_TIME   = _env_int("SESSION_MARKET_OPEN_TIME",   930)   # 9:30 ET RTH open
SESSION_OR_FORMING_END     = _env_int("SESSION_OR_FORMING_END",     945)   # 9:45 ET — end of 15-min OR window
SESSION_OR_ESTABLISHED_END = _env_int("SESSION_OR_ESTABLISHED_END", 1000)  # 10:00 ET
SESSION_PRIME_WINDOW_END   = _env_int("SESSION_PRIME_WINDOW_END",   1100)  # 11:00 ET
SESSION_DEAD_ZONE_END      = _env_int("SESSION_DEAD_ZONE_END",      1330)  # 1:30 PM ET
SESSION_CLOSING_END        = _env_int("SESSION_CLOSING_END",        1600)  # 4:00 PM ET
SESSION_AFTERNOON_PRIME_END= _env_int("SESSION_AFTERNOON_PRIME_END",1530)  # 3:30 PM ET
EOD_SCHEDULE_TIME          = os.getenv("EOD_SCHEDULE_TIME", "15:55")
MAIN_LOOP_SLEEP_SECS       = _env_float("MAIN_LOOP_SLEEP_SECS", 0.5)

# ─── ENTRY GATES ───────────────────────────────────────────
DEAD_ZONE_CONFLUENCE_THRESHOLD = _env_int("DEAD_ZONE_CONFLUENCE_THRESHOLD", 8)
POS_STRUCTURE_MIN_PROFIT_TICKS = _env_int("POS_STRUCTURE_MIN_PROFIT_TICKS", 20)
POS_STRUCTURE_PULLBACK_TICKS   = _env_int("POS_STRUCTURE_PULLBACK_TICKS",    5)
ENTRY_MODE                     = os.getenv("ENTRY_MODE", "LIMIT")
LIMIT_ORDER_MAX_SLIPPAGE       = _env_int("LIMIT_ORDER_MAX_SLIPPAGE",        4)
LIMIT_ORDER_TIMEOUT_SECS       = _env_int("LIMIT_ORDER_TIMEOUT_SECS",        5)

# ─── DASHBOARD REFRESH ─────────────────────────────────────
DASHBOARD_ACCOUNT_REFRESH_SECS = _env_int("DASHBOARD_ACCOUNT_REFRESH_SECS", 5)
DASHBOARD_LIVE_PATCH_SECS      = _env_int("DASHBOARD_LIVE_PATCH_SECS",      10)
PRE_FILTER_LOG_INTERVAL_SECS   = _env_int("PRE_FILTER_LOG_INTERVAL_SECS",   30)

# ─── PRE-FILTER SIGNAL SCORING ─────────────────────────────
PRE_FILTER_SIGNAL_THRESHOLD    = _env_int("PRE_FILTER_SIGNAL_THRESHOLD",    3)
COUNTER_TREND_SIGNAL_THRESHOLD = _env_int("COUNTER_TREND_SIGNAL_THRESHOLD", 5)

# ─── SKIP-CACHE (A.1) ──────────────────────────────────────
SKIP_CACHE_PRICE_DELTA        = _env_float("SKIP_CACHE_PRICE_DELTA",        15.0)
SKIP_CACHE_MAX_AGE_SECS       = _env_int("SKIP_CACHE_MAX_AGE_SECS",         300)
SKIP_CACHE_WATCHLIST_AGE_SECS = _env_int("SKIP_CACHE_WATCHLIST_AGE_SECS",    60)
SKIP_LOG_EVERY_N              = _env_int("SKIP_LOG_EVERY_N",                   5)

# ─── OR THESIS ─────────────────────────────────────────────
OR_THESIS_INVALIDATION_POINTS = _env_int("OR_THESIS_INVALIDATION_POINTS",   80)
OR_PULLBACK_THRESHOLD_PCT     = _env_float("OR_PULLBACK_THRESHOLD_PCT",      0.3)

# ─── DOM SIGNALS ───────────────────────────────────────────
DOM_HISTORY_MAX_SNAPSHOTS        = _env_int("DOM_HISTORY_MAX_SNAPSHOTS",        12)
DOM_SIGNIFICANT_SIZE             = _env_int("DOM_SIGNIFICANT_SIZE",             30)
DOM_LARGE_SIZE                   = _env_int("DOM_LARGE_SIZE",                   75)
DOM_WHALE_SIZE                   = _env_int("DOM_WHALE_SIZE",                  200)
LARGE_PRINT_THRESHOLD            = _env_int("LARGE_PRINT_THRESHOLD",            50)
DOM_BUY_PRESSURE_BULL_THRESHOLD  = _env_float("DOM_BUY_PRESSURE_BULL_THRESHOLD", 0.65)
DOM_SELL_PRESSURE_BEAR_THRESHOLD = _env_float("DOM_SELL_PRESSURE_BEAR_THRESHOLD", 0.35)
DOM_CLUSTER_TOLERANCE_POINTS     = _env_float("DOM_CLUSTER_TOLERANCE_POINTS",    1.25)
DOM_VACUUM_THRESHOLD_SIZE        = _env_int("DOM_VACUUM_THRESHOLD_SIZE",          5)

# ── DOM rate limiting (v4.5.x Gateway EWriter overflow mitigation) ──
# Minimum interval between DOM signal/text recomputations. Caches the
# processed DOM features for this window so the per-snapshot work stays
# bounded under high-frequency DOM updates. 0.0 disables the cache.
DOM_THROTTLE_SECS                = _env_float("DOM_THROTTLE_SECS",               0.1)
# If sustained DOM updateEvent rate exceeds this many Hz we log a one-shot
# warning that the subscription firehose may be unsustainable.
DOM_UPDATE_RATE_WARN_HZ          = _env_int("DOM_UPDATE_RATE_WARN_HZ",           200)
DOM_ICEBERG_SHRINK_PCT           = _env_float("DOM_ICEBERG_SHRINK_PCT",           0.6)
DOM_ICEBERG_RECOVERY_PCT         = _env_float("DOM_ICEBERG_RECOVERY_PCT",         0.7)
DOM_SWEEP_LEVEL_THRESHOLD        = _env_int("DOM_SWEEP_LEVEL_THRESHOLD",          3)

# ─── OFI ───────────────────────────────────────────────────
OFI_STRONG_THRESHOLD_CONTRACTS = _env_int("OFI_STRONG_THRESHOLD_CONTRACTS", 500)
OFI_ACCELERATION_THRESHOLD     = _env_float("OFI_ACCELERATION_THRESHOLD",   1.3)
OFI_DECELERATION_THRESHOLD     = _env_float("OFI_DECELERATION_THRESHOLD",   0.7)
OFI_STRONG_BUY_THRESHOLD       = _env_int("OFI_STRONG_BUY_THRESHOLD",        60)
OFI_BUY_THRESHOLD              = _env_int("OFI_BUY_THRESHOLD",               25)
OFI_STRONG_SELL_THRESHOLD      = _env_int("OFI_STRONG_SELL_THRESHOLD",       -60)
OFI_SELL_THRESHOLD             = _env_int("OFI_SELL_THRESHOLD",              -25)
DELTA_DIVERGENCE_THRESHOLD     = _env_int("DELTA_DIVERGENCE_THRESHOLD",      500)

# ─── VOLUME PROFILE ─────────────────────────────────────────
VOLUME_PROFILE_TARGET_PCT = _env_float("VOLUME_PROFILE_TARGET_PCT", 0.70)
POC_PROXIMITY_POINTS      = _env_float("POC_PROXIMITY_POINTS",       5.0)

# ─── ICT LEVEL PROXIMITY ───────────────────────────────────
FVG_PROXIMITY_POINTS     = _env_float("FVG_PROXIMITY_POINTS",    100.0)
OB_PROXIMITY_POINTS      = _env_float("OB_PROXIMITY_POINTS",     150.0)
LIQUIDITY_POOL_TOLERANCE = _env_float("LIQUIDITY_POOL_TOLERANCE",  2.0)

# ─── BAR CACHE & STREAMS ───────────────────────────────────
TICK_STATE_PERSIST_INTERVAL_SECS = _env_int("TICK_STATE_PERSIST_INTERVAL_SECS", 30)
INIT_BARS_1MIN_DURATION          = os.getenv("INIT_BARS_1MIN_DURATION",  "28800 S")
INIT_BARS_5MIN_DURATION          = os.getenv("INIT_BARS_5MIN_DURATION",  "86400 S")
INIT_BARS_15MIN_DURATION         = os.getenv("INIT_BARS_15MIN_DURATION", "2 D")
INIT_BARS_DAILY_DURATION         = os.getenv("INIT_BARS_DAILY_DURATION", "30 D")
REALTIME_BARS_PER_MINUTE         = _env_int("REALTIME_BARS_PER_MINUTE",    12)
BARS_1MIN_CACHE_SIZE             = _env_int("BARS_1MIN_CACHE_SIZE",        600)
SNAPSHOT_ASSEMBLY_SLEEP_SECS     = _env_float("SNAPSHOT_ASSEMBLY_SLEEP_SECS", 0.3)
NEWS_CACHE_TTL_SECS              = _env_int("NEWS_CACHE_TTL_SECS",         600)

# ─── EXECUTOR ──────────────────────────────────────────────
PROTECTION_RECONCILE_EVERY_N_LOOPS      = _env_int("PROTECTION_RECONCILE_EVERY_N_LOOPS",       4)
DELAYED_DATA_STALENESS_THRESHOLD_POINTS = _env_int("DELAYED_DATA_STALENESS_THRESHOLD_POINTS",  20)
MAX_REASONABLE_PNL_PER_CONTRACT         = _env_float("MAX_REASONABLE_PNL_PER_CONTRACT",     1000.0)
RBUST_MAX_R_PER_TRADE                   = _env_float("RBUST_MAX_R_PER_TRADE",                 1.5)
TRAIL_PROFIT_1_TICKS                    = _env_int("TRAIL_PROFIT_1_TICKS",                    120)
TRAIL_PROFIT_1_LOCK                     = _env_int("TRAIL_PROFIT_1_LOCK",                      30)
TRAIL_PROFIT_2_TICKS                    = _env_int("TRAIL_PROFIT_2_TICKS",                    180)
TRAIL_PROFIT_2_LOCK                     = _env_int("TRAIL_PROFIT_2_LOCK",                      60)

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
# ═══════════════════════════════════════════════════════════

# ── Strategy / Bias ────────────────────────────────────────
FEATURE_ORB_BIAS          = _env_bool("FEATURE_ORB_BIAS",          True)
FEATURE_BIDIRECTIONAL     = _env_bool("FEATURE_BIDIRECTIONAL",     True)
FEATURE_BIAS_DECAY        = _env_bool("FEATURE_BIAS_DECAY",        True)
FEATURE_DOJI_MTF_OVERRIDE = _env_bool("FEATURE_DOJI_MTF_OVERRIDE", True)

# ── Predictive Signals ─────────────────────────────────────
FEATURE_OFI          = _env_bool("FEATURE_OFI",          True)
FEATURE_DOM_ADVANCED = _env_bool("FEATURE_DOM_ADVANCED", True)
FEATURE_MTF_SCORE    = _env_bool("FEATURE_MTF_SCORE",    True)
FEATURE_DELTA_LIVE   = _env_bool("FEATURE_DELTA_LIVE",   True)

# ── Entry Gates ────────────────────────────────────────────
FEATURE_THESIS_GATE = _env_bool("FEATURE_THESIS_GATE", True)
FEATURE_R_BUDGET    = _env_bool("FEATURE_R_BUDGET",    False)
FEATURE_NEWS_GATE   = _env_bool("FEATURE_NEWS_GATE",   True)
FEATURE_DEAD_ZONE   = _env_bool("FEATURE_DEAD_ZONE",   True)

# ── Position Management ────────────────────────────────────
FEATURE_DUAL_TRAIL  = _env_bool("FEATURE_DUAL_TRAIL",  True)
FEATURE_EARLY_EXIT  = _env_bool("FEATURE_EARLY_EXIT",  True)

# ── Learning ───────────────────────────────────────────────
FEATURE_LEARNING_EOD    = _env_bool("FEATURE_LEARNING_EOD",    True)
FEATURE_LEARNING_INJECT = _env_bool("FEATURE_LEARNING_INJECT", True)

# ═══════════════════════════════════════════════════════════
# V4.4 FEATURE FLAGS — Phase 1-3 Strategy Expansion
# ═══════════════════════════════════════════════════════════
# Phase 1 features default True  — active immediately.
# Phase 2-3 features default False — activate after session
# data confirms detection accuracy via backtester.
# ═══════════════════════════════════════════════════════════

# ── Phase 1 — Foundation (default True) ───────────────────
# Classify session as TREND/RANGE/NEWS/HOLIDAY at OR_ESTABLISHED
FEATURE_SESSION_CLASSIFIER  = _env_bool("FEATURE_SESSION_CLASSIFIER",  True)
# Track first 1-min and 5-min candle H/L as named session levels
FEATURE_FIRST_CANDLE_LEVELS = _env_bool("FEATURE_FIRST_CANDLE_LEVELS", True)
# Compute gap size/direction/fill-probability at session start
FEATURE_GAP_CLASSIFICATION  = _env_bool("FEATURE_GAP_CLASSIFICATION",  True)
# Compute classic daily pivot points R1/R2/S1/S2 from prior day
FEATURE_PIVOT_POINTS        = _env_bool("FEATURE_PIVOT_POINTS",        True)

# ── Phase 2 — Range Day Strategies (default False) ────────
# Detect price at 2x OR range extension — fade opportunity
FEATURE_OR_EXTREME_FADE       = _env_bool("FEATURE_OR_EXTREME_FADE",       False)
# Lower dead zone threshold when price is far from VWAP
FEATURE_DEAD_ZONE_VWAP_MAGNET = _env_bool("FEATURE_DEAD_ZONE_VWAP_MAGNET", False)
# VWAP reversion pre-filter signals (requires RANGE day context)
FEATURE_VWAP_REVERSION        = _env_bool("FEATURE_VWAP_REVERSION",        False)

# ── Phase 3 — Reversal Strategies (default False) ─────────
# Promote DOM sweep to named strategy with extra pre-filter weight
FEATURE_SWEEP_REVERSAL    = _env_bool("FEATURE_SWEEP_REVERSAL",    False)
# Detect large first 5-min candle with rejection wick — fade setup
FEATURE_OPENING_DRIVE_FADE = _env_bool("FEATURE_OPENING_DRIVE_FADE", False)
# Refresh watchlist 45 min after high-impact news clears
FEATURE_POST_NEWS_REFRESH  = _env_bool("FEATURE_POST_NEWS_REFRESH",  False)

# ─── COMMISSIONS ───────────────────────────────────────────
SIMULATE_COMMISSIONS    = _env_bool("SIMULATE_COMMISSIONS",    False)
COMMISSION_PER_SIDE_USD = _env_float("COMMISSION_PER_SIDE_USD", 0.85)

# ── Active feature set label ────────────────────────────────
ACTIVE_FEATURE_SET = os.getenv("ACTIVE_FEATURE_SET", "LIVE")

# ═══════════════════════════════════════════════════════════
# V4.4 THRESHOLDS — Session Classifier
# ═══════════════════════════════════════════════════════════
SESSION_CLASSIFIER_TREND_OR_MIN = _env_int("SESSION_CLASSIFIER_TREND_OR_MIN", 50)   # OR range pts minimum for TREND
SESSION_CLASSIFIER_RANGE_OR_MAX = _env_int("SESSION_CLASSIFIER_RANGE_OR_MAX", 35)   # OR range pts maximum for RANGE
SESSION_CLASSIFIER_NEWS_GAP_MIN = _env_int("SESSION_CLASSIFIER_NEWS_GAP_MIN", 100)  # Gap pts that flags NEWS day
SESSION_RANGE_SIGNAL_THRESHOLD  = _env_int("SESSION_RANGE_SIGNAL_THRESHOLD",  7)    # Signals required on RANGE days
SESSION_NEWS_THESIS_GATE        = _env_int("SESSION_NEWS_THESIS_GATE",        80)   # Min thesis prob on NEWS days
SESSION_NEWS_STOP_MULTIPLIER    = _env_float("SESSION_NEWS_STOP_MULTIPLIER",  1.5)  # Stop multiplier on NEWS days

# ═══════════════════════════════════════════════════════════
# V4.4 THRESHOLDS — Gap Classification
# ═══════════════════════════════════════════════════════════
# Academic fill probabilities (MNQ points):
#   < 63pts  → 79% fill probability
#   63-147pts → 52% fill probability
#   147-210pts → 28% fill probability
#   > 210pts  → 12% fill probability (news gap)
GAP_SMALL_THRESHOLD  = _env_int("GAP_SMALL_THRESHOLD",  63)
GAP_MEDIUM_THRESHOLD = _env_int("GAP_MEDIUM_THRESHOLD", 147)
GAP_LARGE_THRESHOLD  = _env_int("GAP_LARGE_THRESHOLD",  210)

# ═══════════════════════════════════════════════════════════
# V4.4 THRESHOLDS — Phase 2 Range Day
# ═══════════════════════════════════════════════════════════
VWAP_REVERSION_MIN_EXTENSION    = _env_int("VWAP_REVERSION_MIN_EXTENSION",    80)   # Points from VWAP to trigger signal
OR_EXTREME_FADE_MULTIPLIER      = _env_float("OR_EXTREME_FADE_MULTIPLIER",     2.0)  # x OR range = extreme zone
DEAD_ZONE_VWAP_MAGNET_MIN_EXT   = _env_int("DEAD_ZONE_VWAP_MAGNET_MIN_EXT",   60)   # Points from VWAP to lower dead zone threshold
DEAD_ZONE_VWAP_MAGNET_THRESHOLD = _env_int("DEAD_ZONE_VWAP_MAGNET_THRESHOLD",  6)   # Reduced threshold when VWAP is far

# ═══════════════════════════════════════════════════════════
# V4.4 THRESHOLDS — Phase 3 Reversal
# ═══════════════════════════════════════════════════════════
OPENING_DRIVE_MIN_POINTS    = _env_int("OPENING_DRIVE_MIN_POINTS",        80)   # First 5-min candle range to classify as drive
OPENING_DRIVE_REJECTION_PCT = _env_float("OPENING_DRIVE_REJECTION_PCT",   0.60) # Wick as % of body to flag rejection
POST_NEWS_WINDOW_MINUTES    = _env_int("POST_NEWS_WINDOW_MINUTES",         45)   # Minutes after news before window opens
POST_NEWS_WINDOW_DURATION   = _env_int("POST_NEWS_WINDOW_DURATION",        30)   # How long post-news window stays open (minutes)


def get_active_features() -> dict:
    """Return dict of all feature flags and their current state."""
    return {
        # V4.1 original flags
        "ORB_BIAS":         FEATURE_ORB_BIAS,
        "BIDIRECTIONAL":    FEATURE_BIDIRECTIONAL,
        "BIAS_DECAY":       FEATURE_BIAS_DECAY,
        "OFI":              FEATURE_OFI,
        "DOM_ADVANCED":     FEATURE_DOM_ADVANCED,
        "MTF_SCORE":        FEATURE_MTF_SCORE,
        "DELTA_LIVE":       FEATURE_DELTA_LIVE,
        "THESIS_GATE":      FEATURE_THESIS_GATE,
        "R_BUDGET":         FEATURE_R_BUDGET,
        "NEWS_GATE":        FEATURE_NEWS_GATE,
        "DEAD_ZONE":        FEATURE_DEAD_ZONE,
        "DUAL_TRAIL":       FEATURE_DUAL_TRAIL,
        "EARLY_EXIT":       FEATURE_EARLY_EXIT,
        "LEARNING_EOD":     FEATURE_LEARNING_EOD,
        "LEARNING_INJECT":  FEATURE_LEARNING_INJECT,
        # V4.4 Phase 1
        "SESSION_CLASSIFIER":  FEATURE_SESSION_CLASSIFIER,
        "FIRST_CANDLE":        FEATURE_FIRST_CANDLE_LEVELS,
        "GAP_CLASS":           FEATURE_GAP_CLASSIFICATION,
        "PIVOT_POINTS":        FEATURE_PIVOT_POINTS,
        # V4.4 Phase 2
        "OR_EXTREME_FADE":     FEATURE_OR_EXTREME_FADE,
        "DZ_VWAP_MAGNET":      FEATURE_DEAD_ZONE_VWAP_MAGNET,
        "VWAP_REVERSION":      FEATURE_VWAP_REVERSION,
        # V4.4 Phase 3
        "SWEEP_REVERSAL":      FEATURE_SWEEP_REVERSAL,
        "OPENING_DRIVE_FADE":  FEATURE_OPENING_DRIVE_FADE,
        "POST_NEWS_REFRESH":   FEATURE_POST_NEWS_REFRESH,
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
