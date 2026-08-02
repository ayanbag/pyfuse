"""Field-length norm — the port of ``src/tools/fieldNorm.ts``.

The norm is what makes a hit in a three-word title outrank the same hit in a
three-paragraph description: shorter fields weigh more. It is quantised to
three decimals to keep serialised indexes small, and that quantisation runs
through :func:`~fusejs._js.js_round` — Python's banker's rounding would move
the result by one unit in the last place for every exact ``.5``, and the norm
multiplies into every score.
"""

from __future__ import annotations

from .._js import js_round


def _is_word_separator(code: int) -> bool:
    """Whether a character code separates words for norm purposes.

    Deliberately narrower than the full Unicode whitespace set: the common
    ASCII whitespace (tab, LF, VT, FF, CR, space) plus NBSP. The original
    skips rarer separators like the ideographic and en/em spaces on the
    grounds that the heuristic is too coarse to benefit from the distinction.
    """
    return (9 <= code <= 13) or code in (32, 160)


class FieldNorm:
    """Computes and caches the field-length norm for text values.

    The cache is keyed on the *token count*, not the text, so every
    three-word field in a corpus shares one entry.

    >>> norm = FieldNorm()
    >>> norm.get("one")
    1.0
    >>> norm.get("one two three four")
    0.5
    """

    __slots__ = ("_cache", "_m", "_weight")

    def __init__(self, weight: float = 1.0, mantissa: int = 3) -> None:
        self._weight = weight
        self._m = float(10**mantissa)
        self._cache: dict[int, float] = {}

    def get(self, value: str) -> float:
        """The norm for ``value`` — higher for shorter fields."""
        # Count word *starts* (separator-or-start followed by non-separator)
        # rather than boundaries. Counting boundaries over-counts by one for
        # every leading or trailing separator.
        num_tokens = 0
        in_word = False
        for char in value:
            if not _is_word_separator(ord(char)):
                if not in_word:
                    num_tokens += 1
                    in_word = True
            else:
                in_word = False

        # Empty and all-whitespace fields have no real words; treat them as
        # one token so the formula never divides by zero.
        if num_tokens == 0:
            num_tokens = 1

        cached = self._cache.get(num_tokens)
        if cached is not None:
            return cached

        # Default function is 1/sqrt(x); `weight` makes the exponent variable.
        norm = js_round(self._m / num_tokens ** (0.5 * self._weight)) / self._m
        self._cache[num_tokens] = norm
        return norm

    def clear(self) -> None:
        """Drop the cache."""
        self._cache.clear()
