"""Result formatting — the port of ``src/core/format.ts``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..types import FuseResult, InternalResult
from .format_matches import format_matches


def format_results(
    results: list[InternalResult],
    docs: Sequence[Any],
    *,
    include_matches: bool = False,
    include_score: bool = False,
) -> list[FuseResult]:
    """Turn internal results into the public result shape."""
    formatted: list[FuseResult] = []

    for result in results:
        data = FuseResult(item=docs[result.idx], ref_index=result.idx)

        if include_matches:
            data.matches = format_matches(result)
        if include_score:
            data.score = result.score

        formatted.append(data)

    return formatted
