"""Opt-in Linux `perf` integration. A benchmark that doesn't opt in is untouched.

  - `PerfStat` - hardware-counter totals. A `Metric` that also builds its own
    `perf stat` prefix, via `wrap`: the only place perf enters the argv.
  - `PerfRecord` - a sampling profile. A `Controller`, so it can wrap the
    invocation it is about to run and read the recording back afterwards.

Usage::

    counters = PerfStat(("cache-misses", "cache-references")).lower_is_better()

    bench("matmul")
        .with_command(counters.wrap("./workload"))
        .with_metric(counters)
        .with_controller(PerfRecord(Path("out")))

perf is Linux-only and needs a permissive enough `perf_event_paranoid`; a missing
`perf` fails loudly.
"""

from __future__ import annotations

import csv
import dataclasses
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from bench.core.metric.base import IterationMetric, StderrMetricSource
from bench.core.process import execute
from bench.model.benchmark import Benchmark
from bench.model.invocation import Invocation
from bench.model.results import Direction, Execution, Sample
from bench.report.dir import execution_dir, variant_path
from bench.runner import Controller

# ---------------------------------------------------------------------------
# perf stat
# ---------------------------------------------------------------------------


class PerfStatMetric(IterationMetric):
    """Run a command under `perf stat` and read its counters from stderr.

    `events` are symbolic perf event names - raw `cpu/event=.../` names embed
    commas and are not supported. `direction` applies to every event.
    """

    events: tuple[str, ...]

    def __init__(self, events: tuple[str, ...], direction: Direction) -> None:
        super().__init__(StderrMetricSource, "", "", direction)

        if len(events) == 0:
            raise ValueError("PerfStat needs at least one event")

        self.events = events

    def prefix(self) -> list[str]:
        return ["perf", "stat", "-x", ",", "-e", ",".join(self.events), "--"]

    def process_text(self, text: str) -> Iterable[Sample]:
        counts: dict[str, str] = {}
        for line in text.splitlines():
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


class PerfStat(Controller):
    __slots__ = ("metric",)

    def __init__(
        self,
        *events: str,
        direction: Direction = "uncomparable",
    ) -> None:
        super().__init__()
        self.metric = PerfStatMetric(events, direction)

    def execute_benchmark(self, b: Benchmark, run: int, verbose: bool) -> Execution:
        b = dataclasses.replace(
            b,
            metrics=[*b.metrics, self.metric],
            invocation=dataclasses.replace(
                b.invocation,
                command=[*self.metric.prefix(), *b.invocation.command],
            ),
        )
        return super().execute_benchmark(b, run, verbose)


# ---------------------------------------------------------------------------
# perf record
# ---------------------------------------------------------------------------


class PerfRecord(Controller):
    """Profile every execution with `perf record`, then read the recording back.

    Writes `perf.data` and, unless `frames=False`, the `perf script` frame table
    `perf-frames.csv`, adding the recording's size and sample / frame counts to
    the execution's process samples. Both land in the run's directory under
    `root` - the layout `DirReporter` writes, so they sit beside its stdout.

    `call_graph` picks the unwind method: `"dwarf"` copies `stack_size` bytes of
    user stack per sample and unwinds offline (works on any binary, but costs
    the most and truncates deeper stacks); `"fp"` needs frame pointers; `"lbr"`
    uses the CPU's branch stack.
    """

    __slots__ = (
        "root",
        "freq",
        "call_graph",
        "stack_size",
        "event",
        "frames",
        "nested",
    )

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

    def record_prefix(self, data_file: Path) -> list[str]:
        call_graph = self.call_graph
        if call_graph == "dwarf":
            call_graph += f",{self.stack_size}"

        return [
            "perf",
            "record",
            "-F",
            str(self.freq),
            "-g",
            "--call-graph",
            call_graph,
            "-k1",
            "-e",
            self.event,
            "-o",
            str(data_file),
            "--",
        ]

    def execute_benchmark(self, b: Benchmark, run: int, verbose: bool) -> Execution:
        folder = execution_dir(
            self.root, b.suite, b.name, variant_path(b.variant, nested=self.nested)
        )
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
        if script.is_failure():
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
