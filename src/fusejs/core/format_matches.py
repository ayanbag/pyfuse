"""Match formatting — the port of ``src/core/formatMatches.ts``."""

from __future__ import annotations

from ..types import FuseResultMatch, InternalResult


def format_matches(result: InternalResult) -> list[FuseResultMatch]:
    """Public match records for one result, dropping matches with no indices.

    A match with no index ranges carries no information a caller could
    highlight, so it is omitted rather than reported as an empty highlight.
    """
    matches: list[FuseResultMatch] = []

    for match in result.matches:
        if not match.indices:
            continue

        formatted = FuseResultMatch(indices=match.indices, value=match.value)

        if match.key is not None:
            # `key.id` is the canonical dotted identity; `key.src` is the raw
            # user input and may be a list.
            formatted.key = match.key.id

        # `idx` is set only for values that came from an array. The original
        # tests `match.idx > -1`, which is false for `undefined`.
        if match.idx is not None and match.idx > -1:
            formatted.ref_index = match.idx

        matches.append(formatted)

    return matches
