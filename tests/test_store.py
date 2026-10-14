from __future__ import annotations

import copy
import unittest

from finmonitor.engine import new_state
from finmonitor.github_issues import (
    STATE_MARKER,
    IncidentStateStore,
    StoreError,
    parse_json_body,
    render_json_body,
)


class FakeClient:
    def __init__(self) -> None:
        self.body = render_json_body(STATE_MARKER, new_state())

    def read_issue(self, issue_number: int) -> dict[str, str]:
        return {"body": self.body}

    def update_issue_body(self, issue_number: int, body: str) -> None:
        self.body = body


class StoreTests(unittest.TestCase):
    def test_round_trip_survives_store_recreation(self) -> None:
        client = FakeClient()
        first_store = IncidentStateStore(client, 1)
        state = first_store.load()
        state["last_scheduler_run_at"] = "2026-01-01T00:00:00Z"
        first_store.save(state, expected_revision=0)
        second_store = IncidentStateStore(client, 1)
        loaded = second_store.load()
        self.assertEqual(loaded["revision"], 1)
        self.assertEqual(loaded["last_scheduler_run_at"], "2026-01-01T00:00:00Z")

    def test_stale_revision_is_rejected(self) -> None:
        client = FakeClient()
        store = IncidentStateStore(client, 1)
        original = store.load()
        first = copy.deepcopy(original)
        store.save(first, expected_revision=0)
        with self.assertRaises(StoreError):
            store.save(original, expected_revision=0)

    def test_parser_requires_marker(self) -> None:
        with self.assertRaises(StoreError):
            parse_json_body("```json\n{}\n```", STATE_MARKER)


if __name__ == "__main__":
    unittest.main()
