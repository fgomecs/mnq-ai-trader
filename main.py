import time
import schedule
from datetime import datetime
import pytz
from config import *
from logger import logger, log_daily_summary
from ibkr_feed import IBKRFeed
from claude_brain import analyze_market, analyze_position, analyze_premarket
from executor import Executor
from memory_manager import (load_recent_memory, save_daily_summary,
                            save_trade_to_memory, load_todays_trades)
from dashboard_writer import update_dashboard

eastern = pytz.timezone('US/Eastern')

ENTRY_ANALYSIS_INTERVAL = 30
POSITION_ANALYSIS_INTERVAL = 10
last_analysis_time = 0
last_position_time = 0
premarket_done = False
analysis_log = []


def is_trading_hours() -> bool:
    now = datetime.now(eastern)
    time_val = now.hour * 100 + now.minute
    return 945 <= time_val <= 1530


def is_premarket_window() -> bool:
    now = datetime.now(eastern)
    time_val = now.hour * 100 + now.minute
    return 930 <= time_val < 945


def is_avoid_hours() -> bool:
    now = datetime.now(eastern)
    time_val = now.hour * 100 + now.minute
    return 1130 <= time_val <= 1330


def run_premarket(feed: IBKRFeed):
    """9:30am — full pre-market analysis with memory"""
    global premarket_done

    if premarket_done:
        return

    logger.info("=" * 50)
    logger.info("PRE-MARKET ANALYSIS STARTING")
    logger.info("=" * 50)

    memory = load_recent_memory(days=5)
    logger.info("Memory loaded — last 5 sessions context ready")

    snapshot = feed.get_snapshot()
    if snapshot:
        result = analyze_premarket(snapshot, memory)
        update_dashboard(
            claude_status="PRE-MARKET ANALYSIS",
            last_decision=result.get('decision'),
            last_reasoning=result.get('reasoning', ''),
            last_confidence=result.get('confidence'),
            amd_phase=snapshot.get('amd_phase', ''),
            session_levels=snapshot.get('session_levels', '')
        )

    premarket_done = True


def run_cycle(feed: IBKRFeed, executor: Executor):
    global last_analysis_time, last_position_time, analysis_log

    now = datetime.now(eastern)
    now_ts = time.time()

    if is_premarket_window():
        run_premarket(feed)
        return

    if not is_trading_hours():
        return

    if executor.daily_loss_remaining <= 0:
        logger.info("Daily loss limit hit. Done for today.")
        update_dashboard(
            claude_status="DAILY LOSS LIMIT HIT",
            daily_pnl=executor.daily_pnl,
            max_loss=MAX_DAILY_LOSS_USD,
            trades=executor.trades_today
        )
        return

    executor.update_position_from_ibkr()

    snapshot = feed.get_snapshot(
        current_position=executor.current_position,
        daily_pnl=executor.daily_pnl,
        daily_loss_remaining=executor.daily_loss_remaining,
        consecutive_losses=executor.consecutive_losses
    )

    if not snapshot:
        logger.error("Empty snapshot — skipping cycle")
        return

    # IN POSITION — position management every 10 seconds
    if executor.current_position != 0:
        if now_ts - last_position_time >= POSITION_ANALYSIS_INTERVAL:
            logger.info(f"--- Position management: {now.strftime('%H:%M:%S')} ET ---")

            result = analyze_position(
                snapshot=snapshot,
                position=executor.current_position,
                entry_price=executor.entry_price,
                stop_price=executor.stop_price,
                target_price=executor.target_price,
                trade_mode=executor.trade_mode
            )

            decision = result.get('decision', 'HOLD')
            new_stop = result.get('new_stop', 'KEEP')

            if decision == "TRAIL" and new_stop != "KEEP":
                try:
                    new_stop_price = float(new_stop)
                    if executor.current_position > 0 and new_stop_price > executor.stop_price:
                        executor.stop_price = new_stop_price
                        logger.info(f"TRAIL: Stop moved to {new_stop_price}")
                    elif executor.current_position < 0 and new_stop_price < executor.stop_price:
                        executor.stop_price = new_stop_price
                        logger.info(f"TRAIL: Stop moved to {new_stop_price}")
                except:
                    pass

            if decision == "CLOSE":
                ticker = feed.ib.reqMktData(feed.contract, '', False, False)
                feed.ib.sleep(0.5)
                price = ticker.last or ticker.close
                executor._close_position(
                    price,
                    result.get('reasoning', 'Claude exit signal')
                )

            # Update dashboard with position status
            update_dashboard(
                position=executor.current_position,
                entry_price=executor.entry_price,
                stop_price=executor.stop_price,
                target_price=executor.target_price,
                daily_pnl=executor.daily_pnl,
                max_loss=MAX_DAILY_LOSS_USD,
                trades=executor.trades_today,
                last_decision=decision,
                last_reasoning=result.get('reasoning', ''),
                last_confidence=result.get('confidence'),
                claude_status=f"MANAGING POSITION — {decision}",
                amd_phase=snapshot.get('amd_phase', ''),
                session_levels=snapshot.get('session_levels', '')
            )

            last_position_time = now_ts

    # NO POSITION — look for entries every 30 seconds
    else:
        if is_avoid_hours():
            logger.info("Midday window — no new entries")
            update_dashboard(
                claude_status="MIDDAY — NO NEW ENTRIES",
                daily_pnl=executor.daily_pnl,
                max_loss=MAX_DAILY_LOSS_USD,
                trades=executor.trades_today,
                amd_phase=snapshot.get('amd_phase', ''),
                session_levels=snapshot.get('session_levels', '')
            )
            return

        if now_ts - last_analysis_time >= ENTRY_ANALYSIS_INTERVAL:
            logger.info(f"--- Entry analysis: {now.strftime('%H:%M:%S')} ET ---")
            decision = analyze_market(snapshot)

            analysis_log.append({
                "time": now.strftime('%H:%M:%S'),
                "decision": decision.get('decision'),
                "reasoning": decision.get('reasoning', ''),
                "mode": decision.get('mode'),
                "confidence": decision.get('confidence')
            })

            executor.execute(decision)

            # Update dashboard after every analysis
            update_dashboard(
                position=executor.current_position,
                entry_price=executor.entry_price,
                stop_price=executor.stop_price,
                target_price=executor.target_price,
                daily_pnl=executor.daily_pnl,
                max_loss=MAX_DAILY_LOSS_USD,
                trades=executor.trades_today,
                last_decision=decision.get('decision'),
                last_reasoning=decision.get('reasoning', ''),
                last_confidence=decision.get('confidence'),
                claude_status=f"SCANNING — last: {decision.get('decision')}",
                amd_phase=snapshot.get('amd_phase', ''),
                session_levels=snapshot.get('session_levels', '')
            )

            last_analysis_time = now_ts


def end_of_day(feed: IBKRFeed, executor: Executor):
    """End of day — close positions, save memory"""
    global premarket_done

    logger.info("=" * 50)
    logger.info("END OF DAY ROUTINE")
    logger.info("=" * 50)

    if executor.current_position != 0:
        logger.info("Closing open position at end of day...")
        ticker = feed.ib.reqMktData(feed.contract, '', False, False)
        feed.ib.sleep(0.5)
        price = ticker.last or ticker.close
        executor._close_position(price, "End of day close")

    save_daily_summary(
        trades=executor.trades_today,
        daily_pnl=executor.daily_pnl,
        analysis_log=analysis_log
    )

    log_daily_summary(executor.trades_today, executor.daily_pnl)

    # Final dashboard update
    update_dashboard(
        position=0,
        daily_pnl=executor.daily_pnl,
        max_loss=MAX_DAILY_LOSS_USD,
        trades=executor.trades_today,
        claude_status="SESSION CLOSED",
        last_decision="HOLD",
        last_reasoning=f"End of day. Final P&L: ${executor.daily_pnl:.2f}. Total trades: {len(executor.trades_today)}. Memory saved."
    )

    logger.info(f"Final P&L: ${executor.daily_pnl:.2f}")
    logger.info(f"Total trades: {len(executor.trades_today)}")
    logger.info("Memory saved — will be used in tomorrow's pre-market analysis")

    premarket_done = False
    analysis_log.clear()


def main():
    global premarket_done

    logger.info("=" * 50)
    logger.info("MNQ AI TRADING SYSTEM — ICT EDITION")
    logger.info(f"Account: ${ACCOUNT_SIZE} | Max Loss: ${MAX_DAILY_LOSS_USD}")
    logger.info(f"Entry analysis: every {ENTRY_ANALYSIS_INTERVAL}s")
    logger.info(f"Position management: every {POSITION_ANALYSIS_INTERVAL}s")
    logger.info(f"Protection loop: every 5s")
    logger.info("=" * 50)

    feed = IBKRFeed()
    if not feed.connect():
        logger.error("Could not connect to IBKR. Is Gateway running?")
        return

    executor = Executor(feed.ib, feed.contract, paper=True)
    logger.info("PAPER TRADING MODE — no real money at risk")
    executor.start_protection_loop()

    # Load memory on startup
    memory = load_recent_memory(days=5)
    if "No previous session" not in memory:
        logger.info("Previous session memory loaded successfully")
    else:
        logger.info("No previous memory — first session")

    # Initial dashboard state
    update_dashboard(
        claude_status="SYSTEM STARTING",
        last_reasoning="Connected to IBKR. Waiting for trading hours.",
        max_loss=MAX_DAILY_LOSS_USD
    )

    schedule.every().day.at("09:30").do(run_premarket, feed=feed)
    schedule.every().day.at("15:30").do(end_of_day, feed=feed, executor=executor)

    logger.info("System ready. Waiting for trading hours...")

    try:
        while True:
            schedule.run_pending()
            run_cycle(feed, executor)
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        end_of_day(feed, executor)
        feed.disconnect()
        logger.info("System shut down cleanly")


if __name__ == "__main__":
    main()
