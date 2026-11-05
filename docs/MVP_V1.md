# MVP V1: unattended job radar

Specification update: 2026-11-04, incorporating the user's clarified scope.
**Design specification; runtime implementation and deployment are pending.**
Update: significant runtime features are now implemented; consult
[PROGRESS.md](PROGRESS.md) and [OPERATIONS.md](OPERATIONS.md) for the current boundary.
Production deployment and seven-day unattended acceptance remain pending.

V1 is a working unattended daily service: it collects independently from email
and job sites, preserves vacancies and application history in SQLite, produces an
Excel review workbook, and sends a daily Telegram result. V2 adds more automation
and optional AI-agent actions. An LLM API may already help score or draft in V1;
agent access is not required for collection, persistence or daily reporting.

## Two acquisition paths

1. **Email:** Gmail alerts → existing deterministic email parsers → observations.
2. **Direct:** saved searches/employer boards → API, HTTP or Playwright adapters
   → observations. This path must work with Gmail disconnected and no known
   email-derived URLs. It performs discovery as well as detail fetching.

Run both every day with separate checkpoints, errors, retries and budgets.
Execution may be sequential on a small host; operational independence means one
path's failure cannot cancel the other. Their shared normalizer, database and
host remain common failure points. Two paths do not make one server highly
available. Record channel and source separately: a LinkedIn listing has one native
listing identity whether discovered by email, browser or JobSpy.

## Playwright in V1

Use one shared headless Chromium transport, initially one browser task at a time.
Use source-specific selectors and bounded pagination; extract structured data
when present and visible DOM when needed. API feeds stay useful where available.
Playwright is sufficient as the browser automation layer, but does not itself
provide durable checkpoints, source identity, anti-duplicate rules or application
history. It also cannot guarantee that a site will accept unattended access.

Pin the Python package and compatible browser binaries in a reproducible runtime;
the [official browser documentation](https://playwright.dev/python/docs/browsers)
describes their version coupling and installation. Keep browser state and secrets
outside the repository. Isolate source contexts and set navigation, task and
whole-run deadlines. Close resources on failure. A timeout, auth wall or CAPTCHA
must become `blocked`/`failed`, not an empty successful search.

Keep bounded redacted failure diagnostics; avoid saving account pages or emails
in traces. A browser adapter must have fixtures for extraction plus a bounded
live smoke check for navigation. Browser execution is part of the source layer;
AI is not needed to click through the daily collection flow.

## Database and prior applications

SQLite is the authoritative store on one persistent host, with one writer and
short transactions. Excel, Telegram and exports are views or validated commands
against it. Do not synchronize a live SQLite/WAL file through cloud file sync.

Separate three concepts:

| Concept | Identity and state |
| --- | --- |
| Source listing | Board/tenant/native ID; per-source URL, content, availability and observations. |
| Vacancy | Stable group ID with conservative matching and aliases across sources/reposts. |
| Application | Candidate + vacancy, with current state and an append-only event history. |

Application states: `not_applied`, `saved`, `applied`, `interview`, `offer`,
`rejected`, `withdrawn`, `skipped`. `rejected` here is an application outcome,
separate from deterministic screening rejection. Preserve submission time, source
URL used, notes, event time, actor/channel and idempotency key. Migrating existing
Applied/Skipped callbacks must retain their dates and identities; preserve legacy
application history even if a listing no longer exists.

Check native IDs/canonical employer requisition IDs first, then conservative
cross-source matching. Reposts with a new source ID are not proof of a new opening.
For a strong prior-application match, show the application state and suppress the
ordinary new-opportunity card. Ambiguous matches remain visible as “possibly
already applied” rather than being silently dropped. Company + title alone is
insufficient evidence to merge distinct openings.

An application is marked `applied` only after explicit user confirmation or, in a
future authorized submission flow, verified submission evidence. Opening a link,
generating a letter or sending a Telegram card is not an application. Updates
from Excel and Telegram go through the same validation service and event ledger.

## Excel review and editing

Create one `.xlsx` with two distinct workflows:

- **Vacancies:** generated snapshot, one row per stable vacancy ID, current
  company/title/location, canonical URL and origins, score/confidence, availability,
  application state and revision, first seen and last verified time.
- **Status edits:** explicit editable requests keyed by vacancy ID and the exported
  application revision; requested state, application date, notes, and import result.
  A blank edit row means no action. Deleting a snapshot row never deletes a job.

The initial workbook is an empty template; it contains no invented jobs. Its
import/export automation must still be implemented. Production output may grow
beyond the template's initial visible capacity.

Choose explicit file-based import/export for V1. A server does not see edits in a
local laptop file automatically. Transfer a reviewed workbook to a configured
private inbox (or implement an authenticated upload) and have the scheduled run
import it before selecting jobs. Telegram actions offer immediate status changes
without file transfer. Do not promise live Excel synchronization without such a
transport. Later, a connected spreadsheet/UI can replace this manual file transfer.

Validate workbook schema, stable IDs, duplicate/conflicting edit rows, status and
date types. Compare the expected per-vacancy application revision to current DB
state; reject conflicts explicitly instead of overwriting a newer Telegram update.
Derive or persist an idempotency key for each edit so reimport cannot duplicate
events. A revision mismatch that represents an already accepted edit is a replay,
not a second application. Never delete application history through an import.

Import accepted edits transactionally, record accepted/rejected results and retain
an audit receipt. Archive the input before regenerating the snapshot; do not erase
unprocessed edits. Export via a temporary file and atomic replacement. If a file
is locked or transfer fails, keep the prior workbook, save a versioned replacement
and report the issue. Write untrusted website text as literal spreadsheet values
to prevent leading `=`, `+`, `-`, `@` text from becoming formulas. No macros required.

## Daily operation and Telegram

Deploy to an always-on Linux host/container with persistent disk, not a laptop
that must stay awake. A small VPS is the initial architecture choice; provider,
cost, exact daily time and deployment credentials remain to be selected. Use one
application image and a host scheduler (for example a systemd timer) plus a small
callback worker/service; no distributed queue is required. Store scheduling rules
in `Europe/Vienna` and timestamps in UTC, with a logical daily run identity based
on the Vienna date. Catch up a missed day once without launching overlapping runs.

Daily stages:

1. Acquire run lock, check storage and credentials, import queued status edits.
2. Collect email and direct sources independently, committing bounded batches.
3. Normalize/deduplicate, retain provenance, refresh availability and stale content.
4. Screen with current rules, check previous applications, optionally score with a
   configured budget. Preserve an unscored review queue if the AI provider fails.
5. Export an updated workbook from one consistent DB snapshot.
6. Commit notification intents, then send/retry the daily Telegram digest.
7. Finalize stage outcomes, backup the database and publish a completion heartbeat.

Always produce a daily result, including when there are zero new jobs. Distinguish
“no new jobs; all sources checked” from “results incomplete; source failed”. Include
new useful count, prior-application/duplicate count, top candidates with links,
Excel availability and source health. A missing draft never blocks delivery.
Split/rate-limit long digests. Material updates to already applied roles belong
in a separate update section rather than the new-opportunity queue.

Use `notification_outbox` with a unique digest key (candidate + Vienna day + kind)
and message-part keys. Persist attempts and returned Telegram message IDs. Retry
only unsent/retryable work. Telegram send and a local DB commit cannot be one
transaction: a crash/timeout after remote acceptance can leave delivery unknown.
Record this state, use bounded retries and a visible digest identifier; do not
claim exactly-once delivery. Normal replay of an acknowledged send must not resend.

The callback worker must continuously process Applied/Skip actions or poll often
enough to meet an explicit latency target (initial target: under two minutes while
healthy). A once-daily poll would leave statuses stale and increase duplicate
recommendations. Authenticate chat/user IDs and persist callback offsets/events.

## Failure handling, backups and monitoring

- Source/API/AI/Excel failures yield a partial report while other stages continue.
  Authentication failures show a precise required action without leaking tokens.
- Whole-run timeout, per-source budgets and process cleanup prevent one hung
  browser from blocking tomorrow's run. Record abandoned runs on restart.
- Use SQLite's consistent backup mechanism, not copying only `jobs.db` while WAL
  writes are active. Proposed initial retention: seven daily backups and four
  weekly backups in private storage separate from the host. Test restore.
- Track last successful collection per source, last complete run, last digest,
  pending/unknown delivery, disk usage and backup freshness. Normal successful
  operation needs no user action; credential expiry/blocking may still require it.
- Use an external dead-man heartbeat to detect a crashed/stopped host. The script
  cannot alert from a host that is down; Telegram-only in-process monitoring is
  insufficient. Monitoring failure must have an independent notification route.
- Configure secrets at runtime. Keep Gmail tokens, Telegram credentials, browser
  sessions, DB, exports and logs private and outside Git. Validate unattended token
  refresh and account configuration on the actual deployment before acceptance.

## V1 acceptance and V2 boundary

V1 acceptance requires seven consecutive scheduled days without manual starts,
with both acquisition paths attempted, daily result delivery and durable recovery.
Include controlled tests for one source unavailable, Gmail unavailable, AI failure,
browser timeout, locked Excel output, conflicting/repeated status import, callback
replay, crash/restart and database restore. Verify no duplicate source rows or
application events and no loss of manual statuses after re-export. A controlled
failure day may be partial, but must be reported accurately and recover.

V2 may add agent tools for research, shortlist reasoning, tailored materials and
more application assistance. Give an agent scoped domain commands with structured
results and the same idempotency/audit rules. Do not grant unrestricted SQL or
browser-session access by default. Vacancy descriptions are untrusted input, not
instructions to the agent. Auto-submit is a separate authorized capability and
must recheck application history immediately before submitting.
