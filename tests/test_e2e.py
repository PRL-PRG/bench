"""End-to-end: real subprocess + full pipeline through Sample stats."""

from pathlib import Path

from benchr import (
    FloatPerLine, JsonReporter, Sequential, Time, bench,
    plan, report_from_json, run, suite,
)


def _all_samples(report):
    return [(r, s) for r in report.runs for s in r.samples]


def test_e2e_sleep_runs_produce_expected_count():
    s = suite("S", bench("a")
              .with_command(["sleep", "0.02"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .with_runs(3))
    pairs = _all_samples(Sequential().run(plan([s], None), ctx=None))
    elapsed = [sm.value for _, sm in pairs if sm.metric == "elapsed"]
    assert len(elapsed) == 3
    assert all(0.01 < v < 0.5 for v in elapsed)


def test_e2e_warmup_then_measure():
    s = suite("S", bench("a")
              .with_command(["sh", "-c", "echo 0.01"])
              .with_cwd(Path("/tmp"))
              .with_metric(FloatPerLine("s").lower_is_better())
              .with_warmup(2)
              .with_runs(2))
    pairs = _all_samples(Sequential().run(plan([s], None), ctx=None))
    phases = [r.phase for r, _ in pairs]
    assert phases == ["warmup", "warmup", "runs", "runs"]


def test_e2e_runs_flag_overrides_every_benchmark():
    # --runs N is applied by the cli orchestrator to the materialized plan, so
    # it replaces each benchmark's own measure count (here 5 and 1) with 2.
    s = suite(
        "S",
        bench("a").with_command(["sleep", "0.01"]).with_cwd(Path("/tmp"))
            .with_metric(Time()).with_runs(5),
        bench("b").with_command(["sleep", "0.01"]).with_cwd(Path("/tmp"))
            .with_metric(Time()).with_runs(1),
    )
    report = run(s, argv=["--runs", "2", "--quiet"])
    per_bench: dict[str, int] = {}
    for r in report.runs:
        per_bench[r.benchmark] = per_bench.get(r.benchmark, 0) + 1
    assert sorted(per_bench.values()) == [2, 2]


def test_e2e_command_not_found_marks_failure(tmp_path: Path):
    out = tmp_path / "r.json"
    s = suite("F", bench("missing")
              .with_command(["/no_such_binary_xyzzy"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .with_runs(3))
    report = Sequential(reporter=JsonReporter(out)).run(plan([s], None), ctx=None)
    assert _all_samples(report) == []
    r = report_from_json(out.read_text())
    assert len(r.failures) == 3
    assert all(f.returncode == -1 for f in r.failures)  # spawn failure


def test_e2e_timeout_marks_failure(tmp_path: Path):
    out = tmp_path / "r.json"
    s = suite("F", bench("hang")
              .with_command(["sh", "-c", "sleep 5"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .with_timeout(0.05)
              .with_runs(1))
    report = Sequential(reporter=JsonReporter(out)).run(plan([s], None), ctx=None)
    assert _all_samples(report) == []
    r = report_from_json(out.read_text())
    assert len(r.failures) == 1
    assert r.failures[0].returncode == 124  # timeout
