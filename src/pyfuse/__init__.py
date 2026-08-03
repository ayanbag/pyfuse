"""pyfuse — a byte-equivalent Python port of fuse.js.

Lightweight fuzzy-search over collections of records: typo-tolerant matching
on a Bitap core, weighted keys, nested-path lookups, extended-query operators
and relevance scoring. No runtime dependencies, and no JavaScript.

Ported from fuse.js (https://github.com/krisk/Fuse) v7.5.0, Apache-2.0.

>>> books = [
...     {"title": "Old Man's War", "author": "John Scalzi"},
...     {"title": "The Lock Artist", "author": "Steve Hamilton"},
... ]
>>> fuse = Fuse(books, {"keys": ["title", "author"]})
>>> [r.item["title"] for r in fuse.search("old man")]
["Old Man's War"]
"""

from __future__ import annotations

from . import features
from .config import MAX_BITS, FuseOptions, default_sort_fn
from .core import Fuse
from .core.query_parser import parse as parse_query
from .core.register import clear_registry, create_searcher, register
from .errors import (
    FeatureUnavailableError,
    FuseError,
    InvalidDocIndexError,
    InvalidIndexTypeError,
    InvalidKeyError,
    InvalidKeyWeightError,
    InvalidQueryError,
    MissingKeyPropertyError,
    PatternLengthError,
)
from .search.bitap import BitapSearch
from .search.extended import ExtendedSearch
from .search.token import TokenSearch
from .tools.fuse_index import FuseIndex, create_index, parse_index
from .types import (
    FuseResult,
    FuseResultMatch,
    KeyObject,
    KeyOption,
    RangeTuple,
    Searcher,
    SearchResult,
)

#: The fuse.js release this port tracks.
__version__ = "7.5.0"

# Registration order is significant — `create_searcher` takes the first
# searcher whose `condition` accepts, so a query with both options enabled
# goes to extended search. This mirrors fuse.js's `entry.ts`.
if features.EXTENDED_SEARCH_ENABLED:
    register(ExtendedSearch)
if features.TOKEN_SEARCH_ENABLED:
    register(TokenSearch)

__all__ = [
    "MAX_BITS",
    "BitapSearch",
    "ExtendedSearch",
    "FeatureUnavailableError",
    "Fuse",
    "FuseError",
    "FuseIndex",
    "FuseOptions",
    "FuseResult",
    "FuseResultMatch",
    "InvalidDocIndexError",
    "InvalidIndexTypeError",
    "InvalidKeyError",
    "InvalidKeyWeightError",
    "InvalidQueryError",
    "KeyObject",
    "KeyOption",
    "MissingKeyPropertyError",
    "PatternLengthError",
    "RangeTuple",
    "SearchResult",
    "Searcher",
    "TokenSearch",
    "__version__",
    "clear_registry",
    "create_index",
    "create_searcher",
    "default_sort_fn",
    "parse_index",
    "parse_query",
    "register",
]
