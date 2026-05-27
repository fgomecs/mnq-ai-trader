"""
Phase 2 — Risk parameter and feature-flag tests.
Mostly env-driven reloads since most risk knobs are module-level constants.
"""
import importlib
import time
import types

import pytest


# ── MAX_CONTRACTS cap ───────────────────────────────────────────────
def test_max_contracts_1_caps_decision_to_1(monkeypatch):
    """min(decision_contracts, MAX_CONTRACTS) — at MAX=1 even a 5-contract
    decision becomes 1."""
    monkeypatch.setenv("MAX_CONTRACTS", "1")
    import config; importlib.reload(config)
    try:
        decision_contracts = 5
        capped = min(int(decision_contracts), config.MAX_CONTRACTS)
        assert capped == 1
    finally:
        monkeypatch.delenv("MAX_CONTRACTS", raising=False)
        importlib.reload(config)


def test_max_contracts_4_caps_decision_to_4(monkeypatch):
    monkeypatch.setenv("MAX_CONTRACTS", "4")
    import config; importlib.reload(config)
    try:
        decision_contracts = 10
        capped = min(int(decision_contracts), config.MAX_CONTRACTS)
        assert capped == 4
    finally:
        monkeypatch.delenv("MAX_CONTRACTS", raising=False)
        importlib.reload(config)


# ── MIN_THESIS_PROBABILITY gate (parse_decision demotes below threshold) ──
def _parse_with_threshold(prob_val: int, threshold: int, gate_on: bool = True):
    """Helper: reload claude_brain with the desired threshold/gate, then parse."""
    import os
    os.environ["MIN_THESIS_PROBABILITY"] = str(threshold)
    os.environ["FEATURE_THESIS_GATE"]    = "true" if gate_on else "false"
    import config; importlib.reload(config)
    import claude_brain; importlib.reload(claude_brain)
    text = (
        "DECISION: BUY\nENTRY_PRICE: 30000\nSTOP_PRICE: 29980\n"
        f"TARGET_1: 30040\nTHESIS_PROBABILITY: {prob_val}\nREASONING: x\n"
    )
    return claude_brain.parse_decision(text)


def _cleanup_env():
    import os
    for k in ("MIN_THESIS_PROBABILITY", "FEATURE_THESIS_GATE"):
        os.environ.pop(k, None)
    import config; importlib.reload(config)
    import claude_brain; importlib.reload(claude_brain)


def test_min_thesis_70_blocks_prob_65():
    try:
        d = _parse_with_threshold(prob_val=65, threshold=70)
        assert d["decision"] == "HOLD", "BUY with prob 65 < threshold 70 must demote to HOLD"
    finally:
        _cleanup_env()


def test_min_thesis_70_allows_prob_72():
    try:
        d = _parse_with_threshold(prob_val=72, threshold=70)
        assert d["decision"] == "BUY"
    finally:
        _cleanup_env()


def test_min_thesis_55_allows_prob_60():
    try:
        d = _parse_with_threshold(prob_val=60, threshold=55)
        assert d["decision"] == "BUY"
    finally:
        _cleanup_env()


# ── consecutive_losses tracking ────────────────────────────────────
def test_consecutive_losses_tracked_correctly():
    from executor import Executor

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
        def placeOrder(self, *_a, **_kw): return None

    ex = Executor(_MockIB(), types.SimpleNamespace(symbol="MNQ"), paper=True)
    ex.entry_timestamp = time.time()
    ex.entry_price     = 30000.0
    ex.stop_price      = 29980.0

    # First loss
    ex._record_pnl(entry_price=30000, exit_price=29980,
                   contracts=1, was_long=True, reason="stop")
    assert ex.consecutive_losses == 1

    # Second loss in a row
    ex._record_pnl(entry_price=30000, exit_price=29980,
                   contracts=1, was_long=True, reason="stop")
    assert ex.consecutive_losses == 2

    # A win resets the counter
    ex._record_pnl(entry_price=30000, exit_price=30040,
                   contracts=1, was_long=True, reason="target")
    assert ex.consecutive_losses == 0


# ── FEATURE_THESIS_GATE bypass ─────────────────────────────────────
def test_thesis_gate_off_bypasses_check():
    """With FEATURE_THESIS_GATE=false, a low-probability BUY survives parse."""
    try:
        d = _parse_with_threshold(prob_val=65, threshold=70, gate_on=False)
        assert d["decision"] == "BUY", \
            "FEATURE_THESIS_GATE=false must not demote based on probability"
    finally:
        _cleanup_env()


# ── FEATURE_R_BUDGET enforcement ───────────────────────────────────
def test_r_budget_on_blocks_when_exhausted(monkeypatch):
    """FEATURE_R_BUDGET=true: when session_r_spent >= MAX_SESSION_R_LOSS the
    bot must refuse new entries. We assert the inequality directly so the
    test doesn't depend on the IBKR-heavy execute() path."""
    monkeypatch.setenv("FEATURE_R_BUDGET",    "true")
    monkeypatch.setenv("MAX_SESSION_R_LOSS",  "3.0")
    import config; importlib.reload(config)
    try:
        session_r_spent = 3.0
        # Same gate logic as executor.py:345
        blocked = config.FEATURE_R_BUDGET and session_r_spent >= config.MAX_SESSION_R_LOSS
        assert blocked, "R-budget gate must block when budget exhausted"
    finally:
        monkeypatch.delenv("FEATURE_R_BUDGET", raising=False)
        monkeypatch.delenv("MAX_SESSION_R_LOSS", raising=False)
        importlib.reload(config)


def test_r_budget_off_ignores_budget(monkeypatch):
    """FEATURE_R_BUDGET=false: gate must not fire even if budget exhausted."""
    monkeypatch.setenv("FEATURE_R_BUDGET",    "false")
    monkeypatch.setenv("MAX_SESSION_R_LOSS",  "3.0")
    import config; importlib.reload(config)
    try:
        session_r_spent = 5.0     # well past the cap
        blocked = config.FEATURE_R_BUDGET and session_r_spent >= config.MAX_SESSION_R_LOSS
        assert not blocked, "R-budget off must ignore session_r_spent overflow"
    finally:
        monkeypatch.delenv("FEATURE_R_BUDGET", raising=False)
        monkeypatch.delenv("MAX_SESSION_R_LOSS", raising=False)
        importlib.reload(config)
