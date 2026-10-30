#!/usr/bin/env python3
"""Manage operator-owned, versioned Lance datasets for one logical collection."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


SCHEMA_VERSION = 1
IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$")
NORMALIZATIONS = {"none", "l2"}
AUTHORIZATION = {
    "operator_scope": "trusted_os_operator_shared_corpus",
    "tenant_isolation": "not_supported",
}
TOP_KEYS = {
    "schema_version", "collection_id", "authorization", "active_version",
    "staged_version", "generation", "versions", "readiness", "activation_history",
}


class ManifestError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ManifestError(f"{name} must match {IDENTIFIER.pattern}")
    return value


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ManifestError(f"{name} must be a non-empty trimmed string")
    if any(ord(char) < 32 for char in value):
        raise ManifestError(f"{name} contains a control character")
    return value


def _dataset_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise ManifestError("dataset_path must be a relative POSIX path")
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise ManifestError("dataset_path must stay below the manifest directory")
    root_real = root.resolve(strict=True)
    candidate = root_real.joinpath(*parsed.parts)
    cursor = root_real
    for part in parsed.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ManifestError("dataset_path cannot contain symbolic links")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root_real):
        raise ManifestError("dataset_path escapes the manifest directory")
    return resolved


def _profile_digest(collection_id: str, version_id: str, descriptor: dict[str, Any]) -> str:
    payload = {
        "collection_id": collection_id,
        "version_id": version_id,
        "dataset_path": descriptor["dataset_path"],
        "embedding": descriptor["embedding"],
        "chunking": descriptor["chunking"],
        "expected_vectors": descriptor["expected_vectors"],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_descriptor(
    collection_id: str, version_id: str, descriptor: Any, root: Path
) -> dict[str, Any]:
    version_id = _identifier(version_id, "version_id")
    if not isinstance(descriptor, dict):
        raise ManifestError(f"versions.{version_id} must be an object")
    expected = {
        "dataset_path", "embedding", "chunking", "expected_vectors",
        "created_at", "profile_sha256",
    }
    if set(descriptor) != expected:
        raise ManifestError(f"versions.{version_id} has missing or unknown fields")
    _dataset_path(root, descriptor["dataset_path"])
    expected_vectors = descriptor["expected_vectors"]
    if isinstance(expected_vectors, bool) or not isinstance(expected_vectors, int) or expected_vectors <= 0:
        raise ManifestError(f"versions.{version_id}.expected_vectors must be a positive integer")

    embedding = descriptor["embedding"]
    if not isinstance(embedding, dict) or set(embedding) != {
        "model", "model_version", "dimensions", "normalization", "normalization_version"
    }:
        raise ManifestError(f"versions.{version_id}.embedding has missing or unknown fields")
    _required_text(embedding["model"], f"versions.{version_id}.embedding.model")
    _required_text(embedding["model_version"], f"versions.{version_id}.embedding.model_version")
    dimensions = embedding["dimensions"]
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions <= 0:
        raise ManifestError(f"versions.{version_id}.embedding.dimensions must be a positive integer")
    if embedding["normalization"] not in NORMALIZATIONS:
        raise ManifestError(f"versions.{version_id}.embedding.normalization must be none or l2")
    _required_text(embedding["normalization_version"], f"versions.{version_id}.embedding.normalization_version")

    chunking = descriptor["chunking"]
    if not isinstance(chunking, dict) or set(chunking) != {
        "strategy", "version", "size_chars", "overlap_chars"
    }:
        raise ManifestError(f"versions.{version_id}.chunking has missing or unknown fields")
    if chunking["strategy"] != "whole_document":
        raise ManifestError("this importer supports only whole_document chunking; chunking is not performed here")
    _required_text(chunking["version"], f"versions.{version_id}.chunking.version")
    if chunking["size_chars"] is not None:
        raise ManifestError("whole_document chunking requires size_chars=null")
    if isinstance(chunking["overlap_chars"], bool) or chunking["overlap_chars"] != 0:
        raise ManifestError("whole_document chunking requires overlap_chars=0")

    _required_text(descriptor["created_at"], f"versions.{version_id}.created_at")
    expected_digest = _profile_digest(collection_id, version_id, descriptor)
    if descriptor["profile_sha256"] != expected_digest:
        raise ManifestError(f"versions.{version_id} profile checksum does not match its settings")
    return descriptor


def validate_manifest(raw: Any, manifest_path: Path) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != TOP_KEYS:
        raise ManifestError("manifest has missing or unknown top-level fields")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ManifestError(f"unsupported manifest schema_version {raw['schema_version']!r}")
    collection_id = _identifier(raw["collection_id"], "collection_id")
    if raw["authorization"] != AUTHORIZATION:
        raise ManifestError("manifest must declare trusted OS operator / shared corpus scope without tenant isolation")
    generation = raw["generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ManifestError("generation must be a non-negative integer")
    versions = raw["versions"]
    if not isinstance(versions, dict) or not versions:
        raise ManifestError("versions must contain at least one immutable profile")
    root = manifest_path.parent.resolve(strict=True)
    checked: dict[str, Any] = {}
    paths: dict[str, str] = {}
    for version_id, descriptor in versions.items():
        version_id = _identifier(version_id, "version_id")
        checked[version_id] = _validate_descriptor(collection_id, version_id, descriptor, root)
        normalized_path = str(_dataset_path(root, descriptor["dataset_path"]))
        if normalized_path in paths:
            raise ManifestError(
                f"versions.{version_id} reuses the dataset path assigned to {paths[normalized_path]}"
            )
        paths[normalized_path] = version_id

    active = raw["active_version"]
    staged = raw["staged_version"]
    if active is not None:
        _identifier(active, "active_version")
    if staged is not None:
        _identifier(staged, "staged_version")
    if active is not None and active not in checked:
        raise ManifestError("active_version does not name a registered version")
    if staged is not None and staged not in checked:
        raise ManifestError("staged_version does not name a registered version")
    if active is not None and active == staged:
        raise ManifestError("active_version and staged_version must be different")
    if active is not None and not _dataset_path(root, checked[active]["dataset_path"]).is_dir():
        raise ManifestError("active dataset path is missing or is not a directory")
    readiness = raw["readiness"]
    if not isinstance(readiness, dict) or not set(readiness).issubset(checked):
        raise ManifestError("readiness must map only registered version IDs")
    for version_id, result in readiness.items():
        if not isinstance(result, dict) or set(result) != {
            "profile_sha256", "completed_at", "records", "vector_rows", "input_sha256", "lance_mode"
        }:
            raise ManifestError(f"readiness.{version_id} has missing or unknown fields")
        descriptor = checked[version_id]
        if result["profile_sha256"] != descriptor["profile_sha256"]:
            raise ManifestError(f"readiness.{version_id} profile checksum does not match")
        _required_text(result["completed_at"], f"readiness.{version_id}.completed_at")
        if isinstance(result["records"], bool) or not isinstance(result["records"], int) or result["records"] <= 0:
            raise ManifestError(f"readiness.{version_id}.records must be positive")
        if (
            isinstance(result["vector_rows"], bool)
            or not isinstance(result["vector_rows"], int)
            or result["vector_rows"] != descriptor["expected_vectors"]
        ):
            raise ManifestError(f"readiness.{version_id} vector count does not match the expected count")
        if not isinstance(result["input_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", result["input_sha256"]):
            raise ManifestError(f"readiness.{version_id}.input_sha256 must be a SHA-256")
        if result["lance_mode"] not in {"create", "upsert"}:
            raise ManifestError(f"readiness.{version_id} must come from direct create/upsert writes")
    history = raw["activation_history"]
    if not isinstance(history, list):
        raise ManifestError("activation_history must be an array")
    for event in history:
        if not isinstance(event, dict) or set(event) != {"action", "from_version", "to_version", "at"}:
            raise ManifestError("activation_history contains an invalid event")
        if event["action"] not in {"activate", "rollback"}:
            raise ManifestError("activation_history action must be activate or rollback")
        if event["from_version"] is not None:
            _identifier(event["from_version"], "activation_history.from_version")
        if event["from_version"] is not None and event["from_version"] not in checked:
            raise ManifestError("activation_history references an unknown previous version")
        if event["to_version"] not in checked or event["to_version"] is None:
            raise ManifestError("activation_history references an unknown target version")
        _required_text(event["at"], "activation_history.at")
    if history and history[-1]["to_version"] != active:
        raise ManifestError("activation_history does not end at active_version")
    return raw


def load_manifest(path: Path) -> dict[str, Any]:
    path = path.absolute()
    if path.is_symlink() or not path.is_file():
        raise ManifestError("manifest must be an existing regular file, not a symbolic link")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    return validate_manifest(raw, path)


def _lock(path: Path, *, shared: bool) -> int:
    lock_path = Path(str(path.absolute()) + ".lock")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        raise ManifestError(f"cannot open collection manifest lock: {exc}") from exc
    fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
    return fd


@contextlib.contextmanager
def import_lock(path: Path) -> Iterator[dict[str, Any]]:
    fd = _lock(path, shared=True)
    try:
        yield load_manifest(path)
    finally:
        os.close(fd)


def _atomic_write(path: Path, raw: dict[str, Any]) -> None:
    encoded = (json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _new_descriptor(
    collection_id: str,
    version_id: str,
    dataset_path: str,
    model: str,
    model_version: str,
    dimensions: int,
    expected_vectors: int,
    normalization: str,
    normalization_version: str,
    chunking_version: str,
) -> dict[str, Any]:
    _identifier(version_id, "version_id")
    descriptor: dict[str, Any] = {
        "dataset_path": dataset_path,
        "embedding": {
            "model": model,
            "model_version": model_version,
            "dimensions": dimensions,
            "normalization": normalization,
            "normalization_version": normalization_version,
        },
        "chunking": {
            "strategy": "whole_document",
            "version": chunking_version,
            "size_chars": None,
            "overlap_chars": 0,
        },
        "expected_vectors": expected_vectors,
        "created_at": _utc_now(),
    }
    descriptor["profile_sha256"] = _profile_digest(collection_id, version_id, descriptor)
    return descriptor


def _parse_new_descriptor(args: argparse.Namespace, collection_id: str) -> dict[str, Any]:
    if args.dimensions <= 0:
        raise ManifestError("--dimensions must be positive")
    if args.normalization not in NORMALIZATIONS:
        raise ManifestError("--normalization must be none or l2")
    return _new_descriptor(
        collection_id, args.version_id, args.dataset_path, args.model,
        args.model_version, args.dimensions, args.expected_vectors, args.normalization,
        args.normalization_version, args.chunking_version,
    )


def _create(args: argparse.Namespace) -> None:
    path = args.manifest.absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ManifestError("refusing to replace an existing manifest")
    collection_id = _identifier(args.collection_id, "collection_id")
    descriptor = _parse_new_descriptor(args, collection_id)
    _dataset_path(path.parent, descriptor["dataset_path"])
    if _dataset_path(path.parent, descriptor["dataset_path"]).exists():
        raise ManifestError("initial dataset path already exists; choose an unused version path")
    raw = {
        "schema_version": SCHEMA_VERSION,
        "collection_id": collection_id,
        "authorization": dict(AUTHORIZATION),
        "active_version": None,
        "staged_version": args.version_id,
        "generation": 0,
        "versions": {args.version_id: descriptor},
        "readiness": {},
        "activation_history": [],
    }
    validate_manifest(raw, path)
    fd = _lock(path, shared=False)
    try:
        if path.exists():
            raise ManifestError("refusing to replace an existing manifest")
        _atomic_write(path, raw)
    finally:
        os.close(fd)


def _stage(args: argparse.Namespace) -> None:
    path = args.manifest.absolute()
    fd = _lock(path, shared=False)
    try:
        raw = load_manifest(path)
        if raw["staged_version"] is not None:
            raise ManifestError("a reindex version is already staged; finish or record that work first")
        if args.version_id in raw["versions"]:
            raise ManifestError("version_id already exists; version IDs and profiles are immutable")
        descriptor = _parse_new_descriptor(args, raw["collection_id"])
        dataset = _dataset_path(path.parent, descriptor["dataset_path"])
        for old in raw["versions"].values():
            if _dataset_path(path.parent, old["dataset_path"]) == dataset:
                raise ManifestError("dataset paths cannot be reused across collection versions")
        if dataset.exists():
            raise ManifestError("staged dataset path already exists; choose a fresh path")
        raw["versions"][args.version_id] = descriptor
        raw["staged_version"] = args.version_id
        raw["generation"] += 1
        validate_manifest(raw, path)
        _atomic_write(path, raw)
    finally:
        os.close(fd)


def _abandon(args: argparse.Namespace) -> None:
    path = args.manifest.absolute()
    fd = _lock(path, shared=False)
    try:
        raw = load_manifest(path)
        if args.version_id != raw["staged_version"]:
            raise ManifestError("version_id is not the currently staged version")
        raw["staged_version"] = None
        raw["generation"] += 1
        validate_manifest(raw, path)
        _atomic_write(path, raw)
    finally:
        os.close(fd)


def record_ready(path: Path, version_id: str, result: dict[str, Any]) -> None:
    fd = _lock(path, shared=False)
    try:
        raw = load_manifest(path)
        if version_id != raw["staged_version"]:
            raise ManifestError("only the currently staged version can be marked ready")
        descriptor = raw["versions"][version_id]
        if result.get("complete") is not True or result.get("execute") is not True:
            raise ManifestError("a staged version requires a complete executed import")
        if result.get("lance_mode") not in {"create", "upsert"}:
            raise ManifestError("a staged reindex must use direct create/upsert writes, not append or outbox")
        records = result.get("records")
        vectors = result.get("vector_rows")
        if isinstance(records, bool) or not isinstance(records, int) or records <= 0:
            raise ManifestError("completed import must contain at least one record")
        if vectors != descriptor["expected_vectors"]:
            raise ManifestError(
                f"completed import produced {vectors} vectors; manifest expects {descriptor['expected_vectors']}"
            )
        input_sha256 = result.get("input_sha256")
        if not isinstance(input_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", input_sha256):
            raise ManifestError("a staged version requires a source input SHA-256")
        raw["readiness"][version_id] = {
            "profile_sha256": descriptor["profile_sha256"],
            "completed_at": _utc_now(),
            "records": records,
            "vector_rows": vectors,
            "input_sha256": input_sha256,
            "lance_mode": result["lance_mode"],
        }
        raw["generation"] += 1
        validate_manifest(raw, path)
        _atomic_write(path, raw)
    finally:
        os.close(fd)


def _dataset_dimension(path: Path, psql: str) -> int:
    sql_value = str(path).replace("'", "''")
    base = shlex.split(psql)
    if not base:
        raise ManifestError("--psql command is empty")
    argv = [*base, "-XAtq", "-v", "ON_ERROR_STOP=1", "-c", f"SELECT public.lance_vector_dim('{sql_value}');"]
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManifestError(f"could not inspect the dataset through PostgreSQL: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip()[-1000:]
        raise ManifestError(f"PostgreSQL could not inspect the dataset (exit {result.returncode}): {detail}")
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise ManifestError("PostgreSQL returned no valid Lance vector dimension") from exc


def _run_psql(sql: str, psql: str) -> None:
    base = shlex.split(psql)
    if not base:
        raise ManifestError("--psql command is empty")
    try:
        result = subprocess.run(
            [*base, "-X", "-v", "ON_ERROR_STOP=1", "-c", sql],
            text=True, capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManifestError(f"could not prepare the dataset through PostgreSQL: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip()[-1000:]
        raise ManifestError(f"PostgreSQL could not prepare the dataset (exit {result.returncode}): {detail}")


def _prepare(args: argparse.Namespace) -> None:
    path = args.manifest.absolute()
    fd = _lock(path, shared=False)
    try:
        raw = load_manifest(path)
        version_id = _identifier(args.version_id, "version_id")
        if version_id != raw["staged_version"]:
            raise ManifestError("only a staged version can be prepared")
        descriptor = raw["versions"][version_id]
        dataset = _dataset_path(path.parent, descriptor["dataset_path"])
        expected = descriptor["embedding"]["dimensions"]
        if dataset.exists():
            observed = _dataset_dimension(dataset, args.psql)
            if observed != expected:
                raise ManifestError(f"prepared dataset dimension is {observed}, manifest requires {expected}")
            return
        sql_uri = str(dataset).replace("'", "''")
        _run_psql(
            f"SELECT public.lance_create_table('{sql_uri}', {expected}, false);",
            args.psql,
        )
        observed = _dataset_dimension(dataset, args.psql)
        if observed != expected:
            raise ManifestError(f"prepared dataset dimension is {observed}, manifest requires {expected}")
    finally:
        os.close(fd)


def _switch(args: argparse.Namespace, action: str) -> None:
    path = args.manifest.absolute()
    fd = _lock(path, shared=False)
    try:
        raw = load_manifest(path)
        current = raw["active_version"]
        target = args.version_id if action == "activate" else (
            raw["activation_history"][-1]["from_version"] if raw["activation_history"] else None
        )
        if target is None:
            raise ManifestError("there is no previously active version to restore")
        if action == "activate" and target != raw["staged_version"]:
            raise ManifestError("only the separately staged version can be activated")
        if action == "rollback" and raw["activation_history"][-1]["to_version"] != current:
            raise ManifestError("active version changed since the last recorded transition")
        if target not in raw["versions"]:
            raise ManifestError("target version is not registered")
        descriptor = raw["versions"][target]
        if target not in raw["readiness"]:
            raise ManifestError("target version has no complete import receipt and cannot be activated")
        dataset = _dataset_path(path.parent, descriptor["dataset_path"])
        if not dataset.is_dir():
            raise ManifestError("target dataset directory is missing")
        observed = _dataset_dimension(dataset, args.psql)
        expected = descriptor["embedding"]["dimensions"]
        if observed != expected:
            raise ManifestError(f"target dataset dimension is {observed}, manifest requires {expected}")
        raw["activation_history"].append({
            "action": action,
            "from_version": current,
            "to_version": target,
            "at": _utc_now(),
        })
        raw["active_version"] = target
        if action == "activate":
            raw["staged_version"] = None
        raw["generation"] += 1
        validate_manifest(raw, path)
        _atomic_write(path, raw)
    finally:
        os.close(fd)


def _resolve(args: argparse.Namespace) -> None:
    fd = _lock(args.manifest, shared=True)
    try:
        raw = load_manifest(args.manifest)
        active = raw["active_version"]
        if active is None:
            raise ManifestError("collection has no active version")
        dataset = _dataset_path(args.manifest.absolute().parent, raw["versions"][active]["dataset_path"])
        print(dataset)
    finally:
        os.close(fd)


def import_profile(raw: dict[str, Any], path: Path, version_id: str | None) -> tuple[str, Path, dict[str, Any]]:
    active = raw["active_version"]
    staged = raw["staged_version"]
    selected = version_id or active
    if selected is None:
        raise ManifestError("collection has no active version; select the initial staged version explicitly")
    if selected not in {active, staged}:
        raise ManifestError("imports can target only the active version or the one staged reindex version")
    descriptor = raw["versions"][selected]
    return raw["collection_id"], _dataset_path(path.absolute().parent, descriptor["dataset_path"]), descriptor


def _add_profile_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--dataset-path", required=True, help="unique relative POSIX path below the manifest directory")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-version", required=True, help="immutable provider revision, deployment version or reviewed checksum")
    parser.add_argument("--dimensions", required=True, type=int)
    parser.add_argument("--expected-vectors", required=True, type=int,
                        help="complete reindex vector count from the source accounting manifest")
    parser.add_argument("--normalization", choices=sorted(NORMALIZATIONS), required=True)
    parser.add_argument("--normalization-version", required=True)
    parser.add_argument("--chunking-version", required=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    create = subs.add_parser("create", help="create a collection with its first staged profile")
    create.add_argument("manifest", type=Path)
    create.add_argument("--collection-id", required=True)
    _add_profile_arguments(create)
    stage = subs.add_parser("stage", help="declare a separate dataset/profile for reindexing")
    stage.add_argument("manifest", type=Path)
    _add_profile_arguments(stage)
    abandon = subs.add_parser("abandon", help="clear a staged pointer while retaining its manifest entry and all files")
    abandon.add_argument("manifest", type=Path)
    abandon.add_argument("--version-id", required=True)
    activate = subs.add_parser("activate", help="switch to the staged dataset after PG verifies its vector dimension")
    activate.add_argument("manifest", type=Path)
    activate.add_argument("--version-id", required=True)
    activate.add_argument("--psql", default=os.environ.get("PGWARC_PSQL", "psql -d postgres"))
    prepare = subs.add_parser("prepare", help="create or verify the staged empty Lance dataset through PostgreSQL")
    prepare.add_argument("manifest", type=Path)
    prepare.add_argument("--version-id", required=True)
    prepare.add_argument("--psql", default=os.environ.get("PGWARC_PSQL", "psql -d postgres"))
    rollback = subs.add_parser("rollback", help="restore the prior active profile without deleting either dataset")
    rollback.add_argument("manifest", type=Path)
    rollback.add_argument("--psql", default=os.environ.get("PGWARC_PSQL", "psql -d postgres"))
    resolve = subs.add_parser("resolve", help="print the currently active managed dataset path")
    resolve.add_argument("manifest", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        if args.command == "create":
            _create(args)
        elif args.command == "stage":
            _stage(args)
        elif args.command == "abandon":
            _abandon(args)
        elif args.command == "prepare":
            _prepare(args)
        elif args.command in {"activate", "rollback"}:
            _switch(args, args.command)
        elif args.command == "resolve":
            _resolve(args)
        return 0
    except ManifestError as exc:
        print(f"collection manifest: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
