"""Durable, record-checkpointed execution path for the WARC importer.

The checkpoint database is operator-owned state, separate from PostgreSQL and
Lance. It is private because parsed document text and vectors are retained so
that database, embedding, and process failures do not require rebuilding the
already validated input spool.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import shlex
import selectors
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


SCHEMA_VERSION = 1
SQLITE_PAGE_BYTES = 4096
SQLITE_CACHE_KIB = 2048


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_identity(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"input must be a regular non-symlink file: {path}")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise ValueError(f"input changed while its checksum was being read: {path}")
    return {
        "path": str(path.resolve()),
        "size_bytes": after.st_size,
        "sha256": digest.hexdigest(),
        "_stat_signature": (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
    }


def _settings(
    args: Any, lance_mode: str, importer_source_path: Path,
    pdf_extractor: Any, docx_extractor: Any,
) -> dict[str, Any]:
    embedding_jsonl_sha256 = None
    if args.embedding_jsonl:
        embedding_jsonl_sha256 = _file_identity(args.embedding_jsonl)["sha256"]
    return {
        "schema_version": SCHEMA_VERSION,
        "importer_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes() + importer_source_path.read_bytes()
        ).hexdigest(),
        "max_record_bytes": args.max_record_bytes,
        "min_text_chars": args.min_text_chars,
        "limit": args.limit,
        "charset_overrides": sorted(args.charset_override),
        "charset_record_overrides": sorted(args.charset_record_override),
        "document_error_policy": args.document_error_policy,
        "extract_docx": args.extract_docx,
        "pdftotext_path": args.pdftotext,
        "pdftotext_version": pdf_extractor.version if pdf_extractor else None,
        "pdftotext_sha256": hashlib.sha256(Path(pdf_extractor.executable).read_bytes()).hexdigest() if pdf_extractor else None,
        "pdf_timeout_seconds": args.pdf_timeout_seconds,
        "bm25_mode": args.bm25_mode,
        "batch_size": args.batch_size,
        "batch_max_bytes": args.batch_max_bytes,
        "skip_schema": args.skip_schema,
        "lance_mode": lance_mode if args.lance_uri else "none",
        "lance_uri_sha256": hashlib.sha256(args.lance_uri.encode()).hexdigest() if args.lance_uri else None,
        "vector_dim": args.vector_dim,
        "no_hash_vectors": args.no_hash_vectors,
        "embedding_command_sha256": hashlib.sha256(args.embedding_command.encode()).hexdigest() if args.embedding_command else None,
        "embedding_jsonl_sha256": embedding_jsonl_sha256,
    }


class CheckpointStore:
    """SQLite ledger protected by an advisory process lock and a hard page cap."""

    def __init__(self, path: Path, max_bytes: int, source_paths: set[Path]) -> None:
        if path.is_symlink():
            raise ValueError("checkpoint database must not be a symlink")
        resolved = path.resolve()
        if resolved in source_paths:
            raise ValueError("checkpoint database must not overwrite a WARC input")
        if not path.parent.is_dir():
            raise ValueError("checkpoint database parent directory must already exist")
        if path.exists() and not path.is_file():
            raise ValueError("checkpoint database path must be a regular file")
        if path.exists() and path.stat().st_size > max_bytes:
            raise ValueError("existing checkpoint database exceeds --checkpoint-max-bytes")
        self.path = resolved
        self.lock_path = Path(str(self.path) + ".lock")
        self.lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(self.lock_fd, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.lock_fd)
            raise RuntimeError("another importer currently owns this checkpoint database") from exc

        self.db: sqlite3.Connection | None = None
        self.max_bytes = max_bytes
        try:
            self.db = sqlite3.connect(self.path, timeout=30, isolation_level="DEFERRED")
            os.chmod(self.path, 0o600)
            self.db.execute(f"PRAGMA page_size={SQLITE_PAGE_BYTES}")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_KIB}")
            self.db.execute("PRAGMA temp_store=FILE")
            self.db.execute("PRAGMA foreign_keys=ON")
            max_pages = max_bytes // SQLITE_PAGE_BYTES
            result = self.db.execute(f"PRAGMA max_page_count={max_pages}").fetchone()
            current_pages = self.db.execute("PRAGMA page_count").fetchone()[0]
            if current_pages > max_pages or result[0] > max_pages:
                raise ValueError("checkpoint database already exceeds --checkpoint-max-bytes")
            self._create_schema()
            self._reconcile_interrupted_work()
        except BaseException:
            if self.db is not None:
                self.db.close()
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            raise

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None
        fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
        os.close(self.lock_fd)

    def __enter__(self) -> CheckpointStore:
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        assert self.db is not None
        return self.db

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS checkpoint_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS import_runs (
                run_id TEXT PRIMARY KEY,
                config_sha256 TEXT NOT NULL,
                target_sha256 TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                summary_json TEXT
            );
            CREATE TABLE IF NOT EXISTS archive_checkpoints (
                archive_key TEXT PRIMARY KEY,
                config_sha256 TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_size_bytes INTEGER NOT NULL,
                status TEXT NOT NULL,
                next_ordinal INTEGER NOT NULL DEFAULT 0,
                raw_records INTEGER NOT NULL DEFAULT 0,
                record_fingerprint_sha256 TEXT,
                last_error_kind TEXT,
                UNIQUE(config_sha256, source_path, source_sha256)
            );
            CREATE TABLE IF NOT EXISTS run_archives (
                run_id TEXT NOT NULL REFERENCES import_runs(run_id),
                archive_key TEXT NOT NULL REFERENCES archive_checkpoints(archive_key),
                source_order INTEGER NOT NULL,
                PRIMARY KEY(run_id, archive_key)
            );
            CREATE TABLE IF NOT EXISTS record_checkpoints (
                config_sha256 TEXT NOT NULL,
                archive_key TEXT NOT NULL REFERENCES archive_checkpoints(archive_key),
                ordinal INTEGER NOT NULL,
                doc_id INTEGER,
                revision TEXT,
                record_json TEXT,
                vector_json TEXT,
                outcome TEXT NOT NULL,
                stage_status TEXT NOT NULL,
                vector_status TEXT NOT NULL,
                pg_status TEXT NOT NULL,
                error_kind TEXT,
                error_sha256 TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(config_sha256, archive_key, ordinal)
            );
            CREATE INDEX IF NOT EXISTS record_checkpoints_doc_idx
                ON record_checkpoints(config_sha256, doc_id);
            CREATE INDEX IF NOT EXISTS record_checkpoints_pending_idx
                ON record_checkpoints(config_sha256, outcome, pg_status, vector_status);
            CREATE TABLE IF NOT EXISTS run_records (
                run_id TEXT NOT NULL REFERENCES import_runs(run_id),
                doc_id INTEGER NOT NULL,
                archive_key TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                PRIMARY KEY(run_id, doc_id),
                UNIQUE(run_id, archive_key, ordinal)
            );
            CREATE TABLE IF NOT EXISTS run_seen_doc_ids (
                run_id TEXT NOT NULL REFERENCES import_runs(run_id),
                doc_id INTEGER NOT NULL,
                archive_key TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                PRIMARY KEY(run_id, doc_id)
            );
            CREATE TABLE IF NOT EXISTS run_vector_ids (
                run_id TEXT NOT NULL REFERENCES import_runs(run_id),
                doc_id INTEGER NOT NULL,
                PRIMARY KEY(run_id, doc_id)
            );
            """
        )
        meta = self.connection.execute(
            "SELECT value FROM checkpoint_meta WHERE key='schema_version'"
        ).fetchone()
        if meta and int(meta[0]) != SCHEMA_VERSION:
            raise ValueError(
                f"checkpoint schema {meta[0]} is unsupported; expected {SCHEMA_VERSION}"
            )
        self.connection.execute(
            "INSERT OR IGNORE INTO checkpoint_meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )

    def _reconcile_interrupted_work(self) -> None:
        now = _now()
        interrupted = [row[0] for row in self.connection.execute(
            "SELECT run_id FROM import_runs WHERE status='running'"
        )]
        with self.connection:
            self.connection.execute(
                "UPDATE import_runs SET status='interrupted',updated_at=? WHERE status='running'",
                (now,),
            )
            for field in ("stage_status", "vector_status", "pg_status"):
                self.connection.execute(
                    f"UPDATE record_checkpoints SET {field}='failed',error_kind='worker_interrupted',"
                    "error_sha256=NULL,updated_at=? WHERE " + field + "='inflight'",
                    (now,),
                )
            for run_id in interrupted:
                self.connection.execute("DELETE FROM run_records WHERE run_id=?", (run_id,))
                self.connection.execute("DELETE FROM run_seen_doc_ids WHERE run_id=?", (run_id,))
                self.connection.execute("DELETE FROM run_vector_ids WHERE run_id=?", (run_id,))

    def start_run(self, config_sha256: str, target_sha256: str | None) -> str:
        if target_sha256 is not None:
            prior = self.connection.execute(
                "SELECT DISTINCT target_sha256 FROM import_runs "
                "WHERE config_sha256=? AND target_sha256 IS NOT NULL AND target_sha256<>? LIMIT 1",
                (config_sha256, target_sha256),
            ).fetchone()
            if prior:
                raise ValueError(
                    "checkpoint database is already bound to a different PostgreSQL target; use a separate checkpoint database"
                )
        run_id = str(uuid.uuid4())
        now = _now()
        with self.connection:
            self.connection.execute(
                "INSERT INTO import_runs(run_id,config_sha256,target_sha256,status,started_at,updated_at) "
                "VALUES(?,?,?,'running',?,?)",
                (run_id, config_sha256, target_sha256, now, now),
            )
        return run_id

    def bind_target(self, run_id: str, target_sha256: str) -> None:
        with self.connection:
            prior = self.connection.execute(
                "SELECT DISTINCT target_sha256 FROM import_runs "
                "WHERE config_sha256=(SELECT config_sha256 FROM import_runs WHERE run_id=?) "
                "AND target_sha256 IS NOT NULL AND target_sha256<>? LIMIT 1",
                (run_id, target_sha256),
            ).fetchone()
            if prior:
                raise ValueError(
                    "checkpoint database is already bound to a different PostgreSQL target; use a separate checkpoint database"
                )
            self.connection.execute(
                "UPDATE import_runs SET target_sha256=?,updated_at=? WHERE run_id=?",
                (target_sha256, _now(), run_id),
            )

    def add_archive(self, run_id: str, config_sha256: str, identity: dict[str, Any], order: int) -> str:
        archive_key = _json_hash({
            "config_sha256": config_sha256,
            "source_path": identity["path"],
            "source_sha256": identity["sha256"],
        })
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO archive_checkpoints(archive_key,config_sha256,source_path,"
                "source_sha256,source_size_bytes,status) VALUES(?,?,?,?,?,'pending')",
                (archive_key, config_sha256, identity["path"], identity["sha256"], identity["size_bytes"]),
            )
            self.connection.execute(
                "INSERT INTO run_archives(run_id,archive_key,source_order) VALUES(?,?,?)",
                (run_id, archive_key, order),
            )
        return archive_key

    def checkpoint_record(
        self,
        *,
        config_sha256: str,
        archive_key: str,
        ordinal: int,
        outcome: str,
        stage_status: str,
        vector_status: str,
        pg_status: str,
        doc_id: int | None = None,
        revision: str | None = None,
        record_json: str | None = None,
        error_kind: str | None = None,
        error_sha256: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO record_checkpoints(config_sha256,archive_key,ordinal,doc_id,revision,"
                "record_json,outcome,stage_status,vector_status,pg_status,error_kind,error_sha256,"
                "attempts,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?) "
                "ON CONFLICT(config_sha256,archive_key,ordinal) DO UPDATE SET "
                "doc_id=excluded.doc_id,revision=excluded.revision,record_json=excluded.record_json,"
                "outcome=excluded.outcome,stage_status=excluded.stage_status,"
                "vector_status=excluded.vector_status,pg_status=excluded.pg_status,"
                "error_kind=excluded.error_kind,error_sha256=excluded.error_sha256,"
                "attempts=record_checkpoints.attempts+1,updated_at=excluded.updated_at",
                (config_sha256, archive_key, ordinal, doc_id, revision, record_json, outcome,
                 stage_status, vector_status, pg_status, error_kind, error_sha256, _now()),
            )

    def record_row(self, config_sha256: str, archive_key: str, ordinal: int) -> sqlite3.Row | None:
        self.connection.row_factory = sqlite3.Row
        return self.connection.execute(
            "SELECT * FROM record_checkpoints WHERE config_sha256=? AND archive_key=? AND ordinal=?",
            (config_sha256, archive_key, ordinal),
        ).fetchone()

    def register_run_record(self, run_id: str, archive_key: str, ordinal: int, doc_id: int) -> bool:
        prior = self.connection.execute(
            "SELECT archive_key,ordinal FROM run_seen_doc_ids WHERE run_id=? AND doc_id=?",
            (run_id, doc_id),
        ).fetchone()
        if prior:
            return prior == (archive_key, ordinal)
        with self.connection:
            self.connection.execute(
                "INSERT INTO run_seen_doc_ids(run_id,doc_id,archive_key,ordinal) VALUES(?,?,?,?)",
                (run_id, doc_id, archive_key, ordinal),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO run_records(run_id,doc_id,archive_key,ordinal) VALUES(?,?,?,?)",
                (run_id, doc_id, archive_key, ordinal),
            )
        return True

    def begin_record(self, config_sha256: str, archive_key: str, ordinal: int) -> None:
        now = _now()
        with self.connection:
            self.connection.execute(
                "INSERT INTO record_checkpoints(config_sha256,archive_key,ordinal,outcome,stage_status,"
                "vector_status,pg_status,attempts,updated_at) VALUES(?,?,?,'failed','inflight','pending','pending',0,?) "
                "ON CONFLICT(config_sha256,archive_key,ordinal) DO UPDATE SET "
                "outcome='failed',stage_status='inflight',error_kind=NULL,error_sha256=NULL,updated_at=excluded.updated_at",
                (config_sha256, archive_key, ordinal, now),
            )

    def advance_archive(self, archive_key: str, next_ordinal: int, raw_records: int, status: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE archive_checkpoints SET next_ordinal=?,raw_records=?,status=?,last_error_kind=NULL "
                "WHERE archive_key=?",
                (next_ordinal, raw_records, status, archive_key),
            )

    def fail_archive(self, archive_key: str, status: str, error_kind: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE archive_checkpoints SET status=?,last_error_kind=? WHERE archive_key=?",
                (status, error_kind, archive_key),
            )

    def archive_stats(self, archive_key: str) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT outcome,COUNT(*) FROM record_checkpoints WHERE archive_key=? GROUP BY outcome",
            (archive_key,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def finish_archive(self, archive_key: str) -> str:
        digest = hashlib.sha256()
        for doc_id, revision, ordinal in self.connection.execute(
            "SELECT doc_id,revision,ordinal FROM record_checkpoints "
            "WHERE archive_key=? AND outcome='ready' ORDER BY ordinal",
            (archive_key,),
        ):
            digest.update(f"{ordinal}\t{doc_id}\t{revision}\n".encode())
        value = digest.hexdigest()
        with self.connection:
            self.connection.execute(
                "UPDATE archive_checkpoints SET record_fingerprint_sha256=? WHERE archive_key=?",
                (value, archive_key),
            )
        return value

    def iter_records(self, run_id: str, where: str = "", parameters: tuple[Any, ...] = ()) -> Iterator[Any]:
        from_run = (
            "SELECT r.record_json FROM run_records rr JOIN record_checkpoints r "
            "ON r.archive_key=rr.archive_key AND r.ordinal=rr.ordinal "
            "WHERE rr.run_id=? AND r.outcome='ready' "
        )
        cursor = self.connection.execute(from_run + where, (run_id, *parameters))
        for (record_json,) in cursor:
            yield json.loads(record_json)

    def pending_vector_records(self, run_id: str) -> Iterator[Any]:
        query = (
            "SELECT r.record_json FROM run_records rr JOIN record_checkpoints r "
            "ON r.archive_key=rr.archive_key AND r.ordinal=rr.ordinal "
            "WHERE rr.run_id=? AND r.outcome='ready' AND r.vector_status IN ('pending','failed') "
            "ORDER BY rr.archive_key,rr.ordinal"
        )
        for (record_json,) in self.connection.execute(query, (run_id,)):
            yield json.loads(record_json)

    def pending_import_rows(self, run_id: str) -> Iterator[tuple[str, int, dict[str, Any]]]:
        query = (
            "SELECT rr.archive_key,rr.ordinal,r.record_json FROM run_records rr "
            "JOIN record_checkpoints r ON r.archive_key=rr.archive_key AND r.ordinal=rr.ordinal "
            "WHERE rr.run_id=? AND r.outcome='ready' AND r.pg_status IN ('pending','failed') "
            "AND r.vector_status IN ('not_required','ready') ORDER BY rr.archive_key,rr.ordinal"
        )
        for archive_key, ordinal, record_json in self.connection.execute(query, (run_id,)):
            yield archive_key, ordinal, json.loads(record_json)

    def get_record(self, run_id: str, doc_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT r.record_json,r.vector_json "
            "FROM run_records rr JOIN record_checkpoints r "
            "ON r.archive_key=rr.archive_key AND r.ordinal=rr.ordinal "
            "WHERE rr.run_id=? AND rr.doc_id=?",
            (run_id, doc_id),
        ).fetchone()
        if not row:
            return None
        record = json.loads(row[0])
        record["_vector"] = json.loads(row[1]) if row[1] else None
        return record

    def set_vector(self, run_id: str, doc_id: int, vector: list[float]) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE record_checkpoints SET vector_json=?,vector_status='ready',error_kind=NULL,error_sha256=NULL,updated_at=? "
                "WHERE (archive_key,ordinal) IN (SELECT archive_key,ordinal FROM run_records WHERE run_id=? AND doc_id=?)",
                (json.dumps(vector, separators=(",", ":")), _now(), run_id, doc_id),
            )

    def set_vector_status(self, run_id: str, doc_id: int, status: str, error_kind: str | None) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE record_checkpoints SET vector_status=?,error_kind=?,updated_at=? "
                "WHERE (archive_key,ordinal) IN (SELECT archive_key,ordinal FROM run_records WHERE run_id=? AND doc_id=?)",
                (status, error_kind, _now(), run_id, doc_id),
            )

    def set_pg_status(self, run_id: str, doc_ids: Iterable[int], status: str, error_kind: str | None = None) -> None:
        now = _now()
        with self.connection:
            for doc_id in doc_ids:
                self.connection.execute(
                    "UPDATE record_checkpoints SET pg_status=?,error_kind=?,error_sha256=NULL,updated_at=? "
                    "WHERE (archive_key,ordinal) IN (SELECT archive_key,ordinal FROM run_records WHERE run_id=? AND doc_id=?)",
                    (status, error_kind, now, run_id, doc_id),
                )

    def summary_counts(self, run_id: str) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT r.outcome,COUNT(*) FROM run_archives ra JOIN record_checkpoints r "
            "ON r.archive_key=ra.archive_key WHERE ra.run_id=? GROUP BY r.outcome",
            (run_id,),
        ).fetchall()
        result = {"imported": 0, "failed": 0, "skipped": 0, "metadata_only": 0}
        for outcome, count in rows:
            if outcome == "ready":
                result["imported"] += count
            elif outcome == "failed":
                result["failed"] += count
            elif outcome == "skipped":
                result["skipped"] += count
        result["metadata_only"] = self.connection.execute(
            "SELECT COUNT(*) FROM run_archives ra JOIN record_checkpoints r "
            "ON r.archive_key=ra.archive_key WHERE ra.run_id=? AND r.outcome='ready' "
            "AND json_extract(r.record_json,'$.extraction_status')='metadata_only'",
            (run_id,),
        ).fetchone()[0]
        return result

    def state_counts(self, run_id: str) -> dict[str, int]:
        return {
            "pending_vectors": self.connection.execute(
                "SELECT COUNT(*) FROM run_records rr JOIN record_checkpoints r ON r.archive_key=rr.archive_key AND r.ordinal=rr.ordinal "
                "WHERE rr.run_id=? AND r.outcome='ready' AND r.vector_status IN ('pending','failed')", (run_id,)
            ).fetchone()[0],
            "pending_database": self.connection.execute(
                "SELECT COUNT(*) FROM run_records rr JOIN record_checkpoints r ON r.archive_key=rr.archive_key AND r.ordinal=rr.ordinal "
                "WHERE rr.run_id=? AND r.outcome='ready' AND r.pg_status<>'complete'", (run_id,)
            ).fetchone()[0],
            "completed_database": self.connection.execute(
                "SELECT COUNT(*) FROM run_records rr JOIN record_checkpoints r ON r.archive_key=rr.archive_key AND r.ordinal=rr.ordinal "
                "WHERE rr.run_id=? AND r.outcome='ready' AND r.pg_status='complete'", (run_id,)
            ).fetchone()[0],
        }

    def finish_run(self, run_id: str, status: str, summary: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE import_runs SET status=?,updated_at=?,summary_json=? WHERE run_id=?",
                (status, _now(), json.dumps(summary, sort_keys=True), run_id),
            )
            self.connection.execute("DELETE FROM run_records WHERE run_id=?", (run_id,))
            self.connection.execute("DELETE FROM run_seen_doc_ids WHERE run_id=?", (run_id,))
            self.connection.execute("DELETE FROM run_vector_ids WHERE run_id=?", (run_id,))


class CheckpointVectors:
    def __init__(self, store: CheckpointStore, run_id: str, wi: Any, dim: int, hash_vectors: bool) -> None:
        self.store = store
        self.run_id = run_id
        self.wi = wi
        self.dim = dim
        self.hash_vectors = hash_vectors

    def get(self, doc_id: int) -> list[float] | None:
        row = self.store.get_record(self.run_id, doc_id)
        if not row or row.get("extraction_status") == "metadata_only":
            return None
        vector = row.get("_vector")
        if vector is not None:
            return vector
        if self.hash_vectors:
            return self.wi.hash_embedding(row["text"], self.dim)
        return None


def _target_fingerprint(psql: str) -> str:
    query = (
        "SELECT concat_ws('|', current_database(), current_user, "
        "coalesce(inet_server_addr()::text,'local'), inet_server_port(), "
        "current_setting('server_version_num'),current_setting('data_directory'))"
    )
    try:
        result = subprocess.run(
            [*shlex.split(psql), "-X", "-A", "-t", "-q", "-c", query],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("could not identify the PostgreSQL target for the checkpoint") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("could not identify the PostgreSQL target for the checkpoint")
    return hashlib.sha256(result.stdout.strip().encode()).hexdigest()


def _stage_record(
    *, store: CheckpointStore, run_id: str, config_sha256: str, archive_key: str,
    ordinal: int, raw: Any, args: Any, charset_policy: Any,
    pdf_extractor: Any, docx_extractor: Any, retention: Any, vector_required: bool,
    wi: Any,
) -> tuple[str, bool]:
    cached = store.record_row(config_sha256, archive_key, ordinal)
    if cached and cached["stage_status"] in {"ready", "skipped"}:
        record_id = raw.headers.get("warc-record-id")
        charset_policy.observe_record(record_id)
        if cached["outcome"] == "ready" and record_id in charset_policy.record_overrides:
            charset_policy.record_used[record_id] = charset_policy.record_used.get(record_id, 0) + 1
        if cached["outcome"] == "ready" and cached["doc_id"] is not None:
            if not store.register_run_record(run_id, archive_key, ordinal, cached["doc_id"]):
                error = "duplicate_doc_id"
                store.checkpoint_record(
                    config_sha256=config_sha256, archive_key=archive_key, ordinal=ordinal,
                    outcome="failed", stage_status="failed", vector_status="failed", pg_status="failed",
                    doc_id=cached["doc_id"], error_kind=error,
                )
                return "failed", False
        return cached["outcome"], True

    store.begin_record(config_sha256, archive_key, ordinal)
    try:
        record = wi.import_record_from_warc(
            raw, args.max_record_bytes, charset_policy, pdf_extractor, docx_extractor, retention
        )
        if record is None or (
            record.extraction_status != "metadata_only" and len(record.text) < args.min_text_chars
        ):
            store.checkpoint_record(
                config_sha256=config_sha256, archive_key=archive_key, ordinal=ordinal,
                outcome="skipped", stage_status="skipped", vector_status="not_required",
                pg_status="not_required",
            )
            return "skipped", False

        record_id = raw.headers.get("warc-record-id")
        if record_id in charset_policy.record_overrides:
            charset_policy.record_used[record_id] = charset_policy.record_used.get(record_id, 0) + 1
        if not store.register_run_record(run_id, archive_key, ordinal, record.doc_id):
            store.checkpoint_record(
                config_sha256=config_sha256, archive_key=archive_key, ordinal=ordinal,
                outcome="failed", stage_status="failed", vector_status="failed", pg_status="failed",
                doc_id=record.doc_id, error_kind="duplicate_doc_id",
            )
            return "failed", False

        revision = wi.record_revision(record)
        vector_status = "not_required"
        if vector_required and record.extraction_status != "metadata_only":
            vector_status = "ready" if not (args.embedding_command or args.embedding_jsonl) else "pending"
        store.checkpoint_record(
            config_sha256=config_sha256, archive_key=archive_key, ordinal=ordinal,
            outcome="ready", stage_status="ready", vector_status=vector_status,
            pg_status="pending" if args.execute else "pending", doc_id=record.doc_id,
            revision=revision,
            record_json=json.dumps(__import__("dataclasses").asdict(record), ensure_ascii=False, separators=(",", ":")),
        )
        return "ready", False
    except Exception as exc:
        store.checkpoint_record(
            config_sha256=config_sha256, archive_key=archive_key, ordinal=ordinal,
            outcome="failed", stage_status="failed", vector_status="failed", pg_status="failed",
            error_kind=type(exc).__name__.lower(), error_sha256=hashlib.sha256(str(exc).encode()).hexdigest(),
        )
        return "failed", False


def _run_embedding_command(records: list[dict[str, Any]], args: Any, wi: Any) -> tuple[dict[int, list[float]], dict[int, str]]:
    expected = {int(record["doc_id"]) for record in records}
    payload = "".join(
        json.dumps(wi.record_to_json(wi.ImportRecord(**record)), ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    try:
        process = subprocess.Popen(
            args.embedding_command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return {}, {doc_id: "embedding_start_failed" for doc_id in expected}

    payload_bytes = payload.encode("utf-8")

    def feed_stdin() -> None:
        assert process.stdin is not None
        try:
            process.stdin.write(payload_bytes)
            process.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass

    writer = threading.Thread(target=feed_stdin, name="pgwarc-embedding-input", daemon=True)
    writer.start()
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + args.embedding_timeout
    max_line_bytes = 65536 + args.vector_dim * 64
    max_output_bytes = min(args.checkpoint_max_memory_bytes, len(expected) * max_line_bytes)
    output_bytes = 0
    buffer = bytearray()
    vectors: dict[int, list[float]] = {}
    failures: dict[int, str] = {}
    seen: set[int] = set()
    malformed = False
    line_no = 0
    eof = False
    failure: str | None = None

    def consume(raw_line: bytes) -> None:
        nonlocal malformed, line_no
        line_no += 1
        if not raw_line.strip():
            return
        try:
            row = json.loads(raw_line.decode("utf-8"))
            if not isinstance(row, dict):
                raise ValueError("row_not_object")
            raw_id = row.get("doc_id", row.get("id"))
            if raw_id is None:
                raise ValueError("missing_doc_id")
            doc_id = int(raw_id)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            malformed = True
            return
        if doc_id not in expected:
            malformed = True
            return
        if doc_id in seen:
            failures[doc_id] = "duplicate_embedding"
            vectors.pop(doc_id, None)
            return
        seen.add(doc_id)
        try:
            vectors[doc_id] = wi.parse_vector(row.get("vector"), doc_id, args.vector_dim, f"embedding command:{line_no}")
        except ValueError:
            failures[doc_id] = "invalid_embedding"

    try:
        while not eof:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "embedding_timeout"
                break
            events = selector.select(min(remaining, 0.1))
            if not events:
                continue
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                eof = True
                break
            output_bytes += len(chunk)
            if output_bytes > max_output_bytes:
                failure = "embedding_output_limit"
                break
            buffer.extend(chunk)
            while True:
                newline = buffer.find(b"\n")
                if newline < 0:
                    if len(buffer) > max_line_bytes:
                        failure = "embedding_line_limit"
                    break
                if newline + 1 > max_line_bytes:
                    failure = "embedding_line_limit"
                    break
                line = bytes(buffer[: newline + 1])
                del buffer[: newline + 1]
                consume(line)
            if failure:
                break
        if not failure and buffer:
            if len(buffer) > max_line_bytes:
                failure = "embedding_line_limit"
            else:
                consume(bytes(buffer))
        if failure:
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            failure = "embedding_timeout"
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:
                pass
            process.wait()
    except BaseException:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        selector.close()
        writer.join(timeout=1)
        process.stdout.close()

    if failure:
        return {}, {doc_id: failure for doc_id in expected}
    if process.returncode:
        return {}, {doc_id: f"embedding_exit_{process.returncode}" for doc_id in expected}
    if malformed:
        return {}, {doc_id: "invalid_embedding_output" for doc_id in expected}
    for doc_id in expected - vectors.keys() - failures.keys():
        failures[doc_id] = "missing_embedding"
    return vectors, failures


def _load_embedding_file(store: CheckpointStore, run_id: str, args: Any, wi: Any) -> dict[str, int]:
    counts = {"valid": 0, "failed": 0, "ignored": 0}
    store.connection.execute("DELETE FROM run_vector_ids WHERE run_id=?", (run_id,))
    max_line_bytes = 65536 + args.vector_dim * 64
    with args.embedding_jsonl.open("rb") as source:
        for line_no in range(1, 2**63):
            raw_line = source.readline(max_line_bytes + 1)
            if not raw_line:
                break
            if len(raw_line) > max_line_bytes:
                raise ValueError(f"{args.embedding_jsonl}:{line_no}: embedding row exceeds size limit")
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{args.embedding_jsonl}:{line_no}: embedding JSONL must be UTF-8") from exc
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("row_not_object")
                raw_id = row.get("doc_id", row.get("id"))
                if raw_id is None:
                    raise ValueError("missing_doc_id")
                doc_id = int(raw_id)
            except (json.JSONDecodeError, TypeError, ValueError):
                counts["failed"] += 1
                continue
            inserted = store.connection.execute(
                "INSERT OR IGNORE INTO run_vector_ids(run_id,doc_id) VALUES(?,?)", (run_id, doc_id)
            ).rowcount
            if not inserted:
                store.set_vector_status(run_id, doc_id, "failed", "duplicate_embedding")
                counts["failed"] += 1
                continue
            target = store.connection.execute(
                "SELECT r.record_json,r.vector_status FROM run_records rr "
                "JOIN record_checkpoints r ON r.archive_key=rr.archive_key AND r.ordinal=rr.ordinal "
                "WHERE rr.run_id=? AND rr.doc_id=?",
                (run_id, doc_id),
            ).fetchone()
            if not target:
                counts["ignored"] += 1
                continue
            record = json.loads(target[0])
            if record.get("extraction_status") == "metadata_only":
                counts["ignored"] += 1
                continue
            try:
                vector = wi.parse_vector(row.get("vector"), doc_id, args.vector_dim, f"{args.embedding_jsonl}:{line_no}")
            except ValueError:
                store.set_vector_status(run_id, doc_id, "failed", "invalid_embedding")
                counts["failed"] += 1
                continue
            store.set_vector(run_id, doc_id, vector)
            counts["valid"] += 1

    for (doc_id,) in store.connection.execute(
        "SELECT rr.doc_id FROM run_records rr JOIN record_checkpoints r "
        "ON r.archive_key=rr.archive_key AND r.ordinal=rr.ordinal "
        "WHERE rr.run_id=? AND r.outcome='ready' AND r.vector_status IN ('pending','failed')",
        (run_id,),
    ).fetchall():
        store.set_vector_status(run_id, doc_id, "failed", "missing_embedding")
        counts["failed"] += 1
    return counts


def _embed_pending(store: CheckpointStore, run_id: str, args: Any, wi: Any) -> dict[str, int]:
    counts = {"attempted": 0, "completed": 0, "failed": 0}
    pending = (
        wi.record_batches(
            (wi.ImportRecord(**record) for record in store.pending_vector_records(run_id)),
            args.batch_size, args.batch_max_bytes, 0,
        )
    )
    for batch in pending:
        rows = [wi.dataclasses.asdict(record) for record in batch]
        doc_ids = [record.doc_id for record in batch]
        with store.connection:
            store.connection.executemany(
                "UPDATE record_checkpoints SET vector_status='inflight',updated_at=? "
                "WHERE (archive_key,ordinal) IN (SELECT archive_key,ordinal FROM run_records WHERE run_id=? AND doc_id=?)",
                [(_now(), run_id, doc_id) for doc_id in doc_ids],
            )
        counts["attempted"] += len(doc_ids)
        vectors, failures = _run_embedding_command(rows, args, wi)
        for doc_id, vector in vectors.items():
            store.set_vector(run_id, doc_id, vector)
            counts["completed"] += 1
        for doc_id, reason in failures.items():
            store.set_vector_status(run_id, doc_id, "failed", reason)
            counts["failed"] += 1
    return counts


def _pending_batches(store: CheckpointStore, run_id: str, args: Any, wi: Any, vector_dim: int) -> Iterator[list[Any]]:
    records = (
        wi.ImportRecord(**record)
        for _archive_key, _ordinal, record in store.pending_import_rows(run_id)
    )
    yield from wi.record_batches(records, args.batch_size, args.batch_max_bytes, vector_dim)


def _sql_for_batch(
    batch: list[Any], args: Any, lance_mode: str, wi: Any,
    vector_map: CheckpointVectors | None, batch_no: int,
) -> str:
    chunks = list(wi.iter_sql_chunks(
        batch, args.lance_uri, args.vector_dim, args.overwrite_lance,
        bool(args.lance_uri and not args.no_hash_vectors and vector_map is None),
        vector_map, max(1, len(batch)), lance_mode, args.bm25_mode, len(batch),
        args.batch_max_bytes, 1, args.skip_schema,
        requires_long_term_storage=any(wi.validate_bm25_terms(record)[0] for record in batch),
    ))
    script = "\\set ON_ERROR_STOP on\n" + "".join(chunks)
    return script.replace("batch 1/1:", f"batch {batch_no}/checkpointed:", 1)


def _fingerprint(store: CheckpointStore, run_id: str, config_sha256: str) -> str:
    digest = hashlib.sha256()
    digest.update(config_sha256.encode())
    for archive_key, source_sha256, record_fingerprint in store.connection.execute(
        "SELECT a.archive_key,a.source_sha256,a.record_fingerprint_sha256 "
        "FROM run_archives ra JOIN archive_checkpoints a USING(archive_key) "
        "WHERE ra.run_id=? ORDER BY ra.source_order",
        (run_id,),
    ):
        digest.update(f"{archive_key}\t{source_sha256}\t{record_fingerprint or 'incomplete'}\n".encode())
    return digest.hexdigest()


def run(args: Any, lance_mode: str, wi: Any) -> int:
    if not args.warc:
        raise ValueError("at least one WARC path is required")
    paths = [path.resolve() for path in args.warc]
    if len(paths) != len(set(paths)):
        raise ValueError("the same WARC path was supplied more than once")
    identities = [_file_identity(path) for path in paths]
    source_paths = {Path(item["path"]) for item in identities}
    for artifact in (args.checkpoint_db, args.output, args.summary_json, args.records_jsonl):
        if artifact is not None and artifact.resolve() in source_paths:
            raise ValueError("checkpoint/output artifacts must not overwrite a WARC input")
    if args.checkpoint_db.resolve() in source_paths:
        raise ValueError("checkpoint database must not overwrite a WARC input")
    importer_source_path = Path(wi.__file__).resolve()
    charset_policy = wi.CharsetPolicy.from_options(args.charset_override, args.charset_record_override)
    pdf_extractor = wi.PdfTextExtractor.from_command(
        args.pdftotext, args.pdf_timeout_seconds, args.max_record_bytes
    )
    docx_extractor = wi.DocxTextExtractor(max_output_bytes=args.max_record_bytes) if args.extract_docx else None
    retention = wi.ExtractionRetention(args.document_error_policy)
    settings = _settings(args, lance_mode, importer_source_path, pdf_extractor, docx_extractor)
    if args.limit is not None:
        settings["ordered_inputs_sha256"] = _json_hash([item["path"] for item in identities])
    config_sha256 = _json_hash(settings)
    vector_cells_bytes = args.batch_size * args.vector_dim * 32
    estimated_memory_bytes = (
        3 * args.max_record_bytes + 2 * args.batch_max_bytes + 2 * vector_cells_bytes + SQLITE_CACHE_KIB * 1024
    )
    if estimated_memory_bytes > args.checkpoint_max_memory_bytes:
        raise ValueError(
            "checkpoint-mode estimated working set exceeds --checkpoint-max-memory-bytes; "
            "reduce record, batch, or vector limits"
        )
    target_sha256 = _target_fingerprint(args.psql) if args.execute else None

    with CheckpointStore(args.checkpoint_db, args.checkpoint_max_bytes, source_paths) as store:
        run_id = store.start_run(config_sha256, target_sha256)
        archive_keys = [
            store.add_archive(run_id, config_sha256, identity, order)
            for order, identity in enumerate(identities)
        ]
        reused_records = 0
        parse_errors = 0
        limited = args.limit == 0
        vector_required = bool(args.lance_uri and (
            args.embedding_command or args.embedding_jsonl or not args.no_hash_vectors
        ))

        try:
            selected_before = 0
            for path, identity, archive_key in zip(paths, identities, archive_keys):
                if limited:
                    store.advance_archive(archive_key, 0, 0, "limited")
                    store.finish_archive(archive_key)
                    continue
                ordinal = 0
                try:
                    for raw in wi.iter_warc_records(path, args.max_record_bytes):
                        outcome, reused = _stage_record(
                            store=store, run_id=run_id, config_sha256=config_sha256,
                            archive_key=archive_key, ordinal=ordinal, raw=raw, args=args,
                            charset_policy=charset_policy, pdf_extractor=pdf_extractor,
                            docx_extractor=docx_extractor, retention=retention,
                            vector_required=vector_required, wi=wi,
                        )
                        reused_records += int(reused)
                        selected_before += int(outcome == "ready")
                        ordinal += 1
                        store.advance_archive(archive_key, ordinal, ordinal, "scanning")
                        if args.limit is not None and selected_before >= args.limit:
                            limited = True
                            store.advance_archive(archive_key, ordinal, ordinal, "limited")
                            store.finish_archive(archive_key)
                            break
                    else:
                        store.advance_archive(archive_key, ordinal, ordinal, "complete")
                        store.finish_archive(archive_key)
                except Exception as exc:
                    parse_errors += 1
                    store.fail_archive(archive_key, "partial", type(exc).__name__.lower())
                current = path.stat()
                if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != identity["_stat_signature"]:
                    parse_errors += 1
                    store.fail_archive(archive_key, "partial", "input_changed_during_import")
            charset_policy.validate_record_overrides()

            if args.records_jsonl:
                with args.records_jsonl.open("w", encoding="utf-8") as output:
                    for record in store.iter_records(run_id):
                        record = wi.ImportRecord(**record)
                        output.write(json.dumps(wi.record_to_json(record), ensure_ascii=False, sort_keys=True) + "\n")

            embedding = {"attempted": 0, "completed": 0, "failed": 0}
            if args.embedding_jsonl:
                embedding = _load_embedding_file(store, run_id, args, wi)
            elif args.embedding_command:
                embedding = _embed_pending(store, run_id, args, wi)

            vector_map = None
            if args.lance_uri and (args.embedding_command or args.embedding_jsonl or not args.no_hash_vectors):
                vector_map = CheckpointVectors(
                    store, run_id, wi, args.vector_dim,
                    hash_vectors=not (args.embedding_command or args.embedding_jsonl),
                )

            sql_path = args.output
            output = sql_path.open("w", encoding="utf-8") if sql_path else None
            batch_count = 0
            sql_bytes = 0
            sql_errors = 0
            try:
                for batch_count, batch in enumerate(
                    _pending_batches(store, run_id, args, wi, args.vector_dim if vector_map else 0), start=1
                ):
                    sql = _sql_for_batch(batch, args, lance_mode, wi, vector_map, batch_count)
                    if output:
                        output.write(sql)
                        output.flush()
                    elif not args.execute:
                        print(sql, end="")
                    sql_bytes += len(sql.encode())
                    if args.execute:
                        doc_ids = [record.doc_id for record in batch]
                        store.set_pg_status(run_id, doc_ids, "inflight")
                        try:
                            wi.execute_sql(sql, args.psql)
                        except SystemExit:
                            store.set_pg_status(run_id, doc_ids, "failed", "postgres_command_failed")
                            sql_errors += 1
                            break
                        store.set_pg_status(run_id, doc_ids, "complete")
            finally:
                if output:
                    output.close()

            counts = store.summary_counts(run_id)
            archive_rows = store.connection.execute(
                "SELECT source_sha256,source_size_bytes,status,next_ordinal,raw_records,record_fingerprint_sha256,last_error_kind "
                "FROM run_archives ra JOIN archive_checkpoints a USING(archive_key) "
                "WHERE ra.run_id=? ORDER BY ra.source_order", (run_id,)
            ).fetchall()
            raw_total = sum(row[4] for row in archive_rows)
            state_counts = store.state_counts(run_id)
            all_scanned = all(row[2] in {"complete", "limited"} for row in archive_rows)
            all_accounted = counts["imported"] + counts["failed"] + counts["skipped"] == raw_total
            pending = state_counts["pending_database"] + state_counts["pending_vectors"]
            if args.execute:
                status = "complete" if all_scanned and all_accounted and not parse_errors and not counts["failed"] and not pending and not sql_errors else "partial"
            else:
                status = "prepared" if all_scanned and all_accounted and not parse_errors and not counts["failed"] and not state_counts["pending_vectors"] else "partial"
            accounting = {
                "status": "accounted" if all_scanned and all_accounted else "partial",
                "raw_records": raw_total,
                "imported": counts["imported"],
                "metadata_only": counts["metadata_only"],
                "policy_skipped": counts["skipped"],
                "failed": counts["failed"] + parse_errors,
                "balanced": bool(all_scanned and all_accounted and not parse_errors),
            }
            summary = {
                "schema_version": 1,
                "run_id": run_id,
                "status": status,
                "config_sha256": config_sha256,
                "target_sha256": target_sha256,
                "source_sha256": [row[0] for row in archive_rows],
                "raw_input_bytes": sum(row[1] for row in archive_rows),
                "archive_statuses": [row[2] for row in archive_rows],
                "checkpoint_next_ordinals": [row[3] for row in archive_rows],
                "record_fingerprint_sha256": _fingerprint(store, run_id, config_sha256),
                "accounting": accounting,
                "reused_records": reused_records,
                "database": state_counts,
                "embedding": embedding,
                "sql_batches": batch_count,
                "sql_bytes": sql_bytes,
                "checkpoint_bytes": store.path.stat().st_size,
                "checkpoint_max_bytes": (args.checkpoint_max_bytes // SQLITE_PAGE_BYTES) * SQLITE_PAGE_BYTES,
                "estimated_working_memory_bytes": estimated_memory_bytes,
                "checkpoint_max_memory_bytes": args.checkpoint_max_memory_bytes,
                "sqlite_cache_kib": SQLITE_CACHE_KIB,
                "max_record_bytes": args.max_record_bytes,
                "batch_max_bytes": args.batch_max_bytes,
                "batch_size": args.batch_size,
                "lance_mode": lance_mode if args.lance_uri else "none",
                "lance_consistency": "eventual_after_commit" if args.lance_uri and lance_mode == "outbox" else ("external_idempotent_upsert" if args.lance_uri and vector_map else "none"),
            }
            store.finish_run(run_id, status, summary)
            if args.summary_json:
                args.summary_json.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            if args.summary:
                print(
                    " ".join([
                        f"run_id={run_id}", f"status={status}",
                        f"raw_records={raw_total}", f"imported={counts['imported']}",
                        f"failed={accounting['failed']}", f"skipped={counts['skipped']}",
                        f"reused_records={reused_records}", f"sql_batches={batch_count}",
                        f"fingerprint={summary['record_fingerprint_sha256']}",
                    ]), file=__import__("sys").stderr,
                )
            return 0 if status in {"complete", "prepared"} else 2
        except BaseException as exc:
            store.finish_run(run_id, "partial", {
                "schema_version": 1, "run_id": run_id, "status": "partial",
                "failure_kind": type(exc).__name__.lower(),
                "config_sha256": config_sha256,
            })
            raise
