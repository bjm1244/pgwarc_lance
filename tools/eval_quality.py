#!/usr/bin/env python3
"""Run smoke-level retrieval quality eval against a labeled corpus fixture."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import execution_budget
from execution_budget import BudgetConfigError, BudgetExhausted, ExecutionBudget


DEFAULT_PSQL = (
    "docker compose exec -T postgres "
    "psql -U pgwarc_lance -d pgwarc_lance_test "
    "-v ON_ERROR_STOP=1 -q -At"
)
MAX_DOC_ID = (1 << 63) - 1
STATUS_TIMEOUT = "quality_eval_timeout"
EXIT_TIMEOUT = 4
PG_STATEMENT_TIMEOUT_MIN_MS = 1
PG_STATEMENT_TIMEOUT_MAX_MS = 2147483647
STATEMENT_TIMEOUT_REASON = "canceling statement due to statement timeout"
STATEMENT_TIMEOUT_SQL_PATTERN = re.compile(r"SET LOCAL statement_timeout = (\d+);")
TIMEOUT_SOURCE_SERVER = "server_statement"
TIMEOUT_SOURCE_CLIENT = "client"
TIMEOUT_SOURCE_BUDGET = "run_budget"
STATEMENT_OUTCOME_UNKNOWN = "unknown"
STATEMENT_OUTCOME_SERVER_TIMEOUT = "server_statement_timeout_reported"
TIMEOUT_STATEMENT_OUTCOMES = (STATEMENT_OUTCOME_UNKNOWN, STATEMENT_OUTCOME_SERVER_TIMEOUT)
BACKEND_STATE_AFTER_TIMEOUT_UNOBSERVED = "unobserved"
CANCELLATION_COMPLETION_UNVERIFIED = "unverified"
TRANSACTION_CLEANUP_UNVERIFIED = "unverified"
TIMEOUT_OBSERVATION_FIELDS = (
    "statement_outcome",
    "backend_state_after_timeout",
    "cancellation_completion",
    "transaction_cleanup",
)


class SqlTimeout(Exception):
    """Raised when a psql subprocess exceeds the effective client-side timeout.

    This is a client subprocess boundary only. It does not cancel the query on
    the PostgreSQL server, so a timed-out backend statement may still be running
    after psql exits.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float,
        stdout: str | bytes | None = None,
        stderr: str | bytes | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.stage: str | None = None
        self.partial_results: list[Any] | None = None
        self.pre_run = False
        super().__init__(
            f"psql subprocess exceeded the effective client timeout of {timeout_seconds}s"
        )


class StatementTimeout(Exception):
    """Raised when psql reports the server canceled a statement by timeout.

    The opt-in query-only ``SET LOCAL statement_timeout`` limit is confirmed by
    the stable ``canceling statement due to statement timeout`` diagnostic, not
    by SQLSTATE 57014 alone. This records the observation only: it does not
    guarantee that the backend statement stopped, that the transaction rolled
    back, or that server resources were reclaimed.
    """

    def __init__(
        self,
        *,
        stderr: str | bytes | None = None,
        stdout: str | bytes | None = None,
        sqlstate: str | None = None,
        effective_statement_timeout_ms: int | None = None,
    ) -> None:
        self.stderr = stderr or ""
        self.stdout = stdout or ""
        self.sqlstate = sqlstate
        self.effective_statement_timeout_ms = effective_statement_timeout_ms
        self.requested_statement_timeout_ms: Any = None
        self.timeout_source = TIMEOUT_SOURCE_SERVER
        self.stage: str | None = None
        self.partial_results: list[Any] | None = None
        super().__init__(
            "PostgreSQL server canceled the statement due to statement_timeout "
            f"(sqlstate={sqlstate!r})"
        )


NON_SUCCESS_STATUSES = (STATUS_TIMEOUT, execution_budget.STATUS_EXHAUSTED)

STATUS_KIND_SUCCESS = "success"
STATUS_KIND_NON_SUCCESS = "non_success"
STATUS_KIND_UNKNOWN = "unknown_status"
STATUS_KIND_CONTRADICTION = "contradiction"
STATUS_KIND_MISSING_STATUS = "missing_status"
STATUS_KIND_MISSING_SUCCESS = "missing_success"


def artifact_status_kind(payload: dict[str, Any]) -> str:
    """Classify a quality eval artifact as success, non-success, or invalid.

    The producer writes neither ``status`` nor ``success`` for a successful
    baseline. A non-success artifact carries both a known ``status`` and
    ``success=false``. Any other combination is a contract violation that
    consumers must surface instead of coercing into a success schema.
    """

    status = payload.get("status")
    success = payload.get("success")
    if status is None:
        if "success" not in payload or success is True:
            return STATUS_KIND_SUCCESS
        return STATUS_KIND_MISSING_STATUS
    if status not in NON_SUCCESS_STATUSES:
        return STATUS_KIND_UNKNOWN
    if success is True:
        return STATUS_KIND_CONTRADICTION
    if success is False:
        return STATUS_KIND_NON_SUCCESS
    return STATUS_KIND_MISSING_SUCCESS


def artifact_status_errors(
    payload: dict[str, Any],
    kind: str,
    *,
    label: str = "quality artifact",
) -> list[str]:
    """Render structured errors for a non-success or contract-violating artifact."""

    status = payload.get("status")
    if kind == STATUS_KIND_UNKNOWN:
        known = ", ".join(repr(value) for value in NON_SUCCESS_STATUSES)
        return [f"{label} reports unknown status {status!r}; expected one of {known}"]
    if kind == STATUS_KIND_CONTRADICTION:
        return [f"{label} status {status!r} contradicts success=true"]
    if kind == STATUS_KIND_MISSING_STATUS:
        return [f"{label} reports success=false without a status"]
    if kind == STATUS_KIND_MISSING_SUCCESS:
        return [f"{label} status {status!r} is missing a boolean success flag"]

    errors = [f"{label} reports non-success status {status!r}"]
    results = payload.get("results")
    completed = len(results) if isinstance(results, list) else 0
    if status == STATUS_TIMEOUT:
        timeout = payload.get("timeout")
        if not isinstance(timeout, dict):
            errors.append(f"{label}.timeout must be an object for status {status!r}")
            return errors
        errors.append(
            f"{label} timed out during stage {timeout.get('stage')!r} "
            f"(limit_seconds={timeout.get('limit_seconds')!r}, "
            f"completed_results={completed}): {timeout.get('reason')!r}"
        )
        observation = timeout.get("timeout_observation")
        if timeout.get("client_side_only") is False:
            errors.append(
                f"{label} timeout recorded a confirmed server statement_timeout; "
                "backend state, cancellation completion, and transaction cleanup "
                "remain unobserved/unverified; partial results are not a success baseline"
            )
        else:
            errors.append(
                f"{label} timeout is a client-side subprocess limit only; "
                "server statement cancellation is not confirmed and partial results "
                "are not a success baseline"
            )
        if not isinstance(observation, dict):
            errors.append(
                f"{label} timeout observation was not recorded; backend state and "
                "cancellation remain unverified"
            )
    elif status == execution_budget.STATUS_EXHAUSTED:
        budget = payload.get("budget")
        if not isinstance(budget, dict):
            errors.append(f"{label}.budget must be an object for status {status!r}")
            return errors
        errors.append(
            f"{label} exhausted the run budget "
            f"(limit_kind={budget.get('limit_kind')!r}, "
            f"used={budget.get('used')!r}, limit={budget.get('limit')!r})"
        )
        errors.append(f"{label} partial results are not a success baseline")
    if completed:
        errors.append(
            f"{label} preserved {completed} partial result(s); "
            "partial output is not a success baseline"
        )
    return errors


def artifact_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve the original status, completion count, and timeout/budget fields."""

    results = payload.get("results")
    diagnostics: dict[str, Any] = {
        "status": payload.get("status"),
        "success": payload.get("success"),
        "completed_results": len(results) if isinstance(results, list) else None,
    }
    timeout = payload.get("timeout")
    if isinstance(timeout, dict):
        diagnostics["timeout"] = timeout
    budget = payload.get("budget")
    if isinstance(budget, dict):
        diagnostics["budget"] = budget
    return diagnostics


DEFAULT_DOCS: list[dict[str, Any]] = [
    {
        "doc_id": 1001,
        "target_uri": "https://quality.example/search",
        "text": "PostgreSQL BM25 검색 엔진 ranking document",
        "vector": [1.0, 0.0, 0.0, 0.0],
    },
    {
        "doc_id": 1002,
        "target_uri": "https://quality.example/vector",
        "text": "Lance vector search embeddings nearest neighbor retrieval",
        "vector": [0.0, 1.0, 0.0, 0.0],
    },
    {
        "doc_id": 1003,
        "target_uri": "https://quality.example/warc",
        "text": "WARC web archive capture metadata replay payload digest",
        "vector": [0.0, 0.0, 1.0, 0.0],
    },
    {
        "doc_id": 1004,
        "target_uri": "https://quality.example/noise",
        "text": "sourdough bread recipe kitchen fermentation notes",
        "vector": [0.0, 0.0, 0.0, 1.0],
    },
]

DEFAULT_QUERIES: list[dict[str, Any]] = [
    {
        "name": "korean_bm25",
        "query": "검색 엔진",
        "vector": [1.0, 0.0, 0.0, 0.0],
        "expected_doc_ids": [1001],
    },
    {
        "name": "vector_embeddings",
        "query": "vector embeddings",
        "vector": [0.0, 1.0, 0.0, 0.0],
        "expected_doc_ids": [1002],
    },
    {
        "name": "warc_metadata",
        "query": "WARC metadata",
        "vector": [0.0, 0.0, 1.0, 0.0],
        "expected_doc_ids": [1003],
    },
]


DEFAULT_FIXTURE: dict[str, Any] = {
    "name": "synthetic",
    "vector_dim": 4,
    "docs": DEFAULT_DOCS,
    "queries": DEFAULT_QUERIES,
}


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def vector_sql(values: list[float]) -> str:
    return "ARRAY[" + ",".join(f"{value:.6f}" for value in values) + "]::float4[]"


def as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def statement_timeout_ms_from_sql(sql: str) -> int | None:
    """Extract the opt-in server limit that this process injected, if any."""

    match = STATEMENT_TIMEOUT_SQL_PATTERN.search(sql)
    return int(match.group(1)) if match else None


def parse_sqlstate(stderr: str | bytes | None) -> str | None:
    text = as_text(stderr)
    match = re.search(r"SQLSTATE:\s*([0-9A-Z]{5})", text)
    if match is None:
        match = re.search(r"ERROR:\s+([0-9A-Z]{5}):", text)
    return match.group(1) if match else None


def is_statement_timeout_diagnostic(stderr: str | bytes | None) -> bool:
    return STATEMENT_TIMEOUT_REASON in as_text(stderr)


def run_sql(psql: str, sql: str, timeout: float | None = None) -> str:
    kwargs: dict[str, Any] = {
        "shell": True,
        "input": sql,
        "text": True,
        "capture_output": True,
        "env": {**os.environ, "LC_ALL": "C"},
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        result = subprocess.run(psql, **kwargs)
    except subprocess.TimeoutExpired as expired:
        raise SqlTimeout(
            timeout_seconds=timeout,
            stdout=expired.stdout,
            stderr=expired.stderr,
        ) from expired
    if result.returncode:
        effective_ms = statement_timeout_ms_from_sql(sql)
        if effective_ms is not None and is_statement_timeout_diagnostic(result.stderr):
            raise StatementTimeout(
                stdout=result.stdout,
                stderr=result.stderr,
                sqlstate=parse_sqlstate(result.stderr),
                effective_statement_timeout_ms=effective_ms,
            )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="")
        raise SystemExit(result.returncode)
    return result.stdout.strip()


def require_list(value: Any, source: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{source}: expected a non-empty list")
    return value


def require_doc_id(value: Any, source: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_DOC_ID:
        raise ValueError(
            f"{source}: doc_id must be a positive integer within PostgreSQL bigint range"
        )
    return value


def require_vector(value: Any, vector_dim: int, source: str) -> list[float]:
    if not isinstance(value, list):
        raise ValueError(f"{source}: vector must be a list")
    if len(value) != vector_dim:
        raise ValueError(f"{source}: vector has dim {len(value)}, expected {vector_dim}")
    vector: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{source}: vector contains non-numeric value")
        try:
            numeric = float(item)
        except OverflowError as exc:
            raise ValueError(
                f"{source}: vector contains non-finite or out-of-range value"
            ) from exc
        if not math.isfinite(numeric):
            raise ValueError(f"{source}: vector contains non-finite or out-of-range value")
        vector.append(numeric)
    return vector


def fixture_from_dict(
    raw: dict[str, Any],
    source: str,
    *,
    reject_duplicate_identities: bool = False,
) -> dict[str, Any]:
    vector_dim = raw.get("vector_dim")
    if isinstance(vector_dim, bool) or not isinstance(vector_dim, int) or vector_dim <= 0:
        raise ValueError(f"{source}: vector_dim must be a positive integer")

    docs = []
    seen_doc_ids: set[int] = set()
    duplicate_doc_ids: list[int] = []
    for index, doc in enumerate(require_list(raw.get("docs"), f"{source}.docs"), start=1):
        if not isinstance(doc, dict):
            raise ValueError(f"{source}.docs[{index}]: expected object")
        doc_id = require_doc_id(doc.get("doc_id"), f"{source}.docs[{index}].doc_id")
        if doc_id in seen_doc_ids and doc_id not in duplicate_doc_ids:
            duplicate_doc_ids.append(doc_id)
        seen_doc_ids.add(doc_id)
        text = str(doc["text"])
        raw_http_status = doc.get("http_status", 200)
        docs.append(
            {
                "doc_id": doc_id,
                "target_uri": str(doc.get("target_uri") or f"https://quality.example/{doc_id}"),
                "warc_date": str(doc.get("warc_date") or "2026-07-01T00:00:00Z"),
                "content_type": str(doc.get("content_type") or "text/plain"),
                "http_status": 200 if raw_http_status is None else int(raw_http_status),
                "source_file": str(doc.get("source_file") or source),
                "text": text,
                "vector": require_vector(doc.get("vector"), vector_dim, f"{source}.docs[{index}]"),
            }
        )
    if duplicate_doc_ids and reject_duplicate_identities:
        sample = ", ".join(str(doc_id) for doc_id in duplicate_doc_ids[:5])
        raise ValueError(f"{source}.docs: duplicate doc_id(s): {sample}")

    doc_ids = {doc["doc_id"] for doc in docs}
    queries = []
    seen_query_names: set[str] = set()
    duplicate_query_names: list[str] = []
    for index, query in enumerate(require_list(raw.get("queries"), f"{source}.queries"), start=1):
        if not isinstance(query, dict):
            raise ValueError(f"{source}.queries[{index}]: expected object")
        query_name = str(query["name"])
        if query_name in seen_query_names and query_name not in duplicate_query_names:
            duplicate_query_names.append(query_name)
        seen_query_names.add(query_name)
        expected = []
        seen_expected: set[int] = set()
        duplicate_expected: list[int] = []
        expected_doc_ids = require_list(
            query.get("expected_doc_ids"), f"{source}.queries[{index}].expected_doc_ids"
        )
        for expected_index, raw_doc_id in enumerate(expected_doc_ids, start=1):
            doc_id = require_doc_id(
                raw_doc_id,
                f"{source}.queries[{index}].expected_doc_ids[{expected_index}]",
            )
            if doc_id in seen_expected and doc_id not in duplicate_expected:
                duplicate_expected.append(doc_id)
            seen_expected.add(doc_id)
            expected.append(doc_id)
        if duplicate_expected:
            sample = ", ".join(str(doc_id) for doc_id in duplicate_expected[:5])
            raise ValueError(
                f"{source}.queries[{index}].expected_doc_ids: "
                f"duplicate expected doc_id(s): {sample}"
            )
        unknown = sorted(set(expected).difference(doc_ids))
        if unknown:
            sample = ", ".join(str(doc_id) for doc_id in unknown[:5])
            raise ValueError(f"{source}.queries[{index}]: unknown expected doc_id(s): {sample}")
        queries.append(
            {
                "name": query_name,
                "query": str(query["query"]),
                "vector": require_vector(
                    query.get("vector"), vector_dim, f"{source}.queries[{index}]"
                ),
                "expected_doc_ids": expected,
            }
        )
    if duplicate_query_names and reject_duplicate_identities:
        sample = ", ".join(repr(name) for name in duplicate_query_names[:5])
        raise ValueError(f"{source}.queries: duplicate name(s): {sample}")

    return {
        "name": str(raw.get("name") or Path(source).stem),
        "vector_dim": vector_dim,
        "docs": docs,
        "queries": queries,
    }


def load_fixture(
    path: Path | None,
    *,
    reject_duplicate_identities: bool = True,
) -> dict[str, Any]:
    if path is None:
        return fixture_from_dict(
            DEFAULT_FIXTURE,
            "synthetic",
            reject_duplicate_identities=reject_duplicate_identities,
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected JSON object")
    return fixture_from_dict(
        raw,
        str(path),
        reject_duplicate_identities=reject_duplicate_identities,
    )


def setup_sql(fixture: dict[str, Any], lance_uri: str) -> str:
    docs = fixture["docs"]
    vector_dim = int(fixture["vector_dim"])
    ids = ",".join(str(doc["doc_id"]) for doc in docs)
    labels = ",".join(sql_string(str(doc["doc_id"])) for doc in docs)
    flat = ",".join(f"{value:.6f}" for doc in docs for value in doc["vector"])
    lines = [
        "DROP EXTENSION IF EXISTS pgwarc_lance CASCADE;",
        "CREATE EXTENSION pgwarc_lance;",
        f"SELECT lance_create_table({sql_string(lance_uri)}, {vector_dim}, true);",
        "BEGIN;",
    ]
    for doc in docs:
        lines.append(
            "INSERT INTO pgwarc_lance.warc_record "
            "(doc_id, target_uri, warc_date, content_type, http_status, text_len, source_file) "
            f"VALUES ({doc['doc_id']}, {sql_string(doc['target_uri'])}, "
            f"{sql_string(doc['warc_date'])}::timestamptz, {sql_string(doc['content_type'])}, "
            f"{doc['http_status']}, {len(doc['text'])}, {sql_string(doc['source_file'])});"
        )
        lines.append(f"SELECT bm25_index_document({doc['doc_id']}, {sql_string(doc['text'])});")
    lines.extend(
        [
            "COMMIT;",
            (
                f"SELECT lance_insert_many({sql_string(lance_uri)}, ARRAY[{ids}]::bigint[], "
                f"ARRAY[{flat}]::float4[], {vector_dim}, ARRAY[{labels}]::text[]);"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def query_sql(
    query: dict[str, Any],
    lance_uri: str,
    k: int,
    statement_timeout_ms: int | None = None,
) -> str:
    statement = (
        "SELECT doc_id, round(score::numeric, 6), source "
        "FROM hybrid_warc_search("
        f"{sql_string(query['query'])}, {vector_sql(query['vector'])}, "
        f"{sql_string(lance_uri)}, {k}) "
        "ORDER BY score DESC, doc_id ASC;\n"
    )
    if statement_timeout_ms is None:
        return statement
    return (
        "\\set QUIET on\n"
        "\\set ON_ERROR_STOP on\n"
        "\\set VERBOSITY verbose\n"
        "BEGIN;\n"
        f"SET LOCAL statement_timeout = {int(statement_timeout_ms)};\n"
        f"{statement}"
        "COMMIT;\n"
    )


def parse_hits(output: str) -> list[dict[str, Any]]:
    hits = []
    for line in output.splitlines():
        if not line.strip():
            continue
        doc_id, score, source = line.split("|", maxsplit=2)
        hits.append({"doc_id": int(doc_id), "score": float(score), "source": source})
    return hits


def score_query(query: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    expected = set(query["expected_doc_ids"])
    returned = [hit["doc_id"] for hit in hits]
    reciprocal_rank = 0.0
    for index, doc_id in enumerate(returned, start=1):
        if doc_id in expected:
            reciprocal_rank = 1.0 / index
            break
    return {
        "name": query["name"],
        "query": query["query"],
        "expected_doc_ids": query["expected_doc_ids"],
        "returned_doc_ids": returned,
        "hit": reciprocal_rank > 0.0,
        "reciprocal_rank": reciprocal_rank,
        "hits": hits,
    }


def budget_from_args(args: argparse.Namespace) -> ExecutionBudget | None:
    """Build the optional run-scoped budget, rejecting partial/invalid config."""

    values = {
        execution_budget.LIMIT_ITERATIONS: args.max_iterations,
        execution_budget.LIMIT_QUERIES: args.max_queries,
        execution_budget.LIMIT_DEADLINE: args.deadline_seconds,
    }
    if not any(value is not None for value in values.values()):
        return None
    return ExecutionBudget(execution_budget.budget_config(values))


def validate_query_timeout(value: Any) -> float | None:
    """Validate the optional explicit query timeout, returning None when absent."""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("query timeout must be a finite positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("query timeout must be a finite positive number")
    return number


def _decimal_number(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite positive number")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{field} must be a finite positive number")
        number = Decimal(str(value))
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field} must be a finite positive number")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"{field} must be a finite positive number") from exc
    else:
        raise ValueError(f"{field} must be a finite positive number")
    if not number.is_finite():
        raise ValueError(f"{field} must be a finite positive number")
    return number


def validate_query_statement_timeout(value: Any) -> Decimal | None:
    """Validate the opt-in server limit, returning milliseconds as a Decimal.

    ``None`` means no server-side limit is injected. Any supplied value must be
    a finite positive number exactly expressible in PostgreSQL's integer
    millisecond ``statement_timeout`` domain (1..2147483647). Boundaries use
    :class:`~decimal.Decimal` rather than binary floats so values such as
    ``0.001`` are accepted at the 1ms edge and ``2147483.647`` at the maximum.
    """

    if value is None:
        return None
    number = _decimal_number(value, "query statement timeout")
    if number <= 0:
        raise ValueError("query statement timeout must be greater than zero")
    milliseconds = number * 1000
    if milliseconds < PG_STATEMENT_TIMEOUT_MIN_MS:
        raise ValueError(
            "query statement timeout must be at least "
            f"{PG_STATEMENT_TIMEOUT_MIN_MS}ms (got {milliseconds}ms)"
        )
    if milliseconds > PG_STATEMENT_TIMEOUT_MAX_MS:
        raise ValueError(
            "query statement timeout exceeds the PostgreSQL maximum of "
            f"{PG_STATEMENT_TIMEOUT_MAX_MS}ms (got {milliseconds}ms)"
        )
    return milliseconds


def seconds_to_milliseconds(seconds: float) -> Decimal:
    return Decimal(str(seconds)) * 1000


def resolve_statement_timeout_ms(
    requested_ms: Decimal,
    *,
    client_timeout_seconds: float | None,
    budget: ExecutionBudget | None,
) -> tuple[int | None, str]:
    """Resolve the effective server limit right before a query subprocess.

    The shortest of the explicit server limit, the client effective limit, and
    the remaining run deadline wins. The result is floored to an integer
    millisecond and is never zero: a cap below 1ms returns ``(None, source)`` so
    the caller refuses to start the query instead of sending ``0ms``.
    """

    candidates: list[tuple[Decimal, str]] = [(requested_ms, TIMEOUT_SOURCE_SERVER)]
    if client_timeout_seconds is not None:
        candidates.append(
            (seconds_to_milliseconds(client_timeout_seconds), TIMEOUT_SOURCE_CLIENT)
        )
    if budget is not None:
        candidates.append(
            (seconds_to_milliseconds(budget.remaining_seconds()), TIMEOUT_SOURCE_BUDGET)
        )
    minimum, source = min(candidates, key=lambda candidate: candidate[0])
    floored = int(minimum.to_integral_value(rounding=ROUND_FLOOR))
    if floored < PG_STATEMENT_TIMEOUT_MIN_MS:
        return None, source
    return floored, source


def decimal_json_number(value: Decimal | None) -> Any:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def effective_timeout(
    budget: ExecutionBudget | None,
    query_timeout_seconds: float | None,
) -> float | None:
    """Resolve the client-side subprocess timeout for the next SQL call.

    Without a run budget the explicit timeout (or None) is returned unchanged.
    With a run budget the shorter of the remaining deadline and the explicit
    timeout is used, and an already-spent deadline raises ``BudgetExhausted``
    before any subprocess is started.
    """

    if budget is None:
        return query_timeout_seconds
    if query_timeout_seconds is None:
        budget.check_deadline()
        remaining = budget.remaining_seconds()
        if remaining <= 0:
            raise BudgetExhausted(
                limit_kind=execution_budget.LIMIT_DEADLINE,
                used=round(budget.elapsed_seconds(), 6),
                limit=budget.config.deadline_seconds,
                detail="no remaining time to run SQL",
            )
        return remaining
    return budget.cap_timeout(query_timeout_seconds)


def evaluate_queries(
    *,
    queries: list[dict[str, Any]],
    psql: str,
    lance_uri: str,
    k: int,
    budget: ExecutionBudget | None = None,
    query_timeout_seconds: float | None = None,
    statement_timeout_ms: Decimal | None = None,
    run_sql_fn: Any = None,
) -> tuple[list[dict[str, Any]], BudgetExhausted | None]:
    """Evaluate queries under the shared budget, preserving partial results.

    Each query atomically charges one iteration and one query unit before the
    callback is invoked. When either limit is exhausted the charge raises
    before the N+1 query is sent, without consuming the other counter, and the
    already-scored results are returned unchanged.

    The client-side subprocess timeout for each query is resolved from the run
    budget and the explicit query timeout right before the callback. When the
    opt-in server limit is enabled, each query subprocess gets its own
    ``BEGIN; SET LOCAL statement_timeout; SELECT; COMMIT`` wrapper computed from
    the shortest of the server limit, the client effective limit, and the
    remaining run deadline. A cap below 1ms refuses to start the query. A
    ``SqlTimeout`` or ``StatementTimeout`` propagates with the completed results
    attached and no further query is sent.
    """

    runner = run_sql_fn if run_sql_fn is not None else run_sql
    results: list[dict[str, Any]] = []
    exhausted: BudgetExhausted | None = None
    for query in queries:
        if budget is not None:
            try:
                budget.charge_query_unit()
            except BudgetExhausted as error:
                exhausted = error
                break
        try:
            timeout = effective_timeout(budget, query_timeout_seconds)
        except BudgetExhausted as error:
            exhausted = error
            break
        server_ms: int | None = None
        server_source: str | None = None
        if statement_timeout_ms is not None:
            server_ms, server_source = resolve_statement_timeout_ms(
                statement_timeout_ms,
                client_timeout_seconds=query_timeout_seconds,
                budget=budget,
            )
            if server_ms is None:
                if server_source == TIMEOUT_SOURCE_BUDGET:
                    exhausted = BudgetExhausted(
                        limit_kind=execution_budget.LIMIT_DEADLINE,
                        used=round(budget.elapsed_seconds(), 6),
                        limit=budget.config.deadline_seconds,
                        detail=(
                            "remaining run deadline caps the server statement "
                            "timeout below 1ms"
                        ),
                    )
                    break
                gate = SqlTimeout(timeout_seconds=float(timeout))
                gate.stage = "query"
                gate.partial_results = list(results)
                gate.pre_run = True
                raise gate
        try:
            output = runner(
                psql,
                query_sql(query, lance_uri, k, statement_timeout_ms=server_ms),
                timeout=timeout,
            )
        except SqlTimeout as error:
            error.stage = "query"
            error.partial_results = list(results)
            raise
        except StatementTimeout as error:
            error.stage = "query"
            error.partial_results = list(results)
            if statement_timeout_ms is not None:
                error.requested_statement_timeout_ms = decimal_json_number(
                    statement_timeout_ms
                )
                error.effective_statement_timeout_ms = server_ms
                error.timeout_source = server_source or TIMEOUT_SOURCE_SERVER
            raise
        results.append(score_query(query, parse_hits(output)))
    return results, exhausted


def exhausted_payload(
    *,
    fixture: dict[str, Any],
    results: list[dict[str, Any]],
    k: int,
    lance_uri: str,
    exhausted: BudgetExhausted,
) -> dict[str, Any]:
    """Build a non-success payload that preserves partial query results."""

    return {
        "benchmark": "retrieval_quality_smoke",
        "fixture": fixture["name"],
        "doc_count": len(fixture["docs"]),
        "query_count": len(fixture["queries"]),
        "vector_dim": fixture["vector_dim"],
        "k": k,
        "lance_uri": lance_uri,
        "status": execution_budget.STATUS_EXHAUSTED,
        "success": False,
        "budget": exhausted.to_status(partial_results=results),
        "results": results,
    }


def exhausted_markdown(exhausted: BudgetExhausted, results: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            "# quality eval incomplete (budget exhausted)",
            "",
            f"- status: `{execution_budget.STATUS_EXHAUSTED}`",
            f"- limit_kind: `{exhausted.limit_kind}`",
            f"- used: `{exhausted.used}`",
            f"- limit: `{exhausted.limit}`",
            f"- partial_results: `{len(results)}`",
            "",
            "Partial output is not a successful quality baseline.",
            "",
        ]
    )


def timeout_observation(statement_outcome: str) -> dict[str, str]:
    """Describe what a timeout artifact actually observed about the server.

    Only ``statement_outcome`` can be reported, and only from the existing
    confirmed server statement timeout path. Backend state after the timeout,
    cancellation completion, and transaction cleanup are explicitly
    unobserved: ``unverified`` means not checked, never that the follow-up
    failed or is still running.
    """

    return {
        "statement_outcome": statement_outcome,
        "backend_state_after_timeout": BACKEND_STATE_AFTER_TIMEOUT_UNOBSERVED,
        "cancellation_completion": CANCELLATION_COMPLETION_UNVERIFIED,
        "transaction_cleanup": TRANSACTION_CLEANUP_UNVERIFIED,
    }


def timeout_payload(
    *,
    fixture: dict[str, Any],
    results: list[dict[str, Any]],
    k: int,
    lance_uri: str,
    stage: str,
    limit_seconds: float,
    reason: str | None = None,
    client_side_only: bool = True,
    statement_outcome: str = STATEMENT_OUTCOME_UNKNOWN,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a non-success payload for a query/setup timeout boundary."""

    timeout: dict[str, Any] = {
        "stage": stage,
        "reason": reason or f"{stage} SQL exceeded the effective client-side timeout",
        "limit_seconds": limit_seconds,
        "completed_results": len(results),
        "partial_result_preserved": bool(results),
        "partial_result_count": len(results),
        "client_side_only": client_side_only,
        "timeout_observation": timeout_observation(statement_outcome),
    }
    if extra:
        timeout.update(extra)
    return {
        "benchmark": "retrieval_quality_smoke",
        "fixture": fixture["name"],
        "doc_count": len(fixture["docs"]),
        "query_count": len(fixture["queries"]),
        "vector_dim": fixture["vector_dim"],
        "k": k,
        "lance_uri": lance_uri,
        "status": STATUS_TIMEOUT,
        "success": False,
        "timeout": timeout,
        "results": results,
    }


def timeout_markdown(stage: str, limit_seconds: float, results: list[dict[str, Any]]) -> str:
    observation = timeout_observation(STATEMENT_OUTCOME_UNKNOWN)
    return "\n".join(
        [
            f"# quality eval timed out ({stage})",
            "",
            f"- status: `{STATUS_TIMEOUT}`",
            f"- stage: `{stage}`",
            f"- limit_seconds: `{limit_seconds}`",
            f"- partial_results: `{len(results)}`",
            f"- timeout_observation.statement_outcome: `{observation['statement_outcome']}`",
            f"- timeout_observation.backend_state_after_timeout: `{observation['backend_state_after_timeout']}`",
            f"- timeout_observation.cancellation_completion: `{observation['cancellation_completion']}`",
            f"- timeout_observation.transaction_cleanup: `{observation['transaction_cleanup']}`",
            "",
            "This is a client subprocess boundary failure, not server-side query cancellation.",
            "Backend state, cancellation completion, and transaction cleanup are unobserved.",
            "Partial output is not a successful quality baseline.",
            "",
        ]
    )


def server_timeout_markdown(stage: str, error: StatementTimeout) -> str:
    observation = timeout_observation(STATEMENT_OUTCOME_SERVER_TIMEOUT)
    return "\n".join(
        [
            f"# quality eval timed out ({stage})",
            "",
            f"- status: `{STATUS_TIMEOUT}`",
            f"- stage: `{stage}`",
            f"- timeout_source: `{error.timeout_source}`",
            f"- requested_statement_timeout_ms: `{error.requested_statement_timeout_ms}`",
            f"- effective_statement_timeout_ms: `{error.effective_statement_timeout_ms}`",
            f"- sqlstate: `{error.sqlstate}`",
            f"- partial_results: `{len(error.partial_results or [])}`",
            f"- timeout_observation.statement_outcome: `{observation['statement_outcome']}`",
            f"- timeout_observation.backend_state_after_timeout: `{observation['backend_state_after_timeout']}`",
            f"- timeout_observation.cancellation_completion: `{observation['cancellation_completion']}`",
            f"- timeout_observation.transaction_cleanup: `{observation['transaction_cleanup']}`",
            "",
            "The opt-in query-only server statement_timeout was observed from the "
            "stable statement-timeout diagnostic.",
            "Backend termination, rollback completion, and resource reclamation are "
            "not guaranteed.",
            "Partial output is not a successful quality baseline.",
            "",
        ]
    )


def write_json(path: Path | None, payload: dict[str, Any]) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path | None, content: str) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def markdown_report(results: list[dict[str, Any]], hit_rate: float, mrr: float, k: int) -> str:
    lines = [
        f"hit_rate_at_{k}={hit_rate:.3f} mrr_at_{k}={mrr:.3f}",
        f"| query | expected | returned | hit@{k} | reciprocal_rank |",
        "|---|---|---|---:|---:|",
    ]
    for result in results:
        lines.append(
            f"| {result['name']} | {result['expected_doc_ids']} | "
            f"{result['returned_doc_ids']} | {int(result['hit'])} | "
            f"{result['reciprocal_rank']:.3f} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--psql", default=DEFAULT_PSQL)
    parser.add_argument("--lance-uri", default="/tmp/pgwarc_lance_quality.lance")
    parser.add_argument("--fixture-json", type=Path, help="labeled quality fixture JSON")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--deadline-seconds", type=float, default=None)
    parser.add_argument("--query-timeout-seconds", type=float, default=None)
    parser.add_argument("--query-statement-timeout-seconds", default=None)
    parser.add_argument("--budget-status-json", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args()


def emit_timeout(
    args: argparse.Namespace,
    fixture: dict[str, Any],
    *,
    stage: str,
    error: SqlTimeout,
    results: list[dict[str, Any]],
) -> int:
    payload = timeout_payload(
        fixture=fixture,
        results=results,
        k=args.k,
        lance_uri=args.lance_uri,
        stage=stage,
        limit_seconds=error.timeout_seconds,
        statement_outcome=STATEMENT_OUTCOME_UNKNOWN,
    )
    write_json(args.json_output, payload)
    write_text(args.markdown_output, timeout_markdown(stage, error.timeout_seconds, results))
    print(
        f"quality eval timed out during {stage}: "
        f"limit={error.timeout_seconds}s completed_results={len(results)}",
        file=sys.stderr,
    )
    return EXIT_TIMEOUT


def emit_server_timeout(
    args: argparse.Namespace,
    fixture: dict[str, Any],
    *,
    stage: str,
    error: StatementTimeout,
    results: list[dict[str, Any]],
) -> int:
    effective_ms = error.effective_statement_timeout_ms
    limit_seconds = float(effective_ms) / 1000 if effective_ms else 0.0
    payload = timeout_payload(
        fixture=fixture,
        results=results,
        k=args.k,
        lance_uri=args.lance_uri,
        stage=stage,
        limit_seconds=limit_seconds,
        reason=(
            f"{stage} SQL was canceled by the opt-in PostgreSQL server "
            "statement timeout"
        ),
        client_side_only=False,
        statement_outcome=STATEMENT_OUTCOME_SERVER_TIMEOUT,
        extra={
            "requested_statement_timeout_ms": error.requested_statement_timeout_ms,
            "effective_statement_timeout_ms": effective_ms,
            "timeout_source": error.timeout_source,
            "sqlstate": error.sqlstate,
        },
    )
    write_json(args.json_output, payload)
    write_text(args.markdown_output, server_timeout_markdown(stage, error))
    print(
        f"quality eval server statement timeout during {stage}: "
        f"effective_ms={effective_ms} source={error.timeout_source} "
        f"sqlstate={error.sqlstate} completed_results={len(results)}",
        file=sys.stderr,
    )
    return EXIT_TIMEOUT


def main() -> int:
    args = parse_args()
    if args.k <= 0:
        raise SystemExit("--k must be positive")

    try:
        query_timeout = validate_query_timeout(args.query_timeout_seconds)
    except ValueError as error:
        print(f"eval-quality query timeout rejected: {error}", file=sys.stderr)
        return 2

    try:
        statement_timeout_ms = validate_query_statement_timeout(
            args.query_statement_timeout_seconds
        )
    except ValueError as error:
        print(
            f"eval-quality query statement timeout rejected: {error}",
            file=sys.stderr,
        )
        return 2

    try:
        budget = budget_from_args(args)
    except BudgetConfigError as error:
        print(f"eval-quality budget rejected: {error}", file=sys.stderr)
        return 2

    fixture = load_fixture(args.fixture_json)

    setup_timeout = query_timeout
    if budget is not None:
        try:
            setup_timeout = effective_timeout(budget, query_timeout)
        except BudgetExhausted as error:
            payload = exhausted_payload(
                fixture=fixture,
                results=[],
                k=args.k,
                lance_uri=args.lance_uri,
                exhausted=error,
            )
            write_json(args.json_output, payload)
            write_text(args.markdown_output, exhausted_markdown(error, []))
            write_json(args.budget_status_json, payload["budget"])
            print(payload["budget"]["detail"], file=sys.stderr)
            return 3

    try:
        run_sql(args.psql, setup_sql(fixture, args.lance_uri), timeout=setup_timeout)
    except SqlTimeout as error:
        return emit_timeout(args, fixture, stage="setup", error=error, results=[])
    except StatementTimeout as error:
        error.stage = "setup"
        error.partial_results = []
        return emit_server_timeout(args, fixture, stage="setup", error=error, results=[])

    try:
        results, exhausted = evaluate_queries(
            queries=fixture["queries"],
            psql=args.psql,
            lance_uri=args.lance_uri,
            k=args.k,
            budget=budget,
            query_timeout_seconds=query_timeout,
            statement_timeout_ms=statement_timeout_ms,
        )
    except SqlTimeout as error:
        return emit_timeout(
            args,
            fixture,
            stage="query",
            error=error,
            results=error.partial_results or [],
        )
    except StatementTimeout as error:
        return emit_server_timeout(
            args,
            fixture,
            stage="query",
            error=error,
            results=error.partial_results or [],
        )

    if exhausted is not None:
        payload = exhausted_payload(
            fixture=fixture,
            results=results,
            k=args.k,
            lance_uri=args.lance_uri,
            exhausted=exhausted,
        )
        write_json(args.json_output, payload)
        write_text(args.markdown_output, exhausted_markdown(exhausted, results))
        write_json(args.budget_status_json, payload["budget"])
        print(
            "quality eval stopped early: "
            f"{exhausted.limit_kind} used={exhausted.used} limit={exhausted.limit}",
            file=sys.stderr,
        )
        return 3

    hit_rate = sum(1 for result in results if result["hit"]) / len(results)
    mrr = sum(float(result["reciprocal_rank"]) for result in results) / len(results)
    payload = {
        "benchmark": "retrieval_quality_smoke",
        "fixture": fixture["name"],
        "doc_count": len(fixture["docs"]),
        "query_count": len(fixture["queries"]),
        "vector_dim": fixture["vector_dim"],
        "k": args.k,
        "lance_uri": args.lance_uri,
        "hit_rate_at_k": hit_rate,
        "mrr_at_k": mrr,
        "results": results,
    }
    if budget is not None:
        payload["budget"] = budget.status()
    markdown = markdown_report(results, hit_rate, mrr, args.k)
    print(markdown, end="")
    write_json(args.json_output, payload)
    write_text(args.markdown_output, markdown)
    if budget is not None:
        write_json(args.budget_status_json, budget.status())
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
