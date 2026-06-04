"""Sample, Report, and JSON round-trip."""

from typing import Any

from benchr import RunRecord, Report, Sample, report_from_json, report_to_json


def _mk(**kw: Any) -> Sample:
    base: dict[str, Any] = dict(
        suite="S", benchmark="B", info=(), run=1, phase="measure",
        metric="runtime", value=1.5, unit="s", lower_is_better=True,
    )
    base.update(kw)
    return Sample(**base)


def test_info_keys_orders_first_seen():
    r = Report()
    r.extend([_mk(info=(("a", "1"),)), _mk(info=(("b", "2"),)), _mk(info=(("a", "3"),))])
    assert r.info_keys() == ["a", "b"]


def test_metrics_distinct():
    r = Report()
    r.extend([_mk(metric="x"), _mk(metric="y"), _mk(metric="x")])
    assert r.metrics() == ["x", "y"]


def test_json_round_trip():
    r = Report(metadata={"note": "hello"})
    r.extend([
        _mk(),
        _mk(metric="max_rss", value=2048, unit="kB"),
    ])
    r.add_run(RunRecord(
        suite="S", benchmark="B", info=(("opt", "O2"),), run=3, phase="measure",
        command=("./bench", "--opt"), returncode=7, failure="exit 7", message="boom",
    ))
    text = report_to_json(r)
    r2 = report_from_json(text)
    assert r2.samples == r.samples
    assert r2.metadata == r.metadata
    assert r2.runs == r.runs
    assert r2.failures == r.failures
