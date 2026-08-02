"""Token search: analyzer, inverted index, IDF scoring, AND coverage."""

from __future__ import annotations

import re

import pytest

from conftest import assert_matches_oracle
from fusejs import Fuse
from fusejs.search.token import MAX_MASK_TERMS, TokenSearch
from fusejs.search.token.analyzer import Analyzer, default_tokenize
from fusejs.search.token.inverted_index import (
    InvertedIndexData,
    build_inverted_index,
    remove_and_shift_inverted_index,
    remove_from_inverted_index,
)
from fusejs.tools import create_index

BOOKS = [
    {"title": "Old Man's War", "author": {"firstName": "John", "lastName": "Scalzi"}},
    {
        "title": "The Lock Artist",
        "author": {"firstName": "Steve", "lastName": "Hamilton"},
    },
    {"title": "The Old Wars", "author": {"firstName": "Remy", "lastName": "Sharp"}},
]

TOKEN = {"keys": ["title"], "use_token_search": True}


class TestDefaultTokenizer:
    def test_splits_on_non_word_characters(self):
        assert default_tokenize("Hello, world_2!") == ["Hello", "world_2"]

    def test_keeps_combining_marks_attached(self):
        # \p{M} is in the class on purpose: without it, NFD-normalised Latin
        # and Devanagari shatter into fragments.
        assert default_tokenize("café") == ["café"]
        decomposed = "café"
        assert default_tokenize(decomposed) == [decomposed]

    def test_handles_non_latin_scripts(self):
        assert default_tokenize("привет мир") == ["привет", "мир"]
        assert default_tokenize("日本語") == ["日本語"]

    def test_numbers_and_underscores_are_word_characters(self):
        assert default_tokenize("a_1 b-2") == ["a_1", "b", "2"]

    def test_empty_and_punctuation_only(self):
        assert default_tokenize("") == []
        assert default_tokenize("!!! ???") == []


class TestAnalyzer:
    def test_lowercases_by_default(self):
        assert Analyzer().tokenize("Old WAR") == ["old", "war"]

    def test_case_sensitive_mode(self):
        assert Analyzer(is_case_sensitive=True).tokenize("Old WAR") == ["Old", "WAR"]

    def test_strips_diacritics_when_asked(self):
        assert Analyzer(ignore_diacritics=True).tokenize("café") == ["cafe"]

    def test_accepts_a_compiled_pattern(self):
        analyzer = Analyzer(tokenize=re.compile(r"[a-z]+"))
        assert analyzer.tokenize("ab12cd") == ["ab", "cd"]

    def test_accepts_a_callable(self):
        analyzer = Analyzer(tokenize=lambda text: text.split("|"))
        assert analyzer.tokenize("a|b") == ["a", "b"]

    def test_rejects_a_callable_returning_junk(self):
        analyzer = Analyzer(tokenize=lambda _text: "not a list")
        with pytest.raises(TypeError, match="must return list"):
            analyzer.tokenize("x")

    def test_rejects_a_junk_tokenize_option(self):
        with pytest.raises(TypeError, match="must be a compiled pattern"):
            Analyzer(tokenize=42)


class TestInvertedIndex:
    @staticmethod
    def _build(docs, keys=("title",)):
        index = create_index(list(keys), docs)
        return build_inverted_index(index.records, len(index.keys), Analyzer())

    def test_counts_fields_and_document_frequency(self):
        data = self._build(BOOKS)
        assert data.field_count == 3
        # "the" appears in two titles, "old" in two.
        assert data.df["the"] == 2
        assert data.df["old"] == 2
        assert data.df["artist"] == 1

    def test_repeated_terms_in_one_field_count_once(self):
        data = self._build([{"title": "war war war"}])
        assert data.df["war"] == 1

    def test_array_elements_each_count_as_a_field(self):
        data = self._build([{"tags": ["alpha", "beta"]}], keys=("tags",))
        assert data.field_count == 2

    def test_removal_subtracts_exactly_what_was_added(self):
        data = self._build(BOOKS)
        before = dict(data.df)
        remove_from_inverted_index(data, 1)
        assert data.field_count == 2
        assert data.df.get("artist") is None
        assert data.df["old"] == before["old"]

    def test_removal_of_unknown_document_is_a_noop(self):
        data = self._build(BOOKS)
        remove_from_inverted_index(data, 99)
        assert data.field_count == 3

    def test_remove_and_shift_renumbers_survivors(self):
        data = self._build(BOOKS)
        remove_and_shift_inverted_index(data, [0])
        # Documents 1 and 2 become 0 and 1.
        assert sorted(data.doc_field_count) == [0, 1]

    def test_remove_and_shift_with_empty_list_is_a_noop(self):
        data = self._build(BOOKS)
        remove_and_shift_inverted_index(data, [])
        assert data.field_count == 3

    def test_empty_index_has_no_statistics(self):
        data = InvertedIndexData()
        assert data.field_count == 0
        assert data.df == {}


class TestTokenSearchScoring:
    def test_rare_terms_outweigh_common_ones(self):
        fuse = Fuse(BOOKS, {**TOKEN, "include_score": True})
        # "artist" is rare (1 doc), "the" is common (2 docs).
        rare = fuse.search("artist")
        common = fuse.search("the")
        assert rare[0].score < common[0].score

    def test_matches_any_term_by_default(self):
        results = Fuse(BOOKS, TOKEN).search("old artist")
        assert len(results) >= 2

    def test_token_match_all_requires_every_term(self):
        loose = Fuse(BOOKS, TOKEN).search("old artist")
        strict = Fuse(BOOKS, {**TOKEN, "token_match": "all"}).search("old artist")
        assert len(strict) < len(loose)

    def test_token_match_all_still_finds_full_coverage(self):
        options = {**TOKEN, "token_match": "all"}
        results = Fuse(BOOKS, options).search("old war")
        assert {r.ref_index for r in results} == {0, 2}

    def test_order_of_terms_does_not_matter(self):
        fuse = Fuse(BOOKS, {**TOKEN, "include_score": True})
        assert [r.ref_index for r in fuse.search("old war")] == [
            r.ref_index for r in fuse.search("war old")
        ]

    def test_empty_query_returns_everything(self):
        assert len(Fuse(BOOKS, TOKEN).search("")) == len(BOOKS)

    def test_searcher_without_terms_matches_nothing(self):
        searcher = TokenSearch("!!!", {"use_token_search": True})
        assert not searcher.search_in("anything").is_match

    def test_mask_threshold_is_the_js_signed_bit_limit(self):
        # Python ints have no 32-bit limit, but the threshold must stay put:
        # crossing it selects the set-based branch, and both branches have to
        # agree with the original.
        assert MAX_MASK_TERMS == 31

    def test_many_term_query_uses_the_set_fallback(self):
        query = " ".join(f"w{i}" for i in range(MAX_MASK_TERMS + 5))
        docs = [{"title": query}]
        options = {"keys": ["title"], "use_token_search": True, "token_match": "all"}
        assert len(Fuse(docs, options).search(query)) == 1


class TestTokenSearchMutation:
    def test_add_updates_corpus_statistics(self):
        fuse = Fuse(list(BOOKS), {**TOKEN, "include_score": True})
        before = fuse.search("artist")[0].score
        fuse.add({"title": "Another Artist Book"})
        after = fuse.search("artist")[0].score
        # "artist" is now less rare, so it discriminates less.
        assert after != before

    def test_remove_at_updates_corpus_statistics(self):
        fuse = Fuse(list(BOOKS), {**TOKEN, "include_score": True})
        exact = fuse.search("artist")[0]
        assert exact.ref_index == 1

        fuse.remove_at(1)

        # The document holding "artist" is gone, so `df` no longer has it and
        # the term stops discriminating. Bitap still fuzzy-matches a
        # remaining title loosely — the oracle agrees, to the bit — so the
        # assertion is on the score collapsing, not on an empty result.
        remaining = fuse.search("artist")
        assert all(r.item["title"] != "The Lock Artist" for r in remaining)
        assert all(r.score > exact.score for r in remaining)


class TestTokenDifferential:
    QUERIES = ["old war", "artist", "the", "old", "man war", "lock artist", "zzz"]

    OPTION_SETS = [
        {"keys": ["title"], "useTokenSearch": True, "includeScore": True},
        {
            "keys": ["title", "author.firstName"],
            "useTokenSearch": True,
            "includeScore": True,
        },
        {
            "keys": ["title"],
            "useTokenSearch": True,
            "includeScore": True,
            "tokenMatch": "all",
        },
        {
            "keys": ["title", "author.lastName"],
            "useTokenSearch": True,
            "includeScore": True,
            "tokenMatch": "all",
        },
        {
            "keys": ["title"],
            "useTokenSearch": True,
            "includeScore": True,
            "includeMatches": True,
        },
    ]

    @pytest.mark.differential
    @pytest.mark.parametrize("options", OPTION_SETS)
    def test_matches_oracle(self, oracle, books, options):
        for query in self.QUERIES:
            assert_matches_oracle(
                Fuse(books, options).search(query),
                oracle.search(books, query, options, None),
            )
