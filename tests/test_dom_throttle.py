"""
DOM throttling + wire-rate monitor tests.

Background: 2026-05-27 09:14 ET, IBKR Gateway killed the socket after the
EWriter buffer grew to ~5MB. Root cause was reqMktDepth(numRows=40) +
reqTickByTickData('AllLast') producing more wire-level updates than the
single asyncio event loop could drain.

The throttle does NOT reduce wire traffic — IBKR sends what IBKR sends.
What it does:
  1. Caches the *processed* DOM signals/text for DOM_THROTTLE_SECS so a
     burst of get_snapshot() calls doesn't multiply the CPU cost.
  2. Counts DOM updateEvent fires and emits a one-shot warning when
     sustained rate exceeds DOM_UPDATE_RATE_WARN_HZ — operator-facing
     signal that the firehose is too large for the current setup.

These tests pin the throttling semantics and the warning behavior.
"""
import time
import types

import pytest


# ─── Helpers: mock feed without real IBKR ─────────────────────
def _make_feed_with_dom(monkeypatch, *, throttle_secs=0.1, warn_hz=200):
    """Build an IBKRFeed instance with the DOM ticker mocked, no IB
    connection. Lets us drive _compute_dom_signals / _get_live_dom in
    isolation."""
    monkeypatch.setenv("DOM_THROTTLE_SECS",     str(throttle_secs))
    monkeypatch.setenv("DOM_UPDATE_RATE_WARN_HZ", str(warn_hz))
    import importlib
    import config; importlib.reload(config)
    import ibkr_feed; importlib.reload(ibkr_feed)

    # Skip __init__'s IBKR setup by constructing without going through it.
    feed = ibkr_feed.IBKRFeed.__new__(ibkr_feed.IBKRFeed)

    # Mirror only the state the throttling/monitor paths read.
    feed._dom_signals_cache     = None
    feed._dom_signals_cache_ts  = 0.0
    feed._dom_text_cache        = None
    feed._dom_text_cache_ts     = 0.0
    feed._dom_throttle_secs     = throttle_secs
    feed._dom_update_count      = 0
    feed._dom_update_rate_ts    = time.time()
    feed._dom_update_warned     = False

    # Minimal DOM ticker mock with empty depth lists.
    feed.dom_ticker              = types.SimpleNamespace(domAsks=[], domBids=[])
    feed.dom_subscription_active = True
    # Required by _compute_dom_signals_impl history append guard.
    feed._dom_history     = []
    feed._dom_history_max = 12
    return feed


# ── Throttle cache behavior ───────────────────────────────────
def test_dom_signals_cached_within_throttle_window(monkeypatch):
    """Two calls inside the throttle window must return the SAME object —
    the cache hit returns the previous result by reference."""
    feed = _make_feed_with_dom(monkeypatch, throttle_secs=0.5)
    first = feed._compute_dom_signals()
    second = feed._compute_dom_signals()
    # Same dict instance → cache hit (not a fresh impl call).
    assert second is first, "expected cached return"


def test_dom_signals_recomputed_after_throttle_window(monkeypatch):
    """After the throttle window elapses, the next call must produce a
    new result object (cache invalidated)."""
    feed = _make_feed_with_dom(monkeypatch, throttle_secs=0.05)
    first = feed._compute_dom_signals()
    time.sleep(0.10)
    second = feed._compute_dom_signals()
    assert second is not first, "expected fresh computation after window"


def test_dom_signals_throttle_disabled_when_zero(monkeypatch):
    """Throttle=0 must disable the cache entirely (always fresh)."""
    feed = _make_feed_with_dom(monkeypatch, throttle_secs=0.0)
    first  = feed._compute_dom_signals()
    second = feed._compute_dom_signals()
    assert second is not first, \
        "throttle=0 must bypass the cache and recompute every call"


def test_dom_text_cached_within_throttle_window(monkeypatch):
    """_get_live_dom shares the same throttle window with its own cache."""
    feed = _make_feed_with_dom(monkeypatch, throttle_secs=0.5)
    first  = feed._get_live_dom()
    second = feed._get_live_dom()
    assert second == first
    # Verify the cache was actually hit (timestamp didn't move).
    ts1 = feed._dom_text_cache_ts
    feed._get_live_dom()
    assert feed._dom_text_cache_ts == ts1


# ── Wire-rate warning ─────────────────────────────────────────
def test_dom_update_counter_increments_on_event(monkeypatch):
    """The cheap counter hook must increment the update tally."""
    feed = _make_feed_with_dom(monkeypatch)
    assert feed._dom_update_count == 0
    feed._on_dom_update()
    feed._on_dom_update()
    feed._on_dom_update()
    assert feed._dom_update_count == 3


def test_dom_rate_warning_fires_when_threshold_exceeded(monkeypatch, caplog):
    """Pumping updates fast enough must trigger the one-shot warning."""
    feed = _make_feed_with_dom(monkeypatch, throttle_secs=0.05, warn_hz=10)

    # Backdate the rate window so >=1s has elapsed for the rate calc.
    feed._dom_update_rate_ts = time.time() - 1.1
    feed._dom_update_count   = 500    # 500 / ~1.1s ≈ 450 Hz >> 10 Hz threshold

    import logging
    with caplog.at_level(logging.WARNING):
        feed._check_dom_update_rate()
    assert feed._dom_update_warned is True, "warning flag must be set"
    msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("DOM update rate" in m for m in msgs), \
        f"expected DOM-rate warning in log; got: {msgs}"


def test_dom_rate_warning_is_one_shot(monkeypatch, caplog):
    """Once warned, subsequent rate-checks must NOT spam the log."""
    feed = _make_feed_with_dom(monkeypatch, throttle_secs=0.05, warn_hz=10)
    feed._dom_update_warned = True   # pretend we already warned

    feed._dom_update_rate_ts = time.time() - 1.1
    feed._dom_update_count   = 500

    import logging
    with caplog.at_level(logging.WARNING):
        feed._check_dom_update_rate()
        feed._check_dom_update_rate()
        feed._check_dom_update_rate()
    rate_warns = [r for r in caplog.records
                  if r.levelno >= logging.WARNING and "DOM update rate" in r.message]
    assert len(rate_warns) == 0, "warning should not re-fire once latched"


def test_dom_rate_no_warning_below_threshold(monkeypatch, caplog):
    """Sustained low-rate updates must not warn."""
    feed = _make_feed_with_dom(monkeypatch, throttle_secs=0.05, warn_hz=500)
    feed._dom_update_rate_ts = time.time() - 1.1
    feed._dom_update_count   = 50    # ~45 Hz, well under 500

    import logging
    with caplog.at_level(logging.WARNING):
        feed._check_dom_update_rate()
    assert feed._dom_update_warned is False
    rate_warns = [r for r in caplog.records
                  if r.levelno >= logging.WARNING and "DOM update rate" in r.message]
    assert len(rate_warns) == 0


# ── Config knobs ──────────────────────────────────────────────
def test_config_exposes_throttle_constants(monkeypatch):
    monkeypatch.setenv("DOM_THROTTLE_SECS",       "0.25")
    monkeypatch.setenv("DOM_UPDATE_RATE_WARN_HZ", "300")
    import importlib, config
    importlib.reload(config)
    try:
        assert config.DOM_THROTTLE_SECS == pytest.approx(0.25)
        assert config.DOM_UPDATE_RATE_WARN_HZ == 300
    finally:
        monkeypatch.delenv("DOM_THROTTLE_SECS", raising=False)
        monkeypatch.delenv("DOM_UPDATE_RATE_WARN_HZ", raising=False)
        importlib.reload(config)
