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
