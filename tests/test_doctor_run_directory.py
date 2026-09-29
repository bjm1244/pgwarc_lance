import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TESTS = ROOT / "tests"
for directory in (TOOLS, TESTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
TOOL_PATH = TOOLS / "doctor_run_directory.py"
SPEC = importlib.util.spec_from_file_location("doctor_run_directory", TOOL_PATH)
doctor_run_directory = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(doctor_run_directory)

import quality_artifact_helpers  # noqa: E402


TIMEOUT_OBSERVATION_FIXTURE = (
    ROOT / "tests" / "fixtures" / "timeout_observation_contract_cases.json"
)


def load_timeout_observation_contract_cases() -> list[dict]:
    return json.loads(
        TIMEOUT_OBSERVATION_FIXTURE.read_text(encoding="utf-8")
    )["cases"]


def timeout_observation_payload(timeout_case: dict) -> dict:
    return {
        "status": "quality_eval_timeout",
        "success": False,
        "timeout": timeout_case,
        "results": [],
    }


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def metadata_payload(run_dir: Path, *, command: str | None = "make bench", dirty: bool = False) -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-07-06T00:00:00Z",
        "run_dir": str(run_dir),
        "command": command,
        "environment": {"PG_MAJOR": "16"},
        "git": {
            "root": str(ROOT),
            "head": "a" * 40,
            "head_short": "a" * 7,
            "branch": "master",
            "dirty": dirty,
            "status": [" M README.md"] if dirty else [],
        },
        "warnings": [],
    }


def artifact_payload(file: str) -> dict:
    import_summary_payload = {
        "records": 1,
        "sql_bytes": 1,
        "vectors": "hash",
        "lance_mode": "create",
        "lance_append_duplicates": "none",
        "bm25_mode": "bulk",
        "execute": False,
        "psql": "",
    }
    benchmark_payloads = {
        "bm25.json": {
            "benchmark": "bm25_index_document",
            "results": [
                {
                    "rows": 1,
                    "elapsed_s": 0.1,
                    "docs_per_s": 10.0,
                    "term_rows_per_s": 20.0,
                }
            ],
        },
        "lance.json": {
            "benchmark": "lance_insert_many",
            "results": [{"rows": 1, "batch_s": 0.1, "batch_count": 1}],
        },
        "warc.json": {
            "benchmark": "warc_importer",
            "rows": 1,
            "requested_rows": 1,
            "sql_bytes": 1,
            "generate_s": 0.0,
            "parse_s": 0.0,
            "sql_s": 0.0,
            "execute_s": None,
        },
        "warc_bm25_modes.json": {
            "benchmark": "warc_importer_bm25_mode_comparison",
            "results": [
                {
                    "rows": 1,
                    "modes": [
                        {"bm25_mode": "function", "sql_bytes": 1, "sql_s": 0.0, "execute_s": None},
                        {"bm25_mode": "bulk", "sql_bytes": 1, "sql_s": 0.0, "execute_s": None},
                        {"bm25_mode": "copy", "sql_bytes": 1, "sql_s": 0.0, "execute_s": None},
                    ],
                }
            ],
        },
        "benchmark_comparison.json": {
            "schema_version": 1,
            "benchmark": "warc_importer_bm25_mode_comparison",
            "baseline": "/tmp/baseline/warc_bm25_modes.json",
            "candidate": "/tmp/candidate/warc_bm25_modes.json",
            "metric": "execute_s",
            "max_regression_ratio": 0.10,
            "valid": True,
            "passed": True,
            "errors": [],
            "warnings": [],
            "comparison_count": 3,
            "regression_count": 0,
            "comparisons": [
                {
                    "rows": 1,
                    "bm25_mode": "function",
                    "metric": "execute_s",
                    "baseline": 1.0,
                    "candidate": 0.9,
                    "change_ratio": -0.1,
                    "regressed": False,
                },
                {
                    "rows": 1,
                    "bm25_mode": "bulk",
                    "metric": "execute_s",
                    "baseline": 0.8,
                    "candidate": 0.8,
                    "change_ratio": 0.0,
                    "regressed": False,
                },
                {
                    "rows": 1,
                    "bm25_mode": "copy",
                    "metric": "execute_s",
                    "baseline": 0.7,
                    "candidate": 0.7,
                    "change_ratio": 0.0,
                    "regressed": False,
                },
            ],
        },
        "quality.fixture.json": {
            "name": "tiny-quality",
            "vector_dim": 2,
            "docs": [
                {"doc_id": 1, "text": "alpha document", "vector": [1.0, 0.0]},
                {"doc_id": 2, "text": "beta control", "vector": [0.0, 1.0]},
            ],
            "queries": [
                {
                    "name": "alpha",
                    "query": "alpha",
                    "vector": [1.0, 0.0],
                    "expected_doc_ids": [1],
                }
            ],
        },
        "quality.json": {
            "benchmark": "retrieval_quality_smoke",
            "fixture": "tiny-quality",
            "doc_count": 2,
            "query_count": 1,
            "vector_dim": 2,
            "k": 3,
            "lance_uri": "/tmp/tiny-quality.lance",
            "hit_rate_at_k": 1.0,
            "mrr_at_k": 1.0,
            "results": [
                {
                    "name": "alpha",
                    "expected_doc_ids": [1],
                    "returned_doc_ids": [1, 2],
                    "hit": True,
                    "reciprocal_rank": 1.0,
                }
            ],
        },
        "import.summary.json": import_summary_payload,
    }
    status_payloads = {
        "quality_fixture_report.json": {
            "schema_version": 1,
            "fixture": "tiny-quality",
            "valid": True,
            "errors": [],
            "warnings": [],
            "vector_dim": 2,
            "doc_count": 2,
            "query_count": 1,
            "expected_label_count": 1,
            "labeled_doc_count": 1,
            "unlabeled_doc_count": 1,
            "queries": [
                {
                    "name": "alpha",
                    "query": "alpha",
                    "expected_count": 1,
                    "expected_doc_ids": [1],
                }
            ],
        },
        "quality_baseline_doctor.json": {
            "schema_version": 1,
            "valid": True,
            "errors": [],
            "warnings": [],
            "fixture": "tiny-quality",
            "thresholds": {
                "min_docs": 1,
                "min_queries": 1,
                "min_expected_labels": 1,
                "min_hit_rate_at_k": 0.0,
                "min_mrr_at_k": 0.0,
            },
            "doc_count": 2,
            "query_count": 1,
            "expected_label_count": 1,
            "k": 3,
            "hit_rate_at_k": 1.0,
            "mrr_at_k": 1.0,
            "results": [
                {
                    "name": "alpha",
                    "hit": True,
                    "reciprocal_rank": 1.0,
                }
            ],
        },
        "quality_labels_doctor.json": {"valid": True},
        "import_doctor.json": {
            "all_valid": True,
            "summary_count": 1,
            "error_count": 0,
            "warning_count": 1,
            "summaries": [
                {
                    "path": "import.summary.json",
                    "valid": True,
                    "errors": [],
                    "warnings": ["summary describes generated SQL only; execute=false"],
                    "summary": import_summary_payload,
                }
            ],
        },
        "release_artifacts_matrix.json": {
            "extension": "pgwarc_lance",
            "version": "0.1.0",
            "dist_root": "/tmp/dist",
            "pg_majors": [16],
            "all_valid": True,
            "artifacts": [
                {
                    "pg_major": 16,
                    "dist_dir": "/tmp/dist/pgwarc_lance-0.1.0-pg16",
                    "valid": True,
                    "errors": [],
                    "files": {
                        "shared_library": {
                            "path": "/tmp/dist/pgwarc_lance-0.1.0-pg16/pgwarc_lance.so",
                            "exists": True,
                            "bytes": 1,
                            "sha256": "0" * 64,
                        },
                        "control": {
                            "path": "/tmp/dist/pgwarc_lance-0.1.0-pg16/pgwarc_lance.control",
                            "exists": True,
                            "bytes": 1,
                            "sha256": "1" * 64,
                        },
                        "sql": {
                            "path": "/tmp/dist/pgwarc_lance-0.1.0-pg16/pgwarc_lance--0.1.0.sql",
                            "exists": True,
                            "bytes": 1,
                            "sha256": "2" * 64,
                        },
                    },
                }
            ],
        },
        "release_smoke_matrix.json": {
            "schema_version": 1,
            "generated_at": "2026-07-06T00:00:00Z",
            "cwd": "/tmp/pglance",
            "make_program": "make",
            "make_target": "smoke-release-artifacts",
            "make_vars": [],
            "pg_majors": [16],
            "output_tail_lines": 40,
            "all_passed": True,
            "results": [
                {
                    "pg_major": 16,
                    "command": "make PG_MAJOR=16 smoke-release-artifacts",
                    "exit_code": 0,
                    "passed": True,
                    "duration_seconds": 0.1,
                    "stdout_tail": "ok",
                    "stderr_tail": "",
                    "error": None,
                }
            ],
        },
    }
    return benchmark_payloads.get(file, status_payloads.get(file, {}))


def source_coverage_quality_report_payload() -> dict:
    payload = artifact_payload("quality_fixture_report.json")
    payload.update(
        {
            "provenance": "warc_provenance.json",
            "provenance_schema_version": 2,
            "min_source_docs": 1,
            "max_source_skipped_ratio": 0.5,
            "source_count": 2,
            "source_coverage": [
                {
                    "index": 1,
                    "kind": "local",
                    "path": "first.warc",
                    "url": None,
                    "warc_record_count": 2,
                    "imported_record_count": 1,
                    "skipped_record_count": 1,
                    "skipped_ratio": 0.5,
                    "doc_count": 1,
                    "labeled_doc_count": 1,
                    "unlabeled_doc_count": 0,
                },
                {
                    "index": 2,
                    "kind": "download",
                    "path": None,
                    "url": "file:///second.warc",
                    "warc_record_count": 1,
                    "imported_record_count": 1,
                    "skipped_record_count": 0,
                    "skipped_ratio": 0.0,
                    "doc_count": 1,
                    "labeled_doc_count": 0,
                    "unlabeled_doc_count": 1,
                },
            ],
        }
    )
    return payload


def warc_provenance_payload(run_dir: Path, source_path: Path, source_spec_path: Path) -> dict:
    return {
        "schema_version": 2,
        "generated_at": "2026-09-14T05:00:00Z",
        "source_spec": {
            "kind": "file",
            "path": str(source_spec_path),
            "sha256": doctor_run_directory.sha256_file(source_spec_path),
            "count": 1,
            "specs": [{"path": str(source_path), "url": None}],
        },
        "sources": [
            {
                "kind": "local",
                "url": None,
                "path": str(source_path),
                "bytes": source_path.stat().st_size,
                "sha256": doctor_run_directory.sha256_file(source_path),
                "downloaded_at": None,
                "records": {
                    "warc_record_count": 1,
                    "imported_record_count": 1,
                    "skipped_record_count": 0,
                },
                "coverage": {
                    "doc_count": 1,
                    "labeled_doc_count": 1,
                    "unlabeled_doc_count": 0,
                },
            }
        ],
        "query_spec": {
            "kind": "cli",
            "path": None,
            "sha256": None,
            "count": 1,
            "specs": [
                {"name": "alpha", "query": "alpha", "label_text": "alpha"}
            ],
        },
        "fixture": {
            "name": "real-warc-quality",
            "output": str(run_dir / "quality.fixture.json"),
            "vector_dim": 4,
            "doc_count": 1,
            "query_count": 1,
            "expected_doc_counts": [1],
        },
    }


def write_run_files(run_dir: Path, *, files: list[str], command: str = "make bench", dirty: bool = False) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for file in files:
        if file not in {"run_metadata.json", "run_manifest.json"}:
            write_json(run_dir / file, artifact_payload(file))
    write_json(
        run_dir / "run_metadata.json",
        metadata_payload(run_dir, command=command, dirty=dirty),
    )
    if "run_manifest.json" not in files:
        write_json(
            run_dir / "run_manifest.json",
            {
                "files": [{"path": file} for file in files if file != "run_manifest.json"]
                + [{"path": "run_metadata.json"}],
            },
        )


class RunDirectoryDoctorTests(unittest.TestCase):
    def test_generic_profile_accepts_metadata_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json"])

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=[],
            )

        self.assertTrue(report["valid"])
        self.assertEqual(report["error_count"], 0)

    def test_required_warc_provenance_validates_source_and_source_spec_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            source_path = run_dir / "source.warc"
            source_path.write_bytes(b"WARC/1.1\n")
            source_spec_path = run_dir / "sources.json"
            source_spec_path.write_text(
                json.dumps([{"path": str(source_path)}]) + "\n",
                encoding="utf-8",
            )
            write_run_files(run_dir, files=["warc_provenance.json"])
            write_json(
                run_dir / "warc_provenance.json",
                warc_provenance_payload(run_dir, source_path, source_spec_path),
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=["warc_provenance.json"],
            )

        self.assertTrue(report["valid"])
        self.assertEqual(report["error_count"], 0)

    def test_required_warc_provenance_detects_source_hash_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            source_path = run_dir / "source.warc"
            source_path.write_bytes(b"WARC/1.1\n")
            source_spec_path = run_dir / "sources.json"
            source_spec_path.write_text(
                json.dumps([{"path": str(source_path)}]) + "\n",
                encoding="utf-8",
            )
            write_run_files(run_dir, files=["warc_provenance.json"])
            write_json(
                run_dir / "warc_provenance.json",
                warc_provenance_payload(run_dir, source_path, source_spec_path),
            )
            source_path.write_bytes(b"WARC/1.1\nchanged\n")

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=["warc_provenance.json"],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "warc_provenance.json.sources[0].sha256 mismatch",
            "\n".join(report["errors"]),
        )

    def test_required_warc_provenance_accepts_schema1_download_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["warc_provenance.json"])
            payload = {
                "schema_version": 1,
                "generated_at": "2026-09-14T05:00:00Z",
                "source": {
                    "kind": "download",
                    "url": "https://example.test/source.warc.gz",
                    "path": None,
                    "bytes": 8,
                    "sha256": "0" * 64,
                    "downloaded_at": "2026-09-14T05:00:00Z",
                },
                "query_spec": {
                    "kind": "cli",
                    "path": None,
                    "sha256": None,
                    "count": 1,
                    "specs": [
                        {"name": "alpha", "query": "alpha", "label_text": "alpha"}
                    ],
                },
                "fixture": {
                    "name": "public-warc-smoke",
                    "output": str(run_dir / "quality.fixture.json"),
                    "vector_dim": 4,
                    "doc_count": 1,
                    "query_count": 1,
                    "expected_doc_counts": [1],
                },
            }
            write_json(run_dir / "warc_provenance.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=["warc_provenance.json"],
            )

        self.assertTrue(report["valid"])
        self.assertEqual(report["error_count"], 0)

    def test_run_metadata_schema_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json"])
            write_json(
                run_dir / "run_metadata.json",
                {
                    "schema_version": 2,
                    "generated_at": "",
                    "run_dir": "",
                    "command": ["make bench"],
                    "environment": {"PG_MAJOR": 16},
                    "git": {
                        "root": "",
                        "head": "not-a-head",
                        "head_short": "bad",
                        "branch": "",
                        "dirty": "false",
                        "status": [1],
                    },
                    "warnings": [None],
                },
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn("run_metadata.json.schema_version must be 1", report["errors"])
        self.assertIn("run_metadata.json.command must be a string or null", report["errors"])
        self.assertIn("run_metadata.json.environment.PG_MAJOR must be a string or null", report["errors"])
        self.assertIn(
            "run_metadata.json.git.dirty must be a boolean or null",
            report["errors"],
        )
        self.assertIn("run_metadata.json.git.status must contain strings", report["errors"])
        self.assertIn("run_metadata.json.warnings must contain strings", report["errors"])

    def test_quality_profile_requires_fixture_report_and_quality_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json"])

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn("missing required artifact: quality_fixture_report.json", report["errors"])
        self.assertIn("missing required artifact: quality.json", report["errors"])

    def test_benchmark_extra_required_checks_core_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json", "bm25.json"])

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="benchmark",
                extra_required=["bm25.json", "lance.json", "warc.json"],
            )

        self.assertFalse(report["valid"])
        self.assertIn("missing required artifact: lance.json", report["errors"])
        self.assertIn("missing required artifact: warc.json", report["errors"])

    def test_benchmark_profile_stays_generic_for_targeted_benchmark_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "warc_bm25_modes.json"],
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="benchmark",
                extra_required=[],
            )

        self.assertTrue(report["valid"])
        self.assertEqual(report["error_count"], 0)

    def test_required_core_benchmark_artifacts_are_schema_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json", "bm25.json"])
            write_json(run_dir / "bm25.json", {"benchmark": "wrong", "results": []})

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="benchmark",
                extra_required=["bm25.json"],
            )

        self.assertFalse(report["valid"])
        self.assertIn("bm25.json.benchmark must be 'bm25_index_document'", report["errors"])
        self.assertIn("bm25.json.results must be a non-empty list", report["errors"])

    def test_required_targeted_benchmark_artifacts_are_schema_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json", "warc_bm25_modes.json"])
            write_json(
                run_dir / "warc_bm25_modes.json",
                {
                    "benchmark": "warc_importer_bm25_mode_comparison",
                    "results": [
                        {
                            "rows": 1,
                            "modes": [
                                {
                                    "bm25_mode": "function",
                                    "sql_bytes": 1,
                                    "sql_s": 0.0,
                                    "execute_s": None,
                                }
                            ],
                        }
                    ],
                },
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="benchmark",
                extra_required=["warc_bm25_modes.json"],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "warc_bm25_modes.json.results[0].modes must include bulk, copy, function; found function",
            report["errors"],
        )

    def test_required_benchmark_comparison_artifact_is_schema_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json", "benchmark_comparison.json"])
            payload = artifact_payload("benchmark_comparison.json")
            payload["comparisons"][0]["regressed"] = True
            payload["valid"] = False
            payload["passed"] = False
            payload["errors"] = ["tampered report"]
            write_json(run_dir / "benchmark_comparison.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=["benchmark_comparison.json"],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "benchmark_comparison.json.comparisons[0].regressed does not match the configured threshold",
            report["errors"],
        )
        self.assertIn(
            "benchmark_comparison.json does not report passed=true",
            report["errors"],
        )

    def test_required_benchmark_comparison_schema_v2_accepts_input_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json", "benchmark_comparison.json"])
            payload = artifact_payload("benchmark_comparison.json")
            payload["schema_version"] = 2
            payload["input_provenance"] = {
                role: {
                    "path": payload[role],
                    "bytes": 1,
                    "sha256": "0" * 64,
                }
                for role in ("baseline", "candidate")
            }
            write_json(run_dir / "benchmark_comparison.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=["benchmark_comparison.json"],
            )

        self.assertTrue(report["valid"])
        self.assertEqual(report["error_count"], 0)

    def test_required_quality_fixture_artifact_is_schema_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json", "quality.fixture.json"])
            write_json(
                run_dir / "quality.fixture.json",
                {
                    "name": "bad",
                    "vector_dim": 2,
                    "docs": [{"doc_id": 1, "text": "alpha", "vector": [1.0, 0.0]}],
                    "queries": [
                        {
                            "name": "bad",
                            "query": "missing doc",
                            "vector": [1.0, 0.0],
                            "expected_doc_ids": [2],
                        }
                    ],
                },
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=["quality.fixture.json"],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "quality.fixture.json.queries[1]: unknown expected doc_id(s): 2",
            report["errors"],
        )

    def test_required_quality_fixture_artifact_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json", "quality.fixture.json"])
            payload = artifact_payload("quality.fixture.json")
            payload["docs"].append({"doc_id": 1, "text": "duplicate", "vector": [0.5, 0.5]})
            payload["queries"].append(
                {
                    "name": "alpha",
                    "query": "duplicate query",
                    "vector": [1.0, 0.0],
                    "expected_doc_ids": [1],
                }
            )
            write_json(run_dir / "quality.fixture.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=["quality.fixture.json"],
            )

        self.assertFalse(report["valid"])
        self.assertIn("quality.fixture.json: duplicate doc_id values: 1", report["errors"])
        self.assertIn("quality.fixture.json: duplicate query names: alpha", report["errors"])

    def test_quality_profile_checks_quality_json_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "quality_fixture_report.json", "quality.json"],
            )
            write_json(
                run_dir / "quality.json",
                {
                    "benchmark": "wrong",
                    "fixture": "tiny-quality",
                    "doc_count": 2,
                    "query_count": 1,
                    "vector_dim": 2,
                    "k": 3,
                    "hit_rate_at_k": 1.2,
                    "mrr_at_k": 1.0,
                    "results": [],
                },
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn("quality.json.benchmark must be 'retrieval_quality_smoke'", report["errors"])
        self.assertIn("quality.json.hit_rate_at_k must be a number between 0 and 1", report["errors"])
        self.assertIn("quality.json.results must be a non-empty list", report["errors"])

    def test_packaging_profile_requires_artifact_and_smoke_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "release_artifacts_matrix.json"],
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="packaging",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn("missing required artifact: release_smoke_matrix.json", report["errors"])

    def test_packaging_profile_checks_release_artifacts_matrix_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=[
                    "run_metadata.json",
                    "release_artifacts_matrix.json",
                    "release_smoke_matrix.json",
                ],
            )
            payload = artifact_payload("release_artifacts_matrix.json")
            payload["pg_majors"] = [16, 17]
            payload["artifacts"][0]["valid"] = False
            payload["artifacts"][0]["files"]["sql"]["sha256"] = "bad"
            write_json(run_dir / "release_artifacts_matrix.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="packaging",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "release_artifacts_matrix.json.artifacts count 1 does not match pg_majors count 2",
            report["errors"],
        )
        self.assertIn(
            "release_artifacts_matrix.json.artifacts[0].valid=false must include at least one error",
            report["errors"],
        )
        self.assertIn(
            "release_artifacts_matrix.json.artifacts[0].files.sql.sha256 must be a 64-character lowercase hex string when exists=true",
            report["errors"],
        )

    def test_status_artifacts_must_report_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=[
                    "run_metadata.json",
                    "quality_fixture_report.json",
                    "quality.json",
                    "quality_baseline_doctor.json",
                ],
            )
            write_json(run_dir / "quality_baseline_doctor.json", {"valid": False})

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "quality_baseline_doctor.json does not report valid=true",
            report["errors"],
        )

    def test_required_quality_baseline_doctor_artifact_schema_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=[
                    "run_metadata.json",
                    "quality_fixture_report.json",
                    "quality.json",
                    "quality_baseline_doctor.json",
                ],
            )
            payload = artifact_payload("quality_baseline_doctor.json")
            payload["query_count"] = 2
            payload["thresholds"]["min_hit_rate_at_k"] = 1.5
            write_json(run_dir / "quality_baseline_doctor.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=["quality_baseline_doctor.json"],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "quality_baseline_doctor.json.thresholds.min_hit_rate_at_k must be a number between 0 and 1",
            report["errors"],
        )
        self.assertIn(
            "quality_baseline_doctor.json.results count 1 does not match query_count 2",
            report["errors"],
        )

    def test_required_quality_baseline_doctor_source_coverage_contract_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=[
                    "run_metadata.json",
                    "quality_fixture_report.json",
                    "quality.json",
                    "quality_baseline_doctor.json",
                ],
            )
            payload = artifact_payload("quality_baseline_doctor.json")
            source_payload = source_coverage_quality_report_payload()
            for field in (
                "provenance",
                "provenance_schema_version",
                "min_source_docs",
                "max_source_skipped_ratio",
                "source_count",
                "labeled_doc_count",
                "unlabeled_doc_count",
                "source_coverage",
            ):
                payload[field] = source_payload[field]
            write_json(run_dir / "quality_baseline_doctor.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=["quality_baseline_doctor.json"],
            )

        self.assertTrue(report["valid"])
        self.assertEqual(report["error_count"], 0)

    def test_quality_fixture_report_artifact_schema_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "quality_fixture_report.json", "quality.json"],
            )
            write_json(
                run_dir / "quality_fixture_report.json",
                {
                    "valid": True,
                    "fixture": "tiny-quality",
                    "vector_dim": 2,
                    "doc_count": 2,
                    "query_count": 1,
                    "expected_label_count": 2,
                    "labeled_doc_count": 1,
                    "unlabeled_doc_count": 1,
                    "queries": [
                        {
                            "name": "alpha",
                            "query": "alpha",
                            "expected_count": 1,
                            "expected_doc_ids": [1],
                        }
                    ],
                },
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "quality_fixture_report.json.expected_label_count 2 does not match query total 1",
            report["errors"],
        )

    def test_quality_fixture_report_source_coverage_contract_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "quality_fixture_report.json", "quality.json"],
            )
            write_json(
                run_dir / "quality_fixture_report.json",
                source_coverage_quality_report_payload(),
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=[],
            )

        self.assertTrue(report["valid"])
        self.assertEqual(report["error_count"], 0)

    def test_quality_fixture_report_legacy_source_coverage_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "quality_fixture_report.json", "quality.json"],
            )
            payload = artifact_payload("quality_fixture_report.json")
            payload.update(
                {
                    "provenance": "warc_provenance.json",
                    "provenance_schema_version": 1,
                    "min_source_docs": 1,
                    "max_source_skipped_ratio": 0.0,
                    "source_count": 0,
                    "source_coverage": [],
                }
            )
            write_json(run_dir / "quality_fixture_report.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=[],
            )

        self.assertTrue(report["valid"])
        self.assertEqual(report["error_count"], 0)

    def test_quality_fixture_report_source_coverage_totals_and_thresholds_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "quality_fixture_report.json", "quality.json"],
            )
            payload = source_coverage_quality_report_payload()
            payload["max_source_skipped_ratio"] = 0.25
            payload["source_coverage"][0]["skipped_ratio"] = 0.25
            payload["source_coverage"][1]["unlabeled_doc_count"] = 0
            write_json(run_dir / "quality_fixture_report.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "quality_fixture_report.json.source_coverage[0].skipped_ratio 0.25 does not match skipped/raw 0.500000",
            report["errors"],
        )
        self.assertIn(
            "quality_fixture_report.json.source_coverage[0] skipped ratio 0.500000 is above maximum 0.250000",
            report["errors"],
        )
        self.assertIn(
            "quality_fixture_report.json.source_coverage[1] labeled/unlabeled counts do not add up to doc_count",
            report["errors"],
        )
        self.assertIn(
            "quality_fixture_report.json unlabeled doc total 0 does not match unlabeled_doc_count 1",
            report["errors"],
        )

    def test_required_quality_label_doctor_artifact_must_report_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "quality_labels_doctor.json"],
            )
            write_json(run_dir / "quality_labels_doctor.json", {"valid": False})

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=["quality_labels_doctor.json"],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "quality_labels_doctor.json does not report valid=true",
            report["errors"],
        )

    def test_import_doctor_artifact_must_report_all_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "import.summary.json", "import_doctor.json"],
            )
            write_json(run_dir / "import_doctor.json", {"all_valid": False})

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="import",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn("import_doctor.json does not report all_valid=true", report["errors"])

    def test_import_profile_checks_import_summary_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "import.summary.json", "import_doctor.json"],
            )
            write_json(
                run_dir / "import.summary.json",
                {
                    "records": -1,
                    "sql_bytes": "many",
                    "vectors": "external",
                    "lance_mode": "replace",
                    "lance_append_duplicates": "yes",
                    "bm25_mode": "fast",
                    "execute": "false",
                    "psql": 123,
                },
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="import",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "import.summary.json: records must be a non-negative integer",
            report["errors"],
        )
        self.assertIn(
            "import.summary.json: bm25_mode must be one of: bulk, copy, function",
            report["errors"],
        )
        self.assertIn("import.summary.json: execute must be a boolean", report["errors"])

    def test_required_import_doctor_artifact_schema_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=["run_metadata.json", "import.summary.json", "import_doctor.json"],
            )
            payload = artifact_payload("import_doctor.json")
            payload["summary_count"] = 2
            payload["summaries"][0]["valid"] = False
            payload["summaries"][0]["errors"] = ["bad summary"]
            write_json(run_dir / "import_doctor.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="import",
                extra_required=["import_doctor.json"],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "import_doctor.json.summary_count 2 does not match summaries count 1",
            report["errors"],
        )
        self.assertIn(
            "import_doctor.json.error_count 0 does not match summaries total 1",
            report["errors"],
        )
        self.assertIn(
            "import_doctor.json.all_valid must be false based on summaries",
            report["errors"],
        )

    def test_packaging_reports_must_report_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=[
                    "run_metadata.json",
                    "release_artifacts_matrix.json",
                    "release_smoke_matrix.json",
                ],
            )
            write_json(run_dir / "release_smoke_matrix.json", {"all_passed": False})

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="packaging",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "release_smoke_matrix.json does not report all_passed=true",
            report["errors"],
        )

    def test_packaging_profile_checks_release_smoke_matrix_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(
                run_dir,
                files=[
                    "run_metadata.json",
                    "release_artifacts_matrix.json",
                    "release_smoke_matrix.json",
                ],
            )
            payload = artifact_payload("release_smoke_matrix.json")
            payload["pg_majors"] = [16, 17]
            payload["results"][0]["passed"] = False
            write_json(run_dir / "release_smoke_matrix.json", payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="packaging",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "release_smoke_matrix.json.results count 1 does not match pg_majors count 2",
            report["errors"],
        )
        self.assertIn(
            "release_smoke_matrix.json.results[0].passed=false must include non-zero/null exit_code or error",
            report["errors"],
        )
        self.assertIn(
            "release_smoke_matrix.json.all_passed must be false based on results",
            report["errors"],
        )

    def test_extra_required_artifact_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json", "quality.json"])

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=["quality.md"],
            )

        self.assertFalse(report["valid"])
        self.assertIn("missing required artifact: quality.md", report["errors"])

    def test_manifest_missing_artifact_entry_is_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_json(run_dir / "run_metadata.json", metadata_payload(run_dir))
            write_json(run_dir / "quality_fixture_report.json", artifact_payload("quality_fixture_report.json"))
            write_json(run_dir / "quality.json", artifact_payload("quality.json"))
            write_json(run_dir / "run_manifest.json", {"files": [{"path": "run_metadata.json"}]})

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=[],
            )

        self.assertTrue(report["valid"])
        self.assertTrue(
            any("absent from run_manifest.json: quality.json" in warning for warning in report["warnings"])
        )
        self.assertTrue(
            any(
                "absent from run_manifest.json: quality_fixture_report.json" in warning
                for warning in report["warnings"]
            )
        )

    def test_manifest_integrity_mismatch_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            metadata = run_dir / "run_metadata.json"
            write_json(metadata, metadata_payload(run_dir))
            write_json(
                run_dir / "run_manifest.json",
                {
                    "files": [
                        {
                            "path": "run_metadata.json",
                            "bytes": 1,
                            "sha256": "0" * 64,
                        }
                    ]
                },
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertTrue(
            any("bytes mismatch for run_metadata.json" in error for error in report["errors"])
        )
        self.assertIn("run_manifest.json sha256 mismatch for run_metadata.json", report["errors"])

    def test_manifest_entry_missing_file_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_json(run_dir / "run_metadata.json", metadata_payload(run_dir))
            write_json(
                run_dir / "run_manifest.json",
                {"files": [{"path": "run_metadata.json"}, {"path": "missing.json"}]},
            )

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertIn("run_manifest.json entry points to missing file: missing.json", report["errors"])

    def test_dirty_metadata_and_missing_command_are_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_run_files(run_dir, files=["run_metadata.json"], command="", dirty=True)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="generic",
                extra_required=[],
            )

        self.assertTrue(report["valid"])
        self.assertTrue(any("dirty git worktree" in warning for warning in report["warnings"]))
        self.assertTrue(any("does not record a command" in warning for warning in report["warnings"]))

    def test_main_writes_outputs_and_returns_nonzero_for_missing_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            write_run_files(run_dir, files=["run_metadata.json"])
            json_output = Path(tmp) / "reports" / "doctor.json"
            markdown_output = Path(tmp) / "reports" / "doctor.md"

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = doctor_run_directory.main(
                    [
                        "--run-dir",
                        str(run_dir),
                        "--profile",
                        "quality",
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ]
                )

            report = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = markdown_output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["valid"])
        self.assertIn("# pgwarc_lance Run Directory Doctor", markdown)
        self.assertIn("missing required artifact: quality.json", markdown)


class QualityEvalNonSuccessArtifactTests(unittest.TestCase):
    def make_run_dir(self, tmp_path: Path, quality_payload: dict) -> Path:
        run_dir = tmp_path / "run"
        write_run_files(
            run_dir,
            files=["run_metadata.json", "quality_fixture_report.json", "quality.json"],
        )
        write_json(run_dir / "quality.json", quality_payload)
        return run_dir

    def test_producer_success_artifact_keeps_success_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "success"
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            doctor_run_directory.validate_quality_eval_artifact("quality.json", payload),
            [],
        )

    def test_setup_timeout_artifact_reports_status_specific_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "setup_timeout"
            )
            errors = doctor_run_directory.validate_quality_eval_artifact(
                "quality.json", payload
            )

        self.assertEqual(exit_code, 4)
        self.assertTrue(
            any("non-success status 'quality_eval_timeout'" in error for error in errors),
            errors,
        )
        self.assertTrue(any("timed out during stage 'setup'" in error for error in errors), errors)
        self.assertTrue(
            any("client-side subprocess limit only" in error for error in errors), errors
        )
        self.assertFalse(any("hit_rate_at_k" in error for error in errors), errors)
        self.assertFalse(
            any("results must be a non-empty list" in error for error in errors), errors
        )

    def test_query_timeout_artifact_validates_partial_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "query_timeout"
            )
            errors = doctor_run_directory.validate_quality_eval_artifact(
                "quality.json", payload
            )

        self.assertEqual(exit_code, 4)
        self.assertTrue(any("timed out during stage 'query'" in error for error in errors), errors)
        self.assertTrue(
            any("preserved 1 partial result" in error for error in errors), errors
        )
        self.assertFalse(any("timeout." in error for error in errors), errors)
        self.assertFalse(any("hit_rate_at_k" in error for error in errors), errors)

    def test_server_statement_timeout_artifact_is_structured_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "server_statement_timeout"
            )
            errors = doctor_run_directory.validate_quality_eval_artifact(
                "quality.json", payload
            )

        self.assertEqual(exit_code, 4)
        self.assertTrue(
            any("reports non-success status 'quality_eval_timeout'" in error for error in errors),
            errors,
        )
        self.assertTrue(
            any("preserved 1 partial result" in error for error in errors), errors
        )
        self.assertFalse(any(".timeout." in error for error in errors), errors)
        observation = payload["timeout"]["timeout_observation"]
        self.assertEqual("server_statement_timeout_reported", observation["statement_outcome"])
        self.assertFalse(any("hit_rate_at_k" in error for error in errors), errors)

    def test_budget_exhausted_artifact_validates_budget_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "budget_exhausted"
            )
            errors = doctor_run_directory.validate_quality_eval_artifact(
                "quality.json", payload
            )

        self.assertEqual(exit_code, 3)
        self.assertTrue(any("exhausted the run budget" in error for error in errors), errors)
        self.assertTrue(
            any("preserved 1 partial result" in error for error in errors), errors
        )
        self.assertFalse(any("budget." in error for error in errors), errors)
        self.assertFalse(any("hit_rate_at_k" in error for error in errors), errors)

    def test_malformed_timeout_diagnostics_are_reported(self):
        payload = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "weird",
                "limit_seconds": 0,
                "completed_results": 1,
                "partial_result_preserved": False,
                "partial_result_count": 0,
                "client_side_only": False,
            },
            "results": [],
        }

        errors = doctor_run_directory.validate_quality_eval_artifact("quality.json", payload)

        self.assertIn("quality.json.timeout.stage must be 'setup' or 'query'", errors)
        self.assertIn("quality.json.timeout.limit_seconds must be a positive number", errors)
        self.assertIn(
            "quality.json.timeout.completed_results 1 does not match results count 0",
            errors,
        )
        self.assertIn(
            "quality.json.timeout.client_side_only=false requires timeout_source "
            "'server_statement'",
            errors,
        )

    def test_client_timeout_observation_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "query_timeout"
            )
            errors = doctor_run_directory.validate_quality_eval_artifact(
                "quality.json", payload
            )

        self.assertEqual(exit_code, 4)
        timeout = payload["timeout"]
        self.assertTrue(timeout["client_side_only"])
        observation = timeout["timeout_observation"]
        self.assertEqual("unknown", observation["statement_outcome"])
        self.assertEqual("unobserved", observation["backend_state_after_timeout"])
        self.assertEqual("unverified", observation["cancellation_completion"])
        self.assertEqual("unverified", observation["transaction_cleanup"])
        self.assertFalse(any(".timeout." in error for error in errors), errors)

    def test_client_timeout_with_statement_text_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload, exit_code, markdown = quality_artifact_helpers.produce_artifact(
                tmp_path, "client_timeout_with_statement_text"
            )
            errors = doctor_run_directory.validate_quality_eval_artifact(
                "quality.json", payload
            )

        self.assertEqual(exit_code, 4)
        timeout = payload["timeout"]
        self.assertTrue(timeout["client_side_only"])
        self.assertNotIn("timeout_source", timeout)
        self.assertEqual(
            "unknown", timeout["timeout_observation"]["statement_outcome"]
        )
        self.assertFalse(any(".timeout." in error for error in errors), errors)
        self.assertIn("timeout_observation.statement_outcome: `unknown`", markdown)

    def test_legacy_timeout_without_observation_is_accepted(self):
        payload = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "setup",
                "reason": "setup SQL exceeded the effective client-side timeout",
                "limit_seconds": 5.0,
                "completed_results": 0,
                "partial_result_preserved": False,
                "partial_result_count": 0,
                "client_side_only": True,
            },
            "results": [],
        }

        errors = doctor_run_directory.validate_quality_eval_artifact("quality.json", payload)

        self.assertNotIn("timeout_observation", payload["timeout"])
        self.assertFalse(any(".timeout." in error for error in errors), errors)
        self.assertTrue(
            any("timeout observation was not recorded" in error for error in errors), errors
        )

    def test_server_timeout_missing_observation_key_is_rejected(self):
        payload = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "query",
                "reason": "server statement timeout",
                "limit_seconds": 5.0,
                "completed_results": 0,
                "partial_result_preserved": False,
                "partial_result_count": 0,
                "client_side_only": False,
                "timeout_source": "server_statement",
                "requested_statement_timeout_ms": 5000,
                "effective_statement_timeout_ms": 1,
                "sqlstate": "57014",
            },
            "results": [],
        }

        errors = doctor_run_directory.validate_quality_eval_artifact("quality.json", payload)

        self.assertNotIn("timeout_observation", payload["timeout"])
        self.assertIn(
            "quality.json.timeout.timeout_observation.statement_outcome must be "
            "'server_statement_timeout_reported' when client_side_only=false",
            errors,
        )

    def test_server_timeout_explicit_null_observation_is_rejected(self):
        payload = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "query",
                "reason": "server statement timeout",
                "limit_seconds": 5.0,
                "completed_results": 0,
                "partial_result_preserved": False,
                "partial_result_count": 0,
                "client_side_only": False,
                "timeout_source": "server_statement",
                "requested_statement_timeout_ms": 5000,
                "effective_statement_timeout_ms": 1,
                "sqlstate": "57014",
                "timeout_observation": None,
            },
            "results": [],
        }

        errors = doctor_run_directory.validate_quality_eval_artifact("quality.json", payload)

        self.assertIn(
            "quality.json.timeout.timeout_observation must be an object", errors
        )
        self.assertIn(
            "quality.json.timeout.timeout_observation.statement_outcome must be "
            "'server_statement_timeout_reported' when client_side_only=false",
            errors,
        )

    def test_client_timeout_missing_observation_is_legacy(self):
        payload = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "query",
                "reason": "client timeout",
                "limit_seconds": 5.0,
                "completed_results": 0,
                "partial_result_preserved": False,
                "partial_result_count": 0,
                "client_side_only": True,
            },
            "results": [],
        }

        errors = doctor_run_directory.validate_quality_eval_artifact("quality.json", payload)

        self.assertNotIn("timeout_observation", payload["timeout"])
        self.assertNotIn(
            "quality.json.timeout.timeout_observation must be an object", errors
        )
        self.assertNotIn(
            "quality.json.timeout.timeout_observation.statement_outcome must be "
            "'server_statement_timeout_reported' when client_side_only=false",
            errors,
        )
        self.assertTrue(
            any("timeout observation was not recorded" in error for error in errors), errors
        )

    def test_client_timeout_explicit_null_observation_is_rejected(self):
        payload = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "query",
                "reason": "client timeout",
                "limit_seconds": 5.0,
                "completed_results": 0,
                "partial_result_preserved": False,
                "partial_result_count": 0,
                "client_side_only": True,
                "timeout_observation": None,
            },
            "results": [],
        }

        errors = doctor_run_directory.validate_quality_eval_artifact("quality.json", payload)

        self.assertIn(
            "quality.json.timeout.timeout_observation must be an object", errors
        )

    def test_client_timeout_malformed_observation_is_rejected(self):
        payload = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "query",
                "reason": "client timeout",
                "limit_seconds": 5.0,
                "completed_results": 0,
                "partial_result_preserved": False,
                "partial_result_count": 0,
                "client_side_only": True,
                "timeout_observation": "not-an-object",
            },
            "results": [],
        }

        errors = doctor_run_directory.validate_quality_eval_artifact("quality.json", payload)

        self.assertIn(
            "quality.json.timeout.timeout_observation must be an object", errors
        )

    def test_malformed_timeout_observation_is_structured_error(self):
        payload = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "query",
                "reason": "client timeout",
                "limit_seconds": 5.0,
                "completed_results": 0,
                "partial_result_preserved": False,
                "partial_result_count": 0,
                "client_side_only": True,
                "timeout_observation": {
                    "statement_outcome": "server_statement_timeout_reported",
                    "backend_state_after_timeout": "stopped",
                    "cancellation_completion": "complete",
                    "transaction_cleanup": "rolled_back",
                },
            },
            "results": [],
        }

        errors = doctor_run_directory.validate_quality_eval_artifact("quality.json", payload)

        self.assertIn(
            "quality.json.timeout.timeout_observation.backend_state_after_timeout "
            "must be 'unobserved'",
            errors,
        )
        self.assertIn(
            "quality.json.timeout.timeout_observation.cancellation_completion "
            "must be 'unverified'",
            errors,
        )
        self.assertIn(
            "quality.json.timeout.timeout_observation.transaction_cleanup "
            "must be 'unverified'",
            errors,
        )
        self.assertIn(
            "quality.json.timeout.client_side_only=true conflicts with "
            "timeout_observation.statement_outcome 'server_statement_timeout_reported'",
            errors,
        )

    def test_timeout_observation_type_and_enum_errors(self):
        payload = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "query",
                "reason": "client timeout",
                "limit_seconds": 5.0,
                "completed_results": 0,
                "partial_result_preserved": False,
                "partial_result_count": 0,
                "client_side_only": True,
                "timeout_observation": "not-an-object",
            },
            "results": [],
        }
        errors = doctor_run_directory.validate_quality_eval_artifact("quality.json", payload)
        self.assertIn("quality.json.timeout.timeout_observation must be an object", errors)

        payload["timeout"]["timeout_observation"] = {
            "statement_outcome": "mystery",
            "backend_state_after_timeout": "unobserved",
            "cancellation_completion": "unverified",
            "transaction_cleanup": "unverified",
        }
        errors = doctor_run_directory.validate_quality_eval_artifact("quality.json", payload)
        self.assertIn(
            "quality.json.timeout.timeout_observation.statement_outcome must be one of: "
            "unknown, server_statement_timeout_reported",
            errors,
        )

    def test_server_timeout_wrong_source_or_outcome_is_rejected(self):
        base_timeout = {
            "stage": "query",
            "reason": "server timeout",
            "limit_seconds": 5.0,
            "completed_results": 0,
            "partial_result_preserved": False,
            "partial_result_count": 0,
            "client_side_only": False,
            "requested_statement_timeout_ms": 5000,
            "effective_statement_timeout_ms": 5000,
            "sqlstate": "57014",
            "timeout_observation": {
                "statement_outcome": "server_statement_timeout_reported",
                "backend_state_after_timeout": "unobserved",
                "cancellation_completion": "unverified",
                "transaction_cleanup": "unverified",
            },
        }

        wrong_source = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {**base_timeout, "timeout_source": "client"},
            "results": [],
        }
        errors = doctor_run_directory.validate_quality_eval_artifact(
            "quality.json", wrong_source
        )
        self.assertIn(
            "quality.json.timeout.client_side_only=false requires timeout_source "
            "'server_statement'",
            errors,
        )

        wrong_outcome = {
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {**base_timeout, "timeout_source": "server_statement"},
            "results": [],
        }
        wrong_outcome["timeout"]["timeout_observation"] = {
            **base_timeout["timeout_observation"],
            "statement_outcome": "unknown",
        }
        errors = doctor_run_directory.validate_quality_eval_artifact(
            "quality.json", wrong_outcome
        )
        self.assertIn(
            "quality.json.timeout.timeout_observation.statement_outcome must be "
            "'server_statement_timeout_reported' when client_side_only=false",
            errors,
        )

    def test_timeout_observation_contract_fixture_cases(self):
        cases = load_timeout_observation_contract_cases()
        self.assertTrue(cases)

        for case in cases:
            with self.subTest(case=case["name"]):
                payload = timeout_observation_payload(case["timeout"])
                errors = doctor_run_directory.validate_quality_eval_artifact(
                    "quality.json", payload
                )
                timeout_errors = [error for error in errors if ".timeout." in error]
                if case["accepted"]:
                    self.assertEqual([], timeout_errors, errors)
                    continue
                self.assertTrue(timeout_errors, errors)
                for expected in case["expected_errors"]:
                    self.assertIn(expected, timeout_errors)

    def test_unsupported_timeout_observation_claims_are_rejected(self):
        base_observation = {
            "statement_outcome": "unknown",
            "backend_state_after_timeout": "unobserved",
            "cancellation_completion": "unverified",
            "transaction_cleanup": "unverified",
        }
        payload = timeout_observation_payload(
            {
                "stage": "query",
                "reason": "client subprocess timeout",
                "limit_seconds": 5.0,
                "completed_results": 0,
                "partial_result_preserved": False,
                "partial_result_count": 0,
                "client_side_only": True,
                "timeout_observation": dict(base_observation),
            }
        )
        baseline_errors = doctor_run_directory.validate_quality_eval_artifact(
            "quality.json", payload
        )
        self.assertFalse(
            any(".timeout." in error for error in baseline_errors), baseline_errors
        )

        for claim, value in (
            ("backend_pid", 4242),
            ("cancelled", True),
            ("rollback_completed", True),
        ):
            with self.subTest(claim=claim):
                payload = timeout_observation_payload(
                    {
                        "stage": "query",
                        "reason": "client subprocess timeout",
                        "limit_seconds": 5.0,
                        "completed_results": 0,
                        "partial_result_preserved": False,
                        "partial_result_count": 0,
                        "client_side_only": True,
                        "timeout_observation": {**base_observation, claim: value},
                    }
                )
                errors = doctor_run_directory.validate_quality_eval_artifact(
                    "quality.json", payload
                )
                self.assertIn(
                    "quality.json.timeout.timeout_observation must not include "
                    f"unsupported fields: {claim}",
                    errors,
                )

    def test_run_doctor_stays_invalid_for_server_timeout_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload, _, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "server_statement_timeout"
            )
            run_dir = self.make_run_dir(tmp_path, payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertTrue(
            any("quality_eval_timeout" in error for error in report["errors"]),
            report["errors"],
        )
        self.assertFalse(
            any("quality.json.hit_rate_at_k" in error for error in report["errors"]),
            report["errors"],
        )

    def test_unknown_and_contradictory_status_are_structured_errors(self):
        cases = (
            ({"status": "mystery", "success": False}, "unknown status 'mystery'"),
            (
                {"status": "quality_eval_timeout", "success": True},
                "contradicts success=true",
            ),
            ({"success": False}, "success=false without a status"),
        )
        for payload, needle in cases:
            with self.subTest(needle=needle):
                errors = doctor_run_directory.validate_quality_eval_artifact(
                    "quality.json", payload
                )
                self.assertTrue(
                    any(needle in error for error in errors),
                    errors,
                )
                self.assertFalse(any("hit_rate_at_k" in error for error in errors), errors)

    def test_run_doctor_stays_invalid_for_timeout_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload, _, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "setup_timeout"
            )
            run_dir = self.make_run_dir(tmp_path, payload)

            report = doctor_run_directory.build_report(
                run_dir=run_dir,
                profile="quality",
                extra_required=[],
            )

        self.assertFalse(report["valid"])
        self.assertTrue(
            any("quality_eval_timeout" in error for error in report["errors"]),
            report["errors"],
        )
        self.assertFalse(
            any("quality.json.results must be a non-empty list" in error for error in report["errors"]),
            report["errors"],
        )
        self.assertFalse(
            any("quality.json.hit_rate_at_k" in error for error in report["errors"]),
            report["errors"],
        )

    def test_main_non_success_artifact_exits_nonzero_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload, _, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "budget_exhausted"
            )
            run_dir = self.make_run_dir(tmp_path, payload)
            json_output = tmp_path / "doctor.json"
            markdown_output = tmp_path / "doctor.md"
            stderr = io.StringIO()

            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                exit_code = doctor_run_directory.main(
                    [
                        "--run-dir",
                        str(run_dir),
                        "--profile",
                        "quality",
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ]
                )

            report = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = markdown_output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["valid"])
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertIn("budget_exhausted", markdown)
        self.assertIn("exhausted the run budget", markdown)


if __name__ == "__main__":
    unittest.main()
