"""Reporter sinks (Csv, Json, Dir, Mixed)."""

import io
from pathlib import Path

from rich.console import Console

from benchr import (
    Csv, Dir, Json, Mixed, P, Progress, Sequential, Summary, bench,
    report_from_json, suite,
)
from benchr.report.reporter import BENCHR_THEME


def _s():
    return suite(
        "S",
        bench("a")
            .with_command(["sh", "-c", "echo 1.5; echo 2.5"])
            .with_cwd(Path("/tmp"))
            .with_process(P.float_per_line("s").last_line().lower_is_better())
            .runs(2),
    )


def test_csv_writer(tmp_path: Path):
    out = tmp_path / "r.csv"
    Sequential(reporter=Csv(out)).run([_s()], ctx=None)
    text = out.read_text()
    lines = text.splitlines()
    assert lines[0].split(",")[:4] == ["suite", "benchmark", "run", "phase"]
    assert len(lines) == 3  # header + 2 rows


def test_json_writer_round_trip(tmp_path: Path):
    out = tmp_path / "r.json"
    Sequential(reporter=Json(out)).run([_s()], ctx=None)
    r = report_from_json(out.read_text())
    assert len(r.samples) == 2
    assert all(s.metric == "runtime" for s in r.samples)


def test_dir_writer_creates_tree(tmp_path: Path):
    root = tmp_path / "tree"
    Sequential(reporter=Dir(root)).run([_s()], ctx=None)
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
    Sequential(reporter=Mixed(Json(js), Csv(cs))).run([_s()], ctx=None)
    assert js.exists() and cs.exists()


def test_csv_header_includes_variant_columns(tmp_path: Path):
    out = tmp_path / "r.csv"
    s = (
        suite("M", bench("c")
              .with_command(lambda b, ctx: ["sh", "-c", "sleep 0.01"])
              .with_matrix(compiler=["gcc"]))
        .with_cwd(Path("/tmp")).with_process(P.time())
        .runs(1)
    )
    Sequential(reporter=Csv(out)).run([s], ctx=None)
    header = out.read_text().splitlines()[0]
    assert "compiler" in header.split(",")


# ---------------------------------------------------------------------------
# Summary failure diagnostics
# ---------------------------------------------------------------------------


def _string_console() -> tuple[Console, io.StringIO]:
    """Rich Console wired to a StringIO; non-TTY, no ANSI markup."""
    buf = io.StringIO()
    c = Console(theme=BENCHR_THEME, file=buf, force_terminal=False,
                width=200, no_color=True, highlight=False)
    return c, buf


def test_summary_appends_failures_block_with_diagnostic():
    c, buf = _string_console()
    s = suite("F", bench("bad")
              .with_command(["sh", "-c", "echo trouble >&2; exit 7"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(1))
    rep = Summary(target_console=c)
    Sequential(reporter=rep, max_consecutive_failures=1).run([s], ctx=None)
    rep.finalize()
    text = buf.getvalue()
    assert "Failures:" in text
    assert "F/bad" in text
    assert "exit 7" in text
    assert "trouble" in text  # last-line stderr excerpt


def test_summary_failures_block_handles_spawn_failure():
    c, buf = _string_console()
    s = suite("F", bench("missing")
              .with_command(["/no_such_binary_xyzzy"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(1))
    rep = Summary(target_console=c)
    Sequential(reporter=rep, max_consecutive_failures=1).run([s], ctx=None)
    rep.finalize()
    text = buf.getvalue()
    assert "spawn failed" in text
    assert "Command not found" in text


def test_summary_no_failures_block_when_all_succeed():
    c, buf = _string_console()
    s = suite("S", bench("a")
              .with_command(["sh", "-c", "echo ok"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(1))
    rep = Summary(target_console=c)
    Sequential(reporter=rep).run([s], ctx=None)
    rep.finalize()
    assert "Failures:" not in buf.getvalue()


# ---------------------------------------------------------------------------
# Progress non-TTY fallback
# ---------------------------------------------------------------------------


def test_progress_plain_lines_in_non_tty():
    c, buf = _string_console()
    s = suite("S", bench("a")
              .with_command(["sh", "-c", "echo ok"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(3))
    Sequential(reporter=Progress(target_console=c)).run([s], ctx=None)
    text = buf.getvalue()
    # One line per sample, with running count and 'ok' tag.
    assert "[1/3]" in text and "[2/3]" in text and "[3/3]" in text
    assert text.count(" ok") >= 3


def test_progress_plain_marks_failures():
    c, buf = _string_console()
    s = suite("F", bench("bad")
              .with_command(["sh", "-c", "exit 11"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(1))
    Sequential(reporter=Progress(target_console=c),
               max_consecutive_failures=1).run([s], ctx=None)
    assert "FAIL exit 11" in buf.getvalue()
