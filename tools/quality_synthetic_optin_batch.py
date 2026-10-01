#!/usr/bin/env python3
"""Internal batch consumer for the synthetic opt-in eligibility/plan contract.

Development decision 2026-09-16, Issue C slice. This module folds an
ordered list of synthetic opt-in requests through the existing Phase 85
evaluator one element at a time. It never opens a DB connection, observes a
backend, grants a permission, sends a cancellation, performs any I/O, reads a
clock, or stores persistent state.

Guarantees:

- Each element is evaluated by ``decide_synthetic_optin_plan`` exactly once
  and the individual result is returned unchanged.
- Duplicates and input order are preserved: element ``n`` of the input maps
  to element ``n`` of the output. Only order-preserving iterables are
  accepted: mappings, unordered iterables (``set``/``frozenset``), scalar
  text/byte sequences (``str``/``bytes``/``bytearray``/``memoryview``), and
  non-iterable scalars (``None``, ``int``, ``float``, ``bool``, ...) are
  rejected with a ``TypeError`` at the input boundary because they cannot
  honor the ordering and determinism guarantees or do not represent a batch
  of requests.
- Totals count only explicit affirmative eligibility values
  (``observation_eligibility == "eligible"`` and
  ``cancellation_eligibility == "eligible"``) and are counted independently;
  ``not_requested`` and ``ineligible`` never count.
- Unexpected evaluator exceptions propagate unchanged; this module never
  converts an evaluator exception into a result.
- The function is pure and deterministic: the same ordered input always
  produces an identical result structure, and it neither mutates the input
  list nor any result.

Everything here is synthetic and contract-only. Batch-level eligibility
totals are consistency statistics, not execution authorization and never
evidence that any backend was observed, cancelled, terminated, rolled back,
or had transaction cleanup completed. This module is not invoked by
``eval-quality``, ``doctor_run_directory``, run finalization, ``make
verify``, or any public CLI.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from quality_synthetic_optin_plan import decide_synthetic_optin_plan

BATCH_SCHEMA_VERSION = 1

RESULTS_FIELD = "results"
OBSERVATION_ELIGIBLE_TOTAL_FIELD = "observation_eligible_total"
CANCELLATION_ELIGIBLE_TOTAL_FIELD = "cancellation_eligible_total"

REJECTED_SCALAR_SEQUENCE_TYPES = (str, bytes, bytearray, memoryview)


def consume_synthetic_optin_batch(
    requests: Iterable[Any],
) -> dict[str, Any]:
    """Evaluate an ordered batch of synthetic opt-in requests.

    Each request is passed to ``decide_synthetic_optin_plan`` unchanged and
    the returned plan dicts are collected in input order together with
    separate observation-eligible and cancellation-eligible totals.
    """

    if isinstance(
        requests, (Mapping, set, frozenset) + REJECTED_SCALAR_SEQUENCE_TYPES
    ) or not isinstance(requests, Iterable):
        raise TypeError(
            "synthetic opt-in batch input must be a finite ordered iterable "
            f"of requests, not {type(requests).__name__}"
        )

    results: list[dict[str, Any]] = [
        decide_synthetic_optin_plan(request) for request in requests
    ]
    observation_eligible_total = sum(
        result["observation_eligibility"] == "eligible"
        for result in results
    )
    cancellation_eligible_total = sum(
        result["cancellation_eligibility"] == "eligible"
        for result in results
    )
    return {
        "schema_version": BATCH_SCHEMA_VERSION,
        RESULTS_FIELD: results,
        OBSERVATION_ELIGIBLE_TOTAL_FIELD: observation_eligible_total,
        CANCELLATION_ELIGIBLE_TOTAL_FIELD: cancellation_eligible_total,
    }
