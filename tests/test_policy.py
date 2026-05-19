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


def test_fixed_runs_counts_observations_not_indices():
    state = FixedRuns(3).start()
    assert not state.converged()
    # Two empty observations: shouldn't count.
    state.observe(1, [])
    state.observe(2, [])
    assert not state.converged()
    # Then three with samples → converges.
    for i in range(3, 6):
        state.observe(i, [_mk(1.0, i)])
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
