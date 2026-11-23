PYTHON ?= python3.11
PY := .venv/bin/python

.PHONY: setup test lint mutants results bench report demo

setup:
	$(PYTHON) -m venv .venv
	$(PY) -m pip install -q -r requirements-dev.txt

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

mutants:
	$(PY) scripts/mutation_check.py

results:
	$(PY) scripts/report.py
	$(PY) scripts/report.py --seeds 20

bench:
	$(PY) scripts/bench_parse.py

report:
	python3 scripts/build_report.py --card site/card.json --readme README.md

demo:
	./run.sh --demo
