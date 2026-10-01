#!/usr/bin/env python3
"""Collect pinned Cargo dependency texts for a target-specific notice review.

Generate Cargo metadata with --filter-platform and the release feature first.
The graph conservatively includes normal and build dependencies, but excludes
root dev-only dependencies. This collector never infers permission from a
missing license or substitutes a generic text for an unidentified copyright.
It is evidence for review, not a statement of legal clearance or an SBOM of
all compiler/native code. --strict refuses incomplete package-level texts.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "COPYRIGHT")
SOURCE_SUFFIXES = {".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".s", ".asm"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reachable_packages(metadata: dict) -> list[dict]:
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    packages = {package["id"]: package for package in metadata["packages"]}
    todo, seen = [metadata["resolve"]["root"]], set()
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        todo.extend(dep["pkg"] for dep in nodes[current]["deps"]
                    if any(kind["kind"] != "dev" for kind in dep["dep_kinds"]))
    return sorted((packages[key] for key in seen if key != metadata["resolve"]["root"]),
                  key=lambda package: (package["name"], package["version"]))


def collect(metadata: dict, lock_path: Path, overrides_path: Path, output: Path, target: str) -> dict:
    lock_bytes = lock_path.read_bytes()
    lock = tomllib.loads(lock_bytes.decode())
    checksums = {(row["name"], row["version"]): row.get("checksum") for row in lock["package"]}
    overrides = json.loads(overrides_path.read_text())
    text_dir = output / "texts"
    text_dir.mkdir(parents=True, exist_ok=True)
    rows, missing = [], []

    def save(data: bytes, origin: str) -> dict:
        data.decode("utf-8")  # Fail instead of silently corrupting a license.
        checksum = digest(data)
        (text_dir / f"{checksum}.txt").write_bytes(data)
        return {"file": f"texts/{checksum}.txt", "sha256": checksum, "origin": origin}

    for package in reachable_packages(metadata):
        key = f"{package['name']}@{package['version']}"
        root = Path(package["manifest_path"]).parent
        files = sorted(path for path in root.rglob("*")
                       if path.is_file() and path.name.upper().startswith(PREFIXES))
        if package.get("license_file"):
            files.append(root / package["license_file"])
        texts = [save(path.read_bytes(), str(path.relative_to(root))) for path in sorted(set(files))]
        has_primary = any(path.parent == root for path in files)
        override = overrides.get(key, {})
        for item in override.get("files", []):
            path = (overrides_path.parent / item["path"]).resolve()
            if not path.is_relative_to(overrides_path.parent.resolve()):
                raise ValueError(f"override path escapes reviewed directory: {key}")
            data = path.read_bytes()
            if digest(data) != item["sha256"]:
                raise ValueError(f"override checksum differs: {key}")
            coverage = item.get("coverage", "package")
            if coverage not in {"package", "bundled-data"}:
                raise ValueError(f"unknown override coverage: {key}: {coverage}")
            if coverage == "bundled-data" and not item.get("covered_path"):
                raise ValueError(f"bundled-data override needs covered_path: {key}")
            saved = save(data, item["url"])
            saved["coverage"] = coverage
            if item.get("covered_path"):
                saved["covered_path"] = item["covered_path"]
            texts.append(saved)
            if coverage == "package":
                has_primary = True
        # REUSE SPDX per-file copyrights supplement its template license texts.
        copyright_lines = set()
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            for line in path.read_text(errors="strict").splitlines():
                if "SPDX-FileCopyrightText:" in line:
                    copyright_lines.add(line.split("SPDX-FileCopyrightText:", 1)[1].strip())
        if copyright_lines:
            texts.append(save(("\n".join(sorted(copyright_lines)) + "\n").encode(), "SPDX-FileCopyrightText in crate sources"))
        if not has_primary or not package.get("license"):
            missing.append(key)
        rows.append({"package": key, "declared_license": package.get("license"),
                     "repository": package.get("repository") or override.get("repository"),
                     "crate_source": f"https://crates.io/api/v1/crates/{package['name']}/{package['version']}/download",
                     "cargo_lock_checksum": checksums.get((package["name"], package["version"])),
                     "reviewed_upstream_revision": override.get("git_revision"),
                     "upstream_verification": override.get("verification"),
                     "has_primary_license_text": has_primary, "texts": texts})
    report = {"schema_version": 2, "status": "needs_review" if missing else "texts_collected",
              "scope": "target-filtered Cargo normal/build graph; compiler/native and distribution review required",
              "target": target, "cargo_lock_sha256": digest(lock_bytes), "package_count": len(rows),
              "missing_primary_texts": missing, "packages": rows}
    (output / "dependency-notices.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = ["pgwarc_lance dependency notice review", f"Target: {target}",
             f"Cargo.lock SHA-256: {digest(lock_bytes)}", "",
             "This conservative inventory includes build dependencies. All original alternatives",
             "and bundled nested notices are retained. Missing texts and compiler/native review",
             "must be resolved before claiming complete binary redistribution notices.", ""]
    for row in rows:
        lines += [row["package"], "Declared license: " + str(row["declared_license"]),
                  "Source: " + row["crate_source"]]
        for item in row["texts"]:
            lines += ["Notice: " + item["file"] + " (" + item["origin"] + ")"]
            if "coverage" in item:
                lines += ["Coverage: " + item["coverage"] +
                          (" (" + item["covered_path"] + ")" if item.get("covered_path") else "")]
        if not row["has_primary_license_text"]:
            lines += ["REVIEW REQUIRED: primary license text missing"]
        lines += [""]
    (output / "DEPENDENCY_NOTICES.txt").write_text("\n".join(lines) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-json", type=Path, required=True)
    parser.add_argument("--cargo-lock", type=Path, default=ROOT / "Cargo.lock")
    parser.add_argument("--overrides-json", type=Path, default=ROOT / "third_party/upstream-licenses/manifest.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target", required=True, help="must match cargo metadata --filter-platform")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = collect(json.loads(args.metadata_json.read_text()), args.cargo_lock,
                     args.overrides_json, args.output_dir, args.target)
    print(json.dumps({key: report[key] for key in ["status", "package_count", "missing_primary_texts", "cargo_lock_sha256"]}, indent=2))
    return 2 if args.strict and report["missing_primary_texts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
