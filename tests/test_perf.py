"""Opt-in perf integration: a self-contained Metric and a profiling Controller.

`PerfStat` both builds the `perf stat` command prefix (via `wrap`) and parses
perf's `-x,` CSV from the process stderr. It never touches argv on its own.
`PerfRecord` is a `Controller`: it wraps the invocation it is about to run in
`perf record` and reads the recording back afterwards. Neither needs a `perf`
binary to be tested - the recording itself is the only part that does.
"""

from pathlib import Path

import pytest
from conftest import make_success

from bench import (
    Params,
    PerfRecord,
    PerfStat,
    Sample,
    bench,
    iter_perf_frames,
    suite,
    write_perf_frames,
)
from bench.builder.suite import plan

# ----- construction ---------------------------------------------------------


def test_no_events_rejected():
    with pytest.raises(ValueError):
        PerfStat()


# ----- wrap (the one place perf enters argv) --------------------------------


def test_wrap_string_command():
    c = PerfStat(("cache-misses", "cache-references"))
    assert c.wrap("./workload") == [
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
    assert c.wrap(["./workload", "-n", "5"]) == [
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
    once = c.wrap("./workload")
    assert c.wrap(once) == once


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
    assert all(s.direction == "lower better" for s in samples)


# ----- perf record: the profiling Controller --------------------------------


SCRIPT_OUT = (
    "workload 42 4194.303: 12345 cpu-cycles:u:\n"
    "\t    7f0a0b0c0d0e do_work+0x2a (/usr/lib/libc.so.6)\n"
    "\t    7f0a0b0c0d0f main+0x10 (/tmp/a.out)\n"
    "workload 42 4194.404: 12345 cpu-cycles:u:\n"
    "\t    7f0a0b0c0d0e do_work (/usr/lib/libc.so.6)\n"
)


def _planned(**matrix: list[str]):
    b = bench("b").with_command(["true"])
    if matrix:
        b = b.with_matrix(**matrix)
    return plan([suite("S", b).with_cwd(Path("/tmp"))], Params())


def test_record_prefix_carries_the_recording_settings(tmp_path: Path):
    prefix = PerfRecord(tmp_path, freq=999, event="instructions:u").record_prefix(
        tmp_path / "perf.data"
    )
    assert prefix[:2] == ["perf", "record"]
    assert prefix[-1] == "--"  # the wrapped command follows
    assert "999" in prefix and "instructions:u" in prefix
    assert str(tmp_path / "perf.data") in prefix


def test_call_graph_arg_only_dwarf_takes_a_stack_size(tmp_path: Path):
    assert PerfRecord(tmp_path, stack_size=4096).call_graph_arg() == "dwarf,4096"
    assert PerfRecord(tmp_path, call_graph="fp").call_graph_arg() == "fp"
    assert PerfRecord(tmp_path, call_graph="lbr").call_graph_arg() == "lbr"


def test_output_dir_follows_the_dir_reporter_layout(tmp_path: Path):
    # A variant is keyed by its `dim=val` sub-path, a plain benchmark by its run
    # number - the same leaves DirReporter writes, so the recording lands next
    # to that run's stdout/stderr.
    (plain,) = _planned()
    assert PerfRecord(tmp_path).output_dir(plain, 2) == tmp_path / "S/b/2"

    variants = _planned(size=["small", "big"])
    assert (
        PerfRecord(tmp_path).output_dir(variants[0], 1) == tmp_path / "S/b/size=small"
    )
    assert (
        PerfRecord(tmp_path, nested=True).output_dir(variants[1], 1)
        == tmp_path / "S/b/size/big"
    )


def test_read_recording_emits_nothing_without_a_recording(tmp_path: Path):
    # perf never wrote perf.data (it is missing, or it failed): no samples, and
    # no attempt to run `perf script` over a file that is not there.
    recorder = PerfRecord(tmp_path)
    samples = recorder.read_recording(tmp_path / "perf.data", tmp_path / "f.csv")
    assert list(samples) == []


def test_read_recording_without_frames_reports_only_the_size(tmp_path: Path):
    data = tmp_path / "perf.data"
    data.write_bytes(b"0123456789")
    recorder = PerfRecord(tmp_path, frames=False)
    assert list(recorder.read_recording(data, tmp_path / "f.csv")) == [
        Sample(metric="perf_data_size", value=10.0, unit="B")
    ]
    assert not (tmp_path / "f.csv").exists()


# ----- perf script -> per-frame CSV -----------------------------------------


def test_iter_perf_frames_splits_samples_and_frames():
    assert list(iter_perf_frames(SCRIPT_OUT)) == [
        (1, "4194.303", 1, "do_work", "/usr/lib/libc.so.6"),
        (1, "4194.303", 2, "main", "/tmp/a.out"),
        (2, "4194.404", 1, "do_work", "/usr/lib/libc.so.6"),
    ]


def test_iter_perf_frames_ignores_output_with_no_stacks():
    assert list(iter_perf_frames("")) == []
    assert list(iter_perf_frames("workload 42 4194.303: 12345 cpu-cycles:u:\n")) == []


def test_write_perf_frames_writes_a_header_and_counts(tmp_path: Path):
    out = tmp_path / "nested" / "frames.csv"
    assert write_perf_frames(SCRIPT_OUT, out) == (2, 3)
    lines = out.read_text().splitlines()
    assert lines[0] == "sample_id,timestamp,frame_pos,sym,dso"
    assert lines[1] == "1,4194.303,1,do_work,/usr/lib/libc.so.6"
    assert len(lines) == 4
