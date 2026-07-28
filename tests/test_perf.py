"""Opt-in perf counters/profiles: self-contained ProcessMetrics.

`PerfStat` both builds the `perf stat` command prefix (via `wrap`) and parses
perf's `-x,` CSV from the process stderr. `PerfRecord` (further down) wraps a
command in `perf record` and, on extract, runs `perf script` over the recording
to write a per-frame CSV and report the data size + sample/frame counts. Neither
touches argv on its own.
"""

import os
from pathlib import Path
from typing import Any

import pytest

from bench import (
    DirReporter,
    FixedRuns,
    PerfRecord,
    PerfStat,
    Sample,
    Sequential,
    bench,
    execution_dir,
    max_rss,
    suite,
    variant_path,
    write_perf_frames,
)
from bench.builder.context import Context
from bench.runner.base import plan

from conftest import make_success


# ----- construction ---------------------------------------------------------


def test_no_events_rejected():
    with pytest.raises(ValueError):
        PerfStat()


# ----- wrap (the one place perf enters argv) --------------------------------


def test_wrap_string_command():
    c = PerfStat(("cache-misses", "cache-references"))
    assert c.wrap_command("./workload") == [
        "perf",
        "stat",
        "-x",
        ",",
        "-e",
        "cache-misses,cache-references",
        "--",
        "./workload",
    ]


def test_wrap_list_command_keeps_args():
    c = PerfStat(("cache-misses",))
    assert c.wrap_command(["./workload", "-n", "5"]) == [
        "perf",
        "stat",
        "-x",
        ",",
        "-e",
        "cache-misses",
        "--",
        "./workload",
        "-n",
        "5",
    ]


def test_wrap_is_idempotent():
    c = PerfStat(("cache-misses", "cache-references"))
    once = c.wrap_command("./workload")
    assert c.wrap_command(once) == once


# ----- extract (parse perf -x, CSV from stderr) -----------------------------


def test_extract_emits_one_sample_per_event():
    stderr = "12345,,cache-misses,1000000,100.00,,\n67890,,cache-references,1000000,100.00,,\n"
    samples = list(
        PerfStat(("cache-misses", "cache-references")).process(
            make_success(stderr=stderr)
        )
    )
    assert samples == [
        Sample(metric="cache-misses", value=12345.0, unit=""),
        Sample(metric="cache-references", value=67890.0, unit=""),
    ]


def test_extract_skips_not_counted_and_not_supported():
    stderr = "<not counted>,,cache-misses,,,,\n<not supported>,,cache-references,,,,\n"
    assert (
        list(
            PerfStat(("cache-misses", "cache-references")).process(
                make_success(stderr=stderr)
            )
        )
        == []
    )


def test_extract_no_perf_output_emits_nothing():
    assert (
        list(
            PerfStat(("cache-misses",)).process(
                make_success(stderr="just program noise\n")
            )
        )
        == []
    )
    assert list(PerfStat(("cache-misses",)).process(make_success(stderr=""))) == []


def test_extract_matches_modifier_suffix():
    stderr = "999,,cache-misses:u,1000000,100.00,,\n"
    samples = list(PerfStat(("cache-misses",)).process(make_success(stderr=stderr)))
    assert samples == [Sample(metric="cache-misses", value=999.0, unit="")]


def test_lower_is_better_preserves_events_and_marks_samples():
    c = PerfStat(("cache-misses", "cache-references")).lower_is_better()
    assert c.events == ("cache-misses", "cache-references")
    stderr = "12345,,cache-misses,1000000,100.00,,\n67890,,cache-references,1000000,100.00,,\n"
    samples = list(c.process(make_success(stderr=stderr)))
    assert all(s.lower_is_better is True for s in samples)


# ===========================================================================
# PerfRecord
# ===========================================================================


# A stand-in `perf`: `record` writes a fake perf.data at the `-o` path; `script`
# prints two samples (three frames total). Lets the whole path run without perf.
_STUB_PERF = (
    "#!/bin/sh\n"
    'if [ "$1" = "record" ]; then\n'
    '    shift; out=""\n'
    '    while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
    "    printf 'FAKEPERFDATA' > \"$out\"; exit 0\n"
    'elif [ "$1" = "script" ]; then\n'
    "    printf 'R 1 [000] 1000.500: 1 cpu-cycles:u:\\n"
    "\\t 55e0 do_gc+0x40 (/usr/lib/R)\\n"
    "\\t 55f0 Rf_eval+0x1 (/usr/lib/R)\\n\\n"
    "R 1 [000] 1000.600: 1 cpu-cycles:u:\\n"
    "\\t aa00 sum+0x2 (/usr/lib/R)\\n'\n"
    "    exit 0\n"
    "fi\nexit 1\n"
)


def _install_stub_perf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    perf = bindir / "perf"
    perf.write_text(_STUB_PERF)
    perf.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


# ----- wrap (the one place perf record enters argv) -------------------------


def test_record_wrap_shape():
    argv = PerfRecord(freq=99, stack_size=16384, out_dir=Path("/o/rep=0")).wrap_command(
        ["R", "x.R"]
    )
    assert argv == [
        "perf",
        "record",
        "-F",
        "99",
        "-g",
        "--call-graph",
        "dwarf,16384",
        "-k1",
        "-e",
        "cpu-cycles:u",
        "-o",
        "/o/rep=0/perf.data",
        "--",
        "R",
        "x.R",
    ]


def test_record_wrap_is_idempotent():
    p = PerfRecord(out_dir=Path("/o"))
    once = p.wrap_command(["R"])
    assert p.wrap_command(once) == once


def test_wrap_requires_out_dir():
    with pytest.raises(ValueError):
        PerfRecord().wrap_command(["R"])  # out_dir unset


# ----- auto-wrap: a wrapping ProcessMetric wraps the command on attach --------


def test_wrapping_process_metric_applied_to_command():
    # Attaching a wrapping ProcessMetric wraps the command at resolution time -
    # nothing is hand-wired into with_command.
    s = (
        suite("S", bench("b"))
        .with_command(["R", "x.R"])
        .with_process_metric(PerfRecord(out_dir=Path("/o/rep=0")))
    )
    (b,) = plan([s], None)
    assert tuple(b.invocation.command[:2]) == ("perf", "record")
    assert tuple(b.invocation.command[-2:]) == ("R", "x.R")


def test_non_wrapping_process_metric_leaves_command():
    # An ordinary metric's wrap is identity: the command is untouched.
    s = suite("S", bench("b")).with_command(["R"]).with_process_metric(max_rss())
    (b,) = plan([s], None)
    assert b.invocation.command == ("R",)


# ----- write_perf_frames (perf script -> CSV) -------------------------------


def test_write_perf_frames_parses_samples_and_frames(tmp_path: Path):
    script = (
        "R 1 [000] 1000.500: 1 cpu-cycles:u:\n"
        "\t 55e0 do_gc+0x40 (/usr/lib/R)\n"
        "\t 55f0 Rf_eval+0x1 (/usr/lib/R)\n"
        "\n"
        "R 1 [000] 1000.600: 1 cpu-cycles:u:\n"
        "\t aa00 sum+0x2 (/usr/lib/R)\n"
    )
    out = tmp_path / "perf-frames.csv"
    n_samples, n_frames = write_perf_frames(script, out)
    assert (n_samples, n_frames) == (2, 3)
    rows = out.read_text().splitlines()
    assert rows[0] == "sample_id,timestamp,frame_pos,sym,dso"
    # offset stripped from sym, parens stripped from dso, timestamp kept as text
    assert rows[1] == "1,1000.500,1,do_gc,/usr/lib/R"
    assert rows[3] == "2,1000.600,1,sum,/usr/lib/R"


# ----- execution_dir / DirReporter.output_dir -------------------------------


def test_variant_path_nested_and_flat():
    v = (("a", "1"), ("b", "2"))
    assert variant_path(v) == Path("a", "1", "b", "2")  # nested is the default
    assert variant_path(v, nested=False) == Path("a=1, b=2")
    assert variant_path(()) == Path()


def test_output_dir_nested_and_flat(tmp_path: Path):
    v = (("rep", "0"),)
    # nested (default): a directory level per dimension
    assert DirReporter(tmp_path).output_dir("S", "b", v) == (
        tmp_path / "S" / "b" / "rep" / "0"
    )
    # flat: a single dim=val component
    assert DirReporter(tmp_path, nested=False).output_dir("S", "b", v) == (
        tmp_path / "S" / "b" / "rep=0"
    )
    # low-level joiner still takes a plain leaf
    assert execution_dir(tmp_path, "S", "b", "x") == tmp_path / "S" / "b" / "x"


def test_context_carries_resolved_variant():
    # The resolver populates ctx.variant, so a factory need not rebuild the tuple.
    seen: dict[str, Any] = {}

    def capture(ctx: Context[Any]) -> list[str]:
        seen["variant"] = ctx.variant
        return ["true"]

    s = suite("S", bench("b")).with_matrix(rep=[0]).with_command(capture)
    plan([s], None)
    assert seen["variant"] == (("rep", "0"),)


def test_dirreporter_start_precreates_variant_dirs(tmp_path: Path):
    s = suite("S", bench("b", arg=1)).with_command(["true"]).with_matrix(rep=[0, 1])
    rep = DirReporter(tmp_path)
    rep.start(plan([s], None))
    assert (tmp_path / "S" / "b" / "rep" / "0").is_dir()  # nested
    assert (tmp_path / "S" / "b" / "rep" / "1").is_dir()


# ----- end-to-end: record -> extract (script + CSV) via a stub perf ---------


def _perf_suite(dirs: DirReporter, tmp_path: Path, *, frames: bool = True):
    # A PerfRecord built per variant with a concrete out_dir in the DirReporter's
    # tree. Attaching it with with_process_metric both wraps the command in
    # `perf record` (done by the builder) and reads the recording back on extract.
    def perf(ctx: Context[Any]) -> PerfRecord:
        assert ctx.benchmark is not None
        out_dir = dirs.output_dir(ctx.suite, ctx.benchmark, ctx.variant)
        return PerfRecord(out_dir=out_dir, frames=frames)

    return (
        suite("S", bench("b", arg=1))
        .with_cwd(tmp_path)
        .with_matrix(rep=[0])
        .with_command(["true"])
        .with_process_metric(lambda ctx: (perf(ctx),))
        .with_warmup(0)
        .with_runs(FixedRuns(1))
    )


def test_perf_record_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _install_stub_perf(tmp_path, monkeypatch)
    root = tmp_path / "out"
    dirs = DirReporter(root)  # drives output_dir (via perf.out_dir) + the tree

    report = Sequential(reporter=dirs).run(
        plan([_perf_suite(dirs, tmp_path)], None), None
    )

    (ex,) = report.executions
    assert not ex.is_failure()
    metrics = {s.metric: s.value for s in ex.process_samples}
    assert metrics["elapsed"] >= 0.0
    assert metrics["perf_data_size"] == float(len(b"FAKEPERFDATA"))
    assert metrics["perf_samples"] == 2.0
    assert metrics["perf_frames"] == 3.0

    d = root / "S" / "b" / "rep" / "0"  # nested variant dir
    assert (d / "perf.data").read_bytes() == b"FAKEPERFDATA"
    assert (d / "perf-frames.csv").read_text().splitlines()[1] == (
        "1,1000.500,1,do_gc,/usr/lib/R"
    )
    assert (d / "stdout").exists()  # DirReporter colocated with the perf output


def test_perf_record_no_frames_emits_size_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_stub_perf(tmp_path, monkeypatch)
    root = tmp_path / "out"
    dirs = DirReporter(root)

    report = Sequential(reporter=dirs).run(
        plan([_perf_suite(dirs, tmp_path, frames=False)], None), None
    )

    (ex,) = report.executions
    metrics = {s.metric for s in ex.process_samples}
    assert "perf_data_size" in metrics
    assert "perf_samples" not in metrics and "perf_frames" not in metrics
    assert not (root / "S" / "b" / "rep" / "0" / "perf-frames.csv").exists()
