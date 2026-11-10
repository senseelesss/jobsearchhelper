# Collection and enrichment v2

Verified 2026-11-10, against baseline `4415243`. Application version remains 1.1.0;
this is a feature branch, not a stable release or unattended-production certificate.

## Actual architecture

`searches.validate_search_url` validates saved public URLs. `sources.registry`
registers `JobSourceAdapter` parsers. Parsers return existing `SourceVacancy`
observations in `CollectionBatch`; `collection.collect_search` owns transport,
limits, pagination, redirect checks and error isolation. `ingestion` converts
observations to native `Job` identities and records source history. Gmail remains
an independent, readonly MIME/email path; it does not masquerade as board discovery.
Filtering, scoring, SQLite and Telegram consume normalized fields.

Existing source audit:

- LinkedIn builds `/jobs/search/` URLs, parses public `.base-search-card` HTML and
  native numeric job IDs. Pagination uses the public frontend guest HTML route,
  not an official search API. HTTP or isolated Playwright is available. Full JDs
  use JSON-LD or the description markup container. No account session is used.
- karriere.at builds `/jobs/<query>/<location>` URLs, parses `.m-jobsListItem`,
  company/location/summary fields and numeric native IDs. HTTP uses page parameters;
  Playwright uses the site's load-more control. JSON-LD and job-content containers
  provide detail text. Repeated IDs stop ignored pagination; later-page failures
  preserve earlier jobs. Neither bounded search proves total inventory or expiry.
- Gmail uses the existing readonly OAuth API, sender/date queries and MIME parsing
  for LinkedIn/Indeed alerts. It is bounded separately by email count and elapsed
  time; failures do not cancel direct sources. Email snippets are not full JDs.
  Native board identities survive acquisition through email or direct collection.
- HTTP timeout/rate/retries remain configurable. Exponential transport backoff now
  has explicit bounds and honors Retry-After; longer requested delays return the
  failure without retrying early. No CAPTCHA/login bypass, stealth or proxy pool.

## Source support and verification

| Source | Implemented / offline | This iteration's live check | Full description | Limits |
| --- | --- | --- | --- | --- |
| LinkedIn | Existing, regression suite | Search 2 jobs; detail 1/1 FULL (1,891 chars) | Yes, structured/HTML | Public frontend can block/change; first page only tested live |
| karriere.at | Existing, regression suite | Search 2 jobs; detail 1/1 FULL (4,293 chars) | Yes, structured/HTML | Bounded pagination; live filter accuracy/coverage not certified |
| Gmail / Indeed alerts | Existing, regression suite | No account access in this iteration | Detail adapters retained | OAuth/alert layouts and destination access required; no Indeed direct discovery |
| Absolventen.at | New public RSS parser and fixtures | 2/2 imported; replay 0 inserts; filtered Python/Wien feed valid but empty | Conditional JSON-LD / job_description | Feed recent window, no invented pagination. Sample detail redirected externally and was refused (403); recovery 0/1 |
| Greenhouse | New documented public board feed + posting recovery | Ketryx board: 2/2 supplied FULL, replay 0 inserts | Yes, public JSON content | Only explicitly configured/discovered board tokens; sample first 2 jobs need not be Vienna/relevant |
| Lever | New documented public board feed + posting recovery | Qualysoft board: 2/2 supplied FULL, replay 0 inserts | Yes, description + lists + additional | Explicit board tokens; US/EU public endpoints; employment commitment retained |
| DevJobs.at | Not implemented | Both tested hosts returned 403 | Unverified | No confirmed acceptable public retrieval interface; no bypass attempted |
| StepStone Austria | Not implemented | Search timed out; no successful direct collection | Unverified | Public API access not established; robots restrict public-api/query routes; employer feeds are not search APIs |
| AMS alle jobs | Not implemented | Public app shell returned 200, no job data | Unverified | HR-API is employer submission, not a public retrieval API; no undocumented backend used |
| WeAreDevelopers | Not implemented | HTML/JSON-LD accessible during investigation | Not supported | Terms explicitly prohibit scraping/systematic downloading; no connector enabled |

There is **one new Austrian aggregator**, plus **two public employer ATS feed
integrations**. These are not three newly supported Austrian aggregators. The
remaining requested boards are documented blockers, not placeholder adapters.
Public feed acceptance does not establish relevance, completeness or permissions
for unrelated websites. Terms can change; keep collection for personal review and
respect access restrictions. External redirects during HTTP collection/enrichment
are refused (LinkedIn's supported regional alias is allowed).

Primary references inspected:
[Greenhouse public GET documentation](https://docs.greenhouse.io/job-board.html),
[Lever Postings API](https://github.com/lever/postings-api),
[Absolventen public feed](https://www.absolventen.at/?feed=job_feed),
[Absolventen terms](https://www.absolventen.at/agb),
[Ketryx board](https://job-boards.greenhouse.io/ketryx),
[Qualysoft board](https://jobs.lever.co/qualysoft),
[StepStone robots](https://www.stepstone.at/robots.txt),
[AMS HR-API](https://www.ams.at/unternehmen/service-zur-personalsuche/ams-hr-api),
[WeAreDevelopers website terms](https://www.wearedevelopers.com/legal/website-terms-of-use).
Robots alone is not a license. Source availability and useful Vienna inventory
must be reassessed in a controlled acceptance run.

## Extraction and completeness

Prefer supplied documented public feed/API descriptions; otherwise use one
unambiguous JSON-LD JobPosting, then the source's description container. JSON-LD
supports nested graphs/type lists, MIME parameters, HTML entity decoding and
employment/work-hours metadata. Multiple JobPosting objects are ambiguous and
are not collapsed into one JD. Original language is retained. No LLM reconstructs
missing text. Source expiry (`validThrough`, explicit closure or 404/410) is evidence;
an empty bounded scan is not.

Full means recognized structured/container evidence plus content checks, not an
employer-certified completeness guarantee. Truncation, talent-community boilerplate,
short consent/login/templates, missing containers and HTTP errors are not full.
Length alone does not classify completeness. Requirements/responsibilities remain
in the source text; structured title, employer, hours, employment, location, remote
policy, salary/basis, publication/deadline and skills are retained when supplied.
No missing values are invented. Explicit TELECOMMUTE + Austria applicant-country
metadata supplies deterministic remote evidence. Salary remains metadata and does
not become a working-hours requirement.

Exports add `jd_completeness`: FULL, PARTIAL, MISSING, INACCESSIBLE or EXPIRED.
Existing `fetch_status` values and `jd_status` remain compatible. Before enrichment,
a saved snippet is PARTIAL. A failed refresh can retain FULL content while retry
state separately records the failure. `jd_provenance` exposes original/content URLs,
source method and supplied structured metadata; raw sensitive HTTP headers/cookies
are never persisted or logged by the new reporting.

One explicit, unique Greenhouse/Lever application link may trigger one public
posting GET. It counts against the same enrichment request budget. The normalized
employer title must equal the vacancy title before substituting content. Ambiguous
links, unknown boards, mismatched titles and arbitrary employer URLs are not crawled.
Aggregator URL/native identity/observations remain; provenance associates the
verified employer posting. This does not implement fuzzy cross-source identity
resolution or a general employer crawler.

## Durable lifecycle and data safety

Daily enrichment has an independent queue: a cached successful or failed AI score
cannot remove a JD candidate. Eligible/reviewable incomplete jobs are selected
before old full descriptions, then by attempt age/first seen. Applied/Skipped,
expired and confirmed hard-incompatible jobs keep the existing exclusion policy.

- Default full-JD freshness: 72 hours (`JOB_RADAR_ENRICH_FRESHNESS_HOURS`).
- Base retry delay: 24 hours (`JOB_RADAR_ENRICH_RETRY_HOURS`), doubled after failures
  up to eight times the base. Blocked/expired attempts have at least seven days.
- Daily automatically stops after five consecutive unsuccessful attempts per URL;
  operator `enrich --retry-failed --refresh` can override after resolving the cause.
- Request/time budgets remain shared across alternate sources and employer recovery.
  `--enrich-limit` is a logical GET budget; HTTP transport retries are separately
  bounded by `JOB_RADAR_ENRICH_RETRIES` and reported separately.
- Already fresh full JDs are skipped. Transient failures/truncated refreshes do not
  replace a verified description/hash/cache. Verified expiry retains text but closes
  that source listing. Changed discovery snippets retain text and mark it stale.
- Material description changes alter the content hash. Existing AI cache keys then
  stop matching; re-scoring still needs an explicit positive score budget. Old valid
  cache records are preserved rather than deleted.

New additive table `enrichment_attempts` stores per-URL consecutive failures,
last result/time, next retry, last latency and structured provenance JSON. Migration
uses existing CREATE IF NOT EXISTS startup; no destructive schema rewrite. SQLite
backup API snapshots of five local databases migrated successfully with integrity
checks and exact preservation of pre-existing application/events, scores,
notifications and observations wherever those tables existed. No source database
was opened for writing during this validation.

Native IDs remain canonical. Tracking parameters are stripped by existing supported
URL normalizers / new feed canonical links. Sparse jobs without an employer/title
now keep native semantic identities, including after screening/reopening, to avoid
unsupported cross-source merges. Exact normalized title/company/location grouping
otherwise remains unchanged. It can still split location aliases or overgroup
identically titled separate requisitions; no broad fuzzy merge was introduced.
Existing applied-state/canonical-switch and observation-preservation regressions pass.

Screening now uses the same detailed location evidence as eligibility for explicit
Austria remote roles with another Austrian office city. Country restrictions remain
hard incompatibilities. Unknown hours, salary-on-full-time-basis, DE/EN employment
signals, preferred/mandatory experience and priority behavior remain regression-tested.

## Health and diagnostics

Batch metrics cover requests, successful HTTP responses, HTTP/transport errors,
retries, latency, discovered/native unique counts, invalid cards and JD counts.
Browser metrics count outer transport calls rather than its internal page subresources.
Enrichment reports per-source logical requests/retries, average latency and
completeness; structured log records carry stage/source/status without payloads.
Daily JSON includes completeness totals and `outcome` SUCCESS/PARTIAL_SUCCESS/FAILED;
legacy `status` success/partial and CLI exit behavior remain compatible. Newly failed
or incomplete enrichment and failed refreshes now make the overall run partial.
A deliberate cooldown/budget deferral is recorded as deferred, not a fabricated
request failure. Success does not mean every stored vacancy has a full JD.

Use report.json source error/stop/invalid-card counters and run_stage_history for
retrieval/parsing failures. Use vacancies.json for content, eligibility and provenance.
`enrichment-report --json` includes completeness and durable URL attempt state.
A 200 response with an unknown layout is failed, never a fabricated empty search.
No concurrency/distributed workers were added; SQLite has one existing run lock.

## Measured evidence

Baseline: 254 pytest tests and Ruff passed at `4415243`. After this change:
**294 tests passed, zero failures/skips; Ruff passed**. Existing Telegram, Excel,
backup, migration and source pagination tests remain unchanged.

A deterministic three-vacancy comparison ran the same synthetic descriptions and
three fake cached scores against the archived baseline and the new implementation:

| Measurement | Baseline | v2 |
| --- | --- | --- |
| Extra scoring calls during enrichment | 0 | 0 |
| JD requests after successful provisional caches | 0 | 3 |
| FULL / incomplete after enrichment | 0 / 3 | 3 / 0 |
| Recovery of eligible synthetic cached snippets | 0% | 100% |

This is a regression benchmark, **not a live JD coverage claim**. Local read-only
inventory found jobs.db with 35 success, 24 snippet_only and 55 not fetched (114
source rows); these legacy statuses are not freshly quality-verified and this DB
was not selected/mutated as authoritative. Other local DB snapshots also existed.
The historical 4/34 figure is not the current measured local inventory.

Bounded live validation: five sources × two saved observations = ten source rows;
four FULL supplied by ATS feeds, six incomplete at discovery. One detail per
LinkedIn/karriere/Absolventen recovered two FULL and one inaccessible response.
No comparable pre-change live scan was run; no live uplift percentage is claimed.
All five replay runs inserted zero jobs. Gmail, paid AI, Telegram delivery,
applications, deployment and broad acceptance were not exercised.

## Configuration and safe acceptance commands

`searches.collection-v2.example.yaml` adds public source examples without changing
private saved searches. ATS examples are disabled by default; their identifiers
were confirmed on the linked public employer boards. Enable only wanted boards;
ATS APIs return board snapshots (not a global Austria search). Absolventen accepts
`feed=job_feed`, `search_keywords`, `search_location` public feed query parameters;
its tested Python/Wien query was empty. Use broad relevant variants and inspect yield.

Run from the repository. These paths are disposable acceptance state, independent
of JOB_RADAR_DB and the authoritative DB. The two scoring/delivery commands below
are **operator-only next steps and were not executed during development**.

```bash
# Offline verification (use the interpreter selected by setup).
~/.cache/job-radar/venv/bin/python -m pytest -q
~/.cache/job-radar/venv/bin/python -m ruff check .

# 1. Small public collection, no account email, paid model or Telegram.
./bin/job-radar daily --no-email --transport http \
  --searches searches.collection-v2.example.yaml --pages 1 --limit 3 --details 0 \
  --enrich-limit 3 --score-limit 0 --database data/collection-v2-acceptance.db \
  --output outputs/collection-v2-acceptance

# 2. Replay the identical command; inspect inserted=0 for unchanged inventory.
# New real postings between runs can legitimately increase inserted.
./bin/job-radar daily --no-email --transport http \
  --searches searches.collection-v2.example.yaml --pages 1 --limit 3 --details 0 \
  --enrich-limit 3 --score-limit 0 --database data/collection-v2-acceptance.db \
  --output outputs/collection-v2-acceptance

# 3. Inspect full/partial/missing/inaccessible/expired totals and retry evidence.
~/.cache/job-radar/venv/bin/python -c 'import json; print(json.load(open("outputs/collection-v2-acceptance/report.json"))["jd_completeness"])'
JOB_RADAR_DB=data/collection-v2-acceptance.db ./bin/job-radar enrichment-report --json

# 4. Intentional paid scoring only after private key rotation/profile/model checks.
./bin/job-radar daily --no-email --transport http \
  --searches searches.collection-v2.example.yaml --pages 1 --limit 3 --details 0 \
  --enrich-limit 0 --score-limit 2 --database data/collection-v2-acceptance.db \
  --output outputs/collection-v2-acceptance

# 5. Readonly Telegram configuration check; next command actually sends.
./bin/job-radar telegram-check
./bin/job-radar daily --no-email --transport http \
  --searches searches.collection-v2.example.yaml --pages 1 --limit 3 --details 0 \
  --enrich-limit 0 --score-limit 0 --telegram \
  --database data/collection-v2-acceptance.db --output outputs/collection-v2-acceptance
```

The sample Telegram test intentionally uses disposable delivery history. Do not
mix it with production acceptance state or assume it tests real callback interaction.
Do not run duplicate callback workers. A partial run exits 1 after preserving results.

## Adding a source

First inspect the source's documented public interface, restrictions and useful
Vienna access. Add a parser implementing `JobSourceAdapter.parse_search`, with
native IDs, canonical public URLs, honest timestamps, partial/empty/failed outcomes
and recorded sanitized fixtures. Register it and validate its public search URL.
Declare the actual pagination model; do not invent cursors for RSS/board snapshots.
Reuse structured detail extraction or add an isolated source adapter. Exercise
collection → persistence → replay, malformed/empty/blocked pages, bounds and full-JD
quality offline; then perform a small allowed read-only live check. Mark live
unverified sources honestly and do not add placeholder production registrations.

## Git and publication boundary

Feature branch starts at clean `4415243`, four understood commits ahead of fetched
origin/main (`7e8ee5c`): prior staging/priority/release/audit changes are preserved.
No main update, tag/release, force push, history rewrite or unrelated branch merge.
An active `feat/telegram-control-center-v2` worktree appeared during this task and
has independent uncommitted work; it is untouched and will need a later reviewed
integration (daily.py/cli.py overlap).

Historical OpenAI/Telegram credentials and a private profile exist on old
master/recovery histories outside feature ancestry; those refs must not be pushed.
Rotation/revocation remains an owner task; this iteration does not claim it occurred.
Only reviewed feature ancestry is eligible for publication. Existing commit author
metadata is preserved; new commits use the established repository noreply identity.
