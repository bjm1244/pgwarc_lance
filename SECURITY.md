# Security policy

This project has not published a supported release. Do not use the development
Compose file or its PostgreSQL role as a production deployment template.

Please report a suspected vulnerability privately through GitHub's
**Report a vulnerability** feature once the public repository is available.
Avoid opening a public issue that includes exploit details or sensitive data.

The extension can read and write Lance datasets at URIs supplied to SQL
functions. Fresh installation revokes `PUBLIC EXECUTE` on extension
functions; PostgreSQL administrators must grant only the functions needed by
trusted roles and restrict filesystem and network access for the server
process. The Python importer executes a caller-supplied embedding command
and psql command; run it only with trusted arguments and files.

Security fixes will be tested against the affected PostgreSQL versions and
documented in release notes before a supported version is published.
