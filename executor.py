import threading
import time
from ib_insync import *
from config import *
from logger import logger, log_trade

class Executor:
    def __init__(self, ib_instance, contract, paper=True):
        self.ib = ib_instance
        self.contract = contract
        self.paper = paper
        self.current_position = 0
        self.entry_price = 0
        self.stop_price = 0
        self.target_price = 0
        self.trade_mode = "NONE"
        self.daily_pnl = 0
        self.daily_loss_remaining = MAX_DAILY_LOSS_USD
        self.consecutive_losses = 0
        self.trades_today = []
        self._lock = threading.Lock()
        self._running = False
        self._protection_thread = None

    def start_protection_loop(self):
        """Start the fast protection loop in background thread"""
        self._running = True
        self._protection_thread = threading.Thread(
            target=self._fast_protection_loop,
            daemon=True
        )
        self._protection_thread.start()
        logger.info("Fast protection loop started — checking every 5 seconds")

    def stop_protection_loop(self):
        self._running = False

    def _fast_protection_loop(self):
        """
        Runs every 5 seconds independently of Claude.
        Manages stops and targets on open positions.
        No API calls — pure Python speed.
        """
        while self._running:
            try:
                with self._lock:
                    if self.current_position != 0:
                        self._check_stop_and_target()
            except Exception as e:
                logger.error(f"Protection loop error: {e}")
            time.sleep(5)

    def _check_stop_and_target(self):
        """Check if stop or target hit — called every 5 seconds"""
        try:
            ticker = self.ib.reqMktData(self.contract, '', False, False)
            self.ib.sleep(0.5)
            current_price = ticker.last or ticker.close

            if not current_price or current_price <= 0:
                return

            if self.current_position > 0:  # Long position
                # Stop hit
                if current_price <= self.stop_price:
                    logger.info(f"STOP HIT — Price {current_price} <= Stop {self.stop_price}")
                    self._close_position(current_price, "STOP HIT")
                    return
                # Target hit
                if self.target_price and current_price >= self.target_price:
                    logger.info(f"TARGET HIT — Price {current_price} >= Target {self.target_price}")
                    self._close_position(current_price, "TARGET HIT")
                    return
                # Trail stop for momentum
                if self.trade_mode == "MOMENTUM":
                    new_stop = current_price - (SWING_TRAIL_TICKS * TICK_SIZE)
                    if new_stop > self.stop_price:
                        self.stop_price = new_stop
                        logger.info(f"TRAIL STOP updated to {self.stop_price}")

            elif self.current_position < 0:  # Short position
                # Stop hit
                if current_price >= self.stop_price:
                    logger.info(f"STOP HIT — Price {current_price} >= Stop {self.stop_price}")
                    self._close_position(current_price, "STOP HIT")
                    return
                # Target hit
                if self.target_price and current_price <= self.target_price:
                    logger.info(f"TARGET HIT — Price {current_price} <= Target {self.target_price}")
                    self._close_position(current_price, "TARGET HIT")
                    return
                # Trail stop for momentum
                if self.trade_mode == "MOMENTUM":
                    new_stop = current_price + (SWING_TRAIL_TICKS * TICK_SIZE)
                    if new_stop < self.stop_price:
                        self.stop_price = new_stop
                        logger.info(f"TRAIL STOP updated to {self.stop_price}")

        except Exception as e:
            logger.error(f"Stop/target check error: {e}")

    def execute(self, decision: dict) -> bool:
        """Execute Claude's trading decision"""
        with self._lock:
            action = decision.get("decision", "HOLD")
            mode = decision.get("mode", "NONE")
            confidence = decision.get("confidence", "LOW")
            contracts = min(int(decision.get("contracts", 1)), MAX_CONTRACTS)
            stop_ticks = int(decision.get("stop_ticks", SCALP_STOP_TICKS))
            target_ticks = decision.get("target_ticks", SCALP_TARGET_TICKS)
            reasoning = decision.get("reasoning", "")

            if not self._safety_checks(action, confidence):
                return False

            if action == "BUY" and self.current_position == 0:
                return self._enter_trade("BUY", contracts, stop_ticks, target_ticks, mode, reasoning)
            elif action == "SELL" and self.current_position == 0:
                return self._enter_trade("SELL", contracts, stop_ticks, target_ticks, mode, reasoning)
            elif action == "CLOSE" and self.current_position != 0:
                ticker = self.ib.reqMktData(self.contract, '', False, False)
                self.ib.sleep(0.5)
                price = ticker.last or ticker.close
                return self._close_position(price, reasoning)
            elif action == "HOLD":
                logger.info(f"HOLD | {reasoning[:150]}")
                return True

            return False

    def _safety_checks(self, action, confidence) -> bool:
        """Check all safety rules before trading"""
        if self.daily_loss_remaining <= 0:
            logger.info("SAFETY: Daily loss limit reached. No more trades today.")
            return False
        if self.consecutive_losses >= 3:
            logger.info("SAFETY: 3 consecutive losses. Pausing.")
            return False
        if action in ["BUY", "SELL"] and confidence == "LOW":
            logger.info("SAFETY: Low confidence — skipping.")
            return False
        if action in ["BUY", "SELL"] and self.current_position != 0:
            logger.info("SAFETY: Already in position.")
            return False
        return True

    def _enter_trade(self, direction: str, contracts: int,
                     stop_ticks: int, target_ticks, mode: str, reasoning: str) -> bool:
        """Enter a trade"""
        try:
            ticker = self.ib.reqMktData(self.contract, '', False, False)
            self.ib.sleep(0.5)
            price = ticker.ask if direction == "BUY" else ticker.bid

            if not price or price <= 0:
                logger.error("Invalid entry price")
                return False

            tick = TICK_SIZE
            if direction == "BUY":
                self.stop_price = round(price - (stop_ticks * tick), 2)
                self.target_price = round(price + (int(target_ticks) * tick), 2) if target_ticks != "TRAIL" else None
            else:
                self.stop_price = round(price + (stop_ticks * tick), 2)
                self.target_price = round(price - (int(target_ticks) * tick), 2) if target_ticks != "TRAIL" else None

            ib_action = "BUY" if direction == "BUY" else "SELL"
            close_action = "SELL" if direction == "BUY" else "BUY"

            # Place entry order
            entry_order = MarketOrder(ib_action, contracts)
            self.ib.placeOrder(self.contract, entry_order)
            self.ib.sleep(0.5)

            # Place hard stop at broker level as backup
            stop_order = StopOrder(close_action, contracts, self.stop_price)
            stop_order.outsideRth = False
            self.ib.placeOrder(self.contract, stop_order)

            self.current_position = contracts if direction == "BUY" else -contracts
            self.entry_price = price
            self.trade_mode = mode

            log_trade(direction, contracts, price, reasoning)
            logger.info(f"ENTERED: {direction} {contracts} MNQ @ {price} | Stop: {self.stop_price} | Target: {self.target_price} | Mode: {mode}")

            return True

        except Exception as e:
            logger.error(f"Entry error: {e}")
            return False

    def _close_position(self, current_price: float, reason: str) -> bool:
        """Close current position immediately"""
        try:
            if self.current_position == 0:
                return False

            contracts = abs(self.current_position)
            close_action = "SELL" if self.current_position > 0 else "BUY"

            close_order = MarketOrder(close_action, contracts)
            self.ib.placeOrder(self.contract, close_order)
            self.ib.sleep(0.5)

            # Calculate P&L
            if self.current_position > 0:
                pnl = (current_price - self.entry_price) / TICK_SIZE * TICK_VALUE * contracts
            else:
                pnl = (self.entry_price - current_price) / TICK_SIZE * TICK_VALUE * contracts

            self.daily_pnl += pnl
            self.daily_loss_remaining -= max(0, -pnl)

            if pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0

            self.trades_today.append({
                "entry": self.entry_price,
                "exit": current_price,
                "pnl": pnl,
                "reason": reason
            })

            log_trade(f"CLOSE ({close_action})", contracts, current_price,
                     f"PnL: ${pnl:.2f} | {reason}")
            logger.info(f"CLOSED: {contracts} MNQ @ {current_price} | P&L: ${pnl:.2f} | Daily P&L: ${self.daily_pnl:.2f} | Reason: {reason}")

            self.current_position = 0
            self.entry_price = 0
            self.stop_price = 0
            self.target_price = 0
            self.trade_mode = "NONE"

            return True

        except Exception as e:
            logger.error(f"Close error: {e}")
            return False

    def update_position_from_ibkr(self):
        """Sync position with actual IBKR position"""
        try:
            positions = self.ib.positions()
            for pos in positions:
                if pos.contract.symbol == SYMBOL:
                    self.current_position = int(pos.position)
                    logger.info(f"Position synced: {self.current_position} MNQ")
                    return
            self.current_position = 0
        except Exception as e:
            logger.error(f"Position sync error: {e}")

print("Executor loaded successfully")