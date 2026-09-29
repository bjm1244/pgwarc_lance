#!/usr/bin/env python3
"""Pure, deterministic timeout-after-timeout state decision contract (synthetic).

Astra Small decision ASTRA-20260915-01, Issue C slice. This module is a pure
function over a caller-supplied, ordered list of synthetic evidence events. It
never opens a DB connection, observes a backend, sends a cancellation, or
modifies any runtime state, and its output values are contract decisions, not
evidence of any real backend state.

The contract reuses two existing synthetic conventions:

- ``quality_attempt_identity.v1`` (``contracts/quality_attempt_identity.v1.schema.json``)
  validated with ``doctor_quality_attempt_identity.validate_identity``.
- the ``timeout.timeout_observation`` field enum from
  ``eval_quality.TIMEOUT_OBSERVATION_FIELDS``.

Conservative guarantees of this contract:

- A recorded timeout never becomes a confirmed backend termination.
- A recorded cancellation request/acceptance never becomes a confirmed
  termination; the backend state after acceptance stays ``unobserved``.
- Confirmation-grade claims (backend termination, cleanup completion) require
  the event to carry a ``session_identity`` exactly equal to the recorded
  attempt envelope's ``session_identity``.
- A missing session identity or missing attempt correlation on a
  confirmation-grade event keeps that claim unverified instead of confirming
  it.
- A mismatched identity (present but different) is a structural error and the
  whole decision becomes ``indeterminate_no_claims``.
- Stale/out-of-order and duplicate events are ignored and cannot introduce any
  claim.
- Observation failure and budget exhaustion never produce a cleanup claim.

Everything here is synthetic and contract-only. No real corpus, backend PID,
cancellation, rollback, or cleanup evidence is involved. This module is not
invoked by ``eval-quality``, run finalization, or ``make verify``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from doctor_quality_attempt_identity import validate_identity
import eval_quality

DECISION_SCHEMA_VERSION = 1
DEFAULT_LABEL = "quality_timeout_state"

KIND_ATTEMPT_RECORDED = "attempt_recorded"
KIND_TIMEOUT_OBSERVED = "timeout_observed"
KIND_QUERY_COMPLETED = "query_completed"
KIND_CANCELLATION_REQUESTED = "cancellation_requested"
KIND_CANCELLATION_ACCEPTED = "cancellation_accepted"
KIND_BACKEND_TERMINATION_REPORTED = "backend_termination_reported"
KIND_OBSERVATION_FAILED = "observation_failed"
KIND_CLEANUP_COMPLETED = "cleanup_completed"
KIND_BUDGET_EXHAUSTED = "budget_exhausted"
EVENT_KINDS = (
    KIND_ATTEMPT_RECORDED,
    KIND_TIMEOUT_OBSERVED,
    KIND_QUERY_COMPLETED,
    KIND_CANCELLATION_REQUESTED,
    KIND_CANCELLATION_ACCEPTED,
    KIND_BACKEND_TERMINATION_REPORTED,
    KIND_OBSERVATION_FAILED,
    KIND_CLEANUP_COMPLETED,
    KIND_BUDGET_EXHAUSTED,
)

EVIDENCE_FIELDS = (
    "event_id",
    "event_seq",
    "kind",
    "attempt",
    "attempt_id",
    "session_identity",
    "timeout_observation",
    "budget",
)

STATE_NO_CLAIMS = "indeterminate_no_claims"
STATE_NO_ATTEMPT = "no_attempt"
STATE_ATTEMPT_RECORDED = "attempt_recorded"
STATE_TIMEOUT_RECORDED = "timeout_recorded_backend_unobserved"
STATE_CANCELLATION_ACCEPTED = "cancellation_accepted_not_confirmed"
STATE_STATEMENT_COMPLETED = "statement_completed"
STATE_TERMINATION_CONFIRMED = "termination_confirmed_cleanup_unconfirmed"
STATE_CLEANUP_CONFIRMED = "cleanup_confirmed"
STATE_BUDGET_EXHAUSTED = "budget_exhausted_without_cleanup"

CONFIRMATION_KINDS = (
    KIND_BACKEND_TERMINATION_REPORTED,
    KIND_CLEANUP_COMPLETED,
)

SCOPE = (
    "pure deterministic contract decision over synthetic event input; "
    "not runtime DB observation and no proof of backend existence, "
    "termination, cancellation, rollback, or cleanup"
)


def is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_valid_event_id(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def identity_matches(claimed: Any, attempt_session: Any) -> bool:
    """True only when the claimed session identity equals the recorded one."""

    if not isinstance(attempt_session, dict):
        return False
    if not isinstance(claimed, dict):
        return False
    return claimed == attempt_session


def validate_timeout_observation_payload(
    payload: Any, at: str, errors: list[str]
) -> bool:
    """True when the payload matches the existing timeout_observation shape."""

    fields = eval_quality.TIMEOUT_OBSERVATION_FIELDS
    if not isinstance(payload, dict):
        errors.append(f"{at}.timeout_observation must be an object")
        return False
    malformed = False
    unexpected = sorted(key for key in payload if key not in fields)
    if unexpected:
        errors.append(
            f"{at}.timeout_observation must not include unsupported fields: "
            + ", ".join(unexpected)
        )
        malformed = True
    missing = [field for field in fields if field not in payload]
    if missing:
        errors.append(
            f"{at}.timeout_observation is missing required fields: "
            + ", ".join(missing)
        )
        return False
    if payload["statement_outcome"] not in (
        eval_quality.STATEMENT_OUTCOME_UNKNOWN,
        eval_quality.STATEMENT_OUTCOME_SERVER_TIMEOUT,
    ):
        errors.append(
            f"{at}.timeout_observation.statement_outcome must be one of: "
            f"{eval_quality.STATEMENT_OUTCOME_UNKNOWN}, "
            f"{eval_quality.STATEMENT_OUTCOME_SERVER_TIMEOUT}"
        )
        malformed = True
    fixed = (
        ("backend_state_after_timeout", "unobserved"),
        ("cancellation_completion", "unverified"),
        ("transaction_cleanup", "unverified"),
    )
    for field, expected in fixed:
        if payload[field] != expected:
            errors.append(
                f"{at}.timeout_observation.{field} must be {expected!r}"
            )
            malformed = True
    return not malformed


def decide_quality_timeout_state(
    events: Any, label: str = DEFAULT_LABEL
) -> dict[str, Any]:
    """Fold an ordered synthetic event list into one state decision.

    The function is pure: no side effects, no external inputs, and no clock
    dependency except the caller-supplied ``event_seq`` ordering. A confirmed
    termination or cleanup claim is only ever produced by an explicitly
    named, identity-matching event.
    """

    result: dict[str, Any] = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "state": STATE_NO_ATTEMPT,
        "identity_scope": "unrecorded",
        "timeout_recorded": False,
        "statement_completed": False,
        "cancellation_requested": False,
        "cancellation_request_accepted": False,
        "backend_termination_confirmed": False,
        "cleanup_confirmed": False,
        "observation_failed": False,
        "budget_exhausted": False,
        "budget": None,
        "backend_state_after_timeout": "unobserved",
        "events_consumed": 0,
        "ignored_events": [],
        "errors": [],
        "scope": SCOPE,
    }

    if not isinstance(events, list):
        result["errors"].append(
            f"{label} must be a list of evidence events"
        )
        result["state"] = STATE_NO_CLAIMS
        return result
    if not events:
        return result

    errors = result["errors"]
    ignored = result["ignored_events"]
    last_seq: int | None = None
    seen_event_ids: set[str] = set()
    attempt_id: str | None = None
    attempt_session: Any = None
    identity_available = False
    timeout_event_ids: list[str] = []

    def fail_indeterminate(message: str) -> None:
        errors.append(message)
        result["state"] = STATE_NO_CLAIMS

    for input_index, event in enumerate(events):
        at = f"{label}.events[{input_index}]"

        if not isinstance(event, dict):
            fail_indeterminate(f"{at} must be an object")
            break

        event_id = event.get("event_id")
        if not is_valid_event_id(event_id):
            fail_indeterminate(f"{at}.event_id must be a non-empty string")
            break

        if event_id in seen_event_ids:
            ignored.append({"event_id": event_id, "reason": "duplicate_event_id"})
            continue
        event_seq = event.get("event_seq")
        if not is_integer(event_seq) or event_seq < 0:
            fail_indeterminate(f"{at}.event_seq must be a non-negative integer")
            break
        if last_seq is not None and event_seq <= last_seq:
            ignored.append({"event_id": event_id, "reason": "stale_or_out_of_order"})
            continue

        unexpected = sorted(key for key in event if key not in EVIDENCE_FIELDS)
        if unexpected:
            fail_indeterminate(
                f"{at} must not include unsupported fields: " + ", ".join(unexpected)
            )
            break

        kind = event.get("kind")
        if kind not in EVENT_KINDS:
            fail_indeterminate(f"{at}.kind must be one of: " + ", ".join(EVENT_KINDS))
            break

        if "timeout_observation" in event and not validate_timeout_observation_payload(
            event["timeout_observation"], at, errors
        ):
            fail_indeterminate(f"{at}.timeout_observation is malformed")
            break

        if kind == KIND_ATTEMPT_RECORDED:
            if attempt_id is not None:
                ignored.append(
                    {"event_id": event_id, "reason": "attempt_already_recorded"}
                )
                continue
            envelope = event.get("attempt")
            identity_errors = validate_identity(envelope, label=f"{at}.attempt")
            if identity_errors:
                fail_indeterminate(identity_errors[0])
                break
            attempt_id = envelope["attempt_id"]
            attempt_session = envelope["session_identity"]
            identity_available = isinstance(attempt_session, dict)
            result["identity_scope"] = envelope["capture_state"]
            result["events_consumed"] += 1
            seen_event_ids.add(event_id)
            last_seq = event_seq
            continue

        if attempt_id is None:
            fail_indeterminate(
                f"{at} must follow an attempt_recorded event that establishes identity"
            )
            break

        event_attempt_id = event.get("attempt_id")
        if event_attempt_id in (None, ""):
            correlated = False
            correlation_note = "missing_attempt_id"
        elif event_attempt_id != attempt_id:
            fail_indeterminate(
                f"{at}.attempt_id does not match the recorded attempt identity"
            )
            break
        else:
            event_session = event.get("session_identity")
            if event_session is not None and not identity_available:
                fail_indeterminate(
                    f"{at}.session_identity was never captured by the recorded "
                    "attempt"
                )
                break
            if (
                event_session is not None
                and not identity_matches(event_session, attempt_session)
            ):
                fail_indeterminate(
                    f"{at}.session_identity does not match the recorded attempt "
                    "identity"
                )
                break
            correlated = True
            correlation_note = ""

        # Fold the evidence conservatively.
        if kind == KIND_TIMEOUT_OBSERVED:
            result["timeout_recorded"] = True
            result["statement_completed"] = False
            result["backend_state_after_timeout"] = "unobserved"
            timeout_event_ids.append(event_id)
            result["events_consumed"] += 1
        elif kind == KIND_QUERY_COMPLETED:
            result["statement_completed"] = True
            result["timeout_recorded"] = False
            result["backend_state_after_timeout"] = "unobserved"
            result["events_consumed"] += 1
        elif kind == KIND_CANCELLATION_REQUESTED:
            result["cancellation_requested"] = True
            result["cancellation_request_accepted"] = False
            result["events_consumed"] += 1
        elif kind == KIND_CANCELLATION_ACCEPTED:
            if not result["cancellation_requested"]:
                fail_indeterminate(
                    f"{at} has no preceding cancellation_requested event"
                )
                break
            result["cancellation_request_accepted"] = True
            result["backend_state_after_timeout"] = "unobserved"
            result["events_consumed"] += 1
        elif kind == KIND_BACKEND_TERMINATION_REPORTED:
            if correlated and identity_available and "session_identity" in event:
                result["backend_termination_confirmed"] = True
                result["backend_state_after_timeout"] = "termination_confirmed"
            else:
                ignored.append(
                    {
                        "event_id": event_id,
                        "reason": "identity_unverified",
                        "detail": correlation_note
                        or "missing_session_identity",
                    }
                )
            result["events_consumed"] += 1
        elif kind == KIND_OBSERVATION_FAILED:
            result["observation_failed"] = True
            result["backend_state_after_timeout"] = "unobserved"
            result["events_consumed"] += 1
        elif kind == KIND_CLEANUP_COMPLETED:
            if correlated and identity_available and "session_identity" in event:
                if result["backend_termination_confirmed"]:
                    result["cleanup_confirmed"] = True
                else:
                    ignored.append(
                        {"event_id": event_id, "reason": "no_confirmed_termination"}
                    )
            else:
                ignored.append(
                    {
                        "event_id": event_id,
                        "reason": "identity_unverified",
                        "detail": correlation_note
                        or "missing_session_identity",
                    }
                )
                if result["backend_termination_confirmed"]:
                    result["cleanup_confirmed"] = False
            result["events_consumed"] += 1
        elif kind == KIND_BUDGET_EXHAUSTED:
            budget = event.get("budget")
            if not isinstance(budget, dict) or not budget.get("limit_kind"):
                fail_indeterminate(
                    f"{at}.budget must be an object with a limit_kind"
                )
                break
            result["budget_exhausted"] = True
            result["budget"] = dict(budget)
            result["events_consumed"] += 1

        seen_event_ids.add(event_id)
        last_seq = event_seq

    if errors:
        result["state"] = STATE_NO_CLAIMS
    else:
        if result["cleanup_confirmed"]:
            result["state"] = STATE_CLEANUP_CONFIRMED
            result["backend_state_after_timeout"] = "cleanup_confirmed"
        elif result["backend_termination_confirmed"]:
            result["state"] = STATE_TERMINATION_CONFIRMED
            result["backend_state_after_timeout"] = (
                "unobserved"
                if result["observation_failed"]
                else "termination_confirmed"
            )
        elif result["statement_completed"]:
            result["state"] = STATE_STATEMENT_COMPLETED
            result["backend_state_after_timeout"] = "unobserved"
        elif result["budget_exhausted"]:
            result["state"] = STATE_BUDGET_EXHAUSTED
            result["backend_state_after_timeout"] = "unobserved"
        elif result["cancellation_request_accepted"]:
            result["state"] = STATE_CANCELLATION_ACCEPTED
            result["backend_state_after_timeout"] = "unobserved"
        elif result["timeout_recorded"]:
            result["state"] = STATE_TIMEOUT_RECORDED
            result["backend_state_after_timeout"] = "unobserved"
        elif attempt_id is not None:
            result["state"] = STATE_ATTEMPT_RECORDED
            result["backend_state_after_timeout"] = "unobserved"
        else:
            result["state"] = STATE_NO_ATTEMPT
            result["backend_state_after_timeout"] = "unobserved"

    result["error_count"] = len(errors)
    result["timeout_event_ids"] = timeout_event_ids
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
        print(f"quality timeout state decision failed: {exc}", file=sys.stderr)
        return 2
    result = decide_quality_timeout_state(payload)
    if args.json_output:
        write_text(
            args.json_output, json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
    print(
        "quality timeout state decision: "
        f"state={result['state']} errors={result['error_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
