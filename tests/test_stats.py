"""Stats: grouping, warmup exclusion, ratios, geomean."""

from pathlib import Path

from benchr import Report, RunRecord, Sample
from benchr.report.stats import (
    build_summary, geomean_with_sigma, group,
    metric_ratio, metric_stats, scale_unit,
)


def _smp(metric: str, value: float, *, unit: str = "s",
         lower_is_better: bool | None = True) -> Sample:
    return Sample(metric=metric, value=value, unit=unit,
                  lower_is_better=lower_is_better)


def _run(run: int = 1, *, returncode: int = 0, failure: str | None = None,
         bench: str = "b", suite: str = "S",
         samples: list[Sample] | None = None) -> RunRecord:
    return RunRecord(
        suite=suite, benchmark=bench, variant=(), run=run,
        command=("x",), returncode=returncode, failure=failure,
        message="boom" if failure else "",
        samples=list(samples) if samples else [],
    )


def _fail(run: int, **kw) -> RunRecord:
    return _run(run, returncode=7, failure="boom", **kw)


def test_group_excludes_warmup_by_default():
    r = Report(runs=[
        _run(1, samples=[_smp("runtime", 1.0)]),
        _run(2, samples=[_smp("runtime", 0.5)]),
    ], warmups={"S/b": 1})
    g = group(r)
    assert len(g.groups) == 1
    assert g.groups[0].metrics[("runtime", "s")] == [0.5]


def test_group_with_warmup_when_opted_in():
    r = Report(runs=[
        _run(1, samples=[_smp("runtime", 1.0)]),
        _run(2, samples=[_smp("runtime", 0.5)]),
    ], warmups={"S/b": 1})
    g = group(r, include_warmup=True)
    assert sorted(g.groups[0].metrics[("runtime", "s")]) == [0.5, 1.0]


def test_failures_count_into_run_counts():
    r = Report(runs=[
        _run(2, samples=[_smp("runtime", 1.0)]),
        _fail(1),
    ])
    g = group(r)
    assert g.groups[0].run_counts.failures == 1
    assert g.groups[0].run_counts.successes == 1


def test_all_failed_benchmark_still_appears():
    r = Report(runs=[_fail(1), _fail(2)])
    g = group(r)
    assert len(g.groups) == 1
    assert g.groups[0].run_counts.failures == 2
    assert g.groups[0].run_counts.successes == 0
    assert g.groups[0].metrics == {}


def test_group_excludes_failures_in_warmup():
    r = Report(runs=[
        _run(1, returncode=1, failure="x"),
    ], warmups={"S/b": 1})
    g = group(r)
    assert g.groups == []


def test_metric_stats_basic():
    ms = metric_stats([1.0, 2.0, 3.0], "runtime", "s", True)
    assert ms.n == 3
    assert ms.mean == 2.0
    assert ms.median == 2.0
    assert ms.min == 1.0 and ms.max == 3.0


def test_metric_stats_single_value_has_zero_stdev():
    ms = metric_stats([5.0], "x", "u", None)
    assert ms.stdev == 0.0


def test_scale_unit_seconds_to_ms():
    sc, unit = scale_unit(0.5, "s")
    assert sc == 1e3 and unit == "ms"


def test_scale_unit_kb_to_mb():
    sc, unit = scale_unit(2048.0, "kB")
    assert unit == "MB" and abs(sc - 1 / 1024) < 1e-12


def test_metric_ratio_lower_is_better_speedup():
    bl = metric_stats([1.0, 1.0, 1.0], "rt", "s", True)
    cur = metric_stats([0.5, 0.5, 0.5], "rt", "s", True)
    r = metric_ratio(bl, cur)
    assert r is not None
    assert abs(r.display_ratio - 2.0) < 1e-9  # 2× faster
    assert r.raw_ratio == 0.5


def test_metric_ratio_higher_is_better():
    bl = metric_stats([100.0], "tp", "iter/s", False)
    cur = metric_stats([200.0], "tp", "iter/s", False)
    r = metric_ratio(bl, cur)
    assert r is not None
    assert abs(r.display_ratio - 2.0) < 1e-9


def test_metric_ratio_zero_returns_none():
    bl = metric_stats([0.0], "rt", "s", True)
    cur = metric_stats([1.0], "rt", "s", True)
    assert metric_ratio(bl, cur) is None


def test_geomean_with_sigma():
    bl1 = metric_stats([1.0], "rt", "s", True)
    cur1 = metric_stats([0.5], "rt", "s", True)
    bl2 = metric_stats([1.0], "rt", "s", True)
    cur2 = metric_stats([2.0], "rt", "s", True)
    r1 = metric_ratio(bl1, cur1)
    r2 = metric_ratio(bl2, cur2)
    assert r1 is not None and r2 is not None
    geo, _ = geomean_with_sigma([r1, r2])
    # speedup 2× × 0.5× → geomean 1.0
    assert abs(geo - 1.0) < 1e-9


def test_build_summary_with_no_baseline():
    r = Report(runs=[
        _run(i, samples=[_smp("runtime", 0.5)]) for i in range(1, 4)
    ])
    data = build_summary(r, [])
    assert len(data.groups) == 1
    assert data.baseline is None
    assert data.ratios == {} and data.geomeans == {}


def test_build_summary_with_baseline(tmp_path: Path):
    from benchr import report_to_json
    baseline = Report(runs=[
        _run(i, samples=[_smp("runtime", 1.0)]) for i in range(1, 4)
    ])
    bpath = tmp_path / "b.json"
    bpath.write_text(report_to_json(baseline))

    current = Report(runs=[
        _run(i, samples=[_smp("runtime", 0.5)]) for i in range(1, 4)
    ])
    data = build_summary(current, [bpath])
    assert data.baseline is not None
    assert "current" in data.ratios
    ratios = data.ratios["current"]
    # one benchmark, one metric, ratio = 2.0 speedup
    only_id = next(iter(ratios))
    r = ratios[only_id][("runtime", "s")]
    assert abs(r.display_ratio - 2.0) < 1e-9
