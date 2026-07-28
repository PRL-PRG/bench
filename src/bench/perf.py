"""Opt-in Linux `perf` integration for benchmarks.

Two `ProcessMetric`s that run a benchmark command under `perf` and read back what
it produced; a benchmark that doesn't opt in is untouched:

- `PerfStat` - hardware-counter totals from `perf stat`.
- `PerfRecord` - a `perf record` sampling profile plus a per-frame table.

Both share the same shape: a `wrap(...)` that is the only place perf enters the
argv (idempotent, so applying it at both suite and benchmark level never
double-prefixes), and a `ProcessMetric.extract` that turns what perf wrote into
Samples. See each class for its events/output and usage.

perf is Linux-only and needs a permissive enough `perf_event_paranoid`; a missing
`perf` fails loudly when a wrapped command runs.
"""

from __future__ import annotations

import csv
import re
import subprocess
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bench.core.invocation import InvocationResult, to_argv
from bench.core.metric import ProcessMetric
from bench.core.results import Sample

if TYPE_CHECKING:
    from bench.builder.context import Context


@dataclass(frozen=True)
class PerfStat(ProcessMetric):
    """Run a command under `perf stat` and read its hardware counters back.

    A single `PerfStat` owns the event list (`events`, a tuple of symbolic perf
    event names) and does two things on request:

      - `wrap(command)` runs the command under `perf stat -e <events>` (the only
        place perf enters the argv).
      - as a `ProcessMetric`, `extract` parses perf's machine-readable (`-x,`)
        output from the process stderr (captured per process, so parallel runs
        need no shared file), one Sample per event.

    Only symbolic event names are supported (raw `cpu/event=.../` names that embed
    commas are not). `direction` and the `lower_is_better`/`higher_is_better`
    combinators come from the `ProcessMetric` base unchanged, applying to every
    event.

    Usage::

        counters = PerfStat(("cache-misses", "cache-references")).lower_is_better()

        bench("matmul")
            .with_command(counters.wrap("./workload"))
            .with_process_metric(counters)
    """

    events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.events:
            raise ValueError("PerfStat needs at least one event")

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

    def extract(self, result: InvocationResult) -> Iterable[Sample]:
        counts: dict[str, str] = {}
        for line in (result.stderr or "").splitlines():
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
            yield Sample(metric=event, value=value, unit="")


# ---------------------------------------------------------------------------
# perf record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerfRecord(ProcessMetric):
    """Profile a command with `perf record`, then read the recording back.

    Prefixes `perf record` and writes `<out_dir>/perf.data`. As a `ProcessMetric`,
    `extract` runs `perf script` over that recording into
    `<out_dir>/perf-frames.csv` and emits its size and sample / frame counts.

    `freq`/`stack_size`/`event` shape the recording (defaults: 99 Hz, a 16 kB
    dwarf stack dump, user-space cpu-cycles). `frames=False` records only - it
    skips the `perf script` pass and emits just `perf_data_size`.
    """

    freq: int = 99
    stack_size: int = 16384
    event: str = "cpu-cycles:u"
    frames: bool = True
    out_dir: Path | Callable[[Context[Any]], Path] | None = None

    def resolve(self, ctx: Context[Any]) -> PerfRecord:
        out = self.out_dir
        if out is not None and not isinstance(out, Path):
            return replace(self, out_dir=out(ctx))
        return self

    def _dir(self) -> Path:
        if not isinstance(self.out_dir, Path):
            raise ValueError(
                "PerfRecord.out_dir is unset: pass a Path, or resolve the "
                "(ctx) -> Path factory per variant via PerfRecord.resolve(ctx) "
                "(e.g. with_process_metric(lambda ctx: (perf.resolve(ctx),)))"
            )
        return self.out_dir

    def data_file(self) -> Path:
        return self._dir() / "perf.data"

    def frames_file(self) -> Path:
        return self._dir() / "perf-frames.csv"

    def record_prefix(self) -> list[str]:
        return [
            "perf",
            "record",
            "-F",
            str(self.freq),
            "-g",
            "--call-graph",
            f"dwarf,{self.stack_size}",
            "-k1",
            "-e",
            self.event,
            "-o",
            str(self.data_file()),
            "--",
        ]

    def wrap(self, command: object) -> list[str]:
        """Prepend the `perf record` invocation to `command` (idempotent).

        Uses the same argv normalization as `with_command` (`to_argv`); requires
        `out_dir` resolved to a `Path` (see `resolve`).
        """
        argv = list(to_argv(command))
        if argv[:2] == ["perf", "record"]:
            return [str(a) for a in argv]
        return [*self.record_prefix(), *(str(a) for a in argv)]

    def extract(self, result: InvocationResult) -> Iterable[Sample]:
        data = self.data_file()
        if not data.exists():
            return
        yield Sample(
            metric="perf_data_size", value=float(data.stat().st_size), unit="B"
        )
        if not self.frames:
            return
        proc = subprocess.run(
            ["perf", "script", "-i", str(data)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return
        n_samples, n_frames = write_perf_frames(proc.stdout, self.frames_file())
        yield Sample(metric="perf_samples", value=float(n_samples), unit="")
        yield Sample(metric="perf_frames", value=float(n_frames), unit="")


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
    Ports the awk / `PerfScriptMetric` in `analysis/harness.py`.
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
