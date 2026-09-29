import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
EVAL_PATH = TOOLS / "eval_quality.py"
SPEC = importlib.util.spec_from_file_location("eval_quality", EVAL_PATH)
eval_quality = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = eval_quality
SPEC.loader.exec_module(eval_quality)

import execution_budget  # noqa: E402


class FakeClock:
    def __init__(self, start=100.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class EvalQualityFixtureTests(unittest.TestCase):
    def assert_strict_load_rejects(self, fixture, message):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = Path(tmp) / "fixture.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, message):
                eval_quality.load_fixture(fixture_path)

    def test_loads_external_fixture_and_generates_setup_sql(self):
        fixture = eval_quality.load_fixture(ROOT / "tests" / "fixtures" / "quality_fixture.json")

        self.assertEqual(fixture["name"], "external-smoke")
        self.assertEqual(fixture["vector_dim"], 4)
        self.assertEqual(len(fixture["docs"]), 3)
        self.assertEqual(len(fixture["queries"]), 2)

        sql = eval_quality.setup_sql(fixture, "/tmp/fixture-quality.lance")

        self.assertIn("SELECT lance_create_table('/tmp/fixture-quality.lance', 4, true);", sql)
        self.assertIn("https://fixture.example/korean", sql)
        self.assertIn("SELECT bm25_index_document(2001", sql)
        self.assertIn("ARRAY[2001,2002,2003]::bigint[]", sql)

    def test_rejects_query_with_unknown_expected_doc_id(self):
        fixture = {
            "vector_dim": 2,
            "docs": [
                {"doc_id": 1, "text": "doc", "vector": [1.0, 0.0]},
            ],
            "queries": [
                {
                    "name": "bad",
                    "query": "doc",
                    "vector": [1.0, 0.0],
                    "expected_doc_ids": [2],
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "unknown expected doc_id"):
            eval_quality.fixture_from_dict(fixture, "fixture")

    def test_rejects_duplicate_expected_doc_ids(self):
        fixture = {
            "vector_dim": 1,
            "docs": [{"doc_id": 1, "text": "doc", "vector": [1.0]}],
            "queries": [
                {
                    "name": "duplicate",
                    "query": "doc",
                    "vector": [1.0],
                    "expected_doc_ids": [1, 1],
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "duplicate expected doc_id"):
            eval_quality.fixture_from_dict(fixture, "fixture")

    def test_rejects_duplicate_document_ids(self):
        fixture = {
            "vector_dim": 1,
            "docs": [
                {"doc_id": 1, "text": "first", "vector": [1.0]},
                {"doc_id": 1, "text": "second", "vector": [1.0]},
            ],
            "queries": [
                {
                    "name": "one",
                    "query": "doc",
                    "vector": [1.0],
                    "expected_doc_ids": [1],
                }
            ],
        }

        self.assert_strict_load_rejects(fixture, "duplicate doc_id")

    def test_rejects_duplicate_query_names(self):
        fixture = {
            "vector_dim": 1,
            "docs": [{"doc_id": 1, "text": "doc", "vector": [1.0]}],
            "queries": [
                {
                    "name": "duplicate",
                    "query": "first",
                    "vector": [1.0],
                    "expected_doc_ids": [1],
                },
                {
                    "name": "duplicate",
                    "query": "second",
                    "vector": [1.0],
                    "expected_doc_ids": [1],
                },
            ],
        }

        self.assert_strict_load_rejects(fixture, "duplicate name")

    def test_rejects_doc_ids_outside_postgresql_bigint_domain(self):
        for raw_id in (0, -1, True, 1.0, "1", 2**63):
            with self.subTest(raw_id=raw_id):
                fixture = {
                    "vector_dim": 1,
                    "docs": [{"doc_id": raw_id, "text": "doc", "vector": [1.0]}],
                    "queries": [
                        {
                            "name": "one",
                            "query": "doc",
                            "vector": [1.0],
                            "expected_doc_ids": [raw_id],
                        }
                    ],
                }

                with self.assertRaisesRegex(
                    ValueError,
                    "positive integer within PostgreSQL bigint range",
                ):
                    eval_quality.fixture_from_dict(
                        fixture,
                        "fixture",
                        reject_duplicate_identities=True,
                    )

    def test_rejects_vector_dimension_mismatch(self):
        fixture = {
            "vector_dim": 3,
            "docs": [
                {"doc_id": 1, "text": "doc", "vector": [1.0, 0.0]},
            ],
            "queries": [
                {
                    "name": "bad",
                    "query": "doc",
                    "vector": [1.0, 0.0, 0.0],
                    "expected_doc_ids": [1],
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "expected 3"):
            eval_quality.fixture_from_dict(fixture, "fixture")

    def test_rejects_non_finite_or_out_of_range_vectors(self):
        for value in (math.nan, math.inf, -math.inf, 10**400):
            with self.subTest(value=value):
                fixture = {
                    "vector_dim": 1,
                    "docs": [{"doc_id": 1, "text": "doc", "vector": [value]}],
                    "queries": [
                        {
                            "name": "one",
                            "query": "doc",
                            "vector": [1.0],
                            "expected_doc_ids": [1],
                        }
                    ],
                }

                with self.assertRaisesRegex(ValueError, "non-finite or out-of-range"):
                    eval_quality.fixture_from_dict(fixture, "fixture")

    def test_cli_fixture_json_output_fields_are_stable_without_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = Path(tmp) / "fixture.json"
            fixture_path.write_text(
                json.dumps(
                    {
                        "name": "tiny",
                        "vector_dim": 2,
                        "docs": [
                            {"doc_id": 1, "text": "doc one", "vector": [1.0, 0.0]},
                        ],
                        "queries": [
                            {
                                "name": "one",
                                "query": "doc",
                                "vector": [1.0, 0.0],
                                "expected_doc_ids": [1],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            fixture = eval_quality.load_fixture(fixture_path)

        self.assertEqual(fixture["name"], "tiny")
        self.assertEqual(fixture["docs"][0]["target_uri"], "https://quality.example/1")

    def sample_fixture(self, query_count):
        return {
            "name": "budget-fixture",
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

    def test_budget_from_args_requires_all_three_limits_together(self):
        import argparse

        partial = argparse.Namespace(max_iterations=3, max_queries=None, deadline_seconds=None)
        with self.assertRaisesRegex(execution_budget.BudgetConfigError, "missing budget value"):
            eval_quality.budget_from_args(partial)

        none = argparse.Namespace(max_iterations=None, max_queries=None, deadline_seconds=None)
        self.assertIsNone(eval_quality.budget_from_args(none))

    def test_evaluate_queries_enforces_exact_boundary_and_preserves_partial(self):
        fixture = self.sample_fixture(3)
        calls = []

        def fake_run_sql(psql, sql, timeout=None):
            calls.append(sql)
            return ""

        budget = execution_budget.ExecutionBudget(
            execution_budget.BudgetConfig(max_iterations=5, max_queries=2, deadline_seconds=60.0)
        )
        results, exhausted = eval_quality.evaluate_queries(
            queries=fixture["queries"],
            psql="psql",
            lance_uri="/tmp/x.lance",
            k=3,
            budget=budget,
            run_sql_fn=fake_run_sql,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(results), 2)
        self.assertIsNotNone(exhausted)
        self.assertEqual(exhausted.limit_kind, execution_budget.LIMIT_QUERIES)
        self.assertEqual(exhausted.used, 2)
        self.assertEqual(exhausted.limit, 2)
        self.assertEqual(budget.iterations, 2)
        self.assertEqual(budget.queries, 2)
        status = exhausted.to_status(partial_results=results)
        self.assertEqual(status["status"], "budget_exhausted")
        self.assertFalse(status["success"])
        self.assertTrue(status["partial_result_preserved"])
        self.assertEqual(status["partial_result_count"], 2)

    def test_evaluate_queries_iteration_limit_blocks_n_plus_one(self):
        fixture = self.sample_fixture(3)
        calls = []

        def fake_run_sql(psql, sql, timeout=None):
            calls.append(sql)
            return ""

        budget = execution_budget.ExecutionBudget(
            execution_budget.BudgetConfig(max_iterations=2, max_queries=5, deadline_seconds=60.0)
        )
        results, exhausted = eval_quality.evaluate_queries(
            queries=fixture["queries"],
            psql="psql",
            lance_uri="/tmp/x.lance",
            k=3,
            budget=budget,
            run_sql_fn=fake_run_sql,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(results), 2)
        self.assertEqual(exhausted.limit_kind, execution_budget.LIMIT_ITERATIONS)
        self.assertEqual(budget.iterations, 2)
        self.assertEqual(budget.queries, 2)

    def test_evaluate_queries_simultaneous_limits_consume_nothing_extra(self):
        fixture = self.sample_fixture(3)
        calls = []

        def fake_run_sql(psql, sql, timeout=None):
            calls.append(sql)
            return ""

        budget = execution_budget.ExecutionBudget(
            execution_budget.BudgetConfig(max_iterations=2, max_queries=2, deadline_seconds=60.0)
        )
        results, exhausted = eval_quality.evaluate_queries(
            queries=fixture["queries"],
            psql="psql",
            lance_uri="/tmp/x.lance",
            k=3,
            budget=budget,
            run_sql_fn=fake_run_sql,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(results), 2)
        self.assertEqual(exhausted.limit_kind, execution_budget.LIMIT_ITERATIONS)
        self.assertEqual(budget.iterations, 2)
        self.assertEqual(budget.queries, 2)

    def test_main_rejects_invalid_budget_before_any_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "quality.json"
            argv = [
                "eval_quality.py",
                "--max-iterations",
                "0",
                "--max-queries",
                "1",
                "--deadline-seconds",
                "5",
                "--json-output",
                str(output),
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                eval_quality, "run_sql"
            ) as run_sql:
                exit_code = eval_quality.main()

        self.assertEqual(exit_code, 2)
        run_sql.assert_not_called()
        self.assertFalse(output.exists())

    def test_exhausted_payload_is_not_labelled_success(self):
        fixture = self.sample_fixture(2)
        exhausted = execution_budget.BudgetExhausted(
            limit_kind=execution_budget.LIMIT_DEADLINE,
            used=1.5,
            limit=1.0,
        )
        payload = eval_quality.exhausted_payload(
            fixture=fixture,
            results=[],
            k=3,
            lance_uri="/tmp/x.lance",
            exhausted=exhausted,
        )

        self.assertEqual(payload["status"], "budget_exhausted")
        self.assertFalse(payload["success"])
        self.assertNotIn("hit_rate_at_k", payload)
        self.assertNotIn("mrr_at_k", payload)


class QueryTimeoutTests(unittest.TestCase):
    def make_fixture(self, query_count=2):
        return {
            "name": "timeout-fixture",
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

    def write_fixture(self, tmp, query_count=2):
        fixture_path = Path(tmp) / "fixture.json"
        fixture_path.write_text(
            json.dumps(self.make_fixture(query_count)),
            encoding="utf-8",
        )
        return fixture_path

    def test_validate_query_timeout_rejects_non_positive_and_non_finite(self):
        for value in (0, -1, -0.5, math.nan, math.inf, -math.inf, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    eval_quality.validate_query_timeout(value)

        self.assertIsNone(eval_quality.validate_query_timeout(None))
        self.assertEqual(eval_quality.validate_query_timeout(2), 2.0)

    def test_main_rejects_invalid_query_timeout_before_sql(self):
        for raw in ("0", "-1", "nan", "inf"):
            with self.subTest(raw=raw):
                with tempfile.TemporaryDirectory() as tmp:
                    output = Path(tmp) / "quality.json"
                    argv = [
                        "eval_quality.py",
                        "--query-timeout-seconds",
                        raw,
                        "--json-output",
                        str(output),
                    ]
                    with mock.patch.object(sys, "argv", argv), mock.patch.object(
                        eval_quality, "run_sql"
                    ) as run_sql:
                        exit_code = eval_quality.main()

                    self.assertEqual(exit_code, 2)
                    run_sql.assert_not_called()
                    self.assertFalse(output.exists())

    def test_run_sql_passes_timeout_to_subprocess(self):
        completed = mock.Mock(returncode=0, stdout="row\n", stderr="")
        with mock.patch.object(
            eval_quality.subprocess, "run", return_value=completed
        ) as runner:
            output = eval_quality.run_sql("psql", "SELECT 1;", timeout=2.5)

        self.assertEqual(output, "row")
        _, kwargs = runner.call_args
        self.assertEqual(kwargs["timeout"], 2.5)
        self.assertTrue(kwargs["shell"])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["input"], "SELECT 1;")

    def test_run_sql_without_timeout_omits_timeout_kwarg(self):
        completed = mock.Mock(returncode=0, stdout="row\n", stderr="")
        with mock.patch.object(
            eval_quality.subprocess, "run", return_value=completed
        ) as runner:
            eval_quality.run_sql("psql", "SELECT 1;")

        _, kwargs = runner.call_args
        self.assertNotIn("timeout", kwargs)

    def test_run_sql_translates_subprocess_timeout(self):
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(
                cmd="psql", timeout=kwargs.get("timeout"), output="partial"
            )

        with mock.patch.object(eval_quality.subprocess, "run", side_effect=raise_timeout):
            with self.assertRaises(eval_quality.SqlTimeout) as caught:
                eval_quality.run_sql("psql", "SELECT 1;", timeout=1.25)

        self.assertEqual(caught.exception.timeout_seconds, 1.25)
        self.assertEqual(caught.exception.stdout, "partial")

    def test_effective_timeout_without_budget_returns_explicit(self):
        self.assertEqual(eval_quality.effective_timeout(None, 5), 5)
        self.assertIsNone(eval_quality.effective_timeout(None, None))

    def test_effective_timeout_caps_by_remaining_deadline(self):
        clock = FakeClock()
        budget = execution_budget.ExecutionBudget(
            execution_budget.BudgetConfig(
                max_iterations=5, max_queries=5, deadline_seconds=10.0
            ),
            clock=clock,
        )
        clock.advance(3.0)

        self.assertEqual(eval_quality.effective_timeout(budget, 30), 7.0)
        self.assertEqual(eval_quality.effective_timeout(budget, 2), 2)
        self.assertEqual(eval_quality.effective_timeout(budget, None), 7.0)

    def test_effective_timeout_deadline_exhausted_raises(self):
        clock = FakeClock()
        budget = execution_budget.ExecutionBudget(
            execution_budget.BudgetConfig(
                max_iterations=5, max_queries=5, deadline_seconds=5.0
            ),
            clock=clock,
        )
        clock.advance(5.0)

        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            eval_quality.effective_timeout(budget, 30)

        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_DEADLINE)

    def test_evaluate_queries_passes_capped_timeout_to_runner(self):
        clock = FakeClock()
        budget = execution_budget.ExecutionBudget(
            execution_budget.BudgetConfig(
                max_iterations=5, max_queries=5, deadline_seconds=10.0
            ),
            clock=clock,
        )
        clock.advance(4.0)
        seen = []

        def fake_run_sql(psql, sql, timeout=None):
            seen.append(timeout)
            return "1|0.5|lance\n"

        results, exhausted = eval_quality.evaluate_queries(
            queries=self.make_fixture(2)["queries"],
            psql="psql",
            lance_uri="/tmp/x.lance",
            k=3,
            budget=budget,
            query_timeout_seconds=30,
            run_sql_fn=fake_run_sql,
        )

        self.assertIsNone(exhausted)
        self.assertEqual(len(results), 2)
        self.assertEqual(seen, [6.0, 6.0])

    def test_evaluate_queries_deadline_exhausted_does_not_call_runner(self):
        clock = FakeClock()
        budget = execution_budget.ExecutionBudget(
            execution_budget.BudgetConfig(
                max_iterations=5, max_queries=5, deadline_seconds=5.0
            ),
            clock=clock,
        )
        clock.advance(5.0)
        calls = []

        def fake_run_sql(psql, sql, timeout=None):
            calls.append(sql)
            return ""

        results, exhausted = eval_quality.evaluate_queries(
            queries=self.make_fixture(2)["queries"],
            psql="psql",
            lance_uri="/tmp/x.lance",
            k=3,
            budget=budget,
            query_timeout_seconds=30,
            run_sql_fn=fake_run_sql,
        )

        self.assertEqual(calls, [])
        self.assertEqual(results, [])
        self.assertEqual(exhausted.limit_kind, execution_budget.LIMIT_DEADLINE)

    def test_evaluate_queries_query_timeout_preserves_completed_results(self):
        fixture = self.make_fixture(3)
        calls = []

        def fake_run_sql(psql, sql, timeout=None):
            calls.append(sql)
            if len(calls) == 1:
                return "1|0.5|lance\n"
            raise eval_quality.SqlTimeout(timeout_seconds=timeout)

        with self.assertRaises(eval_quality.SqlTimeout) as caught:
            eval_quality.evaluate_queries(
                queries=fixture["queries"],
                psql="psql",
                lance_uri="/tmp/x.lance",
                k=3,
                query_timeout_seconds=5,
                run_sql_fn=fake_run_sql,
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(caught.exception.partial_results), 1)
        self.assertEqual(caught.exception.stage, "query")
        self.assertEqual(caught.exception.timeout_seconds, 5)

    def test_main_without_timeout_preserves_success_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = self.write_fixture(tmp, query_count=2)
            output = Path(tmp) / "quality.json"
            seen = []

            def fake_run_sql(psql, sql, timeout=None):
                seen.append(timeout)
                if "hybrid_warc_search" in sql:
                    return "1|0.5|lance\n"
                return ""

            argv = [
                "eval_quality.py",
                "--fixture-json",
                str(fixture_path),
                "--json-output",
                str(output),
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                eval_quality, "run_sql", side_effect=fake_run_sql
            ):
                exit_code = eval_quality.main()

            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["hit_rate_at_k"], 1.0)
        self.assertEqual(payload["mrr_at_k"], 1.0)
        self.assertEqual(seen, [None, None, None])

    def test_main_setup_timeout_emits_non_success_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = self.write_fixture(tmp)
            output = Path(tmp) / "quality.json"
            markdown = Path(tmp) / "quality.md"
            calls = []

            def fake_run_sql(psql, sql, timeout=None):
                calls.append((sql, timeout))
                raise eval_quality.SqlTimeout(timeout_seconds=timeout)

            argv = [
                "eval_quality.py",
                "--fixture-json",
                str(fixture_path),
                "--query-timeout-seconds",
                "5",
                "--json-output",
                str(output),
                "--markdown-output",
                str(markdown),
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                eval_quality, "run_sql", side_effect=fake_run_sql
            ):
                exit_code = eval_quality.main()

            payload = json.loads(output.read_text(encoding="utf-8"))
            markdown_text = markdown.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 4)
        self.assertEqual(len(calls), 1)
        self.assertEqual(payload["status"], eval_quality.STATUS_TIMEOUT)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["timeout"]["stage"], "setup")
        self.assertEqual(payload["timeout"]["limit_seconds"], 5.0)
        self.assertEqual(payload["results"], [])
        self.assertFalse(payload["timeout"]["partial_result_preserved"])
        observation = payload["timeout"]["timeout_observation"]
        self.assertEqual("unknown", observation["statement_outcome"])
        self.assertEqual("unobserved", observation["backend_state_after_timeout"])
        self.assertEqual("unverified", observation["cancellation_completion"])
        self.assertEqual("unverified", observation["transaction_cleanup"])
        self.assertNotIn("hit_rate_at_k", payload)
        self.assertIn(eval_quality.STATUS_TIMEOUT, markdown_text)

    def test_main_query_timeout_preserves_partial_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = self.write_fixture(tmp, query_count=2)
            output = Path(tmp) / "quality.json"
            markdown = Path(tmp) / "quality.md"
            budget_status = Path(tmp) / "budget.json"
            calls = []

            def fake_run_sql(psql, sql, timeout=None):
                calls.append((sql, timeout))
                if "hybrid_warc_search" not in sql:
                    return ""
                if len([call for call in calls if "hybrid_warc_search" in call[0]]) >= 2:
                    raise eval_quality.SqlTimeout(timeout_seconds=timeout)
                return "1|0.5|lance\n"

            argv = [
                "eval_quality.py",
                "--fixture-json",
                str(fixture_path),
                "--query-timeout-seconds",
                "5",
                "--json-output",
                str(output),
                "--markdown-output",
                str(markdown),
                "--budget-status-json",
                str(budget_status),
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                eval_quality, "run_sql", side_effect=fake_run_sql
            ):
                exit_code = eval_quality.main()

            payload = json.loads(output.read_text(encoding="utf-8"))
            markdown_text = markdown.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 4)
        self.assertEqual(len(calls), 3)
        self.assertEqual(payload["status"], eval_quality.STATUS_TIMEOUT)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["timeout"]["stage"], "query")
        self.assertEqual(payload["timeout"]["limit_seconds"], 5.0)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["timeout"]["partial_result_count"], 1)
        self.assertTrue(payload["timeout"]["partial_result_preserved"])
        self.assertEqual(
            "unknown", payload["timeout"]["timeout_observation"]["statement_outcome"]
        )
        self.assertNotIn("hit_rate_at_k", payload)
        self.assertFalse(budget_status.exists())
        self.assertIn("query", markdown_text)


class ServerStatementTimeoutTests(unittest.TestCase):
    def make_fixture(self, query_count=2):
        return {
            "name": "server-timeout-fixture",
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

    def write_fixture(self, tmp, query_count=2):
        fixture_path = Path(tmp) / "fixture.json"
        fixture_path.write_text(
            json.dumps(self.make_fixture(query_count)),
            encoding="utf-8",
        )
        return fixture_path

    def test_validate_statement_timeout_boundaries(self):
        self.assertIsNone(eval_quality.validate_query_statement_timeout(None))
        self.assertEqual(
            Decimal("1.000"),
            eval_quality.validate_query_statement_timeout(0.001),
        )
        self.assertEqual(
            Decimal("5000"),
            eval_quality.validate_query_statement_timeout(5),
        )
        self.assertEqual(
            Decimal("2500"),
            eval_quality.validate_query_statement_timeout(Decimal("2.5")),
        )
        self.assertEqual(
            Decimal("2147483647"),
            eval_quality.validate_query_statement_timeout(2147483.647),
        )

    def test_validate_statement_timeout_rejects_invalid(self):
        for value in (
            0,
            -1,
            -0.5,
            0.0009,
            2147483.648,
            math.nan,
            math.inf,
            -math.inf,
            True,
            "",
            "abc",
            "nan",
            "inf",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    eval_quality.validate_query_statement_timeout(value)

    def test_query_sql_wraps_only_when_server_limit_present(self):
        query = self.make_fixture(1)["queries"][0]

        plain = eval_quality.query_sql(query, "/tmp/x.lance", 3)
        self.assertNotIn("SET LOCAL", plain)
        self.assertNotIn("ON_ERROR_STOP", plain)
        self.assertIsNone(eval_quality.statement_timeout_ms_from_sql(plain))

        wrapped = eval_quality.query_sql(
            query, "/tmp/x.lance", 3, statement_timeout_ms=250
        )
        self.assertIn("\\set ON_ERROR_STOP on", wrapped)
        self.assertIn("\\set VERBOSITY verbose", wrapped)
        self.assertIn("BEGIN;", wrapped)
        self.assertIn("SET LOCAL statement_timeout = 250;", wrapped)
        self.assertIn("COMMIT;", wrapped)
        self.assertLess(wrapped.index("SET LOCAL"), wrapped.index("hybrid_warc_search"))
        self.assertLess(wrapped.index("hybrid_warc_search"), wrapped.index("COMMIT;"))
        self.assertEqual(250, eval_quality.statement_timeout_ms_from_sql(wrapped))

        self.assertEqual(
            [{"doc_id": 1, "score": 0.5, "source": "lance"}],
            eval_quality.parse_hits("1|0.5|lance\n"),
        )

    def test_resolve_statement_timeout_picks_shortest_source(self):
        requested = Decimal("5000")
        self.assertEqual(
            (5000, "server_statement"),
            eval_quality.resolve_statement_timeout_ms(
                requested, client_timeout_seconds=None, budget=None
            ),
        )
        self.assertEqual(
            (2000, "client"),
            eval_quality.resolve_statement_timeout_ms(
                requested, client_timeout_seconds=2.0, budget=None
            ),
        )
        self.assertEqual(
            (2, "server_statement"),
            eval_quality.resolve_statement_timeout_ms(
                Decimal("2.5"), client_timeout_seconds=None, budget=None
            ),
        )

        clock = FakeClock()
        budget = execution_budget.ExecutionBudget(
            execution_budget.BudgetConfig(
                max_iterations=5, max_queries=5, deadline_seconds=10.0
            ),
            clock=clock,
        )
        clock.advance(7.0)
        self.assertEqual(
            (3000, "run_budget"),
            eval_quality.resolve_statement_timeout_ms(
                requested, client_timeout_seconds=None, budget=budget
            ),
        )

    def test_resolve_statement_timeout_below_one_ms_returns_none(self):
        requested = Decimal("5000")
        self.assertEqual(
            (None, "client"),
            eval_quality.resolve_statement_timeout_ms(
                requested, client_timeout_seconds=0.0005, budget=None
            ),
        )

        clock = FakeClock()
        budget = execution_budget.ExecutionBudget(
            execution_budget.BudgetConfig(
                max_iterations=5, max_queries=5, deadline_seconds=10.0
            ),
            clock=clock,
        )
        clock.advance(9.9995)
        self.assertEqual(
            (None, "run_budget"),
            eval_quality.resolve_statement_timeout_ms(
                requested, client_timeout_seconds=None, budget=budget
            ),
        )

    def test_evaluate_queries_injects_server_wrapper_per_query(self):
        seen = []

        def fake_run_sql(psql, sql, timeout=None):
            seen.append(sql)
            return "1|0.5|lance\n"

        results, exhausted = eval_quality.evaluate_queries(
            queries=self.make_fixture(2)["queries"],
            psql="psql",
            lance_uri="/tmp/x.lance",
            k=3,
            statement_timeout_ms=Decimal("5000"),
            run_sql_fn=fake_run_sql,
        )

        self.assertIsNone(exhausted)
        self.assertEqual(2, len(results))
        self.assertEqual(2, len(seen))
        for sql in seen:
            self.assertIn("SET LOCAL statement_timeout = 5000;", sql)

    def test_evaluate_queries_caps_server_limit_by_client(self):
        seen = []

        def fake_run_sql(psql, sql, timeout=None):
            seen.append(sql)
            return "1|0.5|lance\n"

        eval_quality.evaluate_queries(
            queries=self.make_fixture(1)["queries"],
            psql="psql",
            lance_uri="/tmp/x.lance",
            k=3,
            query_timeout_seconds=2,
            statement_timeout_ms=Decimal("5000"),
            run_sql_fn=fake_run_sql,
        )

        self.assertIn("SET LOCAL statement_timeout = 2000;", seen[0])

    def test_evaluate_queries_sub_ms_client_cap_does_not_start_query(self):
        calls = []

        def fake_run_sql(psql, sql, timeout=None):
            calls.append(sql)
            return "1|0.5|lance\n"

        with self.assertRaises(eval_quality.SqlTimeout) as caught:
            eval_quality.evaluate_queries(
                queries=self.make_fixture(2)["queries"],
                psql="psql",
                lance_uri="/tmp/x.lance",
                k=3,
                query_timeout_seconds=0.0005,
                statement_timeout_ms=Decimal("5000"),
                run_sql_fn=fake_run_sql,
            )

        self.assertEqual([], calls)
        self.assertTrue(caught.exception.pre_run)
        self.assertEqual("query", caught.exception.stage)
        self.assertEqual([], caught.exception.partial_results)

    def test_evaluate_queries_sub_ms_budget_cap_exhausts_without_query(self):
        clock = FakeClock()
        budget = execution_budget.ExecutionBudget(
            execution_budget.BudgetConfig(
                max_iterations=5, max_queries=5, deadline_seconds=10.0
            ),
            clock=clock,
        )
        clock.advance(9.9995)
        calls = []

        def fake_run_sql(psql, sql, timeout=None):
            calls.append(sql)
            return "1|0.5|lance\n"

        results, exhausted = eval_quality.evaluate_queries(
            queries=self.make_fixture(2)["queries"],
            psql="psql",
            lance_uri="/tmp/x.lance",
            k=3,
            budget=budget,
            statement_timeout_ms=Decimal("5000"),
            run_sql_fn=fake_run_sql,
        )

        self.assertEqual([], calls)
        self.assertEqual([], results)
        self.assertEqual(execution_budget.LIMIT_DEADLINE, exhausted.limit_kind)

    def test_statement_timeout_propagates_with_diagnostics(self):
        calls = []

        def fake_run_sql(psql, sql, timeout=None):
            calls.append(sql)
            if len(calls) == 1:
                return "1|0.5|lance\n"
            raise eval_quality.StatementTimeout(
                stderr="ERROR:  57014: canceling statement due to statement timeout\n",
                sqlstate="57014",
                effective_statement_timeout_ms=999,
            )

        with self.assertRaises(eval_quality.StatementTimeout) as caught:
            eval_quality.evaluate_queries(
                queries=self.make_fixture(2)["queries"],
                psql="psql",
                lance_uri="/tmp/x.lance",
                k=3,
                statement_timeout_ms=Decimal("5000"),
                run_sql_fn=fake_run_sql,
            )

        error = caught.exception
        self.assertEqual("query", error.stage)
        self.assertEqual(1, len(error.partial_results))
        self.assertEqual(5000, error.requested_statement_timeout_ms)
        self.assertEqual(5000, error.effective_statement_timeout_ms)
        self.assertEqual("server_statement", error.timeout_source)

    def test_main_server_statement_timeout_emits_non_success_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = self.write_fixture(tmp, query_count=2)
            output = Path(tmp) / "quality.json"
            markdown = Path(tmp) / "quality.md"
            query_calls = 0

            def fake_run_sql(psql, sql, timeout=None):
                nonlocal query_calls
                if "hybrid_warc_search" not in sql:
                    return ""
                query_calls += 1
                if query_calls == 1:
                    return "1|0.5|lance\n"
                raise eval_quality.StatementTimeout(
                    stderr="ERROR:  57014: canceling statement due to statement timeout\n",
                    sqlstate="57014",
                )

            argv = [
                "eval_quality.py",
                "--fixture-json",
                str(fixture_path),
                "--query-statement-timeout-seconds",
                "5",
                "--json-output",
                str(output),
                "--markdown-output",
                str(markdown),
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                eval_quality, "run_sql", side_effect=fake_run_sql
            ):
                exit_code = eval_quality.main()

            payload = json.loads(output.read_text(encoding="utf-8"))
            markdown_text = markdown.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 4)
        self.assertEqual(eval_quality.STATUS_TIMEOUT, payload["status"])
        self.assertFalse(payload["success"])
        timeout = payload["timeout"]
        self.assertEqual("query", timeout["stage"])
        self.assertFalse(timeout["client_side_only"])
        self.assertEqual("server_statement", timeout["timeout_source"])
        self.assertEqual(5000, timeout["requested_statement_timeout_ms"])
        self.assertEqual(5000, timeout["effective_statement_timeout_ms"])
        self.assertEqual("57014", timeout["sqlstate"])
        self.assertEqual(
            "server_statement_timeout_reported",
            timeout["timeout_observation"]["statement_outcome"],
        )
        self.assertEqual(
            "unobserved", timeout["timeout_observation"]["backend_state_after_timeout"]
        )
        self.assertEqual(
            "unverified", timeout["timeout_observation"]["cancellation_completion"]
        )
        self.assertEqual(
            "unverified", timeout["timeout_observation"]["transaction_cleanup"]
        )
        self.assertEqual(1, len(payload["results"]))
        self.assertNotIn("hit_rate_at_k", payload)
        self.assertIn("statement_timeout", markdown_text)
        self.assertIn("rollback completion", markdown_text)

    def test_main_rejects_invalid_statement_timeout_before_sql(self):
        for raw in ("0", "-1", "nan", "inf", "0.0009", "2147483.648"):
            with self.subTest(raw=raw):
                with tempfile.TemporaryDirectory() as tmp:
                    output = Path(tmp) / "quality.json"
                    argv = [
                        "eval_quality.py",
                        "--query-statement-timeout-seconds",
                        raw,
                        "--json-output",
                        str(output),
                    ]
                    with mock.patch.object(sys, "argv", argv), mock.patch.object(
                        eval_quality, "run_sql"
                    ) as run_sql:
                        exit_code = eval_quality.main()

                    self.assertEqual(exit_code, 2)
                    run_sql.assert_not_called()
                    self.assertFalse(output.exists())

    def test_main_without_statement_timeout_keeps_plain_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = self.write_fixture(tmp, query_count=1)
            output = Path(tmp) / "quality.json"
            seen = []

            def fake_run_sql(psql, sql, timeout=None):
                seen.append(sql)
                return "1|0.5|lance\n" if "hybrid_warc_search" in sql else ""

            argv = [
                "eval_quality.py",
                "--fixture-json",
                str(fixture_path),
                "--json-output",
                str(output),
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                eval_quality, "run_sql", side_effect=fake_run_sql
            ):
                exit_code = eval_quality.main()

        self.assertEqual(exit_code, 0)
        self.assertFalse(any("SET LOCAL" in sql for sql in seen))

    def test_run_sql_classifies_only_confirmed_statement_timeout(self):
        wrapped = eval_quality.query_sql(
            self.make_fixture(1)["queries"][0], "/tmp/x.lance", 3,
            statement_timeout_ms=250,
        )

        confirmed = mock.Mock(
            returncode=3,
            stdout="",
            stderr="ERROR:  57014: canceling statement due to statement timeout\n",
        )
        with mock.patch.object(eval_quality.subprocess, "run", return_value=confirmed):
            with self.assertRaises(eval_quality.StatementTimeout) as caught:
                eval_quality.run_sql("psql", wrapped)
        self.assertEqual("57014", caught.exception.sqlstate)
        self.assertEqual(250, caught.exception.effective_statement_timeout_ms)

        user_cancel = mock.Mock(
            returncode=3,
            stdout="",
            stderr="ERROR:  57014: canceling statement due to user request\n",
        )
        with mock.patch.object(eval_quality.subprocess, "run", return_value=user_cancel):
            with self.assertRaises(SystemExit):
                eval_quality.run_sql("psql", wrapped)

        no_marker = mock.Mock(
            returncode=3,
            stdout="",
            stderr="ERROR:  57014: canceling statement due to statement timeout\n",
        )
        with mock.patch.object(eval_quality.subprocess, "run", return_value=no_marker):
            with self.assertRaises(SystemExit):
                eval_quality.run_sql("psql", "SELECT pg_sleep(5);")

    def test_artifact_status_errors_reject_server_timeout(self):
        payload = {
            "status": eval_quality.STATUS_TIMEOUT,
            "success": False,
            "timeout": {
                "stage": "query",
                "client_side_only": False,
                "timeout_source": "server_statement",
            },
            "results": [],
        }
        kind = eval_quality.artifact_status_kind(payload)
        self.assertEqual(eval_quality.STATUS_KIND_NON_SUCCESS, kind)
        errors = eval_quality.artifact_status_errors(payload, kind)
        self.assertTrue(any("non-success status" in error for error in errors))


class TimeoutObservationContractTests(unittest.TestCase):
    def make_fixture(self, query_count=1):
        return {
            "name": "observation-fixture",
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

    def write_fixture(self, tmp, query_count=1):
        fixture_path = Path(tmp) / "fixture.json"
        fixture_path.write_text(
            json.dumps(self.make_fixture(query_count)), encoding="utf-8"
        )
        return fixture_path

    def test_timeout_payload_defaults_to_unknown_observation(self):
        payload = eval_quality.timeout_payload(
            fixture=self.make_fixture(),
            results=[],
            k=3,
            lance_uri="/tmp/x.lance",
            stage="setup",
            limit_seconds=5.0,
        )
        observation = payload["timeout"]["timeout_observation"]
        self.assertEqual(
            eval_quality.STATEMENT_OUTCOME_UNKNOWN, observation["statement_outcome"]
        )
        self.assertEqual(
            eval_quality.BACKEND_STATE_AFTER_TIMEOUT_UNOBSERVED,
            observation["backend_state_after_timeout"],
        )
        self.assertEqual(
            eval_quality.CANCELLATION_COMPLETION_UNVERIFIED,
            observation["cancellation_completion"],
        )
        self.assertEqual(
            eval_quality.TRANSACTION_CLEANUP_UNVERIFIED,
            observation["transaction_cleanup"],
        )

    def test_timeout_payload_server_outcome_is_reported(self):
        payload = eval_quality.timeout_payload(
            fixture=self.make_fixture(),
            results=[],
            k=3,
            lance_uri="/tmp/x.lance",
            stage="query",
            limit_seconds=5.0,
            client_side_only=False,
            statement_outcome=eval_quality.STATEMENT_OUTCOME_SERVER_TIMEOUT,
        )
        observation = payload["timeout"]["timeout_observation"]
        self.assertFalse(payload["timeout"]["client_side_only"])
        self.assertEqual(
            eval_quality.STATEMENT_OUTCOME_SERVER_TIMEOUT,
            observation["statement_outcome"],
        )
        self.assertEqual(
            eval_quality.BACKEND_STATE_AFTER_TIMEOUT_UNOBSERVED,
            observation["backend_state_after_timeout"],
        )

    def test_success_budget_and_general_sql_have_no_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_path = self.write_fixture(tmp)
            output = Path(tmp) / "quality.json"
            argv = [
                "eval_quality.py",
                "--fixture-json",
                str(fixture_path),
                "--json-output",
                str(output),
            ]

            def fake_run_sql(psql, sql, timeout=None):
                return "1|0.5|lance\n" if "hybrid_warc_search" in sql else ""

            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                eval_quality, "run_sql", side_effect=fake_run_sql
            ):
                exit_code = eval_quality.main()

            success = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertNotIn("timeout", success)
        self.assertNotIn("timeout_observation", success)

        exhausted = eval_quality.exhausted_payload(
            fixture=self.make_fixture(),
            results=[],
            k=3,
            lance_uri="/tmp/x.lance",
            exhausted=execution_budget.BudgetExhausted(
                limit_kind=execution_budget.LIMIT_QUERIES,
                used=1,
                limit=1,
                detail="query budget already consumed",
            ),
        )
        self.assertNotIn("timeout", exhausted)
        self.assertNotIn("timeout_observation", exhausted)

        generic = mock.Mock(returncode=3, stdout="", stderr="ERROR:  syntax error\n")
        with mock.patch.object(eval_quality.subprocess, "run", return_value=generic):
            with self.assertRaises(SystemExit):
                eval_quality.run_sql("psql", "SELECT 1;")

    def test_client_timeout_expired_stderr_cannot_become_server_outcome(self):
        expired = subprocess.TimeoutExpired(
            cmd="psql",
            timeout=5.0,
            output="",
            stderr="ERROR:  57014: canceling statement due to statement timeout\n",
        )
        with mock.patch.object(eval_quality.subprocess, "run", side_effect=expired):
            with self.assertRaises(eval_quality.SqlTimeout) as caught:
                eval_quality.run_sql("psql", "SELECT pg_sleep(5);", timeout=5.0)

        self.assertNotIsInstance(caught.exception, eval_quality.StatementTimeout)
        self.assertTrue(eval_quality.is_statement_timeout_diagnostic(caught.exception.stderr))


class MakefileStatementTimeoutTests(unittest.TestCase):
    def dry_run(self, target, **variables):
        command = ["make", "-n", target]
        command.extend(f"{key}={value}" for key, value in variables.items())
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout

    def test_eval_quality_omits_statement_flag_without_variable(self):
        output = self.dry_run("eval-quality")
        self.assertNotIn("--query-statement-timeout-seconds", output)

    def test_eval_quality_passes_statement_flag_with_variable(self):
        output = self.dry_run("eval-quality", QUERY_STATEMENT_TIMEOUT_SECONDS="2.5")
        self.assertIn('--query-statement-timeout-seconds "2.5"', output)

    def test_eval_quality_budget_passes_statement_flag_with_variable(self):
        output = self.dry_run(
            "eval-quality-budget",
            ANALYSIS_MAX_ITERATIONS="1",
            ANALYSIS_MAX_QUERIES="1",
            ANALYSIS_DEADLINE_SECONDS="5",
            QUERY_STATEMENT_TIMEOUT_SECONDS="4",
        )
        self.assertIn('--max-queries "1"', output)
        self.assertIn('--query-statement-timeout-seconds "4"', output)


if __name__ == "__main__":
    unittest.main()