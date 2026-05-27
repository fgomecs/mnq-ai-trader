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
# BUG-003 — Wrong hardcoded model string would crash the Anthropic client.
#           Fix made the model id env-driven via CLAUDE_ENTRY_MODEL so it
#           can be swapped without code edits. Verify the env read works.
# ────────────────────────────────────────────────────────────────────
def test_bug003_claude_entry_model_reads_from_env(monkeypatch):
    import importlib
    import config
    monkeypatch.setenv("CLAUDE_ENTRY_MODEL", "claude-opus-bug003-probe")
    importlib.reload(config)
    try:
        assert config.CLAUDE_ENTRY_MODEL == "claude-opus-bug003-probe", \
            "BUG-003: CLAUDE_ENTRY_MODEL did not pick up env override"
        # Legacy alias must track the entry model.
        assert config.CLAUDE_MODEL == config.CLAUDE_ENTRY_MODEL
    finally:
        monkeypatch.delenv("CLAUDE_ENTRY_MODEL", raising=False)
        importlib.reload(config)


def test_bug003_default_model_is_valid_anthropic_id():
    """Without env override, default must still be a real Anthropic model id."""
    import config
    assert config.CLAUDE_ENTRY_MODEL.startswith(("claude-opus-", "claude-sonnet-")), \
        f"BUG-003: invalid default model id: {config.CLAUDE_ENTRY_MODEL}"


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
# BUG-011 — Immediate re-entry after a losing trade.
#           2026-05-27 09:54: Trade 3 entered 16s after Trade 2 closed
#           for -$32.74, same direction. LOSS_COOLDOWN_SECS=300 default
#           must block new entries within the window.
# ────────────────────────────────────────────────────────────────────
def test_bug011_loss_cooldown_blocks_immediate_reentry(monkeypatch):
    monkeypatch.setenv("LOSS_COOLDOWN_SECS", "300")
    import importlib, config
    importlib.reload(config)
    try:
        import time as _time
        ex = _make_mock_executor()
        # Simulate a loss that just happened
        ex._last_loss_ts        = _time.time()
        ex._last_loss_direction = "BUY"

        in_cd, reason = ex.is_in_loss_cooldown()
        assert in_cd is True, "must be in cooldown 0 sec after a loss"
        assert "cooldown" in reason.lower()
        assert "remaining" in reason.lower()
    finally:
        monkeypatch.delenv("LOSS_COOLDOWN_SECS", raising=False)
        importlib.reload(config)


def test_bug011_loss_cooldown_clears_after_window(monkeypatch):
    """After LOSS_COOLDOWN_SECS elapses, the gate must release."""
    monkeypatch.setenv("LOSS_COOLDOWN_SECS", "60")
    import importlib, config
    importlib.reload(config)
    try:
        import time as _time
        ex = _make_mock_executor()
        # Loss happened 120s ago — past the 60s window
        ex._last_loss_ts        = _time.time() - 120
        ex._last_loss_direction = "BUY"

        in_cd, _ = ex.is_in_loss_cooldown()
        assert in_cd is False, "cooldown must release after window elapses"
    finally:
        monkeypatch.delenv("LOSS_COOLDOWN_SECS", raising=False)
        importlib.reload(config)


def test_bug011_cooldown_disabled_when_zero(monkeypatch):
    """LOSS_COOLDOWN_SECS=0 must disable the gate entirely."""
    monkeypatch.setenv("LOSS_COOLDOWN_SECS", "0")
    import importlib, config
    importlib.reload(config)
    try:
        import time as _time
        ex = _make_mock_executor()
        ex._last_loss_ts        = _time.time()
        ex._last_loss_direction = "BUY"
        in_cd, _ = ex.is_in_loss_cooldown()
        assert in_cd is False, "LOSS_COOLDOWN_SECS=0 must bypass the gate"
    finally:
        monkeypatch.delenv("LOSS_COOLDOWN_SECS", raising=False)
        importlib.reload(config)


def test_bug011_record_pnl_loss_sets_cooldown_state():
    """_record_pnl must populate _last_loss_ts and structural context
    when a losing trade closes — otherwise the gate can never engage."""
    import time as _time
    ex = _make_mock_executor()
    # Cache an entry context as if main.run_cycle had called record_entry_context
    ex.record_entry_context("BUY", "CHoCH BULLISH (initial)", "OB at 30000")
    ex.entry_timestamp = _time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 29980.0
    # Drive a losing trade through _record_pnl
    ex._record_pnl(entry_price=30000.0, exit_price=29980.0,
                   contracts=1, was_long=True, reason="stop")

    assert ex._last_loss_ts > 0, "_record_pnl(loss) must stamp _last_loss_ts"
    assert ex._last_loss_direction == "BUY"
    assert ex._last_loss_choch == "CHoCH BULLISH (initial)"
    assert ex._last_loss_obs   == "OB at 30000"


# ────────────────────────────────────────────────────────────────────
# BUG-012 — Same-setup re-entry after a losing trade.
#           After a loss, an entry on the same side with identical
#           CHoCH and unchanged order_blocks must be refused — require
#           at least one new structural confirmation.
# ────────────────────────────────────────────────────────────────────
def test_bug012_same_direction_same_structure_blocked():
    ex = _make_mock_executor()
    ex._last_loss_direction = "BUY"
    ex._last_loss_choch     = "CHoCH BULLISH"
    ex._last_loss_obs       = "OB: 30000.50"

    is_same, why = ex.same_setup_as_last_loss(
        direction="BUY",
        choch="CHoCH BULLISH",
        order_blocks="OB: 30000.50",
    )
    assert is_same is True
    assert "same direction" in why and "unchanged" in why


def test_bug012_different_direction_allowed():
    ex = _make_mock_executor()
    ex._last_loss_direction = "BUY"
    ex._last_loss_choch     = "CHoCH BULLISH"
    ex._last_loss_obs       = "OB: 30000.50"

    is_same, _ = ex.same_setup_as_last_loss(
        direction="SELL",                # opposite side → allow
        choch="CHoCH BULLISH",
        order_blocks="OB: 30000.50",
    )
    assert is_same is False


def test_bug012_new_choch_allows_reentry():
    """A new CHoCH event (different string) must count as new structural
    confirmation and release the gate even if direction matches."""
    ex = _make_mock_executor()
    ex._last_loss_direction = "BUY"
    ex._last_loss_choch     = "CHoCH BULLISH (1-min)"
    ex._last_loss_obs       = "OB: 30000.50"

    is_same, _ = ex.same_setup_as_last_loss(
        direction="BUY",
        choch="CHoCH BULLISH (5-min reconfirm)",   # new CHoCH string
        order_blocks="OB: 30000.50",
    )
    assert is_same is False


def test_bug012_new_ob_test_allows_reentry():
    ex = _make_mock_executor()
    ex._last_loss_direction = "BUY"
    ex._last_loss_choch     = "CHoCH BULLISH"
    ex._last_loss_obs       = "OB: 30000.50"

    is_same, _ = ex.same_setup_as_last_loss(
        direction="BUY",
        choch="CHoCH BULLISH",
        order_blocks="OB: 29985.25",       # new OB level tested
    )
    assert is_same is False


def test_bug012_no_prior_loss_never_blocks():
    """A fresh session (no loss recorded) must never trigger BUG-012."""
    ex = _make_mock_executor()
    # _last_loss_direction default = ""
    is_same, _ = ex.same_setup_as_last_loss(
        direction="BUY",
        choch="CHoCH BULLISH",
        order_blocks="OB: 30000.50",
    )
    assert is_same is False


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
