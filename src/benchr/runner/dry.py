"""Dry runner: advances each Benchmark.compile() once to print what would run.

Prints the plan straight to stdout and ignores any reporter / output sink
(``--json`` / ``--csv`` / ``--dir`` have no effect under ``--dry``). Without
``verbose`` it prints one compact ``suite/benchmark (variant): command`` line
per execution; with ``verbose`` it prints the full per-execution block (the
same one ``--verbose`` echoes for the real runners).
"""

from __future__ import annotations

from typing import Any

from benchr.grammar.execution import format_variant
from benchr.grammar.suite import Suite
from benchr.report.sample import Report
from benchr.runner.base import Runner, format_scheduled, plan


class Dry(Runner):
    """Print one Execution per Benchmark; do not subprocess."""

    def __init__(self, verbose: bool = False) -> None:
        super().__init__(verbose=verbose)

    def run(self, suites: list[Suite], ctx: Any) -> Report:
        planned = plan(suites, ctx)
        for p in planned:
            try:
                gen = p.benchmark.compile(ctx, suite=p.suite)
                sched = next(gen)
                gen.close()
            except StopIteration:
                continue
            if self.verbose:
                print(format_scheduled(sched, p.benchmark))
            else:
                e = sched.execution
                ident = f"{sched.suite}/{sched.benchmark}{format_variant(sched.info)}"
                print(f"{ident}: {' '.join(e.command)}")
        return Report()
