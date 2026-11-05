# Working With Codex

## First Session

Open this repository as the workspace and use:

```text
Read AGENTS.md, README.md, and the complete current codebase.
Run the available checks before editing.
Then inspect the implementation and tell me the current pipeline, risks, and the
smallest next milestone. Do not write code until you understand the repository.
```

## Implementation Prompt

```text
Read AGENTS.md and follow it strictly. Implement the first incomplete milestone in
AGENTS.md. Keep the change scoped, add regression tests, run all available checks,
and update README.md only when behavior or setup changes. Do not expose secrets or
store raw emails.
```

## Review Prompt

```text
Review the current branch as a senior Python engineer. Findings first, ordered by
severity, with exact file and line references. Focus on correctness, data loss,
duplicate handling, Gmail pagination, parser regressions, privacy, and missing tests.
Do not edit files during this review.
```

## Working Style

- Keep one concrete objective per Codex session.
- Let Codex inspect and test before asking it to implement.
- Ask for tests with every parser change.
- Commit after a verified milestone, not after every tiny edit.
- Never paste OAuth tokens or raw personal emails into prompts.
- Store sanitized email layouts in `tests/fixtures/`.

