"""The search engine — the port of ``src/core/index.ts``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .. import features
from ..config import FuseOptions
from ..errors import (
    EXTENDED_SEARCH_UNAVAILABLE,
    LOGICAL_SEARCH_UNAVAILABLE,
    MATCH_TOKEN_SEARCH_UNSUPPORTED,
    TOKEN_SEARCH_UNAVAILABLE,
    FeatureUnavailableError,
    InvalidDocIndexError,
    InvalidIndexTypeError,
)
from ..helpers.type_guards import is_defined
from ..search.token import MAX_MASK_TERMS
from ..search.token.analyzer import create_analyzer
from ..search.token.inverted_index import (
    InvertedIndexData,
    add_to_inverted_index,
    build_inverted_index,
    remove_and_shift_inverted_index,
)
from ..tools.fuse_index import FuseIndex, create_index
from ..tools.key_store import KeyStore
from ..tools.max_heap import Comparator, ComparatorKey, MaxHeap
from ..types import (
    Expression,
    FuseResult,
    InternalResult,
    KeyObject,
    MatchScore,
    Searcher,
    SearchResult,
    SubRecord,
)
from .compute_score import compute_score, compute_score_single
from .format import format_results
from .query_parser import LogicalOperator, ParsedLeaf, ParsedNode, ParsedOperator, parse
from .register import create_searcher

__all__ = ["Fuse"]


class Fuse:
    """Fuzzy search over a collection of documents.

    >>> books = [{"title": "Old Man's War"}, {"title": "The Lock Artist"}]
    >>> fuse = Fuse(books, {"keys": ["title"]})
    >>> [r.item["title"] for r in fuse.search("old man")]
    ["Old Man's War"]
    """

    __slots__ = (
        "_docs",
        "_inverted_index",
        "_key_store",
        "_last_query",
        "_last_searcher",
        "_my_index",
        "options",
    )

    def __init__(
        self,
        docs: Sequence[Any],
        options: FuseOptions | dict[str, Any] | None = None,
        index: FuseIndex | None = None,
    ) -> None:
        self.options = FuseOptions.coerce(options)

        if self.options.use_extended_search and not features.EXTENDED_SEARCH_ENABLED:
            raise FeatureUnavailableError(EXTENDED_SEARCH_UNAVAILABLE)

        if self.options.use_token_search and not features.TOKEN_SEARCH_ENABLED:
            raise FeatureUnavailableError(TOKEN_SEARCH_UNAVAILABLE)

        self._key_store = KeyStore(self.options.keys)

        # A copy, unlike the original: fuse.js pushes straight into the array
        # the caller passed, so `fuse.add(doc)` mutates it as a side effect.
        # Owning our own list keeps `add`/`remove` from reaching back into
        # caller state — and the index would be stale for external mutations
        # anyway.
        self._docs: list[Any] = list(docs)
        self._inverted_index: InvertedIndexData | None = None

        self.set_collection(self._docs, index)

        self._last_query: str | None = None
        self._last_searcher: Searcher | None = None

    # ── Collection management ──────────────────────────────────────

    def set_collection(
        self, docs: Sequence[Any], index: FuseIndex | None = None
    ) -> None:
        """Replace the searched collection, optionally with a prebuilt index."""
        self._docs = list(docs)

        if index is not None and not isinstance(index, FuseIndex):
            raise InvalidIndexTypeError

        self._my_index = index or create_index(
            self.options.keys,
            self._docs,
            get_fn=self.options.get_fn,
            field_norm_weight=self.options.field_norm_weight,
        )

        if self.options.use_token_search:
            self._inverted_index = build_inverted_index(
                self._my_index.records,
                len(self._my_index.keys),
                create_analyzer(self.options),
            )

        self._invalidate_searcher_cache()

    def add(self, doc: Any) -> None:
        """Append a document to the collection and index it."""
        if not is_defined(doc):
            return

        self._docs.append(doc)
        record = self._my_index.add(doc, len(self._docs) - 1)

        # Blank strings produce no record; touching the inverted index here
        # would re-ingest the *previous* document.
        if self._inverted_index is not None and record is not None:
            add_to_inverted_index(
                self._inverted_index,
                record,
                len(self._my_index.keys),
                create_analyzer(self.options),
            )

        self._invalidate_searcher_cache()

    def remove(
        self, predicate: Callable[[Any, int], bool] | None = None
    ) -> list[Any]:
        """Remove every document matching ``predicate``; return those removed."""
        if predicate is None:
            return []

        removed: list[Any] = []
        indices_to_remove: list[int] = []

        for i, doc in enumerate(self._docs):
            if predicate(doc, i):
                removed.append(doc)
                indices_to_remove.append(i)

        if indices_to_remove:
            if self._inverted_index is not None:
                remove_and_shift_inverted_index(
                    self._inverted_index, indices_to_remove
                )

            to_remove = set(indices_to_remove)
            self._docs = [d for i, d in enumerate(self._docs) if i not in to_remove]
            self._my_index.remove_all(indices_to_remove)

            self._invalidate_searcher_cache()

        return removed

    def remove_at(self, idx: int) -> Any:
        """Remove and return the document at ``idx``.

        Raises:
            InvalidDocIndexError: if ``idx`` is out of bounds or not a
                non-negative integer.
        """
        # Validate before mutating anything: the original splices `_docs`
        # first and lets the index removal throw afterwards, leaving partial
        # state behind on invalid input.
        if (
            not isinstance(idx, int)
            or isinstance(idx, bool)
            or idx < 0
            or idx >= len(self._docs)
        ):
            raise InvalidDocIndexError

        if self._inverted_index is not None:
            remove_and_shift_inverted_index(self._inverted_index, [idx])

        doc = self._docs.pop(idx)
        self._my_index.remove_at(idx)
        self._invalidate_searcher_cache()
        return doc

    def get_index(self) -> FuseIndex:
        """The underlying index."""
        return self._my_index

    # ── Searching ──────────────────────────────────────────────────

    def search(
        self, query: str | Expression, limit: int = -1
    ) -> list[FuseResult]:
        """Search the collection.

        ``limit`` caps the number of results; ``-1`` (the default) returns
        every match. An empty or whitespace-only string query returns the
        whole collection unscored, which is what a search UI wants on an
        empty input.
        """
        options = self.options

        if isinstance(query, str) and not query.strip():
            docs = [
                FuseResult(item=item, ref_index=idx)
                for idx, item in enumerate(self._docs)
            ]
            return docs[:limit] if limit > -1 else docs

        comparator: Comparator = options.sort_fn

        # Canonical tie-break for string and object search: break comparator
        # ties by document index so heap selection and the full sort agree,
        # and both equal a full sort sliced to the limit. This only bites
        # when a custom sort_fn returns 0 for distinct results. Logical
        # search has no heap path, so it keeps the raw comparator.
        def stable(a: InternalResult, b: InternalResult) -> int | float:
            return comparator(a, b) or (a.idx - b.idx)

        # The heap selects a sorted top-N, so it applies only when sorting is
        # on. With should_sort=False the collection-order-then-slice path must
        # be kept, so search(q, limit) still equals search(q)[:limit].
        use_heap = (
            options.should_sort and limit > 0 and isinstance(query, str)
        )

        results: list[InternalResult]

        if use_heap and isinstance(query, str):
            heap = MaxHeap(limit, stable)
            if self._docs and isinstance(self._docs[0], str):
                self._search_string_list(query, heap=heap)
            else:
                self._search_object_list(query, heap=heap)
            results = heap.extract_sorted()
        else:
            if isinstance(query, str):
                results = (
                    self._search_string_list(query)
                    if self._docs and isinstance(self._docs[0], str)
                    else self._search_object_list(query)
                )
            else:
                results = self._search_logical(query)

            compute_score(
                results,
                ignore_field_norm=options.ignore_field_norm,
                strict_js_pow=options.strict_js_pow,
            )

            if options.should_sort:
                key = stable if isinstance(query, str) else comparator
                results.sort(key=lambda r: ComparatorKey(r, key))

            if limit > -1:
                results = results[:limit]

        return format_results(
            results,
            self._docs,
            include_matches=options.include_matches,
            include_score=options.include_score,
        )

    # ── Searcher cache ─────────────────────────────────────────────

    def _get_searcher(self, query: str) -> Searcher:
        """The searcher for ``query``, reusing the last one when unchanged."""
        if self._last_query == query and self._last_searcher is not None:
            return self._last_searcher

        options = self.options
        if self._inverted_index is not None:
            options = options.replace(inverted_index=self._inverted_index)

        searcher = create_searcher(query, options)
        self._last_query = query
        self._last_searcher = searcher
        return searcher

    def _invalidate_searcher_cache(self) -> None:
        self._last_query = None
        self._last_searcher = None

    def _requires_all_tokens(self) -> bool:
        return self.options.use_token_search and self.options.token_match == "all"

    # ── Search strategies ──────────────────────────────────────────

    def _search_string_list(
        self, query: str, heap: MaxHeap | None = None
    ) -> list[InternalResult]:
        searcher = self._get_searcher(query)
        require_all = self._requires_all_tokens()
        results: list[InternalResult] = []

        for record in self._my_index.records:
            text = record.v
            if text is None:
                continue

            search_result = searcher.search_in(text)
            if not search_result.is_match:
                continue

            match = MatchScore(
                score=search_result.score,
                value=text,
                norm=record.n if record.n is not None else 1.0,
                indices=search_result.indices,
            )
            if require_all:
                match.matched_mask = search_result.matched_mask
                match.matched_terms = search_result.matched_terms
                match.term_count = search_result.term_count

            matches = [match]

            # Record-level AND gate for token search, applied before heap
            # insertion so `limit` returns the same top-N as an unlimited run.
            if require_all and not self._covers_all_tokens(matches):
                continue

            result = InternalResult(idx=record.i, item=text, matches=matches)

            if heap is not None:
                result.score = compute_score_single(
                    result.matches,
                    ignore_field_norm=self.options.ignore_field_norm,
                    strict_js_pow=self.options.strict_js_pow,
                )
                heap.insert(result)
            else:
                results.append(result)

        return results

    def _search_object_list(
        self, query: str, heap: MaxHeap | None = None
    ) -> list[InternalResult]:
        searcher = self._get_searcher(query)
        require_all = self._requires_all_tokens()
        keys = self._normalized_keys()
        results: list[InternalResult] = []

        for record in self._my_index.records:
            item = record.fields
            if item is None:
                continue

            matches: list[MatchScore] = []
            any_key_failed = False
            has_inverse = False

            for key_index, key in enumerate(keys):
                key_matches = self._find_matches(
                    key=key, value=item.get(key_index), searcher=searcher
                )

                if key_matches:
                    matches.extend(key_matches)
                    if key_matches[0].has_inverse:
                        has_inverse = True
                else:
                    any_key_failed = True

            # When inverse patterns are involved (e.g. `!Syrup`), aggregation
            # across keys switches from "any key matches" to "all keys must
            # match". For a mixed query like `^hello !Syrup` a key failure is
            # ambiguous — it could be the positive or the inverse term that
            # failed — so the item is conservatively excluded. See
            # https://github.com/krisk/Fuse/issues/712
            if has_inverse and any_key_failed:
                continue

            if matches and (not require_all or self._covers_all_tokens(matches)):
                result = InternalResult(idx=record.i, item=item, matches=matches)

                if heap is not None:
                    result.score = compute_score_single(
                        result.matches,
                        ignore_field_norm=self.options.ignore_field_norm,
                    )
                    heap.insert(result)
                else:
                    results.append(result)

        return results

    def _search_logical(self, query: Expression) -> list[InternalResult]:
        if not features.LOGICAL_SEARCH_ENABLED:
            raise FeatureUnavailableError(LOGICAL_SEARCH_UNAVAILABLE)

        options = self.options
        if self._inverted_index is not None:
            options = options.replace(inverted_index=self._inverted_index)

        expression = parse(query, options)

        # Keyless leaves fan out across every key; normalised weights keep
        # their scores consistent with string and keyed queries.
        keys = self._normalized_keys()

        def evaluate(
            node: ParsedNode, item: dict[int, SubRecord | list[SubRecord]], idx: int
        ) -> list[InternalResult]:
            if isinstance(node, ParsedLeaf):
                if node.searcher is None:
                    return []

                matches: list[MatchScore] = []

                if node.key_id is None:
                    # Keyless entry: search across all keys.
                    for key_index, key in enumerate(keys):
                        matches.extend(
                            self._find_matches(
                                key=key,
                                value=item.get(key_index),
                                searcher=node.searcher,
                            )
                        )
                else:
                    matches = self._find_matches(
                        key=self._key_store.get(node.key_id),
                        value=self._my_index.get_value_for_item_at_key_id(
                            item, node.key_id
                        ),
                        searcher=node.searcher,
                    )

                if matches:
                    return [InternalResult(idx=idx, item=item, matches=matches)]
                return []

            operator: ParsedOperator = node
            collected: list[InternalResult] = []
            for child in operator.children:
                child_results = evaluate(child, item, idx)
                if child_results:
                    collected.extend(child_results)
                elif operator.operator == LogicalOperator.AND:
                    return []
            return collected

        result_map: dict[int, InternalResult] = {}
        results: list[InternalResult] = []

        for record in self._my_index.records:
            item = record.fields
            if item is None:
                continue

            expression_results = evaluate(expression, item, record.i)
            if not expression_results:
                continue

            existing = result_map.get(record.i)
            if existing is None:
                existing = InternalResult(idx=record.i, item=item, matches=[])
                result_map[record.i] = existing
                results.append(existing)

            for expression_result in expression_results:
                existing.matches.extend(expression_result.matches)

        return results

    def _find_matches(
        self,
        key: KeyObject | None,
        value: SubRecord | list[SubRecord] | None,
        searcher: Searcher,
    ) -> list[MatchScore]:
        if value is None:
            return []

        matches: list[MatchScore] = []
        sub_records = value if isinstance(value, list) else [value]

        for sub in sub_records:
            search_result = searcher.search_in(sub.v)
            if not search_result.is_match:
                continue

            match = MatchScore(
                score=search_result.score,
                key=key,
                value=sub.v,
                idx=sub.i,
                norm=sub.n,
                indices=search_result.indices,
                has_inverse=search_result.has_inverse,
            )
            # Carry token-search coverage only when present, so the default
            # (non-token) match keeps its original shape.
            if search_result.term_count is not None:
                match.matched_mask = search_result.matched_mask
                match.matched_terms = search_result.matched_terms
                match.term_count = search_result.term_count

            matches.append(match)

        return matches

    def _covers_all_tokens(self, matches: list[MatchScore]) -> bool:
        """Whether a record's matches together cover every query term.

        The record-level AND gate for ``token_match="all"``. ``term_count`` is
        set only by token search in that mode, so every other search passes
        unconditionally.
        """
        term_count = matches[0].term_count if matches else None
        if term_count is None:
            return True

        if term_count <= MAX_MASK_TERMS:
            coverage = 0
            for match in matches:
                coverage |= match.matched_mask or 0
            return bool(coverage == 2**term_count - 1)

        covered: set[int] = set()
        for match in matches:
            if match.matched_terms:
                covered |= match.matched_terms
        return len(covered) == term_count

    def _normalized_keys(self) -> list[KeyObject]:
        """Index keys resolved to their weight-normalised counterparts.

        ``FuseIndex.keys`` carries raw user weights; only ``KeyStore``
        normalises them to sum to 1. Scoring off the raw weights underflows
        for large values and diverges from the keyed logical path.
        """
        return [
            self._key_store.get(key.id) or key for key in self._my_index.keys
        ]

    # ── One-off matching ───────────────────────────────────────────

    @staticmethod
    def match(
        pattern: str, text: str, options: FuseOptions | dict[str, Any] | None = None
    ) -> SearchResult:
        """Match one pattern against one string, without building an index.

        Raises:
            FeatureUnavailableError: if token search is requested. It needs
                corpus statistics that a one-off comparison cannot have.
        """
        resolved = FuseOptions.coerce(options)
        if resolved.use_token_search:
            raise FeatureUnavailableError(MATCH_TOKEN_SEARCH_UNSUPPORTED)
        return create_searcher(pattern, resolved).search_in(text)
