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


def test_get_calendar_events_two_day_uses_today_and_tomorrow_fetcher():
    """get_calendar_events_two_day must call fetch_forexfactory_today_and_tomorrow
    and cache the result by trading-day."""
    import news_calendar
    # Clear cache so we know fresh fetch happens
    news_calendar._calendar_cache["date"]   = None
    news_calendar._calendar_cache["events"] = []

    fake = [{"title": "CPI", "impact": "HIGH", "time_et": "08:30",
             "date": "2026-05-27", "time_obj": None, "source": "ForexFactory"}]
    with patch("news_calendar.fetch_forexfactory_today_and_tomorrow",
               return_value=fake):
        first  = news_calendar.get_calendar_events_two_day()
        second = news_calendar.get_calendar_events_two_day()  # cache hit

    assert first == fake
    assert second == fake


def test_get_news_snapshot_includes_events_calendar():
    """get_news_snapshot must surface a separate `events_calendar` field
    sourced from get_calendar_events_two_day (with time_obj stripped)."""
    import news_calendar

    # Patch the cached calendar so the test is deterministic and doesn't
    # hit the network.
    fake_cal = [{"title": "Jobless Claims", "impact": "HIGH",
                 "time_et": "08:30", "date": "2026-05-28",
                 "time_obj": None, "source": "ForexFactory"}]
    with patch("news_calendar.get_calendar_events_two_day",
               return_value=fake_cal):
        snap = news_calendar.get_news_snapshot(ib=None)

    assert "events_calendar" in snap
    assert any(e["title"] == "Jobless Claims" for e in snap["events_calendar"])
    # time_obj must be stripped (not JSON-serializable in real fetch)
    for e in snap["events_calendar"]:
        assert "time_obj" not in e


def test_dashboard_writer_prefers_events_calendar_over_events_today(tmp_path, monkeypatch):
    """update_dashboard's newsEvents output must source from events_calendar
    when present, falling back to events_today otherwise."""
    import dashboard_writer as dw
    import json as _json
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))

    snap = {
        "last_price": 30000.0,
        "events_today":    [{"title": "today HIGH",    "impact": "HIGH",   "time_et": "08:30"}],
        "events_calendar": [{"title": "tomorrow HIGH", "impact": "HIGH",   "time_et": "08:30"},
                            {"title": "today LOW",    "impact": "LOW",    "time_et": "14:00"}],
    }
    dw.update_dashboard(
        position=0, current_price=30000.0, daily_pnl=0.0,
        trades=[], last_decision="HOLD", last_reasoning="x", snapshot=snap,
    )
    data = _json.loads(target.read_text(encoding="utf-8"))
    titles = [e["title"] for e in data["newsEvents"]]
    assert "tomorrow HIGH" in titles
    assert "today LOW"    in titles
    # Should NOT have used the legacy gate list when calendar is present
    assert "today HIGH" not in titles


def test_dashboard_writer_falls_back_to_events_today_when_no_calendar(tmp_path, monkeypatch):
    """If events_calendar is missing or empty (e.g. ForexFactory unreachable),
    newsEvents must fall back to events_today."""
    import dashboard_writer as dw
    import json as _json
    target = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(dw, "DASHBOARD_FILE", str(target))

    snap = {
        "last_price": 30000.0,
        "events_today":    [{"title": "today HIGH", "impact": "HIGH", "time_et": "08:30"}],
        # events_calendar missing
    }
    dw.update_dashboard(
        position=0, current_price=30000.0, daily_pnl=0.0,
        trades=[], last_decision="HOLD", last_reasoning="x", snapshot=snap,
    )
    data = _json.loads(target.read_text(encoding="utf-8"))
    assert any(e["title"] == "today HIGH" for e in data["newsEvents"])


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
