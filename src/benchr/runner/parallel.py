"""Parallel runner.

Two-tier parallelism:

  ``Parallel(n)``                         n workers; each worker drives one
                                          full Benchmark coroutine end-to-end.
                                          Within one benchmark, runs are still
                                          sequential — required for any policy
                                          that observes per-run state.

  ``Parallel(n, fanout=True)``            opt-in: for benchmarks whose ``measure``
                                          policy is exactly ``FixedRuns(N)`` and
                                          whose ``warmup`` is exactly
                                          ``FixedRuns(M)``, fan out the
                                          (warmup → measure) sequence across
                                          workers. Convergence-driven policies
                                          stay sequential.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any

from benchr.grammar.benchmark import Benchmark
from benchr.grammar.policy import FixedRuns
from benchr.grammar.processor import stamp
from benchr.grammar.suite import Suite
from benchr.report.sample import Sample
from benchr.runner.base import PlannedBenchmark, Runner, execute, plan


class Parallel(Runner):
    """N-worker thread pool across benchmarks (and optionally runs)."""

    def __init__(
        self,
        workers: int,
        *args,
        fanout: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.workers = workers
        self.fanout = fanout
        self._lock = Lock()

    def run(self, suites: list[Suite], ctx: Any) -> list[Sample]:
        planned = plan(suites, ctx)
        self.reporter.start([p.benchmark for p in planned])
        all_samples: list[Sample] = []
        try:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = [pool.submit(self._dispatch, p, ctx) for p in planned]
                for f in futures:
                    all_samples.extend(f.result())
            return all_samples
        finally:
            self.reporter.finalize()

    def _dispatch(self, p: PlannedBenchmark, ctx: Any) -> list[Sample]:
        if self.fanout and self._fanout_eligible(p.benchmark):
            return self._run_fanout(p, ctx)
        return self._run_benchmark_locked(p, ctx)

    def _run_benchmark_locked(self, p: PlannedBenchmark, ctx: Any) -> list[Sample]:
        # The base pump is single-threaded per benchmark; we only need to lock
        # around mutations of shared reporter state which the Reporter
        # implementations themselves must guard.
        return self._run_benchmark(p, ctx)

    # ----- fan-out path for FixedRuns benchmarks ----------------------

    @staticmethod
    def _fanout_eligible(b: Benchmark) -> bool:
        return isinstance(b.warmup, FixedRuns) and isinstance(b.measure, FixedRuns)

    def _run_fanout(self, p: PlannedBenchmark, ctx: Any) -> list[Sample]:
        b = p.benchmark
        if b.processor is None:
            raise ValueError(f"Benchmark {b.name!r} has no processor")
        assert isinstance(b.warmup, FixedRuns) and isinstance(b.measure, FixedRuns)

        # Pre-materialize scheduled executions; runs are order-independent for
        # FixedRuns, so we can submit them in parallel.
        warm = [
            b.schedule(ctx, suite=p.suite, run=i, phase="warmup")
            for i in range(1, b.warmup.n + 1)
        ]
        meas = [
            b.schedule(ctx, suite=p.suite, run=i, phase="measure")
            for i in range(1, b.measure.n + 1)
        ]
        all_sched = warm + meas

        out: list[Sample] = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for sched, pr in zip(all_sched, pool.map(lambda s: execute(s.execution), all_sched)):
                partials = list(b.processor.process(pr))
                samples = list(stamp(partials, sched))
                out.extend(samples)
                with self._lock:
                    self.reporter.sample(sched, pr, samples)
        return out
