"""StoppingPolicy: decides when to stop taking runs."""

from __future__ import annotations

import abc
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bench.model.results import Execution


class StoppingPolicy(abc.ABC):
    __slots__ = ()

    @abc.abstractmethod
    def start(self) -> PolicyState: ...

    def __and__(self, other: StoppingPolicy) -> StoppingPolicy:
        return AndStoppingPolicy(self, other)

    def __or__(self, other: StoppingPolicy) -> StoppingPolicy:
        return OrStoppingPolicy(self, other)

    def at_least(self, n: int) -> StoppingPolicy:
        return self & FixedRuns(n)

    def at_most(self, n: int) -> StoppingPolicy:
        return self | FixedRuns(n)

    def max_runs(self) -> int | None:
        """Upper bound on the number of runs this policy will permit.
        `None` means unbounded by this policy."""
        return None


class PolicyState(abc.ABC):
    """Mutable per-run observer."""

    __slots__ = ()

    @abc.abstractmethod
    def observe(self, execution: Execution) -> None: ...

    @abc.abstractmethod
    def satisfied(self) -> bool: ...


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


def format_policy(p: StoppingPolicy) -> str:
    """A stopping policy's run bound as a string ("unbounded" when open-ended)."""
    n = p.max_runs()
    return str(n) if n is not None else "unbounded"


# ---------------------------------------------------------------------------
# Fixed runs
# ---------------------------------------------------------------------------


class FixedRuns(StoppingPolicy):
    __slots__ = ("n",)

    def __init__(self, n: int) -> None:
        self.n = n

    def start(self) -> FixedRunsState:
        return FixedRunsState(self.n)

    def max_runs(self) -> int:
        return self.n


class FixedRunsState(PolicyState):
    __slots__ = ("target", "cur")

    def __init__(self, target: int):
        self.target = target
        self.cur = 0

    def observe(self, execution: Execution) -> None:
        self.cur += 1

    def satisfied(self) -> bool:
        return self.cur >= self.target


# ---------------------------------------------------------------------------
# Combinators
# ---------------------------------------------------------------------------


class AndStoppingPolicy(StoppingPolicy):
    __slots__ = ("a", "b")

    def __init__(self, a: StoppingPolicy, b: StoppingPolicy) -> None:
        super().__init__()
        self.a = a
        self.b = b

    def start(self) -> PairState:
        return PairState(self.a.start(), self.b.start(), lambda a, b: a and b)

    def max_runs(self) -> int | None:
        # Stops only when both converge, so worst case is the later of the two.
        # If either child is unbounded, the And is unbounded.
        a, b = self.a.max_runs(), self.b.max_runs()
        if a is None or b is None:
            return None
        return max(a, b)


class OrStoppingPolicy(StoppingPolicy):
    __slots__ = ("a", "b")

    def __init__(self, a: StoppingPolicy, b: StoppingPolicy) -> None:
        super().__init__()
        self.a = a
        self.b = b

    def start(self) -> PairState:
        return PairState(self.a.start(), self.b.start(), lambda a, b: a or b)

    def max_runs(self) -> int | None:
        # Stops as soon as either converges, so at most the earlier of the two.
        # Treat `None` as Inf, an unbounded child can't tighten the bound.
        a, b = self.a.max_runs(), self.b.max_runs()
        if a is None:
            return b
        if b is None:
            return a
        return min(a, b)


class PairState(PolicyState):
    __slots__ = ("a", "b", "op")

    def __init__(
        self, a: PolicyState, b: PolicyState, op: Callable[[bool, bool], bool]
    ):
        self.a = a
        self.b = b
        self.op = op

    def observe(self, execution: Execution) -> None:
        self.a.observe(execution)
        self.b.observe(execution)

    def satisfied(self) -> bool:
        return self.op(self.a.satisfied(), self.b.satisfied())
