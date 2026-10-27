from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_distribution_sbom import build
import collect_dependency_notices


class DistributionSbomTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.crate = self.root / "vendor" / "dep"
        self.crate.mkdir(parents=True)
        (self.crate / "Cargo.toml").write_text('[package]\nname="dep"\nversion="1.0.0"\n')
        (self.crate / "LICENSE").write_text("MIT package license text\n")
        self.lock = self.root / "Cargo.lock"
        self.lock.write_text(
            'version = 4\n\n[[package]]\nname = "pgwarc_lance"\nversion = "0.1.20"\n'
            '\n[[package]]\nname = "dep"\nversion = "1.0.0"\n'
        )
        self.metadata = {
            "resolve": {
                "root": "root-id",
                "nodes": [
                    {"id": "root-id", "deps": [{"pkg": "dep-id", "dep_kinds": [{"kind": None}]}]},
                    {"id": "dep-id", "deps": []},
                ],
            },
            "packages": [
                {"id": "root-id", "name": "pgwarc_lance", "version": "0.1.20", "license": "Apache-2.0"},
                {"id": "dep-id", "name": "dep", "version": "1.0.0", "license": "MIT",
                 "manifest_path": str(self.crate / "Cargo.toml"), "repository": "https://example.test/dep",
                 "source": None},
            ],
        }
        self.metadata_path = self.root / "metadata.json"
        self.metadata_path.write_text(json.dumps(self.metadata))
        (self.root / "overrides.json").write_text("{}")
        self.notice_dir = self.root / "notices"
        self.notices = collect_dependency_notices.collect(
            self.metadata, self.lock, self.root / "overrides.json", self.notice_dir,
            "x86_64-unknown-linux-gnu",
        )
        self.notice_path = self.notice_dir / "dependency-notices.json"
        self.artifact = self.root / "pgwarc_lance.so"
        self.artifact.write_bytes(b"fixture extension artifact")
        self.evidence = self.root / "toolchain"
        (self.evidence / "licenses").mkdir(parents=True)
        components = []
        for name, role in (("rustc", "compiler"), ("postgresql", "postgresql-host"),
                           ("glibc", "native-runtime")):
            path = self.evidence / "licenses" / f"{name}.txt"
            path.write_text(f"{name} license text\n")
            components.append({
                "name": name, "version": "1.0", "role": role, "scope": "runtime",
                "license_expression": "MIT", "license_texts": [{
                    "path": f"licenses/{name}.txt",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }],
            })
        self.toolchain_path = self.evidence / "native-compiler.json"
        self.toolchain = {
            "status": "collected", "pg_major": 16, "target": "x86_64-unknown-linux-gnu",
            "artifact_sha256": hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
            "cargo_lock_sha256": hashlib.sha256(self.lock.read_bytes()).hexdigest(),
            "components": components,
        }
        self.write_toolchain()
        self.args = Namespace(
            artifact=self.artifact, cargo_lock=self.lock, pg_major=16,
            native_fingerprint="a" * 64, source_commit="b" * 40,
            metadata_json=self.metadata_path, notice_report=self.notice_path,
            toolchain_json=self.toolchain_path, target="x86_64-unknown-linux-gnu",
            sbom_output=self.root / "out" / "bom.json",
            notices_output=self.root / "out" / "NATIVE_AND_CARGO_NOTICES.txt",
            summary_output=self.root / "out" / "summary.json",
        )

    def write_toolchain(self):
        self.toolchain_path.write_text(json.dumps(self.toolchain))

    def test_builds_cyclonedx_bound_to_artifact_lock_and_notices(self):
        summary = build(self.args)
        bom = json.loads(self.args.sbom_output.read_text())
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(bom["bomFormat"], "CycloneDX")
        self.assertEqual(bom["specVersion"], "1.6")
        root = bom["metadata"]["component"]
        properties = {item["name"]: item["value"] for item in root["properties"]}
        self.assertEqual(properties["pglance:artifact-sha256"], summary["artifact_sha256"])
        self.assertEqual(properties["pglance:cargo-lock-sha256"], summary["cargo_lock_sha256"])
        self.assertEqual(properties["pglance:notice-bundle-sha256"], summary["notice_bundle_sha256"])
        dep = next(item for item in bom["components"] if item["name"] == "dep")
        self.assertEqual(dep["hashes"][0]["content"], hashlib.sha256(
            b"Cargo.toml\0" + (self.crate / "Cargo.toml").read_bytes() + b"\0" +
            b"LICENSE\0" + (self.crate / "LICENSE").read_bytes() + b"\0"
        ).hexdigest())

    def test_refuses_notice_report_from_different_lock(self):
        self.lock.write_text(self.lock.read_text() + "# changed\n")
        with self.assertRaisesRegex(ValueError, "different Cargo.lock"):
            build(self.args)

    def test_refuses_toolchain_inventory_for_different_artifact(self):
        self.toolchain["artifact_sha256"] = "0" * 64
        self.write_toolchain()
        with self.assertRaisesRegex(ValueError, "artifact_sha256"):
            build(self.args)

    def test_refuses_missing_or_modified_native_license_text(self):
        license_path = self.evidence / "licenses" / "rustc.txt"
        license_path.write_text("changed after review\n")
        with self.assertRaisesRegex(ValueError, "license checksum differs"):
            build(self.args)

    def test_requires_postgresql_16_target(self):
        self.args.pg_major = 15
        with self.assertRaisesRegex(ValueError, "PostgreSQL 16"):
            build(self.args)


if __name__ == "__main__":
    unittest.main()
