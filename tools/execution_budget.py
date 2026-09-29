#!/usr/bin/env python3
"""Run-scoped execution budget shared by project analysis harnesses.

This module is the project-local boundary for the 2-hour analysis loop. The
external ``kilo-gpt-report-harness`` owns the report/loop orchestration, but the
budget contract lives here so every analysis run that opts in uses the same
dependency-free gate:

* Max iterations, max queries, and a wall-clock deadline are validated before
  any work starts. Missing, wrong-type, zero, or negative values are rejected.
* One :class:`ExecutionBudget` instance is shared across iterations, queries,
  retries, and child work. Retries and subtasks receive the same instance and
  therefore cannot reset counters.
* Quota is enforced exactly: the N-th unit is permitted and the N+1 callback is
  never invoked.
* The deadline uses ``time.monotonic`` (injectable for tests) and a query
  timeout can be capped by the remaining time.
* Exhaustion raises :class:`BudgetExhausted`, which renders a machine-readable
  ``budget_exhausted`` status carrying the limit kind, used and limit values,
  and whether partial results were preserved. Partial output is never labelled
  as success.

The module can also be used as a CLI boundary. ``--max-iterations``,
``--max-queries``, and ``--deadline-seconds`` are required and validated before
an optional ``--command`` is spawned, so a harness wrapper cannot start work
with an incomplete budget.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

LIMIT_ITERATIONS = "max_iterations"
LIMIT_QUERIES = "max_queries"
LIMIT_DEADLINE = "deadline_seconds"
REQUIRED_LIMITS = (LIMIT_ITERATIONS, LIMIT_QUERIES, LIMIT_DEADLINE)

STATUS_OK = "ok"
STATUS_EXHAUSTED = "budget_exhausted"
STATUS_FAILED = "failed"

ITERATION = "iteration"
QUERY = "query"
RETRY = "retry"
KINDS = (ITERATION, QUERY, RETRY)


class BudgetConfigError(ValueError):
    """Raised when an execution budget configuration is missing or invalid."""


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BudgetConfigError(f"{field}: expected integer, got {type(value).__name__}")
    if value <= 0:
        raise BudgetConfigError(f"{field}: must be greater than zero")
    return value


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BudgetConfigError(f"{field}: expected number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise BudgetConfigError(f"{field}: must be finite")
    if number <= 0:
        raise BudgetConfigError(f"{field}: must be greater than zero")
    return number


@dataclass(frozen=True)
class BudgetConfig:
    """Validated limits for a single analysis run."""

    max_iterations: int
    max_queries: int
    deadline_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            LIMIT_ITERATIONS: self.max_iterations,
            LIMIT_QUERIES: self.max_queries,
            LIMIT_DEADLINE: self.deadline_seconds,
        }


def budget_config(values: Mapping[str, Any]) -> BudgetConfig:
    """Validate raw budget values, rejecting missing or invalid limits."""

    missing = [name for name in REQUIRED_LIMITS if values.get(name) is None]
    if missing:
        raise BudgetConfigError("missing budget value(s): " + ", ".join(missing))
    return BudgetConfig(
        max_iterations=_positive_int(values.get(LIMIT_ITERATIONS), LIMIT_ITERATIONS),
        max_queries=_positive_int(values.get(LIMIT_QUERIES), LIMIT_QUERIES),
        deadline_seconds=_positive_number(values.get(LIMIT_DEADLINE), LIMIT_DEADLINE),
    )


class BudgetExhausted(Exception):
    """Raised when a run-scoped budget limit is reached."""

    def __init__(
        self,
        *,
        limit_kind: str,
        used: float,
        limit: float,
        detail: str | None = None,
    ) -> None:
        self.limit_kind = limit_kind
        self.used = used
        self.limit = limit
        self.detail = detail or f"{limit_kind} budget reached"
        super().__init__(f"budget exhausted: {limit_kind} used={used} limit={limit}")

    def to_status(self, *, partial_results: Any = None) -> dict[str, Any]:
        """Render the machine-readable non-success exhaustion status."""

        status: dict[str, Any] = {
            "status": STATUS_EXHAUSTED,
            "success": False,
            "limit_kind": self.limit_kind,
            "used": self.used,
            "limit": self.limit,
            "partial_result_preserved": partial_results is not None,
            "partial_result_count": (
                len(partial_results)
                if isinstance(partial_results, (list, tuple, dict))
                else 0
            ),
            "detail": self.detail,
        }
        if partial_results is not None:
            status["partial_results"] = partial_results
        return status


class ExecutionBudget:
    """A single run-scoped budget shared across iterations, queries, retries."""

    def __init__(self, config: BudgetConfig, *, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self._clock = clock
        self._started_at = clock()
        self._iterations = 0
        self._queries = 0
        self._retries = 0

    @property
    def iterations(self) -> int:
        return self._iterations

    @property
    def queries(self) -> int:
        return self._queries

    @property
    def retries(self) -> int:
        return self._retries

    def elapsed_seconds(self) -> float:
        elapsed = self._clock() - self._started_at
        return elapsed if elapsed > 0 else 0.0

    def remaining_seconds(self) -> float:
        remaining = self.config.deadline_seconds - self.elapsed_seconds()
        return remaining if remaining > 0 else 0.0

    def check_deadline(self) -> None:
        elapsed = self.elapsed_seconds()
        if elapsed >= self.config.deadline_seconds:
            raise BudgetExhausted(
                limit_kind=LIMIT_DEADLINE,
                used=round(elapsed, 6),
                limit=self.config.deadline_seconds,
                detail="wall-clock deadline reached before the next unit",
            )

    def charge_iteration(self) -> "ExecutionBudget":
        self.check_deadline()
        if self._iterations >= self.config.max_iterations:
            raise BudgetExhausted(
                limit_kind=LIMIT_ITERATIONS,
                used=self._iterations,
                limit=self.config.max_iterations,
                detail="iteration budget already consumed",
            )
        self._iterations += 1
        return self

    def charge_query(self) -> "ExecutionBudget":
        self.check_deadline()
        if self._queries >= self.config.max_queries:
            raise BudgetExhausted(
                limit_kind=LIMIT_QUERIES,
                used=self._queries,
                limit=self.config.max_queries,
                detail="query budget already consumed",
            )
        self._queries += 1
        return self

    def charge_query_unit(self) -> "ExecutionBudget":
        """Atomically charge one iteration and one query for a query-loop unit.

        Both limits are checked before either counter is incremented, so a unit
        that would exceed either ``max_iterations`` or ``max_queries`` is
        blocked without consuming the other counter. This keeps the blocked
        N+1 attempt from inflating ``used_iterations`` when the query limit is
        the one that was reached first.

        When both limits are already reached the iteration limit takes
        precedence, matching the historical check order of the query loop.
        """

        self.check_deadline()
        if self._iterations >= self.config.max_iterations:
            raise BudgetExhausted(
                limit_kind=LIMIT_ITERATIONS,
                used=self._iterations,
                limit=self.config.max_iterations,
                detail="iteration budget already consumed",
            )
        if self._queries >= self.config.max_queries:
            raise BudgetExhausted(
                limit_kind=LIMIT_QUERIES,
                used=self._queries,
                limit=self.config.max_queries,
                detail="query budget already consumed",
            )
        self._iterations += 1
        self._queries += 1
        return self

    def charge_retry(self) -> "ExecutionBudget":
        """Charge a retry against the shared iteration quota.

        Retries reuse the same run-scoped counters, so a retry can never reset
        the iteration limit or grant extra work beyond ``max_iterations``.
        """

        self.charge_iteration()
        self._retries += 1
        return self

    def charge(self, kind: str) -> "ExecutionBudget":
        if kind == ITERATION:
            return self.charge_iteration()
        if kind == QUERY:
            return self.charge_query()
        if kind == RETRY:
            return self.charge_retry()
        raise ValueError(f"unknown budget kind: {kind!r}")

    def guard(self, kind: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Charge ``kind`` first, then invoke ``callback``.

        When the quota is exhausted the charge raises before ``callback`` is
        called, so the N+1 unit is never invoked.
        """

        self.charge(kind)
        return callback(*args, **kwargs)

    def cap_timeout(self, timeout_seconds: Any) -> float:
        """Cap an existing query timeout by the remaining run deadline."""

        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise BudgetConfigError("timeout_seconds: expected number")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise BudgetConfigError("timeout_seconds: must be finite and greater than zero")
        remaining = self.remaining_seconds()
        if remaining <= 0:
            self.check_deadline()
            raise BudgetExhausted(
                limit_kind=LIMIT_DEADLINE,
                used=round(self.elapsed_seconds(), 6),
                limit=self.config.deadline_seconds,
                detail="no remaining time to cap query timeout",
            )
        return min(timeout, remaining)

    def status(self) -> dict[str, Any]:
        return {
            "status": STATUS_OK,
            "success": True,
            **self.config.to_dict(),
            "used_iterations": self._iterations,
            "used_queries": self._queries,
            "used_retries": self._retries,
            "elapsed_seconds": round(self.elapsed_seconds(), 6),
            "remaining_seconds": round(self.remaining_seconds(), 6),
        }


def run_unit(
    budget: ExecutionBudget,
    kind: str,
    callback: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Convenience wrapper around :meth:`ExecutionBudget.guard`."""

    return budget.guard(kind, callback, *args, **kwargs)


def _write_status(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--deadline-seconds", type=float, default=None)
    parser.add_argument("--command", default=None)
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, runner: Callable[..., Any] = subprocess.run) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        config = budget_config(
            {
                LIMIT_ITERATIONS: args.max_iterations,
                LIMIT_QUERIES: args.max_queries,
                LIMIT_DEADLINE: args.deadline_seconds,
            }
        )
    except BudgetConfigError as error:
        print(f"execution budget rejected: {error}", file=sys.stderr)
        return 2

    budget = ExecutionBudget(config)
    if args.command is None:
        status = budget.status()
        _write_status(args.json_output, status)
        print(json.dumps(status, sort_keys=True))
        return 0

    try:
        completed = runner(
            args.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=budget.remaining_seconds(),
        )
    except subprocess.TimeoutExpired as expired:
        partial = {
            "stdout": expired.stdout or "",
            "stderr": expired.stderr or "",
        }
        status = BudgetExhausted(
            limit_kind=LIMIT_DEADLINE,
            used=round(budget.elapsed_seconds(), 6),
            limit=config.deadline_seconds,
            detail="command exceeded the run-scoped deadline",
        ).to_status(partial_results=partial)
        _write_status(args.json_output, status)
        print(json.dumps(status, sort_keys=True))
        return 3

    if completed.returncode != 0:
        status = {
            "status": STATUS_FAILED,
            "success": False,
            **config.to_dict(),
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        _write_status(args.json_output, status)
        print(json.dumps(status, sort_keys=True))
        return 1

    status = {**budget.status(), "exit_code": completed.returncode}
    _write_status(args.json_output, status)
    print(json.dumps(status, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
