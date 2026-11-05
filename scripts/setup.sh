#!/bin/sh
# Install dependencies outside Documents; preserve the old .venv and private data.
set -eu
umask 077
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CACHE_DIR=${JOB_RADAR_CACHE_DIR:-"${XDG_CACHE_HOME:-$HOME/.cache}/job-radar"}
ENV_DIR=${JOB_RADAR_VENV:-"$CACHE_DIR/venv"}
PYTHON_BIN=${JOB_RADAR_PYTHON:-python3}
export PYTHONPYCACHEPREFIX=${PYTHONPYCACHEPREFIX:-"$CACHE_DIR/pycache"}
export PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-"$CACHE_DIR/browsers"}
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required")'
mkdir -p "$CACHE_DIR"
printf 'Creating runtime at %s\n' "$ENV_DIR"
"$PYTHON_BIN" -m venv "$ENV_DIR"
"$ENV_DIR/bin/python" -m pip install --disable-pip-version-check -e "${PROJECT_DIR}[dev,scraping]"
if [ "${1:-}" != "--no-browser" ]; then
    "$ENV_DIR/bin/python" -m playwright install chromium
fi
mkdir -p "$PROJECT_DIR/data"
if [ ! -f "$PROJECT_DIR/data/candidate_profile.yaml" ]; then
    cp "$PROJECT_DIR/candidate_profile.example.yaml" "$PROJECT_DIR/data/candidate_profile.yaml"
fi
printf '\nReady. Run: %s/bin/job-radar telegram-check\n' "$PROJECT_DIR"
