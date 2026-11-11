# Integrated Job Radar v2 release candidate

Validation date: 2026-11-11 (Europe/Vienna). The RC merges both feature heads
(`c5ff14d` and `1baf2f8`) into an isolated worktree based on `origin/main` `7e8ee5c`.
Both original PRs were open at inspection. Their histories are preserved. The
one textual conflict was `.env.example`: both enrichment and owner configuration
were retained. The daily outbox and control sidecar localization hooks were
reviewed together and tested. No main update, stable tag, force push or deployment.

The application remains a modular monolith. `daily` is the one collection and
scoring orchestrator; `control` contains owner-only routing, read projections and
a bounded supervisor around `daily`. `sources` normalize public observations;
`enrichment_adapters` parse content; storage owns SQLite migrations and durable
state. Control-sidecar browsing indexes are derived; application/score/outbox
state remains in the main DB. No new dependency or service framework.

Cleanup: replaced the fragile unused depth-tracking HTML parser with existing
BeautifulSoup; modernized Makefile targets to use the pinned setup/runtime;
consolidated the primary README. Kept historical audits, old CLI commands,
screening rules, migration code, fixtures and all user files. No tracked generated
DB, export or credential was found. Compatibility commands (`run`, `telegram-send`,
`score`, `draft`) remain; do not schedule them alongside `daily`. There is still a
derived read projection for responsive Telegram pagination; it is not a second
collection/scoring implementation.

## Confirmed fixes and evidence

- Self-closing HTML elements previously ended a detail container too early.
  Scoped extraction now handles them and strips script/form/navigation text.
  Related structured postings are disambiguated by URL/title; unrelated results
  are not combined. A boilerplate footer cannot by itself downgrade a substantive
  JD. Missing/restricted/expired content is not reconstructed.
- A live Lever listing put the detailed duties/contract conditions in its
  documented `salaryDescription` field. Preserving it recovered the omitted text
  and explicit 100% capacity; numeric pay data alone still does not set hours.
  See [Lever field documentation](https://github.com/lever/postings-api).
- The bounded queue prioritizes new eligible incomplete descriptions, leaves a
  stale-full refresh opportunity, and checks possible Austria-remote evidence on
  a relevant Austrian-office snippet. Explicit location/hour/seniority constraints
  still prevent recommendation. AI cache never blocks JD retrieval. Retry states
  and fresh descriptions survive restarts. `--all --json` includes screening and
  separate attempt state; `NOT_ATTEMPTED` is not a transport failure.
- Dental/clinical/office-assistant ads with incidental software/part-time terms
  no longer reach recommendations. Dental software roles, Odoo/Python and QA
  remain technical. Unknown/out-of-target stacks remain in the database for review.
  AI/ML and ERP test roles use contextual technical signals, not salary basis.
- Optional public-board `title_terms` and `location_terms` filter before the item
  limit and are preserved by the Telegram gateway. Absolventen defaults off and
  has a narrower Software/Wien example. Known boards were not guessed.
- A 429 sets a durable source cooldown (one hour minimum, longer Retry-After when
  supplied); other sources continue. Partial and failed outcomes are visible.
- A real legacy DB had `status=delivered`, `delivery_status=applied`. The old
  startup migration could replace Applied on reopen. Copy only into newly added
  columns; serialize additive schema migration. Seven existing database backup
  copies preserved job counts and existing protected ledger/notification/cache
  values across two opens. Authoritative files were opened read-only for backup.

## Validation

Merged baseline: 381 tests and Ruff. Integrated suite: 416 tests, zero failures
or skips, Ruff passed. Fresh hash-pinned no-browser install, CLI init/health/backup,
offline demo and true fixture-backed subprocess gateway E2E passed on macOS.
The E2E opens the owner dashboard, triggers Quick Search, collects and enriches
through the real CLI, receives a mocked completion message, browses, Saves and
marks Applied, then verifies state on reopening. Regression coverage includes
budget reservations, confirmation/replay, partial/failure completion, locale,
cooldown/expiry, lost delivery ambiguity and legacy migration.

Bounded public live run with six deployment profiles and two opt-in employer
boards, isolated DB, two results/profile, no Gmail, paid model or Telegram:

| Run | Source observations | Unique | Recommended review | FULL | PARTIAL | MISSING | INACCESSIBLE | Duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| First | 16 | 16 | 5 | 8 | 2 | 6 | 0 | 17.0 s |
| Replay after Lever extraction fix | 16; 0 inserted | 16 | 4 | 11 | 1 | 4 | 0 | 45.8 s |

Both runs succeeded; first discovery 8 HTTP requests / 0 HTTP errors and 4 JD
logical requests with 1 retry; replay discovery 10 requests / 1 HTTP error
recovered, 3 JD logical requests. No final source failures. Prior FULL rows and
a test-only Saved state survived; zero score records and zero outbox sends.
Filtered ATS discovery skipped 97 out-of-profile observations on the first pass.
A separate Absolventen Software/Wien RSS probe returned two partial entries, with
zero detail requests. It is not a full-JD guarantee.

Manual sample: Odoo/Python (40h) and QA Automation (38.5h) were correctly stored
but excluded; the Lever AI/ML listing's recovered 100% capacity is incompatible.
Junior Java/Python and software roles with unknown hours are review only. Senior
and lead postings account for four incomplete rows; they are intentionally not
prioritized for enrichment. The embedded student role is a possible preference
false negative outside the current favored stack; it remains browsable. No
precision/recall or market-wide FULL rate is inferred from this sample.

## Credential gate and recovery

Legacy master/recovery histories contain an OpenAI key, historical Telegram token
and private candidate profile. Do not push those refs. RC ancestors/staged files
were scanned without printing values. Rotation/revocation has not been verified;
**live production is BLOCKED**, even if CI passes. In the provider console create
an appropriately scoped replacement key and revoke the exposed key. Revoke and
regenerate the historical bot token through BotFather or verify it was already
revoked. Put replacements only in the private environment file with mode 0600
(or root:jobradar 0640 on Ubuntu), and keep the verified profile private. Do not
reuse the exposed key to test availability. Review account billing limits and
verify the private owner/chat match with `telegram-check` before starting the bot.

Stop both writers before moving/restoring state. Run `backup --database PATH
--output PRIVATE_DIR` for a consistent SQLite backup of main and existing Telegram
sidecar; copies are separate snapshots, so stop writers for a paired restore.
Keep originals and existing WAL/SHM state. Restore to a **new** path, check `health`,
then point all commands at the validated DB. `init-db --database PATH` keeps a
pre-migration backup of an existing DB without pruning those snapshots. Scheduled
daily backups retain the prior seven-copy policy. Never automatically acknowledge
an unknown notification; inspect the private chat and outbox. Roll back code only
against a known compatible backup. Ctrl-C stops local polling; systemd stops the
service control group on Ubuntu. Kernel locks release on process exit.

## Ubuntu 24.04 procedure (prepared, not deployed)

After rotation, owner acceptance and host approval, put the reviewed checkout
at `/opt/job-radar` (writable by installer, readable by service user). Install
Python prerequisites and the pinned HTTP-only runtime:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv
cd /opt/job-radar
JOB_RADAR_VENV=/opt/job-radar/.venv JOB_RADAR_PYTHON=python3 ./scripts/setup.sh --no-browser
id jobradar >/dev/null 2>&1 || sudo useradd --system --home-dir /var/lib/job-radar \
  --shell /usr/sbin/nologin jobradar
sudo install -d -o jobradar -g jobradar -m 0700 /var/lib/job-radar /var/cache/job-radar
sudo install -o root -g jobradar -m 0640 deploy/job-radar.env.example /etc/job-radar.env
sudo install -o root -g jobradar -m 0640 deploy/searches.vienna.example.yaml /etc/job-radar-searches.yaml
sudo install -o jobradar -g jobradar -m 0600 candidate_profile.example.yaml /var/lib/job-radar/candidate_profile.yaml
sudoedit /etc/job-radar.env   # private rotated token, owner/chat IDs, paths; AI/digest opt-in
sudo -u jobradar /opt/job-radar/.venv/bin/python -m job_radar.cli init-db --database /var/lib/job-radar/jobs.db
sudo systemd-run --wait --pipe --collect -p User=jobradar -p WorkingDirectory=/opt/job-radar \
  -p EnvironmentFile=/etc/job-radar.env /opt/job-radar/.venv/bin/python -m job_radar.cli \
  health --require-telegram --searches /etc/job-radar-searches.yaml --output /var/lib/job-radar/output
sudo install -m 0644 deploy/job-radar.service deploy/job-radar.timer deploy/job-radar-callbacks.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start job-radar.service       # one bounded first search; no paid/send by default
sudo systemctl enable --now job-radar-callbacks.service job-radar.timer
sudo journalctl -u job-radar.service -u job-radar-callbacks.service -n 50
```

The install examples above are for fresh paths; preserve existing private files
and state on upgrades. The single systemd timer is 08:00 Europe/Vienna and shares
`.run.lock` with manual searches. Do not also install a cron/launchd schedule for
the same database. Defaults use HTTP, no Gmail, zero paid scoring, no digest. Set
`JOB_RADAR_SEND_DIGEST=1` only for intentional scheduled Telegram delivery. Ubuntu
CI proves tests and pinned dependencies; host service execution, real Telegram,
reboot/restore drills and unattended operation still require acceptance. To stop:
`sudo systemctl stop job-radar.timer job-radar.service job-radar-callbacks.service`.
