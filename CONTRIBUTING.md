# Contributing

Please open an issue describing the behavior or proposed change before
starting a large implementation. Include the PostgreSQL major version,
reproduction steps, and a small WARC sample that you are allowed to share.
Do not include private archives, credentials, or user data.

For a code change, run:

```bash
make test-warc
make check-docs
cargo fmt --check
```

Changes to PostgreSQL functions, extension SQL, the importer, or release
artifacts also require the relevant database tests in a disposable
environment. Record the exact command and result in the pull request.
The development Compose database is disposable and must not contain
production data.
