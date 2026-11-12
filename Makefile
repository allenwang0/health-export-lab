PYTHON ?= python3.11
PY := .venv/bin/python

.PHONY: setup test lint mutants report demo

setup:
	$(PYTHON) -m venv .venv
	$(PY) -m pip install -q -r requirements-dev.txt

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

mutants:
	$(PY) scripts/mutation_check.py

report:
	$(PY) scripts/report.py

demo:
	./run.sh --demo
