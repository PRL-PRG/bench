"""Controller: the per-benchmark feedback loop over a RunSource."""

from __future__ import annotations

from typing import Any

from benchr.core.loop import benchmarking_loop
from benchr.core.process import interrupted
from benchr.core.sample import Report
from benchr.report.reporter import Reporter
from benchr.runner.base import PlannedBenchmark
from benchr.runner.source import make_source


class Controller:
    """Drive ``benchmarking_loop`` over one benchmark-variant's RunSource.

    Pull one ``Observation`` per slot (each carries its display ``label``), feed
    the stopping policy, count warmup observations, and ``close()`` the source on
    convergence (which kills a running harness and returns the assembled
    ``Run``(s)). The Controller records those runs and marks the variant's
    warmup. It never schedules — the source owns scheduling and spawning.

    ``max_consecutive_failures`` only applies when policies are *unbounded*:
    bounded policies already cap the count, so the failure cap would only mask a
    legitimately short run.
    """

    def __init__(
        self,
        reporter: Reporter,
        *,
        max_runs_per_policy: int = 10_000,
        max_consecutive_failures: int = 5,
        verbose: bool = False,
    ) -> None:
        self.reporter = reporter
        self.max_runs_per_policy = max_runs_per_policy
        self.max_consecutive_failures = max_consecutive_failures
        self.verbose = verbose

    def run_benchmark(
        self, planned: PlannedBenchmark, params: Any, report: Report
    ) -> None:
        b = planned.benchmark
        if interrupted():
            return

        source = make_source(planned, params, verbose=self.verbose)

        bounded = b.warmup.max_runs() is not None and b.runs.max_runs() is not None
        failure_cap = None if bounded else self.max_consecutive_failures
        consecutive_failures = 0
        warmup_obs = 0
        count = 0

        loop = benchmarking_loop(b.warmup, b.runs)
        try:
            in_warmup: bool | None = next(loop)
        except StopIteration:
            in_warmup = None

        try:
            while in_warmup is not None:
                count += 1
                if count > self.max_runs_per_policy * 2:
                    raise RuntimeError(
                        f"Benchmark {b.name!r} exceeded max_runs_per_policy backstop "
                        f"({self.max_runs_per_policy}); did you forget .at_most(N)?"
                    )
                try:
                    obs = source.next()
                except StopIteration:
                    break

                self.reporter.observation(obs)
                if in_warmup:
                    warmup_obs += 1
                consecutive_failures = (
                    0 if not obs.is_failure() else consecutive_failures + 1
                )
                if interrupted() or (
                    failure_cap is not None and consecutive_failures >= failure_cap
                ):
                    break

                try:
                    in_warmup = loop.send(obs)
                except StopIteration:
                    break
        finally:
            runs = source.close()
            for run in runs:
                report.add(run)
                self.reporter.run_done(run)
            if warmup_obs and runs:
                key = runs[0].key()
                report.warmup(key, warmup_obs)
                self.reporter.warmup(key, warmup_obs)
