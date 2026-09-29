#!/usr/bin/env python3
"""Import WARC/WARC.GZ records into pgwarc_lance via generated SQL."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import gzip
import hashlib
import html.parser
import io
import json
import math
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator


HEADER_ENCODING = "iso-8859-1"
DEFAULT_PSQL = (
    "psql -h localhost -p 55432 -U pgwarc_lance "
    "-d pgwarc_lance_test -v ON_ERROR_STOP=1"
)
LANCE_MODES = ("create", "overwrite", "append")
BM25_MODES = ("function", "bulk", "copy")
APPEND_DUPLICATE_WARNING = (
    "lance append mode does not deduplicate by doc_id; repeated imports can "
    "create duplicate vector rows"
)


@dataclasses.dataclass
class WarcRecord:
    version: str
    headers: dict[str, str]
    payload: bytes
    source_file: str


@dataclasses.dataclass
class ImportRecord:
    doc_id: int
    target_uri: str | None
    warc_date: str | None
    content_type: str | None
    http_status: int | None
    payload_digest: str
    text: str
    source_file: str


class HtmlTextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return normalize_text(" ".join(self._parts))


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def ascii_lower_char(char: str) -> str:
    if "A" <= char <= "Z":
        return chr(ord(char) + 32)
    return char


def is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return (
        0xAC00 <= codepoint <= 0xD7A3
        or 0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def emit_cjk_tokens(run: str) -> list[str]:
    chars = list(run)
    if not chars:
        return []
    if len(chars) == 1:
        return [chars[0]]
    return ["".join(chars[index : index + 2]) for index in range(len(chars) - 1)]


def tokenize_bm25(text: str) -> list[str]:
    chars = [ascii_lower_char(char) for char in text]
    tokens: list[str] = []
    index = 0
    while index < len(chars):
        char = chars[index]
        if is_cjk(char):
            start = index
            while index < len(chars) and is_cjk(chars[index]):
                index += 1
            tokens.extend(emit_cjk_tokens("".join(chars[start:index])))
        elif char.isascii() and char.isalnum():
            start = index
            while index < len(chars) and chars[index].isascii() and chars[index].isalnum():
                index += 1
            word = "".join(chars[start:index])
            if len(word) >= 2:
                tokens.append(word)
        else:
            index += 1
    return tokens


def bm25_term_frequencies(text: str) -> dict[str, int]:
    frequencies: dict[str, int] = {}
    for token in tokenize_bm25(text):
        frequencies[token] = frequencies.get(token, 0) + 1
    return frequencies


@contextlib.contextmanager
def open_warc(path: Path) -> Iterator[BinaryIO]:
    with path.open("rb") as raw:
        if path.suffix.lower() == ".gz":
            with gzip.GzipFile(fileobj=raw) as stream:
                yield stream
        else:
            yield raw


def read_header_block(stream: BinaryIO) -> dict[str, str]:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if line in {b"", b"\n", b"\r\n"}:
            return headers
        decoded = line.decode(HEADER_ENCODING).strip()
        if ":" not in decoded:
            continue
        name, value = decoded.split(":", 1)
        name = name.strip().lower()
        if name == "content-length" and name in headers:
            raise ValueError("duplicate WARC Content-Length header")
        headers[name] = value.strip()


def iter_warc_records(path: Path) -> Iterable[WarcRecord]:
    with open_warc(path) as stream:
        while True:
            line = stream.readline()
            if line == b"":
                return
            stripped = line.strip()
            if not stripped:
                continue
            if not stripped.startswith(b"WARC/"):
                continue

            version = stripped.decode(HEADER_ENCODING)
            headers = read_header_block(stream)
            raw_length = headers.get("content-length")
            if raw_length is None:
                raise ValueError(f"missing WARC Content-Length in {path}: {headers!r}")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError(f"invalid WARC Content-Length in {path}: {headers!r}") from exc
            if length < 0:
                raise ValueError(
                    f"invalid WARC Content-Length in {path}: must be non-negative"
                )
            payload = stream.read(length)
            if len(payload) != length:
                raise ValueError(
                    f"truncated WARC payload in {path}: expected {length}, got {len(payload)}"
                )
            yield WarcRecord(version, headers, payload, str(path))


def parse_http_headers(block: bytes) -> tuple[str, dict[str, str], bytes]:
    marker = b"\r\n\r\n"
    split_at = block.find(marker)
    marker_len = len(marker)
    if split_at < 0:
        marker = b"\n\n"
        split_at = block.find(marker)
        marker_len = len(marker)
    if split_at < 0:
        return "", {}, block

    head = block[:split_at].decode(HEADER_ENCODING, errors="replace")
    body = block[split_at + marker_len :]
    lines = head.replace("\r\n", "\n").split("\n")
    status_line = lines[0] if lines else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.lower()] = value.strip()
    return status_line, headers, body


def http_status(status_line: str) -> int | None:
    match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d{3})", status_line)
    if not match:
        return None
    return int(match.group(1))


def dechunk(body: bytes) -> bytes:
    stream = io.BytesIO(body)
    out = bytearray()
    while True:
        line = stream.readline()
        if line == b"":
            raise ValueError(
                "truncated chunked body: missing terminating zero-size chunk"
            )
        if line.endswith(b"\r\n"):
            size_line = line[:-2]
        elif line.endswith(b"\n"):
            size_line = line[:-1]
        else:
            raise ValueError("invalid HTTP chunk size line terminator")

        size_token = size_line.split(b";", 1)[0].strip()
        if not size_token or re.fullmatch(rb"[0-9A-Fa-f]+", size_token) is None:
            raise ValueError(f"invalid HTTP chunk size: {size_token!r}")
        size = int(size_token, 16)
        if size == 0:
            while True:
                trailer = stream.readline()
                if trailer == b"":
                    raise ValueError("truncated chunked body: missing trailer terminator")
                if trailer in {b"\r\n", b"\n"}:
                    return bytes(out)
                if (
                    not trailer.endswith((b"\r\n", b"\n"))
                    or b":" not in trailer
                ):
                    raise ValueError("invalid HTTP chunk trailer")

        chunk = stream.read(size)
        if len(chunk) != size:
            raise ValueError(
                f"truncated HTTP chunk data: expected {size}, got {len(chunk)}"
            )
        out.extend(chunk)
        terminator = stream.readline()
        if terminator not in {b"\r\n", b"\n"}:
            raise ValueError("invalid HTTP chunk data terminator")
    return bytes(out)


def charset_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return "utf-8"
    match = re.search(r"charset\s*=\s*([^;\s]+)", content_type, re.IGNORECASE)
    if not match:
        return "utf-8"
    return match.group(1).strip("\"'")


def decode_body(body: bytes, headers: dict[str, str], content_type: str | None) -> str:
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = dechunk(body)
    if headers.get("content-encoding", "").lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError) as exc:
            raise ValueError("invalid gzip HTTP payload") from exc

    charset = charset_from_content_type(content_type)
    text = body.decode(charset, errors="replace")
    if content_type and "html" in content_type.lower():
        parser = HtmlTextExtractor()
        parser.feed(text)
        return parser.text()
    return normalize_text(text)


def import_record_from_warc(record: WarcRecord) -> ImportRecord | None:
    record_type = record.headers.get("warc-type", "").lower()
    if record_type not in {"response", "resource"}:
        return None

    status_line, http_headers, body = parse_http_headers(record.payload)
    http_ct = http_headers.get("content-type")
    content_type = http_ct or record.headers.get("content-type")
    if status_line:
        text = decode_body(body, http_headers, content_type)
    else:
        text = decode_body(record.payload, {}, content_type)
    if not text:
        return None

    target_uri = record.headers.get("warc-target-uri")
    warc_date = record.headers.get("warc-date")
    payload_digest = (
        record.headers.get("warc-payload-digest")
        or record.headers.get("warc-block-digest")
        or "sha1:" + hashlib.sha1(record.payload).hexdigest()
    )
    return ImportRecord(
        doc_id=stable_doc_id(target_uri, warc_date, payload_digest),
        target_uri=target_uri,
        warc_date=warc_date,
        content_type=content_type,
        http_status=http_status(status_line),
        payload_digest=payload_digest,
        text=text,
        source_file=record.source_file,
    )


def stable_doc_id(target_uri: str | None, warc_date: str | None, digest: str) -> int:
    raw = "\n".join([target_uri or "", warc_date or "", digest]).encode()
    value = int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big")
    value &= (1 << 63) - 1
    return value or 1


def hash_embedding(text: str, dim: int) -> list[float]:
    values: list[float] = []
    seed = text.encode("utf-8")
    counter = 0
    while len(values) < dim:
        digest = hashlib.blake2b(seed + counter.to_bytes(4, "big"), digest_size=32).digest()
        counter += 1
        for offset in range(0, len(digest), 4):
            if len(values) == dim:
                break
            integer = int.from_bytes(digest[offset : offset + 4], "big")
            values.append((integer / 0xFFFFFFFF) * 2.0 - 1.0)

    norm = sum(v * v for v in values) ** 0.5
    if norm:
        values = [v / norm for v in values]
    return values


def record_to_json(record: ImportRecord) -> dict[str, Any]:
    return {
        "doc_id": record.doc_id,
        "target_uri": record.target_uri,
        "warc_date": record.warc_date,
        "content_type": record.content_type,
        "http_status": record.http_status,
        "payload_digest": record.payload_digest,
        "text_len": len(record.text),
        "source_file": record.source_file,
        "text": record.text,
    }


def records_jsonl(records: Iterable[ImportRecord]) -> str:
    return "".join(
        json.dumps(record_to_json(record), ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )


def write_records_jsonl(records: Iterable[ImportRecord], path: Path) -> None:
    path.write_text(records_jsonl(records), encoding="utf-8")


def parse_vector(value: Any, doc_id: int, vector_dim: int, source: str) -> list[float]:
    if not isinstance(value, list):
        raise ValueError(f"{source}: vector for doc_id={doc_id} must be a JSON array")
    if len(value) != vector_dim:
        raise ValueError(
            f"{source}: vector for doc_id={doc_id} has dim {len(value)}, expected {vector_dim}"
        )

    vector: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{source}: vector for doc_id={doc_id} contains non-numeric value")
        try:
            numeric = float(item)
        except OverflowError as exc:
            raise ValueError(
                f"{source}: vector for doc_id={doc_id} contains non-finite or out-of-range value"
            ) from exc
        if not math.isfinite(numeric):
            raise ValueError(
                f"{source}: vector for doc_id={doc_id} contains non-finite value"
            )
        vector.append(numeric)
    return vector


def parse_embedding_jsonl(
    content: str, required_doc_ids: set[int], vector_dim: int, source: str
) -> dict[int, list[float]]:
    vectors: dict[int, list[float]] = {}
    for line_no, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}:{line_no}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{source}:{line_no}: expected JSON object")

        raw_doc_id = row.get("doc_id", row.get("id"))
        if raw_doc_id is None:
            raise ValueError(f"{source}:{line_no}: missing doc_id")
        doc_id = int(raw_doc_id)
        if doc_id not in required_doc_ids:
            continue
        if doc_id in vectors:
            raise ValueError(f"{source}:{line_no}: duplicate vector for doc_id={doc_id}")
        vectors[doc_id] = parse_vector(row.get("vector"), doc_id, vector_dim, source)

    missing = sorted(required_doc_ids.difference(vectors))
    if missing:
        sample = ", ".join(str(doc_id) for doc_id in missing[:5])
        raise ValueError(f"{source}: missing vectors for doc_id(s): {sample}")
    return vectors


def load_embedding_jsonl(
    path: Path, records: Iterable[ImportRecord], vector_dim: int
) -> dict[int, list[float]]:
    record_list = list(records)
    required_doc_ids = {record.doc_id for record in record_list}
    content = path.read_text(encoding="utf-8")
    return parse_embedding_jsonl(content, required_doc_ids, vector_dim, str(path))


def run_embedding_command(
    records: Iterable[ImportRecord], command: str, vector_dim: int
) -> dict[int, list[float]]:
    record_list = list(records)
    required_doc_ids = {record.doc_id for record in record_list}
    payload = records_jsonl(record_list)
    result = subprocess.run(
        command,
        shell=True,
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        if result.stderr:
            sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return parse_embedding_jsonl(result.stdout, required_doc_ids, vector_dim, command)


def sql_string(value: str | None) -> str:
    if value is None:
        return "NULL"
    clean = value.replace("\x00", "")
    return "'" + clean.replace("'", "''") + "'"


def sql_int(value: int | None) -> str:
    return "NULL" if value is None else str(value)


def sql_timestamp(value: str | None) -> str:
    return "NULL" if value is None else f"{sql_string(value)}::timestamptz"


def sql_vector(values: list[float]) -> str:
    return "ARRAY[" + ",".join(f"{value:.8f}" for value in values) + "]::float4[]"


def sql_bigint_array(values: list[int]) -> str:
    return "ARRAY[" + ",".join(str(value) for value in values) + "]::bigint[]"


def sql_text_array(values: list[str]) -> str:
    return "ARRAY[" + ",".join(sql_string(value) for value in values) + "]::text[]"


def copy_value(value: str | int | None) -> str:
    if value is None:
        return r"\N"
    clean = str(value).replace("\x00", "")
    return (
        clean.replace("\\", r"\\")
        .replace("\t", r"\t")
        .replace("\r", r"\r")
        .replace("\n", r"\n")
    )


def copy_row(values: Iterable[str | int | None]) -> str:
    return "\t".join(copy_value(value) for value in values)


def bm25_table_sql() -> str:
    return """
CREATE TABLE IF NOT EXISTS pgwarc_lance.bm25_doc (
    doc_id     bigint PRIMARY KEY,
    content    text,
    doc_len    integer NOT NULL DEFAULT 0,
    indexed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pgwarc_lance.bm25_term (
    term    text NOT NULL,
    doc_id  bigint NOT NULL REFERENCES pgwarc_lance.bm25_doc(doc_id) ON DELETE CASCADE,
    tf      integer NOT NULL,
    PRIMARY KEY (term, doc_id)
);

CREATE INDEX IF NOT EXISTS bm25_term_term_idx ON pgwarc_lance.bm25_term (term);
""".strip()


def emit_bm25_bulk_sql(records: list[ImportRecord]) -> list[str]:
    if not records:
        return []

    lines = [
        f"DELETE FROM pgwarc_lance.bm25_term WHERE doc_id = ANY({sql_bigint_array([record.doc_id for record in records])});"
    ]
    doc_values: list[str] = []
    term_values: list[str] = []
    for record in records:
        frequencies = bm25_term_frequencies(record.text)
        doc_len = sum(frequencies.values())
        doc_values.append(
            f"({record.doc_id}, {sql_string(record.text)}, {doc_len})"
        )
        for term in sorted(frequencies):
            term_values.append(
                f"({sql_string(term)}, {record.doc_id}, {frequencies[term]})"
            )

    lines.append(
        "INSERT INTO pgwarc_lance.bm25_doc (doc_id, content, doc_len) VALUES\n"
        + ",\n".join(doc_values)
        + "\nON CONFLICT (doc_id) DO UPDATE SET "
        "content = EXCLUDED.content, "
        "doc_len = EXCLUDED.doc_len, "
        "indexed_at = now();"
    )
    if term_values:
        lines.append(
            "INSERT INTO pgwarc_lance.bm25_term (term, doc_id, tf) VALUES\n"
            + ",\n".join(term_values)
            + "\nON CONFLICT (term, doc_id) DO UPDATE SET tf = EXCLUDED.tf;"
        )
    return lines


def emit_bm25_copy_sql(records: list[ImportRecord]) -> list[str]:
    if not records:
        return []

    doc_ids = [record.doc_id for record in records]
    lines = [
        f"DELETE FROM pgwarc_lance.bm25_doc WHERE doc_id = ANY({sql_bigint_array(doc_ids)});",
        "COPY pgwarc_lance.bm25_doc (doc_id, content, doc_len) FROM stdin;",
    ]
    term_rows: list[str] = []
    for record in records:
        frequencies = bm25_term_frequencies(record.text)
        doc_len = sum(frequencies.values())
        lines.append(copy_row([record.doc_id, record.text, doc_len]))
        for term in sorted(frequencies):
            term_rows.append(copy_row([term, record.doc_id, frequencies[term]]))
    lines.append(r"\.")

    if term_rows:
        lines.append("COPY pgwarc_lance.bm25_term (term, doc_id, tf) FROM stdin;")
        lines.extend(term_rows)
        lines.append(r"\.")
    return lines


def emit_sql(
    records: Iterable[ImportRecord],
    lance_uri: str | None,
    vector_dim: int,
    overwrite_lance: bool,
    include_hash_vectors: bool,
    vectors_by_doc_id: dict[int, list[float]] | None = None,
    batch_size: int = 1000,
    lance_mode: str | None = None,
    bm25_mode: str = "function",
) -> str:
    record_list = list(records)
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if lance_mode is None:
        lance_mode = "overwrite" if overwrite_lance else "create"
    if lance_mode not in LANCE_MODES:
        raise ValueError(f"lance_mode must be one of: {', '.join(LANCE_MODES)}")
    if bm25_mode not in BM25_MODES:
        raise ValueError(f"bm25_mode must be one of: {', '.join(BM25_MODES)}")

    lines: list[str] = [
        "CREATE SCHEMA IF NOT EXISTS pgwarc_lance;",
        bm25_table_sql(),
        """
CREATE TABLE IF NOT EXISTS pgwarc_lance.warc_record (
    doc_id         bigint PRIMARY KEY,
    target_uri     text,
    warc_date      timestamptz,
    content_type   text,
    http_status    integer,
    payload_digest text,
    text_len       integer NOT NULL DEFAULT 0,
    source_file    text,
    imported_at    timestamptz NOT NULL DEFAULT now()
);
""".strip(),
        "CREATE INDEX IF NOT EXISTS warc_record_target_uri_idx "
        "ON pgwarc_lance.warc_record (target_uri);",
        "CREATE INDEX IF NOT EXISTS warc_record_warc_date_idx "
        "ON pgwarc_lance.warc_record (warc_date);",
    ]
    has_lance_vectors = bool(lance_uri) and (include_hash_vectors or vectors_by_doc_id is not None)
    if lance_uri and has_lance_vectors and lance_mode != "append":
        overwrite = "true" if lance_mode == "overwrite" else "false"
        lines.append(
            f"SELECT lance_create_table({sql_string(lance_uri)}, {vector_dim}, {overwrite});"
        )
    elif lance_uri and has_lance_vectors:
        lines.append(f"-- WARNING: {APPEND_DUPLICATE_WARNING}.")

    total_batches = (len(record_list) + batch_size - 1) // batch_size
    for batch_no, start in enumerate(range(0, len(record_list), batch_size), start=1):
        end = min(len(record_list), start + batch_size)
        lines.append(
            f"-- pgwarc_lance import batch {batch_no}/{total_batches}: "
            f"records {start + 1}-{end}"
        )
        lines.append("BEGIN;")
        lance_ids: list[int] = []
        lance_flat_vectors: list[float] = []
        lance_labels: list[str] = []
        batch_records = record_list[start:end]
        for record in batch_records:
            lines.append(
                """
INSERT INTO pgwarc_lance.warc_record (
    doc_id, target_uri, warc_date, content_type, http_status,
    payload_digest, text_len, source_file, imported_at
) VALUES (
    {doc_id}, {target_uri}, {warc_date}, {content_type}, {http_status},
    {payload_digest}, {text_len}, {source_file}, now()
) ON CONFLICT (doc_id) DO UPDATE SET
    target_uri = EXCLUDED.target_uri,
    warc_date = EXCLUDED.warc_date,
    content_type = EXCLUDED.content_type,
    http_status = EXCLUDED.http_status,
    payload_digest = EXCLUDED.payload_digest,
    text_len = EXCLUDED.text_len,
    source_file = EXCLUDED.source_file,
    imported_at = now();
""".strip().format(
                    doc_id=record.doc_id,
                    target_uri=sql_string(record.target_uri),
                    warc_date=sql_timestamp(record.warc_date),
                    content_type=sql_string(record.content_type),
                    http_status=sql_int(record.http_status),
                    payload_digest=sql_string(record.payload_digest),
                    text_len=len(record.text),
                    source_file=sql_string(record.source_file),
                )
            )
            if bm25_mode == "function":
                lines.append(
                    f"SELECT bm25_index_document({record.doc_id}, {sql_string(record.text)});"
                )

            vector = None
            if lance_uri and vectors_by_doc_id is not None:
                vector = vectors_by_doc_id.get(record.doc_id)
            elif lance_uri and include_hash_vectors:
                vector = hash_embedding(record.text, vector_dim)

            if lance_uri and vector is not None:
                lance_ids.append(record.doc_id)
                lance_flat_vectors.extend(vector)
                lance_labels.append(record.target_uri or str(record.doc_id))
        if bm25_mode == "bulk":
            lines.extend(emit_bm25_bulk_sql(batch_records))
        elif bm25_mode == "copy":
            lines.extend(emit_bm25_copy_sql(batch_records))
        if lance_uri and lance_ids:
            lines.append(
                "SELECT lance_insert_many("
                f"{sql_string(lance_uri)}, {sql_bigint_array(lance_ids)}, "
                f"{sql_vector(lance_flat_vectors)}, {vector_dim}, {sql_text_array(lance_labels)}"
                ");"
            )
        lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def vector_summary(
    lance_uri: str | None,
    no_hash_vectors: bool,
    vectors_by_doc_id: dict[int, list[float]] | None,
) -> int | str:
    if vectors_by_doc_id is not None:
        return len(vectors_by_doc_id)
    if lance_uri and not no_hash_vectors:
        return "hash"
    return 0


def summary_data(
    records: list[ImportRecord],
    sql: str,
    vectors_by_doc_id: dict[int, list[float]] | None,
    lance_uri: str | None,
    no_hash_vectors: bool,
    lance_mode: str,
    bm25_mode: str,
    execute: bool,
    psql: str,
) -> dict[str, Any]:
    vectors = vector_summary(lance_uri, no_hash_vectors, vectors_by_doc_id)
    append_duplicates = bool(lance_uri and lance_mode == "append" and vectors != 0)
    return {
        "records": len(records),
        "sql_bytes": len(sql.encode("utf-8")),
        "vectors": vectors,
        "lance_mode": lance_mode if lance_uri else "none",
        "lance_append_duplicates": "possible" if append_duplicates else "none",
        "bm25_mode": bm25_mode,
        "execute": execute,
        "psql": psql,
    }


def format_summary(data: dict[str, Any]) -> str:
    parts = [
        f"records={data['records']}",
        f"sql_bytes={data['sql_bytes']}",
        f"vectors={data['vectors']}",
        f"lance_mode={data['lance_mode']}",
        f"lance_append_duplicates={data['lance_append_duplicates']}",
        f"bm25_mode={data['bm25_mode']}",
        f"execute={data['execute']}",
        f"psql={shlex.quote(str(data['psql']))}",
    ]
    return " ".join(parts)


def load_import_records(paths: list[Path], limit: int | None, min_text_chars: int) -> list[ImportRecord]:
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    if min_text_chars < 0:
        raise ValueError("min_text_chars must be non-negative")
    if limit == 0:
        return []

    records: list[ImportRecord] = []
    source_by_doc_id: dict[int, str] = {}
    for path in paths:
        for warc_record in iter_warc_records(path):
            record = import_record_from_warc(warc_record)
            if record is None or len(record.text) < min_text_chars:
                continue
            previous_source = source_by_doc_id.get(record.doc_id)
            if previous_source is not None:
                raise ValueError(
                    f"duplicate imported doc_id={record.doc_id}: "
                    f"{previous_source} and {record.source_file}"
                )
            source_by_doc_id[record.doc_id] = record.source_file
            records.append(record)
            if limit is not None and len(records) >= limit:
                return records
    return records


def execute_sql(sql: str, psql_command: str) -> None:
    result = subprocess.run(psql_command, shell=True, input=sql, text=True, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("warc", nargs="+", type=Path, help="WARC or WARC.GZ file")
    parser.add_argument("--output", "-o", type=Path, help="write generated SQL to this file")
    parser.add_argument("--execute", action="store_true", help="execute generated SQL with psql")
    parser.add_argument("--psql", default=os.environ.get("PGWARC_PSQL", DEFAULT_PSQL))
    parser.add_argument("--lance-uri", help="Lance dataset URI to create/append vectors into")
    parser.add_argument("--vector-dim", type=int, default=3)
    parser.add_argument(
        "--lance-mode",
        choices=LANCE_MODES,
        default="create",
        help=(
            "Lance dataset handling before vector inserts: create fails if the dataset "
            "already exists, overwrite recreates it, append assumes it already exists"
        ),
    )
    parser.add_argument(
        "--overwrite-lance",
        action="store_true",
        help="deprecated alias for --lance-mode overwrite",
    )
    parser.add_argument(
        "--embedding-command",
        help=(
            "command that reads extracted record JSONL from stdin and writes "
            '{"doc_id":..., "vector":[...]} JSONL to stdout'
        ),
    )
    parser.add_argument(
        "--embedding-jsonl",
        type=Path,
        help='precomputed JSONL vectors with {"doc_id":..., "vector":[...]} rows',
    )
    parser.add_argument(
        "--records-jsonl",
        type=Path,
        help="write extracted records as JSONL for offline embedding",
    )
    parser.add_argument(
        "--no-hash-vectors",
        action="store_true",
        help="skip deterministic placeholder vectors; BM25 and metadata only",
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--bm25-mode",
        choices=BM25_MODES,
        default="function",
        help=(
            "BM25 indexing SQL path: function calls bm25_index_document per record; "
            "bulk inserts bm25_doc/bm25_term rows per batch; "
            "copy streams bm25_doc/bm25_term rows with psql COPY FROM stdin"
        ),
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-text-chars", type=int, default=1)
    parser.add_argument("--summary", action="store_true", help="print import summary to stderr")
    parser.add_argument("--summary-json", type=Path, help="write machine-readable summary JSON")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.vector_dim <= 0:
        raise SystemExit("--vector-dim must be positive")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if args.limit is not None and args.limit < 0:
        raise SystemExit("--limit must be non-negative")
    if args.min_text_chars < 0:
        raise SystemExit("--min-text-chars must be non-negative")
    if args.embedding_command and args.embedding_jsonl:
        raise SystemExit("--embedding-command and --embedding-jsonl are mutually exclusive")
    if (args.embedding_command or args.embedding_jsonl) and not args.lance_uri:
        raise SystemExit("--embedding-command/--embedding-jsonl require --lance-uri")
    if args.lance_mode != "create" and not args.lance_uri:
        raise SystemExit("--lance-mode requires --lance-uri")
    lance_mode = args.lance_mode
    if args.overwrite_lance:
        if args.lance_mode == "append":
            raise SystemExit("--overwrite-lance conflicts with --lance-mode append")
        lance_mode = "overwrite"

    records = load_import_records(args.warc, args.limit, args.min_text_chars)
    if args.records_jsonl:
        write_records_jsonl(records, args.records_jsonl)

    vectors_by_doc_id = None
    if args.embedding_command:
        vectors_by_doc_id = run_embedding_command(records, args.embedding_command, args.vector_dim)
    elif args.embedding_jsonl:
        vectors_by_doc_id = load_embedding_jsonl(args.embedding_jsonl, records, args.vector_dim)

    sql = emit_sql(
        records=records,
        lance_uri=args.lance_uri,
        vector_dim=args.vector_dim,
        overwrite_lance=args.overwrite_lance,
        include_hash_vectors=not args.no_hash_vectors and vectors_by_doc_id is None,
        vectors_by_doc_id=vectors_by_doc_id,
        batch_size=args.batch_size,
        lance_mode=lance_mode,
        bm25_mode=args.bm25_mode,
    )

    if args.output:
        args.output.write_text(sql, encoding="utf-8")
    elif not args.execute:
        sys.stdout.write(sql)

    if args.execute:
        execute_sql(sql, args.psql)

    summary = summary_data(
        records=records,
        sql=sql,
        vectors_by_doc_id=vectors_by_doc_id,
        lance_uri=args.lance_uri,
        no_hash_vectors=args.no_hash_vectors,
        lance_mode=lance_mode,
        bm25_mode=args.bm25_mode,
        execute=args.execute,
        psql=args.psql,
    )
    if args.summary_json:
        args.summary_json.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    if args.summary:
        print(format_summary(summary), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
