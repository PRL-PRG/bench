"""Reporter sinks (CsvReporter, JsonReporter, DirReporter, CompositeReporter)."""

import csv
import io
import json
import re
from pathlib import Path

from rich.console import Console

from bench import (
    CompositeReporter,
    CsvReporter,
    DirReporter,
    FloatPerLine,
    JsonReporter,
    ProgressReporter,
    SequentialRunner,
    SummaryReporter,
    SystemEnvironment,
    Time,
    bench,
    bench_app,
    report_from_json,
    suite,
)
from bench.builder.context import Params, SharedReporterParams, SharedRunnerParams
from bench.core.invocation import Variant
from bench.core.metric import StdoutMetricSource
from bench.core.results import Execution, Iteration, Report, Sample
from bench.report.reporter import _TUI
from bench.report.reporter import DirReporter as _DirReporter
from bench.report.theme import BENCHR_THEME
from bench.run import default_reporter
from bench.runner.base import plan


def test_dirreporter_writes_on_execution_done(tmp_path):
    rep = _DirReporter(tmp_path)
    rep.start([])
    run = Execution(
        suite="S",
        benchmark="b",
        variant=Variant(),
        run=1,
        runtime=0.01,
        command=("echo", "hi"),
        cwd="/tmp",
        returncode=0,
        stdout="hi\n",
        stderr="",
        iterations=[Iteration(samples=[])],
    )
    rep.execution_done(run)
    assert (tmp_path / "S" / "b" / "1" / "stdout").read_text() == "hi\n"
    assert (tmp_path / "S" / "b" / "1" / "exitcode").read_text() == "0\n"


def _s():
    return suite(
        "S",
        bench("a")
        .with_command(["sh", "-c", "echo 1.5; echo 2.5"])
        .with_cwd(Path("/tmp"))
        .with_metric(
            FloatPerLine(
                StdoutMetricSource, "score", line=2, unit="s"
            ).lower_is_better()
        )
        .with_runs(2),
    )


def test_csv_writer(tmp_path: Path):
    out = tmp_path / "r.csv"
    SequentialRunner().run(plan([_s()], Params()), reporter=CsvReporter(out))
    text = out.read_text()
    lines = text.splitlines()
    assert lines[0].split(",")[:3] == ["suite", "benchmark", "run"]
    assert sum(1 for ln in lines[1:] if ",score," in ln) == 2  # 2 runs
    # There is no implicit `elapsed` metric any more, but the CSV always writes
    # the execution's wall time as a row named `elapsed`, matching what `Time()`
    # calls its sample.
    assert sum(1 for ln in lines[1:] if ",elapsed," in ln) == 2


def test_json_writer_round_trip(tmp_path: Path):
    out = tmp_path / "r.json"
    SequentialRunner().run(plan([_s()], Params()), reporter=JsonReporter(out))
    r = report_from_json(out.read_text())
    all_samples = [s for run in r.executions for o in run.iterations for s in o.samples]
    assert len(all_samples) == 2
    assert all(s.metric == "score" for s in all_samples)


def test_dir_writer_creates_tree(tmp_path: Path):
    root = tmp_path / "tree"
    SequentialRunner().run(plan([_s()], Params()), reporter=DirReporter(root))
    files = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    expected_files = {"seq", "stdout", "stderr", "exitcode"}
    leaf_files = {f.name for f in files}
    assert expected_files <= leaf_files
    # one dir per run
    run_dirs = sorted({p.parent for p in files})
    assert len(run_dirs) == 2


def _matrix_suite():
    return suite(
        "S",
        bench("a")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1)
        .with_matrix(vm=["cpython", "pypy"], opt=["O2"]),
    )


def test_dir_writer_keys_variant_runs_by_their_variant(tmp_path: Path):
    # A matrix variant gets a stable directory named after it, not a completion
    # counter, so a wrapped command's `-o <dir>/...` can target the same place.
    root = tmp_path / "tree"
    rep = DirReporter(root)
    planned = plan([_matrix_suite()], Params())
    SequentialRunner().run(planned, reporter=rep)
    assert (root / "S" / "a" / "opt=O2, vm=cpython" / "exitcode").read_text() == "0\n"
    assert (root / "S" / "a" / "opt=O2, vm=pypy" / "exitcode").read_text() == "0\n"
    # start() pre-creates them, so a wrapped command has the path before it runs
    assert rep.output_dir("S", "a", planned[0].variant).is_dir()


def test_dir_writer_nests_variant_dirs_when_asked(tmp_path: Path):
    root = tmp_path / "tree"
    SequentialRunner().run(
        plan([_matrix_suite()], Params()), reporter=DirReporter(root, nested=True)
    )
    assert (root / "S" / "a" / "opt" / "O2" / "vm" / "pypy" / "exitcode").is_file()


def test_mixed_fans_out(tmp_path: Path):
    js = tmp_path / "r.json"
    cs = tmp_path / "r.csv"
    SequentialRunner().run(
        plan([_s()], Params()),
        reporter=CompositeReporter(JsonReporter(js), CsvReporter(cs)),
    )
    assert js.exists() and cs.exists()


def test_user_composite_reporter_receives_environment(tmp_path: Path):
    # A DirReporter the user supplies via `bench_app(reporter=...)` must get the
    # collected environment injected (not only CLI-built --dir reporters).
    root = tmp_path / "tree"
    (
        bench_app(
            reporter=CompositeReporter(SummaryReporter(), DirReporter(root)),
            environment=SystemEnvironment(),
        )
        .add(_s())
        .run_cli(["--no-progress"])
    )
    env_file = root / "environment.json"
    assert env_file.exists()
    assert "system" in json.loads(env_file.read_text())["environment"]


def _flagged_run() -> Execution:
    return Execution(
        suite="S",
        benchmark="b",
        variant=Variant(),
        run=1,
        runtime=1.0,
        command=("x",),
        iterations=[
            Iteration(
                samples=[
                    Sample("score", 1.0, unit="s", direction="lower better"),
                    Sample(
                        "score",
                        100.0,
                        unit="s",
                        direction="lower better",
                        extra={"outlier": True},
                    ),
                ]
            )
        ],
    )


def test_csv_writes_sample_values(tmp_path: Path):
    out = tmp_path / "r.csv"
    CsvReporter(out).finalize(Report(executions=[_flagged_run()]))
    rows = [r for r in csv.DictReader(out.open()) if r["metric"] == "score"]
    assert sorted(r["value"] for r in rows) == ["1.0", "100.0"]


def test_csv_includes_outlier_column(tmp_path: Path):
    # `outlier` becomes a column because some sample carries it in `extra`
    # `extra` is free-form, so a sample without the key is blank rather than False -
    # the writer cannot know that every extra key is a boolean.
    out = tmp_path / "r.csv"
    CsvReporter(out).finalize(Report(executions=[_flagged_run()]))
    rows = [r for r in csv.DictReader(out.open()) if r["metric"] == "score"]
    assert "outlier" in rows[0]
    assert sorted(r["outlier"] for r in rows) == ["", "True"]


def test_json_persists_outlier_flag(tmp_path: Path):
    out = tmp_path / "r.json"
    rep = JsonReporter(out)
    rep.finalize(Report(executions=[_flagged_run()]))
    samples = [
        s
        for run in report_from_json(out.read_text()).executions
        for it in run.iterations
        for s in it.samples
    ]
    assert sorted(s.extra.get("outlier", False) for s in samples) == [False, True]


def test_csv_header_includes_variant_columns(tmp_path: Path):
    out = tmp_path / "r.csv"
    s = (
        suite(
            "M",
            bench("c")
            .with_command(lambda ctx: ["sh", "-c", "sleep 0.01"])
            .with_matrix(compiler=["gcc"]),
        )
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1)
    )
    SequentialRunner().run(plan([s], Params()), reporter=CsvReporter(out))
    header = out.read_text().splitlines()[0]
    assert "compiler" in header.split(",")


# ---------------------------------------------------------------------------
# Summary failure diagnostics
# ---------------------------------------------------------------------------


def _string_console() -> tuple[Console, io.StringIO]:
    """Rich Console wired to a StringIO, non-TTY, no ANSI markup."""
    buf = io.StringIO()
    c = Console(
        theme=BENCHR_THEME,
        file=buf,
        force_terminal=False,
        width=200,
        no_color=True,
        highlight=False,
    )
    return c, buf


def test_summary_appends_failures_block_with_diagnostic():
    c, buf = _string_console()
    s = suite(
        "F",
        bench("bad")
        .with_command(["sh", "-c", "echo trouble >&2; exit 7"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1),
    )
    rep = SummaryReporter(target_console=c)
    SequentialRunner().run(plan([s], Params()), reporter=rep)
    text = buf.getvalue()
    assert "Failures:" in text
    assert "F/bad" in text
    assert "exit 7" in text
    assert "trouble" in text  # last-line stderr excerpt


def test_summary_failures_block_handles_spawn_failure():
    c, buf = _string_console()
    s = suite(
        "F",
        bench("missing")
        .with_command(["/no_such_binary_xyzzy"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1),
    )
    rep = SummaryReporter(target_console=c)
    SequentialRunner().run(plan([s], Params()), reporter=rep)
    text = buf.getvalue()
    assert "spawn failed" in text
    assert "Command not found" in text


def test_summary_no_failures_block_when_all_succeed():
    c, buf = _string_console()
    s = suite(
        "S",
        bench("a")
        .with_command(["sh", "-c", "echo ok"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1),
    )
    rep = SummaryReporter(target_console=c)
    SequentialRunner().run(plan([s], Params()), reporter=rep)
    assert "Failures:" not in buf.getvalue()


# ---------------------------------------------------------------------------
# Progress non-TTY fallback
# ---------------------------------------------------------------------------


def test_progress_plain_lines_in_non_tty():
    c, buf = _string_console()
    s = suite(
        "S",
        bench("a")
        .with_command(["sh", "-c", "echo ok"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(3),
    )
    SequentialRunner().run(
        plan([s], Params()), reporter=ProgressReporter(target_console=c)
    )
    text = buf.getvalue()
    # One line per sample, with running count and 'ok' tag
    assert "[1/3]" in text and "[2/3]" in text and "[3/3]" in text
    assert text.count(" ok") >= 3


def test_progress_plain_marks_failures():
    c, buf = _string_console()
    s = suite(
        "F",
        bench("bad")
        .with_command(["sh", "-c", "exit 11"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1),
    )
    SequentialRunner().run(
        plan([s], Params()), reporter=ProgressReporter(target_console=c)
    )
    text = buf.getvalue()
    assert "FAIL" in text and "exit code 11" in text


def test_progress_plain_escapes_identifier_markup():
    # Bracketed text in the identifier (here from the label) must be escaped,
    # otherwise rich eats it as a markup tag and it disappears from the line.
    c, buf = _string_console()
    s = suite(
        "S",
        bench("a")
        .with_command(["sh", "-c", "echo ok"])
        .with_metric(Time())
        .with_label(lambda b: "[v1]")
        .with_runs(1),
    )
    SequentialRunner().run(
        plan([s], Params()), reporter=ProgressReporter(target_console=c)
    )
    assert "[v1]" in buf.getvalue()


def test_summary_failure_line_escapes_identifier_markup():
    c, buf = _string_console()
    s = suite(
        "F",
        bench("bad")
        .with_command(["sh", "-c", "exit 11"])
        .with_metric(Time())
        .with_label(lambda b: "[v1]")
        .with_runs(1),
    )
    SequentialRunner().run(
        plan([s], Params()), reporter=SummaryReporter(target_console=c)
    )
    assert "[v1]" in buf.getvalue()


def test_progress_plain_count_scopes_per_benchmark():
    # In non-TTY mode each benchmark restarts its own [n/total] iteration count.
    c, buf = _string_console()
    s = suite(
        "S",
        bench("a")
        .with_command(["sh", "-c", "echo ok"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(2),
        bench("b")
        .with_command(["sh", "-c", "echo ok"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(2),
    )
    SequentialRunner().run(
        plan([s], Params()), reporter=ProgressReporter(target_console=c)
    )
    text = buf.getvalue()
    assert text.count("[1/2]") == 2 and text.count("[2/2]") == 2
    assert "S/a" in text and "S/b" in text


def test_progress_overall_counts_any_failure_as_failed_benchmark():
    # On a TTY the overall bar tallies whole benchmarks: two failing iterations
    # count as one failed benchmark, not two.
    buf = io.StringIO()
    c = Console(theme=BENCHR_THEME, file=buf, force_terminal=True, width=120)
    rep = ProgressReporter(target_console=c)
    s = suite(
        "S",
        bench("ok")
        .with_command(["sh", "-c", "echo ok"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(2),
        bench("bad")
        .with_command(["sh", "-c", "exit 7"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(2),
    )
    SequentialRunner().run(plan([s], Params()), reporter=rep)
    assert rep._passed == 1 and rep._failed == 1


def test_summary_channel_keeps_progress_and_swaps_summary(capsys):
    # bench_app(summary=...) must keep the progress bar (and the CLI sinks)
    # while replacing only the default summary. The composition used to happen
    # inside default_reporter(params, summary); it now happens in
    # get_reporter, so assert the behaviour rather than the plumbing.
    buf = io.StringIO()
    marker = SummaryReporter(
        target_console=Console(file=buf, force_terminal=False, width=200)
    )
    s = suite(
        "S",
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1),
    )
    bench_app(summary=marker).add(s).run_cli([])

    # Progress still reports to the real console (plain lines, capsys is no TTY)...
    assert "S/x #1 ok" in capsys.readouterr().out
    # ...while the summary went to the swapped-in reporter alone.
    assert "elapsed" in buf.getvalue()


def test_summary_channel_survives_an_empty_sink_bundle(capsys):
    # With --no-progress and no output file the flag-driven bundle is empty, so
    # the swapped-in summary becomes the whole reporter - it must not be dropped
    # along with the sinks, nor joined by the default summary it replaced.
    buf = io.StringIO()
    marker = SummaryReporter(
        target_console=Console(file=buf, force_terminal=False, width=200)
    )
    s = suite(
        "S",
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1),
    )
    bench_app(summary=marker).add(s).run_cli(["--no-progress"])

    assert "elapsed" in buf.getvalue()  # the app's summary ran
    out = capsys.readouterr().out
    assert "S/x #1 ok" not in out  # no progress lines
    assert "elapsed" not in out  # and no second, default summary


def test_app_reporter_replaces_the_whole_reporter(capsys):
    # `bench_app(reporter=...)` replaces the whole reporter, progress and summary
    # included. run() used to append a default SummaryReporter on top of it, so
    # the app's chosen output gained a summary it never asked for.
    from bench import Reporter

    class Silent(Reporter):
        pass

    s = suite(
        "S",
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(1),
    )
    bench_app(reporter=Silent()).add(s).run_cli(["--no-progress"])
    assert capsys.readouterr().out == ""


def test_eta_column_blank_for_single_or_unknown_total():
    from types import SimpleNamespace

    col = _TUI._EtaColumn()
    assert str(col.render(SimpleNamespace(total=1))) == ""  # type: ignore[arg-type]
    assert str(col.render(SimpleNamespace(total=None))) == ""  # type: ignore[arg-type]


def test_progress_prints_completed_summary_scrollback():
    # On a TTY, a finished benchmark leaves a persistent summary line above the
    # (transient) bars, with the same elapsed stats as the final summary.
    buf = io.StringIO()
    c = Console(
        theme=BENCHR_THEME, file=buf, force_terminal=True, no_color=True, width=120
    )
    s = suite(
        "S",
        bench("a")
        .with_command(["sh", "-c", "echo ok"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
        .with_runs(3),
    )
    SequentialRunner().run(
        plan([s], Params()), reporter=ProgressReporter(target_console=c)
    )
    out = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", buf.getvalue())
    assert "Finished: S/a" in out
    assert "(3 runs, 0 failed)" in out


def test_task_bar_carries_eta_column():
    # The per-iteration "elapsed estimate" column was removed on purpose (see
    # CHANGES.md); the ETA column on the running-benchmark bar survives.
    c = Console(theme=BENCHR_THEME, file=io.StringIO(), force_terminal=True, width=120)
    rep = ProgressReporter(target_console=c)
    assert rep._tui is not None
    assert any(
        isinstance(col, _TUI._EtaColumn) for col in rep._tui.task_progress.columns
    )


# ----- default_reporter: flags from the reporter half, plus app defaults ---


def _sinks(reporter) -> list[type]:
    if reporter is None:
        return []
    if isinstance(reporter, CompositeReporter):
        return [type(r) for r in reporter.reporters]
    return [type(reporter)]


def test_default_reporter_reads_the_reporter_flags(tmp_path: Path):
    p = SharedReporterParams(json=str(tmp_path / "r.json"), csv=str(tmp_path / "r.csv"))
    assert _sinks(default_reporter(p)) == [ProgressReporter, JsonReporter, CsvReporter]


def test_default_reporter_ignores_the_flags_it_cannot_see(tmp_path: Path):
    # A params class that skips the reporter half has no --json/--progress to
    # read, but the app's own sink defaults still apply - the flags are an
    # opt-in, not a precondition for using the builtin bundle.
    class NoReporterFlags(SharedRunnerParams):
        pass

    reporter = default_reporter(NoReporterFlags(), dir=tmp_path / "out")
    assert _sinks(reporter) == [DirReporter]


def test_default_reporter_is_none_when_every_sink_is_off():
    # `--no-progress` with no output file leaves nothing to report to. The
    # bundle says so by returning None rather than an empty composite.
    assert default_reporter(SharedReporterParams(progress=False)) is None
    assert default_reporter(Params()) is None
