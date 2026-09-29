#!/usr/bin/env python3
"""Check public documentation links and roadmap phase summary."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    "README.md", "ROADMAP.md", "BLUEPRINT.md", "BENCHMARKS.md",
    "PACKAGING.md", "QUALITY.md", "SECURITY.md", "CONTRIBUTING.md",
]
CURRENT_PHASE_PATTERNS = {
    "README.md": re.compile(r"현재 완료된 Phase 1-(\d+)"),
    "ROADMAP.md": re.compile(r"현재 로드맵의 Phase 1-(\d+)"),
}


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def latest_roadmap_phase(text: str) -> int | None:
    phases = [
        int(match.group(1))
        for match in re.finditer(r"^## Phase (\d+):", text, flags=re.MULTILINE)
    ]
    return max(phases) if phases else None


def current_phase_range(text: str, pattern: re.Pattern[str]) -> int | None:
    match = pattern.search(text)
    return int(match.group(1)) if match else None


def check_phase_count_drift(errors: list[str]) -> None:
    latest_phase = latest_roadmap_phase(read_text("ROADMAP.md"))
    if latest_phase is None:
        errors.append("ROADMAP.md: missing phase headers")
        return
    for doc, pattern in CURRENT_PHASE_PATTERNS.items():
        current = current_phase_range(read_text(doc), pattern)
        if current is None:
            errors.append(f"{doc}: missing current Phase 1-N completion summary")
        elif current != latest_phase:
            errors.append(
                f"{doc}: current Phase 1-{current} summary "
                f"does not match latest ROADMAP phase {latest_phase}"
            )


def main() -> int:
    errors: list[str] = []
    for relative in DOCS:
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"missing required doc: {relative}")
            continue
        for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", read_text(relative)):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (path.parent / target.split("#", 1)[0]).exists():
                errors.append(f"{relative}: broken local markdown link: {target}")
    check_phase_count_drift(errors)
    for error in errors:
        print(f"docs check failed: {error}", file=sys.stderr)
    if errors:
        return 1
    print("documentation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
