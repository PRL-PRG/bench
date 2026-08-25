"""Opt-in Linux `perf` integration. A benchmark that doesn't opt in is untouched.

Two ways in, by what perf is asked for:

  - `PerfStat` - hardware-counter totals. It owns the event list, `wrap(command)`
    runs the command under `perf stat -e <events>` (the only place perf enters
    the argv, and idempotent, so wrapping at both suite and benchmark level never
    double-prefixes), and as a `Metric` it parses perf's machine-readable (`-x,`)
    output back out of the process stderr - captured per process, so parallel
    runs need no shared file - one Sample per event.
  - `PerfRecord` - a sampling profile. A `Controller`, so it wraps the invocation
    it is about to run and post-processes the recording afterwards; attach it
    with `.with_controller(...)`.

Usage::

    counters = PerfStat(("cache-misses", "cache-references")).lower_is_better()

    bench("matmul")
        .with_command(counters.wrap("./workload"))
        .with_metric(counters)
        .with_controller(PerfRecord(Path("out")))

perf is Linux-only and needs a permissive enough `perf_event_paranoid`; a missing
`perf` fails loudly. `PerfStat` supports only symbolic event names (raw
`cpu/event=.../` names that embed commas are not) and its direction applies to
every event.
"""

from __future__ import annotations

import csv
import dataclasses
import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, cast

from bench.core.metric import BuildableMetric
from bench.core.process import execute
from bench.model.benchmark import Benchmark
from bench.model.invocation import Invocation, InvocationResult
from bench.model.results import Direction, Execution, Sample
from bench.report.dir import execution_dir, variant_path
from bench.runner import Controller


def to_argv(command: Any) -> tuple[Any, ...]:
    """A bare str/bytes/PathLike is a one-element argv, a Sequence is full argv."""
    if isinstance(command, (str, bytes, os.PathLike)):
        return (cast(Any, command),)
    return tuple(command)


class PerfStat(BuildableMetric):
    """Run a command under `perf stat` and read its counters from stderr.

    `events` is a tuple of symbolic perf event names. `direction` and the
    `lower_is_better`/`higher_is_better` combinators come from bases unchanged.
    """

    events: tuple[str, ...]

    def __init__(
        self, events: tuple[str, ...] = (), direction: Direction = "uncomparable"
    ) -> None:
        super().__init__("", "", direction)

        if len(events) == 0:
            raise ValueError("PerfStat needs at least one event")

        self.events = events

    def _prefix(self) -> list[str]:
        return ["perf", "stat", "-x", ",", "-e", ",".join(self.events), "--"]

    def wrap(self, command: object) -> list[str]:
        """Prepend the `perf stat` invocation to `command` (idempotent).

        Uses the same argv normalization as `with_command` (`to_argv`).
        """
        argv = list(to_argv(command))
        prefix = self._prefix()
        if argv[: len(prefix)] == prefix:
            return [str(a) for a in argv]
        return [*prefix, *(str(a) for a in argv)]

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        counts: dict[str, str] = {}
        for line in (data.stderr or "").splitlines():
            parts = line.split(",")
            if len(parts) < 3:
                continue
            event = parts[2].strip()
            if event:
                counts.setdefault(event, parts[0].strip())
        for event in self.events:
            raw = counts.get(event)
            if raw is None:  # tolerate a `:u`/`:k` modifier suffix in the output
                raw = next(
                    (v for e, v in counts.items() if e.split(":", 1)[0] == event),
                    None,
                )
            if raw is None:
                continue
            try:
                value = float(raw)
            except ValueError:  # `<not counted>` / `<not supported>`
                continue
            yield self.get_sample(metric=event, value=value)


# ---------------------------------------------------------------------------
# perf record
# ---------------------------------------------------------------------------


class PerfRecord(Controller):
    """Profile every execution with `perf record`, then read the recording back.

    Wraps the invocation in `perf record` writing `<dir>/perf.data`, then runs
    `perf script` over that recording into `<dir>/perf-frames.csv` and adds its
    size and sample / frame counts to the execution's process samples. `<dir>` is
    the run's directory under `root`, the same layout `DirReporter` writes, so
    the recording lands next to that run's stdout/stderr.

    `freq`/`call_graph`/`stack_size`/`event` shape the recording (defaults:
    99 Hz, a 16 kB dwarf stack dump, user-space cpu-cycles). `frames=False`
    records only - it skips the `perf script` pass and emits just
    `perf_data_size`.

    `call_graph` picks the unwind method: `"dwarf"` copies `stack_size` bytes of
    user stack into every sample and unwinds offline (works on any binary, but
    the copy dominates the recording cost and truncates stacks deeper than the
    dump); `"fp"` walks %rbp in the kernel and copies nothing, which is far
    cheaper but needs a binary built with frame pointers; `"lbr"` uses the CPU's
    branch stack. `stack_size` is only used by `"dwarf"`.
    """

    def __init__(
        self,
        root: Path,
        *,
        freq: int = 99,
        call_graph: str = "dwarf",
        stack_size: int = 16384,
        event: str = "cpu-cycles:u",
        frames: bool = True,
        nested: bool = False,
    ) -> None:
        super().__init__()
        self.root = root
        self.freq = freq
        self.call_graph = call_graph
        self.stack_size = stack_size
        self.event = event
        self.frames = frames
        self.nested = nested

    def output_dir(self, b: Benchmark, run: int) -> Path:
        leaf = variant_path(b.variant, nested=self.nested) if b.variant else run
        return execution_dir(self.root, b.suite, b.name, leaf)

    def call_graph_arg(self) -> str:
        """The `--call-graph` value; only dwarf takes a stack-dump size."""
        if self.call_graph == "dwarf":
            return f"dwarf,{self.stack_size}"
        return self.call_graph

    def record_prefix(self, data_file: Path) -> list[str]:
        return [
            "perf",
            "record",
            "-F",
            str(self.freq),
            "-g",
            "--call-graph",
            self.call_graph_arg(),
            "-k1",
            "-e",
            self.event,
            "-o",
            str(data_file),
            "--",
        ]

    def execute_benchmark(self, b: Benchmark, run: int, verbose: bool) -> Execution:
        folder = self.output_dir(b, run)
        folder.mkdir(parents=True, exist_ok=True)
        data_file = folder / "perf.data"

        recorded = dataclasses.replace(
            b,
            invocation=dataclasses.replace(
                b.invocation,
                command=[*self.record_prefix(data_file), *b.invocation.command],
            ),
        )
        execution = super().execute_benchmark(recorded, run, verbose)
        if execution.is_failure():
            return execution

        samples = list(self.read_recording(data_file, folder / "perf-frames.csv"))
        return dataclasses.replace(
            execution,
            process_samples=[*execution.process_samples, *samples],
        )

    def read_recording(self, data_file: Path, frames_file: Path) -> Iterable[Sample]:
        """Samples describing the recording: its size, and - unless `frames` is
        off - the sample / frame counts of the `perf script` pass that writes
        `frames_file`. Nothing at all when perf wrote no recording."""
        if not data_file.exists():
            return
        yield Sample(
            metric="perf_data_size", value=float(data_file.stat().st_size), unit="B"
        )
        if not self.frames:
            return

        script = execute(
            Invocation(
                command=["perf", "script", "-i", str(data_file)],
                cwd=data_file.parent,
                inherit_env=True,
            )
        )
        if script.returncode != 0:
            return

        n_samples, n_frames = write_perf_frames(script.stdout, frames_file)
        yield Sample(metric="perf_samples", value=float(n_samples))
        yield Sample(metric="perf_frames", value=float(n_frames))


# ---------------------------------------------------------------------------
# perf script -> per-frame CSV
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^\S")
_FRAME_RE = re.compile(r"^\s+[0-9a-fA-F]+ ")
_TS_RE = re.compile(r"^[0-9]+\.[0-9]+:$")
_OFFSET_RE = re.compile(r"\+0x[0-9a-fA-F]+$")


def iter_perf_frames(script_stdout: str) -> Iterator[tuple[int, str, int, str, str]]:
    """Yield `(sample_id, timestamp, frame_pos, sym, dso)` rows from `perf script`.

    A non-indented line starts a sample (`sample_id` counts from 1; the timestamp
    is its `NNN.NNN:` field). Each following indented `addr sym+0xoff (dso)` line
    is a stack frame (`frame_pos` counts from 1 within the sample), with the
    `+0x...` offset stripped from the symbol and the parentheses from the dso.
    """
    sid = 0
    ts = ""
    fpos = 0
    for line in script_stdout.split("\n"):
        if _HEADER_RE.match(line):
            sid += 1
            fpos = 0
            ts = ""
            for field in line.split():
                if _TS_RE.match(field):
                    ts = field[:-1]  # strip trailing colon
                    break
            continue
        if _FRAME_RE.match(line):
            fpos += 1
            fields = line.split()
            dso = fields[-1].strip("()")
            sym = _OFFSET_RE.sub("", " ".join(fields[1:-1]))
            yield (sid, ts, fpos, sym, dso)


def write_perf_frames(script_stdout: str, out_csv: Path) -> tuple[int, int]:
    """Write `iter_perf_frames` rows to `out_csv`; return `(n_samples, n_frames)`.

    `n_samples` is the number of samples that carried a stack (the last sample id
    emitted); `n_frames` is the total number of frame rows.
    """
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    n_samples = 0
    n_frames = 0
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample_id", "timestamp", "frame_pos", "sym", "dso"])
        for sid, ts, fpos, sym, dso in iter_perf_frames(script_stdout):
            w.writerow([sid, ts, fpos, sym, dso])
            n_samples = sid
            n_frames += 1
    return n_samples, n_frames
