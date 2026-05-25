"""
journal_exporter.py — MNQ AI Trader
=====================================
Builds journal_data.json from all recorded decisions_YYYY-MM-DD.jsonl files.

Output schema:
  {
    "account":          "MNQ Paper",
    "bot_version":      "4.3.0",
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

# Tag rebuilt journal output with the running bot version so consumers
# (UI / future migrations) can tell what schema produced the file.
try:
    from config import VERSION as BOT_VERSION
except ImportError:
    BOT_VERSION = "4.3.0"

# OFI signals present in the data
_OFI_SIGNALS = ("STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL")

# Thesis probability bucket boundaries (lower inclusive, upper exclusive, except last)
_THESIS_BUCKETS = [
    ("70-75",  70, 75),
    ("75-80",  75, 80),
    ("80-85",  80, 85),
    ("85+",    85, 101),
]

# MNQ contract constants used for R:R estimation when stop_price is absent
_MNQ_TICK_VALUE         = 0.50   # $ per tick per contract
_MNQ_DEFAULT_RISK_TICKS = 40     # assumed initial stop when not recorded (~10 pts = $20)


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


def _calc_trade_rr(trade: dict, matched: dict | None) -> float | None:
    """
    Compute R:R for one trade.
    Tries exact price fields first; falls back to pnl-based estimate when
    stop_price is absent (uses _MNQ_DEFAULT_RISK_TICKS as assumed risk).
    Returns None if there is no usable data at all.
    """
    def _f(v):
        try:
            return float(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    entry  = _f(trade.get("entry_price") or trade.get("entry"))
    exit_p = _f(trade.get("exit_price")  or trade.get("exit"))
    stop   = _f(trade.get("stop_price")  or trade.get("stop"))
    pnl    = _f(trade.get("pnl")) or 0.0

    if matched:
        dec = matched.get("decision", {})
        if stop is None:
            stop = _f(dec.get("stop_price") or dec.get("stop"))
        if entry is None:
            entry = _f(dec.get("entry_price") or dec.get("entry"))

    estimated_risk_pts = _MNQ_DEFAULT_RISK_TICKS * 0.25  # points

    if entry is not None and exit_p is not None and entry != exit_p:
        move = abs(exit_p - entry)
        if stop is not None:
            risk = abs(stop - entry)
            if risk > 0:
                return move / risk
        return move / estimated_risk_pts

    if pnl != 0.0:
        return abs(pnl) / (_MNQ_DEFAULT_RISK_TICKS * _MNQ_TICK_VALUE)

    return None


def _profitability_zone(win_rate: float, avg_rr: float) -> str:
    if win_rate >= 60 and avg_rr >= 2.0:
        return "PROFITABLE"
    if win_rate >= 50 or avg_rr >= 1.5:
        return "BREAK_EVEN"
    return "NOT_PROFITABLE"


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
    all_rr_values:  list[float]       = []
    week_data:      dict[str, dict]   = {}   # ISO-week → {rr_values, wins, total}
    day_rr_data:    dict[str, list]   = {}   # date → list of rr values

    for date_str in _available_dates():
        trades, decisions = _load_day(date_str)
        if not trades:
            continue

        day_pnl    = 0.0
        day_wins   = 0
        day_losses = 0
        day_rr_values: list[float] = []

        try:
            week_key = datetime.strptime(date_str, "%Y-%m-%d").strftime("%G-W%V")
        except ValueError:
            week_key = None
        if week_key and week_key not in week_data:
            week_data[week_key] = {"rr_values": [], "wins": 0, "total": 0}

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

            rr = _calc_trade_rr(trade, matched)
            if rr is not None and rr >= 0:
                all_rr_values.append(rr)
                day_rr_values.append(rr)
            if week_key:
                week_data[week_key]["total"] += 1
                if pnl > 0:
                    week_data[week_key]["wins"] += 1
                if rr is not None and rr >= 0:
                    week_data[week_key]["rr_values"].append(rr)

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

        day_rr_data[date_str] = day_rr_values

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

    # ── R:R summary ───────────────────────────────────────────
    avg_rr = round(sum(all_rr_values) / len(all_rr_values), 2) if all_rr_values else 0.0

    total_trades_all = sum(r["trades"] for r in daily_rows)
    total_wins_all   = sum(r["wins"]   for r in daily_rows)
    overall_win_rate = round(total_wins_all / total_trades_all * 100, 1) if total_trades_all > 0 else 0.0

    profitability_zone = _profitability_zone(overall_win_rate, avg_rr)

    rr_by_week = []
    for wk in sorted(week_data.keys()):
        wd = week_data[wk]
        wk_rrs      = wd["rr_values"]
        wk_avg_rr   = round(sum(wk_rrs) / len(wk_rrs), 2) if wk_rrs else 0.0
        wk_win_rate = round(wd["wins"] / wd["total"] * 100, 1) if wd["total"] > 0 else 0.0
        rr_by_week.append({
            "week":     wk,
            "avg_rr":   wk_avg_rr,
            "win_rate": wk_win_rate,
            "zone":     _profitability_zone(wk_win_rate, wk_avg_rr),
        })

    zone_history = []
    for row in daily_rows:
        d      = row["date"]
        drrs   = day_rr_data.get(d, [])
        d_avg_rr   = round(sum(drrs) / len(drrs), 2) if drrs else 0.0
        d_win_rate = round(row["wins"] / row["trades"] * 100, 1) if row["trades"] > 0 else 0.0
        zone_history.append({
            "date":     d,
            "win_rate": d_win_rate,
            "avg_rr":   d_avg_rr,
            "zone":     _profitability_zone(d_win_rate, d_avg_rr),
        })

    # ── Finalize group stats ──────────────────────────────────
    return {
        "account":          account_name,
        "bot_version":      BOT_VERSION,
        "starting_balance": starting_balance,
        "last_updated":     datetime.now(timezone.utc).isoformat(),
        "equity_curve":     equity_curve,
        "by_strategy":      {k: _finalize_stats(v) for k, v in by_strategy.items()},
        "by_hour":          {k: _finalize_stats(v) for k, v in sorted(by_hour.items(), key=lambda x: int(x[0]))},
        "daily_pnl":        daily_rows,
        "ofi_performance":  {k: _finalize_stats(v) for k, v in ofi_perf.items()},
        "thesis_buckets":   {k: _finalize_stats(v) for k, v in thesis_buckets.items()},
        "avg_rr":            avg_rr,
        "overall_win_rate":  overall_win_rate,
        "profitability_zone": profitability_zone,
        "rr_by_week":        rr_by_week,
        "zone_history":      zone_history,
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
        # Atomic write — journal_data.json is read by the journal.html UI
        # and rebuilt fully each EOD; a torn write would break the journal.
        import tempfile as _tempfile, os as _os
        _fd, _tmp = _tempfile.mkstemp(prefix=".tmp_", dir=str(BASE_DIR))
        with _os.fdopen(_fd, "w", encoding="utf-8") as fh:
            json.dump(journal, fh, indent=2)
        _os.replace(_tmp, str(out_path))
        print(
            f"[journal] Wrote {out_path.name} — "
            f"{n_days} days | {n_trades} trades | total P&L ${total_pnl:+.2f}"
        )
    except Exception as e:
        print(f"[journal] ERROR writing {out_path}: {e}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run()
