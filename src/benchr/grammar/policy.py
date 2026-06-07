"""StoppingPolicy: decides when a benchmark phase has converged.

Configuration is frozen / hashable; mutable per-run state lives in a separate
``PolicyState`` produced by ``policy.start()``. Combinators:

    a & b   converged iff both have converged
    a | b   converged iff either has converged
    a.at_least(n)   == a & FixedRuns(n)
    a.at_most(n)    == a | FixedRuns(n)

Each policy also exposes two static introspection methods consumers can use
in place of ``isinstance`` checks:

    .max_runs()    int | None — upper bound on permitted runs, ``None`` = ∞
    .independent() bool       — whether runs can be reordered / parallelized

``observe(run, samples)`` is called by the Runner once per run, whether it
succeeded or failed. ``samples`` is the parsed output of one execution — empty
for a failed run, since failed runs emit no metrics.
"""

from __future__ import annotations

import abc
import math
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable

from benchr.report.sample import Sample


# ---------------------------------------------------------------------------
# Bases
# ---------------------------------------------------------------------------


class StoppingPolicy(abc.ABC):
    """Immutable policy configuration. Use ``.start()`` to get an observer."""

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

    # ----- static introspection ----------------------------------------
    #
    # Defaults are the conservative answers (unknown bound, order-dependent)
    # so adding a new policy subclass is safe by default. Concrete policies
    # and combinators override below.

    def max_runs(self) -> int | None:
        """Upper bound on the number of runs this policy will permit;
        ``None`` means unbounded by this policy."""
        return None

    def independent(self) -> bool:
        """True if convergence does not depend on observation order — i.e.
        the runs may be reordered or executed in parallel without changing
        whether/when the policy converges."""
        return False


class PolicyState(abc.ABC):
    """Mutable per-run observer."""

    @abc.abstractmethod
    def observe(self, run: int, samples: Iterable[Sample]) -> None: ...

    @abc.abstractmethod
    def converged(self) -> bool: ...


# ---------------------------------------------------------------------------
# FixedRuns: the simplest policy. Converges after seeing N runs.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FixedRuns(StoppingPolicy):
    n: int

    def start(self) -> _FixedState:
        return _FixedState(self.n)

    def max_runs(self) -> int:
        return self.n

    def independent(self) -> bool:
        return True


class _FixedState(PolicyState):
    __slots__ = ("target", "cur")

    def __init__(self, target: int):
        self.target = target
        self.cur = 0

    def observe(self, run: int, samples: Iterable[Sample]) -> None:
        # Count every run, success or failure. ``.runs(N)`` means "N attempts",
        # so a crashing benchmark stops after N runs instead of retrying until
        # the consecutive-failure cap.
        self.cur += 1

    def converged(self) -> bool:
        return self.cur >= self.target


# ---------------------------------------------------------------------------
# CoefficientOfVariation: O(1) per observation via running sums of x, x².
# ---------------------------------------------------------------------------


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

    def observe(self, run: int, samples: Iterable[Sample]) -> None:
        # CoV tracks one scalar per run. More than one matching sample is
        # ambiguous (which one is "the" metric?) and would inflate the
        # window / min_runs counters, so reject it loudly.
        matching = [s.value for s in samples if s.metric == self.cfg.metric]
        if len(matching) > 1:
            raise ValueError(
                f"CoefficientOfVariation metric {self.cfg.metric!r} matched "
                f"{len(matching)} samples in run {run}; it expects at most one "
                f"per run. Restrict the processor to a single line (e.g. "
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

    def converged(self) -> bool:
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
# Custom: user-supplied callable. The user is responsible for any state.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Custom(StoppingPolicy):
    """Adapter for ad-hoc policies.

    Pass a state factory ``state_factory: () -> PolicyState``. (Wrapping a
    plain function won't usually compose well because state is needed.)
    """

    state_factory: Callable[[], PolicyState]

    def start(self) -> PolicyState:
        return self.state_factory()


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

    def independent(self) -> bool:
        return self.a.independent() and self.b.independent()


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

    def independent(self) -> bool:
        return self.a.independent() and self.b.independent()


class _PairState(PolicyState):
    __slots__ = ("a", "b", "op")

    def __init__(self, a: PolicyState, b: PolicyState, op: Callable[[Iterable[bool]], bool]):
        self.a = a
        self.b = b
        self.op = op

    def observe(self, run: int, samples: Iterable[Sample]) -> None:
        # Materialize once so both children see the same iterable.
        ss = list(samples)
        self.a.observe(run, ss)
        self.b.observe(run, ss)

    def converged(self) -> bool:
        return self.op((self.a.converged(), self.b.converged()))
