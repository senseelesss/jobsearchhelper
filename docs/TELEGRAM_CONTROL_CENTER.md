# Telegram Control Center v2

> Historical feature-branch guide. The integrated RC now runs bounded enrichment
> in Quick Search and adds confirmed budgeted Full Search plus durable completion
> notifications. See [current startup and controls](../README.md) and the
> [RC runbook](RELEASE_CANDIDATE_V2.md) for current behavior and release gates.


Implemented on `feat/telegram-control-center-v2`, based on committed checkpoint
`4415243`. Collection Engine v2's checkout, private `.env` and authoritative DB
were not used for development. All validation here uses temporary databases,
fake Telegram and fake collection/provider transports. No live messages, site
collection or paid requests were authorized or performed.

## Verified scope

| Capability | Classification | Evidence / boundary |
| --- | --- | --- |
| Dashboard, commands, inline navigation and editing | IMPLEMENTED AND TESTED | Fake Telegram, actual temporary SQLite |
| Full database browsing, sort/filter, stable pagination | IMPLEMENTED AND TESTED | Bounded fetches and disk-backed snapshots; not limited to digest contents |
| Save/applied/interview/offer/rejected/withdrawn/skipped/reset | IMPLEMENTED AND TESTED | Existing ledger, request IDs and optimistic revision checks |
| Ukrainian/default, German, English and persisted language | IMPLEMENTED AND TESTED | Catalog parity, routing and localized digest tests |
| Existing AI scores/cost estimates and stored drafts | IMPLEMENTED AND TESTED | Current cache identity respected; historical scores explicitly labelled |
| New paid individual AI scoring | PARTIALLY IMPLEMENTED | UI explains why unavailable; no budget-safe individual service exists |
| Search gateway, lock transfer, timeout and recovery | IMPLEMENTED AND TESTED | Actual supervisor subprocesses; gateway → existing CLI with fixture collection |
| Real Telegram polling and live source access | IMPLEMENTED BUT LIVE-UNVERIFIED | Requires owner credentials and explicit network validation |
| Localized daily outbox, dedupe and notification preference | IMPLEMENTED AND TESTED | Actual daily orchestration with fake transport, restart/day replay tests |
| Ubuntu units and unattended operation | IMPLEMENTED BUT LIVE-UNVERIFIED | Examples updated; neither installed nor enabled |

A mocked collector does not verify today's source availability, login walls,
browser installation, credentials, Telegram delivery or Linux runtime.

## Commands and navigation

`/start` and `/menu` open Home; `/help` explains operation; `/cancel` returns Home
without cancelling a collection that has already started. No application is
submitted automatically. Opening a vacancy does not mark it applied.

```text
Home
 ├ Find Jobs → saved profile/source + page limit → Find New Jobs → Run Status
 │                                                     └ Refresh → results
 ├ Recommendations / Saved / All / default list
 │   ├ New / Recommended / Saved / Applied / Skipped / All
 │   ├ Sort: newest / application priority / current AI score
 │   ├ Filters: priority / source / work mode / hours category / status
 │   └ Job details → URL / save / applied / skip / lifecycle states
 │                 → previous / next / stored draft / AI availability explanation
 ├ Applications → job details and status changes
 ├ Statistics → actual unique-job counts, sources, priorities, states, recent runs
 └ Settings → UA / DE / EN, notifications, default list, search profile/source/depth
```

All displayed buttons route to actions; the AI button opens an availability
explanation and never spends money. Status changes are immediate, persistent
and idempotent per button. Old buttons cannot overwrite a newer ledger revision.
Invalid/deleted jobs and changed semantic identities are rejected. Old direct
digest actions require a matching persisted alert identity and an unacted/saved
state; otherwise reopen the job through the menu before changing its status. Vacancy titles,
companies, descriptions and employer evidence stay in their original language.
Descriptions and existing drafts are truncated safely to Telegram's UTF-16 limit.
Only HTTP(S) links without embedded credentials are rendered. Messages use plain
text: no HTML/Markdown interpretation of third-party text.

The dashboard distinguishes unique unacted jobs, unread recommendations, saved
jobs, applications, last successful pipeline and last pipeline status. A read
marker only means the owner opened that semantic vacancy. Statistics show 24h/7d
windows, not invented calendar-day metrics. Hours, work mode, priority and JD
completeness are explicitly assessments; absent hours remain unknown. Work mode
currently uses explicit location wording; missing evidence never means on-site.
The application timestamp is the most recent persisted Applied event, while the
last status-change timestamp is displayed separately when present.

## Architecture and persistence

No new Telegram framework or runtime dependency was added.

- `control/runtime.py`: async long polling, off-loop serialized DB/UI execution,
  durable update offset, retry backoff and one polling instance per bot on this host.
- `control/ui.py`, `catalog.py`: owner authorization, callback routing, plain-text
  views, complete translation catalogs and fallback to English/unknown keys.
- `control/jobs.py`: repository adapter, existing eligibility/scoring-cache helpers,
  bounded index refresh (100 metadata rows at a time), indexed snapshots and counts.
- `control/search.py`, `worker.py`: typed operator limits, validated saved profiles,
  detached subprocess supervisor and existing `daily` orchestration.
- `control/state.py`: private sidecar SQLite, independent from collection schema.
- `control/notifications.py`: opt-in localization of existing daily/legacy sends.
- `control/demo.py`: entirely offline interactive terminal and scripted walkthrough.

`<database>.telegram.sqlite3` contains preferences, expiring opaque callback tokens,
a disposable derived job index and disk-backed ordered snapshot IDs. This is
private data: it also caches descriptions and scores. Keep it next to the DB with
private permissions; include it in a separate consistent SQLite backup if you
want to preserve preferences and active buttons. Existing DB backups cover the
primary DB only. Losing the sidecar loses preferences/buttons/read markers, never
application events or collection data. Do not copy live WAL files individually.

The main schema and `storage.py` are unchanged. Application mutations go through
`JobRepository.set_application`, preserving native IDs, revisions, Excel interchange
and request replay. Current score identity includes candidate, description, prompt
and model. Historical scores are shown as possibly outdated and cannot promote a
recommendation. Failed/skip scores and hard rejects stay out of recommendations.
When the current context is missing, only records without historical scores enter
the explicitly unscored rule-based queue. Draft reads never call a provider.

The SQL adapter reads the currently committed tables directly because the existing
repository has no paginated query API. SQL stays in this adapter, never in views.
Index refresh checks job, score, application and legacy-delivery timestamps plus
profile/model/policy/date identity. It recomputes only changed rows and fetches
metadata in batches. Counts are cached for 15 seconds. New snapshots rank canonical
semantic groups (active/full/recent preferred); list membership/order is frozen on
disk for 24 hours. Details are always read live. Deleted jobs show unavailable.
A refreshed list sees new results/statuses; an existing snapshot retains its order.
Initial indexing is proportional to database size and consumes disk, not an
unbounded Python list. The existing daily pipeline still has its original in-memory
review/export behavior; this feature does not rewrite collection internals.

Callback data is `cc:` plus a random 16-character opaque token. The immutable
server-side action retains the native identity and expected application revision.
Expired callbacks/snapshots are pruned during authorized activity; replay outcome
records expire with their callback. One user's private bot uses one owner preference
set. Inline-forwarded messages, group chats and other users are not authorized.

## Search execution and failure recovery

The button acknowledges the callback before starting work. The gateway reserves a
DB-specific OS `flock`, persists a queued request, snapshots the selected validated
search profiles into a private per-request output directory and starts a detached
supervisor. The supervisor inherits the lock file descriptor. Closing the parent's
copy does not unlock the child. `flock` ownership disappears on process exit, so
there is no permanent lock file to delete after crashes.

The actual argument vector runs the existing CLI, equivalent to:

```text
python -m job_radar.cli daily --database <explicit-db>
  --searches <private-selected-profile-snapshot> --output <unique-run-directory>
  --transport playwright --pages 1 --limit 15 --details 0 --enrich-limit 0
  --score-limit 0 --no-email
```

No shell is involved. Telegram cannot supply URLs, file paths, arbitrary flags or
credentials. Default limits are 15 listings per profile, one page selected,
maximum two selectable pages, 600 seconds overall and 300 seconds between starts.
Operator flags below can change these within conservative validation bounds.
Interactive searches intentionally omit Gmail, detail enrichment, AI and digest
sending; the established scheduled daily configuration remains independent.
Sources/profiles come from `load_searches`, without a fixed source list in the UI.
Selection namespaces separate profile names from source names.

The existing `.run.lock` prevents overlapping actual pipelines, including scheduled
CLI runs. A race with the scheduler fails safely at that lock. The supervisor
runs outside polling, suppresses raw CLI output and reads the uniquely addressed
persisted report plus its exact `run_history` record. Exit 1 with a valid partial
report is partial success. Missing reports, spawn errors and worker errors are
failures; timeout terminates the CLI process group. Run status exposes completed
stages where stored, elapsed supervisor duration and actual counters. `shortlisted`
is the pipeline's actionable result count across the database, not a fabricated
number of newly relevant jobs. Fields unavailable from current reports are omitted.
Results use before/after native row-ID bounds to avoid accidentally including older
same-second jobs; existing deduplicated jobs remain in Recommendations/All.

Repeated presses of the same start button only revisit its recorded request;
newly rendered start buttons still obey the global lock and cooldown. Bot restarts
leave the detached worker alive. Refresh Status shows the result; no unsolicited
completion push or background Telegram send is added. If the worker disappears
and both worker/pipeline locks are free, the UI marks the run interrupted.
A SIGKILL of the supervisor can leave its CLI child finishing the existing bounded
pipeline; `.run.lock` still prevents overlap. A next locked daily run marks old
primary `run_history` rows interrupted. Never manually remove an active lock file
or clear outbox records to retry. Ordinary timeout/TERM is handled by the supervisor.

Telegram timeouts trigger bounded exponential polling backoff up to 30 seconds.
SQLite uses the repository/sidecar busy timeouts; failed UI actions show a safe
localized retry message. A definite edit rejection permits one replacement
message; an ambiguous network failure does not blindly resend. An unchanged edit
is harmless. Logs use event names and exception types, never raw responses,
credentials or private vacancy/profile text. Exactly-once remote delivery is not
claimed; the original durable pending/unknown outbox policy remains authoritative.

## Notifications and AI integration boundaries

Before Control Center has created a sidecar, legacy notification templates behave
as before. Starting the center persists Ukrainian as the default locale. Future
daily digests then use that language, open the precise vacancy via an opaque token,
and provide recommendations/backlog navigation. The original reservation of native
and semantic identities, immutable payload, pending/sent/unknown transitions and
duplicate prevention remain. `item_count` explicitly excludes menu rows and old
payloads remain readable. Merely browsing never writes delivery records.

Disabling notifications suppresses future `daily --telegram` and `telegram-send`
sends without reserving backlog items. Existing immutable pending payloads retain
their original language/content when resumed. Frequency is controlled by the
existing scheduler, not an invented bot frequency setting. Explicit connection
test commands remain operator actions.

The current `score_jobs` API has cache/attempt bounds and records estimated usage,
but no safe total-USD reservation and single-job spend confirmation contract.
Telegram therefore displays current/historical cache values and recorded estimated
costs, and explains why a paid request is disabled. There is no confirmation that
can spend money. To enable paid scoring later, provide an injected gateway that
returns a quote (job identity, candidate/model/description, maximum USD, expiry),
atomically reserves global/per-run budget, confirms an opaque one-shot request,
rechecks identity/cache, invokes existing scoring, and records/reconciles actual
usage and ambiguous provider failures. Do not call a provider directly from a
handler or treat an estimated price as a hard spend cap.

## Safe offline validation

Use the interpreter printed by setup; the examples below use the existing cache
runtime and select this worktree's source explicitly. They do not require `.env`,
a populated candidate profile, Gmail, a bot token or an LLM key.

```bash
cd '/Users/senseelesss/Documents/AI assisted job search speedrun/job-radar-telegram'
export PYTHONPATH="$PWD/src"
PYTHON="$HOME/.cache/job-radar/venv/bin/python"
"$PYTHON" -m pytest -q
"$PYTHON" -m ruff check .
"$PYTHON" -m job_radar.control.demo --scenario success --walkthrough
"$PYTHON" -m job_radar.control.demo --scenario partial --walkthrough
"$PYTHON" -m job_radar.control.demo --scenario failed --walkthrough
"$PYTHON" -m job_radar.control.demo --scenario running --walkthrough
# Optional manual terminal interaction; q exits and removes its temporary DB:
"$PYTHON" -m job_radar.control.demo --scenario success
```

The scripted journey is Start → Dashboard → Find Jobs → Run Status → Results →
Detail → Save → Applied → Statistics → Settings → German. All three locale choices
and all application states have automated coverage. Existing tests now load the
public example candidate profile instead of an ignored personal file. The detached
CLI integration test replaces only collection transport in the child process;
regular tests never collect live, send Telegram messages or invoke paid scoring.

## Opt-in live testing (not run during implementation)

Prerequisites: dedicated test bot with no other active poller, numeric owner ID,
matching private chat ID, runtime dependencies and Chromium for Playwright. Supply
credentials through the operator's private environment; do not paste them into
Git or edit the other worktree's `.env`. Configure a separate DB and output path.

```bash
cd '/Users/senseelesss/Documents/AI assisted job search speedrun/job-radar-telegram'
# Enter the TEST bot token privately without echoing it in the terminal:
read -rs 'TELEGRAM_BOT_TOKEN?Test bot token: '; export TELEGRAM_BOT_TOKEN
export TELEGRAM_OWNER_ID=123456789  # replace with your actual numeric ID
export TELEGRAM_CHAT_ID="$TELEGRAM_OWNER_ID"
export JOB_RADAR_CANDIDATE_PROFILE="$PWD/candidate_profile.example.yaml"
./bin/job-radar telegram-poll --database data/telegram-live-test.db \
  --searches searches.example.yaml --output outputs/telegram-live-test \
  --max-pages 1 --search-limit 5 --search-timeout 180 --cooldown 300
```

The `read` syntax above is zsh (macOS). In bash use
`read -rsp 'Test bot token: ' TELEGRAM_BOT_TOKEN; export TELEGRAM_BOT_TOKEN`.
Use your reviewed saved-search file if the example profile is not appropriate.
Starting this command polls Telegram and responds to owner commands. Pressing
Find New Jobs makes real public-site network requests through `daily`; it makes
no paid AI requests and sends no digest. This is an opt-in live test, not part of
the offline walkthrough. Test the complete journey, duplicate start presses,
restart during collection, stale buttons, language persistence and unauthorized
user rejection. Confirm the unique output report and application events in the
isolated DB. Stop with Ctrl-C; an already running detached search may continue
until its configured deadline. Do not run the laptop and Ubuntu pollers together.

## Ubuntu and Collection Engine v2 integration

`deploy/job-radar-callbacks.service` is an example, not an installed service. Its
search file/output use `/etc/job-radar-searches.yaml` and
`/var/lib/job-radar/telegram-output`; supply `TELEGRAM_OWNER_ID` in the private
EnvironmentFile. Keep the writable DB/sidecar/output under the same service user
and mode 0700 directories/0600 private files. `KillMode=control-group` stops workers
on service stop/restart; their persisted state is recovered as interrupted.
Install Chromium/dependencies on Ubuntu 24.04 and run the existing production
runbook's manual/systemd checks before enabling anything. Per-bot locking is local
to the host; two different hosts still must not poll the same bot. Network shared
SQLite/lock filesystems are unsupported. This work does not prove Ubuntu or seven
days of unattended acceptance.

For integration, merge the completed branches through a reviewed integration PR;
do not cherry-pick the collection session's unfinished work. The only collection
orchestration overlap is the small notification hook in `daily._deliver` and the
additive `item_count` payload field. Preserve v2's collection/enrichment changes
when resolving that function. This branch does not edit `collection.py`,
`enrichment*`, `sources/*`, screening, normalization, ingestion or `storage.py`.

The stable contracts consumed here are:

1. `load_searches(Path) -> list[SavedSearch]` with `name`, `source`, `url`.
2. Existing `daily` CLI flags, per-DB `.run.lock`, unique output `report.json` with
   `run_id`, and `run_history.counts_json`/`run_stage_history`.
3. `JobRepository` connection/table fields, application methods and score/draft cache
   APIs. Extend `control/jobs.py` if v2 changes canonicalization or introduces a
   supported paginated query service; replace the local SQL adapter when available.
4. Existing eligibility/work-hours policy and effective-description score identity.
5. Additional source names remain plain data. Unknown JD states conservatively
   display incomplete unless existing completeness helpers recognize them. Extra
   run metrics can be added in the supervisor's summary adapter without collectors
   leaking into Telegram handlers.

No shared schema migration is needed. Run the complete test suite after merging,
including localized outbox replay, cross-source application state, snapshots,
recovery and detached CLI integration. Live source and Telegram acceptance remain
separate explicit tests.

## Validation checkpoint — 2026-11-10

Python 3.14 cached development runtime: **341 passed, 0 failed, 0 skipped**;
`python -m ruff check .`: **All checks passed**. This comprises the existing 254
cases and 87 new Control Center cases. All four terminal walkthrough scenarios
also pass inside the suite. `git diff --check`, private-path checks of unpublished
base commits and a tracked/new-file scan for common token/private-key patterns
passed. Pattern scans do not certify arbitrary secret absence. No production DB
or shared environment file was opened or modified for validation.
