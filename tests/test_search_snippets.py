from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import search_snippets
import warc_importer


class SearchSnippetTests(unittest.TestCase):
    def terms(self, query: str) -> list[str]:
        return warc_importer.tokenize_bm25(query)

    def test_span_tokenizer_matches_the_importer_and_extension_rules(self):
        text = "ABC abc_123 é中日語 カタカナ 한글 𠀀"
        self.assertEqual(
            [token for token, _, _ in search_snippets._token_spans(text)],
            self.terms(text),
        )

    def test_ascii_matches_use_token_boundaries_and_ascii_case_folding(self):
        text = "cartography artful ART art"
        result = search_snippets.build_snippet(text, self.terms("art"))
        self.assertEqual(
            result.matched_spans,
            (search_snippets.MatchedSpan(19, 22), search_snippets.MatchedSpan(23, 26)),
        )
        self.assertEqual(
            result.highlighted_html,
            "cartography artful <mark>ART</mark> <mark>art</mark>",
        )

    def test_cjk_bigram_matches_report_unicode_codepoint_offsets(self):
        text = "🙂前中日語後"
        result = search_snippets.build_snippet(text, self.terms("中日語"))
        self.assertEqual(
            result.matched_spans,
            (search_snippets.MatchedSpan(2, 4), search_snippets.MatchedSpan(3, 5)),
        )
        self.assertEqual(result.highlighted_html, "🙂前<mark>中日語</mark>後")

    def test_excerpt_is_bounded_and_centered_on_the_first_match(self):
        text = "prefix " + ("x" * 600) + " needle " + ("y" * 600)
        result = search_snippets.build_snippet(text, self.terms("needle"), max_chars=80)
        self.assertEqual(len(result.text), 80)
        self.assertIn("needle", result.text)
        self.assertGreater(result.source_start, 0)
        self.assertEqual(result.source_end - result.source_start, 80)
        self.assertEqual(result.matched_spans, (search_snippets.MatchedSpan(608, 614),))

    def test_html_and_script_text_are_escaped_before_highlight_markup(self):
        text = '<script>alert("x&y")</script> needle'
        result = search_snippets.build_snippet(text, self.terms("needle"))
        self.assertIn("&lt;script&gt;", result.highlighted_html)
        self.assertIn("&quot;x&amp;y&quot;", result.highlighted_html)
        self.assertIn("&lt;/script&gt;", result.highlighted_html)
        self.assertIn("<mark>needle</mark>", result.highlighted_html)
        self.assertNotIn("<script>", result.highlighted_html)

    def test_no_match_returns_a_plain_bounded_excerpt(self):
        result = search_snippets.build_snippet("첫 문장, 다음 문장", self.terms("없음"), max_chars=4)
        self.assertEqual(result.text, "첫 문장")
        self.assertEqual(result.matched_spans, ())
        self.assertEqual(result.highlighted_html, "첫 문장")
        self.assertEqual((result.source_start, result.source_end), (0, 4))

    def test_visible_spans_can_be_translated_to_parent_document_offsets(self):
        result = search_snippets.build_snippet("앞🙂target 뒤", self.terms("target"))
        parent_char_start = 480
        absolute_spans = tuple(
            (parent_char_start + span.start, parent_char_start + span.end)
            for span in result.matched_spans
        )
        self.assertEqual(absolute_spans, ((482, 488),))

    def test_length_and_input_limits_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 2000"):
            search_snippets.build_snippet("text", [], max_chars=2_001)
        with self.assertRaisesRegex(TypeError, "integer"):
            search_snippets.build_snippet("text", [], max_chars=True)
        with self.assertRaisesRegex(ValueError, "scan limit"):
            search_snippets.build_snippet("x" * (search_snippets.MAX_SOURCE_CHARS + 1), [])
        result = search_snippets.build_snippet("x" * 2_100, [], max_chars=2_000)
        self.assertEqual(len(result.text), 2_000)

    def test_term_input_is_bounded_and_rejects_a_scalar_string(self):
        with self.assertRaisesRegex(TypeError, "sequence"):
            search_snippets.build_snippet("text", "text")
        terms = [f"term{index}" for index in range(search_snippets.MAX_QUERY_TERMS + 1)]
        with self.assertRaisesRegex(ValueError, "distinct query terms"):
            search_snippets.build_snippet("text", terms)

    def test_a_matching_token_must_fit_the_requested_excerpt(self):
        with self.assertRaisesRegex(ValueError, "shorter than the first matching token"):
            search_snippets.build_snippet("abcdefghij", ["abcdefghij"], max_chars=5)


if __name__ == "__main__":
    unittest.main()
