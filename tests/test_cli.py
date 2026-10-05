"""CLI: bench run / compare / show."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import bench.__main__ as cli
import bench.builder.app as app_module
from bench import (
    Fingerprint,
    NoBenchmarksMatchedError,
    Probe,
    SharedBenchParams,
    SuiteMaterializationError,
    Time,
    bench,
    bench_app,
    run,
    suite,
)
from bench.core.denoise import DENOISE_DEFAULT_STATE_PATH
from bench.error import BenchError

REPO = Path(__file__).resolve().parents[1]


def _run(*args, env_extra: dict | None = None, cwd: Path | None = None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # rich reads these and reports a terminal even when stdout is a pipe, which
    # flips the CLI into the TTY progress path and wraps every string these
    # tests grep for in ANSI escapes. Scrub them so the subprocess sees the
    # non-TTY it actually has, whatever the caller's shell exports.
    for var in ("FORCE_COLOR", "COLORTERM", "CLICOLOR_FORCE"):
        env.pop(var, None)
    env["TERM"] = "dumb"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "bench", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
    )


def test_bench_version():
    r = _run("--version")
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("bench ")


def test_bench_simple_command():
    r = _run("run", "--runs", "2", "sleep 0.01")
    assert r.returncode == 0, r.stderr
    assert "sleep 0.01" in r.stdout


def test_bench_two_commands():
    r = _run("run", "--runs", "2", "sleep 0.01", "sleep 0.02")
    assert r.returncode == 0, r.stderr
    assert "sleep 0.01" in r.stdout
    assert "sleep 0.02" in r.stdout


def test_bench_matrix_substitution():
    r = _run("run", "--runs", "1", "-M", "n", "0.01,0.02", "sleep {n}")
    assert r.returncode == 0, r.stderr
    assert "sleep 0.01" in r.stdout
    assert "sleep 0.02" in r.stdout


def test_bench_matrix_two_dims():
    r = _run(
        "run",
        "--runs",
        "1",
        "-M",
        "a",
        "0.01,0.02",
        "-M",
        "b",
        "x,y",
        "echo {a} {b}",
    )
    assert r.returncode == 0, r.stderr
    for combo in ("0.01 x", "0.01 y", "0.02 x", "0.02 y"):
        assert combo in r.stdout


def test_bench_writes_json(tmp_path: Path):
    out = tmp_path / "out.json"
    r = _run("run", "--runs", "2", "--json", str(out), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    assert out.exists()
    data = json.loads(out.read_text())
    assert "executions" in data
    all_samples = [
        s
        for r in data["executions"]
        for s in (
            [s for o in r.get("iterations", []) for s in o.get("samples", [])]
            + r.get("process_samples", [])
        )
    ]
    assert len(all_samples) >= 2  # `run` measures Time -> 2 elapsed process samples


def test_bench_writes_csv(tmp_path: Path):
    out = tmp_path / "out.csv"
    r = _run("run", "--runs", "2", "--csv", str(out), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    lines = out.read_text().splitlines()
    # A fingerprint comment preamble (# key: value) precedes the header.
    data = [line for line in lines if not line.startswith("#")]
    assert data[0].startswith("suite,benchmark")
    assert len(data) >= 3  # header + 2 samples


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
    n = len(json.loads(out.read_text())["executions"])
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
    assert len(json.loads(out.read_text())["executions"]) == 3


def test_show_subcommand(tmp_path: Path):
    out = tmp_path / "out.json"
    _run("run", "--runs", "2", "--json", str(out), "sleep 0.01")
    r = _run("show", str(out))
    assert r.returncode == 0, r.stderr
    assert "elapsed" in r.stdout


def test_run_list_prints_the_plan_and_runs_nothing():
    # `bench run` takes the app-level default actions too, so the ad-hoc
    # benchmark can be inspected before it is measured.
    r = _run("run", "--runs", "2", "--list", "sleep 0.01", "sleep 0.02")
    assert r.returncode == 0, r.stderr
    assert "sleep 0.01" in r.stdout and "sleep 0.02" in r.stdout
    assert "elapsed" not in r.stdout  # nothing was measured


def test_show_missing_file_errors(tmp_path: Path):
    r = _run("show", str(tmp_path / "nope.json"))
    assert r.returncode == 1
    assert "not found" in r.stderr


def test_compare_subcommand(tmp_path: Path):
    # Two files of the same benchmark become a `compare` matrix axis: the rows
    # are labeled per file and a geomean head-to-head is printed, with the first
    # file as the baseline subject.
    _run("run", "--runs", "2", "--json", str(tmp_path / "a.json"), "sleep 0.01")
    _run("run", "--runs", "2", "--json", str(tmp_path / "b.json"), "sleep 0.01")
    # Invoke from the reports' dir with relative names, as a user would.
    r = _run("compare", "a.json", "b.json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    # rows carry the filename as given as the leading `compare=` dimension
    assert "compare=a.json" in r.stdout and "compare=b.json" in r.stdout
    assert "Comparison - compare" in r.stdout  # the head-to-head block
    assert "a.json was" in r.stdout  # the first file is the baseline subject


def test_compare_missing_file_errors(tmp_path: Path):
    ok = tmp_path / "ok.json"
    _run("run", "--runs", "2", "--json", str(ok), "sleep 0.01")
    r = _run("compare", str(ok), str(tmp_path / "nope.json"))
    assert r.returncode == 1
    assert "not found" in r.stderr


# ----- bench doctor / bench denoise ------------------------------------------

not_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="must run unprivileged"
)

_CLEAN_LINUX = {
    "timestamp": "2026-01-01T00:00:00+00:00",
    "hostname": "testhost",
    "system": "Linux",
    "release": "6.0.0",
    "machine": "x86_64",
    "python_version": "3.14.0",
    "governors": ["performance"],
}


class _StaticProbe(Probe):
    def __init__(self, facts: dict) -> None:
        self.facts = facts

    def collect(self) -> Fingerprint:
        return Fingerprint(self.facts)


def test_doctor_prints_the_fingerprint():
    r = _run("doctor")
    # The exit code depends on this machine's noise sources.
    assert r.returncode in (0, 1), r.stderr
    assert "Fingerprint:" in r.stdout
    assert "hostname:" in r.stdout


def test_doctor_json_is_the_fingerprint():
    r = _run("doctor", "--json")
    assert r.returncode in (0, 1), r.stderr
    assert "hostname" in json.loads(r.stdout)


def test_doctor_succeeds_on_a_quiet_machine(monkeypatch, capsys):
    monkeypatch.setattr(cli, "SystemProbe", lambda: _StaticProbe(_CLEAN_LINUX))
    assert cli.main(["doctor"]) == 0
    assert "No noise sources detected" in capsys.readouterr().out


def test_doctor_fails_on_a_high_severity_finding(monkeypatch, capsys):
    noisy = _CLEAN_LINUX | {"governors": ["powersave"], "aslr": 2}
    monkeypatch.setattr(cli, "SystemProbe", lambda: _StaticProbe(noisy))
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "CPU frequency scaling enabled" in out
    assert "ASLR enabled" in out


def test_doctor_only_warns_on_a_low_severity_finding(monkeypatch):
    monkeypatch.setattr(
        cli, "SystemProbe", lambda: _StaticProbe(_CLEAN_LINUX | {"aslr": 2})
    )
    assert cli.main(["doctor"]) == 0


def test_denoise_status_changes_nothing(tmp_path: Path):
    state = tmp_path / "state.json"
    r = _run("denoise", "status", "--path", str(state))
    assert r.returncode == 0, r.stderr
    assert not state.exists()


@not_root
@pytest.mark.parametrize("action", ["minimize", "restore"])
def test_denoise_requires_root(tmp_path: Path, action: str):
    state = tmp_path / "state.json"
    r = _run("denoise", action, "--path", str(state))
    assert r.returncode == 2
    assert f"denoise {action} requires root" in r.stderr
    assert "Traceback" not in r.stderr
    assert not state.exists()


class _FakeDenoise:
    """Stands in for `Denoise` so nothing touches the real sysfs."""

    created: list["_FakeDenoise"] = []

    def __init__(self, *, state_path: Path) -> None:
        self.state_path = state_path
        self.calls: list[str] = []
        _FakeDenoise.created.append(self)

    def minimize(self) -> dict[str, str]:
        self.calls.append("minimize")
        return {"/knob": "0"}

    def restore(self) -> dict[str, str]:
        self.calls.append("restore")
        return {"/knob": "1"}

    def __enter__(self) -> dict[str, str]:
        return self.minimize()

    def __exit__(self, *exc: object) -> None:
        self.restore()


@pytest.fixture
def fake_denoise() -> list[_FakeDenoise]:
    _FakeDenoise.created = []
    return _FakeDenoise.created


@pytest.mark.parametrize("action", ["minimize", "restore"])
def test_denoise_as_root_uses_the_given_state_path(
    monkeypatch, capsys, tmp_path: Path, fake_denoise, action: str
):
    monkeypatch.setattr(cli, "is_root", lambda: True)
    monkeypatch.setattr(cli, "Denoise", _FakeDenoise)
    state = tmp_path / "state.json"

    assert cli.main(["denoise", action, "--path", str(state)]) == 0

    (d,) = fake_denoise
    assert d.state_path == state
    assert d.calls == [action]
    # rich wraps at the console width, which can split a long tmp path
    assert str(state) in capsys.readouterr().out.replace("\n", "")


def test_run_check_environment_records_the_fingerprint(tmp_path: Path):
    out = tmp_path / "out.json"
    r = _run("run", "--runs", "1", "--check-environment", "--json", str(out), "true")
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text())
    assert data["fingerprint"]["hostname"]
    assert isinstance(data["diagnostics"], list)


def test_run_without_check_environment_records_no_fingerprint(tmp_path: Path):
    out = tmp_path / "out.json"
    r = _run("run", "--runs", "1", "--json", str(out), "true")
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text())
    assert data["fingerprint"] is None
    assert data["diagnostics"] == []


@not_root
def test_run_denoise_requires_root():
    r = _run("run", "--runs", "1", "--denoise", "true")
    assert r.returncode == 2
    assert "Denoise requires root" in r.stderr
    assert "Traceback" not in r.stderr


def test_script_show_replays_through_configured_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    # `./my-bench show r.json` renders a saved report with the script's own
    # configured summary (here a GeomeanComparisonSummary), running nothing. The
    # summary has no console of its own any more - the app prints it - so this
    # reads the real one.
    from bench import (
        ByBenchmarkMetricSummary,
        GeomeanComparisonSummary,
        Time,
    )

    s = (
        suite("s")
        .add(bench("x"))
        .with_matrix(sleep=["0.01", "0.02"])
        .with_command(lambda ctx: ["sleep", ctx.data.sleep])
        .with_metric(Time())
        .with_runs(1)
    )
    out = tmp_path / "r.json"
    # Default reporter honors --json (a bare reporter would take full control).
    bench_app().add(s).run_cli(["--no-progress", "--json", str(out)])
    capsys.readouterr()  # drop the measured run's output

    summary = ByBenchmarkMetricSummary() & GeomeanComparisonSummary(
        axis="sleep"
    ).on_metrics("elapsed")
    bench_app(summary=summary).add(s).run_cli(["show", str(out)])
    # The configured GeomeanComparisonSummary rendered the saved report.
    assert "Comparison - sleep" in capsys.readouterr().out


def test_bench_help_describes_subcommand():
    r = _run("run", "--help")
    assert r.returncode == 0
    assert "Benchmark one or more shell commands" in r.stdout
    # Per-flag descriptions show up:
    assert "Max measured runs" in r.stdout
    assert "Suppress the progress bar" in r.stdout


def test_bench_no_progress_omits_progress_lines():
    r = _run("run", "--no-progress", "--runs", "2", "sleep 0.01")
    assert r.returncode == 0, r.stderr
    # Plain-progress lines look like "[N/M] run/sleep 0.01 #X ok".
    # With --no-progress they should not appear.
    assert "[1/2]" not in r.stdout
    assert "[2/2]" not in r.stdout
    # Summary still prints.
    assert "sleep 0.01" in r.stdout


def test_bench_non_tty_shows_plain_progress():
    r = _run("run", "--runs", "2", "sleep 0.01")
    assert r.returncode == 0, r.stderr
    # subprocess capture is a non-TTY -> Progress falls back to plain lines.
    # The counter used to print the pre-increment value on this path.
    assert "[1/2]" in r.stdout
    assert "[2/2]" in r.stdout


def test_bench_surfaces_failure_diagnostics():
    r = _run("run", "--runs", "1", "false")
    # Returncode is 0 because the runner itself succeeded. The *benchmark*
    # failed, which is communicated through the report - on stderr, since the
    # failures block is the app's own output rather than a reporter's.
    assert r.returncode == 0, r.stderr
    assert "Failures" in r.stderr
    assert "exit code 1" in r.stderr


def test_bench_two_commands_prints_summary_ranking():
    r = _run("run", "--no-progress", "--runs", "3", "sleep 0.01", "sleep 0.05")
    assert r.returncode == 0, r.stderr
    assert "Comparison - run" in r.stdout
    assert "× better than" in r.stdout
    # The fastest (sleep 0.01) is the subject, listed before sleep 0.05.
    ranking = r.stdout.split("Comparison - run")[1]
    assert ranking.index("sleep 0.01") < ranking.index("sleep 0.05")


# ----- run(): suite materialization errors --------------------------------


def _boom_factory(ctx):
    raise subprocess.CalledProcessError(1, ["java", "--list"], output=b"jvm exploded\n")


def test_run_reports_friendly_materialization_error():
    s = suite("My Suite").generator(_boom_factory)
    with pytest.raises(SuiteMaterializationError) as ei:
        bench_app().add(s).run_cli([])
    msg = str(ei.value)
    assert "Failed to materialize suite 'My Suite'" in msg
    assert "jvm exploded" in msg  # the failing command's output is surfaced


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


class _Params(SharedBenchParams):
    label: str = "x"


def test_run_callable_factory_receives_parsed_params():
    # The discovery callable is invoked after CLI parsing with the params
    # instance, so it sees the flag value the user passed.
    seen: dict[str, str] = {}

    def discover(p: _Params):
        seen["label"] = p.label
        return [_trivial("S")]

    report = (
        bench_app(params=_Params)
        .generator(discover)
        .run_cli(["--label", "hello", "--no-progress"])
    )
    assert seen["label"] == "hello"
    assert {r.suite for r in report.executions} == {"S"}


def test_bench_combines_static_and_discovered_suites():
    static = _trivial("Static")

    def discover(_p):
        return [_trivial("Disc")]

    report = bench_app().add(static).generator(discover).run_cli(["--no-progress"])
    assert {r.suite for r in report.executions} == {"Static", "Disc"}


def test_run_sugar_runs_multiple_suites(monkeypatch, capsys):
    # run(*suites) is sugar for bench_app(<script>).add(*suites).main(),
    # reading the argv from sys.argv. It returns nothing, so the summary is
    # the only observable output.
    monkeypatch.setattr(sys, "argv", ["prog", "--no-progress"])
    run(_trivial("A"), _trivial("B"))
    out = capsys.readouterr().out
    assert "A/b" in out and "B/b" in out


def test_bench_app_defaults_fill_suites_but_lose_to_overrides():
    # s1 declares no command, so it inherits the app-level default.
    s1 = suite("S1", bench("b").with_cwd(Path("/tmp")).with_metric(Time()))
    # s2 sets its own command (which wins over the app default - inner-wins) and
    # its own cwd (which the app never sets, so it survives untouched).
    s2 = (
        suite("S2", bench("c").with_metric(Time()))
        .with_command(["echo", "s2"])
        .with_cwd(Path("/tmp"))
    )

    report = (
        bench_app("demo")
        .add(s1)
        .add(s2)
        .with_command(["true"])
        .with_runs(1)
        .run_cli(["--no-progress"])
    )

    runs = {r.suite: r for r in report.executions}
    assert set(runs) == {"S1", "S2"}
    assert runs["S1"].command == ("true",)  # app default filled a suite that set none
    assert runs["S2"].command == ("echo", "s2")  # inner-wins: suite's command survived
    assert runs["S2"].cwd == "/tmp"  # suite-only setting the app never set survives


def test_bench_app_defaults_reach_subsuite_benchmarks():
    # The app is the weakest level, and its defaults keep cascading past the
    # top-level suite into the sub-suites it contains.
    inner = suite("Inner", bench("b").with_cwd(Path("/tmp")).with_metric(Time()))
    report = (
        bench_app("demo")
        .add(suite("Outer", inner))
        .with_command(["true"])
        .with_runs(1)
        .run_cli(["--no-progress"])
    )
    [run_] = report.executions
    assert run_.suite == "Outer/Inner"
    assert run_.command == ("true",)


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
    report = (
        bench_app()
        .add(_trivial("Alpha"), _trivial("Beta"))
        .run_cli(["--list", "--no-progress"])
    )
    out = capsys.readouterr().out
    assert "Alpha" in out
    assert "Beta" in out
    assert not report.executions  # listing executes nothing


def test_list_shows_variants(capsys):
    bench_app().add(_matrix_suite("M", "b", jdk=(11, 17))).run_cli(
        ["--list", "--no-progress"]
    )
    out = capsys.readouterr().out
    assert "jdk=11" in out
    assert "jdk=17" in out


def test_list_reflects_include_exclude(capsys):
    # `--list` answers "what would this command run", so it honors the same
    # selection the run would: a matching filter narrows the listing...
    app = bench_app().add(_trivial("Alpha"), _trivial("Beta"))
    app.run_cli(["--list", "--include", "Alpha", "--no-progress"])
    out = capsys.readouterr().out
    assert "Alpha" in out
    assert "Beta" not in out

    # ...and one that matches nothing lists nothing.
    app.run_cli(["--list", "--include", "no-such-bench", "--no-progress"])
    assert "0 benchmarks" in capsys.readouterr().out


def test_include_keeps_only_matching():
    report = (
        bench_app()
        .add(_trivial("Keep"), _trivial("Drop"))
        .run_cli(["--include", "Keep", "--no-progress"])
    )
    assert {r.suite for r in report.executions} == {"Keep"}


def test_exclude_drops_matching():
    report = (
        bench_app()
        .add(_trivial("Keep"), _trivial("Drop"))
        .run_cli(["--exclude", "Drop", "--no-progress"])
    )
    assert {r.suite for r in report.executions} == {"Keep"}


def test_exclude_wins_over_include():
    report = (
        bench_app()
        .add(_trivial("A"), _trivial("B"))
        .run_cli(["--include", ".", "--exclude", "B", "--no-progress"])
    )
    assert {r.suite for r in report.executions} == {"A"}


def test_include_anchored_regex_targets_whole_suite():
    # `^alpha/` matches "alpha/b" but not "alphabet/b".
    report = (
        bench_app()
        .add(_trivial("alpha"), _trivial("alphabet"))
        .run_cli(["--include", "^alpha/", "--no-progress"])
    )
    assert {r.suite for r in report.executions} == {"alpha"}


def test_include_matches_the_nested_suite_path():
    # Selection runs on `suite/benchmark`, and a sub-suite's suite name is the
    # whole path - so a pattern can address one branch of the tree.
    def _leaf(name: str):
        return suite(name, bench("b").with_cwd(Path("/tmp")).with_metric(Time()))

    report = (
        bench_app()
        .add(suite("Top", _leaf("Keep"), _leaf("Drop")))
        .with_command(["true"])
        .with_runs(1)
        .run_cli(["--include", "^Top/Keep/", "--no-progress"])
    )
    assert {r.suite for r in report.executions} == {"Top/Keep"}


def test_include_selects_single_variant():
    report = (
        bench_app()
        .add(_matrix_suite("M", "b", jdk=(11, 17)))
        .run_cli(["--include", "jdk=17", "--no-progress"])
    )
    assert [r.variant.get("jdk") for r in report.executions] == ["17"]


def test_selection_composes_with_an_app_filter():
    # --include/--exclude reach the plan as an app-level filter, which the
    # builder accumulates rather than replaces: the app's own predicate and the
    # CLI selection both have to pass.
    s = suite(
        "S",
        bench("keep").with_cwd(Path("/tmp")).with_metric(Time()),
        bench("keep-not-really").with_cwd(Path("/tmp")).with_metric(Time()),
        bench("drop").with_cwd(Path("/tmp")).with_metric(Time()),
    )
    app = (
        bench_app()
        .add(s)
        .with_command(["true"])
        .with_runs(1)
        .with_filter(lambda b: not b.name.endswith("really"))
    )
    planned = app.plan_benchmarks(
        SharedBenchParams(include=["keep"]), use_defaults=True
    )
    assert {b.name for b in planned} == {"keep"}
    # Without a selection the app's own filter still applies on its own.
    planned = app.plan_benchmarks(SharedBenchParams(), use_defaults=True)
    assert {b.name for b in planned} == {"keep", "drop"}


def test_selection_reaches_a_benchmarks_own_filter():
    # The selection is inherited down the tree, so it composes with a filter set
    # on the benchmark itself rather than being applied after the fact.
    s = suite(
        "S",
        bench("a")
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_filter(lambda b: False),
        bench("b").with_cwd(Path("/tmp")).with_metric(Time()),
    )
    planned = (
        bench_app()
        .add(s)
        .with_command(["true"])
        .with_runs(1)
        .plan_benchmarks(SharedBenchParams(include=["."]), use_defaults=True)
    )
    assert {b.name for b in planned} == {"b"}


def test_bad_regex_raises():
    with pytest.raises(re.error):
        bench_app().add(_trivial("A")).run_cli(["--include", "(", "--no-progress"])


def test_empty_selection_raises():
    # A selection that matches nothing raises the dedicated error, not a bare
    # ValueError - the type is public, so callers can catch just this case.
    with pytest.raises(NoBenchmarksMatchedError):
        bench_app().add(_trivial("A")).run_cli(
            ["--include", "no-such-bench", "--no-progress"]
        )


# ----- main(): run_cli as a process exit ----------------------------------


def test_main_returns_normally_on_success():
    bench_app().add(_trivial("A")).main(["--no-progress"])


def test_main_translates_no_match_to_exit_code(capsys):
    # The same empty selection that raises through .run_cli() is a clean exit via
    # .main(): a one-line stderr message, exit 1, and crucially no traceback.
    with pytest.raises(SystemExit) as exc:
        (
            bench_app()
            .add(_trivial("A"))
            .main(["--include", "no-such-bench", "--no-progress"])
        )
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "No benchmark planned" in err
    assert "Traceback" not in err


def test_main_translates_materialization_error_to_exit_code(capsys):
    s = suite("My Suite").generator(_boom_factory)
    with pytest.raises(SystemExit) as exc:
        bench_app().add(s).main([])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Failed to materialize suite 'My Suite'" in err
    assert "Traceback" not in err


# ----- app-level denoise -----------------------------------------------------


@not_root
def test_app_denoise_requires_root(capsys):
    with pytest.raises(SystemExit) as exc:
        bench_app(denoise=True).add(_trivial("A")).main(["--no-progress"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Denoise requires root" in err
    assert "Traceback" not in err


@not_root
def test_app_denoise_refuses_before_running_anything(tmp_path: Path):
    marker = tmp_path / "ran"
    s = suite(
        "S",
        bench("b")
        .with_command(["touch", str(marker)])
        .with_inherit_env()
        .with_cwd(tmp_path)
        .with_runs(1),
    )
    with pytest.raises(BenchError):
        bench_app().add(s).with_denoise().run_cli(["--no-progress"])
    assert not marker.exists()


def test_app_denoise_brackets_the_run_with_the_default_state_path(
    monkeypatch, fake_denoise
):
    monkeypatch.setattr(app_module, "is_root", lambda: True)
    monkeypatch.setattr(app_module, "Denoise", _FakeDenoise)

    report = bench_app(denoise=True).add(_trivial("A")).run_cli(["--no-progress"])

    assert len(report.executions) == 1
    (d,) = fake_denoise
    assert d.state_path == DENOISE_DEFAULT_STATE_PATH
    assert d.calls == ["minimize", "restore"]


def test_app_denoise_state_path_factory_receives_the_params(
    monkeypatch, tmp_path: Path, fake_denoise
):
    monkeypatch.setattr(app_module, "is_root", lambda: True)
    monkeypatch.setattr(app_module, "Denoise", _FakeDenoise)
    seen = []

    def state_path(params) -> Path:
        seen.append(params)
        return tmp_path / "state.json"

    bench_app().add(_trivial("A")).with_denoise(state_path).run_cli(["--no-progress"])

    (d,) = fake_denoise
    assert d.state_path == tmp_path / "state.json"
    assert len(seen) == 1 and isinstance(seen[0], SharedBenchParams)


def test_app_denoise_restores_even_when_the_run_fails(monkeypatch, fake_denoise):
    monkeypatch.setattr(app_module, "is_root", lambda: True)
    monkeypatch.setattr(app_module, "Denoise", _FakeDenoise)

    with pytest.raises(NoBenchmarksMatchedError):
        bench_app(denoise=True).add(_trivial("A")).run_cli(
            ["--include", "no-such-bench", "--no-progress"]
        )

    (d,) = fake_denoise
    assert d.calls == ["minimize", "restore"]


# ----- bench_app(): keyword style and setter style agree -------------------


def test_bench_app_setters_match_the_constructor_keywords():
    kwargs = bench_app("X", params=_Params, denoise=True)
    setters = bench_app("X").with_params(_Params).with_denoise()
    # `suites` holds closures, which never compare equal - the settings do.
    for field in ("name", "params", "denoise"):
        assert getattr(kwargs, field) == getattr(setters, field)
