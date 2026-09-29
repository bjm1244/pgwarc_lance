"""Generate quality eval artifacts through the producer CLI for doctor tests.

The doctor tests consume artifacts that `tools/eval_quality.py` actually writes,
so every non-success payload shape (setup timeout with zero results, query
timeout with partial results, and budget exhaustion) is produced by running the
producer `main()` with a mocked `run_sql` boundary instead of hand-building JSON.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import eval_quality  # noqa: E402


def producer_fixture(query_count: int = 2) -> dict:
    return {
        "name": "producer-fixture",
        "vector_dim": 1,
        "docs": [{"doc_id": 1, "text": "doc", "vector": [1.0]}],
        "queries": [
            {
                "name": f"q{index}",
                "query": "doc",
                "vector": [1.0],
                "expected_doc_ids": [1],
            }
            for index in range(query_count)
        ],
    }


def produce_artifact(tmp_path: Path, mode: str, query_count: int = 2):
    """Run `eval_quality.main()` for `mode`.

    Returns ``(payload, exit_code, markdown_text)``. Modes: ``success``,
    ``setup_timeout``, ``query_timeout``, ``client_timeout_with_statement_text``,
    ``server_statement_timeout``, ``budget_exhausted``.
    """

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(producer_fixture(query_count)), encoding="utf-8"
    )
    output = tmp_path / f"{mode}.json"
    markdown_path = tmp_path / f"{mode}.md"
    argv = [
        "eval_quality.py",
        "--fixture-json",
        str(fixture_path),
        "--json-output",
        str(output),
        "--markdown-output",
        str(markdown_path),
    ]

    if mode == "success":

        def fake_run_sql(psql, sql, timeout=None):
            return "1|0.5|lance\n" if "hybrid_warc_search" in sql else ""

    elif mode == "setup_timeout":
        argv += ["--query-timeout-seconds", "5"]

        def fake_run_sql(psql, sql, timeout=None):
            raise eval_quality.SqlTimeout(timeout_seconds=timeout)

    elif mode == "query_timeout":
        argv += ["--query-timeout-seconds", "5"]
        query_calls = []

        def fake_run_sql(psql, sql, timeout=None):
            if "hybrid_warc_search" not in sql:
                return ""
            query_calls.append(sql)
            if len(query_calls) >= 2:
                raise eval_quality.SqlTimeout(timeout_seconds=timeout)
            return "1|0.5|lance\n"

    elif mode == "client_timeout_with_statement_text":
        argv += ["--query-timeout-seconds", "5"]
        query_calls = []

        def fake_run_sql(psql, sql, timeout=None):
            if "hybrid_warc_search" not in sql:
                return ""
            query_calls.append(sql)
            if len(query_calls) >= 2:
                raise eval_quality.SqlTimeout(
                    timeout_seconds=timeout,
                    stderr=(
                        "ERROR:  57014: canceling statement due to statement timeout\n"
                    ),
                )
            return "1|0.5|lance\n"

    elif mode == "server_statement_timeout":
        argv += ["--query-statement-timeout-seconds", "5"]
        query_calls = []

        def fake_run_sql(psql, sql, timeout=None):
            if "hybrid_warc_search" not in sql:
                return ""
            query_calls.append(sql)
            if len(query_calls) >= 2:
                raise eval_quality.StatementTimeout(
                    stderr=(
                        "ERROR:  57014: canceling statement due to statement timeout\n"
                    ),
                    sqlstate="57014",
                    effective_statement_timeout_ms=5000,
                )
            return "1|0.5|lance\n"

    elif mode == "budget_exhausted":
        argv += [
            "--max-iterations",
            "5",
            "--max-queries",
            "1",
            "--deadline-seconds",
            "60",
        ]

        def fake_run_sql(psql, sql, timeout=None):
            return "1|0.5|lance\n" if "hybrid_warc_search" in sql else ""

    else:
        raise ValueError(f"unknown producer mode: {mode}")

    with mock.patch.object(sys, "argv", argv), mock.patch.object(
        eval_quality, "run_sql", side_effect=fake_run_sql
    ):
        exit_code = eval_quality.main()

    payload = json.loads(output.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    return payload, exit_code, markdown
