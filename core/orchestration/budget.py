from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


class RunAborted(RuntimeError):
    """Base for every reason a run stops before finishing normally."""

    status = "failed"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class BudgetExceeded(RunAborted):
    status = "budget_exceeded"


class RunTimedOut(RunAborted):
    status = "timed_out"


class RunCancelled(RunAborted):
    status = "cancelled"


CancelCheck = Callable[[], Awaitable[bool]]


@dataclass
class BudgetGuard:
    """Hard stop for a run.

    Runaway multi-agent loops are the main way to burn money by accident, so
    step count, spend and wall-clock time are all bounded, and none of them can
    be waived by a workflow — a workflow may only ask for something lower than
    the platform ceiling.
    """

    max_steps: int
    max_cost_microcents: int
    max_seconds: float = 3600.0
    cancel_check: CancelCheck | None = None

    steps_used: int = 0
    spent_microcents: int = 0
    started_at: float = field(default_factory=time.monotonic)
    _warned: bool = False

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def remaining_microcents(self) -> int:
        return max(self.max_cost_microcents - self.spent_microcents, 0)

    @property
    def warn_threshold(self) -> int:
        return int(self.max_cost_microcents * 0.8)

    async def check_before_step(self) -> None:
        """Every guard is evaluated *before* spending, never after — once a
        provider call is made the money is gone regardless of what we decide."""
        if self.cancel_check is not None and await self.cancel_check():
            raise RunCancelled("cancelled by user")
        if self.steps_used >= self.max_steps:
            raise BudgetExceeded(f"step limit reached ({self.max_steps} steps)")
        if self.spent_microcents >= self.max_cost_microcents:
            raise BudgetExceeded(
                f"cost limit reached ({self.max_cost_microcents / 1_000_000:.2f} cents)"
            )
        if self.elapsed_seconds >= self.max_seconds:
            raise RunTimedOut(f"time limit reached ({self.max_seconds:.0f}s)")

    def record(self, cost_microcents: int) -> None:
        self.steps_used += 1
        self.spent_microcents += cost_microcents

    def take_warning(self) -> bool:
        """True once, the first time spend crosses 80% of the ceiling."""
        if self._warned or self.spent_microcents < self.warn_threshold:
            return False
        self._warned = True
        return True
