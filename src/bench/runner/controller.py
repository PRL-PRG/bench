"""Controller: the per-benchmark feedback loop over an ExecutionSource."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Generator

from bench.core.outlier import NoDetection, OutlierDetection
from bench.core.policy import StoppingPolicy
from bench.core.process import interrupted
from bench.core.results import Iteration, Report, Execution, Sample
from bench.builder.benchmark import Benchmark
from bench.report.reporter import Reporter
from bench.runner.source import make_source


def benchmarking_loop(
    warmup: StoppingPolicy,
    runs: StoppingPolicy,
) -> Generator[bool, Iteration, None]:
    """Yield `in_warmup` per slot until both policies converge.

    Every iteration, including a failed one (empty samples), counts:
    the active policy observes it and decides.
    """
    for policy, in_warmup in ((warmup, True), (runs, False)):
        state = policy.start()
        while not state.satisfied():
            iteration = yield in_warmup
            state.observe(iteration)


def _mark_warmup(executions: list[Execution], warmup: int) -> list[Execution]:
    """Flag the first `warmup` iterations (in pull order, across executions) as
    warmup. The Controller knows the warmup boundary (it drove the warmup
    policy), so it stamps it onto the assembled Executions. The flag then travels
    with the data."""
    if warmup <= 0:
        return executions
    remaining = warmup
    out: list[Execution] = []
    for execution in executions:
        if remaining <= 0:
            out.append(execution)
            continue
        new_iters: list[Iteration] = []
        for it in execution.iterations:
            if remaining > 0:
                new_iters.append(dataclasses.replace(it, warmup=True))
                remaining -= 1
            else:
                new_iters.append(it)
        out.append(dataclasses.replace(execution, iterations=new_iters))
    return out


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
                    new_samples.append(dataclasses.replace(s, outlier=True))
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

    def __init__(
        self,
        reporter: Reporter,
        *,
        verbose: bool = False,
    ) -> None:
        self.reporter = reporter
        self.verbose = verbose

    def run_benchmark(self, b: Benchmark, report: Report) -> None:
        if interrupted():
            return

        self.reporter.benchmark_start(b)
        source = make_source(b, verbose=self.verbose)

        warmup_iters = 0

        loop = benchmarking_loop(b.warmup, b.runs)
        try:
            in_warmup: bool | None = next(loop)
        except StopIteration:
            in_warmup = None

        # Cooldown pauses between separate process executions. A harness is one
        # streaming process, so its iterations are not separate executions.
        cooldown = b.cooldown if not b.harness else 0.0
        first = True

        try:
            while in_warmup is not None:
                if not first and cooldown > 0:
                    time.sleep(cooldown)
                first = False
                try:
                    it, label = source.next()
                except StopIteration:
                    break

                self.reporter.iteration(it, label)
                if in_warmup:
                    warmup_iters += 1
                if interrupted():
                    break

                try:
                    in_warmup = loop.send(it)
                except StopIteration:
                    break
        finally:
            executions = _mark_warmup(source.close(), warmup_iters)
            executions = _mark_outliers(executions, b.outlier_detection)
            for execution in executions:
                report.add(execution)
                self.reporter.execution_done(execution)
            self.reporter.benchmark_done(b, executions)
