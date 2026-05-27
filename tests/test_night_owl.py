"""
NIGHT_OWL mode tests — 24/7 unrestricted scanning behavior.
"""
import datetime
import importlib

import pytest


def _reload_main_with_night_owl(monkeypatch, *, enabled: bool):
    """Reload config + main with NIGHT_OWL set to the requested value.
    Returns the reloaded `main` module."""
    monkeypatch.setenv("NIGHT_OWL", "true" if enabled else "false")
    import config
    importlib.reload(config)
    import main
    importlib.reload(main)
    return main


def _et(h: int, m: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 5, 27, h, m)


def teardown_function(_fn):
    """Restore NIGHT_OWL=False between tests to avoid state leakage.
    Note: project's real .env has NIGHT_OWL=true, so we must explicitly
    set "false" — popping the env var alone would let load_dotenv()
    pull "true" back in on reload."""
    import os
    os.environ["NIGHT_OWL"] = "false"
    import config; importlib.reload(config)
    import main;   importlib.reload(main)
    os.environ.pop("NIGHT_OWL", None)


# ── config.NIGHT_OWL env-read ────────────────────────────────
def test_config_night_owl_default_false_when_env_says_false(monkeypatch):
    """When env explicitly says 'false', config.NIGHT_OWL is False.
    (Project's real .env has it true; this exercises the negation path.)"""
    monkeypatch.setenv("NIGHT_OWL", "false")
    import config; importlib.reload(config)
    assert config.NIGHT_OWL is False


def test_config_night_owl_reads_true_from_env(monkeypatch):
    monkeypatch.setenv("NIGHT_OWL", "true")
    import config; importlib.reload(config)
    try:
        assert config.NIGHT_OWL is True
    finally:
        monkeypatch.delenv("NIGHT_OWL", raising=False)
        importlib.reload(config)


# ── get_session_state under NIGHT_OWL ────────────────────────
def test_night_owl_returns_prime_window_at_3am(monkeypatch):
    main = _reload_main_with_night_owl(monkeypatch, enabled=True)
    state = main.get_session_state(_et(3, 0))
    assert state == main.SessionState.PRIME_WINDOW


def test_night_owl_returns_prime_window_at_11pm(monkeypatch):
    main = _reload_main_with_night_owl(monkeypatch, enabled=True)
    state = main.get_session_state(_et(23, 0))
    assert state == main.SessionState.PRIME_WINDOW


def test_night_owl_preserves_pre_market_window_at_0830(monkeypatch):
    """The 08:30-09:30 ET window must still return PRE_MARKET so the
    pre-market analysis routine fires."""
    main = _reload_main_with_night_owl(monkeypatch, enabled=True)
    state = main.get_session_state(_et(8, 30))
    assert state == main.SessionState.PRE_MARKET


def test_night_owl_overrides_dead_zone(monkeypatch):
    """Normally 12:00 ET = DEAD_ZONE. Under NIGHT_OWL it must be PRIME_WINDOW."""
    main = _reload_main_with_night_owl(monkeypatch, enabled=True)
    state = main.get_session_state(_et(12, 0))
    assert state == main.SessionState.PRIME_WINDOW


def test_night_owl_overrides_closing(monkeypatch):
    """Normally 15:45 ET = CLOSING (exit-only). Under NIGHT_OWL = PRIME_WINDOW."""
    main = _reload_main_with_night_owl(monkeypatch, enabled=True)
    state = main.get_session_state(_et(15, 45))
    assert state == main.SessionState.PRIME_WINDOW


def test_night_owl_overrides_after_hours(monkeypatch):
    """16:30 ET would be AFTER_HOURS — NIGHT_OWL forces PRIME_WINDOW."""
    main = _reload_main_with_night_owl(monkeypatch, enabled=True)
    state = main.get_session_state(_et(16, 30))
    assert state == main.SessionState.PRIME_WINDOW


def test_night_owl_can_enter_allows_at_dead_zone_clock(monkeypatch):
    """can_enter on the NIGHT_OWL-mapped PRIME_WINDOW must return True."""
    main = _reload_main_with_night_owl(monkeypatch, enabled=True)
    state = main.get_session_state(_et(12, 0))
    allowed, reason = main.can_enter(state, confluence_score=3)
    assert allowed, f"NIGHT_OWL must allow entries at 12:00 ET; got: {reason}"


# ── _wait_for_market_hours under NIGHT_OWL ──────────────────
def test_night_owl_wait_for_market_hours_short_circuits(monkeypatch):
    """Must return immediately without entering the sleep loops."""
    main = _reload_main_with_night_owl(monkeypatch, enabled=True)
    import time
    t0 = time.time()
    main._wait_for_market_hours()
    elapsed = time.time() - t0
    # No-op should complete in well under a second; certainly not the
    # 30 minute weekend tick or 60s morning poll.
    assert elapsed < 0.5, f"_wait_for_market_hours under NIGHT_OWL took {elapsed:.2f}s — should be instant"


# ── Default mode still gates correctly ──────────────────────
def test_default_mode_returns_dead_zone_at_12pm(monkeypatch):
    """With NIGHT_OWL=false the existing state machine is untouched."""
    main = _reload_main_with_night_owl(monkeypatch, enabled=False)
    state = main.get_session_state(_et(12, 0))
    assert state == main.SessionState.DEAD_ZONE


def test_default_mode_returns_after_hours_at_5pm(monkeypatch):
    main = _reload_main_with_night_owl(monkeypatch, enabled=False)
    state = main.get_session_state(_et(17, 0))
    assert state == main.SessionState.AFTER_HOURS


# ── dashboard JSON carries nightOwl flag ────────────────────
def test_dashboard_emits_night_owl_flag_when_true(tmp_path, monkeypatch):
    """update_dashboard must write nightOwl: true into the JSON when
    config.NIGHT_OWL is True so the badge can render."""
    monkeypatch.setenv("NIGHT_OWL", "true")
    import config; importlib.reload(config)
    import dashboard_writer as dw; importlib.reload(dw)
    try:
        target = tmp_path / "dashboard_data.json"
        monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))
        dw.update_dashboard(
            position=0, current_price=30000.0, daily_pnl=0.0, trades=[],
            last_decision="HOLD", last_reasoning="x",
            snapshot={"last_price": 30000.0},
        )
        import json
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["nightOwl"] is True
    finally:
        monkeypatch.delenv("NIGHT_OWL", raising=False)
        importlib.reload(config)
        importlib.reload(dw)


def test_dashboard_night_owl_false_when_disabled(tmp_path, monkeypatch):
    """Explicitly set false (project .env has it true) and verify the
    dashboard JSON reflects that."""
    monkeypatch.setenv("NIGHT_OWL", "false")
    import config; importlib.reload(config)
    import dashboard_writer as dw; importlib.reload(dw)
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))
    dw.update_dashboard(
        position=0, current_price=30000.0, daily_pnl=0.0, trades=[],
        last_decision="HOLD", last_reasoning="x",
        snapshot={"last_price": 30000.0},
    )
    import json
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["nightOwl"] is False
