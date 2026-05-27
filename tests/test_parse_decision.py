"""
Phase 2 — parse_decision unit tests.
Covers the 14 scenarios in TEST_PLAN.md.
"""
import importlib

import pytest

from claude_brain import parse_decision


# ── happy paths ────────────────────────────────────────────────────
def test_normal_buy_response_parses_all_fields():
    text = (
        "DECISION: BUY\n"
        "MODE: SCALP\n"
        "CONTRACTS: 1\n"
        "ENTRY_PRICE: 30000\n"
        "STOP_PRICE: 29980\n"
        "TARGET_1: 30040\n"
        "TARGET_2: 30080\n"
        "STOP_TICKS: 80\n"
        "TARGET_TICKS: 160\n"
        "CONFIDENCE: HIGH\n"
        "THESIS_PROBABILITY: 72\n"
        "STRATEGY: ORB\n"
        "CONFLUENCE: OR + VWAP + DELTA\n"
        "CONFLUENCE_SCORE: 8\n"
        "REASONING: clean breakout above OR high\n"
    )
    d = parse_decision(text)
    assert d["decision"]           == "BUY"
    assert d["mode"]               == "SCALP"
    assert d["contracts"]          == 1
    assert d["entry_price"]        == 30000.0
    assert d["stop_price"]         == 29980.0
    assert d["target_1"]           == 30040.0
    assert d["target_2"]           == 30080.0
    assert d["stop_ticks"]         == 80
    assert d["target_ticks"]       == 160
    assert d["confidence"]         == "HIGH"
    assert d["thesis_probability"] == 72
    assert d["strategy"]           == "ORB"
    assert d["confluence"]         == "OR + VWAP + DELTA"
    assert d["confluence_score"]   == 8
    assert "clean breakout" in d["reasoning"]


def test_normal_sell_response_parses():
    text = (
        "DECISION: SELL\n"
        "MODE: SWING\n"
        "ENTRY_PRICE: 30000\n"
        "STOP_PRICE: 30020\n"
        "TARGET_1: 29950\n"
        "THESIS_PROBABILITY: 75\n"
        "REASONING: short setup\n"
    )
    d = parse_decision(text)
    assert d["decision"] == "SELL"
    assert d["mode"]     == "SWING"
    assert d["stop_price"] > d["entry_price"]


def test_hold_response_parses():
    text = (
        "DECISION: HOLD\n"
        "REASONING: low confidence, waiting for confluence\n"
    )
    d = parse_decision(text)
    assert d["decision"] == "HOLD"
    assert "confluence" in d["reasoning"]


# ── edge cases ─────────────────────────────────────────────────────
def test_truncated_response_does_not_crash_and_does_not_emit_actionable_buy():
    text = "DECISION: BUY\nENTRY_PRICE: 30000\nSTOP_PRI"
    d = parse_decision(text)
    if d["decision"] in ("BUY", "SELL"):
        assert d["stop_price"] > 0, "truncated input must not produce actionable BUY/SELL without a stop"


def test_thesis_probability_missing_defaults_to_zero():
    text = "DECISION: HOLD\nREASONING: nothing\n"
    d = parse_decision(text)
    assert d["thesis_probability"] == 0


def test_thesis_probability_non_numeric_returns_zero():
    text = "DECISION: HOLD\nTHESIS_PROBABILITY: N/A\nREASONING: x\n"
    d = parse_decision(text)
    assert d["thesis_probability"] == 0


def test_decision_first_in_response_parses():
    text = (
        "DECISION: BUY\n"
        "MODE: SCALP\n"
        "ENTRY_PRICE: 30000\n"
        "STOP_PRICE: 29980\n"
        "TARGET_1: 30040\n"
        "THESIS_PROBABILITY: 72\n"
        "REASONING: top-loaded\n"
    )
    d = parse_decision(text)
    assert d["decision"] == "BUY"


def test_prose_before_decision_still_parses():
    text = (
        "Looking at the chart, price has broken above the OR high with strong volume.\n"
        "Delta is positive and DOM shows bid-heavy imbalance.\n"
        "\n"
        "DECISION: BUY\n"
        "MODE: SCALP\n"
        "ENTRY_PRICE: 30000\n"
        "STOP_PRICE: 29980\n"
        "TARGET_1: 30040\n"
        "THESIS_PROBABILITY: 72\n"
        "REASONING: clean break\n"
    )
    d = parse_decision(text)
    assert d["decision"] == "BUY"


def test_markdown_code_fences_are_ignored_and_content_parses():
    text = (
        "```\n"
        "DECISION: BUY\n"
        "MODE: SCALP\n"
        "ENTRY_PRICE: 30000\n"
        "STOP_PRICE: 29980\n"
        "TARGET_1: 30040\n"
        "THESIS_PROBABILITY: 72\n"
        "REASONING: fenced response\n"
        "```\n"
    )
    d = parse_decision(text)
    assert d["decision"] == "BUY"
    assert d["entry_price"] == 30000.0


@pytest.mark.parametrize("conf", ["HIGH", "MEDIUM", "LOW"])
def test_confidence_values_parse(conf):
    text = (
        f"DECISION: HOLD\nCONFIDENCE: {conf}\nREASONING: x\n"
    )
    d = parse_decision(text)
    assert d["confidence"] == conf


def test_stop_price_missing_demotes_buy_to_hold():
    text = (
        "DECISION: BUY\n"
        "ENTRY_PRICE: 30000\n"
        "TARGET_1: 30040\n"
        "THESIS_PROBABILITY: 72\n"
        "REASONING: no stop\n"
    )
    d = parse_decision(text)
    assert d["decision"] == "HOLD"
    assert "DEMOTED" in d["reasoning"]


def test_probability_parenthetical_uses_first_number():
    """BUG fix: '65 (was 70)' must parse as 65, not 6570 (which clamps to 100).
    _extract_int previously concatenated all digits; the fix takes only the
    first numeric run."""
    text = (
        "DECISION: BUY\n"
        "ENTRY_PRICE: 30000\n"
        "STOP_PRICE: 29980\n"
        "TARGET_1: 30040\n"
        "THESIS_PROBABILITY: 65 (was 70)\n"
        "REASONING: edge case\n"
    )
    d = parse_decision(text)
    assert d["thesis_probability"] == 65, \
        f"expected 65 from '65 (was 70)', got {d['thesis_probability']}"


@pytest.mark.parametrize("mode", ["SCALP", "SWING", "NONE"])
def test_mode_values_parse(mode):
    text = f"DECISION: HOLD\nMODE: {mode}\nREASONING: x\n"
    d = parse_decision(text)
    assert d["mode"] == mode


def test_reasoning_captures_text():
    text = (
        "DECISION: HOLD\n"
        "REASONING: line one of reasoning that explains the call in detail\n"
    )
    d = parse_decision(text)
    assert "line one" in d["reasoning"]
