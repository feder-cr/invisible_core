"""When a caller's timeout runs out: one object, so "no limit" has one meaning.

⛔ WHY IT EXISTS (B278). Every wait in this package turned its timeout into a
point in time with `time.monotonic() + timeout`, which has no way to say "no
limit": Playwright's `timeout=0` is exactly that, and the wrapper turned it
into 30 s (`or 30000`) because nothing below could take anything else. The
launch had the same defect until 41.35.0 (B276). A `Deadline` of None seconds
never passes, and every wait that slices its time asks the deadline for the
slice instead of doing the subtraction itself.
"""
from __future__ import annotations

import time
from typing import Optional


class Deadline:
    """`seconds` from now, or never when `seconds` is None."""

    __slots__ = ("seconds", "_at")

    def __init__(self, seconds: Optional[float]) -> None:
        self.seconds = seconds
        self._at = None if seconds is None else time.monotonic() + seconds

    def passed(self) -> bool:
        return self._at is not None and time.monotonic() > self._at

    def left(self, floor: float = 0.0) -> Optional[float]:
        """Seconds left, never below `floor`; None when there is no limit, so
        it can be handed to a wait that takes None as "no bound"."""
        if self._at is None:
            return None
        return max(floor, self._at - time.monotonic())

    def slice(self, step: float) -> float:
        """How long one turn of a polling wait may sleep: `step`, or less when
        the deadline is nearer than that."""
        if self._at is None:
            return step
        return max(0.0, min(step, self._at - time.monotonic()))

    def until(self, moment: float) -> float:
        """Seconds from now to `moment` (a `time.monotonic()` value) or to the
        deadline, whichever is sooner."""
        end = moment if self._at is None else min(moment, self._at)
        return end - time.monotonic()
