"""
Data Recorder — MNQ AI Trader
==============================
Records live session data to disk for backtest replay.

Two JSONL files written per trading day:
  data/snapshots_YYYY-MM-DD.jsonl   — every get_snapshot() output (~5s cadence)
  data/decisions_YYYY-MM-DD.jsonl   — every Claude API call (input + output)

JSONL format: one JSON object per line, newline-delimited.
Fast to write (append-only), fast to read (stream line-by-line), pandas-friendly.

Design goals:
  - Zero impact on live trading latency (writes are sync but tiny, <1ms)
  - Self-contained: each line is a complete, independent record
  - Replay-ready: backtest engine reads these files directly, no IBKR needed
  - Version-tagged: every record includes bot_version so you can compare
    V3.0 vs V4.0 decisions on the same day's data

Usage:
  from data_recorder import recorder
  recorder.record_snapshot(snapshot)             # called from ibkr_feed
  recorder.record_decision(snapshot, response)   # called from claude_brain
"""

import json
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytz

from config import DATA_DIR, RECORDING_ENABLED
from logger import logger

# ── Constants ──────────────────────────────────────────────
BOT_VERSION    = "3.0"
eastern        = pytz.timezone("US/Eastern")

# Fields to EXCLUDE from snapshot recording — large, redundant, or not
# useful for replay (raw bar lists, full DOM text, etc.)
_SNAPSHOT_EXCLUDE = {
    "candles",           # large text block, bars are in bar files
    "dom_text",          # raw DOM string — signals extracted separately
    "volume_profile",    # large text block
    "news_text",         # full news calendar text — events_today has it
    "events_today",      # full event list — rarely needed in replay
    "opening_range",     # formatted text — raw fields (or_high, or_low) kept
    "htf_bias",          # formatted text — raw HTF data kept
    "market_structure",  # formatted text — raw CHoCH/inducement kept
}

# Snapshot cadence control — don't write more than once per N seconds
# The live bot calls get_snapshot() ~6x/second during active periods
_SNAPSHOT_MIN_INTERVAL = 5.0   # seconds


class DataRecorder:
    """
    Thread-safe recorder. Singleton — use the module-level `recorder` instance.
    """

    def __init__(self) -> None:
        self._enabled         = RECORDING_ENABLED
        self._lock            = threading.Lock()
        self._snap_file       = None     # open file handle
        self._dec_file        = None     # open file handle
        self._current_date    = None     # "YYYY-MM-DD" of open files
        self._last_snap_time  = 0.0
        self._snap_count      = 0
        self._dec_count       = 0
        self._data_dir        = Path(DATA_DIR)

        if self._enabled:
            try:
                self._data_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Data recorder initialized — writing to {self._data_dir}")
            except Exception as e:
                logger.warning(f"Data recorder: could not create data dir: {e}")
                self._enabled = False

    # ── File management ────────────────────────────────────

    def _date_str(self) -> str:
        return datetime.now(eastern).strftime("%Y-%m-%d")

    def _ensure_files_open(self) -> bool:
        """Open (or rotate) files for today's date. Returns True if ready."""
        today = self._date_str()
        if self._current_date == today and self._snap_file and self._dec_file:
            return True
        try:
            # Close old files
            if self._snap_file:
                self._snap_file.close()
            if self._dec_file:
                self._dec_file.close()

            snap_path = self._data_dir / f"snapshots_{today}.jsonl"
            dec_path  = self._data_dir / f"decisions_{today}.jsonl"

            # Open in append mode — safe to restart bot mid-day
            self._snap_file    = open(snap_path, "a", encoding="utf-8")
            self._dec_file     = open(dec_path,  "a", encoding="utf-8")
            self._current_date = today

            if self._snap_count == 0:
                logger.info(
                    f"Recording to: snapshots_{today}.jsonl + decisions_{today}.jsonl"
                )
            return True
        except Exception as e:
            logger.warning(f"Data recorder: failed to open files: {e}")
            return False

    def _write_line(self, fh, record: dict) -> None:
        """Write one JSON line. Caller holds lock."""
        try:
            fh.write(json.dumps(record, default=str) + "\n")
            fh.flush()
        except Exception as e:
            logger.debug(f"Data recorder write error: {e}")

    # ── Public API ─────────────────────────────────────────

    def record_snapshot(self, snapshot: dict) -> None:
        """
        Record a market snapshot. Called from ibkr_feed.get_snapshot().
        Throttled to _SNAPSHOT_MIN_INTERVAL seconds to avoid spamming disk.
        """
        if not self._enabled:
            return

        now = time.time()
        if now - self._last_snap_time < _SNAPSHOT_MIN_INTERVAL:
            return
        self._last_snap_time = now

        with self._lock:
            if not self._ensure_files_open():
                return

            # Strip large/redundant fields before writing
            slim = {k: v for k, v in snapshot.items() if k not in _SNAPSHOT_EXCLUDE}

            record = {
                "ts":          datetime.now(timezone.utc).isoformat(),
                "ts_et":       snapshot.get("time_et", ""),
                "bot_version": BOT_VERSION,
                "type":        "snapshot",
                "data":        slim,
            }
            self._write_line(self._snap_file, record)
            self._snap_count += 1

    def record_decision(
        self,
        snapshot: dict,
        raw_response: str,
        parsed_decision: dict,
        model: str,
        cost_usd: float,
        pre_filter_reason: str = "",
    ) -> None:
        """
        Record a Claude entry decision (input + output).
        Called from claude_brain.analyze_market() after every real Opus call.

        Stores:
          - The snapshot that triggered the call (slim version)
          - The raw Claude response text
          - The parsed decision dict
          - Cost, model, version metadata
          - The pre-filter reason that allowed the call through

        This is the replay cache — when backtesting, if the same snapshot
        timestamp is encountered and the pre-filter passes, this decision
        is returned immediately without calling Claude.
        """
        if not self._enabled:
            return

        with self._lock:
            if not self._ensure_files_open():
                return

            slim = {k: v for k, v in snapshot.items() if k not in _SNAPSHOT_EXCLUDE}

            record = {
                "ts":                 datetime.now(timezone.utc).isoformat(),
                "ts_et":              snapshot.get("time_et", ""),
                "bot_version":        BOT_VERSION,
                "type":               "decision",
                "model":              model,
                "cost_usd":           round(cost_usd, 6),
                "pre_filter_reason":  pre_filter_reason,
                "snapshot":           slim,
                "raw_response":       raw_response,
                "decision":           parsed_decision,
            }
            self._write_line(self._dec_file, record)
            self._dec_count += 1

    def record_trade(self, action: str, entry_price: float, exit_price: float,
                     pnl: float, reason: str, mode: str) -> None:
        """
        Record a completed trade. Called from executor after close.
        Separate from decision records — these are actual fills.
        """
        if not self._enabled:
            return

        with self._lock:
            if not self._ensure_files_open():
                return

            record = {
                "ts":          datetime.now(timezone.utc).isoformat(),
                "bot_version": BOT_VERSION,
                "type":        "trade",
                "action":      action,
                "entry":       entry_price,
                "exit":        exit_price,
                "pnl":         pnl,
                "mode":        mode,
                "reason":      reason[:200],
            }
            self._write_line(self._dec_file, record)

    def flush_and_close(self) -> None:
        """Call at EOD or on Ctrl+C to ensure files are closed cleanly."""
        with self._lock:
            try:
                if self._snap_file:
                    self._snap_file.flush()
                    self._snap_file.close()
                    self._snap_file = None
                if self._dec_file:
                    self._dec_file.flush()
                    self._dec_file.close()
                    self._dec_file = None
            except Exception as e:
                logger.debug(f"Data recorder close error: {e}")
            logger.info(
                f"Data recorder closed — {self._snap_count} snapshots, "
                f"{self._dec_count} decisions recorded today"
            )

    def daily_summary(self) -> dict:
        """Return today's recording stats."""
        return {
            "snapshots":   self._snap_count,
            "decisions":   self._dec_count,
            "data_dir":    str(self._data_dir),
            "date":        self._current_date,
            "enabled":     self._enabled,
        }


# ── Module-level singleton ─────────────────────────────────
recorder = DataRecorder()
