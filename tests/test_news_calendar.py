"""
News calendar fetcher tests — verify ForexFactory parser, today+tomorrow
fetch, and graceful fallback when the network is unavailable.

Tests use a mocked urlopen so they don't hit the real ForexFactory feed.
"""
import datetime
from unittest.mock import patch, MagicMock

import pytest

import pytz

EASTERN = pytz.timezone("US/Eastern")


def _sample_ff_json(target_date_str: str, *, tomorrow_str: str = None):
    """Build a fake ForexFactory JSON response with USD + non-USD events
    across HIGH/MEDIUM/LOW impacts and today/tomorrow."""
    events = [
        {"country": "USD", "date": target_date_str, "time": "8:30am",
         "impact": "High",   "title": "CPI"},
        {"country": "USD", "date": target_date_str, "time": "10:00am",
         "impact": "Medium", "title": "Consumer Confidence"},
        {"country": "USD", "date": target_date_str, "time": "2:00pm",
         "impact": "Low",    "title": "FOMC minutes leak"},
        {"country": "EUR", "date": target_date_str, "time": "4:00am",
         "impact": "High",   "title": "ECB rate decision"},   # non-USD → drop
    ]
    if tomorrow_str:
        events.append({
            "country": "USD", "date": tomorrow_str, "time": "8:30am",
            "impact": "High", "title": "Jobless Claims",
        })
    return events


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload
    def read(self):                return self._payload
    def __enter__(self):           return self
    def __exit__(self, *a):        return False


def test_parse_ff_event_extracts_usd_high_impact():
    from news_calendar import _parse_ff_event
    d = datetime.date(2026, 5, 27)
    item = {"country": "USD", "date": "05-27-2026", "time": "8:30am",
            "impact": "High", "title": "CPI"}
    out = _parse_ff_event(item, d, EASTERN)
    assert out is not None
    assert out["impact"] == "HIGH"
    assert out["title"]  == "CPI"
    assert out["time_et"] == "08:30"


def test_parse_ff_event_extracts_low_impact():
    from news_calendar import _parse_ff_event
    d = datetime.date(2026, 5, 27)
    item = {"country": "USD", "date": "05-27-2026", "time": "2:00pm",
            "impact": "Low", "title": "Minor Data"}
    out = _parse_ff_event(item, d, EASTERN)
    assert out is not None
    assert out["impact"] == "LOW"


def test_parse_ff_event_drops_non_usd():
    from news_calendar import _parse_ff_event
    d = datetime.date(2026, 5, 27)
    item = {"country": "EUR", "date": "05-27-2026", "time": "4:00am",
            "impact": "High", "title": "ECB"}
    out = _parse_ff_event(item, d, EASTERN)
    assert out is None


def test_fetch_today_and_tomorrow_includes_both_dates():
    """The today+tomorrow helper must return events from both target dates,
    including LOW impact."""
    today    = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    payload = _sample_ff_json(
        target_date_str=today.strftime("%m-%d-%Y"),
        tomorrow_str=tomorrow.strftime("%m-%d-%Y"),
    )

    import news_calendar
    import json as _json
    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = _FakeResp(_json.dumps(payload).encode())
        events = news_calendar.fetch_forexfactory_today_and_tomorrow()

    titles = [e["title"] for e in events]
    impacts = {e["impact"] for e in events}
    assert "CPI" in titles
    assert "Jobless Claims" in titles
    assert "ECB rate decision" not in titles  # non-USD filtered
    # All three impacts should appear (high, medium, low)
    assert impacts == {"HIGH", "MEDIUM", "LOW"}


def test_fetch_falls_back_to_empty_list_on_network_error():
    """Network blowups must not raise into the caller."""
    import news_calendar
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        events = news_calendar.fetch_forexfactory_today_and_tomorrow()
    assert events == []


def test_fetch_today_only_filters_to_high_medium():
    """The legacy _fetch_forexfactory_today helper must still strip LOW
    impact so the news_danger_zone gate remains tight."""
    today_str = datetime.date.today().strftime("%m-%d-%Y")
    payload = _sample_ff_json(target_date_str=today_str)

    import news_calendar
    import json as _json
    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = _FakeResp(_json.dumps(payload).encode())
        events = news_calendar._fetch_forexfactory_today()

    impacts = {e["impact"] for e in events}
    assert "LOW" not in impacts
    assert "HIGH" in impacts or "MEDIUM" in impacts
