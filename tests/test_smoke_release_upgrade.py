import argparse
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
TOOL_PATH = TOOLS / "smoke_release_upgrade.py"
SPEC = importlib.util.spec_from_file_location("smoke_release_upgrade", TOOL_PATH)
smoke_release_upgrade = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(smoke_release_upgrade)


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
            for symbol in smoke_release_upgrade.check_release_artifacts.REQUIRED_SQL_SYMBOLS
        )
        + "\n",
        encoding="utf-8",
    )


class SmokeReleaseUpgradeTests(unittest.TestCase):
    def test_sql_literal_escapes_single_quotes(self):
        self.assertEqual(smoke_release_upgrade.sql_literal("1.0'rc1"), "'1.0''rc1'")

    def test_validate_upgrade_artifacts_requires_upgrade_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dist = root / "old"
            new_dist = root / "new"
            write_artifacts(old_dist, version="0.1.0")
            write_artifacts(new_dist, version="0.2.0")

            errors = smoke_release_upgrade.validate_upgrade_artifacts(
                old_dist_dir=old_dist,
                old_version="0.1.0",
                new_dist_dir=new_dist,
                new_version="0.2.0",
                pg_major=16,
            )

        self.assertTrue(any("missing upgrade script" in error for error in errors))

    def test_validate_upgrade_artifacts_accepts_upgrade_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dist = root / "old"
            new_dist = root / "new"
            write_artifacts(old_dist, version="0.1.0")
            write_artifacts(new_dist, version="0.2.0")
            (new_dist / "pgwarc_lance--0.1.0--0.2.0.sql").write_text(
                "-- upgrade\n",
                encoding="utf-8",
            )

            errors = smoke_release_upgrade.validate_upgrade_artifacts(
                old_dist_dir=old_dist,
                old_version="0.1.0",
                new_dist_dir=new_dist,
                new_version="0.2.0",
                pg_major=16,
            )

        self.assertEqual(errors, [])

    def test_install_upgrade_script_uses_runtime_extension_dir(self):
        commands = []
        upgrade_script = Path("/tmp/pgwarc_lance--0.1.0--0.2.0.sql")

        def fake_run(command, *, capture=False, quiet=False, check=True):
            commands.append(command)
            if command[-1] == "--sharedir":
                return "/usr/share/postgresql/16"
            return ""

        with mock.patch.object(
            smoke_release_upgrade.smoke_release_artifacts,
            "run_command",
            fake_run,
        ):
            smoke_release_upgrade.install_upgrade_script(
                container="pg-upgrade",
                upgrade_script=upgrade_script,
            )

        self.assertEqual(commands[0], ["docker", "exec", "pg-upgrade", "pg_config", "--sharedir"])
        self.assertEqual(commands[1][0:2], ["docker", "cp"])
        self.assertIn(
            "pg-upgrade:/usr/share/postgresql/16/extension/pgwarc_lance--0.1.0--0.2.0.sql",
            commands[1],
        )

    def test_run_create_old_smoke_creates_requested_version(self):
        commands = []

        def fake_run(command, *, capture=False, quiet=False, check=True):
            commands.append(command)
            return ""

        with mock.patch.object(
            smoke_release_upgrade.smoke_release_artifacts,
            "run_command",
            fake_run,
        ):
            smoke_release_upgrade.run_create_old_smoke(
                container="pg-upgrade",
                user="pgwarc_lance",
                database="pgwarc_lance_test",
                old_version="0.1.0",
            )

        sql = commands[0][-1]
        self.assertIn("CREATE EXTENSION pgwarc_lance VERSION '0.1.0'", sql)
        self.assertIn("SELECT hello_pgwarc_lance()", sql)

    def test_run_update_smoke_alters_to_new_version(self):
        commands = []

        def fake_run(command, *, capture=False, quiet=False, check=True):
            commands.append(command)
            return ""

        with mock.patch.object(
            smoke_release_upgrade.smoke_release_artifacts,
            "run_command",
            fake_run,
        ):
            smoke_release_upgrade.run_update_smoke(
                container="pg-upgrade",
                user="pgwarc_lance",
                database="pgwarc_lance_test",
                new_version="0.2.0",
            )

        sql = commands[0][-1]
        self.assertIn("ALTER EXTENSION pgwarc_lance UPDATE TO '0.2.0'", sql)
        self.assertIn("SELECT hello_pgwarc_lance()", sql)

    def test_smoke_rejects_invalid_artifacts_before_docker_run(self):
        commands = []
        args = argparse.Namespace(
            old_dist_dir=Path("/tmp/old"),
            old_version="0.1.0",
            new_dist_dir=Path("/tmp/new"),
            new_version="0.2.0",
            pg_major=16,
            postgres_image=None,
            container_name="pg-upgrade",
            user="pgwarc_lance",
            password="pgwarc_lance",
            database="pgwarc_lance_test",
            startup_timeout=90,
        )

        with (
            mock.patch.object(
                smoke_release_upgrade,
                "validate_upgrade_artifacts",
                return_value=["missing upgrade script"],
            ),
            mock.patch.object(
                smoke_release_upgrade.smoke_release_artifacts,
                "run_command",
                side_effect=lambda command, **kwargs: commands.append(command) or "",
            ),
        ):
            with self.assertRaisesRegex(
                smoke_release_upgrade.SmokeError,
                "missing upgrade script",
            ):
                smoke_release_upgrade.smoke(args)

        self.assertEqual(commands, [])

    def test_smoke_installs_old_new_and_upgrade_script_then_cleans_up(self):
        calls = []
        args = argparse.Namespace(
            old_dist_dir=Path("/tmp/old"),
            old_version="0.1.0",
            new_dist_dir=Path("/tmp/new"),
            new_version="0.2.0",
            pg_major=16,
            postgres_image=None,
            container_name="pg-upgrade",
            user="pgwarc_lance",
            password="pgwarc_lance",
            database="pgwarc_lance_test",
            startup_timeout=90,
        )

        def fake_run(command, *, capture=False, quiet=False, check=True):
            calls.append(("run", command))
            return ""

        with (
            mock.patch.object(smoke_release_upgrade, "validate_upgrade_artifacts", return_value=[]),
            mock.patch.object(smoke_release_upgrade.smoke_release_artifacts, "run_command", fake_run),
            mock.patch.object(
                smoke_release_upgrade.smoke_release_artifacts,
                "wait_until_ready",
                side_effect=lambda *args, **kwargs: calls.append(("ready", kwargs)),
            ),
            mock.patch.object(
                smoke_release_upgrade.smoke_release_artifacts,
                "install_artifacts",
                side_effect=lambda **kwargs: calls.append(("install", kwargs)),
            ),
            mock.patch.object(
                smoke_release_upgrade,
                "install_upgrade_script",
                side_effect=lambda **kwargs: calls.append(("upgrade_script", kwargs)),
            ),
            mock.patch.object(
                smoke_release_upgrade,
                "run_create_old_smoke",
                side_effect=lambda **kwargs: calls.append(("create_old", kwargs)),
            ),
            mock.patch.object(
                smoke_release_upgrade,
                "run_update_smoke",
                side_effect=lambda **kwargs: calls.append(("update_smoke", kwargs)),
            ),
        ):
            smoke_release_upgrade.smoke(args)

        self.assertEqual(calls[0][0], "run")
        self.assertEqual(calls[0][1][:4], ["docker", "rm", "-f", "pg-upgrade"])
        self.assertEqual(calls[2][0], "ready")
        self.assertEqual(calls[3][1]["dist_dir"], Path("/tmp/old"))
        self.assertEqual(calls[4][0], "create_old")
        self.assertEqual(calls[5][1]["dist_dir"], Path("/tmp/new"))
        self.assertEqual(calls[6][0], "upgrade_script")
        self.assertEqual(calls[7][0], "update_smoke")
        self.assertEqual(calls[-1][1][:4], ["docker", "rm", "-f", "pg-upgrade"])


if __name__ == "__main__":
    unittest.main()
