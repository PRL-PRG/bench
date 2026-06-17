"""Controller: the per-benchmark feedback loop over a RunSource."""

from __future__ import annotations

from typing import Any

from benchr.core.execution import (
    ExecutionResult,
    ScheduledExecution,
    record_key,
)
from benchr.core.loop import benchmarking_loop
from benchr.core.process import interrupted
from benchr.core.sample import Report, RunRecord, RunResult
from benchr.grammar.benchmark import Benchmark
from benchr.report.reporter import Reporter
from benchr.runner.base import PlannedBenchmark, format_scheduled_verbose
from benchr.runner.source import RunSource, make_source


class Controller:
    """Drive ``benchmarking_loop`` over one benchmark-variant's RunSource.

    The consolidation of the old ``_run_benchmark``/``_run_harness``/
    ``_record_harness`` into one uniform loop: pull a ``RunResult`` per slot,
    stamp the run number, feed the policy, and ``close()`` the source on
    convergence (which kills a running harness). Continuous run numbering,
    ``Report.warmups``, the consecutive-failure cap, the ``max_runs_per_policy``
    backstop, and the harness short-/failed-delivery messages are preserved.

    ``max_consecutive_failures`` only applies when policies are *unbounded*:
    bounded policies already cap the number of runs, so the failure cap would
    only mask a legitimately short run.
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
        template = b.schedule(params, suite=planned.suite, run=1)
        key = record_key(planned.suite, b.name, template.variant)
        if self.verbose:
            print(format_scheduled_verbose(template, b))

        source = make_source(planned, params)
        bounded = b.warmup.max_runs() is not None and b.runs.max_runs() is not None
        failure_cap = None if bounded else self.max_consecutive_failures
        consecutive_failures = 0
        run = 0

        loop = benchmarking_loop(b.warmup, b.runs)
        try:
            in_warmup = next(loop)
        except StopIteration:
            source.close()
            return

        try:
            while True:
                run += 1
                if run > self.max_runs_per_policy * 2:
                    raise RuntimeError(
                        f"Benchmark {b.name!r} exceeded max_runs_per_policy backstop "
                        f"({self.max_runs_per_policy}); did you forget .at_most(N)?"
                    )
                try:
                    rr = source.next()
                except StopIteration:
                    self._on_exhausted(
                        report, template, b, run, source, bounded
                    )
                    return

                self._record(report, template, run, rr)
                self._flush_process_events(source)
                consecutive_failures = (
                    0 if not rr.is_failure() else consecutive_failures + 1
                )

                if interrupted() or (
                    failure_cap is not None and consecutive_failures >= failure_cap
                ):
                    if in_warmup:
                        self._mark_warmup(report, key, run)
                    return

                try:
                    next_warmup = loop.send(rr)
                except StopIteration:
                    if in_warmup:
                        self._mark_warmup(report, key, run)
                    return
                if in_warmup and not next_warmup:
                    self._mark_warmup(report, key, run)
                in_warmup = next_warmup
        finally:
            source.close()
            self._flush_process_events(source)
            md = source.metadata()
            if md:
                report.set_metadata(key, md)
                self.reporter.set_metadata(key, md)
                if self.verbose:
                    dump = ", ".join(f"{s.metric}={s.value}{s.unit}" for s in md)
                    print(f"{key} metadata: {dump}")

    # ----- helpers -----

    def _record(
        self, report: Report, template: ScheduledExecution, run: int, rr: RunResult
    ) -> None:
        rec = RunRecord.from_run_result(template, run, rr)
        report.add(rec)
        self.reporter.record(rec)

    def _mark_warmup(self, report: Report, key: str, run: int) -> None:
        """Record (once) that this variant's first ``run`` runs were warmup."""
        report.warmup(key, run)
        self.reporter.warmup(key, run)

    def _flush_process_events(self, source: RunSource) -> None:
        for sched, result in source.drain_process_events():
            self.reporter.process_done(sched, result)

    def _on_exhausted(
        self,
        report: Report,
        template: ScheduledExecution,
        b: Benchmark,
        run: int,
        source: RunSource,
        bounded: bool,
    ) -> None:
        """Source ran out before the policy converged (harness ended early)."""
        self._flush_process_events(source)
        last = source.process_result()
        delivered = run - 1
        if delivered == 0:
            self._record(
                report,
                template,
                1,
                RunResult(
                    samples=[],
                    returncode=(last.returncode if last is not None else 0),
                    failure=self._zero_delivery_failure(last),
                ),
            )
        elif bounded and (last is None or last.failure is None):
            # A real failure (process crash / monitor exception) is already
            # recorded and surfaced via process_done — don't paper over it with
            # a generic short-delivery message.
            warmup_max, runs_max = b.warmup.max_runs(), b.runs.max_runs()
            assert warmup_max is not None and runs_max is not None
            target = warmup_max + runs_max
            if delivered < target:
                self._record(
                    report,
                    template,
                    run,
                    RunResult(
                        samples=[],
                        failure=(
                            f"harness produced {delivered} iterations, "
                            f"expected {target}"
                        ),
                    ),
                )

    @staticmethod
    def _zero_delivery_failure(last: ExecutionResult | None) -> str:
        if last is not None and last.failure is not None:
            return last.failure
        return (
            "no iterations parsed from harness output — use an "
            "output-parsing metric (FloatPerLine, Regex, Rebench)"
        )
