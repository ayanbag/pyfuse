"""Logical query parsing — the port of ``src/core/queryParser.ts``.

Turns a logical expression into a tree of leaves (a key plus a pattern) and
operator nodes (``$and`` / ``$or``). A multi-key object is sugar for ``$and``,
so ``{"title": "a", "author": "b"}`` and
``{"$and": [{"title": "a"}, {"author": "b"}]}`` parse identically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..errors import InvalidQueryError
from ..tools.key_store import create_key_id
from ..types import Expression, Searcher
from .register import create_searcher


class LogicalOperator:
    """The recognised logical operators."""

    AND = "$and"
    OR = "$or"


_PATH = "$path"
_PATTERN = "$val"


@dataclass(slots=True)
class ParsedLeaf:
    """A single key/pattern pair. ``key_id`` is ``None`` for keyless terms."""

    key_id: str | None
    pattern: str
    searcher: Searcher | None = None


@dataclass(slots=True)
class ParsedOperator:
    """A ``$and`` / ``$or`` node."""

    operator: str
    children: list[ParsedNode] = field(default_factory=list)


ParsedNode = ParsedLeaf | ParsedOperator


def _object_keys(value: Any) -> list[str]:
    """``Object.keys`` for the values that reach the parser.

    Strings and lists enumerate as their integer indices — the reason a bare
    string handed to :func:`parse` decomposes into one leaf per character
    rather than being treated as a pattern. Faithful to the original, odd as
    it looks.
    """
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, (str, list, tuple)):
        return [str(i) for i in range(len(value))]
    return []


def _at(value: Any, key: str) -> Any:
    """``value[key]`` with JS's "missing is undefined" result."""
    if isinstance(value, dict):
        return value.get(key)
    if isinstance(value, (str, list, tuple)):
        return value[int(key)] if key.isdigit() and int(key) < len(value) else None
    return None


def _js_truthy(value: Any) -> bool:
    """JavaScript truthiness, which differs from Python's on collections.

    ``[]`` and ``{}`` are truthy in JS and falsy in Python. That matters
    directly here: ``{"$or": []}`` is a valid (empty) operator node to
    fuse.js, but Python's ``bool([])`` would classify it as a leaf and then
    reject it for having a non-string pattern.
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not math.isnan(value)
    # Arrays, objects and everything else are truthy in JS.
    return True


def _is_expression(query: Any) -> bool:
    return _js_truthy(_at(query, LogicalOperator.AND)) or _js_truthy(
        _at(query, LogicalOperator.OR)
    )


def _is_path(query: Any) -> bool:
    return _js_truthy(_at(query, _PATH))


def _is_leaf(query: Any) -> bool:
    return not isinstance(query, (list, tuple)) and not _is_expression(query)


def _convert_to_explicit(query: Any) -> dict[str, Any]:
    """Rewrite a multi-key object as an explicit ``$and``."""
    return {
        LogicalOperator.AND: [{key: _at(query, key)} for key in _object_keys(query)]
    }


def _key_label(key: Any) -> str:
    """How the original renders a key inside an error message.

    JS interpolates an array as its comma-joined elements, so a failed
    ``$path`` of ``["a", "b"]`` reports ``Invalid value for key a,b``.
    """
    if isinstance(key, (list, tuple)):
        return ",".join(str(k) for k in key)
    return str(key)


def parse(query: Expression, options: Any = None, *, auto: bool = True) -> ParsedNode:
    """Parse a logical expression into a searchable tree.

    With ``auto`` (the default) each leaf is given a ready-to-use searcher;
    pass ``auto=False`` to inspect the tree shape alone.

    Raises:
        InvalidQueryError: if a key's value is not a pattern string.

    >>> node = parse({"$or": [{"title": "a"}, {"title": "b"}]}, auto=False)
    >>> node.operator, [c.key_id for c in node.children]
    ('$or', ['title', 'title'])
    """

    def next_node(node: Any) -> ParsedNode:
        # A keyless string term searches across every key.
        if isinstance(node, str):
            leaf = ParsedLeaf(key_id=None, pattern=node)
            if auto:
                leaf.searcher = create_searcher(node, options)
            return leaf

        keys = _object_keys(node)
        is_query_path = _is_path(node)

        if not is_query_path and len(keys) > 1 and not _is_expression(node):
            return next_node(_convert_to_explicit(node))

        if _is_leaf(node):
            key = _at(node, _PATH) if is_query_path else (keys[0] if keys else None)
            pattern = _at(node, _PATTERN) if is_query_path else _at(node, str(key))

            if key is None or not isinstance(pattern, str):
                raise InvalidQueryError.for_key(_key_label(key))

            leaf = ParsedLeaf(key_id=create_key_id(key), pattern=pattern)
            if auto:
                leaf.searcher = create_searcher(pattern, options)
            return leaf

        operator = ParsedOperator(operator=keys[0] if keys else LogicalOperator.AND)

        for key in keys:
            value = _at(node, key)
            if isinstance(value, (list, tuple)):
                for item in value:
                    operator.children.append(next_node(item))

        return operator

    if not _is_expression(query):
        query = _convert_to_explicit(query)

    return next_node(query)


__all__ = [
    "LogicalOperator",
    "ParsedLeaf",
    "ParsedNode",
    "ParsedOperator",
    "parse",
]
