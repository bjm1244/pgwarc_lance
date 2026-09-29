#!/usr/bin/env python3
"""Run and report PostgreSQL release smoke matrix results."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_NAME = re.compile(r"(TOKEN|SECRET|PASSWORD|KEY|COOKIE)", re.IGNORECASE)


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_pg_majors(value: str) -> list[int]:
    majors: list[int] = []
    for raw_part in re.split(r"[,\s]+", value.strip()):
        if not raw_part:
            continue
        try:
            majors.append(int(raw_part))
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"invalid PostgreSQL major {raw_part!r}"
            ) from error
    if not majors:
        raise argparse.ArgumentTypeError("at least one PostgreSQL major is required")
    return majors


def parse_make_var(value: str) -> str:
    name, separator, _ = value.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError(
            f"make var must use NAME=VALUE format: {value!r}"
        )
    return value


def is_sensitive_name(name: str) -> bool:
    return bool(SENSITIVE_NAME.search(name))


def redact_make_var(value: str) -> str:
    name, separator, raw_value = value.partition("=")
    if separator and is_sensitive_name(name):
        return f"{name}=<redacted>"
    if separator:
        return f"{name}={raw_value}"
    return value


def redact_command(command: list[str]) -> list[str]:
    return [redact_make_var(token) if "=" in token else token for token in command]


def shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def tail_lines(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-limit:])


def make_command(
    *,
    make_program: str,
    make_target: str,
    pg_major: int,
    make_vars: list[str],
) -> list[str]:
    return [make_program, f"PG_MAJOR={pg_major}", *make_vars, make_target]


def run_smoke(
    *,
    command: list[str],
    display_command: list[str],
    cwd: Path,
    output_tail_lines: int,
    runner: Any,
) -> dict[str, Any]:
    started = time.monotonic()
    error = None
    stdout = ""
    stderr = ""
    exit_code = None
    try:
        completed = runner(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except OSError as exc:
        error = f"could not execute command: {exc}"
        stderr = str(exc)
    duration_seconds = round(time.monotonic() - started, 3)

    return {
        "command": shell_join(display_command),
        "exit_code": exit_code,
        "passed": exit_code == 0 and error is None,
        "duration_seconds": duration_seconds,
        "stdout_tail": tail_lines(stdout, output_tail_lines),
        "stderr_tail": tail_lines(stderr, output_tail_lines),
        "error": error,
    }


def build_report(
    *,
    pg_majors: list[int],
    make_program: str,
    make_target: str,
    make_vars: list[str],
    cwd: Path,
    output_tail_lines: int,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for pg_major in pg_majors:
        command = make_command(
            make_program=make_program,
            make_target=make_target,
            pg_major=pg_major,
            make_vars=make_vars,
        )
        entry = run_smoke(
            command=command,
            display_command=redact_command(command),
            cwd=cwd,
            output_tail_lines=output_tail_lines,
            runner=runner,
        )
        entry["pg_major"] = pg_major
        results.append(entry)

    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "cwd": str(cwd),
        "make_program": make_program,
        "make_target": make_target,
        "make_vars": [redact_make_var(value) for value in make_vars],
        "pg_majors": pg_majors,
        "output_tail_lines": output_tail_lines,
        "all_passed": all(result["passed"] for result in results),
        "results": results,
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    make_vars = ", ".join(report["make_vars"]) if report["make_vars"] else "none"
    lines = [
        "# pgwarc_lance Release Smoke Matrix",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Target: `{report['make_target']}`",
        f"- Make vars: `{make_vars}`",
        f"- All passed: `{str(report['all_passed']).lower()}`",
        "",
        "| PostgreSQL | Status | Exit Code | Seconds | Command |",
        "|------------|--------|-----------|---------|---------|",
    ]
    for result in report["results"]:
        lines.append(
            "| {pg} | {status} | {exit_code} | {seconds} | `{command}` |".format(
                pg=result["pg_major"],
                status="pass" if result["passed"] else "fail",
                exit_code="n/a" if result["exit_code"] is None else result["exit_code"],
                seconds=result["duration_seconds"],
                command=markdown_cell(result["command"]),
            )
        )

    failing = [result for result in report["results"] if not result["passed"]]
    if failing:
        lines.extend(["", "## Failures", ""])
        for result in failing:
            lines.append(f"### PostgreSQL {result['pg_major']}")
            if result["error"]:
                lines.append(f"- Error: {markdown_cell(result['error'])}")
            lines.append(f"- Command: `{markdown_cell(result['command'])}`")
            if result["stderr_tail"]:
                lines.extend(["", "stderr tail:", "", "```text", result["stderr_tail"], "```"])
            if result["stdout_tail"]:
                lines.extend(["", "stdout tail:", "", "```text", result["stdout_tail"], "```"])
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, report: dict[str, Any]) -> None:
    write_text(path, json.dumps(report, indent=2, sort_keys=True) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pg-majors",
        type=parse_pg_majors,
        default=parse_pg_majors("13 14 15 16 17"),
    )
    parser.add_argument("--make-program", default="make")
    parser.add_argument("--make-target", default="smoke-release-artifacts")
    parser.add_argument("--make-var", action="append", type=parse_make_var, default=[])
    parser.add_argument("--cwd", type=Path, default=ROOT)
    parser.add_argument("--output-tail-lines", type=int, default=40)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, runner: Any = subprocess.run) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.output_tail_lines < 0:
        print(
            "release smoke matrix report failed: output tail lines must be >= 0",
            file=sys.stderr,
        )
        return 2
    if not args.make_target:
        print(
            "release smoke matrix report failed: make target must not be empty",
            file=sys.stderr,
        )
        return 2

    report = build_report(
        pg_majors=args.pg_majors,
        make_program=args.make_program,
        make_target=args.make_target,
        make_vars=args.make_var,
        cwd=args.cwd,
        output_tail_lines=args.output_tail_lines,
        runner=runner,
    )
    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    pass_count = sum(1 for result in report["results"] if result["passed"])
    total_count = len(report["results"])
    print(
        "release smoke matrix report: "
        f"{pass_count}/{total_count} PostgreSQL majors passed target "
        f"{args.make_target}"
    )
    if not report["all_passed"]:
        for result in report["results"]:
            if not result["passed"]:
                print(
                    "release smoke matrix failed for "
                    f"pg{result['pg_major']}: exit_code={result['exit_code']}",
                    file=sys.stderr,
                )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
