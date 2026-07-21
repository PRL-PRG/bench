"""End-to-end: real subprocess + full pipeline through Sample stats."""

from pathlib import Path

from bench import (
    FloatPerLine,
    JsonReporter,
    Sequential,
    Time,
    bench,
    report_from_json,
    suite,
)
from bench.runner.base import plan


def _all_samples(report):
    return [
        (r, s)
        for r in report.executions
        for s in (
            *(s for o in r.iterations for s in o.samples),
            *r.process_samples,
        )
    ]


def test_e2e_sleep_runs_produce_expected_count():
    s = suite(
        "S",
        bench("a")
        .with_command(["sleep", "0.02"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_runs(3),
    )
    pairs = _all_samples(Sequential().run(plan([s], None)))
    elapsed = [sm.value for _, sm in pairs if sm.metric == "elapsed"]
    assert len(elapsed) == 3
    assert all(0.01 < v < 0.5 for v in elapsed)


def test_e2e_warmup_then_measure():
    s = suite(
        "S",
        bench("a")
        .with_command(["sh", "-c", "echo 0.01"])
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("s", metric="runtime").lower_is_better())
        .with_warmup(2)
        .with_runs(2),
    )
    report = Sequential().run(plan([s], None))
    # Continuous numbering: the first two iterations are flagged warmup.
    assert [r.run for r in report.executions] == [1, 2, 3, 4]
    assert [o.warmup for r in report.executions for o in r.iterations] == [
        True,
        True,
        False,
        False,
    ]


def test_e2e_command_not_found_marks_failure(tmp_path: Path):
    out = tmp_path / "r.json"
    s = suite(
        "F",
        bench("missing")
        .with_command(["/no_such_binary_xyzzy"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_runs(3),
    )
    report = Sequential(reporter=JsonReporter(out)).run(plan([s], None))
    assert _all_samples(report) == []
    r = report_from_json(out.read_text())
    assert len(r.failures) == 3
    assert all(f.returncode == -1 for f in r.failures)  # spawn failure


def test_e2e_timeout_marks_failure(tmp_path: Path):
    out = tmp_path / "r.json"
    s = suite(
        "F",
        bench("hang")
        .with_command(["sh", "-c", "sleep 5"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_timeout(0.05)
        .with_runs(1),
    )
    report = Sequential(reporter=JsonReporter(out)).run(plan([s], None))
    assert _all_samples(report) == []
    r = report_from_json(out.read_text())
    assert len(r.failures) == 1
    assert r.failures[0].returncode == 124  # timeout
