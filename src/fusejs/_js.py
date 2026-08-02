"""JavaScript semantics that Python does not share.

The port is only useful if it agrees with fuse.js *numerically*, so a handful
of JS behaviours have to be reproduced deliberately rather than approximated
with the nearest Python builtin. Each function here exists because the obvious
Python equivalent diverges; the divergence is named in the docstring and in
``DECISIONS.md``.
"""

from __future__ import annotations

import math
from typing import Any, Final

# ── 32-bit integer semantics ───────────────────────────────────────
#
# JS bitwise operators coerce both operands to 32-bit integers and return a
# *signed* 32-bit result. Python ints are arbitrary precision, so `x << 1`
# grows without bound where JS would silently drop the overflow bit.
#
# The Bitap scan only ever tests its bit arrays for truthiness against a
# single-bit mask, so emulating the unsigned 32-bit window is sufficient and
# avoids the sign-bit gymnastics: `bitArr[j] & mask` is non-zero in JS exactly
# when it is non-zero here.
UINT32_MASK: Final = 0xFFFFFFFF

# `Number.EPSILON` — the smallest float64 step above 1.0. Used by the scoring
# combinator as a stand-in for a zero score so a perfect match on a weighted
# key does not annihilate the product.
EPSILON: Final = 2.0**-52


def to_uint32(value: int) -> int:
    """Truncate to the low 32 bits, mirroring JS bitwise-operator width."""
    return value & UINT32_MASK


def js_round(value: float) -> int:
    """``Math.round``: ties go toward +Infinity, not to even.

    Python's :func:`round` is banker's rounding — ``round(0.5) == 0`` — while
    JS rounds half up. The field-length norm quantises through this function,
    so the difference is directly observable in every score.
    """
    if math.isnan(value):
        raise ValueError("cannot round NaN")
    if math.isinf(value):
        raise OverflowError("cannot round infinity")
    return math.floor(value + 0.5)


def js_str(value: object) -> str:
    """``String(value)`` for the scalar types fuse.js indexes.

    Differs from :func:`str` on the two types that actually reach it:
    booleans (``true``/``false``, not ``True``/``False``) and whole floats
    (``3``, not ``3.0``) — Python has a distinct ``int`` type where JS numbers
    are all doubles, so an unguarded ``str()`` would index ``"3.0"`` for a
    value fuse.js indexes as ``"3"``.
    """
    if isinstance(value, str):
        return value
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, float):
        return _js_number_to_string(value)
    if isinstance(value, int):  # bool already handled above
        return str(value)
    return str(value)


def _js_number_to_string(value: float) -> str:
    """``Number.prototype.toString`` for float64 values."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0.0:
        # JS renders -0 as "0" in string contexts (`String(-0) === "0"`);
        # only `Object.is` / `1/x` distinguish it.
        return "0"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    # Python's repr already produces the shortest round-tripping decimal, the
    # same guarantee JS makes; only the exponent spelling differs (Python
    # zero-pads to two digits, "1e-07", where JS emits "1e-7").
    text = repr(value)
    if "e" in text:
        mantissa, _, exponent = text.partition("e")
        sign = "-" if exponent.startswith("-") else "+"
        digits = exponent.lstrip("+-").lstrip("0") or "0"
        text = f"{mantissa}e{sign}{digits}"
    return text


# ── String trimming ────────────────────────────────────────────────
#
# `String.prototype.trim` strips the WhiteSpace and LineTerminator production
# *plus* U+FEFF (ZERO WIDTH NO-BREAK SPACE), which Python does not consider
# whitespace. A field consisting solely of a BOM is blank to fuse.js but not
# to `str.strip()`.
_JS_TRIM_CHARS: Final = "\t\n\v\f\r\x20\xa0                　﻿"


def js_trim(value: str) -> str:
    """``String.prototype.trim`` — the ECMAScript whitespace set."""
    return value.strip(_JS_TRIM_CHARS)


def js_index_of(text: str, pattern: str, start: int = 0) -> int:
    """``String.prototype.indexOf`` with its clamping behaviour.

    ``str.find`` returns ``-1`` for a start position past the end of the
    string, but JS returns ``len(text)`` when the needle is empty — a
    difference that turns a terminating loop into an infinite one.
    """
    start = max(start, 0)
    if start > len(text):
        return len(text) if not pattern else -1
    return text.find(pattern, start)


def is_truthy(value: Any) -> bool:
    """JS truthiness for the values that reach the query parser.

    Kept as a named function so call sites mirroring ``if (token)`` — where an
    empty capture group must be treated as "no match" — read explicitly.
    """
    return bool(value)
