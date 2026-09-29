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
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
TOOL_PATH = TOOLS / "report_quality_fixture.py"
SPEC = importlib.util.spec_from_file_location("report_quality_fixture", TOOL_PATH)
report_quality_fixture = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(report_quality_fixture)


def fixture_payload() -> dict:
    return {
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
    }


def provenance_payload() -> dict:
    return {
        "schema_version": 2,
        "source_spec": {"count": 2},
        "sources": [
            {
                "kind": "local",
                "path": "first.warc",
                "records": {
                    "warc_record_count": 2,
                    "imported_record_count": 1,
                    "skipped_record_count": 1,
                },
                "coverage": {
                    "doc_count": 1,
                    "labeled_doc_count": 1,
                    "unlabeled_doc_count": 0,
                },
            },
            {
                "kind": "download",
                "url": "file:///second.warc",
                "records": {
                    "warc_record_count": 1,
                    "imported_record_count": 1,
                    "skipped_record_count": 0,
                },
                "coverage": {
                    "doc_count": 1,
                    "labeled_doc_count": 0,
                    "unlabeled_doc_count": 1,
                },
            },
        ],
    }


class QualityFixtureReportTests(unittest.TestCase):
    def test_build_report_records_fixture_shape_and_label_coverage(self):
        fixture = report_quality_fixture.eval_quality.fixture_from_dict(
            fixture_payload(),
            "fixture",
        )

        report = report_quality_fixture.build_report(
            fixture=fixture,
            source="fixture",
            min_docs=1,
            min_queries=1,
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["fixture"], "tiny-quality")
        self.assertEqual(report["doc_count"], 2)
        self.assertEqual(report["query_count"], 1)
        self.assertEqual(report["expected_label_count"], 1)
        self.assertEqual(report["labeled_doc_count"], 1)
        self.assertEqual(report["unlabeled_doc_count"], 1)
        self.assertEqual(report["queries"][0]["expected_count"], 1)

    def test_min_queries_is_an_error(self):
        fixture = report_quality_fixture.eval_quality.fixture_from_dict(
            fixture_payload(),
            "fixture",
        )

        report = report_quality_fixture.build_report(
            fixture=fixture,
            source="fixture",
            min_docs=1,
            min_queries=2,
        )

        self.assertFalse(report["valid"])
        self.assertIn("query_count 1 is below minimum 2", report["errors"])

    def test_duplicate_doc_id_is_an_error(self):
        payload = fixture_payload()
        payload["docs"].append({"doc_id": 1, "text": "duplicate", "vector": [1.0, 0.0]})
        fixture = report_quality_fixture.eval_quality.fixture_from_dict(payload, "fixture")

        report = report_quality_fixture.build_report(
            fixture=fixture,
            source="fixture",
            min_docs=1,
            min_queries=1,
        )

        self.assertFalse(report["valid"])
        self.assertIn("duplicate doc_id values: 1", report["errors"])

    def test_build_report_includes_source_coverage_and_thresholds(self):
        fixture = report_quality_fixture.eval_quality.fixture_from_dict(
            fixture_payload(),
            "fixture",
        )

        report = report_quality_fixture.build_report(
            fixture=fixture,
            source="fixture",
            min_docs=1,
            min_queries=1,
            provenance=provenance_payload(),
            provenance_source="manifest.json",
            min_source_docs=1,
            max_source_skipped_ratio=0.5,
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["provenance_schema_version"], 2)
        self.assertEqual(report["source_count"], 2)
        self.assertEqual(report["source_coverage"][0]["skipped_ratio"], 0.5)
        self.assertIn("| Source | Raw | Imported |", report_quality_fixture.render_markdown(report))

    def test_source_skipped_ratio_threshold_is_an_error(self):
        fixture = report_quality_fixture.eval_quality.fixture_from_dict(
            fixture_payload(),
            "fixture",
        )

        report = report_quality_fixture.build_report(
            fixture=fixture,
            source="fixture",
            min_docs=1,
            min_queries=1,
            provenance=provenance_payload(),
            provenance_source="manifest.json",
            max_source_skipped_ratio=0.25,
        )

        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "skipped ratio 0.500000 is above maximum 0.250000" in error
                for error in report["errors"]
            )
        )

    def test_main_accepts_provenance_threshold_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_path = tmp_path / "fixture.json"
            provenance_path = tmp_path / "manifest.json"
            json_output = tmp_path / "report.json"
            markdown_output = tmp_path / "report.md"
            fixture_path.write_text(json.dumps(fixture_payload()), encoding="utf-8")
            provenance_path.write_text(json.dumps(provenance_payload()), encoding="utf-8")

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = report_quality_fixture.main(
                    [
                        "--fixture-json",
                        str(fixture_path),
                        "--provenance-json",
                        str(provenance_path),
                        "--min-source-docs",
                        "1",
                        "--max-source-skipped-ratio",
                        "0.5",
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ]
                )

            report = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = markdown_output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["source_count"], 2)
        self.assertIn("first.warc", markdown)

    def test_main_writes_outputs_and_returns_nonzero_for_invalid_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = Path(tmp) / "fixture.json"
            fixture_path.write_text(
                json.dumps({"name": "bad", "vector_dim": 0, "docs": [], "queries": []}),
                encoding="utf-8",
            )
            json_output = Path(tmp) / "reports" / "quality_fixture_report.json"
            markdown_output = Path(tmp) / "reports" / "quality_fixture_report.md"

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = report_quality_fixture.main(
                    [
                        "--fixture-json",
                        str(fixture_path),
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
        self.assertIn("vector_dim must be a positive integer", report["errors"][0])
        self.assertIn("# pgwarc_lance Quality Fixture Report", markdown)


if __name__ == "__main__":
    unittest.main()
