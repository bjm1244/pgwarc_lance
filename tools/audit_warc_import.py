#!/usr/bin/env python3
"""Create a private, checksum-bound accounting manifest for WARC input.

This read-only audit uses the same WARC parser, extractors and BM25 tokenizer
as ``warc_importer.py``. It does not generate SQL or write to PostgreSQL/Lance.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import warc_importer as wi


SCHEMA_VERSION = 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_revision() -> str | None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    return result.stdout.strip() or None


def failure_reason(error: Exception) -> str:
    """Map an error to a stable cause without retaining record text or IDs."""
    message = str(error).lower()
    if "unsupported mime type" in message:
        return "unsupported_mime_type"
    if "requires --pdftotext" in message:
        return "pdf_extractor_not_configured"
    if "requires --extract-docx" in message:
        return "docx_extractor_not_configured"
    if "unknown encoding" in message or "unknown codec" in message:
        return "unknown_charset"
    if "charset" in message or "decode" in message or isinstance(error, UnicodeError):
        return "text_decode_error"
    if "content-language exceeds" in message:
        return "content_language_size_limit"
    if "content-language has more than" in message:
        return "content_language_count_limit"
    if "content-language contains an invalid" in message:
        return "invalid_content_language"
    if "chunk" in message:
        return "invalid_http_chunking"
    if "gzip" in message:
        return "invalid_http_gzip"
    if "pdf" in message:
        return "pdf_extraction_error"
    if "docx" in message:
        return "docx_extraction_error"
    if "exceed" in message or "max_record_bytes" in message:
        return "record_size_limit"
    if isinstance(error, LookupError):
        return "unknown_codec"
    if isinstance(error, OSError):
        return "record_io_error"
    return "record_conversion_error"


def record_format(record: wi.WarcRecord, record_type: str) -> str:
    """Return a bounded format label without retaining untrusted MIME values."""
    if record_type not in {"response", "resource"}:
        return "non_document_warc"
    try:
        status_line, headers, body = wi.parse_http_headers(record.payload)
    except Exception:
        return "unknown"
    content_type = headers.get("content-type") or record.headers.get("content-type")
    mime = wi.media_type(content_type)
    payload = body if status_line else record.payload
    if mime == "application/pdf" or b"%PDF-" in payload[:1024]:
        return "pdf"
    if mime == wi.DOCX_MIME:
        return "docx"
    if content_type and "html" in content_type.lower():
        return "html"
    if not mime or mime.startswith("text/") or mime in wi.TEXT_APPLICATION_MIME_TYPES or mime.endswith(("+xml", "+json")):
        return "text"
    return "unsupported"


def scan_warcs(
    paths: list[Path],
    *,
    max_record_bytes: int = wi.DEFAULT_MAX_RECORD_BYTES,
    min_text_chars: int = 1,
    charset_policy: wi.CharsetPolicy | None = None,
    pdf_extractor: wi.PdfTextExtractor | None = None,
    docx_extractor: wi.DocxTextExtractor | None = None,
    retention: wi.ExtractionRetention | None = None,
    import_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Account for every parsed raw record and fingerprint accepted documents."""
    if max_record_bytes <= 0:
        raise ValueError("max_record_bytes must be positive")
    if min_text_chars < 0:
        raise ValueError("min_text_chars must be non-negative")
    if not paths:
        raise ValueError("at least one WARC path is required")

    inputs: list[dict[str, Any]] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"input must be a regular non-symlink file: {path}")
        inputs.append(
            {
                "path": str(path.resolve()),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    raw_records = 0
    record_types: collections.Counter[str] = collections.Counter()
    outcomes: collections.Counter[str] = collections.Counter()
    format_outcomes: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    skipped: collections.Counter[str] = collections.Counter()
    failed: collections.Counter[str] = collections.Counter()
    metadata_only: collections.Counter[str] = collections.Counter()
    input_errors: collections.Counter[str] = collections.Counter()
    seen_doc_ids: set[int] = set()
    long_term_values: dict[str, str] = {}
    document_rows: list[tuple[int, str, int, int, str]] = []
    posting_rows: list[tuple[int, int, int, str]] = []
    total_postings = 0
    total_tf = 0
    oversized_ascii_term_records = 0
    oversized_ascii_terms = 0
    max_oversized_ascii_term_bytes = 0
    complete_to_eof = True

    active_retention = retention or wi.ExtractionRetention(mode="fail")
    for path in paths:
        try:
            for raw in wi.iter_warc_records(path, max_record_bytes=max_record_bytes):
                raw_records += 1
                record_type = raw.headers.get("warc-type", "").strip().lower()
                record_types[record_type or "unspecified"] += 1
                format_name = record_format(raw, record_type)
                if record_type not in {"response", "resource"}:
                    outcomes["policy_skipped"] += 1
                    skipped["non_document_warc_type"] += 1
                    format_outcomes[format_name]["policy_skipped"] += 1
                    continue

                previous_metadata_counts = dict(active_retention.metadata_only)
                try:
                    record = wi.import_record_from_warc(
                        raw,
                        max_record_bytes,
                        charset_policy,
                        pdf_extractor,
                        docx_extractor,
                        active_retention,
                    )
                    if record is None:
                        outcomes["policy_skipped"] += 1
                        skipped["empty_extracted_text"] += 1
                        format_outcomes[format_name]["policy_skipped"] += 1
                        continue
                    if (
                        record.extraction_status != "metadata_only"
                        and len(record.text) < min_text_chars
                    ):
                        outcomes["policy_skipped"] += 1
                        skipped["below_min_text_chars"] += 1
                        format_outcomes[format_name]["policy_skipped"] += 1
                        continue
                    record_id = raw.headers.get("warc-record-id")
                    if charset_policy is not None and record_id in charset_policy.record_overrides:
                        charset_policy.record_used[record_id] = (
                            charset_policy.record_used.get(record_id, 0) + 1
                        )
                    if record.doc_id in seen_doc_ids:
                        outcomes["failed"] += 1
                        failed["duplicate_doc_id"] += 1
                        format_outcomes[format_name]["failed"] += 1
                        continue

                    metadata_reason: str | None = None
                    if record.extraction_status == "metadata_only":
                        new_kinds = [
                            kind
                            for kind, count in active_retention.metadata_only.items()
                            if count > previous_metadata_counts.get(kind, 0)
                        ]
                        metadata_reason = (
                            new_kinds[0] if len(new_kinds) == 1 else "extractor_failure"
                        )
                        frequencies: dict[str, int] = {}
                    else:
                        oversized_count, longest_term_bytes = wi.validate_bm25_terms(record)
                        oversized_ascii_term_records += int(oversized_count > 0)
                        oversized_ascii_terms += oversized_count
                        max_oversized_ascii_term_bytes = max(
                            max_oversized_ascii_term_bytes, longest_term_bytes
                        )
                        frequencies = wi.bm25_term_frequencies(record.text)

                    content = record.text.encode("utf-8")
                    content_hash = hashlib.sha256(content).hexdigest()

                    posting_digest = hashlib.sha256()
                    document_postings = 0
                    document_tf = 0
                    for term, frequency in sorted(frequencies.items()):
                        key, original = wi.bm25_storage_term(term)
                        if original is not None:
                            existing = long_term_values.get(key)
                            if existing is not None and existing != original:
                                raise ValueError("long-term SHA-256 key collision")
                            long_term_values[key] = original
                        posting_digest.update(
                            f"{key}\t{frequency}\n".encode("utf-8")
                        )
                        document_postings += 1
                        document_tf += frequency
                    seen_doc_ids.add(record.doc_id)
                    document_rows.append(
                        (
                            record.doc_id,
                            record.extraction_status,
                            len(record.text),
                            len(content),
                            content_hash,
                        )
                    )
                    if metadata_reason is not None:
                        outcomes["metadata_only"] += 1
                        metadata_only[metadata_reason] += 1
                        format_outcomes[format_name]["metadata_only"] += 1
                    else:
                        outcomes["imported"] += 1
                        format_outcomes[format_name]["imported"] += 1
                    posting_rows.append(
                        (
                            record.doc_id,
                            document_postings,
                            document_tf,
                            posting_digest.hexdigest(),
                        )
                    )
                    total_postings += document_postings
                    total_tf += document_tf
                except Exception as error:
                    outcomes["failed"] += 1
                    failed[failure_reason(error)] += 1
                    format_outcomes[format_name]["failed"] += 1
        except Exception as error:
            complete_to_eof = False
            input_errors[failure_reason(error)] += 1
            break

    category_total = sum(outcomes.values())
    accounting_balanced = raw_records == category_total
    record_type_total = sum(record_types.values())
    format_outcome_total = sum(sum(counts.values()) for counts in format_outcomes.values())
    format_outcomes_balanced = format_outcome_total == raw_records
    skipped_total = sum(skipped.values())
    failed_total = sum(failed.values())
    metadata_only_total = sum(metadata_only.values())
    reasons_balanced = (
        skipped_total == outcomes["policy_skipped"]
        and failed_total == outcomes["failed"]
        and metadata_only_total == outcomes["metadata_only"]
    )

    document_digest = hashlib.sha256()
    content_digest = hashlib.sha256()
    for doc_id, status, char_count, byte_count, content_hash in sorted(document_rows):
        row = f"{doc_id}\t{status}\t{char_count}\t{byte_count}\t{content_hash}\n"
        document_digest.update(row.encode("utf-8"))
        content_digest.update(
            f"{doc_id}\t{char_count}\t{byte_count}\t{content_hash}\n".encode("utf-8")
        )

    posting_digest = hashlib.sha256()
    tf_digest = hashlib.sha256()
    for doc_id, posting_count, document_tf, digest in sorted(posting_rows):
        posting_digest.update(
            f"{doc_id}\t{posting_count}\t{document_tf}\t{digest}\n".encode("ascii")
        )
        tf_digest.update(f"{doc_id}\t{document_tf}\n".encode("ascii"))

    actual_settings = {
        "max_record_bytes": max_record_bytes,
        "min_text_chars": min_text_chars,
        "document_error_policy": active_retention.mode,
        "charset_overrides": dict(sorted((charset_policy.overrides if charset_policy else {}).items())),
        "charset_record_overrides": dict(sorted((charset_policy.record_overrides if charset_policy else {}).items())),
        "pdf_extractor": pdf_extractor.summary() if pdf_extractor else None,
        "docx_extractor_enabled": docx_extractor is not None,
        "docx_extractor": docx_extractor.summary() if docx_extractor else None,
        "import": import_settings or {"mode": "read_only_audit"},
    }
    charset_observations = None
    if charset_policy is not None:
        charset_observations = {
            "label_overrides_errors": "strict",
            "label_override_records": dict(sorted(charset_policy.used.items())),
            "record_override_errors": "replace",
            "record_override_records": sum(charset_policy.record_used.values()),
            "record_override_u_fffd_codepoints_observed": sum(
                charset_policy.record_replacement_characters.values()
            ),
            "record_override_observations_by_id_sha256": {
                hashlib.sha256(record_id.encode("utf-8")).hexdigest(): count
                for record_id, count in sorted(charset_policy.record_replacement_characters.items())
            },
        }
    source_files = {
        "audit_tool_sha256": sha256_file(Path(__file__).resolve()),
        "importer_sha256": sha256_file(Path(wi.__file__).resolve()),
        "rust_tokenizer_sha256": sha256_file(ROOT / "src" / "tokenizer.rs"),
        "rust_bm25_sha256": sha256_file(ROOT / "src" / "bm25.rs"),
    }
    config_checksum = sha256_json(
        {"settings": actual_settings, "source_files": source_files}
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": (
            "accounted"
            if complete_to_eof and accounting_balanced and reasons_balanced and format_outcomes_balanced
            else "incomplete"
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_revision": source_revision(),
        "scope": "read-only WARC parsing, extraction and BM25 fingerprint; no SQL or Lance writes",
        "inputs": inputs,
        "settings": actual_settings,
        "charset_observations": charset_observations,
        "source_files": source_files,
        "checksums": {
            "configuration_sha256": config_checksum,
            "document_fingerprint_sha256": document_digest.hexdigest(),
            "content_fingerprint_sha256": content_digest.hexdigest(),
            "posting_fingerprint_sha256": posting_digest.hexdigest(),
            "term_frequency_fingerprint_sha256": tf_digest.hexdigest(),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "record_types": dict(sorted(record_types.items())),
        "format_outcomes": {
            name: dict(sorted(counts.items()))
            for name, counts in sorted(format_outcomes.items())
        },
        "outcomes": {
            "raw": raw_records,
            "imported": outcomes["imported"],
            "metadata_only": outcomes["metadata_only"],
            "failed": outcomes["failed"],
            "policy_skipped": outcomes["policy_skipped"],
        },
        "reasons": {
            "metadata_only": dict(sorted(metadata_only.items())),
            "metadata_only_extraction_failures": dict(sorted(active_retention.failure_categories.items())),
            "failed": dict(sorted(failed.items())),
            "policy_skipped": dict(sorted(skipped.items())),
            "input_scan_errors": dict(sorted(input_errors.items())),
        },
        "index_summary": {
            "fingerprinted_documents": len(document_rows),
            "posting_count": total_postings,
            "total_tf": total_tf,
        },
        "oversized_ascii_terms": {
            "records": oversized_ascii_term_records,
            "terms": oversized_ascii_terms,
            "max_term_bytes": max_oversized_ascii_term_bytes,
            "storage_key_policy_threshold_bytes": wi.MAX_BM25_TERM_BYTES,
            "maximum_supported_search_term_bytes": wi.MAX_SEARCHABLE_TERM_BYTES,
        },
        "accounting": {
            "complete_to_eof": complete_to_eof,
            "record_type_count_matches_raw": record_type_total == raw_records,
            "outcomes_sum_to_raw": accounting_balanced,
            "format_outcomes_sum_to_raw": format_outcomes_balanced,
            "reason_counts_match_outcomes": reasons_balanced,
            "import_acceptable": (
                complete_to_eof
                and accounting_balanced
                and reasons_balanced
                and outcomes["failed"] == 0
            ),
        },
    }
    return manifest


def write_private_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path = path.expanduser().absolute()
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = stat.S_IMODE(parent.stat().st_mode)
    if mode & 0o077:
        raise ValueError("manifest directory must not grant group/other access")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=True, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("warc", nargs="+", type=Path, help="WARC or WARC.GZ input")
    parser.add_argument("--output-json", required=True, type=Path, help="new private manifest path")
    parser.add_argument("--max-record-bytes", type=int, default=wi.DEFAULT_MAX_RECORD_BYTES)
    parser.add_argument("--min-text-chars", type=int, default=1)
    parser.add_argument("--charset-override", action="append", default=[], metavar="LABEL=CODEC")
    parser.add_argument("--charset-record-override", action="append", default=[], metavar="WARC-RECORD-ID=CODEC")
    parser.add_argument("--pdftotext", help="Poppler pdftotext executable")
    parser.add_argument("--pdf-timeout-seconds", type=float, default=30)
    parser.add_argument("--extract-docx", action="store_true")
    parser.add_argument(
        "--document-error-policy",
        choices=["fail", "metadata-only"],
        default="fail",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        charset_policy = wi.CharsetPolicy.from_options(
            args.charset_override, args.charset_record_override
        )
        pdf_extractor = (
            wi.PdfTextExtractor(args.pdftotext, args.pdf_timeout_seconds)
            if args.pdftotext
            else None
        )
        docx_extractor = wi.DocxTextExtractor() if args.extract_docx else None
        manifest = scan_warcs(
            args.warc,
            max_record_bytes=args.max_record_bytes,
            min_text_chars=args.min_text_chars,
            charset_policy=charset_policy,
            pdf_extractor=pdf_extractor,
            docx_extractor=docx_extractor,
            retention=wi.ExtractionRetention(mode=args.document_error_policy),
        )
        output_path = args.output_json.expanduser().absolute()
        write_private_manifest(output_path, manifest)
    except (OSError, ValueError) as error:
        print(f"audit_warc_import: {failure_reason(error)}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "status": manifest["status"],
                "outcomes": manifest["outcomes"],
                "accounting": manifest["accounting"],
                "checksums": manifest["checksums"],
                "manifest_mode": oct(stat.S_IMODE(output_path.stat().st_mode)),
            },
        )
    )
    return 0 if manifest["status"] == "accounted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
