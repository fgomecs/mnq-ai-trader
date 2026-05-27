"""
chart_test2.html — minimal diagnostic implementation. Pins the contract
so a future cleanup doesn't accidentally remove the visible debug surface.
"""
from pathlib import Path

import pytest


HTML = Path(r"C:\trading\mnq-ai-trader\chart_test2.html").read_text(encoding="utf-8")


def test_loads_lightweight_charts_v4_from_unpkg():
    """Pinned to v4 — the version this implementation targets."""
    assert "lightweight-charts@4" in HTML, \
        "must pin Lightweight Charts to v4 on the CDN URL"


def test_polls_dashboard_data_json():
    assert "fetch('dashboard_data.json" in HTML, \
        "must fetch dashboard_data.json for bars"


def test_renders_only_candles_no_overlays():
    """Minimal scope: candlesticks only — no volume, vwap, markers,
    price lines, or 5M/15M aggregation."""
    assert "addCandlestickSeries" in HTML
    for forbidden in ("addHistogramSeries", "addLineSeries",
                      "createPriceLine", "setMarkers", "aggregate"):
        assert forbidden not in HTML, \
            f"chart_test2 must stay minimal — '{forbidden}' should not appear"


def test_uses_number_isfinite_for_ohlc():
    """Zero is a valid OHLC value; we reject only NaN."""
    assert "Number.isFinite(open)" in HTML
    assert "Number.isFinite(close)" in HTML


def test_sorts_ascending_before_setdata():
    """setData() requires strictly ascending time."""
    assert "out.sort((a, b) => a.time - b.time)" in HTML


def test_dedupes_timestamps():
    """setData() rejects duplicate times."""
    assert "seenTimes" in HTML, \
        "must dedupe equal timestamps before setData"


def test_status_panel_shows_input_mapped_and_errors():
    """In-page diagnostic surface — visible without DevTools."""
    for status_id in ("s-lib", "s-http", "s-input", "s-raw",
                      "s-mapped", "s-first", "s-last", "s-set", "s-err"):
        assert f'id="{status_id}"' in HTML, \
            f"chart_test2 status panel missing {status_id}"


def test_handles_undefined_lightweight_charts():
    """Surface 'CDN failed to load' clearly in-page if the script doesn't load."""
    assert "typeof LightweightCharts === 'undefined'" in HTML
    assert "CDN failed to load" in HTML


def test_polls_price_data_for_live_forming_bar():
    """The dashboard bars only roll on minute close — between closes
    the chart must update the in-progress bar from price_data.json so
    we see live ticks, not a stale chart."""
    assert "fetch('price_data.json" in HTML, \
        "chart_test2 must also poll price_data.json for the live tick"
    assert "candleSeries.update(" in HTML, \
        "live forming bar must update via candleSeries.update(), not setData"


def test_live_poll_runs_at_500ms_cadence():
    """Live tick poll matches the bot's fast-ticker cadence."""
    import re
    assert re.search(r"setInterval\(\s*pollLive\s*,\s*500\s*\)", HTML), \
        "pollLive must run on 500ms interval"


def test_historical_poll_runs_at_2000ms_cadence():
    """Historical (closed-bar) poll stays on the slower 2s cadence."""
    import re
    assert re.search(r"setInterval\(\s*pollHistorical\s*,\s*2000\s*\)", HTML), \
        "pollHistorical must run on 2000ms interval"


def test_forming_bar_status_id_present():
    """In-page diagnostic surface must show the forming bar so we
    can verify live updates without DevTools."""
    assert 'id="s-forming"' in HTML


# ── BUG-1: forming-bar time alignment with historical bars ──
def test_bug1_uses_et_minute_via_intl_for_forming_bar_time():
    """The forming bar's time must come from current ET wall-clock
    fed through the SAME parseEtAsUtcSec used for historical bars,
    so both land on the same x-axis convention. The old
    currentMinuteEpochSec used Date.now() directly → 4-5h gap."""
    assert "currentEtMinuteAsUtcSec" in HTML, \
        "must define currentEtMinuteAsUtcSec helper"
    assert "'America/New_York'" in HTML, \
        "must resolve ET wall-clock via Intl with America/New_York timezone"
    # currentEtMinuteAsUtcSec must route through parseEtAsUtcSec — that
    # is what guarantees same-axis convention with historical bars
    assert "parseEtAsUtcSec(m[1] + ' ' + m[2])" in HTML, \
        "currentEtMinuteAsUtcSec must invoke parseEtAsUtcSec for consistency"


def test_bug1_legacy_helper_removed():
    """currentMinuteEpochSec used Date.now() and caused the 4-5h gap.
    Must be deleted, not just unused."""
    assert "currentMinuteEpochSec" not in HTML, \
        "currentMinuteEpochSec must be removed entirely; was the source of BUG-1"


def test_bug1_pollLive_uses_new_helper():
    """pollLive must call the new ET-aware helper for forming-bar time."""
    assert "currentEtMinuteAsUtcSec()" in HTML, \
        "pollLive must call currentEtMinuteAsUtcSec()"


# ── BUG-2: forming bar fallback when currentBarHigh/Low are null ──
def test_bug2_tracks_last_historical_high_and_low():
    """The forming-bar fallback when currentBarHigh/Low are null
    must use the most recent completed bar's range as a seed so the
    candle isn't a flat dot."""
    assert "lastHistoricalHigh" in HTML, "must track lastHistoricalHigh"
    assert "lastHistoricalLow"  in HTML, "must track lastHistoricalLow"
    # Captured from the last item of `mapped` in pollHistorical
    assert "lastHistoricalHigh = last.high" in HTML
    assert "lastHistoricalLow  = last.low" in HTML


def test_bug2_pollLive_fallback_uses_historical_then_price():
    """High fallback: max(lastHistoricalHigh, price). Same shape for low."""
    # Match exactly the fallback expressions
    assert "Math.max(lastHistoricalHigh, price)" in HTML, \
        "high fallback must be Math.max(lastHistoricalHigh, price)"
    assert "Math.min(lastHistoricalLow,  price)" in HTML, \
        "low fallback must be Math.min(lastHistoricalLow, price)"


def test_bug2_resets_history_seeds_on_empty_dashboard():
    """If dashboard write is empty (sleep, race), historical seeds
    must reset to 0 so we fall through to the price-only path,
    not carry stale yesterday-night values."""
    assert "lastHistoricalHigh = 0" in HTML
    assert "lastHistoricalLow  = 0" in HTML
