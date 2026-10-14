# Security policy

## Allowed data

Only generic endpoint names (`frontend`, `health`, `readiness`), HTTP result classes,
short error types, timestamps, counters, and incident transition fields may be
stored in workflow logs or GitHub Issues.

## Forbidden data

Never commit or publish endpoint URLs, query strings, credentials, JWTs, API keys,
SMTP passwords, email addresses, user identifiers, personal or financial data,
backup archives, archive names, local paths, checksums, database details, or raw
tracebacks.

## Credentials

- GitHub Actions credentials belong only in repository Actions secrets.
- The workflow uses the ephemeral `GITHUB_TOKEN` with `contents: read` and
  `issues: write`; it uses no service-role key.
- The local publisher token must be a fine-grained token limited to this repository
  with Issues read/write and no access to the application repository.
- Store the local publisher token in Windows Credential Manager under
  `FinMentor/Staging/MonitorPublishToken`.
- Rotate the fine-grained token before its expiry and revoke it immediately if the
  workstation is lost or compromised.

The monitor validates that all probe URLs use HTTPS, have no credentials, query, or
fragment, and contain `staging` in the hostname. This is a production safety stop,
not a substitute for reviewing the configured secrets.

## Logging

Top-level commands emit only boolean results, counts, and exception class names.
Notification bodies are generated exclusively from validated safe event fields.
Do not enable shell tracing or print environment variables in workflows.
