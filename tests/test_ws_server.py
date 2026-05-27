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


def test_broadcast_before_server_starts_is_safe():
    # Ensure no server is running
    ws_server.stop_ws_server()
    assert not ws_server.is_running()
    # Should be a silent no-op, not a crash
    ws_server.broadcast_tick(100.0, 99.0, 101.0)
