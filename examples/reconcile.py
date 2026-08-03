#!/usr/bin/env python3
"""Reconcile two record sets whose join key did not survive a migration.

    python examples/reconcile.py

The problem: you move customers from Oracle to SQL Server. The surrogate keys
are regenerated, the legacy IDs are dropped or reused, and now you have to
prove the two sides agree. You cannot join on a key, so you join on what the
records *say* — names, cities, emails — none of which match exactly.

    ACME CORPORATION LTD   vs   Acme Corp. Limited
    Jose Ramirez-Ortega    vs   José Ramírez Ortega

This is entity resolution, and it is why a record-level fuzzy search beats a
string-similarity function: you match on several weighted fields at once, and
the score reflects the whole record rather than one column.

The output has three buckets, and the middle one is the point of the script.
Anything confident is auto-matched. Anything hopeless is flagged as missing.
Anything in between goes to a human, because a reconciliation job that guesses
is worse than one that admits it does not know.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fusejs import Fuse

# ── The two sides ──────────────────────────────────────────────────

# Oracle: uppercase, abbreviated, ASCII-folded by some ancient loader.
SOURCE: list[dict[str, Any]] = [
    {
        "id": "S1",
        "name": "ACME CORPORATION LTD",
        "city": "KOLKATA",
        "email": "ap@acme.in",
    },
    {
        "id": "S2",
        "name": "JOSE RAMIREZ-ORTEGA",
        "city": "MADRID",
        "email": "j.ramirez@mail.es",
    },
    {
        "id": "S3",
        "name": "NORTHWIND TRADERS",
        "city": "SEATTLE",
        "email": "billing@northwind.com",
    },
    {
        "id": "S4",
        "name": "BLUE RIVER LOGISTIC",
        "city": "ROTTERDAM",
        "email": "ops@blueriver.nl",
    },
    {
        "id": "S5",
        "name": "TATA CONSULTANCY SVCS",
        "city": "MUMBAI",
        "email": "ar@tcs.co.in",
    },
    {"id": "S6", "name": "DELETED ACCOUNT 4471", "city": "", "email": ""},
]

# SQL Server: mixed case, expanded, accents restored, one genuine new row.
TARGET: list[dict[str, Any]] = [
    {
        "id": "T90",
        "name": "Acme Corp. Limited",
        "city": "Kolkata",
        "email": "ap@acme.in",
    },
    {
        "id": "T91",
        "name": "José Ramírez Ortega",
        "city": "Madrid",
        "email": "j.ramirez@mail.es",
    },
    {
        "id": "T92",
        "name": "Northwind Traders Inc",
        "city": "Seattle",
        "email": "billing@northwind.com",
    },
    {
        "id": "T93",
        "name": "Blue River Logistics BV",
        "city": "Rotterdam",
        "email": "ops@blueriver.nl",
    },
    {
        "id": "T94",
        "name": "Tata Consultancy Services",
        "city": "Mumbai",
        "email": "ar@tcs.co.in",
    },
    {
        "id": "T95",
        "name": "Riverbed Analytics",
        "city": "Austin",
        "email": "hello@riverbed.io",
    },
]

# Measured on the rows below: true pairs land at 0.43-0.63, and the row with no
# counterpart at 0.87. That gap is what makes these cutoffs defensible.
AUTO_MATCH_BELOW = 0.55  # accept without review
REVIEW_BELOW = 0.80  # above this, treat as no match at all


@dataclass(frozen=True, slots=True)
class Pairing:
    """One source row and the best target row found for it."""

    source: dict[str, Any]
    target: dict[str, Any] | None
    score: float | None

    @property
    def verdict(self) -> str:
        if self.score is None or self.score >= REVIEW_BELOW:
            return "MISSING"
        return "matched" if self.score < AUTO_MATCH_BELOW else "review"


def build_index(target: list[dict[str, Any]]) -> Fuse:
    """Index the target side once, then probe it with every source row.

    ``email`` outweighs everything because it is the closest thing to a natural
    key left standing.

    ``use_token_search=True`` is the load-bearing choice. The probe query is a
    whole record — name, city and email concatenated — and the default Bitap
    scan treats a query as a single pattern, so a long one scores poorly
    against every short field. Measured on these six rows, Bitap put the true
    pairs at 0.66-0.88 and the row with no counterpart at 0.92: no cutoff
    separates those. Token search scores term by term and moves the true pairs
    to 0.43-0.63, well clear of 0.87.

    ``ignore_diacritics=True`` is what lets JOSE RAMIREZ-ORTEGA find José
    Ramírez Ortega — the loader that stripped the accents is exactly the kind
    of thing you are reconciling around.
    """
    return Fuse(
        target,
        {
            "keys": [
                {"name": "email", "weight": 3.0},
                {"name": "name", "weight": 2.0},
                {"name": "city", "weight": 1.0},
            ],
            "include_score": True,
            "use_token_search": True,
            "token_match": "any",
            "ignore_diacritics": True,
        },
    )


def probe(fuse: Fuse, row: dict[str, Any]) -> Pairing:
    """Find the best target row for one source row."""
    query = f"{row['name']} {row['city']} {row['email']}".strip()
    hits = fuse.search(query, limit=1)
    if not hits:
        return Pairing(row, None, None)
    return Pairing(row, hits[0].item, hits[0].score)


def main() -> None:
    fuse = build_index(TARGET)
    pairings = [probe(fuse, row) for row in SOURCE]

    width = max(len(p.source["name"]) for p in pairings)
    print(f"\n  Oracle: {len(SOURCE)} rows   ->   SQL Server: {len(TARGET)} rows\n")
    print(f"  {'source'.ljust(width)}  score   verdict   target")
    print(f"  {'-' * width}  ------  --------  ------")

    for pair in pairings:
        score = "  --  " if pair.score is None else f"{pair.score:.4f}"
        target = (
            pair.target["name"]
            if pair.target and pair.verdict != "MISSING"
            else "(no match)"
        )
        print(
            f"  {pair.source['name'].ljust(width)}  {score}  "
            f"{pair.verdict.ljust(8)}  {target}"
        )

    matched = [p for p in pairings if p.verdict == "matched"]
    review = [p for p in pairings if p.verdict == "review"]
    missing = [p for p in pairings if p.verdict == "MISSING"]

    claimed = {p.target["id"] for p in pairings if p.target and p.verdict != "MISSING"}
    orphans = [row for row in TARGET if row["id"] not in claimed]

    print(
        f"\n  {len(matched)} matched · {len(review)} need review"
        f" · {len(missing)} missing"
    )
    if orphans:
        names = ", ".join(row["name"] for row in orphans)
        print(f"  {len(orphans)} target row(s) with no source: {names}")

    print(
        "\n  The rows needing review are the most heavily abbreviated names —\n"
        "  LTD -> Limited, SVCS -> Services. That is the honest outcome: the\n"
        "  more a string was mangled on the way in, the less a matcher should\n"
        "  claim about it.\n"
        "\n  Both directions matter. A source row with no target means data was\n"
        "  lost in the migration; a target row with no source means data was\n"
        "  invented. A count-only reconciliation would have reported 6 = 6 and\n"
        "  passed, while one row was dropped and a different one appeared.\n"
    )


if __name__ == "__main__":
    main()
