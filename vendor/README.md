# Arrow IPC compatibility patch

`arrow-ipc-58.3.0` is the crates.io source with only its optional zstd dependency
requirement in Cargo.toml and Cargo.toml.orig changed: 0.13.0 → 0.14.3.
Original archive SHA-256:
`238438f0834483703d88896db6fe5a7138b2230debc31b34c0336c2996e3c64f`.
Upstream: https://github.com/apache/arrow-rs (Apache-2.0; the upstream license
and notice files are preserved). The crate's own version is unchanged.

This permits zstd 0.14.3 / zstd-safe 8.2.0 with the upstream fixes in
https://github.com/gyscos/zstd-rs/pull/384. No vulnerable zstd code is relabeled
and no advisory is suppressed. Arrow IPC source code is unchanged.
Remove this patch when a compatible upstream Arrow release adopts fixed zstd;
validate IPC compression, Lance read/write and PostgreSQL first.

`lance-encoding-7.0.0` likewise only changes the zstd requirement in its two
Cargo manifests from 0.13 to 0.14.3. Archive SHA-256: `7af54edf43dcf9d6a56cc636eb35d457e68373c6448dca3f0891b3325b4a24e6`.
Upstream: https://github.com/lance-format/lance (Apache-2.0).
Remove this patch when a compatible Lance release accepts zstd 0.14.3; verify
encoding, index write/read, Lance dataset I/O and PostgreSQL before deleting
the local copy.

The Lance crate archive omitted a root license file; LICENSE is copied from
upstream commit `a15ae30939b9242d74b00aed1fb83abf7d15bf7f` referenced by its cargo VCS metadata.

## `random_word` compatibility replacement

`random_word` 0.5.2 declares MIT in its Cargo manifest, but the exact upstream
source revision `dc1cab8d7951ff4285ea9158c8954d663fdd6fbf` and its published
crate archive contain no package-level primary license text. No generic MIT
text is substituted. The PG16 dependency graph only uses
`random_word::all(Lang::En)` from Lance `lance-datagen` 7.0.0, so
`random_word@0.5.2` is replaced by the project-authored, Apache-2.0
`random_word@0.5.3` package in `vendor/random-word-compat-0.5.3`.

The replacement contains no code from the original crate. Its `words.txt` is
the exact Brotli expansion of the English asset at the pinned upstream
revision; the compressed input SHA-256 is
`f10ec9f822e45c68dff80543eaf6f92d76b0f3b38f9826e286b433893eca4435` and the
expanded data SHA-256 is
`21be8d95167f1e0b7c8f6cc954d0ead534e21eaa95a5a3576d5b49f629df534c`. The
upstream `src/license/en.txt` identifies the ENABLE word-list data as public
domain, and its notice is preserved with the asset. The Rust implementation is
covered by the project license.

Remove this compatibility patch only when an upstream dependency with a
package-level primary license text replaces the gap and the required data
generation API and output are verified against the pinned word-list checksum.
Do not enable additional languages or APIs without their own source and license
review.
