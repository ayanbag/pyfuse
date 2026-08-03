"""Exception hierarchy for the port.

fuse.js throws a bare ``Error`` for every failure and distinguishes them only
by message text. Python callers expect to select failures with ``except``, so
each failure mode gets its own class here. The *messages* are kept
byte-identical to the originals in ``src/core/errorMessages.ts`` so a
differential run can compare them directly, and every class also inherits the
built-in Python exception a caller would reach for by instinct
(:class:`ValueError`, :class:`IndexError`, ...).
"""

from __future__ import annotations


class FuseError(Exception):
    """Base class for every error raised by this package."""


class FeatureUnavailableError(FuseError, RuntimeError):
    """A search mode was requested that this build does not provide."""


class PatternLengthError(FuseError, ValueError):
    """A Bitap pattern exceeded the machine-word limit."""

    def __init__(self, max_bits: int) -> None:
        super().__init__(f"Pattern length exceeds max of {max_bits}.")
        self.max_bits = max_bits


class InvalidKeyError(FuseError, ValueError):
    """A search key was malformed."""


class MissingKeyPropertyError(InvalidKeyError):
    """A key object was supplied without its required ``name``."""

    def __init__(self, name: str = "name") -> None:
        super().__init__(f"Missing {name} property in key")
        self.property_name = name


class InvalidKeyWeightError(InvalidKeyError):
    """A key was given a non-positive weight."""

    def __init__(self, key_id: str) -> None:
        super().__init__(
            f"Property 'weight' in key '{key_id}' must be a positive integer"
        )
        self.key_id = key_id


class InvalidQueryError(FuseError, ValueError):
    """A logical-search expression was not well formed."""

    @classmethod
    def for_key(cls, key: str) -> InvalidQueryError:
        """The value at ``key`` was not a pattern string."""
        return cls(f"Invalid value for key {key}")


class InvalidIndexTypeError(FuseError, TypeError):
    """``index=`` was passed something that is not a :class:`FuseIndex`."""

    def __init__(self) -> None:
        super().__init__("Incorrect 'index' type")


class InvalidDocIndexError(FuseError, IndexError):
    """A document index was negative, non-integral, or out of bounds."""

    def __init__(self) -> None:
        super().__init__(
            "Invalid doc index: must be a non-negative integer within "
            "the bounds of the docs array"
        )


# ── Message constants, kept verbatim for oracle comparison ─────────

EXTENDED_SEARCH_UNAVAILABLE = "Extended search is not available"
LOGICAL_SEARCH_UNAVAILABLE = "Logical search is not available"
TOKEN_SEARCH_UNAVAILABLE = "Token search is not available"

MATCH_TOKEN_SEARCH_UNSUPPORTED = (
    "Fuse.match does not support useTokenSearch: token search requires "
    "corpus-level statistics (df, fieldCount) that a one-off string "
    "comparison does not have. Use Fuse(...).search(...) instead."
)
