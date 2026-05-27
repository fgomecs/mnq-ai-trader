"""
Regression: max-loss display must reflect ACCOUNT_SIZE × MAX_DAILY_LOSS_PCT
($10,000 on the default $50k account), not the historical $500 placeholder.
"""
import importlib
import json
import inspect

import pytest


def test_dashboard_writer_default_uses_max_daily_loss_usd():
    """update_dashboard's max_loss default must come from config, not 500.0."""
    import dashboard_writer
    importlib.reload(dashboard_writer)
    sig = inspect.signature(dashboard_writer.update_dashboard)
    default = sig.parameters["max_loss"].default
    import config
    assert default == config.MAX_DAILY_LOSS_USD, \
        f"max_loss default {default} should equal MAX_DAILY_LOSS_USD {config.MAX_DAILY_LOSS_USD}"
    assert default > 500.0, "default must not regress to the $500 placeholder"


def test_dashboard_writes_max_loss_into_json(tmp_path, monkeypatch):
    """When update_dashboard is called without an explicit max_loss, the
    value written to dashboard_data.json must be the env-driven cap."""
    import dashboard_writer as dw
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))
    dw.update_dashboard(
        position=0, current_price=30000.0, daily_pnl=0.0,
        trades=[], last_decision="HOLD", last_reasoning="x",
        snapshot={"last_price": 30000.0},
    )
    data = json.loads(target.read_text(encoding="utf-8"))
    import config
    assert data["maxLoss"] == config.MAX_DAILY_LOSS_USD
    assert data["maxLoss"] >= 10_000.0, \
        f"maxLoss in dashboard JSON regressed to {data['maxLoss']}"


def test_mobile_html_no_longer_hardcodes_500():
    """The Loss Remaining placeholder must no longer say $500."""
    text = open(r"C:\trading\mnq-ai-trader\mobile.html", encoding="utf-8").read()
    assert 'id="risk-loss">$500<' not in text, \
        "mobile.html still hardcodes $500 in #risk-loss"
    # JS fallback should be at least $10,000 (the default cap)
    assert "parseFloat(d.maxLoss) || 500" not in text
    assert "parseFloat(d.maxLoss) || 10000" in text
