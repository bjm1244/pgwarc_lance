#!/usr/bin/env python3
"""Import WARC/WARC.GZ records into pgwarc_lance via generated SQL."""

from __future__ import annotations

import argparse
import contextlib
import codecs
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
import signal
import shutil
import sqlite3
import tempfile
import subprocess
import sys
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator


HEADER_ENCODING = "iso-8859-1"
MAX_HEADER_BYTES = 64 * 1024
DEFAULT_MAX_RECORD_BYTES = 64 * 1024 * 1024
# Conservative preflight for the term/doc_id B-tree on standard 8 KiB PG pages.
MAX_BM25_ASCII_TERM_BYTES = 2000
DEFAULT_PSQL = (
    "psql -h localhost -p 55432 -U pgwarc_lance "
    "-d pgwarc_lance_test -v ON_ERROR_STOP=1"
)
LANCE_MODES = ("create", "overwrite", "append", "upsert")
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
    total = 0
    while True:
        line = stream.readline(MAX_HEADER_BYTES + 1)
        total += len(line)
        if total > MAX_HEADER_BYTES:
            raise ValueError("WARC headers exceed the 64 KiB limit")
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


def iter_warc_records(path: Path, max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES) -> Iterable[WarcRecord]:
    if max_record_bytes <= 0:
        raise ValueError("max_record_bytes must be positive")
    with open_warc(path) as stream:
        while True:
            line = stream.readline(MAX_HEADER_BYTES + 1)
            if len(line) > MAX_HEADER_BYTES:
                raise ValueError("WARC record line exceeds the 64 KiB limit")
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
            if length > max_record_bytes:
                raise ValueError(f"WARC payload exceeds max_record_bytes={max_record_bytes}: {length}")
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


# HTTP labels missing from older Python codec registries. This is a small
# compatibility map, not a complete WHATWG browser decoding implementation.
HTTP_CHARSET_ALIASES = {"windows-874": "cp874", "windows-31j": "cp932"}


@dataclasses.dataclass
class CharsetPolicy:
    overrides: dict[str, str]
    used: dict[str, int] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_options(cls, options: list[str]) -> CharsetPolicy:
        overrides: dict[str, str] = {}
        for option in options:
            label, separator, codec = option.partition("=")
            label, codec = label.strip().lower(), codec.strip()
            if not separator or not label or not codec:
                raise ValueError("--charset-override requires LABEL=CODEC")
            try:
                b"codec validation".decode(codec)  # Empty bytes bypass codec checks in CPython.
                codec = codecs.lookup(codec).name
            except (LookupError, TypeError) as exc:
                raise ValueError(f"invalid override text codec: {codec!r}") from exc
            if label in overrides:
                raise ValueError(f"duplicate charset override label: {label!r}")
            overrides[label] = codec
        return cls(overrides)


def decode_body(body: bytes, headers: dict[str, str], content_type: str | None, max_decoded_bytes: int = DEFAULT_MAX_RECORD_BYTES, charset_policy: CharsetPolicy | None = None) -> str:
    if max_decoded_bytes <= 0:
        raise ValueError("max_decoded_bytes must be positive")
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = dechunk(body)
    if headers.get("content-encoding", "").lower() == "gzip":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                body = stream.read(max_decoded_bytes + 1)
        except (OSError, EOFError) as exc:
            raise ValueError("invalid gzip HTTP payload") from exc

    if len(body) > max_decoded_bytes:
        raise ValueError(f"decoded HTTP payload exceeds max_decoded_bytes={max_decoded_bytes}")
    label = charset_from_content_type(content_type).strip().lower()
    charset = HTTP_CHARSET_ALIASES.get(label, label)
    overridden = charset_policy is not None and label in charset_policy.overrides
    if overridden:
        charset = charset_policy.overrides[label]
    # An operator's explicit correction must decode strictly; do not silently
    # replace invalid bytes and present a guessed encoding as verified text.
    text = body.decode(charset, errors="strict" if overridden else "replace")
    if overridden:
        charset_policy.used[label] = charset_policy.used.get(label, 0) + 1
    if content_type and "html" in content_type.lower():
        parser = HtmlTextExtractor()
        parser.feed(text)
        return parser.text()
    return normalize_text(text)


def import_record_from_warc(record: WarcRecord, max_decoded_bytes: int = DEFAULT_MAX_RECORD_BYTES, charset_policy: CharsetPolicy | None = None) -> ImportRecord | None:
    record_type = record.headers.get("warc-type", "").lower()
    if record_type not in {"response", "resource"}:
        return None

    status_line, http_headers, body = parse_http_headers(record.payload)
    http_ct = http_headers.get("content-type")
    content_type = http_ct or record.headers.get("content-type")
    if status_line:
        text = decode_body(body, http_headers, content_type, max_decoded_bytes, charset_policy)
    else:
        text = decode_body(record.payload, {}, content_type, max_decoded_bytes, charset_policy)
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
        if not math.isfinite(numeric) or abs(numeric) > 3.4028234663852886e38:
            raise ValueError(
                f"{source}: vector for doc_id={doc_id} contains non-finite or out-of-range float4 value"
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
CREATE INDEX IF NOT EXISTS bm25_term_doc_id_idx ON pgwarc_lance.bm25_term (doc_id);
""".strip()


def emit_bm25_bulk_sql(records: list[ImportRecord]) -> list[str]:
    if not records:
        return []

    lines: list[str] = []
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
    # The document upsert acquires row locks before replacing postings, just
    # like the SQL extension function; concurrent reindexing cannot leave stale terms.
    lines.append(
        f"DELETE FROM pgwarc_lance.bm25_term WHERE doc_id = ANY({sql_bigint_array([record.doc_id for record in records])});"
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


def record_batches(records: Iterable[ImportRecord], batch_size: int,
                   max_bytes: int | None = None, vector_dim: int = 0) -> Iterator[list[ImportRecord]]:
    batch: list[ImportRecord] = []
    size = 0
    for record in records:
        # Conservative input weight: UTF-8 text/metadata and formatted vector.
        # A single oversized record is still allowed up to max_record_bytes.
        weight = sum(len(value.encode("utf-8")) for value in dataclasses.asdict(record).values() if isinstance(value, str)) + vector_dim * 32
        if batch and max_bytes is not None and size + weight > max_bytes:
            yield batch
            batch, size = [], 0
        batch.append(record)
        size += weight
        if len(batch) == batch_size:
            yield batch
            batch, size = [], 0
    if batch:
        yield batch


def iter_sql_chunks(
    records: Iterable[ImportRecord],
    lance_uri: str | None,
    vector_dim: int,
    overwrite_lance: bool,
    include_hash_vectors: bool,
    vectors_by_doc_id: dict[int, list[float]] | None = None,
    batch_size: int = 1000,
    lance_mode: str | None = None,
    bm25_mode: str = "function",
    total_records: int = 0,
    max_batch_bytes: int | None = None,
    total_batches: int | None = None,
    skip_schema: bool = False,
) -> Iterator[str]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if lance_mode is None:
        lance_mode = "overwrite" if overwrite_lance else "create"
    if lance_mode not in LANCE_MODES:
        raise ValueError(f"lance_mode must be one of: {', '.join(LANCE_MODES)}")
    if lance_mode == "upsert" and batch_size > 10_000:
        raise ValueError("upsert batch_size must not exceed 10000")
    if bm25_mode not in BM25_MODES:
        raise ValueError(f"bm25_mode must be one of: {', '.join(BM25_MODES)}")

    lines: list[str] = [
        "SET standard_conforming_strings = on;",
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
    if skip_schema:
        lines = ["SET standard_conforming_strings = on;"]
    has_lance_vectors = bool(lance_uri) and (include_hash_vectors or vectors_by_doc_id is not None)
    if lance_uri and has_lance_vectors and lance_mode in {"create", "overwrite"}:
        overwrite = "true" if lance_mode == "overwrite" else "false"
        lines.append(
            f"SELECT lance_create_table({sql_string(lance_uri)}, {vector_dim}, {overwrite});"
        )
    elif lance_uri and has_lance_vectors and lance_mode == "append":
        lines.append(f"-- WARNING: {APPEND_DUPLICATE_WARNING}.")

    yield "\n".join(lines) + "\n"
    if total_batches is None:
        total_batches = (total_records + batch_size - 1) // batch_size
    start = 0
    for batch_no, batch_records in enumerate(record_batches(records, batch_size, max_batch_bytes, vector_dim if has_lance_vectors else 0), start=1):
        lines = []
        end = start + len(batch_records)
        lines.append(
            f"-- pgwarc_lance import batch {batch_no}/{total_batches}: "
            f"records {start + 1}-{end}"
        )
        lines.append("BEGIN;")
        lance_ids: list[int] = []
        lance_flat_vectors: list[float] = []
        lance_labels: list[str] = []
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
                f"SELECT {'lance_upsert_many' if lance_mode == 'upsert' else 'lance_insert_many'}("
                f"{sql_string(lance_uri)}, {sql_bigint_array(lance_ids)}, "
                f"{sql_vector(lance_flat_vectors)}, {vector_dim}, {sql_text_array(lance_labels)}"
                ");"
            )
        lines.append("COMMIT;")
        yield "\n".join(lines) + "\n"
        start = end


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
    skip_schema: bool = False,
) -> str:
    record_list = list(records)
    return "".join(iter_sql_chunks(
        record_list, lance_uri, vector_dim, overwrite_lance, include_hash_vectors,
        vectors_by_doc_id, batch_size, lance_mode, bm25_mode, len(record_list),
        skip_schema=skip_schema,
    ))


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


def validate_bm25_terms(record: ImportRecord) -> None:
    # CJK tokens are at most two characters; the remaining tokenizer branch
    # produces ASCII alphanumeric runs. Reject rather than silently truncating
    # terms or dropping postings and changing ranking without an explicit policy.
    if re.search(r"[A-Za-z0-9]{" + str(MAX_BM25_ASCII_TERM_BYTES + 1) + r"}", record.text):
        raise ValueError(
            f"doc_id={record.doc_id}: BM25 ASCII term exceeds "
            f"{MAX_BM25_ASCII_TERM_BYTES} bytes; review extraction before import"
        )


def iter_import_records(paths: list[Path], limit: int | None, min_text_chars: int, max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES, charset_policy: CharsetPolicy | None = None) -> Iterator[ImportRecord]:
    if max_record_bytes <= 0:
        raise ValueError("max_record_bytes must be positive")
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    if min_text_chars < 0:
        raise ValueError("min_text_chars must be non-negative")
    if limit == 0:
        return

    count = 0
    for path in paths:
        for warc_record in iter_warc_records(path, max_record_bytes):
            record = import_record_from_warc(warc_record, max_record_bytes, charset_policy)
            if record is None or len(record.text) < min_text_chars:
                continue
            validate_bm25_terms(record)
            yield record
            count += 1
            if limit is not None and count >= limit:
                return


def load_import_records(paths: list[Path], limit: int | None, min_text_chars: int, max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    sources: dict[int, str] = {}
    for record in iter_import_records(paths, limit, min_text_chars, max_record_bytes):
        if record.doc_id in sources:
            raise ValueError(f"duplicate imported doc_id={record.doc_id}: {sources[record.doc_id]} and {record.source_file}")
        sources[record.doc_id] = record.source_file
        records.append(record)
    return records


def execute_sql(sql: str, psql_command: str) -> None:
    result = subprocess.run(psql_command, shell=True, input=sql, text=True, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("warc", nargs="+", type=Path, help="WARC or WARC.GZ file")
    parser.add_argument("--charset-override", action="append", default=[], metavar="LABEL=CODEC", help="explicitly correct a declared charset; strict decoding, recorded in summary; repeatable")
    parser.add_argument("--output", "-o", type=Path, help="write generated SQL to this file")
    parser.add_argument("--execute", action="store_true", help="execute generated SQL with psql")
    parser.add_argument("--skip-schema", action="store_true", help="omit schema/table/index DDL after administrator installation; use for least-privilege writers")
    parser.add_argument("--psql", default=os.environ.get("PGWARC_PSQL", DEFAULT_PSQL))
    parser.add_argument("--lance-uri", help="Lance dataset URI to create/append vectors into")
    parser.add_argument("--vector-dim", type=int, default=3)
    parser.add_argument(
        "--lance-mode",
        choices=LANCE_MODES,
        default="create",
        help=(
            "Lance dataset handling before vector inserts: create fails if the dataset "
            "already exists, overwrite recreates it, append adds rows, upsert merges by doc_id in an existing dataset"
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
    parser.add_argument("--temp-dir", type=Path, help="directory for disk-backed input/vector/SQL spool files")
    parser.add_argument("--embedding-timeout", type=float, default=600, help="embedding command timeout in seconds (default: 600)")
    parser.add_argument("--batch-max-bytes", type=int, default=16 * 1024 * 1024, help="approximate input bytes per SQL batch, excluding a single oversized record (default: 16 MiB)")
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
    parser.add_argument("--max-record-bytes", type=int, default=DEFAULT_MAX_RECORD_BYTES,
                        help="maximum WARC payload and decoded HTTP body bytes per record (default: 64 MiB)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-text-chars", type=int, default=1)
    parser.add_argument("--summary", action="store_true", help="print import summary to stderr")
    parser.add_argument("--summary-json", type=Path, help="write machine-readable summary JSON")
    return parser.parse_args(argv)


class ImportSpool:
    """Disk-backed CLI input and embeddings, validated before any SQL executes."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.db = sqlite3.connect(directory / "input.sqlite")
        # Keep the cache small regardless of corpus size. All spool files are
        # disposable and are removed by TemporaryDirectory after the import.
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute("CREATE TABLE records (doc_id INTEGER PRIMARY KEY, ordinal INTEGER, source TEXT, data TEXT)")
        self.db.execute("CREATE TABLE vectors (doc_id INTEGER PRIMARY KEY, data TEXT)")
        self.count = 0

    def close(self) -> None:
        self.db.close()

    def add_records(self, records: Iterable[ImportRecord]) -> None:
        for record in records:
            try:
                self.db.execute("INSERT INTO records VALUES (?,?,?,?)", (
                    record.doc_id, self.count, record.source_file,
                    json.dumps(dataclasses.asdict(record), ensure_ascii=False),
                ))
            except sqlite3.IntegrityError as exc:
                previous = self.db.execute("SELECT source FROM records WHERE doc_id=?", (record.doc_id,)).fetchone()[0]
                raise ValueError(f"duplicate imported doc_id={record.doc_id}: {previous} and {record.source_file}") from exc
            self.count += 1
        self.db.execute("CREATE INDEX records_ordinal ON records (ordinal)")
        self.db.commit()

    def records(self) -> Iterator[ImportRecord]:
        for (data,) in self.db.execute("SELECT data FROM records ORDER BY ordinal"):
            yield ImportRecord(**json.loads(data))

    def write_records(self, path: Path) -> None:
        with path.open("w", encoding="utf-8") as output:
            for record in self.records():
                output.write(json.dumps(record_to_json(record), ensure_ascii=False, sort_keys=True) + "\n")

    def add_vectors(self, path: Path, vector_dim: int) -> None:
        with path.open(encoding="utf-8") as source:
            line_no = 0
            max_line = 65536 + vector_dim * 64
            while True:
                line = source.readline(max_line + 1)
                if not line:
                    break
                line_no += 1
                if len(line) > max_line:
                    raise ValueError(f"{path}:{line_no}: embedding row exceeds size limit")
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_no}: expected JSON object")
                raw_id = row.get("doc_id", row.get("id"))
                if raw_id is None:
                    raise ValueError(f"{path}:{line_no}: missing doc_id")
                doc_id = int(raw_id)
                if not self.db.execute("SELECT 1 FROM records WHERE doc_id=?", (doc_id,)).fetchone():
                    continue
                vector = parse_vector(row.get("vector"), doc_id, vector_dim, str(path))
                try:
                    self.db.execute("INSERT INTO vectors VALUES (?,?)", (doc_id, json.dumps(vector)))
                except sqlite3.IntegrityError as exc:
                    raise ValueError(f"{path}:{line_no}: duplicate vector for doc_id={doc_id}") from exc
        missing = self.db.execute("SELECT doc_id FROM records WHERE doc_id NOT IN (SELECT doc_id FROM vectors) LIMIT 5").fetchall()
        if missing:
            raise ValueError(f"{path}: missing vectors for doc_id(s): " + ", ".join(str(row[0]) for row in missing))
        self.db.commit()

    def get(self, doc_id: int) -> list[float] | None:
        row = self.db.execute("SELECT data FROM vectors WHERE doc_id=?", (doc_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def __len__(self) -> int:
        return self.db.execute("SELECT count(*) FROM vectors").fetchone()[0]


def run_spooled_import(args: argparse.Namespace, lance_mode: str) -> int:
    charset_policy = CharsetPolicy.from_options(getattr(args, "charset_override", []))
    with tempfile.TemporaryDirectory(prefix="pgwarc-import-", dir=args.temp_dir) as directory:
        root = Path(directory)
        spool = ImportSpool(root)
        try:
            spool.add_records(iter_import_records(args.warc, args.limit, args.min_text_chars, args.max_record_bytes, charset_policy))
            if args.records_jsonl:
                spool.write_records(args.records_jsonl)
            vectors = None
            if args.embedding_command:
                records_path, vectors_path = root / "records.jsonl", root / "vectors.jsonl"
                spool.write_records(records_path)
                with records_path.open("rb") as source, vectors_path.open("wb") as output:
                    process = subprocess.Popen(args.embedding_command, shell=True, stdin=source, stdout=output, start_new_session=True)
                    try:
                        returncode = process.wait(timeout=args.embedding_timeout)
                    except BaseException:
                        # Kill the command's whole process group on timeout or
                        # Ctrl-C, so its worker children cannot outlive the spool.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                        raise
                if returncode:
                    raise SystemExit(returncode)
                spool.add_vectors(vectors_path, args.vector_dim)
                vectors = spool
            elif args.embedding_jsonl:
                spool.add_vectors(args.embedding_jsonl, args.vector_dim)
                vectors = spool

            # Generate the complete script on disk before execution. A malformed
            # later record/vector must not leave early batches in the database.
            vector_weight = args.vector_dim if args.lance_uri and (vectors is not None or not args.no_hash_vectors) else 0
            batch_count = sum(1 for _ in record_batches(spool.records(), args.batch_size, args.batch_max_bytes, vector_weight))
            sql_path = root / "import.sql"
            with sql_path.open("w", encoding="utf-8") as output:
                for chunk in iter_sql_chunks(
                    spool.records(), args.lance_uri, args.vector_dim,
                    args.overwrite_lance, not args.no_hash_vectors and vectors is None,
                    vectors, args.batch_size, lance_mode, args.bm25_mode, spool.count,
                    args.batch_max_bytes, batch_count, args.skip_schema,
                ):
                    output.write(chunk)
            if args.output:
                shutil.copyfile(sql_path, args.output)
            elif not args.execute:
                with sql_path.open(encoding="utf-8") as source:
                    shutil.copyfileobj(source, sys.stdout)
            if args.execute:
                with sql_path.open("rb") as source:
                    result = subprocess.run(args.psql, shell=True, stdin=source)
                if result.returncode:
                    raise SystemExit(result.returncode)
            vector_count = vector_summary(args.lance_uri, args.no_hash_vectors, vectors)
            summary = {
                "charset_overrides": charset_policy.overrides,
                "charset_override_records": charset_policy.used,
                "charset_override_errors": "strict",
                "records": spool.count, "sql_bytes": sql_path.stat().st_size,
                "vectors": vector_count, "lance_mode": lance_mode if args.lance_uri else "none",
                "lance_append_duplicates": "possible" if args.lance_uri and lance_mode == "append" and vector_count != 0 else "none",
                "bm25_mode": args.bm25_mode, "execute": args.execute, "psql": args.psql,
            }
            if args.summary_json:
                args.summary_json.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
            if args.summary:
                print(format_summary(summary), file=sys.stderr)
            return 0
        finally:
            spool.close()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.embedding_timeout <= 0 or not math.isfinite(args.embedding_timeout):
        raise SystemExit("--embedding-timeout must be finite and positive")
    if args.max_record_bytes <= 0:
        raise SystemExit("--max-record-bytes must be positive")
    if args.vector_dim <= 0:
        raise SystemExit("--vector-dim must be positive")
    if args.batch_max_bytes <= 0:
        raise SystemExit("--batch-max-bytes must be positive")
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
        if args.lance_mode in {"append", "upsert"}:
            raise SystemExit("--overwrite-lance conflicts with --lance-mode append/upsert")
        lance_mode = "overwrite"

    if lance_mode == "upsert" and args.batch_size > 10_000:
        raise SystemExit("upsert --batch-size must not exceed 10000")
    return run_spooled_import(args, lance_mode)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
