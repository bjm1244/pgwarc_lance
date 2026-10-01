#!/usr/bin/env python3
"""Check lightweight documentation and harness consistency."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    "README.md",
    "ROADMAP.md",
    "BLUEPRINT.md",
    "BENCHMARKS.md",
    "PACKAGING.md",
    "QUALITY.md",
    "README.en.md",
    "PRODUCTION.md",
    "SECURITY.md",
    "OPERATIONS.md",
    "THIRD_PARTY.md",
]
README_REQUIRED_SNIPPETS = [
    "BLUEPRINT.md",
    "tools/check_docs.py",
    "make_quality_fixture.py",
    "make make-quality-fixture",
    "doctor_quality_labels.py",
    "make doctor-quality-labels",
    "make finalize-quality-labels-run",
    "make finalize-quality-fixture-build-run",
    "report_quality_fixture.py",
    "make report-quality-fixture",
    "doctor_quality_baseline.py",
    "make doctor-quality-baseline",
    "doctor_real_corpus_input.py",
    "make doctor-real-corpus-input",
    "make finalize-quality-run",
    "preflight_quality_inputs.py",
    "make preflight-quality-inputs",
    "QUALITY_INPUT_PREFLIGHT_MANIFEST_JSON",
    "QUALITY_INPUT_PREFLIGHT_REQUIRED",
    "make finalize-public-warc-quality-run",
    "execution_budget.py",
    "make eval-quality-budget",
    "smoke_public_warc.py",
    "make check-docs",
    "make smoke-public-warc",
    "make smoke-pg-version",
    "make pg-version-matrix",
    "make check-release-artifacts",
    "make smoke-release-artifacts",
    "make smoke-release-upgrade",
    "doctor_release_upgrade_inputs.py",
    "make doctor-release-upgrade-inputs",
    "make release-artifacts-matrix",
    "make smoke-release-artifacts-matrix",
    "make smoke-release-upgrade-matrix",
    "make report-release-artifacts",
    "make report-release-smoke-matrix",
    "make finalize-release-run",
    "make doctor-import-summary",
    "make finalize-import-run",
    "make report-run-directory",
    "make write-run-metadata",
    "make doctor-run-directory",
    "make finalize-run-directory",
    "make finalize-benchmark-run",
    "make finalize-warc-bm25-modes-run",
    "compare_benchmark_runs.py",
    "make compare-benchmark-runs",
    "make finalize-benchmark-comparison-run",
]
ROADMAP_REQUIRED_COMMANDS = [
    "make check-docs",
    "make test-warc",
    "make test-unit",
    "make test-regress",
    "make test",
    "make test-all",
    "make report-quality-fixture",
    "make doctor-quality-labels",
    "make finalize-quality-labels-run",
    "make finalize-quality-fixture-build-run",
    "make doctor-quality-baseline",
    "make doctor-real-corpus-input",
    "make finalize-quality-run",
    "make preflight-quality-inputs",
    "make finalize-public-warc-quality-run",
    "make eval-quality",
    "make eval-quality-budget",
    "make verify",
    "make release-artifacts-matrix",
    "make smoke-release-artifacts-matrix",
    "make report-release-artifacts",
    "make report-release-smoke-matrix",
    "make finalize-release-run",
    "make smoke-release-upgrade",
    "make smoke-release-upgrade-matrix",
    "make doctor-release-upgrade-inputs",
    "make doctor-import-summary",
    "make finalize-import-run",
    "make report-run-directory",
    "make write-run-metadata",
    "make doctor-run-directory",
    "make finalize-run-directory",
    "make finalize-benchmark-run",
    "make finalize-warc-bm25-modes-run",
    "make compare-benchmark-runs",
    "make finalize-benchmark-comparison-run",
    "git diff --check",
]
MAKE_TARGETS_REQUIRED = [
    "check-docs",
    "test-unit",
    "test-warc",
    "test-warc-db",
    "test-regress",
    "test-all",
    "verify",
    "eval-quality",
    "eval-quality-budget",
    "smoke-public-warc",
    "bench-bm25",
    "bench-lance",
    "bench-warc",
    "bench-warc-bm25-modes",
    "finalize-benchmark-run",
    "finalize-warc-bm25-modes-run",
    "compare-benchmark-runs",
    "finalize-benchmark-comparison-run",
    "make-quality-fixture",
    "doctor-quality-labels",
    "finalize-quality-labels-run",
    "finalize-quality-fixture-build-run",
    "report-quality-fixture",
    "doctor-quality-baseline",
    "doctor-real-corpus-input",
    "finalize-quality-run",
    "preflight-quality-inputs",
    "finalize-public-warc-quality-run",
    "public-warc-quality-fixture",
    "eval-public-warc-quality",
    "build-pg-version",
    "smoke-pg-version",
    "pg-version-matrix",
    "check-release-artifacts",
    "smoke-release-artifacts",
    "smoke-release-upgrade",
    "release-artifacts-matrix",
    "smoke-release-artifacts-matrix",
    "smoke-release-upgrade-matrix",
    "doctor-release-upgrade-inputs",
    "report-release-artifacts",
    "report-release-smoke-matrix",
    "finalize-release-run",
    "doctor-import-summary",
    "finalize-import-run",
    "report-run-directory",
    "write-run-metadata",
    "doctor-run-directory",
    "finalize-run-directory",
]
CURRENT_PHASE_PATTERNS = {
    "README.md": re.compile(r"현재 완료된 Phase 1-(\d+)"),
    "ROADMAP.md": re.compile(r"현재 로드맵의 Phase 1-(\d+)"),
}


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def make_targets() -> set[str]:
    targets: set[str] = set()
    for line in read_text("Makefile").splitlines():
        if line.startswith("\t") or line.startswith(" "):
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*:", line)
        if match:
            targets.add(match.group(1))
    return targets


def documented_make_targets() -> set[str]:
    pattern = re.compile(r"\bmake(?:\s+-[A-Za-z])?\s+([A-Za-z0-9_.-]+)")
    targets: set[str] = set()
    for doc in DOCS:
        text = read_text(doc)
        for match in pattern.finditer(text):
            target = match.group(1)
            if target not in {"-"} and not target.endswith("-"):
                targets.add(target)
    return targets


def latest_roadmap_phase(text: str) -> int | None:
    phases = [
        int(match.group(1))
        for match in re.finditer(r"^## Phase (\d+):", text, flags=re.MULTILINE)
    ]
    return max(phases) if phases else None


def current_phase_range(text: str, pattern: re.Pattern[str]) -> int | None:
    match = pattern.search(text)
    return int(match.group(1)) if match else None


def local_markdown_links(text: str) -> list[str]:
    links = []
    for match in re.finditer(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if " " in target and not target.startswith("<"):
            target = target.split()[0]
        target = target.removeprefix("<").removesuffix(">")
        if "#" in target:
            target = target.split("#", maxsplit=1)[0]
        if target:
            links.append(target)
    return links


def check_docs_exist(errors: list[str]) -> None:
    for doc in DOCS:
        if not (ROOT / doc).is_file():
            errors.append(f"missing required doc: {doc}")


def check_markdown_links(errors: list[str]) -> None:
    for doc in DOCS:
        path = ROOT / doc
        if not path.exists():
            continue
        for target in local_markdown_links(path.read_text(encoding="utf-8")):
            if not (path.parent / target).exists():
                errors.append(f"{doc}: broken local markdown link: {target}")


def check_make_docs(errors: list[str]) -> None:
    targets = make_targets()
    for target in MAKE_TARGETS_REQUIRED:
        if target not in targets:
            errors.append(f"Makefile: missing target {target}")

    for target in sorted(documented_make_targets() - targets):
        errors.append(f"documented make target has no Makefile target: {target}")


def check_required_snippets(errors: list[str]) -> None:
    readme = read_text("README.md")
    for snippet in README_REQUIRED_SNIPPETS:
        if snippet not in readme:
            errors.append(f"README.md: missing required snippet {snippet!r}")

    roadmap = read_text("ROADMAP.md")
    for command in ROADMAP_REQUIRED_COMMANDS:
        if command not in roadmap:
            errors.append(f"ROADMAP.md: missing verification command {command!r}")


def check_phase_count_drift(errors: list[str]) -> None:
    roadmap = read_text("ROADMAP.md")
    latest_phase = latest_roadmap_phase(roadmap)
    if latest_phase is None:
        errors.append("ROADMAP.md: missing phase headers")
        return

    for doc, pattern in CURRENT_PHASE_PATTERNS.items():
        current_phase = current_phase_range(read_text(doc), pattern)
        if current_phase is None:
            errors.append(f"{doc}: missing current Phase 1-N completion summary")
        elif current_phase != latest_phase:
            errors.append(
                f"{doc}: current Phase 1-{current_phase} summary "
                f"does not match latest ROADMAP phase {latest_phase}"
            )


def main() -> int:
    errors: list[str] = []
    check_docs_exist(errors)
    check_markdown_links(errors)
    check_make_docs(errors)
    check_required_snippets(errors)
    check_phase_count_drift(errors)

    if errors:
        for error in errors:
            print(f"docs check failed: {error}", file=sys.stderr)
        return 1

    print("documentation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
