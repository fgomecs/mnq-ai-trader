"""
Phase 4 — Market data edge cases.
Snapshots with missing/zero/extreme/garbage fields must NOT crash
pre_filter_signal, the dashboard writer, or the recorder helpers.
"""
import pytest


def _seed_neutral():
    import claude_brain
    claude_brain._session_watchlist = {"bias": "NEUTRAL", "bias_invalidated": False}


# ── Missing fields ─────────────────────────────────────────────────
def test_pre_filter_handles_empty_snapshot():
    from claude_brain import pre_filter_signal
    _seed_neutral()
    # Almost-empty snapshot — only enough to get past or_direction gate
    snap = {"or_direction": "BULL"}
    worth, reason = pre_filter_signal(snap)
    # Must NOT raise. Likely blocks for insufficient signals or rel vol.
    assert isinstance(worth, bool) and isinstance(reason, str)


def test_pre_filter_handles_snapshot_with_none_values():
    from claude_brain import pre_filter_signal
    _seed_neutral()
    snap = {
        "or_direction": "BULL", "or_relative_volume": 100,
        "last_price": None, "vwap": None, "cumulative_delta": None,
        "or_high": None, "or_low": None,
        "ofi": None, "daily_zones": None,
    }
    worth, reason = pre_filter_signal(snap)
    assert isinstance(worth, bool)


def test_pre_filter_handles_no_or_direction():
    from claude_brain import pre_filter_signal
    _seed_neutral()
    snap = {"last_price": 30000.0}
    worth, reason = pre_filter_signal(snap)
    assert not worth
    assert "OR direction" in reason or "or" in reason.lower()


# ── Zero / extreme values ──────────────────────────────────────────
def test_pre_filter_handles_last_price_zero():
    """Sensor glitch / pre-market gap. last_price=0 must not crash; the
    above-OR / above-VWAP signals just won't fire."""
    from claude_brain import pre_filter_signal
    _seed_neutral()
    snap = {
        "or_direction": "BULL", "or_relative_volume": 120,
        "last_price": 0.0, "vwap": 29950.0, "or_high": 29950.0, "or_low": 29900.0,
        "mtf_alignment": "BULLISH_ALIGNED",
    }
    worth, reason = pre_filter_signal(snap)
    assert isinstance(worth, bool)


def test_pre_filter_handles_negative_volume():
    """Negative rel-vol is nonsensical but must not crash."""
    from claude_brain import pre_filter_signal
    _seed_neutral()
    snap = {"or_direction": "BULL", "or_relative_volume": -1,
            "last_price": 30000.0, "or_high": 29950.0, "or_low": 29900.0}
    worth, reason = pre_filter_signal(snap)
    # Should block on "rel vol too low"
    assert not worth
    assert "rel vol" in reason.lower()


def test_pre_filter_handles_very_large_numbers():
    """No overflow on extreme prices."""
    from claude_brain import pre_filter_signal
    _seed_neutral()
    snap = {
        "or_direction": "BULL", "or_relative_volume": 100,
        "last_price": 1e9, "vwap": 1e8, "cumulative_delta": 1e6,
        "or_high": 1e8, "or_low": 0.0,
        "mtf_alignment": "BULLISH_ALIGNED",
    }
    worth, reason = pre_filter_signal(snap)
    assert isinstance(worth, bool)


# ── Garbage values in nested dicts ─────────────────────────────────
def test_pre_filter_handles_unexpected_dom_imbalance_string():
    from claude_brain import pre_filter_signal
    _seed_neutral()
    snap = {
        "or_direction": "BULL", "or_relative_volume": 100,
        "last_price": 30000.0, "or_high": 29950.0, "or_low": 29900.0,
        "vwap": 29950.0, "dom_imbalance": "GARBAGE_VALUE",
    }
    worth, reason = pre_filter_signal(snap)
    # No crash; "GARBAGE_VALUE" won't match BID_HEAVY / ASK_HEAVY checks.
    assert isinstance(worth, bool)


def test_pre_filter_handles_ofi_missing_score():
    """ofi dict missing the 'score' field — use defaults."""
    from claude_brain import pre_filter_signal
    _seed_neutral()
    snap = {
        "or_direction": "BULL", "or_relative_volume": 100,
        "last_price": 30000.0, "or_high": 29950.0, "or_low": 29900.0,
        "ofi": {},   # empty dict
    }
    worth, reason = pre_filter_signal(snap)
    assert isinstance(worth, bool)


# ── News cache edge cases ──────────────────────────────────────────
def test_pre_filter_handles_missing_news_field():
    from claude_brain import pre_filter_signal
    _seed_neutral()
    snap = {"or_direction": "BULL", "or_relative_volume": 100,
            "last_price": 30000.0, "or_high": 29950.0, "or_low": 29900.0}
    # No news_danger_zone key at all — defaults to False / not flagged.
    worth, reason = pre_filter_signal(snap)
    assert isinstance(worth, bool)
