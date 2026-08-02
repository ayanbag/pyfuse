"""Search keys — the port of ``src/tools/KeyStore.ts``.

Resolves the three shapes a user may write a key in (dotted string, path
list, or an object carrying a weight and/or custom getter) into a uniform
:class:`~fusejs.types.KeyObject`, and normalises the weights so they sum to 1.

Normalisation is why ``{"weight": 2}`` and ``{"weight": 200}`` behave the same
next to an unweighted key: only the *ratio* between weights is meaningful.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import InvalidKeyWeightError, MissingKeyPropertyError
from ..types import FuseOptionKey, KeyObject, KeyOption


def create_key_path(key: str | list[str]) -> list[str]:
    """Split a key into its path segments."""
    return list(key) if isinstance(key, (list, tuple)) else key.split(".")


def create_key_id(key: str | list[str]) -> str:
    """The canonical dotted identity of a key."""
    return ".".join(key) if isinstance(key, (list, tuple)) else key


def create_key(key: FuseOptionKey | Mapping[str, Any]) -> KeyObject:
    """Resolve any accepted key spelling into a :class:`KeyObject`.

    Raises:
        MissingKeyPropertyError: if an object-form key has no ``name``.
        InvalidKeyWeightError: if a weight is zero or negative.

    >>> create_key("author.name").path
    ['author', 'name']
    >>> create_key(KeyOption("title", weight=2)).weight
    2
    """
    if isinstance(key, (str, list, tuple)):
        return KeyObject(
            path=create_key_path(key),
            id=create_key_id(key),
            weight=1,
            src=key if isinstance(key, str) else list(key),
            get_fn=None,
        )

    # Object form: either a KeyOption or the plain mapping a JSON config
    # would deserialise to.
    if isinstance(key, KeyOption):
        name, weight, get_fn = key.name, key.weight, key.get_fn
    elif isinstance(key, Mapping):
        if "name" not in key:
            raise MissingKeyPropertyError("name")
        name = key["name"]
        weight = key.get("weight")
        get_fn = key.get("get_fn", key.get("getFn"))
    else:
        raise MissingKeyPropertyError("name")

    if weight is None:
        weight = 1
    elif weight <= 0:
        raise InvalidKeyWeightError(create_key_id(name))

    return KeyObject(
        path=create_key_path(name),
        id=create_key_id(name),
        weight=weight,
        src=name if isinstance(name, str) else list(name),
        get_fn=get_fn,
    )


class KeyStore:
    """The resolved, weight-normalised key set for a search.

    >>> store = KeyStore(["title", "author"])
    >>> [k.weight for k in store.keys()]
    [0.5, 0.5]
    """

    __slots__ = ("_key_map", "_keys")

    def __init__(self, keys: list[FuseOptionKey]) -> None:
        self._keys: list[KeyObject] = []
        self._key_map: dict[str, KeyObject] = {}

        total_weight = 0.0

        for key in keys:
            resolved = create_key(key)
            self._keys.append(resolved)
            self._key_map[resolved.id] = resolved
            total_weight += resolved.weight

        # Normalise so the weights sum to 1. An empty key list never divides.
        for resolved in self._keys:
            resolved.weight /= total_weight

    def get(self, key_id: str) -> KeyObject | None:
        """The key with this id, or ``None`` if there is no such key."""
        return self._key_map.get(key_id)

    def keys(self) -> list[KeyObject]:
        """Every key, in declaration order."""
        return self._keys

    def to_list(self) -> list[dict[str, Any]]:
        """A JSON-serialisable view, matching fuse.js's ``toJSON``."""
        return [
            {"path": k.path, "id": k.id, "weight": k.weight, "src": k.src}
            for k in self._keys
        ]
