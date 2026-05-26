"""
Executor — order placement and position management for MNQ AI Trader.

Fixes from audit:
  P1.1 — _get_market_price no longer fetches inside the lock; uses cached _last_price
  P1.2 — Entry order has explicit outsideRth (matches stop/target orders)
  P2.5 — Orphan check runs on a background thread (lock released first)
"""

import datetime
import threading
import time
from typing import Optional

from ib_insync import Future, LimitOrder, MarketOrder, StopOrder

from config import (
    MAX_CONTRACTS, MAX_DAILY_LOSS_USD, MAX_SESSION_R_LOSS, SYMBOL,
    TICK_SIZE, TICK_VALUE,
    SCALP_STOP_TICKS, SCALP_TARGET_TICKS,
    PROTECTION_LOOP_SECS,
    FEATURE_R_BUDGET, FEATURE_DUAL_TRAIL,
    PROTECTION_RECONCILE_EVERY_N_LOOPS,
    DELAYED_DATA_STALENESS_THRESHOLD_POINTS,
    MAX_REASONABLE_PNL_PER_CONTRACT,
    RBUST_MAX_R_PER_TRADE,
    TRAIL_PROFIT_1_TICKS, TRAIL_PROFIT_1_LOCK,
    TRAIL_PROFIT_2_TICKS, TRAIL_PROFIT_2_LOCK,
    ENTRY_MODE, LIMIT_ORDER_MAX_SLIPPAGE, LIMIT_ORDER_TIMEOUT_SECS,
    SIMULATE_COMMISSIONS, COMMISSION_PER_SIDE_USD,
)
from logger import logger

try:
    from strategy_stats import record_trade as _record_trade_stats
except Exception:
    _record_trade_stats = None

try:
    from notifier import (
        notify_trade_entered, notify_trade_exited,
        notify_stop_to_breakeven, notify_loss_warning,
        notify_consecutive_losses
    )
    _notify_available = True
except ImportError:
    _notify_available = False


class Executor:
    def __init__(self, ib_instance, contract, paper: bool = True):
        self.ib            = ib_instance
        self.contract      = contract
        self.paper         = paper

        # Position state
        self.current_position: int   = 0
        self.entry_price:      float = 0.0
        self.stop_price:       float = 0.0
        self.target_price:     float = 0.0
        self.trade_mode:       str   = "NONE"
        self.entry_timestamp:  float = 0.0   # P1.3 — owned by executor, set on fill

        # P&L / risk state
        self.daily_pnl:            float = 0.0
        self.daily_loss_remaining: float = MAX_DAILY_LOSS_USD
        self.consecutive_losses:   int   = 0
        self.trades_today:         list  = []

        # D.1 — R-budget: track session risk units spent
        # 1R = dollar risk of one trade's stop distance.
        # Stops new entries once MAX_SESSION_R_LOSS R units have been lost.
        self.session_r_spent:      float = 0.0

        # Internal
        self._lock                   = threading.Lock()
        self._running                = False
        self._protection_thread: Optional[threading.Thread] = None
        self._last_price:            float = 0.0
        self._needs_close:           Optional[str] = None
        self._stop_order_id:            Optional[int]              = None
        self._target_order_id:          Optional[int]              = None
        self._closing_in_progress:      bool                       = False
        self._last_invalid_stop_log:    float                      = 0.0  # FIX 3 — throttle warning
        self._pending_limit_order_id:   Optional[int]              = None
        self._limit_order_timeout_thread: Optional[threading.Thread] = None
        # D.2 — Track last stop set by Claude TRAIL so auto-trail doesn't
        # overwrite it with a looser value
        self._claude_trail_stop:        float                      = 0.0

        # Real broker commissions captured via commissionReportEvent.
        # _pending: accumulated since last _record_pnl (consumed and reset per trade).
        # _session: running total for the day (diagnostic).
        # _seen_exec_ids: dedupe — ib_insync can fire the event twice for the same fill.
        self._commission_lock          = threading.Lock()
        self._broker_commission_pending: float = 0.0
        self._broker_commission_session: float = 0.0
        self._seen_exec_ids:            set   = set()
        try:
            self.ib.commissionReportEvent += self._on_commission_report
            logger.info("commissionReportEvent handler registered")
        except Exception as e:
            logger.warning(f"Could not register commissionReportEvent handler: {e}")

    # ─── Protection loop ───────────────────────────────────

    def start_protection_loop(self) -> None:
        self._running = True
        self._protection_thread = threading.Thread(
            target=self._fast_protection_loop,
            daemon=True,
            name="ProtectionLoop",
        )
        self._protection_thread.start()
        logger.info(f"Protection loop started ({PROTECTION_LOOP_SECS}s cadence)")

    def stop_protection_loop(self) -> None:
        self._running = False

    def _fast_protection_loop(self) -> None:
        """
        Runs every PROTECTION_LOOP_SECS. Two responsibilities:
          1. Check stop/target against current price (cheap, runs every loop)
          2. Reconcile local position with broker every N loops (cheap call)

        FIX 4 — Periodic broker reconciliation. Catches zombie positions
        where local state shows position != 0 but broker shows 0 (or vice
        versa), which is what the May 22 race condition produced. Without
        this, the stop-check would fire on a stale position with stop=0
        and submit nonsense orders.
        """
        loop_count = 0
        RECONCILE_EVERY_N = PROTECTION_RECONCILE_EVERY_N_LOOPS

        while self._running:
            try:
                with self._lock:
                    if self.current_position != 0 and self._last_price > 0:
                        if not self._closing_in_progress:
                            self._check_stop_and_target()
                            self._log_unrealized()

                    # FIX 4 — Reconcile periodically. Done OUTSIDE the
                    # closing-in-progress check because the whole point is
                    # to catch state drift.
                    loop_count += 1
                    if loop_count >= RECONCILE_EVERY_N:
                        loop_count = 0
                        if not self._closing_in_progress:
                            self._reconcile_with_broker()

            except Exception as e:
                logger.error(f"Protection loop error: {e}")
            time.sleep(PROTECTION_LOOP_SECS)

    def _reconcile_with_broker(self) -> None:
        """
        Compare local position with broker. If they disagree, flag for main
        thread to fix. We CANNOT call ib.placeOrder or ib.sleep from this
        thread (protection thread) safely — ib_insync's asyncio loop lives
        on the main thread, and calls from worker threads can hang or fail.

        So this method only DETECTS drift and sets _needs_close with a
        special tag. check_pending_close on main thread does the actual
        reconciliation work.
        """
        try:
            broker_pos = self._broker_position()
            local_pos  = self.current_position

            if broker_pos == local_pos:
                return   # in sync

            logger.warning(
                f"Position drift detected — local:{local_pos} broker:{broker_pos}. "
                f"Queuing reconciliation for main thread."
            )

            # Set a flag main thread will pick up. Encode the drift type in the
            # reason so we can dispatch correctly on the main side.
            if broker_pos == 0 and local_pos != 0:
                self._needs_close = "RECONCILE: bracket filled externally"
            elif local_pos == 0 and broker_pos != 0:
                self._needs_close = f"RECONCILE: unexpected broker position {broker_pos}"
            else:
                self._needs_close = f"RECONCILE: size mismatch local={local_pos} broker={broker_pos}"

        except Exception as e:
            logger.error(f"Reconcile detect error: {e}")

    def _handle_reconcile_on_main(self, reason: str) -> None:
        """
        Main-thread reconciliation handler. Called from check_pending_close
        when _needs_close starts with 'RECONCILE'. Safe to call ib_insync
        methods here.
        """
        try:
            broker_pos = self._broker_position()
            local_pos  = self.current_position
            logger.info(f"Reconciling: {reason} | local={local_pos} broker={broker_pos}")

            # Case A: Broker flat but we think we're in a position.
            if broker_pos == 0 and local_pos != 0:
                was_long    = local_pos > 0
                entry_price = self.entry_price
                contracts   = abs(local_pos)
                exit_price  = self._infer_recent_exit_fill(was_long, entry_price)
                if entry_price > 0 and exit_price > 0:
                    pnl = self._record_pnl(entry_price, exit_price, contracts,
                                           was_long, "Reconciled — bracket filled externally")
                    logger.info(
                        f"CLOSED (reconciled): {contracts} MNQ @ {exit_price} | "
                        f"Entry:{entry_price} P&L:${pnl:.2f}"
                    )
                self._cancel_all_orders_and_wait()
                self._reset_position_state()
                return

            # Case B: Unexpected broker position.
            if local_pos == 0 and broker_pos != 0:
                logger.error(
                    f"UNEXPECTED BROKER POSITION: {broker_pos} contracts with "
                    f"no local record. Flattening immediately."
                )
                self._cancel_all_orders_and_wait()
                flatten_action = "SELL" if broker_pos > 0 else "BUY"
                flatten = MarketOrder(flatten_action, abs(broker_pos))
                flatten.tif        = "GTC"
                flatten.outsideRth = True
                self.ib.placeOrder(self.contract, flatten)
                self.ib.sleep(1.5)
                logger.warning("Unexpected broker position flattened — P&L not attributed")
                return

            # Case C: Size mismatch. Adopt broker value as truth.
            logger.warning(f"Position size mismatch — adopting broker value {broker_pos}")
            self.current_position = broker_pos
        except Exception as e:
            logger.error(f"Reconcile handler error: {e}")

    # ─── Price update ──────────────────────────────────────

    def update_price(self, price: float) -> None:
        if price and price > 0:
            self._last_price = price

    # ─── Pending close ─────────────────────────────────────

    def check_pending_close(self) -> bool:
        # FIX 4 — Reconciliation requests are handled on main thread
        if self._needs_close and self._needs_close.startswith("RECONCILE"):
            reason            = self._needs_close
            self._needs_close = None
            self._handle_reconcile_on_main(reason)
            return True

        if self._needs_close and self.current_position == 0:
            logger.info(f"Clearing stale close flag: {self._needs_close}")
            self._needs_close = None
            return False
        if self._needs_close and self.current_position != 0 and not self._closing_in_progress:
            reason            = self._needs_close
            self._needs_close = None
            logger.info(f"Executing pending close: {reason}")
            self._close_position(self._last_price, reason)
            return True
        return False

    # ─── Execute (entry/close/hold) ────────────────────────

    def execute(self, decision: dict) -> bool:
        with self._lock:
            action     = decision.get("decision", "HOLD")
            mode       = decision.get("mode", "NONE")
            confidence = decision.get("confidence", "LOW")
            contracts  = min(int(decision.get("contracts", 1)), MAX_CONTRACTS)
            stop_ticks = int(decision.get("stop_ticks", SCALP_STOP_TICKS))
            target_ticks = decision.get("target_ticks", SCALP_TARGET_TICKS)
            reasoning  = decision.get("reasoning", "")
            entry_price = float(decision.get("entry_price", 0) or 0)

            if not self._safety_checks(action, confidence):
                return False

            if action in ("BUY", "SELL") and self.current_position == 0:
                return self._enter_trade(action, contracts, stop_ticks,
                                         target_ticks, mode, reasoning, entry_price)
            if action == "CLOSE" and self.current_position != 0:
                price = self._get_market_price()
                return self._close_position(price, reasoning)
            if action == "HOLD":
                logger.info(f"HOLD | {reasoning[:150]}")
                return True
            return False

    # ─── Safety checks ─────────────────────────────────────

    def _safety_checks(self, action: str, confidence: str) -> bool:
        if self.daily_loss_remaining <= 0:
            logger.info("SAFETY: Daily loss limit — no more trades.")
            return False
        if action in ("BUY", "SELL"):
            # D.1 — R-budget gate (gated by feature flag)
            if FEATURE_R_BUDGET and self.session_r_spent >= MAX_SESSION_R_LOSS:
                logger.info(
                    f"SAFETY: R-budget exhausted — {self.session_r_spent:.1f}R spent "
                    f"(max {MAX_SESSION_R_LOSS}R). No more entries today."
                )
                return False
            if confidence == "LOW":
                logger.info("SAFETY: Low confidence — skipping.")
                return False
            if self.current_position != 0:
                logger.info("SAFETY: Already in position — skipping.")
                return False
            if self._closing_in_progress:
                logger.info("SAFETY: Close in progress — skipping entry.")
                return False
        return True

    # ─── Market price helper (P1.1 — no blocking inside lock) ─

    def _get_market_price(self) -> float:
        """
        Best available current price WITHOUT blocking.
        Uses cached _last_price written by the 1Hz dashboard ticker.

        Previously this called reqMktData() + ib.sleep(0.5) inside the
        executor lock, blocking the protection loop. That's gone now.
        """
        return self._last_price or 0.0

    # ─── Cancel all orders ─────────────────────────────────

    def _cancel_all_orders_and_wait(self, timeout: float = 5.0) -> None:
        try:
            open_trades = self.ib.openTrades()
            if not open_trades:
                self._stop_order_id   = None
                self._target_order_id = None
                return

            logger.info(f"Cancelling {len(open_trades)} open order(s)…")
            for trade in open_trades:
                try:
                    self.ib.cancelOrder(trade.order)
                    logger.info(
                        f"  Cancelled #{trade.order.orderId} "
                        f"{trade.order.action} x{trade.order.totalQuantity}"
                    )
                except Exception as e:
                    logger.error(f"  Cancel #{trade.order.orderId} failed: {e}")

            # Wait for confirmation
            waited = 0.0
            while waited < timeout:
                self.ib.sleep(0.5)
                waited += 0.5
                if not self.ib.openTrades():
                    logger.info(f"All orders cleared after {waited:.1f}s")
                    break

            # Force-cancel any stragglers
            remaining = self.ib.openTrades()
            if remaining:
                logger.warning(f"{len(remaining)} order(s) still open — force cancelling")
                for trade in remaining:
                    try:
                        self.ib.cancelOrder(trade.order)
                    except Exception:
                        pass
                self.ib.sleep(1)

        except Exception as e:
            logger.error(f"Cancel-all error: {e}")
        finally:
            self._stop_order_id   = None
            self._target_order_id = None

    # ─── Overfill guard ────────────────────────────────────

    def _reconcile_overfill(self, direction: str, intended: int) -> None:
        """
        After entry order submission, verify the broker holds exactly the
        intended contract count. If a limit+MKT race produced a double-fill
        (broker shows 2x), flatten the excess immediately so we never carry
        more contracts than MAX_CONTRACTS / than we sized the bracket for.

        This is the root-cause guard for the position=-2 bug: the LIMIT
        order's cancel doesn't always beat the fill, and the MKT fallback
        then opens a second contract. Detect and unwind here.
        """
        try:
            broker_pos = self._broker_position()
            expected   = intended if direction == "BUY" else -intended
            if broker_pos == expected:
                return   # correct size
            if (expected > 0 and broker_pos > expected) or (expected < 0 and broker_pos < expected):
                excess = broker_pos - expected
                flatten_action = "SELL" if excess > 0 else "BUY"
                flatten_qty    = abs(excess)
                logger.error(
                    f"OVERFILL DETECTED: broker={broker_pos} intended={expected} "
                    f"(double-fill from limit+MKT race). Flattening {flatten_qty} "
                    f"contract(s) immediately."
                )
                flatten = MarketOrder(flatten_action, flatten_qty)
                flatten.tif        = "GTC"
                flatten.outsideRth = True
                self.ib.placeOrder(self.contract, flatten)
                self.ib.sleep(1.5)
                broker_after = self._broker_position()
                logger.info(f"Post-overfill flatten: broker now {broker_after} (expected {expected})")
        except Exception as e:
            logger.error(f"Overfill reconcile error: {e}")

    # ─── Enter trade ───────────────────────────────────────

    def _enter_trade(
        self, direction: str, contracts: int, stop_ticks: int,
        target_ticks, mode: str, reasoning: str, entry_price: float = 0.0,
    ) -> bool:
        try:
            tick         = TICK_SIZE
            close_action = "SELL" if direction == "BUY" else "BUY"

            # ── Entry order: LIMIT with MKT fallback, or pure MKT ──────────
            # Claude passes entry_price from the snapshot (last traded price).
            # We attempt a limit order at that price; if the market has moved
            # more than LIMIT_ORDER_MAX_SLIPPAGE ticks away, or if the limit
            # doesn't fill within LIMIT_ORDER_TIMEOUT_SECS, we cancel and
            # submit a market order so we never miss the trade.
            limit_price = entry_price
            use_limit   = (
                ENTRY_MODE == "LIMIT"
                and limit_price > 0
                and self._last_price > 0
                and abs(self._last_price - limit_price) <= LIMIT_ORDER_MAX_SLIPPAGE * tick
            )

            if use_limit:
                entry_order            = LimitOrder(direction, contracts, limit_price)
                entry_order.tif        = "GTC"
                entry_order.outsideRth = True
                entry_trade            = self.ib.placeOrder(self.contract, entry_order)
                self._pending_limit_order_id = entry_trade.order.orderId
                logger.info(f"LIMIT entry placed @ {limit_price} (slippage guard: {LIMIT_ORDER_MAX_SLIPPAGE}t, timeout: {LIMIT_ORDER_TIMEOUT_SECS}s)")

                # Wait up to LIMIT_ORDER_TIMEOUT_SECS for a fill; check slippage each tick
                waited = 0.0
                filled = False
                while waited < LIMIT_ORDER_TIMEOUT_SECS:
                    self.ib.sleep(0.5)
                    waited += 0.5
                    if entry_trade.fills:
                        filled = True
                        break
                    # Slippage check: if market has run away, cancel and go MKT
                    if (self._last_price > 0
                            and abs(self._last_price - limit_price) > LIMIT_ORDER_MAX_SLIPPAGE * tick):
                        logger.info(
                            f"Slippage exceeded {LIMIT_ORDER_MAX_SLIPPAGE}t "
                            f"(limit:{limit_price} last:{self._last_price:.2f}) — cancelling limit, switching to MKT"
                        )
                        try:
                            self.ib.cancelOrder(entry_order)
                            self.ib.sleep(0.5)
                        except Exception:
                            pass
                        break

                # CRITICAL — re-check fills AFTER cancel/timeout. A fill can land
                # between the last poll and the cancel confirmation; without this
                # re-check we'd submit a MKT fallback and end up with 2 contracts
                # at the broker while local state only tracks 1 (the position=-2
                # bug). Also verify against the broker as a second line of defense.
                if not filled:
                    if entry_trade.fills:
                        filled = True
                        logger.info("Limit filled during cancel window — skipping MKT fallback")
                    else:
                        try:
                            broker_pos_now = self._broker_position()
                        except Exception:
                            broker_pos_now = 0
                        expected_sign = 1 if direction == "BUY" else -1
                        if broker_pos_now == expected_sign * contracts:
                            filled = True
                            logger.info(
                                f"Broker shows position {broker_pos_now} matching intended "
                                f"entry — limit filled silently, skipping MKT fallback"
                            )

                if not filled:
                    # Fallback to market order
                    logger.info("Limit not filled — submitting MKT fallback")
                    entry_order            = MarketOrder(direction, contracts)
                    entry_order.tif        = "GTC"
                    entry_order.outsideRth = True
                    entry_trade            = self.ib.placeOrder(self.contract, entry_order)
                    self._pending_limit_order_id = None
                    self.ib.sleep(1.5)

                    # Post-fallback safety: if the limit ALSO filled (race lost),
                    # broker will show 2x contracts. Flatten the excess immediately.
                    self._reconcile_overfill(direction, contracts)
            else:
                # Pure market entry (ENTRY_MODE=MARKET or no valid limit price)
                # P1.2: explicit outsideRth=True for futures
                entry_order            = MarketOrder(direction, contracts)
                entry_order.tif        = "GTC"
                entry_order.outsideRth = True   # MNQ trades nearly 24/5; be explicit
                entry_trade            = self.ib.placeOrder(self.contract, entry_order)
                self._pending_limit_order_id = None
                self.ib.sleep(1.5)
                self._reconcile_overfill(direction, contracts)

            self._pending_limit_order_id = None

            # Resolve fill price
            if entry_trade.fills:
                actual_fill = entry_trade.fills[-1].execution.price
                logger.info(f"Fill (broker): {actual_fill}")
            else:
                # Use cached last price (no extra reqMktData round-trip)
                actual_fill = self._last_price
                logger.info(f"Fill (cached last_price): {actual_fill}")

            if not actual_fill or actual_fill <= 0:
                logger.error("Cannot determine fill price — aborting")
                return False

            # Sanity-check: delayed data can be 50-100 pts stale
            if self._last_price and abs(self._last_price - actual_fill) > DELAYED_DATA_STALENESS_THRESHOLD_POINTS:
                logger.warning(
                    f"Price mismatch: fill={actual_fill}, last={self._last_price} "
                    f"(diff {abs(self._last_price - actual_fill):.1f}pts) — using last_price"
                )
                actual_fill = self._last_price

            # Calculate stop / target from actual fill
            if direction == "BUY":
                stop_price   = round(actual_fill - stop_ticks * tick, 2)
                target_price = (
                    round(actual_fill + int(target_ticks) * tick, 2)
                    if target_ticks != "TRAIL" else None
                )
                if stop_price >= actual_fill:
                    logger.error(f"BUY stop {stop_price} >= fill {actual_fill} — aborting")
                    return False
                if target_price and target_price <= actual_fill:
                    logger.error(f"BUY target {target_price} <= fill {actual_fill} — aborting")
                    return False
            else:
                stop_price   = round(actual_fill + stop_ticks * tick, 2)
                target_price = (
                    round(actual_fill - int(target_ticks) * tick, 2)
                    if target_ticks != "TRAIL" else None
                )
                if stop_price <= actual_fill:
                    logger.error(f"SELL stop {stop_price} <= fill {actual_fill} — aborting")
                    return False
                if target_price and target_price >= actual_fill:
                    logger.error(f"SELL target {target_price} >= fill {actual_fill} — aborting")
                    return False

            logger.info(f"Fill:{actual_fill} Stop:{stop_price} Target:{target_price} Dir:{direction}")

            # Bracket orders — explicit outsideRth for consistency with entry
            stop_order             = StopOrder(close_action, contracts, stop_price)
            stop_order.outsideRth  = True
            stop_order.tif         = "GTC"
            stop_trade             = self.ib.placeOrder(self.contract, stop_order)
            self._stop_order_id    = stop_trade.order.orderId

            self._target_order_id = None
            if target_price:
                tgt_order             = LimitOrder(close_action, contracts, target_price)
                tgt_order.outsideRth  = True
                tgt_order.tif         = "GTC"
                tgt_trade             = self.ib.placeOrder(self.contract, tgt_order)
                self._target_order_id = tgt_trade.order.orderId

            # Update state — P1.3: own entry_timestamp here, set after fill confirmed
            self.current_position    = contracts if direction == "BUY" else -contracts
            self.entry_price         = actual_fill
            self.stop_price          = stop_price
            self.target_price        = target_price
            self.trade_mode          = mode
            self.entry_timestamp     = time.time()
            self._last_price         = actual_fill
            self._needs_close        = None
            self._closing_in_progress = False
            self._claude_trail_stop  = 0.0   # D.2 — reset trail anchor for new trade

            logger.info(
                f"ENTERED: {direction} {contracts} MNQ @ {actual_fill} | "
                f"Stop:{stop_price} Target:{target_price} Mode:{mode} | {reasoning[:100]}"
            )

            if _notify_available:
                notify_trade_entered(
                    direction="LONG" if direction == "BUY" else "SHORT",
                    entry=self.entry_price,
                    stop=self.stop_price,
                    target=self.target_price or 0.0,
                )
            return True

        except Exception as e:
            logger.error(f"Entry error: {e}")
            try:
                self._cancel_all_orders_and_wait()
            except Exception:
                pass
            return False

    # ─── Close position ────────────────────────────────────

    def _record_pnl(
        self, entry_price: float, exit_price: float,
        contracts: int, was_long: bool, reason: str,
    ) -> float:
        """Compute P&L, update counters, append to trades_today."""
        diff = (exit_price - entry_price) if was_long else (entry_price - exit_price)
        pnl  = (diff / TICK_SIZE) * TICK_VALUE * contracts

        # Prefer real broker commissions (captured via commissionReportEvent).
        # Fall back to SIMULATE_COMMISSIONS only when the broker reported nothing —
        # IBKR paper accounts do report commissions, so this normally takes the
        # real-data path. The pending bucket covers both entry and exit fills.
        commission        = 0.0
        commission_source = "none"
        with self._commission_lock:
            broker_commission = self._broker_commission_pending
            self._broker_commission_pending = 0.0

        if broker_commission > 0:
            commission        = broker_commission
            commission_source = "broker"
        elif SIMULATE_COMMISSIONS:
            commission        = COMMISSION_PER_SIDE_USD * 2 * contracts
            commission_source = "simulated"

        if commission > 0:
            pnl -= commission

        # FIX 6 — P&L sanity bound. On a 1-contract MNQ trade, the maximum
        # realistic single-trade P&L is roughly $200-300 (a 100-point move).
        # Anything wildly larger means corrupted entry_price or exit_price
        # (e.g. entry_price=0 because state was reset prematurely). Reject
        # rather than poison daily_pnl which gates further trading.
        if abs(pnl) > MAX_REASONABLE_PNL_PER_CONTRACT * contracts:
            logger.error(
                f"P&L sanity REJECT: ${pnl:.2f} on {contracts} contracts is impossible "
                f"(entry={entry_price}, exit={exit_price}, was_long={was_long}). "
                f"State is corrupted. Not recording, not updating daily_pnl. "
                f"Reason given: {reason}"
            )
            # Still log the trade so we have a record, but with pnl=None
            hold_secs = time.time() - self.entry_timestamp if self.entry_timestamp > 0 else 0.0
            self.trades_today.append({
                "time":         datetime.datetime.now().strftime("%H:%M:%S"),
                "action":       "SELL" if was_long else "BUY",
                "entry":        entry_price,
                "exit":         exit_price,
                "pnl":          None,
                "commission":   0.0,
                "hold_seconds": round(hold_secs),
                "mode":         self.trade_mode,
                "exit_reason":  f"REJECTED (sanity bound): {reason}",
            })
            return 0.0

        self.daily_pnl            += pnl
        self.daily_loss_remaining -= max(0.0, -pnl)

        if pnl < 0:
            self.consecutive_losses += 1
            # D.1 — Accumulate R spent: this loss = 1R by definition
            # (we sized the stop to be 1R). Use actual loss/stop_dollar ratio
            # to handle partial losses (e.g. Claude CLOSE before stop).
            stop_dollar = abs(self.entry_price - self.stop_price) / TICK_SIZE * TICK_VALUE * contracts
            if stop_dollar > 0:
                r_this_trade = min(abs(pnl) / stop_dollar, RBUST_MAX_R_PER_TRADE)
            else:
                r_this_trade = 1.0   # assume 1R if stop not recorded
            self.session_r_spent += r_this_trade
            logger.info(f"R-budget: {r_this_trade:.2f}R spent this trade | session total: {self.session_r_spent:.1f}R / {MAX_SESSION_R_LOSS}R")
        else:
            self.consecutive_losses = 0

        hold_secs = time.time() - self.entry_timestamp if self.entry_timestamp > 0 else 0.0
        self.trades_today.append({
            "time":         datetime.datetime.now().strftime("%H:%M:%S"),
            "action":       "SELL" if was_long else "BUY",
            "entry":        entry_price,
            "exit":         exit_price,
            "pnl":               round(pnl, 2),
            "commission":        round(commission, 2),
            "commission_source": commission_source,
            "hold_seconds":      round(hold_secs),
            "mode":              self.trade_mode,
            "exit_reason":       reason,
        })

        if _notify_available:
            notify_trade_exited(
                direction="LONG" if was_long else "SHORT",
                entry=entry_price,
                exit_price=exit_price,
                pnl=pnl,
                reason=reason
            )
        if _notify_available and self.consecutive_losses >= 3:
            notify_consecutive_losses(self.consecutive_losses, self.daily_pnl)
        if _notify_available and abs(self.daily_pnl) >= MAX_DAILY_LOSS_USD * 0.9:
            notify_loss_warning(abs(self.daily_pnl), MAX_DAILY_LOSS_USD)

        return pnl

    def _on_commission_report(self, trade, fill, report) -> None:
        """
        ib_insync commissionReportEvent handler. Fires once per fill once IBKR
        reports the realised commission. Accumulates into a pending bucket that
        _record_pnl drains on trade close. Dedupes by execId — the event can
        fire twice for the same fill in some ib_insync versions.

        Runs on the ib_insync event-loop thread. Uses a dedicated lock so it
        never contends with the main Executor._lock.
        """
        try:
            exec_id = getattr(report, "execId", None) or getattr(fill.execution, "execId", "")
            comm    = float(getattr(report, "commission", 0.0) or 0.0)
        except Exception as e:
            logger.warning(f"commissionReport parse failed: {e}")
            return

        if comm <= 0:
            return

        # Only count fills for our symbol — guards against multi-contract
        # accounts where unrelated fills could leak in.
        try:
            if fill.contract.symbol != SYMBOL:
                return
        except Exception:
            pass

        with self._commission_lock:
            if exec_id and exec_id in self._seen_exec_ids:
                return
            if exec_id:
                self._seen_exec_ids.add(exec_id)
            self._broker_commission_pending += comm
            self._broker_commission_session += comm

        logger.info(
            f"Commission captured: ${comm:.2f} (execId={exec_id}) — "
            f"pending=${self._broker_commission_pending:.2f}, "
            f"session=${self._broker_commission_session:.2f}"
        )

    def _reset_position_state(self) -> None:
        self.current_position  = 0
        self.entry_price       = 0.0
        self.stop_price        = 0.0
        self.target_price      = 0.0
        self.trade_mode        = "NONE"
        self.entry_timestamp   = 0.0
        self._needs_close      = None
        self._stop_order_id    = None
        self._target_order_id  = None

    def _broker_position(self) -> int:
        """
        Query broker for current MNQ position, bypassing local state.
        Returns the signed contract count from IBKR's perspective.
        Used by _close_position to verify position is actually open before
        submitting a close order — protects against the race where a broker
        stop fills concurrently with our CLOSE decision.
        """
        try:
            for pos in self.ib.positions():
                if pos.contract.symbol == SYMBOL:
                    return int(pos.position)
            return 0
        except Exception as e:
            logger.warning(f"Broker position query failed: {e}")
            # Fall back to local state on query failure — caller should be
            # cautious but at least not silently drop the close attempt.
            return self.current_position

    def _close_position(self, current_price: float, reason: str) -> bool:
        try:
            if self.current_position == 0:
                return False
            if self._closing_in_progress:
                logger.warning("Close already in progress — skipping duplicate")
                return False

            self._closing_in_progress = True
            self._needs_close         = None

            contracts    = abs(self.current_position)
            close_action = "SELL" if self.current_position > 0 else "BUY"
            entry_price  = self.entry_price
            was_long     = self.current_position > 0

            logger.info(
                f"Closing {contracts} MNQ {'LONG' if was_long else 'SHORT'} "
                f"@ ~{current_price} | {reason}"
            )

            # ── FIX 1+2 — Race-safe broker sync ──────────────
            # Before placing any close order, verify the broker-side position.
            # If a bracket stop already filled concurrently with our CLOSE
            # decision, the broker may already show us flat. In that case
            # we must NOT submit a market sell from flat (which would open
            # an accidental short).
            broker_pos_before = self._broker_position()
            if broker_pos_before == 0:
                logger.warning(
                    "Position already closed at broker (bracket order filled "
                    "before our CLOSE could fire). Reconciling local state, "
                    "no close order submitted."
                )
                # Try to capture the broker stop/target fill from execDetails so
                # we record realistic P&L. If we can't find it, use last_price.
                exit_price = self._infer_recent_exit_fill(was_long, entry_price)
                pnl = self._record_pnl(entry_price, exit_price, contracts,
                                       was_long, f"Broker bracket filled: {reason}")
                logger.info(
                    f"CLOSED (broker): {contracts} MNQ @ {exit_price} | "
                    f"Entry:{entry_price} P&L:${pnl:.2f} Daily:${self.daily_pnl:.2f}"
                )
                self._cancel_all_orders_and_wait()   # clean up the unfilled side
                self._reset_position_state()
                return True

            # If broker shows a position different magnitude than ours, trust broker
            if abs(broker_pos_before) != contracts:
                logger.warning(
                    f"Broker position {broker_pos_before} differs from local "
                    f"{self.current_position} — using broker value for close size"
                )
                contracts    = abs(broker_pos_before)
                close_action = "SELL" if broker_pos_before > 0 else "BUY"
                was_long     = broker_pos_before > 0

            # 1. Cancel bracket orders
            self._cancel_all_orders_and_wait()

            # ── FIX 1 again — RE-CHECK after cancel ──────────
            # Cancellation isn't instantaneous. Between the cancel request and
            # confirmation, a stop or target can fill. Verify post-cancel that
            # we still have a position to close.
            broker_pos_after = self._broker_position()
            if broker_pos_after == 0:
                logger.warning(
                    "Position closed during cancel (broker bracket filled "
                    "between our cancel request and close submission). "
                    "Skipping close order to avoid accidental reverse entry."
                )
                exit_price = self._infer_recent_exit_fill(was_long, entry_price)
                pnl = self._record_pnl(entry_price, exit_price, contracts,
                                       was_long, f"Filled during cancel: {reason}")
                logger.info(
                    f"CLOSED (during cancel): {contracts} MNQ @ {exit_price} | "
                    f"Entry:{entry_price} P&L:${pnl:.2f} Daily:${self.daily_pnl:.2f}"
                )
                self._reset_position_state()
                return True

            # 2. Market close — explicit outsideRth
            close_order             = MarketOrder(close_action, contracts)
            close_order.tif         = "GTC"
            close_order.outsideRth  = True
            close_trade             = self.ib.placeOrder(self.contract, close_order)
            self.ib.sleep(1.5)

            # 3. Resolve exit fill
            if close_trade.fills:
                exit_price = close_trade.fills[-1].execution.price
                logger.info(f"Exit fill (broker): {exit_price}")
            else:
                exit_price = self._last_price or current_price
                logger.info(f"Exit fill (cached last_price): {exit_price}")

            # 4. Record P&L (with sanity bound — see _record_pnl)
            pnl = self._record_pnl(entry_price, exit_price, contracts, was_long, reason)
            logger.info(
                f"CLOSED: {contracts} MNQ @ {exit_price} | "
                f"Entry:{entry_price} P&L:${pnl:.2f} Daily:${self.daily_pnl:.2f} | {reason}"
            )

            # 5. Reset state
            self._reset_position_state()

            # 6. Orphan check — now in-thread on main asyncio loop (FIX 5)
            self._post_close_orphan_check_safe()

            return True

        except Exception as e:
            logger.error(f"Close error: {e}")
            return False
        finally:
            self._closing_in_progress = False

    def _infer_recent_exit_fill(self, was_long: bool, entry_price: float) -> float:
        """
        When a bracket stop/target fires before our CLOSE submission, the
        actual exit price came from the broker bracket. Look through recent
        executions to find the matching fill.
        """
        try:
            # ib.fills() returns recent executions, newest first
            for fill in reversed(self.ib.fills()[-10:]):
                if fill.contract.symbol != SYMBOL:
                    continue
                exec_obj = fill.execution
                # Opposite side of our entry = the close
                if was_long and exec_obj.side == "SLD":
                    return float(exec_obj.price)
                if not was_long and exec_obj.side == "BOT":
                    return float(exec_obj.price)
        except Exception as e:
            logger.warning(f"Could not infer exit fill — using fallback price: {e}")
        # Fall back to cached last_price or stop_price as best estimate
        return self._last_price or self.stop_price or entry_price

    def _post_close_orphan_check_safe(self) -> None:
        """
        FIX 5 — Orphan check that doesn't race with asyncio. Previously this
        ran on a background thread which had no event loop, causing
        "no current event loop in thread 'OrphanCheck'" errors.

        Now runs synchronously on the caller thread (which has the event
        loop). _close_position is already called from the main thread via
        run_cycle, or from check_pending_close (also main). It IS called
        from the protection loop in some cases — we accept a brief block
        there in exchange for correctness.

        Includes a 1.5s sleep to let the broker propagate, then checks.
        With the FIX 1 pre-flight check, orphans should be far rarer now.
        """
        try:
            self.ib.sleep(1.5)   # let broker propagate any concurrent fills
            broker_pos = self._broker_position()
            if broker_pos != 0:
                logger.warning(
                    f"ORPHAN POSITION after close: {broker_pos} contracts "
                    f"— flattening"
                )
                flatten_action = "SELL" if broker_pos > 0 else "BUY"
                flatten_qty    = abs(broker_pos)
                flatten = MarketOrder(flatten_action, flatten_qty)
                flatten.tif        = "GTC"
                flatten.outsideRth = True
                self.ib.placeOrder(self.contract, flatten)
                self.ib.sleep(1.5)
                logger.info("Orphan flatten submitted")
        except Exception as e:
            logger.error(f"Orphan check error: {e}")

    # ─── Position sync from IBKR ──────────────────────────

    def update_position_from_ibkr(self) -> None:
        """Sync local position state with broker — handles external fills."""
        try:
            if self._closing_in_progress:
                return

            mnq_position = 0
            for pos in self.ib.positions():
                if pos.contract.symbol == SYMBOL:
                    mnq_position = int(pos.position)
                    break

            if mnq_position == self.current_position:
                return

            logger.info(f"Position sync: {self.current_position} → {mnq_position}")
            prev_position        = self.current_position
            self.current_position = mnq_position

            # Broker stop / target fired
            if mnq_position == 0 and self.entry_price > 0:
                logger.info("Position closed externally by broker")
                self._cancel_all_orders_and_wait()

                exit_price   = self._last_price or self.stop_price
                contracts    = abs(prev_position)
                was_long     = prev_position > 0

                pnl = self._record_pnl(
                    self.entry_price, exit_price, contracts, was_long,
                    "Broker stop/target hit",
                )
                logger.info(f"External close P&L: ${pnl:.2f}")
                self._reset_position_state()

        except Exception as e:
            logger.error(f"Position sync error: {e}")

    # ─── Stop / target check ───────────────────────────────

    def _check_stop_and_target(self) -> None:
        price = self._last_price
        if not price or price <= 0:
            return

        # FIX 3 — Never act on invalid stop/target. After _reset_position_state
        # stop_price=0.0 and target_price=0.0, but the protection loop might
        # see current_position != 0 momentarily (e.g. orphan from a race) and
        # then fire "STOP HIT: 29670 >= 0.0" which is nonsense.
        #
        # If we have a position but no valid stop, something is wrong — flag
        # for the position-management loop to investigate, don't trigger close.
        if self.stop_price <= 0:
            # Only log once per minute to avoid spam
            now = time.time()
            if (now - getattr(self, "_last_invalid_stop_log", 0)) > 60:
                logger.warning(
                    f"Position {self.current_position} with invalid stop_price={self.stop_price}. "
                    f"Skipping stop check until valid stop set."
                )
                self._last_invalid_stop_log = now
            return

        try:
            pos = self.current_position
            if pos > 0:
                if price <= self.stop_price:
                    logger.info(f"STOP HIT: {price} <= {self.stop_price}")
                    self._needs_close = "STOP HIT"
                elif self.target_price and self.target_price > 0 and price >= self.target_price:
                    logger.info(f"TARGET HIT: {price} >= {self.target_price}")
                    if not self._target_order_id:
                        self._needs_close = "TARGET HIT"
                    else:
                        logger.info("Broker limit order handling target fill")
                else:
                    self._auto_trail_long(price)

            elif pos < 0:
                if price >= self.stop_price:
                    logger.info(f"STOP HIT: {price} >= {self.stop_price}")
                    self._needs_close = "STOP HIT"
                elif self.target_price and self.target_price > 0 and price <= self.target_price:
                    logger.info(f"TARGET HIT: {price} <= {self.target_price}")
                    if not self._target_order_id:
                        self._needs_close = "TARGET HIT"
                    else:
                        logger.info("Broker limit order handling target fill")
                else:
                    self._auto_trail_short(price)

        except Exception as e:
            logger.error(f"Stop/target check error: {e}")
            if not self._needs_close:
                self._needs_close = "PROTECTION: stop/target check failed — review position"

    def _auto_trail_long(self, price: float) -> None:
        """
        Move stop up at profit milestones. D.2 — auto-trail never overwrites
        a stop that Claude explicitly set via TRAIL decision (which is stored
        in _claude_trail_stop). Claude's structural stop is always tighter.
        """
        ticks = (price - self.entry_price) / TICK_SIZE
        proposed = None
        if ticks >= TRAIL_PROFIT_2_TICKS:
            proposed = round(self.entry_price + TRAIL_PROFIT_2_LOCK * TICK_SIZE, 2)
        elif ticks >= TRAIL_PROFIT_1_TICKS:
            proposed = round(self.entry_price + TRAIL_PROFIT_1_LOCK * TICK_SIZE, 2)
        elif ticks >= 50 and self.stop_price < self.entry_price:
            proposed = self.entry_price

        if proposed is None:
            return
        # D.2 — don't move stop backward past what Claude explicitly set
        effective_floor = max(proposed, self._claude_trail_stop)
        if self.stop_price < effective_floor:
            self.stop_price = effective_floor
            logger.info(f"AUTO-TRAIL LONG: stop → {effective_floor} (Claude floor: {self._claude_trail_stop})")
            if _notify_available and proposed == self.entry_price:
                notify_stop_to_breakeven(direction="LONG", entry=self.entry_price)

    def _auto_trail_short(self, price: float) -> None:
        """
        Move stop down at profit milestones. D.2 — auto-trail never overwrites
        Claude's explicit TRAIL stop.
        """
        ticks = (self.entry_price - price) / TICK_SIZE
        proposed = None
        if ticks >= TRAIL_PROFIT_2_TICKS:
            proposed = round(self.entry_price - TRAIL_PROFIT_2_LOCK * TICK_SIZE, 2)
        elif ticks >= TRAIL_PROFIT_1_TICKS:
            proposed = round(self.entry_price - TRAIL_PROFIT_1_LOCK * TICK_SIZE, 2)
        elif ticks >= 50 and self.stop_price > self.entry_price:
            proposed = self.entry_price

        if proposed is None:
            return
        # D.2 — don't move stop forward past Claude's explicit TRAIL stop
        effective_ceiling = min(proposed, self._claude_trail_stop) if self._claude_trail_stop > 0 else proposed
        if self.stop_price > effective_ceiling:
            self.stop_price = effective_ceiling
            logger.info(f"AUTO-TRAIL SHORT: stop → {effective_ceiling} (Claude floor: {self._claude_trail_stop})")
            if _notify_available and proposed == self.entry_price:
                notify_stop_to_breakeven(direction="SHORT", entry=self.entry_price)

    # ─── Unrealized P&L log ────────────────────────────────

    def _log_unrealized(self) -> None:
        try:
            price = self._last_price
            if not price or not self.entry_price:
                return
            direction  = "LONG" if self.current_position > 0 else "SHORT"
            diff       = (price - self.entry_price) if self.current_position > 0 else (self.entry_price - price)
            ticks      = diff / TICK_SIZE
            unrealized = ticks * TICK_VALUE * abs(self.current_position)
            stop_dist  = abs(price - self.stop_price) / TICK_SIZE
            tgt_dist   = abs(price - self.target_price) / TICK_SIZE if self.target_price else 0
            sign       = "+" if unrealized >= 0 else ""
            bar        = "█" * min(20, int(abs(unrealized) / 2.5))
            arrow      = "▲" if unrealized >= 0 else "▼"

            logger.info(
                f"  {arrow} {direction} @ {self.entry_price} | Now:{price} | "
                f"P&L:{sign}${unrealized:.2f} {bar} | "
                f"Stop:{stop_dist:.0f}t | Target:{tgt_dist:.0f}t"
            )
        except Exception:
            pass


logger.info("Executor loaded")
