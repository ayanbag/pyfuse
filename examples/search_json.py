#!/usr/bin/env python3
"""Fuzzy-search any JSON or JSON Lines file from the command line.

    # the fuse.js test fixture that ships with this repo
    python examples/search_json.py tests/original/test/fixtures/books.json "ste ham"

    # pick the fields yourself, including nested paths
    python examples/search_json.py data.json "acme" --keys name,address.city

    # unix-style: exact, prefix, inverse, suffix
    python examples/search_json.py books.json "^the" --extended

    # long free text reads better with token search
    python examples/search_json.py runbooks.jsonl "disk filling up" --tokens

Reads a JSON array or a .jsonl file, searches it, and prints ranked results
with the matched text highlighted. Keys are inferred from the first record
unless you name them.

This is the "why does Python need this" argument in tool form: grep gives you
substrings, `rapidfuzz` compares two strings, and neither ranks records across
weighted fields. Zero dependencies, so it runs anywhere Python does.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fusejs import Fuse, FuseResult, RangeTuple

BOLD = "\033[1;33m"
DIM = "\033[2m"
OFF = "\033[0m"


class DataError(Exception):
    """The input file could not be read as searchable records."""


def load(path: Path) -> list[dict[str, Any]]:
    """Read a JSON array or a JSON Lines file into a list of records.

    Raises :class:`DataError` with something a user can act on — an unreadable
    input is the most likely failure of a tool like this, so it is worth more
    than a traceback.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataError(f"cannot read {path}: {exc}") from exc

    records: list[Any]
    if path.suffix == ".jsonl":
        try:
            records = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise DataError(f"{path} is not valid JSON Lines: {exc}") from exc
    else:
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DataError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(loaded, list):
            raise DataError(
                f"{path} holds a {type(loaded).__name__}, not a list of records"
            )
        records = loaded

    if not records:
        raise DataError(f"{path} contains no records")
    if not all(isinstance(r, dict) for r in records):
        raise DataError(f"{path} must contain objects, not bare values")
    return records


def infer_keys(records: list[dict[str, Any]]) -> list[str]:
    """Guess searchable key paths from the first record.

    Descends one level into nested objects, which covers the common
    ``{"author": {"lastName": ...}}`` shape without inventing a full schema
    walk. Numbers are skipped: fuzzy-matching an ID is rarely what anyone means.
    """
    keys: list[str] = []
    for name, value in records[0].items():
        if isinstance(value, dict):
            keys += [f"{name}.{k}" for k, v in value.items() if isinstance(v, str)]
        elif isinstance(value, str) or (
            isinstance(value, list) and all(isinstance(v, str) for v in value)
        ):
            keys.append(name)
    if not keys:
        raise DataError(
            "no text fields found in the first record; name them with --keys"
        )
    return keys


def highlight(value: str, spans: list[RangeTuple], colour: bool) -> str:
    """Mark the matched ranges. fuse.js reports them inclusive at both ends."""
    out: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        if start < cursor:
            continue
        out.append(value[cursor:start])
        piece = value[start : end + 1]
        out.append(f"{BOLD}{piece}{OFF}" if colour else f"[{piece}]")
        cursor = end + 1
    out.append(value[cursor:])
    return "".join(out)


def label_of(item: dict[str, Any]) -> str:
    """Pick a human label for a record.

    Prefers the conventional naming fields, then falls back to the first string
    value — printing a raw nested object as a heading helps nobody.
    """
    for candidate in ("title", "name", "label", "id"):
        value = item.get(candidate)
        if isinstance(value, str) and value:
            return value
    for value in item.values():
        if isinstance(value, str) and value:
            return value
    return json.dumps(item)[:70]


def show(results: list[FuseResult], colour: bool) -> None:
    if not results:
        print("no matches", file=sys.stderr)
        return
    for result in results:
        score = f"{result.score:.4f}" if result.score is not None else "  --  "
        head = f"{DIM}{score}{OFF}" if colour else score
        print(f"\n{head}  {label_of(result.item)}")
        for match in result.matches or []:
            if match.value is None:
                continue
            label = f"{DIM}{match.key:>18}{OFF}" if colour else f"{match.key:>18}"
            print(f"  {label}  {highlight(match.value, match.indices, colour)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fuzzy-search a JSON or JSONL file.",
        epilog="Scores are distances: 0.0 is perfect, 1.0 is unrelated.",
    )
    parser.add_argument("file", type=Path, help="a JSON array or .jsonl file")
    parser.add_argument("query", help="what to search for")
    parser.add_argument("--keys", help="comma-separated key paths (default: inferred)")
    parser.add_argument("--limit", type=int, default=5, help="max results (default 5)")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="0.0 demands a perfect match, 1.0 matches anything (default 0.6)",
    )
    parser.add_argument(
        "--extended", action="store_true", help="enable =exact ^prefix !inverse suffix$"
    )
    parser.add_argument(
        "--tokens", action="store_true", help="token search with IDF ranking"
    )
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    args = parser.parse_args()

    try:
        records = load(args.file)
        keys = (
            [k.strip() for k in args.keys.split(",") if k.strip()]
            if args.keys
            else infer_keys(records)
        )
    except DataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    fuse = Fuse(
        records,
        {
            "keys": keys,
            "include_score": True,
            "include_matches": True,
            "threshold": args.threshold,
            "use_extended_search": args.extended,
            "use_token_search": args.tokens,
        },
    )

    colour = sys.stdout.isatty() and not args.no_color
    results = fuse.search(args.query, limit=args.limit)
    header = f"{len(records)} records · keys: {', '.join(keys)}"
    print(f"{DIM}{header}{OFF}" if colour else header)
    show(results, colour)
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
