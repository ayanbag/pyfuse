"""Corpus statistics — the port of ``src/search/token/InvertedIndex.ts``.

A stats-only inverted index. The query path consumes only ``df`` and
``field_count`` (for IDF weighting); the per-document maps exist solely to
keep those two correct under ``add`` / ``remove`` / ``remove_at``:

* ``doc_field_count[doc]`` — how many distinct fields the document
  contributed, subtracted from ``field_count`` on removal.
* ``doc_term_field_hits[doc]`` — how many fields each term appears in for
  that document; each entry decrements ``df[term]`` by exactly that count.

``df`` is incremented once per ``(doc, term, field)`` at index time, and
removal decrements by the same count, so the two mirror precisely.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass, field

from ...types import IndexRecord, SubRecord
from .analyzer import Analyzer


@dataclass(slots=True)
class InvertedIndexData:
    """Document-frequency statistics over an indexed corpus."""

    field_count: int = 0
    df: dict[str, int] = field(default_factory=dict)
    doc_field_count: dict[int, int] = field(default_factory=dict)
    doc_term_field_hits: dict[int, dict[str, int]] = field(default_factory=dict)


def _add_field(
    index: InvertedIndexData, text: str, doc_idx: int, analyzer: Analyzer
) -> None:
    tokens = analyzer.tokenize(text)
    if not tokens:
        return

    index.field_count += 1
    index.doc_field_count[doc_idx] = index.doc_field_count.get(doc_idx, 0) + 1

    # Each (doc, term, field) counts once — repeated occurrences within one
    # field must not multiply df.
    per_doc = index.doc_term_field_hits.setdefault(doc_idx, {})

    for term in dict.fromkeys(tokens):
        per_doc[term] = per_doc.get(term, 0) + 1
        index.df[term] = index.df.get(term, 0) + 1


def _ingest_record(
    index: InvertedIndexData,
    record: IndexRecord,
    key_count: int,
    analyzer: Analyzer,
) -> None:
    if record.v is not None:
        _add_field(index, record.v, record.i, analyzer)
        return

    if not record.fields:
        return

    for key_idx in range(key_count):
        value: SubRecord | list[SubRecord] | None = record.fields.get(key_idx)
        if not value:
            continue

        if isinstance(value, list):
            for sub in value:
                _add_field(index, sub.v, record.i, analyzer)
        else:
            _add_field(index, value.v, record.i, analyzer)


def build_inverted_index(
    records: Sequence[IndexRecord], key_count: int, analyzer: Analyzer
) -> InvertedIndexData:
    """Compute document-frequency statistics over ``records``."""
    index = InvertedIndexData()
    for record in records:
        _ingest_record(index, record, key_count, analyzer)
    return index


def add_to_inverted_index(
    index: InvertedIndexData,
    record: IndexRecord,
    key_count: int,
    analyzer: Analyzer,
) -> None:
    """Fold one more record into the statistics."""
    _ingest_record(index, record, key_count, analyzer)


def remove_from_inverted_index(index: InvertedIndexData, doc_idx: int) -> None:
    """Subtract one document's contribution from the statistics."""
    field_count = index.doc_field_count.get(doc_idx)
    if field_count is None:
        return

    index.field_count -= field_count
    del index.doc_field_count[doc_idx]

    per_doc = index.doc_term_field_hits.get(doc_idx)
    if not per_doc:
        return

    for term, hits in per_doc.items():
        remaining = index.df.get(term, 0) - hits
        if remaining <= 0:
            index.df.pop(term, None)
        else:
            index.df[term] = remaining

    del index.doc_term_field_hits[doc_idx]


def remove_and_shift_inverted_index(
    index: InvertedIndexData, removed_indices: Sequence[int]
) -> None:
    """Remove documents and renumber the survivors.

    Keeps the per-document maps in step with ``FuseIndex``'s contiguous
    renumbering, so statistics stay attached to the right documents.
    """
    if not removed_indices:
        return

    ordered = sorted(set(removed_indices))

    for idx in ordered:
        remove_from_inverted_index(index, idx)

    # A surviving index shifts down by the count of removed indices strictly
    # below it.
    first_removed = ordered[0]

    index.doc_field_count = {
        (old - bisect_left(ordered, old) if old > first_removed else old): count
        for old, count in index.doc_field_count.items()
    }
    index.doc_term_field_hits = {
        (old - bisect_left(ordered, old) if old > first_removed else old): terms
        for old, terms in index.doc_term_field_hits.items()
    }


__all__ = [
    "InvertedIndexData",
    "add_to_inverted_index",
    "build_inverted_index",
    "remove_and_shift_inverted_index",
    "remove_from_inverted_index",
]
