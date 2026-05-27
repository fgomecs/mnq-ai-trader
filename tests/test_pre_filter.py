"""
Phase 2 — pre_filter_signal unit tests.
Each test seeds the module-global _session_watchlist so the bias-aware
branches are reachable (the backtester taught us this lesson).
"""
import pytest


def _seed_watchlist(bias="NEUTRAL", invalidated=False):
    import claude_brain
    claude_brain._session_watchlist = {
        "bias": bias,
        "bias_invalidated": invalidated,
    }


def _minimal_snapshot(**overrides):
    """Bare-minimum bullish snapshot — clears nearly every signal so a
    targeted assertion can confirm a *specific* reason bubbles into the
    top-4 reasons list (which pre_filter_signal truncates the message to)."""
    snap = {
        "last_price":         29940.0,    # below OR high → no breakout signal
        "vwap":               29950.0,    # below VWAP    → no above-vwap
        "cumulative_delta":   0,
        "or_high":            29950.0,
        "or_low":             29900.0,
        "or_direction":       "BULL",
        "or_relative_volume": 100,        # passes rel-vol floor
        "or_entry_zone_active": False,
        "choch":              "",
        "mtf_alignment":      "",
        "dom_imbalance":      "NEUTRAL",
        "dom_vacuum_above":   False,
        "dom_vacuum_below":   False,
        "dom_buy_pressure":   0.5,
        "dom_sweep_up":       False,
        "dom_sweep_down":     False,
        "ofi": {"score": 0, "signal": "NEUTRAL", "acceleration": "STABLE"},
        "candle_patterns":    "",
        "tape_bias":          "NEUTRAL",
        "fair_value_gaps":    "",
        "order_blocks":       "",
        "daily_zones":        {},
        "news_danger_zone":   False,
        "premarket_high":     0,           # disabled
        "premarket_low":      0,           # disabled
    }
    snap.update(overrides)
    return snap


def _bull_snapshot(**overrides):
    """Strong bullish snapshot — multiple signals firing."""
    snap = {
        "last_price":         30000.0,
        "vwap":               29950.0,
        "cumulative_delta":   200,
        "or_high":            29950.0,
        "or_low":             29900.0,
        "or_direction":       "BULL",
        "or_relative_volume": 150,
        "or_entry_zone_active": False,
        "choch":              "CHoCH BULLISH",
        "mtf_alignment":      "BULLISH_ALIGNED",
        "dom_imbalance":      "BID_HEAVY",
        "dom_vacuum_above":   False,
        "dom_vacuum_below":   False,
        "dom_buy_pressure":   0.65,
        "dom_sweep_up":       False,
        "dom_sweep_down":     False,
        "ofi": {"score": 50, "signal": "BUY", "acceleration": "STABLE"},
        "candle_patterns":    "",
        "tape_bias":          "NEUTRAL",
        "fair_value_gaps":    "",
        "order_blocks":       "",
        "daily_zones":        {},
        "news_danger_zone":   False,
        "premarket_high":     29960.0,
        "premarket_low":      29880.0,
    }
    snap.update(overrides)
    return snap


def _bear_snapshot(**overrides):
    snap = _bull_snapshot()
    snap.update({
        "last_price":         29800.0,
        "vwap":               29950.0,
        "cumulative_delta":  -200,
        "or_direction":       "BEAR",
        "choch":              "CHoCH BEARISH",
        "mtf_alignment":      "BEARISH_ALIGNED",
        "dom_imbalance":      "ASK_HEAVY",
        "dom_buy_pressure":   0.35,
        "ofi": {"score": -50, "signal": "SELL", "acceleration": "STABLE"},
    })
    snap.update(overrides)
    return snap


@pytest.fixture(autouse=True)
def reset_session_classifier():
    """Make sure the RANGE-day test doesn't leak into other tests."""
    from session_classifier import set_session_type, SessionType
    set_session_type(SessionType.UNKNOWN)
    yield
    set_session_type(SessionType.UNKNOWN)


# ── direction gating ───────────────────────────────────────────────
def test_long_preferred_passes_bull():
    from claude_brain import pre_filter_signal
    _seed_watchlist("LONG_PREFERRED")
    worth, reason = pre_filter_signal(_bull_snapshot())
    assert worth, f"LONG_PREFERRED + bull snapshot should pass: {reason}"
    assert reason.startswith("BULL"), reason


def test_long_preferred_blocks_bear_at_low_count():
    """A handful of bear signals must NOT pass when bias is LONG_PREFERRED
    (counter-trend requires 5+)."""
    from claude_brain import pre_filter_signal
    _seed_watchlist("LONG_PREFERRED")
    # Strip down the bear snapshot to minimal counter signals
    snap = _bear_snapshot(
        choch="",
        mtf_alignment="",
        dom_imbalance="NEUTRAL",
        ofi={"score": 0, "signal": "NEUTRAL", "acceleration": "STABLE"},
        dom_buy_pressure=0.5,
    )
    worth, reason = pre_filter_signal(snap)
    if worth:
        # If it does pass, must be flagged as counter-trend with 5+ signals
        assert "counter-trend" in reason
    else:
        assert "insufficient" in reason.lower() or "bear signals" in reason.lower() or "no qualifying" in reason.lower()


def test_short_preferred_passes_bear():
    from claude_brain import pre_filter_signal
    _seed_watchlist("SHORT_PREFERRED")
    worth, reason = pre_filter_signal(_bear_snapshot())
    assert worth, f"SHORT_PREFERRED + bear snapshot should pass: {reason}"
    assert reason.startswith("BEAR"), reason


def test_neutral_bias_passes_either_direction():
    from claude_brain import pre_filter_signal
    _seed_watchlist("NEUTRAL")
    bull_ok, bull_r = pre_filter_signal(_bull_snapshot())
    bear_ok, bear_r = pre_filter_signal(_bear_snapshot())
    assert bull_ok and "NEUTRAL bias" in bull_r
    assert bear_ok and "NEUTRAL bias" in bear_r


# ── hard gates ─────────────────────────────────────────────────────
def test_no_trade_bias_blocks_regardless_of_signals():
    from claude_brain import pre_filter_signal
    _seed_watchlist("NO_TRADE")
    worth, reason = pre_filter_signal(_bull_snapshot())
    assert not worth
    assert "NO_TRADE" in reason


def test_mtf_conflicted_blocks():
    from claude_brain import pre_filter_signal
    _seed_watchlist("NEUTRAL")
    snap = _bull_snapshot(mtf_alignment="CONFLICTED — TFs split 1/3 bull")
    worth, reason = pre_filter_signal(snap)
    assert not worth
    assert "MTF conflicted" in reason


def test_low_relative_volume_blocks():
    from claude_brain import pre_filter_signal
    _seed_watchlist("NEUTRAL")
    snap = _bull_snapshot(or_relative_volume=40)
    worth, reason = pre_filter_signal(snap)
    assert not worth
    assert "rel vol" in reason.lower()


def test_range_day_requires_seven_signals():
    """On RANGE days the threshold rises so weak setups are rejected."""
    from claude_brain import pre_filter_signal
    from session_classifier import set_session_type, SessionType
    _seed_watchlist("NEUTRAL")
    set_session_type(SessionType.RANGE)
    # Reduce signal strength so we land at 3-4 signals (below 7 RANGE threshold)
    snap = _bull_snapshot(
        choch="",
        mtf_alignment="",
        dom_imbalance="NEUTRAL",
        dom_buy_pressure=0.5,
        ofi={"score": 0, "signal": "NEUTRAL", "acceleration": "STABLE"},
        cumulative_delta=10,
        premarket_high=0,
        premarket_low=0,
    )
    worth, reason = pre_filter_signal(snap)
    # The reduced snapshot still has "above OR high" (+2) and "above VWAP" (+1)
    # = 3 signals — passes 3 threshold, fails 7. So on RANGE day must NOT pass.
    assert not worth, f"RANGE day should reject 3-signal setup: {reason}"


# ── OFI feature flag ───────────────────────────────────────────────
def test_ofi_strong_buy_contributes_when_feature_on(monkeypatch):
    """STRONG_BUY OFI adds +2 (visible in reason text when FEATURE_OFI=true)."""
    import importlib
    monkeypatch.setenv("FEATURE_OFI", "true")
    import config; importlib.reload(config)
    import claude_brain; importlib.reload(claude_brain)
    try:
        claude_brain._session_watchlist = {"bias": "NEUTRAL", "bias_invalidated": False}
        # Minimal snapshot — only OFI signals fire (+3). Pre-filter says
        # NEUTRAL needs threshold (3) AND > opposite side. So we also need
        # to avoid bear signals firing. _minimal_snapshot does that.
        snap = _minimal_snapshot(
            ofi={"score": 100, "signal": "STRONG_BUY", "acceleration": "ACCELERATING"},
            # add a small bull tilt so we clear NEUTRAL threshold
            mtf_alignment="BULLISH_ALIGNED",
        )
        worth, reason = claude_brain.pre_filter_signal(snap)
        assert worth, f"expected pass with OFI STRONG_BUY contributing, got: {reason}"
        assert "OFI STRONG_BUY" in reason, \
            f"OFI STRONG_BUY missing from reason: {reason!r}"
    finally:
        monkeypatch.delenv("FEATURE_OFI", raising=False)
        importlib.reload(config)
        importlib.reload(claude_brain)


def test_ofi_ignored_when_feature_off(monkeypatch):
    import importlib
    monkeypatch.setenv("FEATURE_OFI", "false")
    import config; importlib.reload(config)
    import claude_brain; importlib.reload(claude_brain)
    try:
        claude_brain._session_watchlist = {"bias": "NEUTRAL", "bias_invalidated": False}
        snap = _minimal_snapshot(
            ofi={"score": 100, "signal": "STRONG_BUY", "acceleration": "ACCELERATING"},
            mtf_alignment="BULLISH_ALIGNED",
        )
        worth, reason = claude_brain.pre_filter_signal(snap)
        # OFI signal must NOT appear in reason when feature is off
        assert "OFI STRONG_BUY" not in reason
    finally:
        monkeypatch.delenv("FEATURE_OFI", raising=False)
        importlib.reload(config)
        importlib.reload(claude_brain)


# ── OR entry zone ─────────────────────────────────────────────────
def test_or_entry_zone_active_adds_to_both_sides():
    """or_entry_zone_active contributes +2 to bull AND bear scoring."""
    from claude_brain import pre_filter_signal
    _seed_watchlist("NEUTRAL")
    # Use minimal snapshot so the OR entry zone reason bubbles into top-4
    snap = _minimal_snapshot(
        or_entry_zone_active=True,
        mtf_alignment="BULLISH_ALIGNED",
    )
    worth, reason = pre_filter_signal(snap)
    assert worth
    assert "entry zone active" in reason, \
        f"'entry zone active' missing from reason: {reason!r}"


# ── shape of return ───────────────────────────────────────────────
def test_pass_returns_true_and_descriptive_reason():
    from claude_brain import pre_filter_signal
    _seed_watchlist("NEUTRAL")
    worth, reason = pre_filter_signal(_bull_snapshot())
    assert worth is True
    assert isinstance(reason, str) and len(reason) > 0


def test_block_returns_false_and_descriptive_reason():
    from claude_brain import pre_filter_signal
    _seed_watchlist("NO_TRADE")
    worth, reason = pre_filter_signal(_bull_snapshot())
    assert worth is False
    assert isinstance(reason, str) and len(reason) > 0
