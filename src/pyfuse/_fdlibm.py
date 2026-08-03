"""``Math.pow`` as JavaScript computes it.

CPython's ``**`` delegates to the platform libm, which on every platform we
care about is *correctly rounded*. V8's ``Math.pow`` does not: it routes to
``base::ieee754::pow``, a port of Sun's fdlibm ``__ieee754_pow``, which
carries up to 1 unit-in-the-last-place of error.

That makes ``x ** y`` and ``Math.pow(x, y)`` different functions. Measured
over random inputs in the range the scorer actually uses, they disagree on
about **10% of calls, always by exactly 1 ULP** — and
:func:`~pyfuse.core.compute_score.compute_score_single` multiplies one ``pow``
per matched key, so the discrepancy compounds into the score every user sees.

Reproducing fuse.js's numbers therefore means reproducing fdlibm's rounding,
not improving on it. This module is a direct transcription of
``__ieee754_pow`` — deliberately unidiomatic, structured exactly like the C so
it can be diffed against the original. See DECISIONS.md.

Not a general-purpose math library: use it where JS parity matters, and
:func:`math.pow` everywhere else.
"""

from __future__ import annotations

import math
import struct
from typing import Final

# ── Bit-level access to float64 ────────────────────────────────────

_MASK32: Final = 0xFFFFFFFF
_MASK64: Final = 0xFFFFFFFFFFFFFFFF


def _bits(value: float) -> int:
    packed: int = struct.unpack("<Q", struct.pack("<d", value))[0]
    return packed


def _from_bits(bits: int) -> float:
    value: float = struct.unpack("<d", struct.pack("<Q", bits & _MASK64))[0]
    return value


def _high_word(value: float) -> int:
    """The upper 32 bits, as an unsigned integer."""
    return (_bits(value) >> 32) & _MASK32


def _low_word(value: float) -> int:
    """The lower 32 bits."""
    return _bits(value) & _MASK32


def _set_high_word(value: float, high: int) -> float:
    return _from_bits(((high & _MASK32) << 32) | (_bits(value) & _MASK32))


def _set_low_word(value: float, low: int) -> float:
    return _from_bits((_bits(value) & (_MASK32 << 32)) | (low & _MASK32))


def _div(a: float, b: float) -> float:
    """IEEE-754 division, the way C and JS do it.

    Python raises :class:`ZeroDivisionError` where both C and JS produce a
    signed infinity or a NaN. The C source divides by values that can legally
    be zero — ``1/x`` for ``x == 0`` is how ``pow(0, -1)`` reaches ``+inf`` —
    so the exception has to be turned back into the value.
    """
    if b == 0.0:
        if a == 0.0 or math.isnan(a):
            return math.nan
        negative = (math.copysign(1.0, a) < 0) != (math.copysign(1.0, b) < 0)
        return -math.inf if negative else math.inf
    return a / b


# ── fdlibm constants (e_pow.c) ─────────────────────────────────────

_BP: Final = (1.0, 1.5)
_DP_H: Final = (0.0, 5.84962487220764160156e-01)
_DP_L: Final = (0.0, 1.35003920212974897128e-08)

_TWO53: Final = 9007199254740992.0
_HUGE: Final = 1.0e300
_TINY: Final = 1.0e-300

# Polynomial coefficients for (3/2)*(log(x) - 2s - 2/3*s**3).
_L1: Final = 5.99999999999994648725e-01
_L2: Final = 4.28571428578550184252e-01
_L3: Final = 3.33333329818377432918e-01
_L4: Final = 2.72728123808534006489e-01
_L5: Final = 2.30660745775561754067e-01
_L6: Final = 2.06975017800338417784e-01

_P1: Final = 1.66666666666666019037e-01
_P2: Final = -2.77777777770155933842e-03
_P3: Final = 6.61375632143793436117e-05
_P4: Final = -1.65339022054652515390e-06
_P5: Final = 4.13813679705723846039e-08

_LG2: Final = 6.93147180559945286227e-01
_LG2_H: Final = 6.93147182464599609375e-01
_LG2_L: Final = -1.90465429995776804525e-09

# -(1024 - log2(overflow + 0.5ulp))
_OVT: Final = 8.0085662595372944372e-17

_CP: Final = 9.61796693925975554329e-01  # 2/(3 ln 2)
_CP_H: Final = 9.61796700954437255859e-01  # float(cp)
_CP_L: Final = -7.02846165095275826516e-09  # tail of cp_h

_IVLN2: Final = 1.44269504088896338700e00  # 1/ln 2
_IVLN2_H: Final = 1.44269502162933349609e00  # 24 bits of 1/ln 2
_IVLN2_L: Final = 1.92596299112661746887e-08  # tail


def _classify_odd_integer(iy: int, ly: int) -> int:
    """Whether ``y`` is an integer, and if so its parity.

    Returns 0 when ``y`` is not an integer, 1 when it is an odd integer, and
    2 when it is an even integer. Only consulted for negative bases, where
    the parity decides the sign of the result.
    """
    if iy >= 0x43400000:
        # Magnitude beyond 2**53: every representable value is an even int.
        return 2

    if iy >= 0x3FF00000:
        k = (iy >> 20) - 0x3FF
        if k > 20:
            j = ly >> (52 - k)
            if ((j << (52 - k)) & _MASK32) == ly:
                return 2 - (j & 1)
        elif ly == 0:
            j = iy >> (20 - k)
            if (j << (20 - k)) == iy:
                return 2 - (j & 1)

    return 0


def pow(x: float, y: float) -> float:
    """``Math.pow(x, y)`` — bit-for-bit as V8 computes it.

    >>> pow(0.1, 0.3846666666666666)
    0.41241393704645002
    >>> pow(2.0, 10.0)
    1024.0
    """
    x = float(x)
    y = float(y)

    hx = _high_word(x)
    lx = _low_word(x)
    hy = _high_word(y)
    ly = _low_word(y)

    ix = hx & 0x7FFFFFFF
    iy = hy & 0x7FFFFFFF

    x_is_negative = hx >= 0x80000000
    y_is_negative = hy >= 0x80000000

    # y == 0: x**0 is 1 for every x, NaN included.
    if (iy | ly) == 0:
        return 1.0

    # Either operand NaN.
    if (
        ix > 0x7FF00000
        or (ix == 0x7FF00000 and lx != 0)
        or iy > 0x7FF00000
        or (iy == 0x7FF00000 and ly != 0)
    ):
        return x + y

    y_int_kind = _classify_odd_integer(iy, ly) if x_is_negative else 0

    # Special values of y.
    if ly == 0:
        if iy == 0x7FF00000:  # y is +-inf
            if ((ix - 0x3FF00000) | lx) == 0:
                return y - y  # (+-1)**+-inf is NaN
            if ix >= 0x3FF00000:  # (|x|>1)**+-inf
                return y if not y_is_negative else 0.0
            return -y if y_is_negative else 0.0  # (|x|<1)**+-inf
        if iy == 0x3FF00000:  # y is +-1
            return _div(1.0, x) if y_is_negative else x
        if hy == 0x40000000:  # y is 2
            return x * x
        if hy == 0x3FE00000 and not x_is_negative:  # y is 0.5, x >= +0
            return math.sqrt(x)

    ax = abs(x)

    # Special values of x: +-0, +-inf, +-1.
    if lx == 0 and ix in (0x7FF00000, 0x00000000, 0x3FF00000):
        z = ax
        if y_is_negative:
            z = _div(1.0, z)
        if x_is_negative:
            if ((ix - 0x3FF00000) | y_int_kind) == 0:
                return math.nan  # (-1)**non-integer
            if y_int_kind == 1:
                z = -z  # (x<0)**odd = -(|x|**odd)
        return z

    # In the C, `n` is 0 for a negative base and -1 otherwise; the combined
    # tests below rely on -1 having every bit set.
    n = 0 if x_is_negative else -1

    # (x<0)**(non-integer) is NaN.
    if (n | y_int_kind) == 0:
        return math.nan

    # Sign of the result: negative only for a negative base to an odd power.
    s = 1.0
    if (n | (y_int_kind - 1)) == 0:
        s = -1.0

    if iy > 0x41E00000:  # |y| > 2**31
        if iy > 0x43F00000:  # |y| > 2**64: certain over/underflow
            if ix <= 0x3FEFFFFF:
                return _HUGE * _HUGE if y_is_negative else _TINY * _TINY
            if ix >= 0x3FF00000:
                return _HUGE * _HUGE if not y_is_negative else _TINY * _TINY
        # Over/underflow unless x is very close to 1.
        if ix < 0x3FEFFFFF:
            return s * _HUGE * _HUGE if y_is_negative else s * _TINY * _TINY
        if ix > 0x3FF00000:
            return s * _HUGE * _HUGE if not y_is_negative else s * _TINY * _TINY

        # |1-x| is tiny (<= 2**-20), so log(x) ~ x - x^2/2 + x^3/3 - x^4/4.
        t = ax - 1.0
        w = (t * t) * (0.5 - t * (0.3333333333333333333333 - t * 0.25))
        u = _IVLN2_H * t
        v = t * _IVLN2_L - w * _IVLN2
        t1 = u + v
        t1 = _set_low_word(t1, 0)
        t2 = v - (t1 - u)
    else:
        n = 0
        # Handle subnormal x by scaling it up first.
        if ix < 0x00100000:
            ax *= _TWO53
            n -= 53
            ix = _high_word(ax)

        n += (ix >> 20) - 0x3FF
        j = ix & 0x000FFFFF

        # Normalise ix, then pick the interval.
        ix = j | 0x3FF00000
        if j <= 0x3988E:  # |x| < sqrt(3/2)
            k = 0
        elif j < 0xBB67A:  # |x| < sqrt(3)
            k = 1
        else:
            k = 0
            n += 1
            ix -= 0x00100000

        ax = _set_high_word(ax, ix)

        # ss = s_h + s_l = (x-1)/(x+1), or (x-1.5)/(x+1.5).
        u = ax - _BP[k]
        v = 1.0 / (ax + _BP[k])
        ss = u * v
        s_h = _set_low_word(ss, 0)

        # t_h = (ax + bp[k]) rounded to 24 bits.
        t_h = _set_high_word(0.0, ((ix >> 1) | 0x20000000) + 0x00080000 + (k << 18))
        t_l = ax - (t_h - _BP[k])
        s_l = v * ((u - s_h * t_h) - s_h * t_l)

        # log(ax)
        s2 = ss * ss
        r = (
            s2
            * s2
            * (_L1 + s2 * (_L2 + s2 * (_L3 + s2 * (_L4 + s2 * (_L5 + s2 * _L6)))))
        )
        r += s_l * (s_h + ss)
        s2 = s_h * s_h
        t_h = _set_low_word(3.0 + s2 + r, 0)
        t_l = r - ((t_h - 3.0) - s2)

        # u + v = ss * (1 + ...)
        u = s_h * t_h
        v = s_l * t_h + t_l * ss

        # 2/(3 log2) * (ss + ...)
        p_h = _set_low_word(u + v, 0)
        p_l = v - (p_h - u)
        z_h = _CP_H * p_h
        z_l = _CP_L * p_h + p_l * _CP + _DP_L[k]

        # log2(ax) = n + dp_h[k] + z_h + z_l
        t = float(n)
        t1 = _set_low_word(((z_h + z_l) + _DP_H[k]) + t, 0)
        t2 = z_l - (((t1 - t) - _DP_H[k]) - z_h)

    # Split y into y1 + y2 and form (y1 + y2) * (t1 + t2).
    y1 = _set_low_word(y, 0)
    p_l = (y - y1) * t1 + y * t2
    p_h = y1 * t1
    z = p_l + p_h

    j = _high_word(z)
    i = _low_word(z)
    j_signed = j - 0x100000000 if j >= 0x80000000 else j

    if j_signed >= 0x40900000:  # z >= 1024
        if ((j_signed - 0x40900000) | i) != 0:
            return s * _HUGE * _HUGE  # overflow
        if p_l + _OVT > z - p_h:
            return s * _HUGE * _HUGE  # overflow
    elif (j & 0x7FFFFFFF) >= 0x4090CC00:  # z <= -1075
        if ((j_signed - (0xC090CC00 - 0x100000000)) | i) != 0:
            return s * _TINY * _TINY  # underflow
        if p_l <= z - p_h:
            return s * _TINY * _TINY  # underflow

    # Compute 2**(p_h + p_l).
    i = j & 0x7FFFFFFF
    k = (i >> 20) - 0x3FF
    n = 0

    if i > 0x3FE00000:  # |z| > 0.5 -> n = round(z)
        n = j + (0x00100000 >> (k + 1))
        k = ((n & 0x7FFFFFFF) >> 20) - 0x3FF
        t = _set_high_word(0.0, n & ~(0x000FFFFF >> k) & _MASK32)
        n = ((n & 0x000FFFFF) | 0x00100000) >> (20 - k)
        if j_signed < 0:
            n = -n
        p_h -= t

    t = _set_low_word(p_l + p_h, 0)
    u = t * _LG2_H
    v = (p_l - (t - p_h)) * _LG2 + t * _LG2_L
    z = u + v
    w = v - (z - u)
    t = z * z
    t1 = z - t * (_P1 + t * (_P2 + t * (_P3 + t * (_P4 + t * _P5))))
    r = _div(z * t1, t1 - 2.0) - (w + z * w)
    z = 1.0 - (r - z)

    j = _high_word(z) + (n << 20)

    if (j >> 20) <= 0:  # noqa: SIM108 — mirrors the C branch structure
        z = math.ldexp(z, n)  # subnormal output
    else:
        z = _set_high_word(z, j)

    return s * z


__all__ = ["pow"]
