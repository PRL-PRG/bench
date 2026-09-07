"""Controller: per-benchmark feedback loop, tested in isolation.

The refactored Controller drives real subprocesses through
``execute_benchmark``. To test the loop logic (policy convergence, warmup
flagging, outlier marking) without spawning processes, we subclass Controller
and override ``execute_benchmark`` to return canned ``Execution`` objects — one
``Iteration`` per run, carrying a single ``Sample`` whose value we control.
"""

from pathlib import Path

from bench import Execution, FixedRuns, Iteration, NoDetection, Sample, bench, suite
from bench.builder.suite import plan
from bench.params import Params
from bench.report import Reporter
from bench.runner import Controller


class _Collect(Reporter):
    """`Reporter.iteration` is gone - the controller only reports whole
    Executions now, so that is all we collect."""

    def __init__(self):
        self.runs = []

    def execution_done(self, execution):
        self.runs.append(execution)


class _FakeController(Controller):
    """Controller whose ``execute_benchmark`` yields canned single-iteration
    Executions from a fixed list of sample values, one per run."""

    def __init__(self, values):
        self._values = [float(v) for v in values]
        self.calls = 0

    def execute_benchmark(self, b, run: int, verbose: bool) -> Execution:
        self.calls += 1
        value = self._values[run - 1]
        return Execution(
            suite=b.suite,
            benchmark=b.name,
            variant=b.variant,
            run=run,
            runtime=0.01,
            command=("true",),
            iterations=[Iteration(samples=[Sample("t", value, iteration=0)])],
        )


def _planned(runs, *, warmup=0, outlier_detection=None):
    s = (
        suite("S", bench("b").with_command(["true"]))
        .with_cwd(Path("/tmp"))
        .with_warmup(warmup)
        .with_runs(runs)
    )
    if outlier_detection is not None:
        s = s.with_outlier_detection(outlier_detection)
    return plan([s], Params())[0]


def test_records_run_per_slot(monkeypatch):
    rep = _Collect()
    ctrl = _FakeController([1, 2, 3])
    executions = ctrl.run_benchmark(_planned(FixedRuns(3)), rep)

    assert [r.run for r in executions] == [1, 2, 3]
    assert [r.iterations[0].samples[0].value for r in executions] == [
        1.0,
        2.0,
        3.0,
    ]
    assert len(rep.runs) == 3
    assert rep.runs[0].identifier() == "S/b #1"
    assert ctrl.calls == 3


def test_stops_when_policy_converges():
    # 10 values available but FixedRuns(2) must stop after 2 runs.
    ctrl = _FakeController([1.0] * 10)
    executions = ctrl.run_benchmark(_planned(FixedRuns(2)), _Collect())

    assert len(executions) == 2
    assert ctrl.calls == 2


def test_outliers_marked_across_runs():
    # Spread cluster (MAD > 0) plus a lone 100: detection (on by default) pools
    # the values across all runs and flags only the 100.
    values = [10.0, 11.0, 12.0, 10.0, 11.0, 12.0, 10.0, 100.0]
    executions = _FakeController(values).run_benchmark(
        _planned(FixedRuns(8)), _Collect()
    )

    flags = [r.iterations[0].samples[0].extra.get("outlier", False) for r in executions]
    assert flags == [False] * 7 + [True]


def test_no_detection_leaves_samples_unmarked():
    values = [1.0] * 7 + [100.0]
    executions = _FakeController(values).run_benchmark(
        _planned(FixedRuns(8), outlier_detection=NoDetection()), _Collect()
    )

    assert all(
        not r.iterations[0].samples[0].extra.get("outlier", False) for r in executions
    )


def test_warmup_iterations_excluded_from_detection():
    # The big value is in warmup. The measured tail is flat, so nothing is an
    # outlier and the warmup sample itself is never flagged.
    values = [100.0] + [1.0] * 7
    executions = _FakeController(values).run_benchmark(
        _planned(FixedRuns(7), warmup=1), _Collect()
    )

    assert all(
        not r.iterations[0].samples[0].extra.get("outlier", False) for r in executions
    )


def test_warmup_boundary_marked_on_iterations():
    # warmup=2, runs=3 -> 5 iterations. The first 2 are flagged warmup.
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    rep = _Collect()
    executions = _FakeController(values).run_benchmark(
        _planned(FixedRuns(3), warmup=2), rep
    )

    assert len(executions) == 5
    assert [r.iterations[0].warmup for r in executions] == [
        True,
        True,
        False,
        False,
        False,
    ]
