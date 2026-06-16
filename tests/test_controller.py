"""Controller: per-benchmark feedback loop over a fake RunSource.

The Controller is tested in isolation: a fake ``RunSource`` (so no real
processes spawn) and a collecting ``Reporter``. ``make_source`` is
monkeypatched so the Controller pulls from the fake.
"""

from pathlib import Path

from benchr import FixedRuns, Sample, bench, plan, suite
from benchr.core.execution import Execution, ExecutionResult
from benchr.core.sample import Report, RunResult
from benchr.report.reporter import Reporter
from benchr.runner.controller import Controller
from benchr.runner.source import RunSource


class _Collect(Reporter):
    def __init__(self):
        self.records = []
        self.processes = []
        self.warmups = []

    def record(self, rec):
        self.records.append(rec)

    def process_done(self, sched, result):
        self.processes.append(result)

    def warmup(self, key, runs):
        self.warmups.append((key, runs))


class _FakeSource(RunSource):
    """Yields a fixed list of RunResults, then StopIteration.

    ``process_result`` and ``events`` model a harness's whole-process outcome
    so the Controller's ``_on_exhausted`` classification can be exercised.
    """

    def __init__(self, values, closed_flag, *, proc_result=None, events=None):
        self._it = iter(values)
        self._closed = closed_flag
        self._proc_result = proc_result
        self._events = list(events or [])

    def next(self):
        return next(self._it)

    def drain_process_events(self):
        ev, self._events = self._events, []
        return ev

    def process_result(self):
        return self._proc_result

    def close(self):
        self._closed.append(True)


def _planned(runs, *, warmup=0):
    return plan(
        [
            suite("S", bench("b").with_command(["true"]))
            .with_cwd(Path("/tmp"))
            .with_warmup(warmup)
            .with_runs(runs)
        ],
        None,
    )[0]


def _result(returncode=0, failure=None):
    return ExecutionResult(
        Execution(command=("true",), cwd=Path("/tmp")),
        returncode,
        failure=failure,
    )


def test_records_run_per_slot_and_always_closes(monkeypatch):
    closed = []
    vals = [RunResult(samples=[Sample("t", float(i))]) for i in range(1, 4)]
    monkeypatch.setattr(
        "benchr.runner.controller.make_source",
        lambda p, params: _FakeSource(vals, closed),
    )
    rep = _Collect()
    report = Report()
    Controller(rep).run_benchmark(_planned(FixedRuns(3)), None, report)

    assert [r.run for r in report.runs] == [1, 2, 3]
    assert [r.samples[0].value for r in report.runs] == [1.0, 2.0, 3.0]
    assert [r.run for r in rep.records] == [1, 2, 3]
    assert closed == [True]  # source always closed


def test_stops_when_policy_converges(monkeypatch):
    # 10 values available but FixedRuns(2) must stop pulling at 2.
    closed = []
    vals = [RunResult(samples=[Sample("t", 1.0)]) for _ in range(10)]
    monkeypatch.setattr(
        "benchr.runner.controller.make_source",
        lambda p, params: _FakeSource(vals, closed),
    )
    report = Report()
    Controller(_Collect()).run_benchmark(_planned(FixedRuns(2)), None, report)

    assert len(report.runs) == 2
    assert closed == [True]


def test_warmup_boundary_recorded_once(monkeypatch):
    # warmup=2, runs=3 -> continuous numbering 1..5; warmup noted once at 2.
    closed = []
    vals = [RunResult(samples=[Sample("t", float(i))]) for i in range(1, 6)]
    monkeypatch.setattr(
        "benchr.runner.controller.make_source",
        lambda p, params: _FakeSource(vals, closed),
    )
    rep = _Collect()
    report = Report()
    Controller(rep).run_benchmark(
        _planned(FixedRuns(3), warmup=2), None, report
    )

    assert [r.run for r in report.runs] == [1, 2, 3, 4, 5]
    assert report.warmups == {"S/b": 2}
    assert rep.warmups == [("S/b", 2)]


def test_metadata_recorded_when_source_has_some(monkeypatch):
    closed = []

    class _MdSource(_FakeSource):
        def metadata(self):
            return [Sample("rss", 42.0)]

    vals = [RunResult(samples=[Sample("t", 1.0)])]
    monkeypatch.setattr(
        "benchr.runner.controller.make_source",
        lambda p, params: _MdSource(vals, closed),
    )
    report = Report()
    Controller(_Collect()).run_benchmark(_planned(FixedRuns(1)), None, report)

    assert report.metadata == {"S/b": [Sample("rss", 42.0)]}


# ----- _on_exhausted classification (harness ends early) ---------------------


def test_exhausted_zero_delivery_failed_process_records_process_verdict(monkeypatch):
    # Source delivers nothing and the process failed -> one failure carrying
    # the process verdict (e.g. "exit code 3").
    closed = []
    sched = _planned(FixedRuns(5)).benchmark.schedule(None, suite="S", run=1)
    proc = _result(returncode=3, failure="exit code 3")
    monkeypatch.setattr(
        "benchr.runner.controller.make_source",
        lambda p, params: _FakeSource(
            [], closed, proc_result=proc, events=[(sched, proc)]
        ),
    )
    rep = _Collect()
    report = Report()
    Controller(rep).run_benchmark(_planned(FixedRuns(5)), None, report)

    assert len(report.runs) == 1
    assert report.runs[0].run == 1
    assert report.runs[0].failure == "exit code 3"
    assert report.runs[0].returncode == 3
    # The drained process event still reached the reporter.
    assert rep.processes == [proc]


def test_exhausted_zero_delivery_clean_process_is_loud_no_iterations(monkeypatch):
    # Source delivers nothing but the process exited cleanly -> the loud
    # "no iterations parsed" failure (a harness that produced no parsable lines).
    closed = []
    proc = _result(returncode=0)
    monkeypatch.setattr(
        "benchr.runner.controller.make_source",
        lambda p, params: _FakeSource([], closed, proc_result=proc),
    )
    report = Report()
    Controller(_Collect()).run_benchmark(_planned(FixedRuns(3)), None, report)

    assert len(report.runs) == 1
    assert "no iterations parsed" in (report.runs[0].failure or "")


def test_exhausted_short_delivery_records_trailing_failure(monkeypatch):
    # warmup=0, runs=3, but the source delivers only 2 -> a trailing failure
    # at run 3 saying "produced 2 iterations, expected 3".
    closed = []
    vals = [RunResult(samples=[Sample("t", float(i))]) for i in (1, 2)]
    monkeypatch.setattr(
        "benchr.runner.controller.make_source",
        lambda p, params: _FakeSource(vals, closed, proc_result=_result()),
    )
    report = Report()
    Controller(_Collect()).run_benchmark(_planned(FixedRuns(3)), None, report)

    assert [r.run for r in report.runs] == [1, 2, 3]
    assert report.runs[2].failure == "harness produced 2 iterations, expected 3"
    assert len(report.failures) == 1


def test_exhausted_short_delivery_warmup_counted_in_target(monkeypatch):
    # warmup=2, runs=3 -> target 5; deliver 3 (1 measured) -> trailing failure
    # "produced 3 iterations, expected 5"; warmup boundary still noted at 2.
    closed = []
    vals = [RunResult(samples=[Sample("t", float(i))]) for i in (1, 2, 3)]
    monkeypatch.setattr(
        "benchr.runner.controller.make_source",
        lambda p, params: _FakeSource(vals, closed, proc_result=_result()),
    )
    rep = _Collect()
    report = Report()
    Controller(rep).run_benchmark(
        _planned(FixedRuns(3), warmup=2), None, report
    )

    assert [r.run for r in report.runs] == [1, 2, 3, 4]
    assert report.runs[3].failure == "harness produced 3 iterations, expected 5"
    assert report.warmups == {"S/b": 2}
