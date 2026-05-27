"""
Phase 4 — API resilience: parse_decision must survive every input shape
Claude can plausibly emit (empty, None, malformed, JSON-only, out-of-range
probability, unknown decision tokens).
"""
import pytest


def test_parse_decision_empty_string_returns_hold():
    from claude_brain import parse_decision
    d = parse_decision("")
    assert d["decision"] == "HOLD"
    assert d["thesis_probability"] == 0


def test_parse_decision_none_input_is_handled_gracefully():
    """A None response from the API (e.g. timeout / no body) must not crash.
    Either parses to a safe HOLD or raises a single recoverable exception."""
    from claude_brain import parse_decision
    try:
        d = parse_decision(None)
    except AttributeError:
        pytest.fail("parse_decision(None) should be handled, not raise AttributeError")
    except TypeError:
        pytest.fail("parse_decision(None) should be handled, not raise TypeError")
    assert d["decision"] == "HOLD"


def test_parse_decision_json_only_response_falls_back_safely():
    """A pure JSON blob (not the key:value format) should not crash;
    the parser will skip every line and return defaults."""
    from claude_brain import parse_decision
    blob = '{"decision":"BUY","stop":29980}'   # not in key:value format
    d = parse_decision(blob)
    # Won't be a BUY because no STOP_PRICE: prefix → demoted/default to HOLD
    assert d["decision"] == "HOLD"


def test_parse_decision_handles_weird_whitespace():
    from claude_brain import parse_decision
    text = (
        "    DECISION:    BUY   \n"
        "\tENTRY_PRICE  :   30000\n"
        "STOP_PRICE:29980\n"
        "TARGET_1: 30040\n"
        "THESIS_PROBABILITY: 72\n"
        "REASONING: padded whitespace\n"
    )
    d = parse_decision(text)
    assert d["decision"]    == "BUY"
    assert d["entry_price"] == 30000.0
    assert d["stop_price"]  == 29980.0


def test_parse_decision_thesis_above_100_clamps_to_100():
    from claude_brain import parse_decision
    text = (
        "DECISION: BUY\nENTRY_PRICE: 30000\nSTOP_PRICE: 29980\n"
        "TARGET_1: 30040\nTHESIS_PROBABILITY: 150\nREASONING: x\n"
    )
    d = parse_decision(text)
    assert d["thesis_probability"] == 100


def test_parse_decision_thesis_negative_clamps_to_valid_range():
    """Negative number passes through _extract_int (first digit run) so
    it produces a non-negative result; the clamp guarantees ≤ 100."""
    from claude_brain import parse_decision
    text = (
        "DECISION: HOLD\n"
        "THESIS_PROBABILITY: -15\n"
        "REASONING: x\n"
    )
    d = parse_decision(text)
    assert 0 <= d["thesis_probability"] <= 100


def test_parse_decision_unknown_decision_token_returns_hold():
    from claude_brain import parse_decision
    text = "DECISION: MAYBE\nREASONING: ambiguous\n"
    d = parse_decision(text)
    # _first_match returns None → falls back to "HOLD"
    assert d["decision"] == "HOLD"


def test_parse_decision_only_colon_no_value_does_not_crash():
    """Pathological line with key but blank value."""
    from claude_brain import parse_decision
    text = "DECISION:\nREASONING:\n"
    d = parse_decision(text)
    assert d["decision"] == "HOLD"


def test_parse_decision_lines_without_colon_are_skipped():
    from claude_brain import parse_decision
    text = (
        "this is prose with no colon\n"
        "another prose line\n"
        "DECISION: BUY\n"
        "ENTRY_PRICE: 30000\n"
        "STOP_PRICE: 29980\n"
        "TARGET_1: 30040\n"
        "THESIS_PROBABILITY: 72\n"
        "REASONING: x\n"
    )
    d = parse_decision(text)
    assert d["decision"] == "BUY"
