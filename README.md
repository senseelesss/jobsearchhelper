# Job Radar

Vacancy collector for Gmail alerts and direct LinkedIn/karriere.at searches.

Production launch checkpoint (2026-11-05): local collection, two real AI scores,
Telegram acknowledgement and restart replay are verified. Ubuntu hosting and
scheduled operation are **not deployed or production-ready yet**.
Read the [launch audit](docs/PRODUCTION_AUDIT.md) and
[Ubuntu deployment runbook](docs/PRODUCTION_RUNBOOK.md). Use `daily` for production;
the older email-only `run`/`telegram-send` commands below are separate workflows
and must not be scheduled alongside it.

macOS staging now keeps an explicit UNSCORED review tier alongside AI
recommendations, enriches eligible missing JDs within a global request budget,
and reports per-run versus persistent funnel totals. See the
[offline comparison, six searches and transfer checklist](docs/MACOS_STAGING.md).

The first command reads the last N days of Gmail alerts, extracts individual jobs,
removes tracking parameters, deduplicates them by source ID, and stores them in SQLite.

## Architecture update — 2026-11-04

The current implementation includes screening, enrichment, AI scoring, drafts,
Telegram review, and incremental run reports. The next design expands discovery
beyond email while preserving the existing pipeline:

- [Quick MVP audit and verified gaps](docs/MVP_AUDIT.md)
- [Target multi-source architecture and migration plan](docs/ARCHITECTURE.md)
- [MVP V1: independent collection, Excel, application history and daily operation](docs/MVP_V1.md)
- [Implemented features and verified run results](docs/PROGRESS.md)
- [Austria source research and reusable components](docs/SOURCES.md)

The multi-source `daily` command now combines email and saved direct searches,
bounded pagination/details, SQLite provenance, versioned screening, application
history, Excel interchange and optional Telegram delivery. Read the
[current setup and testing guide](docs/OPERATIONS.md). Linux scheduling examples
are provided; a production server and seven-day unattended acceptance remain pending.

## Direct collection pilot: karriere.at

This independent probe defaults to one search page and writes JSON. Add `--pages 2`
and `--details 3` for bounded pagination and full descriptions. It does not write
SQLite, read Gmail, call AI or send Telegram messages; use `daily` for integration.

Recommended local setup (dependencies and browser binaries stay outside Documents):

```bash
./scripts/setup.sh
```

Run from the project directory:

```bash
./bin/job-radar scrape \
  --source karriere --query python --location wien --limit 5 \
  --transport playwright --output outputs/karriere-probe/jobs.json
```

Use `--headed` to display the browser, or `--transport http` to compare ordinary
HTTP retrieval. `--limit` bounds returned records, not total matching vacancies.
`status=success` confirms this page parsed; `coverage_complete=false` means it is
not a complete crawl. Empty results require a recognized zero-result page;
blocking, rate limits and layout failures are reported explicitly and exit nonzero.
Inspect `jobs`, native `source_job_id`, canonical `url`, company, location and
`snippet` in the JSON before integrating a new source into storage.

## Direct collection pilot: LinkedIn

The same command supports LinkedIn's public job search, with no account login or
Gmail input. Browser installation above is shared between both sources:

```bash
./bin/job-radar scrape \
  --source linkedin --query python --location "Vienna, Austria" --limit 5 \
  --transport playwright --output outputs/linkedin-probe/jobs.json
```

`--location` defaults to `Vienna, Austria` for LinkedIn and `wien` for karriere.at.
Without `--output`, each source uses its own `outputs/<source>-probe/jobs.json`.
HTTP comparison is available with `--transport http`.

LinkedIn search cards usually have no description: `snippet` stays empty and
`description_kind=missing`. A source-provided publication date is preserved when
valid. Native IDs and canonical URLs match the existing email parser so future
storage integration deduplicates across acquisition channels. An approximate
count such as `1,000+` is represented by `reported_total=1000` and
`reported_total_is_lower_bound=true`, not as an exact total.

The `scrape` command remains a bounded probe, not a full crawl. No database, AI,
Telegram or application-state operation is performed by `scrape`.
Login walls, challenges, HTTP 999/403 and rate limits are explicit failures;
the probe does not log in or try to work around them. Public availability may
change independently of code. Senior roles in raw output are expected until
screening is connected.

## 1. Setup

```bash
cd job-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp -n candidate_profile.example.yaml data/candidate_profile.yaml
```

In Google Cloud:

1. Create or select a project.
2. Enable the Gmail API.
3. Configure the OAuth consent screen.
4. Create an OAuth client of type **Desktop app**.
5. Download it as `secrets/credentials.json`.

The first run opens a browser for Google authorization and creates
`secrets/token.json`. Both files are ignored by Git.

## 2. Collect The Last 10 Days

```bash
job-radar backfill --days 10
```

Default Gmail query:

```text
newer_than:10d (from:jobalerts-noreply@linkedin.com OR from:donotreply@jobalert.indeed.com)
```

Useful options:

```bash
job-radar backfill --days 10 --dry-run
job-radar backfill --days 10 --limit 20
job-radar backfill --query 'newer_than:3d from:jobalerts-noreply@linkedin.com'
```

## 3. Inspect Results

```bash
job-radar stats
job-radar screen
job-radar enrich --only eligible
job-radar enrich --only review
job-radar enrich --retry-failed
job-radar enrichment-report --only eligible
job-radar score --only eligible
job-radar score --only review
job-radar score --all-active
job-radar score --all-active --retry-failed --limit 3
job-radar score --limit 3 --dry-run
job-radar draft --recommendation strong_apply,apply,review
job-radar draft --retry-failed
job-radar funnel-report
job-radar application-report
job-radar application-report --all-active
job-radar application-report --format json --all-active \
  --output data/application_queue.json
job-radar telegram-preview --limit 3
job-radar telegram-send --limit 3
job-radar telegram-send --all
job-radar telegram-poll
job-radar run --days 2
job-radar run --dry-run
job-radar run --no-telegram
job-radar export --format json --output data/jobs.json
job-radar export --format json --only eligible --output data/eligible-jobs.json
job-radar export --format csv --only review --output data/review-jobs.csv
job-radar export --format csv --only eligible --collapse-semantic \
  --output data/eligible-jobs.csv
job-radar export --format csv --only eligible --collapse-semantic \
  --require-description --output data/eligible-enriched.csv
job-radar export --format csv --output data/jobs.csv
```

SQLite database: `data/jobs.db`.

`job-radar screen` applies the versioned deterministic rules in
`src/job_radar/screening_rules.v3.json`. Every source vacancy remains stored under
its source deduplication key. Cross-source duplicates share a `semantic_key`, while
`screening_status`, structured `rejection_reasons`, and the rules version are stored
on each source record for auditability.

Screening keeps a broad funnel. `eligible` covers explicit early-career roles and
clearly relevant technical work. `review` retains adjacent, incomplete, uncertain,
and non-technical student roles. `rejected` is reserved for hard exclusions:
explicit seniority, requirements of five or more years, clearly irrelevant
professions, unacceptable location, and expired vacancies.

`--collapse-semantic` affects exports only. It emits one canonical row per
`semantic_key`, preferring direct links, then newer records, then longer snippets.
Each collapsed row includes `semantic_duplicate_count` and
`alternate_source_urls`; all original source rows remain in SQLite.

`job-radar enrich` fetches full descriptions for canonical eligible or review
vacancies using normal HTTP requests. Requests use timeouts, per-host rate limits,
retry backoff, and a persistent SQLite cache. Alternate semantic-duplicate URLs are
only attempted after the canonical URL fails. Successful cached descriptions are
not fetched again. Enrichment records HTTP status and final redirect URL. Full
JSON-LD descriptions have `high` confidence, HTML-selector descriptions have
`medium` confidence, and email snippet fallbacks use `snippet_only` with `low`
confidence. `--require-description` includes both full and explicitly marked
snippet-only descriptions.

`job-radar enrichment-report --only eligible` prints one canonical row per
semantic vacancy with source, title, fetch status, fetch error, HTTP status, and
final redirect URL.

Optional HTTP settings:

```text
JOB_RADAR_ENRICH_TIMEOUT=15
JOB_RADAR_ENRICH_RATE_LIMIT=1
JOB_RADAR_ENRICH_RETRIES=2
```

## 4. Candidate Matching

The populated profile is local and ignored by Git. Edit the copy of
`candidate_profile.example.yaml` with your verified experience before using AI.

The versioned candidate profile is stored in
`data/candidate_profile.yaml`. Scoring considers one canonical eligible or review
vacancy per `semantic_key` and excludes expired vacancies. Full descriptions can
produce final scores. Incomplete and `snippet_only` descriptions always produce
provisional scores with low confidence.

Configure OpenAI in `.env` or the shell:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-nano
OPENAI_INPUT_COST_PER_MILLION=0.20
OPENAI_OUTPUT_COST_PER_MILLION=1.25
```

The model and prices are environment settings so they can be updated together.
The CLI loads ignored `.env` and `data/.env` files when present, without overriding
already exported environment variables. API keys are never stored in SQLite,
exports, or tracked configuration.

```bash
job-radar score --only eligible
job-radar score --only review
job-radar score --all-active
job-radar score --all-active --retry-failed --limit 3
job-radar score --limit 3 --dry-run
job-radar draft --recommendation strong_apply,apply,review
job-radar draft --retry-failed
job-radar funnel-report
job-radar application-report
job-radar application-report --all-active
job-radar application-report --format json --all-active \
  --output data/application_queue.json
```

Scoring and application text are separate paid operations. `job_scores` stores the
match score, confidence, recommendation, strengths, missing requirements, risks,
and role summary. `application_drafts` stores cover letters and recruiter messages.
A valid score remains available even if draft generation fails.

Drafts are generated only for `strong_apply`, `apply`, and `review` with a score
of at least 50. The target
cover-letter length is 180-260 words; 160-290 words is accepted. An out-of-range
draft gets one focused rewrite. A second failure is stored as `draft_status=failed`
without repeating the scoring request.

Score cache identity is based on semantic key, candidate profile hash,
description hash, prompt version, and model. Scores and drafts are considered
current only when those inputs still match; historical rows remain stored for
auditability. Token usage, estimated cost, model, timestamps, response IDs, and
errors are stored for both operations. `job-radar funnel-report` summarizes
the complete source-to-application funnel. The all-active JSON report includes
unscored, failed, scored, and drafted canonical vacancies in a stable queue schema.

## 5. Telegram Delivery

Telegram credentials are read only from ignored environment files or exported
environment variables:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Neither value is printed or stored in SQLite. Previewing is local and requires no
credentials:

```bash
job-radar telegram-preview --limit 3
```

Delivery includes active vacancies with successful scores when the recommendation
is `strong_apply` or `apply`, or when it is `review` with a score of at least 50.
The current draft must be ready. Expired vacancies, `skip` recommendations, and
failed, missing, or stale scores/drafts are excluded. Cards are ordered by
recommendation and score.

```bash
job-radar telegram-send --limit 3
job-radar telegram-send --all
job-radar telegram-send --all --force
job-radar telegram-poll
```

Delivered semantic vacancies are not sent again unless `--force` is supplied.
SQLite stores only the semantic key, Telegram message ID, delivery timestamp,
delivery status, and Applied/Skip timestamps. Inline buttons open the vacancy,
send the cover letter as a separate message, or mark the vacancy Applied/Skipped
and edit the original card. Status callbacks are idempotent. The poller persists
the last processed update ID so restarts do not replay callbacks.

## 6. Incremental Pipeline

`job-radar run` executes Gmail ingestion, parsing, source deduplication,
screening, semantic grouping, enrichment, scoring, draft generation, and optional
Telegram delivery:

```bash
job-radar run
job-radar run --days 2
job-radar run --dry-run
job-radar run --no-telegram
```

Source parsing and screening are limited to new or changed source records.
Downstream stages also inspect unchanged active semantic vacancies and retry
missing or failed enrichment, scoring, drafts, and Telegram delivery. Successful
enrichment, score, and draft caches avoid repeated network or paid AI work.
Delivered Telegram cards remain idempotent and are not resent.

Each run is stored in `run_history`; aggregate stage outcomes are recorded in
`run_stage_history`, and per-vacancy statuses/errors in `run_item_status`. A
non-blocking file lock prevents concurrent runs. SQLite uses WAL mode, a
five-second busy timeout, and foreign-key enforcement. Migrations are additive:
source jobs and historical scores, drafts, application statuses, and delivery
records are preserved.

`--dry-run` reads and classifies Gmail input but does not mutate vacancy records,
fetch descriptions, call OpenAI, or send Telegram messages. Scheduling is not
configured.

Indeed alert parsing treats every vacancy link as a job-block delimiter. Normal
vacancies are deduplicated by their `jk` value. Sponsored vacancies without `jk`
use a deterministic content-based ID. Their complete link is retained in storage,
but normal dry-run output replaces it with `[sponsored link stored]`.

## Development

```bash
pytest
ruff check .
job-radar --help
```

Read `AGENTS.md` before asking Codex to modify the project. A good first Codex task is:

> Read AGENTS.md, docs/MVP_AUDIT.md, and docs/ARCHITECTURE.md. Run the existing checks.
> Implement the first reliability milestone with regression tests: per-source and
> group availability, successful-enrichment freshness, and rules-version rescreening.
> Preserve historical scores, drafts, deliveries, and application statuses.
