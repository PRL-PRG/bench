"""Controller: the per-benchmark feedback loop over a RunSource."""

from __future__ import annotations

from collections.abc import Generator

from bench.core.policy import StoppingPolicy
from bench.core.process import interrupted
from bench.core.sample import Observation, Report
from bench.grammar.benchmark import Benchmark
from bench.report.reporter import Reporter
from bench.runner.source import make_source


def benchmarking_loop(
    warmup: StoppingPolicy,
    runs: StoppingPolicy,
) -> Generator[bool, Observation, None]:
    """Yield `in_warmup` per slot until both policies converge.

    Every observation, including a failed one (empty samples), counts:
    the active policy observes it and decides.
    """
    for policy, in_warmup in ((warmup, True), (runs, False)):
        state = policy.start()
        while not state.satisfied():
            observation = yield in_warmup
            state.observe(observation)


class Controller:
    """Drive `benchmarking_loop` over one benchmark-variant's RunSource.

    Pull one `(Observation, label)` per slot, feed
    the stopping policy, count warmup observations, and `close()` the source on
    convergence (which kills a running harness and returns the assembled
    `Run`(s)). The Controller records those runs and marks the variant's
    warmup. It never schedules. The source owns scheduling and spawning.
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

        warmup_obs = 0

        loop = benchmarking_loop(b.warmup, b.runs)
        try:
            in_warmup: bool | None = next(loop)
        except StopIteration:
            in_warmup = None

        try:
            while in_warmup is not None:
                try:
                    obs, label = source.next()
                except StopIteration:
                    break

                self.reporter.observation(obs, label)
                if in_warmup:
                    warmup_obs += 1
                if interrupted():
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
