"""Public and internal types for the port.

These mirror ``src/types.ts`` in fuse.js. Where the TypeScript original uses a
structurally-typed object literal, this port uses a :mod:`dataclasses`
dataclass: the shapes are closed and known, so nothing is gained by keeping
them dict-shaped, and static typing gets much stronger.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias, TypeVar, runtime_checkable

T = TypeVar("T")

# ── Range / indices ────────────────────────────────────────────────

#: An inclusive ``(start, end)`` character range within a matched value.
RangeTuple: TypeAlias = "tuple[int, int]"


# ── Search internals ───────────────────────────────────────────────


@dataclass(slots=True)
class SearchResult:
    """The outcome of matching one pattern against one text value."""

    is_match: bool
    score: float
    indices: list[RangeTuple] | None = None
    #: Aggregation flag for extended-search inverse terms.
    has_inverse: bool = False
    #: Token-search ``token_match="all"`` coverage for this text. Bit ``i`` of
    #: ``matched_mask`` means query term ``i`` matched here (<=31-term fast
    #: path); ``matched_terms`` is the equivalent set for the >=32-term
    #: fallback.
    matched_mask: int | None = None
    matched_terms: set[int] | None = None
    #: Query token count; descriptor for the record-level AND gate.
    term_count: int | None = None


@runtime_checkable
class Searcher(Protocol):
    """Anything that can match a pattern against a single text value.

    ``BitapSearch``, ``ExtendedSearch`` and ``TokenSearch`` all satisfy this;
    so does any plugin registered through :func:`fusejs.register`.
    """

    def search_in(self, text: str) -> SearchResult:
        """Match this searcher's pattern against ``text``."""
        ...


class SearcherFactory(Protocol):
    """A registrable searcher class.

    ``condition`` decides whether this searcher handles a given
    ``(pattern, options)`` pair; the first registered factory to accept wins.
    """

    def __call__(self, pattern: str, options: Any) -> Searcher: ...

    @staticmethod
    def condition(pattern: str, options: Any) -> bool: ...


# ── Keys ───────────────────────────────────────────────────────────

#: A user-supplied getter for a key: receives the whole document.
KeyGetFn: TypeAlias = Callable[[Any], "list[str] | tuple[str, ...] | str | None"]

#: A user-supplied path resolver: receives the document and the key path.
GetFn: TypeAlias = Callable[[Any, "str | list[str]"], Any]


@dataclass(slots=True)
class KeyObject:
    """A resolved search key: its path, canonical id, and weight."""

    path: list[str]
    id: str
    weight: float
    src: str | list[str]
    get_fn: KeyGetFn | None = None


#: What a user may pass in ``keys``: a dotted string, a path list, or a
#: :class:`KeyOption` describing weight and/or a custom getter.
FuseOptionKey: TypeAlias = "str | list[str] | KeyOption"


@dataclass(slots=True)
class KeyOption:
    """The object form of a search key."""

    name: str | list[str]
    weight: float | None = None
    get_fn: KeyGetFn | None = None


# ── Tokenizer (token search) ───────────────────────────────────────

#: Custom tokenizer for ``use_token_search``. Receives field/query text after
#: case-folding and diacritic-stripping and returns the term list. Must be
#: deterministic — non-deterministic output silently breaks ``df`` accounting.
TokenizeFn: TypeAlias = Callable[[str], "list[str]"]


# ── Index records ──────────────────────────────────────────────────


@dataclass(slots=True)
class SubRecord:
    """One indexed text value: its content, field-length norm, array index."""

    v: str
    n: float
    #: Position within the source array, when this value came from one.
    i: int | None = None


@dataclass(slots=True)
class IndexRecord:
    """One indexed document.

    A string document populates ``v``/``n``; an object document populates
    ``fields``, keyed by the key's position in the key list.
    """

    i: int
    v: str | None = None
    n: float | None = None
    #: ``fields[key_index]`` is the value(s) indexed for that key. Named
    #: ``$`` in fuse.js, which is not a legal Python identifier.
    fields: dict[int, SubRecord | list[SubRecord]] | None = None


# ── Scoring internals ──────────────────────────────────────────────


@dataclass(slots=True)
class MatchScore:
    """A single (key, value) hit contributing to a document's score."""

    score: float
    value: str
    norm: float
    key: KeyObject | None = None
    idx: int | None = None
    has_inverse: bool = False
    indices: list[RangeTuple] | None = None
    #: Token-search ``token_match="all"`` coverage, carried up for the
    #: record-level AND gate.
    matched_mask: int | None = None
    matched_terms: set[int] | None = None
    term_count: int | None = None


@dataclass(slots=True)
class InternalResult:
    """A candidate document plus every match found on it."""

    idx: int
    item: Any
    matches: list[MatchScore] = field(default_factory=list)
    score: float | None = None


# ── Results ────────────────────────────────────────────────────────


@dataclass(slots=True)
class FuseResultMatch:
    """Where a query matched inside one field of a result."""

    indices: list[RangeTuple]
    value: str | None = None
    key: str | None = None
    ref_index: int | None = None


@dataclass(slots=True)
class FuseResult:
    """One search hit."""

    item: Any
    ref_index: int
    score: float | None = None
    matches: list[FuseResultMatch] | None = None


#: Comparator over internal results, matching ``FuseSortFunction``.
SortFn: TypeAlias = Callable[[InternalResult, InternalResult], "int | float"]


# ── Logical-search expressions ─────────────────────────────────────

#: A logical query: a bare pattern, ``{key: pattern}``, ``{"$path": [...],
#: "$val": pattern}``, or ``{"$and"/"$or": [...]}``.
Expression: TypeAlias = "str | dict[str, Any]"
