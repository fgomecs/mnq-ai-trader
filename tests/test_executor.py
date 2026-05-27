"""
Phase 2 — Executor unit tests covering _record_pnl, _reconcile_overfill,
commission dedupe + priming, mark_disconnect, and outage-window handling.
"""
import datetime
import time
import types

import pytest


# ── Shared mock harness ────────────────────────────────────────────
class _Evt:
    def __init__(self): self.handlers = []
    def __iadd__(self, h):
        self.handlers.append(h)
        return self


class _Pos:
    def __init__(self, sym, qty):
        self.contract = types.SimpleNamespace(symbol=sym)
        self.position = qty


class _ExecObj:
    def __init__(self, exec_id, ts=None, side="BOT"):
        self.execId = exec_id
        self.time   = ts
        self.side   = side
        self.price  = 30000.0


class _Fill:
    def __init__(self, exec_id, ts=None, sym="MNQ", side="BOT"):
        self.execution = _ExecObj(exec_id, ts, side)
        self.contract  = types.SimpleNamespace(symbol=sym)


class _MockIB:
    def __init__(self, broker_pos=0, fills_list=None):
        self.commissionReportEvent = _Evt()
        self.connectedEvent        = _Evt()
        self.disconnectedEvent     = _Evt()
        self._broker_pos           = broker_pos
        self._fills                = fills_list or []
        self.placed_orders         = []
    def sleep(self, _t):           pass
    def fills(self):               return list(self._fills)
    def positions(self):
        return [_Pos("MNQ", self._broker_pos)] if self._broker_pos else []
    def placeOrder(self, contract, order):
        self.placed_orders.append((contract, order))


def _make_executor(broker_pos=0, fills_list=None):
    from executor import Executor
    ib = _MockIB(broker_pos=broker_pos, fills_list=fills_list)
    contract = types.SimpleNamespace(symbol="MNQ")
    ex = Executor(ib, contract, paper=True)
    return ex


# ── _record_pnl ────────────────────────────────────────────────────
def test_record_pnl_long_win():
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 29980.0
    pnl = ex._record_pnl(
        entry_price=30000.0, exit_price=30040.0,
        contracts=1, was_long=True, reason="target",
    )
    # 40 points * 4 ticks/point * $0.50/tick = $80 (gross)
    # no broker commission attached in this mock, so net = gross
    assert pnl == pytest.approx(80.0)
    assert ex.daily_pnl == pytest.approx(80.0)


def test_record_pnl_long_loss():
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 29980.0
    pnl = ex._record_pnl(
        entry_price=30000.0, exit_price=29980.0,
        contracts=1, was_long=True, reason="stop",
    )
    assert pnl == pytest.approx(-40.0)
    assert ex.consecutive_losses == 1


def test_record_pnl_short_win():
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 30020.0
    pnl = ex._record_pnl(
        entry_price=30000.0, exit_price=29960.0,
        contracts=1, was_long=False, reason="target",
    )
    assert pnl == pytest.approx(80.0)


def test_record_pnl_short_loss():
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 30020.0
    pnl = ex._record_pnl(
        entry_price=30000.0, exit_price=30020.0,
        contracts=1, was_long=False, reason="stop",
    )
    assert pnl == pytest.approx(-40.0)
    assert ex.consecutive_losses == 1


def test_record_pnl_sanity_reject_returns_zero():
    """When the raw pnl exceeds MAX_REASONABLE_PNL_PER_CONTRACT (default $1000),
    the trade is rejected as state-corrupted: pnl=None recorded, return 0.0."""
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 29000.0
    ex.stop_price      = 28980.0
    pnl = ex._record_pnl(
        entry_price=29000.0, exit_price=31000.0,   # +2000 pts = $4000 = absurd
        contracts=1, was_long=True, reason="bogus",
    )
    assert pnl == 0.0
    # Rejected trade still gets appended with pnl=None
    last = ex.trades_today[-1]
    assert last["pnl"] is None
    assert "REJECTED" in last["exit_reason"]


def test_record_pnl_commission_deducted_from_pnl():
    """A pending broker commission is consumed and subtracted from pnl."""
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 29980.0
    ex._broker_commission_pending = 2.50
    pnl = ex._record_pnl(
        entry_price=30000.0, exit_price=30040.0,
        contracts=1, was_long=True, reason="target",
    )
    assert pnl == pytest.approx(80.0 - 2.50)
    assert ex.trades_today[-1]["commission"] == pytest.approx(2.50)
    assert ex.trades_today[-1]["commission_source"] == "broker"
    # Pending bucket was drained
    assert ex._broker_commission_pending == 0.0


def test_record_pnl_appends_to_trades_today():
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 29980.0
    ex.trade_mode      = "OB_BOUNCE"
    before = len(ex.trades_today)
    ex._record_pnl(
        entry_price=30000.0, exit_price=30040.0,
        contracts=1, was_long=True, reason="target",
    )
    assert len(ex.trades_today) == before + 1
    t = ex.trades_today[-1]
    assert t["mode"] == "OB_BOUNCE"
    assert t["exit_reason"] == "target"


# ── _reconcile_overfill ────────────────────────────────────────────
def test_reconcile_overfill_noop_when_correct():
    ex = _make_executor(broker_pos=1)
    ex._reconcile_overfill("BUY", intended=1)
    assert ex.ib.placed_orders == []


def test_reconcile_overfill_long_excess_flattens():
    ex = _make_executor(broker_pos=2)
    ex._reconcile_overfill("BUY", intended=1)
    assert len(ex.ib.placed_orders) == 1
    _c, order = ex.ib.placed_orders[0]
    assert order.action == "SELL"
    assert order.totalQuantity == 1


def test_reconcile_overfill_short_excess_flattens():
    """Broker at -2 (double-short), intended -1 → buy back 1 to flatten."""
    ex = _make_executor(broker_pos=-2)
    ex._reconcile_overfill("SELL", intended=1)
    assert len(ex.ib.placed_orders) == 1
    _c, order = ex.ib.placed_orders[0]
    assert order.action == "BUY"
    assert order.totalQuantity == 1


# ── commission dedupe ─────────────────────────────────────────────
def test_commission_dedupe_replayed_exec_id_dropped():
    ex = _make_executor()
    ex._seen_exec_ids.add("REPLAY-1")

    report = types.SimpleNamespace(execId="REPLAY-1", commission=1.24)
    fill   = _Fill("REPLAY-1")

    before = ex._broker_commission_pending
    ex._on_commission_report(trade=None, fill=fill, report=report)
    assert ex._broker_commission_pending == before


def test_commission_dedupe_fresh_exec_id_accumulates():
    ex = _make_executor()
    report = types.SimpleNamespace(execId="FRESH-2", commission=1.24)
    fill   = _Fill("FRESH-2")

    ex._on_commission_report(trade=None, fill=fill, report=report)
    assert ex._broker_commission_pending == pytest.approx(1.24)
    assert "FRESH-2" in ex._seen_exec_ids


# ── reprime / mark_disconnect ─────────────────────────────────────
def test_reprime_seen_exec_ids_adds_new_fills():
    """After connect-event reprime, ib.fills() execIds must populate the set."""
    ex = _make_executor()
    # Simulate a new fill arriving in ib.fills() since boot.
    ex.ib._fills = [_Fill("POST-RECONNECT-1"), _Fill("POST-RECONNECT-2")]
    ex.reprime_seen_exec_ids()
    assert "POST-RECONNECT-1" in ex._seen_exec_ids
    assert "POST-RECONNECT-2" in ex._seen_exec_ids


def test_mark_disconnect_records_utc_timestamp():
    ex = _make_executor()
    assert ex._last_disconnect_ts is None
    ex.mark_disconnect()
    assert isinstance(ex._last_disconnect_ts, datetime.datetime)
    assert ex._last_disconnect_ts.tzinfo is not None
    delta = (datetime.datetime.now(datetime.timezone.utc) - ex._last_disconnect_ts).total_seconds()
    assert 0 <= delta < 5, f"timestamp not recent enough: delta={delta}s"


def test_outage_window_fills_not_primed():
    """Fills whose execution.time is at or after the disconnect cutoff must NOT
    be added to the dedupe set — their commissionReports need to land."""
    ex = _make_executor()
    now = datetime.datetime.now(datetime.timezone.utc)
    historical = _Fill("HIST-1",   ts=now - datetime.timedelta(minutes=5))
    outage     = _Fill("OUTAGE-2", ts=now + datetime.timedelta(seconds=10))
    ex.ib._fills = [historical, outage]
    ex.mark_disconnect()
    ex.reprime_seen_exec_ids()
    assert "HIST-1"   in ex._seen_exec_ids
    assert "OUTAGE-2" not in ex._seen_exec_ids, \
        "outage-window fill must be left out of the dedupe set"
    # Cutoff consumed
    assert ex._last_disconnect_ts is None
