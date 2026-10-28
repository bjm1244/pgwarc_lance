from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import lance_outbox_worker


class LanceOutboxWorkerTests(unittest.TestCase):
    def test_parse_empty_result_as_idle(self):
        self.assertIsNone(lance_outbox_worker.parse_result("\n"))

    def test_parse_completed_metrics(self):
        self.assertEqual(
            lance_outbox_worker.parse_result("42|completed|7|135\n"),
            {
                "outbox_id": 42,
                "status": "completed",
                "applied_version": 7,
                "queue_lag_ms": 135,
            },
        )

    def test_parse_failed_attempt_without_applied_version(self):
        self.assertEqual(
            lance_outbox_worker.parse_result("43|failed||901\n"),
            {
                "outbox_id": 43,
                "status": "failed",
                "applied_version": None,
                "queue_lag_ms": 901,
            },
        )

    def test_reject_unexpected_worker_result(self):
        with self.assertRaisesRegex(ValueError, "row shape"):
            lance_outbox_worker.parse_result("42|completed|7")
        with self.assertRaisesRegex(ValueError, "unexpected status"):
            lance_outbox_worker.parse_result("42|claimed||0")

    def test_process_one_uses_one_autocommit_function_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "worker.sql"
            fake_psql = Path(tmp) / "fake-psql"
            fake_psql.write_text(
                "#!/bin/sh\n"
                f"cat > '{output}'\n"
                "printf '9|completed|3|18\\n'\n",
                encoding="utf-8",
            )
            fake_psql.chmod(0o755)
            self.assertEqual(
                lance_outbox_worker.process_one([str(fake_psql)], 5),
                {
                    "outbox_id": 9,
                    "status": "completed",
                    "applied_version": 3,
                    "queue_lag_ms": 18,
                },
            )
            self.assertIn("public.lance_outbox_process_next()", output.read_text())
            self.assertNotIn("BEGIN;", output.read_text())


if __name__ == "__main__":
    unittest.main()
