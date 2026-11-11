# Run the multi-source MVP

This implementation runs locally. Linux service examples are provided, but no
server or scheduler has been installed by this change. Use a test DB until the
live checks below pass on your machine.

For launch evidence and the prepared Ubuntu deployment, use
[PRODUCTION_AUDIT.md](PRODUCTION_AUDIT.md) and
[PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md). The latter supersedes the earlier
deployment outline at the bottom of this page.

For v1.1.0 installation and configuration, start with [README](../README.md).
Daily, application-set and callbacks now share JOB_RADAR_DB by default (data/jobs.db
when unset). Older daily runs defaulted to data/mvp-v1-test.db: explicitly configure
the DB containing your existing state before upgrading. The test commands below
use an explicit isolated DB; callbacks must use that same path. No automatic DB
migration between paths is performed. See [release notes](RELEASE_v1.1.0.md).

## Setup after pulling the update

```bash
./scripts/setup.sh
./bin/job-radar telegram-check
```

The setup script creates a fresh runtime under `~/.cache/job-radar/venv` and browser
binaries under `~/.cache/job-radar/browsers`. The launcher selects this interpreter
explicitly and works without activating a virtualenv, including when an old
`.venv` is still activated. It also keeps Python bytecode outside Documents.
The existing `.venv`, private profile, tokens, `.env`/`data/.env` and DB are preserved.
Override `JOB_RADAR_CACHE_DIR` or `JOB_RADAR_VENV` only when using a custom location.
If a previous shell exported `PLAYWRIGHT_BROWSERS_PATH` into the project folder,
unset it before setup to use the new default browser location.
The populated candidate profile is private too. Fill the local copy with your
verified facts before scoring or drafting; the public example contains no personal
identity, work history or invented skills.

Use `./bin/job-radar` in place of `.venv/bin/python -m job_radar.cli` in the older
examples below. Legacy virtualenv commands still work with a healthy environment.
To check the bot and send an explicit connection test:

```bash
./bin/job-radar telegram-check
./bin/job-radar telegram-check --test-message
```

A traceback interrupted in `importlib.get_data`, OpenAI/Pydantic, or BeautifulSoup
is an import/startup problem, not evidence of a failed Telegram request. Connection
checks no longer import AI code; daily collection prints stage progress before
network work. AI SDK/schema imports occur when scoring or drafting is requested.

## Paste filtered search URLs

```bash
.venv/bin/python -m job_radar.cli search-add \
  'https://www.linkedin.com/jobs/search/?keywords=python&location=Vienna%2C%20Austria&f_E=1%2C2&f_TPR=r604800' \
  --name linkedin-junior-vienna
.venv/bin/python -m job_radar.cli search-add \
  'https://www.karriere.at/jobs/python/wien' --name karriere-python-vienna
```

These commands create `data/searches.yaml`. Alternatively copy and edit
`searches.example.yaml`. Configure up to eight enabled searches. URLs are validated
against supported public HTTPS search hosts/paths; tracking and fragments are
removed, while actual filter values (including repeated parameters) are retained.
Login URLs, detail links, embedded account tokens, userinfo and custom ports are
rejected. Each site's own support for a filter still determines its effect.

Probe any one URL without DB writes:

```bash
.venv/bin/python -m job_radar.cli scrape \
  --url 'https://www.karriere.at/jobs/python/wien' \
  --pages 2 --limit 25 --details 3
```

Limits are independent: pages 1–10, daily jobs 1–500 per search, and detailed pages
0–25 per search. `scrape` retains its smaller 1–100 job limit. A source has an
additional 120-second budget checked between bounded requests. Playwright keeps
the original filters: karriere uses load-more; LinkedIn uses the public frontend's
HTML continuation route after the initial page. This is not an official search
API. HTTP can ignore pagination parameters; repeated native IDs stop the scan
and produce an explicit partial result instead of pretending coverage is complete.

Already collected records survive a later-page failure. Empty, blocked, expired
detail, rate-limited, malformed-layout and transient failures remain distinct.
No complete crawl or vacancy closure is inferred from a bounded scan.

## Test email and direct collection together

Authorize Gmail first with the existing interactive `backfill --dry-run`. If using
the token created in the earlier test, set its path explicitly:

```bash
export JOB_RADAR_TOKEN=secrets/token-test.json
.venv/bin/python -m job_radar.cli daily \
  --searches data/searches.yaml --database data/mvp-v1-test.db \
  --pages 2 --limit 25 --details 3 --email-limit 20
```

`daily` always attempts direct searches independently of email success. `--no-email`
isolates direct collection for diagnosis. It commits each email and each source's
valid results, including partial batches. Errors return a nonzero exit code and
are included in `outputs/daily/report.json`; saved vacancies remain available.

Repeat the same command: identical native IDs create no new source rows; empty
fields cannot erase existing company/location/snippet/email provenance. Application
events and delivery history survive source updates. Counts can legitimately
increase when live sites publish new jobs or change their ranking.

Default output includes an Excel workbook, JSON, a Telegram preview and a checked
SQLite backup. Seven local backups are retained. Copy backups to separate private
storage for host-loss recovery; local backups alone do not provide it. Stop workers
before restoring a backup to the active DB, and test restore on a separate path.

Screening uses full requirements when present and re-evaluates outdated rules.
Expired duplicates do not suppress other active/unknown sources. Successful
description enrichment has a default 24-hour TTL and `enrich --refresh` support.
Transient refresh failure retains last good text and its stale timestamp/error.
Source-native deduplication is exact; cross-site grouping remains conservative
title/company/location matching and is not a guarantee of identifying every repost.

## Optional AI scoring

Application priority is now separate from technical match. See
[APPLICATION_PRIORITY.md](APPLICATION_PRIORITY.md) for the current HIGH/MEDIUM/LOW/
EXCLUDED policy, configurable evidence rules and first-five ranking. Unknown
hours stay in review; explicit full-time-only and incompatible seniority do not
enter paid scoring or actionable notifications. Source data/history remain stored.
Reports partition priorities and hours fits separately; exclusion reasons may
overlap and must not be summed as unique vacancy counts.

The new daily command makes **no paid calls by default**. Explicitly set
`--score-limit 3` to select at most three active eligible unapplied vacancies for scoring, using the existing
candidate profile and model configuration. Draft generation remains separate.
An unavailable model does not erase collected vacancies or prevent an unscored
review queue. Existing price settings must be maintained for cost estimates.
Daily alerts have two tiers for every scoring budget: A contains current successful
apply/strong_apply or review>=50 scores; B contains actionable unscored eligible/
review roles, clearly labelled UNSCORED. Provider unavailability does not hide B.
Failed scores, AI skip results, hard rejects and Applied/Skipped stay excluded.
Unknown hours are separate from technical fit; explicit weekly evidence produces
a distinct 20–25h target assessment. See [macOS staging findings](MACOS_STAGING.md).

Daily defaults to --details 0 and --enrich-limit 5 (global HTTP GET budget), using
the existing cache/rate limits before scoring. Full requirements are re-screened;
missing JDs remain provisional. Set --enrich-limit 0 for an offline replay.
Only missing eligible candidates are selected; no historical bulk refresh occurs.
Requests have configurable input/output/timeout/retry bounds in `.env.example`.
Failed scores can retry after 24h within the daily call budget; use
`--score-retry-failed` only for an explicit bounded retry of failed inputs.

## Connect Telegram

Create a bot with BotFather, open its chat and send `/start`. Configure only your
own intended chat. The existing environment variables are supported:
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

```bash
.venv/bin/python -m job_radar.cli telegram-connect
.venv/bin/python -m job_radar.cli telegram-check
```

Missing values are requested with hidden input. `telegram-connect` verifies access
and saves values in ignored `.env` with mode 0600. Existing shell variables take
precedence; clear stale shell values before configuring a different bot/chat.
Neither command sends by default. `--test-message` explicitly sends a connection
test. Daily delivery requires `daily --telegram`.

The daily digest includes source health, zero-result/partial outcomes and up to
eight review candidates. Applied/skipped history suppresses ordinary review
recommendations. Draft availability is not required. Buttons record confirmed
Applied/Skipped actions without depending on an AI score.

Run the callback worker against the **same database** in a separate terminal:

```bash
JOB_RADAR_DB=data/mvp-v1-test.db .venv/bin/python -m job_radar.cli telegram-poll
```

Delivery has a durable per-chat/per-Vienna-day identity. An acknowledged digest
is not resent on replay. A timeout/crash after starting a send is marked unknown
and not blindly retried; inspect Telegram and the outbox before manual recovery.
Exactly-once delivery across SQLite and Telegram cannot be guaranteed.
Per-chat vacancy identities also suppress previously alerted jobs on later days.
Pending backlog waits for a later day. Definitive HTTP 4xx rejections are safe to
retry after fixing their cause; ambiguous sends require inspection. Per-run JSON
reports and full counters are retained alongside SQLite run history.

## Application states and Excel

`outputs/daily/vacancies.xlsx` is a DB snapshot. Edit only `Зміни статусів`: copy
the native vacancy ID and its revision from `Вакансії`, choose a state and add an
optional note. Then import explicitly on the next daily cycle:

```bash
.venv/bin/python -m job_radar.cli daily --import-xlsx /path/to/reviewed.xlsx
```

The input is archived before the new snapshot is written. The JSON import receipt
lists accepted, replayed and rejected rows. Duplicate IDs, formulas, invalid states
and stale revisions are rejected. Reimporting the same edit creates no second
event. A local laptop file does not synchronize with a server automatically;
transfer it through a private channel before import, or use Telegram buttons.

CLI alternative for an explicit user-confirmed action:

```bash
.venv/bin/python -m job_radar.cli application-set linkedin:1234567890 applied \
  --request-id manual-confirmation-001 --database data/mvp-v1-test.db
```

Use a new request ID for a genuinely new action. A link click, generated letter or
notification never marks an application submitted. No auto-apply is implemented.

## Unattended Linux deployment

`deploy/` contains daily service, timer and callback worker examples. Provision a
persistent host separately, install the package and Chromium as the service user,
and edit paths/user/time before installing units. Proposed layout:

- App `/opt/job-radar`, user `jobradar`.
- Private environment `/etc/job-radar.env` and searches `/etc/job-radar-searches.yaml`.
- Persistent DB/output/tokens `/var/lib/job-radar/`.
- Browsers `/var/cache/job-radar/browsers` (match `PLAYWRIGHT_BROWSERS_PATH`).

Use absolute `JOB_RADAR_CREDENTIALS`, `JOB_RADAR_TOKEN` and candidate-profile paths
in the environment file. Validate Gmail refresh unattended on that host. The timer
example uses 08:00 `Europe/Vienna`, catches up missed runs and bounds the service
to 25 minutes; change this proposed time to your preference. Validate with
`systemd-analyze verify` and `systemd-analyze calendar` on the target Linux host
before enabling the timer and callback service.

External dead-man monitoring, off-host backup storage and deployment credentials
are not configured by these examples. Seven consecutive unattended days,
source-failure recovery, a restore drill and real Telegram delivery still form the
V1 acceptance gate. The macOS development machine cannot validate Linux units.
