from __future__ import annotations

import unittest
from datetime import datetime, timezone

from finmonitor.engine import Event
from finmonitor.mailer import SMTPConfig, build_message
from finmonitor.probes import validate_staging_base_url


class SecurityTests(unittest.TestCase):
    def test_probe_rejects_non_staging_and_unsafe_urls(self) -> None:
        rejected = [
            "http://service-staging.example.invalid",
            "https://service.example.invalid",
            "https://user:pass@service-staging.example.invalid",
            "https://service-staging.example.invalid?token=secret",
            "https://127.0.0.1/staging",
        ]
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_staging_base_url(value)
        self.assertEqual(
            validate_staging_base_url("https://service-staging.example.invalid/"),
            "https://service-staging.example.invalid",
        )

    def test_email_contains_only_safe_event_metadata(self) -> None:
        event = Event(
            event_id="a" * 32,
            fingerprint="synthetics",
            state="failure",
            status="frontend=http_500",
            error_type="frontend.http_status",
            occurred_at="2026-01-01T00:00:00Z",
        )
        config = SMTPConfig("sender@example.invalid", "app-password-secret", "owner@example.invalid")
        message = build_message(config, [event])
        rendered = message.as_string()
        self.assertNotIn("app-password-secret", rendered)
        self.assertNotIn("Authorization", rendered)
        self.assertEqual(message["Subject"], "[ALERT][FinMentor Staging] Reliability check failed")

    def test_message_id_is_idempotent_for_same_event(self) -> None:
        event = Event("b" * 32, "synthetics", "recovered", "healthy", "none", "2026-01-01T00:00:00Z")
        config = SMTPConfig("sender@example.invalid", "secret", "owner@example.invalid")
        self.assertEqual(build_message(config, [event])["Message-ID"], build_message(config, [event])["Message-ID"])


if __name__ == "__main__":
    unittest.main()
