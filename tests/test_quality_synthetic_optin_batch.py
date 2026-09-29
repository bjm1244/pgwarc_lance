#!/usr/bin/env python3
"""Deterministic contract tests for the synthetic opt-in plan batch consumer.

All inputs are hand-authored synthetic requests reusing the
``tests.test_quality_synthetic_optin_plan`` request fixtures. No DB, backend
PID capture, permission grant, cancellation, rollback, cleanup runtime, I/O,
or ambient clock is involved; these tests prove the pure batch consumer
contract only.
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


if "doctor_quality_attempt_identity" not in sys.modules:
    load_module(
        "doctor_quality_attempt_identity",
        TOOLS / "doctor_quality_attempt_identity.py",
    )
if "quality_synthetic_optin_plan" not in sys.modules:
    plan = load_module(
        "quality_synthetic_optin_plan", TOOLS / "quality_synthetic_optin_plan.py"
    )
else:
    plan = sys.modules["quality_synthetic_optin_plan"]

batch = load_module("quality_synthetic_optin_batch", TOOLS / "quality_synthetic_optin_batch.py")

SESSION = {"backend_pid": 4242, "backend_start": "2026-09-15T06:04:00Z"}

DECISION_TIME = "2026-09-15T23:30:00Z"
FUTURE = "2026-09-16T23:30:00Z"

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


def observation_only_request():
    return base_request(observation=json.loads(json.dumps(OBSERVATION)))


def malformed_request(**overrides):
    """A request that reaches invalid_request with a counted error."""
    request = base_request()
    request.update(overrides)
    request["extra"] = 1
    return request


class BatchEmptyTests(unittest.TestCase):
    def test_empty_input_gives_empty_results_and_zero_totals(self):
        result = batch.consume_synthetic_optin_batch([])
        self.assertEqual(
            result,
            {
                "schema_version": batch.BATCH_SCHEMA_VERSION,
                "results": [],
                "observation_eligible_total": 0,
                "cancellation_eligible_total": 0,
            },
        )


class BatchDirectEquivalenceTests(unittest.TestCase):
    def test_each_output_equals_direct_evaluation(self):
        requests = [
            full_request(),
            malformed_request(),
            base_request(),
            "not a request",
            ["not", "an", "object"],
            2026,
            None,
            True,
            observation_only_request(),
        ]
        result = batch.consume_synthetic_optin_batch(requests)
        self.assertEqual(len(result["results"]), len(requests))
        for request, batch_result in zip(requests, result["results"]):
            self.assertEqual(batch_result, plan.decide_synthetic_optin_plan(request))

    def test_non_object_inputs_return_unchanged_evaluator_results_with_error_count(self):
        for value in (["not", "a", "dict"], "string", 42, None, False):
            result = batch.consume_synthetic_optin_batch([value])
            entry = result["results"][0]
            self.assertEqual(entry, plan.decide_synthetic_optin_plan(value))
            self.assertEqual(entry["plan_state"], "invalid_request")
            self.assertEqual(entry["error_count"], 1)
            self.assertEqual(entry["execution_authority"], "none")


class BatchTotalsTests(unittest.TestCase):
    def test_totals_are_independent_and_affirmative_only(self):
        requests = [
            full_request(),
            observation_only_request(),
            malformed_request(),
            "string request",
            base_request(),
        ]
        result = batch.consume_synthetic_optin_batch(requests)
        affirmative_observation = sum(
            entry["observation_eligibility"] == "eligible"
            for entry in result["results"]
        )
        affirmative_cancellation = sum(
            entry["cancellation_eligibility"] == "eligible"
            for entry in result["results"]
        )
        self.assertEqual(
            result["observation_eligible_total"], affirmative_observation
        )
        self.assertEqual(
            result["observation_eligible_total"], 2
        )
        self.assertEqual(result["cancellation_eligible_total"], 1)

    def test_totals_never_conflate_ineligible_or_not_requested(self):
        result = batch.consume_synthetic_optin_batch([malformed_request(), None])
        self.assertEqual(result["observation_eligible_total"], 0)
        self.assertEqual(result["cancellation_eligible_total"], 0)
        for entry in result["results"]:
            self.assertIn(
                entry["observation_eligibility"], ("ineligible", "not_requested")
            )


class BatchOrderAndDuplicationTests(unittest.TestCase):
    def test_duplicates_are_preserved(self):
        request = full_request()
        same = json.loads(json.dumps(request))
        result = batch.consume_synthetic_optin_batch([request, same, request])
        self.assertEqual(result["results"][0], result["results"][1])
        self.assertEqual(result["results"][0], result["results"][2])
        self.assertEqual(result["observation_eligible_total"], 3)
        self.assertEqual(result["cancellation_eligible_total"], 3)

    def test_input_order_is_preserved(self):
        requests = [
            malformed_request(),
            full_request(),
            observation_only_request(),
        ]
        result = batch.consume_synthetic_optin_batch(requests)
        self.assertEqual(
            [entry["attempt_id"] for entry in result["results"]],
            [plan.decide_synthetic_optin_plan(r)["attempt_id"] for r in requests],
        )
        self.assertEqual(
            [entry["plan_state"] for entry in result["results"]],
            [
                "invalid_request",
                "observation_and_cancellation_eligible",
                "observation_eligible_only",
            ],
        )


class BatchMixedInputTests(unittest.TestCase):
    def test_mixed_valid_and_invalid_inputs_preserve_every_result(self):
        requests = [
            malformed_request(),
            full_request(),
            0.5,
            observation_only_request(),
            False,
            base_request(),
        ]
        result = batch.consume_synthetic_optin_batch(requests)
        self.assertEqual(len(result["results"]), 6)
        for request, entry, expected in zip(
            requests,
            result["results"],
            [plan.decide_synthetic_optin_plan(r) for r in requests],
        ):
            self.assertEqual(entry, expected)
        self.assertEqual(sum(entry["plan_state"] == "invalid_request" for entry in result["results"]), 3)


class BatchRepairedBoundaryTests(unittest.TestCase):
    def test_future_backend_start_clock_boundary_is_covered(self):
        envelope = dict(ATTEMPT_ENVELOPE)
        envelope["session_identity"] = {
            "backend_pid": 4242,
            "backend_start": "2026-09-16T23:30:00Z",
        }
        request = base_request(attempt=envelope)
        result = batch.consume_synthetic_optin_batch([request])
        entry = result["results"][0]
        self.assertEqual(entry["plan_state"], "invalid_request")
        self.assertEqual(entry["error_count"], 1)
        self.assertEqual(
            entry,
            plan.decide_synthetic_optin_plan(request),
        )

    def test_equivalent_timezone_boundary_is_covered(self):
        envelope = dict(ATTEMPT_ENVELOPE)
        envelope["session_identity"] = {
            "backend_pid": 4242,
            "backend_start": "2026-09-16T08:30:00+09:00",
        }
        request = base_request(attempt=envelope, observation=json.loads(json.dumps(OBSERVATION)))
        result = batch.consume_synthetic_optin_batch([request])
        entry = result["results"][0]
        self.assertEqual(entry["plan_state"], "observation_eligible_only")
        self.assertEqual(result["observation_eligible_total"], 1)
        self.assertEqual(result["cancellation_eligible_total"], 0)


class BatchInputBoundaryTests(unittest.TestCase):
    def test_mapping_input_is_rejected(self):
        with self.assertRaises(TypeError) as caught:
            batch.consume_synthetic_optin_batch({"attempt-1": full_request()})
        self.assertIn("ordered iterable", str(caught.exception))

    def test_unordered_set_inputs_are_rejected(self):
        for unordered in ({"a", "b"}, frozenset({"a", "b"})):
            with self.assertRaises(TypeError) as caught:
                batch.consume_synthetic_optin_batch(unordered)
            self.assertIn("ordered iterable", str(caught.exception))

    def test_scalar_text_and_byte_sequence_inputs_are_rejected(self):
        for scalar in ("ab", "", "full request string", b"\x00\x01", bytearray(b"x")):
            with self.assertRaises(TypeError) as caught:
                batch.consume_synthetic_optin_batch(scalar)
            self.assertIn("ordered iterable", str(caught.exception))

    def test_scalar_byte_buffer_memoryview_input_is_rejected(self):
        for scalar in (memoryview(b"ab"), memoryview(b"")):
            with self.assertRaises(TypeError) as caught:
                batch.consume_synthetic_optin_batch(scalar)
            self.assertIn("ordered iterable", str(caught.exception))

    def test_non_iterable_scalar_inputs_are_rejected_with_contract_message(self):
        for scalar in (None, 2026, 3.5, True, False, object()):
            with self.assertRaises(TypeError) as caught:
                batch.consume_synthetic_optin_batch(scalar)
            self.assertIn("ordered iterable", str(caught.exception))
            self.assertIn(type(scalar).__name__, str(caught.exception))

    def test_rejected_non_iterable_scalar_never_evaluate_requests(self):
        original = batch.decide_synthetic_optin_plan

        def failing(request, label=plan.DEFAULT_LABEL):
            raise AssertionError("evaluator must not run for rejected input")

        try:
            batch.decide_synthetic_optin_plan = failing
            for scalar in (None, 2026, 3.5, True):
                with self.assertRaises(TypeError):
                    batch.consume_synthetic_optin_batch(scalar)
        finally:
            batch.decide_synthetic_optin_plan = original

    def test_order_preserving_iterables_are_accepted(self):
        request = full_request()
        result = batch.consume_synthetic_optin_batch(
            (request, "string", malformed_request())
        )
        self.assertEqual(len(result["results"]), 3)
        self.assertEqual(result["results"][0], plan.decide_synthetic_optin_plan(request))
        self.assertEqual(result["results"][1], plan.decide_synthetic_optin_plan("string"))
        self.assertEqual(
            result["results"][2],
            plan.decide_synthetic_optin_plan(malformed_request()),
        )
        self.assertEqual(result["observation_eligible_total"], 1)
        self.assertEqual(result["cancellation_eligible_total"], 1)

    def test_rejected_input_types_never_evaluate_requests(self):
        original = batch.decide_synthetic_optin_plan

        def failing(request, label=plan.DEFAULT_LABEL):
            raise AssertionError("evaluator must not run for rejected input")

        try:
            batch.decide_synthetic_optin_plan = failing
            with self.assertRaises(TypeError):
                batch.consume_synthetic_optin_batch({"a": "b"})
            with self.assertRaises(TypeError):
                batch.consume_synthetic_optin_batch({"a"})
            with self.assertRaises(TypeError):
                batch.consume_synthetic_optin_batch("ab")
            with self.assertRaises(TypeError):
                batch.consume_synthetic_optin_batch(b"ab")
            with self.assertRaises(TypeError):
                batch.consume_synthetic_optin_batch(bytearray(b"ab"))
            with self.assertRaises(TypeError):
                batch.consume_synthetic_optin_batch(memoryview(b"ab"))
        finally:
            batch.decide_synthetic_optin_plan = original

    def test_memoryview_element_inside_list_is_evaluated_directly(self):
        element = memoryview(b"x")
        result = batch.consume_synthetic_optin_batch([element])
        entry = result["results"][0]
        self.assertEqual(entry, plan.decide_synthetic_optin_plan(element))
        self.assertEqual(entry["plan_state"], "invalid_request")
        self.assertEqual(entry["error_count"], 1)
        self.assertEqual(entry["execution_authority"], "none")
        self.assertEqual(result["observation_eligible_total"], 0)
        self.assertEqual(result["cancellation_eligible_total"], 0)


class BatchPurityTests(unittest.TestCase):
    def test_repeated_evaluation_is_identical(self):
        requests = [full_request(), malformed_request(), "string", None]
        first = batch.consume_synthetic_optin_batch(requests)
        second = batch.consume_synthetic_optin_batch(requests)
        self.assertEqual(first, second)

    def test_input_is_not_mutated(self):
        request = full_request()
        requests = [request, "string", malformed_request()]
        before = json.loads(json.dumps(requests))
        batch.consume_synthetic_optin_batch(requests)
        self.assertEqual(json.loads(json.dumps(requests)), before)

    def test_results_have_no_shared_state_between_calls(self):
        first = batch.consume_synthetic_optin_batch([full_request()])
        second = batch.consume_synthetic_optin_batch([full_request()])
        first["results"].clear()
        second["results"].clear()
        self.assertEqual(len(first["results"]), 0)
        self.assertEqual(len(second["results"]), 0)

    def test_unexpected_evaluator_exception_propagates(self):
        sentinel_error = RuntimeError("evaluator exploded")
        original = batch.decide_synthetic_optin_plan

        def exploding(request, label=plan.DEFAULT_LABEL):
            raise sentinel_error

        try:
            batch.decide_synthetic_optin_plan = exploding
            with self.assertRaises(RuntimeError) as caught:
                batch.consume_synthetic_optin_batch([full_request()])
            self.assertIs(caught.exception, sentinel_error)
        finally:
            batch.decide_synthetic_optin_plan = original

    def test_no_ambient_clock_or_io_dependencies(self):
        import time

        real_time = time.time
        time.time = lambda: 0  # ambient clock must not matter
        try:
            first = batch.consume_synthetic_optin_batch([full_request()])
            time.time = lambda: 1
            second = batch.consume_synthetic_optin_batch([full_request()])
            self.assertEqual(first, second)
        finally:
            time.time = real_time


if __name__ == "__main__":
    unittest.main()
