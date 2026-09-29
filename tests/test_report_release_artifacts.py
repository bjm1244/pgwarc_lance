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
TOOL_PATH = TOOLS / "report_release_artifacts.py"
SPEC = importlib.util.spec_from_file_location("report_release_artifacts", TOOL_PATH)
report_release_artifacts = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(report_release_artifacts)


def write_artifacts(dist_dir: Path, *, version: str = "0.1.0") -> None:
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
            for symbol in report_release_artifacts.check_release_artifacts.REQUIRED_SQL_SYMBOLS
        )
        + "\n",
        encoding="utf-8",
    )


class ReleaseArtifactReportTests(unittest.TestCase):
    def test_parse_pg_majors_accepts_space_and_comma_separated_values(self):
        self.assertEqual(
            report_release_artifacts.parse_pg_majors("13,14 15"),
            [13, 14, 15],
        )

    def test_build_report_records_valid_artifacts_and_digests(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp)
            write_artifacts(dist_root / "pgwarc_lance-0.1.0-pg16")

            report = report_release_artifacts.build_report(
                dist_root=dist_root,
                version="0.1.0",
                pg_majors=[16],
            )

        self.assertTrue(report["all_valid"])
        entry = report["artifacts"][0]
        self.assertTrue(entry["valid"])
        self.assertEqual(entry["errors"], [])
        self.assertEqual(entry["files"]["shared_library"]["bytes"], 22)
        self.assertRegex(entry["files"]["shared_library"]["sha256"], r"^[0-9a-f]{64}$")

    def test_build_report_marks_missing_major_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = report_release_artifacts.build_report(
                dist_root=Path(tmp),
                version="0.1.0",
                pg_majors=[17],
            )

        self.assertFalse(report["all_valid"])
        self.assertFalse(report["artifacts"][0]["valid"])
        self.assertTrue(
            any("dist directory does not exist" in error for error in report["artifacts"][0]["errors"])
        )

    def test_markdown_includes_errors(self):
        report = {
            "version": "0.1.0",
            "dist_root": "/tmp/dist",
            "all_valid": False,
            "artifacts": [
                {
                    "pg_major": 16,
                    "dist_dir": "/tmp/dist/pgwarc_lance-0.1.0-pg16",
                    "valid": False,
                    "errors": ["missing artifact"],
                    "files": {
                        "shared_library": {"exists": False},
                        "control": {"exists": False},
                        "sql": {"exists": False},
                    },
                }
            ],
        }

        markdown = report_release_artifacts.render_markdown(report)

        self.assertIn("# pgwarc_lance Release Artifact Matrix", markdown)
        self.assertIn("| 16 | fail |", markdown)
        self.assertIn("missing artifact", markdown)

    def test_main_writes_outputs_and_returns_nonzero_for_invalid_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            json_output = Path(tmp) / "reports" / "matrix.json"
            markdown_output = Path(tmp) / "reports" / "matrix.md"

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = report_release_artifacts.main(
                    [
                        "--dist-root",
                        str(dist_root),
                        "--version",
                        "0.1.0",
                        "--pg-majors",
                        "16",
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ]
                )

            data = json.loads(json_output.read_text(encoding="utf-8"))
            markdown_exists = markdown_output.is_file()

        self.assertEqual(exit_code, 1)
        self.assertFalse(data["all_valid"])
        self.assertTrue(markdown_exists)


if __name__ == "__main__":
    unittest.main()
