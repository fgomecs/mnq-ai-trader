"""Tests for the ws_server tick broadcaster."""
import asyncio
import json
import socket
import time

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
