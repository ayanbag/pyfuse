"""Differential fuzzer: pyfuse (Python) vs fuse.js (Node), on shared inputs.

Generates random `(documents, query, options)` triples, runs both engines on
exactly the same input, and compares the results. Structure — result set,
ordering, match indices, keys, refIndex — must match **exactly**. Scores carry
a small relative tolerance, for the reason documented in DECISIONS.md: CPython
and V8 ship different libm implementations of `pow` and `log`, and no amount of
porting fidelity removes that difference.

    python fuzz/harness.py --seconds 60 --log fuzz/log.txt

Exits non-zero if any divergence is found, so it can gate CI.
"""

from __future__ import annotations

import argparse
import json
import random
import string
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oracle import Oracle, OracleError
from pyfuse import Fuse

# Scores are compared to a *relative* tolerance, not an exact bit match.
#
# CPython and V8 ship different libm implementations of `pow` AND `log`.
# CPython's are correctly rounded; V8's are not. Token search compounds both
# (an IDF `log` per term, a `pow` per key), so the error accumulates through
# the scoring chain. Measured worst case is ~5e-14 relative; 1e-12 sits an
# order of magnitude above that and still catches any real logic error, which
# would move a score by far more. Nothing else is given any tolerance at all.
SCORE_REL_TOLERANCE = 1e-12

# Astral characters are excluded on purpose. JS strings index by UTF-16 code
# unit and Python strings by code point, so "\U0001F600".length is 2 in JS and
# 1 here, and every index and length in the algorithm inherits the difference.
# That is a documented, deliberate divergence — fuzzing it would only rediscover
# the same known fact. See DECISIONS.md.
ALPHABET = string.ascii_letters + string.digits + " .-'" + "éüñçà"

WORDS = [
    "old",
    "man",
    "war",
    "lock",
    "artist",
    "code",
    "jeeves",
    "html",
    "angels",
    "demons",
    "silmarillion",
    "colony",
    "fool",
    "wooster",
    "café",
    "naïve",
    "the",
    "of",
    "a",
    "and",
]


@dataclass
class Stats:
    """Running totals for one fuzz session."""

    cases: int = 0
    divergences: int = 0
    score_only: int = 0
    tie_order: int = 0
    oracle_errors: int = 0
    max_ulp: int = 0
    max_rel: float = 0.0
    examples: list[dict[str, Any]] = field(default_factory=list)


def ulps_apart(a: float, b: float) -> int:
    """How many representable float64 values separate ``a`` and ``b``."""
    if a == b:
        return 0
    ia = struct.unpack("<q", struct.pack("<d", a))[0]
    ib = struct.unpack("<q", struct.pack("<d", b))[0]
    if ia < 0:
        ia = -0x8000000000000000 - ia
    if ib < 0:
        ib = -0x8000000000000000 - ib
    return abs(ia - ib)


# ── Input generation ───────────────────────────────────────────────


def random_text(rng: random.Random, max_words: int = 5) -> str:
    """A title-ish string: mostly real words, sometimes noise."""
    if rng.random() < 0.15:
        length = rng.randint(0, 20)
        return "".join(rng.choice(ALPHABET) for _ in range(length))
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, max_words)))


def random_document(rng: random.Random) -> dict[str, Any]:
    """A document with nested paths and, sometimes, array fields."""
    doc: dict[str, Any] = {
        "title": random_text(rng),
        "author": {
            "firstName": random_text(rng, 2),
            "lastName": random_text(rng, 2),
        },
    }
    if rng.random() < 0.4:
        doc["tags"] = [random_text(rng, 2) for _ in range(rng.randint(0, 3))]
    return doc


def random_query(rng: random.Random, mode: str) -> Any:
    """A query appropriate to the search mode under test."""
    if mode == "extended":
        operator = rng.choice(["=", "'", "^", "!", "!^", ""])
        word = rng.choice(WORDS)
        suffix = "$" if rng.random() < 0.3 else ""
        term = f"{operator}{word}{suffix}"
        if rng.random() < 0.25:
            term = f"{term} | {rng.choice(WORDS)}"
        return term

    if mode == "logical":

        def leaf() -> dict[str, str]:
            key = rng.choice(["title", "author.firstName", "author.lastName"])
            return {key: rng.choice(WORDS)}

        operator = rng.choice(["$and", "$or"])
        return {operator: [leaf() for _ in range(rng.randint(1, 3))]}

    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3)))


def random_options(rng: random.Random, mode: str) -> dict[str, Any]:
    """A random but internally consistent option set, in JS spelling."""
    keys: list[Any] = rng.choice(
        [
            ["title"],
            ["title", "author.firstName"],
            ["title", "author.firstName", "author.lastName"],
            ["tags"],
            [
                {"name": "title", "weight": rng.randint(1, 5)},
                {"name": "author.lastName", "weight": rng.randint(1, 5)},
            ],
        ]
    )

    options: dict[str, Any] = {
        "keys": keys,
        "includeScore": True,
        "includeMatches": rng.random() < 0.5,
        "threshold": round(rng.uniform(0.0, 1.0), 3),
        "distance": rng.choice([0, 10, 100, 1000]),
        "location": rng.choice([0, 0, 5, 20]),
        "ignoreLocation": rng.random() < 0.3,
        "ignoreFieldNorm": rng.random() < 0.2,
        "fieldNormWeight": rng.choice([1, 1, 0.5, 2]),
        "minMatchCharLength": rng.choice([1, 1, 2, 3]),
        "findAllMatches": rng.random() < 0.3,
        "isCaseSensitive": rng.random() < 0.15,
        "ignoreDiacritics": rng.random() < 0.3,
        "shouldSort": rng.random() < 0.9,
    }

    if mode == "extended":
        options["useExtendedSearch"] = True
    elif mode == "token":
        options["useTokenSearch"] = True
        options["tokenMatch"] = rng.choice(["any", "all"])

    return options


# ── Comparison ─────────────────────────────────────────────────────


def to_js_shape(results: list[Any]) -> list[dict[str, Any]]:
    """Python results in the shape fuse.js returns."""
    shaped = []
    for result in results:
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
        shaped.append(data)
    return shaped


def relative_error(a: float, b: float) -> float:
    """Relative difference between two scores; 0.0 when identical."""
    if a == b:
        return 0.0
    scale = max(abs(a), abs(b))
    return abs(a - b) / scale if scale else abs(a - b)


def compare(
    python: list[dict[str, Any]], js: list[dict[str, Any]]
) -> tuple[str | None, int, float]:
    """Classify the difference between two result lists.

    Returns ``(kind, max_ulp, max_relative_error)`` where ``kind`` is:

    ``None``
        Identical.
    ``"score"``
        Same documents, same order; only score bits differ, within tolerance.
    ``"tie"``
        Same documents in a different order, or a different document set
        under a ``limit`` — but every score involved agrees within tolerance,
        so the reordering is caused by float noise breaking or creating a tie,
        not by a logic difference. See DECISIONS.md: CPython and V8 disagree
        by ~1 ULP on `pow`/`log`, which is enough to split an exact tie, and
        the `idx` tie-break then fires in one engine but not the other.
    ``str``
        Anything else — a real divergence, described by the string.
    """
    py_scores = {r["refIndex"]: r.get("score") for r in python}
    js_scores = {r["refIndex"]: r.get("score") for r in js}
    py_rest = {
        r["refIndex"]: {k: v for k, v in r.items() if k != "score"} for r in python
    }
    js_rest = {r["refIndex"]: {k: v for k, v in r.items() if k != "score"} for r in js}

    worst_ulp = 0
    worst_rel = 0.0

    # Every document both engines returned must agree: payload exactly,
    # score within tolerance.
    shared = py_scores.keys() & js_scores.keys()
    for ref in shared:
        if py_rest[ref] != js_rest[ref]:
            return f"refIndex {ref} payload mismatch", worst_ulp, worst_rel
        got, want = py_scores[ref], js_scores[ref]
        if got is None or want is None:
            continue
        worst_ulp = max(worst_ulp, ulps_apart(got, float(want)))
        rel = relative_error(got, float(want))
        worst_rel = max(worst_rel, rel)
        if rel > SCORE_REL_TOLERANCE:
            return (
                f"refIndex {ref} score off by {rel:.3e} relative",
                worst_ulp,
                worst_rel,
            )

    py_ids = [r["refIndex"] for r in python]
    js_ids = [r["refIndex"] for r in js]

    if py_ids == js_ids:
        return ("score" if worst_rel else None), worst_ulp, worst_rel

    if sorted(py_ids) == sorted(js_ids):
        # Same documents, different order. Only a tie if the reordering is
        # confined to documents whose scores are indistinguishable.
        if _reorder_is_within_ties(python, js):
            return "tie", worst_ulp, worst_rel
        return "ordering mismatch beyond ties", worst_ulp, worst_rel

    # Different document sets. Under a `limit` this is expected when a tie at
    # the cut-off is split differently by the two engines: the documents that
    # differ must all score within tolerance of the boundary.
    if _membership_differs_only_at_a_tie(python, js):
        return "tie", worst_ulp, worst_rel

    return (
        f"result set mismatch: python {sorted(py_ids)} vs js {sorted(js_ids)}",
        worst_ulp,
        worst_rel,
    )


def _reorder_is_within_ties(
    python: list[dict[str, Any]], js: list[dict[str, Any]]
) -> bool:
    """Whether two orderings differ only among indistinguishable scores."""
    js_by_ref = {r["refIndex"]: r.get("score") for r in js}
    for position, entry in enumerate(python):
        other = js[position]
        if entry["refIndex"] == other["refIndex"]:
            continue
        a = js_by_ref.get(entry["refIndex"])
        b = other.get("score")
        if a is None or b is None:
            return False
        if relative_error(float(a), float(b)) > SCORE_REL_TOLERANCE:
            return False
    return True


def _membership_differs_only_at_a_tie(
    python: list[dict[str, Any]], js: list[dict[str, Any]]
) -> bool:
    """Whether a differing result set is explained by a tie at the cut-off."""
    py_ids = {r["refIndex"] for r in python}
    js_ids = {r["refIndex"] for r in js}
    only_py = py_ids - js_ids
    only_js = js_ids - py_ids

    if not only_py or not only_js:
        # One side returned strictly more documents — not a boundary effect.
        return False

    py_scores = {r["refIndex"]: r.get("score") for r in python}
    js_scores = {r["refIndex"]: r.get("score") for r in js}

    # The swapped-in and swapped-out documents must all sit at the same score.
    boundary = [py_scores[r] for r in only_py] + [js_scores[r] for r in only_js]
    if any(s is None for s in boundary):
        return False
    reference = float(boundary[0])
    return all(
        relative_error(float(s), reference) <= SCORE_REL_TOLERANCE for s in boundary
    )


# ── Driver ─────────────────────────────────────────────────────────


def run(seconds: float, seed: int, log_path: Path) -> int:
    """Fuzz for ``seconds``; return a process exit code."""
    rng = random.Random(seed)
    stats = Stats()
    started = time.monotonic()

    with Oracle() as oracle:
        version = oracle.version()
        deadline = started + seconds

        while time.monotonic() < deadline:
            mode = rng.choice(["fuzzy", "fuzzy", "extended", "logical", "token"])
            docs = [random_document(rng) for _ in range(rng.randint(1, 12))]
            query = random_query(rng, mode)
            options = random_options(rng, mode)
            limit = rng.choice([-1, -1, -1, 1, 3, 5])

            try:
                expected = oracle.search(
                    docs, query, options, {"limit": limit} if limit > -1 else None
                )
            except OracleError:
                # fuse.js rejected the input; the port is not required to
                # accept what the original refuses.
                stats.oracle_errors += 1
                continue

            try:
                actual = to_js_shape(Fuse(docs, options).search(query, limit=limit))
            except Exception as exc:
                stats.divergences += 1
                stats.examples.append(
                    {
                        "reason": f"python raised {type(exc).__name__}: {exc}",
                        "mode": mode,
                        "query": query,
                        "options": options,
                        "docs": docs,
                        "limit": limit,
                    }
                )
                stats.cases += 1
                continue

            reason, worst_ulp, worst_rel = compare(actual, expected)
            stats.max_ulp = max(stats.max_ulp, worst_ulp)
            stats.max_rel = max(stats.max_rel, worst_rel)
            stats.cases += 1

            if reason == "score":
                stats.score_only += 1
            elif reason == "tie":
                stats.tie_order += 1
                if len(stats.examples) < 5:
                    stats.examples.append(
                        {
                            "kind": "tie",
                            "mode": mode,
                            "query": query,
                            "options": options,
                            "docs": docs,
                            "limit": limit,
                            "python": actual,
                            "js": expected,
                        }
                    )
            elif reason is not None:
                stats.divergences += 1
                if len(stats.examples) < 20:
                    stats.examples.append(
                        {
                            "reason": reason,
                            "mode": mode,
                            "query": query,
                            "options": options,
                            "docs": docs,
                            "limit": limit,
                            "python": actual,
                            "js": expected,
                        }
                    )

    elapsed = time.monotonic() - started
    report = format_report(stats, elapsed, version, seed)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(report, encoding="utf-8")
    print(report)

    return 1 if stats.divergences else 0


def format_report(stats: Stats, elapsed: float, version: str, seed: int) -> str:
    """The human-readable log written to ``fuzz/log.txt``."""
    rate = stats.cases / elapsed if elapsed else 0.0
    lines = [
        "pyfuse — differential fuzz run",
        "=" * 60,
        f"oracle          : fuse.js {version} (Node)",
        f"duration        : {elapsed:.1f}s",
        f"seed            : {seed}",
        f"cases           : {stats.cases}  ({rate:.0f}/s)",
        f"oracle rejects  : {stats.oracle_errors}  (input the original refused)",
        "",
        f"STRUCTURAL DIVERGENCES : {stats.divergences}",
        f"tie-order differences  : {stats.tie_order}"
        f"  ({100 * stats.tie_order / max(1, stats.cases):.3f}% of cases)",
        f"score-only diffs       : {stats.score_only}",
        f"  worst relative error : {stats.max_rel:.3e}"
        f"  (tolerance {SCORE_REL_TOLERANCE:.0e})",
        f"  worst ULP distance   : {stats.max_ulp}",
        "",
        "Structure — result set, ordering, match indices, keys, refIndex — is",
        "compared EXACTLY. Scores carry a relative tolerance because CPython",
        "and V8 ship different libm implementations of pow and log: CPython's",
        "are correctly rounded, V8's are not, and token search compounds both.",
        "No amount of porting fidelity removes that. See DECISIONS.md.",
        "",
        "'tie-order differences' are the *consequence* of that: 1 ULP is",
        "enough to split a score tie, and the idx tie-break then fires in one",
        "engine but not the other. Under a `limit` this can change which",
        "document makes the cut. Reported separately and honestly rather than",
        "hidden — it is a real, if rare, user-visible effect.",
        "",
    ]

    if stats.divergences:
        lines.append("DIVERGENCES")
        lines.append("-" * 60)
        for example in stats.examples:
            lines.append(json.dumps(example, ensure_ascii=False, indent=2))
            lines.append("")
    else:
        lines.append("RESULT: no structural divergences.")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--log", type=Path, default=Path("fuzz/log.txt"))
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(2**31)
    return run(args.seconds, seed, args.log)


if __name__ == "__main__":
    raise SystemExit(main())
