"""
Phase 4 — Stress tests: many trades, many commission events, many
snapshots, big parse_decision input. Verifies no quadratic blowups,
no leaks in commission state, no parser pathologies.

Iteration counts are modest (a few hundred at most) to keep the suite
fast on CI; the point is to verify the loops don't crash, not to be
a perf benchmark.
"""
import time
import types

import pytest


class _Evt:
    def __init__(self): self.handlers = []
    def __iadd__(self, h): self.handlers.append(h); return self


class _MockIB:
    def __init__(self):
        self.commissionReportEvent = _Evt()
        self.connectedEvent        = _Evt()
        self.disconnectedEvent     = _Evt()
    def sleep(self, _): pass
    def fills(self): return []
    def positions(self): return []
    def placeOrder(self, *a, **kw): return None


def _make_executor():
    from executor import Executor
    return Executor(_MockIB(), types.SimpleNamespace(symbol="MNQ"), paper=True)


# ── Many trades ────────────────────────────────────────────────────
def test_500_record_pnl_calls_do_not_crash():
    ex = _make_executor()
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 29980.0
    for i in range(500):
        # Alternate win/loss to exercise both branches.
        exit_price = 30040.0 if i % 2 == 0 else 29980.0
        ex._record_pnl(
            entry_price=30000.0, exit_price=exit_price,
            contracts=1, was_long=True, reason="stress",
        )
    assert len(ex.trades_today) == 500


# ── Many commission events ────────────────────────────────────────
def test_2000_commission_events_no_state_corruption():
    """Each new execId accumulates exactly once; the second event for the
    same id is dedupe-dropped. After the loop the pending bucket equals
    the count of UNIQUE accumulated execIds × commission."""
    ex = _make_executor()
    N = 2000
    for i in range(N):
        report = types.SimpleNamespace(execId=f"E-{i}", commission=1.00)
        fill_obj = types.SimpleNamespace(
            execution=types.SimpleNamespace(execId=f"E-{i}", time=None),
            contract=types.SimpleNamespace(symbol="MNQ"),
        )
        ex._on_commission_report(trade=None, fill=fill_obj, report=report)
    assert ex._broker_commission_pending == pytest.approx(float(N))
    # Second pass — every execId now in dedupe set, so pending should not move.
    pending_before = ex._broker_commission_pending
    for i in range(N):
        report = types.SimpleNamespace(execId=f"E-{i}", commission=1.00)
        fill_obj = types.SimpleNamespace(
            execution=types.SimpleNamespace(execId=f"E-{i}", time=None),
            contract=types.SimpleNamespace(symbol="MNQ"),
        )
        ex._on_commission_report(trade=None, fill=fill_obj, report=report)
    assert ex._broker_commission_pending == pending_before


# ── Many pre-filter snapshots ─────────────────────────────────────
def test_1000_pre_filter_calls_no_blowup():
    """Stable behavior + reasonable speed. Cap at ~5s to catch quadratic
    blowups without making this test flaky on slow CI."""
    import claude_brain
    from claude_brain import pre_filter_signal
    claude_brain._session_watchlist = {"bias": "NEUTRAL", "bias_invalidated": False}
    snap = {
        "or_direction": "BULL", "or_relative_volume": 120,
        "last_price": 30000.0, "vwap": 29950.0, "or_high": 29950.0,
        "or_low": 29900.0, "cumulative_delta": 50,
        "mtf_alignment": "BULLISH_ALIGNED",
        "ofi": {"score": 0, "signal": "NEUTRAL", "acceleration": "STABLE"},
        "news_danger_zone": False, "premarket_high": 0, "premarket_low": 0,
    }
    t0 = time.time()
    for _ in range(1000):
        worth, reason = pre_filter_signal(snap)
    elapsed = time.time() - t0
    assert elapsed < 5.0, f"1000 pre_filter calls took {elapsed:.2f}s — likely quadratic"


# ── Big parse_decision input ──────────────────────────────────────
def test_parse_decision_handles_50kb_input():
    """A 50KB blob with one DECISION line and a lot of REASONING text."""
    from claude_brain import parse_decision
    big_reasoning = "x" * (50 * 1024)  # 50KB
    text = (
        "DECISION: BUY\n"
        "ENTRY_PRICE: 30000\n"
        "STOP_PRICE: 29980\n"
        "TARGET_1: 30040\n"
        "THESIS_PROBABILITY: 72\n"
        f"REASONING: {big_reasoning}\n"
    )
    t0 = time.time()
    d = parse_decision(text)
    elapsed = time.time() - t0
    assert d["decision"] == "BUY"
    assert elapsed < 2.0, f"50KB parse took {elapsed:.2f}s"
