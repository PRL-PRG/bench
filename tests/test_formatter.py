"""Formatter output snapshots."""

import re
from pathlib import Path

from benchr import (
    Compact, DefaultSummary, Phase, Report, RunRecord, Sample, report_to_json,
)


def _mk(metric: str, value: float, *, run: int = 1, bench: str = "b",
        suite: str = "S", phase: Phase = "measure", unit: str = "s",
        lower_is_better: bool | None = True) -> Sample:
    return Sample(
        suite=suite, benchmark=bench, info=(), run=run, phase=phase,
        metric=metric, value=value, unit=unit, lower_is_better=lower_is_better,
    )


def _ok(run: int = 1, *, bench: str = "b", suite: str = "S") -> RunRecord:
    return RunRecord(
        suite=suite, benchmark=bench, info=(), run=run, phase="measure",
        command=("x",), returncode=0,
    )


def test_default_summary_no_baseline():
    r = Report()
    r.extend([_mk("runtime", 0.5, run=i) for i in range(1, 4)])
    for i in range(1, 4):
        r.add_run(_ok(i))
    out = DefaultSummary()(r)
    assert "S/b" in out
    # Rich markup may split the literal "0/3 runs" — just check the digits + word.
    assert "3" in out and "runs" in out
    assert "runtime" in out and "ms" in out  # 0.5s → scaled to 500ms


def test_compact_no_baseline_lists_benchmarks():
    r = Report()
    for i in range(1, 4):
        r.extend([_mk("runtime", 0.5, run=i, bench="a"),
                  _mk("runtime", 1.0, run=i, bench="b")])
    out = Compact("runtime")(r)
    assert "a:" in out and "b:" in out
    assert "geomean" in out


def test_compact_with_baseline_shows_speedup(tmp_path: Path):
    baseline = Report()
    for i in range(1, 4):
        baseline.extend([_mk("runtime", 1.0, run=i, bench="a")])
    bpath = tmp_path / "b.json"
    bpath.write_text(report_to_json(baseline))

    current = Report()
    for i in range(1, 4):
        current.extend([_mk("runtime", 0.5, run=i, bench="a")])
    out = Compact("runtime")(current, baseline=[bpath])
    assert "geometric mean speedup" in out
    assert "a:" in out
    # The ratio should be ~2 (50% speedup → 2× faster)
    assert "2.00" in out or "2.0" in out


def test_default_summary_with_baseline_includes_runs(tmp_path: Path):
    baseline = Report()
    baseline.extend([_mk("runtime", 1.0, run=i) for i in range(1, 4)])
    bpath = tmp_path / "b.json"
    bpath.write_text(report_to_json(baseline))

    current = Report()
    current.extend([_mk("runtime", 0.5, run=i) for i in range(1, 4)])
    out = DefaultSummary()(current, baseline=[bpath])
    assert "Summary (geometric mean of ratios)" in out
    assert "better" in out


def test_compact_filters_by_metric():
    r = Report()
    r.extend([_mk("runtime", 1.0), _mk("max_rss", 1024.0, unit="kB")])
    out_rt = Compact("runtime")(r)
    out_rss = Compact("max_rss")(r)
    assert "1.00" in out_rt
    assert "1.00" in out_rss
    # neither output should reference the *other* metric name as a sample
    assert "max_rss" not in out_rt


# ---------------------------------------------------------------------------
# Hyperfine-style intra-run ranking in DefaultSummary
# ---------------------------------------------------------------------------


def _strip_markup(s: str) -> str:
    """Strip rich [tag]...[/] markup so assertions see the visible text."""
    return re.sub(r"\[/?[^]]*\]", "", s)


def test_default_summary_ranks_two_benchmarks():
    r = Report()
    for i in range(1, 4):
        r.extend([_mk("elapsed", 0.10, run=i, bench="a")])
        r.extend([_mk("elapsed", 0.20, run=i, bench="b")])
    text = _strip_markup(DefaultSummary()(r))
    assert "Summary" in text
    assert "'a' ran" in text
    assert "2.00" in text
    assert "faster than" in text
    assert "'b'" in text


def test_default_summary_no_ranking_with_single_benchmark():
    r = Report()
    r.extend([_mk("elapsed", 0.10, run=i, bench="a") for i in range(1, 4)])
    text = _strip_markup(DefaultSummary()(r))
    # The new ranking title is on its own line; the existing
    # "Summary (geometric mean of ratios):" block belongs to comparison
    # mode only (which we're not in here).
    assert "Summary" not in text


def test_default_summary_ranking_uses_higher_for_higher_is_better():
    r = Report()
    for i in range(1, 4):
        r.extend([_mk("throughput", 200.0, run=i, bench="fast",
                      unit="iter/s", lower_is_better=False)])
        r.extend([_mk("throughput", 100.0, run=i, bench="slow",
                      unit="iter/s", lower_is_better=False)])
    text = _strip_markup(DefaultSummary()(r))
    assert "'fast' ran" in text
    assert "2.00" in text
    assert "times higher than" in text
    assert "'slow'" in text


def test_default_summary_ranking_drops_suite_when_uniform():
    r = Report()
    for i in range(1, 4):
        r.extend([_mk("elapsed", 0.1, run=i, bench="a", suite="bench")])
        r.extend([_mk("elapsed", 0.2, run=i, bench="b", suite="bench")])
    text = _strip_markup(DefaultSummary()(r))
    # Quotes around the bare benchmark name, not "bench/a".
    assert "'a' ran" in text
    assert "'bench/a'" not in text
