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
TOOL_PATH = TOOLS / "doctor_quality_baseline.py"
SPEC = importlib.util.spec_from_file_location("doctor_quality_baseline", TOOL_PATH)
doctor_quality_baseline = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(doctor_quality_baseline)

import quality_artifact_helpers  # noqa: E402


def fixture_report_payload() -> dict:
    return {
        "schema_version": 1,
        "fixture": "tiny-quality",
        "valid": True,
        "errors": [],
        "warnings": [],
        "doc_count": 3,
        "query_count": 2,
        "expected_label_count": 3,
        "queries": [
            {"name": "alpha", "expected_count": 1},
            {"name": "beta", "expected_count": 2},
        ],
    }


def source_coverage_fixture_report_payload() -> dict:
    payload = fixture_report_payload()
    payload.update(
        {
            "provenance": "warc_provenance.json",
            "provenance_schema_version": 2,
            "min_source_docs": 1,
            "max_source_skipped_ratio": 0.5,
            "source_count": 2,
            "labeled_doc_count": 2,
            "unlabeled_doc_count": 1,
            "source_coverage": [
                {
                    "index": 1,
                    "kind": "local",
                    "path": "first.warc",
                    "warc_record_count": 3,
                    "imported_record_count": 2,
                    "skipped_record_count": 1,
                    "skipped_ratio": 1 / 3,
                    "doc_count": 2,
                    "labeled_doc_count": 1,
                    "unlabeled_doc_count": 1,
                },
                {
                    "index": 2,
                    "kind": "download",
                    "url": "file:///second.warc",
                    "warc_record_count": 1,
                    "imported_record_count": 1,
                    "skipped_record_count": 0,
                    "skipped_ratio": 0.0,
                    "doc_count": 1,
                    "labeled_doc_count": 1,
                    "unlabeled_doc_count": 0,
                },
            ],
        }
    )
    return payload


def quality_payload() -> dict:
    return {
        "benchmark": "retrieval_quality_smoke",
        "fixture": "tiny-quality",
        "doc_count": 3,
        "query_count": 2,
        "k": 3,
        "hit_rate_at_k": 0.5,
        "mrr_at_k": 0.25,
        "results": [
            {"name": "alpha", "hit": True, "reciprocal_rank": 0.5},
            {"name": "beta", "hit": False, "reciprocal_rank": 0.0},
        ],
    }


def real_corpus_fixture_report_payload() -> dict:
    payload = source_coverage_fixture_report_payload()
    payload["queries"] = [{"name": f"query_{index}", "expected_count": 1} for index in range(20)]
    payload["query_count"] = len(payload["queries"])
    payload["expected_label_count"] = len(payload["queries"])
    return payload


def real_corpus_quality_payload() -> dict:
    payload = quality_payload()
    payload["query_count"] = 20
    payload["hit_rate_at_k"] = 0.8
    payload["mrr_at_k"] = 0.7
    payload["results"] = [
        {"name": f"query_{index}", "hit": True, "reciprocal_rank": 0.7}
        for index in range(20)
    ]
    return payload


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


class QualityBaselineDoctorTests(unittest.TestCase):
    def test_build_report_accepts_matching_artifacts_at_threshold(self):
        report = doctor_quality_baseline.build_report(
            fixture_report=fixture_report_payload(),
            quality=quality_payload(),
            fixture_report_path=Path("quality_fixture_report.json"),
            quality_path=Path("quality.json"),
            min_docs=3,
            min_queries=2,
            min_expected_labels=3,
            min_hit_rate=0.5,
            min_mrr=0.25,
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["fixture"], "tiny-quality")
        self.assertEqual(report["doc_count"], 3)
        self.assertEqual(report["query_count"], 2)
        self.assertEqual(report["expected_label_count"], 3)
        self.assertEqual(report["hit_rate_at_k"], 0.5)
        self.assertEqual(report["mrr_at_k"], 0.25)

    def test_metrics_below_threshold_are_errors(self):
        report = doctor_quality_baseline.build_report(
            fixture_report=fixture_report_payload(),
            quality=quality_payload(),
            fixture_report_path=Path("quality_fixture_report.json"),
            quality_path=Path("quality.json"),
            min_docs=3,
            min_queries=2,
            min_expected_labels=3,
            min_hit_rate=0.75,
            min_mrr=0.5,
        )

        self.assertFalse(report["valid"])
        self.assertIn("hit_rate_at_k 0.500000 is below minimum 0.750000", report["errors"])
        self.assertIn("mrr_at_k 0.250000 is below minimum 0.500000", report["errors"])

    def test_build_report_preserves_source_coverage_contract(self):
        report = doctor_quality_baseline.build_report(
            fixture_report=source_coverage_fixture_report_payload(),
            quality=quality_payload(),
            fixture_report_path=Path("quality_fixture_report.json"),
            quality_path=Path("quality.json"),
            min_docs=3,
            min_queries=2,
            min_expected_labels=3,
            min_hit_rate=0.5,
            min_mrr=0.25,
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["provenance_schema_version"], 2)
        self.assertEqual(report["source_count"], 2)
        self.assertEqual(report["labeled_doc_count"], 2)
        self.assertEqual(report["unlabeled_doc_count"], 1)
        self.assertAlmostEqual(report["source_coverage"][0]["skipped_ratio"], 1 / 3)
        markdown = doctor_quality_baseline.render_markdown(report)
        self.assertIn("## WARC Source Coverage", markdown)
        self.assertIn("| Source | Raw | Imported |", markdown)

    def test_real_corpus_mode_accepts_v2_manifest_and_positive_thresholds(self):
        report = doctor_quality_baseline.build_report(
            fixture_report=real_corpus_fixture_report_payload(),
            quality=real_corpus_quality_payload(),
            fixture_report_path=Path("quality_fixture_report.json"),
            quality_path=Path("quality.json"),
            min_docs=3,
            min_queries=20,
            min_expected_labels=20,
            min_hit_rate=0.75,
            min_mrr=0.6,
            require_real_corpus=True,
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["validation_profile"], "real_warc")
        self.assertTrue(report["real_corpus_required"])

    def test_real_corpus_mode_rejects_synthetic_shape_and_zero_thresholds(self):
        report = doctor_quality_baseline.build_report(
            fixture_report=fixture_report_payload(),
            quality=quality_payload(),
            fixture_report_path=Path("quality_fixture_report.json"),
            quality_path=Path("quality.json"),
            min_docs=3,
            min_queries=1,
            min_expected_labels=1,
            min_hit_rate=0.0,
            min_mrr=0.0,
            require_real_corpus=True,
        )

        self.assertFalse(report["valid"])
        self.assertIn("real corpus mode requires a WARC provenance manifest", report["errors"])
        self.assertIn("real corpus mode requires at least one WARC source", report["errors"])
        self.assertIn(
            "real corpus mode requires query_count between 20 and 50, got 2",
            report["errors"],
        )
        self.assertIn(
            "real corpus mode requires min_hit_rate_at_k greater than 0",
            report["errors"],
        )
        self.assertIn(
            "real corpus mode requires min_mrr_at_k greater than 0",
            report["errors"],
        )

    def test_parse_args_and_validate_args_require_positive_thresholds(self):
        args = doctor_quality_baseline.parse_args(["--fixture-report-json", "report.json", "--quality-json", "quality.json", "--require-real-corpus"])

        self.assertIn("require-real-corpus needs min-hit-rate greater than 0", doctor_quality_baseline.validate_args(args))
        self.assertIn("require-real-corpus needs min-mrr greater than 0", doctor_quality_baseline.validate_args(args))

    def test_build_report_rejects_source_count_mismatch(self):
        fixture_report = source_coverage_fixture_report_payload()
        fixture_report["source_count"] = 3

        report = doctor_quality_baseline.build_report(
            fixture_report=fixture_report,
            quality=quality_payload(),
            fixture_report_path=Path("quality_fixture_report.json"),
            quality_path=Path("quality.json"),
            min_docs=3,
            min_queries=2,
            min_expected_labels=3,
            min_hit_rate=0.0,
            min_mrr=0.0,
        )

        self.assertFalse(report["valid"])
        self.assertIn(
            "source coverage count 2 does not match source_count 3",
            report["errors"],
        )

    def test_mismatched_counts_and_invalid_fixture_report_are_errors(self):
        fixture_report = fixture_report_payload()
        fixture_report["valid"] = False
        fixture_report["errors"] = ["duplicate query names: alpha"]
        quality = quality_payload()
        quality["query_count"] = 1

        report = doctor_quality_baseline.build_report(
            fixture_report=fixture_report,
            quality=quality,
            fixture_report_path=Path("quality_fixture_report.json"),
            quality_path=Path("quality.json"),
            min_docs=3,
            min_queries=2,
            min_expected_labels=3,
            min_hit_rate=0.0,
            min_mrr=0.0,
        )

        self.assertFalse(report["valid"])
        self.assertIn("fixture report is invalid", report["errors"])
        self.assertIn("fixture report error: duplicate query names: alpha", report["errors"])
        self.assertIn(
            "quality query_count 1 does not match fixture report 2",
            report["errors"],
        )

    def test_main_writes_outputs_and_returns_nonzero_for_failed_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_report = tmp_path / "quality_fixture_report.json"
            quality = tmp_path / "quality.json"
            json_output = tmp_path / "quality_baseline_doctor.json"
            markdown_output = tmp_path / "quality_baseline_doctor.md"
            write_json(fixture_report, fixture_report_payload())
            write_json(quality, quality_payload())

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = doctor_quality_baseline.main(
                    [
                        "--fixture-report-json",
                        str(fixture_report),
                        "--quality-json",
                        str(quality),
                        "--min-hit-rate",
                        "0.75",
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
        self.assertIn("# pgwarc_lance Quality Baseline Doctor", markdown)
        self.assertIn("hit_rate_at_k 0.500000 is below minimum 0.750000", markdown)


class NonSuccessQualityBaselineTests(unittest.TestCase):
    def build(self, quality: dict, tmp_path: Path) -> dict:
        return doctor_quality_baseline.build_report(
            fixture_report=fixture_report_payload(),
            quality=quality,
            fixture_report_path=tmp_path / "quality_fixture_report.json",
            quality_path=tmp_path / "quality.json",
            min_docs=3,
            min_queries=2,
            min_expected_labels=3,
            min_hit_rate=0.5,
            min_mrr=0.25,
        )

    def test_producer_success_artifact_still_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            quality, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "success"
            )

        self.assertEqual(exit_code, 0)
        fixture_report = {
            "schema_version": 1,
            "fixture": "producer-fixture",
            "valid": True,
            "errors": [],
            "warnings": [],
            "doc_count": 1,
            "query_count": 2,
            "expected_label_count": 2,
            "queries": [
                {"name": "q0", "expected_count": 1},
                {"name": "q1", "expected_count": 1},
            ],
        }
        report = doctor_quality_baseline.build_report(
            fixture_report=fixture_report,
            quality=quality,
            fixture_report_path=Path("quality_fixture_report.json"),
            quality_path=Path("quality.json"),
            min_docs=1,
            min_queries=2,
            min_expected_labels=2,
            min_hit_rate=1.0,
            min_mrr=1.0,
        )
        self.assertTrue(report["valid"])
        self.assertEqual(report["hit_rate_at_k"], 1.0)
        self.assertNotIn("quality_status", report)

    def test_setup_timeout_artifact_is_not_a_success_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            quality, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "setup_timeout"
            )
            report = self.build(quality, tmp_path)

        self.assertEqual(exit_code, 4)
        self.assertFalse(report["valid"])
        self.assertEqual(report["quality_status"], "quality_eval_timeout")
        self.assertIs(report["quality_success"], False)
        self.assertIsNone(report["hit_rate_at_k"])
        self.assertIsNone(report["mrr_at_k"])
        self.assertFalse(report["metrics_available"])
        self.assertEqual(report["quality_diagnostics"]["completed_results"], 0)
        self.assertEqual(report["quality_diagnostics"]["timeout"]["stage"], "setup")
        self.assertTrue(
            any(
                "non-success status 'quality_eval_timeout'" in error
                for error in report["errors"]
            )
        )
        self.assertTrue(
            any("client-side subprocess limit only" in error for error in report["errors"])
        )
        self.assertFalse(any("below minimum" in error for error in report["errors"]))

    def test_query_timeout_artifact_preserves_partial_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            quality, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "query_timeout"
            )
            report = self.build(quality, tmp_path)

        self.assertEqual(exit_code, 4)
        self.assertFalse(report["valid"])
        self.assertEqual(report["quality_status"], "quality_eval_timeout")
        self.assertEqual(report["quality_diagnostics"]["completed_results"], 1)
        timeout = report["quality_diagnostics"]["timeout"]
        self.assertEqual(timeout["stage"], "query")
        self.assertEqual(timeout["completed_results"], 1)
        self.assertEqual(timeout["partial_result_count"], 1)
        self.assertTrue(timeout["client_side_only"])
        self.assertIsNone(report["hit_rate_at_k"])
        self.assertTrue(any("stage 'query'" in error for error in report["errors"]))
        self.assertTrue(
            any("preserved 1 partial result" in error for error in report["errors"])
        )

    def test_server_statement_timeout_artifact_is_not_a_success_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            quality, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "server_statement_timeout"
            )
            report = self.build(quality, tmp_path)

        self.assertEqual(exit_code, 4)
        self.assertFalse(report["valid"])
        self.assertEqual(report["quality_status"], "quality_eval_timeout")
        self.assertIs(report["quality_success"], False)
        self.assertIsNone(report["hit_rate_at_k"])
        timeout = report["quality_diagnostics"]["timeout"]
        self.assertEqual(timeout["stage"], "query")
        self.assertFalse(timeout["client_side_only"])
        self.assertEqual(timeout["timeout_source"], "server_statement")
        self.assertEqual(timeout["effective_statement_timeout_ms"], 5000)
        self.assertEqual(timeout["sqlstate"], "57014")
        self.assertTrue(
            any(
                "non-success status 'quality_eval_timeout'" in error
                for error in report["errors"]
            )
        )

    def test_server_and_client_timeout_observations_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            server, _, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "server_statement_timeout"
            )
            client, _, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "query_timeout"
            )
            server_report = self.build(server, tmp_path)
            client_report = self.build(client, tmp_path)

        server_observation = server_report["quality_diagnostics"]["timeout"][
            "timeout_observation"
        ]
        self.assertEqual(
            "server_statement_timeout_reported",
            server_observation["statement_outcome"],
        )
        self.assertEqual("unobserved", server_observation["backend_state_after_timeout"])
        self.assertEqual("unverified", server_observation["cancellation_completion"])
        self.assertEqual("unverified", server_observation["transaction_cleanup"])

        client_observation = client_report["quality_diagnostics"]["timeout"][
            "timeout_observation"
        ]
        self.assertEqual("unknown", client_observation["statement_outcome"])

        server_markdown = doctor_quality_baseline.render_markdown(server_report)
        client_markdown = doctor_quality_baseline.render_markdown(client_report)
        self.assertIn(
            "timeout observation statement_outcome: "
            "`server_statement_timeout_reported`",
            server_markdown,
        )
        self.assertIn(
            "timeout observation backend_state_after_timeout: `unobserved`",
            server_markdown,
        )
        self.assertIn(
            "timeout observation cancellation_completion: `unverified`",
            server_markdown,
        )
        self.assertIn(
            "timeout observation transaction_cleanup: `unverified`",
            server_markdown,
        )
        self.assertIn(
            "timeout observation statement_outcome: `unknown`", client_markdown
        )
        self.assertIsNone(server_report["hit_rate_at_k"])
        self.assertFalse(server_report["valid"])

    def test_legacy_timeout_marks_observation_unrecorded(self):
        quality = {
            "benchmark": "retrieval_quality_smoke",
            "fixture": "producer-fixture",
            "doc_count": 1,
            "query_count": 2,
            "vector_dim": 1,
            "k": 3,
            "lance_uri": "/tmp/x.lance",
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "query",
                "reason": "query SQL exceeded the effective client-side timeout",
                "limit_seconds": 5.0,
                "completed_results": 1,
                "partial_result_preserved": True,
                "partial_result_count": 1,
                "client_side_only": True,
            },
            "results": [{"name": "q0", "hit": True, "reciprocal_rank": 1.0}],
        }
        report = self.build(quality, Path("."))

        self.assertFalse(report["valid"])
        self.assertNotIn("timeout_observation", report["quality_diagnostics"]["timeout"])
        markdown = doctor_quality_baseline.render_markdown(report)
        self.assertIn(
            "timeout observation: `not recorded "
            "(backend state and cancellation unverified)`",
            markdown,
        )
        self.assertIsNone(report["hit_rate_at_k"])

    def test_unsupported_timeout_claims_stay_non_success_without_metrics(self):
        quality = {
            "benchmark": "retrieval_quality_smoke",
            "fixture": "producer-fixture",
            "doc_count": 1,
            "query_count": 2,
            "vector_dim": 1,
            "k": 3,
            "lance_uri": "/tmp/x.lance",
            "status": "quality_eval_timeout",
            "success": False,
            "timeout": {
                "stage": "query",
                "reason": "client subprocess timeout",
                "limit_seconds": 5.0,
                "completed_results": 1,
                "partial_result_preserved": True,
                "partial_result_count": 1,
                "client_side_only": True,
                "timeout_observation": {
                    "statement_outcome": "unknown",
                    "backend_state_after_timeout": "unobserved",
                    "cancellation_completion": "unverified",
                    "transaction_cleanup": "unverified",
                    "backend_pid": 4242,
                    "cancelled": True,
                    "rollback_completed": True,
                },
            },
            "results": [{"name": "q0", "hit": True, "reciprocal_rank": 1.0}],
        }
        report = self.build(quality, Path("."))

        self.assertFalse(report["valid"])
        self.assertIsNone(report["hit_rate_at_k"])
        self.assertIsNone(report["mrr_at_k"])
        self.assertFalse(report["metrics_available"])
        self.assertEqual([], report["results"])
        diagnostics = report["quality_diagnostics"]["timeout"]["timeout_observation"]
        self.assertEqual(4242, diagnostics["backend_pid"])
        self.assertTrue(diagnostics["cancelled"])
        self.assertTrue(diagnostics["rollback_completed"])
        markdown = doctor_quality_baseline.render_markdown(report)
        self.assertIn("timeout observation statement_outcome: `unknown`", markdown)
        self.assertTrue(
            any(
                "non-success status 'quality_eval_timeout'" in error
                for error in report["errors"]
            )
        )

    def test_budget_exhausted_artifact_preserves_budget_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            quality, exit_code, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "budget_exhausted"
            )
            report = self.build(quality, tmp_path)

        self.assertEqual(exit_code, 3)
        self.assertFalse(report["valid"])
        self.assertEqual(report["quality_status"], "budget_exhausted")
        budget = report["quality_diagnostics"]["budget"]
        self.assertEqual(budget["limit_kind"], "max_queries")
        self.assertEqual(budget["used"], 1)
        self.assertEqual(budget["limit"], 1)
        self.assertEqual(report["quality_diagnostics"]["completed_results"], 1)
        self.assertTrue(
            any("exhausted the run budget" in error for error in report["errors"])
        )
        self.assertIsNone(report["hit_rate_at_k"])

    def test_contradictory_and_unknown_status_are_structured_errors(self):
        success_quality = quality_payload()
        cases = (
            (
                {**success_quality, "status": "quality_eval_timeout", "success": True},
                "contradicts success=true",
            ),
            (
                {**success_quality, "status": "mystery_status", "success": False},
                "unknown status 'mystery_status'",
            ),
            ({**success_quality, "success": False}, "success=false without a status"),
        )
        for quality, needle in cases:
            with self.subTest(needle=needle):
                report = self.build(quality, Path("."))
                self.assertFalse(report["valid"])
                self.assertTrue(
                    any(needle in error for error in report["errors"]),
                    report["errors"],
                )
                self.assertIsNone(report["hit_rate_at_k"])

    def test_main_non_success_artifact_exits_nonzero_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            quality, _, _ = quality_artifact_helpers.produce_artifact(
                tmp_path, "setup_timeout"
            )
            fixture_report = tmp_path / "quality_fixture_report.json"
            quality_path = tmp_path / "quality.json"
            json_output = tmp_path / "quality_baseline_doctor.json"
            markdown_output = tmp_path / "quality_baseline_doctor.md"
            write_json(fixture_report, fixture_report_payload())
            write_json(quality_path, quality)
            stderr = io.StringIO()

            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                exit_code = doctor_quality_baseline.main(
                    [
                        "--fixture-report-json",
                        str(fixture_report),
                        "--quality-json",
                        str(quality_path),
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
        self.assertIsNone(report["hit_rate_at_k"])
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertIn("## Quality Artifact Status", markdown)
        self.assertIn("quality_eval_timeout", markdown)
        self.assertIn("setup", markdown)


if __name__ == "__main__":
    unittest.main()
