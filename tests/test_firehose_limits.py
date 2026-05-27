"""
Regression: the v4.5.0 firehose bump (DOM 40 levels + 1-sec bars) caused
an IBKR Gateway EWriter overflow on 2026-05-27 09:14 ET. These pins
prevent silent re-bumping back to the unsustainable values.

Asserting on source code rather than runtime behavior because the
reqMktDepth / reqRealTimeBars calls require a live IBKR connection.
"""
from pathlib import Path

import pytest


FEED_SRC = Path(r"C:\trading\mnq-ai-trader\ibkr_feed.py").read_text(encoding="utf-8")


def test_dom_request_uses_20_levels_not_40():
    """numRows=40 was the production-incident value. Must stay at 20."""
    assert "numRows=20" in FEED_SRC, \
        "reqMktDepth must request numRows=20 (was 40 — caused Gateway overflow)"
    assert "numRows=40" not in FEED_SRC, \
        "reqMktDepth call must not regress to numRows=40"


def test_realtime_bars_use_5_second_size_not_1():
    """barSize=1 violates the IBKR API spec (only 5s supported) and
    contributed to the EWriter overflow. Must stay at 5."""
    # The literal positional argument: reqRealTimeBars(contract, 5, "TRADES", False)
    assert "reqRealTimeBars(\n                self.contract, 5," in FEED_SRC \
        or "reqRealTimeBars(self.contract, 5," in FEED_SRC, \
        "reqRealTimeBars must use barSize=5 (was 1 — IBKR API spec violation)"
    # Negative: no leftover 1-sec call
    assert "self.contract, 1, \"TRADES\"" not in FEED_SRC, \
        "1-sec reqRealTimeBars call must not be present"


def test_dom_log_line_reflects_20_levels():
    """The startup log line should not advertise 40 levels anymore."""
    assert '"DOM stream started (20 levels)' in FEED_SRC \
        or "DOM stream started (20 levels)" in FEED_SRC, \
        "DOM startup log must match the new 20-level subscription"


def test_module_header_documents_5_second_bars():
    """Module docstring should match the actual subscription cadence."""
    assert "reqRealTimeBars (5-sec bars)" in FEED_SRC, \
        "module header should document 5-sec bars, not 1-sec"


def test_throttle_machinery_still_present():
    """The client-side throttling stays — defense in depth even with
    the firehose now at safer levels."""
    assert "_compute_dom_signals_impl" in FEED_SRC
    assert "_get_live_dom_impl"        in FEED_SRC
    assert "_on_dom_update"            in FEED_SRC
    assert "_check_dom_update_rate"    in FEED_SRC
