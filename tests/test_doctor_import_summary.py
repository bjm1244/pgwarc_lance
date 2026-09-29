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
TOOL_PATH = TOOLS / "doctor_import_summary.py"
SPEC = importlib.util.spec_from_file_location("doctor_import_summary", TOOL_PATH)
doctor_import_summary = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(doctor_import_summary)


def valid_summary(**overrides):
    summary = {
        "records": 2,
        "sql_bytes": 4096,
        "vectors": "hash",
        "lance_mode": "overwrite",
        "lance_append_duplicates": "none",
        "bm25_mode": "copy",
        "execute": True,
        "psql": "psql -v ON_ERROR_STOP=1",
    }
    summary.update(overrides)
    return summary


def write_summary(path: Path, **overrides) -> None:
    path.write_text(json.dumps(valid_summary(**overrides)) + "\n", encoding="utf-8")


class ImportSummaryDoctorTests(unittest.TestCase):
    def test_valid_summary_has_no_errors_or_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            write_summary(path)

            report = doctor_import_summary.build_report([path])

        self.assertTrue(report["all_valid"])
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["warning_count"], 0)

    def test_append_duplicate_risk_is_warning_not_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            write_summary(
                path,
                lance_mode="append",
                lance_append_duplicates="possible",
                execute=False,
            )

            report = doctor_import_summary.build_report([path])

        self.assertTrue(report["all_valid"])
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["warning_count"], 2)
        warnings = report["summaries"][0]["warnings"]
        self.assertTrue(any("duplicate" in warning for warning in warnings))
        self.assertTrue(any("execute=false" in warning for warning in warnings))

    def test_missing_required_field_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            summary = valid_summary()
            del summary["bm25_mode"]
            path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            report = doctor_import_summary.build_report([path])

        self.assertFalse(report["all_valid"])
        self.assertIn("missing required field: bm25_mode", report["summaries"][0]["errors"])

    def test_empty_sql_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            write_summary(path, sql_bytes=0)

            report = doctor_import_summary.build_report([path])

        self.assertFalse(report["all_valid"])
        self.assertIn("generated SQL is empty", report["summaries"][0]["errors"])

    def test_zero_records_is_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            write_summary(path, records=0, vectors=0, lance_mode="none")

            report = doctor_import_summary.build_report([path])

        self.assertTrue(report["all_valid"])
        self.assertEqual(report["warning_count"], 1)
        self.assertIn("zero records", report["summaries"][0]["warnings"][0])

    def test_main_writes_outputs_and_returns_nonzero_for_invalid_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "summary.json"
            json_output = Path(tmp) / "reports" / "doctor.json"
            markdown_output = Path(tmp) / "reports" / "doctor.md"
            write_summary(summary_path, lance_mode="none", vectors="hash")

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = doctor_import_summary.main(
                    [
                        "--summary-json",
                        str(summary_path),
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ]
                )

            report = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = markdown_output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["all_valid"])
        self.assertIn("# pgwarc_lance Import Summary Doctor", markdown)
        self.assertIn("lance_mode=none must not report vectors", markdown)


if __name__ == "__main__":
    unittest.main()
