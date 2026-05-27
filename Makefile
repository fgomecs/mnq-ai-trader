# MNQ AI Trader — test orchestration
# Usage:
#   make test        # full suite
#   make smoke       # imports / config / dashboard
#   make regression  # one test per fixed bug

PY := py -3.11

# Modules to measure coverage against (project files only, not tests).
COVERAGE_TARGETS := claude_brain executor ibkr_feed main config dashboard_writer \
                    data_recorder memory_manager journal_exporter backtester \
                    session_classifier news_calendar logger learning_session \
                    strategy_stats notifier

.PHONY: test smoke regression coverage

test:
	$(PY) -m pytest tests/ -v

smoke:
	$(PY) -m pytest tests/test_smoke.py -v

regression:
	$(PY) -m pytest tests/test_regression.py -v

coverage:
	$(PY) -m pytest tests/ $(addprefix --cov=,$(COVERAGE_TARGETS)) --cov-report=term-missing --cov-report=html
