# Job Radar v3.1: implementation and operating boundaries

## Repository and cleanup

The existing Python modular monolith and SQLite ledger remain authoritative.
The separate cleanup commit is 37a6451 (redundant `.venv/` ignore entry only).
No files were deleted or moved. Existing private profiles, searches, backup files,
acceptance DB, WAL/SHM, locks, outputs and egg-info were inventoried by path and
preserved. Ignored names do not prove files disposable. Historical docs, runner,
legacy CLI and migration code remain for compatibility. No database contents or
credentials were printed. A filename audit is not certification of secret absence.

Active modules: sources/parsers → ingestion/storage; screening/work_hours/eligibility;
candidate/matching/ai; daily/runner; control/telegram; exporting/workbook/CLI.
Tests and existing source fixtures remain. No new dependencies or migration.
This document supersedes the incomplete checkpoint in ARCHITECTURE_V3_1_AUDIT.md.

## Before and after

Before: coarse screening and application assessment controlled scoring admission;
AI returned a technical score with unstructured strengths/risks. CLI dry-run used
its own candidate selection. Education had no explicit confirmed-profile check.

After: discovery → native/semantic deduplication → preliminary safe screening →
bounded enrichment/rescreen → application constraints → budgeted structured AI →
evidence validation and final deterministic veto → persisted score → ranking/outbox.
Preliminary screening remains to avoid spending enrichment requests on clear rejects;
this is not an unconditional enrich-everything pipeline.

`MatchEvaluation` extends the existing JobScore, with seven dimensions: technical,
experience/seniority, education, hours, language, location/remote and career.
Each has confirmed_match / confirmed_mismatch / unknown, literal vacancy/candidate
excerpts and explanation. Overall unknown requirements and explanation are stored
in existing score JSON. Old scores remain readable by drafting/reporting.

`finalize_evaluation` checks literal evidence against inputs. Unsupported claims
become unknown; unknown dimensions lower confidence and prevent apply promotion.
AI mismatches remain reviewable; they never upgrade skip. Deterministic hard reasons
force skip regardless of score. This validates grounding, not semantic entailment:
a quoted word alone cannot prove the model interpreted a requirement correctly.

Completed Master's requirements are distinguished from preferences, alternatives,
ongoing studies and negation. Explicit user-confirmed qualification facts can veto
incompatible education or required years. Missing facts remain unknown. Existing
seniority/hour safety policy remains: salary FTE is not vacancy hours, unknown hours
stay reviewable; the default acceptable 15–30 range still includes 30 hours.
Nearby Vienna suburbs become review rather than automatic location rejection.
Business/industrial/mechanical roles with only generic technical keywords are excluded;
software-context titles retain review paths. These are conservative heuristics, not
exhaustive occupational or geographic classification.

Daily's scoreable/enrichment gate uses `evaluation_candidate`; scoring_plan enforces
assess_application on canonical candidates. CLI dry-run now uses scoring_plan, matching
actual cache/cooldown/admission decisions. Incomplete old descriptions receive a bounded
fairness opportunity every third turn; retries, stale-full slots and source cooldowns
remain. No scoring cache suppresses JD enrichment.

Cache version is `candidate-score-v3.1-evidence`: old assessments remain historical;
new scoring requires the normal explicit invocation and budget. Profile, content,
model and prompt identity still invalidate reuse. Profile edits never call AI.
Telegram retains uk/de/en navigation and displays the stored explanation. AI explanation
language itself is model output, not a newly localized template.

## Configuration

The same private candidate YAML is used. Optional, **only user-confirmed** facts:

```yaml
verified_qualifications:
  confirmed: true
  completed_degrees: [bachelor]
  professional_experience_years: 1
search_preferences:
  target_hours: [20, 25]
  acceptable_hours: [20, 25]
```

These are illustrative values, not facts about the owner. Do not copy them without
verification. Omit unknown qualification fields; an empty confirmed degree list means
no completed degrees. Existing profiles need no migration. Candidate YAML remains
loosely structured; this release does not implement a profile editor or comprehensive
profile validation. Use `master`, `doctorate` or `phd` for recognized completed higher degrees.

## Commands

Run from repository root. Always select a disposable/copied DB for acceptance:

```bash
export JOB_RADAR_DB=/absolute/private/test-copy.db
export JOB_RADAR_CANDIDATE_PROFILE=/absolute/private/verified-profile.yaml
./bin/job-radar score --dry-run --limit 5
./bin/job-radar daily --no-email --searches searches.example.yaml --score-limit 0 --enrich-limit 3
./bin/job-radar enrichment-report --all --json
./bin/job-radar enrich --only review --retry-failed
./bin/job-radar application-report --all-active --format json
./bin/job-radar telegram-preview --limit 3
```

Dry-run does not invoke AI, but repository initialization can migrate a supplied DB;
use a copy. Daily/enrich perform real source networking. Standalone enrich has its
existing scope; prefer daily's explicit enrichment bound for bounded operation.
No command above sends Telegram or submits applications.

Paid evaluation is opt-in, not run during development:

```bash
./bin/job-radar daily --no-email --searches searches.example.yaml --enrich-limit 3 \
  --score-limit 1 --ai-run-budget-usd 0.02 --ai-day-budget-usd 0.05
```

Requires private OPENAI_API_KEY, configured OPENAI_MODEL and accurate configured
input/output pricing. Caps must cover the configured request ceiling or scoring is
refused. Use this budgeted daily path, not the legacy standalone score command, for
USD reservation controls. No model or pricing changes are introduced here.

Offline verification with the existing development interpreter:

```bash
python -m pytest -q
python -m ruff check .
python scripts/evaluate_policy.py
```

The evaluation script uses the 16 pre-existing synthetic priority fixtures, mapping
non-EXCLUDED to positive. TP=10, TN=6, FP=0, FN=0, precision=recall=1.0 on that fixture
set only. These are compatibility metrics, not measured market or OpenAI accuracy.
No manually labelled production set was available/created; no labels were invented
for private vacancies. New mocked regressions cover hours/FTE, education, experience,
role relevance, suburb review, evidence grounding, cache/restart/drafts and high-score veto.
Existing integration tests cover collection, enrichment, budgets, deduplication and outbox.

## Ubuntu 24.04 readiness and limits

No deployment performed. Existing units/runbook still apply. Host installation,
credential rotation, SQLite backup/restore, permissions, browser availability,
real Telegram delivery and unattended acceptance remain operational gates.
Structured output is tested with mocked OpenAI SDK responses, not a paid live model.
The existing output-token budget may require operator tuning for seven dimensions;
truncated/invalid output fails safely and is not a successful recommendation.
Regex rules cannot cover every negation, degree equivalence or suburb. No claim of
improved real-world matching accuracy is made without a labelled evaluation.
