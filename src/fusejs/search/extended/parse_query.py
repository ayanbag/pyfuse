"""Extended-query parsing — the port of ``src/search/extended/parseQuery.ts``.

Splits a query into OR groups of AND terms:
``"^core go$ | rb$ | py$ xy$"`` becomes
``[["^core", "go$"], ["rb$"], ["py$", "xy$"]]``.
"""

from __future__ import annotations

import re
from typing import Any, Final

from ..._js import js_trim
from .matchers import MATCHERS, Matcher

# Stand-in for an escaped ``\|`` while splitting on the OR token. Spelled
# `chr(0)` rather than as an escape so the source file never contains a
# literal NUL byte, which Python refuses to compile.
_ESCAPED_PIPE: Final = chr(0)
_OR_TOKEN: Final = "|"

_ESCAPED_PIPE_RE: Final = re.compile(r"\\\|")


def tokenize(pattern: str) -> list[str]:
    """Split one OR group into its terms.

    Quoted multi-match tokens survive intact, inner spaces and quotes
    included, so ``'="said "test""'`` and ``'^"hello world"$'`` are each a
    single term.
    """
    tokens: list[str] = []
    length = len(pattern)
    i = 0

    while i < length:
        # Skip spaces.
        while i < length and pattern[i] == " ":
            i += 1
        if i >= length:
            break

        # Scan past prefix characters (=, !, ^, ') to see if a quote follows.
        j = i
        while j < length and pattern[j] != " " and pattern[j] != '"':
            j += 1

        if j < length and pattern[j] == '"':
            # Quoted token: prefix + "content", possibly with inner quotes.
            # The closing quote is the one followed by an optional `$` and
            # then a space or the end of the string.
            j += 1
            while j < length:
                if pattern[j] == '"':
                    nxt = j + 1
                    if nxt >= length or pattern[nxt] == " ":
                        j += 1
                        break
                    if pattern[nxt] == "$" and (
                        nxt + 1 >= length or pattern[nxt + 1] == " "
                    ):
                        j += 2
                        break
                j += 1
            tokens.append(pattern[i:j])
            i = j
        else:
            # Unquoted token: read to the next space.
            while j < length and pattern[j] != " ":
                j += 1
            tokens.append(pattern[i:j])
            i = j

    return tokens


def _get_match(pattern: str, expression: re.Pattern[str]) -> str | None:
    """The first capture group, or ``None`` if the pattern does not match."""
    match = expression.match(pattern)
    return match.group(1) if match else None


def parse_query(pattern: str, options: Any = None) -> list[list[Matcher]]:
    """Parse an extended query into OR groups of AND matchers.

    >>> [[m.type for m in group] for group in parse_query("^core go$ | rb$")]
    [['prefix-exact', 'suffix-exact'], ['suffix-exact']]
    """
    # Protect escaped pipes before splitting on the OR token.
    escaped = _ESCAPED_PIPE_RE.sub(_ESCAPED_PIPE, pattern)

    groups: list[list[Matcher]] = []

    for raw_group in escaped.split(_OR_TOKEN):
        restored = raw_group.replace(_ESCAPED_PIPE, "|")
        terms = [t for t in tokenize(js_trim(restored)) if t and js_trim(t)]

        matchers: list[Matcher] = []

        for term in terms:
            # 1. Quoted (multi-match) forms first.
            found = False
            for definition in MATCHERS:
                token = _get_match(term, definition.multi_regex)
                # An empty capture is falsy in JS and must not create a
                # matcher — `'` alone would otherwise build an include
                # matcher for "" and loop forever.
                if token:
                    matchers.append(definition.create(token, options))
                    found = True
                    break

            if found:
                continue

            # 2. Unquoted forms.
            for definition in MATCHERS:
                token = _get_match(term, definition.single_regex)
                if token:
                    matchers.append(definition.create(token, options))
                    break

        groups.append(matchers)

    return groups


__all__ = ["parse_query", "tokenize"]
