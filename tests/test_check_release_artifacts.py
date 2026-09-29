import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "check_release_artifacts.py"
SPEC = importlib.util.spec_from_file_location("check_release_artifacts", TOOL_PATH)
check_release_artifacts = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(check_release_artifacts)


def write_artifacts(
    dist_dir: Path,
    *,
    version: str = "0.1.0",
    so_bytes: bytes = b"\x7fELFfake-shared-object",
    control_version: str | None = None,
    sql_symbols: list[str] | None = None,
) -> None:
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "pgwarc_lance.so").write_bytes(so_bytes)
    (dist_dir / "pgwarc_lance.control").write_text(
        "\n".join(
            [
                "comment = 'pgwarc_lance test artifact'",
                f"default_version = '{control_version or version}'",
                "module_pathname = 'pgwarc_lance'",
                "relocatable = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    symbols = sql_symbols or check_release_artifacts.REQUIRED_SQL_SYMBOLS
    (dist_dir / f"pgwarc_lance--{version}.sql").write_text(
        "\n".join(f"-- {symbol}" for symbol in symbols) + "\n",
        encoding="utf-8",
    )


class ReleaseArtifactCheckTests(unittest.TestCase):
    def test_accepts_valid_artifact_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp) / "pgwarc_lance-0.1.0-pg16"
            write_artifacts(dist_dir)

            errors = check_release_artifacts.validate_artifacts(
                dist_dir,
                version="0.1.0",
                pg_major=16,
            )

        self.assertEqual(errors, [])

    def test_rejects_missing_required_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp) / "pgwarc_lance-0.1.0-pg16"
            write_artifacts(dist_dir)
            (dist_dir / "pgwarc_lance.so").unlink()

            errors = check_release_artifacts.validate_artifacts(
                dist_dir,
                version="0.1.0",
                pg_major=16,
            )

        self.assertTrue(any("missing required artifact" in error for error in errors))

    def test_rejects_non_elf_shared_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp) / "pgwarc_lance-0.1.0-pg16"
            write_artifacts(dist_dir, so_bytes=b"not-elf")

            errors = check_release_artifacts.validate_artifacts(
                dist_dir,
                version="0.1.0",
                pg_major=16,
            )

        self.assertTrue(any("not an ELF" in error for error in errors))

    def test_rejects_control_version_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp) / "pgwarc_lance-0.1.0-pg16"
            write_artifacts(dist_dir, control_version="@CARGO_VERSION@")

            errors = check_release_artifacts.validate_artifacts(
                dist_dir,
                version="0.1.0",
                pg_major=16,
            )

        self.assertTrue(any("default_version mismatch" in error for error in errors))

    def test_rejects_sql_missing_expected_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp) / "pgwarc_lance-0.1.0-pg16"
            write_artifacts(dist_dir, sql_symbols=["hello_pgwarc_lance"])

            errors = check_release_artifacts.validate_artifacts(
                dist_dir,
                version="0.1.0",
                pg_major=16,
            )

        self.assertTrue(any("missing expected symbol" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
