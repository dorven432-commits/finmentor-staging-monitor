# FinMentor staging monitor

Independent, secret-minimizing reliability checks for the **staging** environment.
The monitor uses GitHub Actions, GitHub Issues for durable incident state, and Gmail
SMTP for transition notifications. It does not access application data, Supabase,
production, backup archives, or backup checksums.

## What it checks

- frontend HTTPS/TLS and HTTP 200;
- backend `/health` and `/readiness` HTTPS/TLS and HTTP 200;
- safe backup heartbeat metadata every three hours;
- failed backup attempts and last successful backup older than 26 hours;
- missing or malformed backup heartbeat metadata.

Synthetics run every 15 minutes. A synthetic incident opens after two consecutive
failed runs. Repeated alerts have a 12-hour cooldown. Recovery is sent once. Healthy
runs send no email.

## Public-data boundary

Repository files contain no deployment URL, email address, token, local path, user
record, financial value, archive name, or checksum. Endpoint URLs and email settings
must be GitHub Actions secrets. The only permitted public backup document is:

```json
{
  "version": 1,
  "status": "succeeded | failed",
  "last_success_at": "ISO-8601 UTC timestamp or null",
  "checked_at": "ISO-8601 UTC timestamp",
  "error_type": "short allowlisted-safe token"
}
```

Incident state exposes only generic check names, safe HTTP/error categories, times,
counters, and notification transition state. Never place a URL, traceback, local
path, archive name, checksum, token, personal data, or financial data in an issue.

## Initial setup

Do not enable monitoring until all steps are complete.

1. Create three issues in this repository:
   - `Monitor incident state (machine managed)`;
   - `Backup heartbeat (machine managed)`;
   - `Acceptance state (machine managed)`.
2. For both state issues, set the body to the marker
   `<!-- finmentor-monitor-state-v1 -->`, followed by a fenced `json` block containing
   `setup/incident-state.json`.
3. For the backup issue, use marker `<!-- finmentor-backup-status-v1 -->`, followed by
   a fenced `json` block containing `setup/backup-status.json`.
4. Create repository variables:
   - `STATE_ISSUE_NUMBER`;
   - `BACKUP_STATUS_ISSUE_NUMBER`;
   - `ACCEPTANCE_STATE_ISSUE_NUMBER`;
   - `MONITOR_ENABLED` = `false` initially.
5. Create repository Actions secrets:
   - `STAGING_FRONTEND_URL`;
   - `STAGING_BACKEND_URL`;
   - `SMTP_USERNAME`;
   - `SMTP_APP_PASSWORD`;
   - `ALERT_EMAIL_TO`.
6. Configure the local backup-metadata publisher as described in `OPERATIONS.md` and
   confirm the backup issue contains only the five allowlisted fields.
7. Run `staging-reliability` manually in `monitor` mode. Confirm the workflow is green
   and no email is sent for healthy state.
8. Set `MONITOR_ENABLED` to `true`.
9. Run the isolated `acceptance_cycle` only after typing `SEND TEST ALERTS`. It sends
   four controlled messages: initial failure, cooldown reminder, recovery, and a new
   failure after recovery. It never changes the real incident-state issue.

## Local tests

```text
PYTHONPATH=src python -m unittest discover -s tests -v
```

The project intentionally uses the Python standard library only at runtime.

## Scheduler limitations

GitHub Actions schedules are best-effort. Jobs can start late or be dropped during
high load, and scheduled workflows in inactive public repositories can be disabled.
The cron minutes avoid the top of the hour, every job has bounded time, concurrency
is serialized, and a monthly empty keepalive commit records repository activity.
The workflow run list remains the source for detecting individual delayed/dropped
runs.

A total GitHub outage or simultaneous failure of GitHub Actions and GitHub email
cannot be detected by this repository itself. A truly independent scheduler
watchdog requires a second provider. This residual risk is intentionally documented;
no paid external resource is created by this implementation.
