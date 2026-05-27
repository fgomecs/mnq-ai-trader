"""Smoke tests: project imports cleanly, core symbols exist, dashboard writes work."""
import importlib
import json
import os
import tempfile

import pytest


def test_config_imports_and_version_set():
    cfg = importlib.import_module("config")
    assert isinstance(cfg.VERSION, str) and cfg.VERSION
    assert cfg.MAX_CONTRACTS >= 1
    assert cfg.MAX_DAILY_LOSS_USD > 0


def test_config_reads_max_contracts_from_env(monkeypatch):
    """MAX_CONTRACTS is env-overridable; reload picks up the new value."""
    import config
    monkeypatch.setenv("MAX_CONTRACTS", "3")
    importlib.reload(config)
    try:
        assert config.MAX_CONTRACTS == 3
    finally:
        monkeypatch.delenv("MAX_CONTRACTS", raising=False)
        importlib.reload(config)   # restore for downstream tests


def test_config_reads_claude_entry_model_from_env(monkeypatch):
    """CLAUDE_ENTRY_MODEL is env-overridable so we can swap Opus versions."""
    import config
    monkeypatch.setenv("CLAUDE_ENTRY_MODEL", "claude-opus-test-stub")
    importlib.reload(config)
    try:
        assert config.CLAUDE_ENTRY_MODEL == "claude-opus-test-stub"
    finally:
        monkeypatch.delenv("CLAUDE_ENTRY_MODEL", raising=False)
        importlib.reload(config)


def test_claude_brain_imports_and_exports():
    mod = importlib.import_module("claude_brain")
    for name in ("pre_filter_signal", "parse_decision", "analyze_market",
                 "analyze_position", "update_watchlist", "get_watchlist"):
        assert hasattr(mod, name), f"claude_brain missing {name}"


def test_executor_class_present():
    from executor import Executor
    # Constructor signature: (ib_instance, contract, paper=True)
    import inspect
    sig = inspect.signature(Executor.__init__)
    assert "ib_instance" in sig.parameters
    assert "contract"    in sig.parameters


def test_data_recorder_singleton_has_record_methods():
    from data_recorder import recorder
    for name in ("record_snapshot", "record_decision", "record_trade"):
        assert hasattr(recorder, name), f"recorder missing {name}"


def test_dashboard_writer_imports():
    mod = importlib.import_module("dashboard_writer")
    assert hasattr(mod, "update_dashboard")
    assert hasattr(mod, "update_price_only")


def test_journal_exporter_builds_empty_journal():
    from journal_exporter import build_journal
    j = build_journal(starting_balance=50_000.0, account_name="TEST")
    # Empty data path still produces the documented top-level shape.
    for key in ("equity_curve", "by_strategy", "by_hour", "trades",
                "ofi_performance", "thesis_buckets", "commission_sources",
                "avg_rr", "overall_win_rate", "profitability_zone"):
        assert key in j, f"journal output missing {key}"


def test_required_feature_flags_present():
    import config
    expected = [
        "FEATURE_NEWS_GATE", "FEATURE_DEAD_ZONE", "FEATURE_DUAL_TRAIL",
        "FEATURE_EARLY_EXIT", "FEATURE_LEARNING_EOD",
        "FEATURE_SESSION_CLASSIFIER", "FEATURE_OFI",
    ]
    for f in expected:
        assert hasattr(config, f), f"missing feature flag {f}"


def test_dashboard_update_price_only_writes_json(tmp_path, monkeypatch):
    """update_price_only writes a parseable JSON file at PRICE_FILE."""
    import dashboard_writer
    target = tmp_path / "price_data.json"
    monkeypatch.setattr(dashboard_writer, "PRICE_FILE", str(target))

    dashboard_writer.update_price_only(
        price=30000.0, bid=29999.75, ask=30000.25, volume=100,
        position=0, entry_price=None, stop_price=None, target_price=None,
        daily_pnl=12.34,
    )
    assert target.exists(), "update_price_only did not write PRICE_FILE"
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["pnl"] == 12.34
    assert data["position"] == "FLAT"
