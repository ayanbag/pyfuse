"""Bounded top-N selection — the port of ``src/tools/MaxHeap.ts``.

A max-heap *by the sort comparator*, so the worst retained result sits at the
root and can be evicted when something better arrives. Ordering by the
comparator rather than the raw score is what keeps the retained top-N
identical to "sort everything, then slice" — including tie-breaks and any
custom ``sort_fn``.

:mod:`heapq` is not used: it is a min-heap over ``<``, and forcing a
comparator into it means wrapping every element in a ``cmp_to_key`` object.
That allocation lands in the hot path of every limited search, and the
resulting eviction order is harder to prove identical to the original's.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cmp_to_key

from ..types import InternalResult

#: Orders two results; negative when ``a`` sorts before ``b``.
Comparator = Callable[[InternalResult, InternalResult], "int | float"]


class MaxHeap:
    """Retains the best ``limit`` results seen, by ``comparator``."""

    __slots__ = ("comparator", "heap", "limit")

    def __init__(self, limit: int, comparator: Comparator) -> None:
        self.limit = limit
        self.heap: list[InternalResult] = []
        self.comparator = comparator

    @property
    def size(self) -> int:
        """How many results are currently retained."""
        return len(self.heap)

    def insert(self, item: InternalResult) -> None:
        """Offer a result, keeping at most ``limit``.

        Fills until full, then replaces the root only when the incoming item
        sorts ahead of it — which makes a separate ``should_insert`` check
        redundant.
        """
        if self.size < self.limit:
            self.heap.append(item)
            self._bubble_up(self.size - 1)
        elif self.heap and self.comparator(item, self.heap[0]) < 0:
            self.heap[0] = item
            self._sink_down(0)

    def extract_sorted(self) -> list[InternalResult]:
        """Return the retained results in comparator order."""
        self.heap.sort(key=cmp_to_key(self.comparator))
        return self.heap

    def _bubble_up(self, i: int) -> None:
        heap = self.heap
        while i > 0:
            parent = (i - 1) >> 1
            if self.comparator(heap[i], heap[parent]) <= 0:
                break
            heap[i], heap[parent] = heap[parent], heap[i]
            i = parent

    def _sink_down(self, i: int) -> None:
        heap = self.heap
        length = len(heap)
        largest = i
        while True:
            i = largest
            left = 2 * i + 1
            right = 2 * i + 2
            if left < length and self.comparator(heap[left], heap[largest]) > 0:
                largest = left
            if right < length and self.comparator(heap[right], heap[largest]) > 0:
                largest = right
            if largest == i:
                break
            heap[i], heap[largest] = heap[largest], heap[i]
