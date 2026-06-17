"""Sample, RunRecord, Report, and JSON round-trip."""

import json
from pathlib import Path
from typing import Any

from benchr import Report, RunRecord, Sample, report_from_json, report_to_json
from benchr.core.execution import Execution, ScheduledExecution
from benchr.core.sample import RunResult


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


def _template():
    return ScheduledExecution(execution=Execution(command=("x",), cwd=Path("/")),
                              suite="S", benchmark="b", variant=(), variant_label="", run=1)


def test_runrecord_from_run_result_stamps_identity_and_run():
    rr = RunResult(samples=[Sample("t", 1.0)], returncode=0, runtime=2.0)
    rec = RunRecord.from_run_result(_template(), 7, rr)
    assert rec.suite == "S" and rec.benchmark == "b" and rec.run == 7
    assert rec.runtime == 2.0 and rec.failure is None
    assert [s.value for s in rec.samples] == [1.0]


def test_runrecord_from_run_result_failure_carries_message():
    rr = RunResult(samples=[], returncode=3, failure="exit code 3", message="boom")
    rec = RunRecord.from_run_result(_template(), 1, rr)
    assert rec.is_failure() and rec.message == "boom" and rec.returncode == 3


def test_report_metadata_roundtrips_json():
    r = Report()
    r.metadata["S/b"] = [Sample("max_rss", 1024.0, unit="kB")]
    back = report_from_json(report_to_json(r))
    assert back.metadata["S/b"][0].metric == "max_rss"


def test_old_json_without_metadata_structures():
    r = report_from_json('{"runs": [], "warmups": {}}')
    assert r.metadata == {}


def test_pre_v4_json_drops_warmup_runs():
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
