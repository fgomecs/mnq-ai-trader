"""
WebSocket tick broadcaster.

Runs an asyncio websockets server in a background thread on port 8765
(configurable). The trading bot's main/ticker threads push ticks via
the thread-safe `broadcast_tick()` call; connected browser clients
receive JSON frames of the form:

    {"price": 30044.75, "bid": 30044.50, "ask": 30045.00, "ts": 1716825600}

Used by chart_test3.html to build live 1-minute bars in the browser
without polling any JSON files.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Optional, Set

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

try:
    from logger import logger
except Exception:
    import logging
    logger = logging.getLogger("ws_server")


_clients: Set = set()
_loop: Optional[asyncio.AbstractEventLoop] = None
_server = None
_thread: Optional[threading.Thread] = None
_started = threading.Event()
_stop_requested = False


async def _handler(websocket, *_args):
    """Register a client for the lifetime of its connection."""
    _clients.add(websocket)
    peer = getattr(websocket, "remote_address", "?")
    logger.info(f"[WS] client connected: {peer} (total={len(_clients)})")
    try:
        async for _ in websocket:
            pass  # we don't expect inbound messages; drain anyway
    except Exception:
        pass
    finally:
        _clients.discard(websocket)
        logger.info(f"[WS] client disconnected: {peer} (total={len(_clients)})")


async def _broadcast_coro(payload: str) -> None:
    if not _clients:
        return
    dead = []
    for ws in list(_clients):
        try:
            await ws.send(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


def broadcast_tick(price: float, bid: float = 0.0, ask: float = 0.0,
                   ts: Optional[int] = None) -> None:
    """Thread-safe broadcast to all connected clients. No-op if server
    not running or no clients."""
    if _loop is None or not _loop.is_running():
        return
    if ts is None:
        ts = int(time.time())
    msg = json.dumps({
        "price": float(price) if price else 0.0,
        "bid":   float(bid)   if bid   else 0.0,
        "ask":   float(ask)   if ask   else 0.0,
        "ts":    int(ts),
    })
    try:
        asyncio.run_coroutine_threadsafe(_broadcast_coro(msg), _loop)
    except Exception as e:
        logger.debug(f"[WS] broadcast schedule failed: {e}")


def _run(host: str, port: int) -> None:
    global _loop, _server
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    async def _serve():
        global _server
        _server = await websockets.serve(_handler, host, port)
        _started.set()
        logger.info(f"[WS] tick broadcaster listening on ws://{host}:{port}")
        # Run until externally stopped
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass

    try:
        _loop.run_until_complete(_serve())
    except Exception as e:
        logger.error(f"[WS] server crashed: {e}")
        _started.set()
    finally:
        try:
            _loop.close()
        except Exception:
            pass


def start_ws_server(host: str = "localhost", port: int = 8765,
                    wait_ready: bool = True, timeout: float = 5.0) -> bool:
    """Start the websocket server in a daemon thread. Returns True on
    success, False if websockets package missing or thread already running."""
    global _thread
    if not _WS_AVAILABLE:
        logger.warning("[WS] websockets package not installed — broadcaster disabled")
        return False
    if _thread is not None and _thread.is_alive():
        return True
    _started.clear()
    _thread = threading.Thread(
        target=_run, args=(host, port), daemon=True, name="WSBroadcaster"
    )
    _thread.start()
    if wait_ready:
        _started.wait(timeout=timeout)
    return True


def stop_ws_server() -> None:
    """Stop the server. Primarily for tests."""
    global _server, _loop, _thread
    if _loop and _loop.is_running():
        async def _shutdown():
            if _server is not None:
                _server.close()
                await _server.wait_closed()
            for ws in list(_clients):
                try:
                    await ws.close()
                except Exception:
                    pass
            _clients.clear()
        try:
            fut = asyncio.run_coroutine_threadsafe(_shutdown(), _loop)
            fut.result(timeout=3)
        except Exception:
            pass
        _loop.call_soon_threadsafe(_loop.stop)
    if _thread is not None:
        _thread.join(timeout=3)
    _thread = None
    _server = None
    _loop = None


def client_count() -> int:
    return len(_clients)


def is_running() -> bool:
    return _thread is not None and _thread.is_alive() and _loop is not None and _loop.is_running()
