# macOS staging optimization — 2026-11-05

Historical staging checkpoint. Current eligibility, scoring fairness and priority
are described in [APPLICATION_PRIORITY.md](APPLICATION_PRIORITY.md).

Base: `7e8ee5ccf3ac17a7b5e19e8decf3f44168cda5bb`. Local-only optimization;
no deployment, paid request or real Telegram message was made in this checkpoint.
SQLite schema, native/semantic identities, callbacks and immutable outbox remain.
Verification: **195 tests passed**, including 21 new staging cases; Ruff and shell
syntax checks passed. The publishable copy also passes using the blank profile
template, with network connections blocked in the main pytest process.
The earlier production audit is historical evidence; the review policy below
supersedes its AI-only shortlist description.

## Code-level findings and targeted changes

1. `daily_cycle` previously required a successful score whenever score_limit>0;
   an unavailable provider/deferred budget could erase the whole Telegram queue.
   `review_tiers` now builds independent queues for any scoring budget:
   - A: current successful apply/strong_apply or review>=50; actual recommendation,
     score and confidence, provisional label when applicable, plus hours/JD state.
   - B: actionable deterministic eligible/review candidates with no current score,
     including budget-deferred jobs. Label `UNSCORED`; score/confidence stay null.
   Failed scores, AI skip/low-review results, hard rejections, expired listings and
   Applied/Skipped states are not promoted by disabling scoring or losing a key.
   Tier A and B alternate inside the existing eight-item/3,800 UTF-16 message
   budget. Previously alerted items are not sent again after later AI evaluation.
2. `collect_search(details=N)` enriches the first search cards before screening;
   Gmail only provides alert text, and daily previously skipped `enrich_jobs`.
   Daily now defaults to details=0 and a **global** enrich-limit=5. It reuses the
   existing enrichment/cache/rate-limit/timeout implementation for eligible,
   actionable missing-JD candidates, then re-screens requirements before scoring.
   Fresh failed/snippet attempts have a 24h cooldown. Current full JDs are not
   refreshed; cached scores do not cause historical bulk refresh. Existing full
   descriptions supplied by direct collection are reused. A failure preserves
   honest snippet/missing state and provisional/low-confidence scoring.
   Legacy success records lacking description-confidence evidence are not
   silently upgraded to verified content; they require a real detail result.
3. `score_jobs` formerly put caches/failures last, but sliced them into the same
   candidate limit and otherwise used database order. `scoring_plan` now removes
   cache hits and cooling failures from call slots, then prefers new actionable
   jobs, part-time evidence, explicit 20–25h-compatible weekly ranges, full JDs,
   and recency. To prevent starvation, queues of >=2 reserve one slot for the
   oldest day-old backlog; limit=1 reserves it every third run. Seven-day aging
   takes precedence and drains old items first. Native first-seen dates survive
   canonical-source changes. The limit still bounds actual scoring attempts.
4. Daily now resolves cache identity against the current profile/model/prompt and
   effective description. An older valid result is not hidden by a newer result
   for a different model. CLI loads local profile/model context even at budget 0
   or without an API key. No OpenAI client is constructed without a key.

`hours_status` remains independent of technical scoring. `hours_target_fit` is
confirmed only for explicit weekly ranges containing a value in 20–25h; generic
Teilzeit/part-time alone stays unknown. Outside-target part-time hours remain
flagged for human review rather than silently changing technical fit. This is
text evidence, not verification of a working-hours contract. Bare "20h" without a
weekly unit is not treated as confirmed weekly availability.

## Description coverage actually observed

Readonly snapshot: `outputs/production-launch-smoke/jobs.db`, 34 source rows and
34 exact semantic groups from the launch validation.

| Acquisition evidence | Full JD | Incomplete/missing | Total |
| --- | ---: | ---: | ---: |
| Email-backed | 0 | 24 | 24 |
| Direct-only | 4 | 6 | 10 |
| Total | 4 | 30 | 34 |

These are saved-state observations, not newly fetched website checks. Offline
fixtures verified Gmail and direct enrichment before scoring, cache reuse,
full-JD hard rejection before paid scoring, failed-fetch provisional fallback,
and request-budget deferral without poisoning unattempted records. No live JD
coverage improvement is claimed until an approved collection/enrichment run.

## Before/after validation on isolated copies

Each scenario started from a separate consistent copy of that same DB. The old
implementation was loaded from the base commit; the new implementation used the
working source. The same private profile/model context and synthetic chat were
used. The fake provider always returned apply/80; the two existing real cached
scores were review/45 and review/42 (below the Tier A threshold) and remained cached. Socket connections were disabled for the
comparison. Fake evaluations are not real recommendations or incurred costs.

| Scoring setting | Before review queue | After A | After B | After total | After mock delivered |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 | 22 | 0 | 20 | 20 | 8 |
| 2 | 2 | 2 | 18 | 20 | 8 |
| 5 | 5 | 5 | 15 | 20 | 8 |
| Missing provider, budget 2 | 0 | 0 | 20 | 20 | 8 |

The budget-0 queue shrinks by two because existing review scores below 50 remain outside Tier A.
There are 19 AI-eligible jobs; Tier B also retains three deterministic review
roles. Eight is a message limit, not a promise to notify all 20 immediately.
The other 12 remained pending for the synthetic chat after mock delivery.

Example new funnel at budget 2: active=34; eligible=19; current scored=4 (2 existing
+2 fake); eligible unscored=15; failed/retry-deferred=0; A=2; B=18; mock delivered
this run=8; synthetic-chat pending=12; full/incomplete JDs=4/30.

Private artifacts: `outputs/staging-optimization/comparison.json` and per-scenario
summaries/reports/workbooks. They are ignored and must **never be deployed as live
state**: they contain fake scores, mock deliveries and validation-only actions.
The original source DB's jobs, scores, applications/events and outbox/alert tables
were compared before/after and stayed unchanged. Applied/Skipped states persisted
after reopening an isolated copy and replay added no events. Existing regressions
also cover callback/outbox acknowledgment, unknown sends and native deduplication.

## Funnel interpretation

`report.json` and SQLite run_history now include `funnel`:

- `database_totals.unique_active`: current non-expired exact semantic groups,
  including deterministic rejections. Unknown availability is not verified active.
- `eligible_for_ai`: actionable eligible groups after hours/application checks.
- `current_scored`: current-context successful scores across active groups;
  `eligible_current_scored` is its eligible/actionable subset.
- `unscored`, `failed`, `retry_deferred`: the eligible population; retry-deferred
  is the subset of failures inside the 24h cooldown. Eligible partitions into
  eligible_current_scored + unscored + failed.
- `ai_recommended` / `rule_based_review`: A/B queue sizes before notification
  deduplication; B also includes deterministic review rows.
- `confirmed_delivered`: known acknowledged vacancy identities for the selected
  chat, including prior runs. Unknown sends are held separately, not confirmed.
- `pending_notification_backlog`: current A/B candidates neither acknowledged nor
  held by an unknown send for this chat. It is not a count of outbox records.
- `full_jd_active` / `incomplete_jd_active`: full-content coverage, not AI confidence.
- `stored_scoring_cost_usd`: sum of stored score-row costs across contexts; cache
  upserts and missing failure usage mean this is not an invoice/lifetime billing ledger.
- `this_run`: observations, inserts/changes, successful/failed scoring attempts,
  actually acknowledged item/digest counts and reported-usage API cost for this run.

`preview_items`/`preview_tiers` describe the newly rendered preview. The immutable
same-day outbox may contain an earlier payload. `alert_items` counts only an
acknowledged send **this run**. A same-day rerun delivers zero even if its preview
contains new backlog. Do not clear acknowledged outbox rows to force an update.

## Six complementary search profiles

The prepared `deploy/searches.vienna.example.yaml` uses four LinkedIn and two
karriere.at searches. Only existing connectors are used. All URLs pass the local
validator; six is below its maximum eight. The websites' current filter/Boolean
semantics and inventory were not live-tested in this offline pass.

| Profile | Role/language coverage | Reason for separate query |
| --- | --- | --- |
| python-wien-de | Python/backend in Wien, German local board | Broad local coverage without depending on junior in title |
| junior-backend-en | junior backend / junior Python, Vienna, LinkedIn part-time | English junior terminology and explicit part-time filter |
| werkstudent-working-student | Werkstudent / working student + software | Student titles; no mandatory part-time filter because listings may omit that tag |
| automation-integration-de-en | Automation / Integration / Automatisierung + software, Vienna | Explicit integration keywords alongside automation; rules retain uncertain IT roles for review |
| qa-testautomatisierung-de | Testautomatisierung, Wien | German QA automation titles missed by Python/backend queries |
| junior-data-ai-en | Data Engineer / AI Engineer, LinkedIn entry-level + part-time | Data/AI adjacency without broad senior data-science scraping |

LinkedIn retains f_E=1,2, Vienna location and a seven-day window; f_JT=P is used
where appropriate. No undocumented karriere hours filter was invented. Paste a
site-generated filter URL if you want a narrower German query. Source IDs dedupe
overlap in storage, but overlapping searches still cost requests. Compare useful
new yield after a few days and disable a low-yield profile rather than adding more.

Prepared settings: pages=1, limit=15/search, details=0, enrich-limit=5 globally,
score-limit=5 globally. The existing systemd timeout stays 25 minutes; no timer
was enabled. Configure JOB_RADAR_ENRICH_LIMIT through the prepared env file.

## Cost and runtime impact

No real LLM spend or Telegram delivery occurred in this pass. At the **configured**
$0.20/input-million and $1.25/output-million, an illustrative 1,000 input +300
output tokens costs $0.000575/job: two=$0.00115, five=$0.002875. Those are the fake
usage estimates recorded in validation, not live full-JD price measurements.
Real profile/JD size changes tokens; existing 48k input-byte, 1,200 output-token,
30s and retry limits remain. No draft generation was added.

Compared with the prior default of five unscreened detail pages **per search**,
daily now spends at most five enrichment GET operations globally; existing HTTP
retry configuration still bounds wire attempts for each GET. Fully cached/no-new-
input runs can make zero JD/scoring calls. Missing full JDs that cannot be fetched
stay provisional; no all-history refresh loop was introduced.

Six one-page profiles imply six search navigations instead of two. Nominal stage
budgets: up to 6x120s for sources +120s Gmail +120s enrichment +5x30s scoring =
1,110s before in-flight request overrun, launch overhead and exports. The final
service timeout remains the hard stop on Ubuntu; actual host runtime needs a
smoke check. Offline copied-DB cycles took about 0.07–0.08s after imports; this is
not a live scraping benchmark.

## Later-today macOS → Ubuntu checklist

1. Select the **authoritative DB actually used by daily and callbacks**, not any
   staging copy or fake-result artifact. Stop laptop writers/callback workers.
2. Take a consistent SQLite backup (SQLite backup API or `.backup`), preserving
   applications/events, job scores, outbox, alert identities and callback offsets.
   Do not copy only the main live WAL file. Run quick_check and record row counts.
3. Transfer that backup plus the real private profile, working OAuth client/token
   and bot/API environment through private SSH. Use absolute target paths,
   jobradar ownership and 0600 private files. Preserve the existing bot/chat.
4. Make this reviewed local code commit available to the host by an approved
   transfer/publication first; do not assume origin/main contains it yet.
   On the approved Ubuntu host, follow PRODUCTION_RUNBOOK.md: install venv and
   matching Chromium, validate units/calendar, restore the DB and compare state
   counts. Do not run a second laptop/host callback poller with the same bot.
5. Start with a manual no-scoring/no-Telegram verification; real paid scoring or
   notification requires explicit approval. Check old acknowledged outbox state
   before enabling a real daily cycle and then the timer. Complete reboot,
   callback and backup/restore checks. No remote infrastructure was changed here.
