"""CLI: bench run / compare / show."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from bench import Bench, Time, bench, run, suite


REPO = Path(__file__).resolve().parents[1]


def _run(*args, env_extra: dict | None = None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "bench", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_bench_simple_command():
    r = _run("run", "--runs", "2", "sleep 0.01")
    assert r.returncode == 0, r.stderr
    assert "sleep 0.01" in r.stdout


def test_bench_two_commands():
    r = _run("run", "--runs", "2", "sleep 0.01", "sleep 0.02")
    assert r.returncode == 0, r.stderr
    assert "sleep 0.01" in r.stdout
    assert "sleep 0.02" in r.stdout


def test_bench_writes_json(tmp_path: Path):
    out = tmp_path / "out.json"
    r = _run("run", "--runs", "2", "--json", str(out), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    assert out.exists()
    data = json.loads(out.read_text())
    assert "runs" in data
    all_samples = [
        s
        for r in data["runs"]
        for o in r.get("observations", [])
        for s in o.get("samples", [])
    ]
    assert len(all_samples) >= 2


def test_bench_writes_csv(tmp_path: Path):
    out = tmp_path / "out.csv"
    r = _run("run", "--runs", "2", "--csv", str(out), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    lines = out.read_text().splitlines()
    assert lines[0].startswith("suite,benchmark")
    assert len(lines) >= 3  # header + 2 samples


def test_bench_time_bound_caps_runs(tmp_path: Path):
    # High run cap but a short time budget: the time bound stops it early.
    out = tmp_path / "t.json"
    r = _run(
        "run",
        "--no-progress",
        "--runs",
        "100",
        "--time",
        "0.3",
        "--json",
        str(out),
        "sleep 0.05",
    )
    assert r.returncode == 0, r.stderr
    n = len(json.loads(out.read_text())["runs"])
    assert 1 <= n < 100  # stopped by --time well before the run cap


def test_bench_time_zero_uses_exact_run_count(tmp_path: Path):
    out = tmp_path / "t.json"
    r = _run(
        "run",
        "--no-progress",
        "--runs",
        "3",
        "--time",
        "0",
        "--json",
        str(out),
        "sleep 0.01",
    )
    assert r.returncode == 0, r.stderr
    assert len(json.loads(out.read_text())["runs"]) == 3


def test_compare_subcommand(tmp_path: Path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _run("run", "--runs", "2", "--json", str(a), "sleep 0.01")
    _run("run", "--runs", "2", "--json", str(b), "sleep 0.01")
    r = _run("compare", str(a), str(b))
    assert r.returncode == 0, r.stderr
    assert "Summary" in r.stdout or "better" in r.stdout or "worse" in r.stdout


def test_compare_single_file_summarizes(tmp_path: Path):
    out = tmp_path / "out.json"
    _run("run", "--runs", "2", "--json", str(out), "sleep 0.01")
    r = _run("compare", str(out))
    assert r.returncode == 0, r.stderr
    assert "elapsed" in r.stdout


def test_compare_first_is_baseline_no_current_label(tmp_path: Path):
    paths: list[str] = []
    for name in ("a", "b", "c"):
        p = tmp_path / f"{name}.json"
        _run(
            "run",
            "--no-progress",
            "--runs",
            "2",
            "--time",
            "0",
            "--json",
            str(p),
            "sleep 0.01",
        )
        paths.append(str(p))
    r = _run("compare", *paths)
    assert r.returncode == 0, r.stderr
    assert "current" not in r.stdout  # the special "current" slot is gone
    assert "was" in r.stdout  # comparees rendered against the baseline


def test_bench_compare_warns_or_diffs(tmp_path: Path):
    base = tmp_path / "base.json"
    _run("run", "--runs", "2", "--json", str(base), "sleep 0.01")
    r = _run("run", "--runs", "2", "--compare", str(base), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    # Should print the per-benchmark comparison block.
    assert (
        "Summary" in r.stdout
        or "geometric mean" in r.stdout
        or "better" in r.stdout
        or "worse" in r.stdout
    )


def test_compare_missing_file_errors(tmp_path: Path):
    r = _run("compare", str(tmp_path / "nope.json"))
    assert r.returncode == 1
    assert "not found" in r.stderr


def test_bench_help_describes_subcommand():
    r = _run("run", "--help")
    assert r.returncode == 0
    assert "Time one or more shell commands" in r.stdout
    # Per-flag descriptions show up:
    assert "Max measured runs" in r.stdout
    assert "Suppress the progress bar" in r.stdout


def test_bench_no_progress_omits_progress_lines():
    r = _run("run", "--no-progress", "--runs", "2", "sleep 0.01")
    assert r.returncode == 0, r.stderr
    # Plain-progress lines look like "[N|M] run/sleep 0.01 #X ok".
    # With --no-progress they should not appear.
    assert "[1|2]" not in r.stdout
    assert "[2|2]" not in r.stdout
    # Summary still prints.
    assert "sleep 0.01" in r.stdout


def test_bench_non_tty_shows_plain_progress():
    r = _run("run", "--runs", "2", "sleep 0.01")
    assert r.returncode == 0, r.stderr
    # subprocess capture is a non-TTY -> Progress falls back to plain lines.
    assert "[1|2]" in r.stdout
    assert "[2|2]" in r.stdout


def test_bench_surfaces_failure_diagnostics():
    r = _run("run", "--runs", "1", "false")
    # Returncode is 0 because the runner itself succeeded. The *benchmark*
    # failed, which is communicated through the report.
    assert r.returncode == 0, r.stderr
    assert "Failures:" in r.stdout
    assert "exit 1" in r.stdout


def test_bench_two_commands_prints_summary_ranking():
    r = _run("run", "--no-progress", "--runs", "3", "sleep 0.01", "sleep 0.05")
    assert r.returncode == 0, r.stderr
    assert "Summary" in r.stdout
    assert "was" in r.stdout
    assert "times lower than" in r.stdout
    # The fastest is named first in the block, sleep 0.01 should be it.
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
        return [_trivial("S")]

    report = run(discover, params=_Params, argv=["--label", "hello", "--no-progress"])
    assert seen["label"] == "hello"
    assert {r.suite for r in report.runs} == {"S"}


def test_bench_combines_static_and_discovered_suites():
    static = _trivial("Static")

    def discover(_p):
        return [_trivial("Disc")]

    report = Bench().add_suite(static).factory(discover).run(argv=["--no-progress"])
    assert {r.suite for r in report.runs} == {"Static", "Disc"}


def test_run_still_accepts_a_list_of_suites():
    report = run([_trivial("A"), _trivial("B")], argv=["--no-progress"])
    assert {r.suite for r in report.runs} == {"A", "B"}


# ----- --list / --include / --exclude -------------------------------------


def _matrix_suite(suite_name: str = "M", bench_name: str = "b", **matrix):
    return suite(
        suite_name,
        bench(bench_name)
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1)
        .with_matrix(**matrix),
    )


def test_list_prints_tree_and_runs_nothing(capsys):
    report = run([_trivial("Alpha"), _trivial("Beta")], argv=["--list", "--no-progress"])
    out = capsys.readouterr().out
    assert "Alpha" in out
    assert "Beta" in out
    assert not report.runs  # listing executes nothing


def test_list_shows_variants(capsys):
    run(_matrix_suite("M", "b", jdk=(11, 17)), argv=["--list", "--no-progress"])
    out = capsys.readouterr().out
    assert "jdk=11" in out
    assert "jdk=17" in out


def test_include_keeps_only_matching():
    report = run(
        [_trivial("Keep"), _trivial("Drop")],
        argv=["--include", "Keep", "--no-progress"],
    )
    assert {r.suite for r in report.runs} == {"Keep"}


def test_exclude_drops_matching():
    report = run(
        [_trivial("Keep"), _trivial("Drop")],
        argv=["--exclude", "Drop", "--no-progress"],
    )
    assert {r.suite for r in report.runs} == {"Keep"}


def test_exclude_wins_over_include():
    report = run(
        [_trivial("A"), _trivial("B")],
        argv=["--include", ".", "--exclude", "B", "--no-progress"],
    )
    assert {r.suite for r in report.runs} == {"A"}


def test_include_anchored_regex_targets_whole_suite():
    # `^alpha/` matches "alpha/b" but not "alphabet/b".
    report = run(
        [_trivial("alpha"), _trivial("alphabet")],
        argv=["--include", "^alpha/", "--no-progress"],
    )
    assert {r.suite for r in report.runs} == {"alpha"}


def test_include_selects_single_variant():
    report = run(
        _matrix_suite("M", "b", jdk=(11, 17)),
        argv=["--include", "jdk=17", "--no-progress"],
    )
    assert [dict(r.variant).get("jdk") for r in report.runs] == ["17"]


def test_bad_regex_exits_2():
    with pytest.raises(SystemExit) as ei:
        run(_trivial("A"), argv=["--include", "(", "--no-progress"])
    assert ei.value.code == 2


def test_empty_selection_exits_1():
    with pytest.raises(SystemExit) as ei:
        run(_trivial("A"), argv=["--include", "no-such-bench", "--no-progress"])
    assert ei.value.code == 1
