"""Sample, Observation, Execution, Report, and JSON round-trip."""

from typing import Any

from bench import Iteration, Report, Execution, Sample, report_from_json, report_to_json


def _smp(metric="runtime", value=1.5, unit="s", lower_is_better=True) -> Sample:
    return Sample(
        metric=metric, value=value, unit=unit, lower_is_better=lower_is_better
    )


def _it(
    *samples: Sample, failure: str | None = None, warmup: bool = False
) -> Iteration:
    return Iteration(samples=list(samples), failure=failure, warmup=warmup)


def _run(variant=(), iterations=None, **kw) -> Execution:
    base: dict[str, Any] = dict(
        suite="S",
        benchmark="B",
        variant=variant,
        run=1,
        command=("./bench",),
        returncode=0,
        runtime=0.1,
        iterations=iterations if iterations is not None else [_it(_smp())],
    )
    base.update(kw)
    return Execution(**base)


def test_variant_keys_orders_first_seen():
    r = Report(
        executions=[
            _run(variant=(("a", "1"),)),
            _run(variant=(("b", "2"),)),
            _run(variant=(("a", "3"),)),
        ]
    )
    assert r.variant_keys() == ["a", "b"]


def test_metrics_distinct():
    # Distinct names span both iteration samples and whole-process samples.
    r = Report(
        executions=[
            _run(iterations=[_it(_smp(metric="x"), _smp(metric="y"))]),
            _run(
                iterations=[_it(_smp(metric="x"))], process_samples=[_smp(metric="z")]
            ),
        ]
    )
    assert r.metrics() == ["x", "y", "z"]


def test_json_round_trip():
    r = Report(
        executions=[
            _run(
                iterations=[_it(_smp(), warmup=True), _it(_smp())],
                process_samples=[_smp(metric="max_rss", value=2048, unit="kB")],
            ),
            Execution(
                suite="S",
                benchmark="B",
                variant=(("opt", "O2"),),
                run=3,
                command=("./bench", "--opt"),
                returncode=7,
                failure="exit 7",
                message="boom",
                iterations=[_it(failure="exit 7")],
            ),
        ],
    )
    text = report_to_json(r)
    r2 = report_from_json(text)
    assert r2.executions == r.executions  # warmup flag + process_samples survive
    assert r2.failures == r.failures


def test_json_excludes_output_by_default():
    r = Report(executions=[_run(stdout="big-out", stderr="big-err", env={"X": "1"})])
    text = report_to_json(r)
    assert "big-out" not in text and "big-err" not in text
    back = report_from_json(text)
    assert (
        back.executions[0].stdout == ""
        and back.executions[0].stderr == ""
        and back.executions[0].env == {}
    )


def test_json_includes_output_when_requested():
    r = Report(executions=[_run(stdout="big-out", stderr="big-err")])
    text = report_to_json(r, include_output=True)
    assert "big-out" in text and "big-err" in text
    assert report_from_json(text).executions[0].stdout == "big-out"


def test_failures_are_failed_runs():
    r = Report(
        executions=[
            _run(),
            _run(returncode=1, failure="exit 1", iterations=[_it(failure="exit 1")]),
        ]
    )
    assert len(r.failures) == 1 and r.failures[0].returncode == 1
