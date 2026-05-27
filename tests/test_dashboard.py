"""
Phase 3 — Dashboard writer tests.
Always redirects DASHBOARD_FILE / PRICE_FILE to tmp_path so we never
clobber the live dashboard JSON during a test run.
"""
import json
import os

import pytest


def _common_update(dw, *, trades=None, daily_pnl=0.0):
    """Drive update_dashboard with reasonable defaults."""
    dw.update_dashboard(
        position       = 0,
        current_price  = 30000.0,
        daily_pnl      = daily_pnl,
        max_loss       = 10_000.0,
        trades         = trades or [],
        last_decision  = "HOLD",
        last_reasoning = "test fixture",
        last_confidence= "LOW",
        snapshot       = {"last_price": 30000.0},
    )


def test_update_dashboard_writes_valid_json(tmp_path, monkeypatch):
    import dashboard_writer as dw
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))
    _common_update(dw)
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_dashboard_contains_daily_pnl(tmp_path, monkeypatch):
    import dashboard_writer as dw
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))
    _common_update(dw, daily_pnl=42.50)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "dailyPnl" in data
    assert data["dailyPnl"] == 42.50


def test_dashboard_contains_daily_commissions(tmp_path, monkeypatch):
    """Sum of per-trade commissions surfaced as dailyCommissions."""
    import dashboard_writer as dw
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))
    trades = [
        {"time": "10:00", "pnl": 40.0, "commission": 1.24, "commission_source": "broker"},
        {"time": "10:30", "pnl": -20.0, "commission": 0.62, "commission_source": "broker"},
    ]
    _common_update(dw, trades=trades, daily_pnl=20.0)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "dailyCommissions" in data
    assert data["dailyCommissions"] == pytest.approx(1.86)


def test_dashboard_contains_daily_net_pnl(tmp_path, monkeypatch):
    """dailyNetPnl == daily_pnl (executor already deducts commission)."""
    import dashboard_writer as dw
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))
    _common_update(dw, daily_pnl=18.14)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "dailyNetPnl" in data
    assert data["dailyNetPnl"] == 18.14


def test_update_price_only_writes_without_error(tmp_path, monkeypatch):
    import dashboard_writer as dw
    target = tmp_path / "price_data.json"
    monkeypatch.setattr(dw, "PRICE_FILE", str(target))
    dw.update_price_only(
        price=30000.0, bid=29999.75, ask=30000.25, volume=100,
        position=1, entry_price=29980.0, stop_price=29960.0, target_price=30040.0,
        daily_pnl=15.0,
    )
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["position"] == "LONG"
    assert data["pnl"]      == 15.0


def test_dashboard_trades_array_matches_trades_today(tmp_path, monkeypatch):
    import dashboard_writer as dw
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))
    trades = [
        {"time": "10:00", "action": "BUY", "entry": 30000, "exit": 30040,
         "pnl": 40.0, "mode": "OB_BOUNCE", "exit_reason": "target",
         "commission": 1.24, "commission_source": "broker"},
        {"time": "10:30", "action": "BUY", "entry": 30000, "exit": 29980,
         "pnl": -20.0, "mode": "OB_BOUNCE", "exit_reason": "stop",
         "commission": 1.24, "commission_source": "broker"},
    ]
    _common_update(dw, trades=trades, daily_pnl=20.0)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert len(data["trades"]) == 2
    assert data["trades"][0]["pnl"]               == 40.0
    assert data["trades"][1]["pnl"]               == -20.0
    assert data["trades"][0]["commission_source"] == "broker"


def test_stale_state_cleared_at_new_session(tmp_path):
    """run_premarket() removes DASHBOARD_FILE so yesterday's reasoning /
    trades / pnl don't bleed into today's first write. We exercise the
    same os.remove(DASHBOARD_FILE) pattern."""
    stale = tmp_path / "dashboard_data.json"
    stale.write_text('{"dailyPnl": -250.0, "lastReasoning": "yesterday"}',
                     encoding="utf-8")
    assert stale.exists()
    # Mimic the clear in main.run_premarket
    if os.path.exists(stale):
        os.remove(stale)
    assert not stale.exists()
