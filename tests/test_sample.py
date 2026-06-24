"""Sample, Observation, Run, Report, and JSON round-trip."""

from typing import Any

from bench import Observation, Report, Run, Sample, report_from_json, report_to_json


def _smp(metric="runtime", value=1.5, unit="s", lower_is_better=True) -> Sample:
    return Sample(
        metric=metric, value=value, unit=unit, lower_is_better=lower_is_better
    )


def _obs(*samples: Sample, failure: str | None = None) -> Observation:
    return Observation(samples=list(samples), failure=failure)


def _run(variant=(), observations=None, **kw) -> Run:
    base: dict[str, Any] = dict(
        suite="S",
        benchmark="B",
        variant=variant,
        run=1,
        command=("./bench",),
        returncode=0,
        runtime=0.1,
        observations=observations if observations is not None else [_obs(_smp())],
    )
    base.update(kw)
    return Run(**base)


def test_variant_keys_orders_first_seen():
    r = Report(
        runs=[
            _run(variant=(("a", "1"),)),
            _run(variant=(("b", "2"),)),
            _run(variant=(("a", "3"),)),
        ]
    )
    assert r.variant_keys() == ["a", "b"]


def test_metrics_distinct():
    r = Report(
        runs=[
            _run(observations=[_obs(_smp(metric="x"), _smp(metric="y"))]),
            _run(observations=[_obs(_smp(metric="x"))]),
        ]
    )
    assert r.metrics() == ["x", "y"]


def test_observation_can_fail():
    o = Observation(failure="bad extraction")
    assert o.is_failure() and o.samples == []


def test_json_round_trip():
    r = Report(
        runs=[
            _run(
                observations=[
                    _obs(_smp(), _smp(metric="max_rss", value=2048, unit="kB"))
                ]
            ),
            Run(
                suite="S",
                benchmark="B",
                variant=(("opt", "O2"),),
                run=3,
                command=("./bench", "--opt"),
                returncode=7,
                failure="exit 7",
                message="boom",
                observations=[_obs(failure="exit 7")],
            ),
        ],
        warmups={"S/B": 2},
    )
    text = report_to_json(r)
    r2 = report_from_json(text)
    assert r2.runs == r.runs
    assert r2.failures == r.failures
    assert r2.warmups == {"S/B": 2}


def test_json_excludes_output_by_default():
    r = Report(runs=[_run(stdout="big-out", stderr="big-err", env={"X": "1"})])
    text = report_to_json(r)
    assert "big-out" not in text and "big-err" not in text
    back = report_from_json(text)
    assert (
        back.runs[0].stdout == ""
        and back.runs[0].stderr == ""
        and back.runs[0].env == {}
    )


def test_json_includes_output_when_requested():
    r = Report(runs=[_run(stdout="big-out", stderr="big-err")])
    text = report_to_json(r, include_output=True)
    assert "big-out" in text and "big-err" in text
    assert report_from_json(text).runs[0].stdout == "big-out"


def test_failures_are_failed_runs():
    r = Report(
        runs=[
            _run(),
            _run(returncode=1, failure="exit 1", observations=[_obs(failure="exit 1")]),
        ]
    )
    assert len(r.failures) == 1 and r.failures[0].returncode == 1
