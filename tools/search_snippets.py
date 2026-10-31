"""Build bounded, HTML-safe snippets from a selected WARC chunk in a client."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Iterable, Iterator

MAX_SNIPPET_CHARS = 2_000
MAX_SOURCE_CHARS = 1_000_000
MAX_QUERY_TERMS = 256

_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


@dataclass(frozen=True, order=True)
class MatchedSpan:
    """A zero-based, half-open Unicode codepoint range in the source chunk."""

    start: int
    end: int


@dataclass(frozen=True)
class SearchSnippet:
    """A bounded source excerpt and matches, all located in the source chunk."""

    text: str
    source_start: int
    source_end: int
    matched_spans: tuple[MatchedSpan, ...]
    highlighted_html: str


def _is_cjk(char: str) -> bool:
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


def _token_spans(text: str) -> Iterator[tuple[str, int, int]]:
    """Mirror the extension's CJK-bigram and ASCII token boundaries."""
    index = 0
    while index < len(text):
        char = text[index]
        if _is_cjk(char):
            start = index
            while index < len(text) and _is_cjk(text[index]):
                index += 1
            if index - start == 1:
                yield text[start:index], start, index
            else:
                for token_start in range(start, index - 1):
                    yield text[token_start : token_start + 2], token_start, token_start + 2
        elif char.isascii() and char.isalnum():
            start = index
            while index < len(text) and text[index].isascii() and text[index].isalnum():
                index += 1
            if index - start >= 2:
                yield text[start:index].translate(_ASCII_LOWER), start, index
        else:
            index += 1


def _validate_terms(terms: Iterable[str]) -> frozenset[str]:
    if isinstance(terms, (str, bytes)):
        raise TypeError("terms must be a sequence returned by tokenize_korean()")
    normalized: set[str] = set()
    for term in terms:
        if not isinstance(term, str):
            raise TypeError("every query term must be a string")
        if term:
            normalized.add(term.translate(_ASCII_LOWER))
            if len(normalized) > MAX_QUERY_TERMS:
                raise ValueError(f"at most {MAX_QUERY_TERMS} distinct query terms are supported")
    return frozenset(normalized)


def _excerpt_bounds(text_len: int, first_match: MatchedSpan | None, limit: int) -> tuple[int, int]:
    if text_len <= limit:
        return 0, text_len
    if first_match is None:
        return 0, limit
    if first_match.end - first_match.start > limit:
        raise ValueError("max_chars is shorter than the first matching token")
    start = max(0, first_match.start - limit // 3)
    start = min(start, text_len - limit)
    return start, start + limit


def _highlight_html(text: str, excerpt_start: int, spans: tuple[MatchedSpan, ...]) -> str:
    visible = [
        (max(span.start, excerpt_start) - excerpt_start,
         min(span.end, excerpt_start + len(text)) - excerpt_start)
        for span in spans
        if span.end > excerpt_start and span.start < excerpt_start + len(text)
    ]
    merged: list[tuple[int, int]] = []
    for start, end in visible:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        parts.append(html.escape(text[cursor:start], quote=True))
        parts.append("<mark>")
        parts.append(html.escape(text[start:end], quote=True))
        parts.append("</mark>")
        cursor = end
    parts.append(html.escape(text[cursor:], quote=True))
    return "".join(parts)


def build_snippet(
    content: str,
    terms: Iterable[str],
    *,
    max_chars: int = 240,
) -> SearchSnippet:
    """Return a bounded excerpt and safe highlight for a selected source chunk.

    ``terms`` should be the result of PostgreSQL ``tokenize_korean(query)``.
    Offsets count Python Unicode codepoints, matching the importer's chunk offsets.
    A source larger than the hard scan bound fails explicitly instead of returning
    a misleading excerpt from only its prefix.
    """
    if not isinstance(content, str):
        raise TypeError("content must be a string")
    if len(content) > MAX_SOURCE_CHARS:
        raise ValueError(f"content exceeds the {MAX_SOURCE_CHARS}-character scan limit")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise TypeError("max_chars must be an integer")
    if not 1 <= max_chars <= MAX_SNIPPET_CHARS:
        raise ValueError(f"max_chars must be between 1 and {MAX_SNIPPET_CHARS}")

    wanted = _validate_terms(terms)
    first_match: MatchedSpan | None = None
    source_spans: list[MatchedSpan] = []
    excerpt_start = 0
    excerpt_end = min(len(content), max_chars)

    if wanted:
        for token, start, end in _token_spans(content):
            if token not in wanted:
                continue
            span = MatchedSpan(start, end)
            if first_match is None:
                first_match = span
                excerpt_start, excerpt_end = _excerpt_bounds(len(content), span, max_chars)
            if span.start >= excerpt_start and span.end <= excerpt_end:
                source_spans.append(span)

    excerpt = content[excerpt_start:excerpt_end]
    spans = tuple(source_spans)
    return SearchSnippet(
        text=excerpt,
        source_start=excerpt_start,
        source_end=excerpt_end,
        matched_spans=spans,
        highlighted_html=_highlight_html(excerpt, excerpt_start, spans),
    )
