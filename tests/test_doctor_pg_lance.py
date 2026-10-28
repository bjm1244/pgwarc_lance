from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import doctor_pg_lance


class DoctorPgLanceTests(unittest.TestCase):
    def test_reports_duplicates_missing_orphans_stale_label_and_pending_outbox(self):
        pg = {
            "row_count": 4,
            "rows": [
                {"doc_id": 1, "has_bm25": True, "has_warc": True, "text_len": 5,
                 "payload_digest": "sha1:a", "bm25_content_md5": "content-a"},
                {"doc_id": 2, "has_bm25": True, "has_warc": True, "text_len": 0,
                 "payload_digest": "sha1:b", "bm25_content_md5": "content-b"},
                {"doc_id": 3, "has_bm25": False, "has_warc": True, "text_len": 8,
                 "payload_digest": "sha1:c", "bm25_content_md5": None},
                {"doc_id": 4, "has_bm25": True, "has_warc": False, "text_len": None,
                 "payload_digest": None, "bm25_content_md5": "content-d"},
            ],
            "unfinished_jobs": [{"job_id": 9, "state": "pending", "ids": [99]}],
        }
        lance = {
            "dataset_count": 4,
            "rows": [
                {"doc_id": 1, "has_warc": True, "label_matches_metadata": True},
                {"doc_id": 2, "has_warc": True, "label_matches_metadata": False},
                {"doc_id": 5, "has_warc": False, "label_matches_metadata": None},
                {"doc_id": 5, "has_warc": False, "label_matches_metadata": None},
            ],
        }

        report = doctor_pg_lance.reconcile(pg, lance, 10)

        self.assertEqual(report["status"], "findings")
        self.assertTrue(report["read_only"])
        self.assertFalse(report["reconciliation_complete"])
        self.assertEqual(report["findings"], {
            "duplicate_postgres_ids": 0,
            "duplicate_lance_ids": 1,
            "missing_vector_candidates": 1,
            "orphan_lance_ids": 1,
            "stale_label_ids": 1,
            "warc_without_bm25": 1,
            "unfinished_outbox_jobs": 1,
            "unfinished_outbox_document_ids": 1,
        })
        self.assertFalse(report["revision_check"]["lance_revision_persisted"])
        self.assertEqual(report["sample_doc_ids"]["duplicate_lance"], [5])

    def test_does_not_claim_a_partial_inventory_is_clean(self):
        pg = {"row_count": 3, "rows": [], "unfinished_jobs": []}
        lance = {"dataset_count": 4, "rows": []}

        report = doctor_pg_lance.reconcile(pg, lance, 2)

        self.assertEqual(report["status"], "incomplete")
        self.assertFalse(report["scan_complete"])
        self.assertIsNone(report["findings"])

    @patch("doctor_pg_lance.inventory", return_value=(
        {"row_count": 0, "rows": [], "unfinished_jobs": []},
        {"dataset_count": 0, "rows": []},
    ))
    def test_exit_code_marks_unresolved_scope_even_without_observed_mismatch(self, _inventory):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = doctor_pg_lance.main(["--uri", "/tmp/disposable.lance", "--max-rows", "10"])

        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 4)
        self.assertEqual(report["status"], "no_observed_mismatch")
        self.assertFalse(report["reconciliation_complete"])

    @patch("doctor_pg_lance.subprocess.run")
    def test_inventory_passes_uri_over_stdin_and_uses_read_only_transaction(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='pg\t{"row_count":0,"rows":[]}\n'
                   'lance\t{"dataset_count":0,"rows":[]}\n'
                   'outbox\t{"job_count":0,"rows":[]}\n', stderr="",
        )

        pg, lance = doctor_pg_lance.inventory("/private/path/test.lance", ["psql"], 10, 5)

        args, kwargs = run.call_args
        self.assertNotIn("/private/path/test.lance", args[0])
        self.assertIn("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY", kwargs["input"])
        self.assertIn("ROLLBACK", kwargs["input"])
        self.assertIn("decode('L3ByaXZhdGUvcGF0aC90ZXN0LmxhbmNl', 'base64')", kwargs["input"])
        self.assertEqual(kwargs["input"].count("lance_scan("), 1)
        self.assertIn("MATERIALIZED", kwargs["input"])
        self.assertEqual(pg["unfinished_jobs"]["rows"], [])
        self.assertEqual(lance["rows"], [])


if __name__ == "__main__":
    unittest.main()
