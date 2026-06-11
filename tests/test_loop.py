"""benchmarking_loop: the pure feedback core."""

import pytest

from benchr import CoefficientOfVariation, FixedRuns, Sample
from benchr.grammar.benchmark import UNSET
from benchr.core.loop import benchmarking_loop


def _pump(warmup, runs, fake_value=1.0):
    """Drive the loop to exhaustion, feeding back one Sample per slot.
    Returns the list of (run, in_warmup) slots seen."""
    out = []
    gen = benchmarking_loop(warmup, runs)
    try:
        slot = next(gen)
        while True:
            out.append(slot)
            s = Sample(metric="runtime", value=fake_value, unit="s",
                       lower_is_better=True)
            slot = gen.send([s])
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


def test_none_send_counts_as_empty_observation():
    gen = benchmarking_loop(FixedRuns(0), FixedRuns(2))
    assert next(gen) == (1, False)
    assert gen.send(None) == (2, False)
    with pytest.raises(StopIteration):
        gen.send(None)


def test_unset_policy_raises():
    with pytest.raises(RuntimeError, match="materialize"):
        next(benchmarking_loop(UNSET, FixedRuns(1)))
