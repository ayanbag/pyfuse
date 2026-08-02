"""Tokenization — the port of ``src/search/token/analyzer.ts``.

The same analyzer runs at index-build and query time, so the two always agree
on what a term is.

The default tokenizer is fuse.js's ``/[\\p{L}\\p{M}\\p{N}_]+/gu``. Python's
:mod:`re` has no ``\\p{...}`` support and the third-party ``regex`` module
would break the zero-runtime-dependency guarantee, so the class is evaluated
directly against :func:`unicodedata.category`. Including Mark (``\\p{M}``) is
what keeps combining marks attached to their base letter — without it
Devanagari and NFD-normalised Latin shatter into fragments.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Final

from ...helpers.diacritics import strip_diacritics
from ...types import TokenizeFn

# Category first letters that make up `\p{L}` | `\p{M}` | `\p{N}`.
_TOKEN_CATEGORIES: Final = frozenset("LMN")

# Per-character category lookups are hot enough to be worth memoising: a
# corpus reuses the same few hundred characters over and over.
_token_char_cache: dict[str, bool] = {}


def _is_token_char(char: str) -> bool:
    """Whether ``char`` is a letter, mark, number, or underscore."""
    cached = _token_char_cache.get(char)
    if cached is None:
        cached = char == "_" or unicodedata.category(char)[0] in _TOKEN_CATEGORIES
        _token_char_cache[char] = cached
    return cached


def default_tokenize(text: str) -> list[str]:
    """Split ``text`` into maximal runs of letters, marks, numbers, and ``_``.

    >>> default_tokenize("Hello, world_2!")
    ['Hello', 'world_2']
    >>> default_tokenize("café naïve")
    ['café', 'naïve']
    """
    tokens: list[str] = []
    current: list[str] = []

    for char in text:
        if _is_token_char(char):
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []

    if current:
        tokens.append("".join(current))

    return tokens


def _resolve_tokenize(tokenize: re.Pattern[str] | TokenizeFn | None) -> TokenizeFn:
    """Normalise the ``tokenize`` option into a callable.

    A compiled pattern is always applied globally. JS distinguishes a regex
    with the ``g`` flag from one without (the latter yields only the first
    match, which the original warns about); Python patterns carry no such
    flag, so the global reading is the only sensible one.
    """
    if tokenize is None:
        return default_tokenize

    if isinstance(tokenize, re.Pattern):

        def by_pattern(text: str) -> list[str]:
            return [m.group(0) for m in tokenize.finditer(text)]

        return by_pattern

    if callable(tokenize):

        def by_callable(text: str) -> list[str]:
            result = tokenize(text)
            if not isinstance(result, list) or not all(
                isinstance(t, str) for t in result
            ):
                raise TypeError(
                    "tokenize must return list[str]; "
                    f"got {type(result).__name__}"
                )
            return result

        return by_callable

    raise TypeError(
        "tokenize must be a compiled pattern, a callable, or None; "
        f"got {type(tokenize).__name__}"
    )


class Analyzer:
    """Applies case folding, diacritic stripping, then tokenization."""

    __slots__ = ("_ignore_diacritics", "_is_case_sensitive", "_tokenize")

    def __init__(
        self,
        is_case_sensitive: bool = False,
        ignore_diacritics: bool = False,
        tokenize: re.Pattern[str] | TokenizeFn | None = None,
    ) -> None:
        self._is_case_sensitive = is_case_sensitive
        self._ignore_diacritics = ignore_diacritics
        self._tokenize = _resolve_tokenize(tokenize)

    def tokenize(self, text: str) -> list[str]:
        """Normalise and split ``text`` into terms."""
        if not self._is_case_sensitive:
            text = text.lower()
        if self._ignore_diacritics:
            text = strip_diacritics(text)
        return self._tokenize(text)


def create_analyzer(options: Any = None) -> Analyzer:
    """Build an analyzer from a :class:`~fusejs.config.FuseOptions`."""
    if options is None:
        return Analyzer()
    return Analyzer(
        is_case_sensitive=options.is_case_sensitive,
        ignore_diacritics=options.ignore_diacritics,
        tokenize=options.tokenize,
    )


__all__ = ["Analyzer", "create_analyzer", "default_tokenize"]
