"""Explicit, isolated notification acceptance cycle.

This module never uses the production incident-state issue. The workflow requires
an exact manual confirmation and a dedicated acceptance-state issue.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from .engine import (
    Observation,
    evaluate,
    mark_events_failed,
    mark_events_sent,
    new_state,
    prune_outbox,
    reserve_events,
)
from .github_issues import GitHubIssueClient, IncidentStateStore
from .mailer import SMTPConfig, send


def _smtp_config() -> SMTPConfig:
    return SMTPConfig(
        username=os.environ.get("SMTP_USERNAME", ""),
        password=os.environ.get("SMTP_APP_PASSWORD", ""),
        recipient=os.environ.get("ALERT_EMAIL_TO", ""),
    )


def _persist_step(
    store: IncidentStateStore,
    observation: Observation,
    *,
    now: datetime,
) -> int:
    state = store.load()
    revision = state["revision"]
    events = evaluate(state, [observation], now=now, cooldown=timedelta(hours=12))
    if events:
        reserve_events(state, events, now=now)
    prune_outbox(state)
    store.save(state, expected_revision=revision)
    if not events:
        return 0

    revision = state["revision"]
    try:
        send(_smtp_config(), events)
    except Exception:
        mark_events_failed(state, events)
        prune_outbox(state)
        store.save(state, expected_revision=revision)
        raise RuntimeError("acceptance notification failed") from None
    mark_events_sent(state, events, now=now)
    prune_outbox(state)
    store.save(state, expected_revision=revision)
    return len(events)


def run() -> int:
    if os.environ.get("ACCEPTANCE_CONFIRMATION") != "SEND TEST ALERTS":
        raise RuntimeError("acceptance confirmation is missing")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("MONITOR_GITHUB_TOKEN", "")
    issue_number = int(os.environ.get("ACCEPTANCE_STATE_ISSUE_NUMBER", "0"))
    if not issue_number:
        raise RuntimeError("acceptance state issue is unavailable")

    store = IncidentStateStore(GitHubIssueClient(repository, token), issue_number)
    current = store.load()
    reset = new_state()
    store.save(reset, expected_revision=current["revision"])

    # Use a logical clock ending close to the real invocation time so the
    # test messages never claim to originate in the future.
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=13)
    healthy = Observation("acceptance.synthetic", False, "healthy", "none", confirm_after=2)
    failed = Observation("acceptance.synthetic", True, "failed", "test_failure", confirm_after=2)
    steps = [
        (healthy, base),
        (failed, base + timedelta(minutes=1)),
        (failed, base + timedelta(minutes=2)),
        (failed, base + timedelta(minutes=3)),
        (failed, base + timedelta(hours=12, minutes=3)),
        (healthy, base + timedelta(hours=12, minutes=4)),
        (healthy, base + timedelta(hours=12, minutes=5)),
        (failed, base + timedelta(hours=12, minutes=6)),
        (failed, base + timedelta(hours=12, minutes=7)),
    ]
    notifications = [_persist_step(store, observation, now=when) for observation, when in steps]
    expected = [0, 0, 1, 0, 1, 1, 0, 0, 1]
    if notifications != expected:
        raise RuntimeError("acceptance transition sequence failed")
    print(json.dumps({"ok": True, "notifications": sum(notifications), "steps": len(steps)}))
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
