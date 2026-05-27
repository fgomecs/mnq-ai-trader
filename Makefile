# MNQ AI Trader — test orchestration
# Usage:
#   make test        # full suite
#   make smoke       # imports / config / dashboard
#   make regression  # one test per fixed bug

PY := py -3.11

.PHONY: test smoke regression

test:
	$(PY) -m pytest tests/ -v

smoke:
	$(PY) -m pytest tests/test_smoke.py -v

regression:
	$(PY) -m pytest tests/test_regression.py -v
