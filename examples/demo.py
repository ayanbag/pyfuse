#!/usr/bin/env python3
"""A guided tour of fusejs-python, meant to be read as it runs.

    python examples/demo.py                # the whole tour
    python examples/demo.py --interactive  # ...then a live search prompt
    python examples/demo.py --no-color     # plain text

The corpus is a set of on-call runbooks and the queries are the kind of thing
a human actually types into a support ticket: half-remembered, misspelled, and
never using the words the document uses. That is the case fuse.js solves and
Python had no answer for, which is why this port exists.

Zero dependencies, same as the library.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fusejs import Fuse, FuseResult, RangeTuple, __version__

# ── Presentation ───────────────────────────────────────────────────


class Style:
    """Whether to emit ANSI colour. Mutable, so --no-color can switch it off."""

    enabled = sys.stdout.isatty()


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if Style.enabled else text


def bold(t: str) -> str:
    return paint(t, "1")


def dim(t: str) -> str:
    return paint(t, "2")


def cyan(t: str) -> str:
    return paint(t, "36")


def hit(t: str) -> str:
    """Highlight a matched span."""
    return paint(t, "1;33") if Style.enabled else f"[{t}]"


def scene(number: int, title: str, why: str) -> None:
    print()
    print(bold(f"  {number}. {title}"))
    print(dim(f"     {why}"))
    print()


def show(code: str) -> None:
    for line in code.strip("\n").splitlines():
        print(cyan(f"     >>> {line}"))
    print()


def highlight(value: str, spans: list[RangeTuple]) -> str:
    """Rebuild a string with its matched ranges marked.

    ``spans`` are inclusive on both ends, exactly as fuse.js reports them.
    """
    out: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        out.append(value[cursor:start])
        out.append(hit(value[start : end + 1]))
        cursor = end + 1
    out.append(value[cursor:])
    return "".join(out)


def report(
    results: list[FuseResult], limit: int = 3, *, show_key: bool = False
) -> None:
    """Print hits as ``score  [matched key]  text``.

    When the engine reported match positions, the text shown is the field that
    actually matched, with the matched spans marked — otherwise the title,
    which is only ever a label.
    """
    if not results:
        print("     (no matches)")
        return
    for result in results[:limit]:
        score = f"{result.score:.4f}" if result.score is not None else "  --  "
        key = ""
        text = str(result.item["title"])
        if result.matches:
            match = result.matches[0]
            if match.value is not None:
                text = highlight(match.value, match.indices)
                key = match.key or ""
        label = dim(f"{result.item['id']}  {key:>12}  ") if show_key else ""
        print(f"     {dim(score)}  {label}{text}")


# ── The corpus ─────────────────────────────────────────────────────

RUNBOOKS: list[dict[str, Any]] = [
    {
        "id": "RB-101",
        "title": "Kafka consumer lag spike on the ingest topic",
        "service": {"name": "ingest-gateway", "team": "Data Platform"},
        "summary": "Consumers fall behind, lag climbs, downstream tables go stale.",
        "tags": ["kafka", "streaming", "backpressure"],
    },
    {
        "id": "RB-102",
        "title": "Oracle to SQL Server migration job stalls mid-batch",
        "service": {"name": "migration-runner", "team": "Data Platform"},
        "summary": "The batch loader stops committing and holds its transaction open.",
        "tags": ["oracle", "sqlserver", "migration", "etl"],
    },
    {
        "id": "RB-103",
        "title": "Reconciliation mismatch between source and target counts",
        "service": {"name": "recon-service", "team": "Data Quality"},
        "summary": "Row counts disagree after a load; usually a silent truncation.",
        "tags": ["reconciliation", "validation", "nifi"],
    },
    {
        "id": "RB-104",
        "title": "Scheduled job retries forever without backing off",
        "service": {"name": "scheduler", "team": "Platform"},
        "summary": "A poisoned message re-enters the queue; retries never stop.",
        "tags": ["scheduler", "retry", "queue"],
    },
    {
        "id": "RB-105",
        "title": "Redis connection pool exhausted under load",
        "service": {"name": "api-edge", "team": "Platform"},
        "summary": "Requests queue on connection checkout and p99 latency collapses.",
        "tags": ["redis", "latency", "pool"],
    },
    {
        "id": "RB-106",
        "title": "LLM ticket triage returns empty classifications",
        "service": {"name": "triage-agent", "team": "Applied AI"},
        "summary": "The model returns a valid response with no label attached.",
        "tags": ["llm", "triage", "agent"],
    },
]

KEYS = ["title", "summary", "service.name", "tags"]


# ── The tour ───────────────────────────────────────────────────────


def tour() -> None:
    print()
    print(bold(f"  fusejs-python {__version__}"), dim("— a Python port of fuse.js"))
    print(dim(f"  {len(RUNBOOKS)} runbooks, {len(KEYS)} keys, 0 dependencies"))

    # 1 ────────────────────────────────────────────────────────────
    scene(
        1,
        "A human typed this, and it still finds the right runbook",
        "Three typos, and not one word matches the document exactly.",
    )
    show("""
fuse = Fuse(RUNBOOKS, {"keys": KEYS, "include_score": True})
fuse.search("kafka konsumer lagg")
""")
    fuse = Fuse(RUNBOOKS, {"keys": KEYS, "include_score": True})
    report(fuse.search("kafka konsumer lagg"))
    print()
    print(dim("     Lower score = better. 0.0 is a perfect match."))

    # 2 ────────────────────────────────────────────────────────────
    scene(
        2,
        "Where it matched, character by character",
        "include_matches gives the exact spans, which is how you render highlights.",
    )
    show("""
fuse = Fuse(RUNBOOKS, {"keys": KEYS, "include_matches": True})
fuse.search("recon mismatch")
""")
    fuse = Fuse(
        RUNBOOKS, {"keys": KEYS, "include_matches": True, "include_score": True}
    )
    report(fuse.search("recon mismatch"), limit=2, show_key=True)
    print()
    print(dim("     Each [start, end] pair comes straight out of the Bitap scan."))

    # 3 ────────────────────────────────────────────────────────────
    scene(
        3,
        "Weighted keys",
        "The same query, twice. Only the weights move — and the winner changes.",
    )
    show("""
title_first = Fuse(RUNBOOKS, {"keys": [
    {"name": "title",   "weight": 3},
    {"name": "summary", "weight": 1},
]})
summary_first = Fuse(RUNBOOKS, {"keys": [
    {"name": "title",   "weight": 1},
    {"name": "summary", "weight": 3},
]})
""")
    query = "retry loop"
    title_first = Fuse(
        RUNBOOKS,
        {
            "keys": [
                {"name": "title", "weight": 3},
                {"name": "summary", "weight": 1},
            ],
            "include_score": True,
        },
    )
    summary_first = Fuse(
        RUNBOOKS,
        {
            "keys": [
                {"name": "title", "weight": 1},
                {"name": "summary", "weight": 3},
            ],
            "include_score": True,
        },
    )
    print(dim(f'     search("{query}") with title weighted 3x:'))
    report(title_first.search(query), limit=2)
    print()
    print(dim(f'     search("{query}") with summary weighted 3x:'))
    report(summary_first.search(query), limit=2)
    print()
    print(dim("     Same query, same corpus. The weights alone flip the winner:"))
    print(dim('     "retry loop" is close to the summary of RB-104, but the'))
    print(dim("     title of RB-105 is shorter, so it wins on field-length norm."))

    # 4 ────────────────────────────────────────────────────────────
    scene(
        4,
        "Nested paths and lists",
        "'service.name' walks into the object; 'tags' searches every element.",
    )
    show("""
fuse.search("data platform")   # matches service.team, nested two levels down
fuse.search("backpresure")     # matches inside the tags list, misspelled
""")
    nested = Fuse(
        RUNBOOKS,
        {
            "keys": ["title", "service.name", "service.team", "tags"],
            "include_score": True,
            "include_matches": True,
        },
    )
    print(dim('     search("data platform")   — the matched key is shown:'))
    report(nested.search("data platform"), limit=2, show_key=True)
    print()
    print(dim('     search("backpresure"):'))
    report(nested.search("backpresure"), limit=2, show_key=True)
    print()
    print(dim("     Note the second column: those hits are scored on a field"))
    print(dim("     two levels deep, and on one element of a list."))

    # 5 ────────────────────────────────────────────────────────────
    scene(
        5,
        "Extended search: operators, not just fuzz",
        "When the user knows what they want, let them say so exactly.",
    )
    ext = Fuse(
        RUNBOOKS,
        {
            "keys": ["title", "summary", "service.name", "service.team", "tags"],
            "use_extended_search": True,
        },
    )
    for pattern, meaning in [
        ("^Kafka", "starts with"),
        ("'migration", "must include, no fuzz"),
        ("=scheduler", "exact field match"),
        ("!redis", "must NOT include"),
        ("pool$", "ends with"),
    ]:
        found = ext.search(pattern)
        ids = ", ".join(r.item["id"] for r in found) or "(none)"
        if len(ids) > 30:
            ids = f"{len(found)} of {len(RUNBOOKS)} runbooks"
        print(f"     {cyan(pattern.ljust(12))} {dim(meaning.ljust(23))} {ids}")

    # 6 ────────────────────────────────────────────────────────────
    scene(
        6,
        "Logical queries",
        "$and / $or compose those operators into a real query tree.",
    )
    show("""
fuse.search({"$and": [
    {"service.team": "'Platform"},   # owned by a Platform team
    {"title": "!Redis"},             # but not the Redis one
]})
""")
    logical = ext.search(
        {
            "$and": [
                {"service.team": "'Platform"},
                {"title": "!Redis"},
            ]
        }
    )
    for result in logical:
        team = result.item["service"]["team"]
        print(f"     {result.item['id']}  {dim(team.ljust(15))} {result.item['title']}")
    print()
    print(dim("     'Data Platform' and 'Platform' both contain \"Platform\", so both"))
    print(dim("     qualify — and RB-105, the Redis one, is excluded by !Redis."))

    # 7 ────────────────────────────────────────────────────────────
    scene(
        7,
        "Token search with IDF ranking",
        "Rare words count for more than common ones — the BM25 idea.",
    )
    show("""
fuse = Fuse(RUNBOOKS, {"keys": KEYS, "use_token_search": True})
fuse.search("job retries")
""")
    tokens = Fuse(
        RUNBOOKS,
        {"keys": KEYS, "use_token_search": True, "include_score": True},
    )
    report(tokens.search("job retries"), limit=3)
    print()
    print(dim('     "retries" is rare in this corpus, so it dominates the ranking.'))

    # 8 ────────────────────────────────────────────────────────────
    scene(
        8,
        "The part that is actually hard",
        "This is a port, so the real question is whether it agrees with the original.",
    )
    print("     Original fuse.js vitest suite, unmodified :  " + bold("285 / 297"))
    print("     Structural divergences in 51,569 fuzz cases:  " + bold("0"))
    print("     Score agreement                            :  " + bold("~1e-13 rel"))
    print("     Runtime dependencies                       :  " + bold("0"))
    print()
    print(dim("     just compat · just fuzz · DECISIONS.md has the 1-ULP story"))
    print()


# ── Interactive ────────────────────────────────────────────────────


def interactive() -> None:
    fuse = Fuse(
        RUNBOOKS,
        {"keys": KEYS, "include_score": True, "include_matches": True},
    )
    print()
    print(bold("  Live search.") + dim("  Misspell it on purpose. Ctrl-C quits."))
    print()
    while True:
        try:
            query = input(cyan("  search> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not query:
            continue
        if query in {"quit", "exit"}:
            return
        results = fuse.search(query)
        print()
        report(results, limit=5)
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="drop into a live search prompt after the tour",
    )
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    args = parser.parse_args()

    if args.no_color:
        Style.enabled = False

    tour()
    if args.interactive:
        interactive()


if __name__ == "__main__":
    main()
