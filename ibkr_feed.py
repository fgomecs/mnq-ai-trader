"""
IBKR Market Data Feed — MNQ AI Trader
======================================
Architecture:
  - Bars fetched ONCE at startup, then updated via reqRealTimeBars (5-sec bars)
  - Historical bars refresh only on bar close (not every snapshot cycle)
  - Snapshot assembly < 1 second (vs 7-17s before)
  - True bid/ask delta classification via live tick stream
  - MTF alignment computed as structured field (not inferred from candle text)
  - OR pullback tracking with explicit entry zone computation
  - Delta trend across bars (distribution detection)
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import pytz
from ib_insync import IB, Future, RealTimeBar

from config import (
    CONTRACT_CONID, CONTRACT_EXPIRY, CURRENCY, EXCHANGE,
    IBKR_CLIENT_ID, IBKR_HOST, IBKR_PORT, LIVE_DATA_ACTIVE, SYMBOL,
    FEATURE_OFI, FEATURE_DOM_ADVANCED, FEATURE_MTF_SCORE, FEATURE_DELTA_LIVE,
    DOM_HISTORY_MAX_SNAPSHOTS, TICK_STATE_PERSIST_INTERVAL_SECS,
    INIT_BARS_1MIN_DURATION, INIT_BARS_5MIN_DURATION,
    INIT_BARS_15MIN_DURATION, INIT_BARS_DAILY_DURATION,
    REALTIME_BARS_PER_MINUTE, BARS_1MIN_CACHE_SIZE,
    SNAPSHOT_ASSEMBLY_SLEEP_SECS, NEWS_CACHE_TTL_SECS,
    OFI_STRONG_THRESHOLD_CONTRACTS, OFI_ACCELERATION_THRESHOLD,
    OFI_DECELERATION_THRESHOLD, OFI_STRONG_BUY_THRESHOLD,
    OFI_BUY_THRESHOLD, OFI_STRONG_SELL_THRESHOLD, OFI_SELL_THRESHOLD,
    DELTA_DIVERGENCE_THRESHOLD,
    DOM_SIGNIFICANT_SIZE, DOM_LARGE_SIZE, DOM_WHALE_SIZE,
    DOM_BUY_PRESSURE_BULL_THRESHOLD, DOM_SELL_PRESSURE_BEAR_THRESHOLD,
    DOM_CLUSTER_TOLERANCE_POINTS, DOM_VACUUM_THRESHOLD_SIZE,
    DOM_ICEBERG_SHRINK_PCT, DOM_ICEBERG_RECOVERY_PCT,
    DOM_SWEEP_LEVEL_THRESHOLD,
    VOLUME_PROFILE_TARGET_PCT, POC_PROXIMITY_POINTS,
    FVG_PROXIMITY_POINTS, OB_PROXIMITY_POINTS, LIQUIDITY_POOL_TOLERANCE,
    OR_PULLBACK_THRESHOLD_PCT,
    SESSION_MARKET_OPEN_TIME,
)
from logger import logger
from news_calendar import get_news_snapshot
from data_recorder import recorder as _recorder

eastern = pytz.timezone("US/Eastern")


def _bar_et(bar) -> datetime:
    """Return timezone-aware Eastern datetime for an ib_insync bar or RealTimeBar."""
    # RealTimeBar uses .time (a datetime), historical bars use .date (str or datetime)
    raw = getattr(bar, "date", None) or getattr(bar, "time", None)
    if raw is None:
        return datetime.now(eastern)
    if isinstance(raw, datetime):
        bt = raw
    elif isinstance(raw, (int, float)):
        import datetime as _dt_module
        bt = _dt_module.datetime.fromtimestamp(raw, tz=_dt_module.timezone.utc)
    else:
        bt = pd.Timestamp(str(raw)).to_pydatetime()
    return bt.astimezone(eastern) if bt.tzinfo else eastern.localize(bt)


class IBKRFeed:
    def __init__(self):
        self.ib        = IB()
        self.contract  = None
        self.connected = False

        # ── Bar cache (Improvement 1) ──────────────────────
        self._bars_1min:  list = []
        self._bars_5min:  list = []
        self._bars_15min: list = []
        self._bars_daily: list = []
        self._bar_lock    = threading.Lock()
        self._bars_initialized = False
        self._last_bars_1min: list = []   # for delta fallback

        # Real-time 5-sec bar accumulator → builds 1-min bars
        self._rt_bar_buffer: list = []
        self._rt_subscription = None

        # ── Session levels ─────────────────────────────────
        self.asia_high      = self.asia_low      = None
        self.london_high    = self.london_low    = None
        self.prev_day_high  = self.prev_day_low  = None
        self.prev_week_high = self.prev_week_low = None

        # ── VWAP ───────────────────────────────────────────
        self.vwap_cum_vol = 0.0
        self.vwap_cum_pv  = 0.0
        self.vwap_date    = None

        # ── Live tick / delta ──────────────────────────────
        self.tick_delta             = 0
        self.tick_subscription      = None
        self._tick_stream_available = True
        self.volume_profile: dict[float, int] = {}
        self.vp_date = None
        self._mkt_ticker       = None
        self._last_trade_price = 0.0
        self._delta_last_bar   = 0
        self._last_bar_start   = None

        # ── DOM ────────────────────────────────────────────
        self.dom_ticker              = None
        self.dom_subscription_active = False

        # DOM history for iceberg / spoof / sweep detection
        # Stores snapshots of (price→size) dicts for asks and bids
        # Keyed by time.time() — last 10 snapshots (~50s at 5s cadence)
        self._dom_history: list[dict] = []   # [{ts, asks:{p:s}, bids:{p:s}}]
        self._dom_history_max = DOM_HISTORY_MAX_SNAPSHOTS

        # ── News cache (10-min TTL) ────────────────────────
        self._news_cache:      dict  = {}
        self._news_cache_time: float = 0.0

        # ── IBKR live news headlines (tick 292) ────────────
        # Stores last 10 headlines as [{"time": str, "headline": str, "provider": str}]
        self._ibkr_headlines: list = []
        self._ibkr_headlines_max  = 10

        # ── Opening Range ──────────────────────────────────
        self.or_high            = self.or_low   = None
        self.or_open            = self.or_close = None
        self.or_volume          = None
        self.or_direction: Optional[str] = None
        self.or_date            = None
        self.or_broken_up       = False
        self.or_broken_down     = False
        self.or_break_count     = 0
        self.or_avg_volume_14d  = None
        self.or_relative_volume = None

        # ── OR pullback tracking (Improvement 4/9) ─────────
        self.or_breakout_candle_high  = None
        self.or_breakout_candle_low   = None
        self.or_pullback_in_progress  = False
        self.or_pullback_low          = None   # lowest point of pullback
        self.or_entry_zone_active     = False  # True when OTF entry is valid

        # ── Session context (Improvement 10) ──────────────
        self.session_context: dict = {}

        # ── ICT level cache (refresh on new 5-min bar) ─────
        self._ict_cache:      dict  = {}
        self._ict_cache_time: float = 0.0

        # ── B.1 — Tick state persistence ───────────────────
        # Save tick_delta + volume_profile every 30s. Restore on startup
        # if same trading day so CUM Δ survives restarts.
        self._persist_path      = None
        self._persist_last_save = 0.0
        self._persist_interval  = TICK_STATE_PERSIST_INTERVAL_SECS

    # ─── Tick State Persistence (B.1, B.2, B.3) ────────────

    def _set_persist_path(self) -> None:
        """Set state file path based on config. Lazy — called when needed."""
        if self._persist_path is None:
            try:
                from config import MEMORY_DIR
                import os as _os
                _os.makedirs(MEMORY_DIR, exist_ok=True)
                self._persist_path = _os.path.join(MEMORY_DIR, "tick_state.json")
            except Exception:
                self._persist_path = None

    def restore_tick_state(self) -> None:
        """
        B.2 — Restore tick_delta + volume_profile from disk if same trading day.
        Called once at startup after connect(). If state file is from a
        previous day, the daily-reset logic in on_tick will wipe it cleanly.
        """
        self._set_persist_path()
        if not self._persist_path:
            return
        try:
            import os as _os, json as _json
            if not _os.path.exists(self._persist_path):
                return
            with open(self._persist_path) as f:
                state = _json.load(f)
            today_iso = datetime.now(eastern).date().isoformat()
            saved_date = state.get("date")
            if saved_date != today_iso:
                logger.info(
                    f"Tick state from {saved_date} — different day, starting fresh"
                )
                return
            self.tick_delta       = int(state.get("tick_delta", 0))
            self._delta_last_bar  = int(state.get("delta_last_bar", 0))
            # Volume profile keys are floats — JSON serializes as strings
            vp = state.get("volume_profile", {})
            self.volume_profile   = {float(k): int(v) for k, v in vp.items()}
            self.vp_date          = datetime.now(eastern).date()
            saved_age = state.get("age_seconds_estimate", 0)
            logger.info(
                f"Tick state restored: delta={self.tick_delta:+,d} "
                f"VP_levels={len(self.volume_profile)} "
                f"(saved ~{saved_age}s ago)"
            )
        except Exception as e:
            logger.warning(f"Tick state restore failed (starting fresh): {e}")

    def maybe_persist_tick_state(self) -> None:
        """
        B.1 — Save tick state to disk every _persist_interval seconds.
        Called from the snapshot path (which runs often during entry scans).
        Cheap: ~5ms write to a small JSON file.
        """
        now = time.time()
        if now - self._persist_last_save < self._persist_interval:
            return
        self._set_persist_path()
        if not self._persist_path:
            return
        try:
            import json as _json
            today_iso = datetime.now(eastern).date().isoformat()
            state = {
                "date":                  today_iso,
                "tick_delta":            int(self.tick_delta),
                "delta_last_bar":        int(self._delta_last_bar),
                "volume_profile":        {str(k): int(v) for k, v in self.volume_profile.items()},
                "age_seconds_estimate":  int(self._persist_interval),
                "saved_at":              datetime.now(eastern).isoformat(),
            }
            with open(self._persist_path, "w") as f:
                _json.dump(state, f)
            self._persist_last_save = now
        except Exception as e:
            logger.debug(f"Tick state persist failed: {e}")

    # ─── Connection ────────────────────────────────────────

    def connect(self) -> bool:
        try:
            self.ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
            self.connected = True
            self._setup_contract()

            if LIVE_DATA_ACTIVE:
                self.ib.reqMarketDataType(1)
                logger.info("LIVE DATA MODE — real-time L2 active")
                self._start_tick_stream()
                self._start_dom_stream()
            else:
                self.ib.reqMarketDataType(3)
                logger.info("DELAYED DATA MODE")

            logger.info("Connected to IBKR")
            return True
        except Exception as e:
            logger.error(f"IBKR connection failed: {e}")
            return False

    def _setup_contract(self) -> None:
        try:
            self.contract = Future(conId=CONTRACT_CONID, exchange=EXCHANGE)
            self.ib.qualifyContracts(self.contract)
            if getattr(self.contract, "conId", None):
                logger.info(f"Contract (conId): {self.contract.localSymbol} — {self.contract}")
                return
        except Exception as e:
            logger.warning(f"conId lookup failed ({e}) — falling back to symbol/date")

        self.contract = Future(
            symbol=SYMBOL,
            lastTradeDateOrContractMonth=CONTRACT_EXPIRY,
            exchange=EXCHANGE, currency=CURRENCY,
        )
        self.ib.qualifyContracts(self.contract)
        logger.info(f"Contract (symbol): {self.contract}")

    def disconnect(self) -> None:
        if self.connected:
            if LIVE_DATA_ACTIVE and self.dom_subscription_active:
                try:
                    self.ib.cancelMktDepth(self.contract)
                except Exception as e:
                    logger.debug(f"DOM cancel on disconnect: {e}")
            if self._rt_subscription:
                try:
                    self.ib.cancelRealTimeBars(self._rt_subscription)
                except Exception as e:
                    logger.debug(f"RT bars cancel on disconnect: {e}")
            self.ib.disconnect()
            logger.info("Disconnected from IBKR")

    # ─── Improvement 1: Real-time bar cache ────────────────

    def initialize_bars(self) -> None:
        """
        Fetch all historical bars once at startup.
        After this, reqRealTimeBars keeps bars_1min current automatically.
        Call from main() after connect(), before the trading loop.
        """
        logger.info("Initializing bar cache…")

        def _fetch(duration, bar_size, use_rth=False):
            try:
                return self.ib.reqHistoricalData(
                    self.contract, endDateTime="",
                    durationStr=duration, barSizeSetting=bar_size,
                    whatToShow="TRADES", useRTH=use_rth,
                    formatDate=1, timeout=15,
                )
            except Exception as e:
                logger.error(f"Bar init ({bar_size}): {e}")
                return []

        with self._bar_lock:
            self._bars_1min  = _fetch(INIT_BARS_1MIN_DURATION,  "1 min")
            self._bars_5min  = _fetch(INIT_BARS_5MIN_DURATION,  "5 mins")
            self._bars_15min = _fetch(INIT_BARS_15MIN_DURATION, "15 mins")
            self._bars_daily = _fetch(INIT_BARS_DAILY_DURATION, "1 day", use_rth=True)
            self._last_bars_1min = list(self._bars_1min)

        logger.info(
            f"Bar cache: {len(self._bars_1min)}×1min  "
            f"{len(self._bars_5min)}×5min  "
            f"{len(self._bars_15min)}×15min  "
            f"{len(self._bars_daily)}×daily"
        )

        # Compute static ICT levels now
        self._refresh_ict_levels()
        self._bars_initialized = True

        # Subscribe to real-time 5-sec bars — builds ongoing 1-min bars
        self._start_realtime_bars()

    def _start_realtime_bars(self) -> None:
        """Subscribe to 5-second real-time bars. Accumulate into 1-min bars."""
        try:
            bars = self.ib.reqRealTimeBars(
                self.contract, 5, "TRADES", False
            )

            def on_rt_bar(bars, has_new_bar):
                if not bars:
                    return
                last = bars[-1]
                now_et = _bar_et(last)

                self._rt_bar_buffer.append(last)

                # Accumulate REALTIME_BARS_PER_MINUTE × 5-sec bars → one 1-min bar
                if len(self._rt_bar_buffer) >= REALTIME_BARS_PER_MINUTE:
                    buf = self._rt_bar_buffer[-REALTIME_BARS_PER_MINUTE:]
                    self._rt_bar_buffer = []

                    # Build synthetic 1-min bar
                    class _Bar:
                        pass
                    bar_1m      = _Bar()
                    bar_1m.date = _bar_et(buf[0])   # always a datetime
                    # RealTimeBar uses open_ (not open) — ib_insync naming quirk
                    bar_1m.open   = getattr(buf[0],  'open_', getattr(buf[0],  'open',  0))
                    bar_1m.high   = max(getattr(b,   'high',  0) for b in buf)
                    bar_1m.low    = min(getattr(b,   'low',   0) for b in buf)
                    bar_1m.close  = getattr(buf[-1], 'close', 0)
                    bar_1m.volume = sum(getattr(b,   'volume',0) for b in buf)

                    with self._bar_lock:
                        self._bars_1min.append(bar_1m)
                        self._bars_1min = self._bars_1min[-BARS_1MIN_CACHE_SIZE:]
                        self._last_bars_1min = list(self._bars_1min)

                        # Refresh delta and VWAP on new bar
                        self._update_vwap_incremental(bar_1m, now_et)

                    # Refresh ICT levels every 5-min bar close
                    t = now_et.minute
                    if t % 5 == 0:
                        self._refresh_ict_levels()
                        self._update_or_pullback_tracking()

            bars.updateEvent += on_rt_bar
            self._rt_subscription = bars
            logger.info("Real-time bar subscription active (5-sec bars -> 1-min cache)")

        except Exception as e:
            logger.error(f"Real-time bars error: {e}")

    def _refresh_ict_levels(self) -> None:
        """Recompute ICT levels from cached bars. Called on new 5-min bar."""
        with self._bar_lock:
            bars_5  = list(self._bars_5min)
            bars_1  = list(self._bars_1min)
            bars_15 = list(self._bars_15min)

        price = self._get_last_price()

        self._ict_cache = {
            "fvgs":           self._find_fvgs(bars_5, price),
            "order_blocks":   self._find_order_blocks(bars_5, price),
            "liquidity_pools":self._find_liquidity_pools(bars_5, price),
            "choch":          self._detect_choch(bars_1),
            "inducement":     self._detect_inducement(bars_5, datetime.now(eastern)),
            "structure":      self._analyze_market_structure(bars_15, bars_5),
            "htf_bias":       self._calculate_htf_bias(self._bars_daily, bars_15),
            "mtf_alignment":  self._check_mtf_alignment(bars_1, bars_5, bars_15),
            "mtf_score":      self._check_mtf_score(bars_1, bars_5, bars_15) if FEATURE_MTF_SCORE else {"score": 0, "bull_tfs": 0, "bear_tfs": 0, "direction": "MIXED"},
            "delta_trend":    self._calculate_delta_trend(bars_1),
            "ofi":            self._compute_ofi() if FEATURE_OFI else {"score": 0, "signal": "NEUTRAL", "text": "OFI disabled"},
        }
        self._ict_cache_time = time.time()

    def _get_last_price(self) -> float:
        if self._mkt_ticker:
            p = self._mkt_ticker.last or self._mkt_ticker.close or 0
            if p > 0:
                return p
        if self._bars_1min:
            return self._bars_1min[-1].close
        return 0.0

    # ─── Live data streams ─────────────────────────────────

    def _start_tick_stream(self) -> None:
        if not self.contract or not getattr(self.contract, "conId", None):
            logger.error("Tick stream: contract not qualified — skipping")
            return
        try:
            self._tick_stream_available = True

            def on_tick(ticker):
                """
                ib_insync's reqTickByTickData passes the Ticker object to
                updateEvent handlers. The actual ticks are stored in
                ticker.tickByTicks as a list of TickByTickAllLast objects
                with .price, .size, .time attributes.

                Previous version of this code tried to read .price/.size
                off the Ticker itself, which always returned False on
                hasattr, so the handler exited early and tick_delta never
                incremented despite live data being active.
                """
                today = datetime.now(eastern).date()
                if self.vp_date != today:
                    self.volume_profile  = {}
                    self.tick_delta      = 0
                    self._delta_last_bar = 0
                    self._last_bar_start = datetime.now(eastern).replace(second=0, microsecond=0)
                    self.vp_date = today

                ticks = getattr(ticker, "tickByTicks", None) or []
                if not ticks:
                    return

                # Cache bid/ask once per batch (not per tick) for classification
                try:
                    cur_bid = (self._mkt_ticker.bid if self._mkt_ticker else 0) or 0
                    cur_ask = (self._mkt_ticker.ask if self._mkt_ticker else 0) or 0
                except Exception:
                    cur_bid = cur_ask = 0

                now_et    = datetime.now(eastern)
                bar_start = now_et.replace(second=0, microsecond=0)
                if bar_start != self._last_bar_start:
                    self._delta_last_bar = 0
                    self._last_bar_start = bar_start

                for tick in ticks:
                    price = getattr(tick, "price", 0) or 0
                    size  = getattr(tick, "size",  0) or 0
                    if not price or not size:
                        continue

                    # Classify aggressor side
                    if cur_ask and price >= cur_ask:
                        signed_size = size
                    elif cur_bid and price <= cur_bid:
                        signed_size = -size
                    else:
                        signed_size = size if price >= self._last_trade_price else -size

                    self._last_trade_price = price
                    self.tick_delta       += signed_size
                    self._delta_last_bar  += signed_size

                    rounded = round(price * 4) / 4
                    self.volume_profile[rounded] = self.volume_profile.get(rounded, 0) + size

                # Note: do NOT clear ticker.tickByTicks here. ib_insync
                # replaces the list with each updateEvent, so clearing would
                # actually be a no-op or could race with the next update.

            self.tick_subscription = self.ib.reqTickByTickData(
                self.contract, "AllLast", 0, False
            )
            self.tick_subscription.updateEvent += on_tick
            logger.info("Tick stream started — true bid/ask delta classification active")
        except Exception as e:
            logger.error(f"Tick stream error: {e}")

    def _start_dom_stream(self) -> None:
        if not self.contract or not getattr(self.contract, "conId", None):
            logger.error("DOM stream: contract not qualified — skipping")
            return
        try:
            self.dom_ticker = self.ib.reqMktDepth(
                self.contract, numRows=20, isSmartDepth=False
            )
            self.ib.sleep(1)
            self.dom_subscription_active = True
            logger.info("DOM stream started (20 levels)")
        except Exception as e:
            logger.warning(f"DOM stream unavailable: {e}")
            self.dom_subscription_active = False

    # ─── Main snapshot (Improvement 1: fast assembly) ──────

    def get_snapshot(
        self,
        current_position: int   = 0,
        daily_pnl:        float = 0.0,
        daily_loss_remaining: float = 500.0,
        consecutive_losses:   int   = 0,
    ) -> dict:
        """
        Assemble market snapshot from CACHED data — no blocking bar fetches.
        Target: < 1 second assembly time.
        """
        try:
            now_et    = datetime.now(eastern)
            time_str  = now_et.strftime("%H:%M:%S")

            # Use cached bars — no reqHistoricalData calls
            with self._bar_lock:
                bars_1min  = list(self._bars_1min)
                bars_5min  = list(self._bars_5min)
                bars_15min = list(self._bars_15min)
                bars_daily = list(self._bars_daily)

            # If bars not initialized yet, do a one-time fetch
            if not self._bars_initialized:
                self.initialize_bars()
                with self._bar_lock:
                    bars_1min  = list(self._bars_1min)
                    bars_5min  = list(self._bars_5min)
                    bars_15min = list(self._bars_15min)
                    bars_daily = list(self._bars_daily)

            # Live price from MNQ futures — no generic ticks (futures don't support tick 292)
            ticker = self.ib.reqMktData(self.contract, "", False, False)
            self._mkt_ticker = ticker

            # V4.1 — Subscribe to news via QQQ (Nasdaq ETF) using tick 292.
            # IBKR only allows news subscriptions on stocks/ETFs, not futures.
            # QQQ is the closest liquid proxy for Nasdaq news flow.
            if not hasattr(self, '_news_handler_wired'):
                try:
                    from ib_insync import Stock
                    qqq = Stock("QQQ", "SMART", "USD")
                    self.ib.qualifyContracts(qqq)
                    self._news_ticker = self.ib.reqMktData(qqq, "292", False, False)
                    self.ib.tickNewsEvent += self._on_tick_news
                    logger.info("IBKR live news: subscribed via QQQ (tick 292)")
                except Exception as e:
                    logger.debug(f"IBKR news subscription failed: {e}")
                self._news_handler_wired = True

            self.ib.sleep(SNAPSHOT_ASSEMBLY_SLEEP_SECS)

            last_price = ticker.last or ticker.close or (bars_1min[-1].close if bars_1min else 0)
            bid        = ticker.bid  or 0
            ask        = ticker.ask  or 0
            volume     = ticker.volume or 0

            # News cache (10-min TTL — events list itself rarely changes,
            # but the time-sensitive fields need to be fresh)
            if time.time() - self._news_cache_time > NEWS_CACHE_TTL_SECS:
                try:
                    self._news_cache      = get_news_snapshot(self.ib)
                    self._news_cache_time = time.time()
                except Exception as e:
                    logger.warning(f"News refresh failed: {e}")
            else:
                # Refresh just the timing-sensitive fields (countdown, danger zone)
                # without re-fetching the full event list or IBKR bulletins (which
                # would block for 2s). Cheap, runs every snapshot.
                try:
                    fresh = get_news_snapshot(ib=None)
                    self._news_cache.update({
                        "news_danger_zone":   fresh.get("news_danger_zone",   False),
                        "next_high_impact":   fresh.get("next_high_impact",   None),
                        "next_event_full":    fresh.get("next_event_full",    None),
                        "next_event_minutes": fresh.get("next_event_minutes", None),
                        "recent_event":       fresh.get("recent_event",       None),
                    })
                except Exception as e:
                    logger.debug(f"News timing refresh failed: {e}")

            # Session levels
            self._update_session_levels(bars_1min, now_et)

            # Opening range
            self._calculate_opening_range(bars_5min, now_et)

            # OR pullback tracking
            self._update_or_pullback_tracking()

            # VWAP
            vwap = self._calculate_vwap(bars_1min, now_et)

            # ICT levels from cache (refreshed on bar close)
            ict = self._ict_cache if self._ict_cache else {}

            # Delta
            if self._tick_stream_available and LIVE_DATA_ACTIVE:
                delta_info = self._get_true_delta()
            else:
                delta_info = self._calculate_delta(bars_1min)

            # Volume profile
            # Volume profile — structured signals
            if LIVE_DATA_ACTIVE:
                vp_signals = self._compute_volume_profile(last_price)
            else:
                vp_signals = {"vp_text": "N/A (live mode only)", "vp_available": False}
            vp_text = vp_signals.get("vp_text", "")

            # DOM — structured signals
            if LIVE_DATA_ACTIVE and self.dom_subscription_active:
                dom_signals = self._compute_dom_signals()
            else:
                dom_signals = {
                    "dom_text": "DOM pending — requires CME Level 2 subscription",
                    "dom_available": False,
                }
            dom_text = dom_signals.get("dom_text", "")

            session_high = max(b.high for b in bars_1min) if bars_1min else 0
            session_low  = min(b.low  for b in bars_1min) if bars_1min else 0

            # B.1 — Persist tick state every 30s (internal throttle in method)
            self.maybe_persist_tick_state()

            snapshot = {
                "timestamp":     datetime.now().isoformat(),
                "time_et":       time_str,
                "session_phase": self._get_session_phase(now_et),
                "killzone":      self._get_killzone(now_et),
                "amd_phase":     self._determine_amd_phase(now_et),
                "data_mode":     "LIVE" if LIVE_DATA_ACTIVE else "DELAYED",

                # Price
                "last_price":   last_price,
                "bid":          bid,
                "ask":          ask,
                "bid_size":     ticker.bidSize,
                "ask_size":     ticker.askSize,
                "volume":       volume,
                "session_high": session_high,
                "session_low":  session_low,
                "vwap":         round(vwap, 2) if vwap else "N/A",

                # HTF
                "htf_bias":         ict.get("htf_bias", self._calculate_htf_bias(bars_daily, bars_15min)),
                "market_structure": ict.get("structure", ""),

                # ICT (from cache)
                "choch":           ict.get("choch", ""),
                "inducement":      ict.get("inducement", ""),
                "fair_value_gaps": ict.get("fvgs", ""),
                "order_blocks":    ict.get("order_blocks", ""),
                "liquidity_pools": ict.get("liquidity_pools", ""),
                "session_levels":  self._format_session_levels(last_price),

                # MTF alignment (Improvement 8)
                "mtf_alignment":   ict.get("mtf_alignment", ""),
                "mtf_score":       ict.get("mtf_score", {"score": 0, "bull_tfs": 0, "bear_tfs": 0, "direction": "MIXED"}),

                # V4.0 — Order Flow Imbalance
                "ofi":             ict.get("ofi", {"score": 0, "signal": "NEUTRAL", "text": "OFI unavailable"}),

                # Delta trend (Improvement 5)
                "delta_trend":     ict.get("delta_trend", ""),

                # Opening range
                "opening_range":      self._format_opening_range(last_price),
                "or_high":            self.or_high,
                "or_low":             self.or_low,
                "or_open":            self.or_open,
                "or_close":           self.or_close,
                "or_direction":       self.or_direction,
                "or_broken_up":       self.or_broken_up,
                "or_broken_down":     self.or_broken_down,
                "or_break_attempts":  self.or_break_count,
                "or_relative_volume": self.or_relative_volume,
                "or_volume":          self.or_volume,

                # V3.0 — Minutes elapsed since OR set (for bias decay logic)
                "mins_since_or": (
                    (now_et - now_et.replace(hour=9, minute=35, second=0, microsecond=0)).total_seconds() / 60
                    if self.or_direction and now_et.hour >= 9
                    else 999
                ),

                # OR pullback tracking (Improvement 4)
                "or_breakout_candle_low":  self.or_breakout_candle_low,
                "or_pullback_in_progress": self.or_pullback_in_progress,
                "or_pullback_low":         self.or_pullback_low,
                "or_entry_zone_active":    self.or_entry_zone_active,

                # Volume profile — structured + text
                "volume_profile":  vp_text,
                "vp_poc":          vp_signals.get("vp_poc"),
                "vp_vah":          vp_signals.get("vp_vah"),
                "vp_val":          vp_signals.get("vp_val"),
                "vp_status":       vp_signals.get("vp_status", ""),
                "vp_above_vah":    vp_signals.get("vp_above_vah", False),
                "vp_below_val":    vp_signals.get("vp_below_val", False),
                "vp_inside_va":    vp_signals.get("vp_inside_va", False),

                # DOM — structured + text (Session 4: full 20 levels + advanced signals)
                "dom":                 dom_signals.get("dom_text", ""),
                "dom_available":       dom_signals.get("dom_available", False),
                "dom_resistance_wall": dom_signals.get("dom_resistance_wall"),
                "dom_support_wall":    dom_signals.get("dom_support_wall"),
                "dom_buy_pressure":    dom_signals.get("dom_buy_pressure", 0.5),
                "dom_imbalance":       dom_signals.get("dom_imbalance", "NEUTRAL"),
                "dom_vacuum_above":    dom_signals.get("dom_vacuum_above", False),
                "dom_vacuum_below":    dom_signals.get("dom_vacuum_below", False),
                "dom_nearest_magnet":  dom_signals.get("dom_nearest_magnet"),
                "dom_cluster_above":   dom_signals.get("dom_cluster_above"),
                "dom_cluster_below":   dom_signals.get("dom_cluster_below"),
                "dom_iceberg_ask":     dom_signals.get("dom_iceberg_ask"),
                "dom_iceberg_bid":     dom_signals.get("dom_iceberg_bid"),
                "dom_spoof_ask":       dom_signals.get("dom_spoof_ask"),
                "dom_spoof_bid":       dom_signals.get("dom_spoof_bid"),
                "dom_sweep_up":        dom_signals.get("dom_sweep_up", False),
                "dom_sweep_down":      dom_signals.get("dom_sweep_down", False),

                # Candles
                "candles":          self._format_candles(bars_1min, bars_5min),
                "candle_patterns":  self._detect_candle_patterns(bars_1min, bars_5min, last_price),

                # Delta — C.2: label whether delta is real bid/ask classification
                # or just signed-volume approximation (delayed data)
                "cumulative_delta": delta_info["cumulative_delta"],
                "delta_last_bar":   delta_info["delta_last_bar"],
                "large_prints":     delta_info["large_prints"],
                "delta_is_live":    LIVE_DATA_ACTIVE and self._tick_stream_available,

                # News — scheduled events + IBKR live headlines
                "news_text":          self._news_cache.get("news_text", "News unavailable"),
                "news_danger_zone":   self._news_cache.get("news_danger_zone",   False),
                "next_high_impact":   self._news_cache.get("next_high_impact",   None),
                "next_event_full":    self._news_cache.get("next_event_full",    None),
                "next_event_minutes": self._news_cache.get("next_event_minutes", None),
                "recent_event":       self._news_cache.get("recent_event",       None),
                "events_today":       self._news_cache.get("events_today",       []),
                # V4.1 — IBKR live headlines (tick 292)
                "ibkr_headlines":     self.get_ibkr_headlines(5),
                "ibkr_headlines_text": self.get_ibkr_headlines_text(3),

                # Bar data for dashboard chart with per-bar VWAP
                "bars_1min": self._serialize_bars_with_vwap(list(self._bars_1min)[-195:]),
                "bars_5min": self._serialize_bars_with_vwap(list(self._bars_5min)[-195:]),
                # Current forming bar (uses live price)
                "currentBarOpen": self._bars_1min[-1].close if self._bars_1min else current_price,

                # Risk
                "current_position":    current_position,
                "daily_pnl":           round(daily_pnl, 2),
                "daily_loss_remaining":round(daily_loss_remaining, 2),
                "consecutive_losses":  consecutive_losses,
            }

            # Record snapshot to disk for backtest replay
            _recorder.record_snapshot(snapshot)
            return snapshot

        except Exception as e:
            logger.error(f"Snapshot error: {e}")
            return {}

    # ─── Improvement 4: OR pullback tracking ───────────────

    def _update_or_pullback_tracking(self) -> None:
        """
        Track OR breakout → pullback → entry zone sequence.
        Stage 1: Confirmed close above OR high (bullish) → record breakout candle low
        Stage 2: Price pulls back toward OR high → pullback_in_progress = True
        Stage 3: New 1-min close above pullback low → entry_zone_active = True
        """
        if not self.or_high or not self.or_direction:
            return

        with self._bar_lock:
            bars = list(self._bars_1min[-20:])
        if not bars:
            return

        price = bars[-1].close if bars else 0

        if self.or_direction == "BULL" and self.or_broken_up:
            # Find the first candle that closed above OR high
            if self.or_breakout_candle_low is None:
                for b in bars:
                    bt = _bar_et(b)
                    if bt.date() == datetime.now(eastern).date():
                        if b.close > self.or_high:
                            self.or_breakout_candle_low = b.low
                            break

            if self.or_breakout_candle_low:
                # Check if price has pulled back toward OR high
                if price < (self.or_high + (self.or_high - self.or_low) * OR_PULLBACK_THRESHOLD_PCT):
                    self.or_pullback_in_progress = True
                    # Track the lowest point of the pullback
                    if self.or_pullback_low is None or price < self.or_pullback_low:
                        self.or_pullback_low = price

                # Entry zone: price reclaims above pullback low with a close
                if self.or_pullback_in_progress and self.or_pullback_low:
                    if bars[-1].close > self.or_pullback_low and bars[-1].close > self.or_high:
                        self.or_entry_zone_active = True
                    else:
                        self.or_entry_zone_active = False

        elif self.or_direction == "BEAR" and self.or_broken_down:
            if self.or_breakout_candle_high is None:
                for b in bars:
                    bt = _bar_et(b)
                    if bt.date() == datetime.now(eastern).date():
                        if b.close < self.or_low:
                            self.or_breakout_candle_high = b.high
                            break

            if self.or_breakout_candle_high:
                if price > (self.or_low - (self.or_high - self.or_low) * OR_PULLBACK_THRESHOLD_PCT):
                    self.or_pullback_in_progress = True
                    if self.or_pullback_low is None or price > self.or_pullback_low:
                        self.or_pullback_low = price

                if self.or_pullback_in_progress and self.or_pullback_low:
                    if bars[-1].close < self.or_pullback_low and bars[-1].close < self.or_low:
                        self.or_entry_zone_active = True
                    else:
                        self.or_entry_zone_active = False

    # ─── Improvement 8: MTF alignment ──────────────────────

    def _check_mtf_alignment(self, bars_1min, bars_5min, bars_15min) -> str:
        """
        Check if all timeframes agree on direction.
        Returns: BULLISH_ALIGNED / BEARISH_ALIGNED / PARTIAL / CONFLICTED
        """
        try:
            signals = {"bull": 0, "bear": 0}

            # 1-min: CHoCH direction
            if bars_1min and len(bars_1min) >= 5:
                highs = [b.high for b in bars_1min[-5:]]
                lows  = [b.low  for b in bars_1min[-5:]]
                if highs[-1] > highs[-3] and lows[-1] > lows[-3]:
                    signals["bull"] += 1
                elif highs[-1] < highs[-3] and lows[-1] < lows[-3]:
                    signals["bear"] += 1

            # 5-min: higher low intact
            if bars_5min and len(bars_5min) >= 6:
                recent_lows = [b.low for b in bars_5min[-6:]]
                if recent_lows[-1] > recent_lows[-3]:
                    signals["bull"] += 1
                elif recent_lows[-1] < recent_lows[-3]:
                    signals["bear"] += 1

            # 15-min: not in bearish impulse
            if bars_15min and len(bars_15min) >= 4:
                last4 = bars_15min[-4:]
                if last4[-1].close > last4[-1].open and last4[-1].close > last4[0].open:
                    signals["bull"] += 1
                elif last4[-1].close < last4[-1].open and last4[-1].close < last4[0].open:
                    signals["bear"] += 1

            b, bear = signals["bull"], signals["bear"]
            if b == 3:   return "BULLISH_ALIGNED ✓ (all 3 TF agree — highest confidence)"
            if bear == 3:return "BEARISH_ALIGNED ✓ (all 3 TF agree — highest confidence)"
            if b == 2:   return f"PARTIAL_BULL (2/3 TF bullish — proceed with caution)"
            if bear == 2:return f"PARTIAL_BEAR (2/3 TF bearish — proceed with caution)"
            return "CONFLICTED — timeframes disagree, avoid entries"

        except Exception:
            return "MTF alignment: insufficient data"

    def _check_mtf_score(self, bars_1min, bars_5min, bars_15min) -> dict:
        """
        C.1 — MTF correlation score: quantitative version of _check_mtf_alignment.
        Returns a dict with:
          score      : 0-100 (100 = all 3 TF fully agree)
          bull_tfs   : count of bullish TFs (0-3)
          bear_tfs   : count of bearish TFs (0-3)
          direction  : "BULL" | "BEAR" | "MIXED"

        Score formula:
          All 3 agree bull/bear  = 100
          2/3 agree              = 67
          1/3 (one signal)       = 33
          No signal or conflicted = 0
        """
        try:
            bull_tfs = 0
            bear_tfs = 0

            if bars_1min and len(bars_1min) >= 5:
                highs = [b.high for b in bars_1min[-5:]]
                lows  = [b.low  for b in bars_1min[-5:]]
                if highs[-1] > highs[-3] and lows[-1] > lows[-3]:
                    bull_tfs += 1
                elif highs[-1] < highs[-3] and lows[-1] < lows[-3]:
                    bear_tfs += 1

            if bars_5min and len(bars_5min) >= 6:
                recent_lows = [b.low for b in bars_5min[-6:]]
                if recent_lows[-1] > recent_lows[-3]:
                    bull_tfs += 1
                elif recent_lows[-1] < recent_lows[-3]:
                    bear_tfs += 1

            if bars_15min and len(bars_15min) >= 4:
                last4 = bars_15min[-4:]
                if last4[-1].close > last4[-1].open and last4[-1].close > last4[0].open:
                    bull_tfs += 1
                elif last4[-1].close < last4[-1].open and last4[-1].close < last4[0].open:
                    bear_tfs += 1

            dominant = max(bull_tfs, bear_tfs)
            score    = round((dominant / 3) * 100)
            direction = "BULL" if bull_tfs > bear_tfs else ("BEAR" if bear_tfs > bull_tfs else "MIXED")
            return {"score": score, "bull_tfs": bull_tfs, "bear_tfs": bear_tfs, "direction": direction}
        except Exception:
            return {"score": 0, "bull_tfs": 0, "bear_tfs": 0, "direction": "MIXED"}

    # ─── Improvement 5: Delta trend ────────────────────────

    def _calculate_delta_trend(self, bars: list) -> str:
        """Detect distribution vs accumulation across recent bars."""
        if not bars or len(bars) < 4:
            return "insufficient data"
        deltas = [b.volume if b.close > b.open else -b.volume for b in bars[-6:]]
        if len(deltas) < 4:
            return "insufficient data"
        # Consecutive increases
        if all(deltas[i] > deltas[i-1] for i in range(1, len(deltas))):
            return "ACCELERATING ▲ — strong institutional buying, trend strengthening"
        if all(deltas[i] < deltas[i-1] for i in range(1, len(deltas))):
            return "DECELERATING ▼ — distribution detected, trend weakening"
        # Check last 3 bars for recent shift
        recent = deltas[-3:]
        if all(d > 0 for d in recent):
            return "POSITIVE (recent 3 bars) — buyers in control"
        if all(d < 0 for d in recent):
            return "NEGATIVE (recent 3 bars) — sellers in control"
        return "MIXED — no clear directional pressure"

    # ─── VWAP incremental update ───────────────────────────

    def _update_vwap_incremental(self, bar, now_et: datetime) -> None:
        today = now_et.date()
        if self.vwap_date != today:
            self.vwap_cum_vol = 0.0
            self.vwap_cum_pv  = 0.0
            self.vwap_date    = today

        tp = (bar.high + bar.low + bar.close) / 3
        self.vwap_cum_pv  += tp * bar.volume
        self.vwap_cum_vol += bar.volume

    def _serialize_bars_with_vwap(self, bars: list) -> list:
        """Serialize bars with cumulative intraday VWAP per bar."""
        result = []
        cum_pv = 0.0
        cum_vol = 0.0
        last_date = None
        for b in bars:
            try:
                bt = _bar_et(b)
                if bt.date() != last_date:
                    cum_pv = 0.0
                    cum_vol = 0.0
                    last_date = bt.date()
                vol = getattr(b, 'volume', 0) or 0
                tp  = (b.high + b.low + b.close) / 3
                cum_pv  += tp * vol
                cum_vol += vol
                vwap = round(cum_pv / cum_vol, 2) if cum_vol > 0 else None
            except Exception:
                vwap = None
            result.append({
                "t": str(b.date)[:16], "o": b.open, "h": b.high,
                "l": b.low, "c": b.close, "v": vol,
                "vwap": vwap, "forming": False
            })
        return result

    def _calculate_vwap(self, bars: list, now_et: datetime) -> float:
        try:
            today    = now_et.date()
            rth_bars = []
            for bar in bars:
                bt = _bar_et(bar)
                if bt.date() == today and bt.hour * 100 + bt.minute >= SESSION_MARKET_OPEN_TIME:
                    rth_bars.append(bar)
            if not rth_bars:
                return 0.0
            cum_pv  = sum(((b.high + b.low + b.close) / 3) * b.volume for b in rth_bars)
            cum_vol = sum(b.volume for b in rth_bars)
            return cum_pv / cum_vol if cum_vol else 0.0
        except Exception:
            return 0.0

    # ─── Delta ─────────────────────────────────────────────

    def _get_true_delta(self) -> dict:
        if not getattr(self, "_tick_stream_available", True):
            return self._calculate_delta(self._last_bars_1min)
        cum = self.tick_delta
        bar = getattr(self, "_delta_last_bar", 0)
        if cum > 0 and bar < -DELTA_DIVERGENCE_THRESHOLD:
            div = "⚠️ DIVERGENCE: cumulative bullish but last bar selling hard"
        elif cum < 0 and bar > DELTA_DIVERGENCE_THRESHOLD:
            div = "⚠️ DIVERGENCE: cumulative bearish but last bar buying hard"
        else:
            div = "Aligned"
        return {
            "cumulative_delta": cum,
            "delta_last_bar":   bar,
            "large_prints":     f"Live tick delta | {div}",
        }

    def _calculate_delta(self, bars: list) -> dict:
        if not bars:
            return {"cumulative_delta": 0, "delta_last_bar": 0, "large_prints": "None"}
        cumulative = sum(b.volume if b.close > b.open else -b.volume for b in bars)
        lb         = bars[-1]
        last_delta = lb.volume if lb.close > lb.open else -lb.volume
        avg_vol    = sum(b.volume for b in bars) / len(bars)
        large_n    = sum(1 for b in bars[-5:] if b.volume > avg_vol * 2)
        return {
            "cumulative_delta": cumulative,
            "delta_last_bar":   last_delta,
            "large_prints":     f"{large_n} large prints in last 5 bars" if large_n else "None",
        }

    # ─── Session levels ────────────────────────────────────

    def _update_session_levels(self, bars_1min: list, now_et: datetime) -> None:
        if not bars_1min:
            return
        try:
            today     = now_et.date()
            yesterday = (now_et - timedelta(days=1)).date()

            week_mon      = today - timedelta(days=today.weekday())
            prev_week_mon = week_mon - timedelta(days=7)
            prev_week_fri = prev_week_mon + timedelta(days=4)

            asia_bars, london_bars, prev_day_bars, prev_week_bars = [], [], [], []

            for bar in bars_1min:
                bt   = _bar_et(bar)
                h    = bt.hour
                date = bt.date()
                if (date == yesterday and h >= 18) or (date == today and h < 7):
                    asia_bars.append(bar)
                if date == today and 3 <= h < 8:
                    london_bars.append(bar)
                if date == yesterday and 9 <= h < 16:
                    prev_day_bars.append(bar)
                if prev_week_mon <= date <= prev_week_fri and 9 <= h < 16:
                    prev_week_bars.append(bar)

            if asia_bars:
                self.asia_high = max(b.high for b in asia_bars)
                self.asia_low  = min(b.low  for b in asia_bars)
            if london_bars:
                self.london_high = max(b.high for b in london_bars)
                self.london_low  = min(b.low  for b in london_bars)
            if prev_day_bars:
                self.prev_day_high = max(b.high for b in prev_day_bars)
                self.prev_day_low  = min(b.low  for b in prev_day_bars)
            if prev_week_bars:
                self.prev_week_high = max(b.high for b in prev_week_bars)
                self.prev_week_low  = min(b.low  for b in prev_week_bars)
        except Exception as e:
            logger.error(f"Session levels error: {e}")

    # ─── Opening Range ─────────────────────────────────────

    def _calculate_opening_range(self, bars_5min: list, now_et: datetime) -> None:
        try:
            today = now_et.date()
            if self.or_date != today:
                self.or_high = self.or_low = self.or_open = self.or_close = None
                self.or_volume = self.or_direction = None
                self.or_date = today
                self.or_broken_up = self.or_broken_down = False
                self.or_break_count = 0
                self.or_avg_volume_14d = self.or_relative_volume = None
                self.or_breakout_candle_high = self.or_breakout_candle_low = None
                self.or_pullback_in_progress = False
                self.or_pullback_low = None
                self.or_entry_zone_active = False

            if not bars_5min:
                return

            hist_volumes: list = []

            for bar in bars_5min:
                bt = _bar_et(bar)
                if bt.hour == 9 and bt.minute == 30:
                    if bt.date() == today:
                        self.or_high  = bar.high
                        self.or_low   = bar.low
                        self.or_open  = bar.open
                        self.or_close = bar.close
                        self.or_volume = bar.volume
                    else:
                        hist_volumes.append(bar.volume)

            if self.or_open is not None and self.or_close is not None:
                if self.or_close > self.or_open:
                    self.or_direction = "BULL"
                elif self.or_close < self.or_open:
                    self.or_direction = "BEAR"
                else:
                    self.or_direction = "DOJI"

            if hist_volumes and self.or_volume:
                avg = sum(hist_volumes[-14:]) / len(hist_volumes[-14:])
                self.or_avg_volume_14d  = avg
                self.or_relative_volume = (self.or_volume / avg * 100) if avg else 0

            # P0.3 FIX — compute break stats idempotently each call.
            # Previously this incremented or_break_count cumulatively on every
            # snapshot, producing absurdly inflated counts (e.g. 11388 by midday).
            if self.or_high and self.or_low:
                failed_breaks = 0
                broken_up = broken_dn = False
                for bar in bars_5min:
                    bt = _bar_et(bar)
                    if bt.date() != today:
                        continue
                    if bt.hour < 9 or (bt.hour == 9 and bt.minute <= 30):
                        continue
                    # Failed break-up: high pierced but close back inside
                    if bar.high > self.or_high and bar.close <= self.or_high:
                        failed_breaks += 1
                    # Failed break-down: low pierced but close back inside
                    if bar.low < self.or_low and bar.close >= self.or_low:
                        failed_breaks += 1
                    if bar.close > self.or_high:
                        broken_up = True
                    if bar.close < self.or_low:
                        broken_dn = True
                self.or_break_count = failed_breaks
                self.or_broken_up   = broken_up
                self.or_broken_down = broken_dn

        except Exception as e:
            logger.error(f"Opening range error: {e}")

    def _format_opening_range(self, current_price) -> str:
        if not self.or_high or not self.or_low:
            return "Opening range: not yet available (waiting for 9:30 ET)"

        or_range = round(self.or_high - self.or_low, 2)
        rv       = self.or_relative_volume

        if rv is None:
            rv_text = "Relative volume: calculating…"
        elif rv >= 300:
            rv_text = f"Relative volume: {rv:.0f}% — EXCEPTIONAL"
        elif rv >= 200:
            rv_text = f"Relative volume: {rv:.0f}% — HIGH"
        elif rv >= 100:
            rv_text = f"Relative volume: {rv:.0f}% — ABOVE AVERAGE"
        else:
            rv_text = f"Relative volume: {rv:.0f}% — BELOW AVERAGE (skip ORB today)"

        dir_map = {
            "BULL": f"First candle BULLISH (O:{self.or_open} C:{self.or_close}) → ONLY LONG above {self.or_high}",
            "BEAR": f"First candle BEARISH (O:{self.or_open} C:{self.or_close}) → ONLY SHORT below {self.or_low}",
            "DOJI": "First candle DOJI → NO ORB TRADES TODAY",
        }
        dir_text = dir_map.get(self.or_direction or "", "Direction: calculating…")

        if self.or_broken_up:
            status = f"CONFIRMED BREAK UP above {self.or_high} ✓"
        elif self.or_broken_down:
            status = f"CONFIRMED BREAK DOWN below {self.or_low} ✓"
        else:
            status = f"INSIDE range — {self.or_break_count} failed attempt(s)"

        # Pullback entry zone
        pullback_text = ""
        if self.or_entry_zone_active:
            pullback_text = f"\n  ★ ENTRY ZONE ACTIVE — pullback to OR level complete, stop below {self.or_pullback_low}"
        elif self.or_pullback_in_progress:
            pullback_text = f"\n  ⏳ Pullback in progress (low: {self.or_pullback_low}) — wait for 1-min close above pullback low"

        return (
            f"OPENING RANGE BREAKOUT (Zarattini/Barbon/Aziz 2024):\n"
            f"  OR High: {self.or_high}  |  OR Low: {self.or_low}  |  Range: {or_range} pts\n"
            f"  {dir_text}\n"
            f"  Status: {status}\n"
            f"  {rv_text}"
            f"{pullback_text}"
        )

    # ─── HTF bias & structure ──────────────────────────────

    def _calculate_htf_bias(self, bars_daily: list, bars_15min: list) -> str:
        try:
            lines = []
            if bars_daily and len(bars_daily) >= 3:
                last3 = bars_daily[-3:]
                if last3[-1].close > last3[-2].close > last3[-3].close:
                    trend = "BULLISH (3 consecutive higher closes)"
                elif last3[-1].close < last3[-2].close < last3[-3].close:
                    trend = "BEARISH (3 consecutive lower closes)"
                else:
                    trend = "MIXED/NEUTRAL"
                rh = max(b.high for b in bars_daily[-5:])
                rl = min(b.low  for b in bars_daily[-5:])
                lines += [f"Daily: {trend}", f"5-day range: {rl} – {rh}"]

            if bars_15min and len(bars_15min) >= 8:
                last8  = bars_15min[-8:]
                highs  = [b.high for b in last8]
                lows   = [b.low  for b in last8]
                if highs[-1] > highs[-4] and lows[-1] > lows[-4]:
                    s15 = "BULLISH (HH/HL on 15min)"
                elif highs[-1] < highs[-4] and lows[-1] < lows[-4]:
                    s15 = "BEARISH (LH/LL on 15min)"
                else:
                    s15 = "RANGING on 15min"
                lines.append(f"15min: {s15}")

            return "\n".join(lines) if lines else "Insufficient data for HTF bias"
        except Exception:
            return "HTF bias unavailable"

    def _analyze_market_structure(self, bars_15min: list, bars_5min: list) -> str:
        try:
            lines = []
            if bars_5min and len(bars_5min) >= 6:
                last6 = bars_5min[-6:]
                swing_highs, swing_lows = [], []
                for i in range(1, len(last6) - 1):
                    if last6[i].high > last6[i-1].high and last6[i].high > last6[i+1].high:
                        swing_highs.append(last6[i].high)
                    if last6[i].low < last6[i-1].low and last6[i].low < last6[i+1].low:
                        swing_lows.append(last6[i].low)
                if len(swing_highs) >= 2:
                    lines.append("5min: HH (bullish BOS)" if swing_highs[-1] > swing_highs[-2] else "5min: LH (bearish CHoCH)")
                if len(swing_lows) >= 2:
                    lines.append("5min: HL (bullish)" if swing_lows[-1] > swing_lows[-2] else "5min: LL (bearish)")
            return "\n".join(lines) if lines else "Structure unclear"
        except Exception:
            return "Structure unavailable"

    # ─── ICT concepts ──────────────────────────────────────

    def _detect_choch(self, bars_1min: list) -> str:
        if not bars_1min or len(bars_1min) < 5:
            return "CHoCH: insufficient data"
        try:
            recent = bars_1min[-10:]
            highs  = [b.high for b in recent]
            lows   = [b.low  for b in recent]
            bull   = highs[-1] > highs[-2] > highs[-3]
            bear   = lows[-1]  < lows[-2]  < lows[-3]
            if bull and not bear:
                return "CHoCH BULLISH ✓ — 1-min HH, reversal confirmed — LONG at OB/FVG"
            if bear and not bull:
                return "CHoCH BEARISH ✓ — 1-min LL, reversal confirmed — SHORT at OB/FVG"
            return "CHoCH NEUTRAL — no clear shift on 1-min"
        except Exception as e:
            return f"CHoCH error: {e}"

    def _detect_inducement(self, bars_5min: list, now_et: datetime) -> str:
        if not bars_5min or len(bars_5min) < 6:
            return "Inducement: insufficient data"
        try:
            today      = now_et.date()
            today_bars = [b for b in bars_5min if _bar_et(b).date() == today]
            if len(today_bars) < 4:
                return "Inducement: building data"
            recent  = today_bars[-8:]
            s_high  = max(b.high for b in recent)
            s_low   = min(b.low  for b in recent)
            h_tests = sum(1 for b in recent if b.high >= s_high * 0.9998)
            l_tests = sum(1 for b in recent if b.low  <= s_low  * 1.0002)
            if h_tests >= 3:
                return f"INDUCEMENT at highs — {s_high:.2f} tested {h_tests}x. Expect sweep and reversal SHORT."
            if l_tests >= 3:
                return f"INDUCEMENT at lows — {s_low:.2f} tested {l_tests}x. Expect sweep and reversal LONG."
            return f"No inducement — high {s_high:.2f}×{h_tests}, low {s_low:.2f}×{l_tests}"
        except Exception as e:
            return f"Inducement error: {e}"

    def _find_fvgs(self, bars: list, current_price: float) -> str:
        try:
            if not bars or len(bars) < 3:
                return "No FVGs detected"
            fvgs = []
            for i in range(1, len(bars) - 1):
                prev, nxt = bars[i-1], bars[i+1]
                if nxt.low > prev.high:
                    mid = (nxt.low + prev.high) / 2
                    if abs(current_price - mid) < FVG_PROXIMITY_POINTS:
                        inside = prev.high <= current_price <= nxt.low
                        fvgs.append(f"BULL FVG: {prev.high:.2f}-{nxt.low:.2f} (mid:{mid:.2f}) {'★ INSIDE' if inside else f'dist:{abs(current_price-mid):.1f}pts'}")
                if nxt.high < prev.low:
                    mid = (prev.low + nxt.high) / 2
                    if abs(current_price - mid) < FVG_PROXIMITY_POINTS:
                        inside = nxt.high <= current_price <= prev.low
                        fvgs.append(f"BEAR FVG: {nxt.high:.2f}-{prev.low:.2f} (mid:{mid:.2f}) {'★ INSIDE' if inside else f'dist:{abs(current_price-mid):.1f}pts'}")
            return "\n".join(fvgs[-4:]) if fvgs else "No nearby FVGs"
        except Exception:
            return "FVG unavailable"

    def _find_order_blocks(self, bars: list, current_price: float) -> str:
        try:
            if not bars or len(bars) < 4:
                return "No order blocks detected"
            obs = []
            for i in range(1, len(bars) - 2):
                c, n1, n2 = bars[i], bars[i+1], bars[i+2]
                if (c.close < c.open and n1.close > n1.open and n2.close > n2.open
                        and n1.close - n1.open > (c.open - c.close) * 1.5):
                    mid  = (c.open + c.close) / 2
                    if abs(current_price - mid) < OB_PROXIMITY_POINTS:
                        inside = c.close <= current_price <= c.open
                        obs.append(f"BULL OB: {c.close:.2f}-{c.open:.2f} {'★ AT OB' if inside else f'dist:{abs(current_price-mid):.1f}pts'}")
                if (c.close > c.open and n1.close < n1.open and n2.close < n2.open
                        and n1.open - n1.close > (c.close - c.open) * 1.5):
                    mid  = (c.close + c.open) / 2
                    if abs(current_price - mid) < OB_PROXIMITY_POINTS:
                        inside = c.open <= current_price <= c.close
                        obs.append(f"BEAR OB: {c.open:.2f}-{c.close:.2f} {'★ AT OB' if inside else f'dist:{abs(current_price-mid):.1f}pts'}")
            return "\n".join(obs[-4:]) if obs else "No nearby order blocks"
        except Exception:
            return "OB unavailable"

    def _detect_candle_patterns(
        self, bars_1min: list, bars_5min: list, current_price: float
    ) -> str:
        """
        Detect candlestick patterns on 5-min (last 3 bars) and 1-min (last 5 bars).
        Returns a formatted string for the snapshot / Claude prompt.
        """
        try:
            or_dir = self.or_direction or ""
            patterns = []

            def _body(b):
                return abs(b.close - b.open)

            def _upper_wick(b):
                return b.high - max(b.open, b.close)

            def _lower_wick(b):
                return min(b.open, b.close) - b.low

            def _is_bull(b):
                return b.close >= b.open

            def _is_bear(b):
                return b.close < b.open

            def _or_align(direction: str) -> str:
                if not or_dir:
                    return ""
                if direction == "BULL" and "BULL" in or_dir.upper():
                    return " [OR aligned]"
                if direction == "BEAR" and "BEAR" in or_dir.upper():
                    return " [OR aligned]"
                return ""

            # ── 5-min patterns (need at least 3 bars) ──────────────
            if bars_5min and len(bars_5min) >= 3:
                b0, b1, b2 = bars_5min[-3], bars_5min[-2], bars_5min[-1]

                # Bullish engulfing (b2 engulfs b1)
                if (_is_bear(b1) and _is_bull(b2)
                        and b2.open < b1.close and b2.close > b1.open):
                    patterns.append(f"BULLISH ENGULFING (5m){_or_align('BULL')}")

                # Bearish engulfing
                if (_is_bull(b1) and _is_bear(b2)
                        and b2.open > b1.close and b2.close < b1.open):
                    patterns.append(f"BEARISH ENGULFING (5m){_or_align('BEAR')}")

                # Hammer — appears after downmove (b0 and b1 declining lows)
                body2 = _body(b2)
                if body2 > 0:
                    lw2 = _lower_wick(b2)
                    uw2 = _upper_wick(b2)
                    down_move = b1.low < b0.low and b2.low <= b1.low
                    if (lw2 >= 2 * body2 and uw2 <= 0.3 * body2 and down_move):
                        patterns.append(f"HAMMER (5m){_or_align('BULL')}")

                # Shooting star — appears after upmove
                if body2 > 0:
                    uw2 = _upper_wick(b2)
                    lw2 = _lower_wick(b2)
                    up_move = b1.high > b0.high and b2.high >= b1.high
                    if (uw2 >= 2 * body2 and lw2 <= 0.3 * body2 and up_move):
                        patterns.append(f"SHOOTING STAR (5m){_or_align('BEAR')}")

                # Morning star (3-bar: bearish, small body, bullish)
                small_body1 = _body(b1) < _body(b0) * 0.4
                if (_is_bear(b0) and small_body1 and _is_bull(b2)
                        and _body(b0) > 0 and _body(b2) >= _body(b0) * 0.6):
                    patterns.append(f"MORNING STAR (5m){_or_align('BULL')}")

                # Evening star
                if (_is_bull(b0) and small_body1 and _is_bear(b2)
                        and _body(b0) > 0 and _body(b2) >= _body(b0) * 0.6):
                    patterns.append(f"EVENING STAR (5m){_or_align('BEAR')}")

                # Inside bar — b2's range is inside b1's range
                if b2.high < b1.high and b2.low > b1.low:
                    # Check 1-min bars to see if price has broken out of b1's range
                    if bars_1min and len(bars_1min) >= 1:
                        last_1m = bars_1min[-1]
                        if last_1m.close > b1.high:
                            patterns.append(f"INSIDE BAR BREAKOUT UP (5m→1m){_or_align('BULL')}")
                        elif last_1m.close < b1.low:
                            patterns.append(f"INSIDE BAR BREAKOUT DOWN (5m→1m){_or_align('BEAR')}")
                        else:
                            patterns.append("INSIDE BAR (5m) — breakout pending")

            # ── 1-min patterns (need at least 2 bars) ──────────────
            if bars_1min and len(bars_1min) >= 3:
                c0, c1, c2 = bars_1min[-3], bars_1min[-2], bars_1min[-1]

                # Bullish engulfing
                if (_is_bear(c1) and _is_bull(c2)
                        and c2.open < c1.close and c2.close > c1.open):
                    patterns.append(f"BULLISH ENGULFING (1m){_or_align('BULL')}")

                # Bearish engulfing
                if (_is_bull(c1) and _is_bear(c2)
                        and c2.open > c1.close and c2.close < c1.open):
                    patterns.append(f"BEARISH ENGULFING (1m){_or_align('BEAR')}")

                # Hammer
                body2 = _body(c2)
                if body2 > 0:
                    lw2 = _lower_wick(c2)
                    uw2 = _upper_wick(c2)
                    down_move = c1.low < c0.low
                    if lw2 >= 2 * body2 and uw2 <= 0.3 * body2 and down_move:
                        patterns.append(f"HAMMER (1m){_or_align('BULL')}")

                # Shooting star
                if body2 > 0:
                    uw2 = _upper_wick(c2)
                    lw2 = _lower_wick(c2)
                    up_move = c1.high > c0.high
                    if uw2 >= 2 * body2 and lw2 <= 0.3 * body2 and up_move:
                        patterns.append(f"SHOOTING STAR (1m){_or_align('BEAR')}")

            if not patterns:
                return "No significant candle patterns"
            return " | ".join(patterns)

        except Exception as e:
            return f"Candle pattern error: {e}"

    def _find_liquidity_pools(self, bars: list, current_price: float) -> str:
        try:
            if not bars or len(bars) < 5:
                return "No liquidity pools"
            recent    = bars[-20:]
            highs     = [b.high for b in recent]
            lows      = [b.low  for b in recent]
            pools     = []
            tolerance = LIQUIDITY_POOL_TOLERANCE
            seen_h: set = set()
            seen_l: set = set()

            for i in range(len(highs)):
                for j in range(i+1, len(highs)):
                    if abs(highs[i] - highs[j]) <= tolerance:
                        level = round((highs[i] + highs[j]) / 2, 2)
                        if level not in seen_h and abs(current_price - level) < 100:
                            seen_h.add(level)
                            swept = current_price > level
                            pools.append(f"BUY-SIDE LIQ: {level} ({'SWEPT' if swept else 'stops above — target'})")
                        break

            for i in range(len(lows)):
                for j in range(i+1, len(lows)):
                    if abs(lows[i] - lows[j]) <= tolerance:
                        level = round((lows[i] + lows[j]) / 2, 2)
                        if level not in seen_l and abs(current_price - level) < 100:
                            seen_l.add(level)
                            swept = current_price < level
                            pools.append(f"SELL-SIDE LIQ: {level} ({'SWEPT' if swept else 'stops below — target'})")
                        break

            return "\n".join(pools[:5]) if pools else "No liquidity pools nearby"
        except Exception:
            return "Liquidity unavailable"

    # ─── V4.1: IBKR Live News (tick 292) ───────────────────

    def _on_tick_news(self, tickerId: int, timeStamp: int, providerCode: str,
                      articleId: str, headline: str, extraData: str) -> None:
        """
        Handler for IBKR tick 292 live news events.
        Fires whenever a news headline is published for the subscribed contract.
        Requires an active IBKR news subscription (Briefing.com, Benzinga, etc.).
        If no subscription: tick 292 silently returns nothing — no error.
        """
        try:
            ts_dt  = datetime.fromtimestamp(timeStamp / 1000, tz=eastern)
            ts_str = ts_dt.strftime("%H:%M ET")
            entry  = {
                "time":     ts_str,
                "headline": headline[:200],
                "provider": providerCode,
                "article":  articleId,
            }
            self._ibkr_headlines.insert(0, entry)
            if len(self._ibkr_headlines) > self._ibkr_headlines_max:
                self._ibkr_headlines.pop()
            logger.info(f"[NEWS] {ts_str} [{providerCode}] {headline[:120]}")
        except Exception as e:
            logger.debug(f"tickNews handler error: {e}")

    def get_ibkr_headlines(self, n: int = 5) -> list:
        """Return last N IBKR live news headlines."""
        return self._ibkr_headlines[:n]

    def get_ibkr_headlines_text(self, n: int = 3) -> str:
        """Return formatted headline string for prompt injection."""
        if not self._ibkr_headlines:
            return ""
        lines = ["IBKR LIVE NEWS:"]
        for h in self._ibkr_headlines[:n]:
            lines.append(f"  {h['time']} [{h['provider']}] {h['headline']}")
        return "\n".join(lines)

    # ─── V4.0: Order Flow Imbalance ────────────────────────

    def _compute_ofi(self) -> dict:
        """
        Order Flow Imbalance (OFI) — V4.0 predictive feature.

        Measures net buying vs selling pressure from DOM bid/ask size changes
        across the 60-second rolling history.

        Based on Cont, Kukanov & Stoikov (2014):
          OFI = Σ (ΔBid - ΔAsk) over N snapshots
          Positive = net buying pressure (bids growing, asks shrinking)
          Negative = net selling pressure

        Returns: score (-100 to +100), acceleration, signal, divergence flag.
        """
        empty = {
            "score": 0, "raw": 0,
            "acceleration": "STABLE",
            "signal": "NEUTRAL",
            "divergence": False,
            "text": "OFI: insufficient DOM history",
        }
        try:
            if len(self._dom_history) < 4:
                return empty

            ofi_series = []
            for i in range(1, len(self._dom_history[-12:])):
                history = self._dom_history[-12:]
                prev = history[i - 1]
                curr = history[i]
                delta_bid = sum(curr["bids"].values()) - sum(prev["bids"].values())
                delta_ask = sum(curr["asks"].values()) - sum(prev["asks"].values())
                ofi_series.append(delta_bid - delta_ask)

            if not ofi_series:
                return empty

            raw_ofi = sum(ofi_series)
            score   = max(-100, min(100, int(raw_ofi / OFI_STRONG_THRESHOLD_CONTRACTS * 100)))

            mid         = len(ofi_series) // 2
            first_half  = sum(ofi_series[:mid]) if mid > 0 else 0
            second_half = sum(ofi_series[mid:])
            if abs(second_half) > abs(first_half) * OFI_ACCELERATION_THRESHOLD:
                acceleration = "ACCELERATING"
            elif abs(second_half) < abs(first_half) * OFI_DECELERATION_THRESHOLD:
                acceleration = "DECELERATING"
            else:
                acceleration = "STABLE"

            if score >= OFI_STRONG_BUY_THRESHOLD:    signal = "STRONG_BUY"
            elif score >= OFI_BUY_THRESHOLD:          signal = "BUY"
            elif score <= OFI_STRONG_SELL_THRESHOLD:  signal = "STRONG_SELL"
            elif score <= OFI_SELL_THRESHOLD:         signal = "SELL"
            else:                                     signal = "NEUTRAL"

            divergence = False
            if hasattr(self, '_bars_1min') and self._bars_1min and len(self._bars_1min) >= 3:
                bars = list(self._bars_1min[-3:])
                price_up = bars[-1].close > bars[0].close
                divergence = (price_up != (raw_ofi > 0)) and abs(score) > 20

            arrow = "▲" if score > 0 else ("▼" if score < 0 else "→")
            text  = (f"OFI: {arrow} {score:+d}/100 | Raw: {raw_ofi:+,d}ct | "
                     f"Signal: {signal} | {acceleration}")
            if divergence:
                text += " | ⚠ DIVERGENCE — OFI disagrees with price"

            return {"score": score, "raw": raw_ofi, "acceleration": acceleration,
                    "signal": signal, "divergence": divergence, "text": text}
        except Exception as e:
            logger.debug(f"OFI compute error: {e}")
            return empty

    # ─── DOM ───────────────────────────────────────────────

    def _compute_dom_signals(self) -> dict:
        """
        Extract actionable signals from DOM — computed in Python, not by Claude.

        Session 4 DOM upgrade:
          - Full 20 levels each side (was 10)
          - Absolute size thresholds tuned for MNQ
          - Cluster magnet detection (groups of large orders within 5 ticks)
          - Iceberg detection (level replenishes after being hit)
          - Spoof detection (large order vanishes without trading)
          - Sweep detection (multiple levels consumed in sequence)
        """
        empty = {
            "dom_available":       False,
            "dom_resistance_wall": None,
            "dom_support_wall":    None,
            "dom_buy_pressure":    0.5,
            "dom_imbalance":       "NEUTRAL",
            "dom_nearest_magnet":  None,
            "dom_vacuum_above":    False,
            "dom_vacuum_below":    False,
            "dom_iceberg_ask":     None,
            "dom_iceberg_bid":     None,
            "dom_spoof_ask":       None,
            "dom_spoof_bid":       None,
            "dom_sweep_up":        False,
            "dom_sweep_down":      False,
            "dom_cluster_above":   None,
            "dom_cluster_below":   None,
            "dom_text":            "DOM not active",
        }
        try:
            if not self.dom_ticker or not self.dom_subscription_active:
                return empty

            # Full 20 levels — no [:10] slice
            asks = [(d.price, d.size) for d in (self.dom_ticker.domAsks or [])]
            bids = [(d.price, d.size) for d in (self.dom_ticker.domBids or [])]

            if not asks and not bids:
                empty["dom_text"] = "DOM pending — CME L2 subscription required"
                return empty

            asks_dict = {p: s for p, s in asks}
            bids_dict = {p: s for p, s in bids}

            # Store snapshot for iceberg / spoof / sweep detection
            # Only if DOM_ADVANCED feature is enabled
            if FEATURE_DOM_ADVANCED:
                self._dom_history.append({
                    "ts":   time.time(),
                    "asks": dict(asks_dict),
                    "bids": dict(bids_dict),
                })
                if len(self._dom_history) > self._dom_history_max:
                    self._dom_history.pop(0)

            # MNQ-tuned absolute size thresholds (from config)
            SIGNIFICANT = DOM_SIGNIFICANT_SIZE
            LARGE       = DOM_LARGE_SIZE
            WHALE       = DOM_WHALE_SIZE

            # Pressure metrics
            total_ask_vol = sum(s for _, s in asks)
            total_bid_vol = sum(s for _, s in bids)
            total_vol     = total_ask_vol + total_bid_vol
            buy_pressure  = total_bid_vol / total_vol if total_vol else 0.5

            if buy_pressure > DOM_BUY_PRESSURE_BULL_THRESHOLD:
                imbalance = "BID_HEAVY"
            elif buy_pressure < DOM_SELL_PRESSURE_BEAR_THRESHOLD:
                imbalance = "ASK_HEAVY"
            else:
                imbalance = "NEUTRAL"

            # Walls — first large order on each side
            large_asks = [(p, s) for p, s in asks if s >= LARGE]
            large_bids = [(p, s) for p, s in bids if s >= LARGE]
            resistance_wall = min(p for p, _ in large_asks) if large_asks else None
            support_wall    = max(p for p, _ in large_bids) if large_bids else None

            # Nearest single dominant order
            all_orders     = asks + bids
            nearest_magnet = max(all_orders, key=lambda x: x[1])[0] if all_orders else None
            magnet_size    = max(all_orders, key=lambda x: x[1])[1] if all_orders else 0
            magnet_label   = ("WHALE" if magnet_size >= WHALE
                              else "LARGE" if magnet_size >= LARGE else None)

            # Cluster magnet: groups within 5 ticks whose total >= 2×LARGE
            def find_clusters(orders, min_total):
                if not orders:
                    return []
                s_orders = sorted(orders)
                clusters, i = [], 0
                while i < len(s_orders):
                    cp, cs = s_orders[i][0], s_orders[i][1]
                    j = i + 1
                    while j < len(s_orders) and s_orders[j][0] - cp <= DOM_CLUSTER_TOLERANCE_POINTS:
                        cs += s_orders[j][1]
                        j  += 1
                    if cs >= min_total:
                        clusters.append((cp, cs))
                    i = j if j > i else i + 1
                return clusters

            ask_clusters  = find_clusters(asks, LARGE * 2)
            bid_clusters  = find_clusters(bids, LARGE * 2)
            cluster_above = min(ask_clusters, key=lambda x: x[0])[0] if ask_clusters else None
            cluster_below = max(bid_clusters, key=lambda x: x[0])[0] if bid_clusters else None

            # Vacuum: near-book levels are very thin (< 5ct each)
            near_asks    = sorted(asks)[:3]
            near_bids    = sorted(bids, reverse=True)[:3]
            vacuum_above = bool(near_asks) and all(s < DOM_VACUUM_THRESHOLD_SIZE for _, s in near_asks)
            vacuum_below = bool(near_bids) and all(s < DOM_VACUUM_THRESHOLD_SIZE for _, s in near_bids)

            # Iceberg, spoof, sweep — only when DOM_ADVANCED enabled
            iceberg_ask = iceberg_bid = None
            spoof_ask   = spoof_bid   = None
            sweep_up    = sweep_down  = False

            if FEATURE_DOM_ADVANCED and len(self._dom_history) >= 3:
                h0, h1, h2 = (self._dom_history[-3],
                              self._dom_history[-2],
                              self._dom_history[-1])
                # Iceberg: level shrank then recovered
                for p in set(h0["asks"]) & set(h1["asks"]) & set(h2["asks"]):
                    if (h0["asks"][p] >= SIGNIFICANT
                            and h1["asks"][p] < h0["asks"][p] * DOM_ICEBERG_SHRINK_PCT
                            and h2["asks"][p] >= h0["asks"][p] * DOM_ICEBERG_RECOVERY_PCT):
                        iceberg_ask = p
                        break
                for p in set(h0["bids"]) & set(h1["bids"]) & set(h2["bids"]):
                    if (h0["bids"][p] >= SIGNIFICANT
                            and h1["bids"][p] < h0["bids"][p] * DOM_ICEBERG_SHRINK_PCT
                            and h2["bids"][p] >= h0["bids"][p] * DOM_ICEBERG_RECOVERY_PCT):
                        iceberg_bid = p
                        break
                # Spoof: large order appeared then vanished
                for p, s in h0["asks"].items():
                    if s >= LARGE and p not in h1["asks"] and p not in h2["asks"]:
                        spoof_ask = p
                        break
                for p, s in h0["bids"].items():
                    if s >= LARGE and p not in h1["bids"] and p not in h2["bids"]:
                        spoof_bid = p
                        break

            if FEATURE_DOM_ADVANCED and len(self._dom_history) >= 2:
                prev, curr = self._dom_history[-2], self._dom_history[-1]
                # Sweep: 3+ significant levels consumed between snapshots
                ask_consumed = sum(
                    1 for p in prev["asks"]
                    if p not in curr["asks"] and prev["asks"][p] >= SIGNIFICANT
                )
                bid_consumed = sum(
                    1 for p in prev["bids"]
                    if p not in curr["bids"] and prev["bids"][p] >= SIGNIFICANT
                )
                sweep_up   = ask_consumed >= DOM_SWEEP_LEVEL_THRESHOLD
                sweep_down = bid_consumed >= DOM_SWEEP_LEVEL_THRESHOLD

            # Build text output
            bar = "█" * int(buy_pressure * 10) + "░" * (10 - int(buy_pressure * 10))
            lines = [
                f"DOM [{bar}] {buy_pressure:.0%} buy | "
                f"Bid:{total_bid_vol:,} Ask:{total_ask_vol:,} | {imbalance}"
            ]
            if resistance_wall:  lines.append(f"  Resistance wall: {resistance_wall}")
            if support_wall:     lines.append(f"  Support wall: {support_wall}")
            if cluster_above:    lines.append(f"  ★ CLUSTER MAGNET ABOVE: {cluster_above}")
            if cluster_below:    lines.append(f"  ★ CLUSTER MAGNET BELOW: {cluster_below}")
            if nearest_magnet and magnet_label:
                lines.append(f"  Dominant order ({magnet_label}): {nearest_magnet}×{magnet_size}ct")
            if vacuum_above:     lines.append("  ⚡ VACUUM ABOVE — thin asks, price can run fast")
            if vacuum_below:     lines.append("  ⚡ VACUUM BELOW — thin bids, price can drop fast")
            if iceberg_ask:      lines.append(f"  🧊 ICEBERG ASK @ {iceberg_ask} — replenishing resistance")
            if iceberg_bid:      lines.append(f"  🧊 ICEBERG BID @ {iceberg_bid} — replenishing support")
            if spoof_ask:        lines.append(f"  ⚠ POSSIBLE SPOOF ASK @ {spoof_ask} — large order vanished")
            if spoof_bid:        lines.append(f"  ⚠ POSSIBLE SPOOF BID @ {spoof_bid} — large order vanished")
            if sweep_up:         lines.append("  🔥 ASK SWEEP — aggressive buyers consuming offer side")
            if sweep_down:       lines.append("  🔥 BID SWEEP — aggressive sellers consuming bid side")

            def tag(s):
                return " ★WHALE" if s >= WHALE else " ★LARGE" if s >= LARGE else " ·sig" if s >= SIGNIFICANT else ""

            lines.append("  ASKS (20 levels):")
            for p, s in sorted(asks)[:20]:
                lines.append(f"    {p:.2f} × {s:>4}{tag(s)}")
            lines.append("  BIDS (20 levels):")
            for p, s in sorted(bids, reverse=True)[:20]:
                lines.append(f"    {p:.2f} × {s:>4}{tag(s)}")

            return {
                "dom_available":       True,
                "dom_resistance_wall": resistance_wall,
                "dom_support_wall":    support_wall,
                "dom_buy_pressure":    round(buy_pressure, 3),
                "dom_imbalance":       imbalance,
                "dom_nearest_magnet":  nearest_magnet,
                "dom_vacuum_above":    vacuum_above,
                "dom_vacuum_below":    vacuum_below,
                "dom_iceberg_ask":     iceberg_ask,
                "dom_iceberg_bid":     iceberg_bid,
                "dom_spoof_ask":       spoof_ask,
                "dom_spoof_bid":       spoof_bid,
                "dom_sweep_up":        sweep_up,
                "dom_sweep_down":      sweep_down,
                "dom_cluster_above":   cluster_above,
                "dom_cluster_below":   cluster_below,
                "dom_text":            "\n".join(lines),
            }
        except Exception as e:
            logger.debug(f"DOM compute error: {e}")
            return {**empty, "dom_text": f"DOM compute error: {e}"}


    def _get_live_dom(self) -> str:
        """Compact DOM text for snapshot — uses full 20 levels."""
        try:
            if not self.dom_ticker or not self.dom_subscription_active:
                return "DOM not active"
            asks = [(d.price, d.size) for d in (self.dom_ticker.domAsks or [])]
            bids = [(d.price, d.size) for d in (self.dom_ticker.domBids or [])]
            if not asks and not bids:
                return "DOM pending — requires CME Level 2 subscription"

            SIGNIFICANT = DOM_SIGNIFICANT_SIZE
            LARGE       = DOM_LARGE_SIZE
            WHALE       = DOM_WHALE_SIZE
            all_orders = asks + bids
            lines = ["ASKS (resistance):"]
            for price, size in sorted(asks):
                tag = (" ← WHALE" if size >= WHALE
                       else " ← WALL" if size >= LARGE
                       else " · sig" if size >= SIGNIFICANT else "")
                lines.append(f"  {price} x {size}{tag}")
            lines.append("BIDS (support):")
            for price, size in sorted(bids, reverse=True):
                tag = (" ← WHALE" if size >= WHALE
                       else " ← WALL" if size >= LARGE
                       else " · sig" if size >= SIGNIFICANT else "")
                lines.append(f"  {price} x {size}{tag}")

            large_asks = [p for p, s in asks if s >= LARGE]
            large_bids = [p for p, s in bids if s >= LARGE]
            if large_asks: lines.append(f"Resistance magnet: {min(large_asks)}")
            if large_bids: lines.append(f"Support magnet: {max(large_bids)}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"DOM text error: {e}")
            return "DOM unavailable"

    def _compute_volume_profile(self, current_price: float) -> dict:
        """
        Compute volume profile signals — structured fields for pre-filter and snapshot.
        POC, VAH, VAL with price relationship.
        """
        empty = {
            "vp_available": False,
            "vp_poc": None, "vp_vah": None, "vp_val": None,
            "vp_status": "building",
            "vp_above_vah": False, "vp_below_val": False, "vp_inside_va": False,
            "vp_text": "Volume profile building…",
        }
        try:
            if not self.volume_profile or len(self.volume_profile) < 10:
                return empty

            poc         = max(self.volume_profile, key=self.volume_profile.get)
            total_vol   = sum(self.volume_profile.values())
            sorted_px   = sorted(self.volume_profile)
            target_vol  = total_vol * VOLUME_PROFILE_TARGET_PCT
            poc_idx     = sorted_px.index(poc)
            upper = lower = poc_idx
            captured    = self.volume_profile.get(poc, 0)

            while captured < target_vol and (upper < len(sorted_px) - 1 or lower > 0):
                u_vol = self.volume_profile.get(sorted_px[min(upper+1, len(sorted_px)-1)], 0)
                l_vol = self.volume_profile.get(sorted_px[max(lower-1, 0)], 0)
                if u_vol >= l_vol and upper < len(sorted_px)-1:
                    upper += 1; captured += u_vol
                elif lower > 0:
                    lower -= 1; captured += l_vol
                else:
                    break

            vah = sorted_px[upper]
            val = sorted_px[lower]

            above_vah  = current_price > vah
            below_val  = current_price < val
            inside_va  = val <= current_price <= vah
            poc_dist   = current_price - poc

            if above_vah:
                status = f"ABOVE VAH {vah} — breakout, strong bullish"
            elif below_val:
                status = f"BELOW VAL {val} — breakdown, strong bearish"
            elif abs(poc_dist) < POC_PROXIMITY_POINTS:
                status = f"AT POC {poc} — equilibrium, expect rotation"
            elif poc_dist > 0:
                status = f"ABOVE POC {poc} by {poc_dist:.1f}pts — mild bullish"
            else:
                status = f"BELOW POC {poc} by {abs(poc_dist):.1f}pts — mild bearish"

            return {
                "vp_available": True,
                "vp_poc":       poc,
                "vp_vah":       vah,
                "vp_val":       val,
                "vp_status":    status,
                "vp_above_vah": above_vah,
                "vp_below_val": below_val,
                "vp_inside_va": inside_va,
                "vp_text":      f"POC:{poc} | VAH:{vah} | VAL:{val} | {status}",
            }
        except Exception as e:
            logger.debug(f"Volume profile error: {e}")
            return empty

    def _get_volume_profile(self, current_price: float) -> str:
        """Legacy text interface."""
        return self._compute_volume_profile(current_price)["vp_text"]

    # ─── Helpers ───────────────────────────────────────────

    def _get_killzone(self, now_et: datetime) -> str:
        t = now_et.hour * 100 + now_et.minute
        if t >= 1900 or t < 300:
            return "ASIAN KILLZONE (7pm-3am ET)"
        if t < 800:
            return "LONDON KILLZONE (3am-8am ET) ★ — sweeps Asia range, pre-NY accumulation"
        if t < 1100:
            return "LONDON-NY OVERLAP (8am-11am ET) ★★ PRIME — highest liquidity"
        if t < 1330:
            return "DEAD ZONE (11am-1:30pm ET) — avoid"
        if t < 1600:
            return "NY PM KILLZONE (1:30pm-4pm ET) ★ — power hour"
        return "OUTSIDE KILLZONE (4pm-7pm ET)"

    def _determine_amd_phase(self, now_et: datetime) -> str:
        t = now_et.hour * 100 + now_et.minute
        if t >= 1900 or t < 300:
            return "ACCUMULATION (Asian session)"
        if t < 800:
            return "MANIPULATION (London) — stop hunts, Asia range sweeps"
        if t < 1100:
            return "DISTRIBUTION (London-NY overlap) — real institutional move"
        if t < 1330:
            return "DEAD ZONE — no new entries"
        if t < 1600:
            return "LATE DISTRIBUTION (NY PM KZ)"
        return "POST-CLOSE"

    def _get_session_phase(self, now_et: datetime) -> str:
        t = now_et.hour * 100 + now_et.minute
        if t < 300:   return "ASIA SESSION"
        if t < 800:   return "LONDON SESSION"
        if t < 930:   return "LONDON-NY OVERLAP"
        if t < 945:   return "OPENING AUCTION"
        if t < 1130:  return "NY AM PRIME"
        if t < 1330:  return "MIDDAY / CAUTION"
        if t < 1530:  return "NY PM PRIME"
        return "AFTER HOURS"

    def _format_session_levels(self, current_price) -> str:
        lines = ["KEY SESSION LEVELS:"]
        pairs = [
            ("Prev Week High", self.prev_week_high, "ABOVE — support",        "BELOW — resistance"),
            ("Prev Week Low",  self.prev_week_low,  "ABOVE — support",        "BELOW — resistance"),
            ("Prev Day High",  self.prev_day_high,  "ABOVE — broken, support","BELOW — resistance"),
            ("Prev Day Low",   self.prev_day_low,   "ABOVE — support",        "BELOW — broken, resistance"),
            ("Asia High",      self.asia_high,      "SWEPT — bullish",        "buy-side liquidity above"),
            ("Asia Low",       self.asia_low,       "SWEPT — bearish",        "sell-side liquidity below"),
            ("London High",    self.london_high,    "SWEPT",                  "buy-side above"),
            ("London Low",     self.london_low,     "SWEPT",                  "sell-side below"),
        ]
        found = False
        for label, level, above_txt, below_txt in pairs:
            if level is None:
                continue
            found = True
            rel = above_txt if (current_price or 0) > level else below_txt
            lines.append(f"  {label}: {level} ({rel})")
        if not found:
            lines.append("  Building session levels…")
        return "\n".join(lines)

    def _format_candles(self, bars_1min: list, bars_5min: list) -> str:
        lines = ["1-MINUTE BARS (last 10, newest first):"]
        for bar in reversed((bars_1min or [])[-10:]):
            d = "▲" if bar.close >= bar.open else "▼"
            lines.append(f"  {bar.date} {d} O:{bar.open} H:{bar.high} L:{bar.low} C:{bar.close} V:{bar.volume}")
        lines.append("\n5-MINUTE BARS (last 8, newest first):")
        for bar in reversed((bars_5min or [])[-8:]):
            d = "▲" if bar.close >= bar.open else "▼"
            lines.append(f"  {bar.date} {d} O:{bar.open} H:{bar.high} L:{bar.low} C:{bar.close} V:{bar.volume}")
        return "\n".join(lines)

    def get_account_data(self) -> dict:
        try:
            data = {}
            for av in self.ib.accountValues():
                if av.currency != "USD":
                    continue
                tag = av.tag
                try:
                    val = float(av.value)
                except (ValueError, TypeError):
                    continue
                if tag == "NetLiquidation":
                    data["netLiq"] = data["net_liquidation"] = val
                elif tag == "TotalCashValue":
                    data["cash"] = val
                elif tag == "RealizedPnL":
                    data["ibkrPnl"] = data["realized_pnl"] = val
                elif tag == "UnrealizedPnL":
                    data["unrealized"] = data["unrealized_pnl"] = val
                elif tag == "MaintMarginReq":
                    data["margin_used"] = val
                elif tag == "AvailableFunds":
                    data["available_funds"] = val
            return data
        except Exception as e:
            logger.error(f"Account data error: {e}")
            return {}


print("IBKR feed loaded")
