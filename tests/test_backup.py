from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from finmonitor.publisher import PUBLIC_FIELDS, safe_projection
from finmonitor.runner import _backup_due, _backup_observations, validate_backup_source


BASE = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)


class BackupTests(unittest.TestCase):
    def test_projection_has_only_allowlisted_fields(self) -> None:
        local = {
            "status": "succeeded",
            "completed_at": "2026-01-02T11:00:00Z",
            "archive_path": "private/path/archive.dump",
            "checksum": "secret-checksum",
            "jwt": "secret-token",
            "email": "person@example.invalid",
        }
        projection = safe_projection(local, previous_last_success=None, checked_at=BASE)
        self.assertEqual(set(projection), PUBLIC_FIELDS)
        encoded = repr(projection)
        for forbidden in ("archive.dump", "secret-checksum", "secret-token", "person@"):
            self.assertNotIn(forbidden, encoded)

    def test_failed_attempt_preserves_previous_success(self) -> None:
        projection = safe_projection(
            {"status": "failed", "error_type": "pg_dump_failed", "details": "do not publish"},
            previous_last_success="2026-01-02T01:00:00Z",
            checked_at=BASE,
        )
        self.assertEqual(projection["status"], "failed")
        self.assertEqual(projection["last_success_at"], "2026-01-02T01:00:00Z")
        self.assertEqual(projection["error_type"], "pg_dump_failed")

    def test_invalid_success_timestamp_becomes_safe_failure(self) -> None:
        projection = safe_projection(
            {"status": "succeeded", "completed_at": "not-a-time"},
            previous_last_success="2026-01-01T00:00:00Z",
            checked_at=BASE,
        )
        self.assertEqual(projection["status"], "failed")
        self.assertEqual(projection["error_type"], "timestamp_invalid")

    def test_backup_due_every_three_hours(self) -> None:
        state = {"last_backup_check_at": "2026-01-02T09:01:00Z"}
        self.assertFalse(_backup_due(state, BASE))
        state["last_backup_check_at"] = "2026-01-02T09:00:00Z"
        self.assertTrue(_backup_due(state, BASE))

    def test_heartbeat_and_success_age_are_independent(self) -> None:
        source = validate_backup_source(
            {
                "version": 1,
                "status": "succeeded",
                "last_success_at": "2026-01-01T09:00:00Z",
                "checked_at": "2026-01-02T11:00:00Z",
                "error_type": "none",
            }
        )
        results = {item.fingerprint: item for item in _backup_observations(source, BASE)}
        self.assertFalse(results["backup.heartbeat_stale"].failing)
        self.assertTrue(results["backup.success_stale"].failing)

    def test_future_timestamp_is_invalid(self) -> None:
        source = validate_backup_source(
            {
                "version": 1,
                "status": "succeeded",
                "last_success_at": "2026-01-03T12:00:00Z",
                "checked_at": "2026-01-03T12:00:00Z",
                "error_type": "none",
            }
        )
        results = {item.fingerprint: item for item in _backup_observations(source, BASE)}
        self.assertTrue(results["backup.timestamp_invalid"].failing)
        self.assertEqual(results["backup.timestamp_invalid"].error_type, "timestamp_in_future")


if __name__ == "__main__":
    unittest.main()
