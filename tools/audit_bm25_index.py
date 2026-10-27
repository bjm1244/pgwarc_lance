#!/usr/bin/env python3
"""Read-only audit of BM25 postings against the stored document content."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools import warc_importer

DEFAULT_PSQL = (
    "psql -h localhost -p 55432 -U pgwarc_lance "
    "-d pgwarc_lance_test -v ON_ERROR_STOP=1"
)
CONTENT_MARKER = "__PGWARC_BM25_CONTENT_DONE__"
POSTING_MARKER = "__PGWARC_BM25_POSTINGS_DONE__"


class AuditError(RuntimeError):
    """The database snapshot could not be fully checked."""


@dataclass(frozen=True)
class ExpectedDocument:
    doc_id: int
    stored_doc_len: int
    expected_postings: int | None
    expected_tf: int | None
    expected_sha256: str | None


def posting_fingerprint(frequencies: dict[str, int]) -> tuple[int, int, str]:
    """Fingerprint sorted stored-key/tf pairs without retaining source terms."""
    stored: dict[str, int] = {}
    source_terms: dict[str, str] = {}
    for source_term, tf in frequencies.items():
        key, _original_term = warc_importer.bm25_storage_term(source_term)
        previous = source_terms.setdefault(key, source_term)
        if previous != source_term:
            raise AuditError("two distinct source terms map to one BM25 posting key")
        if tf <= 0:
            raise AuditError("tokenizer returned a non-positive term frequency")
        stored[key] = tf

    digest = hashlib.sha256()
    for key in sorted(stored):
        encoded = key.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(stored[key].to_bytes(8, "big"))
    return len(stored), sum(stored.values()), digest.hexdigest()


def expected_documents(rows: Iterable[Sequence[str]]) -> list[ExpectedDocument]:
    documents: list[ExpectedDocument] = []
    previous_doc_id: int | None = None
    for row in rows:
        if len(row) != 4:
            raise AuditError("content COPY row does not contain four fields")
        try:
            doc_id, stored_doc_len = int(row[0]), int(row[3])
        except ValueError as exc:
            raise AuditError("content COPY row has an invalid integer field") from exc
        if row[1] not in {"t", "f"}:
            raise AuditError("content COPY row has an invalid content-present flag")
        if previous_doc_id is not None and doc_id <= previous_doc_id:
            raise AuditError("content COPY rows are not uniquely ordered by doc_id")
        if stored_doc_len < 0:
            raise AuditError("bm25_doc.doc_len is negative")
        previous_doc_id = doc_id

        if row[1] == "f":
            documents.append(ExpectedDocument(doc_id, stored_doc_len, None, None, None))
            continue
        frequencies = warc_importer.bm25_term_frequencies(row[2])
        if any(
            len(term.encode("utf-8")) > warc_importer.MAX_SEARCHABLE_TERM_BYTES
            for term in frequencies
        ):
            raise AuditError(
                "document content contains a token above the searchable byte limit"
            )
        posting_count, total_tf, fingerprint = posting_fingerprint(frequencies)
        documents.append(
            ExpectedDocument(
                doc_id,
                stored_doc_len,
                posting_count,
                total_tf,
                fingerprint,
            )
        )
    return documents


def compare_postings(
    documents: Sequence[ExpectedDocument],
    rows: Iterable[Sequence[str]],
    max_mismatch_docs: int = 100,
) -> dict[str, object]:
    if max_mismatch_docs <= 0:
        raise ValueError("max_mismatch_docs must be positive")

    totals = {
        "source_postings": 0,
        "source_tf": 0,
        "expected_postings": sum(d.expected_postings or 0 for d in documents),
        "expected_tf": sum(d.expected_tf or 0 for d in documents),
        "stored_doc_len": sum(d.stored_doc_len for d in documents),
        "doc_len_vs_source_tf_mismatches": 0,
        "doc_len_vs_content_tf_mismatches": 0,
        "posting_mismatch_docs": 0,
        "unverifiable_docs": 0,
    }
    mismatches: list[dict[str, int | None]] = []
    unverifiable: list[int] = []
    expected_index = 0

    def check_document(
        expected: ExpectedDocument,
        observed: dict[str, int],
    ) -> None:
        source_tf = sum(observed.values())
        totals["source_postings"] += len(observed)
        totals["source_tf"] += source_tf
        if expected.stored_doc_len != source_tf:
            totals["doc_len_vs_source_tf_mismatches"] += 1
        if expected.expected_tf is None:
            totals["unverifiable_docs"] += 1
            if len(unverifiable) < max_mismatch_docs:
                unverifiable.append(expected.doc_id)
            return

        if expected.stored_doc_len != expected.expected_tf:
            totals["doc_len_vs_content_tf_mismatches"] += 1
        source_count, _source_tf, source_fingerprint = posting_fingerprint(observed)
        matches = (
            source_count == expected.expected_postings
            and source_tf == expected.expected_tf
            and source_fingerprint == expected.expected_sha256
            and expected.stored_doc_len == expected.expected_tf
        )
        if not matches:
            totals["posting_mismatch_docs"] += 1
            if len(mismatches) < max_mismatch_docs:
                mismatches.append(
                    {
                        "doc_id": expected.doc_id,
                        "stored_doc_len": expected.stored_doc_len,
                        "source_postings": source_count,
                        "expected_postings": expected.expected_postings,
                        "source_tf": source_tf,
                        "expected_tf": expected.expected_tf,
                    }
                )

    # `pending` and this explicit iterator let the audit merge two doc_id-ordered
    # streams while holding only one document's stored posting rows in memory.
    posting_iter = iter(rows)
    pending = next(posting_iter, None)
    for expected in documents:
        observed: dict[str, int] = {}
        if pending is not None:
            try:
                posting_doc_id = int(pending[0])
            except (IndexError, ValueError) as exc:
                raise AuditError("posting COPY row has an invalid document id") from exc
            if posting_doc_id < expected.doc_id:
                raise AuditError("posting COPY contains a document absent from the content scope")
            while pending is not None and posting_doc_id == expected.doc_id:
                if len(pending) != 3:
                    raise AuditError("posting COPY row does not contain three fields")
                term = pending[1]
                try:
                    tf = int(pending[2])
                except ValueError as exc:
                    raise AuditError("posting COPY row has an invalid term frequency") from exc
                if term in observed:
                    raise AuditError("posting COPY contains a duplicate term for one document")
                if tf <= 0:
                    raise AuditError("posting COPY contains a non-positive term frequency")
                observed[term] = tf
                pending = next(posting_iter, None)
                if pending is not None:
                    try:
                        posting_doc_id = int(pending[0])
                    except (IndexError, ValueError) as exc:
                        raise AuditError("posting COPY row has an invalid document id") from exc
        check_document(expected, observed)
        expected_index += 1

    if pending is not None:
        raise AuditError("posting COPY contains trailing documents outside the content scope")
    if expected_index != len(documents):
        raise AuditError("content stream ended before all documents were checked")

    return {
        "documents": len(documents),
        **totals,
        "mismatch_doc_ids": mismatches,
        "mismatch_doc_ids_omitted": max(
            0, totals["posting_mismatch_docs"] - len(mismatches)
        ),
        "unverifiable_doc_ids": unverifiable,
        "unverifiable_doc_ids_omitted": max(
            0, totals["unverifiable_docs"] - len(unverifiable)
        ),
    }


def _content_query(warc_only: bool) -> str:
    join = " JOIN pgwarc_lance.warc_record w USING (doc_id)" if warc_only else ""
    return (
        "SELECT d.doc_id, d.content IS NOT NULL, COALESCE(d.content, ''), d.doc_len "
        f"FROM pgwarc_lance.bm25_doc d{join} ORDER BY d.doc_id"
    )


def _posting_query(warc_only: bool) -> str:
    join = " JOIN pgwarc_lance.warc_record w USING (doc_id)" if warc_only else ""
    return (
        "SELECT t.doc_id, t.term, t.tf "
        f"FROM pgwarc_lance.bm25_term t{join} ORDER BY t.doc_id"
    )


class PsqlCopySession:
    def __init__(self, psql_command: str, warc_only: bool):
        command = shlex.split(psql_command)
        if not command:
            raise AuditError("psql command is empty")
        command.extend(["-X", "-qAt", "-v", "ON_ERROR_STOP=1"])
        script = "\n".join(
            [
                "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;",
                f"COPY ({_content_query(warc_only)}) TO STDOUT WITH (FORMAT csv);",
                f"SELECT '{CONTENT_MARKER}';",
                f"COPY ({_posting_query(warc_only)}) TO STDOUT WITH (FORMAT csv);",
                f"SELECT '{POSTING_MARKER}';",
                "COMMIT;",
                "",
            ]
        )
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.finished = False
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(script.encode("utf-8"))
            self.process.stdin.close()
        except OSError as exc:
            self.close()
            raise AuditError("could not send read-only audit queries to psql") from exc
        assert self.process.stdout is not None
        self.text_stream = io.TextIOWrapper(
            self.process.stdout, encoding="utf-8", newline=""
        )
        self.reader = csv.reader(self.text_stream)

    def rows_until(self, marker: str, width: int) -> Iterator[list[str]]:
        for row in self.reader:
            if row == [marker]:
                return
            if len(row) == 1 and row[0].startswith("COPY "):
                continue
            if len(row) != width:
                raise AuditError("psql returned an unexpected COPY row shape")
            yield row
        raise AuditError("psql ended before completing a COPY section")

    def finish(self) -> None:
        if self.finished:
            return
        # Consume the quiet psql session to EOF so COMMIT completes and stderr
        # cannot fill a pipe while the parent waits for process exit.
        for row in self.reader:
            if row and not (len(row) == 1 and row[0].startswith("COPY ")):
                raise AuditError("psql returned unexpected output after the posting stream")
        self.text_stream.close()
        assert self.process.stderr is not None
        stderr = self.process.stderr.read()
        status = self.process.wait()
        self.finished = True
        if status:
            raise AuditError(
                f"psql audit failed with status {status}; private stderr bytes={len(stderr)}"
            )

    def close(self) -> None:
        if self.finished:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        self.finished = True


def audit_database(
    psql_command: str,
    *,
    warc_only: bool = False,
    max_mismatch_docs: int = 100,
) -> dict[str, object]:
    csv.field_size_limit(2_147_483_647)
    session = PsqlCopySession(psql_command, warc_only)
    try:
        documents = expected_documents(session.rows_until(CONTENT_MARKER, 4))
        comparison = compare_postings(
            documents,
            session.rows_until(POSTING_MARKER, 3),
            max_mismatch_docs,
        )
        session.finish()
    finally:
        session.close()

    status = "consistent"
    if comparison["posting_mismatch_docs"]:
        status = "mismatch"
    elif comparison["unverifiable_docs"]:
        status = "unverifiable"
    return {
        "schema_version": 1,
        "status": status,
        "scope": "warc_backed_documents" if warc_only else "all_documents",
        "snapshot": "repeatable_read_read_only",
        "tokenizer": "tools.warc_importer.tokenize_bm25",
        "term_storage": "tools.warc_importer.bm25_storage_term",
        **comparison,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stream a read-only PostgreSQL snapshot and compare BM25 postings "
            "with tokens recomputed from bm25_doc.content."
        )
    )
    parser.add_argument(
        "--psql",
        default=os.environ.get("PGWARC_PSQL", DEFAULT_PSQL),
        help="psql command plus connection arguments (default: PGWARC_PSQL or local dev DB)",
    )
    parser.add_argument(
        "--warc-only",
        action="store_true",
        help="audit only documents with a pgwarc_lance.warc_record row",
    )
    parser.add_argument(
        "--max-mismatch-docs",
        type=int,
        default=100,
        help="maximum document IDs included per report list (default: 100)",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="private path for the machine-readable report (contains doc IDs)",
    )
    args = parser.parse_args(argv)
    if args.max_mismatch_docs <= 0:
        parser.error("--max-mismatch-docs must be positive")

    try:
        report = audit_database(
            args.psql,
            warc_only=args.warc_only,
            max_mismatch_docs=args.max_mismatch_docs,
        )
    except (AuditError, OSError, csv.Error) as exc:
        print(f"BM25 index audit failed: {exc}", file=sys.stderr)
        return 1

    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output_json:
        try:
            args.output_json.parent.mkdir(
                parents=True, exist_ok=True, mode=0o700
            )
            descriptor = os.open(
                args.output_json,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(encoded)
        except OSError as exc:
            print(
                f"BM25 audit report could not be written ({type(exc).__name__})",
                file=sys.stderr,
            )
            return 1
        print(
            "BM25 audit report saved; "
            f"status={report['status']} documents={report['documents']} "
            f"posting_mismatch_docs={report['posting_mismatch_docs']} "
            f"unverifiable_docs={report['unverifiable_docs']}"
        )
    else:
        print(encoded, end="")
    return 0 if report["status"] == "consistent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
