from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
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
            "revision_count": 3,
            "revision_rows": [
                {"doc_id": 1, "expected_revision": doctor_pg_lance._revision_fingerprint({
                    "doc_id": 1, "has_warc": True, "text_len": 5, "payload_digest": "sha1:a", "bm25_content_md5": "content-a"
                })},
                {"doc_id": 2, "expected_revision": doctor_pg_lance._revision_fingerprint({
                    "doc_id": 2, "has_warc": True, "text_len": 0, "payload_digest": "sha1:b", "bm25_content_md5": "content-b"
                })},
                {"doc_id": 3, "expected_revision": doctor_pg_lance._revision_fingerprint({
                    "doc_id": 3, "has_warc": True, "text_len": 8, "payload_digest": "sha1:c", "bm25_content_md5": None
                })},
            ],
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
                {"doc_id": 1, "revision": doctor_pg_lance._revision_fingerprint({
                    "doc_id": 1, "has_warc": True, "text_len": 5, "payload_digest": "sha1:a", "bm25_content_md5": "content-a"
                }), "has_warc": True, "label_matches_metadata": True},
                {"doc_id": 2, "revision": "0" * 64, "has_warc": True, "label_matches_metadata": False},
                {"doc_id": 5, "revision": None, "has_warc": False, "label_matches_metadata": None},
                {"doc_id": 5, "revision": None, "has_warc": False, "label_matches_metadata": None},
            ],
        }

        report = doctor_pg_lance.reconcile(pg, lance, 10)

        self.assertEqual(report["status"], "findings")
        self.assertTrue(report["read_only"])
        self.assertFalse(report["reconciliation_complete"])
        self.assertEqual(report["findings"], {
            "row_count_mismatch": 0,
            "duplicate_postgres_ids": 0,
            "duplicate_lance_ids": 1,
            "missing_vector_candidates": 1,
            "orphan_lance_ids": 1,
            "orphan_revision_ids": 0,
            "deleted_vector_ids": 0,
            "deleted_metadata_ids": 0,
            "untracked_vector_ids": 0,
            "stale_vector_revisions": 1,
            "postgres_revision_mismatches": 0,
            "stale_label_ids": 1,
            "warc_without_bm25": 1,
            "unfinished_outbox_jobs": 1,
            "unfinished_outbox_document_ids": 1,
            "pending_outbox_jobs": 1,
            "ready_outbox_jobs": 1,
            "processing_outbox_jobs": 0,
            "failed_outbox_jobs": 0,
        })
        self.assertTrue(report["revision_check"]["lance_revision_persisted"])
        self.assertEqual(report["sample_doc_ids"]["duplicate_lance"], [5])
        self.assertEqual(report["metrics"]["outbox_queue_depth"], 1)
        self.assertEqual(report["metrics"]["pending_jobs"], 1)
        self.assertEqual(report["metrics"]["failed_jobs"], 0)
        self.assertEqual(report["counts"]["row_count_delta"], 0)
        self.assertIn("complete same corpus", report["counts"]["comparison_scope"])

    def test_tombstones_are_expected_absence_but_stale_vectors_are_reported(self):
        pg = {
            "row_count": 0,
            "revision_count": 1,
            "revision_rows": [
                {"doc_id": 8, "expected_revision": "f" * 64, "is_deleted": True}
            ],
            "rows": [],
            "unfinished_jobs": [],
        }
        absent = doctor_pg_lance.reconcile(pg, {"dataset_count": 0, "rows": []}, 10)
        self.assertEqual(absent["status"], "no_observed_mismatch")
        self.assertEqual(absent["counts"]["tombstone_documents"], 1)
        self.assertEqual(absent["findings"]["deleted_vector_ids"], 0)

        stale = doctor_pg_lance.reconcile(pg, {
            "dataset_count": 1,
            "rows": [{"doc_id": 8, "revision": "a" * 64,
                      "has_warc": False, "label_matches_metadata": None}],
        }, 10)
        self.assertEqual(stale["findings"]["deleted_vector_ids"], 1)
        self.assertEqual(stale["findings"]["orphan_lance_ids"], 0)
        self.assertEqual(stale["findings"]["row_count_mismatch"], 1)

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

    def test_reports_queue_metrics_versions_and_safe_recovery_guidance(self):
        pg = {
            "row_count": 2,
            "rows": [
                {"doc_id": 1, "has_bm25": True, "has_warc": False},
                {"doc_id": 2, "has_bm25": True, "has_warc": False},
            ],
            "revision_count": 0,
            "revision_rows": [],
            "queue_depth": 2,
            "unfinished_jobs": [
                {"job_id": 1, "state": "pending", "ids": [8]},
                {"job_id": 2, "state": "failed", "ids": [9]},
            ],
            "outbox_status": {
                "pending_jobs": 1,
                "ready_jobs": 1,
                "failed_jobs": 1,
                "oldest_unfinished_ms": 1200,
                "latest_applied_version": 6,
                "last_completed_at": "2026-11-01T00:00:00+00:00",
                "pending_delete_jobs": 0,
                "failed_delete_jobs": 0,
            },
        }
        lance = {"dataset_count": 0, "active_dataset_version": 7, "rows": []}
        collection = {
            "collection_id": "archive-main",
            "generation": 3,
            "active_version": "e5-v2",
            "expected_vectors_at_activation": 21,
        }

        report = doctor_pg_lance.reconcile(pg, lance, 10, collection)
        text = doctor_pg_lance.prometheus_text(report)

        self.assertEqual(report["status"], "findings")
        self.assertEqual(report["metrics"]["outbox_queue_depth"], 2)
        self.assertEqual(report["metrics"]["pending_jobs"], 1)
        self.assertEqual(report["metrics"]["failed_jobs"], 1)
        self.assertEqual(report["metrics"]["oldest_unfinished_ms"], 1200)
        self.assertEqual(report["metrics"]["active_dataset_version"], 7)
        self.assertEqual(report["metrics"]["latest_applied_version"], 6)
        self.assertEqual(report["collection"]["active_version"], "e5-v2")
        self.assertIn('pgwarc_lance_doctor_active_collection_version_info{collection="archive-main",version="e5-v2"} 1', text)
        self.assertIn("pgwarc_lance_doctor_outbox_failed_jobs 1", text)
        self.assertIn("pgwarc_lance_doctor_active_dataset_version 7", text)
        self.assertIn("# TYPE pgwarc_lance_doctor_outbox_failed_jobs gauge", text)
        self.assertEqual(text.count("# HELP pgwarc_lance_doctor_finding "), 1)
        self.assertEqual(text.count("# TYPE pgwarc_lance_doctor_finding gauge"), 1)
        self.assertIn("never mark it completed manually", " ".join(report["recovery_guidance"]))
        self.assertNotIn("/private/", text)
        self.assertNotIn("password=", text)

    def test_failed_only_queue_does_not_recommend_running_a_pending_job(self):
        pg = {
            "row_count": 0,
            "rows": [],
            "revision_count": 0,
            "revision_rows": [],
            "queue_depth": 1,
            "unfinished_jobs": [{"job_id": 1, "state": "failed", "ids": [5]}],
            "outbox_status": {
                "pending_jobs": 0,
                "ready_jobs": 0,
                "failed_jobs": 1,
                "oldest_unfinished_ms": 500,
            },
        }
        report = doctor_pg_lance.reconcile(pg, {"dataset_count": 0, "rows": []}, 10)
        guidance = " ".join(report["recovery_guidance"])
        self.assertIn("review the failure detail privately", guidance.lower())
        self.assertNotIn("lance_outbox_worker.py --once", guidance)

    def test_inventory_includes_aggregate_queue_and_active_version_queries(self):
        with patch("doctor_pg_lance.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=(
                    'pg\t{"row_count":0,"rows":[],"queue_depth":0,"outbox_status":{"pending_jobs":0,"ready_jobs":0,"failed_jobs":0}}\n'
                    'lance\t{"dataset_count":0,"active_dataset_version":4,"rows":[]}\n'
                    'outbox\t{"job_count":0,"rows":[]}\n'
                ), stderr="",
            )
            pg, lance = doctor_pg_lance.inventory("/private/archive.lance", ["psql"], 10, 5)

        sql = run.call_args.kwargs["input"]
        self.assertIn("pgwarc_lance.lance_outbox_status", sql)
        self.assertIn("state <> 'completed'", sql)
        self.assertIn("lance_dataset_versions", sql)
        self.assertEqual(pg["outbox_status"]["pending_jobs"], 0)
        self.assertEqual(lance["active_dataset_version"], 4)

    @patch("doctor_pg_lance.subprocess.run")
    def test_inventory_error_does_not_echo_database_diagnostics(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="/private/archive.lance password=redaction-test-only",
        )
        with self.assertRaises(doctor_pg_lance.DoctorError) as raised:
            doctor_pg_lance.inventory("/private/archive.lance", ["psql"], 10, 5)
        self.assertNotIn("/private/archive.lance", str(raised.exception))
        self.assertNotIn("redaction-test-only", str(raised.exception))

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
        self.assertEqual(kwargs["input"].count("lance_scan_document_state("), 1)
        self.assertIn("lance_document_revision", kwargs["input"])
        self.assertIn("MATERIALIZED", kwargs["input"])
        self.assertIn("lance_dataset_versions", kwargs["input"])
        self.assertEqual(pg["unfinished_jobs"]["rows"], [])
        self.assertEqual(lance["rows"], [])

    def test_inline_database_credentials_are_rejected_without_echoing_them(self):
        secret = "postgresql://operator:unit-test-only@localhost/archive"
        with self.assertRaises(doctor_pg_lance.DoctorError) as raised:
            doctor_pg_lance._psql_args(f"psql {secret}")
        self.assertNotIn("unit-test-only", str(raised.exception))

    def test_collection_manifest_summary_checks_dataset_and_omits_paths(self):
        import collection_manifest

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "archive-v2.lance"
            dataset.mkdir()
            manifest_path = root / "collection.json"
            descriptor = {
                "dataset_path": dataset.name,
                "embedding": {
                    "model": "test-model",
                    "model_version": "test-revision",
                    "dimensions": 4,
                    "normalization": "l2",
                    "normalization_version": "l2-f32-v1",
                },
                "chunking": {
                    "strategy": "whole_document",
                    "version": "whole-document-v1",
                    "size_chars": None,
                    "overlap_chars": 0,
                },
                "expected_vectors": 3,
                "created_at": "2026-11-01T00:00:00Z",
            }
            descriptor["profile_sha256"] = collection_manifest._profile_digest(
                "archive-main", "e5-v2", descriptor
            )
            raw = {
                "schema_version": 1,
                "collection_id": "archive-main",
                "authorization": collection_manifest.AUTHORIZATION,
                "active_version": "e5-v2",
                "staged_version": None,
                "generation": 4,
                "versions": {"e5-v2": descriptor},
                "readiness": {
                    "e5-v2": {
                        "profile_sha256": descriptor["profile_sha256"],
                        "completed_at": "2026-11-01T00:00:00Z",
                        "records": 3,
                        "vector_rows": 3,
                        "input_sha256": "a" * 64,
                        "lance_mode": "upsert",
                    }
                },
                "activation_history": [{
                    "action": "activate",
                    "from_version": None,
                    "to_version": "e5-v2",
                    "at": "2026-11-01T00:00:00Z",
                }],
            }
            manifest_path.write_text(json.dumps(raw), encoding="utf-8")

            result = doctor_pg_lance.collection_metadata(str(dataset), manifest_path)

            with self.assertRaises(doctor_pg_lance.DoctorError) as mismatch:
                doctor_pg_lance.collection_metadata(str(root / "other.lance"), manifest_path)
            unready = dict(raw)
            unready["readiness"] = {}
            unready_path = root / "unready.json"
            unready_path.write_text(json.dumps(unready), encoding="utf-8")
            with self.assertRaises(doctor_pg_lance.DoctorError) as no_receipt:
                doctor_pg_lance.collection_metadata(str(dataset), unready_path)

        self.assertEqual(result, {
            "collection_id": "archive-main",
            "generation": 4,
            "active_version": "e5-v2",
            "expected_vectors_at_activation": 3,
        })
        self.assertNotIn(tmp, json.dumps(result))
        self.assertNotIn(tmp, str(mismatch.exception))
        self.assertNotIn(tmp, str(no_receipt.exception))

    def test_collection_manifest_mismatch_and_missing_path_are_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "private" / "manifest.json"
            with self.assertRaises(doctor_pg_lance.DoctorError) as raised:
                doctor_pg_lance.collection_metadata("/private/archive.lance", missing)
        self.assertNotIn(tmp, str(raised.exception))

    def test_recovery_guidance_matches_operator_runbook(self):
        runbook = (Path(__file__).resolve().parents[1] / "OPERATIONS.md").read_text(encoding="utf-8")
        self.assertIn("python3 tools/lance_outbox_worker.py --once --psql", runbook)
        self.assertIn("Never mark an event completed manually.", runbook)
        self.assertIn("investigate in an isolated restore", runbook)
        self.assertIn("### Read-only doctor and exportable metrics", runbook)


if __name__ == "__main__":
    unittest.main()
