# Job Radar

Job Radar collects Gmail job alerts and public LinkedIn/karriere.at searches,
keeps vacancy/application history in SQLite, and helps prioritize 20–25h/week IT
work in Vienna (15–30h acceptable). **v1.1.0 is the first reproducible application
release; unattended Ubuntu deployment has not been certified or enabled.**

Collection/enrichment v2 adds durable JD recovery, source health, public Absolventen
RSS and optional Greenhouse/Lever employer feeds. See [v2 validation and limits](docs/COLLECTION_V2.md)
and `searches.collection-v2.example.yaml`; private searches are unchanged.

## What works

- Independent email/direct collection; bounded pagination and JD enrichment.
- Native source-ID deduplication and conservative cross-source grouping.
- Evidence-based hours, seniority, role and location eligibility, separately from
  optional cached AI technical matching. HIGH/MEDIUM/LOW/EXCLUDED priorities.
- Tier A scored recommendations and Tier B explicitly UNSCORED human review.
- JSON/Excel reports, application events, Applied/Skipped callbacks, consistent
  SQLite backups, run statistics and source errors.
- Optional daily Telegram digest with persistent cross-day alert identities and
  an immutable per-chat/day outbox. No automatic applications or autonomous agents.

The flow is collectors → normalized source listings/observations → SQLite →
screening/bounded descriptions → deterministic eligibility + optional AI → ranked
review, reports and optional Telegram. SQLite owns state; Excel is an interchange
snapshot. `daily` is the release workflow. Do not schedule legacy `run` or
`telegram-send` alongside it; their notification workflows differ.

## Quick start: macOS

Install Python 3.12 and Git first (`python3.12 --version`). Python >=3.11 is required;
release validation uses Python 3.12. No global Python package install is needed.

```bash
git clone https://github.com/senseelesss/jobsearchhelper.git job-radar
cd job-radar
git switch --detach v1.1.0
JOB_RADAR_PYTHON=python3.12 ./scripts/setup.sh
./bin/job-radar --version
./bin/job-radar --help
```

The tag checkout works after the owner publishes the local release. Setup creates
an environment under `~/.cache/job-radar/venv`, Chromium under
`~/.cache/job-radar/browsers`, a blank private profile at
`data/candidate_profile.yaml`, and six editable searches at `data/searches.yaml`.
It never overwrites existing profiles/searches, credentials, databases or env files.
The launcher selects the correct interpreter and project directory without
activation or a PYTHONPATH export. Old activated `.venv` environments can stay.

Start with direct collection, zero AI calls and no Telegram delivery:

```bash
./bin/job-radar daily --no-email --pages 1 --limit 5 --details 0 \
  --enrich-limit 2 --score-limit 0 --database data/first-test.db \
  --output outputs/first-test
```

This makes bounded public website requests and writes the named test DB/reports.
It is not an offline test. It can return partial results if a source is blocked.
Read `report.json`, `vacancies.json`, `vacancies.xlsx` and `telegram-preview.txt`
in the output directory. No candidate profile is sent to a model at score-limit 0.

## Ubuntu 24.04: manual setup only

Ubuntu's Python 3.12 meets the release requirement. On an approved machine:

```bash
sudo apt-get update
sudo apt-get install -y git python3-venv python3-pip
# Clone and check out v1.1.0 as above, then run from that checkout:
JOB_RADAR_PYTHON=python3 ./scripts/setup.sh --no-browser
JOB_RADAR_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/job-radar"
sudo "$JOB_RADAR_CACHE_DIR/venv/bin/python" -m playwright install-deps chromium
PLAYWRIGHT_BROWSERS_PATH="$JOB_RADAR_CACHE_DIR/browsers" \
  "$JOB_RADAR_CACHE_DIR/venv/bin/python" -m playwright install chromium
./bin/job-radar --version
```

Dependency packages are installed into the venv, never Ubuntu's system Python.
The administrator command installs Chromium OS libraries; the browser itself is
installed as the ordinary user. These commands do not install/enable any service.
Existing systemd examples and the separate [deployment runbook](docs/PRODUCTION_RUNBOOK.md)
require host approval and target-host checks before use. Linux browser access,
unit execution, reboot/catch-up, callbacks and seven-day operation remain unverified.

## Configuration

All integrations are optional for isolated/offline tests. Direct collection needs
only the installed browser and a supported public search URL. Shell variables
win over `.env`, then `data/.env`. Keep real configuration private:

```bash
# Fresh checkout only; preserve any existing env file instead of overwriting it.
[ -f data/.env ] || cp .env.example data/.env
chmod 600 data/.env
```

| Setting/input | Required when | Default / meaning |
| --- | --- | --- |
| `JOB_RADAR_DB` | State persistence | `data/jobs.db`; daily, callbacks and application-set share it |
| `--database PATH` | Isolated run/override | Overrides JOB_RADAR_DB for daily/application-set |
| `--output PATH` | Custom report destination | `outputs/daily`; directory must be writable |
| `--searches PATH` | Custom discovery profiles | `data/searches.yaml`; 1–8 enabled public searches |
| `JOB_RADAR_CANDIDATE_PROFILE` | Review/scoring context | `data/candidate_profile.yaml`; fill verified facts before scoring |
| `JOB_RADAR_CREDENTIALS` | First Gmail authorization | `secrets/credentials.json`, Desktop OAuth client |
| `JOB_RADAR_TOKEN` | Gmail collection | `secrets/token.json`, writable private refresh-token file |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Paid scoring | No scoring unless score-limit >0; model remains configurable |
| `OPENAI_*` token/timeout/retry/price settings | Scoring controls | See `.env.example`; usage cost is an estimate, not an invoice |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Notifications/callbacks | Own intended private bot chat; daily sends only with --telegram |
| `JOB_RADAR_ELIGIBILITY_RULES` | Custom preference policy | Optional absolute path to a copy of packaged eligibility_rules.v1.json |
| `JOB_RADAR_VENV`, `JOB_RADAR_CACHE_DIR` | Custom runtime paths | Used by setup/launcher; must agree between installation and use |

The candidate profile is the verified CV, not a place to invent qualifications.
Optional search_preferences define target/acceptable hours. Residence does not
establish EU work authorization; only add authorization facts you have verified.
Policy thresholds/technology interests can change independently of technical
scores. Details: [application priority](docs/APPLICATION_PRIORITY.md).

Search configuration accepts site-generated filtered URLs through `search-add`
or YAML. The prepared six-profile example spans Python/backend, working student,
automation/integration, QA and data/AI with German/English variants. URL syntax is
validated; current website filter semantics/inventory are not guaranteed.

### Gmail

Enable Gmail API in your Google Cloud project, create a **Desktop app** OAuth
client, and save its JSON at the configured credentials path. Authorize locally:

```bash
./bin/job-radar backfill --days 1 --limit 1 --dry-run
```

This opens Google's consent flow and saves a readonly Gmail token. `daily` never
opens an interactive consent browser: invalid/expired authorization is reported
while direct sources continue. Preserve/re-authorize a revoked token interactively;
see [operations](docs/OPERATIONS.md). Do not upload OAuth files or raw emails.

### AI and Telegram are opt-in

Scoring sends the verified profile and vacancy text to the configured provider.
Missing keys/failed scoring retain unscored review. Failed results are not promoted
into Tier B. Successful caches are keyed by profile, model, prompt and description.
No second paid operation is used for deterministic eligibility/prioritization.

```bash
# Paid operation: run only intentionally, after configuring key/profile/model.
./bin/job-radar daily --score-limit 2
```

Daily scoring limit is 0–25 attempts/run (default 0); prepared deployment example
uses 5. Defaults also bound input bytes, output tokens, timeout and retries.
The example model is configurable and has been marked deprecated in its official
documentation; verify account availability before relying on it. No automatic
model substitution occurs. Never enable SDK debug logging with private payloads.

Create a bot via BotFather, open its private chat and send `/start`. Then:

```bash
./bin/job-radar telegram-connect    # hidden prompts, verifies/saves privately; no send
./bin/job-radar telegram-check      # read-only check; no send
# Real delivery: only when intentionally requested:
./bin/job-radar daily --telegram --score-limit 0
```

`telegram-check --test-message` explicitly sends a connection test. Telegram may
include zero-result/partial summaries. Unknown sends after a timeout/crash are not
blindly retried. An acknowledged digest is not resent that day; score/priority
changes do not resend a previously alerted vacancy. Up to eight items fit; pending
overflow ages into a delivery slot. Inspect ambiguous outbox states before recovery.

Run `telegram-poll` in a separate process with **the same JOB_RADAR_DB** for buttons.
Only one callback worker per bot should run. Excel edits use the existing validated
status import; neither a link click nor a draft marks an application submitted.

## Daily use and upgrades

```bash
./bin/job-radar daily --pages 1 --limit 15 --details 0 --enrich-limit 5 --score-limit 0
./bin/job-radar application-set linkedin:1234567890 skipped --request-id manual-unique-001
```

Gmail and both boards are attempted independently. Each source has bounded requests;
partial data is committed and source errors appear in the report. Exit 0 means the
bounded cycle completed successfully; exit 1 means partial results. Counts distinguish
this run from stored totals. Files remain saved after a later stage fails.

**Upgrade note:** older daily/application-set defaults used data/mvp-v1-test.db
while other commands used JOB_RADAR_DB. v1.1.0 fixes this mismatch. Before updating,
set JOB_RADAR_DB to the authoritative DB you actually used. No files are moved,
merged or deleted automatically. Preserve that DB/outbox/application history when
moving hosts; do not use mock-validation copies as live state.

Stop writers, use SQLite's consistent backup API or `.backup`, and restore only
with services stopped. Copying only the live main file can omit WAL writes.
Seven local backup snapshots are retained; off-host backup and external heartbeat
monitoring are still deployment tasks. Never push private state to GitHub.

## Testing and reproducibility

The package version is defined once in job_radar.__version__; setuptools derives
wheel metadata. `uv.lock` is the dependency resolution; requirements.lock is its
hash-pinned pip export including development, scraping and release-build tools.
Setup installs that exact set, then the local project without dependency resolution
or isolated unpinned build downloads. No uv installation is needed to use setup.
Hashes/versions are reproducible; byte-identical builds and future website/API
behaviour are not promised. Native wheels/browser libraries vary by OS.

```bash
# setup --no-browser is enough for offline fixtures/tests.
JOB_RADAR_CACHE_DIR="${JOB_RADAR_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/job-radar}"
"$JOB_RADAR_CACHE_DIR/venv/bin/python" -m pytest -q
"$JOB_RADAR_CACHE_DIR/venv/bin/ruff" check .
./bin/job-radar --version
```

Tests use fixtures/fake AI/mock Telegram and temporary DBs, including end-to-end
collection, enrichment, eligibility, previews, repeated-run deduplication, callback
state and migrations. They do not require real keys, paid scoring or live sends.
CI is configured for Ubuntu 24.04/Python 3.12 but no service is enabled by CI.

Maintainers regenerate dependency files intentionally using uv 0.12.23:

```bash
uv lock
uv export --locked --all-extras --group release --no-emit-project \
  --no-header --no-annotate --format requirements.txt --output-file requirements.lock
```

## Troubleshooting and limits

- Slow imports from an old Documents `.venv`: use setup/launcher and the external
  cache runtime. No activation/PYTHONPATH export is required.
- Browser executable missing: rerun setup without --no-browser and clear an old
  PLAYWRIGHT_BROWSERS_PATH override, or use the same explicit browser path everywhere.
- Gmail invalid_grant: preserve the stale token and authorize again locally; a
  testing-mode OAuth app may require recurring consent. Daily will not prompt.
- Wrong DB/no application state: inspect explicit --database and JOB_RADAR_DB;
  callbacks and daily must share one persistent local DB.
- Blocked/rate-limited site: inspect source status; absence in a bounded scan never
  proves expiry. No login-wall bypass or automatic browser-account login is used.
- Missing hours/JD: review is provisional. Unknown does not mean confirmed part-time.
  Native IDs dedupe exactly; semantic title/company/location matching can miss
  reposts and location aliases. Rules are evidence heuristics, not contract checks.
- Historical security issue: see [release audit](docs/RELEASE_v1.1.0.md). Rotate
  exposed credentials and resolve history/privacy concerns before publication.
  Removing a value from today's files does not revoke it or erase old commits.

## Documentation

- [v1.1.0 release notes/audit](docs/RELEASE_v1.1.0.md): scope, validation, security and push instructions.
- [Application priority](docs/APPLICATION_PRIORITY.md): policy, examples, evidence limits.
- [Operations](docs/OPERATIONS.md): detailed commands and workbook/outbox recovery.
- [Ubuntu runbook](docs/PRODUCTION_RUNBOOK.md): persistent state and later deployment, approval required.
- [Progress](docs/PROGRESS.md): dated verification history.
- Other MVP/design/source-audit documents are retained historical rationale; they
  are not installation instructions or proof of the current runtime.
