#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""LevelDB ``db_bench``: the canonical key/value microbenchmarks.

Each bench benchmark maps to one db_bench operation. db_bench prints one
result line per operation, e.g.

    fillseq      :       4.805 micros/op;   23.0 MB/s
    readrandom   :       0.531 micros/op; (5000 of 5000 found)
    crc32c       :       0.778 micros/op; 5023.3 MB/s (4K per op)

Write/CRC ops are self-contained and report a MB/s throughput. Read/seek ops
need a populated DB, so we prefix a ``fillseq`` into the *same* db_bench
invocation (one open DB, rebuilt fresh on every run). A per-op Regex anchored
to the operation name picks that op's line out of the output, so the prefixed
fill is ignored by the read benchmarks.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from bench import BenchmarkBuilder, Regex, Time, bench, max_rss, run, suite


@dataclass
class LevelDBParams:
    db_bench: Path          # required: path to the built db_bench binary
    num: int = 100000       # key/value pairs written (and read)


# Self-contained ops that write data / hash - they report MB/s throughput.
WRITE_OPS = ["fillseq", "fillrandom", "overwrite", "fillsync", "fill100K", "crc32c"]
# Read/seek ops - need a populated DB, so prefix a fillseq in the same process.
READ_OPS = ["readseq", "readreverse", "readrandom", "readhot", "seekrandom"]


def _micros(op: str) -> Regex:
    return Regex(
        "micros_per_op",
        rf"(?m)^{re.escape(op)}\s+:\s+([\d.]+) micros/op",
        unit="us",
    ).lower_is_better()


def _throughput(op: str) -> Regex:
    return Regex(
        "throughput",
        rf"(?m)^{re.escape(op)}\s+:\s+[\d.]+ micros/op;\s+([\d.]+) MB/s",
        unit="MB/s",
    ).higher_is_better()


def _command(seq: str):
    return lambda ctx: [
        str(ctx.params.db_bench),
        f"--benchmarks={seq}",
        f"--num={ctx.params.num}",
    ]


def make_benchmarks() -> list[BenchmarkBuilder]:
    specs: list[BenchmarkBuilder] = []
    for op in WRITE_OPS:
        specs.append(
            bench(op)
            .with_command(_command(op))
            .with_metric(
                _micros(op), _throughput(op), max_rss(), Time(user=True, system=True)
            )
        )
    for op in READ_OPS:
        specs.append(
            bench(op)
            .with_command(_command(f"fillseq,{op}"))
            .with_metric(_micros(op), max_rss(), Time(user=True, system=True))
        )
    return specs


leveldb = suite("LevelDB db_bench").add_all(*make_benchmarks()).with_runs(5)


if __name__ == "__main__":
    run(leveldb, params=LevelDBParams)
