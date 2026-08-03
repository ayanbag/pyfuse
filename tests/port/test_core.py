"""The Fuse engine: options, search modes, mutation, scoring, formatting."""

from __future__ import annotations

import pytest

from conftest import assert_matches_oracle
from pyfuse import Fuse, FuseOptions, create_index
from pyfuse.config import default_sort_fn
from pyfuse.core.compute_score import compute_score_single
from pyfuse.core.format_matches import format_matches
from pyfuse.core.query_parser import ParsedLeaf, ParsedOperator, parse
from pyfuse.errors import (
    FeatureUnavailableError,
    InvalidDocIndexError,
    InvalidIndexTypeError,
    InvalidQueryError,
)
from pyfuse.types import InternalResult, KeyObject, MatchScore

BOOKS = [
    {"title": "Old Man's War", "author": {"firstName": "John", "lastName": "Scalzi"}},
    {
        "title": "The Lock Artist",
        "author": {"firstName": "Steve", "lastName": "Hamilton"},
    },
    {"title": "HTML5", "author": {"firstName": "Remy", "lastName": "Sharp"}},
]


class TestOptionDefaults:
    """Pinned to fuse.js v7.5.0. Drift here silently changes every score."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("is_case_sensitive", False),
            ("ignore_diacritics", False),
            ("include_score", False),
            ("should_sort", True),
            ("include_matches", False),
            ("find_all_matches", False),
            ("min_match_char_length", 1),
            ("location", 0),
            ("threshold", 0.6),
            ("distance", 100),
            ("use_extended_search", False),
            ("use_token_search", False),
            ("token_match", "any"),
            ("ignore_location", False),
            ("ignore_field_norm", False),
            ("field_norm_weight", 1.0),
            ("strict_js_pow", False),
        ],
    )
    def test_default(self, name, expected):
        assert getattr(FuseOptions(), name) == expected

    def test_keys_defaults_to_empty_and_is_not_shared(self):
        first, second = FuseOptions(), FuseOptions()
        first.keys.append("title")
        assert second.keys == []

    def test_accepts_camel_case_from_json_config(self):
        options = FuseOptions.from_mapping(
            {"includeScore": True, "minMatchCharLength": 3, "useExtendedSearch": True}
        )
        assert options.include_score
        assert options.min_match_char_length == 3
        assert options.use_extended_search

    def test_rejects_unknown_option(self):
        with pytest.raises(TypeError, match="Unknown search option"):
            FuseOptions.from_mapping({"notAnOption": 1})

    def test_rejects_bad_token_match(self):
        with pytest.raises(ValueError, match="token_match must be"):
            FuseOptions(token_match="sometimes")

    def test_coerce_rejects_junk(self):
        with pytest.raises(TypeError, match="must be a FuseOptions"):
            FuseOptions.coerce(42)


class TestDefaultsAgainstOracle:
    @pytest.mark.differential
    def test_unconfigured_search_matches_oracle(self, oracle, books):
        """Nothing but `keys` set — every default is exercised at once."""
        options = {"keys": ["title"], "includeScore": True, "includeMatches": True}
        assert_matches_oracle(
            Fuse(books, options).search("old"),
            oracle.search(books, "old", options, None),
        )


class TestComputeScore:
    def test_unweighted_match_uses_raw_score(self):
        match = MatchScore(score=0.25, value="x", norm=1.0)
        assert compute_score_single([match]) == 0.25

    def test_perfect_score_on_weighted_key_uses_epsilon_not_zero(self):
        # Without the substitution one exact hit would zero the whole product
        # and flatten every distinction between documents.
        key = KeyObject(path=["t"], id="t", weight=0.5, src="t")
        match = MatchScore(score=0.0, value="x", norm=1.0, key=key)
        assert compute_score_single([match]) > 0.0

    def test_perfect_score_without_key_stays_zero(self):
        match = MatchScore(score=0.0, value="x", norm=1.0)
        assert compute_score_single([match]) == 0.0

    def test_field_norm_can_be_ignored(self):
        match = MatchScore(score=0.5, value="x", norm=0.5)
        assert compute_score_single([match]) != compute_score_single(
            [match], ignore_field_norm=True
        )

    def test_strict_js_pow_changes_nothing_structural(self):
        match = MatchScore(score=0.5, value="x", norm=0.577)
        native = compute_score_single([match])
        strict = compute_score_single([match], strict_js_pow=True)
        assert abs(native - strict) < 1e-15


class TestSortComparator:
    def test_orders_by_score_then_index(self):
        a = InternalResult(idx=1, item=None, score=0.5)
        b = InternalResult(idx=2, item=None, score=0.9)
        assert default_sort_fn(a, b) < 0
        assert default_sort_fn(b, a) > 0

    def test_ties_break_by_index_and_never_return_zero(self):
        a = InternalResult(idx=1, item=None, score=0.5)
        b = InternalResult(idx=2, item=None, score=0.5)
        assert default_sort_fn(a, b) < 0
        assert default_sort_fn(b, a) > 0

    def test_unscored_results_fail_loudly(self):
        a = InternalResult(idx=1, item=None, score=None)
        b = InternalResult(idx=2, item=None, score=0.5)
        with pytest.raises(ValueError, match="must be scored"):
            default_sort_fn(a, b)


class TestFormatMatches:
    def test_drops_matches_without_indices(self):
        result = InternalResult(
            idx=0, item=None, matches=[MatchScore(score=0.1, value="x", norm=1.0)]
        )
        assert format_matches(result) == []

    def test_reports_key_id_and_array_position(self):
        key = KeyObject(path=["a", "b"], id="a.b", weight=1, src="a.b")
        result = InternalResult(
            idx=0,
            item=None,
            matches=[
                MatchScore(
                    score=0.1, value="x", norm=1.0, key=key, idx=2, indices=[(0, 0)]
                )
            ],
        )
        formatted = format_matches(result)[0]
        assert formatted.key == "a.b"
        assert formatted.ref_index == 2

    def test_omits_ref_index_for_non_array_values(self):
        result = InternalResult(
            idx=0,
            item=None,
            matches=[MatchScore(score=0.1, value="x", norm=1.0, indices=[(0, 0)])],
        )
        assert format_matches(result)[0].ref_index is None


class TestQueryParser:
    def test_multi_key_object_is_implicit_and(self):
        node = parse({"title": "a", "author": "b"}, auto=False)
        assert isinstance(node, ParsedOperator)
        assert node.operator == "$and"
        assert [c.key_id for c in node.children] == ["title", "author"]

    def test_nested_operators(self):
        node = parse(
            {"$and": [{"$or": [{"a": "1"}, {"b": "2"}]}, {"c": "3"}]}, auto=False
        )
        assert node.operator == "$and"
        assert node.children[0].operator == "$or"
        assert node.children[1].key_id == "c"

    def test_bare_string_leaf_is_keyless(self):
        node = parse({"$or": [{"a": "1"}, "bare"]}, auto=False)
        assert node.children[1].key_id is None
        assert node.children[1].pattern == "bare"

    def test_path_form_resolves_to_dotted_id(self):
        node = parse({"$and": [{"$path": ["a", "b"], "$val": "v"}]}, auto=False)
        assert node.children[0].key_id == "a.b"

    def test_top_level_path_form_is_an_error(self):
        # Confirmed against the oracle: $path/$val only works nested.
        with pytest.raises(InvalidQueryError, match="Invalid value for key a,b"):
            parse({"$path": ["a", "b"], "$val": "v"}, auto=False)

    def test_non_string_pattern_is_an_error(self):
        with pytest.raises(InvalidQueryError, match="Invalid value for key a"):
            parse({"$and": [{"a": 1}]}, auto=False)

    def test_bare_string_decomposes_per_character(self):
        # Faithful to the original, odd as it is: Object.keys("abc") is
        # ["0","1","2"], so the string becomes one leaf per character.
        node = parse("abc", auto=False)
        assert [c.key_id for c in node.children] == ["0", "1", "2"]
        assert [c.pattern for c in node.children] == ["a", "b", "c"]

    def test_empty_operator_list(self):
        node = parse({"$or": []}, auto=False)
        assert node.operator == "$or"
        assert node.children == []

    def test_auto_attaches_searchers(self):
        node = parse({"title": "a"}, FuseOptions())
        assert isinstance(node.children[0], ParsedLeaf)
        assert node.children[0].searcher is not None


class TestSearch:
    def test_finds_by_title(self):
        results = Fuse(BOOKS, {"keys": ["title"]}).search("old man")
        assert results[0].item["title"] == "Old Man's War"

    def test_empty_query_returns_everything_unscored(self):
        results = Fuse(BOOKS, {"keys": ["title"], "include_score": True}).search("")
        assert len(results) == len(BOOKS)
        assert all(r.score is None for r in results)
        assert [r.ref_index for r in results] == [0, 1, 2]

    def test_whitespace_query_is_treated_as_empty(self):
        assert len(Fuse(BOOKS, {"keys": ["title"]}).search("   ")) == len(BOOKS)

    def test_empty_query_respects_limit(self):
        assert len(Fuse(BOOKS, {"keys": ["title"]}).search("", limit=2)) == 2

    def test_limit_equals_unlimited_then_sliced(self):
        options = {"keys": ["title", "author.firstName"], "include_score": True}
        fuse = Fuse(BOOKS, options)
        assert [r.ref_index for r in fuse.search("a", limit=2)] == [
            r.ref_index for r in fuse.search("a")
        ][:2]

    def test_limit_holds_with_sorting_disabled(self):
        options = {"keys": ["title"], "should_sort": False}
        fuse = Fuse(BOOKS, options)
        assert [r.ref_index for r in fuse.search("a", limit=2)] == [
            r.ref_index for r in fuse.search("a")
        ][:2]

    def test_searches_plain_string_collections(self):
        results = Fuse(["Old Man's War", "HTML5"], {}).search("old")
        assert results[0].item == "Old Man's War"

    def test_include_score_and_matches(self):
        options = {"keys": ["title"], "include_score": True, "include_matches": True}
        result = Fuse(BOOKS, options).search("old")[0]
        assert result.score is not None
        assert result.matches[0].key == "title"

    def test_empty_collection_returns_nothing(self):
        assert Fuse([], {"keys": ["title"]}).search("x") == []

    def test_custom_sort_fn_is_respected(self):
        # Reverse the default: worst first.
        def reverse(a, b):
            return 1 if a.score < b.score else -1

        options = {"keys": ["title"], "include_score": True, "sort_fn": reverse}
        scores = [r.score for r in Fuse(BOOKS, options).search("a")]
        assert scores == sorted(scores, reverse=True)


class TestLogicalSearch:
    def test_and_requires_both(self):
        options = {"keys": ["title", "author.firstName"]}
        fuse = Fuse(BOOKS, options)
        assert (
            len(fuse.search({"$and": [{"title": "old"}, {"author.firstName": "john"}]}))
            == 1
        )
        assert (
            fuse.search({"$and": [{"title": "old"}, {"author.firstName": "steve"}]})
            == []
        )

    def test_or_takes_either(self):
        # Fuzzy matching is loose, so "The Lock Artist" also scores against
        # these patterns; the oracle returns [2, 0, 1] here too. What the
        # assertion pins is that both branches contribute and the exact hits
        # outrank the incidental one.
        options = {"keys": ["title"]}
        results = Fuse(BOOKS, options).search(
            {"$or": [{"title": "old"}, {"title": "html"}]}
        )
        assert [r.ref_index for r in results] == [2, 0, 1]


class TestMutation:
    def test_add_appends_and_is_searchable(self):
        fuse = Fuse(list(BOOKS), {"keys": ["title"]})
        fuse.add({"title": "Brand New Book"})
        assert fuse.search("brand new")[0].item["title"] == "Brand New Book"

    def test_add_does_not_mutate_the_callers_list(self):
        docs = list(BOOKS)
        Fuse(docs, {"keys": ["title"]}).add({"title": "X"})
        assert len(docs) == len(BOOKS)

    def test_add_ignores_none(self):
        fuse = Fuse(list(BOOKS), {"keys": ["title"]})
        fuse.add(None)
        assert fuse.get_index().size() == len(BOOKS)

    def test_remove_by_predicate(self):
        fuse = Fuse(list(BOOKS), {"keys": ["title"]})
        removed = fuse.remove(lambda doc, _i: doc["title"] == "HTML5")
        assert [d["title"] for d in removed] == ["HTML5"]
        # The exact hit is gone; only the loose fuzzy match remains, and its
        # refIndex has been renumbered down.
        assert [r.item["title"] for r in fuse.search("html")] == ["The Lock Artist"]

    def test_remove_without_predicate_removes_nothing(self):
        fuse = Fuse(list(BOOKS), {"keys": ["title"]})
        assert fuse.remove() == []
        assert fuse.get_index().size() == len(BOOKS)

    def test_remove_at_returns_the_document(self):
        fuse = Fuse(list(BOOKS), {"keys": ["title"]})
        assert fuse.remove_at(0)["title"] == "Old Man's War"
        assert fuse.search("old man") == []

    @pytest.mark.parametrize("bad", [-1, 99, 1.5, True])
    def test_remove_at_validates_before_mutating(self, bad):
        fuse = Fuse(list(BOOKS), {"keys": ["title"]})
        with pytest.raises(InvalidDocIndexError):
            fuse.remove_at(bad)
        # Atomic: nothing was removed on the failed call.
        assert fuse.get_index().size() == len(BOOKS)

    def test_set_collection_replaces_everything(self):
        fuse = Fuse(list(BOOKS), {"keys": ["title"]})
        fuse.set_collection([{"title": "Only This"}])
        assert len(fuse.search("only")) == 1

    def test_prebuilt_index_is_accepted(self):
        index = create_index(["title"], BOOKS)
        fuse = Fuse(BOOKS, {"keys": ["title"]}, index)
        assert fuse.search("old man")[0].ref_index == 0

    def test_wrong_index_type_is_rejected(self):
        with pytest.raises(InvalidIndexTypeError):
            Fuse(BOOKS, {"keys": ["title"]}, index={"not": "an index"})


class TestMatchStatic:
    def test_matches_one_string(self):
        result = Fuse.match("old", "Old Man's War")
        assert result.is_match

    def test_rejects_token_search(self):
        with pytest.raises(FeatureUnavailableError, match="does not support"):
            Fuse.match("old", "Old Man's War", {"use_token_search": True})


class TestSearchDifferential:
    """Structure compared exactly; scores to within a few ULP."""

    QUERIES = ["old", "old man", "jeeves", "code", "artist", "the", "xyz", "war"]

    OPTION_SETS = [
        {"keys": ["title"], "includeScore": True},
        {"keys": ["title", "author.firstName"], "includeScore": True},
        {
            "keys": ["title", "author.firstName"],
            "includeScore": True,
            "includeMatches": True,
        },
        {
            "keys": [
                {"name": "title", "weight": 2},
                {"name": "author.lastName", "weight": 1},
            ],
            "includeScore": True,
        },
        {"keys": ["title"], "includeScore": True, "threshold": 0.3},
        {"keys": ["title"], "includeScore": True, "ignoreLocation": True},
        {"keys": ["title"], "includeScore": True, "ignoreFieldNorm": True},
        {"keys": ["title"], "includeScore": True, "shouldSort": False},
        {"keys": ["title"], "includeScore": True, "fieldNormWeight": 2},
        {"keys": ["tags"], "includeScore": True, "includeMatches": True},
        {
            "keys": ["title"],
            "includeScore": True,
            "minMatchCharLength": 3,
            "includeMatches": True,
        },
        {"keys": ["title"], "includeScore": True, "isCaseSensitive": True},
    ]

    @pytest.mark.differential
    @pytest.mark.parametrize("options", OPTION_SETS)
    def test_object_search(self, oracle, books, options):
        for query in self.QUERIES:
            assert_matches_oracle(
                Fuse(books, options).search(query),
                oracle.search(books, query, options, None),
            )

    @pytest.mark.differential
    @pytest.mark.parametrize("limit", [1, 2, 5])
    def test_limited_search(self, oracle, books, limit):
        options = {"keys": ["title", "author.firstName"], "includeScore": True}
        for query in self.QUERIES:
            assert_matches_oracle(
                Fuse(books, options).search(query, limit=limit),
                oracle.search(books, query, options, {"limit": limit}),
            )

    @pytest.mark.differential
    def test_string_collection(self, oracle):
        docs = ["Old Man's War", "The Lock Artist", "HTML5", "Right Ho Jeeves", ""]
        for options in (
            {"includeScore": True},
            {"includeScore": True, "includeMatches": True},
        ):
            for query in self.QUERIES:
                assert_matches_oracle(
                    Fuse(docs, options).search(query),
                    oracle.search(docs, query, options, None),
                )

    @pytest.mark.differential
    @pytest.mark.parametrize(
        "query",
        [
            {"$and": [{"title": "old"}, {"author.firstName": "john"}]},
            {"$or": [{"title": "old"}, {"title": "jeeves"}]},
            {
                "$and": [
                    {"$or": [{"title": "old"}, {"title": "code"}]},
                    {"author.lastName": "s"},
                ]
            },
            {"title": "old", "author.firstName": "john"},
            {"$or": [{"title": "old"}, "jeeves"]},
        ],
    )
    def test_logical_search(self, oracle, books, query):
        options = {
            "keys": ["title", "author.firstName", "author.lastName"],
            "includeScore": True,
        }
        assert_matches_oracle(
            Fuse(books, options).search(query),
            oracle.search(books, query, options, None),
        )
