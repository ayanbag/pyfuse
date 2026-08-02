"""Document indexing — the port of ``src/tools/FuseIndex.ts``.

Walks every document once, resolves every key path, and stores the resulting
text alongside its field-length norm. Searching then runs over these records
rather than the original documents, so path resolution and norm computation
are paid once at build time instead of once per query.

The serialised form (:meth:`FuseIndex.to_dict`) is deliberately wire-identical
to fuse.js's ``toJSON`` — including the ``$`` field name and the terse
``v``/``i``/``n`` record keys — so an index built by either engine can be
loaded by the other.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from typing import Any

from ..config import FuseOptions
from ..errors import InvalidDocIndexError
from ..helpers.get import PathValue
from ..helpers.type_guards import is_blank, is_defined, is_list, to_string
from ..types import FuseOptionKey, GetFn, IndexRecord, KeyObject, SubRecord
from .field_norm import FieldNorm
from .key_store import create_key


def _is_valid_doc_index(value: object) -> bool:
    """A document index must be a non-negative, non-boolean integer."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class FuseIndex:
    """The searchable record set built from a document collection."""

    __slots__ = ("_keys_map", "docs", "get_fn", "is_created", "keys", "norm", "records")

    def __init__(
        self,
        get_fn: GetFn | None = None,
        field_norm_weight: float | None = None,
    ) -> None:
        defaults = FuseOptions()
        self.norm = FieldNorm(
            defaults.field_norm_weight if field_norm_weight is None
            else field_norm_weight,
            3,
        )
        self.get_fn: GetFn = defaults.get_fn if get_fn is None else get_fn
        self.is_created = False
        self.docs: Sequence[Any] = []
        self.records: list[IndexRecord] = []
        self.keys: list[KeyObject] = []
        self._keys_map: dict[str, int] = {}

    # ── Setup ──────────────────────────────────────────────────────

    def set_sources(self, docs: Sequence[Any] | None = None) -> None:
        """Point the index at a document collection."""
        self.docs = [] if docs is None else docs

    def set_index_records(self, records: list[IndexRecord] | None = None) -> None:
        """Install pre-built records, bypassing indexing."""
        self.records = [] if records is None else records

    def set_keys(self, keys: list[KeyObject] | None = None) -> None:
        """Install the resolved key set and its id-to-position map."""
        self.keys = [] if keys is None else keys
        self._keys_map = {key.id: idx for idx, key in enumerate(self.keys)}

    def create(self) -> None:
        """Build records for every document. Idempotent."""
        if self.is_created or not self.docs:
            return

        self.is_created = True

        if isinstance(self.docs[0], str):
            # Collection of plain strings.
            self.records = [
                record
                for i, doc in enumerate(self.docs)
                if (record := self._create_string_record(doc, i)) is not None
            ]
        else:
            # Collection of objects.
            self.records = [
                self._create_object_record(doc, i) for i, doc in enumerate(self.docs)
            ]

        self.norm.clear()

    # ── Mutation ───────────────────────────────────────────────────

    def add(self, doc: Any, doc_index: int) -> IndexRecord | None:
        """Append a record for ``doc`` at ``doc_index``.

        Returns the appended record, or ``None`` when ``doc`` is a blank
        string — those produce no record. Callers use the return value to
        gate downstream bookkeeping (the inverted index must not be touched
        when nothing was appended).

        Raises:
            InvalidDocIndexError: if ``doc_index`` is not a non-negative int.
        """
        if not _is_valid_doc_index(doc_index):
            raise InvalidDocIndexError

        if isinstance(doc, str):
            record = self._create_string_record(doc, doc_index)
            if record is not None:
                self.records.append(record)
            return record

        record = self._create_object_record(doc, doc_index)
        self.records.append(record)
        return record

    def remove_at(self, idx: int) -> None:
        """Remove the record for the document at source index ``idx``.

        Blank string documents have no record; passing such an index is a
        no-op for removal, but later records still need their ``i``
        decremented to track the caller's parallel splice of ``docs``.

        Raises:
            InvalidDocIndexError: if ``idx`` is not a non-negative int.
        """
        if not _is_valid_doc_index(idx):
            raise InvalidDocIndexError

        # Records are typically ordered by `.i`, but the algorithm must not
        # depend on it — indexes loaded via `parse_index` may arrive in any
        # order.
        for position, record in enumerate(self.records):
            if record.i == idx:
                del self.records[position]
                break

        for record in self.records:
            if record.i > idx:
                record.i -= 1

    def remove_all(self, indices: Sequence[int]) -> None:
        """Remove records for several documents and renumber the survivors.

        Invalid entries are dropped silently rather than raising: the natural
        use case is "here is a list of matched document indices", and an
        asymmetric throw-vs-no-op would be more surprising than a clean
        filter.
        """
        to_remove = {v for v in indices if _is_valid_doc_index(v)}

        if not to_remove:
            return

        self.records = [r for r in self.records if r.i not in to_remove]

        ordered = sorted(to_remove)
        for record in self.records:
            # Shift down by the count of removed indices strictly below this
            # record's own index — a binary search, so removing k documents
            # from n records stays O(n log k) rather than O(nk).
            record.i -= bisect_left(ordered, record.i)

    # ── Lookup ─────────────────────────────────────────────────────

    def get_value_for_item_at_key_id(
        self, item: dict[int, SubRecord | list[SubRecord]], key_id: str
    ) -> SubRecord | list[SubRecord] | None:
        """The indexed value(s) for one key of one record."""
        key_index = self._keys_map.get(key_id)
        if key_index is None:
            return None
        return item.get(key_index)

    def size(self) -> int:
        """How many records the index holds."""
        return len(self.records)

    # ── Record construction ────────────────────────────────────────

    def _create_string_record(self, doc: str, doc_index: int) -> IndexRecord | None:
        """A record for a plain-string document, or ``None`` if it is blank."""
        if not is_defined(doc) or is_blank(doc):
            return None
        return IndexRecord(i=doc_index, v=doc, n=self.norm.get(doc))

    def _create_object_record(self, doc: Any, doc_index: int) -> IndexRecord:
        """A record for an object document: one entry per resolvable key."""
        fields: dict[int, SubRecord | list[SubRecord]] = {}
        record = IndexRecord(i=doc_index, fields=fields)

        for key_index, key in enumerate(self.keys):
            value = key.get_fn(doc) if key.get_fn else self.get_fn(doc, key.path)

            if not is_defined(value):
                continue

            if is_list(value):
                sub_records: list[SubRecord] = []

                for i, item in enumerate(value):
                    if not is_defined(item):
                        continue

                    if isinstance(item, str):
                        # A custom get_fn returning a plain string array.
                        if not is_blank(item):
                            sub_records.append(
                                SubRecord(v=item, i=i, n=self.norm.get(item))
                            )
                    elif isinstance(item, PathValue) and is_defined(item.v):
                        # The default getter returns positioned values, so
                        # the original array index survives into results.
                        text = item.v if isinstance(item.v, str) else to_string(item.v)
                        if not is_blank(text):
                            sub_records.append(
                                SubRecord(v=text, i=item.i, n=self.norm.get(text))
                            )

                # Assigned even when empty, so key positions stay aligned.
                fields[key_index] = sub_records

            elif isinstance(value, str) and not is_blank(value):
                fields[key_index] = SubRecord(v=value, n=self.norm.get(value))

        return record

    # ── Serialisation ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable index, wire-compatible with fuse.js."""
        return {
            "keys": [
                {"path": k.path, "id": k.id, "weight": k.weight, "src": k.src}
                for k in self.keys
            ],
            "records": [_record_to_dict(r) for r in self.records],
        }


def _record_to_dict(record: IndexRecord) -> dict[str, Any]:
    """One record in fuse.js's on-the-wire shape."""
    if record.fields is None:
        return {"v": record.v, "i": record.i, "n": record.n}

    fields: dict[str, Any] = {}
    for key_index, value in record.fields.items():
        if isinstance(value, list):
            fields[str(key_index)] = [_sub_to_dict(s) for s in value]
        else:
            fields[str(key_index)] = _sub_to_dict(value)

    return {"i": record.i, "$": fields}


def _sub_to_dict(sub: SubRecord) -> dict[str, Any]:
    out: dict[str, Any] = {"v": sub.v, "n": sub.n}
    if sub.i is not None:
        out["i"] = sub.i
    return out


def _record_from_dict(data: dict[str, Any]) -> IndexRecord:
    """Rebuild a record from fuse.js's on-the-wire shape."""
    if "$" not in data:
        return IndexRecord(i=data["i"], v=data.get("v"), n=data.get("n"))

    fields: dict[int, SubRecord | list[SubRecord]] = {}
    for key_index, value in data["$"].items():
        if isinstance(value, list):
            fields[int(key_index)] = [
                SubRecord(v=s["v"], n=s["n"], i=s.get("i")) for s in value
            ]
        else:
            fields[int(key_index)] = SubRecord(
                v=value["v"], n=value["n"], i=value.get("i")
            )

    return IndexRecord(i=data["i"], fields=fields)


def create_index(
    keys: list[FuseOptionKey],
    docs: Sequence[Any],
    get_fn: GetFn | None = None,
    field_norm_weight: float | None = None,
) -> FuseIndex:
    """Build an index up front, to reuse across several :class:`Fuse` instances.

    >>> index = create_index(["title"], [{"title": "Old Man's War"}])
    >>> index.size()
    1
    """
    index = FuseIndex(get_fn=get_fn, field_norm_weight=field_norm_weight)
    index.set_keys([create_key(k) for k in keys])
    index.set_sources(docs)
    index.create()
    return index


def parse_index(
    data: dict[str, Any],
    get_fn: GetFn | None = None,
    field_norm_weight: float | None = None,
) -> FuseIndex:
    """Load a previously serialised index, including one built by fuse.js."""
    index = FuseIndex(get_fn=get_fn, field_norm_weight=field_norm_weight)
    index.set_keys(
        [
            KeyObject(
                path=k["path"], id=k["id"], weight=k["weight"], src=k["src"],
                get_fn=None,
            )
            for k in data["keys"]
        ]
    )
    index.set_index_records([_record_from_dict(r) for r in data["records"]])
    return index


__all__ = ["FuseIndex", "create_index", "parse_index"]
