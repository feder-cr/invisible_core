"""Float arithmetic whose result does not depend on the Python version.

The same seed has to give the same bytes on every machine, and "every machine" includes every
Python the package supports. Two builtins of the standard library do not keep that promise, and
both sit on the path from a seed to a number a page can read:

* ``sum()`` of floats. Python 3.12 added compensated summation to it; 3.11 adds left to right.
  The two differ in the last bit as soon as the terms differ in size, which a list of log-normal
  step durations always does.
* ``math.log2()``. Measured on 3.11.17: 138 of 3000 results are not the correctly rounded value;
  on 3.12 and 3.13, 2 of 3000. The Fitts-law duration of a pointer movement is one such result.

Both are replaced here by an answer that is the exactly rounded one on every version:
``math.fsum`` is exact by construction, and the logarithm is computed in decimal with 60 digits
and rounded once. A result that is the correctly rounded value is the same value everywhere.

Everything else the seed's arithmetic uses - ``+ - * /``, ``math.exp``, ``math.sqrt``,
``math.hypot``, ``**``, the whole ``random`` API - was measured identical on 3.11, 3.12, 3.13
and 3.14 (tests/test_floats_are_version_independent.py keeps that on record).
"""
from __future__ import annotations

import math
from decimal import Context, Decimal
from typing import Iterable, Union

_CONTEXT = Context(prec=60)
_LN2 = _CONTEXT.ln(Decimal(2))


def exact_sum(values: Iterable[Union[int, float]]) -> Union[int, float]:
    """The exactly rounded sum. A list of integers (counts, booleans) stays an integer and
    stays the integer ``sum`` gives; anything with a float in it is summed with ``math.fsum``."""
    items = list(values)
    if all(isinstance(v, int) for v in items):
        return sum(items)
    return math.fsum(items)


def log2(x: float) -> float:
    """The correctly rounded base-2 logarithm, on every Python version.

    Outside the domain it is ``math.log2`` itself, so a zero, a negative, a NaN or an infinity
    raises or answers exactly as before."""
    x = float(x)
    if not (x > 0.0) or math.isinf(x):
        return math.log2(x)
    return float(_CONTEXT.divide(_CONTEXT.ln(Decimal(x)), _LN2))
