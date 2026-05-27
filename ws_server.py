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


_pending_trade_event = None
_pending_lock = threading.Lock()


def broadcast_tick(price: float, bid: float = 0.0, ask: float = 0.0,
                   ts: Optional[int] = None,
                   vwap: Optional[float] = None,
                   or_high: Optional[float] = None,
                   or_low: Optional[float] = None,
                   entry: float = 0.0,
                   stop: float = 0.0,
                   target: float = 0.0,
                   position: int = 0,
                   trade_event: Optional[dict] = None) -> None:
    """Thread-safe broadcast to all connected clients. No-op if server
    not running or no clients.

    `trade_event` may also be set out-of-band via `broadcast_trade_event()`
    — a pending event is drained into the next tick message.
    """
    global _pending_trade_event
    if _loop is None or not _loop.is_running():
        return
    if ts is None:
        ts = int(time.time())

    if trade_event is None:
        with _pending_lock:
            if _pending_trade_event is not None:
                trade_event = _pending_trade_event
                _pending_trade_event = None

    payload = {
        "price":    float(price) if price else 0.0,
        "bid":      float(bid)   if bid   else 0.0,
        "ask":      float(ask)   if ask   else 0.0,
        "ts":       int(ts),
        "entry":    float(entry)    if entry    else 0.0,
        "stop":     float(stop)     if stop     else 0.0,
        "target":   float(target)   if target   else 0.0,
        "position": int(position),
        "trade_event": trade_event,
    }
    if vwap is not None and vwap > 0:
        payload["vwap"] = float(vwap)
    if or_high is not None and or_high > 0:
        payload["or_high"] = float(or_high)
    if or_low is not None and or_low > 0:
        payload["or_low"] = float(or_low)

    msg = json.dumps(payload)
    try:
        asyncio.run_coroutine_threadsafe(_broadcast_coro(msg), _loop)
    except Exception as e:
        logger.debug(f"[WS] broadcast schedule failed: {e}")


def broadcast_trade_event(event_type: str, price: float,
                          direction: Optional[str] = None,
                          pnl: Optional[float] = None) -> None:
    """Queue a trade event to be attached to the next tick broadcast.

    event_type: "entry" or "exit"
    """
    global _pending_trade_event
    ev = {"type": event_type, "price": float(price)}
    if direction is not None:
        ev["direction"] = direction
    if pnl is not None:
        ev["pnl"] = float(pnl)
    with _pending_lock:
        _pending_trade_event = ev


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
