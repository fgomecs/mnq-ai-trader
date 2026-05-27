"""
Phase 3 — Backtester pipeline tests.
"""
import json
import re
import io
import contextlib

import pytest


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


# ── Loaders ───────────────────────────────────────────────────────
def test_load_snapshots_reads_jsonl(tmp_path, monkeypatch):
    import backtester
    monkeypatch.setattr(backtester, "DATA_PATH", tmp_path)
    path = tmp_path / "snapshots_2026-05-26.jsonl"
    _write_jsonl(path, [
        {"ts_et": "09:45:00", "data": {"last_price": 30000.0}},
        {"ts_et": "09:46:00", "data": {"last_price": 30002.5}},
    ])
    snaps = backtester.load_snapshots("2026-05-26")
    assert len(snaps) == 2
    assert snaps[0]["data"]["last_price"] == 30000.0


def test_load_decisions_reads_jsonl(tmp_path, monkeypatch):
    import backtester
    monkeypatch.setattr(backtester, "DATA_PATH", tmp_path)
    path = tmp_path / "decisions_2026-05-26.jsonl"
    _write_jsonl(path, [
        {"type": "decision", "ts_et": "09:45:00",
         "decision": {"decision": "BUY"}, "pre_filter_reason": "BULL 7 signals"},
        {"type": "trade",    "ts_et": "09:46:00", "pnl": 40.0},  # ignored by loader
    ])
    decs = backtester.load_decisions("2026-05-26")
    # load_decisions only keeps type='decision'
    assert "09:45:00" in decs
    assert decs["09:45:00"]["decision"]["decision"] == "BUY"


# ── infer_session_bias ────────────────────────────────────────────
def test_infer_session_bias_long_preferred_from_bull_reasons():
    from backtester import infer_session_bias
    decisions = {
        "09:45:00": {"pre_filter_reason": "BULL 9 signals [above OR high]"},
        "09:46:00": {"pre_filter_reason": "BULL 12 signals [above VWAP]"},
        "09:47:00": {"pre_filter_reason": "BULL 8 signals [delta positive]"},
    }
    assert infer_session_bias(decisions) == "LONG_PREFERRED"


def test_infer_session_bias_neutral_when_no_decisions():
    from backtester import infer_session_bias
    assert infer_session_bias({}) == "NEUTRAL"


# ── End-to-end mini backtest ──────────────────────────────────────
def test_backtest_with_seeded_bias_yields_passes(tmp_path, monkeypatch):
    """Synthesise a tiny snapshot/decision pair, run the backtester, and
    verify pre-filter actually fires with a seeded bias."""
    import backtester
    monkeypatch.setattr(backtester, "DATA_PATH", tmp_path)

    snap = {
        "last_price":         30000.0,
        "vwap":               29950.0,
        "cumulative_delta":   200,
        "or_high":             29950.0,
        "or_low":              29900.0,
        "or_direction":        "BULL",
        "or_relative_volume":  150,
        "or_entry_zone_active": False,
        "choch":               "CHoCH BULLISH",
        "mtf_alignment":       "BULLISH_ALIGNED",
        "dom_imbalance":       "BID_HEAVY",
        "dom_buy_pressure":    0.65,
        "ofi": {"score": 50, "signal": "BUY", "acceleration": "STABLE"},
        "premarket_high":      29960.0,
        "premarket_low":       29880.0,
        "news_danger_zone":    False,
    }
    _write_jsonl(tmp_path / "snapshots_2026-05-26.jsonl", [
        {"ts_et": "09:46:00", "data": snap},
        {"ts_et": "09:47:00", "data": snap},
    ])
    _write_jsonl(tmp_path / "decisions_2026-05-26.jsonl", [
        {"type": "decision", "ts_et": "09:46:00",
         "decision": {"decision": "HOLD"},
         "pre_filter_reason": "BULL 9 signals [above OR high]"},
    ])

    # Run with no live Claude (default)
    results = backtester.run_backtest(
        date_str="2026-05-26",
        verbose=False,
        use_claude_for_uncached=False,
    )
    assert results["pre_filter_passes"] > 0, \
        f"Expected at least 1 pre-filter pass, got {results['pre_filter_passes']}"


def test_backtest_default_makes_zero_api_calls(tmp_path, monkeypatch):
    """With use_claude_for_uncached=False the API counter must be 0."""
    import backtester
    monkeypatch.setattr(backtester, "DATA_PATH", tmp_path)
    snap = {"last_price": 30000.0, "or_high": 29950.0, "or_low": 29900.0,
            "or_direction": "BULL", "or_relative_volume": 150,
            "mtf_alignment": "BULLISH_ALIGNED", "dom_buy_pressure": 0.65,
            "ofi": {"score": 0, "signal": "NEUTRAL", "acceleration": "STABLE"},
            "premarket_high": 0, "premarket_low": 0, "news_danger_zone": False}
    _write_jsonl(tmp_path / "snapshots_2026-05-26.jsonl",
                 [{"ts_et": "09:46:00", "data": snap}])
    # No decisions JSONL — every pre-filter pass would be uncached.
    results = backtester.run_backtest(
        date_str="2026-05-26",
        verbose=False,
        use_claude_for_uncached=False,
    )
    assert results["api_calls"] == 0


# ── SimExecutor MAX_CONTRACTS semantics ───────────────────────────
def test_sim_executor_respects_one_contract_max():
    from backtester import SimExecutor
    sim = SimExecutor()
    sim.enter(action="BUY",  fill_price=30000.0, stop=29980.0,
              target=30040.0, mode="OB", time_et="09:46", reasoning="first")
    assert sim.position == 1
    # Second entry attempt while in position must be ignored
    sim.enter(action="BUY",  fill_price=30010.0, stop=29990.0,
              target=30050.0, mode="OB", time_et="09:47", reasoning="second")
    # Position still 1, entry_price still the first fill
    assert sim.position    == 1
    assert sim.entry_price == 30000.0


# ── print_report ASCII safety ─────────────────────────────────────
def test_print_report_uses_ascii_separators_only():
    """Earlier versions crashed on cp1252 consoles because the report
    used unicode box-drawing characters."""
    import backtester
    buf = io.StringIO()
    fake_results = {
        "date": "2026-05-26", "elapsed_secs": 1.2, "snapshots": 100,
        "pre_filter_passes": 5, "pre_filter_total": 100,
        "cache_hits": 4, "api_calls": 0, "api_cost_est": 0.0,
        "trade_count": 0, "wins": 0, "losses": 0,
        "daily_pnl": 0.0, "trades": [],
    }
    with contextlib.redirect_stdout(buf):
        backtester.print_report(fake_results)
    out = buf.getvalue()
    # Forbid the unicode box chars that crashed cp1252
    forbidden = ("═", "─", "★")
    for ch in forbidden:
        assert ch not in out, f"print_report still emits {ch!r}"
    # Sanity: ASCII separator should be present
    assert "===" in out
