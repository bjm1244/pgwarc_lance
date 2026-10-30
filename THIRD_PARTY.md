# Dependency notice review

## 0.1.24 development lockfile recheck (2026-10-30)

Strict target-filtered notice collection covered all 436 reachable Linux
x86_64 / PostgreSQL 16 Cargo packages with no missing primary texts. The
Cargo.lock SHA-256 is
`a25325bb3d397aafccadadaf4331585a7a6ab2045c81bc2952472becf2a548a7`. The
current RustSec database revision is
`2295466c4e9df26647fd67d8fa1c705ac2fc7f81` (1,395 advisories). Audit ran with
vulnerable, unsound and yanked dependencies denied; it found zero
vulnerabilities, unsound or yanked packages. Three unmaintained warnings
remain: `paste` 1.0.15, `proc-macro-error2` 2.0.1 and `serde_cbor` 0.11.2.

This recheck is tied to the source lockfile, not a redistribution package. The
checked-in CycloneDX SBOM and complete notice bundle below are still bound to
the 0.1.23 artifact. A matching 0.1.24 artifact-bound SBOM and bundle have not
been generated; this source preview does not grant binary redistribution or
production approval.

## Current 0.1.23 PG16 validation artifact notices and SBOM (2026-10-30)

Strict target-filtered notices collected primary texts for all 436 reachable
Cargo packages with no missing entries against Cargo.lock SHA-256
`147e894f1e41a2fa9c5cddf16feb68f9346661efbc450e4b895e92bdef2863ed`. The
CycloneDX 1.6 SBOM contains 441 components and binds the 0.1.23 PG16 validation
artifact SHA-256
`42045ec4de13487786015698ad9a871f02f96aefe07eacd289d14d66a4df61ec`, native
fingerprint
`b659227e940cfe9c4f44eabb4aaf31d4fcc390c973d6ee8e34a4f7926da934a5`, and the
full notice-bundle SHA-256
`b1a6eea300c87fb3787ce478b797be039a463aec0cc1f654dd9761b49d9c909b`; SBOM
SHA-256 is
`520c3b3d50135a8e6f603d71804a8a9015ed9c3a5026f0ce05314a8086f49228`.

The Linux x86_64 / PostgreSQL 16.15 validation package used Rust 1.96.0 and
cargo-pgrx 0.19.1. Its strict notice inventory is complete for the target Cargo
graph, and the native/compiler inventory records the matching PG16.15 host and
runtime libraries. This is a private validation artifact for source review;
the notices and SBOM do not grant binary redistribution rights or production
approval. The current RustSec audit checked the same lock graph against database
revision `ee1a49a2769a24a596afc1c20cb33748d2769586` (1,386 advisories): zero
vulnerabilities, unsound or yanked packages, with three unmaintained warnings
remaining.

## Previous 0.1.22 PG16 package notices and SBOM (2026-10-29)

Strict target-filtered notices collected primary texts for all 436 reachable
Cargo packages with no missing entries. The CycloneDX 1.6 SBOM contains 441
components: 436 Cargo packages and five native/compiler environment components.
It binds artifact SHA-256
`0e67b2c8259a70c986223f51307c7cd56534d3659d4862e69ac49a961d26e25f`, Cargo.lock
SHA-256
`854a1cb98e529e7c5fa089741f68d39753e586c8e9da3b12df1688c7ab94a8a9`, native
fingerprint
`899c2cafbce2e705cb84a9f380619a07041b02da835b718935fb93a44dfa871c`, and the
notice-bundle SHA-256
`2086597c781294ef80235f125e5aec2bb0dbe62b474c91f806a44a654541ad0d`. The
SBOM SHA-256 is
`9c69075fd81fa46ec9da3729a4b4a6b36843d964b485920060f185a220d7f232`.

The PG16.15 package used Rust 1.96.0 and cargo-pgrx 0.19.1. Its artifact was
installed and exercised with isolated PG16 fixtures; the detailed issue #6
scope and remaining limits are in [PUBLICATION.md](PUBLICATION.md).
`cargo-audit` 0.22.2 checked 557 packages against RustSec database revision
`40f5be6fb12ed93fc591bf4ab23b7d793b3f2239` (1,381 advisories). Vulnerabilities,
unsound packages and yanked packages were zero. Three unmaintained warnings
remain: `paste` 1.0.15, `proc-macro-error2` 2.0.1 and `serde_cbor` 0.11.2.

This inventory is technical evidence for Linux x86_64 / PostgreSQL 16.15 on
Debian Bookworm. It does not grant binary redistribution or production
approval. Regenerate it whenever the lockfile, native fingerprint, artifact
or target changes.

## Historical 0.1.21 PG16 package notices and SBOM (2026-10-28)

The Linux x86_64 / PostgreSQL 16.15 Bookworm candidate has strict target-filtered
package-level notices for all 436 reachable Cargo dependencies, with no
missing primary texts. `random_word` 0.5.2 is absent from this target graph and
is replaced by the project-owned Apache-2.0-compatible 0.5.3 English-word
compatibility package. The English word-list data has its own verified source
and checksum. For `seahash` 4.1.0, the bundle preserves the exact MIT text from
the reviewed upstream revision; it does not substitute a generic license.

The CycloneDX 1.6 SBOM contains 441 components: 436 Cargo packages and five
native/compiler environment components. It binds the PG16 artifact SHA-256
`f326f9b649c177e196768c91e08f819d8a65dc5d1bd92025c17a2d1ad20efb60`,
Cargo.lock SHA-256
`2c9fed1e80c4f7a1bf7acbbdf54e645632df53b991d4615c3f24e3abc158c36c`, native
fingerprint
`b017d3cfb809584f4a1bd2ad2cd4b6fea2407d8537750fa35839d751f68e6c02`, and
notice-bundle SHA-256
`8dd80c8147e61adc7809872ac7346598ec15168f49603026a787306ea31b43cd`.
The SBOM SHA-256 is
`b550be5747223dd147c55a40c972786ced7006d9de3e34f346269110bec9daa1`.
The matching package and database checks are documented in
[PACKAGING.md](PACKAGING.md).

`cargo-audit` 0.22.2 scanned 557 lockfile packages against RustSec database
revision `40f5be6fb12ed93fc591bf4ab23b7d793b3f2239` (1,381 advisories).
Vulnerabilities, unsound packages and yanked packages were all zero, with an
empty exception list. Three unmaintained warnings remain visible:
`paste` 1.0.15, `proc-macro-error2` 2.0.1 and `serde_cbor` 0.11.2.

This evidence covers Linux x86_64 / PostgreSQL 16.15 and the recorded
Bookworm build/runtime only. It is a technical notice and inventory result, not
legal clearance for binary redistribution or production approval. Regenerate
it whenever the lockfile, native fingerprint, artifact or target changes.

The project source is Apache-2.0. Dependency licenses remain their own terms.
`NOTICE` is a short project attribution, not a complete binary license bundle.
For redistribution, preserve dependency copyright/license texts and applicable
upstream NOTICE content. The [Apache redistribution terms](https://www.apache.org/licenses/LICENSE-2.0)
and [Mozilla MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/) describe the
relevant upstream requirements. In particular, recipients of MPL-covered code
must be informed how to obtain its corresponding source.

The 2026-10-01 Linux PG16 Cargo graph contained 437 normal/build dependencies,
excluding the extension itself. This historical conservative graph is not proof
that every crate's code survives linking. The current 0.1.20 target-specific
review is recorded below. It includes an MPL-2.0 package,
`option-ext` 0.2.0; the collector records its exact crate source download URL and
Cargo.lock checksum. That historical inventory predates the manifest-only compatibility patches
described in [vendor/README.md](vendor/README.md). The 2026-10-01 inventory did
not cover the compiler runtime, bundled native source or a bound distribution
artifact; the 0.1.20 PG16 follow-up below adds those checks without granting
legal redistribution clearance.

`tools/collect_dependency_notices.py` reads target-filtered Cargo metadata and
installed crate source files, collects original root and nested license/NOTICE
texts, deduplicates texts by SHA-256 and supplements REUSE per-file copyright
statements in Rust, C, C++ headers and assembly sources. Reviewed upstream texts absent from crate archives are preserved
in `third_party/upstream-licenses/`, with immutable Git revisions, source URLs
and checksums in its manifest. The collector performs no network access and
rejects modified overrides or paths escaping that directory. Schema version 3
records each upstream override's coverage: `package` supplies a package-level
text; `bundled-data` applies only to its named asset, verifies that asset's
SHA-256, and cannot clear a missing package license. Unknown coverage is
rejected. Path-patched crates are identified as local sources instead of being
reported as nonexistent crates.io downloads.

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

The 2026-10-01 PG16 review originally recorded these evidence gaps:

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
- `seahash` 4.1.0: manifest declares MIT; the package archive omitted its license
  text and the first source lookup did not retrieve it.

These are historical findings, not assertions that the packages cannot be
used. Do not fabricate copyright notices or claim the binary license gate
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
The fixed zstd crates remain checksummed crates.io dependencies. Issue #4
resolves the two package-level license gaps recorded in the earlier inventory.
Native/compiler notices and a matching artifact-bound SBOM still require actual
PG16 distribution evidence.

The refreshed target-filtered 0.1.20 inventory covers 440 normal/build package
records; Cargo.lock SHA-256 is
`5107326283ea0b6d0d535a5e500516c445fcd39f4a159568901f8656437bd45e`.
At that point in the source history, strict notice collection returned
`needs_review` (exit 2) for `random_word@0.5.2` and `seahash@4.1.0`. The
following issue #4 work records how both gaps were addressed. Binary
publication and source snapshot publication are separate decisions.

## Issue #4 package-level license review (2026-10-27)

The exact `random_word` 0.5.2 release source declares MIT but has no
package-level license text in either its crate archive or pinned upstream tree.
The PG16 graph only needs `all(Lang::En)` through Lance 7.0.0 data generation.
The dependency is replaced by project-authored `random_word` 0.5.3, licensed
under this project's Apache-2.0 terms. It contains no upstream Rust code and
uses only the exact English word-list asset; the upstream source declares that
data public domain and its notice and checksum are retained. The original
0.5.2 implementation is not included in the PG16 target graph.

The exact `seahash` 4.1.0 source declares MIT but its crate archive omitted the
license text. The official upstream repository added the project's missing
MIT `LICENSE` in commit
[`3088c5c912b70b586d27bf553fbe964e025a2c89`](https://gitlab.redox-os.org/redox-os/seahash/-/commit/3088c5c912b70b586d27bf553fbe964e025a2c89).
That exact upstream text is preserved and checksum-verified in the notice
manifest; it is not a generic license substitution.

The refreshed PG16 Linux x86_64 graph has 436 reachable normal/build package
records. Strict notice collection now returns `texts_collected`, with no
missing package-level primary texts. Its JSON records the active lock hash,
package sources, upstream revisions and notice hashes; bundled-data overrides
also verify and record the exact covered asset hash. Path dependencies are
shown as local sources, never as crates.io download URLs.

`tools/build_distribution_sbom.py` combines that strict report with a real
nonempty `.so`, the exact Cargo.lock, and a separate inventory of native/runtime
and compiler components and their checksum-verified license texts. It emits a
CycloneDX 1.6 SBOM and `NATIVE_AND_CARGO_NOTICES.txt`, binding the artifact hash,
lock hash, source commit, PG16 target, native fingerprint and notice bundle
hash. It refuses mismatched inputs or missing/changed texts. The actual PG16
binary and its builder/runtime license inventory still need to pass this tool;
the source-level notice pass alone is not C2 completion or binary clearance.

## Current RustSec review (2026-10-27)

`cargo-audit` 0.22.2 refreshed the RustSec database to revision
`99670cfc0bc574433034b75876a4b791ccaa2efb` (1,380 advisories) and scanned 557
Cargo.lock packages. On Linux x86_64, the default vulnerability check plus
`--deny unsound --deny yanked` found zero vulnerabilities, unsound packages or
yanked packages; no ignore exceptions are configured. Three informational
unmaintained warnings remain: `paste` 1.0.15, `proc-macro-error2` 2.0.1 and
`serde_cbor` 0.11.2. They remain visible for dependency-owner follow-up and are
not suppressed by the clean deny checks. Re-run audit against the current
RustSec database whenever the lockfile or distribution candidate changes.

The vendored Arrow IPC and Lance encoding zstd requirement patches preserve the
upstream archives, licenses and archive hashes and alter only the compatible
zstd requirement; their provenance, advisories addressed and removal conditions
are in [vendor/README.md](vendor/README.md). The `random_word` replacement's
source/data provenance and removal condition are recorded there as well. None
of these patches exempts the crate from RustSec scanning.

## 0.1.20 PG16 package notice and SBOM verification (2026-10-28)

The issue #4 C1–C3 checks are complete for the Linux x86_64 PostgreSQL 16.15
package candidate. C1 replaces `random_word` 0.5.2, whose package archive and
pinned source lacked its declared MIT text, with the project-owned Apache-2.0
0.5.3 compatibility package required by the target graph. The English word-list
asset's exact bytes and public-domain source declaration are tracked separately.
For `seahash` 4.1.0, the notice bundle uses the exact MIT text from its upstream
repository, not a guessed substitute. Provenance and removal conditions are in
[vendor/README.md](vendor/README.md).

Strict target-filtered notice collection passed for 436 reachable Cargo
packages with zero missing primary texts. The notice bundle is 5,137,808 bytes
and has SHA-256
`df3ca0236e1a296a490cd11d02df900d7f0a50c42515d410f0112b10f78a1fa7`. The
CycloneDX 1.6 SBOM contains 441 components: those 436 Cargo packages and five
compiler, runtime and PostgreSQL environment components. It binds artifact
SHA-256 `701e7050ca694d68d2f828fcc61928640828db49d5139e800620a5b40e896d29`,
Cargo.lock SHA-256
`3869b04a56336e1de2880b5b04a18b22110aa588525ff40d656ad0073cda4550`, PG16
native fingerprint `ccab4bc97d2c04590a86e380ddb232488e1dff8aaac139cc7dcbfee9092c87ab`,
and the verified artifact source commit `39f7e13e75bfbe63b384be78a5341f3dd6bdf95a`.
The SBOM SHA-256 is
`58ef2bbb4313f0db1f17c6bbfb0fb22a081e72f6cec9cd93139ca9793dee7080`.
The prior PG16 native validation was reused only after its fingerprint, lock,
artifact and test receipts matched; scope is Linux x86_64 / PG16.15 Bookworm.
Other PostgreSQL majors and distribution profiles were not covered.

`cargo-audit` 0.22.2 checked 557 packages against RustSec database revision
`99670cfc0bc574433034b75876a4b791ccaa2efb` (1,380 advisories), matching the
database's upstream `main` at verification. Vulnerabilities, unsound packages
and yanked packages were all zero, with no exceptions. Three unmaintained
warnings remain visible: `paste` 1.0.15, `proc-macro-error2` 2.0.1 and
`serde_cbor` 0.11.2. See the [RustSec advisory database](https://github.com/RustSec/advisory-db).

The exact artifact inventory and strict notice collection satisfy the issue's
technical C2 gate; they are not legal clearance to redistribute the binary.
Production approval, other target inventories and independent distribution
review remain open. Regenerate the notice bundle and SBOM whenever the lock,
native fingerprint, artifact or target changes.
