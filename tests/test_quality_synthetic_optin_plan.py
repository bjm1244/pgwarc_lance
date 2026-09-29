#!/usr/bin/env python3
"""Deterministic contract tests for the synthetic opt-in eligibility plan.

All inputs are hand-authored synthetic requests following the
``quality_attempt_identity.v1`` repo convention. No DB, backend PID capture,
permission grant, cancellation, rollback, or cleanup runtime is involved;
these tests prove the pure eligibility contract only.
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
plan = load_module("quality_synthetic_optin_plan", TOOLS / "quality_synthetic_optin_plan.py")

SESSION = {"backend_pid": 4242, "backend_start": "2026-09-15T06:04:00Z"}
OTHER_SESSION = {"backend_pid": 9999, "backend_start": "2026-09-15T06:04:00Z"}

DECISION_TIME = "2026-09-15T23:30:00Z"
FUTURE = "2026-09-16T23:30:00Z"
PAST = "2026-09-14T23:30:00Z"

ATTEMPT_ENVELOPE = {
    "schema_version": 1,
    "attempt_id": "attempt-1",
    "capture_state": "captured",
    "session_identity": dict(SESSION),
    "transaction_identity": {"state": "unobserved"},
    "capture_source": "query_session",
}

OBSERVATION = {
    "permission_granted": True,
    "permission_source": "session-owner",
    "same_session_capture": True,
    "handles_target_race": True,
    "handles_timeout_race": True,
    "max_observations": 3,
    "expires_at": FUTURE,
}

CANCELLATION = {
    "permission_granted": True,
    "permission_source": "session-owner",
    "requires_execution_revalidation": True,
    "target_scope": "single_recorded_attempt",
    "identity_confirmation": dict(SESSION),
    "max_cancellations": 1,
    "expires_at": FUTURE,
}


def base_request(**overrides):
    request = {
        "schema_version": 1,
        "opt_in": True,
        "attempt": json.loads(json.dumps(ATTEMPT_ENVELOPE)),
        "decision_time": DECISION_TIME,
    }
    request.update(overrides)
    return request


def full_request():
    return base_request(
        observation=json.loads(json.dumps(OBSERVATION)),
        cancellation=json.loads(json.dumps(CANCELLATION)),
    )


class OptInDefaultDenyTests(unittest.TestCase):
    def test_absent_opt_in_rejects_plan(self):
        request = full_request()
        del request["opt_in"]
        result = plan.decide_synthetic_optin_plan(request)
        self.assertEqual(result["plan_state"], "rejected_default_deny")
        self.assertFalse(result["observation_eligibility"] == "eligible"
                         and result["cancellation_eligibility"] == "eligible")

    def test_false_opt_in_rejects_plan(self):
        result = plan.decide_synthetic_optin_plan(full_request() | {"opt_in": False})
        self.assertEqual(result["plan_state"], "rejected_default_deny")

    def test_empty_request_is_invalid(self):
        result = plan.decide_synthetic_optin_plan({})
        self.assertEqual(result["plan_state"], "invalid_request")
        self.assertTrue(result["request_errors"])

    def test_non_object_request_output_schema_is_uniform(self):
        """Every plan output carries error_count, including non-object input."""
        expected_keys = set(plan.decide_synthetic_optin_plan(full_request()))
        self.assertIn("error_count", expected_keys)
        for value in (["not", "a", "dict"], "string", 42, None, True):
            result = plan.decide_synthetic_optin_plan(value)
            self.assertEqual(result["plan_state"], "invalid_request")
            self.assertEqual(result["error_count"], 1)
            self.assertEqual(set(result), expected_keys)
            self.assertEqual(result["execution_authority"], "none")
            self.assertEqual(
                result["claims"],
                {field: "not_claimed" for field in plan.CLAIM_FIELDS},
            )

    def test_contradictory_permission_rejects_observation(self):
        observation = dict(OBSERVATION)
        observation["permission_granted"] = False
        result = plan.decide_synthetic_optin_plan(
            base_request(observation=observation)
        )
        self.assertEqual(result["observation_eligibility"], "ineligible")
        self.assertIn("observation.permission_contradictory", result["observation_reasons"])

    def test_missing_permission_source_rejects(self):
        observation = dict(OBSERVATION)
        observation["permission_source"] = ""
        result = plan.decide_synthetic_optin_plan(
            base_request(observation=observation)
        )
        self.assertIn("observation.permission_source_missing", result["observation_reasons"])

    def test_non_captured_identity_is_rejected(self):
        envelope = dict(ATTEMPT_ENVELOPE)
        envelope["capture_state"] = "unavailable"
        envelope["session_identity"] = None
        envelope["capture_source"] = "none"
        result = plan.decide_synthetic_optin_plan(base_request(attempt=envelope))
        self.assertEqual(result["plan_state"], "invalid_request")

    def test_unsupported_fields_make_request_invalid(self):
        result = plan.decide_synthetic_optin_plan(full_request() | {"extra": 1})
        self.assertEqual(result["plan_state"], "invalid_request")


class AttemptClockConsistencyTests(unittest.TestCase):
    def test_backend_start_after_decision_time_rejects_plan(self):
        envelope = dict(ATTEMPT_ENVELOPE)
        envelope["session_identity"] = {
            "backend_pid": 4242,
            "backend_start": "2026-09-16T23:30:00Z",
        }
        result = plan.decide_synthetic_optin_plan(base_request(attempt=envelope))
        self.assertEqual(result["plan_state"], "invalid_request")
        self.assertIn(
            "quality_synthetic_optin_plan.attempt captured "
            "session_identity.backend_start must not be after the request "
            "decision_time",
            result["request_errors"],
        )
        self.assertEqual(result["error_count"], len(result["request_errors"]))
        self.assertEqual(result["observation_eligibility"], "not_requested")

    def test_backend_start_equal_to_decision_time_is_accepted(self):
        envelope = dict(ATTEMPT_ENVELOPE)
        envelope["session_identity"] = {
            "backend_pid": 4242,
            "backend_start": "2026-09-15T23:30:00Z",
        }
        result = plan.decide_synthetic_optin_plan(
            base_request(attempt=envelope, observation=json.loads(json.dumps(OBSERVATION)))
        )
        self.assertEqual(result["plan_state"], "observation_eligible_only")

    def test_backend_start_cross_timezone_equivalent_instant_is_accepted(self):
        envelope = dict(ATTEMPT_ENVELOPE)
        envelope["session_identity"] = {
            "backend_pid": 4242,
            "backend_start": "2026-09-16T08:30:00+09:00",
        }
        result = plan.decide_synthetic_optin_plan(
            base_request(attempt=envelope, observation=json.loads(json.dumps(OBSERVATION)))
        )
        self.assertEqual(result["plan_state"], "observation_eligible_only")


class ObservationVsCancellationSeparationTests(unittest.TestCase):
    def test_observation_only_request_grants_no_cancellation(self):
        result = plan.decide_synthetic_optin_plan(
            base_request(observation=json.loads(json.dumps(OBSERVATION)))
        )
        self.assertEqual(result["plan_state"], "observation_eligible_only")
        self.assertEqual(result["observation_eligibility"], "eligible")
        self.assertEqual(result["cancellation_eligibility"], "not_requested")

    def test_cancellation_requires_observation_prerequisites(self):
        result = plan.decide_synthetic_optin_plan(
            base_request(cancellation=json.loads(json.dumps(CANCELLATION)))
        )
        self.assertEqual(result["cancellation_eligibility"], "ineligible")
        self.assertIn(
            "cancellation.observation_prerequisites_missing",
            result["cancellation_reasons"],
        )

    def test_observation_never_auto_grants_cancellation(self):
        observation = dict(OBSERVATION)
        observation["max_observations"] = 10
        result = plan.decide_synthetic_optin_plan(
            base_request(observation=observation)
        )
        self.assertEqual(result["plan_state"], "observation_eligible_only")
        self.assertEqual(result["cancellation_eligibility"], "not_requested")


class CancellationGatingTests(unittest.TestCase):
    def test_identity_mismatch_blocks_cancellation(self):
        cancellation = dict(CANCELLATION)
        cancellation["identity_confirmation"] = dict(OTHER_SESSION)
        result = plan.decide_synthetic_optin_plan(
            base_request(
                observation=json.loads(json.dumps(OBSERVATION)),
                cancellation=cancellation,
            )
        )
        self.assertEqual(result["observation_eligibility"], "eligible")
        self.assertEqual(result["cancellation_eligibility"], "ineligible")
        self.assertIn(
            "cancellation.identity_confirmation_mismatch",
            result["cancellation_reasons"],
        )

    def test_missing_identity_confirmation_blocks_cancellation(self):
        cancellation = dict(CANCELLATION)
        cancellation["identity_confirmation"] = None
        result = plan.decide_synthetic_optin_plan(
            base_request(
                observation=json.loads(json.dumps(OBSERVATION)),
                cancellation=cancellation,
            )
        )
        self.assertIn(
            "cancellation.identity_confirmation_missing",
            result["cancellation_reasons"],
        )

    def test_reusable_target_scope_blocks_cancellation(self):
        cancellation = dict(CANCELLATION)
        cancellation["target_scope"] = "any_matching_identity"
        result = plan.decide_synthetic_optin_plan(
            base_request(
                observation=json.loads(json.dumps(OBSERVATION)),
                cancellation=cancellation,
            )
        )
        self.assertIn(
            "cancellation.target_scope_reusable_or_ambiguous",
            result["cancellation_reasons"],
        )

    def test_expired_cancellation_grant_blocks_cancellation(self):
        cancellation = dict(CANCELLATION)
        cancellation["expires_at"] = PAST
        result = plan.decide_synthetic_optin_plan(
            base_request(
                observation=json.loads(json.dumps(OBSERVATION)),
                cancellation=cancellation,
            )
        )
        self.assertIn("cancellation.expired", result["cancellation_reasons"])

    def test_execution_revalidation_is_explicit_requirement(self):
        cancellation = dict(CANCELLATION)
        cancellation["requires_execution_revalidation"] = False
        result = plan.decide_synthetic_optin_plan(
            base_request(
                observation=json.loads(json.dumps(OBSERVATION)),
                cancellation=cancellation,
            )
        )
        self.assertIn(
            "cancellation.execution_revalidation_not_required",
            result["cancellation_reasons"],
        )
        self.assertIn(
            "revalidate_identity_target_and_expiry_at_execution_time",
            result["execution_requirements"],
        )

    def test_cancellation_permission_not_granted(self):
        cancellation = dict(CANCELLATION)
        cancellation["permission_granted"] = False
        result = plan.decide_synthetic_optin_plan(
            base_request(
                observation=json.loads(json.dumps(OBSERVATION)),
                cancellation=cancellation,
            )
        )
        self.assertIn(
            "cancellation.permission_not_granted", result["cancellation_reasons"]
        )


class BudgetAndExpiryTests(unittest.TestCase):
    def test_zero_observation_budget_is_not_finite(self):
        observation = dict(OBSERVATION)
        observation["max_observations"] = 0
        result = plan.decide_synthetic_optin_plan(base_request(observation=observation))
        self.assertIn("observation.budget_not_finite", result["observation_reasons"])

    def test_negative_cancellation_budget_is_not_finite(self):
        cancellation = dict(CANCELLATION)
        cancellation["max_cancellations"] = -1
        result = plan.decide_synthetic_optin_plan(
            base_request(
                observation=json.loads(json.dumps(OBSERVATION)),
                cancellation=cancellation,
            )
        )
        self.assertIn("cancellation.budget_not_finite", result["cancellation_reasons"])

    def test_expired_observation_blocks_observation(self):
        observation = dict(OBSERVATION)
        observation["expires_at"] = PAST
        result = plan.decide_synthetic_optin_plan(base_request(observation=observation))
        self.assertEqual(result["observation_eligibility"], "ineligible")
        self.assertIn("observation.expired", result["observation_reasons"])

    def test_naive_expiry_is_invalid(self):
        observation = dict(OBSERVATION)
        observation["expires_at"] = "2026-09-16T23:30:00"
        result = plan.decide_synthetic_optin_plan(base_request(observation=observation))
        self.assertIn("observation.expiry_invalid", result["observation_reasons"])


class NoFalseClaimsTests(unittest.TestCase):
    def test_claims_never_made_even_when_fully_eligible(self):
        result = plan.decide_synthetic_optin_plan(full_request())
        self.assertEqual(result["plan_state"], "observation_and_cancellation_eligible")
        self.assertEqual(result["execution_authority"], "none")
        for field in (
            "backend_termination",
            "cancellation_completed",
            "rollback_completed",
            "transaction_cleanup",
        ):
            self.assertEqual(result["claims"][field], "not_claimed")

    def test_scope_and_requirements_reject_execution(self):
        result = plan.decide_synthetic_optin_plan(full_request())
        self.assertEqual(result["execution_authority"], "none")
        self.assertIn("not execution approval", result["scope"])
        self.assertIn(
            "require_real_db_evidence_for_rollback_and_transaction_cleanup_claims",
            result["execution_requirements"],
        )


class DeterminismAndCliTests(unittest.TestCase):
    def test_repeated_decisions_are_identical(self):
        first = plan.decide_synthetic_optin_plan(full_request())
        second = plan.decide_synthetic_optin_plan(full_request())
        self.assertEqual(first, second)

    def test_cli_writes_json_and_reports_state(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            input_path = pathlib.Path(tmp) / "request.json"
            output_path = pathlib.Path(tmp) / "plan.json"
            input_path.write_text(json.dumps(full_request()), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOLS / "quality_synthetic_optin_plan.py"),
                    "--input-json",
                    str(input_path),
                    "--json-output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("state=observation_and_cancellation_eligible", completed.stdout)
            doc = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                doc["plan_state"], "observation_and_cancellation_eligible"
            )
            self.assertEqual(doc["claims"]["backend_termination"], "not_claimed")

    def test_cli_malformed_input_exits_two(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            input_path = pathlib.Path(tmp) / "bad.json"
            input_path.write_text("{not json", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOLS / "quality_synthetic_optin_plan.py"),
                    "--input-json",
                    str(input_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)


if __name__ == "__main__":
    unittest.main()
