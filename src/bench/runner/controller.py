"""Controller: the per-benchmark feedback loop over a RunSource."""

from __future__ import annotations

import dataclasses
from collections.abc import Generator

from bench.core.outlier import NoDetection, OutlierDetection
from bench.core.policy import StoppingPolicy
from bench.core.process import interrupted
from bench.core.sample import Iteration, Report, Run, Sample
from bench.grammar.benchmark import Benchmark
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


def _mark_warmup(runs: list[Run], warmup: int) -> list[Run]:
    """Flag the first `warmup` iterations (in pull order, across runs) as warmup.

    The Controller knows the warmup boundary (it drove the warmup policy), so it
    stamps it onto the assembled Runs — the flag then travels with the data."""
    if warmup <= 0:
        return runs
    remaining = warmup
    out: list[Run] = []
    for run in runs:
        if remaining <= 0:
            out.append(run)
            continue
        new_iters: list[Iteration] = []
        for it in run.iterations:
            if remaining > 0:
                new_iters.append(dataclasses.replace(it, warmup=True))
                remaining -= 1
            else:
                new_iters.append(it)
        out.append(dataclasses.replace(run, iterations=new_iters))
    return out


def _mark_outliers(runs: list[Run], detection: OutlierDetection) -> list[Run]:
    """Flag outlier Samples per (metric, unit), pooled across the measured
    (non-warmup) iterations of all runs, i.e., the same values that reach the stats."""

    if isinstance(detection, NoDetection):
        return runs

    # 1. Pool values per metric in traversal order.
    pools: dict[tuple[str, str], list[float]] = {}
    for run in runs:
        for it in run.iterations:
            if it.warmup:
                continue
            for s in it.samples:
                pools.setdefault((s.metric, s.unit), []).append(s.value)

    # 2. Outlier mask per metric; nothing flagged -> leave runs untouched.
    masks = {k: detection.detect(v) for k, v in pools.items()}
    if not any(any(m) for m in masks.values()):
        return runs

    # 3. Re-walk in the same order, consuming each metric's mask, rebuilding
    #    only the runs/iterations/samples that actually change.
    cursors = {k: iter(m) for k, m in masks.items()}
    out: list[Run] = []
    for run in runs:
        new_iters: list[Iteration] = []
        run_changed = False
        for it in run.iterations:
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
                run_changed = True
            else:
                new_iters.append(it)
        out.append(
            dataclasses.replace(run, iterations=new_iters) if run_changed else run
        )
    return out


class Controller:
    """Drive `benchmarking_loop` over one benchmark-variant's RunSource.

    Pull one `(Iteration, label)` per slot, feed the stopping policy, count
    warmup iterations, and `close()` the source on convergence (which kills a
    running harness and returns the assembled `Run`(s)). The Controller stamps
    the warmup iterations onto the runs and records them. It never schedules;
    the source owns scheduling and spawning.
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

        source = make_source(b, verbose=self.verbose)

        warmup_iters = 0

        loop = benchmarking_loop(b.warmup, b.runs)
        try:
            in_warmup: bool | None = next(loop)
        except StopIteration:
            in_warmup = None

        try:
            while in_warmup is not None:
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
            runs = _mark_warmup(source.close(), warmup_iters)
            runs = _mark_outliers(runs, b.outlier_detection)
            for run in runs:
                report.add(run)
                self.reporter.run_done(run)
