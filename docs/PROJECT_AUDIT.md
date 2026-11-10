# Job Radar — checkpoint and technical audit

Audit date: 2026-11-08. Inspected baseline: `1c06c677f184c8e6a6ae2f7691e342f0cc899bce`
(`codex/mvp-v1`, existing annotated tag `v1.1.0`). This checkpoint changes documentation
only. Version stays 1.1.0; no new tag, push, merge, deployment or history rewrite.

## 1. Executive summary

Job Radar has a working, extensively fixture-tested local pipeline for discovering
Vienna IT jobs, assessing application constraints, optionally scoring technical
fit, exporting review files and delivering a stateful Telegram digest. It is not
an autonomous application system or a multilingual bot control center.

**Three immediate engineering priorities:**

1. Improve bounded JD acquisition and source observability: a cached provisional
   score currently blocks daily enrichment of that candidate; incomplete enrichment
   can leave the overall run marked successful. More scraping volume alone will
   not fix missing requirements/hours evidence.
2. Align screening with downstream location eligibility. An explicit Austria-remote
   role with another Austrian city in its location can be rejected before the
   richer eligibility assessment gets a chance to preserve it.
3. Strengthen identity/lifecycle handling and its acceptance examples before
   expanding sources: semantic grouping is exact normalized title/company/location,
   full descriptions are not aged out by daily, and pending/unknown sends need
   operator reconciliation. Preserve existing application and delivery state.

A separate **publication security gate** remains: historically committed credentials
outside this branch's ancestry require rotation/revocation; private old branches
must not be published. No new paid requests or live messages were used in this audit.

### Evidence and status vocabulary

- **IMPLEMENTED AND TESTED**: inspected implementation plus passing automated tests
  with fixtures/fakes. This label does not imply current live account/site access.
- **IMPLEMENTED BUT NOT VERIFIED**: code/configuration exists; its intended real
  operating environment or acceptance condition has not been demonstrated here.
- **PARTIALLY IMPLEMENTED**: meaningful pieces exist, but named scope has gaps.
- **NOT IMPLEMENTED**: no corresponding working feature found.

| Subsystem / scope | Status | Evidence boundary |
| --- | --- | --- |
| Gmail MIME ingestion and supported alert parsing | IMPLEMENTED AND TESTED | Parser/error tests; previous live evidence is dated, not repeated |
| LinkedIn/karriere.at public search, bounded pagination | IMPLEMENTED AND TESTED | HTML/transport fixtures, limits and partial-failure tests |
| Consistent live discovery coverage and automatic source recovery | PARTIALLY IMPLEMENTED | No resumable cursors, persistent site cooldown or measured coverage guarantee |
| Normalization, native dedupe, SQLite persistence/migrations | IMPLEMENTED AND TESTED | Temporary DB, reopen/replay, migrations and integrity tests |
| Cross-source/repost identity resolution | PARTIALLY IMPLEMENTED | Exact normalized grouping; no robust alias/fuzzy reconciliation |
| JD extraction/enrichment | PARTIALLY IMPLEMENTED | Adapters, caches and budgets tested; daily selection/lifecycle gaps below |
| Deterministic filtering and application priority | IMPLEMENTED AND TESTED | German/English fixtures; reproduced location false-negative remains |
| Structured AI scoring/cache/queue | IMPLEMENTED AND TESTED | Fake provider/SDK tests, including budgets 0/2/5; no new real request |
| Model/account availability now | IMPLEMENTED BUT NOT VERIFIED | Configured model is known; no availability check performed |
| Daily Telegram outbox, dedupe and status callbacks | IMPLEMENTED AND TESTED | Mock acknowledgements/failures, next-day/restart replay tests |
| Live human callback interaction on target host | IMPLEMENTED BUT NOT VERIFIED | Neither a real button press nor Ubuntu polling tested here |
| Application ledger and Excel interchange | IMPLEMENTED AND TESTED | Revision/replay/import/formula/reopen tests |
| Cover-letter/recruiter-message generation helpers | IMPLEMENTED AND TESTED | Fake-provider draft/rewrite/cache tests; separate from daily |
| Multilingual Telegram Control Center | NOT IMPLEMENTED | No command router, locale preferences or interactive queue navigation |
| Autonomous AI Application Preparation Agent / auto-apply | NOT IMPLEMENTED | Existing draft helpers are not an agent |
| Ubuntu service/timer configuration | IMPLEMENTED BUT NOT VERIFIED | Prepared files; no Linux host execution or service installation |
| Unattended production, off-host backups and external heartbeat | PARTIALLY IMPLEMENTED | Local backup/run reports exist; host acceptance/independent monitoring absent |

## 2. Repository baseline and structure

Initial working tree was clean: no staged, unstaged or nonignored untracked changes.
There were 121 tracked files. Branch `codex/mvp-v1` pointed at the release commit;
`v1.1.0` already existed and remains unchanged. Locally recorded `origin/main` was
`7e8ee5c`; no fetch was made, so this is not a current remote-state claim.
The release branch was three commits ahead of that cached reference.

Other local branches (`master`, `recovery/job-radar-current`,
`recovery/job-radar-lost`) are preserved and contain separate private development
history. The unrelated nested `tracelum-v2` repository is preserved and excluded
only through local `.git/info/exclude`; it is not part of Job Radar's source.

| Location | Responsibility |
| --- | --- |
| `src/job_radar/cli.py`, `bin/job-radar` | Command parsing, settings and integration construction; launcher selects external venv |
| `gmail.py`, `parsers/` | Readonly Gmail OAuth/MIME and LinkedIn/Indeed alert extraction |
| `sources/`, `collection.py`, `searches.py` | Public direct connectors, saved URLs, browser/HTTP pagination and budgets |
| `models.py`, `ingestion.py`, `pipeline.py` | Source identities, observations, normalization/ingestion boundaries |
| `storage.py` | SQLite schema, additive migrations, repositories, caches, application/run history |
| `screening.py`, `work_hours.py`, `eligibility.py`, packaged rule JSON | Screening, evidence interpretation, application constraints and priority |
| `enrichment.py`, `enrichment_adapters/`, `http.py` | JD extraction, fallback, caching, timeout/rate/retry handling |
| `ai/`, `matching.py`, `candidate.py`, `application_models.py` | Provider protocol, structured scores/drafts, hashes, queues, usage estimates |
| `daily.py`, `runner.py`, `telegram.py` | Main multi-source cycle, legacy email cycle/file lock, transport/callbacks |
| `exporting.py`, `workbook.py`, `backups.py` | Canonical views, JSON/CSV/Excel interchange, consistent backups |
| `tests/`, `tests/fixtures/` | 23 test modules plus sanitized HTML/JSON regression fixtures |
| `deploy/`, `.github/workflows/ci.yml` | Ubuntu examples and test-only CI; no deployed scheduler |
| `README.md`, `docs/`, `CODEX_START.md`, `AGENTS.md` | Current setup, dated evidence, policies, operations and development guidance |
| `data/`, `secrets/`, `outputs/` | Private local state/output; only placeholders tracked for data/secrets |

Python requirement is >=3.11; this audit ran Python 3.12.14. Runtime dependencies
cover BeautifulSoup, Google Gmail/auth clients, OpenAI, openpyxl, Pydantic, dotenv
and YAML. Playwright is the scraping extra; pytest/Ruff are development extras.
`uv.lock` and hash-pinned `requirements.lock` include runtime/dev/scraping/build
resolution. `scripts/setup.sh` uses the hashed export then installs local source
without dependency resolution/build isolation. The sole version definition is
`job_radar.__version__`; setuptools and CLI derive 1.1.0 from it.

No artifacts or user files were deleted. Caches, env variants, DB/WAL/SHM files,
OAuth JSON, private profiles, logs, backups, Excel output and build artifacts are
ignored. Sample ignore checks passed; no tracked file is accidentally ignored.
Source/tests, legacy compatibility code, policy versions, example configs and dated
architecture/audit documents are retained because they support tests, migrations,
operations or development evidence. Some older documentation is superseded.

## 3. Current architecture and job collection

`cli._daily` constructs independent fetchers and calls `daily.daily_cycle`:
email/direct collection → source-key upsert/observations → screening → bounded
eligible enrichment → rescreen → optional scoring → eligibility/ranked A/B review
→ JSON/Excel/Telegram preview → optional outbox delivery → run records + backup.
SQLite is authoritative. Excel is a snapshot/status interchange, not a second DB.

### Supported acquisition and search profiles

- Gmail readonly alerts: LinkedIn and Indeed sender/layout-specific parsers.
  This is an independent email route, not a general email/NLP extractor. LinkedIn
  alert parsing often provides title/company/location without JD text.
- Direct LinkedIn public HTML and karriere.at public HTML. There is no authenticated
  LinkedIn API connector. LinkedIn continuation uses the public frontend guest
  HTML endpoint, not a supported official vacancy API.
- Indeed has email parsing and detail enrichment, but no direct saved-search
  collector. StepStone, AMS and other proposed Austrian boards are not implemented.
- `searches.load_searches` accepts 1–8 enabled saved searches with validated HTTPS
  board paths, unique names/URLs and some tracking/token parameters rejected/removed.
  `search-add` saves validated site-generated filter URLs. Filter semantics are
  delegated to sites, not proven by local validation.
- Six example profiles cover German Python/backend, English junior backend,
  Werkstudent/working student, automation/integration, German QA automation and
  junior data/AI. Four use LinkedIn, two karriere.at; searches may overlap even
  though stored native IDs dedupe. No new profiles/requests added in this audit.

### Pagination, limits and failure behavior

`collect_search` supports 1–10 pages, 1–500 returned jobs and 0–25 direct details;
CLI `scrape` caps its job limit at 100. `daily` defaults are 2 pages, 25 jobs/search,
zero unscreened details, 100 emails, five global eligible-enrichment operations,
zero AI attempts, Telegram disabled. Deployment examples use 1 page/15 jobs per
search and five AI attempts with Telegram enabled; deployment is therefore an
explicit paid/send operation unless its configuration is changed.

Playwright uses isolated headless Chromium without saved account cookies. Karriere
uses load-more/cumulative cards; LinkedIn follows observed card offsets to guest
continuation pages. HTTP has separate page/offset construction. Native-ID repeat
protection prevents endless identical pages. Partial results survive later-page,
parse or detail failures. Unknown layouts, auth walls, 429 and other failures have
typed outcomes rather than fabricated empty success. Missing a job in a bounded
search is never expiry proof.

Searches have a 120-second between-iteration/detail budget. Gmail daily uses a fixed
10-day lookback and 120-second between-message budget, not a durable incremental
cursor. Playwright navigation defaults to 20 seconds and two seconds between
client actions; HTTP daily search uses 15 seconds/two-second spacing/one retry.
HTTP enrichment defaults to 15 seconds/one-second spacing/two retries. Retryable
HTTP statuses are 429/500/502/503/504 plus transport failures, with exponential
backoff. No persistent per-board cooldown or Retry-After handling is implemented.
Stage budgets are cooperative and can overrun by in-flight calls/retries; they
are not precise whole-run deadlines. Each board/browser task has overhead and
browser subresource requests are not counted by the logical request budget.

Gmail failure, bad individual email and failed search are caught independently in
`daily_cycle`. Source-stage results persist in SQLite/reports. `batch.error` detail
is reduced to status/stop reason in the daily report; troubleshooting granularity
is lower than the standalone probe. Site selectors, filters, login walls and IP
access remain external reliability dependencies, not guarantees from unit tests.

### Description coverage and enrichment

Adapters prefer JobPosting JSON-LD, then source-specific HTML; employmentType and
workHours JSON-LD evidence is retained as labelled JD text. Language detection is
DE/EN/unknown heuristic. Confidence is an extraction heuristic, not proof that all
requirements or hours were present.

`daily._enrich_review` targets actionable technical candidates with incomplete JDs,
using `enrich_jobs` caches, a global operation/time limit, 24h failed-attempt
cooldown, group alternate URLs and rescreening before paid scoring. Snippets and
missing requirements stay provisional/low confidence. A transient refresh failure
can retain older useful content with an explicit error.

Limits: daily never refreshes already-full JDs just because they are old. The
standalone enrichment function supports TTL/refresh, but daily's selector skips
those records first. A successful provisional score also removes its candidate
from the reused scoring planner, preventing automatic later JD acquisition (F1).
HTTP enrichment does not fall back automatically to browser detail retrieval.
Full-JD coverage is not currently measured live. The dated launch snapshot in
`MACOS_STAGING.md` had 4 full JDs among 34 source records (0/24 email-backed);
this is historical evidence, not today's DB or an estimate of current sites.

## 4. Normalization, storage, migrations and recovery

`Job` and `SourceVacancy` are immutable dataclasses. Canonical native identity is
`source:source_job_id`; board URLs use native IDs and drop tracking where possible.
Indeed sponsored alerts lacking a native key use a deterministic synthetic ID,
which has weaker repost/link identity guarantees. Not every sponsored URL can
shed its query parameters without breaking its destination.

Cross-source semantic identity is a hash of normalized **exact** title, company
and location (accent/case/punctuation normalization). It is not fuzzy matching.
`exporting.semantic_groups` preserves source records and chooses an active,
full-description/direct/recent canonical member. An expired duplicate does not
hide an active group member. Aliases, changed titles/locations, missing companies
and reposts can split or incorrectly combine groups.

`JobRepository` creates SQLite parent directories, enables WAL, foreign keys and
5-second busy timeout, executes the schema and additive migrations on opening.
There are 15 application tables (plus SQLite's internal sequence table):

| State | Tables |
| --- | --- |
| Source listings and observation history | `jobs`, `job_observations` |
| JD cache | `enrichment_cache` |
| AI compatibility/cache | `application_packages`, `job_scores`, `application_drafts` |
| Application state and append-only events | `applications`, `application_events` |
| Telegram compatibility/callback offset | `telegram_deliveries`, `telegram_state` |
| Daily immutable payload and item identities | `notification_outbox`, `daily_alert_items` |
| Run diagnostics | `run_history`, `run_stage_history`, `run_item_status` |

`upsert_detailed` distinguishes inserted/changed/unchanged inputs; empty incoming
fields preserve better stored data and older emails cannot overwrite newer direct
content. Source content changes reset derived screening/JD fields; persisted
application events, scores and outbox survive. Observation identities combine
native key, connector, search URL and content hash, retaining first/last seen.
No raw MIME message is stored as a DB table, but snippets, alert subjects, metadata,
profile-derived outputs and reports are still private operational data.

Migrations use column introspection, additive ALTERs, indexes/backfills and legacy
package/delivery conversion. There is no numbered migration ledger, downgrade
framework or schema-version acceptance matrix. Some legacy backfills infer HTTP
200 from old statuses; that is compatibility data, not a new HTTP observation.
Opening a DB is a mutating operation: this audit only opened temporary test DBs.

`RunLock` uses Unix fcntl on a DB-specific file to exclude overlapping daily runs.
Application writes use SQLite transactions and revision checks. This is suitable
for one persistent local host, not shared network storage/multiple independent DBs.
`backups.backup_database` uses SQLite's consistent backup API and quick_check;
seven snapshots by run are retained. It is not seven guaranteed calendar days.
No off-host copy/replication exists. Preserve jobs, application events, score caches,
outbox/item identities and callback offsets when migrating; copying a live main
file alone can lose WAL data. Restoring old outbox state can cause repeat alerts.

## 5. Filtering accuracy and application prioritization

`screen_job` assigns eligible/review/rejected from title/location/requirements and
screening-v4 rules. It rejects explicit senior/lead/manager/architect scope,
required >=4 years, incompatible known locations, irrelevant professions and
expiry. Unknown context remains review where rules allow it. Full JD is used
when available rather than an older snippet.

`assess_application` then derives hours, seniority, role, location, eligibility and
HIGH/MEDIUM/LOW/EXCLUDED priority separately from AI technical match. Policy JSON
and local preference overrides provide 20–25h target, 15–30h acceptable, required
three-year experience as possible/review and >=4 as incompatible. Preferred years
and company history are distinguished from required professional experience.
Student/junior/intern titles and scoped management requirements have separate rules.

`assess_hours` recognizes German/English weekly ranges, metadata and explicit
part/full-time alternatives. States are confirmed_target, acceptable,
flexible_part_time, unknown and confirmed_mismatch. Salary-equivalent/company-wide
hours are reference evidence; contradictions become uncertain review. Unknown
hours never create a score or imply full-time availability. Bare title/metadata
hours can be interpreted as employment hours, so absence of a weekly unit is still
a heuristic risk; generated `hours_status=full_time_only` can also mean numeric
hours outside the acceptable range: use hours_mismatch_kind for the distinction.

Location allows Vienna, explicitly Austria-remote and verified EU-remote permission;
uncertain remote/work authorization stays review. Residence/company name does not
prove authorization. Early screening has a narrower location rule than downstream
assessment (F2). Other false-negative/positive risks include mixed-city strings,
role polysemy, negation, unusual experience wording, multilingual text beyond
DE/EN, generic benefits and truncated/missing requirements.

Priority sums hours/seniority/role/location evidence, full-JD/freshness points and
a small optional AI contribution. Hard constraints override technical similarity.
Unknown hours prevent HIGH; ambiguous/other-stack/weak technical cases are demoted.
`application_sort_key` ranks priority/points/publication/first-seen with stable ID.

`daily.review_rows` resolves the current profile/model/prompt/description score:
Tier A requires successful current strong_apply/apply or review >=50; provisional
scores remain labelled. Tier B contains actionable deterministically screened jobs
with no current evaluation, explicitly UNSCORED. Missing provider/budget does not
erase Tier B. Failed evaluations, skip/low-review results, rejected/expired or
actioned jobs are not promoted by disabling scoring. Unknown profile context is
also not equivalent to a verified current evaluation. Current ordering combines
A/B by application priority; it does not alternate tiers as an older staging
paragraph describes. See the superseding `APPLICATION_PRIORITY.md`.

## 6. AI scoring, efficiency and existing draft support

`OpenAIProvider` implements the `AIProvider` protocol with Responses structured
Pydantic output and store=false. Config/default example model is `gpt-5.4-nano`;
private configured value is deliberately not copied into this public report.
Live availability/pricing/deprecation statements from earlier docs were not
revalidated here. Default estimate rates in code are $0.20/input-million and
$1.25/output-million; these are configuration, not verified current billing rates.

Inputs: the loaded candidate-profile mapping plus vacancy title/company/location,
effective description/language/confidence/fetch/screening status and provisional
flag. The whole profile is sent, so private contact fields are not automatically
removed from input; the prompt instructs the model not to echo them. It treats JD
instructions as untrusted and forbids invented candidate/job facts. Skill,
experience, seniority, role, location and language weights are 35/25/15/10/10/5;
hours are explicitly separate. No tools, agent loop or automatic application.

Cache identity includes semantic key, full profile hash, effective description
hash, score prompt version (`candidate-score-v3-hours-independent`) and model.
Successful unchanged scores are reused; incomplete JDs force provisional=true and
low confidence locally. Because the entire profile is hashed, preference/contact
edits also invalidate scores even if verified technical facts did not change.
Score rows are upserted per identity, not an append-only paid-request ledger.

`scoring_plan` removes current successes/cooling failures before selecting calls.
Actual priority is hours evidence → early-career seniority → relevant role → full
JD → newly discovered → recent first-seen. Newness is not the first comparator.
A >=2-item budget reserves one FIFO slot for day-old backlog; a one-item budget
reserves it every third run. Failed daily scores retry after 24h unless explicitly
retried; the explicit retry flag selects failures only. Limits apply to attempted
candidates, not just successful results. Daily does not generate drafts.

Daily accepts 0–25 scoring attempts (default zero). Provider bounds: timeout 1–120s
(default 30), SDK retries 0–2 (default zero), output tokens 256–4096 (default 1200),
UTF-8 JSON payload 1024–100000 bytes (default 48000). Oversized input fails before
request; no silent truncation. The byte cap excludes system/schema overhead and
is not a token/dollar ceiling. Retries can multiply actual wire calls beyond the
candidate budget. Successful usage/estimated costs and safe failure labels persist;
usage unavailable on errors may still be billed. Stored cost sums are not invoices.

Standalone `score`/`draft` accept optional unbounded `--limit` rather than daily's
0–25 range, and their provider construction does not pass custom Settings request
limits. Dry-run score listing also differs from the actual planner. These paths
need parity before being exposed through a bot (F5); do not schedule legacy `run`.

Existing `draft_jobs` creates a 180–260-word cover letter and recruiter message
from profile/JD/stored score, with one length rewrite and cache/error states.
DE/EN/unknown text support exists; there is no attachment/resume-tailoring/review
workflow or autonomous preparation agent. These paid helpers were not called.

## 7. Telegram and application tracking

`TelegramApiClient` implements sendMessage, editMessageText, answerCallbackQuery,
getUpdates plus connection getMe/getChat. CLI commands are telegram-connect,
telegram-check, telegram-preview, telegram-send and telegram-poll; these are
terminal commands, **not** bot slash commands. getUpdates requests callback_query
only, so bot text commands are not handled. Sending `/start` opens the bot chat
but no application-level /start handler exists.

Primary daily digest: plain English source/status summary, A/B labels, actual AI
score/confidence or UNSCORED, hours/seniority/JD/priority/reason and vacancy link.
Up to eight items fit within 3800 UTF-16 units. Daily buttons are Open, Applied,
Skip. Application priority drives selection; one fitting slot is reserved for
an oldest candidate pending >=3 days. Overflow remains a later-day queue.

`_deliver` atomically reserves immutable chat/day payload and native/semantic item
identities before a network send. It marks a request unknown before attempting
remote delivery, sent after acknowledgement, pending after definitive rejection.
Unknown is suppressed until operator verification. A same-day rerun does not
replace a sent payload; later scoring/priority/title changes do not resend a known
native identity. Native identities help canonical-source switches. Exactly-once
remote delivery cannot be guaranteed across timeout/crash/restore. Old-day pending
payloads are not automatically drained; no operator reconciliation CLI exists.

Legacy scored cards have Open vacancy/Cover letter/Applied/Skip and require a ready
draft. They use send-then-save delivery, not daily's outbox. Daily consults legacy
acknowledgements, but the reverse workflow is not a unified duplicate guarantee.
Do not run both notification schedulers. The daily Apply/Skip callback acknowledges
the action without editing that aggregate digest card; legacy individual cards edit.

Callback authorization compares configured chat ID. It does not independently
allowlist callback.from.id; private chat is intended. Group use would authorize
other members in the configured group. Actions have strict ID patterns and
idempotent callback request IDs. Poll offsets persist after processing; errors
before offset acknowledgement can replay. SQLite actions are idempotent, but a
legacy cover-letter send could repeat. Network errors escape the poll loop; the
prepared systemd service supplies restart, not in-process retry supervision.

No locale preferences, translation catalog, Ukrainian Telegram UI, slash-command
menu, search management, queue browsing, undo confirmation flow or language switch
exists. The Excel labels are Ukrainian and JD/draft language handling is separate
from bot localization. No general Telegram Control Center should be claimed.

`applications` supports not_applied, saved, applied, interview, offer, rejected,
withdrawn, skipped. Allowed states are validated, but no transition graph prevents
changing any valid state to another valid state. `application_events` records
request ID, native key, actor, note and timestamp; revision increments and
expected-revision conflicts prevent stale Excel edits. Semantic peers inherit
state via application_for, with native-key/legacy fallback. Conflicting actions
across peer identities are resolved by latest timestamp; timestamp ties/semantic
regrouping need additional acceptance coverage. No automatic submission inference.

`application-set`, daily callbacks and Excel import write the ledger. Only
not_applied/saved remain reviewable. Workbook edits are archived before import,
validated against schema/ID/revision/duplicate/formula constraints, and produce
receipts. JSON/Excel/top-five reports expose state/fit/priority; there is no
interview reminder/calendar, attachment tracking or employer-response integration.

## 8. Deployment, observability and Ubuntu readiness

`job-radar` console script and `python -m job_radar.cli` are entry points;
`bin/job-radar` sets source/cache/browser paths and project working directory.
`daily` is the multi-source production candidate. `run` is a legacy email path.
The old Makefile still uses `.venv` and loose pip installation rather than the
release's cache/lock workflow: documentation/build-command debt, not a dependency
of the recommended setup.

Settings precedence: shell → .env → data/.env. Shared DB defaults to data/jobs.db;
explicit daily/application-set --database wins. Before v1.1.0 those two commands
silently used data/mvp-v1-test.db; owners must explicitly select the authoritative
existing DB. No automatic merge/move occurs. Candidate, OAuth/token, AI, Telegram
and custom eligibility settings are documented in README/examples. Candidate YAML
validation is only a mapping check plus minimal daily scoring content check;
malformed nested preferences/numeric env values do not have comprehensive schema
validation. Some errors arise before a run record can be created.

Source failures and scoring failures persist in run history/stage records and
latest/per-run JSON. Previous running records become interrupted on next locked
run. Preview files work without sending. Incomplete JD outcomes have a stage
partial flag, but may not set report.errors, partial run status or digest warning
(F3). Lock/DB/output/final-report failures before/after the guarded region can
prevent a summary; failure observability is not guaranteed when the host is down.
There is no structured log/metrics backend; CLI progress/report JSON goes to stdout
and the prepared systemd units route stdout/stderr to journal.

Ubuntu + systemd matches fcntl, local SQLite, browser and continuous callbacks.
Windows is not a target (fcntl); macOS offline checks do not validate Linux.
Examples: daily oneshot under jobradar with persistent state/cache, 25-minute
hard timeout, Vienna 08:00 Persistent timer with up to 60s jitter, and a separate
callback process Restart=always/15s. No units were installed, verified on Linux,
enabled or started. Units and the daily wrapper repeat default path declarations. Verify the effective
service environment and shared DB paths on the target host rather than inferring
them from the macOS shell; no systemd environment behavior was tested here.

CI is test-only Ubuntu24.04/Python3.12, bounded 15 minutes with read-only repository
permissions and pinned Python dependencies. It has no scheduled production job,
private DB persistence or bot/account secrets. A prepared workflow is not evidence
that CI has executed remotely. Prefer the existing Ubuntu persistent-host plan;
do not introduce ephemeral production Actions state transfer for this checkpoint.

Before unattended acceptance: approve host, install pinned runtime/Chromium and OS
libraries, transfer a consistent authoritative DB/profile/working tokens privately,
verify access/permissions, manual bounded run, live callback/restart, restore drill,
timer/reboot/catch-up, off-host backup and independent dead-man monitoring. Keep
laptop writers/poller stopped during the single-host state transfer. Seven-day
unattended acceptance is outstanding; no production-ready claim is justified.

## 9. Security audit

A targeted in-memory scan checked current tracked files and all nine locally
reachable commits across refs (six ancestors of this baseline). It compared
configured credential/contact values and API/bot/OAuth/email/user-path patterns.
No values were printed. Current tracked files and release ancestry had no detected
key/token/populated-profile findings. This is not proof against all encodings,
unknown secrets, remote copies, reflogs or unreachable objects.

Four historical file findings outside release ancestry remain: a recovery commit's
example env contains an OpenAI key matching the currently configured value plus
an older Telegram token; three old master/recovery commits contain a populated
candidate profile. Eight existing commits' author/committer metadata contain a
personal email address. The release commit uses a noreply identity. Historical
sharing is unknown; no fetch/search/remote inspection was performed.

**Required before publication/paid use:** replace/revoke the exposed OpenAI key,
revoke or verify revocation of the historical bot token, and resolve author-email
privacy acceptance. Publish only reviewed intended refs, never --all/--mirror or
private master/recovery branches. No key rotation, bot revocation, branch deletion
or history rewrite was attempted. The pre-existing release tag is preserved.

CLI/launcher use private umask, Gmail token writes chmod600, connection setup saves
bot config privately, daily exception reporting uses safe type/status labels and
provider requests use store=false. Existing private-file permissions still need
host review; ignored is not encrypted/revoked. Candidate input is sent to the model
when scoring, and AI/draft text may contain private facts. Legacy runner retains
some str(exception) paths: debug/raw exception logging is a risk even though daily
sanitizes its ordinary integration failures. HTTP redirects in generic enrichment
are not revalidated like direct collection redirects; supported input sources are
constrained, but unexpected destinations should have dedicated acceptance tests.

Private audit/test outputs are ignored under outputs/checkpoint-audit and were
not staged. No authoritative DB or credential file was altered or deleted.

## 10. Verification performed for this checkpoint

All source tests ran against a fresh copy of tracked public files, blank profile
and six example searches in a temporary project. Python 3.12.14 from the previously
validated isolated release venv was reused; this checkpoint did not reinstall
packages or claim a second clean-install test. Real integration env variables were
removed/blanked. Main pytest and additional reproduction processes blocked socket
connect/DNS through an audit hook; existing subprocess tests mock transports or
only render help. This is not an OS-level network sandbox.

| Check | Result |
| --- | --- |
| Full pytest | **254 passed, 0 failed**, 4.24s |
| Ruff | Passed |
| pip check | No broken requirements |
| CLI --version / root --help / daily --help | Passed; version 1.1.0; no service access |
| Blank profile + six saved-search URL configuration | Passed; no site-filter semantic check |
| Fresh SQLite/init, quick_check, schema inspection | Passed; 15 application tables |
| Migration tests | Passed as part of full suite; legacy Telegram schema and existing compatibility fixtures |
| Offline end-to-end/filtering/priority/preview/replay tests | Passed as part of full suite |
| AI budgets 0, 2, 5, missing key/cache/failure/cooldown | Passed with fake providers |
| Same-day/cross-day/restart notifications and Applied/Skipped persistence | Passed with mock transport/temp DB |
| Source error isolation, backup failure, interrupted runs | Passed as part of full suite |
| Ignore/whitespace/shell syntax checks | Passed |
| Targeted working-tree/history secret scan | No current/release-ancestor file findings; historical issues above |

Additional disposable synthetic reproductions confirmed F1, F2 and F3 below.
There were no new live collections, Gmail/API checks, paid AI calls, Telegram sends,
real applications or deployments. No authoritative-state mutation was needed.

Earlier evidence remains dated: docs/PRODUCTION_AUDIT.md records local Nov5 real
sources, two approved scores, one Telegram acknowledgement and restart replay;
MACOS_STAGING/APPLICATION_PRIORITY record later offline fake-provider comparisons.
These are historical recorded observations, not reverified current availability.
Release notes record the prior clean-install/wheel/offline Chromium checks. Current
pytest success is not real-site coverage, human callback or unattended-host proof.

## 11. Known bugs, limitations and technical debt

| ID | Evidence | Finding and consequence |
| --- | --- | --- |
| F1 — JD selection/cache coupling | Reproduced offline | Synthetic eligible snippet job scored once with fake provider; next daily with enrich-limit=5 made zero JD requests. _enrich_review reuses scoring_plan, which skips its successful provisional cache. It can stay incomplete indefinitely with unchanged source text. |
| F2 — Location stages disagree | Reproduced offline | Junior Python role, location Graz, body explicitly “Remote work from Austria”, 20h/week: screen_job rejects location; assess_application without that earlier rejection accepts austria_remote. Final pipeline keeps the screening rejection. |
| F3 — Incomplete enrichment health | Reproduced offline | Synthetic JD transport failure yielded enrichment.status=partial but run.status=success. Stage detail shows it; summary/source errors do not necessarily warn about missing requirements. |
| F4 — Full JD lifecycle | Verified code path | _enrich_review skips has_full_description before TTL logic, so daily does not periodically check stale full JDs/expiry. Absence in search is correctly not expiry, but known-active totals may include old unknown availability. |
| F5 — CLI budget/config divergence | Verified code path | Standalone score/draft default limit=None and instantiate provider without custom Settings request bounds. Daily has bounded limits and passes settings. Manual dry-run queue is not the true scoring planner. |
| F6 — Identity resolution limits | Verified algorithm; future acceptance needed | Exact normalized title/company/location can split aliases/reposts or merge sparse jobs. Actions/alerts inherited through semantic identity need conflict/regrouping tests before any fuzzy expansion. |
| F7 — Delivery recovery boundaries | Verified code/tests | Unknown sends require operator review; old-day pending outbox is not auto-drained. Mixing daily and legacy send paths or restoring old state can repeat alerts. No recovery control UI. |
| F8 — Operational configuration | Verified files; host untested | Effective service paths remain host-unverified; Makefile uses older setup; some env/profile validation fails before run diagnostics. |
| F9 — Documentation drift | Verified docs vs code | Staging prose describes tier alternation/seven-day scoring aging that current priority/FIFO code superseded. Some docs describe V1 targets as though fully delivered. Current code, this audit and dated evidence must be read separately. |
| F10 — Scale/accounting debt | Verified code structure | Repeated full repository reads, per-row lookups/commits and full-JD rescreening each cycle; migrations run on every repository open. Cost upserts are not a request ledger; no measured large-backlog performance/coverage percentage. |

Other scope limitations: regex/HTML extraction cannot understand all layouts;
metadata confidence is not employer confirmation; public search response totals
are not full coverage; failed request usage can be missing; no full locale system,
automated response/application submission or independent health monitor exists.
No coverage-percentage, throughput or live precision/recall target was measured.

## 12. Development chronology

Git dates and messages are evidence of commits, not proof of real-world execution.
The release branch starts from a sanitized root; older private branches are
separate histories. Dated docs include intermediate checkpoints not represented
by separate commits. No additional dates/milestones are inferred.

| Recorded Git date | Commit | Milestone supported by code/docs |
| --- | --- | --- |
| 2026-06-23 | dc70069 | Private baseline Job Radar; not a release ancestor |
| 2026-06-23 | a59828f | Private funnel/scoring/drafts/Telegram work on separate recovery branch |
| 2026-06-23 | c46a4e4 | Private incremental pipeline; retained on master/recovery |
| 2026-11-05 | e9bde78 | Sanitized public root: bounded direct sources, saved searches, daily review, application/Excel workflow |
| 2026-11-05 | da6727e | Lightweight imports and cached runtime launcher |
| 2026-11-05 | 7e8ee5c | Daily launch hardening and Ubuntu runbook; dated local live smoke in PRODUCTION_AUDIT |
| 2026-11-05 | b9a7eb3 | A/B unscored review, bounded daily enrichment, scoring fairness, funnel/search staging |
| 2026-11-05 | 5e6ab28 | Independent application eligibility and priority; scoped hours/seniority/location/role rules |
| 2026-11-08 | 1c06c67 | Reproducible v1.1.0 preparation, hash locks, shared DB default fix, version/README/tests and local tag |
| This checkpoint | Commit containing this report | Documentation-only audited baseline; no feature/version/tag changes |

## 13. Modular development roadmap — proposals only

Security rotation/privacy review is a prerequisite to publication/paid testing,
not a new feature. Below preserves the owner's feature priority order. Complexity
is relative engineering effort, not a delivery-time commitment. No task is
implemented by this report; existing components should be extended incrementally.

### 1. Collection reliability, JD coverage and health (high impact, medium)

- Problem: missing JDs, F1/F3/F4, changing layouts, opaque partial failures and no
  durable cooldown/cursor. More source pages alone do not improve actionability.
- Expected improvement: more evidenced hours/requirements and understandable source
  failures within the same request/runtime budget.
- Scope: separate enrichment selection from score-cache exclusion; bounded aged-JD
  revalidation; expose source stop/error/invalid-card/JD-quality counters; persistent
  cooldown/resume only after measuring need. Start with current two boards.
- Dependencies: preserved native IDs, caches, outbox/application state; sanitized
  changed-layout fixtures and owner-authorized small live probes.
- Acceptance: cached provisional candidate can obtain full JD without repeat paid
  scoring first; F1/F3 regressions covered; aged refresh stays globally bounded;
  later-page errors preserve jobs; one failed board cannot suppress the other;
  six-profile useful unique yield/runtime is recorded without excessive requests.
- Complexity: medium; split selection/health fixes from optional cursor work.

### 2. Normalization, identity and lifecycle regression set (high impact, medium)

- Problem: F6 aliases/reposts/sparse identities, inconsistent metadata and uncertain
  application-state inheritance during canonical or semantic changes.
- Expected improvement: fewer repeated vacancies without losing distinct roles or
  Applied/Skipped history.
- Scope: curated cross-source/alias/conflict fixtures, stable evidence/provenance,
  explain grouping decisions; conservative reconciliation before optional fuzzy
  matching. Keep SQLite and existing native keys.
- Dependencies: representative sanitized observations and current ledger/outbox
  compatibility; no destructive merge of authoritative records.
- Acceptance: known repost/alias cases resolve explicitly; distinct same-title roles
  stay separate; canonical changes and conflicting actions have deterministic
  tests; no extra notification or lost event after reopen/migration.
- Complexity: medium; fuzzy matching would require a separate decision/evaluation.

### 3. Screening/eligibility consistency and measured accuracy (high impact, medium)

- Problem: F2 hard rejection prevents richer location reasoning; text heuristics
  can misread unknown hours/experience and nontechnical use of IT terms.
- Expected improvement: fewer missed actionable part-time roles and clearer human
  review reasons rather than broadening every filter.
- Scope: align early/detailed location evidence; add adjudicated DE/EN examples for
  remote cities, hours contradictions, required/preferred years and mixed roles.
- Dependencies: full JD evidence from task1; verified candidate preferences, not
  inferred citizenship/availability.
- Acceptance: F2 survives as Austria-remote when explicitly supported; incompatible
  work-country/full-time/seniority cases still reject; unknown stays review; publish
  precision/false-negative examples on a fixed labelled set without altering AI
  technical scores or application events.
- Complexity: medium; small rule changes plus meaningful regression fixtures.

### 4. AI scoring efficiency and command parity (medium impact, small–medium)

- Problem: F5 manual commands differ from daily; incomplete input/cache preference
  churn and partial usage accounting can waste budget or mislead cost estimates.
- Expected improvement: predictable actual attempts and useful input before spend.
- Scope: consistent finite limits/settings across score/draft/daily and dry-run
  planner parity; separate preference-only invalidation if supported by evidence;
  expose unknown-cost failures without calling them free. No new provider needed.
- Dependencies: task1 JD selection and task3 eligibility; explicit approval for real
  paid validation plus rotated key/model availability check.
- Acceptance: same context produces same candidate plan; 0/2/5 attempt limits and
  nondefault timeout/input/output bounds hold on all commands; unchanged scores
  reuse caches; failure cooldown/FIFO prevents starvation; no hidden draft calls.
- Complexity: small–medium; avoid a new billing database or agent scheduler.

### 5. Multilingual Telegram Control Center (medium impact, medium–large)

- Problem: callbacks only, English digest, no queue/config/status navigation; group
  authorization and ambiguous-send recovery are not designed as interactive tools.
- Expected improvement: faster daily human review in the chosen language.
- Scope: start with authenticated /help, /status, /queue and locale choice (e.g.
  Ukrainian/English/German), central message labels and paged review; add safe
  status change/undo and operator recovery previews incrementally.
- Dependencies: stable task2 IDs/state revisions, task3 priorities, per-user/chat
  authorization policy; reuse SQLite/callbacks/outbox rather than replace them.
- Acceptance: unauthorized user cannot change state; language persists; no paid
  call from read commands; callback replay changes state once; paging/restart
  cannot resend alerts; recovery requires explicit confirmation and audit event.
- Complexity: medium–large; first increment should be read-only status/queue/help.

### 6. AI Application Preparation Agent (later, large)

- Problem: existing draft helper lacks a complete human-reviewed preparation flow.
- Expected improvement: verified job-specific preparation, not autonomous submission.
- Scope: first expose bounded existing draft generation with evidence references,
  input preview, explicit approval and persisted draft/review state; consider agent
  orchestration only after that proves useful. No automatic employer outreach.
- Dependencies: tasks1–5 quality, secure profile handling, separate paid budget and
  clear human authorization for model inputs and every external action.
- Acceptance: no invented qualifications; chosen job/profile/JD provenance visible;
  generation retries bounded; cached unchanged draft reused; approval required
  before any message/application; failures preserve user state/history.
- Complexity: large for an agent; a manual draft-review increment is smaller.

### 7. Ubuntu deployment and automation (operationally necessary, medium)

- Problem: current local baseline does not establish an always-on useful service.
- Expected improvement: persistent daily discovery/delivery and observable failures.
- Scope: follow existing runbook on approved Ubuntu24.04 host, fix path/config
  discrepancies, privately transfer consistent state, verify units/browser, then
  timer/callback/restore/reboot and off-host backup/independent heartbeat.
- Dependencies: explicit host/deployment approval, rotated secrets, selected
  authoritative DB and one active bot poller; this need not wait for a future agent
  or Control Center once discovery/filter fixes are accepted.
- Acceptance: bounded complete configured run, real source access, approved Telegram
  send and button action, restart/idempotency, scheduled/reboot catch-up, restore
  integrity, off-host recovery and missed-run alert; observe seven scheduled days
  before declaring unattended V1 complete. No fake-validation DB deployed.
- Complexity: medium, dependent on host access and site IP compatibility.

## 14. Checkpoint and controlled-test decision

Only this report and its progress-log pointer are included in the new checkpoint
commit, `chore: checkpoint current Job Radar implementation`. No application code,
version, lockfile, existing tag or deployment configuration is changed. Local
private files remain ignored. Obtain the checkpoint hash from git log/owner-facing
completion report; embedding its own hash here would require rewriting the commit.

**Ready for a controlled, explicitly authorized live collection test, with limits.**
Use an isolated DB/output, bounded current-source searches, no AI/Telegram by
default, inspect JD/eligibility/source errors and preserve the authoritative state.
Current live credentials/sites are not verified by this checkpoint. Any paid AI
or real Telegram test requires separate explicit approval and the credential
security gates above. Ubuntu deployment and production readiness remain pending.
