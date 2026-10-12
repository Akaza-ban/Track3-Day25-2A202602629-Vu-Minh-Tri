.PHONY: install test baseline gameday compare workflows slo plot grade sli-data lint clean

PY ?= python

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest -q

baseline:            ## the naive client on all 6 incident tapes
	$(PY) scripts/run_gameday.py --client naive

gameday:             ## your ResilientClient on all 6 incident tapes
	$(PY) scripts/run_gameday.py --client resilient

compare:             ## naive vs yours, side by side
	$(PY) scripts/run_gameday.py --client both

workflows:           ## 500 refund workflows with crash injection
	$(PY) scripts/run_workflows.py --executor both

slo:                 ## burn-rate alerting on 30 days of SLI data
	$(PY) scripts/run_slo.py --alerter both

plot:                ## figure for the postmortem (needs matplotlib)
	$(PY) scripts/plot_timeseries.py --scenario G2_overload_storm

grade:               ## automatic score (90 pts) -> reports/scoreboard.json
	$(PY) scripts/grade.py

sli-data:
	$(PY) scripts/make_sli_data.py

lint:
	ruff check src tests scripts

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache reports/gameday reports/*.json reports/*.png
