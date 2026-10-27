# Dependency notice review

The project source is Apache-2.0. Dependency licenses remain their own terms.
`NOTICE` is a short project attribution, not a complete binary license bundle.
For redistribution, preserve dependency copyright/license texts and applicable
upstream NOTICE content. The [Apache redistribution terms](https://www.apache.org/licenses/LICENSE-2.0)
and [Mozilla MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/) describe the
relevant upstream requirements. In particular, recipients of MPL-covered code
must be informed how to obtain its corresponding source.

The tested Linux PG16 Cargo graph contains 437 normal/build dependencies
excluding the extension itself. This is a conservative graph, not proof that
every crate's code survives linking. It includes an MPL-2.0 package,
`option-ext` 0.2.0; the collector records its exact crate source download URL and
Cargo.lock checksum. That historical inventory predates the manifest-only compatibility patches
described in [vendor/README.md](vendor/README.md). Compiler runtime,
bundled native source licensing and the actual distribution contents still
require review.

`tools/collect_dependency_notices.py` reads target-filtered Cargo metadata and
installed crate source files, collects original root and nested license/NOTICE
texts, deduplicates texts by SHA-256 and supplements REUSE per-file copyright
statements in Rust, C, C++ headers and assembly sources. Reviewed upstream texts absent from crate archives are preserved
in `third_party/upstream-licenses/`, with immutable Git revisions, source URLs
and checksums in its manifest. The collector performs no network access and
rejects modified overrides or paths escaping that directory. Schema version 2
records each upstream override's coverage: `package` supplies a package-level
text; `bundled-data` applies only to its named asset and cannot clear a missing
package license. Unknown coverage is rejected.

In the release build environment, with the same Rust toolchain and Cargo.lock:

```bash
cargo metadata --locked --format-version 1 --no-default-features --features pg16 --filter-platform x86_64-unknown-linux-gnu > /tmp/pgwarc-metadata.json
python3 tools/collect_dependency_notices.py --metadata-json /tmp/pgwarc-metadata.json --target x86_64-unknown-linux-gnu --output-dir /tmp/pgwarc-notices --strict
```

Keep `dependency-notices.json`, `DEPENDENCY_NOTICES.txt` and the complete `texts/`
directory together. Changing Cargo.lock or platform requires regenerating the
review bundle. `texts_collected` means package-level texts were found; it does
not declare redistribution cleared. `needs_review` names unresolved packages;
`--strict` exits non-zero when those primary texts are missing. An abbreviated
Apache header does not count as a full package license.

The 2026-10-01 PG16 review remains incomplete for:

- `random_word` 0.5.2: manifest declares MIT, but the recorded upstream revision
  has no primary license text. Word-list provenance also needs review if its
  data is included in the distribution. The upstream declaration for its English
  ENABLE word list is now preserved separately, covering `src/br/en.br` only.
  Published compressed bytes match the crate-recorded upstream revision
  `dc1cab8d7951ff4285ea9158c8954d663fdd6fbf` exactly (377,689 bytes,
  SHA-256 `f10ec9f822e45c68dff80543eaf6f92d76b0f3b38f9826e286b433893eca4435`).
  The [upstream declaration](https://github.com/MitchellRhysHall/random_word/blob/dc1cab8d7951ff4285ea9158c8954d663fdd6fbf/src/license/en.txt)
  identifies the word-list data as public domain; this does not supply a license
  text for the crate's Rust implementation. Conservative graph inclusion does
  not prove that this asset survives the extension's final link.
- `seahash` 4.1.0: manifest declares MIT; the recorded upstream license URL could
  not be retrieved.

These are unresolved evidence gaps, not assertions that the packages cannot
be used. Do not fabricate copyright notices or claim the binary license gate
complete based only on SPDX expressions. Public model/corpus assets used for
optional quality evaluation have separate licenses and attribution described
in [PRODUCTION.md](PRODUCTION.md); they are not included in the extension artifact.

## 0.1.7 candidate dependency notice recheck (2026-10-20)

The updated Linux x86_64 PG16 Cargo.lock has SHA-256
`677a80ca664ff92defd3f7f8f4106639719ff72af8f2b9fcbf1a756d3ecbc19f`.
Target-filtered metadata contains 440 normal/build dependencies excluding the
extension itself. The refreshed notice inventory includes the updated
`quinn-proto` 0.11.18, `rustls` 0.23.46 and `rand_pcg` 0.10.2 package texts.
Strict collection still returns `needs_review` and exit 2 for the same missing
primary license texts, `random_word` 0.5.2 and `seahash` 4.1.0. The candidate
has not been cleared for binary redistribution. Keep the private generated
inventory with its matching lockfile if the package is reviewed again.

The omitted `deepsize_derive` 0.1.2 text was recovered at upstream commit
`1383a60f86a0ee0a67c76eead74af442eb8a3c9b`: published Rust source and
`Cargo.toml.orig` match that commit byte-for-byte. The override records this
verification because the crate-recorded revision is unavailable.

## 0.1.8 candidate dependency notice recheck (2026-10-21)

The Linux x86_64 PG16 Cargo.lock SHA-256 is
`14bb64b18a50eb1675bc385e9a5537ca4f3be987fce7e0e47862ef5de77c4b6f`.
Target-filtered metadata contains 440 normal/build dependencies, excluding the
extension itself. Strict collection refreshed the inventory for the updated
`rustls-webpki` 0.103.16 and reports `needs_review`, exit 2, for the unchanged
missing primary license texts of `random_word` 0.5.2 and `seahash` 4.1.0. The
private run's `dependency-notices.json` is tied to this lock hash. Binary
redistribution remains uncleared.

## Native build and Rust runtime evidence (2026-10-01)

An additional inventory is tied to the unchanged 0.1.5 Linux PG16 binary
SHA-256 `a77bbdd296f334d7767f73212d287424d3ae1a28c59fdd548eaff31b6fc9d77a`.
Preserved release build-script outputs declare nine static libraries from seven
packages. The source inventory verified 618 Rust/native files against the exact
published crate archives, whose hashes match Cargo.lock. This is a conservative
inventory of build declarations, including test-support archives where declared;
it does not prove every archive member survives final linking.

The review supplement preserves Rust 1.96.0's `COPYRIGHT-library.html` and all
12 accompanying license texts, including LLVM and GCC exception texts, with
file hashes and observed standard-library/unwind/compiler-builtins archive hashes.
These are standard-library notices; the Rust compiler itself is not redistributed.
Combined with the earlier four dynamic ELF dependencies, this improves the
inspectable build/runtime evidence. It is not a complete final-binary SBOM or
legal clearance, and the original immutable 0.1.5 artifact is not rewritten.

The Cargo notice supplement now also preserves SPDX copyrights in C/C++/header
and assembly files. The previous Rust-only scan omitted `Copyright The Lance
Authors` from Lance's C source; a native collection against the 437-package graph
now preserves that statement. Strict collection continues to return status
`needs_review` and exit 2 for the same two missing package-level primary texts.

For a future binary distribution, keep the Cargo notice bundle, native build
inventory, standard-library copyright HTML and its license texts with the
versioned artifact, verify their hashes and finish the unresolved redistribution
review before promotion. Recollect these files when the toolchain or lock changes.

## 0.1.16 development candidate dependency status (2026-10-24)

The PG16 Linux x86_64 Cargo.lock SHA-256 is
`a44685a6005aa3d7ee97733066cf70be1b326f3b855b7e081a03c1e5512fe51f`. The lock
change adds the project version and a direct `sha2` 0.10.9 edge; that package was
already present transitively. A candidate-specific notice inventory was not
collected. The RustSec 0.22.2 tool build was started but stopped before the audit
ran, so this candidate has no current RustSec result. The latest completed notice
review remains the 0.1.8 inventory above; the known `random_word` 0.5.2 and
`seahash` 4.1.0 primary-text gaps remain unresolved. This candidate is not cleared
for binary redistribution.

## 0.1.17 development candidate dependency status (2026-10-24)

The PG16 Linux x86_64 Cargo.lock SHA-256 is
`0696a9cc0b14c0f8dcea5e1b6a35b6cd63afc1df971380c89a7461c3cf52638a`.
Lock-tied, target-filtered metadata covers 440 package records. The refreshed
strict notice collection returns `needs_review` with exit 2 for the same missing
primary texts: `random_word` 0.5.2 and `seahash` 4.1.0. The updated `ctutils`
0.4.4 and `tokio` 1.53.3 are included in this inventory. Retain its private
generated files with the matching lock hash for follow-up review. No binary
redistribution clearance is granted.

## 0.1.19 BM25 index candidate dependency recheck (2026-10-26)

The Linux x86_64 PG16 Cargo.lock SHA-256 is
`02751df37bd975ac89579e57be01720b2ae9b608d28e338d16ec6752b5d8a659`. Compared
with 0.1.18, only the root extension version changed; the dependency graph is
unchanged. Fresh target-filtered metadata lists 440 package records. Strict
notice collection returns `needs_review` with exit 2 because primary license
texts remain missing for `random_word` 0.5.2 and `seahash` 4.1.0. The generated
inventory is private and tied to the lock hash; redistribution is not cleared.

`cargo audit` 0.22.2 fetched advisory database revision
`21d909e4c8bd4e14c7aea58bf980c881a0053ab1` (2026-10-24) and scanned 561 lock
dependencies. It found no vulnerabilities, unsound or yanked packages. Three
unmaintained warnings remain: `paste` 1.0.15, `proc-macro-error2` 2.0.1 and
`serde_cbor` 0.11.2. This check does not clear the missing redistribution texts
or production approval.

## Optional PDF extraction tool (2026-10-26)

PDF extraction invokes an operator-supplied Poppler `pdftotext` executable only
when `--pdftotext` is set. Poppler is neither linked nor vendored by the project;
operators must install and review their platform package under its own license
and security update policy. DOCX body extraction uses Python's standard-library
ZIP and XML modules and adds no Cargo dependency. PDF extraction was exercised
with Poppler 24.02.0; the source validation does not clear or redistribute that
tool.

## 0.1.20 source compatibility patches (2026-10-27)

Two upstream Apache-2.0 crates are vendored with only zstd version requirements
changed in their Cargo manifests. Their license/NOTICE texts and original
archive checksums are recorded in [vendor/README.md](vendor/README.md).
Lance's omitted root LICENSE is supplied from the exact upstream VCS commit.
The fixed zstd crates remain checksummed crates.io dependencies. This source
patch does not clear binary redistribution: the existing package-level primary
license gaps and native/compiler runtime review still require resolution.

The refreshed target-filtered 0.1.20 inventory covers 440 normal/build package
records; Cargo.lock SHA-256 is
`5107326283ea0b6d0d535a5e500516c445fcd39f4a159568901f8656437bd45e`.
Strict notice collection still returns `needs_review` (exit 2), specifically
`random_word@0.5.2` and `seahash@4.1.0`. The vendored crates' root license texts
were found. Binary publication remains blocked; source snapshot publication
and binary redistribution are separate decisions.
