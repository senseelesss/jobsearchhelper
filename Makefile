.PHONY: install test lint check backfill stats

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check .

check: test lint

backfill:
	.venv/bin/job-radar backfill --days 10

stats:
	.venv/bin/job-radar stats

