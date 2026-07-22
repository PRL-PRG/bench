"""Reporter sinks (CsvReporter, JsonReporter, DirReporter, CompositeReporter)."""

import csv
import io
import json
import re
from pathlib import Path

from rich.console import Console

from bench import (
    CsvReporter,
    DirReporter,
    JsonReporter,
    CompositeReporter,
    FloatPerLine,
    ProgressReporter,
    Sequential,
    SummaryReporter,
    SystemEnvironment,
    Time,
    bench,
    bench_app,
    report_from_json,
    suite,
)
from bench.runner.base import plan
from bench.core.metric import StdoutMetricSource
from bench.core.results import Iteration, Execution, Sample
from bench.report.reporter import DirReporter as _DirReporter
from bench.report.theme import BENCHR_THEME


def test_dirreporter_writes_on_execution_done(tmp_path):
    rep = _DirReporter(tmp_path)
    rep.start([])
    run = Execution(
        suite="S",
        benchmark="b",
        variant=(),
        run=1,
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
            FloatPerLine.last_line(StdoutMetricSource, "runtime", unit="s").lower_is_better()
        )
        .with_runs(2),
    )


def test_csv_writer(tmp_path: Path):
    out = tmp_path / "r.csv"
    Sequential(reporter=CsvReporter(out)).run(plan([_s()], None))
    text = out.read_text()
    lines = text.splitlines()
    assert lines[0].split(",")[:3] == ["suite", "benchmark", "run"]
    assert sum(1 for ln in lines[1:] if ",runtime," in ln) == 2  # 2 runs
    assert (
        sum(1 for ln in lines[1:] if ",elapsed," in ln) == 2
    )  # elapsed always measured


def test_json_writer_round_trip(tmp_path: Path):
    out = tmp_path / "r.json"
    Sequential(reporter=JsonReporter(out)).run(plan([_s()], None))
    r = report_from_json(out.read_text())
    all_samples = [s for run in r.executions for o in run.iterations for s in o.samples]
    assert len(all_samples) == 2
    assert all(s.metric == "runtime" for s in all_samples)


def test_dir_writer_creates_tree(tmp_path: Path):
    root = tmp_path / "tree"
    Sequential(reporter=DirReporter(root)).run(plan([_s()], None))
    files = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    expected_files = {"seq", "stdout", "stderr", "exitcode"}
    leaf_files = {f.name for f in files}
    assert expected_files <= leaf_files
    # one dir per run
    run_dirs = sorted({p.parent for p in files})
    assert len(run_dirs) == 2


def test_mixed_fans_out(tmp_path: Path):
    js = tmp_path / "r.json"
    cs = tmp_path / "r.csv"
    Sequential(reporter=CompositeReporter(JsonReporter(js), CsvReporter(cs))).run(
        plan([_s()], None)
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
        .add_all(_s())
        .run(["--no-progress"])
    )
    env_file = root / "environment.json"
    assert env_file.exists()
    assert "system" in json.loads(env_file.read_text())["environment"]


def _flagged_run() -> Execution:
    return Execution(
        suite="S",
        benchmark="b",
        variant=(),
        run=1,
        command=("x",),
        iterations=[
            Iteration(
                samples=[
                    Sample("runtime", 1.0, unit="s", lower_is_better=True),
                    Sample(
                        "runtime",
                        100.0,
                        unit="s",
                        lower_is_better=True,
                        extra={"outlier": True},
                    ),
                ]
            )
        ],
    )


def test_csv_includes_outlier_column(tmp_path: Path):
    out = tmp_path / "r.csv"
    rep = CsvReporter(out)
    rep.execution_done(_flagged_run())
    rep.finalize()
    rows = list(csv.DictReader(out.open()))
    assert "outlier" in rows[0]
    flags = {r["value"]: r["outlier"] for r in rows}
    assert flags["1.0"] == "False"
    assert flags["100.0"] == "True"


def test_json_persists_outlier_flag(tmp_path: Path):
    out = tmp_path / "r.json"
    rep = JsonReporter(out)
    rep.execution_done(_flagged_run())
    rep.finalize()
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
    Sequential(reporter=CsvReporter(out)).run(plan([s], None))
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
    Sequential(reporter=rep).run(plan([s], None))
    rep.finalize()
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
    Sequential(reporter=rep).run(plan([s], None))
    rep.finalize()
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
    Sequential(reporter=rep).run(plan([s], None))
    rep.finalize()
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
    Sequential(reporter=ProgressReporter(target_console=c)).run(plan([s], None))
    text = buf.getvalue()
    # One line per sample, with running count and 'ok' tag.
    assert "[1|3]" in text and "[2|3]" in text and "[3|3]" in text
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
    Sequential(reporter=ProgressReporter(target_console=c)).run(plan([s], None))
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
    Sequential(reporter=ProgressReporter(target_console=c)).run(plan([s], None))
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
    Sequential(reporter=SummaryReporter(target_console=c)).run(plan([s], None))
    assert "[v1]" in buf.getvalue()


def test_progress_plain_count_scopes_per_benchmark():
    # In non-TTY mode each benchmark restarts its own [n|total] iteration count.
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
    Sequential(reporter=ProgressReporter(target_console=c)).run(plan([s], None))
    text = buf.getvalue()
    assert text.count("[1|2]") == 2 and text.count("[2|2]") == 2
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
    Sequential(reporter=rep).run(plan([s], None))
    assert rep._passed == 1 and rep._failed == 1


def test_summary_channel_keeps_progress_and_swaps_summary():
    # bench_app(summary=...) must keep the progress bar (and CLI sinks) while
    # replacing only the default summary.
    from types import SimpleNamespace

    from bench.run import default_reporter
    from bench.report.reporter import CompositeReporter, ProgressReporter

    marker = SummaryReporter()
    ctx = SimpleNamespace(
        params=SimpleNamespace(progress=True, json=None, csv=None, dir=None)
    )
    rep = default_reporter(ctx, marker)  # type: ignore[arg-type]
    assert isinstance(rep, CompositeReporter)
    assert any(isinstance(r, ProgressReporter) for r in rep.reporters)
    assert marker in rep.reporters


def test_eta_column_blank_for_single_or_unknown_total():
    from types import SimpleNamespace

    from bench.report.reporter import _EtaColumn

    col = _EtaColumn()
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
    Sequential(reporter=ProgressReporter(target_console=c)).run(plan([s], None))
    out = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", buf.getvalue())
    assert "Finished: S/a" in out
    assert "(3 runs, 0 failed)" in out


def test_eta_column_present_and_estimate_kept_for_command_bar():
    # Command bars carry an "elapsed estimate" column and an ETA column
    # (_EtaColumn self-blanks when the total is unknown or a single iteration).
    from rich.progress import TextColumn

    from bench.report.reporter import _EtaColumn

    c = Console(theme=BENCHR_THEME, file=io.StringIO(), force_terminal=True, width=120)

    def _columns(bench_builder):
        s = suite("S", bench_builder.with_cwd(Path("/tmp")).with_runs(1))
        b = plan([s], None)[0]
        rep = ProgressReporter(target_console=c)
        rep.benchmark_start(b)
        return rep._local.prog.columns

    def _has_estimate(cols) -> bool:
        return any(
            isinstance(col, TextColumn) and "elapsed estimate" in col.text_format
            for col in cols
        )

    command = _columns(bench("c").with_command(["true"]).with_metric(Time()))

    assert _has_estimate(command)
    assert any(isinstance(col, _EtaColumn) for col in command)
