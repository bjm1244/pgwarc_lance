#!/usr/bin/env python3
"""Deterministic contract tests for the pure timeout state decision.

All inputs are hand-authored synthetic events following the
``quality_attempt_identity.v1`` and ``timeout.timeout_observation`` repo
conventions. No DB, backend PID, cancellation, rollback, or cleanup runtime is
involved; these tests prove the pure decision contract only.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def load_module(name: str, path: pathlib.Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


load_module("doctor_quality_attempt_identity", TOOLS / "doctor_quality_attempt_identity.py")
load_module("eval_quality", TOOLS / "eval_quality.py")
decision = load_module(
    "quality_timeout_state_decision", TOOLS / "quality_timeout_state_decision.py"
)

SESSION = {"backend_pid": 4242, "backend_start": "2026-09-15T06:04:00Z"}

ATTEMPT_ENVELOPE = {
    "schema_version": 1,
    "attempt_id": "attempt-1",
    "capture_state": "captured",
    "session_identity": SESSION,
    "transaction_identity": {"state": "unobserved"},
    "capture_source": "query_session",
}

OBSERVATION = {
    "statement_outcome": "server_statement_timeout_reported",
    "backend_state_after_timeout": "unobserved",
    "cancellation_completion": "unverified",
    "transaction_cleanup": "unverified",
}


def event(event_id, event_seq, kind, **extra):
    payload = {"event_id": event_id, "event_seq": event_seq, "kind": kind}
    if "attempt" not in extra and kind != "attempt_recorded":
        payload["attempt_id"] = "attempt-1"
    payload.update(extra)
    return payload


def attempt(seq=0, envelope=None):
    return event(
        "attempt.recorded",
        seq,
        "attempt_recorded",
        attempt=envelope or dict(ATTEMPT_ENVELOPE),
    )


def expected_state(events, **overrides):
    result = decision.decide_quality_timeout_state(events)
    for key, value in overrides.items():
        result.pop(key, None)
        result[key] = value
    return result


class TimeoutStateDecisionTests(unittest.TestCase):
    def test_timeout_only_is_not_termination(self):
        result = decision.decide_quality_timeout_state(
            [
                attempt(),
                event("t1", 1, "timeout_observed", timeout_observation=dict(OBSERVATION)),
            ]
        )
        self.assertEqual(result["state"], "timeout_recorded_backend_unobserved")
        self.assertTrue(result["timeout_recorded"])
        self.assertFalse(result["backend_termination_confirmed"])
        self.assertFalse(result["cleanup_confirmed"])
        self.assertEqual(result["backend_state_after_timeout"], "unobserved")

    def test_natural_completion_after_timeout_supersedes_timeout(self):
        events = [
            attempt(),
            event("t1", 1, "timeout_observed", timeout_observation=dict(OBSERVATION)),
            event("c1", 2, "query_completed"),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertEqual(result["state"], "statement_completed")
        self.assertFalse(result["timeout_recorded"])
        self.assertFalse(result["backend_termination_confirmed"])

    def test_natural_completion_before_timeout_then_superseded(self):
        events = [
            attempt(),
            event("c1", 1, "query_completed"),
            event("t1", 2, "timeout_observed", timeout_observation=dict(OBSERVATION)),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertEqual(result["state"], "timeout_recorded_backend_unobserved")
        self.assertFalse(result["statement_completed"])

    def test_cancellation_accepted_but_backend_still_running(self):
        events = [
            attempt(),
            event("cr1", 1, "cancellation_requested"),
            event("ca1", 2, "cancellation_accepted"),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertEqual(result["state"], "cancellation_accepted_not_confirmed")
        self.assertTrue(result["cancellation_request_accepted"])
        self.assertFalse(result["backend_termination_confirmed"])
        self.assertEqual(result["backend_state_after_timeout"], "unobserved")

    def test_cancellation_acceptance_without_request_is_rejected(self):
        result = decision.decide_quality_timeout_state(
            [attempt(), event("ca1", 1, "cancellation_accepted")]
        )
        self.assertEqual(result["state"], "indeterminate_no_claims")
        self.assertIn(
            "quality_timeout_state.events[1] has no preceding cancellation_requested event", result["errors"]
        )

    def test_confirmed_termination_then_explicit_cleanup_evidence(self):
        events = [
            attempt(),
            event(
                "term1",
                1,
                "backend_termination_reported",
                session_identity=dict(SESSION),
            ),
            event(
                "cln1", 2, "cleanup_completed", session_identity=dict(SESSION)
            ),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertEqual(result["state"], "cleanup_confirmed")
        self.assertTrue(result["cleanup_confirmed"])
        self.assertEqual(result["backend_state_after_timeout"], "cleanup_confirmed")

    def test_identity_mismatch_makes_decision_indeterminate(self):
        events = [
            attempt(),
            event(
                "term1",
                1,
                "backend_termination_reported",
                session_identity={"backend_pid": 999, "backend_start": SESSION["backend_start"]},
            ),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertEqual(result["state"], "indeterminate_no_claims")
        self.assertIn(
            "quality_timeout_state.events[1].session_identity does not match the recorded attempt identity",
            result["errors"],
        )
        self.assertFalse(result["backend_termination_confirmed"])

    def test_missing_identity_on_termination_cannot_confirm(self):
        events = [
            attempt(),
            event("term1", 1, "backend_termination_reported"),
            event("cln1", 2, "cleanup_completed", session_identity=dict(SESSION)),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertFalse(result["backend_termination_confirmed"])
        self.assertFalse(result["cleanup_confirmed"])
        reasons = [row["reason"] for row in result["ignored_events"]]
        self.assertEqual(reasons, ["identity_unverified", "no_confirmed_termination"])

    def test_capture_state_unavailable_cannot_confirm_termination(self):
        envelope = dict(ATTEMPT_ENVELOPE)
        envelope["capture_state"] = "unavailable"
        envelope["session_identity"] = None
        envelope["capture_source"] = "none"
        events = [
            attempt(envelope=envelope),
            event(
                "term1",
                1,
                "backend_termination_reported",
                session_identity=dict(SESSION),
            ),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertFalse(result["backend_termination_confirmed"])
        self.assertIn(
            "quality_timeout_state.events[1].session_identity was never captured by the recorded attempt",
            result["errors"],
        )

    def test_stale_event_is_ignored_and_adds_no_claim(self):
        events = [
            attempt(),
            event("cln1", 5, "cleanup_completed", session_identity=dict(SESSION)),
            event("old", 2, "backend_termination_reported", session_identity=dict(SESSION)),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertFalse(result["backend_termination_confirmed"])
        self.assertFalse(result["cleanup_confirmed"])
        self.assertEqual(
            result["ignored_events"],
            [
                {"event_id": "cln1", "reason": "no_confirmed_termination"},
                {"event_id": "old", "reason": "stale_or_out_of_order"},
            ],
        )
        self.assertEqual(result["state"], "attempt_recorded")

    def test_duplicate_event_id_is_ignored(self):
        events = [
            attempt(),
            event(
                "term1", 1, "backend_termination_reported", session_identity=dict(SESSION)
            ),
            event(
                "term1", 2, "backend_termination_reported", session_identity=dict(SESSION)
            ),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertTrue(result["backend_termination_confirmed"])
        self.assertEqual(result["events_consumed"], 2)
        self.assertEqual(
            result["ignored_events"],
            [{"event_id": "term1", "reason": "duplicate_event_id"}],
        )

    def test_observation_failure_never_claims_cleanup(self):
        events = [
            attempt(),
            event(
                "term1", 1, "backend_termination_reported", session_identity=dict(SESSION)
            ),
            event("obs1", 2, "observation_failed"),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertEqual(result["state"], "termination_confirmed_cleanup_unconfirmed")
        self.assertFalse(result["cleanup_confirmed"])
        self.assertEqual(result["backend_state_after_timeout"], "unobserved")

    def test_observation_failure_without_termination_has_no_clean_state(self):
        events = [
            attempt(),
            event("t1", 1, "timeout_observed", timeout_observation=dict(OBSERVATION)),
            event("obs1", 2, "observation_failed"),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertEqual(result["state"], "timeout_recorded_backend_unobserved")
        self.assertFalse(result["cleanup_confirmed"])
        self.assertFalse(result["backend_termination_confirmed"])

    def test_budget_exhaustion_is_not_cleanup_success(self):
        events = [
            attempt(),
            event("t1", 1, "timeout_observed", timeout_observation=dict(OBSERVATION)),
            event(
                "b1",
                2,
                "budget_exhausted",
                budget={"limit_kind": "deadline", "used": 1, "limit": 1},
            ),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertEqual(result["state"], "budget_exhausted_without_cleanup")
        self.assertTrue(result["budget_exhausted"])
        self.assertFalse(result["cleanup_confirmed"])
        self.assertFalse(result["backend_termination_confirmed"])
        self.assertTrue(result["timeout_recorded"])

    def test_cleanup_without_confirmed_termination_is_rejected(self):
        events = [
            attempt(),
            event("cln1", 1, "cleanup_completed", session_identity=dict(SESSION)),
        ]
        result = decision.decide_quality_timeout_state(events)
        self.assertFalse(result["cleanup_confirmed"])
        self.assertEqual(
            result["ignored_events"],
            [{"event_id": "cln1", "reason": "no_confirmed_termination"}],
        )

    def test_empty_event_list_has_no_claims(self):
        result = decision.decide_quality_timeout_state([])
        self.assertEqual(result["state"], "no_attempt")
        self.assertFalse(result["backend_termination_confirmed"])
        self.assertFalse(result["cleanup_confirmed"])

    def test_non_list_input_is_indeterminate(self):
        result = decision.decide_quality_timeout_state("not-a-list")
        self.assertEqual(result["state"], "indeterminate_no_claims")
        self.assertIn("quality_timeout_state must be a list of evidence events", result["errors"])

    def test_malformed_attempt_envelope_blocks_all_claims(self):
        envelope = dict(ATTEMPT_ENVELOPE)
        envelope.pop("attempt_id")
        result = decision.decide_quality_timeout_state([attempt(envelope=envelope)])
        self.assertEqual(result["state"], "indeterminate_no_claims")
        self.assertTrue(
            any("attempt_id" in error for error in result["errors"])
        )
        self.assertFalse(result["backend_termination_confirmed"])

    def test_malformed_timeout_observation_is_structured_error(self):
        bad = dict(OBSERVATION)
        bad["rollback_completed"] = True
        result = decision.decide_quality_timeout_state(
            [
                attempt(),
                event("t1", 1, "timeout_observed", timeout_observation=bad),
            ]
        )
        self.assertEqual(result["state"], "indeterminate_no_claims")
        self.assertIn(
            "timeout_observation must not include unsupported fields: rollback_completed",
            result["errors"][0],
        )

    def test_unsupported_event_fields_rejected(self):
        result = decision.decide_quality_timeout_state(
            [attempt(), event("t1", 1, "timeout_observed", terminated=True)]
        )
        self.assertEqual(result["state"], "indeterminate_no_claims")
        self.assertIn(
            "quality_timeout_state.events[1] must not include unsupported fields: terminated",
            result["errors"],
        )

    def test_decision_is_deterministic_for_repeated_inputs(self):
        events = [
            attempt(),
            event("t1", 1, "timeout_observed", timeout_observation=dict(OBSERVATION)),
            event("cr1", 2, "cancellation_requested"),
            event("ca1", 3, "cancellation_accepted"),
            event(
                "term1", 4, "backend_termination_reported", session_identity=dict(SESSION)
            ),
            event("cln1", 5, "cleanup_completed", session_identity=dict(SESSION)),
        ]
        first = decision.decide_quality_timeout_state(events)
        second = decision.decide_quality_timeout_state(events)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(first["state"], "cleanup_confirmed")


if __name__ == "__main__":
    unittest.main()
