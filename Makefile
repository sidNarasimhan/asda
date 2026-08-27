.PHONY: install test api ui worker ingest demo lint

install:
	python3 -m pip install -e ".[dev]"

test:
	python3 -m pytest -q

api:
	python3 -m asda.cli serve

ui:
	python3 -m asda.cli ui

worker:
	python3 -m asda.workers.runner

ingest:
	python3 -m asda.cli ingest csv --path sample_data/leads.csv --limit 50

demo: ingest
	python3 -m asda.cli run --limit 3 --skip-outreach

lint:
	python3 -m ruff check asda tests
