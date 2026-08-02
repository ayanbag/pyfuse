"""Indexing layer: field norm, key store, max-heap, and index build."""

from __future__ import annotations

import json

import pytest

from fusejs.errors import (
    InvalidDocIndexError,
    InvalidKeyWeightError,
    MissingKeyPropertyError,
)
from fusejs.tools import (
    FieldNorm,
    KeyStore,
    MaxHeap,
    create_index,
    create_key,
    create_key_id,
    create_key_path,
    parse_index,
)
from fusejs.types import InternalResult, KeyOption


def _result(idx: int, score: float) -> InternalResult:
    return InternalResult(idx=idx, item=None, score=score)


def _by_score(a: InternalResult, b: InternalResult) -> int:
    if a.score == b.score:
        return -1 if a.idx < b.idx else 1
    return -1 if a.score < b.score else 1


class TestFieldNorm:
    def test_shorter_fields_weigh_more(self):
        norm = FieldNorm()
        assert norm.get("one") > norm.get("one two") > norm.get("one two three")

    def test_single_token_is_one(self):
        assert FieldNorm().get("word") == 1.0

    def test_quantised_to_three_decimals(self):
        # 1/sqrt(2) = 0.7071... -> 0.707
        assert FieldNorm().get("one two") == 0.707

    def test_blank_and_empty_treated_as_one_token(self):
        norm = FieldNorm()
        assert norm.get("") == 1.0
        assert norm.get("   \t\n ") == 1.0

    def test_leading_and_trailing_separators_do_not_inflate_count(self):
        norm = FieldNorm()
        assert norm.get("  one two  ") == norm.get("one two")

    def test_tabs_and_newlines_separate_words(self):
        norm = FieldNorm()
        assert norm.get("one\ttwo") == norm.get("one two")
        assert norm.get("one\ntwo") == norm.get("one two")

    def test_nbsp_separates_but_other_unicode_spaces_do_not(self):
        # The original narrows the separator set on purpose; the ideographic
        # space is deliberately *not* a separator.
        norm = FieldNorm()
        assert norm.get("one two") == norm.get("one two")
        assert norm.get("one　two") == norm.get("onetwo")

    def test_weight_changes_the_curve(self):
        assert FieldNorm(weight=2.0).get("a b c d") != FieldNorm().get("a b c d")

    def test_cache_is_keyed_on_token_count(self):
        # Different text, same token count -> same cache entry, same norm.
        norm = FieldNorm()
        assert norm.get("aa bb") == norm.get("completely different")
        norm.clear()
        assert norm.get("aa bb") == 0.707


class TestCreateKey:
    def test_dotted_string(self):
        key = create_key("author.name")
        assert key.path == ["author", "name"]
        assert key.id == "author.name"
        assert key.weight == 1

    def test_path_list(self):
        key = create_key(["author", "name"])
        assert key.path == ["author", "name"]
        assert key.id == "author.name"

    def test_key_option_with_weight(self):
        assert create_key(KeyOption("title", weight=3)).weight == 3

    def test_plain_mapping_accepted(self):
        # What a JSON config deserialises to.
        assert create_key({"name": "title", "weight": 2}).weight == 2

    def test_mapping_without_name_raises(self):
        with pytest.raises(MissingKeyPropertyError, match="Missing name property"):
            create_key({"weight": 2})

    @pytest.mark.parametrize("weight", [0, -1, -0.5])
    def test_non_positive_weight_raises(self, weight):
        with pytest.raises(InvalidKeyWeightError, match="must be a positive integer"):
            create_key({"name": "title", "weight": weight})

    def test_path_and_id_helpers(self):
        assert create_key_path("a.b") == ["a", "b"]
        assert create_key_id(["a", "b"]) == "a.b"


class TestKeyStore:
    @staticmethod
    def _weights(*keys):
        store = KeyStore(list(keys))
        # KeyStore.keys() is a method returning a list, not a dict view;
        # ruff's `key in dict.keys()` heuristic misfires here.
        return [k.weight for k in store.keys()]  # noqa: SIM118

    def test_weights_normalise_to_one(self):
        assert self._weights("title", "author") == [0.5, 0.5]

    def test_only_the_ratio_matters(self):
        small = self._weights({"name": "a", "weight": 1}, {"name": "b", "weight": 3})
        large = self._weights(
            {"name": "a", "weight": 100}, {"name": "b", "weight": 300}
        )
        assert small == large == [0.25, 0.75]

    def test_lookup_by_id(self):
        store = KeyStore(["title"])
        assert store.get("title") is not None
        assert store.get("nope") is None

    def test_empty_key_list_does_not_divide_by_zero(self):
        assert self._weights() == []


class TestMaxHeap:
    def test_retains_best_n(self):
        heap = MaxHeap(3, _by_score)
        for idx, score in enumerate([0.5, 0.1, 0.9, 0.2, 0.7]):
            heap.insert(_result(idx, score))
        assert [r.score for r in heap.extract_sorted()] == [0.1, 0.2, 0.5]

    def test_matches_full_sort_then_slice(self):
        scores = [0.4, 0.4, 0.1, 0.9, 0.4, 0.2, 0.8, 0.15]
        results = [_result(i, s) for i, s in enumerate(scores)]

        heap = MaxHeap(4, _by_score)
        for result in results:
            heap.insert(result)
        via_heap = [(r.idx, r.score) for r in heap.extract_sorted()]

        full = sorted(results, key=lambda r: (r.score, r.idx))
        via_sort = [(r.idx, r.score) for r in full[:4]]

        assert via_heap == via_sort

    def test_ties_break_by_index(self):
        heap = MaxHeap(2, _by_score)
        for idx in range(5):
            heap.insert(_result(idx, 0.5))
        assert [r.idx for r in heap.extract_sorted()] == [0, 1]

    def test_accepts_float_returning_comparator(self):
        # A user sort_fn is as likely to be written `a.score - b.score`.
        heap = MaxHeap(2, lambda a, b: a.score - b.score)
        for idx, score in enumerate([0.9, 0.1, 0.5]):
            heap.insert(_result(idx, score))
        assert [r.score for r in heap.extract_sorted()] == [0.1, 0.5]

    def test_empty_heap_sorts_to_nothing(self):
        assert MaxHeap(3, _by_score).extract_sorted() == []


class TestFuseIndex:
    DOCS = [
        {"title": "Old Man's War", "author": {"name": "Scalzi"}},
        {"title": "The Lock Artist", "author": {"name": "Hamilton"}},
    ]

    def test_indexes_nested_paths(self):
        index = create_index(["title", "author.name"], self.DOCS)
        assert index.size() == 2
        record = index.records[0]
        assert record.fields is not None
        assert record.fields[1].v == "Scalzi"

    def test_blank_string_docs_are_skipped(self):
        index = create_index([], ["real", "", "   ", "also real"])
        assert [r.v for r in index.records] == ["real", "also real"]
        # Source indices survive the gap.
        assert [r.i for r in index.records] == [0, 3]

    def test_array_values_keep_their_position(self):
        index = create_index(["tags"], [{"tags": ["alpha", "beta"]}])
        subs = index.records[0].fields[0]
        assert [(s.v, s.i) for s in subs] == [("alpha", 0), ("beta", 1)]

    def test_scalars_are_stringified_like_js(self):
        index = create_index(["n"], [{"n": 42}, {"n": True}, {"n": 3.0}])
        assert [r.fields[0].v for r in index.records] == ["42", "true", "3"]

    def test_create_is_idempotent(self):
        index = create_index(["title"], self.DOCS)
        index.create()
        assert index.size() == 2

    def test_add_appends(self):
        index = create_index(["title"], list(self.DOCS))
        index.add({"title": "New"}, 2)
        assert index.size() == 3

    def test_add_blank_string_returns_none(self):
        index = create_index([], ["a"])
        assert index.add("   ", 1) is None
        assert index.size() == 1

    @pytest.mark.parametrize("bad", [-1, 1.5, True, "0"])
    def test_add_rejects_invalid_index(self, bad):
        index = create_index(["title"], list(self.DOCS))
        with pytest.raises(InvalidDocIndexError):
            index.add({"title": "x"}, bad)

    def test_remove_at_renumbers_survivors(self):
        index = create_index([], ["a", "b", "c"])
        index.remove_at(1)
        assert [(r.v, r.i) for r in index.records] == [("a", 0), ("c", 1)]

    def test_remove_all_renumbers_by_removed_count(self):
        index = create_index([], ["a", "b", "c", "d", "e"])
        index.remove_all([1, 3])
        assert [(r.v, r.i) for r in index.records] == [("a", 0), ("c", 1), ("e", 2)]

    def test_remove_all_ignores_invalid_entries(self):
        index = create_index([], ["a", "b"])
        index.remove_all([-1, 2.5])
        assert index.size() == 2

    def test_round_trips_through_serialisation(self):
        index = create_index(["title", "author.name"], self.DOCS)
        restored = parse_index(json.loads(json.dumps(index.to_dict())))
        assert restored.to_dict() == index.to_dict()

    def test_value_lookup_by_key_id(self):
        index = create_index(["title", "author.name"], self.DOCS)
        item = index.records[0].fields
        assert index.get_value_for_item_at_key_id(item, "author.name").v == "Scalzi"
        assert index.get_value_for_item_at_key_id(item, "missing") is None


class TestIndexDifferential:
    """The serialised index must be wire-identical to fuse.js's."""

    CASES = [
        (["title"], None),  # None -> the books fixture
        (["title", "author.firstName"], None),
        ([{"name": "title", "weight": 2}, {"name": "author.lastName", "weight": 3}],
         None),
        ([], ["Old Man's War", "The Lock Artist", "", "   ", "HTML5"]),
        (["tags"], [{"tags": ["a", "bb", "ccc ddd"]}, {"tags": []}, {"tags": ["  "]}]),
        (["a.b.c"], [{"a": {"b": {"c": "deep value here"}}}, {"a": {"b": {}}}, {}]),
        (["n"], [{"n": 42}, {"n": True}, {"n": 3.0}, {"n": None}]),
        (["authors.name"], [{"authors": [{"name": "Ann Lee"}, {"name": "Bob"}]}]),
    ]

    @staticmethod
    def _canonical(value):
        if isinstance(value, list):
            return [TestIndexDifferential._canonical(v) for v in value]
        if isinstance(value, dict):
            return {k: TestIndexDifferential._canonical(value[k])
                    for k in sorted(value)}
        return value

    @pytest.mark.parametrize(("keys", "docs"), CASES)
    def test_matches_oracle(self, oracle, books, keys, docs):
        documents = books if docs is None else docs
        expected = oracle.create_index(keys, documents, {})
        actual = json.loads(json.dumps(create_index(keys, documents).to_dict()))
        assert self._canonical(actual) == expected
