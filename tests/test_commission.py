"""
Phase 3 — End-to-end commission capture flow.
Covers the full pipeline from commissionReport handler through
_record_pnl into the trades_today row and the dashboard sum.
"""
import datetime
import time
import types

import pytest


class _Evt:
    def __init__(self): self.handlers = []
    def __iadd__(self, h): self.handlers.append(h); return self


class _ExecObj:
    def __init__(self, exec_id, ts=None):
        self.execId = exec_id
        self.time   = ts
        self.side   = "BOT"
        self.price  = 30000.0


class _Fill:
    def __init__(self, exec_id, ts=None, sym="MNQ"):
        self.execution = _ExecObj(exec_id, ts)
        self.contract  = types.SimpleNamespace(symbol=sym)


class _MockIB:
    def __init__(self, fills_list=None):
        self.commissionReportEvent = _Evt()
        self.connectedEvent        = _Evt()
        self.disconnectedEvent     = _Evt()
        self._fills                = fills_list or []
        self.placed_orders         = []
    def sleep(self, _t):           pass
    def fills(self):               return list(self._fills)
    def positions(self):           return []
    def placeOrder(self, c, o):    self.placed_orders.append((c, o))


def _make_executor(fills_list=None):
    from executor import Executor
    return Executor(_MockIB(fills_list), types.SimpleNamespace(symbol="MNQ"), paper=True)


# ── Handler behavior ───────────────────────────────────────────────
def test_commission_accumulates_on_new_exec_id():
    ex = _make_executor()
    rpt = types.SimpleNamespace(execId="NEW-A", commission=1.24)
    ex._on_commission_report(trade=None, fill=_Fill("NEW-A"), report=rpt)
    assert ex._broker_commission_pending == pytest.approx(1.24)
    assert ex._broker_commission_session == pytest.approx(1.24)


def test_replayed_exec_id_ignored_by_handler():
    ex = _make_executor()
    ex._seen_exec_ids.add("REPLAY-B")
    rpt = types.SimpleNamespace(execId="REPLAY-B", commission=1.24)
    ex._on_commission_report(trade=None, fill=_Fill("REPLAY-B"), report=rpt)
    assert ex._broker_commission_pending == 0.0


# ── End-to-end into trade row ─────────────────────────────────────
def test_record_pnl_drains_pending_into_trade_row():
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 29980.0
    ex._broker_commission_pending = 1.24
    pnl = ex._record_pnl(
        entry_price=30000.0, exit_price=30040.0,
        contracts=1, was_long=True, reason="target",
    )
    # 40 pt * $2/pt = $80 gross, minus $1.24 commission = $78.76
    assert pnl == pytest.approx(78.76)
    assert ex._broker_commission_pending == 0.0
    assert ex.trades_today[-1]["commission"] == pytest.approx(1.24)


def test_commission_source_broker_when_commission_present():
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 29980.0
    ex._broker_commission_pending = 1.24
    ex._record_pnl(
        entry_price=30000.0, exit_price=30040.0,
        contracts=1, was_long=True, reason="target",
    )
    assert ex.trades_today[-1]["commission_source"] == "broker"


def test_commission_source_none_when_no_commission_received():
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 29980.0
    # Pending bucket already empty
    ex._record_pnl(
        entry_price=30000.0, exit_price=30040.0,
        contracts=1, was_long=True, reason="target",
    )
    assert ex.trades_today[-1]["commission"]        == 0.0
    assert ex.trades_today[-1]["commission_source"] == "none"


# ── Dashboard aggregation ─────────────────────────────────────────
def test_daily_commissions_sum_matches_trades(tmp_path, monkeypatch):
    """dailyCommissions emitted by dashboard_writer must equal the sum of
    individual trade.commission values."""
    import dashboard_writer as dw
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))
    trades = [
        {"time": "10:00", "pnl": 40.0,  "commission": 1.24, "commission_source": "broker"},
        {"time": "10:30", "pnl": -20.0, "commission": 1.24, "commission_source": "broker"},
        {"time": "11:00", "pnl": 60.0,  "commission": 0.62, "commission_source": "broker"},
    ]
    dw.update_dashboard(
        position=0, current_price=30000.0, daily_pnl=80.0,
        max_loss=10_000.0, trades=trades,
        last_decision="HOLD", last_reasoning="x",
        snapshot={"last_price": 30000.0},
    )
    import json
    data = json.loads(target.read_text(encoding="utf-8"))
    individual_sum = sum(t["commission"] for t in trades)
    assert data["dailyCommissions"] == pytest.approx(individual_sum)
    assert data["dailyCommissions"] == pytest.approx(3.10)


# ── Reconnect handling ────────────────────────────────────────────
def test_mark_disconnect_records_timestamp():
    ex = _make_executor()
    assert ex._last_disconnect_ts is None
    ex.mark_disconnect()
    assert isinstance(ex._last_disconnect_ts, datetime.datetime)
    assert ex._last_disconnect_ts.tzinfo is not None


def test_reprime_preserves_outage_window_fills():
    ex = _make_executor()
    now = datetime.datetime.now(datetime.timezone.utc)
    historical = _Fill("HIST-OK",   ts=now - datetime.timedelta(minutes=5))
    outage     = _Fill("OUTAGE-FILL", ts=now + datetime.timedelta(seconds=10))
    ex.ib._fills = [historical, outage]
    ex.mark_disconnect()
    ex.reprime_seen_exec_ids()
    assert "HIST-OK"     in ex._seen_exec_ids
    assert "OUTAGE-FILL" not in ex._seen_exec_ids
