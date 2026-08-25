"""Runner: schedules planned benchmarks and collects them into a `Report`."""

from __future__ import annotations

import abc

from bench.core.diagnostic import Diagnostic
from bench.core.fingerprint import Fingerprint
from bench.core.process import install_sigint_handler, interrupted
from bench.model.benchmark import Benchmark
from bench.model.results import Report
from bench.report import Reporter


class Runner(abc.ABC):
    """Abstract runner: drives a set of benchmarks to a `Report`."""

    def __init__(
        self,
        *,
        verbose: bool = False,
    ) -> None:
        self.verbose = verbose

    def run(
        self,
        planned: list[Benchmark],
        reporter: Reporter = Reporter(),
        fingerprint: Fingerprint | None = None,
        diagnostics: list[Diagnostic] = [],
    ) -> Report:
        reporter.start(planned)
        report = Report(fingerprint=fingerprint, diagnostics=diagnostics)

        try:
            with install_sigint_handler():
                self.run_with_report(planned, reporter, report)
                if interrupted():
                    raise KeyboardInterrupt
        finally:
            reporter.finalize(report)

        return report

    @abc.abstractmethod
    def run_with_report(
        self, planned: list[Benchmark], reporter: Reporter, report: Report
    ) -> None: ...
