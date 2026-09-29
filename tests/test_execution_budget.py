import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import execution_budget  # noqa: E402


class FakeClock:
    def __init__(self, start=100.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_budget(*, iterations=2, queries=2, deadline=10.0, clock=None):
    config = execution_budget.BudgetConfig(
        max_iterations=iterations,
        max_queries=queries,
        deadline_seconds=deadline,
    )
    return execution_budget.ExecutionBudget(config, clock=clock or FakeClock())


class BudgetConfigTests(unittest.TestCase):
    def test_accepts_valid_config(self):
        config = execution_budget.budget_config(
            {
                "max_iterations": 3,
                "max_queries": 7,
                "deadline_seconds": 1.5,
            }
        )
        self.assertEqual(config.max_iterations, 3)
        self.assertEqual(config.max_queries, 7)
        self.assertEqual(config.deadline_seconds, 1.5)

    def test_rejects_missing_values(self):
        for missing in ("max_iterations", "max_queries", "deadline_seconds"):
            values = {"max_iterations": 1, "max_queries": 1, "deadline_seconds": 1.0}
            values[missing] = None
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(
                    execution_budget.BudgetConfigError, f"missing budget value.*{missing}"
                ):
                    execution_budget.budget_config(values)

    def test_rejects_wrong_types(self):
        values = {"max_iterations": "3", "max_queries": 1, "deadline_seconds": 1.0}
        with self.assertRaisesRegex(execution_budget.BudgetConfigError, "expected integer"):
            execution_budget.budget_config(values)

        values = {"max_iterations": True, "max_queries": 1, "deadline_seconds": 1.0}
        with self.assertRaisesRegex(execution_budget.BudgetConfigError, "expected integer"):
            execution_budget.budget_config(values)

        values = {"max_iterations": 1, "max_queries": 1, "deadline_seconds": "5"}
        with self.assertRaisesRegex(execution_budget.BudgetConfigError, "expected number"):
            execution_budget.budget_config(values)

    def test_rejects_zero_and_negative_values(self):
        cases = {
            "max_iterations": (0, -1),
            "max_queries": (0, -1),
            "deadline_seconds": (0, -1, -0.5),
        }
        for field, values in cases.items():
            for value in values:
                values_map = {"max_iterations": 1, "max_queries": 1, "deadline_seconds": 1.0}
                values_map[field] = value
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(
                        execution_budget.BudgetConfigError, "greater than zero"
                    ):
                        execution_budget.budget_config(values_map)

    def test_rejects_non_finite_deadline(self):
        values = {"max_iterations": 1, "max_queries": 1, "deadline_seconds": float("inf")}
        with self.assertRaisesRegex(execution_budget.BudgetConfigError, "finite"):
            execution_budget.budget_config(values)


class ExactBoundaryTests(unittest.TestCase):
    def test_iteration_boundary_blocks_n_plus_one_callback(self):
        budget = make_budget(iterations=2, queries=10)
        invoked = []
        for index in range(2):
            budget.guard(execution_budget.ITERATION, invoked.append, index)

        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.guard(execution_budget.ITERATION, invoked.append, 99)

        self.assertEqual(invoked, [0, 1])
        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_ITERATIONS)
        self.assertEqual(caught.exception.used, 2)
        self.assertEqual(caught.exception.limit, 2)

    def test_query_boundary_blocks_n_plus_one_callback(self):
        budget = make_budget(iterations=10, queries=1)
        invoked = []
        budget.guard(execution_budget.QUERY, invoked.append, "q0")

        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.guard(execution_budget.QUERY, invoked.append, "q1")

        self.assertEqual(invoked, ["q0"])
        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_QUERIES)

    def test_unknown_kind_is_rejected(self):
        budget = make_budget()
        with self.assertRaisesRegex(ValueError, "unknown budget kind"):
            budget.charge("pages")


class SharedBudgetTests(unittest.TestCase):
    def test_retries_share_iteration_counters(self):
        budget = make_budget(iterations=2, queries=10)
        budget.charge_iteration()
        budget.charge_retry()

        self.assertEqual(budget.iterations, 2)
        self.assertEqual(budget.retries, 1)
        with self.assertRaises(execution_budget.BudgetExhausted):
            budget.charge_retry()

    def test_child_work_cannot_reset_counters(self):
        budget = make_budget(iterations=10, queries=2)

        def child(shared):
            shared.charge_query()

        child(budget)
        child(budget)
        self.assertEqual(budget.queries, 2)

        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            child(budget)
        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_QUERIES)


class DeadlineTests(unittest.TestCase):
    def test_deadline_uses_injected_monotonic_clock(self):
        clock = FakeClock()
        budget = make_budget(iterations=5, queries=5, deadline=10.0, clock=clock)

        clock.advance(4.0)
        self.assertEqual(budget.remaining_seconds(), 6.0)
        budget.check_deadline()

        clock.advance(6.0)
        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.check_deadline()

        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_DEADLINE)
        self.assertEqual(caught.exception.used, 10.0)
        self.assertEqual(caught.exception.limit, 10.0)

    def test_deadline_blocks_next_unit(self):
        clock = FakeClock()
        budget = make_budget(iterations=5, queries=5, deadline=5.0, clock=clock)
        clock.advance(5.0)

        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.charge_iteration()

        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_DEADLINE)
        self.assertEqual(budget.iterations, 0)

    def test_cap_timeout_by_remaining_time(self):
        clock = FakeClock()
        budget = make_budget(iterations=5, queries=5, deadline=10.0, clock=clock)
        clock.advance(3.0)

        self.assertEqual(budget.cap_timeout(30), 7.0)
        self.assertEqual(budget.cap_timeout(2), 2)

        clock.advance(7.0)
        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.cap_timeout(5)
        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_DEADLINE)

    def test_exhaustion_status_is_machine_readable(self):
        budget = make_budget(iterations=1, queries=1)
        budget.charge_query()
        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.charge_query()

        status = caught.exception.to_status(partial_results=[{"name": "q0"}])
        self.assertEqual(status["status"], "budget_exhausted")
        self.assertFalse(status["success"])
        self.assertEqual(status["limit_kind"], "max_queries")
        self.assertEqual(status["used"], 1)
        self.assertEqual(status["limit"], 1)
        self.assertTrue(status["partial_result_preserved"])
        self.assertEqual(status["partial_result_count"], 1)
        self.assertEqual(status["partial_results"], [{"name": "q0"}])

    def test_ok_status_is_machine_readable(self):
        budget = make_budget(iterations=5, queries=5)
        budget.charge_iteration()
        budget.charge_query()
        status = budget.status()
        self.assertEqual(status["status"], "ok")
        self.assertTrue(status["success"])
        self.assertEqual(status["max_iterations"], 5)
        self.assertEqual(status["used_iterations"], 1)
        self.assertEqual(status["used_queries"], 1)


class AtomicQueryUnitTests(unittest.TestCase):
    def test_unit_charges_iteration_and_query_together(self):
        budget = make_budget(iterations=5, queries=5)
        budget.charge_query_unit()
        budget.charge_query_unit()

        self.assertEqual(budget.iterations, 2)
        self.assertEqual(budget.queries, 2)

    def test_query_limit_first_does_not_consume_iteration(self):
        budget = make_budget(iterations=5, queries=2)
        budget.charge_query_unit()
        budget.charge_query_unit()

        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.charge_query_unit()

        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_QUERIES)
        self.assertEqual(caught.exception.used, 2)
        self.assertEqual(caught.exception.limit, 2)
        self.assertEqual(budget.iterations, 2)
        self.assertEqual(budget.queries, 2)

    def test_iteration_limit_first_does_not_consume_query(self):
        budget = make_budget(iterations=2, queries=5)
        budget.charge_query_unit()
        budget.charge_query_unit()

        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.charge_query_unit()

        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_ITERATIONS)
        self.assertEqual(caught.exception.used, 2)
        self.assertEqual(caught.exception.limit, 2)
        self.assertEqual(budget.iterations, 2)
        self.assertEqual(budget.queries, 2)

    def test_simultaneous_limits_report_iteration_deterministically(self):
        budget = make_budget(iterations=2, queries=2)
        budget.charge_query_unit()
        budget.charge_query_unit()

        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.charge_query_unit()

        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_ITERATIONS)
        self.assertEqual(budget.iterations, 2)
        self.assertEqual(budget.queries, 2)

    def test_deadline_blocks_unit_without_consuming_counters(self):
        clock = FakeClock()
        budget = make_budget(iterations=5, queries=5, deadline=5.0, clock=clock)
        clock.advance(5.0)

        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.charge_query_unit()

        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_DEADLINE)
        self.assertEqual(budget.iterations, 0)
        self.assertEqual(budget.queries, 0)

    def test_remaining_counters_after_partial_consumption(self):
        budget = make_budget(iterations=3, queries=2)
        budget.charge_query_unit()

        self.assertEqual(budget.iterations, 1)
        self.assertEqual(budget.queries, 1)
        budget.charge_query_unit()
        self.assertEqual(budget.iterations, 2)
        self.assertEqual(budget.queries, 2)

        with self.assertRaises(execution_budget.BudgetExhausted) as caught:
            budget.charge_query_unit()
        self.assertEqual(caught.exception.limit_kind, execution_budget.LIMIT_QUERIES)
        self.assertEqual(budget.iterations, 2)


class CliBoundaryTests(unittest.TestCase):
    def test_invalid_config_is_rejected_before_command_runs(self):
        calls = []

        def runner(*args, **kwargs):
            calls.append(args)
            raise AssertionError("command must not run with an invalid budget")

        exit_code = execution_budget.main(
            ["--max-iterations", "0", "--max-queries", "1", "--deadline-seconds", "5"],
            runner=runner,
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(calls, [])

    def test_missing_config_is_rejected(self):
        exit_code = execution_budget.main(["--max-iterations", "3"], runner=lambda *a, **k: None)
        self.assertEqual(exit_code, 2)

    def test_command_success_emits_ok_status(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "budget.json"
            exit_code = execution_budget.main(
                [
                    "--max-iterations",
                    "2",
                    "--max-queries",
                    "4",
                    "--deadline-seconds",
                    "30",
                    "--json-output",
                    str(output),
                    "--command",
                    "true",
                ],
                runner=runner,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["success"])

    def test_deadline_timeout_preserves_partial_output(self):
        def runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="partial stdout")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "budget.json"
            exit_code = execution_budget.main(
                [
                    "--max-iterations",
                    "2",
                    "--max-queries",
                    "4",
                    "--deadline-seconds",
                    "0.5",
                    "--json-output",
                    str(output),
                    "--command",
                    "sleep 10",
                ],
                runner=runner,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "budget_exhausted")
        self.assertFalse(payload["success"])
        self.assertEqual(payload["limit_kind"], "deadline_seconds")
        self.assertEqual(payload["limit"], 0.5)
        self.assertTrue(payload["partial_result_preserved"])
        self.assertEqual(payload["partial_results"]["stdout"], "partial stdout")


if __name__ == "__main__":
    unittest.main()
