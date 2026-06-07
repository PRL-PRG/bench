#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Lox test runner.

Treats each ``.lox`` file under ``ctx.cwd/tests`` as a test: success is
"stdout matches the ``// expect: <line>`` comments embedded in the source."
Drives benchr's Sequential runner with a custom success policy and a
custom Reporter that counts pass/fail and exits non-zero on any failure.

Usage:
    ./lox_test.py --lox /path/to/lox-interpreter [--cwd .]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from benchr import (
    Benchmark, Execution, ExecutionResult, Reporter, Sample,
    ScheduledExecution, Time, bench, run, suite,
)
from benchr.report.reporter import console


HERE = Path(__file__).resolve().parent


# ----------------------------------------------------------------------
# Success policy: stdout must match the // expect: comments in the source.
# ----------------------------------------------------------------------


_EXPECT_RE = re.compile(r"//\s*expect:\s*(.*)")


def _expected_lines(source: Path) -> list[str] | None:
    try:
        return [
            m.group(1) for line in source.read_text().splitlines()
            if (m := _EXPECT_RE.search(line))
        ]
    except FileNotFoundError:
        return None


def lox_expect(execution: Execution, result: ExecutionResult) -> str | None:
    """Success iff stdout lines equal the // expect: comments in the source."""
    if result.returncode != 0:
        return f"exit code {result.returncode}"
    source = Path(execution.command[-1])
    expected = _expected_lines(source)
    if expected is None:
        return f"source not found: {source}"
    actual = (result.stdout or "").splitlines()
    if actual != expected:
        return f"stdout mismatch ({len(actual)} lines vs {len(expected)} expected)"
    return None


# ----------------------------------------------------------------------
# Custom Reporter: per-test pass/fail + final summary.
# ----------------------------------------------------------------------


class LoxTestSummary(Reporter):
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failed_tests: list[str] = []

    def sample(
        self,
        sched: ScheduledExecution,
        result: ExecutionResult,
        samples: list[Sample],
    ) -> None:
        if result.is_failure():
            self.failed += 1
            self.failed_tests.append(f"{sched.suite}/{sched.benchmark}")
        else:
            self.passed += 1

    def finalize(self) -> None:
        total = self.passed + self.failed
        if total == 0:
            return
        console.print()
        console.print("Summary:")
        if self.passed:
            console.print(f"\t[benchr.success]PASSED:  {self.passed:5d}[/]")
        if self.failed:
            console.print(f"\t[benchr.failure]FAILED:  {self.failed:5d}[/]")
        if self.failed_tests:
            console.print()
            console.print("Failed tests:")
            for t in self.failed_tests:
                console.print(f"\t{t}")


# ----------------------------------------------------------------------
# Suite
# ----------------------------------------------------------------------


@dataclass
class TestParams:
    lox: Path                            # required: lox binary
    cwd: Path = HERE


def _test_root(ctx: TestParams) -> Path:
    return (ctx.cwd / "tests").resolve()


def lox_cmd(b: Benchmark, ctx: TestParams) -> list[str]:
    return [str(ctx.lox), str(b.path)]


def lox_factory(ctx: TestParams) -> list[Benchmark]:
    """Discover .lox files and turn each into a Benchmark with our success
    policy attached. Done in a factory because ``Suite`` propagates metric,
    cwd, command, etc. as defaults but does not propagate ``with_success``.
    """
    root = _test_root(ctx)
    benches: list[Benchmark] = []
    for fp in sorted(root.rglob("*.lox")):
        name = str(fp.relative_to(root).with_suffix(""))
        benches.append(
            bench(name, path=fp)
                .with_command(lox_cmd)
                .with_cwd(root)
                .with_timeout(10)
                .with_metric(Time())
                .with_success(lox_expect)
                .runs(1)
        )
    return benches


lox_tests = suite("LoxTests").factory(lox_factory)


if __name__ == "__main__":
    reporter = LoxTestSummary()
    run(lox_tests, params=TestParams, reporter=reporter)
    sys.exit(1 if reporter.failed > 0 else 0)

# vim: ft=python
