"""Bitap score formula — the port of ``src/search/bitap/computeScore.ts``.

Kept as a standalone, documented function even though the hot loop in
:mod:`~fusejs.search.bitap.search` inlines the same arithmetic, exactly as the
original does. The arithmetic order below is load-bearing: float64 addition is
not associative, so ``accuracy + proximity / distance`` and any algebraically
equivalent rearrangement produce different last bits, and those bits reach the
user as a different score.
"""

from __future__ import annotations

from ...config import FuseOptions


def compute_score(
    pattern: str,
    *,
    errors: int = 0,
    current_location: int = 0,
    expected_location: int = 0,
    distance: int | None = None,
    ignore_location: bool | None = None,
) -> float:
    """Score a candidate match: lower is better, ``0.0`` is exact.

    The score combines *accuracy* (the error rate against the pattern length)
    with *proximity* (how far the match drifted from where it was expected).

    >>> compute_score("abc", errors=0, current_location=0, expected_location=0)
    0.0
    """
    defaults = FuseOptions()
    if distance is None:
        distance = defaults.distance
    if ignore_location is None:
        ignore_location = defaults.ignore_location

    accuracy = errors / len(pattern)

    if ignore_location:
        return accuracy

    proximity = abs(expected_location - current_location)

    if not distance:
        # Dodge divide-by-zero: with no distance budget, any drift at all is
        # a total miss.
        return 1.0 if proximity else accuracy

    return accuracy + proximity / distance
