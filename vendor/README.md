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

The Lance crate archive omitted a root license file; LICENSE is copied from
upstream commit `a15ae30939b9242d74b00aed1fb83abf7d15bf7f` referenced by its cargo VCS metadata.
