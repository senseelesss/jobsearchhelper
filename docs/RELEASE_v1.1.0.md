# Job Radar v1.1.0 — release notes and audit

Prepared locally on 2026-11-08. No GitHub push, paid LLM request, real Telegram
message or external deployment is part of this preparation. Application release
validation does not certify unattended production operation.

## Included capabilities

- Independent Gmail readonly alerts and public LinkedIn/karriere.at discovery.
- Native source-ID deduplication, observations and conservative semantic grouping.
- Bounded, cache-aware JD enrichment with honest provisional fallback.
- Evidence-based eligibility: 20–25h target, 15–30h acceptable, seniority/required
  experience, technical role, location and uncertain work authorization.
- Cached structured AI technical matching with bounded attempts/tokens/timeouts;
  deterministic application priority does not require another paid call.
- Ranked Tier A recommendations and explicitly UNSCORED Tier B review.
- Telegram summaries, persistent per-job alert identity, durable ambiguous-send
  handling, Applied/Skipped callbacks and application-event revisions.
- Excel/JSON reports, top-five review, run statistics and consistent SQLite backups.

No agents, dashboards, external database, auto-apply or new deployment was added.

## Release changes

Version is defined only in src/job_radar/__init__.py; setuptools reads that
attribute for package metadata. CLI --version exposes the same value. uv.lock and
its hash-pinned requirements.lock export pin runtime/dev/scraping/build tools.
Setup installs the pinned dependencies before building the local source without
unbounded dependency resolution. Existing profiles, searches and private files
are preserved; fresh setup also creates the six-search example configuration.

The daily/application-set implicit DB now honors JOB_RADAR_DB, matching callbacks
and other commands. Previously it silently defaulted to data/mvp-v1-test.db.
Explicit --database still wins. Before upgrading, identify the authoritative DB
and configure its path; this release does not move, merge or delete databases.
There are no new SQL migrations in the release-preparation commit.

## Repository cleanup decisions

No authoritative data, credentials, branches or user-generated reports were
deleted. No history was rewritten. Source, parser fixtures, schema/migration code,
useful setup/deployment scripts and historical screening rules are retained.
Historical audits/design documents record dated decisions and are labelled as
such rather than being treated as current installation instructions. README is
the release installation/usage guide; CODEX_START points to the current workflow.

.gitignore now excludes env variants/secrets, OAuth files, SQLite/WAL/SHM/locks,
venvs, Python/test caches, logs, generated Excel, backup/build/dist and OS/editor
artifacts, while retaining example configuration and required fixtures. An
independent nested local project is excluded only in local .git/info/exclude;
its files and own repository are not modified or shipped with Job Radar.

## Security audit and unresolved publication gates

The audit checks tracked working files, every release-ancestor commit and all
other local branch histories against configured credential values and token,
private-profile, email and absolute-user-path patterns. Values are never printed.
The current release tree and release ancestry contain no detected API/bot/OAuth
credential values or populated candidate profile. Generated/private data are not
part of the release archive/wheel. This is a targeted audit, not proof against
every possible encoded or unknown secret.

**Historical exposure exists outside release ancestry:** an old local recovery
commit contains an OpenAI key matching the currently configured key and a
historical Telegram token. Older master/recovery history contains the populated
private candidate profile. These objects must not be pushed. Rotate the OpenAI
key before further use/publication; revoke or verify revocation of the historical
bot token. Whether any old objects were previously shared is not established.
Simply deleting values from today's files is not revocation. Detailed commit/path
findings are retained privately in outputs/release-v1.1.0/security-audit.json.

Existing Git author/committer metadata also contain a personal email address.
No history rewrite is authorized, so that metadata remains. The new release commit
and annotated tag use the repository handle and a noreply address without changing
persistent Git identity settings. This cannot redact existing commits. Decide
whether historical author-email exposure is acceptable before publication.

Only push the reviewed release branch and tag after credential/privacy gates are
resolved. Never push --all/--mirror or the old master/recovery branches. No automatic
rotation, revocation, history rewrite or remote action has been performed.

## Validation

Release validation uses a fresh public-file copy, blank example configuration,
a new isolated Python 3.12 venv, hash-pinned installation, temporary SQLite and
mock network integrations. All 254 tests passed; Ruff, pip check, shell syntax,
lock/export consistency and diff whitespace checks passed. Source and installed CLI
report 1.1.0. A built wheel contains both policy JSON resources and no private data.
An offline Chromium fixture smoke parsed two jobs with network routes blocked.
Tests cover configuration, shared DB defaults, version
consistency, initialization/migrations, offline end-to-end ingestion, filtering,
prioritization, enrichment, previews, repeated-run deduplication, callbacks and
status persistence. Real account credentials are not required or exercised.

CI is prepared for Ubuntu 24.04/Python 3.12 using the same dependency export. No
systemd unit has been installed or enabled. Existing Linux unit files are examples
for a later approved deployment.

## Known limitations

- Browser sites/API/model availability can change; blocks/login walls are reported,
  not bypassed. Public search filters are not a complete-crawl guarantee.
- Many saved jobs lack full JDs/hours; unknown evidence is human review, not a
  verified part-time contract. Work authorization is not inferred.
- Semantic grouping can miss reposts/location aliases or over-group sparse records.
- SQLite/outbox/application state must survive host migration. Local-only backups
  do not protect against host loss. An older restored outbox can resend alerts.
- Timeout/crash delivery can remain unknown; exactly-once SQLite+Telegram delivery
  cannot be guaranteed. Old pending/unknown payload recovery requires inspection.
- Failed scoring may incur usage not returned by the provider. Estimates are not
  invoices or dollar caps. No automatic model migration is implemented.
- Linux browser libraries/IP access, systemd runtime, reboot/catch-up, live callback
  interaction, external heartbeat/off-host backup and seven-day unattended operation
  still require target-host acceptance. No production-readiness claim is made.

## Owner-controlled publication

After rotating/revoking the exposed credentials and resolving the metadata/privacy
review, inspect the local tag and push only the intended branch/tag:

```bash
git show --stat v1.1.0
git status --short
git push origin HEAD:main
git push origin refs/tags/v1.1.0
```

The commands assume the release commit is checked out on codex/mvp-v1 and that
remote main can fast-forward. Fetch/review any intervening remote commits first;
do not force-push. Creating the local commit/tag does not publish a GitHub Release
page or provision a server.
