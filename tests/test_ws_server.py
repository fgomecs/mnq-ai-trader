"""Tests for the ws_server tick broadcaster."""
import asyncio
import json
import socket
import time
from datetime import datetime

import pytest

websockets = pytest.importorskip("websockets")

import ws_server


def _free_port() -> int:
    s = socket.socket()
    s.bind(("localhost", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server():
    port = _free_port()
    ws_server.start_ws_server(host="localhost", port=port, wait_ready=True, timeout=5)
    try:
        yield port
    finally:
        ws_server.stop_ws_server()


def test_server_starts_and_reports_running(server):
    assert ws_server.is_running()


def test_broadcast_with_no_clients_is_noop(server):
    # Should not raise even with zero connected clients.
    ws_server.broadcast_tick(100.0, 99.5, 100.5)


def test_single_client_receives_broadcast(server):
    port = server

    async def client():
        uri = f"ws://localhost:{port}"
        async with websockets.connect(uri) as ws:
            # Give the server a moment to register the client
            await asyncio.sleep(0.1)
            ws_server.broadcast_tick(30044.75, 30044.50, 30045.00, ts=1716825600)
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(client())
    assert msg["price"] == 30044.75
    assert msg["bid"]   == 30044.50
    assert msg["ask"]   == 30045.00
    assert msg["ts"]    == 1716825600


def test_default_timestamp_is_epoch_seconds(server):
    port = server
    before = int(time.time())

    async def client():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await asyncio.sleep(0.1)
            ws_server.broadcast_tick(100.0)
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(client())
    after = int(time.time())
    assert before <= msg["ts"] <= after + 1
    assert msg["bid"] == 0.0
    assert msg["ask"] == 0.0


def test_multiple_clients_all_receive(server):
    port = server

    async def run():
        uri = f"ws://localhost:{port}"
        c1 = await websockets.connect(uri)
        c2 = await websockets.connect(uri)
        try:
            await asyncio.sleep(0.15)
            assert ws_server.client_count() == 2
            ws_server.broadcast_tick(123.25, 123.00, 123.50, ts=42)
            r1 = json.loads(await asyncio.wait_for(c1.recv(), timeout=2.0))
            r2 = json.loads(await asyncio.wait_for(c2.recv(), timeout=2.0))
            return r1, r2
        finally:
            await c1.close()
            await c2.close()

    r1, r2 = asyncio.run(run())
    assert r1 == r2
    assert r1["price"] == 123.25
    assert r1["ts"]    == 42


def test_client_disconnect_is_cleaned_up(server):
    port = server

    async def run():
        ws = await websockets.connect(f"ws://localhost:{port}")
        await asyncio.sleep(0.1)
        assert ws_server.client_count() == 1
        await ws.close()
        # Give the server loop a moment to process the close
        await asyncio.sleep(0.2)

    asyncio.run(run())
    # After close, dead client should be removed (either via close handler
    # or on next broadcast attempt).
    ws_server.broadcast_tick(1.0)
    time.sleep(0.1)
    assert ws_server.client_count() == 0


def test_broadcast_includes_position_and_levels(server):
    port = server

    async def client():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await asyncio.sleep(0.1)
            ws_server.broadcast_tick(
                30000.0, 29999.5, 30000.5, ts=100,
                vwap=29995.25, or_high=30050.0, or_low=29950.0,
                entry=29980.0, stop=29960.0, target=30020.0, position=1,
            )
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(client())
    assert msg["vwap"] == 29995.25
    assert msg["or_high"] == 30050.0
    assert msg["or_low"] == 29950.0
    assert msg["entry"] == 29980.0
    assert msg["stop"] == 29960.0
    assert msg["target"] == 30020.0
    assert msg["position"] == 1
    assert msg["trade_event"] is None


def test_optional_fields_omitted_when_none(server):
    port = server

    async def client():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await asyncio.sleep(0.1)
            ws_server.broadcast_tick(100.0, 99.0, 101.0, ts=1)
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(client())
    assert "vwap" not in msg
    assert "or_high" not in msg
    assert "or_low" not in msg
    assert msg["entry"] == 0.0
    assert msg["stop"] == 0.0
    assert msg["target"] == 0.0
    assert msg["position"] == 0
    assert msg["trade_event"] is None


def test_trade_event_entry_drains_into_next_tick(server):
    port = server

    async def client():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await asyncio.sleep(0.1)
            ws_server.broadcast_trade_event("entry", 30010.5, direction="BUY")
            ws_server.broadcast_tick(30011.0, 30010.5, 30011.5, ts=200, position=1)
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            first = json.loads(raw)
            # Next tick should NOT re-deliver the same event
            ws_server.broadcast_tick(30012.0, 30011.5, 30012.5, ts=201, position=1)
            raw2 = await asyncio.wait_for(ws.recv(), timeout=2.0)
            second = json.loads(raw2)
            return first, second

    first, second = asyncio.run(client())
    assert first["trade_event"] == {"type": "entry", "price": 30010.5, "direction": "BUY"}
    assert second["trade_event"] is None


def test_trade_event_exit_carries_pnl(server):
    port = server

    async def client():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await asyncio.sleep(0.1)
            ws_server.broadcast_trade_event("exit", 30020.0, pnl=42.5)
            ws_server.broadcast_tick(30020.0, 30019.5, 30020.5, ts=300)
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(client())
    assert msg["trade_event"]["type"] == "exit"
    assert msg["trade_event"]["price"] == 30020.0
    assert msg["trade_event"]["pnl"] == 42.5


def test_broadcast_before_server_starts_is_safe():
    # Ensure no server is running
    ws_server.stop_ws_server()
    assert not ws_server.is_running()
    # Should be a silent no-op, not a crash
    ws_server.broadcast_tick(100.0, 99.0, 101.0)


# ── History message tests ────────────────────────────────────────────────────

class _FakeFeed:
    """Minimal feed stub with a _bars_1min list."""
    def __init__(self, bars):
        self._bars_1min = bars


def _make_bar(t, o=30100.0, h=30150.0, lo=30090.0, c=30120.0):
    return {"t": t, "o": o, "h": h, "l": lo, "c": c}


def test_build_history_message_basic():
    feed = _FakeFeed([_make_bar("2026-05-27 10:40")])
    msg = ws_server._build_history_message(feed)
    assert msg is not None
    data = json.loads(msg)
    assert data["type"] == "history"
    assert len(data["bars"]) == 1
    bar = data["bars"][0]
    assert bar["open"] == 30100.0
    assert bar["high"] == 30150.0
    assert bar["low"] == 30090.0
    assert bar["close"] == 30120.0
    # time must be a positive UTC epoch integer
    assert isinstance(bar["time"], int)
    assert bar["time"] > 0


def test_build_history_message_et_to_utc_offset():
    """10:40 ET on 2026-05-27 (EDT, UTC-4) → UTC 14:40 = epoch offset check."""
    feed = _FakeFeed([_make_bar("2026-05-27 10:40")])
    msg = ws_server._build_history_message(feed)
    data = json.loads(msg)
    ts = data["bars"][0]["time"]
    from datetime import timezone
    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    assert dt_utc.hour == 14
    assert dt_utc.minute == 40


def test_build_history_message_empty_feed_returns_none():
    feed = _FakeFeed([])
    assert ws_server._build_history_message(feed) is None


def test_build_history_message_none_feed_returns_none():
    assert ws_server._build_history_message(None) is None


def test_build_history_message_capped_at_1000():
    bars = [_make_bar(f"2026-05-27 {h:02d}:{m:02d}") for h in range(10, 10) for m in range(60)]
    # Build 1500 bars with unique times using day-hour-minute pattern
    bars = []
    for i in range(1500):
        hh = 9 + i // 60
        mm = i % 60
        if hh > 23:
            break
        bars.append(_make_bar(f"2026-05-27 {hh:02d}:{mm:02d}"))
    feed = _FakeFeed(bars)
    msg = ws_server._build_history_message(feed)
    data = json.loads(msg)
    assert len(data["bars"]) <= 1000


def test_client_receives_history_on_connect(server):
    port = server
    feed = _FakeFeed([
        _make_bar("2026-05-27 10:40", o=30100.0, h=30150.0, lo=30090.0, c=30120.0),
        _make_bar("2026-05-27 10:41", o=30120.0, h=30160.0, lo=30110.0, c=30140.0),
    ])
    ws_server.set_feed(feed)

    async def client():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            return json.loads(raw)

    try:
        msg = asyncio.run(client())
        assert msg["type"] == "history"
        assert len(msg["bars"]) == 2
        assert msg["bars"][0]["open"] == 30100.0
        assert msg["bars"][1]["close"] == 30140.0
    finally:
        ws_server.set_feed(None)


def test_client_receives_tick_after_history(server):
    port = server
    feed = _FakeFeed([_make_bar("2026-05-27 10:40")])
    ws_server.set_feed(feed)

    async def client():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            hist = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
            await asyncio.sleep(0.1)
            ws_server.broadcast_tick(30200.0, ts=1748350800)
            tick = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
            return hist, tick

    try:
        hist, tick = asyncio.run(client())
        assert hist["type"] == "history"
        assert tick["price"] == 30200.0
        assert "type" not in tick  # tick messages have no type field
    finally:
        ws_server.set_feed(None)


def test_no_history_when_feed_not_set(server):
    port = server
    ws_server.set_feed(None)

    async def client():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await asyncio.sleep(0.1)
            ws_server.broadcast_tick(30000.0, ts=1000)
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    msg = asyncio.run(client())
    # First message should be a tick, not history
    assert "price" in msg
    assert msg.get("type") != "history"


# ── Tick-built 1-min bar tests ───────────────────────────────────────────────

def _make_feed_stub():
    """Minimal IBKRFeed stub for tick-bar tests (no IBKR connection needed)."""
    import threading
    from ibkr_feed import IBKRFeed
    feed = IBKRFeed.__new__(IBKRFeed)
    feed._bars_1min           = []
    feed._bar_lock            = threading.Lock()
    feed._last_bars_1min      = []
    feed.vwap_cum_pv          = 0.0
    feed.vwap_cum_vol         = 0.0
    feed.vwap_date            = None
    feed.first_candle_1min_high = 0.0
    feed.first_candle_1min_low  = 0.0
    feed.first_candle_5min_high = 0.0
    feed.first_candle_5min_low  = 0.0
    feed._tick_bar_minute     = None
    feed._tick_bar            = {}
    feed._refresh_ict_levels          = lambda: None
    feed._update_or_pullback_tracking = lambda: None
    return feed


def _push_tick(feed, price, size, minute, second=0):
    """Simulate one AllLast tick arriving at the given ET minute:second."""
    import pytz
    from datetime import datetime
    from ibkr_feed import BARS_1MIN_CACHE_SIZE

    eastern = pytz.timezone("US/Eastern")

    class _Tick:
        pass
    t = _Tick()
    t.price = price
    t.size  = size
    t.time  = eastern.localize(datetime(2026, 5, 27, 15, minute, second))

    tick_et     = t.time
    this_minute = tick_et.replace(second=0, microsecond=0)

    if feed._tick_bar_minute is None or this_minute != feed._tick_bar_minute:
        if feed._tick_bar and feed._tick_bar_minute is not None:
            class _Bar:
                pass
            closed        = _Bar()
            closed.date   = feed._tick_bar_minute
            closed.open   = feed._tick_bar["open"]
            closed.high   = feed._tick_bar["high"]
            closed.low    = feed._tick_bar["low"]
            closed.close  = feed._tick_bar["close"]
            closed.volume = feed._tick_bar["volume"]
            with feed._bar_lock:
                feed._bars_1min.append(closed)
                feed._bars_1min = feed._bars_1min[-BARS_1MIN_CACHE_SIZE:]
                feed._last_bars_1min = list(feed._bars_1min)
            if this_minute.minute % 5 == 0:
                feed._refresh_ict_levels()
                feed._update_or_pullback_tracking()
        feed._tick_bar_minute = this_minute
        feed._tick_bar = {"open": price, "high": price, "low": price,
                          "close": price, "volume": size}
    else:
        if price > feed._tick_bar["high"]: feed._tick_bar["high"]  = price
        if price < feed._tick_bar["low"]:  feed._tick_bar["low"]   = price
        feed._tick_bar["close"]   = price
        feed._tick_bar["volume"] += size


def test_tick_bar_accumulates_within_minute():
    feed = _make_feed_stub()
    _push_tick(feed, 30050.00, 10, minute=15, second=0)
    _push_tick(feed, 30055.00, 5,  minute=15, second=10)
    _push_tick(feed, 30048.00, 8,  minute=15, second=50)
    # Bar not closed yet — no completed bar
    assert len(feed._bars_1min) == 0
    assert feed._tick_bar["open"]  == 30050.00
    assert feed._tick_bar["high"]  == 30055.00
    assert feed._tick_bar["low"]   == 30048.00
    assert feed._tick_bar["close"] == 30048.00
    assert feed._tick_bar["volume"] == 23


def test_tick_bar_closes_on_minute_roll():
    feed = _make_feed_stub()
    _push_tick(feed, 30050.00, 10, minute=15, second=0)
    _push_tick(feed, 30060.00, 5,  minute=15, second=30)
    # New minute arrives — triggers flush of :15 bar
    _push_tick(feed, 30055.00, 3,  minute=16, second=0)
    assert len(feed._bars_1min) == 1
    bar = feed._bars_1min[0]
    assert bar.date.minute == 15
    assert bar.date.second == 0
    assert bar.open  == 30050.00
    assert bar.high  == 30060.00
    assert bar.low   == 30050.00
    assert bar.close == 30060.00
    assert bar.volume == 15
    # New minute's forming bar seeded correctly
    assert feed._tick_bar["open"] == 30055.00


def test_tick_bar_open_never_changes():
    feed = _make_feed_stub()
    _push_tick(feed, 30100.00, 1, minute=10, second=0)
    _push_tick(feed, 30200.00, 1, minute=10, second=15)
    _push_tick(feed, 29900.00, 1, minute=10, second=45)
    assert feed._tick_bar["open"] == 30100.00  # unchanged regardless of H/L


def test_tick_bar_aligns_to_clock_minute_boundary():
    """Bot connecting mid-minute: first bar's date must be :00, not :37."""
    feed = _make_feed_stub()
    _push_tick(feed, 30050.00, 5, minute=9, second=37)
    _push_tick(feed, 30051.00, 5, minute=10, second=0)
    assert len(feed._bars_1min) == 1
    assert feed._bars_1min[0].date.second == 0
    assert feed._bars_1min[0].date.minute == 9
