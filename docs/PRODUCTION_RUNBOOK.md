# MVP production launch: Ubuntu 24.04

Status: configuration prepared, not deployed. Do not enable remote services without
owner approval. See `PRODUCTION_AUDIT.md` for observed checks and open gates.
No agent, auto-apply, architecture migration or paid hosting is required.

## Deployment decision

Use an existing always-on Ubuntu 24.04 machine with persistent local disk. Python
venv + systemd matches the existing SQLite and browser design. There is no added
hosting subscription on an already available machine; electricity, connectivity
and optional LLM usage are not free. No free cloud instance has been provisioned
or guaranteed. If no always-on machine exists, host selection remains open.

| Concern | GitHub-hosted Actions | Ubuntu + systemd |
| --- | --- | --- |
| SQLite/applications/outbox | Fresh VM per job; requires private durable state download/upload and locking | One local DB shared by daily process and callback worker |
| Private data | Never upload DB/profile/OAuth token as public repository artifacts or commits; would need private encrypted storage | Private files under `/var/lib/job-radar`, restricted user |
| OAuth refresh | Refreshed token must be persisted outside ephemeral runner | Token rewritten in persistent private directory |
| Browser | Chromium installation supported, but runner IP access to each board is unverified | Chromium installed once; target-host access still must be verified |
| Callbacks | Short scheduled jobs cannot provide continuous polling | Restarting long-running callback service |
| Schedule | Can be delayed/dropped and disabled after 60 inactive days in a public repo | Vienna-time timer with catch-up after downtime |
| Limits | Hosted job limit currently 6h; storage/compute plan limits apply | Explicit run timeout, source limits and scoring limits |

Sources: [fresh hosted runners](https://docs.github.com/en/actions/concepts/runners/github-hosted-runners),
[scheduled workflow caveats](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[Actions limits](https://docs.github.com/en/actions/reference/limits),
[systemd timers](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html).
This comparison is an engineering recommendation, not a cloud reliability test.
Keep GitHub Actions for CI. No production Actions workflow is enabled.

## 1. Install on the approved host

Run the following only after host approval. Administrative commands are examples
for Ubuntu 24.04. These units have not been exercised on this macOS development
machine. Use a machine that stays powered on and connected.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git sqlite3 tzdata
sudo useradd --system --create-home --home-dir /var/lib/job-radar --shell /usr/sbin/nologin jobradar
sudo install -d -o jobradar -g jobradar -m 0700 /var/lib/job-radar/secrets /var/cache/job-radar
sudo git clone https://github.com/senseelesss/jobsearchhelper.git /opt/job-radar
sudo python3 -m venv /opt/job-radar/.venv
sudo /opt/job-radar/.venv/bin/pip install --require-hashes -r /opt/job-radar/requirements.lock
sudo /opt/job-radar/.venv/bin/pip install --no-deps --no-build-isolation '/opt/job-radar[dev,scraping]'
sudo /opt/job-radar/.venv/bin/python -m playwright install-deps chromium
sudo -u jobradar env HOME=/var/lib/job-radar PLAYWRIGHT_BROWSERS_PATH=/var/cache/job-radar/browsers \
  /opt/job-radar/.venv/bin/python -m playwright install chromium
```

If `jobradar` or `/opt/job-radar` already exists, inspect/reuse it; do not repeat
account creation or overwrite an installation. Keep the checkout/venv owned by
an administrator; the service user needs write access only to state/cache.

## 2. Provision private state

Copy your **verified** candidate profile, Gmail Desktop OAuth client JSON and a
working Gmail refresh-token file through your private SSH connection. Place them
at the paths in `deploy/job-radar.env.example`. These are private inputs, not files
to add to the public repository. Set owner `jobradar:jobradar` and mode `0600`.
The token directory must be writable by `jobradar` for token refresh.

```bash
sudo install -o root -g jobradar -m 0640 /opt/job-radar/deploy/job-radar.env.example /etc/job-radar.env
sudo install -o root -g jobradar -m 0640 /opt/job-radar/deploy/searches.vienna.example.yaml /etc/job-radar-searches.yaml
sudoedit /etc/job-radar.env
sudoedit /etc/job-radar-searches.yaml
```

Enter bot token, intended chat ID and API key in the environment file. Send `/start`
to your bot first. Do not paste secrets into command arguments, journals, issues
or CI variables printed by debugging. `JOB_RADAR_SCORE_LIMIT=0` explicitly chooses
rule-based delivery if scoring is not enabled. The supplied value 5 enables at
most five scoring attempts per run; drafting is never invoked by `daily`.

The existing `gpt-5.4-nano` model remains configurable. Official documentation lists
$0.20/input-million and $1.25/output-million, and currently marks the model page
as deprecated. Verify availability in your own account before enabling the timer;
choose a replacement deliberately and update the prices together if unavailable.
No automatic model substitution is implemented.
[Model documentation](https://developers.openai.com/api/docs/models/gpt-5.4-nano).

Defaults: 30s SDK timeout, zero SDK retries, 1,200 output tokens and 48,000 input
payload bytes per request. Oversize input is recorded as a failed score rather
than silently truncated. Counts, input/output bounds and per-request timeout are
limits, not a guaranteed dollar cap. At five calls/day, 48k ASCII payload bytes
and 1,200 output tokens give a deliberately loose estimate around $0.06/day plus
prompt/schema overhead at the listed rates; ordinary runs should use less.
Actual estimates use returned tokens. Failed/timed-out requests can be billed
without returning usage; reported costs are not an invoice or a hard ceiling.

For the prepared six one-page searches, Gmail has a 120s between-message budget, each search
has a 120s between-request budget, and requests have their own timeouts.
Eligible enrichment adds a global budget of five GET operations/120s; direct
unscreened detail fetching defaults to zero. See MACOS_STAGING.md for current
two-tier review and scoring fairness; provider unavailability retains UNSCORED
review candidates in Telegram instead of hiding them. These
budgets may overrun by the current request; the service has a final 25-minute
limit. Keep the default budgets initially. Raising source count, enrichment/score limits,
SDK retries or timeouts requires reviewing the whole-run timeout.

### Gmail reauthorization if a token is revoked

The launch check found an old revoked token and an existing working alternate token.
The ignored local environment now selects the verified alternate. Use its configured
path when transferring state. If authorization is needed later, run interactively
on your local computer, outside systemd:

```bash
./bin/job-radar backfill --days 1 --limit 1 --dry-run
```

If it reports `invalid_grant`, preserve the stale token under a private backup
filename, remove it from the configured token path, and rerun the same command.
Complete Google's browser authorization and copy the new token privately to the
host. Never delete the only working token. Verify the configured path first;
`JOB_RADAR_TOKEN` can override the default. A token in Google OAuth testing mode
may require recurring consent; validate refresh after expiry on the real host.
Unattended `daily` will never open a consent browser: it reports the failure and
continues other sources. The 10-day lookback is bounded by email-limit; after a
long outage or high volume, run an explicit larger backfill before resuming.

## 3. Preserve the existing DB when moving machines

The DB contains vacancies, scores, application states/events, callback offsets,
notification outbox and per-vacancy alert identities. Starting from an empty DB
loses the duplicate-alert and previous-application protection.

Stop writers on the old machine, create a consistent snapshot using SQLite's
`.backup` or the project's `backup_database`, and transfer it privately as
`/var/lib/job-radar/jobs.db` with owner `jobradar`, mode `0600`. Do not copy just
the main live WAL database file. Never run two hosts with diverging DB copies or
two callback workers using the same bot. Preserve this state when updating code.

## 4. Validate units and run manually before enabling the timer

```bash
sudo install -m 0644 /opt/job-radar/deploy/job-radar.service /etc/systemd/system/
sudo install -m 0644 /opt/job-radar/deploy/job-radar.timer /etc/systemd/system/
sudo install -m 0644 /opt/job-radar/deploy/job-radar-callbacks.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/job-radar.service /etc/systemd/system/job-radar.timer /etc/systemd/system/job-radar-callbacks.service
systemd-analyze calendar '*-*-* 08:00:00 Europe/Vienna'
sudo systemctl daemon-reload
sudo systemctl start job-radar.service
sudo journalctl -u job-radar.service -n 120 --no-pager
sudo cat /var/lib/job-radar/output/report.json
```

This manual run sends one real daily digest and makes bounded paid calls when
scoring is enabled. Exit 0 means the bounded cycle succeeded; exit 1 means partial
results and needs inspection. Files already committed remain saved. Check each
source, scoring errors, `telegram=sent`, `telegram_sent_this_run=true`, the Excel
file and backup. A prior `telegram=sent` with `telegram_sent_this_run=false` means
the day's digest was already acknowledged, not a new delivery.

Repeat the service start. Verify no duplicate source rows for repeated native IDs,
no repeated scoring for unchanged cached inputs, and no second digest that day.
Results on changing live pages may legitimately include new IDs or updated text.
Use yesterday/tomorrow observations over the next days to verify ongoing usefulness.

Then start callbacks, press Applied/Skip on a test vacancy, restart the callback
service and confirm the state in the workbook/DB after the next run. If testing
without a real application, use Skip rather than Applied. The ledger never infers
an application from opening a link. Do not run the laptop callback poller as well.

```bash
sudo systemctl enable --now job-radar-callbacks.service
sudo systemctl restart job-radar-callbacks.service
sudo systemctl enable --now job-radar.timer
systemctl list-timers job-radar.timer
```

08:00 uses Vienna time, including DST; persistent catch-up runs once after missed
activation. Keep the host awake. Do not start a second scheduler.

## 5. Daily operations and recovery

- Read `/var/lib/job-radar/output/report.json`, `runs/run-N.json` and the journals.
  SQLite `run_history` stores full run counters/errors; `run_stage_history` records
  email, individual searches, scoring and Telegram. A run interrupted by a killed
  process becomes `interrupted` when the next locked daily cycle starts.
- Zero new relevant vacancies still produces a daily summary. Previously alerted
  items are suppressed across days/chats with native+semantic identity records;
  Applied/Skipped states independently exclude them. Unsent overflow waits for
  a later day. A same-day rerun does not replace an already sent summary.
- A definitive Telegram HTTP 4xx leaves the immutable payload pending for a later
  rerun after fixing the issue. A timeout, transport error or crash during send is
  `unknown`, prevents automatic resend of those items and appears in later health
  reports. Inspect the chat before resolving it. Do not blindly clear the outbox.
- To inspect outbox without message bodies/chat IDs:
  `sudo -u jobradar sqlite3 /var/lib/job-radar/jobs.db 'SELECT notification_key,status,message_id FROM notification_outbox;'`.
  After operator verification that an unknown message was received, mark that
  exact key `sent`. If verified absent, set that exact key to `pending` and rerun
  on the same Vienna day. Old-day pending payloads are not automatically drained;
  resolve them before manually releasing their reservations. Never delete the DB
  to retry a notification. Ambiguous delivery cannot guarantee exactly once.
- Failed score responses are stored as failed with safe type/HTTP status labels;
  successful scores survive another job's failure. Unattended failures can retry
  after 24h within the same call budget. Explicit `--score-retry-failed` retries
  only failures and is another paid operation. Do not combine legacy `run` or
  `telegram-send` with the scheduled `daily` path.
- `hours_status=unknown` is a separate review question, not a technical score
  penalty. Only explicit full-time-only evidence excludes a role from daily
  alerts. A generic full-time benefits paragraph does not prove ineligibility.
- `output/backups/` retains seven consistent snapshots (runs, not necessarily
  seven days). Copy one privately off-host regularly. Do not commit it or upload
  it as a public Actions artifact. Backups/output contain private state.
- Test restore with both services stopped: restore a chosen verified backup to a
  separate temporary DB, run `PRAGMA quick_check`, inspect application/outbox
  counts, then replace the live DB only after approval of the chosen recovery
  point. Never pair a restored main file with stale `-wal`/`-shm` files. Restoring
  older notification state can resend alerts; reconcile Telegram history first.
- For updates: stop the timer and callbacks, back up the DB, pull the reviewed
  commit, update dependencies/Chromium if needed, run tests and one manual cycle,
  then restart callbacks/timer. Record `pip freeze --exclude-editable` privately
  on the target to reproduce its installed environment.

A local process cannot alert when its host is off. Journal logging and partial
Telegram reports work only while the machine/network are running. External
heartbeat monitoring and off-host backup automation remain deployment tasks;
confirm an independent daily check/notification route before relying on the host.

## Acceptance gate

Do not call the deployment production-ready until a complete configured run,
real source access, live scoring, Telegram acknowledgement, restart/state replay,
callback status persistence, a restore drill, a scheduled run on Ubuntu and failure
visibility have been demonstrated. Continue observing seven scheduled days before
claiming the earlier V1 unattended acceptance milestone. A macOS smoke run does
not verify Linux Chromium dependencies, systemd syntax/runtime or host networking.
