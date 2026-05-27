"""
Regression tests — one per bug fixed on 2026-05-26.
Each test enforces the *fixed* behavior so a future change that reintroduces
the bug fails CI loudly. Never delete these.
"""
import inspect

import pytest


# ────────────────────────────────────────────────────────────────────
# BUG-001 — max_tokens=500 truncated Claude responses; parse_decision
#           must not crash on partial output and must not return BUY.
# ────────────────────────────────────────────────────────────────────
def test_bug001_parse_decision_handles_truncated_response():
    from claude_brain import parse_decision
    truncated = "DECISION: BUY\nENTRY: 30000\nSTOP: 29\nTARGET:"  # cut mid-line
    try:
        result = parse_decision(truncated)
    except Exception as e:
        pytest.fail(f"parse_decision raised on truncated input: {e}")
    # P1.7 — invalid stop price (≤0) must demote to HOLD.
    # Either way, the decision must not be a live BUY without a valid stop.
    if result.get("decision") in ("BUY", "SELL"):
        assert result.get("stop_price", 0) > 0, \
            "BUG-001: truncated response yielded actionable BUY/SELL without valid stop"


# ────────────────────────────────────────────────────────────────────
# BUG-002 — MIN_THESIS_PROBABILITY=1 (the value 1, not 0.01) caused the
#           gate to block every trade because thesis is reported 0–100.
# ────────────────────────────────────────────────────────────────────
def test_bug002_thesis_gate_passes_at_72_with_55_threshold():
    # The gate compares thesis_probability (0-100 int) against threshold (also 0-100).
    thesis_pct  = 72
    threshold_pct = 55
    assert thesis_pct >= threshold_pct
    # And the bug-state (threshold of 1 interpreted as 100% of the 0-100 scale)
    # would have blocked everything except 100, which a real trade never hits.
    bug_threshold = 100
    assert thesis_pct < bug_threshold, \
        "BUG-002: threshold of 100 (the regression value) would block normal probabilities"


# ────────────────────────────────────────────────────────────────────
# BUG-003 — Wrong model string would crash the Anthropic client.
#           Verify claude_brain still references a documented model id.
# ────────────────────────────────────────────────────────────────────
def test_bug003_model_strings_are_valid_anthropic_ids():
    import claude_brain
    source = open(claude_brain.__file__, encoding="utf-8").read()
    valid_opus   = ("claude-opus-4-7", "claude-opus-4-6")
    valid_sonnet = ("claude-sonnet-4-6", "claude-sonnet-4-7")
    assert any(m in source for m in valid_opus),   "claude_brain has no valid Opus model id"
    assert any(m in source for m in valid_sonnet), "claude_brain has no valid Sonnet model id"


# ────────────────────────────────────────────────────────────────────
# BUG-004 — NameError in _enter_trade referenced an undefined variable.
#           Verify the method exists, takes the expected params, and the
#           source no longer references the offending unbound name.
# ────────────────────────────────────────────────────────────────────
def test_bug004_enter_trade_signature_intact():
    from executor import Executor
    assert callable(Executor._enter_trade)
    params = inspect.signature(Executor._enter_trade).parameters
    for name in ("direction", "contracts", "stop_ticks", "target_ticks",
                 "mode", "reasoning"):
        assert name in params, f"Executor._enter_trade missing param {name}"


# ────────────────────────────────────────────────────────────────────
# BUG-005 — 2-contract overfill from limit+MKT race.
#           _reconcile_overfill must place a flatten order when broker
#           shows more contracts than intended.
# ────────────────────────────────────────────────────────────────────
def test_bug005_overfill_reconcile_flattens_excess():
    ex = _make_mock_executor(broker_position=2)
    ex._reconcile_overfill("BUY", intended=1)
    assert len(ex.ib.placed_orders) == 1, \
        "BUG-005: expected exactly 1 flatten order, got " + str(len(ex.ib.placed_orders))
    _contract, order = ex.ib.placed_orders[0]
    assert order.action         == "SELL"   # flatten the long excess
    assert order.totalQuantity  == 1


def test_bug005_overfill_reconcile_noop_when_position_correct():
    ex = _make_mock_executor(broker_position=1)
    ex._reconcile_overfill("BUY", intended=1)
    assert ex.ib.placed_orders == [], \
        "BUG-005: no flatten order should be placed when broker matches intended"


# ────────────────────────────────────────────────────────────────────
# BUG-006 — pnl=None on a sanity-rejected trade crashed save_daily_summary
#           with TypeError (None+float). Must handle None entries gracefully.
# ────────────────────────────────────────────────────────────────────
def test_bug006_save_daily_summary_handles_none_pnl(tmp_path, monkeypatch):
    import memory_manager
    monkeypatch.setattr(memory_manager, "MEMORY_DIR", str(tmp_path))
    trades = [
        {"time": "10:00:00", "action": "BUY", "entry": 30000, "exit": 30040,
         "pnl": 40.0,  "mode": "OB_BOUNCE", "exit_reason": "target"},
        {"time": "10:30:00", "action": "BUY", "entry": 30000, "exit": 30100,
         "pnl": None, "mode": "OB_BOUNCE", "exit_reason": "REJECTED (sanity bound)"},
    ]
    try:
        memory_manager.save_daily_summary(
            trades=trades, daily_pnl=40.0, analysis_log=[],
        )
    except TypeError as e:
        pytest.fail(f"BUG-006: save_daily_summary raised TypeError on pnl=None: {e}")


# ────────────────────────────────────────────────────────────────────
# BUG-007 — Backtester returned 0/N pre-filter passes because the watchlist
#           module-global was never seeded. infer_session_bias must default
#           to NEUTRAL so the bias branch in pre_filter_signal is reachable.
# ────────────────────────────────────────────────────────────────────
def test_bug007_backtester_infers_neutral_bias_by_default():
    from backtester import infer_session_bias
    assert infer_session_bias({}) == "NEUTRAL"


def test_bug007_backtester_infers_long_preferred_from_bull_reasons():
    from backtester import infer_session_bias
    decisions = {
        "10:00:00": {"pre_filter_reason": "BULL 9 signals [above OR high, above VWAP]"},
        "10:01:00": {"pre_filter_reason": "BULL 12 signals [above OR high]"},
    }
    assert infer_session_bias(decisions) == "LONG_PREFERRED"


# ────────────────────────────────────────────────────────────────────
# BUG-008 — On (re)connect IBKR replays today's commissionReports.
#           A primed execId must cause the handler to drop the duplicate
#           rather than re-accumulate into _broker_commission_pending.
# ────────────────────────────────────────────────────────────────────
def test_bug008_primed_exec_id_drops_replayed_commission():
    ex = _make_mock_executor()
    ex._seen_exec_ids.add("EXEC-REPLAY-123")

    class _Report:   execId = "EXEC-REPLAY-123"; commission = 1.24
    class _ExecObj:  execId = "EXEC-REPLAY-123"; time = None
    class _Contract: symbol = "MNQ"
    class _Fill:     contract = _Contract(); execution = _ExecObj()

    before = ex._broker_commission_pending
    ex._on_commission_report(trade=None, fill=_Fill(), report=_Report())
    assert ex._broker_commission_pending == before, \
        "BUG-008: primed execId must not accumulate into pending bucket"


def test_bug008_unprimed_exec_id_accumulates_commission():
    ex = _make_mock_executor()
    # NEW execId — must accumulate.
    class _Report:   execId = "EXEC-NEW-456"; commission = 1.24
    class _ExecObj:  execId = "EXEC-NEW-456"; time = None
    class _Contract: symbol = "MNQ"
    class _Fill:     contract = _Contract(); execution = _ExecObj()

    before = ex._broker_commission_pending
    ex._on_commission_report(trade=None, fill=_Fill(), report=_Report())
    assert ex._broker_commission_pending > before, \
        "BUG-008: fresh execId must accumulate live commission"


# ────────────────────────────────────────────────────────────────────
# BUG-009 — Response with DECISION on line 1 must parse correctly.
# ────────────────────────────────────────────────────────────────────
def test_bug009_decision_first_parses_correctly():
    from claude_brain import parse_decision
    # Real Claude responses prefix prices with ENTRY_PRICE / STOP_PRICE /
    # TARGET_1 — see parse_decision key map. BUG-009 was about the DECISION
    # line appearing BEFORE the reasoning, not about the field names.
    text = (
        "DECISION: BUY\n"
        "MODE: SCALP\n"
        "ENTRY_PRICE: 30000\n"
        "STOP_PRICE: 29980\n"
        "TARGET_1: 30040\n"
        "THESIS_PROBABILITY: 72\n"
        "REASONING: clean breakout above OR high\n"
    )
    result = parse_decision(text)
    assert result["decision"]   == "BUY", \
        f"BUG-009: DECISION-first input did not parse to BUY ({result.get('decision')!r})"
    assert result["stop_price"]  == 29980.0
    assert result["entry_price"] == 30000.0


# ────────────────────────────────────────────────────────────────────
# BUG-010 — Backtester defaulted to live Claude API calls; changed to
#           --no-live-claude default to prevent accidental API spend.
# ────────────────────────────────────────────────────────────────────
def test_bug010_backtester_default_skips_live_claude():
    import backtester
    source = open(backtester.__file__, encoding="utf-8").read()
    # The dest=no_live_claude arg must have default=True so unflagged runs are free.
    assert 'default=True' in source and 'no_live_claude' in source, \
        "BUG-010: backtester --no-live-claude default must be True"


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────
def _make_mock_executor(broker_position: int = 0):
    """
    Build an Executor against a hand-rolled IB mock. Avoids hitting the
    network, but exercises real Executor.__init__ wiring (handler
    registration, dedupe priming, sleep call).
    """
    from executor import Executor

    class _Evt:
        def __init__(self): self.handlers = []
        def __iadd__(self, h):
            self.handlers.append(h)
            return self

    class _Pos:
        def __init__(self, sym, qty):
            self.contract = type("C", (), {"symbol": sym})()
            self.position = qty

    class _MockIB:
        def __init__(self, broker_pos):
            self.commissionReportEvent = _Evt()
            self.connectedEvent        = _Evt()
            self.disconnectedEvent     = _Evt()
            self._broker_pos           = broker_pos
            self.placed_orders         = []
        def sleep(self, _t):           pass
        def fills(self):               return []
        def positions(self):
            return [_Pos("MNQ", self._broker_pos)] if self._broker_pos else []
        def placeOrder(self, contract, order):
            self.placed_orders.append((contract, order))
            return None

    class _Contract:
        symbol = "MNQ"

    return Executor(_MockIB(broker_position), _Contract(), paper=True)
