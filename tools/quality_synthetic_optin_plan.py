#!/usr/bin/env python3
"""Pure, deterministic synthetic opt-in eligibility and plan contract.

Astra Small decision ASTRA-20260915-02, Issue C slice. This module is a pure
function over one caller-supplied synthetic opt-in request. It never opens a
DB connection, observes a backend, grants a permission, sends a cancellation,
captures a real identity, or modifies any runtime state.

The contract reuses existing synthetic conventions:

- ``quality_attempt_identity.v1`` (``contracts/quality_attempt_identity.v1.schema.json``)
  validated with ``doctor_quality_attempt_identity.validate_identity``.
- the explicit-timezone ISO-8601 check and positive-integer budget checks from
  ``doctor_quality_attempt_identity``.

Conservative guarantees of this contract:

- Default deny: an absent opt-in, a missing/unknown/contradictory required
  condition, or a malformed request rejects the plan.
- Observation eligibility and cancellation eligibility are decided
  independently. Observation eligibility never automatically grants
  cancellation eligibility: a cancellation must be separately requested and
  satisfy its own permission, budget, expiry, revalidation, target-scope, and
  identity-confirmation requirements.
- A cancellation plan is denied when the claimed identity mismatches the
  recorded attempt identity, when the target scope is reusable or ambiguous,
  or when the observation grant is expired. Execution-time revalidation is an
  explicit requirement of every cancellation plan.
- Observation and cancellation budgets must be finite positive integers and
  both grants must carry an explicit-timezone expiry.
- A captured same-session identity whose ``backend_start`` is after the
  request ``decision_time`` is contradictory and rejects the plan.
- Every plan output has ``execution_authority`` ``none`` and fixed
  ``not_claimed`` values for backend termination, cancellation completion,
  rollback completion, and transaction cleanup. Eligibility is consistency
  checking only: it is not execution approval and never evidence that a
  backend was terminated, a cancellation completed, a rollback completed, or
  transaction cleanup completed.

Everything here is synthetic and contract-only. No real corpus, backend PID,
cancellation, rollback, or cleanup evidence is involved. This module is not
invoked by ``eval-quality``, ``doctor_run_directory``, run finalization, or
``make verify``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from doctor_quality_attempt_identity import (
    CAPTURE_STATE_CAPTURED,
    is_explicit_timezone_datetime,
    is_positive_integer,
    validate_identity,
)

PLAN_SCHEMA_VERSION = 1
DEFAULT_LABEL = "quality_synthetic_optin_plan"

REQUEST_FIELDS = (
    "schema_version",
    "opt_in",
    "attempt",
    "decision_time",
    "observation",
    "cancellation",
)
OBSERVATION_FIELDS = (
    "permission_granted",
    "permission_source",
    "same_session_capture",
    "handles_target_race",
    "handles_timeout_race",
    "max_observations",
    "expires_at",
)
CANCELLATION_FIELDS = (
    "permission_granted",
    "permission_source",
    "requires_execution_revalidation",
    "target_scope",
    "identity_confirmation",
    "max_cancellations",
    "expires_at",
)

TARGET_SCOPE_SINGLE = "single_recorded_attempt"

STATE_INVALID_REQUEST = "invalid_request"
STATE_REJECTED_DEFAULT_DENY = "rejected_default_deny"
STATE_OBSERVATION_ELIGIBLE_ONLY = "observation_eligible_only"
STATE_OBSERVATION_AND_CANCELLATION = "observation_and_cancellation_eligible"

ELIGIBILITY_NOT_REQUESTED = "not_requested"
ELIGIBILITY_INELIGIBLE = "ineligible"
ELIGIBILITY_ELIGIBLE = "eligible"

EXECUTION_AUTHORITY_NONE = "none"
CLAIM_NOT_MADE = "not_claimed"

CLAIM_FIELDS = (
    "backend_termination",
    "cancellation_completed",
    "rollback_completed",
    "transaction_cleanup",
)

EXECUTION_REQUIREMENTS = (
    "capture_session_identity_in_the_same_session_before_the_quality_query",
    "use_a_separate_observer_connection_with_explicit_permission",
    "revalidate_identity_target_and_expiry_at_execution_time",
    "handle_target_already_terminated_and_timeout_races_before_any_cancellation",
    "enforce_finite_observation_and_cancellation_budgets",
    "require_real_db_evidence_for_rollback_and_transaction_cleanup_claims",
)

SCOPE = (
    "pure deterministic eligibility contract over a synthetic opt-in request; "
    "consistency checking only, not execution approval and no proof of "
    "backend existence, termination, cancellation, rollback, or cleanup"
)


def is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def is_non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def parse_tz_datetime(value: str) -> datetime.datetime | None:
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def check_bool_true(section: str, field: str, value: Any, reasons: list[str]) -> None:
    if value is not True:
        reasons.append(f"{section}.{field}_not_declared_true")


def check_expiry(
    section: str,
    expires_at: Any,
    decision_time: datetime.datetime,
    reasons: list[str],
) -> bool:
    if not isinstance(expires_at, str):
        reasons.append(f"{section}.expiry_missing")
        return False
    if not is_explicit_timezone_datetime(expires_at):
        reasons.append(f"{section}.expiry_invalid")
        return False
    expires = parse_tz_datetime(expires_at)
    if expires is None or expires <= decision_time:
        reasons.append(f"{section}.expired")
        return False
    return True


def check_permission(section: str, payload: dict, reasons: list[str]) -> None:
    granted = payload.get("permission_granted")
    source = payload.get("permission_source")
    if granted is not True:
        reasons.append(f"{section}.permission_not_granted")
    if not is_non_empty_str(source):
        reasons.append(f"{section}.permission_source_missing")
    if granted is False and is_non_empty_str(source):
        reasons.append(f"{section}.permission_contradictory")


def evaluate_observation(
    payload: Any, decision_time: datetime.datetime
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if payload is None:
        return False, reasons
    unexpected = sorted(key for key in payload if key not in OBSERVATION_FIELDS)
    if unexpected:
        reasons.append(
            f"observation.unsupported_fields: " + ", ".join(unexpected)
        )
    check_permission("observation", payload, reasons)
    check_bool_true("observation", "same_session_capture", payload.get("same_session_capture"), reasons)
    check_bool_true("observation", "handles_target_race", payload.get("handles_target_race"), reasons)
    check_bool_true("observation", "handles_timeout_race", payload.get("handles_timeout_race"), reasons)
    if not is_positive_integer(payload.get("max_observations")):
        reasons.append("observation.budget_not_finite")
    check_expiry("observation", payload.get("expires_at"), decision_time, reasons)
    return not reasons, reasons


def evaluate_cancellation(
    payload: Any,
    decision_time: datetime.datetime,
    observation_eligible: bool,
    attempt_session: Any,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if payload is None:
        return False, reasons
    if not observation_eligible:
        reasons.append("cancellation.observation_prerequisites_missing")
    unexpected = sorted(key for key in payload if key not in CANCELLATION_FIELDS)
    if unexpected:
        reasons.append(
            f"cancellation.unsupported_fields: " + ", ".join(unexpected)
        )
    check_permission("cancellation", payload, reasons)
    if payload.get("requires_execution_revalidation") is not True:
        reasons.append("cancellation.execution_revalidation_not_required")
    target_scope = payload.get("target_scope")
    if target_scope is None:
        reasons.append("cancellation.target_scope_missing")
    elif target_scope != TARGET_SCOPE_SINGLE:
        reasons.append("cancellation.target_scope_reusable_or_ambiguous")
    confirmation = payload.get("identity_confirmation")
    if confirmation is None:
        reasons.append("cancellation.identity_confirmation_missing")
    elif not isinstance(attempt_session, dict) or confirmation != attempt_session:
        reasons.append("cancellation.identity_confirmation_mismatch")
    if not is_positive_integer(payload.get("max_cancellations")):
        reasons.append("cancellation.budget_not_finite")
    check_expiry("cancellation", payload.get("expires_at"), decision_time, reasons)
    return not reasons, reasons


def decide_synthetic_optin_plan(
    request: Any, label: str = DEFAULT_LABEL
) -> dict[str, Any]:
    """Fold one synthetic opt-in request into an eligibility plan decision.

    The function is pure: no side effects, no external inputs, and no clock
    dependency except the caller-supplied ``decision_time``. The output is an
    eligibility plan, never an execution approval or a claim about any real
    backend state.
    """

    result: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_state": STATE_INVALID_REQUEST,
        "opt_in_declared": False,
        "attempt_id": "",
        "decision_time": "",
        "observation_eligibility": ELIGIBILITY_NOT_REQUESTED,
        "cancellation_eligibility": ELIGIBILITY_NOT_REQUESTED,
        "observation_reasons": [],
        "cancellation_reasons": [],
        "request_errors": [],
        "execution_authority": EXECUTION_AUTHORITY_NONE,
        "claims": {field: CLAIM_NOT_MADE for field in CLAIM_FIELDS},
        "execution_requirements": list(EXECUTION_REQUIREMENTS),
        "scope": SCOPE,
    }
    errors = result["request_errors"]

    if not isinstance(request, dict):
        errors.append(f"{label} must be an object")
        result["error_count"] = len(errors)
        return result

    unexpected = sorted(key for key in request if key not in REQUEST_FIELDS)
    if unexpected:
        errors.append(
            f"{label} must not include unsupported fields: " + ", ".join(unexpected)
        )

    required_missing = [
        field
        for field in ("schema_version", "attempt", "decision_time")
        if field not in request
    ]
    if required_missing:
        errors.append(
            f"{label} is missing required fields: " + ", ".join(required_missing)
        )
    if "schema_version" in request and request["schema_version"] != PLAN_SCHEMA_VERSION:
        errors.append(f"{label}.schema_version must be {PLAN_SCHEMA_VERSION}")

    opt_in = request.get("opt_in")
    if "opt_in" in request and not is_bool(opt_in):
        errors.append(f"{label}.opt_in must be a boolean")
    result["opt_in_declared"] = opt_in is True

    attempt_id = ""
    attempt_session: Any = None
    if "attempt" in request:
        envelope = request["attempt"]
        identity_errors = validate_identity(envelope, label=f"{label}.attempt")
        errors.extend(identity_errors[:1])
        if not identity_errors and isinstance(envelope, dict):
            attempt_id = envelope["attempt_id"]
            attempt_session = envelope["session_identity"]
            if (
                envelope["capture_state"] != CAPTURE_STATE_CAPTURED
                or not isinstance(attempt_session, dict)
            ):
                errors.append(
                    f"{label}.attempt requires same-session identity capture "
                    f"(capture_state {CAPTURE_STATE_CAPTURED!r} with a "
                    "session_identity object)"
                )
    result["attempt_id"] = attempt_id

    decision_time: datetime.datetime | None = None
    decision_time_raw = request.get("decision_time")
    if "decision_time" in request:
        if not is_explicit_timezone_datetime(decision_time_raw):
            errors.append(
                f"{label}.decision_time must be an ISO-8601 date-time with an "
                "explicit timezone"
            )
        else:
            decision_time = parse_tz_datetime(decision_time_raw)
            result["decision_time"] = decision_time_raw

    if not errors and decision_time is not None and isinstance(attempt_session, dict):
        backend_start_raw = attempt_session.get("backend_start")
        backend_start = (
            parse_tz_datetime(backend_start_raw)
            if isinstance(backend_start_raw, str)
            else None
        )
        if backend_start is not None and backend_start > decision_time:
            errors.append(
                f"{label}.attempt captured session_identity.backend_start must "
                "not be after the request decision_time"
            )

    observation_payload = request.get("observation")
    cancellation_payload = request.get("cancellation")
    for name, payload in (("observation", observation_payload), ("cancellation", cancellation_payload)):
        if name in request and payload is not None and not isinstance(payload, dict):
            errors.append(f"{label}.{name} must be an object or null")

    if errors:
        result["plan_state"] = STATE_INVALID_REQUEST
        result["error_count"] = len(errors)
        return result

    if opt_in is not True:
        result["plan_state"] = STATE_REJECTED_DEFAULT_DENY
        result["observation_reasons"] = ["opt_in.absent_or_false"]
        result["cancellation_reasons"] = ["opt_in.absent_or_false"]
        result["error_count"] = len(errors)
        return result

    observation_eligible = False
    cancellation_eligible = False

    if "observation" in request and observation_payload is not None:
        result["observation_eligibility"] = ELIGIBILITY_INELIGIBLE
        observation_eligible, reasons = evaluate_observation(
            observation_payload, decision_time
        )
        result["observation_reasons"] = reasons
        if observation_eligible:
            result["observation_eligibility"] = ELIGIBILITY_ELIGIBLE

    if "cancellation" in request and cancellation_payload is not None:
        result["cancellation_eligibility"] = ELIGIBILITY_INELIGIBLE
        cancellation_eligible, reasons = evaluate_cancellation(
            cancellation_payload, decision_time, observation_eligible, attempt_session
        )
        result["cancellation_reasons"] = reasons
        if cancellation_eligible:
            result["cancellation_eligibility"] = ELIGIBILITY_ELIGIBLE

    if observation_eligible and cancellation_eligible:
        result["plan_state"] = STATE_OBSERVATION_AND_CANCELLATION
    elif observation_eligible:
        result["plan_state"] = STATE_OBSERVATION_ELIGIBLE_ONLY
    else:
        result["plan_state"] = STATE_REJECTED_DEFAULT_DENY

    result["error_count"] = len(errors)
    return result


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"synthetic opt-in plan decision failed: {exc}", file=sys.stderr)
        return 2
    result = decide_synthetic_optin_plan(payload)
    if args.json_output:
        write_text(
            args.json_output, json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
    print(
        "synthetic opt-in plan decision: "
        f"state={result['plan_state']} "
        f"observation={result['observation_eligibility']} "
        f"cancellation={result['cancellation_eligibility']} "
        f"errors={result['error_count']} "
        f"execution_authority={result['execution_authority']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
