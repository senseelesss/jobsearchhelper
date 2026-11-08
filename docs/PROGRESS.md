# Implementation and verification log

As of 2026-11-08. This file records observed results separately from the target
architecture. No claim of full unattended V1 completion is made.

## Documentation checkpoint / full project audit — 2026-11-08

Baseline 1c06c67 was clean; version 1.1.0 and its existing local tag are preserved.
The [full project audit](PROJECT_AUDIT.md) records current subsystem evidence,
historical live checks, security gates, limitations and an incremental roadmap.
No application functionality changed. A fresh public-file copy passed 254 tests,
Ruff, CLI help/version, configuration and temporary SQLite integrity checks.
Additional offline reproductions confirmed cached provisional scores blocking
later daily JD acquisition, early/detailed location disagreement and incomplete
enrichment not necessarily marking the overall run partial. These are documented
follow-up tasks, not fixed here. No paid calls, live collection, Telegram send,
authoritative DB changes, push, new tag, history rewrite or deployment occurred.

## Local release preparation v1.1.0 — 2026-11-08

Continues from 5e6ab28. One authoritative version, CLI --version, hash-pinned
runtime/dev/scraping/build dependencies and fresh-profile/search setup are prepared.
Daily and application-set now honor JOB_RADAR_DB unless --database is explicit,
matching callbacks. Set the actual authoritative DB path before upgrading; no
existing DB is moved, merged or modified by release validation.

Fresh public-file copy and new Python 3.12.14 venv: setup --no-browser, pip check,
source/installed CLI, wheel metadata/resources, 254 tests and Ruff all passed.
The full test run blocked socket connections/DNS; temporary SQLite and fake
providers cover ingestion, eligibility/priority, previews, replay and persistence.
Offline Chromium parsed two fixture jobs with network routes blocked. Lock/export
consistency, shell syntax, ignore rules and diff whitespace also passed.

No private state deleted, paid calls, real Telegram sends, push, history rewrite
or deployment. Existing unrelated nested repository is preserved and excluded
locally. Historical credentials/private profile exist on old master/recovery refs,
outside release ancestry. The exposed OpenAI key matches the configured key:
rotation is required; historical bot revocation and author-email privacy review
remain publication gates. No credential values are printed or committed here.
See [release audit and limitations](RELEASE_v1.1.0.md). Ubuntu CI/setup are prepared;
target-host browser/service/reboot and unattended acceptance remain unverified.

## Filtering accuracy / application priority — 2026-11-05

Continues from b9a7eb3. Deterministic eligibility now derives hours, seniority,
location/role fit, evidence and application priority separately from stored AI
scores. No new DB columns or paid stage. Hours target 20–25, acceptable 15–30;
required >=4 professional years excluded, preferred experience stays review.
Priority-based A/B delivery retains existing identities/outbox and reserves one
aged pending slot; scoring reserves FIFO backlog capacity without bulk aging
overriding newly found target-hours student roles. Workbook/report expose top five.

Saved 34-job comparison: rule pool 22→15; A/B queue 20→13; AI candidates 19→12;
HIGH=0 / MEDIUM=9 / LOW=6 / EXCLUDED=19. Hours target=1, unknown=30, mismatch=3.
Mock delivery=8, remaining queue=5; all 34 records retained. Existing scores and
application events preserved; authoritative DB/profile unchanged. No paid calls,
real messages, deployment or push. The two stored low AI results are review/45
and review/42; earlier staging wording calling them skip was corrected.

251 tests and Ruff pass. See [policy, root causes, examples and limits](APPLICATION_PRIORITY.md).

## macOS staging optimization — 2026-11-05

Continues from 7e8ee5cc. Current daily review has Tier A AI recommendations and
Tier B explicitly UNSCORED actionable review, independent of scoring budget or
provider availability. Failed/skip/hard-rejected/applied/skipped rows stay excluded.
Bounded daily enrichment reuses existing caches and re-screens new full JDs.
Scoring prefers actionable/part-time/full-JD candidates with backlog fairness;
cache hits and retry cooldowns do not consume paid call slots. Existing schema,
callbacks, notification identities and immutable outbox are preserved.

Saved JD coverage: 4 full/34 source records; email-backed 0/24, direct-only 4/10.
Offline old/new comparison on separate copies, fake scores and mock Telegram:
budget 2 review queue 2→20 (A=2/B=18); budget 5 5→20 (A=5/B=15);
missing provider 0→20; budget 0 22→20 because two cached review scores below 50 stay outside Tier A.
The original DB tables were unchanged. No new paid calls, real messages or remote
deployment occurred. Details, runtime/cost estimates, scopes and transfer checklist
are in [MACOS_STAGING.md](MACOS_STAGING.md). Six source URLs pass local validation;
their current website filter semantics were not live-tested.
Validation: **195 tests passed**, including a blank-profile publishable copy;
Ruff, diff whitespace and shell syntax checks passed.

## Production launch checkpoint — 2026-11-05

Minimal hardening adds cross-day vacancy alert identities, isolated score/config
failures, noninteractive Gmail, bounded AI input/output/timeout/retries, safe
provider errors, separate hours evidence, 24h failed-score cooldown and complete
run statistics. **174 tests passed** and Ruff passed. No AI agents added.

Live isolated checks: both boards returned 5 jobs; Gmail recovered using the
existing alternate token (private default updated). All-source run: 37 observations,
34 unique records, 19 scoring-eligible. Process replay: 0 new, 0 changed,
37 unchanged. Two explicitly approved scores succeeded for $0.00120790 total,
0 AI-shortlisted; the separate rule-only run had 22 review candidates. One daily
Telegram summary was acknowledged, later runs did not send a second message.
Restore copy: integrity ok, 34 records, outbox preserved; Applied/Skipped persisted
after reopen with two idempotent events, without changing actual application state.

See [detailed verified findings and limits](PRODUCTION_AUDIT.md) and the
[Ubuntu runbook](PRODUCTION_RUNBOOK.md). Configuration is prepared only; no remote
host was modified, no timer enabled, and production readiness is not claimed.

## Startup recovery — 2026-11-05

The user's interrupted tracebacks stopped while importing OpenAI/Pydantic and
BeautifulSoup from the old Documents virtualenv, before the corresponding HTTP
requests. They do not establish a Telegram or source-network failure. The exact
cause of the slow local file reads has not been established.

Telegram connection checks and daily collection now avoid eager AI SDK/schema
imports. The CLI prints startup and collection-stage progress. `scripts/setup.sh`
installs a separate persistent runtime, bytecode and Chromium under
`~/.cache/job-radar`; `bin/job-radar` selects it without shell activation or a
manual PYTHONPATH. Existing private files and the old virtualenv are preserved.

Verified with the new runtime:

- **157 tests passed**, including fresh-process guards against AI imports during
  connection checks and daily module startup; Ruff passed.
- `./bin/job-radar telegram-check`: bot and chat verified, no message sent.
- Playwright `daily`, no email, one page/job per source, no details/AI/Telegram:
  karriere.at and LinkedIn both succeeded; two source rows inserted, one eligible
  and one rejected. Excel, JSON report and SQLite backup were produced under
  ignored `outputs/startup-fix-check/`.

Real Telegram delivery and unattended deployment acceptance remain pending.

## Checkpoint 1 — existing email path verified

User-operated live commands:

```text
backfill --days 10 --limit 5
Fetched emails: 5
Unique jobs: 24
Inserted: 24; updated: 0

screen
Screened: 24; eligible: 11; review: 5; rejected: 8

stats
Total: 24
indeed: 16
linkedin: 8

run --days 10 --dry-run --no-telegram
Run 1 success: emails=103; new=114; changed=0; screened=0; semantic=0;
enriched=0; scored=0; score_cache_hits=0; drafts=0; draft_cache_hits=0;
telegram=0; failed=0; estimated_cost_usd=0.000000
```

Confirmed: Gmail authorization/read access, both existing email layouts, source
extraction, SQLite writes and screening on the 24 saved jobs, plus the full dry-run
ingestion/classification path. `new=114` is a dry-run insertion estimate for the
database used in that command; it does not mean those rows were saved. These logs
do not establish that both invocations used the same database/environment.

Additional local replay check: copy `data/test-run.db` using SQLite backup, rebuild
the 24 existing `Job` inputs, and upsert into the disposable copy. Result:
`inserted=0`, `changed=0`, `unchanged=24`, database remains 24 rows. The original
test database is unchanged. This verifies source-key idempotence for saved inputs,
not every possible email layout or cross-source semantic match.

Earlier checks on a separate existing database: 114 source rows, integrity check
`ok`; local screening, queue generation, JSON/CSV export and three Telegram-card
previews succeeded. Read-only Telegram `getMe`/`getChat` succeeded. These checks
did not send a new Telegram message or make a paid AI call.

Existing runtime includes HTTP description enrichment, AI scoring/drafting and
Telegram sending/callback code with automated tests. Their existence is distinct
from a fresh live end-to-end run after reauthorization.

## Checkpoint 2 — first direct source probe implemented

Implemented `scrape --source karriere`, independent of Gmail and SQLite:

- Source-neutral `SourceVacancy` and bounded `CollectionBatch` records.
- Deterministic karriere.at first-page card parser with native numeric IDs,
  canonical URLs, title/company/location/snippet and UTC observation time.
- Optional Playwright Chromium transport and existing HTTP transport.
- JSON output with schema version, acquisition transport, counts and outcome.
- Distinct empty, blocked, rate-limited, partial and failed outcomes. Unknown
  layouts fail rather than pretending no vacancies exist. Partial/failure outcomes
  exit nonzero, retaining valid records and diagnostics in JSON.
- No invented email metadata, database writes, LLM calls or notifications.
- Browser cleanup on navigation failure, navigation timeouts and fixture tests.

Bounded live smoke checks for `python` / `wien`, `limit=5`:

| Transport | HTTP | Cards seen | Jobs returned | Invalid cards | Result |
| --- | --- | --- | --- | --- | --- |
| Playwright Chromium | 200 | 15 | 5 | 0 | success |
| HTTP | 200 | 15 | 5 | 0 | success |

The same five native IDs were returned by both routes. Each sample had five unique
source keys, with no missing titles, companies or locations. This proves bounded
retrieval and extraction at this moment, not complete site coverage or future
selector stability. `coverage_complete=false` is deliberate: only page one is
read, and output is limited. Search snippets remain snippets, not full job text.

Verification after implementation: **105 tests passed**, Ruff passed, and
`git diff --check` passed. Browser binaries and probe results are under ignored
`outputs/`; no credentials or personal emails were added to fixtures.

## Remaining work, in small verifiable steps

The user confirmed the karriere.at Playwright JSON: five valid records, HTTP 200,
15 cards seen, zero invalid cards, reported total 131, incomplete coverage. This
completes the manual first-source probe checkpoint. Raw Senior/Lead results are
expected because this command does not yet run screening.

1. User runs the LinkedIn five-job probe below and inspects JSON fields and links.
2. Add bounded pagination for the two sources, with repeat detection and page tests.
3. Fetch detail pages and validate full-description extraction separately.
4. Add generic observation persistence and provenance without fake email fields;
   integrate both sources into a separate test database and replay to prove
   no duplicate inserts. The source-neutral probe model alone is not persistence.
5. Fix audited lifecycle issues before production use: closed duplicate versus
   active listing, successful-cache freshness, and outdated-rule rescreening.
6. Then evaluate StepStone/Indeed and add application ledger/Excel synchronization,
   fresh bounded AI/Telegram delivery checks, and unattended deployment.

Still pending: full multi-source runner, durable per-query checkpoints, full
karriere descriptions, independent application event ledger, workbook import and
export integration, daily scheduler, notification outbox, monitoring and backups.
The Excel file created earlier is an empty control template, not a connected DB
view. No automatic application submission exists.

## Checkpoint 3 — LinkedIn public search probe implemented

Added `scrape --source linkedin`, reusing the existing HTTP/Playwright transports
and source-neutral batch schema. No new scraper dependency or account session is
required. Both sources remain independent of Gmail, with separate default output
files and appropriate location defaults.

Implemented native ID extraction and cross-checking against card URNs, tracking
removal, shared email/direct canonical URLs, optional publication dates, exact
versus lower-bound result counts, missing-description labeling and explicit
blocked/rate-limited/layout-failure outcomes. Fixtures use synthetic job text.

Live smoke results, `python` / `Vienna, Austria`, `limit=5`:

- Public HTTP search returned HTTP 200. The downloaded page parsed to five jobs
  from 60 cards, zero invalid cards, five unique IDs and no missing title/company/
  location in the sample. The reported `1,000+` is recorded as a lower bound.
- Playwright Chromium independently returned `status=success`, five jobs from
  60 cards, zero invalid cards. No account login, database writes, paid AI calls
  or Telegram messages were involved.
- Automated tests verify email/direct identity compatibility, duplicate cards,
  missing optional fields, mismatched URN/link IDs, untrusted hosts, blocked/login
  pages, redirects, empty results, rate limits, query encoding, CLI routing and
  source-specific output paths.

After this change: **127 tests passed**, Ruff passed, `git diff --check` passed.
Full descriptions, pagination, source-neutral database persistence and daily
integration still remain pending. This checkpoint establishes one successful
bounded public search, not a promise of ongoing unattended LinkedIn availability.

## Checkpoint 4 — saved searches and multi-source daily cycle

This checkpoint supersedes earlier pending-feature lists above.

Implemented:

- Paste a filtered URL with `scrape --url`, or save it with `search-add` for a
  multi-source daily run. Public host/path validation preserves filters and
  removes tracking. Configuration and output paths are documented in
  [OPERATIONS.md](OPERATIONS.md).
- Bounded pagination, duplicate-page detection, partial-result preservation,
  navigation/request limits and separately bounded full-description retrieval.
  karriere.at uses load-more; LinkedIn uses public frontend HTML continuation.
- Real generic timestamps/provenance with empty email metadata for direct-only
  records. Native IDs share storage across email/direct routes. Missing values
  do not erase better existing fields. Source changes preserve application events.
- The new `daily` cycle attempts email and each source independently, screens
  current full requirements, exports JSON/Excel/Telegram preview and creates a
  consistent SQLite backup. Paid scoring and Telegram delivery are explicit flags.
- An independent application ledger supports idempotent user-confirmed actions,
  optimistic revisions, daily Telegram buttons and controlled Excel status import.
  Input workbooks are archived before regeneration; stale/duplicate/formula edits
  are rejected with receipts. Website text exports as literal spreadsheet data.
- Telegram connect/check commands validate access without sending by default.
  Credentials are saved privately, and callback events are replay-safe. Daily
  notification state prevents acknowledged duplicates and exposes unknown sends.
- Fixed any-expired-member group suppression, added successful-enrichment TTL
  and refresh, retained stale content after transient failure, and rescreened
  outdated rules. Existing scores/drafts/deliveries survive additive migrations.
- Linux daily timer and callback service examples are supplied. Local backups
  retain seven snapshots. Production hosting, external monitoring, off-host backup
  storage and seven-day unattended acceptance are not configured or proven.

Observed live checks (no paid AI and no Telegram messages):

| Check | Result |
| --- | --- |
| karriere.at pagination/details | 2 pages, 25 returned jobs, 1 full description, success |
| LinkedIn pagination/details | 2 portions, 65 returned jobs, 1 full description, success |
| Combined initial clean-environment run | Gmail 5 emails + LinkedIn 5 jobs persisted; karriere timeout yielded partial report; 29 inserted, Excel and backup produced |
| Recovery run via HTTP | Both searches successful, Gmail 5 emails; 5 new karriere rows, 32 unchanged observations; success |
| Replay after recovery | 0 inserted, 0 changed, 37 unchanged observations; both searches and email successful |
| Telegram connection | Bot and intended chat access verified read-only; nothing sent |

Source/observation counts differ from unique vacancy counts; the replay's 37
unchanged inputs include repeated appearances. Replays never imply complete site
coverage. The LinkedIn UI initially appended duplicate results; the repeat guard
reported partial rather than hiding that behavior, before continuation was fixed.

Validation: clean dependency installation succeeded; **154 tests passed** and
Ruff passed. The established core Ruff rule set is explicit to keep checks stable
across releases. Slow local bytecode imports required a separate temporary
verification environment; no workaround path is a production dependency.

The public GitHub snapshot excludes the populated candidate profile and generated
egg-info. A blank profile example and CI setup are included; the actual profile
remains local. Public publication starts from a clean root commit so private
profile contents from the original development history are not uploaded. That
original history is retained in the local master branch.

Remaining acceptance: user review of Excel import and real Telegram delivery,
deployment to a chosen persistent host, restore/off-host monitoring setup and a
seven-day unattended run. Source-query cursor resume and more conservative fuzzy
repost matching remain future work. Automatic application submission is absent.
