"""Controller: per-benchmark feedback loop over a fake ExecutionSource.

The Controller is tested in isolation: a fake ``ExecutionSource`` (so no real
processes spawn) and a collecting ``Reporter``. ``make_source`` is monkeypatched
so the Controller pulls from the fake.
"""

from pathlib import Path

from bench import FixedRuns, Iteration, NoDetection, Execution, Sample, bench, suite
from bench.runner.base import plan
from bench.core.results import Report
from bench.report.reporter import Reporter
from bench.runner.controller import Controller
from bench.runner.source import ExecutionSource


class _Collect(Reporter):
    def __init__(self):
        self.iterations = []
        self.labels = []
        self.runs = []

    def iteration(self, it, label):
        self.iterations.append(it)
        self.labels.append(label)

    def execution_done(self, execution):
        self.runs.append(execution)


class _FakeSource(ExecutionSource):
    """Command-like fake: yields the given Iterations. close() returns one Execution
    per taken iteration."""

    def __init__(self, iterations, closed):
        self._it = iter(iterations)
        self._closed = closed
        self._taken: list[Iteration] = []

    def next(self) -> tuple[Iteration, str]:
        it = next(self._it)
        self._taken.append(it)
        return it, "S/b"

    def close(self) -> list[Execution]:
        self._closed.append(True)
        return [
            Execution(
                suite="S",
                benchmark="b",
                variant=(),
                run=i + 1,
                command=("true",),
                iterations=[it],
            )
            for i, it in enumerate(self._taken)
        ]


def _obs(value: float) -> Iteration:
    return Iteration(samples=[Sample("t", float(value))])


def _planned(runs, *, warmup=0, outlier_detection=None):
    s = (
        suite("S", bench("b").with_command(["true"]))
        .with_cwd(Path("/tmp"))
        .with_warmup(warmup)
        .with_runs(runs)
    )
    if outlier_detection is not None:
        s = s.with_outlier_detection(outlier_detection)
    return plan([s], None)[0]


def _patch(monkeypatch, obs, closed):
    monkeypatch.setattr(
        "bench.runner.controller.make_source",
        lambda b, verbose=False: _FakeSource(obs, closed),
    )


def test_records_run_per_slot_and_always_closes(monkeypatch):
    closed = []
    _patch(monkeypatch, [_obs(i) for i in range(1, 4)], closed)
    rep = _Collect()
    report = Report()
    Controller(rep).run_benchmark(_planned(FixedRuns(3)), report)

    assert [r.run for r in report.executions] == [1, 2, 3]
    assert [r.iterations[0].samples[0].value for r in report.executions] == [
        1.0,
        2.0,
        3.0,
    ]
    assert len(rep.runs) == 3
    assert len(rep.iterations) == 3
    assert rep.labels[0] == "S/b"
    assert closed == [True]


def test_stops_when_policy_converges(monkeypatch):
    # 10 observations available but FixedRuns(2) must stop pulling at 2.
    closed = []
    _patch(monkeypatch, [_obs(1) for _ in range(10)], closed)
    report = Report()
    Controller(_Collect()).run_benchmark(_planned(FixedRuns(2)), report)

    assert len(report.executions) == 2
    assert closed == [True]


def test_outliers_marked_across_runs(monkeypatch):
    # Spread cluster (MAD > 0) plus a lone 100: detection (on by default) pools
    # the values across all runs and flags only the 100.
    values = [10.0, 11.0, 12.0, 10.0, 11.0, 12.0, 10.0, 100.0]
    _patch(monkeypatch, [_obs(v) for v in values], [])
    report = Report()
    Controller(_Collect()).run_benchmark(_planned(FixedRuns(8)), report)

    flags = [r.iterations[0].samples[0].extra.get("outlier", False) for r in report.executions]
    assert flags == [False] * 7 + [True]


def test_no_detection_leaves_samples_unmarked(monkeypatch):
    values = [1.0] * 7 + [100.0]
    _patch(monkeypatch, [_obs(v) for v in values], [])
    report = Report()
    Controller(_Collect()).run_benchmark(
        _planned(FixedRuns(8), outlier_detection=NoDetection()), report
    )

    assert all(
        not r.iterations[0].samples[0].extra.get("outlier", False) for r in report.executions
    )


def test_warmup_iterations_excluded_from_detection(monkeypatch):
    # The big value is in warmup. The measured tail is flat, so nothing is an
    # outlier and the warmup sample itself is never flagged.
    values = [100.0] + [1.0] * 7
    _patch(monkeypatch, [_obs(v) for v in values], [])
    report = Report()
    Controller(_Collect()).run_benchmark(_planned(FixedRuns(7), warmup=1), report)

    assert all(
        not r.iterations[0].samples[0].extra.get("outlier", False) for r in report.executions
    )


def test_warmup_boundary_marked_on_iterations(monkeypatch):
    # warmup=2, runs=3 -> 5 iterations. The first 2 are flagged warmup.
    closed = []
    _patch(monkeypatch, [_obs(i) for i in range(1, 6)], closed)
    rep = _Collect()
    report = Report()
    Controller(rep).run_benchmark(_planned(FixedRuns(3), warmup=2), report)

    assert len(report.executions) == 5
    assert [r.iterations[0].warmup for r in report.executions] == [
        True,
        True,
        False,
        False,
        False,
    ]
