"""Publish a strict safe projection of the local backup status to GitHub Issues."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import iso_utc, parse_timestamp, safe_value
from .github_issues import (
    BACKUP_MARKER,
    GitHubIssueClient,
    StoreError,
    parse_json_body,
    render_json_body,
)
from .windows_credentials import read_generic_credential


PUBLISH_CREDENTIAL = "FinMentor/Staging/MonitorPublishToken"
PUBLIC_FIELDS = {"version", "status", "last_success_at", "checked_at", "error_type"}


def _previous_success(client: GitHubIssueClient, issue_number: int) -> str | None:
    try:
        current = parse_json_body(client.read_issue(issue_number).get("body"), BACKUP_MARKER)
        value = current.get("last_success_at")
        if value is not None:
            parse_timestamp(value)
            return value
    except (StoreError, ValueError):
        return None
    return None


def safe_projection(
    local_status: object,
    *,
    previous_last_success: str | None,
    checked_at: datetime,
) -> dict[str, Any]:
    status = "failed"
    error_type = "status_json_invalid"
    last_success = previous_last_success
    if isinstance(local_status, dict):
        candidate = local_status.get("status")
        if candidate in {"succeeded", "failed"}:
            status = candidate
            if status == "succeeded":
                try:
                    completed = parse_timestamp(local_status.get("completed_at"))
                    last_success = iso_utc(completed)
                    error_type = "none"
                except ValueError:
                    status = "failed"
                    error_type = "timestamp_invalid"
            else:
                error_type = safe_value(local_status.get("error_type"), fallback="backup_failed")
    projection = {
        "version": 1,
        "status": status,
        "last_success_at": last_success,
        "checked_at": iso_utc(checked_at),
        "error_type": error_type,
    }
    if set(projection) != PUBLIC_FIELDS:
        raise RuntimeError("unsafe backup projection")
    return projection


def load_local_status(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--credential-target", default=PUBLISH_CREDENTIAL)
    args = parser.parse_args()
    token = os.getenv("MONITOR_PUBLISH_TOKEN") or read_generic_credential(args.credential_target)
    client = GitHubIssueClient(args.repository, token)
    projection = safe_projection(
        load_local_status(args.status_file),
        previous_last_success=_previous_success(client, args.issue_number),
        checked_at=datetime.now(timezone.utc),
    )
    client.update_issue_body(
        args.issue_number, render_json_body(BACKUP_MARKER, projection)
    )
    print(json.dumps({"published": True, "status": projection["status"]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"published": False, "error_type": type(exc).__name__}))
        raise SystemExit(2) from None
