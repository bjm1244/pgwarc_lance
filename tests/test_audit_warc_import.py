import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import audit_warc_import as audit
from tools import warc_importer as wi


def make_warc_record(
    record_type: str,
    payload: bytes,
    *,
    target: str = "https://example.test/document",
    date: str = "2026-09-04T13:16:00Z",
    record_id: str = "<urn:uuid:record-1>",
    content_type: str = "text/plain; charset=utf-8",
) -> bytes:
    header = (
        "WARC/1.0\r\n"
        f"WARC-Type: {record_type}\r\n"
        f"WARC-Record-ID: {record_id}\r\n"
        f"WARC-Target-URI: {target}\r\n"
        f"WARC-Date: {date}\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "\r\n"
    ).encode("ascii")
    return header + payload + b"\r\n\r\n"


def response(body: bytes, content_type: str = "text/plain; charset=utf-8") -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\nContent-Type: "
        + content_type.encode("ascii")
        + b"\r\n\r\n"
        + body
    )


class FailingPdfExtractor:
    def extract(self, body, max_output_bytes, record_key=None):
        raise ValueError("private record-specific PDF failure")

    def summary(self):
        return {"enabled": True, "version": "fixture-pdftotext"}


class AuditWarcImportTests(unittest.TestCase):
    def write_fixture(self, directory: Path) -> Path:
        records = [
            make_warc_record("warcinfo", b"metadata", record_id="<urn:uuid:warcinfo>"),
            make_warc_record(
                "response",
                response(b"searchable words"),
                target="https://example.test/imported",
                record_id="<urn:uuid:imported>",
            ),
            make_warc_record(
                "response",
                response(b"%PDF-fixture", "application/pdf"),
                target="https://example.test/metadata-only",
                record_id="<urn:uuid:metadata-only>",
                content_type="application/pdf",
            ),
            make_warc_record(
                "response",
                response(b"binary", "application/zip"),
                target="https://example.test/failed",
                record_id="<urn:uuid:failed>",
                content_type="application/zip",
            ),
            make_warc_record(
                "response",
                response(b""),
                target="https://example.test/empty",
                record_id="<urn:uuid:empty>",
            ),
            make_warc_record(
                "response",
                response(b"x"),
                target="https://example.test/short",
                record_id="<urn:uuid:short>",
            ),
        ]
        path = directory / "fixture.warc"
        path.write_bytes(b"".join(records))
        return path

    def test_accounting_is_balanced_and_reason_counts_are_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self.write_fixture(Path(temporary))
            manifest = audit.scan_warcs(
                [source],
                min_text_chars=2,
                pdf_extractor=FailingPdfExtractor(),
                retention=wi.ExtractionRetention(mode="metadata-only"),
            )

        self.assertEqual(
            manifest["outcomes"],
            {"raw": 6, "imported": 1, "metadata_only": 1, "failed": 1, "policy_skipped": 3},
        )
        self.assertEqual(manifest["reasons"]["failed"], {"unsupported_mime_type": 1})
        self.assertEqual(manifest["reasons"]["metadata_only"], {"pdf": 1})
        self.assertEqual(
            manifest["reasons"]["policy_skipped"],
            {"below_min_text_chars": 1, "empty_extracted_text": 1, "non_document_warc_type": 1},
        )
        self.assertTrue(manifest["accounting"]["outcomes_sum_to_raw"])
        self.assertTrue(manifest["accounting"]["reason_counts_match_outcomes"])
        self.assertTrue(manifest["accounting"]["record_type_count_matches_raw"])

    def test_same_actual_input_has_repeatable_document_posting_and_tf_fingerprints(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self.write_fixture(Path(temporary))
            first = audit.scan_warcs([source])
            second = audit.scan_warcs([source])

        self.assertEqual(first["inputs"], second["inputs"])
        self.assertEqual(first["checksums"], second["checksums"])
        self.assertEqual(first["outcomes"], second["outcomes"])
        self.assertEqual(first["index_summary"], second["index_summary"])
        self.assertEqual(first["index_summary"]["posting_count"], 2)
        self.assertEqual(first["index_summary"]["total_tf"], 2)

    def test_historical_1000_byte_cutoff_is_a_reproducible_fixture(self):
        text = "z" * 1001
        frequencies = wi.bm25_term_frequencies(text)
        legacy_frequencies = {
            term: count
            for term, count in frequencies.items()
            if len(term.encode("utf-8")) <= 1000
        }

        self.assertEqual(len(frequencies), 1)
        self.assertEqual(sum(frequencies.values()), 1)
        self.assertEqual(legacy_frequencies, {})
        self.assertEqual(wi.bm25_storage_term(text)[0], text)

    def test_private_manifest_is_new_and_mode_0600(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            source = self.write_fixture(directory)
            manifest = audit.scan_warcs([source])
            output = directory / "manifest.json"
            audit.write_private_manifest(output, manifest)

            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                audit.write_private_manifest(output, manifest)

    def test_importer_preflights_the_full_input_before_generating_sql(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "valid.warc"
            source.write_bytes(
                make_warc_record(
                    "response",
                    response(b"reproducible import"),
                    target="https://example.test/preflight",
                    record_id="<urn:uuid:preflight>",
                )
            )
            sql = directory / "import.sql"
            manifest_path = directory / "preflight.json"

            status = wi.main(
                [
                    str(source),
                    "--output",
                    str(sql),
                    "--audit-manifest-json",
                    str(manifest_path),
                    "--no-hash-vectors",
                ]
            )

            self.assertEqual(status, 0)
            self.assertTrue(sql.exists())
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["status"], "accounted")
            self.assertEqual(manifest["outcomes"]["imported"], 1)
            self.assertEqual(manifest["settings"]["import"]["bm25_mode"], "function")

    def test_direct_importer_cli_can_load_the_audit_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "valid.warc"
            source.write_bytes(
                make_warc_record(
                    "response",
                    response(b"direct importer invocation"),
                    target="https://example.test/direct-cli",
                    record_id="<urn:uuid:direct-cli>",
                )
            )
            sql = directory / "import.sql"
            manifest_path = directory / "preflight.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(wi.__file__).resolve()),
                    str(source),
                    "--output",
                    str(sql),
                    "--audit-manifest-json",
                    str(manifest_path),
                    "--no-hash-vectors",
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(sql.is_file())
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["status"], "accounted")
            self.assertEqual(manifest["outcomes"]["imported"], 1)
            self.assertEqual(manifest["outcomes"]["failed"], 0)

    def test_importer_writes_failed_accounting_and_stops_before_sql(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "unsupported.warc"
            source.write_bytes(
                make_warc_record(
                    "response",
                    response(b"binary", "application/zip"),
                    target="https://example.test/unsupported",
                    record_id="<urn:uuid:unsupported>",
                    content_type="application/zip",
                )
            )
            sql = directory / "must-not-exist.sql"
            manifest_path = directory / "failed-preflight.json"

            with self.assertRaises(SystemExit):
                wi.main(
                    [
                        str(source),
                        "--output",
                        str(sql),
                        "--audit-manifest-json",
                        str(manifest_path),
                        "--no-hash-vectors",
                    ]
                )

            self.assertFalse(sql.exists())
            manifest = json.loads(manifest_path.read_text())
            self.assertTrue(manifest["accounting"]["outcomes_sum_to_raw"])
            self.assertFalse(manifest["accounting"]["import_acceptable"])
            self.assertEqual(manifest["reasons"]["failed"], {"unsupported_mime_type": 1})

    def test_input_checksum_is_raw_file_checksum(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self.write_fixture(Path(temporary))
            manifest = audit.scan_warcs([source])
            expected = hashlib.sha256(source.read_bytes()).hexdigest()

        self.assertEqual(
            manifest["inputs"][0]["sha256"],
            expected,
        )


if __name__ == "__main__":
    unittest.main()
