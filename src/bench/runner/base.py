"""Scheduler base (Runner), the suite->benchmark plan builder, and shared helpers."""

from __future__ import annotations

import abc
import contextlib
import shlex
import subprocess
from collections.abc import Generator
from pathlib import Path
from typing import Any

from bench.builder.benchmark import Benchmark
from bench.core.invocation import (
    Invocation,
    default_success,
    format_identifier,
)
from bench.core.policy import StoppingPolicy
from bench.core.process import install_sigint_handler, interrupted
from bench.builder.suite import SuiteBuilder
from bench.report.reporter import Reporter
from bench.core.results import Report


class _NoopReporter(Reporter):
    pass


# ---------------------------------------------------------------------------
# Plan builder (SuiteBuilder list -> flat Benchmark list)
# ---------------------------------------------------------------------------


class SuiteMaterializationError(Exception):
    """A suite's factory failed while building its benchmarks."""

    def __init__(self, suite: str, cause: BaseException) -> None:
        self.suite = suite
        self.cause = cause
        super().__init__(self._format())

    def _format(self) -> str:
        lines = [f"Failed to materialize suite {self.suite!r}: {self.cause}"]

        # TODO: the idea is that only subprocess failures carry capturable output worth surfacing to user
        if isinstance(self.cause, subprocess.CalledProcessError):
            out = self.cause.output or self.cause.stderr
            if out:
                text = (
                    out.decode(errors="replace") if isinstance(out, bytes) else str(out)
                )
                lines += ["", text.rstrip()]
        return "\n".join(lines)


def plan(
    suites: list[SuiteBuilder],
    params: Any = None,
) -> list[Benchmark]:
    """Flatten suites + their deferred factories into resolved benchmarks."""
    out: list[Benchmark] = []
    for s in suites:
        try:
            out.extend(s.materialize(params))
        except Exception as cause:
            raise SuiteMaterializationError(s.name, cause) from cause
    return out


def format_command(e: Invocation) -> str:
    """A copy-pasteable shell command for one execution: `cd DIR && KEY='v' cmd
    args`. The `cd` prefix appears only when the cwd differs from the current
    directory, env assignments only when present. Every part is shell-quoted."""
    parts: list[str] = []
    if e.cwd != Path.cwd():
        parts.append(f"cd {shlex.quote(str(e.cwd))} &&")
    if e.env:
        parts.append(" ".join(f"{k}={shlex.quote(v)}" for k, v in e.env.items()))
    parts.append(shlex.join(e.command))
    return " ".join(parts)


def format_policy(p: StoppingPolicy) -> str:
    """A stopping policy's run bound as a string ("unbounded" when open-ended)."""
    n = p.max_runs()
    return str(n) if n is not None else "unbounded"


def _metric_name(m: Any) -> str:
    """A metric's display name: its `metric` field if it has one, else the
    class name (e.g. Time, Rebench)."""
    return getattr(m, "metric", type(m).__name__)


def format_benchmark_verbose(b: Benchmark, run: int) -> str:
    e = b.invocation
    env_str = ", ".join(f"{k}={v}" for k, v in e.env.items()) if e.env else ""
    stdin_str = f"{len(e.stdin)} bytes" if e.stdin is not None else "<none>"
    timeout_str = f"{e.timeout}s" if e.timeout is not None else "<none>"
    metric_str = ", ".join(
        [_metric_name(m) for m, _src in b.iteration_metrics]
        + [_metric_name(m) for m in b.process_metrics]
    )
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
            f"  warmup:     {format_policy(b.warmup)}",
            f"  runs:       {format_policy(b.runs)}",
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
    """Abstract runner: drives a set of benchmarks to a `Report`."""

    def __init__(
        self,
        reporter: Reporter | None = None,
        *,
        verbose: bool = False,
    ) -> None:
        self.reporter = reporter or _NoopReporter()
        self.verbose = verbose

    @contextlib.contextmanager
    def _session(self, planned: list[Benchmark]) -> Generator[Report]:
        """Start the reporter, yield a fresh Report to fill under a SIGINT
        handler, and finalize on exit. Raises KeyboardInterrupt if Ctrl+C fired
        while the body ran."""
        self.reporter.start(planned)
        report = Report()
        try:
            with install_sigint_handler():
                yield report
                if interrupted():
                    raise KeyboardInterrupt
        finally:
            self.reporter.finalize()

    @abc.abstractmethod
    def run(self, planned: list[Benchmark]) -> Report: ...
