"""Value predicates mirroring ``src/helpers/typeGuards.ts``.

Most of the TypeScript original exists to work around JS's lack of real types
(``isString``, ``isArray``, ``isNumber``) and has no Python counterpart worth
writing — ``isinstance`` says it better. What survives here are the three
predicates that encode *fuse.js semantics* rather than JS mechanics.
"""

from __future__ import annotations

from typing import Any, TypeGuard

from .._js import js_str, js_trim


def is_defined(value: object) -> bool:
    """``value !== undefined && value !== null``.

    Deliberately not ``bool(value)``: ``0``, ``False`` and ``""`` are defined
    values that fuse.js indexes, and collapsing them to "missing" would drop
    real fields.
    """
    return value is not None


def is_scalar(value: object) -> TypeGuard[str | int | float | bool]:
    """Whether a leaf value is one fuse.js stringifies and indexes.

    fuse.js accepts ``string | number | boolean | bigint`` at the end of a key
    path. Python's ``int`` covers both JS ``number`` and ``bigint``; ``bool``
    is checked implicitly since it subclasses ``int``.
    """
    return isinstance(value, (str, int, float, bool))


def is_list(value: object) -> TypeGuard[list[Any]]:
    """Whether a value is an array for indexing purposes.

    Tuples count: they are the natural immutable Python spelling of a JS
    array, and refusing them would make read-only documents un-indexable.
    Strings and bytes are excluded even though they are sequences.
    """
    return isinstance(value, (list, tuple))


def to_string(value: object) -> str:
    """``toString``: ``None`` becomes ``""``, everything else stringifies."""
    return "" if value is None else js_str(value)


def is_blank(value: str) -> bool:
    """Whether a string is empty or only whitespace, per JS ``trim``."""
    return not js_trim(value)
