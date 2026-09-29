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
import compare_benchmark_runs


def benchmark_payload(
    values: dict[int, dict[str, float]],
    *,
    batch_size: int = 500,
    execute: bool = True,
) -> dict:
    rows = sorted(values)
    results = []
    for row in rows:
        modes = []
        for mode in compare_benchmark_runs.MODE_NAMES:
            execute_s = values[row][mode]
            modes.append(
                {
                    "bm25_mode": mode,
                    "sql_bytes": 100,
                    "sql_s": 0.1,
                    "execute_s": execute_s if execute else None,
                }
            )
        results.append({"rows": row, "modes": modes})
    return {
        "benchmark": compare_benchmark_runs.BENCHMARK_NAME,
        "body_words": 80,
        "vector_dim": 64,
        "batch_size": batch_size,
        "execute": execute,
        "include_hash_vectors": True,
        "reset_between_modes": True,
        "rows": rows,
        "results": results,
    }


class CompareBenchmarkRunsTests(unittest.TestCase):
    def write_payload(self, directory: Path, name: str, payload: dict) -> Path:
        path = directory / name
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path

    def test_passing_comparison_produces_six_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            baseline = self.write_payload(
                directory,
                "baseline.json",
                benchmark_payload(
                    {
                        1000: {"function": 5.0, "bulk": 2.0, "copy": 1.0},
                        2000: {"function": 10.0, "bulk": 4.0, "copy": 2.0},
                    }
                ),
            )
            candidate = self.write_payload(
                directory,
                "candidate.json",
                benchmark_payload(
                    {
                        1000: {"function": 5.2, "bulk": 1.9, "copy": 1.0},
                        2000: {"function": 10.5, "bulk": 4.2, "copy": 2.1},
                    }
                ),
            )

            report = compare_benchmark_runs.build_report(baseline, candidate, 0.10)

        self.assertTrue(report["valid"])
        self.assertTrue(report["passed"])
        self.assertEqual(report["comparison_count"], 6)
        self.assertEqual(report["regression_count"], 0)
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(
            report["input_provenance"]["baseline"]["path"],
            str(baseline),
        )
        self.assertEqual(
            report["input_provenance"]["candidate"]["path"],
            str(candidate),
        )
        self.assertEqual(len(report["input_provenance"]["baseline"]["sha256"]), 64)
        self.assertEqual(compare_benchmark_runs.validate_report(report), [])

    def test_regression_fails_and_main_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            baseline = self.write_payload(
                directory,
                "baseline.json",
                benchmark_payload(
                    {100: {"function": 1.0, "bulk": 0.8, "copy": 0.7}}
                ),
            )
            candidate = self.write_payload(
                directory,
                "candidate.json",
                benchmark_payload(
                    {100: {"function": 1.5, "bulk": 0.8, "copy": 0.7}}
                ),
            )
            json_output = directory / "comparison.json"
            markdown_output = directory / "comparison.md"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = compare_benchmark_runs.main(
                    [
                        "--baseline-json",
                        str(baseline),
                        "--candidate-json",
                        str(candidate),
                        "--max-regression-ratio",
                        "0.10",
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ]
                )

            report = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = markdown_output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["regression_count"], 1)
        self.assertIn("exceeds max", report["errors"][0])
        self.assertIn("## Errors", markdown)
        self.assertIn("function", markdown)
        self.assertEqual(compare_benchmark_runs.validate_report(report), [])

    def test_report_validation_detects_tampered_regression_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            payload = benchmark_payload(
                {100: {"function": 1.0, "bulk": 0.8, "copy": 0.7}}
            )
            baseline = self.write_payload(directory, "baseline.json", payload)
            candidate = self.write_payload(directory, "candidate.json", payload)
            report = compare_benchmark_runs.build_report(baseline, candidate, 0.10)
            report["comparisons"][0]["regressed"] = True

        errors = compare_benchmark_runs.validate_report(report)
        self.assertTrue(any("does not match the configured threshold" in error for error in errors))

    def test_report_validation_detects_input_hash_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            payload = benchmark_payload(
                {100: {"function": 1.0, "bulk": 0.8, "copy": 0.7}}
            )
            baseline = self.write_payload(directory, "baseline.json", payload)
            candidate = self.write_payload(directory, "candidate.json", payload)
            report = compare_benchmark_runs.build_report(baseline, candidate, 0.10)
            candidate.write_text(candidate.read_text(encoding="utf-8") + "\n", encoding="utf-8")

            errors = compare_benchmark_runs.validate_report(
                report,
                verify_input_paths=True,
            )

        self.assertTrue(any("candidate.sha256 drift" in error for error in errors))

    def test_configuration_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            baseline = self.write_payload(
                directory,
                "baseline.json",
                benchmark_payload(
                    {100: {"function": 1.0, "bulk": 0.8, "copy": 0.7}}
                ),
            )
            candidate = self.write_payload(
                directory,
                "candidate.json",
                benchmark_payload(
                    {100: {"function": 1.0, "bulk": 0.8, "copy": 0.7}},
                    batch_size=100,
                ),
            )

            report = compare_benchmark_runs.build_report(baseline, candidate, 0.10)

        self.assertFalse(report["valid"])
        self.assertFalse(report["passed"])
        self.assertTrue(
            any("configuration mismatch for batch_size" in error for error in report["errors"])
        )
        self.assertEqual(report["comparison_count"], 0)

    def test_rows_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            baseline = self.write_payload(
                directory,
                "baseline.json",
                benchmark_payload(
                    {100: {"function": 1.0, "bulk": 0.8, "copy": 0.7}}
                ),
            )
            candidate = self.write_payload(
                directory,
                "candidate.json",
                benchmark_payload(
                    {200: {"function": 1.0, "bulk": 0.8, "copy": 0.7}}
                ),
            )

            report = compare_benchmark_runs.build_report(baseline, candidate, 0.10)

        self.assertFalse(report["valid"])
        self.assertTrue(any("configuration mismatch for rows" in error for error in report["errors"]))
        self.assertEqual(report["comparison_count"], 0)

    def test_unexecuted_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            payload = benchmark_payload(
                {100: {"function": 1.0, "bulk": 0.8, "copy": 0.7}},
                execute=False,
            )
            baseline = self.write_payload(directory, "baseline.json", payload)
            candidate = self.write_payload(directory, "candidate.json", payload)

            report = compare_benchmark_runs.build_report(baseline, candidate, 0.10)

        self.assertFalse(report["valid"])
        self.assertTrue(any("execute must be true" in error for error in report["errors"]))

    def test_negative_threshold_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            payload = benchmark_payload(
                {100: {"function": 1.0, "bulk": 0.8, "copy": 0.7}}
            )
            baseline = self.write_payload(directory, "baseline.json", payload)
            candidate = self.write_payload(directory, "candidate.json", payload)

            report = compare_benchmark_runs.build_report(baseline, candidate, -0.1)

        self.assertFalse(report["valid"])
        self.assertIn("max_regression_ratio", report["errors"][0])


if __name__ == "__main__":
    unittest.main()
