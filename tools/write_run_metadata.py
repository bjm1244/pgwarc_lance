#!/usr/bin/env python3
"""Write reproducibility metadata for a pgwarc_lance run directory."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SENSITIVE_ENV_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASS", "KEY", "COOKIE")


def is_sensitive_env_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in SENSITIVE_ENV_MARKERS)


def capture_environment(names: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    source = os.environ if env is None else env
    captured: dict[str, Any] = {}
    for name in names:
        value = source.get(name)
        if value is not None and is_sensitive_env_name(name):
            captured[name] = "<redacted>"
        else:
            captured[name] = value
    return captured


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def collect_git_metadata(repo_root: Path) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        root = Path(run_git(repo_root, ["rev-parse", "--show-toplevel"]))
        head = run_git(root, ["rev-parse", "HEAD"])
        branch = run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
        status = run_git(root, ["status", "--short"]).splitlines()
    except RuntimeError as error:
        warnings.append(f"could not collect git metadata: {error}")
        return {
            "root": str(repo_root),
            "head": None,
            "head_short": None,
            "branch": None,
            "dirty": None,
            "status": [],
        }, warnings

    return {
        "root": str(root),
        "head": head,
        "head_short": head[:7],
        "branch": branch,
        "dirty": bool(status),
        "status": status,
    }, warnings


def build_metadata(
    *,
    run_dir: Path,
    command: str | None,
    env_names: list[str],
    generated_at: str | None = None,
    repo_root: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[1]
    git, warnings = collect_git_metadata(root)
    return {
        "schema_version": 1,
        "generated_at": generated_at or utc_now(),
        "run_dir": str(run_dir),
        "command": command,
        "environment": capture_environment(env_names, env=env),
        "git": git,
        "warnings": warnings,
    }


def markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_markdown(metadata: dict[str, Any]) -> str:
    git = metadata["git"]
    lines = [
        "# pgwarc_lance Run Metadata",
        "",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- Run dir: `{metadata['run_dir']}`",
        f"- Command: `{metadata['command'] or ''}`",
        f"- Git head: `{git['head_short'] or ''}`",
        f"- Git branch: `{git['branch'] or ''}`",
        f"- Git dirty: `{git['dirty']}`",
        "",
        "## Environment",
        "",
        "| Name | Value |",
        "|------|-------|",
    ]
    for name, value in metadata["environment"].items():
        lines.append(f"| `{markdown_cell(name)}` | `{markdown_cell(value)}` |")

    if git["status"]:
        lines.extend(["", "## Git Status", ""])
        for entry in git["status"]:
            lines.append(f"- `{markdown_cell(entry)}`")

    if metadata["warnings"]:
        lines.extend(["", "## Warnings", ""])
        for warning in metadata["warnings"]:
            lines.append(f"- {markdown_cell(warning)}")

    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, metadata: dict[str, Any]) -> None:
    write_text(path, json.dumps(metadata, indent=2, sort_keys=True) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--command")
    parser.add_argument("--env", action="append", default=[], help="environment variable name to record")
    parser.add_argument("--generated-at", help="override generated_at timestamp for deterministic tests")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    metadata = build_metadata(
        run_dir=args.run_dir,
        command=args.command,
        env_names=args.env,
        generated_at=args.generated_at,
    )
    if args.json_output:
        write_json(args.json_output, metadata)
    if args.markdown_output:
        write_text(args.markdown_output, render_markdown(metadata))

    print(
        "run metadata: "
        f"git={metadata['git']['head_short'] or 'unknown'} "
        f"dirty={metadata['git']['dirty']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
