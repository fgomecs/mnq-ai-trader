"""
Contract tests for chart_test.html — verify the file references the
right JSON sources (dashboard_data.json for OHLC, price_data.json for
the live tick) and that the bar schema the chart expects matches what
dashboard_writer actually emits.
"""
import json
import re
from pathlib import Path

import pytest


CHART_PATH = Path(r"C:\trading\mnq-ai-trader\chart_test.html")
CHART = CHART_PATH.read_text(encoding="utf-8")


def test_chart_references_dashboard_data_json():
    """The historical bars source must be dashboard_data.json, not price."""
    assert "fetch('dashboard_data.json" in CHART or 'fetch("dashboard_data.json' in CHART, \
        "chart_test.html must fetch dashboard_data.json for bars"


def test_chart_references_price_data_json_for_live_tick():
    """Live-tick update path must still poll price_data.json."""
    assert "fetch('price_data.json" in CHART or 'fetch("price_data.json' in CHART, \
        "chart_test.html must fetch price_data.json for live tick"


def test_chart_reads_bars1min_and_bars5min_keys():
    """Must read bars1min and bars5min (the keys dashboard_writer emits)."""
    assert "bars1min" in CHART, "chart_test.html does not reference bars1min"
    assert "bars5min" in CHART, "chart_test.html does not reference bars5min"


def test_chart_maps_ohlc_short_field_names():
    """Bars use short keys o/h/l/c/v/t/vwap per _serialize_bars_with_vwap.
    The chart should read these by name."""
    for field in ("b.o", "b.h", "b.l", "b.c", "b.v"):
        assert field in CHART, f"chart_test.html does not read bar field {field}"


def test_chart_polls_dashboard_at_two_second_cadence():
    """Bars only change on bar close — 2s is plenty and matches the
    project's standard dashboard polling cadence."""
    assert "setInterval(pollDashboard" in CHART
    # 2000ms cadence
    assert re.search(r"setInterval\(\s*pollDashboard\s*,\s*2000\s*\)", CHART), \
        "pollDashboard not on 2000ms interval"


def test_chart_polls_price_at_500ms_cadence():
    assert re.search(r"setInterval\(\s*pollPrice\s*,\s*500\s*\)", CHART), \
        "pollPrice not on 500ms interval"


def test_chart_ingest_uses_number_not_num_helper_for_ohlc():
    """The num() helper rejects 0 as invalid (was designed for entry/stop
    sentinels). Using it on OHLC silently drops bars that legitimately
    contain a 0 value. ingestList must use Number() + Number.isFinite()
    so zero is accepted."""
    assert "const open  = Number(b.o)" in CHART, \
        "ingestList should coerce open via Number(b.o), not num()"
    assert "const close = Number(b.c)" in CHART, \
        "ingestList should coerce close via Number(b.c), not num()"
    assert "Number.isFinite(open)" in CHART and "Number.isFinite(close)" in CHART, \
        "ingestList must validate via Number.isFinite (zero is valid; NaN is not)"


def test_chart_ingest_logs_diagnostic_first_bar():
    """A console.log after the first ingest is the only way to verify the
    mapping shape from DevTools without rerunning the bot. Don't lose it."""
    assert "[chart] ingestList(" in CHART, \
        "ingestList must emit a [chart] DevTools log of the first mapped bar"


def test_dashboard_writer_emits_bars_keys(tmp_path, monkeypatch):
    """Schema match: dashboard JSON must include bars1min/bars5min as
    lists (possibly empty) so the chart can rely on the field names."""
    import dashboard_writer as dw
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))
    dw.update_dashboard(
        position=0, current_price=30000.0, daily_pnl=0.0, trades=[],
        last_decision="HOLD", last_reasoning="x",
        snapshot={
            "last_price": 30000.0,
            "bars_1min": [
                {"t": "2026-05-27 09:30", "o": 30000.0, "h": 30005.0,
                 "l": 29998.0, "c": 30003.0, "v": 120, "vwap": 30001.5,
                 "forming": False},
            ],
            "bars_5min": [],
        },
    )
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "bars1min" in data, "dashboard JSON missing bars1min key"
    assert "bars5min" in data, "dashboard JSON missing bars5min key"
    assert isinstance(data["bars1min"], list)
    if data["bars1min"]:
        bar = data["bars1min"][0]
        # Field names the chart reads
        for k in ("t", "o", "h", "l", "c", "v"):
            assert k in bar, f"bar missing field {k}"
