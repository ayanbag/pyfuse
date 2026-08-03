"""Searcher registry — the port of ``src/core/register.ts``.

Registration order is significant: :func:`create_searcher` returns the first
registered searcher whose ``condition`` accepts the query, and falls back to
Bitap when none do. ``ExtendedSearch`` is registered before ``TokenSearch``,
so a query with both options enabled goes to extended search — matching
fuse.js's ``entry.ts``.
"""

from __future__ import annotations

from typing import Any

from ..config import FuseOptions
from ..search.bitap import BitapSearch
from ..types import Searcher, SearcherFactory

_registered: list[SearcherFactory] = []


def register(*searchers: SearcherFactory) -> None:
    """Register searcher classes, in priority order."""
    _registered.extend(searchers)


def registered() -> list[SearcherFactory]:
    """The current registry, in priority order."""
    return list(_registered)


def clear_registry() -> None:
    """Empty the registry. Intended for tests."""
    _registered.clear()


def create_searcher(pattern: str, options: FuseOptions | Any) -> Searcher:
    """Build the searcher that should handle ``pattern`` under ``options``."""
    for factory in _registered:
        if factory.condition(pattern, options):
            return factory(pattern, options)

    return BitapSearch(pattern, options)
