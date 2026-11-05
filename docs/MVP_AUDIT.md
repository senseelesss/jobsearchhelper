# MVP audit — 2026-11-04

Scope: quick code and architecture review of the current working tree, including
pre-existing uncommitted changes. This is not an exhaustive security audit or a
live ingestion benchmark. No personal database, email, credentials, paid API, or
delivery service was used. This change updates the design; runtime fixes and new
connectors below remain implementation work.

## Verified baseline

- `.venv/bin/python -m pytest -q`: **89 passed**.
- `.venv/bin/ruff check .`: **passed**.
- Actual flow: Gmail → source email parsers → SQLite source upserts → deterministic
  screening → semantic grouping → HTTP enrichment → cached AI scoring → cached
  drafts → Telegram review actions.
- Keep: source IDs, original source rows, broad review funnel, separate scoring and
  drafting, content/profile/model cache keys, additive migrations, WAL, run lock,
  stage reports, fixtures, and transport injection in tests.
- `AGENTS.md` and the README's suggested first task were behind the implementation.

## Findings, in implementation priority order

| Priority | Finding and code evidence | Consequence | Required change |
| --- | --- | --- | --- |
| P1 | `runner._active_semantic_keys`, `matching.scoring_candidates`, and `matching.application_queue` treat **any** expired group member as group expiry. | A closed LinkedIn copy suppresses an active Indeed/employer listing. | Track listing state per source; prefer a recently verified active source; expire a group only when no active/unknown viable member remains. Use the same policy for queue, scoring, and delivery. |
| P1 | `enrichment.enrich_jobs` immediately reuses successful descriptions; successful URL cache entries have no TTL. | Changed requirements and removed vacancies can stay current indefinitely; `retry_failed=True` cannot refresh successful data. | Add freshness and explicit refresh, revalidate with conditional HTTP where available, and invalidate derived scores only if content changes. |
| P1 | `runner.run_pipeline` screens only newly inserted/changed rows. `storage.screen_keys` stores a rules version but the runner does not compare it to the current version. | Deploying updated rules leaves unchanged vacancies classified under old rules; a failed screen on an unchanged row is not automatically retried. | Select missing/failed/outdated screening independently of source changes. |
| P1 | `Job` requires email metadata; `RunDependencies.fetch_messages`, CLI query construction, parser dispatch, and enrichment dispatch assume two email sources. | Every new API/board requires changes across layers; no per-query cursor or isolated source outcome exists. | Introduce source-neutral observations and connector batches before adding several sites. |
| P2 | `models.semantic_key` hashes normalized title/company/location; `_canonical_rank` uses directness, email date, and snippet length. | `Wien` and `Vienna` do not group; identical titles can over-group separate openings; canonical selection ignores description quality and active state. Scores/statuses depend on an identity that changes with metadata. | Preserve source identity, add conservative matching evidence and stable group IDs; rank by availability, description quality, original employer URL, then freshness. |
| P2 | `runner` and `pipeline.extract_jobs` use `setdefault` for duplicate source keys. | The first alert wins inside a batch, even when a later observation has better/newer fields. | Retain observations or choose by parsed timestamps and field quality; never overwrite good values with missing data. |
| P2 | `list(fetch_messages(...))` completes before ingestion is persisted. | A late Gmail fetch error discards already fetched messages for that run; a fixed two-day window can miss mail after a longer interruption. | Persist page batches and checkpoints after durable writes; use an overlap window and explicit backfill. |
| P2 | `HttpClient.get` uses fixed backoff without `Retry-After`; runner retries failed enrichment each run. | Blocked sources consume repeated requests without durable cooldown or query budgets. | Add host cooldown, typed failures, response-size/page/time budgets, and retry scheduling. |
| P2 | `telegram_candidates` requires a ready draft. | A valid scored opportunity never reaches the daily review if optional drafting fails. | Deliver score cards independently; generate/show drafts on demand. |
| P2 | `storage.screen_keys` runs screening and computes identity; CLI repeats score-selection logic. Runner repeatedly loads/groups the full dataset per semantic key. | Layer boundaries drift and run cost grows roughly quadratically for several stages. | Move policy into domain services; select/group once per run and persist results through storage-only methods. |

## Reproduced independently of existing tests

Temporary SQLite database, synthetic vacancies, no network calls:

1. Insert two identically titled/company/location source listings, screen them,
   mark LinkedIn expired and Indeed successful. Both `scoring_candidates` and
   `_active_semantic_keys` return zero despite the active Indeed row.
2. Set a successful description's `fetched_at` to `2020-01-01`, run enrichment with
   `retry_failed=True`: zero HTTP calls. The old successful description is reused.
3. Compare semantic hashes for identical title/company with `Wien` versus
   `Vienna`: hashes differ.

The passing suite is a useful baseline, but does not validate the proposed new
behaviors. Add regression tests for each runtime fix when implementing it.

## Next milestone

Implement source-neutral collection plus **one** new source, with the P1 lifecycle
and screening fixes. See [architecture](ARCHITECTURE.md) for migration steps and
[source research](SOURCES.md) for connector choices and acceptance criteria.
The clarified [V1 operating contract](MVP_V1.md) also requires independent email
and direct paths, Playwright, an application ledger, Excel control, and unattended
daily Telegram operation. Those features remain pending implementation.

Avoid a wholesale rewrite: the existing scoring, draft, export, SQLite, and
Telegram code can remain behind compatibility boundaries during migration.
