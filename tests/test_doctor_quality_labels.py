import importlib.util
import io
import json
import math
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
TOOL_PATH = TOOLS / "doctor_quality_labels.py"
SPEC = importlib.util.spec_from_file_location("doctor_quality_labels", TOOL_PATH)
doctor_quality_labels = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(doctor_quality_labels)


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def query_payload(**overrides):
    query = {
        "name": "alpha",
        "query": "alpha",
        "vector": [1.0, 0.0],
        "expected_doc_ids": [1],
    }
    query.update(overrides)
    return query


class QualityLabelDoctorTests(unittest.TestCase):
    def test_valid_labels_with_records_report_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            queries = tmp_path / "queries.json"
            records = tmp_path / "records.jsonl"
            write_json(queries, {"name": "labels", "queries": [query_payload()]})
            write_jsonl(
                records,
                [
                    {"doc_id": 1, "text": "alpha"},
                    {"doc_id": 2, "text": "control"},
                ],
            )

            report = doctor_quality_labels.build_report(
                queries_json=queries,
                records_jsonl=records,
                min_queries=1,
                min_expected_labels=1,
            )

        self.assertTrue(report["valid"])
        self.assertEqual(report["query_set"], "labels")
        self.assertEqual(report["query_count"], 1)
        self.assertEqual(report["record_count"], 2)
        self.assertEqual(report["labeled_doc_count"], 1)
        self.assertEqual(report["unlabeled_doc_count"], 1)
        self.assertEqual(report["vector_dim"], 2)

    def test_doc_id_domain_rejects_invalid_record_ids(self):
        for raw_id in (0, -1, True, "1", 2**63):
            with self.subTest(raw_id=raw_id), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                queries = tmp_path / "queries.json"
                records = tmp_path / "records.jsonl"
                write_json(queries, {"queries": [query_payload()]})
                write_jsonl(records, [{"doc_id": raw_id, "text": "invalid"}])

                report = doctor_quality_labels.build_report(
                    queries_json=queries,
                    records_jsonl=records,
                    min_queries=1,
                    min_expected_labels=1,
                )

            self.assertFalse(report["valid"])
            self.assertTrue(
                any("doc_id must be a positive integer" in error for error in report["errors"])
            )

    def test_unknown_expected_doc_id_is_error_when_records_are_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            queries = tmp_path / "queries.json"
            records = tmp_path / "records.jsonl"
            write_json(queries, {"queries": [query_payload(expected_doc_ids=[3])]})
            write_jsonl(records, [{"doc_id": 1, "text": "alpha"}])

            report = doctor_quality_labels.build_report(
                queries_json=queries,
                records_jsonl=records,
                min_queries=1,
                min_expected_labels=1,
            )

        self.assertFalse(report["valid"])
        self.assertIn(
            "queries[1].expected_doc_ids contains unknown doc_id values: 3",
            report["errors"],
        )

    def test_duplicate_query_name_and_vector_dim_mismatch_are_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            queries = tmp_path / "queries.json"
            write_json(
                queries,
                {
                    "queries": [
                        query_payload(name="same", vector=[1.0, 0.0]),
                        query_payload(name="same", vector=[1.0, 0.0, 0.0], expected_doc_ids=[2]),
                    ]
                },
            )

            report = doctor_quality_labels.build_report(
                queries_json=queries,
                records_jsonl=None,
                min_queries=1,
                min_expected_labels=1,
            )

        self.assertFalse(report["valid"])
        self.assertIn("duplicate query names: same", report["errors"])
        self.assertIn("queries[2].vector has dim 3, expected 2", report["errors"])
        self.assertTrue(any("not checked against corpus records" in warning for warning in report["warnings"]))

    def test_non_finite_or_out_of_range_vectors_are_errors(self):
        for value in (math.nan, math.inf, -math.inf, 10**400):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as tmp:
                    queries = Path(tmp) / "queries.json"
                    write_json(queries, {"queries": [query_payload(vector=[value])]})

                    report = doctor_quality_labels.build_report(
                        queries_json=queries,
                        records_jsonl=None,
                        min_queries=1,
                        min_expected_labels=1,
                    )

                self.assertFalse(report["valid"])
                self.assertIn(
                    "queries[1].vector must be a non-empty numeric list",
                    report["errors"],
                )

    def test_minimum_label_count_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            queries = Path(tmp) / "queries.json"
            write_json(queries, {"queries": [query_payload()]})

            report = doctor_quality_labels.build_report(
                queries_json=queries,
                records_jsonl=None,
                min_queries=2,
                min_expected_labels=2,
            )

        self.assertFalse(report["valid"])
        self.assertIn("query_count 1 is below minimum 2", report["errors"])
        self.assertIn("expected_label_count 1 is below minimum 2", report["errors"])

    def test_main_writes_outputs_and_returns_nonzero_for_invalid_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            queries = tmp_path / "queries.json"
            json_output = tmp_path / "reports" / "labels.json"
            markdown_output = tmp_path / "reports" / "labels.md"
            write_json(queries, {"queries": [query_payload(vector=["bad"])]})

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = doctor_quality_labels.main(
                    [
                        "--queries-json",
                        str(queries),
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
        self.assertIn("queries[1].vector must be a non-empty numeric list", report["errors"])
        self.assertIn("# pgwarc_lance Quality Label Doctor", markdown)


if __name__ == "__main__":
    unittest.main()
