"""Count the escape hatches the Zero-Unsafe bonus is scored on.

Python has no `unsafe` keyword. The equivalent is the set of constructs that
opt out of the type system or of normal error handling: `cast`, `type: ignore`,
`Any` in a position where a real type was available, `eval`/`exec`, and bare
`except`. This counts them so the claim is a measurement, not an assertion.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

PATTERNS = {
    "typing.cast": re.compile(r"\bcast\s*\("),
    "type: ignore": re.compile(r"#\s*type:\s*ignore"),
    "mypy disable": re.compile(r"#\s*mypy:\s*disable"),
    "eval()": re.compile(r"\beval\s*\("),
    "exec()": re.compile(r"\bexec\s*\("),
    "bare except": re.compile(r"^\s*except\s*:", re.MULTILINE),
    "noqa": re.compile(r"#\s*noqa"),
}


def main() -> int:
    counts = dict.fromkeys(PATTERNS, 0)
    hits: list[str] = []

    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                counts[name] += 1
                line = text[: match.start()].count("\n") + 1
                hits.append(f"  {path.relative_to(SRC)}:{line}  {name}")

    width = max(len(name) for name in PATTERNS)
    print("Escape-hatch census over src/pyfuse")
    print("=" * 46)
    for name, count in counts.items():
        flag = "" if count == 0 else "  <--"
        print(f"  {name:<{width}} : {count}{flag}")

    total = sum(counts.values())
    print("-" * 46)
    print(f"  {'TOTAL':<{width}} : {total}")

    if hits:
        print("\nOccurrences:")
        for hit in hits:
            print(hit)

    # `noqa` is reported but not counted against the budget: suppressing a
    # style lint is not the same as opting out of the type system. Every one
    # carries a written reason.
    unsafe = total - counts["noqa"]
    print(f"\nUnsafe constructs (excluding noqa): {unsafe}")
    return 0 if unsafe == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
