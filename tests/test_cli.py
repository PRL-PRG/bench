"""CLI: benchr bench / compare / show."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from benchr import Benchr, Time, bench, run, suite


REPO = Path(__file__).resolve().parents[1]


def _run(*args, env_extra: dict | None = None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "benchr", *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_bench_simple_command():
    r = _run("bench", "--runs", "2", "sleep 0.01")
    assert r.returncode == 0, r.stderr
    assert "sleep 0.01" in r.stdout


def test_bench_two_commands():
    r = _run("bench", "--runs", "2", "sleep 0.01", "sleep 0.02")
    assert r.returncode == 0, r.stderr
    assert "sleep 0.01" in r.stdout
    assert "sleep 0.02" in r.stdout


def test_bench_writes_json(tmp_path: Path):
    out = tmp_path / "out.json"
    r = _run("bench", "--runs", "2", "--json", str(out), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    assert out.exists()
    data = json.loads(out.read_text())
    assert "runs" in data
    all_samples = [s for r in data["runs"]
                   for o in r.get("observations", []) for s in o.get("samples", [])]
    assert len(all_samples) >= 2


def test_bench_writes_csv(tmp_path: Path):
    out = tmp_path / "out.csv"
    r = _run("bench", "--runs", "2", "--csv", str(out), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    lines = out.read_text().splitlines()
    assert lines[0].startswith("suite,benchmark")
    assert len(lines) >= 3  # header + 2 samples


def test_compare_subcommand(tmp_path: Path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _run("bench", "--runs", "2", "--json", str(a), "sleep 0.01")
    _run("bench", "--runs", "2", "--json", str(b), "sleep 0.01")
    r = _run("compare", str(a), str(b))
    assert r.returncode == 0, r.stderr
    assert "Summary" in r.stdout or "better" in r.stdout or "worse" in r.stdout


def test_compare_single_file_summarizes(tmp_path: Path):
    out = tmp_path / "out.json"
    _run("bench", "--runs", "2", "--json", str(out), "sleep 0.01")
    r = _run("compare", str(out))
    assert r.returncode == 0, r.stderr
    assert "elapsed" in r.stdout


def test_bench_compare_warns_or_diffs(tmp_path: Path):
    base = tmp_path / "base.json"
    _run("bench", "--runs", "2", "--json", str(base), "sleep 0.01")
    r = _run("bench", "--runs", "2", "--compare", str(base), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    # Should print the per-benchmark comparison block.
    assert "Summary" in r.stdout or "geometric mean" in r.stdout or "better" in r.stdout or "worse" in r.stdout


def test_compare_missing_file_errors(tmp_path: Path):
    r = _run("compare", str(tmp_path / "nope.json"))
    assert r.returncode == 1
    assert "not found" in r.stderr


def test_bench_help_describes_subcommand():
    r = _run("bench", "--help")
    assert r.returncode == 0
    assert "Time one or more shell commands" in r.stdout
    # Per-flag descriptions show up:
    assert "Measured run count" in r.stdout
    assert "Suppress the progress bar" in r.stdout


def test_bench_no_progress_omits_progress_lines():
    r = _run("bench", "--no-progress", "--runs", "2", "sleep 0.01")
    assert r.returncode == 0, r.stderr
    # Plain-progress lines look like "[N|M] bench/sleep 0.01 #X ok".
    # With --no-progress they should not appear.
    assert "[1|2]" not in r.stdout
    assert "[2|2]" not in r.stdout
    # Summary still prints.
    assert "sleep 0.01" in r.stdout


def test_bench_non_tty_shows_plain_progress():
    r = _run("bench", "--runs", "2", "sleep 0.01")
    assert r.returncode == 0, r.stderr
    # subprocess capture is a non-TTY → Progress falls back to plain lines.
    assert "[1|2]" in r.stdout
    assert "[2|2]" in r.stdout


def test_bench_surfaces_failure_diagnostics():
    r = _run("bench", "--runs", "1", "false")
    # Returncode is 0 because the runner itself succeeded; the *benchmark*
    # failed, which is communicated through the report.
    assert r.returncode == 0, r.stderr
    assert "Failures:" in r.stdout
    assert "exit 1" in r.stdout


def test_bench_two_commands_prints_summary_ranking():
    r = _run("bench", "--no-progress", "--runs", "3", "sleep 0.01", "sleep 0.05")
    assert r.returncode == 0, r.stderr
    assert "Summary" in r.stdout
    assert "was" in r.stdout
    assert "times lower than" in r.stdout
    # The fastest is named first in the block; sleep 0.01 should be it.
    assert "'sleep 0.01' [elapsed] was" in r.stdout


# ----- run(): suite materialization errors --------------------------------


def _boom_factory(ctx):
    raise subprocess.CalledProcessError(1, ["java", "--list"], output=b"jvm exploded\n")


def test_run_reports_friendly_materialization_error(capsys):
    s = suite("My Suite").factory(_boom_factory)
    with pytest.raises(SystemExit) as ei:
        run(s, argv=[])
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "Failed to materialize suite 'My Suite'" in err
    assert "jvm exploded" in err  # the failing command's output is surfaced


# ----- run(): suite discovery via factory ---------------------------------


def _trivial(suite_name: str, bench_name: str = "b"):
    return suite(
        suite_name,
        bench(bench_name)
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1),
    )


@dataclass
class _Params:
    label: str = "x"


def test_run_callable_factory_receives_parsed_params():
    # The discovery callable is invoked after CLI parsing with the params
    # instance, so it sees the flag value the user passed.
    seen: dict[str, str] = {}

    def discover(p: _Params):
        seen["label"] = p.label
        return _trivial("S")

    report = run(discover, params=_Params, argv=["--label", "hello", "--no-progress"])
    assert seen["label"] == "hello"
    assert {r.suite for r in report.runs} == {"S"}


def test_benchr_combines_static_and_discovered_suites():
    static = _trivial("Static")

    def discover(_p):
        return [_trivial("Disc")]

    report = Benchr().add_suite(static).factory(discover).run(argv=["--no-progress"])
    assert {r.suite for r in report.runs} == {"Static", "Disc"}


def test_run_callable_factory_may_return_a_single_suite():
    report = run(lambda _p: _trivial("One"), argv=["--no-progress"])
    assert {r.suite for r in report.runs} == {"One"}


def test_run_still_accepts_a_list_of_suites():
    report = run([_trivial("A"), _trivial("B")], argv=["--no-progress"])
    assert {r.suite for r in report.runs} == {"A", "B"}
