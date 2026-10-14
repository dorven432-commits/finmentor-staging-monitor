# Operations guide

## Fine-grained token for the backup metadata publisher

Create this only after the public monitor repository exists.

1. Open GitHub **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
2. Give it a short expiry (90 days is recommended).
3. Select only the public `finmentor-staging-monitor` repository.
4. Under repository permissions grant only **Issues: Read and write**. Keep all other
   permissions at their minimum/default value.
5. Generate and copy the token once. Do not paste it into chat, a file, PowerShell
   history, Task Scheduler arguments, or GitHub Issues.
6. Open Windows **Credential Manager → Windows Credentials → Add a generic
   credential**.
7. Internet/network address: `FinMentor/Staging/MonitorPublishToken`; username:
   `github-token`; password: the token.

## Local safe-status publisher

This is a separate task and does not modify or replace the existing backup task.

1. Clone this public repository to a stable local directory.
2. Create a dedicated virtual environment and install the package:

   ```text
   py -3 -m venv .venv
   .venv\Scripts\python.exe -m pip install .
   ```

3. Test the publisher from a prompt without exposing the credential:

   ```text
   .venv\Scripts\python.exe -m finmonitor.publisher --status-file "<LOCAL_BACKUP_STATUS_JSON>" --repository "<OWNER>/finmentor-staging-monitor" --issue-number <BACKUP_ISSUE_NUMBER>
   ```

4. Inspect the public issue. It must contain exactly `version`, `status`,
   `last_success_at`, `checked_at`, and `error_type`.
5. Create a new Windows Task Scheduler task named
   `FinMentor Staging Safe Backup Status Publisher`:
   - trigger: daily, repeat every 3 hours indefinitely;
   - program: absolute path to `.venv\Scripts\python.exe`;
   - arguments: the same `-m finmonitor.publisher ...` arguments;
   - start in: repository directory;
   - run whether the user is logged on or not;
   - do not store the token in the task action.
6. Run the task once and verify `Last Run Result = 0x0`.

If the PC is off, publication stops, but the cloud monitor continues and raises
`backup.heartbeat_stale`; it also independently checks the age of the last confirmed
successful backup.

## Gmail SMTP App Password

Use a dedicated sender mailbox when possible.

1. Enable two-step verification on the Google account.
2. Open Google Account **Security → App passwords**.
3. Create an app password named `FinMentor staging monitor`.
4. In GitHub repository **Settings → Secrets and variables → Actions**, create:
   - `SMTP_USERNAME`: sender mailbox;
   - `SMTP_APP_PASSWORD`: generated app password;
   - `ALERT_EMAIL_TO`: owner mailbox.
5. Never paste the app password into chat, issues, workflow inputs, logs, or files.
6. Revoke the app password if the repository or mailbox is compromised.

## Controlled acceptance

Run **Actions → staging-reliability → Run workflow** with:

- mode: `acceptance_cycle`;
- confirmation: `SEND TEST ALERTS`.

Expected behavior is four messages and nine green state steps. Then verify:

- first failure email received;
- no duplicate before cooldown;
- one cooldown reminder;
- one recovery email;
- one new failure email after recovery;
- acceptance issue revisions demonstrate persistence between every step.

The cycle uses only artificial observations and the separate acceptance issue. It
does not stop backup, change staging, or alter the real monitor state.

## Routine checks

- Weekly: confirm scheduled runs are present and not consistently delayed.
- Monthly: confirm the keepalive workflow succeeded.
- Before token expiry: rotate the local publisher token.
- After any incident: review only safe workflow summaries and issue state; never add
  raw application logs to this public repository.
