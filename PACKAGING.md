# Packaging

`Cargo.lock`, the Rust toolchain default in `Dockerfile`, and pgrx 0.19.1
define the current build inputs. The first public version is 0.1.0.
Use `make release-artifacts-matrix` and
`make smoke-release-artifacts-matrix` to validate fresh installation
for PostgreSQL 13–17 in disposable containers. Run `make verify` for the
full PostgreSQL 16 regression suite.

No earlier public version exists, so an old-to-new upgrade test is not
applicable to the first release. Starting with the next version, retain
immutable old artifacts, add `pgwarc_lance--OLD--NEW.sql`, and run
`make doctor-release-upgrade-inputs` followed by
`make smoke-release-upgrade-matrix`.

The Compose file is a local development environment and is not a production
deployment manifest. A production operator must supply PostgreSQL role grants,
storage restrictions, backup/restore procedures, and monitoring.
