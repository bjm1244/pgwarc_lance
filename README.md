# pgwarc_lance

Experimental PostgreSQL extension for importing WARC/WARC.GZ records and
searching their text with BM25 and Lance vectors. It supports PostgreSQL
13–17, with PostgreSQL 16 as the primary development target.

**Release status:** source preview. Real-corpus retrieval quality, representative
performance, and a fresh full database regression run have not yet passed the
first-release gates. Do not put this preview on a production database.

The current implementation reached Phase 85 of its development roadmap
(현재 완료된 Phase 1-85). See [ROADMAP.md](ROADMAP.md) for remaining evidence,
[QUALITY.md](QUALITY.md) for evaluation inputs, and
[PACKAGING.md](PACKAGING.md) for installation and upgrade policy.

## Local development

Docker Compose creates a disposable PostgreSQL database bound to
`127.0.0.1:55432`. Set a nonempty local password first:

```bash
export PGWARC_DEV_PASSWORD="$(openssl rand -base64 24)"
make build
make up
make test-warc
make check-docs
```

`make verify` runs the full Docker-backed regression and quality smoke
suite. Never mount production data into the development Compose service.

The Python importer is `tools/warc_importer.py`. It accepts local WARC files
and can emit SQL or execute it with a caller-supplied psql command. Review the
archive and command before execution. Hash-derived vectors in examples are
test fixtures, not semantic embeddings.

## Security and release

Fresh installation revokes `PUBLIC EXECUTE` on extension functions. Grant
specific functions and table permissions only to trusted PostgreSQL roles;
Lance URIs run with the database server's filesystem and network access.
See [SECURITY.md](SECURITY.md).

The source is licensed under [Apache-2.0](LICENSE). Contributions are covered
by [CONTRIBUTING.md](CONTRIBUTING.md).
