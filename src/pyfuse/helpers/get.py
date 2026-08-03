"""Nested-path value resolution — the port of ``src/helpers/get.ts``.

Resolves a dotted or list path against a document, fanning out across any
arrays encountered on the way so that ``"authors.name"`` collects a name from
every author. Array provenance is preserved as :class:`PathValue` so match
results can report which element matched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .type_guards import is_defined, is_list, is_scalar, to_string


@dataclass(slots=True, frozen=True)
class PathValue:
    """A value found inside an array, tagged with its position.

    fuse.js returns bare ``{v, i}`` object literals here; a frozen dataclass
    keeps the same information while making the two possible shapes of a
    resolved value (``str`` vs. array element) statically distinguishable.

    ``v`` is intentionally *un*-coerced: the original only stringifies on the
    scalar-leaf branch, and defers coercion of array elements to index build
    time (``FuseIndex._create_object_record``). Coercing early here would
    stringify values the original leaves alone.
    """

    v: Any
    i: int


def _prop(obj: Any, key: str) -> Any:
    """One step of property access, with JS's "missing is undefined" result.

    JS returns ``undefined`` for any absent property on any value; Python
    raises four different exceptions depending on the container. Mapping all
    of them to ``None`` keeps traversal total, so a partially-shaped document
    yields the fields it does have instead of failing the whole search.

    Attribute access is a deliberate extension over the original: Python
    documents are as likely to be dataclasses or ORM rows as dicts.
    """
    if isinstance(obj, dict):
        return obj.get(key)
    if isinstance(obj, (list, tuple)):
        # JS array indices are string property names: `arr["0"]` is `arr[0]`.
        if key.isdigit():
            index = int(key)
            return obj[index] if index < len(obj) else None
        return None
    if isinstance(obj, (str, bytes, int, float, bool)):
        # Scalars have no searchable properties.
        return None
    return getattr(obj, key, None)


def get(obj: Any, path: str | list[str]) -> Any:
    """Resolve ``path`` against ``obj``.

    Returns a single string when the path resolves to one scalar, a list of
    values when any array was traversed (each array element becoming a
    :class:`PathValue`), or ``None`` when nothing was found.

    >>> get({"title": "Old Man's War"}, "title")
    "Old Man's War"
    >>> get({"author": {"name": "Scalzi"}}, "author.name")
    'Scalzi'
    >>> get({"tags": [{"n": "a"}, {"n": "b"}]}, "tags.n")
    [PathValue(v='a', i=0), PathValue(v='b', i=1)]
    """
    collected: list[Any] = []
    # Sticky: once any array is traversed the result stays a list, even if
    # that array turned out to be empty. Callers rely on the arity, not the
    # contents, to decide whether a key is multi-valued.
    saw_array = False

    def deep_get(
        node: Any, keys: list[str], index: int, array_index: int | None
    ) -> None:
        nonlocal saw_array

        if not is_defined(node):
            return

        if index >= len(keys) or not keys[index]:
            # Path exhausted — this is the value we were after.
            collected.append(
                PathValue(node, array_index) if array_index is not None else node
            )
            return

        key = keys[index]
        value = _prop(node, key)

        if not is_defined(value):
            return

        if index == len(keys) - 1 and is_scalar(value):
            collected.append(
                PathValue(to_string(value), array_index)
                if array_index is not None
                else to_string(value)
            )
        elif is_list(value):
            saw_array = True
            for i, item in enumerate(value):
                deep_get(item, keys, index + 1, i)
        elif keys:
            deep_get(value, keys, index + 1, array_index)

    deep_get(obj, path.split(".") if isinstance(path, str) else list(path), 0, None)

    if saw_array:
        return collected
    return collected[0] if collected else None
