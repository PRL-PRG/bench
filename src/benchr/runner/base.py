"""Runner protocol and shared coroutine pump."""

from __future__ import annotations

import abc
import dataclasses
import subprocess
from dataclasses import dataclass
from typing import Any

from benchr.grammar.benchmark import Benchmark
from benchr.core.loop import benchmarking_loop
from benchr.core.execution import (
    ExecutionResult,
    ScheduledExecution,
    default_success,
    record_key,
)
from benchr.core.metric import extract_all
from benchr.core.process import execute, interrupted
from benchr.grammar.suite import Suite
from benchr.report.reporter import Reporter
from benchr.core.sample import Report, RunRecord, Sample


class _NoopReporter(Reporter):
    def record(self, rec: RunRecord, result: ExecutionResult) -> None:
        pass


def split_iterations(samples: list[Sample]) -> list[list[Sample]]:
    """Group a harness's whole-output samples into per-iteration groups.

    The i-th sample of each metric belongs to iteration i (grouped by metric
    name in first-seen order, zipped positionally); the iteration count is
    the largest group's length.
    """
    by_metric: dict[str, list[Sample]] = {}
    for s in samples:
        by_metric.setdefault(s.metric, []).append(s)
    n = max((len(v) for v in by_metric.values()), default=0)
    return [[v[i] for v in by_metric.values() if i < len(v)] for i in range(n)]


def judge(
    b: Benchmark, result: ExecutionResult
) -> tuple[ExecutionResult, list[Sample]]:
    """Apply the success policy and extract samples.

    Asks the benchmark's ``success`` policy (``default_success`` unless
    overridden) for a verdict and stamps the reason onto ``result.failure``.
    On success runs the metrics; on failure returns no samples — a failed run
    never produces metrics.
    """
    reason = b.success(result)
    if reason is not None and result.failure is None:
        result = dataclasses.replace(result, failure=reason)
    samples = list(extract_all(b.metrics, result)) if not result.is_failure() else []
    return result, samples


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
        # TODO: replace self.cause with better formatter error message in case of CalledProcessError
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
    """Drives the benchmarking loop, calls executor, forwards samples.

    For each slot the loop yields, the Runner materializes a
    ScheduledExecution via ``Benchmark.schedule()``, runs it via ``execute``,
    ``judge``s the result against the benchmark's metrics, forwards samples
    to the Reporter, and sends them back into the loop so the StoppingPolicy
    can observe. ``run()`` returns the accumulated ``Report``.

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

    # ----- shared pump --------------------------------------------------

    def _record(
        self,
        report: Report,
        sched: ScheduledExecution,
        result: ExecutionResult,
        samples: list[Sample],
    ) -> None:
        """Build the RunRecord once; the Report and the Reporter share it."""
        rec = RunRecord.from_result(sched, result, samples)
        report.add(rec)
        self.reporter.record(rec, result)

    def _warmup(self, report: Report, sched: ScheduledExecution, runs: int) -> None:
        """Note that this benchmark variant's first ``runs`` runs were warmup."""
        if runs:
            key = record_key(sched.suite, sched.benchmark, sched.variant)
            report.warmup(key, runs)
            self.reporter.warmup(key, runs)

    def _print_verbose(self, sched: ScheduledExecution, b: Benchmark) -> None:
        """Print the per-benchmark verbose block."""
        print(format_scheduled_verbose(sched, b))

    def _run_benchmark(
        self, planned: PlannedBenchmark, params: Any, report: Report
    ) -> None:
        b = planned.benchmark
        if b.harness:
            self._run_harness(planned, params, report)
            return

        consecutive_failures = 0
        guard = 0

        bounded = b.warmup.max_runs() is not None and b.runs.max_runs() is not None
        failure_cap = None if bounded else self.max_consecutive_failures

        if interrupted():
            return

        loop = benchmarking_loop(b.warmup, b.runs)
        try:
            run, in_warmup = next(loop)
        except StopIteration:
            return

        sched = b.schedule(params, suite=planned.suite, run=run)

        if self.verbose:
            self._print_verbose(sched, b)

        while True:
            guard += 1
            if guard > self.max_runs_per_policy * 2:  # warmup + runs
                raise RuntimeError(
                    f"Benchmark {b.name!r} exceeded max_runs_per_policy backstop "
                    f"({self.max_runs_per_policy}); did you forget .at_most(N)?"
                )

            result, samples = judge(b, execute(sched.execution))
            consecutive_failures = (
                0 if not result.is_failure() else consecutive_failures + 1
            )
            self._record(report, sched, result, samples)

            if interrupted():
                if in_warmup:
                    self._warmup(report, sched, run)
                loop.close()
                return

            if failure_cap is not None and consecutive_failures >= failure_cap:
                if in_warmup:
                    self._warmup(report, sched, run)
                loop.close()
                return

            try:
                next_run, next_warmup = loop.send(samples)
            except StopIteration:
                if in_warmup:
                    self._warmup(report, sched, run)
                return
            if in_warmup and not next_warmup:
                # The loop just left warmup: the first ``run`` runs were warmup.
                self._warmup(report, sched, run)
            run, in_warmup = next_run, next_warmup
            sched = b.schedule(params, suite=planned.suite, run=run)

    # ----- harness benchmarks ---------------------------------------------

    def _run_harness(
        self, planned: PlannedBenchmark, params: Any, report: Report
    ) -> None:
        """One execution; the harness runs all iterations itself."""
        b = planned.benchmark
        if interrupted():
            return
        sched = b.schedule(params, suite=planned.suite, run=1)
        if self.verbose:
            self._print_verbose(sched, b)
        self._record_harness(b, sched, execute(sched.execution), report)

    def _record_harness(
        self,
        b: Benchmark,
        sched: ScheduledExecution,
        result: ExecutionResult,
        report: Report,
    ) -> None:
        """Fan one harness execution out into per-iteration run records.

        Metrics parse the complete output; the i-th iteration's samples
        become run record ``i``. The first ``warmup.max_runs()`` records are
        the warmup (noted via ``Report.warmups``); fewer iterations than
        ``warmup + runs`` is recorded as one trailing failure.
        """
        result, samples = judge(b, result)
        if result.is_failure():
            self._record(report, sched, result, [])
            return

        groups = split_iterations(samples)
        if not groups:
            no_iter = dataclasses.replace(
                result,
                failure=(
                    "no iterations parsed from harness output — use an "
                    "output-parsing metric (FloatPerLine, Regex, Rebench)"
                ),
            )
            self._record(report, sched, no_iter, [])
            return

        # Bounded policies are guaranteed by Suite.materialize().
        warmup, runs = b.warmup.max_runs(), b.runs.max_runs()
        assert warmup is not None and runs is not None
        self._warmup(report, sched, warmup)
        for i, group in enumerate(groups, start=1):
            self._record(report, dataclasses.replace(sched, run=i), result, group)
        if len(groups) < warmup + runs:
            short = dataclasses.replace(
                result,
                failure=(
                    f"harness produced {len(groups)} iterations, "
                    f"expected {warmup + runs}"
                ),
            )
            self._record(
                report, dataclasses.replace(sched, run=len(groups) + 1), short, []
            )
