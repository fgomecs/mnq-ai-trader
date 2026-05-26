"""
inject_today_trades.py — one-off utility to append 8 reconstructed trades
for 2026-05-26 into the decisions JSONL so the journal can render them.

Records follow the schema journal_exporter._load_day expects (type="trade"),
plus the commission_source field added with the broker commissionReport
capture work.
"""

import json
from datetime import datetime
from pathlib import Path

import pytz

DATE         = "2026-05-26"
BOT_VERSION  = "4.4.2"
MODE         = "OB_BOUNCE"
COMM_SRC     = "broker"
DATA_PATH    = Path(__file__).parent / "data" / f"decisions_{DATE}.jsonl"

eastern = pytz.timezone("US/Eastern")

# (time_et, action, entry, exit, pnl, commission, exit_reason)
trades = [
    ("13:58", "BUY",  30001.50, 30037.75,  71.26, 1.24, "target"),
    ("14:23", "BUY",  30046.00, 30047.25,   1.26, 1.24, "target"),
    ("14:45", "SELL", 30033.25, 30033.50,  -1.74, 1.24, "stop"),
    ("14:50", "SELL", 30029.25, 30042.00, -26.74, 1.24, "stop"),
    ("15:00", "SELL", 30011.75, 30015.50, -22.98, 1.24, "stop"),
    ("15:07", "BUY",  30040.50, 30051.25,  41.02, 1.24, "target"),
    ("15:51", "BUY",  30087.75, 30092.00,  15.52, 1.24, "target"),
    ("15:52", "BUY",  30098.00, 30087.50, -22.24, 0.62, "stop"),
]


def _ts_utc(time_et: str) -> str:
    hh, mm = time_et.split(":")
    naive  = datetime(2026, 5, 26, int(hh), int(mm), 0)
    local  = eastern.localize(naive)
    return local.astimezone(pytz.UTC).isoformat()


def main() -> None:
    if not DATA_PATH.exists():
        raise SystemExit(f"Target file does not exist: {DATA_PATH}")

    records = []
    for time_et, action, entry, exit_p, pnl, commission, reason in trades:
        records.append({
            "ts":                _ts_utc(time_et),
            "ts_et":             f"{time_et}:00",
            "bot_version":       BOT_VERSION,
            "type":              "trade",
            "date":              DATE,
            "action":            action,
            "mode":              MODE,
            "entry_price":       entry,
            "exit_price":        exit_p,
            "pnl":               pnl,
            "commission":        commission,
            "commission_source": COMM_SRC,
            "exit_reason":       reason,
        })

    with open(DATA_PATH, "a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")

    print(f"Appended {len(records)} trade records to {DATA_PATH.name}")
    for r in records:
        print(f"  {r['ts_et']} {r['action']:<4} entry={r['entry_price']} exit={r['exit_price']} "
              f"pnl={r['pnl']:+.2f} comm={r['commission']} src={r['commission_source']}")


if __name__ == "__main__":
    main()
