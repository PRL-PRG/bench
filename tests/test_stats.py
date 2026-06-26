"""Stats: grouping, warmup exclusion, ratios, geomean."""

from pathlib import Path

from bench import Iteration, Report, Run, Sample, report_to_json
from bench.report.stats import (
    build_summary,
    geomean_with_sigma,
    group,
    metric_ratio,
    metric_stats,
    scale_unit,
)


def _smp(
    metric: str, value: float, *, unit: str = "s", lower_is_better: bool | None = True
) -> Sample:
    return Sample(
        metric=metric, value=value, unit=unit, lower_is_better=lower_is_better
    )


def _run(
    run: int = 1,
    *,
    returncode: int = 0,
    failure: str | None = None,
    bench: str = "b",
    suite: str = "S",
    variant: tuple[tuple[str, str], ...] = (),
    samples: list[Sample] | None = None,
    warmup: bool = False,
    process_samples: list[Sample] | None = None,
) -> Run:
    it = Iteration(samples=list(samples) if samples else [], warmup=warmup)
    return Run(
        suite=suite,
        benchmark=bench,
        variant=variant,
        run=run,
        command=("x",),
        returncode=returncode,
        failure=failure,
        message="boom" if failure else "",
        iterations=[it],
        process_samples=list(process_samples) if process_samples else [],
    )


def _fail(run: int, *, bench: str = "b", suite: str = "S", warmup: bool = False) -> Run:
    return Run(
        suite=suite,
        benchmark=bench,
        variant=(),
        run=run,
        command=("x",),
        returncode=7,
        failure="boom",
        message="boom",
        iterations=[Iteration(failure="boom", warmup=warmup)],
    )


def test_group_excludes_warmup_by_default():
    r = Report(
        runs=[
            _run(1, samples=[_smp("runtime", 1.0)], warmup=True),
            _run(2, samples=[_smp("runtime", 0.5)]),
        ],
    )
    g = group(r)
    assert len(g.groups) == 1
    assert g.groups[0].metrics[("runtime", "s")] == [0.5]


def test_group_with_warmup_when_opted_in():
    r = Report(
        runs=[
            _run(1, samples=[_smp("runtime", 1.0)], warmup=True),
            _run(2, samples=[_smp("runtime", 0.5)]),
        ],
    )
    g = group(r, include_warmup=True)
    assert sorted(g.groups[0].metrics[("runtime", "s")]) == [0.5, 1.0]


def test_process_samples_collected_but_not_counted():
    # A harness-style run: 2 measured iterations + one whole-process sample.
    r = Report(
        runs=[
            Run(
                suite="S",
                benchmark="b",
                variant=(),
                run=1,
                command=("x",),
                iterations=[
                    Iteration(samples=[_smp("runtime", 1.0)]),
                    Iteration(samples=[_smp("runtime", 2.0)]),
                ],
                process_samples=[_smp("max_rss", 2048.0, unit="kB")],
            )
        ]
    )
    g = group(r).groups[0]
    assert g.run_counts.successes == 2  # process sample is not an extra run
    assert g.metrics[("runtime", "s")] == [1.0, 2.0]
    assert g.metrics[("max_rss", "kB")] == [2048.0]


def test_process_only_run_counts_once():
    # No per-iteration data, just a whole-process metric: the session ran once.
    r = Report(
        runs=[
            Run(
                suite="S",
                benchmark="b",
                variant=(),
                run=1,
                command=("x",),
                iterations=[],
                process_samples=[_smp("max_rss", 1024.0, unit="kB")],
            )
        ]
    )
    g = group(r).groups[0]
    assert g.run_counts.successes == 1
    assert g.metrics[("max_rss", "kB")] == [1024.0]


def test_failures_count_into_run_counts():
    r = Report(
        runs=[
            _run(2, samples=[_smp("runtime", 1.0)]),
            _fail(1),
        ]
    )
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
    r = Report(runs=[_fail(1, warmup=True)])
    g = group(r)
    assert g.groups == []


def test_outliers_stay_in_stats_but_are_counted():
    # An outlier-flagged sample is still part of the values (mean/σ unchanged),
    # and the count surfaces on MetricStats.
    r = Report(
        runs=[
            _run(1, samples=[_smp("runtime", 1.0)]),
            _run(2, samples=[_smp("runtime", 1.0)]),
            _run(
                3,
                samples=[
                    Sample(
                        "runtime", 100.0, unit="s", lower_is_better=True, outlier=True
                    )
                ],
            ),
        ]
    )
    g = group(r).groups[0]
    assert sorted(g.metrics[("runtime", "s")]) == [1.0, 1.0, 100.0]
    assert g.outliers[("runtime", "s")] == 1

    gs = build_summary(r, []).groups[0]
    assert gs.metrics[("runtime", "s")].n_outliers == 1


def test_metric_stats_default_zero_outliers():
    ms = metric_stats([1.0, 2.0, 3.0], "runtime", "s", True)
    assert ms.n_outliers == 0


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
    assert abs(r.display_ratio - 2.0) < 1e-9  # 2x faster
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
    assert abs(geo - 1.0) < 1e-9


def test_build_summary_with_no_baseline():
    r = Report(runs=[_run(i, samples=[_smp("runtime", 0.5)]) for i in range(1, 4)])
    data = build_summary(r, [])
    assert len(data.groups) == 1
    assert data.baseline is None
    assert data.ratios == {} and data.geomeans == {}


def test_build_summary_with_baseline(tmp_path: Path):
    baseline = Report(
        runs=[_run(i, samples=[_smp("runtime", 1.0)]) for i in range(1, 4)]
    )
    bpath = tmp_path / "b.json"
    bpath.write_text(report_to_json(baseline))

    current = Report(
        runs=[_run(i, samples=[_smp("runtime", 0.5)]) for i in range(1, 4)]
    )
    data = build_summary(current, [bpath])
    assert data.baseline is not None
    assert "current" in data.ratios
    ratios = data.ratios["current"]
    only_id = next(iter(ratios))
    r = ratios[only_id][("runtime", "s")]
    assert abs(r.display_ratio - 2.0) < 1e-9


def test_build_summary_compares_single_variant_across_files(tmp_path: Path):
    # `bench compare clox.json krikafil.json`: same (suite, benchmark) but a
    # different command in the variant. With one variant per side they must
    # still align and yield a ratio — not silently drop to nothing.
    baseline = Report(
        runs=[
            _run(
                i,
                suite="run",
                bench="run",
                variant=(("command", "clox"),),
                samples=[_smp("runtime", 2.0)],
            )
            for i in range(1, 4)
        ]
    )
    bpath = tmp_path / "base.json"
    bpath.write_text(report_to_json(baseline))

    mine = Report(
        runs=[
            _run(
                i,
                suite="run",
                bench="run",
                variant=(("command", "krikafil"),),
                samples=[_smp("runtime", 1.0)],
            )
            for i in range(1, 4)
        ]
    )
    mpath = tmp_path / "mine.json"
    mpath.write_text(report_to_json(mine))

    data = build_summary(None, [bpath, mpath])
    assert "mine" in data.ratios
    bench_ratios = data.ratios["mine"]
    assert bench_ratios, "the single benchmark must align across files"
    only_id = next(iter(bench_ratios))
    r = bench_ratios[only_id][("runtime", "s")]
    assert abs(r.display_ratio - 2.0) < 1e-9  # 2.0s baseline / 1.0s = 2x faster
    gmr = data.geomeans["mine"]["run"][("runtime", "s")]
    assert gmr.runs_per_benchmark == 3  # the comparee's run count, not 0
