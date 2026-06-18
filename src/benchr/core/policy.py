"""StoppingPolicy: decides when to stop taking runs.

Life-cycle: The benchmarking loop first calls ``start`` which produces a
stateful ``PolicyState``. For each execution it calls ``observe(run, samples)``
to feed that run's samples and ``satisfied()`` to check whether the policy has
converged. ``satisfied()`` is also checked up front, so a policy that converges
before any run (e.g. ``FixedRuns(0)``) takes no runs at all.

Combinators:
    a & b   satisfied iff both are satisfied
    a | b   satisfied iff either is satisfied
    a.at_least(n)   == a & FixedRuns(n)
    a.at_most(n)    == a | FixedRuns(n)
"""

from __future__ import annotations

import abc
import math
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from benchr.core.sample import Observation


# ---------------------------------------------------------------------------
# Bases
# ---------------------------------------------------------------------------


class StoppingPolicy(abc.ABC):
    __slots__ = ()

    @abc.abstractmethod
    def start(self) -> PolicyState: ...

    def __and__(self, other: StoppingPolicy) -> StoppingPolicy:
        return _And(self, other)

    def __or__(self, other: StoppingPolicy) -> StoppingPolicy:
        return _Or(self, other)

    def at_least(self, n: int) -> StoppingPolicy:
        return self & FixedRuns(n)

    def at_most(self, n: int) -> StoppingPolicy:
        return self | FixedRuns(n)

    def max_runs(self) -> int | None:
        """Upper bound on the number of runs this policy will permit.
        ``None`` means unbounded by this policy."""
        return None


class PolicyState(abc.ABC):
    """Mutable per-run observer."""

    __slots__ = ()

    @abc.abstractmethod
    def observe(self, observation: Observation) -> None: ...

    @abc.abstractmethod
    def satisfied(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class FixedRuns(StoppingPolicy):
    n: int

    def start(self) -> _FixedState:
        return _FixedState(self.n)

    def max_runs(self) -> int:
        return self.n


class _FixedState(PolicyState):
    __slots__ = ("target", "cur")

    def __init__(self, target: int):
        self.target = target
        self.cur = 0

    def observe(self, observation: Observation) -> None:
        self.cur += 1

    def satisfied(self) -> bool:
        return self.cur >= self.target


def coerce_policy(p: StoppingPolicy | int) -> StoppingPolicy:
    """Accept the ``int`` shorthand for a stopping policy: ``n`` = FixedRuns(n)."""
    return p if isinstance(p, StoppingPolicy) else FixedRuns(p)


@dataclass(frozen=True, slots=True)
class CoefficientOfVariation(StoppingPolicy):
    metric: str
    threshold: float = 0.02
    window: int = 5
    min_runs: int = 10

    def start(self) -> _CoVState:
        return _CoVState(self)


class _CoVState(PolicyState):
    __slots__ = ("cfg", "window", "sum", "sumsq", "n_runs")

    def __init__(self, cfg: CoefficientOfVariation):
        if cfg.window < 2:
            raise ValueError("CoV window must be >= 2 for stdev")
        self.cfg = cfg
        self.window: deque[float] = deque(maxlen=cfg.window)
        self.sum = 0.0
        self.sumsq = 0.0
        self.n_runs = 0

    def observe(self, observation: Observation) -> None:
        # CoV tracks one scalar per run. More than one matching sample is
        # ambiguous and would inflate the
        # window / min_runs counters, so reject it loudly.
        matching = [s.value for s in observation.samples if s.metric == self.cfg.metric]
        if len(matching) > 1:
            raise ValueError(
                f"CoefficientOfVariation metric {self.cfg.metric!r} matched "
                f"{len(matching)} samples in a single run; it expects at most "
                f"one per run. Restrict the metric to a single line (e.g. "
                f".last_line()) or watch a different metric."
            )
        for value in matching:  # 0 or 1
            if len(self.window) == self.window.maxlen:
                old = self.window[0]
                self.sum -= old
                self.sumsq -= old * old
            self.window.append(value)
            self.sum += value
            self.sumsq += value * value
            self.n_runs += 1

    def satisfied(self) -> bool:
        cfg = self.cfg
        if self.n_runs < cfg.min_runs or len(self.window) < cfg.window:
            return False
        n = len(self.window)
        mean = self.sum / n
        if mean == 0:
            return False
        # Var = (E[X²] - E[X]²) * n / (n-1)   (Bessel correction)
        var = max((self.sumsq / n) - mean * mean, 0.0) * n / (n - 1)
        return math.sqrt(var) / abs(mean) <= cfg.threshold


# ---------------------------------------------------------------------------
# Combinators
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _And(StoppingPolicy):
    a: StoppingPolicy
    b: StoppingPolicy

    def start(self) -> _PairState:
        return _PairState(self.a.start(), self.b.start(), all)

    def max_runs(self) -> int | None:
        # Stops only when both converge → worst case is the later of the two.
        # If either child is unbounded, the And is unbounded.
        a, b = self.a.max_runs(), self.b.max_runs()
        if a is None or b is None:
            return None
        return max(a, b)


@dataclass(frozen=True, slots=True)
class _Or(StoppingPolicy):
    a: StoppingPolicy
    b: StoppingPolicy

    def start(self) -> _PairState:
        return _PairState(self.a.start(), self.b.start(), any)

    def max_runs(self) -> int | None:
        # Stops as soon as either converges → at most the earlier of the two.
        # Treat ``None`` as Inf, an unbounded child can't tighten the bound.
        a, b = self.a.max_runs(), self.b.max_runs()
        if a is None:
            return b
        if b is None:
            return a
        return min(a, b)


class _PairState(PolicyState):
    __slots__ = ("a", "b", "op")

    def __init__(
        self, a: PolicyState, b: PolicyState, op: Callable[[Iterable[bool]], bool]
    ):
        self.a = a
        self.b = b
        self.op = op

    def observe(self, observation: Observation) -> None:
        self.a.observe(observation)
        self.b.observe(observation)

    def satisfied(self) -> bool:
        return self.op((self.a.satisfied(), self.b.satisfied()))
