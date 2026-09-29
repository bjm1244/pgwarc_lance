import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
TOOL_PATH = TOOLS / "doctor_release_upgrade_inputs.py"
SPEC = importlib.util.spec_from_file_location("doctor_release_upgrade_inputs", TOOL_PATH)
doctor_release_upgrade_inputs = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(doctor_release_upgrade_inputs)


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
            for symbol in doctor_release_upgrade_inputs.check_release_artifacts.REQUIRED_SQL_SYMBOLS
        )
        + "\n",
        encoding="utf-8",
    )


def artifact_dir(dist_root: Path, *, version: str, pg_major: int = 16) -> Path:
    return dist_root / f"pgwarc_lance-{version}-pg{pg_major}"


def build_report(
    dist_root: Path,
    *,
    old_version: str | None,
    new_version: str,
    pg_major: int = 16,
):
    old_dist_dir = (
        artifact_dir(dist_root, version=old_version, pg_major=pg_major)
        if old_version is not None
        else dist_root / f"pgwarc_lance-<old>-pg{pg_major}"
    )
    new_dist_dir = artifact_dir(dist_root, version=new_version, pg_major=pg_major)
    return doctor_release_upgrade_inputs.build_report(
        dist_root=dist_root,
        pg_major=pg_major,
        current_version=new_version,
        old_version=old_version,
        new_version=new_version,
        old_dist_dir=old_dist_dir,
        new_dist_dir=new_dist_dir,
    )


def check_names(report) -> set[str]:
    return {check["name"] for check in report["checks"]}


def check_by_name(report, name: str) -> dict:
    for check in report["checks"]:
        if check["name"] == name:
            return check
    raise AssertionError(f"missing check {name}")


class DoctorReleaseUpgradeInputsTests(unittest.TestCase):
    def test_discover_artifacts_parses_version_and_major(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp)
            write_artifacts(artifact_dir(dist_root, version="0.1.0", pg_major=16), version="0.1.0")
            write_artifacts(artifact_dir(dist_root, version="0.2.0", pg_major=17), version="0.2.0")
            (dist_root / "not-an-artifact").mkdir()
            (dist_root / "some-file.txt").write_text("x", encoding="utf-8")

            artifacts = doctor_release_upgrade_inputs.discover_artifacts(dist_root)

        self.assertEqual(
            artifacts,
            [
                {
                    "version": "0.1.0",
                    "pg_major": 16,
                    "dist_dir": str(artifact_dir(dist_root, version="0.1.0", pg_major=16)),
                },
                {
                    "version": "0.2.0",
                    "pg_major": 17,
                    "dist_dir": str(artifact_dir(dist_root, version="0.2.0", pg_major=17)),
                },
            ],
        )

    def test_blocks_when_old_version_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp)
            write_artifacts(artifact_dir(dist_root, version="0.1.0"), version="0.1.0")

            report = build_report(dist_root, old_version=None, new_version="0.1.0")

        self.assertFalse(report["valid"])
        self.assertEqual(report["status"], "blocked")
        self.assertIn("old_version_provided", check_names(report))
        self.assertFalse(check_by_name(report, "old_version_provided")["ok"])
        self.assertIn("<old>", report["smoke_command"])
        self.assertEqual(report["available_versions"], ["0.1.0"])
        self.assertTrue(any("no distinct old artifact" in warning for warning in report["warnings"]))

    def test_blocks_when_upgrade_script_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp)
            write_artifacts(artifact_dir(dist_root, version="0.1.0"), version="0.1.0")
            write_artifacts(artifact_dir(dist_root, version="0.2.0"), version="0.2.0")

            report = build_report(dist_root, old_version="0.1.0", new_version="0.2.0")

        self.assertFalse(report["valid"])
        self.assertEqual(report["status"], "blocked")
        script_check = check_by_name(report, "upgrade_script_present")
        self.assertFalse(script_check["ok"])
        self.assertIn("missing upgrade script", script_check["detail"])
        self.assertTrue(check_by_name(report, "old_artifact_valid")["ok"])
        self.assertTrue(check_by_name(report, "new_artifact_valid")["ok"])

    def test_blocks_when_versions_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp)
            write_artifacts(artifact_dir(dist_root, version="0.2.0"), version="0.2.0")
            (
                artifact_dir(dist_root, version="0.2.0")
                / "pgwarc_lance--0.2.0--0.2.0.sql"
            ).write_text("-- upgrade\n", encoding="utf-8")

            report = build_report(dist_root, old_version="0.2.0", new_version="0.2.0")

        self.assertFalse(report["valid"])
        self.assertFalse(check_by_name(report, "versions_differ")["ok"])

    def test_blocks_when_old_artifact_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp)
            write_artifacts(artifact_dir(dist_root, version="0.2.0"), version="0.2.0")
            (
                artifact_dir(dist_root, version="0.2.0")
                / "pgwarc_lance--0.1.0--0.2.0.sql"
            ).write_text("-- upgrade\n", encoding="utf-8")

            report = build_report(dist_root, old_version="0.1.0", new_version="0.2.0")

        self.assertFalse(report["valid"])
        self.assertFalse(check_by_name(report, "old_artifact_valid")["ok"])

    def test_ready_when_all_inputs_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp)
            write_artifacts(artifact_dir(dist_root, version="0.1.0"), version="0.1.0")
            write_artifacts(artifact_dir(dist_root, version="0.2.0"), version="0.2.0")
            (
                artifact_dir(dist_root, version="0.2.0")
                / "pgwarc_lance--0.1.0--0.2.0.sql"
            ).write_text("-- upgrade\n", encoding="utf-8")

            report = build_report(dist_root, old_version="0.1.0", new_version="0.2.0")

        self.assertTrue(report["valid"])
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["errors"], [])
        self.assertTrue(all(check["ok"] for check in report["checks"]))

    def test_render_markdown_contains_status_and_resume_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp)
            write_artifacts(artifact_dir(dist_root, version="0.1.0"), version="0.1.0")

            report = build_report(dist_root, old_version=None, new_version="0.1.0")
            markdown = doctor_release_upgrade_inputs.render_markdown(report)

        self.assertIn("status: `blocked`", markdown)
        self.assertIn("## Resume Commands", markdown)
        self.assertIn("make smoke-release-upgrade", markdown)

    def test_main_writes_outputs_and_returns_nonzero_when_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            write_artifacts(artifact_dir(dist_root, version="0.1.0"), version="0.1.0")
            json_out = Path(tmp) / "doctor.json"
            markdown_out = Path(tmp) / "doctor.md"

            exit_code = doctor_release_upgrade_inputs.main(
                [
                    "--dist-root",
                    str(dist_root),
                    "--pg-major",
                    "16",
                    "--new-version",
                    "0.1.0",
                    "--json-output",
                    str(json_out),
                    "--markdown-output",
                    str(markdown_out),
                ]
            )

            report = json.loads(json_out.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertFalse(report["valid"])
        self.assertEqual(report["status"], "blocked")

    def test_main_returns_zero_when_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_root = Path(tmp) / "dist"
            write_artifacts(artifact_dir(dist_root, version="0.1.0"), version="0.1.0")
            write_artifacts(artifact_dir(dist_root, version="0.2.0"), version="0.2.0")
            (
                artifact_dir(dist_root, version="0.2.0")
                / "pgwarc_lance--0.1.0--0.2.0.sql"
            ).write_text("-- upgrade\n", encoding="utf-8")

            exit_code = doctor_release_upgrade_inputs.main(
                [
                    "--dist-root",
                    str(dist_root),
                    "--pg-major",
                    "16",
                    "--new-version",
                    "0.2.0",
                    "--old-version",
                    "0.1.0",
                ]
            )

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
