

from __future__ import annotations

from typing import TYPE_CHECKING

from bench.core.policy.base import PolicyState, StoppingPolicy

if TYPE_CHECKING:
    from bench.model.results import Execution

class MaxDuration(StoppingPolicy):
    """Stop once `seconds` of cumulative command runtime have been observed.

    Counts only time spent running the benchmark command."""

    __slots__ = ("seconds",)

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def start(self) -> MaxDurationState:
        return MaxDurationState(self.seconds)


class MaxDurationState(PolicyState):
    __slots__ = ("seconds", "elapsed")

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.elapsed = 0.0

    def observe(self, execution: Execution) -> None:
        self.elapsed += execution.runtime

    def satisfied(self) -> bool:
        return self.elapsed >= self.seconds
