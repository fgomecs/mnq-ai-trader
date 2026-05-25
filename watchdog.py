"""
watchdog.py — Lightweight bot health monitor.
Runs alongside main.py in a separate terminal.
Sends Pushover alert if bot crashes or dashboard freezes.

Usage:
    py -3.11 watchdog.py
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime
import pytz
from dotenv import load_dotenv

BASE_DIR = Path(os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"))
load_dotenv(BASE_DIR / ".env")

DASHBOARD_FILE  = BASE_DIR / "dashboard_data.json"
CHECK_INTERVAL  = 30    # seconds between checks
STALE_THRESHOLD = 120   # seconds before dashboard is stale
ALERT_COOLDOWN  = 300   # seconds between repeat alerts


def is_main_running() -> bool:
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "commandline", "/format:list"],
            capture_output=True, text=True, timeout=5
        )
        return "main.py" in result.stdout
    except Exception:
        return True  # fail open — don't false-alert if wmic fails


def dashboard_age_secs() -> float:
    try:
        data = json.loads(DASHBOARD_FILE.read_text(encoding="utf-8"))
        if data.get("botSleeping"):
            return 0  # sleeping is normal — not stale
        ts = data.get("timestamp")
        if not ts:
            return 9999
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return (datetime.now(pytz.utc) - dt).total_seconds()
    except Exception:
        return 9999


def send_alert(title: str, message: str) -> None:
    try:
        from notifier import notify
        notify(title, message, priority=1)
        print(f"[watchdog] ALERT sent: {title}")
    except Exception as e:
        print(f"[watchdog] Failed to send alert: {e}")


def main():
    print("[watchdog] Started — monitoring main.py every 30s")
    print(f"[watchdog] Dashboard: {DASHBOARD_FILE}")

    last_alert    = 0
    failures      = 0

    while True:
        time.sleep(CHECK_INTERVAL)
        now = time.time()

        # Check 1 — is main.py process alive?
        if not is_main_running():
            failures += 1
            print(f"[watchdog] main.py not detected (attempt {failures})")
            if failures >= 2:
                if now - last_alert > ALERT_COOLDOWN:
                    send_alert(
                        "BOT CRASHED",
                        "main.py process not found. Bot has stopped."
                    )
                    last_alert = now
                print("[watchdog] Bot gone — watchdog exiting")
                sys.exit(1)
            continue

        failures = 0

        # Check 2 — is dashboard being updated?
        age = dashboard_age_secs()
        if age > STALE_THRESHOLD:
            if now - last_alert > ALERT_COOLDOWN:
                send_alert(
                    "BOT STALLED",
                    f"Dashboard not updated for {int(age)}s — bot may be frozen."
                )
                last_alert = now
            print(f"[watchdog] WARNING — dashboard stale {int(age)}s")
        else:
            print(f"[watchdog] OK — dashboard age {int(age)}s")


if __name__ == "__main__":
    main()
