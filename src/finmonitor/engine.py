"""Persistent incident transitions with confirmation, cooldown and recovery."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


SAFE_TOKEN = re.compile(r"^[a-zA-Z0-9_.:=+ -]{1,160}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp is missing")
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def safe_value(value: object, *, fallback: str = "unknown") -> str:
    text = str(value or fallback).strip()
    if not SAFE_TOKEN.fullmatch(text):
        return fallback
    return text


def new_state() -> dict[str, Any]:
    return {
        "version": 1,
        "revision": 0,
        "last_scheduler_run_at": None,
        "last_backup_check_at": None,
        "synthetic_failures": {},
        "incidents": {},
        "outbox": {},
    }


def validate_state(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("unsupported incident state")
    required = {
        "version",
        "revision",
        "last_scheduler_run_at",
        "last_backup_check_at",
        "synthetic_failures",
        "incidents",
        "outbox",
    }
    if set(value) != required:
        raise ValueError("incident state fields are invalid")
    if not isinstance(value["revision"], int) or value["revision"] < 0:
        raise ValueError("incident state revision is invalid")
    if not isinstance(value["synthetic_failures"], dict):
        raise ValueError("synthetic counters are invalid")
    if not isinstance(value["incidents"], dict) or not isinstance(value["outbox"], dict):
        raise ValueError("incident state maps are invalid")
    return value


@dataclass(frozen=True)
class Observation:
    fingerprint: str
    failing: bool
    status: str
    error_type: str
    confirm_after: int = 1

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9_.-]{1,80}", self.fingerprint):
            raise ValueError("unsafe incident fingerprint")
        if self.confirm_after < 1:
            raise ValueError("confirm_after must be positive")
        if safe_value(self.status) != self.status or safe_value(self.error_type) != self.error_type:
            raise ValueError("unsafe observation metadata")


@dataclass(frozen=True)
class Event:
    event_id: str
    fingerprint: str
    state: str
    status: str
    error_type: str
    occurred_at: str


def _incident(fingerprint: str) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint,
        "active": False,
        "first_seen": None,
        "last_seen": None,
        "last_sent": None,
        "recovered_at": None,
        "consecutive_failures": 0,
        "candidate_first_seen": None,
        "status": "unknown",
        "error_type": "none",
    }


def _event_id(fingerprint: str, state: str, occurred_at: str) -> str:
    raw = f"{fingerprint}|{state}|{occurred_at}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def evaluate(
    state: dict[str, Any],
    observations: Iterable[Observation],
    *,
    now: datetime,
    cooldown: timedelta = timedelta(hours=12),
) -> list[Event]:
    """Apply observations in-place and return notification transitions."""

    validate_state(state)
    stamp = iso_utc(now)
    events: list[Event] = []
    for observation in observations:
        current = state["incidents"].setdefault(
            observation.fingerprint, _incident(observation.fingerprint)
        )
        current["status"] = safe_value(observation.status)
        current["error_type"] = safe_value(observation.error_type, fallback="none")
        if observation.failing:
            current["consecutive_failures"] += 1
            current["last_seen"] = stamp
            if current["candidate_first_seen"] is None:
                current["candidate_first_seen"] = stamp
            if not current["active"] and current["consecutive_failures"] >= observation.confirm_after:
                current["active"] = True
                current["first_seen"] = current["candidate_first_seen"]
                current["recovered_at"] = None
                events.append(
                    Event(
                        _event_id(observation.fingerprint, "failure", stamp),
                        observation.fingerprint,
                        "failure",
                        current["status"],
                        current["error_type"],
                        stamp,
                    )
                )
            elif current["active"]:
                should_send = current["last_sent"] is None
                if current["last_sent"] is not None:
                    last_sent = parse_timestamp(current["last_sent"])
                    should_send = now.astimezone(timezone.utc) - last_sent >= cooldown
                if should_send:
                    events.append(
                        Event(
                            _event_id(observation.fingerprint, "failure", stamp),
                            observation.fingerprint,
                            "failure",
                            current["status"],
                            current["error_type"],
                            stamp,
                        )
                    )
        else:
            current["consecutive_failures"] = 0
            current["candidate_first_seen"] = None
            current["last_seen"] = stamp
            if current["active"]:
                current["active"] = False
                current["recovered_at"] = stamp
                events.append(
                    Event(
                        _event_id(observation.fingerprint, "recovered", stamp),
                        observation.fingerprint,
                        "recovered",
                        current["status"],
                        current["error_type"],
                        stamp,
                    )
                )
    state["last_scheduler_run_at"] = stamp
    return events


def reserve_events(state: dict[str, Any], events: Iterable[Event], *, now: datetime) -> None:
    """Persist deterministic event IDs before SMTP to limit duplicate delivery."""

    stamp = iso_utc(now)
    for event in events:
        incident = state["incidents"][event.fingerprint]
        incident["last_sent"] = stamp
        state["outbox"][event.event_id] = {
            "event_id": event.event_id,
            "status": "sending",
            "created_at": stamp,
            "sent_at": None,
        }


def mark_events_sent(state: dict[str, Any], events: Iterable[Event], *, now: datetime) -> None:
    stamp = iso_utc(now)
    for event in events:
        item = state["outbox"].get(event.event_id)
        if item:
            item["status"] = "sent"
            item["sent_at"] = stamp


def mark_events_failed(state: dict[str, Any], events: Iterable[Event]) -> None:
    """Allow a later retry after an explicit SMTP failure."""

    for event in events:
        item = state["outbox"].get(event.event_id)
        if item:
            item["status"] = "failed"
        incident = state["incidents"].get(event.fingerprint)
        if incident:
            incident["last_sent"] = None


def prune_outbox(state: dict[str, Any], *, keep_terminal: int = 200) -> None:
    """Bound public state growth while never discarding in-flight reservations."""

    validate_state(state)
    sending = {
        key: value
        for key, value in state["outbox"].items()
        if isinstance(value, dict) and value.get("status") == "sending"
    }
    terminal = [
        (key, value)
        for key, value in state["outbox"].items()
        if key not in sending and isinstance(value, dict)
    ]
    terminal.sort(key=lambda item: str(item[1].get("created_at") or ""), reverse=True)
    state["outbox"] = {**sending, **dict(terminal[:keep_terminal])}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
