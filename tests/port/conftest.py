"""Shared fixtures for the ported test suite.

The oracle fixture is session-scoped: starting Node once and reusing the
process is the difference between a differential suite that runs in seconds
and one nobody waits for.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "fuzz"))

from oracle import Oracle  # noqa: E402


@pytest.fixture(scope="session")
def oracle() -> Iterator[Oracle]:
    """A live fuse.js process to compare against.

    Skips rather than fails when Node is unavailable, so the non-differential
    part of the suite still runs on a machine without a JS toolchain.
    """
    try:
        instance = Oracle()
        instance.version()
    except (OSError, RuntimeError) as exc:  # pragma: no cover - env dependent
        pytest.skip(f"fuse.js oracle unavailable: {exc}")

    yield instance
    instance.close()


@pytest.fixture(scope="session")
def books() -> list[dict[str, Any]]:
    """The `books.json` fixture from the original suite."""
    path = ROOT / "tests" / "original" / "test" / "fixtures" / "books.json"
    return json.loads(path.read_text(encoding="utf-8"))
