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
TOOL_PATH = TOOLS / "smoke_release_artifacts.py"
SPEC = importlib.util.spec_from_file_location("smoke_release_artifacts", TOOL_PATH)
smoke_release_artifacts = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(smoke_release_artifacts)


class SmokeReleaseArtifactTests(unittest.TestCase):
    def test_docker_destination_joins_directory_and_filename(self):
        destination = smoke_release_artifacts.docker_destination(
            "container",
            "/usr/share/postgresql/16/extension/",
            "pgwarc_lance.control",
        )

        self.assertEqual(
            destination,
            "container:/usr/share/postgresql/16/extension/pgwarc_lance.control",
        )

    def test_install_artifacts_uses_target_pg_config_paths(self):
        commands = []
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp)

            def fake_run(command, *, capture=False, quiet=False, check=True):
                commands.append(command)
                if command[-1] == "--pkglibdir":
                    return "/usr/lib/postgresql/16/lib"
                if command[-1] == "--sharedir":
                    return "/usr/share/postgresql/16"
                return ""

            with mock.patch.object(smoke_release_artifacts, "run_command", fake_run):
                smoke_release_artifacts.install_artifacts(
                    container="pg-smoke",
                    dist_dir=dist_dir,
                    version="0.1.0",
                )

        docker_cp_commands = [command for command in commands if command[:2] == ["docker", "cp"]]
        self.assertEqual(len(docker_cp_commands), 3)
        self.assertIn(
            "pg-smoke:/usr/lib/postgresql/16/lib/pgwarc_lance.so",
            docker_cp_commands[0],
        )
        self.assertIn(
            "pg-smoke:/usr/share/postgresql/16/extension/pgwarc_lance.control",
            docker_cp_commands[1],
        )
        self.assertIn(
            "pg-smoke:/usr/share/postgresql/16/extension/pgwarc_lance--0.1.0.sql",
            docker_cp_commands[2],
        )

    def test_smoke_removes_container_after_success(self):
        commands = []
        args = argparse.Namespace(
            dist_dir=Path("/tmp/dist"),
            version="0.1.0",
            pg_major=16,
            postgres_image=None,
            container_name="pg-smoke",
            user="pgwarc_lance",
            password="pgwarc_lance",
            database="pgwarc_lance_test",
            startup_timeout=90,
        )

        def fake_run(command, *, capture=False, quiet=False, check=True):
            commands.append(command)
            return ""

        with (
            mock.patch.object(
                smoke_release_artifacts.check_release_artifacts,
                "validate_artifacts",
                return_value=[],
            ),
            mock.patch.object(smoke_release_artifacts, "run_command", fake_run),
            mock.patch.object(smoke_release_artifacts, "wait_until_ready"),
            mock.patch.object(smoke_release_artifacts, "install_artifacts"),
            mock.patch.object(smoke_release_artifacts, "run_extension_smoke"),
        ):
            smoke_release_artifacts.smoke(args)

        self.assertEqual(commands[0][:4], ["docker", "rm", "-f", "pg-smoke"])
        self.assertIn(["docker", "run", "-d", "--name", "pg-smoke"], [commands[1][:5]])
        self.assertEqual(commands[-1][:4], ["docker", "rm", "-f", "pg-smoke"])

    def test_smoke_rejects_invalid_artifacts_before_docker_run(self):
        commands = []
        args = argparse.Namespace(
            dist_dir=Path("/tmp/dist"),
            version="0.1.0",
            pg_major=16,
            postgres_image=None,
            container_name="pg-smoke",
            user="pgwarc_lance",
            password="pgwarc_lance",
            database="pgwarc_lance_test",
            startup_timeout=90,
        )

        with (
            mock.patch.object(
                smoke_release_artifacts.check_release_artifacts,
                "validate_artifacts",
                return_value=["missing artifacts"],
            ),
            mock.patch.object(
                smoke_release_artifacts,
                "run_command",
                side_effect=lambda command, **kwargs: commands.append(command) or "",
            ),
        ):
            with self.assertRaisesRegex(smoke_release_artifacts.SmokeError, "missing artifacts"):
                smoke_release_artifacts.smoke(args)

        self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()
