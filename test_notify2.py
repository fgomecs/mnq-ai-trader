from notifier import *

tests = [
    ("bot_awake",           lambda: notify_bot_awake()),
    ("premarket",           lambda: notify_premarket('Bull bias. Key levels: 21380 / 21290.')),
    ("or_established",      lambda: notify_or_established('BULL', 21380.00, 21325.00)),
    ("trade_entered",       lambda: notify_trade_entered('LONG', 21382.00, 21358.00, 21445.00)),
    ("stop_to_breakeven",   lambda: notify_stop_to_breakeven('LONG', 21382.00)),
    ("trade_exited_win",    lambda: notify_trade_exited('LONG', 21382.00, 21446.00, 96.00, 'TARGET HIT')),
    ("eod_summary",         lambda: notify_eod_summary(290.00, 2, 0, 50290.00, '4.3.0')),
    ("learning_done",       lambda: notify_learning_done('4.3.1', 'MTF aligned entries won 82% today.')),
    ("loss_warning",        lambda: notify_loss_warning(1800.00, 2000.00)),
    ("consecutive_losses",  lambda: notify_consecutive_losses(3, -180.00)),
    ("error",               lambda: notify_error('main.py:694', 'TypeError unexpected keyword argument')),
    ("ibkr_disconnected",   lambda: notify_ibkr_disconnected()),
    ("ibkr_reconnected",    lambda: notify_ibkr_reconnected()),
    ("bot_sleeping",        lambda: notify_bot_sleeping('Tue May 27 8:20 AM ET')),
]

for name, fn in tests:
    result = fn()
    status = "OK" if result else "FAILED"
    print(f"  {status} — {name}")

print("\nDone.")
