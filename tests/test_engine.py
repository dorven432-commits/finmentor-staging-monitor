from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from finmonitor.engine import (
    Observation,
    evaluate,
    mark_events_failed,
    mark_events_sent,
    new_state,
    prune_outbox,
    reserve_events,
)


BASE = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


class EngineTests(unittest.TestCase):
    def test_healthy_never_notifies(self) -> None:
        state = new_state()
        events = evaluate(state, [Observation("synthetics", False, "healthy", "none")], now=BASE)
        self.assertEqual(events, [])

    def test_two_failures_duplicate_suppression_cooldown_and_recovery(self) -> None:
        state = new_state()
        failed = Observation("synthetics", True, "failed", "timeout", confirm_after=2)
        healthy = Observation("synthetics", False, "healthy", "none", confirm_after=2)

        self.assertEqual(evaluate(state, [failed], now=BASE), [])
        first = evaluate(state, [failed], now=BASE + timedelta(minutes=15))
        self.assertEqual([event.state for event in first], ["failure"])
        reserve_events(state, first, now=BASE + timedelta(minutes=15))
        mark_events_sent(state, first, now=BASE + timedelta(minutes=15))

        self.assertEqual(evaluate(state, [failed], now=BASE + timedelta(hours=1)), [])
        reminder = evaluate(state, [failed], now=BASE + timedelta(hours=12, minutes=15))
        self.assertEqual([event.state for event in reminder], ["failure"])
        reserve_events(state, reminder, now=BASE + timedelta(hours=12, minutes=15))
        mark_events_sent(state, reminder, now=BASE + timedelta(hours=12, minutes=15))

        recovered = evaluate(state, [healthy], now=BASE + timedelta(hours=12, minutes=30))
        self.assertEqual([event.state for event in recovered], ["recovered"])
        reserve_events(state, recovered, now=BASE + timedelta(hours=12, minutes=30))
        mark_events_sent(state, recovered, now=BASE + timedelta(hours=12, minutes=30))
        self.assertEqual(evaluate(state, [healthy], now=BASE + timedelta(hours=13)), [])

        self.assertEqual(evaluate(state, [failed], now=BASE + timedelta(hours=13, minutes=15)), [])
        again = evaluate(state, [failed], now=BASE + timedelta(hours=13, minutes=30))
        self.assertEqual([event.state for event in again], ["failure"])

    def test_explicit_send_failure_is_retried(self) -> None:
        state = new_state()
        failed = Observation("backup.attempt_failed", True, "failed", "backup_failed")
        events = evaluate(state, [failed], now=BASE)
        reserve_events(state, events, now=BASE)
        mark_events_failed(state, events)
        retry = evaluate(state, [failed], now=BASE + timedelta(minutes=1))
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0].state, "failure")

    def test_event_ids_are_stable_for_same_transition(self) -> None:
        observation = Observation("synthetics", True, "failed", "timeout")
        first = evaluate(new_state(), [observation], now=BASE)[0]
        second = evaluate(new_state(), [observation], now=BASE)[0]
        self.assertEqual(first.event_id, second.event_id)

    def test_outbox_is_bounded_but_keeps_in_flight_items(self) -> None:
        state = new_state()
        state["outbox"] = {
            f"sent-{index}": {
                "event_id": f"sent-{index}",
                "status": "sent",
                "created_at": f"2026-01-01T00:{index:02d}:00Z",
                "sent_at": f"2026-01-01T00:{index:02d}:00Z",
            }
            for index in range(10)
        }
        state["outbox"]["sending"] = {
            "event_id": "sending",
            "status": "sending",
            "created_at": "2025-01-01T00:00:00Z",
            "sent_at": None,
        }
        prune_outbox(state, keep_terminal=3)
        self.assertEqual(len(state["outbox"]), 4)
        self.assertIn("sending", state["outbox"])


if __name__ == "__main__":
    unittest.main()
