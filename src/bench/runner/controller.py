"""Controller: the per-benchmark feedback loop over an ExecutionSource."""

from __future__ import annotations

import dataclasses
import time
from typing import TYPE_CHECKING

from bench.core.invocation import InvocationResult, format_identifier
from bench.core.outlier import NoDetection, OutlierDetection
from bench.core.process import execute, interrupted
from bench.core.results import Iteration, Report, Execution, Sample, diagnostic_excerpt
from bench.report.reporter import Reporter
from bench.runner.base import format_benchmark_verbose

if TYPE_CHECKING:
    from bench.builder.benchmark import Benchmark


def _make_execution(
    b: Benchmark,
    result: InvocationResult,
    *,
    run: int,
    iterations: list[Iteration],
    process_samples: list[Sample],
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
        failure=result.failure,
        message=diagnostic_excerpt(result.stdout, result.stderr),
        stdout=result.stdout,
        stderr=result.stderr,
        iterations=iterations,
        process_samples=process_samples,
    )


def _mark_outliers(
    executions: list[Execution], detection: OutlierDetection
) -> list[Execution]:
    """Flag outlier Samples per (metric, unit), pooled across the measured
    (non-warmup) iterations of all executions, i.e., the same values that reach
    the stats."""

    if isinstance(detection, NoDetection):
        return executions

    # 1. Pool values per metric in traversal order.
    pools: dict[tuple[str, str], list[float]] = {}
    for execution in executions:
        for it in execution.iterations:
            if it.warmup:
                continue
            for s in it.samples:
                pools.setdefault((s.metric, s.unit), []).append(s.value)

    # 2. Outlier mask per metric. Nothing flagged -> leave executions untouched.
    masks = {k: detection.detect(v) for k, v in pools.items()}
    if not any(any(m) for m in masks.values()):
        return executions

    # 3. Re-walk in the same order, consuming each metric's mask, rebuilding
    #    only the executions/iterations/samples that actually change.
    cursors = {k: iter(m) for k, m in masks.items()}
    out: list[Execution] = []
    for execution in executions:
        new_iters: list[Iteration] = []
        execution_changed = False
        for it in execution.iterations:
            if it.warmup:
                new_iters.append(it)
                continue
            new_samples: list[Sample] = []
            it_changed = False
            for s in it.samples:
                if next(cursors[(s.metric, s.unit)]):
                    new_samples.append(
                        dataclasses.replace(s, extra=dict(s.extra) | {"outlier": True})
                    )
                    it_changed = True
                else:
                    new_samples.append(s)
            if it_changed:
                new_iters.append(dataclasses.replace(it, samples=new_samples))
                execution_changed = True
            else:
                new_iters.append(it)
        out.append(
            dataclasses.replace(execution, iterations=new_iters)
            if execution_changed
            else execution
        )
    return out


class Controller:
    """Drive `benchmarking_loop` over one benchmark-variant's ExecutionSource.

    Pull one `(Iteration, label)` per slot, feed the stopping policy, count
    warmup iterations, and `close()` the source on convergence (which kills a
    running harness and returns the assembled `Execution`(s)). The Controller stamps
    the warmup iterations onto the executions and records them. It never schedules.
    The source owns scheduling and spawning.
    """

    def evaluate_invocation(
        self, b: Benchmark, result: InvocationResult
    ) -> InvocationResult:
        verdict = b.success(result)
        if verdict is not None and result.failure is None:
            return dataclasses.replace(result, failure=verdict)

        return result

    def extract_execution(
        self, b: Benchmark, result: InvocationResult, run: int
    ) -> Execution:
        iterations = list[Iteration]()
        process_samples = list[Sample]()

        for metric in b.metrics:
            for sample in metric.process(result):
                if sample.iteration is not None:
                    # Iteration sample
                    if sample.iteration >= len(iterations):
                        iterations.extend(
                            [Iteration()] * (sample.iteration + 1 - len(iterations))
                        )
                    iterations[sample.iteration] = iterations[
                        sample.iteration
                    ].add_sample(sample)
                else:
                    # Process sample
                    process_samples.append(sample)

        return _make_execution(
            b,
            result,
            run=run,
            iterations=iterations,
            process_samples=process_samples,
        )

    def execute_benchmark(self, b: Benchmark, run: int, verbose: bool) -> Execution:
        if run != 1 and b.cooldown > 0:
            time.sleep(b.cooldown)

        if verbose:
            print(format_benchmark_verbose(b, run))

        # The execution
        result = self.evaluate_invocation(b, execute(b.invocation))
        return self.extract_execution(b, result, run)

    def run_benchmark(
        self, b: Benchmark, report: Report, reporter: Reporter, verbose: bool = False
    ) -> None:
        if interrupted():
            return

        reporter.benchmark_start(b)

        run = 0
        executions = list[Execution]()

        warmup_policy_state = b.warmup.start()
        runs_policy_state = b.runs.start()

        while not warmup_policy_state.satisfied() or not runs_policy_state.satisfied():
            if interrupted():
                break

            run += 1
            execution = self.execute_benchmark(b, run, verbose)

            def observe_iteration(it: Iteration) -> Iteration:
                if not warmup_policy_state.satisfied():
                    it = dataclasses.replace(it, warmup=True)
                    warmup_policy_state.observe(it)

                elif not runs_policy_state.satisfied():
                    runs_policy_state.observe(it)

                reporter.iteration(
                    it,
                    format_identifier(b.suite, b.name, b.variant, run, b.variant_label),
                )

                return it

            if len(execution.iterations) == 0:
                _ = observe_iteration(Iteration(samples=execution.process_samples))

            else:
                result_iterations = execution.iterations
                for idx, it in enumerate(execution.iterations):
                    result_iterations[idx] = observe_iteration(it)

                executions.append(
                    dataclasses.replace(execution, iterations=result_iterations)
                )

        executions = _mark_outliers(executions, b.outlier_detection)

        for execution in executions:
            reporter.execution_done(execution)
            report.add(execution)

        reporter.benchmark_done(b, executions)
