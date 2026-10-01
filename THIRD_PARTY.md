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
Cargo.lock checksum. No dependency source was modified. Compiler runtime,
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

The omitted `deepsize_derive` 0.1.2 text was recovered at upstream commit
`1383a60f86a0ee0a67c76eead74af442eb8a3c9b`: published Rust source and
`Cargo.toml.orig` match that commit byte-for-byte. The override records this
verification because the crate-recorded revision is unavailable.

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
