"""benchmarking_loop: the pure feedback core."""

import pytest

from bench import CoefficientOfVariation, FixedRuns, Observation, Sample
from bench.grammar.benchmark import UNSET
from bench.runner.controller import benchmarking_loop


def _pump(warmup, runs, fake_value=1.0):
    """Drive the loop to exhaustion, feeding one Observation per slot.
    Returns the list of (run, in_warmup) slots seen. The caller (this pump)
    owns run numbering. The loop only reports the phase."""
    out = []
    gen = benchmarking_loop(warmup, runs)
    run = 0
    try:
        in_warmup = next(gen)
        while True:
            run += 1
            out.append((run, in_warmup))
            s = Sample(metric="runtime", value=fake_value, unit="s",
                       lower_is_better=True)
            in_warmup = gen.send(Observation(samples=[s]))
    except StopIteration:
        pass
    return out


def test_three_measured_runs():
    assert _pump(FixedRuns(0), FixedRuns(3)) == [
        (1, False), (2, False), (3, False)]


def test_warmup_then_measure_continuous_numbering():
    assert _pump(FixedRuns(2), FixedRuns(3)) == [
        (1, True), (2, True), (3, False), (4, False), (5, False)]


def test_zero_runs_yields_nothing():
    assert _pump(FixedRuns(0), FixedRuns(0)) == []


def test_cov_warmup_then_fixed_measure():
    cov = CoefficientOfVariation("runtime", threshold=0.0, window=3, min_runs=3)
    # 3 warmup runs at value=1.0 saturate CoV (stdev=0), then 2 measured runs.
    assert _pump(cov, FixedRuns(2)) == [
        (1, True), (2, True), (3, True), (4, False), (5, False)]


def test_empty_observation_counts():
    gen = benchmarking_loop(FixedRuns(0), FixedRuns(2))
    empty = Observation(samples=[])
    assert next(gen) is False        # warmup skipped -> first measured run
    assert gen.send(empty) is False  # second measured run
    with pytest.raises(StopIteration):
        gen.send(empty)


def test_unset_policy_raises():
    with pytest.raises(RuntimeError, match="unset"):
        next(benchmarking_loop(UNSET, FixedRuns(1)))
