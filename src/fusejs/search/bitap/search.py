"""The Bitap scan — the port of ``src/search/bitap/search.ts``.

This is the scoring heart of the engine and the single most parity-sensitive
function in the port. Two things are deliberately preserved from the original
even where Python would prefer otherwise:

* **Arithmetic order.** ``accuracy + proximity / distance`` is evaluated in
  exactly that order. float64 addition is not associative, so factoring the
  expression differently changes the low bits of every score.
* **Loop shape.** The descending scan, the binary search for the error
  budget, and the early exits are structurally identical to the original. A
  more Pythonic rewrite would change *which* match wins a tie, which is
  user-visible as a different result ordering.

The 32-bit bit-array arithmetic is emulated explicitly — see
:mod:`fusejs._js` for why.
"""

from __future__ import annotations

from ..._js import UINT32_MASK
from ...config import MAX_BITS
from ...errors import PatternLengthError
from ...types import SearchResult
from .convert_mask_to_indices import convert_mask_to_indices


def search(
    text: str,
    pattern: str,
    pattern_alphabet: dict[str, int],
    *,
    location: int = 0,
    distance: int = 100,
    threshold: float = 0.6,
    find_all_matches: bool = False,
    min_match_char_length: int = 1,
    include_matches: bool = False,
    ignore_location: bool = False,
) -> SearchResult:
    """Find the best fuzzy occurrence of ``pattern`` in ``text``.

    ``pattern_alphabet`` must come from
    :func:`~fusejs.search.bitap.create_pattern_alphabet.create_pattern_alphabet`
    for this same pattern.

    Raises:
        PatternLengthError: if the pattern exceeds the 32-character machine
            word limit. Callers split longer patterns into chunks first.
        ValueError: if the pattern is empty. The original loops forever on
            this input; failing fast is strictly more useful.
    """
    if len(pattern) > MAX_BITS:
        raise PatternLengthError(MAX_BITS)
    if not pattern:
        raise ValueError("pattern must not be empty")

    pattern_len = len(pattern)
    text_len = len(text)
    # Clamp the anchor into the text; `location` may point past the end.
    expected_location = max(0, min(location, text_len))
    # Highest score beyond which we give up.
    current_threshold = threshold
    # Is there a nearby exact match? (speedup)
    best_location = expected_location

    def calc_score(errors: int, current_location: int) -> float:
        """Inlined :func:`compute_score`; see that module for the rationale."""
        accuracy = errors / pattern_len
        if ignore_location:
            return accuracy
        proximity = abs(expected_location - current_location)
        if not distance:
            return 1.0 if proximity else accuracy
        return accuracy + proximity / distance

    # Only compute the match mask when someone will look at it.
    compute_matches = min_match_char_length > 1 or include_matches
    match_mask = [0] * text_len if compute_matches else []

    # Get all exact matches first — a cheap way to tighten the threshold
    # before the expensive scan.
    while (index := text.find(pattern, best_location)) > -1:
        current_threshold = min(calc_score(0, index), current_threshold)
        best_location = index + pattern_len

        if compute_matches:
            for i in range(pattern_len):
                match_mask[index + i] = 1

    # Reset the best location.
    best_location = -1

    last_bit_arr: list[int] = []
    final_score = 1.0
    best_errors = 0
    bin_max = pattern_len + text_len

    mask = 1 << (pattern_len - 1)

    for i in range(pattern_len):
        # Scan for the best match; each iteration allows one more error.
        # Binary-search how far from the expected location we may stray at
        # this error level.
        bin_min = 0
        bin_mid = bin_max

        while bin_min < bin_mid:
            if calc_score(i, expected_location + bin_mid) <= current_threshold:
                bin_min = bin_mid
            else:
                bin_max = bin_mid
            bin_mid = (bin_max - bin_min) // 2 + bin_min

        # Use this iteration's result as the ceiling for the next.
        bin_max = bin_mid

        start = max(1, expected_location - bin_mid + 1)
        finish = (
            text_len
            if find_all_matches
            else min(expected_location + bin_mid, text_len) + pattern_len
        )

        bit_arr = [0] * (finish + 2)
        bit_arr[finish + 1] = (1 << i) - 1

        # `finish` is non-increasing across iterations, so the previous bit
        # array is always long enough — but a plugin-supplied option set
        # could break that, and JS would read `undefined` (i.e. 0) rather
        # than throw. Padding keeps the same forgiving behaviour.
        if i and len(last_bit_arr) < finish + 2:
            last_bit_arr.extend([0] * (finish + 2 - len(last_bit_arr)))

        # A `while` rather than a `range`: the loop body narrows `start` when
        # it finds a better match, and the original re-tests that bound on
        # every iteration. A `range` would freeze the original bound and keep
        # scanning positions the original skips.
        j = finish
        while j >= start:
            current_location = j - 1
            # Past the end of the text the original reads `undefined` from
            # both the string and the alphabet, which ANDs to 0.
            char_match = (
                pattern_alphabet.get(text[current_location], 0)
                if current_location < text_len
                else 0
            )

            # First pass: exact match.
            bit_arr[j] = (((bit_arr[j + 1] << 1) | 1) & char_match) & UINT32_MASK

            # Subsequent passes: fuzzy match.
            if i:
                bit_arr[j] |= (
                    ((last_bit_arr[j + 1] | last_bit_arr[j]) << 1)
                    | 1
                    | last_bit_arr[j + 1]
                ) & UINT32_MASK

            if bit_arr[j] & mask:
                final_score = calc_score(i, current_location)

                # This match will almost certainly beat any existing one.
                # Check anyway.
                if final_score <= current_threshold:
                    current_threshold = final_score
                    best_location = current_location
                    best_errors = i

                    # Already passed the anchor — downhill from here on in.
                    if best_location <= expected_location:
                        break

                    # Don't stray further than we already have.
                    start = max(1, 2 * expected_location - best_location)

            j -= 1

        # No hope for a better match at greater error levels.
        if calc_score(i + 1, expected_location) > current_threshold:
            break

        last_bit_arr = bit_arr

    # Fill the match mask across the matched window only. Bitap anchors a
    # match at `best_location`, spanning `pattern_len` characters plus up to
    # `best_errors` more when the errors were text-side insertions. Marking
    # alphabet positions only in that window keeps highlight indices honest
    # about what actually matched, rather than every alphabet character the
    # scan happened to visit.
    if compute_matches and best_location >= 0:
        match_end = min(text_len - 1, best_location + pattern_len - 1 + best_errors)
        for k in range(best_location, match_end + 1):
            if pattern_alphabet.get(text[k]):
                match_mask[k] = 1

    result = SearchResult(
        is_match=best_location >= 0,
        # Treat exact matches (score 0) as "almost" exact so they stay
        # distinguishable from "no score yet" downstream.
        score=max(0.001, final_score),
    )

    if compute_matches:
        indices = convert_mask_to_indices(match_mask, min_match_char_length)
        if not indices:
            result.is_match = False
        elif include_matches:
            result.indices = indices

    return result
