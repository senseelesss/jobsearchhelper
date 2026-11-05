#!/bin/sh
# Invoked by systemd; secret values remain in the environment, not argv.
set -eu
umask 077
exec /opt/job-radar/.venv/bin/python -u -m job_radar.cli daily \
  --searches "${JOB_RADAR_SEARCHES:-/etc/job-radar-searches.yaml}" \
  --database "${JOB_RADAR_DB:-/var/lib/job-radar/jobs.db}" \
  --output "${JOB_RADAR_OUTPUT:-/var/lib/job-radar/output}" \
  --pages "${JOB_RADAR_PAGES:-2}" \
  --limit "${JOB_RADAR_SOURCE_LIMIT:-25}" \
  --details "${JOB_RADAR_DETAILS:-5}" \
  --email-limit "${JOB_RADAR_EMAIL_LIMIT:-100}" \
  --score-limit "${JOB_RADAR_SCORE_LIMIT:-5}" --telegram
