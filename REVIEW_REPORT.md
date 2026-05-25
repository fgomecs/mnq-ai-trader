# Full Code Review — MNQ AI Trader V4.4.0
*Reviewed: 2026-05-25 | Files: 30 | Lines: ~12,000*

---

## Summary

Architecture is solid. V4.4 Phase 1-3 features landed cleanly. Three genuine bugs found, one high-severity that is silently breaking dead zone behavior for your configuration. Docs need updating to reflect V4.4.

---

## BUGS

### BUG 1 — HIGH: FEATURE_DEAD_ZONE flag ignored in can_enter()

**File:** `main.py` line 126–133  
**Impact:** You have `FEATURE_DEAD_ZONE=false` in `.env`, meaning you want unrestricted dead zone trading. But `can_enter()` never checks this flag. Dead zone (11am–1:30pm ET) is still gating all entries at score 0/8, blocking the bot from trading during that window entirely.

**Current code:**
```python
if state == SessionState.DEAD_ZONE:
    if FEATURE_DEAD_ZONE_VWAP_MAGNET and snapshot ...:
        return True, "dead zone VWAP magnet override"
    if confluence_score >= DEAD_ZONE_CONFLUENCE_THRESHOLD:
        return True, ...
    return False, ...   # ← always hits this when score=0
```

**Fix — add at top of DEAD_ZONE branch:**
```python
if state == SessionState.DEAD_ZONE:
    if not FEATURE_DEAD_ZONE:
        return True, ""   # dead zone restriction disabled
```

Also need to import `FEATURE_DEAD_ZONE` in the `can_enter()` scope — it's imported at module level but not referenced in the function.

---

### BUG 2 — MEDIUM: Opening drive uses wrong 5-min bar

**File:** `ibkr_feed.py` line 958  
**Impact:** `FEATURE_OPENING_DRIVE_FADE` is currently False so not live, but when you enable it this will produce wrong results.

**Problem:** `bar = self._bars_5min[0]` gets the **oldest** bar in the 5-min cache (24-hour history), not the 9:30 RTH opening bar. Open/close/wick calculations for opening drive detection are using yesterday's first cached bar.

The `first_candle_5min_high` and `first_candle_5min_low` are computed correctly from 9:30–9:34 1-min bars, but `bar.open`, `bar.close` come from the wrong bar entirely.

**Fix:**
```python
# Replace: bar = self._bars_5min[0]
# With:
today = datetime.now(eastern).date()
bar = next(
    (b for b in self._bars_5min
     if _bar_et(b).date() == today
     and _bar_et(b).hour == 9 and _bar_et(b).minute == 30),
    None
)
if not bar:
    # fall through to defaults
    snapshot["opening_drive_up"] = ...False...
```

---

### BUG 3 — LOW: post_news_window uses fragile string parsing

**File:** `ibkr_feed.py` lines 971–977  
**Impact:** `post_news_window` may silently never fire if `news_calendar.py` changes its `recent_event` string format.

**Current code:**
```python
if recent_event and "(HIGH)" in recent_event:
    m = _re.search(r"(\d+)\s*min ago", recent_event)
```

This is brittle. The `recent_event` field from news_calendar is a formatted string — if it ever changes format, this regex returns None and the window never opens. Low severity since FEATURE_POST_NEWS_REFRESH=false, but worth fixing before enabling.

**Fix:** Have `news_calendar.get_news_snapshot()` return a structured `recent_event_dict` alongside the string, with `minutes_since` and `impact` as explicit fields. Then read those directly.

---

## MINOR ISSUES

### M1 — logger.py hardcoded path

`C:\\trading\\logs` is hardcoded. If BASE_DIR ever changes, logs go to the wrong folder. Config already defines `LOG_DIR = f"{BASE_DIR}\\logs"` — logger.py should import and use it.

**One-liner fix in logger.py:**
```python
import os
_base = os.getenv("BASE_DIR", "C:\\trading\\mnq-ai-trader")
_log_dir = f"{_base}\\logs"
os.makedirs(_log_dir, exist_ok=True)
log_filename = f"{_log_dir}\\trading_{datetime.now().strftime('%Y%m%d')}.log"
```

### M2 — Structure pullback trigger only fires for longs

**File:** `main.py` line 218
```python
if profit_ticks > POS_STRUCTURE_MIN_PROFIT_TICKS and adverse_move >= POS_STRUCTURE_PULLBACK_TICKS and executor.current_position > 0:
```
The `executor.current_position > 0` guard means this event trigger never fires for short positions. Shorts won't get the "giving back profit" early-warning call. Low priority but asymmetric.

### M3 — dashboard_data.json shows maxLoss: 500.0

Stale value from before config.py was updated. Will self-correct on Tuesday boot (C.6 deletes the file on startup). Not a code bug — just stale state.

### M4 — FEATURE_NEWS_GATE not checked in news danger zone block

**File:** `main.py` line 590
```python
if snapshot.get("news_danger_zone", False):
    logger.info(f"NEWS DANGER ZONE — no entries...")
    return
```

This hard block fires regardless of `FEATURE_NEWS_GATE`. You have `FEATURE_NEWS_GATE=false` but the danger zone block in `run_cycle` doesn't check the flag. The pre_filter_signal does check it (`if snapshot.get("news_danger_zone"): return False, "news danger zone"`) but that's the same path. The run_cycle check at line 590 bypasses the pre-filter entirely. If FEATURE_NEWS_GATE=false, this block should not fire.

**Fix:**
```python
if FEATURE_NEWS_GATE and snapshot.get("news_danger_zone", False):
```

---

## ARCHITECTURE REVIEW

### What's working well

**Session classifier integration** — Clean. Fires once at OR_ESTABLISHED, sets module-level state, injects into every Claude prompt. The `from session_classifier import ...` inside `analyze_market()` is a local import — fine for a per-call function, though a module-level import would be cleaner. Not a bug.

**Phase 1 snapshot fields** — All correctly implemented and present in `get_snapshot()`: gap classification, pivot points, first candle levels, VWAP extension, OR extreme fade, opening drive (with Bug 2 caveat), post_news_window. Fields appear in the right order in the snapshot dict.

**Pre-filter V4.4 signals** — Correctly gated behind feature flags. Session type threshold routing (`SESSION_RANGE_SIGNAL_THRESHOLD` when RANGE) is clean. All new signals properly scored before the threshold comparison.

**Prompt injection** — `sctx + dynamic_snapshot` in `analyze_market()` correctly prepends session type context to every entry call. Watchlist update also gets session type. This is exactly right.

**can_enter() refactor** — Signature change to accept `snapshot=None` is clean. VWAP magnet dead zone override is correctly placed before the hard score check.

**EOD timing** — `EOD_SCHEDULE_TIME=16:05` and `SESSION_AFTERNOON_PRIME_END=1555` correctly extends the session. The CLOSING window (3:55–4:05 ET) is exit-only, which is correct behavior for end-of-RTH.

**First candle capture** — Correctly implemented in `_start_realtime_bars()`. Captures 1-min bar at 9:30 ET and derives 5-min equivalent from 9:30–9:34 1-min bars. This is the right approach since 5-min bars from historical fetch may lag.

### Design concern: skip-cache vs session type change

When the session type is classified at 9:45 ET, the skip-cache (A.1) may return a cached HOLD from 9:44 ET (pre-classification) for the next 3 minutes. The `sctx` session type string is part of the dynamic block (uncached), so it *is* included in the call — but the skip-cache returns the old decision entirely without calling Claude. The first post-classification scan may get a stale HOLD. This is low impact (180s max age) but worth knowing.

**Not a bug** — the skip-cache checks for price move, new bar, watchlist refresh. The session type classification at 9:45 doesn't invalidate the cache. If you want immediate re-evaluation after classification, add `_last_entry_call["ts"] = 0` after `set_session_type()` in `run_cycle()`.

### Watchdog.py — good addition

Clean implementation. The `is_main_running()` via wmic and `dashboard_age_secs()` checks are correct. `ALERT_COOLDOWN=300` prevents alert storms. One note: runs in a separate terminal and must be started manually — consider adding it to `start_trading.bat`.

---

## DOCUMENTATION ISSUES

| File | Issue |
|---|---|
| `CLAUDE.md` | Says "last verified V4.3", "$500 daily loss cap", missing session_classifier.py |
| `ROADMAP.md` | V4.4 still in "Planned" — Phase 1-3 is shipped |
| `ROADMAP.md` | EOD timing shows 3:30 PM but it's now 4:05 PM |
| `README.md` | Mostly accurate from our update, but doesn't mention watchdog.py |
| `PROJECT_SUMMARY.md` | Missing session_classifier.py from file reference, V4.4 snapshot fields not listed |

---

## BUG FIX PROMPTS (paste into Claude Code)

**Fix Bug 1 — FEATURE_DEAD_ZONE in can_enter (CRITICAL):**
```
In main.py, in the can_enter() function, find the line:
    if state == SessionState.DEAD_ZONE:
Add this as the first line inside that branch:
    if not FEATURE_DEAD_ZONE:
        return True, ""
Make sure FEATURE_DEAD_ZONE is imported from config at the top of the file (check existing imports — it may already be there).
```

**Fix Bug 4 — FEATURE_NEWS_GATE in run_cycle:**
```
In main.py run_cycle(), find the line:
    if snapshot.get("news_danger_zone", False):
Change it to:
    if FEATURE_NEWS_GATE and snapshot.get("news_danger_zone", False):
FEATURE_NEWS_GATE is already imported from config.
```

**Fix M1 — logger.py hardcoded path:**
```
In logger.py, replace the first 3 lines after the imports:
    os.makedirs("C:\\trading\\logs", exist_ok=True)
    log_filename = f"C:\\trading\\logs\\trading_..."
With:
    _log_dir = os.path.join(os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"), "logs")
    os.makedirs(_log_dir, exist_ok=True)
    log_filename = os.path.join(_log_dir, f"trading_{datetime.now().strftime('%Y%m%d')}.log")
Also fix the two hardcoded paths in log_analysis() and log_trade() the same way.
```

**Fix Bug 2 — opening drive uses wrong bar (fix before enabling FEATURE_OPENING_DRIVE_FADE):**
```
In ibkr_feed.py get_snapshot(), find the block starting:
    if FEATURE_OPENING_DRIVE_FADE and self.first_candle_5min_high and self._bars_5min:
        bar = self._bars_5min[0]
Replace the bar assignment with:
    today = datetime.now(eastern).date()
    bar = next((b for b in self._bars_5min if _bar_et(b).date() == today and _bar_et(b).hour == 9 and _bar_et(b).minute == 30), None)
    if not bar:
        snapshot["opening_drive_up"] = snapshot["opening_drive_down"] = False
        snapshot["opening_drive_fade_short"] = snapshot["opening_drive_fade_long"] = False
    else:
Keep the rest of the block (rng, body, uwk, lwk calculations) unchanged.
```

---

## VERSION STATUS

Current: **V4.4.0** ✓ (config.py, dashboard_data.json both correct)

After Bug 1 and Bug 4 fixes, bump to **V4.4.1**:
```
py -3.11 version_manager.py --bump patch
```

