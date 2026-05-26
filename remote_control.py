"""
remote_control.py — Phase 1 read-only remote control for OpenClaw integration.

A safe, read-only CLI tool that reads files already written by main.py
(dashboard_data.json, data/decisions_*.jsonl, memory/, reports/) and prints
plain-text status reports to stdout. No IBKR connection. No mutating writes.
Designed to be invoked by OpenClaw (or any shell caller) and have its stdout
piped into an LLM.

Usage:
  py -3.11 remote_control.py <command>

Commands: status | summary | last_trade | trades | learning | pnl | help
"""

from __future__ import annotations

import json
import os
import sys
import glob
from datetime import datetime
from typing import Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    import pytz
    ET = pytz.timezone("US/Eastern")
except Exception:
    ET = None


BASE_DIR        = os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader")
DASHBOARD_FILE  = os.path.join(BASE_DIR, "dashboard_data.json")
DATA_DIR        = os.path.join(BASE_DIR, "data")
MEMORY_DIR      = os.path.join(BASE_DIR, "memory")
REPORTS_DIR     = os.path.join(BASE_DIR, "reports")

LINE = "=" * 60


# ── Formatting helpers ─────────────────────────────────────

def fmt_money(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "$0.00"
    sign = "+" if f >= 0 else "-"
    return f"{sign}${abs(f):,.2f}"


def fmt_price(v: Any) -> str:
    try:
        f = float(v)
        if f == 0:
            return "--"
        return f"{f:,.2f}"
    except (TypeError, ValueError):
        return "--"


def fmt_ts_et(iso_or_text: Any) -> str:
    if not iso_or_text:
        return "--"
    s = str(iso_or_text)
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if ET is not None and dt.tzinfo is not None:
                dt = dt.astimezone(ET)
            return dt.strftime("%Y-%m-%d %H:%M:%S ET")
    except Exception:
        pass
    return s


def today_et() -> str:
    if ET is not None:
        return datetime.now(ET).strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def header(title: str) -> None:
    print(LINE)
    print(f"  {title}")
    print(LINE)


def footer() -> None:
    print(LINE)


# ── File loaders ───────────────────────────────────────────

def load_dashboard() -> Optional[dict]:
    if not os.path.exists(DASHBOARD_FILE):
        return None
    try:
        with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"(could not parse dashboard_data.json: {e})")
        return None


def todays_decision_file() -> Optional[str]:
    path = os.path.join(DATA_DIR, f"decisions_{today_et()}.jsonl")
    return path if os.path.exists(path) else None


def load_jsonl(path: str) -> list:
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"(error reading {os.path.basename(path)}: {e})")
    return out


# ── Commands ───────────────────────────────────────────────

def cmd_status() -> None:
    header("DOBOT STATUS")
    d = load_dashboard()
    if not d:
        print("No dashboard_data.json yet — bot has not run.")
        footer()
        return

    version = d.get("botVersion", "?")
    mode    = d.get("data_mode", "?")
    sleeping = d.get("botSleeping", False)
    pos     = d.get("position", "FLAT")
    entry   = d.get("entryPrice")
    stop    = d.get("stopPrice")
    target  = d.get("targetPrice")
    price   = d.get("currentPrice")
    pnl     = d.get("dailyPnl", 0.0)
    kz      = d.get("killzone", "")
    decision = d.get("lastDecision", "") or "--"
    conf    = d.get("lastConfidence", "") or "--"
    prob    = d.get("thesisProbability", 0)
    reason  = (d.get("lastReasoning") or "").strip()
    news_danger = d.get("newsDangerZone", False)
    next_news   = d.get("nextHighImpact") or d.get("nextEventFull")
    wake_time   = d.get("wakeTime", "")

    print(f"Version : {version}  |  {mode}")
    if pos == "FLAT":
        print(f"Position: FLAT")
    else:
        print(f"Position: {pos}  Entry: {fmt_price(entry)}  Stop: {fmt_price(stop)}  Target: {fmt_price(target)}")
    print(f"Price   : {fmt_price(price)}")
    print(f"Daily   : {fmt_money(pnl)}")
    print(f"Session : {kz or '--'}")
    print(f"Decision: {decision}  Conf: {conf}  Prob: {prob}%")
    if reason:
        print(f"Reason  : {reason[:120]}")
    if news_danger:
        print(f"News    : DANGER — {next_news or 'high-impact event imminent'}")
    elif next_news:
        print(f"News    : Clean — next event: {next_news}")
    else:
        print(f"News    : Clean")
    if sleeping and wake_time:
        print(f"Sleeping: bot wakes {wake_time}")
    footer()


def cmd_summary() -> None:
    header("DOBOT SESSION SUMMARY")
    d = load_dashboard()
    if not d:
        print("No dashboard_data.json yet — bot has not run.")
        footer()
        return

    # Reuse status block fields
    print(f"Version  : {d.get('botVersion','?')}  |  {d.get('data_mode','?')}")
    pos = d.get("position", "FLAT")
    if pos == "FLAT":
        print(f"Position : FLAT")
    else:
        print(f"Position : {pos}  Entry: {fmt_price(d.get('entryPrice'))}  "
              f"Stop: {fmt_price(d.get('stopPrice'))}  Target: {fmt_price(d.get('targetPrice'))}")
    print(f"Price    : {fmt_price(d.get('currentPrice'))}")
    print(f"Daily P&L: {fmt_money(d.get('dailyPnl', 0))}")
    print(f"Session  : {d.get('killzone','--') or '--'}")
    print()

    print(f"OR High  : {fmt_price(d.get('orHigh'))}")
    print(f"OR Low   : {fmt_price(d.get('orLow'))}")
    print(f"OR Dir   : {d.get('or_direction') or '--'}")
    print(f"MTF      : {d.get('mtf_alignment') or '--'}")
    print(f"HTF Bias : {d.get('htfBias') or '--'}")
    print(f"VWAP     : {fmt_price(d.get('vwap'))}")
    print(f"Sess Hi  : {fmt_price(d.get('sessionHigh'))}")
    print(f"Sess Lo  : {fmt_price(d.get('sessionLow'))}")
    print(f"Premkt Hi: {fmt_price(d.get('premktHigh'))}")
    print(f"Premkt Lo: {fmt_price(d.get('premktLow'))}")
    confl = d.get("confluence") or []
    print(f"Conflnce : {', '.join(confl) if confl else '--'}")

    trades = d.get("trades") or []
    wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    losses = sum(1 for t in trades if (t.get("pnl") or 0) < 0)
    print(f"Trades   : {len(trades)}  (wins {wins} / losses {losses})")

    acct = d.get("account") or {}
    netliq = acct.get("net_liquidation", d.get("netLiq", 0))
    print(f"NetLiq   : {fmt_money(netliq)}")
    footer()


def _find_last_buysell(records: list) -> Optional[dict]:
    for rec in reversed(records):
        if rec.get("type") == "decision":
            dec = rec.get("decision") or {}
            if dec.get("decision") in ("BUY", "SELL"):
                return rec
    return None


def cmd_last_trade() -> None:
    header("LAST ENTRY DECISION (today)")
    path = todays_decision_file()
    if not path:
        print("No trades recorded today yet.")
        footer()
        return
    records = load_jsonl(path)
    rec = _find_last_buysell(records)
    if not rec:
        print("No trades recorded today yet.")
        footer()
        return

    dec = rec.get("decision") or {}
    side = "LONG" if dec.get("decision") == "BUY" else "SHORT"
    print(f"Direction : {side}")
    print(f"Entry     : {fmt_price(dec.get('entry_price'))}")
    print(f"Stop      : {fmt_price(dec.get('stop_price'))}")
    print(f"Target    : {fmt_price(dec.get('target_price') or dec.get('target_1'))}")
    print(f"Mode      : {dec.get('mode','--')}")
    prob = dec.get("thesis_probability", dec.get("probability", "--"))
    print(f"Thesis %  : {prob}")
    print(f"Confidence: {dec.get('confidence','--')}")
    print(f"Strategy  : {dec.get('strategy','--')}")
    print(f"Pre-filter: {rec.get('pre_filter_reason','--')}")
    print(f"Time ET   : {fmt_ts_et(rec.get('ts_et') or rec.get('ts'))}")
    print(f"Cost USD  : ${float(rec.get('cost_usd', 0) or 0):.4f}")
    print()
    print("Reasoning:")
    print(dec.get("reasoning") or dec.get("rationale") or rec.get("raw_response", "") or "(none)")
    footer()


def cmd_trades() -> None:
    header("COMPLETED TRADES (today)")
    path = todays_decision_file()
    if not path:
        print("No completed trades today.")
        footer()
        return
    records = load_jsonl(path)
    trades = [r for r in records if r.get("type") == "trade"]
    if not trades:
        print("No completed trades today.")
        footer()
        return

    print(f"{'Time':<10}  {'Side':<6}  {'Entry':>10}  {'Exit':>10}  {'P&L':>10}  {'Mode':<8}  Reason")
    print("-" * 60)
    total = 0.0
    wins = 0
    losses = 0
    for t in trades:
        ts = fmt_ts_et(t.get("ts"))[-11:-3] if t.get("ts") else "--"
        side = t.get("action", "--")
        entry = fmt_price(t.get("entry"))
        exit_ = fmt_price(t.get("exit"))
        pnl = float(t.get("pnl") or 0)
        total += pnl
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1
        mode = t.get("mode", "--")
        reason = (t.get("reason") or "")[:30]
        print(f"{ts:<10}  {side:<6}  {entry:>10}  {exit_:>10}  {fmt_money(pnl):>10}  {mode:<8}  {reason}")
    print("-" * 60)
    print(f"Total: {len(trades)} trades  |  Wins: {wins}  Losses: {losses}  |  Net: {fmt_money(total)}")
    footer()


def _most_recent(paths: list) -> Optional[str]:
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        return None
    return max(paths, key=os.path.getmtime)


def cmd_learning() -> None:
    header("LEARNING / EOD REPORTS")
    printed_any = False

    # Most recent learning .md
    md_candidates = []
    for d in (MEMORY_DIR, REPORTS_DIR):
        if os.path.isdir(d):
            md_candidates += glob.glob(os.path.join(d, "learning_*.md"))
    latest_md = _most_recent(md_candidates)
    if latest_md:
        print(f"--- {os.path.basename(latest_md)} ---")
        try:
            with open(latest_md, "r", encoding="utf-8") as f:
                print(f.read())
        except Exception as e:
            print(f"(error reading: {e})")
        printed_any = True
    else:
        # Fallback to most recent session JSONL
        sess_candidates = []
        if os.path.isdir(MEMORY_DIR):
            sess_candidates = glob.glob(os.path.join(MEMORY_DIR, "session_*.jsonl"))
        latest_sess = _most_recent(sess_candidates)
        if latest_sess:
            print(f"--- {os.path.basename(latest_sess)} (summary) ---")
            recs = load_jsonl(latest_sess)
            for r in recs[-5:]:
                trades = r.get("trades")
                wr = r.get("win_rate")
                findings = r.get("key_findings") or r.get("findings")
                bits = []
                if trades is not None: bits.append(f"trades={trades}")
                if wr is not None: bits.append(f"win_rate={wr}")
                print("  " + ("  ".join(bits) if bits else json.dumps(r)[:200]))
                if findings:
                    print(f"  findings: {findings}")
            printed_any = True

    # Most recent ablation summary
    abl_candidates = []
    if os.path.isdir(REPORTS_DIR):
        abl_candidates = glob.glob(os.path.join(REPORTS_DIR, "ablation_*.md"))
    latest_abl = _most_recent(abl_candidates)
    if latest_abl:
        print()
        print(f"--- {os.path.basename(latest_abl)} (Summary section) ---")
        try:
            with open(latest_abl, "r", encoding="utf-8") as f:
                text = f.read()
            in_summary = False
            for line in text.splitlines():
                if line.strip().startswith("## Summary"):
                    in_summary = True
                    print(line)
                    continue
                if in_summary:
                    if line.startswith("## ") and "Summary" not in line:
                        break
                    print(line)
        except Exception as e:
            print(f"(error reading: {e})")
        printed_any = True

    if not printed_any:
        print("No learning reports yet — runs after first EOD session.")
    footer()


def cmd_pnl() -> None:
    header("DAILY P&L SUMMARY")
    d = load_dashboard()
    daily_pnl_dash = float((d or {}).get("dailyPnl", 0) or 0)
    unrealized = float((d or {}).get("unrealized", 0) or 0)
    position = (d or {}).get("position", "FLAT")

    realized = 0.0
    wins = 0
    losses = 0
    best = None
    worst = None
    total_cost = 0.0

    path = todays_decision_file()
    if path:
        records = load_jsonl(path)
        for r in records:
            if r.get("type") == "trade":
                pnl = float(r.get("pnl") or 0)
                realized += pnl
                if pnl > 0: wins += 1
                elif pnl < 0: losses += 1
                best = pnl if best is None else max(best, pnl)
                worst = pnl if worst is None else min(worst, pnl)
            if r.get("type") == "decision":
                total_cost += float(r.get("cost_usd") or 0)

    print(f"Realized P&L    : {fmt_money(realized)}  ({wins}W / {losses}L)")
    print(f"Best trade      : {fmt_money(best) if best is not None else '--'}")
    print(f"Worst trade     : {fmt_money(worst) if worst is not None else '--'}")
    print(f"Unrealized P&L  : {fmt_money(unrealized)}  ({position})")
    print(f"Dashboard daily : {fmt_money(daily_pnl_dash)}")
    print(f"API cost today  : ${total_cost:.4f}")
    print(f"Net after API   : {fmt_money(realized - total_cost)}")
    footer()


def cmd_help() -> None:
    header("REMOTE CONTROL — AVAILABLE COMMANDS")
    cmds = [
        ("status",     "Concise one-block bot status"),
        ("summary",    "Status plus OR, MTF, VWAP, session levels"),
        ("last_trade", "Most recent BUY/SELL entry decision today"),
        ("trades",     "Table of all completed trades today"),
        ("learning",   "Latest learning report and ablation summary"),
        ("pnl",        "Realized, unrealized, API cost, net P&L"),
        ("help",       "This message"),
    ]
    for name, desc in cmds:
        print(f"  {name:<12} {desc}")
    print()
    print("Usage: py -3.11 remote_control.py <command>")
    footer()


COMMANDS = {
    "status":     cmd_status,
    "summary":    cmd_summary,
    "last_trade": cmd_last_trade,
    "trades":     cmd_trades,
    "learning":   cmd_learning,
    "pnl":        cmd_pnl,
    "help":       cmd_help,
}


def main() -> int:
    if len(sys.argv) < 2:
        cmd_help()
        return 0
    cmd = sys.argv[1].strip().lower()
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"Unknown command: {cmd}")
        cmd_help()
        return 1
    try:
        fn()
    except Exception as e:
        print(f"(remote_control error: {e})")
        return 1
    return 0


print("remote_control.py loaded — Phase 1 read-only")

if __name__ == "__main__":
    sys.exit(main())
