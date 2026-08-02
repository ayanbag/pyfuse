"""Match-mask to ranges — the port of ``src/search/bitap/convertMaskToIndices.ts``."""

from __future__ import annotations

from ...types import RangeTuple


def convert_mask_to_indices(
    match_mask: list[int], min_match_char_length: int = 1
) -> list[RangeTuple]:
    """Collapse a per-character hit mask into inclusive ranges.

    Runs shorter than ``min_match_char_length`` are dropped.

    >>> convert_mask_to_indices([1, 1, 0, 1, 1, 1])
    [(0, 1), (3, 5)]
    >>> convert_mask_to_indices([1, 1, 0, 1, 1, 1], 3)
    [(3, 5)]
    """
    indices: list[RangeTuple] = []
    start = -1
    length = len(match_mask)

    for i in range(length):
        if match_mask[i] and start == -1:
            start = i
        elif not match_mask[i] and start != -1:
            end = i - 1
            if end - start + 1 >= min_match_char_length:
                indices.append((start, end))
            start = -1

    # Close a run that reaches the end of the mask. The original indexes
    # `matchmask[i - 1]`, which for an empty mask is `matchmask[-1]` —
    # `undefined`, hence falsy, in JS but the *last element* in Python. The
    # explicit length guard keeps an empty mask from wrapping around.
    if length and match_mask[length - 1] and length - start >= min_match_char_length:
        indices.append((start, length - 1))

    return indices
