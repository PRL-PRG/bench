"""ExecutionSource: produces Iterations and assembles Executions for one benchmark-variant.

`CommandSource` spawns one process per iteration (pull): each iteration is a
finished Execution with a single `Iteration`. `HarnessSource` spawns one long-running
process and frames its output into many `Iteration`s (push), all belonging to a
single `Execution`, killable via close(). The `Controller` drives either uniformly: it
pulls `Iteration`s (driving the stopping policy) and, when done, collects the
assembled `Execution`(s) via `close()`.
"""

from __future__ import annotations

import abc
import dataclasses

from bench.core.invocation import (
    InvocationResult,
    Verdict,
    format_identifier,
)
from bench.core.process import execute
from bench.core.results import Iteration, Execution, Sample, diagnostic_excerpt
from bench.builder.benchmark import Benchmark
from bench.runner.base import format_benchmark_verbose


def _with_elapsed(samples: list[Sample], result: InvocationResult) -> list[Sample]:
    """Wall-clock elapsed is intrinsic to every run (`result.runtime`), so record
    it once, prepended, unless a metric already produced an `elapsed` sample."""
    if result.runtime is None or any(s.metric == "elapsed" for s in samples):
        return samples
    return [Sample("elapsed", result.runtime, unit="s", lower_is_better=True), *samples]


def _apply_verdict(result: InvocationResult, reason: Verdict) -> InvocationResult:
    """Record `reason` as the failure on `result`, unless it already failed."""
    if reason is not None and result.failure is None:
        return dataclasses.replace(result, failure=reason)
    return result


def _make_execution(
    b: Benchmark,
    result: InvocationResult,
    *,
    run: int,
    iterations: list[Iteration],
    process_samples: list[Sample],
    failure: str | None,
    message: str,
) -> Execution:
    """Assemble an Execution from a resolved benchmark and its process result."""
    ex = b.invocation
    return Execution(
        suite=b.suite,
        benchmark=b.name,
        variant=b.variant,
        variant_label=b.variant_label,
        run=run,
        command=ex.command,
        cwd=str(ex.cwd),
        env=dict(ex.env),
        returncode=result.returncode,
        runtime=result.runtime,
        failure=failure,
        message=message,
        stdout=result.stdout,
        stderr=result.stderr,
        iterations=iterations,
        process_samples=process_samples,
    )


class ExecutionSource(abc.ABC):
    """Produces Iterations and assembles Executions for one benchmark-variant.

    Two-method surface: pull `(Iteration, label)` pairs with `next()` (the
    label is the benchmark-variant display identifier, for live progress only),
    then `close()` to release resources and get the assembled `Execution`(s). A
    command yields one `Execution` per iteration. A harness yields one `Execution`
    holding all its iterations.
    """

    @abc.abstractmethod
    def next(self) -> tuple[Iteration, str]:
        """Next iteration and its display label. Raise `StopIteration` when
        exhausted.

        The source owns its own sequencing, callers just pull."""

    @abc.abstractmethod
    def close(self) -> list[Execution]:
        """Release resources (kill a running harness) and return the assembled
        `Execution`(s)."""


class CommandSource(ExecutionSource):
    """One process per iteration. Each iteration is its own finished Execution."""

    def __init__(self, b: Benchmark, *, verbose: bool = False) -> None:
        self._b = b
        self._verbose = verbose
        self._run = 0
        self._executions: list[Execution] = []

    def next(self) -> tuple[Iteration, str]:
        self._run += 1
        b = self._b
        if self._verbose and self._run == 1:
            print(format_benchmark_verbose(b, self._run))
        result = execute(b.invocation)

        result = _apply_verdict(result, b.success(result))

        label = format_identifier(
            b.suite, b.name, b.variant, self._run, b.variant_label
        )
        runtime = result.runtime or 0.0
        if result.is_failure():
            it = Iteration(samples=[], failure=result.failure, runtime=runtime)
            process_samples = []
            message = diagnostic_excerpt(result.stdout, result.stderr)
        else:
            # A command is one iteration: its iteration metrics read the chosen
            # source text, while its process metrics read the whole result.
            it_samples = [
                s
                for m in b.iteration_metrics
                for s in m.process(result)
            ]
            process_samples = _with_elapsed(
                [s for m in b.process_metrics for s in m.process(result)], result
            )
            it = Iteration(samples=it_samples, runtime=runtime)
            message = ""

        self._executions.append(
            _make_execution(
                b,
                result,
                run=self._run,
                iterations=[it],
                process_samples=process_samples,
                failure=result.failure,
                message=message,
            )
        )
        return it, label

    def close(self) -> list[Execution]:
        return self._executions

def make_source(b: Benchmark, *, verbose: bool = False) -> ExecutionSource:
    return CommandSource(b, verbose=verbose)
