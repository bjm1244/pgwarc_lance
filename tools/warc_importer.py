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
import threading
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator
from urllib.parse import urlsplit

try:
    from tools import collection_manifest
except ModuleNotFoundError:  # direct execution with tools/ as sys.path[0]
    import collection_manifest


HEADER_ENCODING = "iso-8859-1"
MAX_HEADER_BYTES = 64 * 1024
DEFAULT_MAX_RECORD_BYTES = 64 * 1024 * 1024
# Tokens above this size use the extension's fixed-size SHA-256 posting key.
MAX_BM25_TERM_BYTES = 2000
MAX_SEARCHABLE_TERM_BYTES = 2_500_000
LONG_TERM_STORAGE_MIN_VERSION = "0.1.16"
LONG_TERM_KEY_PREFIX = "\u241fpgwarc-long:"
DEFAULT_PSQL = (
    "psql -h localhost -p 55432 -U pgwarc_lance "
    "-d pgwarc_lance_test -v ON_ERROR_STOP=1"
)
LANCE_MODES = ("create", "overwrite", "append", "upsert", "outbox")
OUTBOX_MIN_EXTENSION_VERSION = "0.1.23"
MAX_CHUNKS_PER_DOCUMENT = 10_000
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
    extraction_status: str = "text"
    parent_doc_id: int | None = None
    chunk_index: int = 0
    char_start: int = 0
    char_end: int = 0
    parent_text_len: int = 0
    chunking_version: str = "whole-document-v1"
    domain: str | None = None
    content_language: str | None = None
    media_type: str | None = None
    collection_id: str | None = None


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
    record_overrides: dict[str, str] = dataclasses.field(default_factory=dict)
    record_seen: dict[str, int] = dataclasses.field(default_factory=dict)
    record_used: dict[str, int] = dataclasses.field(default_factory=dict)
    record_replacement_characters: dict[str, int] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_options(
        cls, options: list[str], record_options: list[str] | None = None
    ) -> CharsetPolicy:
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

        record_overrides: dict[str, str] = {}
        for option in record_options or []:
            record_id, separator, codec = option.rpartition("=")
            record_id, codec = record_id.strip(), codec.strip()
            if (
                not separator
                or not record_id
                or not codec
                or any(char.isspace() for char in record_id)
            ):
                raise ValueError(
                    "--charset-record-override requires WARC-RECORD-ID=CODEC"
                )
            try:
                b"codec validation".decode(codec)
                codec = codecs.lookup(codec).name
            except (LookupError, TypeError) as exc:
                raise ValueError(f"invalid override text codec: {codec!r}") from exc
            if record_id in record_overrides:
                raise ValueError(
                    f"duplicate charset override WARC-Record-ID: {record_id!r}"
                )
            record_overrides[record_id] = codec
        return cls(overrides, record_overrides=record_overrides)

    def observe_record(self, record_id: str | None) -> None:
        if record_id not in self.record_overrides:
            return
        seen = self.record_seen.get(record_id, 0) + 1
        self.record_seen[record_id] = seen
        if seen > 1:
            raise ValueError(
                "duplicate WARC-Record-ID matched charset record override: "
                f"{record_id!r}"
            )

    def validate_record_overrides(self) -> None:
        unused = sorted(set(self.record_overrides) - set(self.record_used))
        if unused:
            raise ValueError(
                "charset record override did not produce an imported text record: "
                + ", ".join(repr(record_id) for record_id in unused)
            )


@dataclasses.dataclass
class PdfTextExtractor:
    executable: str
    version: str
    timeout_seconds: float
    max_output_bytes: int = DEFAULT_MAX_RECORD_BYTES
    attempted: int = 0
    extracted: int = 0
    output_bytes: int = 0

    @classmethod
    def from_command(
        cls,
        command: str | None,
        timeout_seconds: float,
        max_output_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    ) -> PdfTextExtractor | None:
        if command is None:
            return None
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise ValueError("--pdf-timeout-seconds must be finite and positive")
        if max_output_bytes <= 0:
            raise ValueError("--max-record-bytes must be positive")
        executable = shutil.which(command)
        if executable is None:
            candidate = Path(command)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                executable = str(candidate.resolve())
        if executable is None:
            raise ValueError("--pdftotext must name an executable Poppler pdftotext")
        try:
            result = subprocess.run(
                [executable, "-v"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=min(timeout_seconds, 10), check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("could not query configured pdftotext version") from exc
        version_output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        version_line = next((line.strip() for line in version_output.splitlines() if line.strip()), "")
        if result.returncode != 0 or not re.match(r"pdftotext version [^\s]+$", version_line, re.IGNORECASE):
            raise ValueError("--pdftotext must be a Poppler pdftotext executable with a readable version")
        return cls(executable, version_line, timeout_seconds, max_output_bytes)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "executable": self.executable,
            "version": self.version,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "attempted": self.attempted,
            "extracted": self.extracted,
            "output_bytes": self.output_bytes,
        }

    def extract(self, body: bytes, max_output_bytes: int, record_key: int | None = None) -> str:
        if max_output_bytes <= 0:
            raise ValueError("max_record_bytes must be positive")
        if self.timeout_seconds <= 0 or not math.isfinite(self.timeout_seconds):
            raise ValueError("--pdf-timeout-seconds must be finite and positive")
        self.attempted += 1
        identity = f" doc_id={record_key}" if record_key is not None else ""
        try:
            process = subprocess.Popen(
                [self.executable, "-enc", "UTF-8", "-nopgbrk", "-layout", "-", "-"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise ValueError("could not start configured pdftotext") from exc

        output = bytearray()
        output_limit_exceeded = threading.Event()

        def read_output() -> None:
            assert process.stdout is not None
            try:
                while True:
                    try:
                        chunk = process.stdout.read(64 * 1024)
                    except OSError:
                        return
                    if not chunk:
                        return
                    remaining = max_output_bytes + 1 - len(output)
                    if remaining > 0:
                        output.extend(chunk[:remaining])
                    if len(output) > max_output_bytes and not output_limit_exceeded.is_set():
                        output_limit_exceeded.set()
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            finally:
                process.stdout.close()

        def write_input() -> None:
            assert process.stdin is not None
            try:
                process.stdin.write(body)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        reader = threading.Thread(target=read_output, daemon=True)
        writer = threading.Thread(target=write_input, daemon=True)
        reader.start()
        writer.start()
        try:
            process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            writer.join()
            reader.join()
            raise ValueError(
                f"pdftotext timed out after {self.timeout_seconds:g}s{identity}"
            ) from exc
        except BaseException:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            writer.join()
            reader.join()
            raise
        writer.join()
        reader.join()

        output_size = len(output)
        if output_limit_exceeded.is_set() or output_size > max_output_bytes:
            raise ValueError(
                f"pdftotext output exceeds max_record_bytes={max_output_bytes}{identity}"
            )
        if process.returncode != 0:
            raise ValueError(
                f"pdftotext failed with exit code {process.returncode}{identity}"
            )
        try:
            text = bytes(output).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"pdftotext returned invalid UTF-8{identity}") from exc
        text = normalize_text(text)
        if not text:
            raise ValueError(f"pdftotext returned no text; OCR or manual review is required{identity}")
        self.extracted += 1
        self.output_bytes += output_size
        return text


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOCX_DOCUMENT_PART = "word/document.xml"
DOCX_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_DOCX_MEMBERS = 4096


class _DoctypeRejectingReader:
    """Scan an XML stream for declarations before ElementTree sees the bytes."""

    def __init__(self, stream: BinaryIO, max_bytes: int) -> None:
        self.stream = stream
        self.max_bytes = max_bytes
        self.bytes_read = 0
        self.tail = b""

    def read(self, size: int = -1) -> bytes:
        chunk = self.stream.read(size)
        self.bytes_read += len(chunk)
        if self.bytes_read > self.max_bytes:
            raise ValueError("DOCX main XML exceeds the configured record byte limit")
        if b"\x00" in chunk:
            raise ValueError("DOCX main XML with NUL bytes is not supported")
        probe = (self.tail + chunk).upper()
        if b"<!DOCTYPE" in probe or b"<!ENTITY" in probe:
            raise ValueError("DOCX XML DTD/entity declarations are not supported")
        self.tail = probe[-16:]
        return chunk


@dataclasses.dataclass
class DocxTextExtractor:
    max_output_bytes: int = DEFAULT_MAX_RECORD_BYTES
    attempted: int = 0
    extracted: int = 0
    output_bytes: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "implementation": "bounded-stdlib-wordprocessingml-v1",
            "max_output_bytes": self.max_output_bytes,
            "attempted": self.attempted,
            "extracted": self.extracted,
            "output_bytes": self.output_bytes,
        }

    def extract(self, body: bytes, max_output_bytes: int, record_key: int | None = None) -> str:
        if max_output_bytes <= 0:
            raise ValueError("max_record_bytes must be positive")
        self.attempted += 1
        identity = f" doc_id={record_key}" if record_key is not None else ""
        if len(body) > max_output_bytes:
            raise ValueError(f"DOCX input exceeds max_record_bytes={max_output_bytes}{identity}")

        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                members = archive.infolist()
                if len(members) > MAX_DOCX_MEMBERS:
                    raise ValueError(f"DOCX contains more than {MAX_DOCX_MEMBERS} ZIP entries{identity}")
                total_uncompressed = 0
                main_part = None
                seen_names: set[str] = set()
                for member in members:
                    name = member.filename.replace("\\", "/")
                    if name.startswith("/") or ".." in name.split("/"):
                        raise ValueError(f"DOCX contains an unsafe ZIP entry path{identity}")
                    if name in seen_names:
                        raise ValueError(f"DOCX contains duplicate ZIP entry names{identity}")
                    seen_names.add(name)
                    if member.flag_bits & 0x1:
                        raise ValueError(f"DOCX contains an encrypted ZIP entry{identity}")
                    total_uncompressed += member.file_size
                    if total_uncompressed > max_output_bytes:
                        raise ValueError(
                            f"DOCX expanded archive exceeds max_record_bytes={max_output_bytes}{identity}"
                        )
                    if name == DOCX_DOCUMENT_PART:
                        main_part = member
                if main_part is None or main_part.is_dir():
                    raise ValueError(f"DOCX is missing {DOCX_DOCUMENT_PART}{identity}")
                if main_part.file_size == 0:
                    raise ValueError(f"DOCX main document part is empty{identity}")

                pieces: list[str] = []
                text_bytes = 0

                def append_piece(value: str) -> None:
                    nonlocal text_bytes
                    text_bytes += len(value.encode("utf-8"))
                    if text_bytes > max_output_bytes:
                        raise ValueError(
                            f"DOCX extracted text exceeds max_record_bytes={max_output_bytes}{identity}"
                        )
                    pieces.append(value)

                with archive.open(main_part) as xml_stream:
                    guarded = _DoctypeRejectingReader(xml_stream, max_output_bytes)
                    for _, element in ET.iterparse(guarded, events=("end",)):
                        if element.tag == DOCX_WORD_NS + "t":
                            append_piece(element.text or "")
                        elif element.tag in {
                            DOCX_WORD_NS + "tab",
                            DOCX_WORD_NS + "br",
                            DOCX_WORD_NS + "cr",
                            DOCX_WORD_NS + "p",
                        }:
                            append_piece(" ")
                        element.clear()
        except ValueError:
            raise
        except (zipfile.BadZipFile, zipfile.LargeZipFile, ET.ParseError, OSError,
                RuntimeError, NotImplementedError, zlib.error) as exc:
            raise ValueError(f"invalid or unreadable DOCX archive{identity}") from exc

        text = normalize_text("".join(pieces))
        if not text:
            raise ValueError(f"DOCX contains no extractable text{identity}")
        output_size = len(text.encode("utf-8"))
        if output_size > max_output_bytes:
            raise ValueError(f"DOCX extracted text exceeds max_record_bytes={max_output_bytes}{identity}")
        self.extracted += 1
        self.output_bytes += output_size
        return text


TEXT_APPLICATION_MIME_TYPES = {
    "application/atom+xml",
    "application/http",
    "application/javascript",
    "application/json",
    "application/ld+json",
    "application/rss+xml",
    "application/x-bibtex",
    "application/x-chess-pgn",
    "application/x-research-info-systems",
    "application/xml",
}


class _MetadataOnlyExtraction(ValueError):
    """Internal signal: an explicitly selected extractor failed for one record."""


@dataclasses.dataclass
class ExtractionRetention:
    mode: str = "fail"
    metadata_only: dict[str, int] = dataclasses.field(default_factory=dict)
    errors: dict[str, int] = dataclasses.field(default_factory=dict)
    omitted_errors: int = 0

    def failed(self, kind: str, error: ValueError) -> None:
        if self.mode != "metadata-only":
            raise error
        self.metadata_only[kind] = self.metadata_only.get(kind, 0) + 1
        message = str(error)[:512]
        if message in self.errors or len(self.errors) < 16:
            self.errors[message] = self.errors.get(message, 0) + 1
        else:
            self.omitted_errors += 1
        raise _MetadataOnlyExtraction(str(error)) from error


def media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def target_domain(target_uri: str | None) -> str | None:
    """Return the normalized URI host used by exact domain search filters."""
    if not target_uri:
        return None
    try:
        host = urlsplit(target_uri).hostname
        if not host:
            return None
        # Keep internationalized hosts stable across backend locales.
        return host.rstrip(".").encode("idna").decode("ascii").lower() or None
    except (UnicodeError, ValueError):
        # Preserve the record and source URI; a malformed URI has no domain.
        return None


def content_languages(value: str | None) -> str | None:
    """Normalize a bounded HTTP Content-Language list for exact tag matching."""
    if not value:
        return None
    if len(value.encode("utf-8")) > 256:
        raise ValueError("HTTP Content-Language exceeds the 256-byte metadata limit")
    tags = [part.strip().lower() for part in value.split(",") if part.strip()]
    if len(tags) > 8:
        raise ValueError("HTTP Content-Language has more than 8 tags")
    for tag in tags:
        if len(tag) > 63 or tag.startswith("-") or tag.endswith("-") or "--" in tag:
            raise ValueError("HTTP Content-Language contains an invalid language tag")
        if any(not (char.isascii() and (char.isalnum() or char == "-")) for char in tag):
            raise ValueError("HTTP Content-Language contains an invalid language tag")
    return ",".join(tags) or None


def decode_body(
    body: bytes,
    headers: dict[str, str],
    content_type: str | None,
    max_decoded_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    charset_policy: CharsetPolicy | None = None,
    pdf_extractor: PdfTextExtractor | None = None,
    docx_extractor: DocxTextExtractor | None = None,
    record_key: int | None = None,
    record_id: str | None = None,
    retention: ExtractionRetention | None = None,
) -> str:
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
    mime = media_type(content_type)
    if mime == "application/pdf" or b"%PDF-" in body[:1024]:
        if pdf_extractor is None:
            identity = f" doc_id={record_key}" if record_key is not None else ""
            raise ValueError(
                "application/pdf requires --pdftotext; refusing to decode binary bytes as text"
                + identity
            )
        try:
            return pdf_extractor.extract(body, max_decoded_bytes, record_key)
        except ValueError as exc:
            if retention is not None:
                retention.failed("pdf", exc)
            raise
    if mime == DOCX_MIME:
        if docx_extractor is None:
            identity = f" doc_id={record_key}" if record_key is not None else ""
            raise ValueError("DOCX requires --extract-docx; refusing to decode binary bytes as text" + identity)
        try:
            return docx_extractor.extract(body, max_decoded_bytes, record_key)
        except ValueError as exc:
            if retention is not None:
                retention.failed("docx", exc)
            raise
    if mime and not (
        mime.startswith("text/")
        or mime in TEXT_APPLICATION_MIME_TYPES
        or mime.endswith("+xml")
        or mime.endswith("+json")
    ):
        identity = f" doc_id={record_key}" if record_key is not None else ""
        raise ValueError(f"unsupported MIME type {mime}; no text extractor is configured{identity}")
    label = charset_from_content_type(content_type).strip().lower()
    charset = HTTP_CHARSET_ALIASES.get(label, label)
    overridden = charset_policy is not None and label in charset_policy.overrides
    record_override = (
        charset_policy.record_overrides.get(record_id)
        if charset_policy is not None and record_id is not None
        else None
    )
    if overridden and record_override is not None:
        raise ValueError(
            "both charset label and WARC-Record-ID overrides match one record; "
            "use only one override for that record"
        )
    if overridden:
        charset = charset_policy.overrides[label]
    if record_override is not None:
        # A record-scoped correction is an explicit loss-tolerant choice.
        # Keep replacement characters visible in the import summary.
        text = body.decode(record_override, errors="replace")
        charset_policy.record_replacement_characters[record_id] = text.count(
            "\ufffd"
        )
    else:
        # Label-wide corrections remain strict so one broad mapping cannot
        # silently replace malformed bytes across an archive.
        text = body.decode(charset, errors="strict" if overridden else "replace")
    if overridden:
        charset_policy.used[label] = charset_policy.used.get(label, 0) + 1
    if content_type and "html" in content_type.lower():
        parser = HtmlTextExtractor()
        parser.feed(text)
        return parser.text()
    return normalize_text(text)


def import_record_from_warc(
    record: WarcRecord,
    max_decoded_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    charset_policy: CharsetPolicy | None = None,
    pdf_extractor: PdfTextExtractor | None = None,
    docx_extractor: DocxTextExtractor | None = None,
    retention: ExtractionRetention | None = None,
) -> ImportRecord | None:
    record_id = record.headers.get("warc-record-id")
    if charset_policy is not None:
        charset_policy.observe_record(record_id)
    record_type = record.headers.get("warc-type", "").lower()
    if record_type not in {"response", "resource"}:
        return None

    status_line, http_headers, body = parse_http_headers(record.payload)
    http_ct = http_headers.get("content-type")
    content_type = http_ct or record.headers.get("content-type")
    target_uri = record.headers.get("warc-target-uri")
    warc_date = record.headers.get("warc-date")
    payload_digest = (
        record.headers.get("warc-payload-digest")
        or record.headers.get("warc-block-digest")
        or "sha1:" + hashlib.sha1(record.payload).hexdigest()
    )
    doc_id = stable_doc_id(target_uri, warc_date, payload_digest)
    extraction_status = "text"
    try:
        text = decode_body(
            body if status_line else record.payload,
            http_headers if status_line else {}, content_type,
            max_decoded_bytes, charset_policy, pdf_extractor, docx_extractor,
            doc_id, record_id, retention,
        )
    except _MetadataOnlyExtraction:
        text = ""
        extraction_status = "metadata_only"
    if not text and extraction_status != "metadata_only":
        return None

    return ImportRecord(
        doc_id=doc_id,
        target_uri=target_uri,
        warc_date=warc_date,
        content_type=content_type,
        http_status=http_status(status_line),
        payload_digest=payload_digest,
        text=text,
        source_file=record.source_file,
        extraction_status=extraction_status,
        domain=target_domain(target_uri),
        content_language=content_languages(http_headers.get("content-language")),
        media_type=media_type(content_type) or None,
    )


def stable_doc_id(target_uri: str | None, warc_date: str | None, digest: str) -> int:
    raw = "\n".join([target_uri or "", warc_date or "", digest]).encode()
    value = int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big")
    value &= (1 << 63) - 1
    return value or 1


def stable_parent_doc_id(
    target_uri: str | None, warc_date: str | None, digest: str
) -> int:
    """Identify a captured URL/date across extraction revisions when possible."""
    if target_uri and warc_date:
        raw = "\n".join([target_uri, warc_date]).encode("utf-8")
        value = int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big")
        value &= (1 << 63) - 1
        return value or 1
    return stable_doc_id(target_uri, warc_date, digest)


def stable_chunk_id(parent_doc_id: int, version: str, start: int, end: int) -> int:
    raw = f"{parent_doc_id}\n{version}\n{start}\n{end}".encode("utf-8")
    value = int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big")
    value &= (1 << 63) - 1
    return value or 1


def chunk_import_record(
    record: ImportRecord,
    *,
    strategy: str = "whole_document",
    size_chars: int | None = None,
    overlap_chars: int = 0,
    version: str = "whole-document-v1",
) -> list[ImportRecord]:
    """Return indexable records with offsets in normalized extracted-text codepoints."""
    if strategy not in {"whole_document", "char_window"}:
        raise ValueError("chunking strategy must be whole_document or char_window")
    parent_id = (
        stable_parent_doc_id(record.target_uri, record.warc_date, record.payload_digest)
        if strategy == "char_window"
        else record.doc_id
    )
    text = record.text
    parent_len = len(text)
    if strategy == "whole_document" or record.extraction_status == "metadata_only" or not text:
        spans = [(0, parent_len)]
    else:
        if size_chars is None or size_chars <= 0:
            raise ValueError("char_window chunking requires a positive size_chars")
        if overlap_chars < 0 or overlap_chars >= size_chars:
            raise ValueError("overlap_chars must be non-negative and smaller than size_chars")
        step = size_chars - overlap_chars
        spans = []
        start = 0
        while start < parent_len:
            end = min(start + size_chars, parent_len)
            spans.append((start, end))
            if end == parent_len:
                break
            start += step
        if len(spans) > MAX_CHUNKS_PER_DOCUMENT:
            raise ValueError(
                f"doc_id={record.doc_id}: chunk count exceeds {MAX_CHUNKS_PER_DOCUMENT}; "
                "no SQL executed"
            )

    chunks = []
    for index, (start, end) in enumerate(spans):
        chunk_id = (
            stable_chunk_id(parent_id, version, start, end)
            if strategy == "char_window"
            else record.doc_id
        )
        chunks.append(dataclasses.replace(
            record,
            doc_id=chunk_id,
            text=text[start:end],
            parent_doc_id=parent_id,
            chunk_index=index,
            char_start=start,
            char_end=end,
            parent_text_len=parent_len,
            chunking_version=version,
        ))
    return chunks


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
    data = {
        "doc_id": record.doc_id,
        "parent_doc_id": record.parent_doc_id or record.doc_id,
        "chunk_index": record.chunk_index,
        "char_start": record.char_start,
        "char_end": record.char_end,
        "parent_text_len": record.parent_text_len or len(record.text),
        "chunking_version": record.chunking_version,
        "target_uri": record.target_uri,
        "warc_date": record.warc_date,
        "content_type": record.content_type,
        "http_status": record.http_status,
        "payload_digest": record.payload_digest,
        "revision": record_revision(record),
        "text_len": len(record.text),
        "source_file": record.source_file,
        "domain": record.domain,
        "content_language": record.content_language,
        "media_type": record.media_type,
        "collection_id": record.collection_id,
        "text": record.text,
    }

    if record.extraction_status != "text":
        data["extraction_status"] = record.extraction_status
    return data


def record_revision(record: ImportRecord) -> str:
    """Fingerprint imported metadata and BM25 content without exposing text."""
    payload = {
        "doc_id": record.doc_id,
        "target_uri": record.target_uri,
        "content_type": record.content_type,
        "http_status": record.http_status,
        "text_len": len(record.text),
        "payload_digest": record.payload_digest,
        "bm25_content_md5": hashlib.md5(record.text.encode("utf-8")).hexdigest(),
    }
    # Preserve the established revision for whole-document imports. Adding
    # provenance columns alone must not make every existing vector appear
    # stale and trigger a needless full re-embedding on the next outbox pass.
    is_whole_document = (
        (record.parent_doc_id is None or record.parent_doc_id == record.doc_id)
        and record.chunk_index == 0
        and record.char_start == 0
        and record.char_end in {0, len(record.text)}
        and record.parent_text_len in {0, len(record.text)}
    )
    if not is_whole_document:
        payload.update({
            "parent_doc_id": record.parent_doc_id or record.doc_id,
            "chunk_index": record.chunk_index,
            "char_start": record.char_start,
            "char_end": record.char_end,
            "parent_text_len": record.parent_text_len or len(record.text),
            "chunking_version": record.chunking_version,
        })
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def records_jsonl(records: Iterable[ImportRecord]) -> str:
    return "".join(
        json.dumps(record_to_json(record), ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )


def write_records_jsonl(records: Iterable[ImportRecord], path: Path) -> None:
    path.write_text(records_jsonl(records), encoding="utf-8")


def parse_vector(
    value: Any, doc_id: int, vector_dim: int, source: str,
    normalization: str | None = None,
) -> list[float]:
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
    if normalization == "l2":
        norm = math.sqrt(sum(item * item for item in vector))
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-3):
            raise ValueError(
                f"{source}: vector for doc_id={doc_id} has L2 norm {norm:.8g}, expected 1"
            )
    return vector


def parse_embedding_jsonl(
    content: str, required_doc_ids: set[int], vector_dim: int, source: str,
    normalization: str | None = None,
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
        vectors[doc_id] = parse_vector(
            row.get("vector"), doc_id, vector_dim, source, normalization
        )

    missing = sorted(required_doc_ids.difference(vectors))
    if missing:
        sample = ", ".join(str(doc_id) for doc_id in missing[:5])
        raise ValueError(f"{source}: missing vectors for doc_id(s): {sample}")
    return vectors


def load_embedding_jsonl(
    path: Path, records: Iterable[ImportRecord], vector_dim: int,
    normalization: str | None = None,
) -> dict[int, list[float]]:
    record_list = list(records)
    required_doc_ids = {record.doc_id for record in record_list if record.extraction_status != "metadata_only"}
    content = path.read_text(encoding="utf-8")
    return parse_embedding_jsonl(
        content, required_doc_ids, vector_dim, str(path), normalization
    )


def run_embedding_command(
    records: Iterable[ImportRecord], command: str, vector_dim: int,
    normalization: str | None = None,
) -> dict[int, list[float]]:
    record_list = [r for r in records if r.extraction_status != "metadata_only"]
    required_doc_ids = {record.doc_id for record in record_list if record.extraction_status != "metadata_only"}
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
    return parse_embedding_jsonl(
        result.stdout, required_doc_ids, vector_dim, command, normalization
    )


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

CREATE TABLE IF NOT EXISTS pgwarc_lance.bm25_long_term (
    term_key text PRIMARY KEY,
    term     text NOT NULL
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
    long_terms: dict[str, str] = {}
    for record in records:
        frequencies = bm25_term_frequencies(record.text)
        doc_len = sum(frequencies.values())
        doc_values.append(
            f"({record.doc_id}, {sql_string(record.text)}, {doc_len})"
        )
        for term in sorted(frequencies):
            storage_term, original_term = bm25_storage_term(term)
            if original_term is not None:
                existing = long_terms.setdefault(storage_term, original_term)
                if existing != original_term:
                    raise ValueError("SHA-256 collision between distinct oversized BM25 tokens")
            term_values.append(
                f"({sql_string(storage_term)}, {record.doc_id}, {frequencies[term]})"
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
    lines.append(delete_bm25_postings_sql([record.doc_id for record in records]))
    lines.extend(long_term_dictionary_sql(long_terms))
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
        delete_bm25_postings_sql(doc_ids),
        f"DELETE FROM pgwarc_lance.bm25_doc WHERE doc_id = ANY({sql_bigint_array(doc_ids)});",
        "COPY pgwarc_lance.bm25_doc (doc_id, content, doc_len) FROM stdin;",
    ]
    term_rows: list[str] = []
    long_terms: dict[str, str] = {}
    for record in records:
        frequencies = bm25_term_frequencies(record.text)
        doc_len = sum(frequencies.values())
        lines.append(copy_row([record.doc_id, record.text, doc_len]))
        for term in sorted(frequencies):
            storage_term, original_term = bm25_storage_term(term)
            if original_term is not None:
                existing = long_terms.setdefault(storage_term, original_term)
                if existing != original_term:
                    raise ValueError("SHA-256 collision between distinct oversized BM25 tokens")
            term_rows.append(copy_row([storage_term, record.doc_id, frequencies[term]]))
    lines.append(r"\.")

    lines.extend(long_term_dictionary_sql(long_terms))
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
    requires_long_term_storage: bool = False,
    reconcile_chunks: bool = False,
) -> Iterator[str]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if lance_mode is None:
        lance_mode = "overwrite" if overwrite_lance else "create"
    if lance_mode not in LANCE_MODES:
        raise ValueError(f"lance_mode must be one of: {', '.join(LANCE_MODES)}")
    if lance_mode in {"upsert", "outbox"} and batch_size > 10_000:
        raise ValueError(f"{lance_mode} batch_size must not exceed 10000")
    if bm25_mode not in BM25_MODES:
        raise ValueError(f"bm25_mode must be one of: {', '.join(BM25_MODES)}")
    if reconcile_chunks and (not lance_uri or lance_mode != "outbox"):
        raise ValueError("chunk reconciliation requires a Lance URI and outbox mode")

    has_lance_vectors = bool(lance_uri) and (include_hash_vectors or vectors_by_doc_id is not None)
    requires_dataset_lock = has_lance_vectors and lance_mode in {"upsert", "outbox"}

    lines: list[str] = [
        "SET standard_conforming_strings = on;",
        "CREATE SCHEMA IF NOT EXISTS pgwarc_lance;",
        bm25_table_sql(),
        """
CREATE TABLE IF NOT EXISTS pgwarc_lance.warc_record (
    doc_id         bigint PRIMARY KEY,
    parent_doc_id  bigint,
    chunk_index    integer,
    char_start     integer,
    char_end       integer,
    parent_text_len integer,
    chunking_version text,
    target_uri     text,
    warc_date      timestamptz,
    content_type   text,
    http_status    integer,
    payload_digest text,
    text_len       integer NOT NULL DEFAULT 0,
    source_file    text,
    domain         text,
    content_language text,
    media_type     text,
    collection_id  text,
    imported_at    timestamptz NOT NULL DEFAULT now()
);
""".strip(),
        "CREATE INDEX IF NOT EXISTS warc_record_target_uri_idx "
        "ON pgwarc_lance.warc_record (target_uri);",
        "CREATE INDEX IF NOT EXISTS warc_record_warc_date_idx "
        "ON pgwarc_lance.warc_record (warc_date);",
        "CREATE INDEX IF NOT EXISTS warc_record_domain_idx "
        "ON pgwarc_lance.warc_record (domain);",
        "CREATE INDEX IF NOT EXISTS warc_record_language_gin_idx "
        "ON pgwarc_lance.warc_record USING gin (string_to_array(content_language, ','));",
        "CREATE INDEX IF NOT EXISTS warc_record_media_type_idx "
        "ON pgwarc_lance.warc_record (media_type);",
        "CREATE INDEX IF NOT EXISTS warc_record_collection_idx "
        "ON pgwarc_lance.warc_record (collection_id);",
    ]
    if skip_schema:
        lines = ["SET standard_conforming_strings = on;"]
    else:
        lines.extend([
            "ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS parent_doc_id bigint;",
            "ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS chunk_index integer;",
            "ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS char_start integer;",
            "ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS char_end integer;",
            "ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS parent_text_len integer;",
            "ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS chunking_version text;",
            "ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS domain text;",
            "ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS content_language text;",
            "ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS media_type text;",
            "ALTER TABLE pgwarc_lance.warc_record ADD COLUMN IF NOT EXISTS collection_id text;",
            "CREATE INDEX IF NOT EXISTS warc_record_domain_idx ON pgwarc_lance.warc_record (domain);",
            "CREATE INDEX IF NOT EXISTS warc_record_language_gin_idx ON pgwarc_lance.warc_record USING gin (string_to_array(content_language, ','));",
            "CREATE INDEX IF NOT EXISTS warc_record_media_type_idx ON pgwarc_lance.warc_record (media_type);",
            "CREATE INDEX IF NOT EXISTS warc_record_collection_idx ON pgwarc_lance.warc_record (collection_id);",
            "CREATE INDEX IF NOT EXISTS warc_record_parent_chunk_idx "
            "ON pgwarc_lance.warc_record (parent_doc_id, chunk_index);",
        ])
    if reconcile_chunks:
        lines.extend([
            "DROP TABLE IF EXISTS pg_temp.pglance_import_expected_chunks;",
            "CREATE TEMP TABLE pglance_import_expected_chunks ("
            "parent_doc_id bigint NOT NULL, doc_id bigint PRIMARY KEY) ON COMMIT PRESERVE ROWS;",
        ])
    requires_outbox = lance_mode == "outbox"
    guards = []
    if requires_long_term_storage:
        guards.append(long_term_version_guard_sql())
    if requires_outbox or requires_dataset_lock:
        guards.append(lance_outbox_version_guard_sql())
    if guards:
        lines = ["BEGIN;", *guards, *lines]
    if lance_uri and has_lance_vectors and lance_mode in {"create", "overwrite"}:
        overwrite = "true" if lance_mode == "overwrite" else "false"
        lines.append(
            f"SELECT lance_create_table({sql_string(lance_uri)}, {vector_dim}, {overwrite});"
        )
    elif lance_uri and has_lance_vectors and lance_mode == "append":
        lines.append(f"-- WARNING: {APPEND_DUPLICATE_WARNING}.")

    if guards:
        lines.append("COMMIT;")

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
        lines.extend(guards)
        if requires_dataset_lock:
            lines.append(f"SELECT lance_dataset_write_lock({sql_string(lance_uri)});")
        lance_ids: list[int] = []
        lance_flat_vectors: list[float] = []
        lance_labels: list[str] = []
        lance_revisions: list[str] = []
        for record in batch_records:
            lines.append(
                """
INSERT INTO pgwarc_lance.warc_record (
    doc_id, parent_doc_id, chunk_index, char_start, char_end, parent_text_len,
    chunking_version, target_uri, warc_date, content_type, http_status,
    payload_digest, text_len, source_file, domain, content_language,
    media_type, collection_id, imported_at
) VALUES (
    {doc_id}, {parent_doc_id}, {chunk_index}, {char_start}, {char_end}, {parent_text_len},
    {chunking_version}, {target_uri}, {warc_date}, {content_type}, {http_status},
    {payload_digest}, {text_len}, {source_file}, {domain}, {content_language},
    {media_type}, {collection_id}, now()
) ON CONFLICT (doc_id) DO UPDATE SET
    parent_doc_id = EXCLUDED.parent_doc_id,
    chunk_index = EXCLUDED.chunk_index,
    char_start = EXCLUDED.char_start,
    char_end = EXCLUDED.char_end,
    parent_text_len = EXCLUDED.parent_text_len,
    chunking_version = EXCLUDED.chunking_version,
    target_uri = EXCLUDED.target_uri,
    warc_date = EXCLUDED.warc_date,
    content_type = EXCLUDED.content_type,
    http_status = EXCLUDED.http_status,
    payload_digest = EXCLUDED.payload_digest,
    text_len = EXCLUDED.text_len,
    source_file = EXCLUDED.source_file,
    domain = EXCLUDED.domain,
    content_language = EXCLUDED.content_language,
    media_type = EXCLUDED.media_type,
    collection_id = EXCLUDED.collection_id,
    imported_at = now();
""".strip().format(
                    doc_id=record.doc_id,
                    parent_doc_id=(record.parent_doc_id or record.doc_id),
                    chunk_index=record.chunk_index,
                    char_start=record.char_start,
                    char_end=record.char_end,
                    parent_text_len=(record.parent_text_len or len(record.text)),
                    chunking_version=sql_string(record.chunking_version),
                    target_uri=sql_string(record.target_uri),
                    warc_date=sql_timestamp(record.warc_date),
                    content_type=sql_string(record.content_type),
                    http_status=sql_int(record.http_status),
                    payload_digest=sql_string(record.payload_digest),
                    text_len=len(record.text),
                    source_file=sql_string(record.source_file),
                    domain=sql_string(record.domain),
                    content_language=sql_string(record.content_language),
                    media_type=sql_string(record.media_type),
                    collection_id=sql_string(record.collection_id),
                )
            )
            if reconcile_chunks:
                lines.append(
                    "INSERT INTO pg_temp.pglance_import_expected_chunks (parent_doc_id, doc_id) "
                    f"VALUES ({record.parent_doc_id or record.doc_id}, {record.doc_id}) "
                    "ON CONFLICT (doc_id) DO UPDATE SET parent_doc_id = EXCLUDED.parent_doc_id;"
                )
            if bm25_mode == "function":
                lines.append(
                    f"SELECT bm25_index_document({record.doc_id}, {sql_string(record.text)});"
                )

            vector = None
            if lance_uri and vectors_by_doc_id is not None and record.extraction_status != "metadata_only":
                vector = vectors_by_doc_id.get(record.doc_id)
            elif lance_uri and include_hash_vectors and record.extraction_status != "metadata_only":
                vector = hash_embedding(record.text, vector_dim)

            if lance_uri and vector is not None:
                lance_ids.append(record.doc_id)
                lance_flat_vectors.extend(vector)
                lance_labels.append(record.target_uri or str(record.doc_id))
                if lance_mode == "outbox":
                    lance_revisions.append(record_revision(record))
        if bm25_mode == "bulk":
            lines.extend(emit_bm25_bulk_sql(batch_records))
        elif bm25_mode == "copy":
            lines.extend(emit_bm25_copy_sql(batch_records))
        if lance_uri and lance_ids:
            lines.append(
                f"SELECT {'lance_outbox_enqueue_revision' if lance_mode == 'outbox' else 'lance_upsert_many' if lance_mode == 'upsert' else 'lance_insert_many'}("
                f"{sql_string(lance_uri)}, {sql_bigint_array(lance_ids)}, "
                f"{sql_vector(lance_flat_vectors)}, {vector_dim}, {sql_text_array(lance_labels)}"
                f"{', ' + sql_text_array(lance_revisions) if lance_mode == 'outbox' else ''}"
                ");"
            )
        lines.append("COMMIT;")
        yield "\n".join(lines) + "\n"
        start = end

    if reconcile_chunks:
        yield f"""
DO $pgwarc_chunk_reconcile$
DECLARE
    stale_ids bigint[];
    stale_revisions text[];
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pgwarc_lance.warc_record AS old_chunk
        JOIN (SELECT DISTINCT parent_doc_id FROM pg_temp.pglance_import_expected_chunks) AS parents
          USING (parent_doc_id)
        LEFT JOIN pgwarc_lance.lance_document_revision AS revision
          ON revision.uri = {sql_string(lance_uri)} AND revision.doc_id = old_chunk.doc_id
        WHERE NOT EXISTS (
            SELECT 1 FROM pg_temp.pglance_import_expected_chunks AS expected
            WHERE expected.doc_id = old_chunk.doc_id
        )
          AND revision.doc_id IS NULL
    ) THEN
        RAISE EXCEPTION 'cannot reconcile stale chunks without tracked outbox revisions; perform a complete revisioned outbox import first'
            USING ERRCODE = '55000';
    END IF;
    LOOP
        SELECT array_agg(stale.doc_id ORDER BY stale.doc_id),
               array_agg(stale.expected_revision ORDER BY stale.doc_id)
          INTO stale_ids, stale_revisions
        FROM (
            SELECT old_chunk.doc_id, revision.expected_revision
            FROM pgwarc_lance.warc_record AS old_chunk
            JOIN (SELECT DISTINCT parent_doc_id FROM pg_temp.pglance_import_expected_chunks) AS parents
              USING (parent_doc_id)
            JOIN pgwarc_lance.lance_document_revision AS revision
              ON revision.uri = {sql_string(lance_uri)} AND revision.doc_id = old_chunk.doc_id
            WHERE NOT EXISTS (
                SELECT 1 FROM pg_temp.pglance_import_expected_chunks AS expected
                WHERE expected.doc_id = old_chunk.doc_id
            )
              AND NOT revision.is_deleted
            ORDER BY old_chunk.doc_id
            LIMIT 10000
        ) AS stale;
        EXIT WHEN COALESCE(cardinality(stale_ids), 0) = 0;
        PERFORM lance_document_delete({sql_string(lance_uri)}, stale_ids, stale_revisions);
    END LOOP;
END
$pgwarc_chunk_reconcile$;
"""


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
    requires_long_term_storage: bool = False,
    reconcile_chunks: bool = False,
) -> str:
    record_list = list(records)
    return "".join(iter_sql_chunks(
        record_list, lance_uri, vector_dim, overwrite_lance, include_hash_vectors,
        vectors_by_doc_id, batch_size, lance_mode, bm25_mode, len(record_list),
        skip_schema=skip_schema,
        requires_long_term_storage=(requires_long_term_storage or any(
            validate_bm25_terms(record)[0] for record in record_list
        )),
        reconcile_chunks=reconcile_chunks,
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
    oversized = [validate_bm25_terms(record) for record in records]
    oversized_records = sum(1 for count, _ in oversized if count)
    oversized_count = sum(count for count, _ in oversized)
    max_term_bytes = max((size for _, size in oversized), default=0)
    required_versions = []
    if oversized_count:
        required_versions.append(LONG_TERM_STORAGE_MIN_VERSION)
    if lance_uri and lance_mode == "outbox":
        required_versions.append(OUTBOX_MIN_EXTENSION_VERSION)
    elif lance_uri and lance_mode == "upsert" and vectors != 0:
        required_versions.append(OUTBOX_MIN_EXTENSION_VERSION)
    required_version = max(required_versions, key=lambda value: tuple(map(int, value.split(".")))) if required_versions else None
    return {
        "records": len(records),
        "sql_bytes": len(sql.encode("utf-8")),
        "vectors": vectors,
        "lance_mode": lance_mode if lance_uri else "none",
        "lance_index_consistency": (
            "none" if not lance_uri else (
                "eventual_after_commit" if lance_mode == "outbox" else "external_immediate_non_atomic"
            )
        ),
        "lance_append_duplicates": "possible" if append_duplicates else "none",
        "bm25_mode": bm25_mode,
        "execute": execute,
        "psql": psql,
        "oversized_ascii_term_records": oversized_records,
        "oversized_ascii_terms": oversized_count,
        "max_oversized_ascii_term_bytes": max_term_bytes,
        "required_extension_version": required_version,
    }


def format_summary(data: dict[str, Any]) -> str:
    pdf = data.get("pdf_extraction", {})
    docx = data.get("docx_extraction", {})
    parts = [
        f"records={data['records']}",
        f"sql_bytes={data['sql_bytes']}",
        f"vectors={data['vectors']}",
        f"lance_mode={data['lance_mode']}",
        f"lance_index_consistency={data.get('lance_index_consistency', 'external_immediate_non_atomic')}",
        f"lance_append_duplicates={data['lance_append_duplicates']}",
        f"bm25_mode={data['bm25_mode']}",
        f"execute={data['execute']}",
        f"psql={shlex.quote(str(data['psql']))}",
        f"oversized_ascii_term_records={data.get('oversized_ascii_term_records', 0)}",
        f"oversized_ascii_terms={data.get('oversized_ascii_terms', 0)}",
        f"max_oversized_ascii_term_bytes={data.get('max_oversized_ascii_term_bytes', 0)}",
        f"required_extension_version={data.get('required_extension_version') or 'none'}",
        f"pdf_extracted={pdf.get('extracted', 0)}",
        f"docx_extracted={docx.get('extracted', 0)}",
    ]
    if pdf.get("enabled"):
        parts.append(f"pdf_tool_version={shlex.quote(str(pdf['version']))}")
    return " ".join(parts)


def validate_bm25_terms(record: ImportRecord) -> tuple[int, int]:
    """Report long ASCII tokens that will use fixed-size BM25 posting keys."""
    sizes = [len(match.group(0)) for match in re.finditer(r"[A-Za-z0-9]+", record.text)
             if len(match.group(0)) > MAX_BM25_TERM_BYTES]
    max_size = max(sizes, default=0)
    if max_size > MAX_SEARCHABLE_TERM_BYTES:
        raise ValueError(
            f"doc_id={record.doc_id}: BM25 token is {max_size} bytes, above the "
            f"maximum searchable size of {MAX_SEARCHABLE_TERM_BYTES}; no SQL executed"
        )
    return len(sizes), max_size


def bm25_storage_term(term: str) -> tuple[str, str | None]:
    """Return the posting key and, for a long token, its unindexed source text."""
    if len(term.encode("utf-8")) <= MAX_BM25_TERM_BYTES:
        return term, None
    key = LONG_TERM_KEY_PREFIX + hashlib.sha256(term.encode("utf-8")).hexdigest()
    return key, term


def long_term_dictionary_sql(entries: dict[str, str]) -> list[str]:
    if not entries:
        return []
    values = ",\n".join(
        f"({sql_string(key)}, {sql_string(term)})" for key, term in sorted(entries.items())
    )
    return [
        "INSERT INTO pgwarc_lance.bm25_long_term (term_key, term) VALUES\n"
        + values
        + "\nON CONFLICT (term_key) DO NOTHING;",
        "DO $pgwarc_lance_long_term_collision$\n"
        "BEGIN\n"
        "    IF EXISTS (\n"
        "        SELECT 1\n"
        "        FROM pgwarc_lance.bm25_long_term stored\n"
        "        JOIN (VALUES\n"
        + values
        + "\n        ) AS incoming(term_key, term) USING (term_key)\n"
        "        WHERE stored.term <> incoming.term\n"
        "    ) THEN\n"
        "        RAISE EXCEPTION 'SHA-256 collision between distinct oversized BM25 tokens; batch rolled back'\n"
        "            USING ERRCODE = '22000';\n"
        "    END IF;\n"
        "END\n"
        "$pgwarc_lance_long_term_collision$;",
    ]


def delete_bm25_postings_sql(doc_ids: list[int]) -> str:
    # Keep the long-term dictionary append-only. Deleting dictionary entries
    # in the same statement as posting deletion cannot observe the deleted
    # rows under PostgreSQL's data-modifying CTE snapshot rules; a later
    # maintenance command can prune orphans while writes are quiesced.
    return f"DELETE FROM pgwarc_lance.bm25_term WHERE doc_id = ANY({sql_bigint_array(doc_ids)});"


def long_term_version_guard_sql() -> str:
    return """DO $pgwarc_lance_long_term_version_guard$
DECLARE
    installed_version text;
BEGIN
    SELECT extversion INTO installed_version
    FROM pg_catalog.pg_extension
    WHERE extname = 'pgwarc_lance';

    IF installed_version IS NULL
       OR string_to_array(installed_version, '.')::integer[] < ARRAY[0, 1, 16] THEN
        RAISE EXCEPTION
            'oversized BM25 tokens require pgwarc_lance 0.1.16 or newer (found %)',
            COALESCE(installed_version, 'not installed')
            USING ERRCODE = '0A000';
    END IF;
END
$pgwarc_lance_long_term_version_guard$;"""


def lance_outbox_version_guard_sql() -> str:
    return """DO $pgwarc_lance_outbox_version_guard$
DECLARE
    installed_version text;
BEGIN
    SELECT extversion INTO installed_version
    FROM pg_catalog.pg_extension
    WHERE extname = 'pgwarc_lance';

    IF installed_version IS NULL
       OR string_to_array(installed_version, '.')::integer[] < ARRAY[0, 1, 23] THEN
        RAISE EXCEPTION
            'serialized document lifecycle requires pgwarc_lance 0.1.23 or newer (found %)',
            COALESCE(installed_version, 'not installed')
            USING ERRCODE = '0A000';
    END IF;
END
$pgwarc_lance_outbox_version_guard$;"""


def iter_import_records(
    paths: list[Path],
    limit: int | None,
    min_text_chars: int,
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    charset_policy: CharsetPolicy | None = None,
    pdf_extractor: PdfTextExtractor | None = None,
    docx_extractor: DocxTextExtractor | None = None,
    retention: ExtractionRetention | None = None,
    chunking_strategy: str = "whole_document",
    chunk_size_chars: int | None = None,
    chunk_overlap_chars: int = 0,
    chunking_version: str = "whole-document-v1",
    collection_id: str | None = None,
) -> Iterator[ImportRecord]:
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
            record = import_record_from_warc(
                warc_record, max_record_bytes, charset_policy, pdf_extractor, docx_extractor, retention
            )
            if record is None or (record.extraction_status != "metadata_only" and len(record.text) < min_text_chars):
                continue
            record.collection_id = collection_id
            record_id = warc_record.headers.get("warc-record-id")
            if charset_policy is not None and record_id in charset_policy.record_overrides:
                charset_policy.record_used[record_id] = charset_policy.record_used.get(record_id, 0) + 1
            yield from chunk_import_record(
                record,
                strategy=chunking_strategy,
                size_chars=chunk_size_chars,
                overlap_chars=chunk_overlap_chars,
                version=chunking_version,
            )
            count += 1
            if limit is not None and count >= limit:
                return


def load_import_records(
    paths: list[Path],
    limit: int | None,
    min_text_chars: int,
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    pdf_extractor: PdfTextExtractor | None = None,
    docx_extractor: DocxTextExtractor | None = None,
    chunking_strategy: str = "whole_document",
    chunk_size_chars: int | None = None,
    chunk_overlap_chars: int = 0,
    chunking_version: str = "whole-document-v1",
) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    sources: dict[int, str] = {}
    for record in iter_import_records(
        paths, limit, min_text_chars, max_record_bytes,
        pdf_extractor=pdf_extractor, docx_extractor=docx_extractor,
        chunking_strategy=chunking_strategy,
        chunk_size_chars=chunk_size_chars,
        chunk_overlap_chars=chunk_overlap_chars,
        chunking_version=chunking_version,
    ):
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
    parser.add_argument(
        "--charset-record-override", action="append", default=[],
        metavar="WARC-RECORD-ID=CODEC",
        help="decode one reviewed WARC record with replacement; U+FFFD is counted in the summary; repeatable",
    )
    parser.add_argument("--pdftotext", help="Poppler pdftotext executable required to import application/pdf records")
    parser.add_argument("--pdf-timeout-seconds", type=float, default=30, help="maximum time per PDF extraction (default: 30 seconds)")
    parser.add_argument("--document-error-policy", choices=["fail", "metadata-only"], default="fail", help="on configured PDF/DOCX extraction failure: fail before SQL (default), or preserve metadata without a vector")
    parser.add_argument("--extract-docx", action="store_true", help="extract bounded WordprocessingML text from DOCX records")
    parser.add_argument("--output", "-o", type=Path, help="write generated SQL to this file")
    parser.add_argument("--execute", action="store_true", help="execute generated SQL with psql")
    parser.add_argument("--skip-schema", action="store_true", help="omit schema/table/index DDL after administrator installation; use for least-privilege writers")
    parser.add_argument("--psql", default=os.environ.get("PGWARC_PSQL", DEFAULT_PSQL))
    parser.add_argument("--lance-uri", help="Lance dataset URI to create/append vectors into")
    parser.add_argument("--vector-dim", type=int, default=3)
    parser.add_argument(
        "--collection-manifest", type=Path,
        help="use an operator-managed collection manifest; holds a shared lock through import",
    )
    parser.add_argument(
        "--collection-version",
        help="target the staged version during reindex; defaults to the active version",
    )
    parser.add_argument("--embedding-model", help="model identifier asserted by the collection manifest")
    parser.add_argument("--embedding-model-version", help="immutable model/provider revision asserted by the manifest")
    parser.add_argument("--embedding-normalization", choices=["none", "l2"])
    parser.add_argument("--normalization-version", help="normalization implementation/version asserted by the manifest")
    parser.add_argument("--chunking-version", help="chunking configuration version asserted by the manifest")
    parser.add_argument("--chunking-strategy", choices=["whole_document", "char_window"],
                        help="chunking profile; a collection manifest takes precedence")
    parser.add_argument("--chunk-size-chars", type=int,
                        help="maximum Unicode codepoints per chunk for char_window")
    parser.add_argument("--chunk-overlap-chars", type=int,
                        help="overlap in Unicode codepoints between adjacent chunks")
    parser.add_argument(
        "--lance-mode",
        choices=LANCE_MODES,
        default="create",
        help=(
            "Lance dataset handling: create fails if the dataset already exists, "
            "overwrite recreates it, append adds rows, upsert merges by doc_id, "
            "outbox commits vector jobs for lance-outbox-worker (existing dataset required)"
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
            "copy streams bm25_doc/bm25_term rows with psql COPY FROM stdin; "
            "outbox commits vector jobs in SQL and requires lance-outbox-worker to materialize them"
        ),
    )
    parser.add_argument("--max-record-bytes", type=int, default=DEFAULT_MAX_RECORD_BYTES,
                        help="maximum WARC payload and decoded HTTP body bytes per record (default: 64 MiB)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-text-chars", type=int, default=1)
    parser.add_argument("--summary", action="store_true", help="print import summary to stderr")
    parser.add_argument("--summary-json", type=Path, help="write machine-readable summary JSON")
    parser.add_argument(
        "--audit-manifest-json",
        type=Path,
        help="preflight the complete input and save a private accounting/fingerprint manifest before SQL",
    )
    parser.add_argument(
        "--checkpoint-db",
        type=Path,
        help="opt in to a durable, size-limited SQLite checkpoint for resumable imports",
    )
    parser.add_argument(
        "--checkpoint-max-bytes",
        type=int,
        default=20 * 1024 * 1024 * 1024,
        help="maximum SQLite checkpoint database size (default: 20 GiB)",
    )
    parser.add_argument(
        "--checkpoint-max-memory-bytes",
        type=int,
        default=256 * 1024 * 1024,
        help="estimated working-memory ceiling for checkpoint mode (default: 256 MiB)",
    )
    return parser.parse_args(argv)


class ImportSpool:
    """Disk-backed CLI input and embeddings, validated before any SQL executes."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.db = sqlite3.connect(directory / "input.sqlite")
        # Keep the cache small regardless of corpus size. All spool files are
        # disposable and are removed by TemporaryDirectory after the import.
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute("CREATE TABLE records (doc_id INTEGER PRIMARY KEY, parent_doc_id INTEGER NOT NULL, ordinal INTEGER, source TEXT, data TEXT, text_available INTEGER NOT NULL)")
        self.db.execute("CREATE TABLE vectors (doc_id INTEGER PRIMARY KEY, data TEXT)")
        self.count = 0
        self.oversized_ascii_term_records = 0
        self.oversized_ascii_terms = 0
        self.max_oversized_ascii_term_bytes = 0

    def close(self) -> None:
        self.db.close()

    def add_records(self, records: Iterable[ImportRecord]) -> None:
        for record in records:
            oversized_count, max_term_bytes = validate_bm25_terms(record)
            self.oversized_ascii_term_records += int(oversized_count > 0)
            self.oversized_ascii_terms += oversized_count
            self.max_oversized_ascii_term_bytes = max(
                self.max_oversized_ascii_term_bytes, max_term_bytes
            )
            try:
                self.db.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", (
                    record.doc_id, record.parent_doc_id or record.doc_id,
                    self.count, record.source_file,
                    json.dumps(dataclasses.asdict(record), ensure_ascii=False),
                    int(record.extraction_status != "metadata_only"),
                ))
            except sqlite3.IntegrityError as exc:
                previous = self.db.execute("SELECT source FROM records WHERE doc_id=?", (record.doc_id,)).fetchone()[0]
                raise ValueError(f"duplicate imported doc_id={record.doc_id}: {previous} and {record.source_file}") from exc
            self.count += 1
        self.db.execute("CREATE INDEX records_ordinal ON records (ordinal)")
        self.db.commit()

    def records(self, embedding_only: bool = False) -> Iterator[ImportRecord]:
        sql = "SELECT data FROM records" + (" WHERE text_available=1" if embedding_only else "") + " ORDER BY ordinal"
        for (data,) in self.db.execute(sql):
            yield ImportRecord(**json.loads(data))

    def write_records(self, path: Path, embedding_only: bool = False) -> None:
        with path.open("w", encoding="utf-8") as output:
            for record in self.records(embedding_only):
                output.write(json.dumps(record_to_json(record), ensure_ascii=False, sort_keys=True) + "\n")

    def add_vectors(
        self, path: Path, vector_dim: int, normalization: str | None = None
    ) -> None:
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
                if not self.db.execute("SELECT 1 FROM records WHERE doc_id=? AND text_available=1", (doc_id,)).fetchone():
                    continue
                vector = parse_vector(
                    row.get("vector"), doc_id, vector_dim, str(path), normalization
                )
                try:
                    self.db.execute("INSERT INTO vectors VALUES (?,?)", (doc_id, json.dumps(vector)))
                except sqlite3.IntegrityError as exc:
                    raise ValueError(f"{path}:{line_no}: duplicate vector for doc_id={doc_id}") from exc
        missing = self.db.execute("SELECT doc_id FROM records WHERE text_available=1 AND doc_id NOT IN (SELECT doc_id FROM vectors) LIMIT 5").fetchall()
        if missing:
            raise ValueError(f"{path}: missing vectors for doc_id(s): " + ", ".join(str(row[0]) for row in missing))
        self.db.commit()

    def get(self, doc_id: int) -> list[float] | None:
        row = self.db.execute("SELECT data FROM vectors WHERE doc_id=?", (doc_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def __len__(self) -> int:
        return self.db.execute("SELECT count(*) FROM vectors").fetchone()[0]


def run_spooled_import(args: argparse.Namespace, lance_mode: str) -> int:
    if args.checkpoint_db:
        if __name__ == "__main__":
            repo_root = str(Path(__file__).resolve().parents[1])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
        from tools import resumable_import

        return resumable_import.run(args, lance_mode, sys.modules[__name__])

    audit_manifest: dict[str, Any] | None = None
    audit_inputs: list[dict[str, Any]] = []
    if args.audit_manifest_json:
        if args.limit is not None:
            raise SystemExit("--audit-manifest-json requires a full input scan without --limit")
        if __name__ == "__main__":
            repo_root = str(Path(__file__).resolve().parents[1])
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            sys.modules.setdefault("tools.warc_importer", sys.modules[__name__])
        from tools import audit_warc_import

        audit_charset = CharsetPolicy.from_options(
            getattr(args, "charset_override", []),
            getattr(args, "charset_record_override", []),
        )
        audit_pdf = PdfTextExtractor.from_command(
            getattr(args, "pdftotext", None),
            getattr(args, "pdf_timeout_seconds", 30),
            args.max_record_bytes,
        )
        audit_docx = (
            DocxTextExtractor(max_output_bytes=args.max_record_bytes)
            if args.extract_docx
            else None
        )
        audit_manifest = audit_warc_import.scan_warcs(
            args.warc,
            max_record_bytes=args.max_record_bytes,
            min_text_chars=args.min_text_chars,
            charset_policy=audit_charset,
            pdf_extractor=audit_pdf,
            docx_extractor=audit_docx,
            retention=ExtractionRetention(args.document_error_policy),
            import_settings={
                "bm25_mode": args.bm25_mode,
                "batch_size": args.batch_size,
                "batch_max_bytes": args.batch_max_bytes,
                "lance_mode": lance_mode if args.lance_uri else "none",
                "chunking": {
                    "strategy": args.chunking_strategy,
                    "size_chars": args.chunk_size_chars,
                    "overlap_chars": args.chunk_overlap_chars,
                    "version": args.chunking_version,
                },
                "lance_uri_sha256": (
                    hashlib.sha256(args.lance_uri.encode("utf-8")).hexdigest()
                    if args.lance_uri
                    else None
                ),
                "vector_dim": args.vector_dim,
                "no_hash_vectors": args.no_hash_vectors,
                "embedding_command_sha256": (
                    hashlib.sha256(args.embedding_command.encode("utf-8")).hexdigest()
                    if args.embedding_command
                    else None
                ),
                "embedding_jsonl_sha256": (
                    audit_warc_import.sha256_file(args.embedding_jsonl)
                    if args.embedding_jsonl
                    else None
                ),
            },
        )
        audit_charset.validate_record_overrides()
        audit_warc_import.write_private_manifest(
            args.audit_manifest_json, audit_manifest
        )
        if audit_manifest["status"] != "accounted":
            raise SystemExit("WARC preflight accounting was incomplete; no SQL executed")
        if not audit_manifest["accounting"]["import_acceptable"]:
            raise SystemExit(
                "WARC preflight found failed records; see the private manifest; no SQL executed"
            )
        audit_inputs = audit_manifest["inputs"]

    charset_policy = CharsetPolicy.from_options(
        getattr(args, "charset_override", []),
        getattr(args, "charset_record_override", []),
    )
    pdf_extractor = PdfTextExtractor.from_command(
        getattr(args, "pdftotext", None),
        getattr(args, "pdf_timeout_seconds", 30),
        args.max_record_bytes,
    )
    docx_extractor = DocxTextExtractor(max_output_bytes=args.max_record_bytes) if args.extract_docx else None
    retention = ExtractionRetention(getattr(args, "document_error_policy", "fail"))
    with tempfile.TemporaryDirectory(prefix="pgwarc-import-", dir=args.temp_dir) as directory:
        root = Path(directory)
        spool = ImportSpool(root)
        try:
            spool.add_records(iter_import_records(
                args.warc, args.limit, args.min_text_chars, args.max_record_bytes,
                charset_policy, pdf_extractor, docx_extractor, retention,
                args.chunking_strategy, args.chunk_size_chars,
                args.chunk_overlap_chars, args.chunking_version,
                getattr(args, "collection_id", None),
            ))
            charset_policy.validate_record_overrides()
            if audit_manifest is not None:
                expected_records = (
                    audit_manifest["outcomes"]["imported"]
                    + audit_manifest["outcomes"]["metadata_only"]
                )
                parent_count = spool.db.execute(
                    "SELECT count(DISTINCT parent_doc_id) FROM records"
                ).fetchone()[0]
                if parent_count != expected_records:
                    raise ValueError(
                        "WARC preflight/import record counts differ; no SQL executed"
                    )
                if any(
                    audit_warc_import.sha256_file(Path(item["path"])) != item["sha256"]
                    for item in audit_inputs
                ):
                    raise ValueError(
                        "WARC input changed after preflight; no SQL executed"
                    )
            if args.records_jsonl:
                spool.write_records(args.records_jsonl)
            vectors = None
            if args.embedding_command:
                records_path, vectors_path = root / "records.jsonl", root / "vectors.jsonl"
                spool.write_records(records_path, embedding_only=True)
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
                spool.add_vectors(vectors_path, args.vector_dim, args.embedding_normalization)
                vectors = spool
            elif args.embedding_jsonl:
                spool.add_vectors(args.embedding_jsonl, args.vector_dim, args.embedding_normalization)
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
                    requires_long_term_storage=spool.oversized_ascii_terms > 0,
                    reconcile_chunks=(
                        args.chunking_strategy == "char_window"
                        and bool(args.lance_uri)
                        and lance_mode == "outbox"
                    ),
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
                "charset_record_overrides": charset_policy.record_overrides,
                "charset_record_override_records": charset_policy.record_used,
                "charset_record_override_errors": "replace",
                "charset_record_override_replacement_characters": charset_policy.record_replacement_characters,
                "pdf_extraction": pdf_extractor.summary() if pdf_extractor else {"enabled": False, "attempted": 0, "extracted": 0, "output_bytes": 0},
                "docx_extraction": docx_extractor.summary() if docx_extractor else {"enabled": False, "attempted": 0, "extracted": 0, "output_bytes": 0},
                "extraction_retention": {"mode": retention.mode, "metadata_only": retention.metadata_only, "errors": retention.errors, "omitted_errors": retention.omitted_errors},
                "vector_rows_expected": (len(vectors) if vectors is not None else spool.db.execute("SELECT count(*) FROM records WHERE text_available=1").fetchone()[0] if args.lance_uri and not args.no_hash_vectors else 0),
                "records": spool.count,
                "source_documents": spool.db.execute(
                    "SELECT count(DISTINCT parent_doc_id) FROM records"
                ).fetchone()[0],
                "chunking": {
                    "strategy": args.chunking_strategy,
                    "size_chars": args.chunk_size_chars,
                    "overlap_chars": args.chunk_overlap_chars,
                    "version": args.chunking_version,
                    "offset_unit": "unicode_codepoints_in_normalized_extracted_text",
                },
                "sql_bytes": sql_path.stat().st_size,
                "vectors": vector_count, "lance_mode": lance_mode if args.lance_uri else "none",
                "lance_index_consistency": (
                    "none" if not args.lance_uri else (
                        "eventual_after_commit" if lance_mode == "outbox" else "external_immediate_non_atomic"
                    )
                ),
                "lance_append_duplicates": "possible" if args.lance_uri and lance_mode == "append" and vector_count != 0 else "none",
                "bm25_mode": args.bm25_mode, "execute": args.execute, "psql": args.psql,
                "oversized_ascii_term_records": spool.oversized_ascii_term_records,
                "oversized_ascii_terms": spool.oversized_ascii_terms,
                "max_oversized_ascii_term_bytes": spool.max_oversized_ascii_term_bytes,
                "required_extension_version": max(
                    ([LONG_TERM_STORAGE_MIN_VERSION] if spool.oversized_ascii_terms else [])
                    + ([OUTBOX_MIN_EXTENSION_VERSION] if args.lance_uri and (
                        lance_mode == "outbox" or (lance_mode == "upsert" and vector_count != 0)
                    ) else []),
                    key=lambda value: tuple(map(int, value.split("."))),
                    default=None,
                ),
                "collection": (
                    {
                        "collection_id": args.collection_id,
                        "version_id": args.collection_version,
                        "profile_sha256": args.collection_profile_sha256,
                    }
                    if args.collection_manifest
                    else None
                ),
            }
            if args.summary_json:
                args.summary_json.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
            if args.summary:
                print(format_summary(summary), file=sys.stderr)
            if args.collection_manifest:
                input_sha256 = None
                if audit_inputs:
                    encoded = json.dumps(
                        [item["sha256"] for item in audit_inputs], separators=(",", ":")
                    ).encode("ascii")
                    input_sha256 = hashlib.sha256(encoded).hexdigest()
                args.collection_import_result = {
                    "complete": bool(args.execute),
                    "execute": bool(args.execute),
                    "lance_mode": lance_mode,
                    "records": summary["source_documents"],
                    "vector_rows": vector_count,
                    "input_sha256": input_sha256,
                }
            return 0
        finally:
            spool.close()


def _bind_collection_profile(args: argparse.Namespace, raw: dict[str, Any]) -> None:
    collection_id, managed_path, descriptor = collection_manifest.import_profile(
        raw, args.collection_manifest, args.collection_version
    )
    embedding = descriptor["embedding"]
    chunking = descriptor["chunking"]
    supplied = {
        "--embedding-model": (args.embedding_model, embedding["model"]),
        "--embedding-model-version": (args.embedding_model_version, embedding["model_version"]),
        "--embedding-normalization": (args.embedding_normalization, embedding["normalization"]),
        "--normalization-version": (args.normalization_version, embedding["normalization_version"]),
        "--chunking-version": (args.chunking_version, chunking["version"]),
    }
    for flag, (actual, expected) in supplied.items():
        if actual is None:
            raise SystemExit(f"--collection-manifest requires {flag}")
        if actual != expected:
            raise SystemExit(f"{flag} does not match collection {collection_id!r} profile")
    for flag, actual, expected in (
        ("--chunking-strategy", args.chunking_strategy, chunking["strategy"]),
        ("--chunk-size-chars", args.chunk_size_chars, chunking["size_chars"]),
        ("--chunk-overlap-chars", args.chunk_overlap_chars, chunking["overlap_chars"]),
    ):
        if actual is not None and actual != expected:
            raise SystemExit(f"{flag} does not match collection {collection_id!r} profile")
    if args.vector_dim != embedding["dimensions"]:
        raise SystemExit(
            f"--vector-dim {args.vector_dim} does not match collection profile dimension {embedding['dimensions']}"
        )
    if args.lance_uri:
        requested = Path(args.lance_uri).expanduser().resolve(strict=False)
        if requested != managed_path:
            raise SystemExit("--lance-uri must match the selected managed collection dataset path")
    args.lance_uri = str(managed_path)
    if not (args.embedding_command or args.embedding_jsonl) or args.no_hash_vectors:
        raise SystemExit("collection imports require explicit --embedding-command or --embedding-jsonl vectors")

    version_id = args.collection_version or raw["active_version"]
    staged = version_id == raw["staged_version"]
    exists = managed_path.exists()
    if staged:
        if not (args.audit_manifest_json or args.checkpoint_db):
            raise SystemExit("staged reindex requires --audit-manifest-json or --checkpoint-db source checksums")
        if args.lance_mode not in {"create", "upsert"} or args.overwrite_lance:
            raise SystemExit("a staged version requires direct create/upsert writes; outbox is not ready for activation")
        if not exists and args.lance_mode != "create":
            raise SystemExit("prepare the staged Lance dataset before using upsert/outbox")
        if exists and args.lance_mode == "create":
            raise SystemExit("staged dataset already exists; use upsert/outbox to resume without recreating it")
    elif args.lance_mode not in {"upsert", "outbox"} or args.overwrite_lance:
        raise SystemExit("active collection imports require upsert or outbox; use a staged version for reindexing")
    if not staged and chunking["strategy"] == "char_window" and args.lance_mode != "outbox":
        raise SystemExit("active char_window collections require --lance-mode outbox for revisioned chunk reconciliation")

    args.collection_id = collection_id
    args.chunking_strategy = chunking["strategy"]
    args.chunk_size_chars = chunking["size_chars"]
    args.chunk_overlap_chars = chunking["overlap_chars"]
    args.chunking_version = chunking["version"]
    args.collection_profile_sha256 = descriptor["profile_sha256"]
    args.collection_version = version_id
    args.collection_is_staged = staged


def _run_cli(args: argparse.Namespace) -> int:
    if args.chunking_strategy is None:
        args.chunking_strategy = "whole_document"
    if args.chunk_overlap_chars is None:
        args.chunk_overlap_chars = 0
    if args.chunking_version is None:
        args.chunking_version = "whole-document-v1"
    if args.chunking_strategy == "whole_document":
        if args.chunk_size_chars is not None or args.chunk_overlap_chars != 0:
            raise SystemExit("whole_document chunking requires --chunk-size-chars omitted and overlap 0")
    else:
        if args.chunk_size_chars is None or args.chunk_size_chars <= 0:
            raise SystemExit("char_window chunking requires a positive --chunk-size-chars")
        if args.chunk_overlap_chars < 0 or args.chunk_overlap_chars >= args.chunk_size_chars:
            raise SystemExit("--chunk-overlap-chars must be non-negative and smaller than chunk size")
        if not args.chunking_version.strip():
            raise SystemExit("char_window chunking requires a non-empty --chunking-version")
        if args.checkpoint_db:
            raise SystemExit("char_window chunking is not supported by --checkpoint-db yet; no SQL executed")
    if args.embedding_timeout <= 0 or not math.isfinite(args.embedding_timeout):
        raise SystemExit("--embedding-timeout must be finite and positive")
    if args.max_record_bytes <= 0:
        raise SystemExit("--max-record-bytes must be positive")
    if args.pdf_timeout_seconds <= 0 or not math.isfinite(args.pdf_timeout_seconds):
        raise SystemExit("--pdf-timeout-seconds must be finite and positive")
    if args.vector_dim <= 0:
        raise SystemExit("--vector-dim must be positive")
    if args.batch_max_bytes <= 0:
        raise SystemExit("--batch-max-bytes must be positive")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if args.checkpoint_max_bytes <= 0:
        raise SystemExit("--checkpoint-max-bytes must be positive")
    if args.limit is not None and args.limit < 0:
        raise SystemExit("--limit must be non-negative")
    if args.min_text_chars < 0:
        raise SystemExit("--min-text-chars must be non-negative")
    if args.audit_manifest_json and args.limit is not None:
        raise SystemExit("--audit-manifest-json requires a full input scan without --limit")
    if args.embedding_command and args.embedding_jsonl:
        raise SystemExit("--embedding-command and --embedding-jsonl are mutually exclusive")
    if (args.embedding_command or args.embedding_jsonl) and not args.lance_uri:
        raise SystemExit("--embedding-command/--embedding-jsonl require --lance-uri")
    if args.lance_mode != "create" and not args.lance_uri:
        raise SystemExit("--lance-mode requires --lance-uri")
    lance_mode = args.lance_mode
    if args.overwrite_lance:
        if args.lance_mode in {"append", "upsert", "outbox"}:
            raise SystemExit("--overwrite-lance conflicts with --lance-mode append/upsert/outbox")
        lance_mode = "overwrite"

    if lance_mode in {"upsert", "outbox"} and args.batch_size > 10_000:
        raise SystemExit(f"{lance_mode} --batch-size must not exceed 10000")
    if args.chunking_strategy == "char_window" and args.lance_uri:
        staged_direct = bool(args.collection_manifest and getattr(args, "collection_is_staged", False))
        allowed = {"create", "upsert"} if staged_direct else {"create", "outbox"}
        if lance_mode not in allowed:
            raise SystemExit("char_window vector imports require create for a new dataset or revisioned outbox mode")
        if lance_mode == "outbox" and args.no_hash_vectors and not (args.embedding_command or args.embedding_jsonl):
            raise SystemExit("char_window outbox imports require supplied embeddings; no SQL executed")
    if args.checkpoint_db and args.lance_uri and (
        args.embedding_command or args.embedding_jsonl or not args.no_hash_vectors
    ):
        if lance_mode not in {"upsert", "outbox"}:
            raise SystemExit(
                "--checkpoint-db with vectors requires --lance-mode upsert or outbox; "
                "create/overwrite/append cannot safely resume"
            )
    if args.checkpoint_db and args.audit_manifest_json:
        raise SystemExit(
            "--audit-manifest-json is not supported with --checkpoint-db; checkpoint mode reports its own streaming accounting"
        )
    if args.checkpoint_max_bytes <= 0:
        raise SystemExit("--checkpoint-max-bytes must be positive")
    if args.checkpoint_max_memory_bytes <= 0:
        raise SystemExit("--checkpoint-max-memory-bytes must be positive")
    return run_spooled_import(args, lance_mode)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    manifest_options = (
        args.collection_version, args.embedding_model, args.embedding_model_version,
        args.embedding_normalization, args.normalization_version,
    )
    if args.collection_manifest:
        try:
            with collection_manifest.import_lock(args.collection_manifest) as raw:
                _bind_collection_profile(args, raw)
                result = _run_cli(args)
        except collection_manifest.ManifestError as exc:
            raise SystemExit(f"collection manifest: {exc}") from exc
        if result == 0 and args.collection_is_staged and args.execute:
            try:
                collection_manifest.record_ready(
                    args.collection_manifest,
                    args.collection_version,
                    args.collection_import_result,
                )
            except collection_manifest.ManifestError as exc:
                raise SystemExit(
                    f"collection import succeeded but readiness was not recorded: {exc}"
                ) from exc
        return result
    if any(value is not None for value in manifest_options):
        raise SystemExit("collection profile options require --collection-manifest")
    return _run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
