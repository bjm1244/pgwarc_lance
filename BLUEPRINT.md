# Design and release direction

The extension stores WARC metadata and BM25 terms in PostgreSQL and stores
vectors in a Lance dataset identified by a URI. The importer reads local
WARC/WARC.GZ files and generates SQL for controlled execution.

Public releases require independent retrieval labels, representative
performance, least-privilege database roles, and a disposable test environment.
The timeout and cancellation planning modules remain synthetic contracts until
backend identity, observer permissions, race handling, and cleanup are tested
against PostgreSQL.

See [ROADMAP.md](ROADMAP.md) for the current gates.
