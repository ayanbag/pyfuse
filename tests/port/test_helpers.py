"""Helper-layer tests: JS-semantics shims, path resolution, diacritics."""

from __future__ import annotations

import pytest

from fusejs._js import js_index_of, js_round, js_str, js_trim
from fusejs.helpers import merge_indices, strip_diacritics
from fusejs.helpers.get import PathValue, get
from fusejs.helpers.type_guards import is_blank, is_defined, to_string


class TestJsSemantics:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0.5, 1), (1.5, 2), (2.5, 3), (-0.5, 0), (3.4, 3), (3.6, 4), (0.0, 0)],
    )
    def test_round_is_half_up_not_bankers(self, value, expected):
        # Python's round(0.5) == 0; Math.round(0.5) === 1.
        assert js_round(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, "true"),
            (False, "false"),
            (None, "null"),
            (3.0, "3"),
            (3, "3"),
            (1.5, "1.5"),
            (1e-7, "1e-7"),
            (1e21, "1e+21"),
            (-0.0, "0"),
            ("already", "already"),
        ],
    )
    def test_stringification_matches_js(self, value, expected):
        assert js_str(value) == expected

    def test_to_string_maps_none_to_empty(self):
        assert to_string(None) == ""
        assert to_string(False) == "false"

    def test_trim_includes_bom(self):
        # U+FEFF is whitespace to JS `trim` but not to Python `strip`.
        assert js_trim("﻿ hi ﻿") == "hi"
        assert is_blank("﻿  \t\n")

    def test_index_of_empty_needle_clamps(self):
        # str.find would return -1 past the end; indexOf returns len.
        assert js_index_of("abc", "", 99) == 3
        assert js_index_of("abc", "x", 99) == -1

    def test_is_defined_keeps_falsy_values(self):
        assert is_defined(0)
        assert is_defined("")
        assert is_defined(False)
        assert not is_defined(None)


class TestDiacritics:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("café", "cafe"),
            ("café", "cafe"),  # already decomposed
            ("naïve résumé", "naive resume"),
            ("Łódź", "Lodz"),  # non-decomposable + decomposable mixed
            ("ß", "ss"),  # expands to two characters
            ("øØđĐħĦŧŦıł", "oOdDhHtTil"),
            ("plain", "plain"),
        ],
    )
    def test_strips(self, text, expected):
        assert strip_diacritics(text) == expected

    def test_is_idempotent(self):
        once = strip_diacritics("Łódź café")
        assert strip_diacritics(once) == once


class TestGet:
    def test_simple_and_nested_paths(self):
        assert get({"title": "War"}, "title") == "War"
        assert get({"author": {"name": "Scalzi"}}, "author.name") == "Scalzi"
        assert get({"author": {"name": "Scalzi"}}, ["author", "name"]) == "Scalzi"

    def test_missing_path_is_none(self):
        assert get({"a": 1}, "b") is None
        assert get({"a": {"b": 1}}, "a.c") is None
        assert get(None, "a") is None

    def test_scalars_are_stringified(self):
        assert get({"n": 42}, "n") == "42"
        assert get({"n": True}, "n") == "true"
        assert get({"n": 3.0}, "n") == "3"

    def test_array_fanout_preserves_position(self):
        doc = {"tags": [{"n": "alpha"}, {"n": "beta"}]}
        assert get(doc, "tags.n") == [PathValue("alpha", 0), PathValue("beta", 1)]

    def test_array_of_strings(self):
        assert get({"tags": ["a", "b"]}, "tags") == [
            PathValue("a", 0),
            PathValue("b", 1),
        ]

    def test_empty_array_still_yields_a_list(self):
        # The array flag is sticky: arity, not contents, decides the shape.
        assert get({"tags": []}, "tags.n") == []

    def test_resolves_attributes_as_well_as_keys(self):
        # A Python-only extension: documents are as often objects as dicts.
        class Author:
            name = "Scalzi"

        class Book:
            author = Author()

        assert get(Book(), "author.name") == "Scalzi"


class TestMergeIndices:
    def test_merges_overlapping_and_adjacent(self):
        assert merge_indices([(5, 7), (0, 2), (3, 4)]) == [(0, 7)]
        assert merge_indices([(0, 2), (4, 6)]) == [(0, 2), (4, 6)]
        assert merge_indices([(0, 5), (1, 2)]) == [(0, 5)]

    def test_short_inputs_pass_through(self):
        assert merge_indices([]) == []
        assert merge_indices([(3, 4)]) == [(3, 4)]

    def test_does_not_mutate_input(self):
        original = [(5, 7), (0, 2)]
        merge_indices(original)
        assert original == [(5, 7), (0, 2)]
