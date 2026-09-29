import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
TOOL_PATH = TOOLS / "check_docs.py"
SPEC = importlib.util.spec_from_file_location("check_docs", TOOL_PATH)
check_docs = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(check_docs)


class CheckDocsTests(unittest.TestCase):
    def test_latest_roadmap_phase_uses_highest_phase_header(self):
        text = "\n".join(
            [
                "## Phase 1: First",
                "text",
                "## Phase 39: Latest",
                "## Phase 12: Older",
            ]
        )

        self.assertEqual(check_docs.latest_roadmap_phase(text), 39)

    def test_current_phase_range_extracts_completion_summary(self):
        pattern = re.compile(r"현재 완료된 Phase 1-(\d+)")

        self.assertEqual(
            check_docs.current_phase_range("현재 완료된 Phase 1-39 이후", pattern),
            39,
        )

    def test_phase_count_mismatch_is_reported(self):
        errors = []

        def fake_read_text(relative: str) -> str:
            if relative == "ROADMAP.md":
                return "## Phase 39: Latest\n현재 로드맵의 Phase 1-39"
            if relative == "README.md":
                return "현재 완료된 Phase 1-36 이후"
            return ""

        with patch.object(check_docs, "read_text", side_effect=fake_read_text):
            check_docs.check_phase_count_drift(errors)

        self.assertEqual(
            errors,
            [
                "README.md: current Phase 1-36 summary does not match latest ROADMAP phase 39",
            ],
        )


if __name__ == "__main__":
    unittest.main()
