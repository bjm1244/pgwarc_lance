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
TOOL_PATH = TOOLS / "report_run_directory.py"
SPEC = importlib.util.spec_from_file_location("report_run_directory", TOOL_PATH)
report_run_directory = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(report_run_directory)


class RunDirectoryReportTests(unittest.TestCase):
    def test_build_report_records_files_kinds_and_digests(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "quality.json").write_text('{"ok": true}\n', encoding="utf-8")
            (run_dir / "quality_fixture_report.json").write_text("{}", encoding="utf-8")
            (run_dir / "quality_baseline_doctor.json").write_text("{}", encoding="utf-8")
            (run_dir / "quality_baseline_doctor.md").write_text("# ok\n", encoding="utf-8")
            (run_dir / "quality_queries.json").write_text("{}", encoding="utf-8")
            (run_dir / "release_smoke_matrix.json").write_text("{}", encoding="utf-8")
            (run_dir / "run_metadata.json").write_text("{}", encoding="utf-8")
            nested = run_dir / "nested"
            nested.mkdir()
            (nested / "notes.txt").write_text("hello\n", encoding="utf-8")

            report = report_run_directory.build_report(run_dir)

        self.assertTrue(report["valid"])
        self.assertEqual(report["file_count"], 8)
        self.assertEqual(report["error_count"], 0)
        files = {entry["path"]: entry for entry in report["files"]}
        self.assertEqual(files["quality.json"]["kind"], "quality")
        self.assertEqual(files["quality_fixture_report.json"]["kind"], "quality")
        self.assertEqual(files["quality_baseline_doctor.json"]["kind"], "quality")
        self.assertEqual(files["quality_baseline_doctor.md"]["kind"], "quality")
        self.assertEqual(files["quality_queries.json"]["kind"], "quality")
        self.assertEqual(files["release_smoke_matrix.json"]["kind"], "packaging")
        self.assertEqual(files["run_metadata.json"]["kind"], "metadata")
        self.assertEqual(files["nested/notes.txt"]["kind"], "artifact")
        self.assertRegex(files["quality.json"]["sha256"], r"^[0-9a-f]{64}$")

    def test_output_paths_are_excluded_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "bm25.json").write_text("{}", encoding="utf-8")
            previous_manifest = run_dir / "run_manifest.json"
            previous_manifest.write_text("old\n", encoding="utf-8")

            report = report_run_directory.build_report(
                run_dir,
                exclude_paths={previous_manifest},
            )

        self.assertEqual([entry["path"] for entry in report["files"]], ["bm25.json"])

    def test_missing_run_dir_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = report_run_directory.build_report(Path(tmp) / "missing")

        self.assertFalse(report["valid"])
        self.assertEqual(report["error_count"], 1)
        self.assertIn("does not exist", report["errors"][0])

    def test_empty_run_dir_is_warning_not_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = report_run_directory.build_report(Path(tmp))

        self.assertTrue(report["valid"])
        self.assertEqual(report["warning_count"], 1)
        self.assertEqual(report["file_count"], 0)

    def test_markdown_includes_warnings_and_errors(self):
        report = {
            "run_dir": "/tmp/run",
            "valid": False,
            "errors": ["run path is not a directory"],
            "warnings": ["run directory contains no files"],
            "error_count": 1,
            "warning_count": 1,
            "file_count": 0,
            "total_bytes": 0,
            "files": [],
        }

        markdown = report_run_directory.render_markdown(report)

        self.assertIn("# pgwarc_lance Run Directory Manifest", markdown)
        self.assertIn("## Warnings", markdown)
        self.assertIn("## Errors", markdown)

    def test_main_writes_outputs_and_returns_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "import.summary.json").write_text("{}", encoding="utf-8")
            json_output = run_dir / "run_manifest.json"
            markdown_output = run_dir / "run_manifest.md"

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = report_run_directory.main(
                    [
                        "--run-dir",
                        str(run_dir),
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ]
                )

            report = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = markdown_output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["file_count"], 1)
        self.assertEqual(report["files"][0]["path"], "import.summary.json")
        self.assertIn("| `import.summary.json` | import |", markdown)


if __name__ == "__main__":
    unittest.main()
