"""
Backtester — MNQ AI Trader
===========================
Replays a recorded trading day against the current bot logic.
Zero IBKR connection, zero real-time waiting.

Usage:
  py -3.11 backtester.py --date 2026-05-22
  py -3.11 backtester.py --date 2026-05-22 --version v3.0 --verbose
  py -3.11 backtester.py --list

How it works:
  1. Load snapshots_YYYY-MM-DD.jsonl  → market data (5s cadence)
  2. Load decisions_YYYY-MM-DD.jsonl  → cached Claude responses
  3. For each snapshot, run the CURRENT pre-filter code
  4. If pre-filter passes → look up cached Claude decision by timestamp
     - Found: use cached decision instantly (free, <1ms)
     - Not found: call Claude (only for new setups the old pre-filter blocked)
  5. Run simulated executor: track position, apply stops/targets from
     subsequent snapshots, record P&L
  6. Print report

The result: a full day's backtest in under 5 seconds.

Comparing versions:
  Run backtester.py --date 2026-05-22           → current code
  Edit bot code for V4.0
  Run backtester.py --date 2026-05-22 again     → V4.0
  The difference in output IS the improvement (or regression).

For archiving a version's decisions, the recorder tags each record with
bot_version. Future versions of this script can filter by version.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytz

# ── Bootstrap path so we can import bot modules ────────────
# This file lives alongside the bot files in the trading dir.
sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR, TICK_SIZE, TICK_VALUE
from claude_brain import pre_filter_signal, analyze_market, parse_decision

eastern = pytz.timezone("US/Eastern")

# ── Constants ──────────────────────────────────────────────
DATA_PATH = Path(DATA_DIR)

# Simulated fill latency — market order fills at next snapshot's price.
# This is conservative (slightly pessimistic) for 1-contract MNQ.
FILL_NEXT_SNAPSHOT = True


# ── Data loading ───────────────────────────────────────────

def load_snapshots(date_str: str) -> list[dict]:
    path = DATA_PATH / f"snapshots_{date_str}.jsonl"
    if not path.exists():
        print(f"ERROR: No snapshot file for {date_str}")
        print(f"  Expected: {path}")
        print(f"  Run the bot live first to record data.")
        sys.exit(1)
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    print(f"Loaded {len(records)} snapshots from {path.name}")
    return records


def load_decisions(date_str: str) -> dict:
    """Load decisions keyed by ts_et (HH:MM) for fast lookup."""
    path = DATA_PATH / f"decisions_{date_str}.jsonl"
    if not path.exists():
        print(f"NOTE: No decisions file for {date_str} — all calls will hit Claude API")
        return {}
    decisions = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("type") == "decision":
                    # Key by ts_et (HH:MM) for exact match
                    key = rec.get("ts_et", "")
                    if key:
                        decisions[key] = rec
            except json.JSONDecodeError:
                continue
    print(f"Loaded {len(decisions)} cached decisions from {path.name}")
    return decisions


# ── Simulated executor ─────────────────────────────────────

class SimExecutor:
    """
    Minimal simulated executor. No IBKR, no threading.
    Tracks position state and applies stop/target logic
    against subsequent snapshots.
    """

    def __init__(self):
        self.position      = 0       # 0 flat, 1 long, -1 short
        self.entry_price   = 0.0
        self.entry_time    = ""
        self.stop_price    = 0.0
        self.target_price  = 0.0
        self.mode          = "NONE"

        self.trades        = []
        self.daily_pnl     = 0.0
        self.wins          = 0
        self.losses        = 0

    def enter(self, action: str, fill_price: float, stop: float,
              target: float, mode: str, time_et: str, reasoning: str) -> None:
        if self.position != 0:
            return
        self.position    = 1 if action == "BUY" else -1
        self.entry_price = fill_price
        self.entry_time  = time_et
        self.stop_price  = stop
        self.target_price = target
        self.mode        = mode
        print(f"  SIM ENTER: {action} @ {fill_price:.2f} | Stop:{stop:.2f} Target:{target:.2f}")

    def check_exit(self, snapshot: dict) -> Optional[dict]:
        """
        Check if current position should be closed by stop/target.
        Returns trade record if closed, None otherwise.
        """
        if self.position == 0:
            return None
        price = snapshot.get("last_price", 0) or 0
        if not price:
            return None

        exit_reason = None
        exit_price  = price

        if self.position == 1:  # long
            if self.stop_price > 0 and price <= self.stop_price:
                exit_reason = "STOP"
                exit_price  = self.stop_price   # fill at stop level
            elif self.target_price > 0 and price >= self.target_price:
                exit_reason = "TARGET"
                exit_price  = self.target_price  # fill at target

        elif self.position == -1:  # short
            if self.stop_price > 0 and price >= self.stop_price:
                exit_reason = "STOP"
                exit_price  = self.stop_price
            elif self.target_price > 0 and price <= self.target_price:
                exit_reason = "TARGET"
                exit_price  = self.target_price

        if exit_reason:
            return self._close(exit_price, exit_reason, snapshot.get("time_et", ""))
        return None

    def force_close(self, snapshot: dict, reason: str = "EOD") -> Optional[dict]:
        """Close position at current price (EOD or Claude CLOSE decision)."""
        if self.position == 0:
            return None
        price = snapshot.get("last_price", 0) or self.entry_price
        return self._close(price, reason, snapshot.get("time_et", ""))

    def _close(self, exit_price: float, reason: str, time_et: str) -> dict:
        was_long = self.position == 1
        diff = (exit_price - self.entry_price) if was_long else (self.entry_price - exit_price)
        pnl  = (diff / TICK_SIZE) * TICK_VALUE

        self.daily_pnl += pnl
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

        trade = {
            "entry_time":  self.entry_time,
            "exit_time":   time_et,
            "direction":   "LONG" if was_long else "SHORT",
            "entry":       self.entry_price,
            "exit":        exit_price,
            "pnl":         round(pnl, 2),
            "reason":      reason,
            "mode":        self.mode,
        }
        self.trades.append(trade)
        self.position    = 0
        self.entry_price = 0.0
        self.stop_price  = 0.0
        self.target_price = 0.0
        print(f"  SIM EXIT:  @ {exit_price:.2f} | P&L: ${pnl:+.2f} ({reason})")
        return trade


# ── Backtest replay ────────────────────────────────────────

def run_backtest(date_str: str, verbose: bool = False,
                 use_claude_for_uncached: bool = True) -> dict:
    """
    Core replay loop. Returns results dict.
    """
    print(f"\n{'='*60}")
    print(f"BACKTEST — {date_str}")
    print(f"{'='*60}\n")

    t_start = time.time()

    snapshots = load_snapshots(date_str)
    decisions = load_decisions(date_str)

    executor    = SimExecutor()
    api_calls   = 0
    cache_hits  = 0
    pre_filter_passes = 0
    pre_filter_total  = 0
    api_cost    = 0.0

    # Process each snapshot in chronological order
    for i, snap_rec in enumerate(snapshots):
        snapshot = snap_rec.get("data", {})
        ts_et    = snap_rec.get("ts_et", "")

        # First check if open position should be closed by stop/target
        if executor.position != 0:
            trade = executor.check_exit(snapshot)
            if trade and verbose:
                print(f"    [{ts_et}] {trade['direction']} closed by {trade['reason']}")

        # Pre-filter — runs current code against recorded market data
        pre_filter_total += 1
        worth_calling, filter_reason = pre_filter_signal(snapshot)

        if not worth_calling:
            continue

        pre_filter_passes += 1

        # Already in position — don't try to enter again
        if executor.position != 0:
            if verbose:
                print(f"  [{ts_et}] Pre-filter PASS ({filter_reason}) — already in position, skip")
            continue

        if verbose:
            print(f"  [{ts_et}] Pre-filter PASS: {filter_reason}")

        # Look up cached Claude decision
        decision = None
        if ts_et in decisions:
            cached_rec = decisions[ts_et]
            decision   = cached_rec.get("decision", {})
            cache_hits += 1
            if verbose:
                d = decision.get("decision", "HOLD")
                print(f"    CACHE HIT → {d} (conf: {decision.get('confidence','?')})")
        else:
            # No cached decision — either call Claude or skip
            if use_claude_for_uncached:
                try:
                    snapshot["_pre_filter_reason"] = filter_reason
                    decision = analyze_market(snapshot)
                    api_calls += 1
                    api_cost  += 0.05  # rough estimate
                    if verbose:
                        print(f"    CLAUDE CALL → {decision.get('decision','?')}")
                except Exception as e:
                    if verbose:
                        print(f"    Claude error: {e}")
                    continue
            else:
                if verbose:
                    print(f"    No cached decision — skipping (use --live-claude to call API)")
                continue

        if not decision:
            continue

        action = decision.get("decision", "HOLD")

        # Execute the decision
        if action in ("BUY", "SELL") and executor.position == 0:
            # Fill at NEXT snapshot's price (simulated market order latency)
            fill_price = snapshot.get("last_price", 0) or 0
            if i + 1 < len(snapshots) and FILL_NEXT_SNAPSHOT:
                next_snap = snapshots[i + 1].get("data", {})
                fill_price = next_snap.get("last_price", fill_price)

            stop   = decision.get("stop_price", 0) or 0
            target = decision.get("target_1", 0) or 0
            mode   = decision.get("mode", "SCALP")
            reasoning = decision.get("reasoning", "")

            if fill_price > 0 and stop > 0:
                executor.enter(action, fill_price, stop, target, mode, ts_et, reasoning)

        elif action == "CLOSE" and executor.position != 0:
            executor.force_close(snapshot, reason="Claude CLOSE")

    # Close any open position at end of session (EOD)
    if executor.position != 0 and snapshots:
        last_snap = snapshots[-1].get("data", {})
        executor.force_close(last_snap, reason="EOD_FLAT")

    elapsed = time.time() - t_start

    return {
        "date":              date_str,
        "elapsed_secs":      round(elapsed, 2),
        "snapshots":         len(snapshots),
        "pre_filter_total":  pre_filter_total,
        "pre_filter_passes": pre_filter_passes,
        "cache_hits":        cache_hits,
        "api_calls":         api_calls,
        "api_cost_est":      round(api_cost, 4),
        "trades":            executor.trades,
        "trade_count":       len(executor.trades),
        "wins":              executor.wins,
        "losses":            executor.losses,
        "daily_pnl":         round(executor.daily_pnl, 2),
        "win_rate":          round(executor.wins / max(len(executor.trades), 1) * 100, 1),
    }


# ── Report printer ─────────────────────────────────────────

def print_report(results: dict) -> None:
    r = results
    trades = r["trades"]

    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS - {r['date']}")
    print(f"{'='*60}")
    print(f"  Replay time:    {r['elapsed_secs']}s  ({r['snapshots']} snapshots)")
    print(f"  Pre-filter:     {r['pre_filter_passes']}/{r['pre_filter_total']} passed")
    print(f"  Cache:          {r['cache_hits']} hits / {r['api_calls']} new Claude calls")
    if r["api_cost_est"] > 0:
        print(f"  API cost est:   ~${r['api_cost_est']}")
    print(f"{'-'*60}")
    print(f"  TRADES:         {r['trade_count']}")
    print(f"  Wins / Losses:  {r['wins']}W / {r['losses']}L")
    if r["trade_count"] > 0:
        print(f"  Win rate:       {r['win_rate']}%")
    print(f"  P&L:            ${r['daily_pnl']:+.2f}")
    print(f"{'-'*60}")

    if trades:
        print(f"  Trade log:")
        for t in trades:
            pnl_str = f"${t['pnl']:+.2f}"
            print(
                f"    {t['entry_time']} {t['direction']:<5} "
                f"entry={t['entry']:.2f} exit={t['exit']:.2f} "
                f"P&L={pnl_str:<9} [{t['reason']}]"
            )
    else:
        print(f"  No trades.")

    print(f"{'='*60}\n")


def list_available_dates() -> None:
    """List all dates that have recorded data."""
    if not DATA_PATH.exists():
        print(f"No data directory found at {DATA_PATH}")
        print("Run the bot live first to record data.")
        return
    snap_files = sorted(DATA_PATH.glob("snapshots_*.jsonl"))
    if not snap_files:
        print(f"No recorded sessions found in {DATA_PATH}")
        return
    print(f"\nAvailable backtest dates ({len(snap_files)} sessions):")
    for f in snap_files:
        date_str = f.stem.replace("snapshots_", "")
        dec_file = DATA_PATH / f"decisions_{date_str}.jsonl"
        has_dec  = "[OK] decisions" if dec_file.exists() else "     no decisions"
        size_kb  = f.stat().st_size // 1024
        print(f"  {date_str}  {has_dec}  ({size_kb}KB snapshots)")
    print()


# ── CLI ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MNQ AI Trader — Backtest Replay Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  py -3.11 backtester.py --list
  py -3.11 backtester.py --date 2026-05-22
  py -3.11 backtester.py --date 2026-05-22 --verbose
  py -3.11 backtester.py --date 2026-05-22 --no-live-claude
        """,
    )
    parser.add_argument(
        "--date", "-d",
        help="Date to replay (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all available recorded sessions",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show every pre-filter pass and decision",
    )
    parser.add_argument(
        "--no-live-claude",
        action="store_true",
        help="Skip uncached decisions instead of calling Claude (faster, free)",
    )
    args = parser.parse_args()

    if args.list:
        list_available_dates()
        return

    if not args.date:
        parser.print_help()
        print("\nError: --date required. Use --list to see available sessions.")
        sys.exit(1)

    use_claude = not args.no_live_claude

    results = run_backtest(
        date_str=args.date,
        verbose=args.verbose,
        use_claude_for_uncached=use_claude,
    )
    print_report(results)


if __name__ == "__main__":
    main()
