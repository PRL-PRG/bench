#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Opt-in hardware counters via `perf stat` (Linux).

`PerfStat` is the single source of truth for the event list: `counters.wrap(...)`
runs the command under `perf stat -e <events>`, and `with_process_metric(counters)`
parses those counters back out of stderr. Nothing perf-related touches a benchmark
that doesn't opt in.

Execution on a Linux box where `perf` can count (see `bench doctor` /
`perf_event_paranoid`). On other platforms this still imports fine. It only fails
if you actually run it without perf.
"""

from bench import PerfStat, bench, run, suite

counters = PerfStat(("cache-misses", "cache-references")).lower_is_better()

# A workload that walks a large array, so the cache counters are non-trivial.
WORKLOAD = ["sh", "-c", "awk 'BEGIN{for (i = 0; i < 3000000; i++) a[i] = i}'"]

s = suite(
    "perf",
    bench("memwalk")
    .with_command(counters.wrap(WORKLOAD))
    .with_process_metric(counters)
    .with_runs(5),
)


if __name__ == "__main__":
    run(s)
