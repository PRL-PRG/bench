"""Opt-in hardware counters via Linux `perf stat`.

A single `PerfStat` object is the source of truth for the event list. It does
two things, and only when you ask:

  - `wrap(command)` runs your command under `perf stat -e <events>`. This is the
    only place perf enters the argv. `wrap` is idempotent, so applying it twice
    (or at both suite and benchmark level) never double-prefixes.
  - As a `ProcessMetric`, it parses perf's machine-readable (`-x,`) output from
    the process stderr and emits one Sample per event. It never touches argv.

Usage::

    counters = PerfStat(("cache-misses", "cache-references")).lower_is_better()

    bench("matmul")
        .with_command(counters.wrap("./workload"))
        .with_process_metric(counters)

`perf stat` writes its summary to stderr, which bench captures per process, so
this works under parallel runs without any shared output file. perf is Linux
only and needs `perf_event_paranoid` to permit counting. If `perf` is not on
PATH the command fails loudly ("Command not found: perf"), which is the right
signal for a feature you explicitly opted into.

Only symbolic event names are supported (``cache-misses``, ``instructions``,
``branch-misses``, ...). Raw ``cpu/event=.../`` names that embed commas are not
parsed. Direction (``lower_is_better`` / ``higher_is_better``) applies uniformly
to every event, which is fine for a homogeneous set like cache counters.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from bench.core.execution import ExecutionResult, to_argv
from bench.core.metric import ProcessMetric
from bench.core.sample import Sample


@dataclass(frozen=True)
class PerfStat(ProcessMetric):
    """Run a command under `perf stat` and read its counters from stderr.

    `events` is a tuple of symbolic perf event names. `direction`/`predicate`
    and the `lower_is_better`/`higher_is_better`/`when` combinators come from
    the `ProcessMetric` base unchanged.
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

    def extract(self, result: ExecutionResult) -> Iterable[Sample]:
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
