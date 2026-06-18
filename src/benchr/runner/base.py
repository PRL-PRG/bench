"""Scheduler base (Runner), the suite→benchmark plan builder, and shared helpers."""

from __future__ import annotations

import abc
import subprocess
from typing import Any

from benchr.grammar.benchmark import Benchmark
from benchr.core.execution import (
    default_success,
    format_identifier,
)
from benchr.grammar.suite import Suite
from benchr.report.reporter import Reporter
from benchr.core.sample import Report


class _NoopReporter(Reporter):
    pass


# ---------------------------------------------------------------------------
# Plan builder (Suite list -> flat Benchmark list)
# ---------------------------------------------------------------------------


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


def plan(suites: Suite | list[Suite], params: Any = None) -> list[Benchmark]:
    """Flatten suites + their deferred factories into resolved benchmarks."""
    if isinstance(suites, Suite):
        suites = [suites]
    out: list[Benchmark] = []
    for s in suites:
        try:
            out.extend(s.materialize(params))
        except Exception as cause:
            raise SuiteMaterializationError(s.name, cause) from cause
    return out


def format_benchmark_verbose(b: Benchmark, run: int) -> str:
    """Dump a resolved Benchmark plan as a deterministic text block.

    One header (the run identifier) followed by every field of the resolved
    `Execution` plus the benchmark's metric/success/variant summary. Every line
    printed every time — no conditional fields.
    """
    e = b.execution
    env_str = ", ".join(f"{k}={v}" for k, v in e.env.items()) if e.env else ""
    stdin_str = f"{len(e.stdin)} bytes" if e.stdin is not None else "<none>"
    timeout_str = f"{e.timeout}s" if e.timeout is not None else "<none>"
    metric_str = ", ".join(type(m).__name__ for m in b.metrics)
    success_str = (
        "<default>"
        if b.success is default_success
        else getattr(b.success, "__name__", repr(b.success))
    )
    variant_str = dict(b.variant) if b.variant else {}
    label_str = b.variant_label or "<none>"

    return "\n".join(
        [
            format_identifier(b.suite, b.name, b.variant, run, b.variant_label),
            f"  suite:      {b.suite}",
            f"  benchmark:  {b.name}",
            f"  run:        {run}",
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
    """Abstract runner: drives a set of planned benchmarks to a `Report`.

    Concrete runners (`Sequential`, `Parallel`) drive one `Controller`
    per benchmark; `Dry` just enumerates the plan. `run()` returns the
    accumulated `Report`.

    `max_runs_per_policy` is a defensive backstop for non-converging custom
    policies. `max_consecutive_failures` silently aborts a benchmark whose
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
    def run(self, planned: list[Benchmark], params: Any = None) -> Report: ...
