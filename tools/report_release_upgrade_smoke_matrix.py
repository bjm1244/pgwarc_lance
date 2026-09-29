#!/usr/bin/env python3
"""Report the release upgrade smoke matrix with a mandatory per-major preflight.

The ``report-release-smoke-matrix`` target can be pointed at
``smoke-release-upgrade``. Without a guard, a blocked upgrade input pair
(missing old artifact or ``pgwarc_lance--OLD--NEW.sql``) would still be handed
to ``make smoke-release-upgrade`` per major. This matrix runs the existing
``doctor_release_upgrade_inputs`` report builder with explicit old/new dist,
version, and ``pg_major`` inputs for every major first, then dispatches the
existing smoke target only for majors whose preflight is ready. Blocked majors
keep their reason and input identity, never execute an ``ALTER EXTENSION
UPDATE`` smoke, and make the whole report non-zero.

The JSON report keeps the ``release_smoke_matrix.json`` schema (``schema_version``
1 plus the per-major result rows validated by the run doctor) and adds
per-major ``preflight``/``smoke_skipped`` fields. It is only a harness contract,
not versioned upgrade evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import check_release_artifacts
import doctor_release_upgrade_inputs
import report_release_smoke_matrix


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_NAME = check_release_artifacts.EXTENSION_NAME
SCHEMA_VERSION = 1
RESOLVED_MAKE_VARS = (
    "UPGRADE_OLD_VERSION",
    "UPGRADE_NEW_VERSION",
    "UPGRADE_OLD_DIST_DIR",
    "UPGRADE_NEW_DIST_DIR",
)
UPGRADE_SMOKE_TARGET = "smoke-release-upgrade"


def make_var_name(value: str) -> str:
    return value.partition("=")[0]


def parse_make_vars(make_vars: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in make_vars:
        name, separator, raw_value = value.partition("=")
        if separator and name:
            parsed[name] = raw_value
    return parsed


def resolve_dist_dir(
    dist_root: Path,
    *,
    override: str | None,
    version: str | None,
    placeholder: str,
    pg_major: int,
) -> Path:
    if override:
        return Path(override)
    return dist_root / doctor_release_upgrade_inputs.artifact_dir_name(
        version=version if version is not None else placeholder,
        pg_major=pg_major,
    )


def resolved_smoke_make_vars(
    make_vars: list[str],
    *,
    old_version: str | None,
    new_version: str,
    old_dist_dir: Path,
    new_dist_dir: Path,
) -> list[str]:
    resolved = [
        value for value in make_vars if make_var_name(value) not in RESOLVED_MAKE_VARS
    ]
    resolved.append(f"UPGRADE_OLD_VERSION={old_version if old_version else '<old>'}")
    resolved.append(f"UPGRADE_NEW_VERSION={new_version}")
    resolved.append(f"UPGRADE_OLD_DIST_DIR={old_dist_dir}")
    resolved.append(f"UPGRADE_NEW_DIST_DIR={new_dist_dir}")
    return resolved


def preflight_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "valid": bool(report.get("valid")),
        "pg_major": report.get("pg_major"),
        "old_version": report.get("requested_old_version"),
        "new_version": report.get("new_version"),
        "old_dist_dir": report.get("old_dist_dir"),
        "new_dist_dir": report.get("new_dist_dir"),
        "upgrade_script": report.get("upgrade_script"),
        "errors": list(report.get("errors", [])),
        "checks": list(report.get("checks", [])),
    }


def build_report(
    *,
    pg_majors: list[int],
    make_program: str,
    make_target: str,
    make_vars: list[str],
    dist_root: Path,
    new_version_default: str,
    current_version: str,
    cwd: Path,
    output_tail_lines: int,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    parsed = parse_make_vars(make_vars)
    old_version = parsed.get("UPGRADE_OLD_VERSION") or None
    new_version = parsed.get("UPGRADE_NEW_VERSION") or new_version_default
    old_override = parsed.get("UPGRADE_OLD_DIST_DIR") or None
    new_override = parsed.get("UPGRADE_NEW_DIST_DIR") or None

    results: list[dict[str, Any]] = []
    for pg_major in pg_majors:
        old_dist_dir = resolve_dist_dir(
            dist_root,
            override=old_override,
            version=old_version,
            placeholder="<old>",
            pg_major=pg_major,
        )
        new_dist_dir = resolve_dist_dir(
            dist_root,
            override=new_override,
            version=new_version,
            placeholder="<new>",
            pg_major=pg_major,
        )

        preflight = doctor_release_upgrade_inputs.build_report(
            dist_root=dist_root,
            pg_major=pg_major,
            current_version=current_version,
            old_version=old_version,
            new_version=new_version,
            old_dist_dir=old_dist_dir,
            new_dist_dir=new_dist_dir,
        )

        planned_vars = resolved_smoke_make_vars(
            make_vars,
            old_version=old_version,
            new_version=new_version,
            old_dist_dir=old_dist_dir,
            new_dist_dir=new_dist_dir,
        )
        planned_command = report_release_smoke_matrix.make_command(
            make_program=make_program,
            make_target=make_target,
            pg_major=pg_major,
            make_vars=planned_vars,
        )
        display_command = report_release_smoke_matrix.shell_join(
            report_release_smoke_matrix.redact_command(planned_command)
        )

        entry: dict[str, Any] = {
            "pg_major": pg_major,
            "command": display_command,
            "exit_code": None,
            "passed": False,
            "duration_seconds": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "error": None,
            "preflight": preflight_summary(preflight),
            "preflight_passed": bool(preflight["valid"]),
            "smoke_skipped": True,
            "smoke_skipped_reason": None,
        }

        if not preflight["valid"]:
            reason = "; ".join(preflight.get("errors", [])) or (
                "preflight reported status blocked"
            )
            entry["error"] = f"upgrade preflight blocked: {reason}"
            entry["smoke_skipped_reason"] = entry["error"]
            results.append(entry)
            continue

        smoke = report_release_smoke_matrix.run_smoke(
            command=planned_command,
            display_command=report_release_smoke_matrix.redact_command(planned_command),
            cwd=cwd,
            output_tail_lines=output_tail_lines,
            runner=runner,
        )
        entry.update(smoke)
        entry["smoke_skipped"] = False
        results.append(entry)

    blocked_majors = [
        int(entry["pg_major"]) for entry in results if not entry["preflight_passed"]
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": report_release_smoke_matrix.utc_now(),
        "cwd": str(cwd),
        "make_program": make_program,
        "make_target": make_target,
        "make_vars": [
            report_release_smoke_matrix.redact_make_var(value) for value in make_vars
        ],
        "pg_majors": list(pg_majors),
        "output_tail_lines": output_tail_lines,
        "all_passed": all(entry["passed"] for entry in results),
        "results": results,
        "preflight_doctor": "tools/doctor_release_upgrade_inputs.py",
        "dist_root": str(dist_root),
        "old_version": old_version,
        "new_version": new_version,
        "blocked_majors": blocked_majors,
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    make_vars = ", ".join(report["make_vars"]) if report["make_vars"] else "none"
    lines = [
        "# pgwarc_lance Release Upgrade Smoke Matrix",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Target: `{report['make_target']}`",
        f"- Make vars: `{make_vars}`",
        f"- Preflight doctor: `{report['preflight_doctor']}`",
        f"- All passed: `{str(report['all_passed']).lower()}`",
        f"- Blocked majors: `{report['blocked_majors']}`",
        "",
        "| PostgreSQL | Preflight | Smoke | Exit Code | Command |",
        "|------------|-----------|-------|-----------|---------|",
    ]
    for result in report["results"]:
        preflight = result["preflight"]
        lines.append(
            "| {pg} | {preflight} | {smoke} | {exit_code} | `{command}` |".format(
                pg=result["pg_major"],
                preflight="ready" if preflight["valid"] else "blocked",
                smoke=(
                    "skipped"
                    if result["smoke_skipped"]
                    else ("pass" if result["passed"] else "fail")
                ),
                exit_code=(
                    "n/a" if result["exit_code"] is None else result["exit_code"]
                ),
                command=markdown_cell(result["command"]),
            )
        )

    blocked = [result for result in report["results"] if result["smoke_skipped"]]
    if blocked:
        lines.extend(["", "## Blocked Preflight", ""])
        for result in blocked:
            preflight = result["preflight"]
            lines.append(f"### PostgreSQL {result['pg_major']}")
            lines.append(f"- Reason: {markdown_cell(result['smoke_skipped_reason'])}")
            lines.append(f"- Old version: `{preflight['old_version']}`")
            lines.append(f"- New version: `{preflight['new_version']}`")
            lines.append(f"- Old dist dir: `{preflight['old_dist_dir']}`")
            lines.append(f"- New dist dir: `{preflight['new_dist_dir']}`")
            lines.append(f"- Upgrade script: `{preflight['upgrade_script']}`")
            lines.append(f"- Withheld command: `{markdown_cell(result['command'])}`")
            lines.append("")

    failing = [
        result
        for result in report["results"]
        if not result["passed"] and not result["smoke_skipped"]
    ]
    if failing:
        lines.extend(["", "## Failures", ""])
        for result in failing:
            lines.append(f"### PostgreSQL {result['pg_major']}")
            if result["error"]:
                lines.append(f"- Error: {markdown_cell(result['error'])}")
            lines.append(f"- Command: `{markdown_cell(result['command'])}`")
            if result["stderr_tail"]:
                lines.extend(
                    ["", "stderr tail:", "", "```text", result["stderr_tail"], "```"]
                )
            if result["stdout_tail"]:
                lines.extend(
                    ["", "stdout tail:", "", "```text", result["stdout_tail"], "```"]
                )
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
        type=report_release_smoke_matrix.parse_pg_majors,
        default=report_release_smoke_matrix.parse_pg_majors("13 14 15 16 17"),
    )
    parser.add_argument("--make-program", default="make")
    parser.add_argument("--make-target", default=UPGRADE_SMOKE_TARGET)
    parser.add_argument(
        "--make-var",
        action="append",
        type=report_release_smoke_matrix.parse_make_var,
        default=[],
    )
    parser.add_argument("--dist-root", type=Path, default=ROOT / "dist")
    parser.add_argument("--new-version", default=check_release_artifacts.cargo_version())
    parser.add_argument("--cwd", type=Path, default=ROOT)
    parser.add_argument("--output-tail-lines", type=int, default=40)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, runner: Any = subprocess.run) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.output_tail_lines < 0:
        print(
            "release upgrade smoke matrix report failed: output tail lines must be >= 0",
            file=sys.stderr,
        )
        return 2
    if not args.make_target:
        print(
            "release upgrade smoke matrix report failed: make target must not be empty",
            file=sys.stderr,
        )
        return 2

    report = build_report(
        pg_majors=args.pg_majors,
        make_program=args.make_program,
        make_target=args.make_target,
        make_vars=args.make_var,
        dist_root=args.dist_root,
        new_version_default=args.new_version,
        current_version=check_release_artifacts.cargo_version(),
        cwd=args.cwd,
        output_tail_lines=args.output_tail_lines,
        runner=runner,
    )
    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    ready_count = sum(
        1 for result in report["results"] if result["preflight_passed"]
    )
    smoke_count = sum(
        1 for result in report["results"] if not result["smoke_skipped"]
    )
    passed_count = sum(1 for result in report["results"] if result["passed"])
    total_count = len(report["results"])
    print(
        "release upgrade smoke matrix report: "
        f"{passed_count}/{total_count} majors passed, "
        f"{ready_count} preflight-ready, {smoke_count} smoke attempted"
    )
    if report["blocked_majors"]:
        print(
            "blocked majors were not smoke-tested: "
            f"{report['blocked_majors']}; run make doctor-release-upgrade-inputs "
            "for a legitimate old/new artifact pair and "
            f"{EXTENSION_NAME}--OLD--NEW.sql",
            file=sys.stderr,
        )
    if not report["all_passed"]:
        for result in report["results"]:
            if result["smoke_skipped"]:
                print(
                    "release upgrade smoke matrix blocked for "
                    f"pg{result['pg_major']}: {result['smoke_skipped_reason']}",
                    file=sys.stderr,
                )
            elif not result["passed"]:
                print(
                    "release upgrade smoke matrix failed for "
                    f"pg{result['pg_major']}: exit_code={result['exit_code']}",
                    file=sys.stderr,
                )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
