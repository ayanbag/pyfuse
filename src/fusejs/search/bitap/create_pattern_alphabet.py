"""Pattern alphabet — the port of ``src/search/bitap/createPatternAlphabet.ts``."""

from __future__ import annotations


def create_pattern_alphabet(pattern: str) -> dict[str, int]:
    """Map each character of ``pattern`` to a bitmask of its positions.

    Bit ``len(pattern) - i - 1`` is set for a character appearing at index
    ``i``, so the mask reads left-to-right as the pattern does. A character
    occurring more than once accumulates a bit per occurrence.

    >>> create_pattern_alphabet("abc") == {"a": 0b100, "b": 0b010, "c": 0b001}
    True
    >>> create_pattern_alphabet("aba") == {"a": 0b101, "b": 0b010}
    True
    """
    mask: dict[str, int] = {}
    length = len(pattern)

    for i, char in enumerate(pattern):
        mask[char] = mask.get(char, 0) | (1 << (length - i - 1))

    return mask
