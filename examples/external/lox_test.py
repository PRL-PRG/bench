#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""Custom Reporter as a pass/fail test runner that sets the exit code.

Each `.lox` under `tests/` is a test; a `.with_success` policy passes when
stdout matches the `// expect:` comments in the source, and a custom `Reporter`
counts pass/fail and exits non-zero on any failure. Needs a real Lox binary;
for a *timing* (not testing) Lox config see `lox.py`.

Usage: ./lox_test.py --lox /path/to/lox-interpreter [--cwd .]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from bench import (
    Context,
    ExecutionResult,
    Reporter,
    Run,
    Time,
    bench_app,
    from_files,
    suite,
)
from bench.report.reporter import console


HERE = Path(__file__).resolve().parent


# ----------------------------------------------------------------------
# Success policy: stdout must match the // expect: comments in the source.
# ----------------------------------------------------------------------


_EXPECT_RE = re.compile(r"//\s*expect:\s*(.*)")


def _expected_lines(source: Path) -> list[str] | None:
    try:
        return [
            m.group(1)
            for line in source.read_text().splitlines()
            if (m := _EXPECT_RE.search(line))
        ]
    except FileNotFoundError:
        return None


def lox_expect(result: ExecutionResult) -> str | None:
    """Success iff stdout lines equal the // expect: comments in the source."""
    if result.returncode != 0:
        return f"exit code {result.returncode}"
    source = Path(result.execution.command[-1])
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

    def run_done(self, run: Run) -> None:
        if run.is_failure():
            self.failed += 1
            self.failed_tests.append(f"{run.suite}/{run.benchmark}")
        else:
            self.passed += 1

    def finalize(self) -> None:
        total = self.passed + self.failed
        if total == 0:
            return
        console.print()
        console.print("Summary:")
        if self.passed:
            console.print(f"\t[bench.success]PASSED:  {self.passed:5d}[/]")
        if self.failed:
            console.print(f"\t[bench.failure]FAILED:  {self.failed:5d}[/]")
        if self.failed_tests:
            console.print()
            console.print("Failed tests:")
            for t in self.failed_tests:
                console.print(f"\t{t}")


# ----------------------------------------------------------------------
# SuiteBuilder
# ----------------------------------------------------------------------


@dataclass
class TestParams:
    lox: Path  # required: lox binary
    cwd: Path = HERE


def _test_root(ctx: TestParams) -> Path:
    return (ctx.cwd / "tests").resolve()


def lox_cmd(ctx: Context[TestParams]) -> list[str]:
    return [str(ctx.params.lox), str(ctx.data.path)]


lox_tests = (
    suite("LoxTests")
    .factory(lambda ctx: from_files(_test_root(ctx.params), pattern=r"\.lox$"))
    .with_command(lox_cmd)
    .with_cwd(lambda ctx: _test_root(ctx.params))
    .with_timeout(10)
    .with_process_metric(Time())
    .with_success(lox_expect)
    .with_runs(1)
)


if __name__ == "__main__":
    reporter = LoxTestSummary()
    bench_app(params=TestParams, reporter=reporter).add_all(lox_tests).run()
    sys.exit(1 if reporter.failed > 0 else 0)

# vim: ft=python
