#!/usr/bin/env python3
"""Apply committed pgwarc_lance vector upserts using PostgreSQL's durable outbox.

Run under a trusted worker role with a protected libpq credential source. This
process adds no scheduler; use the deployment's existing process supervision.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Any

PROCESS_SQL = "SELECT outbox_id, status, applied_version, queue_lag_ms FROM public.lance_outbox_process_next();"
_stop = threading.Event()
_child: subprocess.Popen[str] | None = None


def parse_result(output: str) -> dict[str, Any] | None:
    """Parse psql's unaligned one-row result; empty output means no ready job."""
    line = output.strip()
    if not line:
        return None
    fields = line.split("|")
    if len(fields) != 4:
        raise ValueError("worker query returned an unexpected row shape")
    event_id, status, version, lag_ms = fields
    if status not in {"completed", "pending", "failed"}:
        raise ValueError(f"worker query returned unexpected status {status!r}")
    return {
        "outbox_id": int(event_id),
        "status": status,
        "applied_version": int(version) if version else None,
        "queue_lag_ms": int(lag_ms),
    }


def _signal_handler(signum: int, _frame: object) -> None:
    _stop.set()
    child = _child
    if child is not None and child.poll() is None:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass


def process_one(psql: list[str], timeout: float) -> dict[str, Any] | None:
    global _child
    _child = subprocess.Popen(
        psql + ["-X", "-q", "-A", "-t", "-F", "|", "-v", "ON_ERROR_STOP=1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = _child.communicate(PROCESS_SQL + "\n", timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(_child.pid, signal.SIGTERM)
        try:
            _child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(_child.pid, signal.SIGKILL)
            _child.communicate()
        raise TimeoutError("outbox worker SQL call timed out")
    finally:
        process = _child
        _child = None
    if process.returncode:
        if _stop.is_set():
            return None
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else "psql failed"
        raise RuntimeError(detail)
    return parse_result(stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--psql",
        default=os.environ.get("PGWARC_PSQL", "psql"),
        help="psql executable and connection options (default: PGWARC_PSQL or psql)",
    )
    parser.add_argument("--once", action="store_true", help="process at most one ready job, then exit")
    parser.add_argument("--poll-interval-ms", type=int, default=1000, help="idle/retry polling interval (10..60000 ms)")
    parser.add_argument("--statement-timeout-seconds", type=float, default=300.0)
    args = parser.parse_args(argv)
    if not 10 <= args.poll_interval_ms <= 60_000:
        parser.error("--poll-interval-ms must be between 10 and 60000")
    if not 0 < args.statement_timeout_seconds <= 3600:
        parser.error("--statement-timeout-seconds must be in (0, 3600]")
    psql = shlex.split(args.psql)
    if not psql:
        parser.error("--psql must include an executable")

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    poll_seconds = args.poll_interval_ms / 1000
    connection_backoff = poll_seconds
    if args.once:
        try:
            result = process_one(psql, args.statement_timeout_seconds)
        except (RuntimeError, TimeoutError, ValueError) as exc:
            print(f"outbox worker error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result or {"status": "idle"}, sort_keys=True))
        return 0

    while not _stop.is_set():
        try:
            result = process_one(psql, args.statement_timeout_seconds)
            connection_backoff = poll_seconds
            if result is None:
                _stop.wait(poll_seconds)
            else:
                print(json.dumps(result, sort_keys=True), flush=True)
                if result["status"] != "completed":
                    _stop.wait(poll_seconds)
        except (RuntimeError, TimeoutError, ValueError) as exc:
            print(f"outbox worker error: {exc}", file=sys.stderr, flush=True)
            _stop.wait(connection_backoff)
            connection_backoff = min(60.0, max(poll_seconds, connection_backoff * 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
