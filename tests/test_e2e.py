"""End-to-end: real subprocess + full pipeline through Sample stats."""

from pathlib import Path

from benchr import (
    Json, P, Sequential, bench,
    report_from_json, suite,
)


def test_e2e_sleep_runs_produce_expected_count():
    s = suite("S", bench("a")
              .with_command(["sleep", "0.02"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(3))
    samples = Sequential().run([s], ctx=None).samples
    elapsed = [s.value for s in samples if s.metric == "elapsed"]
    assert len(elapsed) == 3
    assert all(0.01 < v < 0.5 for v in elapsed)


def test_e2e_warmup_then_measure():
    s = suite("S", bench("a")
              .with_command(["sh", "-c", "echo 0.01"])
              .with_cwd(Path("/tmp"))
              .with_process(P.float_per_line("s").lower_is_better())
              .with_warmup(2)
              .runs(2))
    samples = Sequential().run([s], ctx=None).samples
    phases = [s.phase for s in samples]
    assert phases == ["warmup", "warmup", "measure", "measure"]


def test_e2e_command_not_found_marks_failure(tmp_path: Path):
    out = tmp_path / "r.json"
    s = suite("F", bench("missing")
              .with_command(["/no_such_binary_xyzzy"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(3))
    samples = Sequential(reporter=Json(out)).run([s], ctx=None).samples
    assert samples == []  # failed runs emit no metrics
    r = report_from_json(out.read_text())
    assert len(r.failures) == 3
    assert all(f.returncode == -1 for f in r.failures)  # spawn failure


def test_e2e_timeout_marks_failure(tmp_path: Path):
    out = tmp_path / "r.json"
    s = suite("F", bench("hang")
              .with_command(["sh", "-c", "sleep 5"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .with_timeout(0.05)
              .runs(1))
    samples = Sequential(reporter=Json(out)).run([s], ctx=None).samples
    assert samples == []
    r = report_from_json(out.read_text())
    assert len(r.failures) == 1
    assert r.failures[0].returncode == 124  # timeout
