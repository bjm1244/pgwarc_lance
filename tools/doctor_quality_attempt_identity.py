#!/usr/bin/env python3
"""Validate one synthetic quality query attempt identity envelope (opt-in).

This is a prospective, repository-local acceptance boundary for a future
``quality_attempt_identity.v1`` envelope. It checks structural consistency only
and never observes PostgreSQL. It is not invoked by ``eval-quality``,
``doctor_run_directory``, run finalization, or ``make verify``.

A structurally valid envelope is not evidence that a backend existed or still
exists, that a statement or transaction was executed or identified, that
cancellation was requested or completed, that a rollback happened, or that any
resource was cleaned up. ``attempt_id`` is correlation-only, and
``session_identity`` is an author-supplied claim rather than a value read from
the server.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_LABEL = "quality_attempt_identity"

CAPTURE_STATE_UNAVAILABLE = "unavailable"
CAPTURE_STATE_CAPTURED = "captured"
CAPTURE_STATES = (CAPTURE_STATE_UNAVAILABLE, CAPTURE_STATE_CAPTURED)

CAPTURE_SOURCE_NONE = "none"
CAPTURE_SOURCE_QUERY_SESSION = "query_session"
CAPTURE_SOURCES = (CAPTURE_SOURCE_NONE, CAPTURE_SOURCE_QUERY_SESSION)

TRANSACTION_STATE_UNOBSERVED = "unobserved"

ENVELOPE_FIELDS = (
    "schema_version",
    "attempt_id",
    "capture_state",
    "session_identity",
    "transaction_identity",
    "capture_source",
)
SESSION_IDENTITY_FIELDS = ("backend_pid", "backend_start")
TRANSACTION_IDENTITY_FIELDS = ("state",)

SCOPE = (
    "structural consistency of a synthetic envelope only; not runtime backend "
    "observation and no proof of authenticity, existence, statement identity, "
    "transaction identity, cancellation, rollback, or cleanup"
)

BACKEND_START_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def is_integer(value: Any) -> bool:
    """True for JSON integers only; booleans are not integers here."""

    return isinstance(value, int) and not isinstance(value, bool)


def is_positive_integer(value: Any) -> bool:
    return is_integer(value) and value > 0


def is_explicit_timezone_datetime(value: Any) -> bool:
    """True for an ISO-8601 date-time string with an explicit timezone."""

    if not isinstance(value, str) or not BACKEND_START_PATTERN.match(value):
        return False
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_session_identity(
    session: Any,
    capture_state: Any,
    prefix: str,
    errors: list[str],
) -> None:
    if session is None:
        if capture_state == CAPTURE_STATE_CAPTURED:
            errors.append(
                f"{prefix}.session_identity must be an object when "
                f"capture_state is {CAPTURE_STATE_CAPTURED!r}"
            )
        return
    if not isinstance(session, dict):
        errors.append(f"{prefix}.session_identity must be null or an object")
        return
    if capture_state == CAPTURE_STATE_UNAVAILABLE:
        errors.append(
            f"{prefix}.session_identity must be null when "
            f"capture_state is {CAPTURE_STATE_UNAVAILABLE!r}"
        )
    unexpected = sorted(key for key in session if key not in SESSION_IDENTITY_FIELDS)
    if unexpected:
        errors.append(
            f"{prefix}.session_identity must not include unsupported fields: "
            + ", ".join(unexpected)
        )
    missing = [field for field in SESSION_IDENTITY_FIELDS if field not in session]
    if missing:
        errors.append(
            f"{prefix}.session_identity is missing required fields: "
            + ", ".join(missing)
        )
    if "backend_pid" in session and not is_positive_integer(session["backend_pid"]):
        errors.append(
            f"{prefix}.session_identity.backend_pid must be a positive integer"
        )
    if "backend_start" in session and not is_explicit_timezone_datetime(
        session["backend_start"]
    ):
        errors.append(
            f"{prefix}.session_identity.backend_start must be an ISO-8601 "
            "date-time with an explicit timezone"
        )


def validate_transaction_identity(tx: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(tx, dict):
        errors.append(f"{prefix}.transaction_identity must be an object")
        return
    unexpected = sorted(key for key in tx if key not in TRANSACTION_IDENTITY_FIELDS)
    if unexpected:
        errors.append(
            f"{prefix}.transaction_identity must not include unsupported fields: "
            + ", ".join(unexpected)
        )
    missing = [field for field in TRANSACTION_IDENTITY_FIELDS if field not in tx]
    if missing:
        errors.append(
            f"{prefix}.transaction_identity is missing required fields: "
            + ", ".join(missing)
        )
    if "state" in tx and tx["state"] != TRANSACTION_STATE_UNOBSERVED:
        errors.append(
            f"{prefix}.transaction_identity.state must be "
            f"{TRANSACTION_STATE_UNOBSERVED!r}"
        )


def validate_identity(
    payload: Any, label: str = DEFAULT_LABEL
) -> list[str]:
    """Return structural errors for one attempt identity envelope.

    The function is side-effect free and reusable by callers that do not want
    the CLI. An empty list means the envelope is structurally consistent with
    the v1 contract; it never means the claimed identity is authentic.
    """

    if not isinstance(payload, dict):
        return [f"{label} must be an object"]

    errors: list[str] = []

    missing = [field for field in ENVELOPE_FIELDS if field not in payload]
    if missing:
        errors.append(
            f"{label} is missing required fields: " + ", ".join(missing)
        )
    unexpected = sorted(key for key in payload if key not in ENVELOPE_FIELDS)
    if unexpected:
        errors.append(
            f"{label} must not include unsupported fields: " + ", ".join(unexpected)
        )

    if "schema_version" in payload and not (
        is_integer(payload["schema_version"])
        and payload["schema_version"] == SCHEMA_VERSION
    ):
        errors.append(f"{label}.schema_version must be {SCHEMA_VERSION}")

    if "attempt_id" in payload:
        attempt_id = payload["attempt_id"]
        if not isinstance(attempt_id, str) or attempt_id == "":
            errors.append(f"{label}.attempt_id must be a non-empty string")

    capture_state = payload.get("capture_state")
    if "capture_state" in payload and capture_state not in CAPTURE_STATES:
        errors.append(
            f"{label}.capture_state must be one of: " + ", ".join(CAPTURE_STATES)
        )

    capture_source = payload.get("capture_source")
    if "capture_source" in payload and capture_source not in CAPTURE_SOURCES:
        errors.append(
            f"{label}.capture_source must be one of: " + ", ".join(CAPTURE_SOURCES)
        )

    if "session_identity" in payload:
        validate_session_identity(
            payload["session_identity"], capture_state, label, errors
        )

    if "transaction_identity" in payload:
        validate_transaction_identity(payload["transaction_identity"], label, errors)

    if (
        capture_state == CAPTURE_STATE_UNAVAILABLE
        and "capture_source" in payload
        and capture_source != CAPTURE_SOURCE_NONE
    ):
        errors.append(
            f"{label}.capture_source must be {CAPTURE_SOURCE_NONE!r} when "
            f"capture_state is {CAPTURE_STATE_UNAVAILABLE!r}"
        )
    if (
        capture_state == CAPTURE_STATE_CAPTURED
        and "capture_source" in payload
        and capture_source != CAPTURE_SOURCE_QUERY_SESSION
    ):
        errors.append(
            f"{label}.capture_source must be {CAPTURE_SOURCE_QUERY_SESSION!r} when "
            f"capture_state is {CAPTURE_STATE_CAPTURED!r}"
        )

    return errors


def report_field(payload: Any, key: str, allowed: tuple[str, ...] | None = None) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key)
    if not isinstance(value, str):
        return ""
    if allowed is not None and value not in allowed:
        return ""
    return value


def build_report(input_json: Path) -> dict[str, Any]:
    try:
        payload = json.loads(input_json.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return report(
            input_json=input_json,
            payload=None,
            errors=[f"could not load input JSON: {exc}"],
        )
    return report(
        input_json=input_json,
        payload=payload,
        errors=validate_identity(payload),
    )


def report(
    *,
    input_json: Path,
    payload: Any,
    errors: list[str],
) -> dict[str, Any]:
    session = payload.get("session_identity") if isinstance(payload, dict) else None
    transaction = (
        payload.get("transaction_identity") if isinstance(payload, dict) else None
    )
    backend_pid: int | None = None
    backend_start = ""
    transaction_state = ""
    if isinstance(session, dict):
        if is_integer(session.get("backend_pid")):
            backend_pid = session["backend_pid"]
        if isinstance(session.get("backend_start"), str):
            backend_start = session["backend_start"]
    if isinstance(transaction, dict) and isinstance(transaction.get("state"), str):
        transaction_state = transaction["state"]
    return {
        "schema_version": SCHEMA_VERSION,
        "valid": not errors,
        "input_json": str(input_json),
        "attempt_id": report_field(payload, "attempt_id"),
        "capture_state": report_field(payload, "capture_state", CAPTURE_STATES),
        "capture_source": report_field(payload, "capture_source", CAPTURE_SOURCES),
        "backend_pid": backend_pid,
        "backend_start": backend_start,
        "transaction_state": transaction_state,
        "scope": SCOPE,
        "errors": errors,
        "error_count": len(errors),
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report_doc: dict[str, Any]) -> str:
    backend_pid = (
        "unset" if report_doc["backend_pid"] is None else report_doc["backend_pid"]
    )
    lines = [
        "# pgwarc_lance Quality Attempt Identity Doctor",
        "",
        f"- Input: `{report_doc['input_json']}`",
        f"- Valid: `{str(report_doc['valid']).lower()}`",
        f"- Attempt id: `{report_doc['attempt_id']}`",
        f"- Capture state: `{report_doc['capture_state'] or 'unset'}`",
        f"- Capture source: `{report_doc['capture_source'] or 'unset'}`",
        f"- Backend pid: `{backend_pid}`",
        f"- Backend start: `{report_doc['backend_start'] or 'unset'}`",
        f"- Transaction state: `{report_doc['transaction_state'] or 'unset'}`",
        f"- Scope: {report_doc['scope']}",
        "",
        "A structurally valid envelope is synthetic and opt-in. It does not prove "
        "authenticity, continued backend existence, query or transaction "
        "identity, cancellation, rollback, or cleanup.",
        "",
    ]
    if report_doc["errors"]:
        lines.extend(["## Errors", ""])
        for error in report_doc["errors"]:
            lines.append(f"- {markdown_cell(error)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, report_doc: dict[str, Any]) -> None:
    write_text(path, json.dumps(report_doc, indent=2, sort_keys=True) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report_doc = build_report(args.input_json)

    if args.json_output:
        write_json(args.json_output, report_doc)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report_doc))

    print(
        "quality attempt identity doctor: "
        f"valid={str(report_doc['valid']).lower()} "
        f"errors={report_doc['error_count']}"
    )
    if report_doc["errors"]:
        for error in report_doc["errors"]:
            print(f"quality attempt identity doctor failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
