"""Formatter output snapshots."""

import re
from pathlib import Path

from benchr import (
    Compact, DefaultSummary, Report, RunRecord, Sample, report_to_json,
)


def _smp(metric: str = "runtime", value: float = 0.5, unit: str = "s",
         lower_is_better: bool | None = True) -> Sample:
    return Sample(metric=metric, value=value, unit=unit,
                  lower_is_better=lower_is_better)


def _ok(run: int = 1, *, bench: str = "b", suite: str = "S",
        variant=(), variant_label: str = "",
        samples: list[Sample] | None = None) -> RunRecord:
    return RunRecord(
        suite=suite, benchmark=bench, variant=variant, run=run, phase="measure",
        command=("x",), returncode=0,
        variant_label=variant_label,
        samples=list(samples) if samples else [],
    )


def test_default_summary_no_baseline():
    r = Report(runs=[_ok(i, samples=[_smp("runtime", 0.5)]) for i in range(1, 4)])
    out = DefaultSummary()(r)
    assert "S/b" in out
    assert "3" in out and "runs" in out
    assert "runtime" in out and "ms" in out  # 0.5s → scaled to 500ms


def test_compact_no_baseline_lists_benchmarks():
    runs = []
    for i in range(1, 4):
        runs.append(_ok(i, bench="a", samples=[_smp("runtime", 0.5)]))
        runs.append(_ok(i, bench="b", samples=[_smp("runtime", 1.0)]))
    r = Report(runs=runs)
    out = Compact("runtime")(r)
    assert "a:" in out and "b:" in out
    assert "geomean" in out


def test_compact_with_baseline_shows_speedup(tmp_path: Path):
    baseline = Report(runs=[
        _ok(i, bench="a", samples=[_smp("runtime", 1.0)]) for i in range(1, 4)
    ])
    bpath = tmp_path / "b.json"
    bpath.write_text(report_to_json(baseline))

    current = Report(runs=[
        _ok(i, bench="a", samples=[_smp("runtime", 0.5)]) for i in range(1, 4)
    ])
    out = Compact("runtime")(current, baseline=[bpath])
    assert "geometric mean speedup" in out
    assert "a:" in out
    assert "2.00" in out or "2.0" in out


def test_default_summary_with_baseline_includes_runs(tmp_path: Path):
    baseline = Report(runs=[
        _ok(i, samples=[_smp("runtime", 1.0)]) for i in range(1, 4)
    ])
    bpath = tmp_path / "b.json"
    bpath.write_text(report_to_json(baseline))

    current = Report(runs=[
        _ok(i, samples=[_smp("runtime", 0.5)]) for i in range(1, 4)
    ])
    out = DefaultSummary()(current, baseline=[bpath])
    assert "Summary (geometric mean of ratios)" in out
    assert "better" in out


def test_compact_filters_by_metric():
    r = Report(runs=[
        _ok(1, samples=[_smp("runtime", 1.0),
                        _smp("max_rss", 1024.0, unit="kB")]),
    ])
    out_rt = Compact("runtime")(r)
    out_rss = Compact("max_rss")(r)
    assert "1.00" in out_rt
    assert "1.00" in out_rss
    assert "max_rss" not in out_rt


# ---------------------------------------------------------------------------
# Hyperfine-style intra-run ranking in DefaultSummary
# ---------------------------------------------------------------------------


def _strip_markup(s: str) -> str:
    s = s.replace("\\[", "\x00")
    s = re.sub(r"\[/?[^]]*\]", "", s)
    return s.replace("\x00", "[")


def _vrun(value: float, *, run: int, label: str, variant_axis: str = "k",
          bench: str = "b", suite: str = "S", metric: str = "elapsed",
          unit: str = "s", lower_is_better: bool | None = True) -> RunRecord:
    return _ok(
        run, bench=bench, suite=suite,
        variant=((variant_axis, label),),
        variant_label=label,
        samples=[_smp(metric, value, unit=unit, lower_is_better=lower_is_better)],
    )


def test_default_summary_ranks_variants_within_benchmark():
    runs = []
    for i in range(1, 4):
        runs.append(_vrun(0.10, run=i, label="fast"))
        runs.append(_vrun(0.20, run=i, label="slow"))
    r = Report(runs=runs)
    text = _strip_markup(DefaultSummary()(r))
    assert "Summary" in text
    assert "'fast' [elapsed] was" in text
    assert "2.00" in text
    assert "lower than" in text
    assert "'slow'" in text


def test_default_summary_no_ranking_across_distinct_benchmarks():
    runs = []
    for i in range(1, 4):
        runs.append(_ok(i, bench="a", samples=[_smp("elapsed", 0.10)]))
        runs.append(_ok(i, bench="b", samples=[_smp("elapsed", 0.20)]))
    r = Report(runs=runs)
    text = _strip_markup(DefaultSummary()(r))
    assert "Summary" not in text


def test_default_summary_no_ranking_with_single_variant():
    r = Report(runs=[
        _ok(i, bench="a", samples=[_smp("elapsed", 0.10)]) for i in range(1, 4)
    ])
    text = _strip_markup(DefaultSummary()(r))
    assert "Summary" not in text


def test_default_summary_ranking_uses_higher_for_higher_is_better():
    runs = []
    for i in range(1, 4):
        runs.append(_vrun(200.0, run=i, label="fast", metric="throughput",
                          unit="iter/s", lower_is_better=False))
        runs.append(_vrun(100.0, run=i, label="slow", metric="throughput",
                          unit="iter/s", lower_is_better=False))
    r = Report(runs=runs)
    text = _strip_markup(DefaultSummary()(r))
    assert "'fast' [throughput] was" in text
    assert "2.00" in text
    assert "times higher than" in text
    assert "'slow'" in text


def test_default_summary_unit_label_survives_rich_markup():
    # The "[ms]" unit tag must be escaped, otherwise rich eats it as a
    # markup tag and the rendered summary loses the unit.
    from io import StringIO

    from rich.console import Console

    r = Report(runs=[_ok(i, samples=[_smp("runtime", 0.5)]) for i in range(1, 4)])
    out = DefaultSummary()(r)
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    console.print(out)
    rendered = buf.getvalue()
    assert "runtime [ms]" in rendered
