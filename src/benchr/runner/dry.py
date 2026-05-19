"""Dry runner: advances each Benchmark.compile() once to print what would run."""

from __future__ import annotations

from typing import Any

from benchr.grammar.suite import Suite
from benchr.report.sample import Sample
from benchr.runner.base import Runner, plan


class Dry(Runner):
    """Print one Execution per Benchmark; do not subprocess."""

    def run(self, suites: list[Suite], ctx: Any) -> list[Sample]:
        planned = plan(suites, ctx)
        for p in planned:
            try:
                gen = p.benchmark.compile(ctx, suite=p.suite)
                sched = next(gen)
                gen.close()
            except StopIteration:
                continue
            self._print(sched)
        return []

    @staticmethod
    def _print(sched) -> None:
        e = sched.execution
        print(f"{sched.suite}/{sched.benchmark}: {' '.join(e.command)}")
        print(f"  cwd:     {e.cwd}")
        if e.env:
            print(f"  env:     {{{', '.join(f'{k}={v}' for k, v in e.env.items())}}}")
        if e.timeout is not None:
            print(f"  timeout: {e.timeout}s")
        if sched.info:
            print(f"  info:    {dict(sched.info)}")
        print()
