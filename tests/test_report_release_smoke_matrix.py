import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
TOOL_PATH = TOOLS / "report_release_smoke_matrix.py"
SPEC = importlib.util.spec_from_file_location("report_release_smoke_matrix", TOOL_PATH)
report_release_smoke_matrix = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(report_release_smoke_matrix)


def completed(command: list[str], returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class ReleaseSmokeMatrixReportTests(unittest.TestCase):
    def test_parse_pg_majors_accepts_space_and_comma_separated_values(self):
        self.assertEqual(
            report_release_smoke_matrix.parse_pg_majors("13,14 15"),
            [13, 14, 15],
        )

    def test_build_report_continues_after_failed_major(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            if "PG_MAJOR=16" in command:
                return completed(command, 0, stdout="pg16 ok\n")
            return completed(command, 2, stdout="line1\nline2\n", stderr="bad\nworse\n")

        report = report_release_smoke_matrix.build_report(
            pg_majors=[16, 17],
            make_program="make",
            make_target="smoke-release-artifacts",
            make_vars=[],
            cwd=Path("/tmp"),
            output_tail_lines=1,
            runner=runner,
        )

        self.assertFalse(report["all_passed"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(report["results"][0]["passed"])
        self.assertFalse(report["results"][1]["passed"])
        self.assertEqual(report["results"][1]["stdout_tail"], "line2")
        self.assertEqual(report["results"][1]["stderr_tail"], "worse")

    def test_report_redacts_sensitive_make_vars_in_command(self):
        def runner(command, **kwargs):
            return completed(command, 0, stdout="ok\n")

        report = report_release_smoke_matrix.build_report(
            pg_majors=[16],
            make_program="make",
            make_target="smoke-release-upgrade",
            make_vars=["UPGRADE_OLD_VERSION=0.0.9", "PG_MATRIX_PASSWORD=secret"],
            cwd=Path("/tmp"),
            output_tail_lines=5,
            runner=runner,
        )

        command = report["results"][0]["command"]
        self.assertIn("PG_MATRIX_PASSWORD=<redacted>", command)
        self.assertNotIn("secret", command)
        self.assertEqual(
            report["make_vars"],
            ["UPGRADE_OLD_VERSION=0.0.9", "PG_MATRIX_PASSWORD=<redacted>"],
        )

    def test_main_writes_outputs_and_returns_nonzero_for_failed_major(self):
        def runner(command, **kwargs):
            return completed(command, 1, stderr="install failed\n")

        with tempfile.TemporaryDirectory() as tmp:
            json_output = Path(tmp) / "reports" / "smoke.json"
            markdown_output = Path(tmp) / "reports" / "smoke.md"

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = report_release_smoke_matrix.main(
                    [
                        "--pg-majors",
                        "16",
                        "--make-target",
                        "smoke-release-artifacts",
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ],
                    runner=runner,
                )

            data = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = markdown_output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertFalse(data["all_passed"])
        self.assertIn("# pgwarc_lance Release Smoke Matrix", markdown)
        self.assertIn("install failed", markdown)


if __name__ == "__main__":
    unittest.main()
