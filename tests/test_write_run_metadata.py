import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
TOOL_PATH = TOOLS / "write_run_metadata.py"
SPEC = importlib.util.spec_from_file_location("write_run_metadata", TOOL_PATH)
write_run_metadata = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(write_run_metadata)


class RunMetadataTests(unittest.TestCase):
    def test_capture_environment_redacts_sensitive_values(self):
        captured = write_run_metadata.capture_environment(
            ["PG_MAJOR", "OPENAI_API_KEY", "SESSION_COOKIE", "MISSING"],
            env={
                "PG_MAJOR": "16",
                "OPENAI_API_KEY": "secret-key",
                "SESSION_COOKIE": "cookie-value",
            },
        )

        self.assertEqual(captured["PG_MAJOR"], "16")
        self.assertEqual(captured["OPENAI_API_KEY"], "<redacted>")
        self.assertEqual(captured["SESSION_COOKIE"], "<redacted>")
        self.assertIsNone(captured["MISSING"])

    def test_build_metadata_records_git_command_and_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            metadata = write_run_metadata.build_metadata(
                run_dir=Path(tmp),
                command="make bench",
                env_names=["PG_MAJOR"],
                generated_at="2026-07-06T00:00:00Z",
                repo_root=ROOT,
                env={"PG_MAJOR": "17"},
            )

        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(metadata["generated_at"], "2026-07-06T00:00:00Z")
        self.assertEqual(metadata["command"], "make bench")
        self.assertEqual(metadata["environment"]["PG_MAJOR"], "17")
        self.assertRegex(metadata["git"]["head_short"], r"^[0-9a-f]{7}$")
        self.assertIsInstance(metadata["git"]["status"], list)

    def test_markdown_includes_environment_and_git_status(self):
        metadata = {
            "schema_version": 1,
            "generated_at": "2026-07-06T00:00:00Z",
            "run_dir": "/tmp/run",
            "command": "make bench",
            "environment": {"PG_MAJOR": "16"},
            "git": {
                "root": str(ROOT),
                "head": "abcdef0123456789",
                "head_short": "abcdef0",
                "branch": "master",
                "dirty": True,
                "status": [" M README.md"],
            },
            "warnings": [],
        }

        markdown = write_run_metadata.render_markdown(metadata)

        self.assertIn("# pgwarc_lance Run Metadata", markdown)
        self.assertIn("| `PG_MAJOR` | `16` |", markdown)
        self.assertIn("` M README.md`", markdown)

    def test_main_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            json_output = run_dir / "run_metadata.json"
            markdown_output = run_dir / "run_metadata.md"

            with redirect_stdout(io.StringIO()):
                exit_code = write_run_metadata.main(
                    [
                        "--run-dir",
                        str(run_dir),
                        "--command",
                        "make bench",
                        "--env",
                        "PG_MAJOR",
                        "--generated-at",
                        "2026-07-06T00:00:00Z",
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ]
                )

            metadata = json.loads(json_output.read_text(encoding="utf-8"))
            markdown = markdown_output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(metadata["command"], "make bench")
        self.assertIn("# pgwarc_lance Run Metadata", markdown)


if __name__ == "__main__":
    unittest.main()
