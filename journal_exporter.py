"""
journal_exporter.py — MNQ AI Trader
=====================================
Builds journal_data.json from all recorded decisions_YYYY-MM-DD.jsonl files.

Output schema:
  {
    "account":          "MNQ Paper",
    "starting_balance": 50000,
    "last_updated":     "...",
    "equity_curve":     [{"date", "equity", "daily_pnl", "trades"}],
    "by_strategy":      {"SCALP": {"trades","wins","losses","pnl","win_rate"}},
    "by_hour":          {"9": {"trades","wins","losses","pnl","win_rate"}},
    "daily_pnl":        [{"date","pnl","trades","wins","losses"}],
    "ofi_performance":  {"STRONG_BUY": {"trades","wins","pnl"}, ...},
    "thesis_buckets":   {"70-75":{"trades","wins","pnl"}, "75-80":...,
                          "80-85":..., "85+":...}
  }

Reads ONLY type="trade" and type="decision" records from decisions JSONL files.
Rebuilds from all available files each run so equity_curve accumulates across days.
Writes to BASE_DIR/journal_data.json.

Called from learning_session.run_learning_session() at EOD.
Can also be run standalone: py -3.11 journal_exporter.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytz

BASE_DIR = Path(os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"))
sys.path.insert(0, str(BASE_DIR))

eastern          = pytz.timezone("US/Eastern")
JOURNAL_FILE     = BASE_DIR / "journal_data.json"
DATA_DIR         = BASE_DIR / "data"

# OFI signals present in the data
_OFI_SIGNALS = ("STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL")

# Thesis probability bucket boundaries (lower inclusive, upper exclusive, except last)
_THESIS_BUCKETS = [
    ("70-75",  70, 75),
    ("75-80",  75, 80),
    ("80-85",  80, 85),
    ("85+",    85, 101),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_ts_et(ts_utc: str) -> datetime | None:
    """Parse UTC ISO timestamp string → ET-localised datetime, or None."""
    try:
        dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
        return dt.astimezone(eastern)
    except Exception:
        return None


def _et_hour(ts_utc: str) -> str | None:
    dt = _parse_ts_et(ts_utc)
    return str(dt.hour) if dt else None


def _et_date(ts_utc: str) -> str | None:
    dt = _parse_ts_et(ts_utc)
    return dt.strftime("%Y-%m-%d") if dt else None


def _empty_stats() -> dict:
    return {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}


def _win_rate(stats: dict) -> float:
    t = stats["trades"]
    return round(stats["wins"] / t * 100, 1) if t else 0.0


def _add_trade_to(stats: dict, pnl: float) -> None:
    stats["trades"] += 1
    stats["pnl"]    += pnl
    if pnl > 0:
        stats["wins"] += 1
    else:
        stats["losses"] += 1


def _finalize_stats(stats: dict) -> dict:
    out = dict(stats)
    out["pnl"]      = round(out["pnl"], 2)
    out["win_rate"] = _win_rate(stats)
    return out


# ── File loading ──────────────────────────────────────────────────────────────

def _load_day(date_str: str) -> tuple[list, list]:
    """
    Read decisions_YYYY-MM-DD.jsonl for date_str.
    Returns (trades, decisions) — both lists of raw dicts.
    """
    path = DATA_DIR / f"decisions_{date_str}.jsonl"
    if not path.exists():
        return [], []

    trades    = []
    decisions = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rec_type = rec.get("type", "")
                if rec_type == "trade":
                    trades.append(rec)
                elif rec_type == "decision":
                    decisions.append(rec)
    except Exception as e:
        print(f"[journal] Warning: could not read {path}: {e}")

    return trades, decisions


def _match_decision(trade: dict, decisions: list) -> dict | None:
    """
    Find the decision record that most likely triggered this trade.
    Looks for the latest type="decision" with action BUY or SELL whose
    timestamp is at or before the trade timestamp.
    """
    trade_ts_str = trade.get("ts", "")
    try:
        trade_ts = datetime.fromisoformat(trade_ts_str.replace("Z", "+00:00"))
    except Exception:
        return None

    best     = None
    best_ts  = None

    for dec in decisions:
        action = (dec.get("decision") or {}).get("decision", "")
        if action not in ("BUY", "SELL"):
            continue
        try:
            dec_ts = datetime.fromisoformat(dec["ts"].replace("Z", "+00:00"))
        except Exception:
            continue
        if dec_ts > trade_ts:
            continue
        if best_ts is None or dec_ts > best_ts:
            best    = dec
            best_ts = dec_ts

    return best


def _available_dates() -> list[str]:
    """Return sorted list of dates that have decisions JSONL files."""
    paths = sorted(DATA_DIR.glob("decisions_*.jsonl"))
    dates = []
    for p in paths:
        stem = p.stem  # decisions_YYYY-MM-DD
        date_part = stem[len("decisions_"):]
        if len(date_part) == 10:
            dates.append(date_part)
    return dates


# ── Core build ────────────────────────────────────────────────────────────────

def build_journal(starting_balance: float, account_name: str) -> dict:
    """
    Scan all decisions JSONL files and build the full journal dict.
    """
    # Accumulators
    daily_rows:     list[dict]        = []   # one entry per day
    by_strategy:    dict[str, dict]   = {}
    by_hour:        dict[str, dict]   = {}
    ofi_perf:       dict[str, dict]   = {s: _empty_stats() for s in _OFI_SIGNALS}
    thesis_buckets: dict[str, dict]   = {name: _empty_stats() for name, *_ in _THESIS_BUCKETS}

    for date_str in _available_dates():
        trades, decisions = _load_day(date_str)
        if not trades:
            continue

        day_pnl   = 0.0
        day_wins  = 0
        day_losses = 0

        for trade in trades:
            pnl    = float(trade.get("pnl", 0.0))
            mode   = trade.get("mode", "UNKNOWN").upper()
            ts_utc = trade.get("ts", "")

            day_pnl   += pnl
            if pnl > 0:
                day_wins += 1
            else:
                day_losses += 1

            # ── by_strategy ──────────────────────────────────
            if mode not in by_strategy:
                by_strategy[mode] = _empty_stats()
            _add_trade_to(by_strategy[mode], pnl)

            # ── by_hour ──────────────────────────────────────
            hour = _et_hour(ts_utc)
            if hour:
                if hour not in by_hour:
                    by_hour[hour] = _empty_stats()
                _add_trade_to(by_hour[hour], pnl)

            # ── decision-linked analytics ─────────────────────
            matched = _match_decision(trade, decisions)
            if matched:
                snap   = matched.get("snapshot", {})
                dec    = matched.get("decision", {})

                # OFI performance
                ofi_signal = (snap.get("ofi") or {}).get("signal", "NEUTRAL")
                if ofi_signal in ofi_perf:
                    _add_trade_to(ofi_perf[ofi_signal], pnl)

                # Thesis buckets
                prob = dec.get("thesis_probability", 0)
                if isinstance(prob, (int, float)) and prob > 0:
                    for bucket_name, lo, hi in _THESIS_BUCKETS:
                        if lo <= prob < hi:
                            _add_trade_to(thesis_buckets[bucket_name], pnl)
                            break

        n_trades = day_wins + day_losses
        daily_rows.append({
            "date":    date_str,
            "pnl":     round(day_pnl, 2),
            "trades":  n_trades,
            "wins":    day_wins,
            "losses":  day_losses,
        })

    # ── Equity curve ──────────────────────────────────────────
    equity         = starting_balance
    equity_curve   = []
    for row in daily_rows:
        equity += row["pnl"]
        equity_curve.append({
            "date":      row["date"],
            "equity":    round(equity, 2),
            "daily_pnl": row["pnl"],
            "trades":    row["trades"],
        })

    # ── Finalize group stats ──────────────────────────────────
    return {
        "account":          account_name,
        "starting_balance": starting_balance,
        "last_updated":     datetime.now(timezone.utc).isoformat(),
        "equity_curve":     equity_curve,
        "by_strategy":      {k: _finalize_stats(v) for k, v in by_strategy.items()},
        "by_hour":          {k: _finalize_stats(v) for k, v in sorted(by_hour.items(), key=lambda x: int(x[0]))},
        "daily_pnl":        daily_rows,
        "ofi_performance":  {k: _finalize_stats(v) for k, v in ofi_perf.items()},
        "thesis_buckets":   {k: _finalize_stats(v) for k, v in thesis_buckets.items()},
    }


# ── Public entry point ────────────────────────────────────────────────────────

def run() -> None:
    """
    Build journal_data.json from all available session recordings.
    Called from learning_session.run_learning_session() at EOD.
    """
    try:
        from config import ACCOUNT_SIZE, BASE_DIR as CFG_BASE
        starting_balance = float(ACCOUNT_SIZE)
        account_name     = f"MNQ Paper (${int(starting_balance):,})"
    except Exception:
        starting_balance = 50_000.0
        account_name     = "MNQ Paper"

    print("[journal] Building journal_data.json from all session recordings...")

    journal = build_journal(starting_balance, account_name)

    n_days   = len(journal["daily_pnl"])
    n_trades = sum(r["trades"] for r in journal["daily_pnl"])
    total_pnl = sum(r["pnl"]    for r in journal["daily_pnl"])

    out_path = BASE_DIR / "journal_data.json"
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(journal, fh, indent=2)
        print(
            f"[journal] Wrote {out_path.name} — "
            f"{n_days} days | {n_trades} trades | total P&L ${total_pnl:+.2f}"
        )
    except Exception as e:
        print(f"[journal] ERROR writing {out_path}: {e}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run()
