import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.audit_bm25_index import (
    AuditError,
    compare_postings,
    expected_documents,
    main,
    posting_fingerprint,
)
from tools.warc_importer import bm25_storage_term


class AuditBm25IndexTests(unittest.TestCase):
    def test_matching_postings_and_document_length_are_consistent(self):
        documents = expected_documents([["11", "t", "alpha alpha beta", "3"]])
        result = compare_postings(
            documents,
            [["11", "alpha", "2"], ["11", "beta", "1"]],
        )

        self.assertEqual(result["documents"], 1)
        self.assertEqual(result["source_postings"], 2)
        self.assertEqual(result["expected_postings"], 2)
        self.assertEqual(result["source_tf"], 3)
        self.assertEqual(result["expected_tf"], 3)
        self.assertEqual(result["posting_mismatch_docs"], 0)
        self.assertEqual(result["doc_len_vs_source_tf_mismatches"], 0)
        self.assertEqual(result["doc_len_vs_content_tf_mismatches"], 0)

    def test_same_counts_with_different_term_is_reported(self):
        documents = expected_documents([["12", "t", "alpha beta", "2"]])
        result = compare_postings(
            documents,
            [["12", "alpha", "1"], ["12", "gamma", "1"]],
        )

        self.assertEqual(result["source_postings"], result["expected_postings"])
        self.assertEqual(result["source_tf"], result["expected_tf"])
        self.assertEqual(result["posting_mismatch_docs"], 1)
        self.assertEqual(result["mismatch_doc_ids"][0]["doc_id"], 12)

    def test_missing_and_extra_postings_are_reported(self):
        documents = expected_documents(
            [["13", "t", "alpha beta", "2"], ["14", "t", "gamma", "1"]]
        )
        result = compare_postings(documents, [["13", "alpha", "1"]])

        self.assertEqual(result["posting_mismatch_docs"], 2)
        self.assertEqual(result["source_postings"], 1)
        self.assertEqual(result["expected_postings"], 3)
        self.assertEqual(
            [item["doc_id"] for item in result["mismatch_doc_ids"]], [13, 14]
        )

    def test_document_length_disagreement_is_reported(self):
        documents = expected_documents([["15", "t", "alpha beta", "9"]])
        result = compare_postings(
            documents,
            [["15", "alpha", "1"], ["15", "beta", "1"]],
        )

        self.assertEqual(result["doc_len_vs_source_tf_mismatches"], 1)
        self.assertEqual(result["doc_len_vs_content_tf_mismatches"], 1)
        self.assertEqual(result["posting_mismatch_docs"], 1)

    def test_null_content_is_unverifiable(self):
        documents = expected_documents([["16", "f", "", "1"]])
        result = compare_postings(documents, [["16", "alpha", "1"]])

        self.assertEqual(result["unverifiable_docs"], 1)
        self.assertEqual(result["unverifiable_doc_ids"], [16])
        self.assertEqual(result["posting_mismatch_docs"], 0)

    def test_documents_and_postings_must_be_in_scope_and_ordered(self):
        documents = expected_documents([["18", "t", "alpha", "1"]])
        with self.assertRaisesRegex(AuditError, "absent from the content scope"):
            compare_postings(documents, [["17", "alpha", "1"]])
        with self.assertRaisesRegex(AuditError, "uniquely ordered"):
            expected_documents(
                [["19", "t", "alpha", "1"], ["19", "t", "beta", "1"]]
            )

    def test_long_tokens_use_the_importer_storage_key(self):
        term = "x" * 2001
        documents = expected_documents([["20", "t", term, "1"]])
        key, original = bm25_storage_term(term)
        self.assertIsNotNone(original)
        self.assertEqual(documents[0].expected_postings, 1)
        self.assertEqual(
            documents[0].expected_sha256,
            posting_fingerprint({key: 1})[2],
        )
        result = compare_postings(documents, [["20", key, "1"]])
        self.assertEqual(result["posting_mismatch_docs"], 0)

    def test_content_matching_copy_null_marker_is_still_content(self):
        documents = expected_documents([["21", "t", r"\N", "0"]])

        self.assertIsNotNone(documents[0].expected_tf)
        self.assertEqual(documents[0].expected_tf, 0)
        result = compare_postings(documents, [])
        self.assertEqual(result["unverifiable_docs"], 0)
        self.assertEqual(result["posting_mismatch_docs"], 0)

    def test_report_write_failure_returns_operational_error(self):
        report = {"schema_version": 1, "status": "consistent"}
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "report-directory"
            output_path.mkdir()
            error = io.StringIO()
            with patch("tools.audit_bm25_index.audit_database", return_value=report):
                with redirect_stderr(error):
                    status = main(["--output-json", str(output_path)])

        self.assertEqual(status, 1)
        self.assertIn("report could not be written", error.getvalue())
        self.assertIn("IsADirectoryError", error.getvalue())

    def test_private_report_file_and_stdout_redact_document_ids(self):
        report = {
            "schema_version": 1,
            "status": "mismatch",
            "documents": 1,
            "posting_mismatch_docs": 1,
            "unverifiable_docs": 0,
            "mismatch_doc_ids": [{"doc_id": 9876}],
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "nested" / "report.json"
            stdout = io.StringIO()
            with patch("tools.audit_bm25_index.audit_database", return_value=report):
                with redirect_stdout(stdout):
                    status = main(["--output-json", str(output_path)])

            written_mode = output_path.stat().st_mode & 0o777
            directory_mode = output_path.parent.stat().st_mode & 0o777
            saved_report = output_path.read_text(encoding="utf-8")

        self.assertEqual(status, 2)
        self.assertEqual(written_mode, 0o600)
        self.assertEqual(directory_mode, 0o700)
        self.assertIn("9876", saved_report)
        self.assertIn("posting_mismatch_docs=1", stdout.getvalue())
        self.assertNotIn("9876", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
