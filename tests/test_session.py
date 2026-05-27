"""
Phase 3 — Session state machine and entry-gate tests.
"""
import datetime

import pytest


@pytest.fixture(autouse=True)
def reset_classifier():
    """HOLIDAY hard-blocks all entries — make sure no test leaks state."""
    from session_classifier import set_session_type, SessionType
    set_session_type(SessionType.UNKNOWN)
    yield
    set_session_type(SessionType.UNKNOWN)


def _et(h: int, m: int = 0) -> datetime.datetime:
    """Build a tz-naive datetime — main.get_session_state uses .hour/.minute only."""
    return datetime.datetime(2026, 5, 27, h, m)   # mid-week trading day


# ── State machine boundary tests ───────────────────────────────────
def test_before_pre_market_is_pre_session_and_blocks_entries():
    from main import get_session_state, can_enter, SessionState
    state = get_session_state(_et(8, 0))
    assert state == SessionState.PRE_SESSION
    allowed, reason = can_enter(state)
    assert not allowed
    assert "pre_session" in reason


def test_or_established_window_allows_entries():
    """09:45-10:00 ET."""
    from main import get_session_state, can_enter, SessionState
    state = get_session_state(_et(9, 50))
    assert state == SessionState.OR_ESTABLISHED
    allowed, reason = can_enter(state)
    assert allowed and reason == ""


def test_prime_window_allows_entries():
    """10:00-11:00 ET."""
    from main import get_session_state, can_enter, SessionState
    state = get_session_state(_et(10, 30))
    assert state == SessionState.PRIME_WINDOW
    allowed, _ = can_enter(state)
    assert allowed


def test_dead_zone_blocks_when_feature_flag_on(monkeypatch):
    """11:00-13:30 ET with FEATURE_DEAD_ZONE=true and low confluence → blocked."""
    import main
    monkeypatch.setattr(main, "FEATURE_DEAD_ZONE", True)
    state = main.get_session_state(_et(12, 0))
    assert state == main.SessionState.DEAD_ZONE
    allowed, reason = main.can_enter(state, confluence_score=3)
    assert not allowed
    assert "dead zone" in reason


def test_dead_zone_allows_when_feature_flag_off(monkeypatch):
    import main
    monkeypatch.setattr(main, "FEATURE_DEAD_ZONE", False)
    state = main.get_session_state(_et(12, 0))
    assert state == main.SessionState.DEAD_ZONE
    allowed, reason = main.can_enter(state, confluence_score=3)
    assert allowed, f"FEATURE_DEAD_ZONE=False should bypass the gate; got {reason!r}"


def test_afternoon_prime_allows_entries():
    """13:30-15:30 ET (AFTERNOON_PRIME ends at SESSION_AFTERNOON_PRIME_END=1530)."""
    from main import get_session_state, can_enter, SessionState
    state = get_session_state(_et(14, 30))
    assert state == SessionState.AFTERNOON_PRIME
    allowed, _ = can_enter(state)
    assert allowed


def test_after_hours_blocks_entries():
    from main import get_session_state, can_enter, SessionState
    state = get_session_state(_et(16, 30))
    assert state == SessionState.AFTER_HOURS
    allowed, _ = can_enter(state)
    assert not allowed


def test_holiday_hard_blocks_at_any_time():
    """HOLIDAY session is a hard block regardless of clock state."""
    from main import get_session_state, can_enter
    from session_classifier import set_session_type, SessionType
    set_session_type(SessionType.HOLIDAY)
    # Try a time that would normally allow entries (10:30 = PRIME_WINDOW)
    state = get_session_state(_et(10, 30))
    allowed, reason = can_enter(state)
    assert not allowed
    assert "HOLIDAY" in reason
