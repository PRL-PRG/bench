"""Controller: per-benchmark feedback loop over a fake RunSource.

The Controller is tested in isolation: a fake ``RunSource`` (so no real
processes spawn) and a collecting ``Reporter``. ``make_source`` is monkeypatched
so the Controller pulls from the fake.
"""

from pathlib import Path

from benchr import FixedRuns, Observation, Run, Sample, bench, plan, suite
from benchr.core.sample import Report
from benchr.report.reporter import Reporter
from benchr.runner.controller import Controller
from benchr.runner.source import RunSource


class _Collect(Reporter):
    def __init__(self):
        self.observations = []
        self.runs = []
        self.warmups = []

    def observation(self, obs):
        self.observations.append(obs)

    def run_done(self, run):
        self.runs.append(run)

    def warmup(self, key, observations):
        self.warmups.append((key, observations))


class _FakeSource(RunSource):
    """Command-like fake: yields the given Observations; close() returns one Run
    per taken observation."""

    def __init__(self, observations, closed):
        self._it = iter(observations)
        self._closed = closed
        self._taken: list[Observation] = []

    def next(self) -> Observation:
        obs = next(self._it)
        self._taken.append(obs)
        return obs

    def close(self) -> list[Run]:
        self._closed.append(True)
        return [Run(suite="S", benchmark="b", variant=(), run=i + 1,
                    command=("true",), observations=[obs])
                for i, obs in enumerate(self._taken)]


def _obs(value: float) -> Observation:
    return Observation(samples=[Sample("t", float(value))], label="S/b")


def _planned(runs, *, warmup=0):
    return plan([
        suite("S", bench("b").with_command(["true"]))
        .with_cwd(Path("/tmp")).with_warmup(warmup).with_runs(runs)
    ], None)[0]


def _patch(monkeypatch, obs, closed):
    monkeypatch.setattr("benchr.runner.controller.make_source",
                        lambda b, verbose=False: _FakeSource(obs, closed))


def test_records_run_per_slot_and_always_closes(monkeypatch):
    closed = []
    _patch(monkeypatch, [_obs(i) for i in range(1, 4)], closed)
    rep = _Collect()
    report = Report()
    Controller(rep).run_benchmark(_planned(FixedRuns(3)), report)

    assert [r.run for r in report.runs] == [1, 2, 3]
    assert [r.observations[0].samples[0].value for r in report.runs] == [1.0, 2.0, 3.0]
    assert len(rep.runs) == 3
    assert len(rep.observations) == 3
    assert rep.observations[0].label == "S/b"
    assert closed == [True]


def test_stops_when_policy_converges(monkeypatch):
    # 10 observations available but FixedRuns(2) must stop pulling at 2.
    closed = []
    _patch(monkeypatch, [_obs(1) for _ in range(10)], closed)
    report = Report()
    Controller(_Collect()).run_benchmark(_planned(FixedRuns(2)), report)

    assert len(report.runs) == 2
    assert closed == [True]


def test_warmup_boundary_recorded_once(monkeypatch):
    # warmup=2, runs=3 -> 5 observations; warmup noted once at 2.
    closed = []
    _patch(monkeypatch, [_obs(i) for i in range(1, 6)], closed)
    rep = _Collect()
    report = Report()
    Controller(rep).run_benchmark(_planned(FixedRuns(3), warmup=2), report)

    assert len(report.runs) == 5
    assert report.warmups == {"S/b": 2}
    assert rep.warmups == [("S/b", 2)]
