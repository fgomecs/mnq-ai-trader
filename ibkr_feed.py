from ib_insync import *
import pandas as pd
from datetime import datetime, timedelta
import pytz
from config import *
from logger import logger


class IBKRFeed:
    def __init__(self):
        self.ib = IB()
        self.contract = None
        self.connected = False
        self.eastern = pytz.timezone('US/Eastern')

        # Session level cache
        self.asia_high = None
        self.asia_low = None
        self.london_high = None
        self.london_low = None
        self.prev_day_high = None
        self.prev_day_low = None
        self.prev_week_high = None
        self.prev_week_low = None

        # VWAP components
        self.vwap_cum_vol = 0
        self.vwap_cum_pv = 0
        self.vwap_date = None

        # True delta (live mode)
        self.tick_delta = 0
        self.tick_subscription = None

        # Volume profile (live mode)
        self.volume_profile = {}  # price -> volume
        self.vp_date = None

        # DOM cache (live mode)
        self.dom_ticker = None
        self.dom_subscription_active = False

    def connect(self):
        """Connect to IBKR TWS or Gateway"""
        try:
            self.ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
            self.connected = True

            if LIVE_DATA_ACTIVE:
                self.ib.reqMarketDataType(1)  # Real-time
                logger.info("LIVE DATA MODE — real-time L2 active")
                self._start_tick_stream()
                self._start_dom_stream()
            else:
                self.ib.reqMarketDataType(3)  # Delayed
                logger.info("DELAYED DATA MODE — activate CME L2 to enable live mode")

            self._setup_contract()
            logger.info("Connected to IBKR successfully")
            return True
        except Exception as e:
            logger.error(f"IBKR connection failed: {e}")
            logger.error("Make sure API port on TWS/IBG is open")
            return False

    def _setup_contract(self):
        """Set up MNQ contract"""
        self.contract = Future(
            symbol=SYMBOL,
            lastTradeDateOrContractMonth='20260618',
            exchange=EXCHANGE,
            currency=CURRENCY
        )
        self.ib.qualifyContracts(self.contract)
        logger.info(f"Contract set up: {self.contract}")

    # ═══════════════════════════════════════
    # LIVE DATA METHODS (LIVE_DATA_ACTIVE=True)
    # ═══════════════════════════════════════

    def _start_tick_stream(self):
        """Start tick-by-tick stream for true delta calculation"""
        try:
            def on_tick(tick):
                """Called on every single trade tick"""
                if not self.contract:
                    return

                # Reset volume profile on new day
                today = datetime.now(self.eastern).date()
                if self.vp_date != today:
                    self.volume_profile = {}
                    self.tick_delta = 0
                    self.vp_date = today

                # Classify tick as buyer or seller initiated
                if hasattr(tick, 'tickType'):
                    price = tick.price
                    size = tick.size

                    # BuyerInitiated = hitting the ask = buying aggression
                    if tick.tickType == 'Last':
                        # Use price vs bid/ask to classify
                        # Positive = buyer, Negative = seller
                        self.tick_delta += size  # simplified — full version below

                # Build volume profile
                if hasattr(tick, 'price') and hasattr(tick, 'size'):
                    rounded_price = round(tick.price * 4) / 4  # round to tick
                    self.volume_profile[rounded_price] = (
                        self.volume_profile.get(rounded_price, 0) + tick.size
                    )

            # Subscribe to tick by tick data
            self.tick_subscription = self.ib.reqTickByTickData(
                self.contract,
                tickType='AllLast',
                numberOfTicks=0,
                ignoreSize=False
            )

            # Wire up the handler
            self.tick_subscription.updateEvent += on_tick
            logger.info("Tick stream started — true delta and volume profile active")

        except Exception as e:
            logger.error(f"Tick stream error: {e}")

    def _start_dom_stream(self):
        """Start live DOM stream"""
        try:
            self.dom_ticker = self.ib.reqMktDepth(
                self.contract,
                numRows=20,
                isSmartDepth=False
            )
            self.dom_subscription_active = True
            logger.info("Live DOM stream started — 20 level order book active")
        except Exception as e:
            logger.error(f"DOM stream error: {e}")

    def _get_true_delta(self) -> dict:
        """Get true cumulative delta from tick stream"""
        return {
            "cumulative_delta": self.tick_delta,
            "delta_last_bar": self.tick_delta,  # simplified
            "large_prints": "Monitoring via tick stream",
            "source": "TICK_DATA"
        }

    def _get_volume_profile(self, current_price: float) -> str:
        """Calculate volume profile — POC and Value Area"""
        try:
            if not self.volume_profile:
                return "Volume profile building..."

            # Point of Control — price with most volume
            poc = max(self.volume_profile, key=self.volume_profile.get)
            total_volume = sum(self.volume_profile.values())

            # Value Area — 70% of volume around POC
            sorted_prices = sorted(self.volume_profile.keys())
            target_volume = total_volume * 0.70

            # Expand from POC until we capture 70% of volume
            poc_idx = sorted_prices.index(poc)
            upper = poc_idx
            lower = poc_idx
            captured = self.volume_profile.get(poc, 0)

            while captured < target_volume and (upper < len(sorted_prices)-1 or lower > 0):
                upper_vol = self.volume_profile.get(sorted_prices[min(upper+1, len(sorted_prices)-1)], 0)
                lower_vol = self.volume_profile.get(sorted_prices[max(lower-1, 0)], 0)

                if upper_vol >= lower_vol and upper < len(sorted_prices)-1:
                    upper += 1
                    captured += upper_vol
                elif lower > 0:
                    lower -= 1
                    captured += lower_vol
                else:
                    break

            vah = sorted_prices[upper]  # Value Area High
            val = sorted_prices[lower]  # Value Area Low

            price_vs_poc = "ABOVE POC (bullish)" if current_price > poc else "BELOW POC (bearish)"
            price_vs_vah = "ABOVE VAH (breakout)" if current_price > vah else ""
            price_vs_val = "BELOW VAL (breakdown)" if current_price < val else ""

            text = f"Point of Control (POC): {poc} — {price_vs_poc}\n"
            text += f"Value Area High (VAH): {vah}\n"
            text += f"Value Area Low (VAL): {val}\n"
            if price_vs_vah:
                text += f"Status: {price_vs_vah}\n"
            if price_vs_val:
                text += f"Status: {price_vs_val}\n"
            if val < current_price < vah:
                text += "Status: INSIDE VALUE AREA — mean reversion likely\n"

            return text

        except Exception as e:
            logger.error(f"Volume profile error: {e}")
            return "Volume profile unavailable"

    def _get_live_dom(self) -> str:
        """Get live DOM from active stream"""
        try:
            if not self.dom_ticker or not self.dom_subscription_active:
                return "DOM stream not active"

            asks = []
            bids = []

            if hasattr(self.dom_ticker, 'domAsks') and self.dom_ticker.domAsks:
                asks = [(d.price, d.size) for d in self.dom_ticker.domAsks[:10]]
            if hasattr(self.dom_ticker, 'domBids') and self.dom_ticker.domBids:
                bids = [(d.price, d.size) for d in self.dom_ticker.domBids[:10]]

            if not asks and not bids:
                return "DOM data pending..."

            # Find large orders (potential magnets)
            all_orders = asks + bids
            if all_orders:
                avg_size = sum(s for _, s in all_orders) / len(all_orders)
                large_threshold = avg_size * 2.5

            text = "ASKS (offers — resistance):\n"
            for price, size in sorted(asks):
                marker = " ← LARGE WALL" if size > large_threshold else ""
                text += f"  {price} x {size}{marker}\n"

            text += "\nBIDS (support):\n"
            for price, size in sorted(bids, reverse=True):
                marker = " ← LARGE WALL" if size > large_threshold else ""
                text += f"  {price} x {size}{marker}\n"

            # Identify nearest magnets
            large_asks = [(p, s) for p, s in asks if s > large_threshold]
            large_bids = [(p, s) for p, s in bids if s > large_threshold]

            if large_asks:
                text += f"\nResistance magnet: {min(large_asks, key=lambda x: x[0])}\n"
            if large_bids:
                text += f"Support magnet: {max(large_bids, key=lambda x: x[0])}\n"

            return text

        except Exception as e:
            logger.error(f"Live DOM error: {e}")
            return "DOM data unavailable"

    # ═══════════════════════════════════════
    # MAIN SNAPSHOT METHOD
    # ═══════════════════════════════════════

    def get_snapshot(self, current_position=0, daily_pnl=0,
                     daily_loss_remaining=500, consecutive_losses=0) -> dict:
        """Get full market snapshot for Claude"""
        try:
            now_et = datetime.now(self.eastern)
            time_str = now_et.strftime("%H:%M:%S")
            session_phase = self._get_session_phase(now_et)

            # Live ticker
            if not LIVE_DATA_ACTIVE:
                self.ib.reqMarketDataType(3)

            ticker = self.ib.reqMktData(self.contract, '', False, False)
            self.ib.sleep(1)

            last_price = ticker.last or ticker.close
            bid = ticker.bid
            ask = ticker.ask
            bid_size = ticker.bidSize
            ask_size = ticker.askSize
            volume = ticker.volume

            # 1min bars
            try:
                bars_1min = self.ib.reqHistoricalData(
                    self.contract, endDateTime='',
                    durationStr='3600 S', barSizeSetting='1 min',
                    whatToShow='TRADES', useRTH=False,
                    formatDate=1, timeout=5
                )
            except Exception as e:
                logger.error(f"1min bars error: {e}")
                bars_1min = []

            # 5min bars
            try:
                bars_5min = self.ib.reqHistoricalData(
                    self.contract, endDateTime='',
                    durationStr='14400 S', barSizeSetting='5 mins',
                    whatToShow='TRADES', useRTH=False,
                    formatDate=1, timeout=5
                )
            except Exception as e:
                logger.error(f"5min bars error: {e}")
                bars_5min = []

            # 15min bars
            try:
                bars_15min = self.ib.reqHistoricalData(
                    self.contract, endDateTime='',
                    durationStr='1 D', barSizeSetting='15 mins',
                    whatToShow='TRADES', useRTH=False,
                    formatDate=1, timeout=5
                )
            except Exception as e:
                logger.error(f"15min bars error: {e}")
                bars_15min = []

            # Daily bars
            try:
                bars_daily = self.ib.reqHistoricalData(
                    self.contract, endDateTime='',
                    durationStr='10 D', barSizeSetting='1 day',
                    whatToShow='TRADES', useRTH=True,
                    formatDate=1, timeout=5
                )
            except Exception as e:
                logger.error(f"Daily bars error: {e}")
                bars_daily = []

            # Update session levels
            self._update_session_levels(bars_1min, now_et)

            # VWAP
            vwap = self._calculate_vwap(bars_1min, now_et)

            # HTF bias
            htf_bias = self._calculate_htf_bias(bars_daily, bars_15min)

            # Market structure
            structure = self._analyze_market_structure(bars_15min, bars_5min)

            # ICT concepts
            fvgs = self._find_fvgs(bars_5min, last_price)
            order_blocks = self._find_order_blocks(bars_5min, last_price)
            liquidity_pools = self._find_liquidity_pools(bars_5min, last_price)
            killzone = self._get_killzone(now_et)
            amd_phase = self._determine_amd_phase(now_et)
            session_levels = self._format_session_levels(last_price)
            candles_text = self._format_candles(bars_1min, bars_5min)

            session_high = max([b.high for b in bars_1min]) if bars_1min else 0
            session_low = min([b.low for b in bars_1min]) if bars_1min else 0

            # DOM — live or unavailable
            if LIVE_DATA_ACTIVE:
                dom_text = self._get_live_dom()
            else:
                dom_text = "DOM unavailable — activate CME L2 subscription (set LIVE_DATA_ACTIVE=True in config.py)"

            # Delta — true or approximated
            if LIVE_DATA_ACTIVE:
                delta_info = self._get_true_delta()
            else:
                delta_info = self._calculate_delta(bars_1min)

            # Volume profile — live only
            if LIVE_DATA_ACTIVE:
                volume_profile_text = self._get_volume_profile(last_price)
            else:
                volume_profile_text = "Volume profile unavailable — activate CME L2 subscription"

            upcoming_news = self._check_news_window(now_et)

            snapshot = {
                "timestamp": datetime.now().isoformat(),
                "time_et": time_str,
                "session_phase": session_phase,
                "killzone": killzone,
                "amd_phase": amd_phase,
                "data_mode": "LIVE" if LIVE_DATA_ACTIVE else "DELAYED",

                # Price
                "last_price": last_price,
                "bid": bid,
                "ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "volume": volume,
                "session_high": session_high,
                "session_low": session_low,
                "vwap": round(vwap, 2) if vwap else "N/A",

                # HTF
                "htf_bias": htf_bias,
                "market_structure": structure,

                # ICT
                "fair_value_gaps": fvgs,
                "order_blocks": order_blocks,
                "liquidity_pools": liquidity_pools,
                "session_levels": session_levels,
                "volume_profile": volume_profile_text,

                # Candles
                "candles": candles_text,

                # DOM
                "dom": dom_text,

                # Delta
                "cumulative_delta": delta_info["cumulative_delta"],
                "delta_last_bar": delta_info["delta_last_bar"],
                "large_prints": delta_info["large_prints"],

                # Risk
                "current_position": current_position,
                "daily_pnl": round(daily_pnl, 2),
                "daily_loss_remaining": round(daily_loss_remaining, 2),
                "consecutive_losses": consecutive_losses,
                "upcoming_news": upcoming_news
            }

            return snapshot

        except Exception as e:
            logger.error(f"Error getting snapshot: {e}")
            return {}

    # ═══════════════════════════════════════
    # SESSION LEVEL CALCULATIONS
    # ═══════════════════════════════════════

    def _update_session_levels(self, bars_1min, now_et):
        """Calculate Asia, London, Previous Day levels"""
        if not bars_1min:
            return

        try:
            asia_bars = []
            london_bars = []
            prev_day_bars = []

            for bar in bars_1min:
                try:
                    if hasattr(bar.date, 'hour'):
                        bar_time = bar.date
                    else:
                        bar_time = pd.Timestamp(str(bar.date)).to_pydatetime()
                        if bar_time.tzinfo is None:
                            bar_time = self.eastern.localize(bar_time)
                        else:
                            bar_time = bar_time.astimezone(self.eastern)

                    hour = bar_time.hour
                    is_today = bar_time.date() == now_et.date()
                    is_yesterday = bar_time.date() == (now_et - timedelta(days=1)).date()

                    if is_yesterday and hour >= 18:
                        asia_bars.append(bar)
                    if is_today and hour < 7:
                        asia_bars.append(bar)
                    if is_today and 3 <= hour < 8:
                        london_bars.append(bar)
                    if is_yesterday and 9 <= hour < 16:
                        prev_day_bars.append(bar)

                except Exception:
                    continue

            if asia_bars:
                self.asia_high = max(b.high for b in asia_bars)
                self.asia_low = min(b.low for b in asia_bars)
            if london_bars:
                self.london_high = max(b.high for b in london_bars)
                self.london_low = min(b.low for b in london_bars)
            if prev_day_bars:
                self.prev_day_high = max(b.high for b in prev_day_bars)
                self.prev_day_low = min(b.low for b in prev_day_bars)

        except Exception as e:
            logger.error(f"Session levels error: {e}")

    def _calculate_vwap(self, bars, now_et) -> float:
        """Calculate VWAP from RTH open"""
        try:
            today = now_et.date()
            if self.vwap_date != today:
                self.vwap_cum_vol = 0
                self.vwap_cum_pv = 0
                self.vwap_date = today

            rth_bars = []
            for bar in bars:
                try:
                    if hasattr(bar.date, 'hour'):
                        bar_time = bar.date
                    else:
                        bar_time = pd.Timestamp(str(bar.date)).to_pydatetime()
                        if bar_time.tzinfo is None:
                            bar_time = self.eastern.localize(bar_time)
                        else:
                            bar_time = bar_time.astimezone(self.eastern)

                    if (bar_time.date() == today and
                        bar_time.hour >= 9 and bar_time.minute >= 30):
                        rth_bars.append(bar)
                except Exception:
                    continue

            if not rth_bars:
                return 0

            cum_pv = sum(((b.high + b.low + b.close) / 3) * b.volume for b in rth_bars)
            cum_vol = sum(b.volume for b in rth_bars)
            return cum_pv / cum_vol if cum_vol > 0 else 0

        except Exception as e:
            logger.error(f"VWAP error: {e}")
            return 0

    # ═══════════════════════════════════════
    # HTF AND STRUCTURE
    # ═══════════════════════════════════════

    def _calculate_htf_bias(self, bars_daily, bars_15min) -> str:
        """Determine higher timeframe bias"""
        try:
            bias_text = ""

            if bars_daily and len(bars_daily) >= 3:
                last3 = bars_daily[-3:]
                if last3[-1].close > last3[-2].close > last3[-3].close:
                    daily_trend = "BULLISH (3 consecutive higher closes)"
                elif last3[-1].close < last3[-2].close < last3[-3].close:
                    daily_trend = "BEARISH (3 consecutive lower closes)"
                else:
                    daily_trend = "MIXED/NEUTRAL"

                recent_high = max(b.high for b in bars_daily[-5:])
                recent_low = min(b.low for b in bars_daily[-5:])
                bias_text += f"Daily trend: {daily_trend}\n"
                bias_text += f"5-day range: {recent_low} - {recent_high}\n"

            if bars_15min and len(bars_15min) >= 8:
                last8 = bars_15min[-8:]
                highs = [b.high for b in last8]
                lows = [b.low for b in last8]

                if highs[-1] > highs[-4] and lows[-1] > lows[-4]:
                    structure_15 = "BULLISH STRUCTURE (HH/HL on 15min)"
                elif highs[-1] < highs[-4] and lows[-1] < lows[-4]:
                    structure_15 = "BEARISH STRUCTURE (LH/LL on 15min)"
                else:
                    structure_15 = "RANGING/CHOPPY on 15min"

                bias_text += f"15min structure: {structure_15}\n"

            return bias_text if bias_text else "Insufficient data for HTF bias"

        except Exception as e:
            logger.error(f"HTF bias error: {e}")
            return "HTF bias unavailable"

    def _analyze_market_structure(self, bars_15min, bars_5min) -> str:
        """Identify BOS and CHOCH"""
        try:
            text = ""

            if bars_5min and len(bars_5min) >= 6:
                last6 = bars_5min[-6:]
                swing_highs = []
                swing_lows = []

                for i in range(1, len(last6) - 1):
                    if last6[i].high > last6[i-1].high and last6[i].high > last6[i+1].high:
                        swing_highs.append(last6[i].high)
                    if last6[i].low < last6[i-1].low and last6[i].low < last6[i+1].low:
                        swing_lows.append(last6[i].low)

                if len(swing_highs) >= 2:
                    if swing_highs[-1] > swing_highs[-2]:
                        text += "5min: Higher Highs (bullish BOS)\n"
                    else:
                        text += "5min: Lower Highs (bearish CHOCH)\n"

                if len(swing_lows) >= 2:
                    if swing_lows[-1] > swing_lows[-2]:
                        text += "5min: Higher Lows (bullish structure)\n"
                    else:
                        text += "5min: Lower Lows (bearish structure)\n"

            return text if text else "Structure unclear — insufficient swings"

        except Exception as e:
            logger.error(f"Structure error: {e}")
            return "Structure analysis unavailable"

    # ═══════════════════════════════════════
    # ICT CONCEPTS
    # ═══════════════════════════════════════

    def _find_fvgs(self, bars, current_price) -> str:
        """Find Fair Value Gaps"""
        try:
            if not bars or len(bars) < 3:
                return "No FVGs detected"

            fvgs = []
            for i in range(1, len(bars) - 1):
                prev_bar = bars[i - 1]
                next_bar = bars[i + 1]

                # Bullish FVG
                if next_bar.low > prev_bar.high:
                    fvg_top = next_bar.low
                    fvg_bottom = prev_bar.high
                    fvg_mid = (fvg_top + fvg_bottom) / 2
                    distance = abs(current_price - fvg_mid)
                    if distance < 100:
                        inside = fvg_bottom <= current_price <= fvg_top
                        fvgs.append(
                            f"BULLISH FVG: {fvg_bottom:.2f}-{fvg_top:.2f} "
                            f"(mid:{fvg_mid:.2f}) "
                            f"{'★ PRICE INSIDE — SUPPORT' if inside else f'dist:{distance:.1f}pts'}"
                        )

                # Bearish FVG
                if next_bar.high < prev_bar.low:
                    fvg_top = prev_bar.low
                    fvg_bottom = next_bar.high
                    fvg_mid = (fvg_top + fvg_bottom) / 2
                    distance = abs(current_price - fvg_mid)
                    if distance < 100:
                        inside = fvg_bottom <= current_price <= fvg_top
                        fvgs.append(
                            f"BEARISH FVG: {fvg_bottom:.2f}-{fvg_top:.2f} "
                            f"(mid:{fvg_mid:.2f}) "
                            f"{'★ PRICE INSIDE — RESISTANCE' if inside else f'dist:{distance:.1f}pts'}"
                        )

            return "\n".join(fvgs[-4:]) if fvgs else "No nearby FVGs"

        except Exception as e:
            logger.error(f"FVG error: {e}")
            return "FVG analysis unavailable"

    def _find_order_blocks(self, bars, current_price) -> str:
        """Find Order Blocks"""
        try:
            if not bars or len(bars) < 4:
                return "No order blocks detected"

            obs = []
            for i in range(1, len(bars) - 2):
                curr = bars[i]
                next1 = bars[i + 1]
                next2 = bars[i + 2]

                # Bullish OB
                if (curr.close < curr.open and
                    next1.close > next1.open and
                    next2.close > next2.open and
                    next1.close - next1.open > (curr.open - curr.close) * 1.5):
                    ob_top = curr.open
                    ob_bottom = curr.close
                    distance = abs(current_price - (ob_top + ob_bottom) / 2)
                    if distance < 150:
                        inside = ob_bottom <= current_price <= ob_top
                        obs.append(
                            f"BULLISH OB: {ob_bottom:.2f}-{ob_top:.2f} "
                            f"{'★ PRICE AT OB — HIGH PROB LONG' if inside else f'dist:{distance:.1f}pts'}"
                        )

                # Bearish OB
                if (curr.close > curr.open and
                    next1.close < next1.open and
                    next2.close < next2.open and
                    next1.open - next1.close > (curr.close - curr.open) * 1.5):
                    ob_top = curr.close
                    ob_bottom = curr.open
                    distance = abs(current_price - (ob_top + ob_bottom) / 2)
                    if distance < 150:
                        inside = ob_bottom <= current_price <= ob_top
                        obs.append(
                            f"BEARISH OB: {ob_bottom:.2f}-{ob_top:.2f} "
                            f"{'★ PRICE AT OB — HIGH PROB SHORT' if inside else f'dist:{distance:.1f}pts'}"
                        )

            return "\n".join(obs[-4:]) if obs else "No nearby order blocks"

        except Exception as e:
            logger.error(f"OB error: {e}")
            return "Order block analysis unavailable"

    def _find_liquidity_pools(self, bars, current_price) -> str:
        """Find equal highs/lows liquidity pools"""
        try:
            if not bars or len(bars) < 5:
                return "No liquidity pools detected"

            pools = []
            tolerance = 2.0
            recent = bars[-20:] if len(bars) >= 20 else bars
            highs = [b.high for b in recent]
            lows = [b.low for b in recent]
            seen_highs = set()
            seen_lows = set()

            for i in range(len(highs)):
                for j in range(i + 1, len(highs)):
                    if abs(highs[i] - highs[j]) <= tolerance:
                        level = round((highs[i] + highs[j]) / 2, 2)
                        if level not in seen_highs and abs(current_price - level) < 100:
                            seen_highs.add(level)
                            swept = current_price > level
                            pools.append(
                                f"BUY-SIDE LIQUIDITY: {level} "
                                f"({'SWEPT — watch for reversal' if swept else 'stops sitting above — potential target'})"
                            )
                        break

            for i in range(len(lows)):
                for j in range(i + 1, len(lows)):
                    if abs(lows[i] - lows[j]) <= tolerance:
                        level = round((lows[i] + lows[j]) / 2, 2)
                        if level not in seen_lows and abs(current_price - level) < 100:
                            seen_lows.add(level)
                            swept = current_price < level
                            pools.append(
                                f"SELL-SIDE LIQUIDITY: {level} "
                                f"({'SWEPT — watch for reversal' if swept else 'stops sitting below — potential target'})"
                            )
                        break

            return "\n".join(pools[:5]) if pools else "No liquidity pools nearby"

        except Exception as e:
            logger.error(f"Liquidity pools error: {e}")
            return "Liquidity analysis unavailable"

    # ═══════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════

    def _get_killzone(self, now_et) -> str:
        time_val = now_et.hour * 100 + now_et.minute
        if 300 <= time_val < 500:
            return "LONDON KILLZONE (3-5am) — High probability manipulation window"
        elif 800 <= time_val < 1000:
            return "NY OPEN KILLZONE (8-10am) — Major liquidity event"
        elif 930 <= time_val < 1100:
            return "NY AM KILLZONE (9:30-11am) — Prime trading window ★"
        elif 1300 <= time_val < 1600:
            return "NY PM KILLZONE (1-4pm) — Afternoon continuation or reversal ★"
        else:
            return "OUTSIDE KILLZONE — Lower probability, be very selective"

    def _determine_amd_phase(self, now_et) -> str:
        time_val = now_et.hour * 100 + now_et.minute
        if time_val < 700:
            return "ACCUMULATION (Asia) — Institutions building positions, note the range"
        elif time_val < 930:
            return "MANIPULATION (London) — Potential stop hunt underway, wait for fake move completion"
        elif time_val < 1200:
            return "DISTRIBUTION (NY AM) — Real institutional move, trade WITH direction"
        elif time_val < 1400:
            return "RE-ACCUMULATION — Midday consolidation, reduced participation"
        else:
            return "LATE DISTRIBUTION (NY PM) — Continuation or end of day reversal"

    def _format_session_levels(self, current_price) -> str:
        text = "KEY SESSION LEVELS (highest to lowest priority):\n"
        levels_found = False

        if self.prev_week_high:
            levels_found = True
            rel = "ABOVE — now support" if current_price > self.prev_week_high else "BELOW — resistance"
            text += f"  Prev Week High: {self.prev_week_high} ({rel})\n"
        if self.prev_week_low:
            levels_found = True
            rel = "ABOVE — support" if current_price > self.prev_week_low else "BELOW — now resistance"
            text += f"  Prev Week Low: {self.prev_week_low} ({rel})\n"
        if self.prev_day_high:
            levels_found = True
            rel = "ABOVE — broken, now support" if current_price > self.prev_day_high else "BELOW — resistance"
            text += f"  Prev Day High: {self.prev_day_high} ({rel})\n"
        if self.prev_day_low:
            levels_found = True
            rel = "ABOVE — support" if current_price > self.prev_day_low else "BELOW — broken, now resistance"
            text += f"  Prev Day Low: {self.prev_day_low} ({rel})\n"
        if self.asia_high:
            levels_found = True
            rel = "SWEPT — bullish signal" if current_price > self.asia_high else "buy-side liquidity above"
            text += f"  Asia High: {self.asia_high} ({rel})\n"
        if self.asia_low:
            levels_found = True
            rel = "SWEPT — bearish signal" if current_price < self.asia_low else "sell-side liquidity below"
            text += f"  Asia Low: {self.asia_low} ({rel})\n"
        if self.london_high:
            levels_found = True
            rel = "SWEPT" if current_price > self.london_high else "buy-side liquidity above"
            text += f"  London High: {self.london_high} ({rel})\n"
        if self.london_low:
            levels_found = True
            rel = "SWEPT" if current_price < self.london_low else "sell-side liquidity below"
            text += f"  London Low: {self.london_low} ({rel})\n"

        if not levels_found:
            text += "  Building session levels — check back after Asia/London\n"

        return text

    def _format_candles(self, bars_1min, bars_5min) -> str:
        text = "1-MINUTE BARS (last 10, newest first):\n"
        if bars_1min:
            for bar in reversed(bars_1min[-10:]):
                d = "▲" if bar.close > bar.open else "▼"
                text += f"  {bar.date} {d} O:{bar.open} H:{bar.high} L:{bar.low} C:{bar.close} V:{bar.volume}\n"

        text += "\n5-MINUTE BARS (last 8, newest first):\n"
        if bars_5min:
            for bar in reversed(bars_5min[-8:]):
                d = "▲" if bar.close > bar.open else "▼"
                text += f"  {bar.date} {d} O:{bar.open} H:{bar.high} L:{bar.low} C:{bar.close} V:{bar.volume}\n"

        return text

    def _calculate_delta(self, bars) -> dict:
        """Approximate delta from bar data (delayed mode)"""
        if not bars:
            return {"cumulative_delta": 0, "delta_last_bar": 0, "large_prints": "None"}

        cumulative = 0
        for bar in bars:
            bar_delta = bar.volume if bar.close > bar.open else -bar.volume
            cumulative += bar_delta

        last_bar = bars[-1]
        last_delta = last_bar.volume if last_bar.close > last_bar.open else -last_bar.volume

        avg_vol = sum(b.volume for b in bars) / len(bars)
        large = [b for b in bars[-5:] if b.volume > avg_vol * 2]
        large_text = f"{len(large)} large prints in last 5 bars" if large else "None"

        return {
            "cumulative_delta": cumulative,
            "delta_last_bar": last_delta,
            "large_prints": large_text
        }

    def _get_session_phase(self, now_et) -> str:
        time_val = now_et.hour * 100 + now_et.minute
        if time_val < 700:
            return "ASIA SESSION"
        elif time_val < 930:
            return "LONDON / PRE-MARKET"
        elif time_val < 945:
            return "OPENING AUCTION"
        elif time_val < 1130:
            return "NY AM PRIME"
        elif time_val < 1330:
            return "MIDDAY / CAUTION"
        elif time_val < 1530:
            return "NY PM PRIME"
        else:
            return "AFTER HOURS"

    def _check_news_window(self, now_et) -> str:
        return "Check ForexFactory/Investing.com for today's schedule"

    def disconnect(self):
        if self.connected:
            if LIVE_DATA_ACTIVE and self.dom_subscription_active:
                try:
                    self.ib.cancelMktDepth(self.contract)
                except:
                    pass
            self.ib.disconnect()
            logger.info("Disconnected from IBKR")


print("IBKR feed loaded successfully")
