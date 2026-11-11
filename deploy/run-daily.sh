#!/bin/sh
# Invoked by systemd; secrets stay in the environment, never argv.
set -eu
umask 077
set -- /opt/job-radar/.venv/bin/python -u -m job_radar.cli daily \
  --searches "${JOB_RADAR_SEARCHES:-/etc/job-radar-searches.yaml}" \
  --database "${JOB_RADAR_DB:-/var/lib/job-radar/jobs.db}" \
  --output "${JOB_RADAR_OUTPUT:-/var/lib/job-radar/output}" \
  --transport "${JOB_RADAR_TRANSPORT:-http}" \
  --pages "${JOB_RADAR_PAGES:-1}" \
  --limit "${JOB_RADAR_SOURCE_LIMIT:-15}" \
  --details "${JOB_RADAR_DETAILS:-0}" \
  --enrich-limit "${JOB_RADAR_ENRICH_LIMIT:-5}" \
  --email-limit "${JOB_RADAR_EMAIL_LIMIT:-100}" \
  --score-limit "${JOB_RADAR_SCORE_LIMIT:-0}"
[ "${JOB_RADAR_NO_EMAIL:-1}" = 0 ] || set -- "$@" --no-email
[ "${JOB_RADAR_SEND_DIGEST:-0}" = 0 ] || set -- "$@" --telegram
exec "$@"
