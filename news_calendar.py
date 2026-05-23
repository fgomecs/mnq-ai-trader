"""
News & Economic Calendar for MNQ AI Trader
===========================================

Primary source: FRED (Federal Reserve Bank of St. Louis)
  - Free API key — get one in 30 seconds at https://fred.stlouisfed.org/docs/api/api_key.html
  - Set FRED_API_KEY=yourkey in your .env file
  - Covers: NFP, CPI, PPI, GDP, PCE, FOMC, Retail Sales, Jobless Claims, etc.
  - Never rate-limited, never blocked, official government data

Fallback source: Hardcoded weekly recurring schedule
  - Works with zero network access
  - Covers all high-impact recurring releases by day-of-week + time
  - Automatically active when FRED key is missing or fetch fails

IBKR bulletins: Always active, free, built-in to TWS/Gateway
"""

import os
import json
import urllib.request
from datetime import datetime, date, timedelta
from typing import Optional

import pytz

from logger import logger

eastern = pytz.timezone("US/Eastern")

# ── FRED config ────────────────────────────────────────────
# Free key at: https://fred.stlouisfed.org/docs/api/api_key.html
# Add to .env:  FRED_API_KEY=your32charkey
FRED_API_KEY = os.getenv("FRED_API_KEY", "")

# FRED release IDs for the events that move NQ/MNQ most
# Full list at: https://fred.stlouisfed.org/releases
FRED_RELEASE_IDS = {
    50:  ("NFP / Employment Situation",  "HIGH"),
    46:  ("Consumer Price Index (CPI)",  "HIGH"),
    62:  ("Producer Price Index (PPI)",  "HIGH"),
    18:  ("GDP",                         "HIGH"),
    54:  ("PCE Price Index",             "HIGH"),
    10:  ("Consumer Credit",             "MEDIUM"),
    175: ("Retail Sales",                "HIGH"),
    22:  ("Initial Jobless Claims",      "HIGH"),
    26:  ("Personal Income & Outlays",   "HIGH"),
    23:  ("Durable Goods Orders",        "MEDIUM"),
    # P2.9 — duplicate 175 (Retail Sales) removed
    20:  ("Industrial Production",       "MEDIUM"),
    184: ("Housing Starts",              "MEDIUM"),
    232: ("Existing Home Sales",         "MEDIUM"),
    17:  ("Consumer Confidence (Conf. Board)", "MEDIUM"),
    160: ("JOLTS",                       "HIGH"),
    # FOMC is release 305 but dates come from the Fed calendar separately
}

# FOMC meeting dates 2026 — hardcoded since they're scheduled a year out
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_DATES_2026 = [
    "2026-01-28", "2026-01-29",  # decision on 29th
    "2026-03-18", "2026-03-19",
    "2026-04-29", "2026-04-30",
    "2026-06-10", "2026-06-11",
    "2026-07-29", "2026-07-30",
    "2026-09-16", "2026-09-17",
    "2026-10-28", "2026-10-29",
    "2026-12-16", "2026-12-17",
]
FOMC_DECISION_DATES_2026 = {  # date -> time ET when decision released
    "2026-01-29": "14:00",
    "2026-03-19": "14:00",
    "2026-04-30": "14:00",
    "2026-06-11": "14:00",
    "2026-07-30": "14:00",
    "2026-09-17": "14:00",
    "2026-10-29": "14:00",
    "2026-12-17": "14:00",
}

# ── Hardcoded recurring schedule ───────────────────────────
# Format: (weekday, hour_et, minute_et, title, impact)
# weekday: 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri
# This covers EVERY week — the system checks if today matches
RECURRING_SCHEDULE = [
    # Monday
    (0, 10,  0, "ISM Manufacturing PMI",         "HIGH",   "1st Mon of month"),
    (0, 10,  0, "Factory Orders",                "MEDIUM", "varies"),
    # Tuesday
    (1,  8, 30, "Trade Balance",                 "MEDIUM", "monthly"),
    (1, 10,  0, "ISM Services PMI",              "HIGH",   "3rd Tue of month"),
    (1, 10,  0, "JOLTS Job Openings",            "HIGH",   "monthly"),
    # Wednesday
    (2,  8, 30, "ADP Employment Change",         "HIGH",   "weekly"),
    (2, 10, 30, "EIA Crude Oil Inventories",     "MEDIUM", "weekly"),
    (2, 14,  0, "FOMC Minutes (when scheduled)", "HIGH",   "3 weeks after meeting"),
    # Thursday
    (3,  8, 30, "Initial Jobless Claims",        "HIGH",   "weekly — every Thursday"),
    (3,  8, 30, "Continuing Claims",             "MEDIUM", "weekly — every Thursday"),
    (3, 10,  0, "ISM Manufacturing PMI",         "HIGH",   "alt weeks"),
    # Friday
    (4,  8, 30, "NFP / Employment Situation",    "HIGH",   "1st Fri of month"),
    (4,  8, 30, "Average Hourly Earnings",       "HIGH",   "1st Fri of month"),
    (4,  8, 30, "Unemployment Rate",             "HIGH",   "1st Fri of month"),
    (4,  8, 30, "CPI",                           "HIGH",   "monthly — mid month"),
    (4,  8, 30, "PPI",                           "HIGH",   "monthly"),
    (4,  8, 30, "Retail Sales",                  "HIGH",   "monthly"),
    (4,  9, 15, "Industrial Production",         "MEDIUM", "monthly"),
    (4, 10,  0, "Consumer Sentiment (Michigan)", "MEDIUM", "2nd Fri of month"),
    (4, 10,  0, "Existing Home Sales",           "MEDIUM", "monthly"),
    (4, 10,  0, "New Home Sales",                "MEDIUM", "monthly"),
]

# ── Day-level cache ─────────────────────────────────────────
_cache_date:   Optional[date] = None
_cache_events: list           = []   # WITH time_obj — stripped before JSON export


def _is_cache_fresh() -> bool:
    return _cache_date == datetime.now(eastern).date() and _cache_events is not None


# ── FRED fetch ─────────────────────────────────────────────

def _fred_release_dates(release_id: int, title: str, impact: str,
                        today: date, window_days: int = 7) -> list:
    """Fetch upcoming release dates for one FRED release."""
    if not FRED_API_KEY:
        return []
    start = today.strftime("%Y-%m-%d")
    end   = (today + timedelta(days=window_days)).strftime("%Y-%m-%d")
    url   = (
        f"https://api.stlouisfed.org/fred/release/dates"
        f"?release_id={release_id}"
        f"&realtime_start={start}&realtime_end={end}"
        f"&include_release_dates_with_no_data=true"
        f"&api_key={FRED_API_KEY}&file_type=json"
    )
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "MNQ-AI-Trader/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        dates = data.get("release_dates", [])
        events = []
        for d in dates:
            release_date_str = d.get("date", "")
            if not release_date_str:
                continue
            release_date = date.fromisoformat(release_date_str)
            if release_date != today:
                continue
            # FRED doesn't give us the release time — use known standard times
            release_time = _known_release_time(title)
            dt_et = eastern.localize(
                datetime(release_date.year, release_date.month, release_date.day,
                         release_time[0], release_time[1])
            )
            events.append({
                "time_et":  dt_et.strftime("%H:%M"),
                "time_obj": dt_et,
                "title":    title,
                "impact":   impact,
                "forecast": "",
                "previous": "",
                "actual":   "",
                "country":  "USD",
                "source":   "FRED",
            })
        return events
    except Exception as e:
        logger.debug(f"FRED release {release_id} ({title}): {e}")
        return []


def _known_release_time(title: str) -> tuple:
    """Return (hour, minute) ET for known release titles."""
    title_upper = title.upper()
    # 8:30 AM ET releases
    if any(kw in title_upper for kw in [
        "NFP", "EMPLOYMENT", "CPI", "PPI", "RETAIL", "TRADE BALANCE",
        "JOBLESS", "CLAIMS", "GDP", "PCE", "PERSONAL INCOME", "DURABLE",
        "HOUSING STARTS", "ADP", "JOLTS",
    ]):
        return (8, 30)
    # 9:15 AM ET
    if "INDUSTRIAL" in title_upper:
        return (9, 15)
    # 10:00 AM ET releases
    if any(kw in title_upper for kw in [
        "ISM", "PMI", "CONSUMER CONFIDENCE", "SENTIMENT", "MICHIGAN",
        "EXISTING HOME", "NEW HOME", "FACTORY ORDERS", "JOLTS",
    ]):
        return (10, 0)
    # 10:30 AM
    if "CRUDE" in title_upper or "EIA" in title_upper:
        return (10, 30)
    # 2:00 PM
    if "FOMC" in title_upper or "FEDERAL RESERVE" in title_upper or "BEIGE" in title_upper:
        return (14, 0)
    return (8, 30)   # default


def _fetch_fred_calendar(today: date) -> list:
    """Fetch today's events from FRED across all tracked releases."""
    if not FRED_API_KEY:
        logger.info("FRED_API_KEY not set — using hardcoded schedule only")
        return []

    events = []
    for release_id, (title, impact) in FRED_RELEASE_IDS.items():
        results = _fred_release_dates(release_id, title, impact, today)
        events.extend(results)

    # Add FOMC decisions
    today_str = today.strftime("%Y-%m-%d")
    if today_str in FOMC_DECISION_DATES_2026:
        t    = FOMC_DECISION_DATES_2026[today_str]
        h, m = int(t.split(":")[0]), int(t.split(":")[1])
        dt   = eastern.localize(datetime(today.year, today.month, today.day, h, m))
        events.append({
            "time_et":  dt.strftime("%H:%M"),
            "time_obj": dt,
            "title":    "FOMC Interest Rate Decision + Press Conference",
            "impact":   "HIGH",
            "forecast": "", "previous": "", "actual": "",
            "country":  "USD", "source": "FOMC_SCHEDULE",
        })

    if events:
        logger.info(f"FRED calendar: {len(events)} events for today")
    return events


# ── Hardcoded fallback ─────────────────────────────────────

def _build_hardcoded_events(today: date) -> list:
    """
    Build a best-effort event list from the recurring schedule.
    This only fires on known high-recurrence days (e.g. Thursday = always
    has Jobless Claims). Lower-frequency events (1st Friday NFP) are
    included every matching weekday — Claude will note the uncertainty.
    """
    events = []
    weekday = today.weekday()

    # Always-weekly events (no ambiguity)
    always_weekly = {
        3: [  # Thursday — every week without exception
            ("08:30", "Initial Jobless Claims",   "HIGH"),
            ("08:30", "Continuing Claims",        "MEDIUM"),
        ],
        2: [  # Wednesday — every week
            ("08:30", "ADP Employment Change",    "HIGH"),
            ("10:30", "EIA Crude Oil Inventories","MEDIUM"),
        ],
        4: [  # Friday — check specific dates, but flag as possible
            # NOTE: These are MONTHLY releases, not every Friday.
            # Without FRED key we flag them as possible so Claude is aware.
            # Add FRED_API_KEY to .env for exact dates.
            ("08:30", "Possible: NFP / CPI / PPI / Retail Sales (check calendar)", "HIGH"),
        ],
        0: [  # Monday
            ("10:00", "Possible: ISM Manufacturing PMI (1st Mon)", "HIGH"),
        ],
        1: [  # Tuesday
            ("10:00", "Possible: JOLTS / ISM Services (check calendar)", "HIGH"),
        ],
    }

    if weekday in always_weekly:
        for time_str, title, impact in always_weekly[weekday]:
            h, m  = int(time_str.split(":")[0]), int(time_str.split(":")[1])
            dt    = eastern.localize(datetime(today.year, today.month, today.day, h, m))
            events.append({
                "time_et":  dt.strftime("%H:%M"),
                "time_obj": dt,
                "title":    title + " (recurring — check calendar for confirmation)",
                "impact":   impact,
                "forecast": "", "previous": "", "actual": "",
                "country":  "USD", "source":  "HARDCODED",
            })

    # FOMC decisions (exact dates known)
    today_str = today.strftime("%Y-%m-%d")
    if today_str in FOMC_DECISION_DATES_2026:
        t    = FOMC_DECISION_DATES_2026[today_str]
        h, m = int(t.split(":")[0]), int(t.split(":")[1])
        dt   = eastern.localize(datetime(today.year, today.month, today.day, h, m))
        events.append({
            "time_et":  dt.strftime("%H:%M"),
            "time_obj": dt,
            "title":    "FOMC Interest Rate Decision + Press Conference",
            "impact":   "HIGH",
            "forecast": "", "previous": "", "actual": "",
            "country":  "USD", "source":  "FOMC_SCHEDULE",
        })

    return events


# ── Main fetch ─────────────────────────────────────────────

# ── ForexFactory scraper (free, no API key needed) ────────

def _fetch_forexfactory_today() -> list:
    """
    Fetch today's USD economic events from ForexFactory JSON feed.
    Free, no API key required, always current.
    Falls back silently on any error.
    """
    try:
        import urllib.request as _urllib
        from datetime import datetime as _dt, date as _date
        import pytz as _pytz, json as _json

        _eastern = _pytz.timezone("US/Eastern")
        today    = _date.today()
        today_str = today.strftime("%m-%d-%Y")   # FF format: MM-DD-YYYY

        url     = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, */*",
            "Referer": "https://www.forexfactory.com/",
        }
        req = _urllib.Request(url, headers=headers)
        with _urllib.urlopen(req, timeout=8) as resp:
            raw = _json.loads(resp.read().decode())

        events = []
        for item in raw:
            if item.get("country") != "USD":
                continue
            if item.get("date", "") != today_str:
                continue

            impact_raw = (item.get("impact") or "").lower()
            if impact_raw == "high":
                impact = "HIGH"
            elif impact_raw == "medium":
                impact = "MEDIUM"
            else:
                continue   # skip low-impact

            title    = item.get("title", "Unknown event")
            time_str = (item.get("time") or "").strip()

            try:
                if time_str and time_str.lower() not in ("all day", "tentative", ""):
                    import re as _re
                    t_clean = time_str.lower().replace(" ", "")
                    fmt     = "%I:%M%p" if ":" in t_clean else "%I%p"
                    t_naive = _dt.strptime(t_clean, fmt).replace(
                        year=today.year, month=today.month, day=today.day
                    )
                    t_et = _eastern.localize(t_naive)
                else:
                    t_et = _eastern.localize(_dt.combine(today, _dt.min.time()))
            except Exception:
                t_et = _eastern.localize(_dt.combine(today, _dt.min.time()))

            events.append({
                "title":    title,
                "impact":   impact,
                "time_et":  t_et.strftime("%H:%M"),
                "time_obj": t_et,
                "source":   "ForexFactory",
            })

        return sorted(events, key=lambda e: e["time_obj"])

    except Exception as ex:
        logger.debug(f"ForexFactory fetch failed: {ex}")
        return []



def get_todays_events() -> list:
    """
    Return today's HIGH/MEDIUM USD events sorted by time.
    Uses FRED if API key is set, otherwise hardcoded schedule.
    Result is cached for the full session — called only once at startup.
    """
    global _cache_date, _cache_events

    today_et = datetime.now(eastern).date()

    if _is_cache_fresh():
        return [e for e in _cache_events if e["impact"] in ("HIGH", "MEDIUM")]

    # Try ForexFactory first (free, no key, always current)
    ff_events = _fetch_forexfactory_today()
    if ff_events:
        logger.info(f"ForexFactory calendar: {len(ff_events)} USD events today")
        for e in ff_events:
            logger.info(f"  {e['time_et']} ET | {e['impact']:<6} | {e['title']}")
        _cache_events = ff_events
        _cache_date   = today_et
        return [e for e in ff_events if e["impact"] in ("HIGH", "MEDIUM")]

    # Try FRED next
    fred_events = _fetch_fred_calendar(today_et)

    if fred_events:
        all_events = fred_events
    else:
        # Fall back to hardcoded schedule
        all_events = _build_hardcoded_events(today_et)
        if all_events:
            source_note = "hardcoded recurring schedule"
        else:
            source_note = "no events found"
        logger.info(f"News calendar ({source_note}): {len(all_events)} events today")

    all_events.sort(key=lambda x: x["time_obj"])
    _cache_events = all_events
    _cache_date   = today_et

    return [e for e in _cache_events if e["impact"] in ("HIGH", "MEDIUM")]


# ── Formatting ─────────────────────────────────────────────

def _parse_numeric(s: str) -> Optional[float]:
    try:
        return float(
            s.replace("%","").replace("K","e3").replace("M","e6").replace("B","e9")
        )
    except Exception:
        return None


def format_news_context(events: list, ibkr_bulletins: Optional[list] = None) -> str:
    now_et = datetime.now(eastern)
    lines  = ["ECONOMIC CALENDAR & NEWS:"]

    if not events and not ibkr_bulletins:
        lines.append("  No major USD events scheduled — pure technical session")
        if not FRED_API_KEY:
            lines.append(
                "  ℹ️  Add FRED_API_KEY to .env for full calendar "
                "(free key at fred.stlouisfed.org/docs/api/api_key.html)"
            )
        return "\n".join(lines)

    upcoming = [e for e in events if e["time_obj"] > now_et]
    past     = [e for e in events if e["time_obj"] <= now_et]

    # Next 60 minutes
    next_60 = [e for e in upcoming
               if (e["time_obj"] - now_et).total_seconds() <= 3_600]
    if next_60:
        lines.append("\n  ⚠️  UPCOMING (next 60 min):")
        for e in next_60:
            mins   = int((e["time_obj"] - now_et).total_seconds() / 60)
            icon   = "🔴" if e["impact"] == "HIGH" else "🟡"
            fc_str = f" | Forecast: {e['forecast']}" if e["forecast"] else ""
            pv_str = f" | Prev: {e['previous']}"    if e["previous"] else ""
            ac_str = f" → ACTUAL: {e['actual']}"    if e["actual"]   else ""
            lines.append(
                f"  {icon} {e['time_et']} ET — {e['title']} "
                f"({e['impact']}){fc_str}{pv_str}{ac_str}"
            )
            lines.append(f"       → IN {mins} MINUTES")
            if e["impact"] == "HIGH":
                if mins <= 5:
                    lines.append("       ⛔ DO NOT ENTER — extreme volatility imminent")
                elif mins <= 15:
                    lines.append("       ⚠️  CAUTION — avoid new entries, tighten stops")
                else:
                    lines.append("       📋 Wait for spike + reversal before entering")

    # Just released (within 30 min)
    just_out = [e for e in past
                if (now_et - e["time_obj"]).total_seconds() <= 1_800]
    if just_out:
        lines.append("\n  📢 JUST RELEASED:")
        for e in just_out:
            mins_ago = int((now_et - e["time_obj"]).total_seconds() / 60)
            icon     = "🔴" if e["impact"] == "HIGH" else "🟡"
            ac_str   = f"ACTUAL: {e['actual']}" if e["actual"] else "result pending"
            fc_str   = f" vs Forecast: {e['forecast']}" if e["forecast"] else ""
            lines.append(
                f"  {icon} {e['title']} — {ac_str}{fc_str} ({mins_ago}min ago)"
            )
            if e["actual"] and e["forecast"]:
                a = _parse_numeric(e["actual"])
                f = _parse_numeric(e["forecast"])
                if a is not None and f is not None:
                    if a > f:
                        lines.append("       → BEAT expectations — bullish NQ bias")
                    elif a < f:
                        lines.append("       → MISSED expectations — bearish NQ bias")
                    else:
                        lines.append("       → IN LINE — muted reaction likely")

    # Later today
    later = [e for e in upcoming
             if (e["time_obj"] - now_et).total_seconds() > 3_600]
    if later:
        lines.append("\n  📅 LATER TODAY:")
        for e in later[:6]:
            icon = "🔴" if e["impact"] == "HIGH" else "🟡"
            src  = f" [{e.get('source','').replace('HARDCODED','~est')}]" if e.get('source') == 'HARDCODED' else ""
            lines.append(f"  {icon} {e['time_et']} — {e['title']}{src} ({e['impact']})")

    if ibkr_bulletins:
        lines.append("\n  📰 IBKR BULLETINS:")
        for b in ibkr_bulletins[-3:]:
            lines.append(f"  • {b}")

    if not next_60 and not just_out:
        lines.append("\n  ✅ No major events in next hour — clean technical window")

    return "\n".join(lines)


# ── IBKR bulletins ─────────────────────────────────────────

def get_ibkr_bulletins(ib) -> list:
    try:
        ib.reqNewsBulletins(True)
        ib.sleep(2)
        bulletins = ib.newsBulletins()
        return [b.message[:150] for b in bulletins[-5:]] if bulletins else []
    except Exception as e:
        logger.warning(f"IBKR bulletins failed: {e}")
        return []


# ── Main snapshot ──────────────────────────────────────────

def get_news_snapshot(ib=None) -> dict:
    """Return complete news context dict. Safe to call every 10 min —
    calendar is fetched only once per session at startup."""
    events    = get_todays_events()
    bulletins = get_ibkr_bulletins(ib) if ib else []
    text      = format_news_context(events, bulletins)

    now_et           = datetime.now(eastern)
    danger_zone      = False
    next_high_impact = None
    next_event_full  = None   # any-impact next event details
    next_event_mins  = None   # any-impact minutes-until
    recent_event     = None   # any-impact event that just printed

    # P1.5 — Asymmetric window per impact level.
    # Previous logic was `-5 <= mins <= 15` which only blocked 5 min POST
    # release. HIGH-impact events (NFP, CPI, FOMC) keep whipping markets
    # for 30+ min after. Widen the danger window, asymmetric pre vs post.
    #
    # mins > 0 means event is in the future; mins < 0 means it just happened.
    HIGH_PRE_MIN, HIGH_POST_MIN     = 15, 30   # block 15 min before to 30 min after
    MED_PRE_MIN,  MED_POST_MIN      = 10, 10   # block 10 min each side

    # Reactive windows for "just released" awareness (wider than danger zone)
    HIGH_REACTIVE_MIN = 60   # market still digesting HIGH events for ~1hr
    MED_REACTIVE_MIN  = 30

    for e in events:
        mins = (e["time_obj"] - now_et).total_seconds() / 60

        # Danger-zone gating (hard block)
        if e["impact"] == "HIGH":
            if -HIGH_POST_MIN <= mins <= HIGH_PRE_MIN:
                danger_zone = True
        elif e["impact"] == "MEDIUM":
            if -MED_POST_MIN <= mins <= MED_PRE_MIN:
                danger_zone = True

        # Next HIGH-impact event (existing logic, preserved)
        if mins > 0 and next_high_impact is None and e["impact"] == "HIGH":
            next_high_impact = f"{e['title']} at {e['time_et']} ET"

        # Next event of any impact (NEW — for Claude context)
        if mins > 0 and next_event_full is None:
            next_event_full = f"{e['title']} ({e['impact']}) at {e['time_et']} ET"
            next_event_mins = int(mins)

        # Most recent event that just released (NEW — reactive window)
        if mins < 0:
            reactive_window = HIGH_REACTIVE_MIN if e["impact"] == "HIGH" else MED_REACTIVE_MIN
            if abs(mins) <= reactive_window:
                # Take the most recent one (events are time-sorted, last wins)
                recent_event = f"{e['title']} ({e['impact']}) at {e['time_et']} ET — {int(abs(mins))} min ago"

    # Strip time_obj — not JSON serializable
    events_serializable = [
        {k: v for k, v in e.items() if k != "time_obj"}
        for e in events
    ]

    return {
        "news_text":          text,
        "events_today":       events_serializable,
        "news_danger_zone":   danger_zone,
        "next_high_impact":   next_high_impact,
        "next_event_full":    next_event_full,
        "next_event_minutes": next_event_mins,
        "recent_event":       recent_event,
        "bulletin_count":     len(bulletins),
    }


# ── Startup prefetch ───────────────────────────────────────

def prefetch_calendar() -> None:
    """Call once at startup to warm the cache and log today's schedule."""
    logger.info("Loading economic calendar…")

    # P2.10 — FOMC dates are hardcoded for 2026 only. Warn if we're past that.
    current_year = datetime.now(eastern).year
    fomc_year    = int(list(FOMC_DECISION_DATES_2026.keys())[0][:4]) if FOMC_DECISION_DATES_2026 else current_year
    if current_year != fomc_year:
        logger.warning(
            f"⚠️  FOMC schedule is for {fomc_year}, current year is {current_year}. "
            f"Update FOMC_DATES_{current_year} and FOMC_DECISION_DATES_{current_year} "
            f"in news_calendar.py from https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
        )

    events = get_todays_events()

    if not FRED_API_KEY:
        logger.warning(
            "FRED_API_KEY not set. Calendar using hardcoded recurring schedule. "
            "For full accuracy: get a FREE key at "
            "https://fred.stlouisfed.org/docs/api/api_key.html "
            "then add FRED_API_KEY=yourkey to C:\\trading\\mnq-ai-trader\\.env"
        )

    if events:
        logger.info(f"Today's high-impact events ({len(events)}):")
        for e in events:
            src = f" [{e.get('source','')}]" if e.get('source') != 'FRED' else ""
            logger.info(f"  {e['time_et']} ET | {e['impact']:6} | {e['title']}{src}")
    else:
        logger.info("No high-impact USD events scheduled today")


print("News calendar loaded")
