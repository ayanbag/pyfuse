"""Score combination — the port of ``src/core/computeScore.ts``.

Combines the per-field match scores of one document into a single relevance
score, folding in key weight and field-length norm. The result is a *product*
of powers, not a sum: a document matching two keys well beats one matching a
single key perfectly.

The arithmetic order is preserved exactly from the original. So is the
``Number.EPSILON`` substitution, which is not a rounding detail but a
load-bearing one — see :func:`compute_score_single`.
"""

from __future__ import annotations

from .._js import EPSILON
from ..types import InternalResult, MatchScore


def compute_score_single(
    matches: list[MatchScore], *, ignore_field_norm: bool = False
) -> float:
    """The combined relevance score for one document's matches.

    Lower is better; ``0`` would be a perfect match on every key.

    A perfect (zero) score on a *weighted* key is substituted with
    ``Number.EPSILON``. Without it, one exact match would drive the whole
    product to zero and flatten every distinction between documents that also
    matched it — the substitution keeps a perfect hit dominant without making
    it absorbing.
    """
    total_score = 1.0

    for match in matches:
        weight = match.key.weight if match.key is not None else None

        # `score == 0 and weight` mirrors the original's truthiness: a null
        # key, or a zero weight, falls through to the raw score.
        base = EPSILON if (match.score == 0 and weight) else match.score
        exponent = (weight or 1) * (1 if ignore_field_norm else match.norm)

        total_score *= base**exponent

    return total_score


def compute_score(
    results: list[InternalResult], *, ignore_field_norm: bool = False
) -> None:
    """Populate ``score`` on every result, in place."""
    for result in results:
        result.score = compute_score_single(
            result.matches, ignore_field_norm=ignore_field_norm
        )
