"""Stopping policies: FixedRuns, CoV, combinators."""

import statistics

import pytest

from benchr import CoefficientOfVariation, Custom, FixedRuns, PolicyState, Sample


def _mk(value: float, run: int, *, metric: str = "rt") -> Sample:
    return Sample(
        suite="S", benchmark="B", info=(), run=run, phase="measure",
        metric=metric, value=value, unit="s", lower_is_better=True,
    )


# ---------------------------------------------------------------------------
# FixedRuns
# ---------------------------------------------------------------------------


def test_fixed_runs_counts_every_observation():
    state = FixedRuns(3).start()
    assert not state.converged()
    # Every run counts — success or failure (failed runs observe with []).
    state.observe(1, [])
    state.observe(2, [_mk(1.0, 2)])
    assert not state.converged()
    state.observe(3, [])
    assert state.converged()


def test_fixed_runs_zero_converges_at_entry():
    assert FixedRuns(0).start().converged()


# ---------------------------------------------------------------------------
# CoefficientOfVariation
# ---------------------------------------------------------------------------


def test_cov_converges_on_stable_input():
    state = CoefficientOfVariation("rt", threshold=0.01, window=5, min_runs=10).start()
    for i in range(1, 15):
        state.observe(i, [_mk(10.0, i)])
    assert state.converged()


def test_cov_does_not_converge_on_noisy_input():
    import random
    random.seed(42)
    state = CoefficientOfVariation("rt", threshold=0.01, window=5, min_runs=10).start()
    for i in range(1, 20):
        state.observe(i, [_mk(10 + random.uniform(-2, 2), i)])
    assert not state.converged()


def test_cov_matches_reference_stdev():
    """Incremental CoV must match a fresh statistics.stdev() on the same window."""
    state = CoefficientOfVariation("rt", threshold=0.0, window=5, min_runs=5).start()
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    for i, v in enumerate(values, 1):
        state.observe(i, [_mk(v, i)])
    window = values[-5:]
    ref = statistics.stdev(window) / statistics.mean(window)
    mean = state.sum / len(state.window)
    var = max((state.sumsq / len(state.window)) - mean * mean, 0.0) * len(state.window) / (len(state.window) - 1)
    inc = (var ** 0.5) / abs(mean)
    assert abs(inc - ref) < 1e-9


def test_cov_window_validates_min():
    with pytest.raises(ValueError):
        CoefficientOfVariation("rt", window=1).start()


def test_cov_ignores_unrelated_metrics():
    state = CoefficientOfVariation("rt", threshold=0.0, window=2, min_runs=2).start()
    for i in range(1, 4):
        state.observe(i, [_mk(10.0, i, metric="other")])
    assert not state.converged()


def test_cov_raises_on_multiple_matching_samples():
    state = CoefficientOfVariation("rt", window=2, min_runs=2).start()
    with pytest.raises(ValueError, match="at most one"):
        state.observe(1, [_mk(1.0, 1), _mk(2.0, 1)])


# ---------------------------------------------------------------------------
# Combinators
# ---------------------------------------------------------------------------


def test_and_requires_both():
    p = FixedRuns(3) & CoefficientOfVariation("rt", threshold=0.0, window=3, min_runs=3)
    state = p.start()
    for i in range(1, 4):
        state.observe(i, [_mk(10.0, i)])
    # CoV: 3 obs at value=10.0 → window full, mean=10, stdev=0, threshold=0 ✓.
    # Fixed(3): 3 obs ✓.
    assert state.converged()


def test_and_blocks_until_slowest():
    p = FixedRuns(5) & FixedRuns(3)
    state = p.start()
    for i in range(1, 4):
        state.observe(i, [_mk(1.0, i)])
    assert not state.converged()
    for i in range(4, 6):
        state.observe(i, [_mk(1.0, i)])
    assert state.converged()


def test_or_stops_at_first():
    p = FixedRuns(3) | FixedRuns(10)
    state = p.start()
    for i in range(1, 4):
        state.observe(i, [_mk(1.0, i)])
    assert state.converged()


def test_at_least_at_most_sugar():
    p = CoefficientOfVariation("rt", threshold=0.0, window=3, min_runs=3).at_least(5).at_most(7)
    state = p.start()
    # 7 stable values: at_most kicks in at run 7.
    for i in range(1, 8):
        state.observe(i, [_mk(10.0, i)])
    assert state.converged()


# ---------------------------------------------------------------------------
# Custom
# ---------------------------------------------------------------------------


class _SeenN(PolicyState):
    def __init__(self, n):
        self.target = n
        self.cur = 0

    def observe(self, run, samples):
        if any(s.value > 0 for s in samples):
            self.cur += 1

    def converged(self):
        return self.cur >= self.target


def test_custom_policy():
    p = Custom(state_factory=lambda: _SeenN(2))
    state = p.start()
    state.observe(1, [_mk(0.0, 1)])
    state.observe(2, [_mk(1.0, 2)])
    state.observe(3, [_mk(2.0, 3)])
    assert state.converged()


# ---------------------------------------------------------------------------
# Introspection: max_runs() and independent()
# ---------------------------------------------------------------------------


def test_fixed_runs_introspection():
    p = FixedRuns(10)
    assert p.max_runs() == 10
    assert p.independent() is True


def test_cov_introspection():
    p = CoefficientOfVariation("rt")
    assert p.max_runs() is None        # unbounded
    assert p.independent() is False    # observations are order-sensitive


def test_custom_introspection_defaults_conservative():
    p = Custom(state_factory=lambda: _SeenN(3))
    assert p.max_runs() is None
    assert p.independent() is False


def test_and_max_runs_is_later_of_two():
    assert (FixedRuns(3) & FixedRuns(7)).max_runs() == 7


def test_and_max_runs_propagates_none():
    # If either branch can run forever, the And as a whole can too.
    assert (CoefficientOfVariation("rt") & FixedRuns(5)).max_runs() is None
    assert (FixedRuns(5) & CoefficientOfVariation("rt")).max_runs() is None


def test_or_max_runs_is_earlier_of_two():
    assert (FixedRuns(3) | FixedRuns(7)).max_runs() == 3


def test_or_max_runs_ignores_unbounded_child():
    # `.at_most(20)` desugars to `self | FixedRuns(20)` — the cap bounds the Or.
    assert (CoefficientOfVariation("rt") | FixedRuns(20)).max_runs() == 20
    assert CoefficientOfVariation("rt").at_most(20).max_runs() == 20


def test_or_max_runs_both_unbounded_is_none():
    assert (CoefficientOfVariation("rt") | CoefficientOfVariation("rt")).max_runs() is None


def test_combinator_independent_requires_both():
    assert (FixedRuns(3) & FixedRuns(7)).independent() is True
    assert (FixedRuns(3) | FixedRuns(7)).independent() is True
    assert (FixedRuns(3) & CoefficientOfVariation("rt")).independent() is False
    assert (FixedRuns(3) | CoefficientOfVariation("rt")).independent() is False
