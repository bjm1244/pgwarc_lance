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
TOOL_PATH = TOOLS / "report_release_upgrade_smoke_matrix.py"
SPEC = importlib.util.spec_from_file_location(
    "report_release_upgrade_smoke_matrix", TOOL_PATH
)
report_release_upgrade_smoke_matrix = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(report_release_upgrade_smoke_matrix)

import doctor_run_directory  # noqa: E402


def completed(command: list[str], returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def write_artifacts(dist_dir: Path, *, version: str) -> None:
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "pgwarc_lance.so").write_bytes(b"\x7fELFfake-shared-object")
    (dist_dir / "pgwarc_lance.control").write_text(
        "\n".join(
            [
                "comment = 'pgwarc_lance test artifact'",
                f"default_version = '{version}'",
                "module_pathname = 'pgwarc_lance'",
                "relocatable = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (dist_dir / f"pgwarc_lance--{version}.sql").write_text(
        "\n".join(
            f"-- {symbol}"
            for symbol in report_release_upgrade_smoke_matrix.check_release_artifacts.REQUIRED_SQL_SYMBOLS
        )
        + "\n",
        encoding="utf-8",
    )


def artifact_dir(dist_root: Path, *, version: str, pg_major: int) -> Path:
    return dist_root / f"pgwarc_lance-{version}-pg{pg_major}"


def ready_pair(
    dist_root: Path,
    *,
    old_version: str,
    new_version: str,
    pg_major: int,
) -> tuple[Path, Path]:
    old_dist_dir = artifact_dir(dist_root, version=old_version, pg_major=pg_major)
    new_dist_dir = artifact_dir(dist_root, version=new_version, pg_major=pg_major)
    write_artifacts(old_dist_dir, version=old_version)
    write_artifacts(new_dist_dir, version=new_version)
    (
        new_dist_dir / f"pgwarc_lance--{old_version}--{new_version}.sql"
    ).write_text("-- upgrade\n", encoding="utf-8")
    return old_dist_dir, new_dist_dir


class RecordingRunner:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        return completed(list(command), self.returncode, stdout="smoke ok\n")


class ReleaseUpgradeSmokeMatrixTests(unittest.TestCase):
    def test_parse_and_resolve_make_vars(self):
        parsed = report_release_upgrade_smoke_matrix.parse_make_vars(
            ["UPGRADE_OLD_VERSION=0.1.0", "PG_MATRIX_PASSWORD=secret"]
        )
        self.assertEqual(parsed["UPGRADE_OLD_VERSION"], "0.1.0")
        self.assertEqual(parsed["PG_MATRIX_PASSWORD"], "secret")

    def test_ready_major_dispatches_smoke_with_matching_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            old_dist_dir, new_dist_dir = ready_pair(
                dist_root,
                old_version="0.1.0",
                new_version="0.2.0",
                pg_major=16,
            )
            runner = RecordingRunner(returncode=0)

            report = report_release_upgrade_smoke_matrix.build_report(
                pg_majors=[16],
                make_program="make",
                make_target="smoke-release-upgrade",
                make_vars=["UPGRADE_OLD_VERSION=0.1.0"],
                dist_root=dist_root,
                new_version_default="0.2.0",
                current_version="0.2.0",
                cwd=Path("/tmp"),
                output_tail_lines=5,
                runner=runner,
            )

        result = report["results"][0]
        self.assertTrue(result["preflight_passed"])
        self.assertFalse(result["smoke_skipped"])
        self.assertTrue(result["passed"])
        self.assertEqual(report["blocked_majors"], [])
        self.assertTrue(report["all_passed"])
        self.assertEqual(len(runner.calls), 1)

        command = runner.calls[0]
        self.assertEqual(command[0], "make")
        self.assertIn("PG_MAJOR=16", command)
        self.assertIn("UPGRADE_OLD_VERSION=0.1.0", command)
        self.assertIn("UPGRADE_NEW_VERSION=0.2.0", command)
        self.assertIn(f"UPGRADE_OLD_DIST_DIR={old_dist_dir}", command)
        self.assertIn(f"UPGRADE_NEW_DIST_DIR={new_dist_dir}", command)
        self.assertEqual(command[-1], "smoke-release-upgrade")
        self.assertEqual(result["preflight"]["old_dist_dir"], str(old_dist_dir))
        self.assertEqual(result["preflight"]["new_dist_dir"], str(new_dist_dir))

    def test_blocked_major_skips_smoke_and_reports_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            write_artifacts(
                artifact_dir(dist_root, version="0.1.0", pg_major=16),
                version="0.1.0",
            )
            runner = RecordingRunner(returncode=0)

            report = report_release_upgrade_smoke_matrix.build_report(
                pg_majors=[16],
                make_program="make",
                make_target="smoke-release-upgrade",
                make_vars=[],
                dist_root=dist_root,
                new_version_default="0.1.0",
                current_version="0.1.0",
                cwd=Path("/tmp"),
                output_tail_lines=5,
                runner=runner,
            )

        result = report["results"][0]
        self.assertFalse(result["preflight_passed"])
        self.assertEqual(result["preflight"]["status"], "blocked")
        self.assertTrue(result["smoke_skipped"])
        self.assertFalse(result["passed"])
        self.assertIsNone(result["exit_code"])
        self.assertIn("upgrade preflight blocked", result["smoke_skipped_reason"])
        self.assertEqual(runner.calls, [])
        self.assertEqual(report["blocked_majors"], [16])
        self.assertFalse(report["all_passed"])

    def test_mixed_majors_keep_per_major_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            old_dist_dir, new_dist_dir = ready_pair(
                dist_root,
                old_version="0.1.0",
                new_version="0.2.0",
                pg_major=16,
            )
            write_artifacts(
                artifact_dir(dist_root, version="0.2.0", pg_major=17),
                version="0.2.0",
            )
            runner = RecordingRunner(returncode=0)

            report = report_release_upgrade_smoke_matrix.build_report(
                pg_majors=[16, 17],
                make_program="make",
                make_target="smoke-release-upgrade",
                make_vars=["UPGRADE_OLD_VERSION=0.1.0"],
                dist_root=dist_root,
                new_version_default="0.2.0",
                current_version="0.2.0",
                cwd=Path("/tmp"),
                output_tail_lines=5,
                runner=runner,
            )

        self.assertEqual([result["pg_major"] for result in report["results"]], [16, 17])
        self.assertTrue(report["results"][0]["preflight_passed"])
        self.assertFalse(report["results"][1]["preflight_passed"])
        self.assertEqual(report["blocked_majors"], [17])
        self.assertFalse(report["all_passed"])
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("PG_MAJOR=16", runner.calls[0])
        self.assertEqual(report["results"][0]["preflight"]["old_dist_dir"], str(old_dist_dir))
        self.assertEqual(report["results"][0]["preflight"]["new_dist_dir"], str(new_dist_dir))

    def test_report_preserves_release_smoke_matrix_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            ready_pair(
                dist_root,
                old_version="0.1.0",
                new_version="0.2.0",
                pg_major=16,
            )
            ready_report = report_release_upgrade_smoke_matrix.build_report(
                pg_majors=[16],
                make_program="make",
                make_target="smoke-release-upgrade",
                make_vars=["UPGRADE_OLD_VERSION=0.1.0"],
                dist_root=dist_root,
                new_version_default="0.2.0",
                current_version="0.2.0",
                cwd=Path("/tmp"),
                output_tail_lines=5,
                runner=RecordingRunner(returncode=0),
            )

        ready_errors = doctor_run_directory.validate_release_smoke_matrix_artifact(
            "release_smoke_matrix.json", ready_report
        )
        self.assertEqual(ready_errors, [])

    def test_blocked_report_preserves_release_smoke_matrix_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            write_artifacts(
                artifact_dir(dist_root, version="0.1.0", pg_major=16),
                version="0.1.0",
            )
            blocked_report = report_release_upgrade_smoke_matrix.build_report(
                pg_majors=[16],
                make_program="make",
                make_target="smoke-release-upgrade",
                make_vars=[],
                dist_root=dist_root,
                new_version_default="0.1.0",
                current_version="0.1.0",
                cwd=Path("/tmp"),
                output_tail_lines=5,
                runner=RecordingRunner(returncode=0),
            )

        blocked_errors = doctor_run_directory.validate_release_smoke_matrix_artifact(
            "release_smoke_matrix.json", blocked_report
        )
        self.assertEqual(blocked_errors, [])

    def test_main_writes_outputs_and_returns_nonzero_when_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            write_artifacts(
                artifact_dir(dist_root, version="0.1.0", pg_major=16),
                version="0.1.0",
            )
            json_output = Path(tmp) / "reports" / "smoke.json"
            markdown_output = Path(tmp) / "reports" / "smoke.md"

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = report_release_upgrade_smoke_matrix.main(
                    [
                        "--pg-majors",
                        "16",
                        "--dist-root",
                        str(dist_root),
                        "--new-version",
                        "0.1.0",
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ],
                    runner=RecordingRunner(returncode=0),
                )

            data = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = markdown_output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertFalse(data["all_passed"])
        self.assertEqual(data["blocked_majors"], [16])
        self.assertIn("# pgwarc_lance Release Upgrade Smoke Matrix", markdown)
        self.assertIn("## Blocked Preflight", markdown)

    def test_main_returns_zero_when_ready_and_smoke_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            ready_pair(
                dist_root,
                old_version="0.1.0",
                new_version="0.2.0",
                pg_major=16,
            )

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = report_release_upgrade_smoke_matrix.main(
                    [
                        "--pg-majors",
                        "16",
                        "--dist-root",
                        str(dist_root),
                        "--new-version",
                        "0.2.0",
                        "--make-var",
                        "UPGRADE_OLD_VERSION=0.1.0",
                    ],
                    runner=RecordingRunner(returncode=0),
                )

        self.assertEqual(exit_code, 0)

    def test_main_returns_nonzero_when_ready_smoke_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            ready_pair(
                dist_root,
                old_version="0.1.0",
                new_version="0.2.0",
                pg_major=16,
            )
            runner = RecordingRunner(returncode=1)

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = report_release_upgrade_smoke_matrix.main(
                    [
                        "--pg-majors",
                        "16",
                        "--dist-root",
                        str(dist_root),
                        "--new-version",
                        "0.2.0",
                        "--make-var",
                        "UPGRADE_OLD_VERSION=0.1.0",
                    ],
                    runner=runner,
                )

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
