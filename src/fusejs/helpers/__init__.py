"""Shared helpers: value coercion, path resolution, diacritics, index merging."""

from __future__ import annotations

from .diacritics import strip_diacritics
from .get import get
from .merge_indices import merge_indices
from .type_guards import is_blank, is_defined, is_scalar, to_string

__all__ = [
    "get",
    "is_blank",
    "is_defined",
    "is_scalar",
    "merge_indices",
    "strip_diacritics",
    "to_string",
]
