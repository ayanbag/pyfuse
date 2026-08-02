"""Shared fixtures and comparison helpers for the ported test suite.

The oracle fixture is session-scoped: starting Node once and reusing the
process is the difference between a differential suite that runs in seconds
and one nobody waits for.
"""

from __future__ import annotations

import json
import struct
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "fuzz"))

from oracle import Oracle  # noqa: E402

# ── Score comparison ───────────────────────────────────────────────
#
# Scores carry a relative tolerance; everything else is compared exactly.
# CPython and V8 ship different libm implementations of `pow` and `log` —
# CPython's are correctly rounded, V8's are not — and token search compounds
# both. Measured worst case is ~5e-14 relative. See DECISIONS.md.
SCORE_REL_TOLERANCE = 1e-12


def ulps_apart(a: float, b: float) -> int:
    """How many representable float64 values separate ``a`` and ``b``."""
    if a == b:
        return 0
    ia = struct.unpack("<q", struct.pack("<d", a))[0]
    ib = struct.unpack("<q", struct.pack("<d", b))[0]
    # Map the negative range onto a continuous ordering.
    if ia < 0:
        ia = -0x8000000000000000 - ia
    if ib < 0:
        ib = -0x8000000000000000 - ib
    return abs(ia - ib)


def result_to_js(result: Any) -> dict[str, Any]:
    """One :class:`FuseResult` in the shape fuse.js returns."""
    data: dict[str, Any] = {"item": result.item, "refIndex": result.ref_index}
    if result.score is not None:
        data["score"] = result.score
    if result.matches is not None:
        data["matches"] = [
            {
                key: value
                for key, value in (
                    ("indices", [list(r) for r in match.indices]),
                    ("value", match.value),
                    ("key", match.key),
                    ("refIndex", match.ref_index),
                )
                if value is not None
            }
            for match in result.matches
        ]
    return data


def assert_matches_oracle(actual: list[Any], expected: list[Any]) -> None:
    """Assert a Python result list matches the oracle's.

    Structure is compared exactly; scores to within
    :data:`SCORE_REL_TOLERANCE` relative error.
    """
    converted = [result_to_js(r) for r in actual]

    assert len(converted) == len(expected), (
        f"result count diverged: python {len(converted)} vs js {len(expected)}"
    )

    for position, (got, want) in enumerate(zip(converted, expected, strict=True)):
        got_score = got.pop("score", None)
        want_score = want.get("score")
        want_rest = {k: v for k, v in want.items() if k != "score"}

        assert got == want_rest, f"result {position} diverged structurally"

        if want_score is not None:
            assert got_score is not None, f"result {position} missing score"
            want_float = float(want_score)
            scale = max(abs(got_score), abs(want_float))
            relative = abs(got_score - want_float) / scale if scale else 0.0
            assert relative <= SCORE_REL_TOLERANCE, (
                f"result {position} score diverged by {relative:.3e} relative "
                f"({ulps_apart(got_score, want_float)} ULP): "
                f"python {got_score!r} vs js {want_score!r}"
            )


# ── Fixtures ───────────────────────────────────────────────────────


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
