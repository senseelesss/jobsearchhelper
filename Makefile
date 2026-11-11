.PHONY: install test lint check backfill stats

JOB_RADAR_CACHE_DIR ?= $(if $(XDG_CACHE_HOME),$(XDG_CACHE_HOME),$(HOME)/.cache)/job-radar
JOB_RADAR_VENV ?= $(JOB_RADAR_CACHE_DIR)/venv

install:
	./scripts/setup.sh

test:
	"$(JOB_RADAR_VENV)/bin/python" -m pytest

lint:
	"$(JOB_RADAR_VENV)/bin/python" -m ruff check .

check: test lint

backfill:
	./bin/job-radar backfill --days 10

stats:
	./bin/job-radar stats
