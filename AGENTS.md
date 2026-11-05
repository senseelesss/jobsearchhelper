# AGENTS.md

## Mission

Build a reliable job search pipeline for a Computer Science student in Vienna.
The implemented flow includes Gmail ingestion, deterministic extraction, screening,
enrichment, cached AI scoring, drafts, Telegram review, storage, and export.
The next milestone is reliable multi-source collection. Read `docs/MVP_AUDIT.md`,
`docs/ARCHITECTURE.md`, `docs/MVP_V1.md`, and `docs/SOURCES.md`; target architecture is explicitly
distinguished from implemented behavior.
Read `docs/PROGRESS.md` for verified live runs and current checkpoint status.
Bounded pagination/details, saved search URLs, multi-source daily ingestion,
application events, Excel interchange and optional daily Telegram are implemented.
Read `docs/OPERATIONS.md` for current commands. Server units are examples, not an
installed deployment; seven-day unattended acceptance remains pending.

## Product Priorities

1. Never lose a potentially relevant vacancy.
2. Prefer deterministic parsing before LLM calls.
3. Keep personal data, OAuth credentials, tokens, and raw emails out of Git.
4. Make every pipeline stage independently testable.
5. Optimize for a 10-minute daily review workflow, not full auto-apply.

## Current Target Profile

- Location: Vienna, hybrid Vienna, or remote Austria
- Seniority: working student, internship, junior, entry level
- Focus: backend, Python, Node.js/TypeScript, automation, integrations, APIs,
  data engineering, AI automation
- Avoid: senior, lead, principal, and roles requiring extensive experience

## Architecture Rules

- `gmail.py`: Gmail API and MIME decoding only.
- `parsers/`: source-specific deterministic extraction.
- `pipeline.py`: orchestration and cross-source deduplication.
- `storage.py`: SQLite persistence only.
- Do not put business rules into the CLI.
- Preserve source IDs. They are the canonical deduplication keys.
- Normalize public vacancy URLs and remove personal tracking parameters.
- Add a fixture and regression test for every new email layout.
- New discovery connectors belong behind a source-neutral boundary; keep Gmail
  compatible and preserve native board IDs across acquisition routes.
- Separate per-source availability from group availability. A closed duplicate
  must not suppress an active listing from another source.
- Keep source observations and timestamps honest; never invent email metadata
  for API/feed observations. Use additive migrations to preserve history.
- Email and direct discovery are independent V1 paths; attempt both each daily run.
- Playwright is an available direct-source transport in V1, with isolated browser
  tasks, hard timeouts and typed failure outcomes.
- SQLite owns application state/history independently of Telegram deliveries.
  Excel exports and validated status imports use stable IDs and revision checks.
- V1 includes persistent hosting, scheduled daily Telegram results, callback
  processing, durable notification retries, backups and health monitoring.

## Commands

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
job-radar backfill --days 10
job-radar export --format json
```

## Definition Of Done

- Tests pass.
- `ruff check .` passes.
- No secrets or raw personal emails are committed.
- README is updated when setup or behavior changes.
- New parser behavior is covered by a fixture-based test.

## Next Milestones

The user confirmed the probes; combined live ingestion and replay now pass.
Keep each next step independently testable and preserve the evidence in
`docs/PROGRESS.md`. Never run paid scoring or send test messages merely to verify
configuration; those operations have explicit CLI flags.

1. Validate the new daily workflow, workbook status imports and callbacks on the
   user's machine; maintain migration and replay regressions.
2. Add durable query cursors/cooldowns if source coverage requires resumable scans;
   current bounded collection deliberately restarts and deduplicates.
3. Add the next source only after measuring useful coverage of the current two.
4. Deploy unattended daily collection and Telegram reporting with callback
   processing, backups, recovery and external health checks. Prove seven-day V1
   acceptance before broader agent-driven V2 automation.
