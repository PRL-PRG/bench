"""Opt-in perf counters: a self-contained ProcessMetric.

`PerfStat` both builds the `perf stat` command prefix (via `wrap`) and parses
perf's `-x,` CSV from the process stderr. It never touches argv on its own.
"""

import pytest

from bench import PerfStat, Sample

from conftest import make_success


# ----- construction ---------------------------------------------------------


def test_events_stored():
    assert PerfStat(("cache-misses", "cache-references")).events == (
        "cache-misses",
        "cache-references",
    )


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
    assert all(s.lower_is_better is True for s in samples)
