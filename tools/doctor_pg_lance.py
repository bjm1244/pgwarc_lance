#!/usr/bin/env python3
"""Read-only PG–Lance consistency doctor for one Lance dataset URI.

The report deliberately separates observable mismatches from vector revisions
that the current storage format cannot prove. It never repairs data.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shlex
import subprocess
import sys
from collections import Counter
from typing import Any, Sequence


DEFAULT_PSQL = "psql -X -v ON_ERROR_STOP=1"
DEFAULT_SCAN_ROWS = 100_000
MAX_SCAN_ROWS = 250_000


class DoctorError(RuntimeError):
    """The requested read-only inventory could not be completed."""


def _json_rows(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise DoctorError(f"database response field {field!r} is not a row list")
    if any(not isinstance(row, dict) for row in value):
        raise DoctorError(f"database response field {field!r} contains an invalid row")
    return value


def _revision_fingerprint(row: dict[str, Any]) -> str | None:
    """Fingerprint the PG-side record snapshot without exposing document text."""
    if not row.get("has_warc"):
        return None
    payload = {
        "doc_id": int(row["doc_id"]),
        "target_uri": row.get("target_uri"),
        "content_type": row.get("content_type"),
        "http_status": row.get("http_status"),
        "text_len": row.get("text_len"),
        "payload_digest": row.get("payload_digest"),
        "bm25_content_md5": row.get("bm25_content_md5"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def reconcile(pg_state: dict[str, Any], lance_state: dict[str, Any], max_rows: int) -> dict[str, Any]:
    """Summarize a complete inventory. Inputs contain no raw document text."""
    pg_rows = _json_rows(pg_state.get("rows"), "PostgreSQL rows")
    lance_rows = _json_rows(lance_state.get("rows"), "Lance rows")
    pg_count = int(pg_state.get("row_count", -1))
    lance_count = int(lance_state.get("dataset_count", -1))
    if pg_count < 0 or lance_count < 0:
        raise DoctorError("database response has an invalid row count")

    pending_state = pg_state.get("unfinished_jobs", [])
    if isinstance(pending_state, list):
        pending_rows = _json_rows(pending_state, "unfinished jobs")
        pending_count = len(pending_rows)
    elif isinstance(pending_state, dict):
        pending_rows = _json_rows(pending_state.get("rows"), "unfinished jobs")
        pending_count = int(pending_state.get("job_count", -1))
        if pending_count < 0:
            raise DoctorError("database response has an invalid unfinished-job count")
    else:
        raise DoctorError("database response has an invalid unfinished-job inventory")

    revision_rows = _json_rows(pg_state.get("revision_rows", []), "document revisions")
    revision_count = int(pg_state.get("revision_count", len(revision_rows)))
    if revision_count < 0:
        raise DoctorError("database response has an invalid document revision count")
    complete = (
        pg_count <= max_rows
        and lance_count <= max_rows
        and pending_count <= max_rows
        and revision_count <= max_rows
    )
    if complete and (pg_count != len(pg_rows) or lance_count != len(lance_rows)):
        raise DoctorError("database inventory row count does not match returned rows")
    if complete and pending_count != len(pending_rows):
        raise DoctorError("database unfinished-job count does not match returned rows")
    if complete and revision_count != len(revision_rows):
        raise DoctorError("database document revision count does not match returned rows")

    pg_by_id: dict[int, dict[str, Any]] = {}
    duplicate_pg_ids: set[int] = set()
    for row in pg_rows:
        doc_id = int(row["doc_id"])
        if doc_id in pg_by_id:
            duplicate_pg_ids.add(doc_id)
        pg_by_id[doc_id] = row

    lance_counts: Counter[int] = Counter()
    lance_ids: set[int] = set()
    duplicate_lance_ids: set[int] = set()
    stale_label_ids: set[int] = set()
    for row in lance_rows:
        doc_id = int(row["doc_id"])
        lance_counts[doc_id] += 1
        lance_ids.add(doc_id)
        if lance_counts[doc_id] > 1:
            duplicate_lance_ids.add(doc_id)
        if row.get("has_warc") and not row.get("label_matches_metadata"):
            stale_label_ids.add(doc_id)

    pg_ids = set(pg_by_id)
    warc_ids = {doc_id for doc_id, row in pg_by_id.items() if row.get("has_warc")}
    tombstone_ids = {
        int(row["doc_id"]) for row in revision_rows if row.get("is_deleted") is True
    }
    expected_revisions = {
        int(row["doc_id"]): str(row["expected_revision"])
        for row in revision_rows if row.get("is_deleted") is not True
    }
    pending_ids = {
        int(doc_id)
        for job in pending_rows
        for doc_id in job.get("ids", [])
    }
    expected_vector_ids = set(expected_revisions)
    missing_vector_candidates = expected_vector_ids - lance_ids - pending_ids
    deleted_vector_ids = lance_ids & tombstone_ids
    deleted_metadata_ids = pg_ids & tombstone_ids
    orphan_lance_ids = lance_ids - pg_ids - tombstone_ids
    orphan_revision_ids = set(expected_revisions) - pg_ids
    untracked_vector_ids = (lance_ids & pg_ids) - set(expected_revisions)
    lance_revision_by_id = {
        int(row["doc_id"]): row.get("revision") for row in lance_rows
    }
    stale_revision_ids = {
        doc_id for doc_id, revision in expected_revisions.items()
        if doc_id in lance_revision_by_id and lance_revision_by_id[doc_id] != revision
    }
    versioned_expected_ids = set(expected_revisions) & lance_ids
    pg_revision_mismatch_ids = {
        doc_id for doc_id, revision in expected_revisions.items()
        if doc_id in pg_by_id and _revision_fingerprint(pg_by_id[doc_id]) != revision
    }
    warc_without_bm25 = {
        doc_id for doc_id, row in pg_by_id.items()
        if row.get("has_warc") and not row.get("has_bm25")
    }
    revision_fingerprints = sum(
        _revision_fingerprint(row) is not None for row in pg_by_id.values()
    )
    findings = {
        "duplicate_postgres_ids": len(duplicate_pg_ids),
        "duplicate_lance_ids": len(duplicate_lance_ids),
        "missing_vector_candidates": len(missing_vector_candidates),
        "orphan_lance_ids": len(orphan_lance_ids),
        "orphan_revision_ids": len(orphan_revision_ids),
        "deleted_vector_ids": len(deleted_vector_ids),
        "deleted_metadata_ids": len(deleted_metadata_ids),
        "untracked_vector_ids": len(untracked_vector_ids),
        "stale_vector_revisions": len(stale_revision_ids),
        "postgres_revision_mismatches": len(pg_revision_mismatch_ids),
        "stale_label_ids": len(stale_label_ids),
        "warc_without_bm25": len(warc_without_bm25),
        "unfinished_outbox_jobs": pending_count,
        "unfinished_outbox_document_ids": len(pending_ids),
    }
    has_findings = any(value for value in findings.values())
    return {
        "schema_version": 1,
        "status": "incomplete" if not complete else ("findings" if has_findings else "no_observed_mismatch"),
        "read_only": True,
        "scan_complete": complete,
        "reconciliation_complete": False,
        "row_limit": max_rows,
        "counts": {
            "postgres_documents": pg_count,
            "lance_rows": lance_count,
            "unfinished_outbox_jobs": pending_count,
            "expected_revision_documents": len(expected_revisions),
            "tombstone_documents": len(tombstone_ids),
            "warc_documents": len(warc_ids) if complete else None,
            "pg_revision_fingerprints": revision_fingerprints if complete else None,
        },
        "findings": findings if complete else None,
        "sample_doc_ids": {
            "duplicate_lance": sorted(duplicate_lance_ids)[:20] if complete else [],
            "missing_vector_candidates": sorted(missing_vector_candidates)[:20] if complete else [],
            "orphan_lance": sorted(orphan_lance_ids)[:20] if complete else [],
            "orphan_revision": sorted(orphan_revision_ids)[:20] if complete else [],
            "deleted_vector": sorted(deleted_vector_ids)[:20] if complete else [],
            "deleted_metadata": sorted(deleted_metadata_ids)[:20] if complete else [],
            "untracked_vector": sorted(untracked_vector_ids)[:20] if complete else [],
            "stale_vector_revision": sorted(stale_revision_ids)[:20] if complete else [],
            "postgres_revision_mismatch": sorted(pg_revision_mismatch_ids)[:20] if complete else [],
            "stale_label": sorted(stale_label_ids)[:20] if complete else [],
            "unfinished_outbox": sorted(pending_ids)[:20] if complete else [],
        },
        "revision_check": {
            "postgres_fingerprint": "sha256(doc_id,target_uri,content_type,http_status,text_len,payload_digest,bm25_content_md5)",
            "postgres_documents_scoped_to_lance_uri": bool(expected_revisions),
            "lance_revision_persisted": bool(versioned_expected_ids)
            and all(lance_revision_by_id.get(doc_id) is not None for doc_id in versioned_expected_ids),
            "exact_stale_vector_detection": (
                "available by comparing URI-scoped expected revisions with each Lance row; "
                "embedding-model identity is not part of this ingestion revision"
            ),
            "label_mismatch_detection": "available when WARC metadata exists",
        },
        "snapshot": {
            "postgres": "REPEATABLE READ READ ONLY",
            "lance": "one scan snapshot",
            "cross_store_atomic_snapshot": False,
        },
        "missing_vector_semantics": "active URI-scoped expected documents from the revision map; pending IDs and tombstones are excluded",
        "destructive_actions": [],
    }


def inventory(uri: str, psql: Sequence[str], max_rows: int, timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if not uri:
        raise DoctorError("Lance URI must not be empty")
    if max_rows < 1:
        raise DoctorError("max_rows must be positive")

    uri_b64 = base64.b64encode(uri.encode("utf-8")).decode("ascii")
    sql = r"""BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;
SELECT 'pg' || E'\t' || jsonb_build_object(
    'row_count', (SELECT count(*) FROM (
        SELECT doc_id FROM pgwarc_lance.bm25_doc
        UNION SELECT doc_id FROM pgwarc_lance.warc_record
    ) AS ids),
    'revision_count', (SELECT count(*) FROM pgwarc_lance.lance_document_revision
        WHERE uri = convert_from(decode('__URI_B64__', 'base64'), 'UTF8')),
    'revision_rows', COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'doc_id', doc_id, 'expected_revision', expected_revision,
            'is_deleted', is_deleted
        ) ORDER BY doc_id)
        FROM (
            SELECT doc_id, expected_revision, is_deleted
            FROM pgwarc_lance.lance_document_revision
            WHERE uri = convert_from(decode('__URI_B64__', 'base64'), 'UTF8')
            ORDER BY doc_id LIMIT __SCAN_LIMIT__
        ) AS revisions
    ), '[]'::jsonb),
    'rows', COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'doc_id', ids.doc_id,
            'has_bm25', b.doc_id IS NOT NULL,
            'has_warc', w.doc_id IS NOT NULL,
            'text_len', w.text_len,
            'target_uri', w.target_uri,
            'content_type', w.content_type,
            'http_status', w.http_status,
            'payload_digest', w.payload_digest,
            'bm25_content_md5', CASE WHEN b.doc_id IS NULL THEN NULL ELSE md5(COALESCE(b.content, '')) END,
            'expected_revision', r.expected_revision,
            'is_deleted', r.is_deleted
        ) ORDER BY ids.doc_id)
        FROM (
            SELECT doc_id FROM pgwarc_lance.bm25_doc
            UNION SELECT doc_id FROM pgwarc_lance.warc_record
            ORDER BY doc_id LIMIT __SCAN_LIMIT__
        ) AS ids
        LEFT JOIN pgwarc_lance.bm25_doc AS b USING (doc_id)
        LEFT JOIN pgwarc_lance.warc_record AS w USING (doc_id)
        LEFT JOIN pgwarc_lance.lance_document_revision AS r
          ON r.doc_id = ids.doc_id
         AND r.uri = convert_from(decode('__URI_B64__', 'base64'), 'UTF8')
    ), '[]'::jsonb)
)::text;
WITH lance_snapshot AS MATERIALIZED (
    SELECT l.doc_id,
           l.revision,
           w.doc_id IS NOT NULL AS has_warc,
           CASE WHEN w.doc_id IS NULL THEN NULL
                ELSE l.label IS NOT DISTINCT FROM COALESCE(w.target_uri, l.doc_id::text) END AS label_matches_metadata
    FROM lance_scan_document_state(convert_from(decode('__URI_B64__', 'base64'), 'UTF8'), __SCAN_LIMIT__) AS l
    LEFT JOIN pgwarc_lance.warc_record AS w ON w.doc_id = l.doc_id
)
SELECT 'lance' || E'\t' || jsonb_build_object(
    'dataset_count', (SELECT count(*) FROM lance_snapshot),
    'rows', COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'doc_id', doc_id,
        'revision', revision,
        'has_warc', has_warc,
        'label_matches_metadata', label_matches_metadata
    ) ORDER BY doc_id) FROM lance_snapshot), '[]'::jsonb)
)::text;
SELECT 'outbox' || E'\t' || jsonb_build_object(
    'job_count', (SELECT count(*) FROM pgwarc_lance.lance_outbox
        WHERE uri = convert_from(decode('__URI_B64__', 'base64'), 'UTF8') AND state <> 'completed'),
    'rows', COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'job_id', jobs.id, 'state', jobs.state, 'ids', jobs.ids
    ) ORDER BY jobs.id) FROM (
        SELECT id, state, ids FROM pgwarc_lance.lance_outbox
        WHERE uri = convert_from(decode('__URI_B64__', 'base64'), 'UTF8') AND state <> 'completed'
        ORDER BY id LIMIT __SCAN_LIMIT__
    ) AS jobs), '[]'::jsonb)
)::text;
ROLLBACK;
"""
    sql = sql.replace("__URI_B64__", uri_b64).replace("__SCAN_LIMIT__", str(max_rows + 1))
    command = list(psql) + ["-X", "-q", "-A", "-t", "-F", "\t", "-v", "ON_ERROR_STOP=1"]
    try:
        result = subprocess.run(command, input=sql, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DoctorError(f"read-only PostgreSQL inventory could not run ({type(exc).__name__})") from exc
    if result.returncode:
        raise DoctorError(f"read-only PostgreSQL inventory failed (psql exit {result.returncode})")

    decoded: dict[str, Any] = {}
    for line in result.stdout.splitlines():
        key, separator, payload = line.partition("\t")
        if not separator or key not in {"pg", "lance", "outbox"} or key in decoded:
            raise DoctorError("psql returned an unexpected inventory format")
        try:
            decoded[key] = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise DoctorError("psql returned invalid inventory JSON") from exc
    if set(decoded) != {"pg", "lance", "outbox"}:
        raise DoctorError("psql did not return every read-only inventory section")
    decoded["pg"]["unfinished_jobs"] = decoded.pop("outbox")
    return decoded["pg"], decoded["lance"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", required=True, help="Lance dataset URI; omitted from the report")
    parser.add_argument("--psql", default=DEFAULT_PSQL, help="psql command and connection options")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_SCAN_ROWS)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.max_rows > MAX_SCAN_ROWS:
            raise DoctorError(f"max_rows cannot exceed the memory guard ({MAX_SCAN_ROWS})")
        if args.timeout_seconds < 1:
            raise DoctorError("timeout_seconds must be positive")
        pg_state, lance_state = inventory(args.uri, shlex.split(args.psql), args.max_rows, args.timeout_seconds)
        report = reconcile(pg_state, lance_state, args.max_rows)
    except DoctorError as exc:
        print(f"doctor_pg_lance: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["scan_complete"]:
        return 3
    if report["status"] == "findings":
        return 1
    if not report["reconciliation_complete"]:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
