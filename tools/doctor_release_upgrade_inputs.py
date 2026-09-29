#!/usr/bin/env python3
"""Preflight versioned release upgrade inputs before running the ALTER EXTENSION smoke.

This doctor inspects the repository's extracted release artifact tree without
starting PostgreSQL or Docker. It records the current and requested versions,
lists the artifact directories that actually exist, and fails nonzero with an
explicit report when a legitimate old artifact or
``pgwarc_lance--OLD--NEW.sql`` upgrade script is missing. It never installs an
extension and never claims an upgrade pass.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import check_release_artifacts


ROOT = check_release_artifacts.ROOT
EXTENSION_NAME = check_release_artifacts.EXTENSION_NAME
SCHEMA_VERSION = 1
ARTIFACT_DIR_PATTERN = re.compile(
    rf"^{re.escape(EXTENSION_NAME)}-(?P<version>.+)-pg(?P<pg_major>\d+)$"
)
RESUME_REQUIREMENTS = [
    (
        "Bump Cargo.toml package.version to the intended new version and re-extract "
        "the current artifact so the control file and generated SQL match it."
    ),
    (
        "Keep an immutable old release artifact directory "
        f"dist/{EXTENSION_NAME}-<old>-pg<major>/ with the .so, .control, and "
        f"{EXTENSION_NAME}--<old>.sql files for the installed old version."
    ),
    (
        "Add the versioned upgrade script "
        f"{EXTENSION_NAME}--<old>--<new>.sql under the new artifact directory that "
        "describes the schema/data migration for ALTER EXTENSION UPDATE."
    ),
    (
        "Re-run this doctor; only when it reports valid=true may "
        "make smoke-release-upgrade be executed."
    ),
]


def artifact_dir_name(*, version: str, pg_major: int) -> str:
    return f"{EXTENSION_NAME}-{version}-pg{pg_major}"


def discover_artifacts(dist_root: Path) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    if not dist_root.is_dir():
        return artifacts
    for entry in sorted(dist_root.iterdir(), key=lambda path: path.name):
        if not entry.is_dir():
            continue
        match = ARTIFACT_DIR_PATTERN.match(entry.name)
        if not match:
            continue
        artifacts.append(
            {
                "version": match.group("version"),
                "pg_major": int(match.group("pg_major")),
                "dist_dir": str(entry),
            }
        )
    return artifacts


def upgrade_script_path(
    new_dist_dir: Path,
    *,
    old_version: str,
    new_version: str,
) -> Path:
    return new_dist_dir / f"{EXTENSION_NAME}--{old_version}--{new_version}.sql"


def build_report(
    *,
    dist_root: Path,
    pg_major: int,
    current_version: str,
    old_version: str | None,
    new_version: str,
    old_dist_dir: Path,
    new_dist_dir: Path,
) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    errors: list[str] = []
    warnings: list[str] = []

    def add_check(name: str, ok: bool, detail: str) -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            errors.append(f"{name}: {detail}")
        return bool(ok)

    artifacts = discover_artifacts(dist_root)
    available_versions = sorted({str(artifact["version"]) for artifact in artifacts})

    add_check(
        "old_version_provided",
        old_version is not None,
        "old version was provided"
        if old_version is not None
        else (
            "no old version was provided and no released old artifact is recorded; "
            "the versioned upgrade path cannot be exercised"
        ),
    )

    if old_version is None:
        add_check(
            "versions_differ",
            False,
            "cannot compare versions without an old version",
        )
    else:
        add_check(
            "versions_differ",
            old_version != new_version,
            f"old {old_version} != new {new_version}"
            if old_version != new_version
            else f"old and new version are both {new_version}",
        )

    if old_version is None:
        add_check(
            "old_artifact_valid",
            False,
            f"cannot validate old artifact without an old version: {old_dist_dir}",
        )
    else:
        old_errors = check_release_artifacts.validate_artifacts(
            old_dist_dir,
            version=old_version,
            pg_major=pg_major,
        )
        add_check(
            "old_artifact_valid",
            not old_errors,
            "; ".join(old_errors)
            if old_errors
            else f"valid old artifact at {old_dist_dir}",
        )

    new_errors = check_release_artifacts.validate_artifacts(
        new_dist_dir,
        version=new_version,
        pg_major=pg_major,
    )
    add_check(
        "new_artifact_valid",
        not new_errors,
        "; ".join(new_errors) if new_errors else f"valid new artifact at {new_dist_dir}",
    )

    script_path: Path | None = None
    if old_version is None:
        add_check(
            "upgrade_script_present",
            False,
            "cannot locate the versioned upgrade script without an old version",
        )
    else:
        script_path = upgrade_script_path(
            new_dist_dir,
            old_version=old_version,
            new_version=new_version,
        )
        if not script_path.is_file():
            add_check(
                "upgrade_script_present",
                False,
                f"missing upgrade script: {script_path}",
            )
        elif script_path.stat().st_size == 0:
            add_check(
                "upgrade_script_present",
                False,
                f"upgrade script is empty: {script_path}",
            )
        else:
            add_check(
                "upgrade_script_present",
                True,
                f"upgrade script present: {script_path}",
            )

    if not artifacts:
        warnings.append(f"no versioned artifact directories found under {dist_root}")
    elif old_version is None and not any(version != new_version for version in available_versions):
        warnings.append(
            f"only the current version {new_version} is present under {dist_root}; "
            "no distinct old artifact exists to upgrade from"
        )

    valid = not errors
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "extension": EXTENSION_NAME,
        "dist_root": str(dist_root),
        "pg_major": pg_major,
        "current_version": current_version,
        "requested_old_version": old_version,
        "requested_new_version": new_version,
        "new_version": new_version,
        "old_dist_dir": str(old_dist_dir),
        "new_dist_dir": str(new_dist_dir),
        "upgrade_script": str(script_path) if script_path is not None else None,
        "available_artifacts": artifacts,
        "available_versions": available_versions,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "valid": valid,
        "status": "ready" if valid else "blocked",
        "smoke_command": (
            f"UPGRADE_OLD_VERSION={old_version or '<old>'} "
            f"UPGRADE_NEW_VERSION={new_version} PG_MAJOR={pg_major} "
            "make smoke-release-upgrade"
        ),
        "finalize_command": (
            "RELEASE_SMOKE_TARGET=smoke-release-upgrade "
            f"RELEASE_SMOKE_MAKE_VARS='UPGRADE_OLD_VERSION={old_version or '<old>'}' "
            f"PG_MAJOR={pg_major} BENCH_OUT_DIR=<run_dir> make finalize-release-run"
        ),
        "resume_requirements": RESUME_REQUIREMENTS,
    }
    return report


def markdown_cell(value: object) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Release Upgrade Input Doctor",
        "",
        f"- status: `{report['status']}`",
        f"- valid: `{report['valid']}`",
        f"- generated_at: `{report['generated_at']}`",
        f"- extension: `{report['extension']}`",
        f"- dist_root: `{report['dist_root']}`",
        f"- pg_major: `{report['pg_major']}`",
        f"- current_version: `{report['current_version']}`",
        f"- requested_old_version: `{report['requested_old_version']}`",
        f"- new_version: `{report['new_version']}`",
        f"- upgrade_script: `{report['upgrade_script']}`",
        "",
        "## Checks",
        "",
        "| check | ok | detail |",
        "| --- | --- | --- |",
    ]
    for check in report["checks"]:  # type: ignore[union-attr]
        lines.append(
            f"| {markdown_cell(check['name'])} | {check['ok']} | "
            f"{markdown_cell(check['detail'])} |"
        )

    lines.extend(
        [
            "",
            "## Available Artifacts",
            "",
            "| version | pg_major | dist_dir |",
            "| --- | --- | --- |",
        ]
    )
    artifacts = report["available_artifacts"]
    if artifacts:  # type: ignore[truthy-bool]
        for artifact in artifacts:  # type: ignore[union-attr]
            lines.append(
                f"| {markdown_cell(artifact['version'])} | "
                f"{markdown_cell(artifact['pg_major'])} | "
                f"{markdown_cell(artifact['dist_dir'])} |"
            )
    else:
        lines.append("| _none_ |  |  |")

    errors = report["errors"]
    if errors:  # type: ignore[truthy-bool]
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in errors)  # type: ignore[union-attr]

    warnings = report["warnings"]
    if warnings:  # type: ignore[truthy-bool]
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)  # type: ignore[union-attr]

    lines.extend(["", "## Resume Requirements", ""])
    lines.extend(f"- {requirement}" for requirement in report["resume_requirements"])  # type: ignore[union-attr]

    lines.extend(
        [
            "",
            "## Resume Commands",
            "",
            "```bash",
            str(report["smoke_command"]),
            str(report["finalize_command"]),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, report: dict[str, object]) -> None:
    write_text(path, json.dumps(report, indent=2, sort_keys=True) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    current_version = check_release_artifacts.cargo_version()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-root", type=Path, default=ROOT / "dist")
    parser.add_argument("--pg-major", type=int, default=16)
    parser.add_argument("--new-version", default=current_version)
    parser.add_argument("--old-version")
    parser.add_argument("--old-dist-dir", type=Path)
    parser.add_argument("--new-dist-dir", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    current_version = check_release_artifacts.cargo_version()

    old_dist_dir = args.old_dist_dir
    if old_dist_dir is None:
        if args.old_version is not None:
            old_dist_dir = args.dist_root / artifact_dir_name(
                version=args.old_version,
                pg_major=args.pg_major,
            )
        else:
            old_dist_dir = args.dist_root / artifact_dir_name(
                version="<old>",
                pg_major=args.pg_major,
            )
    new_dist_dir = args.new_dist_dir
    if new_dist_dir is None:
        new_dist_dir = args.dist_root / artifact_dir_name(
            version=args.new_version,
            pg_major=args.pg_major,
        )

    report = build_report(
        dist_root=args.dist_root,
        pg_major=args.pg_major,
        current_version=current_version,
        old_version=args.old_version,
        new_version=args.new_version,
        old_dist_dir=old_dist_dir,
        new_dist_dir=new_dist_dir,
    )

    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(report))

    if report["valid"]:
        print(
            "release upgrade input doctor passed: "
            f"old {args.old_version} -> new {report['new_version']} on pg{args.pg_major}"
        )
        return 0

    for error in report["errors"]:  # type: ignore[union-attr]
        print(f"release upgrade input doctor failed: {error}", file=sys.stderr)
    print(
        "release upgrade smoke was not attempted; a real old artifact and "
        f"{EXTENSION_NAME}--OLD--NEW.sql upgrade script are required",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
