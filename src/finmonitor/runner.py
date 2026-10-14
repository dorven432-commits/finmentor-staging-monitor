"""Cloud runner for staging synthetics, backup freshness and email transitions."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from .engine import (
    Observation,
    evaluate,
    iso_utc,
    mark_events_failed,
    mark_events_sent,
    parse_timestamp,
    reserve_events,
    prune_outbox,
    safe_value,
)
from .github_issues import (
    BACKUP_MARKER,
    GitHubIssueClient,
    IncidentStateStore,
    StoreError,
    parse_json_body,
)
from .mailer import SMTPConfig, send
from .probes import ProbeResult, run_staging_probes


BACKUP_FIELDS = {"version", "status", "last_success_at", "checked_at", "error_type"}


def _synthetic_observation(state: dict[str, Any], results: list[ProbeResult]) -> Observation:
    current_ok = True
    confirmed = False
    parts: list[str] = []
    errors: list[str] = []
    for result in results:
        count = 0 if result.ok else int(state["synthetic_failures"].get(result.name, 0)) + 1
        state["synthetic_failures"][result.name] = count
        current_ok = current_ok and result.ok
        confirmed = confirmed or count >= 2
        parts.append(f"{result.name}={result.status}")
        if result.error_type != "none":
            errors.append(f"{result.name}.{result.error_type}")
    active = bool(state["incidents"].get("synthetics", {}).get("active"))
    failing = (not current_ok) if active else confirmed
    return Observation(
        "synthetics",
        failing,
        safe_value(" ".join(parts)),
        safe_value("+".join(errors) if errors else "none"),
    )


def _backup_due(state: dict[str, Any], now: datetime) -> bool:
    value = state.get("last_backup_check_at")
    if not value:
        return True
    try:
        return now.astimezone(timezone.utc) - parse_timestamp(value) >= timedelta(hours=3)
    except ValueError:
        return True


def validate_backup_source(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != BACKUP_FIELDS or value.get("version") != 1:
        raise ValueError("backup source fields are invalid")
    if value.get("status") not in {"succeeded", "failed"}:
        raise ValueError("backup status is invalid")
    parse_timestamp(value.get("checked_at"))
    if value.get("last_success_at") is not None:
        parse_timestamp(value.get("last_success_at"))
    value["error_type"] = safe_value(value.get("error_type"), fallback="unknown")
    return value


def _backup_observations(
    source: dict[str, Any], now: datetime
) -> list[Observation]:
    checked_at = parse_timestamp(source["checked_at"])
    last_success_raw = source.get("last_success_at")
    last_success = parse_timestamp(last_success_raw) if last_success_raw else None
    future_limit = now.astimezone(timezone.utc) + timedelta(minutes=10)
    if checked_at > future_limit or (last_success is not None and last_success > future_limit):
        return [
            Observation("backup.source_unavailable", False, "available", "none"),
            Observation("backup.timestamp_invalid", True, "invalid", "timestamp_in_future"),
        ]
    heartbeat_stale = now.astimezone(timezone.utc) - checked_at > timedelta(hours=4)
    success_stale = last_success is None or now.astimezone(timezone.utc) - last_success > timedelta(hours=26)
    return [
        Observation("backup.source_unavailable", False, "available", "none"),
        Observation("backup.timestamp_invalid", False, "valid", "none"),
        Observation(
            "backup.attempt_failed",
            source["status"] == "failed",
            safe_value(source["status"]),
            source["error_type"],
        ),
        Observation(
            "backup.heartbeat_stale",
            heartbeat_stale,
            "stale" if heartbeat_stale else "current",
            "heartbeat_missing" if heartbeat_stale else "none",
        ),
        Observation(
            "backup.success_stale",
            success_stale,
            "missing_or_stale" if success_stale else "current",
            "last_success_missing" if last_success is None else ("last_success_stale" if success_stale else "none"),
        ),
    ]


def _load_backup_observations(
    client: GitHubIssueClient, issue_number: int, now: datetime
) -> list[Observation]:
    try:
        issue = client.read_issue(issue_number)
        raw = parse_json_body(issue.get("body"), BACKUP_MARKER)
    except StoreError:
        return [Observation("backup.source_unavailable", True, "unavailable", "source_unavailable")]
    try:
        source = validate_backup_source(raw)
    except ValueError:
        return [
            Observation("backup.source_unavailable", False, "available", "none"),
            Observation("backup.timestamp_invalid", True, "invalid", "timestamp_invalid"),
        ]
    return _backup_observations(source, now)


def _smtp_config() -> SMTPConfig:
    return SMTPConfig(
        username=os.environ.get("SMTP_USERNAME", ""),
        password=os.environ.get("SMTP_APP_PASSWORD", ""),
        recipient=os.environ.get("ALERT_EMAIL_TO", ""),
    )


def run() -> int:
    now = datetime.now(timezone.utc)
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("MONITOR_GITHUB_TOKEN", "")
    state_issue = int(os.environ.get("STATE_ISSUE_NUMBER", "0"))
    backup_issue = int(os.environ.get("BACKUP_STATUS_ISSUE_NUMBER", "0"))
    if not state_issue or not backup_issue:
        raise RuntimeError("monitor issue numbers are unavailable")
    client = GitHubIssueClient(repository, token)
    store = IncidentStateStore(client, state_issue)
    state = store.load()
    revision = state["revision"]

    results = run_staging_probes(
        os.environ.get("STAGING_FRONTEND_URL", ""),
        os.environ.get("STAGING_BACKEND_URL", ""),
    )
    observations = [_synthetic_observation(state, results)]
    backup_checked = _backup_due(state, now)
    if backup_checked:
        observations.extend(_load_backup_observations(client, backup_issue, now))
        state["last_backup_check_at"] = iso_utc(now)

    events = evaluate(state, observations, now=now, cooldown=timedelta(hours=12))
    if events:
        reserve_events(state, events, now=now)
    prune_outbox(state)
    store.save(state, expected_revision=revision)

    if events:
        revision = state["revision"]
        try:
            send(_smtp_config(), events)
        except Exception:
            mark_events_failed(state, events)
            prune_outbox(state)
            store.save(state, expected_revision=revision)
            raise RuntimeError("notification transport failed") from None
        mark_events_sent(state, events, now=datetime.now(timezone.utc))
        prune_outbox(state)
        store.save(state, expected_revision=revision)

    print(
        json.dumps(
            {
                "ok": True,
                "synthetics_ok": all(result.ok for result in results),
                "backup_checked": backup_checked,
                "notifications": len(events),
            }
        )
    )
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
