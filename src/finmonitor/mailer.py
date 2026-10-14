"""Minimal SMTP transport with deterministic Message-ID values."""

from __future__ import annotations

import hashlib
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Iterable

from .engine import Event, safe_value


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@dataclass(frozen=True)
class SMTPConfig:
    username: str
    password: str
    recipient: str
    host: str = "smtp.gmail.com"
    port: int = 465

    def validate(self) -> None:
        if not EMAIL_RE.fullmatch(self.username) or not EMAIL_RE.fullmatch(self.recipient):
            raise ValueError("SMTP address is invalid")
        if not self.password:
            raise ValueError("SMTP credential is unavailable")
        if self.host != "smtp.gmail.com" or self.port != 465:
            raise ValueError("unexpected SMTP endpoint")


def _message_id(events: Iterable[Event]) -> str:
    ids = sorted(event.event_id for event in events)
    return hashlib.sha256("|".join(ids).encode("ascii")).hexdigest()[:32]


def build_message(config: SMTPConfig, events: list[Event]) -> EmailMessage:
    config.validate()
    if not events:
        raise ValueError("email requires at least one event")
    has_failure = any(event.state == "failure" for event in events)
    subject = (
        "[ALERT][FinMentor Staging] Reliability check failed"
        if has_failure
        else "[RECOVERED][FinMentor Staging] Reliability restored"
    )
    lines = ["FinMentor staging reliability event", ""]
    # Moscow has used fixed UTC+03:00 since 2014. A fixed offset keeps the
    # monitor dependency-free on both GitHub Linux runners and Windows.
    moscow = timezone(timedelta(hours=3), name="MSK")
    for event in sorted(events, key=lambda item: item.fingerprint):
        occurred = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00")).astimezone(moscow)
        lines.extend(
            [
                f"check: {safe_value(event.fingerprint)}",
                f"time_msk: {occurred.isoformat(timespec='seconds')}",
                f"status: {safe_value(event.status)}",
                f"error_type: {safe_value(event.error_type, fallback='none')}",
                f"state: {event.state}",
                "",
            ]
        )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.username
    message["To"] = config.recipient
    message["Message-ID"] = f"<{_message_id(events)}@finmentor-monitor.invalid>"
    message.set_content("\n".join(lines))
    return message


def send(config: SMTPConfig, events: list[Event]) -> None:
    message = build_message(config, events)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(config.host, config.port, timeout=30, context=context) as smtp:
        smtp.login(config.username, config.password)
        smtp.send_message(message)
