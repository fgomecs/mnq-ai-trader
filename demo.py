"""
demo.py — MNQ AI Trader Dashboard Demo

Pumps realistic fake MNQ data into dashboard_data.json at 10x speed
so you can see all dashboard components updating without running the
real bot or connecting to IBKR.

Run: py -3.11 demo.py
Then open: http://localhost:8080/dashboard.html
       or: http://localhost:8080/mobile.html

Press Ctrl+C to stop.
"""

import json
import math
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytz

BASE_DIR       = Path(os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"))
DASHBOARD_FILE = BASE_DIR / "dashboard_data.json"
eastern        = pytz.timezone("US/Eastern")

# ── Simulated market state ───────────────────────────────────

class MarketSim:
    def __init__(self):
        self.price       = 29650.0
        self.vwap        = 29640.0
        self.session_high = 29680.0
        self.session_low  = 29600.0
        self.or_high     = 29665.0
        self.or_low      = 29630.0
        self.volume      = 12000
        self.cum_delta   = 0
        self.daily_pnl   = 0.0
        self.position    = "FLAT"
        self.entry_price = 0.0
        self.stop_price  = 0.0
        self.target_price = 0.0
        self.trades      = []
        self.tick        = 0
        self.phase       = "SCANNING"  # SCANNING, ENTERING, IN_TRADE, EXITING
        self.phase_ticks = 0
        self.decision    = "HOLD"
        self.confidence  = "MEDIUM"
        self.thesis_prob = 0
        self.strategy    = ""
        self.confluence  = ""
        self.score       = 0
        self.reasoning   = "Analyzing market structure — OR forming at 29665/29630..."
        self.bias        = "LONG_PREFERRED"
        self.bias_tick   = 0

        # Simulate a realistic price path
        self._trend      = 1.0   # +1 up, -1 down
        self._volatility = 0.25  # ticks per update

    def step(self):
        self.tick += 1
        self.phase_ticks += 1
        self.bias_tick   += 1

        # Price walk with mean reversion toward VWAP
        noise    = random.gauss(0, self._volatility)
        revert   = (self.vwap - self.price) * 0.01
        trend    = self._trend * 0.05
        self.price = round(self.price + noise + revert + trend, 2)

        # Clamp price to reasonable range
        self.price = max(29500.0, min(29800.0, self.price))

        # Update session high/low
        self.session_high = max(self.session_high, self.price)
        self.session_low  = min(self.session_low,  self.price)

        # Volume + delta
        self.volume    += random.randint(10, 80)
        delta_bar       = random.randint(-30, 30)
        self.cum_delta += delta_bar

        # Flip trend occasionally
        if random.random() < 0.02:
            self._trend *= -1

        # Rotate bias every ~200 ticks
        if self.bias_tick > 200:
            self.bias_tick = 0
            options = ["LONG_PREFERRED", "SHORT_PREFERRED", "NEUTRAL"]
            self.bias = random.choice(options)

        # State machine
        self._update_phase()

        # Update VWAP slowly
        self.vwap = round(self.vwap * 0.999 + self.price * 0.001, 2)

    def _update_phase(self):
        if self.phase == "SCANNING":
            if self.phase_ticks > random.randint(15, 40):
                self._enter_analysis()

        elif self.phase == "ANALYSIS":
            if self.phase_ticks > 5:
                self._make_decision()

        elif self.phase == "ENTERING":
            if self.phase_ticks > 2:
                self._open_position()

        elif self.phase == "IN_TRADE":
            if self.position != "FLAT":
                self._manage_position()
            if self.phase_ticks > random.randint(20, 60):
                self._exit_position()

        elif self.phase == "EXITING":
            if self.phase_ticks > 3:
                self._reset_scan()

    def _enter_analysis(self):
        self.phase       = "ANALYSIS"
        self.phase_ticks = 0
        strategies = [
            "ORB_PULLBACK", "OB_BOUNCE", "FVG_FILL",
            "CHOCH_ENTRY", "ICT_SWEEP_REVERSAL", "VWAP_RECLAIM"
        ]
        confluences = [
            "OR_BULL + CHOCH_BULL + ABOVE_VWAP + DELTA_POS + NY_AM_KZ",
            "OR_BEAR + OB_REJECT + BELOW_VWAP + DELTA_NEG + MTF_BEAR",
            "FVG_FILL + CHOCH_BULL + DOM_SWEEP_UP + OFI_BUY + NY_PM_KZ",
            "SWEEP_REVERSAL + DOM_VACUUM + ICEBERG_BID + DELTA_POS",
            "NEUTRAL + CHOCH_BEAR + CLUSTER_MAGNET_ABOVE + SELL_PRESSURE",
        ]
        reasonings = [
            "Price swept sell-side liquidity at 29625 and immediately rejected — CHoCH confirmed on 1m. "
            "OFI showing strong buy pressure +72, iceberg bid replenishing at 29632. "
            "OR thesis intact, targeting bear OB fill at 29685. Invalidation: break below 29620.",

            "Bear OB rejection at 29678 — price tapped into the 29670-29678 zone and stalled. "
            "MTF all bearish, OFI -58 and decelerating. DOM cluster magnet below at 29640. "
            "Short bias confirmed. Stop above the OB at 29682. Target liquidity pool at 29630.",

            "VWAP reclaim after morning sweep. Price holding above 29645 VWAP with HH/HL structure. "
            "Volume profile shows POC at 29650 acting as support. Delta trend positive last 3 bars. "
            "Entering on the pullback to VWAP. Stop below the swing low at 29638.",

            "Dead zone chop — MTF conflicted, no clean setup. OR thesis still valid but "
            "price oscillating around VWAP. Waiting for PM session to develop. "
            "Key levels: resistance 29680, support 29625. No trade until structure clarifies.",
        ]

        self.strategy   = random.choice(strategies)
        self.confluence = random.choice(confluences)
        self.score      = random.randint(4, 9)
        self.reasoning  = random.choice(reasonings)
        self.thesis_prob = random.randint(55, 95)
        self.confidence = "HIGH" if self.thesis_prob >= 80 else "MEDIUM" if self.thesis_prob >= 65 else "LOW"

        # 40% chance of a real entry, 60% HOLD
        if random.random() < 0.4 and self.thesis_prob >= 70:
            self.decision = "BUY" if self.price < self.vwap or random.random() < 0.5 else "SELL"
        else:
            self.decision = "HOLD"
            self.thesis_prob = random.randint(45, 69)

    def _make_decision(self):
        if self.decision in ("BUY", "SELL"):
            self.phase       = "ENTERING"
            self.phase_ticks = 0
        else:
            self._reset_scan()

    def _open_position(self):
        self.phase       = "IN_TRADE"
        self.phase_ticks = 0
        self.position    = "LONG" if self.decision == "BUY" else "SHORT"
        self.entry_price = self.price
        stop_dist        = random.uniform(8, 15)
        target_dist      = stop_dist * random.uniform(1.5, 2.5)

        if self.position == "LONG":
            self.stop_price   = round(self.price - stop_dist, 2)
            self.target_price = round(self.price + target_dist, 2)
        else:
            self.stop_price   = round(self.price + stop_dist, 2)
            self.target_price = round(self.price - target_dist, 2)

        self.reasoning = (
            f"{'LONG' if self.decision=='BUY' else 'SHORT'} entry at {self.price:.2f}. "
            f"Stop: {self.stop_price:.2f} | Target: {self.target_price:.2f} | "
            f"R:R {target_dist/stop_dist:.1f}:1. Monitoring for structure confirmation."
        )

    def _manage_position(self):
        # Occasionally trail the stop
        if self.position == "LONG" and random.random() < 0.05:
            new_stop = round(self.price - random.uniform(5, 10), 2)
            if new_stop > self.stop_price:
                self.stop_price = new_stop
                self.reasoning  = (
                    f"Trailing stop moved to {self.stop_price:.2f} — "
                    f"locking in {self.price - self.entry_price:.2f} pts profit. "
                    f"Target {self.target_price:.2f} still valid."
                )
        elif self.position == "SHORT" and random.random() < 0.05:
            new_stop = round(self.price + random.uniform(5, 10), 2)
            if new_stop < self.stop_price:
                self.stop_price = new_stop
                self.reasoning  = (
                    f"Trailing stop moved to {self.stop_price:.2f} — "
                    f"locking in {self.entry_price - self.price:.2f} pts profit. "
                    f"Target {self.target_price:.2f} still valid."
                )

        # Check stop/target hit
        if self.position == "LONG":
            if self.price <= self.stop_price or self.price >= self.target_price:
                self._exit_position()
        elif self.position == "SHORT":
            if self.price >= self.stop_price or self.price <= self.target_price:
                self._exit_position()

    def _exit_position(self):
        if self.position == "FLAT":
            self._reset_scan()
            return

        self.phase       = "EXITING"
        self.phase_ticks = 0

        # Calculate P&L
        if self.position == "LONG":
            pnl = (self.price - self.entry_price) * 2  # $2/pt for MNQ
        else:
            pnl = (self.entry_price - self.price) * 2

        pnl          = round(pnl, 2)
        self.daily_pnl += pnl

        now_et = datetime.now(eastern)
        self.trades.append({
            "time":        now_et.strftime("%H:%M"),
            "action":      "BUY" if self.position == "LONG" else "SELL",
            "direction":   self.position,
            "entry":       self.entry_price,
            "exit":        self.price,
            "pnl":         pnl,
            "exit_reason": "Target hit" if pnl > 0 else "Stop hit",
            "strategy":    self.strategy,
        })

        exit_word    = "Target hit" if pnl > 0 else "Stop hit"
        self.reasoning = (
            f"CLOSED {self.position} at {self.price:.2f}. "
            f"{exit_word}. P&L: ${pnl:+.2f}. "
            f"Daily P&L: ${self.daily_pnl:+.2f}."
        )
        self.decision  = "CLOSE"
        self.position  = "FLAT"
        self.entry_price = self.stop_price = self.target_price = 0.0

    def _reset_scan(self):
        self.phase       = "SCANNING"
        self.phase_ticks = 0
        self.decision    = "HOLD"
        self.thesis_prob = 0
        self.strategy    = ""
        self.score       = 0

    def build_snapshot(self) -> dict:
        now_et   = datetime.now(eastern)
        ts       = now_et.strftime("%H:%M:%S")
        wins     = sum(1 for t in self.trades if t["pnl"] > 0)
        losses   = sum(1 for t in self.trades if t["pnl"] < 0)

        # Simulate OR broken state
        or_broken_up   = self.price > self.or_high
        or_broken_down = self.price < self.or_low

        # ICT levels (fake but realistic)
        fvg_text = f"BULL FVG 29648.50-29652.00 (active)" if self.price < 29652 else "BULL FVG 29648.50-29652.00 (filled)"
        ob_text  = f"BEAR OB 29672.00-29678.00 | BULL OB 29622.00-29628.00"
        liq_text = f"Buy-side liq: {self.session_high:.2f} | Sell-side liq: {self.session_low:.2f}"
        choch    = "BULLISH CHoCH — HH/HL structure on 1m" if self.cum_delta > 0 else "BEARISH CHoCH — LH/LL structure on 1m"
        mtf      = ("PARTIAL_BULL (2/3 TF bullish)" if self.bias == "LONG_PREFERRED"
                    else "PARTIAL_BEAR (2/3 TF bearish)" if self.bias == "SHORT_PREFERRED"
                    else "CONFLICTED — timeframes disagree")

        # AMD phase based on simulated ET time
        h = now_et.hour
        amd = ("ACCUMULATION" if h < 10 else
               "MANIPULATION" if h < 11 else
               "DISTRIBUTION" if h < 15 else "REVERSAL")

        killzone = ("NY AM Kill Zone" if 9 <= h < 11 else
                    "NY PM Kill Zone" if 13 <= h < 15 else
                    "Outside Kill Zone")

        # Candle text
        candles = "\n".join([
            f"{(now_et - timedelta(minutes=i)).strftime('%H:%M')} "
            f"O:{self.price-random.uniform(-3,3):.2f} "
            f"H:{self.price+random.uniform(0,4):.2f} "
            f"L:{self.price-random.uniform(0,4):.2f} "
            f"C:{self.price+random.uniform(-2,2):.2f}"
            for i in range(5, 0, -1)
        ])

        # OFI
        ofi_score = max(-100, min(100, self.cum_delta // 5))
        ofi_signal = ("STRONG_BUY" if ofi_score > 60 else "BUY" if ofi_score > 25
                      else "STRONG_SELL" if ofi_score < -60 else "SELL" if ofi_score < -25
                      else "NEUTRAL")

        # IBKR headlines
        headlines = [
            {"time": now_et.strftime("%H:%M ET"), "provider": "BRF",
             "headline": "Nasdaq futures hold gains as tech sector leads market higher"},
            {"time": (now_et - timedelta(minutes=12)).strftime("%H:%M ET"), "provider": "DJ",
             "headline": "Fed officials signal patience on rate cuts amid strong jobs data"},
        ]

        # Reasoning block with timestamp
        reasoning_block = {
            "time":     ts,
            "iso_ts":   now_et.isoformat(),
            "decision": self.decision,
            "reasoning": self.reasoning,
        }

        return {
            "timestamp":   now_et.isoformat(),
            "time_et":     ts,
            "data_mode":   "LIVE L2 (DEMO)",
            "botVersion":  "4.1.0-DEMO",

            "position":    self.position,
            "entryPrice":  self.entry_price or None,
            "stopPrice":   self.stop_price  or None,
            "targetPrice": self.target_price or None,
            "currentPrice": self.price,
            "bid":         round(self.price - 0.25, 2),
            "ask":         round(self.price + 0.25, 2),
            "dailyPnl":    round(self.daily_pnl, 2),
            "maxLoss":     500.0,
            "netLiq":      50000 + self.daily_pnl,

            "claudeStatus":        f"{'SCANNING' if self.phase == 'SCANNING' else 'ANALYZING' if self.phase == 'ANALYSIS' else 'IN POSITION' if self.position != 'FLAT' else 'MONITORING'}",
            "lastDecision":        self.decision,
            "lastConfidence":      self.confidence,
            "lastStrategy":        self.strategy,
            "lastConfluence":      self.confluence,
            "lastConfluenceScore": self.score,
            "thesisProbability":   self.thesis_prob,
            "reasoning":           reasoning_block,
            "lastReasoning":       self.reasoning,

            "bias":       self.bias,
            "amdPhase":   amd,
            "killzone":   killzone,
            "htfBias":    "BEARISH — Daily below 20EMA, 15m lower highs",
            "sessionLevels": f"Key levels: {self.or_high:.2f} (OR high) | {self.or_low:.2f} (OR low) | {self.vwap:.2f} (VWAP)",
            "market_structure": "LH/LL on 15m — bearish structure",

            "fair_value_gaps": fvg_text,
            "order_blocks":    ob_text,
            "liquidity_pools": liq_text,
            "choch":           choch,
            "inducement":      "None detected" if random.random() > 0.2 else "Possible inducement at session high",
            "mtf_alignment":   mtf,
            "delta_trend":     "POSITIVE — net buyers last 3 bars" if self.cum_delta > 0 else "NEGATIVE — net sellers last 3 bars",

            "vwap":        self.vwap,
            "sessionHigh": self.session_high,
            "sessionLow":  self.session_low,
            "volume":      self.volume,
            "cumDelta":    self.cum_delta,
            "deltaLastBar": random.randint(-25, 25),
            "candleText":  candles,

            "orHigh":             self.or_high,
            "orLow":              self.or_low,
            "orBrokenUp":         or_broken_up,
            "orBrokenDown":       or_broken_down,
            "or_direction":       "BULL",
            "or_relative_volume": round(random.uniform(85, 145), 1),

            "newsText":       "No major USD events in next hour — clean technical window",
            "newsDangerZone": random.random() < 0.05,  # 5% chance danger zone active
            "nextEventFull":  "FOMC Minutes 14:00 ET" if 13 <= now_et.hour < 14 else None,
            "ibkrHeadlines":  headlines,

            "trades": self.trades[-10:],  # last 10 trades
        }


def write_dashboard(data: dict):
    tmp = str(DASHBOARD_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, str(DASHBOARD_FILE))


def main():
    print("=" * 55)
    print("  MNQ AI TRADER — DEMO MODE")
    print("  Pumping realistic data at 10x speed")
    print("=" * 55)
    print(f"  Dashboard: http://localhost:8080/dashboard.html")
    print(f"  Mobile:    http://localhost:8080/mobile.html")
    print(f"  Writing:   {DASHBOARD_FILE}")
    print("  Press Ctrl+C to stop")
    print("=" * 55)

    sim      = MarketSim()
    interval = 0.2   # 200ms = 10x the normal 2s refresh

    tick = 0
    while True:
        try:
            sim.step()
            data = sim.build_snapshot()
            write_dashboard(data)

            tick += 1
            if tick % 25 == 0:  # print status every 5 simulated seconds
                wins   = sum(1 for t in sim.trades if t["pnl"] > 0)
                losses = sum(1 for t in sim.trades if t["pnl"] < 0)
                print(
                    f"  {data['time_et']}  "
                    f"Price: {sim.price:.2f}  "
                    f"P&L: ${sim.daily_pnl:+.2f}  "
                    f"Trades: {len(sim.trades)} ({wins}W/{losses}L)  "
                    f"Phase: {sim.phase}"
                )

            time.sleep(interval)

        except KeyboardInterrupt:
            print("\nDemo stopped.")
            break
        except Exception as e:
            print(f"Demo error: {e}")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
