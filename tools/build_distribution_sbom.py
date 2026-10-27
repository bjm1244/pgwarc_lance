#!/usr/bin/env python3
"""Create artifact-bound CycloneDX and full notices for a PG16 extension build."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import tomllib
import uuid
from pathlib import Path
from urllib.parse import quote

from collect_dependency_notices import reachable_packages

ROOT = Path(__file__).resolve().parents[1]
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
HEX_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes())


def tree_sha256(root: Path) -> str:
    """Hash a local Cargo package as sorted relative paths and file bytes."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink in local Cargo package: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def inside(base: Path, relative: str) -> Path:
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f"evidence path escapes its directory: {relative}")
    if not path.is_file():
        raise ValueError(f"evidence file is missing: {relative}")
    return path


def license_texts(report: dict, notice_dir: Path) -> list[tuple[str, str, bytes]]:
    collected = []
    for package in report["packages"]:
        for item in package["texts"]:
            path = inside(notice_dir, item["file"])
            data = path.read_bytes()
            if sha256(data) != item["sha256"]:
                raise ValueError(f"notice text checksum differs: {package['package']}: {item['file']}")
            data.decode("utf-8")
            covered_sha = item.get("covered_sha256")
            if item.get("coverage") == "bundled-data" and not HEX_64.fullmatch(covered_sha or ""):
                raise ValueError(f"bundled-data notice lacks an asset checksum: {package['package']}")
            collected.append((package["package"], item["file"], data))
    return collected


def build(args: argparse.Namespace) -> dict:
    artifact = args.artifact.resolve()
    if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size == 0:
        raise ValueError("native artifact must be a nonempty regular file")
    artifact_sha = file_sha256(artifact)
    lock_bytes = args.cargo_lock.read_bytes()
    lock_sha = sha256(lock_bytes)
    if args.pg_major != 16:
        raise ValueError("this distribution SBOM target is PostgreSQL 16")
    if not HEX_64.fullmatch(args.native_fingerprint) or not HEX_COMMIT.fullmatch(args.source_commit):
        raise ValueError("native fingerprint must be 64 hex characters and source commit a full Git object ID")

    metadata = json.loads(args.metadata_json.read_text())
    notices = json.loads(args.notice_report.read_text())
    if notices.get("status") != "texts_collected" or notices.get("missing_primary_texts"):
        raise ValueError("strict Cargo notice collection did not pass")
    if notices.get("target") != args.target:
        raise ValueError("notice inventory was generated for a different target")
    if notices.get("cargo_lock_sha256") != lock_sha:
        raise ValueError("notice inventory was generated from a different Cargo.lock")

    toolchain = json.loads(args.toolchain_json.read_text())
    if toolchain.get("status") != "collected":
        raise ValueError("native/compiler inventory is not marked collected")
    for key, expected in (("pg_major", 16), ("target", args.target),
                          ("artifact_sha256", artifact_sha), ("cargo_lock_sha256", lock_sha)):
        if toolchain.get(key) != expected:
            raise ValueError(f"native/compiler inventory {key} does not match this build")
    components_input = toolchain.get("components")
    if not isinstance(components_input, list) or not components_input:
        raise ValueError("native/compiler inventory has no components")
    roles = {component.get("role") for component in components_input}
    if not {"compiler", "postgresql-host", "native-runtime"}.issubset(roles):
        raise ValueError("inventory must cover the compiler, PostgreSQL host, and native runtime")
    component_names = [component.get("name") for component in components_input]
    if len(component_names) != len(set(component_names)):
        raise ValueError("native/compiler inventory component names must be unique")

    cargo_lock = tomllib.loads(lock_bytes.decode())
    lock_rows = {(row["name"], row["version"]): row for row in cargo_lock["package"]}
    if len(lock_rows) != len(cargo_lock["package"]):
        raise ValueError("Cargo.lock contains ambiguous duplicate name/version package keys")
    checksums = {key: row.get("checksum") for key, row in lock_rows.items()}
    root_id = metadata["resolve"]["root"]
    packages_by_id = {package["id"]: package for package in metadata["packages"]}
    root_package = packages_by_id[root_id]
    cargo_packages = reachable_packages(metadata)
    expected_names = {f"{package['name']}@{package['version']}" for package in cargo_packages}
    notice_by_name = {row["package"]: row for row in notices["packages"]}
    if len(notice_by_name) != len(notices["packages"]) or set(notice_by_name) != expected_names:
        raise ValueError("strict notice inventory does not match the active Cargo target graph")

    package_refs = {}
    cargo_components = []
    local_package_count = 0
    for package in cargo_packages:
        name, version = package["name"], package["version"]
        key = f"{name}@{version}"
        notice = notice_by_name[key]
        if (name, version) not in lock_rows:
            raise ValueError(f"reachable package is absent from Cargo.lock: {key}")
        lock_checksum = checksums.get((name, version))
        if notice.get("cargo_lock_checksum") != lock_checksum:
            raise ValueError(f"Cargo.lock checksum mismatch in notice inventory: {key}")
        license_expression = package.get("license")
        if notice.get("declared_license") != license_expression:
            raise ValueError(f"declared license differs from Cargo metadata: {key}")
        if not license_expression or not notice.get("has_primary_license_text"):
            raise ValueError(f"package has no reviewed primary license text: {key}")

        package_root = Path(package["manifest_path"]).resolve().parent
        source = package.get("source")
        ref = f"urn:pglance:cargo:{quote(name, safe='._-')}:{quote(version, safe='.-+')}:" \
              f"{sha256(package['id'].encode())[:16]}"
        package_refs[package["id"]] = ref
        hashes = []
        if lock_checksum:
            hashes.append({"alg": "SHA-256", "content": lock_checksum})
        elif source is None:
            local_package_count += 1
            hashes.append({"alg": "SHA-256", "content": tree_sha256(package_root)})
        elif source.startswith("git+"):
            hashes.append({"alg": "SHA-256", "content": tree_sha256(package_root)})

        properties = [{"name": "pglance:cargo-lock-sha256", "value": lock_sha},
                      {"name": "pglance:package-source", "value": notice["crate_source"]}]
        if notice.get("reviewed_upstream_revision"):
            properties.append({"name": "pglance:reviewed-upstream-revision",
                               "value": notice["reviewed_upstream_revision"]})
        if notice.get("upstream_verification"):
            properties.append({"name": "pglance:upstream-verification",
                               "value": notice["upstream_verification"]})
        for text in notice["texts"]:
            properties.append({"name": "pglance:notice-text-sha256", "value": text["sha256"]})
            if text.get("covered_sha256"):
                properties.append({"name": "pglance:covered-asset-sha256",
                                   "value": text["covered_sha256"]})
        component = {
            "type": "library", "bom-ref": ref, "name": name, "version": version,
            "purl": f"pkg:cargo/{quote(name, safe='._-')}@{quote(version, safe='.-+')}",
            "licenses": [{"expression": license_expression}], "scope": "required",
            "hashes": hashes, "properties": properties,
        }
        repository = package.get("repository")
        if repository:
            component["externalReferences"] = [{"type": "vcs", "url": repository}]
        cargo_components.append(component)

    if local_package_count == 0:
        raise ValueError("active Cargo graph has no hashed local vendor/path packages")

    evidence_root = args.toolchain_json.resolve().parent
    tool_components = []
    tool_refs = {}
    tool_license_texts = []
    for component in components_input:
        required = ("name", "version", "role", "scope", "license_expression", "license_texts")
        if any(not component.get(key) for key in required):
            raise ValueError("native/compiler component lacks version, role, scope, license, or text")
        if component["scope"] not in {"build", "runtime"}:
            raise ValueError(f"unknown native/compiler scope: {component['scope']}")
        if not component["license_texts"]:
            raise ValueError(f"native/compiler component has no license text: {component['name']}")
        ref = f"urn:pglance:environment:{quote(component['name'], safe='._-')}:{quote(component['version'], safe='.-+')}:" \
              f"{sha256(json.dumps(component, sort_keys=True).encode())[:16]}"
        tool_refs[component["name"]] = ref
        hashes = []
        if component.get("sha256"):
            if not HEX_64.fullmatch(component["sha256"]):
                raise ValueError(f"invalid component SHA-256: {component['name']}")
            hashes.append({"alg": "SHA-256", "content": component["sha256"]})
        text_properties = []
        for item in component["license_texts"]:
            path = inside(evidence_root, item["path"])
            data = path.read_bytes()
            if sha256(data) != item.get("sha256"):
                raise ValueError(f"native/compiler license checksum differs: {component['name']}")
            data.decode("utf-8")
            text_properties.append({"name": "pglance:license-text-sha256", "value": item["sha256"]})
            tool_license_texts.append((component["name"], component["license_expression"], item["path"], data))
        tool_component = {
            "type": component.get("type", "library"), "bom-ref": ref,
            "name": component["name"], "version": component["version"],
            "licenses": [{"expression": component["license_expression"]}],
            "hashes": hashes,
            "properties": [
                {"name": "pglance:component-role", "value": component["role"]},
                {"name": "pglance:component-scope", "value": component["scope"]},
                *text_properties,
            ],
        }
        if component.get("purl"):
            tool_component["purl"] = component["purl"]
        tool_components.append(tool_component)

    if not tool_license_texts:
        raise ValueError("native/compiler license collection is empty")

    all_components = cargo_components + tool_components
    by_id = {package["id"]: package for package in metadata["packages"]}
    reachable_ids = {package["id"] for package in cargo_packages}
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    dependencies = []
    root_node = nodes[root_id]
    direct = []
    for dep in root_node["deps"]:
        if dep["pkg"] in reachable_ids and all(kind.get("kind") != "dev" for kind in dep["dep_kinds"]):
            direct.append(package_refs[dep["pkg"]])
    for component in components_input:
        if component["scope"] == "runtime" and component["role"] in {"native-runtime", "postgresql-host"}:
            direct.append(tool_refs[component["name"]])
    root_ref = f"urn:pglance:artifact:{root_package['name']}:{root_package['version']}"
    dependencies.append({"ref": root_ref, "dependsOn": sorted(set(direct))})
    for package_id in sorted(reachable_ids):
        deps = []
        for dep in nodes[package_id]["deps"]:
            if dep["pkg"] in reachable_ids and all(kind.get("kind") != "dev" for kind in dep["dep_kinds"]):
                deps.append(package_refs[dep["pkg"]])
        dependencies.append({"ref": package_refs[package_id], "dependsOn": sorted(set(deps))})

    cargo_texts = license_texts(notices, args.notice_report.resolve().parent)
    notice_lines = [
        "Artifact-bound dependency notices",
        f"Artifact SHA-256: {artifact_sha}",
        f"Cargo.lock SHA-256: {lock_sha}",
        f"Native fingerprint: {args.native_fingerprint}",
        "",
    ]
    for package, source, data in cargo_texts:
        notice_lines += [f"## Cargo: {package}", f"Source text: {source}", "", data.decode("utf-8").rstrip(), ""]
    for component, expression, source, data in tool_license_texts:
        notice_lines += [f"## Native/compiler: {component} ({expression})",
                         f"Source text: {source}", "", data.decode("utf-8").rstrip(), ""]
    notice_bytes = ("\n".join(notice_lines) + "\n").encode()
    notice_sha = sha256(notice_bytes)

    root_license = root_package.get("license")
    if not root_license:
        raise ValueError("extension package has no SPDX license expression")
    root_component = {
        "type": "application", "bom-ref": root_ref, "name": root_package["name"],
        "version": root_package["version"], "licenses": [{"expression": root_license}],
        "hashes": [{"alg": "SHA-256", "content": artifact_sha}],
        "properties": [
            {"name": "pglance:artifact-file", "value": artifact.name},
            {"name": "pglance:artifact-sha256", "value": artifact_sha},
            {"name": "pglance:cargo-lock-sha256", "value": lock_sha},
            {"name": "pglance:native-fingerprint", "value": args.native_fingerprint},
            {"name": "pglance:source-commit", "value": args.source_commit},
            {"name": "pglance:pg-major", "value": "16"},
            {"name": "pglance:target", "value": args.target},
            {"name": "pglance:notice-bundle-sha256", "value": notice_sha},
        ],
    }
    serial = uuid.uuid5(uuid.NAMESPACE_URL, f"{artifact_sha}:{lock_sha}:{args.native_fingerprint}")
    bom = {
        "bomFormat": "CycloneDX", "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{serial}", "version": 1,
        "metadata": {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "component": root_component,
            "properties": [
                {"name": "pglance:strict-notice-status", "value": notices["status"]},
                {"name": "pglance:cargo-package-count", "value": str(len(cargo_components))},
                {"name": "pglance:environment-component-count", "value": str(len(tool_components))},
            ],
        },
        "components": all_components,
        "dependencies": dependencies,
    }
    for output_path in (args.sbom_output, args.notices_output, args.summary_output):
        output_path.parent.mkdir(parents=True, exist_ok=True)
    args.sbom_output.write_text(json.dumps(bom, indent=2, sort_keys=True) + "\n")
    args.notices_output.write_bytes(notice_bytes)
    summary = {
        "status": "passed", "sbom_format": "CycloneDX 1.6",
        "artifact_sha256": artifact_sha, "cargo_lock_sha256": lock_sha,
        "native_fingerprint": args.native_fingerprint, "source_commit": args.source_commit,
        "pg_major": 16, "target": args.target,
        "cargo_package_count": len(cargo_components),
        "environment_component_count": len(tool_components),
        "strict_notice_status": notices["status"],
        "notice_bundle_sha256": notice_sha,
        "sbom_sha256": file_sha256(args.sbom_output),
    }
    args.summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-json", type=Path, required=True)
    parser.add_argument("--notice-report", type=Path, required=True)
    parser.add_argument("--cargo-lock", type=Path, default=ROOT / "Cargo.lock")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--toolchain-json", type=Path, required=True)
    parser.add_argument("--pg-major", type=int, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--native-fingerprint", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--sbom-output", type=Path, required=True)
    parser.add_argument("--notices-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
