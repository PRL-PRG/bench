"""Sample, RunRecord, Report, and JSON round-trip."""

from typing import Any

from benchr import Report, RunRecord, Sample, report_from_json, report_to_json


def _run(variant=(), **kw) -> RunRecord:
    base: dict[str, Any] = dict(
        suite="S", benchmark="B", variant=variant, run=1,
        command=("./bench",), returncode=0, runtime=0.1,
    )
    base.update(kw)
    return RunRecord(**base)


def _smp(metric="runtime", value=1.5, unit="s", lower_is_better=True) -> Sample:
    return Sample(metric=metric, value=value, unit=unit, lower_is_better=lower_is_better)


def test_variant_keys_orders_first_seen():
    r = Report(runs=[
        _run(variant=(("a", "1"),)),
        _run(variant=(("b", "2"),)),
        _run(variant=(("a", "3"),)),
    ])
    assert r.variant_keys() == ["a", "b"]


def test_metrics_distinct():
    r = Report(runs=[
        _run(samples=[_smp(metric="x"), _smp(metric="y")]),
        _run(samples=[_smp(metric="x")]),
    ])
    assert r.metrics() == ["x", "y"]


def test_json_round_trip():
    r = Report(runs=[
        _run(samples=[_smp(), _smp(metric="max_rss", value=2048, unit="kB")]),
        RunRecord(
            suite="S", benchmark="B", variant=(("opt", "O2"),), run=3,
            command=("./bench", "--opt"), returncode=7, failure="exit 7", message="boom",
        ),
    ], warmups={"S/B": 2})
    text = report_to_json(r)
    r2 = report_from_json(text)
    assert r2.runs == r.runs
    assert r2.failures == r.failures
    assert r2.warmups == {"S/B": 2}


def test_pre_v4_json_drops_warmup_runs():
    import json

    old = json.dumps({"runs": [
        {"suite": "S", "benchmark": "B", "variant": [], "run": 1,
         "phase": "warmup", "command": ["x"], "returncode": 0, "runtime": 0.1,
         "failure": None, "message": "", "variant_label": "", "samples": []},
        {"suite": "S", "benchmark": "B", "variant": [], "run": 1,
         "phase": "runs", "command": ["x"], "returncode": 0, "runtime": 0.2,
         "failure": None, "message": "", "variant_label": "", "samples": []},
    ]})
    r = report_from_json(old)
    assert [run.runtime for run in r.runs] == [0.2]
    assert r.warmups == {}
