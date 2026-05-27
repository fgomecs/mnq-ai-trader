"""Shared pytest fixtures for MNQ AI Trader tests."""
import sys
from pathlib import Path

# Make the project root importable for every test module.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture
def mock_snapshot():
    """A representative bullish-leaning snapshot for pre-filter / brain tests."""
    return {
        "last_price":            30000.0,
        "bid":                   29999.75,
        "ask":                   30000.25,
        "volume":                1_500_000,
        "vwap":                  29980.0,
        "cumulative_delta":      150,
        "or_high":               29950.0,
        "or_low":                29900.0,
        "or_direction":          "BULL",
        "or_relative_volume":    150,
        "or_entry_zone_active":  False,
        "or_break_attempts":     0,
        "choch":                 "CHoCH BULLISH",
        "mtf_alignment":         "BULLISH_ALIGNED",
        "dom_imbalance":         "BID_HEAVY",
        "dom_vacuum_above":      False,
        "dom_vacuum_below":      False,
        "dom_buy_pressure":      0.65,
        "dom_sweep_up":          False,
        "dom_sweep_down":        False,
        "ofi": {"score": 50, "signal": "BUY", "acceleration": "STABLE"},
        "candle_patterns":       "",
        "tape_bias":             "NEUTRAL",
        "fair_value_gaps":       "",
        "order_blocks":          "",
        "daily_zones":           {},
        "news_danger_zone":      False,
        "premarket_high":        29960.0,
        "premarket_low":         29880.0,
        "session_high":          30010.0,
        "session_low":           29870.0,
    }


@pytest.fixture
def mock_decision():
    """A normal BUY decision with all required fields populated."""
    return {
        "decision":           "BUY",
        "mode":               "OB_BOUNCE",
        "contracts":          1,
        "entry_price":        30000.0,
        "stop_price":         29980.0,
        "target_1":           30040.0,
        "target_2":           30080.0,
        "stop_ticks":         80,
        "target_ticks":       160,
        "confidence":         "HIGH",
        "thesis_probability": 72,
        "strategy":           "OB_BOUNCE",
        "confluence":         "OR_BREAK + VWAP + DELTA",
        "confluence_score":   8,
        "reasoning":          "Test fixture decision",
    }


@pytest.fixture
def mock_watchlist():
    """The shape pre_filter_signal expects from claude_brain.get_watchlist().
    Default bias is LONG_PREFERRED so tests exercise the bias-aware branch."""
    return {
        "bias":               "LONG_PREFERRED",
        "bias_invalidated":   False,
    }


@pytest.fixture
def sample_env(monkeypatch):
    """Apply a clean, predictable env so config-reading tests are stable."""
    monkeypatch.setenv("MAX_CONTRACTS",       "1")
    monkeypatch.setenv("MAX_DAILY_LOSS_PCT",  "0.20")
    monkeypatch.setenv("ACCOUNT_SIZE",        "50000")
    monkeypatch.setenv("RECORDING_ENABLED",   "false")
    return None
