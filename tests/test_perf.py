"""Opt-in perf integration: two profiling Controllers.

`PerfStat` is a `Controller` owning a `PerfStatMetric`: the controller prepends
the `perf stat` invocation and registers the metric, the metric parses perf's
`-x,` CSV out of the process stderr. `PerfRecord` wraps the invocation in
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
    suite,
)
from bench.builder.suite import plan
from bench.perf import PerfStatMetric, iter_perf_frames, write_perf_frames
from bench.runner import Controller

TWO_EVENTS = (
    "12345,,cache-misses,1000000,100.00,,\n67890,,cache-references,1000000,100.00,,\n"
)

# ----- construction ---------------------------------------------------------


def test_no_events_rejected():
    with pytest.raises(ValueError):
        PerfStat()


def test_prefix_carries_every_event_as_one_list():
    metric = PerfStat("cache-misses", "cache-references").metric
    assert metric.prefix() == [
        "perf",
        "stat",
        "-x",
        ",",
        "-e",
        "cache-misses,cache-references",
        "--",
    ]


# ----- the metric (parse perf -x, CSV from stderr) --------------------------


def test_metric_emits_one_sample_per_event():
    metric = PerfStat("cache-misses", "cache-references").metric
    assert list(metric.process(make_success(stderr=TWO_EVENTS))) == [
        Sample(metric="cache-misses", value=12345.0, unit=""),
        Sample(metric="cache-references", value=67890.0, unit=""),
    ]


def test_metric_reads_stderr_not_stdout():
    # perf writes its counters to stderr; the same CSV on stdout is the
    # workload's own output and must not be mistaken for counters.
    metric = PerfStat("cache-misses", "cache-references").metric
    assert list(metric.process(make_success(stdout=TWO_EVENTS))) == []


def test_metric_skips_not_counted_and_not_supported():
    stderr = "<not counted>,,cache-misses,,,,\n<not supported>,,cache-references,,,,\n"
    metric = PerfStat("cache-misses", "cache-references").metric
    assert list(metric.process(make_success(stderr=stderr))) == []


def test_metric_no_perf_output_emits_nothing():
    metric = PerfStat("cache-misses").metric
    assert list(metric.process(make_success(stderr="just program noise\n"))) == []
    assert list(metric.process(make_success(stderr=""))) == []


def test_metric_matches_modifier_suffix():
    stderr = "999,,cache-misses:u,1000000,100.00,,\n"
    metric = PerfStat("cache-misses").metric
    assert list(metric.process(make_success(stderr=stderr))) == [
        Sample(metric="cache-misses", value=999.0, unit="")
    ]


def test_direction_is_set_once_and_applies_to_every_event():
    counters = PerfStat("cache-misses", "cache-references", direction="lower better")
    samples = list(counters.metric.process(make_success(stderr=TWO_EVENTS)))
    assert len(samples) == 2
    assert all(s.direction == "lower better" for s in samples)


def test_metric_is_usable_on_its_own():
    # The metric is the reusable half: given the text, it parses it, whether or
    # not the controller put `perf stat` on the argv.
    metric = PerfStatMetric(("cache-misses",), "lower better")
    assert list(metric.process_text("999,,cache-misses,1000000,100.00,,\n")) == [
        Sample(metric="cache-misses", value=999.0, unit="", direction="lower better")
    ]


# ----- perf stat: the wrapping Controller -----------------------------------


def _planned(controller: Controller):
    s = suite("S", bench("b").with_command(["true"]).with_controller(controller))
    return plan([s.with_cwd(Path("/tmp"))], Params())[0]


def test_perf_stat_controller_wraps_the_command_and_adds_its_metric(monkeypatch):
    handed_down = []
    monkeypatch.setattr(
        Controller,
        "execute_benchmark",
        lambda self, b, run, verbose: handed_down.append(b),
    )

    controller = PerfStat("cache-misses")
    b = _planned(controller)
    controller.execute_benchmark(b, 1, False)

    (wrapped,) = handed_down
    assert list(wrapped.invocation.command) == [
        *controller.metric.prefix(),
        "true",
    ]
    assert controller.metric in wrapped.metrics


# ----- perf record: the profiling Controller --------------------------------


SCRIPT_OUT = (
    "workload 42 4194.303: 12345 cpu-cycles:u:\n"
    "\t    7f0a0b0c0d0e do_work+0x2a (/usr/lib/libc.so.6)\n"
    "\t    7f0a0b0c0d0f main+0x10 (/tmp/a.out)\n"
    "workload 42 4194.404: 12345 cpu-cycles:u:\n"
    "\t    7f0a0b0c0d0e do_work (/usr/lib/libc.so.6)\n"
)


def test_record_prefix_carries_the_recording_settings(tmp_path: Path):
    prefix = PerfRecord(tmp_path, freq=999, event="instructions:u").record_prefix(
        tmp_path / "perf.data"
    )
    assert prefix[:2] == ["perf", "record"]
    assert prefix[-1] == "--"  # the wrapped command follows
    assert "999" in prefix and "instructions:u" in prefix
    assert str(tmp_path / "perf.data") in prefix


def test_record_prefix_only_dwarf_takes_a_stack_size(tmp_path: Path):
    def call_graph(recorder: PerfRecord) -> str:
        prefix = recorder.record_prefix(tmp_path / "perf.data")
        return prefix[prefix.index("--call-graph") + 1]

    assert call_graph(PerfRecord(tmp_path, stack_size=4096)) == "dwarf,4096"
    assert call_graph(PerfRecord(tmp_path, call_graph="fp")) == "fp"
    assert call_graph(PerfRecord(tmp_path, call_graph="lbr")) == "lbr"


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
