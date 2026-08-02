"""fusejs — a byte-equivalent Python port of fuse.js.

Lightweight fuzzy-search over collections of records: typo-tolerant matching
on a Bitap core, weighted keys, nested-path lookups, extended-query operators
and relevance scoring. No runtime dependencies, and no JavaScript.

Ported from fuse.js (https://github.com/krisk/Fuse) v7.5.0, Apache-2.0.
"""

from __future__ import annotations

from .config import MAX_BITS, FuseOptions
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

__all__ = [
    "MAX_BITS",
    "BitapSearch",
    "FeatureUnavailableError",
    "FuseError",
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
    "__version__",
]
