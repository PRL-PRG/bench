"""Opt-in hardware counters via Linux `perf stat`.

A single `PerfStat` object owns the event list and does two things, on request:

  - `wrap(command)` runs the command under `perf stat -e <events>` - the only
    place perf enters the argv. It is idempotent, so applying it at both suite
    and benchmark level never double-prefixes.
  - As a `Metric`, it parses perf's machine-readable (`-x,`) output from
    the process stderr (captured per process, so parallel runs need no shared
    file), one Sample per event.

Usage::

    counters = PerfStat(("cache-misses", "cache-references")).lower_is_better()

    bench("matmul")
        .with_command(counters.wrap("./workload"))
        .with_process_metric(counters)

perf is Linux-only and needs `perf_event_paranoid` to permit counting; a missing
`perf` fails loudly. Only symbolic event names are supported (raw
`cpu/event=.../` names that embed commas are not). Direction applies to every
event.
"""

from __future__ import annotations

from collections.abc import Iterable

from bench.core.invocation import InvocationResult, to_argv
from bench.core.metric import Direction, Metric
from bench.core.results import Sample


class PerfStat(Metric):
    """Run a command under `perf stat` and read its counters from stderr.

    `events` is a tuple of symbolic perf event names. `direction` and the
    `lower_is_better`/`higher_is_better` combinators come from the
    `Metric` base unchanged.
    """

    events: tuple[str, ...] = ()

    def __init__(self, direction: Direction = None) -> None:
        super().__init__("", "", direction)

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
