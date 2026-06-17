"""Scheduler base (Runner), the suite→benchmark plan builder, and shared helpers."""

from __future__ import annotations

import abc
import subprocess
from dataclasses import dataclass
from typing import Any

from benchr.grammar.benchmark import Benchmark
from benchr.core.execution import (
    ScheduledExecution,
    default_success,
)
from benchr.grammar.suite import Suite
from benchr.report.reporter import Reporter
from benchr.core.sample import Report, RunRecord


class _NoopReporter(Reporter):
    def record(self, rec: RunRecord) -> None:
        pass


# ---------------------------------------------------------------------------
# Plan builder (Suite list -> flat Benchmark list)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PlannedBenchmark:
    """A Benchmark associated with the suite name it belongs to."""

    suite: str
    benchmark: Benchmark


class SuiteMaterializationError(Exception):
    """A suite's factory failed while building its benchmarks."""

    def __init__(self, suite: str, cause: BaseException) -> None:
        self.suite = suite
        self.cause = cause
        super().__init__(self._format())

    def _format(self) -> str:
        lines = [f"Failed to materialize suite {self.suite!r}: {self.cause}"]
        if isinstance(self.cause, subprocess.CalledProcessError):
            out = self.cause.output or self.cause.stderr
            if out:
                text = (
                    out.decode(errors="replace") if isinstance(out, bytes) else str(out)
                )
                lines += ["", text.rstrip()]
        return "\n".join(lines)


def plan(suites: Suite | list[Suite], params: Any = None) -> list[PlannedBenchmark]:
    """Flatten suites + their deferred factories into concrete benchmarks."""
    if isinstance(suites, Suite):
        suites = [suites]
    out: list[PlannedBenchmark] = []
    for s in suites:
        try:
            materialized = s.materialize(params)
        except Exception as cause:
            raise SuiteMaterializationError(s.name, cause) from cause
        for b in materialized:
            out.append(PlannedBenchmark(suite=s.name, benchmark=b))
    return out


def format_scheduled_verbose(sched: ScheduledExecution, benchmark: Benchmark) -> str:
    """Dump a ScheduledExecution + benchmark plan as a deterministic text block.

    One header (``sched.identifier()``) followed by every field of
    ``ScheduledExecution`` (and its nested ``Execution``) plus the benchmark's
    metric/success/plan summary. Every line printed every time — no
    conditional fields.
    """
    e = sched.execution
    env_str = ", ".join(f"{k}={v}" for k, v in e.env.items()) if e.env else ""
    stdin_str = f"{len(e.stdin)} bytes" if e.stdin is not None else "<none>"
    timeout_str = f"{e.timeout}s" if e.timeout is not None else "<none>"
    metric_str = ", ".join(type(m).__name__ for m in benchmark.metrics)
    success_str = (
        "<default>"
        if benchmark.success is default_success
        else getattr(benchmark.success, "__name__", repr(benchmark.success))
    )
    variant_str = dict(sched.variant) if sched.variant else {}
    label_str = sched.variant_label or "<none>"

    return "\n".join(
        [
            sched.identifier(),
            f"  suite:      {sched.suite}",
            f"  benchmark:  {sched.benchmark}",
            f"  run:        {sched.run}",
            f"  command:    {' '.join(e.command)}",
            f"  cwd:        {e.cwd}",
            f"  env:        {{{env_str}}}",
            f"  timeout:    {timeout_str}",
            f"  stdin:      {stdin_str}",
            f"  metrics:    {metric_str}",
            f"  success:    {success_str}",
            f"  variant:    {variant_str}",
            f"  label:      {label_str}",
        ]
    )


# ---------------------------------------------------------------------------
# Runner base
# ---------------------------------------------------------------------------


class Runner(abc.ABC):
    """Abstract runner: drives a set of planned benchmarks to a ``Report``.

    Concrete runners (``Sequential``, ``Parallel``) drive one ``Controller``
    per benchmark; ``Dry`` just enumerates the plan. ``run()`` returns the
    accumulated ``Report``.

    ``max_runs_per_policy`` is a defensive backstop for non-converging custom
    policies. ``max_consecutive_failures`` silently aborts a benchmark whose
    runs keep failing — surface that fact in the report rather than retrying
    forever. Bump it for flaky benchmarks; set high for purely-success suites.
    """

    def __init__(
        self,
        reporter: Reporter | None = None,
        *,
        max_runs_per_policy: int = 10_000,
        max_consecutive_failures: int = 5,
        verbose: bool = False,
    ) -> None:
        self.reporter = reporter or _NoopReporter()
        self.max_runs_per_policy = max_runs_per_policy
        self.max_consecutive_failures = max_consecutive_failures
        self.verbose = verbose

    @abc.abstractmethod
    def run(self, planned: list[PlannedBenchmark], params: Any = None) -> Report: ...
