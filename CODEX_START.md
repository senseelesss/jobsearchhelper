# Working on Job Radar

Read AGENTS.md, README.md and the latest docs/PROGRESS.md checkpoint first.
Use daily as the release workflow. Current business policy is in
[application policy](docs/APPLICATION_PRIORITY.md); Linux host setup is in
[PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md).
Historical MVP audits/designs are rationale, not a list of still-missing features.

Keep changes scoped, preserve native IDs, SQLite application/outbox state and
private files, and run pytest/Ruff before committing. Parser changes need sanitized
fixtures. Validate on isolated DBs. Never run paid scoring or real Telegram sends
without explicit authorization. Do not deploy, push, rewrite history or upload
legacy private branches without owner approval. See docs/RELEASE_v1.1.0.md for the
historical credential-rotation and metadata/privacy publication gates.

Keep real profiles, OAuth clients/tokens, keys, emails and reports out of Git and
prompts. The blank profile and public filtered search examples are templates.
