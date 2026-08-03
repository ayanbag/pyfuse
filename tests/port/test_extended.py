"""Extended query syntax: matchers, tokenizer, OR/AND grouping."""

from __future__ import annotations

import pytest

from conftest import assert_matches_oracle
from pyfuse import Fuse
from pyfuse.search.extended import ExtendedSearch
from pyfuse.search.extended.matchers import MATCHERS, is_inverse
from pyfuse.search.extended.parse_query import parse_query, tokenize

BOOKS = [
    {"title": "Old Man's War", "author": {"firstName": "John", "lastName": "Scalzi"}},
    {
        "title": "The Lock Artist",
        "author": {"firstName": "Steve", "lastName": "Hamilton"},
    },
    {"title": "HTML5", "author": {"firstName": "Remy", "lastName": "Sharp"}},
]

EXTENDED = {"keys": ["title"], "use_extended_search": True}


def search_in(pattern: str, text: str, **options):
    opts = {"use_extended_search": True, **options}
    return ExtendedSearch(pattern, opts).search_in(text)


class TestTokenizer:
    def test_splits_on_spaces(self):
        assert tokenize("^core go$") == ["^core", "go$"]

    def test_keeps_quoted_tokens_whole(self):
        assert tokenize('="hello world"') == ['="hello world"']

    def test_quoted_token_with_trailing_suffix_marker(self):
        assert tokenize('^"hello world"$') == ['^"hello world"$']

    def test_inner_quotes_survive(self):
        assert tokenize('="said "test""') == ['="said "test""']

    def test_mixed_quoted_and_bare(self):
        assert tokenize('="a b" plain') == ['="a b"', "plain"]

    def test_empty_and_blank(self):
        assert tokenize("") == []
        assert tokenize("   ") == []


class TestParseQuery:
    def test_or_groups(self):
        groups = parse_query("^core go$ | rb$")
        assert [[m.type for m in g] for g in groups] == [
            ["prefix-exact", "suffix-exact"],
            ["suffix-exact"],
        ]

    def test_escaped_pipe_is_literal(self):
        groups = parse_query(r"a\|b")
        assert len(groups) == 1

    def test_bare_operator_char_falls_through_to_fuzzy(self):
        # An empty capture is falsy in JS, so `'` alone must not build an
        # include matcher for "" — which would loop forever on `indexOf("")`.
        # Detection falls through to the fuzzy catch-all instead, which then
        # treats the apostrophe as an ordinary pattern. Confirmed against the
        # oracle: `search("'")` fuzzy-matches "old man's war".
        groups = parse_query("'")
        assert [[m.type for m in g] for g in groups] == [["fuzzy"]]

    def test_every_operator_is_recognised(self):
        cases = {
            "=exact": "exact",
            "'include": "include",
            "^prefix": "prefix-exact",
            "!^noprefix": "inverse-prefix-exact",
            "!nosuffix$": "inverse-suffix-exact",
            "suffix$": "suffix-exact",
            "!noexact": "inverse-exact",
            "fuzzy": "fuzzy",
        }
        for pattern, expected in cases.items():
            assert parse_query(pattern)[0][0].type == expected

    def test_fuzzy_is_last_so_it_cannot_shadow(self):
        assert MATCHERS[-1].type == "fuzzy"

    def test_detection_regexes_anchor_at_the_true_end(self):
        # Python's `$` also matches *before* a trailing newline, JS's does
        # not, so the ported regexes use `\Z`. Tested on the patterns
        # directly: `parse_query` trims its input, which would hide the
        # difference for a trailing newline.
        exact = next(d for d in MATCHERS if d.type == "exact")
        assert exact.single_regex.match("=term") is not None
        assert exact.single_regex.match("=term\n") is None

    def test_interior_newline_yields_no_matcher_at_all(self):
        # `.` excludes newlines in both languages, so no detection regex can
        # span one — not even the fuzzy catch-all. The term is dropped and
        # the group matches nothing. Confirmed against the oracle:
        # `search("=ex\nact")` returns [].
        assert parse_query("=ex\nact") == [[]]


class TestMatchers:
    def test_exact(self):
        assert search_in("=html5", "html5").is_match
        assert not search_in("=html5", "html5 extra").is_match

    def test_include(self):
        assert search_in("'lock", "the lock artist").is_match
        assert not search_in("'zzz", "the lock artist").is_match

    def test_prefix(self):
        assert search_in("^old", "old man's war").is_match
        assert not search_in("^man", "old man's war").is_match

    def test_suffix(self):
        assert search_in("war$", "old man's war").is_match
        assert not search_in("old$", "old man's war").is_match

    def test_inverse_exact(self):
        assert search_in("!zzz", "old man's war").is_match
        assert not search_in("!old", "old man's war").is_match

    def test_inverse_prefix(self):
        assert search_in("!^man", "old man's war").is_match
        assert not search_in("!^old", "old man's war").is_match

    def test_inverse_suffix(self):
        assert search_in("!old$", "old man's war").is_match
        assert not search_in("!war$", "old man's war").is_match

    def test_is_inverse_classifier(self):
        assert is_inverse("inverse-exact")
        assert not is_inverse("exact")

    def test_and_requires_every_term(self):
        assert search_in("^old war$", "old man's war").is_match
        assert not search_in("^old zzz$", "old man's war").is_match

    def test_or_takes_the_first_matching_group(self):
        assert search_in("^zzz | ^old", "old man's war").is_match

    def test_include_reports_every_occurrence(self):
        result = search_in("'a", "a b a", include_matches=True)
        assert result.indices == [(0, 0), (4, 4)]

    def test_inverse_sets_the_aggregation_flag(self):
        assert search_in("!zzz", "old man's war").has_inverse
        assert not search_in("^old", "old man's war").has_inverse


class TestExtendedSearchInFuse:
    def test_prefix_query(self):
        assert Fuse(BOOKS, EXTENDED).search("^old")[0].ref_index == 0

    def test_exact_query(self):
        assert Fuse(BOOKS, EXTENDED).search("=HTML5")[0].ref_index == 2

    def test_inverse_excludes(self):
        results = Fuse(BOOKS, EXTENDED).search("!HTML5")
        assert 2 not in {r.ref_index for r in results}

    def test_case_folding_applies_to_pattern_and_text(self):
        assert Fuse(BOOKS, EXTENDED).search("=html5")[0].ref_index == 2

    def test_empty_query_returns_all(self):
        assert len(Fuse(BOOKS, EXTENDED).search("")) == len(BOOKS)


class TestExtendedDifferential:
    QUERIES = [
        "^old",
        "=HTML5",
        "'jeeves",
        "code$",
        "!jeeves",
        "^the | code$",
        "!^the",
        "'the code",
        "^old war$",
        "!^html",
        "artist$",
        "=old man's war",
        "'o | 'a",
        "!zzz",
    ]

    OPTION_SETS = [
        {"keys": ["title"], "useExtendedSearch": True, "includeScore": True},
        {
            "keys": ["title", "author.firstName"],
            "useExtendedSearch": True,
            "includeScore": True,
            "includeMatches": True,
        },
        {
            "keys": ["title"],
            "useExtendedSearch": True,
            "includeScore": True,
            "ignoreLocation": True,
        },
        {
            "keys": ["title", "author.lastName"],
            "useExtendedSearch": True,
            "includeScore": True,
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
