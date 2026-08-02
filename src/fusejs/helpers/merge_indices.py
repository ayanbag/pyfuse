"""Merge overlapping match ranges — the port of ``src/helpers/mergeIndices.ts``."""

from __future__ import annotations

from ..types import RangeTuple


def merge_indices(indices: list[RangeTuple]) -> list[RangeTuple]:
    """Coalesce overlapping and adjacent ``(start, end)`` ranges.

    Adjacent ranges merge too — ``(0, 2)`` and ``(3, 5)`` become ``(0, 5)`` —
    so a highlight rendered from the result has no one-character gaps.

    Unlike the original, this neither sorts the caller's list in place nor
    mutates the range objects it returns. fuse.js can get away with mutation
    because its ranges are freshly-allocated arrays; here a ``RangeTuple`` is
    an immutable tuple that may be shared between the merged output and a
    ``SearchResult`` still held by a caller.

    >>> merge_indices([(5, 7), (0, 2), (3, 4)])
    [(0, 7)]
    """
    if len(indices) <= 1:
        return indices

    ordered = sorted(indices)
    merged: list[RangeTuple] = [ordered[0]]

    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged
