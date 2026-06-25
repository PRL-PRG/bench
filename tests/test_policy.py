"""Stopping policies: FixedRuns, CoV, combinators.

Protocol: ``policy.start()`` returns a ``PolicyState``. ``observe(observation)``
feeds one ``Iteration``. ``satisfied()`` reports whether the policy has
converged (and is also valid before any observation). Run numbering lives in
the caller. A policy keeps its own counter if it needs one.
"""

import random
import statistics

import pytest

from bench import (
    CoefficientOfVariation,
    FixedRuns,
    MaxDuration,
    Iteration,
    PolicyState,
    Sample,
    StoppingPolicy,
)


def _mk(value: float, *, metric: str = "rt") -> Sample:
    return Sample(metric=metric, value=value, unit="s", lower_is_better=True)


def _rr(*samples: Sample, runtime: float = 0.0) -> Iteration:
    """An Iteration carrying the given samples (empty = failed observation)."""
    return Iteration(samples=list(samples), runtime=runtime)


# ---------------------------------------------------------------------------
# FixedRuns
# ---------------------------------------------------------------------------


def test_fixed_runs_counts_every_observation():
    state = FixedRuns(3).start()
    assert not state.satisfied()
    # Every run counts, success or failure (failed runs observe with no samples).
    state.observe(_rr())
    state.observe(_rr(_mk(1.0)))
    assert not state.satisfied()
    state.observe(_rr())
    assert state.satisfied()


def test_fixed_runs_zero_satisfied_at_entry():
    # FixedRuns(0) is satisfied before any run, so the loop takes none.
    assert FixedRuns(0).start().satisfied()


# ---------------------------------------------------------------------------
# CoefficientOfVariation
# ---------------------------------------------------------------------------


def test_cov_converges_on_stable_input():
    state = CoefficientOfVariation("rt", threshold=0.01, window=5, min_runs=10).start()
    for _ in range(14):
        state.observe(_rr(_mk(10.0)))
    assert state.satisfied()


def test_cov_does_not_converge_on_noisy_input():
    random.seed(42)
    state = CoefficientOfVariation("rt", threshold=0.01, window=5, min_runs=10).start()
    for _ in range(19):
        state.observe(_rr(_mk(10 + random.uniform(-2, 2))))
    assert not state.satisfied()


def test_cov_matches_reference_stdev():
    """Incremental CoV must match a fresh statistics.stdev() on the same window."""
    state = CoefficientOfVariation("rt", threshold=0.0, window=5, min_runs=5).start()
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    for v in values:
        state.observe(_rr(_mk(v)))
    window = values[-5:]
    ref = statistics.stdev(window) / statistics.mean(window)
    mean = state.sum / len(state.window)
    var = (
        max((state.sumsq / len(state.window)) - mean * mean, 0.0)
        * len(state.window)
        / (len(state.window) - 1)
    )
    inc = (var**0.5) / abs(mean)
    assert abs(inc - ref) < 1e-9


def test_cov_window_validates_min():
    with pytest.raises(ValueError):
        CoefficientOfVariation("rt", window=1).start()


def test_cov_ignores_unrelated_metrics():
    state = CoefficientOfVariation("rt", threshold=0.0, window=2, min_runs=2).start()
    for _ in range(3):
        state.observe(_rr(_mk(10.0, metric="other")))
    assert not state.satisfied()


def test_cov_raises_on_multiple_matching_samples():
    state = CoefficientOfVariation("rt", window=2, min_runs=2).start()
    with pytest.raises(ValueError, match="at most one"):
        state.observe(_rr(_mk(1.0), _mk(2.0)))


# ---------------------------------------------------------------------------
# Combinators
# ---------------------------------------------------------------------------


def test_and_requires_both():
    p = FixedRuns(3) & CoefficientOfVariation("rt", threshold=0.0, window=3, min_runs=3)
    state = p.start()
    for _ in range(3):
        state.observe(_rr(_mk(10.0)))
    # CoV: 3 obs at value=10.0 -> window full, mean=10, stdev=0, threshold=0 ok.
    # Fixed(3): 3 obs ok.
    assert state.satisfied()


def test_and_blocks_until_slowest():
    p = FixedRuns(5) & FixedRuns(3)
    state = p.start()
    for _ in range(3):
        state.observe(_rr(_mk(1.0)))
    assert not state.satisfied()
    for _ in range(2):
        state.observe(_rr(_mk(1.0)))
    assert state.satisfied()


def test_or_stops_at_first():
    p = FixedRuns(3) | FixedRuns(10)
    state = p.start()
    for _ in range(3):
        state.observe(_rr(_mk(1.0)))
    assert state.satisfied()


def test_at_least_at_most_sugar():
    p = (
        CoefficientOfVariation("rt", threshold=0.0, window=3, min_runs=3)
        .at_least(5)
        .at_most(7)
    )
    state = p.start()
    # 7 stable values: at_most kicks in at run 7.
    for _ in range(7):
        state.observe(_rr(_mk(10.0)))
    assert state.satisfied()


# ---------------------------------------------------------------------------
# Custom policy via subclassing (the Custom adapter was removed. Subclassing
# StoppingPolicy / PolicyState is the supported extension point).
# ---------------------------------------------------------------------------


class _SeenNState(PolicyState):
    def __init__(self, n: int):
        self.target = n
        self.cur = 0

    def observe(self, iteration):
        if any(s.value > 0 for s in iteration.samples):
            self.cur += 1

    def satisfied(self):
        return self.cur >= self.target


class SeenN(StoppingPolicy):
    def __init__(self, n: int):
        self.n = n

    def start(self) -> _SeenNState:
        return _SeenNState(self.n)


def test_custom_policy_via_subclassing():
    state = SeenN(2).start()
    assert not state.satisfied()
    state.observe(_rr(_mk(0.0)))
    state.observe(_rr(_mk(1.0)))
    state.observe(_rr(_mk(2.0)))
    assert state.satisfied()


def test_custom_policy_max_runs_defaults_to_unbounded():
    assert SeenN(3).max_runs() is None


# ---------------------------------------------------------------------------
# Introspection: max_runs()
# ---------------------------------------------------------------------------


def test_fixed_runs_max_runs():
    assert FixedRuns(10).max_runs() == 10


def test_cov_max_runs_is_unbounded():
    assert CoefficientOfVariation("rt").max_runs() is None


def test_and_max_runs_is_later_of_two():
    assert (FixedRuns(3) & FixedRuns(7)).max_runs() == 7


def test_and_max_runs_propagates_none():
    # If either branch can run forever, the And as a whole can too.
    assert (CoefficientOfVariation("rt") & FixedRuns(5)).max_runs() is None
    assert (FixedRuns(5) & CoefficientOfVariation("rt")).max_runs() is None


def test_or_max_runs_is_earlier_of_two():
    assert (FixedRuns(3) | FixedRuns(7)).max_runs() == 3


def test_or_max_runs_ignores_unbounded_child():
    # `.at_most(20)` desugars to `self | FixedRuns(20)`. The cap bounds the Or.
    assert (CoefficientOfVariation("rt") | FixedRuns(20)).max_runs() == 20
    assert CoefficientOfVariation("rt").at_most(20).max_runs() == 20


def test_or_max_runs_both_unbounded_is_none():
    assert (
        CoefficientOfVariation("rt") | CoefficientOfVariation("rt")
    ).max_runs() is None


# ---------------------------------------------------------------------------
# MaxDuration
# ---------------------------------------------------------------------------


def test_max_duration_is_count_unbounded():
    assert MaxDuration(5.0).max_runs() is None


def test_max_duration_satisfied_when_runtime_accumulates():
    state = MaxDuration(0.05).start()
    assert not state.satisfied()
    # A run with no measured runtime (e.g. spawn failure) doesn't spend budget.
    state.observe(_rr(_mk(1.0), runtime=0.0))
    assert not state.satisfied()
    state.observe(_rr(_mk(1.0), runtime=0.03))
    assert not state.satisfied()
    state.observe(_rr(_mk(1.0), runtime=0.03))
    assert state.satisfied()


def test_fixed_or_duration_stops_on_whichever_first():
    # Count cap reached before the (long) time bound.
    state = (FixedRuns(2) | MaxDuration(30.0)).start()
    state.observe(_rr(_mk(1.0)))
    assert not state.satisfied()
    state.observe(_rr(_mk(1.0)))
    assert state.satisfied()  # FixedRuns(2) fired first

    # Time bound reached before the (high) count cap.
    state = (FixedRuns(1000) | MaxDuration(0.05)).start()
    state.observe(_rr(_mk(1.0), runtime=0.06))
    assert state.satisfied()  # MaxDuration fired first


def test_fixed_or_duration_max_runs_is_the_count_cap():
    # _Or.max_runs() = min(count cap, unbounded) = the count cap.
    assert (FixedRuns(10) | MaxDuration(3.0)).max_runs() == 10
