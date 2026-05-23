"""
MNQ AI Trading System — ICT Edition (v2)
==========================================
Session 2 changes from audit:
  P1.3 — _position_entry_time global removed; executor owns entry_timestamp.
         No more sync race when IBKR is slow to propagate a fresh fill.
  P2.8 — end_of_day() now calls reset_session_state() so tomorrow's
         pre-market doesn't see yesterday's watchlist / consecutive_holds.
  P3.1 — Magic numbers (adverse move, stop proximity, etc.) now live in
         config.py and are env-overridable.
"""

import threading
import time
from datetime import datetime
from enum import Enum
from typing import Optional

import pytz
import schedule

from config import (
    TICK_SIZE, MAX_DAILY_LOSS_USD, ACCOUNT_SIZE,
    POS_ADVERSE_MOVE_TICKS, POS_STOP_PROXIMITY_TICKS,
    POS_TARGET_PROXIMITY_TICKS, POS_GIVEBACK_PEAK_TICKS,
    POS_GIVEBACK_AMOUNT_TICKS,
    MIN_HOLD_SCALP, MIN_HOLD_SWING, MIN_HOLD_DEFAULT,
    EMERGENCY_STOP_DIST_TICKS,
    ENTRY_SCAN_INTERVAL_SECS, POS_INTERVAL_NORMAL_SECS,
    POS_INTERVAL_ALERT_SECS, WATCHLIST_REFRESH_SECS,
)

from logger import logger, log_daily_summary

def _flush_log() -> None:
    """C.4 — Force-flush log handlers after critical events (entry, exit, errors).
    Ensures logs are written to disk even if the process crashes immediately after."""
    try:
        for h in logger.handlers:
            h.flush()
    except Exception:
        pass
from news_calendar import prefetch_calendar
from ibkr_feed import IBKRFeed
from claude_brain import (
    analyze_market, analyze_position, analyze_premarket,
    update_watchlist, get_watchlist, pre_filter_signal,
    update_session_context, reset_session_state,
)
from executor import Executor
from memory_manager import (
    load_recent_memory, save_daily_summary,
    save_trade_to_memory, load_todays_trades,
    generate_morning_review,
)
from dashboard_writer import update_dashboard, update_price_only
from data_recorder import recorder as _recorder

eastern = pytz.timezone("US/Eastern")


# ─── Session State Machine ────────────────────────────────

class SessionState(Enum):
    PRE_SESSION     = "pre_session"
    PRE_MARKET      = "pre_market"
    OR_FORMING      = "or_forming"
    OR_ESTABLISHED  = "or_established"
    PRIME_WINDOW    = "prime_window"
    DEAD_ZONE       = "dead_zone"
    AFTERNOON_PRIME = "afternoon_prime"
    CLOSING         = "closing"
    AFTER_HOURS     = "after_hours"


def get_session_state(now_et: datetime) -> SessionState:
    t = now_et.hour * 100 + now_et.minute
    if t < 830:    return SessionState.PRE_SESSION
    if t < 930:    return SessionState.PRE_MARKET
    if t < 935:    return SessionState.OR_FORMING
    if t < 1000:   return SessionState.OR_ESTABLISHED
    if t < 1100:   return SessionState.PRIME_WINDOW
    if t < 1330:   return SessionState.DEAD_ZONE
    if t < 1530:   return SessionState.AFTERNOON_PRIME
    if t < 1600:   return SessionState.CLOSING
    return SessionState.AFTER_HOURS


def can_enter(state: SessionState, confluence_score: int = 0) -> tuple[bool, str]:
    """Return (allowed, reason). Dead zone requires score 8+."""
    if state in (SessionState.OR_ESTABLISHED, SessionState.PRIME_WINDOW,
                 SessionState.AFTERNOON_PRIME):
        return True, ""
    if state == SessionState.DEAD_ZONE:
        if confluence_score >= 8:
            return True, "dead zone override — score 8+"
        return False, f"dead zone (score {confluence_score}/8 needed)"
    if state == SessionState.CLOSING:
        return False, "closing — exit only"
    return False, f"no entries in {state.value}"


# ─── Module-level state ────────────────────────────────────

last_analysis_time    = 0.0
last_position_time    = 0.0
last_watchlist_time   = 0.0
premarket_done        = False
analysis_log: list    = []

# Event-driven position tracking
_last_position_price  = 0.0
_last_position_delta  = 0
_peak_profit_ticks    = 0.0
_last_swing_high      = 0.0
_last_swing_low       = 999_999.0
# P1.3 — _position_entry_time global removed; use executor.entry_timestamp

# Fast ticker
_fast_ticker_running  = False
_last_snapshot_lock   = threading.Lock()
_last_snapshot: dict  = {}


# ─── Event-driven position trigger ────────────────────────

def _should_call_claude_now(executor: Executor, snapshot: dict) -> tuple[bool, str]:
    global _last_position_price, _last_position_delta, _peak_profit_ticks

    if executor.current_position == 0:
        return False, ""

    price  = snapshot.get("last_price", 0)
    delta  = snapshot.get("cumulative_delta", 0)
    entry  = executor.entry_price
    stop   = executor.stop_price
    target = executor.target_price

    if not price or not entry:
        return False, ""

    if executor.current_position > 0:
        profit_ticks  = (price - entry)  / TICK_SIZE
        ticks_to_stop = (price - stop)   / TICK_SIZE
    else:
        profit_ticks  = (entry - price)  / TICK_SIZE
        ticks_to_stop = (stop - price)   / TICK_SIZE

    ticks_to_target = abs((target - price) / TICK_SIZE) if target else 999

    if profit_ticks > _peak_profit_ticks:
        _peak_profit_ticks = profit_ticks

    price_move   = price - _last_position_price if _last_position_price else 0
    adverse_move = (-price_move if executor.current_position > 0 else price_move)

    if _last_position_price and adverse_move >= POS_ADVERSE_MOVE_TICKS:
        _last_position_price = price
        return True, f"ADVERSE MOVE: {adverse_move:.0f}t against position"

    if _last_position_delta:
        if executor.current_position > 0 and delta < 0 and _last_position_delta >= 0:
            _last_position_delta = delta
            return True, "DELTA FLIP: turned negative on long"
        if executor.current_position < 0 and delta > 0 and _last_position_delta <= 0:
            _last_position_delta = delta
            return True, "DELTA FLIP: turned positive on short"

    if 0 < ticks_to_stop <= POS_STOP_PROXIMITY_TICKS:
        return True, f"STOP PROXIMITY: {ticks_to_stop:.0f}t from stop"
    if ticks_to_target <= POS_TARGET_PROXIMITY_TICKS:
        return True, f"TARGET PROXIMITY: {ticks_to_target:.0f}t from target"

    giveback = _peak_profit_ticks - profit_ticks
    if _peak_profit_ticks >= POS_GIVEBACK_PEAK_TICKS and giveback >= POS_GIVEBACK_AMOUNT_TICKS:
        return True, f"GIVEBACK: was +{_peak_profit_ticks:.0f}t, now +{profit_ticks:.0f}t"

    if profit_ticks > 20 and adverse_move >= 5 and executor.current_position > 0:
        return True, f"STRUCTURE: +{profit_ticks:.0f}t profit but pulling back {adverse_move:.0f}t"

    _last_position_price = price
    _last_position_delta = delta
    return False, ""


def _reset_position_tracking() -> None:
    global _last_position_price, _last_position_delta, _peak_profit_ticks
    global _last_swing_high, _last_swing_low
    _last_position_price = 0.0
    _last_position_delta = 0
    _peak_profit_ticks   = 0.0
    _last_swing_high     = 0.0
    _last_swing_low      = 999_999.0


# ─── Fast dashboard ticker (1 Hz) ─────────────────────────

def _fast_dashboard_ticker(feed: IBKRFeed, executor: Executor) -> None:
    global _fast_ticker_running
    ticker_ref: list = [None]

    def _get_ticker():
        if ticker_ref[0] is None:
            try:
                ticker_ref[0] = feed.ib.reqMktData(feed.contract, "", False, False)
            except Exception:
                pass
        return ticker_ref[0]

    while _fast_ticker_running:
        try:
            ticker = _get_ticker()
            price = bid = ask = vol = 0.0

            def _clean(v):
                """Convert NaN/None/negative to 0.0. ib_insync returns NaN
                for delayed-mode bid/ask which is truthy, so `or 0.0` doesn't
                catch it. This does."""
                try:
                    f = float(v) if v is not None else 0.0
                    return f if f == f and f > 0 else 0.0   # f == f is NaN check
                except (TypeError, ValueError):
                    return 0.0

            if ticker:
                price = _clean(ticker.last) or _clean(ticker.close) or _clean(ticker.bid) or _clean(ticker.ask)
                bid   = _clean(ticker.bid)
                ask   = _clean(ticker.ask)
                vol   = _clean(ticker.volume) or _clean(getattr(ticker, "avVolume", 0))

            # Fallback chain when ticker hasn't populated yet (delayed data
            # mode has a 1-3s warmup before fields appear). Use last close
            # from feed's bar cache so dashboard shows SOMETHING immediately.
            if price <= 0 and executor._last_price > 0:
                price = executor._last_price
            if price <= 0:
                try:
                    price = feed._get_last_price()
                except Exception:
                    pass
            # Last resort: pull from the most recent 1-min bar
            if price <= 0 and feed._bars_1min:
                price = feed._bars_1min[-1].close or 0.0

            # If bid/ask still empty (common in delayed mode), synthesize a
            # tight spread around price so the dashboard shows SOMETHING.
            # Mark approximate by using exact price = bid = ask.
            if bid <= 0 and price > 0:
                bid = price
            if ask <= 0 and price > 0:
                ask = price

            if price > 0:
                executor.update_price(price)

            if int(time.time()) % 5 == 0:
                account_data = feed.get_account_data()
                with _last_snapshot_lock:
                    _last_snapshot["account_data"] = account_data
            else:
                with _last_snapshot_lock:
                    account_data = _last_snapshot.get("account_data", {})

            update_price_only(
                price=price, bid=bid, ask=ask, volume=vol,
                position=executor.current_position,
                entry_price=executor.entry_price,
                stop_price=executor.stop_price,
                target_price=executor.target_price,
                daily_pnl=executor.daily_pnl,
                account=account_data,
            )

            # Every 10s — patch dashboard with live OR + position data
            if int(time.time()) % 10 == 0:
                try:
                    _patch_dashboard_live(feed, executor, price, account_data)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Ticker error: {e}")
        time.sleep(1)


# ─── Helpers ───────────────────────────────────────────────

def cancel_all_orders(ib) -> None:
    try:
        open_trades = ib.openTrades()
        if not open_trades:
            logger.info("No open orders to cancel")
            return
        logger.info(f"Cancelling {len(open_trades)} order(s)…")
        for trade in open_trades:
            ib.cancelOrder(trade.order)
            ib.sleep(0.2)
        ib.sleep(1)
        logger.info("All orders cancelled")
    except Exception as e:
        logger.error(f"Cancel orders error: {e}")


def _apply_structure_stop(decision: dict, executor: Executor) -> dict:
    """
    Wire Claude's STOP_PRICE into executor's stop.
    Also compute stop_ticks for legacy executor.execute() compatibility.

    Note: P1.7 in claude_brain.parse_decision now demotes BUY/SELL → HOLD
    when stop_price <= 0, so we won't see a bogus stop_ticks calc here
    anymore. The previous code computed stop_ticks=118632 when stop_price=0,
    which only got caught by downstream sanity checks. That path is dead.
    """
    stop_price = decision.get("stop_price", 0.0)
    if stop_price and stop_price > 0:
        decision["stop_ticks"] = int(abs(
            (stop_price - (executor._last_price or stop_price)) / TICK_SIZE
        ))
    return decision


# ─── Pre-market ────────────────────────────────────────────

def run_premarket(feed: IBKRFeed) -> None:
    global premarket_done
    if premarket_done:
        return

    logger.info("=" * 50)
    logger.info("PRE-MARKET ANALYSIS")
    logger.info("=" * 50)

    memory       = load_recent_memory(days=5)
    snapshot     = feed.get_snapshot()
    account_data = feed.get_account_data()

    if snapshot:
        # C.3 — Build initial watchlist before pre-market analysis so Claude
        # has a game plan to reference (OR not yet set, so watchlist will be
        # NEUTRAL bias until OR forms at 9:30)
        try:
            update_watchlist(snapshot)
        except Exception as e:
            logger.warning(f"Pre-market watchlist build failed: {e}")

        result = analyze_premarket(snapshot, memory)
        update_dashboard(
            claude_status  = "PRE-MARKET ANALYSIS",
            last_decision  = result.get("decision"),
            last_reasoning = result.get("reasoning", ""),
            last_confidence= result.get("confidence"),
            amd_phase      = snapshot.get("amd_phase", ""),
            session_levels = snapshot.get("session_levels", ""),
            current_price  = snapshot.get("last_price", 0),
            account        = account_data,
            snapshot       = snapshot,
        )

    premarket_done = True


# ─── Main cycle ────────────────────────────────────────────

def run_cycle(feed: IBKRFeed, executor: Executor) -> None:
    global last_analysis_time, last_position_time, last_watchlist_time, analysis_log

    now    = datetime.now(eastern)
    now_ts = time.time()
    state  = get_session_state(now)

    # ── Pre-market (8:30-9:30) ────────────────────────────
    if state == SessionState.PRE_MARKET:
        run_premarket(feed)
        return

    # ── Session gates ─────────────────────────────────────
    if state in (SessionState.PRE_SESSION, SessionState.OR_FORMING, SessionState.AFTER_HOURS):
        if int(now_ts) % 60 < 2:
            logger.info(f"Session gate: {state.value} ({now.strftime('%H:%M')} ET)")
        return

    # ── Daily loss check ──────────────────────────────────
    if executor.daily_loss_remaining <= 0:
        logger.info("Daily loss limit hit — done for today.")
        update_dashboard(
            claude_status="DAILY LOSS LIMIT HIT",
            daily_pnl=executor.daily_pnl,
            max_loss=MAX_DAILY_LOSS_USD,
            trades=executor.trades_today,
        )
        return

    executor.update_position_from_ibkr()
    executor.check_pending_close()

    # ── Closing — exits only ──────────────────────────────
    if state == SessionState.CLOSING and executor.current_position == 0:
        return

    # ── Fast snapshot (< 1s with cached bars) ─────────────
    snapshot = feed.get_snapshot(
        current_position      = executor.current_position,
        daily_pnl             = executor.daily_pnl,
        daily_loss_remaining  = executor.daily_loss_remaining,
        consecutive_losses    = executor.consecutive_losses,
    )
    if not snapshot:
        logger.error("Empty snapshot — skipping cycle")
        return

    current_price = snapshot.get("last_price", 0)
    executor.update_price(current_price)
    account_data = feed.get_account_data()

    # ── Watchlist refresh (every 5 min) ───────────────────
    if now_ts - last_watchlist_time >= WATCHLIST_REFRESH_SECS:
        try:
            update_watchlist(snapshot)
            last_watchlist_time = now_ts
        except Exception as e:
            logger.warning(f"Watchlist refresh failed: {e}")

    # ── IN POSITION ───────────────────────────────────────
    if executor.current_position != 0:
        event_trigger, event_reason = _should_call_claude_now(executor, snapshot)
        interval = POS_INTERVAL_ALERT_SECS if event_trigger else POS_INTERVAL_NORMAL_SECS

        if now_ts - last_position_time >= interval:
            if event_trigger:
                logger.info(f"--- Position [EVENT: {event_reason}] ---")
            else:
                logger.info(f"--- Position check: {now.strftime('%H:%M:%S')} ET ---")

            result   = analyze_position(
                snapshot     = snapshot,
                position     = executor.current_position,
                entry_price  = executor.entry_price,
                stop_price   = executor.stop_price,
                target_price = executor.target_price,
                trade_mode   = executor.trade_mode,
            )
            decision = result.get("decision", "HOLD")
            new_stop  = result.get("new_stop", "KEEP")

            # P1.3 — Use executor.entry_timestamp instead of module global.
            # Set on actual fill inside _enter_trade(), so no IBKR sync race.
            if decision == "CLOSE" and executor.entry_timestamp > 0:
                hold_secs = now_ts - executor.entry_timestamp
                mode = executor.trade_mode or "SCALP"
                min_hold = MIN_HOLD_SWING if "SWING" in mode.upper() else MIN_HOLD_SCALP
                if hold_secs < min_hold:
                    ticks_to_stop = abs(
                        (snapshot.get("last_price", 0) - executor.stop_price) / TICK_SIZE
                    ) if executor.stop_price else 999
                    is_emergency = (
                        ticks_to_stop <= EMERGENCY_STOP_DIST_TICKS or
                        event_trigger
                    )
                    if not is_emergency:
                        logger.info(
                            f"Hold time gate: Claude wanted CLOSE after {hold_secs:.0f}s "
                            f"(min {min_hold}s) — overriding to HOLD. "
                            f"Reason: {result.get('reasoning','')[:80]}"
                        )
                        decision = "HOLD"
                        result["decision"] = "HOLD"

            if decision == "TRAIL" and new_stop != "KEEP":
                try:
                    nsp = float(new_stop)
                    if executor.current_position > 0 and nsp > executor.stop_price:
                        executor.stop_price       = nsp
                        executor._claude_trail_stop = nsp   # D.2
                        logger.info(f"TRAIL: stop → {nsp}")
                    elif executor.current_position < 0 and nsp < executor.stop_price:
                        executor.stop_price       = nsp
                        executor._claude_trail_stop = nsp   # D.2
                        logger.info(f"TRAIL: stop → {nsp}")
                except (ValueError, TypeError):
                    pass

            if decision == "CLOSE":
                price = executor._get_market_price()
                executor._close_position(price, result.get("reasoning", "Claude exit"))
                _flush_log()   # C.4

            update_dashboard(
                position           = executor.current_position,
                entry_price        = executor.entry_price,
                stop_price         = executor.stop_price,
                target_price       = executor.target_price,
                current_price      = current_price,
                daily_pnl          = executor.daily_pnl,
                max_loss           = MAX_DAILY_LOSS_USD,
                trades             = executor.trades_today,
                last_decision      = decision,
                last_reasoning     = result.get("reasoning", ""),
                last_confidence    = result.get("confidence"),
                last_thesis_status = result.get("thesis_status", ""),
                claude_status      = f"MANAGING — {decision}",
                amd_phase          = snapshot.get("amd_phase", ""),
                session_levels     = snapshot.get("session_levels", ""),
                account            = account_data,
                snapshot           = snapshot,
            )
            last_position_time = now_ts
        return

    # ── NO POSITION ───────────────────────────────────────

    # Helper — writes full snapshot to dashboard for visibility during
    # gated cycles. Without this, HTF/ICT/STRUCTURE panels go empty whenever
    # the bot doesn't get to the entry-decision write at the bottom of the
    # function (i.e. during dead zone, pre-filter rejects, news blocks).
    def _refresh_dashboard_with_snapshot(status_label: str) -> None:
        update_dashboard(
            position        = executor.current_position,
            entry_price     = executor.entry_price,
            stop_price      = executor.stop_price,
            target_price    = executor.target_price,
            current_price   = current_price,
            daily_pnl       = executor.daily_pnl,
            max_loss        = MAX_DAILY_LOSS_USD,
            trades          = executor.trades_today,
            claude_status   = status_label,
            amd_phase       = snapshot.get("amd_phase", ""),
            session_levels  = snapshot.get("session_levels", ""),
            account         = account_data,
            snapshot        = snapshot,
        )

    # News hard block
    if snapshot.get("news_danger_zone", False):
        logger.info(f"NEWS DANGER ZONE — no entries | {snapshot.get('next_high_impact','')}")
        _refresh_dashboard_with_snapshot("BLOCKED — news danger zone")
        return

    # Only scan at configured interval
    if now_ts - last_analysis_time < ENTRY_SCAN_INTERVAL_SECS:
        return

    last_analysis_time = now_ts

    # ── Pre-filter ────────────────────────────────────────
    worth_calling, filter_reason = pre_filter_signal(snapshot)
    if not worth_calling:
        if int(now_ts) % 30 < ENTRY_SCAN_INTERVAL_SECS:
            logger.info(f"Pre-filter: SKIP — {filter_reason}")
            # Refresh dashboard periodically during pre-filter rejects too
            _refresh_dashboard_with_snapshot(f"WAITING — {filter_reason[:40]}")
        return

    logger.info(f"--- Entry scan: {now.strftime('%H:%M:%S')} ET [{filter_reason}] ---")

    # ── Session state check ───────────────────────────────
    allowed, state_reason = can_enter(state)
    if not allowed:
        logger.info(f"Session gate: {state_reason}")
        _refresh_dashboard_with_snapshot(f"GATED — {state_reason[:40]}")
        return

    # Tag snapshot with pre-filter reason so backtest recorder captures it
    snapshot["_pre_filter_reason"] = filter_reason

    # ── Claude entry decision ─────────────────────────────
    decision = analyze_market(snapshot)
    dec_str  = decision.get("decision", "HOLD")

    if dec_str in ("BUY", "SELL"):
        decision = _apply_structure_stop(decision, executor)

        # Dead zone requires score 8+
        if state == SessionState.DEAD_ZONE:
            score = decision.get("confluence_score", 0)
            allowed, state_reason = can_enter(state, score)
            if not allowed:
                logger.info(f"Dead zone gate: {state_reason} — forcing HOLD")
                decision["decision"] = "HOLD"
                dec_str = "HOLD"

    analysis_log.append({
        "time":       now.strftime("%H:%M:%S"),
        "decision":   dec_str,
        "reasoning":  decision.get("reasoning", ""),
        "mode":       decision.get("mode"),
        "confidence": decision.get("confidence"),
        "state":      state.value,
        "filter":     filter_reason,
    })

    executor.execute(decision)

    # C.4 — Flush log after any BUY/SELL/CLOSE so entry/exit is on disk
    # immediately in case of crash
    if dec_str in ("BUY", "SELL", "CLOSE"):
        _flush_log()

    update_dashboard(
        position        = executor.current_position,
        entry_price     = executor.entry_price,
        stop_price      = executor.stop_price,
        target_price    = executor.target_price,
        current_price   = current_price,
        daily_pnl       = executor.daily_pnl,
        max_loss        = MAX_DAILY_LOSS_USD,
        trades          = executor.trades_today,
        last_decision   = dec_str,
        last_reasoning  = decision.get("reasoning", ""),
        last_confidence = decision.get("confidence"),
        last_strategy   = decision.get("strategy", ""),
        last_confluence = decision.get("confluence", ""),
        last_confluence_score = decision.get("confluence_score", 0),
        claude_status   = f"SCANNING — last: {dec_str}",
        amd_phase       = snapshot.get("amd_phase", ""),
        session_levels  = snapshot.get("session_levels", ""),
        account         = account_data,
        snapshot        = snapshot,
    )


# ─── End-of-day ────────────────────────────────────────────

def end_of_day(feed: IBKRFeed, executor: Executor) -> None:
    global premarket_done

    logger.info("=" * 50)
    logger.info("END OF DAY ROUTINE")
    logger.info("=" * 50)

    cancel_all_orders(feed.ib)

    if executor.current_position != 0:
        logger.info("Closing open position at EOD…")
        price = executor._get_market_price()
        executor._close_position(price, "End of day close")

    save_daily_summary(
        trades       = executor.trades_today,
        daily_pnl    = executor.daily_pnl,
        analysis_log = analysis_log,
    )
    log_daily_summary(executor.trades_today, executor.daily_pnl)

    account_data = feed.get_account_data()
    update_dashboard(
        position       = 0,
        daily_pnl      = executor.daily_pnl,
        max_loss       = MAX_DAILY_LOSS_USD,
        trades         = executor.trades_today,
        claude_status  = "SESSION CLOSED",
        last_decision  = "HOLD",
        last_reasoning = f"EOD. P&L: ${executor.daily_pnl:.2f}. Trades: {len(executor.trades_today)}.",
        account        = account_data,
    )

    logger.info(f"Final P&L: ${executor.daily_pnl:.2f}  Trades: {len(executor.trades_today)}")

    # P2.8 — wipe per-session brain state so tomorrow doesn't inherit
    # yesterday's consecutive_holds, watchlist, etc.
    reset_session_state()

    # Flush recorder files cleanly at EOD
    _recorder.flush_and_close()

    premarket_done = False
    analysis_log.clear()


# ─── Live dashboard patch (runs every 10s from fast ticker) ─

def _patch_dashboard_live(feed: IBKRFeed, executor: Executor, price: float, account: dict) -> None:
    """
    Write OR direction, session levels, P&L, and position to dashboard
    every 10 seconds regardless of whether Claude fired.
    """
    from dashboard_writer import update_dashboard

    import datetime as _dt, pytz as _pytz
    now_et = _dt.datetime.now(_pytz.timezone("US/Eastern"))
    s = {
        "or_high":            feed.or_high,
        "or_low":             feed.or_low,
        "or_direction":       feed.or_direction,
        "or_broken_up":       feed.or_broken_up,
        "or_broken_down":     feed.or_broken_down,
        "or_break_attempts":  feed.or_break_count,
        "or_relative_volume": feed.or_relative_volume,
        "or_pullback_low":    feed.or_pullback_low,
        "or_entry_zone_active": feed.or_entry_zone_active,
        "session_high":       max((b.high for b in feed._bars_1min[-60:]), default=0) if feed._bars_1min else 0,
        "session_low":        min((b.low  for b in feed._bars_1min[-60:]), default=0) if feed._bars_1min else 0,
        "vwap":               0,
        "cumulative_delta":   feed.tick_delta,
        "amd_phase":          feed._determine_amd_phase(now_et),
        "killzone":           feed._get_killzone(now_et),
        "news_text":          feed._news_cache.get("news_text", ""),
        "news_danger_zone":   feed._news_cache.get("news_danger_zone", False),
    }

    state  = get_session_state(now_et)

    if executor.current_position != 0:
        claude_status = f"MANAGING — {state.value}"
    elif state in (SessionState.PRIME_WINDOW, SessionState.OR_ESTABLISHED, SessionState.AFTERNOON_PRIME):
        claude_status = "SCANNING"
    else:
        claude_status = f"WAITING — {state.value}"

    update_dashboard(
        position        = executor.current_position,
        entry_price     = executor.entry_price,
        stop_price      = executor.stop_price,
        target_price    = executor.target_price,
        current_price   = price,
        daily_pnl       = executor.daily_pnl,
        max_loss        = MAX_DAILY_LOSS_USD,
        trades          = executor.trades_today,
        claude_status   = claude_status,
        account         = account,
        snapshot        = s,
    )


# ─── Main ──────────────────────────────────────────────────

def main() -> None:
    global premarket_done, _fast_ticker_running

    logger.info("=" * 50)
    logger.info("MNQ AI TRADING SYSTEM — ICT EDITION v2")
    logger.info(f"Account: ${ACCOUNT_SIZE:,} | Max Loss: ${MAX_DAILY_LOSS_USD:,}")
    logger.info(f"Entry scan: {ENTRY_SCAN_INTERVAL_SECS}s (pre-filter active)")
    logger.info(f"Position management: event-driven ({POS_INTERVAL_ALERT_SECS}s alert / {POS_INTERVAL_NORMAL_SECS}s normal)")
    logger.info("=" * 50)

    # C.6 — Clear stale dashboard state from previous session.
    # The dashboard_writer merge logic preserves fields across writes (so the
    # fast ticker doesn't wipe reasoning). On a fresh boot, that means stale
    # EOD reasoning from the previous run sticks around until Claude fires.
    # Solution: delete the JSON file so the first write is clean.
    import os as _os
    from config import DASHBOARD_FILE as _DASH_FILE
    try:
        if _os.path.exists(_DASH_FILE):
            _os.remove(_DASH_FILE)
            logger.info("Cleared stale dashboard state from previous session")
    except Exception as e:
        logger.warning(f"Could not clear dashboard state: {e}")

    feed = IBKRFeed()
    if not feed.connect():
        logger.error("Could not connect to IBKR. Is Gateway running?")
        return

    logger.info("Initializing bar cache — fetching historical data once…")
    feed.initialize_bars()
    logger.info("Bar cache ready — snapshot assembly will now be < 1 second")

    # B.2 — Restore tick state from disk if same trading day
    feed.restore_tick_state()

    executor = Executor(feed.ib, feed.contract, paper=True)
    logger.info("PAPER TRADING MODE — no real money at risk")
    executor.start_protection_loop()

    # Fast dashboard ticker (1 Hz)
    _fast_ticker_running = True
    ticker_thread = threading.Thread(
        target=_fast_dashboard_ticker,
        args=(feed, executor),
        daemon=True,
        name="DashboardTicker",
    )
    ticker_thread.start()
    logger.info("Fast dashboard ticker started (1 Hz)")

    memory = load_recent_memory(days=5)
    logger.info(
        "Previous session memory loaded"
        if "No previous session" not in memory
        else "No previous memory — first session"
    )

    # Economic calendar
    prefetch_calendar()

    # Build initial watchlist if market is already open
    now_et = datetime.now(eastern)
    state  = get_session_state(now_et)
    if state not in (SessionState.PRE_SESSION, SessionState.PRE_MARKET, SessionState.OR_FORMING):
        logger.info("Market open — building initial watchlist…")
        try:
            snap = feed.get_snapshot()
            if snap:
                update_watchlist(snap)
        except Exception as e:
            logger.warning(f"Initial watchlist failed: {e}")

    account_data = feed.get_account_data()
    from news_calendar import get_news_snapshot as _get_news
    _news = _get_news()

    update_dashboard(
        claude_status  = "SYSTEM READY",
        last_reasoning = f"Connected. Live data active. Bar cache initialized. State: {state.value}",
        max_loss       = MAX_DAILY_LOSS_USD,
        account        = account_data,
        snapshot       = _news,
    )

    schedule.every().day.at("15:30").do(end_of_day, feed=feed, executor=executor)
    logger.info(f"System ready. Session state: {state.value}")

    try:
        while True:
            schedule.run_pending()
            run_cycle(feed, executor)
            time.sleep(0.5)

    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        _fast_ticker_running = False
        end_of_day(feed, executor)
        cancel_all_orders(feed.ib)
        feed.disconnect()
        logger.info("System shut down cleanly")


if __name__ == "__main__":
    main()
